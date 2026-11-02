"""
The solar events viewer's Regions and indices tab: active regions, the
geomagnetic, solar wind and solar indices, and the GOES X-ray plot.
"""

import logging
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QScrollArea,
    QWidget,
    QVBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
)
from ..ui.busy import busy
from ..ui.download_parts import abandon
from ..ui.errors import show_error
from ..ui.geometry import fit_to_screen
from ..ui.plot_theme import figure_toolbar, follow_theme
from ..ui.styles import CONTROL_HEIGHT, SPACE_M, SPACE_S, SPACE_XS, themed

from .widgets import EventTable, follow_cell_tones, set_cell_tone, source_note
from .workers import GOESPlotWorker
logger = logging.getLogger(__name__)

CARD_WIDTH = 360  # of a card of values: a wide window does not spread them apart
NOT_IN_LIST = "not in the day's region list"
LEAST_ROWS = 6  # of the regions table, header included
RISK_TONES = {"Very High": "error", "High": "warning"}


def kp_text(value):
    """A Kp value in the thirds it is given in: 2, 2+, 3-."""
    thirds = round(value * 3)
    return f"{(thirds + 1) // 3}" + {0: "", 1: "+", 2: "-"}[thirds % 3]


def storm_tone(kp_max):
    """The one rule for a tone here: none below G1, so that a quiet day reads
    as quiet."""
    return "error" if kp_max >= 7 else "warning" if kp_max >= 5 else None


def region_key(number):
    """The event lists give a region's last four digits, the region lists may
    give five."""
    return str(number).strip()[-4:]


def region_flares(events):
    """The events of a day by region: {key: (X-ray classes, H-alpha classes,
    a location)}."""
    regions = {}
    for event in events or ():
        if not event.active_region:
            continue
        xray, optical, location = regions.setdefault(region_key(event.active_region), ([], [], [None]))
        if event.event_type == "XRA":
            xray.append(event.flare_class or "?")
        elif event.event_type == "FLA":
            optical.append(event.optical_class or "?")
            location[0] = location[0] or event.location_or_freq
    return regions


def _values_card(title, keys):
    """A group box of ``keys`` with their values in the KeyValue role; the
    box and the cells of the values."""
    table = QTableWidget(len(keys), 2)
    table.setObjectName("KeyValue")
    table.horizontalHeader().hide()
    table.verticalHeader().hide()
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setSelectionMode(QTableWidget.NoSelection)
    table.setFocusPolicy(Qt.NoFocus)
    table.setShowGrid(False)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    row_height = table.fontMetrics().height() + SPACE_S
    cells = []
    for row, key in enumerate(keys):
        table.setItem(row, 0, QTableWidgetItem(key))
        value = QTableWidgetItem("—")
        value.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        table.setItem(row, 1, value)
        table.setRowHeight(row, row_height)
        cells.append(value)
    table.setFixedHeight(row_height * len(keys) + 2)
    follow_cell_tones(table)
    card = QGroupBox(title)
    card.setMaximumWidth(CARD_WIDTH)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(SPACE_M, SPACE_XS, SPACE_M, SPACE_XS)
    layout.addWidget(table)
    layout.addStretch()
    return card, cells


class ConditionsTabMixin:
    """Builds and fills the Regions and indices tab.

    Mixed into :class:`NOAAEventsViewer`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``_cleanup_viewer``, ``begin_job``, ``end_job``, ``events``, ``goes_worker``,
    ``image_viewers``, ``plot_goes_btn``, ``selected_date``, ``tabs``,
    ``update_job``
    """

    def _create_regions_tab(self):
        """The day's regions over the cards of its indices."""
        page = QWidget()
        # A short window scrolls here, not down to a table of two rows
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, SPACE_M, 0, 0)
        layout.setSpacing(SPACE_S)

        self.ar_table = EventTable(
            [
                "AR#",
                "Location",
                "Area",
                "McIntosh",
                "Mag type",
                "Flares",
                "C%",
                "M%",
                "X%",
                "Forecast",
            ],
            empty_text="Fetch a date to see its active regions and flare probabilities",
        )
        self.ar_table.setMinimumHeight(LEAST_ROWS * CONTROL_HEIGHT)
        layout.addWidget(self.ar_table, 1)

        cards = QHBoxLayout()
        cards.setSpacing(SPACE_M)

        geo_card, (
            self.geo_ap_label,
            self.geo_kp_max_label,
            self.geo_kp_avg_label,
            self.geo_storm_label,
            self.geo_kp_vals_label,
        ) = _values_card(
            "Geomagnetic activity (daily)",
            ("Ap index", "Kp max", "Kp average", "Storm level", "3-hour Kp values"),
        )
        cards.addWidget(geo_card, 1)

        self.wind_card, (
            self.sw_speed_label,
            self.sw_density_label,
            self.sw_temp_label,
            self.sw_status_label,
        ) = _values_card("Solar wind (real-time)", ("Speed", "Density", "Temperature", "Status"))
        cards.addWidget(self.wind_card, 1)
        self.wind_card.hide()  # Only show when available

        f107_card, (
            self.f107_value_label,
            self.sunspot_number_label,
            self.sunspot_area_label,
            self.f107_activity_label,
        ) = _values_card(
            "Solar indices (daily)",
            ("10.7 cm flux", "Sunspot number", "Sunspot area", "Activity level"),
        )
        cards.addWidget(f107_card, 1)
        cards.addStretch()
        layout.addLayout(cards)

        notes = QHBoxLayout()
        notes.setSpacing(SPACE_M)
        self.ar_info_label = QLabel()
        self.ar_info_label.setObjectName("Hint")
        self.ar_info_label.hide()
        notes.addWidget(self.ar_info_label)
        self.conditions_info_label = QLabel()
        self.conditions_info_label.setObjectName("Hint")
        notes.addWidget(self.conditions_info_label)
        notes.addStretch()
        layout.addLayout(notes)

        layout.addWidget(source_note(
            'Data sources: <a href="https://solarmonitor.org/">SolarMonitor.org</a> '
            "(NOAA/SWPC Solar Region Summary &amp; ARM Flare Forecasts), "
            '<a href="https://www.swpc.noaa.gov/">NOAA SWPC</a>'
        ))

        self.tabs.addTab(scroll, "Regions and indices")

    def display_active_regions(self, regions):
        """Display active regions in the AR table, with the day's flares of
        each and a row for a region only the events name."""
        self.ar_table.setRowCount(0)

        if regions is None or len(regions) == 0:
            text = (
                "Could not fetch the active regions for this date."
                if regions is None
                else "No active regions data available for this date."
            )
            self.ar_table.set_empty_text(text)
            self.ar_info_label.setText(text)
            self.ar_info_label.hide()
            return

        self.ar_info_label.setText(f"{len(regions)} active regions.")
        self.ar_info_label.show()

        flares = region_flares(self.events)
        rows = []
        for region in regions:
            # Format probabilities
            c_prob = f"{region.prob_c}%" if region.prob_c is not None else "—"
            m_prob = f"{region.prob_m}%" if region.prob_m is not None else "—"
            x_prob = f"{region.prob_x}%" if region.prob_x is not None else "—"

            risk = region.flare_risk_level
            tones = {9: RISK_TONES.get(risk)}

            # Also M% and X% if they're significant
            if region.prob_m and region.prob_m >= 20:
                tones[7] = "warning"
            if region.prob_x and region.prob_x >= 5:
                tones[8] = "error"

            count, tip = self._flares_cell(flares.pop(region_key(region.noaa_number), None))
            rows.append((
                (-int(count), -region.area),
                [
                    f"AR{region.noaa_number}",
                    region.location,
                    str(region.area),
                    region.mcintosh_class,
                    region.mag_type,
                    count,
                    c_prob,
                    m_prob,
                    x_prob,
                    risk,
                ],
                tones,
                {5: tip} if tip else None,
            ))

        for key, flare in sorted(flares.items()):
            count, tip = self._flares_cell(flare)
            rows.append((
                (-int(count), 0),
                [f"AR{key}", flare[2][0] or "—", "—", "—", "—", count, "—", "—", "—", NOT_IN_LIST],
                {9: "text_secondary"},
                {5: tip} if tip else None,
            ))

        # What flared first: it is what the day's events are asking about
        with self.ar_table.filling():
            for _order, values, tones, tooltips in sorted(rows, key=lambda row: row[0]):
                self.ar_table.add_event_row(values, tones, tooltips)

        self.ar_table.scrollToTop()

    @staticmethod
    def _flares_cell(flare):
        """The count of a region's X-ray flares, and all its classes."""
        if flare is None:
            return "0", ""
        xray, optical, _location = flare
        parts = []
        if xray:
            parts.append("X-ray: " + ", ".join(xray))
        if optical:
            parts.append("H-alpha: " + ", ".join(optical))
        return str(len(xray)), " · ".join(parts)

    def display_solar_conditions(self, conditions):
        """Display solar conditions for the selected date."""
        if conditions is None:
            self.conditions_info_label.setText(
                "Unable to fetch solar conditions data"
            )
            self.geo_ap_label.setText("—")
            self.geo_kp_max_label.setText("—")
            self.geo_kp_avg_label.setText("—")
            self.geo_kp_vals_label.setText("—")
            self.geo_storm_label.setText("—")
            set_cell_tone(self.geo_storm_label, None)
            self.wind_card.hide()
            self._clear_f107()
            return

        # Update title label to show data source
        self.conditions_info_label.setText(f"{conditions.data_source}")

        # 1. Geomagnetic Data (Kp)
        if conditions.kp_index:
            kp = conditions.kp_index
            self.geo_ap_label.setText(f"{kp.ap_value}")
            self.geo_kp_max_label.setText(kp_text(kp.kp_max))
            self.geo_kp_avg_label.setText(f"{kp.kp_avg:.1f}")
            self.geo_kp_vals_label.setText(" ".join(kp_text(v) for v in kp.kp_values))

            self.geo_storm_label.setText(f"{kp.storm_level}")
            set_cell_tone(self.geo_storm_label, storm_tone(kp.kp_max))
        else:
            self.geo_ap_label.setText("—")
            self.geo_kp_max_label.setText("—")
            self.geo_kp_avg_label.setText("—")
            self.geo_kp_vals_label.setText("No data for this date")
            self.geo_storm_label.setText("Data unavailable")
            set_cell_tone(self.geo_storm_label, None)

        # 2. Solar Wind Data (Real-time only)
        if hasattr(conditions, "solar_wind") and conditions.solar_wind:
            sw = conditions.solar_wind
            self.wind_card.show()
            self.sw_speed_label.setText(f"{sw.speed:.0f} km/s")
            self.sw_density_label.setText(f"{sw.density:.1f} p/cm³")
            self.sw_temp_label.setText(f"{sw.temperature:.0f} K")

            status_text = sw.speed_category
            self.sw_status_label.setText(f"{status_text} speed")
            set_cell_tone(self.sw_status_label, {"High": "error", "Elevated": "warning"}.get(status_text))
        else:
            self.wind_card.hide()

        # F10.7 Flux (historical daily data)
        if conditions.f107_flux:
            f107 = conditions.f107_flux
            self.f107_value_label.setText(f"{f107.flux_value:.1f} sfu")
            self.sunspot_number_label.setText(f"{f107.sunspot_number}")

            area = getattr(f107, "sunspot_area", "—")
            area_str = f"{area} (10⁻⁶ hemispheres)" if area != "—" else "—"
            self.sunspot_area_label.setText(area_str)

            self.f107_activity_label.setText(f"{f107.activity_level}")
        else:
            self._clear_f107()

    def clear_regions_and_indices(self, text):
        """Nothing to show yet for this date: ``text`` says how to change that."""
        self.ar_table.setRowCount(0)
        self.ar_table.set_empty_text(text)
        self.ar_info_label.hide()
        self.display_solar_conditions(None)
        self.conditions_info_label.setText("")

    def _clear_f107(self):
        self.f107_value_label.setText("—")
        self.sunspot_number_label.setText("—")
        self.sunspot_area_label.setText("—")
        self.f107_activity_label.setText("Data unavailable")

    def plot_goes_xray(self):
        """Fetch and plot GOES X-ray flux for the selected date."""
        if self.goes_worker and self.goes_worker.isRunning():
            return

        selected_date = self.selected_date()
        if selected_date is None:
            return

        self.plot_goes_btn.setEnabled(False)
        self.begin_job("goes", f"Fetching GOES data for {selected_date}…", self._cancel_goes)

        self.goes_worker = GOESPlotWorker(selected_date)
        self.goes_worker.done.connect(self.on_goes_plot_ready)
        self.goes_worker.error.connect(self.on_goes_plot_error)
        self.goes_worker.progress.connect(self.on_goes_progress)
        self.goes_worker.start()

    def on_goes_progress(self, message):
        self.update_job("goes", message)

    def _goes_ended(self):
        self.end_job("goes")
        self.plot_goes_btn.setEnabled(True)

    def _cancel_goes(self):
        abandon(self.goes_worker)
        self.goes_worker = None
        self._goes_ended()

    def on_goes_plot_ready(self, ts):
        """Handle ready GOES data."""
        if self.goes_worker is None:
            return  # cancelled
        event_date = self.goes_worker.event_date
        try:
            # Check validity
            if not self.isVisible() and not self.parent():
                return

            label = f"Plotting GOES data for {event_date}…"
            self.update_job("goes", label)
            with busy(label, self):
                self._plot_goes(ts, event_date)
        finally:
            self._goes_ended()

    def _plot_goes(self, ts, event_date):
        """Plot the GOES data ``ts`` in a figure of its own."""
        try:
            ts_list = ts if isinstance(ts, list) else [ts]
            if not ts_list:
                return

            import numpy as np
            from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
            from matplotlib.figure import Figure
            from pandas.plotting import register_matplotlib_converters

            register_matplotlib_converters()

            # Create figure
            fig = Figure(figsize=(12, 6), tight_layout=True)
            ax = fig.add_subplot()
            levels = []  # what is drawn in the theme's colours

            # Try native SunPy TimeSeries.plot() first, fallback to manual plotting if it fails
            # (e.g., due to xarray multi-dimensional indexing deprecation in newer versions)
            try:
                for t in ts_list:
                    t.plot(axes=ax)
            except (IndexError, TypeError, ValueError):
                # Fallback: manual plotting with proper GOES styling
                fig.clear()  # Clear the failed attempt
                ax = fig.add_subplot()

                # Color scheme for GOES channels
                colors = {
                    "xrsa": "#1f77b4",
                    "xrsb": "#d62728",
                }  # Blue for short, Red for long
                labels = {"xrsa": "GOES 0.5-4 Å", "xrsb": "GOES 1-8 Å"}

                for t in ts_list:
                    # Convert to DataFrame to avoid xarray multi-dimensional indexing deprecation
                    df = t.to_dataframe()

                    # Only plot the actual flux columns (xrsa and xrsb), not quality flags
                    for col in ["xrsa", "xrsb"]:
                        if col in df.columns:
                            data = df[col].values
                            # Filter out invalid values (zeros, negatives, NaN)
                            valid_mask = (data > 0) & np.isfinite(data)
                            times = df.index[valid_mask]
                            values = data[valid_mask]
                            ax.plot(
                                times,
                                values,
                                color=colors.get(col, "gray"),
                                label=labels.get(col, col),
                                linewidth=1.0,
                            )

                # Set logarithmic scale for Y-axis (essential for GOES plots)
                ax.set_yscale("log")

                # Set Y-axis limits and flare classification levels
                ax.set_ylim(1e-9, 1e-3)

                # Add flare classification horizontal lines and labels
                flare_levels = {
                    "A": 1e-8,
                    "B": 1e-7,
                    "C": 1e-6,
                    "M": 1e-5,
                    "X": 1e-4,
                }
                for flare_class, level in flare_levels.items():
                    levels.append(ax.axhline(y=level, linestyle="--", alpha=0.5, linewidth=0.8))
                    levels.append(
                        ax.text(
                            ax.get_xlim()[1],
                            level,
                            f" {flare_class}",
                            va="center",
                            ha="left",
                            fontsize=9,
                        )
                    )

                # Labels and formatting
                ax.set_xlabel("Time (UTC)")
                ax.set_ylabel("Flux (W/m²)")
                ax.legend(loc="upper right")
                ax.grid(True, alpha=0.3, which="both")

            ax.set_title(f"GOES X-ray flux, {event_date:%Y-%m-%d}")

            # A window of its own, kept until it is closed
            window = QDialog(None)
            window.setWindowTitle(f"GOES X-ray flux, {event_date:%Y-%m-%d}")
            window.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
            window.setAttribute(Qt.WA_DeleteOnClose)
            fit_to_screen(window, 1200, 640)
            layout = QVBoxLayout(window)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
            canvas = FigureCanvasQTAgg(fig)
            layout.addWidget(figure_toolbar(canvas, window))
            layout.addWidget(canvas, 1)

            def recolour(palette):
                for artist in levels:
                    artist.set_color(palette["text_secondary"])
                ax.tick_params(which="both", grid_color=palette["plot_grid"])

            themed(window, recolour)
            follow_theme(window, fig, canvas)
            self.image_viewers.append(window)
            window.finished.connect(lambda result, w=window: self._cleanup_viewer(w))
            window.show()

        except RuntimeError:
            pass
        except Exception as e:
            show_error(self, "Could not plot the GOES data", "The GOES data could not be plotted.", e)

    def on_goes_plot_error(self, error_msg):
        """Handle GOES fetch error."""
        if self.goes_worker is None:
            return  # cancelled
        self._goes_ended()
        show_error(
            self, "Could not fetch the GOES data",
            "The GOES data for this date could not be fetched. Check the connection and try again.",
            error_msg,
        )

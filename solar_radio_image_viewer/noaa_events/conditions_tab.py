"""
The solar events viewer's Conditions tab: solar wind, geomagnetic and
X-ray conditions, and the GOES X-ray plot.
"""

import logging
from datetime import date
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QFrame,
    QScrollArea,
    QMessageBox,
)
from PyQt5.QtCore import Qt
from ..ui.styles import set_tone, theme_manager

from .workers import GOESPlotWorker
logger = logging.getLogger(__name__)



def _tone_for(color_code):
    """The tone ("success", "warning", "error" or None) standing for a
    NOAA level colour."""
    code = (color_code or "").lower()
    if code in ("#4caf50", "#2e7d32", "green"):
        return "success"
    if code in ("#ff9800", "#ffc107", "#e65100", "orange", "amber"):
        return "warning"
    if code in ("#f44336", "#9c27b0", "#d32f2f", "red", "purple"):
        return "error"
    return None


class ConditionsTabMixin:
    """Builds and fills the Conditions tab.

    Mixed into :class:`NOAAEventsViewer`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``date_edit``, ``is_dark_theme``, ``plot_goes_btn``, ``progress``,
    ``summary_label``, ``tabs``
    """

    def _create_conditions_tab(self):
        """Solar Conditions tab: geomagnetic, solar wind and F10.7 cards."""
        palette = theme_manager.palette

        # Tab 3: Solar Conditions (Real-time data)
        conditions_tab = QWidget()
        conditions_layout = QVBoxLayout(conditions_tab)
        conditions_layout.setContentsMargins(16, 12, 16, 12)
        conditions_layout.setSpacing(12)

        # Geomagnetic Activity Card - modern styling
        geo_card = QFrame()
        geo_card.setObjectName("Card")
        geo_layout = QVBoxLayout(geo_card)
        geo_layout.setContentsMargins(20, 20, 20, 20)

        geo_title = QLabel("Geomagnetic Activity (Daily)")
        geo_title.setObjectName("SectionTitle")
        geo_layout.addWidget(geo_title)

        self.geo_ap_label = QLabel("Ap Index: —")
        self.geo_kp_max_label = QLabel("Kp max: —")
        self.geo_kp_avg_label = QLabel("Kp avg: —")
        self.geo_kp_vals_label = QLabel("3-hour Kp values: —")
        self.geo_storm_label = QLabel("Storm Level: —")

        for lbl in [
            self.geo_ap_label,
            self.geo_kp_max_label,
            self.geo_kp_avg_label,
            self.geo_storm_label,
            self.geo_kp_vals_label,
        ]:
            lbl.setIndent(12)
            geo_layout.addWidget(lbl)

        conditions_layout.addWidget(geo_card)

        # Solar Wind Card - modern styling
        self.wind_card = QFrame()
        self.wind_card.setObjectName("Card")
        wind_layout = QVBoxLayout(self.wind_card)
        wind_layout.setContentsMargins(20, 20, 20, 20)

        wind_title = QLabel("Solar Wind (Real-time)")
        wind_title.setObjectName("SectionTitle")
        wind_layout.addWidget(wind_title)

        self.sw_speed_label = QLabel("Speed: — km/s")
        self.sw_density_label = QLabel("Density: — p/cm³")
        self.sw_temp_label = QLabel("Temperature: — K")
        self.sw_status_label = QLabel("Status: —")

        for lbl in [
            self.sw_speed_label,
            self.sw_density_label,
            self.sw_temp_label,
            self.sw_status_label,
        ]:
            lbl.setIndent(12)
            wind_layout.addWidget(lbl)

        conditions_layout.addWidget(self.wind_card)
        self.wind_card.hide()  # Only show when available

        # F10.7 Flux card - modern styling
        f107_card = QFrame()
        f107_card.setObjectName("Card")
        f107_layout = QVBoxLayout(f107_card)
        f107_layout.setContentsMargins(20, 20, 20, 20)

        f107_title = QLabel("Solar Indices (Daily)")
        f107_title.setObjectName("SectionTitle")
        f107_layout.addWidget(f107_title)

        self.f107_value_label = QLabel("Flux: — sfu")
        self.sunspot_area_label = QLabel("Sunspot Area: —")
        # self.xray_bg_label = QLabel("X-Ray Background: —")
        self.f107_activity_label = QLabel("Activity Level: —")

        # for lbl in [self.f107_value_label, self.sunspot_area_label, self.xray_bg_label, self.f107_activity_label]:
        for lbl in [
            self.f107_value_label,
            self.sunspot_area_label,
            self.f107_activity_label,
        ]:
            lbl.setIndent(12)
            f107_layout.addWidget(lbl)

        conditions_layout.addWidget(f107_card)

        # Conditions info label - theme-aware
        self.conditions_info_label = QLabel(
            "Real-time solar conditions from NOAA SWPC"
        )
        self.conditions_info_label.setWordWrap(True)
        self.conditions_info_label.setObjectName("Hint")
        conditions_layout.addWidget(self.conditions_info_label)

        conditions_layout.addStretch()

        # Solar Conditions credits label
        conditions_credits_label = QLabel(
            'Data Source: <a href="https://www.swpc.noaa.gov/">NOAA SWPC</a> '
            "(Space Weather Prediction Center)"
        )
        conditions_credits_label.setOpenExternalLinks(True)
        conditions_credits_label.setObjectName("Hint")
        conditions_layout.addWidget(conditions_credits_label)

        # Make conditions tab scrollable
        conditions_scroll = QScrollArea()
        conditions_scroll.setWidgetResizable(True)
        conditions_scroll.setFrameShape(QFrame.NoFrame)
        conditions_scroll.setWidget(conditions_tab)
        self.tabs.addTab(conditions_scroll, "Solar Conditions")

    def display_solar_conditions(self, conditions):
        """Display solar conditions for the selected date."""
        if conditions is None:
            self.conditions_info_label.setText(
                "Unable to fetch solar conditions data"
            )
            self.geo_ap_label.setText("Ap Index: —")
            self.geo_kp_max_label.setText("Kp max: —")
            self.geo_kp_avg_label.setText("Kp avg: —")
            self.geo_kp_vals_label.setText("3-hour Kp/Ap values: —")
            self.geo_storm_label.setText("Storm Level: —")
            self.wind_card.hide()
            return

        # Update title label to show data source
        self.conditions_info_label.setText(f"{conditions.data_source}")

        # 1. Geomagnetic Data (Kp)
        if conditions.kp_index:
            kp = conditions.kp_index
            self.geo_ap_label.setText(f"Ap Index: {kp.ap_value}")
            self.geo_kp_max_label.setText(f"Kp max: {kp.kp_max:.0f}")
            self.geo_kp_avg_label.setText(f"Kp avg: {kp.kp_avg:.1f}")
            self.geo_kp_vals_label.setText(
                f"8 Kp values: {', '.join([f'{v:.0f}' for v in kp.kp_values])}"
            )
            set_tone(self.geo_kp_vals_label, None)

            self.geo_storm_label.setText(f"Storm Level: {kp.storm_level}")
            set_tone(self.geo_storm_label, _tone_for(kp.color_code))
        else:
            self.geo_ap_label.setText("Ap Index: —")
            self.geo_kp_max_label.setText("Kp max: —")
            self.geo_kp_avg_label.setText("Kp avg: —")
            self.geo_kp_vals_label.setText("No geomagnetic data for this date")
            self.geo_storm_label.setText("Storm Level: Data unavailable")

        # 2. Solar Wind Data (Real-time only)
        if hasattr(conditions, "solar_wind") and conditions.solar_wind:
            sw = conditions.solar_wind
            self.wind_card.show()
            self.sw_speed_label.setText(f"Speed: {sw.speed:.0f} km/s")
            self.sw_density_label.setText(f"Density: {sw.density:.1f} p/cm³")
            self.sw_temp_label.setText(f"Temperature: {sw.temperature:.0f} K")

            status_text = sw.speed_category
            self.sw_status_label.setText(f"Status: {status_text} Speed")
            set_tone(
                self.sw_status_label,
                {"High": "error", "Elevated": "warning", "Normal": "success"}.get(status_text),
            )
        else:
            self.wind_card.hide()

        # F10.7 Flux (historical daily data)
        if conditions.f107_flux:
            f107 = conditions.f107_flux
            self.f107_value_label.setText(
                f"10.7cm Flux: {f107.flux_value:.1f} sfu (Sunspot #: {f107.sunspot_number})"
            )

            area = getattr(f107, "sunspot_area", "—")
            area_str = f"{area} (10⁻⁶ Hemis.)" if area != "—" else "—"
            self.sunspot_area_label.setText(f"Sunspot Area: {area_str}")


            self.f107_activity_label.setText(f"Activity Level: {f107.activity_level}")
            set_tone(
                self.f107_activity_label,
                {
                    "Very Low": "success",
                    "Low": "success",
                    "Moderate": "warning",
                    "Elevated": "warning",
                    "High": "error",
                    "Very High": "error",
                }.get(f107.activity_level),
            )
        else:
            self.f107_value_label.setText("10.7cm Flux: — sfu")
            self.sunspot_area_label.setText("Sunspot Area: —")
            # self.xray_bg_label.setText("X-Ray Background: —")
            self.f107_activity_label.setText("Activity Level: Data unavailable")

    def plot_goes_xray(self):
        """Fetch and plot GOES X-ray flux for the selected date."""
        if (
            hasattr(self, "goes_worker")
            and self.goes_worker
            and self.goes_worker.isRunning()
        ):
            return

        qdate = self.date_edit.date()
        selected_date = date(qdate.year(), qdate.month(), qdate.day())

        # Save current summary to restore later
        self.previous_summary = self.summary_label.text()
        self.summary_label.setText(f"Fetching GOES data for {selected_date}...")
        self.progress.show()
        self.plot_goes_btn.setEnabled(False)

        from PyQt5.QtWidgets import QApplication

        QApplication.setOverrideCursor(Qt.WaitCursor)

        self.goes_worker = GOESPlotWorker(selected_date)
        self.goes_worker.finished.connect(self.on_goes_plot_ready)
        self.goes_worker.error.connect(self.on_goes_plot_error)
        self.goes_worker.start()

    def on_goes_plot_ready(self, ts):
        """Handle ready GOES data."""
        try:
            # Check validity
            if not self.isVisible() and not self.parent():
                return

            from PyQt5.QtWidgets import QApplication

            QApplication.restoreOverrideCursor()
            self.progress.hide()
            self.plot_goes_btn.setEnabled(True)

            # Restore previous summary
            if hasattr(self, "previous_summary"):
                self.summary_label.setText(self.previous_summary)
            else:
                self.summary_label.setText(
                    f"GOES data loaded for {self.date_edit.date().toString('yyyy-MM-dd')}"
                )

            ts_list = ts if isinstance(ts, list) else [ts]
            if not ts_list:
                return

            import matplotlib.pyplot as plt
            import numpy as np
            from pandas.plotting import register_matplotlib_converters

            register_matplotlib_converters()

            # Create figure
            fig, ax = plt.subplots(figsize=(12, 6))

            # Try native SunPy TimeSeries.plot() first, fallback to manual plotting if it fails
            # (e.g., due to xarray multi-dimensional indexing deprecation in newer versions)
            try:
                for t in ts_list:
                    t.plot(axes=ax)
            except (IndexError, TypeError, ValueError):
                # Fallback: manual plotting with proper GOES styling
                plt.close(fig)  # Close the failed figure
                fig, ax = plt.subplots(figsize=(12, 6))

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
                    ax.axhline(
                        y=level, color="gray", linestyle="--", alpha=0.5, linewidth=0.8
                    )
                    ax.text(
                        ax.get_xlim()[1],
                        level,
                        f" {flare_class}",
                        va="center",
                        ha="left",
                        fontsize=10,
                        color="gray",
                    )

                # Labels and formatting
                ax.set_xlabel("Time (UTC)")
                ax.set_ylabel("Flux (W/m²)")
                ax.legend(loc="upper right")
                ax.grid(True, alpha=0.3, which="both")

            ax.set_title(
                f"GOES X-ray Flux - {self.date_edit.date().toString('yyyy-MM-dd')}"
            )

            plt.tight_layout()
            plt.show(block=False)

        except RuntimeError:
            pass
        except Exception as e:
            QMessageBox.warning(
                self, "Plot Error", f"Failed to plot GOES data:\n{str(e)}"
            )

    def on_goes_plot_error(self, error_msg):
        """Handle GOES fetch error."""
        from PyQt5.QtWidgets import QApplication

        QApplication.restoreOverrideCursor()
        self.progress.hide()
        self.plot_goes_btn.setEnabled(True)
        # Restore previous summary
        if hasattr(self, "previous_summary"):
            self.summary_label.setText(self.previous_summary)
        else:
            self.summary_label.setText("Error fetching GOES data")
        QMessageBox.warning(
            self, "GOES Error", f"Failed to fetch GOES data:\n{error_msg}"
        )

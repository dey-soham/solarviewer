#!/usr/bin/env python3
"""
Solar Activity Viewer GUI - Comprehensive solar context data display.
"""

import sys
import os
import re
from datetime import datetime, date, timedelta
from typing import Optional
import logging

# Qt imports
try:
    from PyQt5.QtWidgets import (
        QMainWindow,
        QWidget,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QLineEdit,
        QFrame,
        QScrollArea,
        QSizePolicy,
        QMessageBox,
        QTabWidget,
    )
    from PyQt5.QtCore import QTimer
    from PyQt5.QtNetwork import QNetworkAccessManager
except ImportError:
    print("PyQt5 is required. Install with: pip install PyQt5")
    sys.exit(1)

try:
    # Try relative imports (when run as module)
    from . import noaa_events as ne
    from ..ui.styles import SPACE_L, SPACE_M, SPACE_S, theme_manager, set_hand_cursor
except ImportError:
    # Fallback for standalone execution
    # Add project root to path to allow absolute imports
    import sys
    import os

    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(current_dir, "../../"))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from solar_radio_image_viewer.noaa_events import noaa_events as ne
    from solar_radio_image_viewer.ui.styles import SPACE_L, SPACE_M, SPACE_S, theme_manager, set_hand_cursor
from .cache import _noaa_cache
from .conditions_tab import ConditionsTabMixin
from .context_images_tab import ContextImagesTabMixin
from .radio_spectra_tab import RadioSpectraTabMixin
from .widgets import CollapsibleSection, EventTable, source_note
from .workers import FetchWorker
from ..processing.fits_io import is_fits
from ..processing.image_meta import casa_info, casa_obs_time, fits_header


from ..ui.activity import ActivityLabel
from ..ui.background import Cancelled, run_in_background
from ..ui.busy import busy
from ..ui.date_picker import attach_date_picker
from ..ui.download_parts import abandon
from ..ui.errors import show_error
from ..ui.geometry import fit_to_screen

logger = logging.getLogger(__name__)

DATE_FORMAT = "%Y-%m-%d"
CANCEL_NOTE_MS = 4000  # how long "Cancelled" stays beside the buttons


class NOAAEventsViewer(
    ConditionsTabMixin, RadioSpectraTabMixin, ContextImagesTabMixin, QMainWindow
):
    """Main Solar Activity Viewer window - displays events, active regions, conditions, and CMEs."""

    def __init__(self, parent=None, initial_date: Optional[date] = None):
        super().__init__(parent)
        self.setWindowTitle("Solar activity")
        fit_to_screen(self, 1000, 800)

        # Network Manager for image downloading
        self.nam = QNetworkAccessManager(self)
        self.image_downloads = {}  # Keep references to replies
        self.image_viewers = []  # Keep references to open image windows

        self.worker = None
        self.goes_worker = None
        self.dh_worker = None
        self.events = []
        self.download_queue = []
        self.image_savers = {}  # file path -> its ImageSaver
        self.shown_date = None  # the date the tables and images are of
        self._spectra_date = None  # the date of the last spectra fetched
        self._jobs = {}  # what is running, oldest first: key -> [text, how to cancel it]

        self.is_dark_theme = theme_manager.is_dark

        self.init_ui()

        # Set initial date
        if initial_date:
            self._set_date(initial_date)
        else:
            # Default to yesterday
            self._set_date(date.today() - timedelta(days=1))
        self.shown_date = self.selected_date()

        set_hand_cursor(self)

    def _set_date(self, value):
        self.date_edit.setText(value.strftime(DATE_FORMAT))

    def selected_date(self) -> Optional[date]:
        """The date in the field; if it is not one, None, and it says so."""
        try:
            # (YYYY.MM.DD, the form it had, still reads)
            return datetime.strptime(self.date_edit.text().strip().replace(".", "-"), DATE_FORMAT).date()
        except ValueError:
            self.summary_label.setText("Enter the date as YYYY-MM-DD.")
            return None

    def closeEvent(self, event):
        """Leave what is running to end by itself: a stalled server must not
        hold the window up as it closes."""
        for _, cancel in reversed(list(self._jobs.values())):
            cancel()
        self._jobs.clear()
        self.stop_dh_spectra()
        self.leave_image_downloads()
        self._note_timer.stop()
        self._show_jobs()
        super().closeEvent(event)

    # What is running, beside the buttons: the fetch, the GOES plot and saved
    # images overlap, and the line lasts until the last of them has ended

    def begin_job(self, key, text, cancel):
        """Show ``text`` until ``end_job(key)``; ``cancel()`` is what the
        Cancel button calls to stop this job."""
        self._jobs[key] = [text, cancel]
        self._note_timer.stop()
        self._show_jobs()

    def update_job(self, key, text):
        if key in self._jobs:
            self._jobs[key][0] = text
            self._show_jobs()

    def end_job(self, key):
        self._jobs.pop(key, None)
        self._show_jobs()

    def _show_jobs(self):
        """The latest job is the one shown, and the one Cancel stops."""
        running = bool(self._jobs)
        self.cancel_btn.setVisible(running)
        if running:
            self.activity_line.start(list(self._jobs.values())[-1][0])
            self.activity_line.show()
        elif not self._note_timer.isActive():
            self.activity_line.stop()
            self.activity_line.hide()

    def cancel_latest_job(self):
        if not self._jobs:
            return
        key = list(self._jobs)[-1]
        cancel = self._jobs[key][1]
        self._jobs.pop(key)
        cancel()
        if self._jobs:
            self._show_jobs()
        else:
            self.activity_line.stop("Cancelled")
            self.activity_line.show()
            self.cancel_btn.hide()
            self._note_timer.start()

    def init_ui(self):
        """Initialize the user interface."""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(SPACE_M)
        layout.setContentsMargins(SPACE_L, SPACE_L, SPACE_L, SPACE_L)

        # Top bar: date selection
        top_bar = QHBoxLayout()
        top_bar.setSpacing(SPACE_S)

        date_label = QLabel("Date")
        date_label.setObjectName("Hint")
        top_bar.addWidget(date_label)

        self.date_edit = QLineEdit()
        self.date_edit.setMinimumWidth(130)
        self.date_edit.setMaximumWidth(180)
        attach_date_picker(self.date_edit, lambda text: None, fmt=DATE_FORMAT)
        # Styles handled by global stylesheet
        top_bar.addWidget(self.date_edit)

        # Get date from current tab button
        self.get_date_btn = QPushButton("From tab")
        self.get_date_btn.setToolTip("Take the date from the image or FITS file open now")
        self.get_date_btn.clicked.connect(self.get_date_from_parent_tab)
        if not self.parent():
            self.get_date_btn.setEnabled(False)
            self.get_date_btn.setToolTip("Not available in independent mode")
        top_bar.addWidget(self.get_date_btn)

        top_bar.addStretch()

        self.activity_line = ActivityLabel()
        self.activity_line.hide()
        top_bar.addWidget(self.activity_line)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.cancel_latest_job)
        self.cancel_btn.hide()
        top_bar.addWidget(self.cancel_btn)

        self._note_timer = QTimer(self)
        self._note_timer.setSingleShot(True)
        self._note_timer.setInterval(CANCEL_NOTE_MS)
        self._note_timer.timeout.connect(self._show_jobs)

        self.plot_goes_btn = QPushButton("Plot GOES X-ray flux")
        self.plot_goes_btn.setObjectName("GhostButton")
        self.plot_goes_btn.setToolTip("Plot the GOES X-ray light curve for this date")
        self.plot_goes_btn.clicked.connect(self.plot_goes_xray)
        top_bar.addWidget(self.plot_goes_btn)

        self.fetch_btn = QPushButton("Fetch")
        self.fetch_btn.setObjectName("PrimaryButton")
        self.fetch_btn.clicked.connect(self.fetch_data)
        top_bar.addWidget(self.fetch_btn)

        layout.addLayout(top_bar)

        self.summary_frame = QFrame()

        self.summary_frame.setObjectName("Card")
        summary_layout = QHBoxLayout(self.summary_frame)
        summary_layout.setContentsMargins(SPACE_M, SPACE_S, SPACE_M, SPACE_S)

        self.summary_label = QLabel("Pick a date and press Fetch to see that day's solar activity.")
        self.summary_label.setObjectName("Hint")
        self.summary_label.setWordWrap(True)
        summary_layout.addWidget(self.summary_label)

        layout.addWidget(self.summary_frame)

        self.tabs = QTabWidget()

        self._create_events_tab()

        self._create_active_regions_tab()

        self._create_conditions_tab()

        self._create_cme_tab()

        self._create_context_images_tab()

        self._create_radio_spectra_tab()

        layout.addWidget(self.tabs)

    def _create_events_tab(self):
        """Events tab: X-ray, optical and radio event tables."""
        # Tab 1: Solar Events (existing content)
        events_tab = QWidget()
        events_layout = QVBoxLayout(events_tab)
        events_layout.setContentsMargins(0, SPACE_M, 0, 0)
        events_layout.setSpacing(SPACE_S)

        # X-ray Flares section
        self.xray_section = CollapsibleSection("X-ray flares")
        self.xray_table = EventTable(
            ["Time (UTC)", "Class", "Integrated flux (J/m²)", "Region", "Duration", "Observatory"],
            empty_text="Fetch a date to see its X-ray flares",
        )
        self.xray_section.add_widget(self.xray_table)
        events_layout.addWidget(self.xray_section)

        # Optical Flares section
        self.optical_section = CollapsibleSection("Optical flares (H-alpha)")
        self.optical_table = EventTable(
            ["Time (UTC)", "Class", "Location", "Region", "Notes", "Observatory"],
            empty_text="Fetch a date to see its optical flares",
        )
        self.optical_section.add_widget(self.optical_table)
        events_layout.addWidget(self.optical_section)

        # Radio Events section
        self.radio_section = CollapsibleSection("Radio events")
        self.radio_table = EventTable(
            ["Type", "Time (UTC)", "Frequency", "Particulars", "Region", "Observatory"],
            empty_text="Fetch a date to see its radio events",
        )
        self.radio_section.add_widget(self.radio_table)
        events_layout.addWidget(self.radio_section)

        # Connect signals for dynamic layout
        self.xray_section.toggled.connect(self.update_events_layout_logic)
        self.optical_section.toggled.connect(self.update_events_layout_logic)
        self.radio_section.toggled.connect(self.update_events_layout_logic)

        # Dynamic spacer - stays hidden unless all sections are collapsed
        self.events_bottom_spacer = QWidget()
        self.events_bottom_spacer.setSizePolicy(
            QSizePolicy.Preferred, QSizePolicy.Expanding
        )
        events_layout.addWidget(self.events_bottom_spacer)

        # Solar Events credits label
        events_credits_label = source_note(
            'Data sources: <a href="https://solarmonitor.org/">SolarMonitor.org</a> (NOAA/SWPC), '
            '<a href="https://soleil.i4ds.ch/solarradio/">e-CALLISTO</a> (C. Monstein)'
        )
        events_layout.addWidget(events_credits_label)

        # Initial logic check
        self.update_events_layout_logic()

        # Make events tab scrollable
        events_scroll = QScrollArea()
        events_scroll.setWidgetResizable(True)
        events_scroll.setFrameShape(QFrame.NoFrame)
        events_scroll.setWidget(events_tab)
        self.tabs.addTab(events_scroll, "Events")

    def _create_active_regions_tab(self):
        """Active Regions tab."""
        # Tab 2: Active Regions
        ar_tab = QWidget()
        ar_layout = QVBoxLayout(ar_tab)
        ar_layout.setContentsMargins(0, SPACE_M, 0, 0)
        ar_layout.setSpacing(SPACE_S)

        # Active regions table
        self.ar_table = EventTable(
            [
                "AR#",
                "Location",
                "Area",
                "McIntosh",
                "Mag type",
                "C%",
                "M%",
                "X%",
                "Flare potential",
            ],
            empty_text="Fetch a date to see its active regions and flare probabilities",
        )
        ar_layout.addWidget(self.ar_table)

        # AR info label
        self.ar_info_label = QLabel()
        self.ar_info_label.setWordWrap(True)
        self.ar_info_label.setObjectName("Hint")
        self.ar_info_label.hide()
        ar_layout.addWidget(self.ar_info_label)

        # Active Regions credits label
        ar_credits_label = source_note(
            'Data source: <a href="https://solarmonitor.org/">SolarMonitor.org</a> '
            "(NOAA/SWPC Solar Region Summary &amp; ARM Flare Forecasts)"
        )
        ar_layout.addWidget(ar_credits_label)

        # ar_layout.addStretch()

        # Make AR tab scrollable
        ar_scroll = QScrollArea()
        ar_scroll.setWidgetResizable(True)
        ar_scroll.setFrameShape(QFrame.NoFrame)
        ar_scroll.setWidget(ar_tab)
        self.tabs.addTab(ar_scroll, "Active regions")

    def _create_cme_tab(self):
        """CMEs tab."""
        # Tab 4: CME Alerts
        cme_tab = QWidget()
        cme_layout = QVBoxLayout(cme_tab)
        cme_layout.setContentsMargins(0, SPACE_M, 0, 0)
        cme_layout.setSpacing(SPACE_S)

        # CME table
        self.cme_table = EventTable(
            [
                "Time (UTC)",
                "Speed (km/s)",
                "Source",
                "Half angle",
                "Earth-directed",
            ],
            empty_text="Fetch a date to see the CMEs around it",
        )
        cme_layout.addWidget(self.cme_table)

        self.cme_info_label = QLabel(
            "CME data from NASA DONKI (±3 days from selected date)"
        )
        self.cme_info_label.setWordWrap(True)
        self.cme_info_label.setObjectName("Hint")
        cme_layout.addWidget(self.cme_info_label)

        # CME credits label with link
        cme_credits_label = source_note(
            'Data source: <a href="https://kauai.ccmc.gsfc.nasa.gov/DONKI/">NASA DONKI</a> '
            "(Space Weather Database Of Notifications, Knowledge, Information)"
        )
        cme_layout.addWidget(cme_credits_label)

        # Make CME tab scrollable
        cme_scroll = QScrollArea()
        cme_scroll.setWidgetResizable(True)
        cme_scroll.setFrameShape(QFrame.NoFrame)
        cme_scroll.setWidget(cme_tab)
        self.tabs.addTab(cme_scroll, "CMEs")

    def update_events_layout_logic(self, *args):
        """Show/hide bottom spacer based on whether any section is open."""
        any_open = not (
            self.xray_section.is_collapsed
            and self.optical_section.is_collapsed
            and self.radio_section.is_collapsed
        )

        # If any section is open, hide spacer so the open section can expand
        # If all are closed, show spacer to push headers to the top
        if hasattr(self, "events_bottom_spacer"):
            self.events_bottom_spacer.setVisible(not any_open)

    def fetch_data(self):
        """Start fetching data for the selected date."""
        if self.worker and self.worker.isRunning():
            return

        selected_date = self.selected_date()
        if selected_date is None:
            return

        # Clear radio spectra UI immediately on new fetch
        self.stop_dh_spectra()
        self.clear_dh_spectra_ui()

        self.date_edit.setEnabled(False)
        self.fetch_btn.setEnabled(False)
        self._summary_before = self.summary_label.text()
        self.summary_label.setText(f"Fetching data for {selected_date}…")
        self.begin_job("fetch", "Starting…", self._cancel_fetch)

        # Clean up old worker if exists
        if self.worker is not None:
            self.worker.deleteLater()

        self.worker = FetchWorker(selected_date)
        self.worker.done.connect(self.on_fetch_finished)
        self.worker.error.connect(self.on_fetch_error)
        self.worker.progress.connect(self.on_fetch_progress)
        self.worker.start()

        # [AUTO-FETCH SPECTRA] If radio spectra are already cached, load them automatically
        if _noaa_cache.has_any_spectra(selected_date):
            self.fetch_dh_spectra()

    def on_fetch_progress(self, message):
        self.update_job("fetch", message)

    def _fetch_ended(self):
        self.end_job("fetch")
        self.date_edit.setEnabled(True)
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("Fetch")

    def _cancel_fetch(self):
        """Back to the tables and summary from before the fetch. The spectra
        went when it started; those of the new date do not stay beside them."""
        abandon(self.worker)
        self.worker = None
        self._fetch_ended()
        self.summary_label.setText(self._summary_before)
        if self._spectra_date != self.shown_date:
            self.stop_dh_spectra()
            self.clear_dh_spectra_ui()

    def on_fetch_finished(self, events, active_regions, conditions, cmes, images, ecallisto_bursts=None):
        """Handle fetched data."""
        if self.worker is None:
            return  # cancelled
        ecallisto_bursts = ecallisto_bursts or []
        event_date = self.worker.event_date
        try:
            # Check for validity
            if not self.isVisible() and not self.parent():
                return  # Window closed

            self.events = events
            self.shown_date = event_date

            label = f"Showing the data for {event_date}…"
            self.update_job("fetch", label)
            with busy(label, self):
                # Display events
                self.display_events(events, ecallisto_bursts)

                # Display active regions
                self.display_active_regions(active_regions)

                # Display conditions
                self.display_solar_conditions(conditions)

                # Display CMEs
                self.display_cme_events(cmes)

                # Display images
                self.display_context_images(images)

                # Update comprehensive summary
                self._update_comprehensive_summary(events, active_regions, conditions, cmes)

        except RuntimeError:
            # Widget deleted during update
            pass
        finally:
            self._fetch_ended()

    def _update_comprehensive_summary(self, events, active_regions, conditions, cmes):
        """Update the main summary label with a comprehensive overview of all data."""
        summary_parts = []

        # 1. Active Regions
        ar_count = len(active_regions) if active_regions else 0
        if ar_count > 0:
            summary_parts.append(f"Regions: {ar_count}")
        elif active_regions is not None:
            summary_parts.append("Regions: 0")

        # 2. Sunspots & Flux (from conditions)
        if conditions and conditions.f107_flux:
            ssn = conditions.f107_flux.sunspot_number
            flux = conditions.f107_flux.flux_value
            summary_parts.append(f"Sunspots: {ssn}")
            summary_parts.append(f"Flux: {flux:.0f} sfu")

        # 3. Solar Flares (from events)
        if events:
            categories = ne.categorize_events(events)
            xray = categories.get("xray", [])
            stats = ne.get_event_statistics(events)
            max_class = stats.get("max_xray_class", None)

            flare_part = f"Flares: {len(xray)}"
            if max_class:
                flare_part += f" (Max: {max_class})"
            summary_parts.append(flare_part)
        elif events is not None:  # None: the list could not be fetched
            summary_parts.append("Flares: 0")

        # 4. CMEs
        if cmes:
            cme_count = len(cmes)
            earth_directed = sum(1 for cme in cmes if cme.is_earth_directed)
            cme_text = f"CMEs: {cme_count}"
            if earth_directed > 0:
                cme_text += f" ({earth_directed} Earth-directed)"
            summary_parts.append(cme_text)
        elif cmes is not None:
            summary_parts.append("CMEs: 0")

        if not summary_parts:
            self.summary_label.setText("No data available for this date.")
        else:
            self.summary_label.setText(" · ".join(summary_parts))

    def on_fetch_error(self, error_msg):
        """Handle fetch error."""
        if self.worker is None:
            return  # cancelled
        try:
            self._fetch_ended()

            # Check validity
            if not self.isVisible() and not self.parent():
                return

            self.summary_label.setText(f"Could not fetch the data: {error_msg}")
            show_error(
                self, "Could not fetch the data",
                "The data for this date could not be fetched. Check the connection and try again.",
                error_msg,
            )
        except RuntimeError:
            pass

    def clear_tables(self):
        """Clear all event tables."""
        self.xray_table.setRowCount(0)
        self.optical_table.setRowCount(0)
        self.radio_table.setRowCount(0)
        self.ar_table.setRowCount(0)
        self.cme_table.setRowCount(0)
        self.xray_section.set_count(0)
        self.optical_section.set_count(0)
        self.radio_section.set_count(0)

    def _say_when_empty(self, fetched):
        self.xray_table.set_empty_text("No X-ray flares on this date" if fetched else "Could not fetch the X-ray flares")
        self.optical_table.set_empty_text("No optical flares on this date" if fetched else "Could not fetch the optical flares")
        self.radio_table.set_empty_text("No radio events on this date" if fetched else "Could not fetch the radio events")

    def display_events(self, events, ecallisto_bursts=None):
        """Display events in categorized tables."""
        ecallisto_bursts = ecallisto_bursts or []
        self.clear_tables()
        self._say_when_empty(events is not None)

        if events is None:
            self.summary_label.setText("No data could be fetched.")
            return

        categories = ne.categorize_events(events)

        # Update summary - MOVED to _update_comprehensive_summary
        xray_count = len(categories["xray"])
        optical_count = len(categories["optical"])
        radio_count = len(categories["radio"]) + len(ecallisto_bursts)
        # max_class = stats.get("max_xray_class", "—")

        # summary_parts = []
        # if xray_count > 0:
        #     max_note = f" (max: {max_class})" if max_class else ""
        #     summary_parts.append(f"{xray_count} X-ray flare{'s' if xray_count > 1 else ''}{max_note}")
        # if optical_count > 0:
        #     summary_parts.append(f"{optical_count} Optical")
        # if radio_count > 0:
        #     summary_parts.append(f"{radio_count} Radio")

        # if summary_parts:
        #     self.summary_label.setText(" | ".join(summary_parts))
        # else:
        #     self.summary_label.setText("No significant events recorded for this date.")

        # Populate X-ray table
        self.xray_section.set_count(xray_count)
        with self.xray_table.filling():
            for event in sorted(categories["xray"], key=_time_order):
                duration = (
                    f"{event.duration_minutes} min" if event.duration_minutes else "—"
                )
                flare_class = event.flare_class or "—"
                peak_flux = (
                    event.particulars.split()[1]
                    if len(event.particulars.split()) > 1
                    else "—"
                )

                tones = {}
                if event.flare_class_letter in ["M", "X"]:
                    tones[1] = event.flare_class_tone

                self.xray_table.add_event_row(
                    [
                        event.time_range,
                        flare_class,
                        peak_flux,
                        event.active_region or "—",
                        duration,
                        event.observatory_name,
                    ],
                    tones,
                )

        # Populate Optical table
        self.optical_section.set_count(optical_count)
        with self.optical_table.filling():
            for event in sorted(
                categories["optical"], key=_time_order
            ):
                optical_class = event.optical_class or "—"
                notes_parts = event.particulars.split()[1:] if event.particulars else []
                notes = " ".join(notes_parts) if notes_parts else "—"

                self.optical_table.add_event_row(
                    [
                        event.time_range,
                        optical_class,
                        event.location_or_freq,
                        event.active_region or "—",
                        notes,
                        event.observatory_name,
                    ]
                )

        # Populate Radio table
        self.radio_section.set_count(radio_count)
        
        # Combine and sort radio events
        all_radio_events = []
        for e in categories["radio"]:
            all_radio_events.append({
                'type': e.event_type,
                'time': e.time_range,
                'freq': e.location_or_freq,
                'particulars': e.particulars,
                'region': e.active_region or "—",
                'obs': e.observatory_name,
                'sort_time': _time_order(e)
            })
            
        for b in ecallisto_bursts:
            all_radio_events.append({
                'type': 'RSP',
                'time': b.time_range,
                'freq': "—",
                'particulars': b.burst_type,
                'region': "—",
                'obs': b.stations,
                'sort_time': b.begin_time.replace(":", "") or "9999"
            })
            
        with self.radio_table.filling():
            for event in sorted(all_radio_events, key=lambda x: x['sort_time']):
                tooltips = {}
                display_values = [
                    event['type'],
                    event['time'],
                    event['freq'],
                    event['particulars'],
                    event['region'],
                    event['obs'],
                ]
            
                # Truncate long station lists for display and show full in tooltip
                if event['obs'] and len(event['obs']) > 30 and ',' in event['obs']:
                    stations = [s.strip() for s in event['obs'].split(',')]
                    if len(stations) > 2:
                        display_values[5] = f"{stations[0]}, {stations[1]} ..."
                        tooltips[5] = event['obs']  # Full list in tooltip

                self.radio_table.add_event_row(display_values, tooltips=tooltips)

        self.xray_table.scrollToTop()
        self.optical_table.scrollToTop()
        self.radio_table.scrollToTop()

    def display_active_regions(self, regions):
        """Display active regions in the AR table."""
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

        self.ar_info_label.setText(f"Found {len(regions)} active regions.")
        self.ar_info_label.show()

        # Tones of the risk levels
        risk_tones = {
            "Very High": "error",
            "High": "warning",
            "Moderate": "warning",
            "Low": "success",
            "Quiet": "text_secondary",
        }

        with self.ar_table.filling():
            for region in sorted(regions, key=lambda r: r.area, reverse=True):
                # Format probabilities
                c_prob = f"{region.prob_c}%" if region.prob_c is not None else "—"
                m_prob = f"{region.prob_m}%" if region.prob_m is not None else "—"
                x_prob = f"{region.prob_x}%" if region.prob_x is not None else "—"

                risk = region.flare_risk_level

                # Add row with a tone for the risk level column
                tones = {8: risk_tones.get(risk, "text_secondary")}

                # Also M% and X% if they're significant
                if region.prob_m and region.prob_m >= 20:
                    tones[6] = "warning"
                if region.prob_x and region.prob_x >= 5:
                    tones[7] = "error"

                self.ar_table.add_event_row(
                    [
                        f"AR{region.noaa_number}",
                        region.location,
                        str(region.area),
                        region.mcintosh_class,
                        region.mag_type,
                        c_prob,
                        m_prob,
                        x_prob,
                        risk,
                    ],
                    tones,
                )

        self.ar_table.scrollToTop()

    def display_cme_events(self, cmes):
        """Display CME events in the CME table."""
        self.cme_table.setRowCount(0)

        if cmes is None or len(cmes) == 0:
            text = (
                "Could not fetch the CMEs for this date."
                if cmes is None
                else "No CME activity detected in the ±3 day range for this date."
            )
            self.cme_table.set_empty_text(text)
            self.cme_info_label.setText(text)
            self.cme_info_label.show()
            return

        if cmes[0].source == "CDAW":
            self.cme_info_label.setText(
                f"Found {len(cmes)} CME events (±3 days from selected date) in the "
                "CDAW LASCO catalogue. NASA DONKI could not be reached, so their "
                "source and Earth direction are not known."
            )
        else:
            self.cme_info_label.setText(
                f"Found {len(cmes)} CME events (±3 days from selected date)"
            )

        with self.cme_table.filling():
            for cme in cmes:
                # Format columns
                time_str = cme.start_time.strftime("%Y-%m-%d %H:%M")
                speed_str = f"{cme.speed:.0f}"
                width_str = f"{cme.half_angle:.0f}°" if cme.half_angle else "—"
                earth_str = (
                    "—" if cme.is_earth_directed is None
                    else "Yes" if cme.is_earth_directed else "No"
                )

                tones = {}

                # The Earth-directed column
                if cme.is_earth_directed:
                    tones[4] = "error" if cme.speed >= 1000 else "warning"

                # The speed column, by category
                speed_tones = {
                    "Slow": "success",
                    "Moderate": "warning",
                    "Fast": "warning",
                    "Extreme": "error",
                }
                tones[1] = speed_tones.get(cme.speed_category, "text_secondary")

                self.cme_table.add_event_row(
                    [
                        time_str,
                        speed_str,
                        cme.source_location,
                        width_str,
                        earth_str,
                    ],
                    tones,
                )

        self.cme_table.scrollToTop()

    def get_date_from_parent_tab(self):
        """Extract date from the currently open tab in the parent viewer.

        Uses the same logic as the viewer's figure title date extraction.
        """
        try:
            # Get parent main window
            parent = self.parent()
            if parent is None:
                QMessageBox.information(
                    self, "No image", "No parent viewer found. Open an image first."
                )
                return

            # Try to get current tab
            current_tab = None
            if hasattr(parent, "tab_widget"):
                current_tab = parent.tab_widget.currentWidget()

            if current_tab is None:
                QMessageBox.information(self, "No image", "No image is currently open.")
                return

            extracted_date = None
            image_time = None
            imagename = getattr(current_tab, "imagename", None)

            # Method 1: Try FITS header from tab attribute first
            header = getattr(current_tab, "current_header", None) or None

            # Method 1b: If no header attribute, read FITS/FTS file directly
            if header is None and imagename:
                if is_fits(imagename):
                    try:
                        header = run_in_background(
                            self, f"Reading the date of {os.path.basename(imagename)}…", fits_header, imagename
                        )
                    except Cancelled:
                        raise
                    except Exception as fits_err:
                        logger.warning("FITS header read failed: %s", fits_err)

            # Extract date from header
            if header is not None:
                # Check DATE-OBS (standard), DATE_OBS (IRIS), and STARTOBS
                image_time = (
                    header.get("DATE-OBS")
                    or header.get("DATE_OBS")
                    or header.get("STARTOBS")
                )

                # Special handling for SOHO (DATE-OBS + TIME-OBS)
                if (
                    header.get("TELESCOP") == "SOHO"
                    and header.get("TIME-OBS")
                    and image_time
                ):
                    image_time = f"{image_time}T{header['TIME-OBS']}"

                if image_time:
                    extracted_date = self._parse_date_string(str(image_time))

            # Method 2: CASA image - read csys_record directly from file (like viewer.py)
            if extracted_date is None and imagename:
                # Check if it's a CASA image (directory, not .fits/.fts)
                is_casa_image = not is_fits(imagename)

                if is_casa_image:
                    try:
                        info = run_in_background(
                            self, f"Reading the date of {os.path.basename(imagename)}…", casa_info, imagename
                        )
                        t = casa_obs_time(info.csys_record)
                        if t is not None:
                            extracted_date = t.to_datetime().date()
                    except Cancelled:
                        raise
                    except Exception as casa_err:
                        logger.warning("CASA date extraction failed: %s", casa_err)

            # Method 3: Try filename parsing (e.g., 20231002_image.fits)
            if extracted_date is None and imagename:
                # The file's own name first: a folder's digits say less
                filename = f"{os.path.basename(imagename)} {imagename}"
                # Try various date patterns in filename
                patterns = [
                    r"(\d{4})(\d{2})(\d{2})",  # YYYYMMDD
                    r"(\d{4})-(\d{2})-(\d{2})",  # YYYY-MM-DD
                    r"(\d{4})\.(\d{2})\.(\d{2})",  # YYYY.MM.DD
                ]
                for pattern in patterns:
                    match = re.search(pattern, filename)
                    if match:
                        try:
                            y, m, d = (
                                int(match.group(1)),
                                int(match.group(2)),
                                int(match.group(3)),
                            )
                            if 1990 < y < 2100 and 1 <= m <= 12 and 1 <= d <= 31:
                                extracted_date = date(y, m, d)
                                break
                        except (ValueError, IndexError):
                            continue

            if extracted_date:
                self._set_date(extracted_date)
                self.summary_label.setText(
                    f"Date set to {extracted_date} from current image."
                )
            else:
                QMessageBox.information(
                    self,
                    "No date found",
                    "Could not extract date from the current image.\n\n"
                    "Supported formats:\n"
                    "• FITS files with DATE-OBS header\n"
                    "• CASA images with observation date\n"
                    "• Files with date in filename (YYYYMMDD)",
                )

        except Cancelled:
            self.summary_label.setText("Cancelled")
        except Exception as e:
            show_error(self, "No date found", "Could not read the date of the current image.", e)

    def _parse_date_string(self, date_str: str) -> Optional[date]:
        """Parse various date string formats."""
        if not date_str:
            return None

        date_str = str(date_str).strip()

        try:
            # ISO format with time (2023-10-02T12:30:00)
            if "T" in date_str:
                # Clean up the date string for parsing
                clean_str = date_str.replace("Z", "").split("+")[0].split(".")[0]
                # Handle potential timezone info
                if "-" in clean_str[11:]:  # Timezone like -05:00 after time
                    clean_str = clean_str[:19]
                try:
                    dt = datetime.fromisoformat(clean_str)
                    return dt.date()
                except ValueError:
                    # Fallback: just extract date part
                    date_part = clean_str.split("T")[0]
                    if len(date_part) >= 10:
                        return datetime.strptime(date_part[:10], "%Y-%m-%d").date()

            # YYYY-MM-DD
            if "-" in date_str and len(date_str) >= 10:
                return datetime.strptime(date_str[:10], "%Y-%m-%d").date()

            # YYYY/MM/DD
            if "/" in date_str and len(date_str) >= 10:
                return datetime.strptime(date_str[:10], "%Y/%m/%d").date()

            # YYYYMMDD (8 digits)
            if date_str.isdigit() and len(date_str) >= 8:
                return datetime.strptime(date_str[:8], "%Y%m%d").date()

            # MJD (Modified Julian Date)
            if date_str.replace(".", "").isdigit():
                mjd = float(date_str)
                if 40000 < mjd < 100000:  # Valid MJD range
                    from astropy.time import Time

                    t = Time(mjd, format="mjd")
                    return t.to_datetime().date()
        except (ValueError, TypeError, ImportError):
            pass

        return None

    def set_date_from_fits(self, fits_date: Optional[date]):
        """Set the date from a FITS file's DATE-OBS."""
        if fits_date:
            self._set_date(fits_date)


def _time_order(event):
    """Sort key: an event's begin time, without the mark it may carry."""
    return ne._clock(event.begin_time or "9999")


def show_noaa_events_viewer(parent=None, initial_date: Optional[date] = None):
    """
    Show the NOAA Events Viewer dialog.

    Args:
        parent: Parent widget
        initial_date: Optional initial date (e.g., from FITS header)

    Returns:
        The viewer window instance
    """
    viewer = NOAAEventsViewer(parent, initial_date)
    viewer.show()

    # If initial date provided, auto-fetch
    if initial_date:
        viewer.fetch_data()

    return viewer


def main():
    import argparse

    # Parse command line arguments
    parser = argparse.ArgumentParser(description="NOAA Solar Events Viewer")
    parser.add_argument(
        "--theme",
        choices=["light", "dark"],
        default=None,
        help="Set application theme (light or dark; default: the one SolarViewer uses)",
    )
    args = parser.parse_args()

    from ..ui.app import create_app

    app = create_app(args.theme)

    viewer = NOAAEventsViewer()
    viewer.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

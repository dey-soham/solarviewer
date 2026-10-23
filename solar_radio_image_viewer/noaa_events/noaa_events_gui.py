#!/usr/bin/env python3
"""
Solar Activity Viewer GUI - Comprehensive solar context data display.
"""

import sys
import os
import re
from datetime import datetime, date
from typing import Optional

# Qt imports
try:
    from PyQt5.QtWidgets import (
        QMainWindow,
        QWidget,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QDateEdit,
        QFrame,
        QScrollArea,
        QSizePolicy,
        QMessageBox,
        QProgressBar,
        QTabWidget,
    )
    from PyQt5.QtCore import QDate
    from PyQt5.QtNetwork import QNetworkAccessManager
except ImportError:
    print("PyQt5 is required. Install with: pip install PyQt5")
    sys.exit(1)

try:
    # Try relative imports (when run as module)
    from . import noaa_events as ne
    from ..ui.styles import theme_manager, set_hand_cursor
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
    from solar_radio_image_viewer.ui.styles import theme_manager, set_hand_cursor
from .cache import _noaa_cache
from .conditions_tab import ConditionsTabMixin
from .context_images_tab import ContextImagesTabMixin
from .radio_spectra_tab import RadioSpectraTabMixin
from .widgets import CollapsibleSection, EventTable
from .workers import FetchWorker
from ..processing.fits_io import is_fits, readable_image_path


from ..ui.geometry import fit_to_screen


class NOAAEventsViewer(
    ConditionsTabMixin, RadioSpectraTabMixin, ContextImagesTabMixin, QMainWindow
):
    """Main Solar Activity Viewer window - displays events, active regions, conditions, and CMEs."""

    def __init__(self, parent=None, initial_date: Optional[date] = None):
        super().__init__(parent)
        self.setWindowTitle("☀️ Solar Activity Viewer")
        fit_to_screen(self, 1000, 800)

        # Network Manager for image downloading
        self.nam = QNetworkAccessManager(self)
        self.image_downloads = {}  # Keep references to replies
        self.image_viewers = []  # Keep references to open image windows

        self.worker = None
        self.goes_worker = None
        self.events = []

        # Initial load state to manage cursor
        self._initial_load = False
        if initial_date:
            self._initial_load = True
            from PyQt5.QtWidgets import QApplication
            from PyQt5.QtCore import Qt

            QApplication.setOverrideCursor(Qt.WaitCursor)

        self.is_dark_theme = theme_manager.is_dark

        self.init_ui()

        # Set initial date
        if initial_date:
            self.date_edit.setDate(
                QDate(initial_date.year, initial_date.month, initial_date.day)
            )
        else:
            # Default to yesterday
            yesterday = QDate.currentDate().addDays(-1)
            self.date_edit.setDate(yesterday)
            
        set_hand_cursor(self)

    def closeEvent(self, event):
        """Clean up worker threads when window is closed."""
        # Stop fetch worker if running
        if hasattr(self, "worker") and self.worker is not None:
            if self.worker.isRunning():
                self.worker.quit()
                self.worker.wait(2000)
        # Stop GOES plot worker if running
        if hasattr(self, "goes_worker") and self.goes_worker is not None:
            if self.goes_worker.isRunning():
                self.goes_worker.quit()
                self.goes_worker.wait(2000)
        super().closeEvent(event)

    def init_ui(self):
        """Initialize the user interface."""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(16)
        layout.setContentsMargins(20, 16, 20, 16)

        # Modern button styles from theme_manager

        # Top bar: date selection
        top_bar = QHBoxLayout()
        top_bar.setSpacing(12)

        date_label = QLabel("Date:")
        date_label.setStyleSheet("font-weight: bold;")
        top_bar.addWidget(date_label)

        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy.MM.dd")
        self.date_edit.setMaximumDate(QDate.currentDate())
        # Styles handled by global stylesheet
        top_bar.addWidget(self.date_edit)

        # Get date from current tab button
        self.get_date_btn = QPushButton("📅 From Tab")
        self.get_date_btn.setToolTip("Get date from currently open image/FITS file")
        self.get_date_btn.clicked.connect(self.get_date_from_parent_tab)
        if not self.parent():
            self.get_date_btn.setEnabled(False)
            self.get_date_btn.setToolTip("Not available in independent mode")
        top_bar.addWidget(self.get_date_btn)

        top_bar.addStretch()

        self.progress = QProgressBar()
        self.progress.setMaximumWidth(150)
        self.progress.setMaximum(0)  # Indeterminate
        self.progress.hide()
        top_bar.addWidget(self.progress)

        self.fetch_btn = QPushButton("🔍 Fetch")
        self.fetch_btn.setObjectName("PrimaryButton")
        self.fetch_btn.clicked.connect(self.fetch_data)
        top_bar.addWidget(self.fetch_btn)

        self.plot_goes_btn = QPushButton("📈 Plot GOES X-ray Flux")
        self.plot_goes_btn.setToolTip("Plot the GOES X-ray light curve for this date")
        self.plot_goes_btn.clicked.connect(self.plot_goes_xray)
        top_bar.addWidget(self.plot_goes_btn)

        layout.addLayout(top_bar)

        # Modern summary bar - subtle styling
        self.summary_frame = QFrame()

        # Use subtle transparent background matching the theme
        palette = theme_manager.palette
        if theme_manager.is_dark:
            # Dark theme: subtle purple tint
            self.summary_frame.setStyleSheet(
                """
                QFrame {
                    background-color: rgba(99, 102, 241, 0.08);
                    border-radius: 8px;
                    border: 1px solid rgba(99, 102, 241, 0.2);
                }
            """
            )
        else:
            # Light theme: subtle tinted background
            self.summary_frame.setStyleSheet(
                f"""
                QFrame {{
                    background-color: {palette['surface']};
                    border-radius: 8px;
                    border: 1px solid {palette['border']};
                }}
            """
            )
        summary_layout = QHBoxLayout(self.summary_frame)
        summary_layout.setContentsMargins(16, 10, 16, 10)

        self.summary_label = QLabel(
            "Select a date and click 'Fetch' to view solar activity."
        )
        self.summary_label.setStyleSheet(f"color: {palette['text_secondary']};")
        self.summary_label.setWordWrap(True)
        summary_layout.addWidget(self.summary_label)

        layout.addWidget(self.summary_frame)

        # Modern tab styling handled by global stylesheet
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
        events_layout.setContentsMargins(16, 12, 16, 12)
        events_layout.setSpacing(10)

        # X-ray Flares section
        self.xray_section = CollapsibleSection("X-ray Flares")
        self.xray_table = EventTable(
            ["Time (UT)", "Class", "Peak Flux", "Region", "Duration", "Observatory"]
        )
        self.xray_section.add_widget(self.xray_table)
        events_layout.addWidget(self.xray_section)

        # Optical Flares section
        self.optical_section = CollapsibleSection("Optical Flares (H-alpha)")
        self.optical_table = EventTable(
            ["Time (UT)", "Class", "Location", "Region", "Notes", "Observatory"]
        )
        self.optical_section.add_widget(self.optical_table)
        events_layout.addWidget(self.optical_section)

        # Radio Events section
        self.radio_section = CollapsibleSection("Radio Events")
        self.radio_table = EventTable(
            ["Type", "Time (UT)", "Frequency", "Particulars", "Region", "Observatory"]
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
        events_credits_label = QLabel(
            'Data Sources: <a href="https://solarmonitor.org/">SolarMonitor.org</a> (NOAA/SWPC), '
            '<a href="https://soleil.i4ds.ch/solarradio/">e-CALLISTO</a> (C. Monstein)'
        )
        events_credits_label.setOpenExternalLinks(True)
        events_credits_label.setStyleSheet(
            f"color: {theme_manager.palette['text_secondary']}; font-size: 10pt; padding: 10px;"
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
        ar_layout.setContentsMargins(16, 12, 16, 12)
        ar_layout.setSpacing(10)

        # Active regions table
        self.ar_table = EventTable(
            [
                "AR#",
                "Location",
                "Area",
                "McIntosh",
                "Mag Type",
                "C%",
                "M%",
                "X%",
                "Flare Potential",
            ]
        )
        ar_layout.addWidget(self.ar_table)

        # AR info label
        self.ar_info_label = QLabel(
            "Fetch data to view active sunspot regions and flare probabilities."
        )
        self.ar_info_label.setWordWrap(True)
        self.ar_info_label.setStyleSheet(
            f"color: {theme_manager.palette['text']}; font-style: italic; padding: 10px; font-weight: light; opacity: 0.4;"
        )
        ar_layout.addWidget(self.ar_info_label)

        # Active Regions credits label
        ar_credits_label = QLabel(
            'Data Source: <a href="https://solarmonitor.org/">SolarMonitor.org</a> '
            "(NOAA/SWPC Solar Region Summary & ARM Flare Forecasts)"
        )
        ar_credits_label.setOpenExternalLinks(True)
        ar_credits_label.setStyleSheet(
            f"color: {theme_manager.palette['text_secondary']}; font-size: 10pt; padding: 10px;"
        )
        ar_layout.addWidget(ar_credits_label)

        # ar_layout.addStretch()

        # Make AR tab scrollable
        ar_scroll = QScrollArea()
        ar_scroll.setWidgetResizable(True)
        ar_scroll.setFrameShape(QFrame.NoFrame)
        ar_scroll.setWidget(ar_tab)
        self.tabs.addTab(ar_scroll, "Active Regions")

    def _create_cme_tab(self):
        """CMEs tab."""
        # Tab 4: CME Alerts
        cme_tab = QWidget()
        cme_layout = QVBoxLayout(cme_tab)
        cme_layout.setContentsMargins(16, 12, 16, 12)
        cme_layout.setSpacing(10)

        # CME table
        self.cme_table = EventTable(
            [
                "Time (UT)",
                "Speed (km/s)",
                "Source",
                "Width",
                "Earth Dir.",
            ]
        )
        cme_layout.addWidget(self.cme_table)

        # CME info label - theme-aware
        self.cme_info_label = QLabel(
            "CME data from NASA DONKI (±3 days from selected date)"
        )
        self.cme_info_label.setWordWrap(True)
        self.cme_info_label.setStyleSheet(
            f"color: {theme_manager.palette['text']}; font-style: italic; padding: 10px; font-weight: light; opacity: 0.4;"
        )
        cme_layout.addWidget(self.cme_info_label)

        # CME credits label with link
        cme_credits_label = QLabel(
            'Data Source: <a href="https://kauai.ccmc.gsfc.nasa.gov/DONKI/">NASA DONKI</a> '
            "(Space Weather Database Of Notifications, Knowledge, Information)"
        )
        cme_credits_label.setOpenExternalLinks(True)
        cme_credits_label.setStyleSheet(
            f"color: {theme_manager.palette['text_secondary']}; font-size: 10pt; padding: 5px 10px;"
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
        # Ensure imports are available for whole scope
        from PyQt5.QtWidgets import QApplication
        from PyQt5.QtCore import Qt

        # Clear radio spectra UI immediately on new fetch
        self.clear_dh_spectra_ui()

        if self.worker and self.worker.isRunning():
            return

        # Show busy cursor immediately
        # If this is the initial load, cursor was already set in __init__
        if getattr(self, "_initial_load", False):
            self._initial_load = False
        else:
            QApplication.setOverrideCursor(Qt.WaitCursor)

        qdate = self.date_edit.date()
        selected_date = date(qdate.year(), qdate.month(), qdate.day())

        self.date_edit.setEnabled(False)
        self.fetch_btn.setEnabled(False)
        self.summary_label.setText(f"Fetching data for {selected_date}...")
        self.progress.show()

        QApplication.processEvents()  # Force UI update immediately

        # Clean up old worker if exists
        if self.worker is not None:
            self.worker.finished.disconnect()
            self.worker.error.disconnect()
            self.worker.deleteLater()

        self.worker = FetchWorker(selected_date)
        self.worker.finished.connect(self.on_fetch_finished)
        self.worker.error.connect(self.on_fetch_error)
        self.worker.start()

        # [AUTO-FETCH SPECTRA] If radio spectra are already cached, load them automatically
        if _noaa_cache.has_any_spectra(selected_date):
            self.fetch_dh_spectra()

    def on_fetch_finished(self, events, active_regions, conditions, cmes, images, ecallisto_bursts=None):
        """Handle fetched data."""
        ecallisto_bursts = ecallisto_bursts or []
        try:
            # Check for validity
            if not self.isVisible() and not self.parent():
                return  # Window closed

            # Restore cursor
            from PyQt5.QtWidgets import QApplication

            QApplication.restoreOverrideCursor()

            self.date_edit.setEnabled(True)
            self.fetch_btn.setEnabled(True)
            self.fetch_btn.setText("🔍 Fetch")
            self.progress.hide()
            self.events = events

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
        else:
            summary_parts.append("Flares: 0")

        # 4. CMEs
        if cmes:
            cme_count = len(cmes)
            earth_directed = sum(1 for cme in cmes if cme.is_earth_directed)
            cme_text = f"CMEs: {cme_count}"
            if earth_directed > 0:
                cme_text += f" (🌍 {earth_directed})"
            summary_parts.append(cme_text)
        elif cmes is not None:
            summary_parts.append("CMEs: 0")

        if not summary_parts:
            self.summary_label.setText("No data available for this date.")
        else:
            self.summary_label.setText(" | ".join(summary_parts))

    def on_fetch_error(self, error_msg):
        """Handle fetch error."""
        try:
            # Check validity
            if not self.isVisible() and not self.parent():
                return

            from PyQt5.QtWidgets import QApplication
            from PyQt5.QtWidgets import QMessageBox

            QApplication.restoreOverrideCursor()

            self.date_edit.setEnabled(True)
            self.fetch_btn.setEnabled(True)
            self.fetch_btn.setText("🔍 Fetch")
            self.progress.hide()

            self.summary_label.setText(f"Error fetching data: {error_msg}")
            QMessageBox.critical(self, "Fetch Error", "Failed to fetch data")
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

    def display_events(self, events, ecallisto_bursts=None):
        """Display events in categorized tables."""
        ecallisto_bursts = ecallisto_bursts or []
        self.clear_tables()

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
        #     summary_parts.append(f"📊 {xray_count} X-ray flare{'s' if xray_count > 1 else ''}{max_note}")
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
        for event in sorted(categories["xray"], key=lambda e: e.begin_time or "9999"):
            duration = (
                f"{event.duration_minutes} min" if event.duration_minutes else "—"
            )
            flare_class = event.flare_class or "—"
            peak_flux = (
                event.particulars.split()[1]
                if len(event.particulars.split()) > 1
                else "—"
            )

            color_col = {}
            if event.flare_class_letter in ["M", "X"]:
                color_col[1] = event.flare_class_color

            self.xray_table.add_event_row(
                [
                    event.time_range,
                    flare_class,
                    peak_flux,
                    event.active_region or "—",
                    duration,
                    event.observatory_name,
                ],
                color_col,
            )

        # Populate Optical table
        self.optical_section.set_count(optical_count)
        for event in sorted(
            categories["optical"], key=lambda e: e.begin_time or "9999"
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
                'sort_time': e.begin_time or "9999"
            })
            
        for b in ecallisto_bursts:
            all_radio_events.append({
                'type': 'RBR',
                'time': b.time_range,
                'freq': "—",
                'particulars': b.burst_type,
                'region': "—",
                'obs': b.stations,
                'sort_time': b.begin_time.replace(":", "") or "9999"
            })
            
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

        # Resize columns to fit contents and scroll to top
        self.xray_table.resizeColumnsToContents()
        self.optical_table.resizeColumnsToContents()
        self.radio_table.resizeColumnsToContents()
        self.xray_table.scrollToTop()
        self.optical_table.scrollToTop()
        self.radio_table.scrollToTop()

    def display_active_regions(self, regions):
        """Display active regions in the AR table."""
        self.ar_table.setRowCount(0)

        if regions is None or len(regions) == 0:
            self.ar_info_label.setText(
                "No active regions data available for this date."
            )
            self.ar_info_label.show()
            return

        # self.ar_info_label.hide()
        self.ar_info_label.setText(f"Found {len(regions)} active regions.")

        # Color coding for risk levels
        risk_colors = {
            "Very High": "#F44336",  # Red
            "High": "#FF9800",  # Orange
            "Moderate": "#FFC107",  # Amber
            "Low": "#4CAF50",  # Green
            "Quiet": "#9E9E9E",  # Grey
        }

        for region in sorted(regions, key=lambda r: r.area, reverse=True):
            # Format probabilities
            c_prob = f"{region.prob_c}%" if region.prob_c is not None else "—"
            m_prob = f"{region.prob_m}%" if region.prob_m is not None else "—"
            x_prob = f"{region.prob_x}%" if region.prob_x is not None else "—"

            risk = region.flare_risk_level
            risk_color = risk_colors.get(risk, "#9E9E9E")

            # Add row with color for risk level column
            color_col = {8: risk_color}

            # Also color M% and X% if they're significant
            if region.prob_m and region.prob_m >= 20:
                color_col[6] = "#FF9800"
            if region.prob_x and region.prob_x >= 5:
                color_col[7] = "#F44336"

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
                color_col,
            )

        self.ar_table.resizeColumnsToContents()
        self.ar_table.scrollToTop()

    def display_cme_events(self, cmes):
        """Display CME events in the CME table."""
        self.cme_table.setRowCount(0)

        if cmes is None or len(cmes) == 0:
            self.cme_info_label.setText(
                "No CME activity detected in the ±3 day range for this date."
            )
            self.cme_info_label.show()
            return

        self.cme_info_label.setText(
            f"Found {len(cmes)} CME events (±3 days from selected date)"
        )

        for cme in cmes:
            # Format columns
            time_str = cme.start_time.strftime("%Y-%m-%d %H:%M")
            speed_str = f"{cme.speed:.0f}"
            width_str = f"{cme.half_angle:.0f}°" if cme.half_angle else "—"
            earth_str = "🌍 Yes" if cme.is_earth_directed else "No"

            # Color coding
            color_col = {}

            # Color Earth-directed column
            if cme.is_earth_directed:
                color_col[4] = "#FF9800"  # Orange for Earth-directed
                if cme.speed >= 1000:
                    color_col[4] = "#F44336"  # Red for fast Earth-directed

            # Color speed column based on category
            speed_colors = {
                "Slow": "#4CAF50",
                "Moderate": "#FFC107",
                "Fast": "#FF9800",
                "Extreme": "#F44336",
            }
            color_col[1] = speed_colors.get(cme.speed_category, "#9E9E9E")

            self.cme_table.add_event_row(
                [
                    time_str,
                    speed_str,
                    cme.source_location,
                    width_str,
                    earth_str,
                ],
                color_col,
            )

        self.cme_table.resizeColumnsToContents()
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
                    self, "Info", "No parent viewer found. Please open an image first."
                )
                return

            # Try to get current tab
            current_tab = None
            if hasattr(parent, "tab_widget"):
                current_tab = parent.tab_widget.currentWidget()

            if current_tab is None:
                QMessageBox.information(self, "Info", "No image is currently open.")
                return

            extracted_date = None
            image_time = None
            imagename = getattr(current_tab, "imagename", None)

            # Method 1: Try FITS header from tab attribute first
            header = None
            if hasattr(current_tab, "header") and current_tab.header:
                header = current_tab.header

            # Method 1b: If no header attribute, read FITS/FTS file directly
            if header is None and imagename:
                if is_fits(imagename):
                    try:
                        from astropy.io import fits

                        header = fits.getheader(readable_image_path(imagename))
                    except Exception as fits_err:
                        print(f"FITS header read failed: {fits_err}")

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
                        from casatools import image as IA

                        ia_tool = IA()
                        ia_tool.open(readable_image_path(imagename))
                        csys_record = ia_tool.coordsys().torecord()
                        ia_tool.close()

                        if "obsdate" in csys_record:
                            obsdate = csys_record["obsdate"]
                            m0 = obsdate.get("m0", {})
                            time_value = m0.get("value", None)
                            time_unit = m0.get("unit", None)
                            refer = obsdate.get("refer", None)

                            if (refer == "UTC" or time_unit == "d") and time_value:
                                from astropy.time import Time

                                t = Time(time_value, format="mjd")
                                extracted_date = t.to_datetime().date()
                    except Exception as casa_err:
                        print(f"CASA date extraction failed: {casa_err}")

            # Method 3: Try filename parsing (e.g., 20231002_image.fits)
            if extracted_date is None and imagename:
                filename = imagename
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
                self.date_edit.setDate(
                    QDate(extracted_date.year, extracted_date.month, extracted_date.day)
                )
                self.summary_label.setText(
                    f"Date set to {extracted_date} from current image."
                )
            else:
                QMessageBox.information(
                    self,
                    "Info",
                    "Could not extract date from the current image.\n\n"
                    "Supported formats:\n"
                    "• FITS files with DATE-OBS header\n"
                    "• CASA images with observation date\n"
                    "• Files with date in filename (YYYYMMDD)",
                )

        except Exception as e:
            QMessageBox.warning(self, "Error", f"Error extracting date: {str(e)}")

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
            self.date_edit.setDate(
                QDate(fits_date.year, fits_date.month, fits_date.day)
            )


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

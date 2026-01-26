#!/usr/bin/env python3
"""
Radio Data Downloader GUI - PyQt5-based interface for downloading radio solar data.

This module provides a graphical interface for downloading radio solar data
from various observatories (starting with Learmonth) and converting to FITS format.
"""

import sys
import os
from datetime import datetime, timedelta
from typing import Optional

try:
    from PyQt5.QtWidgets import (
        QApplication,
        QMainWindow,
        QWidget,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QComboBox,
        QPushButton,
        QLineEdit,
        QDateTimeEdit,
        QFileDialog,
        QProgressBar,
        QMessageBox,
        QGroupBox,
        QCheckBox,
        QTextEdit,
    )
    from PyQt5.QtCore import Qt, QDateTime, pyqtSignal, QThread
except ImportError:
    print("Error: PyQt5 is required. Please install it with:")
    print("  pip install PyQt5")
    sys.exit(1)

# Import the radio data downloader module
try:
    from . import radio_data_downloader as rdd
    from ..styles import set_hand_cursor
except ImportError:
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        if script_dir not in sys.path:
            sys.path.append(script_dir)
        import radio_data_downloader as rdd
    except ImportError:
        print("Error: Could not import radio_data_downloader module.")
        sys.exit(1)


class DownloadWorker(QThread):
    """Worker thread for handling downloads without blocking the UI."""

    progress = pyqtSignal(str)
    finished = pyqtSignal(str)  # Emits the path to the created FITS file
    error = pyqtSignal(str)

    def __init__(self, params: dict):
        super().__init__()
        self.params = params
        self._cancelled = False

    def cancel(self):
        """Cancel the download."""
        self._cancelled = True

    def run(self):
        """Execute the download and conversion."""
        try:
            # Extract site name from the instrument combo text
            instrument = self.params.get("instrument", "Learmonth")
            
            # Check if this is e-CALLISTO
            if instrument == "e-CALLISTO":
                self._run_ecallisto()
                return
            
            # Map GUI display names to internal site names for RSTN
            site_map = {
                "Learmonth (Australia)": "Learmonth",
                "San Vito (Italy)": "San Vito",
                "Palehua (Hawaii, USA)": "Palehua",
                "Holloman (New Mexico, USA)": "Holloman",
                # Handle direct names too for backwards compatibility
                "Learmonth": "Learmonth",
                "San Vito": "San Vito",
                "Palehua": "Palehua",
                "Holloman": "Holloman",
            }
            site = site_map.get(instrument, "Learmonth")

            date = self.params.get("date")
            start_time = self.params.get("start_time")
            end_time = self.params.get("end_time")
            output_dir = self.params.get("output_dir")
            bkg_sub = self.params.get("background_subtract", False)
            do_flag = self.params.get("flag_bad_channels", True)
            flag_cal = self.params.get("flag_cal_time", True)

            # Use generic RSTN download function for all sites
            result = rdd.download_and_convert_rstn(
                site=site,
                date=date,
                output_dir=output_dir,
                start_time=start_time,
                end_time=end_time,
                bkg_sub=bkg_sub,
                do_flag=do_flag,
                flag_cal_time=flag_cal,
                progress_callback=self.progress.emit,
            )

            if result:
                self.finished.emit(result)
            else:
                self.error.emit(
                    f"Download or conversion failed for {site}. Check if data is available for this date."
                )

        except Exception as e:
            self.error.emit(str(e))
    
    def _run_ecallisto(self):
        """Handle e-CALLISTO download and conversion."""
        try:
            observatory = self.params.get("ecallisto_observatory")
            start_datetime = self.params.get("start_datetime")
            end_datetime = self.params.get("end_datetime")
            output_dir = self.params.get("output_dir")
            
            # Download and convert e-CALLISTO data
            results = rdd.download_and_convert_ecallisto(
                start_time=start_datetime,
                end_time=end_datetime,
                observatory=observatory if observatory and observatory != "All Observatories" else None,
                output_dir=output_dir,
                progress_callback=self.progress.emit,
            )
            
            if results:
                # Return the first file or a summary
                if len(results) == 1:
                    self.finished.emit(results[0])
                else:
                    self.finished.emit(f"{len(results)} files created in {output_dir}")
            else:
                self.error.emit(
                    f"Download or conversion failed for e-CALLISTO. Check if data is available for this date/time."
                )
        except Exception as e:
            self.error.emit(str(e))


class ObservatoryRefreshWorker(QThread):
    """Worker thread for fetching available observatories without blocking the UI."""

    progress = pyqtSignal(str)
    finished = pyqtSignal(list)  # Emits list of observatory names
    error = pyqtSignal(str)

    def __init__(self, date: str):
        super().__init__()
        self.date = date

    def run(self):
        """Fetch observatories from the server."""
        try:
            observatories = rdd.fetch_ecallisto_observatories(
                self.date,
                progress_callback=self.progress.emit,
            )
            self.finished.emit(observatories)
        except Exception as e:
            self.error.emit(str(e))

class RadioDataDownloaderGUI(QMainWindow):
    """Main window for the Radio Data Downloader GUI."""

    def __init__(self, parent=None, initial_datetime=None):
        super().__init__(parent)
        self.setWindowTitle("Radio Solar Data Downloader")
        self.setMinimumWidth(600)
        self.setMinimumHeight(800)

        # Store initial datetime for time selection
        self.initial_datetime = initial_datetime

        # Main widget and layout
        self.main_widget = QWidget()
        self.setCentralWidget(self.main_widget)
        self.layout = QVBoxLayout(self.main_widget)

        # Create UI sections
        self.create_instrument_selection()
        self.create_time_selection()
        self.create_output_selection()
        self.create_processing_options()
        self.create_log_section()
        self.create_download_section()
        set_hand_cursor(self)

        # Initialize worker
        self.download_worker = None

    def closeEvent(self, event):
        """Clean up download worker thread when window is closed."""
        if hasattr(self, "download_worker") and self.download_worker is not None:
            if self.download_worker.isRunning():
                self.download_worker.cancel()
                self.download_worker.quit()
                self.download_worker.wait(2000)
        super().closeEvent(event)

    def create_instrument_selection(self):
        """Create the instrument selection section."""
        group = QGroupBox("Select Instrument")
        layout = QVBoxLayout()

        self.instrument_combo = QComboBox()
        self.instrument_combo.addItems(
            [
                "Learmonth (Australia)",
                "San Vito (Italy)",
                "Palehua (Hawaii, USA)",
                "Holloman (New Mexico, USA)",
                "e-CALLISTO",
            ]
        )
        self.instrument_combo.currentTextChanged.connect(self.on_instrument_changed)

        # Info label - dynamically updated based on selection
        self.info_label = QLabel(
            "Learmonth Solar Observatory, Western Australia.\nFrequency: 25-180 MHz | Data from BOM Australia or NOAA NCEI."
        )
        self.info_label.setStyleSheet("color: gray; font-style: italic;")
        self.info_label.setWordWrap(True)

        layout.addWidget(self.instrument_combo)
        layout.addWidget(self.info_label)
        
        # e-CALLISTO observatory selector (initially hidden)
        self.ecallisto_widget = QWidget()
        ecallisto_layout = QHBoxLayout(self.ecallisto_widget)
        ecallisto_layout.setContentsMargins(0, 10, 0, 0)
        ecallisto_layout.addWidget(QLabel("Observatory:"))
        self.ecallisto_combo = QComboBox()
        # Add "All Observatories" option plus common observatories
        self.ecallisto_combo.addItems(
            ["All Observatories"] + rdd.ECALLISTO_OBSERVATORIES
        )
        ecallisto_layout.addWidget(self.ecallisto_combo)
        
        # Refresh button to fetch observatories for selected date
        self.refresh_obs_button = QPushButton("🔄")
        self.refresh_obs_button.setToolTip("Refresh observatory list for selected date")
        self.refresh_obs_button.setMaximumWidth(40)
        self.refresh_obs_button.clicked.connect(self.refresh_observatories)
        ecallisto_layout.addWidget(self.refresh_obs_button)
        
        self.ecallisto_widget.hide()  # Hidden by default
        layout.addWidget(self.ecallisto_widget)
        
        group.setLayout(layout)
        self.layout.addWidget(group)

    def on_instrument_changed(self, text):
        """Update info label and show/hide e-CALLISTO options when instrument selection changes."""
        site_info = {
            "Learmonth (Australia)": "Learmonth Solar Observatory, Western Australia.\nFrequency: 25-180 MHz | Data from BOM Australia or NOAA NCEI.",
            "San Vito (Italy)": "San Vito dei Normanni, Southern Italy.\nFrequency: 25-180 MHz | Data from NOAA NCEI archive.",
            "Palehua (Hawaii, USA)": "Palehua, Hawaii.\nFrequency: 25-180 MHz | Data from NOAA NCEI archive.",
            "Holloman (New Mexico, USA)": "Holloman AFB, New Mexico.\nFrequency: 25-180 MHz | ⚠️ Limited data: Apr 2000 - Jul 2004 only.",
            "e-CALLISTO": "e-CALLISTO Network: Global network of solar radio spectrometers.\nFrequency: 45-870 MHz typical | Select observatory or search all.",
        }
        self.info_label.setText(
            site_info.get(text, "RSTN Solar Spectrograph: 25-180 MHz")
        )
        
        # Show/hide e-CALLISTO observatory selector and processing options
        if text == "e-CALLISTO":
            self.ecallisto_widget.show()
            # Hide RSTN-specific processing options (they don't apply to e-CALLISTO)
            self.processing_group.hide()
        else:
            self.ecallisto_widget.hide()
            # Show RSTN processing options
            self.processing_group.show()

    def create_time_selection(self):
        """Create the time range selection section."""
        group = QGroupBox("Time Range")
        layout = QVBoxLayout()

        # Full day observation toggle
        self.full_day_checkbox = QCheckBox("Full Day Observation (no time filtering)")
        self.full_day_checkbox.setChecked(True)  # Default to full day
        self.full_day_checkbox.setToolTip(
            "When checked, downloads the entire day's observation without time filtering"
        )
        self.full_day_checkbox.toggled.connect(self.on_full_day_toggled)
        layout.addWidget(self.full_day_checkbox)

        # Date-only selector (visible when full day is checked)
        self.date_layout_widget = QWidget()
        date_layout = QHBoxLayout(self.date_layout_widget)
        date_layout.setContentsMargins(0, 0, 0, 0)
        date_layout.addWidget(QLabel("Date:"))
        self.date_only_edit = QDateTimeEdit()
        self.date_only_edit.setCalendarPopup(True)
        self.date_only_edit.setDisplayFormat("yyyy.MM.dd")

        # Use initial_datetime if provided, otherwise yesterday
        if self.initial_datetime:
            initial_qdt = QDateTime(self.initial_datetime)
            self.date_only_edit.setDateTime(initial_qdt)
        else:
            yesterday = QDateTime.currentDateTime().addDays(-1)
            self.date_only_edit.setDateTime(yesterday)
        date_layout.addWidget(self.date_only_edit)
        layout.addWidget(self.date_layout_widget)

        # Start time (hidden when full day is checked)
        self.start_layout_widget = QWidget()
        start_layout = QHBoxLayout(self.start_layout_widget)
        start_layout.setContentsMargins(0, 0, 0, 0)
        start_layout.addWidget(QLabel("Start:"))
        self.start_datetime = QDateTimeEdit()
        self.start_datetime.setCalendarPopup(True)
        self.start_datetime.setDisplayFormat("yyyy.MM.dd HH:mm:ss")

        # Use initial_datetime if provided, otherwise yesterday 00:00:00
        if self.initial_datetime:
            initial_qdt = QDateTime(self.initial_datetime)
            self.start_datetime.setDateTime(initial_qdt)
        else:
            yesterday = QDateTime.currentDateTime().addDays(-1)
            yesterday.setTime(yesterday.time().fromString("00:00:00", "HH:mm:ss"))
            self.start_datetime.setDateTime(yesterday)
        self.start_datetime.dateChanged.connect(self.on_start_date_changed)
        self.start_datetime.dateTimeChanged.connect(self.on_start_time_changed)
        start_layout.addWidget(self.start_datetime)
        layout.addWidget(self.start_layout_widget)

        # End time (hidden when full day is checked)
        self.end_layout_widget = QWidget()
        end_layout = QHBoxLayout(self.end_layout_widget)
        end_layout.setContentsMargins(0, 0, 0, 0)
        end_layout.addWidget(QLabel("End:"))
        self.end_datetime = QDateTimeEdit()
        self.end_datetime.setCalendarPopup(True)
        self.end_datetime.setDisplayFormat("yyyy.MM.dd HH:mm:ss")

        # Use initial_datetime + 1 hour if provided, otherwise yesterday 23:59:59
        if self.initial_datetime:
            from datetime import timedelta

            end_dt = self.initial_datetime + timedelta(hours=1)
            self.end_datetime.setDateTime(QDateTime(end_dt))
        else:
            end_time = QDateTime.currentDateTime().addDays(-1)
            end_time.setTime(end_time.time().fromString("23:59:59", "HH:mm:ss"))
            self.end_datetime.setDateTime(end_time)
        end_layout.addWidget(self.end_datetime)
        layout.addWidget(self.end_layout_widget)

        # Initially hide start/end time pickers (full day is default)
        self.start_layout_widget.hide()
        self.end_layout_widget.hide()

        # Note about data availability
        """note_label = QLabel(
            "Note: Data may not be available for all dates. "
            "Learmonth data is typically available within 1-2 days."
        )
        note_label.setStyleSheet("color: #888;")
        note_label.setWordWrap(True)
        layout.addWidget(note_label)"""

        group.setLayout(layout)
        self.layout.addWidget(group)

    def on_full_day_toggled(self, checked):
        """Toggle time picker visibility based on full day checkbox."""
        if checked:
            # Full day mode - show only date picker
            self.date_layout_widget.show()
            self.start_layout_widget.hide()
            self.end_layout_widget.hide()
        else:
            # Time range mode - show start/end time pickers
            self.date_layout_widget.hide()
            self.start_layout_widget.show()
            self.end_layout_widget.show()

    def refresh_observatories(self):
        """Fetch available observatories for the selected date."""
        # Get the date from the appropriate widget
        if self.full_day_checkbox.isChecked():
            date = self.date_only_edit.dateTime().toString("yyyy-MM-dd")
        else:
            date = self.start_datetime.dateTime().toString("yyyy-MM-dd")
        
        # Disable refresh button while fetching
        self.refresh_obs_button.setEnabled(False)
        self.refresh_obs_button.setText("...")
        self.log_message(f"Fetching observatories for {date}...")
        
        # Start worker thread
        self.obs_refresh_worker = ObservatoryRefreshWorker(date)
        self.obs_refresh_worker.progress.connect(self.log_message)
        self.obs_refresh_worker.finished.connect(self.on_observatory_refresh_finished)
        self.obs_refresh_worker.error.connect(self.on_observatory_refresh_error)
        self.obs_refresh_worker.start()

    def on_observatory_refresh_finished(self, observatories: list):
        """Handle successful observatory fetch."""
        self.refresh_obs_button.setEnabled(True)
        self.refresh_obs_button.setText("🔄")
        
        # Update the combobox
        current_selection = self.ecallisto_combo.currentText()
        self.ecallisto_combo.clear()
        self.ecallisto_combo.addItem("All Observatories")
        
        if observatories:
            self.ecallisto_combo.addItems(observatories)
            self.log_message(f"Found {len(observatories)} observatories")
            
            # Try to restore previous selection
            index = self.ecallisto_combo.findText(current_selection)
            if index >= 0:
                self.ecallisto_combo.setCurrentIndex(index)
        else:
            # Fall back to default list
            self.ecallisto_combo.addItems(rdd.ECALLISTO_OBSERVATORIES)
            self.log_message("No observatories found, using default list")

    def on_observatory_refresh_error(self, error_message: str):
        """Handle observatory fetch error."""
        self.refresh_obs_button.setEnabled(True)
        self.refresh_obs_button.setText("🔄")
        self.log_message(f"Error refreshing observatories: {error_message}")

    def on_start_date_changed(self, new_date):
        """Sync end date when start date is changed."""
        from PyQt5.QtCore import QTime

        # Keep the current end time but change the date
        current_end_time = self.end_datetime.time()
        end_dt = QDateTime(new_date, current_end_time)
        self.end_datetime.setDateTime(end_dt)

    def on_start_time_changed(self, new_datetime):
        """Sync end time when start time is changed (keep 1 hour difference)."""
        # Set end time to start time + 1 hour
        end_dt = new_datetime.addSecs(3600)
        self.end_datetime.setDateTime(end_dt)

    def create_output_selection(self):
        """Create the output directory selection section."""
        group = QGroupBox("Output Settings")
        layout = QVBoxLayout()

        dir_layout = QHBoxLayout()
        dir_layout.addWidget(QLabel("Output Directory:"))

        self.output_dir = QLineEdit()
        self.output_dir.setText(os.path.join(os.getcwd(), "radio_solar_data"))
        dir_layout.addWidget(self.output_dir)

        browse_button = QPushButton("Browse...")
        browse_button.clicked.connect(self.browse_output_dir)
        dir_layout.addWidget(browse_button)

        layout.addLayout(dir_layout)
        group.setLayout(layout)
        self.layout.addWidget(group)

    def create_processing_options(self):
        """Create the processing options section."""
        self.processing_group = QGroupBox("Processing Options (RSTN only)")
        layout = QVBoxLayout()

        self.flag_checkbox = QCheckBox("Flag known bad frequency channels")
        self.flag_checkbox.setChecked(True)
        self.flag_checkbox.setToolTip(
            "Remove data from frequency channels known to have interference or issues"
        )
        layout.addWidget(self.flag_checkbox)

        self.flag_cal_checkbox = QCheckBox("Flag calibration time periods")
        self.flag_cal_checkbox.setChecked(True)
        self.flag_cal_checkbox.setToolTip(
            "Detect and remove calibration periods that show as spikes in the data"
        )
        layout.addWidget(self.flag_cal_checkbox)

        self.bkg_sub_checkbox = QCheckBox("Background subtraction")
        self.bkg_sub_checkbox.setChecked(False)
        self.bkg_sub_checkbox.setToolTip(
            "Normalize each frequency channel by its median value"
        )
        layout.addWidget(self.bkg_sub_checkbox)

        self.processing_group.setLayout(layout)
        self.layout.addWidget(self.processing_group)

    def create_download_section(self):
        """Create the download button and progress section."""
        layout = QHBoxLayout()

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel_download)
        self.cancel_button.setEnabled(False)
        self.cancel_button.setMinimumHeight(40)
        layout.addWidget(self.cancel_button)

        self.download_button = QPushButton("Download && Convert to FITS")
        self.download_button.clicked.connect(self.start_download)
        self.download_button.setMinimumHeight(40)
        self.download_button.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.download_button)

        self.layout.addLayout(layout)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 0)  # Indeterminate
        self.progress_bar.hide()
        self.layout.addWidget(self.progress_bar)

    def create_log_section(self):
        """Create the log/status section."""
        group = QGroupBox("Status")
        layout = QVBoxLayout()

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        #self.log_text.setMaximumHeight(350)
        self.log_text.setPlaceholderText("Download status will appear here...")

        layout.addWidget(self.log_text)
        group.setLayout(layout)
        self.layout.addWidget(group)

    def browse_output_dir(self):
        """Open directory selection dialog."""
        directory = QFileDialog.getExistingDirectory(
            self, "Select Output Directory", self.output_dir.text()
        )
        if directory:
            self.output_dir.setText(directory)

    def log_message(self, message: str):
        """Add a message to the log."""
        self.log_text.append(message)
        # Scroll to bottom
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def start_download(self):
        """Start the download process."""
        # Validate inputs
        output_dir = self.output_dir.text()
        if not output_dir:
            QMessageBox.warning(self, "Error", "Please specify an output directory.")
            return

        instrument = self.instrument_combo.currentText()
        
        # Handle e-CALLISTO differently
        if instrument == "e-CALLISTO":
            observatory = self.ecallisto_combo.currentText()
            
            # Check if full day mode
            if self.full_day_checkbox.isChecked():
                # Full day mode - use 00:00:00 to 23:59:59
                date = self.date_only_edit.dateTime().toString("yyyy-MM-dd")
                start_datetime_str = f"{date} 00:00:00"
                end_datetime_str = f"{date} 23:59:59"
                log_msg = f"Starting e-CALLISTO download (full day)..."
            else:
                # Time range mode
                start_dt = self.start_datetime.dateTime()
                end_dt = self.end_datetime.dateTime()
                
                # Validate time range
                if start_dt >= end_dt:
                    QMessageBox.warning(
                        self, "Error", "Start time must be before end time."
                    )
                    return
                
                # Format as 'YYYY-MM-DD HH:MM:SS' for e-CALLISTO
                start_datetime_str = start_dt.toString("yyyy-MM-dd HH:mm:ss")
                end_datetime_str = end_dt.toString("yyyy-MM-dd HH:mm:ss")
                log_msg = f"Starting e-CALLISTO download..."
            
            log_msg += f"\nObservatory: {observatory}"
            log_msg += f"\nTime range: {start_datetime_str} to {end_datetime_str}"
            
            params = {
                "instrument": instrument,
                "ecallisto_observatory": observatory,
                "start_datetime": start_datetime_str,
                "end_datetime": end_datetime_str,
                "output_dir": output_dir,
            }
        else:
            # RSTN instruments
            # Check if full day mode or time range mode
            if self.full_day_checkbox.isChecked():
                # Full day mode - no time filtering
                date = self.date_only_edit.dateTime().toString("yyyy-MM-dd")
                start_time = None
                end_time = None
                log_msg = f"Starting download for {date} (full day observation)..."
            else:
                # Time range mode
                start_dt = self.start_datetime.dateTime()
                end_dt = self.end_datetime.dateTime()

                # Validate time range
                if start_dt >= end_dt:
                    QMessageBox.warning(
                        self, "Error", "Start time must be before end time."
                    )
                    return

                date = start_dt.toString("yyyy-MM-dd")
                start_time = start_dt.toString("HH:mm:ss")
                end_time = end_dt.toString("HH:mm:ss")
                log_msg = f"Starting download for {date}...\nTime range: {start_time} to {end_time}"

            params = {
                "instrument": instrument,
                "date": date,
                "start_time": start_time,
                "end_time": end_time,
                "output_dir": output_dir,
                "background_subtract": self.bkg_sub_checkbox.isChecked(),
                "flag_bad_channels": self.flag_checkbox.isChecked(),
                "flag_cal_time": self.flag_cal_checkbox.isChecked(),
            }

        # Update UI
        self.download_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress_bar.show()
        self.log_text.clear()
        self.log_message(log_msg)

        # Start worker thread
        self.download_worker = DownloadWorker(params)
        self.download_worker.progress.connect(self.on_progress)
        self.download_worker.finished.connect(self.on_finished)
        self.download_worker.error.connect(self.on_error)
        self.download_worker.start()

    def cancel_download(self):
        """Cancel the current download."""
        if self.download_worker:
            self.download_worker.cancel()
            self.log_message("Cancelling download...")

    def on_progress(self, message: str):
        """Handle progress updates."""
        self.log_message(message)

    def on_finished(self, fits_file: str):
        """Handle download completion."""
        self.download_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress_bar.hide()

        self.log_message(f"\n✓ Success! FITS file created:")
        self.log_message(f"  {fits_file}")
        self.log_message(
            "\nYou can now open this file with the Dynamic Spectrum Viewer:"
        )
        self.log_message("  Tools → LOFAR Tools → Dynamic Spectrum Viewer")

        QMessageBox.information(
            self,
            "Download Complete",
            f"FITS file created successfully!\n\n{fits_file}\n\n"
            "You can open this file with the Dynamic Spectrum Viewer.",
        )

    def on_error(self, error_message: str):
        """Handle download errors."""
        self.download_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress_bar.hide()

        self.log_message(f"\n✗ Error: {error_message}")

        QMessageBox.critical(
            self,
            "Download Failed",
            f"Failed to download or convert data:\n\n{error_message}",
        )


def launch_gui(parent=None, initial_datetime=None) -> RadioDataDownloaderGUI:
    """
    Launch the Radio Data Downloader GUI.

    Args:
        parent: Optional parent widget for integration with other PyQt applications
        initial_datetime: Optional datetime to initialize the time selectors

    Returns:
        RadioDataDownloaderGUI: The main window instance
    """
    if not QApplication.instance():
        app = QApplication(sys.argv)
    else:
        app = QApplication.instance()

    window = RadioDataDownloaderGUI(parent, initial_datetime=initial_datetime)
    window.show()

    if parent is None:
        sys.exit(app.exec_())

    return window


if __name__ == "__main__":
    launch_gui()

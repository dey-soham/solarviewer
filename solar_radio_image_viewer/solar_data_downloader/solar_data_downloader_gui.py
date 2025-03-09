#!/usr/bin/env python3
"""
Solar Data Viewer GUI - PyQt5-based interface for downloading solar observatory data.

This module provides a graphical interface for downloading data from various solar
observatories using the solar_data_downloader module. It can be used as a standalone
application or integrated into other PyQt applications.
"""

import sys
import os
import datetime
from pathlib import Path
from typing import Optional, Dict, List

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
        QRadioButton,
        QButtonGroup,
    )
    from PyQt5.QtCore import Qt, QDateTime, pyqtSignal, QThread
except ImportError:
    print("Error: PyQt5 is required. Please install it with:")
    print("  pip install PyQt5")
    sys.exit(1)

# Try to import the solar_data_downloader module
try:
    # First try relative import (when used as part of package)
    from . import solar_data_downloader as sdd
except ImportError:
    try:
        # Then try importing from the same directory (when run as script)
        script_dir = os.path.dirname(os.path.abspath(__file__))
        if script_dir not in sys.path:
            sys.path.append(script_dir)
        import solar_data_downloader as sdd
    except ImportError:
        print("Error: Could not import solar_data_downloader module.")
        print(
            "Make sure solar_data_downloader.py is in the same directory as this script."
        )
        sys.exit(1)

# Check if required packages are installed
try:
    import sunpy
    import drms
    import astropy
except ImportError as e:
    print(f"Error: Missing required package: {e.name}")
    print("Please install the required packages with:")
    print("  pip install sunpy drms astropy")
    print("For AIA Level 1.5 calibration, also install:")
    print("  pip install aiapy")
    sys.exit(1)


class DownloadWorker(QThread):
    """Worker thread for handling downloads without blocking the GUI."""

    progress = pyqtSignal(str)  # Signal to update progress text
    finished = pyqtSignal(list)  # Signal emitted with list of downloaded files
    error = pyqtSignal(str)  # Signal emitted when an error occurs

    def __init__(self, download_params: dict):
        super().__init__()
        self.params = download_params

    def run(self):
        """Execute the download operation in a separate thread."""
        try:
            instrument = self.params.get("instrument")

            if instrument == "AIA":
                if self.params.get("use_fido", False):
                    files = sdd.download_aia_with_fido(
                        wavelength=self.params["wavelength"],
                        start_time=self.params["start_time"],
                        end_time=self.params["end_time"],
                        output_dir=self.params["output_dir"],
                    )
                else:
                    files = sdd.download_aia(
                        wavelength=self.params["wavelength"],
                        cadence=self.params["cadence"],
                        start_time=self.params["start_time"],
                        end_time=self.params["end_time"],
                        output_dir=self.params["output_dir"],
                        email=self.params.get("email"),
                    )

            elif instrument == "HMI":
                if self.params.get("use_fido", False):
                    files = sdd.download_hmi_with_fido(
                        series=self.params["series"],
                        start_time=self.params["start_time"],
                        end_time=self.params["end_time"],
                        output_dir=self.params["output_dir"],
                    )
                else:
                    files = sdd.download_hmi(
                        series=self.params["series"],
                        start_time=self.params["start_time"],
                        end_time=self.params["end_time"],
                        output_dir=self.params["output_dir"],
                        email=self.params.get("email"),
                    )

            elif instrument == "IRIS":
                files = sdd.download_iris(
                    start_time=self.params["start_time"],
                    end_time=self.params["end_time"],
                    output_dir=self.params["output_dir"],
                    obs_type=self.params["obs_type"],
                    wavelength=self.params.get("wavelength"),
                )

            elif instrument == "SOHO":
                files = sdd.download_soho(
                    instrument=self.params["soho_instrument"],
                    start_time=self.params["start_time"],
                    end_time=self.params["end_time"],
                    output_dir=self.params["output_dir"],
                    wavelength=self.params.get("wavelength"),
                    detector=self.params.get("detector"),
                )

            self.finished.emit(files)

        except Exception as e:
            self.error.emit(str(e))


class SolarDataViewerGUI(QMainWindow):
    """Main window for the Solar Data Viewer GUI application."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Solar Data Viewer")
        self.setMinimumWidth(800)

        # Initialize the main widget and layout
        self.main_widget = QWidget()
        self.setCentralWidget(self.main_widget)
        self.layout = QVBoxLayout(self.main_widget)

        # Create the UI components
        self.create_instrument_selection()
        self.create_parameter_widgets()
        self.create_time_selection()
        self.create_output_selection()
        self.create_download_section()

        # Initialize the download worker
        self.download_worker = None

    def create_instrument_selection(self):
        """Create the instrument selection section."""
        group = QGroupBox("Select Instrument")
        layout = QVBoxLayout()

        self.instrument_combo = QComboBox()
        self.instrument_combo.addItems(
            [
                "SDO/AIA (Atmospheric Imaging Assembly)",
                "SDO/HMI (Helioseismic and Magnetic Imager)",
                "IRIS (Interface Region Imaging Spectrograph)",
                "SOHO (Solar and Heliospheric Observatory)",
            ]
        )
        self.instrument_combo.currentIndexChanged.connect(self.on_instrument_changed)

        layout.addWidget(self.instrument_combo)
        group.setLayout(layout)
        self.layout.addWidget(group)

    def create_parameter_widgets(self):
        """Create the parameter selection widgets for each instrument."""
        self.param_group = QGroupBox("Instrument Parameters")
        self.param_layout = QVBoxLayout()

        # AIA parameters
        self.aia_params = QWidget()
        aia_layout = QVBoxLayout()

        # Wavelength selection
        wavelength_layout = QHBoxLayout()
        wavelength_layout.addWidget(QLabel("Wavelength:"))
        self.wavelength_combo = QComboBox()
        self.wavelength_combo.addItems(
            [
                "94 Å",
                "131 Å",
                "171 Å",
                "193 Å",
                "211 Å",
                "304 Å",
                "335 Å",
                "1600 Å",
                "1700 Å",
                "4500 Å",
            ]
        )
        wavelength_layout.addWidget(self.wavelength_combo)
        aia_layout.addLayout(wavelength_layout)

        # Cadence selection
        cadence_layout = QHBoxLayout()
        cadence_layout.addWidget(QLabel("Cadence:"))
        self.cadence_combo = QComboBox()
        self.cadence_combo.addItems(["12s", "24s", "1h"])
        cadence_layout.addWidget(self.cadence_combo)
        aia_layout.addLayout(cadence_layout)

        self.aia_params.setLayout(aia_layout)

        # HMI parameters
        self.hmi_params = QWidget()
        hmi_layout = QVBoxLayout()

        series_layout = QHBoxLayout()
        series_layout.addWidget(QLabel("Series:"))
        self.series_combo = QComboBox()
        self.series_combo.addItems(
            [
                "45s (Vector magnetogram)",
                "720s (Vector magnetogram)",
                "B_45s (Line-of-sight magnetogram)",
                "B_720s (Line-of-sight magnetogram)",
                "Ic_45s (Continuum intensity)",
                "Ic_720s (Continuum intensity)",
            ]
        )
        series_layout.addWidget(self.series_combo)
        hmi_layout.addLayout(series_layout)

        self.hmi_params.setLayout(hmi_layout)

        # IRIS parameters
        self.iris_params = QWidget()
        iris_layout = QVBoxLayout()

        obs_type_layout = QHBoxLayout()
        obs_type_layout.addWidget(QLabel("Observation Type:"))
        self.obs_type_combo = QComboBox()
        self.obs_type_combo.addItems(["SJI", "Raster"])
        obs_type_layout.addWidget(self.obs_type_combo)
        iris_layout.addLayout(obs_type_layout)

        iris_wavelength_layout = QHBoxLayout()
        iris_wavelength_layout.addWidget(QLabel("Wavelength:"))
        self.iris_wavelength_combo = QComboBox()
        self.iris_wavelength_combo.addItems(
            [
                "1330 Å (C II)",
                "1400 Å (Si IV)",
                "2796 Å (Mg II k)",
                "2832 Å (Photosphere)",
            ]
        )
        iris_wavelength_layout.addWidget(self.iris_wavelength_combo)
        iris_layout.addLayout(iris_wavelength_layout)

        self.iris_params.setLayout(iris_layout)

        # SOHO parameters
        self.soho_params = QWidget()
        soho_layout = QVBoxLayout()

        soho_instrument_layout = QHBoxLayout()
        soho_instrument_layout.addWidget(QLabel("SOHO Instrument:"))
        self.soho_instrument_combo = QComboBox()
        self.soho_instrument_combo.addItems(["EIT", "LASCO", "MDI"])
        self.soho_instrument_combo.currentIndexChanged.connect(
            self.on_soho_instrument_changed
        )
        soho_instrument_layout.addWidget(self.soho_instrument_combo)
        soho_layout.addLayout(soho_instrument_layout)

        # SOHO EIT wavelength
        self.soho_eit_params = QWidget()
        eit_layout = QHBoxLayout()
        eit_layout.addWidget(QLabel("Wavelength:"))
        self.eit_wavelength_combo = QComboBox()
        self.eit_wavelength_combo.addItems(
            ["171 Å (Fe IX/X)", "195 Å (Fe XII)", "284 Å (Fe XV)", "304 Å (He II)"]
        )
        eit_layout.addWidget(self.eit_wavelength_combo)
        self.soho_eit_params.setLayout(eit_layout)

        # SOHO LASCO detector
        self.soho_lasco_params = QWidget()
        lasco_layout = QHBoxLayout()
        lasco_layout.addWidget(QLabel("Detector:"))
        self.lasco_detector_combo = QComboBox()
        self.lasco_detector_combo.addItems(["C1", "C2", "C3"])
        lasco_layout.addWidget(self.lasco_detector_combo)
        self.soho_lasco_params.setLayout(lasco_layout)

        soho_layout.addWidget(self.soho_eit_params)
        soho_layout.addWidget(self.soho_lasco_params)
        self.soho_params.setLayout(soho_layout)

        # Add all parameter widgets to the group
        self.param_layout.addWidget(self.aia_params)
        self.param_layout.addWidget(self.hmi_params)
        self.param_layout.addWidget(self.iris_params)
        self.param_layout.addWidget(self.soho_params)

        self.param_group.setLayout(self.param_layout)
        self.layout.addWidget(self.param_group)

        # Show only AIA parameters initially
        self.hmi_params.hide()
        self.iris_params.hide()
        self.soho_params.hide()
        self.soho_eit_params.hide()
        self.soho_lasco_params.hide()

    def create_time_selection(self):
        """Create the time range selection section."""
        group = QGroupBox("Time Range")
        layout = QVBoxLayout()

        # Start time
        start_layout = QHBoxLayout()
        start_layout.addWidget(QLabel("Start:"))
        self.start_datetime = QDateTimeEdit()
        self.start_datetime.setDateTime(QDateTime.currentDateTime())
        self.start_datetime.setCalendarPopup(True)
        self.start_datetime.setDisplayFormat("yyyy.MM.dd HH:mm:ss")  # 24-hour format
        start_layout.addWidget(self.start_datetime)
        layout.addLayout(start_layout)

        # End time
        end_layout = QHBoxLayout()
        end_layout.addWidget(QLabel("End:"))
        self.end_datetime = QDateTimeEdit()
        self.end_datetime.setDateTime(
            QDateTime.currentDateTime().addSecs(3600)  # Default to 1 hour later
        )
        self.end_datetime.setCalendarPopup(True)
        self.end_datetime.setDisplayFormat("yyyy.MM.dd HH:mm:ss")  # 24-hour format
        end_layout.addWidget(self.end_datetime)
        layout.addLayout(end_layout)

        group.setLayout(layout)
        self.layout.addWidget(group)

    def create_output_selection(self):
        """Create the output directory selection section."""
        group = QGroupBox("Output Settings")
        layout = QVBoxLayout()

        # Output directory
        dir_layout = QHBoxLayout()
        dir_layout.addWidget(QLabel("Output Directory:"))
        self.output_dir = QLineEdit()
        self.output_dir.setText(os.path.join(os.getcwd(), "solar_data"))
        dir_layout.addWidget(self.output_dir)

        browse_button = QPushButton("Browse...")
        browse_button.clicked.connect(self.browse_output_dir)
        dir_layout.addWidget(browse_button)
        layout.addLayout(dir_layout)

        # Download method
        method_layout = QHBoxLayout()
        method_layout.addWidget(QLabel("Download Method:"))
        self.method_group = QButtonGroup()

        drms_radio = QRadioButton("DRMS")
        drms_radio.setChecked(True)
        self.method_group.addButton(drms_radio, 0)
        method_layout.addWidget(drms_radio)

        fido_radio = QRadioButton("Fido")
        self.method_group.addButton(fido_radio, 1)
        method_layout.addWidget(fido_radio)

        layout.addLayout(method_layout)

        # Email for DRMS
        email_layout = QHBoxLayout()
        email_layout.addWidget(QLabel("Email (for DRMS):"))
        self.email_input = QLineEdit()
        email_layout.addWidget(self.email_input)
        layout.addLayout(email_layout)

        group.setLayout(layout)
        self.layout.addWidget(group)

    def create_download_section(self):
        """Create the download button and progress section."""
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.hide()
        self.layout.addWidget(self.progress_bar)

        # Status label
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.layout.addWidget(self.status_label)

        # Download button
        self.download_button = QPushButton("Download")
        self.download_button.clicked.connect(self.start_download)
        self.layout.addWidget(self.download_button)

    def browse_output_dir(self):
        """Open a directory selection dialog."""
        dir_path = QFileDialog.getExistingDirectory(
            self,
            "Select Output Directory",
            self.output_dir.text(),
            QFileDialog.ShowDirsOnly,  # PyQt5 version doesn't use Option enum
        )
        if dir_path:
            self.output_dir.setText(dir_path)

    def on_instrument_changed(self, index):
        """Handle instrument selection changes."""
        # Hide all parameter widgets
        self.aia_params.hide()
        self.hmi_params.hide()
        self.iris_params.hide()
        self.soho_params.hide()

        # Show the selected instrument's parameters
        if index == 0:  # AIA
            self.aia_params.show()
        elif index == 1:  # HMI
            self.hmi_params.show()
        elif index == 2:  # IRIS
            self.iris_params.show()
        elif index == 3:  # SOHO
            self.soho_params.show()
            self.on_soho_instrument_changed(self.soho_instrument_combo.currentIndex())

    def on_soho_instrument_changed(self, index):
        """Handle SOHO instrument selection changes."""
        self.soho_eit_params.hide()
        self.soho_lasco_params.hide()

        if index == 0:  # EIT
            self.soho_eit_params.show()
        elif index == 1:  # LASCO
            self.soho_lasco_params.show()

    def get_download_parameters(self) -> dict:
        """Gather all parameters needed for the download."""
        instrument_index = self.instrument_combo.currentIndex()
        start_time = self.start_datetime.dateTime().toString("yyyy.MM.dd HH:mm:ss")
        end_time = self.end_datetime.dateTime().toString("yyyy.MM.dd HH:mm:ss")
        output_dir = self.output_dir.text()
        use_fido = self.method_group.checkedId() == 1
        email = self.email_input.text() if not use_fido else None

        params = {
            "start_time": start_time,
            "end_time": end_time,
            "output_dir": output_dir,
            "use_fido": use_fido,
            "email": email,
        }

        if instrument_index == 0:  # AIA
            params.update(
                {
                    "instrument": "AIA",
                    "wavelength": self.wavelength_combo.currentText().split()[0],
                    "cadence": self.cadence_combo.currentText(),
                }
            )
        elif instrument_index == 1:  # HMI
            params.update(
                {
                    "instrument": "HMI",
                    "series": self.series_combo.currentText().split()[0],
                }
            )
        elif instrument_index == 2:  # IRIS
            params.update(
                {
                    "instrument": "IRIS",
                    "obs_type": self.obs_type_combo.currentText(),
                    "wavelength": self.iris_wavelength_combo.currentText().split()[0],
                }
            )
        elif instrument_index == 3:  # SOHO
            soho_instrument = self.soho_instrument_combo.currentText()
            params.update({"instrument": "SOHO", "soho_instrument": soho_instrument})

            if soho_instrument == "EIT":
                params["wavelength"] = self.eit_wavelength_combo.currentText().split()[
                    0
                ]
            elif soho_instrument == "LASCO":
                params["detector"] = self.lasco_detector_combo.currentText()

        return params

    def start_download(self):
        """Start the download process."""
        try:
            # Create output directory if it doesn't exist
            Path(self.output_dir.text()).mkdir(parents=True, exist_ok=True)

            # Disable the download button and show progress
            self.download_button.setEnabled(False)
            self.progress_bar.setMaximum(0)  # Indeterminate progress
            self.progress_bar.show()
            self.status_label.setText("Preparing download...")

            # Create and start the download worker
            params = self.get_download_parameters()
            self.download_worker = DownloadWorker(params)
            self.download_worker.progress.connect(self.update_progress)
            self.download_worker.finished.connect(self.download_finished)
            self.download_worker.error.connect(self.download_error)
            self.download_worker.start()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to start download: {str(e)}")
            self.download_button.setEnabled(True)
            self.progress_bar.hide()

    def update_progress(self, message):
        """Update the progress display."""
        self.status_label.setText(message)

    def download_finished(self, files):
        """Handle download completion."""
        self.download_button.setEnabled(True)
        self.progress_bar.hide()

        if files:
            message = f"Download complete! Downloaded {len(files)} files to {self.output_dir.text()}"
            self.status_label.setText(message)
            QMessageBox.information(self, "Success", message)
        else:
            QMessageBox.warning(self, "Warning", "No files were downloaded.")

    def download_error(self, error_message):
        """Handle download errors."""
        self.download_button.setEnabled(True)
        self.progress_bar.hide()
        self.status_label.setText(f"Error: {error_message}")
        QMessageBox.critical(self, "Error", f"Download failed: {error_message}")


def launch_gui(parent=None) -> SolarDataViewerGUI:
    """
    Launch the Solar Data Viewer GUI.

    Args:
        parent: Optional parent widget for integration with other PyQt applications

    Returns:
        SolarDataViewerGUI: The main window instance
    """
    if not QApplication.instance():
        app = QApplication(sys.argv)
    else:
        app = QApplication.instance()

    window = SolarDataViewerGUI(parent)
    window.show()

    if parent is None:
        sys.exit(app.exec_())  # Note the underscore in exec_ for PyQt5

    return window


if __name__ == "__main__":
    launch_gui()

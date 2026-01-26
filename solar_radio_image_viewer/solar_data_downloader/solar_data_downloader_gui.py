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
        QCheckBox,
        QScrollArea,
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
    from ..styles import set_hand_cursor
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


# Instruments that only support Fido (no DRMS option)
FIDO_ONLY_INSTRUMENTS = ["IRIS", "SOHO", "GOES SUVI", "STEREO", "GONG"]


class DownloadWorker(QThread):
    """Worker thread for handling downloads using subprocess with real-time progress updates."""

    progress = pyqtSignal(str)  # Signal to update progress text
    finished = pyqtSignal(list)  # Signal emitted with list of downloaded files
    error = pyqtSignal(str)  # Signal emitted when an error occurs

    def __init__(self, download_params: dict):
        super().__init__()
        self.params = download_params
        self._cancelled = False

    def cancel(self):
        """Cancel the download."""
        self._cancelled = True

    def run(self):
        """Execute the download operation in a subprocess with real-time progress streaming."""
        import subprocess
        import json
        import re
        import time
        import select
        import os
        import sys

        try:
            # Create a temporary Python script to run the download
            script = self._generate_download_script()

            # Run the download in a subprocess with real-time output
            self.progress.emit("Starting download...")

            launch_cwd = os.getcwd() if os.access(os.getcwd(), os.W_OK) else os.path.expanduser("~")
            process = subprocess.Popen(
                [sys.executable, "-u", "-c", script],  # -u for unbuffered output
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,  # Unbuffered
                cwd=launch_cwd,
            )

            # Set non-blocking mode on stderr for progress reading
            import fcntl

            fl = fcntl.fcntl(process.stderr, fcntl.F_GETFL)
            fcntl.fcntl(process.stderr, fcntl.F_SETFL, fl | os.O_NONBLOCK)
            fl = fcntl.fcntl(process.stdout, fcntl.F_GETFL)
            fcntl.fcntl(process.stdout, fcntl.F_SETFL, fl | os.O_NONBLOCK)

            files = []
            stdout_buffer = ""
            stderr_buffer = ""
            total_files = 0
            last_progress_update = time.time()

            # Read output in real-time
            while True:
                if self._cancelled:
                    process.terminate()
                    self.error.emit("Download cancelled by user")
                    return

                # Use select to check for readable data
                readable, _, _ = select.select(
                    [process.stdout, process.stderr], [], [], 0.1
                )

                # Read from stdout (main status messages)
                if process.stdout in readable:
                    try:
                        chunk = process.stdout.read(4096)
                        if chunk:
                            stdout_buffer += chunk.decode("utf-8", errors="replace")

                            # Process complete lines
                            while "\n" in stdout_buffer:
                                line, stdout_buffer = stdout_buffer.split("\n", 1)
                                line = line.strip()
                                
                                # Print ALL subprocess output to parent terminal
                                if line:
                                    print(line, flush=True)

                                if line.startswith("DOWNLOADED_FILES:"):
                                    try:
                                        files_json = line.replace(
                                            "DOWNLOADED_FILES:", ""
                                        ).strip()
                                        files = json.loads(files_json)
                                    except json.JSONDecodeError:
                                        pass
                                elif line.startswith("Searching for"):
                                    self.progress.emit(line)
                                elif line.startswith("Found"):
                                    self.progress.emit(line)
                                    match = re.search(r"Found (\d+) files", line)
                                    if match:
                                        total_files = int(match.group(1))
                                elif "Successfully" in line:
                                    self.progress.emit(line)
                                elif "Processing" in line:
                                    self.progress.emit(line)
                                # New diagnostic messages for better visibility
                                elif "Calibration:" in line:
                                    self.progress.emit(line)
                                elif "NO DATA FOUND" in line:
                                    self.progress.emit("❌ No data found for this query")
                                elif "Retrying" in line:
                                    self.progress.emit(line)
                                elif "WARNING:" in line:
                                    self.progress.emit(line)
                                elif "Starting download" in line:
                                    self.progress.emit(line)
                                elif "Troubleshooting" in line or "Check VSO" in line:
                                    self.progress.emit(line)
                    except (BlockingIOError, IOError):
                        pass

                # Read from stderr (parfive progress bars)
                if process.stderr in readable:
                    try:
                        chunk = process.stderr.read(4096)
                        if chunk:
                            # Write raw output to terminal for live progress bars
                            sys.stderr.write(chunk.decode("utf-8", errors="replace"))
                            sys.stderr.flush()
                            
                            stderr_buffer += chunk.decode("utf-8", errors="replace")

                            # Process carriage return separated updates
                            while "\r" in stderr_buffer or "\n" in stderr_buffer:
                                # Split on either \r or \n
                                sep = "\r" if "\r" in stderr_buffer else "\n"
                                line, stderr_buffer = stderr_buffer.split(sep, 1)

                                # Clean ANSI escape codes
                                clean_line = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()

                                current_time = time.time()

                                # Parse "Files Downloaded:" progress
                                if "Files Downloaded:" in clean_line:
                                    match = re.search(
                                        r"Files Downloaded:\s*(\d+)%.*?(\d+)/(\d+)",
                                        clean_line,
                                    )
                                    if match:
                                        pct = match.group(1)
                                        completed = match.group(2)
                                        total = match.group(3)
                                        if current_time - last_progress_update > 0.5:
                                            self.progress.emit(
                                                f"Downloading: {completed}/{total} files ({pct}%)"
                                            )
                                            last_progress_update = current_time
                                    else:
                                        match = re.search(r"(\d+)%", clean_line)
                                        if (
                                            match
                                            and current_time - last_progress_update
                                            > 0.5
                                        ):
                                            pct = match.group(1)
                                            self.progress.emit(f"Downloading... {pct}%")
                                            last_progress_update = current_time
                    except (BlockingIOError, IOError):
                        pass

                # Check if process has finished
                if process.poll() is not None:
                    # Read any remaining buffered output
                    try:
                        remaining_stdout = process.stdout.read()
                        if remaining_stdout:
                            stdout_buffer += remaining_stdout.decode(
                                "utf-8", errors="replace"
                            )
                    except:
                        pass

                    # Process remaining stdout
                    for line in stdout_buffer.split("\n"):
                        line = line.strip()
                        if line.startswith("DOWNLOADED_FILES:"):
                            try:
                                files_json = line.replace(
                                    "DOWNLOADED_FILES:", ""
                                ).strip()
                                files = json.loads(files_json)
                            except json.JSONDecodeError:
                                pass
                    break

            if process.returncode != 0:
                self.error.emit("Download failed. Check console for details.")
                return

            self.progress.emit(f"Download complete! {len(files)} files")
            self.finished.emit(files)

        except Exception as e:
            self.error.emit(str(e))

    def _generate_download_script(self):
        """Generate a Python script to run the download."""
        import json

        params = self.params
        instrument = params.get("instrument")

        script_lines = [
            "import sys",
            "import json",
            f"sys.path.insert(0, '{os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}' )",
            "from solar_radio_image_viewer.solar_data_downloader import solar_data_downloader as sdd",
            "",
            "try:",
        ]

        if instrument == "AIA":
            skip_cal = params.get("skip_calibration", False)
            apply_psf = params.get("apply_psf", False)
            apply_deg = params.get("apply_degradation", True)
            apply_exp = params.get("apply_exposure_norm", True)

            if params.get("use_fido", True):
                script_lines.append(
                    f"""    files = sdd.download_aia_with_fido(
        wavelength="{params['wavelength']}",
        start_time="{params['start_time']}",
        end_time="{params['end_time']}",
        output_dir="{params['output_dir']}",
        skip_calibration={skip_cal},
        apply_psf={apply_psf},
        apply_degradation={apply_deg},
        apply_exposure_norm={apply_exp},
    )"""
                )
            else:
                email = params.get("email") or "None"
                script_lines.append(
                    f"""    files = sdd.download_aia(
        wavelength="{params['wavelength']}",
        cadence="{params['cadence']}",
        start_time="{params['start_time']}",
        end_time="{params['end_time']}",
        output_dir="{params['output_dir']}",
        email={email!r},
        skip_calibration={skip_cal},
    )"""
                )

        elif instrument == "HMI":
            skip_cal = params.get("skip_calibration", False)
            if params.get("use_fido", True):
                script_lines.append(
                    f"""    files = sdd.download_hmi_with_fido(
        series="{params['series']}",
        start_time="{params['start_time']}",
        end_time="{params['end_time']}",
        output_dir="{params['output_dir']}",
        skip_calibration={skip_cal},
    )"""
                )
            else:
                email = params.get("email") or "None"
                script_lines.append(
                    f"""    files = sdd.download_hmi(
        series="{params['series']}",
        start_time="{params['start_time']}",
        end_time="{params['end_time']}",
        output_dir="{params['output_dir']}",
        email={email!r},
        skip_calibration={skip_cal},
    )"""
                )

        elif instrument == "IRIS":
            wavelength = params.get("wavelength")
            script_lines.append(
                f"""    files = sdd.download_iris(
        start_time="{params['start_time']}",
        end_time="{params['end_time']}",
        output_dir="{params['output_dir']}",
        obs_type="{params['obs_type']}",
        wavelength={wavelength!r},
    )"""
            )

        elif instrument == "SOHO":
            wavelength = params.get("wavelength")
            detector = params.get("detector")
            script_lines.append(
                f"""    files = sdd.download_soho(
        instrument="{params['soho_instrument']}",
        start_time="{params['start_time']}",
        end_time="{params['end_time']}",
        output_dir="{params['output_dir']}",
        wavelength={wavelength!r},
        detector={detector!r},
    )"""
            )

        elif instrument == "GOES SUVI":
            wavelength = params.get("wavelength")
            level = params.get("level", "2")
            script_lines.append(
                f"""    files = sdd.download_goes_suvi(
        start_time="{params['start_time']}",
        end_time="{params['end_time']}",
        output_dir="{params['output_dir']}",
        wavelength={wavelength!r},
        level="{level}",
    )"""
            )

        elif instrument == "STEREO":
            spacecraft = params.get("spacecraft", "A")
            stereo_inst = params.get("stereo_instrument", "EUVI")
            wavelength = params.get("wavelength")
            script_lines.append(
                f"""    files = sdd.download_stereo(
        start_time="{params['start_time']}",
        end_time="{params['end_time']}",
        output_dir="{params['output_dir']}",
        spacecraft="{spacecraft}",
        instrument="{stereo_inst}",
        wavelength={wavelength!r},
    )"""
            )

        elif instrument == "GONG":
            script_lines.append(
                f"""    files = sdd.download_gong(
        start_time="{params['start_time']}",
        end_time="{params['end_time']}",
        output_dir="{params['output_dir']}",
    )"""
            )

        # Add output section - flush to ensure real-time output
        script_lines.extend(
            [
                "    sys.stdout.flush()",
                "    if files:",
                "        print('DOWNLOADED_FILES:' + json.dumps(files))",
                "        sys.stdout.flush()",
                "    else:",
                "        print('DOWNLOADED_FILES:[]')",
                "        sys.stdout.flush()",
                "except Exception as e:",
                "    import traceback",
                "    traceback.print_exc()",
                "    sys.exit(1)",
            ]
        )

        return "\n".join(script_lines)


class SolarDataViewerGUI(QMainWindow):
    """Main window for the Solar Data Viewer GUI application."""

    def __init__(self, parent=None, initial_datetime=None):
        super().__init__(parent)
        self.setWindowTitle("Solar Data Downloader")
        self.setMinimumWidth(600)
        self.setMinimumHeight(800)

        # Store initial datetime for time selection
        self.initial_datetime = initial_datetime

        # Initialize the main widget and layout
        self.main_widget = QWidget()
        self.setCentralWidget(self.main_widget)
        self.layout = QVBoxLayout(self.main_widget)

        # Create the UI components
        self.create_instrument_selection()
        self.create_parameter_widgets()
        self.create_time_selection()
        self.create_output_selection()
        self.create_calibration_options()
        self.create_download_section()

        # Initialize the download worker
        self.download_worker = None

        # Initial update for method visibility
        self.on_instrument_changed(0)
        
        try:
            set_hand_cursor(self)
        except:
            pass

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
                "SDO/AIA (Atmospheric Imaging Assembly)",
                "SDO/HMI (Helioseismic and Magnetic Imager)",
                "IRIS (Interface Region Imaging Spectrograph)",
                "SOHO (Solar and Heliospheric Observatory)",
                "GOES/SUVI (Solar Ultraviolet Imager)",
                "STEREO/SECCHI (Sun Earth Connection)",
                # "GONG (Global Oscillation Network Group)",
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
                "94 Å (Fe XVIII, hot)",
                "131 Å (Fe VIII/XXI)",
                "171 Å (Fe IX, corona)",
                "193 Å (Fe XII/XXIV)",
                "211 Å (Fe XIV)",
                "304 Å (He II)",
                "335 Å (Fe XVI)",
                "1600 Å (C IV, UV)",
                "1700 Å (UV continuum)",
                "4500 Å (Visible)",
            ]
        )
        self.wavelength_combo.currentIndexChanged.connect(
            self.on_aia_wavelength_changed
        )
        wavelength_layout.addWidget(self.wavelength_combo)
        aia_layout.addLayout(wavelength_layout)

        # Cadence selection (for DRMS only)
        self.cadence_widget = QWidget()
        cadence_layout = QHBoxLayout()
        cadence_layout.setContentsMargins(0, 0, 0, 0)
        cadence_layout.addWidget(QLabel("Cadence:"))
        self.cadence_combo = QComboBox()
        self.cadence_combo.addItems(["12s", "24s", "1h"])
        cadence_layout.addWidget(self.cadence_combo)
        self.cadence_widget.setLayout(cadence_layout)
        aia_layout.addWidget(self.cadence_widget)

        self.aia_params.setLayout(aia_layout)

        # HMI parameters
        self.hmi_params = QWidget()
        hmi_layout = QVBoxLayout()

        series_layout = QHBoxLayout()
        series_layout.addWidget(QLabel("Series:"))
        self.series_combo = QComboBox()
        self.series_combo.addItems(
            [
                "45s (LOS magnetogram)",
                "720s (LOS magnetogram, 12 min)",
                "B_45s (LOS magnetogram)",
                "B_720s (LOS magnetogram, 12 min)",
                "Ic_45s (Continuum intensity)",
                "Ic_720s (Continuum intensity, 12 min)",
                "V_45s (LOS velocity)",
                "V_720s (LOS velocity, 12 min)",
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
        self.obs_type_combo.addItems(["SJI (Slit-Jaw)", "Raster (Spectrograph)"])
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
        eit_layout.setContentsMargins(0, 0, 0, 0)
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
        lasco_layout.setContentsMargins(0, 0, 0, 0)
        lasco_layout.addWidget(QLabel("Detector:"))
        self.lasco_detector_combo = QComboBox()
        self.lasco_detector_combo.addItems(["C2 (2-6 Rs)", "C3 (3.7-30 Rs)"])
        lasco_layout.addWidget(self.lasco_detector_combo)
        self.soho_lasco_params.setLayout(lasco_layout)

        soho_layout.addWidget(self.soho_eit_params)
        soho_layout.addWidget(self.soho_lasco_params)
        self.soho_params.setLayout(soho_layout)

        # GOES SUVI parameters
        self.suvi_params = QWidget()
        suvi_layout = QVBoxLayout()

        suvi_wavelength_layout = QHBoxLayout()
        suvi_wavelength_layout.addWidget(QLabel("Wavelength:"))
        self.suvi_wavelength_combo = QComboBox()
        self.suvi_wavelength_combo.addItems(
            [
                "94 Å (Fe XVIII)",
                "131 Å (Fe VIII/XXI)",
                "171 Å (Fe IX)",
                "195 Å (Fe XII)",
                "284 Å (Fe XV)",
                "304 Å (He II)",
            ]
        )
        suvi_wavelength_layout.addWidget(self.suvi_wavelength_combo)
        suvi_layout.addLayout(suvi_wavelength_layout)

        suvi_level_layout = QHBoxLayout()
        suvi_level_layout.addWidget(QLabel("Data Level:"))
        self.suvi_level_combo = QComboBox()
        self.suvi_level_combo.addItems(["Level 2 (calibrated)", "Level 1b (raw)"])
        suvi_level_layout.addWidget(self.suvi_level_combo)
        suvi_layout.addLayout(suvi_level_layout)

        self.suvi_params.setLayout(suvi_layout)

        # STEREO parameters
        self.stereo_params = QWidget()
        stereo_layout = QVBoxLayout()

        stereo_sc_layout = QHBoxLayout()
        stereo_sc_layout.addWidget(QLabel("Spacecraft:"))
        self.stereo_sc_combo = QComboBox()
        self.stereo_sc_combo.addItems(["STEREO-A", "STEREO-B (pre-2014 only)"])
        stereo_sc_layout.addWidget(self.stereo_sc_combo)
        stereo_layout.addLayout(stereo_sc_layout)

        stereo_inst_layout = QHBoxLayout()
        stereo_inst_layout.addWidget(QLabel("Instrument:"))
        self.stereo_inst_combo = QComboBox()
        self.stereo_inst_combo.addItems(
            [
                "EUVI (EUV Imager)",
                "COR1 (Inner coronagraph)",
                "COR2 (Outer coronagraph)",
            ]
        )
        self.stereo_inst_combo.currentIndexChanged.connect(
            self.on_stereo_instrument_changed
        )
        stereo_inst_layout.addWidget(self.stereo_inst_combo)
        stereo_layout.addLayout(stereo_inst_layout)

        # STEREO EUVI wavelength
        self.stereo_euvi_params = QWidget()
        euvi_layout = QHBoxLayout()
        euvi_layout.setContentsMargins(0, 0, 0, 0)
        euvi_layout.addWidget(QLabel("Wavelength:"))
        self.stereo_wavelength_combo = QComboBox()
        self.stereo_wavelength_combo.addItems(["171 Å", "195 Å", "284 Å", "304 Å"])
        euvi_layout.addWidget(self.stereo_wavelength_combo)
        self.stereo_euvi_params.setLayout(euvi_layout)
        stereo_layout.addWidget(self.stereo_euvi_params)

        self.stereo_params.setLayout(stereo_layout)

        # GONG parameters (minimal - just magnetograms)
        self.gong_params = QWidget()
        gong_layout = QVBoxLayout()
        gong_label = QLabel(
            "GONG provides magnetogram data.\nNo additional parameters needed."
        )
        gong_label.setStyleSheet("color: gray; font-style: italic;")
        gong_layout.addWidget(gong_label)
        self.gong_params.setLayout(gong_layout)

        # Add all parameter widgets to the group
        self.param_layout.addWidget(self.aia_params)
        self.param_layout.addWidget(self.hmi_params)
        self.param_layout.addWidget(self.iris_params)
        self.param_layout.addWidget(self.soho_params)
        self.param_layout.addWidget(self.suvi_params)
        self.param_layout.addWidget(self.stereo_params)
        self.param_layout.addWidget(self.gong_params)

        self.param_group.setLayout(self.param_layout)
        self.layout.addWidget(self.param_group)

        # Hide all except AIA initially
        self.hmi_params.hide()
        self.iris_params.hide()
        self.soho_params.hide()
        self.soho_eit_params.hide()
        self.soho_lasco_params.hide()
        self.suvi_params.hide()
        self.stereo_params.hide()
        self.gong_params.hide()

    def create_time_selection(self):
        """Create the time range selection section."""
        group = QGroupBox("Time Range")
        layout = QVBoxLayout()

        # Start time
        start_layout = QHBoxLayout()
        start_layout.addWidget(QLabel("Start:"))
        self.start_datetime = QDateTimeEdit()
        self.start_datetime.setCalendarPopup(True)
        self.start_datetime.setDisplayFormat("yyyy.MM.dd HH:mm:ss")

        # Use initial_datetime if provided, otherwise current time
        if self.initial_datetime:
            self.start_datetime.setDateTime(QDateTime(self.initial_datetime))
        else:
            self.start_datetime.setDateTime(QDateTime.currentDateTime())

        # Connect to sync end time when start time changes
        self.start_datetime.dateTimeChanged.connect(self.on_start_datetime_changed)
        start_layout.addWidget(self.start_datetime)
        layout.addLayout(start_layout)

        # End time
        end_layout = QHBoxLayout()
        end_layout.addWidget(QLabel("End:"))
        self.end_datetime = QDateTimeEdit()
        self.end_datetime.setCalendarPopup(True)
        self.end_datetime.setDisplayFormat("yyyy.MM.dd HH:mm:ss")

        # Use initial_datetime + 1 hour if provided, otherwise current time + 1 hour
        if self.initial_datetime:
            from datetime import timedelta

            end_dt = self.initial_datetime + timedelta(hours=1)
            self.end_datetime.setDateTime(QDateTime(end_dt))
        else:
            self.end_datetime.setDateTime(
                QDateTime.currentDateTime().addSecs(3600)  # Default to 1 hour later
            )
        end_layout.addWidget(self.end_datetime)
        layout.addLayout(end_layout)

        # Cadence info label
        self.cadence_label = QLabel()
        self.cadence_label.setStyleSheet("color: #888; font-style: italic;")
        self.cadence_label.setWordWrap(True)
        self.update_cadence_info()  # Set initial value
        layout.addWidget(self.cadence_label)

        group.setLayout(layout)
        self.layout.addWidget(group)

    def on_start_datetime_changed(self, new_datetime):
        """Sync end time when start time is changed (keep 1 hour difference)."""
        # Set end time to start time + 1 hour
        end_dt = new_datetime.addSecs(3600)
        self.end_datetime.setDateTime(end_dt)

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

        # Download method section
        self.method_widget = QWidget()
        method_layout = QHBoxLayout()
        method_layout.setContentsMargins(0, 0, 0, 0)
        method_layout.addWidget(QLabel("Download Method:"))
        self.method_group = QButtonGroup()

        self.fido_radio = QRadioButton("Fido (recommended)")
        self.fido_radio.setChecked(True)  # Fido is now default
        self.method_group.addButton(self.fido_radio, 1)
        method_layout.addWidget(self.fido_radio)

        self.drms_radio = QRadioButton("DRMS")
        self.method_group.addButton(self.drms_radio, 0)
        method_layout.addWidget(self.drms_radio)

        self.method_group.buttonClicked.connect(self.on_method_changed)
        self.method_widget.setLayout(method_layout)
        layout.addWidget(self.method_widget)

        # Email for DRMS (hidden by default since Fido is default)
        self.email_widget = QWidget()
        email_layout = QHBoxLayout()
        email_layout.setContentsMargins(0, 0, 0, 0)
        email_layout.addWidget(QLabel("Email (for DRMS):"))
        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("Required for DRMS downloads")
        email_layout.addWidget(self.email_input)
        self.email_widget.setLayout(email_layout)
        self.email_widget.hide()  # Hidden by default
        layout.addWidget(self.email_widget)

        group.setLayout(layout)
        self.layout.addWidget(group)

    def create_calibration_options(self):
        """Create the calibration options section."""
        group = QGroupBox("Calibration Options")
        layout = QVBoxLayout()

        # Main calibration checkbox
        self.calibrate_checkbox = QCheckBox("Apply Level 1.5 Calibration")
        self.calibrate_checkbox.setChecked(True)
        self.calibrate_checkbox.setToolTip(
            "Apply standard calibration (recommended for scientific use)\n"
            "Includes: pointing correction, rotation, scaling, centering"
        )
        self.calibrate_checkbox.stateChanged.connect(self.on_calibration_changed)
        layout.addWidget(self.calibrate_checkbox)

        # Advanced options container
        self.calib_options_widget = QWidget()
        calib_layout = QVBoxLayout()
        calib_layout.setContentsMargins(20, 0, 0, 0)  # Indent

        # AIA-specific options
        self.aia_calib_label = QLabel("AIA Advanced Options:")
        self.aia_calib_label.setStyleSheet("font-weight: bold;")
        calib_layout.addWidget(self.aia_calib_label)

        self.psf_checkbox = QCheckBox("PSF Deconvolution")
        self.psf_checkbox.setChecked(False)  # Off by default (slow)
        self.psf_checkbox.setToolTip(
            "Apply Point Spread Function deconvolution to sharpen images.\n"
            "⚠️ WARNING: This is VERY SLOW (~30-60 seconds per image)\n"
            "Uses Richardson-Lucy algorithm with 25 iterations.\n"
            "Downloads PSF data from JSOC on first use."
        )
        calib_layout.addWidget(self.psf_checkbox)

        # Warning label for PSF
        self.psf_warning = QLabel("⚠️ PSF deconvolution might take a while")
        self.psf_warning.setStyleSheet("color: orange;")
        self.psf_warning.hide()
        self.psf_checkbox.stateChanged.connect(
            lambda state: self.psf_warning.setVisible(state == 2)
        )
        calib_layout.addWidget(self.psf_warning)

        self.degradation_checkbox = QCheckBox("Degradation Correction")
        self.degradation_checkbox.setChecked(True)
        self.degradation_checkbox.setToolTip(
            "Apply time-dependent degradation correction.\n"
            "Compensates for instrument sensitivity changes over time."
        )
        calib_layout.addWidget(self.degradation_checkbox)

        self.exposure_norm_checkbox = QCheckBox("Exposure Time Normalization")
        self.exposure_norm_checkbox.setChecked(True)
        self.exposure_norm_checkbox.setToolTip(
            "Normalize data by exposure time.\n"
            "Converts DN to DN/s for consistent comparison."
        )
        calib_layout.addWidget(self.exposure_norm_checkbox)

        self.calib_options_widget.setLayout(calib_layout)
        layout.addWidget(self.calib_options_widget)

        group.setLayout(layout)
        self.layout.addWidget(group)

    def on_calibration_changed(self, state):
        """Handle calibration checkbox state changes."""
        self.calib_options_widget.setVisible(state == 2)

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
            QFileDialog.ShowDirsOnly,
        )
        if dir_path:
            self.output_dir.setText(dir_path)

    def get_instrument_name(self, index):
        """Get short instrument name from index."""
        names = ["AIA", "HMI", "IRIS", "SOHO", "GOES SUVI", "STEREO", "GONG"]
        return names[index] if index < len(names) else "Unknown"

    def on_instrument_changed(self, index):
        """Handle instrument selection changes."""
        # Hide all parameter widgets
        self.aia_params.hide()
        self.hmi_params.hide()
        self.iris_params.hide()
        self.soho_params.hide()
        self.suvi_params.hide()
        self.stereo_params.hide()
        self.gong_params.hide()

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
        elif index == 4:  # GOES SUVI
            self.suvi_params.show()
        elif index == 5:  # STEREO
            self.stereo_params.show()
            self.on_stereo_instrument_changed(self.stereo_inst_combo.currentIndex())
        elif index == 6:  # GONG
            self.gong_params.show()

        # Handle Fido-only instruments
        instrument_name = self.get_instrument_name(index)
        if instrument_name in FIDO_ONLY_INSTRUMENTS:
            # Force Fido and hide method selection
            self.fido_radio.setChecked(True)
            self.method_widget.hide()
            self.email_widget.hide()
            self.cadence_widget.hide()
        else:
            # Show method selection for AIA/HMI
            self.method_widget.show()
            self.on_method_changed()

        # Show AIA-specific calibration options only for AIA
        if index == 0:  # AIA
            self.aia_calib_label.show()
            self.psf_checkbox.show()
            self.psf_warning.setVisible(self.psf_checkbox.isChecked())
            self.degradation_checkbox.show()
            self.exposure_norm_checkbox.show()
        else:
            self.aia_calib_label.hide()
            self.psf_checkbox.hide()
            self.psf_warning.hide()
            self.degradation_checkbox.hide()
            self.exposure_norm_checkbox.hide()

        # Update cadence info for the selected instrument
        self.update_cadence_info()

    def on_method_changed(self, button=None):
        """Handle download method changes."""
        use_fido = self.method_group.checkedId() == 1

        if use_fido:
            self.email_widget.hide()
            self.cadence_widget.hide()  # Fido doesn't use cadence
        else:
            self.email_widget.show()
            # Show cadence only for AIA with DRMS
            if self.instrument_combo.currentIndex() == 0:
                self.cadence_widget.show()
                self.on_aia_wavelength_changed()  # Update cadence based on wavelength

    def on_aia_wavelength_changed(self, index=None):
        """Auto-select cadence based on AIA wavelength."""
        wavelength_text = self.wavelength_combo.currentText()

        if "1600" in wavelength_text or "1700" in wavelength_text:
            self.cadence_combo.setCurrentIndex(1)  # 24s
        elif "4500" in wavelength_text:
            self.cadence_combo.setCurrentIndex(2)  # 1h
        else:
            self.cadence_combo.setCurrentIndex(0)  # 12s

        self.update_cadence_info()

    def on_soho_instrument_changed(self, index):
        """Handle SOHO instrument selection changes."""
        self.soho_eit_params.hide()
        self.soho_lasco_params.hide()

        if index == 0:  # EIT
            self.soho_eit_params.show()
        elif index == 1:  # LASCO
            self.soho_lasco_params.show()

        self.update_cadence_info()

    def on_stereo_instrument_changed(self, index):
        """Handle STEREO instrument selection changes."""
        if index == 0:  # EUVI
            self.stereo_euvi_params.show()
        else:  # COR1/COR2
            self.stereo_euvi_params.hide()
        self.update_cadence_info()

    def update_cadence_info(self):
        """Update the cadence info label based on selected instrument and parameters."""
        index = self.instrument_combo.currentIndex()

        # Cadence info for each instrument
        cadence_info = {
            0: {  # AIA
                "default": "12s (EUV), 24s (UV), 1h (4500Å)",
                "wavelengths": {
                    "94": "12s",
                    "131": "12s",
                    "171": "12s",
                    "193": "12s",
                    "211": "12s",
                    "304": "12s",
                    "335": "12s",
                    "1600": "24s",
                    "1700": "24s",
                    "4500": "1 hour",
                },
            },
            1: {"default": "45s (magnetogram), 45s (continuum), 45s (Doppler)"},  # HMI
            2: {  # IRIS
                "default": "Variable (depends on observing program, typically minutes)"
            },
            3: {  # SOHO
                "EIT": "~12 min (synoptic), 1-6 min (campaign)",
                "LASCO": "~12-30 min (C2/C3)",
                "MDI": "1 min (discontinued 2011)",
            },
            4: {"default": "4 min (Level 2 composites)"},  # GOES SUVI
            5: {  # STEREO
                "EUVI": "2.5-10 min (wavelength dependent)",
                "COR1": "5 min",
                "COR2": "15-30 min",
            },
            6: {"default": "1 min (magnetograms)"},  # GONG
        }

        if index == 0:  # AIA - show wavelength-specific cadence
            wl = self.wavelength_combo.currentText().split()[0]
            wl_cadences = cadence_info[0]["wavelengths"]
            cadence = wl_cadences.get(wl, "12s")
            self.cadence_label.setText(f"ℹ️ Typical cadence: {cadence}")
        elif index == 3:  # SOHO - depends on sub-instrument
            soho_inst = self.soho_instrument_combo.currentText()
            cadence = cadence_info[3].get(soho_inst, "Variable")
            self.cadence_label.setText(f"ℹ️ Typical cadence: {cadence}")
        elif index == 5:  # STEREO - depends on sub-instrument
            stereo_inst = self.stereo_inst_combo.currentText()
            if "EUVI" in stereo_inst:
                cadence = cadence_info[5]["EUVI"]
            elif "COR1" in stereo_inst:
                cadence = cadence_info[5]["COR1"]
            else:
                cadence = cadence_info[5]["COR2"]
            self.cadence_label.setText(f"ℹ️ Typical cadence: {cadence}")
        else:
            cadence = cadence_info.get(index, {}).get("default", "Variable")
            self.cadence_label.setText(f"ℹ️ Typical cadence: {cadence}")

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
            # Calibration options
            "skip_calibration": not self.calibrate_checkbox.isChecked(),
            "apply_psf": self.psf_checkbox.isChecked(),
            "apply_degradation": self.degradation_checkbox.isChecked(),
            "apply_exposure_norm": self.exposure_norm_checkbox.isChecked(),
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
            obs_type = "SJI" if "SJI" in self.obs_type_combo.currentText() else "raster"
            params.update(
                {
                    "instrument": "IRIS",
                    "obs_type": obs_type,
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
                params["detector"] = self.lasco_detector_combo.currentText().split()[0]

        elif instrument_index == 4:  # GOES SUVI
            params.update(
                {
                    "instrument": "GOES SUVI",
                    "wavelength": self.suvi_wavelength_combo.currentText().split()[0],
                    "level": (
                        "2"
                        if "Level 2" in self.suvi_level_combo.currentText()
                        else "1b"
                    ),
                }
            )
        elif instrument_index == 5:  # STEREO
            spacecraft = (
                "A" if "STEREO-A" in self.stereo_sc_combo.currentText() else "B"
            )
            stereo_inst_text = self.stereo_inst_combo.currentText()
            if "EUVI" in stereo_inst_text:
                stereo_inst = "EUVI"
            elif "COR1" in stereo_inst_text:
                stereo_inst = "COR1"
            else:
                stereo_inst = "COR2"

            params.update(
                {
                    "instrument": "STEREO",
                    "spacecraft": spacecraft,
                    "stereo_instrument": stereo_inst,
                }
            )
            if stereo_inst == "EUVI":
                params["wavelength"] = (
                    self.stereo_wavelength_combo.currentText().split()[0]
                )

        elif instrument_index == 6:  # GONG
            params.update({"instrument": "GONG"})

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


def launch_gui(parent=None, initial_datetime=None) -> SolarDataViewerGUI:
    """
    Launch the Solar Data Viewer GUI.

    Args:
        parent: Optional parent widget for integration with other PyQt applications
        initial_datetime: Optional datetime to initialize the time selectors

    Returns:
        SolarDataViewerGUI: The main window instance
    """
    if not QApplication.instance():
        app = QApplication(sys.argv)
    else:
        app = QApplication.instance()

    window = SolarDataViewerGUI(parent, initial_datetime=initial_datetime)
    window.show()

    if parent is None:
        sys.exit(app.exec_())

    return window


if __name__ == "__main__":
    launch_gui()

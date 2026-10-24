#!/usr/bin/env python3
"""
Solar Data Downloader window: downloads images from SDO, IRIS, SOHO, GOES
and STEREO through solar_data_downloader and prepares them for SolarViewer.
Works on its own or inside the viewer.
"""

import sys
import os
from pathlib import Path

try:
    from PyQt5.QtWidgets import (
        QApplication,
        QButtonGroup,
        QCheckBox,
        QComboBox,
        QDateTimeEdit,
        QFileDialog,
        QFormLayout,
        QFrame,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QRadioButton,
        QScrollArea,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
    from PyQt5.QtCore import QDateTime, pyqtSignal, QThread
except ImportError:
    print("Error: PyQt5 is required. Please install it with:")
    print("  pip install PyQt5")
    sys.exit(1)

try:
    from . import solar_data_downloader as sdd  # noqa: F401 (availability check)
    from ..ui.styles import set_hand_cursor, set_tone
    from ..ui.geometry import fit_to_screen
except ImportError:
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        if script_dir not in sys.path:
            sys.path.append(script_dir)
        import solar_data_downloader as sdd  # noqa: F401 (availability check)

        def set_hand_cursor(widget):
            pass

        def set_tone(widget, tone):
            pass

        def fit_to_screen(window, width, height, fraction=0.9):
            window.resize(width, height)

    except ImportError:
        print("Error: Could not import solar_data_downloader module.")
        sys.exit(1)

try:
    import sunpy  # noqa: F401 (availability check)
    import drms  # noqa: F401 (availability check)
    import astropy  # noqa: F401 (availability check)
except ImportError as e:
    print(f"Error: Missing required package: {e.name}")
    print("Please install the required packages with:")
    print("  pip install sunpy drms astropy")
    print("For AIA Level 1.5 calibration, also install:")
    print("  pip install aiapy")
    sys.exit(1)


INSTRUMENTS = ["AIA", "HMI", "IRIS", "SOHO", "GOES SUVI", "STEREO"]
# Instruments served only through Fido (no JSOC export)
FIDO_ONLY_INSTRUMENTS = ["IRIS", "SOHO", "GOES SUVI", "STEREO"]

# Cadence choices: label and seconds (None keeps every image)
CADENCES = [
    ("Every image", None),
    ("1 minute", 60),
    ("2 minutes", 120),
    ("5 minutes", 300),
    ("10 minutes", 600),
    ("15 minutes", 900),
    ("30 minutes", 1800),
    ("1 hour", 3600),
    ("6 hours", 21600),
    ("1 day", 86400),
]
DEFAULT_CADENCE = {"AIA": 300, "HMI": 300}

HMI_SERIES_ITEMS = [
    ("Magnetogram, 45 s", "M_45s"),
    ("Continuum intensity, 45 s", "Ic_45s"),
    ("Doppler velocity, 45 s", "V_45s"),
    ("Magnetogram, 720 s (JSOC only)", "M_720s"),
    ("Continuum intensity, 720 s (JSOC only)", "Ic_720s"),
    ("Continuum without limb darkening, 720 s (JSOC only)", "Ic_noLimbDark_720s"),
    ("Doppler velocity, 720 s (JSOC only)", "V_720s"),
]


# Runs one downloader function in a separate process: argv[1] is the folder
# holding the package, argv[2] the JSON request from _download_request
_DOWNLOAD_SCRIPT = """
import json, sys
sys.path.insert(0, sys.argv[1])
from solar_radio_image_viewer.solar_data_downloader import solar_data_downloader as sdd

request = json.loads(sys.argv[2])
try:
    files = getattr(sdd, request["function"])(**request["kwargs"])
    sys.stdout.flush()
    print("DOWNLOADED_FILES:" + json.dumps(files or []))
    sys.stdout.flush()
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(1)
"""


class DownloadWorker(QThread):
    """Runs the download in a separate process and streams its output."""

    progress = pyqtSignal(str)  # a status line
    finished = pyqtSignal(list)  # the files, when done
    error = pyqtSignal(str)  # when it failed

    def __init__(self, download_params: dict):
        super().__init__()
        self.params = download_params
        self._cancelled = False

    def cancel(self):
        """Cancel the download."""
        self._cancelled = True

    def run(self):
        """Run the download process, relaying its status lines and progress."""
        import json
        import os
        import queue
        import re
        import subprocess
        import sys
        import threading
        import time

        try:
            # The download runs in a separate process; what it calls travels
            # as an argument, never as Python source
            function, kwargs = self._download_request()
            request = json.dumps({"function": function, "kwargs": kwargs})
            package_parent = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
            self.progress.emit("Starting download...")

            launch_cwd = os.getcwd() if os.access(os.getcwd(), os.W_OK) else os.path.expanduser("~")
            process = subprocess.Popen(
                [sys.executable, "-u", "-c", _DOWNLOAD_SCRIPT, package_parent, request],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                cwd=launch_cwd,
            )

            # One thread per pipe hands over what arrives (works on every
            # platform, unlike select() on pipes)
            chunks = queue.Queue()

            def pump(stream, name):
                for chunk in iter(lambda: stream.read(4096), b""):
                    chunks.put((name, chunk))
                chunks.put((name, None))

            for stream, name in ((process.stdout, "out"), (process.stderr, "err")):
                threading.Thread(target=pump, args=(stream, name), daemon=True).start()

            files = []
            stdout_buffer = ""
            stderr_buffer = ""
            last_progress_update = time.time()

            def stdout_line(line):
                nonlocal files
                if line:
                    print(line, flush=True)  # the terminal sees everything
                if line.startswith("DOWNLOADED_FILES:"):
                    try:
                        files = json.loads(line.replace("DOWNLOADED_FILES:", "").strip())
                    except json.JSONDecodeError:
                        pass
                elif "NO DATA FOUND" in line:
                    self.progress.emit("No data found for this query")
                elif line and not set(line) <= set("=-"):
                    self.progress.emit(line)

            def stderr_line(line):
                """A parfive progress update (lines end in \\r or \\n)."""
                nonlocal last_progress_update
                clean_line = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
                current_time = time.time()
                if current_time - last_progress_update <= 0.5:
                    return
                match = re.search(r"Files Downloaded:\s*(\d+)%.*?(\d+)/(\d+)", clean_line)
                if "Files Downloaded:" in clean_line and match:
                    pct, completed, total = match.groups()
                    self.progress.emit(f"Downloading: {completed}/{total} files ({pct}%)")
                    last_progress_update = current_time
                    return
                match = re.search(r"(\d+)%", clean_line)
                if match:
                    self.progress.emit(f"Downloading... {match.group(1)}%")
                    last_progress_update = current_time

            open_pipes = 2
            while open_pipes:
                if self._cancelled:
                    process.terminate()
                    self.error.emit("Download cancelled by user")
                    return
                try:
                    name, chunk = chunks.get(timeout=0.1)
                except queue.Empty:
                    continue
                if chunk is None:
                    open_pipes -= 1
                    continue
                text = chunk.decode("utf-8", errors="replace")
                if name == "out":
                    stdout_buffer += text
                    while "\n" in stdout_buffer:
                        line, stdout_buffer = stdout_buffer.split("\n", 1)
                        stdout_line(line.strip())
                else:
                    # Raw output to the terminal, for live progress bars
                    sys.stderr.write(text)
                    sys.stderr.flush()
                    stderr_buffer += text
                    while "\r" in stderr_buffer or "\n" in stderr_buffer:
                        sep = "\r" if "\r" in stderr_buffer else "\n"
                        line, stderr_buffer = stderr_buffer.split(sep, 1)
                        stderr_line(line)

            process.wait()
            if stdout_buffer.strip():
                stdout_line(stdout_buffer.strip())

            if process.returncode != 0:
                self.error.emit("Download failed. Check console for details.")
                return

            self.progress.emit(f"Download complete! {len(files)} files")
            self.finished.emit(files)

        except Exception as e:
            self.error.emit(str(e))

    def _download_request(self):
        """The downloader function to call and its keyword arguments."""
        params = self.params
        instrument = params.get("instrument")
        common = dict(
            start_time=params["start_time"],
            end_time=params["end_time"],
            output_dir=params["output_dir"],
            skip_calibration=params.get("skip_calibration", False),
            sample=params.get("sample"),
            keep_originals=params.get("keep_originals", False),
        )

        if instrument == "AIA":
            aia = dict(
                common,
                wavelength=str(params["wavelength"]),
                apply_psf=params.get("apply_psf", False),
                apply_degradation=params.get("apply_degradation", True),
                apply_exposure_norm=params.get("apply_exposure_norm", True),
            )
            if params.get("use_fido", True):
                return "download_aia_with_fido", aia
            return "download_aia", dict(aia, email=params.get("email") or None)
        if instrument == "HMI":
            hmi = dict(common, series=str(params["series"]))
            if params.get("use_fido", True) and "720" not in hmi["series"]:
                return "download_hmi_with_fido", hmi
            return "download_hmi", dict(hmi, email=params.get("email") or None)
        if instrument == "IRIS":
            return "download_iris", dict(common, obs_type="SJI", wavelength=params.get("wavelength"))
        if instrument == "SOHO":
            return "download_soho", dict(
                common,
                instrument=str(params["soho_instrument"]),
                wavelength=params.get("wavelength"),
                detector=params.get("detector"),
                physobs=params.get("physobs"),
            )
        if instrument == "GOES SUVI":
            return "download_goes_suvi", dict(
                common, wavelength=params.get("wavelength"), level=str(params.get("level", "2"))
            )
        if instrument == "STEREO":
            return "download_stereo", dict(
                common,
                spacecraft=str(params.get("spacecraft", "A")),
                instrument=str(params.get("stereo_instrument", "EUVI")),
                wavelength=params.get("wavelength"),
            )
        raise ValueError(f"Unknown instrument: {instrument}")


def _combo(items, data=None):
    """A combo box of ``items``; ``data`` holds a value per item."""
    box = QComboBox()
    for i, text in enumerate(items):
        box.addItem(text, data[i] if data is not None else text.split()[0])
    return box


def _hint(text=""):
    label = QLabel(text)
    label.setObjectName("Hint")
    label.setWordWrap(True)
    return label


def _form():
    form = QFormLayout()
    form.setContentsMargins(0, 0, 0, 0)
    form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
    return form


class SolarDataViewerGUI(QMainWindow):
    """The Solar Data Downloader window."""

    def __init__(self, parent=None, initial_datetime=None):
        super().__init__(parent)
        self.setWindowTitle("Solar Data Downloader")
        self.setMinimumWidth(600)
        fit_to_screen(self, 640, 900)
        self.initial_datetime = initial_datetime

        self.main_widget = QWidget()
        self.setCentralWidget(self.main_widget)
        outer = QVBoxLayout(self.main_widget)

        # The form scrolls on a short screen; the status and the buttons
        # stay in view below it
        form = QWidget()
        self.layout = QVBoxLayout(form)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.create_instrument_selection()
        self.create_parameter_widgets()
        self.create_time_selection()
        self.create_output_selection()
        self.create_calibration_options()
        self.layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidget(form)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll, 1)

        self.layout = outer
        self.create_download_section()

        self.download_worker = None
        self.on_instrument_changed(0)
        try:
            set_hand_cursor(self)
        except Exception:
            pass

    def closeEvent(self, event):
        """Stop a running download when the window is closed."""
        if getattr(self, "download_worker", None) is not None and self.download_worker.isRunning():
            self.download_worker.cancel()
            self.download_worker.quit()
            self.download_worker.wait(2000)
        super().closeEvent(event)

    # ------------------------------------------------------------ the form
    def create_instrument_selection(self):
        group = QGroupBox("Instrument")
        layout = QVBoxLayout(group)
        self.instrument_combo = QComboBox()
        self.instrument_combo.addItems(
            [
                "SDO/AIA (Atmospheric Imaging Assembly)",
                "SDO/HMI (Helioseismic and Magnetic Imager)",
                "IRIS slit-jaw images (Interface Region Imaging Spectrograph)",
                "SOHO (EIT, LASCO, MDI)",
                "GOES/SUVI (Solar Ultraviolet Imager)",
                "STEREO/SECCHI (EUVI, COR1, COR2)",
            ]
        )
        self.instrument_combo.currentIndexChanged.connect(self.on_instrument_changed)
        layout.addWidget(self.instrument_combo)
        self.layout.addWidget(group)

    def create_parameter_widgets(self):
        """One page of parameters per instrument."""
        self.param_group = QGroupBox("Instrument Parameters")
        self.param_layout = QVBoxLayout(self.param_group)
        self.param_pages = []  # one per instrument; only the chosen one is shown

        # AIA
        page = QWidget()
        form = _form()
        page.setLayout(form)
        self.wavelength_combo = _combo(
            [
                "94 Å (Fe XVIII, flaring)",
                "131 Å (Fe VIII, Fe XXI)",
                "171 Å (Fe IX, quiet corona)",
                "193 Å (Fe XII, Fe XXIV)",
                "211 Å (Fe XIV, active regions)",
                "304 Å (He II, chromosphere)",
                "335 Å (Fe XVI)",
                "1600 Å (C IV, transition region)",
                "1700 Å (UV continuum)",
                "4500 Å (visible continuum)",
            ]
        )
        self.wavelength_combo.setCurrentIndex(2)
        self.wavelength_combo.currentIndexChanged.connect(self.update_cadence_info)
        form.addRow("Wavelength:", self.wavelength_combo)
        self._add_page(page)

        # HMI
        page = QWidget()
        form = _form()
        page.setLayout(form)
        self.series_combo = _combo([t for t, _ in HMI_SERIES_ITEMS], [k for _, k in HMI_SERIES_ITEMS])
        self.series_combo.currentIndexChanged.connect(self.on_hmi_series_changed)
        form.addRow("Series:", self.series_combo)
        self._add_page(page)

        # IRIS
        page = QWidget()
        form = _form()
        page.setLayout(form)
        self.iris_wavelength_combo = _combo(
            [
                "1330 Å (C II, upper chromosphere)",
                "1400 Å (Si IV, transition region)",
                "2796 Å (Mg II k, chromosphere)",
                "2832 Å (Mg II wing, photosphere)",
                "All channels",
            ],
            ["1330", "1400", "2796", "2832", None],
        )
        form.addRow("Channel:", self.iris_wavelength_combo)
        form.addRow(
            _hint(
                "Level 2 slit-jaw cubes; each becomes one file per exposure with "
                "that exposure's time and pointing. Rasters are spectra and are not offered."
            )
        )
        self._add_page(page)

        # SOHO
        page = QWidget()
        form = _form()
        page.setLayout(form)
        self.soho_instrument_combo = _combo(["EIT", "LASCO", "MDI"])
        self.soho_instrument_combo.currentIndexChanged.connect(self.on_soho_instrument_changed)
        form.addRow("Instrument:", self.soho_instrument_combo)
        self.eit_wavelength_combo = _combo(
            ["171 Å (Fe IX, Fe X)", "195 Å (Fe XII)", "284 Å (Fe XV)", "304 Å (He II)"]
        )
        self.eit_wavelength_combo.setCurrentIndex(1)
        self.eit_wavelength_label = QLabel("Wavelength:")
        form.addRow(self.eit_wavelength_label, self.eit_wavelength_combo)
        self.lasco_detector_combo = _combo(["C2 (2 to 6 solar radii)", "C3 (3.7 to 30 solar radii)"])
        self.lasco_detector_label = QLabel("Detector:")
        form.addRow(self.lasco_detector_label, self.lasco_detector_combo)
        self.mdi_product_combo = _combo(
            ["Magnetogram (96 min, level 1.8)", "Continuum intensity"], ["magnetogram", "continuum"]
        )
        self.mdi_product_label = QLabel("Product:")
        form.addRow(self.mdi_product_label, self.mdi_product_combo)
        self._add_page(page)

        # GOES SUVI
        page = QWidget()
        form = _form()
        page.setLayout(form)
        self.suvi_wavelength_combo = _combo(
            [
                "94 Å (Fe XVIII)",
                "131 Å (Fe VIII, Fe XXI)",
                "171 Å (Fe IX)",
                "195 Å (Fe XII)",
                "284 Å (Fe XV)",
                "304 Å (He II)",
            ]
        )
        self.suvi_wavelength_combo.setCurrentIndex(2)
        form.addRow("Wavelength:", self.suvi_wavelength_combo)
        self.suvi_level_combo = _combo(
            ["Level 2 (4 min composites, calibrated)", "Level 1b (single exposures)"], ["2", "1b"]
        )
        form.addRow("Level:", self.suvi_level_combo)
        self._add_page(page)

        # STEREO
        page = QWidget()
        form = _form()
        page.setLayout(form)
        self.stereo_sc_combo = _combo(["STEREO-A", "STEREO-B (until 2014)"], ["A", "B"])
        form.addRow("Spacecraft:", self.stereo_sc_combo)
        self.stereo_inst_combo = _combo(
            ["EUVI (EUV imager)", "COR1 (inner coronagraph, 1.4 to 4 solar radii)", "COR2 (outer coronagraph, 2.5 to 15 solar radii)"]
        )
        self.stereo_inst_combo.currentIndexChanged.connect(self.on_stereo_instrument_changed)
        form.addRow("Instrument:", self.stereo_inst_combo)
        self.stereo_wavelength_combo = _combo(["171 Å", "195 Å", "284 Å", "304 Å"])
        self.stereo_wavelength_combo.setCurrentIndex(1)
        self.stereo_wavelength_label = QLabel("Wavelength:")
        form.addRow(self.stereo_wavelength_label, self.stereo_wavelength_combo)
        self._add_page(page)

        self.layout.addWidget(self.param_group)

    def _add_page(self, page):
        self.param_layout.addWidget(page)
        self.param_pages.append(page)
        page.hide()

    def create_time_selection(self):
        group = QGroupBox("Time Range")
        form = _form()
        form.setContentsMargins(9, 9, 9, 9)
        group.setLayout(form)

        self.start_datetime = QDateTimeEdit()
        self.start_datetime.setCalendarPopup(True)
        self.start_datetime.setDisplayFormat("yyyy.MM.dd HH:mm:ss")
        self.end_datetime = QDateTimeEdit()
        self.end_datetime.setCalendarPopup(True)
        self.end_datetime.setDisplayFormat("yyyy.MM.dd HH:mm:ss")
        if self.initial_datetime:
            from datetime import timedelta

            self.start_datetime.setDateTime(QDateTime(self.initial_datetime))
            self.end_datetime.setDateTime(QDateTime(self.initial_datetime + timedelta(hours=1)))
        else:
            self.start_datetime.setDateTime(QDateTime.currentDateTime())
            self.end_datetime.setDateTime(QDateTime.currentDateTime().addSecs(3600))
        self.start_datetime.dateTimeChanged.connect(self.on_start_datetime_changed)
        form.addRow("Start (UTC):", self.start_datetime)
        form.addRow("End (UTC):", self.end_datetime)

        self.cadence_combo = _combo([t for t, _ in CADENCES], [s for _, s in CADENCES])
        self.cadence_combo.setToolTip("Keep one image per interval; the rest are not downloaded")
        form.addRow("Cadence:", self.cadence_combo)
        self.cadence_label = _hint()
        form.addRow(self.cadence_label)
        self.layout.addWidget(group)

    def on_start_datetime_changed(self, new_datetime):
        """The end time keeps an hour behind a moved start time."""
        self.end_datetime.setDateTime(new_datetime.addSecs(3600))

    def create_output_selection(self):
        group = QGroupBox("Output")
        form = _form()
        form.setContentsMargins(9, 9, 9, 9)
        group.setLayout(form)

        dir_row = QHBoxLayout()
        self.output_dir = QLineEdit()
        self.output_dir.setText(os.path.join(os.getcwd(), "solar_data"))
        dir_row.addWidget(self.output_dir)
        browse_button = QPushButton("Browse...")
        browse_button.clicked.connect(self.browse_output_dir)
        dir_row.addWidget(browse_button)
        form.addRow("Folder:", dir_row)

        self.method_widget = QWidget()
        method_layout = QHBoxLayout(self.method_widget)
        method_layout.setContentsMargins(0, 0, 0, 0)
        self.method_group = QButtonGroup(self)
        self.fido_radio = QRadioButton("VSO (no account needed)")
        self.fido_radio.setChecked(True)
        self.method_group.addButton(self.fido_radio, 1)
        method_layout.addWidget(self.fido_radio)
        self.drms_radio = QRadioButton("JSOC export")
        self.drms_radio.setToolTip(
            "Export from JSOC with an email address registered there.\n"
            "The only source of the HMI 720 s series, and the alternative\n"
            "when the VSO's SDO export is down."
        )
        self.method_group.addButton(self.drms_radio, 0)
        method_layout.addWidget(self.drms_radio)
        method_layout.addStretch()
        self.method_group.buttonClicked.connect(self.on_method_changed)
        self.method_label = QLabel("Source:")
        form.addRow(self.method_label, self.method_widget)

        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("Address registered at jsoc.stanford.edu")
        self.email_label = QLabel("JSOC email:")
        form.addRow(self.email_label, self.email_input)
        self.layout.addWidget(group)

    def create_calibration_options(self):
        group = QGroupBox("Processing")
        layout = QVBoxLayout(group)

        self.calibrate_checkbox = QCheckBox("Prepare the images for SolarViewer")
        self.calibrate_checkbox.setChecked(True)
        self.calibrate_checkbox.setToolTip(
            "One image per file, solar north up, Sun centred, counts per second\n"
            "where the data are raw counts, a standard header."
        )
        self.calibrate_checkbox.stateChanged.connect(self.on_calibration_changed)
        layout.addWidget(self.calibrate_checkbox)
        self.processing_hint = _hint()
        self.processing_hint.setContentsMargins(20, 0, 0, 0)
        layout.addWidget(self.processing_hint)

        self.calib_options_widget = QWidget()
        calib_layout = QVBoxLayout(self.calib_options_widget)
        calib_layout.setContentsMargins(20, 0, 0, 0)

        self.aia_calib_label = QLabel("AIA level 1.5 options")
        self.aia_calib_label.setObjectName("SectionTitle")
        calib_layout.addWidget(self.aia_calib_label)
        self.psf_checkbox = QCheckBox("PSF deconvolution")
        self.psf_checkbox.setToolTip(
            "Sharpen each image with the instrument point spread function\n"
            "(Richardson-Lucy, 25 iterations). Slow: about a minute per image."
        )
        calib_layout.addWidget(self.psf_checkbox)
        self.psf_warning = QLabel("PSF deconvolution takes about a minute per image")
        set_tone(self.psf_warning, "warning")
        self.psf_warning.hide()
        self.psf_checkbox.stateChanged.connect(lambda state: self.psf_warning.setVisible(state == 2))
        calib_layout.addWidget(self.psf_warning)
        self.degradation_checkbox = QCheckBox("Degradation correction")
        self.degradation_checkbox.setChecked(True)
        self.degradation_checkbox.setToolTip("Correct for the loss of sensitivity since launch")
        calib_layout.addWidget(self.degradation_checkbox)
        self.exposure_norm_checkbox = QCheckBox("Counts per second")
        self.exposure_norm_checkbox.setChecked(True)
        self.exposure_norm_checkbox.setToolTip("Divide by the exposure time (DN to DN/s)")
        calib_layout.addWidget(self.exposure_norm_checkbox)

        self.keep_originals_checkbox = QCheckBox("Keep the downloaded files as well")
        self.keep_originals_checkbox.setToolTip(
            "Leave the files as the archive sent them next to the prepared ones"
        )
        calib_layout.addWidget(self.keep_originals_checkbox)
        layout.addWidget(self.calib_options_widget)
        self.layout.addWidget(group)

    def on_calibration_changed(self, state):
        self.calib_options_widget.setVisible(state == 2)
        self.processing_hint.setVisible(state == 2)

    def create_download_section(self):
        """The status log, the progress bar and the Cancel and Download buttons."""
        group = QGroupBox("Status")
        group_layout = QVBoxLayout(group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        line = self.log_text.fontMetrics().lineSpacing()
        self.log_text.setMinimumHeight(line * 5)
        self.log_text.setMaximumHeight(line * 9)
        self.log_text.setPlaceholderText("Download status will appear here...")
        group_layout.addWidget(self.log_text)
        self.layout.addWidget(group)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 0)  # busy
        self.progress_bar.hide()
        self.layout.addWidget(self.progress_bar)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_download)
        buttons.addWidget(self.cancel_button)
        self.download_button = QPushButton("Download")
        self.download_button.setObjectName("PrimaryButton")
        self.download_button.clicked.connect(self.start_download)
        buttons.addWidget(self.download_button)
        self.layout.addLayout(buttons)

    # -------------------------------------------------------------- events
    def log_message(self, message):
        self.log_text.append(message)
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def cancel_download(self):
        if self.download_worker is not None and self.download_worker.isRunning():
            self.download_worker.cancel()
            self.log_message("Cancelling download...")

    def browse_output_dir(self):
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select Output Directory", self.output_dir.text(), QFileDialog.ShowDirsOnly
        )
        if dir_path:
            self.output_dir.setText(dir_path)

    def get_instrument_name(self, index):
        return INSTRUMENTS[index] if index < len(INSTRUMENTS) else "Unknown"

    def on_instrument_changed(self, index):
        for i, page in enumerate(self.param_pages):
            page.setVisible(i == index)
        name = self.get_instrument_name(index)
        if name == "SOHO":
            self.on_soho_instrument_changed(self.soho_instrument_combo.currentIndex())
        elif name == "STEREO":
            self.on_stereo_instrument_changed(self.stereo_inst_combo.currentIndex())

        fido_only = name in FIDO_ONLY_INSTRUMENTS
        if fido_only:
            self.fido_radio.setChecked(True)
        self.method_label.setVisible(not fido_only)
        self.method_widget.setVisible(not fido_only)
        if name == "HMI":
            self.on_hmi_series_changed()
        else:
            self.fido_radio.setEnabled(True)
        self.on_method_changed()

        is_aia = name == "AIA"
        for w in (self.aia_calib_label, self.psf_checkbox, self.degradation_checkbox, self.exposure_norm_checkbox):
            w.setVisible(is_aia)
        self.psf_warning.setVisible(is_aia and self.psf_checkbox.isChecked())

        seconds = DEFAULT_CADENCE.get(name)
        self.cadence_combo.setCurrentIndex([s for _, s in CADENCES].index(seconds))
        self.update_cadence_info()

    def on_method_changed(self, button=None):
        use_jsoc = self.method_group.checkedId() == 0 and self.method_widget.isVisibleTo(self)
        self.email_label.setVisible(use_jsoc)
        self.email_input.setVisible(use_jsoc)

    def on_hmi_series_changed(self, index=None):
        """The 720 s series exist only at JSOC."""
        jsoc_only = "720" in str(self.series_combo.currentData())
        if jsoc_only:
            self.drms_radio.setChecked(True)
        self.fido_radio.setEnabled(not jsoc_only)
        self.on_method_changed()
        self.update_cadence_info()

    def on_soho_instrument_changed(self, index):
        name = self.soho_instrument_combo.currentData()
        for label, widget, shown in (
            (self.eit_wavelength_label, self.eit_wavelength_combo, name == "EIT"),
            (self.lasco_detector_label, self.lasco_detector_combo, name == "LASCO"),
            (self.mdi_product_label, self.mdi_product_combo, name == "MDI"),
        ):
            label.setVisible(shown)
            widget.setVisible(shown)
        self.update_cadence_info()

    def on_stereo_instrument_changed(self, index):
        euvi = self.stereo_inst_combo.currentData() == "EUVI"
        self.stereo_wavelength_label.setVisible(euvi)
        self.stereo_wavelength_combo.setVisible(euvi)
        self.update_cadence_info()

    def update_cadence_info(self, index=None):
        """The native cadence of what is selected, and what preparing it does."""
        name = self.get_instrument_name(self.instrument_combo.currentIndex())
        if name == "AIA":
            wl = self.wavelength_combo.currentData()
            native = {"1600": "24 s", "1700": "24 s", "4500": "1 hour"}.get(wl, "12 s")
            cadence = f"AIA takes a {wl} Å image every {native}; at every image an hour is about 300 files of 12 MB."
            if native != "12 s":
                cadence = f"AIA takes a {wl} Å image every {native}."
            processing = (
                "Level 1.5 with aiapy: pointing update, registration (north up, 0.6 arcsec per pixel, "
                "Sun centred), degradation correction, counts per second."
            )
        elif name == "HMI":
            series = str(self.series_combo.currentData())
            cadence = f"One image every {'720 s' if '720' in series else '45 s'}; each file is about 15 MB."
            processing = "North up with the Sun centred, at the native 0.504 arcsec per pixel."
        elif name == "IRIS":
            cadence = (
                "One file per observation holds every exposure, seconds to minutes apart; "
                "the cadence thins the exposures when the files are prepared."
            )
            processing = "One file per exposure with its own time, exposure time and pointing; counts per second; north up."
        elif name == "SOHO":
            inst = self.soho_instrument_combo.currentData()
            cadence = {
                "EIT": "About every 12 minutes for the synoptic 195 Å images, less often for the other wavelengths.",
                "LASCO": "About every 12 minutes (C2) or 20 minutes (C3).",
                "MDI": "Every 96 minutes; MDI ended in April 2011.",
            }[inst]
            processing = {
                "EIT": "The level 1 product where SDAC has it (dark, flat, degridding, degradation and exposure corrected), else level 0 in counts per second; north up.",
                "LASCO": "The level 0.5 roll undone (north up, Sun centred); counts per second.",
                "MDI": "North up with the Sun centred.",
            }[inst]
        elif name == "GOES SUVI":
            cadence = "Level 2 composites every 4 minutes; level 1b exposures every few seconds."
            processing = "Level 2 composites moved into the primary HDU as they are; level 1b exposures turned north up."
        else:
            inst = self.stereo_inst_combo.currentData()
            cadence = {
                "EUVI": "Every 2.5 to 10 minutes depending on the wavelength.",
                "COR1": "A polarizer triplet every 5 to 10 minutes.",
                "COR2": "A polarizer triplet every 15 to 30 minutes.",
            }[inst]
            processing = (
                "North up with the Sun centred; counts per second."
                if inst == "EUVI"
                else "The three polarizer images of each sequence combined into total brightness; north up; counts per second."
            )
        self.cadence_label.setText(cadence)
        self.processing_hint.setText(processing)

    def get_download_parameters(self) -> dict:
        """What the download needs, from the form."""
        name = self.get_instrument_name(self.instrument_combo.currentIndex())
        use_fido = self.method_group.checkedId() == 1 or name in FIDO_ONLY_INSTRUMENTS
        params = {
            "instrument": name,
            "start_time": self.start_datetime.dateTime().toString("yyyy.MM.dd HH:mm:ss"),
            "end_time": self.end_datetime.dateTime().toString("yyyy.MM.dd HH:mm:ss"),
            "output_dir": self.output_dir.text(),
            "use_fido": use_fido,
            "email": self.email_input.text().strip() if not use_fido else None,
            "sample": self.cadence_combo.currentData(),
            "skip_calibration": not self.calibrate_checkbox.isChecked(),
            "keep_originals": self.keep_originals_checkbox.isChecked(),
            "apply_psf": self.psf_checkbox.isChecked(),
            "apply_degradation": self.degradation_checkbox.isChecked(),
            "apply_exposure_norm": self.exposure_norm_checkbox.isChecked(),
        }
        if name == "AIA":
            params["wavelength"] = self.wavelength_combo.currentData()
        elif name == "HMI":
            params["series"] = self.series_combo.currentData()
        elif name == "IRIS":
            params["obs_type"] = "SJI"
            params["wavelength"] = self.iris_wavelength_combo.currentData()
        elif name == "SOHO":
            inst = self.soho_instrument_combo.currentData()
            params["soho_instrument"] = inst
            if inst == "EIT":
                params["wavelength"] = self.eit_wavelength_combo.currentData()
            elif inst == "LASCO":
                params["detector"] = self.lasco_detector_combo.currentData()
            else:
                params["physobs"] = self.mdi_product_combo.currentData()
        elif name == "GOES SUVI":
            params["wavelength"] = self.suvi_wavelength_combo.currentData()
            params["level"] = self.suvi_level_combo.currentData()
        elif name == "STEREO":
            params["spacecraft"] = self.stereo_sc_combo.currentData()
            params["stereo_instrument"] = self.stereo_inst_combo.currentData()
            if params["stereo_instrument"] == "EUVI":
                params["wavelength"] = self.stereo_wavelength_combo.currentData()
        return params

    def start_download(self):
        params = self.get_download_parameters()
        if self.end_datetime.dateTime() <= self.start_datetime.dateTime():
            QMessageBox.warning(self, "Time range", "The end time must be after the start time.")
            return
        if not params["use_fido"] and not params["email"]:
            QMessageBox.warning(
                self,
                "JSOC email",
                "A JSOC export needs an email address registered at JSOC\n"
                "(jsoc.stanford.edu/ajax/register_email.html).",
            )
            return
        try:
            Path(params["output_dir"]).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.critical(self, "Output folder", f"Cannot create the output folder:\n{e}")
            return
        self.download_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress_bar.show()
        self.log_text.clear()
        self.log_message("Preparing download...")
        self.download_worker = DownloadWorker(params)
        self.download_worker.progress.connect(self.update_progress)
        self.download_worker.finished.connect(self.download_finished)
        self.download_worker.error.connect(self.download_error)
        self.download_worker.start()

    def update_progress(self, message):
        self.log_message(message)

    def download_finished(self, files):
        self.download_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress_bar.hide()
        if files:
            message = f"{len(files)} files are ready in {self.output_dir.text()}"
            self.log_message(message)
            QMessageBox.information(self, "Download complete", message)
        else:
            self.log_message("No files were downloaded.")
            QMessageBox.warning(self, "Nothing downloaded", "No files were downloaded.")

    def download_error(self, error_message):
        self.download_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress_bar.hide()
        self.log_message(f"Error: {error_message}")
        QMessageBox.critical(self, "Error", f"Download failed: {error_message}")


def launch_gui(parent=None, initial_datetime=None) -> SolarDataViewerGUI:
    """Show the Solar Data Downloader window (its own application when run
    without a parent)."""
    app = QApplication.instance() or QApplication(sys.argv)
    window = SolarDataViewerGUI(parent, initial_datetime=initial_datetime)
    window.show()
    if parent is None:
        sys.exit(app.exec_())
    return window


if __name__ == "__main__":
    launch_gui()

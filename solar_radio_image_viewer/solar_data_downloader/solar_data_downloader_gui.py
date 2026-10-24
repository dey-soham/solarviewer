#!/usr/bin/env python3
"""
The Solar Data Downloader dialog: a fixed 820×600 window in two columns.
The left column holds the decisions in the order they are made (instrument,
time range, where to save, processing); the right column says what those
decisions will get, as a live estimate, and turns into progress and a log
once the download runs. The download itself and the preparation of the
files are solar_data_downloader's; this module only drives them.
"""

import os
import re
import sys
import time
from datetime import datetime, timedelta

try:
    from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QThread, QSize
    from PyQt5.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
    from PyQt5.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QDialog,
        QFileDialog,
        QFrame,
        QGraphicsOpacityEffect,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QSizePolicy,
        QStackedWidget,
        QVBoxLayout,
        QWidget,
    )
except ImportError:
    print("Error: PyQt5 is required. Please install it with:")
    print("  pip install PyQt5")
    sys.exit(1)

from . import solar_data_downloader as sdd  # noqa: F401 (the backend; the worker names its functions)
from ..ui.styles import set_hand_cursor, set_tone, theme_manager
from ..ui.widgets import kicker, kicker_row, link_button, rule, segmented

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


# ------------------------------------------------------------------ catalog
# Instruments served only through Fido (no JSOC export)
FIDO_ONLY_INSTRUMENTS = ["IRIS", "SOHO", "GOES SUVI", "STEREO"]

# Colour of each channel's dot: the usual rendering colours of the
# instruments, by wavelength in Å or LASCO detector
CHANNEL_COLORS = {
    "94": "#6fbf8a",
    "131": "#5fb6c8",
    "171": "#e0b23a",
    "193": "#c8a040",
    "195": "#c8a040",
    "211": "#c17ad9",
    "284": "#e0c060",
    "304": "#d96a5a",
    "335": "#5a86d6",
    "1600": "#9397ab",
    "1700": "#75798c",
    "4500": "#cfd3e5",
    "1330": "#d96a5a",
    "1400": "#5fb6c8",
    "2796": "#c17ad9",
    "2832": "#cfd3e5",
    "C2": "#d96a5a",
    "C3": "#5a86d6",
}

# The cadence segments: label, seconds (None = every image; the label then
# names the instrument's native cadence)
CADENCE_SEGMENTS = [(None, None), ("1 min", 60), ("5 min", 300), ("15 min", 900), ("1 h", 3600)]
CADENCE_VALUES = [seconds for _, seconds in CADENCE_SEGMENTS]


class Source:
    """One row of the instrument dropdown and what the summary needs to
    know about it."""

    def __init__(
        self,
        label,
        instrument,
        slug,
        channels=(),
        channel_param="wavelength",
        channel_name="Channel",
        native=None,
        size_mb=None,
        prepared="",
        description="",
        output="",
        params=None,
        default_cadence=None,
        process_seconds=0.5,
        unit="file",
    ):
        self.label = label  # "SDO · AIA"
        self.instrument = instrument  # the backend's instrument name
        self.slug = slug  # the folder name: "aia" (the channel is appended)
        self.channels = list(channels)  # (label, value, ion or note)
        self.channel_param = channel_param
        self.channel_name = channel_name
        self.native = native  # seconds between images: a number, a dict by channel, or None
        self.size_mb = size_mb  # per downloaded file (or sequence)
        self.prepared = prepared  # "AIA level 1.5": what preparing gives
        self.description = description
        self.output = output  # the summary's Output line
        self.params = params or {}
        self.default_cadence = default_cadence
        self.process_seconds = process_seconds
        self.unit = unit  # what is counted: a file or a sequence

    def native_seconds(self, channel):
        if isinstance(self.native, dict):
            return self.native.get(channel)
        return self.native


EUV_IONS = {
    "94": "Fe XVIII",
    "131": "Fe VIII, XXI",
    "171": "Fe IX",
    "193": "Fe XII, XXIV",
    "195": "Fe XII",
    "211": "Fe XIV",
    "284": "Fe XV",
    "304": "He II",
    "335": "Fe XVI",
    "1600": "C IV",
    "1700": "continuum",
    "4500": "continuum",
}


def _wavelengths(*values):
    return [(f"{v} Å", v, EUV_IONS.get(v, "")) for v in values]


def _stereo(craft, detector):
    label = f"STEREO-{craft} · {detector}"
    params = {"spacecraft": craft, "stereo_instrument": detector}
    if detector == "EUVI":
        return Source(
            label,
            "STEREO",
            f"euvi_{craft.lower()}",
            _wavelengths("171", "195", "284", "304"),
            native={"171": 150, "195": 300, "284": 1200, "304": 600},
            size_mb=8.4,
            prepared="north up",
            description="North up · Sun-centred · counts/s",
            output="North-up FITS, counts/s",
            params=params,
            process_seconds=1.0,
        )
    return Source(
        label,
        "STEREO",
        f"{detector.lower()}_{craft.lower()}",
        native=300 if detector == "COR1" else 900,
        size_mb=6.3,
        prepared="total brightness",
        description="Polarizer triplet → total brightness · north up · counts/s",
        output="One total-brightness FITS per sequence",
        params=params,
        unit="sequence",
    )


SOURCES = [
    Source(
        "SDO · AIA",
        "AIA",
        "aia",
        _wavelengths("94", "131", "171", "193", "211", "304", "335", "1600", "1700", "4500"),
        native={**{w: 12 for w in ("94", "131", "171", "193", "211", "304", "335")}, "1600": 24, "1700": 24, "4500": 3600},
        size_mb=12,
        prepared="AIA level 1.5",
        description="Pointing update · registration (north up, 0.6″/px, Sun-centred) · degradation correction · counts/s",
        output="Level 1.5 FITS, ready to open",
        default_cadence=300,
        process_seconds=3.0,
    ),
    Source(
        "SDO · HMI",
        "HMI",
        "hmi",
        [
            ("Magnetogram", "M_45s", "45 s"),
            ("Continuum", "Ic_45s", "45 s"),
            ("Doppler velocity", "V_45s", "45 s"),
            ("Magnetogram", "M_720s", "720 s · JSOC"),
            ("Continuum", "Ic_720s", "720 s · JSOC"),
            ("Continuum, no limb darkening", "Ic_noLimbDark_720s", "720 s · JSOC"),
            ("Doppler velocity", "V_720s", "720 s · JSOC"),
        ],
        channel_param="series",
        channel_name="Series",
        native={"M_45s": 45, "Ic_45s": 45, "V_45s": 45, "M_720s": 720, "Ic_720s": 720, "Ic_noLimbDark_720s": 720, "V_720s": 720},
        size_mb=16,
        prepared="north up",
        description="North up · Sun-centred · native 0.504″/px",
        output="North-up FITS, ready to open",
        default_cadence=300,
        process_seconds=3.0,
    ),
    Source(
        "IRIS · SJI",
        "IRIS",
        "iris_sji",
        [("1330 Å", "1330", "C II"), ("1400 Å", "1400", "Si IV"), ("2796 Å", "2796", "Mg II k"), ("2832 Å", "2832", "Mg II wing"), ("All channels", None, "")],
        size_mb=None,
        prepared="one file per exposure",
        description="One file per exposure · its own time and pointing · counts/s · north up",
        output="One FITS per exposure",
    ),
    Source(
        "SOHO · EIT",
        "SOHO",
        "eit",
        _wavelengths("171", "195", "284", "304"),
        native=720,
        size_mb=2.1,
        prepared="level 1",
        description="Level 1 where SDAC has it (dark, flat, degrid, degradation, exposure) · north up",
        output="Level 1 FITS, ready to open",
        params={"soho_instrument": "EIT"},
    ),
    Source(
        "SOHO · LASCO",
        "SOHO",
        "lasco",
        [("C2", "C2", "2–6 R☉"), ("C3", "C3", "3.7–30 R☉")],
        channel_param="detector",
        channel_name="Detector",
        native={"C2": 720, "C3": 1200},
        size_mb=2.1,
        prepared="level 1",
        description="Calibrated to mean solar brightness (NRL level 1) · distortion removed · north up · F corona background in a second HDU",
        output="Level 1 FITS, mean solar brightness, with background",
        params={"soho_instrument": "LASCO"},
        process_seconds=3.0,
    ),
    Source(
        "SOHO · MDI",
        "SOHO",
        "mdi",
        [("Magnetogram", "magnetogram", "96 min · level 1.8"), ("Continuum", "continuum", "")],
        channel_param="physobs",
        channel_name="Product",
        native=5760,
        size_mb=2.1,
        prepared="north up",
        description="North up · Sun-centred · MDI ended in 2011",
        output="North-up FITS, ready to open",
        params={"soho_instrument": "MDI"},
    ),
    Source(
        "GOES · SUVI",
        "GOES SUVI",
        "suvi",
        _wavelengths("94", "131", "171", "195", "284", "304"),
        native=240,
        size_mb=6.3,
        prepared="level 2 composites",
        description="Image into the primary HDU · level 1b exposures turned north up",
        output="FITS, ready to open",
    ),
    _stereo("A", "EUVI"),
    _stereo("A", "COR1"),
    _stereo("A", "COR2"),
    _stereo("B", "EUVI"),
    _stereo("B", "COR1"),
    _stereo("B", "COR2"),
]
SUVI_NATIVE = {"2": 240, "1b": 20}
THROUGHPUT_MB_S = {"vso": 1.5, "jsoc": 4.0}
JSOC_STAGING_S = 60
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


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


# ------------------------------------------------------------------ helpers
def _dot_icon(color, size=10):
    """A round dot of ``color`` as an icon (a channel's colour)."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(0, 0, size, size)
    painter.end()
    return QIcon(pixmap)


def _monospace(widget):
    font = QFont(widget.font())
    font.setFamily("monospace")
    font.setStyleHint(QFont.Monospace)
    widget.setFont(font)
    return widget


def _label(text="", role=None, mono=False):
    label = QLabel(text)
    if role:
        label.setObjectName(role)
    if mono:
        _monospace(label)
    return label


def _segments(labels):
    """Checkable buttons for ``labels`` in one segmented group on a filled
    track (the header look). Returns (group, buttons)."""
    buttons = []
    for text in labels:
        button = QPushButton(text)
        button.setCheckable(True)
        button.setAutoDefault(False)
        buttons.append(button)
    return segmented(buttons, role="HeaderSegmented"), buttons


def _switch(checked=True):
    switch = QCheckBox()
    switch.setObjectName("Switch")
    switch.setChecked(checked)
    return switch


def _size_text(mb):
    if mb is None:
        return "size varies"
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    return f"{mb:.0f} MB"


def _duration_text(seconds):
    if seconds < 90:
        return f"{max(10, int(round(seconds / 10.0)) * 10)} s"
    minutes = seconds / 60.0
    if minutes < 90:
        return f"{int(round(minutes))} min"
    return f"{minutes / 60.0:.1f} h"


def _cadence_text(seconds):
    if seconds is None:
        return "every image"
    if seconds < 60:
        return f"{int(seconds)} s"
    if seconds < 3600:
        m = seconds / 60.0
        return f"{int(m)} min" if m == int(m) else f"{m:.1f} min"
    h = seconds / 3600.0
    return f"{int(h)} h" if h == int(h) else f"{h:.1f} h"


def _short_home(path):
    home = os.path.expanduser("~")
    if path == home or path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def _escape(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _muted(text, color):
    return f"<span style='color:{color}'>{_escape(text)}</span>"


class Timeline(QWidget):
    """One pill per expected file across the window; a solid bar when there
    are too many to tell apart."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.count = 0
        self.setFixedHeight(12)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_count(self, count):
        self.count = count or 0
        self.update()

    def paintEvent(self, event):
        if not self.count:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme_manager.palette["highlight_deep"]))
        width, y, h, gap = self.width(), 3, 6, 3
        if self.count * (gap + 4) > width:  # too many to tell apart: one bar
            painter.drawRoundedRect(0, y, width, h, 3, 3)
        else:
            pill = (width - gap * (self.count - 1)) / float(self.count)
            for i in range(self.count):
                x = int(round(i * (pill + gap)))
                painter.drawRoundedRect(x, y, int(round(pill)), h, 3, 3)
        painter.end()


# ------------------------------------------------------------------- dialog
class SolarDataViewerGUI(QDialog):
    """The Solar Data Downloader window."""

    def __init__(self, parent=None, initial_datetime=None):
        super().__init__(parent)
        self.setWindowTitle("Download solar data")
        self.setWindowFlags((self.windowFlags() & ~Qt.WindowContextHelpButtonHint) | Qt.WindowMinimizeButtonHint)
        self.setFixedSize(820, 600)
        self.setModal(False)
        self.viewer = parent if hasattr(parent, "open_path") else None
        self.initial_datetime = initial_datetime
        self.download_worker = None
        self.files = []
        self._started = None
        self._log_file = None
        self._fetched = self._processed = self._total = 0
        self._log_lines = []
        self._estimate = {}
        self._updating = False
        self._window_seconds = 3600
        self.base_dir = os.path.join(os.path.expanduser("~"), "solar_data")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_form(), 1)
        body.addWidget(self._build_side(), 0)
        outer.addLayout(body, 1)
        outer.addWidget(self._build_footer(), 0)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

        self.on_instrument_changed(0)
        try:
            set_hand_cursor(self)
        except Exception:
            pass

    # ------------------------------------------------------------ the form
    def _build_form(self):
        self.form = QWidget()
        layout = QVBoxLayout(self.form)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(20)

        # Instrument
        block = QVBoxLayout()
        block.setSpacing(10)
        block.addWidget(kicker("Instrument"))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.instrument_combo = QComboBox()
        for source in SOURCES:
            self.instrument_combo.addItem(source.label)
        self.instrument_combo.setMinimumHeight(30)
        self.instrument_combo.currentIndexChanged.connect(self.on_instrument_changed)
        row.addWidget(self.instrument_combo, 14)
        self.channel_combo = QComboBox()
        self.channel_combo.setIconSize(QSize(10, 10))
        self.channel_combo.setMinimumHeight(30)
        self.channel_combo.currentIndexChanged.connect(self.on_channel_changed)
        row.addWidget(self.channel_combo, 10)
        self.level_group, self.level_buttons = _segments(["L2", "L1b"])
        self.level_buttons[0].setChecked(True)
        self.level_buttons[0].setToolTip("Level 2: 4 min composites, calibrated")
        self.level_buttons[1].setToolTip("Level 1b: single exposures")
        for button in self.level_buttons:
            button.clicked.connect(lambda _=False, b=button: self._pick(self.level_buttons, b, self.on_channel_changed))
        row.addWidget(self.level_group, 0)
        block.addLayout(row)
        layout.addLayout(block)

        # Time range
        block = QVBoxLayout()
        block.setSpacing(10)
        head, _ = kicker_row("Time range", "UTC")
        block.addWidget(head)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(3)
        self.start_edit, self.end_edit = QLineEdit(), QLineEdit()
        for col, (name, edit) in enumerate((("Start", self.start_edit), ("End", self.end_edit))):
            grid.addWidget(_label(name, "Meta"), 0, col)
            _monospace(edit)
            edit.setPlaceholderText("YYYY-MM-DD HH:MM:SS")
            edit.setMinimumHeight(30)
            grid.addWidget(edit, 1, col)
        start = self.initial_datetime or datetime.utcnow().replace(microsecond=0)
        self.start_edit.setText(start.strftime(TIME_FORMAT))
        self.end_edit.setText((start + timedelta(hours=1)).strftime(TIME_FORMAT))
        self.start_edit.textEdited.connect(self._start_edited)
        self.end_edit.textEdited.connect(self._end_edited)
        block.addLayout(grid)
        row = QHBoxLayout()
        row.setSpacing(8)
        label = _label("Cadence", "Hint")
        label.setFixedWidth(60)
        row.addWidget(label)
        self.cadence_group, self.cadence_buttons = _segments([text or "All" for text, _ in CADENCE_SEGMENTS])
        for button in self.cadence_buttons:
            button.clicked.connect(lambda _=False, b=button: self._pick(self.cadence_buttons, b, self.update_summary))
        row.addWidget(self.cadence_group)
        row.addStretch()
        block.addLayout(row)
        layout.addLayout(block)

        # Save to
        block = QVBoxLayout()
        block.setSpacing(10)
        block.addWidget(kicker("Save to"))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.folder_field = QFrame()
        self.folder_field.setObjectName("Field")
        self.folder_field.setMinimumHeight(30)
        field_layout = QHBoxLayout(self.folder_field)
        field_layout.setContentsMargins(10, 0, 10, 0)
        self.folder_label = QLabel()
        self.folder_label.setTextFormat(Qt.RichText)
        field_layout.addWidget(self.folder_label)
        row.addWidget(self.folder_field, 1)
        browse = QPushButton("Browse…")
        browse.setAutoDefault(False)
        browse.clicked.connect(self.browse_output_dir)
        row.addWidget(browse)
        block.addLayout(row)
        row = QHBoxLayout()
        row.setSpacing(8)
        label = _label("Source", "Hint")
        label.setFixedWidth(60)
        row.addWidget(label)
        self.source_group, self.source_buttons = _segments(["VSO", "JSOC export"])
        self.source_buttons[0].setChecked(True)
        self.source_buttons[1].setToolTip(
            "Export from JSOC with an email address registered there: the only source\n"
            "of the HMI 720 s series, and the alternative when the VSO's SDO export is down"
        )
        for button in self.source_buttons:
            button.clicked.connect(lambda _=False, b=button: self._pick(self.source_buttons, b, self.on_source_changed))
        row.addWidget(self.source_group)
        self.source_hint = _label("No account needed", "Meta")
        row.addWidget(self.source_hint)
        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("email registered at JSOC")
        self.email_input.textChanged.connect(self.update_summary)
        self.email_input.hide()
        row.addWidget(self.email_input, 1)
        row.addStretch()
        block.addLayout(row)
        layout.addLayout(block)

        # Processing
        block = QVBoxLayout()
        block.setSpacing(8)
        block.addWidget(kicker("Processing"))
        self.processing_row = QFrame()
        self.processing_row.setObjectName("Surface")
        row = QHBoxLayout(self.processing_row)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(8)
        self.calibrate_checkbox = _switch(True)
        self.calibrate_checkbox.toggled.connect(self.on_calibration_changed)
        row.addWidget(self.calibrate_checkbox, 0, Qt.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(1)
        self.processing_title = QLabel()
        self.processing_title.setTextFormat(Qt.RichText)
        self.processing_desc = _label("", "Meta")
        self.processing_desc.setWordWrap(True)
        self.processing_desc.setMinimumHeight(2 * self.processing_desc.fontMetrics().lineSpacing())
        text.addWidget(self.processing_title)
        text.addWidget(self.processing_desc)
        row.addLayout(text, 1)
        self.options_link = link_button("Options ▾", self.toggle_options)
        row.addWidget(self.options_link, 0, Qt.AlignTop)
        block.addWidget(self.processing_row)
        self.options_row = QWidget()
        options = QGridLayout(self.options_row)
        options.setContentsMargins(10, 0, 10, 0)
        options.setHorizontalSpacing(14)
        options.setVerticalSpacing(6)
        self.psf_checkbox = QCheckBox("PSF deconvolution")
        self.psf_checkbox.setToolTip("Sharpen with the instrument point spread function. Slow: about a minute per image.")
        self.degradation_checkbox = QCheckBox("Degradation correction")
        self.degradation_checkbox.setChecked(True)
        self.exposure_norm_checkbox = QCheckBox("Normalise to counts/s")
        self.exposure_norm_checkbox.setChecked(True)
        self.keep_originals_checkbox = QCheckBox("Keep downloaded files")
        self.keep_originals_checkbox.setToolTip("Leave the files as the archive sent them next to the prepared ones")
        boxes = (self.psf_checkbox, self.degradation_checkbox, self.exposure_norm_checkbox, self.keep_originals_checkbox)
        for i, box in enumerate(boxes):
            box.setObjectName("SmallCheck")
            box.toggled.connect(self.update_summary)
            options.addWidget(box, i // 2, i % 2)
        options.setColumnStretch(2, 1)
        self.options_row.hide()
        block.addWidget(self.options_row)
        layout.addLayout(block)
        layout.addStretch()
        return self.form

    # ------------------------------------------------------ the side column
    def _build_side(self):
        side = QFrame()
        side.setObjectName("SidePanel")
        side.setFixedWidth(300)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(0, 0, 0, 0)
        self.side_pages = QStackedWidget()
        self.side_pages.addWidget(self._build_summary())
        self.side_pages.addWidget(self._build_progress())
        layout.addWidget(self.side_pages)
        return side

    def _build_summary(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(16)
        layout.addWidget(kicker("You'll get"))
        head = QVBoxLayout()
        head.setSpacing(2)
        self.count_label = _label("", "DialogTitle")
        self.size_label = _label("", "Hint")
        self.size_label.setWordWrap(True)
        head.addWidget(self.count_label)
        head.addWidget(self.size_label)
        layout.addLayout(head)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        self.summary_values = {}
        for i, key in enumerate(("Instrument", "Window", "Cadence", "Output")):
            grid.addWidget(_label(key, "Hint"), i, 0, Qt.AlignTop)
            value = _label("", mono=(key == "Window"))
            value.setTextFormat(Qt.RichText)
            value.setWordWrap(True)
            self.summary_values[key] = value
            grid.addWidget(value, i, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        layout.addWidget(rule())
        timeline = QVBoxLayout()
        timeline.setSpacing(6)
        timeline.addWidget(_label("Timeline · one dot per file", "Meta"))
        self.timeline = Timeline()
        timeline.addWidget(self.timeline)
        ends = QHBoxLayout()
        self.timeline_start = _label("", "Meta", mono=True)
        self.timeline_end = _label("", "Meta", mono=True)
        ends.addWidget(self.timeline_start)
        ends.addStretch()
        ends.addWidget(self.timeline_end)
        timeline.addLayout(ends)
        layout.addLayout(timeline)
        layout.addStretch()
        self.native_note = _label("", "Meta")
        self.native_note.setWordWrap(True)
        self.native_note.setTextFormat(Qt.RichText)
        self.native_note.linkActivated.connect(lambda _: self.use_native_cadence())
        layout.addWidget(self.native_note)
        return page

    def _build_progress(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)
        head, self.elapsed_label = kicker_row("Downloading", "0:00 elapsed")
        self.progress_kicker = head.findChild(QLabel, "Kicker")
        _monospace(self.elapsed_label)
        layout.addWidget(head)
        block = QVBoxLayout()
        block.setSpacing(6)
        numbers = QHBoxLayout()
        numbers.setSpacing(8)
        self.progress_count = _label("", "DialogTitle")
        self.progress_count.setTextFormat(Qt.RichText)
        self.progress_left = _label("", "Hint")
        numbers.addWidget(self.progress_count)
        numbers.addWidget(self.progress_left, 0, Qt.AlignBottom)
        numbers.addStretch()
        block.addLayout(numbers)
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("ThinProgress")
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 100)
        block.addWidget(self.progress_bar)
        layout.addLayout(block)
        tiles = QHBoxLayout()
        tiles.setSpacing(6)
        self.tile_values = {}
        for name in ("Fetched", "Processed"):
            tile = QFrame()
            tile.setObjectName("Tile")
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(9, 7, 9, 7)
            tile_layout.setSpacing(1)
            tile_layout.addWidget(_label(name, "Meta"))
            value = _label("—", "Mono")
            tile_layout.addWidget(value)
            self.tile_values[name] = value
            tiles.addWidget(tile, 1)
        layout.addLayout(tiles)
        layout.addWidget(rule())
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("QuietLog")
        self.log_view.setReadOnly(True)
        self.log_view.setFrameShape(QFrame.NoFrame)
        self.log_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.log_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(self.log_view, 1)
        self.log_path_label = _label("", "Meta")
        self.log_path_label.setTextFormat(Qt.RichText)
        self.log_path_label.setWordWrap(True)
        layout.addWidget(self.log_path_label)
        return page

    def _build_footer(self):
        footer = QFrame()
        footer.setObjectName("CanvasBar")
        footer.setFixedHeight(52)
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(8)
        self.open_label = _label("Opens in SolarViewer when done", "Meta")
        self.open_switch = _switch(True)
        layout.addWidget(self.open_label)
        layout.addWidget(self.open_switch)
        if self.viewer is None:
            self.open_label.hide()
            self.open_switch.hide()
            self.open_switch.setChecked(False)
        layout.addStretch()
        self.background_button = QPushButton("Run in background")
        self.background_button.setObjectName("GhostButton")
        self.background_button.setAutoDefault(False)
        self.background_button.setToolTip("Hide this window; it comes back when the download is done")
        self.background_button.clicked.connect(self.hide)
        self.background_button.hide()
        layout.addWidget(self.background_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("GhostButton")
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.close)
        layout.addWidget(self.cancel_button)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setAutoDefault(False)
        self.stop_button.clicked.connect(self.cancel_download)
        self.stop_button.hide()
        layout.addWidget(self.stop_button)
        self.open_button = QPushButton("Open in SolarViewer")
        self.open_button.setObjectName("PrimaryButton")
        self.open_button.setAutoDefault(False)
        self.open_button.clicked.connect(self.open_in_viewer)
        self.open_button.hide()
        layout.addWidget(self.open_button)
        self.download_button = QPushButton("Download")
        self.download_button.setObjectName("PrimaryButton")
        self.download_button.setDefault(True)
        self.download_button.clicked.connect(self.start_download)
        layout.addWidget(self.download_button)
        return footer

    # ------------------------------------------------------------- state
    @property
    def source(self):
        return SOURCES[self.instrument_combo.currentIndex()]

    def channel(self):
        """The chosen channel's value (wavelength, series, detector...)."""
        return self.channel_combo.currentData() if self.channel_combo.count() else None

    def cadence_seconds(self):
        for button, seconds in zip(self.cadence_buttons, CADENCE_VALUES):
            if button.isChecked():
                return seconds
        return None

    def suvi_level(self):
        return "2" if self.level_buttons[0].isChecked() else "1b"

    def native_seconds(self):
        if self.source.instrument == "GOES SUVI":
            return SUVI_NATIVE[self.suvi_level()]
        return self.source.native_seconds(self.channel())

    def use_jsoc(self):
        return self.source_buttons[1].isChecked() and self.source.instrument not in FIDO_ONLY_INSTRUMENTS

    def times(self):
        """(start, end) as datetimes, None where a field does not parse."""
        out = []
        for edit in (self.start_edit, self.end_edit):
            try:
                out.append(datetime.strptime(edit.text().strip(), TIME_FORMAT))
            except ValueError:
                out.append(None)
        return tuple(out)

    def output_dir(self):
        """The folder for this download: base / date / instrument_channel."""
        start, _ = self.times()
        date = (start or datetime.utcnow()).strftime("%Y-%m-%d")
        channel = self.channel()
        slug = self.source.slug + (f"_{channel}".lower() if channel else "")
        if self.source.instrument == "GOES SUVI":
            slug += f"_l{self.suvi_level()}"
        return os.path.join(self.base_dir, date, slug)

    @staticmethod
    def _pick(buttons, chosen, then=None):
        for button in buttons:
            button.setChecked(button is chosen)
        if then is not None:
            then()

    # ------------------------------------------------------------- events
    def on_instrument_changed(self, index):
        source = self.source
        self._updating = True
        self.channel_combo.clear()
        for label, value, note in source.channels:
            text = f"{label} · {note}" if note else label
            color = CHANNEL_COLORS.get(str(value))
            if color:
                self.channel_combo.addItem(_dot_icon(color), text, value)
            else:
                self.channel_combo.addItem(text, value)
        self.channel_combo.setVisible(bool(source.channels))
        self.channel_combo.setToolTip(source.channel_name)
        if source.instrument == "AIA":
            self.channel_combo.setCurrentIndex(2)  # 171 Å
        elif source.slug == "eit":
            self.channel_combo.setCurrentIndex(1)  # 195 Å
        self.level_group.setVisible(source.instrument == "GOES SUVI")
        fido_only = source.instrument in FIDO_ONLY_INSTRUMENTS
        self.source_group.setVisible(not fido_only)
        if fido_only:
            self._pick(self.source_buttons, self.source_buttons[0])
        index = CADENCE_VALUES.index(source.default_cadence) if source.default_cadence in CADENCE_VALUES else 0
        self._pick(self.cadence_buttons, self.cadence_buttons[index])
        self._updating = False
        self.on_channel_changed()

    def on_channel_changed(self, index=None):
        if self._updating:
            return
        source = self.source
        # the 720 s HMI series exist only at JSOC
        jsoc_only = source.instrument == "HMI" and "720" in str(self.channel())
        self.source_buttons[0].setEnabled(not jsoc_only)
        if jsoc_only:
            self._pick(self.source_buttons, self.source_buttons[1])
        # cadence segments finer than the native cadence change nothing
        native = self.native_seconds()
        self.cadence_buttons[0].setText(_cadence_text(native) if native else "All")
        self.cadence_buttons[0].setToolTip("Every image" + (f", one every {_cadence_text(native)}" if native else ""))
        for button, seconds in zip(self.cadence_buttons[1:], CADENCE_VALUES[1:]):
            button.setEnabled(not native or seconds > native)
        chosen = [b for b in self.cadence_buttons if b.isChecked()]
        if not chosen or not chosen[0].isEnabled():
            self._pick(self.cadence_buttons, self.cadence_buttons[0])
        self.on_source_changed()

    def on_source_changed(self):
        jsoc = self.use_jsoc()
        fido_only = self.source.instrument in FIDO_ONLY_INSTRUMENTS
        self.source_hint.setText("VSO · no account needed" if fido_only else "No account needed")
        self.source_hint.setVisible(not jsoc)
        self.email_input.setVisible(jsoc)
        self.update_summary()

    def on_calibration_changed(self, on):
        self.options_row.setVisible(on and self.options_link.text().endswith("▴"))
        for widget in (self.options_link, self.processing_desc, self.processing_title):
            widget.setEnabled(on)
        self.update_summary()

    def toggle_options(self):
        shown = not self.options_row.isVisibleTo(self)
        self.options_row.setVisible(shown)
        self.options_link.setText("Options ▴" if shown else "Options ▾")

    def _start_edited(self, text):
        start, _ = self.times()
        if start is not None:  # the end keeps its distance
            self.end_edit.setText((start + timedelta(seconds=self._window_seconds)).strftime(TIME_FORMAT))
        self.update_summary()

    def _end_edited(self, text):
        start, end = self.times()
        if start is not None and end is not None and end > start:
            self._window_seconds = (end - start).total_seconds()
        self.update_summary()

    def use_native_cadence(self):
        self._pick(self.cadence_buttons, self.cadence_buttons[0], self.update_summary)

    def browse_output_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Save downloads under", self.base_dir, QFileDialog.ShowDirsOnly)
        if dir_path:
            self.base_dir = dir_path
            self.update_summary()

    # ------------------------------------------------------------ summary
    def estimate(self):
        """What the form asks for: count, size and time, None where it
        cannot be known (IRIS: one cube per observation)."""
        source = self.source
        start, end = self.times()
        window = (end - start).total_seconds() if start and end else None
        native = self.native_seconds()
        cadence = self.cadence_seconds()
        step = max(cadence or 0, native or 0)
        count = size = seconds = None
        if window is not None and window > 0 and step and source.size_mb is not None:
            count = int(window // step) + 1
            size = count * source.size_mb
            rate = THROUGHPUT_MB_S["jsoc" if self.use_jsoc() else "vso"]
            seconds = size / rate + (JSOC_STAGING_S if self.use_jsoc() else 0)
            if self.calibrate_checkbox.isChecked():
                per_file = source.process_seconds
                if source.instrument == "AIA" and self.psf_checkbox.isChecked():
                    per_file *= 20
                seconds += count * per_file / 2.0  # two at a time
        native_count = native_size = None
        if count is not None and native and cadence and cadence > native:
            native_count = int(window // native) + 1
            native_size = native_count * source.size_mb
        return dict(
            window=window, count=count, size_mb=size, seconds=seconds, native=native,
            native_count=native_count, native_size_mb=native_size, start=start, end=end,
        )

    def update_summary(self, *args):
        source = self.source
        palette = theme_manager.palette
        secondary, muted, link = palette["text_secondary"], palette["disabled"], palette["link"]
        est = self._estimate = self.estimate()
        start, end = est["start"], est["end"]
        bad_window = start is None or end is None or end <= start
        set_tone(self.start_edit, None if start is not None else "error")
        set_tone(self.end_edit, None if (end is not None and not bad_window) else "error")

        # the folder
        self.folder_label.setText(
            _escape(_short_home(self.base_dir)) + _muted(self.output_dir()[len(self.base_dir):], muted)
        )

        # the processing row
        self.processing_title.setText("Prepare for SolarViewer " + _muted(f"· {source.prepared}", secondary))
        self.processing_desc.setText(source.description)
        is_aia = source.instrument == "AIA"
        for box in (self.psf_checkbox, self.degradation_checkbox, self.exposure_norm_checkbox):
            box.setVisible(is_aia)

        # the big number
        count = est["count"]
        unit = source.unit
        set_tone(self.size_label, None)
        if source.size_mb is None:
            self.count_label.setText("IRIS cubes")
            self.size_label.setText("one per observation in the window · size varies")
        elif count is None:
            self.count_label.setText("—")
            self.size_label.setText("Set a start time and a later end time")
        else:
            self.count_label.setText(f"{count} {unit}{'s' if count != 1 else ''}")
            where = "JSOC" if self.use_jsoc() else "VSO"
            self.size_label.setText(f"≈ {_size_text(est['size_mb'])} · about {_duration_text(est['seconds'])} on {where}")

        # the key/value list
        channel_text = ""
        if source.channels and self.channel_combo.currentIndex() >= 0:
            channel_text = self.channel_combo.currentText().split(" · ")[0]
            if channel_text == "All channels":
                channel_text = "all channels"
        self.summary_values["Instrument"].setText(_escape(f"{source.label.split(' · ')[-1]} {channel_text}".strip()))
        if start is not None and end is not None:
            if start.date() == end.date():
                window_text = f"{start:%H:%M} → {end:%H:%M} UT"
            else:
                window_text = f"{start:%m-%d %H:%M} → {end:%m-%d %H:%M} UT"
        else:
            window_text = "—"
        self.summary_values["Window"].setText(_escape(window_text))
        cadence, native = self.cadence_seconds(), est["native"]
        cadence_text = _cadence_text(cadence) if cadence else ("every image" if not native else _cadence_text(native))
        extra = " " + _muted(f"(native {_cadence_text(native)})", secondary) if native and cadence else ""
        self.summary_values["Cadence"].setText(_escape(cadence_text) + extra)
        output = source.output if self.calibrate_checkbox.isChecked() else "Files as the archive sends them"
        self.summary_values["Output"].setText(_escape(output))

        # the timeline
        self.timeline.set_count(count or 0)
        self.timeline_start.setText(f"{start:%H:%M}" if start else "")
        self.timeline_end.setText(f"{end:%H:%M}" if end else "")

        # the native-cadence note
        if est["native_count"]:
            span = "this hour" if abs(est["window"] - 3600) < 1 else "this window"
            self.native_note.setText(
                f"At native {_escape(_cadence_text(native))} {span} would be ~{est['native_count']} files / "
                f"{_escape(_size_text(est['native_size_mb']))}. "
                f"<a href='native' style='color:{link};text-decoration:none'>Use {_escape(_cadence_text(native))}</a>"
            )
            self.native_note.show()
        else:
            self.native_note.hide()

        # the download button
        self.download_button.setText(f"Download {count} {unit}{'s' if count != 1 else ''}" if count else "Download")
        reason = None
        if bad_window:
            reason = "Set a start time and a later end time (YYYY-MM-DD HH:MM:SS)"
        elif self.use_jsoc() and not self.email_input.text().strip():
            reason = "A JSOC export needs an email address registered at JSOC"
        else:
            probe = self.base_dir
            while probe and not os.path.exists(probe):
                probe = os.path.dirname(probe)
            if not probe or not os.access(probe, os.W_OK):
                reason = f"Cannot write to {_short_home(self.base_dir)}"
        self.download_button.setEnabled(reason is None)
        self.download_button.setToolTip(reason or "")

    # ----------------------------------------------------------- download
    def get_download_parameters(self) -> dict:
        """What the download needs, from the form."""
        source = self.source
        start, end = self.times()
        use_fido = not self.use_jsoc()
        params = {
            "instrument": source.instrument,
            "start_time": start.strftime("%Y.%m.%d %H:%M:%S"),
            "end_time": end.strftime("%Y.%m.%d %H:%M:%S"),
            "output_dir": self.output_dir(),
            "use_fido": use_fido,
            "email": self.email_input.text().strip() if not use_fido else None,
            "sample": self.cadence_seconds(),
            "skip_calibration": not self.calibrate_checkbox.isChecked(),
            "keep_originals": self.keep_originals_checkbox.isChecked(),
            "apply_psf": self.psf_checkbox.isChecked(),
            "apply_degradation": self.degradation_checkbox.isChecked(),
            "apply_exposure_norm": self.exposure_norm_checkbox.isChecked(),
        }
        params.update(source.params)
        if source.channels:
            params[source.channel_param] = self.channel()
        if source.instrument == "GOES SUVI":
            params["level"] = self.suvi_level()
        return params

    def start_download(self):
        self.update_summary()
        if not self.download_button.isEnabled():
            return
        params = self.get_download_parameters()
        try:
            os.makedirs(params["output_dir"], exist_ok=True)
        except OSError as e:
            set_tone(self.size_label, "error")
            self.size_label.setText(f"Cannot create the folder: {e}")
            return
        self.files = []
        self._fetched = self._processed = 0
        self._total = self._estimate.get("count") or 0
        self._started = time.time()
        self._log_lines = []
        self.log_view.clear()
        log_path = os.path.join(params["output_dir"], "download.log")
        try:
            self._log_file = open(log_path, "a", encoding="utf-8")
            self._log_file.write(f"\n# {datetime.utcnow():%Y-%m-%d %H:%M:%S} UT  {params}\n")
        except OSError:
            self._log_file = None
        self.log_path_label.setText("Full log → " + _muted(_short_home(log_path), theme_manager.palette["link"]))
        self._show_progress("Downloading")
        self._set_form_locked(True)
        self._tick()
        self._timer.start()
        self.download_worker = DownloadWorker(params)
        self.download_worker.progress.connect(self.update_progress)
        self.download_worker.finished.connect(self.download_finished)
        self.download_worker.error.connect(self.download_error)
        self.download_worker.start()

    def _show_progress(self, kicker_text):
        self.progress_kicker.setText(kicker_text.upper())
        self.side_pages.setCurrentIndex(1)
        self.cancel_button.hide()
        self.download_button.hide()
        self.open_button.hide()
        self.background_button.show()
        self.stop_button.show()
        self._render_progress()

    def _set_form_locked(self, locked):
        self.form.setEnabled(not locked)
        if locked:
            effect = QGraphicsOpacityEffect(self.form)
            effect.setOpacity(0.45)
            self.form.setGraphicsEffect(effect)
        else:
            self.form.setGraphicsEffect(None)

    def _tick(self):
        if self._started is None:
            return
        elapsed = int(time.time() - self._started)
        self.elapsed_label.setText(f"{elapsed // 60}:{elapsed % 60:02d} elapsed")

    def _render_progress(self, done=False):
        secondary = theme_manager.palette["text_secondary"]
        total = max(self._total, self._fetched, self._processed)
        preparing = self.calibrate_checkbox.isChecked()
        if done:
            n = len(self.files)
            self.progress_count.setText(f"{n}" + _muted(f" / {n} · done", secondary))
            self.progress_left.setText("")
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(100)
        else:
            self.progress_count.setText(f"{self._fetched}" + _muted(f" / {total or '?'}", secondary))
            if total and self._started and self._fetched:
                per_file = (time.time() - self._started) / float(self._fetched)
                left = per_file * (total - self._fetched) + (per_file * (total - self._processed) * 0.3 if preparing else 0)
                self.progress_left.setText(f"≈ {_duration_text(left)} left" if left > 5 else "almost done")
            else:
                self.progress_left.setText("")
            if total:
                steps = self._fetched + (self._processed if preparing else 0)
                self.progress_bar.setRange(0, 100)
                self.progress_bar.setValue(int(100 * steps / (2.0 * total if preparing else total)))
            else:
                self.progress_bar.setRange(0, 0)  # busy
        size_mb = self._fetched * (self.source.size_mb or 0)
        self.tile_values["Fetched"].setText(f"{self._fetched}" + (f" · {_size_text(size_mb)}" if size_mb else ""))
        short = {"AIA level 1.5": "L1.5", "level 1": "L1", "level 2 composites": "L2"}.get(self.source.prepared, "prepped")
        self.tile_values["Processed"].setText(f"{self._processed} · {short}" if preparing else "—")

    def update_progress(self, message):
        """A status line from the download: read for its figures, then
        logged, newest on top."""
        if self._log_file is not None:
            try:
                self._log_file.write(message + "\n")
                self._log_file.flush()
            except OSError:
                pass
        m = re.match(r"Found (\d+) files", message)
        if m:
            self._total = int(m.group(1))
        m = re.match(r"Downloading: (\d+)/(\d+) files", message)
        if m:  # the progress bars are figures, not log lines
            self._fetched, self._total = int(m.group(1)), int(m.group(2))
            self._render_progress()
            return
        if re.match(r"Downloading\.\.\. \d+%", message):
            return
        m = re.match(r"Successfully downloaded (\d+) files", message)
        if m:
            self._fetched = int(m.group(1))
        status = ""
        m = re.match(r"\[(\d+)/(\d+)\] Processed (.+?) -> (.+)", message)
        if m:
            self._processed = int(m.group(1))
            message, status = m.group(4).split(", ")[0], "✓"
        m = re.match(r"\[(\d+)/(\d+)\] Warning: could not process (.+?):", message)
        if m:
            message, status = m.group(3), "not processed"
        self._render_progress()
        self._log(message, status)

    def _log(self, message, status=""):
        """Add a line on top of the log: the time, the message and a short
        status at the right."""
        self._log_lines.insert(0, (datetime.utcnow().strftime("%H:%M"), message, status))
        self._render_log()

    def _render_log(self):
        """The log lines, each elided in the middle to the column's width."""
        metrics = self.log_view.fontMetrics()
        total = self.log_view.viewport().width() - 4
        lines = []
        for stamp, message, status in self._log_lines:
            tail = f"  {status}" if status else ""
            width = total - metrics.horizontalAdvance(f"{stamp}  {tail}")
            lines.append(f"{stamp}  {metrics.elidedText(message, Qt.ElideMiddle, max(40, width))}{tail}")
        self.log_view.setPlainText("\n".join(lines))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._log_lines:
            self._render_log()

    def showEvent(self, event):
        super().showEvent(event)
        if self._log_lines:
            QTimer.singleShot(0, self._render_log)

    def cancel_download(self):
        if self.download_worker is not None and self.download_worker.isRunning():
            self.download_worker.cancel()
            self.update_progress("Stopping...")

    def _finish(self, kicker_text):
        self._timer.stop()
        self._tick()
        self._started = None
        if self._log_file is not None:
            try:
                self._log_file.close()
            except OSError:
                pass
            self._log_file = None
        self._set_form_locked(False)
        self.progress_kicker.setText(kicker_text.upper())
        self.background_button.hide()
        self.stop_button.hide()
        self.cancel_button.setText("Close")
        self.cancel_button.show()
        self.download_button.setText("Download again")
        self.download_button.show()
        if not self.isVisible():  # run in the background: come back
            self.show()
            self.raise_()

    def download_finished(self, files):
        self.files = list(files)
        self._fetched = max(self._fetched, len(files))
        if self.calibrate_checkbox.isChecked():
            self._processed = max(self._processed, len(files))
        self._render_progress(done=True)
        self._finish("Done" if files else "Nothing downloaded")
        if not files:
            self.update_progress("No files were downloaded.")
        elif self.viewer is not None:
            self.open_button.show()
            if self.open_switch.isChecked():
                self.open_in_viewer()

    def download_error(self, error_message):
        self.update_progress(f"Error: {error_message}")
        self._render_progress()
        self._finish("Stopped" if "cancel" in error_message.lower() else "Failed")
        self.download_button.setText("Try again")

    def open_in_viewer(self):
        """Open the first downloaded file in the viewer that owns this
        dialog (the rest follow with its next and previous file keys)."""
        if self.viewer is None or not self.files:
            return
        try:
            self.viewer.open_path(self.files[0])
            self.viewer.raise_()
        except Exception as e:
            self.update_progress(f"Could not open in SolarViewer: {e}")

    def closeEvent(self, event):
        """Stop a running download when the window is closed."""
        if self.download_worker is not None and self.download_worker.isRunning():
            self.download_worker.cancel()
            self.download_worker.quit()
            self.download_worker.wait(2000)
        if self._log_file is not None:
            try:
                self._log_file.close()
            except OSError:
                pass
            self._log_file = None
        super().closeEvent(event)


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

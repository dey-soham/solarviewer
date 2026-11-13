#!/usr/bin/env python3
"""
Radio Data Downloader GUI: the window that downloads solar radio dynamic
spectra (RSTN spectrographs, e-CALLISTO stations, OVRO-LWA and the radio
receivers of Wind, STEREO, Parker Solar Probe and Solar Orbiter) and writes
them as FITS files for the Dynamic Spectrum Viewer.

It has the solar downloader's shape: the form on the left (source, time
range, folder, processing), on the right what the download will give,
checked with the archive as the form changes, with the bursts reported on
the day; during a download the right column shows progress and a log.

What differs from one source to the next is in sources.py: the window asks
the chosen source.
"""

import os
import sys
import threading
import time
from datetime import datetime, timedelta

try:
    from PyQt5.QtCore import QObject, QSettings, QSize, Qt, QThread, QTimer, QUrl, pyqtSignal
    from PyQt5.QtGui import QDesktopServices, QIcon
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
        QProgressBar,
        QPushButton,
        QStackedWidget,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
except ImportError:
    print("Error: PyQt5 is required. Please install it with:")
    print("  pip install PyQt5")
    sys.exit(1)

from . import radio_data_downloader as rdd
from . import sources
from ..noaa_events import noaa_events as _noaa_events  # noqa: F401 (loaded here: its package imports Qt, never from a worker thread)
from ..ui.activity import activity
from ..ui.date_picker import attach_date_picker
from ..ui.download_parts import (
    LOG_LINES_SHOWN,
    CloseWithViewer,
    Spinner,
    Timeline,
    _duration_text,
    abandon,
    _escape,
    _label,
    _monospace,
    _muted,
    _segments,
    _short_home,
    _size_text,
    _switch,
    render_log,
)
from ..ui.geometry import fit_to_screen
from ..ui.icons import themed_icon
from ..ui.styles import SPACE_L, SPACE_M, SPACE_S, SPACE_XS, set_hand_cursor, set_tone, theme_manager, themed
from ..ui.widgets import kicker, kicker_row, link_button, rule

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
DAY_FORMAT = "%Y-%m-%d"
COUNT_DELAY_MS = 700  # the form settles this long before the archive is asked
BURST_MARGIN = timedelta(minutes=5)  # around a burst picked from the list
BURSTS_SHOWN = 8  # before "more"
THROUGHPUT_MB_S = 2.0  # assumed, for the time estimate
SETTINGS_GROUP = "RadioDownloader"

BURST_TYPES = {
    "I": "Type I: short narrow-band bursts (noise storm)",
    "II": "Type II: slow drift, a shock wave (often with a CME)",
    "III": "Type III: fast drift, electron beams along open field lines",
    "IV": "Type IV: broadband continuum after a flare",
    "V": "Type V: continuum following type III bursts",
    "VI": "Type VI: a series of type III bursts over ten minutes or more",
    "VII": "Type VII: a series of type III and V bursts",
    "CTM": "Continuum",
}


# ---------------------------------------------------------------- workers
class DownloadWorker(QThread):
    """Downloads and converts in a thread; ``done`` gives the FITS files
    written."""

    progress = pyqtSignal(str)
    counts = pyqtSignal(int, int, int)  # fetched, written, total
    done = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, params: dict):
        super().__init__()
        self.params = params
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        p = self.params
        hooks = dict(
            progress=self.progress.emit,
            counts=lambda *a: self.counts.emit(*a),
            should_stop=lambda: self._cancelled,
        )
        try:
            self.done.emit(sources.SOURCES[p["source"]].download(p, **hooks))
        except rdd.Cancelled:
            self.error.emit("Download cancelled")
        except rdd.ArchiveUnreachable as e:
            self.error.emit(f"The archive could not be reached ({e})")
        except Exception as e:
            self.error.emit(str(e))


class _Answer(QObject):
    """Carries a background question's answer to the GUI thread."""

    ready = pyqtSignal(object, object)  # key, answer


_quitting = []  # set when the application quits: answers are dropped then


def _note_quit():
    _quitting.append(True)


def ask_in_background(key, question, slot):
    """Run ``question()`` in a daemon thread and call ``slot(key, answer)``
    in the GUI thread (answer None when it raised). Nothing is tied to the
    window: it can close while the archive is slow to answer. Returns a
    handle whose ``stopped`` flag drops the answer."""
    carrier = _Answer()
    carrier.stopped = False
    carrier.ready.connect(slot)
    app = QApplication.instance()
    if app is not None and not getattr(app, "_radio_quit_noted", False):
        app.aboutToQuit.connect(_note_quit)
        app._radio_quit_noted = True

    def run():
        try:
            answer = question()
        except Exception as e:
            answer = e
        if not carrier.stopped and not _quitting:
            try:
                carrier.ready.emit(key, answer)
            except RuntimeError:  # the window has gone
                pass

    threading.Thread(target=run, daemon=True).start()
    return carrier


def archive_question(key):
    """What the archive holds for a request (it begins with its source):
    the source's answer."""
    return lambda: sources.SOURCES[key[0]].ask(key)


def bursts_question(days):
    """The radio bursts reported on ``days`` (e-CALLISTO's list and NOAA's
    event reports)."""
    return lambda: [b for day in days for b in rdd.radio_bursts(day)]


# ------------------------------------------------------------------ window
class RadioDataDownloaderGUI(QDialog):
    """The Radio Data Downloader window."""

    def __init__(self, parent=None, initial_datetime=None):
        # A window of its own, not owned by the viewer, so either can be in
        # front and it shows in the task switcher
        super().__init__(None)
        self.setWindowTitle("Download radio spectra")
        self.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.setMinimumSize(820, 600)
        fit_to_screen(self, 820, 600)
        self.setModal(False)
        self.viewer = parent if hasattr(parent, "open_spectrum") else None
        self.initial_datetime = initial_datetime
        self.settings = QSettings("SolarViewer", "SolarViewer")
        self.download_worker = None
        self.files = []
        self._started = None
        self._log_file = None
        self._fetched = self._written = self._total = 0
        self._unit = "files"  # what the running download counts
        self._log_lines = []
        self._estimate = {}
        self._found = None  # (key, answer) once the archive has answered
        self._failed = None  # (key, reason) when it could not be asked
        self._count_key = None
        self._count_worker = None
        self._bursts = {}  # day tuple -> bursts
        self._bursts_failed = set()  # day tuples no list answered for
        self._burst_worker = None
        self._burst_days = None
        self._show_all_bursts = False
        self._wanted_station = None  # a station to pick once the archive has listed them
        self._burst_stations = []  # or one of these (a burst picked from the list saw them)
        self._updating = False
        self._window_seconds = 3600
        self._options_open = False
        self._done = False  # the progress page shows a finished download
        self._log_path = ""
        self.base_dir = self._setting("base_dir", os.path.join(os.path.expanduser("~"), "solar_data"))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_form(), 5)
        body.addWidget(self._build_side(), 3)
        outer.addLayout(body, 1)
        outer.addWidget(self._build_footer(), 0)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._count_timer = QTimer(self)
        self._count_timer.setSingleShot(True)
        self._count_timer.setInterval(COUNT_DELAY_MS)
        self._count_timer.timeout.connect(self._ask_archive)

        self.choose_source(self._setting("network", sources.DEFAULT))
        self.on_source_changed()
        themed(self, self._restyle)
        try:
            set_hand_cursor(self)
        except Exception:
            pass

    def _restyle(self, _palette):
        """Rich text and the log hold the theme's colours: they are written again."""
        self._show_options_chevron()
        self.on_processing_changed(self.process_switch.isChecked())
        if self.side_pages.currentIndex() == 1:
            self._render_progress(self._done)
            render_log(self.log_view, self._log_lines)
            self._say_log_path()

    # ------------------------------------------------------------ settings
    def _setting(self, key, default):
        value = self.settings.value(f"{SETTINGS_GROUP}/{key}", default)
        if isinstance(default, bool):
            return value in (True, "true", "1", 1)
        return value

    def _save_settings(self):
        source = self.source()
        values = {"network": source.key, "base_dir": self.base_dir}
        values.update({key: box.isChecked() for key, box in self.option_boxes.items()})
        if source.switch is not None:
            values[source.switch.setting] = self.process_switch.isChecked()
        for other in sources.SOURCES.values():  # what was chosen for each source
            choice = self.choice_combos[other.key].currentData()
            if choice:
                values[other.setting] = choice
        for key, value in values.items():
            self.settings.setValue(f"{SETTINGS_GROUP}/{key}", value)

    # ------------------------------------------------------------ the form
    def _build_form(self):
        self.form = QWidget()
        layout = QVBoxLayout(self.form)
        layout.setContentsMargins(SPACE_L, SPACE_L, SPACE_L, SPACE_L)
        layout.setSpacing(SPACE_L)

        # Source: where the spectrum comes from, and its station or receiver
        block = QVBoxLayout()
        block.setSpacing(SPACE_S)
        block.addWidget(kicker("Source"))
        row = QHBoxLayout()
        row.setSpacing(SPACE_S)
        self.source_combo = QComboBox()
        self.source_combo.setToolTip("The instrument or network the spectrum comes from")
        group = None
        for source in sources.SOURCES.values():
            if source.group != group:  # a heading, not to be chosen
                group = source.group
                self.source_combo.addItem(group.upper())
                heading = self.source_combo.model().item(self.source_combo.count() - 1)
                heading.setFlags(Qt.NoItemFlags)
                font = heading.font()
                font.setPointSizeF(font.pointSizeF() * 0.85)
                heading.setFont(font)
            self.source_combo.addItem(source.name, source.key)
            self.source_combo.setItemData(self.source_combo.count() - 1, source.tip, Qt.ToolTipRole)
        self.source_combo.setMaxVisibleItems(self.source_combo.count())
        self.source_combo.currentIndexChanged.connect(self.on_source_changed)
        row.addWidget(self.source_combo, 2)
        self.station_pages = QStackedWidget()
        self.choice_combos = {}
        for source in sources.SOURCES.values():
            combo = QComboBox()
            for text, value, tip in source.choices():
                combo.addItem(text, value)
                if tip:
                    combo.setItemData(combo.count() - 1, tip, Qt.ToolTipRole)
            if source.listed:
                combo.setToolTip(f"The {source.picks.lower()}s with files in this window, as the archive lists them")
            else:
                combo.setCurrentIndex(max(combo.findData(self._setting(source.setting, "")), 0))
            combo.currentIndexChanged.connect(self.on_station_changed)
            self.choice_combos[source.key] = combo
            self.station_pages.addWidget(combo)
        self.station_pages.setFixedHeight(self.source_combo.sizeHint().height())
        row.addWidget(self.station_pages, 3)
        block.addLayout(row)
        self.station_note = _label("", "Meta")
        self.station_note.setWordWrap(True)
        block.addWidget(self.station_note)
        layout.addLayout(block)

        # Time range
        block = QVBoxLayout()
        block.setSpacing(SPACE_S)
        head, _ = kicker_row("Time range", "UTC")
        block.addWidget(head)
        row = QHBoxLayout()
        row.setSpacing(SPACE_S)
        self.mode_group, self.mode_buttons = _segments(["Whole day", "Time range"])
        for button in self.mode_buttons:
            button.clicked.connect(lambda _=False, b=button: self._pick(self.mode_buttons, b, self.on_mode_changed))
        row.addWidget(self.mode_group)
        self.mode_note = _label("", "Meta")
        self.mode_note.setWordWrap(True)
        row.addWidget(self.mode_note, 1)
        row.addStretch()
        block.addLayout(row)
        self.time_pages = QStackedWidget()
        day_page = QWidget()
        grid = QGridLayout(day_page)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(SPACE_S)
        grid.setVerticalSpacing(SPACE_XS)
        grid.addWidget(_label("Day", "Meta"), 0, 0)
        self.day_edit = QLineEdit()
        _monospace(self.day_edit)
        self.day_edit.setPlaceholderText("YYYY-MM-DD")
        self.day_edit.textEdited.connect(self._day_edited)
        attach_date_picker(self.day_edit, self._day_edited, fmt=DAY_FORMAT)
        grid.addWidget(self.day_edit, 1, 0)
        grid.addWidget(QWidget(), 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.time_pages.addWidget(day_page)
        range_page = QWidget()
        grid = QGridLayout(range_page)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(SPACE_S)
        grid.setVerticalSpacing(SPACE_XS)
        self.start_edit, self.end_edit = QLineEdit(), QLineEdit()
        for col, (name, edit) in enumerate((("Start", self.start_edit), ("End", self.end_edit))):
            grid.addWidget(_label(name, "Meta"), 0, col)
            _monospace(edit)
            edit.setPlaceholderText("YYYY-MM-DD HH:MM:SS")
            grid.addWidget(edit, 1, col)
        self.start_edit.textEdited.connect(self._start_edited)
        self.end_edit.textEdited.connect(self._end_edited)
        attach_date_picker(self.start_edit, self._start_edited, band=lambda: self.range_times()[1])
        attach_date_picker(self.end_edit, self._end_edited, band=lambda: self.range_times()[0])
        self.time_pages.addWidget(range_page)
        self.time_pages.setFixedHeight(day_page.sizeHint().height())
        block.addWidget(self.time_pages)
        layout.addLayout(block)
        if self.initial_datetime is not None:  # around the image's time
            moment = self.initial_datetime.replace(microsecond=0)
            start, end = moment - timedelta(minutes=30), moment + timedelta(minutes=30)
            day = moment.date()
            self._pick(self.mode_buttons, self.mode_buttons[1])
        else:
            day = (datetime.utcnow() - timedelta(days=1)).date()
            start = datetime.combine(day, datetime.min.time()).replace(hour=12)
            end = start + timedelta(hours=1)
            self._pick(self.mode_buttons, self.mode_buttons[0])
        self.time_pages.setCurrentIndex(0 if self.whole_day() else 1)
        self.day_edit.setText(day.strftime(DAY_FORMAT))
        self.start_edit.setText(start.strftime(TIME_FORMAT))
        self.end_edit.setText(end.strftime(TIME_FORMAT))
        self._window_seconds = (end - start).total_seconds()

        # Save to
        block = QVBoxLayout()
        block.setSpacing(SPACE_S)
        block.addWidget(kicker("Save to"))
        row = QHBoxLayout()
        row.setSpacing(SPACE_S)
        self.folder_field = QFrame()
        self.folder_field.setObjectName("Field")
        field_layout = QHBoxLayout(self.folder_field)
        field_layout.setContentsMargins(SPACE_S, 0, SPACE_S, 0)
        self.folder_label = QLabel()
        self.folder_label.setTextFormat(Qt.RichText)
        field_layout.addWidget(self.folder_label)
        row.addWidget(self.folder_field, 1)
        browse = QPushButton("Browse…")
        browse.setAutoDefault(False)
        browse.clicked.connect(self.browse_output_dir)
        row.addWidget(browse)
        block.addLayout(row)
        layout.addLayout(block)

        # Processing
        self.processing_block = QWidget()
        block = QVBoxLayout(self.processing_block)
        block.setContentsMargins(0, 0, 0, 0)
        block.setSpacing(SPACE_S)
        self.processing_kicker = kicker("Processing")
        block.addWidget(self.processing_kicker)
        self.processing_row = QFrame()
        self.processing_row.setObjectName("Surface")
        row = QHBoxLayout(self.processing_row)
        row.setContentsMargins(SPACE_M, SPACE_S, SPACE_M, SPACE_S)
        row.setSpacing(SPACE_S)
        self.process_switch = _switch(True)
        self.process_switch.toggled.connect(self.on_processing_changed)
        row.addWidget(self.process_switch, 0, Qt.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(0)
        self.processing_title = QLabel()
        self.processing_title.setTextFormat(Qt.RichText)
        self.processing_desc = _label("", "Meta")
        self.processing_desc.setWordWrap(True)
        self.processing_desc.setMinimumHeight(2 * self.processing_desc.fontMetrics().lineSpacing())
        text.addWidget(self.processing_title)
        text.addWidget(self.processing_desc)
        row.addLayout(text, 1)
        self.options_link = link_button("Options", self.toggle_options)
        self.options_link.setLayoutDirection(Qt.RightToLeft)  # the chevron after the word
        self.options_link.setIconSize(QSize(10, 10))
        row.addWidget(self.options_link, 0, Qt.AlignTop)
        block.addWidget(self.processing_row)
        self.options_row = QWidget()
        options = QGridLayout(self.options_row)
        options.setContentsMargins(SPACE_M, 0, SPACE_M, 0)
        options.setHorizontalSpacing(SPACE_M)
        options.setVerticalSpacing(SPACE_S)
        self.flag_channels_box = QCheckBox("Flag bad channels")
        self.flag_channels_box.setToolTip("Blank the frequency channels known to carry interference, then fill them in")
        self.flag_calibration_box = QCheckBox("Flag calibration periods")
        self.flag_calibration_box.setToolTip("Blank the spikes of the daily calibrations")
        self.divide_median_box = QCheckBox("Divide by each channel's median")
        self.divide_median_box.setToolTip("Relative intensity: flattens the receiver's frequency response")
        self.background_box = QCheckBox("Subtract each channel's median")
        self.background_box.setToolTip("Remove the constant background of each channel (the horizontal stripes)")
        self.keep_raw_box = QCheckBox("Keep downloaded files")
        self.keep_raw_box.setToolTip("Leave the archive's files next to the spectra")
        self.flag_channels_box.setChecked(self._setting("flag_channels", True))
        self.flag_calibration_box.setChecked(self._setting("flag_calibration", True))
        self.divide_median_box.setChecked(self._setting("divide_median", False))
        self.background_box.setChecked(self._setting("subtract_background", False))
        self.keep_raw_box.setChecked(self._setting("keep_raw", False))
        self.option_boxes = dict(zip(sources.OPTIONS, (
            self.flag_channels_box, self.flag_calibration_box, self.divide_median_box,
            self.background_box, self.keep_raw_box,
        )))
        for box in self.option_boxes.values():
            box.setObjectName("SmallCheck")
            box.toggled.connect(self.update_summary)
        options.setColumnStretch(2, 1)
        self._options_grid = options
        self.options_row.hide()
        block.addWidget(self.options_row)
        layout.addWidget(self.processing_block)
        layout.addStretch(1)  # (all the room left over: the blocks keep their spacing whatever a source shows)
        return self.form

    # ------------------------------------------------------ the side column
    def _build_side(self):
        side = QFrame()
        side.setObjectName("SidePanel")
        side.setMinimumWidth(300)
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
        layout.setContentsMargins(SPACE_L, SPACE_L, SPACE_L, SPACE_L)
        layout.setSpacing(SPACE_M)
        layout.addWidget(kicker("You'll get"))
        head = QVBoxLayout()
        head.setSpacing(SPACE_XS)
        self.count_label = _label("", "DialogTitle")
        self.size_label = _label("", "Hint")
        self.size_label.setWordWrap(True)
        count_row = QHBoxLayout()
        count_row.setSpacing(SPACE_S)
        count_row.addWidget(self.count_label)
        self.count_label.ensurePolished()
        self.archive_spinner = Spinner(max(12, int(self.count_label.fontMetrics().capHeight() * 0.9)))
        self.archive_spinner.setToolTip("Asking the archive which files it holds")
        count_row.addWidget(self.archive_spinner, 0, Qt.AlignVCenter)
        count_row.addStretch()
        head.addLayout(count_row)
        head.addWidget(self.size_label)
        layout.addLayout(head)
        grid = QGridLayout()
        grid.setHorizontalSpacing(SPACE_M)
        grid.setVerticalSpacing(SPACE_XS)
        self.summary_values = {}
        for i, key in enumerate(("Station", "Window", "Observes", "Output")):
            name = _label(key, "Hint")
            grid.addWidget(name, i, 0, Qt.AlignTop)
            value = _label("", mono=(key == "Window"))
            value.setTextFormat(Qt.RichText)
            value.setWordWrap(True)
            self.summary_values[key] = (name, value)
            grid.addWidget(value, i, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        self.timeline_box = QWidget()
        timeline = QVBoxLayout(self.timeline_box)
        timeline.setContentsMargins(0, 0, 0, 0)
        timeline.setSpacing(SPACE_XS)
        timeline.addWidget(_label("Timeline · one dot per 15 min file", "Meta"))
        self.timeline = Timeline()
        timeline.addWidget(self.timeline)
        ends = QHBoxLayout()
        self.timeline_start = _label("", "Meta", mono=True)
        self.timeline_end = _label("", "Meta", mono=True)
        ends.addWidget(self.timeline_start)
        ends.addStretch()
        ends.addWidget(self.timeline_end)
        timeline.addLayout(ends)
        layout.addWidget(self.timeline_box)
        layout.addWidget(rule())
        bursts = QVBoxLayout()
        bursts.setSpacing(SPACE_XS)
        self.bursts_head = _label("", "Meta")
        self.bursts_head.setTextFormat(Qt.RichText)
        self.bursts_head.setWordWrap(True)
        bursts_row = QHBoxLayout()
        bursts_row.setSpacing(SPACE_S)
        bursts_row.addWidget(self.bursts_head, 1)
        self.bursts_spinner = Spinner(12)
        self.bursts_spinner.setToolTip("Asking the burst lists")
        bursts_row.addWidget(self.bursts_spinner, 0, Qt.AlignVCenter)
        bursts.addLayout(bursts_row)
        self.bursts_label = QLabel()
        self.bursts_label.setTextFormat(Qt.RichText)
        self.bursts_label.setWordWrap(True)
        self.bursts_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.bursts_label.linkActivated.connect(self.use_burst)
        bursts.addWidget(self.bursts_label)
        layout.addLayout(bursts)
        layout.addStretch()
        self.note_label = _label("", "Meta")
        self.note_label.setWordWrap(True)
        self.note_label.setTextFormat(Qt.RichText)
        self.note_label.linkActivated.connect(self._note_link)
        layout.addWidget(self.note_label)
        return page

    def _build_progress(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(SPACE_L, SPACE_L, SPACE_L, SPACE_L)
        layout.setSpacing(SPACE_M)
        head, self.elapsed_label = kicker_row("Downloading", "0:00 elapsed")
        self.progress_kicker = head.findChild(QLabel, "Kicker")
        _monospace(self.elapsed_label)
        layout.addWidget(head)
        block = QVBoxLayout()
        block.setSpacing(SPACE_XS)
        numbers = QHBoxLayout()
        numbers.setSpacing(SPACE_S)
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
        tiles.setSpacing(SPACE_S)
        self.tile_values = {}
        for name in ("Fetched", "Converted"):
            tile = QFrame()
            tile.setObjectName("Tile")
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(SPACE_S, SPACE_XS, SPACE_S, SPACE_XS)
            tile_layout.setSpacing(0)
            tile_layout.addWidget(_label(name, "Meta"))
            value = _label("—", "Mono")
            tile_layout.addWidget(value)
            self.tile_values[name] = value
            tiles.addWidget(tile, 1)
        layout.addLayout(tiles)
        layout.addWidget(rule())
        self.log_view = QTextEdit()
        self.log_view.setObjectName("QuietLog")
        self.log_view.setReadOnly(True)
        self.log_view.setFrameShape(QFrame.NoFrame)
        self.log_view.setLineWrapMode(QTextEdit.WidgetWidth)
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
        layout.setContentsMargins(SPACE_L, 0, SPACE_L, 0)
        layout.setSpacing(SPACE_S)
        self.open_label = _label("Opens in the Dynamic Spectrum Viewer when done", "Meta")
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
        self.folder_button = QPushButton("Show folder")
        self.folder_button.setObjectName("GhostButton")
        self.folder_button.setAutoDefault(False)
        self.folder_button.setToolTip("Open the folder the spectra were saved in")
        self.folder_button.clicked.connect(self.show_folder)
        self.folder_button.hide()
        layout.addWidget(self.folder_button)
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
        self.open_button = QPushButton("Open spectrum")
        self.open_button.setToolTip("Show the spectrum in the Dynamic Spectrum Viewer")
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
    def source(self):
        """The source chosen (an entry of sources.SOURCES)."""
        return sources.SOURCES.get(self.source_combo.currentData()) or sources.SOURCES[sources.DEFAULT]

    def choose_source(self, key):
        """Show the source ``key`` (the default one when there is none such)."""
        index = self.source_combo.findData(key if key in sources.SOURCES else sources.DEFAULT)
        self.source_combo.setCurrentIndex(index)

    def choice(self):
        """What the source's second list has chosen: a station, a receiver…
        ("" while a source that lists what the archive has for the window
        has not listed it, or has nothing)."""
        return self.choice_combos[self.source().key].currentData() or ""

    def whole_day(self):
        return self.mode_buttons[0].isChecked()

    def day(self):
        try:
            return datetime.strptime(self.day_edit.text().strip(), DAY_FORMAT).date()
        except ValueError:
            return None

    def range_times(self):
        out = []
        for edit in (self.start_edit, self.end_edit):
            try:
                out.append(datetime.strptime(edit.text().strip(), TIME_FORMAT))
            except ValueError:
                out.append(None)
        return tuple(out)

    def times(self):
        """(start, end) of the request, UT. A whole day is what the source
        makes of it: 00:00 to 24:00, or the hours its observing day's file
        may span."""
        if not self.whole_day():
            return self.range_times()
        day = self.day()
        if day is None:
            return None, None
        return self.source().day_window(self.choice(), day)

    def output_dir(self):
        start, _ = self.times()
        day = self.day() if self.whole_day() else (start.date() if start else None)
        stamp = (day or datetime.utcnow().date()).strftime("%Y-%m-%d")
        return os.path.join(self.base_dir, stamp, self.source().folder(self.choice()))

    @staticmethod
    def _pick(buttons, chosen, then=None):
        for button in buttons:
            button.setChecked(button is chosen)
        if then is not None:
            then()

    # ------------------------------------------------------------- events
    def on_source_changed(self, *args):
        source = self.source()
        combo = self.choice_combos[source.key]
        self.station_pages.setCurrentWidget(combo)
        # (a source with nothing to choose shows no list)
        self.station_pages.setVisible(source.listed or combo.count() > 1)
        self._updating = True
        if source.switch is not None:
            self.process_switch.setChecked(self._setting(source.switch.setting, source.switch.default))
        # (what the archive lists is filled in when it answers: the one chosen
        # before, in this window or the last time, is wanted again)
        self._wanted_station = (combo.currentData() or self._setting(source.setting, "")) if source.listed else None
        self._burst_stations = []
        self._updating = False
        # its processing: the switch when it has one, and its options two to a
        # row (behind "Options" when there is a switch to hang them on)
        for box in self.option_boxes.values():
            self._options_grid.removeWidget(box)
            box.hide()
        for i, key in enumerate(source.options):
            self._options_grid.addWidget(self.option_boxes[key], i // 2, i % 2)
            self.option_boxes[key].show()
        self.processing_block.setVisible(source.switch is not None or bool(source.options))
        self.processing_row.setVisible(source.switch is not None)
        self.processing_kicker.setText("PROCESSING" if source.switch is not None else "FILES")
        self.options_row.setVisible(bool(source.options) and (self._options_open or source.switch is None))
        self.on_processing_changed(self.process_switch.isChecked())
        self.on_station_changed()

    def on_station_changed(self, *args):
        if self._updating:
            return
        source, choice = self.source(), self.choice()
        self.station_note.setText(source.note(choice))
        self.mode_note.setText(source.day_note(choice))
        if source.listed and choice and choice != getattr(source, "every", None):
            self._wanted_station = choice
        self.mode_note.setVisible(self.whole_day())
        self.update_summary()

    def on_mode_changed(self):
        whole = self.whole_day()
        self.time_pages.setCurrentIndex(0 if whole else 1)
        if whole:  # the day follows the window, and back
            start, _ = self.range_times()
            if start is not None:
                self.day_edit.setText(start.strftime(DAY_FORMAT))
        else:
            day = self.day()
            start, end = self.range_times()
            if day is not None and start is not None and start.date() != day:
                start = datetime.combine(day, start.time())
                self.start_edit.setText(start.strftime(TIME_FORMAT))
                self.end_edit.setText((start + timedelta(seconds=self._window_seconds)).strftime(TIME_FORMAT))
        self.mode_note.setVisible(whole)
        self.update_summary()

    def on_processing_changed(self, on):
        switch = self.source().switch
        if switch is not None:
            self.processing_title.setText(
                switch.title + _muted(f" · {switch.aside}", theme_manager.palette["text_secondary"])
            )
            self.processing_desc.setText(switch.on if on else switch.off)
        if not self._updating:
            self.update_summary()

    def toggle_options(self):
        self._options_open = not self._options_open
        self.options_row.setVisible(self._options_open)
        self._show_options_chevron()

    def _show_options_chevron(self):
        self.options_link.setIcon(QIcon(themed_icon("spinbox_up.png" if self._options_open else "spinbox_down.png")))

    def _day_edited(self, text):
        self.update_summary()

    def _start_edited(self, text):
        start, _ = self.range_times()
        if start is not None:  # the end keeps its distance
            self.end_edit.setText((start + timedelta(seconds=self._window_seconds)).strftime(TIME_FORMAT))
        self.update_summary()

    def _end_edited(self, text):
        start, end = self.range_times()
        if start is not None and end is not None and end > start:
            self._window_seconds = (end - start).total_seconds()
        self.update_summary()

    def browse_output_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Save downloads under", self.base_dir, QFileDialog.ShowDirsOnly)
        if path:
            self.base_dir = path
            self.update_summary()

    def set_window(self, start, end):
        """Show the time range from ``start`` to ``end``."""
        self._pick(self.mode_buttons, self.mode_buttons[1])
        self.time_pages.setCurrentIndex(1)
        self.mode_note.hide()
        self.start_edit.setText(start.strftime(TIME_FORMAT))
        self.end_edit.setText(end.strftime(TIME_FORMAT))
        self._window_seconds = (end - start).total_seconds()
        self.update_summary()

    def use_burst(self, link):
        """A burst in the list was clicked: its time, with a margin, and
        where the stations are those the archive lists (e-CALLISTO) one
        that saw it when the chosen one did not."""
        if link == "more":
            self._show_all_bursts = not self._show_all_bursts
            self._render_bursts()
            return
        bursts = self._bursts.get(self._burst_days) or []
        try:
            burst = bursts[int(link)]
        except (ValueError, IndexError):
            return
        start = (burst["start"] - BURST_MARGIN).replace(second=0)
        end = (burst["end"] + timedelta(minutes=1) + BURST_MARGIN).replace(second=0)
        source = self.source()
        if source.listed:
            seen = [s.upper() for s in burst["stations"]]
            current = self.choice()
            if not current or current == source.every or current.upper() not in seen:
                self._wanted_station = burst["stations"][0] if burst["stations"] else None
                self._burst_stations = list(burst["stations"])
        self.set_window(start, end)

    def _note_link(self, link):
        if link == "day":
            self._pick(self.mode_buttons, self.mode_buttons[0], self.on_mode_changed)

    # ----------------------------------------------------- archive check
    def _request_key(self):
        """What the archive is asked for the form: a tuple the source makes
        (it begins with the source's key); None when the form does not make
        a request."""
        start, end = self.times()
        if start is None or end is None or end <= start:
            return None
        return self.source().request(self.choice(), start, end, self.day() if self.whole_day() else None)

    def _ask_archive(self):
        key = self._request_key()
        if key is None or key != self._count_key or not self.isVisible():
            return
        self._stop_count()
        self._count_worker = ask_in_background(key, archive_question(key), self._archive_answered)
        self.update_summary()

    def _archive_answered(self, key, answer):
        if key != self._count_key:
            return
        self._count_worker = None
        if isinstance(answer, Exception):
            self._failed = (key, str(answer))
            answer = None
        else:
            self._found = (key, answer)
        if sources.SOURCES[key[0]].listed and answer is not None:
            self._fill_stations(answer)
        self.update_summary()

    def _stop_count(self):
        self._count_timer.stop()
        if self._count_worker is not None:
            self._count_worker.stopped = True
            self._count_worker = None

    def _fill_stations(self, answer):
        """What the archive lists for the window (e-CALLISTO's stations with
        files), the wanted one chosen, or the one the source suggests."""
        source = self.source()
        combo = self.choice_combos[source.key]
        items, suggested = source.listing(answer)
        wanted = [s.upper() for s in ([self._wanted_station] if self._wanted_station else [])]
        wanted += [s.upper() for s in self._burst_stations]
        self._updating = True
        combo.clear()
        for text, value, tip in items:
            combo.addItem(text, value)
            if tip:
                combo.setItemData(combo.count() - 1, tip, Qt.ToolTipRole)
        index = -1
        for name in wanted:
            index = next((i for i in range(combo.count()) if str(combo.itemData(i)).upper() == name), -1)
            if index >= 0:
                break
        if index < 0:
            index = combo.findData(suggested)
        combo.setCurrentIndex(max(index, 0))
        self._burst_stations = []
        self._updating = False
        self.on_station_changed()

    def _ask_bursts(self, start, end):
        """The bursts reported on the window's days (up to three)."""
        if start is None or end is None or end <= start:
            return
        days = []
        day = start.date()
        while day <= end.date() and len(days) < 3:
            days.append(day)
            day += timedelta(days=1)
        days = tuple(days)
        if days == self._burst_days:
            return
        self._burst_days = days
        self._show_all_bursts = False
        self._bursts_failed.discard(days)  # asked again when the form comes back to them
        if days in self._bursts:
            self._render_bursts()
            return
        if self._burst_worker is not None:
            self._burst_worker.stopped = True
        self._burst_worker = ask_in_background(days, bursts_question(days), self._bursts_answered)
        self._render_bursts()

    def _bursts_answered(self, days, bursts):
        self._burst_worker = None
        if isinstance(bursts, Exception):
            self._bursts_failed.add(days)
        else:
            self._bursts[days] = bursts
        if days == self._burst_days:
            self._render_bursts()

    def _render_bursts(self):
        days = self._burst_days
        failed = days in self._bursts_failed
        self.bursts_spinner.setVisible(bool(days) and days not in self._bursts and not failed)
        if not days:
            self.bursts_head.setText("")
            self.bursts_label.setText("")
            return
        when = days[0].strftime("%Y-%m-%d") + (f" to {days[-1]:%m-%d}" if len(days) > 1 else "")
        if self.whole_day() and self.day() is not None:
            when = f"{self.day():%Y-%m-%d}"
        palette = theme_manager.palette
        secondary, link, accent = palette["text_secondary"], palette["link"], palette["highlight"]
        bursts = self._bursts.get(days)
        chosen = self.source()
        rstn = chosen.bursts == "noaa"  # its station's reports to NOAA; else e-CALLISTO's list
        station = self.choice() if (rstn or chosen.listed) else ""
        if station == getattr(chosen, "every", None):
            station = ""
        source = f"reported by {_escape(station)}" if rstn else "seen by e-CALLISTO"
        if bursts is None:
            self.bursts_head.setText(f"Bursts {source} · {when}")
            text = "The burst lists could not be reached" if failed else "Asking the burst lists…"
            self.bursts_label.setText(_muted(text, secondary))
            return
        # RSTN: NOAA's reports of this station; e-CALLISTO: its network's
        # list. A whole day keeps the bursts in it.
        if rstn:
            indexed = [(i, b) for i, b in enumerate(bursts) if b["source"] == "NOAA" and b["stations"] == [station]]
        else:
            indexed = [(i, b) for i, b in enumerate(bursts) if b["source"] == "e-CALLISTO"]
        start, end = self.times()
        if self.whole_day() and start is not None:
            indexed = [(i, b) for i, b in indexed if b["end"] >= start and b["start"] <= end]
        mark = f"<span style='color:{accent}'>●</span>"
        seen_here = not rstn and station
        legend = f" · {mark} seen by {_escape(station)}" if seen_here and indexed else ""
        self.bursts_head.setText(f"Bursts {source} · {when}{legend}")
        if not indexed:
            none = (f"None reported by {_escape(station)} (NOAA's event lists)" if rstn
                    else "None listed (e-CALLISTO's burst lists cover 2010–11, 2014, 2020 on)")
            self.bursts_label.setText(_muted(none, secondary))
            return
        shown = indexed if self._show_all_bursts else indexed[:BURSTS_SHOWN]
        rows = []
        for i, b in shown:
            span = f"{b['start']:%H:%M}" + (f"–{b['end']:%H:%M}" if b["end"] > b["start"] else "")
            if len(days) > 1:
                span = f"{b['start']:%m-%d} " + span
            kind = f"<span title='{_escape(BURST_TYPES.get(b['type'], ''))}'>{_escape(b['type'])}</span>"
            if rstn:
                extra = ""
            else:
                n = len(b["stations"])
                saw = seen_here and any(s.upper() == station.upper() for s in b["stations"])
                extra = (f"{mark} " if saw else "") + _muted(f"{n} station{'s' if n > 1 else ''}", secondary)
            rows.append(
                f"<tr><td style='padding-right:10px'><a href='{i}' style='color:{link};text-decoration:none'>"
                f"{span}</a></td><td style='padding-right:10px'>{kind}</td><td>{extra}</td></tr>"
            )
        more = ""
        if len(indexed) > BURSTS_SHOWN:
            text = "fewer" if self._show_all_bursts else f"{len(indexed) - BURSTS_SHOWN} more"
            more = f"<a href='more' style='color:{link};text-decoration:none'>{text}</a>"
        self.bursts_label.setText(f"<table cellspacing='0' cellpadding='1'>{''.join(rows)}</table>{more}")
        self.bursts_label.setToolTip("Click a time to download around that burst")

    # ------------------------------------------------------------ summary
    def estimate(self):
        """What the form asks for, once the archive has answered: files,
        spectra, size and time; None where it is not known yet."""
        key = self._request_key()
        answer = self._found[1] if self._found and self._found[0] == key else None
        start, end = self.times()
        out = dict(key=key, start=start, end=end, files=None, spectra=None, size_mb=None, seconds=None,
                   receivers=None, positions=None)
        if answer is None:
            return out
        source = self.source()
        out.update(source.estimate(answer, self.choice(), start, end, self.process_switch.isChecked()))
        if out["files"]:
            out["seconds"] = out["size_mb"] / THROUGHPUT_MB_S + out.get("units", out["files"]) * source.seconds_each
        return out

    def update_summary(self, *args):
        if self._updating:
            return
        palette = theme_manager.palette
        secondary, link = palette["text_secondary"], palette["link"]
        source = self.source()
        key = self._request_key()
        if key != self._count_key:  # a new question for the archive
            self._stop_count()
            self._count_key, self._found, self._failed = key, None, None
            if key is not None:
                self._count_timer.start()
        failed = self._failed is not None and self._failed[0] == key
        est = self._estimate = self.estimate()
        start, end = est["start"], est["end"]
        bad_window = start is None or end is None or end <= start
        if self.whole_day():
            set_tone(self.day_edit, None if self.day() is not None else "error")
        else:
            rs, re_ = self.range_times()
            set_tone(self.start_edit, None if rs is not None else "error")
            set_tone(self.end_edit, None if (re_ is not None and rs is not None and re_ > rs) else "error")
        station = self.choice()
        waiting = source.listed and station == ""  # its stations are not listed yet
        checking = not bad_window and est["files"] is None and not failed
        self.archive_spinner.setVisible(checking)

        # the count and its line
        set_tone(self.size_label, None)
        spectra = est["spectra"]
        if bad_window:
            self._say_count("—")
            self.size_label.setText("Choose a day or a window that ends after it starts")
            set_tone(self.size_label, "error")
        elif failed:
            self._say_count("Unknown", "Hint")
            self.size_label.setText("The archive could not be reached; it is asked again when the form changes")
            set_tone(self.size_label, "error")
        elif est["files"] is None:
            self._say_count("…")
            self.size_label.setText("checking the archive…")
        elif spectra == 0:
            self._say_count("No data")
            self.size_label.setText(self._no_data_reason())
            set_tone(self.size_label, "warning")
        else:
            noun = "spectrum" if spectra == 1 else "spectra"
            self._say_count(f"{spectra} {noun}")
            n = est["files"]
            self.size_label.setText(
                f"from {n} file{'s' if n > 1 else ''} · ≈ {_size_text(est['size_mb'])} · "
                f"about {_duration_text(est['seconds'])}"
            )

        # the details
        def put(row, value, title=None):
            name, label = self.summary_values[row]
            if title:
                name.setText(title)
            name.setVisible(bool(value))
            label.setVisible(bool(value))
            label.setText(value or "")

        name, aside, row, text = source.describe(station, est)
        put("Station", _escape(name) + (_muted(f" · {_escape(aside)}", secondary) if aside else ""), "Source")
        put("Observes", _escape(text), row)
        if bad_window:
            put("Window", "—")
        elif self.whole_day() and source.observing_day:
            put("Window", f"{self.day():%Y-%m-%d}" + _muted(" · its observing day", secondary))
        else:
            fmt = "%m-%d %H:%M"
            put("Window", f"{start:{fmt}} → {end:{fmt if end.date() != start.date() else '%H:%M'}} UTC")
        put("Output", "Dynamic spectrum FITS" + _muted(" · for the Dynamic Spectrum Viewer", secondary))

        # the files across the window (e-CALLISTO's 15 min files)
        positions = est.get("positions")
        self.timeline_box.setVisible(source.timeline and bool(positions))
        if positions:
            self.timeline.set_positions(positions)
            self.timeline_start.setText(f"{start:%H:%M}")
            self.timeline_end.setText(f"{end:%H:%M}")

        # a note at the foot
        note = ""
        if source.observing_day and not bad_window and not self.whole_day():
            night = source.night(station, start, end)
            if night:
                note = f"{_escape(night)} <a href='day' style='color:{link}'>Use its observing day</a>"
        self.note_label.setText(note)
        self.note_label.setVisible(bool(note))

        # the folder
        out = self.output_dir()
        base = _short_home(self.base_dir)
        tail = out[len(self.base_dir):] if out.startswith(self.base_dir) else ""
        self.folder_label.setText(_escape(base) + _muted(tail, secondary))
        _monospace(self.folder_label)
        self.folder_field.setToolTip(out)

        # the button
        ready = not bad_window and not failed and bool(spectra) and not waiting
        if spectra:
            noun = "spectrum" if spectra == 1 else "spectra"
            self.download_button.setText(f"Download {spectra} {noun}")
        else:
            self.download_button.setText("Download")
        # (e-CALLISTO with no station listed yet would mean every station)
        self.download_button.setEnabled(
            ready or (not bad_window and est["files"] is None and not failed and not waiting)
        )
        self.download_button.setToolTip("" if ready else "Waiting for the archive" if checking else "Nothing to download")
        self._ask_bursts(start, end)
        self._render_bursts()

    def _say_count(self, text, role="DialogTitle"):
        self.count_label.setText(text)
        if self.count_label.objectName() != role:
            self.count_label.setObjectName(role)
            self.count_label.style().unpolish(self.count_label)
            self.count_label.style().polish(self.count_label)

    def _no_data_reason(self):
        key = self._request_key()
        answer = self._found[1] if self._found and self._found[0] == key else None
        start, end = self.times()
        return self.source().no_data(answer, self.choice(), start, end, self.day() if self.whole_day() else None)

    def showEvent(self, event):
        super().showEvent(event)
        if self._count_key is not None and self._found is None and self._count_worker is None:
            self._count_timer.start()

    # ----------------------------------------------------------- download
    def get_download_parameters(self):
        """What a download is asked: the source and its choice, the window
        (``whole_day`` the day when a whole day is asked), the folder,
        whether the source's processing is ``on`` and every option."""
        start, end = self.times()
        source = self.source()
        params = {
            "source": source.key,
            "choice": self.choice(),
            "start": start,
            "end": end,
            "whole_day": self.day() if self.whole_day() else None,
            "output_dir": self.output_dir(),
            "on": source.switch is not None and self.process_switch.isChecked(),
        }
        params.update({key: box.isChecked() for key, box in self.option_boxes.items()})
        return params

    def start_download(self):
        self.update_summary()
        if not self.download_button.isEnabled():
            return
        self._stop_count()
        self._save_settings()
        params = self.get_download_parameters()
        try:
            os.makedirs(params["output_dir"], exist_ok=True)
        except OSError as e:
            set_tone(self.size_label, "error")
            self.size_label.setText(f"Cannot create the folder: {e}")
            return
        self.files = []
        self._fetched = self._written = 0
        self._total = self._estimate.get("units") or self._estimate.get("files") or 0
        self._unit = self.source().unit
        self._started = time.time()
        self._log_lines = []
        self.log_view.clear()
        log_path = os.path.join(params["output_dir"], "download.log")
        try:
            self._log_file = open(log_path, "a", encoding="utf-8")
            self._log_file.write(f"\n# {datetime.utcnow():%Y-%m-%d %H:%M:%S} UT  {params}\n")
        except OSError:
            self._log_file = None
        self._log_path = log_path
        self._say_log_path()
        self.progress_kicker.setText("DOWNLOADING")
        self.side_pages.setCurrentIndex(1)
        for button in (self.cancel_button, self.download_button, self.open_button, self.folder_button):
            button.hide()
        self.background_button.show()
        self.stop_button.show()
        self.open_label.setVisible(self.viewer is not None)
        self.open_switch.setVisible(self.viewer is not None)
        self._set_form_locked(True)
        self._render_progress()
        self._tick()
        self._timer.start()
        self.download_worker = DownloadWorker(params)
        self.download_worker.progress.connect(self.update_progress)
        self.download_worker.counts.connect(self.update_counts)
        self.download_worker.done.connect(self.download_finished)
        self.download_worker.error.connect(self.download_error)
        self.download_worker.start()

    def _say_log_path(self):
        self.log_path_label.setText("Full log → " + _muted(_short_home(self._log_path), theme_manager.palette["link"]))

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
        self._done = done
        secondary = theme_manager.palette["text_secondary"]
        total = max(self._total, self._fetched)
        if done:
            n = len(self.files)
            noun = "spectrum" if n == 1 else "spectra"
            self.progress_count.setText(f"{n}" + _muted(f" {noun} · done", secondary))
            self.progress_left.setText("")
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(100)
        else:
            unit = self._unit
            self.progress_count.setText(
                f"{self._fetched}" + _muted(f" / {total} {unit}" if total else f" {unit} so far", secondary)
            )
            if total and self._started and self._fetched:
                per_file = (time.time() - self._started) / float(self._fetched)
                left = per_file * (total - self._fetched)
                self.progress_left.setText(f"≈ {_duration_text(left)} left" if left > 5 else "almost done")
            else:
                self.progress_left.setText("")
            steps = self._fetched + self._written
            if self._started is not None and not steps:  # at 0 % it would look stuck
                self.progress_bar.setRange(0, 0)  # busy
            else:
                self.progress_bar.setRange(0, 100)
                self.progress_bar.setValue(int(100 * steps / (2.0 * total)) if total else 0)
        self.tile_values["Fetched"].setText(str(self._fetched))
        self.tile_values["Converted"].setText(f"{self._written}" if self._written or self._fetched else "—")

    def update_counts(self, fetched, written, total):
        self._fetched, self._written, self._total = fetched, written, total
        self._render_progress()

    def update_progress(self, message):
        """A status line from the download, logged newest first."""
        if self._log_file is not None:
            try:
                self._log_file.write(message + "\n")
                self._log_file.flush()
            except OSError:
                pass
        status = ""
        for prefix, mark in (("Downloaded ", "downloaded"), ("Wrote ", "written")):
            if message.startswith(prefix):
                message, status = message[len(prefix):], mark
        self._log(message, status)

    def _log(self, message, status=""):
        self._log_lines.insert(0, (datetime.utcnow().strftime("%H:%M"), message, status))
        del self._log_lines[LOG_LINES_SHOWN:]
        render_log(self.log_view, self._log_lines)

    def is_downloading(self):
        return self.download_worker is not None and self.download_worker.isRunning()

    def cancel_download(self):
        if self.is_downloading():
            self.download_worker.cancel()
            self._log("Stopping…")

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
        self._render_progress(done=True)
        self._finish("Done" if files else "Nothing written")
        self.download_button.setEnabled(True)
        if not files:
            self._log("No spectrum was written: see the lines above")
            return
        self.folder_button.show()
        if self.viewer is not None:
            # the switch has done its part: room for the buttons
            self.open_label.hide()
            self.open_switch.hide()
            self.open_button.show()
            if self.open_switch.isChecked():
                self.open_in_viewer()

    def download_error(self, message):
        self._log(message, "stopped" if "cancel" in message.lower() else "failed")
        self._finish("Stopped" if "cancel" in message.lower() else "Failed")
        self._render_progress()
        self.download_button.setText("Try again")
        self.download_button.setEnabled(True)

    def open_in_viewer(self):
        """Show the first spectrum in the Dynamic Spectrum Viewer."""
        if self.viewer is None or not self.files:
            return
        try:
            with activity(f"Opening {os.path.basename(self.files[0])} in the Dynamic Spectrum Viewer…", self, blocking=False):
                self.viewer.open_spectrum(self.files[0])
        except Exception as e:
            self._log(f"Could not open the Dynamic Spectrum Viewer: {e}", "failed")

    def show_folder(self):
        folder = os.path.dirname(self.files[0]) if self.files else self.output_dir()
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def closeEvent(self, event):
        """Stop a running download and the archive checks."""
        self._stop_count()
        if self._burst_worker is not None:
            self._burst_worker.stopped = True
            self._burst_worker = None
        if self.is_downloading():  # a stalled read must not hold the window up
            abandon(self.download_worker)
            self.download_worker = None
            self._timer.stop()
            self._started = None
        if self._log_file is not None:
            try:
                self._log_file.close()
            except OSError:
                pass
            self._log_file = None
        self._save_settings()
        super().closeEvent(event)


def launch_gui(parent=None, initial_datetime=None) -> RadioDataDownloaderGUI:
    """Show the Radio Data Downloader window (its own application when run
    without a parent). A viewer keeps one window: opening it again brings
    that one to the front."""
    app = QApplication.instance() or QApplication(sys.argv)
    window = getattr(parent, "_radio_downloader", None) if parent is not None else None
    if window is not None and not window.isVisible() and not window.is_downloading():
        window.deleteLater()  # closed earlier: start afresh from the current image's time
        window = None
    if window is None:
        window = RadioDataDownloaderGUI(parent, initial_datetime=initial_datetime)
        if parent is not None:
            parent._radio_downloader = window
            CloseWithViewer(window, parent)
    if window.isMinimized():
        window.showNormal()
    window.show()
    window.raise_()
    window.activateWindow()
    if parent is None:
        sys.exit(app.exec_())
    return window


if __name__ == "__main__":
    launch_gui()

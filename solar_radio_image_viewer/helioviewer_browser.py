"""
The Helioviewer browser: solar images around a time, from Helioviewer's
archive (or OVRO-LWA's daily movie), shown one frame at a time with a
coordinate frame over them and a time band under them.

The window is a companion to the radio image it was opened from: it opens on
that image's UTC time, marks it on the band, boxes its field on the frames,
and can hand a frame back to the viewer. Every time shown is the archive's
own observed time, never the time asked for. Nothing here opens a message
box: what is running is one line in the header, beside Cancel.

``open_helioviewer`` is the one way in; ``Target`` is what an opener knows.
All times are naive UTC datetimes.
"""

import dataclasses
import logging
import math
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import List, Optional, Tuple

from PyQt5.QtCore import QDate, QDateTime, QPointF, QRectF, QSettings, QSize, QThread, QTime, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QPen, QPixmap, QPolygonF
from PyQt5.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QShortcut,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStackedWidget,
    QStyledItemDelegate,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import helioviewer_api as hv
from . import helioviewer_catalog as catalog
from . import helioviewer_lwa as lwa
from . import helioviewer_view as hvw
from .helioviewer_data import FAILED, LOADING, MOST_WIDTH, PLANNED, READY, Frame, Plan, View, plan_frames
from .helioviewer_export import HelioviewerExportMixin, difference_image
from .helioviewer_loader import FrameLoader, memory_note
from .helioviewer_timeline import EventsFetch, FrameBand, Playback, offset_text
from .noaa_events import cache as noaa_cache
from .noaa_events.day_axis import GOES_COLOUR, GUTTER
from .noaa_events.widgets import ElidedLabel, set_source_note, source_note
from .ui.activity import ActivityLabel
from .ui.app import SETTINGS
from .ui.date_picker import attach_date_picker
from .ui.errors import show_error
from .ui.geometry import fit_to_screen
from .ui.icons import themed_icon, transport_icon
from .ui.styles import FONT_META, FONT_SMALL, MONO_FAMILY, SPACE_L, SPACE_M, SPACE_S, SPACE_XS, set_hand_cursor, set_tone, theme_manager, themed
from .ui.widgets import empty_note, kicker, link_button, segmented, vrule
from .video.ui.progress import WorkDialog

logger = logging.getLogger(__name__)

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
TIME_QT_FORMAT = "yyyy-MM-dd HH:mm:ss"
# (label, compact label, seconds each side of the time; None: the whole UTC day)
SPANS = (("±15 min", "±15m", 900), ("±30 min", "±30m", 1800), ("±1 h", "±1h", 3600), ("±3 h", "±3h", 10800), ("Day", "Day", None))
# (label, compact label, seconds between frames; None: every image the archive has)
STEPS = (("Every image", "All", None), ("1 min", "1m", 60), ("5 min", "5m", 300), ("15 min", "15m", 900), ("1 h", "1h", 3600))
DIFF_MODES = ("None", "Running", "Base", "Base = radio time")
FPS_CHOICES = Playback.FPS
NOTE_MS = 5000  # how long a note holds the activity line
LINK_NOTE_MS = 15000  # a note with a link ("Show in folder") stays longer
DIFF_CACHE = 48  # difference pixmaps kept, by (frame, mode, base)
PNG_BYTES_PER_PIXEL = 1.2  # about what a screenshot takes on the wire
FAR_S = 3600  # an image this far from the time asked for is said to be far
DEFAULT_INSTRUMENT = "AIA 171"
OVERLAY_OPACITY = 60  # % when a second layer is added
SOURCE_ROLE, GROUP_ROLE, DIM_ROLE, NOTE_ROLE = Qt.UserRole, Qt.UserRole + 1, Qt.UserRole + 2, Qt.UserRole + 3
SHOWS_LEAST = 96  # px the list keeps for what an instrument shows, whatever the names' length
HELIOVIEWER_NOTE = (
    'Images: <a href="https://helioviewer.org/">Helioviewer.org</a> · events: HEK via Helioviewer · '
    "times are the archive's records"
)
LWA_NOTE = (
    'Images: <a href="https://ovsa.njit.edu/lwa/">OVRO-LWA</a> daily movie (NJIT), not Helioviewer · '
    "times are the movie's frame list"
)
KEY = "helioviewer/browser/{}"
LWA_ORIGIN = "ovro-lwa"  # Frame.origin of the movie's frames: no Helioviewer geometry, link or archive image
NARROW_WIDTH = 1100  # under this the band does without its flare lane
ICON_BUTTON_LEAST = 18 + 2 * SPACE_XS + 4  # px: an icon button never squeezed under its icon

# Threads that outlive the window (a request cannot be interrupted), kept until they end
_active_threads: List[QThread] = []


def _park(thread):
    """Keep ``thread`` until it ends by itself."""
    _active_threads[:] = [t for t in _active_threads if t.isRunning()]
    if thread is not None and thread.isRunning():
        _active_threads.append(thread)


# ---------------------------------------------------------------- times


def utc_time(when):
    """A naive UTC datetime as a UTC QDateTime; an invalid one for None."""
    if when is None:
        return QDateTime()
    return QDateTime(QDate(when.year, when.month, when.day), QTime(when.hour, when.minute, when.second), Qt.UTC)


def py_time(value):
    """A QDateTime as a naive UTC datetime."""
    value = value.toUTC()
    day, moment = value.date(), value.time()
    return datetime(day.year(), day.month(), day.day(), moment.hour(), moment.minute(), moment.second())


def parse_field(text) -> Optional[datetime]:
    """``2024-05-14 16:15:00`` (seconds and the time optional) as a datetime, or None."""
    text = (text or "").strip()
    for fmt in (TIME_FORMAT, "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def cadence_text(seconds) -> str:
    """``36 s``, ``12 min``, ``1 h``."""
    seconds = max(1, int(round(seconds)))
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 90 * 60:
        return f"{round(seconds / 60):g} min"
    hours = seconds / 3600
    return f"{hours:.1f}".rstrip("0").rstrip(".") + " h"


def span_text(start: datetime, end: datetime) -> str:
    """``16:00–16:30 UTC``, with the dates when the range crosses midnight."""
    if start.date() == end.date():
        return f"{start:%H:%M}–{end:%H:%M} UTC"
    return f"{start:%Y-%m-%d %H:%M} – {end:%Y-%m-%d %H:%M} UTC"


def typical_gap(times: List[datetime]) -> Optional[float]:
    """The usual seconds between the sorted ``times`` (their median gap)."""
    gaps = sorted((b - a).total_seconds() for a, b in zip(times, times[1:]) if b > a)
    if not gaps:
        return None
    return gaps[len(gaps) // 2]


def megabytes(count: int, width: int, height: int) -> str:
    """``60 MB``: about what ``count`` screenshots of the size take to download."""
    mb = count * width * height * PNG_BYTES_PER_PIXEL / 1e6
    return f"{mb:.0f} MB" if mb >= 10 else f"{mb:.1f} MB"


def plan_summary(name: str, plan: Plan, budget: Optional[int] = None) -> str:
    """``AIA 171 · 16:00–16:30 UTC · 50 images at 36 s · 60 MB``, with what
    Helioviewer said of the list and what will not fit in ``budget`` bytes of
    memory (the loader's)."""
    downloads = plan.downloads
    parts = [name, span_text(plan.start, plan.end)]
    count = len(downloads)
    if not count:
        parts.append("no images")
        return " · ".join(parts)
    images = f"{count} image" + ("s" if count != 1 else "")
    gap = typical_gap([frame.observed for frame in downloads])
    if gap:
        images += f" at {cadence_text(gap)}"
    repeats = len(plan.frames) - count
    if repeats:
        images += f" ({repeats} repeated at this step)"
    parts.append(images)
    parts.append(megabytes(count, plan.view.width, plan.view.height))
    if plan.message:
        if len(plan.frames) >= hv.MOST_JPX_FRAMES and gap:
            parts.append(f"Helioviewer gives at most {hv.MOST_JPX_FRAMES} frames: every {cadence_text(gap)}")
        else:
            parts.append(plan.message)
    note = memory_note(plan) if budget is None else memory_note(plan, budget)
    if note:
        parts.append(note)
    return " · ".join(parts)


def far_note(at: datetime, nearest: Optional[datetime]) -> str:
    """``Nearest image is 35 d earlier than asked`` when the archive's nearest
    image is over ``FAR_S`` from the time asked for, else ""."""
    if nearest is None:
        return ""
    offset = (nearest - at).total_seconds()
    if abs(offset) <= FAR_S:
        return ""
    size = offset_text(abs(offset))[1:]
    return f"Nearest image is {size} {'earlier' if offset < 0 else 'later'} than asked"


# ---------------------------------------------------------------- the opener


@dataclass
class Target:
    """What an opener knows: the time to centre on, the seconds each side,
    the radio image's field ``(x1, y1, x2, y2)`` in true arcsec, its mark on
    the band ``(label, time)``, the entry to select, and an OVRO-LWA day
    whose movie loads at once."""

    at: Optional[datetime] = None
    span: Optional[int] = None
    fov: Optional[Tuple[float, float, float, float]] = None
    mark: Optional[Tuple[str, datetime]] = None
    instrument: Optional[str] = None
    movie_day: Optional[date] = None
    mark_tip: str = ""


def open_helioviewer(parent=None, at=None, span=None, fov=None, mark=None, instrument=None, movie_day=None, mark_tip=""):
    """Open the browser on ``at`` (a naive UTC datetime), ``span`` seconds each
    side, boxing ``fov`` and marking ``mark``; with ``at`` the images load at
    once. Returns the window, shown; it frees its frames when closed."""
    target = Target(at=at, span=span, fov=fov, mark=mark, instrument=instrument, movie_day=movie_day, mark_tip=mark_tip)
    browser = HelioviewerBrowser(parent, target)
    browser.setAttribute(Qt.WA_DeleteOnClose)
    browser.show()
    return browser


def pane_time(parent) -> Optional[datetime]:
    """The observation time of the image pane the browser was opened from, or None."""
    from . import helioviewer_use as use

    pane = use.pane_of(parent)
    reader = getattr(pane, "_observation_datetime", None)
    if reader is None:
        return None
    try:
        when = reader()
    except Exception as e:
        logger.warning("Could not read the image's time: %s", e)
        return None
    if when is not None and not isinstance(when, datetime):
        when = datetime.combine(when, time())
    return when


# ---------------------------------------------------------------- threads


def fetch_sources() -> Tuple[List[catalog.Source], str]:
    """Helioviewer's full list, or the known one with why."""
    try:
        return catalog.catalogue(hv.data_sources()), ""
    except Exception as e:
        logger.warning("Could not fetch the instruments from Helioviewer: %s", e)
        return catalog.fallback_catalogue(), str(e) or "No answer"


class SourcesThread(QThread):
    done = pyqtSignal(object, str)  # sources, why they are only the known ones ("" if Helioviewer's)

    def run(self):
        sources, problem = fetch_sources()
        self.done.emit(sources, problem)


class _Cancelled(Exception):
    pass


class PlanThread(QThread):
    """Finds the frames of a load (``plan_frames``), off the GUI thread."""

    done = pyqtSignal(object, str)  # the plan, or None and why

    def __init__(self, source, start, end, step_s, view, parent=None):
        super().__init__(parent)
        self.source, self.start_time, self.end_time, self.step_s, self.view = source, start, end, step_s, view
        self._stop = threading.Event()

    def cancel(self):
        self._stop.set()

    def _check(self):
        if self._stop.is_set():
            raise _Cancelled()

    def run(self):
        try:
            plan = plan_frames(self.source, self.start_time, self.end_time, self.step_s, self.view, check=self._check)
        except _Cancelled:
            return
        except Exception as e:
            logger.warning("Could not plan the frames of %s: %s", self.source.name, e)
            self.done.emit(None, str(e) or "Helioviewer did not answer")
            return
        if not self._stop.is_set():
            self.done.emit(plan, "")


class RetryPlan(Plan):
    """A plan whose downloads are only the frames that failed or were never fetched."""

    def order(self, at=None):
        return [frame for frame in super().order(at) if frame.state in (FAILED, PLANNED)]


def lwa_frames(plan: lwa.Plan, view_name: str) -> Tuple[List[Frame], dict]:
    """OVRO-LWA's plan as frames, with the note each carries ("from the
    2024-05-14 movie"). A clip's unproved frames are spread over their
    movie's day, as nothing better is known of their times."""
    frames, notes = [], {}
    count = max(1, len(plan.times))
    for index, (when, day, number) in enumerate(zip(plan.times, plan.days, plan.numbers)):
        if when is None:
            when = datetime.combine(day, time()) + timedelta(seconds=86400 * (index + 0.5) / count)
            notes[index] = f"Time not confirmed · frame {number + 1} of the {day} movie"
        else:
            notes[index] = f"from the {day} movie"
        frames.append(
            Frame(
                index=index,
                wanted=when,
                observed=when,
                source_id=0,
                layers=lwa.LAYER + view_name,
                image_scale=1.0,
                width=plan.side or 600,
                height=plan.side or 600,
                origin=LWA_ORIGIN,
            )
        )
    return frames, notes


# ---------------------------------------------------------------- the band with a GOES lane


class HelioBand(FrameBand):
    """The time band with, under its lanes, the GOES 1–8 Å curve of the
    range's day(s) when Solar Activity has saved it (no fetch here)."""

    GOES = 44  # px: room for the curve and the class letters beside it

    def __init__(self, axis=None, parent=None, inset=SPACE_L):
        self.goes = {}  # date -> one value per minute, None in a gap
        super().__init__(axis, parent, inset)

    def set_goes(self, curves):
        """``{day: flux}`` for the days the range touches; empty for no lane."""
        self.goes = {day: list(flux) for day, flux in (curves or {}).items() if flux}
        self._set_height()

    def _set_height(self):
        super()._set_height()
        if getattr(self, "goes", None):
            self.setFixedHeight(self.height() + self.GOES)

    def _goes_points(self):
        """``[(hours, value)]`` of the curve inside the band's range, in order."""
        points = []
        for day in sorted(self.goes):
            base = (day - self.axis.day).days * 24
            for minute, value in enumerate(self.goes[day]):
                hours = base + minute / 60
                if value is not None and value > 0 and self.axis.first <= hours <= self.axis.last:
                    points.append((hours, value))
                elif points and points[-1] is not None:
                    points.append(None)  # a gap
        return points

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.goes:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        palette = theme_manager.palette
        left, right = self.axis.x_of(self.axis.start, self), self.axis.x_of(self.axis.end, self)
        top = self.height() - 2 - self.GOES
        painter.setFont(self._font(FONT_SMALL))
        painter.setPen(QColor(palette["text_secondary"]))
        painter.drawText(QRectF(self.inset, top, GUTTER - SPACE_S, self.LANE), Qt.AlignVCenter, "GOES 1–8 Å")
        points = self._goes_points()
        values = [v for p in points if p is not None for v in (p[1],)]
        area = QRectF(left, top, right - left, self.GOES)
        if not values:
            painter.drawText(area, Qt.AlignCenter, "No GOES data in this range")
            painter.end()
            return
        low = math.floor(math.log10(min(values)))
        high = max(math.ceil(math.log10(max(values))), low + 2)
        y_of = lambda value: top + self.GOES - 1 - (math.log10(value) - low) / (high - low) * (self.GOES - 2)
        painter.setFont(self._font(FONT_META, mono=True))
        last_label_y = None
        for name, power in (("A", -8), ("B", -7), ("C", -6), ("M", -5), ("X", -4)):
            if low <= power <= high:
                y = y_of(10.0 ** power)
                painter.setPen(QPen(QColor(palette["plot_grid"]), 1, Qt.DotLine))
                painter.drawLine(QPointF(left, y), QPointF(right, y))
                # The letter beside its line, kept inside the lane and clear of the one before
                label_y = min(max(y, top + 6), top + self.GOES - 6)
                if last_label_y is None or abs(label_y - last_label_y) >= 11:
                    painter.setPen(QColor(palette["text_faint"]))
                    painter.drawText(QRectF(left - 14, label_y - 7, 11, 14), Qt.AlignRight | Qt.AlignVCenter, name)
                    last_label_y = label_y
        painter.save()
        painter.setClipRect(area)
        painter.setPen(QPen(QColor(GOES_COLOUR), 1.2))
        run = []
        for point in points:
            if point is None:
                if run:
                    painter.drawPolyline(QPolygonF(run))
                run = []
            else:
                run.append(QPointF(self.axis.x_of(point[0], self), y_of(point[1])))
        if run:
            painter.drawPolyline(QPolygonF(run))
        painter.restore()
        painter.end()


# ---------------------------------------------------------------- the instrument list


class SourceTree(QTreeWidget):
    """The instruments, grouped by observatory. A Ctrl+click on an entry
    asks for it as the composite's second layer instead of selecting it."""

    overlay_picked = pyqtSignal(object)  # the item
    resized = pyqtSignal()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()

    def mousePressEvent(self, event):
        item = self.itemAt(event.pos())
        if event.button() == Qt.LeftButton and event.modifiers() & Qt.ControlModifier and item is not None and item.data(0, SOURCE_ROLE):
            self.overlay_picked.emit(item)
            return
        super().mousePressEvent(event)


class SourceDelegate(QStyledItemDelegate):
    """Group rows in the strong text, the second and third columns muted, an
    instrument without images of the day dimmed: by role, so a theme change
    repaints them without touching the items."""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        palette = theme_manager.palette
        if index.data(GROUP_ROLE):
            option.font.setBold(True)
            option.palette.setColor(option.palette.Text, QColor(palette["text_strong"]))
        elif index.data(DIM_ROLE):
            option.palette.setColor(option.palette.Text, QColor(palette["disabled"]))
        elif index.column() > 0:
            option.palette.setColor(option.palette.Text, QColor(palette["text_secondary"]))
        if index.column() == 2 and index.data(NOTE_ROLE):
            option.font.setPointSizeF(FONT_META)


# ---------------------------------------------------------------- the window


class HelioviewerBrowser(HelioviewerExportMixin, QMainWindow):
    """Images around a time: the header picks the time, span and step, the
    list the instrument, Load finds the frames and downloads them nearest
    the time first; the band seeks, the footer plays, compares, reframes,
    zooms and exports."""

    time_chosen = pyqtSignal(object)  # the user chose a time on the band (a datetime)

    def __init__(self, parent=None, target: Optional[Target] = None, loader: Optional[FrameLoader] = None, settings: Optional[QSettings] = None):
        super().__init__(parent)
        self.setWindowTitle("Helioviewer browser")
        self.target = target or Target()
        self.settings = settings if settings is not None else QSettings(*SETTINGS)
        # What is loaded
        self.sources: List[catalog.Source] = []
        self.instruments_problem = ""
        self.plan: Optional[Plan] = None
        self.frames: List[Frame] = []
        self.frame_notes = {}  # index -> what the stamp says after the time (OVRO-LWA's movie)
        self.current: Optional[int] = None
        self.loading = False
        self.lwa_mode = False  # the frames shown are OVRO-LWA's
        self.lwa_notes: List[str] = []
        self.region: Optional[hvw.Region] = None  # the reframed field, None for the full one
        self.overlay: Optional[Tuple[catalog.Source, int]] = None  # the composite's second layer and its opacity
        self.radio_fov = self.target.fov
        self.events_rows = None
        self.diff_mode_name = "None"
        self.diff_base_index = 0
        self._diff_cache: "OrderedDict[tuple, QPixmap]" = OrderedDict()
        self._compact = False
        self._footer_compact = False
        self._jobs = OrderedDict()
        self._closing = False
        self._close_after_export = False
        self._load_at: Optional[datetime] = None
        # Threads
        self.loader = loader if loader is not None else FrameLoader(parent=self)
        self.loader.frame_ready.connect(self._on_frame_ready)
        self.loader.frame_failed.connect(self._on_frame_failed)
        self.loader.progress.connect(self._on_progress)
        self.loader.finished.connect(self._on_finished)
        self.sources_thread: Optional[SourcesThread] = None
        self.planner: Optional[PlanThread] = None
        self.events_thread: Optional[EventsFetch] = None
        self.lwa_loader: Optional[lwa.LwaMovieLoader] = None

        self._build_ui()
        self._setup_shortcuts()
        themed(self, self._restyle)
        set_hand_cursor(self)
        fit_to_screen(self, 1600, 1000)
        self._restore_settings()
        self._apply_target()
        self.load_instruments()

    # ------------------------------------------------------------ building

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = self._build_header()
        layout.addWidget(self.header)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self._build_left())
        self.splitter.addWidget(self._build_image_column())
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([400, 1000])
        page = QVBoxLayout()
        page.setContentsMargins(SPACE_L, SPACE_S, SPACE_L, 0)
        page.setSpacing(SPACE_S)
        page.addWidget(self.splitter, 1)
        layout.addLayout(page, 1)

        self.footer = self._build_footer()
        layout.addWidget(self.footer)
        self.source_line = source_note(HELIOVIEWER_NOTE)
        self.source_line.setContentsMargins(SPACE_L, SPACE_XS, SPACE_L, SPACE_S)
        layout.addWidget(self.source_line)
        # The band takes the keys nothing else uses, so ] and [ step frames
        central.setFocusPolicy(Qt.ClickFocus)
        central.setFocusProxy(self.band)

    def _build_header(self):
        top = QFrame()
        top.setObjectName("DayHeader")
        # The window may be dragged narrower than the row's full labels: the
        # labels shorten (_fit_labels) and the window stops where the short ones end
        top.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        themed(top, lambda palette: top.setStyleSheet(
            f"QFrame#DayHeader {{ background-color: {palette['surface']}; border: none;"
            f" border-bottom: 1px solid {palette['divider']}; }}"
        ))
        column = QVBoxLayout(top)
        column.setContentsMargins(0, SPACE_M, 0, SPACE_S)
        column.setSpacing(SPACE_S)

        row = QHBoxLayout()
        row.setContentsMargins(SPACE_L, 0, SPACE_L, 0)
        row.setSpacing(SPACE_S)
        row.addWidget(self._day_button("fd_back.svg", "Day before (Alt+Left)", -1))
        self.time_edit = QLineEdit()
        self.time_edit.setToolTip("The time the images are around, UTC: YYYY-MM-DD HH:MM:SS")
        self.time_edit.setPlaceholderText("YYYY-MM-DD HH:MM:SS")
        font = QFont(self.time_edit.font())
        font.setFamily(MONO_FAMILY)
        self.time_edit.setFont(font)
        self.time_edit.setMinimumWidth(190)
        self.time_edit.setMaximumWidth(230)
        attach_date_picker(self.time_edit, lambda text: self._time_committed())
        self.time_edit.editingFinished.connect(self._time_committed)
        self.time_edit.returnPressed.connect(self.load)
        row.addWidget(self.time_edit)
        row.addWidget(self._day_button("fd_forward.svg", "Day after (Alt+Right)", 1))
        self.from_image_btn = QPushButton("From image")
        self.from_image_btn.setToolTip("Take the time from the image open in SolarViewer")
        self.from_image_btn.clicked.connect(self.from_image)
        self.from_image_btn.setVisible(self.parent() is not None)
        row.addWidget(self.from_image_btn)
        row.addSpacing(SPACE_XS)
        self.span_label = QLabel("Span")
        self.span_label.setObjectName("Hint")
        row.addWidget(self.span_label)
        self.span_group, self.span_buttons = self._choice(SPANS, "The images come from this far each side of the time")
        self.span_group.buttonClicked.connect(lambda _b: self._time_committed())
        row.addWidget(segmented(self.span_buttons))
        row.addSpacing(SPACE_XS)
        self.step_label = QLabel("Step")
        self.step_label.setObjectName("Hint")
        row.addWidget(self.step_label)
        self.step_group, self.step_buttons = self._choice(STEPS, "One frame per step, the archive's image nearest each; Every image keeps them all")
        row.addWidget(segmented(self.step_buttons))
        row.addStretch()
        column.addLayout(row)

        line = QHBoxLayout()
        line.setContentsMargins(SPACE_L, 0, SPACE_L, 0)
        line.setSpacing(SPACE_S)
        self.summary_label = ElidedLabel("")
        line.addWidget(self.summary_label, 1)
        self.activity_line = ActivityLabel()
        self.activity_line.label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.activity_line.hide()
        line.addWidget(self.activity_line, 1)
        self.note_link = link_button("")
        self.note_link.hide()
        line.addWidget(self.note_link)
        self.retry_link = link_button("Retry", self.load)
        self.retry_link.hide()
        line.addWidget(self.retry_link)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.cancel_latest_job)
        self.cancel_btn.hide()
        line.addWidget(self.cancel_btn)
        self.load_btn = QPushButton("Load")
        self.load_btn.setObjectName("PrimaryButton")
        self.load_btn.setToolTip("Find the images and download them (Ctrl+L, or Return outside a control)")
        self.load_btn.clicked.connect(self.load)
        line.addWidget(self.load_btn)
        column.addLayout(line)

        self._note_timer = QTimer(self)
        self._note_timer.setSingleShot(True)
        self._note_timer.timeout.connect(self._note_over)
        self._note_link_slot = None
        return top

    def _day_button(self, icon, tip, days):
        button = QPushButton()
        button.setObjectName("IconOnlyNBGButton")
        button.setIconSize(QSize(18, 18))
        button.setMinimumWidth(ICON_BUTTON_LEAST)
        button.setToolTip(tip)
        themed(button, lambda palette: button.setIcon(QIcon(themed_icon(icon))))
        button.clicked.connect(lambda: self.step_day(days))
        return button

    def _choice(self, rows, tip):
        """Checkable buttons for ``rows`` of (label, compact label, value) in one exclusive group."""
        group = QButtonGroup(self)
        group.setExclusive(True)
        buttons = []
        for i, (label, short, _value) in enumerate(rows):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setToolTip(tip)
            button.setProperty("labels", (label, short))
            group.addButton(button, i)
            buttons.append(button)
        buttons[0].setChecked(True)
        return group, buttons

    def _build_left(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, SPACE_S, 0)
        layout.setSpacing(SPACE_S)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter instruments (Ctrl+F)")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._filter_tree)
        layout.addWidget(self.filter_edit)

        self.tree = SourceTree()
        self.tree.setHeaderLabels(["Instrument", "Shows", "Cadence"])
        self.tree.setColumnCount(3)
        header = self.tree.header()
        # The name column is sized to the names (a group's spanned title would widen it): see _size_columns
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        header.setStretchLastSection(False)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        # The column of what an instrument shows gives way, cut short with an ellipsis, rather than a scrollbar
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tree.setTextElideMode(Qt.ElideRight)
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(SPACE_L)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setItemDelegate(SourceDelegate(self.tree))
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.overlay_picked.connect(self._overlay_picked)
        self.tree.setToolTip("Click an instrument; Ctrl+click another to lay it over as a second layer")
        self.tree.resized.connect(self._size_columns)
        self.lwa_group = None
        layout.addWidget(self.tree, 1)

        # Said when the list is not the one Helioviewer gave
        self.instruments_note = QWidget()
        note = QHBoxLayout(self.instruments_note)
        note.setContentsMargins(0, 0, 0, 0)
        note.setSpacing(SPACE_S)
        self.instruments_note_label = QLabel(
            "Helioviewer did not answer: these are the usual instruments, and may not all have images."
        )
        self.instruments_note_label.setObjectName("Hint")
        self.instruments_note_label.setWordWrap(True)
        note.addWidget(self.instruments_note_label, 1)
        note.addWidget(link_button("Retry", self.load_instruments), 0, Qt.AlignTop)
        self.instruments_note.hide()
        layout.addWidget(self.instruments_note)

        # The composite's second layer
        self.overlay_chip = QFrame()
        self.overlay_chip.setObjectName("Surface")
        chip = QHBoxLayout(self.overlay_chip)
        chip.setContentsMargins(9, 5, 7, 5)
        chip.setSpacing(SPACE_S)
        self.overlay_label = QLabel("")
        self.overlay_label.setObjectName("Hint")
        chip.addWidget(self.overlay_label)
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(20, 100)
        self.opacity_slider.setValue(OVERLAY_OPACITY)
        self.opacity_slider.setToolTip("How strongly the second layer shows (20–100 %)")
        self.opacity_slider.valueChanged.connect(self._opacity_changed)
        chip.addWidget(self.opacity_slider, 1)
        self.opacity_label = QLabel(f"{OVERLAY_OPACITY} %")
        self.opacity_label.setObjectName("DetailMono")
        chip.addWidget(self.opacity_label)
        self.overlay_remove = link_button("Remove", self.remove_overlay)
        chip.addWidget(self.overlay_remove)
        self.overlay_chip.hide()
        layout.addWidget(self.overlay_chip)

        layout.addSpacing(SPACE_XS)
        layout.addWidget(kicker("Field of view"))
        self.fov_group = QButtonGroup(self)
        self.fov_group.setExclusive(True)
        self.fov_buttons = []
        presets = list(catalog.FOV_PRESETS) + [("Radio map", None)]
        for i, (label, arcsec) in enumerate(presets):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setProperty("labels", (label, label))
            button.setToolTip(
                "The field of the radio image this browser was opened from" if arcsec is None
                else f"{hvw.arcsec_text(arcsec)} across ({arcsec / hv.RSUN_ARCSEC:.1f} solar radii)"
            )
            self.fov_group.addButton(button, i)
            self.fov_buttons.append(button)
        self.fov_buttons[0].setChecked(True)
        self.fov_buttons[-1].setVisible(self.radio_fov is not None)
        self.fov_row = segmented(self.fov_buttons)
        layout.addWidget(self.fov_row)
        self.fov_hint = QLabel("")
        self.fov_hint.setObjectName("Hint")
        self.fov_hint.setWordWrap(True)
        self.fov_hint.hide()
        layout.addWidget(self.fov_hint)

        layout.addSpacing(SPACE_XS)
        self.view = hvw.FrameView(settings=self.settings)
        self.toggles = hvw.OverlayToggles(self.view)
        self.toggles.set_radio_available(self.radio_fov is not None)
        layout.addWidget(self.toggles)
        return panel

    def _build_image_column(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(SPACE_S, 0, 0, 0)
        layout.setSpacing(SPACE_S)

        caption = QHBoxLayout()
        caption.setSpacing(SPACE_S)
        self.caption_name = QLabel("")
        self.caption_name.setObjectName("SectionTitle")
        caption.addWidget(self.caption_name)
        caption.addStretch()
        self.caption_time = QLabel("")
        self.caption_time.setObjectName("Mono")
        caption.addWidget(self.caption_time)
        self.caption_offset = QLabel("")
        self.caption_offset.setObjectName("DetailMono")
        self.caption_offset.setProperty("muted", "true")
        caption.addWidget(self.caption_offset)
        caption.addSpacing(SPACE_S)
        self.caption_count = QLabel("")
        self.caption_count.setObjectName("Meta")
        caption.addWidget(self.caption_count)
        layout.addLayout(caption)

        self.well = QStackedWidget()
        self.well.addWidget(self.view)
        self.message = QFrame()
        self.message.setObjectName("ImageWell")
        column = QVBoxLayout(self.message)
        column.setContentsMargins(SPACE_L, SPACE_L, SPACE_L, SPACE_L)
        column.addStretch()
        self.message_label = empty_note("")
        column.addWidget(self.message_label)
        self.message_activity = ActivityLabel()
        self.message_activity.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        self.message_activity.hide()
        column.addWidget(self.message_activity, 0, Qt.AlignCenter)
        links = QHBoxLayout()
        links.addStretch()
        self.details_link = link_button("Show details…", self._show_frame_error)
        self.details_link.hide()
        links.addWidget(self.details_link)
        self.frame_retry_link = link_button("Retry", self.retry_failed)
        self.frame_retry_link.hide()
        links.addWidget(self.frame_retry_link)
        links.addStretch()
        column.addLayout(links)
        column.addStretch()
        self.well.addWidget(self.message)
        layout.addWidget(self.well, 1)

        self.readout = hvw.Readout(self.view)
        layout.addWidget(self.readout)
        self.band = HelioBand()
        self.band.current_changed.connect(self.show_frame)
        self.band.cursor_set.connect(self._cursor_set)
        layout.addWidget(self.band)

        self.view.zoom_changed.connect(lambda _scale: self._zoom_changed())
        self.view.reframe_requested.connect(self._reframe_requested)
        self.view.reframe_ended.connect(self._reframe_ended)
        self.view.set_radio_field(self.radio_fov, self._radio_label())
        return panel

    def _build_footer(self):
        bar = QFrame()
        bar.setObjectName("CanvasBar")
        bar.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(SPACE_M, 6, SPACE_M, 6)
        layout.setSpacing(SPACE_S)

        self.transport_buttons = []
        buttons = []
        for attribute, kind, tip, slot in (
            ("first_btn", "first", "First frame (Shift+[, Home)", self.first_frame),
            ("prev_btn", "previous", "Previous frame ([)", self.prev_frame),
            ("play_btn", "play", "Play (P, Space)", self.toggle_play),
            ("next_btn", "next", "Next frame (])", self.next_frame),
            ("last_btn", "last", "Last frame (Shift+], End)", self.last_frame),
        ):
            button = QPushButton()
            button.setObjectName("IconOnlyNBGButton")
            button.setIconSize(QSize(16, 16))
            button.setMinimumWidth(ICON_BUTTON_LEAST)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            button.setEnabled(False)
            setattr(self, attribute, button)
            self.transport_buttons.append((button, kind))
            buttons.append(button)
        layout.addWidget(segmented(buttons))
        self.playback = Playback(self.band)
        self.playback.changed.connect(self._playing_changed)
        self.playback.fps_changed.connect(self._fps_changed)
        self.fps_group = QButtonGroup(self)
        self.fps_group.setExclusive(True)
        self.fps_buttons = []
        for fps in FPS_CHOICES:
            button = QPushButton(str(fps))
            button.setCheckable(True)
            button.setToolTip(f"{fps} frames a second (> and < change the speed)")
            self.fps_group.addButton(button, fps)
            self.fps_buttons.append(button)
        self.fps_group.buttonClicked.connect(lambda button: self.playback.set_fps(self.fps_group.id(button)))
        layout.addWidget(segmented(self.fps_buttons))
        self.fps_label = QLabel("fps")
        self.fps_label.setObjectName("Meta")
        layout.addWidget(self.fps_label)
        self._check_fps(self.playback.fps)

        layout.addWidget(vrule())
        self.diff_group = QButtonGroup(self)
        self.diff_group.setExclusive(True)
        self.diff_buttons = []
        for i, mode in enumerate(DIFF_MODES):
            button = QPushButton(mode)
            button.setCheckable(True)
            button.setProperty("labels", (mode, {"Base = radio time": "Base=radio", "Running": "Run"}.get(mode, mode)))
            self.diff_group.addButton(button, i)
            self.diff_buttons.append(button)
        self.diff_buttons[0].setChecked(True)
        self.diff_buttons[0].setToolTip("The frames as they are (D cycles the modes)")
        self.diff_buttons[1].setToolTip("Each frame minus the one before it")
        self.diff_buttons[2].setToolTip("Each frame minus a base frame")
        self.diff_buttons[3].setToolTip("Each frame minus the one nearest the radio image's time")
        self.diff_buttons[3].setVisible(self.target.mark is not None)
        self.diff_group.buttonClicked.connect(lambda button: self.set_diff_mode(DIFF_MODES[self.diff_group.id(button)]))
        self.diff_row = segmented(self.diff_buttons)
        layout.addWidget(self.diff_row)
        self.diff_hint = QLabel("")
        self.diff_hint.setObjectName("Hint")
        self.diff_hint.hide()
        layout.addWidget(self.diff_hint)
        self.set_base_link = link_button("Set base here", self.set_base_here)
        self.set_base_link.setToolTip("Make the frame shown the base the others are compared with")
        self.set_base_link.hide()
        layout.addWidget(self.set_base_link)
        self.reframe_btn = QPushButton("Reframe")
        self.reframe_btn.setProperty("labels", ("Reframe", "Region"))
        self.reframe_btn.setCheckable(True)
        self.reframe_btn.setToolTip("Drag a box on the image to load that region at full detail (G)")
        self.reframe_btn.toggled.connect(self._reframe_toggled)
        layout.addWidget(self.reframe_btn)
        self.full_field_link = link_button("Back to full field", self.back_to_full_field)
        self.full_field_link.setProperty("labels", ("Back to full field", "Full field"))
        self.full_field_link.hide()
        layout.addWidget(self.full_field_link)
        layout.addStretch()

        self.zoom_buttons = []
        cluster = []
        for icon, tip, slot in (("zoom_in.png", "Zoom in (+)", self.zoom_in),):
            cluster.append(self._zoom_button(icon, tip, slot))
        self.zoom_label = QLabel("—")
        self.zoom_label.setObjectName("Meta")
        self.zoom_label.setAlignment(Qt.AlignCenter)
        self.zoom_label.setMinimumWidth(self.zoom_label.fontMetrics().horizontalAdvance("0000%"))
        self.zoom_label.setToolTip("Image pixels to screen pixels")
        cluster.append(self.zoom_label)
        cluster.append(self._zoom_button("zoom_out.png", "Zoom out (-)", self.zoom_out))
        cluster.append(self._zoom_button("reset.png", "Fit the frame to the window (R)", self.fit))
        layout.addWidget(segmented(cluster))

        self.save_btn = QPushButton("Save frame")
        self.save_btn.setProperty("labels", ("Save frame", "Save"))
        self.save_btn.setToolTip("Save the frame shown as a PNG, with a JSON record of what it is (Ctrl+S)")
        self.save_btn.clicked.connect(self.save_current_frame)
        layout.addWidget(self.save_btn)
        self.export_btn = QPushButton("Export…")
        self.export_btn.setProperty("labels", ("Export…", "Export"))
        self.export_btn.setToolTip("The frames as an MP4, a GIF or PNG files (Ctrl+E)")
        self.export_btn.clicked.connect(lambda: self.export_animation())
        layout.addWidget(self.export_btn)
        self.use_btn = QPushButton("Use in viewer")
        self.use_btn.setProperty("labels", ("Use in viewer", "Use"))
        self.use_btn.setToolTip("The frame shown as a FITS file in SolarViewer")
        self.use_btn.setMenu(self.use_in_viewer_menu())
        layout.addWidget(self.use_btn)
        self.link_btn = QPushButton("Copy Helioviewer link")
        self.link_btn.setProperty("labels", ("Copy Helioviewer link", "Copy link"))
        self.link_btn.setToolTip("The same view on helioviewer.org, to the clipboard")
        self.link_btn.clicked.connect(self.copy_helioviewer_link)
        layout.addWidget(self.link_btn)
        return bar

    def _zoom_button(self, icon, tip, slot):
        button = QPushButton()
        button.setObjectName("IconOnlyNBGButton")
        button.setIconSize(QSize(18, 18))
        button.setMinimumWidth(ICON_BUTTON_LEAST)
        button.setToolTip(tip)
        button.clicked.connect(slot)
        self.zoom_buttons.append((button, icon))
        return button

    def _setup_shortcuts(self):
        # Space and Return are not window shortcuts: a focused button, switch
        # or field keeps them (see keyPressEvent for when nothing does); the
        # band's own Space and < > reach Playback directly
        for keys, slot in (
            ("P", self.toggle_play),
            ("]", self.next_frame),
            ("[", self.prev_frame),
            ("Shift+]", self.last_frame),
            ("Shift+[", self.first_frame),
            ("Home", self.first_frame),
            ("End", self.last_frame),
            ("Alt+]", lambda: self.band.step_flare(1)),
            ("Alt+[", lambda: self.band.step_flare(-1)),
            ("+", self.zoom_in),
            ("=", self.zoom_in),
            ("-", self.zoom_out),
            ("R", self.fit),
            (">", lambda: self.playback.step_fps(1)),
            (".", lambda: self.playback.step_fps(1)),
            ("<", lambda: self.playback.step_fps(-1)),
            (",", lambda: self.playback.step_fps(-1)),
            ("D", self.cycle_diff_mode),
            ("G", self.reframe_btn.toggle),
            ("Ctrl+S", self.save_current_frame),
            ("Ctrl+E", lambda: self.export_animation()),
            ("Ctrl+L", self.load),
            ("Ctrl+F", lambda: (self.filter_edit.setFocus(), self.filter_edit.selectAll())),
            ("Alt+Left", lambda: self.step_day(-1)),
            ("Alt+Right", lambda: self.step_day(1)),
            ("Esc", self._escape),
            ("Ctrl+Q", self.close),
        ):
            QShortcut(QKeySequence(keys), self).activated.connect(slot)

    def keyPressEvent(self, event):
        """Space plays and Return loads when no control took the key (a
        focused button, switch or field keeps its own)."""
        focus = QApplication.focusWidget()
        # A button or a field lets Return through to here: the button's is not a Load, the field's loaded already
        taken = isinstance(focus, (QAbstractButton, QLineEdit))
        if event.key() == Qt.Key_Space and event.modifiers() == Qt.NoModifier:
            self.toggle_play()
            event.accept()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() in (Qt.NoModifier, Qt.KeypadModifier) and not taken:
            self.load()
            event.accept()
        else:
            super().keyPressEvent(event)

    def _restyle(self, palette):
        """What holds a colour of its own: the icons."""
        for button, kind in self.transport_buttons:
            button.setIcon(transport_icon(kind, palette))
        self.play_btn.setIcon(transport_icon("pause" if self.playback.playing else "play", palette))
        for button, icon in self.zoom_buttons:
            button.setIcon(QIcon(themed_icon(icon)))
        self.tree.viewport().update()

    def _restore_settings(self):
        span = self.settings.value(KEY.format("span"), 3600)
        step = self.settings.value(KEY.format("step"), "")
        self._check_choice(self.span_group, SPANS, None if span in ("", None, "None") else int(span))
        self._check_choice(self.step_group, STEPS, None if step in ("", None, "None") else int(step))
        fps = self.settings.value(KEY.format("fps"), 4, type=int)
        self.playback.set_fps(fps)
        sizes = self.settings.value(KEY.format("splitter"))
        if sizes:
            try:
                self.splitter.setSizes([int(s) for s in sizes])
            except (TypeError, ValueError):
                pass

    @staticmethod
    def _check_choice(group, rows, value):
        for i, (_label, _short, held) in enumerate(rows):
            if held == value:
                group.button(i).setChecked(True)
                return

    def _apply_target(self):
        """The opener's time, span, mark and instrument."""
        target = self.target
        at = target.at
        if at is None and target.movie_day is not None:
            at = datetime.combine(target.movie_day, time(12))
            self._check_choice(self.span_group, SPANS, None)
        if at is None:
            at = datetime.now(timezone.utc).replace(tzinfo=None, second=0, microsecond=0)
        if target.span is not None:
            # The span first: the time sets the band's range from it
            nearest = min((row for row in SPANS if row[2] is not None), key=lambda row: abs(row[2] - target.span))
            self._check_choice(self.span_group, SPANS, nearest[2])
        self.set_time(at)
        if target.mark is not None:
            label, when = target.mark
            tip = f"{target.mark_tip} · {when:%Y-%m-%d %H:%M:%S} UTC" if target.mark_tip else ""
            self.band.set_mark(label, when, tip)
        self.diff_buttons[3].setVisible(target.mark is not None)
        self._show_empty()
        if target.movie_day is not None:
            # OVRO-LWA's movie needs nothing from Helioviewer: it loads now
            self.select_named(target.instrument or lwa.ALL_BANDS) or self.select_named(lwa.ALL_BANDS)
            QTimer.singleShot(0, self.load)

    # ------------------------------------------------------------ the activity line

    def begin_job(self, key, text, cancel):
        """Show ``text`` until ``end_job(key)``; ``cancel()`` is what Cancel calls."""
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
        self.load_btn.setEnabled("plan" not in self._jobs)
        if self._note_timer.isActive():
            pass  # a note has the line for now
        elif running:
            self.activity_line.start(list(self._jobs.values())[-1][0])
            self.activity_line.show()
            self.note_link.hide()
        else:
            self.activity_line.stop()
            self.activity_line.hide()
            self.note_link.hide()
        self.summary_label.setVisible(self.activity_line.isHidden())

    def cancel_latest_job(self):
        if not self._jobs:
            return
        key = list(self._jobs)[-1]
        cancel = self._jobs[key][1]
        self._jobs.pop(key)
        cancel()
        self._show_jobs()  # (Cancel goes, Load comes back, whatever the cancel said)

    def say(self, text, link=None):
        """A short note in the activity line, gone after a few seconds;
        ``link`` is ``(label, callable)`` for a link after it."""
        self.activity_line.stop(text)
        self.activity_line.show()
        if link is not None:
            label, slot = link
            self.note_link.setText(label)
            if self._note_link_slot is not None:
                self.note_link.clicked.disconnect(self._note_link_slot)
            self._note_link_slot = slot
            self.note_link.clicked.connect(slot)
            self.note_link.show()
        else:
            self.note_link.hide()
        self._note_timer.start(LINK_NOTE_MS if link else NOTE_MS)
        self.summary_label.hide()

    def _note_over(self):
        if self._note_link_slot is not None:
            try:
                self.note_link.clicked.disconnect(self._note_link_slot)
            except TypeError:
                pass
            self._note_link_slot = None
        self._show_jobs()

    def set_summary(self, text, tone=None, retry=False):
        """The line under the time: what is loaded, or what went wrong (``tone`` error or warning)."""
        self.summary_label.setText(text)
        self.summary_label.setToolTip(text)
        set_tone(self.summary_label, tone)
        self.retry_link.setVisible(retry)

    # ------------------------------------------------------------ the time

    def at_time(self) -> Optional[datetime]:
        """The time in the field, or None (and a note) if it does not read."""
        when = parse_field(self.time_edit.text())
        if when is None:
            self.say("Enter the time as YYYY-MM-DD HH:MM:SS, UTC")
            self.time_edit.setFocus()
            self.time_edit.selectAll()
        return when

    def set_time(self, when: datetime):
        self.time_edit.setText(when.strftime(TIME_FORMAT))
        self._time_committed()

    @staticmethod
    def _time_of(field):
        """The field's text as a UTC QDateTime (invalid if it does not parse)."""
        value = QDateTime.fromString(field.text().strip() + " UTC", TIME_QT_FORMAT + " t")
        return value.toUTC() if value.isValid() else value

    def _time_committed(self):
        when = parse_field(self.time_edit.text())
        if when is None:
            return
        self._refresh_tree_dates(when.date())
        if not self.frames and not self.loading:
            # The band shows the range the next Load would cover
            self.band.set_range(*self.time_range(when))
            self._show_empty()

    def step_day(self, days):
        when = parse_field(self.time_edit.text())
        if when is None:
            self.at_time()
            return
        try:
            self.set_time(when + timedelta(days=days))
        except OverflowError:
            self.say(f"There is no date {'before' if days < 0 else 'after'} {when:%Y-%m-%d}")

    def from_image(self):
        when = pane_time(self.parent())
        if when is None:
            self.say("The image open in SolarViewer has no observation time")
            return
        self.set_time(when)
        self.say(f"Time taken from the image: {when:%Y-%m-%d %H:%M:%S} UTC")

    def span_seconds(self) -> Optional[int]:
        return SPANS[self.span_group.checkedId()][2]

    def step_seconds(self) -> Optional[int]:
        return STEPS[self.step_group.checkedId()][2]

    def time_range(self, at: datetime) -> Tuple[datetime, datetime]:
        """The range the span makes about ``at`` (Day: the whole UTC day)."""
        span = self.span_seconds()
        if span is None:
            start = datetime.combine(at.date(), time())
            return start, start + timedelta(days=1)
        return at - timedelta(seconds=span), at + timedelta(seconds=span)

    # ------------------------------------------------------------ the instruments

    def load_instruments(self):
        """Ask Helioviewer for its instruments, on a thread: the window opens
        at once, with a spinner in the list until they are in."""
        self.instruments_problem = ""
        self.instruments_note.hide()
        if self.sources_thread is not None:  # asked again: the note's Retry
            _park(self.sources_thread)
        self._clear_helioviewer_items()
        self.sources = []
        row = QTreeWidgetItem()
        self.tree.insertTopLevelItem(0, row)
        row.setFlags(Qt.NoItemFlags)
        row.setFirstColumnSpanned(True)
        self.instruments_status = ActivityLabel(parent=self.tree)
        self.instruments_status.start("Loading the instruments…")
        self.tree.setItemWidget(row, 0, self.instruments_status)
        self.sources_thread = SourcesThread()
        self.sources_thread.done.connect(lambda sources, problem, t=self.sources_thread: self._on_sources(t, sources, problem))
        self.sources_thread.start()

    def _on_sources(self, thread, sources, problem):
        if self._closing or thread is not self.sources_thread:
            return
        _park(thread)
        self._clear_helioviewer_items()
        self.sources = list(sources)
        if problem:
            self.instruments_problem = problem
            self.instruments_note_label.setToolTip(problem)
            self.instruments_note.show()
        for observatory, group in catalog.group_by_observatory(self.sources):
            item = QTreeWidgetItem()
            item.setText(0, observatory)
            item.setToolTip(0, catalog.OBSERVATORY_NAMES.get(observatory, observatory))
            item.setData(0, GROUP_ROLE, True)
            item.setFlags(Qt.ItemIsEnabled)
            for source in group:
                child = QTreeWidgetItem()
                child.setText(0, source.name)
                child.setText(1, source.shows)
                child.setText(2, cadence_text(source.cadence))
                child.setData(0, SOURCE_ROLE, source)
                child.setToolTip(0, f"{source.name}\n{source.observatory_name}\nImages usually {cadence_text(source.cadence)} apart")
                item.addChild(child)
            place = self.tree.topLevelItemCount()
            if self.lwa_group is not None:  # it stays last
                place = self.tree.indexOfTopLevelItem(self.lwa_group)
            self.tree.insertTopLevelItem(place, item)
            item.setFirstColumnSpanned(True)
        if self.lwa_group is None:
            self._add_lwa_group()
        self.tree.expandAll()
        self._size_columns()
        when = parse_field(self.time_edit.text())
        if when is not None:
            self._refresh_tree_dates(when.date())
        self._filter_tree(self.filter_edit.text())
        if not self.tree.selectedItems():
            wanted = self.target.instrument or self.settings.value(KEY.format("instrument"), DEFAULT_INSTRUMENT)
            self.select_named(wanted) or self.select_named(DEFAULT_INSTRUMENT)
        if self.tree.selectedItems():
            self.tree.scrollToItem(self.tree.selectedItems()[0])
        if self.target.at is not None and self.plan is None and not self.loading and not self.lwa_mode:
            QTimer.singleShot(0, self.load)  # opened on a time: its images come at once

    def _size_columns(self):
        """The three columns in the list's width: the name column as wide as
        the longest name, but never so wide that what an instrument shows or
        when it has images is cut off (a long name is elided; its tooltip has
        it whole)."""
        metrics = self.tree.fontMetrics()
        header = self.tree.header()
        widest = max((metrics.horizontalAdvance(item.text(0)) for _group, item in self._items()), default=80)
        wanted = widest + 2 * self.tree.indentation() + SPACE_L
        third = max((metrics.horizontalAdvance(item.text(2)) for _group, item in self._items()), default=40) + SPACE_L
        room = self.tree.viewport().width() or self.tree.width()
        if room > 0:
            wanted = min(wanted, max(120, room - third - SHOWS_LEAST))
        header.resizeSection(0, wanted)
        header.resizeSection(2, third)

    def _clear_helioviewer_items(self):
        for i in reversed(range(self.tree.topLevelItemCount())):
            if self.tree.topLevelItem(i) is not self.lwa_group:
                self.tree.takeTopLevelItem(i)

    def _add_lwa_group(self):
        """OVRO-LWA's daily movie: the montage of its 12 bands, and each band."""
        group = QTreeWidgetItem()
        group.setText(0, f"{lwa.OBSERVATORY} · not from Helioviewer")
        group.setToolTip(
            0,
            "Owens Valley Radio Observatory Long Wavelength Array: its daily movie, from the "
            "OVRO-LWA portal (NJIT), not from Helioviewer. Each frame shows at the time it was taken.",
        )
        group.setData(0, GROUP_ROLE, True)
        group.setFlags(Qt.ItemIsEnabled)
        for entry in lwa.entries():
            item = QTreeWidgetItem()
            item.setText(0, entry[0])
            item.setText(1, "12 bands, 34–84 MHz" if lwa.view_of(entry) == lwa.MONTAGE else "Radio, one band")
            item.setText(2, "10–60 s")
            item.setData(0, SOURCE_ROLE, entry)
            item.setToolTip(0, f"{entry[0]}\nFrom OVRO-LWA's daily movie, not from Helioviewer.\nFrames come every 10 to 60 s, with gaps.")
            group.addChild(item)
        self.tree.addTopLevelItem(group)
        group.setFirstColumnSpanned(True)
        group.setExpanded(True)
        self.lwa_group = group
        self._size_columns()

    def _items(self):
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            for j in range(group.childCount()):
                yield group, group.child(j)

    def _refresh_tree_dates(self, day: date):
        """Instruments without images of ``day`` are dimmed, with when they have them."""
        for _group, item in self._items():
            source = item.data(0, SOURCE_ROLE)
            if not isinstance(source, catalog.Source):
                continue
            note = source.date_note(day)
            item.setData(0, DIM_ROLE, bool(note))
            item.setData(1, DIM_ROLE, bool(note))
            item.setData(2, DIM_ROLE, bool(note))
            item.setData(2, NOTE_ROLE, bool(note))
            item.setText(2, note or cadence_text(source.cadence))
            item.setToolTip(2, f"Images {note}" if note else "")
        self._size_columns()
        self.tree.viewport().update()

    def _filter_tree(self, text):
        words = text.lower().split()
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            shown = 0
            for j in range(group.childCount()):
                item = group.child(j)
                haystack = " ".join((item.text(0), item.text(1), group.text(0), group.toolTip(0))).lower()
                hide = any(word not in haystack for word in words)
                item.setHidden(hide)
                shown += not hide
            group.setHidden(bool(words) and shown == 0 and group.childCount() > 0)

    def _entry_named(self, name):
        for _group, item in self._items():
            data = item.data(0, SOURCE_ROLE)
            held = data.name if isinstance(data, catalog.Source) else (data[0] if data else None)
            if held == name:
                return item
        return None

    def select_named(self, name) -> bool:
        """Select the entry ``name``; Helioviewer's are there once they load."""
        if self._entry_named(name) is None and name in {entry[0] for entry in lwa.entries()}:
            self._add_lwa_group()
        item = self._entry_named(name)
        if item is None:
            return False
        self.tree.clearSelection()
        item.setSelected(True)
        self.tree.scrollToItem(item)
        return True

    def selected_source(self):
        """The ``Source`` selected, or OVRO-LWA's entry tuple, or None."""
        for item in self.tree.selectedItems():
            data = item.data(0, SOURCE_ROLE)
            if data:
                return data
        return None

    def selected_name(self) -> str:
        source = self.selected_source()
        if source is None:
            return ""
        return source.name if isinstance(source, catalog.Source) else source[0]

    def _selection_changed(self):
        source = self.selected_source()
        if source is None:
            return
        is_lwa = not isinstance(source, catalog.Source)
        self.settings.setValue(KEY.format("instrument"), self.selected_name())
        self.fov_row.setEnabled(not is_lwa)
        self.fov_hint.setText("The movie has its own field" if is_lwa else "")
        self.fov_hint.setVisible(is_lwa)
        for button in self.diff_buttons:
            button.setEnabled(not is_lwa)
        self.diff_row.setToolTip(
            "Not for OVRO-LWA: its frames switch between original and refraction corrected images, "
            "so a difference would show the switch, not the Sun" if is_lwa else ""
        )
        self.reframe_btn.setEnabled(not is_lwa)
        if is_lwa and self.diff_mode_name != "None":
            self.set_diff_mode("None")
        if not is_lwa:
            self._fov_for_source(source)
            if self.overlay is not None and self.overlay[0].source_id == source.source_id:
                self.remove_overlay()

    # ------------------------------------------------------------ the field of view and the second layer

    def _fov_for_source(self, source):
        """The preset nearest the instrument's own field."""
        wanted = source.fov
        if self.overlay is not None:
            wanted = max(wanted, self.overlay[0].fov)
        choices = [(i, arcsec) for i, (_label, arcsec) in enumerate(catalog.FOV_PRESETS)]
        best = min(choices, key=lambda pair: abs(math.log(pair[1] / wanted)))
        self.fov_buttons[best[0]].setChecked(True)

    def _radio_label(self) -> str:
        if self.target.mark is not None:
            return f"Radio map {self.target.mark[1]:%H:%M}"
        return "Radio map"

    def request_field(self) -> Tuple[float, float, float, Optional[float]]:
        """``(fov across, x0, y0, height across or None)`` of the request, in
        Helioviewer's arcsec: the preset, the radio image's field, or the
        reframed region."""
        if self.region is not None:
            return self.region.width, self.region.x0, self.region.y0, self.region.height
        chosen = self.fov_group.checkedId()
        if chosen == len(catalog.FOV_PRESETS) and self.radio_fov is not None:
            x1, y1, x2, y2 = self.radio_fov
            return max(abs(x2 - x1), abs(y2 - y1)), (x1 + x2) / 2, (y1 + y2) / 2, None
        return catalog.FOV_PRESETS[chosen][1], 0.0, 0.0, None

    def frame_width(self) -> int:
        """The pixels a frame is asked at: the well's width (the view's
        viewport is not laid out until the first frame shows), 256 to 1200."""
        width_px = self.well.width() - 2 if self.well.width() > 2 else (self.view.viewport().width() or 1024)
        return max(256, min(width_px, MOST_WIDTH))

    def request_view(self) -> View:
        """The view of the next load: the field across the well's width (at most 1200 px)."""
        width_px = self.frame_width()
        fov, x0, y0, height = self.request_field()
        height_px = None if height is None else max(1, round(width_px * height / fov))
        return View.of_fov(fov, width_px, x0, y0, height_px)

    def _overlay_picked(self, item):
        source = item.data(0, SOURCE_ROLE)
        primary = self.selected_source()
        if not isinstance(source, catalog.Source) or not isinstance(primary, catalog.Source):
            self.say("A second layer must be one of Helioviewer's instruments")
            return
        if source.source_id == primary.source_id:
            self.say("That is the layer shown already: Ctrl+click another instrument")
            return
        self.overlay = (source, self.opacity_slider.value())
        self.overlay_label.setText(f"Overlay: {source.name}")
        self.overlay_chip.show()
        self._fov_for_source(primary)
        self.say(f"{source.name} laid over {primary.name} at {self.overlay[1]} % · press Load")

    def remove_overlay(self):
        self.overlay = None
        self.overlay_chip.hide()
        primary = self.selected_source()
        if isinstance(primary, catalog.Source):
            self._fov_for_source(primary)

    def _opacity_changed(self, value):
        self.opacity_label.setText(f"{value} %")
        if self.overlay is not None:
            self.overlay = (self.overlay[0], int(value))

    def layers(self, source: catalog.Source) -> str:
        """The request's layer string: the instrument, and the second layer over it."""
        if self.overlay is None:
            return source.layer
        other, opacity = self.overlay
        return f"{source.layer},[{other.source_id},1,{opacity}]"

    # ------------------------------------------------------------ loading

    def load(self):
        """Find the images around the time and download them, nearest first."""
        at = self.at_time()
        if at is None:
            return
        source = self.selected_source()
        if source is None:
            self.say("Choose an instrument first")
            self.tree.setFocus()
            return
        self.settings.setValue(KEY.format("span"), self.span_seconds() if self.span_seconds() is not None else "")
        self.settings.setValue(KEY.format("step"), self.step_seconds() if self.step_seconds() is not None else "")
        start, end = self.time_range(at)
        self._stop_everything()
        self._load_at = at
        self.retry_link.hide()
        if isinstance(source, catalog.Source):
            self._load_helioviewer(source, at, start, end)
        else:
            self._load_lwa(source, start, end)

    def _stop_everything(self):
        """A new load: whatever was running for the last one stops."""
        self.playback.pause()
        if self.planner is not None:
            self.planner.cancel()
            _park(self.planner)
            self.planner = None
        if self.loader.busy:
            self.loader.cancel()
        if self.lwa_loader is not None:
            self._abandon_lwa(self.lwa_loader)
            self.lwa_loader = None
        if self.events_thread is not None:
            _park(self.events_thread)
            self.events_thread = None
        self.end_job("frames")
        self.end_job("plan")
        self.loading = False
        self._diff_cache.clear()

    def _load_helioviewer(self, source, at, start, end):
        self.lwa_mode = False
        view = self.request_view()
        self.begin_job("plan", f"Finding {source.name} images…", self._cancel_plan)
        self.set_summary(f"{source.name} · {span_text(start, end)}")
        planner = PlanThread(source, start, end, self.step_seconds(), view)
        planner.done.connect(lambda plan, error, t=planner: self._on_planned(t, source, at, plan, error))
        self.planner = planner
        planner.start()

    def _cancel_plan(self):
        if self.planner is not None:
            self.planner.cancel()
            _park(self.planner)
            self.planner = None
        self.say("Cancelled")

    def _on_planned(self, thread, source, at, plan, error):
        if self._closing or thread is not self.planner:
            return
        _park(thread)  # it is still ending its run: kept until then, never destroyed running
        self.planner = None
        self.end_job("plan")
        if plan is None:
            # The one failure that stops everything: the summary says so in the
            # error tone with Retry, the well keeps the details, and the app's
            # error dialog names it once
            text = f"Helioviewer did not answer: the list of {source.name} images could not be fetched. Check the connection and try again."
            self.set_summary("Helioviewer did not answer", tone="error", retry=True)
            self._show_message(text, details=error, retry=True)
            self._enable_transport(False)
            self.plan = None  # Retry plans again rather than downloading an older plan's frames
            show_error(self, "Helioviewer did not answer", text, error)
            return
        layers = self.layers(source)
        for frame in plan.frames:
            frame.layers = layers
        self.plan = plan
        self.frames = plan.frames
        self.frame_notes = {}
        self.lwa_notes = []
        self.current = None
        self.diff_base_index = 0
        self.view.reset_limb_check()
        self.view.set_observer(source.observatory)
        self.toggles.set_earth_view(self.view.earth_view, source.observatory_name)
        self.caption_name.setText(f"{source.name} · {source.observatory_name}")
        self.caption_name.setToolTip(f"Overlay: {self.overlay[0].name} at {self.overlay[1]} %, the archive's image nearest each time" if self.overlay else "")
        set_source_note(self.source_line, HELIOVIEWER_NOTE)
        self._leave_lwa_look()
        self.band.set_plan(plan)
        self._load_goes(plan.start, plan.end)
        self._fetch_events(plan.start, plan.end)
        if not plan.frames:
            self.set_summary(f"No {source.name} images between {span_text(plan.start, plan.end)}" + (f" · {plan.message}" if plan.message else ""), tone="warning")
            self._enable_transport(False)
            self._show_message(f"Helioviewer has no {source.name} images in this range.")
            return
        nearest = plan.nearest(at)
        summary = plan_summary(source.name, plan, self.loader.memory_bytes)
        far = far_note(at, nearest.observed if nearest is not None else None)
        self.set_summary(f"{summary} · {far}" if far else summary, tone="warning" if far else None)
        self._enable_transport(True)
        self.loading = True
        self.begin_job("frames", f"Downloading 0 of {len(plan.downloads)}", self.cancel_loading)
        self.loader.start(plan, at=at)
        self.band.set_current(nearest.index if nearest is not None else 0)
        if self.current is None:
            self.show_frame(nearest.index if nearest is not None else 0)
        self.full_field_link.setVisible(self.region is not None)
        self._fit_labels()

    def retry_failed(self):
        """Download again the frames that failed."""
        if self.plan is None or self.lwa_mode:
            self.load()
            return
        plan = self.plan
        retry = RetryPlan(source=plan.source, start=plan.start, end=plan.end, step_s=plan.step_s, view=plan.view, frames=plan.frames, message=plan.message, by_closest=plan.by_closest, scale_correction=plan.scale_correction, closest=plan.closest)
        for frame in plan.frames:
            if frame.state == FAILED:
                frame.state, frame.error = PLANNED, ""
        if not retry.downloads:
            return
        self.loading = True
        self.begin_job("frames", f"Downloading 0 of {len(retry.downloads)}", self.cancel_loading)
        self.loader.start(retry, at=self._load_at)
        self.band.refresh()
        if self.current is not None:
            self.show_frame(self.current)

    def cancel_loading(self):
        """Stop the downloads; the frames in are kept."""
        if self.lwa_loader is not None:
            self._abandon_lwa(self.lwa_loader)
            self.lwa_loader = None
            self._lwa_over(cancelled=True)
            return
        if self.loader.busy:
            self.loader.cancel()  # its finished(True) says the rest

    def _on_frame_ready(self, frame):
        if self._closing or self.lwa_mode:
            return
        self.band.refresh()
        if self.current is None:
            self.band.set_current(frame.index)
        elif frame.index == self.current or frame.index == self._reference_of(self.current):
            self.show_frame(frame.index if frame.index == self.current else self.current)

    def _reference_of(self, index) -> Optional[int]:
        """The frame ``index`` is compared with in the difference mode on, or None."""
        mode = self.diff_mode()
        if mode == "None" or index is None:
            return None
        return self.diff_base_index if mode == "Base" else index - 1

    def _on_frame_failed(self, frame):
        if self._closing or self.lwa_mode:
            return
        self.band.refresh()
        if frame.index == self.current:
            self.show_frame(frame.index)

    def _on_progress(self, done, failed, total):
        if self._closing or self.lwa_mode:
            return
        text = f"Downloading {done + failed} of {total}"
        if failed:
            text += f" · {failed} failed"
        self.update_job("frames", text)

    def _on_finished(self, cancelled):
        if self._closing or self.lwa_mode:
            return
        self.loading = False
        self.end_job("frames")
        ready = sum(1 for frame in self.frames if frame.state == READY and frame.duplicate_of is None)
        failed = sum(1 for frame in self.frames if frame.state == FAILED and frame.duplicate_of is None)
        total = len(self.plan.downloads) if self.plan is not None else len(self.frames)
        if cancelled:
            self.say(f"Cancelled · {ready} frame{'s' if ready != 1 else ''} kept")
        else:
            text = f"Loaded {ready} of {total}"
            if failed:
                text += f" · {failed} failed"
            self.say(text, ("Retry", self.retry_failed) if failed else None)
        self.band.refresh()
        if self.current is not None:
            self.show_frame(self.current)

    def _ready(self, index) -> bool:
        if index is None or not 0 <= index < len(self.frames):
            return False
        return self.band.is_ready(index)

    # ------------------------------------------------------------ events and GOES

    def _fetch_events(self, start, end):
        self.events_rows = None
        self.view.set_events([])
        thread = EventsFetch(start, end)  # no parent: it may outlive the window, parked until it ends
        thread.done.connect(lambda rows, error, t=thread: self._events_came(t, rows, error))
        self.events_thread = thread
        thread.start()

    def _events_came(self, thread, rows, error):
        if self._closing or thread is not self.events_thread:
            return
        _park(thread)
        self.events_thread = None
        if error:
            self.band.set_events_note(f"The flares could not be fetched: {error}")
            return
        self.events_rows = rows
        self.band.set_events(rows)
        self.view.set_events(rows)

    def _load_goes(self, start, end):
        """Solar Activity's saved GOES curves of the days the range touches, if any."""
        curves = {}
        day = start.date()
        while day <= end.date():
            try:
                flux = noaa_cache._noaa_cache.load_goes_day(day)
            except Exception as e:  # a lane, never in the way of the load
                logger.info("No GOES curve for %s: %s", day, e)
                flux = None
            if flux:
                curves[day] = flux
            day += timedelta(days=1)
        self.band.set_goes(curves)

    # ------------------------------------------------------------ OVRO-LWA

    def _load_lwa(self, entry, start, end):
        """OVRO-LWA's frames between ``start`` and ``end`` (or all of the
        opener's movie day), at their own times, by a thread of its own."""
        movie_day, self.target.movie_day = self.target.movie_day, None
        self.lwa_mode = True
        self.plan = None
        self.loader.release()  # Helioviewer's frames go: the movie has a budget of its own
        self.frames = []
        self.frame_notes = {}
        self.lwa_notes = []
        self.current = None
        self.band.set_frames([])
        self.band.set_goes({})
        self.band.set_events_note("")
        self.view.set_events([])
        self.view.set_frame(None)
        self._enable_transport(False)
        self.caption_name.setText(f"{entry[0]} · {lwa.OBSERVATORY}")
        self.caption_name.setToolTip("")
        set_source_note(self.source_line, LWA_NOTE)
        self._enter_lwa_look()
        self.set_summary(f"{entry[0]} · {span_text(start, end)}")
        self.loading = True
        self.begin_job("frames", "Asking OVRO-LWA for the movie…", self.cancel_loading)
        self._show_message("", spinner="Asking OVRO-LWA for the movie…")
        step = self.step_seconds()
        side = self.frame_width()
        loader = lwa.LwaMovieLoader(lwa.view_of(entry), start, end, interval=step or lwa.LEAST_CADENCE, side=side, movie_day=movie_day)
        self.lwa_loader = loader
        loader.stage.connect(lambda text, done, total, l=loader: self._on_lwa_stage(l, text, done, total))
        loader.planned.connect(lambda plan, l=loader: self._on_lwa_planned(l, entry, plan))
        loader.range_found.connect(lambda first, last, l=loader: self._on_lwa_range(l, first, last))
        loader.frame_ready.connect(lambda idx, image, l=loader: self._on_lwa_frame(l, idx, image))
        loader.failed.connect(lambda text, l=loader: self._on_lwa_failed(l, text))
        loader.done.connect(lambda l=loader: self._on_lwa_done(l))
        loader.start()

    def _enter_lwa_look(self):
        """The movie's frames have no geometry: no overlays, no readout, no
        Helioviewer link (Use in viewer says so in its entries)."""
        self.toggles.setEnabled(False)
        for name in hvw.OVERLAYS:
            self.view.overlays[name] = False
        self.view.set_coordinates_known(False)
        self.view.set_observer("")
        self.link_btn.setEnabled(False)
        self.link_btn.setToolTip("Not from Helioviewer: the movie's frames have no view there")

    def _leave_lwa_look(self):
        self.toggles.setEnabled(True)
        for name in hvw.OVERLAYS:
            self.view.overlays[name] = self.toggles.switches[name].isChecked()
        self.view.set_coordinates_known(True)
        self.link_btn.setEnabled(True)
        self.link_btn.setToolTip("The same view on helioviewer.org, to the clipboard")

    def _current_lwa(self, loader):
        return not self._closing and loader is self.lwa_loader

    def _on_lwa_stage(self, loader, text, done, total):
        if not self._current_lwa(loader):
            return
        if text == "Decoding frames":
            self.update_job("frames", f"Decoding {done} of {total} frames")
            return
        self.update_job("frames", text)
        if not self.frames:
            self.message_activity.start(text)

    def _on_lwa_planned(self, loader, entry, plan):
        """The frames to come, at their own times: the band knows them all."""
        if not self._current_lwa(loader):
            return
        self.frames, self.frame_notes = lwa_frames(plan, lwa.view_of(entry))
        self.lwa_notes = list(plan.notes)
        self.band.set_frames(self.frames)
        parts = [entry[0]]
        if self.frames:
            parts.append(span_text(self.frames[0].observed, self.frames[-1].observed))
            parts.append(f"{len(self.frames)} frame{'s' if len(self.frames) != 1 else ''} at their own times")
        if plan.clip:
            parts.append("Times not confirmed: the movie's frames do not match its frame list")
        parts += self.lwa_notes
        self.set_summary(" · ".join(parts), tone="warning" if plan.clip else None)
        self._enable_transport(bool(self.frames))
        if self.frames:
            at = self._load_at or self.frames[0].observed
            nearest = min(self.frames, key=lambda f: abs((f.observed - at).total_seconds()))
            self.band.set_current(nearest.index)
            if self.current is None:
                self.show_frame(nearest.index)

    def _on_lwa_range(self, loader, first, last):
        """One day's movie: the time becomes its middle, the band its range."""
        if not self._current_lwa(loader):
            return
        self.time_edit.setText((first + (last - first) / 2).replace(microsecond=0).strftime(TIME_FORMAT))
        self.band.set_range(first, last)

    def _on_lwa_frame(self, loader, index, image):
        if not self._current_lwa(loader) or not 0 <= index < len(self.frames):
            return
        frame = self.frames[index]
        frame.pixmap = QPixmap.fromImage(image)
        frame.state = READY
        self.band.refresh()
        if self.current is None:
            self.band.set_current(index)
        elif index == self.current:
            self.show_frame(index)

    def _on_lwa_failed(self, loader, text):
        if not self._current_lwa(loader):
            return
        self.lwa_loader = None
        _park(loader)
        self._lwa_over(cancelled=False, error=text)

    def _on_lwa_done(self, loader):
        if not self._current_lwa(loader):
            return
        self.lwa_loader = None
        _park(loader)
        self._lwa_over(cancelled=False)

    def _lwa_over(self, cancelled, error=""):
        self.loading = False
        self.end_job("frames")
        ready = sum(1 for frame in self.frames if frame.state == READY)
        if error:
            self.set_summary(error, tone="error", retry=True)
            self.say(error)
            if not ready:
                self._show_message(error)
        elif cancelled:
            self.say(f"Cancelled · {ready} frame{'s' if ready != 1 else ''} kept")
        else:
            self.say(f"Loaded {ready} of {len(self.frames)} OVRO-LWA frames")
        for frame in self.frames:
            if frame.state != READY:
                frame.state = PLANNED
        self.band.refresh()
        if self.current is not None:
            self.show_frame(self.current)

    @staticmethod
    def _abandon_lwa(loader):
        """Hear no more from ``loader``, kill its ffmpeg and let it end alone."""
        for signal in (loader.stage, loader.planned, loader.range_found, loader.frame_ready, loader.failed, loader.done):
            try:
                signal.disconnect()
            except TypeError:
                pass
        loader.stop()
        _park(loader)

    # ------------------------------------------------------------ showing a frame

    def _pixmap_of(self, frame) -> Optional[QPixmap]:
        """The frame's image: OVRO-LWA's from the frame, Helioviewer's from the
        loader (None while one dropped from memory is read again)."""
        if self.lwa_mode:
            pixmap = frame.pixmap
            if pixmap is None and frame.duplicate_of is not None and 0 <= frame.duplicate_of < len(self.frames):
                pixmap = self.frames[frame.duplicate_of].pixmap
            return pixmap
        return self.loader.pixmap_of(frame)

    def show_frame(self, index):
        """Frame ``index`` on the well: its picture, or why there is none."""
        if not 0 <= index < len(self.frames):
            return
        self.current = index
        if not self.lwa_mode:
            self.loader.set_current(index)
        frame = self.frames[index]
        self._show_caption(frame)
        pixmap = self._pixmap_of(frame)
        state = self.band.original(index).state
        if pixmap is None:
            rereading = state == READY and not self.lwa_mode and self.loader.rereading(frame)
            if state == FAILED:
                self._show_message("This frame could not be downloaded", details=self.band.original(index).error, retry=not self.loading)
            elif (state == LOADING or rereading) and self.view.frame is not None:
                pass  # the frame before stays until this one comes
            elif state == LOADING or rereading:
                self._show_message("", spinner="Decoding this frame…" if self.lwa_mode else "Downloading this frame…" if state == LOADING else "Reading this frame…")
            else:
                self._show_message("Not downloaded", retry=not self.loading)
            return
        shown, note = self._with_difference(index, frame, pixmap)
        if shown is None:
            self._show_message(note)
            return
        self.view.set_frame(frame if frame.pixmap is shown else dataclasses.replace(frame, pixmap=shown))
        self.message_activity.stop()
        self.well.setCurrentWidget(self.view)
        self._zoom_changed()

    def _show_caption(self, frame):
        self.caption_time.setText(f"{frame.observed:%H:%M:%S} UTC")
        self.caption_time.setToolTip(self._provenance_tip(frame))
        if self.lwa_mode:
            self.caption_offset.setText(self.frame_notes.get(frame.index, ""))
            set_tone(self.caption_offset, "warning" if self.frame_notes.get(frame.index, "").startswith("Time not confirmed") else None)
        else:
            offset = frame.offset
            cadence = self.plan.source.cadence if self.plan is not None else 60
            self.caption_offset.setText(offset_text(offset) if frame.wanted != frame.observed else "")
            set_tone(self.caption_offset, "warning" if abs(offset) > cadence else None)
        self.caption_count.setText(f"Frame {frame.index + 1} of {len(self.frames)}")

    def _provenance_tip(self, frame) -> str:
        lines = [f"Observed {frame.observed:%Y-%m-%d %H:%M:%S} UTC"]
        if frame.wanted != frame.observed:
            lines.append(f"Asked for {frame.wanted:%H:%M:%S} ({offset_text(frame.offset)})")
        if self.lwa_mode:
            lines.append(self.frame_notes.get(frame.index, ""))
            return "\n".join(line for line in lines if line)
        lines.append(f"Image id {frame.image_id if frame.image_id is not None else 'not looked up yet'} · source {frame.source_id}")
        lines.append(f"{frame.image_scale:.4g}″/px · scale correction {frame.scale_correction:.4f} · {frame.width}×{frame.height} px at ({frame.x0:g}, {frame.y0:g})″")
        lines.append(frame.url)
        return "\n".join(lines)

    def _show_message(self, text, spinner=None, details=None, retry=False):
        """The well's message page: ``text``, a spinner with ``spinner``'s
        text, Show details… for ``details``, and Retry."""
        self.message_label.setText(text)
        self.message_label.setVisible(bool(text))
        if spinner:
            self.message_activity.start(spinner)
            self.message_activity.show()
        else:
            self.message_activity.stop()
            self.message_activity.hide()
        self._frame_error = details or ""
        self.details_link.setVisible(bool(details))
        self.frame_retry_link.setVisible(bool(retry))
        self.well.setCurrentWidget(self.message)

    def _show_empty(self):
        when = parse_field(self.time_edit.text())
        around = f" around {when:%H:%M} UTC" if when is not None else ""
        self._show_message(f"Choose an instrument and press Load for images{around}.")
        if not self.summary_label.text():
            self.set_summary("Pick an instrument, a time and a span, then press Load.")

    def _show_frame_error(self):
        """Show details…: the error's text, in the app's error dialog (asked for, so not in the way)."""
        show_error(self, "Helioviewer did not answer", "This is what the request came back with.", self._frame_error)

    def _enable_transport(self, on):
        for button, _kind in self.transport_buttons:
            button.setEnabled(on)

    def _cursor_set(self, when):
        self.time_chosen.emit(when)

    # ------------------------------------------------------------ difference

    def set_diff_mode(self, mode):
        if mode not in DIFF_MODES or mode == self.diff_mode_name:
            self._refresh_diff_hint()
            return
        if self.lwa_mode and mode != "None":
            return
        self.diff_mode_name = mode
        self.diff_buttons[DIFF_MODES.index(mode)].setChecked(True)
        if mode == "Base = radio time" and self.target.mark is not None and self.frames:
            when = self.target.mark[1]
            self.diff_base_index = min(self.frames, key=lambda f: abs((f.observed - when).total_seconds())).index
        self._refresh_diff_hint()
        if self.current is not None:
            self.show_frame(self.current)

    def cycle_diff_mode(self):
        if self.lwa_mode:
            return
        modes = [mode for i, mode in enumerate(DIFF_MODES) if not self.diff_buttons[i].isHidden()]
        self.set_diff_mode(modes[(modes.index(self.diff_mode_name) + 1) % len(modes)])

    def set_base_here(self):
        if self.current is None:
            return
        self.diff_base_index = self.current
        self._diff_cache.clear()
        self._refresh_diff_hint()
        self.show_frame(self.current)

    def _refresh_diff_hint(self):
        base = self.diff_mode_name in ("Base", "Base = radio time")
        if base and 0 <= self.diff_base_index < len(self.frames):
            self.diff_hint.setText(f"vs {self.frames[self.diff_base_index].observed:%H:%M:%S}")
            self.diff_hint.show()
        else:
            self.diff_hint.hide()
        self.set_base_link.setVisible(self.diff_mode_name == "Base")
        self._fit_labels()

    def diff_mode(self) -> str:
        """(For the exports.) "None", "Running" or "Base"."""
        return "Base" if self.diff_mode_name.startswith("Base") else self.diff_mode_name

    def diff_base(self) -> int:
        return self.diff_base_index

    def _with_difference(self, index, frame, pixmap):
        """``(pixmap to show, note)``: the difference against the base or the
        frame before in the mode on, memoised; None and why when there is nothing to subtract."""
        mode = self.diff_mode()
        if mode == "None":
            return pixmap, ""
        ref_index = self.diff_base_index if mode == "Base" else index - 1
        if ref_index < 0 or ref_index == index:
            return None, "No earlier frame to subtract" if mode == "Running" else "This is the base frame"
        key = (index, mode, ref_index)
        held = self._diff_cache.get(key)
        if held is not None:
            self._diff_cache.move_to_end(key)
            return held, ""
        if not 0 <= ref_index < len(self.frames):
            return None, "No base frame"
        reference = self._pixmap_of(self.frames[ref_index])
        if reference is None:
            return None, "The frame to subtract has not come yet"
        image = difference_image(pixmap.toImage(), reference.toImage())
        if image is None:
            return pixmap, ""
        result = QPixmap.fromImage(image)
        self._diff_cache[key] = result
        while len(self._diff_cache) > DIFF_CACHE:
            self._diff_cache.popitem(last=False)
        return result, ""

    # ------------------------------------------------------------ playback and transport

    def toggle_play(self):
        self.playback.toggle()

    def _playing_changed(self, playing):
        self.play_btn.setIcon(transport_icon("pause" if playing else "play", theme_manager.palette))
        self.play_btn.setToolTip("Pause (P, Space)" if playing else "Play (P, Space)")

    def _check_fps(self, fps):
        button = self.fps_group.button(fps)
        if button is not None and not button.isChecked():
            button.setChecked(True)

    def _fps_changed(self, fps):
        self._check_fps(fps)
        self.settings.setValue(KEY.format("fps"), fps)

    def fps(self) -> int:
        return self.playback.fps

    def first_frame(self):
        self.band.step_frame(-1, to_end=True)

    def prev_frame(self):
        self.band.step_frame(-1)

    def next_frame(self):
        self.band.step_frame(1)

    def last_frame(self):
        self.band.step_frame(1, to_end=True)

    # ------------------------------------------------------------ zoom and reframe

    def zoom_in(self):
        self.view.zoom_in()

    def zoom_out(self):
        self.view.zoom_out()

    def fit(self):
        self.view.fit()

    def _zoom_changed(self):
        self.zoom_label.setText(self.view.zoom_text() if self.view.frame is not None else "—")

    def _reframe_toggled(self, on):
        if on:
            if self.view.frame is None or self.lwa_mode:
                self.reframe_btn.setChecked(False)
                self.say("Load a frame first, then drag a box on it")
                return
            self.playback.pause()
            self.view.start_reframe()
        elif self.view.reframing:
            self.view.cancel_reframe()

    def _reframe_requested(self, region):
        self.region = region
        self.load()

    def _reframe_ended(self):
        if self.reframe_btn.isChecked():
            self.reframe_btn.setChecked(False)

    def back_to_full_field(self):
        self.region = None
        self.full_field_link.hide()
        self._fit_labels()
        self.load()

    def _escape(self):
        if self.view.reframing:
            self.view.cancel_reframe()
        elif self.band.cancel_drag():
            pass
        elif self.band.axis.zoomed:
            self.band.axis.whole_range()

    # ------------------------------------------------------------ what the exports ask

    def export_frames(self):
        return list(self.frames)

    def current_frame_index(self):
        return self.current or 0

    def source_name(self):
        if self.lwa_mode and self.frames:
            held = self.caption_name.text().split(" · ")[0]
            return held or lwa.ALL_BANDS
        if self.plan is not None:
            return self.plan.source.name
        return self.selected_name() or "Helioviewer"

    def band_zoom(self):
        axis = self.band.axis
        if not axis.zoomed:
            return None
        return axis.datetime_of(axis.start), axis.datetime_of(axis.end)

    def overlay_painter(self):
        """``fn(painter, frame, width, height)``: the overlays as the view draws
        them, for that frame's geometry; None when none is on."""
        if self.lwa_mode or not any(self.view.overlays.values()) or self.view.overlay_scene() is None:
            return None
        paint = hvw.overlay_painter(self.view)
        template = self.view.overlay_scene()

        def painter_fn(painter, frame, width, height):
            geometry = hvw.Geometry.of_frame(frame)
            scene = dataclasses.replace(template, geometry=geometry, observed=frame.observed)
            paint(painter, QRectF(0, 0, width, height), scene)

        return painter_fn

    def frame_note(self, frame):
        return self.frame_notes.get(frame.index, "")

    def file_prefix(self):
        return "ovro" if self.lwa_mode else "helioviewer"

    def images_note(self):
        if self.lwa_mode:
            return "OVRO-LWA daily movie frames (NJIT); times from the movie's frame list"
        return None

    def frames_are_helioviewer(self):
        return not self.lwa_mode

    # ------------------------------------------------------------ the window

    def _footer_buttons(self):
        return self.diff_buttons + [self.reframe_btn, self.full_field_link, self.link_btn, self.save_btn, self.use_btn, self.export_btn]

    @staticmethod
    def _bar_widths(bar, buttons, labels, compact) -> tuple:
        """The widths ``bar`` wants with every label in full and with the
        short ones: its hint, plus or minus what the shortened labels and the
        hidden words give up."""
        hint = bar.sizeHint().width()
        metrics = bar.fontMetrics()
        saved = 0
        for button in buttons:
            pair = button.property("labels")
            if pair and button.isVisibleTo(bar):
                saved += max(0, metrics.horizontalAdvance(pair[0]) - metrics.horizontalAdvance(pair[1]))
        for label in labels:
            saved += label.sizeHint().width() + SPACE_S
        return (hint + saved, hint) if compact else (hint, hint - saved)

    def _fit_labels(self):
        """Each bar shortens its labels when its full ones would not fit the
        window, so nothing is ever cut off: the footer first (it is the
        wider), then the header too, when the band also drops its flare lane.
        The bars do not size the window (they would hold it at their full
        labels); the window itself stops where the short ones end, measured
        once they are in place."""
        width = self.width()
        header_full = self._bar_widths(self.header, self.span_buttons + self.step_buttons, [self.span_label, self.step_label], self._compact)[0]
        footer_full = self._bar_widths(self.footer, self._footer_buttons(), [self.fps_label], self._footer_compact)[0]
        self._set_compact(header_full > width or width < NARROW_WIDTH, footer_full > width)
        header_short = self._bar_widths(self.header, self.span_buttons + self.step_buttons, [self.span_label, self.step_label], self._compact)[1]
        footer_short = self._bar_widths(self.footer, self._footer_buttons(), [self.fps_label], self._footer_compact)[1]
        self.setMinimumWidth(max(header_short, footer_short, 0))

    def _set_compact(self, compact, footer=None):
        footer = compact if footer is None else (footer or compact)
        if compact == self._compact and footer == self._footer_compact:
            return
        self._compact, self._footer_compact = compact, footer
        for button in self.span_buttons + self.step_buttons:
            self._label(button, compact)
        for button in self._footer_buttons():
            self._label(button, footer)
        self.span_label.setVisible(not compact)
        self.step_label.setVisible(not compact)
        self.fps_label.setVisible(not footer)
        self.band.set_compact(compact)

    @staticmethod
    def _label(button, short):
        labels = button.property("labels")
        if labels:
            button.setText(labels[1] if short else labels[0])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_labels()

    def closeEvent(self, event):
        if self._closing:  # closed once already: there is nothing left to stop
            event.accept()
            return
        # An export under way goes first: its dialog says it is stopping
        dialog = self.findChild(WorkDialog)
        if dialog is not None and dialog.isVisible():
            self._close_after_export = True
            dialog.reject()
            event.ignore()
            return
        self._closing = True
        self.playback.pause()
        self._note_timer.stop()
        if self.planner is not None:
            self.planner.cancel()
            _park(self.planner)
        if self.lwa_loader is not None:
            self._abandon_lwa(self.lwa_loader)
            self.lwa_loader = None
        _park(self.events_thread)
        _park(self.sources_thread)
        self.loader.close(wait_ms=0)  # a request under way is parked, not waited for
        self.settings.setValue(KEY.format("splitter"), self.splitter.sizes())
        # The frames go now: the theme's hold on the window can outlast it
        self.view.set_frame(None)
        self.frames = []
        self.plan = None
        self._diff_cache.clear()
        if self.testAttribute(Qt.WA_DeleteOnClose):
            theme_manager.unregister_owner(self)
        event.accept()


def main():
    import sys

    from .ui.app import create_app

    app = create_app()
    window = HelioviewerBrowser()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

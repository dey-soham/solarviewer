"""
The Helioviewer browser's time band, its seekbar: the frames of a load as
marks on a UTC ruler, each at the time the archive observed it; the radio
image's time beside them; the flares of the range under them; and a cursor
on the frame shown. Clicks and keys choose frames, playback glides the
cursor along, and the band is zoomed by dragging, as Solar Activity's day
band is.

``HelioAxis`` is Solar Activity's ``DayAxis`` freed from the day: hours still
count from 00:00 UTC of the day the range starts on, but run past 24 when the
range does, up to 48 hours. ``FrameBand`` paints the band, ``Playback`` steps
it, ``EventsFetch`` brings the flares. All times are naive UTC datetimes.
"""

import logging
import math
from datetime import datetime, time, timedelta, timezone

from PyQt5.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, QThread, QTimer, QVariantAnimation, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen, QPolygonF
from PyQt5.QtWidgets import QApplication, QToolTip, QWidget

from . import helioviewer_api as hv
from .helioviewer_data import FAILED, LOADING, READY
from .noaa_events.day_axis import EDGE_ARROW, EDGE_INSET, EDGE_TIP, GUTTER, LINK_INSET, TICK_ROOM, DayAxis, flare_size
from .ui.activity import track_thread
from .ui.styles import FONT_META, FONT_SMALL, MONO_FAMILY, SPACE_L, SPACE_S, SPACE_XS, theme_manager, themed
from .ui.widgets import link_button

logger = logging.getLogger(__name__)

PAD = 0.02  # of the range, added at each end so the first and last marks clear the edges
MARK = 5  # px, a frame's dot
CURRENT = 7  # px, the dot of the frame shown
DENSE_PX = 6  # px per frame; with less room the marks become one bar
HINT = "Click to show the nearest frame. Drag to zoom, double-click for the whole range."
NO_FLARES = "No flares listed for this range"
TICK_STEPS = (1 / 60, 1 / 30, 1 / 12, 1 / 6, 0.25, 0.5, 1, 2, 3, 4, 6, 12)
LABEL_ROOM = 56  # px between tick labels


def clock(hours):
    """``16:14:57``: the clock time of ``hours``, past midnight wrapped."""
    seconds = int(round(hours * 3600)) % 86400
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def offset_text(seconds):
    """``−3 s``, ``+2 min``, ``−1.5 h``, ``−35 d``: how far one time is from another."""
    sign = "−" if seconds < 0 else "+"
    size = abs(seconds)
    if size < 90:
        return f"{sign}{size:.0f} s"
    if size < 90 * 60:
        return f"{sign}{size / 60:.0f} min"
    if size < 48 * 3600:
        hours = f"{size / 3600:.1f}"
        return f"{sign}{hours[:-2] if hours.endswith('.0') else hours} h"
    return f"{sign}{size / 86400:.0f} d"


def _tick_step(hours, width):
    for step in TICK_STEPS:
        if step / hours * width >= LABEL_ROOM:
            return step
    return TICK_STEPS[-1]


class HelioAxis(DayAxis):
    """A ``DayAxis`` whose window is the loaded range, not the day: it may run
    into the next day, and down to ten minutes. ``first`` and ``last`` are the
    range's edges in hours from 00:00 UTC of ``day``."""

    MIN_SPAN = 10 / 60
    MAX_SPAN = 48.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.day = datetime.now(timezone.utc).date()  # until a range is set
        self.first, self.last = 0.0, 24.0

    def set_range(self, start, end):
        """The range the band covers: ``day`` is its first day, and the window
        all of it. A range over 48 hours is cut to 48 about its middle."""
        start, end = min(start, end), max(start, end)
        self.day = start.date()
        first, last = self.hours_of(start), self.hours_of(end)
        span = min(max(last - first, self.MIN_SPAN), self.MAX_SPAN)
        if span != last - first:
            middle = (first + last) / 2
            first, last = middle - span / 2, middle + span / 2
        if first < 0:
            first, last = 0.0, span
        self.first, self.last = first, last
        self.start, self.end = first, last
        if self.cursor is not None and not first <= self.cursor <= last:
            self.cursor = None
        self.window_changed.emit()
        self.cursor_changed.emit()

    def hours_of(self, when):
        """``when`` in hours from 00:00 UTC of ``day``."""
        if self.day is None:
            self.day = when.date()
        return (when - datetime.combine(self.day, time())).total_seconds() / 3600

    def datetime_of(self, hours):
        return datetime.combine(self.day, time()) + timedelta(hours=hours)

    def set_window(self, start, end):
        """The part of the range shown: at least ten minutes, within the range."""
        start, end = min(start, end), max(start, end)
        span = min(max(end - start, self.MIN_SPAN), self.MAX_SPAN, self.last - self.first)
        if span != end - start:
            middle = (start + end) / 2
            start, end = middle - span / 2, middle + span / 2
        if start < self.first:
            start, end = self.first, self.first + span
        if end > self.last:
            start, end = self.last - span, self.last
        if (start, end) != (self.start, self.end):
            self.start, self.end = start, end
            self.window_changed.emit()

    def whole_range(self):
        self.set_window(self.first, self.last)

    def whole_day(self):
        """What a double-click on a day band asks for: here, the whole range."""
        self.whole_range()

    @property
    def zoomed(self):
        return self.end - self.start < self.last - self.first - 1e-9

    def set_cursor(self, hours):
        """``None`` clears it; otherwise it is kept in the range and brought
        into view."""
        self.cursor_set.emit()
        if hours is not None:
            hours = max(self.first, min(self.last, float(hours)))
            self._reveal(hours)
        if hours != self.cursor:
            self.cursor = hours
            self.cursor_changed.emit()

    def move_cursor(self, hours):
        """The cursor on its way somewhere (a glide): not a choice of its own."""
        hours = max(self.first, min(self.last, float(hours)))
        self._reveal(hours)
        if hours != self.cursor:
            self.cursor = hours
            self.cursor_changed.emit()

    def set_cursor_time(self, when):
        self.set_cursor(self.hours_of(when))

    def cursor_text(self):
        return "" if self.cursor is None else clock(self.cursor)


def frame_tip(frame, original=None):
    """What hovering a frame's mark says: its observed time, the time asked
    for when that differs, and its state or image id."""
    parts = [frame.observed.strftime("%H:%M:%S") + " UTC"]
    if frame.wanted != frame.observed:
        parts.append(f"requested {frame.wanted:%H:%M:%S} ({offset_text(frame.offset)})")
    if original is not None:
        parts.append(f"same image as frame {original.index + 1}")
    state = frame if original is None else original
    if state.state == FAILED:
        parts.append(state.error or "could not be downloaded")
    elif state.state == LOADING:
        parts.append("downloading")
    elif state.state != READY:
        parts.append("not downloaded yet")
    elif frame.image_id is not None:
        parts.append(f"id {frame.image_id}")
    return " · ".join(parts)


def flare_lane(rows):
    """The X-ray flares among Helioviewer's event ``rows`` as ``(start, peak,
    end, label, tip)`` datetimes, in time order: those with a GOES class
    (a detection without one is not an X-ray flare). A flare two lists
    report, peaking in the same minute, is taken once, from SSW Latest
    Events when it has it (the lists may differ by a tenth of a class)."""
    flares = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("event_type") != "FL":
            continue
        start, peak, end = hv.event_times(row)
        label = (row.get("fl_goescls") or "").strip()
        if start is None or not label:
            continue
        end = end or peak or start
        source = row.get("frm_name") or ""
        key = (peak or start).replace(second=0, microsecond=0)
        if key in flares and "SSW" not in source:
            continue
        tip = f"{label} · {start:%H:%M}–{end:%H:%M} UTC"
        if peak is not None:
            tip += f", peak {peak:%H:%M}"
        if source:
            tip += f" · {source.replace('_', ' ')}"
        flares[key] = (start, peak, end, label, tip)
    return sorted(flares.values(), key=lambda flare: flare[0])


class FrameBand(QWidget):
    """The frames of a load along a UTC ruler. Lanes: Frames always, Your
    image when a mark is set, X-ray when the flares have come. It owns the
    time column; the browser reads the frame shown through ``current`` and
    ``current_changed``, and the user's choices through ``cursor_set``."""

    RULER, LANE = 20, 16
    current_changed = pyqtSignal(int)  # the frame to show
    cursor_set = pyqtSignal(object)  # the user chose a time (a datetime)
    play_toggle = pyqtSignal()
    fps_step = pyqtSignal(int)

    def __init__(self, axis=None, parent=None, inset=SPACE_L):
        super().__init__(parent)
        self.axis = axis or HelioAxis(self)
        self.inset = inset  # the band's own margin at each side
        self.frames = []
        self._hours = []  # each frame's observed time, in the axis's hours
        self._mark_hours = []  # where each frame's mark goes
        self.current = None  # the index of the frame shown
        self.mark = None  # (label, datetime, tooltip): the radio image's time
        self.flares = []  # (start, peak, end, label, tip) datetimes
        self._events = None  # the event rows once fetched; None before, or after a failure
        self._events_note = ""  # why there is no flare lane
        self._compact = False
        self._press = None  # the x a drag started from
        self._drag = None
        self._before_click = None  # (the frame the click before just replaced,)
        self._peak_step = None  # (frame index, peak hours): the last flare stepped to
        self._glide = QVariantAnimation(self)
        self._glide.valueChanged.connect(lambda value: self.axis.move_cursor(value))
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setAccessibleName("Time band")
        self.whole_range_button = link_button("Whole range", self.axis.whole_range, self)
        self.whole_range_button.setToolTip("Back to the whole range (double-click the band, or Esc)")
        self.whole_range_button.hide()
        self.axis.window_changed.connect(self._place_button)
        for signal in (self.axis.window_changed, self.axis.cursor_changed):
            signal.connect(self.update)
        self._set_height()
        themed(self, self._restyle)

    def _restyle(self, _palette):
        self._place_button()  # (the new stylesheet gave the button its height back)
        self.update()

    # What is shown

    def lanes(self):
        names = ["Frames"]
        if self.mark is not None:
            names.append("Your image")
        if self._events is not None and not self._compact:
            names.append("X-ray")
        return names

    def _set_height(self):
        self.setFixedHeight(self.RULER + len(self.lanes()) * (self.LANE + 2) + 2)
        self.update()

    def set_compact(self, compact):
        """A narrow window does without the flare lane."""
        if compact != self._compact:
            self._compact = compact
            self._set_height()

    def set_range(self, start, end):
        """The range to show, padded a little at each end; frames keep their places."""
        pad = (end - start) * PAD
        self.axis.set_range(start - pad, end + pad)
        self._rehours()
        self.update()

    def set_frames(self, frames, start=None, end=None):
        """The frames of a load. The range is theirs, widened to ``start`` and
        ``end`` (the range asked for) when given."""
        self.frames = list(frames)
        self.current = None
        self._peak_step = None
        self._glide.stop()
        times = [frame.observed for frame in self.frames]
        lo = min([t for t in [start, min(times, default=None)] if t is not None], default=None)
        hi = max([t for t in [end, max(times, default=None)] if t is not None], default=None)
        if lo is not None and hi is not None:
            self.set_range(lo, hi)
        else:
            self._rehours()
        self.axis.set_cursor(None)
        self.update()

    def set_plan(self, plan):
        self.set_frames(plan.frames, plan.start, plan.end)

    def _rehours(self):
        self._hours = [self.axis.hours_of(frame.observed) for frame in self.frames]
        # A repeat's mark sits at the time it was asked for: its image's own mark is its original's
        self._mark_hours = [
            self.axis.hours_of(frame.wanted if frame.duplicate_of is not None else frame.observed) for frame in self.frames
        ]

    def refresh(self):
        """After a frame's state changed."""
        self.update()

    def set_mark(self, label, when, tip=""):
        """A mark at ``when`` on a lane of its own (``None`` removes it)."""
        self.mark = None if when is None else (label, when, tip or f"{label} · {when:%Y-%m-%d %H:%M:%S} UTC")
        self._set_height()

    def set_events(self, rows):
        """The flares among Helioviewer's event rows, on the X-ray lane."""
        self._events = list(rows)
        self._events_note = ""
        self.flares = flare_lane(self._events)
        self._peak_step = None
        self._set_height()

    def set_events_note(self, text):
        """No flare lane, and ``text`` (why) as the gutter's tooltip."""
        self._events = None
        self.flares = []
        self._events_note = text
        self._set_height()

    # Frames and the cursor

    def set_current(self, index, glide_ms=0):
        """Show frame ``index`` (``None`` for none): the cursor goes to its
        observed time, gliding there over ``glide_ms`` during playback."""
        if index is not None and not 0 <= index < len(self.frames):
            index = None
        changed = index != self.current
        self.current = index
        self._glide.stop()
        if index is None:
            self.axis.set_cursor(None)
        else:
            target = self._hours[index]
            if glide_ms > 0 and self.axis.cursor is not None and target > self.axis.cursor:
                self._glide.setDuration(int(glide_ms))
                self._glide.setStartValue(float(self.axis.cursor))
                self._glide.setEndValue(float(target))
                self._glide.start()
            else:
                self.axis.set_cursor(target)
        if changed and index is not None:
            self.current_changed.emit(index)
        self.update()

    def stop_glide(self):
        self._glide.stop()
        if self.current is not None:
            self.axis.set_cursor(self._hours[self.current])

    def _choose(self, index):
        """The user picked frame ``index``."""
        self.set_current(index)
        self.cursor_set.emit(self.frames[index].observed)

    def original(self, index):
        """The frame whose image frame ``index`` shows (itself, or the one it repeats)."""
        frame = self.frames[index]
        return self.frames[frame.duplicate_of] if frame.duplicate_of is not None else frame

    def is_ready(self, index):
        return self.original(index).state == READY

    def nearest(self, hours, ready_only=True):
        """The frame nearest ``hours``: a ready one, or with none ready any
        frame; None without frames."""
        if not self.frames:
            return None
        ready = [i for i in range(len(self.frames)) if self.is_ready(i)] if ready_only else []
        choices = ready or range(len(self.frames))
        return min(choices, key=lambda i: (abs(self._hours[i] - hours), i))

    def nearest_time(self, when, ready_only=True):
        return self.nearest(self.axis.hours_of(when), ready_only)

    def next_ready(self, index, direction=1, wrap=True):
        """The ready frame after (or before) ``index``; None if there is none."""
        count = len(self.frames)
        if not count:
            return None
        here = index if index is not None else (-1 if direction > 0 else count)
        for step in range(1, count + 1):
            candidate = here + step * direction
            if not 0 <= candidate < count:
                if not wrap:
                    return None
                candidate %= count
            if self.is_ready(candidate):
                return candidate
        return None

    def step_frame(self, direction, to_end=False):
        """The frame after or before the one shown; the last or first with ``to_end``."""
        if not self.frames:
            return
        last = len(self.frames) - 1
        if to_end or self.current is None:
            index = last if direction > 0 else 0
        else:
            index = max(0, min(last, self.current + direction))
        self._choose(index)

    def flare_peaks(self):
        """The flares' peak times (their starts when none is given), in hours, in order."""
        return sorted(self.axis.hours_of(peak or start) for start, peak, _end, _label, _tip in self.flares)

    def step_flare(self, direction):
        """The frame nearest the flare peak after (or before) the cursor."""
        peaks = self.flare_peaks()
        if not peaks:
            return
        here = self.axis.cursor
        if self._peak_step is not None and self._peak_step[0] == self.current:
            here = self._peak_step[1]  # from the peak itself, not the frame nearest it
        if here is None:
            here = self.axis.first - 1 if direction > 0 else self.axis.last + 1
        beyond = [p for p in peaks if (p - here) * direction > 1e-6]
        if not beyond:
            return
        peak = beyond[0] if direction > 0 else beyond[-1]
        if self.frames:
            index = self.nearest(peak)
            self._choose(index)
            self._peak_step = (index, peak)
        else:
            self.axis.set_cursor(peak)
            self.cursor_set.emit(self.axis.datetime_of(peak))

    # The column

    def x_of_frame(self, index):
        """Where frame ``index``'s mark is: at its observed time, or for a
        repeat of an image at the time it was asked for."""
        return self.axis.x_of(self._mark_hours[index], self)

    def _publish_column(self):
        offset = self.mapTo(self.window(), QPoint(0, 0)).x()
        self.axis.set_column(offset + self.inset + GUTTER, offset + self.width() - self.inset)
        self.update()

    def _place_button(self):
        self.whole_range_button.setVisible(self.axis.zoomed)
        # The stylesheet's button height would reach over the Frames lane and take its clicks
        self.whole_range_button.setMinimumSize(0, 0)
        self.whole_range_button.setGeometry(
            self.inset + 36 - LINK_INSET, 0, self.whole_range_button.sizeHint().width(), self.RULER - 2
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._publish_column()
        self._place_button()

    def showEvent(self, event):
        super().showEvent(event)
        self._publish_column()
        self._place_button()

    def _in_column(self, x):
        return self.axis.x_of(self.axis.start, self) - 1 <= x <= self.axis.x_of(self.axis.end, self) + 1

    def _visible(self):
        """The indices of the frames inside the window, in order."""
        start, end = self.axis.start, self.axis.end
        return [i for i, h in enumerate(self._mark_hours) if start <= h <= end]

    def dense(self):
        """Whether the frames in view are too many for marks of their own."""
        left, right = self.axis.x_of(self.axis.start, self), self.axis.x_of(self.axis.end, self)
        return len(self._visible()) * DENSE_PX > right - left

    # Painting

    def _font(self, size, mono=False):
        font = QFont(self.font())
        if mono:
            font.setFamily(MONO_FAMILY)
        font.setPointSizeF(size)
        return font

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        palette = theme_manager.palette
        left, right = self.axis.x_of(self.axis.start, self), self.axis.x_of(self.axis.end, self)
        bottom = self.height() - 2
        self._paint_ruler(painter, palette, left, right, bottom)
        y = self.RULER
        for name in self.lanes():
            painter.setFont(self._font(FONT_SMALL))
            painter.setPen(QColor(palette["text_secondary"]))
            painter.drawText(QRectF(self.inset, y, GUTTER - SPACE_S, self.LANE), Qt.AlignVCenter, name)
            painter.setPen(QColor(palette["plot_grid"]))
            painter.drawLine(QPointF(left, y + self.LANE + 0.5), QPointF(right, y + self.LANE + 0.5))
            painter.save()
            painter.setClipRect(QRectF(left - 1, y, right - left + 2, self.LANE + 1))
            if name == "Frames":
                self._paint_frames(painter, palette, y, left, right)
            elif name == "Your image":
                self._paint_mark(painter, palette, y, left, right)
            else:
                self._paint_flares(painter, palette, y, left, right)
            painter.restore()
            y += self.LANE + 2
        self._paint_cursor(painter, palette, left, right, bottom)
        self._paint_drag(painter, palette, left, right, bottom)
        painter.end()

    def _tick_label(self, hour):
        """``16:05``, or the date at midnight: a range may cross one."""
        if abs(hour / 24 - round(hour / 24)) < 1e-9:
            midnight = self.axis.datetime_of(hour)
            return f"{midnight.day} {midnight:%b}"
        return clock(hour)[:5]

    def _paint_ruler(self, painter, palette, left, right, bottom):
        span = self.axis.end - self.axis.start
        painter.setFont(self._font(FONT_META, mono=True))
        step = _tick_step(span, right - left)
        cursor = self.axis.cursor
        cursor_x = self.axis.x_of(cursor, self) if cursor is not None and self.axis.start <= cursor <= self.axis.end else None
        edge = self._edge_marker()
        if edge is not None:
            cursor_x = edge.center().x()
        hour = math.ceil(self.axis.start / step - 1e-9) * step
        while hour <= self.axis.end + 1e-6:
            x = self.axis.x_of(hour, self)
            painter.setPen(QColor(palette["plot_grid"]))
            painter.drawLine(QPointF(x, self.RULER - 2), QPointF(x, bottom))
            if cursor_x is None or abs(x - cursor_x) > TICK_ROOM:  # its own label goes there
                painter.setPen(QColor(palette["text_dim"]))
                painter.drawText(QRectF(x - 28, 2, 56, 15), Qt.AlignCenter, self._tick_label(hour))
            hour += step
        painter.setFont(self._font(FONT_SMALL))
        painter.setPen(QColor(palette["text_secondary"]))
        painter.drawText(QRectF(self.inset, 2, 32, 15), Qt.AlignVCenter, "UTC")
        if self.hasFocus():
            painter.setPen(QColor(palette["highlight"]))
            painter.drawLine(QPointF(left, self.RULER - 2), QPointF(right, self.RULER - 2))

    def _state_colour(self, palette, index):
        state = self.original(index).state
        if state == READY:
            return QColor(palette["highlight"])
        if state == FAILED:
            return QColor(palette["error"])
        if state == LOADING:
            return QColor(palette["text_dim"])
        return QColor(palette["text_faint"])

    def _paint_frames(self, painter, palette, y, left, right):
        if not self.frames:
            return
        middle = y + self.LANE / 2 + 0.5
        if self.dense():
            self._paint_bar(painter, palette, y, left, right)
            return
        for index in self._visible():
            x = self.x_of_frame(index)
            frame = self.frames[index]
            colour = self._state_colour(palette, index)
            state = self.original(index).state
            radius = (CURRENT if index == self.current else MARK) / 2
            if frame.duplicate_of is not None:  # a repeat of an image: a tick
                painter.setPen(Qt.NoPen)
                painter.setBrush(colour)
                painter.drawRect(QRectF(x - 1, middle - 3, 2, 6))
            elif state == FAILED:
                painter.setPen(QPen(colour, 1.5))
                painter.drawLine(QPointF(x - radius, middle - radius), QPointF(x + radius, middle + radius))
                painter.drawLine(QPointF(x - radius, middle + radius), QPointF(x + radius, middle - radius))
            elif state in (READY, LOADING):
                painter.setPen(Qt.NoPen)
                painter.setBrush(colour)
                painter.drawEllipse(QPointF(x, middle), radius, radius)
            else:  # planned: hollow
                painter.setPen(QPen(colour, 1))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(QPointF(x, middle), radius - 0.5, radius - 0.5)
            if index == self.current:
                painter.setPen(QPen(QColor(palette["text_strong"]), 1))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(QPointF(x, middle), radius + 1.5, radius + 1.5)

    def _bar_edges(self):
        """Each frame's stretch of the bar, ``(lo, hi)`` in hours: halfway to
        its neighbours, and as far again at the ends."""
        hours = self._hours
        count = len(hours)
        if count == 1:
            return [(self.axis.start, self.axis.end)]
        edges = []
        for index in range(count):
            lo = (hours[index - 1] + hours[index]) / 2 if index > 0 else hours[0] - (hours[1] - hours[0]) / 2
            hi = (hours[index] + hours[index + 1]) / 2 if index + 1 < count else hours[-1] + (hours[-1] - hours[-2]) / 2
            edges.append((lo, hi))
        return edges

    def _paint_bar(self, painter, palette, y, left, right):
        """Too many frames for marks: one bar, each frame's stretch of it in
        its state's colour, so it fills in as the frames come."""
        painter.setPen(Qt.NoPen)
        top, height = y + 4, self.LANE - 8
        for index, (lo, hi) in enumerate(self._bar_edges()):
            if hi < self.axis.start or lo > self.axis.end:
                continue
            x0, x1 = self.axis.x_of(lo, self), self.axis.x_of(hi, self)
            colour = self._state_colour(palette, index)
            if self.original(index).state not in (READY, FAILED, LOADING):
                colour.setAlpha(80)
            painter.setBrush(colour)
            painter.drawRect(QRectF(x0, top, max(x1 - x0, 0.5), height))

    def _paint_mark(self, painter, palette, y, left, right):
        label, when, _tip = self.mark
        hours = self.axis.hours_of(when)
        painter.setFont(self._font(FONT_META, mono=True))
        metrics = painter.fontMetrics()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(palette["text"]))
        size = self.LANE - 6
        if self.axis.first <= hours <= self.axis.last:
            x = self.axis.x_of(hours, self)
            painter.drawPolygon(
                QPolygonF([QPointF(x, y + 3), QPointF(x - size / 2, y + 3 + size), QPointF(x + size / 2, y + 3 + size)])
            )
            text = when.strftime("%H:%M:%S")
            width = metrics.horizontalAdvance(text)
            box = QRectF(x + size / 2 + 3, y, width, self.LANE)
            if box.right() > right:
                box.moveRight(x - size / 2 - 3)
        else:  # outside the range: at the edge it lies beyond
            before = hours < self.axis.first
            x = left if before else right
            tip_x = x - 2 if before else x + 2
            back = tip_x + size if before else tip_x - size
            painter.drawPolygon(
                QPolygonF([QPointF(tip_x, y + 3 + size / 2), QPointF(back, y + 3), QPointF(back, y + 3 + size)])
            )
            gap = abs((when - self.axis.datetime_of(self.axis.first if before else self.axis.last)).total_seconds())
            text = f"{when:%H:%M:%S} · {offset_text(gap)[1:]} {'earlier' if before else 'later'}"
            width = metrics.horizontalAdvance(text)
            box = QRectF(x + size + 4, y, width, self.LANE) if before else QRectF(x - size - 4 - width, y, width, self.LANE)
        painter.setPen(QColor(palette["text_secondary"]))
        painter.drawText(box, Qt.AlignVCenter, text)

    def _paint_flares(self, painter, palette, y, left, right):
        height = self.LANE
        if not self.flares:
            painter.setFont(self._font(FONT_SMALL))
            painter.setPen(QColor(palette["text_secondary"]))
            painter.drawText(QRectF(left, y, right - left, height), Qt.AlignCenter, NO_FLARES)
            return
        spots = []  # (x0, x1, label)
        for start, peak, end, label, _tip in self.flares:
            begin_h, end_h = self.axis.hours_of(start), self.axis.hours_of(end)
            if end_h < self.axis.start or begin_h > self.axis.end:
                continue
            x0 = self.axis.x_of(begin_h, self)
            x1 = max(self.axis.x_of(end_h, self), x0 + 3)
            painter.fillRect(QRectF(x0, y + 2, x1 - x0, height - 4), QColor(palette["warning_soft"]))
            if peak is not None:
                x = self.axis.x_of(self.axis.hours_of(peak), self)
                painter.setPen(QPen(QColor(palette["warning"]), 1.5))
                painter.drawLine(QPointF(x, y + 1), QPointF(x, y + height - 1))
            spots.append((x0, x1, label))
        self._paint_labels(painter, palette, spots, y, height, left, right)

    def _paint_labels(self, painter, palette, spots, y, height, left, right):
        """The largest flare is labelled first; then the rest, in time order,
        where the bars and the labels before leave room."""
        painter.setFont(self._font(FONT_META, mono=True))
        metrics = painter.fontMetrics()
        taken = [(x0, x1) for x0, x1, _label in spots]
        for spot in sorted(spots, key=lambda spot: -flare_size(spot[2])):
            x0, x1, label = spot
            if not label:
                continue
            width = metrics.horizontalAdvance(label) + 2
            places = [(a, a + width) for a in (x1 + 3, x0 - 3 - width) if a >= left and a + width <= right]
            others = [t for t in taken if t != (x0, x1)]
            place = next((p for p in places if not any(a < p[1] and b > p[0] for a, b in others)), None)
            if place is None:
                continue
            taken.append(place)
            painter.setPen(QColor(palette["text"]))
            painter.drawText(QRectF(place[0], y, width, height), Qt.AlignVCenter, label)

    def _cursor_label(self):
        """The observed time of the frame shown, or where the cursor is."""
        if self.current is not None:
            return self.frames[self.current].observed.strftime("%H:%M:%S")
        return self.axis.cursor_text()

    def _paint_cursor(self, painter, palette, left, right, bottom):
        hours = self.axis.cursor
        edge = self._edge_marker()
        if edge is not None:
            self._paint_edge_marker(painter, palette, edge)
        if hours is None or not self.axis.start <= hours <= self.axis.end:
            return
        x = self.axis.x_of(hours, self)
        painter.setPen(QPen(QColor(palette["text_strong"]), 1))
        painter.drawLine(QPointF(x, self.RULER - 2), QPointF(x, self.height()))
        # Over the tick labels, on a ground of its own
        painter.setFont(self._font(FONT_META, mono=True))
        text = self._cursor_label()
        width = painter.fontMetrics().horizontalAdvance(text) + SPACE_S
        box = QRectF(min(max(x - width / 2, left - 20), right + 12 - width), 2, width, 15)
        painter.fillRect(box, QColor(palette["highlight_soft"]))
        painter.setPen(QColor(palette["text"]))
        painter.drawText(box, Qt.AlignCenter, text)

    def _edge_marker(self):
        """Where the ruler says the cursor is, if it is outside the zoom: at
        the edge it lies beyond, and ``None`` otherwise."""
        hours = self.axis.cursor
        if hours is None or self.axis.start <= hours <= self.axis.end:
            return None
        width = QFontMetricsF(self._font(FONT_META, mono=True)).horizontalAdvance("00:00:00") + 2 * SPACE_S + EDGE_ARROW
        if hours < self.axis.start:
            return QRectF(self.axis.x_of(self.axis.start, self) - EDGE_INSET, 2, width, 15)
        return QRectF(self.axis.x_of(self.axis.end, self) + EDGE_INSET - width, 2, width, 15)

    def _paint_edge_marker(self, painter, palette, box):
        before = self.axis.cursor < self.axis.start
        painter.fillRect(box, QColor(palette["highlight_soft"]))
        tip = box.left() + SPACE_XS if before else box.right() - SPACE_XS
        back = tip + EDGE_ARROW if before else tip - EDGE_ARROW
        middle = box.center().y()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(palette["text"]))
        painter.drawPolygon(QPolygonF([QPointF(tip, middle), QPointF(back, middle - 4), QPointF(back, middle + 4)]))
        painter.setFont(self._font(FONT_META, mono=True))
        painter.setPen(QColor(palette["text"]))
        text = box.adjusted(EDGE_ARROW, 0, 0, 0) if before else box.adjusted(0, 0, -EDGE_ARROW, 0)
        painter.drawText(text, Qt.AlignCenter, self._cursor_label())

    def _paint_drag(self, painter, palette, left, right, bottom):
        if self._drag is None:
            return
        x0, x1 = sorted((max(left, min(right, self._press)), max(left, min(right, self._drag))))
        colour = QColor(palette["highlight"])
        colour.setAlpha(60)
        painter.fillRect(QRectF(x0, self.RULER - 2, x1 - x0, bottom - self.RULER + 2), colour)

    # Pointer and keys

    def mousePressEvent(self, event):
        self._before_click = None  # a press starts a click of its own
        edge = self._edge_marker()
        if event.button() == Qt.LeftButton and edge is not None and edge.contains(QPointF(event.pos())):
            self.axis.set_cursor(self.axis.cursor)
        elif event.button() == Qt.LeftButton and self._in_column(event.x()):
            self._press = event.x()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        edge = self._edge_marker()
        if edge is not None and edge.contains(QPointF(event.pos())):
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.unsetCursor()
        if self._press is not None and abs(event.x() - self._press) >= QApplication.startDragDistance():
            self._drag = event.x()
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        press, drag = self._press, self._drag
        self._press = self._drag = None
        if press is None:
            return
        if drag is None:
            self._before_click = (self.current,)
            index = self.nearest(self.axis.hours_at(press, self))
            if index is not None:
                self._choose(index)
        else:
            self.axis.set_window(self.axis.hours_at(press, self), self.axis.hours_at(drag, self))
        self.update()

    def mouseDoubleClickEvent(self, event):
        self._press = self._drag = None
        self.axis.whole_range()
        before, self._before_click = self._before_click, None
        if before and event.button() == Qt.LeftButton and self._in_column(event.x()) and before[0] is not None:
            self._choose(before[0])  # the pair's first click was not meant as one

    def cancel_drag(self):
        """Forget a drag under way; True if there was one."""
        dragging = self._drag is not None
        self._press = self._drag = None
        self.update()
        return dragging

    def keyPressEvent(self, event):
        key, modifiers = event.key(), event.modifiers()
        forward = key in (Qt.Key_BracketRight, Qt.Key_BraceRight)
        back = key in (Qt.Key_BracketLeft, Qt.Key_BraceLeft)
        if forward or back:
            direction = 1 if forward else -1
            if modifiers & Qt.AltModifier:
                self.step_flare(direction)
            else:
                self.step_frame(direction, to_end=bool(modifiers & Qt.ShiftModifier))
        elif key in (Qt.Key_Right, Qt.Key_Left) and modifiers == Qt.NoModifier:
            self.step_frame(1 if key == Qt.Key_Right else -1)
        elif key == Qt.Key_Home:
            self.step_frame(-1, to_end=True)
        elif key == Qt.Key_End:
            self.step_frame(1, to_end=True)
        elif key in (Qt.Key_Space, Qt.Key_P):
            self.play_toggle.emit()
        elif key in (Qt.Key_Greater, Qt.Key_Period):
            self.fps_step.emit(1)
        elif key in (Qt.Key_Less, Qt.Key_Comma):
            self.fps_step.emit(-1)
        elif key == Qt.Key_Escape:
            if not self.cancel_drag() and self.axis.zoomed:
                self.axis.whole_range()
            else:
                super().keyPressEvent(event)
        else:
            super().keyPressEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()

    # Tooltips

    def _frame_at(self, x):
        """The frame whose mark (or stretch of the bar) is at ``x``; None otherwise."""
        if not self.frames:
            return None
        hours = self.axis.hours_at(x, self)
        index = min(range(len(self.frames)), key=lambda i: (abs(self._mark_hours[i] - hours), i))
        if self.dense():
            per_px = (self.axis.end - self.axis.start) / max(1, self.axis.x_of(self.axis.end, self) - self.axis.x_of(self.axis.start, self))
            gaps = [abs(self._hours[j] - self._hours[index]) for j in (index - 1, index + 1) if 0 <= j < len(self.frames)]
            room = max(gaps, default=per_px) / 2
            return index if abs(self._hours[index] - hours) <= max(room, per_px) else None
        return index if abs(self.x_of_frame(index) - x) <= CURRENT else None

    def _tip_at(self, pos):
        edge = self._edge_marker()
        if edge is not None and edge.contains(QPointF(pos)):
            return EDGE_TIP
        left = self.axis.x_of(self.axis.start, self)
        if pos.x() < left:
            return self._events_note
        y = self.RULER
        for name in self.lanes():
            if y <= pos.y() < y + self.LANE + 2:
                if name == "Frames":
                    index = self._frame_at(pos.x())
                    if index is not None:
                        frame = self.frames[index]
                        return frame_tip(frame, self.original(index) if frame.duplicate_of is not None else None)
                elif name == "Your image":
                    x = self.axis.x_of(self.axis.hours_of(self.mark[1]), self)
                    if abs(pos.x() - max(left, min(self.axis.x_of(self.axis.end, self), x))) <= self.LANE:
                        return self.mark[2]
                else:
                    for start, _peak, end, _label, tip in self.flares:
                        x0 = self.axis.x_of(self.axis.hours_of(start), self) - 3
                        if x0 <= pos.x() <= max(self.axis.x_of(self.axis.hours_of(end), self), x0 + 6) + 3:
                            return tip
            y += self.LANE + 2
        return HINT if self._in_column(pos.x()) else ""

    def event(self, event):
        if event.type() == QEvent.ToolTip:
            QToolTip.showText(event.globalPos(), self._tip_at(event.pos()), self)
            return True
        return super().event(event)


class Playback(QObject):
    """Steps a band's frames at ``fps``, over the ready ones only, round and
    round; a click or key on the band pauses it."""

    FPS = (1, 2, 4, 8, 16)
    changed = pyqtSignal(bool)  # playing, or not
    fps_changed = pyqtSignal(int)

    def __init__(self, band, fps=4):
        super().__init__(band)
        self.band = band
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.fps = fps
        self.set_fps(fps)
        band.play_toggle.connect(self.toggle)
        band.fps_step.connect(self.step_fps)
        band.cursor_set.connect(self.pause)

    @property
    def playing(self):
        return self._timer.isActive()

    @property
    def interval(self):
        return int(round(1000 / self.fps))

    def set_fps(self, fps):
        fps = min(self.FPS, key=lambda f: abs(f - fps))
        self.fps = fps
        self._timer.setInterval(self.interval)
        self.fps_changed.emit(fps)

    def step_fps(self, direction):
        index = max(0, min(len(self.FPS) - 1, self.FPS.index(self.fps) + direction))
        self.set_fps(self.FPS[index])

    def play(self):
        if self.playing or self.band.next_ready(self.band.current) is None:
            return
        self._timer.start()
        self.changed.emit(True)

    def pause(self, *_args):
        if not self.playing:
            return
        self._timer.stop()
        self.band.stop_glide()
        self.changed.emit(False)

    def toggle(self):
        if self.playing:
            self.pause()
        else:
            self.play()

    def _tick(self):
        band = self.band
        index = band.next_ready(band.current, 1, wrap=True)
        if index is None:
            self.pause()
            return
        wrapped = band.current is not None and index <= band.current
        band.set_current(index, glide_ms=0 if wrapped else self.interval)


class EventsFetch(QThread):
    """Brings the flares and active regions of a range from Helioviewer."""

    done = pyqtSignal(object, str)  # rows, why there are none ("" when they came)

    def __init__(self, start, end, parent=None):
        super().__init__(parent)
        self.start_time, self.end_time = start, end
        self.rows = []
        self.error = ""

    def cancel(self):
        """Nothing to stop short: one request, bounded by its timeout."""

    def run(self):
        try:
            self.rows = hv.events(self.start_time, self.end_time)
        except Exception as e:
            logger.warning("No events from Helioviewer for %s–%s: %s", self.start_time, self.end_time, e)
            self.error = str(e) or "Helioviewer did not answer"
        self.done.emit(self.rows, self.error)


def fetch_events(band, start, end, parent=None, label="Finding flares…"):
    """Fetch the range's events off-thread and put the flares on ``band``, or
    its note when they cannot be had; ``label`` is shown meanwhile (None for
    none). Returns the thread, for the window to abandon on close."""
    thread = EventsFetch(start, end, parent)

    def finish(rows, error):
        if error:
            band.set_events_note(f"The flares could not be fetched: {error}")
        else:
            band.set_events(rows)

    thread.done.connect(finish)
    if label:
        track_thread(thread, label, parent)
    thread.start()
    return thread

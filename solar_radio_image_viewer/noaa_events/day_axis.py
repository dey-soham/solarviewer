"""
The UTC day the solar activity window is about: which part of it is on screen,
the cursor, and the time column every time-aligned view shares.
"""

import math
from datetime import datetime, time, timedelta

from PyQt5.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen, QPolygonF
from PyQt5.QtWidgets import QApplication, QToolTip, QWidget

from ..ui.styles import FONT_META, FONT_SMALL, MONO_FAMILY, SPACE_L, SPACE_S, SPACE_XS, theme_manager, themed


class DayAxis(QObject):
    """The day on screen. Hours are floats from 00:00 UTC of ``day``, 0 to 24."""

    window_changed = pyqtSignal()
    cursor_changed = pyqtSignal()
    cursor_set = pyqtSignal()  # someone chose where it goes, even where it is
    column_changed = pyqtSignal()
    MIN_SPAN = 0.5
    LAST_MINUTE = 1439 / 60  # the cursor stays in the day: 24:00 is the next one's

    def __init__(self, parent=None):
        super().__init__(parent)
        self.day, self.cursor = None, None  # date; hours or None
        self.start, self.end = 0.0, 24.0  # the part of the day shown
        self._left, self._right = 0, 1  # the time column, in window coordinates

    def set_day(self, day):
        """Keeps window and cursor: stepping days compares the same clock time."""
        self.day = day
        self.window_changed.emit()
        self.cursor_changed.emit()

    def set_window(self, start, end):
        start, end = min(start, end), max(start, end)
        if end - start < self.MIN_SPAN:
            middle = (start + end) / 2
            start, end = middle - self.MIN_SPAN / 2, middle + self.MIN_SPAN / 2
        if start < 0.0:
            start, end = 0.0, end - start
        if end > 24.0:
            start, end = start - (end - 24.0), 24.0
        start, end = max(start, 0.0), min(end, 24.0)
        if (start, end) != (self.start, self.end):
            self.start, self.end = start, end
            self.window_changed.emit()

    def whole_day(self):
        self.set_window(0.0, 24.0)

    def set_cursor(self, hours):
        """``None`` clears it. Outside a zoom it brings the zoom along, as
        nothing would show where it is."""
        self.cursor_set.emit()
        if hours is not None:
            hours = max(0.0, min(self.LAST_MINUTE, float(hours)))
            self._reveal(hours)
        if hours != self.cursor:
            self.cursor = hours
            self.cursor_changed.emit()

    def _reveal(self, hours):
        """A step past the edge pans by the step, a jump centres on ``hours``."""
        span = self.end - self.start
        shift = hours - self.end if hours > self.end else hours - self.start if hours < self.start else 0
        if abs(shift) > span / 2:
            shift = hours - (self.start + self.end) / 2
        if shift:
            self.set_window(self.start + shift, self.end + shift)

    def arrow_key(self, event):
        """Left or Right: the cursor a minute along. False for any other key."""
        if event.modifiers() != Qt.NoModifier or event.key() not in (Qt.Key_Left, Qt.Key_Right):
            return False
        step = -1 if event.key() == Qt.Key_Left else 1
        here = self.cursor if self.cursor is not None else (self.start + self.end) / 2
        # From the minute on show, to a whole one, as the tables give them
        self.set_cursor((round(here * 60) + step) / 60)
        return True

    def set_column(self, left, right):
        """Called by ``DayBand`` only: the band owns the time column."""
        if (left, right) != (self._left, self._right) and right > left:
            self._left, self._right = left, right
            self.column_changed.emit()

    def x_of(self, hours, widget):
        """Where ``hours`` falls, in ``widget``'s own x."""
        offset = widget.mapTo(widget.window(), QPoint(0, 0)).x()
        span = (self._right - self._left) / (self.end - self.start)
        return self._left - offset + (hours - self.start) * span

    def hours_at(self, x, widget):
        """The inverse of ``x_of``."""
        offset = widget.mapTo(widget.window(), QPoint(0, 0)).x()
        span = (self._right - self._left) / (self.end - self.start)
        return self.start + (x + offset - self._left) / span

    def cursor_text(self):
        """``13:16``, or "" with no cursor."""
        if self.cursor is None:
            return ""
        return _clock(self.cursor)

    def cursor_datetime(self):
        """The cursor as a naive UTC datetime, or ``None``."""
        if self.cursor is None or self.day is None:
            return None
        return datetime.combine(self.day, time()) + timedelta(hours=self.cursor)


GUTTER = 132  # the labels' column, right of the window's margin
TICK_ROOM = 44  # no tick label nearer than this to the cursor's own
EDGE_TIP = "The cursor is outside this zoom. Click to bring it into view."
GOES_COLOUR = "#d62728"  # the plot's colour for 1–8 Å: data, not chrome
LANES = ("X-ray", "H-alpha", "Radio", "CME")
HINT = "Click to set the cursor. Drag to zoom, double-click for the whole day."
LINK_INSET = 5  # a link's text sits this far in from the button's edge
EDGE_ARROW = 6  # the width of the arrow on the edge marker
EDGE_INSET = 14  # how far it reaches past the column, short of the day map


def _tick_step(hours, width):
    for step in (1 / 6, 0.25, 0.5, 1, 2, 3, 4, 6):
        if step / hours * width >= 56:
            return step
    return 6


def _clock(hours):
    minutes = int(round(hours * 60))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


class DayBand(QWidget):
    """The day at a glance, the same on every tab: ruler, GOES 1–8 Å and one
    lane per kind of event. It owns the time column; everything aligned to
    the day reads it through ``DayAxis``."""

    RULER, GOES, GOES_SMALL, LANE = 20, 52, 28, 16

    def __init__(self, axis, plot_button, whole_day_button, parent=None):
        super().__init__(parent)
        self.axis = axis
        self.lanes = {}  # lane -> [(begin, end, peak or None, label, tooltip)]
        self.flux = None  # one value per minute, None in a gap
        self.goes_text = ""  # what the GOES lane says while it has no curve
        self._goes_height = self.GOES
        self._press = None  # the x a drag started from
        self._drag = None
        self._before_click = None  # (the cursor the click before just replaced,)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setAccessibleName("Day band")
        self._set_height()

        self.plot_button = plot_button
        plot_button.setParent(self)
        self.whole_day_button = whole_day_button
        whole_day_button.setParent(self)
        # The app's button height would push it over Open plot and take its clicks
        whole_day_button.setStyleSheet("#LinkButton { min-height: 0px; }")
        whole_day_button.hide()
        axis.window_changed.connect(self._place_button)
        for signal in (axis.window_changed, axis.cursor_changed):
            signal.connect(self.update)
        themed(self, lambda palette: self.update())

    def _set_height(self):
        self.setFixedHeight(self.RULER + self._goes_height + SPACE_XS + len(LANES) * (self.LANE + 2) + 2)

    def set_compact(self, compact):
        """A small window keeps its height for the tabs."""
        height = self.GOES_SMALL if compact else self.GOES
        if height != self._goes_height:
            self._goes_height = height
            self._set_height()
            self._place_button()

    def set_events(self, lanes):
        self.lanes = lanes
        self.update()

    def set_flux(self, flux):
        self.flux, self.goes_text = flux, ""
        self.update()

    def set_goes_text(self, text):
        self.flux, self.goes_text = None, text
        self.update()

    def event_hours(self):
        return sorted({item[0] for items in self.lanes.values() for item in items})

    def step_event(self, direction):
        """The cursor to the event before (-1) or after (1) it."""
        here = self.axis.cursor if self.axis.cursor is not None else (-1.0 if direction > 0 else 25.0)
        times = [t for t in self.event_hours() if (t - here) * direction > 1e-6]
        if times:
            self.axis.set_cursor(times[0] if direction > 0 else times[-1])

    # The column

    def _publish_column(self):
        offset = self.mapTo(self.window(), QPoint(0, 0)).x()
        self.axis.set_column(offset + SPACE_L + GUTTER, offset + self.width() - SPACE_L)
        self.update()

    def _place_button(self):
        hint = self.plot_button.sizeHint()
        fits_below = self._goes_height >= hint.height() + self.LANE
        x = SPACE_L - LINK_INSET if fits_below else SPACE_L + SPACE_L + SPACE_L + LINK_INSET
        y = self.RULER + self.LANE - 2 if fits_below else self.RULER - 2
        self.plot_button.setGeometry(x, y, hint.width(), hint.height())

        zoomed = self.axis.end - self.axis.start < 24.0
        self.whole_day_button.setVisible(zoomed)
        # Under Open plot, or in a small window over the day map, which it replaces
        y = self.plot_button.geometry().bottom() + 1 if fits_below else 0
        x = SPACE_L - LINK_INSET if fits_below else SPACE_L + 36 - LINK_INSET
        self.whole_day_button.setGeometry(x, y, self.whole_day_button.sizeHint().width(), self.RULER - 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._publish_column()
        self._place_button()

    def showEvent(self, event):
        super().showEvent(event)
        self._publish_column()

    def _in_column(self, x):
        return self.axis.x_of(self.axis.start, self) - 1 <= x <= self.axis.x_of(self.axis.end, self) + 1

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
        goes_top = self.RULER
        self._paint_goes(painter, palette, left, right, goes_top, self._goes_height)
        y = goes_top + self._goes_height + SPACE_XS
        for name in LANES:
            self._paint_lane(painter, palette, name, y, left, right)
            y += self.LANE + 2
        self._paint_cursor(painter, palette, left, right, bottom)
        self._paint_drag(painter, palette, left, right, bottom)
        painter.end()

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
                painter.drawText(QRectF(x - 28, 2, 56, 15), Qt.AlignCenter, _clock(hour))
            hour += step
        painter.setFont(self._font(FONT_SMALL))
        painter.setPen(QColor(palette["text_secondary"]))
        painter.drawText(QRectF(SPACE_L, 2, 32, 15), Qt.AlignVCenter, "UTC")
        if span < 24.0 and self._goes_height == self.GOES:  # where in the day the window is
            track = QRectF(SPACE_L + 36, 8, GUTTER - 52, 3)
            painter.fillRect(track, QColor(palette["plot_grid"]))
            painter.fillRect(
                QRectF(
                    track.left() + track.width() * self.axis.start / 24, track.top() - 1,
                    max(2.0, track.width() * span / 24), track.height() + 2,
                ),
                QColor(palette["highlight"]),
            )
        if self.hasFocus():
            painter.setPen(QColor(palette["highlight"]))
            painter.drawLine(QPointF(left, self.RULER - 2), QPointF(right, self.RULER - 2))

    def _paint_goes(self, painter, palette, left, right, top, height):
        painter.setFont(self._font(FONT_SMALL))
        painter.setPen(QColor(palette["text_secondary"]))
        name = "GOES" if self._goes_height == self.GOES_SMALL else "GOES 1–8 Å"  # beside Open plot
        painter.drawText(QRectF(SPACE_L, top, GUTTER - SPACE_S, self.LANE), Qt.AlignVCenter, name)
        area = QRectF(left, top, right - left, height)
        if self.flux is None:
            painter.setFont(self._font(FONT_SMALL))
            painter.drawText(area, Qt.AlignCenter, self.goes_text)
            return
        known = [v for v in self.flux if v]
        if not known:
            painter.drawText(area, Qt.AlignCenter, "No GOES data for this date")
            return
        low = math.floor(math.log10(min(known)))
        high = max(math.ceil(math.log10(max(known))), low + 2)
        y_of = lambda value: top + height - 1 - (math.log10(value) - low) / (high - low) * (height - 2)

        painter.setFont(self._font(FONT_META, mono=True))
        room = height / (high - low) >= 11
        for name, power in (("A", -8), ("B", -7), ("C", -6), ("M", -5), ("X", -4)):
            if low <= power <= high:
                y = y_of(10.0 ** power)
                painter.setPen(QPen(QColor(palette["plot_grid"]), 1, Qt.DotLine))
                painter.drawLine(QPointF(left, y), QPointF(right, y))
                if room:
                    painter.setPen(QColor(palette["text_faint"]))
                    painter.drawText(QRectF(left - 14, y - 7, 11, 14), Qt.AlignRight | Qt.AlignVCenter, name)

        painter.save()
        painter.setClipRect(area)
        painter.setPen(QPen(QColor(GOES_COLOUR), 1.2))
        run = []
        for minute, value in enumerate(self.flux):
            if value:
                run.append(QPointF(self.axis.x_of(minute / 60, self), y_of(value)))
            elif run:
                painter.drawPolyline(QPolygonF(run))
                run = []
        if run:
            painter.drawPolyline(QPolygonF(run))
        painter.restore()

    def _paint_lane(self, painter, palette, name, y, left, right):
        height = self.LANE
        painter.setFont(self._font(FONT_SMALL))
        painter.setPen(QColor(palette["text_secondary"]))
        painter.drawText(QRectF(SPACE_L, y, GUTTER - SPACE_S, height), Qt.AlignVCenter, name)
        painter.setPen(QColor(palette["plot_grid"]))
        painter.drawLine(QPointF(left, y + height + 0.5), QPointF(right, y + height + 0.5))

        fill, tick = {
            "X-ray": ("warning_soft", "warning"),
            "H-alpha": ("highlight_soft", "highlight"),
            "Radio": ("link", None),
            "CME": ("text", None),
        }[name]
        painter.save()
        painter.setClipRect(QRectF(left, y, right - left, height + 1))
        items = self.lanes.get(name, ())
        reached = left  # a short event can lie inside the one before it
        for index, (begin, end, peak, label, _tip) in enumerate(items):
            if end < self.axis.start or begin > self.axis.end:
                continue
            x0 = self.axis.x_of(begin, self)
            x1 = max(self.axis.x_of(end, self), x0 + 3)
            if name == "CME":
                size = height - 6
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(palette[fill]))
                painter.drawPolygon(
                    QPolygonF([QPointF(x0, y + 3), QPointF(x0 + size * 0.85, y + 3 + size / 2), QPointF(x0, y + 3 + size)])
                )
                x1 = x0 + size
            else:
                painter.fillRect(QRectF(x0, y + 2, x1 - x0, height - 4), QColor(palette[fill]))
                if tick and peak is not None:
                    x = self.axis.x_of(peak, self)
                    painter.setPen(QPen(QColor(palette[tick]), 1.5))
                    painter.drawLine(QPointF(x, y + 1), QPointF(x, y + height - 1))
            painter.setFont(self._font(FONT_META, mono=True))
            advance = painter.fontMetrics().horizontalAdvance(label)
            reached = max(reached, x1)
            # A label only where the next event leaves it room
            room = right if index + 1 == len(items) else self.axis.x_of(items[index + 1][0], self) - SPACE_XS
            if label and reached + 3 + advance <= room:
                painter.setPen(QColor(palette["text"]))
                painter.drawText(QRectF(reached + 3, y, advance + 2, height), Qt.AlignVCenter, label)
        painter.restore()

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
        text = self.axis.cursor_text()
        width = painter.fontMetrics().horizontalAdvance(text) + SPACE_S
        box = QRectF(min(max(x - width / 2, left - 20), right + 12 - width), 2, width, 15)
        painter.fillRect(box, QColor(palette["highlight_soft"]))
        painter.setPen(QColor(palette["text"]))
        painter.drawText(box, Qt.AlignCenter, text)

    def _edge_marker(self):
        """Where the ruler says the cursor is, if it is outside the zoom:
        at the edge it lies beyond, and ``None`` otherwise."""
        hours = self.axis.cursor
        if hours is None or self.axis.start <= hours <= self.axis.end:
            return None
        width = QFontMetricsF(self._font(FONT_META, mono=True)).horizontalAdvance("00:00") + 2 * SPACE_S + EDGE_ARROW
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
        painter.drawPolygon(
            QPolygonF([QPointF(tip, middle), QPointF(back, middle - 4), QPointF(back, middle + 4)])
        )
        painter.setFont(self._font(FONT_META, mono=True))
        painter.setPen(QColor(palette["text"]))
        text = box.adjusted(EDGE_ARROW, 0, 0, 0) if before else box.adjusted(0, 0, -EDGE_ARROW, 0)
        painter.drawText(text, Qt.AlignCenter, self.axis.cursor_text())

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
            self._before_click = (self.axis.cursor,)
            self.axis.set_cursor(self.axis.hours_at(press, self))
        else:
            self.axis.set_window(self.axis.hours_at(press, self), self.axis.hours_at(drag, self))
        self.update()

    def mouseDoubleClickEvent(self, event):
        self._press = self._drag = None
        self.axis.whole_day()
        before, self._before_click = self._before_click, None
        if before and event.button() == Qt.LeftButton and self._in_column(event.x()):
            self.axis.set_cursor(before[0])  # the pair's first click was not meant as one

    def cancel_drag(self):
        """Forget a drag under way; True if there was one."""
        dragging = self._drag is not None
        self._press = self._drag = None
        self.update()
        return dragging

    def keyPressEvent(self, event):
        if not self.axis.arrow_key(event):
            super().keyPressEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()

    def _tip_at(self, pos):
        edge = self._edge_marker()
        if edge is not None and edge.contains(QPointF(pos)):
            return EDGE_TIP
        y = self.RULER + self._goes_height + SPACE_XS
        for name in LANES:
            if y <= pos.y() < y + self.LANE + 2:
                for begin, end, _peak, _label, tip in self.lanes.get(name, ()):
                    x0 = self.axis.x_of(begin, self) - 3
                    if x0 <= pos.x() <= max(self.axis.x_of(end, self), x0 + 6) + 3:
                        return tip
            y += self.LANE + 2
        return HINT if self._in_column(pos.x()) else ""

    def event(self, event):
        if event.type() == QEvent.ToolTip:
            QToolTip.showText(event.globalPos(), self._tip_at(event.pos()), self)
            return True
        return super().event(event)

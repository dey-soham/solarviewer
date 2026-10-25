"""A date and time picker for a text field holding "YYYY-MM-DD HH:MM:SS":
a calendar button inside the field opens a popup with a month grid (the
title switches to months, then years), a time entry and Now / Done. The
field stays typeable; the popup changes it as you pick."""

import calendar
from datetime import date, datetime, time, timedelta

from PyQt5.QtCore import QEvent, QObject, QPoint, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .styles import FONT_BODY, FONT_META, FONT_SMALL, RADIUS, RADIUS_LG, theme_manager

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
FIRST_YEAR = 1990  # before any of the archives
CELL = QSize(34, 30)  # a day's cell
YEARS_PER_PAGE = 12


def _color(name, alpha=None):
    color = QColor(theme_manager.palette[name])
    if alpha is not None:
        color.setAlphaF(alpha)
    return color


def _chosen():
    """The fill and text of the chosen day, month or year: the deep accent
    with the brightest text on dark, the accent with white on light."""
    if theme_manager.is_dark:
        return _color("highlight_deep"), _color("text_bright")
    return _color("highlight"), _color("highlight_text")


def _band():
    """The shading of the days in the window."""
    return _color("highlight_soft") if theme_manager.is_dark else _color("highlight", 0.16)


def calendar_icon(size=16):
    """A calendar glyph in the muted text colour, drawn at the screen's
    scale so it stays sharp."""
    ratio = QApplication.instance().devicePixelRatio() if QApplication.instance() else 1.0
    pixmap = QPixmap(int(size * ratio), int(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(_color("text_secondary"), 1.3)
    painter.setPen(pen)
    body = QRectF(2.5, 3.5, size - 5, size - 6)
    painter.drawRoundedRect(body, 2, 2)
    painter.drawLine(QPoint(3, 7), QPoint(size - 3, 7))
    for x in (5.5, size - 5.5):
        painter.drawLine(QPoint(int(x), 2), QPoint(int(x), 5))
    painter.setPen(Qt.NoPen)
    painter.setBrush(_color("text_secondary"))
    for row in range(2):
        for col in range(3):
            painter.drawRect(QRectF(5 + col * 2.6, 9 + row * 2.5, 1.4, 1.4))
    painter.end()
    return QIcon(pixmap)


class CalendarGrid(QWidget):
    """The painted grid: the days of a month, the months of a year or a
    page of years. Days after ``latest`` cannot be picked (no data yet);
    the days from ``band`` to the chosen one are shaded (the window)."""

    picked = pyqtSignal(object)  # a date, in the days view
    title_changed = pyqtSignal()

    DAYS, MONTHS, YEARS = range(3)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.mode = self.DAYS
        self.selected = date.today()
        self.shown = self.selected.replace(day=1)  # the month on screen
        self.latest = date.today()
        self.band = None
        self._hover = None
        self.first_weekday = 0  # Monday
        self.setFixedSize(CELL.width() * 7, CELL.height() * 7)

    # what is on screen
    def title(self):
        if self.mode == self.DAYS:
            return self.shown.strftime("%B %Y")
        if self.mode == self.MONTHS:
            return str(self.shown.year)
        first = self._first_year()
        return f"{first} – {first + YEARS_PER_PAGE - 1}"

    def _first_year(self):
        return self.shown.year - (self.shown.year - FIRST_YEAR) % YEARS_PER_PAGE

    def set_date(self, day):
        self.selected = day
        self.shown = day.replace(day=1)
        self.mode = self.DAYS
        self.update()
        self.title_changed.emit()

    def step(self, sign):
        """The previous (-1) or next (+1) month, year or page of years."""
        year, month = self.shown.year, self.shown.month
        if self.mode == self.DAYS:
            month += sign
            year, month = year + (month - 1) // 12, (month - 1) % 12 + 1
        elif self.mode == self.MONTHS:
            year += sign
        else:
            year += sign * YEARS_PER_PAGE
        year = min(max(year, FIRST_YEAR), self.latest.year)
        self.shown = date(year, month, 1)
        self.update()
        self.title_changed.emit()

    def can_step(self, sign):
        if self.mode == self.DAYS:
            target = (self.shown.year * 12 + self.shown.month - 1) + sign
            return FIRST_YEAR * 12 <= target <= self.latest.year * 12 + self.latest.month - 1
        if self.mode == self.MONTHS:
            return FIRST_YEAR <= self.shown.year + sign <= self.latest.year
        first = self._first_year() + sign * YEARS_PER_PAGE
        return first + YEARS_PER_PAGE > FIRST_YEAR and first <= self.latest.year

    def zoom_out(self):
        """Days → months → years (the title was clicked)."""
        if self.mode < self.YEARS:
            self.mode += 1
            self._hover = None
            self.update()
            self.title_changed.emit()

    # the cells
    def _days(self):
        """(date, rect) for the 42 cells under the weekday names."""
        offset = (self.shown.weekday() - self.first_weekday) % 7
        start = self.shown - timedelta(days=offset)
        w, h = CELL.width(), CELL.height()
        return [(start + timedelta(days=i), QRect((i % 7) * w, (i // 7 + 1) * h, w, h)) for i in range(42)]

    def _blocks(self):
        """(value, rect) for the 12 months or years, in a 3 × 4 grid."""
        w, h = self.width() // 3, self.height() // 4
        if self.mode == self.MONTHS:
            values = range(1, 13)
        else:
            values = range(self._first_year(), self._first_year() + YEARS_PER_PAGE)
        return [(v, QRect((i % 3) * w, (i // 3) * h, w, h)) for i, v in enumerate(values)]

    def _cells(self):
        return self._days() if self.mode == self.DAYS else self._blocks()

    def _enabled(self, value):
        if self.mode == self.DAYS:
            return date(FIRST_YEAR, 1, 1) <= value <= self.latest
        if self.mode == self.MONTHS:
            return (self.shown.year, value) <= (self.latest.year, self.latest.month)
        return FIRST_YEAR <= value <= self.latest.year

    def _at(self, pos):
        for value, rect in self._cells():
            if rect.contains(pos):
                return value if self._enabled(value) else None
        return None

    # events
    def mouseMoveEvent(self, event):
        hover = self._at(event.pos())
        if hover != self._hover:
            self._hover = hover
            self.setCursor(Qt.PointingHandCursor if hover is not None else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, event):
        self._hover = None
        self.update()

    def mousePressEvent(self, event):
        value = self._at(event.pos())
        if value is None or event.button() != Qt.LeftButton:
            return
        if self.mode == self.DAYS:
            self.selected = value
            if (value.year, value.month) != (self.shown.year, self.shown.month):
                self.shown = value.replace(day=1)
                self.title_changed.emit()
            self.update()
            self.picked.emit(value)
        elif self.mode == self.MONTHS:
            self.shown = date(self.shown.year, value, 1)
            self.mode = self.DAYS
            self.title_changed.emit()
        else:
            self.shown = date(value, min(self.shown.month, 12 if value < self.latest.year else self.latest.month), 1)
            self.mode = self.MONTHS
            self.title_changed.emit()
        self._hover = None
        self.update()

    def keyPressEvent(self, event):
        moves = {Qt.Key_Left: -1, Qt.Key_Right: 1, Qt.Key_Up: -7, Qt.Key_Down: 7}
        if self.mode == self.DAYS and event.key() in moves:
            day = self.selected + timedelta(days=moves[event.key()])
        elif self.mode == self.DAYS and event.key() in (Qt.Key_PageUp, Qt.Key_PageDown):
            sign = -1 if event.key() == Qt.Key_PageUp else 1
            month = self.selected.month - 1 + sign
            year, month = self.selected.year + month // 12, month % 12 + 1
            day = date(year, month, min(self.selected.day, calendar.monthrange(year, month)[1]))
        else:
            return super().keyPressEvent(event)
        if self._enabled(day):
            self.set_date(day)
            self.picked.emit(day)

    def wheelEvent(self, event):
        sign = -1 if event.angleDelta().y() > 0 else 1
        if self.can_step(sign):
            self.step(sign)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        font = QFont(self.font())
        font.setPointSizeF(FONT_SMALL)
        if self.mode == self.DAYS:
            self._paint_days(painter, font)
        else:
            self._paint_blocks(painter, font)
        painter.end()

    def _paint_days(self, painter, font):
        w, h = CELL.width(), CELL.height()
        small = QFont(font)
        small.setPointSizeF(FONT_META)
        painter.setFont(small)
        painter.setPen(_color("text_secondary"))
        for i in range(7):
            name = calendar.day_abbr[(self.first_weekday + i) % 7][:2]
            painter.drawText(QRect(i * w, 0, w, h), Qt.AlignCenter, name)
        painter.setFont(font)
        today = date.today()
        low, high = sorted((self.band, self.selected)) if self.band else (None, None)
        for day, rect in self._days():
            inner = QRectF(rect).adjusted(2, 2, -2, -2)
            in_month = day.month == self.shown.month
            if low is not None and low <= day <= high and low != high:
                # the window, as a band joining the two ends
                band = QRectF(rect).adjusted(0, 3, 0, -3)
                if day == low:
                    band.setLeft(inner.center().x())
                if day == high:
                    band.setRight(inner.center().x())
                painter.fillRect(band, _band())
            selected = day == self.selected
            end = self.band is not None and day == self.band
            painter.setPen(Qt.NoPen)
            if selected:
                painter.setBrush(_chosen()[0])
                painter.drawRoundedRect(inner, RADIUS, RADIUS)
            elif day == self._hover:
                painter.setBrush(_color("highlight", 0.18))
                painter.drawRoundedRect(inner, RADIUS, RADIUS)
            if end and not selected:
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(_color("highlight"), 1))
                painter.drawRoundedRect(inner.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)
            if day == today and not selected:
                painter.setPen(Qt.NoPen)
                painter.setBrush(_color("highlight"))
                painter.drawEllipse(QRectF(inner.center().x() - 1.5, inner.bottom() - 5, 3, 3))
            if selected:
                color = _chosen()[1]
            elif not self._enabled(day):
                color = _color("disabled")
            elif not in_month:
                color = _color("text_secondary")
            else:
                color = _color("text")
            painter.setPen(color)
            painter.drawText(rect, Qt.AlignCenter, str(day.day))

    def _paint_blocks(self, painter, font):
        painter.setFont(font)
        current = self.selected.month if self.mode == self.MONTHS else self.selected.year
        same = self.mode == self.YEARS or self.selected.year == self.shown.year
        for value, rect in self._blocks():
            inner = QRectF(rect).adjusted(4, 6, -4, -6)
            chosen = same and value == current
            painter.setPen(Qt.NoPen)
            if chosen:
                painter.setBrush(_chosen()[0])
                painter.drawRoundedRect(inner, RADIUS, RADIUS)
            elif value == self._hover:
                painter.setBrush(_color("highlight", 0.18))
                painter.drawRoundedRect(inner, RADIUS, RADIUS)
            text = calendar.month_abbr[value] if self.mode == self.MONTHS else str(value)
            if chosen:
                painter.setPen(_chosen()[1])
            else:
                painter.setPen(_color("text" if self._enabled(value) else "disabled"))
            painter.drawText(rect, Qt.AlignCenter, text)


class TimeEdit(QLineEdit):
    """HH:MM:SS. The arrow keys and the wheel step the part under the
    cursor (hours, minutes or seconds), wrapping round."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setInputMask("99:99:99")
        font = QFont(self.font())
        font.setFamily("monospace")
        font.setStyleHint(QFont.Monospace)
        self.setFont(font)
        self.setAlignment(Qt.AlignCenter)
        self.setFixedWidth(self.fontMetrics().horizontalAdvance("00:00:00") + 28)
        self.setMinimumHeight(28)
        self.setToolTip("Hours : minutes : seconds (UTC)\nThe arrow keys or the wheel step the part under the cursor")

    def value(self):
        try:
            h, m, s = (int(part) for part in self.text().split(":"))
            return time(h, m, s)
        except ValueError:
            return None

    def set_value(self, value):
        position = self.cursorPosition()
        self.setText(value.strftime("%H:%M:%S"))
        self.setCursorPosition(position)

    def _step(self, sign):
        value = self.value() or time(0, 0, 0)
        part = min(self.cursorPosition() // 3, 2)
        h, m, s = value.hour, value.minute, value.second
        if part == 0:
            h = (h + sign) % 24
        elif part == 1:
            m = (m + sign) % 60
        else:
            s = (s + sign) % 60
        self.set_value(time(h, m, s))
        self.textEdited.emit(self.text())

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Up, Qt.Key_Down):
            self._step(1 if event.key() == Qt.Key_Up else -1)
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event):
        self._step(1 if event.angleDelta().y() > 0 else -1)
        event.accept()


class DateTimePopup(QFrame):
    """The popup under a field: a month header, the grid, the time and
    Now / Done. Emits ``changed`` with each pick."""

    changed = pyqtSignal(object)  # a datetime

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setObjectName("PickerPopup")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(4)
        self.title_button = QPushButton()
        self.title_button.setObjectName("PickerTitle")
        self.title_button.setCursor(Qt.PointingHandCursor)
        self.title_button.setToolTip("Choose the month or year")
        self.prev_button = QPushButton("‹")
        self.next_button = QPushButton("›")
        for button, tip in ((self.prev_button, "Earlier"), (self.next_button, "Later")):
            button.setObjectName("PickerArrow")
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip(tip)
            button.setFixedSize(28, 28)
        head.addWidget(self.title_button)
        head.addStretch()
        head.addWidget(self.prev_button)
        head.addWidget(self.next_button)
        layout.addLayout(head)

        self.grid = CalendarGrid()
        layout.addWidget(self.grid, 0, Qt.AlignHCenter)

        rule = QFrame()
        rule.setObjectName("PickerRule")
        rule.setFixedHeight(1)
        layout.addWidget(rule)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        label = QLabel("Time")
        label.setObjectName("Hint")
        foot.addWidget(label)
        self.time_edit = TimeEdit()
        foot.addWidget(self.time_edit)
        utc = QLabel("UTC")
        utc.setObjectName("Meta")
        foot.addWidget(utc)
        foot.addStretch()
        self.now_button = QPushButton("Now")
        self.now_button.setObjectName("LinkButton")
        self.now_button.setCursor(Qt.PointingHandCursor)
        self.now_button.setToolTip("The current time, UTC")
        self.done_button = QPushButton("Done")
        self.done_button.setObjectName("PrimaryButton")
        self.done_button.setCursor(Qt.PointingHandCursor)
        self.done_button.setDefault(True)
        foot.addWidget(self.now_button)
        foot.addWidget(self.done_button)
        layout.addLayout(foot)

        self.title_button.clicked.connect(self.grid.zoom_out)
        self.prev_button.clicked.connect(lambda: self.grid.step(-1))
        self.next_button.clicked.connect(lambda: self.grid.step(1))
        self.grid.title_changed.connect(self._update_head)
        self.grid.picked.connect(lambda _: self._emit())
        self.time_edit.textEdited.connect(lambda _: self._emit())
        self.now_button.clicked.connect(self._now)
        self.done_button.clicked.connect(self.close)

    def set_value(self, value, band=None):
        """Show ``value`` (a datetime), the window's other end ``band``."""
        self.grid.latest = datetime.utcnow().date()
        self.grid.band = band.date() if band is not None else None
        self.grid.set_date(min(value.date(), self.grid.latest))
        self.time_edit.set_value(value.time())
        self._update_head()

    def value(self):
        clock = self.time_edit.value()
        return None if clock is None else datetime.combine(self.grid.selected, clock)

    def _emit(self):
        value = self.value()
        if value is not None:
            self.changed.emit(value)

    def _now(self):
        now = datetime.utcnow().replace(microsecond=0)
        self.set_value(now, None if self.grid.band is None else datetime.combine(self.grid.band, time()))
        self._emit()

    def _update_head(self):
        mode = self.grid.mode
        self.title_button.setText(self.grid.title() + ("  ▾" if mode < CalendarGrid.YEARS else ""))
        self.title_button.setEnabled(mode < CalendarGrid.YEARS)
        self.prev_button.setEnabled(self.grid.can_step(-1))
        self.next_button.setEnabled(self.grid.can_step(1))

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.close()
        elif event.key() == Qt.Key_Escape and self.grid.mode != CalendarGrid.DAYS:
            self.grid.mode -= 1
            self.grid.update()
            self._update_head()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), RADIUS_LG, RADIUS_LG)
        painter.fillPath(path, _color("surface_elevated"))
        painter.setPen(QPen(_color("border_light"), 1))
        painter.drawPath(path)
        painter.end()

    def show_under(self, field):
        """Open below ``field`` (above it when there is no room), kept on
        its screen."""
        self.adjustSize()
        screen = (field.screen() or QApplication.primaryScreen()).availableGeometry()
        below = field.mapToGlobal(QPoint(0, field.height() + 4))
        x = min(max(below.x(), screen.left()), screen.right() - self.width())
        y = below.y()
        if y + self.height() > screen.bottom():
            y = field.mapToGlobal(QPoint(0, 0)).y() - self.height() - 4
        self.move(x, max(y, screen.top()))
        self.show()
        self.grid.setFocus()


class _Picker(QObject):
    """Keeps a field's calendar button: opens the popup, writes the picks
    into the field and redraws the icon when the theme changes."""

    def __init__(self, field, on_change, band):
        super().__init__(field)
        self.field = field
        self.on_change = on_change
        self.band = band
        self.popup = None
        self.action = QAction(calendar_icon(), "Pick a date and time", field)
        self.action.setToolTip("Pick a date and time (Alt+Down)")
        self.action.triggered.connect(self.open)
        field.addAction(self.action, QLineEdit.TrailingPosition)
        field.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.StyleChange:
            self.action.setIcon(calendar_icon())
        elif event.type() == QEvent.KeyPress and event.key() == Qt.Key_Down and event.modifiers() & Qt.AltModifier:
            self.open()
            return True
        return False

    def open(self):
        if self.popup is None:
            self.popup = DateTimePopup(self.field)
            self.popup.changed.connect(self._picked)
        try:
            value = datetime.strptime(self.field.text().strip(), TIME_FORMAT)
        except ValueError:
            value = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
        self.popup.set_value(value, self.band() if self.band else None)
        self.popup.show_under(self.field)

    def _picked(self, value):
        text = value.strftime(TIME_FORMAT)
        if text != self.field.text():
            self.field.setText(text)
            self.on_change(text)


def attach_date_picker(field, on_change, band=None):
    """Give ``field`` (a QLineEdit of TIME_FORMAT text) a calendar button.
    Each pick sets its text and calls ``on_change(text)``, as typing calls
    a textEdited slot. ``band()`` returns the other end of the window (a
    datetime or None), shaded in the calendar."""
    return _Picker(field, on_change, band)

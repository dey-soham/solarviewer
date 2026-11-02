"""
The day's indices as strips on the window's time column: Kp with Hp30, Dst and
the solar wind. Each follows the day band's zoom and cursor, and a click or a
drag on one works as it does on the band.
"""

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QPen, QPolygonF
from PyQt5.QtWidgets import QVBoxLayout, QWidget

from ..solar_context.realtime_data import g_scale
from ..ui.styles import FONT_META, FONT_SMALL, SPACE_S, SPACE_XS
from .radio_spectra_tab import TimeRow, _font

STRIP_GAP = 2
GUTTER_LINE = 15
KP_HEIGHT = 56
STRIP_HEIGHT = 42
LINE_COLOUR = "link"  # data on the page's ground, as the band's radio lane
KP_MOST = 9


def kp_text(value):
    """A Kp value in the thirds it is given in: 2, 2+, 3-."""
    if not 0 <= value <= KP_MOST:  # (and not a NaN)
        return "—"
    thirds = round(value * 3)
    return f"{(thirds + 1) // 3}" + {0: "", 1: "+", 2: "-"}[thirds % 3]


def storm_tone(kp_max):
    """The one rule for a tone here: none below G1, so that a quiet day reads
    as quiet."""
    level = g_scale(kp_max)
    return "error" if level >= 3 else "warning" if level else None


def g_level(kp):
    """G1 to G5 for a Kp, "" below G1."""
    level = g_scale(kp)
    return f"G{level}" if level else ""


class IndexStrip(TimeRow):
    """One index: its name, its reading at the cursor (or its extreme over the
    day) and its scale in the gutter, the values on the column, or a sentence."""

    def __init__(self, axis, name, unit, tip, height=STRIP_HEIGHT, parent=None):
        super().__init__(axis, parent)
        self.name, self.unit = name, unit
        self.text = ""  # what it says while it has no values
        self.trouble = False
        self.setFixedHeight(height + STRIP_GAP)
        self.setCursor(Qt.CrossCursor)
        self.setToolTip(tip)
        for signal in (axis.window_changed, axis.cursor_changed, axis.column_changed):
            signal.connect(self.update)

    def has_values(self):
        raise NotImplementedError

    def clear(self, text="", trouble=False):
        self.text, self.trouble = text, trouble
        self.update()

    def reading(self):
        """The gutter's second line."""
        return ""

    def scale(self):
        """``(top, bottom)`` of the column, as text."""
        return "", ""

    def paint_row(self, painter, width, height, left, right, start, end, palette):
        painter.setRenderHint(painter.Antialiasing)
        body = QRectF(left, 0, right - left, height - STRIP_GAP - 1)
        gutter = QRectF(0, 0, left - SPACE_S, body.height())
        painter.setFont(_font(FONT_SMALL, bold=True))
        painter.setPen(QColor(palette["text"]))
        painter.drawText(gutter, Qt.AlignTop | Qt.AlignLeft, self.name)
        painter.setPen(QColor(palette["plot_grid"]))
        painter.drawRect(body)
        if not self.has_values():
            painter.setFont(_font(FONT_SMALL))
            painter.setPen(QColor(palette["warning" if self.trouble else "text_secondary"]))
            painter.drawText(body.adjusted(SPACE_S, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, self.text)
            return
        painter.setFont(_font(FONT_META))
        painter.setPen(QColor(palette["text_secondary"]))
        painter.drawText(gutter.adjusted(0, GUTTER_LINE, 0, 0), Qt.AlignTop | Qt.AlignLeft, self.reading())
        top, bottom = self.scale()
        painter.setFont(_font(FONT_META, mono=True))
        painter.drawText(gutter, Qt.AlignTop | Qt.AlignRight, top)
        painter.drawText(gutter, Qt.AlignBottom | Qt.AlignRight, bottom)
        painter.save()
        painter.setClipRect(body)
        scale = (right - left) / (end - start)
        self.paint_values(painter, body, lambda hours: left + (hours - start) * scale, palette)
        painter.restore()


class SeriesStrip(IndexStrip):
    """A curve from values at a fixed step. ``zero``: its scale holds zero, and
    the line is drawn; ``levels``: dotted lines worth having."""

    def __init__(self, axis, name, unit, tip, least, most, digits=0, zero=False, stepped=False, extreme=max,
                 levels=(), parent=None):
        super().__init__(axis, name, unit, tip, parent=parent)
        self.values = None
        self.step = 1  # minutes
        self.least, self.most = least, most  # the scale goes no narrower
        self.digits, self.zero, self.stepped, self.extreme = digits, zero, stepped, extreme
        self.levels = levels
        self.low, self.high = least, most

    def has_values(self):
        return self.values is not None

    def set_values(self, values, step):
        self.values, self.step, self.text = values, step, ""
        known = [value for value in values if value is not None]
        pad = 0.08 * ((max(known) - min(known)) or 1)
        self.low = self.least if min(known) >= self.least else min(known) - pad
        self.high = self.most if max(known) <= self.most else max(known) + pad
        self.update()

    def clear(self, text="", trouble=False):
        self.values = None
        super().clear(text, trouble)

    def _value_text(self, value):
        return f"{value:.{self.digits}f} {self.unit}"

    def value_at(self, hours):
        return self.values[min(int(hours * 60 // self.step), len(self.values) - 1)]

    def reading(self):
        hours = self.axis.cursor
        if hours is not None:
            value = self.value_at(hours)
            return "no value" if value is None else self._value_text(value)
        known = [value for value in self.values if value is not None]
        word = "max" if self.extreme is max else "min"
        return f"{word} {self._value_text(self.extreme(known))}"

    def scale(self):
        return f"{self.high:.0f}", f"{self.low:.0f}"

    def paint_values(self, painter, body, x, palette):
        y = lambda value: body.bottom() - 2 - (value - self.low) / (self.high - self.low) * (body.height() - 4)
        for level in self.levels:
            if self.low <= level <= self.high:
                painter.setPen(QPen(QColor(palette["plot_grid"]), 1, Qt.DotLine))
                painter.drawLine(QPointF(body.left(), y(level)), QPointF(body.right(), y(level)))
        if self.zero and self.low <= 0 <= self.high:
            painter.setPen(QPen(QColor(palette["text_faint"]), 1))
            painter.drawLine(QPointF(body.left(), y(0)), QPointF(body.right(), y(0)))
        painter.setPen(QPen(QColor(palette[LINE_COLOUR]), 1.2))
        hours = self.step / 60
        run = []

        def finish():
            if len(run) == 1:
                painter.drawPoint(run[0])
            elif run:
                painter.drawPolyline(QPolygonF(run))
            run.clear()

        for index, value in enumerate(self.values):
            if value is None:
                finish()
            elif self.stepped:
                run += [QPointF(x(index * hours), y(value)), QPointF(x((index + 1) * hours), y(value))]
            else:
                run.append(QPointF(x(index * hours), y(value)))
        finish()


class KpStrip(IndexStrip):
    """Eight bars of Kp, toned by the G scale, with Hp30's half hours over
    them: Kp says how large, Hp30 when."""

    def __init__(self, axis, parent=None):
        super().__init__(
            axis, "Kp", "",
            "Bars: Kp, every 3 hours, coloured from G1 up. Line: Hp30, the same scale every 30 minutes.",
            height=KP_HEIGHT, parent=parent,
        )
        self.kp = self.hp30 = None
        self.top = 9.0

    def has_values(self):
        return self.kp is not None

    def set_values(self, kp, hp30):
        self.kp, self.hp30, self.text = kp, hp30, ""
        self.top = max([9.0, *(value for value in hp30 or () if value is not None)])
        self.update()

    def clear(self, text="", trouble=False):
        self.kp = self.hp30 = None
        super().clear(text, trouble)

    def scale(self):
        return f"{self.top:.0f}", "0"

    def reading(self):
        known = [value for value in self.kp if value is not None]
        hours = self.axis.cursor
        if hours is None:
            return f"max {kp_text(max(known))} {g_level(max(known))}".rstrip()
        value = self.kp[int(hours // 3)]
        text = "no value" if value is None else f"{kp_text(value)} {g_level(value)}".rstrip()
        half = self.hp30[int(hours * 2)] if self.hp30 else None
        return text if half is None else f"{text}, Hp30 {half:.1f}"

    def paint_values(self, painter, body, x, palette):
        y = lambda value: body.bottom() - 1 - value / self.top * (body.height() - 2)
        painter.setFont(_font(FONT_META, mono=True))
        for index, value in enumerate(self.kp):
            if value is None:
                continue
            left, right = x(index * 3) + 1, x((index + 1) * 3) - 1
            tone = storm_tone(value)
            colour = QColor(palette[tone or "highlight_soft"])
            if tone:
                colour.setAlpha(170)
            bar = QRectF(left, y(value), right - left, body.bottom() - y(value))
            painter.fillRect(bar, colour)
            label = " ".join(part for part in (kp_text(value), g_level(value)) if part)
            painter.setPen(QColor(palette["text"]))
            # In the bar if it can hold it, as the line runs over the bars
            room = QRectF(left, y(value) if value >= 2 else y(value) - 14, right - left, 14)
            if painter.fontMetrics().horizontalAdvance(label) + SPACE_XS <= right - left:
                painter.drawText(room, Qt.AlignCenter, label)
        if self.hp30:
            painter.setPen(QPen(QColor(palette[LINE_COLOUR]), 1))
            run = []
            for index, value in enumerate(self.hp30):
                if value is None:
                    if run:
                        painter.drawPolyline(QPolygonF(run))
                    run = []
                else:
                    run += [QPointF(x(index / 2), y(value)), QPointF(x((index + 1) / 2), y(value))]
            if run:
                painter.drawPolyline(QPolygonF(run))


class IndicesPanel(QWidget):
    """The strips, one under the other."""

    def __init__(self, axis, parent=None):
        super().__init__(parent)
        self.kp = KpStrip(axis)
        self.dst = SeriesStrip(
            axis, "Dst", "nT", "Hourly Dst, each value over the hour it closes, from WDC Kyoto.",
            -50, 20, zero=True, stepped=True, extreme=min, levels=(-50, -100, -250),
        )
        self.speed = SeriesStrip(axis, "Wind speed", "km/s", "Solar wind speed.", 300, 600, levels=(500, 700))
        self.density = SeriesStrip(axis, "Density", "cm⁻³", "Solar wind proton density.", 0, 15, digits=1)
        self.bz = SeriesStrip(
            axis, "Bz (GSM)", "nT", "Interplanetary field, north positive. South is what drives storms.",
            -10, 10, digits=1, zero=True, extreme=min,
        )
        self.strips = (self.kp, self.dst, self.speed, self.density, self.bz)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        for strip in self.strips:
            layout.addWidget(strip)

    def clear(self, text="", trouble=False):
        for strip in self.strips:
            strip.clear(text, trouble)

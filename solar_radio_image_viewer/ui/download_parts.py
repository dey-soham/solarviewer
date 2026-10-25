"""Pieces both downloader windows use: the small widgets, the checking
spinner, the files timeline, the wrapping log and the tie that closes a
downloader with the viewer that opened it."""

import math
import os
import re

from PyQt5.QtCore import QEvent, QObject, QRectF, Qt, QVariantAnimation
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QTextBlockFormat, QTextCharFormat, QTextCursor
from PyQt5.QtWidgets import QCheckBox, QLabel, QPushButton, QSizePolicy, QWidget

from .styles import theme_manager
from .widgets import segmented

LOG_LINES_SHOWN = 500  # the full log is in the log file
SPINNER_TURN_MS = 1400  # one turn of the checking spinner's arc, as in the compare view


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




def _short_home(path):
    home = os.path.expanduser("~")
    if path == home or path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def _escape(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _muted(text, color):
    return f"<span style='color:{color}'>{_escape(text)}</span>"


class Spinner(QWidget):
    """A slim ring with an arc of the accent running round it, its length
    breathing as it goes (the compare view's busy look): shown while the
    archive is being asked. It turns only while visible."""

    def __init__(self, diameter, parent=None):
        super().__init__(parent)
        self.diameter = diameter
        self.setFixedSize(diameter + 4, diameter + 4)
        self._spin = 0.0
        self._turn = QVariantAnimation(self)
        self._turn.setDuration(SPINNER_TURN_MS)
        self._turn.setLoopCount(-1)
        self._turn.setStartValue(0.0)
        self._turn.setEndValue(1.0)
        self._turn.valueChanged.connect(self._set_spin)
        self.hide()

    def setVisible(self, visible):
        super().setVisible(visible)
        if visible and self._turn.state() != QVariantAnimation.Running:
            self._turn.start()
        elif not visible:
            self._turn.stop()

    def _set_spin(self, value):
        self._spin = value
        self.update()

    def paintEvent(self, event):
        palette = theme_manager.palette
        track = QColor(palette["text_secondary"])
        track.setAlphaF(0.25)
        rect = QRectF(2, 2, self.diameter, self.diameter)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width = max(2.0, self.diameter / 9.0)
        painter.setPen(QPen(track, width, Qt.SolidLine, Qt.RoundCap))
        painter.drawEllipse(rect)
        # Qt arcs: degrees from 3 o'clock, anticlockwise, in sixteenths
        head = -self._spin * 360
        span = 50 + 130 * (0.5 - 0.5 * math.cos(self._spin * 2 * math.pi * 2))
        painter.setPen(QPen(QColor(palette["highlight"]), width, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(rect, int(head * 16), int(-span * 16))
        painter.end()


class Timeline(QWidget):
    """One pill per expected file across the window, a solid bar when there
    are too many to tell apart; once the archive has answered, a tick
    where each file it holds falls (gaps show)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.count = 0
        self.positions = None
        self.setFixedHeight(12)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_count(self, count):
        self.count = count or 0
        self.positions = None
        self.update()

    def set_positions(self, positions):
        """Ticks at ``positions``, fractions (0 to 1) of the window."""
        self.positions = [min(max(p, 0.0), 1.0) for p in positions]
        self.count = len(self.positions)
        self.update()

    def paintEvent(self, event):
        if not self.count:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme_manager.palette["highlight_deep"]))
        width, y, h, gap = self.width(), 3, 6, 3
        if self.positions is not None:
            for p in self.positions:
                painter.drawRoundedRect(int(round(p * (width - 4))), y, 4, h, 2, 2)
        elif self.count * (gap + 4) > width:  # too many to tell apart: one bar
            painter.drawRoundedRect(0, y, width, h, 3, 3)
        else:
            pill = (width - gap * (self.count - 1)) / float(self.count)
            for i in range(self.count):
                x = int(round(i * (pill + gap)))
                painter.drawRoundedRect(x, y, int(round(pill)), h, 3, 3)
        painter.end()


def render_log(view, lines):
    """Show ``lines`` (time, message, status) in the QuietLog ``view``, newest first. A long line wraps under its message
    (a hanging indent past the time), and file names may break after
    _ . - and /, so nothing is cut off."""
    palette = theme_manager.palette
    muted = QTextCharFormat()
    muted.setForeground(QColor(palette["text_secondary"]))
    plain = QTextCharFormat()
    plain.setForeground(QColor(palette["text_strong"]))
    indent = view.fontMetrics().horizontalAdvance("00:00  ")
    block = QTextBlockFormat()
    block.setLeftMargin(indent)
    block.setTextIndent(-indent)
    block.setBottomMargin(2)
    view.clear()
    cursor = QTextCursor(view.document())
    for i, (stamp, message, status) in enumerate(lines):
        if i:
            cursor.insertBlock()
        cursor.setBlockFormat(block)
        cursor.insertText(stamp + "  ", muted)
        cursor.insertText(re.sub(r"([_./-])", "\\1\u200b", str(message)), plain)
        if status:
            cursor.insertText("  " + status, muted)
    view.moveCursor(QTextCursor.Start)


class CloseWithViewer(QObject):
    """Closes the downloader when the viewer that opened it closes (the
    downloader is a window of its own, so Qt does not do it)."""

    def __init__(self, window, viewer):
        super().__init__(window)
        self.window = window
        viewer.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Close and event.isAccepted():
            self.window.close()
        return False

"""Pieces both downloader windows use: the small widgets, the files timeline,
the wrapping log, the tie that closes a downloader with the viewer that opened
it, and the labelled wait for their first import. :func:`abandon` also serves
the solar events viewer."""

import importlib
import os
import re
import sys

from PyQt5.QtCore import QEvent, QObject, Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QTextBlockFormat, QTextCharFormat, QTextCursor
from PyQt5.QtWidgets import QCheckBox, QLabel, QPushButton, QSizePolicy, QWidget

from .activity import Spinner as Spinner  # for the downloaders
from .background import Cancelled, run_in_thread
from .styles import theme_manager
from .widgets import segmented

LOG_LINES_SHOWN = 500  # the full log is in the log file

_abandoned = set()  # QThreads left to end by themselves


def abandon(worker):
    """Stop listening to the QThread ``worker``, tell it to stop if it can be
    told, and leave it to end by itself. Its network call may take a minute,
    too long to hold the window up for; it is kept here till then, as
    destroying a running QThread takes the application down."""
    try:
        worker.disconnect()
    except TypeError:  # nothing was connected
        pass
    if hasattr(worker, "cancel"):
        worker.cancel()
    if worker.isRunning():
        _abandoned.add(worker)
        worker.finished.connect(lambda: _abandoned.discard(worker))
        if worker.isFinished():  # before that was connected
            _abandoned.discard(worker)


def _import_quietly(names):
    """Import ``names`` ahead of the module that needs them, which reports
    anything missing."""
    for name in names:
        try:
            importlib.import_module(name)
        except Exception:
            pass


def load_downloader(parent, label, libraries, module):
    """The window module ``module`` of a downloader, imported on first use;
    None if the user cancelled. The ``libraries`` it needs, free of Qt, are
    imported first in a thread, the window drawing and ``label`` showing: that
    is seconds of work. The module itself imports Qt, so it stays on this
    thread."""
    if module not in sys.modules:
        try:
            run_in_thread(parent, label, _import_quietly, libraries)
        except Cancelled:
            if hasattr(parent, "statusBar"):
                parent.statusBar().showMessage("Cancelled", 5000)
            return None
    return importlib.import_module(module)


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

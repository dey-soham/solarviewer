"""
What the canvas shows before an image is loaded: how to open one, with the
images opened lately to open with a click.

Two columns on a soft glow: at the left a kicker, a headline, a note and
the Open file… and Paste path buttons; at the right the recent files, each
with its date and size, and a link to the whole list.
"""

import os
import time

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QRadialGradient
from PyQt5.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .recent_files import recent_images
from .styles import theme_manager
from .widgets import kicker, link_button

HEADLINE = "Drop a FITS, CASA image or dynamic spectrum anywhere on this canvas."
NOTE = (
    "Regions, statistics and the cursor readout appear in the inspector "
    "once an image is loaded."
)
RECENT_SHOWN = 4


def _when(path):
    """When ``path`` was last changed: Today, Yesterday, or the date."""
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return ""
    today = time.localtime()
    then = time.localtime(stamp)
    days = (
        time.mktime(today[:3] + (0, 0, 0) + today[6:])
        - time.mktime(then[:3] + (0, 0, 0) + then[6:])
    ) / 86400
    if days < 1:
        return "Today"
    if days < 2:
        return "Yesterday"
    if then.tm_year == today.tm_year:
        return time.strftime("%b %d", then).replace(" 0", " ")
    return time.strftime("%b %d, %Y", then).replace(" 0", " ")


def _size(path):
    """``path``'s size (a CASA image's: its tables', one level down)."""
    try:
        if os.path.isdir(path):
            total = 0
            with os.scandir(path) as entries:
                for entry in entries:
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat().st_size
        else:
            total = os.path.getsize(path)
    except OSError:
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if total < 1024 or unit == "TB":
            return f"{total:.0f} {unit}" if unit == "B" else f"{total:.1f} {unit}"
        total /= 1024


def _kind(path):
    if os.path.isdir(path):
        return "CASA image"
    return "FITS"


class _RecentRow(QFrame):
    """One recent file: its name and date, then its kind and size."""

    clicked = pyqtSignal(str)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.setObjectName("RecentRow")
        self.path = path
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(path)
        grid = QGridLayout(self)
        grid.setContentsMargins(10, 7, 10, 7)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(2)
        name = QLabel(os.path.basename(path.rstrip("/\\")) or path)
        name.setObjectName("Mono")
        date = QLabel(_when(path))
        date.setObjectName("Meta")
        date.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        meta = QLabel(" · ".join(part for part in (_kind(path), _size(path)) if part))
        meta.setObjectName("Meta")
        grid.addWidget(name, 0, 0)
        grid.addWidget(date, 0, 1)
        grid.addWidget(meta, 1, 0, 1, 2)
        grid.setColumnStretch(0, 1)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit(self.path)
        super().mouseReleaseEvent(event)


class EmptyCanvas(QWidget):
    """The canvas's empty state. ``openRequested`` asks for the file
    dialog, ``pathRequested(path)`` to open that path, ``allRecentRequested``
    for the whole recent list.

    The dynamic spectrum viewer shows one too, with its own words
    (``kicker_text``, ``headline``, ``note``, ``open_tip``), its own
    recent list (``recent``: a function returning the paths) and further
    buttons after Paste path (``extra_buttons``: [(text, slot, tip)])."""

    openRequested = pyqtSignal()
    pathRequested = pyqtSignal(str)
    allRecentRequested = pyqtSignal()

    def __init__(
        self,
        parent=None,
        kicker_text="No image",
        headline=HEADLINE,
        note=NOTE,
        open_tip="Open a FITS file or CASA image (Ctrl+O)",
        recent=recent_images,
        extra_buttons=(),
    ):
        super().__init__(parent)
        self._recent = recent
        self.setAcceptDrops(False)  # drops fall through to the window
        outer = QHBoxLayout(self)
        outer.setContentsMargins(60, 40, 60, 40)
        outer.setSpacing(48)
        outer.addStretch(1)

        # The words and the buttons
        words = QWidget()
        words.setMaximumWidth(420)
        column = QVBoxLayout(words)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(12)
        column.addStretch()
        column.addWidget(kicker(kicker_text))
        headline = QLabel(headline)
        headline.setObjectName("Headline")
        headline.setWordWrap(True)
        column.addWidget(headline)
        note = QLabel(note)
        note.setObjectName("Hint")
        note.setWordWrap(True)
        column.addWidget(note)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.open_button = QPushButton("Open file…")
        self.open_button.setObjectName("PrimaryButton")
        self.open_button.setToolTip(open_tip)
        self.open_button.clicked.connect(self.openRequested)
        self.paste_button = QPushButton("Paste path")
        self.paste_button.setToolTip("Open the path on the clipboard")
        self.paste_button.clicked.connect(self._paste_path)
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.paste_button)
        for text, slot, tip in extra_buttons:
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch()
        column.addSpacing(2)
        column.addLayout(buttons)
        column.addStretch()
        outer.addWidget(words, 3)

        # The recent files
        self.recent = QWidget()
        self.recent.setMaximumWidth(440)
        self._recent_layout = QVBoxLayout(self.recent)
        self._recent_layout.setContentsMargins(0, 0, 0, 0)
        self._recent_layout.setSpacing(2)
        outer.addWidget(self.recent, 3)
        outer.addStretch(1)
        self.refresh()

    def refresh(self):
        """Show the recent files as they are now."""
        layout = self._recent_layout
        while layout.count():
            widget = layout.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)  # gone at once, not at the next event loop turn
                widget.deleteLater()
        # (the link row is a widget of its own: a nested layout's widgets
        # would be left behind, and the link shown twice)
        paths = self._recent()
        self.recent.setVisible(bool(paths))
        if not paths:
            return
        layout.addStretch()
        heading = kicker("Recent")
        heading.setContentsMargins(10, 0, 10, 6)
        layout.addWidget(heading)
        for path in paths[:RECENT_SHOWN]:
            row = _RecentRow(path)
            row.clicked.connect(self.pathRequested)
            layout.addWidget(row)
        more = link_button("All recent files →", self.allRecentRequested)
        more.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(6, 4, 0, 0)
        row_layout.addWidget(more)
        row_layout.addStretch()
        layout.addWidget(row)
        layout.addStretch()

    def _paste_path(self):
        from PyQt5.QtWidgets import QApplication

        text = (QApplication.clipboard().text() or "").strip().strip("'\"")
        if text and os.path.exists(os.path.expanduser(text)):
            self.pathRequested.emit(os.path.expanduser(text))
        else:
            self.openRequested.emit()

    def paintEvent(self, event):
        """The ground: a soft glow left of centre fading into the window."""
        palette = theme_manager.palette
        painter = QPainter(self)
        rect = self.rect()
        painter.fillRect(rect, QColor(palette["canvas"]))
        glow = QRadialGradient(
            rect.width() * 0.4, rect.height() * 0.5, max(rect.width(), rect.height()) * 0.6
        )
        glow.setColorAt(0.0, QColor(palette["canvas_glow"]))
        glow.setColorAt(1.0, QColor(palette["canvas"]))
        painter.fillRect(rect, glow)
        painter.end()
        super().paintEvent(event)

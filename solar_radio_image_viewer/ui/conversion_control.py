"""
A conversion of the image shown as its state: "Jy/beam | K", "RA/Dec |
Solar XY". The segment on is what the image is in now; clicking the other
asks for the conversion.

The loaders and the conversions drive it with the QPushButton calls they
made on the old TB and HPC buttons (``setText`` of the action offered,
"TB" / "FLUX" and "HPC" / "RA/DEC"; ``setChecked``; ``setEnabled``;
``setToolTip``), so they need not know it is a pair of segments.
"""

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QWidget

from .widgets import segmented


class ConversionControl(QWidget):
    """``segments`` are the two states' labels, in order; ``actions`` maps
    the action text the host sets (what a click would convert *to*) to
    the index of the state the image is in while that action is offered.
    ``clicked`` asks the host for the conversion."""

    clicked = pyqtSignal(bool)

    def __init__(self, segments, actions, tip, parent=None):
        super().__init__(parent)
        self._actions = actions
        self._text = ""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons = []
        for index, label in enumerate(segments):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setToolTip(tip)
            button.clicked.connect(lambda _=False, i=index: self._chosen(i))
            self._group.addButton(button)
            self._buttons.append(button)
        self._buttons[0].setChecked(True)
        layout.addWidget(segmented(self._buttons))

    def _current(self):
        return next((i for i, b in enumerate(self._buttons) if b.isChecked()), 0)

    def _show(self, index):
        self._group.setExclusive(False)
        for i, button in enumerate(self._buttons):
            button.setChecked(i == index)
        self._group.setExclusive(True)

    def _chosen(self, index):
        """A segment clicked: the one on stays until the host converts."""
        current = self._current()
        self._show(current)
        if index != current:
            self.clicked.emit(False)

    # ----------------------------------------- the QPushButton calls used
    def setText(self, text):
        self._text = text
        index = self._actions.get(text)
        if index is not None:
            self._show(index)

    def text(self):
        return self._text

    def setChecked(self, checked):
        """Checked meant "converted" on the old button: the second state."""
        self._show(1 if checked else 0)

    def isChecked(self):
        return self._current() == 1

    def setToolTip(self, tip):
        for button in self._buttons:
            button.setToolTip(tip)

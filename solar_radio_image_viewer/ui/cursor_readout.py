"""
The cursor readout pinned at the foot of the inspector: the pixel under
the mouse, its world coordinates and its value, each on its own line.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFrame, QGridLayout, QLabel

from .widgets import kicker_row

BLANK = "—"


class CursorReadout(QFrame):
    """``set_state("no image")`` clears the lines and names the state;
    ``show_cursor(pixel, world, value)`` fills them."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Pinned")
        grid = QGridLayout(self)
        grid.setContentsMargins(14, 10, 14, 10)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        header, self.state_label = kicker_row("Cursor", "no image")
        grid.addWidget(header, 0, 0, 1, 2)
        self._values = {}
        for row, name in enumerate(("Pixel", "World", "Value"), start=1):
            label = QLabel(name)
            label.setObjectName("Hint")
            value = QLabel(BLANK)
            value.setObjectName("Mono")
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            value.setMinimumWidth(value.fontMetrics().horizontalAdvance("0") * 14)
            grid.addWidget(label, row, 0)
            grid.addWidget(value, row, 1)
            self._values[name] = value
        grid.setColumnStretch(1, 1)

    def set_state(self, state):
        """No readout: say why ("no image", "off the image")."""
        self.state_label.setText(state)
        for value in self._values.values():
            value.setText(BLANK)

    def show_cursor(self, pixel, world, value):
        self.state_label.setText("")
        self._values["Pixel"].setText(pixel or BLANK)
        self._values["World"].setText(world or BLANK)
        self._values["Value"].setText(value or BLANK)

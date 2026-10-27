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
    ``show_cursor(pixel, world, value)`` fills them. ``rows`` names the
    lines (the dynamic spectrum viewer's: Time, Frequency, Intensity)."""

    def __init__(self, parent=None, rows=("Pixel", "World", "Value"), state="no image"):
        super().__init__(parent)
        self.setObjectName("Pinned")
        grid = QGridLayout(self)
        grid.setContentsMargins(14, 10, 14, 10)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)
        header, self.state_label = kicker_row("Cursor", state)
        grid.addWidget(header, 0, 0, 1, 2)
        self._values = {}
        self._labels = {}
        self._rows = tuple(rows)
        for row, name in enumerate(self._rows, start=1):
            label = QLabel(name)
            label.setObjectName("Hint")
            label.setMinimumWidth(label.fontMetrics().horizontalAdvance("Solar XY"))
            self._labels[name] = label
            value = QLabel(BLANK)
            value.setObjectName("Mono")
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            value.setMinimumWidth(value.fontMetrics().horizontalAdvance("0") * 14)
            grid.addWidget(label, row, 0)
            grid.addWidget(value, row, 1)
            self._values[name] = value
        grid.setColumnStretch(1, 1)

    def set_frame_name(self, name):
        """The second line is named after the image's frame ("RA/Dec",
        "Solar XY"), or "World" with no image."""
        self._labels["World"].setText(name or "World")

    def set_state(self, state):
        """No readout: say why ("no image", "off the image")."""
        self.state_label.setText(state)
        for value in self._values.values():
            value.setText(BLANK)

    def show_cursor(self, *values, state=""):
        """The lines' values, in the order of ``rows`` (rich text allowed)."""
        self.state_label.setText(state)
        for name, value in zip(self._rows, values):
            self._values[name].setText(value or BLANK)

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

    def __init__(self, segments, actions, tip, reason, parent=None):
        """``tip`` says what the control converts; ``reason`` is the
        tooltip while the conversion is unavailable."""
        super().__init__(parent)
        self._actions = actions
        self._text = ""
        self._index = None  # the state on (None: no image); the clicked
        # button is checked by the group before the control sees the click
        self._tip = tip
        self._reason = reason
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
        layout.addWidget(segmented(self._buttons, role="HeaderSegmented"))

    def _show(self, index):
        self._index = index
        self._group.setExclusive(False)
        for i, button in enumerate(self._buttons):
            button.setChecked(i == index)
        self._group.setExclusive(True)

    def clear(self):
        """No image: nothing on."""
        self._show(None)

    def current_label(self):
        """The state on ("Solar XY"), or "" with no image."""
        return self._buttons[self._index].text() if self._index is not None else ""

    def setEnabled(self, enabled):
        """Unavailable: dimmed (the stylesheet's disabled colours; an
        opacity effect of its own would undo the header's), and the
        tooltip says why."""
        super().setEnabled(enabled)
        for button in self._buttons:
            button.setToolTip(self._tip if enabled else self._reason)

    def _chosen(self, index):
        """A segment clicked (and so checked by the group): the one on
        stays until the host converts."""
        current = self._index
        self._show(current)
        if current is not None and index != current:
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
        """Checked meant "converted by the viewer" on the old button, not
        which state the image is in (an image loaded in K is not
        converted): kept for ``isChecked``, the segments follow setText."""
        self._checked = bool(checked)

    def isChecked(self):
        return getattr(self, "_checked", False)

    def setToolTip(self, tip):
        """The hosts' action tips ("Convert to ...") are not shown: the
        control's own tip says what it converts, the segments say which
        way."""
        self._action_tip = tip

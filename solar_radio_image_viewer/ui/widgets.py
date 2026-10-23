"""
Pieces every dialog is built from, so they all read the same way.

- ``button_bar``: the row of buttons at the bottom of a dialog, laid out
  the platform's way, with the one main action filled in the accent
- ``section``: a titled block inside a dialog or panel, lighter than a
  group box (no border), for grouping within an already framed area
- ``FoldSection``: a titled block that folds when its title is clicked,
  and remembers whether it was open
- ``ResponsiveRow``: widgets in as many columns as the width allows
"""

from PyQt5.QtCore import QSettings, Qt
from PyQt5.QtWidgets import (
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


def button_bar(primary=None, secondary=None, extras=(), parent=None):
    """A dialog's buttons: ``primary`` ("Apply", slot) is the main action,
    filled in the accent and the default; ``secondary`` ("Cancel", slot)
    closes or cancels; ``extras`` [("Reset", slot), ...] are other actions,
    kept to the left. The platform decides the order on the right.

    Returns (box, buttons) with ``buttons`` the created QPushButtons by
    their text, for enabling, hiding or renaming later.
    """
    box = QDialogButtonBox(parent)
    buttons = {}
    for text, slot in extras:
        button = box.addButton(text, QDialogButtonBox.ResetRole)
        button.setAutoDefault(False)  # the accent stays on the main action
        button.clicked.connect(slot)
        buttons[text] = button
    if secondary is not None:
        text, slot = secondary
        button = box.addButton(text, QDialogButtonBox.RejectRole)
        button.setAutoDefault(False)
        button.clicked.connect(slot)
        buttons[text] = button
    if primary is not None:
        text, slot = primary
        button = box.addButton(text, QDialogButtonBox.AcceptRole)
        button.setObjectName("PrimaryButton")
        button.setDefault(True)
        button.clicked.connect(slot)
        buttons[text] = button
    return box, buttons


def section(title, parent=None):
    """A titled block: a label in the SectionTitle role over a layout to
    fill. Returns (widget, layout)."""
    widget = QWidget(parent)
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    label = QLabel(title)
    label.setObjectName("SectionTitle")
    layout.addWidget(label)
    return widget, layout


class FoldSection(QWidget):
    """A titled block of a sidebar that folds when its title is clicked.
    ``key`` names the setting that remembers whether it is open (None: it
    is not remembered). Fill ``self.layout``."""

    SETTINGS = ("SolarViewer", "SolarViewer")

    def __init__(self, title, key=None, parent=None):
        super().__init__(parent)
        self._key = f"panels/{key}" if key else None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)
        self.header = QToolButton()
        self.header.setObjectName("FoldHeader")
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.setToolTip("Click to fold or unfold")
        outer.addWidget(self.header)
        self.body = QWidget()
        self.layout = QVBoxLayout(self.body)
        self.layout.setContentsMargins(0, 0, 0, 4)
        self.layout.setSpacing(8)
        outer.addWidget(self.body)
        self.header.toggled.connect(self._fold)
        opened = True
        if self._key:
            opened = QSettings(*self.SETTINGS).value(self._key, True, type=bool)
        self.header.setChecked(opened)
        self._fold(opened)

    def _fold(self, opened):
        self.body.setVisible(opened)
        self.header.setArrowType(Qt.DownArrow if opened else Qt.RightArrow)
        if self._key:
            QSettings(*self.SETTINGS).setValue(self._key, opened)


class ResponsiveRow(QWidget):
    """``widgets`` side by side in as many columns as fit ``cell_width``
    each, the rest wrapping to the next row: one row in a wide sidebar,
    two or more in a narrow one."""

    def __init__(self, widgets, cell_width, parent=None):
        super().__init__(parent)
        self._widgets = list(widgets)
        self._cell_width = cell_width
        self._columns = 0
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(12)
        self._grid.setVerticalSpacing(6)
        self._place(len(self._widgets))

    def _place(self, columns):
        columns = max(1, min(columns, len(self._widgets)))
        if columns == self._columns:
            return
        self._columns = columns
        for widget in self._widgets:
            self._grid.removeWidget(widget)
        for index, widget in enumerate(self._widgets):
            self._grid.addWidget(widget, index // columns, index % columns, Qt.AlignTop)
        for column in range(len(self._widgets)):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place(max(1, self.width() // self._cell_width))

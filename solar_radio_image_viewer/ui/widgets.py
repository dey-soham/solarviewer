"""
Pieces every window is built from, so they all read the same way.

- ``button_bar``: the row of buttons at the bottom of a dialog, laid out
  the platform's way, with the one main action outlined in the accent
- ``section``: a titled block inside a dialog or panel, lighter than a
  group box (no border), for grouping within an already framed area
- ``kicker``: a block's small-capitals heading in the accent
- ``segmented``: a row of choices in one outlined group, one chosen
- ``link_button``: a text button in the accent with no border
- ``toggle_row``: a switch with its name and settings button on a surface
- ``vrule``: a short vertical line between groups in a bar
"""

from PyQt5.QtCore import QEvent, QObject, Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


def button_bar(primary=None, secondary=None, extras=(), parent=None):
    """A dialog's buttons: ``primary`` ("Apply", slot) is the main action,
    outlined in the accent and the default; ``secondary`` ("Cancel", slot)
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


def kicker(text, parent=None):
    """A block's heading: ``text`` in small spaced capitals, in the accent
    (the Kicker role; the stylesheet cannot space or capitalise letters,
    so that is done here)."""
    label = QLabel(text.upper(), parent)
    label.setObjectName("Kicker")
    font = QFont(label.font())
    font.setLetterSpacing(QFont.PercentageSpacing, 110)
    label.setFont(font)
    return label


def kicker_row(text, aside=None, parent=None):
    """A kicker with, at the right end of its line, ``aside``: a label in
    the Meta role, for a block's current state ("1–99%", "no image").
    Returns (row widget, aside label)."""
    row = QWidget(parent)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    layout.addWidget(kicker(text))
    layout.addStretch()
    label = QLabel(aside or "")
    label.setObjectName("Meta")
    layout.addWidget(label)
    return row, label


def segmented(buttons, parent=None, stretch=False, role="Segmented"):
    """``buttons`` (QPushButtons or QToolButtons, checkable ones for a
    choice) in one group, the Segmented role (outlined) or the
    HeaderSegmented one (a filled track): the one checked has an accent
    outline and the brightest text. ``stretch`` gives each the same share
    of the width (the presets row). Returns the group widget."""
    group = QFrame(parent)
    group.setObjectName(role)
    layout = QHBoxLayout(group)
    layout.setContentsMargins(2, 2, 2, 2)
    layout.setSpacing(1)
    for button in buttons:
        button.setCursor(Qt.PointingHandCursor)
        if stretch:
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(button, 1 if stretch else 0)
    return group


def link_button(text, slot=None, parent=None):
    """A text button in the accent with no border, for a small action
    beside a line ("RMS box…", "All recent files →")."""
    button = QPushButton(text, parent)
    button.setObjectName("LinkButton")
    button.setCursor(Qt.PointingHandCursor)
    if slot is not None:
        button.clicked.connect(slot)
    return button


def toggle_row(switch, settings_button=None, label=None, parent=None):
    """A switch (a QCheckBox in the Switch role) with its name and, at the
    right, its settings button, on the surface colour. ``label`` is a
    separate QLabel when the switch has no text of its own. The row,
    label and gear follow the switch: on means a brighter label, a
    hairline round the row and a brighter gear (never hue alone)."""
    from PyQt5.QtGui import QIcon

    from .icons import themed_icon

    row = QFrame(parent)
    row.setObjectName("Surface")
    layout = QHBoxLayout(row)
    layout.setContentsMargins(9, 5, 7, 5)
    layout.setSpacing(6)
    layout.addWidget(switch)
    if label is not None:
        label.setObjectName("SwitchLabel")
        layout.addWidget(label)
    layout.addStretch()
    if settings_button is not None:
        layout.addWidget(settings_button)

    def follow(on):
        for widget in (row, label):
            if widget is not None:
                widget.setProperty("on", "true" if on else "false")
                widget.style().unpolish(widget)
                widget.style().polish(widget)
        if settings_button is not None:
            settings_button.setIcon(QIcon(themed_icon("gear_on.svg" if on else "gear_off.svg")))

    switch.toggled.connect(follow)
    row.refresh = lambda: follow(switch.isChecked())  # (after a theme change)
    follow(switch.isChecked())
    return row


def rule(parent=None):
    """A 1px horizontal line, fading at both ends (the Rule role)."""
    line = QFrame(parent)
    line.setObjectName("Rule")
    line.setFrameShape(QFrame.NoFrame)
    return line


def vrule(height=20, parent=None):
    """A 1px vertical line ``height`` tall, between groups in a bar."""
    line = QFrame(parent)
    line.setObjectName("VRule")
    line.setFrameShape(QFrame.NoFrame)
    line.setFixedHeight(height)
    return line


class _UnitPlacer(QObject):
    """Keeps an inline unit at the right end of its entry as it resizes."""

    def __init__(self, entry, label):
        super().__init__(entry)
        self._label = label
        entry.installEventFilter(self)
        self.place(entry)

    def place(self, entry):
        hint = self._label.sizeHint()
        self._label.setGeometry(
            entry.width() - hint.width() - 7, (entry.height() - hint.height()) // 2,
            hint.width(), hint.height(),
        )

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Resize:
            self.place(watched)
        return False


def inline_unit(entry, unit):
    """Show ``unit`` (say "σ") in muted text inside the right end of the
    line edit ``entry``; what is typed stays clear of it. Returns the label."""
    label = QLabel(unit, entry)
    label.setObjectName("Hint")
    label.setAttribute(Qt.WA_TransparentForMouseEvents)  # clicks reach the entry
    margins = entry.textMargins()
    entry.setTextMargins(
        margins.left(), margins.top(), label.sizeHint().width() + 3, margins.bottom()
    )
    _UnitPlacer(entry, label)
    return label

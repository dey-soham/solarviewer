"""
Pieces every dialog is built from, so they all read the same way.

- ``button_bar``: the row of buttons at the bottom of a dialog, laid out
  the platform's way, with the one main action filled in the accent
- ``section``: a titled block inside a dialog or panel, lighter than a
  group box (no border), for grouping within an already framed area
"""

from PyQt5.QtWidgets import QDialogButtonBox, QLabel, QVBoxLayout, QWidget


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

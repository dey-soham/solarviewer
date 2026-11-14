"""
Pieces every window is built from, so they all read the same way.

- ``dialog_layout``: a dialog's outer layout, with the usual margins
- ``button_bar``: the row of buttons at the bottom of a dialog, with the one
  main action last and outlined in the accent
- ``section``: a titled block inside a dialog or panel, lighter than a
  group box (no border), for grouping within an already framed area
- ``kicker``: a block's small-capitals heading in the accent
- ``segmented``: a row of choices in one outlined group, one chosen
- ``link_button``: a text button in the accent with no border
- ``toggle_row``: a switch with its name and settings button on a surface
- ``vrule``: a short vertical line between groups in a bar
- ``empty_note``: what an area without content says, in the middle of it
- ``ElidedLabel``: a label that shortens its text with "…" to fit, never cut
- ``PathEdit``: a line edit for a path, which shows a long one whole at both
  ends ("/home/…/image.fits") while it is not being typed in
- ``StatusDotButton``: a status bar button led by a green or red dot
"""

from PyQt5.QtCore import QEvent, QObject, QPointF, QSize, Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QPalette
from PyQt5.QtWidgets import (
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleOptionFrame,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from .styles import SPACE_L, SPACE_M, SPACE_S, STATUS_DOT, theme_manager


def dialog_layout(dialog):
    """``dialog``'s vertical layout: 16px margins, 12px between blocks."""
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(SPACE_L, SPACE_L, SPACE_L, SPACE_L)
    layout.setSpacing(SPACE_M)
    return layout


def button_bar(primary=None, secondary=None, extras=(), parent=None):
    """A dialog's buttons: ``primary`` ("Apply", slot) is the main action,
    outlined in the accent and the default; ``secondary`` ("Cancel", slot)
    closes or cancels; ``extras`` [("Reset", slot), ...] are other actions,
    kept to the left, with ``secondary`` then ``primary`` at the right (the
    stylesheet's button-layout, the same on every platform).

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


def empty_note(text, parent=None):
    """``text`` in muted type, centred: what an empty list or plot area says
    ("No data loaded", "Choose a table to see its solutions")."""
    label = QLabel(text, parent)
    label.setObjectName("Hint")
    label.setAlignment(Qt.AlignCenter)
    label.setWordWrap(True)
    return label


def elided_path(metrics, path, width):
    """``path`` as it fits in ``width``: the name at its end whole, and of
    the folders before it as many of the last as fit, whole, with the first
    where it fits too ("/home/…/day/image.fits", "…/day/image.fits"); the
    end of the name alone when even the name is too long."""
    if metrics.horizontalAdvance(path) <= width:
        return path
    folders, slash, name = path.rstrip("/").rpartition("/")
    room = width - metrics.horizontalAdvance(slash + name)
    if not slash or room < metrics.horizontalAdvance("…"):
        return metrics.elidedText(name or path, Qt.ElideLeft, width)
    parts = folders.split("/")
    lead = parts[: 2 if parts[0] == "" else 1]  # ("/home", "~", "data")
    for kept in range(len(parts) - 1, -1, -1):  # (the folders nearest the name tell the most)
        for start in (lead, []):
            if len(start) + kept >= len(parts) or not (start or kept):
                continue  # (the "…" stands for a folder at least)
            shown = "/".join(start + ["…"] + parts[len(parts) - kept:])
            if metrics.horizontalAdvance(shown) <= room:
                return shown + slash + name
    return "…" + slash + name


class ElidedLabel(QLabel):
    """A label of one line that gives way to its neighbours: text too long
    for it is shortened with "…" (in the ``mode``'s place: the middle keeps
    both ends of a name; "path" keeps a path's file name whole, as
    ``elided_path`` does) and is whole in its tooltip. Set the text with
    ``setFullText``. ``least`` is the characters it always has room for."""

    def __init__(self, text="", role=None, mode=Qt.ElideMiddle, least=4, parent=None):
        super().__init__(parent)
        if role:
            self.setObjectName(role)
        self._full = ""
        self._mode = mode
        self._least = least
        self._own_tip = True  # (until a tooltip of its own is set)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setFullText(text)

    def fullText(self):
        return self._full

    def setFullText(self, text):
        self._full = str(text)
        self.updateGeometry()
        self._elide()

    def setToolTip(self, text):
        """A tooltip of its own, in place of the whole text; an empty one
        gives the whole text's back."""
        self._own_tip = not text
        if text:
            super().setToolTip(text)
        else:
            self._elide()

    def sizeHint(self):
        metrics = self.fontMetrics()
        return QSize(metrics.horizontalAdvance(self._full) + 4, metrics.height())

    def minimumSizeHint(self):
        metrics = self.fontMetrics()
        return QSize(metrics.horizontalAdvance("M") * self._least, metrics.height())

    def resizeEvent(self, event):
        self._elide()
        super().resizeEvent(event)

    def _elide(self):
        room = max(0, self.width() - 2)
        if self._mode == "path":
            shown = elided_path(self.fontMetrics(), self._full, room)
        else:
            shown = self.fontMetrics().elidedText(self._full, self._mode, room)
        self.setText(shown)
        if self._own_tip:  # (the whole text, only while some of it is hidden)
            super().setToolTip(self._full if shown != self._full else "")


class PathEdit(QLineEdit):
    """A line edit for the path of a file or folder. A path too long for it
    is shown shortened while it is not being typed in, as ``elided_path``
    does: the name at its end whole, "…" among the folders before it (a
    plain line edit shows the end, cut in mid-letter); its tooltip is then
    the whole path."""

    def _shortened(self):
        """(the text as it fits, where it goes), or None when the whole
        text fits or is being typed."""
        text = self.text()
        if self.hasFocus() or not text:
            return None
        option = QStyleOptionFrame()
        self.initStyleOption(option)
        rect = self.style().subElementRect(QStyle.SE_LineEditContents, option, self)
        margins = self.textMargins()
        left, right = margins.left(), margins.right()
        # The icons of its actions, as a line edit makes room for them:
        # each its width and 4 px
        for icon in self.findChildren(QToolButton):
            if icon.isHidden():
                continue
            if icon.geometry().center().x() < self.width() / 2:
                left += icon.width() + 4
            else:
                right += icon.width() + 4
        # (2 px: the gap a line edit keeps beside its text)
        rect.adjust(left + 2, margins.top(), -right - 2, -margins.bottom())
        if self.fontMetrics().horizontalAdvance(text) <= rect.width():
            return None
        return elided_path(self.fontMetrics(), text, rect.width()), rect, option

    def paintEvent(self, event):
        shortened = self._shortened()
        if shortened is None:
            super().paintEvent(event)
            return
        text, rect, option = shortened
        painter = QPainter(self)
        self.style().drawPrimitive(QStyle.PE_PanelLineEdit, option, painter, self)
        group = QPalette.Active if self.isEnabled() else QPalette.Disabled
        painter.setPen(self.palette().color(group, QPalette.Text))
        painter.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft, text)
        painter.end()

    def event(self, event):
        if event.type() == QEvent.ToolTip and self._shortened() is not None:
            tip = self.toolTip()
            QToolTip.showText(event.globalPos(), f"{self.text()}\n{tip}" if tip else self.text(), self)
            return True
        return super().event(event)


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


class StatusDotButton(QPushButton):
    """A StatusButton led by a round dot, as modern status bars show a
    connection: green while ``on`` (``set_on``), quiet while off, the
    state it rests in. Its text still names the state, so the dot's
    colour is never the only sign of it."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setObjectName("StatusButton")
        self.setProperty("dot", "true")  # (the stylesheet leaves room for it)
        self._on = False

    def set_on(self, on):
        self._on = bool(on)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme_manager.palette["status_on" if self._on else "text_faint"]))
        radius = STATUS_DOT / 2
        # Where the text would begin without it (inside the 1px border),
        # level with the middle of the capitals
        centre = QPointF(1 + SPACE_S + radius, self.height() / 2)
        painter.drawEllipse(centre, radius, radius)
        painter.end()

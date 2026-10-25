"""
A document tab's page: up to four panes, each a :class:`SolarRadioImageTab`
showing one image, in a grid under one top bar and beside one inspector.

The chrome around the images is the active pane's. Each pane builds its
own top bar, inspector header, Display and Statistics pages, cursor
readout and canvas bar; the page keeps each kind in a stack and shows the
active pane's, so clicking another pane swaps every control to it while
the inspector keeps the tab (Display or Statistics) it was on.

The layout group the page puts in the canvas bar chooses one, two or
four slots; a slot without an image offers to add one (or takes a
dropped file). With more than one slot each pane gets a header: its
letter on its colour, the file's name, what the image is, a close
button. The page is what the window's tabs hold; ``tab_widget.
currentWidget()`` is the current page's active pane.
"""

import logging
import math
import os

from PyQt5.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QPoint,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    pyqtSignal,
)
from PyQt5.QtGui import QColor, QIcon, QKeySequence, QPainter, QPalette, QPen
from PyQt5.QtWidgets import (
    QAction,
    QActionGroup,
    QCheckBox,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QShortcut,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..ui.icons import launcher_icon, themed_icon
from ..ui.styles import _qt_deleted, set_hand_cursor, set_tone, theme_manager
from ..ui.widgets import kicker_row, link_button, segmented, toggle_row
from .compare_sync import (
    FRAME_NAMES,
    SYNC_WAYS,
    CursorValues,
    PaneSync,
    SyncPopover,
    apart,
    format_angle,
    frame_kind,
    sync_button,
)

logger = logging.getLogger(__name__)

MAX_PANES = 6
LETTERS = "ABCDEF"
# The layouts: slots, icon, what the button says
LAYOUTS = (
    (1, "layout_single.svg", "One image"),
    (2, "layout_two.svg", "Two images side by side"),
    (4, "layout_grid.svg", "Up to four images in a grid"),
    (6, "layout_six.svg", "Up to six images in two rows of three"),
)
GRID_GAP = 6
ICON_SIZE = QSize(16, 16)
# A change of layout happens under a picture of the image area as it
# was: a wash of the background comes over it, with a spinner, while the
# panes draw themselves into their new places (held at most
# VEIL_HOLD_MS), then the new layout fades in
VEIL_DIM_MS = 200
VEIL_DIM_LEVEL = 0.7  # how much of the picture the wash takes
VEIL_REVEAL_MS = 260
VEIL_HOLD_MS = 900
SPINNER_SIZE = 36  # the spinner's diameter, in points
SPINNER_TURN_MS = 1400  # one turn of its arc


def _positions(size):
    """(row, column) of each slot of a layout of ``size``."""
    if size == 1:
        return [(0, 0)]
    if size == 2:
        return [(0, 0), (0, 1)]
    if size == 4:
        return [(0, 0), (0, 1), (1, 0), (1, 1)]
    return [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2)]


class _Veil(QWidget):
    """A picture of the image area as it was, over it while the panes
    settle into a new layout: a wash of the page's background comes over
    it as they rebuild, a spinner turning at its centre, and the new
    layout fades in once they have drawn."""

    def __init__(self, page, host):
        super().__init__(page)
        self._picture = host.grab()
        self._colour = page.palette().color(QPalette.Window)
        colours = theme_manager.palette
        self._accent = QColor(colours["highlight"])
        self._track = QColor(colours["text_secondary"])
        self._track.setAlphaF(0.25)
        self.setGeometry(QRect(host.mapTo(page, QPoint(0, 0)), host.size()))
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._dim = 0.0  # how far the wash has come over the picture
        self._reveal = 0.0  # how far the new layout shows through
        self._spin = 0.0  # where the spinner's arc is in its turn
        self._waiting = set()
        self._drawn = False
        self._revealing = None
        self._then = None  # what to do once dimmed
        self.raise_()
        self.show()
        page.installEventFilter(self)
        self._dimming = _tween(self, VEIL_DIM_MS, QEasingCurve.OutCubic, self._set_dim)
        self._dimming.finished.connect(self._dimmed)
        self._spinning = _tween(self, SPINNER_TURN_MS, QEasingCurve.Linear, self._set_spin, loops=-1)

    def dimmed(self):
        return self._dimming.state() != QAbstractAnimation.Running

    def when_dimmed(self, callback):
        """Call ``callback`` once the picture has gone to the background
        (the change can then happen with nothing moving in view)."""
        self._then = callback
        if self.dimmed():
            self._dimmed()

    def _dimmed(self):
        then, self._then = self._then, None
        if then is not None:
            then()
        self._maybe_reveal()

    def wait_for(self, panes):
        """Hold until each of ``panes`` has drawn (or the hold runs out)."""
        self._waiting = set(panes)
        if not self._waiting:
            self.fade()
        else:
            QTimer.singleShot(VEIL_HOLD_MS, self.fade)

    def drawn(self, pane):
        # A pane with a plot scheduled (drawn compact, say) is not there
        # yet: this draw is of the old picture at the new size
        timer = getattr(pane, "_plot_timer", None)
        if timer is not None and timer.isActive():
            return
        self._waiting.discard(pane)
        if not self._waiting:
            self.fade()

    def fading(self):
        return self._drawn

    def fade(self):
        """The panes are in place: show them once the dip is complete."""
        if _qt_deleted(self):
            return
        self._drawn = True
        self._maybe_reveal()

    def _maybe_reveal(self):
        if not self._drawn or self._revealing is not None or _qt_deleted(self):
            return
        if not self.dimmed():
            return  # finished() brings us back
        self._revealing = _tween(self, VEIL_REVEAL_MS, QEasingCurve.InOutSine, self._set_reveal)
        self._revealing.finished.connect(self._done)

    def _set_dim(self, value):
        self._dim = value
        self.update()

    def _set_reveal(self, value):
        self._reveal = value
        self.update()

    def _set_spin(self, value):
        self._spin = value
        self.update()

    def paintEvent(self, _event):
        # The picture under a wash of the background that thickens as the
        # veil dims, leaving the picture showing through; the whole thins
        # as the new layout is revealed
        showing = 1.0 - self._reveal
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setOpacity(showing * showing)  # the picture goes first
        painter.drawPixmap(0, 0, self._picture)
        painter.setOpacity(showing)
        wash = QColor(self._colour)
        wash.setAlphaF(self._dim * VEIL_DIM_LEVEL)
        painter.fillRect(self.rect(), wash)
        self._paint_spinner(painter, showing * self._dim)
        painter.end()

    def _paint_spinner(self, painter, opacity):
        """A slim ring at the centre, an arc of the accent running round
        it, its length breathing as it goes."""
        if opacity <= 0:
            return
        size = SPINNER_SIZE
        rect = QRectF(
            (self.width() - size) / 2, (self.height() - size) / 2, size, size
        )
        painter.setOpacity(opacity)
        # A soft disc of the background behind it, so the ring reads over
        # a bright image too
        backing = QColor(self._colour)
        backing.setAlphaF(0.55)
        painter.setPen(Qt.NoPen)
        painter.setBrush(backing)
        painter.drawEllipse(rect.adjusted(-12, -12, 12, 12))
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(self._track, 3, Qt.SolidLine, Qt.RoundCap))
        painter.drawEllipse(rect)
        # Qt arcs: degrees from 3 o'clock, anticlockwise, in sixteenths
        head = -self._spin * 360
        span = 50 + 130 * (0.5 - 0.5 * math.cos(self._spin * 2 * math.pi * 2))
        painter.setPen(QPen(self._accent, 3, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(rect, int(head * 16), int(-span * 16))

    def _done(self):
        if _qt_deleted(self):
            return
        self.hide()
        self.setParent(None)
        self.deleteLater()
        then, self._then = self._then, None
        if then is not None:
            then()  # gone early: the change still happens

    def eventFilter(self, watched, event):
        # The page resized under the picture: it no longer fits, so go
        if event.type() == QEvent.Resize:
            self._done()
        return False


def _tween(owner, duration, easing, on_value, loops=1):
    """A 0 to 1 animation of ``duration`` ms, ``on_value`` called with
    each value, started at once; ``loops`` of it (-1: until stopped)."""
    tween = QVariantAnimation(owner)
    tween.setDuration(duration)
    tween.setLoopCount(loops)
    tween.setStartValue(0.0)
    tween.setEndValue(1.0)
    tween.setEasingCurve(easing)
    tween.valueChanged.connect(on_value)
    tween.start()
    return tween


def _tool_button(action):
    button = QToolButton()
    button.setDefaultAction(action)
    button.setIconSize(ICON_SIZE)
    button.setAutoRaise(True)
    return button


def overlay_box(pane, name):
    """``pane``'s own switch for the overlay ``name`` ("solar_disk", "grid")."""
    return getattr(pane, f"show_{name}_checkbox")


def pane_name(pane):
    """The file name of the image ``pane`` shows, or ""."""
    path = getattr(pane, "imagename", None) or ""
    return os.path.basename(path.rstrip("/"))


def pane_caption(pane):
    """What the header says beside the file name: the plot title's parts
    other than the time (the instrument, the frequency), then the time
    of day. "" while there is no image."""
    title = getattr(pane, "plot_title", "") or ""
    time = str(getattr(pane, "observation_time", "") or "")
    name = pane_name(pane)
    words = []
    for part in (part.strip() for part in title.split("|")):
        if part.startswith("Time:"):
            continue
        if part.startswith("Freq:"):
            part = part[5:].strip()
        if time and part.startswith(time):
            part = part[len(time) :].strip()
        part = part.replace("$\\AA$", "Å").strip()
        if part and part != name and part != "No Image":
            words.append(part)
    clock = ""
    if "T" in time:
        clock = time.split("T", 1)[1][:8]
    elif " " in time:
        clock = time.split(" ", 1)[1][:8]
    if clock:
        words.append(clock)
    return " · ".join(words)


class _ElidedLabel(QLabel):
    """A label that gives way to its neighbours, eliding its text in the
    middle; the whole text is its tooltip."""

    def __init__(self, role, least=4, parent=None):
        super().__init__(parent)
        self.setObjectName(role)
        self._full = ""
        self._least = least  # the characters it keeps room for
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)

    def setFullText(self, text):
        self._full = text
        self.setToolTip(text)
        self.updateGeometry()
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
        self.setText(
            self.fontMetrics().elidedText(self._full, Qt.ElideMiddle, max(0, self.width() - 2))
        )


def _repolish(widget):
    widget.style().unpolish(widget)
    widget.style().polish(widget)


REGION_ROWS = ("Min", "Max", "Mean", "Std Dev", "Sum", "RMS")
IMAGE_ROWS = ("Max", "Min", "RMS", "Box mean", "Pos. DR", "Neg. DR")


def _stats_table(rows, letters):
    """A table of ``rows`` with a column per pane, headed by the panes'
    letters, in the KeyValue role; as tall as its rows."""
    table = QTableWidget(len(rows), 1 + len(letters))
    table.setObjectName("KeyValue")
    table.verticalHeader().setVisible(False)
    table.setHorizontalHeaderLabels([""] + list(letters))
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setSelectionMode(QTableWidget.NoSelection)
    table.setFocusPolicy(Qt.NoFocus)
    table.setShowGrid(False)
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, header.ResizeToContents)
    for column in range(1, 1 + len(letters)):
        header.setSectionResizeMode(column, header.Stretch)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    row_height = table.fontMetrics().height() + 8
    for row, label in enumerate(rows):
        table.setItem(row, 0, QTableWidgetItem(label))
        for column in range(1, 1 + len(letters)):
            item = QTableWidgetItem("—")
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(row, column, item)
        table.setRowHeight(row, row_height)
    table.setFixedHeight(row_height * len(rows) + header.sizeHint().height() + 2 * table.frameWidth())
    table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return table


def _scroll(widget):
    area = QScrollArea()
    area.setWidget(widget)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    return area


class ComparePanel(QWidget):
    """The inspector's Compare tab: the panes of the page, and what is
    done to all of them at once."""

    def __init__(self, page, parent=None):
        super().__init__(parent)
        self._page = page
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 16, 14, 16)
        layout.setSpacing(20)

        # The panes
        block = QWidget()
        block_layout = QVBoxLayout(block)
        block_layout.setContentsMargins(0, 0, 0, 0)
        block_layout.setSpacing(10)
        row, self.panes_meta = kicker_row("Panes", "")
        block_layout.addWidget(row)
        self._rows = QVBoxLayout()
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(4)
        block_layout.addLayout(self._rows)
        layout.addWidget(block)

        # The display of every pane
        block = QWidget()
        block_layout = QVBoxLayout(block)
        block_layout.setContentsMargins(0, 0, 0, 0)
        block_layout.setSpacing(10)
        row, _aside = kicker_row("Display", "")
        block_layout.addWidget(row)
        self.full_axes_switch = QCheckBox()
        self.full_axes_switch.setObjectName("Switch")
        self.full_axes_switch.setToolTip(
            "Show every pane's plot with its ticks and labels; off, each pane is "
            "the image alone"
        )
        self.full_axes_switch.toggled.connect(page.set_full_axes)
        self.colorbar_switch = QCheckBox()
        self.colorbar_switch.setObjectName("Switch")
        self.colorbar_switch.setToolTip(
            "Show a colorbar under every pane's image, marked with the ends of its range"
        )
        self.colorbar_switch.toggled.connect(page.set_colorbar)
        # Every pane's own overlay switches at once; on while all panes have it
        self.overlay_switches = {}
        self._following = False
        for name, tip in (
            ("solar_disk", "Draw the solar disk on every pane"),
            ("grid", "Draw the coordinate grid on every pane"),
        ):
            switch = QCheckBox()
            switch.setObjectName("Switch")
            switch.setToolTip(tip)
            switch.toggled.connect(
                lambda on, name=name: self._following or page.set_overlay(name, on)
            )
            self.overlay_switches[name] = switch
        switches = QGridLayout()
        switches.setContentsMargins(0, 0, 0, 0)
        switches.setHorizontalSpacing(6)
        switches.setVerticalSpacing(6)
        for index, (switch, label) in enumerate((
            (self.full_axes_switch, "Axes"),
            (self.colorbar_switch, "Colorbar"),
            (self.overlay_switches["solar_disk"], "Solar disk"),
            (self.overlay_switches["grid"], "Grid"),
        )):
            switches.addWidget(toggle_row(switch, label=QLabel(label)), index // 2, index % 2)
        block_layout.addLayout(switches)
        apply_row = QHBoxLayout()
        apply_row.setContentsMargins(0, 0, 0, 0)
        apply_row.setSpacing(8)
        self.apply_hint = QLabel("Colormap, stretch, gamma, range, overlays")
        self.apply_hint.setObjectName("Hint")
        self.apply_hint.setWordWrap(True)  # (gives way, rather than widening the tab)
        apply_row.addWidget(self.apply_hint)
        apply_row.addStretch()
        self.apply_button = link_button("Apply to all panes", page.apply_display_to_all)
        self.apply_button.setToolTip(
            "Give every pane the active pane's colormap, stretch, gamma and "
            "overlays, and its range where the units match"
        )
        apply_row.addWidget(self.apply_button)
        block_layout.addLayout(apply_row)
        layout.addWidget(block)

        # The statistics of every pane: the region's, the image's
        self._region_block, self._region_layout, _aside = self._stats_block(
            "Region", "Draw a rectangle or ellipse on a pane: the panes following it take it too"
        )
        layout.addWidget(self._region_block)
        self._image_block, self._image_layout, _aside = self._stats_block(
            "Image", "Loads with the images"
        )
        layout.addWidget(self._image_block)
        self._region_table = self._image_table = None
        layout.addStretch()

    @staticmethod
    def _stats_block(title, hint):
        block = QWidget()
        block_layout = QVBoxLayout(block)
        block_layout.setContentsMargins(0, 0, 0, 0)
        block_layout.setSpacing(10)
        row, aside = kicker_row(title, "")
        block_layout.addWidget(row)
        label = QLabel(hint)
        label.setObjectName("Hint")
        label.setWordWrap(True)
        block_layout.addWidget(label)
        return block, block_layout, aside

    def refresh_stats(self):
        """The tables again, a column per pane, from each pane's own tables."""
        page = self._page
        letters = [page.letter(pane) for pane in page.panes]
        for old in (self._region_table, self._image_table):
            if old is not None:
                old.setParent(None)
                old.deleteLater()
        self._region_table = _stats_table(REGION_ROWS, letters)
        self._image_table = _stats_table(IMAGE_ROWS, letters)
        self._region_layout.addWidget(self._region_table)
        self._image_layout.addWidget(self._image_table)
        any_region = any_image = False
        for column, pane in enumerate(page.panes, start=1):
            if not pane.stats_table.isHidden():
                any_region = True
                for row in range(len(REGION_ROWS)):
                    item = pane.stats_table.item(row, 1)
                    self._region_table.item(row, column).setText(item.text() if item else "—")
            if not pane.image_stats_table.isHidden():
                any_image = True
                for row in range(len(IMAGE_ROWS)):
                    item = pane.image_stats_table.item(row, 1)
                    self._image_table.item(row, column).setText(item.text() if item else "—")
        self._region_table.setVisible(any_region)
        self._image_table.setVisible(any_image)

    def refresh(self):
        """The rows follow the page's panes: letter, name, what it shows."""
        while self._rows.count():
            widget = self._rows.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)  # gone at once, not at the next event loop turn
                widget.deleteLater()
        page = self._page
        self.panes_meta.setText(f"{len(page.panes)} of {page.layout_size}")
        for pane in page.panes:
            letter = page.letter(pane)
            row = QFrame()
            row.setObjectName("Surface")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(9, 5, 9, 5)
            row_layout.setSpacing(8)
            badge = QLabel(letter)
            badge.setObjectName("PaneBadge")
            badge.setProperty("letter", letter)
            row_layout.addWidget(badge)
            name = _ElidedLabel("PaneName")
            name.setFullText(pane_name(pane) or "No image")
            row_layout.addWidget(name)
            caption = _ElidedLabel("Meta", least=8)
            caption.setFullText(pane_caption(pane))
            row_layout.addWidget(caption)
            row_layout.addStretch()
            if pane is page.active_pane:
                editing = QLabel("editing")
                editing.setObjectName("Meta")
                row_layout.addWidget(editing)
            self._rows.addWidget(row)
        self.refresh_overlays()
        self.refresh_stats()

    def refresh_overlays(self):
        """The overlay switches are on while every pane has the overlay."""
        panes = self._page.panes
        for name, switch in self.overlay_switches.items():
            on = bool(panes) and all(overlay_box(pane, name).isChecked() for pane in panes)
            if switch.isChecked() != on:
                # (the row's look follows; the panes are not told)
                self._following = True
                try:
                    switch.setChecked(on)
                finally:
                    self._following = False


class PaneFrame(QFrame):
    """One pane of a page, with a header over it while the page shows
    more than one: the pane's letter on its colour, the file's name,
    what the image is, and a close button. The frame takes the accent
    while its pane is the active one; pressing anywhere in it makes it
    so."""

    def __init__(self, pane, page):
        # A child of the page's grid host from the start (the grid places
        # it later, which may wait for a transition): the pane's window
        # is the main window at once, as loading an image expects
        super().__init__(page._grid_host)
        # Sized by the grid's stretch alone, not by what the pane would
        # like: every slot of a layout the same size, full or empty
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.setObjectName("PaneFrame")
        self.pane = pane
        self._page = page
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        self.header = QFrame()
        self.header.setObjectName("PaneHeader")
        row = QHBoxLayout(self.header)
        row.setContentsMargins(8, 0, 4, 0)
        row.setSpacing(8)
        self.badge = QLabel("A")
        self.badge.setObjectName("PaneBadge")
        row.addWidget(self.badge)
        self.name = _ElidedLabel("PaneName")
        row.addWidget(self.name)
        self.caption = _ElidedLabel("Meta", least=12)
        row.addWidget(self.caption)
        row.addStretch()
        # Why a linked pane does not follow the image being edited
        self.notice = QLabel()
        self.notice.setObjectName("Badge")
        set_tone(self.notice, "warning")
        self.notice.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.notice.hide()
        row.addWidget(self.notice)
        self.link_button = QToolButton()
        self.link_button.setCheckable(True)
        self.link_button.setChecked(True)
        self.link_button.setAccessibleName("Follow the other panes")
        self.link_button.setIconSize(QSize(14, 14))
        self.link_button.setAutoRaise(True)
        self.link_button.toggled.connect(lambda on: self._page.set_linked(self.pane, on))
        row.addWidget(self.link_button)
        self.close_button = QToolButton()
        self.close_button.setToolTip("Close this pane")
        self.close_button.setAccessibleName("Close pane")
        self.close_button.setIconSize(QSize(14, 14))
        self.close_button.setAutoRaise(True)
        self.close_button.clicked.connect(lambda: self._page.remove_pane(self.pane))
        row.addWidget(self.close_button)
        self._layout.addWidget(self.header)
        self._layout.addWidget(pane, 1)

        # Pressing the pane, its canvas or its header makes it the active one
        for widget in (self.header, pane, pane.canvas, getattr(pane, "empty_canvas", None)):
            if widget is not None:
                widget.installEventFilter(self)
        self.refresh_icons()
        self.refresh()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.MouseButtonPress:
            self._page.set_active_pane(self.pane)
        return False

    def set_letter(self, letter):
        self.badge.setText(letter)
        self.badge.setProperty("letter", letter)
        _repolish(self.badge)

    def set_single(self, single):
        """Alone on the page: no header, no border (the look of one image)."""
        self.header.setVisible(not single)
        self.setProperty("single", "true" if single else "false")
        self._layout.setContentsMargins(*((0,) * 4 if single else (1, 1, 1, 1)))
        _repolish(self)

    def set_active(self, active):
        self.setProperty("active", "true" if active else "false")
        _repolish(self)

    def set_closable(self, closable):
        self.close_button.setEnabled(closable)
        self.close_button.setToolTip(
            "Close this pane" if closable else "The only pane stays; close the tab instead"
        )

    def refresh(self):
        """The header follows the pane's image."""
        self.name.setFullText(pane_name(self.pane) or "No image")
        self.caption.setFullText(pane_caption(self.pane))

    def refresh_icons(self):
        self.close_button.setIcon(QIcon(themed_icon("pane_close.svg")))
        self.refresh_link()

    def refresh_link(self):
        """The link toggle: on, off, or dimmed with the reason it cannot
        follow the active pane (another sky frame, or no sky in common),
        the reason also in a badge beside it."""
        pane = self.pane
        page = self._page
        on = pane.linked
        self.link_button.blockSignals(True)
        self.link_button.setChecked(on)
        self.link_button.blockSignals(False)
        self.link_button.setIcon(QIcon(themed_icon("link.svg" if on else "link_off.svg")))
        reason, badge = unfollowed(pane, page)
        opacity = 0.45 if reason else 1.0
        if getattr(self, "_link_opacity", None) != opacity:
            self._link_opacity = opacity
            effect = QGraphicsOpacityEffect(self.link_button)
            effect.setOpacity(opacity)
            self.link_button.setGraphicsEffect(effect)
        self.notice.setText(badge or "")
        self.notice.setToolTip(reason or "")
        self.notice.setVisible(bool(badge))
        # The badge takes the caption's room (the Compare tab lists it), not the name's
        self.caption.setVisible(not badge)
        if reason:
            self.link_button.setToolTip(reason)
        elif on:
            self.link_button.setToolTip("Follows the other panes (click to let it go its own way)")
        else:
            self.link_button.setToolTip("Goes its own way (click to follow the other panes)")


def unfollowed(pane, page):
    """(why, badge) when linked ``pane`` cannot follow the active pane:
    its image is in another sky frame, or shares no sky with the active
    one; (None, None) otherwise."""
    active = page.active_pane
    if (
        not pane.linked
        or active is None
        or pane is active
        or pane.current_image_data is None
        or active.current_image_data is None
    ):
        return None, None
    letter = page.letter(active)
    kind, active_kind = frame_kind(pane), frame_kind(active)
    if kind != active_kind:
        own = FRAME_NAMES.get(kind, "no sky frame")
        theirs = FRAME_NAMES.get(active_kind, "no sky frame")
        return (
            f"Linked, but its own view: a {own} image cannot follow the "
            f"{theirs} image in pane {letter}",
            "Other frame",
        )
    distance = apart(pane, active)
    if distance is not None:
        return (
            f"Linked, but its own view: this image and pane {letter}'s share no "
            f"sky ({format_angle(distance)} apart, centre to centre), so zoom, pan, "
            "the crosshair and regions do not reach it. Open an image of the "
            "same part of the sky to compare them.",
            "No overlap",
        )
    return None, None


class EmptySlot(QFrame):
    """A slot of the grid without an image: offers to add one (``addRequested``)."""

    addRequested = pyqtSignal(int)  # the slot's index

    def __init__(self, letter, index=0, parent=None):
        super().__init__(parent)
        self.setObjectName("EmptySlot")
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addStretch()
        self.letter = letter
        self.index = index
        badge = QLabel(letter)
        badge.setObjectName("PaneBadge")
        badge.setProperty("letter", letter)
        layout.addWidget(badge, 0, Qt.AlignHCenter)
        self.add_button = QPushButton("Add image…")
        self.add_button.setToolTip("Open a FITS file or CASA image in this pane")
        self.add_button.clicked.connect(lambda: self.addRequested.emit(self.index))
        layout.addWidget(self.add_button, 0, Qt.AlignHCenter)
        hint = QLabel("or drop a file here")
        hint.setObjectName("Hint")
        layout.addWidget(hint, 0, Qt.AlignHCenter)
        layout.addStretch()


class ImagePage(QWidget):
    """The page of one document tab. ``panes`` are its SolarRadioImageTabs
    in slot order; ``active_pane`` the one the shared controls work on;
    ``layout_size`` how many slots the grid shows."""

    def __init__(self, name="", parent=None):
        super().__init__(parent)
        self.setObjectName(name)
        self.slots = [None]  # a pane or None per slot of the layout
        self.frames = {}  # pane -> its PaneFrame
        self._slots = []
        self._active = None
        self._pane_names = {}
        self.layout_size = 1
        self.full_axes = False  # compare panes drawn with ticks and labels
        self.colorbar = False  # and a colorbar under each image
        self._veil_widget = None
        self.sync = PaneSync(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # The active pane's top bar
        self._top_bars = QStackedWidget()
        layout.addWidget(self._top_bars)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setChildrenCollapsible(False)

        # The panes, with the active pane's canvas bar under them
        column = QWidget()
        column_layout = QVBoxLayout(column)
        column_layout.setContentsMargins(0, 0, 0, 0)
        column_layout.setSpacing(0)
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(0)
        column_layout.addWidget(self._grid_host, 1)
        self._canvas_bars = QStackedWidget()
        column_layout.addWidget(self._canvas_bars)
        column.setMinimumWidth(400)
        splitter.addWidget(column)

        inspector = self._build_inspector()
        inspector.setMinimumWidth(inspector.fontMetrics().averageCharWidth() * 40)
        splitter.addWidget(inspector)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        width = inspector.fontMetrics().averageCharWidth() * 48
        splitter.setSizes([max(800, 1400 - width), width])
        layout.addWidget(splitter, 1)

        self._layout_actions = {}
        self._layout_group = self._build_layout_group()
        self.sync_popover = SyncPopover(self)
        self._sync_button = sync_button(self)
        self._sync_button.hide()
        # The file-stepping keys, for the active pane ([ ] { })
        for keys, name in (
            ("[", "_on_prev_file"),
            ("]", "_on_next_file"),
            ("{", "_on_first_file"),
            ("}", "_on_last_file"),
        ):
            QShortcut(QKeySequence(keys), self).activated.connect(
                lambda name=name: self._active_step(name)
            )

    def _active_step(self, name):
        if self._active is not None:
            getattr(self._active, name)()

    def _build_inspector(self):
        """The column at the right: the active pane's header, the Display
        and Statistics tabs (each the active pane's page), the active
        pane's cursor readout pinned at the foot."""
        inspector = QFrame()
        inspector.setObjectName("Inspector")
        layout = QVBoxLayout(inspector)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._headers = QStackedWidget()
        layout.addWidget(self._headers)
        self._editing_row = self._build_editing_row()
        layout.addWidget(self._editing_row)

        self.inspector_tabs = QTabWidget()
        self.inspector_tabs.setObjectName("InspectorTabs")
        self.inspector_tabs.tabBar().setFocusPolicy(Qt.NoFocus)
        self._display_pages = QStackedWidget()
        self._statistics_pages = QStackedWidget()
        self.inspector_tabs.addTab(self._display_pages, "Display")
        self.inspector_tabs.addTab(self._statistics_pages, "Statistics")
        self._compare_panel = ComparePanel(self)
        self.inspector_tabs.addTab(_scroll(self._compare_panel), "Compare")
        self.inspector_tabs.setTabVisible(2, False)  # with more than one slot
        layout.addWidget(self.inspector_tabs, 1)

        self._readouts = QStackedWidget()
        layout.addWidget(self._readouts)
        self.cursor_values = CursorValues(self)
        layout.addWidget(self.cursor_values)
        return inspector

    def _build_layout_group(self):
        """The layout buttons for the canvas bar: one, two, four or six
        slots."""
        group = QActionGroup(self)
        group.setExclusive(True)
        buttons = []
        for size, icon, tip in LAYOUTS:
            action = QAction(launcher_icon(icon), "", self)
            action.setCheckable(True)
            action.setToolTip(tip)
            action.setText(tip)
            action.triggered.connect(lambda _checked, size=size: self.set_layout(size))
            group.addAction(action)
            self._layout_actions[size] = action
            buttons.append(_tool_button(action))
        self._layout_actions[1].setChecked(True)
        return segmented(buttons)

    def _build_editing_row(self):
        """The inspector's row over the tabs while there is more than one
        slot: which pane the controls edit, and the way to give every
        pane its display."""
        row = QFrame()
        row.setObjectName("EditingRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(14, 0, 10, 0)
        layout.setSpacing(8)
        self._editing_badge = QLabel("A")
        self._editing_badge.setObjectName("PaneBadge")
        layout.addWidget(self._editing_badge)
        editing = QLabel("Editing")
        editing.setObjectName("Hint")
        layout.addWidget(editing)
        self._editing_name = _ElidedLabel("PaneName", least=6)
        layout.addWidget(self._editing_name)
        layout.addStretch()
        button = link_button("Apply to all panes", self.apply_display_to_all)
        button.setToolTip(
            "Give every pane this pane's colormap, stretch, gamma and overlays, "
            "and its range where the units match"
        )
        layout.addWidget(button)
        row.hide()
        return row

    def _show_editing(self):
        """The editing row names the active pane."""
        pane = self._active
        if pane is None:
            return
        letter = self.letter(pane)
        self._editing_badge.setText(letter)
        self._editing_badge.setProperty("letter", letter)
        _repolish(self._editing_badge)
        self._editing_name.setFullText(pane_name(pane) or "No image")

    # ----------------------------------------------------------- panes
    def _chrome(self, pane):
        """(stack, the pane's widget for it) for each piece of chrome."""
        return (
            (self._top_bars, pane.top_bar),
            (self._canvas_bars, pane.canvas_bar),
            (self._headers, pane.inspector_header),
            (self._display_pages, pane.display_page),
            (self._statistics_pages, pane.statistics_page),
            (self._readouts, pane.cursor_readout),
        )

    def add_pane(self, pane, slot=None):
        """Take ``pane`` (a SolarRadioImageTab built for this page) into
        ``slot``, or the first free one (the layout grows when none is);
        the first pane added is the active one."""
        if len(self.panes) >= MAX_PANES:
            raise ValueError(f"a page holds at most {MAX_PANES} panes")
        if slot is None or not (0 <= slot < len(self.slots)) or self.slots[slot] is not None:
            slot = self.free_slot()
        pane.page = self
        pane.inspector_tabs = self.inspector_tabs
        for stack, widget in self._chrome(pane):
            stack.addWidget(widget)
        self.frames[pane] = PaneFrame(pane, self)
        set_hand_cursor(self.frames[pane])
        if slot is None:
            self._resize_slots(self._fitting_layout(len(self.panes) + 1))
            slot = self.free_slot()
            self.slots[slot] = pane
            self.set_layout(len(self.slots))
        else:
            self.slots[slot] = pane
            self._place_panes()
        # The overlays every pane draws, the new one draws too
        for name in self._compare_panel.overlay_switches:
            box = overlay_box(pane, name)
            others = [p for p in self.panes if p is not pane]
            if others and all(overlay_box(p, name).isChecked() for p in others):
                box.setChecked(True)
            box.toggled.connect(lambda _on: self._compare_panel.refresh_overlays())
        if self._active is None:
            self.set_active_pane(pane)
        return pane

    def new_pane(self, slot=None):
        """A pane of this page, in ``slot`` or the first free one (the
        layout grows if every slot is taken). None when the page is full."""
        if len(self.panes) >= MAX_PANES:
            return None
        from ..viewer import SolarRadioImageTab

        pane = SolarRadioImageTab(self, "")
        self.add_pane(pane, slot)
        pane.refresh_icons()
        return pane

    def remove_pane(self, pane):
        """Close ``pane``; its slot is left empty, the other panes where
        they are. The only pane stays."""
        if pane not in self.panes or len(self.panes) == 1:
            return
        index = self.slots.index(pane)
        self.slots[index] = None
        frame = self.frames.pop(pane)
        for stack, widget in self._chrome(pane):
            stack.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        if self._active is pane:
            self._active = None
            # The nearest pane after it takes over, else the nearest before
            after = [p for p in self.slots[index:] if p is not None]
            before = [p for p in self.slots[:index] if p is not None]
            self.set_active_pane(after[0] if after else before[-1])
        pane.cleanup()
        self._grid.removeWidget(frame)
        frame.setParent(None)
        frame.deleteLater()
        self._place_panes()

    def open_in_new_pane(self, path, slot=None):
        """Show the image at ``path`` in a new pane, in ``slot`` or the
        first free one. Returns whether it loaded (None if cancelled), as
        ``open_image_path``; a pane whose image did not load goes again."""
        pane = self.new_pane(slot)
        if pane is None:
            return False
        self.set_active_pane(pane)
        loaded = pane.open_path(path)
        if not loaded or pane.current_image_data is None:
            self.remove_pane(pane)
        return loaded

    def add_image(self, slot=None):
        """Choose an image for ``slot`` (the first free one otherwise)."""
        from ..ui.open_image import choose_image
        from ..ui.recent_files import last_image_folder

        start = last_image_folder()
        shown = [pane.imagename for pane in self.panes if pane.imagename]
        if shown:
            start = os.path.dirname(shown[-1].rstrip("/")) or start
        path = choose_image(self.window(), "Open an image to compare", directory=start)
        if path:
            self.open_in_new_pane(path, slot)

    @property
    def active_pane(self):
        return self._active

    @property
    def panes(self):
        """The panes, in the order of their slots."""
        return [pane for pane in self.slots if pane is not None]

    def letter(self, pane):
        """The letter of ``pane``'s slot (its badge)."""
        return LETTERS[self.slots.index(pane)]

    def free_slot(self):
        """The first slot without a pane, or None."""
        return next((index for index, pane in enumerate(self.slots) if pane is None), None)

    def set_active_pane(self, pane):
        """The shared controls show and work on ``pane``."""
        if pane not in self.panes or pane is self._active:
            return
        self._active = pane
        for stack, widget in self._chrome(pane):
            stack.setCurrentWidget(widget)
        # The page's controls go into the active pane's canvas bar
        pane.page_controls_slot.addWidget(self._layout_group)
        pane.page_tools_slot.addWidget(self._sync_button)
        self._show_editing()
        for other, frame in self.frames.items():
            frame.set_active(other is pane)
        self._compare_panel.refresh()
        self.refresh_sync()
        self.update_title()

    def pane_changed(self, pane):
        """``pane``'s image changed: its header and the title follow."""
        frame = self.frames.get(pane)
        if frame is not None:
            frame.refresh()
        self._compare_panel.refresh()
        self._show_editing()
        self.cursor_values.rebuild()
        self.refresh_sync()
        self.update_title()

    # ------------------------------------------------------------ sync
    def set_sync(self, key, on):
        """Turn one way of following each other on or off."""
        self.sync.on[key] = bool(on)
        self.refresh_sync()
        if key == "view" and on and self._active is not None:
            self.sync.view_settled(self._active)

    def set_linked(self, pane, on):
        """``pane`` follows the others, or goes its own way."""
        pane.linked = bool(on)
        self.refresh_sync()
        if on and self._active is not None and self._active is not pane:
            self.sync.view_settled(self._active)

    def sync_all(self):
        for pane in self.panes:
            pane.linked = True
        self.refresh_sync()
        if self._active is not None:
            self.sync.view_settled(self._active)

    def refresh_sync(self):
        """The Synced button, the popover, the headers' link toggles and
        the status bar say which panes are in sync."""
        in_sync = self.sync.in_sync()
        letters = " ".join(self.letter(pane) for pane in in_sync)
        ways = [words for key, words, _default in SYNC_WAYS if self.sync.on[key]]
        synced = bool(ways) and len(in_sync) >= 2
        self._sync_button.setText(letters if synced else "")
        self._sync_button.setIcon(QIcon(themed_icon("link.svg" if synced else "link_off.svg")))
        self._sync_button.setToolTip(
            (f"Panes {letters} follow each other on {', '.join(ways).lower()}" if synced
             else "The panes go their own way") + "\nClick for how the panes follow each other"
        )
        for frame in self.frames.values():
            frame.refresh_link()
        if self.sync_popover.isVisible():
            self.sync_popover.refresh()
        self.cursor_values.clear()
        if self.layout_size > 1:
            count = len([pane for pane in self.panes if pane.current_image_data is not None])
            said = f"{count} image{'s' if count != 1 else ''}"
            if len(in_sync) >= 2 and ways:
                said += f" · {', '.join(ways).lower()} synced on {letters}"
            stray = self.unfollowed_note()
            if stray:
                said += f" · {stray}"
            self.window().statusBar().showMessage(said)

    def unfollowed_note(self):
        """Which linked panes cannot follow the active one, for the status
        bar; "" when every one can."""
        stray = [pane for pane in self.panes if unfollowed(pane, self)[1]]
        if not stray or self._active is None:
            return ""
        return (
            f"{' '.join(self.letter(pane) for pane in stray)} cannot follow "
            f"{self.letter(self._active)}: see the pane header"
        )

    def regions_changed(self):
        """A region drawn (or mirrored): the Compare tab's tables follow."""
        self._compare_panel.refresh_stats()

    # --------------------------------------------------------- display
    def set_full_axes(self, on):
        """Draw the panes with their ticks and labels (``on``) or as the
        image alone."""
        self.full_axes = bool(on)
        self._apply_compact()

    def set_colorbar(self, on):
        """Draw a colorbar under each pane's image (``on``) or none."""
        self.colorbar = bool(on)
        self._apply_compact()

    def set_overlay(self, name, on):
        """Every pane draws the overlay ``name`` ("solar_disk", "grid") or not."""
        for pane in self.panes:
            overlay_box(pane, name).setChecked(bool(on))
        self._compare_panel.refresh_overlays()

    def _apply_compact(self):
        """Each pane drawn the way the layout wants: compact (no title)
        with more than one slot, with the axes and the colorbar the page's
        switches ask for."""
        compact = self.layout_size > 1
        wanted = (compact, compact and self.full_axes, compact and self.colorbar)
        for pane in self.panes:
            if (pane.compact_plot, pane.pane_axes, pane.pane_colorbar) != wanted:
                pane.compact_plot, pane.pane_axes, pane.pane_colorbar = wanted
                if pane.current_image_data is not None:
                    pane.schedule_plot()

    def apply_display_to_all(self):
        """Give every pane the active pane's colormap, stretch, gamma and
        overlay switches (not its contours, which are images of their
        own), and its range where the units match."""
        source = self._active
        if source is None:
            return
        cmap = source.cmap_combo.currentText()
        stretch = source.stretch_combo.currentText()
        gamma = source.gamma_entry.text()
        unit = getattr(source, "_current_bunit", None)
        try:
            vmin, vmax = float(source.vmin_entry.text()), float(source.vmax_entry.text())
        except ValueError:
            vmin = vmax = None
        overlays = [
            box.isChecked()
            for box in (
                source.show_beam_checkbox,
                source.show_grid_checkbox,
                source.show_solar_disk_checkbox,
            )
        ]
        for pane in self.panes:
            if pane is source:
                continue
            boxes = (
                pane.show_beam_checkbox,
                pane.show_grid_checkbox,
                pane.show_solar_disk_checkbox,
            )
            controls = (
                pane.cmap_combo,
                pane.stretch_combo,
                pane.gamma_slider,
                pane.gamma_entry,
                pane.vmin_entry,
                pane.vmax_entry,
                *boxes,
            )
            for control in controls:
                control.blockSignals(True)
            try:
                pane.cmap_combo.setCurrentText(cmap)
                pane.stretch_combo.setCurrentText(stretch)
                pane.gamma_entry.setText(gamma)
                try:
                    pane.gamma_slider.setValue(int(float(gamma) * 20))
                except ValueError:
                    pass
                if vmin is not None and getattr(pane, "_current_bunit", None) == unit:
                    pane.show_range(vmin, vmax)
                    pane.show_range_preset(None)
                for box, on in zip(boxes, overlays):
                    box.setChecked(on)
            finally:
                for control in controls:
                    control.blockSignals(False)
            pane.update_gamma_slider_state()
            for row in pane._overlay_rows:
                row.refresh()
            if pane.current_image_data is not None:
                pane.update_display()
        self._compare_panel.refresh_overlays()
        self.window().statusBar().showMessage(
            f"Pane {self.letter(source)}'s display applied to every pane"
        )

    # ---------------------------------------------------------- layout
    @staticmethod
    def _fitting_layout(count):
        """The smallest layout with room for ``count`` panes."""
        for size, _icon, _tip in LAYOUTS:
            if size >= count:
                return size
        return MAX_PANES

    def set_layout(self, size):
        """Show ``size`` slots (1, 2, 4 or 6); not fewer than there are panes."""
        if size not in self._layout_actions or size < len(self.panes):
            self._layout_actions[self.layout_size].setChecked(True)
            return
        self.layout_size = size
        self._layout_actions[size].setChecked(True)
        self._resize_slots(size)
        self._place_panes()

    def _resize_slots(self, size):
        """``size`` slots: more are empty at the end; with fewer, the panes
        keep their slots if they fit, else close up from the first."""
        if size < len(self.slots) and any(self.slots[size:]):
            panes = self.panes
            self.slots = panes + [None] * (size - len(panes))
        else:
            self.slots = (self.slots + [None] * size)[:size]

    def add_slot(self):
        """One more slot: the next bigger layout."""
        for size, _icon, _tip in LAYOUTS:
            if size > self.layout_size:
                self.set_layout(size)
                return

    def _veil(self):
        """The picture of the image area that covers the change of layout
        about to happen, if the page is on screen to see it; None
        otherwise."""
        veil = self._veil_widget
        if veil is not None and (_qt_deleted(veil) or veil.fading()):
            veil = self._veil_widget = None
        if veil is None and self.isVisible() and not self.rect().isEmpty():
            veil = self._veil_widget = _Veil(self, self._grid_host)
        return veil

    def pane_drawn(self, pane):
        """``pane``'s canvas drew itself afresh."""
        self.sync.canvas_drawn(pane)
        veil = self._veil_widget
        if veil is not None and not _qt_deleted(veil):
            veil.drawn(pane)

    def _place_panes(self, now=False):
        """Lay the panes out in the grid, a slot offering an image where
        there is none; alone, a pane fills the grid without a header. On
        screen, the change happens under a picture of the image area as
        it was: once that has dipped to the background the panes move,
        and the new layout fades in once they have drawn."""
        veil = self._veil()
        if veil is not None and not now:
            veil.when_dimmed(lambda: self._place_panes(now=True))
            return
        # The plots first: a pane with a plot scheduled skips the draw its
        # resize would ask for, so it draws once, by its plot, at its new
        # size
        self._apply_compact()
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget in self._slots:
                widget.setParent(None)
                widget.deleteLater()
        self._slots = []
        single = self.layout_size == 1
        gap = 0 if single else GRID_GAP
        self._grid.setSpacing(gap)
        self._grid.setContentsMargins(gap, gap, gap, gap)
        positions = _positions(self.layout_size)
        for index, (row, column) in enumerate(positions):
            pane = self.slots[index]
            if pane is not None:
                frame = self.frames[pane]
                frame.set_letter(LETTERS[index])
                frame.set_single(single)
                frame.set_closable(len(self.panes) > 1)
                self._grid.addWidget(frame, row, column)
            else:
                slot = EmptySlot(LETTERS[index], index)
                slot.addRequested.connect(self.add_image)
                set_hand_cursor(slot)
                self._grid.addWidget(slot, row, column)
                self._slots.append(slot)
        rows = 1 + max(row for row, _column in positions)
        columns = 1 + max(column for _row, column in positions)
        for row in range(2):
            self._grid.setRowStretch(row, 1 if row < rows else 0)
        for column in range(3):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)
        self.setAcceptDrops(not single)
        self.inspector_tabs.setTabVisible(2, not single)
        self._editing_row.setVisible(not single)
        self._sync_button.setVisible(not single)
        self.cursor_values.setVisible(not single)
        self.cursor_values.rebuild()
        self._show_editing()
        self._compare_panel.refresh()
        self.refresh_sync()
        for size, action in self._layout_actions.items():
            action.setEnabled(size >= len(self.panes))
            action.setToolTip(
                dict((s, t) for s, _i, t in LAYOUTS)[size]
                if size >= len(self.panes)
                else "Close a pane first"
            )
        self.update_title()
        if veil is not None:
            veil.wait_for(pane for pane in self.panes if pane.imagename)

    # ----------------------------------------------------------- drops
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and self.layout_size > 1:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        """A file dropped on a pane replaces its image; on a slot (or the
        gaps), it goes in a new pane; more files fill the slots after."""
        paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if url.isLocalFile() and os.path.exists(url.toLocalFile())
        ]
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        target = self._target_at(event.pos())
        first, rest = paths[0], paths[1:]
        if isinstance(target, PaneFrame):
            self.set_active_pane(target.pane)
            target.pane.open_path(first)
        else:
            self.open_in_new_pane(first, target.index if isinstance(target, EmptySlot) else None)
        for path in rest:
            if len(self.panes) >= MAX_PANES:
                break
            self.open_in_new_pane(path)

    def _target_at(self, pos):
        """The pane frame or slot under ``pos`` (page coordinates), or None."""
        local = self._grid_host.mapFrom(self, pos)
        for index in range(self._grid.count()):
            widget = self._grid.itemAt(index).widget()
            if widget is not None and widget.geometry().contains(local):
                return widget
        return None

    # ---------------------------------------------------------- naming
    def pane_renamed(self, pane, name):
        """``pane`` now shows the image called ``name``: the tab follows."""
        self._pane_names[pane] = name
        self.update_title()

    def title(self):
        """What the tab says: the image, or for several, "Compare:" and
        the date they share (else the first image's name)."""
        if len(self.panes) <= 1:
            return self._pane_names.get(self._active, "") or self.objectName()
        dates = [
            str(pane.observation_time)[:10] for pane in self.panes if pane.observation_time
        ]
        if len(dates) == len(self.panes) and len(set(dates)) == 1:
            return f"Compare: {dates[0]}"
        first = self._pane_names.get(self.panes[0]) or pane_name(self.panes[0]) or "images"
        return f"Compare: {first}"

    def update_title(self):
        window = self.window()
        if hasattr(window, "set_tab_title"):
            window.set_tab_title(self, self.title())

    # --------------------------------------------------------- lifetime
    def refresh_icons(self):
        for pane in self.panes:
            pane.refresh_icons()
        for frame in self.frames.values():
            frame.refresh_icons()
        for (size, icon, _tip) in LAYOUTS:
            self._layout_actions[size].setIcon(launcher_icon(icon))
        self.refresh_sync()

    def cleanup(self):
        """Release what the panes hold once the tab is closed."""
        for pane in self.panes:
            pane.cleanup()

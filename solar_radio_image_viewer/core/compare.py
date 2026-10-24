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
import os

from PyQt5.QtCore import QEvent, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QAction,
    QActionGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..ui.icons import themed_icon
from ..ui.styles import set_hand_cursor
from ..ui.widgets import segmented

logger = logging.getLogger(__name__)

MAX_PANES = 4
LETTERS = "ABCD"
# The layouts: slots, icon, what the button says
LAYOUTS = (
    (1, "layout_single.svg", "One image"),
    (2, "layout_two.svg", "Two images side by side"),
    (4, "layout_grid.svg", "Up to four images in a grid"),
)
GRID_GAP = 6
ICON_SIZE = QSize(16, 16)


def _positions(size):
    """(row, column) of each slot of a layout of ``size``."""
    if size == 1:
        return [(0, 0)]
    if size == 2:
        return [(0, 0), (0, 1)]
    return [(0, 0), (0, 1), (1, 0), (1, 1)]


def _tool_button(action):
    button = QToolButton()
    button.setDefaultAction(action)
    button.setIconSize(ICON_SIZE)
    button.setAutoRaise(True)
    return button


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


class PaneFrame(QFrame):
    """One pane of a page, with a header over it while the page shows
    more than one: the pane's letter on its colour, the file's name,
    what the image is, and a close button. The frame takes the accent
    while its pane is the active one; pressing anywhere in it makes it
    so."""

    def __init__(self, pane, page):
        super().__init__()
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


class EmptySlot(QFrame):
    """A slot of the grid without an image: offers to add one (``addRequested``)."""

    addRequested = pyqtSignal()

    def __init__(self, letter, parent=None):
        super().__init__(parent)
        self.setObjectName("EmptySlot")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addStretch()
        badge = QLabel(letter)
        badge.setObjectName("PaneBadge")
        badge.setProperty("letter", letter)
        layout.addWidget(badge, 0, Qt.AlignHCenter)
        self.add_button = QPushButton("Add image…")
        self.add_button.setToolTip("Open a FITS file or CASA image in this pane")
        self.add_button.clicked.connect(self.addRequested)
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
        self.panes = []
        self.frames = {}  # pane -> its PaneFrame
        self._slots = []
        self._active = None
        self._pane_names = {}
        self.layout_size = 1

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

        self.inspector_tabs = QTabWidget()
        self.inspector_tabs.setObjectName("InspectorTabs")
        self.inspector_tabs.tabBar().setFocusPolicy(Qt.NoFocus)
        self._display_pages = QStackedWidget()
        self._statistics_pages = QStackedWidget()
        self.inspector_tabs.addTab(self._display_pages, "Display")
        self.inspector_tabs.addTab(self._statistics_pages, "Statistics")
        layout.addWidget(self.inspector_tabs, 1)

        self._readouts = QStackedWidget()
        layout.addWidget(self._readouts)
        return inspector

    def _build_layout_group(self):
        """The layout buttons for the canvas bar: one, two or four slots,
        and one more slot."""
        group = QActionGroup(self)
        group.setExclusive(True)
        buttons = []
        for size, icon, tip in LAYOUTS:
            action = QAction(QIcon(themed_icon(icon)), "", self)
            action.setCheckable(True)
            action.setToolTip(tip)
            action.setText(tip)
            action.triggered.connect(lambda _checked, size=size: self.set_layout(size))
            group.addAction(action)
            self._layout_actions[size] = action
            buttons.append(_tool_button(action))
        self._add_pane_action = QAction(QIcon(themed_icon("pane_add.svg")), "", self)
        self._add_pane_action.setToolTip("Add a pane for another image")
        self._add_pane_action.setText("Add a pane")
        self._add_pane_action.triggered.connect(self.add_slot)
        buttons.append(_tool_button(self._add_pane_action))
        self._layout_actions[1].setChecked(True)
        return segmented(buttons)

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

    def add_pane(self, pane):
        """Take ``pane`` (a SolarRadioImageTab built for this page) into
        the next slot; the first pane added is the active one."""
        if len(self.panes) >= MAX_PANES:
            raise ValueError(f"a page holds at most {MAX_PANES} panes")
        pane.page = self
        pane.inspector_tabs = self.inspector_tabs
        self.panes.append(pane)
        for stack, widget in self._chrome(pane):
            stack.addWidget(widget)
        self.frames[pane] = PaneFrame(pane, self)
        set_hand_cursor(self.frames[pane])
        if len(self.panes) > self.layout_size:
            self.set_layout(self._fitting_layout(len(self.panes)))
        else:
            self._place_panes()
        if self._active is None:
            self.set_active_pane(pane)
        return pane

    def new_pane(self):
        """A pane of this page, added to its next slot (the layout grows
        if every slot is taken). None when the page is full."""
        if len(self.panes) >= MAX_PANES:
            return None
        from ..viewer import SolarRadioImageTab

        pane = SolarRadioImageTab(self, "")
        self.add_pane(pane)
        pane.refresh_icons()
        return pane

    def remove_pane(self, pane):
        """Close ``pane``; the panes after it move up a slot. The only
        pane stays."""
        if pane not in self.panes or len(self.panes) == 1:
            return
        index = self.panes.index(pane)
        self.panes.remove(pane)
        frame = self.frames.pop(pane)
        for stack, widget in self._chrome(pane):
            stack.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        if self._active is pane:
            self._active = None
            self.set_active_pane(self.panes[min(index, len(self.panes) - 1)])
        pane.cleanup()
        self._grid.removeWidget(frame)
        frame.setParent(None)
        frame.deleteLater()
        self._place_panes()

    def open_in_new_pane(self, path):
        """Show the image at ``path`` in a new pane. Returns whether it
        loaded (None if cancelled), as ``open_image_path``; a pane whose
        image did not load goes again."""
        pane = self.new_pane()
        if pane is None:
            return False
        self.set_active_pane(pane)
        loaded = pane.open_path(path)
        if not loaded or pane.current_image_data is None:
            self.remove_pane(pane)
        return loaded

    def add_image(self):
        """Choose an image for the next slot."""
        from ..ui.open_image import choose_image
        from ..ui.recent_files import last_image_folder

        start = last_image_folder()
        shown = [pane.imagename for pane in self.panes if pane.imagename]
        if shown:
            start = os.path.dirname(shown[-1].rstrip("/")) or start
        path = choose_image(self.window(), "Open an image to compare", directory=start)
        if path:
            self.open_in_new_pane(path)

    @property
    def active_pane(self):
        return self._active

    def set_active_pane(self, pane):
        """The shared controls show and work on ``pane``."""
        if pane not in self.panes or pane is self._active:
            return
        self._active = pane
        for stack, widget in self._chrome(pane):
            stack.setCurrentWidget(widget)
        # The page's controls go into the active pane's canvas bar
        pane.page_controls_slot.addWidget(self._layout_group)
        for other, frame in self.frames.items():
            frame.set_active(other is pane)
        self.update_title()

    def pane_changed(self, pane):
        """``pane``'s image changed: its header and the title follow."""
        frame = self.frames.get(pane)
        if frame is not None:
            frame.refresh()
        self.update_title()

    # ---------------------------------------------------------- layout
    @staticmethod
    def _fitting_layout(count):
        """The smallest layout with room for ``count`` panes."""
        for size, _icon, _tip in LAYOUTS:
            if size >= count:
                return size
        return MAX_PANES

    def set_layout(self, size):
        """Show ``size`` slots (1, 2 or 4); not fewer than there are panes."""
        if size not in self._layout_actions or size < len(self.panes):
            self._layout_actions[self.layout_size].setChecked(True)
            return
        self.layout_size = size
        self._layout_actions[size].setChecked(True)
        self._place_panes()

    def add_slot(self):
        """One more slot: the next bigger layout."""
        for size, _icon, _tip in LAYOUTS:
            if size > self.layout_size:
                self.set_layout(size)
                return

    def _place_panes(self):
        """Lay the panes out in the grid, a slot offering an image where
        there is none; alone, a pane fills the grid without a header."""
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
            if index < len(self.panes):
                pane = self.panes[index]
                frame = self.frames[pane]
                frame.set_letter(LETTERS[index])
                frame.set_single(single)
                frame.set_closable(len(self.panes) > 1)
                self._grid.addWidget(frame, row, column)
            else:
                slot = EmptySlot(LETTERS[index])
                slot.addRequested.connect(self.add_image)
                set_hand_cursor(slot)
                self._grid.addWidget(slot, row, column)
                self._slots.append(slot)
        rows = 1 if self.layout_size <= 2 else 2
        columns = 1 if single else 2
        for row in range(2):
            self._grid.setRowStretch(row, 1 if row < rows else 0)
        for column in range(2):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)
        self.setAcceptDrops(not single)
        for size, action in self._layout_actions.items():
            action.setEnabled(size >= len(self.panes))
            action.setToolTip(
                dict((s, t) for s, _i, t in LAYOUTS)[size]
                if size >= len(self.panes)
                else "Close a pane first"
            )
        self._add_pane_action.setEnabled(self.layout_size < MAX_PANES)
        self.update_title()

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
            self.open_in_new_pane(first)
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
            self._layout_actions[size].setIcon(QIcon(themed_icon(icon)))
        self._add_pane_action.setIcon(QIcon(themed_icon("pane_add.svg")))

    def cleanup(self):
        """Release what the panes hold once the tab is closed."""
        for pane in self.panes:
            pane.cleanup()

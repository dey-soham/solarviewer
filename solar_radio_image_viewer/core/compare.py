"""
A document tab's page: up to four panes, each a :class:`SolarRadioImageTab`
showing one image, in a grid under one top bar and beside one inspector.

The chrome around the images is the active pane's. Each pane builds its
own top bar, inspector header, Display and Statistics pages, cursor
readout and canvas bar; the page keeps each kind in a stack and shows the
active pane's, so clicking another pane swaps every control to it while
the inspector keeps the tab (Display or Statistics) it was on.
"""

import logging

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QFrame,
    QGridLayout,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

MAX_PANES = 4


class ImagePage(QWidget):
    """The page of one document tab. ``panes`` are its SolarRadioImageTabs
    in slot order; ``active_pane`` the one the shared controls work on."""

    def __init__(self, name="", parent=None):
        super().__init__(parent)
        self.setObjectName(name)
        self.panes = []
        self._active = None
        self._pane_names = {}

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
        grid_host = QWidget()
        self._grid = QGridLayout(grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(0)
        column_layout.addWidget(grid_host, 1)
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
        self._place_panes()
        if self._active is None:
            self.set_active_pane(pane)
        return pane

    def _place_panes(self):
        """Lay the panes out in the grid: one fills it, more share it two
        to a row."""
        for index, pane in enumerate(self.panes):
            self._grid.addWidget(pane, index // 2, index % 2)

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
        self.update_title()

    # ---------------------------------------------------------- naming
    def pane_renamed(self, pane, name):
        """``pane`` now shows the image called ``name``: the tab follows."""
        self._pane_names[pane] = name
        self.update_title()

    def title(self):
        """What the tab says: the active pane's image."""
        return self._pane_names.get(self._active, "") or self.objectName()

    def update_title(self):
        window = self.window()
        if hasattr(window, "set_tab_title"):
            window.set_tab_title(self, self.title())

    # --------------------------------------------------------- lifetime
    def refresh_icons(self):
        for pane in self.panes:
            pane.refresh_icons()

    def cleanup(self):
        """Release what the panes hold once the tab is closed."""
        for pane in self.panes:
            pane.cleanup()

"""
The main window's image tabs: a strip of tabs at the right end of the menu
bar ("Tab 1", a "+" after them) over a stack of pages.

``DocumentTabs`` is the stack, with the QTabWidget methods the window and
its mixins use (``addTab``, ``currentWidget``, ``tabText``...); its
``strip`` is the widget that goes in the menu bar's corner.
"""

from PyQt5.QtCore import QEvent, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QMenuBar,
    QStackedWidget,
    QTabBar,
    QToolButton,
    QWidget,
)

from .icons import themed_icon


class DocumentTabBar(QTabBar):
    """The strip of tabs: flat, each outlined in the accent while open;
    double-click renames one."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DocumentTabs")
        self.setDrawBase(False)
        self.setExpanding(False)
        self.setUsesScrollButtons(True)
        self.setElideMode(Qt.ElideRight)
        self.setTabsClosable(True)
        self.setFocusPolicy(Qt.NoFocus)

    def mouseDoubleClickEvent(self, event):
        index = self.tabAt(event.pos())
        if index >= 0:
            # A modal dialog rather than in-place editing (which crashed)
            new_text, ok = QInputDialog.getText(
                self, "Edit Tab Name", "Enter new tab name:", text=self.tabText(index)
            )
            if ok and new_text:
                self.setTabText(index, new_text)
        super().mouseDoubleClickEvent(event)

    def tabSizeHint(self, index):
        size = super().tabSizeHint(index)
        return QSize(min(size.width(), 220), size.height())

    def minimumTabSizeHint(self, index):
        # Tabs are never squeezed to "Ta…": past the room, they scroll
        return self.tabSizeHint(index)

    def minimumSizeHint(self):
        # No room kept for the scroll buttons while the tabs all fit (it
        # left a gap between the last tab and the + button)
        least = super().minimumSizeHint()
        return QSize(min(least.width(), self.sizeHint().width()), least.height())


class _Strip(QWidget):
    """The strip's size hint keeps within its maximum width: a menu bar
    places its corner widget by the hint, so a wider one would be put
    over the menus."""

    def sizeHint(self):
        hint = super().sizeHint()
        return QSize(min(hint.width(), self.maximumWidth()), hint.height())


class DocumentTabs(QStackedWidget):
    """The pages, one per image tab, under the tab strip."""

    currentChanged = pyqtSignal(int)
    tabCloseRequested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.strip = _Strip()
        layout = QHBoxLayout(self.strip)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(2)
        # The tabs keep to the right, against the + (a strip wider than
        # its tabs, as at the start, leaves its spare room on the left)
        layout.addStretch()
        self._bar = DocumentTabBar(self.strip)
        layout.addWidget(self._bar)
        self.add_tab_button = QToolButton(self.strip)
        self.add_tab_button.setToolTip("New tab")
        self.add_tab_button.setFixedSize(28, 28)
        self.add_tab_button.setIconSize(QSize(22, 22))
        self.add_tab_button.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self.add_tab_button)
        self._watched = None  # the strip's parent, watched for resizes
        self.refresh_theme()

        self._bar.currentChanged.connect(self._show_current)
        self._bar.tabCloseRequested.connect(self.tabCloseRequested)
        super().currentChanged.connect(self.currentChanged)

    def _show_current(self, index):
        if 0 <= index < self.count():
            super().setCurrentIndex(index)

    # ---------------------------------------------------- QTabWidget's API
    def tabBar(self):
        return self._bar

    def addTab(self, page, text):
        index = self.addWidget(page)
        self._bar.addTab(text)
        self._relayout()
        return index

    def removeTab(self, index):
        page = self.widget(index)
        if page is not None:
            self.removeWidget(page)
        self._bar.removeTab(index)
        self._relayout()

    def _relayout(self):
        """Give the strip the width its tabs now need. A menu bar places
        its corner widget only when it is resized, so a strip that grew
        (a tab opened, a name changed) was cut off until the window was
        resized; sizing the bar to its hint makes it place the strip."""
        parent = self.strip.parentWidget()
        if parent is None:
            return
        if parent is not self._watched:
            parent.installEventFilter(self)
            self._watched = parent
        self._fit_strip(parent)
        if isinstance(parent, QMenuBar):
            parent.adjustSize()
        else:
            parent.updateGeometry()

    def _fit_strip(self, parent):
        """The strip takes no more than the room right of the menus (the
        tabs scroll within it), so many tabs never push the menus out."""
        if isinstance(parent, QMenuBar):
            menus_end = max(
                (parent.actionGeometry(action).right() for action in parent.actions()),
                default=0,
            )
            room = parent.width() - menus_end - 24
        else:
            room = parent.width() - 24
        least = self.add_tab_button.width() + 120  # the + and one tab
        self.strip.setMaximumWidth(max(least, room))

    def eventFilter(self, watched, event):
        if watched is self._watched and event.type() == QEvent.Resize:
            self._fit_strip(watched)
        return super().eventFilter(watched, event)

    def setCurrentIndex(self, index):
        self._bar.setCurrentIndex(index)

    def setCurrentWidget(self, page):
        self.setCurrentIndex(self.indexOf(page))

    def tabText(self, index):
        return self._bar.tabText(index)

    def setTabText(self, index, text):
        self._bar.setTabText(index, text)
        self._relayout()

    def setTabToolTip(self, index, text):
        self._bar.setTabToolTip(index, text)

    def setTabsClosable(self, closable):
        self._bar.setTabsClosable(closable)

    def refresh_theme(self):
        """The add and close icons are pictures of the theme."""
        self.add_tab_button.setIcon(QIcon(themed_icon("add_tab_default.png")))
        close_default = themed_icon("close_tab_default.png")
        close_hover = themed_icon("close_tab_hover.png")
        self._bar.setStyleSheet(
            f"""
            QTabBar::close-button {{
                image: url("{close_default}");
                subcontrol-position: right;
                subcontrol-origin: padding;
                margin-left: 2px;
                width: 14px;
                height: 14px;
            }}
            QTabBar::close-button:hover {{
                image: url("{close_hover}");
            }}
            """
        )

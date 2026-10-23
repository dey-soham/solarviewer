"""
The main window's image tabs: a strip of tabs at the right end of the menu
bar ("Tab 1", a "+" after them) over a stack of pages.

``DocumentTabs`` is the stack, with the QTabWidget methods the window and
its mixins use (``addTab``, ``currentWidget``, ``tabText``...); its
``strip`` is the widget that goes in the menu bar's corner.
"""

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
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


class DocumentTabs(QStackedWidget):
    """The pages, one per image tab, under the tab strip."""

    currentChanged = pyqtSignal(int)
    tabCloseRequested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.strip = QWidget()
        layout = QHBoxLayout(self.strip)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(2)
        self._bar = DocumentTabBar(self.strip)
        layout.addWidget(self._bar)
        self.add_tab_button = QToolButton(self.strip)
        self.add_tab_button.setToolTip("New tab")
        self.add_tab_button.setFixedSize(24, 24)
        self.add_tab_button.setIconSize(QSize(18, 18))
        self.add_tab_button.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self.add_tab_button)
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
        return index

    def removeTab(self, index):
        page = self.widget(index)
        if page is not None:
            self.removeWidget(page)
        self._bar.removeTab(index)

    def setCurrentIndex(self, index):
        self._bar.setCurrentIndex(index)

    def setCurrentWidget(self, page):
        self.setCurrentIndex(self.indexOf(page))

    def tabText(self, index):
        return self._bar.tabText(index)

    def setTabText(self, index, text):
        self._bar.setTabText(index, text)

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

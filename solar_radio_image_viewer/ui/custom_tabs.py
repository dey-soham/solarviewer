"""
Main-window tab bar and tab widget with the add/close buttons.
"""

from PyQt5.QtCore import QSize, QTimer, Qt
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QTabBar, QTabWidget, QToolButton
from .icons import themed_icon
from .styles import theme_manager


class CustomTabBar(QTabBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Create the add tab button
        self.add_tab_button = QToolButton(self)
        self.add_tab_button.setIcon(QIcon(themed_icon("add_tab_default.png")))
        self.add_tab_button.setToolTip("Add new tab")
        self.add_tab_button.setFixedSize(32, 32)
        self.add_tab_button.setIconSize(QSize(32, 32))
        self.add_tab_button.setStyleSheet("""
            QToolButton {
                background-color: transparent;
                border: none;
                border-radius: 4px;
                margin: 2px 0px 2px 0px;
                padding: 0px;
            }
            */QToolButton:hover {
                background-color: #2D2D2D;
            }*/
            QToolButton:pressed {
                background-color: #3D3D3D;
            }
            """)

        # Connect hover events for the add button
        self.add_tab_button.enterEvent = self._handle_add_button_hover_enter
        self.add_tab_button.leaveEvent = self._handle_add_button_hover_leave

        # Enable expanding tabs to fill available space
        self.setExpanding(True)

        # Make sure the add button is visible and on top
        self.add_tab_button.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.add_tab_button.show()
        self.add_tab_button.raise_()

        # Initialize button position
        QTimer.singleShot(0, self.moveAddButton)

        # Apply current theme immediately (for correct startup colors)
        self.refresh_theme()

    # Add mouseDoubleClickEvent to handle tab editing
    def mouseDoubleClickEvent(self, event):
        """Handle double-click on a tab to edit its text using a dialog"""
        index = self.tabAt(event.pos())
        if index >= 0:
            current_text = self.tabText(index)

            # Use a modal dialog instead of in-place editing to avoid crashes
            from PyQt5.QtWidgets import QInputDialog

            new_text, ok = QInputDialog.getText(
                self, "Edit Tab Name", "Enter new tab name:", text=current_text
            )

            if ok and new_text:
                self.setTabText(index, new_text)

        super().mouseDoubleClickEvent(event)

    def _handle_add_button_hover_enter(self, event):
        self.add_tab_button.setIcon(QIcon(themed_icon("add_tab_hover.png")))

    def _handle_add_button_hover_leave(self, event):
        self.add_tab_button.setIcon(QIcon(themed_icon("add_tab_default.png")))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.moveAddButton()

    def tabLayoutChange(self):
        super().tabLayoutChange()
        self.moveAddButton()

    def moveAddButton(self):
        """Position the add button at the extreme right of the tab bar"""
        button_x = self.width() - self.add_tab_button.width() - 2
        button_y = (self.height() - self.add_tab_button.height()) // 2
        self.add_tab_button.move(button_x, button_y)
        self.add_tab_button.show()  # Ensure button is visible
        self.add_tab_button.raise_()  # Ensure button is on top

    def sizeHint(self):
        """Return a size that accounts for the add button at the right"""
        size = super().sizeHint()
        # Add extra space for the add button
        size.setWidth(
            size.width() + self.add_tab_button.width() + 720
        )  # 20px extra padding (increased from 10px)
        return size

    def tabSizeHint(self, index):
        """Calculate the size for each tab to distribute space evenly"""
        width = (
            self.width() - self.add_tab_button.width() - 40
        )  # Reserve more space for add button (20px instead of 10px)
        if self.count() > 0:
            tab_width = width // self.count()
            # Ensure minimum tab width with enough space for text
            return QSize(max(tab_width, 120), super().tabSizeHint(index).height())
        return super().tabSizeHint(index)

    def setTabText(self, index, text):
        """Override setTabText to ensure text is properly elided if too long"""
        # Call the parent implementation
        super().setTabText(index, text)

        # Get the current tab size
        tab_rect = self.tabRect(index)

        # Calculate available width for text (accounting for close button and padding)
        available_width = tab_rect.width() - 10  # 40px for close button and padding

        # If text is too long, elide it
        if self.fontMetrics().horizontalAdvance(text) > available_width:
            elided_text = self.fontMetrics().elidedText(
                text, Qt.ElideRight, available_width
            )
            # Call parent implementation again with elided text
            super().setTabText(index, elided_text)

    def refresh_theme(self):
        """Update tab bar styling and icons for current theme."""
        palette = theme_manager.palette
        is_dark = theme_manager.is_dark

        # Theme-aware colors
        if is_dark:
            tab_selected_bg = "#383838"
            tab_unselected_bg = "#252525"
            tab_hover_bg = "#404040"
            tab_border = "#484848"
            tab_border_unsel = "#353535"
            button_pressed = "#3D3D3D"
            tab_text_color = "#ffffff"
        else:
            tab_selected_bg = palette.get("surface", "#f5f5f5")
            tab_unselected_bg = palette.get("window", "#e8e8e8")
            tab_hover_bg = palette.get("button_hover", "#d0d0d0")
            tab_border = palette.get("border", "#b0b0b0")
            tab_border_unsel = palette.get("border", "#c0c0c0")
            button_pressed = palette.get("button_pressed", "#c0c0c0")
            tab_text_color = palette.get("text", "#1a1a1a")

        # Update add button icon - themed_icon handles light/dark switching
        self.add_tab_button.setIcon(QIcon(themed_icon("add_tab_default.png")))

        # Update add button styling
        self.add_tab_button.setStyleSheet(f"""
            QToolButton {{
                background-color: transparent;
                border: none;
                border-radius: 4px;
            }}
            QToolButton:pressed {{
                background-color: {button_pressed};
            }}
        """)

        # Update tab bar styling with theme colors
        close_default = themed_icon("close_tab_default.png")
        close_hover = themed_icon("close_tab_hover.png")

        self.setStyleSheet(f"""
            QTabBar::tab {{
                padding: 4px 12px 4px 8px;
                margin: 0px 0px 0px 0px;
                border-top-left-radius: 0px;
                border-top-right-radius: 0px;
                text-align: left;
                border-top: none;
                color: {tab_text_color};
            }}
            QTabBar::tab:selected {{
                background: {tab_selected_bg};
                border: 1px solid {tab_border};
                border-top: none;
            }}
            QTabBar::tab:!selected {{
                background: {tab_unselected_bg};
                border: 1px solid {tab_border_unsel};
                border-top: none;
            }}
            QTabBar::tab:hover {{
                background: {tab_hover_bg};
            }}
            QTabBar::close-button {{
                image: url("{close_default}");
                subcontrol-position: left;
                subcontrol-origin: margin;
                margin-left: 4px;
                width: 32px;
                height: 32px;
            }}
            QTabBar::close-button:hover {{
                image: url("{close_hover}");
            }}
        """)


class CustomTabWidget(QTabWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        # Use our custom tab bar
        self.setTabBar(CustomTabBar())

        # Get reference to the add button from our custom tab bar
        self.add_tab_button = self.tabBar().add_tab_button

        # Set the tab widget to use the entire available width
        self.setUsesScrollButtons(False)
        self.setElideMode(Qt.ElideRight)

        # Ensure the tab bar is visible
        self.tabBar().setVisible(True)

        # Tab widget pane styling is now handled by the main theme stylesheet

        # Make sure the add button is properly initialized
        QTimer.singleShot(100, self.ensureAddButtonVisible)

    def _handle_add_button_hover_enter(self, event):
        self.add_tab_button.setIcon(QIcon(themed_icon("add_tab_hover.png")))

    def _handle_add_button_hover_leave(self, event):
        self.add_tab_button.setIcon(QIcon(themed_icon("add_tab_default.png")))

    def resizeEvent(self, event):
        """Handle resize events to ensure tab bar is properly updated"""
        super().resizeEvent(event)
        # Force tab bar to update its layout
        self.tabBar().tabLayoutChange()
        # Ensure add button is visible after resize
        self.ensureAddButtonVisible()

    def ensureAddButtonVisible(self):
        """Make sure the add button is visible and on top"""
        if hasattr(self, "add_tab_button") and self.add_tab_button:
            self.add_tab_button.show()
            self.add_tab_button.raise_()

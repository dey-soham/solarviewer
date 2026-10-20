"""
Pill-style segmented toggle (e.g. CASA Image / FITS File).
"""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QPushButton, QWidget
from .styles import theme_manager


class SegmentedControl(QWidget):
    """A modern segmented control widget."""

    selectionChanged = pyqtSignal(int)  # Emits index of selected segment

    def __init__(self, options, parent=None):
        super().__init__(parent)
        self.options = options
        self._selected_index = 0
        self._buttons = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Create container for the pill shape
        self._container = QWidget()
        container_layout = QHBoxLayout(self._container)
        container_layout.setContentsMargins(2, 2, 2, 2)
        container_layout.setSpacing(1)

        for i, option in enumerate(options):
            btn = QPushButton(option)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked, idx=i: self._on_button_clicked(idx))
            self._buttons.append(btn)
            container_layout.addWidget(btn)

        # Select first by default
        if self._buttons:
            self._buttons[0].setChecked(True)

        layout.addWidget(self._container)
        self._apply_styles()

        # Register for theme changes
        theme_manager.register_callback(self._on_theme_changed)

    def _on_theme_changed(self, theme):
        """Update styling when theme changes."""
        self._apply_styles()

    def _apply_styles(self):
        """Apply button-style toggle styling matching app palette."""
        from .styles import theme_manager

        palette = theme_manager.palette
        is_dark = theme_manager.is_dark

        # Container styling - subtle pill background
        if is_dark:
            surface = palette.get("surface", "#16162a")
            border_style = "none"
            hover_bg = palette.get("button_hover", "#32325d")
        else:
            surface = "rgba(0, 0, 0, 0.03)"
            border_color = palette.get("border", "#d1d5db")
            border_style = f"1px solid {border_color}"
            hover_bg = "rgba(0, 0, 0, 0.06)"

        self._container.setStyleSheet(f"""
            QWidget {{
                background-color: {surface};
                border: {border_style};
                border-radius: 8px;
            }}
        """)

        # Button styling - matches app button palette
        text_color = palette.get("text", "#f0f0f5")
        text_secondary = palette.get("text_secondary", "#a0a0b0")
        highlight = palette.get("highlight", "#6366f1")
        highlight_hover = palette.get("highlight_hover", "#818cf8")

        for btn in self._buttons:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    color: {text_secondary};
                    border: none;
                    border-radius: 5px;
                    padding: 4px 10px;
                    font-weight: 500;
                    font-size: 10pt;
                    min-width: 65px;
                    min-height: 22px;
                }}
                QPushButton:hover {{
                    color: {text_color};
                    background-color: {hover_bg};
                }}
                QPushButton:checked {{
                    background-color: {highlight};
                    color: #ffffff;
                    font-weight: 600;
                }}
                QPushButton:checked:hover {{
                    background-color: {highlight_hover};
                }}
            """)

    def _on_button_clicked(self, index):
        """Handle button click - ensure only one is selected."""
        if index == self._selected_index:
            # Re-check if clicking already selected (don't allow deselect)
            self._buttons[index].setChecked(True)
            return

        # Uncheck all others
        for i, btn in enumerate(self._buttons):
            btn.setChecked(i == index)

        self._selected_index = index
        self.selectionChanged.emit(index)

    def selectedIndex(self):
        """Return the currently selected index."""
        return self._selected_index

    def setSelectedIndex(self, index):
        """Set the selected index programmatically."""
        if 0 <= index < len(self._buttons):
            self._on_button_clicked(index)

    def selectedText(self):
        """Return the text of the selected option."""
        return self.options[self._selected_index]

    def updateTheme(self):
        """Update styling when theme changes."""
        self._apply_styles()

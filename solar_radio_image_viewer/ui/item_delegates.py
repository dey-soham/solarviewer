"""
Item delegates for combo boxes and lists.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import QStyledItemDelegate
from .styles import theme_manager


class DisabledItemDelegate(QStyledItemDelegate):
    """Custom delegate that properly renders disabled items with grayed text."""

    def paint(self, painter, option, index):
        # Check if item is disabled
        if not (index.flags() & Qt.ItemIsEnabled):
            # Get disabled color from theme
            try:
                disabled_color = QColor(
                    theme_manager.palette.get("disabled", "#cccccc")
                )
            except:
                disabled_color = QColor("#cccccc")

            # Modify the palette to use disabled color for text
            option.palette.setColor(QPalette.Text, disabled_color)
            option.palette.setColor(QPalette.HighlightedText, disabled_color)

        super().paint(painter, option, index)

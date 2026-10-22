"""
Widgets of the solar events viewer: clickable thumbnails, the full-size
image viewer, collapsible sections and the events table.
"""

import logging
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QScrollArea,
    QSizePolicy,
    QDialog,
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPixmap
from ..ui.styles import theme_manager, set_hand_cursor

from .workers import ImageLoader, ImageUrlResolver
logger = logging.getLogger(__name__)


class ClickableLabel(QLabel):
    """QLabel that emits a clicked signal."""

    clicked = pyqtSignal()

    def mouseReleaseEvent(self, event):
        self.clicked.emit()


class FullImageViewer(QDialog):
    """Dialog to view high-resolution image."""

    def __init__(self, parent, title, page_url):
        super().__init__(parent)
        self.setWindowTitle(f"{title} - High Resolution")
        self.setWindowFlags(
            Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint
        )
        self.resize(1920, 1080)
        self.page_url = page_url
        set_hand_cursor(self)

        layout = QVBoxLayout(self)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(
            True
        )  # Start resizable, maybe set False when huge image loads?
        self.scroll.setStyleSheet("background-color: #222;")

        self.img_label = QLabel("Resolving high-resolution image URL...")
        self.img_label.setAlignment(Qt.AlignCenter)
        self.img_label.setStyleSheet("color: #ccc; font-weight: bold;")

        self.scroll.setWidget(self.img_label)
        layout.addWidget(self.scroll)

        # Close btn
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        btn_layout.addWidget(close)
        layout.addLayout(btn_layout)

        # Start Resolve
        self.resolve_url()

    def resolve_url(self):
        self.resolver = ImageUrlResolver(self.page_url)
        self.resolver.found.connect(self.on_url_found)
        self.resolver.start()

    def closeEvent(self, event):
        """Clean up threads when dialog is closed."""
        # Stop resolver thread if running
        if hasattr(self, "resolver") and self.resolver is not None:
            if self.resolver.isRunning():
                self.resolver.quit()
                self.resolver.wait(1000)
        # Stop downloader thread if running
        if hasattr(self, "downloader") and self.downloader is not None:
            if self.downloader.isRunning():
                self.downloader.quit()
                self.downloader.wait(1000)
        super().closeEvent(event)

    def on_url_found(self, full_url):
        try:
            if not self.isVisible() and not self.parent():
                return

            if not full_url:
                self.img_label.setText("Failed to resolve high-res image.")
                return

            self.img_label.setText("Loading... Please wait")

            # Download
            self.downloader = ImageLoader(full_url)
            self.downloader.loaded.connect(self.on_image_loaded)
            self.downloader.error.connect(self.on_image_error)
            self.downloader.start()
        except RuntimeError:
            pass

    def on_image_error(self, error_msg):
        """Handle image download error safely."""
        try:
            if self.isVisible():
                self.img_label.setText(f"Error: {error_msg}")
        except RuntimeError:
            pass  # Widget was deleted

    def on_image_loaded(self, data):
        try:
            if not self.isVisible():
                return

            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                self.img_label.setPixmap(pixmap)
                self.img_label.adjustSize()
                # If huge, maybe enable scrollbars
                self.scroll.setWidgetResizable(
                    True
                )
                # To scroll, widgetResizable is complicated.
                # If we want scroll, setWidgetResizable(False) implies widget dictates size.
                if (
                    pixmap.width() > self.scroll.width()
                    or pixmap.height() > self.scroll.height()
                ):
                    self.scroll.setWidgetResizable(False)  # Let label be big
                else:
                    self.scroll.setWidgetResizable(True)  # Center it
            else:
                self.img_label.setText("Failed to load image data.")
        except RuntimeError:
            pass


class CollapsibleSection(QWidget):
    """A collapsible section widget with header and content."""

    toggled = pyqtSignal(bool)

    def __init__(self, title: str, icon: str = "", count: int = 0, parent=None):
        super().__init__(parent)
        self.is_collapsed = False

        # Allow expanding vertically
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        self.header = QPushButton()

        # Theme-aware styling
        palette = theme_manager.palette
        is_dark = theme_manager.is_dark

        if is_dark:
            bg_normal = "rgba(128, 128, 128, 0.12)"
            bg_hover = "rgba(128, 128, 128, 0.18)"
            bg_pressed = "rgba(128, 128, 128, 0.1)"
            border = "none"
            text_color = palette["text"]
        else:
            # Light theme: Use distinct solid colors
            bg_normal = palette["button"]  # Distinct from window background
            bg_hover = palette["button_hover"]
            bg_pressed = palette["button_pressed"]
            border = f"1px solid {palette['border']}"
            text_color = palette["text"]

        self.header.setStyleSheet(
            f"""
            QPushButton {{
                text-align: left;
                padding: 8px 14px;
                font-weight: 600;
                border: {border};
                border-radius: 6px;
                background-color: {bg_normal};
                color: {text_color};
            }}
            QPushButton:hover {{
                background-color: {bg_hover};
                border-color: {palette['highlight']};
            }}
            QPushButton:pressed {{
                background-color: {bg_pressed};
            }}
        """
        )
        self.update_header(title, icon, count)
        self.header.clicked.connect(self.toggle)
        layout.addWidget(self.header)

        # Content container
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 5, 0, 5)
        layout.addWidget(self.content)

        self.title = title
        self.icon = icon

    def update_header(self, title: str, icon: str = "", count: int = 0):
        arrow = "▼" if not self.is_collapsed else "▶"
        count_str = f" [{count}]" if count > 0 else ""
        self.header.setText(f"{arrow}  {icon} {title} {count_str}")

    def toggle(self):
        self.is_collapsed = not self.is_collapsed
        self.content.setVisible(not self.is_collapsed)
        self.update_header(self.title, self.icon, getattr(self, "_count", 0))

        # Update size policy based on state
        if self.is_collapsed:
            self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        else:
            self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        self.toggled.emit(self.is_collapsed)

    def set_count(self, count: int):
        self._count = count
        self.update_header(self.title, self.icon, count)

    def add_widget(self, widget):
        self.content_layout.addWidget(widget)


class EventTable(QTableWidget):
    """Custom table widget for displaying events."""

    def __init__(self, columns: list, parent=None):
        super().__init__(parent)
        self.setColumnCount(len(columns))
        self.setHorizontalHeaderLabels(columns)
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.verticalHeader().setVisible(False)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setSortingEnabled(True)
        self.setShowGrid(False)

        # Allow table to grow
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Better row height
        self.verticalHeader().setDefaultSectionSize(32)

        # Modern table styling handled by global stylesheet
        pass

    def add_event_row(self, values: list, colors: dict = None, tooltips: dict = None):
        """Add a row with optional cell coloring and tooltips."""
        # Temporarily disable sorting to prevent row movement during insertion
        sorting_enabled = self.isSortingEnabled()
        self.setSortingEnabled(False)

        row = self.rowCount()
        self.insertRow(row)
        for col, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setTextAlignment(Qt.AlignCenter)
            if colors and col in colors:
                item.setForeground(QColor(colors[col]))
            if tooltips and col in tooltips:
                item.setToolTip(str(tooltips[col]))
            self.setItem(row, col, item)

        # Re-enable sorting
        self.setSortingEnabled(sorting_enabled)

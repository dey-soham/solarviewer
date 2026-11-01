"""
Widgets of the solar events viewer: clickable thumbnails, the full-size
image viewer, collapsible sections and the events table.
"""

import logging
from contextlib import contextmanager
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QScrollArea,
    QSizePolicy,
    QDialog,
)
from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QPixmap, QTransform
from ..ui.activity import ActivityLabel
from ..ui.download_parts import abandon
from ..ui.icons import themed_icon
from ..ui.styles import CONTROL_HEIGHT, SPACE_L, SPACE_S, set_hand_cursor, theme_manager, themed
from ..ui.widgets import button_bar, dialog_layout, empty_note

from .workers import ImageLoader, ImageUrlResolver
logger = logging.getLogger(__name__)


from ..ui.geometry import fit_to_screen

TONE_ROLE = Qt.UserRole + 1  # the palette key of a table cell's text


def source_note(html):
    """A footer naming the data's sources. Qt fixes the colour of a link when
    it parses the text, so the text is set again on a theme change."""
    label = QLabel()
    label.setObjectName("Hint")
    label.setOpenExternalLinks(True)
    themed(label, lambda palette: label.setText(html.replace("<a ", f"<a style='color:{palette['link']}' ")))
    return label


def set_cell_tone(item, tone):
    """Colour a table cell by a palette key, or ``None`` for the plain colour;
    ``follow_cell_tones`` keeps it through a theme change."""
    item.setData(TONE_ROLE, tone)
    item.setData(Qt.ForegroundRole, QColor(theme_manager.palette[tone]) if tone else None)


def follow_cell_tones(table):
    def style(palette):
        for row in range(table.rowCount()):
            for col in range(table.columnCount()):
                item = table.item(row, col)
                if item is not None and item.data(TONE_ROLE):
                    item.setData(Qt.ForegroundRole, QColor(palette[item.data(TONE_ROLE)]))

    themed(table, style)


def megabytes_text(done, total):
    """How much of a download has come, ``total`` 0 if that is not known."""
    if total:
        return f"{done / 1e6:.1f} of {total / 1e6:.1f} MB"
    return f"{done / 1e6:.1f} MB"


class ClickableLabel(QLabel):
    """QLabel that emits a clicked signal. While its image is on the way, it
    shows a spinner in place of the image."""

    clicked = pyqtSignal()

    def __init__(self, *args):
        super().__init__(*args)
        self.loading = None

    def mouseReleaseEvent(self, event):
        self.clicked.emit()

    def start_loading(self, text):
        self.clear()
        if self.loading is None:
            self.loading = ActivityLabel(parent=self)
            self.loading.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.loading.start(text)
        self.loading.show()
        self._centre_loading()

    def stop_loading(self):
        if self.loading is not None:
            self.loading.stop()
            self.loading.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._centre_loading()

    def _centre_loading(self):
        if self.loading is not None and self.loading.isVisibleTo(self):
            self.loading.adjustSize()
            self.loading.move(self.rect().center() - self.loading.rect().center())


class FullImageViewer(QDialog):
    """Dialog to view high-resolution image."""

    def __init__(self, parent, title, page_url):
        super().__init__(parent)
        self.setWindowTitle(f"{title}: high resolution")
        self.setWindowFlags(
            Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint
        )
        fit_to_screen(self, 1920, 1080)
        self.page_url = page_url
        set_hand_cursor(self)

        layout = dialog_layout(self)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(
            True
        )  # Start resizable, maybe set False when huge image loads?
        self.scroll.setObjectName("ImageWell")

        self.img_label = ClickableLabel()
        self.img_label.setAlignment(Qt.AlignCenter)
        self.img_label.start_loading("Finding the high-resolution image…")

        self.scroll.setWidget(self.img_label)
        layout.addWidget(self.scroll)

        buttons, _ = button_bar(secondary=("Close", self.accept))
        layout.addWidget(buttons)

        # Close, the window's X and Escape all end here
        self.finished.connect(self._leave_workers)
        self.downloader = None

        # Start Resolve
        self.resolve_url()

    def resolve_url(self):
        self.resolver = ImageUrlResolver(self.page_url)
        self.resolver.found.connect(self.on_url_found)
        self.resolver.start()

    def _leave_workers(self):
        """The image is no longer wanted: nobody waits for what is under way."""
        for worker in (self.resolver, self.downloader):
            if worker is not None:
                abandon(worker)

    def on_url_found(self, full_url):
        try:
            if not self.isVisible() and not self.parent():
                return

            if not full_url:
                self.img_label.stop_loading()
                self.img_label.setText("Could not find the high-resolution image.")
                return

            self.img_label.start_loading("Downloading the image…")

            # Download
            self.downloader = ImageLoader(full_url)
            self.downloader.loaded.connect(self.on_image_loaded)
            self.downloader.error.connect(self.on_image_error)
            self.downloader.received.connect(self.on_image_received)
            self.downloader.start()
        except RuntimeError:
            pass

    def on_image_received(self, done, total):
        self.img_label.start_loading(f"Downloading the image… {megabytes_text(done, total)}")

    def on_image_error(self, error_msg):
        """Handle image download error safely."""
        try:
            if self.isVisible():
                self.img_label.stop_loading()
                self.img_label.setText(f"Error: {error_msg}")
        except RuntimeError:
            pass  # Widget was deleted

    def on_image_loaded(self, data):
        try:
            if not self.isVisible():
                return

            self.img_label.stop_loading()
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
                self.img_label.setText("The image could not be read.")
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

        self.header.setObjectName("SectionHeader")
        self.header.setIconSize(QSize(12, 12))
        self.update_header(title, icon, count)
        themed(self.header, lambda palette: self._show_chevron())
        self.header.clicked.connect(self.toggle)
        layout.addWidget(self.header)

        # Content container
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, SPACE_S, 0, 0)
        layout.addWidget(self.content)

        self.title = title
        self.icon = icon

    def update_header(self, title: str, icon: str = "", count: int = 0):
        count_str = f" · {count}" if count > 0 else ""
        self.header.setText(f"{icon} {title}{count_str}".strip())

    def _show_chevron(self):
        chevron = QPixmap(themed_icon("spinbox_down.png"))
        if self.is_collapsed:
            chevron = chevron.transformed(QTransform().rotate(-90), Qt.SmoothTransformation)
        self.header.setIcon(QIcon(chevron))

    def toggle(self):
        self.is_collapsed = not self.is_collapsed
        self.content.setVisible(not self.is_collapsed)
        self.update_header(self.title, self.icon, getattr(self, "_count", 0))
        self._show_chevron()

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


def _sort_key(text):
    """Numbers (45%, 120°, 1.5E-02, 90 min) in order of size, before any text."""
    try:
        return (0, float(text.removesuffix(" min").rstrip("%°")), "")
    except ValueError:
        return (1, 0.0, text)


class _EventItem(QTableWidgetItem):
    def __lt__(self, other):
        return _sort_key(self.text()) < _sort_key(other.text())


class EventTable(QTableWidget):
    """Custom table widget for displaying events."""

    def __init__(self, columns: list, parent=None, empty_text=""):
        super().__init__(parent)
        self.setColumnCount(len(columns))
        self.setHorizontalHeaderLabels(columns)
        self.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self._filling = False
        self.verticalHeader().setVisible(False)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        # Rows stay in the order they were added until a header is clicked
        self.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
        self.setSortingEnabled(True)
        self.setShowGrid(False)

        # Allow table to grow
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.verticalHeader().setDefaultSectionSize(CONTROL_HEIGHT)

        # What the table says while it has no rows
        self.empty_note = empty_note(empty_text, self.viewport())
        self.model().rowsInserted.connect(self._show_empty_note)
        self.model().rowsRemoved.connect(self._show_empty_note)

        follow_cell_tones(self)

    def set_empty_text(self, text):
        self.empty_note.setText(text)

    def _show_empty_note(self):
        self.empty_note.setVisible(self.rowCount() == 0)

    def fit_columns(self):
        """Each column as wide as what it holds, and what is left of the width
        shared out, so a scroll bar only shows when they do not fit."""
        self.resizeColumnsToContents()
        header = self.horizontalHeader()
        spare = self.viewport().width() - header.length()
        if spare > 0:
            columns = self.columnCount()
            for col in range(columns):
                extra = spare // columns + (spare % columns if col == columns - 1 else 0)
                header.resizeSection(col, header.sectionSize(col) + extra)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.empty_note.setGeometry(self.viewport().rect().adjusted(SPACE_L, 0, -SPACE_L, 0))
        self.fit_columns()

    @contextmanager
    def filling(self):
        """For adding many rows: each one costs a sort and a resize of every
        column otherwise, which grows with the rows already there."""
        sorting = self.isSortingEnabled()
        self.setSortingEnabled(False)
        self.setUpdatesEnabled(False)
        self._filling = True
        try:
            yield
        finally:
            self._filling = False
            self.fit_columns()
            self.setSortingEnabled(sorting)
            self.setUpdatesEnabled(True)

    def add_event_row(self, values: list, tones: dict = None, tooltips: dict = None):
        """Add a row with optional cell tones (palette keys) and tooltips."""
        # Temporarily disable sorting to prevent row movement during insertion
        sorting_enabled = self.isSortingEnabled()
        self.setSortingEnabled(False)

        row = self.rowCount()
        self.insertRow(row)
        for col, value in enumerate(values):
            item = _EventItem(str(value))
            if tones and tones.get(col):
                set_cell_tone(item, tones[col])
            if tooltips and col in tooltips:
                item.setToolTip(str(tooltips[col]))
            self.setItem(row, col, item)

        # Re-enable sorting
        self.setSortingEnabled(sorting_enabled)
        if not self._filling:
            self.fit_columns()

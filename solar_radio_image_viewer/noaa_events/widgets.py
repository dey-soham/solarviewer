"""
Widgets of the solar events viewer: clickable thumbnails, the zoomable image
view, the full-size image viewer and the events table.
"""

import logging
from contextlib import contextmanager
from PyQt5.QtWidgets import (
    QGraphicsLineItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QStackedLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyleOption,
    QDialog,
)
from PyQt5.QtCore import QLineF, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QPainter, QPen, QPixmap
from ..ui.activity import ActivityLabel
from ..ui.download_parts import abandon
from ..ui.styles import CONTROL_HEIGHT, SPACE_L, SPACE_M, set_hand_cursor, theme_manager, themed
from ..ui.widgets import button_bar, dialog_layout, empty_note

from .workers import ImageLoader, ImageUrlResolver
logger = logging.getLogger(__name__)


from ..ui.geometry import fit_to_screen

TONE_ROLE = Qt.UserRole + 1  # the palette key of a table cell's text
SORT_ROLE = Qt.UserRole + 2  # what a cell sorts by, when not its text
GIVE_WAY_WIDTH = 120  # the narrowest a column giving way goes


def source_note(html):
    """A footer naming the data's sources. Qt fixes the colour of a link when
    it parses the text, so the text is set again on a theme change."""
    label = QLabel()
    label.setObjectName("Hint")
    label.setOpenExternalLinks(True)
    label.setProperty("html", html)
    themed(label, lambda palette: _show_source_note(label, palette))
    return label


def _show_source_note(label, palette):
    label.setText(label.property("html").replace("<a ", f"<a style='color:{palette['link']}' "))


def set_source_note(label, html):
    """Change what a ``source_note`` names."""
    label.setProperty("html", html)
    _show_source_note(label, theme_manager.palette)


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


class ElidedLabel(QLabel):
    """One line that gives way to what is beside it: cut short with an
    ellipsis, the whole of it in its tooltip. ``text()`` stays whole."""

    def __init__(self, *args):
        super().__init__(*args)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setToolTip(self.text())

    def setText(self, text):
        super().setText(text)
        self.setToolTip(text)

    def paintEvent(self, event):
        painter = QPainter(self)
        option = QStyleOption()
        option.initFrom(self)
        self.style().drawPrimitive(QStyle.PE_Widget, option, painter, self)
        text = self.fontMetrics().elidedText(self.text(), Qt.ElideRight, self.contentsRect().width())
        self.style().drawItemText(
            painter, self.contentsRect(), int(self.alignment()), self.palette(), self.isEnabled(), text,
            self.foregroundRole(),
        )
        painter.end()


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


class ImageZoomView(QGraphicsView):
    """One image on the black well: it opens fitted and stays fitted through
    resizes until it is zoomed. The wheel zooms at the pointer, a drag pans."""

    hovered = pyqtSignal(float, float)  # image pixels
    MAX_SCALE = 16.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ImageWell")
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QBrush(QColor("#000000")))
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setMouseTracking(True)
        self._picture = QGraphicsPixmapItem()
        self._picture.setTransformationMode(Qt.SmoothTransformation)
        self.scene().addItem(self._picture)
        self._marker = self._new_line()
        self._marker_rest = []  # the lines of a figure with more than one time axis
        self._fitted = True
        themed(self, self._style_marker)

    def _new_line(self):
        line = QGraphicsLineItem()
        line.setZValue(1)
        line.hide()
        self.scene().addItem(line)
        return line

    def _lines(self):
        return [self._marker, *self._marker_rest]

    def _style_marker(self, palette):
        pen = QPen(QColor(palette["highlight"]), 1)
        pen.setCosmetic(True)
        for line in self._lines():
            line.setPen(pen)

    def set_image(self, image):
        """Show the QImage ``image`` at Fit."""
        self._picture.setPixmap(QPixmap.fromImage(image))
        self.scene().setSceneRect(self._picture.boundingRect())
        self.set_marks([])
        self.fit()

    def fit(self):
        """The whole image in view, and so it stays through resizes."""
        self._fitted = True
        self.fitInView(self._picture, Qt.KeepAspectRatio)

    def actual_size(self):
        """One image pixel to one screen pixel."""
        self._fitted = False
        self.resetTransform()

    def set_marker(self, image_x):
        """A vertical line at ``image_x``, ``None`` removes it."""
        self.set_marks([] if image_x is None else [(image_x, None, None)])

    def set_marks(self, marks):
        """Vertical lines at ``[(image_x, top row, bottom row)]``, ``None`` rows
        being the image's own edges, and no others."""
        while len(self._lines()) < len(marks):
            self._marker_rest.append(self._new_line())
            self._style_marker(theme_manager.palette)
        height = self._picture.boundingRect().height()
        lines = self._lines()
        for line, (image_x, top, bottom) in zip(lines, marks):
            line.setLine(QLineF(image_x, top or 0, image_x, height if bottom is None else bottom))
            line.show()
        for line in lines[len(marks):]:
            line.hide()
        if marks and not self._fitted:  # zoomed: the view goes along, at its own height
            middle = self.mapToScene(self.viewport().rect().center()).y()
            self.ensureVisible(QRectF(marks[0][0], middle, 1, 1))

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120
        if not steps or self._picture.pixmap().isNull():
            return
        rect = self._picture.boundingRect()
        least = min(self.viewport().width() / rect.width(), self.viewport().height() / rect.height())
        scale = self.transform().m11()
        wanted = min(self.MAX_SCALE, scale * 1.25 ** steps)
        if wanted <= least:
            self.fit()  # nothing outside it left to see
            return
        self._fitted = False
        self.scale(wanted / scale, wanted / scale)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fitted:
            self.fit()

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        where = self.mapToScene(event.pos())
        if self.scene().sceneRect().contains(where):
            self.hovered.emit(where.x(), where.y())


class FullImageViewer(QDialog):
    """Dialog to view high-resolution image. ``save``, if given, is what its
    Save high-res… button calls."""

    def __init__(self, parent, title, page_url, save=None, event_date=None):
        super().__init__(parent)
        self.event_date = event_date  # what a file naming its own date has to agree with
        self.setWindowTitle(f"{title}: high resolution")
        self.setWindowFlags(
            Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint
        )
        fit_to_screen(self, 1920, 1080)
        self.page_url = page_url
        set_hand_cursor(self)

        layout = dialog_layout(self)

        # The spinner and messages until the image is here, then the image
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setObjectName("ImageWell")

        self.img_label = ClickableLabel()
        self.img_label.setAlignment(Qt.AlignCenter)
        self.img_label.start_loading("Finding the high-resolution image…")
        self.scroll.setWidget(self.img_label)

        self.zoom_view = ImageZoomView()
        self.well = QStackedLayout()
        self.well.addWidget(self.scroll)
        self.well.addWidget(self.zoom_view)
        layout.addLayout(self.well, 1)

        extras = [("Save high-res…", save)] if save else []
        buttons, _ = button_bar(secondary=("Close", self.accept), extras=extras)
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
            self.downloader = ImageLoader(full_url, event_date=self.event_date)
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
                self.zoom_view.set_image(pixmap.toImage())
                self.well.setCurrentWidget(self.zoom_view)
            else:
                self.img_label.setText("The image could not be read.")
        except RuntimeError:
            pass


def _sort_key(text):
    """Numbers (45%, 120°, 1.5E-02, 90 min) in order of size, before any text."""
    try:
        return (0, float(text.removesuffix(" min").rstrip("%°")), "")
    except ValueError:
        return (1, 0.0, text)


class _EventItem(QTableWidgetItem):
    def __lt__(self, other):
        mine, theirs = self.data(SORT_ROLE), other.data(SORT_ROLE)
        if mine is not None and theirs is not None:
            return mine < theirs
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
        self.give_way_column = None  # cut short first when the width runs out
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
        self.empty_note.setVisible(all(self.isRowHidden(row) for row in range(self.rowCount())))

    def fit_columns(self):
        """Each column as wide as what it holds, and what is left of the width
        shared out, so a scroll bar only shows when they do not fit."""
        self.resizeColumnsToContents()
        header = self.horizontalHeader()
        if header.length() > self.viewport().width():
            # Most of a header's width is padding for a sort arrow not shown
            for col in range(self.columnCount()):
                label = self.horizontalHeaderItem(col).text()
                header.resizeSection(
                    col, max(self.sizeHintForColumn(col), header.fontMetrics().horizontalAdvance(label) + SPACE_M)
                )
        spare = self.viewport().width() - header.length()
        if spare < 0 and self.give_way_column is not None:
            col = self.give_way_column
            header.resizeSection(col, max(GIVE_WAY_WIDTH, header.sectionSize(col) + spare))
            spare = self.viewport().width() - header.length()
        shown = [col for col in range(self.columnCount()) if not self.isColumnHidden(col)]
        if spare > 0 and shown:
            for col in shown:
                extra = spare // len(shown) + (spare % len(shown) if col == shown[-1] else 0)
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

    def add_event_row(self, values: list, tones: dict = None, tooltips: dict = None, keys: dict = None):
        """Add a row with optional cell tones (palette keys), tooltips and
        sort keys for the cells whose text does not sort as it should."""
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
            if keys and keys.get(col) is not None:
                item.setData(SORT_ROLE, keys[col])
            self.setItem(row, col, item)

        # Re-enable sorting
        self.setSortingEnabled(sorting_enabled)
        if not self._filling:
            self.fit_columns()

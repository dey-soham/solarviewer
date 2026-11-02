"""
The solar events viewer's Context Images tab: images of the Sun from
several instruments, downloaded a few at a time.
"""

import logging
from datetime import datetime, time
from math import ceil
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QMenu,
    QScrollArea,
    QSizePolicy,
)
from PyQt5.QtCore import QEvent, Qt, pyqtSignal
from PyQt5.QtGui import QImage, QPainter, QPixmap
from ..solar_context import context_images as ci
from ..ui.activity import ActivityLabel
from ..ui.download_parts import abandon
from ..ui.errors import show_error
from ..ui.styles import CONTROL_HEIGHT, SPACE_M, SPACE_S, SPACE_XS, set_tone
from ..ui.widgets import empty_note, link_button

from .cache import _noaa_cache
from .widgets import ClickableLabel, ElidedLabel, FullImageViewer, megabytes_text, source_note
from .workers import ImageSaver, StoppableThread, Stopped, ThumbnailLoader, thumbnail_image
logger = logging.getLogger(__name__)

MAX_CONCURRENT = 4
LEAST_TILE = 140  # of an image to make out what it shows
FAR_MINUTES = 60  # an image further than this from the time asked for is flagged
IMAGE_CREDITS = {
    "Helioviewer": 'Data source: <a href="https://helioviewer.org/">Helioviewer.org</a> '
    "(SDO, SOHO, STEREO, GOES imagery)",
    "SolarMonitor": 'Data source: <a href="https://solarmonitor.org/">SolarMonitor.org</a>',
}


def span_text(minutes):
    """A length of time in words: ``76 min``, ``3 h 10 min``."""
    minutes = abs(minutes)
    return f"{minutes} min" if minutes < 120 else f"{minutes // 60} h {minutes % 60} min"


def short_span(minutes):
    """A signed length of time for a tile's one line: ``+76m``, ``−3h10``. Any
    longer takes the end off a title, which tells LASCO C2 from C3."""
    sign = "+" if minutes > 0 else "−"
    minutes = abs(minutes)
    return f"{sign}{minutes}m" if minutes < 120 else f"{sign}{minutes // 60}h{minutes % 60:02d}"


def minutes_off(image, near):
    """How far ``image`` is from ``near`` (a datetime or None), 0 if it has no time."""
    moment = ci.image_moment(image)
    return round((moment - near).total_seconds() / 60) if moment and near else 0


def image_details(image, near=None):
    """What a tile leaves out, a line each."""
    moment = ci.image_moment(image)
    lines = [
        f"Instrument: {image.instrument}",
        image.description,
        f"Credits: {image.credits}",
        f"{moment:%Y-%m-%d %H:%M:%S} UTC" if moment else f"{ci.image_day(image)}",
    ]
    minutes = minutes_off(image, near)
    if minutes:
        lines.append(f"{span_text(minutes)} {'after' if minutes > 0 else 'before'} {near:%H:%M}")
    return lines


def caption_time(image, near=None):
    """A tile's time, and whether it is too far from ``near`` (a datetime) to
    pass for it. SolarMonitor's images are dated by their folder alone."""
    moment = ci.image_moment(image)
    if moment is None:
        return str(ci.image_day(image)), False
    minutes = minutes_off(image, near)
    if not minutes:
        return f"{moment:%H:%M}", False
    return f"{moment:%H:%M} {short_span(minutes)}", abs(minutes) > FAR_MINUTES


def left_out_text(left_out):
    """The line naming the images that are not shown, by the date they have."""
    by_date = {}
    for image in left_out:
        by_date.setdefault(_nearest(image), []).append(image.title)
    return "Not shown: " + " · ".join(f"{', '.join(titles)} ({date})" for date, titles in by_date.items())


def _nearest(image):
    day = ci.image_day(image)
    return f"nearest {day}" if day else "date not known"


def squared(image):
    """``image`` centred on black, so that a square tile never stretches it."""
    if image.width() == image.height():
        return image
    side = max(image.width(), image.height())
    square = QImage(side, side, QImage.Format_RGB32)
    square.fill(Qt.black)
    painter = QPainter(square)
    painter.drawImage((side - image.width()) // 2, (side - image.height()) // 2, image)
    painter.end()
    return square


class ImagesNearTime(StoppableThread):
    """Looks up the day's images nearest to a time of day (a dozen requests)."""

    found = pyqtSignal(object)
    failed = pyqtSignal(str)
    looked_up = pyqtSignal(int, int)  # images so far, of this many

    def __init__(self, day, when):
        super().__init__()
        self.day = day
        self.when = when

    def run(self):
        try:
            images = ci.fetch_context_images(self.day, progress=self._looked_up, when=self.when)
            self.check_stop()
            self.found.emit(images)
        except Stopped:
            pass
        except Exception as e:
            self.failed.emit(str(e))

    def _looked_up(self, done, total):
        self.check_stop()
        self.looked_up.emit(done, total)


class ImageLabel(ClickableLabel):
    """A tile's image, stretched over the tile: ``squared`` keeps it true."""

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setScaledContents(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to view the high-resolution image")

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:  # the right one opens the menu
            super().mouseReleaseEvent(event)


class ImageTile(QWidget):
    """An image and, under it, its title and time on one line: the title
    gives way, and the tooltip has it all."""

    def __init__(self, title, when, far, details):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_XS)

        well = QFrame()
        well.setObjectName("ImageWell")
        well_layout = QVBoxLayout(well)
        well_layout.setContentsMargins(0, 0, 0, 0)
        self.label = ImageLabel()
        well_layout.addWidget(self.label)
        layout.addWidget(well, 1)

        self.caption = QWidget()
        self.caption.setToolTip(details)
        caption_layout = QHBoxLayout(self.caption)
        caption_layout.setContentsMargins(0, 0, 0, 0)
        caption_layout.setSpacing(SPACE_S)
        self.title = ElidedLabel(title)
        self.title.setToolTip(details)
        caption_layout.addWidget(self.title, 1)
        self.time = QLabel(when)
        self.time.setObjectName("DetailMono")
        if far:
            set_tone(self.time, "warning")
        caption_layout.addWidget(self.time)
        layout.addWidget(self.caption)


class TileGrid(QWidget):
    """As many columns of square tiles as fit the width, in ``area``."""

    def __init__(self, area):
        super().__init__()
        self.area = area
        self.tiles = []
        area.installEventFilter(self)  # the fit reads its size, which can change while this one's does not

    def set_tiles(self, tiles):
        for tile in self.tiles:
            tile.hide()
            tile.deleteLater()
        self.tiles = tiles
        for tile in tiles:
            tile.setParent(self)
            tile.show()
        self._place()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Resize:
            self._place()
        return False

    def _fit(self, width, height, caption):
        """The columns and side of the tiles: the largest that show them all at
        once, or, if none do, that show a tile whole."""
        count = len(self.tiles)
        side_of = lambda columns: (width - SPACE_M * (columns - 1)) // columns
        for columns in range(1, count + 1):
            side = side_of(columns)
            rows = (count + columns - 1) // columns
            if side >= LEAST_TILE and rows * (side + SPACE_XS + caption) + (rows - 1) * SPACE_M <= height:
                # (no tile alone on a row, if they can all share one)
                if count == columns + 1 and side_of(count) >= LEAST_TILE:
                    columns = count
                return columns, side_of(columns)
        side = max(LEAST_TILE, height - SPACE_XS - caption)
        columns = max(1, ceil((width + SPACE_M) / (side + SPACE_M)))
        if side_of(columns) < LEAST_TILE:
            columns = max(1, columns - 1)
        return columns, side_of(columns)

    def _place(self):
        if not self.tiles:
            self.setMinimumHeight(0)
            return
        # The bar's room stays set aside whether it shows or not: taller
        # tiles could bring it up, and the narrower ones send it away again
        width = self.area.width() - self.area.verticalScrollBar().sizeHint().width()
        # A title's line: a time's height changes with its glyphs, and no list
        # may fit otherwise than the one before
        caption = self.tiles[0].title.sizeHint().height()
        columns, side = self._fit(width, self.area.height(), caption)
        height = side + SPACE_XS + caption
        for index, tile in enumerate(self.tiles):
            row, column = divmod(index, columns)
            tile.caption.setFixedHeight(caption)
            tile.setGeometry(column * (side + SPACE_M), row * (height + SPACE_M), side, height)
        rows = (len(self.tiles) + columns - 1) // columns
        self.setMinimumHeight(rows * height + (rows - 1) * SPACE_M)


class ContextImagesTabMixin:
    """Builds the Context Images tab and downloads its images.

    Mixed into :class:`NOAAEventsViewer`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``begin_job``, ``day_axis``, ``download_queue``, ``end_job``,
    ``image_downloads``, ``image_savers``, ``image_viewers``, ``say``,
    ``shown_date``, ``tabs``, ``update_job``
    """

    def _create_context_images_tab(self):
        """Context Images tab."""
        self.images_lookup = None  # the look-up of the images at the cursor
        self.day_images = None  # the list fetched for the date, as it came
        self.images_at = {}  # (date, time) -> the list looked up, for the session
        self.images_shown = []  # the images with a tile
        self.images_when = None  # the time they were looked up for, None for noon

        images_tab = QWidget()
        images_layout = QVBoxLayout(images_tab)
        images_layout.setContentsMargins(0, SPACE_M, 0, 0)
        images_layout.setSpacing(SPACE_S)

        # Set to the buttons' height, so that they do not move the tiles
        self.images_header = QWidget()
        self.images_header.setMinimumHeight(CONTROL_HEIGHT)
        header_layout = QHBoxLayout(self.images_header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(SPACE_S)
        self.images_status = ActivityLabel()
        self.images_status.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        header_layout.addWidget(self.images_status)
        self.images_near = QLabel()
        self.images_near.setObjectName("Hint")
        header_layout.addWidget(self.images_near)
        header_layout.addStretch()
        self.images_noon_btn = link_button("", self.display_day_images)
        header_layout.addWidget(self.images_noon_btn)
        self.images_cursor_btn = link_button("", self.show_images_at_cursor)
        header_layout.addWidget(self.images_cursor_btn)
        images_layout.addWidget(self.images_header)

        self.images_empty = empty_note("")
        images_layout.addWidget(self.images_empty, 1)

        self.images_scroll = QScrollArea()
        self.images_scroll.setWidgetResizable(True)
        self.images_scroll.setFrameShape(QFrame.NoFrame)
        self.images_grid = TileGrid(self.images_scroll)
        self.images_scroll.setWidget(self.images_grid)
        images_layout.addWidget(self.images_scroll, 1)

        # One line, whatever a list shows here: more would take height from
        # the tiles and re-tile them
        footer = QHBoxLayout()
        footer.setSpacing(SPACE_M)
        self.images_credits = {name: source_note(html) for name, html in IMAGE_CREDITS.items()}
        for note in self.images_credits.values():
            footer.addWidget(note)
        self.images_note = ElidedLabel()
        self.images_note.setObjectName("Hint")
        self.images_note.hide()
        footer.addWidget(self.images_note, 1)
        footer.addStretch()
        images_layout.addLayout(footer)

        self.tabs.addTab(images_tab, "Context images")
        self.day_axis.cursor_changed.connect(self._show_image_times)
        self.clear_context_images()

    def clear_context_images(self):
        """Nothing fetched for the date: no tiles, nothing left of another date."""
        self.day_images = None
        self._show_tiles([], [], None, "Fetch a date to see its context images")

    def display_context_images(self, images, when=None):
        """Show the images of the date in ``images``, the list fetched for it
        or, with ``when`` (a time of day), the one looked up for that time.
        This is the one way in: only here does an image get a tile."""
        if when is None:
            self.day_images = images
        day = self.shown_date
        kept, left_out = ci.images_of_date(images, day)
        if any(ci.image_day(image) for image in left_out):
            empty = f"No context images from {day}"
        elif left_out:
            empty = f"The dates of the images for {day} could not be confirmed. Check the connection and press Fetch."
        else:
            empty = f"Could not fetch the context images for {day}. Check the connection and press Fetch."
        self._show_tiles(kept, left_out, when, empty)

    def display_fetched_images(self, images):
        """The list fetched for the date, or the one at the cursor if it is
        here: stepping days compares the same clock time."""
        self.day_images = images
        when = self._cursor_time()
        if (self.shown_date, when) in self.images_at:
            self.display_context_images(self.images_at[self.shown_date, when], when)
        else:
            self.display_context_images(images)

    def display_day_images(self):
        self.day_band.setFocus()  # the button goes, and the arrow keys stay on the cursor
        self.display_context_images(self.day_images)

    def _show_tiles(self, images, left_out, when, empty_text):
        self.leave_image_downloads()
        self.images_loaded = self.images_failed = 0
        self.images_total = len(images)
        self.images_shown = images
        self.images_when = when
        self.images_status.stop()
        self.images_status.setVisible(bool(images))
        self.images_note.setText(left_out_text(left_out))
        self.images_note.setVisible(bool(left_out))
        self.images_empty.setText(empty_text)
        self.images_empty.setVisible(not images)
        self.images_scroll.setVisible(bool(images))

        near = datetime.combine(self.shown_date, when) if when else None
        tiles = []
        for img in images:
            tile = ImageTile(img.title, *caption_time(img, near), "\n".join(image_details(img, near)))
            tile.label.clicked.connect(lambda i=img: self.show_high_res_image(i))
            tile.setContextMenuPolicy(Qt.CustomContextMenu)
            tile.customContextMenuRequested.connect(
                lambda pos, t=tile, i=img: self._show_tile_menu(t, pos, i)
            )
            tiles.append(tile)
            # What a thumbnail is cached under: the noon list's have the title alone
            key = f"{img.title} {when:%H%M}" if when else img.title
            # On disk it shows at once: a thread for each would blank the tiles
            cached = _noaa_cache.load_thumbnail(self.shown_date, key)
            image = thumbnail_image(cached.read_bytes()) if cached else None
            if image is not None:
                tile.label.setPixmap(QPixmap.fromImage(squared(image)))
                self.images_loaded += 1
            else:
                tile.label.start_loading("Waiting…")
                self.download_queue.append((img.thumb_url, tile.label, img.page_url, key, self.shown_date))
        self.images_grid.set_tiles(tiles)

        credits = {img.credits for img in images} or {"Helioviewer"}
        for name, note in self.images_credits.items():
            note.setVisible(name in credits)

        self._show_image_times()
        if images:
            self._show_images_progress()
            self._process_download_queue()

    def _show_tile_menu(self, tile, pos, img):
        menu = QMenu(tile)
        menu.addAction("View high resolution", lambda: self.show_high_res_image(img))
        menu.addAction("Save high-res…", lambda: self.save_high_res_image(img))
        menu.exec_(tile.mapToGlobal(pos))

    def _cursor_time(self):
        """The cursor as a time of day, to the minute, or None."""
        if self.day_axis.cursor is None:
            return None
        hours, minutes = divmod(min(round(self.day_axis.cursor * 60), 1439), 60)
        return time(hours, minutes)

    def _show_image_times(self):
        """What the tiles are near, and how to get those of another time."""
        when = self.images_when or ci.NOON
        cursor = self._cursor_time()
        text = ""
        if any(ci.image_moment(image) for image in self.images_shown):
            text = f"near {when:%H:%M} UTC"
            if cursor is not None and cursor != when:
                minutes = (cursor.hour - when.hour) * 60 + cursor.minute - when.minute
                text += f", {span_text(minutes)} {'before' if minutes > 0 else 'after'} the cursor"
        elif self.images_shown:
            text = "SolarMonitor gives the date, not the time"
        self.images_near.setText(text)

        self.images_noon_btn.setText(f"Show images at {ci.NOON:%H:%M}")
        self.images_noon_btn.setVisible(self.images_when is not None)
        # (the noon list has a button of its own)
        offer = self.day_images is not None and cursor is not None and cursor not in (when, ci.NOON)
        self.images_cursor_btn.setVisible(offer)
        if offer:
            self.images_cursor_btn.setText(f"Show images at {cursor:%H:%M}")
            self.images_cursor_btn.setEnabled(self.images_lookup is None)

    def show_images_at_cursor(self):
        """The images nearest the cursor. A dozen requests, so only when asked."""
        when = self._cursor_time()
        day = self.shown_date
        if when is None or self.images_lookup is not None:
            return
        self.day_band.setFocus()  # the button goes, and the arrow keys stay on the cursor
        if (day, when) in self.images_at:
            self.display_context_images(self.images_at[day, when], when)
            return
        label = f"Finding the images at {when:%H:%M}…"
        self.images_lookup = ImagesNearTime(day, when)
        self.images_lookup.looked_up.connect(
            lambda done, total: self.update_job("images_at", f"{label} {done} of {total}")
        )
        self.images_lookup.found.connect(lambda images: self._on_images_found(day, when, images))
        self.images_lookup.failed.connect(self._on_images_lookup_failed)
        self.begin_job("images_at", label, self._leave_images_lookup)
        self.images_lookup.start()
        self._show_image_times()

    def _leave_images_lookup(self):
        """The look-up is over or no longer wanted; a cancel gets here too."""
        lookup, self.images_lookup = self.images_lookup, None
        if lookup is not None:
            abandon(lookup)
            self.end_job("images_at")
            self._show_image_times()

    def _on_images_found(self, day, when, images):
        self._leave_images_lookup()
        if not ci.images_of_date(images, day)[0]:
            # (not kept: this may only be the connection)
            self.say(f"No images found near {when:%H:%M} on {day}. Check the connection and try again.")
            return
        self.images_at[day, when] = images
        self.display_context_images(images, when)

    def _on_images_lookup_failed(self, message):
        self._leave_images_lookup()
        show_error(self, "Could not find the images", "Check the connection and try again.", message)

    def leave_image_downloads(self):
        """Forget the images under way and waiting (the labels they were for
        are going, or the window is)."""
        self._leave_images_lookup()
        for loader in self.image_downloads.values():
            abandon(loader)
        self.image_downloads = {}  # Active ones
        self.download_queue = []  # Waiting ones

    def _show_images_progress(self):
        done = self.images_loaded + self.images_failed
        if done < self.images_total:
            self.images_status.start(f"Loading images… {done} of {self.images_total}")
        elif self.images_failed:
            self.images_status.stop(f"{self.images_loaded} of {self.images_total} images loaded")
        else:
            self.images_status.stop(f"{self.images_total} images loaded")

    def show_high_res_image(self, img_obj):
        """Open dialog to show full resolution image."""
        # Use None as parent to make it an independent window
        viewer = FullImageViewer(
            None, img_obj.title, img_obj.page_url, save=lambda: self.save_high_res_image(img_obj)
        )
        details = QLabel(" · ".join(image_details(img_obj)))
        details.setObjectName("Hint")
        details.setWordWrap(True)
        viewer.layout().insertWidget(0, details)
        viewer.setAttribute(Qt.WA_DeleteOnClose)  # Cleanup on close

        # Keep reference to prevent GC
        self.image_viewers.append(viewer)
        viewer.finished.connect(lambda result, v=viewer: self._cleanup_viewer(v))

        viewer.show()  # Non-blocking

    def _cleanup_viewer(self, viewer):
        """Safely remove viewer reference."""
        try:
            if viewer in self.image_viewers:
                self.image_viewers.remove(viewer)
        except RuntimeError:
            pass

    def save_high_res_image(self, img_obj):
        """Save high resolution image as PNG."""
        from PyQt5.QtWidgets import QFileDialog

        # Ask user for save location (PNG only)
        stamp = f"{self.shown_date:%Y%m%d}"
        moment = ci.image_moment(img_obj)
        if self.images_when is not None and moment is not None:
            stamp = f"{moment:%Y%m%d_%H%M}"  # one image of several from the date
        default_name = f"{img_obj.title.replace(' ', '_')}_{stamp}.png"

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save high-resolution image", default_name, "PNG Image (*.png)"
        )

        if not file_path:
            return  # User cancelled

        # Ensure .png extension
        if not file_path.lower().endswith(".png"):
            file_path += ".png"
        if file_path in self.image_savers:
            return

        # The window stays free meanwhile: this can take a while
        saver = ImageSaver(img_obj.page_url, file_path)
        self.image_savers[file_path] = saver
        label = f"Saving {img_obj.title}…"
        self.begin_job(file_path, label, lambda: self._cancel_save(file_path))
        saver.received.connect(
            lambda done, total: self.update_job(file_path, f"{label} {megabytes_text(done, total)}")
        )
        saver.saved.connect(self._on_image_saved)
        saver.error.connect(lambda message: self._on_save_error(file_path, message))
        saver.start()

    def _leave_saver(self, file_path):
        """Forget the save of ``file_path``; False if it was cancelled. Even a
        saver that has said how it ended is still running, so ``abandon`` it."""
        saver = self.image_savers.pop(file_path, None)
        if saver is None:
            return False
        abandon(saver)
        return True

    def _cancel_save(self, file_path):
        self._leave_saver(file_path)

    def _on_image_saved(self, file_path):
        if not self._leave_saver(file_path):
            return
        self.end_job(file_path)
        self.say(f"Image saved to {file_path}")

    def _on_save_error(self, file_path, message):
        if not self._leave_saver(file_path):
            return
        self.end_job(file_path)
        show_error(self, "Could not save the image", f"The image could not be saved to:\n{file_path}", message)

    def _process_download_queue(self):
        """Start next downloads if under limit."""
        while len(self.image_downloads) < MAX_CONCURRENT and self.download_queue:
            self._start_download(*self.download_queue.pop(0))

    def _start_download(self, url, label, page_url, title, day):
        try:
            label.start_loading("Loading…")
        except RuntimeError:
            return  # the card has gone
        loader = ThumbnailLoader(url, page_url, day, title)  # (a queued one can outlast its date)
        loader.ready.connect(lambda image, l=label: self._on_image_loaded(image, l))
        loader.error.connect(lambda err, l=label: self._on_image_error(err, l))

        # Cleanup and process next on finish
        loader.finished.connect(self._on_download_finished)

        # Keep reference
        self.image_downloads[id(loader)] = loader
        loader.start()

    def _on_download_finished(self):
        """Handle download thread finish (cleanup and next)."""
        try:
            sender = self.sender()
            if sender is not None and self.image_downloads.pop(id(sender), None) is None:
                return  # one left behind by a later fetch

            self._show_images_progress()
            self._process_download_queue()
        except RuntimeError:
            pass

    def _on_image_loaded(self, image, label):
        """Show a card's image."""
        try:
            label.stop_loading()
            if image is not None:
                label.setPixmap(QPixmap.fromImage(squared(image)))
                self.images_loaded += 1
            else:
                label.setText("Could not read this image")
                self.images_failed += 1
        except RuntimeError:
            # Widget deleted, ignore
            pass

    def _on_image_error(self, error_msg, label):
        """Handle download error."""
        self.images_failed += 1
        try:
            label.stop_loading()
            # shorten error message
            short = (
                "Connection error"
                if "101" in error_msg or "Unreachable" in str(error_msg)
                else "Could not load this image"
            )
            label.setText(short)
        except RuntimeError:
            pass

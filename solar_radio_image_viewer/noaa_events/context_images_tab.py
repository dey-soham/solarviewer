"""
The solar events viewer's Context Images tab: images of the Sun from
several instruments, downloaded a few at a time.
"""

import logging
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QScrollArea,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from ..ui.activity import ActivityLabel
from ..ui.download_parts import abandon
from ..ui.errors import show_error
from ..ui.styles import SPACE_M, SPACE_S, set_tone
from ..ui.widgets import empty_note

from .widgets import ClickableLabel, FullImageViewer, megabytes_text, source_note
from .workers import ImageSaver, ThumbnailLoader
logger = logging.getLogger(__name__)

MAX_CONCURRENT = 4
TILE_SIZE = 320  # at most: a tile takes a third of the window on a small screen


class ContextImagesTabMixin:
    """Builds the Context Images tab and downloads its images.

    Mixed into :class:`NOAAEventsViewer`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``begin_job``, ``end_job``, ``image_downloads``, ``image_savers``,
    ``image_viewers``, ``shown_date``, ``tabs``, ``update_job``
    """

    def _create_context_images_tab(self):
        """Context Images tab."""
        # Tab 5: Context Images
        images_tab = QWidget()
        images_layout = QVBoxLayout(images_tab)
        images_layout.setContentsMargins(0, SPACE_M, 0, 0)
        images_layout.setSpacing(SPACE_S)

        self.images_status = ActivityLabel()
        self.images_status.hide()
        images_layout.addWidget(self.images_status)

        images_scroll = QScrollArea()
        images_scroll.setWidgetResizable(True)
        images_scroll.setFrameShape(QFrame.NoFrame)
        images_scroll_content = QWidget()
        self.images_grid = QVBoxLayout(
            images_scroll_content
        )  # Use VBox for list of cards or Grid
        self.images_grid.setSpacing(SPACE_M)
        self.images_grid.setContentsMargins(0, 0, 0, 0)
        self.images_grid.addWidget(empty_note("Fetch a date to see its context images"))

        images_scroll.setWidget(images_scroll_content)
        images_layout.addWidget(images_scroll)

        # Context Images credits label
        images_credits_label = source_note(
            'Data source: <a href="https://helioviewer.org/">Helioviewer.org</a> '
            "(SDO, SOHO, STEREO, GOES imagery)"
        )
        images_layout.addWidget(images_credits_label)

        self.tabs.addTab(images_tab, "Context images")

    def display_context_images(self, images):
        """Display context images."""
        # Clear existing content from the grid layout
        while self.images_grid.count():
            item = self.images_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                pass

        # Cancel any pending downloads and reset queue
        self.leave_image_downloads()
        self.images_loaded = self.images_failed = 0
        self.images_total = len(images or [])
        self.images_status.stop()
        self.images_status.setVisible(bool(images))
        query_date = self.shown_date

        if not images:
            self.images_grid.addWidget(empty_note("Could not fetch the context images for this date"))
            return

        tile = min(TILE_SIZE, max(self.tabs.width() // 3, TILE_SIZE // 2))

        # Create a card for each image
        for img in images:
            card = QFrame()
            card.setObjectName("Card")
            card_layout = QHBoxLayout(card)
            card_layout.setContentsMargins(SPACE_M, SPACE_M, SPACE_M, SPACE_M)
            card_layout.setSpacing(SPACE_M)

            # Image container
            img_container = QFrame()
            img_container.setFixedSize(tile, tile)
            img_container.setObjectName("ImageWell")
            img_container_layout = QVBoxLayout(img_container)
            img_container_layout.setContentsMargins(0, 0, 0, 0)

            # Use ClickableLabel
            img_label = ClickableLabel()
            img_label.setAlignment(Qt.AlignCenter)
            img_label.start_loading("Waiting…")
            img_label.setToolTip("Click to view the high-resolution image")
            img_label.setCursor(Qt.PointingHandCursor)

            # Connect click to viewer
            img_label.clicked.connect(lambda i=img: self.show_high_res_image(i))

            img_container_layout.addWidget(img_label)

            card_layout.addWidget(img_container)

            # Info container
            info_layout = QVBoxLayout()
            info_layout.setSpacing(SPACE_S)
            title = ClickableLabel(img.title)
            title.clicked.connect(lambda i=img: self.show_high_res_image(i))
            title.setCursor(Qt.PointingHandCursor)
            title.setObjectName("SectionTitle")

            instrument_lbl = QLabel(f"Instrument: {img.instrument}")
            instrument_lbl.setObjectName("Hint")

            desc = QLabel(img.description)
            desc.setWordWrap(True)

            # Credits label instead of View Source Page link
            credits_lbl = QLabel(f"Credits: {img.credits}")
            credits_lbl.setObjectName("Hint")

            info_layout.addWidget(title)
            info_layout.addWidget(instrument_lbl)

            if hasattr(img, 'image_date') and img.image_date:
                time_str = img.image_date.strftime("%Y-%m-%d %H:%M:%S UTC")
                date_lbl = QLabel(f"Image time: {time_str}")
                if img.image_date.date() != query_date:
                    set_tone(date_lbl, "warning")
                    date_lbl.setText(f"{date_lbl.text()} (not the date asked for)")
                    date_lbl.setToolTip("This image is from a different date than requested.")
                else:
                    set_tone(date_lbl, "success")
                info_layout.addWidget(date_lbl)

            info_layout.addWidget(desc)
            info_layout.addWidget(credits_lbl)

            # Add save button for high-res image
            save_btn = QPushButton("Save high-res")
            save_btn.clicked.connect(lambda checked, i=img: self.save_high_res_image(i))
            info_layout.addWidget(save_btn, 0, Qt.AlignLeft)
            info_layout.addStretch()

            card_layout.addLayout(info_layout, 1)
            self.images_grid.addWidget(card)

            # Add to download queue instead of starting immediately
            # Include image title for thumbnail caching
            self.download_queue.append((img.thumb_url, img_label, img.page_url, img.title))

        self.images_grid.addStretch()

        # Start processing queue
        self._show_images_progress()
        self._process_download_queue()

    def leave_image_downloads(self):
        """Forget the images under way and waiting (the labels they were for
        are going, or the window is)."""
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
        viewer = FullImageViewer(None, img_obj.title, img_obj.page_url)
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
        default_name = f"{img_obj.title.replace(' ', '_')}_{self.shown_date:%Y%m%d}.png"

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
        from PyQt5.QtWidgets import QMessageBox

        if not self._leave_saver(file_path):
            return
        self.end_job(file_path)
        QMessageBox.information(self, "Image saved", f"Image saved to:\n{file_path}")

    def _on_save_error(self, file_path, message):
        if not self._leave_saver(file_path):
            return
        self.end_job(file_path)
        show_error(self, "Could not save the image", f"The image could not be saved to:\n{file_path}", message)

    def _process_download_queue(self):
        """Start next downloads if under limit."""
        while len(self.image_downloads) < MAX_CONCURRENT and self.download_queue:
            self._start_download(*self.download_queue.pop(0))

    def _start_download(self, url, label, page_url, title):
        try:
            label.start_loading("Loading…")
        except RuntimeError:
            return  # the card has gone
        loader = ThumbnailLoader(url, page_url, self.shown_date, title)
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
                tile = label.parentWidget().maximumWidth()
                if image.width() > tile or image.height() > tile:
                    image = image.scaled(tile, tile, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                label.setPixmap(QPixmap.fromImage(image))
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

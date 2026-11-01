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
from ..ui.styles import set_tone, theme_manager

from .widgets import ClickableLabel, FullImageViewer, megabytes_text
from .workers import ImageSaver, ThumbnailLoader
logger = logging.getLogger(__name__)

MAX_CONCURRENT = 4


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

        self.images_status = ActivityLabel()
        images_layout.addWidget(self.images_status)

        images_scroll = QScrollArea()
        images_scroll.setWidgetResizable(True)
        images_scroll_content = QWidget()
        self.images_grid = QVBoxLayout(
            images_scroll_content
        )  # Use VBox for list of cards or Grid
        self.images_grid.setSpacing(16)
        self.images_grid.setContentsMargins(16, 12, 16, 12)

        images_scroll.setWidget(images_scroll_content)
        images_layout.addWidget(images_scroll)

        # Context Images credits label
        images_credits_label = QLabel(
            'Data Source: <a href="https://helioviewer.org/">Helioviewer.org</a> '
            "(SDO, SOHO, STEREO, GOES imagery)"
        )
        images_credits_label.setOpenExternalLinks(True)
        images_credits_label.setObjectName("Hint")
        images_layout.addWidget(images_credits_label)

        self.tabs.addTab(images_tab, "Context Images")

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
        query_date = self.shown_date

        if not images:
            no_data = QLabel("Failed to retrieve context images for this date.")
            no_data.setAlignment(Qt.AlignCenter)
            self.images_grid.addWidget(no_data)
            return

        palette = theme_manager.palette
        # header = QLabel("Solar Context Imagery (Helioviewer.org / SolarMonitor.org / NASA SDO / SOHO)")
        # header.setStyleSheet(f"color: {palette['text']}; padding: 10px; opacity: 0.4;")
        # self.images_grid.addWidget(header)

        # Create a card for each image
        for img in images:
            card = QFrame()
            # Theme-aware card styling
            card.setObjectName("Card")
            card_layout = QHBoxLayout(card)
            card_layout.setContentsMargins(10, 10, 10, 10)

            # Image container - LARGER thumbnails
            img_container = QFrame()
            img_container.setFixedSize(320, 320)  # Increased from 222
            img_container.setObjectName("ImageWell")
            img_container_layout = QVBoxLayout(img_container)
            img_container_layout.setContentsMargins(0, 0, 0, 0)

            # Use ClickableLabel
            img_label = ClickableLabel()
            img_label.setAlignment(Qt.AlignCenter)
            img_label.start_loading("Waiting…")
            img_label.setToolTip("Click to view High Resolution Image")
            img_label.setCursor(Qt.PointingHandCursor)

            # Connect click to viewer
            img_label.clicked.connect(lambda i=img: self.show_high_res_image(i))

            img_container_layout.addWidget(img_label)

            card_layout.addWidget(img_container)

            # Info container
            info_layout = QVBoxLayout()
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
                date_lbl = QLabel(f"Image Time: {time_str}")
                if img.image_date.date() != query_date:
                    set_tone(date_lbl, "warning")
                    date_lbl.setToolTip("WARNING: This image is from a different date than requested.")
                else:
                    set_tone(date_lbl, "success")
                info_layout.addWidget(date_lbl)

            info_layout.addWidget(desc)
            info_layout.addWidget(credits_lbl)

            # Add save button for high-res image
            save_btn = QPushButton("Save High-Res")
            save_btn.clicked.connect(lambda checked, i=img: self.save_high_res_image(i))
            info_layout.addWidget(save_btn)
            info_layout.addStretch()

            card_layout.addLayout(info_layout)
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
            self, "Save High Resolution Image", default_name, "PNG Image (*.png)"
        )

        if not file_path:
            return  # User cancelled

        # Ensure .png extension
        if not file_path.endswith(".png"):
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
        QMessageBox.information(self, "Success", f"Image saved to:\n{file_path}")

    def _on_save_error(self, file_path, message):
        from PyQt5.QtWidgets import QMessageBox

        if not self._leave_saver(file_path):
            return
        self.end_job(file_path)
        QMessageBox.critical(self, "Error", f"Failed to save image:\n{message}")

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
                label.setPixmap(QPixmap.fromImage(image))
                self.images_loaded += 1
            else:
                label.setText("Format Error")
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
                "Connection Error"
                if "101" in error_msg or "Unreachable" in str(error_msg)
                else "Error"
            )
            label.setText(short)
        except RuntimeError:
            pass

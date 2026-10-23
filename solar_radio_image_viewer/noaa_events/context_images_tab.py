"""
The solar events viewer's Context Images tab: images of the Sun from
several instruments, downloaded a few at a time.
"""

import logging
from datetime import date
from PyQt5.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QScrollArea,
)
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QPixmap
from ..ui.styles import set_tone, theme_manager

from .cache import _noaa_cache
from .widgets import ClickableLabel, FullImageViewer
from .workers import ImageLoader
logger = logging.getLogger(__name__)


class ContextImagesTabMixin:
    """Builds the Context Images tab and downloads its images.

    Mixed into :class:`NOAAEventsViewer`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``date_edit``, ``image_viewers``, ``tabs``
    """

    def _create_context_images_tab(self):
        """Context Images tab."""
        # Tab 5: Context Images
        images_tab = QWidget()
        images_layout = QVBoxLayout(images_tab)

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
        self.image_downloads = {}  # Active ones
        self.download_queue = []  # Waiting ones
        self.active_downloads = 0

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
            img_label = ClickableLabel("Queued...")
            img_label.setAlignment(Qt.AlignCenter)
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
                qdate = self.date_edit.date()
                query_date = date(qdate.year(), qdate.month(), qdate.day())
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
        self._process_download_queue()

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
        from PyQt5.QtWidgets import QFileDialog, QMessageBox, QProgressDialog
        from PyQt5.QtCore import Qt
        import requests

        # Ask user for save location (PNG only)
        default_name = f"{img_obj.title.replace(' ', '_')}_{self.date_edit.date().toString('yyyyMMdd')}.png"

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save High Resolution Image", default_name, "PNG Image (*.png)"
        )

        if not file_path:
            return  # User cancelled

        # Ensure .png extension
        if not file_path.endswith(".png"):
            file_path += ".png"

        # Show progress dialog
        progress = QProgressDialog(
            "Downloading high resolution image...", "Cancel", 0, 0, self
        )
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        progress.show()
        QApplication.processEvents()

        try:
            # Download high-res image
            response = requests.get(img_obj.page_url, timeout=60)
            response.raise_for_status()

            progress.setLabelText("Saving image...")
            QApplication.processEvents()

            # Save as PNG
            with open(file_path, "wb") as f:
                f.write(response.content)

            progress.close()
            QMessageBox.information(self, "Success", f"Image saved to:\n{file_path}")

        except Exception as e:
            progress.close()
            QMessageBox.critical(self, "Error", f"Failed to save image:\n{str(e)}")

    def _process_download_queue(self):
        """Start next downloads if under limit."""
        MAX_CONCURRENT = 4

        while self.active_downloads < MAX_CONCURRENT and self.download_queue:
            url, label, page_url, title = self.download_queue.pop(0)
            self.active_downloads += 1
            
            # Check cache for thumbnail first
            qdate = self.date_edit.date()
            event_date = date(qdate.year(), qdate.month(), qdate.day())
            cached_path = _noaa_cache.load_thumbnail(event_date, title)
            
            if cached_path:
                # Load from cache
                label.setText("Loading cached...")
                try:
                    pixmap = QPixmap(str(cached_path))
                    if not pixmap.isNull():
                        label.setPixmap(
                            pixmap.scaled(QSize(320, 320), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        )
                        label.setText("")
                        self.active_downloads -= 1
                        continue
                except Exception:
                    pass
            
            label.setText("Loading...")
            self._start_download(url, label, page_url, title)

    def _start_download(self, url, label, page_url, title):
        loader = ImageLoader(url, page_url)
        loader.loaded.connect(lambda data, l=label, t=title: self._on_image_loaded(data, l, t))
        loader.error.connect(lambda err, l=label: self._on_image_error(err, l))

        # Cleanup and process next on finish
        loader.finished.connect(self._on_download_finished)

        # Keep reference
        self.image_downloads[id(loader)] = loader
        loader.start()

    def _on_download_finished(self):
        """Handle download thread finish (cleanup and next)."""
        try:
            # Check validity
            if not self.isVisible() and not self.parent():
                return

            sender = self.sender()
            if sender:
                self.image_downloads.pop(id(sender), None)

            self.active_downloads -= 1
            if self.active_downloads < 0:
                self.active_downloads = 0

            self._process_download_queue()
        except RuntimeError:
            pass

    def _on_image_loaded(self, data, label, title=""):
        """Handle image download completion and cache thumbnail."""
        try:
            # Check if label is still valid (not deleted c++ object)
            if not label:
                return

            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                label.setPixmap(
                    pixmap.scaled(
                        QSize(320, 320), Qt.KeepAspectRatio, Qt.SmoothTransformation
                    )
                )
                label.setText("")
                
                # Save thumbnail to cache
                if title:
                    try:
                        qdate = self.date_edit.date()
                        event_date = date(qdate.year(), qdate.month(), qdate.day())
                        _noaa_cache.save_thumbnail(event_date, title, data)
                    except Exception as e:
                        print(f"Failed to cache thumbnail: {e}")
            else:
                label.setText("Format Error")
        except RuntimeError:
            # Widget deleted, ignore
            pass

    def _on_image_error(self, error_msg, label):
        """Handle download error."""
        try:
            if not label:
                return

            # shorten error message
            short = (
                "Connection Error"
                if "101" in error_msg or "Unreachable" in str(error_msg)
                else "Error"
            )
            label.setText(f"{short}\nRetrying..." if "101" in error_msg else f"{short}")
            if "101" in error_msg:
                # Maybe retry? For now just show error.
                pass
        except RuntimeError:
            pass

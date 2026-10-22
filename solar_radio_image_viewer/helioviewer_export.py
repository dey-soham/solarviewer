"""
Saving from the Helioviewer browser: the current frame, the animation as
GIF or MP4 (with timestamps and difference images), and all frames.
"""

import logging
from PyQt5.QtWidgets import (
    QFileDialog,
    QMessageBox,
)
from PyQt5.QtCore import (
    Qt,
)
from PyQt5.QtGui import QPixmap, QImage, QColor
import os

logger = logging.getLogger(__name__)


class HelioviewerExportMixin:
    """Saves frames and animations from the browser.

    Mixed into :class:`HelioviewerBrowser`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``_calculate_difference``, ``current_frame``, ``diff_type``, ``frames``,
    ``get_selected_instruments``, ``speed_combo``, ``timestamps``
    """

    # Export/Save functions
    def save_current_frame(self):
        """Save current frame as PNG."""
        if not self.frames or self.current_frame >= len(self.timestamps):
            QMessageBox.warning(self, "No Frame", "No frame to save.")
            return

        # Get selected instrument
        selected = self.get_selected_instruments()
        if not selected:
            QMessageBox.warning(self, "No Instrument", "Please select an instrument.")
            return

        nickname, _, _, _ = selected[0]
        timestamp = self.timestamps[self.current_frame]

        # Get pixmap for current frame
        frame_data = self.frames.get(self.current_frame, {})
        pixmap = frame_data.get(nickname)

        if not pixmap:
            QMessageBox.warning(self, "No Image", "Current frame image not loaded yet.")
            return

        # Create default filename
        safe_name = nickname.replace("/", "_").replace(" ", "_")
        default_name = (
            f"helioviewer_{safe_name}_{timestamp.toString('yyyyMMdd_HHmmss')}.png"
        )

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Frame", default_name, "PNG Image (*.png)"
        )

        if file_path:
            try:
                if pixmap.save(file_path, "PNG"):
                    QMessageBox.information(
                        self, "Save Successful", f"Frame saved to:\n{file_path}"
                    )
                else:
                    QMessageBox.warning(
                        self,
                        "Save Failed",
                        "Failed to save image. Please check file path and permissions.",
                    )
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Error saving frame:\n{str(e)}")

    def export_animation(self):
        """Export animation as GIF or MP4."""
        if len(self.frames) < 2:
            QMessageBox.warning(
                self, "Insufficient Frames", "Need at least 2 frames for animation."
            )
            return

        # Get selected instrument
        selected = self.get_selected_instruments()
        if not selected:
            QMessageBox.warning(self, "No Instrument", "Please select an instrument.")
            return

        nickname, _, _, _ = selected[0]

        # Ask for format
        from PyQt5.QtWidgets import QInputDialog, QProgressDialog

        formats = ["GIF", "MP4"]
        format_choice, ok = QInputDialog.getItem(
            self, "Export Format", "Choose export format:", formats, 0, False
        )

        if not ok:
            return

        # Ask about timestamp overlay
        timestamp_dialog = QMessageBox(self)
        timestamp_dialog.setWindowTitle("Timestamp Overlay")
        timestamp_dialog.setText("Include timestamp on each frame?")
        timestamp_dialog.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        timestamp_dialog.setDefaultButton(QMessageBox.Yes)
        include_timestamp = timestamp_dialog.exec_() == QMessageBox.Yes

        # Get save path
        safe_name = nickname.replace("/", "_").replace(" ", "_")
        start_time = self.timestamps[0].toString("yyyyMMdd_HHmmss")
        ext = "gif" if format_choice == "GIF" else "mp4"
        default_name = f"helioviewer_{safe_name}_{start_time}.{ext}"

        file_filter = (
            "GIF Image (*.gif)" if format_choice == "GIF" else "MP4 Video (*.mp4)"
        )
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Animation", default_name, file_filter
        )

        if not file_path:
            return

        # Collect frames
        frames_to_export = []
        for idx in sorted(self.frames.keys()):
            frame_data = self.frames[idx]
            pixmap = frame_data.get(nickname)
            if pixmap:
                frames_to_export.append(pixmap)

        if len(frames_to_export) < 2:
            QMessageBox.warning(
                self,
                "Insufficient Frames",
                f"Only {len(frames_to_export)} frame(s) loaded for {nickname}. Need at least 2.",
            )
            return

        # Show progress dialog
        progress = QProgressDialog(
            "Exporting animation...", "Cancel", 0, len(frames_to_export), self
        )
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)

        # Get animation speed from UI
        speed_text = self.speed_combo.currentText()
        speed = float(speed_text.replace("x", ""))
        # Calculate frame duration in ms (base: 2 fps at 1x speed = 500ms)
        frame_duration_ms = int(500 / speed)

        try:
            if format_choice == "GIF":
                self._export_gif(
                    frames_to_export,
                    file_path,
                    progress,
                    frame_duration_ms,
                    include_timestamp,
                )
            else:
                # For MP4, convert to FPS
                fps = 1000.0 / frame_duration_ms
                self._export_mp4(
                    frames_to_export, file_path, progress, fps, include_timestamp
                )

            if not progress.wasCanceled():
                QMessageBox.information(
                    self,
                    "Export Successful",
                    f"Animation exported to:\n{file_path}\n\n"
                    f"Frames: {len(frames_to_export)}",
                )
        except Exception as e:
            progress.close()
            QMessageBox.critical(
                self, "Export Failed", f"Error exporting animation:\n{str(e)}"
            )
        finally:
            progress.close()

    def _add_timestamp_to_pixmap(self, pixmap, timestamp_text, show_bg=True):
        """Add timestamp overlay to a pixmap with adaptive sizing."""
        from PyQt5.QtGui import QPainter, QFont, QColor, QPen
        from PyQt5.QtCore import Qt, QRect

        # Create a copy to avoid modifying original
        result = QPixmap(pixmap)
        painter = QPainter(result)
        
        # Enable anti-aliasing for smoother text and shapes
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)

        # Set up font based on image height
        # Scale font size with height, but keep it within readable range (6 to 22)
        font_size = max(6, min(22, int(result.height() * 0.030)))
        font = QFont("Arial", font_size, QFont.Bold)
        painter.setFont(font)

        # Calculate metrics with the scaled font
        metrics = painter.fontMetrics()
        text_rect = metrics.boundingRect(timestamp_text)

        # Scale padding and margins with font size
        padding_h = max(4, font_size)
        padding_v = max(2, font_size // 2)
        margin = max(4, font_size // 2)

        # Define background rectangle
        bg_width = text_rect.width() + padding_h * 2
        bg_height = text_rect.height() + padding_v * 2

        bg_rect = QRect(
            margin,
            result.height() - bg_height - margin,
            bg_width,
            bg_height,
        )

        # Semi-transparent black background
        if show_bg:
            painter.fillRect(bg_rect, QColor(0, 0, 0, 100))

        # Draw white text
        painter.setPen(QPen(QColor(255, 255, 255)))
        painter.drawText(
            bg_rect.adjusted(padding_h, padding_v, -padding_h, -padding_v),
            Qt.AlignLeft | Qt.AlignVCenter,
            timestamp_text,
        )

        painter.end()
        return result

    def _export_gif(
        self, frames, file_path, progress, duration_ms, include_timestamp=False
    ):
        """Export frames as animated GIF."""
        try:
            from PIL import Image
        except ImportError:
            raise Exception(
                "PIL/Pillow is required for GIF export. Install with: pip install Pillow"
            )

        # Convert QPixmaps to PIL Images
        pil_images = []
        for i, pixmap in enumerate(frames):
            if progress.wasCanceled():
                return
            progress.setValue(i)

            # Apply difference map if mode is on
            if self.diff_type != "None":
                if i > 0:
                    ref_idx = 0 if self.diff_type == "Base Diff" else i - 1
                    ref_pixmap = frames[ref_idx]
                    pixmap = self._calculate_difference(pixmap, ref_pixmap)
                else:
                    # Neutral Gray for first frame
                    gray_pixmap = QPixmap(pixmap.size())
                    gray_pixmap.fill(QColor(128, 128, 128))
                    pixmap = gray_pixmap

            # Add timestamp if requested
            if include_timestamp and i < len(self.timestamps):
                timestamp_text = (
                    self.timestamps[i].toString("yyyy-MM-dd HH:mm:ss") + " UTC"
                )
                # Remove background in diff mode to avoid artifacts
                pixmap = self._add_timestamp_to_pixmap(
                    pixmap, timestamp_text, show_bg=(self.diff_type == "None")
                )

            # Convert QPixmap to PIL Image via QImage
            qimage = pixmap.toImage()
            qimage = qimage.convertToFormat(QImage.Format_RGB888)

            width = qimage.width()
            height = qimage.height()
            ptr = qimage.bits()
            ptr.setsize(qimage.byteCount())

            import numpy as np

            # Handle scanline stride (bytesPerLine) to avoid skewing
            bytes_per_line = qimage.bytesPerLine()
            arr = np.frombuffer(ptr, np.uint8).reshape((height, bytes_per_line))
            # Slice out the padding bytes at the end of each scanline
            arr = arr[:, : width * 3].reshape((height, width, 3))

            pil_img = Image.fromarray(arr, "RGB")
            pil_images.append(pil_img)

        # Save as animated GIF with user-selected speed
        progress.setLabelText("Saving GIF...")
        pil_images[0].save(
            file_path,
            save_all=True,
            append_images=pil_images[1:],
            duration=duration_ms,
            loop=0,
        )
        progress.setValue(len(frames))

    def _export_mp4(self, frames, file_path, progress, fps, include_timestamp=False):
        """Export frames as an H.264 MP4 video (with imageio's ffmpeg writer,
        as Create Video does)."""
        import imageio
        import numpy as np

        out = imageio.get_writer(
            file_path,
            format="FFMPEG",
            fps=fps,
            codec="libx264",
            quality=None,
            pixelformat="yuv420p",
            macro_block_size=1,
            output_params=["-crf", "18"],
        )

        try:
            for i, pixmap in enumerate(frames):
                if progress.wasCanceled():
                    return
                progress.setValue(i)

                # Apply difference map if mode is on
                if self.diff_type != "None":
                    if i > 0:
                        ref_idx = 0 if self.diff_type == "Base Diff" else i - 1
                        ref_pixmap = frames[ref_idx]
                        pixmap = self._calculate_difference(pixmap, ref_pixmap)
                    else:
                        # Neutral Gray for first frame
                        gray_pixmap = QPixmap(pixmap.size())
                        gray_pixmap.fill(QColor(128, 128, 128))
                        pixmap = gray_pixmap

                # Add timestamp if requested
                if include_timestamp and i < len(self.timestamps):
                    timestamp_text = (
                        self.timestamps[i].toString("yyyy-MM-dd HH:mm:ss") + " UTC"
                    )
                    # Remove background in diff mode to avoid artifacts
                    pixmap = self._add_timestamp_to_pixmap(
                        pixmap, timestamp_text, show_bg=(self.diff_type == "None")
                    )

                # Convert QPixmap to numpy array
                qimage = pixmap.toImage()
                qimage = qimage.convertToFormat(QImage.Format_RGB888)

                width = qimage.width()
                height = qimage.height()
                ptr = qimage.bits()
                ptr.setsize(qimage.byteCount())

                # Handle scanline stride (bytesPerLine) to avoid skewing
                bytes_per_line = qimage.bytesPerLine()
                arr = np.frombuffer(ptr, np.uint8).reshape((height, bytes_per_line))
                # Slice out the padding bytes at the end of each scanline
                arr = arr[:, : width * 3].reshape((height, width, 3))

                # H.264 (yuv420p) needs even sizes
                out.append_data(arr[: height - height % 2, : width - width % 2])

            progress.setValue(len(frames))
        finally:
            out.close()

    def batch_download(self):
        """Batch download all frames."""
        if not self.frames:
            QMessageBox.warning(self, "No Data", "No frames to download.")
            return

        # Get selected instrument
        selected = self.get_selected_instruments()
        if not selected:
            QMessageBox.warning(self, "No Instrument", "Please select an instrument.")
            return

        nickname, _, _, _ = selected[0]

        dir_path = QFileDialog.getExistingDirectory(self, "Select Download Directory")
        if not dir_path:
            return

        # Create subdirectory with timestamp
        from datetime import datetime

        safe_name = nickname.replace("/", "_").replace(" ", "_")
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        batch_dir = os.path.join(dir_path, f"helioviewer_{safe_name}_{timestamp_str}")

        try:
            os.makedirs(batch_dir, exist_ok=True)
        except Exception as e:
            QMessageBox.critical(
                self, "Error", f"Failed to create directory:\n{str(e)}"
            )
            return

        # Show progress
        from PyQt5.QtWidgets import QProgressDialog

        progress = QProgressDialog(
            "Downloading frames...", "Cancel", 0, len(self.frames), self
        )
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)

        saved_count = 0
        metadata_lines = []
        metadata_lines.append("Helioviewer Batch Download")
        metadata_lines.append(f"Instrument: {nickname}")
        metadata_lines.append(
            f"Download Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        metadata_lines.append(f"Total Frames: {len(self.frames)}")
        metadata_lines.append("")
        metadata_lines.append("Frame Index | Timestamp | Filename | Status")
        metadata_lines.append("-" * 80)

        try:
            for idx in sorted(self.frames.keys()):
                if progress.wasCanceled():
                    break

                progress.setValue(saved_count)

                frame_data = self.frames[idx]
                pixmap = frame_data.get(nickname)

                if pixmap:
                    timestamp = self.timestamps[idx]
                    filename = (
                        f"frame_{idx:04d}_{timestamp.toString('yyyyMMdd_HHmmss')}.png"
                    )
                    file_path = os.path.join(batch_dir, filename)

                    if pixmap.save(file_path, "PNG"):
                        saved_count += 1
                        status = "OK"
                    else:
                        status = "FAILED"
                else:
                    timestamp = (
                        self.timestamps[idx]
                        if idx < len(self.timestamps)
                        else "Unknown"
                    )
                    filename = f"frame_{idx:04d}_missing.png"
                    status = "MISSING"

                metadata_lines.append(
                    f"{idx:4d} | {timestamp.toString('yyyy-MM-dd HH:mm:ss') if hasattr(timestamp, 'toString') else timestamp} | {filename} | {status}"
                )

            # Save metadata file
            metadata_path = os.path.join(batch_dir, "_metadata.txt")
            with open(metadata_path, "w") as f:
                f.write("\n".join(metadata_lines))

            progress.setValue(len(self.frames))

            if not progress.wasCanceled():
                QMessageBox.information(
                    self,
                    "Batch Download Complete",
                    f"Successfully saved {saved_count}/{len(self.frames)} frames to:\n{batch_dir}\n\n"
                    f"Metadata saved to: _metadata.txt",
                )
        except Exception as e:
            progress.close()
            QMessageBox.critical(
                self, "Error", f"Error during batch download:\n{str(e)}"
            )
        finally:
            progress.close()

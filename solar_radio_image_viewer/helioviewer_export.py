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
    QRect,
)
from PyQt5.QtGui import QImage, QColor, QPainter, QFont, QPen
import os

from .ui.busy import busy
from .ui.errors import show_error
from .video.ui.progress import run_with_dialog

logger = logging.getLogger(__name__)


class _Stop(Exception):
    """The export was cancelled."""


# Exports run on a thread, where a QPixmap cannot go: these work on QImages


def _rgb_array(image):
    """``image`` as an array of rows of RGB pixels."""
    import numpy as np

    image = image.convertToFormat(QImage.Format_RGB888)
    width = image.width()
    height = image.height()
    ptr = image.bits()
    ptr.setsize(image.byteCount())
    # Handle scanline stride (bytesPerLine) to avoid skewing
    arr = np.frombuffer(ptr, np.uint8).reshape((height, image.bytesPerLine()))
    # Slice out the padding bytes at the end of each scanline
    return arr[:, : width * 3].reshape((height, width, 3)).copy()


def difference_image(current, reference):
    """Pixel-wise grayscale difference of two QImages, or None if their sizes
    differ."""
    import numpy as np

    # Basic size check (should match as per API usage)
    if current.size() != reference.size():
        return None
    height, width = current.height(), current.width()

    # Difference math: (Current - Reference)
    # We add 128 to show "no change" as mid-gray
    diff = _rgb_array(current).astype(np.int16) - _rgb_array(reference).astype(np.int16)

    # Enhance contrast for visibility: Multiply by 2.5 (increased for grayscale)
    diff = (diff * 2.5 + 128).clip(0, 255)

    # Convert to Grayscale (Average of channels)
    gray_arr = np.mean(diff, axis=2).astype(np.uint8)

    result = QImage(gray_arr.data, width, height, width, QImage.Format_Grayscale8)
    return result.copy()  # copy() ensures memory safety


def stamped_image(image, timestamp_text, show_bg=True):
    """Copy of ``image`` with a timestamp overlay of adaptive size."""
    result = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
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


def _export_frames(images, stamps, diff_type):
    """Each of ``images`` as the export shows it, an RGB array: with the
    difference map if the mode is on, and its timestamp of ``stamps``, if any."""
    for i, image in enumerate(images):
        if diff_type != "None":
            if i > 0:
                ref_idx = 0 if diff_type == "Base" else i - 1
                difference = difference_image(image, images[ref_idx])
                if difference is not None:
                    image = difference
            else:
                # Neutral Gray for first frame
                gray = QImage(image.size(), QImage.Format_RGB32)
                gray.fill(QColor(128, 128, 128))
                image = gray

        if i < len(stamps) and stamps[i]:
            # Remove background in diff mode to avoid artifacts
            image = stamped_image(image, stamps[i], show_bg=(diff_type == "None"))

        yield _rgb_array(image)


def _partial_path(file_path):
    """Written first, so that a cancelled export leaves any earlier file there."""
    stem, ext = os.path.splitext(file_path)
    return f"{stem}.part{ext}"


def write_gif(images, stamps, diff_type, file_path, duration_ms, progress, cancelled):
    """Write the frames as an animated GIF with the given speed. Raises _Stop
    if ``cancelled()``."""
    try:
        from PIL import Image
    except ImportError:
        raise Exception(
            "PIL/Pillow is required for GIF export. Install with: pip install Pillow"
        )

    total = len(images)
    frames = _export_frames(images, stamps, diff_type)

    # Pillow takes the frames as they come, and quantizes each as it does
    # (the longest part), so these counts are how far it really is
    def later_frames():
        for number, frame in enumerate(frames, 2):
            if cancelled():
                raise _Stop()
            progress(f"Encoding frame {number} of {total}", number, total)
            yield Image.fromarray(frame)
        progress("Writing the GIF file…")

    partial = _partial_path(file_path)
    try:
        Image.fromarray(next(frames)).save(
            partial,
            format="GIF",
            save_all=True,
            append_images=later_frames(),
            duration=duration_ms,
            loop=0,
        )
        if cancelled():
            raise _Stop()
        os.replace(partial, file_path)
    finally:
        if os.path.exists(partial):
            os.remove(partial)


def write_mp4(images, stamps, diff_type, file_path, fps, progress, cancelled):
    """Write the frames as an H.264 MP4 video (with imageio's ffmpeg writer,
    as Create Video does). Raises _Stop if ``cancelled()``."""
    import imageio

    total = len(images)
    progress("Starting the encoder…")
    partial = _partial_path(file_path)
    out = imageio.get_writer(
        partial,
        format="FFMPEG",
        fps=fps,
        codec="libx264",
        quality=None,
        pixelformat="yuv420p",
        macro_block_size=1,
        output_params=["-crf", "18"],
    )
    try:
        try:
            for number, arr in enumerate(_export_frames(images, stamps, diff_type), 1):
                if cancelled():
                    raise _Stop()
                progress(f"Encoding frame {number} of {total}", number, total)
                height, width = arr.shape[:2]
                # H.264 (yuv420p) needs even sizes
                out.append_data(arr[: height - height % 2, : width - width % 2])
            progress("Finishing the video file…")
        finally:
            out.close()
        if cancelled():
            raise _Stop()
        os.replace(partial, file_path)
    finally:
        if os.path.exists(partial):
            os.remove(partial)


def save_frames(frames, metadata_lines, batch_dir, progress, cancelled):
    """Save ``frames`` (index, QImage or None, its time as text, file name)
    as PNG files, and ``_metadata.txt`` with what became of each; the number
    saved. Cancelled, the frames so far stay, and are listed."""
    saved = 0
    total = len(frames)
    for number, (idx, image, when, filename) in enumerate(frames, 1):
        if cancelled():
            break
        if image is None:
            status = "MISSING"
        elif image.save(os.path.join(batch_dir, filename), "PNG"):
            saved += 1
            status = "OK"
        else:
            status = "FAILED"
        metadata_lines.append(f"{idx:4d} | {when} | {filename} | {status}")
        progress(f"Saving frames: {number} of {total}", number, total)

    # Save metadata file
    with open(os.path.join(batch_dir, "_metadata.txt"), "w") as f:
        f.write("\n".join(metadata_lines))
    return saved


class HelioviewerExportMixin:
    """Saves frames and animations from the browser.

    Mixed into :class:`HelioviewerBrowser`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``_close_after_export``, ``current_frame``, ``diff_type``, ``frames``,
    ``get_selected_instruments``, ``speed_combo``, ``timestamps`` and, with
    OVRO-LWA's frames, ``frame_labels``
    """

    # A frame's time. OVRO-LWA's frames have their own (or none proved, in a clip)
    def _label_of(self, idx):
        labels = getattr(self, "frame_labels", [])
        return labels[idx] if idx < len(labels) else ""

    def time_text(self, idx):
        """As the window shows it, with the movie an OVRO-LWA frame is from."""
        when, label = self.timestamps[idx], self._label_of(idx)
        if not when.isValid():
            return f"Time not confirmed · {label}" if label else "Time not known"
        text = f"{when.toString('yyyy-MM-dd HH:mm:ss')} UTC"
        return f"{text} · {label}" if label else text

    def stamp_text(self, idx):
        """As it is printed on an exported frame."""
        when = self.timestamps[idx]
        if when.isValid():
            return when.toString("yyyy-MM-dd HH:mm:ss") + " UTC"
        return self._label_of(idx) or ""

    def name_time(self, idx):
        """As it goes in a file's name."""
        when = self.timestamps[idx]
        return when.toString("yyyyMMdd_HHmmss") if when.isValid() else f"untimed_{idx + 1:04d}"

    def file_stem(self, nickname):
        safe_name = nickname.replace("/", "_").replace(" ", "_")
        return f"ovro_{safe_name}" if getattr(self, "frame_labels", []) else f"helioviewer_{safe_name}"

    # Export/Save functions
    def save_current_frame(self):
        """Save current frame as PNG."""
        if not self.frames or self.current_frame >= len(self.timestamps):
            QMessageBox.warning(self, "No frame", "No frame to save.")
            return

        # Get selected instrument
        selected = self.get_selected_instruments()
        if not selected:
            QMessageBox.warning(self, "No instrument", "Choose an instrument first.")
            return

        nickname, _, _, _ = selected[0]

        # Get pixmap for current frame
        frame_data = self.frames.get(self.current_frame, {})
        pixmap = frame_data.get(nickname)

        if not pixmap:
            QMessageBox.warning(self, "No image", "This frame's image has not loaded yet.")
            return

        # Create default filename
        default_name = f"{self.file_stem(nickname)}_{self.name_time(self.current_frame)}.png"

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save frame", default_name, "PNG Image (*.png)"
        )

        if file_path:
            try:
                with busy(f"Saving {os.path.basename(file_path)}…", self):
                    saved = pixmap.save(file_path, "PNG")
                if saved:
                    QMessageBox.information(
                        self, "Frame saved", f"Frame saved to:\n{file_path}"
                    )
                else:
                    show_error(
                        self,
                        "Could not save the frame",
                        "The image could not be saved. Check the path and its permissions.",
                    )
            except Exception as e:
                show_error(
                    self,
                    "Could not save the frame",
                    "The image could not be saved. Check the path and its permissions.",
                    e,
                )

    def export_animation(self):
        """Export animation as GIF or MP4."""
        if len(self.frames) < 2:
            QMessageBox.warning(
                self, "Too few frames", "An animation needs at least 2 frames."
            )
            return

        # Get selected instrument
        selected = self.get_selected_instruments()
        if not selected:
            QMessageBox.warning(self, "No instrument", "Choose an instrument first.")
            return

        nickname, _, _, _ = selected[0]

        # Ask for format
        from PyQt5.QtWidgets import QInputDialog

        formats = ["GIF", "MP4"]
        format_choice, ok = QInputDialog.getItem(
            self, "Export format", "Format", formats, 0, False
        )

        if not ok:
            return

        # Ask about timestamp overlay
        timestamp_dialog = QMessageBox(self)
        timestamp_dialog.setWindowTitle("Timestamp overlay")
        timestamp_dialog.setText("Include timestamp on each frame?")
        timestamp_dialog.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        timestamp_dialog.setDefaultButton(QMessageBox.Yes)
        include_timestamp = timestamp_dialog.exec_() == QMessageBox.Yes

        # Get save path
        ext = "gif" if format_choice == "GIF" else "mp4"
        default_name = f"{self.file_stem(nickname)}_{self.name_time(0)}.{ext}"

        file_filter = (
            "GIF Image (*.gif)" if format_choice == "GIF" else "MP4 Video (*.mp4)"
        )
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export animation", default_name, file_filter
        )

        if not file_path:
            return
        if not file_path.lower().endswith(f".{ext}"):  # the encoder goes by it
            file_path += f".{ext}"

        # Collect frames, with the time of each: some may be missing
        frames_to_export = []
        frame_stamps = []
        for idx in sorted(self.frames.keys()):
            frame_data = self.frames[idx]
            pixmap = frame_data.get(nickname)
            if pixmap:
                frames_to_export.append(pixmap)
                frame_stamps.append(self.stamp_text(idx))

        if len(frames_to_export) < 2:
            QMessageBox.warning(
                self,
                "Too few frames",
                f"Only {len(frames_to_export)} frame(s) loaded for {nickname}. Need at least 2.",
            )
            return

        # A QImage of each (cheap): the frames stay as they are in the window
        with busy(f"Preparing {len(frames_to_export)} frames…", self):
            images = [pixmap.toImage() for pixmap in frames_to_export]
        stamps = [stamp if include_timestamp else "" for stamp in frame_stamps]

        # Get animation speed from UI
        speed_text = self.speed_combo.currentText()
        speed = float(speed_text.replace("x", ""))
        # Calculate frame duration in ms (base: 2 fps at 1x speed = 500ms)
        frame_duration_ms = int(500 / speed)

        diff_type = self.diff_type

        def work(progress, cancelled):
            if format_choice == "GIF":
                write_gif(
                    images, stamps, diff_type, file_path, frame_duration_ms, progress, cancelled
                )
            else:
                # For MP4, convert to FPS
                fps = 1000.0 / frame_duration_ms
                write_mp4(images, stamps, diff_type, file_path, fps, progress, cancelled)
            return True

        outcome = run_with_dialog(self, "Exporting animation", "Exporting animation…", work)
        if self._close_after_export:
            self.close()
        elif outcome.cancelled and not outcome.result:
            pass  # (nothing was written)
        elif outcome.error:
            show_error(
                self,
                "Could not export the animation",
                "The animation could not be written. Check the path and the free disk space.",
                outcome.error,
            )
        else:
            QMessageBox.information(
                self,
                "Animation exported",
                f"Animation exported to:\n{file_path}\n\n"
                f"Frames: {len(frames_to_export)}",
            )

    def batch_download(self):
        """Batch download all frames."""
        if not self.frames:
            QMessageBox.warning(self, "No frames", "No frames to download.")
            return

        # Get selected instrument
        selected = self.get_selected_instruments()
        if not selected:
            QMessageBox.warning(self, "No instrument", "Choose an instrument first.")
            return

        nickname, _, _, _ = selected[0]

        dir_path = QFileDialog.getExistingDirectory(self, "Choose a folder for the frames")
        if not dir_path:
            return

        # Create subdirectory with timestamp
        from datetime import datetime

        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        batch_dir = os.path.join(dir_path, f"{self.file_stem(nickname)}_{timestamp_str}")

        try:
            os.makedirs(batch_dir, exist_ok=True)
        except Exception as e:
            show_error(
                self, "Could not create the folder", f"The folder could not be created:\n{batch_dir}", e
            )
            return

        metadata_lines = []
        metadata_lines.append(
            "OVRO-LWA frames (from its daily movies)" if getattr(self, "frame_labels", []) else "Helioviewer Batch Download"
        )
        metadata_lines.append(f"Instrument: {nickname}")
        metadata_lines.append(
            f"Download Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        metadata_lines.append(f"Total Frames: {len(self.frames)}")
        metadata_lines.append("")
        metadata_lines.append("Frame Index | Timestamp | Filename | Status")
        metadata_lines.append("-" * 80)

        with busy(f"Preparing {len(self.frames)} frames…", self):
            to_save = []
            for idx in sorted(self.frames.keys()):
                pixmap = self.frames[idx].get(nickname)
                known = idx < len(self.timestamps)
                if pixmap and known:
                    filename = f"frame_{idx:04d}_{self.name_time(idx)}.png"
                else:
                    filename = f"frame_{idx:04d}_missing.png"
                when = self.stamp_text(idx).removesuffix(" UTC") if known else "Unknown"
                if known and self._label_of(idx):
                    when = f"{when} ({self._label_of(idx)})" if self.timestamps[idx].isValid() else when
                to_save.append((idx, pixmap.toImage() if pixmap else None, when, filename))

        outcome = run_with_dialog(
            self,
            "Saving frames",
            "Saving frames…",
            lambda progress, cancelled: save_frames(
                to_save, metadata_lines, batch_dir, progress, cancelled
            ),
        )
        if self._close_after_export:
            self.close()
        elif outcome.error:
            show_error(
                self,
                "Could not save the frames",
                f"Saving the frames to this folder failed:\n{batch_dir}",
                outcome.error,
            )
        elif outcome.cancelled:
            QMessageBox.information(
                self,
                "Batch download cancelled",
                f"Saved {outcome.result} of {len(to_save)} frames to:\n{batch_dir}",
            )
        else:
            QMessageBox.information(
                self,
                "Batch download complete",
                f"Saved {outcome.result} of {len(to_save)} frames to:\n{batch_dir}\n\n"
                f"Metadata saved to: _metadata.txt",
            )

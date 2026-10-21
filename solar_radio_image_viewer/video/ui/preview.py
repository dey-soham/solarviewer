"""
The Create Video window's preview: rendering frames in a worker process,
the frame shown (drag a box on it), and its panel.
"""

import logging
import os
from PyQt5.QtWidgets import (
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
    QSlider,
    QSizePolicy,
)
from PyQt5.QtCore import QRect, QSize, Qt, QObject, pyqtSignal
from PyQt5.QtGui import QIcon, QImage, QPixmap
from collections import OrderedDict
import threading
import numpy as np
import multiprocessing
from ..lightcurve import (
    line_plot_options,
)



logger = logging.getLogger(__name__)

class PreviewRenderer(QObject):
    """Renders preview frames in a background thread, newest request first.

    Frames are made by the video code itself (prepare_video_options,
    process_image), in a worker process like the video's frames, so the
    preview frame is exactly the video frame (this process's matplotlib
    settings follow the app's theme). The shared setup (timeline, global
    range) and recent frames are kept, so stepping through frames and
    replaying them is quick.
    """

    # request id, RGB frame or None, problems, geometry (see creator.process_image)
    rendered = pyqtSignal(int, object, object, object)
    status = pyqtSignal(int, str)  # request id, what is being done

    def __init__(self):
        super().__init__()
        self._condition = threading.Condition()
        self._request = None
        self._stopped = False
        self._prepared = OrderedDict()  # setup key -> (options, global stats)
        # (setup key, frame index) -> (frame, problems, geometry)
        self._frames = OrderedDict()
        self._executor = None  # one worker process, started on first use
        self._converted = {}  # (coordinates, cache folder) -> images converted
        # Images on a server (RemoteFiles): fetched as previewed (the first,
        # middle and last frames, and those shown), or all with render_all
        self.remote = None
        self.render_all = False
        self.all_fetched = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def request(self, request_id, files, index, options):
        """Render frame ``index``; replaces any request not yet started."""
        with self._condition:
            self._request = (request_id, list(files), index, dict(options))
            self._condition.notify()

    def stop(self):
        with self._condition:
            self._stopped = True
            self._condition.notify()
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)

    def _run(self):
        while True:
            with self._condition:
                while self._request is None and not self._stopped:
                    self._condition.wait()
                if self._stopped:
                    return
                request, self._request = self._request, None
            request_id = request[0]
            try:
                frame, problems, geometry = self._render(*request)
            except Exception as e:
                logger.error(f"Preview failed: {e}")
                self.status.emit(request_id, f"Preview failed: {e}")
                frame, problems, geometry = None, [], {}
            self.rendered.emit(request_id, frame, problems, geometry)

    def _render(self, request_id, files, index, options):
        from ...processing.workers import process_pool_executor
        from ..creator import (
            calculate_global_stats,
            prepare_video_options,
            process_image_wrapper,
        )

        if self.remote is not None:
            files, options = self._fetch(request_id, files, index, options)

        setup_key = repr((files, sorted((k, repr(v)) for k, v in options.items())))
        frame_key = (setup_key, index)
        if frame_key in self._frames:
            self._frames.move_to_end(frame_key)
            return self._frames[frame_key]

        if self._executor is None:
            self.status.emit(request_id, "Starting the preview renderer...")
            # Frames are rendered one at a time; reading all of them (range,
            # timeline, steady levels) uses all the processes
            self._workers = max(1, min(4, multiprocessing.cpu_count() - 1))
            self._executor = process_pool_executor(
                self._workers, preload=["solar_radio_image_viewer.video.creator"]
            )
        self._convert(request_id, files, index, options)
        # The workers keep contour images between frames (one video id)
        options = dict(options, video_id=f"preview-{id(self)}")

        prepared = self._prepared.get(setup_key)
        if prepared is None:

            def progress(done, total):
                self.status.emit(request_id, f"Reading frames {done} of {total}...")

            prepared_options = prepare_video_options(
                files, dict(options), self._executor, progress
            )
            prepared = (
                prepared_options,
                calculate_global_stats(files, prepared_options),
            )
            self._prepared[setup_key] = prepared
            while len(self._prepared) > 4:
                self._prepared.popitem(last=False)
        prepared_options, global_stats = prepared

        self.status.emit(request_id, "Rendering...")
        options = prepared_options
        try:
            _, frame, problems, geometry = self._executor.submit(
                process_image_wrapper, (files[index], index, options, global_stats)
            ).result()
        except Exception:
            # A broken worker: start a new one next time
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None
            raise
        self._frames[frame_key] = (frame, problems, geometry)
        while len(self._frames) > 60:
            self._frames.popitem(last=False)
        return frame, problems, geometry

    def _fetch(self, request_id, files, index, options):
        """Fetch from the server what this preview needs (the sample frames
        and frame ``index``, or all of them with render_all); (files,
        options) with the local copies (where not fetched yet: where they
        will be, which can't be read)."""
        from ..remote_video import frame_paths, localize, remote_times, sample_indices

        remote = self.remote
        indices = range(len(files)) if self.render_all else sorted(
            set(sample_indices(len(files))) | {index}
        )
        missing = [p for p in frame_paths(files, options, indices) if not remote.cached(p)]
        if missing:
            def report(done, total, fetched, count):
                self.status.emit(
                    request_id,
                    f"Fetching from {remote.label}: {fetched} of {count} images "
                    f"({done / 1e6:.0f} of {total / 1e6:.0f} MB)...",
                )

            remote.fetch(missing, progress=report)
        self.all_fetched = all(remote.cached(p) for p in frame_paths(files, options, range(len(files))))

        def local_of(path):
            return remote.cached(path) or str(remote.cache.get_cache_path(remote.host, path))

        times = remote_times(remote, files)
        local_files, options = localize(files, options, local_of)
        available = sum(os.path.exists(p) for p in local_files)
        # With more frames fetched, the range and line plot are worked out again
        return local_files, dict(options, frame_times=times, _fetched_frames=available)

    def _convert(self, request_id, files, index, options):
        """Convert the images this preview reads to the video's coordinates,
        and export CASA images to FITS, in the worker (not in the app): the
        first and shown frames, or all of them for a range or timeline over
        all frames."""
        from ...processing.fits_io import is_casa_image
        from ..creator import _convert_one

        target = options.get("coordinates") or "native"

        everything = (
            options.get("range_mode") == 2
            or line_plot_options(options) is not None
            or options.get("contour_level_mode", "frame") == "all"
        )
        indices = range(len(files)) if everything else sorted({0, index})
        difference = options.get("difference_files") or []
        paths = [files[i] for i in indices] + [difference[i] for i in indices if i < len(difference)]
        paths = list(dict.fromkeys(paths))
        if target == "native":
            paths = [p for p in paths if is_casa_image(p)]
        cache_dir = options.get("coordinate_cache")
        todo = [p for p in paths if p not in self._converted.get((target, cache_dir), set())]
        if not todo:
            return
        images = f"{len(todo)} image{'s' if len(todo) > 1 else ''}"
        if target == "native":
            text = f"Reading {images} (CASA)..."
        else:
            name = "helioprojective" if target == "hpc" else "RA/Dec"
            text = f"Converting {images} to {name} coordinates..."
        self.status.emit(request_id, text)
        list(self._executor.map(_convert_one, [(p, target, cache_dir) for p in todo]))
        self._converted.setdefault((target, cache_dir), set()).update(todo)


class PreviewImage(QLabel):
    """The preview frame; dragging a box on it emits ``dragged`` with the
    box in pixels of the frame (left, top, right, bottom)."""

    dragged = pyqtSignal(float, float, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.frame_size = None  # (width, height) of the frame shown
        self._start = None  # (widget point, frame point) where the drag began
        self._box = None  # QRect being dragged, in widget pixels

    def _frame_point(self, pos):
        """Point of the frame at widget position ``pos`` (the frame is drawn
        scaled to fit, centred)."""
        width, height = self.frame_size
        scale = min(self.width() / width, self.height() / height)
        left = (self.width() - width * scale) / 2
        top = (self.height() - height * scale) / 2
        return (pos.x() - left) / scale, (pos.y() - top) / scale

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.frame_size:
            # In frame pixels now: the preview may be resized while dragging
            self._start = (event.pos(), self._frame_point(event.pos()))
            self._box = QRect(event.pos(), QSize())
            self.update()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._start is not None:
            self._box = QRect(self._start[0], event.pos()).normalized()
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._start is not None and event.button() == Qt.LeftButton:
            (start, (x0, y0)), self._start, self._box = self._start, None, None
            self.update()
            if (event.pos() - start).manhattanLength() >= 6:  # not a click
                x1, y1 = self._frame_point(event.pos())
                self.dragged.emit(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._box is not None:
            from PyQt5.QtGui import QColor, QPainter, QPen

            painter = QPainter(self)
            # Orange of the colour-blind safe palette, seen on light and dark
            pen = QPen(QColor("#e69f00"), 2, Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(QColor(230, 159, 0, 40))
            painter.drawRect(self._box)
            painter.end()


def frame_point_to_region(geometry, x, y):
    """Point (x, y) of a rendered frame (pixels from its top left) as image
    pixels in the region's coordinates (those of the region settings: the
    first frame's, when the region follows solar rotation)."""
    left, top, right, bottom = geometry["axes"]
    (x_lo, x_hi), (y_lo, y_hi) = geometry["xlim"], geometry["ylim"]
    data_x = x_lo + (x - left) / (right - left) * (x_hi - x_lo)
    data_y = y_lo + (bottom - y) / (bottom - top) * (y_hi - y_lo)
    origin_x, origin_y = geometry["origin"]
    shift_x, shift_y = geometry["shift"]
    return data_x + origin_x - shift_x, data_y + origin_y - shift_y


def zoom_region(bounds, image_shape, factor=None, size=None):
    """Region (x_min, x_max, y_min, y_max, inclusive) around the middle of
    ``bounds`` (None: the whole image of ``image_shape``, rows and columns),
    ``factor`` times its size or ``size`` (width, height) pixels, kept
    inside the image. None if it would show the whole image."""
    rows, cols = image_shape
    x_min, x_max, y_min, y_max = bounds or (0, cols - 1, 0, rows - 1)
    if size is None:
        size = ((x_max - x_min + 1) * factor, (y_max - y_min + 1) * factor)
    new = []
    for low, high, length, limit in ((x_min, x_max, size[0], cols), (y_min, y_max, size[1], rows)):
        length = int(round(min(max(length, 2), limit)))
        start = int(round((low + high) / 2 - (length - 1) / 2))
        start = max(0, min(start, limit - length))
        new += [start, start + length - 1]
    if new == [0, cols - 1, 0, rows - 1]:
        return None
    return tuple(new)


class PreviewPanel(QWidget):
    """The video frame being previewed, with a frame slider and Play."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 16, 16, 16)
        layout.setSpacing(8)

        header_row = QHBoxLayout()
        header = QLabel("Preview")
        header.setStyleSheet("font-weight: 600;")
        header_row.addWidget(header)
        header_row.addStretch()
        # Zoom buttons as in the main window; they set the video's region
        self.zoom_buttons = {}
        self._zoom_icons = {}
        for name, icon, tip in (
            ("in", "zoom_in.png", "Zoom in: show the middle half of the region"),
            ("out", "zoom_out.png", "Zoom out: show twice the region"),
            ("reset", "reset.png", "Show the whole image (no region)"),
            ("degree", "zoom_60arcmin.png", "1°×1°: show 1°×1° around the middle of the region"),
        ):
            button = QPushButton()
            button.setObjectName("IconOnlyButton")
            self._zoom_icons[name] = icon
            button.setIconSize(QSize(20, 20))
            button.setFixedHeight(28)
            button.setToolTip(tip)
            header_row.addWidget(button)
            self.zoom_buttons[name] = button
        layout.addLayout(header_row)
        self._update_icons()
        from ...ui.styles import theme_manager

        theme_manager.register_callback(self._update_icons)

        self.image_label = PreviewImage()
        self.image_label.setToolTip("Drag a box to show only that part of the image")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumSize(320, 240)
        self.image_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.image_label, 1)

        # One line, cut off if long, so the preview above keeps its size
        self.status_label = QLabel()
        self.status_label.setObjectName("SecondaryText")
        self.status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        layout.addWidget(self.status_label)

        # What could not be drawn on the frame shown
        self.problems_label = QLabel()
        self.problems_label.setWordWrap(True)
        self.problems_label.setStyleSheet("color: #e69f00; font-weight: 600;")
        self.problems_label.hide()
        layout.addWidget(self.problems_label)

        # Images on a server: what the preview shows so far, and all of it
        remote_row = QHBoxLayout()
        self.remote_note = QLabel()
        self.remote_note.setObjectName("SecondaryText")
        self.remote_note.setWordWrap(True)
        remote_row.addWidget(self.remote_note, 1)
        self.render_all_button = QPushButton("Render All Frames")
        self.render_all_button.setToolTip(
            "Fetch every frame's images from the server, so all frames can be\n"
            "previewed and played (they stay cached for the video)"
        )
        remote_row.addWidget(self.render_all_button)
        layout.addLayout(remote_row)
        self.show_remote(False)

        controls = QHBoxLayout()
        self.play_button = QPushButton("▶ Play")
        self.play_button.setCheckable(True)
        self.play_button.setToolTip("Play the preview at the video's frame rate")
        controls.addWidget(self.play_button)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.setEnabled(False)  # until the frames are known
        self.slider.setToolTip("Frame to preview (arrow keys step one frame)")
        controls.addWidget(self.slider, 1)
        self.frame_label = QLabel()
        self.frame_label.setMinimumWidth(110)
        controls.addWidget(self.frame_label)
        layout.addLayout(controls)

        self._pixmap = None
        self.show_message("Choose input files to see the preview")

    def show_remote(self, on, all_fetched=False, fetched=0, total=0):
        """The note (and Render All button) for images on a server."""
        self.remote_note.setVisible(on and not all_fetched)
        self.render_all_button.setVisible(on and not all_fetched)
        self.remote_note.setText(
            f"Preview of {fetched} of {total} frames (the first, middle and last, "
            "and those you go to); the range and line plot use these until all "
            "are rendered. The video uses all frames."
        )

    def show_frame(self, frame):
        frame = np.ascontiguousarray(frame)
        height, width = frame.shape[:2]
        image = QImage(frame.data, width, height, 3 * width, QImage.Format_RGB888)
        self._pixmap = QPixmap.fromImage(image.copy())
        self.image_label.frame_size = (width, height)
        self._update_pixmap()

    def _update_icons(self, *_):
        """Icons for the current (light or dark) theme."""
        from ...ui.icons import themed_icon

        for name, button in self.zoom_buttons.items():
            button.setIcon(QIcon(themed_icon(self._zoom_icons[name])))

    def release(self):
        """Stop following theme changes (the window is closing)."""
        from ...ui.styles import theme_manager

        theme_manager.unregister_callback(self._update_icons)

    def show_problems(self, problems):
        """List what could not be drawn (or hide the list if nothing)."""
        self.problems_label.setText("\n".join(f"⚠ {message}" for message in problems))
        self.problems_label.setVisible(bool(problems))

    def show_message(self, text):
        self._pixmap = None
        self.image_label.frame_size = None
        self.image_label.clear()
        self.image_label.setText(text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_pixmap()

    def _update_pixmap(self):
        if self._pixmap is not None:
            self.image_label.setPixmap(
                self._pixmap.scaled(
                    self.image_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )


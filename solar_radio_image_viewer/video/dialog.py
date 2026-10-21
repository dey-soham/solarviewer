#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QFileDialog,
    QGroupBox,
    QComboBox,
    QCheckBox,
    QSpinBox,
    QDoubleSpinBox,
    QMessageBox,
    QApplication,
    QTabWidget,
    QScrollArea,
    QWidget,
    QProgressDialog,
    QFormLayout,
    QSlider,
    QSplitter,
    QSizePolicy,
    QRadioButton,
    QButtonGroup,
    QListWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QDialogButtonBox,
)
from PyQt5.QtCore import QRect, QSize, Qt, QObject, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QIcon, QImage, QPixmap
from collections import OrderedDict
import logging
import threading
import matplotlib.pyplot as plt
import numpy as np
import glob
import time
import multiprocessing
import psutil
import shutil
from contextlib import contextmanager



OUTPUT_FORMATS = [
    ("MP4 (H.264)", ".mp4"),
    ("WebM (VP9)", ".webm"),
    ("GIF", ".gif"),
    ("AVI", ".avi"),
    ("PNG frames", ".png"),
]

SIZE_PRESETS = [
    ("Auto (1000 px)", 0),
    ("720 px tall", 720),
    ("1080 px tall", 1080),
    ("1440 px tall", 1440),
    ("2160 px tall (4K)", 2160),
]


def _format_duration(seconds):
    """'0.3 s', '45 s', '3 min', '1 h 20 min'."""
    if seconds < 10:
        return f"{seconds:.1f} s"
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60} min"


@contextmanager
def wait_cursor():
    """Context manager to show a wait cursor"""
    QApplication.setOverrideCursor(Qt.WaitCursor)
    try:
        yield
    finally:
        QApplication.restoreOverrideCursor()


from ..processing.colormaps import ensure_solar_colormaps
from .creator import (
    QUALITY_CRF,
    apply_difference,
    observation_sort_key,
)
from ..processing.contour_layers import (
    MAX_CONTOUR_LAYERS,
    contour_layer_name,
    contour_layer_settings,
    default_contour_options,
    new_contour_layer,
)
from .coordinates import COORDINATE_NAMES, COORDINATES
from .lightcurve import (
    IMAGE_AREAS,
    LAYER_AREAS,
    MAX_CURVES,
    QUANTITIES,
    default_line_plot,
    lightcurve_csv_path,
    line_plot_options,
)
from ..processing.fits_io import is_casa_image
from ..processing.plot_style import (
    DEFAULT_BEAM_STYLE,
    DEFAULT_GRID_STYLE,
    DEFAULT_PLOT_SETTINGS,
    DEFAULT_SOLAR_DISK_STYLE,
    default_style,
)


logger = logging.getLogger(__name__)


def is_image(path):
    """Whether ``path`` is an image the video can show: a FITS file (also
    compressed) or a CASA image."""
    from ..processing.fits_io import FITS_SUFFIXES

    return is_casa_image(path) or (
        os.path.isfile(path) and path.lower().endswith(FITS_SUFFIXES)
    )


def viewer_image(main_window):
    """(image file, coordinates) shown in the main window's current tab: the
    original file and "hpc" / "radec" when it shows a converted copy of it
    (its HPC / RA-Dec button), else (its file, "native")."""
    tab_widget = getattr(main_window, "tab_widget", None)
    tab = tab_widget.currentWidget() if tab_widget is not None else None
    name = getattr(tab, "imagename", None)
    if not name:
        return None, "native"
    if name == getattr(tab, "_hpc_temp_file", None) and getattr(
        tab, "_original_imagename", None
    ):
        return tab._original_imagename, "hpc"
    if name == getattr(tab, "_radec_temp_file", None) and getattr(
        tab, "_hpc_original_imagename", None
    ):
        return tab._hpc_original_imagename, "radec"
    return name, "native"


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
        from ..processing.workers import process_pool_executor
        from .creator import (
            calculate_global_stats,
            prepare_video_options,
            process_image_wrapper,
        )

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

    def _convert(self, request_id, files, index, options):
        """Convert the images this preview reads to the video's coordinates,
        and export CASA images to FITS, in the worker (not in the app): the
        first and shown frames, or all of them for a range or timeline over
        all frames."""
        from ..processing.fits_io import is_casa_image
        from .creator import _convert_one

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
        from ..ui.styles import theme_manager

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

    def show_frame(self, frame):
        frame = np.ascontiguousarray(frame)
        height, width = frame.shape[:2]
        image = QImage(frame.data, width, height, 3 * width, QImage.Format_RGB888)
        self._pixmap = QPixmap.fromImage(image.copy())
        self.image_label.frame_size = (width, height)
        self._update_pixmap()

    def _update_icons(self, *_):
        """Icons for the current (light or dark) theme."""
        from ..ui.icons import themed_icon

        for name, button in self.zoom_buttons.items():
            button.setIcon(QIcon(themed_icon(self._zoom_icons[name])))

    def release(self):
        """Stop following theme changes (the window is closing)."""
        from ..ui.styles import theme_manager

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


class VideoCreationDialog(QDialog):
    """
    Dialog for creating videos from FITS files
    """

    def __init__(self, parent=None, current_file=None):
        super().__init__(parent)
        # Force window behavior to ensure maximize button works
        self.setWindowFlags(
            Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint
        )
        self._main_window = parent
        self.current_file = current_file
        self.progress_tracker = None
        self.reference_image = None

        self.setWindowTitle("Create Video")
        self.resize(1320, 800)

        # Preview: rendered in the background, shortly after settings change
        self._preview_files = []
        self._preview_request = 0
        self._preview_shown = 0
        self._preview_renderer = PreviewRenderer()
        self._preview_renderer.rendered.connect(self._on_preview_rendered)
        self._preview_renderer.status.connect(self._on_preview_status)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._request_preview)
        self._play_timer = QTimer(self)
        self._play_timer.timeout.connect(self._play_step)

        # Solar disk, beam and grid styles, and plot appearance (None: the
        # video's own look), as in the main window
        self.solar_disk_style = default_style(DEFAULT_SOLAR_DISK_STYLE)
        self.beam_style = default_style(DEFAULT_BEAM_STYLE)
        self.grid_style = default_style(DEFAULT_GRID_STYLE)
        self.plot_settings = None

        # Images converted to the video's coordinates, while the window is open
        import tempfile

        self._coordinate_cache = tempfile.mkdtemp(prefix="solarviewer-video-")

        from ..ui.styles import set_hand_cursor

        self.setup_ui()
        set_hand_cursor(self)

        # Initialize stretch controls
        self.update_gamma_controls()

        # Initialize range mode controls (for default "Auto per Frame")
        self.toggle_range_mode(self.range_mode_combo.currentIndex())

        # Opened from the main window showing a converted (HPC or RA/Dec)
        # copy of an image: the image itself, in the coordinates shown
        if current_file and parent is not None:
            original, coordinates = viewer_image(parent)
            if original and current_file == getattr(
                parent.tab_widget.currentWidget(), "imagename", None
            ):
                current_file = original
                self.current_file = original
                self.coordinates_combo.setCurrentIndex(
                    self.coordinates_combo.findData(coordinates)
                )

        # Initialize with current file if provided
        if current_file:
            dir_path = os.path.dirname(current_file)
            # If file has no directory (just filename), use CWD
            if not dir_path:
                dir_path = os.getcwd()
            self.input_directory_edit.setText(dir_path)
            file_ext = os.path.splitext(current_file)[1]
            self.input_pattern_edit.setText(f"*{file_ext}")

            # Set reference image to current file (use absolute path)
            abs_file = (
                os.path.join(dir_path, os.path.basename(current_file))
                if not os.path.isabs(current_file)
                else current_file
            )
            self.reference_image = abs_file
            self.reference_image_edit.setText(abs_file)

            # Set default output file
            self.output_file_edit.setText(os.path.join(dir_path, "output_video.mp4"))

            # Start from the current tab's display settings and look
            self._copy_viewer_settings(parent)
            self._copy_viewer_look(parent)

            # Auto-scan for files
            self.preview_input_files()
        else:
            # No file provided - use CWD as default
            cwd = os.getcwd()
            if os.path.isdir(cwd):
                self.input_directory_edit.setText(cwd)
                self.output_file_edit.setText(os.path.join(cwd, "output_video.mp4"))
                # Check if CWD has any FITS files to set pattern
                fits_in_cwd = glob.glob(os.path.join(cwd, "*.fits")) + glob.glob(
                    os.path.join(cwd, "*.fts")
                )
                if fits_in_cwd:
                    self.input_pattern_edit.setText("*.fits")
                    # Auto-scan for files
                    self.preview_input_files()

        # Update preview if we have a valid reference image
        if self.reference_image:
            self.update_preview(self.reference_image)

    def _copy_viewer_settings(self, main_window):
        """Use the colormap, stretch, gamma, Stokes and range of the main
        window's current tab as the starting display settings."""
        tab_widget = getattr(main_window, "tab_widget", None)
        tab = tab_widget.currentWidget() if tab_widget is not None else None
        if tab is None:
            return

        def text(name):
            widget = getattr(tab, name, None)
            try:
                return (
                    widget.currentText()
                    if hasattr(widget, "currentText")
                    else widget.text()
                )
            except Exception:
                return ""

        colormap = text("cmap_combo")
        if colormap:
            if self.colormap_combo.findText(colormap) < 0:
                self.colormap_combo.addItem(colormap)
            self.colormap_combo.setCurrentText(colormap)

        stretch = {"histeq": "Histogram Equalization", "zscale": "ZScale"}.get(
            text("stretch_combo"), text("stretch_combo").capitalize()
        )
        index = self.stretch_combo.findText(stretch)
        if index >= 0:
            self.stretch_combo.setCurrentIndex(index)

        stokes = text("stokes_combo")
        if self.stokes_combo.findText(stokes) >= 0:
            self.stokes_combo.setCurrentText(stokes)

        for name, spinbox in (
            ("gamma_entry", self.gamma_spinbox),
            ("vmin_entry", self.vmin_spinbox),
            ("vmax_entry", self.vmax_spinbox),
        ):
            try:
                spinbox.setValue(float(text(name)))
            except ValueError:
                pass

    # ---------- Preview ----------

    def schedule_preview(self, *_, delay=250):
        """Re-render the preview shortly (settings often change in bursts)."""
        self._preview_timer.start(delay)

    def _request_preview(self):
        options = self._video_options()
        files, problem = self._video_files(options)
        panel = self.preview_panel
        self.create_btn.setEnabled(not problem)
        self.create_btn.setToolTip(problem[1] if problem else "")
        if problem:
            self._preview_files = []
            panel.slider.setEnabled(False)
            panel.frame_label.setText("")
            panel.status_label.setText("")
            panel.show_message(problem[1])
            panel.show_problems([])
            return

        if files != self._preview_files:
            current = (
                self._preview_files[panel.slider.value()]
                if self._preview_files
                else self.reference_image
            )
            self._preview_files = files
            panel.slider.blockSignals(True)
            panel.slider.setRange(0, len(files) - 1)
            panel.slider.setValue(files.index(current) if current in files else 0)
            panel.slider.blockSignals(False)
        panel.slider.setEnabled(len(files) > 1)

        index = panel.slider.value()
        file_path = files[index]
        panel.frame_label.setText(f"{index + 1} / {len(files)}")
        panel.frame_label.setToolTip(file_path)
        # The frame shown is the reference for presets and region selection
        if self.reference_image != file_path:
            self.reference_image = file_path
            self.reference_image_edit.setText(file_path)

        self._preview_request += 1
        self._preview_renderer.request(self._preview_request, files, index, options)

    def _on_preview_status(self, request_id, text):
        if request_id == self._preview_request:
            self.preview_panel.status_label.setText(text)

    def _on_preview_rendered(self, request_id, frame, problems, geometry):
        if request_id != self._preview_request:
            return  # a newer frame is on its way
        self._preview_shown = request_id
        self._preview_geometry = geometry or None
        panel = self.preview_panel
        panel.show_problems(problems)
        if frame is None:
            panel.show_message("Could not render this frame")
            return
        panel.show_frame(frame)
        self._last_preview_frame = frame
        self._update_estimate()
        height, width = frame.shape[:2]
        panel.status_label.setText(
            f"{os.path.basename(self._preview_files[panel.slider.value()])} · "
            f"frame size {width}×{height}"
        )

    # ---------- Region from the preview (drag, zoom buttons) ----------

    def _current_region(self):
        """The region as (x_min, x_max, y_min, y_max), or None (whole image)."""
        if not self.region_enabled.isChecked():
            return None
        x_min, x_max = sorted((self.x_min_spinbox.value(), self.x_max_spinbox.value()))
        y_min, y_max = sorted((self.y_min_spinbox.value(), self.y_max_spinbox.value()))
        return x_min, x_max, y_min, y_max

    def _set_region(self, bounds):
        """Show ``bounds`` (x_min, x_max, y_min, y_max) of the image, or the
        whole image for None."""
        if bounds is None:
            self.region_enabled.setChecked(False)
            self.show_status_message("Whole image")
            return
        for spinbox, value in zip(
            (self.x_min_spinbox, self.x_max_spinbox, self.y_min_spinbox, self.y_max_spinbox),
            bounds,
        ):
            spinbox.setValue(int(value))
        self.region_enabled.setChecked(True)
        x_min, x_max, y_min, y_max = bounds
        self.show_status_message(
            f"Region {x_max - x_min + 1}×{y_max - y_min + 1} px "
            f"(x {x_min}–{x_max}, y {y_min}–{y_max})"
        )

    def _shown_geometry(self):
        """Geometry of the frame in the preview, or None (and a note)."""
        geometry = getattr(self, "_preview_geometry", None)
        if not geometry:
            self.show_status_message("Wait for the preview to choose a region")
        return geometry

    def _on_region_dragged(self, left, top, right, bottom):
        """A box dragged on the preview: show only that part of the image
        (or, after Choose on Preview, the line plot's box)."""
        geometry = self._shown_geometry()
        if not geometry:
            return
        if getattr(self, "_drag_target", "region") == "box":
            self._drag_target = "region"
            x0, y0 = frame_point_to_region(geometry, left, bottom)
            x1, y1 = frame_point_to_region(geometry, right, top)
            rows, cols = geometry["image_shape"]
            xs = sorted(max(0, min(int(round(v)), cols - 1)) for v in (x0, x1))
            ys = sorted(max(0, min(int(round(v)), rows - 1)) for v in (y0, y1))
            if xs[1] - xs[0] >= 1 and ys[1] - ys[0] >= 1:
                self._set_curve_box((xs[0], xs[1], ys[0], ys[1]))
                self.schedule_preview()
            return
        x0, y0 = frame_point_to_region(geometry, left, bottom)
        x1, y1 = frame_point_to_region(geometry, right, top)
        rows, cols = geometry["image_shape"]
        x_min, x_max = (max(0, min(int(round(v)), cols - 1)) for v in sorted((x0, x1)))
        y_min, y_max = (max(0, min(int(round(v)), rows - 1)) for v in sorted((y0, y1)))
        if x_max - x_min < 1 or y_max - y_min < 1:
            self.show_status_message("Drag over the image to choose a region")
            return
        self._set_region((x_min, x_max, y_min, y_max))

    def _zoom(self, factor):
        geometry = self._shown_geometry()
        if geometry:
            self._set_region(
                zoom_region(self._current_region(), geometry["image_shape"], factor=factor)
            )

    def _zoom_one_degree(self):
        geometry = self._shown_geometry()
        if not geometry:
            return
        scale = geometry.get("pixel_scale_deg")
        if not scale:
            self.show_status_message("1°×1° needs images with sky coordinates")
            return
        size = (1.0 / scale[0], 1.0 / scale[1])
        self._set_region(zoom_region(self._current_region(), geometry["image_shape"], size=size))

    def _on_preview_slider(self, _value):
        self.schedule_preview(delay=0)

    def _toggle_play(self, playing):
        panel = self.preview_panel
        panel.play_button.setText("⏸ Pause" if playing else "▶ Play")
        if playing:
            self._play_timer.start(max(40, int(1000 / self.fps_spinbox.value())))
        else:
            self._play_timer.stop()

    def _play_step(self):
        panel = self.preview_panel
        if not self._preview_files or self._preview_shown != self._preview_request:
            return  # wait for the frame being rendered
        panel.slider.setValue((panel.slider.value() + 1) % len(self._preview_files))

    def _watch_settings(self):
        """Re-render the preview when any setting that changes the frames does."""
        for widget in self.tab_widget.findChildren(QWidget):
            if self._output_tab.isAncestorOf(widget):
                continue  # file, frame rate, quality: same frames
            if isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self.schedule_preview)
            elif isinstance(widget, (QCheckBox, QRadioButton)):
                widget.toggled.connect(self.schedule_preview)
            elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                widget.valueChanged.connect(self.schedule_preview)
            elif isinstance(widget, QLineEdit) and not isinstance(
                widget.parent(), (QSpinBox, QDoubleSpinBox, QComboBox)
            ):
                widget.textChanged.connect(self.schedule_preview)

    def _use_current_view(self):
        """Take the display settings, range and visible area of the main
        window's current image."""
        main_window = self._main_window
        tab = main_window.tab_widget.currentWidget() if main_window else None
        imagename = getattr(tab, "imagename", None)
        if tab is None or getattr(tab, "current_image_data", None) is None:
            QMessageBox.information(
                self, "No Image", "Open an image in the main window first."
            )
            return

        # The image itself, also when the main window shows a converted copy
        original, coordinates = viewer_image(main_window)
        self.coordinates_combo.setCurrentIndex(self.coordinates_combo.findData(coordinates))
        if original and is_image(original):
            directory = os.path.dirname(os.path.abspath(original))
            self.input_directory_edit.setText(directory)
            self.input_pattern_edit.setText("*" + os.path.splitext(original)[1])
            self.reference_image = os.path.abspath(original)
            self.reference_image_edit.setText(self.reference_image)

        self._copy_viewer_settings(main_window)
        self.range_mode_combo.setCurrentIndex(0)  # the viewer's min/max
        self.wcs_coords_check.setChecked(getattr(tab, "current_wcs", None) is not None)
        self._copy_viewer_look(main_window)

        # Visible area as the region, in the file's pixels (the viewer may
        # show a downsampled copy)
        ax = tab.figure.axes[0] if tab.figure.axes else None
        shown_w, shown_h = tab.current_image_data.shape[:2]
        scale = 1.0
        try:
            from ..processing.image_meta import fits_header

            scale = fits_header(imagename).get("NAXIS1", shown_w) / shown_w
        except Exception:
            pass
        if ax is not None:
            (x0, x1), (y0, y1) = sorted(ax.get_xlim()), sorted(ax.get_ylim())
            x_min, x_max = max(0, x0 + 0.5), min(shown_w - 1, x1 - 0.5)
            y_min, y_max = max(0, y0 + 0.5), min(shown_h - 1, y1 - 0.5)
            zoomed = bool(
                x_min > 0.5
                or y_min > 0.5
                or x_max < shown_w - 1.5
                or y_max < shown_h - 1.5
            )
            self.region_enabled.setChecked(zoomed)
            if zoomed:
                for spinbox, value in (
                    (self.x_min_spinbox, x_min),
                    (self.x_max_spinbox, x_max),
                    (self.y_min_spinbox, y_min),
                    (self.y_max_spinbox, y_max),
                ):
                    spinbox.setValue(int(round(value * scale)))
        # Its contour layers, if contours are shown
        if tab.show_contours_checkbox.isChecked() and getattr(
            tab, "contour_layers", None
        ):
            self._set_contour_layers(tab.contour_layers, tab.contour_options)
        if imagename and is_image(original):
            self.image_series_radio.setChecked(True)
            self.preview_input_files()
        self.schedule_preview()

    def setup_ui(self):
        """Set up the UI elements"""
        self.setSizeGripEnabled(True)
        # Settings on the left, the preview on the right
        outer_layout = QHBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Horizontal)
        outer_layout.addWidget(splitter)
        settings_panel = QWidget()
        splitter.addWidget(settings_panel)
        self.preview_panel = PreviewPanel()
        splitter.addWidget(self.preview_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        settings_panel.setMinimumWidth(600)
        splitter.setSizes([640, 680])
        self.preview_panel.slider.valueChanged.connect(self._on_preview_slider)
        self.preview_panel.play_button.toggled.connect(self._toggle_play)
        self.preview_panel.image_label.dragged.connect(self._on_region_dragged)
        buttons = self.preview_panel.zoom_buttons
        buttons["in"].clicked.connect(lambda: self._zoom(factor=0.5))
        buttons["out"].clicked.connect(lambda: self._zoom(factor=2.0))
        buttons["reset"].clicked.connect(lambda: self._set_region(None))
        buttons["degree"].clicked.connect(self._zoom_one_degree)

        main_layout = QVBoxLayout(settings_panel)
        main_layout.setContentsMargins(16, 16, 8, 16)
        main_layout.setSpacing(12)

        # Create tab widget
        self.tab_widget = QTabWidget()

        # Create tabs
        input_tab = QWidget()
        display_tab = QWidget()
        overlay_tab = QWidget()
        region_tab = QWidget()  # New tab for region selection
        output_tab = QWidget()

        # Set up tab layouts
        input_layout = QVBoxLayout(input_tab)
        display_layout = QVBoxLayout(display_tab)
        overlay_layout = QVBoxLayout(overlay_tab)
        region_layout = QVBoxLayout(region_tab)  # Layout for the new region tab
        output_layout = QVBoxLayout(output_tab)

        # Start from the main window's view, or set things up below
        preview_controls_group = QGroupBox("Start")
        preview_controls_layout = QHBoxLayout(preview_controls_group)
        preview_controls_layout.setContentsMargins(10, 8, 10, 8)
        preview_controls_layout.setSpacing(12)

        use_view_btn = QPushButton("Use Current View")
        use_view_btn.setToolTip(
            "Take the files, colormap, stretch, range, visible area and\n"
            "contours of the image open in the main window"
        )
        use_view_btn.setEnabled(self._main_window is not None)
        use_view_btn.clicked.connect(self._use_current_view)
        preview_controls_layout.addWidget(use_view_btn)

        preview_controls_layout.addStretch()

        # Add preview controls to the main layout first
        main_layout.addWidget(preview_controls_group)

        input_scroll = self._create_input_tab(input_layout)

        display_scroll = self._create_display_tab(display_layout)

        region_scroll = self._create_region_tab(region_layout)

        overlay_scroll = self._create_overlay_tab(overlay_layout)

        output_scroll = self._create_output_tab(output_layout)
        self._output_tab = output_scroll

        contours_scroll = self._create_contours_tab()
        frames_scroll = self._create_frames_tab()

        # Add tabs to tab widget (Contours is second when in Contour Mode)
        self.tab_widget.addTab(input_scroll, "Input")
        self.tab_widget.addTab(contours_scroll, "Contours")
        self.tab_widget.addTab(frames_scroll, "Frames")
        self.tab_widget.addTab(display_scroll, "Display")
        self.tab_widget.addTab(region_scroll, "Region")
        self.tab_widget.addTab(overlay_scroll, "Overlays")
        self.tab_widget.addTab(output_scroll, "Output")

        # Add the tab widget to the main layout (after the preview)
        main_layout.addWidget(self.tab_widget)

        # Buttons at the bottom
        button_layout = QHBoxLayout()
        button_layout.setSpacing(12)
        button_layout.addStretch()

        save_btn = QPushButton("Save Settings...")
        save_btn.setToolTip("Save all settings (and the frames) as a preset file")
        save_btn.clicked.connect(self.save_settings)
        button_layout.insertWidget(0, save_btn)
        load_btn = QPushButton("Load Settings...")
        load_btn.clicked.connect(self.load_settings)
        button_layout.insertWidget(1, load_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setMinimumWidth(100)
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)

        self.create_btn = QPushButton("Create Video")
        self.create_btn.setMinimumWidth(120)
        self.create_btn.setObjectName("PrimaryButton")
        self.create_btn.clicked.connect(self.create_video)
        self.create_btn.setEnabled(False)
        button_layout.addWidget(self.create_btn)

        main_layout.addLayout(button_layout)

        self._watch_settings()

    def _create_input_tab(self, input_layout):
        """Input tab: directory, file pattern, sorting, Stokes. Returns the tab's scroll area."""
        # ------ Input Tab ------
        # Create a scroll area for the input tab
        input_scroll = QScrollArea()
        input_scroll.setWidgetResizable(True)
        input_scroll_content = QWidget()
        input_layout = QVBoxLayout(input_scroll_content)
        input_layout.setContentsMargins(12, 12, 12, 12)
        input_layout.setSpacing(12)
        input_scroll.setWidget(input_scroll_content)

        # Input pattern section
        input_group = QGroupBox("Input Files")
        input_group_layout = QGridLayout(input_group)
        input_group_layout.setContentsMargins(12, 16, 12, 12)
        input_group_layout.setHorizontalSpacing(10)
        input_group_layout.setVerticalSpacing(8)

        # 0. Image: a series of files, or one image for every frame
        source_row = QHBoxLayout()
        self.image_series_radio = QRadioButton("Image series (folder + pattern)")
        self.image_single_radio = QRadioButton("Single image")
        self.image_series_radio.setChecked(True)
        self.image_single_radio.setToolTip(
            "The same image in every frame; the frames then follow the first\n"
            "contour layer that is a series"
        )
        source_group = QButtonGroup(self)
        source_group.addButton(self.image_series_radio)
        source_group.addButton(self.image_single_radio)
        source_row.addWidget(self.image_series_radio)
        source_row.addWidget(self.image_single_radio)
        source_row.addStretch()
        input_layout.addLayout(source_row)

        # 1. Directory field
        input_group_layout.addWidget(QLabel("Input Directory:"), 0, 0)
        self.input_directory_edit = QLineEdit()
        input_group_layout.addWidget(self.input_directory_edit, 0, 1)

        browse_dir_btn = QPushButton("Browse")
        browse_dir_btn.clicked.connect(
            lambda: (
                (
                    self.input_directory_edit.setText(os.getcwd())
                    if not self.input_directory_edit.text()
                    else None
                ),
                self.browse_input_directory(),
            )
        )
        input_group_layout.addWidget(browse_dir_btn, 0, 2)

        # 2. Pattern field
        input_group_layout.addWidget(QLabel("File Pattern:"), 1, 0)
        self.input_pattern_edit = QLineEdit()
        self.input_pattern_edit.setPlaceholderText("e.g., *.fits, *_171*.fits or *.image (CASA)")
        input_group_layout.addWidget(self.input_pattern_edit, 1, 1)

        # Scan button
        scan_btn = QPushButton("Scan")
        scan_btn.clicked.connect(self.preview_input_files)
        input_group_layout.addWidget(scan_btn, 1, 2)

        # File sorting
        input_group_layout.addWidget(QLabel("Sort Files By:"), 2, 0)
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["Filename", "Date/Time", "Extension"])
        input_group_layout.addWidget(self.sort_combo, 2, 1)

        # Stokes parameter
        input_group_layout.addWidget(QLabel("Stokes Parameter:"), 3, 0)
        self.stokes_combo = QComboBox()
        self.stokes_combo.addItems(["I", "Q", "U", "V"])
        input_group_layout.addWidget(self.stokes_combo, 3, 1)

        input_layout.addWidget(input_group)

        self.single_image_group = QGroupBox("Single Image")
        single_layout = QHBoxLayout(self.single_image_group)
        self.single_image_edit = QLineEdit()
        self.single_image_edit.setPlaceholderText("FITS or CASA image shown in every frame")
        single_layout.addWidget(self.single_image_edit, 1)
        single_browse = QPushButton("Browse")
        single_browse.clicked.connect(self._browse_single_image)
        single_layout.addWidget(single_browse)
        casa_browse = QPushButton("CASA Image…")
        casa_browse.setToolTip("Choose a CASA image (a folder)")
        casa_browse.clicked.connect(self._browse_single_casa_image)
        single_layout.addWidget(casa_browse)
        input_layout.addWidget(self.single_image_group)

        def show_image_source(series):
            input_group.setEnabled(series)
            self.single_image_group.setEnabled(not series)

        self.image_series_radio.toggled.connect(show_image_source)
        show_image_source(True)

        # Files found status (separate from the group) - use theme-compatible styling
        self.files_found_label = QLabel("No files found yet")
        self.files_found_label.setObjectName("StatusLabel")
        input_layout.addWidget(self.files_found_label)
        input_layout.addStretch()
        return input_scroll

    def _create_display_tab(self, display_layout):
        """Display tab: colormap, stretch, range, frame size, presets. Returns the tab's scroll area."""
        # ------ Display Tab ------
        # Create a scroll area for the display tab
        display_scroll = QScrollArea()
        display_scroll.setWidgetResizable(True)
        display_scroll_content = QWidget()
        display_layout = QVBoxLayout(display_scroll_content)
        display_layout.setContentsMargins(12, 12, 12, 12)
        display_layout.setSpacing(12)
        display_scroll.setWidget(display_scroll_content)

        # Reference image for display settings
        reference_group = QGroupBox("Reference Image")
        reference_layout = QGridLayout(reference_group)
        reference_layout.setContentsMargins(12, 16, 12, 12)
        reference_layout.setHorizontalSpacing(10)
        reference_layout.setVerticalSpacing(8)

        reference_layout.addWidget(QLabel("Reference Image:"), 0, 0)
        self.reference_image_edit = QLineEdit()
        self.reference_image_edit.setReadOnly(True)  # Make it read-only
        reference_layout.addWidget(self.reference_image_edit, 0, 1)

        browse_reference_btn = QPushButton("Browse")
        browse_reference_btn.clicked.connect(self.browse_reference_image)
        reference_layout.addWidget(browse_reference_btn, 0, 2)

        display_layout.addWidget(reference_group)

        # Visualization settings section
        viz_group = QGroupBox("Visualization")
        viz_form = QFormLayout(viz_group)
        viz_form.setContentsMargins(12, 16, 12, 12)
        viz_form.setVerticalSpacing(8)
        viz_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        # Colormap
        self.colormap_combo = QComboBox()
        ensure_solar_colormaps()
        colormaps = sorted(
            [cmap for cmap in plt.colormaps() if not cmap.endswith("_r")]
        )
        self.colormap_combo.addItems(colormaps)
        idx = self.colormap_combo.findText("viridis")
        if idx >= 0:
            self.colormap_combo.setCurrentIndex(idx)
        viz_form.addRow("Colormap:", self.colormap_combo)

        # Stretch
        self.stretch_combo = QComboBox()
        self.stretch_combo.addItems(
            [
                "Linear",
                "Log",
                "Sqrt",
                "Power",
                "Arcsinh",
                "ZScale",
                "Histogram Equalization",
            ]
        )
        self.stretch_combo.setItemData(
            0, "Linear stretch - no transformation", Qt.ToolTipRole
        )
        self.stretch_combo.setItemData(
            1, "Logarithmic stretch - enhances very faint features", Qt.ToolTipRole
        )
        self.stretch_combo.setItemData(
            2, "Square root stretch - enhances faint features", Qt.ToolTipRole
        )
        self.stretch_combo.setItemData(
            3, "Power law stretch - adjustable using gamma", Qt.ToolTipRole
        )
        self.stretch_combo.setItemData(
            4,
            "Arcsinh stretch - similar to log but handles negative values",
            Qt.ToolTipRole,
        )
        self.stretch_combo.setItemData(
            5,
            "ZScale stretch - automatic contrast based on image statistics",
            Qt.ToolTipRole,
        )
        self.stretch_combo.setItemData(
            6,
            "Histogram equalization - enhances contrast by redistributing intensities",
            Qt.ToolTipRole,
        )
        self.stretch_combo.currentIndexChanged.connect(self.update_gamma_controls)
        viz_form.addRow("Stretch:", self.stretch_combo)

        # Gamma (for power stretch)
        self.gamma_spinbox = QDoubleSpinBox()
        self.gamma_spinbox.setRange(0.1, 10.0)
        self.gamma_spinbox.setSingleStep(0.1)
        self.gamma_spinbox.setValue(1.0)
        viz_form.addRow("Gamma:", self.gamma_spinbox)

        # Colorbar option
        self.colorbar_check = QCheckBox("Show Colorbar")
        self.colorbar_check.setChecked(True)
        viz_form.addRow("", self.colorbar_check)

        self.difference_combo = QComboBox()
        for text, mode in (
            ("None", "none"),
            ("Running (each frame minus the previous)", "running"),
            ("Base (each frame minus the first)", "base"),
        ):
            self.difference_combo.addItem(text, mode)
        self.difference_combo.setToolTip(
            "Difference movies show what changes. Auto ranges become symmetric\n"
            "about zero; a diverging colormap (e.g. RdBu_r, coolwarm) suits them.\n"
            "A running difference starts at the second frame."
        )
        viz_form.addRow("Difference:", self.difference_combo)

        display_layout.addWidget(viz_group)

        # Range settings section
        range_group = QGroupBox("Range Scaling")
        range_form = QFormLayout(range_group)
        range_form.setContentsMargins(12, 16, 12, 12)
        range_form.setVerticalSpacing(8)
        range_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        # Range mode selection
        self.range_mode_combo = QComboBox()
        self.range_mode_combo.addItems(["Fixed Range", "Auto per Frame", "Global Auto"])
        self.range_mode_combo.setCurrentIndex(1)  # Default to "Auto per Frame"
        self.range_mode_combo.setToolTip(
            "Fixed Range: Use the min/max values specified below for all frames\n"
            "Auto per Frame: Calculate min/max independently for each frame based on percentiles\n"
            "Global Auto: Calculate min/max once from all frames based on percentiles"
        )
        self.range_mode_combo.currentIndexChanged.connect(self.toggle_range_mode)
        range_form.addRow("Mode:", self.range_mode_combo)

        # Add explanatory label
        """self.range_explanation_label = QLabel(
            "Auto Per Frame: Min/max calculated independently for each frame"
        )
        self.range_explanation_label.setObjectName("SecondaryText")
        range_form.addRow("", self.range_explanation_label)"""

        # Min/Max values in a horizontal layout
        minmax_widget = QWidget()
        minmax_layout = QHBoxLayout(minmax_widget)
        minmax_layout.setContentsMargins(0, 0, 0, 0)
        minmax_layout.setSpacing(10)

        minmax_layout.addWidget(QLabel("Min:"))
        self.vmin_spinbox = QDoubleSpinBox()
        self.vmin_spinbox.setRange(-1e10, 1e10)
        self.vmin_spinbox.setDecimals(2)
        self.vmin_spinbox.setValue(0)
        minmax_layout.addWidget(self.vmin_spinbox)

        minmax_layout.addWidget(QLabel("Max:"))
        self.vmax_spinbox = QDoubleSpinBox()
        self.vmax_spinbox.setRange(-1e10, 1e10)
        self.vmax_spinbox.setDecimals(2)
        self.vmax_spinbox.setValue(3000)
        minmax_layout.addWidget(self.vmax_spinbox)

        range_form.addRow("Values:", minmax_widget)

        # Percentile range used by the two Auto modes
        percentile_widget = QWidget()
        percentile_layout = QHBoxLayout(percentile_widget)
        percentile_layout.setContentsMargins(0, 0, 0, 0)
        percentile_layout.setSpacing(10)
        percentile_layout.addWidget(QLabel("Low:"))
        self.lower_percentile_spinbox = QDoubleSpinBox()
        self.lower_percentile_spinbox.setRange(0, 100)
        self.lower_percentile_spinbox.setDecimals(1)
        self.lower_percentile_spinbox.setSingleStep(0.5)
        self.lower_percentile_spinbox.setSuffix(" %")
        self.lower_percentile_spinbox.setValue(0)
        percentile_layout.addWidget(self.lower_percentile_spinbox)
        percentile_layout.addWidget(QLabel("High:"))
        self.upper_percentile_spinbox = QDoubleSpinBox()
        self.upper_percentile_spinbox.setRange(0, 100)
        self.upper_percentile_spinbox.setDecimals(1)
        self.upper_percentile_spinbox.setSingleStep(0.5)
        self.upper_percentile_spinbox.setSuffix(" %")
        self.upper_percentile_spinbox.setValue(100)
        percentile_layout.addWidget(self.upper_percentile_spinbox)
        percentile_widget.setToolTip(
            "Auto modes scale each frame (or all frames) from this percentile\n"
            "range of the pixel values; 0-100 % is the full min-max range"
        )
        range_form.addRow("Percentiles:", percentile_widget)

        display_layout.addWidget(range_group)

        # Frame settings section
        frame_group = QGroupBox("Frame Settings")
        frame_form = QFormLayout(frame_group)
        frame_form.setContentsMargins(12, 16, 12, 12)
        frame_form.setVerticalSpacing(8)
        frame_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        # Frame resize in a horizontal layout
        size_widget = QWidget()
        size_layout = QHBoxLayout(size_widget)
        size_layout.setContentsMargins(0, 0, 0, 0)
        size_layout.setSpacing(10)

        size_layout.addWidget(QLabel("Width:"))
        self.width_spinbox = QSpinBox()
        self.width_spinbox.setRange(0, 7680)
        self.width_spinbox.setValue(0)
        self.width_spinbox.setSpecialValueText("Auto")
        size_layout.addWidget(self.width_spinbox)

        size_layout.addWidget(QLabel("Height:"))
        self.height_spinbox = QSpinBox()
        self.height_spinbox.setRange(0, 4320)
        self.height_spinbox.setValue(0)
        self.height_spinbox.setSpecialValueText("Auto")
        size_layout.addWidget(self.height_spinbox)

        size_widget.setToolTip(
            "Frame size in pixels. Auto follows the image's shape: with both on\n"
            "Auto the longer side is 1000 px; set one and the other follows."
        )
        frame_form.addRow("Size:", size_widget)

        self.size_preset_combo = QComboBox()
        for text, height in SIZE_PRESETS:
            self.size_preset_combo.addItem(text, height)
        self.size_preset_combo.setToolTip(
            "Frame height in pixels; the width follows the image's shape"
        )
        self.size_preset_combo.activated.connect(self._apply_size_preset)
        frame_form.addRow("Preset:", self.size_preset_combo)

        display_layout.addWidget(frame_group)

        # WCS Coordinates section
        wcs_group = QGroupBox("Coordinate System")
        wcs_form = QFormLayout(wcs_group)
        wcs_form.setContentsMargins(12, 16, 12, 12)
        wcs_form.setVerticalSpacing(8)

        self.coordinates_combo = QComboBox()
        for key in COORDINATES:
            self.coordinates_combo.addItem(COORDINATE_NAMES[key], key)
        self.coordinates_combo.setToolTip(
            "Show every frame in these coordinates, converting images that are\n"
            "in the other system as the main window's HPC / RA-Dec button does\n"
            "(rotated to solar north for helioprojective). Converted images are\n"
            "kept while this window is open, so each is converted once."
        )
        wcs_form.addRow("Video in:", self.coordinates_combo)

        self.wcs_coords_check = QCheckBox("Show WCS Coordinates in Video")
        self.wcs_coords_check.setChecked(True)
        self.wcs_coords_check.setToolTip(
            "Display RA/Dec or Solar-X/Solar-Y coordinates instead of pixels"
        )
        wcs_form.addRow("", self.wcs_coords_check)

        self.wcs_info_label = QLabel("Axes are labelled in the video's coordinates")
        self.wcs_info_label.setObjectName("SecondaryText")
        wcs_form.addRow("", self.wcs_info_label)

        display_layout.addWidget(wcs_group)

        # Add preset buttons similar to main application
        presets_group = QGroupBox("Display Presets")
        presets_layout = QGridLayout(presets_group)

        # Auto range presets
        auto_minmax_btn = QPushButton("Auto Min/Max")
        auto_minmax_btn.clicked.connect(self.apply_auto_minmax)
        presets_layout.addWidget(auto_minmax_btn, 0, 0)

        auto_percentile_btn = QPushButton("Auto Percentile (1-99%)")
        auto_percentile_btn.clicked.connect(self.apply_auto_percentile)
        presets_layout.addWidget(auto_percentile_btn, 0, 1)

        """auto_median_btn = QPushButton("Auto Median±3×RMS")
        auto_median_btn.clicked.connect(self.apply_auto_median_rms)
        presets_layout.addWidget(auto_median_btn, 1, 0)"""

        # AIA/HMI Presets
        aia_preset_btn = QPushButton("AIA 171Å Preset")
        aia_preset_btn.clicked.connect(self.apply_aia_preset)
        presets_layout.addWidget(aia_preset_btn, 1, 0)

        hmi_preset_btn = QPushButton("HMI Preset")
        hmi_preset_btn.clicked.connect(self.apply_hmi_preset)
        presets_layout.addWidget(hmi_preset_btn, 1, 1)

        display_layout.addWidget(presets_group)
        display_layout.addStretch()
        return display_scroll

    def _create_region_tab(self, region_layout):
        """Region tab: optional sub-region selection. Returns the tab's scroll area."""
        # ------ Region Tab ------
        # Create a scroll area for the region tab
        region_scroll = QScrollArea()
        region_scroll.setWidgetResizable(True)
        region_scroll_content = QWidget()
        region_layout = QVBoxLayout(region_scroll_content)
        region_layout.setContentsMargins(12, 12, 12, 12)
        region_layout.setSpacing(12)
        region_scroll.setWidget(region_scroll_content)

        region_group = QGroupBox("Region Selection")
        region_main_layout = QVBoxLayout(region_group)
        region_main_layout.setContentsMargins(12, 16, 12, 12)
        region_main_layout.setSpacing(12)

        # Enable region selection
        self.region_enabled = QCheckBox("Enable Region Selection (Zoomed Video)")
        self.region_enabled.setChecked(False)
        self.region_enabled.stateChanged.connect(self.toggle_region_controls)
        region_main_layout.addWidget(self.region_enabled)

        # Coordinate inputs using form layout
        coord_form = QFormLayout()
        coord_form.setVerticalSpacing(8)
        coord_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        # X range
        x_range_widget = QWidget()
        x_range_layout = QHBoxLayout(x_range_widget)
        x_range_layout.setContentsMargins(0, 0, 0, 0)
        x_range_layout.setSpacing(8)

        self.x_min_spinbox = QSpinBox()
        self.x_min_spinbox.setRange(0, 10000)
        self.x_min_spinbox.setValue(0)
        x_range_layout.addWidget(self.x_min_spinbox)
        x_range_layout.addWidget(QLabel("to"))
        self.x_max_spinbox = QSpinBox()
        self.x_max_spinbox.setRange(0, 10000)
        self.x_max_spinbox.setValue(1000)
        x_range_layout.addWidget(self.x_max_spinbox)
        x_range_layout.addWidget(QLabel("px"))
        x_range_layout.addStretch()
        coord_form.addRow("X Range:", x_range_widget)

        # Y range
        y_range_widget = QWidget()
        y_range_layout = QHBoxLayout(y_range_widget)
        y_range_layout.setContentsMargins(0, 0, 0, 0)
        y_range_layout.setSpacing(8)

        self.y_min_spinbox = QSpinBox()
        self.y_min_spinbox.setRange(0, 10000)
        self.y_min_spinbox.setValue(0)
        y_range_layout.addWidget(self.y_min_spinbox)
        y_range_layout.addWidget(QLabel("to"))
        self.y_max_spinbox = QSpinBox()
        self.y_max_spinbox.setRange(0, 10000)
        self.y_max_spinbox.setValue(1000)
        y_range_layout.addWidget(self.y_max_spinbox)
        y_range_layout.addWidget(QLabel("px"))
        y_range_layout.addStretch()
        coord_form.addRow("Y Range:", y_range_widget)

        region_main_layout.addLayout(coord_form)

        drag_hint = QLabel(
            "Or drag a box on the preview, and use the zoom buttons above it "
            "(zoom in / out, whole image, 1°×1°) as in the main window."
        )
        drag_hint.setWordWrap(True)
        drag_hint.setObjectName("SecondaryText")
        region_main_layout.addWidget(drag_hint)

        self.track_rotation_check = QCheckBox("Follow solar rotation")
        self.track_rotation_check.setToolTip(
            "Move the region with the Sun's differential rotation from the first\n"
            "frame, so a feature stays in view (helioprojective images, on the disk)"
        )
        region_main_layout.addWidget(self.track_rotation_check)

        region_layout.addWidget(region_group)

        # Presets group
        presets_group = QGroupBox("Quick Presets")
        presets_layout = QHBoxLayout(presets_group)
        presets_layout.setContentsMargins(12, 16, 12, 12)
        presets_layout.setSpacing(10)

        center_25_btn = QPushButton("Center 25%")
        center_25_btn.clicked.connect(lambda: self.set_region_preset(0.25))
        presets_layout.addWidget(center_25_btn)

        center_50_btn = QPushButton("Center 50%")
        center_50_btn.clicked.connect(lambda: self.set_region_preset(0.5))
        presets_layout.addWidget(center_50_btn)

        center_75_btn = QPushButton("Center 75%")
        center_75_btn.clicked.connect(lambda: self.set_region_preset(0.75))
        presets_layout.addWidget(center_75_btn)

        region_layout.addWidget(presets_group)

        # Help text at the bottom
        help_label = QLabel(
            "Make a video of only part of the image. Region pixels are those of "
            "the images in the video's coordinates (Display tab)."
        )
        help_label.setWordWrap(True)
        help_label.setObjectName("SecondaryText")
        region_layout.addWidget(help_label)

        # Initially disable the region controls
        self.toggle_region_controls(False)

        region_layout.addStretch()
        return region_scroll

    def _create_overlay_tab(self, overlay_layout):
        """Overlays tab: timestamp, frame number, file name, min/max timeline. Returns the tab's scroll area."""
        # ------ Overlay Tab ------
        # Create a scroll area for the overlay tab
        overlay_scroll = QScrollArea()
        overlay_scroll.setWidgetResizable(True)
        overlay_scroll_content = QWidget()
        overlay_layout = QVBoxLayout(overlay_scroll_content)
        overlay_layout.setContentsMargins(12, 12, 12, 12)
        overlay_layout.setSpacing(12)
        overlay_scroll.setWidget(overlay_scroll_content)

        # Overlay settings section
        overlay_group = QGroupBox("Overlay Settings")
        overlay_main_layout = QHBoxLayout(overlay_group)
        overlay_main_layout.setContentsMargins(12, 16, 12, 12)

        # Use a form layout for cleaner organization
        overlay_form = QFormLayout()
        overlay_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        # Timestamp, Frame number, and Filename as checkboxes with labels
        self.timestamp_check = QCheckBox("Add Timestamp")
        self.timestamp_check.setChecked(True)
        self.timestamp_check.setToolTip("Show date/time information from FITS header")
        overlay_form.addRow("", self.timestamp_check)

        self.frame_number_check = QCheckBox("Add Frame Number")
        self.frame_number_check.setChecked(False)
        self.frame_number_check.setToolTip("Show frame counter (e.g., Frame: 1/100)")
        overlay_form.addRow("", self.frame_number_check)

        self.filename_check = QCheckBox("Add Filename")
        self.filename_check.setChecked(False)
        self.filename_check.setToolTip("Show source filename in the video frame")
        overlay_form.addRow("", self.filename_check)

        overlay_main_layout.addLayout(overlay_form)
        overlay_layout.addWidget(overlay_group)

        overlay_layout.addWidget(self._create_plot_elements_group())
        overlay_layout.addWidget(self._create_appearance_group())

        overlay_layout.addWidget(self._create_line_plot_group())

        # Add spacer to push controls to the top
        overlay_layout.addStretch()
        return overlay_scroll

    def _create_plot_elements_group(self):
        """Solar disk, beam and grid, each with its main-window style dialog."""
        group = QGroupBox("Solar Disk, Beam and Grid")
        grid = QGridLayout(group)
        grid.setContentsMargins(12, 16, 12, 12)
        rows = (
            (
                "show_solar_disk_check",
                "Solar disk",
                self._edit_solar_disk_style,
                "Circle of the Sun's apparent size at each frame's time, centred\n"
                "where the Sun is in that frame (from its FITS header)",
            ),
            (
                "show_beam_check",
                "Beam",
                self._edit_beam_style,
                "Restoring beam (BMAJ, BMIN, BPA in the header) in the lower left corner",
            ),
            (
                "show_grid_check",
                "Coordinate grid",
                self._edit_grid_style,
                "Grid of the image's coordinates (needs WCS coordinates, Display tab)",
            ),
        )
        for row, (name, text, edit, tip) in enumerate(rows):
            check = QCheckBox(text)
            check.setToolTip(tip)
            setattr(self, name, check)
            grid.addWidget(check, row, 0)
            button = QPushButton("Style…")
            button.setToolTip(
                f"Colour and line style of the {text.lower()}, as in the main window"
            )
            button.clicked.connect(edit)
            grid.addWidget(button, row, 1)
        grid.setColumnStretch(0, 1)
        self.wcs_coords_check.toggled.connect(self._update_grid_check)
        self._update_grid_check()
        return group

    def _create_appearance_group(self):
        """Plot appearance (main window's Plot Customization) and copying
        the look of the main window."""
        group = QGroupBox("Plot Appearance")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(12, 16, 12, 12)

        row = QHBoxLayout()
        self.appearance_label = QLabel()
        row.addWidget(self.appearance_label, 1)
        customize = QPushButton("Customize…")
        customize.setToolTip(
            "Labels, title, fonts, colours, ticks, borders and padding"
        )
        customize.clicked.connect(self._edit_plot_settings)
        row.addWidget(customize)
        reset = QPushButton("Reset")
        reset.setToolTip("Back to the video's own default look")
        reset.clicked.connect(lambda: self._set_plot_settings(None, "video default"))
        row.addWidget(reset)
        layout.addLayout(row)

        copy_row = QHBoxLayout()
        copy_btn = QPushButton("Copy from Main Window")
        copy_btn.setToolTip(
            "Take the solar disk, beam and grid (on/off and style) and the plot\n"
            "appearance of the main window's current tab"
        )
        copy_btn.clicked.connect(
            lambda: self._copy_viewer_look(self._main_window, notify=True)
        )
        copy_btn.setEnabled(self._main_window is not None)
        copy_row.addWidget(copy_btn)
        copy_row.addStretch()
        layout.addLayout(copy_row)

        note = QLabel(
            "Opened from the main window, this window starts with the main window's "
            "solar disk, beam, grid and plot appearance. Change them here, or set "
            "them up in the main window and press Copy from Main Window. "
            '"auto" colours are those of the light theme.'
        )
        note.setWordWrap(True)
        note.setObjectName("SecondaryText")
        layout.addWidget(note)
        self._set_plot_settings(None, "video default")
        return group

    def _create_line_plot_group(self):
        """The line plot under the image: light curves of the image or of
        contour images, against time."""
        group = QGroupBox("Line Plot")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(12, 16, 12, 12)
        layout.setSpacing(8)

        self.line_plot_check = QCheckBox("Show a line plot under the image")
        self.line_plot_check.setToolTip(
            "Curves of the image or of contour images over the frames (light\n"
            "curves), drawn up to the frame shown"
        )
        layout.addWidget(self.line_plot_check)

        self.line_plot_curves = [dict(c) for c in default_line_plot()["curves"]]
        self._curve_grid = QGridLayout()
        self._curve_grid.setHorizontalSpacing(6)
        layout.addLayout(self._curve_grid)
        self.add_curve_btn = QPushButton("Add Curve")
        self.add_curve_btn.clicked.connect(self._add_curve)
        add_row = QHBoxLayout()
        add_row.addWidget(self.add_curve_btn)
        add_row.addStretch()
        layout.addLayout(add_row)

        form = QFormLayout()
        self.line_plot_x_combo = QComboBox()
        self.line_plot_x_combo.addItem("Observation time (UT)", "time")
        self.line_plot_x_combo.addItem("Frame number", "frame")
        self.line_plot_x_combo.setToolTip(
            "Observation time shows gaps and changes of cadence (frame numbers\n"
            "are used if a frame has no time)"
        )
        form.addRow("X axis:", self.line_plot_x_combo)
        y_row = QHBoxLayout()
        self.line_plot_y_combo = QComboBox()
        self.line_plot_y_combo.addItem("Shared (in the images' unit)", "shared")
        self.line_plot_y_combo.addItem("Normalised (each ÷ its largest value)", "normalised")
        y_row.addWidget(self.line_plot_y_combo)
        self.line_plot_log_check = QCheckBox("Log scale")
        y_row.addWidget(self.line_plot_log_check)
        y_row.addStretch()
        form.addRow("Y axis:", y_row)
        self.line_plot_height_spin = QSpinBox()
        self.line_plot_height_spin.setRange(8, 40)
        self.line_plot_height_spin.setValue(default_line_plot()["height"])
        self.line_plot_height_spin.setSuffix(" % of the frame")
        form.addRow("Height:", self.line_plot_height_spin)
        layout.addLayout(form)

        box_row = QHBoxLayout()
        self.curve_box_label = QLabel()
        box_row.addWidget(self.curve_box_label, 1)
        choose = QPushButton("Choose on Preview")
        choose.setToolTip('Drag the box for "box" curves on the preview')
        choose.clicked.connect(self._choose_curve_box)
        box_row.addWidget(choose)
        clear = QPushButton("Clear")
        clear.clicked.connect(lambda: (self._set_curve_box(None), self.schedule_preview()))
        box_row.addWidget(clear)
        layout.addLayout(box_row)
        self.show_curve_box_check = QCheckBox("Show the box in the video")
        self.show_curve_box_check.setChecked(True)
        layout.addWidget(self.show_curve_box_check)

        self.line_plot_csv_check = QCheckBox("Save the values as a CSV file next to the video")
        self.line_plot_csv_check.setToolTip(
            "VIDEO_lightcurve.csv: frame, file, time and every curve's value"
        )
        layout.addWidget(self.line_plot_csv_check)

        self.curve_box = None
        self._set_curve_box(None)
        self._show_curves()
        return group

    def _line_plot_settings(self):
        """The line plot's settings (see lightcurve.default_line_plot)."""
        return dict(
            default_line_plot(),
            enabled=self.line_plot_check.isChecked(),
            curves=[dict(c) for c in self.line_plot_curves],
            x=self.line_plot_x_combo.currentData(),
            y=self.line_plot_y_combo.currentData(),
            log=self.line_plot_log_check.isChecked(),
            box=tuple(self.curve_box) if self.curve_box else None,
            show_box=self.show_curve_box_check.isChecked(),
            height=self.line_plot_height_spin.value(),
            csv=self.line_plot_csv_check.isChecked(),
        )

    def _show_curves(self):
        """One row per curve: source, quantity, area and a remove button."""
        while self._curve_grid.count():
            widget = self._curve_grid.takeAt(0).widget()
            if widget is not None:
                # Gone now, not when the event loop next deletes it
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        sources = [("Image", "image")] + [
            (contour_layer_name(layer, "Image"), layer.get("id"))
            # (the Overlays tab is made before the Contours tab)
            for layer in getattr(self, "contour_layers", [])
            if layer.get("id")
        ]
        for row, curve in enumerate(self.line_plot_curves):
            source = QComboBox()
            for text, key in sources:
                source.addItem(text, key)
            source.setCurrentIndex(max(source.findData(curve.get("source", "image")), 0))
            curve["source"] = source.currentData()
            quantity = QComboBox()
            for key, text in QUANTITIES.items():
                quantity.addItem(text, key)
            quantity.setCurrentIndex(max(quantity.findData(curve.get("quantity", "max")), 0))
            curve["quantity"] = quantity.currentData()
            quantity.setToolTip(
                "Sum: of a Jy/beam image, the integrated flux in Jy (÷ the beam area)"
            )
            area = QComboBox()
            areas = IMAGE_AREAS if curve["source"] == "image" else LAYER_AREAS
            for key, text in areas.items():
                area.addItem(text, key)
            area.setCurrentIndex(max(area.findData(curve.get("area")), 0))
            curve["area"] = area.currentData()
            remove = QPushButton("✕")
            remove.setFixedWidth(28)
            remove.setToolTip("Remove this curve")

            def changed(*_, row=row, source=source, quantity=quantity, area=area):
                curve = self.line_plot_curves[row]
                new_source = source.currentData()
                if new_source != curve.get("source"):
                    curve.update(source=new_source, area=None)
                    self._show_curves()  # the other source's areas
                else:
                    curve.update(quantity=quantity.currentData(), area=area.currentData())
                self.schedule_preview()

            source.currentIndexChanged.connect(changed)
            quantity.currentIndexChanged.connect(changed)
            area.currentIndexChanged.connect(changed)
            remove.clicked.connect(lambda *_, row=row: self._remove_curve(row))
            for column, widget in enumerate((source, quantity, area, remove)):
                self._curve_grid.addWidget(widget, row, column)
        self._curve_grid.setColumnStretch(0, 2)
        self._curve_grid.setColumnStretch(1, 1)
        self._curve_grid.setColumnStretch(2, 2)
        self.add_curve_btn.setEnabled(len(self.line_plot_curves) < MAX_CURVES)

    def _add_curve(self):
        if len(self.line_plot_curves) < MAX_CURVES:
            self.line_plot_curves.append({"source": "image", "quantity": "mean", "area": "shown"})
            self._show_curves()
            self.schedule_preview()

    def _remove_curve(self, row):
        del self.line_plot_curves[row]
        self._show_curves()
        self.schedule_preview()

    def _set_curve_box(self, box):
        self.curve_box = tuple(int(v) for v in box) if box else None
        if self.curve_box:
            x_min, x_max, y_min, y_max = self.curve_box
            self.curve_box_label.setText(f"Box: x {x_min}–{x_max}, y {y_min}–{y_max} px")
        else:
            self.curve_box_label.setText('Box: none (for "box" curves)')

    def _choose_curve_box(self):
        self._drag_target = "box"
        self.show_status_message("Drag the line plot's box on the preview")

    def _update_grid_check(self, *_):
        wcs = self.wcs_coords_check.isChecked()
        self.show_grid_check.setEnabled(wcs)
        if not wcs:
            self.show_grid_check.setChecked(False)

    # The main window's style dialogs apply to their parent through these
    def schedule_plot(self):
        self.schedule_preview()

    def show_status_message(self, text):
        self.preview_panel.status_label.setText(text)

    def _edit_solar_disk_style(self):
        from ..dialogs.solar_disk_style import SolarDiskStyleDialog

        SolarDiskStyleDialog(self, self.solar_disk_style).exec_()

    def _edit_beam_style(self):
        from ..dialogs.beam_settings import BeamSettingsDialog

        BeamSettingsDialog(self, self.beam_style).exec_()

    def _edit_grid_style(self):
        from ..dialogs.grid_settings import GridSettingsDialog

        GridSettingsDialog(self, self.grid_style).exec_()

    def _edit_plot_settings(self):
        from ..dialogs.plot_customization import PlotCustomizationDialog

        dialog = PlotCustomizationDialog(
            self, self.plot_settings or default_style(DEFAULT_PLOT_SETTINGS)
        )
        if dialog.exec_() == QDialog.Accepted:
            self._set_plot_settings(dialog.get_settings(), "customized")

    def _set_plot_settings(self, settings, source):
        """Plot appearance ``settings`` (None: the video's own look), and
        where they came from, for the label."""
        self.plot_settings = settings
        self._plot_settings_source = source
        self.appearance_label.setText(f"Plot appearance: {source}")
        self.schedule_preview()

    def _copy_viewer_look(self, main_window, notify=False):
        """Solar disk, beam, grid and plot appearance of the main window's
        current tab."""
        tab_widget = getattr(main_window, "tab_widget", None)
        tab = tab_widget.currentWidget() if tab_widget is not None else None
        if tab is None or not hasattr(tab, "plot_settings"):
            if notify:
                QMessageBox.information(
                    self, "No Image", "Open an image in the main window first."
                )
            return
        self.solar_disk_style = dict(tab.solar_disk_style)
        self.beam_style = dict(tab.beam_style)
        self.grid_style = dict(tab.grid_style)
        self.show_solar_disk_check.setChecked(tab.show_solar_disk_checkbox.isChecked())
        # The main window draws the beam only for images that have one
        self.show_beam_check.setChecked(
            tab.show_beam_checkbox.isChecked() and bool(getattr(tab, "psf", None))
        )
        self.show_grid_check.setChecked(
            tab.show_grid_checkbox.isChecked() and self.wcs_coords_check.isChecked()
        )
        self._set_plot_settings(dict(tab.plot_settings), "from the main window")
        if notify:
            self.show_status_message(
                "Copied the solar disk, beam, grid and plot appearance from the main window"
            )

    def _create_output_tab(self, output_layout):
        """Output tab: file name, fps, quality, multiprocessing. Returns the tab's scroll area."""
        # ------ Output Tab ------
        # Create a scroll area for the output tab
        output_scroll = QScrollArea()
        output_scroll.setWidgetResizable(True)
        output_scroll_content = QWidget()
        output_layout = QVBoxLayout(output_scroll_content)
        output_layout.setContentsMargins(12, 12, 12, 12)
        output_layout.setSpacing(12)
        output_scroll.setWidget(output_scroll_content)

        # File output section
        file_group = QGroupBox("Output File")
        file_form = QFormLayout(file_group)
        file_form.setContentsMargins(12, 16, 12, 12)
        file_form.setVerticalSpacing(8)
        file_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        file_widget = QWidget()
        file_layout = QHBoxLayout(file_widget)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(8)

        self.output_file_edit = QLineEdit()
        file_layout.addWidget(self.output_file_edit)

        browse_output_btn = QPushButton("Browse")
        browse_output_btn.clicked.connect(self.browse_output_file)
        file_layout.addWidget(browse_output_btn)

        file_form.addRow("Path:", file_widget)

        self.format_combo = QComboBox()
        for text, ext in OUTPUT_FORMATS:
            self.format_combo.addItem(text, ext)
        self.format_combo.setToolTip(
            "MP4 plays almost everywhere; WebM is smaller for the same quality;\n"
            "PNG frames saves each frame as name_0001.png, name_0002.png, ..."
        )
        self.format_combo.currentIndexChanged.connect(self._on_format_changed)
        self.output_file_edit.textChanged.connect(self._sync_format_to_path)
        file_form.addRow("Format:", self.format_combo)
        output_layout.addWidget(file_group)

        # Video settings section
        video_group = QGroupBox("Video Settings")
        video_form = QFormLayout(video_group)
        video_form.setContentsMargins(12, 16, 12, 12)
        video_form.setVerticalSpacing(8)
        video_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.fps_spinbox = QSpinBox()
        self.fps_spinbox.setRange(1, 60)
        self.fps_spinbox.setValue(10)
        video_form.addRow("Frames Per Second:", self.fps_spinbox)

        self.quality_combo = QComboBox()
        self.quality_combo.addItems(list(QUALITY_CRF))
        self.quality_combo.setCurrentText("High")
        self.quality_combo.setToolTip(
            "Low: smallest files, visible blocks on fine structure\n"
            "Medium: good for sharing\n"
            "High: hard to tell from the frames themselves\n"
            "Lossless: exact pixels, large files (MP4 lossless may not play everywhere)"
        )
        video_form.addRow("Quality:", self.quality_combo)

        self.estimate_label = QLabel()
        self.estimate_label.setObjectName("SecondaryText")
        self.estimate_label.setWordWrap(True)
        video_form.addRow("Result:", self.estimate_label)
        self.fps_spinbox.valueChanged.connect(self._update_estimate)
        self.quality_combo.currentIndexChanged.connect(self._update_estimate)
        self.format_combo.currentIndexChanged.connect(self._update_estimate)
        output_layout.addWidget(video_group)

        # Performance section
        perf_group = QGroupBox("Performance")
        perf_form = QFormLayout(perf_group)
        perf_form.setContentsMargins(12, 16, 12, 12)
        perf_form.setVerticalSpacing(8)
        perf_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.multiprocessing_check = QCheckBox("Use Multiprocessing")
        self.multiprocessing_check.setChecked(True)
        perf_form.addRow("", self.multiprocessing_check)

        max_cores = multiprocessing.cpu_count()
        self.cores_spinbox = QSpinBox()
        self.cores_spinbox.setRange(1, max_cores)
        self.cores_spinbox.setValue(max(1, max_cores - 1))
        self.cores_spinbox.setEnabled(self.multiprocessing_check.isChecked())
        perf_form.addRow("CPU Cores:", self.cores_spinbox)

        self.multiprocessing_check.stateChanged.connect(
            lambda state: self.cores_spinbox.setEnabled(state == Qt.Checked)
        )

        output_layout.addWidget(perf_group)
        output_layout.addStretch()
        return output_scroll

    def _create_contours_tab(self):
        """Contours tab: the video's contour layers. Returns its scroll area."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        scroll.setWidget(content)

        info = QLabel(
            f"Up to {MAX_CONTOUR_LAYERS} contour layers, as in the main window. "
            "Each can contour the video image itself (in any Stokes), a fixed "
            "file, or a series of files: each frame then takes the file "
            "nearest to it in observation time."
        )
        info.setWordWrap(True)
        info.setObjectName("SecondaryText")
        layout.addWidget(info)

        self.contour_layers = []
        self.contour_options = default_contour_options()
        self.contour_layer_list = QListWidget()
        self.contour_layer_list.setSelectionMode(QAbstractItemView.NoSelection)
        self.contour_layer_list.setMinimumHeight(140)
        layout.addWidget(self.contour_layer_list)

        buttons = QHBoxLayout()
        edit_btn = QPushButton("Edit Layers...")
        edit_btn.setToolTip("Add, change or remove contour layers, and set the legend")
        edit_btn.clicked.connect(self._edit_contour_layers)
        buttons.addWidget(edit_btn)
        copy_btn = QPushButton("Copy from Main Window")
        copy_btn.setToolTip(
            "Use the contour layers of the image open in the main window"
        )
        copy_btn.clicked.connect(self._copy_viewer_contours)
        buttons.addWidget(copy_btn)
        clear_btn = QPushButton("Remove All")
        clear_btn.clicked.connect(lambda: self._set_contour_layers([]))
        buttons.addWidget(clear_btn)
        buttons.addStretch()
        layout.addLayout(buttons)

        levels_row = QHBoxLayout()
        levels_row.addWidget(QLabel("Levels from:"))
        self.contour_level_combo = QComboBox()
        for text, mode in (
            ("Each frame", "frame"),
            ("The first frame", "first"),
            ("All frames", "all"),
        ):
            self.contour_level_combo.addItem(text, mode)
        self.contour_level_combo.setToolTip(
            "What fraction (of the peak) and sigma (× RMS) levels are relative to.\n"
            "Each frame: its own peak / RMS, so levels change as the peak does.\n"
            "The first frame: that frame's, the same in every frame.\n"
            "All frames: the highest peak / the median RMS of all frames,\n"
            "the same in every frame. Absolute levels never change."
        )
        levels_row.addWidget(self.contour_level_combo)
        levels_row.addStretch()
        layout.addLayout(levels_row)

        layout.addStretch()
        self._show_contour_layers()
        return scroll

    def _create_frames_tab(self):
        """Frames tab: which frames make the video. Returns its scroll area."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        scroll.setWidget(content)

        form = QFormLayout()
        self.frame_start_edit = QLineEdit()
        self.frame_start_edit.setPlaceholderText(
            "YYYY-MM-DD HH:MM:SS (empty: from the first)"
        )
        form.addRow("From:", self.frame_start_edit)
        self.frame_end_edit = QLineEdit()
        self.frame_end_edit.setPlaceholderText(
            "YYYY-MM-DD HH:MM:SS (empty: to the last)"
        )
        form.addRow("To:", self.frame_end_edit)
        self.frame_every_spinbox = QSpinBox()
        self.frame_every_spinbox.setRange(1, 1000)
        self.frame_every_spinbox.setPrefix("every ")
        self.frame_every_spinbox.setSuffix(" frame(s)")
        form.addRow("Use:", self.frame_every_spinbox)
        layout.addLayout(form)

        self.frames_summary_label = QLabel()
        layout.addWidget(self.frames_summary_label)

        self._excluded_frames = set()
        self.frames_table = QTableWidget(0, 4)
        self.frames_table.setHorizontalHeaderLabels(["", "Time", "Image", "Contours"])
        self.frames_table.verticalHeader().setVisible(False)
        self.frames_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self.frames_table.horizontalHeader()
        header.setMinimumSectionSize(24)  # the app style's minimum is wide
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self.frames_table.setColumnWidth(0, 28)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setStretchLastSection(False)
        self.frames_table.setToolTip("Untick frames to leave them out of the video")
        self.frames_table.itemChanged.connect(self._on_frame_ticked)
        self.frames_table.setMinimumHeight(220)
        layout.addWidget(self.frames_table, 1)

        include_all = QPushButton("Include All Frames")
        include_all.clicked.connect(self._include_all_frames)
        layout.addWidget(include_all, 0, Qt.AlignLeft)
        return scroll

    def toggle_range_mode(self, index):
        """Enable/disable controls based on the range mode selection"""
        # Update the explanation label
        if index == 0:  # Fixed Range
            """self.range_explanation_label.setText(
                "Fixed Range: Same min/max values used for all frames"
            )"""
            # Enable min/max spinboxes
            self.vmin_spinbox.setEnabled(True)
            self.vmax_spinbox.setEnabled(True)

        elif index == 1:  # Auto Per Frame
            """self.range_explanation_label.setText(
                "Auto Per Frame: Min/max calculated independently for each frame"
            )"""
            # Disable min/max spinboxes (they'll be updated for reference only)
            self.vmin_spinbox.setEnabled(False)
            self.vmax_spinbox.setEnabled(False)

        else:  # Global Auto
            """self.range_explanation_label.setText(
                "Global Auto: Min/max calculated once from all frames"
            )"""
            # Disable min/max spinboxes
            self.vmin_spinbox.setEnabled(False)
            self.vmax_spinbox.setEnabled(False)

        # The percentile range applies to the Auto modes only
        self.lower_percentile_spinbox.setEnabled(index != 0)
        self.upper_percentile_spinbox.setEnabled(index != 0)

        # Update preview with new settings
        self.schedule_preview()

    def get_smart_start_directory(self, *extra_paths):
        """
        Get a smart starting directory for file/directory dialogs.

        Checks multiple sources in priority order:
        1. Any extra paths passed as arguments (e.g., current field value)
        2. Contour base file directory (if set)
        3. Reference image directory
        4. Input directory from Input tab
        5. Current file from main viewer
        6. Current working directory (CWD)
        7. Fall back to home directory

        Returns the first valid directory found.
        """
        candidates = list(extra_paths) + [
            self.reference_image,
            (
                self.input_directory_edit.text().strip()
                if hasattr(self, "input_directory_edit")
                else None
            ),
            self.current_file,
            os.getcwd(),  # CWD as fallback before home
        ]

        for path in candidates:
            if path:
                # If it's a file, get its directory
                if os.path.isfile(path):
                    dir_path = os.path.dirname(path)
                else:
                    dir_path = path

                if dir_path and os.path.isdir(dir_path):
                    return dir_path

        return os.path.expanduser("~")

    def browse_input_directory(self):
        """Browse for input directory"""
        start_dir = self.get_smart_start_directory(self.input_directory_edit.text())

        directory = QFileDialog.getExistingDirectory(
            self, "Select Input Directory", start_dir
        )

        if directory:
            self.input_directory_edit.setText(directory)

            # Set default output file if not already set
            if not self.output_file_edit.text():
                self.output_file_edit.setText(
                    os.path.join(directory, "output_video.mp4")
                )

            # Preview files if pattern is already set
            if self.input_pattern_edit.text():
                self.preview_input_files()

    def preview_input_files(self):
        """Find the images matching the input pattern and preview them."""
        with wait_cursor():
            if not self.input_directory_edit.text() or not self.input_pattern_edit.text():
                QMessageBox.warning(
                    self,
                    "Incomplete Input",
                    "Please specify both directory and pattern.",
                )
                return
            files, problem = self._input_files()
            if problem:
                self.files_found_label.setText(problem[0])
                self.create_btn.setEnabled(False)
                self.create_btn.setToolTip(problem[1])
                QMessageBox.warning(self, *problem)
                return

            casa = sum(is_casa_image(f) for f in files)
            self.files_found_label.setText(
                f"Found {len(files)} images matching the pattern"
                + (f" ({casa} CASA)" if casa else "")
            )
            self.create_btn.setEnabled(True)
            self.create_btn.setToolTip("")

            # The first image is previewed unless one of them already is
            if not self.reference_image or self.reference_image not in files:
                self.reference_image = files[0]
                self.reference_image_edit.setText(self.reference_image)
            self.update_preview(self.reference_image)

    def browse_reference_image(self):
        """Browse for a reference image to use for preview and settings"""
        if self.reference_image:
            start_dir = os.path.dirname(self.reference_image)
        elif self.input_directory_edit.text():
            start_dir = self.input_directory_edit.text()
        elif self.current_file:
            start_dir = os.path.dirname(self.current_file)
        else:
            start_dir = os.path.expanduser("~")

        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "Select Reference Image",
            start_dir,
            "FITS Files (*.fits *.fit);;All Files (*.*)",
        )

        if filepath:
            self.reference_image = filepath
            self.reference_image_edit.setText(filepath)
            self.update_preview(filepath)

    def browse_output_file(self):
        """Browse for output file"""
        if self.output_file_edit.text():
            start_dir = os.path.dirname(self.output_file_edit.text())
        elif self.input_directory_edit.text():
            start_dir = self.input_directory_edit.text()
        elif self.current_file:
            start_dir = os.path.dirname(self.current_file)
        else:
            start_dir = os.path.expanduser("~")

        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "Save Video As",
            start_dir,
            "MP4 Files (*.mp4);;AVI Files (*.avi);;GIF Files (*.gif);;All Files (*.*)",
        )

        if filepath:
            self.output_file_edit.setText(filepath)

    def update_preview(self, preview_file=None):
        """Show ``preview_file``'s frame (if it is one) in the preview."""
        if preview_file and preview_file in self._preview_files:
            self.preview_panel.slider.setValue(self._preview_files.index(preview_file))
        self.schedule_preview()

    def _input_files(self):
        """(files, problem): the Input tab's files in the chosen order, or
        ([], a message saying why there are none)."""
        input_dir = self.input_directory_edit.text()
        if not input_dir or not os.path.isdir(input_dir):
            return [], (
                "Invalid Directory",
                "The specified input directory does not exist.",
            )
        if not self.input_pattern_edit.text().strip():
            return [], ("No Pattern", "Enter a file pattern, e.g. *.fits or *.image")
        input_path = os.path.join(input_dir, self.input_pattern_edit.text())
        files = glob.glob(input_path)
        if not files:
            return [], ("No Files Found", f"No files match the pattern: {input_path}")
        invalid = [os.path.basename(f) for f in files if not is_image(f)]
        if invalid:
            msg = (
                f"Found {len(invalid)} files that are not images.\n"
                "FITS files and CASA images are supported."
            )
            if len(invalid) > 5:
                msg += "\n\nExamples:\n" + "\n".join(invalid[:5]) + "\n..."
            else:
                msg += "\n\nFiles:\n" + "\n".join(invalid)
            return [], ("Invalid File Extensions", msg)

        sort_method = self.sort_combo.currentText().lower()
        if sort_method == "filename":
            files.sort()
        elif sort_method == "date/time":
            # Observation time (header or file name), not file dates
            files.sort(key=observation_sort_key)
        elif sort_method == "extension":
            files.sort(key=lambda x: os.path.splitext(x)[1])
        return files, None

    def _video_options(self):
        """The settings of all tabs, as options for create_video."""
        region_options = {
            "region_enabled": self.region_enabled.isChecked(),
            "x_min": self.x_min_spinbox.value(),
            "x_max": self.x_max_spinbox.value(),
            "y_min": self.y_min_spinbox.value(),
            "y_max": self.y_max_spinbox.value(),
        }
        # Ensure proper order of min/max values
        if region_options["region_enabled"]:
            for low, high in (("x_min", "x_max"), ("y_min", "y_max")):
                region_options[low], region_options[high] = sorted(
                    (region_options[low], region_options[high])
                )
        return {
            # Display
            "stokes": self.stokes_combo.currentText(),
            "colormap": self.colormap_combo.currentText(),
            "stretch": self.stretch_combo.currentText().lower(),
            "gamma": self.gamma_spinbox.value(),
            "range_mode": self.range_mode_combo.currentIndex(),  # 0: Fixed Range, 1: Auto Per Frame, 2: Global Auto
            "vmin": self.vmin_spinbox.value(),
            "vmax": self.vmax_spinbox.value(),
            "lower_percentile": self.lower_percentile_spinbox.value(),
            "upper_percentile": self.upper_percentile_spinbox.value(),
            "colorbar": self.colorbar_check.isChecked(),
            "width": self.width_spinbox.value(),
            "height": self.height_spinbox.value(),
            "wcs_enabled": self.wcs_coords_check.isChecked(),
            "coordinates": self.coordinates_combo.currentData(),
            "coordinate_cache": self._coordinate_cache,
            "difference": self.difference_combo.currentData(),
            "track_rotation": self.track_rotation_check.isChecked(),
            # Overlays
            "timestamp": self.timestamp_check.isChecked(),
            "frame_number": self.frame_number_check.isChecked(),
            "filename": self.filename_check.isChecked(),
            "line_plot": self._line_plot_settings(),
            "show_solar_disk": self.show_solar_disk_check.isChecked(),
            "solar_disk_style": dict(self.solar_disk_style),
            "show_beam": self.show_beam_check.isChecked(),
            "beam_style": dict(self.beam_style),
            "show_grid": self.show_grid_check.isChecked(),
            "grid_style": dict(self.grid_style),
            "plot_settings": dict(self.plot_settings) if self.plot_settings else None,
            # Region
            **region_options,
            # Contours (series layers get their files per frame in _video_files)
            "contour_layers": [dict(layer) for layer in self.contour_layers],
            "contour_options": dict(self.contour_options),
            "contour_level_mode": self.contour_level_combo.currentData(),
            # Frames are drawn in worker processes, as the preview is, so
            # the app's theme doesn't change them
            "render_in_worker": True,
            # Output
            "fps": self.fps_spinbox.value(),
            "quality": self.quality_combo.currentText(),
        }

    def _frame_plan(self, layers):
        """(plan, problem): frame_plan for the Input and Frames tabs (frames
        unticked in the Frames tab included), or (None, (title, message))."""
        from datetime import datetime

        from .creator import frame_plan

        if self.image_single_radio.isChecked():
            path = self.single_image_edit.text().strip()
            if not path or not is_image(path):
                return None, ("No Image", "Choose the image shown in every frame.")
            image_files, is_series = [path], False
        else:
            image_files, problem = self._input_files()
            if problem:
                return None, problem
            is_series = True

        bounds = []
        for edit, name in (
            (self.frame_start_edit, "From"),
            (self.frame_end_edit, "To"),
        ):
            text = edit.text().strip()
            try:
                bounds.append(
                    datetime.fromisoformat(text.replace("T", " ")) if text else None
                )
            except ValueError:
                return None, ("Invalid Time", f"{name}: use YYYY-MM-DD HH:MM:SS")

        scanned = []
        for layer in layers:
            layer = dict(layer)
            if layer.get("source") == "series":
                pattern = os.path.join(
                    layer.get("series_dir", ""), layer.get("series_pattern") or "*.fits"
                )
                layer["series_files"] = sorted(
                    glob.glob(pattern), key=observation_sort_key
                )
                if layer.get("enabled", True) and not layer["series_files"]:
                    return None, ("No Contour Files", f"No files match {pattern}")
            scanned.append(layer)

        plan = frame_plan(
            image_files,
            is_series,
            scanned,
            bounds[0],
            bounds[1],
            self.frame_every_spinbox.value(),
        )
        if not plan["image_files"]:
            return None, ("No Frames", "No frames in the chosen time range.")
        return plan, None

    def _video_files(self, options):
        """(files, problem): the image shown in each frame, with the frames
        unticked in the Frames tab left out and ``options["contour_layers"]``
        given each series layer's file per frame; or ([], (title, message))."""
        plan, problem = self._frame_plan(options.get("contour_layers", []))
        if problem:
            return [], problem
        self._show_frames(plan)
        keep = [
            i
            for i, f in enumerate(plan["driver_files"])
            if f not in self._excluded_frames
        ]
        if not keep:
            return [], ("No Frames", "All frames are unticked in the Frames tab.")
        layers = []
        for layer in plan["layers"]:
            layer = dict(layer)
            if "frame_files" in layer:
                layer["frame_files"] = [layer["frame_files"][i] for i in keep]
            layer.pop("series_files", None)  # not needed by the frames
            layers.append(layer)
        options["contour_layers"] = layers
        files, difference_options = apply_difference(
            [plan["image_files"][i] for i in keep], options
        )
        options.update(difference_options)
        if not files:
            return [], ("No Frames", "A running difference needs at least two frames.")
        return files, None

    def _show_frames(self, plan):
        """List the frames of ``plan`` in the Frames tab."""
        rows = list(zip(plan["driver_files"], plan["image_files"], plan["times"]))
        if getattr(self, "_shown_frame_rows", None) == (rows, repr(plan["gaps"])):
            return
        self._shown_frame_rows = (rows, repr(plan["gaps"]))
        series = [
            l for l in plan["layers"] if l.get("enabled", True) and "frame_files" in l
        ]
        table = self.frames_table
        table.blockSignals(True)
        table.setRowCount(len(rows))
        for row, (driver, image, when) in enumerate(rows):
            tick = QTableWidgetItem()
            tick.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            tick.setCheckState(
                Qt.Unchecked if driver in self._excluded_frames else Qt.Checked
            )
            tick.setData(Qt.UserRole, driver)
            table.setItem(row, 0, tick)
            table.setItem(
                row,
                1,
                QTableWidgetItem(when.strftime("%Y-%m-%d %H:%M:%S") if when else "?"),
            )
            table.setItem(row, 2, QTableWidgetItem(os.path.basename(image)))
            pairs = []
            for layer in series:
                gap = plan["gaps"].get(layer.get("id"), [None] * len(rows))[row]
                name = os.path.basename(layer["frame_files"][row])
                pairs.append(name + (f" ({gap:+.0f} s)" if gap else ""))
            table.setItem(row, 3, QTableWidgetItem(", ".join(pairs)))
        table.setColumnHidden(3, not series)  # contours with their own files only
        table.blockSignals(False)
        used = sum(1 for d, _, _ in rows if d not in self._excluded_frames)
        self.frames_summary_label.setText(f"{used} of {len(rows)} frames in the video")

    def _on_frame_ticked(self, item):
        if item.column() != 0:
            return
        driver = item.data(Qt.UserRole)
        if item.checkState() == Qt.Checked:
            self._excluded_frames.discard(driver)
        else:
            self._excluded_frames.add(driver)
        self._shown_frame_rows = None
        self.schedule_preview()

    def _include_all_frames(self):
        self._excluded_frames.clear()
        self._shown_frame_rows = None
        self.schedule_preview()

    def _browse_single_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select the Image",
            self.get_smart_start_directory(self.single_image_edit.text()),
            "FITS Files (*.fits *.fts);;All Files (*)",
        )
        if path:
            self.single_image_edit.setText(path)

    def _browse_single_casa_image(self):
        path = QFileDialog.getExistingDirectory(
            self,
            "Select the CASA Image",
            self.get_smart_start_directory(self.single_image_edit.text()),
        )
        if path:
            if not is_casa_image(path):
                QMessageBox.warning(self, "Not a CASA Image", f"{path} is not a CASA image.")
                return
            self.single_image_edit.setText(path)

    # ---------- Output ----------

    def _on_format_changed(self, _index):
        """Give the output file the chosen format's extension."""
        path = self.output_file_edit.text().strip()
        if path:
            stem, ext = os.path.splitext(path)
            new_ext = self.format_combo.currentData()
            if ext.lower() != new_ext:
                self.output_file_edit.setText(stem + new_ext)

    def _sync_format_to_path(self, path):
        index = self.format_combo.findData(os.path.splitext(path)[1].lower())
        if index >= 0 and index != self.format_combo.currentIndex():
            self.format_combo.blockSignals(True)
            self.format_combo.setCurrentIndex(index)
            self.format_combo.blockSignals(False)
            self._update_estimate()

    def _apply_size_preset(self, index):
        height = self.size_preset_combo.itemData(index)
        self.width_spinbox.setValue(0)
        self.height_spinbox.setValue(height)

    def _update_estimate(self, *_):
        """Duration, frame count and size, and a rough file size from
        encoding the frame in the preview."""
        frames = len(self._preview_files)
        frame = getattr(self, "_last_preview_frame", None)
        if not frames or frame is None:
            self.estimate_label.setText("")
            return
        fps = self.fps_spinbox.value()
        height, width = frame.shape[:2]
        text = (
            f"{frames} frames, {_format_duration(frames / fps)} at {fps} fps, "
            f"{width}×{height} px"
        )
        size = self._estimate_size(frame, frames)
        if size:
            text += (
                f"; roughly {size / 1e6:.1f} MB"
                if size < 1e9
                else f"; roughly {size / 1e9:.1f} GB"
            )
        self.estimate_label.setText(text)

    def _estimate_size(self, frame, frames):
        """Rough output size in bytes: the preview frame encoded alone (or as
        PNG), times the frames; later video frames take about half as much,
        as solar images change little from frame to frame."""
        import tempfile

        from .creator import ensure_even_dimensions, video_writer

        ext = self.format_combo.currentData()
        options = {
            "fps": self.fps_spinbox.value(),
            "quality": self.quality_combo.currentText(),
        }
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "estimate" + ext)
                with video_writer(path, options)() as writer:
                    writer.append_data(ensure_even_dimensions(frame))
                written = [p for p in os.listdir(tmp)]
                one = sum(os.path.getsize(os.path.join(tmp, p)) for p in written)
        except Exception:
            return None
        if ext in (".png", ".gif"):
            return one * frames
        return one * (1 + 0.5 * (frames - 1))

    # ---------- Settings presets ----------

    _PRESET_WIDGETS = (
        "input_directory_edit",
        "input_pattern_edit",
        "sort_combo",
        "stokes_combo",
        "image_series_radio",
        "image_single_radio",
        "single_image_edit",
        "frame_start_edit",
        "frame_end_edit",
        "frame_every_spinbox",
        "colormap_combo",
        "stretch_combo",
        "gamma_spinbox",
        "range_mode_combo",
        "vmin_spinbox",
        "vmax_spinbox",
        "lower_percentile_spinbox",
        "upper_percentile_spinbox",
        "colorbar_check",
        "width_spinbox",
        "height_spinbox",
        "coordinates_combo",
        "wcs_coords_check",
        "difference_combo",
        "track_rotation_check",
        "region_enabled",
        "x_min_spinbox",
        "x_max_spinbox",
        "y_min_spinbox",
        "y_max_spinbox",
        "timestamp_check",
        "frame_number_check",
        "filename_check",
        "line_plot_check",
        "line_plot_x_combo",
        "line_plot_y_combo",
        "line_plot_log_check",
        "show_curve_box_check",
        "line_plot_height_spin",
        "line_plot_csv_check",
        "contour_level_combo",
        "show_solar_disk_check",
        "show_beam_check",
        "show_grid_check",
        "output_file_edit",
        "format_combo",
        "fps_spinbox",
        "quality_combo",
        "multiprocessing_check",
        "cores_spinbox",
    )

    def _settings_state(self):
        """All settings, as JSON-ready values."""
        widgets = {}
        for name in self._PRESET_WIDGETS:
            widget = getattr(self, name)
            if isinstance(widget, QLineEdit):
                widgets[name] = widget.text()
            elif isinstance(widget, QComboBox):
                widgets[name] = widget.currentText()
            elif isinstance(widget, (QCheckBox, QRadioButton)):
                widgets[name] = widget.isChecked()
            else:
                widgets[name] = widget.value()
        return {
            "widgets": widgets,
            "contour_layers": self.contour_layers,
            "contour_options": self.contour_options,
            "line_plot_curves": self.line_plot_curves,
            "curve_box": self.curve_box,
            "excluded_frames": sorted(self._excluded_frames),
            "solar_disk_style": self.solar_disk_style,
            "beam_style": self.beam_style,
            "grid_style": self.grid_style,
            "plot_settings": self.plot_settings,
            "plot_settings_source": self._plot_settings_source,
        }

    def _apply_settings_state(self, state):
        for name, value in state.get("widgets", {}).items():
            widget = getattr(self, name, None)
            if widget is None:
                continue
            if isinstance(widget, QLineEdit):
                widget.setText(value)
            elif isinstance(widget, QComboBox):
                if widget.findText(value) < 0:
                    widget.addItem(value)
                widget.setCurrentText(value)
            elif isinstance(widget, (QCheckBox, QRadioButton)):
                widget.setChecked(bool(value))
            else:
                widget.setValue(value)
        self._excluded_frames = set(state.get("excluded_frames", []))
        self._shown_frame_rows = None
        self._set_contour_layers(
            state.get("contour_layers", []), state.get("contour_options")
        )
        if "line_plot_curves" in state:
            self.line_plot_curves = [dict(c) for c in state["line_plot_curves"]]
            self._set_curve_box(state.get("curve_box"))
        elif state.get("widgets", {}).get("minmax_timeline_check"):
            # Settings saved with the min/max timeline: its peak and minimum
            self.line_plot_check.setChecked(True)
            self.line_plot_curves = [dict(c) for c in default_line_plot()["curves"]]
        self._show_curves()
        for name, defaults in (
            ("solar_disk_style", DEFAULT_SOLAR_DISK_STYLE),
            ("beam_style", DEFAULT_BEAM_STYLE),
            ("grid_style", DEFAULT_GRID_STYLE),
        ):
            setattr(self, name, dict(defaults, **(state.get(name) or {})))
        settings = state.get("plot_settings")
        self._set_plot_settings(
            settings,
            state.get("plot_settings_source", "customized")
            if settings
            else "video default",
        )
        self.schedule_preview()

    def save_settings(self):
        """Save all settings, and the frames they give, as a JSON preset:
        load it here again, or make the same video with
        python -m solar_radio_image_viewer.video.creator --preset FILE OUTPUT"""
        import json

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Video Settings",
            self.get_smart_start_directory(),
            "Video settings (*.json)",
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        state = self._settings_state()
        options = self._video_options()
        files, problem = self._video_files(options)
        if not problem:
            # The folder of converted images is this window's own
            options.pop("coordinate_cache", None)
            state["video"] = {"files": files, "options": options}
        with open(path, "w") as f:
            json.dump(state, f, indent=1, default=list)

    def load_settings(self):
        import json

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Video Settings",
            self.get_smart_start_directory(),
            "Video settings (*.json)",
        )
        if not path:
            return
        try:
            with open(path) as f:
                state = json.load(f)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Could Not Load Settings", str(e))
            return
        self._apply_settings_state(state)

    # ---------- Contour layers ----------

    def _set_contour_layers(self, layers, options=None):
        self.contour_layers = [contour_layer_settings(layer) for layer in layers]
        if options is not None:
            self.contour_options = dict(default_contour_options(), **options)
        self._show_contour_layers()
        if hasattr(self, "line_plot_curves"):
            self._show_curves()  # their sources include the layers
        self.schedule_preview()

    def _show_contour_layers(self):
        self.contour_layer_list.clear()
        kinds = {"same": "video image", "external": "fixed file", "series": "series"}
        for layer in self.contour_layers:
            state = "" if layer.get("enabled", True) else " (hidden)"
            self.contour_layer_list.addItem(
                f"{contour_layer_name(layer, 'Image')}  —  {kinds.get(layer.get('source'), '')}{state}"
            )
        if not self.contour_layers:
            self.contour_layer_list.addItem("No contours")

    def _edit_contour_layers(self):
        from ..dialogs.contour_settings import ContourSettingsDialog

        layers = self.contour_layers or [new_contour_layer()]
        dialog = ContourSettingsDialog(
            self, layers, self.contour_options, for_video=True
        )
        box = dialog.findChild(QDialogButtonBox)
        if box is not None:
            box.button(QDialogButtonBox.Ok).setText("OK")
            box.button(QDialogButtonBox.Cancel).setText("Cancel")
        if dialog.exec_() == QDialog.Accepted:
            self._set_contour_layers(dialog.get_layers(), dialog.get_options())

    def _copy_viewer_contours(self):
        tab = (
            self._main_window.tab_widget.currentWidget()
            if getattr(self._main_window, "tab_widget", None) is not None
            else None
        )
        layers = getattr(tab, "contour_layers", None)
        if not layers:
            QMessageBox.information(
                self, "No Contours", "The main window has no contour layers."
            )
            return
        enabled = tab.show_contours_checkbox.isChecked()
        self._set_contour_layers(
            [
                dict(layer, enabled=layer.get("enabled", True) and enabled)
                for layer in layers
            ],
            tab.contour_options,
        )

    def create_video(self):
        """Create a video from the selected files"""
        try:
            options = self._video_options()
            video_files, problem = self._video_files(options)
            if problem:
                QMessageBox.warning(self, *problem)
                return

            # Get output file
            output_file = self.output_file_edit.text().strip()
            if not output_file:
                QMessageBox.warning(
                    self,
                    "No Output File",
                    "Please specify an output file for the video.",
                )
                return

            # Normalize path (handle ~ and make absolute)
            output_file = os.path.abspath(os.path.expanduser(output_file))
            self.output_file_edit.setText(output_file)

            # Check if output directory is writable
            output_dir = os.path.dirname(output_file)
            if not output_dir:
                output_dir = os.getcwd()

            if not os.path.isdir(output_dir):
                try:
                    os.makedirs(output_dir)
                except OSError:
                    QMessageBox.critical(
                        self,
                        "Error",
                        f"Could not create output directory: {output_dir}",
                    )
                    return

            if not os.access(output_dir, os.W_OK):
                QMessageBox.critical(
                    self,
                    "Permission Denied",
                    f"Output directory is not writable: {output_dir}",
                )
                return

            # Confirm overwrite if file exists
            if os.path.exists(output_file):
                reply = QMessageBox.question(
                    self,
                    "Confirm Overwrite",
                    f"File already exists:\n{output_file}\n\nDo you want to overwrite it?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply == QMessageBox.No:
                    return

            # Check system resources
            # 1. Disk Space
            try:
                total, used, free = shutil.disk_usage(output_dir)
                if free < 500 * 1024 * 1024:  # Less than 500MB
                    reply = QMessageBox.warning(
                        self,
                        "Low Disk Space",
                        f"Free disk space on {output_dir} is low ({free / (1024 * 1024):.1f} MB).\nVideo creation might fail or produce incomplete files.\n\nDo you want to continue?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No,
                    )
                    if reply == QMessageBox.No:
                        return
            except Exception as e:
                print(f"Warning: Could not check disk space: {e}")

            # 2. Memory
            try:
                vm = psutil.virtual_memory()
                # Estimate memory needed: ~100MB per core overhead + frame size
                # Very rough estimate, but good for catching extreme cases
                estimated_needed = 1024 * 1024 * 1024  # 1GB base
                if self.multiprocessing_check.isChecked():
                    estimated_needed += (
                        self.cores_spinbox.value() * 200 * 1024 * 1024
                    )  # 200MB per core

                if vm.available < estimated_needed:
                    reply = QMessageBox.warning(
                        self,
                        "Low Memory",
                        f"System memory is low ({vm.available / (1024 * 1024 * 1024):.1f} GB available).\nUsing {self.cores_spinbox.value()} cores might cause system instability.\n\nDo you want to continue?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No,
                    )
                    if reply == QMessageBox.No:
                        return
            except Exception as e:
                print(f"Warning: Could not check memory: {e}")

            # Additional validation for dimensions
            for name, value in (
                ("Width", options["width"]),
                ("Height", options["height"]),
            ):
                if value > 0 and value % 2 != 0:
                    QMessageBox.warning(
                        self,
                        f"Invalid {name}",
                        f"Video {name.lower()} must be an even number.",
                    )
                    return

            options["use_multiprocessing"] = self.multiprocessing_check.isChecked()
            options["cpu_count"] = self.cores_spinbox.value()

            # Use a worker thread for video creation
            self.worker = VideoWorker(video_files, output_file, options)
            self.worker.video_created.connect(self.on_video_creation_finished)
            self.worker.error.connect(self.on_video_creation_error)
            self.worker.cancelled.connect(self.on_video_creation_cancelled)
            self.worker.progress.connect(self.on_video_progress)
            self.worker.status.connect(self._on_video_status)

            # Progress dialog: busy while preparing, then counts frames
            self.progress_dialog = QProgressDialog(
                "Preparing frames...", "Cancel", 0, 0, self
            )
            self.progress_dialog.setWindowTitle("Creating Video")
            self.progress_dialog.setWindowModality(Qt.WindowModal)
            self.progress_dialog.setMinimumDuration(0)
            self.progress_dialog.setAutoClose(False)
            self.progress_dialog.setAutoReset(False)
            self.progress_dialog.canceled.connect(self._cancel_video)
            self.progress_dialog.show()
            self._render_start = None

            # Disable the create button while processing
            self.create_btn.setEnabled(False)
            self.create_btn.setText("Creating Video...")

            # Start the worker thread
            self.worker.start()

        except Exception as e:
            self._close_progress_dialog()
            QMessageBox.critical(
                self,
                "Error",
                f"Error creating video: {str(e)}",
            )

    def _on_video_status(self, text):
        """What is being prepared, before frames are rendered."""
        dialog = getattr(self, "progress_dialog", None)
        if dialog is not None and self._render_start is None:
            dialog.setLabelText(text)

    def on_video_progress(self, done, total):
        """Show how many frames are written, and an estimate of the time left."""
        dialog = getattr(self, "progress_dialog", None)
        if dialog is None or getattr(self.worker, "is_cancelled", False):
            return
        if done <= 0 or total <= 1 and done <= 1:
            return  # still preparing (busy indicator)
        now = time.time()
        if self._render_start is None:
            # First frame: time the rest from here
            self._render_start = (now, done)
        start, start_done = self._render_start
        text = f"Rendering frame {done} of {total}"
        if done > start_done:
            remaining = (now - start) / (done - start_done) * (total - done)
            text += f" (about {_format_duration(remaining)} left)"
        dialog.setMaximum(total)
        dialog.setValue(done)
        dialog.setLabelText(text)

    def _cancel_video(self):
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.cancel()
            if getattr(self, "progress_dialog", None) is not None:
                self.progress_dialog.setLabelText("Cancelling...")

    def _close_progress_dialog(self):
        dialog = getattr(self, "progress_dialog", None)
        if dialog is not None:
            dialog.canceled.disconnect(self._cancel_video)
            dialog.close()
            self.progress_dialog = None
        self.create_btn.setEnabled(True)
        self.create_btn.setText("Create Video")

    def on_video_creation_cancelled(self):
        """The video was cancelled: nothing was written."""
        self._close_progress_dialog()
        QMessageBox.information(
            self,
            "Video Cancelled",
            "Video creation was cancelled; no file was written.",
        )

    def on_video_creation_finished(self, output_file):
        """Handle successful video creation"""
        self._close_progress_dialog()

        problems = self.worker.problems
        csv_path = lightcurve_csv_path(output_file)
        if self._line_plot_settings().get("csv") and os.path.exists(csv_path):
            output_file = f"{output_file}\nLine plot values: {csv_path}"
        if problems:
            frames = len(self.worker.files)
            lines = "\n".join(
                f"• {message} ({count} of {frames} frames)"
                for message, count in problems.items()
            )
            QMessageBox.warning(
                self,
                "Video Created, With Problems",
                f"Video created: {output_file}\n\nSome things could not be drawn:\n{lines}",
            )
            return
        QMessageBox.information(
            self,
            "Video Created",
            f"Video successfully created: {output_file}",
        )
        # self.accept()  # Keep dialog open after creation

    def on_video_creation_error(self, error_message):
        """Handle error in video creation"""
        self._close_progress_dialog()

        QMessageBox.critical(
            self,
            "Error Creating Video",
            f"Error creating video: {error_message}",
        )

    def apply_auto_minmax(self):
        """Apply Auto Min/Max preset to the display range"""
        if not self.reference_image:
            return

        data = self._load_reference()
        if data is None:
            return

        # Calculate min/max, excluding NaN values
        vmin = np.nanmin(data)
        vmax = np.nanmax(data)

        # Update spinboxes
        self.range_mode_combo.setCurrentIndex(0)  # Switch to fixed range
        self.vmin_spinbox.setValue(vmin)
        self.vmax_spinbox.setValue(vmax)

        # Update preview
        self.schedule_preview()

    def apply_auto_percentile(self):
        """Apply Auto Percentile preset to the display range"""
        if not self.reference_image:
            return

        data = self._load_reference()
        if data is None:
            return

        # Calculate 1st and 99th percentiles
        vmin, vmax = np.nanpercentile(data, [1, 99])

        # Update spinboxes
        self.range_mode_combo.setCurrentIndex(0)  # Switch to fixed range
        self.vmin_spinbox.setValue(vmin)
        self.vmax_spinbox.setValue(vmax)

        # Update preview
        self.schedule_preview()

    def apply_auto_median_rms(self):
        """Apply Auto Median ± 3×RMS preset to the display range"""
        if not self.reference_image:
            return

        data = self._load_reference()
        if data is None:
            return

        # Calculate median and RMS
        median = np.nanmedian(data)
        rms = np.sqrt(np.nanmean(np.square(data - median)))

        # Set range to median ± 3×RMS
        vmin = median - 3 * rms
        vmax = median + 3 * rms

        # Update spinboxes
        self.range_mode_combo.setCurrentIndex(0)  # Switch to fixed range
        self.vmin_spinbox.setValue(vmin)
        self.vmax_spinbox.setValue(vmax)

        # Update preview
        self.schedule_preview()

    def apply_aia_preset(self):
        """Apply AIA 171Å preset to the display"""
        # Set colormap to SDO-AIA 171
        idx = self.colormap_combo.findText("sdoaia171")
        if idx >= 0:
            self.colormap_combo.setCurrentIndex(idx)
        else:
            # Fallback to similar colormap
            idx = self.colormap_combo.findText("hot")
            if idx >= 0:
                self.colormap_combo.setCurrentIndex(idx)

        # Set stretch to log
        idx = self.stretch_combo.findText("Log")
        if idx >= 0:
            self.stretch_combo.setCurrentIndex(idx)

        # Update preview
        self.schedule_preview()

    def apply_hmi_preset(self):
        """Apply HMI preset to the display"""
        # Set colormap to gray for HMI
        idx = self.colormap_combo.findText("gray")
        if idx >= 0:
            self.colormap_combo.setCurrentIndex(idx)

        # Set stretch to linear
        idx = self.stretch_combo.findText("Linear")
        if idx >= 0:
            self.stretch_combo.setCurrentIndex(idx)

        # Update preview
        self.schedule_preview()

    def set_region_preset(self, percentage):
        """Set the region to a centered area covering the given percentage of the image.

        Parameters
        ----------
        percentage : float
            Percentage of the image to cover (0.0 to 1.0)
        """
        if not self.reference_image:
            QMessageBox.warning(
                self, "No Reference Image", "Please select a reference image first."
            )
            return

        try:
            # Load the reference image data
            data = self._load_reference()

            if data is None:
                return

            # Get image dimensions
            height, width = data.shape

            # Calculate the size of the region
            region_width = int(width * percentage)
            region_height = int(height * percentage)

            # Calculate the center of the image
            center_x = width // 2
            center_y = height // 2

            # Calculate region boundaries
            x_min = center_x - region_width // 2
            x_max = center_x + region_width // 2
            y_min = center_y - region_height // 2
            y_max = center_y + region_height // 2

            # Ensure region is within image boundaries
            x_min = max(0, x_min)
            x_max = min(width - 1, x_max)
            y_min = max(0, y_min)
            y_max = min(height - 1, y_max)

            # Update spinboxes
            self.x_min_spinbox.setValue(x_min)
            self.x_max_spinbox.setValue(x_max)
            self.y_min_spinbox.setValue(y_min)
            self.y_max_spinbox.setValue(y_max)

            # Ensure region selection is enabled
            self.region_enabled.setChecked(True)

            # Update preview
            self.schedule_preview()

        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not set region preset: {str(e)}")

    def toggle_region_controls(self, enabled):
        """Enable or disable region controls"""
        self.x_min_spinbox.setEnabled(enabled)
        self.x_max_spinbox.setEnabled(enabled)
        self.y_min_spinbox.setEnabled(enabled)
        self.y_max_spinbox.setEnabled(enabled)

    def _load_reference(self):
        """Data of the reference (previewed) image as the video shows it, in
        the video's coordinates; None (and a message) if it can't be read."""
        from .coordinates import ConversionError
        from .creator import load_frame_data

        try:
            data, _ = load_frame_data(self.reference_image, self._video_options())
        except ConversionError as e:
            QMessageBox.warning(self, "Cannot Convert", str(e))
            return None
        return data

    def _remove_coordinate_cache(self):
        import shutil

        shutil.rmtree(self._coordinate_cache, ignore_errors=True)

    def closeEvent(self, event):
        """Handle dialog close event"""
        self._play_timer.stop()
        self._preview_renderer.stop()
        # Stop worker thread if running
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.cancel()
            # Stops after the frame being rendered
            self.worker.wait(30000)
        self._remove_coordinate_cache()
        self.preview_panel.release()
        event.accept()

    def reject(self):
        """Handle dialog rejection (Cancel button or Esc key)"""
        self._play_timer.stop()
        self._preview_renderer.stop()
        # Stop worker thread if running
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.cancel()
            # Stops after the frame being rendered
            self.worker.wait(30000)
        self._remove_coordinate_cache()
        self.preview_panel.release()
        super().reject()

    def update_gamma_controls(self):
        """Enable/disable gamma controls based on the selected stretch type"""
        stretch = self.stretch_combo.currentText().lower()
        enable_gamma = stretch == "power"

        self.gamma_spinbox.setEnabled(enable_gamma)

    def _parse_levels(self, text):
        """Parse comma-separated level values from text"""
        try:
            levels = []
            for part in text.split(","):
                part = part.strip()
                if part:
                    levels.append(float(part))
            return levels
        except ValueError:
            return [0.1, 0.3, 0.5, 0.7, 0.9]  # Default levels


# Worker thread for video creation
class VideoWorker(QThread):
    """Makes the video in the background; cancel() stops it after the frame
    being written (no file is left behind)."""

    video_created = pyqtSignal(str)  # output file
    error = pyqtSignal(str)
    cancelled = pyqtSignal()
    progress = pyqtSignal(int, int)  # frames written, total frames
    status = pyqtSignal(str)  # what is being prepared

    def __init__(self, files, output_file, options):
        super().__init__()
        self.files = files
        self.output_file = output_file
        self.options = options
        self.is_cancelled = False
        # What could not be drawn: message -> number of frames
        self.problems = {}

    def run(self):
        from .creator import create_video

        def on_progress(done, total):
            self.progress.emit(done, total)
            return not self.is_cancelled

        try:
            result = create_video(
                self.files,
                self.output_file,
                self.options,
                progress_callback=on_progress,
                problems=self.problems,
                status_callback=self.status.emit,
            )
        except Exception as e:
            if self.is_cancelled:
                self.cancelled.emit()
            else:
                self.error.emit(str(e))
            return
        if result is None or self.is_cancelled:
            self.cancelled.emit()
        else:
            self.video_created.emit(result)

    def cancel(self):
        self.is_cancelled = True


if __name__ == "__main__":
    app = QApplication(sys.argv)
    dialog = VideoCreationDialog()
    dialog.show()
    sys.exit(app.exec_())

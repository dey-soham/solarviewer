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
from PyQt5.QtCore import Qt, QObject, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap
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

import matplotlib.style as mplstyle

mplstyle.use("fast")


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
    load_fits_data,
    get_norm,
    observation_sort_key,
)
from ..processing.contour_layers import (
    MAX_CONTOUR_LAYERS,
    contour_layer_name,
    contour_layer_settings,
    default_contour_options,
    new_contour_layer,
)




logger = logging.getLogger(__name__)


class PreviewRenderer(QObject):
    """Renders preview frames in a background thread, newest request first.

    Frames are made by the video code itself (prepare_video_options,
    process_image), so the preview frame is exactly the video frame. The
    shared setup (timeline, global range) and recent frames are kept, so
    stepping through frames and replaying them is quick.
    """

    rendered = pyqtSignal(int, object)  # request id, RGB frame or None
    status = pyqtSignal(int, str)  # request id, what is being done

    def __init__(self):
        super().__init__()
        self._condition = threading.Condition()
        self._request = None
        self._stopped = False
        self._prepared = OrderedDict()  # setup key -> (options, global stats)
        self._frames = OrderedDict()  # (setup key, frame index) -> frame
        self._contours = None  # FrameContours, keeping contour images read
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
                frame = self._render(*request)
            except Exception as e:
                logger.error(f"Preview failed: {e}")
                self.status.emit(request_id, f"Preview failed: {e}")
                frame = None
            self.rendered.emit(request_id, frame)

    def _render(self, request_id, files, index, options):
        from .creator import calculate_global_stats, prepare_video_options, process_image
        from .utils import FrameContours

        setup_key = repr((files, sorted((k, repr(v)) for k, v in options.items())))
        frame_key = (setup_key, index)
        if frame_key in self._frames:
            self._frames.move_to_end(frame_key)
            return self._frames[frame_key]

        prepared = self._prepared.get(setup_key)
        if prepared is None:
            if options.get("range_mode") == 2 or options.get("minmax_timeline_enabled"):
                self.status.emit(
                    request_id, f"Reading all {len(files)} frames for the range / timeline..."
                )
            prepared_options = prepare_video_options(files, dict(options))
            prepared = (prepared_options, calculate_global_stats(files, prepared_options))
            self._prepared[setup_key] = prepared
            while len(self._prepared) > 4:
                self._prepared.popitem(last=False)
        prepared_options, global_stats = prepared

        if self._contours is None:
            self._contours = FrameContours()

        self.status.emit(request_id, "Rendering...")
        frame = process_image(
            files[index],
            dict(prepared_options, current_frame=index),
            global_stats,
            frame_contours=self._contours,
            frame_idx=index,
        )
        self._frames[frame_key] = frame
        while len(self._frames) > 60:
            self._frames.popitem(last=False)
        return frame


class PreviewPanel(QWidget):
    """The video frame being previewed, with a frame slider and Play."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 16, 16, 16)
        layout.setSpacing(8)

        header = QLabel("Preview (exactly as in the video)")
        header.setStyleSheet("font-weight: 600;")
        layout.addWidget(header)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumSize(320, 240)
        self.image_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.image_label, 1)

        self.status_label = QLabel()
        self.status_label.setObjectName("SecondaryText")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

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
        self._update_pixmap()

    def show_message(self, text):
        self._pixmap = None
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
        self.preview_image = None
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

        from ..ui.styles import set_hand_cursor

        self.setup_ui()
        set_hand_cursor(self)

        # Initialize stretch controls
        self.update_gamma_controls()

        # Initialize range mode controls (for default "Auto per Frame")
        self.toggle_range_mode(self.range_mode_combo.currentIndex())

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

            # Start from the current tab's display settings
            self._copy_viewer_settings(parent)

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
                return widget.currentText() if hasattr(widget, "currentText") else widget.text()
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

    def _on_preview_rendered(self, request_id, frame):
        if request_id != self._preview_request:
            return  # a newer frame is on its way
        self._preview_shown = request_id
        panel = self.preview_panel
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
            QMessageBox.information(self, "No Image", "Open an image in the main window first.")
            return

        if imagename and imagename.lower().endswith((".fits", ".fts")):
            directory = os.path.dirname(os.path.abspath(imagename))
            self.input_directory_edit.setText(directory)
            self.input_pattern_edit.setText("*" + os.path.splitext(imagename)[1])
            self.reference_image = os.path.abspath(imagename)
            self.reference_image_edit.setText(self.reference_image)

        self._copy_viewer_settings(main_window)
        self.range_mode_combo.setCurrentIndex(0)  # the viewer's min/max
        self.wcs_coords_check.setChecked(getattr(tab, "current_wcs", None) is not None)

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
                x_min > 0.5 or y_min > 0.5 or x_max < shown_w - 1.5 or y_max < shown_h - 1.5
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
        if tab.show_contours_checkbox.isChecked() and getattr(tab, "contour_layers", None):
            self._set_contour_layers(tab.contour_layers, tab.contour_options)
        if imagename and imagename.lower().endswith((".fits", ".fts")):
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
        self.input_pattern_edit.setPlaceholderText("e.g., *.fits or *_171*.fits")
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
        self.stokes_combo.currentIndexChanged.connect(self.update_preview_settings)
        input_group_layout.addWidget(self.stokes_combo, 3, 1)

        input_layout.addWidget(input_group)

        self.single_image_group = QGroupBox("Single Image")
        single_layout = QHBoxLayout(self.single_image_group)
        self.single_image_edit = QLineEdit()
        self.single_image_edit.setPlaceholderText("FITS image shown in every frame")
        single_layout.addWidget(self.single_image_edit, 1)
        single_browse = QPushButton("Browse")
        single_browse.clicked.connect(self._browse_single_image)
        single_layout.addWidget(single_browse)
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
        self.colormap_combo.currentIndexChanged.connect(self.update_preview_settings)
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
        self.stretch_combo.currentIndexChanged.connect(self.update_preview_settings)
        self.stretch_combo.currentIndexChanged.connect(self.update_gamma_controls)
        viz_form.addRow("Stretch:", self.stretch_combo)

        # Gamma (for power stretch)
        self.gamma_spinbox = QDoubleSpinBox()
        self.gamma_spinbox.setRange(0.1, 10.0)
        self.gamma_spinbox.setSingleStep(0.1)
        self.gamma_spinbox.setValue(1.0)
        self.gamma_spinbox.valueChanged.connect(self.update_preview_settings)
        viz_form.addRow("Gamma:", self.gamma_spinbox)

        # Colorbar option
        self.colorbar_check = QCheckBox("Show Colorbar")
        self.colorbar_check.setChecked(True)
        self.colorbar_check.stateChanged.connect(self.update_preview_settings)
        viz_form.addRow("", self.colorbar_check)

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
        self.vmin_spinbox.valueChanged.connect(self.update_preview_settings)
        minmax_layout.addWidget(self.vmin_spinbox)

        minmax_layout.addWidget(QLabel("Max:"))
        self.vmax_spinbox = QDoubleSpinBox()
        self.vmax_spinbox.setRange(-1e10, 1e10)
        self.vmax_spinbox.setDecimals(2)
        self.vmax_spinbox.setValue(3000)
        self.vmax_spinbox.valueChanged.connect(self.update_preview_settings)
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
        self.lower_percentile_spinbox.valueChanged.connect(self.update_preview_settings)
        percentile_layout.addWidget(self.lower_percentile_spinbox)
        percentile_layout.addWidget(QLabel("High:"))
        self.upper_percentile_spinbox = QDoubleSpinBox()
        self.upper_percentile_spinbox.setRange(0, 100)
        self.upper_percentile_spinbox.setDecimals(1)
        self.upper_percentile_spinbox.setSingleStep(0.5)
        self.upper_percentile_spinbox.setSuffix(" %")
        self.upper_percentile_spinbox.setValue(100)
        self.upper_percentile_spinbox.valueChanged.connect(self.update_preview_settings)
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

        self.wcs_coords_check = QCheckBox("Show WCS Coordinates in Video")
        self.wcs_coords_check.setChecked(True)
        self.wcs_coords_check.setToolTip(
            "Display RA/Dec or Solar-X/Solar-Y coordinates instead of pixels"
        )
        wcs_form.addRow("", self.wcs_coords_check)

        self.wcs_info_label = QLabel("Coordinates will be detected from FITS header")
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
        self.x_min_spinbox.valueChanged.connect(self.update_region_preview)
        x_range_layout.addWidget(self.x_min_spinbox)
        x_range_layout.addWidget(QLabel("to"))
        self.x_max_spinbox = QSpinBox()
        self.x_max_spinbox.setRange(0, 10000)
        self.x_max_spinbox.setValue(1000)
        self.x_max_spinbox.valueChanged.connect(self.update_region_preview)
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
        self.y_min_spinbox.valueChanged.connect(self.update_region_preview)
        y_range_layout.addWidget(self.y_min_spinbox)
        y_range_layout.addWidget(QLabel("to"))
        self.y_max_spinbox = QSpinBox()
        self.y_max_spinbox.setRange(0, 10000)
        self.y_max_spinbox.setValue(1000)
        self.y_max_spinbox.valueChanged.connect(self.update_region_preview)
        y_range_layout.addWidget(self.y_max_spinbox)
        y_range_layout.addWidget(QLabel("px"))
        y_range_layout.addStretch()
        coord_form.addRow("Y Range:", y_range_widget)

        region_main_layout.addLayout(coord_form)

        # Interactive selection button
        select_from_preview_btn = QPushButton("Select Region from Preview...")
        select_from_preview_btn.clicked.connect(self.select_region_from_preview)
        region_main_layout.addWidget(select_from_preview_btn)

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
            "Create a video focused on a specific region of interest. "
            "The selected region will be shown with a red rectangle in the preview."
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

        # Min/Max Timeline section
        timeline_group = QGroupBox("Min/Max Timeline")
        timeline_layout = QVBoxLayout(timeline_group)
        timeline_layout.setContentsMargins(12, 16, 12, 12)
        timeline_layout.setSpacing(8)

        self.minmax_timeline_check = QCheckBox("Show Min/Max Timeline Plot")
        self.minmax_timeline_check.setChecked(False)
        self.minmax_timeline_check.setToolTip(
            "Display a continuous line plot showing min/max values for all frames"
        )
        timeline_layout.addWidget(self.minmax_timeline_check)

        # Position selector
        """position_widget = QWidget()
        position_layout = QHBoxLayout(position_widget)
        position_layout.setContentsMargins(0, 0, 0, 0)
        position_layout.setSpacing(10)

        position_layout.addWidget(QLabel("Position:"))
        self.timeline_position_combo = QComboBox()
        self.timeline_position_combo.addItems([
            "Bottom Left", "Bottom Right", "Top Left", "Top Right"
        ])
        self.timeline_position_combo.setCurrentIndex(0)
        position_layout.addWidget(self.timeline_position_combo)
        position_layout.addStretch()
        timeline_layout.addWidget(position_widget)"""

        # Data source selector (for contour mode)
        source_widget = QWidget()
        source_layout = QHBoxLayout(source_widget)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.setSpacing(10)

        source_layout.addWidget(QLabel("Data Source:"))
        self.timeline_source_combo = QComboBox()
        self.timeline_source_combo.addItems(["Colormap", "Contours"])
        self.timeline_source_combo.setCurrentIndex(0)
        self.timeline_source_combo.setToolTip(
            "Select which data to plot (applies in contour mode)"
        )
        source_layout.addWidget(self.timeline_source_combo)
        source_layout.addStretch()
        timeline_layout.addWidget(source_widget)

        # Log scale option
        self.timeline_log_scale_check = QCheckBox("Use Log Scale")
        self.timeline_log_scale_check.setChecked(False)
        self.timeline_log_scale_check.setToolTip("Plot values on logarithmic scale")
        timeline_layout.addWidget(self.timeline_log_scale_check)

        timeline_info = QLabel(
            "Plots min (blue) and max (orange) pixel values over time"
        )
        timeline_info.setObjectName("SecondaryText")
        timeline_layout.addWidget(timeline_info)

        overlay_layout.addWidget(timeline_group)

        # Add spacer to push controls to the top
        overlay_layout.addStretch()
        return overlay_scroll

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
        copy_btn.setToolTip("Use the contour layers of the image open in the main window")
        copy_btn.clicked.connect(self._copy_viewer_contours)
        buttons.addWidget(copy_btn)
        clear_btn = QPushButton("Remove All")
        clear_btn.clicked.connect(lambda: self._set_contour_layers([]))
        buttons.addWidget(clear_btn)
        buttons.addStretch()
        layout.addLayout(buttons)
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
        self.frame_start_edit.setPlaceholderText("YYYY-MM-DD HH:MM:SS (empty: from the first)")
        form.addRow("From:", self.frame_start_edit)
        self.frame_end_edit = QLineEdit()
        self.frame_end_edit.setPlaceholderText("YYYY-MM-DD HH:MM:SS (empty: to the last)")
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
        self.update_preview()

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
        """Preview the files matching the input pattern"""
        with wait_cursor():
            directory = self.input_directory_edit.text()
            pattern = self.input_pattern_edit.text()

            if not directory or not pattern:
                QMessageBox.warning(
                    self,
                    "Incomplete Input",
                    "Please specify both directory and pattern.",
                )
                return

            full_pattern = os.path.join(directory, pattern)

            # Find matching files
            files = glob.glob(full_pattern)

            if not files:
                self.files_found_label.setText("No files found matching the pattern")
                self.create_btn.setEnabled(False)
                self.create_btn.setToolTip("No files found matching the pattern")
                QMessageBox.warning(
                    self,
                    "No Files Found",
                    f"No files match the pattern: {full_pattern}",
                )
                return

            # Validate extensions
            invalid_extensions = []
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext not in [".fits", ".fts"]:
                    invalid_extensions.append(os.path.basename(f))

            if invalid_extensions:
                self.files_found_label.setText(
                    f"Found {len(files)} files, but {len(invalid_extensions)} have invalid extensions"
                )
                self.create_btn.setEnabled(False)
                self.create_btn.setToolTip("Invalid file extensions found")

                # Show first few invalid files
                msg = f"Found {len(invalid_extensions)} files with invalid extensions.\nOnly .fits and .fts files are supported."
                if len(invalid_extensions) > 5:
                    msg += (
                        "\n\nExamples:\n" + "\n".join(invalid_extensions[:5]) + "\n..."
                    )
                else:
                    msg += "\n\nFiles:\n" + "\n".join(invalid_extensions)

                QMessageBox.warning(self, "Invalid File Extensions", msg)
                return

            # Update label with file count
            self.files_found_label.setText(
                f"Found {len(files)} files matching the pattern"
            )
            self.create_btn.setEnabled(True)
            self.create_btn.setToolTip("")

            # Always update the reference list and force a preview refresh
            # Use first file as reference if no reference is set, or if we want to refresh
            if not self.reference_image or self.reference_image not in files:
                self.reference_image = files[0]
                self.reference_image_edit.setText(self.reference_image)

            # Explicitly update preview to satisfy user request "Refresh the figure, each time user presses 'preview files'"
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

    def update_preview_from_reference(self):
        """Load and update the preview from the reference image"""
        if self.reference_image:
            self.update_preview(self.reference_image)
        else:
            QMessageBox.warning(
                self, "No Reference Image", "Please select a reference image first."
            )

    def update_preview_settings(self):
        """Update the preview with new settings"""
        self.schedule_preview()

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
            return [], ("Invalid Directory", "The specified input directory does not exist.")
        input_path = os.path.join(input_dir, self.input_pattern_edit.text())
        files = glob.glob(input_path)
        if not files:
            return [], ("No Files Found", f"No files match the pattern: {input_path}")
        invalid = [
            os.path.basename(f)
            for f in files
            if os.path.splitext(f)[1].lower() not in (".fits", ".fts")
        ]
        if invalid:
            msg = (
                f"Found {len(invalid)} files with invalid extensions.\n"
                "Only .fits and .fts files are supported."
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
            # Overlays
            "timestamp": self.timestamp_check.isChecked(),
            "frame_number": self.frame_number_check.isChecked(),
            "filename": self.filename_check.isChecked(),
            "minmax_timeline_enabled": self.minmax_timeline_check.isChecked(),
            "timeline_position": 0,  # Position selector removed - using default bottom dock
            "timeline_source": self.timeline_source_combo.currentIndex(),  # 0=Colormap, 1=Contours
            "timeline_log_scale": self.timeline_log_scale_check.isChecked(),
            # Region
            **region_options,
            # Contours (series layers get their files per frame in _video_files)
            "contour_layers": [dict(layer) for layer in self.contour_layers],
            "contour_options": dict(self.contour_options),
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
            if not path or not os.path.isfile(path):
                return None, ("No Image", "Choose the image shown in every frame.")
            image_files, is_series = [path], False
        else:
            image_files, problem = self._input_files()
            if problem:
                return None, problem
            is_series = True

        bounds = []
        for edit, name in ((self.frame_start_edit, "From"), (self.frame_end_edit, "To")):
            text = edit.text().strip()
            try:
                bounds.append(datetime.fromisoformat(text.replace("T", " ")) if text else None)
            except ValueError:
                return None, ("Invalid Time", f"{name}: use YYYY-MM-DD HH:MM:SS")

        scanned = []
        for layer in layers:
            layer = dict(layer)
            if layer.get("source") == "series":
                pattern = os.path.join(
                    layer.get("series_dir", ""), layer.get("series_pattern") or "*.fits"
                )
                layer["series_files"] = sorted(glob.glob(pattern), key=observation_sort_key)
                if layer.get("enabled", True) and not layer["series_files"]:
                    return None, ("No Contour Files", f"No files match {pattern}")
            scanned.append(layer)

        plan = frame_plan(
            image_files, is_series, scanned, bounds[0], bounds[1], self.frame_every_spinbox.value()
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
        keep = [i for i, f in enumerate(plan["driver_files"]) if f not in self._excluded_frames]
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
        return [plan["image_files"][i] for i in keep], None

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
            tick.setCheckState(Qt.Unchecked if driver in self._excluded_frames else Qt.Checked)
            tick.setData(Qt.UserRole, driver)
            table.setItem(row, 0, tick)
            table.setItem(
                row, 1, QTableWidgetItem(when.strftime("%Y-%m-%d %H:%M:%S") if when else "?")
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
            text += f"; roughly {size / 1e6:.1f} MB" if size < 1e9 else f"; roughly {size / 1e9:.1f} GB"
        self.estimate_label.setText(text)

    def _estimate_size(self, frame, frames):
        """Rough output size in bytes: the preview frame encoded alone (or as
        PNG), times the frames; later video frames take about half as much,
        as solar images change little from frame to frame."""
        import tempfile

        from .creator import ensure_even_dimensions, video_writer

        ext = self.format_combo.currentData()
        options = {"fps": self.fps_spinbox.value(), "quality": self.quality_combo.currentText()}
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
        "input_directory_edit", "input_pattern_edit", "sort_combo", "stokes_combo",
        "image_series_radio", "image_single_radio", "single_image_edit",
        "frame_start_edit", "frame_end_edit", "frame_every_spinbox",
        "colormap_combo", "stretch_combo", "gamma_spinbox", "range_mode_combo",
        "vmin_spinbox", "vmax_spinbox", "lower_percentile_spinbox",
        "upper_percentile_spinbox", "colorbar_check", "width_spinbox", "height_spinbox",
        "wcs_coords_check", "region_enabled", "x_min_spinbox", "x_max_spinbox",
        "y_min_spinbox", "y_max_spinbox", "timestamp_check", "frame_number_check",
        "filename_check", "minmax_timeline_check", "timeline_source_combo",
        "timeline_log_scale_check", "output_file_edit", "format_combo", "fps_spinbox",
        "quality_combo", "multiprocessing_check", "cores_spinbox",
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
            "excluded_frames": sorted(self._excluded_frames),
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
        self._set_contour_layers(state.get("contour_layers", []), state.get("contour_options"))
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
        dialog = ContourSettingsDialog(self, layers, self.contour_options, for_video=True)
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
            QMessageBox.information(self, "No Contours", "The main window has no contour layers.")
            return
        enabled = tab.show_contours_checkbox.isChecked()
        self._set_contour_layers(
            [dict(layer, enabled=layer.get("enabled", True) and enabled) for layer in layers],
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
                        f"Free disk space on {output_dir} is low ({free / (1024*1024):.1f} MB).\nVideo creation might fail or produce incomplete files.\n\nDo you want to continue?",
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
                        f"System memory is low ({vm.available / (1024*1024*1024):.1f} GB available).\nUsing {self.cores_spinbox.value()} cores might cause system instability.\n\nDo you want to continue?",
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
                        self, f"Invalid {name}", f"Video {name.lower()} must be an even number."
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

            # Progress dialog: busy while preparing, then counts frames
            self.progress_dialog = QProgressDialog("Preparing frames...", "Cancel", 0, 0, self)
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
            self, "Video Cancelled", "Video creation was cancelled; no file was written."
        )

    def on_video_creation_finished(self, output_file):
        """Handle successful video creation"""
        self._close_progress_dialog()

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

    @wait_cursor()
    def select_region_from_preview(self, *args):
        """Let the user select a region from the preview image"""
        if not self.reference_image:
            QMessageBox.warning(
                self, "No Preview", "Please load a reference image first."
            )
            return

        try:
            # Enable region selection
            self.region_enabled.setChecked(True)

            # Create a separate figure/canvas for selection to avoid parenting issues
            from matplotlib.figure import Figure as MplFigure
            from matplotlib.backends.backend_qt5agg import (
                FigureCanvasQTAgg as MplCanvas,
            )

            selection_figure = MplFigure(figsize=(5, 4), dpi=100)
            selection_canvas = MplCanvas(selection_figure)
            ax = selection_figure.add_subplot(111)

            # Load data
            data, _ = load_fits_data(
                self.reference_image, stokes=self.stokes_combo.currentText()
            )

            if data is None:
                return

            # Display image for selection
            stretch = self.stretch_combo.currentText().lower()
            gamma = self.gamma_spinbox.value()
            cmap = self.colormap_combo.currentText()

            # Determine vmin/vmax
            if self.range_mode_combo.currentIndex() == 0:  # Fixed
                vmin = self.vmin_spinbox.value()
                vmax = self.vmax_spinbox.value()
            else:  # Auto
                vmin = np.nanpercentile(data, 0)
                vmax = np.nanpercentile(data, 100)

            # Create normalization
            norm = get_norm(stretch, vmin, vmax, gamma)

            # Display the image
            ax.imshow(
                data, cmap=cmap, norm=norm, origin="lower", interpolation="nearest"
            )

            ax.set_title("Click and drag to select region", fontsize=10)

            # Add interactive rectangle selector
            from matplotlib.widgets import RectangleSelector

            def onselect(eclick, erelease):
                """Handle region selection event"""
                # Get coordinates in data space
                x1, y1 = eclick.xdata, eclick.ydata
                x2, y2 = erelease.xdata, erelease.ydata

                # Check for None (outside axes)
                if None in (x1, y1, x2, y2):
                    return

                # Ensure proper min/max
                x_min, x_max = int(min(x1, x2)), int(max(x1, x2))
                y_min, y_max = int(min(y1, y2)), int(max(y1, y2))

                # Update spinboxes with selected region
                self.x_min_spinbox.setValue(max(0, x_min))
                self.x_max_spinbox.setValue(min(data.shape[1] - 1, x_max))
                self.y_min_spinbox.setValue(max(0, y_min))
                self.y_max_spinbox.setValue(min(data.shape[0] - 1, y_max))

                # We don't update the main preview here to keep things fast/safe
                # The main preview will update when the dialog closes via the changed spinboxes if needed
                # or we can update it explicitly at the end

            # Draw rectangle selector
            rect_selector = RectangleSelector(
                ax,
                onselect,
                useblit=True,
                button=[1],  # Left mouse button only
                minspanx=5,
                minspany=5,
                spancoords="pixels",
                interactive=True,
                props=dict(facecolor="none", edgecolor="red", linewidth=2),
            )

            # Need to keep a reference to prevent garbage collection
            self._rect_selector = rect_selector

            # Show message
            """status_text = ax.text(
                0.5,
                0.02,
                "Click and drag to select region, then close this window",
                transform=ax.transAxes,
                ha="center",
                va="bottom",
                bbox=dict(boxstyle="round", fc="white", alpha=0.8),
            )"""

            # Refresh canvas
            selection_canvas.draw()

            # Create a modal dialog to use for selection
            selector_dialog = QDialog(self)
            selector_dialog.setWindowTitle("Select Region")
            selector_layout = QVBoxLayout(selector_dialog)

            # Add the canvas to the dialog
            from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT

            toolbar = NavigationToolbar2QT(selection_canvas, selector_dialog)
            selector_layout.addWidget(toolbar)
            selector_layout.addWidget(selection_canvas)

            # Add instructions
            instructions = QLabel(
                "Click and drag to select a region. Use toolbar to pan/zoom if needed. "
                "Close this dialog when finished."
            )
            instructions.setWordWrap(True)
            selector_layout.addWidget(instructions)

            # Add done button
            done_btn = QPushButton("Done")
            done_btn.clicked.connect(selector_dialog.accept)
            selector_layout.addWidget(done_btn)

            # Set a reasonable size
            selector_dialog.resize(800, 600)

            # Execute dialog
            selector_dialog.exec_()

            # Clean up selector to break circular references
            self._rect_selector = None

            # Update the preview after dialog closes
            self.update_preview()

        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not select region: {str(e)}")

    def apply_auto_minmax(self):
        """Apply Auto Min/Max preset to the display range"""
        if not self.reference_image:
            return

        data, _ = load_fits_data(
            self.reference_image, stokes=self.stokes_combo.currentText()
        )
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
        self.update_preview()

    def apply_auto_percentile(self):
        """Apply Auto Percentile preset to the display range"""
        if not self.reference_image:
            return

        data, _ = load_fits_data(
            self.reference_image, stokes=self.stokes_combo.currentText()
        )
        if data is None:
            return

        # Calculate 1st and 99th percentiles
        vmin = np.nanpercentile(data, 1)
        vmax = np.nanpercentile(data, 99)

        # Update spinboxes
        self.range_mode_combo.setCurrentIndex(0)  # Switch to fixed range
        self.vmin_spinbox.setValue(np.nanpercentile(data, 1))
        self.vmax_spinbox.setValue(np.nanpercentile(data, 99))

        # Update preview
        self.update_preview()

    def apply_auto_median_rms(self):
        """Apply Auto Median ± 3×RMS preset to the display range"""
        if not self.reference_image:
            return

        data, _ = load_fits_data(
            self.reference_image, stokes=self.stokes_combo.currentText()
        )
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
        self.update_preview()

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
        self.update_preview()

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
        self.update_preview()

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
            data, _ = load_fits_data(
                self.reference_image, stokes=self.stokes_combo.currentText()
            )

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
            self.update_preview()

        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not set region preset: {str(e)}")

    def toggle_region_controls(self, enabled):
        """Enable or disable region controls"""
        self.x_min_spinbox.setEnabled(enabled)
        self.x_max_spinbox.setEnabled(enabled)
        self.y_min_spinbox.setEnabled(enabled)
        self.y_max_spinbox.setEnabled(enabled)
        self.update_region_preview()

    def update_region_preview(self):
        """Update the preview when region controls change"""
        if self.region_enabled.isChecked():
            self.update_preview()

    def closeEvent(self, event):
        """Handle dialog close event"""
        self._play_timer.stop()
        self._preview_renderer.stop()
        # Stop worker thread if running
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.cancel()
            # Stops after the frame being rendered
            self.worker.wait(30000)
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

    def __init__(self, files, output_file, options):
        super().__init__()
        self.files = files
        self.output_file = output_file
        self.options = options
        self.is_cancelled = False

    def run(self):
        from .creator import create_video

        def on_progress(done, total):
            self.progress.emit(done, total)
            return not self.is_cancelled

        try:
            result = create_video(
                self.files, self.output_file, self.options, progress_callback=on_progress
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

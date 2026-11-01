#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
The Create Video window. Its tabs, preview and the rest are in video.ui;
their names can still be imported from here.
"""

import glob
import logging
import os
import shutil
import sys
import threading
import time

import psutil
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QMessageBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..processing.plot_style import (
    DEFAULT_BEAM_STYLE,
    DEFAULT_GRID_STYLE,
    DEFAULT_SOLAR_DISK_STYLE,
    default_style,
)
from .lightcurve import lightcurve_csv_path
from ..ui.background import Cancelled
from ..ui.errors import show_error
from ..ui.free_dialogs import free_modal_dialogs

# The window is put together from these (moved here from this module,
# whose names still import from it)
from .ui.preview_control import PreviewMixin
from .ui.main_window import MainWindowMixin
from .ui.input_tab import InputTabMixin
from .ui.contours_tab import ContoursTabMixin
from .ui.frames_tab import FramesTabMixin
from .ui.display_tab import DisplayTabMixin
from .ui.region_tab import RegionTabMixin
from .ui.overlays_tab import OverlaysTabMixin
from .ui.output_tab import OutputTabMixin
from .ui.presets import PresetsMixin
from .ui.remote_source import RemoteSourceMixin
from .ui.helpers import (
    OUTPUT_FORMATS,
    SIZE_PRESETS,
    _format_duration,
    is_image,
    viewer_image,
)
from .ui.planner import FramePlanner
from .ui.preview import (
    PreviewRenderer,
    PreviewImage,
    frame_point_to_region,
    zoom_region,
    PreviewPanel,
)
from .ui.progress import WorkDialog
from .ui.worker import VideoWorker

logger = logging.getLogger(__name__)


def _first_output_file(output_file):
    """The file making ``output_file`` starts: PNG frames are numbered."""
    stem, ext = os.path.splitext(output_file)
    return f"{stem}_0001.png" if ext.lower() == ".png" else output_file


__all__ = [
    "VideoCreationDialog",
    "OUTPUT_FORMATS",
    "SIZE_PRESETS",
    "_format_duration",
    "is_image",
    "viewer_image",
    "PreviewRenderer",
    "PreviewImage",
    "frame_point_to_region",
    "zoom_region",
    "PreviewPanel",
    "VideoWorker",
]


from ..ui.widgets import button_bar
from ..ui.geometry import fit_to_screen


class VideoCreationDialog(
    PreviewMixin,
    MainWindowMixin,
    InputTabMixin,
    ContoursTabMixin,
    FramesTabMixin,
    DisplayTabMixin,
    RegionTabMixin,
    OverlaysTabMixin,
    OutputTabMixin,
    PresetsMixin,
    RemoteSourceMixin,
    QDialog,
):
    """
    Dialog for creating videos from FITS files
    """

    def __init__(self, parent=None, current_file=None):
        super().__init__(parent)
        # Force window behavior to ensure maximize button works
        self.setWindowFlags(
            Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint
        )
        free_modal_dialogs(self)  # (its settings, browsers... move on their own)
        self._main_window = parent
        self.current_file = current_file
        self.progress_tracker = None
        self.reference_image = None

        self.setWindowTitle("Create video")
        fit_to_screen(self, 1320, 800)

        # Preview: rendered in the background, shortly after settings change
        self._preview_files = []
        self._preview_request = 0
        self._preview_shown = 0
        self._preview_renderer = PreviewRenderer()
        self._preview_renderer.rendered.connect(self._on_preview_rendered)
        self._preview_renderer.status.connect(self._on_preview_status)
        # Which frames: planned in the background, too
        self._planner = FramePlanner()
        self._planner.planned.connect(self._on_planned)
        self._planner.status.connect(self._on_plan_status)
        self._plan = None
        self._plan_request = 0
        self._plan_outdated = True
        self._scanning = False
        self._closed = False
        self._after_video = None  # closes the window, when the video has stopped
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

        self._coordinate_cache = tempfile.mkdtemp(prefix=f"solarviewer-video-p{os.getpid()}-")

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

        # The main window shows an image of a server: that server's images
        remote_reference = (
            self._take_main_window_source(parent.tab_widget.currentWidget())
            if current_file and parent is not None and hasattr(parent, "tab_widget")
            else None
        )
        if remote_reference:
            self.output_file_edit.setText(
                os.path.join(os.path.expanduser("~"), "output_video.mp4")
            )
            self._copy_viewer_settings(parent)
            self._copy_viewer_look(parent)
            current_file = None

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
        elif not remote_reference:
            # No file provided - use CWD as default
            cwd = os.getcwd()
            if os.path.isdir(cwd):
                self.input_directory_edit.setText(cwd)
                self.output_file_edit.setText(os.path.join(cwd, "output_video.mp4"))
                # Check if CWD has any FITS files to set pattern
                fits_in_cwd = glob.glob(os.path.join(glob.escape(cwd), "*.fits")) + glob.glob(
                    os.path.join(glob.escape(cwd), "*.fts")
                )
                if fits_in_cwd:
                    self.input_pattern_edit.setText("*.fits")
                    # Auto-scan for files
                    self.preview_input_files()

        # Update preview if we have a valid reference image
        if self.reference_image:
            self.update_preview(self.reference_image)


    # ---------- Preview ----------


    # ---------- Region from the preview (drag, zoom buttons) ----------


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
        self.preview_panel.render_all_button.clicked.connect(self._render_all_frames)
        self.preview_panel.stop_fetch_button.clicked.connect(self._stop_render_all)
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

        # All seven tabs fit the pane: shrunk and elided rather than scrolled
        self.tab_widget.setUsesScrollButtons(False)
        self.tab_widget.setElideMode(Qt.ElideRight)
        main_layout.addWidget(self.tab_widget)

        box, buttons = button_bar(
            primary=("Create video", self.create_video),
            secondary=("Cancel", self.reject),
            extras=[
                ("Save settings…", self.save_settings),
                ("Load settings…", self.load_settings),
            ],
        )
        buttons["Save settings…"].setToolTip(
            "Save all settings (and the frames) as a preset file"
        )
        self.cancel_btn = buttons["Cancel"]
        self.create_btn = buttons["Create video"]
        self.create_btn.setEnabled(False)
        main_layout.addWidget(box)

        self._watch_settings()


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
            "annotations": (
                self.annotations if self.show_annotations_check.isChecked() else None
            ),
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


    # ---------- Output ----------


    # ---------- Settings presets ----------


    # ---------- Contour layers ----------


    def create_video(self):
        """Create a video from the selected files"""
        try:
            options = self._video_options()
            try:
                video_files, problem = self._video_files(options)
            except Cancelled:
                self.preview_panel.show_idle("Cancelled")
                return
            if problem:
                QMessageBox.warning(self, *problem)
                return

            # Get output file
            output_file = self.output_file_edit.text().strip()
            if not output_file:
                QMessageBox.warning(
                    self,
                    "No output file",
                    "Enter an output file for the video on the Output tab.",
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
                except OSError as e:
                    show_error(
                        self,
                        "Could not create the folder",
                        f"Could not create {output_dir}. Choose another output folder.",
                        e,
                    )
                    return

            if not os.access(output_dir, os.W_OK):
                show_error(
                    self,
                    "Folder not writable",
                    f"Cannot write to {output_dir}. Choose another output folder.",
                )
                return

            # Confirm overwrite if file exists
            first_file = _first_output_file(output_file)
            if os.path.exists(first_file):
                reply = QMessageBox.question(
                    self,
                    "Overwrite the file?",
                    f"File already exists:\n{first_file}\n\nDo you want to overwrite it?",
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
                        "Low disk space",
                        f"Free disk space on {output_dir} is low ({free / (1024 * 1024):.1f} MB).\nVideo creation might fail or produce incomplete files.\n\nDo you want to continue?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No,
                    )
                    if reply == QMessageBox.No:
                        return
            except Exception as e:
                logger.warning(f"Could not check disk space: {e}")

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
                        "Low memory",
                        f"System memory is low ({vm.available / (1024 * 1024 * 1024):.1f} GB available).\nUsing {self.cores_spinbox.value()} cores might cause system instability.\n\nDo you want to continue?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No,
                    )
                    if reply == QMessageBox.No:
                        return
            except Exception as e:
                logger.warning(f"Could not check memory: {e}")

            # Additional validation for dimensions
            for name, value in (
                ("Width", options["width"]),
                ("Height", options["height"]),
            ):
                if value > 0 and value % 2 != 0:
                    QMessageBox.warning(
                        self,
                        f"Invalid {name.lower()}",
                        f"Video {name.lower()} must be an even number.",
                    )
                    return

            options["use_multiprocessing"] = self.multiprocessing_check.isChecked()
            options["cpu_count"] = self.cores_spinbox.value()

            # Use a worker thread for video creation
            self.worker = VideoWorker(video_files, output_file, options, remote=self.remote)
            self.worker.video_created.connect(self.on_video_creation_finished)
            self.worker.error.connect(self.on_video_creation_error)
            self.worker.cancelled.connect(self.on_video_creation_cancelled)
            self.worker.progress.connect(self.on_video_progress)
            self.worker.status.connect(self._on_video_status)
            self.worker.finished.connect(self._on_worker_ended)

            # Progress dialog: busy while preparing, then counts frames
            self.progress_dialog = WorkDialog(
                "Creating video",
                "Preparing frames…",
                self,
                cancelling_text="Cancelling… (this finishes the frames being drawn first)",
            )
            self.progress_dialog.cancel_requested.connect(self._cancel_video)
            self.progress_dialog.show()
            self._render_start = None

            # Disable the create button while processing
            self.create_btn.setEnabled(False)
            self.create_btn.setText("Creating video…")

            # Start the worker thread
            self.worker.start()

        except Exception as e:
            self._close_progress_dialog()
            show_error(
                self,
                "Could not create the video",
                "The video could not be started. Check the settings and try again.",
                e,
            )

    def _on_video_status(self, text, done, total):
        """What is being done before frames are rendered, and after."""
        dialog = getattr(self, "progress_dialog", None)
        if dialog is not None:
            dialog.set_text(text)
            dialog.set_progress(done, total)

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
        dialog.set_progress(done, total)
        dialog.set_text(text)

    def _making_video(self):
        return getattr(self, "progress_dialog", None) is not None

    def _cancel_video(self):
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.cancel()

    def _close_progress_dialog(self):
        dialog = getattr(self, "progress_dialog", None)
        if dialog is not None:
            dialog.finish()
            self.progress_dialog = None
        self.create_btn.setEnabled(True)
        self.create_btn.setText("Create video")

    def on_video_creation_cancelled(self):
        """The video was cancelled: nothing was written."""
        self._close_progress_dialog()
        if self._after_video is not None:
            return  # the window is closing: that is what was asked for
        QMessageBox.information(
            self,
            "Video cancelled",
            "Video creation was cancelled; no file was written.",
        )

    def on_video_creation_finished(self, output_file):
        """Handle successful video creation"""
        self._close_progress_dialog()

        problems = self.worker.problems
        csv_path = lightcurve_csv_path(output_file)
        output_file = _first_output_file(output_file)
        if self._line_plot_settings().get("csv") and os.path.exists(csv_path):
            output_file = f"{output_file}\nLine plot values: {csv_path}"
        if problems:
            frames = len(self.worker.files)
            lines = "\n".join(
                f"• {message} ({count} of {frames} frames)"
                for message, count in problems.items()
            )
            QMessageBox.warning(
                self._message_parent(),
                "Video created, with problems",
                f"Video created: {output_file}\n\nSome things could not be drawn:\n{lines}",
            )
            return
        QMessageBox.information(
            self._message_parent(),
            "Video created",
            f"Video successfully created: {output_file}",
        )
        # self.accept()  # Keep dialog open after creation

    def on_video_creation_error(self, error_message):
        """Handle error in video creation"""
        self._close_progress_dialog()

        show_error(
            self._message_parent(),
            "Could not create the video",
            "The video was not finished. Check the settings and try again.",
            error_message,
        )


    def _message_parent(self):
        """Over this window, unless it closes as soon as the video has ended."""
        return self._main_window if self._after_video is not None else self

    def _on_worker_ended(self):
        if self._after_video is not None:
            self._after_video()

    def _stopping_video(self, then):
        """If a video is being made, have it stop and return True: ``then``
        runs when it has. Stopping takes until the frames being rendered are
        done, which the window must not freeze for, and the progress dialog
        says it is cancelling."""
        worker = getattr(self, "worker", None)
        if worker is None or not worker.isRunning():
            return False
        self._after_video = then
        if self._making_video():
            self.progress_dialog.reject()
        worker.cancel()
        return True

    def _release(self):
        """Stop what works for this window, without waiting for any of it."""
        if self._closed:
            return
        self._closed = True
        self._preview_timer.stop()
        self._play_timer.stop()
        self._estimate_timer.stop()
        self._preview_renderer.stop()
        self._planner.stop()
        self.preview_panel.release()
        remote, cache = self.remote, self._coordinate_cache

        def clean_up():
            shutil.rmtree(cache, ignore_errors=True)
            if remote is not None:
                remote.close()  # its SFTP channels (the connection stays)

        # (not a daemon: the folder goes also when the application is ending)
        threading.Thread(target=clean_up, name="video-window-clean-up").start()

    def closeEvent(self, event):
        """Handle dialog close event"""
        if self._stopping_video(self.close):
            event.ignore()
            return
        self._release()
        event.accept()

    def reject(self):
        """Handle dialog rejection (Cancel button or Esc key)"""
        if self._stopping_video(self.reject):
            return
        self._release()
        super().reject()


if __name__ == "__main__":
    from ..ui.app import create_app

    app = create_app()
    dialog = VideoCreationDialog()
    dialog.show()
    sys.exit(app.exec_())

"""
The preview: rendering requests, playing, and choosing the region on it.
"""

import os
from PyQt5.QtWidgets import (
    QLineEdit,
    QComboBox,
    QCheckBox,
    QSpinBox,
    QDoubleSpinBox,
    QWidget,
    QRadioButton,
)
from .preview import (
    frame_point_to_region,
    zoom_region,
)


class PreviewMixin:
    """The preview: rendering requests, playing, and choosing the region on it. Mixed into VideoCreationDialog."""

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
            # Other frames: fetch only the sample until asked for all again
            self._preview_renderer.render_all = False
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
        self._update_remote_preview()
        height, width = frame.shape[:2]
        panel.status_label.setText(
            f"{os.path.basename(self._preview_files[panel.slider.value()])} · "
            f"frame size {width}×{height}"
        )

    def _update_remote_preview(self):
        """For images on a server: how many frames the preview has, and Play
        only once all are fetched."""
        panel = self.preview_panel
        renderer = self._preview_renderer
        if self.remote is None:
            panel.show_remote(False)
            panel.play_button.setEnabled(True)
            return
        fetched = sum(bool(self.remote.cached(f)) for f in self._preview_files)
        panel.show_remote(True, renderer.all_fetched, fetched, len(self._preview_files))
        panel.play_button.setEnabled(renderer.all_fetched)
        panel.render_all_button.setEnabled(not renderer.render_all)
        panel.play_button.setToolTip(
            "Play the preview at the video's frame rate"
            if renderer.all_fetched
            else "Render All Frames first, to play the preview"
        )

    def _render_all_frames(self):
        """Fetch every frame's images, then preview (and play) all of them."""
        self._preview_renderer.render_all = True
        self.preview_panel.render_all_button.setEnabled(False)
        self.schedule_preview(delay=0)

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

    def update_preview(self, preview_file=None):
        """Show ``preview_file``'s frame (if it is one) in the preview."""
        if preview_file and preview_file in self._preview_files:
            self.preview_panel.slider.setValue(self._preview_files.index(preview_file))
        self.schedule_preview()

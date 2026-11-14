"""
Output tab: file, format, quality and the size estimate.
"""

import os
from PyQt5.QtWidgets import (
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFileDialog,
    QGroupBox,
    QComboBox,
    QCheckBox,
    QSpinBox,
    QScrollArea,
    QWidget,
    QFormLayout,
)
from PyQt5.QtCore import Qt, QTimer
import multiprocessing
from ..creator import (
    QUALITY_CRF,
)
from .helpers import (
    OUTPUT_FORMATS,
    _format_duration,
    hint,
)
from .progress import Job
from ...ui.widgets import PathEdit


def encoded_size(frame, ext, options):
    """Bytes of ``frame`` encoded alone in format ``ext`` (or None). Runs a
    video encoder, so it takes up to a second: not for the GUI thread."""
    import tempfile

    from ..creator import ensure_even_dimensions, video_writer

    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "estimate" + ext)
            with video_writer(path, options)() as writer:
                writer.append_data(ensure_even_dimensions(frame))
            return sum(os.path.getsize(os.path.join(tmp, p)) for p in os.listdir(tmp))
    except Exception:
        return None


class OutputTabMixin:
    """Output tab: file, format, quality and the size estimate. Mixed into VideoCreationDialog."""

    def _create_output_tab(self, output_layout):
        """Output tab: file name, fps, quality, multiprocessing. Returns the tab's scroll area."""
        # ------ Output Tab ------
        # Create a scroll area for the output tab
        output_scroll = QScrollArea()
        output_scroll.setWidgetResizable(True)
        output_scroll_content = QWidget()
        output_layout = QVBoxLayout(output_scroll_content)
        output_layout.setContentsMargins(0, 12, 8, 12)
        output_layout.setSpacing(12)
        output_scroll.setWidget(output_scroll_content)

        # File output section
        file_group = QGroupBox("Output file")
        file_form = QFormLayout(file_group)
        file_form.setContentsMargins(12, 4, 12, 4)
        file_form.setVerticalSpacing(8)
        file_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        file_widget = QWidget()
        file_layout = QHBoxLayout(file_widget)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(8)

        self.output_file_edit = PathEdit()
        file_layout.addWidget(self.output_file_edit)

        browse_output_btn = QPushButton("Browse…")
        browse_output_btn.clicked.connect(self.browse_output_file)
        file_layout.addWidget(browse_output_btn)

        file_form.addRow(hint("Path"), file_widget)

        self.format_combo = QComboBox()
        for text, ext in OUTPUT_FORMATS:
            self.format_combo.addItem(text, ext)
        self.format_combo.setToolTip(
            "MP4 plays almost everywhere; WebM is smaller for the same quality;\n"
            "PNG frames saves each frame as name_0001.png, name_0002.png, …"
        )
        self.format_combo.currentIndexChanged.connect(self._on_format_changed)
        self.output_file_edit.textChanged.connect(self._sync_format_to_path)
        file_form.addRow(hint("Format"), self.format_combo)
        output_layout.addWidget(file_group)

        # Video settings section
        video_group = QGroupBox("Video settings")
        video_form = QFormLayout(video_group)
        video_form.setContentsMargins(12, 4, 12, 4)
        video_form.setVerticalSpacing(8)
        video_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.fps_spinbox = QSpinBox()
        self.fps_spinbox.setRange(1, 60)
        self.fps_spinbox.setValue(10)
        video_form.addRow(hint("Frames per second"), self.fps_spinbox)

        self.quality_combo = QComboBox()
        self.quality_combo.addItems(list(QUALITY_CRF))
        self.quality_combo.setCurrentText("High")
        self.quality_combo.setToolTip(
            "Low: smallest files, visible blocks on fine structure\n"
            "Medium: good for sharing\n"
            "High: hard to tell from the frames themselves\n"
            "Lossless: exact pixels, large files (MP4 lossless may not play everywhere)"
        )
        video_form.addRow(hint("Quality"), self.quality_combo)

        self.estimate_label = QLabel()
        self.estimate_label.setObjectName("SecondaryText")
        self.estimate_label.setWordWrap(True)
        video_form.addRow(hint("Result"), self.estimate_label)
        self._estimate = None  # (settings, frame, bytes of that frame alone)
        self._estimate_job = None
        self._estimate_timer = QTimer(self)
        self._estimate_timer.setSingleShot(True)
        self._estimate_timer.setInterval(400)
        self._estimate_timer.timeout.connect(self._start_estimate)
        self.fps_spinbox.valueChanged.connect(self._update_estimate)
        self.quality_combo.currentIndexChanged.connect(self._update_estimate)
        self.format_combo.currentIndexChanged.connect(self._update_estimate)
        output_layout.addWidget(video_group)

        # Performance section
        perf_group = QGroupBox("Performance")
        perf_form = QFormLayout(perf_group)
        perf_form.setContentsMargins(12, 4, 12, 4)
        perf_form.setVerticalSpacing(8)
        perf_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.multiprocessing_check = QCheckBox("Use multiprocessing")
        self.multiprocessing_check.setChecked(True)
        perf_form.addRow(hint(""), self.multiprocessing_check)

        max_cores = multiprocessing.cpu_count()
        self.cores_spinbox = QSpinBox()
        self.cores_spinbox.setRange(1, max_cores)
        self.cores_spinbox.setValue(max(1, max_cores - 1))
        self.cores_spinbox.setEnabled(self.multiprocessing_check.isChecked())
        perf_form.addRow(hint("CPU cores"), self.cores_spinbox)

        self.multiprocessing_check.stateChanged.connect(
            lambda state: self.cores_spinbox.setEnabled(state == Qt.Checked)
        )

        output_layout.addWidget(perf_group)
        output_layout.addStretch()
        return output_scroll

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
            "Save video as",
            start_dir,
            "MP4 files (*.mp4);;WebM files (*.webm);;GIF files (*.gif);;AVI files (*.avi);;"
            "PNG frames (*.png);;All files (*.*)",
        )

        if filepath:
            self.output_file_edit.setText(filepath)

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
        encoding the frame in the preview, which is done in the background
        once the settings and frame stay put (so not while playing)."""
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
        settings = self._estimate_settings()
        if self._estimate is not None and self._estimate[0] == settings and self._estimate[2]:
            size = self._estimate_size(self._estimate[2], frames)
            text += (
                f"; roughly {size / 1e6:.1f} MB"
                if size < 1e9
                else f"; roughly {size / 1e9:.1f} GB"
            )
        self.estimate_label.setText(text)
        fresh = self._estimate is not None and self._estimate[:2] == (settings, id(frame))
        if not fresh and not self._play_timer.isActive():
            self._estimate_timer.start()

    def _estimate_settings(self):
        frame = self._last_preview_frame
        return (
            self.format_combo.currentData(),
            self.fps_spinbox.value(),
            self.quality_combo.currentText(),
            frame.shape,
        )

    def _start_estimate(self):
        if self._estimate_job is not None:
            return  # when it ends, _update_estimate comes back here
        frame = self._last_preview_frame
        settings = self._estimate_settings()
        ext, fps, quality, _ = settings
        options = {"fps": fps, "quality": quality}
        job = Job(lambda _progress: encoded_size(frame, ext, options))
        job.done.connect(lambda size: self._on_estimated(settings, id(frame), size))
        job.failed.connect(lambda _: self._on_estimated(settings, id(frame), None))
        job.finished.connect(self._on_estimate_ended)
        self._estimate_job = job
        job.start()

    def _on_estimated(self, settings, frame_id, size):
        self._estimate = (settings, frame_id, size)

    def _on_estimate_ended(self):
        self._estimate_job = None
        if not self._closed:
            self._update_estimate()

    def _estimate_size(self, one, frames):
        """Rough output size in bytes, from a frame encoded alone (or as PNG)
        taking ``one``, times the frames; later video frames take about half
        as much, as solar images change little from frame to frame."""
        if self.format_combo.currentData() in (".png", ".gif"):
            return one * frames
        return one * (1 + 0.5 * (frames - 1))

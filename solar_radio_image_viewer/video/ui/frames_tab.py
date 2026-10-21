"""
Frames tab: which frames make the video.
"""

import os
from PyQt5.QtWidgets import (
    QVBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QScrollArea,
    QWidget,
    QFormLayout,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
)
from PyQt5.QtCore import Qt
import glob
from ..creator import (
    apply_difference,
    observation_sort_key,
)
from .helpers import (
    is_image,
)


class FramesTabMixin:
    """Frames tab: which frames make the video. Mixed into VideoCreationDialog."""

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

    def _frame_plan(self, layers):
        """(plan, problem): frame_plan for the Input and Frames tabs (frames
        unticked in the Frames tab included), or (None, (title, message))."""
        from datetime import datetime

        from ..creator import frame_plan

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

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
from ...ui.background import wait_for
from ...ui.busy import busy
from .helpers import hint
from .planner import PlanSpec


class FramesTabMixin:
    """Frames tab: which frames make the video. Mixed into VideoCreationDialog."""

    def _create_frames_tab(self):
        """Frames tab: which frames make the video. Returns its scroll area."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 12, 8, 12)
        layout.setSpacing(12)
        scroll.setWidget(content)

        form = QFormLayout()
        form.setVerticalSpacing(8)
        self.frame_start_edit = QLineEdit()
        self.frame_start_edit.setPlaceholderText(
            "YYYY-MM-DD HH:MM:SS UTC (empty: from the first)"
        )
        form.addRow(hint("From"), self.frame_start_edit)
        self.frame_end_edit = QLineEdit()
        self.frame_end_edit.setPlaceholderText(
            "YYYY-MM-DD HH:MM:SS UTC (empty: to the last)"
        )
        form.addRow(hint("To"), self.frame_end_edit)
        self.frame_every_spinbox = QSpinBox()
        self.frame_every_spinbox.setRange(1, 1000)
        self.frame_every_spinbox.setPrefix("every ")
        self.frame_every_spinbox.setSuffix(" frame")
        self.frame_every_spinbox.valueChanged.connect(
            lambda n: self.frame_every_spinbox.setSuffix(" frame" if n == 1 else " frames")
        )
        form.addRow(hint("Use"), self.frame_every_spinbox)
        layout.addLayout(form)

        self.frames_summary_label = QLabel()
        self.frames_summary_label.setObjectName("SecondaryText")
        layout.addWidget(self.frames_summary_label)

        self._excluded_frames = set()
        self.frames_table = QTableWidget(0, 4)
        self.frames_table.setHorizontalHeaderLabels(["", "Time (UTC)", "Image", "Contours"])
        self.frames_table.verticalHeader().setVisible(False)
        self.frames_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self.frames_table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
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

        include_all = QPushButton("Include all frames")
        include_all.clicked.connect(self._include_all_frames)
        layout.addWidget(include_all, 0, Qt.AlignLeft)
        return scroll

    def _plan_spec(self, options):
        """What the Input and Frames tabs say about the frames, for the
        planner (frames unticked in the Frames tab are in the plan, and left
        out of its files)."""
        return PlanSpec(
            single=self.image_single_radio.isChecked(),
            single_path=self.single_image_edit.text().strip(),
            folder=self.input_directory_edit.text(),
            pattern=self.input_pattern_edit.text(),
            sort=self.sort_combo.currentText().lower(),
            start=self.frame_start_edit.text().strip(),
            end=self.frame_end_edit.text().strip(),
            every=self.frame_every_spinbox.value(),
            layers=options.get("contour_layers", []),
            excluded=frozenset(self._excluded_frames),
            difference=options.get("difference", "none"),
            remote=self.remote,
        )

    def _video_files(self, options):
        """(files, problem): the image shown in each frame, with the frames
        unticked in the Frames tab left out and ``options["contour_layers"]``
        given each series layer's file per frame; or ([], (title, message)).
        Waits for the plan with the window drawing, so can raise Cancelled."""
        future = self._planner.plan_now(self._plan_spec(options))
        plan = wait_for(self, "Working out the frames…", future, on_cancel=future.cancel)
        return self._use_plan(plan, options)

    def _use_plan(self, plan, options):
        """``plan`` (see planner.PlanResult) as _video_files returns it."""
        if plan.plan is not None:
            self._show_frames(plan.plan)
        if plan.problem:
            return [], plan.problem
        options.update(plan.updates)
        options["contour_layers"] = [dict(layer) for layer in plan.updates["contour_layers"]]
        return plan.files, None

    def _show_frames(self, plan):
        """List the frames of ``plan`` in the Frames tab."""
        rows = list(zip(plan["driver_files"], plan["image_files"], plan["times"]))
        if getattr(self, "_shown_frame_rows", None) == (rows, repr(plan["gaps"])):
            return
        self._shown_frame_rows = (rows, repr(plan["gaps"]))
        if len(rows) > 1000:
            with busy(f"Listing {len(rows)} frames…", self):
                self._fill_frames_table(plan, rows)
        else:
            self._fill_frames_table(plan, rows)

    def _fill_frames_table(self, plan, rows):
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

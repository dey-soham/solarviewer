"""
Overlays tab: text, solar disk, beam, grid, annotations, plot appearance and the line plot.
"""

from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QGroupBox,
    QComboBox,
    QCheckBox,
    QSpinBox,
    QMessageBox,
    QScrollArea,
    QWidget,
    QFormLayout,
)
from ...processing.contour_layers import (
    contour_layer_name,
)
from ..lightcurve import (
    IMAGE_AREAS,
    LAYER_AREAS,
    MAX_CURVES,
    QUANTITIES,
    default_line_plot,
)
from ...processing.plot_style import (
    DEFAULT_PLOT_SETTINGS,
    default_style,
)


class OverlaysTabMixin:
    """Overlays tab: text, solar disk, beam, grid, annotations, plot appearance and the line plot. Mixed into VideoCreationDialog."""

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
        # The main window's shapes, text and arrows, tied to the sky
        self.annotations = None
        self.show_annotations_check = QCheckBox("Main window annotations")
        self.show_annotations_check.setToolTip(
            "Shapes, text and arrows drawn in the main window, at the same\n"
            "sky positions (and sizes in arcsec) in every frame. Taken with\n"
            "Copy from Main Window (Plot Appearance)."
        )
        grid.addWidget(self.show_annotations_check, len(rows), 0)
        self.annotations_label = QLabel()
        self.annotations_label.setObjectName("SecondaryText")
        grid.addWidget(self.annotations_label, len(rows), 1)
        self._set_annotations(None)

        grid.setColumnStretch(0, 1)
        self.wcs_coords_check.toggled.connect(self._update_grid_check)
        self._update_grid_check()
        return group

    def _set_annotations(self, annotations):
        """The main window's annotations (see video.annotations), or None."""
        from ..annotations import annotation_counts, has_annotations

        self.annotations = annotations if has_annotations(annotations) else None
        self.show_annotations_check.setEnabled(self.annotations is not None)
        self.show_annotations_check.setChecked(self.annotations is not None)
        self.annotations_label.setText(
            annotation_counts(self.annotations) if self.annotations else "none copied"
        )

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
        from ...dialogs.solar_disk_style import SolarDiskStyleDialog

        SolarDiskStyleDialog(self, self.solar_disk_style).exec_()

    def _edit_beam_style(self):
        from ...dialogs.beam_settings import BeamSettingsDialog

        BeamSettingsDialog(self, self.beam_style).exec_()

    def _edit_grid_style(self):
        from ...dialogs.grid_settings import GridSettingsDialog

        GridSettingsDialog(self, self.grid_style).exec_()

    def _edit_plot_settings(self):
        from ...dialogs.plot_customization import PlotCustomizationDialog

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
        from ..annotations import annotations_from_viewer

        self._set_annotations(annotations_from_viewer(tab))
        if notify:
            self.show_status_message(
                "Copied the solar disk, beam, grid and plot appearance from the main window"
            )

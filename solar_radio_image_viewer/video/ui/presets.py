"""
Saving and loading all settings (presets for the window and the command line).
"""

from PyQt5.QtWidgets import (
    QLineEdit,
    QFileDialog,
    QComboBox,
    QCheckBox,
    QMessageBox,
    QRadioButton,
)
from ..lightcurve import (
    default_line_plot,
)
from ...processing.plot_style import (
    DEFAULT_BEAM_STYLE,
    DEFAULT_GRID_STYLE,
    DEFAULT_SOLAR_DISK_STYLE,
)


class PresetsMixin:
    """Saving and loading all settings (presets for the window and the command line). Mixed into VideoCreationDialog."""

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
        "show_annotations_check",
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
            "annotations": self.annotations,
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
        self._set_annotations(state.get("annotations"))
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

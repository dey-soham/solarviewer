"""
Starting from the main window's image, view and display settings.
"""

import os
from PyQt5.QtWidgets import (
    QMessageBox,
)
from .helpers import (
    is_image,
    viewer_image,
)


class MainWindowMixin:
    """Starting from the main window's image, view and display settings. Mixed into VideoCreationDialog."""

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
            from ...processing.image_meta import fits_header

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

"""
Whole-image statistics and the RMS box settings dialog.
"""

import warnings

import numpy as np
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIntValidator
from PyQt5.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSlider,
    QTableWidgetItem,
)

from ..dialogs.form import field_label, form_block
from ..ui.busy import busy
from ..ui.geometry import fit_width_to_text
from ..ui.styles import SPACE_L, set_hand_cursor
from ..ui.widgets import button_bar, dialog_layout


MIN_BOX_PIXELS = 25  # a noise box with fewer measured pixels than this is not used


def _edge_bands(shape):
    """Bands along the four edges of an image indexed [x, y], as
    [x1, x2, y1, y2]: the bottom one is the box a new image gets
    (core/loading.py), then the top, left and right."""
    nx, ny = shape
    return [
        ("bottom band", [int(0.05 * nx), int(0.95 * nx), int(0.02 * ny), int(0.20 * ny)]),
        ("top band", [int(0.05 * nx), int(0.95 * nx), int(0.80 * ny), int(0.98 * ny)]),
        ("left band", [int(0.02 * nx), int(0.20 * nx), int(0.05 * ny), int(0.95 * ny)]),
        ("right band", [int(0.80 * nx), int(0.98 * nx), int(0.05 * ny), int(0.95 * ny)]),
    ]


def _measured(data, box):
    x1, x2, y1, y2 = (int(v) for v in box)
    return int(np.isfinite(data[max(x1, 0) : max(x2, 0), max(y1, 0) : max(y2, 0)]).sum())


def noise_box(data, rms_box):
    """The box to measure the noise in, and a note when it is not the one
    asked for: ``rms_box`` when it holds at least MIN_BOX_PIXELS measured
    pixels; else the edge band holding the most (an image turned north up
    or a coronagraph's mask can leave the asked-for box blank); else
    (None, note)."""
    if _measured(data, rms_box) >= MIN_BOX_PIXELS:
        return list(rms_box), ""
    name, band = max(_edge_bands(data.shape), key=lambda item: _measured(data, item[1]))
    if _measured(data, band) >= MIN_BOX_PIXELS:
        return band, f"the set box is blank, measured in the {name}"
    return None, "no measured pixels near the edges"


class ImageStatsMixin:
    """Whole-image statistics table and the RMS box dialog.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``contour_layers``, ``image_info_label``, ``image_stats_table``,
    ``imagename``, ``on_visualization_changed``, ``show_status_message``
    """

    _rms_dialog = None  # the open RMS box dialog

    def show_image_stats(self, rms_box=None):
        if self.current_image_data is None:
            return

        # Use the current RMS box if none is provided
        if rms_box is None:
            rms_box = self.current_rms_box

        data = self.current_image_data
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-blank images
            dmax = float(np.nanmax(data))
            dmin = float(np.nanmin(data))
        box, note = noise_box(data, rms_box)
        if box is None:
            drms = dmean_rms_box = np.nan
        else:
            region = np.asarray(data[box[0] : box[1], box[2] : box[3]], dtype=np.float64)
            drms = float(np.sqrt(np.nanmean(region**2)))
            dmean_rms_box = float(np.nanmean(region))
        # The dynamic range needs a noise level: not on a blank or uniform
        # zero image, and not one lost in the rounding of the brightest value
        # (relative, so that images in tiny units, MSB ~1e-10, keep theirs)
        peak = max(abs(dmax), abs(dmin)) if np.isfinite(dmax) else 0.0
        if np.isfinite(drms) and drms > 0 and drms > 1e-12 * peak:
            positive_DR = dmax / drms
            negative_DR = dmin / drms
        else:
            positive_DR = np.nan
            negative_DR = np.nan

        # Update the image stats table
        stats_values = [dmax, dmin, drms, dmean_rms_box, positive_DR, negative_DR]
        for i, val in enumerate(stats_values):
            self.image_stats_table.setItem(i, 1, QTableWidgetItem(f"{val:.6g}" if np.isfinite(val) else "—"))
        self.image_stats_table.show()  # (hidden until there is an image)

        # Update the RMS box info in the label
        h, w = data.shape
        shown = box or rms_box
        rms_box_percent = (
            (shown[1] - shown[0]) * (shown[3] - shown[2]) / (h * w)
        ) * 100
        self.image_info_label.setText(
            f"{w}×{h} px · RMS box {rms_box_percent:.1f}%" + (f" ({note})" if note else "")
        )
        if hasattr(self, "show_image_summary"):
            self.show_image_summary((h, w, rms_box_percent, dmax, drms, positive_DR))

        return dmax, dmin, drms, dmean_rms_box, positive_DR, negative_DR

    def show_rms_box_dialog(self):
        """Show a dialog for configuring the RMS box settings"""
        # One at a time: its fields are kept on the tab
        if self._rms_dialog is not None:
            self._rms_dialog.raise_()
            self._rms_dialog.activateWindow()
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("RMS box settings")
        set_hand_cursor(dialog)

        # Create layout
        layout = dialog_layout(dialog)

        # Add a description label
        description = QLabel(
            "The region the RMS is measured in, for the dynamic range and the "
            "thresholds of derived Stokes. Applies to this image and later Stokes choices."
        )
        description.setWordWrap(True)
        description.setObjectName("Hint")
        layout.addWidget(description)

        # Create grid layout for RMS box inputs
        rms_grid = form_block(layout)
        rms_grid.setColumnStretch(1, 0)

        # Create input fields for RMS box coordinates
        self.dialog_rms_x1_entry = QLineEdit(str(self.current_rms_box[0]))
        self.dialog_rms_x2_entry = QLineEdit(str(self.current_rms_box[1]))
        self.dialog_rms_y1_entry = QLineEdit(str(self.current_rms_box[2]))
        self.dialog_rms_y2_entry = QLineEdit(str(self.current_rms_box[3]))

        # Add validators to ensure values are numeric
        max_val = 9999
        if hasattr(self, "current_image_data") and self.current_image_data is not None:
            height, width = self.current_image_data.shape
            self.dialog_rms_x1_entry.setValidator(QIntValidator(0, height - 1))
            self.dialog_rms_x2_entry.setValidator(QIntValidator(1, height))
            self.dialog_rms_y1_entry.setValidator(QIntValidator(0, width - 1))
            self.dialog_rms_y2_entry.setValidator(QIntValidator(1, width))
        else:
            self.dialog_rms_x1_entry.setValidator(QIntValidator(0, max_val))
            self.dialog_rms_x2_entry.setValidator(QIntValidator(1, max_val))
            self.dialog_rms_y1_entry.setValidator(QIntValidator(0, max_val))
            self.dialog_rms_y2_entry.setValidator(QIntValidator(1, max_val))

        # Create sliders for RMS box coordinates
        self.dialog_rms_x1_slider = QSlider(Qt.Horizontal)
        self.dialog_rms_x2_slider = QSlider(Qt.Horizontal)
        self.dialog_rms_y1_slider = QSlider(Qt.Horizontal)
        self.dialog_rms_y2_slider = QSlider(Qt.Horizontal)
        # Configure sliders
        if hasattr(self, "current_image_data") and self.current_image_data is not None:
            height, width = self.current_image_data.shape
            self.dialog_rms_x1_slider.setMaximum(height - 1)
            self.dialog_rms_x2_slider.setMaximum(height)
            self.dialog_rms_y1_slider.setMaximum(width - 1)
            self.dialog_rms_y2_slider.setMaximum(width)
        else:
            self.dialog_rms_x1_slider.setMaximum(max_val)
            self.dialog_rms_x2_slider.setMaximum(max_val)
            self.dialog_rms_y1_slider.setMaximum(max_val)
            self.dialog_rms_y2_slider.setMaximum(max_val)

        self.dialog_rms_x1_slider.setMinimum(0)
        self.dialog_rms_x2_slider.setMinimum(1)
        self.dialog_rms_y1_slider.setMinimum(0)
        self.dialog_rms_y2_slider.setMinimum(1)

        self.dialog_rms_x1_slider.setValue(self.current_rms_box[0])
        self.dialog_rms_x2_slider.setValue(self.current_rms_box[1])
        self.dialog_rms_y1_slider.setValue(self.current_rms_box[2])
        self.dialog_rms_y2_slider.setValue(self.current_rms_box[3])

        # Connect slider signals
        self.dialog_rms_x1_slider.valueChanged.connect(
            lambda v: self.dialog_rms_x1_entry.setText(str(v))
        )
        self.dialog_rms_x2_slider.valueChanged.connect(
            lambda v: self.dialog_rms_x2_entry.setText(str(v))
        )
        self.dialog_rms_y1_slider.valueChanged.connect(
            lambda v: self.dialog_rms_y1_entry.setText(str(v))
        )
        self.dialog_rms_y2_slider.valueChanged.connect(
            lambda v: self.dialog_rms_y2_entry.setText(str(v))
        )

        # Connect text entry signals
        self.dialog_rms_x1_entry.textChanged.connect(self.update_dialog_rms_box)
        self.dialog_rms_x2_entry.textChanged.connect(self.update_dialog_rms_box)
        self.dialog_rms_y1_entry.textChanged.connect(self.update_dialog_rms_box)
        self.dialog_rms_y2_entry.textChanged.connect(self.update_dialog_rms_box)

        # The fields as wide as a pixel index, the sliders taking the rest,
        # never shrunk to a dot
        for entry, slider in (
            (self.dialog_rms_x1_entry, self.dialog_rms_x1_slider),
            (self.dialog_rms_x2_entry, self.dialog_rms_x2_slider),
            (self.dialog_rms_y1_entry, self.dialog_rms_y1_slider),
            (self.dialog_rms_y2_entry, self.dialog_rms_y2_slider),
        ):
            fit_width_to_text(entry, "000000")
            slider.setMinimumWidth(entry.width() * 2)

        # Add widgets to grid layout
        rms_grid.addWidget(field_label("X1"), 0, 0)
        rms_grid.addWidget(self.dialog_rms_x1_entry, 0, 1)
        rms_grid.addWidget(self.dialog_rms_x1_slider, 0, 2)

        rms_grid.addWidget(field_label("X2"), 1, 0)
        rms_grid.addWidget(self.dialog_rms_x2_entry, 1, 1)
        rms_grid.addWidget(self.dialog_rms_x2_slider, 1, 2)

        rms_grid.addWidget(field_label("Y1"), 2, 0)
        rms_grid.addWidget(self.dialog_rms_y1_entry, 2, 1)
        rms_grid.addWidget(self.dialog_rms_y1_slider, 2, 2)

        rms_grid.addWidget(field_label("Y2"), 3, 0)
        rms_grid.addWidget(self.dialog_rms_y2_entry, 3, 1)
        rms_grid.addWidget(self.dialog_rms_y2_slider, 3, 2)

        # Set column stretching to make sliders take most of the space
        rms_grid.setColumnStretch(2, 1)

        # Add buttons
        def on_apply():
            try:
                self.apply_dialog_rms_box(dialog)
            except RuntimeError:
                QMessageBox.warning(
                    dialog, "Error", "The target tab is no longer available."
                )
                dialog.close()

        button_box, _ = button_bar(primary=("Apply", on_apply), secondary=("Cancel", dialog.close))
        layout.addWidget(button_box)
        # No smaller than its rows: the description wraps to their width and
        # keeps the height it needs there, the fields their spacing
        description.setMinimumWidth(layout.sizeHint().width() - 2 * SPACE_L)
        dialog.setMinimumSize(dialog.sizeHint())

        # Show the dialog as non-modal
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        self._rms_dialog = dialog
        dialog.destroyed.connect(lambda: setattr(self, "_rms_dialog", None))
        dialog.show()

    def update_dialog_rms_box(self):
        """Update RMS box sliders in the dialog when text entries change"""
        try:
            x1 = int(self.dialog_rms_x1_entry.text())
            x2 = int(self.dialog_rms_x2_entry.text())
            y1 = int(self.dialog_rms_y1_entry.text())
            y2 = int(self.dialog_rms_y2_entry.text())

            # Update sliders without triggering signals
            self.dialog_rms_x1_slider.blockSignals(True)
            self.dialog_rms_x2_slider.blockSignals(True)
            self.dialog_rms_y1_slider.blockSignals(True)
            self.dialog_rms_y2_slider.blockSignals(True)

            self.dialog_rms_x1_slider.setValue(x1)
            self.dialog_rms_x2_slider.setValue(x2)
            self.dialog_rms_y1_slider.setValue(y1)
            self.dialog_rms_y2_slider.setValue(y2)

            self.dialog_rms_x1_slider.blockSignals(False)
            self.dialog_rms_x2_slider.blockSignals(False)
            self.dialog_rms_y1_slider.blockSignals(False)
            self.dialog_rms_y2_slider.blockSignals(False)
        except ValueError:
            pass  # Ignore invalid input

    def _set_rms_box(self, box):
        self.current_rms_box = box
        # The RMS box of contours from this image as well
        for layer in self.contour_layers:
            if layer["source"] == "same":
                layer["rms_box"] = tuple(box)

    def apply_dialog_rms_box(self, dialog):
        """Apply the RMS box settings from the dialog and close it"""
        # Early return if no valid image is loaded or if it's the splash image
        is_splash = (
            hasattr(self, "imagename")
            and self.imagename
            and self.imagename.endswith("splash.fits")
        )
        if self.current_image_data is None or is_splash:
            QMessageBox.warning(
                self,
                "No image loaded",
                "Please load an image before changing RMS box settings.",
            )
            dialog.reject()
            return
        try:
            x1 = int(self.dialog_rms_x1_entry.text())
            x2 = int(self.dialog_rms_x2_entry.text())
            y1 = int(self.dialog_rms_y1_entry.text())
            y2 = int(self.dialog_rms_y2_entry.text())

            # Ensure x1 < x2 and y1 < y2
            if x1 >= x2 or y1 >= y2:
                QMessageBox.warning(
                    self, "Invalid RMS box", "Please ensure that X1 < X2 and Y1 < Y2."
                )
                return

            # Ensure values are within image bounds
            if self.current_image_data is not None:
                height, width = self.current_image_data.shape
                if x2 > height or y2 > width:
                    QMessageBox.warning(
                        self,
                        "Invalid RMS box",
                        f"RMS box exceeds image dimensions ({height}x{width}).",
                    )
                    return

            # The form is done with: close it before the work, not after
            dialog.accept()

            previous = list(self.current_rms_box)
            self._set_rms_box([x1, x2, y1, y2])
            self.show_status_message(
                f"Updating RMS box to [{x1}:{x2}, {y1}:{y2}] and recalculating…"
            )

            # Reload the current image with the new RMS box: this recalculates
            # RMS for all Stokes parameters. None if the user cancelled
            with busy("Recalculating with the new RMS box…", self):
                shown = self.on_visualization_changed()
            if shown:
                self.show_status_message(f"RMS box updated to [{x1}:{x2}, {y1}:{y2}]")
            else:
                self._set_rms_box(previous)
                self.show_image_stats()
                if shown is None:
                    self.show_status_message("RMS box change cancelled")
        except ValueError:
            QMessageBox.warning(
                self,
                "Invalid input",
                "Please enter valid integer values for the RMS box coordinates.",
            )

"""
Whole-image statistics and the RMS box settings dialog.
"""

import warnings

import numpy as np
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIntValidator
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSlider,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..ui.styles import set_hand_cursor


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

    ``cmap_combo``, ``contour_layers``, ``gamma_entry``, ``image_info_label``,
    ``image_stats_table``, ``imagename``, ``plot_image``,
    ``show_status_message``, ``stokes_combo``, ``stretch_combo``,
    ``threshold_entry``, ``vmax_entry``, ``vmin_entry``
    """

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
            f"Image: {h}x{w} px - RMS box: {rms_box_percent:.1f}%" + (f" ({note})" if note else "")
        )
        if hasattr(self, "show_image_summary"):
            self.show_image_summary((h, w, rms_box_percent, dmax, drms, positive_DR))

        return dmax, dmin, drms, dmean_rms_box, positive_DR, negative_DR

    def show_rms_box_dialog(self):
        """Show a dialog for configuring the RMS box settings"""
        dialog = QDialog(self)
        dialog.setWindowTitle("RMS Box Settings")
        set_hand_cursor(dialog)
        dialog.setMinimumWidth(400)

        # Create layout
        layout = QVBoxLayout(dialog)

        # Add a description label
        description = QLabel(
            "Set the region used for RMS calculation. This affects dynamic range calculations, "
            "thresholding for derived Stokes parameters (Lfrac, Vfrac, Q/I, etc.), and other statistics. "
            "Changes will be applied to the current image and all future Stokes parameter selections."
        )
        description.setWordWrap(True)
        description.setObjectName("Hint")
        layout.addWidget(description)

        # Create grid layout for RMS box inputs
        rms_grid = QGridLayout()
        rms_grid.setVerticalSpacing(10)
        rms_grid.setHorizontalSpacing(10)

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

        # Add widgets to grid layout
        rms_grid.addWidget(QLabel("X1:"), 0, 0)
        rms_grid.addWidget(self.dialog_rms_x1_entry, 0, 1)
        rms_grid.addWidget(self.dialog_rms_x1_slider, 0, 2)

        rms_grid.addWidget(QLabel("X2:"), 1, 0)
        rms_grid.addWidget(self.dialog_rms_x2_entry, 1, 1)
        rms_grid.addWidget(self.dialog_rms_x2_slider, 1, 2)

        rms_grid.addWidget(QLabel("Y1:"), 2, 0)
        rms_grid.addWidget(self.dialog_rms_y1_entry, 2, 1)
        rms_grid.addWidget(self.dialog_rms_y1_slider, 2, 2)

        rms_grid.addWidget(QLabel("Y2:"), 3, 0)
        rms_grid.addWidget(self.dialog_rms_y2_entry, 3, 1)
        rms_grid.addWidget(self.dialog_rms_y2_slider, 3, 2)

        # Set column stretching to make sliders take most of the space
        rms_grid.setColumnStretch(2, 1)

        layout.addLayout(rms_grid)

        # Add buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)

        def on_apply():
            try:
                self.apply_dialog_rms_box(dialog)
            except RuntimeError:
                QMessageBox.warning(
                    dialog, "Error", "The target tab is no longer available."
                )
                dialog.close()

        button_box.accepted.connect(on_apply)
        button_box.rejected.connect(dialog.close)
        layout.addWidget(button_box)

        # Show the dialog as non-modal
        dialog.setAttribute(Qt.WA_DeleteOnClose)
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
                "No Image Loaded",
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
                    self, "Invalid RMS Box", "Please ensure that X1 < X2 and Y1 < Y2."
                )
                return

            # Ensure values are within image bounds
            if self.current_image_data is not None:
                height, width = self.current_image_data.shape
                if x2 > height or y2 > width:
                    QMessageBox.warning(
                        self,
                        "Invalid RMS Box",
                        f"RMS box exceeds image dimensions ({height}x{width}).",
                    )
                    return

            # Store the current RMS box values
            self.current_rms_box = [x1, x2, y1, y2]

            # Update the RMS box of contours from this image as well
            for layer in self.contour_layers:
                if layer["source"] == "same":
                    layer["rms_box"] = tuple(self.current_rms_box)

            # Update image stats with new RMS box
            if self.current_image_data is not None:
                self.show_image_stats(rms_box=self.current_rms_box)

                # Reload the current image with the new RMS box
                # This will recalculate RMS for all Stokes parameters
                if hasattr(self, "imagename") and self.imagename:
                    current_stokes = self.stokes_combo.currentText()
                    try:
                        threshold = float(self.threshold_entry.text())
                    except (ValueError, AttributeError):
                        threshold = 10.0

                    # Show a status message
                    self.show_status_message(
                        f"Updating RMS box to [{x1}:{x2}, {y1}:{y2}] and recalculating..."
                    )

                    # Reload the data with the new RMS box
                    from ..processing.image_data import get_pixel_values_from_image

                    pix, csys, psf = get_pixel_values_from_image(
                        self.imagename,
                        current_stokes,
                        threshold,
                        rms_box=tuple(self.current_rms_box),
                    )
                    self.current_image_data = pix
                    self.current_wcs = csys
                    self.psf = psf

                    # Update the plot
                    try:
                        vmin_val = float(self.vmin_entry.text())
                        vmax_val = float(self.vmax_entry.text())
                        stretch = self.stretch_combo.currentText()
                        cmap = self.cmap_combo.currentText()
                        gamma = float(self.gamma_entry.text())
                        self.plot_image(vmin_val, vmax_val, stretch, cmap, gamma)
                    except (ValueError, AttributeError):
                        self.plot_image()

                self.show_status_message(f"RMS box updated to [{x1}:{x2}, {y1}:{y2}]")

            # Close the dialog
            dialog.accept()
        except ValueError:
            QMessageBox.warning(
                self,
                "Invalid Input",
                "Please enter valid integer values for the RMS box coordinates.",
            )

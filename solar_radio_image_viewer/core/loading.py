"""
Reading an image into the tab: the pixels of the chosen Stokes product,
its coordinates and header, Fast mode, and which Stokes products the image
offers.

The file is read in a worker process (ui/background) while the window keeps
drawing; the tab's state changes only once the read is complete.
"""

import logging
import os

import numpy as np
from PyQt5.QtWidgets import (
    QApplication,
    QMessageBox,
)
from PyQt5.QtCore import Qt, QEventLoop
from ..ui.background import Cancelled, run_in_background, wait_for
from ..ui.busy import busy
from ..processing.fits_io import is_fits
from ..processing.image_meta import casa_info, coordsys_from_record, fits_header
from ..processing.prefetch import image_prefetcher
from ..processing.image_data import read_image
from ..processing.stokes import StokesUnavailable, missing_planes
from ..ui.errors import show_error

logger = logging.getLogger(__name__)


class LoadingMixin:
    """Loads the tab's image and keeps the Stokes choices to what the image has.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``_is_already_hpc``, ``_tb_original_imagename``, ``_tb_temp_file``,
    ``contour_layers``, ``current_roi``, ``downsample_toggle``,
    ``helioviewer_btn``, ``hpc_btn``, ``imagename``, ``noaa_events_btn``,
    ``schedule_plot``, ``set_range``, ``show_beam_checkbox``,
    ``show_beam_label``, ``show_image_stats``, ``show_status_message``,
    ``stokes_combo``, ``tb_btn``, ``threshold_entry``
    """

    def _full_resolution(self, imagename, target_size):
        """(the [x, y] sky shape of the file, the downsample factor of the
        plane shown), from the image's cached metadata."""
        from ..processing.image_data import downsample_factor
        from ..processing.image_meta import sky_axes

        summary = casa_info(imagename).summary
        x_idx, y_idx = sky_axes(summary["axisnames"])
        shape = [int(n) for n in summary["shape"]]
        full = (shape[x_idx], shape[y_idx])
        return full, downsample_factor(full, target_size)

    def roi_at_full_resolution(self, roi=None):
        """The ROI (xlow, xhigh, ylow, yhigh) in the file's own pixels. In
        Fast mode the plane shown is every Nth pixel, so the ROI drawn on it
        must be scaled up before CASA reads the file."""
        xlow, xhigh, ylow, yhigh = roi if roi is not None else self.current_roi
        factor = getattr(self, "_downsample", 1) or 1
        xlow, xhigh, ylow, yhigh = (v * factor for v in (xlow, xhigh, ylow, yhigh))
        full = getattr(self, "_full_image_shape", None)
        if full is not None:
            xhigh, yhigh = min(xhigh, full[0]), min(yhigh, full[1])
        return int(xlow), int(xhigh), int(ylow), int(yhigh)

    def _load_target_size(self):
        """Max dimension to downsample loads to in fast load mode, 0 for none."""
        if hasattr(self, "downsample_toggle") and self.downsample_toggle.isChecked():
            return 800  # Smart downsampling to ~800px max dimension
        return 0

    def _read_image(
        self, imagename, stokes, threshold, rms_box, target_size, fallback_stokes
    ):
        """The :class:`ImageRead` of these inputs: read ahead by the
        prefetcher, or now in the worker process."""
        label = f"Loading {os.path.basename(imagename.rstrip('/'))}…"
        # Let a background read of this file finish before reading it again
        image_prefetcher.settle(
            imagename, wait=lambda future: wait_for(self, label, future)
        )
        read = image_prefetcher.take(imagename, stokes, threshold, rms_box, target_size)
        if read is None:
            read = run_in_background(
                self,
                label,
                read_image,
                imagename,
                stokes,
                threshold,
                rms_box,
                target_size,
                fallback_stokes,
            )
            read.remember()
        return read

    def load_data(
        self, imagename, stokes, threshold, auto_adjust_rms=False, fallback_stokes=None
    ):
        """Read the ``stokes`` product of ``imagename`` and make it the tab's
        image; returns the product shown.

        With ``fallback_stokes`` (for a newly opened image), an image lacking
        a plane ``stokes`` needs shows that product instead, after a warning,
        and the Stokes choices are limited to those the image offers.

        Raises Cancelled if the user cancels the read, StokesUnavailable or
        ImageUnreadable if it fails; the tab is unchanged then.
        """
        target_size = self._load_target_size()
        read = self._read_image(
            imagename,
            stokes,
            threshold,
            tuple(self.current_rms_box),
            target_size,
            fallback_stokes,
        )
        if fallback_stokes is not None:
            self._offer_available_stokes(read.available, stokes, read.stokes)

        self._loaded_threshold = threshold
        pix = read.pix
        self.current_image_data = pix
        if read.csys_record is not None:
            self.current_wcs = coordsys_from_record(read.csys_record)
            self.psf = read.psf
            self._full_image_shape, self._downsample = self._full_resolution(
                imagename, target_size
            )
        else:
            # Read with astropy: nothing of the previous image's may remain,
            # its beam and its coordinate system (zoom to 1°, the solar disk,
            # HPC detection); and this reader does not downsample
            self.current_wcs = None
            self.psf = None
            self._full_image_shape = pix.shape
            self._downsample = 1

        if pix is not None:
            nx, ny = pix.shape  # the image is indexed [x, y]
            # Only auto adjust RMS box dimensions when loading a new image:
            # a band along the bottom, inset from the sides
            if auto_adjust_rms:
                x1 = int(0.05 * nx)
                x2 = int(0.95 * nx)
                y1 = int(0.02 * ny)
                y2 = int(0.20 * ny)
                self.current_rms_box = [x1, x2, y1, y2]
                for layer in self.contour_layers:
                    if layer["source"] == "same":
                        layer["rms_box"] = tuple(self.current_rms_box)

            # For HPC/SOLAR-X or HPLN images, disk center is at world (0,0)
            _is_hpc = self._is_already_hpc()
            if _is_hpc and self.current_wcs:
                try:
                    # Use CASA coordsys to find pixel of world (0,0)
                    # Use reference values for non-spatial axes (freq, Stokes)
                    ref_world = list(self.current_wcs.referencevalue()["numeric"])
                    ref_world[0] = 0.0  # SOLAR-X / HPLN = 0
                    ref_world[1] = 0.0  # SOLAR-Y / HPLT = 0
                    pix_origin = self.current_wcs.topixel(ref_world)["numeric"]
                    cx, cy = float(pix_origin[0]), float(pix_origin[1])
                    self.solar_disk_center = (cx, cy)
                except Exception:
                    self.solar_disk_center = (nx // 2, ny // 2)
            else:
                self.solar_disk_center = (nx // 2, ny // 2)

            # Make sure RMS box is within image bounds
            if self.current_rms_box[1] > nx:
                self.current_rms_box[1] = nx
            if self.current_rms_box[3] > ny:
                self.current_rms_box[3] = ny

            # Update image stats when data is loaded
            try:
                self.show_image_stats(rms_box=self.current_rms_box)
            except Exception as e:
                logger.error(f"Error showing image stats: {e}")
                self.show_status_message(f"Error showing image stats: {e}")

        # Store the FITS header for TB calculation
        try:
            # Don't reset TB mode if we're loading the TB temp file
            is_tb_temp = (
                hasattr(self, "_tb_temp_file")
                and self._tb_temp_file
                and imagename == self._tb_temp_file
            )

            if is_fits(imagename):
                self.current_header = dict(fits_header(imagename))
                bunit = self.current_header.get("BUNIT", "").lower()
                self._current_bunit = self.current_header.get("BUNIT", "")

                # Enable TB/Flux button if units are Jy/beam or K (not for TB temp file)
                if hasattr(self, "tb_btn") and not is_tb_temp:
                    is_jy_beam = "jy" in bunit and "beam" in bunit
                    is_kelvin = bunit.strip().lower() == "k"
                    self.tb_btn.setEnabled(is_jy_beam or is_kelvin)

                    if is_kelvin:
                        self.tb_btn.setText("FLUX")
                        self.tb_btn.setToolTip("Convert to Flux (Jy/beam) view")
                    elif is_jy_beam:
                        self.tb_btn.setText("TB")
                        self.tb_btn.setToolTip(
                            "Convert to Brightness Temperature (K)"
                        )
                    else:
                        self.tb_btn.setText("FLUX")
                        self.tb_btn.setToolTip("Convert to Flux (Jy/beam) view")

                    # Reset TB mode when loading new image (not when loading TB temp)
                    if (
                        not hasattr(self, "_tb_original_imagename")
                        or not self._tb_original_imagename
                    ):
                        self._tb_mode = False
                        self.tb_btn.setChecked(False)

                # Update HPC button based on coordinate system
                if hasattr(self, "hpc_btn"):
                    ctype1 = self.current_header.get("CTYPE1", "").upper()
                    ctype2 = self.current_header.get("CTYPE2", "").upper()
                    is_hpc = (
                        "HPLN" in ctype1 or "HPLT" in ctype2 or "SOLAR" in ctype1
                    )

                    if is_hpc:
                        self.hpc_btn.setText("RA/DEC")
                        self.hpc_btn.setToolTip("Convert to RA/DEC coordinates")
                    else:
                        self.hpc_btn.setText("HPC")
                        self.hpc_btn.setToolTip("Convert to Helioprojective view")
            else:
                # Try to get header from CASA image
                try:
                    # Get CASA image info
                    info = casa_info(imagename)
                    csys = info.coordsys()

                    # Build header dict from CASA info
                    self.current_header = {}

                    # Get beam info
                    beam = info.beam
                    if beam and "major" in beam:
                        self.current_header["BMAJ"] = (
                            beam["major"]["value"] / 3600
                            if beam["major"]["unit"] == "arcsec"
                            else beam["major"]["value"]
                        )
                        self.current_header["BMIN"] = (
                            beam["minor"]["value"] / 3600
                            if beam["minor"]["unit"] == "arcsec"
                            else beam["minor"]["value"]
                        )

                    # Get frequency
                    refval = csys.referencevalue()["numeric"]
                    for i, unit in enumerate(csys.units()):
                        if unit == "Hz":
                            self.current_header["CRVAL3"] = refval[i]
                            break

                    # Get units
                    bunit = info.brightness_unit
                    self.current_header["BUNIT"] = bunit
                    self._current_bunit = bunit

                    # Get coordinate names for HPC detection (before closing ia)
                    dimension_names = [n.upper() for n in csys.names()]

                    # Enable TB/Flux button if units are Jy/beam or K
                    if hasattr(self, "tb_btn"):
                        is_jy_beam = "jy" in bunit.lower() and "beam" in bunit.lower()
                        is_kelvin = bunit.strip().lower() == "k"
                        self.tb_btn.setEnabled(is_jy_beam or is_kelvin)

                        if is_kelvin:
                            self.tb_btn.setText("FLUX")
                            self.tb_btn.setToolTip("Convert to Flux (Jy/beam) view")
                        elif is_jy_beam:
                            self.tb_btn.setText("TB")
                            self.tb_btn.setToolTip(
                                "Convert to Brightness Temperature (K)"
                            )
                        else:
                            self.tb_btn.setText("TB")
                            self.tb_btn.setToolTip(
                                "Convert to Brightness Temperature (K)"
                            )

                        if (
                            not hasattr(self, "_tb_original_imagename")
                            or not self._tb_original_imagename
                        ):
                            self._tb_mode = False
                            self.tb_btn.setChecked(False)

                    # Update HPC button based on coordinate system
                    if hasattr(self, "hpc_btn"):
                        is_hpc = (
                            "SOLAR-X" in dimension_names
                            or "HPLN-TAN" in dimension_names
                        )

                        if is_hpc:
                            self.hpc_btn.setText("RA/DEC")
                            self.hpc_btn.setToolTip("Convert to RA/DEC coordinates")
                        else:
                            self.hpc_btn.setText("HPC")
                            self.hpc_btn.setToolTip("Convert to Helioprojective view")

                except Exception as casa_err:
                    logger.warning(f"[TB] CASA header extraction error: {casa_err}")
                    self.show_status_message(
                        f"[TB] CASA header extraction error: {casa_err}"
                    )
                    self.current_header = None
                    self._current_bunit = ""
                    if hasattr(self, "tb_btn"):
                        self.tb_btn.setEnabled(False)

        except Exception as e:
            logger.error(f"Error loading header: {e}")
            self.show_status_message(f"[TB] Error loading header: {e}")
            self.current_header = None
            self._current_bunit = ""
            if hasattr(self, "tb_btn"):
                self.tb_btn.setEnabled(False)

        if not self.psf:
            self.show_beam_checkbox.setChecked(False)
            self.show_beam_checkbox.setEnabled(False)
            self.show_beam_label.setEnabled(False)
        else:
            self.show_beam_checkbox.setEnabled(True)
            self.show_beam_label.setEnabled(True)

        # Enable NOAA Events button when an image is loaded
        if hasattr(self, "noaa_events_btn"):
            self.noaa_events_btn.setEnabled(True)
        if hasattr(self, "helioviewer_btn"):
            self.helioviewer_btn.setEnabled(True)
        return read.stokes

    def on_stokes_changed(self, stokes):
        if not self.imagename:
            return

        main_window = self.parent()
        if main_window:
            self.show_status_message(
                f"Loading data for Stokes {stokes}... Please wait..."
            )
            QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)

        try:
            threshold = float(self.threshold_entry.text())
        except (ValueError, AttributeError):
            threshold = 10.0
            if hasattr(self, "threshold_entry"):
                self.threshold_entry.setText("10.0")

        try:
            with busy():
                self.load_data(self.imagename, stokes, threshold)
            self._range_stokes = stokes
        except Exception as e:
            import traceback

            if isinstance(e, Cancelled):
                self.show_status_message(f"Loading Stokes {stokes} cancelled")
            elif isinstance(e, StokesUnavailable):
                # e.g. "The image has no Stokes Q plane (its planes are I, V)."
                QMessageBox.warning(
                    self, "Stokes Not Available", f"Cannot show Stokes {stokes}.\n\n{e}"
                )
            else:
                show_error(
                    self,
                    "Could Not Load Stokes",
                    f"Stokes {stokes} could not be loaded:\n\n{e}",
                    traceback.format_exc(),
                )
            # Back to the product still on screen
            shown = getattr(self, "_range_stokes", "I")
            if self.stokes_combo.currentText() != shown:
                self.stokes_combo.blockSignals(True)
                self.stokes_combo.setCurrentText(shown)
                self.stokes_combo.blockSignals(False)
            return

        data = self.current_image_data
        if data is not None:
            dmin = float(np.nanmin(data))
            dmax = float(np.nanmax(data))
            self.set_range(dmin, dmax)
            self.schedule_plot()

            if main_window:
                self.show_status_message(
                    f"Stokes changed to {stokes}, display range: [{dmin:.4g}, {dmax:.4g}]"
                )

    def _update_stokes_combo_state(self, available_stokes):
        """
        Update the Stokes combo box to enable/disable items based on available Stokes.

        Args:
            available_stokes: List of available base Stokes, e.g., ["I"] or ["I", "Q", "U", "V"]
        """
        if not hasattr(self, "stokes_combo") or self.stokes_combo is None:
            return

        from PyQt5.QtGui import QBrush, QColor

        # Get theme-aware colors for disabled state - use palette for consistency
        try:
            from ..ui.styles import theme_manager

            palette = theme_manager.palette
            is_dark = theme_manager.is_dark
            # Use the same disabled color from palette as contour dialog
            disabled_color = QColor(palette.get("disabled", "#cccccc"))
            enabled_color = QColor(
                palette.get("text", "#ffffff" if is_dark else "#000000")
            )
        except ImportError:
            # Fallback colors
            disabled_color = QColor("#cccccc")
            enabled_color = QColor("#000000")

        # Changing the current item's flags/colour makes QComboBox emit
        # currentTextChanged, which would reload and replot the image twice.
        # Nothing is selected here, so keep the combo quiet while updating.
        was_blocked = self.stokes_combo.blockSignals(True)
        try:
            # Iterate through combo items and enable/disable based on requirements
            model = self.stokes_combo.model()
            for i in range(self.stokes_combo.count()):
                # Enabled when the image has the planes it is computed from
                enabled = not missing_planes(self.stokes_combo.itemText(i), available_stokes)

                # Set item enabled/disabled using model flags AND foreground color
                item = model.item(i)
                if item:
                    if enabled:
                        item.setFlags(item.flags() | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                        item.setData(QBrush(enabled_color), Qt.ForegroundRole)
                    else:
                        # Remove both enabled and selectable flags
                        item.setFlags(
                            item.flags() & ~Qt.ItemIsEnabled & ~Qt.ItemIsSelectable
                        )
                        item.setData(QBrush(disabled_color), Qt.ForegroundRole)
        finally:
            self.stokes_combo.blockSignals(was_blocked)

        # Store available stokes for later reference
        self._available_stokes = available_stokes

    def _offer_available_stokes(self, available, requested, shown):
        """Enable the Stokes choices the image offers (``available``); if
        ``requested`` could not be shown, say so and select ``shown``."""
        self._update_stokes_combo_state(available)
        if shown == requested:
            return
        QMessageBox.warning(
            self,
            "Stokes Parameter Unavailable",
            f"The selected Stokes parameter '{requested}' requires "
            f"polarization data ({', '.join(missing_planes(requested, available))}) "
            f"that is not available in this image.\n\n"
            f"Available Stokes: {', '.join(available)}\n\n"
            f"Switching to Stokes {shown}.",
        )
        self.stokes_combo.blockSignals(True)
        self.stokes_combo.setCurrentText(shown)
        self.stokes_combo.blockSignals(False)

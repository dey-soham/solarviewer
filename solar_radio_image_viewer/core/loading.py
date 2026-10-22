"""
Reading an image into the tab: the pixels of the chosen Stokes product,
its coordinates and header, Fast mode, and which Stokes products the image
offers.
"""

import logging
import numpy as np
from PyQt5.QtWidgets import (
    QApplication,
    QMessageBox,
)
from PyQt5.QtCore import Qt, QEventLoop
from ..ui.busy import busy
from ..processing.fits_io import is_fits, readable_image_path
from ..processing.image_meta import casa_info, fits_header
from ..processing.prefetch import image_prefetcher
from ..processing.image_data import ImageUnreadable
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

    def _load_fits_stokes(self, imagename, stokes, thres, rms_box=(0, 200, 0, 130)):
        """
        Load a Stokes parameter (or a derived one) from a FITS file with
        astropy, for files CASA cannot read. Returns an [x, y] plane like the
        CASA reader, computed the same way.
        """
        from astropy.io import fits

        from ..processing.stokes import (
            fits_stokes_axis,
            fits_stokes_labels,
            plane_index,
            planes_needed,
            stokes_product,
        )

        with fits.open(readable_image_path(imagename), memmap=True) as hdul:
            header = hdul[0].header
            data = hdul[0].data
            labels = fits_stokes_labels(header)
            stokes_axis = fits_stokes_axis(header)

            def plane(name):
                """The ``name`` plane, first channel of every other axis, as [x, y]."""
                # NumPy axes are the FITS axes reversed: the last two are y, x
                index = [0] * (data.ndim - 2) + [slice(None), slice(None)]
                if stokes_axis is not None:
                    index[data.ndim - stokes_axis] = plane_index(labels, name)
                else:
                    plane_index(None, name)  # only Stokes I without a Stokes axis
                return np.array(data[tuple(index)], dtype=float).T

            planes = {name: plane(name) for name in planes_needed(stokes)}
        return stokes_product(stokes, planes, thres, rms_box)

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

    def load_data(self, imagename, stokes, threshold, auto_adjust_rms=False):
        self._loaded_threshold = threshold
        try:
            # Use the current RMS box when loading data
            from ..processing.image_data import get_pixel_values_from_image

            target_size = self._load_target_size()

            image_prefetcher.settle(imagename)
            prefetched = image_prefetcher.take(
                imagename, stokes, threshold, tuple(self.current_rms_box), target_size
            )
            if prefetched is not None:
                pix, csys, psf = prefetched
            else:
                pix, csys, psf = get_pixel_values_from_image(
                    imagename,
                    stokes,
                    threshold,
                    rms_box=tuple(self.current_rms_box),
                    target_size=target_size,
                )

            self.current_image_data = pix
            self.current_wcs = csys
            self.psf = psf
            self._full_image_shape, self._downsample = self._full_resolution(
                imagename, target_size
            )

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

                # Update image stats when data is loaded
                try:
                    self.show_image_stats(rms_box=self.current_rms_box)
                except Exception as e:
                    print(f"Error showing image stats: {e}")
                    self.show_status_message(f"Error showing image stats: {e}")

        except StokesUnavailable:
            # The image lacks a plane this product needs: say so, rather than
            # showing another plane under this one's name
            raise
        except Exception as e:
            from astropy.io import fits

            # FITS files CASA cannot read
            try:
                pix = self._load_fits_stokes(
                    imagename, stokes, threshold, rms_box=tuple(self.current_rms_box)
                )
            except StokesUnavailable:
                raise
            except Exception as fits_err:
                logger.error(f"Error loading FITS data: {fits_err}")
                # Last resort: the raw pixels of a 2D image, as [x, y]
                try:
                    raw = np.squeeze(fits.getdata(readable_image_path(imagename)))
                except Exception as e2:
                    raise ImageUnreadable(
                        f"Neither CASA nor the FITS reader can read it. CASA: {e}. "
                        f"FITS: {e2}"
                    ) from e2
                if raw.ndim != 2:
                    raise ImageUnreadable(
                        f"CASA cannot read it ({e}), and the FITS reader found no "
                        f"2D image in it ({fits_err})"
                    ) from fits_err
                pix = raw.T
            self.current_image_data = pix
            csys = None
            psf = None
            # Nothing of the previous image's may remain: its beam and its
            # coordinate system (zoom to 1°, the solar disk, HPC detection)
            self.psf = None
            self.current_wcs = None
            # The FITS reader does not downsample
            self._full_image_shape = pix.shape
            self._downsample = 1

        if pix is not None:
            nx, ny = pix.shape  # the image is indexed [x, y]
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
            from astropy.io import fits

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

            if isinstance(e, StokesUnavailable):
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

    def _validate_and_switch_stokes(self, imagename, selected_stokes):
        """
        Check if selected Stokes is available; if not, warn user and switch to I.

        Args:
            imagename: Path to the image
            selected_stokes: Currently selected Stokes parameter

        Returns:
            str: The validated Stokes parameter to use
        """
        from ..processing.stokes import get_available_stokes

        available_stokes = get_available_stokes(imagename)

        # Update combo box state
        self._update_stokes_combo_state(available_stokes)

        missing_stokes = missing_planes(selected_stokes, available_stokes)
        if missing_stokes:
            # Show warning dialog
            QMessageBox.warning(
                self,
                "Stokes Parameter Unavailable",
                f"The selected Stokes parameter '{selected_stokes}' requires "
                f"polarization data ({', '.join(missing_stokes)}) that is not available "
                f"in this image.\n\n"
                f"Available Stokes: {', '.join(available_stokes)}\n\n"
                f"Switching to Stokes I.",
            )

            # Switch to Stokes I
            self.stokes_combo.blockSignals(True)
            self.stokes_combo.setCurrentText("I")
            self.stokes_combo.blockSignals(False)
            return "I"

        return selected_stokes

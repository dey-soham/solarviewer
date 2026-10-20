"""
Contour overlays: settings, loading/reprojecting contour data, drawing.
"""

import os

import numpy as np
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialogButtonBox, QMessageBox

from ..processing.dependencies import IA
from ..processing.image_data import get_pixel_values_from_image
from ..processing.fits_io import readable_image_path


class ContoursMixin:
    """Contour settings dialog, contour data loading/reprojection and drawing.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``_cached_csys``, ``_cached_fits_flag``, ``_cached_fits_header``,
    ``_cached_summary``, ``contour_settings``, ``current_image_data``,
    ``_get_coordinate_system_from_image``, ``imagename``, ``_open_dialogs``,
    ``parent``, ``_reproject_cache``, ``show_status_message``,
    ``_temp_file_id``, ``window``
    """

    def show_contour_settings(self):
        """Show non-modal contour settings dialog."""
        from ..dialogs import ContourSettingsDialog

        # If dialog already exists and is visible, just raise it
        if (
            hasattr(self, "_contour_settings_dialog")
            and self._contour_settings_dialog is not None
        ):
            try:
                if self._contour_settings_dialog.isVisible():
                    self._contour_settings_dialog.raise_()
                    self._contour_settings_dialog.activateWindow()
                    return
            except RuntimeError:
                self._contour_settings_dialog = None

        dialog = ContourSettingsDialog(self, self.contour_settings)
        self._contour_settings_dialog = dialog

        # Store reference to viewer
        viewer = self

        def on_apply():
            """Apply settings without closing dialog."""
            try:
                from PyQt5 import sip

                if sip.isdeleted(dialog):
                    return

                if not viewer:
                    # This case should be rare as viewer is 'self'
                    try:
                        dialog.close()
                    except RuntimeError:
                        pass
                    return

                viewer.contour_settings = dialog.get_settings()
                if viewer.show_contours_checkbox.isChecked():
                    viewer.load_contour_data()
                    viewer.on_visualization_changed()

                    # Update dialog with captured dimensions
                    w = viewer.contour_settings.get("dim_w", 0)
                    h = viewer.contour_settings.get("dim_h", 0)
                    if hasattr(dialog, "update_dimensions_label") and not sip.isdeleted(
                        dialog
                    ):
                        dialog.update_dimensions_label(w, h)

                viewer.show_status_message("Contour settings applied")
            except RuntimeError:
                # Dialog or a child widget was likely deleted during processing
                pass
            except Exception as e:
                if viewer:
                    viewer.show_status_message(f"Error applying settings: {e}")

        def on_close():
            dialog.close()

        def on_dialog_destroyed():
            if hasattr(viewer, "_contour_settings_dialog"):
                viewer._contour_settings_dialog = None

        # Replace the standard button box with Apply/Close
        # The dialog already has a button box, so we need to modify it
        button_box = dialog.findChild(QDialogButtonBox)
        if button_box:
            button_box.clear()
            apply_btn = button_box.addButton("Apply", QDialogButtonBox.ApplyRole)
            close_btn = button_box.addButton("Close", QDialogButtonBox.RejectRole)
            apply_btn.clicked.connect(on_apply)
            close_btn.clicked.connect(on_close)

        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(on_dialog_destroyed)

        # Track dialog for garbage collection
        dialog.destroyed.connect(
            lambda: (
                self._open_dialogs.remove(dialog)
                if dialog in self._open_dialogs
                else None
            )
        )
        self._open_dialogs.append(dialog)

        dialog.show()

    def _get_image_dimensions(self, imagepath):
        """
        Get dimensions (height, width) of an image file (FITS or CASA).

        Parameters
        ----------
        imagepath : str
            Path to the image file or directory

        Returns
        -------
        tuple
            (height, width) in pixels, or (0, 0) on failure
        """
        imagepath = readable_image_path(imagepath)
        height, width = 0, 0
        if not imagepath or not os.path.exists(imagepath):
            return height, width

        try:
            if os.path.isdir(imagepath):  # CASA image
                ia_tool = IA()
                ia_tool.open(imagepath)
                shape = ia_tool.shape()
                ia_tool.close()
                # CASA shape is usually [width, height, stokes, freq]
                if len(shape) >= 2:
                    width, height = shape[0], shape[1]
            else:  # FITS file
                from astropy.io import fits

                # Use headers to be fast, avoid loading full data
                header = fits.getheader(imagepath)
                # NAXIS1 is width (x), NAXIS2 is height (y)
                width = header.get("NAXIS1", 0)
                height = header.get("NAXIS2", 0)
        except Exception as e:
            print(f"[ERROR] Could not get image dimensions for {imagepath}: {e}")

        return height, width

    def load_contour_data(self):
        try:
            if self.contour_settings["source"] == "external":
                h, w = self._get_image_dimensions(
                    self.contour_settings["external_image"]
                )
            else:
                h, w = self._get_image_dimensions(self.imagename)

            if h > 0 and w > 0:
                self.contour_settings["dim_h"] = h
                self.contour_settings["dim_w"] = w
                # use default region logic
                rms_x1 = int(w * 0.05)
                rms_y1 = int(h * 0.05)
                rms_x2 = int(w * 0.95)
                rms_y2 = int(h * 0.20)
                rms_box = (rms_x1, rms_x2, rms_y1, rms_y2)

                # Ensure valid region
                if rms_x2 < rms_x1 or rms_y2 < rms_y1:
                    print("[Warning] Invalid RMS region. Using default RMS region.")
                    rms_box = (30, 200, 30, 130)
            else:
                print("[Warning] Invalid image dimensions. Using default RMS region.")
                rms_box = (30, 200, 30, 130)

            # Calculate target size for fast load mode (must match load_data)
            target_size = 0  # 0 = no downsampling
            if (
                hasattr(self, "downsample_toggle")
                and self.downsample_toggle.isChecked()
            ):
                target_size = 800  # Smart downsampling to ~800px max dimension
                rms_box = (40, 400, 40, 160)

            if not self.contour_settings.get("use_default_rms_region", True):
                rms_box = self.contour_settings.get("rms_box", rms_box)
            else:
                self.contour_settings["rms_box"] = rms_box

            contour_csys = None

            self._validate_contour_stokes()

            if self.contour_settings["source"] == "same":
                if self.imagename:
                    stokes = self.contour_settings["stokes"]
                    threshold = self.contour_settings.get("threshold", 5.0)

                    contour_csys = self._load_contour_stokes(
                        self.imagename, stokes, threshold, rms_box, target_size
                    )
                    self.current_contour_wcs = contour_csys
                else:
                    self.contour_settings["contour_data"] = None
                    self.current_contour_wcs = None
            else:
                external_image = self.contour_settings["external_image"]
                if external_image and os.path.exists(external_image):
                    stokes = self.contour_settings["stokes"]
                    threshold = self.contour_settings.get("threshold", 5.0)

                    image_to_load = self._resolve_contour_image(external_image)

                    contour_csys = self._load_contour_stokes(
                        image_to_load, stokes, threshold, rms_box, target_size
                    )
                    self.current_contour_wcs = contour_csys
                else:
                    print(
                        "[ERROR] load_contour_data: external image not found or empty"
                    )
                    self.contour_settings["contour_data"] = None
                    self.current_contour_wcs = None

            main_window = self.parent()
            if main_window:
                if self.contour_settings["contour_data"] is not None:
                    self.show_status_message(
                        f"Contour data loaded: {self.contour_settings['source']} image, Stokes {self.contour_settings['stokes']}"
                    )
                else:
                    self.show_status_message("Failed to load contour data")
        except Exception as e:
            print(f"[ERROR] Error loading contour data: {e}")
            self.show_status_message(f"Error loading contour data: {e}")
            self.contour_settings["contour_data"] = None

    def _validate_contour_stokes(self):
        """Fall back to an available Stokes if the requested one isn't in the source image."""
        # Validate contour Stokes before loading
        contour_stokes = self.contour_settings.get("stokes", "I")
        source_image = None

        if self.contour_settings["source"] == "same":
            source_image = self.imagename
        else:
            source_image = self.contour_settings.get("external_image")

        if source_image:
            from ..processing.stokes import get_available_stokes

            available_stokes = get_available_stokes(source_image)

            # Check if contour Stokes is valid
            requires_q = {"Q", "Q/I", "L", "Lfrac", "PANG"}
            requires_u = {"U", "U/I", "V/I", "L", "Lfrac", "PANG"}
            requires_v = {"V", "Vfrac", "V/I"}

            has_q = "Q" in available_stokes
            has_u = "U" in available_stokes
            has_v = "V" in available_stokes

            needs_switch = False
            missing_stokes = []

            if contour_stokes in requires_q and not has_q:
                needs_switch = True
                missing_stokes.append("Q")
            if contour_stokes in requires_u and not has_u:
                needs_switch = True
                missing_stokes.append("U")
            if contour_stokes in requires_v and not has_v:
                needs_switch = True
                missing_stokes.append("V")

            if needs_switch:
                # Show warning dialog and switch to Stokes I
                QMessageBox.warning(
                    self,
                    "Contour Stokes Unavailable",
                    f"The contour Stokes parameter '{contour_stokes}' requires "
                    f"polarization data ({', '.join(set(missing_stokes))}) that is not available "
                    f"in the source image.\n\n"
                    f"Available Stokes: {', '.join(available_stokes)}\n\n"
                    f"Switching contour to Stokes I.",
                )
                self.contour_settings["stokes"] = "I"
                contour_stokes = "I"

    def _resolve_contour_image(self, external_image):
        """Path of the contour image to load, converting its coordinate system
        (RA/Dec <-> helioprojective) to match the base image if needed."""
        # Check if we need to transform coordinate systems
        # Detect coordinate systems of base image and contour image
        base_csys_type = self._get_coordinate_system_from_image(self.imagename)
        contour_csys_type = self._get_coordinate_system_from_image(external_image)

        # Use cached transformed file if available and from same source
        image_to_load = external_image

        if (
            self._contour_transformed_file
            and self._contour_transformed_from == external_image
            and os.path.exists(self._contour_transformed_file)
        ):
            # Check if transformation is still needed
            transformed_csys = self._get_coordinate_system_from_image(
                self._contour_transformed_file
            )
            if transformed_csys == base_csys_type:
                image_to_load = self._contour_transformed_file
                self.show_status_message(
                    f"Using cached transformed contour ({contour_csys_type}→{base_csys_type})"
                )
            else:
                # Transformation no longer matches (base image changed), cleanup and retransform
                try:
                    os.remove(self._contour_transformed_file)
                except Exception:
                    pass
                self._contour_transformed_file = None
                self._contour_transformed_from = None

        # Check if transformation is needed
        if (
            base_csys_type != "unknown"
            and contour_csys_type != "unknown"
            and base_csys_type != contour_csys_type
            and image_to_load == external_image
        ):

            # Clear old cached file if external image changed
            if self._contour_transformed_from != external_image:
                if self._contour_transformed_file and os.path.exists(
                    self._contour_transformed_file
                ):
                    try:
                        os.remove(self._contour_transformed_file)
                    except Exception:
                        pass
                self._contour_transformed_file = None
                self._contour_transformed_from = None

            # Create temp file for coordinate-transformed contour
            import tempfile

            temp_dir = tempfile.gettempdir()
            temp_filename = f"sv_contour_transformed_{self._temp_file_id}.fits"
            transformed_path = os.path.join(temp_dir, temp_filename)

            transform_success = False

            if base_csys_type == "hpc" and contour_csys_type == "radec":
                # Convert RA/Dec contour to HPC (all Stokes for full flexibility)
                self.show_status_message(f"Transforming contour from RA/Dec to HPC...")
                from ..coordinates.helioprojective import (
                    convert_and_save_hpc_all_stokes,
                )

                transform_success = convert_and_save_hpc_all_stokes(
                    external_image, transformed_path, overwrite=True
                )

            elif base_csys_type == "radec" and contour_csys_type == "hpc":
                # Convert HPC contour to RA/Dec
                self.show_status_message(f"Transforming contour from HPC to RA/Dec...")
                from ..coordinates.helioprojective import convert_hpc_to_radec

                transform_success = convert_hpc_to_radec(
                    external_image, transformed_path, overwrite=True
                )

            if transform_success and os.path.exists(transformed_path):
                image_to_load = transformed_path
                self._contour_transformed_file = transformed_path
                self._contour_transformed_from = external_image
                self.show_status_message(
                    f"Contour transformed ({contour_csys_type}→{base_csys_type})"
                )
            else:
                print(
                    f"[ERROR] Transformation failed, using original: {external_image}"
                )
                self.show_status_message(
                    f"Coordinate transformation failed, using original contour"
                )

        elif (
            contour_csys_type != "unknown"
            and base_csys_type == contour_csys_type
            and self._contour_transformed_from == external_image
        ):
            # Coordinates now match (base image changed back), cleanup temp file
            if self._contour_transformed_file and os.path.exists(
                self._contour_transformed_file
            ):
                try:
                    os.remove(self._contour_transformed_file)
                except Exception:
                    pass
            self._contour_transformed_file = None
            self._contour_transformed_from = None
        return image_to_load

    def _load_contour_stokes(self, image_path, stokes, threshold, rms_box, target_size):
        """Load contour data for a Stokes product (I/Q/U/V, X/I, L, Lfrac, PANG)
        into contour_settings["contour_data"]; returns the image's coordsys."""
        if stokes in ["I", "Q", "U", "V"]:
            pix, contour_csys, _ = get_pixel_values_from_image(
                image_path,
                stokes,
                threshold,
                rms_box,
                target_size=target_size,
            )
            self.contour_settings["contour_data"] = pix

        elif stokes in ["Q/I", "U/I", "V/I"]:
            from ..processing.rms import estimate_rms_near_Sun

            numerator_stokes = stokes.split("/")[0]
            numerator_pix, contour_csys, _ = get_pixel_values_from_image(
                image_path,
                numerator_stokes,
                threshold,
                rms_box,
                target_size=target_size,
            )
            denominator_pix, contour_csys, _ = get_pixel_values_from_image(
                image_path,
                "I",
                threshold,
                rms_box,
                target_size=target_size,
            )
            # Estimate RMS of the numerator Stokes for noise thresholding
            try:
                num_rms = estimate_rms_near_Sun(image_path, numerator_stokes, rms_box)
            except Exception:
                print(
                    "[WARNING] Failed to estimate RMS, using standard deviation instead"
                )
                num_rms = np.nanstd(numerator_pix)

            # Create mask: pixels below threshold * RMS are masked out
            noise_mask = np.abs(numerator_pix) < (threshold * num_rms)
            numerator_pix_masked = numerator_pix.copy()
            numerator_pix_masked[noise_mask] = 0

            # Division mask: avoid divide by zero
            div_mask = denominator_pix != 0
            ratio = np.zeros_like(numerator_pix)
            ratio[div_mask] = numerator_pix_masked[div_mask] / denominator_pix[div_mask]
            self.contour_settings["contour_data"] = ratio
        elif stokes == "L":
            q_pix, contour_csys, _ = get_pixel_values_from_image(
                image_path,
                "Q",
                threshold,
                rms_box,
                target_size=target_size,
            )
            u_pix, contour_csys, _ = get_pixel_values_from_image(
                image_path,
                "U",
                threshold,
                rms_box,
                target_size=target_size,
            )
            l_pix = np.sqrt(q_pix**2 + u_pix**2)
            self.contour_settings["contour_data"] = l_pix
        elif stokes == "Lfrac":
            from ..processing.rms import estimate_rms_near_Sun

            q_pix, contour_csys, _ = get_pixel_values_from_image(
                image_path,
                "Q",
                threshold,
                rms_box,
                target_size=target_size,
            )
            u_pix, contour_csys, _ = get_pixel_values_from_image(
                image_path,
                "U",
                threshold,
                rms_box,
                target_size=target_size,
            )
            i_pix, contour_csys, _ = get_pixel_values_from_image(
                image_path,
                "I",
                threshold,
                rms_box,
                target_size=target_size,
            )
            l_pix = np.sqrt(q_pix**2 + u_pix**2)

            # Estimate RMS for polarized intensity
            try:
                q_rms = estimate_rms_near_Sun(image_path, "Q", rms_box)
                u_rms = estimate_rms_near_Sun(image_path, "U", rms_box)
                l_rms = np.sqrt(q_rms**2 + u_rms**2)
            except Exception:
                print(
                    "[WARNING] Failed to estimate RMS, using standard deviation instead"
                )
                l_rms = np.nanstd(l_pix)

            # Mask pixels below threshold
            noise_mask = l_pix < (threshold * l_rms)
            l_pix_masked = l_pix.copy()
            l_pix_masked[noise_mask] = 0

            # Division with zero protection
            div_mask = i_pix != 0
            lfrac = np.zeros_like(l_pix)
            lfrac[div_mask] = l_pix_masked[div_mask] / i_pix[div_mask]
            self.contour_settings["contour_data"] = lfrac
        elif stokes == "PANG":
            from ..processing.rms import estimate_rms_near_Sun

            q_pix, contour_csys, _ = get_pixel_values_from_image(
                image_path,
                "Q",
                threshold,
                rms_box,
                target_size=target_size,
            )
            u_pix, contour_csys, _ = get_pixel_values_from_image(
                image_path,
                "U",
                threshold,
                rms_box,
                target_size=target_size,
            )

            # Calculate polarized intensity for thresholding
            l_pix = np.sqrt(q_pix**2 + u_pix**2)

            # Estimate RMS for polarized intensity
            try:
                q_rms = estimate_rms_near_Sun(image_path, "Q", rms_box)
                u_rms = estimate_rms_near_Sun(image_path, "U", rms_box)
                l_rms = np.sqrt(q_rms**2 + u_rms**2)
            except Exception:
                print(
                    "[WARNING] Failed to estimate RMS, using standard deviation instead"
                )
                l_rms = np.nanstd(l_pix)

            # Polarization angle
            pang = 0.5 * np.arctan2(u_pix, q_pix) * 180 / np.pi

            # Mask pixels below threshold with NaN
            noise_mask = l_pix < (threshold * l_rms)
            pang[noise_mask] = np.nan
            self.contour_settings["contour_data"] = pang
        else:
            pix, contour_csys, _ = get_pixel_values_from_image(
                image_path,
                "I",
                threshold,
                rms_box,
                target_size=target_size,
            )
            self.contour_settings["contour_data"] = pix
        return contour_csys

    def draw_contours(self, ax):
        main_window = self.window()
        if self.contour_settings["contour_data"] is None:
            self.load_contour_data()

        if self.contour_settings["contour_data"] is None:
            print(
                "[ERROR] draw_contours: contour_data is None after load_contour_data()"
            )
            return

        if self.current_contour_wcs is None:
            print("[ERROR] draw_contours: current_contour_wcs is None")
            return

        # Reset contour offset (will be set if extended reprojection is used)
        self._contour_offset = [0, 0]

        # OPTIMIZATION: Fast path for same-image contours
        # When contour source is the current image, skip expensive metadata loading
        # and reprojection since the data is already aligned
        is_same_image = self.contour_settings["source"] == "same"

        metadata = self._contour_source_metadata(is_same_image)
        if metadata is None:
            return
        fits_flag, header, csys, summary = metadata

        try:
            # Check if the contour and image projection match
            self.show_status_message("Drawing contours.... Please wait...")
            image_wcs_obj = None
            if hasattr(self, "_cached_wcs_obj"):
                image_wcs_obj = self._cached_wcs_obj

            # Calculate contour levels

            contour_data = self.contour_settings["contour_data"]
            abs_max = np.nanmax(np.abs(contour_data))

            pos_levels, neg_levels = self._compute_contour_levels(contour_data, abs_max)

            (
                contour_wcs_obj,
                fits_flag,
                image_wcs_for_reproject,
                needs_reprojection,
            ) = self._prepare_contour_reprojection(
                contour_data,
                csys,
                fits_flag,
                header,
                image_wcs_obj,
                is_same_image,
                summary,
            )

            contour_data = self._downsample_contour_for_display(
                contour_data, needs_reprojection
            )

            proceed, contour_data = self._reproject_contour_data(
                contour_data,
                contour_wcs_obj,
                image_wcs_for_reproject,
                fits_flag,
                is_same_image,
                main_window,
                needs_reprojection,
            )
            if not proceed:
                return

            # For reprojected data, the output matches the base image orientation
            # so we transpose to match how the base image is displayed
            display_contour_data = contour_data.transpose()

            # Get offset for extended canvas positioning
            contour_offset = getattr(self, "_contour_offset", [0, 0])

            # Calculate extent for proper positioning when using extended canvas
            # extent = [left, right, bottom, top] in data coordinates

            ds_factor = getattr(self, "_contour_ds_factor", 1)

            # Apply extent if offset is non-zero OR downsampling is active (since pixels differ from base)
            if contour_offset != [0, 0] or ds_factor > 1:
                extent = [
                    -contour_offset[1],  # left (shifted by x offset)
                    -contour_offset[1]
                    + (
                        display_contour_data.shape[0] * ds_factor
                    ),  # right (add Width / shape[0])
                    -contour_offset[0],  # bottom (shifted by y offset)
                    -contour_offset[0]
                    + (
                        display_contour_data.shape[1] * ds_factor
                    ),  # top (add Height / shape[1])
                ]
            else:
                extent = None  # Use default (0 to shape)

            self._draw_contour_set(
                ax, display_contour_data, extent, pos_levels, "pos", "positive"
            )
            self._draw_contour_set(
                ax, display_contour_data, extent, neg_levels, "neg", "negative"
            )

            if main_window:

                self.show_status_message("Done. ")

        except Exception as e:
            print(f"[ERROR] Error drawing contours: {e}")
            import traceback

            traceback.print_exc()
            main_window = self.parent()
            if main_window:
                self.show_status_message(f"Error drawing contours: {str(e)}")

    def _draw_contour_set(self, ax, display_contour_data, extent, levels, sign, label):
        """Draw one set of contour levels; sign is "pos" or "neg"."""
        if not levels or len(levels) == 0:
            return
        settings = self.contour_settings
        style = dict(
            levels=levels,
            colors=settings.get(f"{sign}_color", settings["color"]),
            linewidths=settings.get(f"{sign}_linewidth", settings["linewidth"]),
            linestyles=settings[f"{sign}_linestyle"],
        )
        try:
            if extent:
                # display_contour_data is transposed (X, Y): rows map to the
                # vertical axis (X-range extent[0:2]), columns to the horizontal
                # axis (Y-range extent[2:4]).
                y = np.linspace(extent[0], extent[1], display_contour_data.shape[0])
                x = np.linspace(extent[2], extent[3], display_contour_data.shape[1])
                cs = ax.contour(x, y, display_contour_data, **style)
            else:
                cs = ax.contour(display_contour_data, origin="lower", **style)

            # Add contour labels if enabled
            if settings.get("show_labels", False):
                ax.clabel(cs, inline=True, fontsize=8, fmt="%.2g")

        except Exception as e:
            print(f"[ERROR] Error drawing {label} contours: {e}, levels: {levels}")
            self.show_status_message(
                f"Error drawing {label} contours: {e}, levels: {levels}"
            )

    def _contour_source_metadata(self, is_same_image):
        """FITS header / CASA coordsys of the contour source image.

        Returns (fits_flag, header, csys, summary), or None if it can't be read.
        """
        fits_flag = False
        header = None
        csys = None
        summary = None

        if is_same_image:
            # Use cached metadata from plot_image - no need to reload
            fits_flag = self._cached_fits_flag
            header = self._cached_fits_header or {}
            csys = self._cached_csys
            summary = self._cached_summary
            contour_imagename = self.imagename
        else:
            # External image - need to load metadata
            # Use transformed file if coordinate transformation was applied
            if (
                self._contour_transformed_file
                and os.path.exists(self._contour_transformed_file)
                and self._contour_transformed_from
                == self.contour_settings.get("external_image")
            ):
                contour_imagename = self._contour_transformed_file
            else:
                contour_imagename = self.contour_settings["external_image"]

            if contour_imagename.endswith(".fits") or contour_imagename.endswith(
                ".fts"
            ):

                from astropy.io import fits

                fits_flag = True
                try:
                    with fits.open(readable_image_path(contour_imagename), memmap=True) as hdul:
                        header = dict(hdul[0].header)
                except Exception as e:
                    print(f"[ERROR] Error getting contour FITS header: {e}")
                    header = {}

            try:
                ia_tool = IA()
                ia_tool.open(readable_image_path(contour_imagename))
                csys = ia_tool.coordsys()
                summary = ia_tool.summary()
                ia_tool.close()
            except Exception as e:
                print(f"[ERROR] Error getting metadata: {e}")
                self.show_status_message(f"Error getting metadata: {e}")
                return None
        return fits_flag, header, csys, summary

    def _compute_contour_levels(self, contour_data, abs_max):
        """Positive/negative contour levels for the configured level_type."""
        if self.contour_settings["level_type"] == "fraction":
            vmax = np.nanmax(contour_data)
            vmin = np.nanmin(contour_data)
            if vmax > 0:
                pos_levels = sorted(
                    [level * abs_max for level in self.contour_settings["pos_levels"]]
                )
            else:
                pos_levels = []

            if vmin < 0:
                neg_levels = sorted(
                    [level * abs_max for level in self.contour_settings["neg_levels"]]
                )
                neg_levels = [-level for level in reversed(neg_levels)]
            else:
                neg_levels = []

        elif self.contour_settings["level_type"] == "sigma":
            # For sigma levels, calculate RMS from the RMS box region of the contour data
            # This gives a proper noise estimate without source contamination
            rms_box = self.contour_settings.get("rms_box", (0, 200, 0, 130))

            try:
                # Calculate RMS from the specified region
                rms_region = contour_data[
                    rms_box[0] : rms_box[1], rms_box[2] : rms_box[3]
                ]
                rms = np.sqrt(np.nanmean(rms_region**2))
            except Exception as e:
                # Fallback to using std of entire image if RMS box fails
                print(f"[WARNING] Could not calculate RMS from box, using std: {e}")
                self.show_status_message(
                    f"WARNING: Could not calculate RMS from box, using std: {e}"
                )
                rms = np.nanstd(contour_data)

            # Positive levels: level * rms (e.g., 3σ, 6σ, 9σ...)
            pos_levels = sorted(
                [level * rms for level in self.contour_settings["pos_levels"]]
            )
            # Negative levels: -level * rms (e.g., -3σ, -6σ, -9σ...)
            # Must be in increasing order for matplotlib (-30, -20, -10)
            neg_levels = sorted(
                [-level * rms for level in self.contour_settings["neg_levels"]]
            )

        else:
            pos_levels = sorted(self.contour_settings["pos_levels"])
            neg_levels = sorted(
                [-level for level in reversed(self.contour_settings["neg_levels"])]
            )
        return pos_levels, neg_levels

    def _prepare_contour_reprojection(
        self,
        contour_data,
        csys,
        fits_flag,
        header,
        image_wcs_obj,
        is_same_image,
        summary,
    ):
        """Build the contour image's WCS and decide whether it must be reprojected.

        Returns (contour_wcs_obj, fits_flag, image_wcs_for_reproject,
        needs_reprojection).
        """
        image_wcs_for_reproject = None
        contour_wcs_obj = None  # Initialize before the condition block

        # Check if reprojection is needed (WCS differs between contour and image)
        # OPTIMIZATION: Skip reprojection check for same-image contours - they're already aligned
        needs_reprojection = False

        if (
            not is_same_image
            and self.current_contour_wcs is not None
            and image_wcs_obj is not None
        ):
            contour_wcs_obj, fits_flag = self._build_contour_wcs(
                csys, fits_flag, header, image_wcs_obj, summary
            )

            image_wcs_for_reproject = self._swapped_image_wcs(image_wcs_obj)

            # Check for different projections and warn user
            # Extract projection type from ctype (e.g., "SIN" from "RA---SIN" or "DEC--SIN")
            def get_projection_type(ctype):
                if not ctype:
                    return ""
                # SOLAR-X/SOLAR-Y are equivalent to TAN projection
                if "SOLAR-X" in ctype.upper() or "SOLAR-Y" in ctype.upper():
                    return "TAN"
                if len(ctype) >= 3:
                    return ctype[-3:]  # Get last 3 chars (SIN, TAN, etc.)
                return ""

            contour_proj = get_projection_type(contour_wcs_obj.wcs.ctype[0])
            image_proj = get_projection_type(image_wcs_for_reproject.wcs.ctype[0])

            if contour_proj != image_proj:
                # Show warning dialog to user
                QMessageBox.warning(
                    self,
                    "Projection Mismatch",
                    f"The contour image uses a different projection than the base image:\n\n"
                    f"Base image: {image_proj}\n"
                    f"Contour image: {contour_proj}\n\n"
                    f"Contours will be plotted but may not align perfectly.",
                )

            # Check for different increments (pixel scales)
            contour_cdelt = np.abs(contour_wcs_obj.wcs.cdelt)
            image_cdelt = np.abs(image_wcs_obj.wcs.cdelt)
            scale_ratio_x = contour_cdelt[0] / image_cdelt[0]
            scale_ratio_y = contour_cdelt[1] / image_cdelt[1]

            if (np.abs(scale_ratio_x - 1) > 1e-3) or (np.abs(scale_ratio_y - 1) > 1e-3):
                needs_reprojection = True

            # Check for different reference pixels
            contour_crpix = np.array(contour_wcs_obj.wcs.crpix)
            image_crpix = np.array(image_wcs_obj.wcs.crpix)
            if np.any(np.abs(contour_crpix - image_crpix) > 1e-3):
                needs_reprojection = True

            # Check for different reference values
            contour_crval = np.array(contour_wcs_obj.wcs.crval)
            image_crval = np.array(image_wcs_obj.wcs.crval)
            if np.any(np.abs(contour_crval - image_crval) > 1e-6):
                needs_reprojection = True

            # Check if image shapes differ
            if contour_data.shape != self.current_image_data.shape:
                needs_reprojection = True
        return contour_wcs_obj, fits_flag, image_wcs_for_reproject, needs_reprojection

    def _build_contour_wcs(self, csys, fits_flag, header, image_wcs_obj, summary):
        """2D astropy WCS for the contour image (axes swapped to match the base image).

        Returns (contour_wcs_obj, fits_flag).
        """
        # Build the contour WCS object
        from astropy.wcs import WCS

        contour_wcs_obj = WCS(naxis=2)

        # For FITS files, we need to handle the WCS carefully:
        # - Use current_contour_wcs for CRPIX/CRVAL/CDELT (which is adjusted for downsampling)
        # - But for HPC FITS files, CASA misinterprets the CTYPE, so use header for CTYPE
        if fits_flag and header:
            try:
                # Get downsampling-adjusted values from CASA coordsys
                ref_val = self.current_contour_wcs.referencevalue()["numeric"][0:2]
                ref_pix = self.current_contour_wcs.referencepixel()["numeric"][0:2]
                increment = self.current_contour_wcs.increment()["numeric"][0:2]

                # Swap axes for numpy (row, col) = (y, x) order
                contour_wcs_obj.wcs.crpix = [
                    ref_pix[1] + 1,
                    ref_pix[0] + 1,
                ]  # CASA 0-indexed -> FITS 1-indexed

                # Get CTYPE from FITS header (correct for HPC)
                ctype1 = header.get("CTYPE1", "")
                ctype2 = header.get("CTYPE2", "")
                is_hpc = (
                    "HPLN" in ctype1
                    or "HPLT" in ctype1
                    or "HPLN" in ctype2
                    or "HPLT" in ctype2
                    or "SOLAR-X" in ctype1.upper()
                    or "SOLAR-Y" in ctype2.upper()
                )

                if is_hpc:
                    # Distinguish HPLN-TAN (CASA stores in radians) from
                    # SOLAR-X (CASA stores as linear coords in arcsec)
                    is_solar_xy = (
                        "SOLAR-X" in ctype1.upper() or "SOLAR-Y" in ctype2.upper()
                    )
                    if is_solar_xy:
                        # SOLAR-X/Y: CASA coordsys has arcsec values
                        # Convert arcsec → degrees for astropy WCS
                        crval1 = ref_val[0] / 3600.0
                        crval2 = ref_val[1] / 3600.0
                        cdelt1 = increment[0] / 3600.0
                        cdelt2 = increment[1] / 3600.0
                    else:
                        # HPLN/HPLT: CASA coordsys is in radians
                        # Convert to degrees for WCS reprojection
                        crval1 = ref_val[0] * 180.0 / np.pi
                        crval2 = ref_val[1] * 180.0 / np.pi
                        cdelt1 = increment[0] * 180.0 / np.pi
                        cdelt2 = increment[1] * 180.0 / np.pi
                else:
                    # For RA/Dec, CASA coordsys is already in radians, convert to degrees
                    crval1 = ref_val[0] * 180.0 / np.pi
                    crval2 = ref_val[1] * 180.0 / np.pi
                    cdelt1 = increment[0] * 180.0 / np.pi
                    cdelt2 = increment[1] * 180.0 / np.pi

                contour_wcs_obj.wcs.crval = [crval2, crval1]  # Swapped
                contour_wcs_obj.wcs.cdelt = [cdelt2, cdelt1]  # Swapped

                # Map SOLAR-X/Y to standard celestial types for reproject
                def _to_celestial_ctype(ct):
                    ct_upper = ct.upper()
                    if "SOLAR-X" in ct_upper:
                        return "HPLN-TAN"
                    elif "SOLAR-Y" in ct_upper:
                        return "HPLT-TAN"
                    return ct

                contour_wcs_obj.wcs.ctype = [
                    _to_celestial_ctype(ctype2),
                    _to_celestial_ctype(ctype1),
                ]  # Map to celestial types for reproject
            except Exception as e:

                print(f"[ERROR] Failed to build WCS from FITS header: {e}")
                # Fall back to CASA coordsys without FITS header
                fits_flag = False

        if not fits_flag:
            # Use CASA coordsys (for CASA images or as fallback)
            ref_val = self.current_contour_wcs.referencevalue()["numeric"][0:2]
            ref_pix = self.current_contour_wcs.referencepixel()["numeric"][0:2]
            increment = self.current_contour_wcs.increment()["numeric"][0:2]

            # IMPORTANT: CASA WCS stores in (RA, Dec) = (x, y) order
            # But numpy arrays are (row, col) = (y, x)
            # We need to SWAP the axes for proper alignment with reproject
            contour_wcs_obj.wcs.crpix = [
                ref_pix[1] + 1,
                ref_pix[0] + 1,
            ]  # Swap to (y, x); CASA 0-indexed -> FITS 1-indexed

            if "Right Ascension" in summary["axisnames"]:
                contour_wcs_obj.wcs.crval = [
                    ref_val[1] * 180 / np.pi,  # Dec first
                    ref_val[0] * 180 / np.pi,  # RA second
                ]
                contour_wcs_obj.wcs.cdelt = [
                    increment[1] * 180 / np.pi,
                    increment[0] * 180 / np.pi,
                ]
                contour_wcs_obj.wcs.ctype = ["DEC--SIN", "RA---SIN"]
            else:
                contour_wcs_obj.wcs.crval = [ref_val[1], ref_val[0]]
                contour_wcs_obj.wcs.cdelt = [increment[1], increment[0]]

        # Set projection type for contour WCS (swapped to match axis order)
        if fits_flag:
            try:
                # Swap CTYPE order to match swapped axes
                # Map SOLAR-X/Y to celestial types for reproject
                def _to_celestial_ctype2(ct):
                    ct_upper = ct.upper()
                    if "SOLAR-X" in ct_upper:
                        return "HPLN-TAN"
                    elif "SOLAR-Y" in ct_upper:
                        return "HPLT-TAN"
                    return ct

                contour_wcs_obj.wcs.ctype = [
                    _to_celestial_ctype2(header["CTYPE2"]),
                    _to_celestial_ctype2(header["CTYPE1"]),
                ]
            except Exception as e:
                print(f"[ERROR] Error getting projection type from FITS: {e}")
                self.show_status_message(
                    f"Error getting projection type from FITS: {e}"
                )
                # Use swapped image ctype, mapping SOLAR-X/Y if needed
                ct0 = image_wcs_obj.wcs.ctype[1]
                ct1 = image_wcs_obj.wcs.ctype[0]
                if "SOLAR-X" in ct1.upper():
                    ct1 = "HPLN-TAN"
                if "SOLAR-Y" in ct0.upper():
                    ct0 = "HPLT-TAN"
                contour_wcs_obj.wcs.ctype = [ct0, ct1]
        elif (csys.projection()["type"] == "SIN") and (
            "Right Ascension" in summary["axisnames"]
        ):
            contour_wcs_obj.wcs.ctype = ["DEC--SIN", "RA---SIN"]  # Swapped
        elif (csys.projection()["type"] == "TAN") and (
            "Right Ascension" in summary["axisnames"]
        ):
            contour_wcs_obj.wcs.ctype = ["DEC--TAN", "RA---TAN"]  # Swapped
        else:
            # Use swapped image ctype, mapping SOLAR-X/Y if needed
            ct0 = image_wcs_obj.wcs.ctype[1]
            ct1 = image_wcs_obj.wcs.ctype[0]
            if "SOLAR-X" in ct1.upper():
                ct1 = "HPLN-TAN"
            if "SOLAR-Y" in ct0.upper():
                ct0 = "HPLT-TAN"
            contour_wcs_obj.wcs.ctype = [ct0, ct1]
        return contour_wcs_obj, fits_flag

    def _swapped_image_wcs(self, image_wcs_obj):
        """Base-image WCS with axes swapped (and SOLAR-X/Y converted to degrees)
        for use as the reprojection target."""
        from astropy.wcs import WCS

        # IMPORTANT: Also swap the image WCS to match!
        # Create a copy with swapped axes for reprojection
        image_wcs_swapped = WCS(naxis=2)
        image_wcs_swapped.wcs.crpix = [
            image_wcs_obj.wcs.crpix[1],
            image_wcs_obj.wcs.crpix[0],
        ]

        # Check if base image uses SOLAR-X/Y (non-celestial in astropy)
        # If so, convert values from arcsec to degrees and remap CTYPE
        img_ctype0 = image_wcs_obj.wcs.ctype[0]
        img_ctype1 = image_wcs_obj.wcs.ctype[1]
        is_base_solar_xy = (
            "SOLAR-X" in img_ctype0.upper() or "SOLAR-Y" in img_ctype1.upper()
        )

        if is_base_solar_xy:
            # SOLAR-X/Y: values are in arcsec, convert to degrees
            image_wcs_swapped.wcs.crval = [
                image_wcs_obj.wcs.crval[1] / 3600.0,
                image_wcs_obj.wcs.crval[0] / 3600.0,
            ]
            image_wcs_swapped.wcs.cdelt = [
                image_wcs_obj.wcs.cdelt[1] / 3600.0,
                image_wcs_obj.wcs.cdelt[0] / 3600.0,
            ]
            image_wcs_swapped.wcs.ctype = ["HPLT-TAN", "HPLN-TAN"]
        else:
            image_wcs_swapped.wcs.crval = [
                image_wcs_obj.wcs.crval[1],
                image_wcs_obj.wcs.crval[0],
            ]
            image_wcs_swapped.wcs.cdelt = [
                image_wcs_obj.wcs.cdelt[1],
                image_wcs_obj.wcs.cdelt[0],
            ]
            image_wcs_swapped.wcs.ctype = [
                image_wcs_obj.wcs.ctype[1],
                image_wcs_obj.wcs.ctype[0],
            ]

        # Use the swapped WCS for reprojection
        image_wcs_for_reproject = image_wcs_swapped
        return image_wcs_for_reproject

    def _downsample_contour_for_display(self, contour_data, needs_reprojection):
        """Downsample large, non-reprojected contour data for plotting speed."""
        # Handle manual downsampling for "Same Image" or aligned images (no reprojection needed)
        # This ensures large images are still downsampled for performance
        if not needs_reprojection:
            self._contour_ds_factor = 1  # Default
            if self.contour_settings.get("downsample", True):
                max_dim = max(contour_data.shape)
                if max_dim > 2048:
                    factor = int(np.ceil(max_dim / 2048.0))
                    print(
                        f"[INFO] Downsampling aligned contour by {factor}x (original size: {contour_data.shape})"
                    )
                    # Simple slicing is sufficient for aligned data
                    contour_data = contour_data[::factor, ::factor]
                    self._contour_ds_factor = factor
        return contour_data

    def _reproject_contour_data(
        self,
        contour_data,
        contour_wcs_obj,
        image_wcs_for_reproject,
        fits_flag,
        is_same_image,
        main_window,
        needs_reprojection,
    ):
        """Reproject contour data onto the base image grid (if needed).

        Returns (proceed, contour_data); proceed is False if drawing should
        stop (user cancelled, or no overlap).
        """
        if needs_reprojection and contour_wcs_obj is not None:
            try:
                from reproject import reproject_interp

                if main_window:
                    self.show_status_message("Reprojecting.. Please wait...")

                # print("Reprojecting contour data to match base image WCS...")
                # print(f"  Contour data shape: {contour_data.shape}")
                # print(f"  Image data shape: {self.current_image_data.shape}")
                # print(f"  Contour WCS (swapped): crpix={contour_wcs_obj.wcs.crpix}, crval={contour_wcs_obj.wcs.crval}, cdelt={contour_wcs_obj.wcs.cdelt}, ctype={contour_wcs_obj.wcs.ctype}")
                # print(f"  Image WCS (swapped):   crpix={image_wcs_for_reproject.wcs.crpix}, crval={image_wcs_for_reproject.wcs.crval}, cdelt={image_wcs_for_reproject.wcs.cdelt}, ctype={image_wcs_for_reproject.wcs.ctype}")

                # Set the array shape in the WCS header for reproject
                # Note: contour_data shape is (height, width) but WCS uses (NAXIS1=width, NAXIS2=height)
                contour_wcs_obj.array_shape = contour_data.shape
                image_wcs_for_reproject.array_shape = self.current_image_data.shape

                canvas = self._extended_contour_canvas(
                    contour_data, contour_wcs_obj, image_wcs_for_reproject, main_window
                )
                if canvas is None:
                    return False, contour_data
                extended_shape, extended_wcs, contour_offset, show_full_extent = canvas

                # Store offset for contour drawing
                self._contour_offset = contour_offset

                array, ds_factor, extended_shape = self._reproject_with_cache(
                    contour_data,
                    contour_wcs_obj,
                    image_wcs_for_reproject,
                    extended_shape,
                    extended_wcs,
                    fits_flag,
                    is_same_image,
                    main_window,
                    show_full_extent,
                )

                # Replace the NaNs with zeros
                array = np.nan_to_num(array, nan=0.0)

                # Check if reprojection produced valid data
                if np.all(array == 0):
                    # Re-run with footprint to diagnose coverage
                    if main_window:
                        self.show_status_message(
                            "Reprojection yielded zeros, checking overlap..."
                        )

                    _, footprint = reproject_interp(
                        (contour_data, contour_wcs_obj),
                        extended_wcs,
                        shape_out=extended_shape,
                        return_footprint=True,
                    )
                    footprint_pct = np.sum(footprint > 0) / footprint.size * 100
                    if main_window:
                        self.show_status_message(
                            "WARNING: Reprojection produced all zeros ..."
                        )
                    print(
                        "[WARNING] Reprojection produced all zeros - checking footprint coverage"
                    )
                    print(f"  Footprint coverage: {footprint_pct:.1f}%")
                    if footprint_pct < 1.0:
                        # Show warning dialog about no overlap
                        QMessageBox.warning(
                            self,
                            "No Coordinate Overlap",
                            "Contour reprojection produced no valid data (0% footprint).\n\n"
                            "This typically happens when the base image and contour image "
                            "are from different observation times, causing them to point at "
                            "different sky coordinates.\n\n"
                            "Please ensure both images are from the same or similar observation time.",
                        )
                        return False, contour_data  # Don't try to draw empty contours
                else:

                    if main_window:
                        self.show_status_message(
                            f"Reprojection done.. plotting... Please wait..."
                        )

                contour_data = array

                # Store factor for extent calculation
                self._contour_ds_factor = ds_factor

            except ImportError as e:
                print(f"[ERROR] reproject library not available: {e}")
                # print("Install with: pip install reproject")
                QMessageBox.warning(
                    self,
                    "Reprojection Not Available",
                    "The 'reproject' library is required to align contours from images "
                    "with different pixel scales.\n\nInstall with: pip install reproject",
                )
            except Exception as e:
                print(f"[ERROR] Error reprojecting contour data: {e}")
                contour_data = self._fallback_reproject_contour(
                    contour_data, contour_wcs_obj, image_wcs_for_reproject
                )
        return True, contour_data

    def _extended_contour_canvas(
        self, contour_data, contour_wcs_obj, image_wcs_for_reproject, main_window
    ):
        """Output grid for reprojection: the base image extended to cover the
        contour image (capped at 1.5x unless show_full_extent).

        Returns (extended_shape, extended_wcs, contour_offset, show_full_extent),
        or None if the user cancelled a very large reprojection.
        """
        # Calculate extended or clipped bounds based on show_full_extent setting
        extended_shape = self.current_image_data.shape
        contour_offset = [0, 0]  # Offset for contour plotting (y, x)
        extended_wcs = image_wcs_for_reproject

        show_full_extent = self.contour_settings.get("show_full_extent", False)

        # Always try to extend canvas (default: 1.5x cap, full extent: no cap)
        try:
            # Get world coordinates of contour image corners
            contour_corners_pix = np.array(
                [
                    [0, 0],
                    [contour_data.shape[1] - 1, 0],
                    [0, contour_data.shape[0] - 1],
                    [contour_data.shape[1] - 1, contour_data.shape[0] - 1],
                ]
            )
            contour_corners_world = contour_wcs_obj.wcs_pix2world(
                contour_corners_pix, 0
            )
            image_corners_pix = image_wcs_for_reproject.wcs_world2pix(
                contour_corners_world, 0
            )

            # Calculate the extent of contour in base image coordinates
            min_x = min(np.min(image_corners_pix[:, 0]), 0)
            max_x = max(
                np.max(image_corners_pix[:, 0]),
                self.current_image_data.shape[1] - 1,
            )
            min_y = min(np.min(image_corners_pix[:, 1]), 0)
            max_y = max(
                np.max(image_corners_pix[:, 1]),
                self.current_image_data.shape[0] - 1,
            )

            # Calculate extended dimensions
            extended_width = int(np.ceil(max_x - min_x)) + 1
            extended_height = int(np.ceil(max_y - min_y)) + 1

            # Check for large reprojected matrix size when show_full_extent is enabled
            if show_full_extent:
                max_dim = max(extended_width, extended_height)
                if max_dim > 5000:
                    reply = QMessageBox.warning(
                        self,
                        "Large Reprojection Size",
                        f"The reprojected matrix size ({extended_width} x {extended_height}) "
                        f"exceeds 5000 pixels in at least one dimension.\n\n"
                        f"This may consume significant memory and take a long time to compute.\n\n"
                        f"Do you want to continue?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No,
                    )
                    if reply == QMessageBox.No:
                        self.show_status_message("Contour drawing cancelled by user.")
                        return None

            # Apply 1.5x cap by default (unless show_full_extent is enabled)
            if not show_full_extent:
                max_width = int(self.current_image_data.shape[1] * 1.5)
                max_height = int(self.current_image_data.shape[0] * 1.5)

                # Recalculate bounds if capped
                if extended_width > max_width:
                    center_x = self.current_image_data.shape[1] / 2
                    min_x = center_x - max_width / 2
                    max_x = center_x + max_width / 2 - 1
                    extended_width = max_width
                if extended_height > max_height:
                    center_y = self.current_image_data.shape[0] / 2
                    min_y = center_y - max_height / 2
                    max_y = center_y + max_height / 2 - 1
                    extended_height = max_height

            extended_shape = (extended_height, extended_width)

            # Calculate offset (how much the base image is shifted in the extended canvas)
            # Use floats to preserve sub-pixel accuracy
            contour_offset = [-min_y, -min_x]

            # Adjust WCS reference pixel for the extended shape
            extended_wcs = image_wcs_for_reproject.deepcopy()
            extended_wcs.wcs.crpix = [
                image_wcs_for_reproject.wcs.crpix[0] - min_x,
                image_wcs_for_reproject.wcs.crpix[1] - min_y,
            ]
            extended_wcs.array_shape = extended_shape

        except Exception as e:
            if main_window:
                self.show_status_message(f"Could not compute extended bounds: {e}")
            print(f"[ERROR] Could not compute extended bounds: {e}")
            extended_wcs = image_wcs_for_reproject
            extended_shape = self.current_image_data.shape
            contour_offset = [0, 0]
        return extended_shape, extended_wcs, contour_offset, show_full_extent

    def _reproject_with_cache(
        self,
        contour_data,
        contour_wcs_obj,
        image_wcs_for_reproject,
        extended_shape,
        extended_wcs,
        fits_flag,
        is_same_image,
        main_window,
        show_full_extent,
    ):
        """Reproject onto the extended grid, using self._reproject_cache.

        Returns (array, ds_factor, extended_shape); large outputs are
        downsampled by ds_factor.
        """
        from reproject import reproject_interp

        # Check cache for reprojected data
        original_contour_source = (
            self.imagename
            if is_same_image
            else self.contour_settings.get("external_image")
        )

        # Create a comprehensive cache key
        cache_key = (
            self.imagename,  # Base image
            original_contour_source,  # Contour image source
            self.contour_settings.get("stokes", ""),  # Stokes parameter (crucial!)
            contour_data.shape,  # Original contour shape
            "fits_header" if fits_flag else "casa_csys",  # Source type
            str(contour_wcs_obj.wcs.crval),  # Contour WCS params
            str(contour_wcs_obj.wcs.cdelt),
            str(
                image_wcs_for_reproject.wcs.crval
            ),  # Base image WCS params (includes downsampling state)
            str(image_wcs_for_reproject.wcs.cdelt),
            show_full_extent,  # Extent setting
            self.contour_settings.get("downsample", True),  # Downsample setting
        )

        cached_result = self._reproject_cache.get(cache_key)

        if cached_result is not None:
            # cache hit
            # print("Using cached reprojected contour data")
            if main_window:
                self.show_status_message("Using cached contour alignment...")
            array, offset, ds_factor = cached_result
            self._contour_offset = offset

            # Apply downsampling factor to offset if needed for plotting calculations
            # Note: The array itself is already downsampled, so we just use it.
            # The offset is in base image pixels, which is correct for placement.
        else:
            # Cache miss - perform reprojection

            # OPTIMIZATION: Downsample large arrays to save memory/time
            # Contours don't need pixel-perfect 4k resolution
            contour_ds_factor = 1
            max_dim = max(extended_shape)
            downsample_enabled = self.contour_settings.get("downsample", True)

            if downsample_enabled and max_dim > 2048:
                contour_ds_factor = int(np.ceil(max_dim / 2048.0))
                print(
                    f"[INFO] Downsampling contour reprojection by {contour_ds_factor}x (original size: {extended_shape})"
                )

                # Adjust shape
                new_height = extended_shape[0] // contour_ds_factor
                new_width = extended_shape[1] // contour_ds_factor
                extended_shape = (new_height, new_width)

                # Adjust WCS
                extended_wcs.wcs.cdelt *= contour_ds_factor
                extended_wcs.wcs.crpix /= contour_ds_factor

            # Reproject the contour data to the extended/clipped WCS
            # OPTIMIZATION: Don't calculate footprint unless needed (2x speedup)
            array = reproject_interp(
                (contour_data, contour_wcs_obj),
                extended_wcs,
                shape_out=extended_shape,
                return_footprint=False,
            )

            # Store in cache
            self._reproject_cache[cache_key] = (
                array,
                self._contour_offset,
                contour_ds_factor,
            )
        if cached_result is not None:
            return array, ds_factor, extended_shape
        return array, contour_ds_factor, extended_shape

    def _fallback_reproject_contour(
        self, contour_data, contour_wcs_obj, image_wcs_for_reproject
    ):
        """Simpler reprojection onto the base image grid after the main path failed."""
        import traceback

        traceback.print_exc()

        # Fallback: try scipy-based scaling using pixel scale ratio
        # This is useful when WCS reprojection fails (e.g., HPC observer=None)
        try:
            from scipy.ndimage import zoom

            self.show_status_message(
                "WCS reprojection failed, trying pixel-scale fallback..."
            )

            # Calculate scale factors from CDELT
            scale_x = (
                contour_wcs_obj.wcs.cdelt[0] / image_wcs_for_reproject.wcs.cdelt[0]
            )
            scale_y = (
                contour_wcs_obj.wcs.cdelt[1] / image_wcs_for_reproject.wcs.cdelt[1]
            )

            # Zoom to match pixel scales
            zoomed = zoom(
                contour_data,
                [abs(scale_y), abs(scale_x)],
                order=1,
                mode="constant",
                cval=0,
            )

            # Pad or crop to match target shape
            target_shape = self.current_image_data.shape
            result = np.zeros(target_shape)

            # Calculate the offset to center the contour
            # Use reference value difference to determine shift
            dcrval = contour_wcs_obj.wcs.crval - image_wcs_for_reproject.wcs.crval
            offset_x = int(dcrval[0] / image_wcs_for_reproject.wcs.cdelt[0])
            offset_y = int(dcrval[1] / image_wcs_for_reproject.wcs.cdelt[1])

            # Calculate copy ranges
            src_start_y = max(0, -offset_y)
            src_end_y = min(zoomed.shape[0], target_shape[0] - offset_y)
            src_start_x = max(0, -offset_x)
            src_end_x = min(zoomed.shape[1], target_shape[1] - offset_x)

            dst_start_y = max(0, offset_y)
            dst_end_y = dst_start_y + (src_end_y - src_start_y)
            dst_start_x = max(0, offset_x)
            dst_end_x = dst_start_x + (src_end_x - src_start_x)

            if src_end_y > src_start_y and src_end_x > src_start_x:
                result[dst_start_y:dst_end_y, dst_start_x:dst_end_x] = zoomed[
                    src_start_y:src_end_y, src_start_x:src_end_x
                ]

            contour_data = result
            self.show_status_message("Fallback reprojection done")
            print("[INFO] Fallback pixel-scale reprojection successful")
        except Exception as fallback_e:
            print(f"[ERROR] Fallback reprojection also failed: {fallback_e}")
        return contour_data

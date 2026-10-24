"""
Contour overlays: settings, loading contour data, drawing.

A tab draws up to ten contour layers, each from the current image or
another file with its own Stokes, levels and style, plus a legend.

Contours from another image are traced on that image's own pixel grid and
the lines are mapped onto the base image through both images' WCS.
"""

import logging
import os

import numpy as np
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialogButtonBox, QMessageBox

from ..processing.dependencies import IA
from ..processing.image_data import get_pixel_values_from_image
from ..processing.fits_io import is_fits, readable_image_path
from ..processing.contour_mapping import (
    PixelToPixel,
    check_mapping,
    contour_artists,
    pixel_mapping,
    same_pixel_grid,
    transformable_wcs,
    view_kept,
)
from ..processing.contour_layers import (
    contour_layer_name,
    contour_levels,
    legend_handles,
    merge_contour_layers,
)
from ..processing.image_meta import casa_info, fits_header

logger = logging.getLogger(__name__)


class ContoursMixin:
    """Contour settings dialog, contour data loading and drawing.

    A tab draws up to ``MAX_CONTOUR_LAYERS`` contour layers
    (``contour_layers``, see :func:`new_contour_layer`), with options shared
    by all of them in ``contour_options``.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``_build_display_wcs``, ``_cached_csys``, ``_cached_fits_flag``,
    ``_cached_fits_header``, ``_cached_summary``, ``_cached_wcs_obj``,
    ``contour_layers``, ``_contour_conversions``, ``contour_options``,
    ``current_image_data``, ``_get_coordinate_system_from_image``,
    ``imagename``, ``_open_dialogs``, ``parent``, ``show_contours_checkbox``,
    ``show_status_message``, ``_temp_file_id``, ``window``
    """

    def invalidate_contours(self):
        """Forget all layers' loaded contour data (e.g. the base image changed),
        so they are loaded again when next drawn."""
        for layer in self.contour_layers:
            layer["contour_data"] = None
            layer["contour_wcs"] = None
            layer["loaded_path"] = None

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

        dialog = ContourSettingsDialog(self, self.contour_layers, self.contour_options)
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

                viewer.contour_layers = merge_contour_layers(
                    viewer.contour_layers, dialog.get_layers()
                )
                viewer.contour_options = dialog.get_options()
                if viewer.show_contours_checkbox.isChecked():
                    # Layers whose source settings changed have no data and are
                    # loaded while drawing; the others keep theirs
                    viewer.on_visualization_changed(reload_data=False)

                    # Update dialog with captured dimensions
                    if hasattr(dialog, "update_layer_dimensions") and not sip.isdeleted(
                        dialog
                    ):
                        dialog.update_layer_dimensions(viewer.contour_layers)

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
            logger.error(f"Could not get image dimensions for {imagepath}: {e}")

        return height, width

    def load_contour_data(self, layer=None):
        """Load the contour data of ``layer``, or of every enabled layer."""
        if layer is None:
            for each in self.contour_layers:
                if each.get("enabled", True):
                    self.load_contour_data(each)
            return
        try:
            if layer["source"] == "external":
                h, w = self._get_image_dimensions(layer["external_image"])
            else:
                h, w = self._get_image_dimensions(self.imagename)

            if h > 0 and w > 0:
                layer["dim_h"] = h
                layer["dim_w"] = w
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

            if not layer.get("use_default_rms_region", True):
                rms_box = layer.get("rms_box", rms_box)
            else:
                layer["rms_box"] = rms_box

            self._validate_contour_stokes(layer)

            layer["contour_data"] = None
            layer["contour_wcs"] = None
            layer["loaded_path"] = None
            if layer["source"] == "same":
                image_to_load = self.imagename or None
            else:
                external_image = layer["external_image"]
                if external_image and os.path.exists(external_image):
                    image_to_load = self._resolve_contour_image(external_image)
                else:
                    print(
                        "[ERROR] load_contour_data: external image not found or empty"
                    )
                    image_to_load = None

            if image_to_load:
                contour_data, contour_csys = self._load_contour_stokes(
                    image_to_load,
                    layer["stokes"],
                    layer.get("threshold", 5.0),
                    rms_box,
                    target_size,
                )
                layer["contour_data"] = contour_data
                layer["contour_wcs"] = contour_csys
                layer["loaded_path"] = image_to_load

            if self.window():
                if layer["contour_data"] is not None:
                    self.show_status_message(
                        f"Contour data loaded: {contour_layer_name(layer)}"
                    )
                else:
                    self.show_status_message(
                        f"Failed to load contour data: {contour_layer_name(layer)}"
                    )
        except Exception as e:
            logger.error(f"Error loading contour data: {e}")
            self.show_status_message(
                f"Error loading contour data ({contour_layer_name(layer)}): {e}"
            )
            layer["contour_data"] = None

    def _validate_contour_stokes(self, layer):
        """Fall back to an available Stokes if the requested one isn't in the source image."""
        # Validate contour Stokes before loading
        contour_stokes = layer.get("stokes", "I")
        source_image = None

        if layer["source"] == "same":
            source_image = self.imagename
        else:
            source_image = layer.get("external_image")

        if source_image:
            from ..processing.stokes import get_available_stokes, missing_planes

            available_stokes = get_available_stokes(source_image)

            missing_stokes = missing_planes(contour_stokes, available_stokes)
            if missing_stokes:
                # Show warning dialog and switch to Stokes I
                QMessageBox.warning(
                    self,
                    "Contour Stokes Unavailable",
                    f"The contour Stokes parameter '{contour_stokes}' of "
                    f"'{contour_layer_name(layer)}' requires "
                    f"polarization data ({', '.join(missing_stokes)}) that is not available "
                    f"in the source image.\n\n"
                    f"Available Stokes: {', '.join(available_stokes)}\n\n"
                    f"Switching contour to Stokes I.",
                )
                layer["stokes"] = "I"
                contour_stokes = "I"

    def _resolve_contour_image(self, external_image):
        """Path of the contour image to load, converting its coordinate system
        (RA/Dec <-> helioprojective) to match the base image if needed.

        Conversions are kept per source file (all Stokes are converted at
        once), so layers from the same file share one.
        """
        # Detect coordinate systems of base image and contour image
        base_csys_type = self._get_coordinate_system_from_image(self.imagename)
        contour_csys_type = self._get_coordinate_system_from_image(external_image)
        if (
            base_csys_type == "unknown"
            or contour_csys_type == "unknown"
            or base_csys_type == contour_csys_type
        ):
            return external_image

        cached = self._contour_conversions.get(external_image)
        if cached and os.path.exists(cached):
            if self._get_coordinate_system_from_image(cached) == base_csys_type:
                self.show_status_message(
                    f"Using cached transformed contour ({contour_csys_type}→{base_csys_type})"
                )
                return cached
            # Converted for a base image in another coordinate system
            try:
                os.remove(cached)
            except Exception:
                pass
        self._contour_conversions.pop(external_image, None)

        # Create temp file for coordinate-transformed contour
        import hashlib
        import tempfile

        source_id = hashlib.sha1(os.path.abspath(external_image).encode()).hexdigest()[:8]
        transformed_path = os.path.join(
            tempfile.gettempdir(),
            f"sv_contour_transformed_{self._temp_file_id}_{source_id}.fits",
        )

        transform_success = False
        if base_csys_type == "hpc" and contour_csys_type == "radec":
            # Convert RA/Dec contour to HPC (all Stokes for full flexibility)
            self.show_status_message("Transforming contour from RA/Dec to HPC...")
            from ..coordinates.helioprojective import convert_and_save_hpc_all_stokes

            transform_success = convert_and_save_hpc_all_stokes(
                external_image, transformed_path, overwrite=True
            )
        elif base_csys_type == "radec" and contour_csys_type == "hpc":
            # Convert HPC contour to RA/Dec
            self.show_status_message("Transforming contour from HPC to RA/Dec...")
            from ..coordinates.helioprojective import convert_hpc_to_radec

            transform_success = convert_hpc_to_radec(
                external_image, transformed_path, overwrite=True
            )

        if transform_success and os.path.exists(transformed_path):
            self._contour_conversions[external_image] = transformed_path
            self.show_status_message(
                f"Contour transformed ({contour_csys_type}→{base_csys_type})"
            )
            return transformed_path
        logger.error(f"Transformation failed, using original: {external_image}")
        self.show_status_message(
            "Coordinate transformation failed, using original contour"
        )
        return external_image

    def remove_contour_conversions(self):
        """Delete the temporary converted contour images."""
        for path in self._contour_conversions.values():
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass
        self._contour_conversions.clear()

    def _load_contour_stokes(self, image_path, stokes, threshold, rms_box, target_size):
        """Contour data for a Stokes product (I/Q/U/V, X/I, L, Lfrac, PANG):
        returns (data, the image's coordsys)."""
        pix, contour_csys, _ = get_pixel_values_from_image(
            image_path, stokes, threshold, rms_box, target_size=target_size
        )
        return pix, contour_csys

    def draw_contours(self, ax):
        """Draw every enabled contour layer, and their legend."""
        options = self.contour_options
        layers = [layer for layer in self.contour_layers if layer.get("enabled", True)]

        handles = []
        for layer in layers:
            drawn = self._draw_contour_layer(ax, layer)
            if not options.get("legend_show_negative", False):
                drawn = [sign for sign in drawn if sign == "pos"]
            handles.extend(legend_handles(layer, drawn))

        if handles and options.get("show_legend", True):
            legend = ax.legend(
                handles=handles,
                loc=options.get("legend_loc", "upper right"),
                fontsize=options.get("legend_fontsize", 8),
                framealpha=0.6,
            )
            legend.set_zorder(10)

    def _draw_contour_layer(self, ax, layer):
        """Draw one layer; returns the signs ("pos"/"neg") it drew."""
        main_window = self.window()
        name = contour_layer_name(layer)
        if layer.get("contour_data") is None:
            self.load_contour_data(layer)

        if layer.get("contour_data") is None:
            logger.error(f"draw_contours: no contour data for {name}")
            return []

        if layer.get("contour_wcs") is None:
            logger.error(f"draw_contours: no coordinate system for {name}")
            return []

        # OPTIMIZATION: Fast path for same-image contours
        # When contour source is the current image, skip expensive metadata loading
        # since the data is already aligned
        is_same_image = layer["source"] == "same"

        metadata = self._contour_source_metadata(layer, is_same_image)
        if metadata is None:
            return []
        fits_flag, header, csys, summary = metadata

        try:
            self.show_status_message(f"Drawing contours ({name}).... Please wait...")
            image_wcs_obj = getattr(self, "_cached_wcs_obj", None)

            contour_data = layer["contour_data"]
            abs_max = np.nanmax(np.abs(contour_data))

            pos_levels, neg_levels = self._compute_contour_levels(
                layer, contour_data, abs_max
            )

            transform = None
            if not is_same_image and image_wcs_obj is not None:
                transform = self._contour_transform(
                    layer, contour_data, fits_flag, header, csys, summary, image_wcs_obj
                )
                if transform is False:
                    return []

            if transform is None:
                drawn = self._draw_aligned_contours(
                    ax, layer, contour_data, pos_levels, neg_levels
                )
            else:
                drawn = self._draw_transformed_contours(
                    ax, layer, contour_data, transform, pos_levels, neg_levels
                )

            if main_window:

                self.show_status_message("Done. ")
            return drawn

        except Exception as e:
            logger.error(f"Error drawing contours ({name}): {e}")
            import traceback

            traceback.print_exc()
            if self.window():
                self.show_status_message(f"Error drawing contours ({name}): {str(e)}")
            return []

    def _draw_aligned_contours(self, ax, layer, contour_data, pos_levels, neg_levels):
        """Draw contours whose pixel grid is the base image's.

        Returns the signs ("pos"/"neg") that were drawn."""
        contour_data = self._downsample_contour_for_display(contour_data)
        display_contour_data = contour_data.transpose()
        # Pixel centres in base-image pixels (the image's pixel i is centred on i)
        ds_factor = self._contour_ds_factor
        rows = np.arange(display_contour_data.shape[0]) * ds_factor
        cols = np.arange(display_contour_data.shape[1]) * ds_factor
        args = (cols, rows, display_contour_data)
        drawn = []
        for levels, sign, label in (
            (pos_levels, "pos", "positive"),
            (neg_levels, "neg", "negative"),
        ):
            if self._draw_contour_set(ax, layer, args, {}, levels, sign, label):
                drawn.append(sign)
        return drawn

    def _draw_transformed_contours(
        self, ax, layer, contour_data, transform, pos_levels, neg_levels
    ):
        """Trace contours on the contour image's own pixel grid and map the
        lines onto the base image with ``transform``.

        Returns the signs ("pos"/"neg") that were drawn."""
        display_contour_data = contour_data.transpose()
        step = 1
        if self.contour_options.get("downsample", True):
            step = max(1, int(np.ceil(max(display_contour_data.shape) / 2048.0)))
        rows = np.arange(0, display_contour_data.shape[0], step)
        cols = np.arange(0, display_contour_data.shape[1], step)
        args = (cols, rows, display_contour_data[::step, ::step])
        kwargs = {"transform": transform + ax.transData}

        clip = None
        if not self.contour_options.get("show_full_extent", True):
            # Only within the base image extended by 25% on each side. A clip
            # box, so the usual clipping to the axes still applies too
            from matplotlib.transforms import Bbox, TransformedBbox

            width, height = self.current_image_data.shape
            clip = TransformedBbox(
                Bbox(
                    [
                        [-0.25 * width - 0.5, -0.25 * height - 0.5],
                        [1.25 * width - 0.5, 1.25 * height - 0.5],
                    ]
                ),
                ax.transData,
            )

        vertices = []
        drawn = []
        for levels, sign, label in (
            (pos_levels, "pos", "positive"),
            (neg_levels, "neg", "negative"),
        ):
            cs = self._draw_contour_set(ax, layer, args, kwargs, levels, sign, label)
            if cs is not None:
                drawn.append(sign)
                for artist in contour_artists(cs):
                    if clip is not None:
                        artist.set_clip_box(clip)
                    vertices.extend(p.vertices for p in artist.get_paths())

        vertices = np.concatenate(vertices) if vertices else np.empty((0, 2))
        width, height = self.current_image_data.shape
        inside = (
            np.isfinite(vertices).all(axis=1)
            & (vertices[:, 0] >= -0.5)
            & (vertices[:, 0] <= width - 0.5)
            & (vertices[:, 1] >= -0.5)
            & (vertices[:, 1] <= height - 0.5)
        )
        if len(vertices) and not inside.any():
            QMessageBox.warning(
                self,
                "No Coordinate Overlap",
                f"None of the contours of '{contour_layer_name(layer)}' fall on "
                "the base image.\n\n"
                "This typically happens when the base image and contour image "
                "are from different observation times, causing them to point at "
                "different sky coordinates.\n\n"
                "Please ensure both images are from the same or similar observation time.",
            )
        return drawn

    def _draw_contour_set(self, ax, layer, args, kwargs, levels, sign, label):
        """Draw one set of contour levels; sign is "pos" or "neg".

        ``args``/``kwargs`` give the data (and coordinates/transform) for
        ``ax.contour``. Returns the ContourSet, or None.
        """
        if not levels or len(levels) == 0:
            return None
        style = dict(
            levels=levels,
            colors=layer.get(f"{sign}_color", "white"),
            linewidths=layer.get(f"{sign}_linewidth", 1.0),
            linestyles=layer.get(f"{sign}_linestyle", "-" if sign == "pos" else "--"),
        )
        try:
            # Contours never change which part of the image is shown (and the
            # view must be right at once: status messages let queued redraws
            # run in the middle of drawing)
            with view_kept(ax):
                cs = ax.contour(*args, **kwargs, **style)

            # Add contour labels if enabled
            if layer.get("show_labels", False):
                ax.clabel(cs, inline=True, fontsize=8, fmt="%.2g")
            return cs

        except Exception as e:
            logger.error(f"Error drawing {label} contours: {e}, levels: {levels}")
            self.show_status_message(
                f"Error drawing {label} contours: {e}, levels: {levels}"
            )
            return None

    def _contour_source_metadata(self, layer, is_same_image):
        """FITS header / CASA coordsys of the layer's contour source image.

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
        else:
            # External image - need to load metadata
            # (the coordinate-transformed copy if one was loaded)
            contour_imagename = layer.get("loaded_path") or layer["external_image"]

            if is_fits(contour_imagename):
                fits_flag = True
                try:
                    header = dict(fits_header(contour_imagename))
                except Exception as e:
                    logger.error(f"Error getting contour FITS header: {e}")
                    header = {}

            try:
                info = casa_info(contour_imagename)
                csys = info.coordsys()
                summary = info.summary
            except Exception as e:
                logger.error(f"Error getting metadata: {e}")
                self.show_status_message(f"Error getting metadata: {e}")
                return None
        return fits_flag, header, csys, summary

    def _compute_contour_levels(self, layer, contour_data, abs_max):
        """Positive/negative contour levels for the layer's level_type."""
        return contour_levels(layer, contour_data, abs_max)

    def _contour_transform(
        self, layer, contour_data, fits_flag, header, csys, summary, image_wcs_obj
    ):
        """Mapping from contour-image pixels to base-image pixels.

        None if the two share a pixel grid (contours can be drawn as they are),
        False if the contour image's coordinates can't be mapped onto the base
        image (the reason has been shown), else a matplotlib Transform.
        """
        try:
            contour_wcs = self._build_display_wcs(
                layer["contour_wcs"], fits_flag, header, csys, summary
            )
        except Exception as e:
            contour_wcs = None
            logger.error(f"Could not read the contour image's coordinates: {e}")
        if contour_wcs is None:
            self.show_status_message(
                f"Contours not drawn ({contour_layer_name(layer)}): "
                "could not read the contour image's coordinates"
            )
            return False

        if same_pixel_grid(contour_wcs, image_wcs_obj) and (
            contour_data.shape == self.current_image_data.shape
        ):
            return None

        base_header = getattr(self, "_cached_fits_header", None)
        mapping = pixel_mapping(
            transformable_wcs(contour_wcs, header),
            transformable_wcs(image_wcs_obj, base_header),
        )
        try:
            check_mapping(mapping, contour_data.shape)
        except Exception as e:
            # Without observation time and observer for both images: treat the
            # two coordinate frames as the same
            mapping = pixel_mapping(
                transformable_wcs(contour_wcs, None),
                transformable_wcs(image_wcs_obj, None),
            )
            try:
                check_mapping(mapping, contour_data.shape)
            except Exception as e2:
                logger.error(f"Cannot map contour coordinates onto the image: {e2}")
                self.show_status_message(
                    f"Contours not drawn ({contour_layer_name(layer)}): cannot map "
                    f"their coordinates onto the image ({e2})"
                )
                return False
            # Drawn, but not where they would be with both observers known
            logger.warning(f"Contours mapped without observer information: {e}")
            self.show_status_message(
                f"Contours of {contour_layer_name(layer)} placed without the "
                "observation times and observer positions, which could not be "
                "used: they may be offset"
            )
        return PixelToPixel(mapping)

    def _downsample_contour_for_display(self, contour_data):
        """Downsample large contour data that is aligned with the base image."""
        # Handle manual downsampling for "Same Image" or aligned images
        # This ensures large images are still downsampled for performance
        self._contour_ds_factor = 1  # Default
        if self.contour_options.get("downsample", True):
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

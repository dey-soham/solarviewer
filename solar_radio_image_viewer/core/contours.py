"""
Contour overlays: settings, loading contour data, drawing.

A tab draws up to ten contour layers, each from the current image or
another file with its own Stokes, levels and style, plus a legend.

Contours from another image are traced on that image's own pixel grid and
the lines are mapped onto the base image through both images' WCS.
"""

import os
import uuid

import numpy as np
from matplotlib.transforms import Transform
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialogButtonBox, QMessageBox

from ..processing.dependencies import IA
from ..processing.image_data import get_pixel_values_from_image
from ..processing.fits_io import readable_image_path
from ..processing.image_meta import casa_info, fits_header


MAX_CONTOUR_LAYERS = 10

# Default colours of new layers, in order: the Okabe-Ito palette, which stays
# distinguishable with the common kinds of colour blindness, plus white, grey
# and black; brightest first for dark solar images
CONTOUR_LAYER_COLORS = [
    "white",
    "#E69F00",
    "#56B4E9",
    "#F0E442",
    "#D55E00",
    "#009E73",
    "#CC79A7",
    "#0072B2",
    "#999999",
    "black",
]
CONTOUR_COLOR_NAMES = {
    "white": "White",
    "#E69F00": "Orange",
    "#56B4E9": "Sky blue",
    "#F0E442": "Yellow",
    "#D55E00": "Vermillion",
    "#009E73": "Bluish green",
    "#CC79A7": "Reddish purple",
    "#0072B2": "Blue",
    "#999999": "Grey",
    "black": "Black",
}

# Keys of a layer that hold loaded data rather than settings
_LAYER_RUNTIME_KEYS = ("contour_data", "contour_wcs", "loaded_path", "dim_w", "dim_h")

# Settings whose change means a layer's data must be loaded again
_LAYER_LOAD_KEYS = (
    "source",
    "external_image",
    "stokes",
    "threshold",
    "use_default_rms_region",
    "rms_box",
)


def new_contour_layer(index=0, **settings):
    """A contour layer: one contour set (source, Stokes, levels, style).

    ``index`` picks its default colour. Runtime keys (``contour_data``,
    ``contour_wcs``, ...) hold the loaded data and are filled in by
    :meth:`ContoursMixin.load_contour_data`.
    """
    color = CONTOUR_LAYER_COLORS[index % len(CONTOUR_LAYER_COLORS)]
    layer = {
        "id": uuid.uuid4().hex,
        "enabled": True,
        "label": "",  # empty: named after its source and Stokes
        "source": "same",
        "external_image": "",
        "stokes": "I",
        "threshold": 5.0,
        "pos_levels": [0.1, 0.3, 0.5, 0.7, 0.9],
        "neg_levels": [0.1, 0.3, 0.5, 0.7, 0.9],
        "level_type": "fraction",
        "pos_color": color,
        "neg_color": color,
        "pos_linewidth": 1.0,
        "neg_linewidth": 1.0,
        "pos_linestyle": "-",
        "neg_linestyle": "--",
        "show_labels": False,
        "use_default_rms_region": True,
        "rms_box": (0, 200, 0, 130),
        "contour_data": None,
        "contour_wcs": None,
        "loaded_path": None,
        "dim_h": 0,
        "dim_w": 0,
    }
    layer.update(settings)
    return layer


def default_contour_options():
    """Settings shared by all contour layers."""
    return {
        "show_legend": True,
        "legend_loc": "upper right",
        "legend_fontsize": 8,
        "legend_show_negative": False,  # entries for negative contours too
        "show_full_extent": True,  # If False, only contours within 1.5x the base image
        "downsample": True,
    }


def contour_layer_name(layer):
    """The layer's label, or one made from its source and Stokes."""
    if layer.get("label"):
        return layer["label"]
    if layer.get("source") == "external" and layer.get("external_image"):
        source = _short_source_name(layer["external_image"])
    else:
        source = "Current image"
    return f"{source} · {layer.get('stokes', 'I')}"


def _short_source_name(path):
    """The telescope that took an image (from its header), else its file name."""
    try:
        if os.path.isdir(path):
            telescope = casa_info(path).csys_record.get("telescope", "")
        else:
            telescope = fits_header(path).get("TELESCOP", "")
        telescope = str(telescope).strip()
        if telescope and telescope.upper() not in ("UNKNOWN", "NONE"):
            return telescope
    except Exception:
        pass
    name = os.path.basename(os.path.normpath(path))
    for suffix in (".fits", ".fts", ".fit", ".image"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name if len(name) <= 24 else name[:23] + "…"


def contour_layer_settings(layer):
    """Copy of a layer without its loaded data."""
    return {k: v for k, v in layer.items() if k not in _LAYER_RUNTIME_KEYS}


def merge_contour_layers(old_layers, new_layers):
    """``new_layers`` (settings from the dialog), keeping the loaded data of
    layers whose source settings did not change."""
    old_by_id = {layer.get("id"): layer for layer in old_layers}
    def same_source(old, new):
        keys = _LAYER_LOAD_KEYS
        if old.get("use_default_rms_region", True) and new.get(
            "use_default_rms_region", True
        ):
            # The box is then worked out when loading, not set by the user
            keys = [k for k in keys if k != "rms_box"]
        return all(old.get(k) == new.get(k) for k in keys)

    merged = []
    for layer in new_layers:
        layer = dict(layer)
        old = old_by_id.get(layer.get("id"))
        if old is not None and same_source(old, layer):
            for key in _LAYER_RUNTIME_KEYS:
                layer[key] = old.get(key)
        else:
            layer.update({k: None for k in ("contour_data", "contour_wcs", "loaded_path")})
            layer.setdefault("dim_h", 0)
            layer.setdefault("dim_w", 0)
        merged.append(layer)
    return merged


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
            print(f"[ERROR] Could not get image dimensions for {imagepath}: {e}")

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

            main_window = self.parent()
            if main_window:
                if layer["contour_data"] is not None:
                    self.show_status_message(
                        f"Contour data loaded: {contour_layer_name(layer)}"
                    )
                else:
                    self.show_status_message(
                        f"Failed to load contour data: {contour_layer_name(layer)}"
                    )
        except Exception as e:
            print(f"[ERROR] Error loading contour data: {e}")
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
                    f"The contour Stokes parameter '{contour_stokes}' of "
                    f"'{contour_layer_name(layer)}' requires "
                    f"polarization data ({', '.join(set(missing_stokes))}) that is not available "
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
        print(f"[ERROR] Transformation failed, using original: {external_image}")
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
        if stokes in ["I", "Q", "U", "V"]:
            pix, contour_csys, _ = get_pixel_values_from_image(
                image_path,
                stokes,
                threshold,
                rms_box,
                target_size=target_size,
            )
            contour_data = pix

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
            contour_data = ratio
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
            contour_data = l_pix
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
            contour_data = lfrac
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
            contour_data = pang
        else:
            pix, contour_csys, _ = get_pixel_values_from_image(
                image_path,
                "I",
                threshold,
                rms_box,
                target_size=target_size,
            )
            contour_data = pix
        return contour_data, contour_csys

    def draw_contours(self, ax):
        """Draw every enabled contour layer, and their legend."""
        options = self.contour_options
        layers = [layer for layer in self.contour_layers if layer.get("enabled", True)]

        # Contours never change which part of the image is shown. Matplotlib
        # autoscales to each contour set, and one from another image is traced
        # in that image's pixels before being mapped onto this one
        xlim, ylim = ax.get_xlim(), ax.get_ylim()
        data_lim = ax.dataLim.frozen()
        autoscale = ax.get_autoscalex_on(), ax.get_autoscaley_on()

        handles = []
        for layer in layers:
            drawn = self._draw_contour_layer(ax, layer)
            if not options.get("legend_show_negative", False):
                drawn = [sign for sign in drawn if sign == "pos"]
            handles.extend(_legend_handles(layer, drawn))

        ax.dataLim.set(data_lim)
        ax.set_xlim(xlim)
        ax.set_ylim(ylim)
        ax.set_autoscalex_on(autoscale[0])
        ax.set_autoscaley_on(autoscale[1])

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
            print(f"[ERROR] draw_contours: no contour data for {name}")
            return []

        if layer.get("contour_wcs") is None:
            print(f"[ERROR] draw_contours: no coordinate system for {name}")
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
            print(f"[ERROR] Error drawing contours ({name}): {e}")
            import traceback

            traceback.print_exc()
            main_window = self.parent()
            if main_window:
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
                for artist in _contour_artists(cs):
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
            cs = ax.contour(*args, **kwargs, **style)

            # Add contour labels if enabled
            if layer.get("show_labels", False):
                ax.clabel(cs, inline=True, fontsize=8, fmt="%.2g")
            return cs

        except Exception as e:
            print(f"[ERROR] Error drawing {label} contours: {e}, levels: {levels}")
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

            if contour_imagename.endswith(".fits") or contour_imagename.endswith(
                ".fts"
            ):
                fits_flag = True
                try:
                    header = dict(fits_header(contour_imagename))
                except Exception as e:
                    print(f"[ERROR] Error getting contour FITS header: {e}")
                    header = {}

            try:
                info = casa_info(contour_imagename)
                csys = info.coordsys()
                summary = info.summary
            except Exception as e:
                print(f"[ERROR] Error getting metadata: {e}")
                self.show_status_message(f"Error getting metadata: {e}")
                return None
        return fits_flag, header, csys, summary

    def _compute_contour_levels(self, layer, contour_data, abs_max):
        """Positive/negative contour levels for the layer's level_type."""
        if layer["level_type"] == "fraction":
            vmax = np.nanmax(contour_data)
            vmin = np.nanmin(contour_data)
            if vmax > 0:
                pos_levels = sorted([level * abs_max for level in layer["pos_levels"]])
            else:
                pos_levels = []

            if vmin < 0:
                neg_levels = sorted([level * abs_max for level in layer["neg_levels"]])
                neg_levels = [-level for level in reversed(neg_levels)]
            else:
                neg_levels = []

        elif layer["level_type"] == "sigma":
            # For sigma levels, calculate RMS from the RMS box region of the contour data
            # This gives a proper noise estimate without source contamination
            rms_box = layer.get("rms_box", (0, 200, 0, 130))

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
            pos_levels = sorted([level * rms for level in layer["pos_levels"]])
            # Negative levels: -level * rms (e.g., -3σ, -6σ, -9σ...)
            # Must be in increasing order for matplotlib (-30, -20, -10)
            neg_levels = sorted([-level * rms for level in layer["neg_levels"]])

        else:
            pos_levels = sorted(layer["pos_levels"])
            neg_levels = sorted([-level for level in reversed(layer["neg_levels"])])
        return pos_levels, neg_levels

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
            print(f"[ERROR] Could not read the contour image's coordinates: {e}")
        if contour_wcs is None:
            self.show_status_message(
                f"Contours not drawn ({contour_layer_name(layer)}): "
                "could not read the contour image's coordinates"
            )
            return False

        if _same_pixel_grid(contour_wcs, image_wcs_obj) and (
            contour_data.shape == self.current_image_data.shape
        ):
            return None

        base_header = getattr(self, "_cached_fits_header", None)
        mapping = _pixel_mapping(
            _transformable_wcs(contour_wcs, header),
            _transformable_wcs(image_wcs_obj, base_header),
        )
        try:
            _check_mapping(mapping, contour_data.shape)
        except Exception as e:
            # Without observation time and observer for both images: treat the
            # two coordinate frames as the same
            print(f"[INFO] Mapping contours without observer information: {e}")
            mapping = _pixel_mapping(
                _transformable_wcs(contour_wcs, None),
                _transformable_wcs(image_wcs_obj, None),
            )
            try:
                _check_mapping(mapping, contour_data.shape)
            except Exception as e2:
                print(f"[ERROR] Cannot map contour coordinates onto the image: {e2}")
                self.show_status_message(
                    f"Contours not drawn ({contour_layer_name(layer)}): cannot map "
                    f"their coordinates onto the image ({e2})"
                )
                return False
        return _PixelToPixel(mapping)

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


def _legend_handles(layer, drawn):
    """Legend entries for a layer's drawn positive/negative contours."""
    from matplotlib.lines import Line2D

    name = contour_layer_name(layer)
    handles = []
    for sign in drawn:
        handles.append(
            Line2D(
                [],
                [],
                color=layer.get(f"{sign}_color", "white"),
                linewidth=layer.get(f"{sign}_linewidth", 1.0),
                linestyle=layer.get(f"{sign}_linestyle", "-" if sign == "pos" else "--"),
                label=name if sign == "pos" else f"{name} (−)",
            )
        )
    return handles


def _contour_artists(cs):
    """The artists holding a ContourSet's paths (itself on Matplotlib >= 3.8)."""
    from matplotlib.collections import Collection

    return [cs] if isinstance(cs, Collection) else list(cs.collections)


def _same_pixel_grid(wcs_a, wcs_b):
    """True if two display WCSs describe the same pixel grid."""
    return (
        list(wcs_a.wcs.ctype) == list(wcs_b.wcs.ctype)
        and np.allclose(wcs_a.wcs.crpix, wcs_b.wcs.crpix, rtol=0, atol=1e-3)
        and np.allclose(wcs_a.wcs.crval, wcs_b.wcs.crval, rtol=0, atol=1e-6)
        and np.allclose(wcs_a.wcs.cdelt, wcs_b.wcs.cdelt, rtol=1e-3, atol=0)
    )


def _transformable_wcs(display_wcs, header):
    """Copy of a display WCS that astropy can transform between frames.

    SOLAR-X/Y (linear, in arcsec) becomes HPLN/HPLT-TAN in degrees, and the
    observation time and observer position are taken from ``header`` when
    it has them.
    """
    wcs = display_wcs.deepcopy()
    if "SOLAR" in wcs.wcs.ctype[0].upper():
        wcs.wcs.crval = np.asarray(display_wcs.wcs.crval) / 3600.0
        wcs.wcs.cdelt = np.asarray(display_wcs.wcs.cdelt) / 3600.0
        wcs.wcs.ctype = ["HPLN-TAN", "HPLT-TAN"]
    header = header or {}
    date_obs = header.get("DATE-OBS") or header.get("DATE_OBS")
    if date_obs:
        wcs.wcs.dateobs = str(date_obs)
    if wcs.wcs.ctype[0].startswith("HPLN"):
        for key in ("HGLN_OBS", "HGLT_OBS", "DSUN_OBS", "RSUN_REF"):
            if header.get(key) is not None:
                setattr(wcs.wcs.aux, key.lower(), float(header[key]))
    wcs.wcs.set()
    return wcs


def _pixel_mapping(src_wcs, dst_wcs):
    """Function mapping (n, 2) pixel positions of ``src_wcs`` to ``dst_wcs``.

    Between helioprojective frames of different observers, points are taken
    to lie on a sphere through the Sun's centre as seen from the source's
    observer, so off-limb (coronal) emission maps as well.
    """
    import contextlib

    from astropy.wcs.utils import pixel_to_pixel, wcs_to_celestial_frame

    observer = None
    try:
        from sunpy.coordinates import Helioprojective

        frame = wcs_to_celestial_frame(src_wcs)
        if isinstance(frame, Helioprojective):
            observer = frame.observer
    except Exception:
        pass

    def mapping(xy):
        xy = np.asarray(xy, dtype=float).reshape(-1, 2)
        if not len(xy):
            return xy
        if observer is None:
            screen = contextlib.nullcontext()
        else:
            screen = Helioprojective.assume_spherical_screen(observer)
        with screen:
            x, y = pixel_to_pixel(src_wcs, dst_wcs, xy[:, 0], xy[:, 1])
        return np.column_stack([x, y])

    return mapping


def _check_mapping(mapping, shape):
    """Raise if ``mapping`` fails on the corners and centre of an image."""
    nx, ny = shape
    probe = np.array(
        [[0, 0], [nx - 1, 0], [0, ny - 1], [nx - 1, ny - 1], [nx / 2, ny / 2]]
    )
    if not np.isfinite(mapping(probe)).any():
        raise ValueError("no point of the contour image maps onto the base image")


class _PixelToPixel(Transform):
    """Matplotlib transform wrapping a pixel-to-pixel mapping function.

    WCSAxes.contour applies it once to all contour vertices."""

    input_dims = output_dims = 2

    def __init__(self, mapping):
        super().__init__()
        self._mapping = mapping

    def transform_non_affine(self, values):
        return self._mapping(values)

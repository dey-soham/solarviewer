#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Video utilities for advanced video creation features.

This module provides:
- WCS coordinate detection and axes creation
- Contour layers of video frames
"""

import os

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# WCS Coordinate Detection and Axes
# =============================================================================


def detect_coordinate_system(fits_file):
    """
    Detect the coordinate system from a FITS file.

    Parameters
    ----------
    fits_file : str
        Path to the FITS file

    Returns
    -------
    dict
        Dictionary with:
        - 'type': 'radec', 'helioprojective', or 'pixel'
        - 'wcs': WCS object if available, None otherwise
        - 'ctype1', 'ctype2': Coordinate type strings
    """
    try:
        from ..processing.fits_io import fits_image_path

        with fits.open(fits_image_path(fits_file)) as hdul:
            header = hdul[0].header

            try:
                wcs_obj = WCS(header, naxis=2)
            except Exception:
                wcs_obj = None

            ctype1 = header.get("CTYPE1", "").upper()
            ctype2 = header.get("CTYPE2", "").upper()

            # Detect coordinate system
            if "HPLN" in ctype1 or "HPLT" in ctype1:
                coord_type = "helioprojective"
            elif "RA" in ctype1 or "DEC" in ctype1:
                coord_type = "radec"
            elif "GLON" in ctype1 or "GLAT" in ctype1:
                coord_type = "galactic"
            else:
                coord_type = "pixel"

            return {
                "type": coord_type,
                "wcs": wcs_obj,
                "ctype1": ctype1,
                "ctype2": ctype2,
                "cunit1": header.get("CUNIT1", ""),
                "cunit2": header.get("CUNIT2", ""),
            }
    except Exception as e:
        logger.error(f"Error detecting coordinate system: {e}")
        return {
            "type": "pixel",
            "wcs": None,
            "ctype1": "",
            "ctype2": "",
        }


def coordinate_type(header):
    """'hpc', 'radec' or 'other': the sky coordinates of a FITS header."""
    ctype = str((header or {}).get("CTYPE1", "")).upper()
    if "HPLN" in ctype or "SOLAR" in ctype:
        return "hpc"
    if ctype.startswith("RA"):
        return "radec"
    return "other"


def get_wcs_axis_labels(coord_info):
    """
    Get appropriate axis labels based on coordinate system.

    Parameters
    ----------
    coord_info : dict
        Dictionary from detect_coordinate_system()

    Returns
    -------
    tuple
        (xlabel, ylabel)
    """
    coord_type = coord_info.get("type", "pixel")
    cunit1 = coord_info.get("cunit1", "")
    cunit2 = coord_info.get("cunit2", "")

    if coord_type == "helioprojective":
        xlabel = f"Solar-X ({cunit1})" if cunit1 else "Solar-X (arcsec)"
        ylabel = f"Solar-Y ({cunit2})" if cunit2 else "Solar-Y (arcsec)"
    elif coord_type == "radec":
        xlabel = "Right Ascension"
        ylabel = "Declination"
    elif coord_type == "galactic":
        xlabel = "Galactic Longitude"
        ylabel = "Galactic Latitude"
    else:
        xlabel = "X (pixels)"
        ylabel = "Y (pixels)"

    return xlabel, ylabel


def create_wcs_axes(fig, wcs_obj, subplot_spec=111):
    """
    Create matplotlib axes with WCS projection.

    Parameters
    ----------
    fig : matplotlib.figure.Figure
        Figure to add axes to
    wcs_obj : astropy.wcs.WCS
        WCS object for projection
    subplot_spec : int or SubplotSpec
        Subplot specification

    Returns
    -------
    matplotlib.axes.Axes
        Axes with WCS projection
    """
    if wcs_obj is not None:
        ax = fig.add_subplot(subplot_spec, projection=wcs_obj)
    else:
        ax = fig.add_subplot(subplot_spec)
    return ax


# =============================================================================
# Contour Video Processor
# =============================================================================


class FrameContours:
    """Draws the contour layers of video frames.

    Layers are those of the main viewer (see processing.contour_layers),
    with one more kind of source for videos: ``"series"``, whose file for
    each frame is in ``layer["frame_files"]``. A ``"same"`` layer contours
    the frame's own image, an ``"external"`` one a fixed file. The last few
    contour images read (and converted) are kept, so a fixed one is read
    once.
    """

    def __init__(self):
        # (path, stokes, coordinate system) -> (data, wcs), see contour_image
        self._contour_images = {}
        # Same keys -> why the image could not be read or converted
        self._failures = {}

    def contour_image(self, path, stokes, base_header, load_func=None):
        """(data, wcs) of contour image ``path`` in the coordinate system of
        the image it is drawn on (RA/Dec or helioprojective).

        When the systems differ the image is converted the way the main
        viewer converts contour images, so contours land in the same place
        in videos as on screen. (None, None) if it can't be read. The last
        few images are kept, so a fixed contour image is read only once.
        """
        target = coordinate_type(base_header)
        key = (path, stokes, target)
        if key in self._contour_images:
            return self._contour_images[key]

        if load_func is None:
            from .creator import load_fits_data

            load_func = load_fits_data

        from ..processing.fits_io import fits_image_path

        result = (None, None)
        data, header = load_func(path, stokes=stokes)
        if data is None:
            self._failures[key] = f"could not read {os.path.basename(path)}"
        else:
            own = coordinate_type(header)
            try:
                if target == "hpc" and own == "radec":
                    from ..coordinates.helioprojective import convert_to_hpc

                    hpc_map, _, _ = convert_to_hpc(fits_image_path(path), Stokes=stokes)
                    result = (np.asarray(hpc_map.data), hpc_map.wcs)
                elif target == "radec" and own == "hpc":
                    import tempfile

                    from ..coordinates.helioprojective import convert_hpc_to_radec

                    with tempfile.TemporaryDirectory() as tmp:
                        converted = os.path.join(tmp, "radec.fits")
                        if convert_hpc_to_radec(fits_image_path(path), converted):
                            data, header = load_func(converted, stokes=stokes)
                            result = (data, WCS(header, naxis=2))
                        else:
                            self._failures[key] = "could not convert it to RA/Dec"
                else:
                    result = (data, WCS(header, naxis=2))
            except Exception as e:
                self._failures[key] = f"could not convert {os.path.basename(path)} ({e})"

        self._contour_images[key] = result
        while len(self._contour_images) > 4:
            self._contour_images.pop(next(iter(self._contour_images)))
        return result

    def draw(
        self,
        ax,
        layers,
        frame_idx,
        image_path,
        image_header,
        image_data,
        image_stokes="I",
        region_origin=(0, 0),
        legend_options=None,
        report=None,
        level_scales=None,
    ):
        """Draw ``layers`` for frame ``frame_idx`` on ``ax``, which shows
        ``image_data`` (the full image of ``image_path``, before any region
        is cut out; ``region_origin`` is the region's lower left corner).
        ``report(message)`` hears of layers that could not be drawn;
        ``level_scales`` (layer id -> see level_scale) fixes levels.
        """
        from ..processing.contour_layers import contour_layer_name, legend_handles

        def problem(layer, reason):
            message = f"Contours {contour_layer_name(layer, 'Image')} not drawn: {reason}"
            logger.warning(f"Frame {frame_idx}: {message}")
            if report:
                report(message)

        legend_options = legend_options or {}
        target_wcs = None
        if image_header is not None:
            try:
                target_wcs = WCS(image_header, naxis=2)
            except Exception:
                pass

        handles = []
        for layer in layers:
            if not layer.get("enabled", True):
                continue
            path = layer_file(layer, frame_idx, image_path)
            if not path:
                continue
            data, wcs = self.layer_data(
                layer, frame_idx, image_path, image_header, image_data, image_stokes, target_wcs
            )
            if data is None:
                key = (path, layer.get("stokes", "I"), coordinate_type(image_header))
                problem(
                    layer, self._failures.get(key, f"could not read {os.path.basename(path)}")
                )
                continue
            drawn = self._draw_layer(
                ax,
                layer,
                data,
                wcs,
                target_wcs,
                image_data.shape,
                region_origin,
                lambda reason, layer=layer: problem(layer, reason),
                (level_scales or {}).get(layer.get("id")),
            )
            if not legend_options.get("legend_show_negative", False):
                drawn = [sign for sign in drawn if sign == "pos"]
            handles.extend(legend_handles(layer, drawn, image_name="Image"))

        if handles and legend_options.get("show_legend", True):
            legend = ax.legend(
                handles=handles,
                loc=legend_options.get("legend_loc", "upper right"),
                fontsize=legend_options.get("legend_fontsize", 8),
                framealpha=0.6,
            )
            legend.set_zorder(10)

    def layer_data(
        self, layer, frame_idx, image_path, image_header, image_data, image_stokes, target_wcs=None
    ):
        """(data, wcs) ``layer`` contours on frame ``frame_idx`` (whose image
        ``image_path`` has ``image_data`` and ``image_header``), in the
        frame's coordinates; (None, None) if it has none or can't be read."""
        path = layer_file(layer, frame_idx, image_path)
        if not path:
            return None, None
        if path == image_path and layer.get("stokes", "I") == image_stokes:
            if target_wcs is None and image_header is not None:
                try:
                    target_wcs = WCS(image_header, naxis=2)
                except Exception:
                    pass
            return image_data, target_wcs
        return self.contour_image(path, layer.get("stokes", "I"), image_header)

    def _draw_layer(
        self,
        ax,
        layer,
        data,
        contour_wcs,
        target_wcs,
        target_shape,
        region_origin,
        problem,
        scale=None,
    ):
        """Draw one layer; returns the signs ("pos"/"neg") drawn. Calls
        ``problem(reason)`` if it can't be drawn."""
        from astropy.wcs import WCSCOMPARE_ANCILLARY

        from ..processing.contour_mapping import draw_mapped_contours, mapping_between

        mapping = None
        if target_wcs is not None and contour_wcs is not None:
            same_grid = data.shape == tuple(target_shape) and target_wcs.wcs.compare(
                contour_wcs.wcs, cmp=WCSCOMPARE_ANCILLARY, tolerance=1e-6
            )
            if not same_grid:
                try:
                    mapping = mapping_between(
                        contour_wcs, target_wcs, (data.shape[1], data.shape[0])
                    )
                except Exception as e:
                    problem(f"cannot place them on the image ({e})")
                    return []
        elif data.shape != tuple(target_shape):
            problem("the images differ in size and have no coordinates to align them")
            return []

        # Trace large contour images on at most about 2048 x 2048 pixels
        step = max(1, int(np.ceil(max(data.shape) / 2048.0)))
        drawn = []
        for sign, levels in zip(("pos", "neg"), frame_levels(layer, data, scale)):
            if not levels:
                continue
            try:
                cs = draw_mapped_contours(
                    ax,
                    data,
                    mapping,
                    step=step,
                    offset=region_origin,
                    levels=levels,
                    colors=layer.get(f"{sign}_color", "white"),
                    linewidths=layer.get(f"{sign}_linewidth", 1.0),
                    linestyles=layer.get(f"{sign}_linestyle", "-" if sign == "pos" else "--"),
                )
                if layer.get("show_labels", False):
                    ax.clabel(cs, inline=True, fontsize=8, fmt="%.2g")
                drawn.append(sign)
            except Exception as e:
                problem(f"error drawing the {'negative' if sign == 'neg' else 'positive'} levels ({e})")
        return drawn


def layer_file(layer, frame_idx, image_path):
    """The file a contour layer draws on frame ``frame_idx``, or None."""
    source = layer.get("source", "same")
    if source == "series":
        frame_files = layer.get("frame_files") or []
        return frame_files[frame_idx] if frame_idx < len(frame_files) else None
    if source == "external":
        return layer.get("external_image") or None
    return image_path


def _with_rms_box(layer, data):
    """``layer`` with its RMS box for ``data`` (rows = y): the main viewer's
    default box (5-95 % across, 5-20 % up) unless one was chosen."""
    if layer.get("level_type") == "sigma" and layer.get("use_default_rms_region", True):
        rows, cols = data.shape
        layer = dict(
            layer,
            rms_box=(int(cols * 0.05), int(cols * 0.95), int(rows * 0.05), int(rows * 0.20)),
        )
    return layer


def level_scale(layer, data):
    """What ``layer``'s levels are relative to in ``data`` (rows = y): the
    peak |value| for fraction levels, the RMS for sigma levels, else None."""
    from ..processing.contour_layers import box_rms

    kind = layer.get("level_type")
    if kind == "fraction":
        return float(np.nanmax(np.abs(data)))
    if kind == "sigma":
        layer = _with_rms_box(layer, data)
        return float(box_rms(data.T, layer.get("rms_box", (0, 200, 0, 130))))
    return None


def frame_levels(layer, data, scale=None):
    """(positive, negative) levels of ``layer`` for ``data`` (rows = y), as
    the main viewer works them out (its data is x-first, hence the .T).
    ``scale`` (see level_scale) fixes the peak / RMS the levels are
    relative to, e.g. the same in every frame."""
    from ..processing.contour_layers import contour_levels

    layer = _with_rms_box(layer, data)
    if scale is None:
        return contour_levels(layer, data.T)
    if layer.get("level_type") == "sigma":
        return contour_levels(layer, data.T, rms=scale)
    return contour_levels(layer, data.T, abs_max=scale)

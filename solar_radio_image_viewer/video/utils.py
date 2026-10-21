#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Video utilities for advanced video creation features.

This module provides:
- WCS coordinate detection and axes creation
- Min/Max timeline plotting
- Contour layers of video frames
"""

import numpy as np
import matplotlib.pyplot as plt
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
        with fits.open(fits_file) as hdul:
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
# Min/Max Timeline
# =============================================================================


class MinMaxTimeline:
    """
    Manages min/max timeline plotting for video frames.

    The timeline shows a continuous plot of min and max pixel values
    across all frames processed so far.
    """

    def __init__(self, total_frames, position="bottom-left", log_scale=False):
        """
        Initialize the timeline.

        Parameters
        ----------
        total_frames : int
            Total number of frames in the video
        position : str
            Position of the timeline: 'bottom-left', 'bottom-right',
            'top-left', 'top-right'
        log_scale : bool
            If True, use logarithmic scale for y-axis
        """
        self.total_frames = total_frames
        self.position = position
        self.log_scale = log_scale
        self.min_values = []
        self.max_values = []
        self.frame_numbers = []

        # Position configurations [left, bottom, width, height]
        self.positions = {
            "bottom-left": [0.05, 0.05, 0.20, 0.10],
            "bottom-right": [0.75, 0.05, 0.20, 0.10],
            "top-left": [0.05, 0.85, 0.20, 0.10],
            "top-right": [0.75, 0.85, 0.20, 0.10],
        }

    def add_frame_stats(self, frame_idx, vmin, vmax):
        """
        Add statistics for a frame.

        Parameters
        ----------
        frame_idx : int
            Frame index (0-based)
        vmin : float
            Minimum pixel value for this frame
        vmax : float
            Maximum pixel value for this frame
        """
        self.frame_numbers.append(frame_idx)
        self.min_values.append(vmin)
        self.max_values.append(vmax)

    def precompute_stats(self, files, options, load_func):
        """
        Precompute min/max statistics for all frames.

        Parameters
        ----------
        files : list
            List of file paths
        options : dict
            Video creation options
        load_func : callable
            Function to load FITS data: load_func(file, stokes) -> (data, header)

        Returns
        -------
        tuple
            (all_mins, all_maxs) lists
        """
        all_mins = []
        all_maxs = []
        stokes = options.get("stokes", "I")

        for file_path in files:
            try:
                data, _ = load_func(file_path, stokes=stokes)
                if data is not None:
                    # Apply region if enabled
                    if options.get("region_enabled", False):
                        x_min = options.get("x_min", 0)
                        x_max = options.get("x_max", data.shape[1] - 1)
                        y_min = options.get("y_min", 0)
                        y_max = options.get("y_max", data.shape[0] - 1)
                        data = data[y_min : y_max + 1, x_min : x_max + 1]

                    all_mins.append(np.nanmin(data))
                    all_maxs.append(np.nanmax(data))
            except Exception as e:
                logger.warning(f"Error computing stats for {file_path}: {e}")
                all_mins.append(np.nan)
                all_maxs.append(np.nan)

        self.min_values = all_mins
        self.max_values = all_maxs
        self.frame_numbers = list(range(len(files)))

        return all_mins, all_maxs

    def draw_timeline(self, fig, current_frame_idx, ax=None):
        """
        Draw the timeline on the figure.

        Parameters
        ----------
        fig : matplotlib.figure.Figure
            Figure to draw on
        current_frame_idx : int
            Current frame index (0-based)
        ax : matplotlib.axes.Axes, optional
            If provided, use this axes instead of creating an overlay

        Returns
        -------
        matplotlib.axes.Axes
            The timeline axes
        """
        # Use provided axes (dock panel mode) or create overlay (legacy mode)
        if ax is None:
            pos = self.positions.get(self.position, self.positions["bottom-left"])
            ax = fig.add_axes(pos)

        # Plot data up to current frame
        frames_to_show = self.frame_numbers[: current_frame_idx + 1]
        mins_to_show = self.min_values[: current_frame_idx + 1]
        maxs_to_show = self.max_values[: current_frame_idx + 1]

        # Plot filled area between min and max (shows range)
        if len(frames_to_show) > 1:
            ax.fill_between(
                frames_to_show,
                mins_to_show,
                maxs_to_show,
                alpha=0.3,
                color="#4FC3F7",
                label="Range",
            )

        # Plot min and max lines
        ax.plot(
            frames_to_show, mins_to_show, color="#29B6F6", linewidth=1.5, label="Min"
        )
        ax.plot(
            frames_to_show, maxs_to_show, color="#FF7043", linewidth=1.5, label="Max"
        )

        # Plot future data as faded lines
        if current_frame_idx < len(self.min_values) - 1:
            future_frames = self.frame_numbers[current_frame_idx:]
            future_mins = self.min_values[current_frame_idx:]
            future_maxs = self.max_values[current_frame_idx:]
            ax.plot(
                future_frames, future_mins, color="#29B6F6", linewidth=0.8, alpha=0.3
            )
            ax.plot(
                future_frames, future_maxs, color="#FF7043", linewidth=0.8, alpha=0.3
            )

        # Current frame marker - vertical line and points
        if current_frame_idx < len(self.min_values):
            # Use gray for current frame line to work on both light/dark backgrounds
            ax.axvline(
                current_frame_idx, color="gray", linestyle="-", linewidth=1.5, alpha=0.7
            )
            ax.plot(
                current_frame_idx,
                self.min_values[current_frame_idx],
                "o",
                color="#29B6F6",
                markersize=6,
                markeredgecolor="gray",
                markeredgewidth=1,
            )
            ax.plot(
                current_frame_idx,
                self.max_values[current_frame_idx],
                "o",
                color="#FF7043",
                markersize=6,
                markeredgecolor="gray",
                markeredgewidth=1,
            )

        # Set x limits to show full range
        if self.total_frames > 1:
            ax.set_xlim(-0.5, self.total_frames - 0.5)

        # Set y limits based on all data with margin
        all_vals = self.min_values + self.max_values
        valid_vals = [v for v in all_vals if not np.isnan(v)]
        if valid_vals:
            ymin, ymax = min(valid_vals), max(valid_vals)
            margin = (ymax - ymin) * 0.15 if ymax != ymin else abs(ymax) * 0.1
            ax.set_ylim(ymin - margin, ymax + margin)

        # Style for dock panel (dynamic theme)
        # ax.set_facecolor('#1a1a2e') # Let theme decide facecolor
        ax.tick_params(labelsize=10, direction="in", length=3)

        # Show only left and bottom spines (let theme decide color)
        # ax.spines['bottom'].set_color('#555555')
        # ax.spines['left'].set_color('#555555')
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        # X-axis: show frame numbers
        ax.set_xlabel("Frame", fontsize=11, labelpad=2)

        # Y-axis: show value range with scientific notation if needed
        ax.set_ylabel("Value", fontsize=11, labelpad=2)

        # Apply log scale if enabled
        if self.log_scale:
            ax.set_yscale("log")
        else:
            ax.yaxis.set_major_locator(plt.MaxNLocator(3))  # Limit to 3 ticks

        # Format y-axis with scientific notation for large values
        from matplotlib.ticker import ScalarFormatter

        formatter = ScalarFormatter(useMathText=True)
        formatter.set_scientific(True)
        formatter.set_powerlimits((-2, 3))
        ax.yaxis.set_major_formatter(formatter)
        # ax.yaxis.get_offset_text().set_color('white')
        ax.yaxis.get_offset_text().set_fontsize(8)

        # Add current values annotation
        if current_frame_idx < len(self.min_values):
            curr_min = self.min_values[current_frame_idx]
            curr_max = self.max_values[current_frame_idx]
            info_text = f"Frame {current_frame_idx + 1}/{self.total_frames}"
            ax.text(
                0.98,
                0.92,
                info_text,
                transform=ax.transAxes,
                fontsize=10,
                ha="right",
                va="top",
                alpha=0.9,
            )

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

        result = (None, None)
        data, header = load_func(path, stokes=stokes)
        if data is not None:
            own = coordinate_type(header)
            try:
                if target == "hpc" and own == "radec":
                    from ..coordinates.helioprojective import convert_to_hpc

                    hpc_map, _, _ = convert_to_hpc(path, Stokes=stokes)
                    result = (np.asarray(hpc_map.data), hpc_map.wcs)
                elif target == "radec" and own == "hpc":
                    import os
                    import tempfile

                    from ..coordinates.helioprojective import convert_hpc_to_radec

                    with tempfile.TemporaryDirectory() as tmp:
                        converted = os.path.join(tmp, "radec.fits")
                        if convert_hpc_to_radec(path, converted):
                            data, header = load_func(converted, stokes=stokes)
                            result = (data, WCS(header, naxis=2))
                else:
                    result = (data, WCS(header, naxis=2))
            except Exception as e:
                logger.warning(f"Could not prepare contour image {path}: {e}")

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
    ):
        """Draw ``layers`` for frame ``frame_idx`` on ``ax``, which shows
        ``image_data`` (the full image of ``image_path``, before any region
        is cut out; ``region_origin`` is the region's lower left corner).
        """
        from ..processing.contour_layers import legend_handles

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
            if path == image_path and layer.get("stokes", "I") == image_stokes:
                data, wcs = image_data, target_wcs
            else:
                data, wcs = self.contour_image(path, layer.get("stokes", "I"), image_header)
            if data is None:
                logger.warning(f"Frame {frame_idx}: no contour data from {path}")
                continue
            drawn = self._draw_layer(ax, layer, data, wcs, target_wcs, image_data.shape, region_origin)
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

    def _draw_layer(self, ax, layer, data, contour_wcs, target_wcs, target_shape, region_origin):
        """Draw one layer; returns the signs ("pos"/"neg") drawn."""
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
                    logger.warning(f"Contours not drawn: cannot place them on the image ({e})")
                    return []
        elif data.shape != tuple(target_shape):
            logger.warning(
                "Contours not drawn: the images differ in size and have no "
                "coordinates to align them"
            )
            return []

        # Trace large contour images on at most about 2048 x 2048 pixels
        step = max(1, int(np.ceil(max(data.shape) / 2048.0)))
        drawn = []
        for sign, levels in zip(("pos", "neg"), frame_levels(layer, data)):
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
                logger.warning(f"Error drawing {sign} contours: {e}")
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


def frame_levels(layer, data):
    """(positive, negative) levels of ``layer`` for ``data`` (rows = y), as
    the main viewer works them out (its data is x-first, hence the .T)."""
    from ..processing.contour_layers import contour_levels

    if layer.get("level_type") == "sigma" and layer.get("use_default_rms_region", True):
        # The main viewer's default RMS box: 5-95 % across, 5-20 % up
        rows, cols = data.shape
        layer = dict(
            layer,
            rms_box=(int(cols * 0.05), int(cols * 0.95), int(rows * 0.05), int(rows * 0.20)),
        )
    return contour_levels(layer, data.T)

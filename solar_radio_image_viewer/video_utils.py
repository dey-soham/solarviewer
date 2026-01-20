#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Video utilities for advanced video creation features.

This module provides:
- WCS coordinate detection and axes creation
- Min/Max timeline plotting
- Contour video processing
"""

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
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


class ContourVideoProcessor:
    """
    Handles contour processing for video creation.

    Supports three modes:
    - Mode A: Fixed base image, evolving contours
    - Mode B: Fixed contours, evolving colormap image
    - Mode C: Both evolve
    """

    def __init__(self, mode="A", contour_settings=None):
        """
        Initialize the contour processor.

        Parameters
        ----------
        mode : str
            'A', 'B', or 'C'
        contour_settings : dict
            Contour configuration dictionary
        """
        self.mode = mode.upper()
        self.contour_settings = contour_settings or self._default_settings()

        # Cache for base image / contour data
        self.base_image_data = None
        self.base_image_wcs = None
        self.fixed_contour_data = None
        self.fixed_contour_wcs = None
        self.fixed_contour_levels = None

    def _default_settings(self):
        """Return default contour settings."""
        return {
            "level_type": "fraction",  # 'fraction', 'sigma', 'absolute'
            "pos_levels": [0.1, 0.3, 0.5, 0.7, 0.9],
            "neg_levels": [0.1, 0.3, 0.5, 0.7, 0.9],
            "pos_color": "white",
            "neg_color": "cyan",
            "linewidth": 1.0,
            "pos_linestyle": "-",
            "neg_linestyle": "--",
        }

    def load_base_image(self, file_path, stokes="I", load_func=None):
        """Load the fixed base image (for mode A)."""
        if load_func is None:
            from .create_video import load_fits_data

            load_func = load_fits_data

        self.base_image_data, header = load_func(file_path, stokes=stokes)

        # Create WCS from the same header used for data loading
        if header:
            try:
                self.base_image_wcs = WCS(header, naxis=2)
                logger.info(
                    f"Loaded base_image_wcs: CRPIX={self.base_image_wcs.wcs.crpix}"
                )
            except Exception as e:
                logger.warning(f"Failed to create base_image_wcs: {e}")
                self.base_image_wcs = None
        else:
            self.base_image_wcs = None

    def load_fixed_contour(self, file_path, stokes="I", load_func=None):
        """Load the fixed contour data (for mode B)."""
        if load_func is None:
            from .create_video import load_fits_data

            load_func = load_fits_data

        self.fixed_contour_data, header = load_func(file_path, stokes=stokes)

        # Create WCS from the same header used for data loading
        if header:
            try:
                self.fixed_contour_wcs = WCS(header, naxis=2)
            except Exception:
                self.fixed_contour_wcs = None
        else:
            self.fixed_contour_wcs = None

        # Pre-compute levels
        self._compute_contour_levels(self.fixed_contour_data)

    def _compute_contour_levels(self, data):
        """Compute contour levels based on settings."""
        settings = self.contour_settings
        level_type = settings.get("level_type", "fraction")
        pos_levels = settings.get("pos_levels", [])
        neg_levels = settings.get("neg_levels", [])

        abs_max = np.nanmax(np.abs(data))

        if level_type == "fraction":
            pos = sorted([level * abs_max for level in pos_levels])
            neg = sorted([-level * abs_max for level in neg_levels])
        elif level_type == "sigma":
            # Use bottom 10% of image (full width, bottom 10% height) for RMS calculation
            # This avoids including the sun in the noise estimate
            height = data.shape[0]
            bottom_10_pct = max(1, int(height * 0.1))  # At least 1 row
            noise_region = data[:bottom_10_pct, :]  # Bottom rows (low y indices)
            rms = np.nanstd(noise_region)
            pos = sorted([level * rms for level in pos_levels])
            neg = sorted([-level * rms for level in neg_levels])
        else:  # absolute
            pos = sorted(pos_levels)
            neg = sorted([-level for level in neg_levels])

        self.fixed_contour_levels = {"pos": pos, "neg": neg}
        return self.fixed_contour_levels

    def compute_contour_levels(self, data):
        """
        Compute contour levels for given data.

        Parameters
        ----------
        data : ndarray
            Image data

        Returns
        -------
        dict
            {'pos': [...], 'neg': [...]}
        """
        return self._compute_contour_levels(data)

    def draw_contours(
        self,
        ax,
        contour_data,
        levels=None,
        target_wcs=None,
        contour_wcs=None,
        target_shape=None,
        region_info=None,
    ):
        """
        Draw contours on axes with support for WCS reprojection.

        Parameters
        ----------
        region_info : dict, optional
            If provided, contains 'x_min', 'y_min', 'x_max', 'y_max', 'full_shape'
            Used to reproject to full shape first, then crop.
        """
        settings = self.contour_settings

        if levels is None:
            levels = self.compute_contour_levels(contour_data)

        collections = []
        # Reprojection logic
        if target_wcs and contour_wcs and target_shape is not None:
            try:
                from reproject import reproject_interp
                from astropy.wcs import WCS

                # Create axis-swapped WCS objects matching viewer.py's approach
                def create_swapped_wcs(orig_wcs):
                    """Create WCS with swapped axes for reprojection."""
                    swapped = WCS(naxis=2)
                    swapped.wcs.crpix = [orig_wcs.wcs.crpix[1], orig_wcs.wcs.crpix[0]]
                    swapped.wcs.crval = [orig_wcs.wcs.crval[1], orig_wcs.wcs.crval[0]]
                    try:
                        swapped.wcs.cdelt = [
                            orig_wcs.wcs.cdelt[1],
                            orig_wcs.wcs.cdelt[0],
                        ]
                    except Exception:
                        pass
                    if orig_wcs.wcs.ctype[0] and orig_wcs.wcs.ctype[1]:
                        swapped.wcs.ctype = [
                            orig_wcs.wcs.ctype[1],
                            orig_wcs.wcs.ctype[0],
                        ]
                    return swapped

                # Swap both WCS for consistent reprojection
                target_wcs_swapped = create_swapped_wcs(target_wcs)
                contour_wcs_swapped = create_swapped_wcs(contour_wcs)

                # If region_info provided, reproject to FULL shape first, then crop
                if region_info:
                    full_shape = region_info.get("full_shape", target_shape)

                    # Reproject to full base image shape
                    reprojected_data, footprint = reproject_interp(
                        (contour_data, contour_wcs_swapped),
                        target_wcs_swapped,
                        shape_out=full_shape,
                    )

                    # Then crop to the region
                    x_min = region_info.get("x_min", 0)
                    y_min = region_info.get("y_min", 0)
                    x_max = region_info.get("x_max", full_shape[1] - 1)
                    y_max = region_info.get("y_max", full_shape[0] - 1)
                    reprojected_data = reprojected_data[
                        y_min : y_max + 1, x_min : x_max + 1
                    ]
                else:
                    # No region - reproject directly to target shape
                    reprojected_data, footprint = reproject_interp(
                        (contour_data, contour_wcs_swapped),
                        target_wcs_swapped,
                        shape_out=target_shape,
                    )

                # Log reprojection result
                nan_pct = 100 * np.isnan(reprojected_data).sum() / reprojected_data.size
                logger.info(f"[REPROJ] Result: {nan_pct:.1f}% NaNs")

                # Check validity of reprojected data
                is_all_nan = np.all(np.isnan(reprojected_data))
                is_all_zero = np.all(np.nan_to_num(reprojected_data) == 0)

                if is_all_nan or is_all_zero:
                    logger.warning(
                        "Reprojection yielded empty data. Falling back to pixel overlay."
                    )
                else:
                    contour_data = reprojected_data

            except Exception as e:
                logger.warning(
                    f"Contour reprojection failed: {e}. Falling back to pixel overlay."
                )

        # Draw positive contours
        if levels.get("pos"):
            try:
                cs_pos = ax.contour(
                    contour_data,
                    levels=levels["pos"],
                    colors=settings.get("pos_color", "white"),
                    linewidths=settings.get("linewidth", 1.0),
                    linestyles=settings.get("pos_linestyle", "-"),
                )
                collections.append(cs_pos)
            except Exception as e:
                logger.warning(f"Error drawing positive contours: {e}")

        # Draw negative contours
        if levels.get("neg"):
            try:
                cs_neg = ax.contour(
                    contour_data,
                    levels=levels["neg"],
                    colors=settings.get("neg_color", "cyan"),
                    linewidths=settings.get("linewidth", 1.0),
                    linestyles=settings.get("neg_linestyle", "--"),
                )
                collections.append(cs_neg)
            except Exception as e:
                logger.warning(f"Error drawing negative contours: {e}")

        return collections

    def get_frame_data(
        self,
        frame_idx,
        colormap_files=None,
        contour_files=None,
        stokes="I",
        load_func=None,
    ):
        """
        Get data for a specific frame based on mode.

        Parameters
        ----------
        frame_idx : int
            Frame index
        colormap_files : list
            List of colormap image files
        contour_files : list
            List of contour image files
        stokes : str
            Stokes parameter
        load_func : callable
            Function to load FITS data

        Returns
        -------
        dict
            {'colormap_data': ..., 'contour_data': ..., 'contour_levels': ...}
        """
        if load_func is None:
            from .create_video import load_fits_data

            load_func = load_fits_data

        result = {}

        if self.mode == "A":
            # Fixed base, evolving contours
            result["colormap_data"] = self.base_image_data
            if contour_files and frame_idx < len(contour_files):
                data, _ = load_func(contour_files[frame_idx], stokes=stokes)
                result["contour_data"] = data
                result["contour_levels"] = self.compute_contour_levels(data)

        elif self.mode == "B":
            # Fixed contours, evolving colormap
            result["contour_data"] = self.fixed_contour_data
            result["contour_levels"] = self.fixed_contour_levels
            if colormap_files and frame_idx < len(colormap_files):
                data, _ = load_func(colormap_files[frame_idx], stokes=stokes)
                result["colormap_data"] = data

        elif self.mode == "C":
            # Both evolve
            if colormap_files and frame_idx < len(colormap_files):
                data, _ = load_func(colormap_files[frame_idx], stokes=stokes)
                result["colormap_data"] = data
            if contour_files and frame_idx < len(contour_files):
                data, _ = load_func(contour_files[frame_idx], stokes=stokes)
                result["contour_data"] = data
                result["contour_levels"] = self.compute_contour_levels(data)

        return result

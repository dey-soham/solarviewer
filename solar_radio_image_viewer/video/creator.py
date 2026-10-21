#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import re
import numpy as np
from datetime import datetime
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize, LogNorm, PowerNorm
from matplotlib.figure import Figure
from matplotlib.gridspec import GridSpec
from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas
from astropy.io import fits
import imageio
from tqdm import tqdm
import threading
import time
from multiprocessing import cpu_count
from astropy.visualization import (
    ImageNormalize,
    LogStretch,
)
import warnings
import logging

# Import custom norms
from ..processing.norms import (
    SqrtNorm,
    AsinhNorm,
    PowerNorm as CustomPowerNorm,
    ZScaleNorm,
    HistEqNorm,
)
from ..processing.colormaps import ensure_solar_colormaps
from ..processing.fits_io import readable_image_path
from ..processing.workers import process_pool_executor
from .utils import (
    detect_coordinate_system,
    get_wcs_axis_labels,
    create_wcs_axes,
    MinMaxTimeline,
    FrameContours,
    layer_file,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Suppress common warnings
warnings.filterwarnings("ignore", category=UserWarning, module="matplotlib")
warnings.filterwarnings("ignore", category=UserWarning, module="astropy")


def ensure_even_dimensions(image):
    """
    Ensure image has even dimensions by padding if necessary.
    This is required for video codecs like H.264 that require dimensions divisible by 2.

    Parameters
    ----------
    image : numpy.ndarray
        Input image array

    Returns
    -------
    numpy.ndarray
        Image with even dimensions
    """
    height, width = image.shape[:2]
    pad_height = 0 if height % 2 == 0 else 1
    pad_width = 0 if width % 2 == 0 else 1

    if pad_height > 0 or pad_width > 0:
        # Pad to make dimensions even
        return np.pad(image, ((0, pad_height), (0, pad_width), (0, 0)), mode="edge")
    return image


def canvas_to_rgb_array(canvas):
    """
    Convert FigureCanvas to RGB numpy array, handling Matplotlib version differences.
    """
    width, height = canvas.get_width_height()
    try:
        # Try newer API first (Matplotlib 3.1+)
        buf = canvas.buffer_rgba()
        img_data = np.frombuffer(buf, dtype=np.uint8)
        img_data = img_data.reshape(height, width, 4)
        return img_data[:, :, :3]  # Return RGB
    except AttributeError:
        # Fallback for older Matplotlib
        img_data = np.frombuffer(canvas.tostring_rgb(), dtype=np.uint8)
        img_data = img_data.reshape(height, width, 3)
        return img_data


def create_error_frame(
    options, error_msg="Error loading frame", frame_idx=0, filename=""
):
    """
    Create a blank frame with axis and error message when data loading fails.

    This ensures the video continues with consistent frame count instead of
    showing weird plots or skipping frames entirely.
    """
    dpi = 100
    width, height = options.get("frame_size") or frame_size(options, (1, 1))
    fig = Figure(figsize=(width / dpi, height / dpi), dpi=dpi)
    canvas = FigureCanvas(fig)
    ax = fig.add_subplot(111)

    # Create a blank image (gray background)
    blank_data = np.zeros((100, 100))
    ax.imshow(blank_data, cmap="gray", vmin=0, vmax=1)

    # Add error message in the center
    ax.text(
        0.5,
        0.5,
        error_msg,
        transform=ax.transAxes,
        ha="center",
        va="center",
        fontsize=12,
        color="red",
        bbox=dict(boxstyle="round", facecolor="white", alpha=0.8),
    )

    # Add frame info
    if filename:
        ax.set_title(f"Frame {frame_idx}: {filename}", fontsize=10)
    else:
        ax.set_title(f"Frame {frame_idx}", fontsize=10)

    ax.set_xlabel("X")
    ax.set_ylabel("Y")

    # Render to image
    fig.tight_layout()
    canvas.draw()

    # Convert to numpy array
    image = canvas_to_rgb_array(fig.canvas)

    plt.close(fig)

    return image


def natural_sort_key(s):
    """
    Sort strings containing numbers naturally (e.g., 'file1', 'file2', 'file10')
    """
    return [
        int(text) if text.isdigit() else text.lower() for text in re.split(r"(\d+)", s)
    ]


STOKES_CODES = {"I": 1, "Q": 2, "U": 3, "V": 4}


def observation_time(header, filepath=None):
    """Observation time of an image as a datetime, or None if unknown.

    From the header (DATE-OBS and its variants, with a separate TIME-OBS if
    the date has no time), else from a date in the file name.
    """
    from astropy.time import Time

    if header is not None:
        for key in ("DATE-OBS", "DATE_OBS", "DATE-BEG", "STARTOBS", "DATE"):
            value = header.get(key)
            if not value:
                continue
            text = str(value).strip().rstrip("Z").replace("/", "-")
            if "T" not in text and " " not in text:
                time_obs = header.get("TIME-OBS") or header.get("TIME_OBS")
                if time_obs:
                    text = f"{text}T{str(time_obs).strip()}"
            try:
                return Time(text.replace(" ", "T")).to_datetime()
            except Exception:
                continue

    if filepath:
        filename = os.path.basename(filepath)
        patterns = [
            r"(\d{4}[-_]\d{2}[-_]\d{2}[-_T]\d{2}[-_]\d{2}[-_]\d{2})",
            r"(\d{8}[-_T]\d{6})",
            r"(\d{4}[-_]\d{2}[-_]\d{2})",
            r"(\d{8})",
        ]
        formats = [
            "%Y-%m-%d-%H-%M-%S",
            "%Y_%m_%d_%H_%M_%S",
            "%Y-%m-%dT%H-%M-%S",
            "%Y_%m_%dT%H_%M_%S",
            "%Y%m%d_%H%M%S",
            "%Y%m%d-%H%M%S",
            "%Y%m%dT%H%M%S",
            "%Y-%m-%d",
            "%Y_%m_%d",
            "%Y%m%d",
        ]
        for pattern in patterns:
            match = re.search(pattern, filename)
            if match:
                for fmt in formats:
                    try:
                        return datetime.strptime(match.group(1), fmt)
                    except ValueError:
                        continue
    return None


def observation_sort_key(filepath):
    """Sort key ordering files by observation time (then name); files
    without one come last, by modification time."""
    from ..processing.image_meta import fits_header

    try:
        when = observation_time(fits_header(filepath), filepath)
    except Exception:
        when = observation_time(None, filepath)
    if when is not None:
        return (0, when.timestamp(), filepath)
    return (1, os.path.getmtime(filepath), filepath)


def _plane_index(header, ndim, stokes):
    """Index selecting one Stokes plane and the first channel of every other
    non-spatial axis of an ``ndim``-dimensional FITS image."""
    if isinstance(stokes, (int, np.integer)):
        stokes = "IQUV"[int(stokes)]
    extra = ndim - 2  # numpy axes before the two spatial ones
    ctypes = [str(header.get(f"CTYPE{ndim - axis}", "")).upper() for axis in range(extra)]
    index = [0] * extra
    if "STOKES" in ctypes:
        axis = ctypes.index("STOKES")
        fits_axis = ndim - axis
        crval = header.get(f"CRVAL{fits_axis}", 1.0)
        cdelt = header.get(f"CDELT{fits_axis}", 1.0) or 1.0
        crpix = header.get(f"CRPIX{fits_axis}", 1.0)
        plane = int(round((STOKES_CODES[stokes] - crval) / cdelt + crpix - 1))
        if not 0 <= plane < header.get(f"NAXIS{fits_axis}", 1):
            raise ValueError(f"no Stokes {stokes} plane in the image")
        index[axis] = plane
    elif not any(ctypes):
        # No axis types: assume the first extra axis is Stokes (I, Q, U, V)
        index[0] = "IQUV".index(stokes)
    elif stokes != "I":
        raise ValueError(f"no Stokes axis in the image for Stokes {stokes}")
    return tuple(index)


def load_fits_data(filepath, stokes="I"):
    """One Stokes plane (first frequency channel) of a FITS image, rows = y,
    and its header.

    Images stored in an extension (e.g. tile-compressed) are read through
    readable_image_path; the Stokes axis is found from the CTYPE keywords.
    Returns (None, None) if the file or the Stokes plane can't be read.
    """
    try:
        with fits.open(readable_image_path(filepath)) as hdul:
            header = hdul[0].header
            data = hdul[0].data
            if data is None:
                raise ValueError("no image data")
            if data.ndim > 2:
                data = data[_plane_index(header, data.ndim, stokes)]
            return np.array(data), header
    except Exception as e:
        logger.error(f"Error loading {filepath}: {e}")
        return None, None


def frame_size(options, data_shape):
    """(width, height) of video frames in pixels, both even.

    The Width/Height options if set; a missing one follows the image's
    aspect ratio, and with neither the longer side is 1000 pixels.
    """
    width, height = options.get("width", 0), options.get("height", 0)
    rows, cols = data_shape[:2]
    aspect = cols / rows if rows else 1.0
    if width <= 0 and height <= 0:
        if aspect >= 1:
            width = 1000
        else:
            height = 1000
    if width <= 0:
        width = height * aspect
    if height <= 0:
        height = width / aspect
    return (max(2, int(round(width / 2)) * 2), max(2, int(round(height / 2)) * 2))


def apply_visualization(data, vmin=None, vmax=None, stretch="linear", gamma=1.0):
    """
    Apply visualization transformations (normalization and stretching)
    """
    if data is None:
        return None

    # Handle NaN values
    data = np.nan_to_num(data, nan=0.0)

    # Apply normalization
    if vmin is None:
        vmin = np.percentile(data, 0)
    if vmax is None:
        vmax = np.percentile(data, 100)

    # Create normalized data according to the stretch
    if stretch == "linear":
        norm = Normalize(vmin=vmin, vmax=vmax)
        normalized_data = norm(data)
    elif stretch == "log":
        # Ensure data is positive for log stretch
        if vmin <= 0:
            vmin = max(np.min(data[data > 0]), 1e-10)
        norm = LogNorm(vmin=vmin, vmax=vmax)
        normalized_data = norm(data)
    elif stretch == "sqrt":
        norm = PowerNorm(gamma=0.5, vmin=vmin, vmax=vmax)
        normalized_data = norm(data)
    elif stretch == "power":
        norm = PowerNorm(gamma=gamma, vmin=vmin, vmax=vmax)
        normalized_data = norm(data)
    else:
        # Default to linear
        norm = Normalize(vmin=vmin, vmax=vmax)
        normalized_data = norm(data)

    # Ensure range [0, 1]
    normalized_data = np.clip(normalized_data, 0, 1)

    return normalized_data


def get_norm(stretch_type, vmin, vmax, gamma=1.0):
    """Create a normalization object based on the stretch type"""
    stretch_type = stretch_type.lower()

    if stretch_type == "linear":
        return Normalize(vmin=vmin, vmax=vmax)

    elif stretch_type == "log":
        # Ensure positive values for log stretch
        if vmin <= 0:
            vmin = max(1e-10, np.min([1e-10, vmax / 1000]))
        return ImageNormalize(vmin=vmin, vmax=vmax, stretch=LogStretch())

    elif stretch_type == "sqrt":
        return SqrtNorm(vmin=vmin, vmax=vmax)

    elif stretch_type == "power":
        return CustomPowerNorm(vmin=vmin, vmax=vmax, gamma=gamma)

    elif stretch_type == "arcsinh":
        return AsinhNorm(vmin=vmin, vmax=vmax)

    elif stretch_type == "zscale":
        return ZScaleNorm(vmin=vmin, vmax=vmax)

    elif stretch_type == "histogram equalization":
        return HistEqNorm(vmin=vmin, vmax=vmax)

    else:
        # Default to linear if unknown
        logger.warning(f"Unknown stretch type: {stretch_type}, defaulting to linear")
        return Normalize(vmin=vmin, vmax=vmax)


def _frame_contour_file(options, frame_idx):
    """The file of the first contour layer that has its own files, drawn on
    frame ``frame_idx``, or None (for the Contour: overlay)."""
    for layer in options.get("contour_layers") or []:
        if layer.get("enabled", True) and layer.get("source") in ("series", "external"):
            return layer_file(layer, frame_idx, None)
    return None


def process_image(
    file_path, options, global_stats=None, frame_contours=None, frame_idx=0
):
    """Process a FITS image and return a frame for the video"""
    try:
        # Load FITS data
        data, header = load_fits_data(file_path, stokes=options.get("stokes", "I"))

        if data is None:
            logger.warning(f"Could not load data from {file_path}")
            return create_error_frame(
                options, "Could not load data", frame_idx, os.path.basename(file_path)
            )

        # Check for blank/all-NaN data
        if np.all(np.isnan(data)) or np.nanmax(data) == np.nanmin(data):
            logger.warning(f"Blank or constant data in {file_path}")
            return create_error_frame(
                options, "Blank/constant data", frame_idx, os.path.basename(file_path)
            )

        # Contours of the image itself are of the image, not of a difference
        full_data = data

        # Difference movies: subtract the previous / first image
        difference_files = options.get("difference_files")
        if difference_files:
            other, _ = load_fits_data(
                difference_files[frame_idx], stokes=options.get("stokes", "I")
            )
            if other is None or other.shape != data.shape:
                return create_error_frame(
                    options,
                    "Cannot subtract: image missing or of another size",
                    frame_idx,
                    os.path.basename(file_path),
                )
            data = data - other

        # Region shown, as (x_min, y_min) of the full image (for WCS and contours)
        region_origin = (0, 0)

        # Apply region selection if enabled
        if options.get("region_enabled", False):
            # Get region coordinates
            x_min = options.get("x_min", 0)
            x_max = options.get("x_max", data.shape[1] - 1)
            y_min = options.get("y_min", 0)
            y_max = options.get("y_max", data.shape[0] - 1)

            # Ensure proper order
            x_min, x_max = min(x_min, x_max), max(x_min, x_max)
            y_min, y_max = min(y_min, y_max), max(y_min, y_max)

            # Follow solar rotation: move the region with the feature in it
            dx, dy = _rotation_shift(options, header)
            x_min, x_max, y_min, y_max = x_min + dx, x_max + dx, y_min + dy, y_max + dy

            # Apply consistent region dimensions if provided
            if "region_width" in options and "region_height" in options:
                region_width = options["region_width"]
                region_height = options["region_height"]

                # Make sure the region doesn't exceed the image dimensions
                if x_min >= data.shape[1] or y_min >= data.shape[0]:
                    logger.warning(f"Region outside image bounds for {file_path}")
                    x_min = 0
                    y_min = 0
                    x_max = min(region_width - 1, data.shape[1] - 1)
                    y_max = min(region_height - 1, data.shape[0] - 1)
                else:
                    # Adjust end points to match the required dimensions
                    x_max = min(x_min + region_width - 1, data.shape[1] - 1)
                    y_max = min(y_min + region_height - 1, data.shape[0] - 1)

            # Check boundaries
            x_min = max(0, min(x_min, data.shape[1] - 1))
            x_max = max(0, min(x_max, data.shape[1] - 1))
            y_min = max(0, min(y_min, data.shape[0] - 1))
            y_max = max(0, min(y_max, data.shape[0] - 1))

            # Extract the region
            data = data[y_min : y_max + 1, x_min : x_max + 1]
            region_origin = (x_min, y_min)

        # Get filename for display
        filename = os.path.basename(file_path)

        # Create figure (all frames of a video share one size)
        dpi = 100
        width, height = options.get("frame_size") or frame_size(options, data.shape)
        fig = Figure(figsize=(width / dpi, height / dpi), dpi=dpi)
        canvas = FigureCanvas(fig)

        # Create axes - use WCS projection if enabled: this frame's own WCS,
        # cut to the region shown
        wcs_enabled = options.get("wcs_enabled", False)
        coord_info = options.get("coord_info", None)
        frame_wcs = None
        if wcs_enabled and coord_info and coord_info.get("wcs") is not None:
            from astropy.wcs import WCS

            try:
                frame_wcs = WCS(header, naxis=2)
            except Exception:
                frame_wcs = coord_info["wcs"]
            x0, y0 = region_origin
            frame_wcs = frame_wcs[y0 : y0 + data.shape[0], x0 : x0 + data.shape[1]]

        # Check if timeline is enabled to use GridSpec layout
        timeline_enabled = options.get("minmax_timeline_enabled", False)
        timeline_ax = None

        if timeline_enabled:
            # Use GridSpec for bottom dock panel layout: 88% image, 12% timeline
            gs = GridSpec(2, 1, height_ratios=[88, 12], hspace=0.25, figure=fig)

            if frame_wcs is not None:
                ax = fig.add_subplot(gs[0], projection=frame_wcs)
            else:
                ax = fig.add_subplot(gs[0])

            # Create timeline axes
            timeline_ax = fig.add_subplot(gs[1])
            # timeline_ax.set_facecolor('#1a1a2e')  # Removed to support dynamic themes
        else:
            # No timeline - use full figure for image
            if frame_wcs is not None:
                ax = create_wcs_axes(fig, frame_wcs)
            else:
                ax = fig.add_subplot(111)

        # Determine min/max values based on range_mode
        range_mode = options.get("range_mode", 1)  # Default to Auto Per Frame

        if range_mode == 0:  # Fixed Range
            vmin = options.get("vmin", np.nanmin(data))
            vmax = options.get("vmax", np.nanmax(data))
        elif range_mode == 1:  # Auto Per Frame
            # Calculate percentiles for this frame
            lower_percentile = options.get("lower_percentile", 0)
            upper_percentile = options.get("upper_percentile", 100)
            vmin = np.nanpercentile(data, lower_percentile)
            vmax = np.nanpercentile(data, upper_percentile)
        else:  # Global Auto
            # Use pre-calculated global stats
            if global_stats:
                vmin, vmax = global_stats
            else:
                # Fallback to Auto Per Frame if global stats not available
                lower_percentile = options.get("lower_percentile", 0)
                upper_percentile = options.get("upper_percentile", 100)
                vmin = np.nanpercentile(data, lower_percentile)
                vmax = np.nanpercentile(data, upper_percentile)

        # Difference images: a range symmetric about zero, so brightening
        # and dimming look alike
        if options.get("difference_files") and range_mode != 0:
            limit = max(abs(vmin), abs(vmax))
            vmin, vmax = -limit, limit

        # Ensure min/max are proper
        if vmin >= vmax:
            vmax = vmin + 1.0

        # Create normalization
        stretch = options.get("stretch", "linear")
        gamma = options.get("gamma", 1.0)
        norm = get_norm(stretch, vmin, vmax, gamma)

        # Display the image
        cmap = options.get("colormap", "viridis")
        if cmap not in plt.colormaps():
            ensure_solar_colormaps()
        img = ax.imshow(
            data,
            cmap=cmap,
            norm=norm,
            origin="lower",
            interpolation="none",
            # aspect="auto",
        )

        # Draw the contour layers
        layers = [l for l in options.get("contour_layers") or [] if l.get("enabled", True)]
        if layers:
            try:
                (frame_contours or FrameContours()).draw(
                    ax,
                    layers,
                    frame_idx,
                    file_path,
                    header,
                    full_data,
                    image_stokes=options.get("stokes", "I"),
                    region_origin=region_origin,
                    legend_options=options.get("contour_options"),
                )
            except Exception as e:
                logger.error(f"Error drawing contours: {e}")

        # Handle axis labels based on WCS mode
        if wcs_enabled and coord_info:
            xlabel, ylabel = get_wcs_axis_labels(coord_info)
            ax.set_xlabel(xlabel, fontsize=12)
            ax.set_ylabel(ylabel, fontsize=12)
            ax.tick_params(labelsize=12)
        else:
            # Turn off axis labels and ticks for pixel mode
            ax.set_xticks([])
            ax.set_yticks([])

        # Add overlays if requested
        overlay_text = []

        # Add timestamp if requested
        if options.get("timestamp", False):
            when = observation_time(header, file_path)
            text = when.strftime("%Y-%m-%d %H:%M:%S") if when else "unknown"
            # Use "Colormap:" prefix in contour mode
            contour_file = _frame_contour_file(options, frame_idx)
            prefix = "Colormap" if contour_file else "Time"
            overlay_text.append(f"{prefix}: {text}")

            # Add the contour image's time when it has its own file
            if contour_file:
                try:
                    c_header = fits.getheader(readable_image_path(contour_file))
                except Exception:
                    c_header = None
                c_when = observation_time(c_header, contour_file)
                overlay_text.append(
                    "Contour: "
                    + (
                        c_when.strftime("%Y-%m-%d %H:%M:%S")
                        if c_when
                        else os.path.basename(contour_file)
                    )
                )

        # Add frame number if requested
        if options.get("frame_number", False):
            frame_num = options.get("current_frame", 0) + 1
            total_frames = options.get("total_frames", 0)
            overlay_text.append(f"Frame: {frame_num}/{total_frames}")

        # Add filename if requested
        if options.get("filename", False):
            # With contours from their own files, name both images
            contour_file = _frame_contour_file(options, frame_idx)
            overlay_text.append(f"{'Colormap' if contour_file else 'File'}: {filename}")
            if contour_file:
                overlay_text.append(f"Contour: {os.path.basename(contour_file)}")

        # Add text overlay
        if overlay_text:
            # ax.text(
            #    0.98,
            #    0.98,
            #    "\n".join(overlay_text),
            #    transform=ax.transAxes,
            #    fontsize=4,
            #    verticalalignment="bottom",
            #    bbox=dict(boxstyle="round", facecolor="white", alpha=0.5),
            #    horizontalalignment="right",
            # )
            ax.set_title("\n".join(overlay_text))

        # Add colorbar if requested
        if options.get("colorbar", False):
            import matplotlib.ticker as mticker

            # Create colorbar with proper sizing that works with GridSpec
            cbar = fig.colorbar(img, ax=ax, shrink=0.9, aspect=30, pad=0.05)

            # Format the colorbar nicely
            cbar.ax.tick_params(labelsize=12)
            formatter = mticker.ScalarFormatter(useMathText=True)
            formatter.set_scientific(True)
            formatter.set_powerlimits((-2, 3))
            cbar.ax.yaxis.set_major_formatter(formatter)
            cbar.ax.yaxis.get_offset_text().set_fontsize(12)

            # Add units if available
            if header and "BUNIT" in header:
                cbar.set_label(header["BUNIT"], fontsize=12)

        # Draw min/max timeline if enabled (using the pre-created timeline_ax from GridSpec)
        timeline = options.get("minmax_timeline", None)
        if (
            timeline
            and options.get("minmax_timeline_enabled", False)
            and timeline_ax is not None
        ):
            current_frame = options.get("current_frame", 0)
            timeline.draw_timeline(fig, current_frame, ax=timeline_ax)

        # Adjust layout and render
        # fig.tight_layout(pad=0.01)
        canvas.draw()

        # Convert to numpy array
        # Convert to numpy array
        img_data = canvas_to_rgb_array(canvas)

        # Clean up
        plt.close(fig)

        return img_data

    except Exception as e:
        logger.error(f"Error processing image {file_path}: {e}")
        return create_error_frame(
            options, f"Error: {str(e)[:30]}", frame_idx, os.path.basename(file_path)
        )


def calculate_global_stats(files, options):
    """Calculate global statistics for all frames (for Global Auto mode)"""
    # Check if we need global stats
    if options.get("range_mode", 1) != 2:  # Not Global Auto
        return None

    # Only log the message if we're actually calculating global stats
    logger.info("Calculating global statistics...")

    all_mins = []
    all_maxs = []

    # Sample frames to calculate global stats (using every 10th frame or at least 10 frames)
    # sample_step = max(1, len(files) // 10)
    sample_step = 1  # Sample every frame
    sample_files = files[::sample_step]

    # Ensure we have at least some files
    if len(sample_files) == 0:
        sample_files = files[:1]

    difference_files = options.get("difference_files")
    for i, file_path in enumerate(tqdm(sample_files, desc="Calculating global stats")):
        try:
            data, _ = load_fits_data(file_path, stokes=options.get("stokes", "I"))

            if data is None:
                continue
            if difference_files:
                other, _ = load_fits_data(difference_files[i], stokes=options.get("stokes", "I"))
                if other is None or other.shape != data.shape:
                    continue
                data = data - other

            # Apply region selection if enabled
            if options.get("region_enabled", False):
                # Get region coordinates
                x_min = options.get("x_min", 0)
                x_max = options.get("x_max", data.shape[1] - 1)
                y_min = options.get("y_min", 0)
                y_max = options.get("y_max", data.shape[0] - 1)

                # Ensure proper order and boundaries
                x_min, x_max = min(x_min, x_max), max(x_min, x_max)
                y_min, y_max = min(y_min, y_max), max(y_min, y_max)
                x_min = max(0, min(x_min, data.shape[1] - 1))
                x_max = max(0, min(x_max, data.shape[1] - 1))
                y_min = max(0, min(y_min, data.shape[0] - 1))
                y_max = max(0, min(y_max, data.shape[0] - 1))

                # Extract the region
                data = data[y_min : y_max + 1, x_min : x_max + 1]

            # Calculate percentiles
            lower_percentile = options.get("lower_percentile", 0)
            upper_percentile = options.get("upper_percentile", 100)
            vmin = np.nanpercentile(data, lower_percentile)
            vmax = np.nanpercentile(data, upper_percentile)

            all_mins.append(vmin)
            all_maxs.append(vmax)

        except Exception as e:
            logger.error(f"Error calculating stats for {file_path}: {e}")

    # Calculate overall min/max from the sampled frames
    if all_mins and all_maxs:
        global_vmin = np.min(all_mins)
        global_vmax = np.max(all_maxs)
        return (global_vmin, global_vmax)

    return None


def make_frame_contours(options):
    """The FrameContours drawing a video's contour layers, or None."""
    return FrameContours() if options.get("contour_layers") else None


# In a worker process: the FrameContours of the video being made, kept
# between frames so a fixed contour image is read (and converted) once
_worker_contours = {"key": None, "contours": None}


def process_image_wrapper(args):
    """
    Wrapper function for process_image to use with multiprocessing.
    Takes a tuple of (file_path, index, options, global_stats) and returns
    (index, processed_frame)
    """
    # Configure matplotlib for non-interactive backend to avoid thread safety issues
    import matplotlib

    matplotlib.use("Agg")  # Use non-interactive backend

    file_path, idx, options, global_stats = args
    try:
        options = dict(options, current_frame=idx)
        key = options.get("video_id")
        if _worker_contours["key"] != key:
            _worker_contours.update(key=key, contours=make_frame_contours(options))
        result = process_image(
            file_path,
            options,
            global_stats,
            frame_contours=_worker_contours["contours"],
            frame_idx=idx,
        )
        return idx, result
    except Exception as e:
        logger.error(f"Error processing frame {idx} ({file_path}): {e}")
        return idx, None


def _frames_in_order(files, options, global_stats, frame_contours):
    """Render the frames, yielding (index, frame) in order as they are ready.

    With multiprocessing, up to two frames per core are rendered ahead, so
    memory stays bounded; closing the generator (e.g. on cancel) stops
    submitting frames and waits only for those already being rendered.
    """
    num_cores = options.get("cpu_count", max(1, cpu_count() - 1))
    if not (options.get("use_multiprocessing", False) and num_cores > 1 and len(files) > 1):
        for i, file_path in enumerate(files):
            frame = process_image(
                file_path,
                dict(options, current_frame=i),
                global_stats,
                frame_contours=frame_contours,
                frame_idx=i,
            )
            yield i, frame
        return

    # Worker processes make their own FrameContours (see process_image_wrapper)
    worker_options = dict(options)
    worker_options["video_id"] = f"{os.getpid()}-{time.time()}"
    logger.info(f"Rendering frames with {num_cores} processes")
    executor = process_pool_executor(
        num_cores, preload=["solar_radio_image_viewer.video.creator"]
    )
    pending = {}
    try:
        next_to_submit = 0
        for i in range(len(files)):
            while next_to_submit < len(files) and next_to_submit < i + 2 * num_cores:
                pending[next_to_submit] = executor.submit(
                    process_image_wrapper,
                    (files[next_to_submit], next_to_submit, worker_options, global_stats),
                )
                next_to_submit += 1
            _, frame = pending.pop(i).result()
            yield i, frame
    finally:
        for future in pending.values():
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)


def file_times(paths):
    """Observation time (datetime or None) of each file."""
    from ..processing.image_meta import fits_header

    times = []
    for path in paths:
        try:
            times.append(observation_time(fits_header(path), path))
        except Exception:
            times.append(observation_time(None, path))
    return times


def _nearest_files(times, series_files, series_times):
    """For each of ``times``, the file of ``series_files`` nearest in time,
    and the gap in seconds (None when times are unknown: then by order)."""
    known = sorted(
        ((t.timestamp(), f) for t, f in zip(series_times, series_files) if t is not None),
        key=lambda item: item[0],
    )
    stamps = np.array([t for t, _ in known])
    files, gaps = [], []
    for i, when in enumerate(times):
        if when is None or not known:
            files.append(series_files[min(i, len(series_files) - 1)])
            gaps.append(None)
            continue
        t = when.timestamp()
        j = int(np.searchsorted(stamps, t))
        # The nearer of the neighbours either side (the earlier one on a tie)
        candidates = [k for k in (j - 1, j) if 0 <= k < len(known)]
        k = min(candidates, key=lambda k: abs(stamps[k] - t))
        files.append(known[k][1])
        gaps.append(abs(stamps[k] - t))
    return files, gaps


def frame_plan(image_files, image_is_series, layers, start=None, end=None, every=1, excluded=()):
    """Which image and contour files make each frame of a video.

    Frames follow the image series; with a fixed image, the first contour
    layer that is a series (``layer["series_files"]``), or one frame.
    ``start``/``end`` (datetimes) and ``every`` select frames, and frames
    whose driving file is in ``excluded`` are left out. Each series layer
    gets, for every frame, its file nearest in observation time.

    Returns a dict with ``image_files``, ``times``, ``driver_files`` (the
    files frames follow), ``layers`` (copies, ``frame_files`` filled in for
    series layers) and ``gaps`` (per series layer id: seconds between each
    frame and its contour file).
    """
    series_layers = [
        layer for layer in layers
        if layer.get("enabled", True) and layer.get("source") == "series" and layer.get("series_files")
    ]
    if image_is_series:
        driver = list(image_files)
    elif series_layers:
        driver = list(series_layers[0]["series_files"])
    else:
        driver = list(image_files[:1])
    times = file_times(driver)

    keep = []
    for i, (path, when) in enumerate(zip(driver, times)):
        if path in excluded:
            continue
        if when is not None and ((start and when < start) or (end and when > end)):
            continue
        keep.append(i)
    keep = keep[:: max(1, int(every))]
    driver = [driver[i] for i in keep]
    times = [times[i] for i in keep]
    if image_is_series:
        image = driver
    else:
        image = [image_files[0]] * len(driver) if image_files else []

    driver_layer = series_layers[0] if not image_is_series and series_layers else None
    planned, gaps = [], {}
    for layer in layers:
        layer = dict(layer)
        if layer.get("source") == "series" and layer.get("series_files"):
            if driver_layer is not None and layer.get("id") == driver_layer.get("id"):
                # The layer frames follow
                layer["frame_files"] = list(driver)
                gaps[layer.get("id")] = [0.0 if t is not None else None for t in times]
            else:
                layer["frame_files"], gaps[layer.get("id")] = _nearest_files(
                    times, layer["series_files"], file_times(layer["series_files"])
                )
        planned.append(layer)
    return {
        "image_files": image,
        "times": times,
        "driver_files": driver,
        "layers": planned,
        "gaps": gaps,
    }


def _layers_from_contour_mode(options):
    """Turn the options of the former contour modes (contour_video_enabled,
    contour_mode A/B/C with contour_files / fixed_contour_file) into one
    contour layer, for callers that still use them."""
    if not options.get("contour_video_enabled") or options.get("contour_layers"):
        return
    from ..processing.contour_layers import new_contour_layer

    width = options.get("linewidth", 1.0)
    layer = new_contour_layer(
        0,
        label="Contours",
        level_type=options.get("level_type", "fraction"),
        pos_levels=options.get("pos_levels", [0.1, 0.3, 0.5, 0.7, 0.9]),
        neg_levels=options.get("neg_levels", [0.1, 0.3, 0.5, 0.7, 0.9]),
        pos_color=options.get("pos_color", "white"),
        neg_color=options.get("neg_color", "cyan"),
        pos_linewidth=width,
        neg_linewidth=width,
        stokes=options.get("stokes", "I"),
    )
    if options.get("contour_mode", 0) == 1:  # B: one fixed contour file
        layer.update(source="external", external_image=options.get("fixed_contour_file", ""))
    else:  # A, C: one contour file per frame
        layer.update(source="series", frame_files=list(options.get("contour_files", [])))
    options["contour_layers"] = [layer]
    options.setdefault("contour_options", {"show_legend": False})


# Quality presets: x264/VP9 constant rate factor (lower is better)
QUALITY_CRF = {"Low": 30, "Medium": 24, "High": 19, "Lossless": 0}


def _crf(quality):
    """CRF for a quality preset name, or for the former 1-10 scale."""
    if isinstance(quality, str):
        return QUALITY_CRF.get(quality, QUALITY_CRF["High"])
    return int(round(34 - 16 * (min(max(quality, 1), 10) - 1) / 9))  # 1 -> 34, 10 -> 18


def video_writer(output_file, options):
    """A function returning the context manager that writes the frames,
    chosen by the file extension: .mp4/.avi (H.264), .webm (VP9), .gif, or
    .png (frames as name_0001.png, name_0002.png, ...)."""
    fps = options.get("fps", 15)
    ext = os.path.splitext(output_file)[1].lower()
    crf = _crf(options.get("quality", "High"))
    if ext == ".png":
        return lambda: _PngFrames(output_file)
    if ext == ".gif":
        # imageio's Pillow GIF writer takes the frame time in ms (since 2.28;
        # the older GIF-PIL writer took fps)
        major, minor = (int(part) for part in imageio.__version__.split(".")[:2])
        if (major, minor) >= (2, 28):
            return lambda: imageio.get_writer(
                output_file, format="GIF", duration=1000 / fps, loop=0
            )
        return lambda: imageio.get_writer(output_file, format="GIF", fps=fps, subrectangles=True)
    if ext == ".webm":
        params = ["-crf", str(crf), "-b:v", "0"] + (["-lossless", "1"] if crf == 0 else [])
        return lambda: imageio.get_writer(
            output_file, format="FFMPEG", fps=fps, codec="libvpx-vp9", quality=None,
            pixelformat="yuv420p", macro_block_size=1, output_params=params,
        )
    if ext not in (".mp4", ".avi"):
        logger.warning(f"Unrecognized extension {ext}, using MP4 settings")
    # Lossless keeps full colour (yuv444p), which not every player can show
    return lambda: imageio.get_writer(
        output_file, format="FFMPEG", fps=fps, codec="libx264", quality=None,
        pixelformat="yuv444p" if crf == 0 else "yuv420p", macro_block_size=1,
        output_params=["-crf", str(crf)],
    )


class _PngFrames:
    """Writes each frame as a numbered PNG next to ``name.png``."""

    def __init__(self, output_file):
        self._stem = os.path.splitext(output_file)[0]
        self.written = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def append_data(self, frame):
        path = f"{self._stem}_{len(self.written) + 1:04d}.png"
        imageio.imwrite(path, frame)
        self.written.append(path)


def apply_difference(files, options):
    """(files, options) for a difference movie (options["difference"]:
    "running" subtracts the previous image, so the video starts at the
    second; "base" subtracts the first). Records the image subtracted from
    each frame in options["difference_files"]; series contour layers are
    shifted along with the frames."""
    mode = options.get("difference", "none")
    if "difference_files" in options or mode not in ("running", "base") or not files:
        return files, options
    options = dict(options)
    if mode == "base":
        options["difference_files"] = [files[0]] * len(files)
        return files, options
    if len(files) < 2:
        return files, options
    options["difference_files"] = list(files[:-1])
    options["contour_layers"] = [
        dict(layer, frame_files=layer["frame_files"][1:]) if "frame_files" in layer else layer
        for layer in options.get("contour_layers") or []
    ]
    return list(files[1:]), options


def _rotation_shift(options, header):
    """(dx, dy) pixels by which a region following solar rotation has moved
    in the image with ``header`` since the reference image."""
    reference = options.get("track_reference")
    if not reference or header is None:
        return 0, 0
    try:
        import warnings

        from astropy.wcs import WCS
        from sunpy.physics.differential_rotation import solar_rotate_coordinate

        from ..processing.image_meta import fits_header

        cx, cy = reference["center"]
        start = WCS(fits_header(reference["file"]), naxis=2).pixel_to_world(cx, cy)
        wcs = WCS(header, naxis=2)
        frame = wcs.pixel_to_world(cx, cy).frame
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            # The observer carries its time; without one, rotate to the time
            if frame.observer is not None:
                moved = solar_rotate_coordinate(start, observer=frame.observer)
            else:
                moved = solar_rotate_coordinate(start, time=frame.obstime)
        x, y = wcs.world_to_pixel(moved)
        if not (np.isfinite(x) and np.isfinite(y)):
            return 0, 0  # off the disk: no rotation to follow
        return int(round(float(x) - cx)), int(round(float(y) - cy))
    except Exception as e:
        logger.warning(f"Could not follow solar rotation: {e}")
        return 0, 0


def prepare_video_options(files, options):
    """Fill in ``options`` with what all frames of a video share: the frame
    count, the region (with even dimensions), the frame size, the coordinate
    system and the min/max timeline. Used for the video and its preview, so
    the preview frame is the video frame. Returns ``options``.
    """
    _layers_from_contour_mode(options)

    # Calculate total frames
    total_frames = len(files)
    options["total_frames"] = total_frames

    # If region selection is enabled, pre-scan the first file to determine dimensions
    if options.get("region_enabled", False):
        logger.info("Determining region dimensions...")

        # Load the first file
        first_data, _ = load_fits_data(files[0], stokes=options.get("stokes", "I"))

        if first_data is not None:
            # Get region coordinates
            x_min = options.get("x_min", 0)
            x_max = options.get("x_max", first_data.shape[1] - 1)
            y_min = options.get("y_min", 0)
            y_max = options.get("y_max", first_data.shape[0] - 1)

            # Ensure proper order and boundaries
            x_min, x_max = min(x_min, x_max), max(x_min, x_max)
            y_min, y_max = min(y_min, y_max), max(y_min, y_max)
            x_min = max(0, min(x_min, first_data.shape[1] - 1))
            x_max = max(0, min(x_max, first_data.shape[1] - 1))
            y_min = max(0, min(y_min, first_data.shape[0] - 1))
            y_max = max(0, min(y_max, first_data.shape[0] - 1))

            # Calculate region dimensions
            region_width = x_max - x_min + 1
            region_height = y_max - y_min + 1

            # Ensure dimensions are even (required for H.264 codec)
            if region_width % 2 != 0:
                # If width is odd, increase by 1 if possible
                if x_max < first_data.shape[1] - 1:
                    x_max += 1
                    region_width += 1
                elif x_min > 0:
                    x_min -= 1
                    region_width += 1

            if region_height % 2 != 0:
                # If height is odd, increase by 1 if possible
                if y_max < first_data.shape[0] - 1:
                    y_max += 1
                    region_height += 1
                elif y_min > 0:
                    y_min -= 1
                    region_height += 1

            # Update the region coordinates in options
            options["x_min"] = x_min
            options["x_max"] = x_max
            options["y_min"] = y_min
            options["y_max"] = y_max

            # Store dimensions for consistent region extraction
            options["region_width"] = region_width
            options["region_height"] = region_height

            logger.info(
                f"Region dimensions: {region_width}x{region_height} (adjusted to ensure even dimensions)"
            )
        else:
            logger.warning("Could not determine region dimensions from first file")

    # A region following solar rotation: its centre in the first image
    options.pop("track_reference", None)
    if options.get("track_rotation") and options.get("region_enabled") and files:
        from ..processing.image_meta import fits_header
        from .utils import coordinate_type

        if coordinate_type(fits_header(files[0])) == "hpc":
            options["track_reference"] = {
                "file": files[0],
                "center": (
                    (options.get("x_min", 0) + options.get("x_max", 0)) / 2,
                    (options.get("y_min", 0) + options.get("y_max", 0)) / 2,
                ),
            }
        else:
            logger.warning("Following solar rotation needs helioprojective images")

    # One frame size for the whole video, from the first image (or region)
    if "frame_size" not in options:
        first_data, _ = load_fits_data(files[0], stokes=options.get("stokes", "I"))
        shape = first_data.shape if first_data is not None else (1, 1)
        if options.get("region_enabled", False) and "region_width" in options:
            shape = (options["region_height"], options["region_width"])
        options["frame_size"] = frame_size(options, shape)
        logger.info(f"Frame size: {options['frame_size'][0]}x{options['frame_size'][1]}")

    # Initialize WCS coordinate detection if enabled
    if options.get("wcs_enabled", False):
        logger.info("Detecting coordinate system from first file...")
        coord_info = detect_coordinate_system(files[0])
        options["coord_info"] = coord_info
        logger.info(f"Coordinate system: {coord_info.get('type', 'unknown')}")

    # Initialize MinMax timeline if enabled (unless one was given)
    if options.get("minmax_timeline_enabled", False) and "minmax_timeline" not in options:
        logger.info("Precomputing min/max statistics for timeline...")
        position_map = {
            0: "bottom-left",
            1: "bottom-right",
            2: "top-left",
            3: "top-right",
        }
        position = position_map.get(
            options.get("timeline_position", 0), "bottom-left"
        )

        # Determine which files to use for timeline stats
        timeline_source = options.get(
            "timeline_source", 0
        )  # 0=Colormap, 1=Contours
        timeline_files = files  # Default to colormap files

        # In contour mode with source=1, use contour files instead
        if timeline_source == 1:
            contour_files = [_frame_contour_file(options, i) for i in range(len(files))]
            if all(contour_files):
                timeline_files = contour_files
                logger.info(
                    f"Timeline will use contour files ({len(timeline_files)} files)"
                )

        log_scale = options.get("timeline_log_scale", False)
        timeline = MinMaxTimeline(
            total_frames=len(files), position=position, log_scale=log_scale
        )
        timeline.precompute_stats(timeline_files, options, load_fits_data)
        options["minmax_timeline"] = timeline
        logger.info("Timeline statistics computed")

    return options


def create_video(files, output_file, options, progress_callback=None):
    """
    Create a video from a list of FITS files.

    Parameters
    ----------
    files : list
        List of file paths to FITS files.
    output_file : str
        Output file path for the video.
    options : dict
        Dictionary of options:
        - stokes : Stokes parameter to use (I, Q, U, V)
        - colormap : Matplotlib colormap name
        - stretch : Normalization stretch (linear, log, sqrt, power)
        - gamma : Gamma value for power stretch
        - range_mode : Range scaling mode (0:Fixed, 1:Auto Per Frame, 2:Global Auto)
        - vmin, vmax : Fixed min/max values (for Fixed Range mode)
        - lower_percentile, upper_percentile : Percentile values for auto scaling
        - region_enabled : Whether to use region selection
        - x_min, x_max, y_min, y_max : Region selection coordinates
        - timestamp, frame_number, filename : Whether to show these overlays
        - fps : Frames per second for the video
        - quality : Video quality (0-10)
        - width, height : Output frame size (0 for original size)
        - colorbar : Whether to show a colorbar
        - use_multiprocessing : Whether to use multiprocessing for frame generation
        - cpu_count : Number of CPU cores to use (default: number of cores - 1)
    progress_callback : callable, optional
        Callback function for progress updates. Takes two parameters:
        - current_frame : int, Current frame number
        - total_frames : int, Total number of frames
        Returns:
        - continue_processing : bool, Whether to continue processing

    Returns
    -------
    output_file : str or None
        Path to the created video file, or None if progress_callback
        cancelled it (no file is left behind).
    """
    start_time = time.time()
    try:
        # Call progress callback once at the beginning to indicate initialization
        if progress_callback:
            progress_callback(0, 1)  # This will trigger the UI to show initial progress

        if not files:
            raise ValueError("No input files provided")

        if not output_file:
            raise ValueError("No output file specified")

        # Ensure output directory exists
        output_dir = os.path.dirname(output_file)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)

        # Ensure output file has extension
        if not os.path.splitext(output_file)[1]:
            output_file += ".mp4"  # Default to MP4 if no extension
        writer_factory = video_writer(output_file, options)


        files, options = apply_difference(files, options)
        options = prepare_video_options(files, options)
        total_frames = options["total_frames"]
        logger.info(f"Creating video from {total_frames} files...")

        # Contour drawing (worker processes make their own)
        frame_contours = make_frame_contours(options)

        # Calculate global stats if needed
        global_stats = calculate_global_stats(files, options)

        # Render and write the frames in order, one at a time
        render_start = time.time()
        written = 0
        frames = _frames_in_order(files, options, global_stats, frame_contours)
        cancelled = False
        try:
            with writer_factory() as writer:
                for i, frame in frames:
                    if frame is not None:
                        writer.append_data(ensure_even_dimensions(frame))
                        written += 1
                    else:
                        logger.warning(f"Could not process frame {i} ({files[i]})")
                    if progress_callback and not progress_callback(i + 1, total_frames):
                        cancelled = True
                        break
        finally:
            frames.close()

        if cancelled:
            logger.info("Video creation cancelled by user")
            for path in getattr(writer, "written", [output_file]):
                try:
                    os.remove(path)
                except OSError:
                    pass
            return None

        render_time = time.time() - render_start
        logger.info(
            f"Rendered {written} of {total_frames} frames in {render_time:.2f}s "
            f"({total_frames / render_time if render_time > 0 else 0:.2f} frames/sec)"
        )

        total_time = time.time() - start_time
        logger.info(f"Video created successfully: {output_file} in {total_time:.2f}s")
        return output_file

    except Exception as e:
        logger.error(f"Error creating video: {e}")
        raise


class VideoProgress:
    """
    Class to manage progress updates for the video creation process
    """

    def __init__(self, callback=None):
        self.callback = callback
        self.progress = 0
        self._cancel = False
        self.thread = None

    def update(self, progress):
        self.progress = progress
        if self.callback:
            self.callback(progress)

    def cancel(self):
        self._cancel = True

    def is_cancelled(self):
        return self._cancel

    def start_thread(self, func, *args, **kwargs):
        def run():
            func(*args, **kwargs)
            if self.callback:
                self.callback(100)  # Ensure we end at 100%

        self.thread = threading.Thread(target=run)
        self.thread.daemon = True
        self.thread.start()

        return self.thread


def main(argv=None):
    """Command line:
    python -m solar_radio_image_viewer.video.creator OUTPUT FILE.fits [FILE.fits ...]
    python -m solar_radio_image_viewer.video.creator --preset SETTINGS.json OUTPUT
    (a preset saved from the Create Video window makes the same video)
    """
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="python -m solar_radio_image_viewer.video.creator",
        description="Make a video from FITS images.",
    )
    parser.add_argument("--preset", help="settings saved from the Create Video window")
    parser.add_argument("output", help="video file (.mp4, .webm, .gif, .avi or .png frames)")
    parser.add_argument("files", nargs="*", help="FITS images, one per frame")
    args = parser.parse_args(argv)

    if args.preset:
        with open(args.preset) as f:
            video = json.load(f).get("video")
        if not video:
            parser.error("the preset has no frames (its settings had a problem when saved)")
        files, options = video["files"], video["options"]
        if args.files:
            parser.error("give either --preset or files, not both")
    elif args.files:
        files = sorted(args.files)
        options = {"width": 0, "height": 0, "fps": 10, "timestamp": True, "colorbar": True}
    else:
        parser.error("give FITS files or --preset")
    return 0 if create_video(files, args.output, options) else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())

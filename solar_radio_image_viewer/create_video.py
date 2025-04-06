#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import glob
import re
import numpy as np
from datetime import datetime
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize, LogNorm, PowerNorm
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas
import matplotlib.dates as mdates
from astropy.io import fits
import imageio
from tqdm import tqdm
from pathlib import Path
import threading
import time
from multiprocessing import Pool, cpu_count
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import concurrent.futures  # Add this import for as_completed
from astropy.visualization import (
    ImageNormalize,
    LinearStretch,
    LogStretch,
    SqrtStretch,
    PowerStretch,
)
import warnings
from functools import partial
import logging

# Import custom norms
from .norms import (
    SqrtNorm,
    AsinhNorm,
    PowerNorm as CustomPowerNorm,
    ZScaleNorm,
    HistEqNorm,
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


def natural_sort_key(s):
    """
    Sort strings containing numbers naturally (e.g., 'file1', 'file2', 'file10')
    """
    return [
        int(text) if text.isdigit() else text.lower() for text in re.split(r"(\d+)", s)
    ]


def extract_datetime(filepath):
    """
    Try to extract datetime from filename or FITS header
    Returns a datetime object if successful, None otherwise
    """
    try:
        # First try to get from FITS header
        with fits.open(filepath) as hdul:
            header = hdul[0].header
            if "DATE-OBS" in header:
                try:
                    # Try different date formats
                    formats = [
                        "%Y-%m-%dT%H:%M:%S.%f",
                        "%Y-%m-%dT%H:%M:%S",
                        "%Y-%m-%d %H:%M:%S.%f",
                        "%Y-%m-%d %H:%M:%S",
                        "%Y/%m/%d %H:%M:%S",
                        "%Y-%m-%d",
                    ]

                    date_str = header["DATE-OBS"]
                    for fmt in formats:
                        try:
                            return datetime.strptime(date_str, fmt)
                        except ValueError:
                            continue
                except Exception:
                    pass
    except Exception:
        pass

    # Fall back to filename pattern matching
    filename = os.path.basename(filepath)
    patterns = [
        r"(\d{4}[-_]\d{2}[-_]\d{2}[-_T]\d{2}[-_]\d{2}[-_]\d{2})",  # 2023-01-01-12-30-00
        r"(\d{4}[-_]\d{2}[-_]\d{2})",  # 2023-01-01
        r"(\d{8}[-_]\d{6})",  # 20230101_123000
        r"(\d{8})",  # 20230101
    ]

    for pattern in patterns:
        match = re.search(pattern, filename)
        if match:
            date_str = match.group(1)
            formats = [
                "%Y-%m-%d-%H-%M-%S",
                "%Y_%m_%d_%H_%M_%S",
                "%Y-%m-%dT%H-%M-%S",
                "%Y-%m-%d",
                "%Y_%m_%d",
                "%Y%m%d_%H%M%S",
                "%Y%m%d",
            ]

            for fmt in formats:
                try:
                    return datetime.strptime(date_str, fmt)
                except ValueError:
                    continue

    return None


def load_fits_data(filepath, stokes="I"):
    """
    Load data from a FITS file
    """
    try:
        with fits.open(filepath) as hdul:
            header = hdul[0].header
            data = hdul[0].data

            # Handle data dimensionality
            if data.ndim == 4:  # [stokes, freq, y, x]
                # Find the Stokes index
                stokes_index = 0  # default to first index
                if stokes == "I" or stokes == 0:
                    stokes_index = 0
                elif stokes == "Q" or stokes == 1:
                    stokes_index = 1
                elif stokes == "U" or stokes == 2:
                    stokes_index = 2
                elif stokes == "V" or stokes == 3:
                    stokes_index = 3

                # Use first frequency channel
                data = data[stokes_index, 0, :, :]
            elif data.ndim == 3:  # [freq, y, x] or [stokes, y, x]
                # Assume [stokes, y, x] format
                stokes_index = 0  # default to first index
                if stokes == "I" or stokes == 0:
                    stokes_index = 0
                elif stokes == "Q" or stokes == 1:
                    stokes_index = 1
                elif stokes == "U" or stokes == 2:
                    stokes_index = 2
                elif stokes == "V" or stokes == 3:
                    stokes_index = 3

                data = data[stokes_index, :, :]

            return data, header
    except Exception as e:
        logger.error(f"Error loading {filepath}: {e}")
        return None, None


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


def format_timestamp(dt_str):
    """Format timestamp in a consistent way similar to the main application"""
    try:
        # Try different date formats
        formats = [
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y/%m/%d %H:%M:%S",
            "%Y-%m-%d",
        ]

        for fmt in formats:
            try:
                dt = datetime.strptime(dt_str, fmt)
                # Format like "2023-01-01 12:30:45 UTC"
                return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
            except ValueError:
                continue
    except:
        pass

    # Return original if parsing fails
    return dt_str


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


def process_image(file_path, options, global_stats=None):
    """Process a FITS image and return a frame for the video"""
    try:
        # Load FITS data
        data, header = load_fits_data(file_path, stokes=options.get("stokes", "I"))

        if data is None:
            logger.warning(f"Could not load data from {file_path}")
            return None

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

            # Update the region dimensions in options for reference
            options["actual_region_width"] = data.shape[1]
            options["actual_region_height"] = data.shape[0]

        # Get filename for display
        filename = os.path.basename(file_path)

        # Create figure
        dpi = 100

        # Determine figure size based on data dimensions and any resize options
        if options.get("width", 0) > 0 and options.get("height", 0) > 0:
            # Use specified dimensions
            figsize = (options["width"] / dpi, options["height"] / dpi)
        else:
            # Use data dimensions
            # figsize = (data.shape[1] / dpi + 1, data.shape[0] / dpi)
            figsize = (10, 10)

        fig = Figure(figsize=figsize, dpi=dpi)
        canvas = FigureCanvas(fig)
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

        # Ensure min/max are proper
        if vmin >= vmax:
            vmax = vmin + 1.0

        # Create normalization
        stretch = options.get("stretch", "linear")
        gamma = options.get("gamma", 1.0)
        norm = get_norm(stretch, vmin, vmax, gamma)

        # Display the image
        cmap = options.get("colormap", "viridis")
        img = ax.imshow(
            data,
            cmap=cmap,
            norm=norm,
            origin="lower",
            interpolation="none",
            # aspect="auto",
        )

        # Turn off axis labels and ticks
        ax.set_xticks([])
        ax.set_yticks([])

        # Add overlays if requested
        overlay_text = []

        # Add timestamp if requested
        if options.get("timestamp", False) and header:
            # Extract date from header if available
            timestamp = None
            for key in ["DATE-OBS", "DATE_OBS", "DATE"]:
                if key in header:
                    try:
                        date_str = header[key]
                        # Parse the date string
                        if "T" in date_str:
                            timestamp = datetime.strptime(
                                date_str, "%Y-%m-%dT%H:%M:%S.%f"
                            )
                        else:
                            timestamp = datetime.strptime(
                                date_str, "%Y-%m-%d %H:%M:%S.%f"
                            )
                        break
                    except (ValueError, TypeError):
                        try:
                            # Try alternative format
                            timestamp = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                            break
                        except (ValueError, TypeError):
                            pass

            if timestamp:
                overlay_text.append(f"Time: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
            else:
                # Use file modification time as fallback
                mtime = os.path.getmtime(file_path)
                overlay_text.append(
                    f"Time: {datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')}"
                )

        # Add frame number if requested
        if options.get("frame_number", False):
            frame_num = options.get("current_frame", 0) + 1
            total_frames = options.get("total_frames", 0)
            overlay_text.append(f"Frame: {frame_num}/{total_frames}")

        # Add filename if requested
        if options.get("filename", False):
            overlay_text.append(f"File: {filename}")

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
            cbar = fig.colorbar(img, ax=ax, pad=0.01, fraction=0.05)
            import matplotlib.ticker as mticker

            formatter = mticker.ScalarFormatter(useMathText=True)
            formatter.set_scientific(True)
            formatter.set_powerlimits((-1, 1))
            # cbar.ax.tick_params(labelsize=12)
            cbar.ax.yaxis.set_major_formatter(formatter)
            # if header and "BUNIT" in header:
            #    cbar.set_label(header["BUNIT"])

        # Adjust layout and render
        # fig.tight_layout(pad=0.01)
        canvas.draw()

        # Convert to numpy array
        try:
            img_data = np.frombuffer(canvas.tostring_rgb(), dtype=np.uint8)
            img_data = img_data.reshape(canvas.get_width_height()[::-1] + (3,))
        except AttributeError:
            # Compatibility issue with newer versions of Matplotlib
            img_data = np.frombuffer(canvas.buffer_rgba(), dtype=np.uint8)
            img_data = img_data.reshape(canvas.get_width_height()[::-1] + (4,))
            # Convert RGBA to RGB by discarding the alpha channel
            img_data = img_data[:, :, :3]

        # Clean up
        plt.close(fig)

        return img_data

    except Exception as e:
        logger.error(f"Error processing image {file_path}: {e}")
        return None


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
    sample_step = max(1, len(files) // 10)
    sample_files = files[::sample_step]

    # Ensure we have at least some files
    if len(sample_files) == 0:
        sample_files = files[:1]

    for file_path in tqdm(sample_files, desc="Calculating global stats"):
        try:
            data, _ = load_fits_data(file_path, stokes=options.get("stokes", "I"))

            if data is None:
                continue

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


def process_image_wrapper(args):
    """
    Wrapper function for process_image to use with multiprocessing.
    Takes a tuple of (file_path, index) and returns (index, processed_frame)
    """
    # Configure matplotlib for non-interactive backend to avoid thread safety issues
    import matplotlib

    matplotlib.use("Agg")  # Use non-interactive backend

    file_path, idx, options, global_stats = args
    try:
        # Update current frame index in options
        options_copy = options.copy()  # Make a copy to avoid thread safety issues
        options_copy["current_frame"] = idx

        # Process the frame
        result = process_image(file_path, options_copy, global_stats)
        return idx, result
    except Exception as e:
        logger.error(f"Error processing frame {idx} ({file_path}): {e}")
        return idx, None


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
    output_file : str
        Path to the created video file.
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

        # Handle case-insensitive file extensions
        output_ext = os.path.splitext(output_file)[1].lower()

        # Determine the video writer based on the file extension
        if output_ext == ".mp4":
            # MP4 format with H.264 codec
            writer_kwargs = {
                "format": "FFMPEG",
                "fps": options.get("fps", 15),
                "codec": "libx264",
                "quality": None,
                "bitrate": str(options.get("quality", 8) * 100000),
                "pixelformat": "yuv420p",
                "macro_block_size": 1,  # Important: Use 1 to prevent resizing issues
            }
        elif output_ext == ".gif":
            # GIF format
            writer_kwargs = {
                "format": "GIF",
                "fps": options.get("fps", 15),
                "subrectangles": True,
            }
        elif output_ext == ".avi":
            # AVI format
            writer_kwargs = {
                "format": "FFMPEG",
                "fps": options.get("fps", 15),
                "codec": "libx264",
                "pixelformat": "yuv420p",
                "quality": None,
                "bitrate": str(options.get("quality", 8) * 100000),
                "macro_block_size": 1,  # Important: Use 1 to prevent resizing issues
            }
        else:
            # Default to MP4 for other extensions
            logger.warning(f"Unrecognized extension {output_ext}, using MP4 settings")
            writer_kwargs = {
                "format": "FFMPEG",
                "fps": options.get("fps", 15),
                "codec": "libx264",
                "quality": None,
                "bitrate": str(options.get("quality", 8) * 100000),
                "pixelformat": "yuv420p",
                "macro_block_size": 1,  # Important: Use 1 to prevent resizing issues
            }

        # Add fps and quality info to the options for reference
        options["fps"] = writer_kwargs.get("fps", 15)

        # Calculate total frames
        total_frames = len(files)
        options["total_frames"] = total_frames

        logger.info(f"Creating video from {total_frames} files...")

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

        # Calculate global stats if needed
        global_stats = calculate_global_stats(files, options)

        # Determine whether to use multiprocessing
        use_multiprocessing = options.get("use_multiprocessing", False)
        num_cores = options.get("cpu_count", max(1, cpu_count() - 1))
        # Use a larger chunk size for fewer, larger batches
        chunk_size = options.get(
            "chunk_size", max(1, min(50, len(files) // (num_cores * 2)))
        )

        if use_multiprocessing and num_cores > 1 and len(files) > num_cores:
            logger.info(
                f"Using multiprocessing with {num_cores} cores and chunk size {chunk_size}"
            )
            print(
                f"Multiprocessing enabled: {num_cores} cores, {chunk_size} frames per batch"
            )

            # Process frames in parallel
            processed_frames = [None] * len(files)
            processed_count = 0
            failed_count = 0

            # Prepare arguments for each frame
            process_args = [
                (file_path, i, options, global_stats)
                for i, file_path in enumerate(files)
            ]

            # Use ProcessPoolExecutor for parallel processing in batches
            with ProcessPoolExecutor(max_workers=num_cores) as executor:
                # Process in chunks to reduce overhead
                batch_start_time = time.time()
                results = list(
                    tqdm(
                        executor.map(
                            process_image_wrapper, process_args, chunksize=chunk_size
                        ),
                        total=len(files),
                        desc="Processing frames",
                        unit="frame",
                    )
                )
                batch_end_time = time.time()
                batch_duration = batch_end_time - batch_start_time
                frames_per_second = (
                    len(files) / batch_duration if batch_duration > 0 else 0
                )

                logger.info(
                    f"Parallel processing complete: {len(results)} frames in {batch_duration:.2f}s ({frames_per_second:.2f} frames/sec)"
                )
                print(
                    f"Processed {len(results)} frames in {batch_duration:.2f}s ({frames_per_second:.2f} frames/sec)"
                )

                # Store results in the processed_frames list
                for idx, frame in results:
                    processed_frames[idx] = frame
                    if frame is not None:
                        processed_count += 1
                    else:
                        failed_count += 1

                    # Call progress callback
                    if (
                        progress_callback and idx % 5 == 0
                    ):  # Update every 5 frames to reduce overhead
                        continue_processing = progress_callback(idx + 1, len(files))
                        if not continue_processing:
                            logger.info("Video creation cancelled by user")
                            return None

            logger.info(f"Processed {processed_count} frames ({failed_count} failed)")

            # Create video writer
            with imageio.get_writer(output_file, **writer_kwargs) as writer:
                # Add frames to video in order
                for i, frame in enumerate(processed_frames):
                    if frame is not None:
                        # Ensure image has even dimensions
                        frame = ensure_even_dimensions(frame)
                        # Add the frame to the video
                        writer.append_data(frame)
                    else:
                        logger.warning(f"Could not process frame {i}")

        else:
            if use_multiprocessing:
                if num_cores <= 1:
                    logger.info("Multiprocessing disabled: only 1 core available")
                elif len(files) <= num_cores:
                    logger.info(
                        "Multiprocessing disabled: too few files for parallel processing"
                    )
                print("Using sequential processing")

            # Sequential processing (original method)
            seq_start_time = time.time()
            # Create video writer
            with imageio.get_writer(output_file, **writer_kwargs) as writer:
                # Process each frame
                for i, file_path in enumerate(files):
                    # Call progress callback if provided
                    if progress_callback:
                        continue_processing = progress_callback(i, total_frames)
                        if not continue_processing:
                            logger.info("Video creation cancelled by user")
                            break

                    # Update current frame index in options
                    options["current_frame"] = i

                    # Process the image
                    frame = process_image(file_path, options, global_stats)

                    if frame is not None:
                        # Ensure image has even dimensions
                        frame = ensure_even_dimensions(frame)
                        # Add the frame to the video
                        writer.append_data(frame)
                    else:
                        logger.warning(f"Could not process frame {i} ({file_path})")

            seq_end_time = time.time()
            seq_duration = seq_end_time - seq_start_time
            frames_per_second = len(files) / seq_duration if seq_duration > 0 else 0
            logger.info(
                f"Sequential processing complete: {len(files)} frames in {seq_duration:.2f}s ({frames_per_second:.2f} frames/sec)"
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


if __name__ == "__main__":
    # Example usage
    create_video(
        input_pattern="/path/to/*.fits",
        output_file="/path/to/output.mp4",
        fps=10,
        sort_by="datetime",
        stokes="I",
        colormap="viridis",
        stretch="linear",
        vmin=None,
        vmax=None,
        gamma=1.0,
        width=None,
        height=None,
        add_timestamp=True,
        add_frame_number=True,
        add_filename=True,
        use_multiprocessing=True,
        region_enabled=False,
        x_min=0,
        x_max=0,
        y_min=0,
        y_max=0,
        add_colorbar=False,
        range_mode=1,  # 0=Fixed, 1=Auto Per Frame, 2=Global Auto
        lower_percentile=0.0,
        upper_percentile=100.0,
    )

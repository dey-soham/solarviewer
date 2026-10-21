#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import re
import numpy as np
from datetime import datetime
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize
from matplotlib import rcParams
from matplotlib.figure import Figure
from matplotlib.gridspec import GridSpec
from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas
from astropy.io import fits
import imageio
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
from ..processing.fits_io import casa_observation_date, fits_image_path, is_casa_image
from ..processing.workers import process_pool_executor
from .coordinates import ConversionError, conversion_cache, frame_file
from .lightcurve import (
    MAX_CURVES,
    LightCurves,
    curve_label,
    curve_unit,
    curve_value,
    line_plot_options,
    lightcurve_csv_path,
    sum_scale,
)
from .utils import (
    frame_levels,
    level_scale,
    detect_coordinate_system,
    get_wcs_axis_labels,
    FrameContours,
    layer_file,
)

logger = logging.getLogger(__name__)


def _quiet_warnings():
    """Hide matplotlib's and astropy's user warnings (header fixes and the
    like, repeated for every frame). Only in processes this module runs:
    worker processes and the command line, never the app importing it."""
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
        # RGB, copied: the canvas (and its buffer) is reused for the next frame
        return np.ascontiguousarray(img_data[:, :, :3])
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


def _time_header(path):
    """A header with the observation time of image ``path`` (for a CASA
    image just DATE-OBS, read without exporting it)."""
    if is_casa_image(path):
        return {"DATE-OBS": casa_observation_date(path)}
    from ..processing.image_meta import fits_header

    return fits_header(path)


def observation_sort_key(filepath):
    """Sort key ordering files by observation time (then name); files
    without one come last, by modification time."""
    try:
        when = observation_time(_time_header(filepath), filepath)
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
    """One Stokes plane (first frequency channel) of a FITS or CASA image,
    rows = y, and its header.

    Images stored in an extension (e.g. tile-compressed) and CASA images are
    read through fits_image_path; the Stokes axis is found from the CTYPE
    keywords.
    Returns (None, None) if the file or the Stokes plane can't be read.
    """
    try:
        with fits.open(fits_image_path(filepath)) as hdul:
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


def load_frame_data(path, options):
    """(data, header) of frame image ``path`` in the video's coordinates
    (options["coordinates"], see video.coordinates) and Stokes. Raises
    ConversionError if it can't be converted."""
    return load_fits_data(frame_file(path, options), stokes=options.get("stokes", "I"))


def process_image(
    file_path,
    options,
    global_stats=None,
    frame_contours=None,
    frame_idx=0,
    problems=None,
    geometry=None,
):
    """Process a FITS image and return a frame for the video.

    Messages about what could not be drawn (contours that can't be placed,
    an image without a beam, ...) are added to the list ``problems``. The
    dict ``geometry`` is filled with where the image is in the frame (see
    _frame_geometry), so a point of the frame can be found in the image.
    """

    def report(message):
        if problems is not None and message not in problems:
            problems.append(message)

    try:
        # Load FITS data (converted to the video's coordinates if chosen)
        try:
            data, header = load_frame_data(file_path, options)
        except ConversionError as e:
            report(f"Frame not shown: {e}")
            return create_error_frame(
                options, "Cannot convert coordinates", frame_idx, os.path.basename(file_path)
            )

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
            try:
                other, _ = load_frame_data(difference_files[frame_idx], options)
            except ConversionError:
                other = None
            if other is None or other.shape != data.shape:
                return create_error_frame(
                    options,
                    "Cannot subtract: image missing or of another size",
                    frame_idx,
                    os.path.basename(file_path),
                )
            data = data - other

        # The region shown, and its lower left corner in the full image (for
        # WCS and contours)
        shift = _rotation_shift(options, header)
        data, region_origin = crop_region(data, options, shift=shift)

        # Get filename for display
        filename = os.path.basename(file_path)

        # Figure (all frames of a video share one size)
        width, height = options.get("frame_size") or frame_size(options, data.shape)

        # Axes - use WCS projection if enabled: this frame's own WCS, cut to
        # the region shown
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

        # The previous frame's figure when this one has the same layout
        # (building axes and colorbar is a third of a frame's time)
        line_plot = line_plot_options(options)
        frame = _frame_figure(
            (width, height, frame_wcs is not None, line_plot["height"] if line_plot else 0,
             data.shape, bool(options.get("colorbar", False))),
            frame_wcs,
            options.get("reuse_figures", True),
        )
        fig, canvas, ax, timeline_ax = frame.fig, frame.canvas, frame.ax, frame.timeline_ax

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
        if frame.img is None:
            frame.img = ax.imshow(
                data,
                cmap=cmap,
                norm=norm,
                origin="lower",
                interpolation="none",
            )
        else:
            frame.img.set_data(data)
            frame.img.set_cmap(cmap)
            frame.img.set_norm(norm)
            frame.img.set_extent((-0.5, data.shape[1] - 0.5, -0.5, data.shape[0] - 0.5))
        img = frame.img

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
                    report=report,
                    level_scales=options.get("contour_level_scales"),
                )
            except Exception as e:
                logger.error(f"Error drawing contours: {e}")
                report(f"Contours not drawn: {e}")

        # Solar disk, beam and grid, styled as in the main window
        _draw_plot_overlays(ax, header, options, region_origin, report)

        # The line plot's light-curve box
        if line_plot:
            _draw_curve_box(ax, line_plot, region_origin, shift)

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
                    c_header = _time_header(contour_file)
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
        else:
            ax.set_title("")

        # Add colorbar if requested
        if options.get("colorbar", False):
            import matplotlib.ticker as mticker

            # Create colorbar with proper sizing that works with GridSpec
            if frame.cbar is None:
                frame.cbar = fig.colorbar(img, ax=ax, shrink=0.9, aspect=30, pad=0.05)
            else:
                frame.cbar.update_normal(img)
            cbar = frame.cbar

            # Format the colorbar nicely
            cbar.ax.tick_params(labelsize=12)
            formatter = mticker.ScalarFormatter(useMathText=True)
            formatter.set_scientific(True)
            formatter.set_powerlimits((-2, 3))
            cbar.ax.yaxis.set_major_formatter(formatter)
            cbar.ax.yaxis.get_offset_text().set_fontsize(12)
            if cbar.ax.get_yscale() != "linear":
                # Not the labels of ticks outside the range at the ends
                from ..processing.plot_style import drop_outside_ticks

                drop_outside_ticks(cbar, norm.vmin, norm.vmax)

            # Add units if available
            cbar.set_label(header["BUNIT"] if header and "BUNIT" in header else "", fontsize=12)
        else:
            cbar = None

        # Labels, fonts, colours, ticks and borders as in the main window
        plot_settings = options.get("plot_settings")
        if plot_settings:
            _apply_frame_plot_settings(fig, ax, cbar, header, plot_settings, report)

        # The line plot under the image
        light_curves = options.get("light_curves")
        if light_curves is not None and timeline_ax is not None:
            light_curves.draw(
                timeline_ax, options.get("current_frame", 0), options.get("plot_settings")
            )

        # Adjust layout and render
        _keep_labels_in_frame(fig, ax)
        canvas.draw()
        if geometry is not None:
            geometry.update(
                _frame_geometry(fig, ax, header, full_data.shape, region_origin, shift)
            )

        return canvas_to_rgb_array(canvas)

    except Exception as e:
        logger.error(f"Error processing image {file_path}: {e}")
        _frame_figures.clear()  # may be half drawn
        return create_error_frame(
            options, f"Error: {str(e)[:30]}", frame_idx, os.path.basename(file_path)
        )


class _FrameFigure:
    """A frame's figure, axes, image and colorbar, kept for the next frame
    of the same layout."""

    def __init__(self, width, height, use_wcs, plot_height, frame_wcs):
        dpi = 100
        self.fig = Figure(figsize=(width / dpi, height / dpi), dpi=dpi)
        self.canvas = FigureCanvas(self.fig)
        self.timeline_ax = None
        projection = frame_wcs if use_wcs else None
        if plot_height:
            # The line plot under the image, ``plot_height`` % of the frame
            gs = GridSpec(
                2, 1, height_ratios=[100 - plot_height, plot_height], hspace=0.25, figure=self.fig
            )
            self.ax = self.fig.add_subplot(gs[0], projection=projection)
            self.timeline_ax = self.fig.add_subplot(gs[1])
        else:
            self.ax = self.fig.add_subplot(111, projection=projection)
        self.img = None
        self.cbar = None

    def clear(self, frame_wcs):
        """Back to the state of a new figure, keeping the axes, image and
        colorbar: what frames draw on top goes, and the layout is reset."""
        from matplotlib.transforms import Bbox

        ax = self.ax
        self.fig.subplots_adjust(
            **{k: rcParams[f"figure.subplot.{k}"] for k in ("left", "right", "bottom", "top", "wspace", "hspace")}
        )
        if frame_wcs is not None:
            ax.reset_wcs(frame_wcs)
        for artist in list(ax.collections) + list(ax.patches) + list(ax.lines) + list(ax.texts):
            artist.remove()
        if ax.get_legend() is not None:
            ax.get_legend().remove()
        ax.dataLim = Bbox.null()
        ax.ignore_existing_data_limits = True
        ax.set_xlabel("")
        ax.set_ylabel("")
        if self.timeline_ax is not None:
            self.timeline_ax.clear()


# The last frame's figure in this process: (layout key, _FrameFigure)
_frame_figures = {}


def _frame_figure(key, frame_wcs, reuse=True):
    """A figure for a frame of layout ``key`` (frame size, WCS axes or not,
    line plot height (0: none), data shape, colorbar): the previous
    frame's, cleared, if it had the same layout, else a new one."""
    frame = _frame_figures.get(key) if reuse else None
    if frame is None:
        width, height, use_wcs, plot_height = key[:4]
        frame = _FrameFigure(width, height, use_wcs, plot_height, frame_wcs)
        _frame_figures.clear()
        if reuse:
            _frame_figures[key] = frame
    else:
        frame.clear(frame_wcs)
    return frame


def _frame_geometry(fig, ax, header, image_shape, region_origin, shift):
    """Where the image is in a rendered frame: the axes box in frame pixels
    (left, top, right, bottom, from the top left corner), the axes limits in
    pixels of the region shown, its lower left corner in the full image
    (``image_shape``, rows and columns), the solar rotation shift of this
    frame's region, and the pixel size in degrees (None without sky
    coordinates)."""
    box = ax.get_window_extent()
    height = fig.bbox.height
    scale = None
    try:
        from astropy.wcs import WCS
        from astropy.wcs.utils import proj_plane_pixel_scales

        wcs = WCS(header, naxis=2)
        if wcs.has_celestial:
            scale = tuple(float(abs(v)) for v in proj_plane_pixel_scales(wcs.celestial))
    except Exception:
        pass
    return {
        "axes": (box.x0, height - box.y1, box.x1, height - box.y0),
        "xlim": tuple(float(v) for v in ax.get_xlim()),
        "ylim": tuple(float(v) for v in ax.get_ylim()),
        "origin": tuple(int(v) for v in region_origin),
        "shift": tuple(int(v) for v in shift),
        "image_shape": tuple(int(v) for v in image_shape[:2]),
        "pixel_scale_deg": scale,
    }


def _keep_labels_in_frame(fig, ax):
    """Widen the left / bottom margin if the axis and tick labels of ``ax``
    stick out of the frame (long tick labels, e.g. of a large field in
    arcsec); frames whose labels fit are left as they are."""
    try:
        box = ax.get_tightbbox(fig.canvas.get_renderer())
    except Exception:
        return
    if box is None:
        return
    pad = 4  # pixels
    params = fig.subplotpars
    left = params.left + max(0.0, pad - box.x0) / fig.bbox.width
    bottom = params.bottom + max(0.0, pad - box.y0) / fig.bbox.height
    if (left, bottom) != (params.left, params.bottom) and left < params.right and bottom < params.top:
        fig.subplots_adjust(left=left, bottom=bottom)


def _draw_plot_overlays(ax, header, options, region_origin, report):
    """Solar disk, beam and coordinate grid, if chosen (video.overlays), and
    the main window's annotations (video.annotations)."""
    from ..processing.plot_style import (
        DEFAULT_BEAM_STYLE,
        DEFAULT_GRID_STYLE,
        DEFAULT_SOLAR_DISK_STYLE,
    )
    from .overlays import draw_beam, draw_grid, draw_solar_disk

    header = header or {}
    if options.get("show_solar_disk"):
        style = dict(DEFAULT_SOLAR_DISK_STYLE, **(options.get("solar_disk_style") or {}))
        draw_solar_disk(ax, header, style, region_origin, report)
    if options.get("show_beam"):
        style = dict(DEFAULT_BEAM_STYLE, **(options.get("beam_style") or {}))
        draw_beam(ax, header, style, report)
    if options.get("show_grid"):
        style = dict(DEFAULT_GRID_STYLE, **(options.get("grid_style") or {}))
        draw_grid(ax, style, report)
    if options.get("annotations"):
        from .annotations import draw_annotations

        draw_annotations(ax, options["annotations"], header, region_origin, report)


def _draw_curve_box(ax, line_plot, region_origin, shift):
    """The light-curve box on the image, if it is used and shown."""
    from matplotlib.patches import Rectangle

    from .lightcurve import COLORS

    box = line_plot.get("box")
    users = [
        k for k, c in enumerate(line_plot["curves"][:MAX_CURVES])
        if c.get("source") == "image" and c.get("area") == "box"
    ]
    if not box or not users or not line_plot.get("show_box", True):
        return
    x_min, x_max, y_min, y_max = box
    x0 = x_min + shift[0] - region_origin[0] - 0.5
    y0 = y_min + shift[1] - region_origin[1] - 0.5
    ax.add_patch(
        Rectangle(
            (x0, y0), x_max - x_min + 1, y_max - y_min + 1, fill=False,
            edgecolor=COLORS[users[0] % len(COLORS)], linewidth=1.5, linestyle="--",
        )
    )


def _apply_frame_plot_settings(fig, ax, cbar, header, plot_settings, report):
    """Plot customization settings of the main window on a frame. A custom
    title goes above the time / frame / file lines; "auto" colours are
    those of the light theme, and the layout is the main window's padding."""
    from ..processing.plot_style import DEFAULT_PLOT_SETTINGS, apply_plot_settings

    settings = dict(DEFAULT_PLOT_SETTINGS, **plot_settings)
    # Light theme backgrounds, not those of the process (the preview is
    # rendered in the app, whose theme may be dark)
    for key in ("plot_bg_color", "figure_bg_color"):
        if settings.get(key) == "auto":
            settings[key] = "white"
    if settings.get("title") and ax.get_title():
        settings["title"] = settings["title"] + "\n" + ax.get_title()
    apply_plot_settings(
        ax,
        cbar,
        settings,
        fig,
        dark=False,
        bunit=(header or {}).get("BUNIT"),
        on_error=report,
    )
    if settings.get("use_tight_layout"):
        fig.tight_layout()
    else:
        # The main window's padding leaves the right edge to its colorbar;
        # without one, keep a margin there like the left one, or the plot
        # runs off the frame
        right = settings["pad_right"]
        if cbar is None:
            right = min(right, 1 - settings["pad_left"])
        if right <= settings["pad_left"]:
            right = settings["pad_right"]
        fig.subplots_adjust(
            left=settings["pad_left"],
            right=right,
            top=settings["pad_top"],
            bottom=settings["pad_bottom"],
            wspace=settings["pad_wspace"],
            hspace=settings["pad_hspace"],
        )


def calculate_global_stats(files, options):
    """(vmin, vmax) over all frames for the Global Auto range (None in the
    other modes): worked out by prepare_video_options, else read now."""
    if options.get("range_mode", 1) != 2:
        return None
    if "global_range" not in options:
        results = _scan_frames(files, options, {"range"})
        options["global_range"] = _global_range(results)
    return options["global_range"]


def _global_range(results):
    ranges = [r["range"] for r in results if r.get("range")]
    if not ranges:
        return None
    return (min(low for low, _ in ranges), max(high for _, high in ranges))


class _Cancelled(Exception):
    pass


def _scan_frames(files, options, needs, executor=None, progress=None):
    """Read every frame once (in ``executor``'s worker processes if given)
    for what the whole video needs: ``needs`` among "range" (the frame's
    colour range, as Auto Per Frame would give), "curves" (the values of
    the line plot's curves options["_curves_now"], see _curve_values) and
    "levels" (each contour
    layer's peak or RMS, see level_scale; of the first frame only with
    contour_level_mode "first"). Returns one dict per frame.

    ``progress(done, total)`` is told how far it got; returning False
    cancels (raises _Cancelled).
    """
    first_only = options.get("contour_level_mode") == "first"
    tasks = []
    for i, path in enumerate(files):
        frame_needs = set(needs)
        if first_only and i > 0:
            frame_needs.discard("levels")
        tasks.append((i, path, options, frame_needs))

    results = [{} for _ in files]
    if executor is None:
        done = (_scan_one(task) for task in tasks)
    else:
        futures = [executor.submit(_scan_one, task) for task in tasks]
        done = (future.result() for future in futures)
    try:
        for count, (i, result) in enumerate(done, 1):
            results[i] = result
            if progress and progress(count, len(tasks)) is False:
                raise _Cancelled()
    finally:
        if executor is not None:
            for future in futures:
                future.cancel()
    return results


def _scan_one(args):
    """See _scan_frames (in a worker)."""
    _quiet_warnings()
    i, path, options, needs = args
    result = {}
    try:
        data, header = load_frame_data(path, options)
    except Exception as e:
        logger.warning(f"Could not read {os.path.basename(path)}: {e}")
        return i, result
    if data is None:
        return i, result
    shift = _rotation_shift(options, header) if needs & {"range", "curves"} else (0, 0)

    if "levels" in needs:
        scales = result["levels"] = {}
        contours = _worker_frame_contours(options)
        for layer in options.get("contour_layers") or []:
            if not layer.get("enabled", True) or layer.get("level_type") not in ("fraction", "sigma"):
                continue
            try:
                layer_data, _ = contours.layer_data(
                    layer, i, path, header, data, options.get("stokes", "I")
                )
                if layer_data is not None:
                    scales[layer.get("id")] = level_scale(layer, layer_data)
            except Exception as e:
                logger.warning(f"Contour levels of frame {i}: {e}")

    if "curves" in needs:
        result["curves"] = _curve_values(i, path, data, header, shift, options)

    if "range" in needs:
        try:
            difference_files = options.get("difference_files")
            if difference_files:
                other, _ = load_frame_data(difference_files[i], options)
                if other is None or other.shape != data.shape:
                    return i, result
                data = data - other
            shown = crop_region(data, options, shift=shift)[0]
            result["range"] = (
                np.nanpercentile(shown, options.get("lower_percentile", 0)),
                np.nanpercentile(shown, options.get("upper_percentile", 100)),
            )
        except Exception as e:
            logger.error(f"Error calculating stats for {path}: {e}")
    return i, result


def make_frame_contours(options):
    """The FrameContours drawing a video's contour layers, or None."""
    return FrameContours() if options.get("contour_layers") else None


# In a worker process: the FrameContours of the video being made, kept
# between frames so a fixed contour image is read (and converted) once
_worker_contours = {"key": None, "contours": None}


def _worker_frame_contours(options):
    """This process's FrameContours for the video of ``options`` (kept
    between its frames, so contour images are read once)."""
    key = options.get("video_id")
    if _worker_contours["key"] != key or _worker_contours["contours"] is None:
        _worker_contours.update(key=key, contours=FrameContours())
    return _worker_contours["contours"]


def process_image_wrapper(args):
    """
    Wrapper function for process_image to use with multiprocessing.
    Takes a tuple of (file_path, index, options, global_stats) and returns
    (index, processed_frame, problems, geometry); see process_image.
    """
    # Configure matplotlib for non-interactive backend to avoid thread safety issues
    import matplotlib

    matplotlib.use("Agg")  # Use non-interactive backend

    _quiet_warnings()
    file_path, idx, options, global_stats = args
    problems, geometry = [], {}
    try:
        options = dict(options, current_frame=idx)
        result = process_image(
            file_path,
            options,
            global_stats,
            frame_contours=_worker_frame_contours(options),
            frame_idx=idx,
            problems=problems,
            geometry=geometry,
        )
        return idx, result, problems, geometry
    except Exception as e:
        logger.error(f"Error processing frame {idx} ({file_path}): {e}")
        return idx, None, problems, geometry


def _frames_in_order(files, options, global_stats, frame_contours, executor=None, workers=1):
    """Render the frames, yielding (index, frame, problems) in order as they
    are ready (problems: see process_image).

    In ``executor`` (with ``workers`` processes) up to two frames per
    process are rendered ahead, so memory stays bounded; closing the
    generator (e.g. on cancel) stops submitting frames and drops those not
    started. Without one, frames are rendered here.
    """
    if executor is None:
        for i, file_path in enumerate(files):
            problems = []
            frame = process_image(
                file_path,
                dict(options, current_frame=i),
                global_stats,
                frame_contours=frame_contours,
                frame_idx=i,
                problems=problems,
            )
            yield i, frame, problems
        return

    logger.info(f"Rendering frames with {workers} processes")
    pending = {}
    try:
        next_to_submit = 0
        for i in range(len(files)):
            while next_to_submit < len(files) and next_to_submit < i + 2 * workers:
                pending[next_to_submit] = executor.submit(
                    process_image_wrapper,
                    (files[next_to_submit], next_to_submit, options, global_stats),
                )
                next_to_submit += 1
            _, frame, problems, _ = pending.pop(i).result()
            yield i, frame, problems
    finally:
        for future in pending.values():
            future.cancel()


def file_times(paths):
    """Observation time (datetime or None) of each file."""
    times = []
    for path in paths:
        try:
            times.append(observation_time(_time_header(path), path))
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


def region_bounds(options, shape, shift=(0, 0)):
    """(x_min, x_max, y_min, y_max), inclusive, of the region shown in an
    image of ``shape`` (rows, columns), moved by ``shift`` pixels; None if
    the whole image is shown. With region_width / region_height (set by
    prepare_video_options) every frame's region has that size."""
    if not options.get("region_enabled", False):
        return None
    rows, cols = shape[:2]
    x_min, x_max = sorted((options.get("x_min", 0), options.get("x_max", cols - 1)))
    y_min, y_max = sorted((options.get("y_min", 0), options.get("y_max", rows - 1)))
    dx, dy = shift
    x_min, x_max, y_min, y_max = x_min + dx, x_max + dx, y_min + dy, y_max + dy

    if "region_width" in options and "region_height" in options:
        width, height = options["region_width"], options["region_height"]
        if x_min >= cols or y_min >= rows:
            logger.warning("Region outside the image: showing its lower left corner")
            x_min, y_min = 0, 0
        x_max = x_min + width - 1
        y_max = y_min + height - 1

    x_min, x_max = (max(0, min(v, cols - 1)) for v in (x_min, x_max))
    y_min, y_max = (max(0, min(v, rows - 1)) for v in (y_min, y_max))
    return x_min, x_max, y_min, y_max


def crop_region(data, options, header=None, shift=None):
    """(data cut to the region shown, its lower left corner (x, y) in
    ``data``). The region follows solar rotation if chosen (needs the
    frame's ``header``, or the ``shift`` from _rotation_shift)."""
    if shift is None:
        shift = _rotation_shift(options, header)
    bounds = region_bounds(options, data.shape, shift)
    if bounds is None:
        return data, (0, 0)
    x_min, x_max, y_min, y_max = bounds
    return data[y_min : y_max + 1, x_min : x_max + 1], (x_min, y_min)


def _first_frame_file(files, options):
    """The file to read for the first frame that can be shown in the video's
    coordinates (frame size, region and axes come from it); frames that
    can't be converted are reported when drawn. Raises ConversionError if
    none can."""
    problem = None
    for path in files:
        try:
            return frame_file(path, options)
        except ConversionError as e:
            problem = problem or e
    raise problem or ConversionError("no images")


def prepare_video_options(files, options, executor=None, scan_progress=None):
    """Fill in ``options`` with what all frames of a video share: the frame
    count, the region (with even dimensions), the frame size, the coordinate
    system, and from one read of every frame (see _scan_frames; in
    ``executor``'s processes if given, telling ``scan_progress``) the global
    colour range, the line plot's curves and steady contour levels. Used for
    the video and its preview, so the preview frame is the video frame.
    Returns ``options``.
    """
    _layers_from_contour_mode(options)

    # Calculate total frames
    total_frames = len(files)
    options["total_frames"] = total_frames

    # If region selection is enabled, pre-scan the first file to determine dimensions
    if options.get("region_enabled", False):
        logger.info("Determining region dimensions...")

        # Load the first file
        first_data, _ = load_fits_data(
            _first_frame_file(files, options), stokes=options.get("stokes", "I")
        )

        if first_data is not None:
            # The region as chosen (in order, inside the image); its size is
            # worked out here
            options.pop("region_width", None)
            options.pop("region_height", None)
            x_min, x_max, y_min, y_max = region_bounds(options, first_data.shape)

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

        first = _first_frame_file(files, options)
        if coordinate_type(fits_header(first)) == "hpc":
            options["track_reference"] = {
                "file": first,
                "center": (
                    (options.get("x_min", 0) + options.get("x_max", 0)) / 2,
                    (options.get("y_min", 0) + options.get("y_max", 0)) / 2,
                ),
            }
        else:
            logger.warning("Following solar rotation needs helioprojective images")

    # One frame size for the whole video, from the first image (or region)
    if "frame_size" not in options:
        first_data, _ = load_fits_data(
            _first_frame_file(files, options), stokes=options.get("stokes", "I")
        )
        shape = first_data.shape if first_data is not None else (1, 1)
        if options.get("region_enabled", False) and "region_width" in options:
            shape = (options["region_height"], options["region_width"])
        options["frame_size"] = frame_size(options, shape)
        logger.info(f"Frame size: {options['frame_size'][0]}x{options['frame_size'][1]}")

    # Initialize WCS coordinate detection if enabled
    if options.get("wcs_enabled", False):
        logger.info("Detecting coordinate system from first file...")
        coord_info = detect_coordinate_system(_first_frame_file(files, options))
        options["coord_info"] = coord_info
        logger.info(f"Coordinate system: {coord_info.get('type', 'unknown')}")

    # One read of every frame for the global range, the line plot and steady
    # contour levels, whichever are needed
    needs = set()
    if options.get("range_mode", 1) == 2 and "global_range" not in options:
        needs.add("range")
    line_plot = line_plot_options(options)
    later = []
    if line_plot and "light_curves" not in options:
        needs.add("curves")
        curves = line_plot["curves"][:MAX_CURVES]
        # Inside-contour values with steady levels need those levels first
        if options.get("contour_level_mode", "frame") != "frame":
            later = [
                k for k, c in enumerate(curves)
                if c.get("source") != "image" and c.get("area") == "contour"
            ]
        options["_line_plot"] = line_plot
        options["_curves_now"] = [k for k in range(len(curves)) if k not in later]
    if options.get("contour_level_mode", "frame") != "frame" and any(
        layer.get("enabled", True) and layer.get("level_type") in ("fraction", "sigma")
        for layer in options.get("contour_layers") or []
    ):
        needs.add("levels")

    if needs:
        logger.info(f"Reading all {len(files)} frames for: {', '.join(sorted(needs))}")
        results = _scan_frames(files, options, needs, executor, scan_progress)
        if "range" in needs:
            options["global_range"] = _global_range(results)
        if "levels" in needs:
            options["contour_level_scales"] = _steady_level_scales(
                options.get("contour_layers") or [], results, options["contour_level_mode"]
            )
        if "curves" in needs:
            options["light_curves"] = _light_curves(
                files, options, line_plot, results, later, executor, scan_progress
            )

    return options


def _curve_values(i, path, data, header, shift, options):
    """(values, units) of the line plot curves options["_curves_now"] in
    frame ``i`` (image ``path`` with ``data``, before any difference); NaN
    for the other curves and where a value can't be had."""
    from ..processing.fits_io import fits_image_path
    from ..processing.image_meta import fits_header

    line_plot = options["_line_plot"]
    curves = line_plot["curves"][:MAX_CURVES]
    values, units = [np.nan] * len(curves), [""] * len(curves)
    layers = {layer.get("id"): layer for layer in options.get("contour_layers") or []}
    for k in options.get("_curves_now", range(len(curves))):
        curve = curves[k]
        quantity = curve.get("quantity", "max")
        try:
            wcs = None
            if curve.get("source", "image") == "image":
                image_header = header
                if curve.get("area") == "box":
                    if not line_plot.get("box"):
                        continue
                    x_min, x_max, y_min, y_max = line_plot["box"]
                    bounds = region_bounds(
                        dict(region_enabled=True, x_min=x_min, x_max=x_max, y_min=y_min, y_max=y_max),
                        data.shape,
                        shift,
                    )
                    x0, x1, y0, y1 = bounds
                    part = data[y0 : y1 + 1, x0 : x1 + 1]
                else:
                    part = crop_region(data, options, shift=shift)[0]
            else:
                layer = layers.get(curve.get("source"))
                if layer is None or not layer.get("enabled", True):
                    continue
                contours = _worker_frame_contours(options)
                part, wcs = contours.layer_data(
                    layer, i, path, header, data, options.get("stokes", "I")
                )
                if part is None:
                    continue
                layer_path = layer_file(layer, i, path)
                image_header = header if layer_path == path else fits_header(fits_image_path(layer_path))
                if curve.get("area") == "contour":
                    scale = (options.get("contour_level_scales") or {}).get(layer.get("id"))
                    positive, _ = frame_levels(layer, part, scale)
                    part = part[part >= positive[0]] if positive else part[:0]
            value = curve_value(part, quantity)
            if quantity == "sum":
                factor, unit = sum_scale(image_header, wcs)
                value *= factor
            else:
                unit = curve_unit(quantity, image_header)
            values[k], units[k] = value, unit
        except Exception as e:
            logger.warning(f"Line plot value {k + 1} of frame {i + 1}: {e}")
    return values, units


def _light_curves(files, options, line_plot, results, later, executor, progress):
    """The LightCurves of the line plot from the frames' scan ``results``,
    reading the frames again for the curves ``later`` (inside a contour,
    with steady levels just worked out)."""
    from ..processing.contour_layers import contour_layer_name

    names = {
        layer.get("id"): contour_layer_name(layer, "Image")
        for layer in options.get("contour_layers") or []
    }
    curves = line_plot["curves"][:MAX_CURVES]
    light_curves = LightCurves(
        line_plot, [curve_label(c, names) for c in curves], file_times(files), len(files)
    )
    for i, result in enumerate(results):
        if "curves" in result:
            light_curves.set_frame(i, *result["curves"], only=options["_curves_now"])
    if later:
        options["_curves_now"] = later
        for i, result in enumerate(_scan_frames(files, options, {"curves"}, executor, progress)):
            if "curves" in result:
                light_curves.set_frame(i, *result["curves"], only=later)
    for key in ("_curves_now", "_line_plot"):
        options.pop(key, None)
    return light_curves


def _steady_level_scales(layers, results, mode):
    """Layer id -> the peak / RMS all frames' levels are relative to: the
    first frame's (mode "first"), or over all frames (mode "all") the
    highest peak (fraction levels) or the median RMS (sigma levels)."""
    scales = {}
    for layer in layers:
        key = layer.get("id")
        values = [r["levels"][key] for r in results if key in r.get("levels", {})]
        values = [v for v in values if v is not None and np.isfinite(v)]
        if not values:
            continue
        if mode == "first":
            scales[key] = values[0]
        elif layer.get("level_type") == "sigma":
            scales[key] = float(np.median(values))
        else:
            scales[key] = float(max(values))
    return scales


def _convert_frames(files, options, progress_callback=None, status_callback=None, executor=None):
    """Convert the frames (and difference images) to the video's coordinates,
    and export CASA images to FITS, up front, in parallel if allowed; the
    frames then read the copies. Frames that can't be converted are left for
    process_image to report. Returns False if cancelled through
    ``progress_callback``."""
    target = options.get("coordinates") or "native"
    paths = list(dict.fromkeys(list(files) + list(options.get("difference_files") or [])))
    if target == "native":
        paths = [p for p in paths if is_casa_image(p)]
    if not paths:
        return True
    cache_dir = options.get("coordinate_cache")
    if target == "native":
        what = "Reading CASA images"
    else:
        what = f"Converting images to {'helioprojective' if target == 'hpc' else 'RA/Dec'} coordinates"
    def done(count):
        if status_callback:
            status_callback(f"{what}: {count} of {len(paths)}")
        return not progress_callback or progress_callback(0, len(files))

    if not done(0):
        return False
    if executor is not None:
        futures = [executor.submit(_convert_one, (p, target, cache_dir)) for p in paths]
        try:
            for count, future in enumerate(futures, 1):
                future.result()
                if not done(count):
                    return False
        finally:
            for future in futures:
                future.cancel()
    else:
        for count, path in enumerate(paths, 1):
            _convert_one((path, target, cache_dir))
            if not done(count):
                return False
    return True


def _convert_one(args):
    """Convert one frame image, or export a CASA image (target "native"), in
    a worker; failures are reported when the frame is drawn."""
    _quiet_warnings()
    path, target, cache_dir = args
    from .coordinates import converted_file

    try:
        if target == "native":
            fits_image_path(path)
        else:
            converted_file(path, target, cache_dir)
    except Exception as e:
        logger.warning(f"{os.path.basename(path)}: {e}")


def create_video(
    files, output_file, options, progress_callback=None, problems=None, status_callback=None
):
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
    problems : dict, optional
        Filled with what could not be drawn (see process_image), each
        message with the number of frames it applies to.
    status_callback : callable, optional
        Told (with a text) what is being prepared before frames are drawn,
        such as converting the images to the video's coordinates.

    Returns
    -------
    output_file : str or None
        Path to the created video file, or None if progress_callback
        cancelled it (no file is left behind).
    """
    with conversion_cache(options) as options:
        return _create_video(files, output_file, options, progress_callback, problems, status_callback)


def _create_video(files, output_file, options, progress_callback, problems, status_callback):
    """create_video, with a folder for converted frames if needed."""
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

        # One pool of worker processes converts, reads and renders the frames
        # (none: all here); render_in_worker renders even without
        # multiprocessing in a worker, whose matplotlib settings are the
        # defaults, not those of the app (whose theme changes them)
        num_cores = options.get("cpu_count", max(1, cpu_count() - 1))
        parallel = options.get("use_multiprocessing", False) and num_cores > 1 and len(files) > 1
        workers = num_cores if parallel else (1 if options.get("render_in_worker") else 0)
        executor = (
            process_pool_executor(workers, preload=["solar_radio_image_viewer.video.creator"])
            if workers
            else None
        )
        options = dict(options, video_id=f"{os.getpid()}-{time.time()}")
        try:
            return _render_video(
                files, output_file, options, progress_callback, problems, status_callback,
                writer_factory, executor, workers, start_time,
            )
        finally:
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)

    except Exception as e:
        logger.error(f"Error creating video: {e}")
        raise


def _render_video(
    files, output_file, options, progress_callback, problems, status_callback,
    writer_factory, executor, workers, start_time,
):
    """The rest of _create_video, with the worker pool ``executor``."""
    if not _convert_frames(files, options, progress_callback, status_callback, executor):
        logger.info("Video creation cancelled by user")
        return None

    def scan_progress(done, total):
        if status_callback:
            status_callback(f"Reading frames for the range, line plot and levels: {done} of {total}")
        return not progress_callback or progress_callback(0, total)

    try:
        options = prepare_video_options(files, options, executor, scan_progress)
    except _Cancelled:
        logger.info("Video creation cancelled by user")
        return None
    total_frames = options["total_frames"]
    logger.info(f"Creating video from {total_frames} files...")

    # Contour drawing (worker processes make their own)
    frame_contours = make_frame_contours(options)

    # Calculate global stats if needed
    global_stats = calculate_global_stats(files, options)

    # Render and write the frames in order, one at a time
    render_start = time.time()
    written = 0
    frames = _frames_in_order(
        files, options, global_stats, frame_contours, executor, max(workers, 1)
    )
    cancelled = False
    try:
        with writer_factory() as writer:
            for i, frame, frame_problems in frames:
                for message in frame_problems:
                    if problems is not None:
                        problems[message] = problems.get(message, 0) + 1
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

    # The line plot's values, next to the video
    line_plot = line_plot_options(options)
    if line_plot and line_plot.get("csv") and options.get("light_curves") is not None:
        csv_path = options["light_curves"].write_csv(lightcurve_csv_path(output_file), files)
        logger.info(f"Line plot values saved: {csv_path}")

    total_time = time.time() - start_time
    logger.info(f"Video created successfully: {output_file} in {total_time:.2f}s")
    return output_file


def main(argv=None):
    """Command line:
    python -m solar_radio_image_viewer.video.creator OUTPUT FILE.fits [FILE.fits ...]
    python -m solar_radio_image_viewer.video.creator --preset SETTINGS.json OUTPUT
    (a preset saved from the Create Video window makes the same video)
    """
    import argparse
    import json

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    _quiet_warnings()

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
    problems = {}
    created = create_video(files, args.output, options, problems=problems)
    for message, count in problems.items():
        print(f"Warning: {message} ({count} of {len(files)} frames)")
    return 0 if created else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())

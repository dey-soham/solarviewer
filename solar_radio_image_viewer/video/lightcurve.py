"""
The line plot under video frames: up to MAX_CURVES light curves of the
video image or of its contour images, against observation time or frame.

Each curve is a quantity (peak, mean, sum, ...) of an area of a source:
the image (the region shown, or a light-curve box) or a contour layer's
image (all of it, or inside its lowest contour). Values come from the one
read of every frame (creator._scan_frames); the plot follows the plot
appearance settings and can be saved as a CSV file.
"""

import csv
import logging
import math

import numpy as np

logger = logging.getLogger(__name__)

MAX_CURVES = 4

QUANTITIES = {
    "max": "peak",
    "min": "minimum",
    "mean": "mean",
    "median": "median",
    "rms": "RMS",
    "sum": "sum",
}

# Areas of the image, and of a contour layer's image
IMAGE_AREAS = {"shown": "region shown", "box": "box"}
LAYER_AREAS = {"all": "whole image", "contour": "inside lowest contour"}

# Okabe-Ito colours: told apart with all common colour vision deficiencies
COLORS = ["#E69F00", "#56B4E9", "#009E73", "#CC79A7"]


def default_line_plot():
    """Line plot settings: the image's peak and minimum against time."""
    return {
        "enabled": False,
        "curves": [
            {"source": "image", "quantity": "max", "area": "shown"},
            {"source": "image", "quantity": "min", "area": "shown"},
        ],
        "x": "time",  # or "frame"
        "y": "shared",  # or "normalised": each curve / its largest |value|
        "log": False,
        "box": None,  # (x_min, x_max, y_min, y_max) image pixels, inclusive
        "show_box": True,
        "height": 12,  # % of the frame height
        "csv": False,
    }


def line_plot_options(options):
    """The video's line plot settings (None if it has none): ``options
    ["line_plot"]``, or made from the older min/max timeline options
    (minmax_timeline_enabled, timeline_source, timeline_log_scale)."""
    settings = options.get("line_plot")
    if settings is None:
        if not options.get("minmax_timeline_enabled"):
            return None
        source = "image"
        if options.get("timeline_source", 0) == 1:
            # The contours' own images (a fixed file or a series)
            for layer in options.get("contour_layers") or []:
                if layer.get("enabled", True) and layer.get("source") in ("series", "external"):
                    source = layer.get("id")
                    break
        area = "shown" if source == "image" else "all"
        settings = dict(
            default_line_plot(),
            enabled=True,
            x="frame",
            log=bool(options.get("timeline_log_scale", False)),
            curves=[
                {"source": source, "quantity": "max", "area": area},
                {"source": source, "quantity": "min", "area": area},
            ],
        )
    if not settings.get("enabled"):
        return None
    return dict(default_line_plot(), **settings)


def lightcurve_csv_path(output_file):
    """Where the line plot's values of video ``output_file`` are saved."""
    import os

    return os.path.splitext(output_file)[0] + "_lightcurve.csv"


def curve_value(values, quantity):
    """``quantity`` of the finite ``values`` (NaN if there are none)."""
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if not values.size:
        return math.nan
    if quantity == "max":
        return float(values.max())
    if quantity == "min":
        return float(values.min())
    if quantity == "mean":
        return float(values.mean())
    if quantity == "median":
        return float(np.median(values))
    if quantity == "rms":
        return float(np.sqrt(np.mean(values**2)))
    if quantity == "sum":
        return float(values.sum())
    raise ValueError(f"unknown quantity {quantity}")


def sum_scale(header, wcs=None):
    """(factor, unit) of a sum of pixel values of an image with ``header``
    (and ``wcs``, else its own): for a Jy/beam image the integrated flux in
    Jy (the sum divided by the beam area in pixels), else the plain sum."""
    header = header or {}
    unit = str(header.get("BUNIT", "") or "").strip()
    compact = unit.lower().replace(" ", "").replace(".", "")
    if compact in ("jy/beam", "jybeam-1", "jy/bm"):
        try:
            if wcs is None:
                from astropy.wcs import WCS

                wcs = WCS(header, naxis=2)
            from astropy.wcs.utils import proj_plane_pixel_scales

            sx, sy = (abs(float(v)) for v in proj_plane_pixel_scales(wcs.celestial))
            bmaj, bmin = float(header["BMAJ"]), float(header["BMIN"])
            beam_pixels = math.pi * bmaj * bmin / (4 * math.log(2)) / (sx * sy)
            if beam_pixels > 0:
                return 1.0 / beam_pixels, "Jy"
        except Exception:
            pass
    return 1.0, f"{unit} × pixels" if unit else ""


def curve_unit(quantity, header, wcs=None):
    """Unit of ``quantity`` of an image with ``header``."""
    if quantity == "sum":
        return sum_scale(header, wcs)[1]
    return str((header or {}).get("BUNIT", "") or "").strip()


def curve_label(curve, layer_names):
    """Name of a curve, e.g. "Image · peak" or "LOFAR · I · mean (inside
    lowest contour)"; ``layer_names`` maps layer ids to names."""
    source = curve.get("source", "image")
    name = "Image" if source == "image" else layer_names.get(source, "Contours")
    label = f"{name} · {QUANTITIES.get(curve.get('quantity'), curve.get('quantity'))}"
    area = curve.get("area")
    if area in ("box", "contour"):
        label += f" ({(IMAGE_AREAS if source == 'image' else LAYER_AREAS)[area]})"
    if curve.get("quantity") == "sum" and area == "shown":
        label += " (region shown)"
    return label


class LightCurves:
    """The curves of a video: their values in every frame, and the plot."""

    def __init__(self, settings, labels, times, total_frames):
        self.settings = settings
        self.curves = settings["curves"][:MAX_CURVES]
        self.labels = labels
        # Observation times (datetime) of the frames, or None where unknown
        self.times = list(times) if times is not None else [None] * total_frames
        self.total_frames = total_frames
        self.values = [np.full(total_frames, np.nan) for _ in self.curves]
        self.units = ["" for _ in self.curves]

    def set_frame(self, index, values, units, only=None):
        """The curves' values (and units) in frame ``index`` (of the curves
        ``only``, default all)."""
        for k in range(len(self.curves)) if only is None else only:
            self.values[k][index] = values[k]
            if units[k] and not self.units[k]:
                self.units[k] = units[k]

    # ---------- Plot ----------

    def _x(self):
        """x of every frame (matplotlib dates, or frame numbers) and label."""
        if self.settings.get("x") == "time" and all(t is not None for t in self.times):
            from matplotlib.dates import date2num

            return np.array([date2num(t) for t in self.times]), "Time (UT)"
        return np.arange(self.total_frames, dtype=float), "Frame"

    def _y(self):
        """The curves as plotted (normalised, positive for log) and y label."""
        values = [v.copy() for v in self.values]
        if self.settings.get("y") == "normalised":
            for v in values:
                scale = np.nanmax(np.abs(v)) if np.isfinite(v).any() else 0
                if scale:
                    v /= scale
            label = "Normalised"
        else:
            units = {u for u in self.units if u}
            label = units.pop() if len(units) == 1 else "Value"
        if self.settings.get("log"):
            for v in values:
                v[~(v > 0)] = np.nan
        return values, label

    def draw(self, ax, index, plot_settings=None):
        """Plot the curves on ``ax``, up to frame ``index`` in full and
        faintly after it, with a marker at the frame."""
        x, xlabel = self._x()
        values, ylabel = self._y()
        last = index + 1

        # Band between a minimum and a peak of the same source and area
        for k, curve in enumerate(self.curves):
            if curve.get("quantity") != "min":
                continue
            for j, other in enumerate(self.curves):
                if other.get("quantity") == "max" and (
                    other.get("source"), other.get("area")
                ) == (curve.get("source"), curve.get("area")):
                    if last > 1:
                        ax.fill_between(
                            x[:last], values[k][:last], values[j][:last],
                            alpha=0.25, color=COLORS[j % len(COLORS)], linewidth=0,
                        )

        for k, label in enumerate(self.labels):
            color = COLORS[k % len(COLORS)]
            ax.plot(
                x[:last], values[k][:last], color=color, linewidth=1.5, label=label,
                marker="." if np.isnan(values[k]).any() else None, markersize=5,
            )
            if index < self.total_frames - 1:
                # Dots where values have gaps (e.g. frames not fetched yet),
                # so values between gaps show
                ax.plot(
                    x[index:], values[k][index:], color=color, linewidth=0.8, alpha=0.3,
                    marker="." if np.isnan(values[k]).any() else None, markersize=4,
                )
            if index < self.total_frames and np.isfinite(values[k][index]):
                ax.plot(
                    x[index], values[k][index], "o", color=color, markersize=5,
                    markeredgecolor="gray", markeredgewidth=0.8,
                )
        if index < self.total_frames:
            ax.axvline(x[index], color="gray", linewidth=1.2, alpha=0.7)

        # Limits
        if self.total_frames > 1:
            pad = (x[-1] - x[0]) * 0.01 if xlabel != "Frame" else 0.5
            ax.set_xlim(x[0] - pad, x[-1] + pad)
        finite = np.concatenate([v[np.isfinite(v)] for v in values]) if values else []
        if self.settings.get("log"):
            ax.set_yscale("log")
            if len(finite):
                ax.set_ylim(finite.min() / 1.3, finite.max() * 1.3)
        elif len(finite):
            low, high = finite.min(), finite.max()
            margin = (high - low) * 0.15 if high != low else abs(high) * 0.1 or 1.0
            ax.set_ylim(low - margin, high + margin)

        self._style(ax, xlabel, ylabel, plot_settings)
        if len(self.labels) > 1:
            legend = ax.legend(
                loc="upper left", fontsize=7, frameon=False, ncol=min(len(self.labels), 4),
                handlelength=1.5, borderaxespad=0.2,
            )
            if plot_settings:
                text_color = plot_settings.get("text_color", "auto")
                if text_color not in ("auto", "", None):
                    for text in legend.get_texts():
                        text.set_color(text_color)

    def _style(self, ax, xlabel, ylabel, plot_settings):
        import matplotlib.pyplot as plt
        from matplotlib.ticker import ScalarFormatter

        ps = plot_settings or {}
        tick_size = ps.get("axis_tick_fontsize", 10) if ps else 10
        label_size = ps.get("axis_label_fontsize", 11) if ps else 11
        text_color = ps.get("text_color", "auto")
        text_color = None if text_color in ("auto", "", None) else text_color
        tick_color = ps.get("tick_color", "auto")
        tick_color = None if tick_color in ("auto", "", None) else tick_color

        if xlabel == "Time (UT)":
            from matplotlib.dates import AutoDateLocator, ConciseDateFormatter

            locator = AutoDateLocator(minticks=3, maxticks=6)
            ax.xaxis.set_major_locator(locator)
            ax.xaxis.set_major_formatter(ConciseDateFormatter(locator))
        if not self.settings.get("log"):
            ax.yaxis.set_major_locator(plt.MaxNLocator(3))
            formatter = ScalarFormatter(useMathText=True)
            formatter.set_scientific(True)
            formatter.set_powerlimits((-2, 3))
            ax.yaxis.set_major_formatter(formatter)
        ax.yaxis.get_offset_text().set_fontsize(8)

        params = dict(labelsize=tick_size, direction="in", length=3)
        if tick_color:
            params["color"] = tick_color
        if text_color:
            params["labelcolor"] = text_color
        ax.tick_params(**params)
        for name, label in (("x", xlabel), ("y", ylabel)):
            getattr(ax, f"set_{name}label")(label, fontsize=label_size, labelpad=2)
            if text_color:
                getattr(ax, f"{name}axis").label.set_color(text_color)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        border = ps.get("border_color", "auto") if ps else "auto"
        for side in ("left", "bottom"):
            if border not in ("auto", "", None):
                ax.spines[side].set_color(border)
            if ps:
                ax.spines[side].set_linewidth(ps.get("border_width", 1.0))
        background = ps.get("plot_bg_color", "auto") if ps else "auto"
        if background not in ("auto", "", None):
            ax.set_facecolor(background)

    # ---------- Export ----------

    def write_csv(self, path, files):
        """Save the curves' values: one row per frame (number, file, time)."""
        import os

        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["frame", "file", "time_utc"]
                + [f"{label} [{unit}]" if unit else label for label, unit in zip(self.labels, self.units)]
            )
            for i in range(self.total_frames):
                when = self.times[i]
                writer.writerow(
                    [i + 1, os.path.basename(files[i]) if i < len(files) else "",
                     when.isoformat() if when else ""]
                    + [repr(float(v[i])) if np.isfinite(v[i]) else "" for v in self.values]
                )
        return path

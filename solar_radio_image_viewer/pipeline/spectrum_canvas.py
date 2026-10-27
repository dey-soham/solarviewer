"""
The dynamic spectrum viewer's plots: the spectrum itself (PyQtGraph, with
its time axis, selections and overlays) and the overview strip of the
whole file.
"""

import logging
import os
import re
import warnings
import numpy as np
import numpy.ma as ma
from datetime import datetime, timedelta
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFileDialog,
    QMessageBox,
    QPushButton,
    QInputDialog,
)
from PyQt5 import QtWidgets
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm, Normalize, PowerNorm
from ..ui.styles import palette_for as get_palette
import pyqtgraph as pg
from pyqtgraph import ColorMap

from .spectra_common import _channel_medians
logger = logging.getLogger(__name__)


class _AxisLayout:
    """Where the samples of an axis are: each covers [lo, hi], up to halfway
    to its neighbours (at gaps, and at the ends, half its spacing), and is
    drawn and picked (hover, clicks, masks) there alike.

    An axis evenly spaced, or in evenly spaced stretches with gaps between,
    is drawn as one image per stretch (``segments``). Any other (e.g.
    e-Callisto's irregular channels) is drawn on a fine even grid of cells,
    each showing the sample that covers it (``grid``)."""

    MAX_SEGMENTS = 64
    GAP = 8.0  # a spacing this many times the typical one is a gap
    EVEN = 0.02  # spacing within a stretch varying less than this is even

    def __init__(self, values, n):
        if values is None:
            values = np.arange(n, dtype=np.float64)
        c = np.asarray(values, dtype=np.float64)
        self.centers = c
        self.order = np.argsort(c, kind="stable")
        sc = c[self.order]
        d = np.diff(sc)
        positive = d[d > 0]
        typical = float(np.median(positive)) if positive.size else 1.0
        gap = d > self.GAP * typical
        # Edges, in sorted order: halfway to the neighbours; at the ends and
        # at gaps, half the spacing on the other side
        left = np.r_[np.nan, d]
        right = np.r_[d, np.nan]
        left_ok = np.r_[False, ~gap]
        right_ok = np.r_[~gap, False]
        half = 0.5 * np.where(right_ok, right, np.where(left_ok, left, typical))
        half_left = 0.5 * np.where(left_ok, left, np.where(right_ok, right, typical))
        lo_s = sc - np.where(left_ok, left / 2, half)
        hi_s = sc + np.where(right_ok, right / 2, half_left)
        self.lo_sorted, self.hi_sorted = lo_s, hi_s
        self.lo = np.empty(n)
        self.hi = np.empty(n)
        self.lo[self.order] = lo_s
        self.hi[self.order] = hi_s

        self.segments = self._even_stretches(c)
        self.grid = None
        if self.segments is None:
            self.segments = [(0, n)]
            self.grid = self._grid(lo_s, hi_s, n)

    def _even_stretches(self, c):
        """[(start, stop)] of evenly spaced stretches (in index order), when
        the axis is made of such (monotonic, separated by gaps); else None."""
        n = len(c)
        if n < 3:
            return [(0, n)]
        d = np.diff(c)
        if not (np.all(d > 0) or np.all(d < 0)):
            return None
        a = np.abs(d)
        typical = float(np.median(a))
        starts = [0]
        for i in np.flatnonzero(a > self.GAP * typical):
            starts.append(int(i) + 1)
        segments = [(s, e) for s, e in zip(starts, starts[1:] + [n]) if e > s]
        if len(segments) > self.MAX_SEGMENTS:
            return None
        for s, e in segments:
            steps = a[s : e - 1]
            if steps.size and np.ptp(steps) > self.EVEN * np.median(steps):
                return None
        return segments

    @staticmethod
    def _grid(lo_s, hi_s, n):
        """(sample of each cell or -1, low, high): cells as fine as the
        narrowest sample (at most 4 per sample)."""
        low, high = float(lo_s.min()), float(hi_s.max())
        widths = (hi_s - lo_s)[hi_s > lo_s]
        finest = float(widths.min()) if widths.size else (high - low) / max(n, 1)
        cells = int(min(max(n, np.ceil((high - low) / finest)), 4 * n))
        centers = low + (np.arange(cells) + 0.5) * (high - low) / cells
        k = np.searchsorted(lo_s, centers, "right") - 1
        inside = (k >= 0) & (centers <= hi_s[np.clip(k, 0, None)])
        return np.where(inside, k, -1), low, high

    def parts(self):
        """[(selector, low, high, reversed)]: how to draw the axis, a part
        per image: a slice of the samples (a stretch), or the grid's sample
        index per cell (-1 empty)."""
        if self.grid is not None:
            cells, low, high = self.grid
            return [(np.where(cells >= 0, self.order[np.clip(cells, 0, None)], -1), low, high, False)]
        out = []
        for a, b in self.segments:
            low, high = float(self.lo[a:b].min()), float(self.hi[a:b].max())
            out.append((slice(a, b), low, high, b - a > 1 and self.centers[b - 1] < self.centers[a]))
        return out

    def index(self, x, clip=False):
        """The sample at ``x`` (None in a gap or outside, unless ``clip``:
        then the nearest)."""
        k = int(np.searchsorted(self.lo_sorted, x, "right")) - 1
        if k >= 0 and x <= self.hi_sorted[k]:
            return int(self.order[k])
        if not clip:
            return None
        j = int(np.searchsorted(self.lo_sorted, x))
        near = [i for i in (j - 1, j) if 0 <= i < len(self.order)]
        i = min(near, key=lambda i: abs(self.centers[self.order[i]] - x))
        return int(self.order[i])

    def extent(self, a=0, b=None):
        """(low, high) edges of samples a..b."""
        b = len(self.centers) if b is None else b
        return float(self.lo[a:b].min()), float(self.hi[a:b].max())


class TimeAxisItem(pg.AxisItem):
    """A custom AxisItem that formats MJD seconds into UTC HH:MM:SS strings."""

    def tickStrings(self, values, scale, spacing):
        try:
            # MJD 0 = 1858-11-17
            times = [datetime(1858, 11, 17) + timedelta(seconds=float(v)) for v in values]
        except (OverflowError, ValueError):
            return [f"{v:.0f}" for v in values]
        # With the date when the ticks span more than a day
        days = {t.date() for t in times}
        fmt = "%m-%d %H:%M" if len(days) > 1 else "%H:%M:%S"
        return [t.strftime(fmt) for t in times]


class PyQtGraphSpectrumCanvas(QWidget):
    """
    A PyQtGraph-based canvas for GPU-accelerated dynamic spectrum display.
    Replaces DynamicSpectrumCanvas for much faster pan/zoom/rendering.
    """

    # Signal for cross-section clicks
    crossSectionClicked = pyqtSignal(int, int)  # (time_idx, freq_idx)
    # Signal emitted when cursor moves over the plot: (time_str, freq_str, intensity_val)
    hoverChanged = pyqtSignal(str, str, float)
    # Drift-rate mode: two points clicked, (t1, f1, t2, f2) in view coordinates
    driftMeasured = pyqtSignal(float, float, float, float)
    # Region mode (Mask, Keep): a click, not a drag, at view (x, y)
    regionClicked = pyqtSignal(float, float)
    # Time-pick mode: the time (MJD seconds) and frequency (MHz, NaN if
    # unknown) of the sample clicked
    timePicked = pyqtSignal(float, float)
    # The sample under the cursor: its time (MJD seconds) and frequency (MHz)
    cursorSample = pyqtSignal(float, float)
    # A click (not a drag) on the data: the sample's time and frequency
    samplePicked = pyqtSignal(float, float)

    AXIS_WIDTH = 52  # of the frequency axis (the time bar lines up with it)
    AXIS_HEIGHT = 30  # of the time axis

    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent

        import logging

        self.logger = logging.getLogger("PyQtGraphSpectrumCanvas")
        self.logger.setLevel(logging.DEBUG)

        # Internal data references
        self._data = None  # Float array with NaNs
        self._time_axis = None
        self._freq_axis = None
        self._filename = ""

        # Visualization parameters
        self._scale_mode = "Linear"
        self._gamma = 1.0
        self._vmin = None
        self._vmax = None
        self._vmin_manual = None
        self._vmax_manual = None
        self._cmap = "inferno"
        self._smart_scale = "0.5-99.5%"

        # Mode flags
        self.roi_active = False
        self.roi_callback = None
        self.cross_section_active = False
        self.drift_active = False
        self.time_pick_active = False
        self._popups = []  # profile windows (closed with the viewer)
        self._drift_points = []
        self._drift_items = []
        self._time_marker = None
        self._extent = None
        self._layouts = None  # (_AxisLayout time, _AxisLayout freq) of the data
        self._overlay = None  # flagged share of each sample (0..1), tinted
        self._overlay_items = []
        self._share = None  # (flagged share per channel, frequencies), beside
        self._boxes = []  # outlines (Keep regions)
        self._extra_items = []  # images of further stretches (uneven axes)
        self._auto_downsample = True  # data extent the view was last fitted to
        self._image_is_light = False  # the crosshair dark on it if so

        # Setup PyQtGraph
        self._setup_pyqtgraph()
        self._setup_layout()

    def _setup_pyqtgraph(self):
        """Configure PyQtGraph settings for optimal performance."""
        pg.setConfigOptions(
            antialias=False,  # Faster rendering
            useOpenGL=True,  # GPU acceleration
            enableExperimental=False,
        )

    def _setup_layout(self):
        # Reuse existing layout if the widget already has one (avoids
        # "QLayout already has a parent" warning on re-open)
        layout = self.layout()
        if layout is None:
            layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # The plot, then a 12px gap and the colorbar: a 6px strip beside the
        # image with its tick values (a plot of its own with a bottom axis
        # as tall as the spectrum's, so the two are level), then the flagged
        # share of each channel when the automatic flags are on
        self.graphics_widget = pg.GraphicsLayoutWidget()
        self.graphics_widget.setFocusPolicy(Qt.StrongFocus)  # (for the arrow keys)
        self.graphics_widget.setBackground("#161826")
        grid = self.graphics_widget.ci.layout
        # (room above for the top tick labels, half of each sticking out)
        grid.setContentsMargins(0, 8, 0, 0)
        grid.setHorizontalSpacing(0)
        grid.setVerticalSpacing(0)

        # Main plot with custom time axis; no title (the file's name is in
        # the menu bar) and no autoscale button (Reset view in the toolbar)
        self.plot_item = self.graphics_widget.addPlot(row=0, col=0)
        self.plot_item.hideButtons()
        self.plot_item.setMenuEnabled(False)
        self.plot_item.setLabel("left", "MHz")
        self.plot_item.getAxis("left").setWidth(self.AXIS_WIDTH)
        self.plot_item.getAxis("bottom").setHeight(self.AXIS_HEIGHT)

        # Image item for the spectrum data
        self.image_item = pg.ImageItem()
        # Each screen pixel shows the mean of its samples (View menu)
        self.image_item.setAutoDownsample(True)
        self.plot_item.addItem(self.image_item)

        # Store the current LUT
        self._current_lut = None
        self._transparent_lut = None
        self._build_lut(self._cmap)

        grid.setColumnSpacing(0, 12)
        self.colorbar_plot = self.graphics_widget.addPlot(row=0, col=1)
        self.colorbar_plot.hideButtons()
        self.colorbar_plot.setMenuEnabled(False)
        self.colorbar_plot.hideAxis("left")
        self.colorbar_plot.showAxis("right")
        self.colorbar_plot.getAxis("bottom").setHeight(self.AXIS_HEIGHT)
        self.colorbar_plot.getAxis("bottom").setStyle(showValues=False, tickLength=0)
        self.colorbar_plot.getAxis("right").setWidth(44)
        self.colorbar_plot.setFixedWidth(6 + 44)
        self.colorbar_vb = self.colorbar_plot.vb
        self.colorbar_vb.setMouseEnabled(x=False, y=False)
        self.colorbar_vb.setXRange(0, 1, padding=0)

        # Create vertical gradient image for colorbar
        self.colorbar_image = pg.ImageItem()
        self.colorbar_vb.addItem(self.colorbar_image)

        # Initialize colorbar gradient immediately
        # Use (1, 256) shape - ImageItem displays rows as X and cols as Y
        gradient_data = np.arange(256, dtype=np.uint8).reshape(1, 256)
        self.colorbar_image.setImage(gradient_data, autoLevels=False)
        self.colorbar_image.setLookupTable(self._current_lut)
        self.colorbar_image.setLevels([0, 255])
        self.colorbar_image.setRect(pg.QtCore.QRectF(0, 0, 1, 255))
        self.colorbar_vb.setYRange(0, 255, padding=0)

        # Colorbar axis labels
        self.colorbar_axis = self.colorbar_plot.getAxis("right")

        # The flagged share of each channel (automatic RFI flags), when on
        self.flag_plot = self.graphics_widget.addPlot(row=0, col=2)
        self.flag_plot.hideButtons()
        self.flag_plot.setYLink(self.plot_item)
        self.flag_plot.hideAxis("left")
        self.flag_plot.getAxis("bottom").setHeight(self.AXIS_HEIGHT)
        self.flag_plot.setXRange(0, 100, padding=0)
        # (90 px wide: only the ends labelled)
        self.flag_plot.getAxis("bottom").setTicks([[(0, "0"), (100, "100% flagged")], []])
        self.flag_plot.getAxis("bottom").setStyle(hideOverlappingLabels=False)
        self.flag_plot.setMouseEnabled(x=False, y=False)
        self.flag_plot.setMaximumWidth(0)
        self.flag_plot.hide()

        # The date under the time ticks, at the right ("UTC · 2022-03-28")
        self.date_label = pg.LabelItem("", justify="right")
        self.date_label.setMaximumHeight(18)
        self.graphics_widget.addItem(self.date_label, row=1, col=0)

        # ROI for masking
        self.roi_rect = None

        # The crosshair at the cursor and a chip beside it with the time,
        # frequency and value there
        self.cross_v = pg.InfiniteLine(angle=90, movable=False)
        self.cross_h = pg.InfiniteLine(angle=0, movable=False)
        self.chip = pg.TextItem(anchor=(0, 0))
        for item in (self.cross_v, self.cross_h, self.chip):
            item.setZValue(50)
            self.plot_item.addItem(item, ignoreBounds=True)
            item.hide()

        # Connect mouse events
        self.image_item.scene().sigMouseMoved.connect(self._on_mouse_move)
        self.image_item.scene().sigMouseClicked.connect(self._on_mouse_click)

        layout.addWidget(self.graphics_widget)

    def _build_lut(self, cmap_name):
        """Build a 256-color LUT from a matplotlib colormap."""
        try:
            import matplotlib.cm as cm
            import numpy as np

            mpl_cmap = cm.get_cmap(cmap_name)
            lut = np.zeros((256, 4), dtype=np.ubyte)
            for i in range(256):
                r, g, b, a = mpl_cmap(i / 255.0)
                lut[i] = [int(r * 255), int(g * 255), int(b * 255), int(a * 255)]
            self._current_lut = lut

            # Pre-calculate transparent LUT: index 0 is transparent, 1-255 is colormap
            trans_lut = np.zeros((256, 4), dtype=np.ubyte)
            trans_lut[0] = [0, 0, 0, 0]  # Transparent
            for i in range(1, 256):
                # Map 1-255 to colormap indices 0-255
                idx = int((i - 1) * 255.0 / 254.0)
                trans_lut[i] = lut[idx]
            self._transparent_lut = trans_lut

        except Exception as e:
            self.logger.warning(f"Failed to build LUT for {cmap_name}: {e}")
            # Fallback to grayscale
            gray = np.array([[i, i, i, 255] for i in range(256)], dtype=np.ubyte)
            self._current_lut = gray

            trans_gray = np.zeros((256, 4), dtype=np.ubyte)
            trans_gray[0] = [0, 0, 0, 0]
            for i in range(1, 256):
                idx = int((i - 1) * 255.0 / 254.0)
                trans_gray[i] = gray[idx]
            self._transparent_lut = trans_gray

    def _get_colormap(self, cmap_name):
        """Get a PyQtGraph colormap from a matplotlib colormap name."""
        try:
            import matplotlib.cm as cm

            mpl_cmap = cm.get_cmap(cmap_name)
            colors = [mpl_cmap(i) for i in np.linspace(0, 1, 256)]
            colors_255 = [
                (int(c[0] * 255), int(c[1] * 255), int(c[2] * 255), int(c[3] * 255))
                for c in colors
            ]
            return ColorMap(pos=np.linspace(0, 1, 256), color=colors_255)
        except Exception as e:
            self.logger.warning(f"Failed to load colormap {cmap_name}: {e}")
            return pg.colormap.get("viridis")

    def _update_colorbar(self):
        """Update the colorbar gradient."""
        if not hasattr(self, "colorbar_image") or self._current_lut is None:
            return
        try:
            # Use (1, 256) shape - ImageItem displays rows as X and cols as Y
            gradient_data = np.arange(256, dtype=np.uint8).reshape(1, 256)
            self.colorbar_image.setImage(gradient_data, autoLevels=False)
            self.colorbar_image.setLookupTable(self._current_lut)
            self.colorbar_image.setLevels([0, 255])
        except Exception as e:
            self.logger.warning(f"Failed to update colorbar: {e}")

    def _update_colorbar_labels(self):
        """Update colorbar Y axis to show vmin/vmax range with correct scale."""
        if not hasattr(self, "colorbar_vb") or self._vmin is None:
            return
        try:
            vmin, vmax = self._vmin, self._vmax

            # Generate tick values based on scale mode
            if self._scale_mode == "Log":
                # For log scale, use log-spaced ticks
                if vmin > 0:
                    log_min = np.log10(max(vmin, 1e-10))
                    log_max = np.log10(vmax)
                    log_ticks = np.linspace(log_min, log_max, 3)
                    tick_values = 10**log_ticks
                else:
                    # If vmin <= 0, fallback to linear
                    tick_values = np.linspace(vmin, vmax, 5)
            elif self._scale_mode == "Sqrt":
                # For sqrt scale, use sqrt-spaced ticks
                sqrt_ticks = np.linspace(np.sqrt(max(0, vmin)), np.sqrt(vmax), 5)
                tick_values = sqrt_ticks**2
            elif self._scale_mode == "Gamma":
                # For gamma scale, use gamma-spaced ticks
                gamma_ticks = np.linspace(0, 1, 5) ** (1 / max(self._gamma, 0.1))
                tick_values = vmin + gamma_ticks * (vmax - vmin)
            else:  # Linear
                tick_values = np.linspace(vmin, vmax, 5)

            # Create custom tick labels for the colorbar axis
            # Map raw values (vmin..vmax) to normalized positions (0..255)
            ticks = []
            for v in tick_values:
                # Calculate the position in normalized [0, 255] space
                norm_pos = (
                    float((v - vmin) / (vmax - vmin) * 255.0) if vmax != vmin else 0.0
                )
                ticks.append((norm_pos, f"{v:.3g}"))

            self.colorbar_axis.setTicks([ticks])

            # Set the colorbar rect and range in NORMALIZED [0, 255] space
            # This prevents overflow in ViewBox if vmin/vmax are very large
            self.colorbar_image.setRect(pg.QtCore.QRectF(0, 0, 1, 255))
            self.colorbar_vb.setYRange(0, 255, padding=0)
        except Exception as e:
            self.logger.warning(f"Failed to update colorbar labels: {e}")

    def set_data(self, data, time_axis=None, freq_axis=None, filename=""):
        """Set data and optional time and frequency axes."""
        self._data = data
        self._time_axis = time_axis
        self._freq_axis = freq_axis
        axes = (time_axis, freq_axis) if time_axis is not None and freq_axis is not None else (None, None)
        self._layouts = (
            _AxisLayout(axes[0], data.shape[0]),
            _AxisLayout(axes[1], data.shape[1]),
        )
        if filename:
            self._filename = filename

    def draw_spectrum(self, fast=False):
        """Render the dynamic spectrum using PyQtGraph (GPU accelerated)."""
        if self._data is None:
            return

        self._vmin, self._vmax = self._limits()

        # Push limits to UI
        if hasattr(self.main_window, "vminEntry") and self.main_window is not None:
            if not self.main_window.vminEntry.hasFocus():
                self.main_window.vminEntry.setText(f"{self._vmin:.4g}")
            if not self.main_window.vmaxEntry.hasFocus():
                self.main_window.vmaxEntry.setText(f"{self._vmax:.4g}")
        levels = getattr(self.main_window, "levels", None)
        if levels is not None:
            levels.set_range(self._vmin, self._vmax)

        image = self._to_lut_indices(self._data)
        self._image_is_light = self._mostly_light(image)
        time_layout, freq_layout = self._layouts
        blocks = [(t, f) for t in time_layout.parts() for f in freq_layout.parts()]
        items = self._image_items(len(blocks))
        for item, ((tsel, x0, x1, tflip), (fsel, y0, y1, fflip)) in zip(items, blocks):
            block = self._take(image, tsel, 0)
            block = self._take(block, fsel, 1)
            # Shown with time and frequency increasing
            if tflip:
                block = block[::-1, :]
            if fflip:
                block = block[:, ::-1]
            item.setImage(block, autoLevels=False)
            item.setLookupTable(self._transparent_lut)
            item.setLevels([0, 255])
            item.setRect(pg.QtCore.QRectF(x0, y0, x1 - x0, y1 - y0))
        self._draw_overlay(blocks)

        (x_low, x_high), (y_low, y_high) = time_layout.extent(), freq_layout.extent()
        width, height = x_high - x_low, y_high - y_low
        # Other data (a page, a file): show all of it; the same data redrawn
        # (a mask, a scale) keeps the view
        extent = (x_low, width, y_low, height)
        refit = extent != self._extent
        self._extent = extent
        if self._time_axis is not None and self._freq_axis is not None:
            # Set zoom limits to prevent zooming beyond data
            # Use explicit float casting to avoid numpy.float64 issues in Py3.13
            self.plot_item.setLimits(
                xMin=float(x_low),
                xMax=float(x_low + width),
                yMin=float(y_low),
                yMax=float(y_low + height),
                minXRange=min(1.0, float(width)),  # at most 1 second
                minYRange=min(1.0, float(height)),  # at most 1 MHz
            )

            # Format time axis with UTC labels
            self._setup_time_axis()
        if refit:
            self.plot_item.vb.setRange(
                xRange=(x_low, x_low + width), yRange=(y_low, y_low + height), padding=0
            )

        # Update colorbar with current colormap and values
        self._update_colorbar()
        self._update_colorbar_labels()

        self._apply_canvas_theme_colors()
        self._align_share_plot()

    # Values sampled for the percentile limits (enough for stable limits)
    LIMIT_SAMPLES = 1_000_000
    # Values converted to LUT indices at a time (bounds temporary memory)
    CHUNK = 1_000_000

    def _limits(self):
        """(vmin, vmax) for the scaling option, from the finite values (a
        random sample of them for percentiles on large data)."""
        data = self._data
        if self._smart_scale == "Manual" and self._vmin_manual is not None and self._vmax_manual is not None:
            return self._vmin_manual, self._vmax_manual
        flat = data.reshape(-1)
        percent = {
            "0.1-99.9%": (0.1, 99.9),
            "0.5-99.5%": (0.5, 99.5),
            "1-99%": (1, 99),
            "5-95%": (5, 95),
        }.get(self._smart_scale)
        if percent is None:  # 0-100% (and Manual without values)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)  # all NaN
                low, high = np.nanmin(flat), np.nanmax(flat)
            return (0.0, 1.0) if not np.isfinite(low) else (float(low), float(high))
        if flat.size > self.LIMIT_SAMPLES:
            pick = np.random.default_rng(0).integers(0, flat.size, self.LIMIT_SAMPLES)
            flat = flat[pick]
        flat = flat[np.isfinite(flat)]
        if flat.size == 0:
            return 0.0, 1.0
        low, high = np.percentile(flat, percent)
        return float(low), float(high)

    def _to_lut_indices(self, data):
        """``data`` scaled to LUT indices: 1-255 for values, 0 (transparent)
        for NaN; converted in blocks, so the only full-size array is the
        uint8 result."""
        flat = data.reshape(-1)
        out = np.empty(flat.size, np.uint8)
        for start in range(0, flat.size, self.CHUNK):
            block = flat[start : start + self.CHUNK]
            nan = ~np.isfinite(block)
            norm = self._apply_scaling(np.where(nan, self._vmin, block))
            # Scale to [1, 255] for LUT indexing (reserve 0 for NaN)
            indices = (norm * 254.0 + 1.5).astype(np.uint8)
            indices[nan] = 0
            out[start : start + self.CHUNK] = indices
        return out.reshape(data.shape)

    def _mostly_light(self, indices):
        """Whether the image as shown is mostly light (its mean luminance
        over half): the crosshair is then dark, else light, so it differs
        from the image in brightness on any colormap and with any colour
        vision (as tools.preview.contrast_colors does for images)."""
        lut = self._current_lut
        if lut is None or indices.size == 0:
            return False
        step = max(1, int(np.ceil(max(indices.shape) / 128)))
        sample = indices[::step, ::step].reshape(-1)
        sample = sample[sample > 0]  # (0: NaN, not drawn)
        if sample.size == 0:
            return False
        rgb = np.asarray(lut, np.float64)[sample, :3] / 255.0
        return float((rgb @ [0.2126, 0.7152, 0.0722]).mean()) > 0.5

    @staticmethod
    def _take(image, selector, axis):
        """``image``'s samples along ``axis``: a slice (a stretch) or the
        grid's sample per cell (-1: empty, transparent)."""
        if isinstance(selector, slice):
            return image[selector, :] if axis == 0 else image[:, selector]
        out = np.take(image, np.clip(selector, 0, None), axis=axis)
        empty = selector < 0
        if empty.any():
            if axis == 0:
                out[empty, :] = 0
            else:
                out[:, empty] = 0
        return out

    def set_overlay(self, overlay):
        """A tint over the samples: each one's flagged share (0..1, the
        data's shape), or None."""
        self._overlay = overlay

    def _draw_overlay(self, blocks):
        for item in self._overlay_items:
            self.plot_item.removeItem(item)
        self._overlay_items = []
        overlay = self._overlay
        if overlay is None or self._data is None or overlay.shape != self._data.shape:
            return
        # 0: clear .. 255: fully flagged (a cyan tint, more opaque the more flagged)
        lut = np.zeros((256, 4), np.ubyte)
        lut[:, :3] = (0, 229, 255)
        lut[:, 3] = (np.arange(256) * 0.65).astype(np.ubyte)
        values = np.clip(np.nan_to_num(overlay) * 255, 0, 255).astype(np.uint8)
        for (tsel, x0, x1, tflip), (fsel, y0, y1, fflip) in blocks:
            block = self._take(self._take(values, tsel, 0), fsel, 1)
            if tflip:
                block = block[::-1, :]
            if fflip:
                block = block[:, ::-1]
            item = pg.ImageItem(block, autoLevels=False)
            item.setLookupTable(lut)
            item.setLevels([0, 255])
            item.setAutoDownsample(self._auto_downsample)
            item.setRect(pg.QtCore.QRectF(x0, y0, x1 - x0, y1 - y0))
            item.setZValue(5)
            self.plot_item.addItem(item)
            self._overlay_items.append(item)

    def set_channel_share(self, share, freqs):
        """The flagged share of each channel (beside the spectrum, sharing
        its frequency axis), or None to hide it."""
        self._share = None if share is None else (np.asarray(share), np.asarray(freqs))
        plot = self.flag_plot
        plot.clear()
        if self._share is None or freqs is None:
            plot.hide()
            plot.setMaximumWidth(0)
            return
        order = np.argsort(self._share[1])
        plot.plot(
            self._share[0][order] * 100, self._share[1][order],
            pen=pg.mkPen(get_palette(getattr(self.main_window, "current_theme", "dark"))["highlight"], width=1.5),
        )
        plot.setMaximumWidth(90)
        plot.show()
        self._align_share_plot()

    def _align_share_plot(self):
        """The strip's plot area level with the spectrum's: a bottom axis
        of the same height, so a frequency is at the same height in both."""
        if self.flag_plot.isVisible():
            self.flag_plot.getAxis("bottom").setHeight(self.AXIS_HEIGHT)

    def set_boxes(self, boxes, color="#3ddc84"):
        """Dashed outlines of view rectangles (x0, x1, y0, y1)."""
        for item in self._boxes:
            self.plot_item.vb.removeItem(item)
        self._boxes = []
        for x0, x1, y0, y1 in boxes:
            item = QtWidgets.QGraphicsRectItem(x0, y0, x1 - x0, y1 - y0)
            item.setPen(pg.mkPen(color, width=1.5, style=Qt.DashLine))
            item.setZValue(6)
            self.plot_item.vb.addItem(item)
            self._boxes.append(item)

    def set_selected_box(self, box):
        """A solid highlighted outline of the selected region (x0, x1, y0,
        y1), or None."""
        if getattr(self, "_selected_item", None) is not None:
            self.plot_item.vb.removeItem(self._selected_item)
            self._selected_item = None
        if box is not None:
            x0, x1, y0, y1 = box
            item = QtWidgets.QGraphicsRectItem(x0, y0, x1 - x0, y1 - y0)
            item.setPen(pg.mkPen("#ffcc00", width=2.5))
            item.setZValue(7)
            self.plot_item.vb.addItem(item)
            self._selected_item = item

    def _image_items(self, count):
        """``count`` image items: the main one and further ones (removed
        when no longer needed)."""
        while len(self._extra_items) < count - 1:
            item = pg.ImageItem()
            item.setAutoDownsample(self._auto_downsample)
            self.plot_item.addItem(item)
            self._extra_items.append(item)
        while len(self._extra_items) > count - 1:
            self.plot_item.removeItem(self._extra_items.pop())
        return [self.image_item] + self._extra_items

    def set_auto_downsample(self, enabled):
        """Each screen pixel the mean of its samples (or a subset of them)."""
        self._auto_downsample = enabled
        for item in [self.image_item] + self._extra_items:
            item.setAutoDownsample(enabled)

    def _index_at(self, x, y, clip=False):
        """(time index, frequency index) of the sample at view position
        (x, y); None outside the data or in a gap, unless ``clip``."""
        time_layout, freq_layout = self._layouts
        ti, fi = time_layout.index(x, clip), freq_layout.index(y, clip)
        if ti is None or fi is None:
            return None
        return ti, fi

    def _sample_labels(self, ti, fi):
        """(time, frequency) labels of sample (ti, fi)."""
        if self._time_axis is None or self._freq_axis is None:
            return f"idx {ti}", f"idx {fi}"
        try:
            utc = datetime(1858, 11, 17) + timedelta(seconds=float(self._time_axis[ti]))
            time_label = utc.strftime("%H:%M:%S.%f")[:-4]
        except (OverflowError, ValueError):
            time_label = f"{self._time_axis[ti]:.2f}"
        return time_label, f"{self._freq_axis[fi]:.3f} MHz"

    def _apply_scaling(self, data):
        """Apply intensity scaling (Linear/Log/Sqrt/Gamma) and return normalized float [0, 1]."""
        vmin, vmax = self._vmin, self._vmax
        if vmax == vmin:
            vmax = vmin + 1e-9

        # Clip and normalize to [0, 1]
        data_clipped = np.clip(data, vmin, vmax)
        data_norm = (data_clipped - vmin) / (vmax - vmin)

        if self._scale_mode == "Log":
            # Avoid log(0)
            data_norm = np.clip(data_norm, 1e-10, 1)
            data_norm = np.log10(data_norm * 9 + 1) / np.log10(10)
        elif self._scale_mode == "Sqrt":
            data_norm = np.sqrt(data_norm)
        elif self._scale_mode == "Gamma":
            data_norm = np.power(data_norm, self._gamma)

        return data_norm

    def _setup_time_axis(self):
        """Configure time axis with custom TimeAxisItem for automatic UTC labels."""
        if self._time_axis is None or len(self._time_axis) < 2:
            return

        try:
            # Check if we already have a TimeAxisItem
            ax = self.plot_item.getAxis("bottom")
            if not isinstance(ax, TimeAxisItem):
                time_axis = TimeAxisItem(orientation="bottom")
                self.plot_item.setAxisItems({"bottom": time_axis})
                ax = time_axis

            # Format the label with the reference date
            dt = datetime(1858, 11, 17) + timedelta(seconds=float(self._time_axis[0]))
            start_date = dt.strftime("%Y-%m-%d")

            ax.setHeight(self.AXIS_HEIGHT)
            ax.enableAutoSIPrefix(False)
            self._date_text = f"UTC · {start_date}"
            self._style_date_label()

        except Exception as e:
            self.logger.warning(f"Failed to setup time axis: {e}")

    def set_scale_mode(self, mode):
        """Set the intensity scaling mode."""
        self._scale_mode = mode
        self.draw_spectrum()

    def set_gamma(self, gamma):
        """Set gamma value for Gamma scaling."""
        self._gamma = gamma
        if self._scale_mode == "Gamma":
            self.draw_spectrum()

    def set_cmap(self, cmap_name):
        """Set the colormap."""
        self._cmap = cmap_name
        self._build_lut(cmap_name)  # Rebuild LUT
        self._update_colorbar()  # Update colorbar gradient
        if self._data is not None:
            # Re-apply LUT to current image
            for item in [self.image_item] + self._extra_items:
                item.setLookupTable(self._transparent_lut)

    def set_smart_scale(self, scale_option):
        """Set the percentile scale option."""
        self._smart_scale = scale_option
        self.draw_spectrum()

    def set_manual_range(self, vmin, vmax):
        """Set manual vmin/vmax values."""
        self._vmin_manual = vmin
        self._vmax_manual = vmax
        if self._smart_scale == "Manual":
            self.draw_spectrum()

    def get_normalization(self, vmin, vmax):
        """Compatibility method - returns normalize object for comparison plots."""
        if self._scale_mode == "Log":
            return LogNorm(vmin=max(vmin, 1e-12), vmax=vmax)
        elif self._scale_mode == "Sqrt":
            return PowerNorm(gamma=0.5, vmin=vmin, vmax=vmax)
        elif self._scale_mode == "Gamma":
            return PowerNorm(gamma=self._gamma, vmin=vmin, vmax=vmax)
        else:
            return Normalize(vmin=vmin, vmax=vmax)

    # -------------------------- ROI Selector ------------------------------------
    def enable_roi_selector(self, enable, callback=None, label="MASK"):
        """Enable or disable ROI selection mode: a drag draws a region
        (``callback``), a click emits regionClicked."""
        self.roi_active = enable
        self.roi_callback = callback

        if enable:
            self.setCursor(Qt.CrossCursor)

            # Disable default pan/zoom so drag creates ROI instead
            self.plot_item.vb.setMouseEnabled(x=False, y=False)

            # Initialize selection state
            self._roi_start = None
            self._roi_rect_item = None

            # Save original mouseDragEvent before overriding
            self._orig_mouseDragEvent = self.plot_item.vb.mouseDragEvent

            # Disconnect cross-section click handler during ROI mode
            try:
                self.plot_item.scene().sigMouseClicked.disconnect(self._on_mouse_click)
            except Exception:
                pass
            try:
                self.plot_item.scene().sigMouseClicked.connect(self._roi_click)
            except TypeError:
                pass

            # Override mouseDragEvent — this is what PyQtGraph actually calls
            # during click-and-drag operations (not mouseMoveEvent)
            self.plot_item.vb.mouseDragEvent = self._roi_drag_event
        else:
            self.setCursor(Qt.ArrowCursor)

            # Re-enable pan/zoom
            self.plot_item.vb.setMouseEnabled(x=True, y=True)

            # Restore normal mouse behavior
            try:
                self.plot_item.scene().sigMouseClicked.disconnect(self._roi_click)
            except TypeError:
                pass
            try:
                self.plot_item.scene().sigMouseClicked.connect(self._on_mouse_click)
            except Exception:
                pass

            # Restore saved original handler
            if hasattr(self, "_orig_mouseDragEvent"):
                self.plot_item.vb.mouseDragEvent = self._orig_mouseDragEvent

            # Remove visual rects if exist
            if hasattr(self, "_roi_rect_item") and self._roi_rect_item is not None:
                self.plot_item.vb.removeItem(self._roi_rect_item)
                self._roi_rect_item = None
            if hasattr(self, "_roi_border_item") and self._roi_border_item is not None:
                self.plot_item.vb.removeItem(self._roi_border_item)
                self._roi_border_item = None

    def _roi_click(self, event):
        """A plain left click in region mode (a drag never comes here)."""
        if self.roi_active and event.button() == Qt.LeftButton and not event.double():
            point = self.plot_item.vb.mapSceneToView(event.scenePos())
            self.regionClicked.emit(point.x(), point.y())

    def _roi_drag_event(self, ev, axis=None):
        """Handle the full drag lifecycle for ROI selection.

        PyQtGraph's ViewBox calls mouseDragEvent (not mouseMoveEvent) for
        click-and-drag operations.  The event has:
          - ev.isStart()  → first call of the drag
          - ev.isFinish() → final call (button released)
          - ev.scenePos() → current scene position
          - ev.buttonDownScenePos() → scene position where button was pressed
        """
        if not self.roi_active:
            ev.ignore()
            return

        ev.accept()

        # Current mouse position in data (view) coordinates
        pos = self.plot_item.vb.mapSceneToView(ev.scenePos())

        if ev.isStart():
            # Use buttonDownScenePos to get the *exact* click location.
            # isStart() fires only after the mouse has moved past a drag
            # threshold, so ev.scenePos() would be offset from the click.
            start_pos = self.plot_item.vb.mapSceneToView(ev.buttonDownScenePos())
            self._roi_start = (start_pos.x(), start_pos.y())

            # Remove any stale rect
            if self._roi_rect_item is not None:
                self.plot_item.vb.removeItem(self._roi_rect_item)

            # Create two rects for a "Neon Cyan" effect
            self._roi_border_item = QtWidgets.QGraphicsRectItem(
                start_pos.x(), start_pos.y(), 0, 0
            )
            # Deep navy thick border for glow/contrast
            self._roi_border_item.setPen(pg.mkPen("#001133", width=4.5))

            self._roi_rect_item = QtWidgets.QGraphicsRectItem(
                start_pos.x(), start_pos.y(), 0, 0
            )
            # Neon Cyan main line
            self._roi_rect_item.setPen(pg.mkPen("#00f2ff", width=2.5))

            # Clip the ViewBox so child items don't paint outside
            from PyQt5.QtWidgets import QGraphicsItem

            self.plot_item.vb.setFlag(QGraphicsItem.ItemClipsChildrenToShape, True)

            self.plot_item.vb.addItem(self._roi_border_item)
            self.plot_item.vb.addItem(self._roi_rect_item)

        if self._roi_start is None:
            return

        x0, y0 = self._roi_start
        x1, y1 = pos.x(), pos.y()

        # Clip coordinates to the current view range so the rect
        # never extends beyond the axes
        view_range = self.plot_item.vb.viewRange()
        x1 = max(view_range[0][0], min(view_range[0][1], x1))
        y1 = max(view_range[1][0], min(view_range[1][1], y1))

        # Update visual rectangle (handles any drag direction)
        rx = min(x0, x1)
        ry = min(y0, y1)
        rw = abs(x1 - x0)
        rh = abs(y1 - y0)

        if self._roi_rect_item is not None:
            self._roi_rect_item.setRect(rx, ry, rw, rh)
        if hasattr(self, "_roi_border_item") and self._roi_border_item is not None:
            self._roi_border_item.setRect(rx, ry, rw, rh)

        if ev.isFinish():
            # --- Drag finished: process the selection ---
            self._roi_start = None

            # Remove visual rects
            if self._roi_rect_item is not None:
                self.plot_item.vb.removeItem(self._roi_rect_item)
                self._roi_rect_item = None
            if hasattr(self, "_roi_border_item") and self._roi_border_item is not None:
                self.plot_item.vb.removeItem(self._roi_border_item)
                self._roi_border_item = None

            # Ensure proper order
            if x0 > x1:
                x0, x1 = x1, x0
            if y0 > y1:
                y0, y1 = y1, y0

            self._process_roi_selection(x0, x1, y0, y1)

    def _process_roi_selection(self, x0, x1, y0, y1):
        """Process the ROI selection and call callback."""
        if self._data is None or self.roi_callback is None:
            return

        try:
            # Corners to samples (either axis may decrease)
            ta, fa = self._index_at(x0, y0, clip=True)
            tb, fb = self._index_at(x1, y1, clip=True)
            idx_time_min, idx_time_max = sorted((ta, tb))
            idx_freq_min, idx_freq_max = sorted((fa, fb))

            # Trigger callback
            self.roi_callback(idx_time_min, idx_time_max, idx_freq_min, idx_freq_max)

        except Exception as e:
            self.logger.error(f"ROI selection error: {e}")

    # --------------------- Cross-Section Mode -----------------------------------
    def enable_cross_section(self, enable):
        """Enable or disable cross-section mode."""
        self.cross_section_active = enable

        if enable:
            self.setCursor(Qt.CrossCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    # --------------------- Drift Rate / Time Marker -----------------------------
    def enable_drift(self, enable):
        """Drift-rate mode: click the start and the end of a burst."""
        self.drift_active = enable
        self._drift_points = []
        if enable:
            self.setCursor(Qt.CrossCursor)
        else:
            self.setCursor(Qt.ArrowCursor)
            self._clear_drift()

    def _clear_drift(self):
        for item in self._drift_items:
            self.plot_item.vb.removeItem(item)
        self._drift_items = []

    def _drift_click(self, event):
        point = self.plot_item.vb.mapSceneToView(event.scenePos())
        if self._index_at(point.x(), point.y()) is None:
            return  # outside the data
        if len(self._drift_points) != 1:
            # A new measurement
            self._clear_drift()
            self._drift_points = []
        self._drift_points.append((point.x(), point.y()))
        (t1, f1) = self._drift_points[0]
        if len(self._drift_points) == 1:
            dot = pg.ScatterPlotItem([t1], [f1], size=9, brush=pg.mkBrush("#00f2ff"), pen=None)
            self.plot_item.vb.addItem(dot)
            self._drift_items.append(dot)
            return
        (t2, f2) = self._drift_points[1]
        line = pg.PlotDataItem(
            [t1, t2], [f1, f2], pen=pg.mkPen("#00f2ff", width=2.5),
            symbol="o", symbolSize=9, symbolBrush="#00f2ff", symbolPen=None,
        )
        self.plot_item.vb.addItem(line)
        self._drift_items.append(line)
        self.driftMeasured.emit(t1, f1, t2, f2)

    def mark_time(self, t):
        """A dashed line at time ``t`` (None removes it)."""
        if self._time_marker is not None:
            self.plot_item.vb.removeItem(self._time_marker)
            self._time_marker = None
        if t is not None:
            self._time_marker = pg.InfiniteLine(
                pos=t, angle=90, pen=pg.mkPen("#00f2ff", width=1.5, style=Qt.DashLine)
            )
            self.plot_item.vb.addItem(self._time_marker)

    def center_on(self, t):
        """Center the view on time ``t``, at the current zoom (at most five
        minutes wide when the whole data is shown)."""
        (x0, x1), _ = self.plot_item.vb.viewRange()
        width = x1 - x0
        if self._extent is not None and width >= 0.99 * self._extent[1]:
            width = min(width, 300.0)
        self.plot_item.vb.setXRange(t - width / 2, t + width / 2, padding=0)

    def enable_time_pick(self, enable):
        """Time-pick mode: a click gives the time of the sample under it."""
        self.time_pick_active = enable
        self.setCursor(Qt.CrossCursor if enable else Qt.ArrowCursor)

    def _on_mouse_click(self, event):
        """Handle mouse clicks for cross-section, drift-rate and time-pick modes."""
        if (
            event.button() == Qt.LeftButton
            and self._data is not None
            and self._time_axis is not None
            and self._freq_axis is not None
        ):
            point = self.plot_item.vb.mapSceneToView(event.scenePos())
            sample = self._index_at(point.x(), point.y())
            if sample is not None:
                self.samplePicked.emit(
                    float(self._time_axis[sample[0]]), float(self._freq_axis[sample[1]])
                )
        if self.drift_active and self._data is not None:
            self._drift_click(event)
            return
        if self.time_pick_active and self._data is not None and self._time_axis is not None:
            point = self.plot_item.vb.mapSceneToView(event.scenePos())
            sample = self._index_at(point.x(), point.y())
            if sample is not None:
                t = float(self._time_axis[sample[0]])
                f = float(self._freq_axis[sample[1]]) if self._freq_axis is not None else float("nan")
                self.mark_time(t)
                self.timePicked.emit(t, f)
            return
        if not self.cross_section_active or self._data is None:
            return

        try:
            pos = event.scenePos()
            mouse_point = self.plot_item.vb.mapSceneToView(pos)
            x, y = mouse_point.x(), mouse_point.y()

            sample = self._index_at(x, y)
            if sample is None:
                return
            time_idx, freq_idx = sample
            time_label, freq_label = self._sample_labels(time_idx, freq_idx)

            # Show cross-section dialog
            options = [
                f"Time slice at {freq_label}",
                f"Freq slice at {time_label}",
            ]
            choice, ok = QInputDialog.getItem(
                None, "Cross Section", "Which slice to plot?", options, 0, False
            )
            if not ok:
                return

            if choice.startswith("Time slice"):
                self._plot_1d_time(self._data[:, freq_idx], freq_label)
            else:
                self._plot_1d_freq(self._data[time_idx, :], time_label)

        except Exception as e:
            self.logger.error(f"Cross-section click error: {e}")

    def _plot_1d_time(self, data_slice, freq_label):
        """The light curve at one frequency, in its own window."""
        self._show_profile(data_slice, freq_label, along_time=True)

    def _plot_1d_freq(self, data_slice, time_label):
        """The spectrum at one time, in its own window."""
        self._show_profile(data_slice, time_label, along_time=False)

    def _show_profile(self, values, at_label, along_time):
        """A window with the profile and Save CSV / Save Plot buttons."""
        theme_name = getattr(self.main_window, "current_theme", "dark")
        palette = get_palette(theme_name)
        text_color = palette.get("text", "white")
        values = np.asarray(values, dtype=np.float64)
        axis = self._time_axis if along_time else self._freq_axis
        if axis is not None:
            x = np.asarray(axis, dtype=np.float64)
        else:
            x = np.arange(values.size, dtype=np.float64)
        if along_time:
            title = f"Light curve @ {at_label}"
            x_label = "Time (UTC)" if axis is not None else "Time index"
        else:
            title = f"Spectrum @ {at_label}"
            x_label = "Frequency (MHz)" if axis is not None else "Frequency index"

        win = QWidget()
        win.setWindowTitle(title)
        win.resize(800, 500)
        layout = QVBoxLayout(win)
        layout.setContentsMargins(6, 6, 6, 6)
        axis_items = {}
        if along_time and axis is not None:
            axis_items["bottom"] = TimeAxisItem(orientation="bottom")
            axis_items["bottom"].enableAutoSIPrefix(False)
        plot = pg.PlotWidget(title=title, axisItems=axis_items)
        plot.setBackground(palette.get("base", "#1e1e2e"))
        for side in ("left", "bottom"):
            plot.getAxis(side).setPen(text_color)
            plot.getAxis(side).setTextPen(text_color)
        plot.setLabel("left", "Intensity", color=text_color)
        plot.setLabel("bottom", x_label, color=text_color)
        plot.plot(x, values, pen=pg.mkPen(palette.get("highlight", "#6366f1"), width=1.8))
        layout.addWidget(plot, 1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        csv_btn = QPushButton("Save CSV")
        csv_btn.clicked.connect(
            lambda: self._save_profile_csv(win, x, values, at_label, along_time, axis is not None)
        )
        plot_btn = QPushButton("Save Plot")
        plot_btn.clicked.connect(
            lambda: self._save_profile_plot(win, x, values, at_label, title, x_label, along_time, axis is not None)
        )
        buttons.addWidget(csv_btn)
        buttons.addWidget(plot_btn)
        layout.addLayout(buttons)

        self._popups.append(win)
        win.show()
        return win

    def _profile_file_stem(self, at_label, along_time):
        """e.g. ``obs_lightcurve_45.000MHz`` (a name for the saved profile)."""
        source = getattr(self.main_window, "current_filename", "") or "spectrum"
        base = os.path.splitext(os.path.basename(source))[0]
        kind = "lightcurve" if along_time else "spectrum"
        label = re.sub(r"[^0-9A-Za-z.]+", "", at_label.replace(":", ""))
        return f"{base}_{kind}_{label}"

    def _save_profile_csv(self, parent, x, values, at_label, along_time, has_axis):
        fileName, _ = QFileDialog.getSaveFileName(
            parent, "Save Profile as CSV",
            self._profile_file_stem(at_label, along_time) + ".csv", "CSV (*.csv)",
        )
        if not fileName:
            return
        source = getattr(self.main_window, "current_filename", "")
        kind = "Light curve" if along_time else "Spectrum"
        try:
            with open(fileName, "w") as out:
                out.write(f"# {kind} at {at_label}\n")
                if source:
                    out.write(f"# Source: {os.path.basename(source)}\n")
                if along_time and has_axis:
                    out.write("time_utc,time_mjd,intensity\n")
                    for t, v in zip(x, values):
                        utc = datetime(1858, 11, 17) + timedelta(seconds=float(t))
                        out.write(f"{utc.isoformat(timespec='milliseconds')},{t / 86400:.9f},{v:.6g}\n")
                else:
                    name = "frequency_mhz" if has_axis else "index"
                    out.write(f"{name},intensity\n")
                    for f, v in zip(x, values):
                        out.write(f"{f:.6f},{v:.6g}\n" if has_axis else f"{int(f)},{v:.6g}\n")
        except OSError as e:
            QMessageBox.warning(parent, "Save CSV", f"Could not save:\n{e}")
            return
        self.logger.info(f"Profile saved to {fileName}")

    def _save_profile_plot(self, parent, x, values, at_label, title, x_label, along_time, has_axis):
        fileName, chosen = QFileDialog.getSaveFileName(
            parent, "Save Profile Plot",
            self._profile_file_stem(at_label, along_time) + ".png",
            "PNG (*.png);;PDF (*.pdf);;SVG (*.svg);;JPEG (*.jpg)",
        )
        if not fileName:
            return
        if not os.path.splitext(fileName)[1]:
            fileName += "." + chosen.split("*.")[1].rstrip(")")
        from matplotlib.figure import Figure
        import matplotlib.dates as mdates

        # (a plain light style, whatever the app theme)
        with matplotlib.style.context("default"):
            fig = Figure(figsize=(8, 4.5), dpi=150)
            ax = fig.add_subplot(111)
            if along_time and has_axis:
                # (MJD seconds -> datetimes, so the axis reads in UTC)
                x = [datetime(1858, 11, 17) + timedelta(seconds=float(t)) for t in x]
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
            ax.plot(x, values, color="#4f46e5", linewidth=1.2)
            ax.set_xlabel(x_label)
            ax.set_ylabel("Intensity")
            source = getattr(self.main_window, "current_filename", "")
            ax.set_title(f"{title}\n{os.path.basename(source)}" if source else title, fontsize=10)
            ax.grid(True, alpha=0.3)
            if along_time and has_axis:
                fig.autofmt_xdate()
            fig.tight_layout()
            try:
                fig.savefig(fileName)
            except (OSError, ValueError) as e:
                QMessageBox.warning(parent, "Save Plot", f"Could not save:\n{e}")
                return
        self.logger.info(f"Profile plot saved to {fileName}")

    # ------------------ Mouse Hover: Display Amplitude --------------------------
    def _on_mouse_move(self, pos):
        """The crosshair and its chip at the sample under the cursor; the
        readout (hoverChanged) and its time and frequency (cursorSample)."""
        if self._data is None:
            return

        try:
            mouse_point = self.plot_item.vb.mapSceneToView(pos)
            x, y = mouse_point.x(), mouse_point.y()

            sample = self._index_at(x, y)
            if sample is None or not self.plot_item.vb.sceneBoundingRect().contains(pos):
                self._hide_crosshair()
                self.hoverChanged.emit("", "", float("nan"))
                return
            time_idx, freq_idx = sample
            time_str, freq_str = self._sample_labels(time_idx, freq_idx)

            val = self._data[time_idx, freq_idx]

            # Emit signal for UI update
            self.hoverChanged.emit(time_str, freq_str, val)
            if self._time_axis is not None and self._freq_axis is not None:
                self.cursorSample.emit(
                    float(self._time_axis[time_idx]), float(self._freq_axis[freq_idx])
                )
            self._show_crosshair(x, y, time_str, freq_str, val)

        except Exception:
            pass  # (e.g. the mouse left while data changed)

    def _show_crosshair(self, x, y, time_str, freq_str, val):
        """1px lines through (x, y) and, 8px down and right of it (left of
        it near the right edge), the chip: 11:35:36 · 120.20 MHz · 0.791."""
        value = "NaN" if val is None or not np.isfinite(val) else f"{val:.3g}"
        freq = freq_str.replace(" MHz", "")
        try:
            freq = f"{float(freq):.2f}"
        except ValueError:
            pass
        self.chip.setText(f"{time_str.split('.')[0]} · {freq} MHz · {value}")
        self.cross_v.setPos(x)
        self.cross_h.setPos(y)
        vb = self.plot_item.vb
        (x0, x1), _ = vb.viewRange()
        px, py = vb.viewPixelSize()
        right = x > x0 + 0.7 * (x1 - x0)
        self.chip.setAnchor((1, 0) if right else (0, 0))
        self.chip.setPos(x - 8 * px if right else x + 8 * px, y - 8 * py)
        for item in (self.cross_v, self.cross_h, self.chip):
            item.show()

    def _hide_crosshair(self):
        for item in (self.cross_v, self.cross_h, self.chip):
            item.hide()

    def leaveEvent(self, event):
        self._hide_crosshair()
        self.hoverChanged.emit("", "", float("nan"))
        super().leaveEvent(event)

    def release_gl(self):
        """Drop the OpenGL viewport (on closing, while the application is
        alive): the font engines' glyph caches made in its context are
        freed now, not when the application is destroyed, where freeing
        them crashes."""
        try:
            self.graphics_widget.useOpenGL(False)
        except Exception:
            pass

    def keyPressEvent(self, event):
        """Arrow keys move the view a tenth of its width or height (as in
        SolarViewer), once the plot has the focus."""
        steps = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, 1), Qt.Key_Down: (0, -1)}
        step = steps.get(event.key())
        if step is None or self._data is None:
            super().keyPressEvent(event)
            return
        (x0, x1), (y0, y1) = self.plot_item.vb.viewRange()
        self.plot_item.vb.translateBy(x=step[0] * 0.1 * (x1 - x0), y=step[1] * 0.1 * (y1 - y0))
        event.accept()

    def zoom_by(self, factor):
        """Zoom the view about its centre: ``factor`` < 1 in, > 1 out."""
        if self._data is not None:
            self.plot_item.vb.scaleBy((factor, factor))

    def reset_view(self):
        """All of the data shown (the page, the range or the file)."""
        if self._extent is not None:
            x_low, width, y_low, height = self._extent
            self.plot_item.vb.setRange(
                xRange=(x_low, x_low + width), yRange=(y_low, y_low + height), padding=0
            )

    # Compatibility methods for MainWindow
    def clear_plot(self):
        """Show nothing (no file open)."""
        self.image_item.clear()
        self._data = None
        self._filename = None
        self.set_boxes([])
        self.set_selected_box(None)
        self._hide_crosshair()
        self.date_label.setText("")

    def draw_idle(self):
        """Compatibility method - PyQtGraph updates automatically."""
        pass

    def draw(self):
        """Compatibility method - PyQtGraph updates automatically."""
        pass

    def _theme_palette(self):
        parent = self.parent()
        while parent is not None and not hasattr(parent, "current_theme"):
            parent = parent.parent()
        return get_palette(getattr(parent, "current_theme", "dark"))

    def _style_date_label(self):
        p = self._theme_palette()
        self.date_label.setText(
            getattr(self, "_date_text", ""), color=p["text_secondary"], size="8pt"
        )

    def _apply_canvas_theme_colors(self):
        """Ticks in monospace (text_dim), axis titles muted, no axis lines:
        the image's only frame is a 1px edge."""
        from PyQt5.QtGui import QColor

        p = self._theme_palette()
        self.graphics_widget.setBackground(p["window"])

        tick_font = QFont("monospace")
        tick_font.setStyleHint(QFont.TypeWriter)
        tick_font.setPointSize(8)
        edge = p["border_light"]
        for axis in (
            self.plot_item.getAxis("left"),
            self.plot_item.getAxis("bottom"),
            self.colorbar_axis,
            self.flag_plot.getAxis("bottom"),
        ):
            axis.setPen(pg.mkPen(QColor(0, 0, 0, 0)))
            axis.setTextPen(p["text_dim"])
            axis.setTickFont(tick_font)
            axis.setStyle(tickLength=0, tickTextOffset=8)
        self.colorbar_plot.getAxis("bottom").setPen(pg.mkPen(QColor(0, 0, 0, 0)))
        left = self.plot_item.getAxis("left")
        left.setLabel(text="MHz", color=p["text_secondary"], **{"font-size": "8pt"})
        self.plot_item.vb.setBorder(pg.mkPen(edge, width=1))
        self.flag_plot.vb.setBorder(pg.mkPen(edge, width=1))
        self._style_date_label()

        # The crosshair: text at 55%; the chip on the ground at 86% (the
        # dark theme's colours over a dark image, the light theme's over
        # a light one, whatever the theme)
        ink, ground = ("#1d1f2b", "#ffffff") if self._image_is_light else ("#e9e9ed", "#161826")
        line = QColor(ink)
        line.setAlphaF(0.55)
        for item in (self.cross_v, self.cross_h):
            item.setPen(pg.mkPen(line, width=1))
        ground = QColor(ground)
        ground.setAlphaF(0.86)
        self.chip.fill = pg.mkBrush(ground)
        self.chip.border = pg.mkPen(None)
        self.chip.setColor(ink)
        self.chip.setFont(QFont(tick_font))


class OverviewStrip(QWidget):
    """The whole file in a strip under the spectrum: veiled, but for the
    part shown, outlined. A click or a drag moves the outline; on release
    it asks for that time (seekRequested, the fraction of the file).

    Painted with QPainter (never a Matplotlib blit, which misplaces
    sub-rectangles at fractional display scales)."""

    seekRequested = pyqtSignal(float)  # fraction of the file, 0..1

    VEIL = 0.55  # the ground's opacity over the parts not shown

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image = None  # QImage of the file (columns: time, low frequency at the bottom)
        self._view = (0.0, 1.0)  # the part shown, as fractions of the file
        self._drag = None  # the outline's start while dragging
        self._span = ""  # "12.5 h"
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click or drag to go to a time")

    def _palette(self):
        parent = self.parent()
        while parent is not None and not hasattr(parent, "current_theme"):
            parent = parent.parent()
        return get_palette(getattr(parent, "current_theme", "dark"))

    def _apply_theme(self):
        self.update()

    def draw(self):
        self.update()

    def set_span(self, seconds):
        """The file's length, for the kicker ("WHOLE FILE · 12.5 h")."""
        if seconds is None or not np.isfinite(seconds) or seconds <= 0:
            self._span = ""
        elif seconds >= 3600:
            self._span = f"{seconds / 3600:.1f} h"
        elif seconds >= 60:
            self._span = f"{seconds / 60:.0f} min"
        else:
            self._span = f"{seconds:.0f} s"
        self.update()

    def set_full_data(self, data_downsampled):
        """The file's overview, (freq, time): each channel divided by its
        median, 1-99 percentile limits, linear, in inferno; no more columns
        than 2000, each the maximum of those it stands for (so short bursts
        stay)."""
        self._image = None
        if data_downsampled is not None and data_downsampled.size:
            med = _channel_medians(data_downsampled.T)[:, None]
            med[np.isnan(med)] = 1e-20
            med[med == 0] = 1e-20
            data = data_downsampled / med
            n = data.shape[1]
            if n > 2000:
                starts = np.arange(0, n, -(-n // 2000))
                with np.errstate(invalid="ignore"):
                    data = np.fmax.reduceat(data, starts, axis=1)
            finite = data[np.isfinite(data)]
            vmin, vmax = (np.percentile(finite, [1, 99]) if finite.size else (0.0, 1.0))
            if vmax <= vmin:
                vmax = vmin + 1e-9
            with np.errstate(invalid="ignore"):
                norm = np.clip((data - vmin) / (vmax - vmin), 0, 1)
            norm = np.nan_to_num(norm, nan=0.0)
            lut = (plt.get_cmap("inferno")(np.linspace(0, 1, 256))[:, :3] * 255).astype(np.uint8)
            rgb = np.ascontiguousarray(lut[(norm[::-1] * 255).astype(np.uint8)])
            from PyQt5.QtGui import QImage

            h, w = rgb.shape[:2]
            self._image = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        self.update()

    def update_view_percent(self, start_pct, end_pct):
        """Outline the part shown (fractions of the file)."""
        self._view = (max(0.0, start_pct), min(1.0, end_pct))
        self.update()

    def paintEvent(self, event):
        from PyQt5.QtCore import QRectF
        from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen

        p = self._palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(rect, 4, 4)
        painter.setClipPath(clip)
        painter.fillRect(rect, QColor(p["surface"]))
        if self._image is not None:
            painter.drawImage(rect, self._image)

        # The veil, but over the part shown
        veil = QColor(p["window"])
        veil.setAlphaF(self.VEIL)
        x0 = rect.left() + self._view[0] * rect.width()
        x1 = rect.left() + self._view[1] * rect.width()
        whole = self._view[1] - self._view[0] >= 0.999
        if whole:
            painter.fillRect(rect, veil)
        else:
            painter.fillRect(QRectF(rect.left(), rect.top(), x0 - rect.left(), rect.height()), veil)
            painter.fillRect(QRectF(x1, rect.top(), rect.right() - x1, rect.height()), veil)
            # The outline, 2px inside the part (at least 4px wide)
            if x1 - x0 < 4:
                x0, x1 = (x0 + x1) / 2 - 2, (x0 + x1) / 2 + 2
            painter.setClipping(False)
            painter.setPen(QPen(QColor("#e7e5fe"), 2))  # (accent-200: over the image in both themes)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(QRectF(x0 + 1, rect.top() + 1, x1 - x0 - 2, rect.height() - 2))
            painter.setClipPath(clip)

        # The kicker
        if self._image is not None:
            font = QFont(self.font())
            font.setPointSize(7)
            font.setLetterSpacing(QFont.PercentageSpacing, 110)
            painter.setFont(font)
            text = ("Whole file" + (f" · {self._span}" if self._span else "")).upper()
            box = painter.fontMetrics().boundingRect(text)
            backing = QColor(p["window"])
            backing.setAlphaF(0.7)
            painter.setPen(Qt.NoPen)
            painter.setBrush(backing)
            # (the advance counts the letter spacing; the bounding box does not)
            advance = painter.fontMetrics().horizontalAdvance(text)
            painter.drawRoundedRect(QRectF(rect.left() + 3, rect.top() + 3, advance + 10, box.height() + 2), 3, 3)
            painter.setPen(QColor(p["text_strong"]))
            painter.drawText(QRectF(rect.left() + 7, rect.top() + 4, rect.width() - 12, box.height()),
                             Qt.AlignLeft | Qt.AlignVCenter, text)

        # The edge
        painter.setClipping(False)
        painter.setPen(QPen(QColor(p["border_light"]), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect, 4, 4)
        painter.end()

    def _fraction(self, x):
        return min(1.0, max(0.0, x / max(1, self.width())))

    def _centre_outline(self, x):
        width = self._view[1] - self._view[0]
        start = min(1.0 - width, max(0.0, self._fraction(x) - width / 2))
        self._view = (start, start + width)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._image is not None:
            self._drag = True
            self._centre_outline(event.pos().x())

    def mouseMoveEvent(self, event):
        if self._drag:
            self._centre_outline(event.pos().x())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._drag:
            self._drag = None
            self.seekRequested.emit(self._fraction(event.pos().x()))

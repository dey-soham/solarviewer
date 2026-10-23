"""
The intensity histogram of the inspector: the distribution of the image's
values with the display range on it, to set the range by eye.

A short, wide Matplotlib canvas: the histogram of a sample of the image
(log counts) as bars, those inside the display range in the accent and
the rest neutral, and the two range markers, which drag. Dragging emits
``rangeEdited``. It is drawn as one whole canvas (never blitted in parts),
so it is right at any display scale.
"""

import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from .styles import theme_manager

SAMPLE = 400_000  # values at most in the histogram: enough for its shape
BINS = 48


class LevelsHistogram(QWidget):
    """The histogram with draggable range markers.

    ``set_data(array)`` gives it the image, ``set_range(vmin, vmax)`` what
    the display shows. ``rangeEdited(vmin, vmax)`` is emitted while a
    marker is dragged.
    """

    rangeEdited = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._counts = None
        self._edges = None
        self._vmin = 0.0
        self._vmax = 1.0
        self._drag = None  # "min" or "max" while a marker is dragged

        self.figure = Figure(figsize=(3, 0.7), dpi=96)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.canvas.setFixedHeight(int(self.fontMetrics().height() * 4.5))
        self.canvas.setToolTip("Drag the markers to set the range shown")
        # At a fractional display scale the widget can be a pixel wider
        # than the figure's bitmap; the gap shows the widget's palette,
        # which Matplotlib sets white. An empty one inherits the theme's.
        self.canvas.setPalette(QPalette())
        self.ax = self.figure.add_axes([0.0, 0.0, 1.0, 1.0])
        self.ax.set_axis_off()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.canvas.mpl_connect("button_press_event", self._on_press)
        self.canvas.mpl_connect("motion_notify_event", self._on_move)
        self.canvas.mpl_connect("button_release_event", self._on_release)
        theme_manager.register_callback(lambda _theme: self._draw(), owner=self)
        self._draw()

    # ------------------------------------------------------------- the data
    def set_data(self, data):
        """The image's values (any shape; NaNs and infinities ignored)."""
        if data is None:
            self._counts = self._edges = None
            self._draw()
            return
        values = np.asarray(data).ravel()
        if values.size > SAMPLE:
            values = values[:: max(1, values.size // SAMPLE)]
        values = values[np.isfinite(values)]
        if values.size < 2 or not np.nanmax(values) > np.nanmin(values):
            self._counts = self._edges = None
        else:
            # The bulk of the values, not the one hot pixel: clip the
            # extremes so the histogram keeps its shape
            low, high = np.percentile(values, [0.1, 99.9])
            if not high > low:
                low, high = float(values.min()), float(values.max())
            self._counts, self._edges = np.histogram(values, bins=BINS, range=(low, high))
        self._draw()

    def set_range(self, vmin, vmax):
        try:
            vmin, vmax = float(vmin), float(vmax)
        except (TypeError, ValueError):
            return
        if np.isfinite(vmin) and np.isfinite(vmax) and (vmin, vmax) != (self._vmin, self._vmax):
            self._vmin, self._vmax = vmin, vmax
            self._draw()

    # ------------------------------------------------------------ the picture
    def _draw(self):
        palette = theme_manager.palette
        ax = self.ax
        ax.clear()
        ax.set_axis_off()
        self.figure.set_facecolor(palette["window"])
        ax.set_facecolor(palette["window"])
        if self._counts is None:
            ax.axhline(0.02, color=palette["border_light"], linewidth=1)
            ax.text(
                0.5, 0.55, "No image", ha="center", va="center",
                color=palette["text_secondary"], fontsize=8, transform=ax.transAxes,
            )
            ax.set_xlim(0, 1)
            ax.set_ylim(0, 1)
            self.canvas.draw_idle()
            return
        edges, counts = self._edges, self._counts
        centres = (edges[:-1] + edges[1:]) / 2
        heights = np.log10(counts + 1)
        top = heights.max() or 1.0
        # The whole range in view: the data's bulk and the markers
        left = min(edges[0], self._vmin)
        right = max(edges[-1], self._vmax)
        span = right - left or 1.0
        left, right = left - 0.02 * span, right + 0.02 * span
        ax.set_xlim(left, right)
        ax.set_ylim(0, top * 1.1)

        width = (edges[1] - edges[0]) * 0.8
        inside = (centres >= self._vmin) & (centres <= self._vmax)
        colours = np.where(inside, palette["highlight_soft"], palette["border_light"])
        ax.bar(centres, heights, width=width, color=colours, linewidth=0)
        ax.axhline(0, color=palette["border_light"], linewidth=1)
        for value in (self._vmin, self._vmax):
            ax.axvline(value, color=palette["highlight"], linewidth=1.4, zorder=3)
        self.canvas.draw_idle()

    # ------------------------------------------------------------- dragging
    def _nearest_marker(self, event):
        if event.xdata is None or self._counts is None:
            return None
        x0, x1 = self.ax.get_xlim()
        tolerance = (x1 - x0) * 0.04
        distances = {"min": abs(event.xdata - self._vmin), "max": abs(event.xdata - self._vmax)}
        marker = min(distances, key=distances.get)
        return marker if distances[marker] <= tolerance else None

    def _on_press(self, event):
        if event.button == 1:
            self._drag = self._nearest_marker(event)
            if self._drag:
                self.canvas.setCursor(Qt.SizeHorCursor)

    def _on_move(self, event):
        if self._drag is None:
            self.canvas.setCursor(
                Qt.SizeHorCursor if self._nearest_marker(event) else Qt.ArrowCursor
            )
            return
        if event.xdata is None:
            return
        if self._drag == "min":
            self._vmin = min(float(event.xdata), self._vmax)
        else:
            self._vmax = max(float(event.xdata), self._vmin)
        self._draw()
        self.rangeEdited.emit(self._vmin, self._vmax)

    def _on_release(self, event):
        if self._drag:
            self._drag = None
            self.rangeEdited.emit(self._vmin, self._vmax)

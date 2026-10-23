"""
Live preview of the Ruler and Profile tools: the figure that follows the mouse.

Matplotlib's blitting repaints a rectangle of the canvas on every mouse move.
On a display with a fractional scale factor Qt lands each such rectangle up
to a pixel off, so the strips that follow the mouse leave seams, and the
repaint is slow on big windows. The preview is instead a Qt widget over the
canvas: opaque, showing a picture of the canvas with the figure painted over
it. Each mouse move repaints only the small area the figure covered and
covers now, from the picture, which Qt does exactly at any scale and in well
under a millisecond. The figure is given in data coordinates and mapped
through the axes on every move, so it stays right if the view changes.
"""

import numpy as np
from matplotlib.patheffects import withStroke
from PyQt5.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PyQt5.QtGui import QColor, QPainter, QPen, QPixmap, QRegion
from PyQt5.QtWidgets import QWidget


def contrast_colors(tab):
    """(line, halo) colours that stand out on the tab's colormap.

    Lines are drawn in one over a halo of the other, so they differ from the
    image in brightness, not only in hue, and stay visible on any colormap
    and with any colour vision: a white line with a black halo on dark
    colormaps, a black line with a white halo on light ones.
    """
    image = getattr(tab, "image_plot", None)
    if image is None:
        return "white", "black"
    rgb = image.get_cmap()(np.linspace(0, 1, 64))[:, :3]
    luminance = rgb @ [0.2126, 0.7152, 0.0722]
    return ("black", "white") if luminance.mean() > 0.5 else ("white", "black")


def halo(color, linewidth):
    """Matplotlib path effects drawing a ``color`` halo under a line of
    ``linewidth`` points (and under its markers)."""
    return [withStroke(linewidth=linewidth + 2.5, foreground=color, alpha=0.85)]


def halo_pen(color, linewidth):
    """A pen for the halo under a preview line of ``linewidth`` pixels."""
    return QPen(QColor(color), linewidth + 2.5, Qt.SolidLine, Qt.FlatCap)


def dashed_pen(color, width=1.5, alpha=0.7):
    """A pen like Matplotlib's ``"--"`` line style, with ``color`` a Qt name."""
    pen = QPen(QColor(color), width, Qt.CustomDashLine, Qt.FlatCap)
    pen.setDashPattern([3.7, 1.6])  # Matplotlib's dashed pattern, in widths
    c = pen.color()
    c.setAlphaF(alpha)
    pen.setColor(c)
    return pen


class MeasurePreview(QWidget):
    """A picture of the canvas with line segments drawn over it.

    Segments are given in data coordinates of an axes; see :meth:`show_lines`.
    """

    def __init__(self, canvas):
        super().__init__(canvas)
        self._canvas = canvas
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.hide()
        self._picture = None
        self._lines = []  # (QPointF, QPointF, QPen)
        self._halo = None
        self._clip = QRectF()
        self._area = QRect()  # the lines' area, in this widget's pixels
        # The picture goes stale when the figure is drawn again
        canvas.mpl_connect("draw_event", self._on_canvas_drawn)

    def _on_canvas_drawn(self, event):
        self._picture = None

    def _take_picture(self):
        """Copy the canvas as drawn, without this widget over it."""
        canvas = self._canvas
        dpr = canvas.device_pixel_ratio
        picture = QPixmap(round(canvas.width() * dpr), round(canvas.height() * dpr))
        picture.setDevicePixelRatio(dpr)
        # Rendering finishes any pending draw first, through paintEvent
        canvas.render(picture, QPoint(), QRegion(), QWidget.DrawWindowBackground)
        self._picture = picture
        self.setGeometry(canvas.rect())

    def _to_widget(self, x, y):
        """Data coordinates of the axes to this widget's (logical) pixels."""
        return self._display_to_widget(*self._ax.transData.transform((x, y)))

    def _display_to_widget(self, px, py):
        """Matplotlib display (physical) pixels to this widget's pixels."""
        canvas = self._canvas
        dpr = canvas.device_pixel_ratio
        return QPointF(px / dpr, (canvas.figure.bbox.height - py) / dpr)

    def show_lines(self, ax, lines, halo=None):
        """Show ``lines``, each ``((x0, y0), (x1, y1), pen)`` in data
        coordinates of ``ax``, clipped to the axes, over a ``halo`` pen."""
        self._halo = halo
        canvas = self._canvas
        self._ax = ax
        fresh = self._picture is None or self.size() != canvas.size()
        if fresh:
            self._take_picture()

        x0, y0, w, h = ax.bbox.bounds
        corner = self._display_to_widget(x0, y0 + h)
        far = self._display_to_widget(x0 + w, y0)
        self._clip = QRectF(corner, far)

        self._lines = [
            (self._to_widget(*start), self._to_widget(*end), pen)
            for start, end, pen in lines
        ]
        area = QRectF()
        pad = halo.widthF() if halo is not None else 0.0
        for start, end, pen in self._lines:
            area = area.united(QRectF(start, end).normalized())
            pad = max(pad, pen.widthF())
        area = area.adjusted(-pad - 2, -pad - 2, pad + 2, pad + 2)
        area = area.intersected(self._clip).toAlignedRect()

        previous, self._area = self._area, area
        if fresh or not self.isVisible():
            self.show()
            self.raise_()
            self.update()
        else:
            self.update(previous.united(area))

    def end(self):
        """Take the preview away; the canvas shows through again."""
        self._lines = []
        self._area = QRect()
        self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        if self._picture is not None:
            painter.drawPixmap(0, 0, self._picture)
        else:
            painter.fillRect(event.rect(), self.palette().window())
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setClipRect(self._clip)
        if self._halo is not None:
            painter.setPen(self._halo)
            for start, end, pen in self._lines:
                painter.drawLine(start, end)
        for start, end, pen in self._lines:
            painter.setPen(pen)
            painter.drawLine(start, end)
        painter.end()


def measure_preview(tab):
    """The tab's preview widget, made on first use."""
    preview = getattr(tab, "_measure_preview", None)
    if preview is None:
        preview = tab._measure_preview = MeasurePreview(tab.canvas)
    return preview

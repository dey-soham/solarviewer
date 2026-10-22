"""
Matplotlib canvas setup plus zoom, pan and mouse interaction.
"""

import logging
import time

import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib import rcParams
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
from PyQt5.QtCore import QEvent, QObject, QPointF, QRect, QRectF, Qt, QTimer
from PyQt5.QtGui import QColor, QCursor, QImage, QPainter, QPalette
from PyQt5.QtWidgets import QWidget

from ..tools import RegionMode

logger = logging.getLogger(__name__)

# Zoom per wheel notch, and per zoom button, key press or double-click
WHEEL_ZOOM = 1.2
STEP_ZOOM = 2.0
# How long an animated zoom or key pan takes, in seconds
GLIDE_SECONDS = 0.15
# Zoom out no further than the image filling 1/MAX_ZOOM_OUT of the view
MAX_ZOOM_OUT = 2.0
# Zoom in no further than this many image pixels across the view
MIN_VIEW_PIXELS = 5
# The image keeps covering at least this fraction of the view when panned
MIN_IMAGE_IN_VIEW = 0.2
# A right-drag box smaller than this (screen pixels) does not zoom
MIN_ZOOM_BOX = 5
# Arrow keys pan by this fraction of the view
KEY_PAN_FRACTION = 0.1
# gid of the text that says how to open an image while there is none
EMPTY_HINT_GID = "empty-hint"
EMPTY_HINT = "Drop a FITS file or CASA image here\nor open one from the File menu"
# Cursor Position text while the mouse is off the image
READOUT_HINT = "Hover over the image"
# Arrow key -> (x, y) pan steps; Right shows more of the image to the right
_ARROW_PAN_STEPS = {
    Qt.Key_Left: (-1, 0),
    Qt.Key_Right: (1, 0),
    Qt.Key_Up: (0, 1),
    Qt.Key_Down: (0, -1),
}
# Redraw properly once the view has been still this long, in milliseconds
SETTLE_MS = 200
# Size of the coarse picture under the preview's newly exposed edges
OVERVIEW_PIXELS = 768


class _ViewPreview(QWidget):
    """A picture of the plot area, moved and scaled to follow the view.

    A Matplotlib redraw runs on the CPU and takes ~100 ms, too slow to follow
    the mouse. While the view moves this widget sits over the plot area showing
    the last drawn picture where the new view puts it, over a coarse picture
    of the image around it for the edges that come into view; the real redraw
    waits until the view settles.
    """

    def __init__(self, canvas):
        super().__init__(canvas)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()
        self._pixmap = None
        self._fill = QColor()
        self._target = QRectF()
        self._overview = None
        self._overview_target = QRectF()

    def show_picture(self, pixmap, rect, fill, overview):
        self._pixmap = pixmap
        self._fill = fill
        self._overview = overview
        self.setGeometry(rect)
        self.show()
        self.raise_()

    def set_target(self, target, overview_target):
        """Draw the pictures into these rects (this widget's coordinates)."""
        self._target = target
        self._overview_target = overview_target
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._fill)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        if self._overview is not None:
            painter.drawImage(
                self._overview_target, self._overview, QRectF(self._overview.rect())
            )
        if self._pixmap is not None:
            painter.drawPixmap(self._target, self._pixmap, QRectF(self._pixmap.rect()))


class _ImageCanvas(FigureCanvas):
    """The figure canvas, taking keyboard focus when clicked or scrolled on.

    It acts on the view keys itself and passes every other key on to the
    window too (Matplotlib's canvas would swallow them), so the window's own
    keys keep working while the image has focus.
    """

    def __init__(self, figure, view_key_handler):
        super().__init__(figure)
        self._view_key_handler = view_key_handler
        self.setFocusPolicy(Qt.WheelFocus)

    def keyPressEvent(self, event):
        if self._view_key_handler(event):
            return
        super().keyPressEvent(event)
        event.ignore()


class _HistoryToolbar(NavigationToolbar):
    """Matplotlib's toolbar, kept hidden for its Back/Forward view history."""

    def mouse_move(self, event):
        # Its coordinate message is never shown, and formatting it on every
        # mouse move slowed the tools that follow the mouse
        pass


class _PinchFilter(QObject):
    """Sends trackpad pinch gestures on the canvas to the tab's zoom."""

    def __init__(self, tab):
        super().__init__(tab.canvas)
        self._tab = tab

    def eventFilter(self, obj, event):
        if (
            event.type() == QEvent.NativeGesture
            and event.gestureType() == Qt.ZoomNativeGesture
        ):
            self._tab._on_pinch(event)
            return True
        return False


def _wheel_steps(event):
    """Wheel notches scrolled, fractions of one on trackpads; positive is up."""
    gui = getattr(event, "guiEvent", None)
    if gui is not None and hasattr(gui, "angleDelta"):
        return gui.angleDelta().y() / 120
    return event.step


def _format_value(value, unit):
    """A pixel value for the cursor readout, e.g. ``-30.42 Jy/beam``."""
    if np.ma.is_masked(value) or not np.isfinite(value):
        return "no data"
    return f"{float(value):.4g} {unit}".strip()


def _keep_overlap(lim, lo, hi):
    """``lim`` shifted to overlap ``[lo, hi]`` by MIN_IMAGE_IN_VIEW of its width."""
    v0, v1 = lim
    lo, hi = min(lo, hi), max(lo, hi)
    if not v1 > v0:
        return lim
    need = min(MIN_IMAGE_IN_VIEW * (v1 - v0), hi - lo)
    if v1 < lo + need:
        shift = lo + need - v1
    elif v0 > hi - need:
        shift = hi - need - v0
    else:
        return lim
    return (v0 + shift, v1 + shift)


class CanvasMixin:
    """Canvas creation, zoom buttons, scroll/pinch zoom, drag panning and mouse handlers.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``coord_label``, ``image_plot``, ``init_region_editor``, ``_is_panning``,
    ``_nav_record_timer``, ``_profile_mode``, ``ruler_action``, ``_ruler_mode``,
    ``show_beam_checkbox``, ``show_status_message``, ``_toggle_ruler_mode``,
    ``_update_beam_position``, ``_view_anim``, ``window``, ``_zoom_timer``
    """

    def setup_canvas(self, parent_layout):
        self.figure = Figure(figsize=(5, 5), dpi=100)
        self.canvas = _ImageCanvas(self.figure, self.handle_view_key)

        # No style sheet on the canvas: one makes Qt drop Matplotlib's
        # WA_OpaquePaintEvent, and then every cursor readout update repaints
        # the whole canvas, which made the Ruler and Profile preview lines lag.
        # Gaps while resizing are filled with the palette's Window colour:
        # Matplotlib sets a white palette, so take the theme's instead (an
        # empty palette inherits it, and follows theme changes).
        self.canvas.setPalette(QPalette())

        # Matplotlib's toolbar stays hidden: the figure toolbar has the tools,
        # and this one only keeps the view history for its Back/Forward
        self.nav_toolbar = _HistoryToolbar(self.canvas, self)
        self.nav_toolbar.hide()
        parent_layout.addWidget(self.canvas, 1)

        self.current_image_data = None
        self.current_wcs = None
        self.psf = None
        self.current_roi = None
        self.roi_selector = None
        self.imagename = None

        self.solar_disk_center = None
        self.solar_disk_diameter_arcmin = 32.0
        self.solar_disk_auto_compute = True

        self.canvas.mpl_connect("motion_notify_event", self.on_mouse_move)
        self.canvas.mpl_connect("scroll_event", self._on_mouse_scroll)
        self.canvas.mpl_connect("button_press_event", self._on_mouse_press)
        self.canvas.mpl_connect("button_release_event", self._on_mouse_release)

        self._pinch_filter = _PinchFilter(self)
        self.canvas.installEventFilter(self._pinch_filter)

        # Moving views show a picture until they settle; see _ViewPreview
        self._view_preview = _ViewPreview(self.canvas)
        self._drawn_view = None
        self._preview_source = None
        self._overview_source = None
        self._settle_timer = QTimer(self.canvas)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.setInterval(SETTLE_MS)
        self._settle_timer.timeout.connect(self._settle_view)
        self.canvas.mpl_connect("draw_event", self._on_canvas_drawn)

        self.show_empty_hint()

    def show_empty_hint(self):
        """Say on the figure how to open an image (while none is shown)."""
        self.figure.text(
            0.5,
            0.5,
            EMPTY_HINT,
            ha="center",
            va="center",
            fontsize=13,
            linespacing=1.8,
            color=rcParams["text.color"],
            alpha=0.6,
            gid=EMPTY_HINT_GID,
        )

    def set_region_mode(self, mode_id):
        self.region_mode = mode_id
        # Disable ruler mode when switching to ROI/Pan
        if hasattr(self, "_ruler_mode") and self._ruler_mode:
            self._toggle_ruler_mode(False)
            if hasattr(self, "ruler_action"):
                self.ruler_action.setChecked(False)

        # Status feedback
        if mode_id == RegionMode.PAN:
            self.show_status_message("Mode: Pan")
        elif mode_id == RegionMode.RECTANGLE:
            self.show_status_message("Mode: Rectangle Select")
        elif mode_id == RegionMode.ELLIPSE:
            self.show_status_message("Mode: Ellipse Select")

        # Re-init region editor if we have an axis
        if hasattr(self, "figure") and self.figure.axes:
            self.init_region_editor(self.figure.axes[0])

    # ------------------------------------------------------------------
    # Zoom and pan commands (buttons, menus, keys)
    # ------------------------------------------------------------------

    def zoom_in(self):
        self._step_zoom(1 / STEP_ZOOM, "Zoomed in")

    def zoom_out(self):
        self._step_zoom(STEP_ZOOM, "Zoomed out")

    def _step_zoom(self, factor, message):
        """Zoom about the mouse if it is over the image, else the view centre."""
        ax = self._nav_axes()
        if ax is None:
            return
        self._zoom_about(ax, factor, self._cursor_data_position(ax), animate=True)
        self.show_status_message(message)

    def zoom_60arcmin(self):
        ax = self._nav_axes()
        if ax is None or self.current_wcs is None:
            return

        try:
            xlim, ylim = self._view_target(ax)
            xcenter = (xlim[0] + xlim[1]) / 2
            ycenter = (ylim[0] + ylim[1]) / 2

            cdelt = self.current_wcs.increment()["numeric"][0:2]
            if isinstance(cdelt, list):
                cdelt = [float(c) for c in cdelt]
            _hdr = getattr(self, "_cached_fits_header", None) or {}
            _is_solar = "SOLAR-X" in str(_hdr.get("CTYPE1", "")).upper()
            if _is_solar:
                cdelt = np.array(cdelt) / 3600.0  # arcsec -> degrees
            else:
                cdelt = np.array(cdelt) * 180 / np.pi  # radians -> degrees
            arcmin_60_deg = 60.0 / 60.0
            pixels_x = arcmin_60_deg / abs(cdelt[0])
            pixels_y = arcmin_60_deg / abs(cdelt[1])

            self._animate_view(
                ax,
                (xcenter - pixels_x / 2, xcenter + pixels_x / 2),
                (ycenter - pixels_y / 2, ycenter + pixels_y / 2),
            )
            self.show_status_message("Zoomed to 1°×1°")
        except Exception as e:
            logger.error(f"Error in zoom_60arcmin: {e}")
            self.show_status_message(f"Error in zoom_60arcmin: {e}")

    def reset_view(self, show_status_message=True, animate=False):
        """Reset the view to show the full image"""
        ax = self._nav_axes()
        if ax is None:
            return

        # The image's pixels are centred on integers, so it spans N-1 + a pixel
        nx, ny = self.current_image_data.shape[:2]
        xlim, ylim = (-0.5, nx - 0.5), (-0.5, ny - 0.5)
        if animate:
            self._animate_view(ax, xlim, ylim)
        else:
            self._stop_view_anim()
            self._begin_navigation(ax)
            self._set_view(ax, xlim, ylim)
            self._settle_view()
            self._record_view_soon()

        if show_status_message:
            self.show_status_message("View reset")

    def handle_view_key(self, event):
        """Act on a view key (R, 1, +/-, arrows). True if it was one."""
        if event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            return False
        key = event.key()
        if key == Qt.Key_R:
            self.reset_view(animate=True)
        elif key == Qt.Key_1:
            self.zoom_60arcmin()
        elif key in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom_in()
        elif key == Qt.Key_Minus:
            self.zoom_out()
        elif key in _ARROW_PAN_STEPS:
            self.pan_view(*_ARROW_PAN_STEPS[key])
        else:
            return False
        return True

    def pan_view(self, x_steps, y_steps):
        """Glide the view by KEY_PAN_FRACTION of its size per step (arrow keys)."""
        ax = self._nav_axes()
        if ax is None:
            return
        xlim, ylim = self._view_target(ax)
        dx = (xlim[1] - xlim[0]) * KEY_PAN_FRACTION * x_steps
        dy = (ylim[1] - ylim[0]) * KEY_PAN_FRACTION * y_steps
        self._animate_view(
            ax, (xlim[0] + dx, xlim[1] + dx), (ylim[0] + dy, ylim[1] + dy)
        )

    # ------------------------------------------------------------------
    # View changes
    # ------------------------------------------------------------------

    def _nav_axes(self):
        """The image's axes, or None if there is no image to navigate."""
        if self.current_image_data is None or self.image_plot is None:
            return None
        return self.image_plot.axes

    def _view_target(self, ax):
        """Where the view is heading: the glide's end if gliding, else the view."""
        if self._view_anim is not None and self._view_anim["ax"] is ax:
            return self._view_anim["xlim"], self._view_anim["ylim"]
        return ax.get_xlim(), ax.get_ylim()

    def _cursor_data_position(self, ax):
        """Data position of the mouse if it is over the axes, else the view centre."""
        pos = self.canvas.mapFromGlobal(QCursor.pos())
        if self.canvas.rect().contains(pos):
            x, y = self.canvas.mouseEventCoords(pos)
            if ax.bbox.contains(x, y):
                return tuple(ax.transData.inverted().transform((x, y)))
        xlim, ylim = self._view_target(ax)
        return ((xlim[0] + xlim[1]) / 2, (ylim[0] + ylim[1]) / 2)

    def _zoom_about(self, ax, factor, anchor, animate):
        """Scale the view by ``factor`` (>1 zooms out), keeping ``anchor`` in place."""
        xlim, ylim = self._view_target(ax)
        factor = self._limit_zoom_factor(xlim, ylim, factor)
        if factor == 1:
            return
        x, y = anchor
        new_xlim = (x + (xlim[0] - x) * factor, x + (xlim[1] - x) * factor)
        new_ylim = (y + (ylim[0] - y) * factor, y + (ylim[1] - y) * factor)
        if animate:
            self._animate_view(ax, new_xlim, new_ylim)
        else:
            self._stop_view_anim()
            self._begin_navigation(ax)
            self._set_view(ax, new_xlim, new_ylim)
            self._record_view_soon()

    def _init_box_zoom(self, ax):
        """Right-drag draws a box on ``ax`` and zooms to it, in every mode but
        the measurement tools."""
        from matplotlib.widgets import RectangleSelector

        old = getattr(self, "_zoom_box", None)
        if old is not None:
            old.disconnect_events()
        self._zoom_box = RectangleSelector(
            ax,
            self._zoom_to_box,
            button=[3],
            useblit=True,
            minspanx=MIN_ZOOM_BOX,
            minspany=MIN_ZOOM_BOX,
            spancoords="pixels",
            props={"facecolor": "white", "edgecolor": "white", "alpha": 0.25},
        )

    def _zoom_to_box(self, press, release):
        """Zoom so the dragged box fills the view (grown to the view's shape)."""
        ax = self._nav_axes()
        if ax is None or ax is not self._zoom_box.ax or self._in_measurement_mode():
            return
        if None in (press.xdata, press.ydata, release.xdata, release.ydata):
            return
        xlim, ylim = self._view_target(ax)
        w, h = abs(xlim[1] - xlim[0]), abs(ylim[1] - ylim[0])
        box_w = abs(release.xdata - press.xdata)
        box_h = abs(release.ydata - press.ydata)
        factor = self._limit_zoom_factor(xlim, ylim, max(box_w / w, box_h / h))
        cx = (press.xdata + release.xdata) / 2
        cy = (press.ydata + release.ydata) / 2
        # The same orientation as before (an axis may run backwards)
        half_x = (xlim[1] - xlim[0]) * factor / 2
        half_y = (ylim[1] - ylim[0]) * factor / 2
        self._animate_view(ax, (cx - half_x, cx + half_x), (cy - half_y, cy + half_y))

    def _limit_zoom_factor(self, xlim, ylim, factor):
        """``factor`` reduced so the view stays between the zoom limits."""
        x1, x2, y1, y2 = self.image_plot.get_extent()
        img_w, img_h = abs(x2 - x1), abs(y2 - y1)
        w, h = abs(xlim[1] - xlim[0]), abs(ylim[1] - ylim[0])
        if not (w > 0 and h > 0 and img_w > 0 and img_h > 0):
            return factor
        if factor > 1:
            most = MAX_ZOOM_OUT / min(w / img_w, h / img_h)
            return min(factor, max(1.0, most))
        rows, cols = self.image_plot.get_size()
        least = MIN_VIEW_PIXELS * max(img_w / cols / w, img_h / rows / h)
        return max(factor, min(1.0, least))

    def _keep_image_in_view(self, xlim, ylim):
        """The limits shifted so that the image still covers part of the view."""
        x1, x2, y1, y2 = self.image_plot.get_extent()
        return _keep_overlap(xlim, x1, x2), _keep_overlap(ylim, y1, y2)

    def _set_view(self, ax, xlim, ylim):
        """Show ``xlim``/``ylim`` (kept partly on the image), moving the beam along."""
        xlim, ylim = self._keep_image_in_view(xlim, ylim)
        self._apply_limits(ax, xlim, ylim)

    def _apply_limits(self, ax, xlim, ylim):
        ax.set_xlim(xlim)
        ax.set_ylim(ylim)
        # The beam sits in the lower left corner of the view
        if hasattr(self, "show_beam_checkbox") and self.show_beam_checkbox.isChecked():
            self._update_beam_position(ax)
        self._preview_view(ax)

    def _preview_view(self, ax):
        """Show the new view as the last drawn picture, redrawing once it settles."""
        self._settle_timer.start()
        if not self._view_preview.isVisible() and not self._start_preview(ax):
            self.canvas.draw_idle()
            return

        # Where the pictured data range lies in the new view
        xl, xr, yb, yt = self._preview_source
        (cx0, cx1), (cy0, cy1) = ax.get_xlim(), ax.get_ylim()
        bx0, by0, bw, bh = ax.bbox.bounds
        fig_h = self.canvas.figure.bbox.height
        dpr = self.canvas.device_pixel_ratio
        origin = self._view_preview.geometry().topLeft()

        def to_widget(x, y):
            px = bx0 + (x - cx0) / (cx1 - cx0) * bw
            py = fig_h - (by0 + (y - cy0) / (cy1 - cy0) * bh)
            return QPointF(px / dpr - origin.x(), py / dpr - origin.y())

        target = QRectF(to_widget(xl, yt), to_widget(xr, yb))
        overview_target = QRectF()
        if self._overview_source is not None:
            oxl, oxr, oyb, oyt = self._overview_source
            overview_target = QRectF(to_widget(oxl, oyt), to_widget(oxr, oyb))
        self._view_preview.set_target(target, overview_target)

    def _start_preview(self, ax):
        """Take the picture of the plot area. False if there is none to take."""
        dpr = self.canvas.device_pixel_ratio
        bx0, by0, bw, bh = ax.bbox.bounds
        fig_h = self.canvas.figure.bbox.height
        # Inset so the axes frame stays put
        inset = 2
        rect = QRect(
            int(np.ceil(bx0 / dpr)) + inset,
            int(np.ceil((fig_h - by0 - bh) / dpr)) + inset,
            int(bw / dpr) - 2 * inset,
            int(bh / dpr) - 2 * inset,
        )
        if rect.width() <= 0 or rect.height() <= 0:
            return False

        # Grabbing finishes any pending draw first, which updates _drawn_view
        pixmap = self.canvas.grab(rect)
        drawn = self._drawn_view
        if drawn is None or drawn[0] is not ax:
            return False
        (dx0, dx1), (dy0, dy1) = drawn[1], drawn[2]
        if not (dx1 != dx0 and dy1 != dy0):
            return False

        # The data range the picture shows
        def to_data(px, py):
            return (
                dx0 + (px * dpr - bx0) / bw * (dx1 - dx0),
                dy0 + (fig_h - py * dpr - by0) / bh * (dy1 - dy0),
            )

        xl, yt = to_data(rect.left(), rect.top())
        xr, yb = to_data(rect.left() + rect.width(), rect.top() + rect.height())
        self._preview_source = (xl, xr, yb, yt)
        overview, self._overview_source = self._overview_picture(ax)
        self._view_preview.show_picture(
            pixmap, rect, QColor.fromRgbF(*ax.get_facecolor()), overview
        )
        return True

    def _overview_picture(self, ax):
        """A coarse picture of the image within a view's width/height of the view,
        and the data range it covers; (None, None) if there is none to make."""
        im = self.image_plot
        A = im.get_array()
        if A is None or A.ndim != 2 or im.origin != "lower":
            return None, None
        rows, cols = A.shape
        x1, x2, y1, y2 = im.get_extent()
        dx, dy = (x2 - x1) / cols, (y2 - y1) / rows
        (vx0, vx1), (vy0, vy1) = sorted(ax.get_xlim()), sorted(ax.get_ylim())
        w, h = vx1 - vx0, vy1 - vy0
        j0 = int(np.clip(np.floor((vx0 - w - x1) / dx), 0, cols))
        j1 = int(np.clip(np.ceil((vx1 + w - x1) / dx), 0, cols))
        i0 = int(np.clip(np.floor((vy0 - h - y1) / dy), 0, rows))
        i1 = int(np.clip(np.ceil((vy1 + h - y1) / dy), 0, rows))
        if j1 <= j0 or i1 <= i0:
            return None, None

        step = max(1, int(np.ceil(max(j1 - j0, i1 - i0) / OVERVIEW_PIXELS)))
        rgba = im.to_rgba(A[i0:i1:step, j0:j1:step], bytes=True)
        # Image rows run bottom-up, QImage rows top-down
        rgba = np.ascontiguousarray(rgba[::-1])
        height, width = rgba.shape[:2]
        image = QImage(
            rgba.data, width, height, 4 * width, QImage.Format_RGBA8888
        ).copy()
        # Each sample stands for the step x step block it starts
        source = (
            x1 + j0 * dx,
            x1 + (j0 + width * step) * dx,
            y1 + i0 * dy,
            y1 + (i0 + height * step) * dy,
        )
        return image, source

    def _settle_view(self):
        """Redraw properly; the picture goes away when the drawing is done."""
        self._settle_timer.stop()
        if self._view_preview.isVisible():
            self.canvas.draw_idle()

    def _on_canvas_drawn(self, event):
        ax = self._nav_axes()
        self._drawn_view = (ax, ax.get_xlim(), ax.get_ylim()) if ax else None
        self._view_preview.hide()

    def _animate_view(self, ax, xlim, ylim):
        """Glide to ``xlim``/``ylim`` over GLIDE_SECONDS, easing out."""
        xlim, ylim = self._keep_image_in_view(xlim, ylim)
        if self._view_anim is None:
            self._begin_navigation(ax)
        self._view_anim = {
            "ax": ax,
            "from": (ax.get_xlim(), ax.get_ylim()),
            "xlim": xlim,
            "ylim": ylim,
            "start": time.perf_counter(),
        }
        if not self._zoom_timer.isActive():
            self._zoom_timer.start(16)

    def _perform_zoom_step(self):
        """Timer callback: move the view along the glide by elapsed time."""
        anim = self._view_anim
        if anim is None or self._nav_axes() is not anim["ax"]:
            # Finished, or the figure was redrawn for another image
            self._stop_view_anim()
            self._settle_view()
            return

        t = min(1.0, (time.perf_counter() - anim["start"]) / GLIDE_SECONDS)
        ease = 1 - (1 - t) ** 3
        (from_x, from_y) = anim["from"]
        xlim = [a + (b - a) * ease for a, b in zip(from_x, anim["xlim"])]
        ylim = [a + (b - a) * ease for a, b in zip(from_y, anim["ylim"])]
        self._apply_limits(anim["ax"], xlim, ylim)

        if t >= 1:
            self._stop_view_anim()
            self._record_view_soon()

    def _stop_view_anim(self):
        """Stop a glide where it is."""
        self._zoom_timer.stop()
        self._view_anim = None

    def _begin_navigation(self, ax):
        """Make sure the toolbar's Back can return to the view before this move."""
        toolbar = getattr(self, "nav_toolbar", None)
        if toolbar is None:
            return
        self._nav_record_timer.stop()
        views = toolbar._nav_stack()
        if views is None or ax not in views:
            toolbar.update()  # drop views of earlier images' axes
            toolbar.push_current()

    def _record_view_soon(self):
        """Add the view to the toolbar's Back/Forward history once it settles."""
        self._nav_record_timer.start()

    def _record_view(self):
        toolbar = getattr(self, "nav_toolbar", None)
        if toolbar is not None and self._nav_axes() is not None:
            toolbar.push_current()

    # ------------------------------------------------------------------
    # Mouse
    # ------------------------------------------------------------------

    def _in_measurement_mode(self):
        return (hasattr(self, "_ruler_mode") and self._ruler_mode) or (
            hasattr(self, "_profile_mode") and self._profile_mode
        )

    def _in_pan_mode(self):
        return (
            getattr(self, "region_mode", None) == RegionMode.PAN
            and not self._in_measurement_mode()
        )

    def on_mouse_move(self, event):
        if self._is_panning:
            self._pan_to_mouse(event)
            return

        if self.current_image_data is None:
            self.coord_label.setText("No image loaded")
            return

        if not event.inaxes:
            self.coord_label.setText(READOUT_HINT)
            self.canvas.setCursor(Qt.ArrowCursor)
            return

        # Priority to Matplotlib toolbar tools
        if hasattr(self, "nav_toolbar") and self.nav_toolbar.mode != "":
            return

        self.canvas.setCursor(Qt.OpenHandCursor if self._in_pan_mode() else Qt.CrossCursor)

        x, y = round(event.xdata), round(event.ydata)

        # Delegate coordinate and value formatting to Matplotlib Artists
        try:
            ax = event.inaxes
            im = self.image_plot

            # 1. Get exact World coordinates as Matplotlib would show them
            world_str = ax.format_coord(event.xdata, event.ydata)
            # Remove any trailing [value] Matplotlib might have appended to format_coord
            import re

            world_str = (
                re.sub(r"\s*\[.*?\]$", "", world_str).replace("(world)", "").strip()
            )

            # 2. The pixel value, with the image's unit
            cursor_data = im.get_cursor_data(event)
            if cursor_data is not None:
                val_str = _format_value(
                    cursor_data, getattr(self, "_current_bunit", "") or ""
                )

                pixel_info = f"<b>Pixel:</b> X={x}, Y={y}<br><b>Value:</b> {val_str}"
                world_info = f"<b>World:</b> {world_str}"
                coord_info = f"{pixel_info}<br>{world_info}"
            else:
                pixel_info = f"<b>Pixel:</b> X={x}, Y={y}"
                world_info = f"<b>World:</b> {world_str}"
                coord_info = f"{pixel_info}<br>{world_info}"

            self.coord_label.setText(coord_info)
            return

        except Exception as e:
            # Fallback to simple pixel info if Artist API fails
            logger.debug(f"Coordinate Readout Fallback: {e}")
            self.coord_label.setText(f"<b>Pixel:</b> X={x}, Y={y}")

    def _on_mouse_press(self, event):
        """Start a drag pan (middle button, or left in Pan mode); double-click zooms."""
        # Priority to Matplotlib toolbar tools
        if hasattr(self, "nav_toolbar") and self.nav_toolbar.mode != "":
            return

        ax = self._nav_axes()
        if ax is None or event.inaxes is not ax:
            return

        # The Ruler and Profile tools redraw badly if the view moves mid-measurement
        if self._in_measurement_mode():
            return

        pan_mode = self._in_pan_mode()
        if event.button == 1 and pan_mode and event.dblclick:
            shift = "shift" in getattr(event, "modifiers", ())
            factor = STEP_ZOOM if shift else 1 / STEP_ZOOM
            self._zoom_about(ax, factor, (event.xdata, event.ydata), animate=True)
            return

        if event.button == 2 or (event.button == 1 and pan_mode):
            self._stop_view_anim()
            self._begin_navigation(ax)
            self._is_panning = True
            self._pan_button = event.button
            self._pan_last_px = (event.x, event.y)
            self.canvas.setCursor(Qt.ClosedHandCursor)

    def _pan_to_mouse(self, event):
        """Move the view with the mouse so the grabbed point stays under it."""
        ax = self._nav_axes()
        if ax is None or event.x is None:
            self._is_panning = False
            return

        dx_px = event.x - self._pan_last_px[0]
        dy_px = event.y - self._pan_last_px[1]
        self._pan_last_px = (event.x, event.y)
        if not (dx_px or dy_px):
            return

        xlim, ylim = ax.get_xlim(), ax.get_ylim()
        dx = dx_px * (xlim[1] - xlim[0]) / ax.bbox.width
        dy = dy_px * (ylim[1] - ylim[0]) / ax.bbox.height
        self._set_view(ax, (xlim[0] - dx, xlim[1] - dx), (ylim[0] - dy, ylim[1] - dy))

    def _on_mouse_release(self, event):
        """Finish a drag pan."""
        if not self._is_panning or event.button != self._pan_button:
            return

        self._is_panning = False
        self._settle_view()
        self._record_view_soon()

        if event.inaxes:
            self.canvas.setCursor(
                Qt.OpenHandCursor if self._in_pan_mode() else Qt.CrossCursor
            )
        else:
            self.canvas.setCursor(Qt.ArrowCursor)

    def _on_mouse_scroll(self, event):
        """Zoom about the mouse: a glide per wheel notch, directly for trackpads."""
        # Check if feature is enabled in View menu
        main_window = self.window()
        if hasattr(main_window, "scroll_zoom_action"):
            if not main_window.scroll_zoom_action.isChecked():
                return

        ax = self._nav_axes()
        if ax is None or event.inaxes is not ax:
            return

        steps = _wheel_steps(event)
        if not steps:
            return

        # The Ruler and Profile tools redraw badly if the view moves mid-measurement
        if self._in_measurement_mode():
            self.show_status_message(
                "Scroll to zoom suspended in measurement mode", timeout=2000
            )
            return

        # Trackpads send many small steps; gliding each would lag behind the fingers
        animate = abs(steps) >= 1 and not self._is_panning
        self._zoom_about(
            ax, WHEEL_ZOOM ** -steps, (event.xdata, event.ydata), animate=animate
        )

    def _on_pinch(self, event):
        """Trackpad pinch: zoom about the fingers."""
        ax = self._nav_axes()
        if ax is None or self._in_measurement_mode():
            return
        x, y = self.canvas.mouseEventCoords(event)
        if not ax.bbox.contains(x, y):
            return
        anchor = tuple(ax.transData.inverted().transform((x, y)))
        factor = 1 / max(0.1, 1 + event.value())
        self._zoom_about(ax, factor, anchor, animate=False)

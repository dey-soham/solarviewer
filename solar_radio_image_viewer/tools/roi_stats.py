"""
ROI statistics tool for SolarViewer.
"""

import itertools
import math

import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt5.QtCore import QEvent, QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QCursor, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..ui.geometry import fit_to_screen
from ..ui.styles import set_hand_cursor
from .preview import contrast_colors

NO_REGION_HINT = "No selection. Draw a rectangle or ellipse on the image."
# A drag shorter than this (screen pixels) either way is a click
CLICK_PX = 3
# The selected region's handles: their size, and how near the mouse must be
# to grab one (screen pixels)
HANDLE_PX = 7
GRAB_PX = 6
# Which sides of the region's box each handle moves, (x side, y side) with
# 0 the low side, 1 the high side and None neither; the corners first
HANDLES = ((0, 0), (1, 0), (1, 1), (0, 1), (None, 0), (1, None), (None, 1), (0, None))
# The selected ellipse's round handle, which turns it: how far out from its
# box it is and its radius (screen pixels), and the steps Shift turns it by
TURN_PX = 22
TURN_RADIUS = 4.5
TURN_STEP = 15
_keys = itertools.count(1)
_turn_cursors = {}


class RegionMode:
    RECTANGLE = 0
    ELLIPSE = 1
    PAN = 2


def _box(corners):
    """``corners`` (x1, y1, x2, y2) in order: low x and y first."""
    x1, y1, x2, y2 = (float(v) for v in corners)
    return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))


def _centre(corners):
    x1, y1, x2, y2 = corners
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def _turned(points, centre, angle):
    """``points`` turned ``angle`` degrees anticlockwise (in pixels of the
    image) about ``centre``."""
    if not angle:
        return [(float(x), float(y)) for x, y in points]
    t = math.radians(angle)
    c, s = math.cos(t), math.sin(t)
    cx, cy = centre
    return [(cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c) for x, y in points]


class Region:
    """A rectangle or ellipse on a pane: ``shape`` ("rect" or "ellipse") in
    the box with ``corners`` (x1, y1, x2, y2), in pixels of the image,
    turned ``angle`` degrees anticlockwise about the box's centre. Its
    copies in linked panes share its ``key``."""

    __slots__ = ("shape", "corners", "key", "angle")

    def __init__(self, shape, corners, key=None, angle=0.0):
        self.shape = shape
        self.corners = _box(corners)
        self.key = key if key is not None else next(_keys)
        self.angle = float(angle)

    def outline(self, corners=None, angle=None):
        """The outline as points (pixels of the image), closed; in the box
        ``corners`` turned by ``angle`` instead of its own if given (while
        it is dragged)."""
        return region_outline(self.shape, corners or self.corners, self.angle if angle is None else angle)

    def contains(self, x, y):
        x1, y1, x2, y2 = self.corners
        cx, cy = _centre(self.corners)
        ((x, y),) = _turned([(x, y)], (cx, cy), -self.angle)  # (into its own frame)
        if self.shape == "rect":
            return x1 <= x <= x2 and y1 <= y <= y2
        a, b = (x2 - x1) / 2, (y2 - y1) / 2
        return a > 0 and b > 0 and ((x - cx) / a) ** 2 + ((y - cy) / b) ** 2 <= 1

    def area(self):
        x1, y1, x2, y2 = self.corners
        return (x2 - x1) * (y2 - y1)


def region_outline(shape, corners, angle=0.0):
    x1, y1, x2, y2 = corners
    if shape == "rect":
        points = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    else:
        t = np.linspace(0, 2 * np.pi, 120, endpoint=False)
        cx, cy, a, b = (x1 + x2) / 2, (y1 + y2) / 2, abs(x2 - x1) / 2, abs(y2 - y1) / 2
        points = list(zip(cx + a * np.cos(t), cy + b * np.sin(t)))
    return _turned(points, _centre(corners), angle)


def handle_points(corners, angle=0.0):
    """Where the handles of a region in the box ``corners`` turned by
    ``angle`` are (pixels of the image), in the order of HANDLES."""
    x1, y1, x2, y2 = corners
    xs, ys = (x1, x2, (x1 + x2) / 2), (y1, y2, (y1 + y2) / 2)
    points = [(xs[2 if hx is None else hx], ys[2 if hy is None else hy]) for hx, hy in HANDLES]
    return _turned(points, (xs[2], ys[2]), angle)


def resized(corners, handle, x, y, angle=0.0):
    """The box ``corners`` (of a region turned by ``angle``) with the sides
    that ``handle`` moves taken to (x, y) and the others where they are; it
    may turn over."""
    x1, y1, x2, y2 = corners
    centre = _centre(corners)
    # In the region's own frame, where its box is upright
    ((x, y),) = _turned([(x, y)], centre, -angle)
    xs, ys = [x1, x2], [y1, y2]
    hx, hy = HANDLES[handle]
    if hx is not None:
        xs[hx] = x
    if hy is not None:
        ys[hy] = y
    if not angle:
        return (xs[0], ys[0], xs[1], ys[1])
    # The box's new centre, back in the image, turned about the old one
    ((cx, cy),) = _turned([((xs[0] + xs[1]) / 2, (ys[0] + ys[1]) / 2)], centre, angle)
    half_w, half_h = abs(xs[1] - xs[0]) / 2, abs(ys[1] - ys[0]) / 2
    return (cx - half_w, cy - half_h, cx + half_w, cy + half_h)


def turned_to(region, start, now, snap=False):
    """``region``'s angle once its round handle is dragged from ``start``
    to ``now`` (pixels of the image), in TURN_STEP steps with ``snap``; in
    (-180, 180]."""
    cx, cy = _centre(region.corners)
    angle = region.angle + math.degrees(
        math.atan2(now[1] - cy, now[0] - cx) - math.atan2(start[1] - cy, start[0] - cx)
    )
    if snap:
        angle = round(angle / TURN_STEP) * TURN_STEP
    angle = -((-angle + 180) % 360 - 180)  # (-180, 180]
    return 0.0 if abs(angle) < 1e-9 else angle


def turn_cursor(dpr):
    """The cursor over the round handle: a curved arrow, light over dark."""
    key = round(dpr * 100)
    cursor = _turn_cursors.get(key)
    if cursor is not None:
        return cursor
    size = 24
    pixmap = QPixmap(round(size * dpr), round(size * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    # Three quarters of a circle, from the top round to the left, clockwise
    arc = QPainterPath()
    box = QRectF(5.5, 5.5, 13, 13)
    arc.arcMoveTo(box, 90)
    arc.arcTo(box, 90, -270)
    # Its head at the left, pointing up
    head = QPolygonF([QPointF(5.5, 7.5), QPointF(2.0, 12.5), QPointF(9.0, 12.5)])
    for colour, width in ((QColor(0, 0, 0, 200), 3.6), (QColor(255, 255, 255), 1.6)):
        painter.setPen(QPen(colour, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(arc)
        painter.setBrush(colour)
        painter.drawPolygon(head)
    painter.end()
    cursor = _turn_cursors[key] = QCursor(pixmap)  # (the hot spot in the middle)
    return cursor


class RegionLayer(QWidget):
    """A pane's regions, drawn in Qt over its canvas.

    Drawing, selecting, moving or deleting a region, or moving the view,
    repaints this layer alone; the Matplotlib figure is never redrawn for
    it. Each region is a thin line over a soft halo, in the colours that
    contrast with the image. With the Rectangle or Ellipse tool, the
    selected region is drawn stronger, faintly filled, with handles to
    resize it and a round one to turn it; with the other tools every region
    looks the same.
    """

    def __init__(self, tab):
        super().__init__(tab.canvas)
        self._tab = tab
        self.preview = None  # (shape, corners) of a region being drawn
        self.edited = None  # (region, corners, angle) of a region being moved, resized or turned
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        tab.canvas.region_layer = self  # (the canvas's other overlays raise it)
        tab.canvas.installEventFilter(self)
        self.setGeometry(tab.canvas.rect())
        self.show()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Resize:
            self.setGeometry(watched.rect())
        return False

    def refresh(self):
        """Paint again (the regions, the view or the image changed)."""
        self.raise_()
        self.update()  # (the whole layer: a part would repaint a part of the canvas)

    def to_widget(self, points):
        """Points in pixels of the image to this layer's (logical) pixels."""
        tab = self._tab
        ax = tab._nav_axes()
        dpr = tab.canvas.device_pixel_ratio
        fig_h = tab.canvas.figure.bbox.height
        return [QPointF(px / dpr, (fig_h - py) / dpr) for px, py in ax.transData.transform(points)]

    def shown_handles(self, corners, angle=0.0):
        """The handles shown on a region in the box ``corners`` turned by
        ``angle``, as (index, point in this layer); the side handles only
        when the region is long enough on screen for them."""
        points = self.to_widget(handle_points(corners, angle))
        width = math.hypot(points[1].x() - points[0].x(), points[1].y() - points[0].y())
        height = math.hypot(points[3].x() - points[0].x(), points[3].y() - points[0].y())
        room = 4 * HANDLE_PX
        shown = []
        for index, point in enumerate(points):
            hx, hy = HANDLES[index]
            if (hx is None and width < room) or (hy is None and height < room):
                continue
            shown.append((index, point))
        return shown

    def turn_handle(self, corners, angle):
        """Where a region's round handle is in this layer, and the middle of
        the side of its box it stands out from: the side at the top when
        the region is upright."""
        cx, cy = _centre(corners)
        x1, y1, x2, y2 = corners
        low, high = self.to_widget([(cx, y1), (cx, y2)])
        top = y2 if high.y() <= low.y() else y1  # (an axis may run either way)
        centre, foot = self.to_widget(_turned([(cx, cy), (cx, top)], (cx, cy), angle))
        dx, dy = foot.x() - centre.x(), foot.y() - centre.y()
        length = math.hypot(dx, dy)
        if length < 1e-6:
            dx, dy, length = 0.0, -1.0, 1.0
        return QPointF(foot.x() + dx / length * TURN_PX, foot.y() + dy / length * TURN_PX), foot

    def paintEvent(self, event):
        tab = self._tab
        ax = tab._nav_axes()
        if ax is None or not (tab.regions or self.preview):
            return
        dpr = tab.canvas.device_pixel_ratio
        fig_h = tab.canvas.figure.bbox.height
        x0, y0, w, h = ax.bbox.bounds
        clip = QRectF(QPointF(x0 / dpr, (fig_h - y0 - h) / dpr), QPointF((x0 + w) / dpr, (fig_h - y0) / dpr))
        line, halo = (QColor(c) for c in contrast_colors(tab))
        selected = tab.selected_region if tab._region_tool() else None
        edited, edited_corners, edited_angle = self.edited or (None, None, None)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setClipRect(clip)
        for region in tab.regions:
            if region is not selected:
                if region is edited:
                    points = region.outline(edited_corners, edited_angle)
                else:
                    points = region.outline()
                self._outline(painter, points, line, halo, strong=False)
        if selected is not None:
            if selected is edited:
                corners, angle = edited_corners, edited_angle
            else:
                corners, angle = selected.corners, selected.angle
            self._outline(painter, selected.outline(corners, angle), line, halo, strong=True)
            if selected.shape == "ellipse":
                # The box the handles are on, faintly
                faint = QColor(line)
                faint.setAlphaF(0.35)
                painter.setPen(QPen(faint, 1.0))
                painter.setBrush(Qt.NoBrush)
                painter.drawPolygon(QPolygonF(self.to_widget(region_outline("rect", corners, angle))))
            # The round handle that turns it, on a stalk from its box
            knob, foot = self.turn_handle(corners, angle)
            painter.setPen(QPen(halo, 3.0, Qt.SolidLine, Qt.FlatCap))
            painter.drawLine(foot, knob)
            painter.setPen(QPen(line, 1.0, Qt.SolidLine, Qt.FlatCap))
            painter.drawLine(foot, knob)
            painter.setPen(QPen(halo, 1.0))
            painter.setBrush(line)
            half = HANDLE_PX / 2
            for _index, point in self.shown_handles(corners, angle):
                painter.drawRect(QRectF(point.x() - half, point.y() - half, HANDLE_PX, HANDLE_PX))
            painter.drawEllipse(knob, TURN_RADIUS, TURN_RADIUS)
        if self.preview is not None:
            self._outline(painter, region_outline(*self.preview), line, halo, strong=True)
        painter.end()

    def _outline(self, painter, points, line, halo, strong):
        """A region's outline: a thin line over a soft halo; ``strong`` (the
        selected region, or one being drawn) is a little heavier and
        faintly filled."""
        path = QPainterPath()
        path.addPolygon(QPolygonF(self.to_widget(points)))
        path.closeSubpath()
        width = 1.6 if strong else 1.2
        if strong:
            fill = QColor(line)
            fill.setAlphaF(0.1)
            painter.fillPath(path, fill)
        shadow = QColor(halo)
        shadow.setAlphaF(0.55)
        painter.strokePath(path, QPen(shadow, width + 2.0, Qt.SolidLine, Qt.RoundCap, Qt.MiterJoin))
        colour = QColor(line)
        colour.setAlphaF(1.0 if strong else 0.85)
        painter.strokePath(path, QPen(colour, width, Qt.SolidLine, Qt.RoundCap, Qt.MiterJoin))


class RoiStatsMixin:
    """ROI selection (rectangle/ellipse), ROI statistics and the ROI histogram.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``canvas``, ``current_image_data``, ``current_wcs``, ``info_label``,
    ``_in_measurement_mode``, ``_nav_axes``, ``parent``, ``region_mode``,
    ``_region_layer``, ``region_links_dot``, ``regions``, ``selected_region``,
    ``show_status_message``, ``stats_table``
    """

    def show_roi_stats(self, roi, ra_dec_info=""):
        if roi.size == 0:  # (a region off the image)
            self.current_roi = None
            self._hide_roi_stats()
            return

        self._roi_values = roi  # (for the histogram: an ellipse's pixels, not its box's)
        rmin = np.nanmin(roi)
        rmax = np.nanmax(roi)
        rmean = np.nanmean(roi)
        rstd = np.nanstd(roi)
        rsum = np.nansum(roi)
        rrms = np.sqrt(np.nanmean(roi**2))

        self.info_label.setText(f"{ra_dec_info}")

        stats_values = [rmin, rmax, rmean, rstd, rsum, rrms]
        for i, val in enumerate(stats_values):
            self.stats_table.setItem(i, 1, QTableWidgetItem(f"{val:.6g}"))
        self.stats_table.show()  # (hidden until there are statistics)
        self.histogram_btn.show()
        self.fit_gaussian_btn.show()
        self.export_region_btn.show()

        if self.window():
            self.show_status_message(
                f"ROI selected: {roi.size} pixels, Mean={rmean:.4g}, Sum={rsum:.4g}, RMS={rrms:.4g}"
            )

    def _show_roi_histogram(self):
        """Show histogram of pixel values in current ROI with fitting options"""
        from scipy.optimize import curve_fit
        from scipy.stats import poisson

        if self.current_roi is None or self.current_image_data is None:
            QMessageBox.warning(self, "No ROI", "Please select a region first")
            return

        # The pixels the statistics are of
        roi_flat = np.asarray(self._roi_values, dtype=float).ravel()
        roi_flat = roi_flat[~np.isnan(roi_flat)]  # Remove NaNs

        if roi_flat.size == 0:
            QMessageBox.warning(
                self, "Empty ROI", "Selected region contains no valid data"
            )
            return

        # Create dialog - resizable
        dialog = QDialog(self)
        dialog.setWindowTitle("ROI Histogram")
        set_hand_cursor(dialog)
        dialog.resize(800, 650)
        dialog.setMinimumSize(600, 400)
        fit_to_screen(dialog, 600, 400)
        layout = QVBoxLayout(dialog)

        # Create figure
        fig = Figure(figsize=(8, 5))
        canvas = FigureCanvas(fig)
        layout.addWidget(canvas, stretch=1)  # Make canvas expand

        # Controls row 1
        controls_layout1 = QHBoxLayout()

        # Number of bins
        controls_layout1.addWidget(QLabel("Bins:"))
        bins_spin = QSpinBox()
        bins_spin.setRange(10, 500)
        bins_spin.setValue(50)
        controls_layout1.addWidget(bins_spin)

        # Log scale checkbox
        log_check = QCheckBox("Log Scale")
        controls_layout1.addWidget(log_check)

        # Show statistics checkbox
        stats_check = QCheckBox("Show Stats")
        stats_check.setChecked(True)
        controls_layout1.addWidget(stats_check)

        controls_layout1.addStretch()
        layout.addLayout(controls_layout1)

        # Controls row 2 - Fitting options
        controls_layout2 = QHBoxLayout()
        controls_layout2.addWidget(QLabel("Fit:"))

        gaussian_check = QCheckBox("Gaussian")
        controls_layout2.addWidget(gaussian_check)

        poisson_check = QCheckBox("Poisson")
        controls_layout2.addWidget(poisson_check)

        controls_layout2.addStretch()

        # Update button
        update_btn = QPushButton("Update")
        controls_layout2.addWidget(update_btn)

        layout.addLayout(controls_layout2)

        # Statistics info label
        stats_label = QLabel()
        stats_label.setObjectName("Mono")
        stats_label.setMargin(5)
        stats_label.setWordWrap(True)
        layout.addWidget(stats_label)

        # Fit results label
        fit_label = QLabel()
        fit_label.setObjectName("Mono")
        fit_label.setMargin(5)
        fit_label.setWordWrap(True)
        layout.addWidget(fit_label)

        # Close button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        def gaussian(x, amp, mean, sigma):
            return amp * np.exp(-((x - mean) ** 2) / (2 * sigma**2))

        def update_histogram():
            fig.clear()
            ax = fig.add_subplot(111)

            n_bins = bins_spin.value()
            use_log = log_check.isChecked()
            show_stats = stats_check.isChecked()
            fit_gaussian = gaussian_check.isChecked()
            fit_poisson = poisson_check.isChecked()

            # Calculate statistics
            mean_val = np.mean(roi_flat)
            median_val = np.median(roi_flat)
            std_val = np.std(roi_flat)
            min_val = np.min(roi_flat)
            max_val = np.max(roi_flat)

            # Plot histogram
            counts, bins_edges, patches = ax.hist(
                roi_flat,
                bins=n_bins,
                color="#2196F3",
                edgecolor="#1565C0",
                alpha=0.7,
                density=False,
            )

            # Bin centers for fitting
            bin_centers = (bins_edges[:-1] + bins_edges[1:]) / 2
            bin_width = bins_edges[1] - bins_edges[0]

            fit_results = []

            # Gaussian fit
            if fit_gaussian:
                try:
                    # Initial guesses
                    p0 = [np.max(counts), mean_val, std_val]
                    popt, pcov = curve_fit(
                        gaussian, bin_centers, counts, p0=p0, maxfev=5000
                    )
                    perr = np.sqrt(np.diag(pcov))

                    # Plot fit
                    x_fit = np.linspace(min_val, max_val, 300)
                    y_fit = gaussian(x_fit, *popt)
                    ax.plot(x_fit, y_fit, "r-", linewidth=2, label="Gaussian Fit")

                    # Calculate chi-square
                    y_pred = gaussian(bin_centers, *popt)
                    mask = counts > 0
                    chi2 = np.sum((counts[mask] - y_pred[mask]) ** 2 / counts[mask])
                    dof = np.sum(mask) - 3  # degrees of freedom
                    chi2_red = chi2 / dof if dof > 0 else np.nan

                    fit_results.append(
                        f"Gaussian: Amp={popt[0]:.4g}±{perr[0]:.2g}, "
                        f"Mean={popt[1]:.4g}±{perr[1]:.2g}, "
                        f"σ={popt[2]:.4g}±{perr[2]:.2g} | "
                        f"χ²={chi2:.2f}, Red. χ²={chi2_red:.3f}"
                    )
                except Exception as e:
                    fit_results.append(f"Gaussian fit failed: {str(e)}")

            # Poisson fit (for count-like data)
            if fit_poisson:
                try:
                    # Poisson is discrete - scale to match histogram
                    lambda_est = mean_val
                    if lambda_est > 0:
                        x_poisson = np.arange(max(0, int(min_val)), int(max_val) + 1)
                        y_poisson = (
                            poisson.pmf(x_poisson, lambda_est)
                            * len(roi_flat)
                            * bin_width
                        )
                        ax.plot(
                            x_poisson,
                            y_poisson,
                            "g-",
                            linewidth=2,
                            label=f"Poisson (λ={lambda_est:.2f})",
                        )

                        # Chi-square for Poisson
                        y_pred_p = (
                            poisson.pmf(bin_centers.astype(int), lambda_est)
                            * len(roi_flat)
                            * bin_width
                        )
                        mask = counts > 0
                        chi2_p = np.sum(
                            (counts[mask] - y_pred_p[mask]) ** 2 / counts[mask]
                        )
                        dof_p = np.sum(mask) - 1
                        chi2_red_p = chi2_p / dof_p if dof_p > 0 else np.nan

                        fit_results.append(
                            f"Poisson: λ={lambda_est:.4g} (from data mean) | "
                            f"χ²={chi2_p:.2f}, Red. χ²={chi2_red_p:.3f}"
                        )
                    else:
                        fit_results.append("Poisson fit: λ must be positive")
                except Exception as e:
                    fit_results.append(f"Poisson fit failed: {str(e)}")

            # Add statistics lines
            if show_stats:
                ax.axvline(
                    mean_val,
                    color="red",
                    linewidth=2,
                    linestyle="-",
                    label=f"Mean: {mean_val:.4g}",
                )
                ax.axvline(
                    median_val,
                    color="green",
                    linewidth=2,
                    linestyle="--",
                    label=f"Median: {median_val:.4g}",
                )
                ax.axvline(
                    mean_val - std_val,
                    color="orange",
                    linewidth=1.5,
                    linestyle=":",
                    label="Mean±σ",
                )
                ax.axvline(
                    mean_val + std_val, color="orange", linewidth=1.5, linestyle=":"
                )

            if fit_gaussian or fit_poisson or show_stats:
                ax.legend(loc="upper right", fontsize=9)

            if use_log:
                ax.set_yscale("log")

            ax.set_xlabel("Pixel Value")
            ax.set_ylabel("Count")
            ax.set_title(f"ROI Histogram ({roi_flat.size:,} pixels)")
            ax.grid(True, alpha=0.3)

            fig.tight_layout()
            canvas.draw()

            # Update stats label
            stats_text = (
                f"Min: {min_val:.6g}  |  Max: {max_val:.6g}  |  "
                f"Mean: {mean_val:.6g}  |  Median: {median_val:.6g}  |  "
                f"Std Dev: {std_val:.6g}  |  Pixels: {roi_flat.size:,}"
            )
            stats_label.setText(stats_text)

            # Update fit label
            if fit_results:
                fit_label.setText("\n".join(fit_results))
            else:
                fit_label.setText("")

        # Connect signals
        update_btn.clicked.connect(update_histogram)
        bins_spin.valueChanged.connect(update_histogram)
        log_check.stateChanged.connect(update_histogram)
        stats_check.stateChanged.connect(update_histogram)
        gaussian_check.stateChanged.connect(update_histogram)
        poisson_check.stateChanged.connect(update_histogram)

        # Initial plot
        update_histogram()

        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

    # ------------------------------------------------------------------
    # The regions: drawn with the Rectangle and Ellipse tools, kept until
    # deleted; the selected one's statistics are shown
    # ------------------------------------------------------------------

    def on_rectangle(self, eclick, erelease):
        """A rectangle from ``eclick`` to ``erelease`` (anything with
        ``xdata`` and ``ydata``) is added as a region."""
        self.add_region("rect", (eclick.xdata, eclick.ydata, erelease.xdata, erelease.ydata))

    def on_ellipse(self, eclick, erelease):
        """The ellipse in the box from ``eclick`` to ``erelease`` is added as a region."""
        self.add_region("ellipse", (eclick.xdata, eclick.ydata, erelease.xdata, erelease.ydata))

    def add_region(self, shape, corners, key=None, angle=0.0, follow=True, select=True):
        """Add a ``shape`` region in the box with ``corners``, turned by
        ``angle``, and with ``select`` select it; with ``follow``, the panes
        following this one get a copy."""
        if self.current_image_data is None or None in corners:
            return None
        region = Region(shape, corners, key, angle)
        self.regions.append(region)
        if select:
            self.select_region(region, follow=False)
        else:
            self._region_layer.refresh()
            self._refresh_region_controls()
        page = getattr(self, "page", None)
        if follow and page is not None:
            page.sync.region_added(self, region)
        return region

    def edit_region(self, region, corners, angle=None, follow=True):
        """``region`` moved or resized to the box ``corners`` (and turned to
        ``angle``, if given): measured again, and with ``follow`` its copies
        in the panes following this one move with it."""
        region.corners = _box(corners)
        if angle is not None:
            region.angle = float(angle)
        if region is self.selected_region:
            self._measure_selected()
        self._region_layer.refresh()
        page = getattr(self, "page", None)
        if follow and page is not None:
            page.sync.region_changed(self, region)

    def select_region(self, region, follow=True):
        """Select ``region`` (or nothing, None): its statistics are shown,
        and (with ``follow``) its copies are selected in the panes
        following this one."""
        self.selected_region = region
        self._measure_selected()
        self._region_layer.refresh()
        page = getattr(self, "page", None)
        if follow and page is not None:
            page.sync.region_selected(self, region)

    def delete_region(self, region=None, follow=True):
        """Delete ``region`` (the selected one by default), and with
        ``follow`` its copies in the panes following this one."""
        region = region or self.selected_region
        if region is None or region not in self.regions:
            return
        self.regions.remove(region)
        if region is self.selected_region:
            self.select_region(None, follow=False)
        else:
            self._region_layer.refresh()
            self._refresh_region_controls()
        page = getattr(self, "page", None)
        if follow:
            if page is not None:
                page.sync.region_deleted(self, region.key)
            self.show_status_message("Region deleted")

    def clear_regions(self, follow=True):
        """Delete every region (and with ``follow`` their copies)."""
        for region in list(self.regions):
            self.delete_region(region, follow=follow)
        if follow:
            self.show_status_message("Regions cleared")

    def _confirm_clear_regions(self):
        count = len(self.regions)
        if count and QMessageBox.question(
            self,
            "Clear Regions",
            f"Delete all {count} region{'s' * (count != 1)} on this image?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) == QMessageBox.Yes:
            self.clear_regions()

    def region_by_key(self, key):
        return next((region for region in self.regions if region.key == key), None)

    def _measure_selected(self):
        """The selected region's statistics, from the image as it is now."""
        self._measured_data = self.current_image_data
        region = self.selected_region
        if region is None or self.current_image_data is None:
            self.current_roi = None
            self._hide_roi_stats()
        elif region.shape == "rect" and region.angle % 90 == 0:
            # Upright, or turned by quarters: the pixels of its box
            xs, ys = zip(*region.outline())
            self._rect_stats(min(xs), min(ys), max(xs), max(ys))
        else:
            self._masked_stats(region.shape, *region.corners, angle=region.angle)
        self._refresh_region_controls()
        page = getattr(self, "page", None)
        if page is not None:
            page.regions_changed()

    def _hide_roi_stats(self):
        for widget in (self.stats_table, self.histogram_btn, self.fit_gaussian_btn,
                       self.export_region_btn):
            widget.hide()
        if self.selected_region is not None:
            self.info_label.setText("The selected region is off the image.")
            return
        count = len(self.regions)
        self.info_label.setText(
            f"{count} region{'s' * (count != 1)}. Select one with the Rectangle or "
            "Ellipse tool." if count else NO_REGION_HINT
        )

    def _refresh_region_controls(self):
        """Delete and Clear all, beside the Region heading, with the
        Rectangle or Ellipse tool only: the other tools leave the regions be."""
        editing = self._region_tool()
        delete = editing and self.selected_region is not None
        clear = editing and bool(self.regions)
        self.delete_region_btn.setVisible(delete)
        self.region_links_dot.setVisible(delete and clear)
        self.clear_regions_btn.setVisible(clear)

    def region_tool_changed(self):
        """Another tool: the regions are drawn (and offered for editing)
        as it wants."""
        if not hasattr(self, "_region_layer"):
            return  # (still being built)
        self._region_press_at = None
        self._region_layer.preview = self._region_layer.edited = None
        self._region_layer.refresh()
        self._refresh_region_controls()

    def init_region_editor(self, ax):
        """After a plot or a change of tool: the regions painted again, and
        measured again if the image changed under them (those no longer on
        the image go)."""
        data = self.current_image_data
        if self.regions and self._measured_data is not data:
            for region in list(self.regions):
                x1, y1, x2, y2 = region.corners
                # Display x runs along the data's first axis, y along its second
                if data is None or x2 < 0 or x1 > data.shape[0] or y2 < 0 or y1 > data.shape[1]:
                    self.delete_region(region, follow=False)
            self._measure_selected()
        self.region_tool_changed()

    def _region_tool(self):
        """Whether the Rectangle or Ellipse tool is the one in use."""
        return (
            getattr(self, "region_mode", RegionMode.PAN) in (RegionMode.RECTANGLE, RegionMode.ELLIPSE)
            and not self._in_measurement_mode()
        )

    # ------------------------------------------------------------------
    # The mouse, with the Rectangle or Ellipse tool: a drag draws a region,
    # a click selects one; the selected one is dragged to move it, by its
    # handles to resize it and by its round handle to turn it
    # ------------------------------------------------------------------

    def _grab_at(self, event):
        """What of the selected region the mouse at ``event`` would take:
        ("turn", None), ("handle", index), ("move", None), or None."""
        region = self.selected_region
        ax = self._nav_axes()
        if region is None or ax is None or event.x is None:
            return None
        dpr = self.canvas.device_pixel_ratio
        fig_h = self.canvas.figure.bbox.height
        mouse = QPointF(event.x / dpr, (fig_h - event.y) / dpr)
        layer = self._region_layer
        knob, _foot = layer.turn_handle(region.corners, region.angle)
        if math.hypot(knob.x() - mouse.x(), knob.y() - mouse.y()) <= GRAB_PX + 2:
            return ("turn", None)
        near = [
            (math.hypot(point.x() - mouse.x(), point.y() - mouse.y()), index)
            for index, point in layer.shown_handles(region.corners, region.angle)
        ]
        if near and min(near)[0] <= GRAB_PX:
            return ("handle", min(near)[1])
        if event.xdata is not None and region.contains(event.xdata, event.ydata):
            return ("move", None)
        return None

    def region_cursor(self, event):
        """The cursor for the mouse at ``event``: a move, resize or turn
        cursor over the selected region, else None (the tool's own)."""
        if not self._region_tool():
            return None
        press = self._region_press_at
        grab = press["grab"] if press is not None else self._grab_at(event)
        if grab is None:
            return None
        if grab[0] == "move":
            return Qt.SizeAllCursor
        if grab[0] == "turn":
            return turn_cursor(self.canvas.device_pixel_ratio)
        # The way the handle pulls on screen: out from the middle along the
        # region's own axes, the corners at 45° (an axis may run backwards)
        region = self.selected_region
        hx, hy = HANDLES[grab[1]]
        pull = (0 if hx is None else 2 * hx - 1, 0 if hy is None else 2 * hy - 1)
        ((dx, dy),) = _turned([pull], (0.0, 0.0), region.angle)
        cx, cy = _centre(region.corners)
        (px0, py0), (px1, py1) = self._nav_axes().transData.transform([(cx, cy), (cx + dx, cy + dy)])
        way = math.degrees(math.atan2(py1 - py0, px1 - px0)) % 180  # (display y runs up)
        return (Qt.SizeHorCursor, Qt.SizeBDiagCursor, Qt.SizeVerCursor, Qt.SizeFDiagCursor)[int((way + 22.5) // 45) % 4]

    def _region_press(self, event):
        ax = self._nav_axes()
        if event.button != 1 or ax is None or event.inaxes is not ax or not self._region_tool():
            return
        self._region_press_at = dict(
            x=event.x, y=event.y, data=(event.xdata, event.ydata), grab=self._grab_at(event), dragging=False
        )

    def _region_motion(self, event):
        press = self._region_press_at
        if press is None or event.x is None:
            return
        if not press["dragging"]:
            if max(abs(event.x - press["x"]), abs(event.y - press["y"])) <= CLICK_PX:
                return
            press["dragging"] = True
        x, y = self._data_in_axes(event)
        x0, y0 = press["data"]
        layer = self._region_layer
        grab = press["grab"]
        if grab is None:
            if "shift" in (event.key or ""):  # a square or a circle
                side = max(abs(x - x0), abs(y - y0))
                x = x0 + np.copysign(side, x - x0)
                y = y0 + np.copysign(side, y - y0)
            shape = "rect" if self.region_mode == RegionMode.RECTANGLE else "ellipse"
            layer.preview = (shape, (x0, y0, x, y))
        else:
            region = self.selected_region
            if region is None:
                return  # (deleted meanwhile, with the Delete key)
            corners, angle = region.corners, region.angle
            if grab[0] == "move":
                x1, y1, x2, y2 = corners
                corners = (x1 + x - x0, y1 + y - y0, x2 + x - x0, y2 + y - y0)
            elif grab[0] == "turn":
                angle = turned_to(region, (x0, y0), (x, y), snap="shift" in (event.key or ""))
            else:
                corners = resized(corners, grab[1], x, y, angle)
            layer.edited = (region, corners, angle)
        layer.refresh()

    def _data_in_axes(self, event):
        """The mouse's position in pixels of the image, kept to the axes."""
        ax = self._nav_axes()
        box = ax.bbox
        px = min(max(event.x, box.x0), box.x1)
        py = min(max(event.y, box.y0), box.y1)
        return ax.transData.inverted().transform((px, py))

    def _big_enough(self, corners):
        """Whether a box is more than a click across on screen."""
        ax = self._nav_axes()
        (px1, py1), (px2, py2) = ax.transData.transform([corners[:2], corners[2:]])
        return min(abs(px2 - px1), abs(py2 - py1)) > CLICK_PX

    def _region_release(self, event):
        press, self._region_press_at = self._region_press_at, None
        if press is None or event.button != 1:
            return
        layer = self._region_layer
        preview, layer.preview = layer.preview, None
        edited, layer.edited = layer.edited, None
        if press["dragging"]:
            if preview is not None and self._big_enough(preview[1]):
                self.add_region(*preview)
            elif edited is not None and (press["grab"][0] != "handle" or self._big_enough(edited[1])):
                self.edit_region(*edited)
            else:
                layer.refresh()  # (too thin to be a region)
            return
        if press["grab"] is not None and press["grab"][0] in ("handle", "turn"):
            return  # (a click on a handle)
        # A click: the region under it (the smallest, if they overlap), or none
        x, y = press["data"]
        hits = [region for region in reversed(self.regions) if region.contains(x, y)]
        region = min(hits, key=Region.area) if hits else None
        if region is not self.selected_region:
            self.select_region(region)
        if region is not None:
            self.show_status_message(
                f"{'Ellipse' if region.shape == 'ellipse' else 'Rectangle'} selected: drag it to "
                "move it, its handles to resize it and the round one to rotate it "
                f"(Shift: {TURN_STEP}° steps); Delete removes it, Esc deselects"
            )

    def _on_region_key(self, event):
        """With the Rectangle or Ellipse tool, Delete or Backspace deletes
        the selected region and Esc deselects it (or drops a drag)."""
        if not self._region_tool():
            return
        layer = self._region_layer
        if event.key == "escape":
            if layer.preview is not None or layer.edited is not None:
                self._region_press_at = layer.preview = layer.edited = None
                layer.refresh()
            elif self.selected_region is not None:
                self.select_region(None)
        elif event.key in ("delete", "backspace") and self.selected_region is not None:
            self.delete_region()

    def _rect_stats(self, x1, y1, x2, y2):
        """Show the statistics of the rectangle from (x1, y1) to (x2, y2)."""
        # (a hair under a whole pixel, as the mouse lands, is that pixel)
        x1, x2, y1, y2 = (int(np.floor(v + 1e-6)) for v in (x1, x2, y1, y2))

        xlow, xhigh = sorted([x1, x2])
        ylow, yhigh = sorted([y1, y2])

        xlow = max(0, xlow)
        ylow = max(0, ylow)
        xhigh = min(self.current_image_data.shape[0], xhigh)
        yhigh = min(self.current_image_data.shape[1], yhigh)

        self.current_roi = (xlow, xhigh, ylow, yhigh)
        roi = self.current_image_data[xlow:xhigh, ylow:yhigh]

        ra_dec_info = ""
        if self.current_wcs:
            try:
                import astropy.units as u
                from astropy.coordinates import SkyCoord
                from astropy.wcs import WCS

                ref_val = self.current_wcs.referencevalue()["numeric"][0:2]
                ref_pix = self.current_wcs.referencepixel()["numeric"][0:2]
                increment = self.current_wcs.increment()["numeric"][0:2]

                w = WCS(naxis=2)
                w.wcs.crpix = [
                    ref_pix[0] + 1,
                    ref_pix[1] + 1,
                ]  # CASA 0-indexed -> FITS 1-indexed
                _hdr = getattr(self, "_cached_fits_header", None) or {}
                _is_solar = "SOLAR-X" in str(_hdr.get("CTYPE1", "")).upper()
                if _is_solar:
                    w.wcs.crval = [ref_val[0] / 3600.0, ref_val[1] / 3600.0]
                    w.wcs.cdelt = [increment[0] / 3600.0, increment[1] / 3600.0]
                else:
                    w.wcs.crval = [ref_val[0] * 180 / np.pi, ref_val[1] * 180 / np.pi]
                    w.wcs.cdelt = [
                        increment[0] * 180 / np.pi,
                        increment[1] * 180 / np.pi,
                    ]

                w1x, w1y = w.wcs_pix2world(xlow, ylow, 0)
                w2x, w2y = w.wcs_pix2world(xhigh, yhigh, 0)

                center_wx = (w1x + w2x) / 2
                center_wy = (w1y + w2y) / 2

                ctype1_roi = str(_hdr.get("CTYPE1", "")).upper()
                if "SOLAR" in ctype1_roi:
                    # SOLAR-X/Y: WCS values are in arcsec (crval was set directly)
                    sx = center_wx * 3600.0  # degrees -> arcsec
                    sy = center_wy * 3600.0
                    w_arcsec = abs(w2x - w1x) * 3600.0
                    h_arcsec = abs(w2y - w1y) * 3600.0
                    ra_dec_info = (
                        f'Center: SOLAR-X={sx:.1f}", SOLAR-Y={sy:.1f}"'
                        f'\nSize: {w_arcsec:.1f}" × {h_arcsec:.1f}"'
                    )
                elif "HPLN" in ctype1_roi:
                    # Unwrap helioprojective to [-180, 180]
                    center_wx = ((center_wx + 180.0) % 360.0) - 180.0
                    center_wy = ((center_wy + 180.0) % 360.0) - 180.0
                    hx = center_wx * 3600.0
                    hy = center_wy * 3600.0
                    w_arcsec = abs(w2x - w1x) * 3600.0
                    h_arcsec = abs(w2y - w1y) * 3600.0
                    ra_dec_info = (
                        f'Center: HPLN={hx:.1f}", HPLT={hy:.1f}"'
                        f'\nSize: {w_arcsec:.1f}" × {h_arcsec:.1f}"'
                    )
                else:
                    center_coord = SkyCoord(
                        ra=center_wx * u.degree, dec=center_wy * u.degree
                    )
                    width = abs(w2x - w1x) * u.degree
                    height = abs(w2y - w1y) * u.degree
                    ra_dec_info = (
                        f"Center: RA={center_coord.ra.to_string(unit=u.hour, sep=':', precision=1)}, "
                        f"Dec={center_coord.dec.to_string(sep=':', precision=1)}"
                        f"\nSize: {width.to(u.arcsec):.1f} × {height.to(u.arcsec):.1f}"
                    )
            except Exception as e:
                ra_dec_info = f"\nRA/Dec conversion error: {str(e)}"

        self.show_roi_stats(roi, ra_dec_info)

    def _masked_stats(self, shape, x1, y1, x2, y2, angle=0.0):
        """Show the statistics of the ellipse (or rectangle, ``shape``) in
        the box from (x1, y1) to (x2, y2), turned ``angle`` degrees
        anticlockwise about its centre: of the pixels whose middles are in it."""

        # Calculate ellipse parameters in display coordinates
        center_x = (x1 + x2) / 2
        center_y = (y1 + y2) / 2
        width = abs(x2 - x1)
        height = abs(y2 - y1)

        if width < 1 or height < 1:
            self.current_roi = None
            self._hide_roi_stats()
            return

        # The displayed image is transposed from current_image_data:
        # display x runs along the data's rows (axis 0), y along its columns
        # (axis 1). The region's half-axes, and how far it reaches along x
        # and y once turned
        t = np.radians(angle)
        cos_t, sin_t = np.cos(t), np.sin(t)
        half_w, half_h = width / 2, height / 2
        if shape == "ellipse":
            reach_x = np.hypot(half_w * cos_t, half_h * sin_t)
            reach_y = np.hypot(half_w * sin_t, half_h * cos_t)
        else:
            reach_x = half_w * abs(cos_t) + half_h * abs(sin_t)
            reach_y = half_w * abs(sin_t) + half_h * abs(cos_t)

        # The mask, over the box the region reaches (no pixel outside it is in)
        h, w = self.current_image_data.shape  # h = rows (axis 0), w = cols (axis 1)
        row_low = max(0, int(np.floor(center_x - reach_x)))
        row_high = min(h, int(np.ceil(center_x + reach_x)) + 1)
        col_low = max(0, int(np.floor(center_y - reach_y)))
        col_high = min(w, int(np.ceil(center_y + reach_y)) + 1)
        rows, cols = np.ogrid[row_low:row_high, col_low:col_high]
        dx, dy = rows - center_x, cols - center_y
        along = dx * cos_t + dy * sin_t  # along the region's first axis
        across = dy * cos_t - dx * sin_t
        if shape == "ellipse":
            mask = (along / half_w) ** 2 + (across / half_h) ** 2 <= 1
        else:
            mask = (np.abs(along) <= half_w) & (np.abs(across) <= half_h)

        # Get pixels within the region
        roi_data = self.current_image_data[row_low:row_high, col_low:col_high][mask]

        if roi_data.size == 0:
            self.current_roi = None
            self._hide_roi_stats()
            return

        # Store the ROI as its bounding box for compatibility
        # current_roi uses data coordinates: (row_low, row_high, col_low, col_high)
        self.current_roi = (
            max(0, int(center_x - reach_x)),
            min(h, int(center_x + reach_x)),
            max(0, int(center_y - reach_y)),
            min(w, int(center_y + reach_y)),
        )

        ra_dec_info = ""
        if self.current_wcs:
            try:
                import astropy.units as u
                from astropy.coordinates import SkyCoord
                from astropy.wcs import WCS

                ref_val = self.current_wcs.referencevalue()["numeric"][0:2]
                ref_pix = self.current_wcs.referencepixel()["numeric"][0:2]
                increment = self.current_wcs.increment()["numeric"][0:2]

                wcs = WCS(naxis=2)
                wcs.wcs.crpix = [
                    ref_pix[0] + 1,
                    ref_pix[1] + 1,
                ]  # CASA 0-indexed -> FITS 1-indexed
                _hdr = getattr(self, "_cached_fits_header", None) or {}
                _is_solar = "SOLAR-X" in str(_hdr.get("CTYPE1", "")).upper()
                if _is_solar:
                    wcs.wcs.crval = [ref_val[0] / 3600.0, ref_val[1] / 3600.0]
                    wcs.wcs.cdelt = [increment[0] / 3600.0, increment[1] / 3600.0]
                else:
                    wcs.wcs.crval = [ref_val[0] * 180 / np.pi, ref_val[1] * 180 / np.pi]
                    wcs.wcs.cdelt = [
                        increment[0] * 180 / np.pi,
                        increment[1] * 180 / np.pi,
                    ]

                # Convert display coordinates to world coordinates
                # center_x, center_y are in display coordinates (used for WCS)
                wx_c, wy_c = wcs.wcs_pix2world(center_x, center_y, 0)

                # Angular size of the region (along its own axes, as turned)
                per_x, per_y = abs(increment[0]), abs(increment[1])
                if not _is_solar:
                    per_x, per_y = (v * 180 / np.pi * 3600 for v in (per_x, per_y))  # arcsec
                angular_width = width * np.hypot(per_x * cos_t, per_y * sin_t)
                angular_height = height * np.hypot(per_x * sin_t, per_y * cos_t)
                # (turned half a turn, either shape is the same)
                turn = f", rotated {(angle + 90) % 180 - 90:.1f}°" if angle else ""

                ctype1_ell = str(_hdr.get("CTYPE1", "")).upper()
                if "SOLAR" in ctype1_ell:
                    sx = wx_c * 3600.0
                    sy = wy_c * 3600.0
                    ra_dec_info = (
                        f'Center: SOLAR-X={sx:.1f}", SOLAR-Y={sy:.1f}"'
                        f'\nSize: {angular_width:.1f}" × {angular_height:.1f}"{turn}'
                    )
                elif "HPLN" in ctype1_ell:
                    # Unwrap helioprojective to [-180, 180]
                    wx_c = ((wx_c + 180.0) % 360.0) - 180.0
                    wy_c = ((wy_c + 180.0) % 360.0) - 180.0
                    hx = wx_c * 3600.0
                    hy = wy_c * 3600.0
                    ra_dec_info = (
                        f'Center: HPLN={hx:.1f}", HPLT={hy:.1f}"'
                        f'\nSize: {angular_width:.1f}" × {angular_height:.1f}"{turn}'
                    )
                else:
                    center_coord = SkyCoord(ra=wx_c * u.degree, dec=wy_c * u.degree)
                    ra_dec_info = (
                        f"Center: RA={center_coord.ra.to_string(unit=u.hour, sep=':', precision=1)}, "
                        f"Dec={center_coord.dec.to_string(sep=':', precision=1)}"
                        f'\nSize: {angular_width:.1f}" × {angular_height:.1f}"{turn}'
                    )
            except Exception as e:
                ra_dec_info = f"\nRA/Dec conversion error: {str(e)}"

        self.show_roi_stats(roi_data, ra_dec_info)

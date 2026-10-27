"""
ROI statistics tool for SolarViewer.
"""

import itertools

import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt5.QtCore import QEvent, QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
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
_keys = itertools.count(1)


class RegionMode:
    RECTANGLE = 0
    ELLIPSE = 1
    PAN = 2


class Region:
    """A rectangle or ellipse on a pane: ``shape`` ("rect" or "ellipse") in
    the box with ``corners`` (x1, y1, x2, y2), in pixels of the image. Its
    copies in linked panes share its ``key``."""

    __slots__ = ("shape", "corners", "key")

    def __init__(self, shape, corners, key=None):
        x1, y1, x2, y2 = (float(v) for v in corners)
        self.shape = shape
        self.corners = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        self.key = key if key is not None else next(_keys)

    def outline(self):
        """The outline as points (pixels of the image), closed."""
        return region_outline(self.shape, self.corners)

    def contains(self, x, y):
        x1, y1, x2, y2 = self.corners
        if self.shape == "rect":
            return x1 <= x <= x2 and y1 <= y <= y2
        a, b = (x2 - x1) / 2, (y2 - y1) / 2
        return a > 0 and b > 0 and (
            ((x - (x1 + x2) / 2) / a) ** 2 + ((y - (y1 + y2) / 2) / b) ** 2 <= 1
        )


def region_outline(shape, corners):
    x1, y1, x2, y2 = corners
    if shape == "rect":
        return [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
    t = np.linspace(0, 2 * np.pi, 73)
    cx, cy, a, b = (x1 + x2) / 2, (y1 + y2) / 2, abs(x2 - x1) / 2, abs(y2 - y1) / 2
    return list(zip(cx + a * np.cos(t), cy + b * np.sin(t)))


class RegionLayer(QWidget):
    """A pane's regions, drawn in Qt over its canvas.

    Drawing, selecting or deleting a region, or moving the view, repaints
    this layer alone; the Matplotlib figure is never redrawn for it. The
    selected region is drawn solid and thicker, the others dashed (they
    differ in weight, not colour), over a halo that contrasts with the image.
    """

    def __init__(self, tab):
        super().__init__(tab.canvas)
        self._tab = tab
        self.preview = None  # (shape, corners) of a region being dragged out
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

    def paintEvent(self, event):
        tab = self._tab
        ax = tab._nav_axes()
        if ax is None or (not tab.regions and self.preview is None):
            return
        canvas = tab.canvas
        dpr = canvas.device_pixel_ratio
        fig_h = canvas.figure.bbox.height

        def to_widget(points):
            return [QPointF(px / dpr, (fig_h - py) / dpr) for px, py in ax.transData.transform(points)]

        x0, y0, w, h = ax.bbox.bounds
        clip = QRectF(QPointF(x0 / dpr, (fig_h - y0 - h) / dpr), QPointF((x0 + w) / dpr, (fig_h - y0) / dpr))
        line, halo = (QColor(c) for c in contrast_colors(tab))
        halo.setAlphaF(0.8)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setClipRect(clip)

        def draw(points, selected):
            path = QPainterPath()
            path.addPolygon(QPolygonF(to_widget(points)))
            width = 2.0 if selected else 1.3
            painter.strokePath(path, QPen(halo, width + 1.8, Qt.SolidLine, Qt.FlatCap, Qt.MiterJoin))
            pen = QPen(line, width, Qt.SolidLine if selected else Qt.CustomDashLine, Qt.FlatCap, Qt.MiterJoin)
            if not selected:
                pen.setDashPattern([4, 3])
            painter.strokePath(path, pen)

        for region in tab.regions:
            if region is not tab.selected_region:
                draw(region.outline(), False)
        if tab.selected_region is not None:
            draw(tab.selected_region.outline(), True)
        if self.preview is not None:
            draw(region_outline(*self.preview), True)
        painter.end()


class RoiStatsMixin:
    """ROI selection (rectangle/ellipse), ROI statistics and the ROI histogram.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``canvas``, ``current_image_data``, ``current_wcs``, ``info_label``,
    ``_in_measurement_mode``, ``_nav_axes``, ``parent``, ``region_mode``,
    ``_region_layer``, ``regions``, ``selected_region``,
    ``show_status_message``, ``stats_table``
    """

    def show_roi_stats(self, roi, ra_dec_info=""):
        if roi.size == 0:  # (a region off the image)
            self.current_roi = None
            self._hide_roi_stats()
            return

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
        self.delete_region_btn.show()
        self.clear_regions_btn.setVisible(len(self.regions) > 1)

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

        xlow, xhigh, ylow, yhigh = self.current_roi
        roi_data = self.current_image_data[xlow:xhigh, ylow:yhigh]
        roi_flat = roi_data.flatten()
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

    def add_region(self, shape, corners, key=None, follow=True):
        """Add a ``shape`` region in the box with ``corners`` and select it;
        with ``follow``, the panes following this one get a copy."""
        if self.current_image_data is None or None in corners:
            return None
        region = Region(shape, corners, key)
        self.regions.append(region)
        self.select_region(region, follow=False)
        page = getattr(self, "page", None)
        if follow and page is not None:
            page.sync.region_added(self, region)
        return region

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
            self._show_region_hint()
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

    def region_by_key(self, key):
        return next((region for region in self.regions if region.key == key), None)

    def _measure_selected(self):
        """The selected region's statistics, from the image as it is now."""
        self._measured_data = self.current_image_data
        region = self.selected_region
        if region is None or self.current_image_data is None:
            self.current_roi = None
            self._hide_roi_stats()
        elif region.shape == "rect":
            self._rect_stats(*region.corners)
        else:
            self._ellipse_stats(*region.corners)
        page = getattr(self, "page", None)
        if page is not None:
            page.regions_changed()

    def _hide_roi_stats(self):
        for widget in (self.stats_table, self.histogram_btn, self.fit_gaussian_btn,
                       self.export_region_btn, self.delete_region_btn):
            widget.hide()
        self._show_region_hint()

    def _show_region_hint(self):
        """Without a selected region, the Region block says what there is."""
        self.clear_regions_btn.setVisible(len(self.regions) > 1)
        if self.selected_region is not None:
            self.info_label.setText("The selected region is off the image.")
            return
        count = len(self.regions)
        self.info_label.setText(
            f"{count} region{'s' * (count != 1)}. Click one with the Rectangle or "
            "Ellipse tool to select it." if count else NO_REGION_HINT
        )

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
        self._region_layer.refresh()

    def _region_tool(self):
        """Whether the Rectangle or Ellipse tool is the one in use."""
        return (
            getattr(self, "region_mode", RegionMode.PAN) in (RegionMode.RECTANGLE, RegionMode.ELLIPSE)
            and not self._in_measurement_mode()
        )

    def _region_press(self, event):
        """A left press with the Rectangle or Ellipse tool: a drag draws a
        region, a click selects the region under it (or none)."""
        ax = self._nav_axes()
        if event.button != 1 or ax is None or event.inaxes is not ax or not self._region_tool():
            return
        self._region_press_at = (event.x, event.y, event.xdata, event.ydata)

    def _region_motion(self, event):
        press = self._region_press_at
        if press is None or event.x is None:
            return
        x0, y0, xdata0, ydata0 = press
        layer = self._region_layer
        if layer.preview is None and max(abs(event.x - x0), abs(event.y - y0)) <= CLICK_PX:
            return
        x, y = self._data_in_axes(event)
        if "shift" in (event.key or ""):  # a square or a circle
            side = max(abs(x - xdata0), abs(y - ydata0))
            x = xdata0 + np.copysign(side, x - xdata0)
            y = ydata0 + np.copysign(side, y - ydata0)
        shape = "rect" if self.region_mode == RegionMode.RECTANGLE else "ellipse"
        layer.preview = (shape, (xdata0, ydata0, x, y))
        layer.refresh()

    def _data_in_axes(self, event):
        """The mouse's position in pixels of the image, kept to the axes."""
        ax = self._nav_axes()
        box = ax.bbox
        px = min(max(event.x, box.x0), box.x1)
        py = min(max(event.y, box.y0), box.y1)
        return ax.transData.inverted().transform((px, py))

    def _region_release(self, event):
        press, self._region_press_at = self._region_press_at, None
        if press is None or event.button != 1:
            return
        layer = self._region_layer
        preview, layer.preview = layer.preview, None
        if preview is not None:
            ax = self._nav_axes()
            corners = preview[1]
            (px1, py1), (px2, py2) = ax.transData.transform([corners[:2], corners[2:]])
            if min(abs(px2 - px1), abs(py2 - py1)) > CLICK_PX:
                self.add_region(*preview)
                return
            layer.refresh()  # (too thin to be a region)
            return
        # A click: the most recent region under it, or none
        x, y = press[2], press[3]
        hits = [region for region in self.regions if region.contains(x, y)]
        self.select_region(hits[-1] if hits else None)
        if hits:
            self.show_status_message("Region selected: Delete removes it, Esc deselects")

    def _on_region_key(self, event):
        """Delete or Backspace deletes the selected region; Esc deselects it
        (or drops a region being drawn)."""
        if self._in_measurement_mode():
            return
        if event.key == "escape":
            if self._region_layer.preview is not None:
                self._region_press_at = self._region_layer.preview = None
                self._region_layer.refresh()
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

    def _ellipse_stats(self, x1, y1, x2, y2):
        """Show the statistics of the ellipse in the box from (x1, y1) to (x2, y2)."""

        # Calculate ellipse parameters in display coordinates
        center_x = (x1 + x2) / 2
        center_y = (y1 + y2) / 2
        width = abs(x2 - x1)
        height = abs(y2 - y1)

        if width < 1 or height < 1:
            self.current_roi = None
            self._hide_roi_stats()
            return

        # The displayed image is transposed from current_image_data.
        # Display: transposed_data[x_display, y_display] = current_image_data[y_display, x_display]
        # So matplotlib's xdata corresponds to axis 0 (rows) and ydata to axis 1 (cols) in current_image_data.
        # We need to swap coordinates for the mask.
        data_center_row = center_x  # display x -> data row (axis 0)
        data_center_col = center_y  # display y -> data col (axis 1)
        data_height = width  # display width -> data height (axis 0 extent)
        data_width = height  # display height -> data width (axis 1 extent)

        # Create ellipse mask on current_image_data
        h, w = self.current_image_data.shape  # h = rows (axis 0), w = cols (axis 1)
        row_coords, col_coords = np.ogrid[:h, :w]

        # Ellipse equation: ((col - center_col) / a)^2 + ((row - center_row) / b)^2 <= 1
        a = data_width / 2  # semi-axis along columns (axis 1)
        b = data_height / 2  # semi-axis along rows (axis 0)
        mask = ((col_coords - data_center_col) / a) ** 2 + (
            (row_coords - data_center_row) / b
        ) ** 2 <= 1

        # Get pixels within ellipse
        roi_data = self.current_image_data[mask]

        if roi_data.size == 0:
            self.current_roi = None
            self._hide_roi_stats()
            return

        # Store ellipse ROI info as bounding box for compatibility
        # current_roi uses data coordinates: (row_low, row_high, col_low, col_high)
        row_low, row_high = int(data_center_row - b), int(data_center_row + b)
        col_low, col_high = int(data_center_col - a), int(data_center_col + a)
        self.current_roi = (
            max(0, row_low),
            min(h, row_high),
            max(0, col_low),
            min(w, col_high),
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

                # Angular size of ellipse (display width/height)
                if _is_solar:
                    angular_width = abs(width * increment[0])  # already arcsec
                    angular_height = abs(height * increment[1])  # already arcsec
                else:
                    angular_width = (
                        abs(width * increment[0] * 180 / np.pi) * 3600
                    )  # arcsec
                    angular_height = (
                        abs(height * increment[1] * 180 / np.pi) * 3600
                    )  # arcsec

                ctype1_ell = str(_hdr.get("CTYPE1", "")).upper()
                if "SOLAR" in ctype1_ell:
                    sx = wx_c * 3600.0
                    sy = wy_c * 3600.0
                    ra_dec_info = (
                        f'Center: SOLAR-X={sx:.1f}", SOLAR-Y={sy:.1f}"'
                        f'\nSize: {angular_width:.1f}" × {angular_height:.1f}"'
                    )
                elif "HPLN" in ctype1_ell:
                    # Unwrap helioprojective to [-180, 180]
                    wx_c = ((wx_c + 180.0) % 360.0) - 180.0
                    wy_c = ((wy_c + 180.0) % 360.0) - 180.0
                    hx = wx_c * 3600.0
                    hy = wy_c * 3600.0
                    ra_dec_info = (
                        f'Center: HPLN={hx:.1f}", HPLT={hy:.1f}"'
                        f'\nSize: {angular_width:.1f}" × {angular_height:.1f}"'
                    )
                else:
                    center_coord = SkyCoord(ra=wx_c * u.degree, dec=wy_c * u.degree)
                    ra_dec_info = (
                        f"Center: RA={center_coord.ra.to_string(unit=u.hour, sep=':', precision=1)}, "
                        f"Dec={center_coord.dec.to_string(sep=':', precision=1)}"
                        f'\nSize: {angular_width:.1f}" × {angular_height:.1f}"'
                    )
            except Exception as e:
                ra_dec_info = f"\nRA/Dec conversion error: {str(e)}"

        self.show_roi_stats(roi_data, ra_dec_info)

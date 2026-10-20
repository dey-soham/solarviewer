"""
Matplotlib canvas setup plus zoom, pan and mouse interaction.
"""

import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

from ..tools import RegionMode


class CanvasMixin:
    """Canvas creation, zoom buttons, animated scroll-zoom/pan and mouse handlers.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``_cached_transposed``, ``coord_label``, ``image_plot``,
    ``init_region_editor``, ``_pan_timer``, ``_profile_mode``, ``ruler_action``,
    ``_ruler_mode``, ``show_beam_checkbox``, ``show_solar_disk_checkbox``,
    ``show_status_message``, ``_toggle_ruler_mode``, ``_update_beam_position``,
    ``_update_solar_disk_position``, ``window``, ``_zoom_lowres_transposed``,
    ``_zoom_timer``
    """

    def setup_canvas(self, parent_layout):
        self.figure = Figure(figsize=(5, 5), dpi=100)
        self.canvas = FigureCanvas(self.figure)

        # Ensure canvas background matches theme to avoid white gaps when scaling
        self.canvas.setStyleSheet("background-color: transparent;")

        self.nav_toolbar = NavigationToolbar(self.canvas, self)
        # Make the matplotlib toolbar more compact
        self.nav_toolbar.setFixedHeight(24)
        self.nav_toolbar.setStyleSheet("""
            NavigationToolbar2QT { padding: 0px; margin: 0px; spacing: 1px; background-color: transparent; }
            QToolButton { padding: 2px; margin: 0px; }
        """)
        parent_layout.addWidget(self.nav_toolbar)
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

        """
        # Performance Safety: Ensure high-res is restored when switching modes
        if hasattr(self, "image_plot") and self.image_plot:
            if hasattr(self, "_cached_transposed") and self._cached_transposed is not None:
                self.image_plot.set_data(self._cached_transposed)
                self.canvas.draw_idle()
        """

        # Re-init region editor if we have an axis
        if hasattr(self, "figure") and self.figure.axes:
            self.init_region_editor(self.figure.axes[0])

    def zoom_in(self):
        if self.current_image_data is None:
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        ax = self.figure.axes[0]
        xlim = ax.get_xlim()
        ylim = ax.get_ylim()

        xcenter = (xlim[0] + xlim[1]) / 2
        ycenter = (ylim[0] + ylim[1]) / 2

        width = (xlim[1] - xlim[0]) / 2
        height = (ylim[1] - ylim[0]) / 2

        ax.set_xlim(xcenter - width / 2, xcenter + width / 2)
        ax.set_ylim(ycenter - height / 2, ycenter + height / 2)

        self._update_beam_position(ax)
        # If solar disk checkbox is checked, draw the solar disk
        if self.show_solar_disk_checkbox.isChecked():
            self._update_solar_disk_position(ax)
        self.canvas.draw()
        QApplication.restoreOverrideCursor()
        self.show_status_message("Zoomed in")

    def zoom_out(self):
        if self.current_image_data is None:
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        ax = self.figure.axes[0]
        xlim = ax.get_xlim()
        ylim = ax.get_ylim()

        xcenter = (xlim[0] + xlim[1]) / 2
        ycenter = (ylim[0] + ylim[1]) / 2

        width = (xlim[1] - xlim[0]) * 2
        height = (ylim[1] - ylim[0]) * 2

        ax.set_xlim(xcenter - width / 2, xcenter + width / 2)
        ax.set_ylim(ycenter - height / 2, ycenter + height / 2)

        self._update_beam_position(ax)
        # If solar disk checkbox is checked, draw the solar disk
        if self.show_solar_disk_checkbox.isChecked():
            self._update_solar_disk_position(ax)
        self.canvas.draw()
        QApplication.restoreOverrideCursor()
        self.show_status_message("Zoomed out")

    def zoom_60arcmin(self):
        if self.current_image_data is None or self.current_wcs is None:
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            ax = self.figure.axes[0]
            xlim = ax.get_xlim()
            ylim = ax.get_ylim()
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

            ax.set_xlim(xcenter - pixels_x / 2, xcenter + pixels_x / 2)
            ax.set_ylim(ycenter - pixels_y / 2, ycenter + pixels_y / 2)

            self._update_beam_position(ax)
            # If solar disk checkbox is checked, draw the solar disk
            if self.show_solar_disk_checkbox.isChecked():
                self._update_solar_disk_position(ax)
            self.canvas.draw()
            self.show_status_message("Zoomed to 1°×1°")
        except Exception as e:
            print(f"[ERROR] Error in zoom_60arcmin: {e}")
            self.show_status_message(f"Error in zoom_60arcmin: {e}")
        finally:
            QApplication.restoreOverrideCursor()

    def reset_view(self, show_status_message=True):
        """Reset the view to show the full image with original limits"""
        if self.current_image_data is None:
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        ax = self.figure.axes[0]

        # Reset to show the full image
        ax.set_xlim(0, self.current_image_data.shape[0])
        ax.set_ylim(0, self.current_image_data.shape[1])

        self._update_beam_position(ax)
        # If solar disk checkbox is checked, draw the solar disk
        if self.show_solar_disk_checkbox.isChecked():
            self._update_solar_disk_position(ax)

        self.canvas.draw()
        QApplication.restoreOverrideCursor()
        if show_status_message:
            self.show_status_message("View reset")

    def on_mouse_move(self, event):
        if self.current_image_data is None:
            self.coord_label.setText("No image loaded")
            return

        if not event.inaxes:
            self.coord_label.setText("")
            self.canvas.setCursor(Qt.ArrowCursor)
            return

        # Priority to Matplotlib toolbar tools
        if hasattr(self, "nav_toolbar") and self.nav_toolbar.mode != "":
            return

        is_precision_mode = (hasattr(self, "_ruler_mode") and self._ruler_mode) or (
            hasattr(self, "_profile_mode") and self._profile_mode
        )

        # Update cursor for panning mode feedback
        if is_precision_mode:
            self.canvas.setCursor(Qt.CrossCursor)
        elif hasattr(self, "region_mode") and self.region_mode == RegionMode.PAN:
            if self._is_panning:
                self.canvas.setCursor(Qt.ClosedHandCursor)
            else:
                self.canvas.setCursor(Qt.OpenHandCursor)
        else:
            # Default cursor for other modes within axes
            self.canvas.setCursor(Qt.CrossCursor)

        # Suspend optimization during precision measurement tasks
        is_precision_mode = (hasattr(self, "_ruler_mode") and self._ruler_mode) or (
            hasattr(self, "_profile_mode") and self._profile_mode
        )

        x, y = round(event.xdata), round(event.ydata)

        # Panning Logic (Interactive Panning)
        if not is_precision_mode and self._is_panning and event.x is not None:
            ax = event.inaxes

            # Calculate delta in pixel coordinates
            dx_px = event.x - self._pan_start_x_px
            dy_px = event.y - self._pan_start_y_px

            # Convert pixel delta to data delta using initial scale
            width_px = ax.bbox.width
            height_px = ax.bbox.height
            width_data = self._pan_start_xlim[1] - self._pan_start_xlim[0]
            height_data = self._pan_start_ylim[1] - self._pan_start_ylim[0]

            dx_data = dx_px * (width_data / width_px)
            dy_data = dy_px * (height_data / height_px)

            # Update panning target
            self._pan_target_xlim = [
                self._pan_start_xlim[0] - dx_data,
                self._pan_start_xlim[1] - dx_data,
            ]
            self._pan_target_ylim = [
                self._pan_start_ylim[0] - dy_data,
                self._pan_start_ylim[1] - dy_data,
            ]

            if not self._pan_timer.isActive():
                self._pan_timer.start(16)

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

            # 2. Get exact Value as Matplotlib would show it
            cursor_data = im.get_cursor_data(event)
            if cursor_data is not None:
                # Use Matplotlib's own value formatter for perfect parity
                val_str = im.format_cursor_data(cursor_data).strip("[]").strip()

                pixel_info = f"<b>Pixel:</b> X={x}, Y={y}<br><b>Value:</b> {val_str}"
                world_info = f"<b>World:</b> {world_str}"
                coord_info = f"{pixel_info}<br>{world_info}"
            else:
                pixel_info = f"<b>Pixel:</b> X={x}, Y={y}"
                world_info = f"<b>World:</b> {world_str}"
                coord_info = f"{pixel_info}<br>{world_info}"

            #    coord_info = f"<b>Pixel:</b> Outside Image"

            self.coord_label.setText(coord_info)
            return

        except Exception as e:
            # Fallback to simple pixel info if Artist API fails
            print(f"[DEBUG] Coordinate Readout Fallback: {e}")
            self.coord_label.setText(f"<b>Pixel:</b> X={x}, Y={y}")

    def _on_mouse_press(self, event):
        """Handle mouse button press for panning."""
        # Priority to Matplotlib toolbar tools
        if hasattr(self, "nav_toolbar") and self.nav_toolbar.mode != "":
            return

        # Suspend custom panning logic during precision measurement tasks
        if (hasattr(self, "_ruler_mode") and self._ruler_mode) or (
            hasattr(self, "_profile_mode") and self._profile_mode
        ):
            return

        if (
            not hasattr(self, "region_mode")
            or self.region_mode != RegionMode.PAN
            or event.inaxes is None
            or event.button != 1
        ):
            return

        self._is_panning = True
        self.canvas.setCursor(Qt.ClosedHandCursor)
        self._pan_start_x_px = event.x
        self._pan_start_y_px = event.y

        ax = event.inaxes
        self._pan_start_xlim = ax.get_xlim()
        self._pan_start_ylim = ax.get_ylim()

        # Performance Optimization: Switch image to low-res decimation during the drag
        if self.image_plot is not None and self._zoom_lowres_transposed is not None:
            self.image_plot.set_data(self._zoom_lowres_transposed)
            self.canvas.draw_idle()

    def _on_mouse_release(self, event):
        """Handle mouse button release to finalize panning."""
        if not self._is_panning:
            return

        self._is_panning = False
        self._pan_timer.stop()

        # Restore cursor based on location
        if event.inaxes:
            self.canvas.setCursor(Qt.OpenHandCursor)
        else:
            self.canvas.setCursor(Qt.ArrowCursor)

        # Restore FULL resolution data whenever panning/clicking ends
        if (
            self.image_plot
            and hasattr(self, "_cached_transposed")
            and self._cached_transposed is not None
        ):
            self.image_plot.set_data(self._cached_transposed)
            self.canvas.draw_idle()

        # (axes is None if the figure was cleared while dragging)
        if self._pan_target_xlim and self.image_plot and self.image_plot.axes:
            ax = self.image_plot.axes
            ax.set_xlim(self._pan_target_xlim)
            ax.set_ylim(self._pan_target_ylim)

            # Update Overlays (Beam, Solar Disk) to reflect new viewport
            if (
                hasattr(self, "show_beam_checkbox")
                and self.show_beam_checkbox.isChecked()
            ):
                self._update_beam_position(ax)

            self.canvas.draw_idle()

        self._pan_target_xlim = None
        self._pan_target_ylim = None

    def _perform_pan_step(self):
        """Timer callback for smooth panning animation using easing."""
        if self.image_plot is None or self._pan_target_xlim is None:
            self._pan_timer.stop()
            return

        ax = self.image_plot.axes
        if ax is None:
            # The figure was cleared mid-animation (e.g. a new file is loading);
            # the image is no longer on an axes, so abandon the animation.
            self._pan_timer.stop()
            self._pan_target_xlim = None
            self._pan_target_ylim = None
            return
        curr_xlim = ax.get_xlim()
        curr_ylim = ax.get_ylim()

        # Snappy alpha (0.5 = 50% distance per frame) for responsive follow
        alpha = 0.5

        new_xlim = [
            curr_xlim[0] + (self._pan_target_xlim[0] - curr_xlim[0]) * alpha,
            curr_xlim[1] + (self._pan_target_xlim[1] - curr_xlim[1]) * alpha,
        ]
        new_ylim = [
            curr_ylim[0] + (self._pan_target_ylim[0] - curr_ylim[0]) * alpha,
            curr_ylim[1] + (self._pan_target_ylim[1] - curr_ylim[1]) * alpha,
        ]

        ax.set_xlim(new_xlim)
        ax.set_ylim(new_ylim)
        self.canvas.draw_idle()

        # Stop once we are close enough to target
        if abs(new_xlim[0] - self._pan_target_xlim[0]) < 0.001 * (
            new_xlim[1] - new_xlim[0]
        ):
            self._pan_timer.stop()

    def _on_mouse_scroll(self, event):
        """Handle mouse scroll events for zooming with smooth animation."""
        # Check if feature is enabled in View menu
        main_window = self.window()
        if hasattr(main_window, "scroll_zoom_action"):
            if not main_window.scroll_zoom_action.isChecked():
                return

        if event.inaxes is None or self.current_image_data is None:
            return

        # Disable scroll-to-zoom during precision measurement tasks (Ruler/Profile)
        if (hasattr(self, "_ruler_mode") and self._ruler_mode) or (
            hasattr(self, "_profile_mode") and self._profile_mode
        ):
            self.show_status_message(
                "Scroll to zoom suspended in measurement mode", timeout=2000
            )
            return

        ax = event.inaxes
        base_scale = 1.2

        if event.button == "up":
            scale_factor = 1 / base_scale
        elif event.button == "down":
            scale_factor = base_scale
        else:
            return

        # Use current target as base if already animating to prevent "jerking"
        if self._zoom_timer.isActive() and self._zoom_target_xlim:
            cur_xlim = self._zoom_target_xlim
            cur_ylim = self._zoom_target_ylim
        else:
            cur_xlim = ax.get_xlim()
            cur_ylim = ax.get_ylim()

        xdata = event.xdata
        ydata = event.ydata

        # Calculate target limits
        new_width = (cur_xlim[1] - cur_xlim[0]) * scale_factor
        new_height = (cur_ylim[1] - cur_ylim[0]) * scale_factor

        relx = (cur_xlim[1] - xdata) / (cur_xlim[1] - cur_xlim[0])
        rely = (cur_ylim[1] - ydata) / (cur_ylim[1] - cur_ylim[0])

        self._zoom_target_xlim = [
            xdata - new_width * (1 - relx),
            xdata + new_width * relx,
        ]
        self._zoom_target_ylim = [
            ydata - new_height * (1 - rely),
            ydata + new_height * rely,
        ]

        # Start animation timer
        if not self._zoom_timer.isActive():
            # Performance Optimization: Switch image to low-res decimation during the animation
            if self.image_plot is not None and self._zoom_lowres_transposed is not None:
                self.image_plot.set_data(self._zoom_lowres_transposed)

            # 16ms (60 FPS)
            self._zoom_timer.start(16)

    def _perform_zoom_step(self):
        """Timer callback for smooth zooming animation using easing."""
        if self.image_plot is None or not self._zoom_target_xlim:
            self._zoom_timer.stop()
            return

        ax = self.image_plot.axes
        if ax is None:
            # The figure was cleared mid-animation (e.g. a new file is loading);
            # the image is no longer on an axes, so abandon the animation.
            self._zoom_timer.stop()
            self._zoom_target_xlim = None
            self._zoom_target_ylim = None
            return
        curr_xlim = ax.get_xlim()
        curr_ylim = ax.get_ylim()

        # Easing factor: 0.5 (50% per frame)
        alpha = 0.5

        new_xlim = [
            curr_xlim[0] + (self._zoom_target_xlim[0] - curr_xlim[0]) * alpha,
            curr_xlim[1] + (self._zoom_target_xlim[1] - curr_xlim[1]) * alpha,
        ]
        new_ylim = [
            curr_ylim[0] + (self._zoom_target_ylim[0] - curr_ylim[0]) * alpha,
            curr_ylim[1] + (self._zoom_target_ylim[1] - curr_ylim[1]) * alpha,
        ]

        ax.set_xlim(new_xlim)
        ax.set_ylim(new_ylim)
        self.canvas.draw_idle()

        # Precision stopping
        # This prevents the "hanging" feeling at the end of the zoom.
        if abs(new_xlim[0] - self._zoom_target_xlim[0]) < 0.002 * (
            new_xlim[1] - new_xlim[0]
        ):
            ax.set_xlim(self._zoom_target_xlim)
            ax.set_ylim(self._zoom_target_ylim)

            # Restore FULL resolution data once animation completes
            if (
                hasattr(self, "_cached_transposed")
                and self._cached_transposed is not None
            ):
                self.image_plot.set_data(self._cached_transposed)

            # Update Overlays (Beam, Solar Disk) to reflect new viewport
            if (
                hasattr(self, "show_beam_checkbox")
                and self.show_beam_checkbox.isChecked()
            ):
                self._update_beam_position(ax)

            self.canvas.draw_idle()
            self._zoom_timer.stop()

"""
Ruler (distance measurement) tool for SolarViewer.
"""

import logging
import numpy as np
from PyQt5.QtCore import Qt

from .geometry import snap_to_axis

logger = logging.getLogger(__name__)


class RulerMixin:
    """Ruler / angular distance measurement tool.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``canvas``, ``current_wcs``, ``roi_selector``, ``show_status_message``
    """

    def _toggle_ruler_mode(self, checked):
        """Toggle ruler mode for distance measurement"""
        self._ruler_mode = checked
        if checked:
            self.show_status_message(
                "Ruler mode: Click first point, then click second point to measure (Shift for H/V snap)"
            )
            # Disconnect ROI selector temporarily
            if self.roi_selector:
                self.roi_selector.set_active(False)

            self.canvas.setCursor(Qt.CrossCursor)
            # Connect mouse events for ruler
            self._ruler_click_cid = self.canvas.mpl_connect(
                "button_press_event", self._ruler_on_click
            )
        else:
            self.show_status_message("Ruler mode off")
            # Reconnect ROI selector
            if self.roi_selector:
                self.roi_selector.set_active(True)
            # Disconnect ruler events
            if hasattr(self, "_ruler_click_cid"):
                self.canvas.mpl_disconnect(self._ruler_click_cid)
            if hasattr(self, "_ruler_motion_cid") and self._ruler_motion_cid:
                self.canvas.mpl_disconnect(self._ruler_motion_cid)
                self._ruler_motion_cid = None
            # Clear ruler graphics
            self._clear_ruler()

    def _clear_ruler(self):
        """Clear ruler line and text from plot"""
        if self._ruler_line is not None:
            try:
                self._ruler_line.remove()
            except Exception:
                pass
            self._ruler_line = None
        if self._ruler_text is not None:
            try:
                self._ruler_text.remove()
            except Exception:
                pass
            self._ruler_text = None
        if (
            hasattr(self, "_ruler_preview_line")
            and self._ruler_preview_line is not None
        ):
            try:
                self._ruler_preview_line.remove()
            except Exception:
                pass
            self._ruler_preview_line = None
        self._ruler_blit_bg = None
        self._ruler_start = None
        self.canvas.draw_idle()

    def _ruler_on_click(self, event):
        """Handle mouse click for ruler - two clicks to measure"""
        if event.inaxes is None or event.button != 1:
            return

        from PyQt5.QtWidgets import QApplication

        modifiers = QApplication.keyboardModifiers()
        shift_held = bool(modifiers & Qt.ShiftModifier)

        if self._ruler_start is None:
            # First click - clear previous measurement first, then set start point
            # Clear graphics but don't reset _ruler_start yet
            if self._ruler_line is not None:
                try:
                    self._ruler_line.remove()
                except Exception:
                    pass
                self._ruler_line = None
            if self._ruler_text is not None:
                try:
                    self._ruler_text.remove()
                except Exception:
                    pass
                self._ruler_text = None

            # Now set start point
            self._ruler_start = (event.xdata, event.ydata)

            # Draw start point marker
            ax = event.inaxes
            (self._ruler_line,) = ax.plot(
                [event.xdata], [event.ydata], "ro", markersize=8
            )
            self.canvas.draw_idle()

            # Connect motion for live preview
            self._ruler_preview_line = None
            self._ruler_blit_bg = None
            self._ruler_motion_cid = self.canvas.mpl_connect(
                "motion_notify_event", self._ruler_on_motion
            )

            self.show_status_message(
                f"Start point set at ({event.xdata:.1f}, {event.ydata:.1f}) - click second point (Shift for H/V snap)"
            )
        else:
            # Second click - set end point and calculate distance
            x1, y1 = self._ruler_start
            x2, y2 = event.xdata, event.ydata

            if shift_held:
                x2, y2 = snap_to_axis(x1, y1, x2, y2)

            # Disconnect motion handler
            if hasattr(self, "_ruler_motion_cid") and self._ruler_motion_cid:
                self.canvas.mpl_disconnect(self._ruler_motion_cid)
                self._ruler_motion_cid = None

            # Clean up preview line
            if (
                hasattr(self, "_ruler_preview_line")
                and self._ruler_preview_line is not None
            ):
                try:
                    self._ruler_preview_line.remove()
                except Exception:
                    pass
                self._ruler_preview_line = None
            self._ruler_blit_bg = None

            ax = event.inaxes

            # Draw line between points
            if self._ruler_line is not None:
                self._ruler_line.remove()
            (self._ruler_line,) = ax.plot(
                [x1, x2], [y1, y2], "r-", linewidth=2, marker="o", markersize=8
            )

            # Calculate distance
            distance_info = self._calculate_angular_distance(x1, y1, x2, y2)

            # Draw distance text at midpoint
            mid_x, mid_y = (x1 + x2) / 2, (y1 + y2) / 2
            self._ruler_text = ax.text(
                mid_x,
                mid_y,
                distance_info,
                fontsize=9,
                color="red",
                fontweight="bold",
                bbox=dict(boxstyle="round", facecolor="white", alpha=0.6),
            )

            self.canvas.draw_idle()

            # Show in status bar
            self.show_status_message(
                f"Distance: {distance_info} (end: {x2:.1f}, {y2:.1f})"
            )

            # Reset for next measurement
            self._ruler_start = None

    def _ruler_on_motion(self, event):
        """Handle mouse motion for ruler live preview line with blitting"""
        if self._ruler_start is None or event.inaxes is None:
            return

        from PyQt5.QtWidgets import QApplication

        modifiers = QApplication.keyboardModifiers()
        shift_held = bool(modifiers & Qt.ShiftModifier)

        x1, y1 = self._ruler_start
        x2, y2 = event.xdata, event.ydata

        if shift_held:
            x2, y2 = snap_to_axis(x1, y1, x2, y2)

        ax = event.inaxes

        # Update or create preview line
        if self._ruler_preview_line is None:
            (self._ruler_preview_line,) = ax.plot(
                [x1, x2], [y1, y2], "r--", linewidth=1.5, alpha=0.7
            )
            # Capture background for blitting
            self.canvas.draw()
            self._ruler_blit_bg = self.canvas.copy_from_bbox(ax.bbox)
        else:
            self._ruler_preview_line.set_data([x1, x2], [y1, y2])

        # Use blitting for fast updates
        if self._ruler_blit_bg is not None:
            self.canvas.restore_region(self._ruler_blit_bg)
            ax.draw_artist(self._ruler_preview_line)
            self.canvas.blit(ax.bbox)
        else:
            self.canvas.draw_idle()

        # Show distance in status bar
        pixel_dist = np.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        self.show_status_message(
            f"Distance: {pixel_dist:.1f} px | Shift for H/V snap | Click to confirm"
        )

    def _calculate_angular_distance(self, x1, y1, x2, y2):
        """Calculate angular distance between two pixel coordinates using WCS"""
        # Pixel distance
        pixel_dist = np.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        dx_px = abs(x2 - x1)
        dy_px = abs(y2 - y1)

        if self.current_wcs:
            try:
                # Get pixel scale from WCS
                increment = self.current_wcs.increment()["numeric"][0:2]
                # Check if image uses SOLAR-X/Y (increment is already in arcsec)
                _hdr = getattr(self, "_cached_fits_header", None) or {}
                _is_solar = "SOLAR-X" in str(_hdr.get("CTYPE1", "")).upper()
                if _is_solar:
                    scale_x = abs(increment[0])  # already arcsec/pixel
                    scale_y = abs(increment[1])
                else:
                    scale_x = abs(increment[0]) * 180 / np.pi * 3600  # arcsec/pixel
                    scale_y = abs(increment[1]) * 180 / np.pi * 3600  # arcsec/pixel

                # Calculate angular distance
                dx_arcsec = dx_px * scale_x
                dy_arcsec = dy_px * scale_y
                angular_dist = np.sqrt(dx_arcsec**2 + dy_arcsec**2)

                # Get world coordinates for both points
                world1 = self.current_wcs.toworld([x1, y1, 0, 0])["numeric"]
                world2 = self.current_wcs.toworld([x2, y2, 0, 0])["numeric"]
                # Check if SOLAR-X (toworld returns arcsec, not radians)
                _hdr = getattr(self, "_cached_fits_header", None) or {}
                _is_solar = "SOLAR-X" in str(_hdr.get("CTYPE1", "")).upper()
                if _is_solar:
                    ra1_deg = world1[0] / 3600.0 if world1[0] else None
                    dec1_deg = world1[1] / 3600.0 if world1[1] else None
                    ra2_deg = world2[0] / 3600.0 if world2[0] else None
                    dec2_deg = world2[1] / 3600.0 if world2[1] else None
                else:
                    ra1_deg = world1[0] * 180 / np.pi if world1[0] else None
                    dec1_deg = world1[1] * 180 / np.pi if world1[1] else None
                    ra2_deg = world2[0] * 180 / np.pi if world2[0] else None
                    dec2_deg = world2[1] * 180 / np.pi if world2[1] else None

                # Determine coordinate label names
                _hdr_lbl = getattr(self, "_cached_fits_header", None) or {}
                _ctype1_lbl = str(_hdr_lbl.get("CTYPE1", "")).upper()
                if "SOLAR" in _ctype1_lbl:
                    _coord1_name, _coord2_name = 'SOLAR-X (")', 'SOLAR-Y (")'
                    # Convert deg back to arcsec for display
                    if ra1_deg is not None:
                        ra1_deg = ra1_deg * 3600.0
                    if dec1_deg is not None:
                        dec1_deg = dec1_deg * 3600.0
                    if ra2_deg is not None:
                        ra2_deg = ra2_deg * 3600.0
                    if dec2_deg is not None:
                        dec2_deg = dec2_deg * 3600.0
                elif "HPLN" in _ctype1_lbl:
                    _coord1_name, _coord2_name = 'HPLN (")', 'HPLT (")'
                    # Unwrap helioprojective degrees to [-180, 180] then convert to arcsec
                    if ra1_deg is not None:
                        ra1_deg = ((ra1_deg + 180.0) % 360.0 - 180.0) * 3600.0
                    if dec1_deg is not None:
                        dec1_deg = ((dec1_deg + 180.0) % 360.0 - 180.0) * 3600.0
                    if ra2_deg is not None:
                        ra2_deg = ((ra2_deg + 180.0) % 360.0 - 180.0) * 3600.0
                    if dec2_deg is not None:
                        dec2_deg = ((dec2_deg + 180.0) % 360.0 - 180.0) * 3600.0
                else:
                    _coord1_name, _coord2_name = "RA (deg)", "Dec (deg)"

                # Professional terminal output
                print("\n" + "=" * 60)
                print("              LINE DISTANCE MEASUREMENT")
                print("=" * 60)
                print(f'  Coordinate System: WCS (scale: {scale_x:.4f}"/px)')
                print("-" * 60)
                print(
                    f"  {'Point':<12} {'X (px)':<12} {'Y (px)':<12} {_coord1_name:<15} {_coord2_name:<15}"
                )
                print("-" * 60)
                if ra1_deg is not None and dec1_deg is not None:
                    print(
                        f"  {'Start':<12} {x1:<12.2f} {y1:<12.2f} {ra1_deg:<15.4f} {dec1_deg:<15.4f}"
                    )
                else:
                    print(
                        f"  {'Start':<12} {x1:<12.2f} {y1:<12.2f} {'N/A':<15} {'N/A':<15}"
                    )
                if ra2_deg is not None and dec2_deg is not None:
                    print(
                        f"  {'End':<12} {x2:<12.2f} {y2:<12.2f} {ra2_deg:<15.4f} {dec2_deg:<15.4f}"
                    )
                else:
                    print(
                        f"  {'End':<12} {x2:<12.2f} {y2:<12.2f} {'N/A':<15} {'N/A':<15}"
                    )
                print("-" * 60)
                print(f"  {'ΔX':<20} {dx_px:>12.2f} px    {dx_arcsec:>12.2f} arcsec")
                print(f"  {'ΔY':<20} {dy_px:>12.2f} px    {dy_arcsec:>12.2f} arcsec")
                print("-" * 60)
                print(
                    f"  {'Total Distance':<20} {pixel_dist:>12.2f} px    {angular_dist:>12.2f} arcsec"
                )
                if angular_dist >= 60:
                    print(f"  {'':<20} {'':<12}       {angular_dist/60:>12.2f} arcmin")
                print("=" * 60 + "\n")

                # Format output for display
                if angular_dist >= 60:
                    return f"{angular_dist / 60:.2f}' ({pixel_dist:.1f} px)"
                else:
                    return f'{angular_dist:.2f}" ({pixel_dist:.1f} px)'
            except Exception as e:
                logger.error(f"WCS error: {e}")
                self.show_status_message(f"[Ruler] WCS error: {e}")
                return f"{pixel_dist:.1f} px (WCS error)"
        else:
            # Professional terminal output (pixel-only)
            print("\n" + "=" * 60)
            print("              LINE DISTANCE MEASUREMENT")
            print("=" * 60)
            print("  Coordinate System: Pixel")
            print("-" * 60)
            print(f"  {'Point':<12} {'X (px)':<15} {'Y (px)':<15}")
            print("-" * 60)
            print(f"  {'Start':<12} {x1:<15.2f} {y1:<15.2f}")
            print(f"  {'End':<12} {x2:<15.2f} {y2:<15.2f}")
            print("-" * 60)
            print(f"  {'ΔX':<20} {dx_px:>15.2f} px")
            print(f"  {'ΔY':<20} {dy_px:>15.2f} px")
            print("-" * 60)
            print(f"  {'Total Distance':<20} {pixel_dist:>15.2f} px")
            print("=" * 60 + "\n")

            return f"{pixel_dist:.1f} px"

"""
Ruler (distance measurement) tool for SolarViewer.
"""

import logging
import numpy as np
from PyQt5.QtCore import Qt

from .geometry import snap_to_axis
from .preview import contrast_colors, dashed_pen, halo, halo_pen, measure_preview

logger = logging.getLogger(__name__)


class RulerMixin:
    """Ruler / angular distance measurement tool.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``canvas``, ``current_wcs``, ``roi_selector``, ``show_status_message``

    A measurement is drawn as a right-angled triangle: the measured line is
    the hypotenuse, and the legs show its horizontal and vertical parts, each
    labelled with its length. It is drawn in the colours that contrast with
    the colormap (see :func:`contrast_colors`).
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
        """Clear ruler triangle and labels from plot"""
        self._remove_ruler_artists()
        measure_preview(self).end()
        self._ruler_start = None
        self.canvas.draw_idle()

    def _remove_ruler_artists(self):
        """Take the triangle, its labels and the start marker off the axes."""
        artists = [self._ruler_line, self._ruler_text]
        artists += getattr(self, "_ruler_legs", None) or []
        for artist in artists:
            if artist is not None:
                try:
                    artist.remove()
                except Exception:
                    pass
        self._ruler_line = None
        self._ruler_text = None
        self._ruler_legs = []

    def _ruler_on_click(self, event):
        """Handle mouse click for ruler - two clicks to measure"""
        if event.inaxes is None or event.button != 1:
            return

        from PyQt5.QtWidgets import QApplication

        modifiers = QApplication.keyboardModifiers()
        shift_held = bool(modifiers & Qt.ShiftModifier)

        if self._ruler_start is None:
            # First click - clear previous measurement first, then set start point
            self._remove_ruler_artists()
            self._ruler_start = (event.xdata, event.ydata)

            # Draw start point marker
            ax = event.inaxes
            line, shadow = contrast_colors(self)
            (self._ruler_line,) = ax.plot(
                [event.xdata],
                [event.ydata],
                "o",
                color=line,
                markersize=8,
                path_effects=halo(shadow, 0),
            )
            self.canvas.draw_idle()

            # Connect motion for live preview
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

            measure_preview(self).end()

            ax = event.inaxes

            # The measured line, as the hypotenuse of the triangle
            self._remove_ruler_artists()
            line, shadow = contrast_colors(self)
            (self._ruler_line,) = ax.plot(
                [x1, x2],
                [y1, y2],
                "-",
                color=line,
                linewidth=2,
                marker="o",
                markersize=8,
                path_effects=halo(shadow, 2),
            )

            # Calculate distance
            distance_info = self._calculate_angular_distance(x1, y1, x2, y2)

            # Distance label by the midpoint, on the side away from the legs
            offset, ha, va = _distance_label_placement(x1, y1, x2, y2)
            self._ruler_text = ax.annotate(
                distance_info,
                ((x1 + x2) / 2, (y1 + y2) / 2),
                xytext=offset,
                textcoords="offset points",
                ha=ha,
                va=va,
                fontsize=9,
                color=line,
                fontweight="bold",
                bbox=dict(boxstyle="round", facecolor=shadow, alpha=0.7),
                annotation_clip=True,
            )
            self._ruler_legs = self._draw_ruler_legs(ax, x1, y1, x2, y2, line, shadow)

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

        line, shadow = contrast_colors(self)
        measure_preview(self).show_lines(
            event.inaxes, _triangle_lines(x1, y1, x2, y2, line), halo_pen(shadow, 1.5)
        )

        # Show distance in status bar
        pixel_dist = np.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        self.show_status_message(
            f"Distance: {pixel_dist:.1f} px (\u0394x {abs(x2 - x1):.1f}, "
            f"\u0394y {abs(y2 - y1):.1f}) | Shift for H/V snap | Click to confirm",
            flush=False,
        )

    def _draw_ruler_legs(self, ax, x1, y1, x2, y2, line, shadow):
        """Draw the triangle's legs, labelled with their lengths; the artists.

        The right angle is at the end point's x and the start point's y. The
        labels sit on the outer sides of the legs, away from the hypotenuse.
        No legs are drawn for a horizontal or vertical line.
        """
        if x1 == x2 or y1 == y2:
            return []
        try:
            scale = self._ruler_arcsec_per_pixel()
        except Exception as e:
            logger.error(f"WCS error: {e}")
            scale = None
        if scale is None:
            dx_label, dy_label = f"{abs(x2 - x1):.1f} px", f"{abs(y2 - y1):.1f} px"
        else:
            dx_label = _format_arcsec(abs(x2 - x1) * scale[0])
            dy_label = _format_arcsec(abs(y2 - y1) * scale[1])
        (legs,) = ax.plot(
            [x1, x2, x2],
            [y1, y1, y2],
            "--",
            color=line,
            linewidth=1,
            path_effects=halo(shadow, 1),
        )
        label_style = dict(
            textcoords="offset points",
            fontsize=8,
            color=line,
            bbox=dict(boxstyle="round", facecolor=shadow, alpha=0.7),
            annotation_clip=True,
        )
        below = y2 > y1  # the hypotenuse is above the horizontal leg
        dx_text = ax.annotate(
            dx_label,
            ((x1 + x2) / 2, y1),
            xytext=(0, -6 if below else 6),
            ha="center",
            va="top" if below else "bottom",
            **label_style,
        )
        right = x2 > x1  # the hypotenuse is left of the vertical leg
        dy_text = ax.annotate(
            dy_label,
            (x2, (y1 + y2) / 2),
            xytext=(6 if right else -6, 0),
            ha="left" if right else "right",
            va="center",
            **label_style,
        )
        return [legs, dx_text, dy_text]

    def _ruler_arcsec_per_pixel(self):
        """(x, y) pixel scales in arcsec from the WCS; None without a WCS."""
        if not self.current_wcs:
            return None
        increment = self.current_wcs.increment()["numeric"][0:2]
        # Check if image uses SOLAR-X/Y (increment is already in arcsec)
        _hdr = getattr(self, "_cached_fits_header", None) or {}
        if "SOLAR-X" in str(_hdr.get("CTYPE1", "")).upper():
            return abs(increment[0]), abs(increment[1])
        to_arcsec = 180 / np.pi * 3600
        return abs(increment[0]) * to_arcsec, abs(increment[1]) * to_arcsec

    def _calculate_angular_distance(self, x1, y1, x2, y2):
        """Calculate angular distance between two pixel coordinates using WCS"""
        # Pixel distance
        pixel_dist = np.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        dx_px = abs(x2 - x1)
        dy_px = abs(y2 - y1)

        if self.current_wcs:
            try:
                # Pixel scale from the WCS, in arcsec/pixel
                scale_x, scale_y = self._ruler_arcsec_per_pixel()

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


def _triangle_lines(x1, y1, x2, y2, color):
    """The preview's lines: the measured line and the triangle's legs."""
    lines = [((x1, y1), (x2, y2), dashed_pen(color, 1.5, 1.0))]
    if x1 != x2 and y1 != y2:
        legs = dashed_pen(color, 1.0, 0.8)
        lines += [((x1, y1), (x2, y1), legs), ((x2, y1), (x2, y2), legs)]
    return lines


def _format_arcsec(arcsec):
    return f"{arcsec / 60:.2f}'" if arcsec >= 60 else f'{arcsec:.2f}"'


def _distance_label_placement(x1, y1, x2, y2):
    """Offset (points) and alignment putting the distance label beside the
    midpoint on the far side from the right-angle corner, so it keeps clear
    of the legs and their labels; above a horizontal line, right of a vertical one."""
    away_x = (x1 + x2) / 2 - x2  # from the corner (x2, y1) to the midpoint
    away_y = (y1 + y2) / 2 - y1
    if away_x == 0 and away_y == 0:
        return (6, 0), "left", "center"  # vertical line
    if away_x == 0:
        return (6, 0), "left", "center"  # vertical line
    if away_y == 0:
        return (0, 6), "center", "bottom"  # horizontal line
    dx, dy = (6 if away_x > 0 else -6), (6 if away_y > 0 else -6)
    return (dx, dy), ("left" if dx > 0 else "right"), ("bottom" if dy > 0 else "top")

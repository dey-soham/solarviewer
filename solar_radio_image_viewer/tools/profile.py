"""
Profile cut tool for SolarViewer.
"""

import logging

import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from .geometry import snap_to_axis
from .preview import contrast_colors, dashed_pen, halo, halo_pen, measure_preview

logger = logging.getLogger(__name__)

from ..ui.geometry import fit_to_screen


class ProfileMixin:
    """Flux profile cuts (line and radial) and the profile plot dialog.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``canvas``, ``current_image_data``, ``current_wcs``, ``figure``,
    ``profile_action``, ``show_status_message``
    """

    def _toggle_profile_mode(self, checked):
        """Toggle profile mode for flux profile cut"""
        self._profile_mode = checked
        if checked:
            self.canvas.setCursor(Qt.CrossCursor)
            self._show_profile_mode_dialog()
        else:
            self.show_status_message("Profile mode off")
            # Disconnect all profile events
            for attr in ["_profile_click_cid", "_profile_motion_cid"]:
                if hasattr(self, attr):
                    try:
                        self.canvas.mpl_disconnect(getattr(self, attr))
                    except Exception:
                        pass
            # Clear profile graphics including center marker
            self._clear_profile()
            self._clear_crosshairs()
            measure_preview(self).end()
            # Also clear center marker
            if (
                hasattr(self, "_profile_center_marker")
                and self._profile_center_marker is not None
            ):
                try:
                    self._profile_center_marker.remove()
                except Exception:
                    pass
                self._profile_center_marker = None
            # Clear preview line if any
            if (
                hasattr(self, "_profile_preview_line")
                and self._profile_preview_line is not None
            ):
                try:
                    self._profile_preview_line.remove()
                except Exception:
                    pass
                self._profile_preview_line = None
            self.canvas.draw_idle()

    def _show_profile_mode_dialog(self):
        """Show dialog to select profile method"""
        from PyQt5.QtWidgets import (
            QButtonGroup,
            QDialog,
            QHBoxLayout,
            QLabel,
            QPushButton,
            QRadioButton,
            QVBoxLayout,
        )

        dialog = QDialog(self)
        dialog.setWindowTitle("Profile Cut Method")
        dialog.resize(300, 150)
        layout = QVBoxLayout(dialog)

        layout.addWidget(QLabel("Select profile cut method:"))

        # Radio buttons for method selection
        btn_group = QButtonGroup(dialog)

        line_radio = QRadioButton(
            "Line Mode - Click two points (hold Shift to snap H/V)"
        )
        line_radio.setChecked(self._profile_method == "line")
        btn_group.addButton(line_radio)
        layout.addWidget(line_radio)

        radial_radio = QRadioButton(
            "Radial Mode - Click center, set angle (plots both directions)"
        )
        radial_radio.setChecked(self._profile_method == "radial")
        btn_group.addButton(radial_radio)
        layout.addWidget(radial_radio)

        # Buttons
        btn_layout = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

        def on_ok():
            self._profile_method = "line" if line_radio.isChecked() else "radial"
            dialog.accept()
            self._activate_profile_mode()

        def on_cancel():
            self.profile_action.setChecked(False)
            self._profile_mode = False

        ok_btn.clicked.connect(on_ok)
        cancel_btn.clicked.connect(dialog.reject)
        dialog.rejected.connect(on_cancel)

        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

    def _activate_profile_mode(self):
        """Activate profile mode with selected method"""

        if self._profile_method == "line":
            self.show_status_message(
                "Line Profile: Click first point (Shift to snap H/V)"
            )
            # Connect click and motion events for line mode
            self._profile_click_cid = self.canvas.mpl_connect(
                "button_press_event", self._profile_line_on_click
            )
            self._profile_motion_cid = self.canvas.mpl_connect(
                "motion_notify_event", self._profile_line_on_motion
            )
        else:  # radial
            self.show_status_message("Radial Profile: Click center point")
            self._profile_click_cid = self.canvas.mpl_connect(
                "button_press_event", self._profile_radial_on_click
            )

    def _clear_profile(self):
        """Clear profile line from plot"""
        if self._profile_line is not None:
            try:
                self._profile_line.remove()
            except Exception:
                pass
            self._profile_line = None
        self._profile_start = None
        self.canvas.draw_idle()

    def _clear_crosshairs(self):
        """Clear crosshair overlays"""
        for line in self._profile_crosshairs:
            try:
                line.remove()
            except Exception:
                pass
        self._profile_crosshairs = []
        if self._profile_preview_line is not None:
            try:
                self._profile_preview_line.remove()
            except Exception:
                pass
            self._profile_preview_line = None
        measure_preview(self).end()

    def _profile_line_on_click(self, event):
        """Handle click for line profile mode"""
        if event.inaxes is None or event.button != 1:
            return

        from PyQt5.QtWidgets import QApplication

        modifiers = QApplication.keyboardModifiers()
        shift_held = bool(modifiers & Qt.ShiftModifier)

        if self._profile_start is None:
            # First click - set start point
            self._clear_profile()
            self._profile_start = (event.xdata, event.ydata)

            ax = event.inaxes
            line, shadow = contrast_colors(self)
            (self._profile_line,) = ax.plot(
                [event.xdata],
                [event.ydata],
                "o",
                color=line,
                markersize=6,
                path_effects=halo(shadow, 0),
            )
            self.canvas.draw_idle()

            self.show_status_message(
                f"Start: ({event.xdata:.1f}, {event.ydata:.1f}) - move mouse, click to set end (Shift for snap)"
            )
        else:
            # Second click - calculate end point (with shift snap if held)
            x1, y1 = self._profile_start
            x2, y2 = event.xdata, event.ydata

            if shift_held:
                x2, y2 = snap_to_axis(x1, y1, x2, y2)

            measure_preview(self).end()

            # Show coordinate editor dialog
            self._show_profile_coordinate_dialog(x1, y1, x2, y2)

    def _profile_line_on_motion(self, event):
        """Handle mouse motion for live preview line"""
        if self._profile_start is None or event.inaxes is None:
            return

        from PyQt5.QtWidgets import QApplication

        modifiers = QApplication.keyboardModifiers()
        shift_held = bool(modifiers & Qt.ShiftModifier)

        x1, y1 = self._profile_start
        x2, y2 = event.xdata, event.ydata

        # Apply shift snap if held
        if shift_held:
            x2, y2 = snap_to_axis(x1, y1, x2, y2)

        line, shadow = contrast_colors(self)
        measure_preview(self).show_lines(
            event.inaxes,
            [((x1, y1), (x2, y2), dashed_pen(line, 1.2, 1.0))],
            halo_pen(shadow, 1.2),
        )

        # Calculate and show distance
        pixel_dist = np.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        self.show_status_message(
            f"Distance: {pixel_dist:.1f} px | Shift for H/V snap | Click to confirm",
            flush=False,
        )

    def _profile_radial_on_click(self, event):
        """Handle click for radial profile mode - click center, then show dialog"""
        if event.inaxes is None or event.button != 1:
            return

        cx, cy = event.xdata, event.ydata

        # Draw center marker
        self._clear_profile()
        ax = event.inaxes
        line, shadow = contrast_colors(self)
        (self._profile_line,) = ax.plot(
            [cx], [cy], "*", color=line, markersize=12, path_effects=halo(shadow, 0)
        )
        self.canvas.draw_idle()

        # Show radial input dialog
        self._show_radial_profile_dialog(cx, cy)

    def _show_profile_coordinate_dialog(self, x1, y1, x2, y2):
        """Show dialog to edit/confirm profile cut coordinates"""
        dialog = QDialog(self)
        dialog.setWindowTitle("Profile Cut Coordinates")
        dialog.resize(400, 200)
        layout = QVBoxLayout(dialog)

        # Instructions
        layout.addWidget(
            QLabel("Edit coordinates if needed, then click 'Extract Profile':")
        )

        # Coordinate inputs
        coord_layout = QGridLayout()

        coord_layout.addWidget(QLabel("Start X:"), 0, 0)
        x1_spin = QDoubleSpinBox()
        x1_spin.setRange(0, 10000)
        x1_spin.setDecimals(1)
        x1_spin.setValue(x1)
        coord_layout.addWidget(x1_spin, 0, 1)

        coord_layout.addWidget(QLabel("Start Y:"), 0, 2)
        y1_spin = QDoubleSpinBox()
        y1_spin.setRange(0, 10000)
        y1_spin.setDecimals(1)
        y1_spin.setValue(y1)
        coord_layout.addWidget(y1_spin, 0, 3)

        coord_layout.addWidget(QLabel("End X:"), 1, 0)
        x2_spin = QDoubleSpinBox()
        x2_spin.setRange(0, 10000)
        x2_spin.setDecimals(1)
        x2_spin.setValue(x2)
        coord_layout.addWidget(x2_spin, 1, 1)

        coord_layout.addWidget(QLabel("End Y:"), 1, 2)
        y2_spin = QDoubleSpinBox()
        y2_spin.setRange(0, 10000)
        y2_spin.setDecimals(1)
        y2_spin.setValue(y2)
        coord_layout.addWidget(y2_spin, 1, 3)

        layout.addLayout(coord_layout)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)

        extract_btn = QPushButton("Extract Profile")
        extract_btn.setDefault(True)
        btn_layout.addWidget(extract_btn)

        layout.addLayout(btn_layout)

        def do_extract():
            dialog.accept()
            # Get final coordinates
            fx1, fy1 = x1_spin.value(), y1_spin.value()
            fx2, fy2 = x2_spin.value(), y2_spin.value()

            # Draw final line on plot
            ax = self.figure.axes[0] if self.figure.axes else None
            if ax:
                if self._profile_line is not None:
                    self._profile_line.remove()
                line, shadow = contrast_colors(self)
                (self._profile_line,) = ax.plot(
                    [fx1, fx2],
                    [fy1, fy2],
                    "-",
                    color=line,
                    linewidth=1.5,
                    marker="o",
                    markersize=6,
                    path_effects=halo(shadow, 1.5),
                )
                self.canvas.draw_idle()

            # Extract and show profile
            self._extract_and_show_profile(fx1, fy1, fx2, fy2)

        extract_btn.clicked.connect(do_extract)

        # Reset for next measurement
        self._profile_start = None

        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

    def _show_radial_profile_dialog(self, cx, cy):
        """Show dialog for radial profile - set angle and length from center"""
        dialog = QDialog(self)
        dialog.setWindowTitle("Radial Profile Settings")
        dialog.resize(450, 300)
        layout = QVBoxLayout(dialog)

        # Center coordinates (editable)
        center_layout = QHBoxLayout()
        center_layout.addWidget(QLabel("Center:"))
        cx_spin = QDoubleSpinBox()
        cx_spin.setRange(0, 10000)
        cx_spin.setDecimals(1)
        cx_spin.setValue(cx)
        cx_spin.setPrefix("X: ")
        center_layout.addWidget(cx_spin)
        cy_spin = QDoubleSpinBox()
        cy_spin.setRange(0, 10000)
        cy_spin.setDecimals(1)
        cy_spin.setValue(cy)
        cy_spin.setPrefix("Y: ")
        center_layout.addWidget(cy_spin)
        layout.addLayout(center_layout)

        # Angle input
        angle_layout = QHBoxLayout()
        angle_layout.addWidget(QLabel("Angle (deg):"))
        angle_spin = QDoubleSpinBox()
        angle_spin.setRange(0, 180)
        angle_spin.setDecimals(1)
        angle_spin.setValue(0)
        angle_spin.setToolTip(
            "0° = East-West, 90° = North-South (profile extends both ways from center)"
        )
        angle_layout.addWidget(angle_spin)
        layout.addLayout(angle_layout)

        # Quick angle buttons
        quick_layout = QHBoxLayout()
        quick_layout.addWidget(QLabel("Quick:"))
        for angle in [0, 45, 90, 135, 180]:
            btn = QPushButton(f"{angle}°")
            btn.setFixedWidth(45)
            btn.clicked.connect(lambda checked, a=angle: angle_spin.setValue(a))
            quick_layout.addWidget(btn)
        quick_layout.addStretch()
        layout.addLayout(quick_layout)

        # Length input
        length_layout = QHBoxLayout()
        length_layout.addWidget(QLabel("Length (pixels):"))
        length_spin = QDoubleSpinBox()
        length_spin.setRange(1, 5000)
        length_spin.setDecimals(1)
        length_spin.setValue(100)
        length_layout.addWidget(length_spin)
        layout.addLayout(length_layout)

        # Explanatory note
        note_label = QLabel(
            "ℹ️ Length extends ±{} pixels from center (total {} pixels)"
        )
        note_label.setObjectName("Hint")
        layout.addWidget(note_label)

        def update_note():
            L = length_spin.value()
            note_label.setText(
                f"ℹ️ Length extends ±{L:.0f} pixels from center (total {2 * L:.0f} pixels)"
            )

        length_spin.valueChanged.connect(update_note)
        update_note()

        # Preview update function (always shows preview)
        _updating_preview = [False]  # Use list to allow modification in nested function

        def update_preview():
            if _updating_preview[0]:
                return  # Prevent recursion
            _updating_preview[0] = True
            try:
                ax = self.figure.axes[0] if self.figure.axes else None
                if not ax:
                    return

                c_x, c_y = cx_spin.value(), cy_spin.value()
                angle = np.radians(angle_spin.value())
                length = length_spin.value()

                # Calculate BOTH endpoints (bidirectional from center)
                x1 = c_x - length * np.cos(angle)
                y1 = c_y - length * np.sin(angle)
                x2 = c_x + length * np.cos(angle)
                y2 = c_y + length * np.sin(angle)

                # Update line on plot
                if self._profile_preview_line is not None:
                    try:
                        self._profile_preview_line.remove()
                    except Exception:
                        pass
                line, shadow = contrast_colors(self)
                (self._profile_preview_line,) = ax.plot(
                    [x1, x2],
                    [y1, y2],
                    "--",
                    color=line,
                    linewidth=1.5,
                    path_effects=halo(shadow, 1.5),
                )
                # Mark center
                if (
                    hasattr(self, "_profile_center_marker")
                    and self._profile_center_marker is not None
                ):
                    try:
                        self._profile_center_marker.remove()
                    except Exception:
                        pass
                (self._profile_center_marker,) = ax.plot(
                    [c_x],
                    [c_y],
                    "*",
                    color=line,
                    markersize=12,
                    path_effects=halo(shadow, 0),
                )
                self.canvas.draw_idle()
            finally:
                _updating_preview[0] = False

        # Connect spinboxes to update preview
        angle_spin.valueChanged.connect(update_preview)
        length_spin.valueChanged.connect(update_preview)
        cx_spin.valueChanged.connect(update_preview)
        cy_spin.valueChanged.connect(update_preview)

        # Initial preview
        update_preview()

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)

        extract_btn = QPushButton("Extract Profile")
        extract_btn.setDefault(True)
        btn_layout.addWidget(extract_btn)
        layout.addLayout(btn_layout)

        def do_extract():
            dialog.accept()
            c_x, c_y = cx_spin.value(), cy_spin.value()
            angle = np.radians(angle_spin.value())
            length = length_spin.value()

            # Calculate BOTH endpoints (bidirectional from center)
            x1 = c_x - length * np.cos(angle)
            y1 = c_y - length * np.sin(angle)
            x2 = c_x + length * np.cos(angle)
            y2 = c_y + length * np.sin(angle)

            # Clear preview line only (keep center marker)
            if self._profile_preview_line is not None:
                try:
                    self._profile_preview_line.remove()
                except Exception:
                    pass
                self._profile_preview_line = None

            ax = self.figure.axes[0] if self.figure.axes else None
            if ax:
                if self._profile_line is not None:
                    try:
                        self._profile_line.remove()
                    except Exception:
                        pass
                # Draw final line AND redraw center marker
                line, shadow = contrast_colors(self)
                (self._profile_line,) = ax.plot(
                    [x1, x2],
                    [y1, y2],
                    "-",
                    color=line,
                    linewidth=1.5,
                    marker="o",
                    markersize=6,
                    path_effects=halo(shadow, 1.5),
                )
                # Redraw center marker on top
                if (
                    hasattr(self, "_profile_center_marker")
                    and self._profile_center_marker is not None
                ):
                    try:
                        self._profile_center_marker.remove()
                    except Exception:
                        pass
                (self._profile_center_marker,) = ax.plot(
                    [c_x],
                    [c_y],
                    "*",
                    color=line,
                    markersize=14,
                    zorder=10,
                    path_effects=halo(shadow, 0),
                )
                self.canvas.draw_idle()

            # Extract profile (radial mode - center at 0)
            self._extract_and_show_profile(x1, y1, x2, y2, is_radial=True)

        extract_btn.clicked.connect(do_extract)

        def on_reject():
            # Clear preview line on cancel
            if self._profile_preview_line is not None:
                try:
                    self._profile_preview_line.remove()
                except Exception:
                    pass
                self._profile_preview_line = None
            if (
                hasattr(self, "_profile_center_marker")
                and self._profile_center_marker is not None
            ):
                try:
                    self._profile_center_marker.remove()
                except Exception:
                    pass
                self._profile_center_marker = None
            self._clear_profile()
            self.canvas.draw_idle()

        dialog.rejected.connect(on_reject)

        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

    def _extract_and_show_profile(self, x1, y1, x2, y2, is_radial=False):
        """Extract flux profile along line and show dialog

        Args:
            is_radial: If True, distances are centered at 0 (from -half to +half)
        """
        from scipy.ndimage import map_coordinates

        if self.current_image_data is None:
            QMessageBox.warning(self, "No Data", "No image data loaded")
            return

        # Calculate number of points along the line
        pixel_dist = np.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        num_points = max(int(np.ceil(pixel_dist)), 2)

        # Create coordinates along the line
        x_coords = np.linspace(x1, x2, num_points)
        y_coords = np.linspace(y1, y2, num_points)

        # Extract profile using bilinear interpolation
        # Note: map_coordinates expects (row, col) = (y, x) for image data
        profile = map_coordinates(
            self.current_image_data, [x_coords, y_coords], order=1
        )

        # Calculate distance array
        if self.current_wcs:
            try:
                increment = self.current_wcs.increment()["numeric"][0:2]
                # Check if image uses SOLAR-X/Y (increment is already in arcsec)
                _hdr = getattr(self, "_cached_fits_header", None) or {}
                _is_solar = "SOLAR-X" in str(_hdr.get("CTYPE1", "")).upper()
                if _is_solar:
                    scale = abs(increment[0])  # already arcsec/pixel
                else:
                    scale = abs(increment[0]) * 180 / np.pi * 3600  # arcsec/pixel

                if is_radial:
                    # Radial mode: center at 0, from -half_dist to +half_dist
                    half_dist = (pixel_dist * scale) / 2
                    distances = np.linspace(-half_dist, half_dist, num_points)
                else:
                    # Line mode: from 0 to end
                    distances = np.linspace(0, pixel_dist * scale, num_points)
                dist_unit = "arcsec"

                # Get WCS world coordinates for secondary axis
                # Convert pixel coords to world coords
                world_coords = []
                for px, py in zip(x_coords, y_coords):
                    world = self.current_wcs.toworld([px, py, 0, 0])["numeric"]
                    # world[0] and world[1] are RA, Dec in radians (or arcsec for SOLAR-X)
                    _hdr_chk = getattr(self, "_cached_fits_header", None) or {}
                    _is_solar_chk = "SOLAR-X" in str(_hdr_chk.get("CTYPE1", "")).upper()
                    if _is_solar_chk:
                        ra_deg = world[0] / 3600.0
                        dec_deg = world[1] / 3600.0
                    else:
                        ra_deg = world[0] * 180 / np.pi
                        dec_deg = world[1] * 180 / np.pi
                    world_coords.append((ra_deg, dec_deg))
                world_coords = np.array(world_coords)
                wcs_available = True
            except Exception as e:
                self.show_status_message(f"WCS conversion error: {e}")
                if is_radial:
                    half_dist = pixel_dist / 2
                    distances = np.linspace(-half_dist, half_dist, num_points)
                else:
                    distances = np.linspace(0, pixel_dist, num_points)
                dist_unit = "pixels"
                world_coords = None
                wcs_available = False
        else:
            if is_radial:
                half_dist = pixel_dist / 2
                distances = np.linspace(-half_dist, half_dist, num_points)
            else:
                distances = np.linspace(0, pixel_dist, num_points)
            dist_unit = "pixels"
            world_coords = None
            wcs_available = False

        self.show_status_message(
            f"Extracted {num_points} points from ({x1:.1f}, {y1:.1f}) to ({x2:.1f}, {y2:.1f})"
        )
        self.show_status_message(
            f"Flux range: {np.nanmin(profile):.4g} to {np.nanmax(profile):.4g}"
        )

        # Show profile dialog with dual x-axis (distance + WCS)
        self._show_profile_plot_dialog(
            profile, distances, dist_unit, world_coords, wcs_available, x1, y1, x2, y2
        )

    def _show_profile_plot_dialog(
        self, profile, distances, dist_unit, world_coords, wcs_available, x1, y1, x2, y2
    ):
        """Show dialog with profile plot - single plot with dual x-axes (distance + WCS)"""
        dialog = QDialog(self)
        dialog.setWindowTitle("Flux Profile")
        dialog.resize(800, 550)
        dialog.setMinimumSize(600, 400)
        fit_to_screen(dialog, 600, 400)
        layout = QVBoxLayout(dialog)

        # Create figure with single plot
        fig = Figure(figsize=(8, 5))
        canvas = FigureCanvas(fig)
        layout.addWidget(canvas, stretch=1)

        # Main plot with distance on bottom x-axis
        ax = fig.add_subplot(111)
        ax.plot(distances, profile, "b-", linewidth=1.5)
        ax.fill_between(distances, profile, alpha=0.3)
        ax.set_xlabel(f"Distance from start ({dist_unit})")
        ax.set_ylabel("Flux")
        ax.set_title(f"Profile from ({x1:.1f}, {y1:.1f}) to ({x2:.1f}, {y2:.1f})")
        ax.grid(True, alpha=0.3)

        # Calculate FWHM (only for suitable single-peak profiles)
        fwhm_value = None
        fwhm_left = None
        fwhm_right = None

        try:
            # Find max and baseline
            max_val = np.nanmax(profile)
            min_val = np.nanmin(profile)
            half_max = (max_val + min_val) / 2
            max_idx = np.nanargmax(profile)

            # Validation: Check if profile is suitable for FWHM
            # 1. Peak must be prominent (at least 20% above min)
            prominence = (max_val - min_val) / (abs(min_val) + 1e-10)
            if prominence < 0.2:
                raise ValueError("Peak not prominent enough")

            # 2. Peak should not be at the edges
            if max_idx < len(profile) * 0.05 or max_idx > len(profile) * 0.95:
                raise ValueError("Peak too close to edge")

            # 3. Count number of crossings through half-max (should be exactly 2 for FWHM)
            above_half = profile > half_max
            crossings = np.sum(np.diff(above_half.astype(int)) != 0)
            if crossings != 2:
                raise ValueError(f"Multiple peaks detected ({crossings} crossings)")

            # 4. Check monotonic decrease from peak to half-max on both sides
            # Left side: profile should generally decrease from max to left crossing
            left_half = profile[:max_idx]
            if len(left_half) > 3:
                # Check if left side is mostly increasing toward peak
                left_diffs = np.diff(left_half)
                if np.sum(left_diffs < 0) > len(left_diffs) * 0.5:
                    raise ValueError("Non-monotonic left side")

            # Right side: profile should generally decrease from max to right crossing
            right_half = profile[max_idx:]
            if len(right_half) > 3:
                # Check if right side is mostly decreasing from peak
                right_diffs = np.diff(right_half)
                if np.sum(right_diffs > 0) > len(right_diffs) * 0.5:
                    raise ValueError("Non-monotonic right side")

            # Find left crossing point (from max going left)
            left_indices = np.where(profile[:max_idx] <= half_max)[0]
            if len(left_indices) > 0:
                left_idx = left_indices[-1]
                # Linear interpolation for better accuracy
                if left_idx + 1 < len(profile):
                    denom = profile[left_idx + 1] - profile[left_idx]
                    if abs(denom) > 1e-10:
                        frac = (half_max - profile[left_idx]) / denom
                        fwhm_left = distances[left_idx] + frac * (
                            distances[left_idx + 1] - distances[left_idx]
                        )
                    else:
                        fwhm_left = distances[left_idx]
                else:
                    fwhm_left = distances[left_idx]

            # Find right crossing point (from max going right)
            right_indices = np.where(profile[max_idx:] <= half_max)[0]
            if len(right_indices) > 0:
                right_idx = max_idx + right_indices[0]
                # Linear interpolation for better accuracy
                if right_idx > 0:
                    denom = profile[right_idx] - profile[right_idx - 1]
                    if abs(denom) > 1e-10:
                        frac = (half_max - profile[right_idx - 1]) / denom
                        fwhm_right = distances[right_idx - 1] + frac * (
                            distances[right_idx] - distances[right_idx - 1]
                        )
                    else:
                        fwhm_right = distances[right_idx]
                else:
                    fwhm_right = distances[right_idx]

            # Calculate FWHM if both crossing points found
            if fwhm_left is not None and fwhm_right is not None:
                fwhm_value = abs(fwhm_right - fwhm_left)

                # Draw FWHM visualization
                ax.axhline(
                    y=half_max,
                    color="red",
                    linestyle="--",
                    alpha=0.7,
                    label=f"Half Max = {half_max:.4g}",
                )
                ax.plot(
                    [fwhm_left, fwhm_right],
                    [half_max, half_max],
                    "r-",
                    linewidth=2.5,
                    label=f"FWHM = {fwhm_value:.3f} {dist_unit}",
                )
                ax.plot(
                    [fwhm_left, fwhm_right], [half_max, half_max], "ro", markersize=8
                )

                # Add vertical lines at FWHM boundaries
                ax.axvline(x=fwhm_left, color="red", linestyle=":", alpha=0.5)
                ax.axvline(x=fwhm_right, color="red", linestyle=":", alpha=0.5)

                ax.legend(loc="best", fontsize=9)

                # Print to terminal professionally
                print("\n" + "=" * 50)
                print("         FLUX PROFILE FWHM MEASUREMENT")
                print("=" * 50)
                print(f"  {'Max Value':<20} {max_val:>15.6g}")
                print(f"  {'Min Value':<20} {min_val:>15.6g}")
                print(f"  {'Half Maximum':<20} {half_max:>15.6g}")
                print(f"  {'Left Position':<20} {fwhm_left:>15.3f} {dist_unit}")
                print(f"  {'Right Position':<20} {fwhm_right:>15.3f} {dist_unit}")
                print(f"  {'FWHM':<20} {fwhm_value:>15.3f} {dist_unit}")
                print("=" * 50 + "\n")
        except ValueError as e:
            # Not a single clear peak: say why there is no FWHM
            ax.text(
                0.99,
                0.98,
                f"No FWHM: {str(e)[0].lower()}{str(e)[1:]}",
                transform=ax.transAxes,
                ha="right",
                va="top",
                fontsize=8,
                alpha=0.7,
            )
        except Exception:
            logger.exception("FWHM measurement failed")

        # Add secondary x-axis at top with WCS coordinates
        if wcs_available and world_coords is not None:
            ax2 = ax.twiny()  # Create twin axis sharing y-axis

            # Determine axis labels based on coordinate type
            _hdr_ax = getattr(self, "_cached_fits_header", None) or {}
            _ctype1_ax = str(_hdr_ax.get("CTYPE1", "")).upper()

            # Use coord1 or coord2 based on which varies more
            ra_range = abs(world_coords[-1, 0] - world_coords[0, 0])
            dec_range = abs(world_coords[-1, 1] - world_coords[0, 1])

            if ra_range >= dec_range:
                wcs_vals = world_coords[:, 0]
                if "SOLAR" in _ctype1_ax:
                    # Convert deg to arcsec for SOLAR-X
                    wcs_vals = wcs_vals * 3600.0
                    ax2.set_xlabel("SOLAR-X (arcsec)")
                elif "HPLN" in _ctype1_ax:
                    # Unwrap helioprojective to [-180, 180] then to arcsec
                    wcs_vals = ((wcs_vals + 180.0) % 360.0 - 180.0) * 3600.0
                    ax2.set_xlabel("HPLN (arcsec)")
                else:
                    ax2.set_xlabel("RA (deg)")
            else:
                wcs_vals = world_coords[:, 1]
                if "SOLAR" in _ctype1_ax:
                    wcs_vals = wcs_vals * 3600.0
                    ax2.set_xlabel("SOLAR-Y (arcsec)")
                elif "HPLN" in _ctype1_ax:
                    wcs_vals = ((wcs_vals + 180.0) % 360.0 - 180.0) * 3600.0
                    ax2.set_xlabel("HPLT (arcsec)")
                else:
                    ax2.set_xlabel("Dec (deg)")

            # Set the same data limits as main axis but mapped to WCS values
            ax2.set_xlim(wcs_vals[0], wcs_vals[-1])

        fig.tight_layout()

        # Statistics with FWHM
        fwhm_str = f"FWHM: {fwhm_value:.3f} {dist_unit}" if fwhm_value else "FWHM: N/A"
        stats_text = (
            f"Min: {np.nanmin(profile):.6g}  |  Max: {np.nanmax(profile):.6g}  |  "
            f"Mean: {np.nanmean(profile):.6g}  |  Std: {np.nanstd(profile):.6g}  |  "
            f"{fwhm_str}  |  Points: {len(profile)}"
        )
        stats_label = QLabel(stats_text)
        stats_label.setObjectName("Mono")
        stats_label.setMargin(5)
        stats_label.setWordWrap(True)
        layout.addWidget(stats_label)

        # Buttons
        btn_layout = QHBoxLayout()

        # Save CSV button
        save_btn = QPushButton("Save CSV")

        def save_csv():
            from PyQt5.QtWidgets import QFileDialog

            filepath, _ = QFileDialog.getSaveFileName(
                dialog, "Save Profile", "", "CSV Files (*.csv)"
            )
            if filepath:
                with open(filepath, "w") as f:
                    f.write(
                        f"# Profile from ({x1:.1f}, {y1:.1f}) to ({x2:.1f}, {y2:.1f})\n"
                    )
                    f.write(f"Distance_{dist_unit},Flux\n")
                    for d, p in zip(distances, profile):
                        f.write(f"{d},{p}\n")
                self.show_status_message(f"Profile saved to {filepath}")

        save_btn.clicked.connect(save_csv)
        btn_layout.addWidget(save_btn)

        btn_layout.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

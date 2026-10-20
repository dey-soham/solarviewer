"""
Solar disk overlay: auto-detection, manual settings and drawing.
"""

import matplotlib.pyplot as plt
import numpy as np
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..ui.styles import set_hand_cursor


class SolarDiskMixin:
    """Solar disk overlay: centre/diameter detection, settings dialog and drawing.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``current_image_data``, ``current_wcs``, ``_is_already_hpc``,
    ``_open_dialogs``, ``show_status_message``, ``solar_disk_style``
    """

    def _compute_solar_disk_center(self, shape):
        """Auto-compute solar disk center and diameter from WCS/FITS header."""
        height, width = shape
        _is_hpc = self._is_already_hpc()
        if _is_hpc and self.current_wcs:
            try:
                ref_world = list(self.current_wcs.referencevalue()["numeric"])
                ref_world[0] = 0.0
                ref_world[1] = 0.0
                pix_origin = self.current_wcs.topixel(ref_world)["numeric"]
                cx, cy = float(pix_origin[0]), float(pix_origin[1])
                self.solar_disk_center = (cx, cy)
                self._auto_set_solar_diameter()
            except Exception:
                self.solar_disk_center = (width // 2, height // 2)
        elif (
            _is_hpc
            and hasattr(self, "_cached_wcs_obj")
            and self._cached_wcs_obj is not None
        ):
            try:
                px, py = self._cached_wcs_obj.wcs_world2pix(0.0, 0.0, 0)
                cx, cy = float(px), float(py)
                self.solar_disk_center = (cx, cy)
                self._auto_set_solar_diameter()
            except Exception:
                self.solar_disk_center = (width // 2, height // 2)
        else:
            _auto_computed = False
            _hdr = getattr(self, "_cached_fits_header", None) or {}
            if (
                (_hdr or self.current_wcs)
                and hasattr(self, "_cached_wcs_obj")
                and self._cached_wcs_obj is not None
            ):
                try:
                    from astropy.coordinates import get_sun
                    from astropy.time import Time
                    from sunpy.coordinates.sun import angular_radius

                    date_str = None
                    for key in ["DATE-OBS", "DATE_OBS", "DATE", "STARTOBS", "T_OBS"]:
                        date_str = _hdr.get(key)
                        if date_str:
                            break

                    if not date_str and self.current_wcs:
                        csys_record = {}
                        if hasattr(self.current_wcs, "torecord"):
                            csys_record = self.current_wcs.torecord()
                        elif isinstance(self.current_wcs, dict):
                            csys_record = self.current_wcs

                        if "obsdate" in csys_record:
                            obsdate = csys_record["obsdate"]
                            if "m0" in obsdate:
                                t = Time(obsdate["m0"]["value"], format="mjd")
                                date_str = t.iso

                    if date_str:
                        obs_time = Time(str(date_str))
                        sun_coord = get_sun(obs_time)
                        ra_deg = sun_coord.ra.deg
                        dec_deg = sun_coord.dec.deg
                        px, py = self._cached_wcs_obj.wcs_world2pix(ra_deg, dec_deg, 0)
                        cx, cy = float(px), float(py)
                        self.solar_disk_center = (cx, cy)
                        ang_radius = angular_radius(obs_time)
                        self.solar_disk_diameter_arcmin = (
                            float(ang_radius.to("arcmin").value) * 2.0
                        )
                        _auto_computed = True
                except Exception as e:
                    print(f"[WARN] Auto-compute solar position failed: {e}")
            if not _auto_computed:
                self.solar_disk_center = (width // 2, height // 2)

    def _auto_set_solar_diameter(self):
        """Auto-set solar_disk_diameter_arcmin from DATE-OBS if available."""
        try:
            _hdr = getattr(self, "_cached_fits_header", None) or {}
            date_str = None
            for key in ["DATE-OBS", "DATE_OBS", "DATE", "STARTOBS", "T_OBS"]:
                date_str = _hdr.get(key)
                if date_str:
                    break

            if not date_str and self.current_wcs:
                csys_record = {}
                if hasattr(self.current_wcs, "torecord"):
                    csys_record = self.current_wcs.torecord()
                elif isinstance(self.current_wcs, dict):
                    csys_record = self.current_wcs

                if "obsdate" in csys_record:
                    obsdate = csys_record["obsdate"]
                    if "m0" in obsdate:
                        from astropy.time import Time

                        t = Time(obsdate["m0"]["value"], format="mjd")
                        date_str = t.iso

            if date_str:
                from astropy.time import Time
                from sunpy.coordinates.sun import angular_radius

                obs_time = Time(str(date_str))
                ang_radius = angular_radius(obs_time)
                self.solar_disk_diameter_arcmin = (
                    float(ang_radius.to("arcmin").value) * 2.0
                )
        except Exception:
            pass  # keep default 32 arcmin

    def set_solar_disk_center(self):
        """Show non-modal solar disk settings dialog."""
        if self.current_image_data is None:
            QMessageBox.warning(self, "No Image", "Please load an image first.")
            return

        # If dialog already exists and is visible, just raise it
        if hasattr(self, "_solar_disk_dialog") and self._solar_disk_dialog is not None:
            try:
                if self._solar_disk_dialog.isVisible():
                    self._solar_disk_dialog.raise_()
                    self._solar_disk_dialog.activateWindow()
                    return
            except RuntimeError:
                self._solar_disk_dialog = None

        height, width = self.current_image_data.shape

        dialog = QDialog(self)
        dialog.setWindowTitle("Solar Disk Settings")
        set_hand_cursor(dialog)
        dialog.setMinimumWidth(400)
        layout = QVBoxLayout(dialog)

        # Store reference to prevent garbage collection
        self._solar_disk_dialog = dialog

        # Create tab widget for organizing settings
        tab_widget = QTabWidget()

        # Style tab (formerly Appearance tab)
        style_tab = QWidget()
        style_layout = QVBoxLayout(style_tab)

        # Color selection
        color_group = QGroupBox("Color")
        color_layout = QHBoxLayout(color_group)

        color_label = QLabel("Disk Color:")
        color_combo = QComboBox()
        colors = ["yellow", "white", "red", "green", "blue", "cyan", "magenta", "black"]
        for color in colors:
            color_combo.addItem(color)
        color_combo.setCurrentText(self.solar_disk_style["color"])
        color_layout.addWidget(color_label)
        color_layout.addWidget(color_combo)
        style_layout.addWidget(color_group)

        # Line style
        line_group = QGroupBox("Line Style")
        line_layout = QGridLayout(line_group)

        # Line style
        linestyle_label = QLabel("Line Style:")
        linestyle_combo = QComboBox()
        linestyles = [
            ("-", "Solid"),
            ("--", "Dashed"),
            (":", "Dotted"),
            ("-.", "Dash-dot"),
        ]
        for style_code, style_name in linestyles:
            linestyle_combo.addItem(style_name, style_code)

        # Set current line style
        current_style = self.solar_disk_style["linestyle"]
        for i in range(linestyle_combo.count()):
            if linestyle_combo.itemData(i) == current_style:
                linestyle_combo.setCurrentIndex(i)
                break

        line_layout.addWidget(linestyle_label, 0, 0)
        line_layout.addWidget(linestyle_combo, 0, 1)

        # Line width
        linewidth_label = QLabel("Line Width:")
        linewidth_spinbox = QDoubleSpinBox()
        linewidth_spinbox.setRange(0.5, 5.0)
        linewidth_spinbox.setSingleStep(0.5)
        linewidth_spinbox.setValue(self.solar_disk_style["linewidth"])
        line_layout.addWidget(linewidth_label, 1, 0)
        line_layout.addWidget(linewidth_spinbox, 1, 1)

        # Alpha/transparency
        alpha_label = QLabel("Opacity:")
        alpha_spinbox = QDoubleSpinBox()
        alpha_spinbox.setRange(0.1, 1.0)
        alpha_spinbox.setSingleStep(0.1)
        alpha_spinbox.setValue(self.solar_disk_style["alpha"])
        line_layout.addWidget(alpha_label, 2, 0)
        line_layout.addWidget(alpha_spinbox, 2, 1)

        style_layout.addWidget(line_group)

        # Center marker toggle
        center_marker_group = QGroupBox("Center Marker")
        center_marker_layout = QVBoxLayout(center_marker_group)

        # Add a checkbox to toggle the center marker
        show_center_checkbox = QCheckBox("Show center marker (+)")
        # Initialize checkbox state - if not in the dictionary, default to True
        if "show_center" not in self.solar_disk_style:
            self.solar_disk_style["show_center"] = True
        show_center_checkbox.setChecked(self.solar_disk_style["show_center"])
        center_marker_layout.addWidget(show_center_checkbox)

        style_layout.addWidget(center_marker_group)
        style_layout.addStretch()

        # Position tab
        position_tab = QWidget()
        position_layout = QVBoxLayout(position_tab)

        # Auto-compute toggle
        auto_group = QGroupBox("Auto-Compute")
        auto_layout = QVBoxLayout(auto_group)
        auto_checkbox = QCheckBox("Auto-compute from FITS header")
        auto_checkbox.setToolTip(
            "Automatically compute the Sun's position and apparent\n"
            "diameter from the observation date in the FITS header."
        )
        auto_checkbox.setChecked(self.solar_disk_auto_compute)
        auto_layout.addWidget(auto_checkbox)
        auto_info = QLabel("")
        auto_info.setWordWrap(True)
        auto_layout.addWidget(auto_info)
        position_layout.addWidget(auto_group)

        viewer_ref = self

        def run_auto_compute():
            """Run auto-compute and populate spinboxes + info label."""
            _hdr = getattr(viewer_ref, "_cached_fits_header", None) or {}
            wcs_obj = getattr(viewer_ref, "_cached_wcs_obj", None)
            if not _hdr and not viewer_ref.current_wcs:
                auto_info.setText("No metadata available.")
                return False
            try:
                from astropy.time import Time
                from sunpy.coordinates.sun import angular_radius

                date_str = None
                for key in ["DATE-OBS", "DATE_OBS", "DATE", "STARTOBS", "T_OBS"]:
                    date_str = _hdr.get(key)
                    if date_str:
                        break

                if not date_str and viewer_ref.current_wcs:
                    csys_record = {}
                    if hasattr(viewer_ref.current_wcs, "torecord"):
                        csys_record = viewer_ref.current_wcs.torecord()
                    elif isinstance(viewer_ref.current_wcs, dict):
                        csys_record = viewer_ref.current_wcs

                    if "obsdate" in csys_record:
                        obsdate = csys_record["obsdate"]
                        if "m0" in obsdate:
                            t = Time(obsdate["m0"]["value"], format="mjd")
                            date_str = t.iso

                if not date_str:
                    auto_info.setText("No observation date found.")
                    return False
                obs_time = Time(str(date_str))
                _is_hpc = viewer_ref._is_already_hpc()

                ang_radius = angular_radius(obs_time)
                diam = float(ang_radius.to("arcmin").value) * 2.0
                diameter_spinbox.setValue(diam)

                if _is_hpc:
                    cx, cy = None, None
                    if viewer_ref.current_wcs:
                        try:
                            ref_world = list(
                                viewer_ref.current_wcs.referencevalue()["numeric"]
                            )
                            ref_world[0] = 0.0
                            ref_world[1] = 0.0
                            pix_origin = viewer_ref.current_wcs.topixel(ref_world)[
                                "numeric"
                            ]
                            cx, cy = float(pix_origin[0]), float(pix_origin[1])
                        except Exception:
                            pass
                    if cx is None and wcs_obj is not None:
                        try:
                            px, py = wcs_obj.wcs_world2pix(0.0, 0.0, 0)
                            cx, cy = float(px), float(py)
                        except Exception:
                            pass
                    if cx is not None:
                        x_spinbox.setValue(int(round(cx)))
                        y_spinbox.setValue(int(round(cy)))
                    auto_info.setText(
                        f"HPC image: center set to (0,0)\n"
                        f"Apparent ⌀ = {diam:.2f}′  ({obs_time.iso[:19]})"
                    )
                else:
                    if wcs_obj is None:
                        auto_info.setText("No WCS available for RA/DEC conversion.")
                        return False
                    from astropy.coordinates import get_sun

                    sun_coord = get_sun(obs_time)
                    ra_deg = sun_coord.ra.deg
                    dec_deg = sun_coord.dec.deg
                    px, py = wcs_obj.wcs_world2pix(ra_deg, dec_deg, 0)
                    cx, cy = float(px), float(py)
                    x_spinbox.setValue(int(round(cx)))
                    y_spinbox.setValue(int(round(cy)))
                    auto_info.setText(
                        f"Sun RA={ra_deg:.4f}° DEC={dec_deg:.4f}°\n"
                        f"Pixel ({cx:.1f}, {cy:.1f})\n"
                        f"Apparent ⌀ = {diam:.2f}′  ({obs_time.iso[:19]})"
                    )
                return True
            except Exception as e:
                auto_info.setText(f"Auto-compute failed: {e}")
                return False

        def on_auto_toggle(checked):
            """Toggle auto-compute on/off and enable/disable manual controls."""
            x_spinbox.setEnabled(not checked)
            y_spinbox.setEnabled(not checked)
            diameter_spinbox.setEnabled(not checked)
            if checked:
                run_auto_compute()
            else:
                auto_info.setText("")

        auto_checkbox.toggled.connect(on_auto_toggle)

        # Center coordinates
        center_group = QGroupBox("Disk Center")
        center_layout = QHBoxLayout(center_group)

        x_label = QLabel("X coordinate:")
        x_spinbox = QSpinBox()
        x_spinbox.setRange(-width * 5, width * 5)
        if self.solar_disk_center is not None:
            x_spinbox.setValue(int(round(self.solar_disk_center[0])))
        else:
            x_spinbox.setValue(width // 2)
        center_layout.addWidget(x_label)
        center_layout.addWidget(x_spinbox)

        y_label = QLabel("Y coordinate:")
        y_spinbox = QSpinBox()
        y_spinbox.setRange(-height * 5, height * 5)
        if self.solar_disk_center is not None:
            y_spinbox.setValue(int(round(self.solar_disk_center[1])))
        else:
            y_spinbox.setValue(height // 2)
        center_layout.addWidget(y_label)
        center_layout.addWidget(y_spinbox)

        position_layout.addWidget(center_group)

        # Diameter
        size_group = QGroupBox("Disk Size")
        size_layout = QHBoxLayout(size_group)
        diameter_label = QLabel("Diameter (arcmin):")
        diameter_spinbox = QDoubleSpinBox()
        diameter_spinbox.setRange(1.0, 100.0)
        diameter_spinbox.setDecimals(2)
        diameter_spinbox.setSingleStep(0.1)
        diameter_spinbox.setValue(self.solar_disk_diameter_arcmin)
        size_layout.addWidget(diameter_label)
        size_layout.addWidget(diameter_spinbox)
        position_layout.addWidget(size_group)

        position_layout.addStretch()

        # Add tabs to tab widget
        tab_widget.addTab(style_tab, "Style")
        tab_widget.addTab(position_tab, "Configure")

        layout.addWidget(tab_widget)

        # Store reference to viewer for callbacks
        viewer = self

        def on_apply():
            """Apply settings without closing dialog."""
            try:
                if not viewer or viewer.current_image_data is None:
                    dialog.close()
                    return

                viewer.solar_disk_auto_compute = auto_checkbox.isChecked()
                if viewer.solar_disk_auto_compute:
                    viewer.solar_disk_center = None
                else:
                    viewer.solar_disk_center = (x_spinbox.value(), y_spinbox.value())
                viewer.solar_disk_diameter_arcmin = float(diameter_spinbox.value())

                # Update style properties
                viewer.solar_disk_style["color"] = color_combo.currentText()
                viewer.solar_disk_style["linestyle"] = linestyle_combo.currentData()
                viewer.solar_disk_style["linewidth"] = linewidth_spinbox.value()
                viewer.solar_disk_style["alpha"] = alpha_spinbox.value()
                viewer.solar_disk_style["show_center"] = (
                    show_center_checkbox.isChecked()
                )

                # Directly update solar disk without full replot for instant feedback
                ax = viewer.figure.gca()
                viewer._update_solar_disk_position(ax)
                viewer.canvas.draw_idle()
                viewer.show_status_message("Solar disk settings applied")
            except RuntimeError:
                dialog.close()
            except Exception as e:
                try:
                    viewer.show_status_message(f"Error applying settings: {e}")
                except:
                    pass

        def on_close():
            dialog.close()

        def on_dialog_destroyed():
            if hasattr(viewer, "_solar_disk_dialog"):
                viewer._solar_disk_dialog = None

        # Create button box with Apply and Close
        button_box = QDialogButtonBox()
        apply_btn = button_box.addButton("Apply", QDialogButtonBox.ApplyRole)
        close_btn = button_box.addButton("Close", QDialogButtonBox.RejectRole)
        apply_btn.clicked.connect(on_apply)
        close_btn.clicked.connect(on_close)
        layout.addWidget(button_box)

        # Set up for non-modal behavior
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(on_dialog_destroyed)

        # Track dialog for garbage collection
        dialog.destroyed.connect(
            lambda: (
                self._open_dialogs.remove(dialog)
                if dialog in self._open_dialogs
                else None
            )
        )
        self._open_dialogs.append(dialog)

        dialog.show()

        on_auto_toggle(auto_checkbox.isChecked())

    def _update_solar_disk_position(self, ax):
        if (
            hasattr(self, "show_solar_disk_checkbox")
            and self.show_solar_disk_checkbox.isChecked()
        ):
            try:
                # Remove old solar disk patches/lines before drawing new ones
                patches_to_remove = [
                    p for p in ax.patches if getattr(p, "_solar_disk", False)
                ]
                for p in patches_to_remove:
                    p.remove()
                lines_to_remove = [
                    l for l in ax.lines if getattr(l, "_solar_disk", False)
                ]
                for l in lines_to_remove:
                    l.remove()

                if (
                    getattr(self, "solar_disk_auto_compute", True)
                    or self.solar_disk_center is None
                ):
                    if self.current_image_data is not None:
                        self._compute_solar_disk_center(self.current_image_data.shape)
                    else:
                        self.solar_disk_center = (0, 0)

                center_x, center_y = self.solar_disk_center
                if self.current_wcs:
                    radius_deg = (self.solar_disk_diameter_arcmin / 60.0) / 2.0
                    cdelt = self.current_wcs.increment()["numeric"][0:2]
                    if isinstance(cdelt, list):
                        cdelt = [float(c) for c in cdelt]
                    _hdr = getattr(self, "_cached_fits_header", None) or {}
                    _is_solar = "SOLAR-X" in str(_hdr.get("CTYPE1", "")).upper()
                    if _is_solar:
                        cdelt = np.array(cdelt) / 3600.0  # arcsec -> degrees
                    else:
                        cdelt = np.array(cdelt) * 180 / np.pi  # radians -> degrees
                    dx_deg = abs(cdelt[0])
                    radius_pix = radius_deg / dx_deg
                else:
                    radius_pix = min(self.current_image_data.shape) / 8

                circle = plt.Circle(
                    (center_x, center_y),
                    radius_pix,
                    fill=False,
                    edgecolor=self.solar_disk_style["color"],
                    linestyle=self.solar_disk_style["linestyle"],
                    linewidth=self.solar_disk_style["linewidth"],
                    alpha=self.solar_disk_style["alpha"],
                )
                circle._solar_disk = True
                ax.add_patch(circle)

                if self.solar_disk_style.get("show_center", True):
                    cross_size = radius_pix / 20
                    (line1,) = ax.plot(
                        [center_x - cross_size, center_x + cross_size],
                        [center_y, center_y],
                        color=self.solar_disk_style["color"],
                        linewidth=1.5,
                        alpha=self.solar_disk_style["alpha"],
                    )
                    line1._solar_disk = True
                    (line2,) = ax.plot(
                        [center_x, center_x],
                        [center_y - cross_size, center_y + cross_size],
                        color=self.solar_disk_style["color"],
                        linewidth=1.5,
                        alpha=self.solar_disk_style["alpha"],
                    )
                    line2._solar_disk = True
            except Exception as e:
                print(f"[ERROR] Error drawing solar disk: {e}")
                self.show_status_message(f"Error drawing solar disk: {e}")

    def _draw_solar_disk(self, ax, data):
        """Compute solar disk metadata and draw the disk if enabled."""
        # Ensure Solar disk metadata (center and diameter) is ALWAYS computed on load,
        # even if not drawn, so Shape Annotations and Dialogs have physically accurate data
        if (
            getattr(self, "solar_disk_auto_compute", True)
            or self.solar_disk_center is None
        ):
            self._compute_solar_disk_center(data.shape)

        # Draw solar disk if enabled
        if (
            hasattr(self, "show_solar_disk_checkbox")
            and self.show_solar_disk_checkbox.isChecked()
        ):
            try:
                # Remove old solar disk patches/lines before drawing new ones
                patches_to_remove = [
                    p for p in ax.patches if getattr(p, "_solar_disk", False)
                ]
                for p in patches_to_remove:
                    p.remove()
                lines_to_remove = [
                    l for l in ax.lines if getattr(l, "_solar_disk", False)
                ]
                for l in lines_to_remove:
                    l.remove()

                center_x, center_y = self.solar_disk_center

                if self.current_wcs:
                    radius_deg = (self.solar_disk_diameter_arcmin / 60.0) / 2.0
                    cdelt = self.current_wcs.increment()["numeric"][0:2]
                    if isinstance(cdelt, list):
                        cdelt = [float(c) for c in cdelt]
                    # Check if image uses SOLAR-X/Y (increment is in arcsec, not radians)
                    _hdr = getattr(self, "_cached_fits_header", None) or {}
                    _is_solar = "SOLAR-X" in str(_hdr.get("CTYPE1", "")).upper()
                    if _is_solar:
                        cdelt = np.array(cdelt) / 3600.0  # arcsec -> degrees
                    else:
                        cdelt = np.array(cdelt) * 180 / np.pi  # radians -> degrees
                    dx_deg = abs(cdelt[0])
                    radius_pix = radius_deg / dx_deg
                else:
                    radius_pix = min(data.shape) / 8

                circle = plt.Circle(
                    (center_x, center_y),
                    radius_pix,
                    fill=False,
                    edgecolor=self.solar_disk_style["color"],
                    linestyle=self.solar_disk_style["linestyle"],
                    linewidth=self.solar_disk_style["linewidth"],
                    alpha=self.solar_disk_style["alpha"],
                )
                circle._solar_disk = True
                ax.add_patch(circle)

                # Only draw the center marker if show_center is True
                if self.solar_disk_style.get("show_center", True):
                    cross_size = radius_pix / 20
                    (line1,) = ax.plot(
                        [center_x - cross_size, center_x + cross_size],
                        [center_y, center_y],
                        color=self.solar_disk_style["color"],
                        linewidth=1.5,
                        alpha=self.solar_disk_style["alpha"],
                    )
                    line1._solar_disk = True
                    (line2,) = ax.plot(
                        [center_x, center_x],
                        [center_y - cross_size, center_y + cross_size],
                        color=self.solar_disk_style["color"],
                        linewidth=1.5,
                        alpha=self.solar_disk_style["alpha"],
                    )
                    line2._solar_disk = True
            except Exception as e:
                print(f"[ERROR] Error drawing solar disk: {e}")
                self.show_status_message(f"Error drawing solar disk: {e}")

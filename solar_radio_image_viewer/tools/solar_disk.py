"""
Solar disk overlay: auto-detection, manual settings and drawing.
"""

import logging
import matplotlib.pyplot as plt
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QLabel,
    QMessageBox,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..dialogs.beam_settings import OVERLAY_LONGEST
from ..dialogs.form import field_label, form_block
from ..dialogs.solar_disk_style import SolarDiskStyleWidget
from ..processing.image_meta import casa_obs_time
from ..shape_annotations import pixel_scales_deg
from ..ui.styles import SPACE_M, set_hand_cursor
from ..ui.widgets import button_bar, dialog_layout

logger = logging.getLogger(__name__)


class SolarDiskMixin:
    """Solar disk overlay: centre/diameter detection, settings dialog and drawing.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``current_image_data``, ``current_wcs``, ``_is_already_hpc``,
    ``_open_dialogs``, ``show_status_message``, ``solar_disk_style``
    """

    def _compute_solar_disk_center(self, shape):
        """Auto-compute solar disk center and diameter from WCS/FITS header.

        If the Sun's position cannot be worked out, the disk goes to the image
        centre and ``_solar_disk_guess`` says why (None when it was found).
        """
        nx, ny = shape[:2]  # the image is indexed [x, y]
        self._solar_disk_guess = None
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
            except Exception as e:
                self._centre_disk_on_image(nx, ny, f"could not place Solar-X/Y (0, 0): {e}")
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
            except Exception as e:
                self._centre_disk_on_image(nx, ny, f"could not place Solar-X/Y (0, 0): {e}")
        else:
            _auto_computed = False
            _reason = "the image has no sky coordinates"
            _hdr = getattr(self, "_cached_fits_header", None) or {}
            if (
                (_hdr or self.current_wcs)
                and hasattr(self, "_cached_wcs_obj")
                and self._cached_wcs_obj is not None
            ):
                try:
                    from astropy.time import Time
                    from sunpy.coordinates.sun import angular_radius

                    from ..coordinates.observer import sun_radec

                    date_str = None
                    for key in ["DATE-OBS", "DATE_OBS", "DATE", "STARTOBS", "T_OBS"]:
                        date_str = _hdr.get(key)
                        if date_str:
                            break

                    if not date_str and self.current_wcs:
                        t = casa_obs_time(self.current_wcs)
                        date_str = t.iso if t is not None else None

                    if not date_str:
                        _reason = "the image has no observation date"
                    else:
                        obs_time = Time(str(date_str))
                        # (as the telescope saw it, so that the disk is where
                        # the Solar XY view of this image has its (0, 0))
                        ra_deg, dec_deg, _ = sun_radec(
                            getattr(self, "imagename", None), _hdr, obs_time
                        )
                        px, py = self._cached_wcs_obj.wcs_world2pix(ra_deg, dec_deg, 0)
                        cx, cy = float(px), float(py)
                        self.solar_disk_center = (cx, cy)
                        ang_radius = angular_radius(obs_time)
                        self.solar_disk_diameter_arcmin = (
                            float(ang_radius.to("arcmin").value) * 2.0
                        )
                        _auto_computed = True
                except Exception as e:
                    _reason = f"finding the Sun failed: {e}"
            if not _auto_computed:
                self._centre_disk_on_image(nx, ny, _reason)

    def _centre_disk_on_image(self, nx, ny, reason):
        """Put the disk at the image centre, as the Sun's position is unknown."""
        self.solar_disk_center = (nx // 2, ny // 2)
        self._solar_disk_guess = reason

    def _say_if_disk_is_a_guess(self, what="Solar disk"):
        """When ``what`` is drawn at the image centre for want of the Sun's
        position, say so in the status bar."""
        reason = getattr(self, "_solar_disk_guess", None)
        if reason and getattr(self, "solar_disk_auto_compute", True):
            self.show_status_message(
                f"{what} drawn at the image centre ({reason}); "
                "set the Sun's centre in the Solar Disk settings"
            )

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
                t = casa_obs_time(self.current_wcs)
                date_str = t.iso if t is not None else None

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
            QMessageBox.warning(self, "No image", "Please load an image first.")
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

        nx, ny = self.current_image_data.shape  # the image is indexed [x, y]

        dialog = QDialog(self)
        dialog.setWindowTitle("Solar disk settings")
        set_hand_cursor(dialog)
        layout = dialog_layout(dialog)

        # Store reference to prevent garbage collection
        self._solar_disk_dialog = dialog

        # Create tab widget for organizing settings
        tab_widget = QTabWidget()

        # Style tab (formerly Appearance tab)
        style_tab = QWidget()
        style_layout = QVBoxLayout(style_tab)
        style_layout.setContentsMargins(0, SPACE_M, 0, 0)

        style_widget = SolarDiskStyleWidget(self.solar_disk_style)
        style_layout.addWidget(style_widget)
        style_layout.addStretch()

        # Position tab
        position_tab = QWidget()
        position_layout = QVBoxLayout(position_tab)
        position_layout.setContentsMargins(0, SPACE_M, 0, 0)
        position_layout.setSpacing(SPACE_M)

        # Auto-compute toggle
        auto_checkbox = QCheckBox("Compute from the FITS header")
        auto_checkbox.setToolTip(
            "Automatically compute the Sun's position and apparent\n"
            "diameter from the observation date in the FITS header."
        )
        auto_checkbox.setChecked(self.solar_disk_auto_compute)
        position_layout.addWidget(auto_checkbox)
        auto_info = QLabel("")
        auto_info.setObjectName("Hint")
        auto_info.setWordWrap(True)
        position_layout.addWidget(auto_info)

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
                    t = casa_obs_time(viewer_ref.current_wcs)
                    date_str = t.iso if t is not None else None

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
                    if cx is None:
                        auto_info.setText(
                            "Could not place Solar-X/Y (0, 0) in this image; "
                            "set the centre by hand."
                        )
                        return False
                    x_spinbox.setValue(int(round(cx)))
                    y_spinbox.setValue(int(round(cy)))
                    auto_info.setText(
                        f"HPC image: centre set to (0, 0)\n"
                        f"Apparent diameter {diam:.2f}′ on {obs_time.iso[:19]}"
                    )
                else:
                    if wcs_obj is None:
                        auto_info.setText("No WCS available for the RA/Dec conversion.")
                        return False
                    from ..coordinates.observer import sun_radec

                    ra_deg, dec_deg, seen_from = sun_radec(
                        getattr(viewer_ref, "imagename", None), _hdr, obs_time
                    )
                    px, py = wcs_obj.wcs_world2pix(ra_deg, dec_deg, 0)
                    cx, cy = float(px), float(py)
                    x_spinbox.setValue(int(round(cx)))
                    y_spinbox.setValue(int(round(cy)))
                    auto_info.setText(
                        f"Sun RA {ra_deg:.4f}°, Dec {dec_deg:.4f}°"
                        f" (seen from {seen_from or 'the centre of the Earth'})\n"
                        f"At pixel ({cx:.1f}, {cy:.1f})\n"
                        f"Apparent diameter {diam:.2f}′ on {obs_time.iso[:19]}"
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
        center_layout = form_block(position_layout, pairs=2, longest=OVERLAY_LONGEST)

        x_label = field_label("Centre X")
        x_spinbox = QSpinBox()
        x_spinbox.setRange(-nx * 5, nx * 5)
        if self.solar_disk_center is not None:
            x_spinbox.setValue(int(round(self.solar_disk_center[0])))
        else:
            x_spinbox.setValue(nx // 2)
        center_layout.addWidget(x_label, 0, 0)
        center_layout.addWidget(x_spinbox, 0, 1)

        y_label = field_label("Centre Y")
        y_spinbox = QSpinBox()
        y_spinbox.setRange(-ny * 5, ny * 5)
        if self.solar_disk_center is not None:
            y_spinbox.setValue(int(round(self.solar_disk_center[1])))
        else:
            y_spinbox.setValue(ny // 2)
        center_layout.addWidget(y_label, 0, 2)
        center_layout.addWidget(y_spinbox, 0, 3)

        # Diameter
        diameter_label = field_label("Diameter")
        diameter_spinbox = QDoubleSpinBox()
        diameter_spinbox.setRange(1.0, 100.0)
        diameter_spinbox.setDecimals(2)
        diameter_spinbox.setSingleStep(0.1)
        diameter_spinbox.setSuffix(" arcmin")
        diameter_spinbox.setValue(self.solar_disk_diameter_arcmin)
        center_layout.addWidget(diameter_label, 1, 0)
        center_layout.addWidget(diameter_spinbox, 1, 1, 1, 3)

        position_layout.addStretch()

        # Add tabs to tab widget
        tab_widget.addTab(style_tab, "Style")
        tab_widget.addTab(position_tab, "Position")

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
                viewer.solar_disk_style.update(style_widget.get_style())

                # Directly update solar disk without full replot for instant feedback
                ax = viewer._nav_axes()
                if ax is not None:
                    viewer._update_solar_disk_position(ax)
                viewer.canvas.draw_idle()
                viewer.show_status_message("Solar disk settings applied")
            except RuntimeError:
                dialog.close()
            except Exception as e:
                try:
                    viewer.show_status_message(f"Error applying settings: {e}")
                except Exception:
                    pass

        def on_close():
            dialog.close()

        def on_dialog_destroyed():
            if hasattr(viewer, "_solar_disk_dialog"):
                viewer._solar_disk_dialog = None

        button_box, _ = button_bar(primary=("Apply", on_apply), secondary=("Close", on_close))
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
                    radius_pix = radius_deg / pixel_scales_deg(self.current_wcs)[0]
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
                self._say_if_disk_is_a_guess()

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
                logger.error(f"Error drawing solar disk: {e}")
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
                    radius_pix = radius_deg / pixel_scales_deg(self.current_wcs)[0]
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
                self._say_if_disk_is_a_guess()

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
                logger.error(f"Error drawing solar disk: {e}")
                self.show_status_message(f"Error drawing solar disk: {e}")

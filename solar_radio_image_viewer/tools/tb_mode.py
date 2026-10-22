"""
Brightness temperature (TB) mode tool for SolarViewer.
"""

from PyQt5.QtWidgets import QMessageBox

from ..processing.maps import TEMP_EXPORT_SUFFIX, write_flux_map, write_tb_map
from ..ui.background import Cancelled, run_in_background
from ..ui.busy import begin_busy, end_busy
from ..ui.errors import show_error


class TbModeMixin:
    """Toggle between flux (Jy/beam) and brightness temperature (K) views.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``current_image_data``, ``hpc_btn``, ``_hpc_temp_file``,
    ``on_visualization_changed``, ``show_status_message``, ``tb_btn``,
    ``_temp_file_id``
    """

    def _toggle_tb_mode(self):
        """Toggle between flux (Jy/beam) and brightness temperature (K) view"""
        import os
        import tempfile

        from PyQt5.QtCore import QEventLoop
        from PyQt5.QtWidgets import QApplication

        if self.current_image_data is None:
            return

        # Check if we're viewing the TB/Flux temp file - only then revert
        # If we're viewing something else (e.g. HPC file), do fresh conversion
        is_viewing_temp = (
            hasattr(self, "_tb_temp_file")
            and self._tb_temp_file
            and self.imagename == self._tb_temp_file
        )

        if (
            is_viewing_temp
            and hasattr(self, "_tb_original_imagename")
            and self._tb_original_imagename
        ):
            self._revert_from_tb_mode()
            return

        # If we have stale TB state (not viewing temp file), clear it
        if hasattr(self, "_tb_original_imagename") and self._tb_original_imagename:
            # Clean up old temp file if exists
            if (
                hasattr(self, "_tb_temp_file")
                and self._tb_temp_file
                and os.path.exists(self._tb_temp_file)
            ):
                try:
                    os.remove(self._tb_temp_file)
                except Exception:
                    pass
            self._tb_original_imagename = None
            self._tb_temp_file = None

        # Also clear stale HPC state when doing fresh conversion
        # This prevents issues when converting HPC K -> Jy/beam
        if hasattr(self, "_original_imagename") and self._original_imagename:
            # We're converting from an HPC file - clear HPC revert state
            if (
                hasattr(self, "_hpc_temp_file")
                and self._hpc_temp_file
                and self.imagename == self._hpc_temp_file
            ):
                # Currently viewing HPC temp - clear HPC state since we'll create new temp
                self._original_imagename = None
                # Update HPC button to reflect that we're now on an HPC-derived file
                # (revert not available since we're converting to new temp)
                if hasattr(self, "hpc_btn"):
                    self.hpc_btn.setText("HPC")
                    self.hpc_btn.setToolTip("Already in helioprojective coordinates")

        # Clear HPC->RA/Dec revert state when doing fresh conversion
        # This prevents trying to revert to a deleted flux temp file
        if hasattr(self, "_hpc_original_imagename") and self._hpc_original_imagename:
            # Only delete RA/Dec temp if we're NOT currently viewing it
            # (if we are viewing it, it will be the source for the new conversion)
            if hasattr(self, "_radec_temp_file") and self._radec_temp_file:
                if self.imagename != self._radec_temp_file:
                    # Safe to delete - not currently viewing this file
                    if os.path.exists(self._radec_temp_file):
                        try:
                            os.remove(self._radec_temp_file)
                        except Exception:
                            pass
                self._radec_temp_file = None
            self._hpc_original_imagename = None

        # Determine which direction to convert based on current units
        current_bunit = getattr(self, "_current_bunit", "").strip().lower()
        is_kelvin = current_bunit == "k"
        is_jy_beam = "jy" in current_bunit and "beam" in current_bunit

        if not is_kelvin and not is_jy_beam:
            QMessageBox.warning(self, "Error", f"Cannot convert units: {current_bunit}")
            return

        try:
            begin_busy()

            if is_kelvin:
                # K -> Jy/beam (Flux conversion)
                self.show_status_message("Converting to flux (Jy/beam)...")
                QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)

                # Store original imagename
                self._tb_original_imagename = self.imagename
                self._tb_original_unit = "K"

                # Create temp file
                temp_dir = tempfile.gettempdir()
                self._tb_temp_file = os.path.join(
                    temp_dir, f"solarviewer_flux_temp_{self._temp_file_id}.fits"
                )

                error = run_in_background(
                    self,
                    "Converting to flux density…",
                    write_flux_map,
                    self._tb_original_imagename,
                    self._tb_temp_file,
                )

                if error is not None:
                    end_busy()
                    QMessageBox.warning(
                        self, "Error", f"Flux conversion failed: {error}"
                    )
                    self._tb_original_imagename = None
                    return

                self.show_status_message("Created temp flux file")

                # Load the flux FITS file
                self.imagename = self._tb_temp_file
                self._tb_mode = True
                self._current_bunit = "Jy/beam"

                # Reload and plot
                shown = self.on_visualization_changed(dir_load=True)

                # Update button
                self.tb_btn.setText("TB")
                self.tb_btn.setToolTip("Revert to Brightness Temperature (K) view")
                self.tb_btn.setChecked(True)

                end_busy()
                if shown:
                    self.show_status_message(
                        "Showing flux (Jy/beam) - click TB to revert"
                    )

            else:
                # Jy/beam -> K (TB conversion)
                self.show_status_message("Converting to brightness temperature...")
                QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)

                # Store original imagename
                self._tb_original_imagename = self.imagename
                self._tb_original_unit = "Jy/beam"

                # Create temp file for TB FITS
                temp_dir = tempfile.gettempdir()
                self._tb_temp_file = os.path.join(
                    temp_dir, f"solarviewer_tb_temp_{self._temp_file_id}.fits"
                )

                error = run_in_background(
                    self,
                    "Converting to brightness temperature…",
                    write_tb_map,
                    self._tb_original_imagename,
                    self._tb_temp_file,
                )

                if error is not None:
                    end_busy()
                    QMessageBox.warning(
                        self, "Error", f"TB conversion failed: {error}"
                    )
                    self._tb_original_imagename = None
                    return

                # Load the TB FITS file
                self.imagename = self._tb_temp_file
                self._tb_mode = True
                self._current_bunit = "K"

                # Reload and plot (use dir_load to avoid changing presets)
                shown = self.on_visualization_changed(dir_load=True)

                # Update button
                self.tb_btn.setText("FLUX")
                self.tb_btn.setToolTip("Revert to Flux (Jy/beam) view")
                self.tb_btn.setChecked(True)

                end_busy()
                if shown:
                    self.show_status_message(
                        "Showing brightness temperature (K) - click FLUX to revert"
                    )

        except Cancelled:
            end_busy()
            # What the stopped conversion had written
            for path in (self._tb_temp_file, self._tb_temp_file + TEMP_EXPORT_SUFFIX):
                if os.path.exists(path):
                    os.remove(path)
            self._tb_original_imagename = None
            self._tb_temp_file = None
            self.show_status_message("Conversion cancelled")
        except Exception as e:
            import traceback

            traceback.print_exc()
            end_busy()
            self._tb_original_imagename = None
            show_error(
                self,
                "Conversion Failed",
                f"Could not convert between flux density and brightness "
                f"temperature: {e}\n\nThe conversion needs the beam (BMAJ, BMIN) "
                "and the frequency from the image's header.",
                traceback.format_exc(),
            )

    def _revert_from_tb_mode(self):
        """Revert from converted mode to original image"""
        import os

        # Store original unit before clearing
        original_unit = self._tb_original_unit or "Jy/beam"

        # Check if original file still exists
        if not self._tb_original_imagename:
            QMessageBox.warning(self, "Error", "No original image to revert to")
            return

        if not os.path.exists(self._tb_original_imagename):
            QMessageBox.warning(
                self,
                "Error",
                f"Original file no longer exists:\n{self._tb_original_imagename}",
            )
            self._tb_original_imagename = None
            self._tb_temp_file = None
            self._tb_mode = False
            self.tb_btn.setChecked(False)
            return

        begin_busy()
        try:
            self.show_status_message(f"Reverting to original ({original_unit})...")

            # Restore original imagename
            self.imagename = self._tb_original_imagename
            self._tb_original_imagename = None
            self._tb_original_unit = None
            self._tb_mode = False

            # Reload original image
            shown = self.on_visualization_changed(dir_load=True)

            # Clean up temp file
            if (
                hasattr(self, "_tb_temp_file")
                and self._tb_temp_file
                and os.path.exists(self._tb_temp_file)
            ):
                try:
                    os.remove(self._tb_temp_file)
                except Exception:
                    pass
                self._tb_temp_file = None

            # Update button based on original unit
            if original_unit == "K":
                self.tb_btn.setText("FLUX")
                self.tb_btn.setToolTip("Convert to Flux (Jy/beam) view")
                if shown:
                    self.show_status_message("Showing brightness temperature (K)")
            else:
                self.tb_btn.setText("TB")
                self.tb_btn.setToolTip("Convert to Brightness Temperature (K)")
                if shown:
                    self.show_status_message("Showing flux (Jy/beam)")

            self.tb_btn.setChecked(False)
        finally:
            end_busy()

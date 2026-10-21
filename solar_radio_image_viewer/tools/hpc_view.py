"""
Switching a tab between RA/Dec and helioprojective (HPC) views.
"""

import os


from ..processing.dependencies import IA
from ..processing.fits_io import readable_image_path
from ..processing.image_meta import casa_info, fits_header


class HpcViewMixin:
    """Toggle the current image between RA/Dec and helioprojective coordinates.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``contour_layers``, ``figure``, ``hpc_btn``, ``load_contour_data``,
    ``on_visualization_changed``, ``schedule_plot``, ``show_contours_checkbox``,
    ``show_status_message``, ``tb_btn``, ``_temp_file_id``, ``threshold_entry``,
    ``update_tab_name_from_path``
    """

    def launch_helioprojective_viewer(self):
        """Toggle between HPC and RA/DEC views in main canvas"""
        if not hasattr(self, "imagename") or not self.imagename:
            self.show_status_message("No image loaded")
            return

        # Check if splash image
        if self.imagename.endswith("splash.fits"):
            self.show_status_message("Please load an image first")
            return

        # Check if we're currently in HPC mode - if so, revert to original
        if hasattr(self, "_original_imagename") and self._original_imagename:
            self._revert_to_radec_view()
            return

        # Check if we're viewing RA/Dec temp file from HPC conversion - revert to HPC
        if hasattr(self, "_hpc_original_imagename") and self._hpc_original_imagename:
            self._revert_to_hpc_from_radec()
            return

        try:
            import os
            import tempfile

            from PyQt5.QtCore import Qt
            from PyQt5.QtWidgets import QApplication, QMessageBox

            # Check if image is already in helioprojective coordinates (Solar-X/Y)
            # If so, convert TO RA/Dec instead of showing error
            if self._is_already_hpc():
                self._revert_to_radec_view()
                return

            QApplication.setOverrideCursor(Qt.WaitCursor)
            self.show_status_message("Converting to helioprojective coordinates...")
            QApplication.processEvents()

            # Import conversion function
            from ..coordinates.helioprojective import convert_and_save_hpc_all_stokes

            # Store original imagename before switching
            self._original_imagename = self.imagename

            # Get current parameters
            try:
                threshold = float(self.threshold_entry.text())
            except (ValueError, AttributeError):
                threshold = 10.0

            # Create temp file for HPC FITS
            temp_dir = tempfile.gettempdir()
            temp_hpc_file = os.path.join(
                temp_dir, f"solarviewer_hpc_temp_{self._temp_file_id}.fits"
            )

            # Convert all Stokes and save to temp file
            success = convert_and_save_hpc_all_stokes(
                input_fits_file=self.imagename,
                output_fits_file=temp_hpc_file,
                thres=threshold,
                overwrite=True,
            )

            if not success or not os.path.exists(temp_hpc_file):
                QApplication.restoreOverrideCursor()
                self._original_imagename = None  # Reset since conversion failed
                QMessageBox.warning(
                    self,
                    "Conversion Failed",
                    "Failed to convert to helioprojective coordinates.\n\n"
                    "Please ensure the image has valid RA/DEC coordinate information.",
                )
                return

            # Store temp file path for cleanup
            self._hpc_temp_file = temp_hpc_file

            # Clear old contour data (stale WCS would cause reprojection failures)
            self.invalidate_contours()

            # Load the HPC FITS file in the main canvas
            self.imagename = temp_hpc_file
            # Clear figure to prevent restoring old view limits
            self.figure.clear()
            self.on_visualization_changed(dir_load=True)
            self.update_tab_name_from_path(temp_hpc_file)

            # Refresh contours if enabled
            if (
                hasattr(self, "show_contours_checkbox")
                and self.show_contours_checkbox.isChecked()
            ):
                self.load_contour_data()
                self.schedule_plot()  # Redraw to show contours

            # Update button to show RA/DEC option
            if hasattr(self, "hpc_btn"):
                self.hpc_btn.setText("RA/DEC")
                self.hpc_btn.setToolTip("Revert to RA/DEC view")

            QApplication.restoreOverrideCursor()
            self.show_status_message(
                "Helioprojective view - click RA/DEC button to revert"
            )

        except Exception as e:
            import traceback

            traceback.print_exc()
            QApplication.restoreOverrideCursor()
            self._original_imagename = None  # Reset on error
            QMessageBox.critical(
                self,
                "Error",
                f"Error converting to helioprojective coordinates:\n\n{str(e)}",
            )

    def _revert_to_radec_view(self):
        """Revert from HPC view back to RA/DEC view"""
        import os
        import tempfile

        try:
            from PyQt5.QtCore import Qt
            from PyQt5.QtWidgets import QApplication, QMessageBox

            QApplication.setOverrideCursor(Qt.WaitCursor)
            self.show_status_message("Converting to RA/DEC view...")

            # Check if we have an original file to revert to
            has_original = (
                hasattr(self, "_original_imagename") and self._original_imagename
            )

            if has_original:
                # Revert to original file
                self.imagename = self._original_imagename
                self._original_imagename = None

                # Clean up temp HPC file
                if hasattr(self, "_hpc_temp_file") and self._hpc_temp_file:
                    if os.path.exists(self._hpc_temp_file):
                        try:
                            os.remove(self._hpc_temp_file)
                        except Exception:
                            pass
                    self._hpc_temp_file = None
            else:
                # No original - convert current HPC file to RA/Dec using the new function
                from ..coordinates.helioprojective import convert_hpc_to_radec

                # Create temp output file
                temp_dir = tempfile.gettempdir()
                self._radec_temp_file = os.path.join(
                    temp_dir, f"solarviewer_radec_temp_{self._temp_file_id}.fits"
                )

                # Store current as "original" for potential revert
                self._hpc_original_imagename = self.imagename

                success = convert_hpc_to_radec(
                    self.imagename, self._radec_temp_file, overwrite=True
                )

                if not success:
                    QApplication.restoreOverrideCursor()
                    QMessageBox.warning(
                        self, "Error", "Failed to convert HPC to RA/Dec"
                    )
                    return

                self.imagename = self._radec_temp_file

            # Clear old contour data (stale WCS would cause reprojection failures)
            self.invalidate_contours()

            # Reload image
            # Clear figure to prevent restoring old view limits
            self.figure.clear()
            self.on_visualization_changed(dir_load=True)
            self.update_tab_name_from_path(self.imagename)

            # Refresh contours if enabled
            if (
                hasattr(self, "show_contours_checkbox")
                and self.show_contours_checkbox.isChecked()
            ):
                self.load_contour_data()
                self.schedule_plot()  # Redraw to show contours

            # Update button back to Helioprojective
            if hasattr(self, "hpc_btn"):
                self.hpc_btn.setText("HPC")
                self.hpc_btn.setToolTip("Convert to Helioprojective view")

            QApplication.restoreOverrideCursor()
            self.show_status_message("RA/DEC view - Click HPC button to revert")

        except Exception as e:
            import traceback

            traceback.print_exc()
            QApplication.restoreOverrideCursor()
            self.show_status_message(f"Error reverting: {str(e)}")

    def _revert_to_hpc_from_radec(self):
        """Revert from RA/Dec temp file back to original HPC file"""
        import os

        from PyQt5.QtWidgets import QMessageBox

        if not self._hpc_original_imagename:
            return

        # Check if original HPC file still exists
        if not os.path.exists(self._hpc_original_imagename):
            QMessageBox.warning(
                self,
                "Error",
                f"Original HPC file no longer exists:\n{self._hpc_original_imagename}",
            )
            self._hpc_original_imagename = None
            self._radec_temp_file = None
            return

        try:
            from PyQt5.QtCore import Qt
            from PyQt5.QtWidgets import QApplication

            QApplication.setOverrideCursor(Qt.WaitCursor)
            self.show_status_message("Reverting to HPC view...")

            # Restore HPC file
            self.imagename = self._hpc_original_imagename
            self._hpc_original_imagename = None

            # Clean up temp RA/Dec file
            if hasattr(self, "_radec_temp_file") and self._radec_temp_file:
                if os.path.exists(self._radec_temp_file):
                    try:
                        os.remove(self._radec_temp_file)
                        # print(
                        #    f"[HPC] Removed temp RA/Dec file: {self._radec_temp_file}"
                        # )
                    except Exception:
                        pass
                self._radec_temp_file = None

            # Clear old contour data
            self.invalidate_contours()

            # Reload HPC image
            # Clear figure to prevent restoring old view limits
            self.figure.clear()
            self.on_visualization_changed(dir_load=True)
            self.update_tab_name_from_path(self.imagename)

            # Update button
            if hasattr(self, "hpc_btn"):
                self.hpc_btn.setText("RA/DEC")
                self.hpc_btn.setToolTip("Convert to RA/DEC view")

            QApplication.restoreOverrideCursor()
            self.show_status_message("Showing HPC view")

        except Exception as e:
            import traceback

            traceback.print_exc()
            QApplication.restoreOverrideCursor()
            self.show_status_message(f"Error reverting: {str(e)}")

    def _reset_hpc_state(self):
        """Reset HPC and TB state when loading a new image"""
        import os

        # Clear the original imagename reference for HPC
        self._original_imagename = None

        # Clean up temp HPC file if exists
        if hasattr(self, "_hpc_temp_file") and self._hpc_temp_file:
            if os.path.exists(self._hpc_temp_file):
                try:
                    os.remove(self._hpc_temp_file)
                except Exception:
                    pass
            self._hpc_temp_file = None

        # Clean up temp RA/Dec file if exists (from HPC->RA/Dec conversion)
        if hasattr(self, "_radec_temp_file") and self._radec_temp_file:
            if os.path.exists(self._radec_temp_file):
                try:
                    os.remove(self._radec_temp_file)
                except Exception:
                    pass
            self._radec_temp_file = None

        # Clear HPC original reference (for HPC->RA/Dec revert)
        self._hpc_original_imagename = None

        # Reset HPC button
        if hasattr(self, "hpc_btn"):
            self.hpc_btn.setText("HPC")
            self.hpc_btn.setToolTip("Convert to Helioprojective view")

        # Clear TB state as well
        self._tb_original_imagename = None
        self._tb_original_unit = None
        self._tb_mode = False

        # Clean up temp TB file if exists
        if hasattr(self, "_tb_temp_file") and self._tb_temp_file:
            if os.path.exists(self._tb_temp_file):
                try:
                    os.remove(self._tb_temp_file)
                    # print(
                    #    f"[TB] Cleaned up temp file on new image load: {self._tb_temp_file}"
                    # )
                except Exception:
                    pass
            self._tb_temp_file = None

        # Reset TB button
        if hasattr(self, "tb_btn"):
            self.tb_btn.setText("TB")
            self.tb_btn.setToolTip("Convert to Brightness Temperature (K)")
            self.tb_btn.setChecked(False)

    def _is_already_hpc(self):
        """Check if the current image is already in helioprojective coordinates (Solar-X/Y)"""
        try:
            # Check via CASA image tool
            csys = casa_info(self.imagename).coordsys()
            dimension_names = [n.upper() for n in csys.names()]

            # Check for Solar-X/Solar-Y in coordinate names
            if "SOLAR-X" in dimension_names or "HPLN-TAN" in dimension_names:
                return True

            # Also check FITS header if it's a FITS file
            if self.imagename.endswith(".fits") or self.imagename.endswith(".fts"):
                header = fits_header(self.imagename)
                ctype1 = header.get("CTYPE1", "").upper()
                ctype2 = header.get("CTYPE2", "").upper()
                if (
                    "HPLN" in ctype1
                    or "HPLT" in ctype2
                    or "SOLAR" in ctype1
                    or "SOLAR" in ctype2
                ):
                    return True

            return False
        except Exception:
            return False

    def _get_coordinate_system_from_image(self, imagepath):
        """
        Detect coordinate system from a FITS or CASA image.

        Parameters
        ----------
        imagepath : str
            Path to the FITS file or CASA image

        Returns
        -------
        str
            'hpc' for helioprojective, 'radec' for RA/Dec, 'unknown' otherwise
        """
        imagepath = readable_image_path(imagepath)
        if not imagepath or not os.path.exists(imagepath):
            return "unknown"

        try:
            # For FITS files, check the header first (more reliable)
            if imagepath.endswith(".fits") or imagepath.endswith(".fts"):
                from astropy.io import fits

                header = fits.getheader(imagepath)
                ctype1 = header.get("CTYPE1", "").upper()
                ctype2 = header.get("CTYPE2", "").upper()

                # Check for HPC (Helioprojective)
                if (
                    "HPLN" in ctype1
                    or "HPLT" in ctype2
                    or "SOLAR" in ctype1
                    or "SOLAR" in ctype2
                ):
                    return "hpc"
                # Check for RA/Dec
                if "RA" in ctype1 or "DEC" in ctype2:
                    return "radec"
        except Exception as e:
            print(f"[ERROR] FITS header check failed: {e}")

        try:
            # For CASA images, use coordinate system info
            ia_tool = IA()
            ia_tool.open(imagepath)
            csys = ia_tool.coordsys()

            # Get direction reference code (e.g., 'J2000' for RA/Dec, 'SUN' for HPC)
            ref_code = csys.referencecode("direction")
            dimension_names = [n.upper() for n in csys.names()]

            ia_tool.close()

            # Check for Solar/Helioprojective (SUN reference frame)
            if ref_code and "SUN" in str(ref_code).upper():
                return "hpc"
            if "SOLAR-X" in dimension_names or "SOLAR-Y" in dimension_names:
                return "hpc"
            # Check for RA/Dec (J2000 reference frame)
            if ref_code and "J2000" in str(ref_code).upper():
                return "radec"
            if "RIGHT ASCENSION" in dimension_names or "DECLINATION" in dimension_names:
                return "radec"
        except Exception as e:
            print(f"[ERROR] CASA check failed: {e}")

        return "unknown"

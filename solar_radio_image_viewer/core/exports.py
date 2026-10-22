"""
Main-window export actions (FITS, CASA image, sub-image, regions, HPC).
"""

import os

from PyQt5.QtCore import QEventLoop
from PyQt5.QtWidgets import QApplication, QFileDialog, QMessageBox

from ..processing.dependencies import IA
from ..processing.casa_tasks import run_casa_task
from ..processing.fits_io import is_fits, readable_image_path
from ..ui.busy import begin_busy, busy, end_busy
from ..ui.errors import show_error


def _export_failed(parent, what, error):
    """Say that saving ``what`` failed (called while handling ``error``)."""
    import traceback

    show_error(
        parent,
        "Export Failed",
        f"Could not save {what}: {error}\n\nCheck that the folder exists, that "
        "you can write to it, and that the disk is not full.",
        traceback.format_exc(),
    )


class ExportsMixin:
    """Export the current tab's image/regions in various formats.

    Mixed into :class:`SolarRadioImageViewerApp`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``statusBar``, ``tab_widget``
    """

    def export_data(self):
        current_tab = self.tab_widget.currentWidget()
        if not current_tab:
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Figure",
            "",
            "PNG Files (*.png);;PDF Files (*.pdf);;All Files (*)",
        )
        if path:
            with busy():
                current_tab.figure.savefig(path, dpi=300, bbox_inches="tight")
            QMessageBox.information(self, "Exported", f"Figure saved to {path}")

    def export_as_fits(self):
        """Export current image data as FITS file with all metadata preserved."""
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or current_tab.current_image_data is None:
            QMessageBox.warning(self, "No Data", "No image data to export")
            return

        try:
            import os

            from astropy.io import fits

            path, _ = QFileDialog.getSaveFileName(
                self, "Export as FITS", "", "FITS Files (*.fits);;All Files (*)"
            )
            if not path:
                return

            # Check if source is a CASA image (directory)
            if hasattr(current_tab, "imagename") and current_tab.imagename:
                source_image = current_tab.imagename
            else:
                source_image = None

            # If source is a CASA image directory, use casatask exportfits
            if source_image and os.path.isdir(source_image):
                try:
                    # Every Stokes plane, in a separate process
                    with busy():
                        run_casa_task(
                            "exportfits",
                            imagename=os.path.abspath(source_image),
                            fitsimage=os.path.abspath(path),
                            overwrite=True,
                            stokeslast=True,
                        )

                    QMessageBox.information(
                        self, "Exported", f"CASA image exported to {path}"
                    )
                    return
                except Exception as e:
                    _export_failed(self, "the CASA image", e)
                    return

            # For FITS files, copy the original with all data and metadata
            if source_image and (
                is_fits(source_image)
            ):
                try:
                    import shutil

                    # If saving to a different location, copy the original file
                    if os.path.abspath(source_image) != os.path.abspath(path):
                        shutil.copy2(source_image, path)
                        QMessageBox.information(
                            self, "Exported", f"FITS file exported to {path}"
                        )
                    else:
                        QMessageBox.warning(
                            self,
                            "Same File",
                            "Source and destination are the same file.",
                        )
                    return
                except Exception:
                    # Fall back to creating from current data if copy fails
                    pass

            # Fallback: create FITS from current_image_data with header
            # Get the original header if available
            original_header = None
            if hasattr(current_tab, "current_header") and current_tab.current_header:
                original_header = current_tab.current_header

            # Create HDU with data
            if original_header:
                # Convert dict to FITS Header if needed
                if isinstance(original_header, dict):
                    header = fits.Header()
                    for key, value in original_header.items():
                        # Skip problematic keys and ensure valid FITS keywords
                        if key in ["SIMPLE", "BITPIX", "NAXIS", "EXTEND", ""]:
                            continue
                        if key.startswith("NAXIS"):
                            continue
                        try:
                            # Handle COMMENT and HISTORY specially
                            if key in ["COMMENT", "HISTORY"]:
                                if isinstance(value, list):
                                    for v in value:
                                        header[key] = str(v)
                                else:
                                    header[key] = str(value)
                            else:
                                header[key] = value
                        except (ValueError, KeyError):
                            # Skip keys that can't be added
                            pass
                    hdu = fits.PrimaryHDU(current_tab.current_image_data, header=header)
                else:
                    # Already a FITS header
                    hdu = fits.PrimaryHDU(
                        current_tab.current_image_data, header=original_header
                    )
            else:
                # No header available, create basic HDU
                hdu = fits.PrimaryHDU(current_tab.current_image_data)

            # Add HISTORY entry
            hdu.header.add_history("Exported with SolarViewer")

            hdul = fits.HDUList([hdu])
            hdul.writeto(path, overwrite=True)
            QMessageBox.information(self, "Exported", f"Data saved to {path}")
        except ImportError:
            QMessageBox.warning(
                self,
                "Missing Dependency",
                "Astropy is required for FITS export. Please install it.",
            )
        except Exception as e:
            _export_failed(self, "the FITS file", e)

    def export_as_casa_image(self):
        """Export/convert current image to CASA image format."""
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or current_tab.current_image_data is None:
            QMessageBox.warning(self, "No Data", "No image data to export")
            return

        try:
            # Get source image path
            if hasattr(current_tab, "imagename") and current_tab.imagename:
                source_image = current_tab.imagename
            else:
                QMessageBox.warning(self, "No Source", "No source image file available")
                return

            # Suggest a default output name based on source
            default_name = (
                os.path.splitext(os.path.basename(source_image))[0] + ".image"
            )

            # Ask user for output path and name
            output_image, _ = QFileDialog.getSaveFileName(
                self,
                "Export as CASA Image",
                default_name,
                "CASA Image (*.image);;All Files (*)",
            )
            if not output_image:
                return

            # Ensure it has .image extension
            if not output_image.endswith(".image"):
                output_image += ".image"

            # Check if output already exists
            if os.path.exists(output_image):
                reply = QMessageBox.question(
                    self,
                    "Overwrite?",
                    f"'{output_image}' already exists. Do you want to overwrite it?",
                    QMessageBox.Yes | QMessageBox.No,
                )
                if reply == QMessageBox.No:
                    return
                import shutil

                shutil.rmtree(output_image)

            # If source is already a CASA image, copy it
            if os.path.isdir(source_image):
                import shutil

                shutil.copytree(source_image, output_image)
                QMessageBox.information(
                    self, "Exported", f"CASA image exported to {output_image}"
                )
                return

            # If source is a FITS file, use importfits via subprocess
            if is_fits(source_image):
                # Check coordinate system
                import tempfile

                fits_to_import = source_image
                is_hpc_converted = False

                try:
                    from astropy.io import fits as afits

                    with afits.open(source_image) as hdul:
                        header = hdul[0].header
                        ctype1 = header.get("CTYPE1", "").upper()
                        ctype2 = header.get("CTYPE2", "").upper()

                        # Check for helioprojective coordinates
                        if (
                            "HPLN" in ctype1
                            or "HPLN" in ctype2
                            or "HPLT" in ctype1
                            or "HPLT" in ctype2
                        ):
                            # Convert HPC to RA/Dec first
                            from ..coordinates.helioprojective import convert_hpc_to_radec

                            temp_radec_file = os.path.join(
                                tempfile.gettempdir(),
                                f"solarviewer_radec_export_{os.getpid()}.fits",
                            )

                            with busy():
                                success = convert_hpc_to_radec(
                                    source_image, temp_radec_file, overwrite=True
                                )

                            if not success:
                                QMessageBox.warning(
                                    self,
                                    "Conversion Failed",
                                    "Failed to convert helioprojective coordinates to RA/Dec.",
                                )
                                return

                            fits_to_import = temp_radec_file
                            is_hpc_converted = True

                        # Check for other unsupported coordinate systems
                        elif not (
                            "RA" in ctype1 or "DEC" in ctype2 or "GLON" not in ctype1
                        ):
                            # Block galactic, ecliptic, and other non-celestial coordinates
                            unsupported_types = []
                            if "GLON" in ctype1 or "GLAT" in ctype2:
                                unsupported_types.append("Galactic")
                            elif "ELON" in ctype1 or "ELAT" in ctype2:
                                unsupported_types.append("Ecliptic")
                            elif "SLON" in ctype1 or "SLAT" in ctype2:
                                unsupported_types.append("Supergalactic")
                            else:
                                unsupported_types.append(f"{ctype1}/{ctype2}")

                            if unsupported_types:
                                QMessageBox.warning(
                                    self,
                                    "Unsupported Coordinates",
                                    f"This FITS file uses {', '.join(unsupported_types)} coordinates.\n\n"
                                    "Only RA/Dec (celestial) and helioprojective (Solar-X/Y) "
                                    "coordinate systems are supported for CASA export.",
                                )
                                return
                except Exception:
                    pass  # If we can't check, proceed anyway

                # Run importfits in a separate process
                try:
                    with busy():
                        run_casa_task(
                            "importfits",
                            fitsimage=os.path.abspath(fits_to_import),
                            imagename=os.path.abspath(output_image),
                            overwrite=True,
                        )
                finally:
                    # Clean up temp file if we created one
                    if is_hpc_converted and os.path.exists(fits_to_import):
                        try:
                            os.remove(fits_to_import)
                        except OSError:
                            pass

                if is_hpc_converted:
                    QMessageBox.information(
                        self,
                        "Exported",
                        f"CASA image exported to:\n{output_image}\n\n"
                        "Note: Helioprojective coordinates were converted to RA/Dec.",
                    )
                else:
                    QMessageBox.information(
                        self,
                        "Exported",
                        f"FITS converted to CASA image: {output_image}",
                    )
                return

            QMessageBox.warning(
                self, "Unsupported", "Source must be a FITS file or CASA image"
            )

        except Exception as e:
            _export_failed(self, "the CASA image", e)

    def save_tb_map_as_fits(self):
        """Save brightness temperature map as FITS file"""
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or current_tab.current_image_data is None:
            QMessageBox.warning(self, "No Data", "No image data loaded")
            return

        if not hasattr(current_tab, "imagename") or not current_tab.imagename:
            QMessageBox.warning(self, "No Image", "No image file loaded")
            return

        # Check if TB conversion is available (units must be Jy/beam)
        bunit = getattr(current_tab, "_current_bunit", "").lower()
        if "k" in bunit:
            # Already in temperature units
            QMessageBox.information(
                self,
                "Already TB",
                "Current image is already in temperature units (K).\n"
                "Use 'Export Data as FITS' to save it.",
            )
            return

        if not ("jy" in bunit and "beam" in bunit):
            QMessageBox.warning(
                self,
                "Invalid Units",
                f"TB conversion requires Jy/beam units.\n"
                f"Current units: {bunit or 'unknown'}",
            )
            return

        try:
            from ..processing.maps import generate_tb_map

            # Get save path
            default_name = os.path.basename(current_tab.imagename)
            if is_fits(default_name):
                default_name = default_name.rsplit(".", 1)[0] + "_TB.fits"
            else:
                default_name = default_name + "_TB.fits"

            path, _ = QFileDialog.getSaveFileName(
                self,
                "Save TB Map as FITS",
                default_name,
                "FITS Files (*.fits);;All Files (*)",
            )

            if not path:
                return

            begin_busy()

            # Generate and save TB map
            tb_data, result = generate_tb_map(
                current_tab.imagename,
                outfile=path,
                flux_data=current_tab.current_image_data,
            )

            end_busy()

            if tb_data is None:
                QMessageBox.warning(self, "Error", f"TB conversion failed: {result}")
            else:
                QMessageBox.information(self, "Saved", f"TB map saved to:\n{path}")

        except Exception as e:
            end_busy()
            _export_failed(self, "the brightness temperature map", e)

    def save_sub_image(self):
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or not current_tab.current_roi:
            QMessageBox.warning(self, "No ROI", "Please select a region first")
            return

        if not current_tab.imagename:
            QMessageBox.warning(self, "No Image", "No image loaded")
            return

        output_dir, _ = QFileDialog.getSaveFileName(
            self, "Save Subimage As", "", "CASA Image (*);;All Files (*)"
        )

        if output_dir:
            try:
                ia_tool = IA()
                ia_tool.open(readable_image_path(current_tab.imagename))

                if isinstance(current_tab.current_roi, tuple):
                    # In the file's own pixels (Fast mode shows fewer)
                    xlow, xhigh, ylow, yhigh = current_tab.roi_at_full_resolution()
                    region_dict = (
                        "box[["
                        + str(xlow)
                        + "pix, "
                        + str(ylow)
                        + "pix],["
                        + str(xhigh)
                        + "pix, "
                        + str(yhigh)
                        + "pix]]"
                    )

                    with busy():
                        ia_tool.subimage(outfile=output_dir, region=region_dict)
                else:
                    QMessageBox.information(
                        self,
                        "Not Implemented",
                        "Subimage for polygon/circle ROI not implemented yet",
                    )
                    return

                ia_tool.close()
                QMessageBox.information(
                    self, "Success", f"Subimage saved to {output_dir}"
                )
            except Exception as e:
                _export_failed(self, "the sub-image", e)

    def export_casa_region(self):
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or not current_tab.current_roi:
            QMessageBox.warning(self, "No ROI", "Please select a region first")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Region", "", "CASA Region (*.crtf);;All Files (*)"
        )

        if path:
            try:
                with open(path, "w") as f:
                    f.write("#CRTFv0\n")

                if isinstance(current_tab.current_roi, tuple):
                    # In the file's own pixels (Fast mode shows fewer)
                    xlow, xhigh, ylow, yhigh = current_tab.roi_at_full_resolution()
                    with open(path, "a") as f:
                        f.write(
                            f"box[[{xlow}pix, {ylow}pix], [{xhigh}pix, {yhigh}pix]]\n"
                        )
                else:
                    with open(path, "a") as f:
                        f.write("# Complex region - simplified representation\n")
                        f.write("circle[[512pix, 512pix], 100pix]\n")

                QMessageBox.information(self, "Success", f"Region saved to {path}")
            except Exception as e:
                _export_failed(self, "the region", e)

    def export_as_hpc_fits(self):
        """Export the current image as a helioprojective FITS file"""
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or not current_tab.imagename:
            QMessageBox.warning(self, "No Image", "No image loaded to export")
            return

        # Check if the image is already in helioprojective coordinates
        if current_tab._is_already_hpc():
            QMessageBox.information(
                self,
                "Already in Helioprojective",
                "This image is already in helioprojective (Solar-X/Y) coordinates.\n\n"
                "No conversion is needed. You can save it directly using File → Save As FITS.",
            )
            return

        try:
            # Get the output filename from user
            path, _ = QFileDialog.getSaveFileName(
                self,
                "Export as Helioprojective FITS",
                "",
                "FITS Files (*.fits);;All Files (*)",
            )

            if path:
                # Get current Stokes parameter and threshold
                stokes = (
                    current_tab.stokes_combo.currentText()
                    if current_tab.stokes_combo
                    else "I"
                )
                try:
                    threshold = float(current_tab.threshold_entry.text())
                except (ValueError, AttributeError):
                    threshold = 10.0

                # Show progress in status bar
                self.statusBar().showMessage(
                    "Converting to helioprojective coordinates..."
                )
                QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)

                # Call convert_and_save_hpc
                from ..coordinates.helioprojective import convert_and_save_hpc

                with busy():
                    success = convert_and_save_hpc(
                        current_tab.imagename,
                        path,
                        Stokes=stokes,
                        thres=threshold,
                        overwrite=True,
                    )

                if success:
                    QMessageBox.information(
                        self,
                        "Success",
                        f"Image exported as helioprojective FITS to:\n{path}",
                    )
                    self.statusBar().showMessage(
                        f"Exported helioprojective FITS to {path}"
                    )
                else:
                    QMessageBox.critical(
                        self,
                        "Export Failed",
                        "Failed to export image as helioprojective FITS",
                    )
                    self.statusBar().showMessage("Export failed")

        except Exception as e:
            _export_failed(self, "the helioprojective FITS file", e)
            self.statusBar().showMessage("Export error")

"""
Main-window export actions (FITS, CASA image, sub-image, regions, HPC).
"""

import os
import shutil
import tempfile
import threading

from PyQt5.QtWidgets import QFileDialog, QMessageBox

from ..processing.casa_tasks import (
    HPC_MODULE,
    CasaTask,
    write_sub_image,
    write_tb_map_from,
)
from ..processing.fits_io import is_fits
from ..ui.background import Cancelled, run_in_background, run_in_thread
from ..ui.busy import busy
from ..ui.errors import show_error

COPY_CHUNK = 1024 * 1024


class _Stopped(Exception):
    """A copy on a thread was cancelled."""


def _name(path):
    return os.path.basename(path.rstrip("/"))


def _partial(path):
    """Where an export of ``path`` is written until it is complete: in a new
    folder beside it, so the name is nobody else's and moving it into place
    is a rename. A cancelled or failed export leaves what was at ``path``
    alone, and :func:`_discard` removes the folder."""
    folder = tempfile.mkdtemp(
        prefix=".solarviewer_export_", dir=os.path.dirname(os.path.abspath(path))
    )
    return os.path.join(folder, _name(path))


def _discard(partial):
    """Remove the folder of ``partial`` and what is left in it."""
    shutil.rmtree(os.path.dirname(partial), ignore_errors=True)


def _remove(path):
    """Delete the file or CASA image ``path``, if it is there."""
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, ignore_errors=True)
    elif os.path.lexists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def _put_in_place(parent, partial, path):
    """The finished export ``partial`` takes the place of what is at ``path``.

    Renames only, on the GUI thread once the wait is over, so that no Cancel
    can land between them. An old CASA image is deleted after that, which on
    a slow disk takes a while."""
    if not (os.path.isdir(partial) or os.path.isdir(path)):
        os.replace(partial, path)
        return
    old = None
    if os.path.lexists(path):
        old = _partial(path)
        os.replace(path, old)
    try:
        os.replace(partial, path)
    except OSError:
        if old is not None:
            os.replace(old, path)
            _discard(old)
        raise
    if old is not None:
        try:
            run_in_thread(parent, f"Removing the old {_name(path)}…", _discard, old)
        except Cancelled:
            pass  # the export is done; the old image goes on being deleted


def _copy_file(source, partial, stop):
    """As shutil.copy2, stopping between chunks once ``stop`` is set."""
    with open(source, "rb") as reader, open(partial, "wb") as writer:
        while chunk := reader.read(COPY_CHUNK):
            if stop.is_set():
                raise _Stopped()
            writer.write(chunk)
    shutil.copystat(source, partial)


def _copy_image(source, partial, stop):
    """Copy the CASA image ``source``, stopping between files once ``stop``
    is set."""

    def copy(src, dst):
        if stop.is_set():
            raise _Stopped()
        shutil.copy2(src, dst)

    shutil.copytree(source, partial, copy_function=copy)


def _write_fits(hdul, partial, _stop):
    """Write ``hdul``; astropy has no way to stop half-way."""
    hdul.writeto(partial)


def _stoppable(parent, label, path, job, *args):
    """``job(*args, partial, stop)`` writes the export of ``path`` on a thread,
    then it goes in place, here. Cancel sets ``stop`` for it to end early. It
    may run on for a while with nobody waiting, so it then removes what it
    wrote, unless it was done by the time Cancel came."""
    partial = _partial(path)
    stop = threading.Event()
    ended = threading.Event()

    def run():
        try:
            job(*args, partial, stop)
        finally:
            ended.set()
            if stop.is_set():
                _discard(partial)

    still_writing = False
    try:
        run_in_thread(parent, label, run)
        _put_in_place(parent, partial, path)
    except Cancelled:
        stop.set()
        still_writing = not ended.is_set()
        raise
    finally:
        if not still_writing:
            _discard(partial)


def wait_for_task(parent, label, job):
    """Run the :class:`CasaTask` ``job``, waited for on a thread; returns its
    result. Cancel ends its process."""
    try:
        return run_in_thread(parent, label, job.run)
    except BaseException:
        job.stop()
        raise


def _casa_export(parent, label, task, output_arg, output, **kwargs):
    """The CASA ``task`` writes ``output``, named by its argument ``output_arg``."""
    partial = _partial(output)
    kwargs[output_arg] = partial
    try:
        wait_for_task(parent, label, CasaTask(task, None, kwargs))
        _put_in_place(parent, partial, output)
    finally:
        _discard(partial)


def _reproject(parent, label, task, source, output, **kwargs):
    """The function ``task`` of coordinates.helioprojective writes ``output``.
    Returns whether it did."""
    kwargs.update(
        input_fits_file=os.path.abspath(source),
        output_fits_file=os.path.abspath(output),
        overwrite=True,
    )
    return wait_for_task(parent, label, CasaTask(task, None, kwargs, HPC_MODULE))


def _crtf_box(xlow, xhigh, ylow, yhigh):
    """The CRTF box of the ROI (xlow, xhigh, ylow, yhigh): CASA's box
    includes its top corner, the ROI's high ends are past its last pixel."""
    return f"box[[{xlow}pix, {ylow}pix], [{xhigh - 1}pix, {yhigh - 1}pix]]"


def _crtf_line(tab):
    """The CRTF line of ``tab``'s region, in the file's own pixels."""
    region = tab.selected_region
    if region is None or (region.shape == "rect" and not region.angle):
        return _crtf_box(*tab.roi_at_full_resolution())
    factor = getattr(tab, "_downsample", 1) or 1  # (Fast mode shows fewer)
    if region.shape == "rect":
        # Not rotbox: its sense of turning changes with the sign of CDELT1
        points = [(x * factor, y * factor) for x, y in region.outline()]
        return "poly[" + ", ".join(f"[{x:.3f}pix, {y:.3f}pix]" for x, y in points) + "]"
    x1, y1, x2, y2 = (v * factor for v in region.corners)
    # An ellipse's angle runs from the y axis, a region's from the x axis
    return (
        f"ellipse[[{(x1 + x2) / 2:.3f}pix, {(y1 + y2) / 2:.3f}pix], "
        f"[{(x2 - x1) / 2:.3f}pix, {(y2 - y1) / 2:.3f}pix], {region.angle - 90:.3f}deg]"
    )


def _export_failed(parent, what, error):
    """Say that saving ``what`` failed (called while handling ``error``)."""
    import traceback

    show_error(
        parent,
        "Export failed",
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
        if not current_tab or current_tab.current_image_data is None:
            QMessageBox.warning(self, "No data", "No image to export")
            return

        path, chosen = QFileDialog.getSaveFileName(
            self,
            "Export figure",
            "",
            "PNG files (*.png);;PDF files (*.pdf);;All files (*)",
        )
        if path:
            # (savefig would add .png itself, whatever the type chosen)
            if not os.path.splitext(path)[1]:
                path += ".pdf" if "*.pdf" in chosen else ".png"
            try:
                # The figure is the window's: it cannot be drawn on another thread
                with busy(f"Saving {_name(path)}…", self):
                    current_tab.figure.savefig(path, dpi=300, bbox_inches="tight")
            except Exception as e:
                _export_failed(self, "the figure", e)
                return
            QMessageBox.information(self, "Exported", f"Figure saved to {path}")

    def export_as_fits(self):
        """Export current image data as FITS file with all metadata preserved."""
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or current_tab.current_image_data is None:
            QMessageBox.warning(self, "No data", "No image data to export")
            return

        try:
            import os

            from astropy.io import fits

            path, _ = QFileDialog.getSaveFileName(
                self, "Export as FITS", "", "FITS files (*.fits);;All files (*)"
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
                    _casa_export(
                        self,
                        f"Exporting {_name(source_image)} to FITS…",
                        "exportfits",
                        "fitsimage",
                        path,
                        imagename=os.path.abspath(source_image),
                        overwrite=True,
                        stokeslast=True,
                    )

                    QMessageBox.information(
                        self, "Exported", f"CASA image exported to {path}"
                    )
                    return
                except Cancelled:
                    raise
                except Exception as e:
                    _export_failed(self, "the CASA image", e)
                    return

            # For FITS files, copy the original with all data and metadata
            if source_image and (
                is_fits(source_image)
            ):
                try:
                    # If saving to a different location, copy the original file
                    if os.path.abspath(source_image) != os.path.abspath(path):
                        _stoppable(
                            self,
                            f"Copying {_name(source_image)}…",
                            path,
                            _copy_file,
                            source_image,
                        )
                        QMessageBox.information(
                            self, "Exported", f"FITS file exported to {path}"
                        )
                    else:
                        QMessageBox.warning(
                            self,
                            "Same file",
                            "Source and destination are the same file.",
                        )
                    return
                except Cancelled:
                    raise
                except Exception:
                    # Fall back to creating from current data if copy fails
                    pass

            # Fallback: create FITS from current_image_data with header
            if (getattr(current_tab, "_downsample", 1) or 1) > 1:
                # The header describes the file's pixels, not a preview's
                raise RuntimeError("the image shown is a Fast load preview")
            data = current_tab.current_image_data.T  # FITS is [y, x]

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
                    hdu = fits.PrimaryHDU(data, header=header)
                else:
                    # Already a FITS header
                    hdu = fits.PrimaryHDU(data, header=original_header)
            else:
                # No header available, create basic HDU
                hdu = fits.PrimaryHDU(data)

            # Add HISTORY entry
            hdu.header.add_history("Exported with SolarViewer")

            hdul = fits.HDUList([hdu])
            _stoppable(self, f"Writing {_name(path)}…", path, _write_fits, hdul)
            QMessageBox.information(self, "Exported", f"Data saved to {path}")
        except Cancelled:
            self.statusBar().showMessage("Export cancelled")
        except ImportError:
            QMessageBox.warning(
                self,
                "Missing dependency",
                "Astropy is required for FITS export. Please install it.",
            )
        except Exception as e:
            _export_failed(self, "the FITS file", e)

    def export_as_casa_image(self):
        """Export/convert current image to CASA image format."""
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or current_tab.current_image_data is None:
            QMessageBox.warning(self, "No data", "No image data to export")
            return

        try:
            # Get source image path
            if hasattr(current_tab, "imagename") and current_tab.imagename:
                source_image = current_tab.imagename
            else:
                QMessageBox.warning(self, "No source", "No source image file available")
                return

            # Suggest a default output name based on source
            default_name = os.path.splitext(_name(source_image))[0] + ".image"

            # Ask user for output path and name
            output_image, _ = QFileDialog.getSaveFileName(
                self,
                "Export as CASA Image",
                default_name,
                "CASA image (*.image);;All files (*)",
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

            # If source is already a CASA image, copy it
            if os.path.isdir(source_image):
                _stoppable(
                    self,
                    f"Copying {_name(source_image)}…",
                    output_image,
                    _copy_image,
                    source_image,
                )
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
                                    "Unsupported coordinates",
                                    f"This FITS file uses {', '.join(unsupported_types)} coordinates.\n\n"
                                    "Only RA/Dec (celestial) and helioprojective (Solar-X/Y) "
                                    "coordinate systems are supported for CASA export.",
                                )
                                return
                except Exception:
                    pass  # If we can't check, proceed anyway

                name = _name(source_image)
                import_label = f"Importing {name} as a CASA image…"
                try:
                    if is_hpc_converted:
                        fits_to_import = os.path.join(
                            tempfile.gettempdir(),
                            f"solarviewer_radec_export_{os.getpid()}.fits",
                        )
                        success = _reproject(
                            self,
                            f"Reprojecting {name} to RA/Dec (step 1 of 2)…",
                            "convert_hpc_to_radec",
                            source_image,
                            fits_to_import,
                        )
                        if not success:
                            QMessageBox.warning(
                                self,
                                "Conversion failed",
                                "Failed to convert helioprojective coordinates to RA/Dec.",
                            )
                            return
                        import_label = f"Importing {name} as a CASA image (step 2 of 2)…"

                    # Run importfits in a separate process
                    _casa_export(
                        self,
                        import_label,
                        "importfits",
                        "imagename",
                        output_image,
                        fitsimage=os.path.abspath(fits_to_import),
                        overwrite=True,
                    )
                finally:
                    # Clean up temp file if we created one
                    if is_hpc_converted:
                        _remove(fits_to_import)

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

        except Cancelled:
            self.statusBar().showMessage("Export cancelled")
        except Exception as e:
            _export_failed(self, "the CASA image", e)

    def save_tb_map_as_fits(self):
        """Save brightness temperature map as FITS file"""
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or current_tab.current_image_data is None:
            QMessageBox.warning(self, "No data", "No image data loaded")
            return

        if not hasattr(current_tab, "imagename") or not current_tab.imagename:
            QMessageBox.warning(self, "No image", "No image file loaded")
            return

        # Check if TB conversion is available (units must be Jy/beam)
        bunit = getattr(current_tab, "_current_bunit", "").lower()
        if "k" in bunit:
            # Already in temperature units
            QMessageBox.information(
                self,
                "Already TB",
                "Current image is already in temperature units (K).\n"
                "Use File → Export image as → FITS… to save it.",
            )
            return

        if not ("jy" in bunit and "beam" in bunit):
            QMessageBox.warning(
                self,
                "Invalid units",
                f"TB conversion requires Jy/beam units.\n"
                f"Current units: {bunit or 'unknown'}",
            )
            return

        try:
            # Get save path
            default_name = _name(current_tab.imagename)
            if is_fits(default_name):
                default_name = default_name.rsplit(".", 1)[0] + "_TB.fits"
            else:
                default_name = default_name + "_TB.fits"

            path, _ = QFileDialog.getSaveFileName(
                self,
                "Save TB Map as FITS",
                default_name,
                "FITS files (*.fits);;All files (*)",
            )

            if not path:
                return

            # Generate and save TB map
            partial = _partial(path)
            try:
                error = run_in_background(
                    self,
                    f"Writing {_name(path)}…",
                    write_tb_map_from,
                    current_tab.imagename,
                    partial,
                    current_tab.current_image_data,
                )
                if error is None:
                    _put_in_place(self, partial, path)
            finally:
                _discard(partial)  # and the map's own temporary file

            if error is not None:
                show_error(
                    self,
                    "Brightness temperature conversion failed",
                    "The map was not saved. Check that the image has a beam and try again.",
                    str(error),
                )
            else:
                QMessageBox.information(self, "Saved", f"TB map saved to:\n{path}")

        except Cancelled:
            self.statusBar().showMessage("Export cancelled")
        except Exception as e:
            _export_failed(self, "the brightness temperature map", e)

    def save_sub_image(self):
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or not current_tab.current_roi:
            QMessageBox.warning(self, "No ROI", "Please select a region first")
            return

        if not current_tab.imagename:
            QMessageBox.warning(self, "No image", "No image loaded")
            return

        output_dir, _ = QFileDialog.getSaveFileName(
            self, "Save sub-image as", "", "CASA image (*);;All files (*)"
        )

        if output_dir:
            if not isinstance(current_tab.current_roi, tuple):
                QMessageBox.information(
                    self,
                    "Not implemented",
                    "Subimage for polygon/circle ROI not implemented yet",
                )
                return

            existed = os.path.exists(output_dir)
            try:
                # In the file's own pixels (Fast mode shows fewer)
                xlow, xhigh, ylow, yhigh = current_tab.roi_at_full_resolution()
                region_dict = _crtf_box(xlow, xhigh, ylow, yhigh)

                run_in_background(
                    self,
                    f"Writing {_name(output_dir)}…",
                    write_sub_image,
                    current_tab.imagename,
                    output_dir,
                    region_dict,
                )
                QMessageBox.information(
                    self, "Success", f"Subimage saved to {output_dir}"
                )
            except Cancelled:
                if not existed:
                    _remove(output_dir)  # what the stopped job had written
                self.statusBar().showMessage("Export cancelled")
            except Exception as e:
                _export_failed(self, "the sub-image", e)

    def export_casa_region(self):
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or not current_tab.current_roi:
            QMessageBox.warning(self, "No ROI", "Please select a region first")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Export region", "", "CASA region (*.crtf);;All files (*)"
        )

        if path:
            try:
                with open(path, "w") as f:
                    f.write("#CRTFv0\n")
                    f.write(_crtf_line(current_tab) + "\n")

                QMessageBox.information(self, "Success", f"Region saved to {path}")
            except Exception as e:
                _export_failed(self, "the region", e)

    def export_as_hpc_fits(self):
        """Export the current image as a helioprojective FITS file"""
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or not current_tab.imagename:
            QMessageBox.warning(self, "No image", "No image loaded to export")
            return

        # Check if the image is already in helioprojective coordinates
        if current_tab._is_already_hpc():
            QMessageBox.information(
                self,
                "Already in helioprojective",
                "This image is already in helioprojective (Solar-X/Y) coordinates.\n\n"
                "No conversion is needed. You can save it directly using File → Export image as → FITS…",
            )
            return

        try:
            # Get the output filename from user
            path, _ = QFileDialog.getSaveFileName(
                self,
                "Export as helioprojective FITS",
                "",
                "FITS files (*.fits);;All files (*)",
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

                partial = _partial(path)
                try:
                    success = _reproject(
                        self,
                        f"Reprojecting {_name(current_tab.imagename)} to helioprojective coordinates…",
                        "convert_and_save_hpc",
                        current_tab.imagename,
                        partial,
                        Stokes=stokes,
                        thres=threshold,
                        # (a ratio, as it is on screen)
                        sun_radius_arcmin=current_tab.sun_limit(),
                    )
                    if success:
                        _put_in_place(self, partial, path)
                finally:
                    _discard(partial)

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
                        "Export failed",
                        "Failed to export image as helioprojective FITS",
                    )
                    self.statusBar().showMessage("Export failed")

        except Cancelled:
            self.statusBar().showMessage("Export cancelled")
        except Exception as e:
            _export_failed(self, "the helioprojective FITS file", e)
            self.statusBar().showMessage("Export error")

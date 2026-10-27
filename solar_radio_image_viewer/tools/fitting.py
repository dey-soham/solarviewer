"""
2D Gaussian fitting tool for SolarViewer.
"""

import logging
import numpy as np
from PyQt5.QtCore import QEventLoop
from PyQt5.QtWidgets import QApplication, QMessageBox
from ..ui.errors import show_error

from ..processing.fits_io import readable_image_path
from ..ui.busy import begin_busy, end_busy

logger = logging.getLogger(__name__)


def imfit_box(roi, shape=None):
    """CASA ``box`` string ("blcx,blcy,trcx,trcy") for an ROI (xlow, xhigh,
    ylow, yhigh) of an image indexed [x, y] with ``shape`` (nx, ny).

    The highs are exclusive; the box keeps one pixel clear of the ROI's edge.
    Raises ValueError if nothing is left inside.
    """
    xlow, xhigh, ylow, yhigh = roi
    # Shrink by 1 pixel safety margin (exclusive -> inclusive -> shrink)
    xlow, ylow = xlow + 1, ylow + 1
    xhigh, yhigh = xhigh - 2, yhigh - 2
    # Clamp to image dimensions to prevent out-of-bounds errors
    if shape is not None:
        nx, ny = shape
        xlow, xhigh = (max(0, min(v, nx - 1)) for v in (xlow, xhigh))
        ylow, yhigh = (max(0, min(v, ny - 1)) for v in (ylow, yhigh))
    if xhigh <= xlow:
        raise ValueError("xhigh <= xlow")
    if yhigh <= ylow:
        raise ValueError("yhigh <= ylow")
    return f"{xlow},{ylow},{xhigh},{yhigh}"


class FittingMixin:
    """2D Gaussian fitting on the current tab's ROI.

    Mixed into :class:`SolarRadioImageViewerApp`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``statusBar``, ``tab_widget``
    """

    def fit_2d_gaussian(self):
        """Fit a 2D Gaussian to the current image using CASA's imfit task."""
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or not current_tab.imagename:
            QMessageBox.warning(self, "No Image", "Please load an image first.")
            return

        # A region selected (and on the image) to fit in
        has_visible_roi = current_tab.selected_region is not None and bool(current_tab.current_roi)

        if not has_visible_roi:
            QMessageBox.warning(
                self,
                "No ROI",
                "Please select a region of interest (ROI) first.\n\nUse the ROI tool to draw a box around the source.",
            )
            return

        imagename = current_tab.imagename

        # CASA imfit fits only the planes I, Q, U and V: a derived product
        # (L, Lfrac, ...) on screen is fitted as Stokes I, and the user told so
        combo = getattr(current_tab, "stokes_combo", None)
        shown = combo.currentText() if combo is not None else "I"
        stokes = shown if shown in ("I", "Q", "U", "V") else "I"
        note = ""
        if shown != stokes:
            note = (
                f"The image shows {shown}, which cannot be fitted: "
                "the fit uses Stokes I.\n\n"
            )

        # Ask user about zero-level offset
        reply = QMessageBox.question(
            self,
            "Fitting Options",
            f"{note}Fit zero-level offset (background)?\n\n"
            "Yes: Fit a constant background level\n"
            "No: Assume zero background",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        dooff = reply == QMessageBox.Yes

        try:
            import json
            import os
            import subprocess
            import sys
            import tempfile

            # Box format: 'x1,y1,x2,y2' (pixel coordinates)
            box = ""
            if current_tab.current_roi and isinstance(current_tab.current_roi, tuple):
                try:
                    # CASA fits the file itself: Fast mode shows fewer pixels
                    box = imfit_box(
                        current_tab.roi_at_full_resolution(),
                        getattr(current_tab, "_full_image_shape", None),
                    )
                except ValueError as e:
                    QMessageBox.warning(self, "Invalid ROI", f"Invalid ROI: {e}")
                    return

            # Create temp file for results
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as f:
                result_file = f.name

            # Show wait cursor during fitting
            begin_busy()
            self.statusBar().showMessage("Fitting Gaussian ... Please wait")
            QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)

            # Run imfit in a subprocess to avoid casatasks/PyQt5 conflicts
            env = os.environ.copy()
            # Clear Qt plugin paths to avoid conflicts
            for key in ["QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"]:
                if key in env:
                    del env[key]

            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "solar_radio_image_viewer.processing.imfit",
                    os.path.abspath(readable_image_path(imagename)),
                    box,
                    os.path.abspath(result_file),
                    str(dooff),
                    stokes,
                ],
                capture_output=True,
                text=True,
                env=env,
                timeout=120,  # 2 minute timeout
                cwd=(
                    os.getcwd()
                    if os.access(os.getcwd(), os.W_OK)
                    else tempfile.gettempdir()
                ),
            )

            # Check if subprocess had errors
            if process.returncode != 0:
                end_busy()
                error_msg = process.stderr or process.stdout or "Unknown error"
                QMessageBox.warning(
                    self, "Fit Error", f"imfit subprocess failed:\n{error_msg}"
                )
                if os.path.exists(result_file):
                    os.remove(result_file)
                return

            # Read results from temp file
            if not os.path.exists(result_file):
                end_busy()
                QMessageBox.warning(self, "Fit Error", "No results from fitting")
                return

            with open(result_file, "r") as f:
                output = json.load(f)

            os.remove(result_file)

            if not output.get("success", False):
                end_busy()
                error = output.get("error", "Unknown error")
                tb = output.get("traceback", "")
                logger.error(f"Fitting failed: {error}\n{tb}")
                QMessageBox.warning(self, "Fit Error", f"Fitting failed: {error}")
                return

            results = output.get("results", {})
            # With a zero-level offset, imfit can return only the offset when it
            # fails to fit a component; don't report that as an all-zero source.
            if "major_arcsec" not in results:
                end_busy()
                QMessageBox.warning(
                    self,
                    "Fit Failed",
                    "No Gaussian component could be fitted in the selected region.\n\n"
                    "Try a region that is better centred on the source"
                    + (", or fit without the zero-level offset." if dooff else "."),
                )
                return

            # Helper function for smart unit formatting
            def format_smart_arcsec(value_arcsec):
                """Convert arcsec to appropriate unit (arcsec, arcmin, or deg)"""
                if abs(value_arcsec) >= 3600:
                    return f"{value_arcsec / 3600:.3f}°"
                elif abs(value_arcsec) >= 60:
                    return f"{value_arcsec / 60:.3f}'"
                else:
                    return f'{value_arcsec:.3f}"'

            # Extract results
            ra_rad = results.get("ra_rad", 0)
            dec_rad = results.get("dec_rad", 0)
            ra_deg = np.degrees(ra_rad)
            dec_deg = np.degrees(dec_rad)
            # Errors already in arcsec from subprocess
            ra_err_arcsec = results.get("ra_err_arcsec")
            dec_err_arcsec = results.get("dec_err_arcsec")

            major_arcsec = results.get("major_arcsec", 0)
            minor_arcsec = results.get("minor_arcsec", 0)
            pa_deg = results.get("pa_deg", 0)

            flux = results.get("flux", 0)
            flux_unit = results.get("flux_unit", "Jy")
            flux_err = results.get("flux_err")
            peak_flux = results.get("peak_flux")
            peak_unit = results.get("peak_unit", flux_unit)
            peak_err = results.get("peak_err")

            # Errors for shape
            major_err = results.get("major_err")
            minor_err = results.get("minor_err")
            pa_err = results.get("pa_err")

            deconv_major = results.get("deconv_major", 0)
            deconv_minor = results.get("deconv_minor", 0)
            deconv_pa = results.get("deconv_pa")
            deconv_major_err = results.get("deconv_major_err")
            deconv_minor_err = results.get("deconv_minor_err")
            deconv_pa_err = results.get("deconv_pa_err")

            offset = results.get("offset", 0)
            offset_unit = results.get("offset_unit", "")
            offset_err = results.get("offset_err")

            # Convert to HMS/DMS
            import astropy.units as u
            from astropy.coordinates import SkyCoord

            coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg)
            ra_hms = coord.ra.to_string(unit=u.hourangle, sep=":", precision=2)
            dec_dms = coord.dec.to_string(unit=u.deg, sep=":", precision=2)

            # Get pixel coordinates if available
            ra_pix = None
            dec_pix = None
            if current_tab.current_wcs:
                try:
                    # Try with just RA/Dec (2 axes) first, then 4 axes
                    try:
                        pix = current_tab.current_wcs.topixel([ra_rad, dec_rad])[
                            "numeric"
                        ]
                    except Exception:
                        # Use 4 axes but suppress warning by using valid stokes index 1
                        pix = current_tab.current_wcs.topixel([ra_rad, dec_rad, 1, 1])[
                            "numeric"
                        ]
                    ra_pix = pix[0]
                    dec_pix = pix[1]
                except Exception:
                    pass

            # Beam parameters
            beam_major = 0
            beam_minor = 0
            beam_pa = 0
            if current_tab.psf:
                try:
                    if isinstance(current_tab.psf["major"]["value"], list):
                        beam_major = float(current_tab.psf["major"]["value"][0])
                    else:
                        beam_major = float(current_tab.psf["major"]["value"])

                    if isinstance(current_tab.psf["minor"]["value"], list):
                        beam_minor = float(current_tab.psf["minor"]["value"][0])
                    else:
                        beam_minor = float(current_tab.psf["minor"]["value"])

                    if isinstance(current_tab.psf["positionangle"]["value"], list):
                        beam_pa = float(current_tab.psf["positionangle"]["value"][0])
                    else:
                        beam_pa = float(current_tab.psf["positionangle"]["value"])
                except Exception:
                    pass

            # Helper to format value with error
            def fmt_with_err(val_str, err, unit=""):
                if err is not None:
                    return f"{val_str} ± {err:.3f}{unit}"
                return val_str

            # Professional terminal output
            print("\n" + "=" * 70)
            print("           2D GAUSSIAN FIT RESULTS")
            print("=" * 70)
            print("-" * 70)
            print(f"  {'Parameter':<25} {'Value':>25} {'Error':>15}")
            print("-" * 70)

            # Position - show errors on deg rows with smart units
            print(f"  {'RA (HMS)':<25} {ra_hms:>25}")
            print(f"  {'Dec (DMS)':<25} {dec_dms:>25}")
            ra_err_str = (
                f"± {format_smart_arcsec(ra_err_arcsec)}" if ra_err_arcsec else ""
            )
            dec_err_str = (
                f"± {format_smart_arcsec(dec_err_arcsec)}" if dec_err_arcsec else ""
            )
            print(f"  {'RA (deg)':<25} {ra_deg:>25.6f} {ra_err_str:>15}")
            print(f"  {'Dec (deg)':<25} {dec_deg:>25.6f} {dec_err_str:>15}")
            if ra_pix is not None:
                print(f"  {'RA (pix)':<25} {ra_pix:>25.2f}")
                print(f"  {'Dec (pix)':<25} {dec_pix:>25.2f}")

            print("-" * 70)
            print("  IMAGE COMPONENT SIZE (convolved with beam):")
            major_str = format_smart_arcsec(major_arcsec)
            minor_str = format_smart_arcsec(minor_arcsec)
            major_err_str = f'± {major_err:.3f}"' if major_err else ""
            minor_err_str = f'± {minor_err:.3f}"' if minor_err else ""
            pa_err_str = f"± {pa_err:.2f}" if pa_err else ""
            print(f"  {'Major axis FWHM':<25} {major_str:>25} {major_err_str:>15}")
            print(f"  {'Minor axis FWHM':<25} {minor_str:>25} {minor_err_str:>15}")
            print(f"  {'Position Angle (deg)':<25} {pa_deg:>25.2f} {pa_err_str:>15}")

            if beam_major > 0:
                print("-" * 70)
                print("  CLEAN BEAM SIZE:")
                print(
                    f"  {'Major axis FWHM':<25} {format_smart_arcsec(beam_major):>25}"
                )
                print(
                    f"  {'Minor axis FWHM':<25} {format_smart_arcsec(beam_minor):>25}"
                )
                print(f"  {'Position Angle (deg)':<25} {beam_pa:>25.2f}")

            deconv_info = ""
            deconv_theta_info = ""
            if deconv_major > 0 or deconv_minor > 0:
                print("-" * 70)
                print("  IMAGE COMPONENT SIZE (deconvolved from beam):")
                if deconv_major > 0:
                    deconv_maj_str = format_smart_arcsec(deconv_major)
                    deconv_maj_err_str = (
                        f'± {deconv_major_err:.3f}"' if deconv_major_err else ""
                    )
                    print(
                        f"  {'Major axis FWHM':<25} {deconv_maj_str:>25} {deconv_maj_err_str:>15}"
                    )
                else:
                    print(f"  {'Major axis FWHM':<25} {'Unresolved':>25}")
                if deconv_minor > 0:
                    deconv_min_str = format_smart_arcsec(deconv_minor)
                    deconv_min_err_str = (
                        f'± {deconv_minor_err:.3f}"' if deconv_minor_err else ""
                    )
                    print(
                        f"  {'Minor axis FWHM':<25} {deconv_min_str:>25} {deconv_min_err_str:>15}"
                    )
                else:
                    print(f"  {'Minor axis FWHM':<25} {'Unresolved':>25}")
                if deconv_pa is not None:
                    deconv_pa_err_str = (
                        f"± {deconv_pa_err:.2f}" if deconv_pa_err else ""
                    )
                    print(
                        f"  {'Position Angle (deg)':<25} {deconv_pa:>25.2f} {deconv_pa_err_str:>15}"
                    )
                else:
                    print(f"  {'Position Angle (deg)':<25} {'N/A':>25}")

                # Build deconv info for GUI
                deconv_maj_str = (
                    format_smart_arcsec(deconv_major) if deconv_major > 0 else "Unres"
                )
                deconv_min_str = (
                    format_smart_arcsec(deconv_minor) if deconv_minor > 0 else "Unres"
                )
                deconv_info = f"\nDeconv: {deconv_maj_str} × {deconv_min_str}"
                if deconv_pa is not None:
                    deconv_theta_info = f"\nDeconv PA: {deconv_pa:.1f}°"

            print("-" * 70)
            print("  FLUX:")
            flux_str = f"{flux:.4g} {flux_unit}"
            flux_err_str = f"± {flux_err:.4g}" if flux_err else ""
            print(f"  {'Integrated':<25} {flux_str:>25} {flux_err_str:>15}")

            if peak_flux is not None:
                peak_str = f"{peak_flux:.4g} {peak_unit}"
                peak_err_str = f"± {peak_err:.4g}" if peak_err else ""
                print(f"  {'Peak':<25} {peak_str:>25} {peak_err_str:>15}")

            if offset != 0 or offset_err:
                offset_str = f"{offset:.4g} {offset_unit}"
                offset_err_str = f"± {offset_err:.4g}" if offset_err else ""
                print(
                    f"  {'Zero-level offset':<25} {offset_str:>25} {offset_err_str:>15}"
                )

            print("=" * 70 + "\n")

            end_busy()

            # GUI message (compact version)
            msg = (
                f"2D Gaussian Fit:\n"
                f"Position: {ra_hms}, {dec_dms}\n"
                f"Size: {format_smart_arcsec(major_arcsec)} × {format_smart_arcsec(minor_arcsec)}\n"
                f"PA: {pa_deg:.1f}°\n"
                f"Flux: {flux:.4g} {flux_unit}"
                f"{deconv_info}"
                f"{deconv_theta_info}"
            )

            QMessageBox.information(self, "Fit Result", msg)
            self.statusBar().showMessage("Gaussian fit completed", 3000)

        except subprocess.TimeoutExpired:
            end_busy()
            QMessageBox.warning(
                self, "Timeout", "Gaussian fit took too long to complete"
            )
        except Exception as e:
            end_busy()
            import traceback

            traceback.print_exc()
            show_error(
                self,
                "Gaussian Fit Failed",
                f"The Gaussian fit failed: {e}\n\nTry a box drawn around a single, "
                "isolated source, a little larger than it.",
                traceback.format_exc(),
            )

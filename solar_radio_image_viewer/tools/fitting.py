"""
2D Gaussian / ring fitting tool for SolarViewer.
"""

import os
import sys
import tempfile

import numpy as np
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QMessageBox

from ..utils import twoD_elliptical_ring


class FittingMixin:
    """2D Gaussian and elliptical ring fitting on the current tab's ROI.

    Mixed into :class:`SolarRadioImageViewerApp`; relies on its attributes via ``self``.
    """

    def fit_2d_gaussian(self):
        """Fit a 2D Gaussian to the current image using CASA's imfit task."""
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or not current_tab.imagename:
            QMessageBox.warning(self, "No Image", "Please load an image first.")
            return

        # Require ROI selection - check if selector exists and is visible
        has_visible_roi = False
        if (
            current_tab.current_roi
            and isinstance(current_tab.current_roi, tuple)
            and current_tab.roi_selector
        ):

            # Check if selector's artist is visible
            # Both RectangleSelector and EllipseSelector have 'to_draw' artist
            if hasattr(current_tab.roi_selector, "to_draw"):
                has_visible_roi = current_tab.roi_selector.to_draw.get_visible()
            # Fallback for older matplotlib versions or if to_draw is missing
            elif hasattr(current_tab.roi_selector, "artists"):
                has_visible_roi = any(
                    a.get_visible() for a in current_tab.roi_selector.artists
                )

        if not has_visible_roi:
            QMessageBox.warning(
                self,
                "No ROI",
                "Please select a region of interest (ROI) first.\n\nUse the ROI tool to draw a box around the source.",
            )
            return

        imagename = current_tab.imagename

        # Get current Stokes from left panel (may be in tab or main app)
        # CASA imfit only accepts standard Stokes: I, Q, U, V
        stokes = "I"
        if hasattr(self, "stokes_combo") and self.stokes_combo:
            selected_stokes = self.stokes_combo.currentText()
            if selected_stokes in ["I", "Q", "U", "V"]:
                stokes = selected_stokes
            # For derived parameters (L, Lfrac, etc), fall back to I
        elif hasattr(current_tab, "stokes_combo") and current_tab.stokes_combo:
            selected_stokes = current_tab.stokes_combo.currentText()
            if selected_stokes in ["I", "Q", "U", "V"]:
                stokes = selected_stokes

        # Ask user about zero-level offset
        reply = QMessageBox.question(
            self,
            "Fitting Options",
            "Fit zero-level offset (background)?\n\n"
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
                xlow, xhigh, ylow, yhigh = current_tab.current_roi

                # Shrink by 1 pixel safety margin
                safe_xlow = xlow + 1
                safe_ylow = ylow + 1
                safe_xhigh = xhigh - 1 - 1  # Exclusive -> Inclusive (-1) -> Shrink (-1)
                safe_yhigh = yhigh - 1 - 1  # Exclusive -> Inclusive (-1) -> Shrink (-1)

                # Clamp to image dimensions to prevent out-of-bounds errors
                if current_tab.current_image_data is not None:
                    ny, nx = current_tab.current_image_data.shape
                    safe_xlow = max(0, min(safe_xlow, nx - 1))
                    safe_xhigh = max(0, min(safe_xhigh, nx - 1))
                    safe_ylow = max(0, min(safe_ylow, ny - 1))
                    safe_yhigh = max(0, min(safe_yhigh, ny - 1))

                # Ensure valid bounds (don't cross over or be invalid)
                if safe_xhigh <= safe_xlow:
                    QMessageBox.warning(
                        self, "Invalid ROI", "Invalid ROI: xhigh <= xlow"
                    )
                    return
                if safe_yhigh <= safe_ylow:
                    QMessageBox.warning(
                        self, "Invalid ROI", "Invalid ROI: yhigh <= ylow"
                    )
                    return

                box = f"{safe_ylow},{safe_xlow},{safe_yhigh},{safe_xhigh}"  # box uses col,row order (x,y)

            # Create temp file for results
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as f:
                result_file = f.name

            # Show wait cursor during fitting
            QApplication.setOverrideCursor(Qt.WaitCursor)
            self.statusBar().showMessage("Fitting Gaussian ... Please wait")
            QApplication.processEvents()

            # Run imfit in a subprocess to avoid casatasks/PyQt5 conflicts
            dooff_str = "True" if dooff else "False"
            script = f'''
import json
import numpy as np
from casatasks import imfit

imagename = "{imagename}"
box_param = "{box}"
result_file = "{result_file}"
dooff = {dooff_str}
stokes_param = "{stokes}"

def safe_get(d, keys, default=None):
    """Safely extract nested dictionary values."""
    try:
        result = d
        for key in keys:
            if isinstance(result, (list, np.ndarray)):
                result = result[int(key)]
            elif isinstance(result, dict):
                result = result[key]
            else:
                return default
        # Convert numpy types to Python types
        if isinstance(result, np.ndarray):
            result = result.tolist()
            if len(result) == 1:
                result = result[0]
        if hasattr(result, 'item'):
            result = result.item()
        return result
    except:
        return default

try:
    # Use box parameter (allows stokes) instead of region (doesn't allow stokes)
    fit_results = imfit(
        imagename=imagename,
        box=box_param,
        dooff=dooff,
        stokes=stokes_param,
    )
    
    
    # Extract the data we need (convert numpy types to Python types)
    output = {{"success": True, "results": {{}}}}
    
    if fit_results and "results" in fit_results:
        results = fit_results["results"]
        if "component0" in results:
            comp = results["component0"]
            
            # Position
            direction = comp.get("shape", {{}}).get("direction", {{}})
            output["results"]["ra_rad"] = float(safe_get(direction, ["m0", "value"], 0))
            output["results"]["dec_rad"] = float(safe_get(direction, ["m1", "value"], 0))
            # Position errors - CASA uses direction.error.longitude (RA) and direction.error.latitude (Dec)
            # CASA stores these in arcsec (check unit field to confirm)
            ra_err = safe_get(direction, ["error", "longitude", "value"])
            ra_err_unit = safe_get(direction, ["error", "longitude", "unit"], "arcsec")
            if ra_err is not None:
                # Store in arcsec - convert if needed based on unit
                if ra_err_unit == "rad":
                    output["results"]["ra_err_arcsec"] = float(ra_err) * 180 / 3.14159265359 * 3600
                else:  # assume arcsec
                    output["results"]["ra_err_arcsec"] = float(ra_err)
            
            dec_err = safe_get(direction, ["error", "latitude", "value"])
            dec_err_unit = safe_get(direction, ["error", "latitude", "unit"], "arcsec")
            if dec_err is not None:
                if dec_err_unit == "rad":
                    output["results"]["dec_err_arcsec"] = float(dec_err) * 180 / 3.14159265359 * 3600
                else:  # assume arcsec
                    output["results"]["dec_err_arcsec"] = float(dec_err)
            
            # Shape
            shape = comp.get("shape", {{}})
            output["results"]["major_arcsec"] = float(safe_get(shape, ["majoraxis", "value"], 0))
            output["results"]["minor_arcsec"] = float(safe_get(shape, ["minoraxis", "value"], 0))
            output["results"]["pa_deg"] = float(safe_get(shape, ["positionangle", "value"], 0))
            
            # Shape errors - CASA uses "majoraxiserror", "minoraxiserror", "positionangleerror"
            maj_err = safe_get(shape, ["majoraxiserror", "value"])
            if maj_err is not None:
                output["results"]["major_err"] = float(maj_err)
            
            min_err = safe_get(shape, ["minoraxiserror", "value"])
            if min_err is not None:
                output["results"]["minor_err"] = float(min_err)
                
            pa_err = safe_get(shape, ["positionangleerror", "value"])
            if pa_err is not None:
                output["results"]["pa_err"] = float(pa_err)
            
            # Flux - handle array values
            flux_val = safe_get(comp, ["flux", "value"])
            if flux_val is not None:
                if isinstance(flux_val, (list, tuple, np.ndarray)):
                    flux_val = float(flux_val[0]) if len(flux_val) > 0 else 0
                output["results"]["flux"] = float(flux_val)
            output["results"]["flux_unit"] = str(safe_get(comp, ["flux", "unit"], "Jy"))
            
            # Flux error - CASA stores as flux.error array
            flux_err = safe_get(comp, ["flux", "error"])
            if flux_err is not None:
                if isinstance(flux_err, (list, tuple, np.ndarray)):
                    flux_err = float(flux_err[0]) if len(flux_err) > 0 else None
                if flux_err is not None:
                    output["results"]["flux_err"] = float(flux_err)
            
            # Peak flux - CASA stores as peak.value (float) and peak.error (float)
            peak_val = safe_get(comp, ["peak", "value"])
            if peak_val is not None:
                output["results"]["peak_flux"] = float(peak_val)
                output["results"]["peak_unit"] = str(safe_get(comp, ["peak", "unit"], "Jy/beam"))
                # Peak error is a float
                peak_err = safe_get(comp, ["peak", "error"])
                if peak_err is not None:
                    output["results"]["peak_err"] = float(peak_err)
            
    # Deconvolved - uses same key structure as 'results'
    if fit_results and "deconvolved" in fit_results:
        deconv_comp = safe_get(fit_results, ["deconvolved", "component0", "shape"])
        if deconv_comp:
            output["results"]["deconv_major"] = float(safe_get(deconv_comp, ["majoraxis", "value"], 0))
            output["results"]["deconv_minor"] = float(safe_get(deconv_comp, ["minoraxis", "value"], 0))
            output["results"]["deconv_pa"] = float(safe_get(deconv_comp, ["positionangle", "value"], 0))
            # Deconvolved errors
            dmaj_err = safe_get(deconv_comp, ["majoraxiserror", "value"])
            if dmaj_err is not None:
                output["results"]["deconv_major_err"] = float(dmaj_err)
            
            dmin_err = safe_get(deconv_comp, ["minoraxiserror", "value"])
            if dmin_err is not None:
                output["results"]["deconv_minor_err"] = float(dmin_err)
            
            dpa_err = safe_get(deconv_comp, ["positionangleerror", "value"])
            if dpa_err is not None:
                output["results"]["deconv_pa_err"] = float(dpa_err)
    
    # Zero offset - CASA stores value in zerooff.value (ndarray) and error in zeroofferr.value (ndarray)
    zerooff = fit_results.get("zerooff", {{}}) if fit_results else {{}}
    if zerooff:
        offset_val = safe_get(zerooff, ["value"])
        if offset_val is not None:
            if isinstance(offset_val, (list, tuple, np.ndarray)):
                offset_val = float(offset_val[0]) if len(offset_val) > 0 else 0
            output["results"]["offset"] = float(offset_val)
        output["results"]["offset_unit"] = str(zerooff.get("unit", ""))
    
    # Zero offset error - stored in separate zeroofferr dict
    zeroofferr = fit_results.get("zeroofferr", {{}}) if fit_results else {{}}
    if zeroofferr:
        offset_err = safe_get(zeroofferr, ["value"])
        if offset_err is not None:
            if isinstance(offset_err, (list, tuple, np.ndarray)):
                offset_err = float(offset_err[0]) if len(offset_err) > 0 else None
            if offset_err is not None:
                output["results"]["offset_err"] = float(offset_err)
    
    with open(result_file, "w") as f:
        json.dump(output, f)
        
except Exception as e:
    import traceback
    with open(result_file, "w") as f:
        json.dump({{"success": False, "error": str(e), "traceback": traceback.format_exc()}}, f)
'''

            # Run the script in a subprocess
            env = os.environ.copy()
            # Clear Qt plugin paths to avoid conflicts
            for key in ["QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"]:
                if key in env:
                    del env[key]

            imagename_abs = os.path.abspath(imagename)
            result_file_abs = os.path.abspath(result_file)

            script = script.replace(
                f'imagename = "{imagename}"', f'imagename = "{imagename_abs}"'
            )
            script = script.replace(
                f'result_file = "{result_file}"', f'result_file = "{result_file_abs}"'
            )

            process = subprocess.run(
                [sys.executable, "-c", script],
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
                QApplication.restoreOverrideCursor()
                error_msg = process.stderr or process.stdout or "Unknown error"
                QMessageBox.warning(
                    self, "Fit Error", f"imfit subprocess failed:\n{error_msg}"
                )
                if os.path.exists(result_file):
                    os.remove(result_file)
                return

            # Read results from temp file
            if not os.path.exists(result_file):
                QApplication.restoreOverrideCursor()
                QMessageBox.warning(self, "Fit Error", "No results from fitting")
                return

            with open(result_file, "r") as f:
                output = json.load(f)

            os.remove(result_file)

            if not output.get("success", False):
                QApplication.restoreOverrideCursor()
                error = output.get("error", "Unknown error")
                tb = output.get("traceback", "")
                print(f"[ERROR] Fitting failed: {error}\n{tb}")
                QMessageBox.warning(self, "Fit Error", f"Fitting failed: {error}")
                return

            results = output.get("results", {})
            if not results:
                QApplication.restoreOverrideCursor()
                QMessageBox.warning(
                    self, "Fit Failed", "No components fitted in Gaussian fit"
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
                    except:
                        # Use 4 axes but suppress warning by using valid stokes index 1
                        pix = current_tab.current_wcs.topixel([ra_rad, dec_rad, 1, 1])[
                            "numeric"
                        ]
                    ra_pix = pix[0]
                    dec_pix = pix[1]
                except:
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
                except:
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

            QApplication.restoreOverrideCursor()

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
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(
                self, "Timeout", "Gaussian fit took too long to complete"
            )
        except Exception as e:
            QApplication.restoreOverrideCursor()
            import traceback

            traceback.print_exc()
            QMessageBox.warning(self, "Fit Error", f"Gaussian fit failed: {str(e)}")

    def fit_2d_ring(self):
        from scipy.optimize import curve_fit

        current_tab = self.tab_widget.currentWidget()
        if not current_tab or current_tab.current_image_data is None:
            QMessageBox.warning(self, "No Data", "Load data first.")
            return

        data = current_tab.current_image_data
        roi_offset = (0, 0)  # Offset for ROI coordinates

        # Check for visible ROI
        has_visible_roi = False
        if (
            current_tab.current_roi
            and isinstance(current_tab.current_roi, tuple)
            and current_tab.roi_selector
        ):
            if hasattr(current_tab.roi_selector, "to_draw"):
                has_visible_roi = current_tab.roi_selector.to_draw.get_visible()
            elif hasattr(current_tab.roi_selector, "artists"):
                has_visible_roi = any(
                    a.get_visible() for a in current_tab.roi_selector.artists
                )

        if not has_visible_roi:
            QMessageBox.warning(
                self,
                "No ROI",
                "Please select a region of interest (ROI) first.\n\nUse the ROI tool to draw a box around the source.",
            )
            return

        xlow, xhigh, ylow, yhigh = current_tab.current_roi
        data = data[xlow:xhigh, ylow:yhigh]
        roi_offset = (ylow, xlow)  # Store offset for coordinate conversion
        if data.size == 0:
            QMessageBox.warning(self, "Invalid ROI", "ROI contains no data")
            return

        ny, nx = data.shape
        x = np.arange(nx)
        y = np.arange(ny)
        xmesh, ymesh = np.meshgrid(x, y)
        coords = np.vstack((xmesh.ravel(), ymesh.ravel()))
        data_flat = data.ravel()

        guess = [np.nanmax(data), nx / 2, ny / 2, nx / 6, nx / 3, np.nanmedian(data)]

        try:
            popt, pcov = curve_fit(twoD_elliptical_ring, coords, data_flat, p0=guess)
            perr = np.sqrt(np.diag(pcov))

            # Get absolute pixel coordinates (accounting for ROI offset)
            x0_px = popt[1] + roi_offset[0]
            y0_px = popt[2] + roi_offset[1]
            inner_r_px = abs(popt[3])
            outer_r_px = abs(popt[4])

            # Try WCS conversion
            use_wcs = False
            wcs_info = ""
            if current_tab.current_wcs:
                try:
                    # Get pixel scale from WCS (radians -> arcsec)
                    increment = current_tab.current_wcs.increment()["numeric"][0:2]
                    # Check if image uses SOLAR-X/Y (increment is already in arcsec)
                    _hdr = getattr(current_tab, "_cached_fits_header", None) or {}
                    _is_solar = "SOLAR-X" in str(_hdr.get("CTYPE1", "")).upper()
                    if _is_solar:
                        scale_x = abs(increment[0])  # already arcsec/pixel
                        scale_y = abs(increment[1])
                    else:
                        scale_x = abs(increment[0]) * 180 / np.pi * 3600  # arcsec/pixel
                        scale_y = abs(increment[1]) * 180 / np.pi * 3600  # arcsec/pixel
                    avg_scale = (scale_x + scale_y) / 2

                    # Convert to world coordinates
                    world = current_tab.current_wcs.toworld([x0_px, y0_px, 0, 0])[
                        "numeric"
                    ]
                    if _is_solar:
                        ra_deg = world[0] / 3600.0 if world[0] else None
                        dec_deg = world[1] / 3600.0 if world[1] else None
                    else:
                        ra_deg = world[0] * 180 / np.pi if world[0] else None
                        dec_deg = world[1] * 180 / np.pi if world[1] else None

                    # Convert radii to arcsec
                    inner_r_arcsec = inner_r_px * avg_scale
                    outer_r_arcsec = outer_r_px * avg_scale
                    width_arcsec = outer_r_arcsec - inner_r_arcsec

                    use_wcs = True
                    wcs_info = f'WCS (scale: {avg_scale:.4f}"/px)'
                except Exception as wcs_err:
                    print(
                        f"[INFO] WCS conversion failed, using pixel coordinates: {wcs_err}"
                    )

            # Professional terminal output
            print("\n" + "=" * 60)
            print("           2D ELLIPTICAL RING FIT RESULTS")
            print("=" * 60)
            if use_wcs:
                print(f"  Coordinate System: {wcs_info}")
                print("-" * 60)
                print(f"  {'Parameter':<20} {'Value':>15} {'Error':>15}")
                print("-" * 60)
                print(f"  {'Amplitude':<20} {popt[0]:>15.4g} {perr[0]:>15.4g}")
                if ra_deg is not None and dec_deg is not None:
                    _ctype1_rf = str(_hdr.get("CTYPE1", "")).upper()
                    if "SOLAR" in _ctype1_rf:
                        print(f"  {'SOLAR-X (arcsec)':<20} {ra_deg * 3600.0:>15.2f}")
                        print(f"  {'SOLAR-Y (arcsec)':<20} {dec_deg * 3600.0:>15.2f}")
                    elif "HPLN" in _ctype1_rf:
                        # Unwrap helioprojective degrees to [-180, 180] then to arcsec
                        hpln_as = ((ra_deg + 180.0) % 360.0 - 180.0) * 3600.0
                        hplt_as = ((dec_deg + 180.0) % 360.0 - 180.0) * 3600.0
                        print(f"  {'HPLN (arcsec)':<20} {hpln_as:>15.2f}")
                        print(f"  {'HPLT (arcsec)':<20} {hplt_as:>15.2f}")
                    else:
                        print(f"  {'RA (deg)':<20} {ra_deg:>15.6f}")
                        print(f"  {'Dec (deg)':<20} {dec_deg:>15.6f}")
                print(f"  {'X0 (pixel)':<20} {x0_px:>15.2f} {perr[1]:>15.2f}")
                print(f"  {'Y0 (pixel)':<20} {y0_px:>15.2f} {perr[2]:>15.2f}")
                print(f"  {'Inner R (arcsec)':<20} {inner_r_arcsec:>15.3f}")
                print(f"  {'Outer R (arcsec)':<20} {outer_r_arcsec:>15.3f}")
                print(f"  {'Ring Width (arcsec)':<20} {width_arcsec:>15.3f}")
                print(f"  {'Inner R (pixel)':<20} {inner_r_px:>15.2f} {perr[3]:>15.2f}")
                print(f"  {'Outer R (pixel)':<20} {outer_r_px:>15.2f} {perr[4]:>15.2f}")
                print(f"  {'Offset':<20} {popt[5]:>15.4g} {perr[5]:>15.4g}")
            else:
                print("  Coordinate System: Pixel")
                print("-" * 60)
                print(f"  {'Parameter':<20} {'Value':>15} {'Error':>15}")
                print("-" * 60)
                print(f"  {'Amplitude':<20} {popt[0]:>15.4g} {perr[0]:>15.4g}")
                print(f"  {'X0 (pixel)':<20} {x0_px:>15.2f} {perr[1]:>15.2f}")
                print(f"  {'Y0 (pixel)':<20} {y0_px:>15.2f} {perr[2]:>15.2f}")
                print(f"  {'Inner R (pixel)':<20} {inner_r_px:>15.2f} {perr[3]:>15.2f}")
                print(f"  {'Outer R (pixel)':<20} {outer_r_px:>15.2f} {perr[4]:>15.2f}")
                print(f"  {'Ring Width (pixel)':<20} {outer_r_px - inner_r_px:>15.2f}")
                print(f"  {'Offset':<20} {popt[5]:>15.4g} {perr[5]:>15.4g}")
            print("=" * 60 + "\n")

            # GUI message (compact version)
            if use_wcs:
                msg = (
                    f"2D Ring Fit (WCS):\n"
                    f"Amp={popt[0]:.4g}±{perr[0]:.4g}\n"
                    f"Center: ({x0_px:.1f}, {y0_px:.1f}) px\n"
                    f'Inner R={inner_r_arcsec:.2f}", Outer R={outer_r_arcsec:.2f}"\n'
                    f'Ring Width={width_arcsec:.2f}"'
                )
            else:
                msg = (
                    f"2D Ring Fit (Pixel):\n"
                    f"Amp={popt[0]:.4g}±{perr[0]:.4g}\n"
                    f"X0={x0_px:.2f}±{perr[1]:.2f}, Y0={y0_px:.2f}±{perr[2]:.2f}\n"
                    f"Inner R={inner_r_px:.2f}±{perr[3]:.2f}, Outer R={outer_r_px:.2f}±{perr[4]:.2f}"
                )

            QMessageBox.information(self, "Fit Result", msg)

        except Exception as e:
            QMessageBox.warning(self, "Fit Error", f"Ring fit failed: {str(e)}")

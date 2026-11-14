"""
2D Gaussian fitting tool for SolarViewer.
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtWidgets import QApplication, QDialog, QLabel, QLayout, QMessageBox
from ..dialogs.form import field_label, form_block
from ..ui.background import Cancelled, run_in_thread
from ..ui.errors import show_error
from ..ui.widgets import button_bar, dialog_layout

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


def ask_fit_offset(parent, note=""):
    """Whether to fit a zero-level offset (a constant background) with the
    Gaussian. ``note`` comes first. Closing the box assumes zero background,
    as No did."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle("Fitting options")
    box.setText("Fit a constant background level (zero-level offset) with the Gaussian?")
    if note:
        box.setInformativeText(note.strip())
    # Buttons of plain words, with no mnemonic underlines (the standard
    # Yes and No have them)
    zero = box.addButton("Zero background", QMessageBox.RejectRole)
    fit = box.addButton("Fit offset", QMessageBox.AcceptRole)
    fit.setObjectName("PrimaryButton")
    box.setDefaultButton(fit)
    box.setEscapeButton(zero)
    box.exec_()
    return box.clickedButton() is fit


_SUPERSCRIPT = str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹")


def readable_number(value, digits=4):
    """``value`` to ``digits`` significant digits in plain decimals with
    thousands separated ("312,200", "0.01234"), or very large or small as
    "3.122 × 10⁻⁵" rather than "3.122e-05"."""
    value = float(value)
    if not np.isfinite(value):
        return str(value)
    size = abs(value)
    if size == 0 or 1e-3 <= size < 1e7:
        decimals = 0 if size == 0 else max(0, digits - 1 - int(np.floor(np.log10(size))))
        return f"{float(f'{value:.{digits}g}'):,.{decimals}f}"
    mantissa, exponent = f"{value:.{digits - 1}e}".split("e")
    return f"{mantissa} × 10{str(int(exponent)).translate(_SUPERSCRIPT)}"


def angle_text(arcsec):
    """An angle in arcsec as degrees, arcmin or arcsec, whichever reads
    best, with its unit ("8.160 arcmin"); "unresolved" for none."""
    if arcsec <= 0:
        return "unresolved"
    if arcsec >= 3600:
        return f"{arcsec / 3600:.3f}°"
    if arcsec >= 60:
        return f"{arcsec / 60:.3f} arcmin"
    return f"{arcsec:.3f} arcsec"


def angle_pair(major, minor):
    """A size as "major × minor", the unit said once when both share it."""
    first, second = angle_text(major), angle_text(minor)
    unit = first.rpartition(" ")[2]
    if " " in first and second.endswith(" " + unit):
        return f"{first.rpartition(' ')[0]} × {second}"
    return f"{first} × {second}"


class FitResultDialog(QDialog):
    """The fitted Gaussian as rows of name and value, the deconvolved size
    in a block of its own, with a button copying them as text."""

    def __init__(self, parent, component, deconvolved=()):
        super().__init__(parent)
        self.setWindowTitle("Fit result")
        layout = dialog_layout(self)
        longest = max((name for name, _ in list(component) + list(deconvolved)), key=len)
        lines = ["2D Gaussian fit"]
        for title, rows in (("Fitted component", component), ("Deconvolved from the beam", deconvolved)):
            if not rows:
                continue
            grid = form_block(layout, title, longest=longest)
            for row, (name, value) in enumerate(rows):
                grid.addWidget(field_label(name), row, 0)
                label = QLabel(value)
                label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                grid.addWidget(label, row, 1)
            lines.append("")
            lines.append(title)
            lines.extend(f"{name}: {value}" for name, value in rows)
        self.text = "\n".join(lines)
        box, buttons = button_bar(secondary=("Close", self.accept), extras=(("Copy", self.copy),))
        self.copy_button = buttons["Copy"]
        layout.addWidget(box)
        layout.setSizeConstraint(QLayout.SetFixedSize)

    def copy(self):
        QApplication.clipboard().setText(self.text)
        self.copy_button.setText("Copied")
        QTimer.singleShot(1500, lambda: self.copy_button.setText("Copy"))


def show_fit_result(parent, component, deconvolved=()):
    """Show the fit's rows in a FitResultDialog until it is closed."""
    FitResultDialog(parent, component, deconvolved).exec_()


def run_imfit_process(command, env, cwd, stop, result_file, timeout=120):
    """Run ``command`` (the imfit process) to its end: (exit code, stdout,
    stderr). Raises subprocess.TimeoutExpired after ``timeout`` seconds.

    Cancel cannot stop the thread this runs on, so it sets ``stop``: the
    process is killed, ``result_file`` removed, and None returned.
    """
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=cwd,
    )
    deadline = time.monotonic() + timeout
    while True:
        try:
            stdout, stderr = process.communicate(timeout=0.1)
            break
        except subprocess.TimeoutExpired:
            if stop.is_set() or time.monotonic() > deadline:
                process.kill()
                process.communicate()
                if not stop.is_set():
                    raise subprocess.TimeoutExpired(command, timeout)
                break
    if stop.is_set():
        if os.path.exists(result_file):
            os.remove(result_file)
        return None
    return process.returncode, stdout, stderr


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
            QMessageBox.warning(self, "No image", "Please load an image first.")
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
        dooff = ask_fit_offset(self, note)

        try:
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

            self.statusBar().showMessage("Fitting a Gaussian…")

            # Run imfit in a subprocess to avoid casatasks/PyQt5 conflicts
            env = os.environ.copy()
            # Clear Qt plugin paths to avoid conflicts
            for key in ["QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"]:
                if key in env:
                    del env[key]

            stop = threading.Event()
            try:
                returncode, stdout, stderr = run_in_thread(
                    self,
                    "Fitting a Gaussian…",
                    run_imfit_process,
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
                    env,
                    (
                        os.getcwd()
                        if os.access(os.getcwd(), os.W_OK)
                        else tempfile.gettempdir()
                    ),
                    stop,
                    result_file,
                )
            except Cancelled:
                stop.set()
                self.statusBar().showMessage("Gaussian fit cancelled", 3000)
                return

            # Check if subprocess had errors
            if returncode != 0:
                error_msg = stderr or stdout or "Unknown error"
                QMessageBox.warning(
                    self, "Fit error", f"imfit subprocess failed:\n{error_msg}"
                )
                if os.path.exists(result_file):
                    os.remove(result_file)
                return

            # Read results from temp file
            if not os.path.exists(result_file):
                QMessageBox.warning(self, "Fit error", "No results from fitting")
                return

            with open(result_file, "r") as f:
                output = json.load(f)

            os.remove(result_file)

            if not output.get("success", False):
                error = output.get("error", "Unknown error")
                tb = output.get("traceback", "")
                logger.error(f"Fitting failed: {error}\n{tb}")
                QMessageBox.warning(self, "Fit error", f"Fitting failed: {error}")
                return

            results = output.get("results", {})
            # With a zero-level offset, imfit can return only the offset when it
            # fails to fit a component; don't report that as an all-zero source.
            if "major_arcsec" not in results:
                QMessageBox.warning(
                    self,
                    "Fit failed",
                    "No Gaussian component could be fitted in the selected region.\n\n"
                    "Try a region that is better centred on the source"
                    + (", or fit without the zero-level offset." if dooff else "."),
                )
                return

            begin_busy("Reading the fit…", self)

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

            # The window's rows (the whole table is in the log above)
            if current_tab._is_already_hpc():
                # (imfit's longitude and latitude are Solar-X and Y here)
                solar_x = ((ra_deg + 180.0) % 360.0 - 180.0) * 3600.0
                position = [("Solar-X", f"{solar_x:.1f} arcsec"), ("Solar-Y", f"{dec_deg * 3600.0:.1f} arcsec")]
            else:
                position = [("RA", ra_hms), ("Dec", dec_dms)]
            component = position + [
                ("Size", angle_pair(major_arcsec, minor_arcsec)),
                ("Position angle", f"{pa_deg:.1f}°"),
                ("Flux", f"{readable_number(flux)} {flux_unit}"),
            ]
            deconvolved = []
            if deconv_major > 0 or deconv_minor > 0:
                deconvolved.append(("Size", angle_pair(deconv_major, deconv_minor)))
                if deconv_pa is not None:
                    deconvolved.append(("Position angle", f"{deconv_pa:.1f}°"))

            show_fit_result(self, component, deconvolved)
            self.statusBar().showMessage("Gaussian fit completed", 3000)

        except subprocess.TimeoutExpired:
            QMessageBox.warning(
                self, "Timeout", "Gaussian fit took too long to complete"
            )
        except Exception as e:
            end_busy()
            import traceback

            traceback.print_exc()
            show_error(
                self,
                "Gaussian fit failed",
                f"The Gaussian fit failed: {e}\n\nTry a box drawn around a single, "
                "isolated source, a little larger than it.",
                traceback.format_exc(),
            )

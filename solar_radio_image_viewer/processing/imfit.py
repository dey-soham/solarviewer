"""
Run CASA's ``imfit`` on an image and write the fit results as JSON.

Executed in a separate process (to avoid casatasks/PyQt5 conflicts) by
``FittingMixin.fit_2d_gaussian``:

    python -m solar_radio_image_viewer.processing.imfit \
        IMAGENAME BOX RESULT_FILE DOOFF STOKES

DOOFF is ``True`` or ``False``. The JSON written to RESULT_FILE has the form
``{"success": True, "results": {...}}`` or
``{"success": False, "error": ..., "traceback": ...}``.
"""

import json
import sys

import numpy as np


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
        if hasattr(result, "item"):
            result = result.item()
        return result
    except:
        return default


def run_imfit(imagename, box_param, result_file, dooff, stokes_param):
    from casatasks import imfit

    try:
        # Use box parameter (allows stokes) instead of region (doesn't allow stokes)
        fit_results = imfit(
            imagename=imagename,
            box=box_param,
            dooff=dooff,
            stokes=stokes_param,
        )

        # Extract the data we need (convert numpy types to Python types)
        output = {"success": True, "results": {}}

        if fit_results and "results" in fit_results:
            results = fit_results["results"]
            if "component0" in results:
                comp = results["component0"]

                # Position
                direction = comp.get("shape", {}).get("direction", {})
                output["results"]["ra_rad"] = float(
                    safe_get(direction, ["m0", "value"], 0)
                )
                output["results"]["dec_rad"] = float(
                    safe_get(direction, ["m1", "value"], 0)
                )
                # Position errors - CASA uses direction.error.longitude (RA) and direction.error.latitude (Dec)
                # CASA stores these in arcsec (check unit field to confirm)
                ra_err = safe_get(direction, ["error", "longitude", "value"])
                ra_err_unit = safe_get(
                    direction, ["error", "longitude", "unit"], "arcsec"
                )
                if ra_err is not None:
                    # Store in arcsec - convert if needed based on unit
                    if ra_err_unit == "rad":
                        output["results"]["ra_err_arcsec"] = (
                            float(ra_err) * 180 / 3.14159265359 * 3600
                        )
                    else:  # assume arcsec
                        output["results"]["ra_err_arcsec"] = float(ra_err)

                dec_err = safe_get(direction, ["error", "latitude", "value"])
                dec_err_unit = safe_get(
                    direction, ["error", "latitude", "unit"], "arcsec"
                )
                if dec_err is not None:
                    if dec_err_unit == "rad":
                        output["results"]["dec_err_arcsec"] = (
                            float(dec_err) * 180 / 3.14159265359 * 3600
                        )
                    else:  # assume arcsec
                        output["results"]["dec_err_arcsec"] = float(dec_err)

                # Shape
                shape = comp.get("shape", {})
                output["results"]["major_arcsec"] = float(
                    safe_get(shape, ["majoraxis", "value"], 0)
                )
                output["results"]["minor_arcsec"] = float(
                    safe_get(shape, ["minoraxis", "value"], 0)
                )
                output["results"]["pa_deg"] = float(
                    safe_get(shape, ["positionangle", "value"], 0)
                )

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
                output["results"]["flux_unit"] = str(
                    safe_get(comp, ["flux", "unit"], "Jy")
                )

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
                    output["results"]["peak_unit"] = str(
                        safe_get(comp, ["peak", "unit"], "Jy/beam")
                    )
                    # Peak error is a float
                    peak_err = safe_get(comp, ["peak", "error"])
                    if peak_err is not None:
                        output["results"]["peak_err"] = float(peak_err)

        # Deconvolved - uses same key structure as 'results'
        if fit_results and "deconvolved" in fit_results:
            deconv_comp = safe_get(fit_results, ["deconvolved", "component0", "shape"])
            if deconv_comp:
                output["results"]["deconv_major"] = float(
                    safe_get(deconv_comp, ["majoraxis", "value"], 0)
                )
                output["results"]["deconv_minor"] = float(
                    safe_get(deconv_comp, ["minoraxis", "value"], 0)
                )
                output["results"]["deconv_pa"] = float(
                    safe_get(deconv_comp, ["positionangle", "value"], 0)
                )
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
        zerooff = fit_results.get("zerooff", {}) if fit_results else {}
        if zerooff:
            offset_val = safe_get(zerooff, ["value"])
            if offset_val is not None:
                if isinstance(offset_val, (list, tuple, np.ndarray)):
                    offset_val = float(offset_val[0]) if len(offset_val) > 0 else 0
                output["results"]["offset"] = float(offset_val)
            output["results"]["offset_unit"] = str(zerooff.get("unit", ""))

        # Zero offset error - stored in separate zeroofferr dict
        zeroofferr = fit_results.get("zeroofferr", {}) if fit_results else {}
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
            json.dump(
                {
                    "success": False,
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                },
                f,
            )


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    imagename, box_param, result_file, dooff, stokes_param = argv
    run_imfit(imagename, box_param, result_file, dooff == "True", stokes_param)


if __name__ == "__main__":
    main()

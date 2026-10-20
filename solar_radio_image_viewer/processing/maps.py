"""
Brightness temperature <-> flux density map conversion.
"""

import os

from .dependencies import CASA_AVAILABLE, IA
from .fits_io import readable_image_path


def generate_tb_map(imagename, outfile=None, flux_data=None):
    """
    Generate brightness temperature map from flux-calibrated image.

    Formula: TB = 1.222e6 * flux / freq^2 / (major * minor)
    Where: freq in GHz, major/minor in arcsec

    Parameters
    ----------
    imagename : str
        Path to the input image (FITS or CASA format)
    outfile : str, optional
        Path for output FITS file. If None, returns data without saving.
    flux_data : numpy.ndarray, optional
        Pre-loaded flux data. If None, loads from imagename.

    Returns
    -------
    tuple
        (tb_data, header_info) where header_info contains beam and freq info
        Returns (None, error_message) on failure
    """
    try:
        from astropy.io import fits

        is_fits = imagename.endswith(".fits") or imagename.endswith(".fts")

        header_info = {}

        if is_fits:
            # FITS file
            with fits.open(readable_image_path(imagename)) as hdul:
                header = hdul[0].header
                if flux_data is None:
                    flux_data = hdul[0].data

                # Get beam major/minor (degrees -> arcsec)
                if "BMAJ" in header and "BMIN" in header:
                    major = header["BMAJ"] * 3600
                    minor = header["BMIN"] * 3600
                else:
                    return None, "Beam parameters (BMAJ/BMIN) not found in header"

                # Get frequency (Hz -> GHz)
                freq_hz = None
                for key in ["CRVAL3", "CRVAL4", "FREQ", "RESTFRQ"]:
                    if key in header and header[key] is not None:
                        try:
                            val = float(header[key])
                            if val > 1e6:  # Must be Hz
                                freq_hz = val
                                break
                        except:
                            pass

                if freq_hz is None:
                    return None, "Frequency not found in header"

                freq_ghz = freq_hz / 1e9

                header_info = {
                    "major": major,
                    "minor": minor,
                    "freq_ghz": freq_ghz,
                    "original_header": header.copy(),
                }
        else:
            # CASA image
            if not CASA_AVAILABLE:
                return None, "CASA tools not available for CASA image"

            ia = IA()
            ia.open(readable_image_path(imagename))

            if flux_data is None:
                flux_data = ia.getchunk()
                # Squeeze to 2D for display (keep original for full Stokes save)
                if flux_data.ndim == 4:
                    flux_data = flux_data[:, :, 0, 0]  # Take first Stokes and freq
                elif flux_data.ndim == 3:
                    flux_data = flux_data[:, :, 0]  # Take first plane

            # Get beam info
            beam = ia.restoringbeam()
            if beam and "major" in beam:
                major = beam["major"]["value"]
                minor = beam["minor"]["value"]
                if beam["major"]["unit"] == "arcsec":
                    pass  # already in arcsec
                elif beam["major"]["unit"] == "deg":
                    major *= 3600
                    minor *= 3600
            else:
                ia.close()
                return None, "Beam parameters not found in CASA image"

            # Get frequency
            csys = ia.coordsys()
            units = csys.units()
            refval = csys.referencevalue()["numeric"]

            freq_hz = None
            for i, unit in enumerate(units):
                if unit == "Hz":
                    freq_hz = refval[i]
                    break

            ia.close()

            if freq_hz is None:
                return None, "Frequency not found in CASA image"

            freq_ghz = freq_hz / 1e9

            header_info = {"major": major, "minor": minor, "freq_ghz": freq_ghz}

        # Calculate brightness temperature
        # print(f"[TB] Beam: {header_info['major']:.2f}\" x {header_info['minor']:.2f}\", Freq: {header_info['freq_ghz']:.4f} GHz")
        tb_data = 1.222e6 * flux_data / (freq_ghz**2) / (major * minor)

        # print(f"[TB] Temperature range: {np.nanmin(tb_data):.2e} to {np.nanmax(tb_data):.2e} K")

        # Save to file if outfile specified
        if outfile is not None:
            if is_fits:
                new_header = header_info["original_header"].copy()
                new_header["BUNIT"] = "K"
                new_header["HISTORY"] = (
                    "Converted to brightness temperature with SolarViewer"
                )

                # Ensure RESTFRQ is present (needed for downstream HPC conversion)
                if "RESTFRQ" not in new_header:
                    freq_hz = header_info["freq_ghz"] * 1e9
                    new_header["RESTFRQ"] = freq_hz

                # Get original data to check for full Stokes
                original_data = fits.getdata(readable_image_path(imagename))

                # Check if original is multi-Stokes (3D or 4D with Stokes axis)
                if original_data.ndim >= 3:
                    # Find number of Stokes planes
                    stokes_idx = None
                    for i in range(
                        1, header_info["original_header"].get("NAXIS", 0) + 1
                    ):
                        if (
                            header_info["original_header"].get(f"CTYPE{i}", "").upper()
                            == "STOKES"
                        ):
                            stokes_idx = i - 1  # 0-indexed for numpy
                            break

                    if stokes_idx is not None:
                        # Full Stokes - convert all planes
                        # print(f"[TB] Converting full Stokes data (shape: {original_data.shape})")
                        tb_data_save = (
                            1.222e6 * original_data / (freq_ghz**2) / (major * minor)
                        )
                    else:
                        # 3D but not Stokes - transpose as needed
                        if original_data.shape != tb_data.shape:
                            tb_data_save = tb_data.T
                        else:
                            tb_data_save = tb_data
                else:
                    # 2D data
                    if original_data.shape != tb_data.shape:
                        tb_data_save = tb_data.T
                    else:
                        tb_data_save = tb_data

                new_hdu = fits.PrimaryHDU(data=tb_data_save, header=new_header)
                new_hdu.header.add_history(
                    "Brightness temperature map generated with SolarViewer"
                )
                new_hdu.writeto(outfile, overwrite=True)
            else:
                # For CASA, need to export first
                temp_export = outfile + ".temp_export.fits"
                ia = IA()
                ia.open(readable_image_path(imagename))
                ia.tofits(temp_export, overwrite=True, stokeslast=False)
                ia.close()

                with fits.open(temp_export) as hdul:
                    original_data = hdul[0].data
                    new_header = hdul[0].header.copy()
                    new_header["BUNIT"] = "K"
                    new_header["HISTORY"] = (
                        "Converted to brightness temperature with SolarViewer"
                    )

                    # Check for multi-Stokes
                    if original_data.ndim >= 3:
                        # Full Stokes - convert all planes
                        # print(f"[TB] Converting full Stokes CASA data (shape: {original_data.shape})")
                        tb_data_save = (
                            1.222e6 * original_data / (freq_ghz**2) / (major * minor)
                        )
                    else:
                        if original_data.shape != tb_data.shape:
                            tb_data_save = tb_data.T
                        else:
                            tb_data_save = tb_data
                    new_hdu = fits.PrimaryHDU(data=tb_data_save, header=new_header)
                    new_hdu.header.add_history(
                        "Brightness temperature map generated with SolarViewer"
                    )
                    new_hdu.writeto(outfile, overwrite=True)

                if os.path.exists(temp_export):
                    os.remove(temp_export)

            # print(f"[TB] Saved TB map to: {outfile}")

        return tb_data, header_info

    except Exception as e:
        import traceback

        traceback.print_exc()
        return None, str(e)


def generate_flux_map(imagename, outfile=None, tb_data=None):
    """
    Generate flux map from brightness temperature image.

    Reverse formula: flux = TB * freq^2 * (major * minor) / 1.222e6
    Where: freq in GHz, major/minor in arcsec

    Parameters
    ----------
    imagename : str
        Path to the input TB image (FITS or CASA format)
    outfile : str, optional
        Path for output FITS file. If None, returns data without saving.
    tb_data : numpy.ndarray, optional
        Pre-loaded TB data. If None, loads from imagename.

    Returns
    -------
    tuple
        (flux_data, header_info) where header_info contains beam and freq info
        Returns (None, error_message) on failure
    """
    try:
        from astropy.io import fits

        is_fits = imagename.endswith(".fits") or imagename.endswith(".fts")

        header_info = {}

        if is_fits:
            # FITS file
            with fits.open(readable_image_path(imagename)) as hdul:
                header = hdul[0].header
                if tb_data is None:
                    tb_data = hdul[0].data

                # Get beam major/minor (degrees -> arcsec)
                if "BMAJ" in header and "BMIN" in header:
                    major = header["BMAJ"] * 3600
                    minor = header["BMIN"] * 3600
                else:
                    return None, "Beam parameters (BMAJ/BMIN) not found in header"

                # Get frequency (Hz -> GHz)
                freq_hz = None
                for key in ["CRVAL3", "CRVAL4", "FREQ", "RESTFRQ"]:
                    if key in header and header[key] is not None:
                        try:
                            val = float(header[key])
                            if val > 1e6:  # Must be Hz
                                freq_hz = val
                                break
                        except:
                            pass

                if freq_hz is None:
                    return None, "Frequency not found in header"

                freq_ghz = freq_hz / 1e9

                header_info = {
                    "major": major,
                    "minor": minor,
                    "freq_ghz": freq_ghz,
                    "original_header": header.copy(),
                }
        else:
            # CASA image
            if not CASA_AVAILABLE:
                return None, "CASA tools not available for CASA image"

            ia = IA()
            ia.open(readable_image_path(imagename))

            if tb_data is None:
                tb_data = ia.getchunk()
                while tb_data.ndim > 2:
                    tb_data = (
                        tb_data[:, :, 0]
                        if tb_data.shape[2] == 1
                        else tb_data[:, :, 0, 0]
                    )

            # Get beam info
            beam = ia.restoringbeam()
            if beam and "major" in beam:
                major = beam["major"]["value"]
                minor = beam["minor"]["value"]
                if beam["major"]["unit"] == "arcsec":
                    pass
                elif beam["major"]["unit"] == "deg":
                    major *= 3600
                    minor *= 3600
            else:
                ia.close()
                return None, "Beam parameters not found in CASA image"

            # Get frequency
            csys = ia.coordsys()
            units = csys.units()
            refval = csys.referencevalue()["numeric"]

            freq_hz = None
            for i, unit in enumerate(units):
                if unit == "Hz":
                    freq_hz = refval[i]
                    break

            ia.close()

            if freq_hz is None:
                return None, "Frequency not found in CASA image"

            freq_ghz = freq_hz / 1e9

            header_info = {"major": major, "minor": minor, "freq_ghz": freq_ghz}

        # Calculate flux: flux = TB * freq^2 * (major * minor) / 1.222e6
        # print(f"[Flux] Beam: {header_info['major']:.2f}\" x {header_info['minor']:.2f}\", Freq: {header_info['freq_ghz']:.4f} GHz")
        flux_data = tb_data * (freq_ghz**2) * (major * minor) / 1.222e6

        # print(f"[Flux] Flux range: {np.nanmin(flux_data):.2e} to {np.nanmax(flux_data):.2e} Jy/beam")

        # Save to file if outfile specified
        if outfile is not None:
            if is_fits:
                new_header = header_info["original_header"].copy()
                new_header["BUNIT"] = "Jy/beam"
                new_header["HISTORY"] = (
                    "Converted from brightness temperature with SolarViewer"
                )

                # Ensure RESTFRQ is present (needed for downstream HPC conversion)
                if "RESTFRQ" not in new_header:
                    freq_hz = header_info["freq_ghz"] * 1e9
                    new_header["RESTFRQ"] = freq_hz

                # Get original data to check shape
                original_data = fits.getdata(readable_image_path(imagename))

                # Handle multi-Stokes
                if original_data.ndim >= 3:
                    stokes_idx = None
                    for i in range(
                        1, header_info["original_header"].get("NAXIS", 0) + 1
                    ):
                        if (
                            header_info["original_header"].get(f"CTYPE{i}", "").upper()
                            == "STOKES"
                        ):
                            stokes_idx = i - 1
                            break

                    if stokes_idx is not None:
                        # print(f"[Flux] Converting full Stokes data (shape: {original_data.shape})")
                        flux_data_save = (
                            original_data * (freq_ghz**2) * (major * minor) / 1.222e6
                        )
                    else:
                        if original_data.shape != flux_data.shape:
                            flux_data_save = flux_data.T
                        else:
                            flux_data_save = flux_data
                else:
                    if original_data.shape != flux_data.shape:
                        flux_data_save = flux_data.T
                    else:
                        flux_data_save = flux_data

                new_hdu = fits.PrimaryHDU(data=flux_data_save, header=new_header)
                new_hdu.header.add_history(
                    "Flux density map generated with SolarViewer"
                )
                new_hdu.writeto(outfile, overwrite=True)
            else:
                # For CASA, need to export first
                temp_export = outfile + ".temp_export.fits"
                ia = IA()
                ia.open(readable_image_path(imagename))
                ia.tofits(temp_export, overwrite=True, stokeslast=False)
                ia.close()

                with fits.open(temp_export) as hdul:
                    original_data = hdul[0].data
                    new_header = hdul[0].header.copy()
                    new_header["BUNIT"] = "Jy/beam"
                    new_header["HISTORY"] = (
                        "Converted from brightness temperature with SolarViewer"
                    )

                    if original_data.ndim >= 3:
                        # print(f"[Flux] Converting full Stokes CASA data (shape: {original_data.shape})")
                        flux_data_save = (
                            original_data * (freq_ghz**2) * (major * minor) / 1.222e6
                        )
                    else:
                        if original_data.shape != flux_data.shape:
                            flux_data_save = flux_data.T
                        else:
                            flux_data_save = flux_data

                    new_hdu = fits.PrimaryHDU(data=flux_data_save, header=new_header)
                    new_hdu.header.add_history(
                        "Flux density map generated with SolarViewer"
                    )
                    new_hdu.writeto(outfile, overwrite=True)

                if os.path.exists(temp_export):
                    os.remove(temp_export)

            # print(f"[Flux] Saved flux map to: {outfile}")

        return flux_data, header_info

    except Exception as e:
        import traceback

        traceback.print_exc()
        return None, str(e)

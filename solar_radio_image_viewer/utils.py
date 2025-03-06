import os
import numpy as np

# Try to import CASA tools & tasks
try:
    from casatools import image as IA
    from casatasks import immath

    CASA_AVAILABLE = True
except ImportError:
    print(
        "WARNING: CASA tools not found. This application requires CASA to be installed."
    )
    CASA_AVAILABLE = False
    IA = None
    immath = None

# Try to import scipy
try:
    from scipy.optimize import curve_fit

    SCIPY_AVAILABLE = True
except ImportError:
    print("WARNING: scipy not found. Fitting functionality will be disabled.")
    SCIPY_AVAILABLE = False
    curve_fit = None

# Try to import astropy
try:
    from astropy.wcs import WCS
    import astropy.units as u

    ASTROPY_AVAILABLE = True
except ImportError:
    print("WARNING: astropy not found. Some functionality will be limited.")
    ASTROPY_AVAILABLE = False
    WCS = None
    u = None


def estimate_rms_near_Sun(imagename, stokes="I", box=(0, 200, 0, 130)):
    stokes_map = {"I": 0, "Q": 1, "U": 2, "V": 3}
    ia_tool = IA()
    ia_tool.open(imagename)
    summary = ia_tool.summary()
    dimension_names = summary["axisnames"]

    ra_idx = np.where(dimension_names == "Right Ascension")[0][0]
    dec_idx = np.where(dimension_names == "Declination")[0][0]

    stokes_idx = None
    freq_idx = None
    if "Stokes" in dimension_names:
        stokes_idx = np.where(np.array(dimension_names) == "Stokes")[0][0]
    if "Frequency" in dimension_names:
        freq_idx = np.where(np.array(dimension_names) == "Frequency")[0][0]

    data = ia_tool.getchunk()
    ia_tool.close()

    if stokes_idx is not None:
        idx = stokes_map.get(stokes, 0)
        slice_list = [slice(None)] * len(data.shape)
        slice_list[stokes_idx] = idx

        if freq_idx is not None:
            slice_list[freq_idx] = 0

        stokes_data = data[tuple(slice_list)]
    else:
        stokes_data = data

    x1, x2, y1, y2 = box
    region_slice = [slice(None)] * len(stokes_data.shape)
    region_slice[ra_idx] = slice(x1, x2)
    region_slice[dec_idx] = slice(y1, y2)
    region = stokes_data[tuple(region_slice)]
    if region.size == 0:
        return 0.0
    rms = np.sqrt(np.mean(region**2))
    return rms


def remove_pixels_away_from_sun(pix, csys, radius_arcmin=55):
    rad_to_deg = 180.0 / np.pi
    # Use astropy's WCS for coordinate conversion
    from astropy.wcs import WCS

    w = WCS(naxis=2)
    w.wcs.cdelt = csys.increment()["numeric"][0:2] * rad_to_deg
    radius_deg = radius_arcmin / 60.0
    delta_deg = abs(w.wcs.cdelt[0])
    pixel_radius = radius_deg / delta_deg

    cx = pix.shape[0] / 2
    cy = pix.shape[1] / 2
    y, x = np.ogrid[: pix.shape[1], : pix.shape[0]]
    mask = (x - cx) ** 2 + (y - cy) ** 2 > pixel_radius**2
    pix[mask] = 0
    return pix


# TODO: Handle single stokes case, return flag so that some features can be disabled


def get_pixel_values_from_image(
    imagename,
    stokes,
    thres,
    rms_box=(0, 200, 0, 130),
    stokes_map={"I": 0, "Q": 1, "U": 2, "V": 3},
):
    """
    Retrieve pixel values from a CASA image with proper error handling and dimension checks.

    Parameters:
      imagename : str
         Path to the CASA image directory.
      stokes : str
         The stokes parameter to extract ("I", "Q", "U", "V", "L", "Lfrac", "Vfrac", "Q/I", "U/I", "U/V", or "PANG").
      thres : float
         Threshold value.
      rms_box : tuple, optional
         Region coordinates (x1, x2, y1, y2) for RMS estimation.
      stokes_map : dict, optional
         Mapping of standard stokes parameters to their corresponding axis indices.

    Returns:
      pix : numpy.ndarray
         The extracted pixel data.
      csys : object
         Coordinate system object from CASA.
      psf : object
         Beam information from CASA.

    Raises:
      RuntimeError: For errors in reading the image or if required dimensions are missing.
    """

    if not CASA_AVAILABLE:
        raise RuntimeError("CASA is not available")

    single_stokes_flag = False
    try:
        ia_tool = IA()
        ia_tool.open(imagename)
    except Exception as e:
        raise RuntimeError(f"Failed to open image {imagename}: {e}")

    try:
        summary = ia_tool.summary()
        dimension_names = summary.get("axisnames")
        dimension_shapes = summary.get("shape")
        if dimension_names is None:
            raise ValueError("Image summary does not contain 'axisnames'")
        # Ensure we can index; convert to numpy array if needed
        dimension_names = np.array(dimension_names)

        try:
            ra_idx = int(np.where(dimension_names == "Right Ascension")[0][0])
        except IndexError:
            raise ValueError("Right Ascension axis not found in image summary.")

        try:
            dec_idx = int(np.where(dimension_names == "Declination")[0][0])
        except IndexError:
            raise ValueError("Declination axis not found in image summary.")

        if "Stokes" in dimension_names:
            stokes_idx = int(np.where(dimension_names == "Stokes")[0][0])
            if dimension_shapes[stokes_idx] == 1:
                single_stokes_flag = True
        else:
            # Assume single stokes; set index to 0
            stokes_idx = None
            single_stokes_flag = True

        if "Frequency" in dimension_names:
            freq_idx = int(np.where(dimension_names == "Frequency")[0][0])
        else:
            # If Frequency axis is missing, assume index 0
            freq_idx = None

        data = ia_tool.getchunk()
        psf = ia_tool.restoringbeam()
        csys = ia_tool.coordsys()
    except Exception as e:
        ia_tool.close()
        raise RuntimeError(f"Error reading image metadata: {e}")
    ia_tool.close()

    # Verify that our slice indices are within data dimensions
    n_dims = len(data.shape)
    if stokes_idx is not None and (stokes_idx >= n_dims):
        raise RuntimeError(
            "The determined axis index is out of bounds for the image data."
        )
    if freq_idx is not None and (freq_idx >= n_dims):
        raise RuntimeError(
            "The determined axis index is out of bounds for the image data."
        )

    # Process based on stokes type
    if stokes in ["I", "Q", "U", "V"]:
        idx = stokes_map.get(stokes)
        if idx is None:
            raise ValueError(f"Unknown Stokes parameter: {stokes}")
        slice_list = [slice(None)] * n_dims
        if stokes_idx is not None:
            if single_stokes_flag:
                if stokes != "I":
                    raise RuntimeError(
                        "The image is single stokes, but the Stokes parameter is not 'I'."
                    )
            slice_list[stokes_idx] = idx
        if freq_idx is not None:
            slice_list[freq_idx] = 0
        pix = data[tuple(slice_list)]
    elif stokes == "L":
        if stokes_idx is None:
            raise RuntimeError("The image does not have a Stokes axis.")
        elif single_stokes_flag:
            raise RuntimeError(
                "The image is single stokes, but the Stokes parameter is not 'I'."
            )
        slice_list_Q = [slice(None)] * n_dims
        slice_list_U = [slice(None)] * n_dims
        slice_list_Q[stokes_idx] = 1
        slice_list_U[stokes_idx] = 2
        slice_list_Q[freq_idx] = 0
        slice_list_U[freq_idx] = 0
        pix_Q = data[tuple(slice_list_Q)]
        pix_U = data[tuple(slice_list_U)]
        pix = np.sqrt(pix_Q**2 + pix_U**2)
    elif stokes == "Lfrac":
        if stokes_idx is None:
            raise RuntimeError("The image does not have a Stokes axis.")
        elif single_stokes_flag:
            raise RuntimeError(
                "The image is single stokes, but the Stokes parameter is not 'I'."
            )
        outfile = "temp_p_map.im"
        try:
            immath(imagename=imagename, outfile=outfile, mode="lpoli")
            p_rms = estimate_rms_near_Sun(outfile, "I", rms_box)
        except Exception as e:
            raise RuntimeError(f"Error generating polarization map: {e}")
        finally:
            os.system(f"rm -rf {outfile}")
        slice_list_Q = [slice(None)] * n_dims
        slice_list_U = [slice(None)] * n_dims
        slice_list_I = [slice(None)] * n_dims
        slice_list_Q[stokes_idx] = 1
        slice_list_U[stokes_idx] = 2
        slice_list_I[stokes_idx] = 0
        slice_list_Q[freq_idx] = 0
        slice_list_U[freq_idx] = 0
        slice_list_I[freq_idx] = 0
        pix_Q = data[tuple(slice_list_Q)]
        pix_U = data[tuple(slice_list_U)]
        pix_I = data[tuple(slice_list_I)]
        pvals = np.sqrt(pix_Q**2 + pix_U**2)
        mask = pvals < (thres * p_rms)
        pvals[mask] = 0
        pix = pvals / pix_I
        pix = remove_pixels_away_from_sun(pix, csys, 55)
    elif stokes == "Vfrac":
        if stokes_idx is None:
            raise RuntimeError("The image does not have a Stokes axis.")
        elif single_stokes_flag:
            raise RuntimeError(
                "The image is single stokes, but the Stokes parameter is not 'I'."
            )
        slice_list_V = [slice(None)] * n_dims
        slice_list_I = [slice(None)] * n_dims
        slice_list_V[stokes_idx] = 3
        slice_list_I[stokes_idx] = 0
        slice_list_V[freq_idx] = 0
        slice_list_I[freq_idx] = 0
        pix_V = data[tuple(slice_list_V)]
        pix_I = data[tuple(slice_list_I)]
        v_rms = estimate_rms_near_Sun(imagename, "V", rms_box)
        mask = np.abs(pix_V) < (thres * v_rms)
        pix_V[mask] = 0
        pix = pix_V / pix_I
        pix = remove_pixels_away_from_sun(pix, csys, 55)
    elif stokes == "Q/I":
        if stokes_idx is None:
            raise RuntimeError("The image does not have a Stokes axis.")
        elif single_stokes_flag:
            raise RuntimeError(
                "The image is single stokes, but the Stokes parameter is not 'I'."
            )
        q_rms = estimate_rms_near_Sun(imagename, "Q", rms_box)
        slice_list_Q = [slice(None)] * n_dims
        slice_list_I = [slice(None)] * n_dims
        slice_list_Q[stokes_idx] = 1
        slice_list_I[stokes_idx] = 0
        slice_list_Q[freq_idx] = 0
        slice_list_I[freq_idx] = 0
        pix_Q = data[tuple(slice_list_Q)]
        mask = np.abs(pix_Q) < (thres * q_rms)
        pix_Q[mask] = 0
        pix_I = data[tuple(slice_list_I)]
        pix = pix_Q / pix_I
        pix = remove_pixels_away_from_sun(pix, csys, 55)
    elif stokes == "U/I":
        if stokes_idx is None:
            raise RuntimeError("The image does not have a Stokes axis.")
        elif single_stokes_flag:
            raise RuntimeError(
                "The image is single stokes, but the Stokes parameter is not 'I'."
            )
        u_rms = estimate_rms_near_Sun(imagename, "U", rms_box)
        slice_list_U = [slice(None)] * n_dims
        slice_list_I = [slice(None)] * n_dims
        slice_list_U[stokes_idx] = 2
        slice_list_I[stokes_idx] = 0
        slice_list_U[freq_idx] = 0
        slice_list_I[freq_idx] = 0
        pix_U = data[tuple(slice_list_U)]
        mask = np.abs(pix_U) < (thres * u_rms)
        pix_U[mask] = 0
        pix_I = data[tuple(slice_list_I)]
        pix = pix_U / pix_I
        pix = remove_pixels_away_from_sun(pix, csys, 55)
    elif stokes == "U/V":
        if stokes_idx is None:
            raise RuntimeError("The image does not have a Stokes axis.")
        elif single_stokes_flag:
            raise RuntimeError(
                "The image is single stokes, but the Stokes parameter is not 'I'."
            )
        u_rms = estimate_rms_near_Sun(imagename, "U", rms_box)
        slice_list_U = [slice(None)] * n_dims
        slice_list_V = [slice(None)] * n_dims
        slice_list_U[stokes_idx] = 2
        slice_list_V[stokes_idx] = 3
        slice_list_U[freq_idx] = 0
        slice_list_V[freq_idx] = 0
        pix_U = data[tuple(slice_list_U)]
        pix_V = data[tuple(slice_list_V)]
        mask = np.abs(pix_U) < (thres * u_rms)
        pix_U[mask] = 0
        pix = pix_U / pix_V
        pix = remove_pixels_away_from_sun(pix, csys, 55)
    elif stokes == "PANG":
        if stokes_idx is None:
            raise RuntimeError("The image does not have a Stokes axis.")
        elif single_stokes_flag:
            raise RuntimeError(
                "The image is single stokes, but the Stokes parameter is not 'I'."
            )
        slice_list_Q = [slice(None)] * n_dims
        slice_list_U = [slice(None)] * n_dims
        slice_list_Q[stokes_idx] = 1
        slice_list_U[stokes_idx] = 2
        slice_list_Q[freq_idx] = 0
        slice_list_U[freq_idx] = 0
        pix_Q = data[tuple(slice_list_Q)]
        pix_U = data[tuple(slice_list_U)]
        pix = 0.5 * np.arctan2(pix_U, pix_Q) * 180 / np.pi
    else:
        slice_list_I = [slice(None)] * n_dims
        slice_list_I[stokes_idx] = 0
        slice_list_I[freq_idx] = 0
        pix = data[tuple(slice_list_I)]

    return pix, csys, psf


def get_image_metadata(imagename):
    if not CASA_AVAILABLE:
        if ASTROPY_AVAILABLE:
            from astropy.coordinates import SkyCoord

            ref_coord = SkyCoord(ra=180.0 * u.degree, dec=45.0 * u.degree)
            ra_str = ref_coord.ra.to_string(unit=u.hour, sep=":", precision=2)
            dec_str = ref_coord.dec.to_string(sep=":", precision=2)
            ref_info = f"Reference: RA={ra_str}, Dec={dec_str}"
        else:
            ref_info = f"Reference: RA=180.000000°, Dec=45.000000°"
        metadata = (
            f"Image: {os.path.basename(imagename) if imagename else 'Demo Image'}\n"
            f"Shape: (512, 512, 1, 1)\n"
            f"Beam: 10.00 × 8.00 arcsec @ 45.0°\n"
            f"{ref_info}\n"
            f"Pixel scale: 3.600 × 3.600 arcsec\n"
            f"Demo Mode: This is simulated data\n"
        )
        return metadata

    ia_tool = IA()
    ia_tool.open(imagename)
    shape = ia_tool.shape()
    csys = ia_tool.coordsys()

    try:
        beam = ia_tool.restoringbeam()
        beam_info = (
            f"Beam: {beam['major']['value']:.2f} × "
            f"{beam['minor']['value']:.2f} arcsec @ "
            f"{beam['positionangle']['value']:.1f}°"
        )
    except:
        beam_info = "No beam information"
    try:
        ra_ref = csys.referencevalue()["numeric"][0] * 180 / np.pi
        dec_ref = csys.referencevalue()["numeric"][1] * 180 / np.pi
        if ASTROPY_AVAILABLE:
            from astropy.coordinates import SkyCoord

            ref_coord = SkyCoord(ra=ra_ref * u.degree, dec=dec_ref * u.degree)
            ra_str = ref_coord.ra.to_string(unit=u.hour, sep=":", precision=2)
            dec_str = ref_coord.dec.to_string(sep=":", precision=2)
            coord_info = f"Reference: RA={ra_str}, Dec={dec_str}"
        else:
            coord_info = f"Reference: RA={ra_ref:.6f}°, Dec={dec_ref:.6f}°"
    except:
        coord_info = "No coordinate reference information"
    try:
        cdelt = csys.increment()["numeric"][0:2] * 180 / np.pi * 3600
        pixel_scale = f"Pixel scale: {abs(cdelt[0]):.3f} × {abs(cdelt[1]):.3f} arcsec"
    except:
        pixel_scale = "No pixel scale information"
    ia_tool.close()
    metadata = (
        f"Image: {os.path.basename(imagename)}\n"
        f"Shape: {shape}\n"
        f"{beam_info}\n"
        f"{coord_info}\n"
        f"{pixel_scale}\n"
    )
    return metadata


def twoD_gaussian(coords, amplitude, xo, yo, sigma_x, sigma_y, theta, offset):
    x, y = coords
    xo = float(xo)
    yo = float(yo)
    a = (np.cos(theta) ** 2) / (2 * sigma_x**2) + (np.sin(theta) ** 2) / (
        2 * sigma_y**2
    )
    b = -np.sin(2 * theta) / (4 * sigma_x**2) + np.sin(2 * theta) / (4 * sigma_y**2)
    c = (np.sin(theta) ** 2) / (2 * sigma_x**2) + (np.cos(theta) ** 2) / (
        2 * sigma_y**2
    )
    g = offset + amplitude * np.exp(
        -(a * ((x - xo) ** 2) + 2 * b * (x - xo) * (y - yo) + c * ((y - yo) ** 2))
    )
    return g.ravel()


def twoD_elliptical_ring(coords, amplitude, xo, yo, inner_r, outer_r, offset):
    x, y = coords
    dist2 = (x - xo) ** 2 + (y - yo) ** 2
    inner2 = inner_r**2
    outer2 = outer_r**2
    vals = np.full_like(dist2, offset, dtype=float)
    ring_mask = (dist2 >= inner2) & (dist2 <= outer2)
    vals[ring_mask] = offset + amplitude
    return vals.ravel()

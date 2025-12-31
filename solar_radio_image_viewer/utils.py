import os
import numpy as np

# Try to import CASA tools & tasks
try:
    # Suppress CASA logging warnings before importing casatools
    import os as _os
    _os.environ['CASA_LOGLEVEL'] = 'ERROR'
    _os.environ['CASARC'] = '/dev/null'
    
    from casatools import image as IA
    from casatasks import immath
    
    # Configure CASA logging to suppress warnings
    try:
        from casatools import logsink
        _casalog = logsink()
        _casalog.setlogfile('/dev/null')  # Redirect CASA logs to null
        _casalog.setglobal(True)
        # Filter out WARN and INFO level messages
        _casalog.filter('ERROR')
    except Exception:
        pass

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

        if "Right Ascension" in dimension_names:
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
        if "SOLAR-X" in dimension_names:
            try:
                ra_idx = int(np.where(dimension_names == "SOLAR-X")[0][0])
            except IndexError:
                raise ValueError("SOLAR-X axis not found in image summary.")
            try:
                dec_idx = int(np.where(dimension_names == "SOLAR-Y")[0][0])
            except IndexError:
                raise ValueError("SOLAR-Y axis not found in image summary.")

            if "Stokes" in dimension_names:
                stokes_idx = int(np.where(dimension_names == "Stokes")[0][0])
                if dimension_shapes[stokes_idx] == 1:
                    single_stokes_flag = True
            else:
                # Assume single stokes; set index to 0
                stokes_idx = None
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
        if freq_idx is not None:
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
        if freq_idx is not None:
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
        if freq_idx is not None:
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
        if freq_idx is not None:
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
        # Get Q and U data
        slice_list_Q = [slice(None)] * n_dims
        slice_list_U = [slice(None)] * n_dims
        slice_list_Q[stokes_idx] = 1
        slice_list_U[stokes_idx] = 2
        slice_list_Q[freq_idx] = 0
        slice_list_U[freq_idx] = 0
        pix_Q = data[tuple(slice_list_Q)]
        pix_U = data[tuple(slice_list_U)]
        
        # Calculate polarized intensity for thresholding
        p_intensity = np.sqrt(pix_Q**2 + pix_U**2)
        
        # Estimate RMS for polarized intensity using L (linear polarization) estimation
        # We use Q RMS as an approximation since we can't directly estimate L RMS
        q_rms = estimate_rms_near_Sun(imagename, "Q", rms_box)
        u_rms = estimate_rms_near_Sun(imagename, "U", rms_box)
        p_rms = np.sqrt(q_rms**2 + u_rms**2)
        
        # Calculate polarization angle: 0.5 * arctan2(U, Q) in degrees
        pix = 0.5 * np.arctan2(pix_U, pix_Q) * 180 / np.pi
        
        # Apply threshold mask - only show where polarized intensity is significant
        mask = p_intensity < (thres * p_rms)
        pix[mask] = np.nan
        
        # Handle any infinite values by setting them to NaN
        pix = np.where(np.isinf(pix), np.nan, pix)
        
        # Remove pixels away from the Sun
        pix = remove_pixels_away_from_sun(pix, csys, 55)

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
    summary = ia_tool.summary(list=False, verbose=True)
    metadata = ""
    if "messages" in summary:
        mds = summary["messages"]
        for i, md in enumerate(mds, start=1):
            clean_message = md.strip()
            metadata += f"\n{clean_message}\n"
    else:
        metadata = "No metadata available"

    """shape = ia_tool.shape()
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
    )"""
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
        
        is_fits = imagename.endswith('.fits') or imagename.endswith('.fts')
        
        header_info = {}
        
        if is_fits:
            # FITS file
            with fits.open(imagename) as hdul:
                header = hdul[0].header
                if flux_data is None:
                    flux_data = hdul[0].data
                
                # Get beam major/minor (degrees -> arcsec)
                if 'BMAJ' in header and 'BMIN' in header:
                    major = header['BMAJ'] * 3600
                    minor = header['BMIN'] * 3600
                else:
                    return None, "Beam parameters (BMAJ/BMIN) not found in header"
                
                # Get frequency (Hz -> GHz)
                freq_hz = None
                for key in ['CRVAL3', 'CRVAL4', 'FREQ', 'RESTFRQ']:
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
                    'major': major,
                    'minor': minor,
                    'freq_ghz': freq_ghz,
                    'original_header': header.copy()
                }
        else:
            # CASA image
            if not CASA_AVAILABLE:
                return None, "CASA tools not available for CASA image"
            
            ia = IA()
            ia.open(imagename)
            
            if flux_data is None:
                flux_data = ia.getchunk()
                # Squeeze to 2D for display (keep original for full Stokes save)
                if flux_data.ndim == 4:
                    flux_data = flux_data[:, :, 0, 0]  # Take first Stokes and freq
                elif flux_data.ndim == 3:
                    flux_data = flux_data[:, :, 0]  # Take first plane
            
            # Get beam info
            beam = ia.restoringbeam()
            if beam and 'major' in beam:
                major = beam['major']['value']
                minor = beam['minor']['value']
                if beam['major']['unit'] == 'arcsec':
                    pass  # already in arcsec
                elif beam['major']['unit'] == 'deg':
                    major *= 3600
                    minor *= 3600
            else:
                ia.close()
                return None, "Beam parameters not found in CASA image"
            
            # Get frequency
            csys = ia.coordsys()
            units = csys.units()
            refval = csys.referencevalue()['numeric']
            
            freq_hz = None
            for i, unit in enumerate(units):
                if unit == 'Hz':
                    freq_hz = refval[i]
                    break
            
            ia.close()
            
            if freq_hz is None:
                return None, "Frequency not found in CASA image"
            
            freq_ghz = freq_hz / 1e9
            
            header_info = {
                'major': major,
                'minor': minor,
                'freq_ghz': freq_ghz
            }
        
        # Calculate brightness temperature
        # print(f"[TB] Beam: {header_info['major']:.2f}\" x {header_info['minor']:.2f}\", Freq: {header_info['freq_ghz']:.4f} GHz")
        tb_data = 1.222e6 * flux_data / (freq_ghz**2) / (major * minor)
        
        # print(f"[TB] Temperature range: {np.nanmin(tb_data):.2e} to {np.nanmax(tb_data):.2e} K")
        
        # Save to file if outfile specified
        if outfile is not None:
            if is_fits:
                new_header = header_info['original_header'].copy()
                new_header['BUNIT'] = 'K'
                new_header['HISTORY'] = 'Converted to brightness temperature by SolarViewer'
                
                # Ensure RESTFRQ is present (needed for downstream HPC conversion)
                if 'RESTFRQ' not in new_header:
                    freq_hz = header_info['freq_ghz'] * 1e9
                    new_header['RESTFRQ'] = freq_hz
                
                # Get original data to check for full Stokes
                original_data = fits.getdata(imagename)
                
                # Check if original is multi-Stokes (3D or 4D with Stokes axis)
                if original_data.ndim >= 3:
                    # Find number of Stokes planes
                    stokes_idx = None
                    for i in range(1, header_info['original_header'].get('NAXIS', 0) + 1):
                        if header_info['original_header'].get(f'CTYPE{i}', '').upper() == 'STOKES':
                            stokes_idx = i - 1  # 0-indexed for numpy
                            break
                    
                    if stokes_idx is not None:
                        # Full Stokes - convert all planes
                        # print(f"[TB] Converting full Stokes data (shape: {original_data.shape})")
                        tb_data_save = 1.222e6 * original_data / (freq_ghz**2) / (major * minor)
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
                new_hdu.writeto(outfile, overwrite=True)
            else:
                # For CASA, need to export first
                temp_export = outfile + '.temp_export.fits'
                ia = IA()
                ia.open(imagename)
                ia.tofits(temp_export, overwrite=True, stokeslast=False)
                ia.close()
                
                with fits.open(temp_export) as hdul:
                    original_data = hdul[0].data
                    new_header = hdul[0].header.copy()
                    new_header['BUNIT'] = 'K'
                    new_header['HISTORY'] = 'Converted to brightness temperature by SolarViewer'
                    
                    # Check for multi-Stokes
                    if original_data.ndim >= 3:
                        # Full Stokes - convert all planes
                        # print(f"[TB] Converting full Stokes CASA data (shape: {original_data.shape})")
                        tb_data_save = 1.222e6 * original_data / (freq_ghz**2) / (major * minor)
                    else:
                        if original_data.shape != tb_data.shape:
                            tb_data_save = tb_data.T
                        else:
                            tb_data_save = tb_data
                    new_hdu = fits.PrimaryHDU(data=tb_data_save, header=new_header)
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
        
        is_fits = imagename.endswith('.fits') or imagename.endswith('.fts')
        
        header_info = {}
        
        if is_fits:
            # FITS file
            with fits.open(imagename) as hdul:
                header = hdul[0].header
                if tb_data is None:
                    tb_data = hdul[0].data
                
                # Get beam major/minor (degrees -> arcsec)
                if 'BMAJ' in header and 'BMIN' in header:
                    major = header['BMAJ'] * 3600
                    minor = header['BMIN'] * 3600
                else:
                    return None, "Beam parameters (BMAJ/BMIN) not found in header"
                
                # Get frequency (Hz -> GHz)
                freq_hz = None
                for key in ['CRVAL3', 'CRVAL4', 'FREQ', 'RESTFRQ']:
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
                    'major': major,
                    'minor': minor,
                    'freq_ghz': freq_ghz,
                    'original_header': header.copy()
                }
        else:
            # CASA image
            if not CASA_AVAILABLE:
                return None, "CASA tools not available for CASA image"
            
            ia = IA()
            ia.open(imagename)
            
            if tb_data is None:
                tb_data = ia.getchunk()
                while tb_data.ndim > 2:
                    tb_data = tb_data[:, :, 0] if tb_data.shape[2] == 1 else tb_data[:, :, 0, 0]
            
            # Get beam info
            beam = ia.restoringbeam()
            if beam and 'major' in beam:
                major = beam['major']['value']
                minor = beam['minor']['value']
                if beam['major']['unit'] == 'arcsec':
                    pass
                elif beam['major']['unit'] == 'deg':
                    major *= 3600
                    minor *= 3600
            else:
                ia.close()
                return None, "Beam parameters not found in CASA image"
            
            # Get frequency
            csys = ia.coordsys()
            units = csys.units()
            refval = csys.referencevalue()['numeric']
            
            freq_hz = None
            for i, unit in enumerate(units):
                if unit == 'Hz':
                    freq_hz = refval[i]
                    break
            
            ia.close()
            
            if freq_hz is None:
                return None, "Frequency not found in CASA image"
            
            freq_ghz = freq_hz / 1e9
            
            header_info = {
                'major': major,
                'minor': minor,
                'freq_ghz': freq_ghz
            }
        
        # Calculate flux: flux = TB * freq^2 * (major * minor) / 1.222e6
        # print(f"[Flux] Beam: {header_info['major']:.2f}\" x {header_info['minor']:.2f}\", Freq: {header_info['freq_ghz']:.4f} GHz")
        flux_data = tb_data * (freq_ghz**2) * (major * minor) / 1.222e6
        
        # print(f"[Flux] Flux range: {np.nanmin(flux_data):.2e} to {np.nanmax(flux_data):.2e} Jy/beam")
        
        # Save to file if outfile specified
        if outfile is not None:
            if is_fits:
                new_header = header_info['original_header'].copy()
                new_header['BUNIT'] = 'Jy/beam'
                new_header['HISTORY'] = 'Converted from brightness temperature by SolarViewer'
                
                # Ensure RESTFRQ is present (needed for downstream HPC conversion)
                if 'RESTFRQ' not in new_header:
                    freq_hz = header_info['freq_ghz'] * 1e9
                    new_header['RESTFRQ'] = freq_hz
                
                # Get original data to check shape
                original_data = fits.getdata(imagename)
                
                # Handle multi-Stokes
                if original_data.ndim >= 3:
                    stokes_idx = None
                    for i in range(1, header_info['original_header'].get('NAXIS', 0) + 1):
                        if header_info['original_header'].get(f'CTYPE{i}', '').upper() == 'STOKES':
                            stokes_idx = i - 1
                            break
                    
                    if stokes_idx is not None:
                        # print(f"[Flux] Converting full Stokes data (shape: {original_data.shape})")
                        flux_data_save = original_data * (freq_ghz**2) * (major * minor) / 1.222e6
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
                new_hdu.writeto(outfile, overwrite=True)
            else:
                # For CASA, need to export first
                temp_export = outfile + '.temp_export.fits'
                ia = IA()
                ia.open(imagename)
                ia.tofits(temp_export, overwrite=True, stokeslast=False)
                ia.close()
                
                with fits.open(temp_export) as hdul:
                    original_data = hdul[0].data
                    new_header = hdul[0].header.copy()
                    new_header['BUNIT'] = 'Jy/beam'
                    new_header['HISTORY'] = 'Converted from brightness temperature by SolarViewer'
                    
                    if original_data.ndim >= 3:
                        # print(f"[Flux] Converting full Stokes CASA data (shape: {original_data.shape})")
                        flux_data_save = original_data * (freq_ghz**2) * (major * minor) / 1.222e6
                    else:
                        if original_data.shape != flux_data.shape:
                            flux_data_save = flux_data.T
                        else:
                            flux_data_save = flux_data
                    
                    new_hdu = fits.PrimaryHDU(data=flux_data_save, header=new_header)
                    new_hdu.writeto(outfile, overwrite=True)
                
                if os.path.exists(temp_export):
                    os.remove(temp_export)
            
            # print(f"[Flux] Saved flux map to: {outfile}")
        
        return flux_data, header_info
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return None, str(e)

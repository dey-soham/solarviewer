import os
import tempfile
import numpy as np

# Try to import CASA tools (casatasks now run via subprocess)
try:
    # Suppress CASA logging warnings before importing casatools
    import os as _os

    _os.environ["CASA_LOGLEVEL"] = "ERROR"
    _os.environ["CASARC"] = "/dev/null"

    from casatools import image as IA

    # Note: casatasks (immath) is now run via subprocess - see run_immath_subprocess()

    # Configure CASA logging to suppress warnings
    try:
        from casatools import logsink

        _casalog = logsink()
        _casalog.setlogfile("/dev/null")  # Redirect CASA logs to null
        _casalog.setglobal(True)
        # Filter out WARN and INFO level messages
        _casalog.filter("ERROR")
    except Exception:
        pass

    CASA_AVAILABLE = True
except ImportError:
    print(
        "WARNING: CASA tools not found. This application requires CASA to be installed."
    )
    CASA_AVAILABLE = False
    IA = None


def run_immath_subprocess(imagename, outfile, mode="lpoli"):
    """Run immath in a subprocess to avoid memory issues with casatasks."""
    import subprocess
    import sys

    imagename = os.path.abspath(imagename)
    outfile = os.path.abspath(outfile)

    script = f"""
import sys
from casatasks import immath
try:
    immath(imagename="{imagename}", outfile="{outfile}", mode="{mode}")
    print("SUCCESS")
except Exception as e:
    print(f"ERROR: {{e}}", file=sys.stderr)
    sys.exit(1)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=os.getcwd() if os.access(os.getcwd(), os.W_OK) else tempfile.gettempdir(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"immath failed: {result.stderr}")
    return True


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


def get_available_stokes(imagename):
    """
    Detect available Stokes parameters from a CASA image or FITS file.

    Parameters:
        imagename : str
            Path to the CASA image directory or FITS file.

    Returns:
        list: Available Stokes parameters, e.g., ["I"] or ["I", "Q", "U", "V"]
    """
    all_stokes = ["I", "Q", "U", "V"]

    # Check if it's a FITS file
    is_fits = imagename.lower().endswith(".fits") or imagename.lower().endswith(".fts")

    if is_fits and ASTROPY_AVAILABLE:
        try:
            from astropy.io import fits

            with fits.open(imagename, memmap=True) as hdul:
                header = hdul[0].header
                ndim = header.get("NAXIS", 0)

                # Find Stokes axis
                for i in range(1, ndim + 1):
                    ctype = header.get(f"CTYPE{i}", "").lower()
                    if ctype == "stokes":
                        num_stokes = header.get(f"NAXIS{i}", 1)
                        return all_stokes[:num_stokes]

                # No Stokes axis found - assume single Stokes I
                return ["I"]
        except Exception as e:
            print(f"[WARNING] Could not detect Stokes from FITS: {e}")
            return ["I"]

    elif CASA_AVAILABLE:
        try:
            ia_tool = IA()
            ia_tool.open(imagename)
            summary = ia_tool.summary()
            ia_tool.close()

            dimension_names = summary.get("axisnames")
            dimension_shapes = summary.get("shape")

            if dimension_names is None:
                return ["I"]

            # Convert to list for case-insensitive search
            dimension_names_lower = [str(name).lower() for name in dimension_names]

            # Find Stokes axis
            if "stokes" in dimension_names_lower:
                stokes_idx = dimension_names_lower.index("stokes")
                num_stokes = dimension_shapes[stokes_idx]
                return all_stokes[:num_stokes]

            # No Stokes axis found - assume single Stokes I
            return ["I"]
        except Exception as e:
            print(f"[WARNING] Could not detect Stokes from CASA image: {e}")
            return ["I"]

    # Fallback
    return ["I"]


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
    downsample=1,
    target_size=0,
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
      downsample : int, optional
         Fixed factor by which to downsample. Default is 1 (no downsampling).
         If target_size is set, this is ignored.
      target_size : int, optional
         Target maximum dimension in pixels. If > 0, downsample factor is calculated
         automatically to achieve approximately this size. Default is 0 (disabled).

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

        # Calculate smart downsample factor based on image dimensions
        if target_size > 0 and len(dimension_shapes) >= 2:
            # Get spatial dimensions (first two axes are typically RA/Dec or X/Y)
            max_spatial_dim = max(dimension_shapes[0], dimension_shapes[1])
            if max_spatial_dim > target_size:
                # Calculate factor to achieve target size
                downsample = max(1, int(np.ceil(max_spatial_dim / target_size)))

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

            # Use strided reading for fast downsampling (reads every Nth pixel from disk)
            # IMPORTANT: Only downsample spatial axes, not Stokes or Frequency
            if downsample > 1:
                inc = [1] * len(dimension_shapes)  # Start with no downsampling
                inc[ra_idx] = downsample  # Downsample RA axis
                inc[dec_idx] = downsample  # Downsample Dec axis
                # Keep Stokes and Frequency at 1 to read all values
                data = ia_tool.getchunk(inc=inc)
            else:
                data = ia_tool.getchunk()
            psf = ia_tool.restoringbeam()
            csys = ia_tool.coordsys()

            # Adjust coordinate system for downsampled data
            if downsample > 1:
                # Get current increment and reference pixel
                current_inc = csys.increment()
                current_refpix = csys.referencepixel()

                # Scale increment (pixel size) by downsample factor
                new_inc_vals = [v * downsample for v in current_inc["numeric"]]
                csys.setincrement(value=new_inc_vals)

                # Adjust reference pixel position (divide by downsample factor)
                new_refpix_vals = [(v / downsample) for v in current_refpix["numeric"]]
                csys.setreferencepixel(value=new_refpix_vals)

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
            # Use strided reading for fast downsampling (reads every Nth pixel from disk)
            # IMPORTANT: Only downsample spatial axes, not Stokes or Frequency
            if downsample > 1:
                inc = [1] * len(dimension_shapes)  # Start with no downsampling
                inc[ra_idx] = downsample  # Downsample SOLAR-X axis
                inc[dec_idx] = downsample  # Downsample SOLAR-Y axis
                # Keep Stokes and Frequency at 1 to read all values
                data = ia_tool.getchunk(inc=inc)
            else:
                data = ia_tool.getchunk()
            psf = ia_tool.restoringbeam()
            csys = ia_tool.coordsys()

            # Adjust coordinate system for downsampled data
            if downsample > 1:
                # Get current increment and reference pixel
                current_inc = csys.increment()
                current_refpix = csys.referencepixel()

                # Scale increment (pixel size) by downsample factor
                new_inc_vals = [v * downsample for v in current_inc["numeric"]]
                csys.setincrement(value=new_inc_vals)

                # Adjust reference pixel position (divide by downsample factor)
                new_refpix_vals = [(v / downsample) for v in current_refpix["numeric"]]
                csys.setreferencepixel(value=new_refpix_vals)

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
            run_immath_subprocess(imagename=imagename, outfile=outfile, mode="lpoli")
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
    """
    Extract structured metadata from a FITS or CASA image file.

    Returns a dictionary with organized metadata categories:
    - observation: Date, telescope, observer, object
    - spectral: Frequency, bandwidth, spectral system
    - beam: Major/minor axis, position angle
    - image: Dimensions, pixel scale, reference position, units
    - processing: Origin software, version, weighting, history

    Also returns a formatted text representation for display.
    """
    metadata = {
        "observation": {},
        "spectral": {},
        "beam": {},
        "image": {},
        "processing": {},
        "raw_header": {},
    }

    def format_frequency(freq_hz):
        """Format frequency in appropriate units."""
        if freq_hz is None:
            return None
        freq_hz = float(freq_hz)
        if freq_hz >= 1e9:
            return f"{freq_hz / 1e9:.4f} GHz"
        elif freq_hz >= 1e6:
            return f"{freq_hz / 1e6:.3f} MHz"
        elif freq_hz >= 1e3:
            return f"{freq_hz / 1e3:.2f} kHz"
        else:
            return f"{freq_hz:.2f} Hz"

    def format_angle_arcsec(angle_deg):
        """Format angle from degrees to arcsec/arcmin as appropriate."""
        if angle_deg is None:
            return None
        angle_arcsec = abs(float(angle_deg)) * 3600
        if angle_arcsec >= 60:
            return f"{angle_arcsec / 60:.3f} arcmin"
        else:
            return f"{angle_arcsec:.3f} arcsec"

    def format_ra_hms(ra_deg):
        """Format RA in hours:minutes:seconds."""
        if ra_deg is None:
            return None
        try:
            if ASTROPY_AVAILABLE:
                from astropy.coordinates import SkyCoord

                coord = SkyCoord(ra=float(ra_deg) * u.degree, dec=0 * u.degree)
                return coord.ra.to_string(unit=u.hour, sep=":", precision=2)
            else:
                # Manual conversion
                ra_hours = float(ra_deg) / 15.0
                h = int(ra_hours)
                m = int((ra_hours - h) * 60)
                s = ((ra_hours - h) * 60 - m) * 60
                return f"{h:02d}:{m:02d}:{s:05.2f}"
        except:
            return f"{ra_deg:.6f}°"

    def format_dec_dms(dec_deg):
        """Format Dec in degrees:arcmin:arcsec."""
        if dec_deg is None:
            return None
        try:
            if ASTROPY_AVAILABLE:
                from astropy.coordinates import SkyCoord

                coord = SkyCoord(ra=0 * u.degree, dec=float(dec_deg) * u.degree)
                return coord.dec.to_string(sep=":", precision=2)
            else:
                # Manual conversion
                sign = "+" if dec_deg >= 0 else "-"
                dec_deg = abs(float(dec_deg))
                d = int(dec_deg)
                m = int((dec_deg - d) * 60)
                s = ((dec_deg - d) * 60 - m) * 60
                return f"{sign}{d:02d}:{m:02d}:{s:05.2f}"
        except:
            return f"{dec_deg:.6f}°"

    def format_datetime(date_str):
        """Format observation date/time nicely."""
        if not date_str:
            return None
        try:
            from datetime import datetime

            # Handle various date formats
            date_str = str(date_str).strip()
            # Try ISO format with fractional seconds
            if "." in date_str:
                # Handle fractional seconds by truncating
                base, frac = date_str.rsplit(".", 1)
                # Limit fractional seconds to 6 digits for microseconds
                frac = frac[:6].ljust(6, "0")
                date_str = f"{base}.{frac}"
                dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S.%f")
            else:
                dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S")
            return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        except:
            return str(date_str)

    is_fits = imagename.endswith(".fits") or imagename.endswith(".fts")

    try:
        if is_fits and ASTROPY_AVAILABLE:
            # Use astropy for FITS files
            from astropy.io import fits

            with fits.open(imagename) as hdul:
                header = hdul[0].header

                # Store raw header for reference
                for key in header.keys():
                    if key and key not in ("", "COMMENT", "HISTORY"):
                        try:
                            metadata["raw_header"][key] = str(header[key])
                        except:
                            pass

                # Observation info
                metadata["observation"]["Date/Time"] = format_datetime(
                    header.get("DATE-OBS")
                )
                metadata["observation"]["Telescope"] = header.get(
                    "TELESCOP", header.get("INSTRUME")
                )
                metadata["observation"]["Observer"] = header.get("OBSERVER")
                metadata["observation"]["Object"] = header.get("OBJECT")
                metadata["observation"]["Origin"] = header.get("ORIGIN")

                # Spectral info
                freq_hz = None
                for key in ["CRVAL3", "CRVAL4", "RESTFRQ", "FREQ"]:
                    if key in header:
                        try:
                            val = float(header[key])
                            if val > 1e6:  # Likely Hz
                                freq_hz = val
                                break
                        except:
                            pass
                if freq_hz:
                    metadata["spectral"]["Frequency"] = format_frequency(freq_hz)
                    metadata["spectral"]["Frequency (Hz)"] = f"{freq_hz:.0f}"
                    # Wavelength
                    c = 299792458  # m/s
                    wavelength_m = c / freq_hz
                    if wavelength_m >= 1:
                        metadata["spectral"]["Wavelength"] = f"{wavelength_m:.2f} m"
                    elif wavelength_m >= 0.01:
                        metadata["spectral"][
                            "Wavelength"
                        ] = f"{wavelength_m * 100:.2f} cm"
                    else:
                        metadata["spectral"][
                            "Wavelength"
                        ] = f"{wavelength_m * 1000:.3f} mm"

                metadata["spectral"]["Spectral System"] = header.get("SPECSYS")

                # Beam info
                if "BMAJ" in header and "BMIN" in header:
                    bmaj_arcsec = float(header["BMAJ"]) * 3600
                    bmin_arcsec = float(header["BMIN"]) * 3600

                    # Smart beam axis formatting
                    def smart_beam_format(arcsec_val):
                        if arcsec_val >= 60:
                            return f"{arcsec_val / 60:.2f}'"
                        else:
                            return f'{arcsec_val:.2f}"'

                    metadata["beam"]["Major Axis"] = smart_beam_format(bmaj_arcsec)
                    metadata["beam"]["Minor Axis"] = smart_beam_format(bmin_arcsec)
                    metadata["beam"][
                        "Beam Area"
                    ] = f"{np.pi * bmaj_arcsec * bmin_arcsec / (4 * np.log(2)):.2f} arcsec²"
                    if "BPA" in header:
                        metadata["beam"][
                            "Position Angle"
                        ] = f"{float(header['BPA']):.1f}°"

                # Image properties
                naxis1 = header.get("NAXIS1", 0)
                naxis2 = header.get("NAXIS2", 0)
                metadata["image"]["Dimensions"] = f"{naxis1} × {naxis2} pixels"

                if "CDELT1" in header and "CDELT2" in header:
                    cdelt1_arcsec = abs(float(header["CDELT1"])) * 3600
                    cdelt2_arcsec = abs(float(header["CDELT2"])) * 3600

                    # Smart pixel scale formatting
                    def smart_angle_format(arcsec_val):
                        """Format angle in appropriate units."""
                        if arcsec_val >= 3600:
                            return f"{arcsec_val / 3600:.3f}°"
                        elif arcsec_val >= 60:
                            return f"{arcsec_val / 60:.3f}'"
                        else:
                            return f'{arcsec_val:.3f}"'

                    ps1 = smart_angle_format(cdelt1_arcsec)
                    ps2 = smart_angle_format(cdelt2_arcsec)
                    metadata["image"]["Pixel Scale"] = f"{ps1} × {ps2} /pixel"

                    # Smart FOV formatting
                    fov1_arcsec = cdelt1_arcsec * naxis1
                    fov2_arcsec = cdelt2_arcsec * naxis2
                    fov1 = smart_angle_format(fov1_arcsec)
                    fov2 = smart_angle_format(fov2_arcsec)
                    metadata["image"]["Field of View"] = f"{fov1} × {fov2}"

                metadata["image"][
                    "Coordinate Type"
                ] = f"{header.get('CTYPE1', 'N/A')}, {header.get('CTYPE2', 'N/A')}"

                if "CRVAL1" in header and "CRVAL2" in header:
                    ra_deg = float(header["CRVAL1"])
                    dec_deg = float(header["CRVAL2"])
                    metadata["image"]["Reference RA"] = format_ra_hms(ra_deg)
                    metadata["image"]["Reference Dec"] = format_dec_dms(dec_deg)

                metadata["image"]["Units"] = header.get("BUNIT")
                metadata["image"]["Data Type"] = f"BITPIX={header.get('BITPIX', 'N/A')}"
                metadata["image"]["Equinox"] = header.get("EQUINOX")

                # Processing info
                if header.get("ORIGIN"):
                    metadata["processing"]["Software"] = header.get("ORIGIN")

                # WSClean specific
                if "WSCVERSI" in header:
                    metadata["processing"]["WSClean Version"] = header.get("WSCVERSI")
                if "WSCWEIGH" in header:
                    metadata["processing"]["Weighting"] = header.get("WSCWEIGH")
                # if 'WSCNITER' in header:
                #    metadata['processing']['Iterations'] = f"{int(float(header['WSCNITER']))}"
                # if 'WSCGAIN' in header:
                #    metadata['processing']['Gain'] = f"{float(header['WSCGAIN']):.3f}"

                # History
                history_cards = [str(h) for h in header.get("HISTORY", [])]
                if history_cards:
                    # Combine and summarize history
                    history_text = " ".join(history_cards)
                    if len(history_text) > 500:
                        history_text = history_text[:500] + "..."
                    metadata["processing"]["History"] = history_text

        elif CASA_AVAILABLE:
            # Use CASA tools for CASA images or FITS without astropy
            ia_tool = IA()
            ia_tool.open(imagename)

            try:
                summary = ia_tool.summary(list=False, verbose=True)
                shape = ia_tool.shape()
                csys = ia_tool.coordsys()

                # Parse summary messages to extract useful fields
                # Messages are multi-line strings that need to be split first
                if "messages" in summary:
                    for msg_block in summary["messages"]:
                        # Split the message block into individual lines
                        lines = str(msg_block).split("\n")
                        for line in lines:
                            line = line.strip()
                            if not line or line.startswith("-"):
                                continue

                            # Skip table header lines
                            if (
                                line.startswith("Axis")
                                or line.startswith("0 ")
                                or line.startswith("1 ")
                                or line.startswith("2 ")
                                or line.startswith("3 ")
                            ):
                                continue

                            # Parse key: value pairs
                            if ":" in line:
                                key, _, value = line.partition(":")
                                key = key.strip()
                                value = value.strip()

                                # Skip empty values or very long values or coordinate values
                                if not value or len(value) > 150:
                                    continue

                                # Skip if value looks like a coordinate table entry
                                if value.startswith("[") and "ITRF" in value:
                                    metadata["observation"][
                                        "Telescope Position"
                                    ] = value
                                    metadata["raw_header"][key] = value
                                    continue

                                # Always add to raw_header for "All Headers" view
                                metadata["raw_header"][key] = value

                                # Also map to appropriate organized sections
                                if key in ["Object name", "OBJECT"]:
                                    metadata["observation"]["Object"] = value
                                elif key == "Image name":
                                    # Just the filename, not the full path
                                    metadata["observation"]["Image Name"] = (
                                        value.split("/")[-1] if "/" in value else value
                                    )
                                elif key == "Image type":
                                    metadata["image"]["Image Type"] = value
                                elif key == "Image quantity":
                                    metadata["image"]["Quantity"] = value
                                elif key == "Image units":
                                    metadata["image"]["Units"] = value
                                elif key in [
                                    "Spectral  reference",
                                    "Spectral reference",
                                ]:
                                    metadata["spectral"]["Reference Frame"] = value
                                elif key in ["Velocity  type", "Velocity type"]:
                                    metadata["spectral"]["Velocity Type"] = value
                                elif key in ["Direction reference"]:
                                    metadata["image"]["Direction Reference"] = value
                                elif key == "Telescope":
                                    if not metadata["observation"].get("Telescope"):
                                        metadata["observation"]["Telescope"] = value
                                elif key == "Observer":
                                    if not metadata["observation"].get("Observer"):
                                        metadata["observation"]["Observer"] = value
                                elif key == "Date observation":
                                    if not metadata["observation"].get("Date/Time"):
                                        metadata["observation"]["Date/Time"] = (
                                            format_datetime(
                                                value.replace("/", "-")
                                                .replace("-", "-", 2)
                                                .replace("/", "T", 1)
                                            )
                                        )
                                elif key == "Restoring Beam":
                                    metadata["beam"]["Restoring Beam"] = value
                                elif key in ["Pixel mask(s)", "Region(s)"]:
                                    if value and value != "None":
                                        metadata["processing"][key] = value

                # Image dimensions
                if len(shape) >= 2:
                    metadata["image"]["Dimensions"] = f"{shape[0]} × {shape[1]} pixels"

                # Coordinate info
                try:
                    refval = csys.referencevalue()["numeric"]
                    if len(refval) >= 2:
                        ra_deg = refval[0] * 180 / np.pi
                        dec_deg = refval[1] * 180 / np.pi
                        metadata["image"]["Reference RA"] = format_ra_hms(ra_deg)
                        metadata["image"]["Reference Dec"] = format_dec_dms(dec_deg)
                except:
                    pass

                # Pixel scale
                try:
                    increment = csys.increment()["numeric"]
                    if len(increment) >= 2:
                        cdelt1_arcsec = abs(increment[0]) * 180 / np.pi * 3600
                        cdelt2_arcsec = abs(increment[1]) * 180 / np.pi * 3600
                        metadata["image"][
                            "Pixel Scale"
                        ] = f"{cdelt1_arcsec:.3f} × {cdelt2_arcsec:.3f} arcsec/pixel"
                except:
                    pass

                # Frequency
                try:
                    units = csys.units()
                    refval = csys.referencevalue()["numeric"]
                    for i, unit in enumerate(units):
                        if unit == "Hz" and i < len(refval):
                            freq_hz = refval[i]
                            metadata["spectral"]["Frequency"] = format_frequency(
                                freq_hz
                            )
                            break
                except:
                    pass

                # Beam info
                try:
                    beam = ia_tool.restoringbeam()
                    if beam and "major" in beam:
                        major = beam["major"]["value"]
                        minor = beam["minor"]["value"]
                        if beam["major"]["unit"] == "deg":
                            major *= 3600
                            minor *= 3600
                        metadata["beam"]["Major Axis"] = f"{major:.2f} arcsec"
                        metadata["beam"]["Minor Axis"] = f"{minor:.2f} arcsec"
                        if "positionangle" in beam:
                            metadata["beam"][
                                "Position Angle"
                            ] = f"{beam['positionangle']['value']:.1f}°"
                except:
                    pass

                try:
                    bunit = ia_tool.brightnessunit()
                    if bunit:
                        metadata["image"]["Units"] = bunit
                except:
                    pass

                # Extract all keywords from miscinfo and store important ones
                try:
                    miscinfo = ia_tool.miscinfo()

                    # Store miscinfo keys as raw headers (only simple values, not nested structures)
                    for key, value in miscinfo.items():
                        # Skip None, empty, dicts, lists, and overly long values
                        if value is None:
                            continue
                        if isinstance(value, (dict, list, tuple)):
                            continue
                        value_str = str(value).strip()
                        if not value_str or len(value_str) > 200:
                            continue
                        # Skip if value contains newlines (multi-line output)
                        if "\n" in value_str:
                            continue
                        metadata["raw_header"][key] = value_str

                    # Observation section
                    telescope_keys = ["TELESCOP", "INSTRUME", "ANTENNA"]
                    for key in telescope_keys:
                        if key in miscinfo and miscinfo[key]:
                            metadata["observation"]["Telescope"] = miscinfo[key]
                            break

                    datetime_keys = ["DATE-OBS", "DATEOBS", "DATE_OBS", "OBSDATE"]
                    for key in datetime_keys:
                        if key in miscinfo and miscinfo[key]:
                            metadata["observation"]["Date/Time"] = format_datetime(
                                miscinfo[key]
                            )
                            break

                    if "OBSERVER" in miscinfo and miscinfo["OBSERVER"]:
                        metadata["observation"]["Observer"] = miscinfo["OBSERVER"]

                    object_keys = ["OBJECT", "SRCNAME", "TARGET"]
                    for key in object_keys:
                        if key in miscinfo and miscinfo[key]:
                            metadata["observation"]["Object"] = miscinfo[key]
                            break

                    if "ORIGIN" in miscinfo and miscinfo["ORIGIN"]:
                        metadata["observation"]["Origin"] = miscinfo["ORIGIN"]

                    # Processing section - capture software info
                    if "ORIGIN" in miscinfo and miscinfo["ORIGIN"]:
                        metadata["processing"]["Software"] = miscinfo["ORIGIN"]

                    # WSClean specific keywords
                    wsclean_keys = {
                        "WSCVERSI": "WSClean Version",
                        "WSCWEIGH": "Weighting",
                        "WSCNWLAY": "W-Layers",
                        "WSCCHANS": "Channels",
                    }
                    for key, display_name in wsclean_keys.items():
                        if key in miscinfo and miscinfo[key]:
                            metadata["processing"][display_name] = miscinfo[key]

                    # CASA/tclean specific keywords
                    casa_keys = {
                        "IMAGER": "Imager",
                        "IMAGETYP": "Image Type",
                        "PROJECT": "Project",
                    }
                    for key, display_name in casa_keys.items():
                        if key in miscinfo and miscinfo[key]:
                            metadata["processing"][display_name] = miscinfo[key]

                except Exception as e:
                    pass

            finally:
                ia_tool.close()
        else:
            metadata["observation"]["Error"] = "No FITS/CASA tools available"

    except Exception as e:
        metadata["observation"]["Error"] = f"Failed to read metadata: {str(e)}"

    # Clean up None values and empty sections
    for section in list(metadata.keys()):
        if section == "raw_header":
            continue
        metadata[section] = {
            k: v for k, v in metadata[section].items() if v is not None
        }
        if not metadata[section]:
            del metadata[section]

    return metadata


def format_metadata_text(metadata):
    """
    Format metadata dictionary as readable text.

    Parameters:
        metadata: Dictionary from get_image_metadata()

    Returns:
        Formatted text string
    """
    lines = []

    section_titles = {
        "observation": "📅 Observation",
        "spectral": "📡 Spectral Properties",
        "beam": "🎯 Beam Properties",
        "image": "🖼️ Image Properties",
        "processing": "⚙️ Processing",
    }

    for section, title in section_titles.items():
        if section in metadata and metadata[section]:
            lines.append(f"\n{title}")
            lines.append("─" * 40)
            for key, value in metadata[section].items():
                # Handle long values
                if len(str(value)) > 60:
                    lines.append(f"  {key}:")
                    lines.append(f"    {value}")
                else:
                    lines.append(f"  {key}: {value}")

    return "\n".join(lines)


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

        is_fits = imagename.endswith(".fits") or imagename.endswith(".fts")

        header_info = {}

        if is_fits:
            # FITS file
            with fits.open(imagename) as hdul:
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
                original_data = fits.getdata(imagename)

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
                ia.open(imagename)
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
            with fits.open(imagename) as hdul:
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
            ia.open(imagename)

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
                original_data = fits.getdata(imagename)

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
                ia.open(imagename)
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

import os
import tempfile
import numpy as np
import sys
import glob
from casatools import msmetadata, table, measures, quanta, image

# casatasks are now run via subprocess to avoid memory issues
import subprocess
import json
from astropy.io import fits
from astropy.wcs import WCS
import scipy.ndimage as ndi
import argparse
import multiprocessing
from multiprocessing import Pool
from functools import partial
import shutil
import hashlib


# Subprocess wrappers for casatasks to avoid segfaults
def run_casatask_subprocess(task_name, **kwargs):
    """Generic wrapper to run any casatask in a subprocess."""
    # Ensure common path keywords are absolute before passing to subprocess
    path_keywords = [
        "imagename",
        "outfile",
        "fitsimage",
        "vis",
        "outputvis",
        "fitsname",
        "infile",
        "filename",
        "mask",
    ]
    for key in path_keywords:
        if key in kwargs and kwargs[key] and isinstance(kwargs[key], str):
            kwargs[key] = os.path.abspath(kwargs[key])

    # Convert kwargs to a JSON-safe format
    kwargs_str = json.dumps(kwargs)

    script = f"""
import sys
import json
from casatasks import {task_name}

kwargs = json.loads('{kwargs_str}')
try:
    result = {task_name}(**kwargs)
    # Output result as JSON if it's serializable
    try:
        print(json.dumps({{"success": True, "result": result}}))
    except (TypeError, ValueError):
        print(json.dumps({{"success": True, "result": "completed"}}))
except Exception as e:
    print(json.dumps({{"success": False, "error": str(e)}}), file=sys.stderr)
    sys.exit(1)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=os.getcwd() if os.access(os.getcwd(), os.W_OK) else tempfile.gettempdir(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"{task_name} failed: {result.stderr}")

    try:
        output = json.loads(result.stdout.strip())
        return output.get("result")
    except json.JSONDecodeError:
        return None


def imhead_subprocess(imagename, mode="list", hdkey=None, hdvalue=None):
    """Run imhead in a subprocess."""
    kwargs = {"imagename": imagename, "mode": mode}
    if hdkey is not None:
        kwargs["hdkey"] = hdkey
    if hdvalue is not None:
        kwargs["hdvalue"] = hdvalue
    return run_casatask_subprocess("imhead", **kwargs)


def imsmooth_subprocess(imagename, targetres, major, minor, pa, outfile):
    """Run imsmooth in a subprocess."""
    return run_casatask_subprocess(
        "imsmooth",
        imagename=imagename,
        targetres=targetres,
        major=major,
        minor=minor,
        pa=pa,
        outfile=outfile,
    )


def imstat_subprocess(imagename, box=None):
    """Run imstat in a subprocess."""
    kwargs = {"imagename": imagename}
    if box is not None:
        kwargs["box"] = box
    return run_casatask_subprocess("imstat", **kwargs)


def imfit_subprocess(imagename, box=None):
    """Run imfit in a subprocess."""
    kwargs = {"imagename": imagename}
    if box is not None:
        kwargs["box"] = box
    return run_casatask_subprocess("imfit", **kwargs)


def exportfits_subprocess(
    imagename, fitsimage, dropdeg=False, dropstokes=False, overwrite=True
):
    """Run exportfits in a subprocess."""
    return run_casatask_subprocess(
        "exportfits",
        imagename=imagename,
        fitsimage=fitsimage,
        dropdeg=dropdeg,
        dropstokes=dropstokes,
        overwrite=overwrite,
    )


def imsubimage_subprocess(imagename, outfile, stokes=None, dropdeg=False):
    """Run imsubimage in a subprocess."""
    kwargs = {"imagename": imagename, "outfile": outfile, "dropdeg": dropdeg}
    if stokes is not None:
        kwargs["stokes"] = stokes
    return run_casatask_subprocess("imsubimage", **kwargs)


def fixvis_subprocess(vis, outputvis, phasecenter, datacolumn="all"):
    """Run fixvis in a subprocess."""
    return run_casatask_subprocess(
        "fixvis",
        vis=vis,
        outputvis=outputvis,
        phasecenter=phasecenter,
        datacolumn=datacolumn,
    )


class SolarPhaseCenter:
    """
    Class to calculate and apply phase shifts to solar images

    This class contains methods to:
    1. Calculate the difference between solar center and phase center
    2. Apply the phase shift to align the solar center with the phase center

    Parameters
    ----------
    msname : str
        Name of the measurement set
    cellsize : float
        Cell size of the image in arcsec
    imsize : int
        Size of the image in pixels
    """

    def __init__(self, msname=None, cellsize=None, imsize=None):
        self.msname = msname
        self.cellsize = cellsize  # in arcsec
        self.imsize = imsize

        # Get working directory
        self.cwd = os.getcwd()

        # Initialize rms boxes with default values
        self.rms_box = "50,50,100,75"
        self.rms_box_nearsun = "40,40,80,60"

        # Setup RMS box for calculations (near Sun and general)
        if imsize is not None and cellsize is not None:
            self.setup_rms_boxes(imsize, cellsize)

    def get_observation_time(self, imagename):
        """
        Extract observation time from FITS header or CASA image.

        Parameters
        ----------
        imagename : str
            Path to FITS file or CASA image

        Returns
        -------
        str or None
            ISO format observation time string, or None if not found
        """
        obs_time = None

        try:
            if os.path.isdir(imagename):
                try:
                    ia = image()
                    ia.open(imagename)

                    # Get observation date from image miscinfo
                    misc = ia.miscinfo()
                    for key in ["date-obs", "DATE-OBS", "obsdate", "OBSDATE"]:
                        if key in misc and misc[key]:
                            obs_time = misc[key]
                            break

                    # Try to extract from the summary which has the observation date
                    if obs_time is None:
                        summary = ia.summary(list=False)  # Suppress output
                        if isinstance(summary, dict):
                            # Check for 'obsdate' key in summary
                            if "obsdate" in summary:
                                date_dict = summary["obsdate"]
                                if isinstance(date_dict, dict) and "m0" in date_dict:
                                    from astropy.time import Time

                                    mjd = date_dict["m0"]["value"] / 86400.0
                                    obs_time = Time(mjd, format="mjd").isot

                    ia.close()

                    # If we still don't have obs_time, try reading from the exported FITS
                    # since exportfits often preserves DATE-OBS correctly
                    if obs_time is None and "working_fits" in dir():
                        # Will be handled by FITS path below
                        pass

                except Exception as e:
                    print(f"Error reading CASA image for observation time: {e}")
            else:
                header = fits.getheader(imagename)

                # Check common keywords for observation time
                time_keywords = [
                    "DATE-OBS",
                    "DATE_OBS",
                    "DATEOBS",
                    "DATE-BEG",
                    "DATE-AVG",
                    "DATE-END",
                    "OBSDATE",
                    "OBS-DATE",
                ]

                for key in time_keywords:
                    if key in header and header[key]:
                        obs_time = header[key]
                        break

                # If only TIME-OBS is available separately, combine with DATE-OBS
                if obs_time and "T" not in obs_time:
                    for time_key in ["TIME-OBS", "TIME_OBS", "TIMEOBS"]:
                        if time_key in header and header[time_key]:
                            obs_time = f"{obs_time}T{header[time_key]}"
                            break

                # Handle MJD format
                if obs_time is None:
                    for mjd_key in ["MJD-OBS", "MJD_OBS", "MJDOBS"]:
                        if mjd_key in header and header[mjd_key]:
                            from astropy.time import Time

                            obs_time = Time(header[mjd_key], format="mjd").isot
                            break

        except Exception as e:
            print(f"Error extracting observation time: {e}")

        if obs_time:
            print(f"Observation time extracted: {obs_time}")
        else:
            print("Warning: Could not extract observation time from image header")

        return obs_time

    def get_solar_position(self, obs_time):
        """
        Get the true RA/DEC of the Sun at the given observation time using ephemeris.

        Parameters
        ----------
        obs_time : str or astropy.time.Time
            Observation time as ISO format string or Time object

        Returns
        -------
        tuple (float, float)
            Solar RA and DEC in degrees
        """
        from astropy.coordinates import get_sun
        from astropy.time import Time

        if not isinstance(obs_time, Time):
            obs_time = Time(obs_time, scale="utc")

        sun = get_sun(obs_time)

        ra_deg = sun.ra.deg
        dec_deg = sun.dec.deg

        print(
            f"True solar position (ephemeris): RA = {ra_deg:.6f} deg, DEC = {dec_deg:.6f} deg"
        )

        return ra_deg, dec_deg

    def setup_rms_boxes(self, imsize, cellsize):
        """
        Set up RMS boxes for calculations

        Parameters
        ----------
        imsize : int
            Size of the image in pixels
        cellsize : float
            Cell size in arcsec
        """
        # Ensure parameters are valid
        if imsize <= 0 or cellsize <= 0:
            print("Warning: Invalid image size or cell size. Using default RMS boxes.")
            self.rms_box = "50,50,100,75"
            self.rms_box_nearsun = "40,40,80,60"
            return

        # General RMS box - set to a reasonable size relative to the image
        rms_width = min(int(imsize / 4), imsize - 50)
        self.rms_box = f"50,50,{min(imsize-10, 100)},{min(rms_width, 100)}"

        try:
            # Calculate reasonable values for boxcenter_y and ywidth
            # Using a safer approach to avoid negative values
            center_y = int(imsize / 2)

            # Calculate offsets based on solar diameter, but ensure they're reasonable
            y_offset = min(int(3 * 3600 / max(1, cellsize)), int(imsize / 4))
            boxcenter_y = max(y_offset + 10, center_y - y_offset)

            # Limit ywidth to prevent box from going outside image
            ywidth = min(int(3600 / max(1, cellsize)), int(imsize / 6))

            # Reference center of the image for x coordinate
            boxcenter_x = center_y

            # Calculate safe box bounds (ensure at least 10 pixels from each edge)
            safe_margin = 10
            x_min = safe_margin
            y_min = safe_margin
            x_max = imsize - safe_margin
            y_max = imsize - safe_margin

            # Ensure the box is inside the image and has reasonable size
            box_width = min(int(imsize / 5), (x_max - x_min) / 2)
            box_height = min(ywidth, (y_max - y_min) / 2)

            # Define box coordinates ensuring they're within image bounds
            x1 = max(x_min, boxcenter_x - box_width)
            y1 = max(y_min, boxcenter_y - box_height)
            x2 = min(x_max, boxcenter_x + box_width)
            y2 = min(y_max, boxcenter_y + box_height)

            # Ensure the box has minimum dimensions
            if x2 - x1 < 20:
                x2 = min(x_max, x1 + 20)
            if y2 - y1 < 20:
                y2 = min(y_max, y1 + 20)

            self.rms_box_nearsun = f"{int(x1)},{int(y1)},{int(x2)},{int(y2)}"
            print(f"RMS box near sun: {self.rms_box_nearsun}")
        except Exception as e:
            print(f"Error setting up RMS boxes: {e}")
            # Fallback to a very conservative box that should work for any image
            self.rms_box_nearsun = (
                f"{safe_margin},{safe_margin},{imsize-safe_margin},{imsize-safe_margin}"
            )

    def get_phasecenter(self):
        """
        Get the phase center of the MS

        Returns
        -------
        tuple
            (radec_str, radeg, decdeg) - RA/DEC as string and degrees
        """
        if self.msname is None:
            print("Error: MS name not provided")
            return None, None, None

        ms_meta = msmetadata()
        ms_meta.open(self.msname)

        # Get field ID 0 (assuming single field)
        t = table()
        t.open(f"{self.msname}/FIELD")
        direction = t.getcol("PHASE_DIR")
        t.close()

        # Convert to degrees
        radeg = np.degrees(direction[0][0][0])
        decdeg = np.degrees(direction[0][0][1])

        # Format as strings
        ra_hms = self.deg2hms(radeg)
        dec_dms = self.deg2dms(decdeg)

        ms_meta.close()
        return [ra_hms, dec_dms], radeg, decdeg

    def deg2hms(self, ra_deg):
        """
        Convert RA from degrees to HH:MM:SS.SSS format

        Parameters
        ----------
        ra_deg : float
            RA in degrees

        Returns
        -------
        str
            RA in HH:MM:SS.SSS format
        """
        ra_hour = ra_deg / 15.0
        ra_h = int(ra_hour)
        ra_m = int((ra_hour - ra_h) * 60)
        ra_s = ((ra_hour - ra_h) * 60 - ra_m) * 60
        return f"{ra_h:02d}h{ra_m:02d}m{ra_s:.3f}s"

    def deg2dms(self, dec_deg):
        """
        Convert DEC from degrees to DD:MM:SS.SSS format

        Parameters
        ----------
        dec_deg : float
            DEC in degrees

        Returns
        -------
        str
            DEC in DD:MM:SS.SSS format
        """
        dec_sign = "+" if dec_deg >= 0 else "-"
        dec_deg = abs(dec_deg)
        dec_d = int(dec_deg)
        dec_m = int((dec_deg - dec_d) * 60)
        dec_s = ((dec_deg - dec_d) * 60 - dec_m) * 60
        return f"{dec_sign}{dec_d:02d}d{dec_m:02d}m{dec_s:.3f}s"

    def negative_box(self, max_pix, imsize=None, box_width=3):
        """
        Create a box around the maximum pixel for searching

        Parameters
        ----------
        max_pix : list
            Maximum pixel [xxmax, yymax]
        imsize : int
            Image size (if None, use self.imsize)
        box_width : float
            Box width in degrees (default: 3 degrees)

        Returns
        -------
        str
            CASA box format 'xblc,yblc,xrtc,yrtc'
        """
        if imsize is None:
            imsize = self.imsize

        if self.cellsize is None:
            print("Error: Cell size not provided")
            return "0,0,0,0"

        max_pix_xx = max_pix[0]
        max_pix_yy = max_pix[1]

        # Calculate box length in pixels (box_width in degrees, cellsize in arcsec)
        box_length = (float(box_width) * 3600.0) / self.cellsize

        xblc = max(0, int(max_pix_xx - (box_length / 2.0)))
        yblc = max(0, int(max_pix_yy - (box_length / 2.0)))
        xrtc = min(imsize - 1, int(max_pix_xx + (box_length / 2.0)))
        yrtc = min(imsize - 1, int(max_pix_yy + (box_length / 2.0)))

        return f"{xblc},{yblc},{xrtc},{yrtc}"

    def create_circular_mask(self, h, w, center=None, radius=None):
        """
        Create a circular mask for an image

        Parameters
        ----------
        h, w : int
            Height and width of the image
        center : tuple
            (x, y) center of the circle
        radius : float
            Radius of the circle

        Returns
        -------
        ndarray
            Boolean mask array (True inside circle, False outside)
        """
        if center is None:
            center = (int(w / 2), int(h / 2))
        if radius is None:
            radius = min(center[0], center[1], w - center[0], h - center[1])

        Y, X = np.ogrid[:h, :w]
        dist_from_center = np.sqrt((X - center[0]) ** 2 + (Y - center[1]) ** 2)

        mask = dist_from_center <= radius
        return mask

    def calc_sun_dia(self):
        """
        Calculate the apparent diameter of the sun in arcmin

        Returns
        -------
        float
            Sun diameter in arcmin
        """
        # Standard solar diameter in arcmin at 1 AU
        standard_dia = 32.0

        if self.msname is None:
            return standard_dia

        try:
            # Get the observation time
            ms_meta = msmetadata()
            ms_meta.open(self.msname)

            time_mid = ms_meta.timesforfield(0)[int(len(ms_meta.timesforfield(0)) / 2)]

            # Setup measures and quanta tools
            me = measures()
            qa = quanta()

            # Set the reference frame
            me.doframe(me.epoch("UTC", qa.quantity(time_mid, "s")))
            me.doframe(me.observatory("LOFAR"))  # Assuming LOFAR observations

            # Get the sun position
            sun_pos = me.direction("SUN")

            # Get the distance to sun in AU
            sun_dist = me.separation(me.direction("SUN"), me.direction("SUN_DIST"))
            sun_dist_au = qa.convert(sun_dist, "AU")["value"]

            # Scale the solar diameter
            sun_dia = standard_dia / sun_dist_au

            ms_meta.close()
            return sun_dia
        except Exception as e:
            print(f"Error calculating sun diameter: {e}")
            return standard_dia

    def cal_solar_phaseshift(self, imagename, fit_gaussian=True, sigma=10, is_hpc=None):
        """
        Calculate the phase shift needed to align the apparent solar position with the true position.

        At low frequencies, ionospheric refraction shifts the apparent solar position.
        This method finds WHERE THE SUN APPEARS (apparent position via Gaussian/centroid)
        and WHERE IT SHOULD BE (true position from ephemeris, or (0,0) for HPC images).

        Parameters
        ----------
        imagename : str
            Name of the image
        fit_gaussian : bool
            Perform Gaussian fitting to unresolved Sun to estimate apparent center
        sigma : float
            If Gaussian fitting is not used, threshold for estimating center of mass
        is_hpc : bool or None
            If True, image is in helioprojective coordinates (target (0,0) Solar-X/Y).
            If False, image is in RA/Dec (target ephemeris position).
            If None, will auto-detect from image headers.

        Returns
        -------
        dict
            Dictionary containing:
            - 'true_ra': float - True solar RA in degrees (from ephemeris) or 0 for HPC
            - 'true_dec': float - True solar DEC in degrees (from ephemeris) or 0 for HPC
            - 'apparent_pix_x': int - X pixel position of apparent sun center
            - 'apparent_pix_y': int - Y pixel position of apparent sun center
            - 'apparent_ra': float - Apparent RA in degrees (from image)
            - 'apparent_dec': float - Apparent DEC in degrees (from image)
            - 'needs_shift': bool - Whether phase shift is required
            - 'is_hpc': bool - Whether the image is in helioprojective coordinates
        """
        result = {
            "true_ra": None,
            "true_dec": None,
            "apparent_pix_x": None,
            "apparent_pix_y": None,
            "apparent_ra": None,
            "apparent_dec": None,
            "needs_shift": False,
            "is_hpc": False,
        }

        # Convert CASA image to FITS at the start for unified processing
        is_casa_image = os.path.isdir(imagename)
        temp_fits_file = None
        working_fits = imagename

        if is_casa_image:
            print("Converting CASA image to FITS for processing...")
            image_path = os.path.dirname(os.path.abspath(imagename))
            temp_fits_file = f"{image_path}/temp_phase_calc_{os.getpid()}.fits"
            try:
                exportfits_subprocess(
                    imagename=imagename,
                    fitsimage=temp_fits_file,
                    dropdeg=False,
                    dropstokes=False,
                    overwrite=True,
                )
                working_fits = temp_fits_file
            except Exception as e:
                print(f"Error exporting CASA to FITS: {e}")
                return result
        # Now work exclusively with FITS file
        header = fits.getheader(working_fits)

        # Auto-detect HPC if not specified
        if is_hpc is None:
            ctype1 = header.get("CTYPE1", "").upper()
            ctype2 = header.get("CTYPE2", "").upper()
            if (
                "HPLN" in ctype1
                or "HPLT" in ctype2
                or "SOLAR" in ctype1
                or "SOLAR" in ctype2
            ):
                is_hpc = True
                print("Detected helioprojective coordinates (Solar-X/Y)")
            else:
                is_hpc = False

        result["is_hpc"] = is_hpc

        # Get current phase center (reference RA/DEC)
        if self.msname:
            radec_str, radeg, decdeg = self.get_phasecenter()
        else:
            radeg = header.get("CRVAL1", 0)
            decdeg = header.get("CRVAL2", 0)

        # Extract cell size and imsize from FITS header
        if self.cellsize is None or self.imsize is None:
            try:
                cunit1 = header.get("CUNIT1", "deg").lower()
                cdelt1 = np.abs(header.get("CDELT1", 1))

                if "arcsec" in cunit1:
                    self.cellsize = cdelt1
                else:
                    # Assume degrees
                    self.cellsize = cdelt1 * 3600.0

                self.imsize = header.get("NAXIS1", 512)
                self.setup_rms_boxes(self.imsize, self.cellsize)
            except Exception as e:
                print(f"Error extracting image properties: {e}")
                result["true_ra"] = radeg
                result["true_dec"] = decdeg
                return result

        # Step 1: Get TRUE solar position
        if is_hpc:
            # For HPC images, target position is (0,0) Solar-X/Y
            print("Target position: Solar-X = 0 arcsec, Solar-Y = 0 arcsec")
            result["true_ra"] = 0.0
            result["true_dec"] = 0.0
        else:
            # For RA/Dec images, get position from ephemeris
            obs_time = self.get_observation_time(working_fits)
            if obs_time:
                try:
                    true_ra, true_dec = self.get_solar_position(obs_time)
                    result["true_ra"] = true_ra
                    result["true_dec"] = true_dec
                except Exception as e:
                    print(f"Error getting solar position from ephemeris: {e}")
                    # Fall back to current phase center
                    result["true_ra"] = radeg
                    result["true_dec"] = decdeg
            else:
                print(
                    "Warning: No observation time found, using current phase center as true position"
                )
                result["true_ra"] = radeg
                result["true_dec"] = decdeg

        # Step 2: Find APPARENT solar position using image-based methods
        apparent_ra = None
        apparent_dec = None
        apparent_pix_x = None
        apparent_pix_y = None

        # Center of mass method using the working FITS file
        try:
            # Calculate RMS for thresholding from FITS data
            data = fits.getdata(working_fits)

            # Get 2D data
            if data.ndim == 4:
                data_2d = data[0, 0, :, :]
            elif data.ndim == 3:
                data_2d = data[0, :, :]
            else:
                data_2d = data

            valid_data = data_2d[~np.isnan(data_2d)]
            rms = np.sqrt(np.mean(valid_data**2)) if valid_data.size > 0 else 1.0

            # Apply threshold
            data_binary = np.zeros_like(data_2d)
            data_binary[data_2d > sigma * rms] = 1

            # Create circular mask around center (5 degrees radius)
            circular_mask = self.create_circular_mask(
                data_binary.shape[0],
                data_binary.shape[1],
                center=(int(data_binary.shape[0] / 2), int(data_binary.shape[1] / 2)),
                radius=int(5 / (self.cellsize / 3600.0)),
            )
            data_binary[~circular_mask] = 0

            # Method 1: Try Gaussian fitting with scipy if requested
            if fit_gaussian:
                try:
                    from scipy.optimize import curve_fit
                    from scipy.ndimage import gaussian_filter

                    # Smooth the data slightly for better fitting
                    smoothed = gaussian_filter(data_2d, sigma=3)

                    # Find initial guess from max position
                    max_idx = np.unravel_index(np.nanargmax(smoothed), smoothed.shape)
                    y0, x0 = max_idx[0], max_idx[1]

                    # Define 2D Gaussian function
                    def gaussian_2d(xy, amplitude, x0, y0, sigma_x, sigma_y, offset):
                        x, y = xy
                        g = offset + amplitude * np.exp(
                            -(
                                ((x - x0) ** 2) / (2 * sigma_x**2)
                                + ((y - y0) ** 2) / (2 * sigma_y**2)
                            )
                        )
                        return g.ravel()

                    # Create coordinate grids for fitting region
                    fit_size = 50  # Fit in a region around the max
                    y_min = max(0, y0 - fit_size)
                    y_max = min(data_2d.shape[0], y0 + fit_size)
                    x_min = max(0, x0 - fit_size)
                    x_max = min(data_2d.shape[1], x0 + fit_size)

                    y_grid, x_grid = np.mgrid[y_min:y_max, x_min:x_max]
                    data_region = smoothed[y_min:y_max, x_min:x_max]

                    # Initial parameters
                    p0 = [
                        np.nanmax(data_region),
                        x0,
                        y0,
                        20,
                        20,
                        np.nanmin(data_region),
                    ]

                    # Fit the Gaussian
                    popt, pcov = curve_fit(
                        gaussian_2d,
                        (x_grid, y_grid),
                        data_region.ravel(),
                        p0=p0,
                        maxfev=5000,
                    )

                    apparent_pix_x = int(popt[1])
                    apparent_pix_y = int(popt[2])
                    print(
                        f"Gaussian fit center: pixel ({apparent_pix_x}, {apparent_pix_y})"
                    )

                except Exception as e:
                    print(f"Gaussian fitting failed: {e}, using center-of-mass")
                    fit_gaussian = False  # Fall back to center of mass

            # Method 2: Center of mass (fallback or default)
            if apparent_pix_x is None:
                cy, cx = ndi.center_of_mass(data_binary)
                apparent_pix_x = int(cx)
                apparent_pix_y = int(cy)

            # Convert pixel position to world coordinates using WCS
            w = WCS(working_fits)

            # If WCS has more than 2 dimensions, extract the celestial 2D WCS
            if w.naxis > 2:
                try:
                    w = w.celestial
                except:
                    pass

            try:
                # Use wcs_pix2world with 0-based pixel coordinates
                world = w.wcs_pix2world([[apparent_pix_x, apparent_pix_y]], 0)[0]
                apparent_ra = float(world[0])
                apparent_dec = float(world[1])
            except Exception as e:
                print(f"Error converting pixel to world coordinates: {e}")

            if apparent_ra is not None and apparent_dec is not None:
                print(
                    f"Apparent solar position: RA = {apparent_ra:.6f} deg, DEC = {apparent_dec:.6f} deg"
                )
            print(f"Apparent pixel position: ({apparent_pix_x}, {apparent_pix_y})")

        except Exception as e:
            print(f"Error in apparent position calculation: {e}")
            import traceback

            traceback.print_exc()

        finally:
            # Clean up temp FITS file if we created one
            if temp_fits_file and os.path.exists(temp_fits_file):
                os.remove(temp_fits_file)

        # Store apparent position results
        result["apparent_ra"] = apparent_ra
        result["apparent_dec"] = apparent_dec
        result["apparent_pix_x"] = apparent_pix_x
        result["apparent_pix_y"] = apparent_pix_y

        # Determine if shift is needed
        if result["true_ra"] is not None and apparent_ra is not None:
            offset = np.sqrt(
                (result["true_ra"] - apparent_ra) ** 2
                + (result["true_dec"] - apparent_dec) ** 2
            )
            # Shift is needed if offset is more than 1 cell
            if offset > (self.cellsize / 3600.0):
                result["needs_shift"] = True
                print(f"Phase shift needed: offset = {offset * 3600:.2f} arcsec")
            else:
                print(
                    f"No significant shift needed: offset = {offset * 3600:.2f} arcsec"
                )

        return result

    def shift_phasecenter(
        self,
        imagename,
        ra=None,
        dec=None,
        stokes="I",
        process_id=None,
        phase_result=None,
    ):
        """
        Shift the image WCS so that the apparent solar position maps to the true solar coordinates.

        This sets:
        - CRPIX = apparent pixel position (where sun appears due to refraction)
        - CRVAL = true RA/DEC (from ephemeris, where sun actually is)

        Parameters
        ----------
        imagename : str
            Name of the image
        ra : float, optional
            True solar RA in degrees (deprecated, use phase_result instead)
        dec : float, optional
            True solar DEC in degrees (deprecated, use phase_result instead)
        stokes : str
            Stokes parameter to use
        process_id : int, optional
            Process ID for multiprocessing (creates unique temp files)
        phase_result : dict, optional
            Result from cal_solar_phaseshift() containing true and apparent positions

        Returns
        -------
        int
            Success code 0: Successfully shifted, 1: Shifting not required, 2: Error
        """
        try:
            if stokes is None:
                return 2

            # Handle new dict format or legacy arguments
            if phase_result is not None:
                true_ra = phase_result.get("true_ra")
                true_dec = phase_result.get("true_dec")
                apparent_pix_x = phase_result.get("apparent_pix_x")
                apparent_pix_y = phase_result.get("apparent_pix_y")

                if true_ra is None or true_dec is None:
                    print("Error: No true solar position available")
                    return 2
                if apparent_pix_x is None or apparent_pix_y is None:
                    print("Error: No apparent pixel position available")
                    return 2
            else:
                # Legacy mode: calculate pixel position from ra/dec
                if ra is None or dec is None:
                    print("Error: RA/DEC must be provided")
                    return 2
                true_ra, true_dec = ra, dec
                apparent_pix_x, apparent_pix_y = None, None  # Will be calculated below

            # Determine image type
            if os.path.isdir(imagename):
                imagetype = "casa"
            else:
                imagetype = "fits"

            image_path = os.path.dirname(os.path.abspath(imagename))

            # Create unique temporary filenames for multiprocessing
            if process_id is not None:
                temp_image = f"{image_path}/I_model_{process_id}_{os.getpid()}"
                temp_fits = f"{image_path}/wcs_model_{process_id}_{os.getpid()}.fits"
            else:
                temp_image = f"{image_path}/I.model"
                temp_fits = f"{image_path}/wcs_model.fits"

            # Clean up previous files
            if os.path.isfile(temp_fits):
                os.system(f"rm -rf {temp_fits}")
            if os.path.isdir(temp_image):
                os.system(f"rm -rf {temp_image}")

            # Handle trailing slashes
            if imagename.endswith("/"):
                imagename = imagename[:-1]

            # If we don't have pixel positions, calculate them (legacy mode)
            if apparent_pix_x is None or apparent_pix_y is None:
                # Extract stokes plane for coordinate calculation
                if imagetype == "casa":
                    imsubimage_subprocess(
                        imagename=imagename,
                        outfile=temp_image,
                        stokes=stokes,
                        dropdeg=False,
                    )
                    exportfits_subprocess(
                        imagename=temp_image,
                        fitsimage=temp_fits,
                        dropdeg=True,
                        dropstokes=True,
                    )
                else:
                    import shutil

                    shutil.copy(imagename, temp_fits)

                # Calculate pixel position for the target RA/DEC
                w = WCS(temp_fits)

                # Handle multi-dimensional WCS by using celestial part
                if hasattr(w, "celestial"):
                    w_celest = w.celestial
                else:
                    w_celest = w

                try:
                    # Provide RA and Dec to celestial WCS
                    pix = w_celest.all_world2pix(np.array([[true_ra, true_dec]]), 0)
                    apparent_pix_x = int(np.round(pix[0][0]))
                    apparent_pix_y = int(np.round(pix[0][1]))
                except Exception as e:
                    print(f"Error calculating pixel position for RA/Dec: {e}")
                    # Fallback to image center if WCS conversion fails
                    naxis1 = header.get("NAXIS1", 512)
                    naxis2 = header.get("NAXIS2", 512)
                    apparent_pix_x = naxis1 // 2
                    apparent_pix_y = naxis2 // 2

                # Clean up temp files
                os.system(f"rm -rf {temp_image} {temp_fits}")

            # Apply the shift: set CRPIX to apparent position, CRVAL to true position
            # Note: apparent_pix_x/y are 0-based (from center_of_mass or WCS with origin=0)
            # CASA imhead uses 0-based pixel indexing
            # FITS CRPIX uses 1-based pixel indexing
            if imagetype == "casa":
                # Update CRPIX and CRVAL in CASA image (0-based)
                imhead_subprocess(
                    imagename=imagename,
                    mode="put",
                    hdkey="CRPIX1",
                    hdvalue=str(apparent_pix_x),
                )
                imhead_subprocess(
                    imagename=imagename,
                    mode="put",
                    hdkey="CRPIX2",
                    hdvalue=str(apparent_pix_y),
                )
                # Also update CRVAL to true position (in radians for CASA)
                imhead_subprocess(
                    imagename=imagename,
                    mode="put",
                    hdkey="CRVAL1",
                    hdvalue=str(np.deg2rad(true_ra)),
                )
                imhead_subprocess(
                    imagename=imagename,
                    mode="put",
                    hdkey="CRVAL2",
                    hdvalue=str(np.deg2rad(true_dec)),
                )
            elif imagetype == "fits":
                # Update CRPIX and CRVAL in FITS header
                data = fits.getdata(imagename)
                header = fits.getheader(imagename)

                # Set CRPIX to apparent pixel position (1-based for FITS)
                # Add 1 because FITS CRPIX is 1-based but our pixel positions are 0-based
                header["CRPIX1"] = float(apparent_pix_x + 1)
                header["CRPIX2"] = float(apparent_pix_y + 1)

                # Set CRVAL to true solar position
                header["CRVAL1"] = float(true_ra)
                header["CRVAL2"] = float(true_dec)

                # Add HISTORY
                header.add_history(
                    "Phase center shifted with SolarViewer (ephemeris-based)"
                )
                header.add_history(
                    f"True solar RA={true_ra:.6f} deg, DEC={true_dec:.6f} deg"
                )
                header.add_history(
                    f"Apparent pixel position (0-based): ({apparent_pix_x}, {apparent_pix_y})"
                )

                fits.writeto(imagename, data=data, header=header, overwrite=True)
            else:
                print("Image is not either fits or CASA format.")
                return 1

            ra_hms = self.deg2hms(true_ra)
            dec_dms = self.deg2dms(true_dec)
            print(
                f"Phase center shifted: CRVAL = ({ra_hms}, {dec_dms}), CRPIX = ({apparent_pix_x}, {apparent_pix_y})"
            )

            return 0

        except Exception as e:
            print(f"Error in shift_phasecenter: {e}")
            import traceback

            traceback.print_exc()
            return 2

    def visually_center_image(self, imagename, output_file, crpix1, crpix2):
        """
        Create a new visually centered image with the Sun in the middle

        Parameters
        ----------
        imagename : str
            Name of the input image (FITS or CASA)
        output_file : str
            Name of the output image (will always be FITS format)
        crpix1 : int
            X coordinate of the reference pixel (solar center, 0-based)
        crpix2 : int
            Y coordinate of the reference pixel (solar center, 0-based)

        Returns
        -------
        bool
            True if successful, False if there was an error
        """
        try:
            temp_fits = None

            # Handle CASA images by exporting to FITS first
            if os.path.isdir(imagename):
                print("Input is CASA image - exporting to FITS for visual centering")
                image_path = os.path.dirname(os.path.abspath(imagename))
                temp_fits = f"{image_path}/temp_visual_center.fits"

                exportfits_subprocess(
                    imagename=imagename,
                    fitsimage=temp_fits,
                    dropdeg=False,
                    dropstokes=False,
                    overwrite=True,
                )
                fits_file = temp_fits

                # Ensure output is FITS format for CASA input
                if not output_file.endswith(".fits"):
                    output_file = output_file + ".fits"
                    print(f"Output changed to FITS format: {output_file}")
            else:
                fits_file = imagename

            # Load the FITS image
            hdul = fits.open(fits_file)
            header = hdul[0].header
            data = hdul[0].data

            # Get image dimensions
            if len(data.shape) == 2:
                ny, nx = data.shape
            else:
                ny, nx = data.shape[-2:]

            # Create a new array for the centered image
            new_data = np.zeros_like(data)
            center_x = nx // 2
            center_y = ny // 2

            # For FITS, CRPIX is 1-based, but our crpix1/crpix2 are 0-based
            # Calculate offsets
            offset_x = center_x - crpix1
            offset_y = center_y - crpix2

            print(f"Original image dimensions: {data.shape}")
            print(f"Sun at pixel (0-based): CRPIX1={crpix1}, CRPIX2={crpix2}")
            print(f"Image center: ({center_x}, {center_y})")
            print(
                f"Shifting data by ({offset_x}, {offset_y}) pixels to visually center"
            )

            # Shift the data using numpy roll for efficiency
            if len(data.shape) == 2:
                new_data = np.roll(np.roll(data, offset_y, axis=0), offset_x, axis=1)
            else:
                # For higher dimensions, roll on last two axes
                new_data = np.roll(np.roll(data, offset_y, axis=-2), offset_x, axis=-1)

            # Update the header - CRPIX is 1-based in FITS
            header["CRPIX1"] = float(center_x + 1)  # 1-based
            header["CRPIX2"] = float(center_y + 1)  # 1-based

            # Save the centered image
            hdul[0].data = new_data
            hdul[0].header.add_history("Visually centered with SolarViewer")
            hdul.writeto(output_file, overwrite=True)
            hdul.close()

            # Clean up temp file
            if temp_fits and os.path.exists(temp_fits):
                os.remove(temp_fits)

            print(f"Created a visually centered image: {output_file}")
            print(
                f"New reference pixel (1-based): CRPIX1={center_x + 1}, CRPIX2={center_y + 1}"
            )
            return True

        except Exception as e:
            print(f"Error creating visually centered image: {e}")
            import traceback

            traceback.print_exc()
            # Clean up temp file on error
            if temp_fits and os.path.exists(temp_fits):
                os.remove(temp_fits)
            return False

    def shift_phasecenter_ms(self, msname, ra, dec):
        """
        Apply phase shift to a measurement set

        Parameters
        ----------
        msname : str
            Name of the measurement set
        ra : float
            RA of the new phase center in degrees
        dec : float
            DEC of the new phase center in degrees

        Returns
        -------
        int
            Success code 0: Successfully shifted, 1: Error in shifting
        """
        try:
            # Create a table tool
            t = table()

            # Get original phase center from MS
            t.open(f"{msname}/FIELD")
            orig_dir = t.getcol("PHASE_DIR")
            # Convert the new coordinates to radians
            new_ra_rad = np.deg2rad(ra)
            new_dec_rad = np.deg2rad(dec)

            # Format for display
            ra_hms = self.deg2hms(ra)
            dec_dms = self.deg2dms(dec)
            orig_ra_deg = np.degrees(orig_dir[0][0][0])
            orig_dec_deg = np.degrees(orig_dir[0][0][1])
            orig_ra_hms = self.deg2hms(orig_ra_deg)
            orig_dec_dms = self.deg2dms(orig_dec_deg)

            print(
                f"Original phase center: RA = {orig_ra_hms} ({orig_ra_deg} deg), DEC = {orig_dec_dms} ({orig_dec_deg} deg)"
            )
            print(
                f"New phase center: RA = {ra_hms} ({ra} deg), DEC = {dec_dms} ({dec} deg)"
            )

            # Update the phase center
            for i in range(orig_dir.shape[0]):
                orig_dir[i][0][0] = new_ra_rad
                orig_dir[i][0][1] = new_dec_rad

            # Write back to the table
            t.putcol("PHASE_DIR", orig_dir)
            t.close()

            # Update UVW coordinates to match the new phase center
            fixvis_subprocess(
                vis=msname,
                outputvis="",
                phasecenter=f"J2000 {ra_hms} {dec_dms}",
                datacolumn="all",
            )

            print(f"Phase center of MS successfully updated")
            return 0
        except Exception as e:
            print(f"Error shifting phase center in MS: {e}")
            return 1

    def apply_shift_to_multiple_fits(
        self,
        ra,
        dec,
        input_pattern,
        output_pattern=None,
        stokes="I",
        visual_center=False,
        use_multiprocessing=True,
        max_processes=None,
        phase_result=None,
    ):
        """
        Apply the same phase shift to multiple FITS files

        Parameters
        ----------
        ra : float
            RA of the solar center in degrees (legacy, use phase_result)
        dec : float
            DEC of the solar center in degrees (legacy, use phase_result)
        input_pattern : str
            Glob pattern for input files (e.g., "path/to/*.fits")
        output_pattern : str, optional
            Pattern for output files (if None, input files will be modified)
        stokes : str
            Stokes parameter to use
        visual_center : bool
            Whether to also create visually centered images
        use_multiprocessing : bool
            Whether to use multiprocessing for batch processing
        max_processes : int, optional
            Maximum number of processes to use (defaults to number of CPU cores)
        phase_result : dict, optional
            Result from cal_solar_phaseshift() to apply to all files

        Returns
        -------
        list
            List of [success_count, total_count]
        """
        try:
            # Clean up any leftover temporary files first
            input_dir = os.path.dirname(input_pattern)
            if input_dir and os.path.exists(input_dir):
                print(f"Cleaning up any leftover temporary files in {input_dir}")
                os.system(
                    f"rm -rf {input_dir}/I_model_* {input_dir}/wcs_model_*.fits {input_dir}/I.model {input_dir}/wcs_model.fits"
                )

            # Get list of files matching the pattern
            files = glob.glob(input_pattern)
            if not files:
                print(f"No files found matching pattern: {input_pattern}")
                return [0, 0]

            total_count = len(files)
            print(f"Found {total_count} files matching pattern: {input_pattern}")
            print(f"Applying phase shift: RA = {ra} deg, DEC = {dec} deg")

            # If only one file or multiprocessing is disabled, use the single-processing approach
            if total_count == 1 or not use_multiprocessing:
                success_count = 0
                results = []
                for i, file in enumerate(files):
                    print(f"Processing file {i+1}/{total_count}: {file}")

                    file_info = (
                        file,
                        ra,
                        dec,
                        stokes,
                        output_pattern,
                        visual_center,
                        phase_result,
                    )
                    res = self.process_single_file(file_info)
                    results.append(res)

                    if res[0]:
                        success_count += 1
                    elif res[2]:
                        print(f"Error processing {file}: {res[2]}")

                print(f"Successfully processed {success_count}/{total_count} files")

                # Clean up any temporary files
                if input_dir and os.path.exists(input_dir):
                    os.system(
                        f"rm -rf {input_dir}/I_model_* {input_dir}/wcs_model_*.fits {input_dir}/I.model {input_dir}/wcs_model.fits"
                    )

                return [success_count, total_count]

            # Use multiprocessing for batch processing
            else:
                # Determine number of processes to use
                if max_processes is None:
                    max_processes = min(multiprocessing.cpu_count(), total_count)
                else:
                    max_processes = min(
                        max_processes, multiprocessing.cpu_count(), total_count
                    )

                print(f"Using multiprocessing with {max_processes} processes")

                # Prepare the arguments for each file
                file_args = [
                    (file, ra, dec, stokes, output_pattern, visual_center, phase_result)
                    for file in files
                ]

                # Create a process pool and process the files
                with Pool(processes=max_processes) as pool:
                    results = pool.map(self.process_single_file, file_args)

                # Count successful operations
                success_count = sum(1 for success, _, _ in results if success)

                # Print any errors or warnings
                for success, file, message in results:
                    if message:
                        print(f"{file}: {message}")

                print(f"Successfully processed {success_count}/{total_count} files")

                # Final cleanup to ensure all temporary files are removed
                if input_dir and os.path.exists(input_dir):
                    print(f"Final cleanup of temporary files in {input_dir}")
                    os.system(
                        f"rm -rf {input_dir}/I_model_* {input_dir}/wcs_model_*.fits {input_dir}/I.model {input_dir}/wcs_model.fits"
                    )

                return [success_count, total_count]

        except Exception as e:
            print(f"Error in applying shift to multiple files: {e}")

            # Cleanup even if an error occurred
            if "input_dir" in locals() and input_dir and os.path.exists(input_dir):
                print(f"Cleaning up temporary files after error in {input_dir}")
                os.system(
                    f"rm -rf {input_dir}/I_model_* {input_dir}/wcs_model_*.fits {input_dir}/I.model {input_dir}/wcs_model.fits"
                )

            try:
                return [0, total_count]
            except:
                return [0, 0]

    def process_single_file(self, file_info):
        """
        Process a single file for multiprocessing in batch mode

        Parameters
        ----------
        file_info : tuple
            Tuple containing (file_path, ra, dec, stokes, output_pattern, visual_center, phase_result)

        Returns
        -------
        tuple
            Tuple containing (success, file_path, error_message)
        """
        if len(file_info) == 7:
            file, ra, dec, stokes, output_pattern, visual_center, phase_result = (
                file_info
            )
        else:
            file, ra, dec, stokes, output_pattern, visual_center = file_info
            phase_result = None

        try:
            # Use process ID and file identifier to create a unique identifier for this task
            unique_id = hashlib.md5(file.encode()).hexdigest()[:8]
            process_id = int(hashlib.md5(file.encode()).hexdigest(), 16) % 10000

            # Determine input type
            is_casa = os.path.isdir(file)

            # Determine final output FITS file path
            if output_pattern:
                file_basename = os.path.basename(file)
                file_name, file_ext = os.path.splitext(file_basename)

                # If it's a CASA image, the extension might be .image or .im - strip it
                if is_casa:
                    for ext in [".image", ".im", ".ims"]:
                        if file_name.lower().endswith(ext):
                            file_name = file_name[: -len(ext)]
                            break

                # Replace wildcards in the output pattern
                output_file = output_pattern.replace("*", file_name)
                if not output_file.lower().endswith(".fits"):
                    output_file += ".fits"

                # Ensure output directory exists
                out_dir = os.path.dirname(output_file)
                if out_dir and not os.path.exists(out_dir):
                    try:
                        os.makedirs(out_dir, exist_ok=True)
                    except OSError:
                        # Ignore race condition error if dir created by another process
                        pass
            else:
                # If no output pattern, we modify in-place or generate a fits next to the source
                if is_casa:
                    output_file = file.rstrip("/") + ".fits"
                else:
                    output_file = file  # In-place for FITS

            # Define temporary FITS file for processing
            temp_fits = output_file + f".tmp_{unique_id}.fits"

            # Step 1: Get a FITS file to work with
            if is_casa:
                exportfits_subprocess(
                    imagename=file, fitsimage=temp_fits, overwrite=True
                )
            else:
                shutil.copy(file, temp_fits)

            # Step 2: Apply the phase shift to the temp FITS
            result = self.shift_phasecenter(
                imagename=temp_fits,
                ra=ra,
                dec=dec,
                stokes=stokes,
                process_id=process_id,
                phase_result=phase_result,
            )

            if result != 0 and result != 1:  # 0: shifted, 1: not needed
                return (False, file, f"Error applying phase shift (code: {result})")

            # Step 3: Handle visual centering and Finalize Output
            if visual_center:
                try:
                    # Get the reference pixel values from the shifted image
                    header = fits.getheader(temp_fits)

                    # Use values from phase_result if available, else from header
                    if phase_result and "apparent_pix_x" in phase_result:
                        cpix1 = phase_result["apparent_pix_x"]
                        cpix2 = phase_result["apparent_pix_y"]
                    else:
                        cpix1 = int(header["CRPIX1"])
                        cpix2 = int(header["CRPIX2"])

                    # Create the visually centered image directly as the final output
                    self.visually_center_image(temp_fits, output_file, cpix1, cpix2)

                    # Cleanup temp
                    if os.path.exists(temp_fits):
                        os.remove(temp_fits)

                    return (True, file, None)
                except Exception as e:
                    # If centering fails, at least we have the shifted file
                    if os.path.exists(output_file) and output_file != file:
                        os.remove(output_file)
                    shutil.move(temp_fits, output_file)
                    return (
                        True,
                        file,
                        f"Warning: Shift applied but visual centering failed: {str(e)}",
                    )
            else:
                # Simply move the shifted temp file to the final output
                if os.path.exists(output_file) and output_file != file:
                    os.remove(output_file)
                shutil.move(temp_fits, output_file)
                return (True, file, None)

        except Exception as e:
            # Cleanup on failure
            if "temp_fits" in locals() and os.path.exists(temp_fits):
                os.remove(temp_fits)
            return (False, file, f"Error: {str(e)}")


def main():
    """
    Main function to run from command line
    """
    parser = argparse.ArgumentParser(
        description="Calculate and apply phase shifts to solar images"
    )
    parser.add_argument(
        "--imagename",
        type=str,
        required=False,
        help="Input image name (CASA or FITS format) for calculating phase shift",
    )
    parser.add_argument(
        "--msname", type=str, default=None, help="Measurement set name (optional)"
    )
    parser.add_argument(
        "--cellsize",
        type=float,
        default=None,
        help="Cell size in arcsec (optional, will be read from image if not provided)",
    )
    parser.add_argument(
        "--imsize",
        type=int,
        default=None,
        help="Image size in pixels (optional, will be read from image if not provided)",
    )
    parser.add_argument(
        "--stokes", type=str, default="I", help="Stokes parameter to use (default: I)"
    )
    parser.add_argument(
        "--fit_gaussian",
        action="store_true",
        default=False,
        help="Use Gaussian fitting for solar center",
    )
    parser.add_argument(
        "--sigma",
        type=float,
        default=10,
        help="Sigma threshold for center-of-mass calculation (default: 10)",
    )
    parser.add_argument(
        "--apply_shift",
        action="store_true",
        default=True,
        help="Apply the calculated shift to the image",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output image name (if not specified, input image will be modified)",
    )
    parser.add_argument(
        "--visual_center",
        action="store_true",
        default=False,
        help="Create a visually centered image (moves pixel data)",
    )

    # New arguments for batch processing
    parser.add_argument(
        "--ra",
        type=float,
        default=None,
        help="RA in degrees (if provided, skips calculation)",
    )
    parser.add_argument(
        "--dec",
        type=float,
        default=None,
        help="DEC in degrees (if provided, skips calculation)",
    )
    parser.add_argument(
        "--apply_to_ms",
        action="store_true",
        default=False,
        help="Apply the calculated/provided shift to the MS file",
    )
    parser.add_argument(
        "--input_pattern",
        type=str,
        default=None,
        help="Glob pattern for batch processing multiple files",
    )
    parser.add_argument(
        "--output_pattern",
        type=str,
        default=None,
        help='Output pattern for batch processing (e.g., "/path/to/shifted_*.fits")',
    )

    args = parser.parse_args()

    # Initialize the object
    spc = SolarPhaseCenter(
        msname=args.msname, cellsize=args.cellsize, imsize=args.imsize
    )

    # Determine phase shift coordinates
    if args.ra is not None and args.dec is not None:
        # Use provided coordinates
        ra = args.ra
        dec = args.dec
        needs_shift = True
        print(f"Using provided coordinates: RA = {ra} deg, DEC = {dec} deg")
    elif args.imagename:
        # Calculate from image
        ra, dec, needs_shift = spc.cal_solar_phaseshift(
            imagename=args.imagename, fit_gaussian=args.fit_gaussian, sigma=args.sigma
        )
        print(f"Calculated solar center: RA = {ra} deg, DEC = {dec} deg")
        print(f"Phase shift needed: {needs_shift}")
    else:
        print(
            "Error: Either provide an image for calculation or specify RA and DEC coordinates"
        )
        return

    # Handle MS phase shift
    if args.apply_to_ms and args.msname:
        if needs_shift:
            result = spc.shift_phasecenter_ms(args.msname, ra, dec)
            if result == 0:
                print(f"Successfully applied phase shift to MS: {args.msname}")
            else:
                print(f"Failed to apply phase shift to MS: {args.msname}")
        else:
            print("No phase shift needed for the MS")

    # Handle batch processing of FITS files
    if args.input_pattern:
        if needs_shift:
            success_count, total_count = spc.apply_shift_to_multiple_fits(
                ra,
                dec,
                args.input_pattern,
                args.output_pattern,
                args.stokes,
                args.visual_center,
            )
            if success_count == total_count:
                print(f"Successfully applied phase shift to all {total_count} files")
            else:
                print(
                    f"Applied phase shift to {success_count} out of {total_count} files"
                )
        else:
            print("No phase shift needed for the image files")

    # Handle single image (original functionality)
    elif args.imagename and args.apply_shift and needs_shift:
        if args.output:
            # Make a copy of the image
            if os.path.isdir(args.imagename):
                os.system(f"rm -rf {args.output}")
                os.system(f"cp -r {args.imagename} {args.output}")
                target = args.output
            else:
                import shutil

                shutil.copy(args.imagename, args.output)
                target = args.output
        else:
            target = args.imagename

        result = spc.shift_phasecenter(
            imagename=target, ra=ra, dec=dec, stokes=args.stokes
        )

        if result == 0:
            print("Phase shift successfully applied")

            # Create a visually centered image if requested
            if args.visual_center and args.output:
                # Get the reference pixel values from the shifted image
                header = fits.getheader(target)
                crpix1 = int(header["CRPIX1"])
                crpix2 = int(header["CRPIX2"])

                # Generate output filename for visually centered image
                visual_output = (
                    os.path.splitext(args.output)[0]
                    + "_centered"
                    + os.path.splitext(args.output)[1]
                )

                # Create the visually centered image
                spc.visually_center_image(target, visual_output, crpix1, crpix2)

        elif result == 1:
            print("Phase shift not needed")
        else:
            print("Error applying phase shift")
    elif args.imagename and args.output and not needs_shift:
        # User requested output file but no shift needed
        if os.path.isdir(args.imagename):
            os.system(f"rm -rf {args.output}")
            os.system(f"cp -r {args.imagename} {args.output}")
        else:
            import shutil

            shutil.copy(args.imagename, args.output)
        print(f"No phase shift needed. Copied original image to {args.output}")

        # If visual centering was requested but no shift needed, still create it
        if args.visual_center:
            # Need to get current reference pixels
            header = fits.getheader(args.output)
            crpix1 = int(header["CRPIX1"])
            crpix2 = int(header["CRPIX2"])

            # Generate output filename for visually centered image
            visual_output = (
                os.path.splitext(args.output)[0]
                + "_centered"
                + os.path.splitext(args.output)[1]
            )

            # Create the visually centered image
            spc.visually_center_image(args.output, visual_output, crpix1, crpix2)


if __name__ == "__main__":
    main()

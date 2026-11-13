"""Where the telescope of an image was, and the Sun as it saw it.

The conversion to helioprojective (helioprojective.py) looks at the Sun from
the telescope's site, not from the Earth's centre: the two directions differ
by the Sun's parallax, up to 8.8 arcsec. ``sun_radec`` gives the Sun's RA and
Dec as that conversion has them (the direction it makes Solar X = Y = 0), for
whatever else places the Sun in an RA/Dec image: the phase centre shift and
the solar disk drawn on an image.

Nothing here loads CASA, sunpy or matplotlib until it is used: the viewer's
window imports this module.
"""

import contextlib
import io
import logging
import os

import astropy.units as u
import numpy as np
from astropy.coordinates import GCRS, CartesianDifferential, EarthLocation, SkyCoord
from astropy.io import fits

from ..processing.fits_io import readable_image_path

logger = logging.getLogger(__name__)


def IA():
    """A CASA image tool."""
    from ..processing.dependencies import IA as tool

    return tool()


KNOWN_OBSERVATORIES = ("LOFAR", "MWA", "MEERKAT", "GMRT", "UGMRT")
RADEC_HISTORY = "Converted from Helioprojective to RA/Dec with SolarViewer"
OBSERVER_KEYS = ("HGLN_OBS", "HGLT_OBS", "DSUN_OBS")


def get_Earthlocation(fits_file="", lat=None, long=None, height=None, observatory=None):
    if os.path.isdir(fits_file):
        header = {}  # a CASA image: it has no OBSGEO keywords to read
    else:
        fits_file = readable_image_path(fits_file)
        header = fits.getheader(fits_file)
    POS = None
    if observatory is not None:
        if observatory.upper() == "LOFAR":
            lat = "52:55:53.90"
            long = "06:51:56.95"
            height = 50.16
        elif observatory.upper() == "MWA":
            lat = "-26:42:12.09"
            long = "116:40:14.84"
            height = 375.75
        elif observatory.upper() == "MEERKAT":
            lat = "-30:42:47.36"
            long = "21:26:38.09"
            height = 1050.82
        elif observatory.upper() == "GMRT" or observatory.upper() == "UGMRT":
            lat = "19:05:26.21"
            long = "74:02:59.90"
            height = 639.68
        else:
            print(
                "[WARNING] Observatory parameter could not be understood. Trying from lat and long...."
            )
    if lat is not None and long is not None and height is not None:
        POS = EarthLocation.from_geodetic(lon=long, lat=lat, height=height)

    if lat is None or long is None or height is None:
        try:
            x = header["OBSGEO-X"]
            y = header["OBSGEO-Y"]
            z = header["OBSGEO-Z"]
            if abs(x) > 1e6 and abs(y) > 1e6 and abs(z) > 1e6:
                POS = EarthLocation.from_geocentric(x, y, z, unit=u.m)
            else:
                print(
                    "[WARNING] Invalid telescope position in header. Looking for observatory from header..."
                )
                ia_tool = IA()
                ia_tool.open(fits_file)
                metadata_dict = ia_tool.summary(list=False, verbose=True)
                lat, long, height, observatory = extract_telescope_position(
                    metadata_dict
                )
                ia_tool.close()
                POS = EarthLocation.from_geodetic(lon=long, lat=lat, height=height)
        except KeyError:
            pass  # no OBSGEO keywords: not an error, _seen_from has a backup
        except Exception as e:
            logger.error(f"{str(e)}")
    return POS


def _at_rest(position, obstime):
    """``position`` (of a GCRS frame) as an observer, without a velocity:
    only a telescope's site gives one."""
    return SkyCoord(
        position.with_differentials(CartesianDifferential([0, 0, 0] * u.km / u.s)),
        frame=GCRS(obstime=obstime),
    )


def _observer_from_header(header, obstime):
    """The observer of the HGLN_OBS, HGLT_OBS and DSUN_OBS in ``header`` in
    GCRS, or None if they are not all there."""
    if not all(key in header for key in OBSERVER_KEYS):
        return None
    from sunpy.coordinates import HeliographicStonyhurst

    observer = SkyCoord(
        header["HGLN_OBS"] * u.deg,
        header["HGLT_OBS"] * u.deg,
        header["DSUN_OBS"] * u.m,
        frame=HeliographicStonyhurst(obstime=obstime),
    )
    return _at_rest(observer.transform_to(GCRS(obstime=obstime)).cartesian, obstime)


def _seen_from(fits_file, header, obstime):
    """The observer of the image in GCRS, and its observatory if known."""
    # What convert_hpc_to_radec wrote is as its observer saw it, which is
    # the Earth's centre for a file from before it recorded that
    if any(RADEC_HISTORY in str(line) for line in header.get("HISTORY", [])):
        observer = _observer_from_header(header, obstime)
        if observer is None:
            centre = EarthLocation.from_geocentric(0, 0, 0, unit=u.m)
            observer = SkyCoord(centre.get_gcrs(obstime))
        return observer, header.get("TELESCOP")

    ia_tool = IA()
    ia_tool.open(fits_file)
    metadata_dict = ia_tool.summary(list=False, verbose=True)
    lat, long, height, observatory = extract_telescope_position(metadata_dict)
    ia_tool.close()
    if str(observatory).upper() not in KNOWN_OBSERVATORIES:
        observatory = None
    POS = get_Earthlocation(fits_file=fits_file, observatory=observatory)
    if POS is None and None not in (lat, long, height):
        # The backup: the image names a telescope CASA knows the place of (the
        # VLA, ALMA, OVRO-LWA...) and has no OBSGEO keywords, as a CASA image
        # never has. It is what CASA writes in them when it exports to FITS.
        POS = EarthLocation.from_geodetic(lon=long, lat=lat, height=height)
        logger.info("No telescope position in the header of %s: using CASA's (%s, %s, %.0f m)", fits_file, lat, long, height)
    if POS is None:
        raise RuntimeError("The image does not say where the telescope was")
    return SkyCoord(POS.get_gcrs(obstime)), observatory


_sun_seen = {}  # (image, its time of change, obstime) -> sun_radec's answer


def _quietly(call, *args):
    """``call(*args)`` without what it prints and logs: the conversion says
    once which observatory it matched, a disk drawn on every image and every
    frame of a video would say it each time."""
    level = logger.level
    logger.setLevel(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            return call(*args)
    finally:
        logger.setLevel(level)


def sun_radec(image, header, obstime):
    """The RA and Dec (degrees) of the Sun's centre at ``obstime`` (a Time)
    for the image at the path ``image`` with the FITS ``header`` (any mapping;
    empty for a CASA image), and who saw it: the observatory's name, "the
    telescope" for one with a position and no known name, or None.

    It is the Sun as ``convert_to_hpc`` has it: seen from the telescope the
    image names (``_seen_from``), the direction that conversion makes
    Solar X = Y = 0. For an image that does not say where the telescope was
    (the conversion refuses it), or without a path, it is the Sun seen from
    the Earth's centre, and the name is None."""
    from astropy.coordinates import get_sun

    try:
        key = (os.path.abspath(image), os.path.getmtime(image), obstime.isot)
    except (OSError, TypeError):
        key = None
    if key in _sun_seen:
        return _sun_seen[key]
    observer = name = None
    if key is not None:
        try:
            observer, name = _quietly(_seen_from, image, header if header is not None else {}, obstime)
            name = name or "the telescope"
        except Exception as e:
            logger.info("The Sun as seen from the Earth's centre for %s: %s", image, e)
            observer = name = None
    if observer is None:
        sun = get_sun(obstime)
        answer = (float(sun.ra.deg), float(sun.dec.deg), None)
    else:
        from sunpy.coordinates import Helioprojective

        centre = SkyCoord(
            0 * u.arcsec, 0 * u.arcsec, frame=Helioprojective(observer=observer, obstime=obstime)
        ).transform_to(
            GCRS(
                obstime=obstime,
                obsgeoloc=observer.cartesian.without_differentials(),
                obsgeovel=observer.velocity.to_cartesian(),
            )
        )
        answer = (float(centre.ra.deg), float(centre.dec.deg), name)
    if key is not None:
        if len(_sun_seen) >= 64:
            _sun_seen.pop(next(iter(_sun_seen)))
        _sun_seen[key] = answer
    return answer


def itrf_to_geodetic(x, y, z, scale_factor=1.0):
    """
    Convert ITRF (International Terrestrial Reference Frame) coordinates to geodetic coordinates.

    ITRF coordinates are Earth-centered, Earth-fixed Cartesian coordinates.
    Typical ITRF coordinates are in the range of millions of meters from the Earth's center.
    For example, a point on the Earth's surface might have ITRF coordinates like:
    X = 4075539.5, Y = 931735.8, Z = 4801629.6 (in meters)

    Parameters
    ----------
    x : float
        X coordinate in meters.
    y : float
        Y coordinate in meters.
    z : float
        Z coordinate in meters.
    scale_factor : float, optional
        Scale factor to apply to the coordinates. Default is 1.0.
        This is useful if the coordinates are provided in different units.

    Returns
    -------
    lat : str
        Latitude in the format "DD:MM:SS.SS".
    long : str
        Longitude in the format "DD:MM:SS.SS".
    height : float
        Height in meters above the WGS84 ellipsoid.

    Examples
    --------
    >>> lat, long, height = itrf_to_geodetic(4075539.5, 931735.8, 4801629.6)
    >>> print(lat, long, height)
    '49:08:42.04' '12:52:38.85' 669.13
    """
    try:
        # Apply scale factor if provided
        x = x * scale_factor
        y = y * scale_factor
        z = z * scale_factor

        # Use astropy's EarthLocation for the conversion if available
        try:
            # Create EarthLocation object from ITRF coordinates
            location = EarthLocation.from_geocentric(x, y, z, unit=u.m)

            # Get latitude and longitude in degrees
            lat_deg = location.lat.deg
            lon_deg = location.lon.deg
            height = location.height.value  # in meters

        except Exception as e:
            logger.error(f"Error {str(e)}. Falling back to manual calculation.")
            # Fall back to manual calculation if astropy is not available
            # WGS84 ellipsoid parameters
            a = 6378137.0  # semi-major axis in meters
            f = 1 / 298.257223563  # flattening
            b = a * (1 - f)  # semi-minor axis
            e_sq = 1 - (b / a) ** 2  # eccentricity squared

            # Calculate longitude (easy)
            longitude = np.arctan2(y, x)

            # Iterative calculation of latitude and height
            p = np.sqrt(x**2 + y**2)

            # Initial guess
            latitude = np.arctan2(z, p * (1 - e_sq))

            # Iterative improvement
            for _ in range(5):  # Usually converges in a few iterations
                N = a / np.sqrt(1 - e_sq * np.sin(latitude) ** 2)
                height = p / np.cos(latitude) - N
                latitude = np.arctan2(z, p * (1 - e_sq * N / (N + height)))

            # Convert to degrees
            lat_deg = np.degrees(latitude)
            lon_deg = np.degrees(longitude)

        # Convert decimal degrees to DD:MM:SS.SS format
        def decimal_to_dms(decimal_degrees):
            is_negative = decimal_degrees < 0
            decimal_degrees = abs(decimal_degrees)

            degrees = int(decimal_degrees)
            decimal_minutes = (decimal_degrees - degrees) * 60
            minutes = int(decimal_minutes)
            seconds = (decimal_minutes - minutes) * 60

            # Format as DD:MM:SS.SS
            dms = f"{degrees:02d}:{minutes:02d}:{seconds:05.2f}"

            # Add negative sign if needed
            if is_negative:
                dms = f"-{dms}"

            return dms

        lat_str = decimal_to_dms(lat_deg)
        lon_str = decimal_to_dms(lon_deg)

        return lat_str, lon_str, height

    except Exception as e:
        logger.error(f"Error converting ITRF to geodetic: {str(e)}")
        import traceback

        traceback.print_exc()
        return None, None, None


def extract_telescope_position(metadata_dict):
    """
    Extract telescope position from metadata dictionary and convert to latitude, longitude, and height.

    Parameters
    ----------
    metadata_dict : dict
        Metadata dictionary containing telescope position information.

    Returns
    -------
    lat : str
        Latitude in the format "DD:MM:SS.SS".
    long : str
        Longitude in the format "DD:MM:SS.SS".
    height : float
        Height in meters above the WGS84 ellipsoid.
    observatory : str
        Observatory name if available, otherwise None.

    Examples
    --------
    >>> metadata = {'messages': [
    ...     'Telescope position: [-2.55945e+06m, 5.09537e+06m, -2.84906e+06m] (ITRF)',
    ...     'Telescope: MWA'
    ... ]}
    >>> lat, lon, height, observatory = extract_telescope_position(metadata)
    >>> print(lat, lon, height, observatory)
    '-26:42:11.95' '116:40:14.93' 377.8 'MWA'
    """
    try:
        # Extract telescope position from messages
        telescope_position = None
        observatory = None

        if "messages" in metadata_dict:
            for message in metadata_dict["messages"]:
                # Look for telescope position in the message
                if "Telescope position:" in message:
                    # Extract the position string
                    pos_start = message.find("Telescope position:") + len(
                        "Telescope position:"
                    )
                    pos_end = (
                        message.find("(ITRF)") if "(ITRF)" in message else len(message)
                    )
                    telescope_position = message[pos_start:pos_end].strip()

                # Look for telescope/observatory name
                for line in message.split("\n"):
                    if "Telescope" in line and ":" in line and "position" not in line:
                        parts = line.split(":")
                        if len(parts) > 1:
                            observatory = parts[1].strip()
                            break

        # If observatory is still None, check if it's directly in the metadata
        if observatory is None and "observatory" in metadata_dict:
            observatory = metadata_dict["observatory"]

        if telescope_position is None:
            logger.error("Telescope position not found in metadata")
            return None, None, None, observatory

        # Extract the ITRF coordinates
        # Remove brackets and 'm' units, then split by comma
        telescope_position = telescope_position.replace("[", "").replace("]", "")
        coords = telescope_position.split(",")

        if len(coords) != 3:
            logger.error(f"Invalid telescope position format: {telescope_position}")
            return None, None, None, observatory

        # Parse the coordinates
        x = float(coords[0].replace("m", "").strip())
        y = float(coords[1].replace("m", "").strip())
        z = float(coords[2].replace("m", "").strip())
        if abs(x) < 1e6 and abs(y) < 1e6 and abs(z) < 1e6:
            logger.warning(f"Invalid telescope position format: {telescope_position}")
            print("Checking if it is a known telescope ....")
            if observatory.upper() == "LOFAR":
                print("Observatory matched with LOFAR")
                lat = "52:55:53.90"
                lon = "06:51:56.95"
                height = 50.16
            elif observatory.upper() == "MWA":
                print("Observatory matched with MWA")
                lat = "-26:42:12.09"
                lon = "116:40:14.84"
                height = 375.75
            elif observatory.upper() == "MEERKAT":
                print("Observatory matched with MEERKAT")
                lat = "-30:42:47.36"
                lon = "21:26:38.09"
                height = 1050.82
            elif observatory.upper() == "GMRT" or observatory.upper() == "UGMRT":
                print("Observatory matched with uGMRT")
                lat = "19:05:26.21"
                lon = "74:02:59.90"
                height = 639.68
            else:
                print("Observatory not matched with database of known telescopes")
                return None, None, None, observatory
        else:
            # Convert ITRF coordinates to latitude, longitude, and height
            lat, lon, height = itrf_to_geodetic(x, y, z)

        return lat, lon, height, observatory

    except Exception as e:
        logger.error(f"Error extracting telescope position: {str(e)}")
        import traceback

        traceback.print_exc()
        return None, None, None, None

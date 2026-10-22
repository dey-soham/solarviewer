import logging
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

# Only set QT_QPA_PLATFORM to offscreen when running this script directly
# This prevents issues when importing this module from other scripts
if __name__ == "__main__":
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

import numpy as np, matplotlib.pyplot as plt, astropy.units as u
from astropy.coordinates import SkyCoord, EarthLocation
from astropy.time import Time
from astropy.io import fits
from matplotlib import rcParams
from casatools import image as IA

# casatasks (immath, exportfits) are now run via subprocess to avoid memory issues

try:
    import sunpy.map
    from sunpy.coordinates import Helioprojective
    from sunpy.coordinates.sun import P

    sunpy_imported = True
except ImportError:
    logger.error("sunpy is not installed or some components are missing")
    sunpy_imported = False

from ..processing.fits_io import is_fits, readable_image_path
from ..processing.stokes import (
    fits_stokes_labels,
    plane_index,
    planes_needed,
    stokes_product,
)


rcParams["axes.linewidth"] = 1.4
rcParams["font.size"] = 12


def get_Earthlocation(fits_file="", lat=None, long=None, height=None, observatory=None):
    fits_file = readable_image_path(fits_file)
    hdul = fits.open(fits_file, memmap=True)
    header = hdul[0].header
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
        except Exception as e:
            logger.error(f"{str(e)}")
    return POS


def convert_to_hpc(
    fits_file,
    Stokes="I",
    thres=10,
    lat=None,
    long=None,
    height=None,
    observatory=None,
    rms_box=(0, 200, 0, 130),
):
    """
    Convert a FITS file to helioprojective coordinates
    TODO: Redundantly written for ndim>2 and ndim=2, should be refactored
    """
    maps, csys, psf = convert_to_hpc_planes(
        fits_file, [Stokes], thres, lat, long, height, observatory, rms_box
    )
    if maps is None:
        return None, None, None
    return maps[0], csys, psf


def convert_to_hpc_planes(
    fits_file,
    stokes_list,
    thres=10,
    lat=None,
    long=None,
    height=None,
    observatory=None,
    rms_box=(0, 200, 0, 130),
):
    """
    Like :func:`convert_to_hpc`, for several Stokes products of one image.

    The file is read and the helioprojective header worked out once, and the
    planes are rotated to solar north in parallel. Returns ``(maps, csys,
    psf)`` with one map per entry of ``stokes_list`` (None where that product
    could not be made), or ``(None, None, None)`` if the image can't be read.
    Only a single product is supported for 2D images.
    """
    fits_file = readable_image_path(fits_file)
    if not sunpy_imported:
        logger.error("Cannot convert to helioprojective coordinates without sunpy")
        return None, None, None

    try:
        ia_tool = IA()
        ia_tool.open(fits_file)
        psf = ia_tool.restoringbeam()
        csys = ia_tool.coordsys()
        ia_tool.close()
    except Exception as e:
        raise RuntimeError(f"Failed to open image {fits_file}: {e}")

    # Read the FITS file
    hdu = fits.open(fits_file, memmap=True)
    header = hdu[0].header
    if header["SIMPLE"] == False:
        logger.error("FITS file is not a valid image")
        return None, None, None
    ndim = header["NAXIS"]

    if ndim > 2:
        try:
            # Create a mapping of axis types to their positions
            axis_types = {f"CTYPE{i+1}": header[f"CTYPE{i+1}"] for i in range(ndim)}
            stokes_axis = None
            freq_axis = None
            spatial_axes = []

            # Find the axes by their types
            for axis_num, axis_type in axis_types.items():
                axis_index = int(axis_num[-1]) - 1  # Convert to 0-based index
                if axis_type == "STOKES":
                    stokes_axis = axis_index
                elif axis_type == "FREQ":
                    freq_axis = axis_index
                elif axis_type in ["RA---SIN", "DEC--SIN", "HPLN-TAN", "HPLT-TAN"]:
                    spatial_axes.append(axis_index)

            # Account for NumPy/FITS axis reversal
            # In FITS: (RA, DEC, FREQ, STOKES) -> In NumPy: (STOKES, FREQ, DEC, RA)
            numpy_axis_map = {}
            if stokes_axis is not None:
                numpy_axis_map[stokes_axis] = ndim - stokes_axis - 1
            if freq_axis is not None:
                numpy_axis_map[freq_axis] = ndim - freq_axis - 1
            for spatial_axis in spatial_axes:
                numpy_axis_map[spatial_axis] = ndim - spatial_axis - 1


            # Update the axes to their NumPy positions
            if stokes_axis is not None:
                stokes_axis = numpy_axis_map[stokes_axis]
            if freq_axis is not None:
                original_freq_axis = (
                    freq_axis + 1
                )  # Save the original FITS axis number (1-indexed)
                freq_axis = numpy_axis_map[freq_axis]

            data_all = hdu[0].data
        except Exception:
            return None, None, None

        labels = fits_stokes_labels(header) if stokes_axis is not None else None

        def plane(name):
            """The ``name`` Stokes plane, first channel, as [x, y]."""
            index = [slice(None)] * ndim
            if stokes_axis is not None:
                index[stokes_axis] = plane_index(labels, name)
            else:
                plane_index(None, name)  # only Stokes I without a Stokes axis
            if freq_axis is not None:
                index[freq_axis] = 0
            return np.array(data_all[tuple(index)]).T

        def select(Stokes):
            """The ``Stokes`` product as [y, x], the order of the FITS data."""
            planes = {name: plane(name) for name in planes_needed(Stokes)}
            return stokes_product(
                Stokes, planes, thres, rms_box, csys, sun_radius_arcmin=60
            ).T

        planes = []
        for Stokes in stokes_list:
            try:
                planes.append(select(Stokes))
            except Exception:
                planes.append(None)
        data = next((plane for plane in planes if plane is not None), None)
        if data is None:
            return None, None, None


        # Get frequency from the appropriate header keyword based on the original FITS axis
        if freq_axis is not None:
            frequency = header[f"CRVAL{original_freq_axis}"] * u.Hz
        else:
            # Try to find frequency from other header keywords if freq_axis is not identified
            if "FREQ" in header:
                frequency = header["FREQ"] * u.Hz
            else:
                raise RuntimeError("Could not determine frequency from header")
        obstime = Time(header["date-obs"])
        ia_tool = IA()
        ia_tool.open(fits_file)
        metadata_dict = ia_tool.summary(list=False, verbose=True)
        lat, long, height, observatory = extract_telescope_position(metadata_dict)
        ia_tool.close()
        if (
            observatory.upper() == "LOFAR"
            or observatory.upper() == "MWA"
            or observatory.upper() == "MEERKAT"
            or observatory.upper() == "GMRT"
            or observatory.upper() == "UGMRT"
        ):
            pass
        else:
            observatory = None
        POS = get_Earthlocation(fits_file=fits_file, observatory=observatory)
        gcrs = SkyCoord(POS.get_gcrs(obstime))
        reference_coord = SkyCoord(
            header["CRVAL1"] * u.Unit(header["CUNIT1"]),
            header["CRVAL2"] * u.Unit(header["CUNIT2"]),
            frame="gcrs",
            obstime=obstime,
            obsgeoloc=gcrs.cartesian,
            obsgeovel=gcrs.velocity.to_cartesian(),
            distance=gcrs.hcrs.distance,
        )
        reference_coord_arcsec = reference_coord.transform_to(
            Helioprojective(observer=gcrs)
        )
        cdelta_1 = (np.abs(header["CDELT1"]) * u.deg).to(u.arcsec)
        cdelta_2 = (np.abs(header["CDELT2"]) * u.deg).to(u.arcsec)
        P_angle = P(obstime) * -1

        # Ensure frequency unit is properly formatted for the FITS header
        new_header = sunpy.map.make_fitswcs_header(
            data,
            reference_coord_arcsec,
            reference_pixel=u.Quantity(
                [header["CRPIX1"] - 1, header["CRPIX2"] - 1] * u.pix,
            ),
            scale=u.Quantity([cdelta_1, cdelta_2] * u.arcsec / u.pix),
            rotation_angle=P_angle,
            wavelength=frequency.to(u.MHz).round(2),
            observatory=observatory,
        )

        # Add additional metadata to the header
        new_header["DATE-OBS"] = header.get("DATE-OBS", obstime.isot)
        new_header["TELESCOP"] = observatory
        new_header["INSTRUME"] = header.get("INSTRUME", "Unknown")
        new_header["OBJECT"] = "Sun"
        new_header["ORIGIN"] = "Solar Radio Image Viewer"

        # Add frequency information
        new_header["FREQ"] = frequency.to(u.Hz).value
        new_header["FREQUNIT"] = "Hz"

        # Add coordinate system information
        new_header["WCSNAME"] = "Helioprojective"
        new_header["CTYPE1"] = (
            "HPLN-TAN"  # Helioprojective longitude with TAN projection
        )
        new_header["CTYPE2"] = (
            "HPLT-TAN"  # Helioprojective latitude with TAN projection
        )
        new_header["CUNIT1"] = "arcsec"
        new_header["CUNIT2"] = "arcsec"

        # Add original processing information if available
        if "HISTORY" in new_header:
            for history in header["HISTORY"]:
                new_header.append(history)

        # Add new history entry
        new_history = f"Converted to helioprojective coordinates on {Time.now().isot}"
        if "HISTORY" in new_header:
            if isinstance(new_header["HISTORY"], list):
                new_header["HISTORY"].append(new_history)
            else:
                new_header["HISTORY"] = [new_header["HISTORY"], new_history]
        else:
            new_header["HISTORY"] = new_history

        # Add beam information if available (adjust BPA by P-angle rotation)
        if psf:
            new_header["BMAJ"] = header["BMAJ"]
            new_header["BMIN"] = header["BMIN"]
            # Adjust beam position angle by the P-angle rotation
            original_bpa = header.get("BPA", 0)
            rotated_bpa = original_bpa + P_angle.to(u.deg).value
            new_header["BPA"] = rotated_bpa

        # Ensure the wavelength unit is properly formatted
        if "waveunit" in new_header:
            waveunit = new_header["waveunit"].strip().lower()
            if waveunit == "mhz":
                new_header["waveunit"] = "MHz"
            elif waveunit == "ghz":
                new_header["waveunit"] = "GHz"
            elif waveunit == "khz":
                new_header["waveunit"] = "kHz"
            elif waveunit == "hz":
                new_header["waveunit"] = "Hz"

        def to_hpc_map(Stokes, plane):
            if plane is None:
                return None
            plane_header = new_header.copy()
            # Add information about the Stokes parameter
            plane_header["STOKES"] = Stokes
            map = sunpy.map.Map(plane, plane_header)
            try:
                map_rotated = map.rotate(P_angle)
            except Exception as e:
                logger.error(f"Error rotating the map by the P angle: {e}")
                map_rotated = None

            return map_rotated

        if len(planes) == 1:
            maps = [to_hpc_map(stokes_list[0], planes[0])]
        else:
            # The rotations dominate and run in parallel (scipy releases the GIL)
            with ThreadPoolExecutor(len(planes)) as pool:
                maps = list(pool.map(to_hpc_map, stokes_list, planes))
        return maps, csys, psf
    elif ndim == 2:
        Stokes = stokes_list[0]
        data = hdu[0].data
        try:
            # Try CRVAL3 first, then fallback to RESTFRQ or FREQ
            if "CRVAL3" in header:
                freq = header["CRVAL3"] * u.Hz
            elif "RESTFRQ" in header:
                freq = header["RESTFRQ"] * u.Hz
            elif "FREQ" in header:
                freq = header["FREQ"] * u.Hz
            else:
                logger.warning("No frequency keyword found in header")
                freq = None
        except Exception as e:
            logger.error(f"Error getting frequency: {e}")
            freq = None
        try:
            obstime = Time(hdu[0].header["DATE-OBS"])
        except Exception as e:
            logger.error(f"Error getting observation time: {e}")
            obstime = None
        ia_tool = IA()
        ia_tool.open(fits_file)
        metadata_dict = ia_tool.summary(list=False, verbose=True)
        lat, long, height, observatory = extract_telescope_position(metadata_dict)
        ia_tool.close()
        if (
            observatory.upper() == "LOFAR"
            or observatory.upper() == "MWA"
            or observatory.upper() == "MEERKAT"
            or observatory.upper() == "GMRT"
            or observatory.upper() == "UGMRT"
        ):
            pass
        else:
            observatory = None
        POS = get_Earthlocation(fits_file=fits_file, observatory=observatory)
        gcrs = SkyCoord(POS.get_gcrs(obstime))
        reference_coord = SkyCoord(
            header["CRVAL1"] * u.Unit(header["CUNIT1"]),
            header["CRVAL2"] * u.Unit(header["CUNIT2"]),
            frame="gcrs",
            obstime=obstime,
            obsgeoloc=gcrs.cartesian,
            obsgeovel=gcrs.velocity.to_cartesian(),
            distance=gcrs.hcrs.distance,
        )
        # Transform to Helioprojective frame (same as 4D branch)
        reference_coord_arcsec = reference_coord.transform_to(
            Helioprojective(observer=gcrs)
        )
        cdelta_1 = (np.abs(header["CDELT1"]) * u.deg).to(u.arcsec)
        cdelta_2 = (np.abs(header["CDELT2"]) * u.deg).to(u.arcsec)
        P_angle = P(obstime) * -1
        new_header = sunpy.map.make_fitswcs_header(
            data,
            reference_coord_arcsec,
            reference_pixel=u.Quantity(
                [header["CRPIX1"] - 1, header["CRPIX2"] - 1] * u.pix
            ),
            scale=u.Quantity([cdelta_1, cdelta_2] * u.arcsec / u.pix),
            rotation_angle=P_angle,
            wavelength=freq.to(u.MHz).round(2),
            observatory=observatory,
        )
        new_header["DATE-OBS"] = header.get("DATE-OBS", obstime.isot)
        new_header["TELESCOP"] = observatory
        new_header["INSTRUME"] = header.get("INSTRUME", "Unknown")
        new_header["OBJECT"] = "Sun"
        new_header["ORIGIN"] = "Solar Radio Image Viewer"
        try:
            new_header["FREQ"] = freq.to(u.Hz).value
            new_header["FREQUNIT"] = "Hz"
        except Exception as e:
            logger.error(f"Error adding frequency information: {e}")
        new_header["WCSNAME"] = "Helioprojective"
        new_header["CTYPE1"] = "HPLN-TAN"
        new_header["CTYPE2"] = "HPLT-TAN"
        new_header["CUNIT1"] = "arcsec"
        new_header["CUNIT2"] = "arcsec"
        new_header["STOKES"] = Stokes
        if psf:
            new_header["BMAJ"] = header["BMAJ"]
            new_header["BMIN"] = header["BMIN"]
            # Adjust beam position angle by the P-angle rotation
            original_bpa = header.get("BPA", 0)
            rotated_bpa = original_bpa + P_angle.to(u.deg).value
            new_header["BPA"] = rotated_bpa
        map = sunpy.map.Map(data, new_header)
        # (A fallback here once rotated by the map's own metadata instead,
        # silently; a map that cannot be rotated by the P angle is an error)
        try:
            map_rotated = map.rotate(P_angle)
        except Exception as e:
            logger.error(f"Error rotating the map by the P angle: {e}")
            map_rotated = None

        return [map_rotated], csys, psf
    else:
        return None, None, None


def convert_casaimage_to_fits(
    imagename=None, fitsname="temp.fits", dropdeg=False, overwrite=True
):
    """
    Convert a CASA image to a FITS file.
    Runs exportfits in a subprocess to avoid memory issues with casatasks.
    """
    if imagename is None:
        raise ValueError("imagename is required")

    from ..processing.casa_tasks import run_casa_task

    imagename = os.path.abspath(imagename)
    fitsname = os.path.abspath(fitsname)

    # Run exportfits in a separate process to avoid segfault
    run_casa_task(
        "exportfits",
        imagename=imagename,
        fitsimage=fitsname,
        overwrite=overwrite,
        dropdeg=dropdeg,
    )

    return fitsname


def plot_helioprojective_map(
    fits_file,
    output_file="helioprojective_map.png",
    cmap="viridis",
    figsize=(14, 12),
    dpi=300,
    show_limb=True,
    show_grid=True,
    **kwargs,
):
    """
    Plot a FITS file in helioprojective coordinates and save it to a file.

    Parameters
    ----------
    fits_file : str
        Path to the FITS file.
    output_file : str, optional
        Path to save the output image. Default is "helioprojective_map.png".
    cmap : str, optional
        Colormap to use for the plot. Default is "viridis".
    figsize : tuple, optional
        Figure size in inches. Default is (14, 12).
    dpi : int, optional
        DPI for the saved image. Default is 300.
    show_limb : bool, optional
        Whether to show the solar limb. Default is True.
    show_grid : bool, optional
        Whether to show coordinate grid lines. Default is True.
    **kwargs : dict
        Additional keyword arguments to pass to convert_to_hpc.

    Returns
    -------
    map : sunpy.map.Map or None
        The helioprojective map if successful, None otherwise.
    """
    map, cdelta_1, cdelta_2 = convert_to_hpc(fits_file, **kwargs)
    if map is not None:
        # Create a figure with a specific size
        fig = plt.figure(figsize=figsize)

        # Create a SunPy plot with proper helioprojective coordinates
        ax = fig.add_subplot(111, projection=map)

        # Plot the map with proper coordinate axes and customized appearance
        im = map.plot(axes=ax, cmap=cmap, title=False)

        # Add a colorbar with a label
        cbar = plt.colorbar(im, ax=ax, label="Intensity")
        cbar.ax.tick_params(labelsize=12)

        # Add grid lines for helioprojective coordinates if requested
        if show_grid:
            ax.grid(color="white", linestyle="--", alpha=0.7)

        # Add a limb (solar edge) overlay if requested
        if show_limb:
            map.draw_limb(axes=ax, color="white", alpha=0.5, linewidth=1.5)

        # Set title with observation information
        wavelength_str = f"{map.wavelength.value:.2f} {map.wavelength.unit}"
        title = f"Helioprojective Coordinate Map\n{wavelength_str} - {map.date.strftime('%Y-%m-%d %H:%M:%S')}"
        ax.set_title(title, fontsize=16)

        # Add coordinate labels with larger font
        ax.set_xlabel("Helioprojective Longitude (arcsec)", fontsize=14)
        ax.set_ylabel("Helioprojective Latitude (arcsec)", fontsize=14)
        ax.tick_params(labelsize=12)

        # Adjust layout to make room for labels
        plt.tight_layout()

        # Save the figure with high resolution
        plt.savefig(output_file, dpi=dpi, bbox_inches="tight")
        print(
            f"[INFO] Map saved to {output_file} with proper helioprojective coordinates"
        )

        return map
    else:
        logger.error("Failed to convert to helioprojective coordinates")
        return None


def save_helioprojective_map(map_obj, output_file):
    """
    Save a helioprojective map as a FITS file.

    Parameters
    ----------
    map_obj : sunpy.map.Map
        The helioprojective map to save.
    output_file : str
        The path to save the FITS file.

    Returns
    -------
    bool
        True if the file was saved successfully, False otherwise.
    """
    if not sunpy_imported:
        logger.error("Cannot save helioprojective map without sunpy")
        return False

    if map_obj is None:
        logger.error("No map to save")
        return False

    try:
        # Save the map as a FITS file
        map_obj.save(output_file, overwrite=True)
        logger.info(f"Helioprojective map saved to {output_file}")
        return True
    except Exception as e:
        logger.error(f"Error saving helioprojective map: {str(e)}")
        return False


def convert_and_save_hpc(
    input_fits_file,
    output_fits_file,
    Stokes="I",
    thres=10,
    lat=None,
    long=None,
    height=None,
    observatory=None,
    overwrite=True,
    temp_suffix="",
):
    """
    Convert a FITS file to helioprojective coordinates and save it as a new FITS file.

    Parameters
    ----------
    input_fits_file : str
        Path to the input FITS file/Casa Image.
    output_fits_file : str
        Path to save the output FITS file.
    Stokes : str, optional
        Stokes parameter to use. Default is "I".
    thres : float, optional
        Threshold value. Default is 10.
    lat : str, optional
        Observer latitude. Default is "-26:42:11.95" (MWA).
    long : str, optional
        Observer longitude. Default is "116:40:14.93" (MWA).
    height : float, optional
        Observer height in meters. Default is 377.8 (MWA).
    observatory : str, optional
        Observatory name. Default is "MWA".
    overwrite : bool, optional
        Whether to overwrite the output file if it exists. Default is True.
    temp_suffix : str, optional
        Suffix to add to temporary files to avoid conflicts. Default is "".

    Returns
    -------
    bool
        True if the file was saved successfully, False otherwise.
    """
    input_fits_file = readable_image_path(input_fits_file)
    if not sunpy_imported:
        logger.error("Cannot convert to helioprojective coordinates without sunpy")
        return False

    try:
        not_fits_flag = False

        # Fix: use AND instead of OR to check if NOT a FITS file
        if not is_fits(input_fits_file):
            temp_filename = os.path.join(
                tempfile.gettempdir(), f"temp_hpc{temp_suffix}_{os.getpid()}.fits"
            )
            input_fits_file = convert_casaimage_to_fits(
                imagename=input_fits_file, fitsname=temp_filename
            )
            not_fits_flag = True

        # Get BUNIT from original file BEFORE any processing
        original_bunit = None
        try:
            from astropy.io import fits as afits

            with afits.open(input_fits_file) as orig_hdu:
                original_bunit = orig_hdu[0].header.get("BUNIT", None)
        except Exception as e:
            logger.error(f"Could not read BUNIT: {e}")

        # Convert to helioprojective coordinates
        map_obj, cdelta_1, cdelta_2 = convert_to_hpc(
            input_fits_file,
            Stokes=Stokes,
            thres=thres,
            lat=lat,
            long=long,
            height=height,
            observatory=observatory,
        )
        if not_fits_flag:
            # Remove the unique temp file
            if os.path.exists(input_fits_file):
                os.remove(input_fits_file)

        if map_obj is None:
            logger.error("Failed to convert to helioprojective coordinates")
            return False

        # Save the map as a FITS file
        map_obj.save(output_fits_file, overwrite=overwrite)

        # Add BUNIT directly to the saved file
        if original_bunit:
            from astropy.io import fits as afits

            with afits.open(output_fits_file, mode="update") as hdul:
                hdul[0].header["BUNIT"] = original_bunit
                hdul.flush()

        logger.info(f"Helioprojective map saved to {output_fits_file}")

        # Return the map object for further use if needed
        return True
    except Exception as e:
        print(
            f"[ERROR] Error converting and saving to helioprojective coordinates: {str(e)}"
        )
        import traceback

        traceback.print_exc()
        return False


def convert_and_save_hpc_all_stokes(
    input_fits_file,
    output_fits_file,
    thres=10,
    lat=None,
    long=None,
    height=None,
    observatory=None,
    overwrite=True,
    temp_suffix="",
):
    """
    Convert a FITS file to helioprojective coordinates for all Stokes parameters,
    and save it as a new FITS file with Stokes axis preserved.

    Parameters
    ----------
    input_fits_file : str
        Path to the input FITS file/Casa Image.
    output_fits_file : str
        Path to save the output FITS file.
    thres : float, optional
        Threshold value. Default is 10.
    lat : str, optional
        Observer latitude.
    long : str, optional
        Observer longitude.
    height : float, optional
        Observer height in meters.
    observatory : str, optional
        Observatory name.
    overwrite : bool, optional
        Whether to overwrite the output file if it exists. Default is True.
    temp_suffix : str, optional
        Suffix to add to temporary files to avoid conflicts. Default is "".

    Returns
    -------
    bool
        True if the file was saved successfully, False otherwise.
    """
    input_fits_file = readable_image_path(input_fits_file)
    if not sunpy_imported:
        logger.error("Cannot convert to helioprojective coordinates without sunpy")
        return False

    try:
        not_fits_flag = False
        original_input = input_fits_file

        if not is_fits(input_fits_file):
            # Create unique temp filename with provided suffix in a writable directory
            temp_filename = os.path.join(
                tempfile.gettempdir(), f"temp_hpc_all{temp_suffix}_{os.getpid()}.fits"
            )
            input_fits_file = convert_casaimage_to_fits(
                imagename=input_fits_file, fitsname=temp_filename
            )
            not_fits_flag = True

        # Check how many Stokes parameters are available
        hdu = fits.open(input_fits_file)
        header = hdu[0].header
        ndim = header["NAXIS"]

        # Get BUNIT early before temp file is deleted
        original_bunit = header.get("BUNIT", None)
        if original_bunit:
            pass

        # Find Stokes axis
        stokes_axis_fits = None
        num_stokes = 1
        for i in range(1, ndim + 1):
            if header.get(f"CTYPE{i}", "").upper() == "STOKES":
                stokes_axis_fits = i
                num_stokes = header[f"NAXIS{i}"]
                break
        hdu.close()

        if stokes_axis_fits is None or num_stokes == 1:
            # Single Stokes - just use regular conversion
            return convert_and_save_hpc(
                input_fits_file=original_input,
                output_fits_file=output_fits_file,
                Stokes="I",
                thres=thres,
                lat=lat,
                long=long,
                height=height,
                observatory=observatory,
                overwrite=overwrite,
                temp_suffix=temp_suffix,
            )

        # Convert each Stokes parameter
        stokes_list = ["I", "Q", "U", "V"][:num_stokes]
        stokes_maps = []
        reference_header = None

        # All planes in one go: the file is read and the geometry worked out once
        map_objs, _, _ = convert_to_hpc_planes(
            input_fits_file,
            stokes_list,
            thres=thres,
            lat=lat,
            long=long,
            height=height,
            observatory=observatory,
        )
        if map_objs is None:
            raise RuntimeError(f"Failed to convert first Stokes {stokes_list[0]}")
        for stokes, map_obj in zip(stokes_list, map_objs):
            if map_obj is not None:
                stokes_maps.append(map_obj.data)
                if reference_header is None:
                    reference_header = dict(map_obj.meta)
            else:
                logger.error(f"Failed to convert Stokes {stokes}, using zeros")
                if stokes_maps:
                    stokes_maps.append(np.zeros_like(stokes_maps[0]))
                else:
                    raise RuntimeError(f"Failed to convert first Stokes {stokes}")

        if not_fits_flag:
            # Remove the temp file
            if os.path.exists(input_fits_file):
                os.remove(input_fits_file)

        if not stokes_maps:
            logger.error("No Stokes maps were successfully converted")
            return False

        # Ensure all Stokes maps have the same shape (reference is first map - Stokes I)
        reference_shape = stokes_maps[0].shape
        aligned_maps = [stokes_maps[0]]

        for i, smap in enumerate(stokes_maps[1:], 1):
            if smap.shape != reference_shape:
                # Resize to match reference shape
                from scipy.ndimage import zoom

                zoom_factors = (
                    reference_shape[0] / smap.shape[0],
                    reference_shape[1] / smap.shape[1],
                )
                smap = zoom(smap, zoom_factors, order=1)
                # Ensure exact shape match after zoom
                smap = smap[: reference_shape[0], : reference_shape[1]]
            aligned_maps.append(smap)

        # Stack all Stokes into a single 3D array
        stokes_data = np.stack(aligned_maps, axis=0)

        # Create new header with Stokes axis
        from astropy.io import fits as afits

        # BUNIT was already read earlier (before temp file deletion)

        new_header = afits.Header()
        for key, value in reference_header.items():
            if key not in ["COMMENT", "HISTORY", ""]:
                try:
                    new_header[key] = value
                except Exception:
                    pass

        # Update for 3D with Stokes
        new_header["NAXIS"] = 3
        new_header["NAXIS3"] = len(stokes_maps)
        new_header["CTYPE3"] = "STOKES"
        new_header["CRVAL3"] = 1.0
        new_header["CDELT3"] = 1.0
        new_header["CRPIX3"] = 1.0
        new_header["CUNIT3"] = ""

        # Preserve BUNIT from original file
        if original_bunit:
            new_header["BUNIT"] = original_bunit

        # Remove single-Stokes marker if present
        if "STOKES" in new_header:
            del new_header["STOKES"]

        # Write the combined FITS file
        hdu_out = afits.PrimaryHDU(stokes_data, header=new_header)
        hdu_out.header.add_history(
            "Converted to Helioprojective coordinates with SolarViewer"
        )
        hdu_out.writeto(output_fits_file, overwrite=overwrite)

        return True

    except Exception as e:
        print(
            f"[ERROR] Error converting and saving all Stokes to helioprojective: {str(e)}"
        )
        import traceback

        traceback.print_exc()
        return False


def convert_hpc_to_radec(
    input_fits_file,
    output_fits_file,
    overwrite=True,
):
    """
    Convert a helioprojective (Solar-X/Y) FITS file back to RA/Dec coordinates.

    This actually rotates the data by the P-angle to undo the solar rotation.

    Parameters
    ----------
    input_fits_file : str
        Path to the input helioprojective FITS file.
    output_fits_file : str
        Path to save the output RA/Dec FITS file.
    overwrite : bool, optional
        Whether to overwrite the output file if it exists. Default is True.

    Returns
    -------
    bool
        True if the file was saved successfully, False otherwise.
    """
    input_fits_file = readable_image_path(input_fits_file)
    if not sunpy_imported:
        logger.error("Cannot convert from helioprojective coordinates without sunpy")
        return False

    try:
        from astropy.io import fits as afits
        from scipy.ndimage import rotate


        # Load the HPC FITS file
        with afits.open(input_fits_file) as hdul:
            hpc_data = hdul[0].data.copy()
            hpc_header = hdul[0].header.copy()

        # Check if it's actually HPC
        ctype1 = hpc_header.get("CTYPE1", "").upper()
        ctype2 = hpc_header.get("CTYPE2", "").upper()
        if "HPLN" not in ctype1 and "HPLT" not in ctype2 and "SOLAR" not in ctype1:
            print(
                f"[ERROR] File does not appear to be in HPC coordinates (CTYPE1={ctype1}, CTYPE2={ctype2})"
            )
            return False

        # Get observation time
        obstime_str = hpc_header.get("DATE-OBS", None)
        if obstime_str is None:
            logger.error("DATE-OBS not found in header")
            return False
        obstime = Time(obstime_str)

        # Get P-angle - this is the rotation we need to undo
        p_angle = P(obstime).to(u.deg).value

        # Get Sun center in RA/Dec at observation time
        sun_coord = SkyCoord(
            0 * u.arcsec,
            0 * u.arcsec,
            frame=Helioprojective(obstime=obstime, observer="earth"),
        )
        sun_radec = sun_coord.transform_to("gcrs")
        sun_ra = sun_radec.ra.deg
        sun_dec = sun_radec.dec.deg

        # Get HPC coordinate parameters
        crpix1 = hpc_header.get("CRPIX1", hpc_data.shape[-1] / 2)
        crpix2 = hpc_header.get("CRPIX2", hpc_data.shape[-2] / 2)
        cdelt1 = hpc_header.get("CDELT1", 1.0)
        cdelt2 = hpc_header.get("CDELT2", 1.0)

        # Rotate the data by -P_angle to undo the solar rotation
        # scipy.ndimage.rotate uses counter-clockwise positive angles
        # so we negate to rotate clockwise (undoing the original rotation)
        # Using reshape=False to maintain original image dimensions
        if hpc_data.ndim == 2:
            rotated_data = rotate(
                hpc_data, -p_angle, reshape=False, order=1, mode="constant", cval=np.nan
            )
        elif hpc_data.ndim == 3:
            # Rotate each plane separately (could be Stokes or frequency)
            rotated_data = np.zeros_like(hpc_data)
            for i in range(hpc_data.shape[0]):
                rotated_data[i] = rotate(
                    hpc_data[i],
                    -p_angle,
                    reshape=False,
                    order=1,
                    mode="constant",
                    cval=np.nan,
                )
        elif hpc_data.ndim == 4:
            # 4D data: typically (Stokes, Freq, Dec, RA) or (Freq, Stokes, Dec, RA)
            rotated_data = np.zeros_like(hpc_data)
            for i in range(hpc_data.shape[0]):
                for j in range(hpc_data.shape[1]):
                    rotated_data[i, j] = rotate(
                        hpc_data[i, j],
                        -p_angle,
                        reshape=False,
                        order=1,
                        mode="constant",
                        cval=np.nan,
                    )
        else:
            logger.error(f"Unexpected data dimensions: {hpc_data.ndim}")
            return False

        # Crop to non-NaN bounding box to remove NaN borders
        if rotated_data.ndim == 2:
            valid_mask = ~np.isnan(rotated_data)
        elif rotated_data.ndim == 3:
            # Use union of all planes for the mask
            valid_mask = ~np.isnan(rotated_data[0])
            for i in range(1, rotated_data.shape[0]):
                valid_mask |= ~np.isnan(rotated_data[i])
        else:  # 4D
            # Use union of all 2D slices
            valid_mask = ~np.isnan(rotated_data[0, 0])
            for i in range(rotated_data.shape[0]):
                for j in range(rotated_data.shape[1]):
                    valid_mask |= ~np.isnan(rotated_data[i, j])

        # Find bounding box of valid data
        rows = np.any(valid_mask, axis=1)
        cols = np.any(valid_mask, axis=0)
        if not np.any(rows) or not np.any(cols):
            logger.error("No valid data after rotation")
            return False

        rmin, rmax = np.where(rows)[0][[0, -1]]
        cmin, cmax = np.where(cols)[0][[0, -1]]

        # Add small padding to avoid over-cropping due to interpolation edge effects
        padding = 0  # pixels
        if rotated_data.ndim == 2:
            rmin = max(0, rmin - padding)
            rmax = min(rotated_data.shape[0] - 1, rmax + padding)
            cmin = max(0, cmin - padding)
            cmax = min(rotated_data.shape[1] - 1, cmax + padding)
        elif rotated_data.ndim == 3:
            rmin = max(0, rmin - padding)
            rmax = min(rotated_data.shape[1] - 1, rmax + padding)
            cmin = max(0, cmin - padding)
            cmax = min(rotated_data.shape[2] - 1, cmax + padding)
        else:  # 4D
            rmin = max(0, rmin - padding)
            rmax = min(rotated_data.shape[2] - 1, rmax + padding)
            cmin = max(0, cmin - padding)
            cmax = min(rotated_data.shape[3] - 1, cmax + padding)

        # Crop the data
        if rotated_data.ndim == 2:
            rotated_data = rotated_data[rmin : rmax + 1, cmin : cmax + 1]
        elif rotated_data.ndim == 3:
            rotated_data = rotated_data[:, rmin : rmax + 1, cmin : cmax + 1]
        else:  # 4D
            rotated_data = rotated_data[:, :, rmin : rmax + 1, cmin : cmax + 1]

        # Adjust CRPIX for the crop
        crpix1 = crpix1 - cmin
        crpix2 = crpix2 - rmin


        # Convert cdelt from arcsec to degrees if needed
        cunit1 = hpc_header.get("CUNIT1", "arcsec").lower()
        cunit2 = hpc_header.get("CUNIT2", "arcsec").lower()
        if "arcsec" in cunit1:
            cdelt1_deg = cdelt1 / 3600.0
        elif "deg" in cunit1:
            cdelt1_deg = cdelt1
        else:
            cdelt1_deg = cdelt1 / 3600.0

        if "arcsec" in cunit2:
            cdelt2_deg = cdelt2 / 3600.0
        elif "deg" in cunit2:
            cdelt2_deg = cdelt2
        else:
            cdelt2_deg = cdelt2 / 3600.0

        # Create new RA/Dec header
        new_header = afits.Header()

        # Copy essential keywords
        for key in [
            "SIMPLE",
            "BITPIX",
            "NAXIS",
            "NAXIS1",
            "NAXIS2",
            "BMAJ",
            "BMIN",
            "BPA",
            "BUNIT",
            "TELESCOP",
            "INSTRUME",
            "DATE-OBS",
            "FREQ",
            "FREQUNIT",
            "OBJECT",
            "ORIGIN",
            "CRVAL3",
            "CRVAL4",
            "RESTFRQ",
        ]:
            if key in hpc_header:
                new_header[key] = hpc_header[key]

        # Handle Stokes and frequency axes based on data dimensions
        if hpc_data.ndim == 3:
            # 3D: could be Stokes or frequency - preserve original axis info
            orig_freq = (
                hpc_header.get("CRVAL3")
                or hpc_header.get("RESTFRQ")
                or hpc_header.get("FREQ")
            )

            new_header["NAXIS"] = 3
            new_header["NAXIS3"] = rotated_data.shape[0]
            new_header["CTYPE3"] = hpc_header.get("CTYPE3", "STOKES")
            new_header["CRVAL3"] = hpc_header.get("CRVAL3", 1.0)
            new_header["CDELT3"] = hpc_header.get("CDELT3", 1.0)
            new_header["CRPIX3"] = hpc_header.get("CRPIX3", 1.0)
            if hpc_header.get("CUNIT3"):
                new_header["CUNIT3"] = hpc_header["CUNIT3"]

            # Preserve frequency in RESTFRQ if available
            if orig_freq and "RESTFRQ" not in new_header:
                new_header["RESTFRQ"] = float(orig_freq)

        elif hpc_data.ndim == 4:
            # 4D: Stokes + frequency - preserve both axes
            new_header["NAXIS"] = 4
            new_header["NAXIS3"] = rotated_data.shape[1]  # Inner axis (usually freq)
            new_header["NAXIS4"] = rotated_data.shape[0]  # Outer axis (usually Stokes)

            # Copy axis 3 info (usually frequency)
            new_header["CTYPE3"] = hpc_header.get("CTYPE3", "FREQ")
            new_header["CRVAL3"] = hpc_header.get("CRVAL3", 1.0)
            new_header["CDELT3"] = hpc_header.get("CDELT3", 1.0)
            new_header["CRPIX3"] = hpc_header.get("CRPIX3", 1.0)
            if hpc_header.get("CUNIT3"):
                new_header["CUNIT3"] = hpc_header["CUNIT3"]

            # Copy axis 4 info (usually Stokes)
            new_header["CTYPE4"] = hpc_header.get("CTYPE4", "STOKES")
            new_header["CRVAL4"] = hpc_header.get("CRVAL4", 1.0)
            new_header["CDELT4"] = hpc_header.get("CDELT4", 1.0)
            new_header["CRPIX4"] = hpc_header.get("CRPIX4", 1.0)
            if hpc_header.get("CUNIT4"):
                new_header["CUNIT4"] = hpc_header["CUNIT4"]

        # Set RA/Dec coordinate system (SIN projection for radio)
        new_header["CTYPE1"] = "RA---SIN"
        new_header["CTYPE2"] = "DEC--SIN"
        new_header["CUNIT1"] = "deg"
        new_header["CUNIT2"] = "deg"
        new_header["CRPIX1"] = crpix1
        new_header["CRPIX2"] = crpix2
        new_header["CDELT1"] = -abs(cdelt1_deg)  # RA increases to the left
        new_header["CDELT2"] = abs(cdelt2_deg)
        new_header["CRVAL1"] = sun_ra
        new_header["CRVAL2"] = sun_dec

        # No PC matrix needed - rotation already applied to data
        new_header["PC1_1"] = 1.0
        new_header["PC1_2"] = 0.0
        new_header["PC2_1"] = 0.0
        new_header["PC2_2"] = 1.0

        new_header["RADESYS"] = "ICRS"
        new_header["EQUINOX"] = 2000.0

        # Copy existing HISTORY entries from original header
        if "HISTORY" in hpc_header:
            for hist in hpc_header["HISTORY"]:
                new_header.add_history(str(hist))

        # Add conversion history
        new_header.add_history(
            f"Converted from Helioprojective to RA/Dec with SolarViewer (rotated {-p_angle:.2f} deg)"
        )

        # Write output file
        hdu = afits.PrimaryHDU(data=rotated_data, header=new_header)
        hdu.writeto(output_fits_file, overwrite=overwrite)

        return True

    except Exception as e:
        logger.error(f"Error converting HPC to RA/Dec: {str(e)}")
        import traceback

        traceback.print_exc()
        return False


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


def geodetic_to_itrf(lat, lon, height):
    """
    Convert geodetic coordinates to ITRF (International Terrestrial Reference Frame) coordinates.

    Parameters
    ----------
    lat : str or float
        Latitude in the format "DD:MM:SS.SS" or decimal degrees.
    lon : str or float
        Longitude in the format "DD:MM:SS.SS" or decimal degrees.
    height : float
        Height in meters above the WGS84 ellipsoid.

    Returns
    -------
    x : float
        X coordinate in meters.
    y : float
        Y coordinate in meters.
    z : float
        Z coordinate in meters.

    Examples
    --------
    >>> x, y, z = geodetic_to_itrf("49:08:42.04", "12:52:38.85", 669.13)
    >>> print(f"{x:.1f}, {y:.1f}, {z:.1f}")
    '4075539.5, 931735.8, 4801629.6'
    """
    try:
        # Convert lat/lon from string to decimal degrees if needed
        if isinstance(lat, str):
            lat = dms_to_decimal(lat)
        if isinstance(lon, str):
            lon = dms_to_decimal(lon)

        # Use astropy's EarthLocation for the conversion if available
        try:
            # Create EarthLocation object from geodetic coordinates
            location = EarthLocation.from_geodetic(lon, lat, height, unit=u.m)

            # Get ITRF coordinates
            x = location.x.value
            y = location.y.value
            z = location.z.value

        except Exception as e:
            logger.error(f"Error {str(e)}. Falling back to manual calculation.")
            # Convert to radians
            lat_rad = np.radians(lat)
            lon_rad = np.radians(lon)

            # WGS84 ellipsoid parameters
            a = 6378137.0  # semi-major axis in meters
            f = 1 / 298.257223563  # flattening
            e_sq = 2 * f - f**2  # eccentricity squared

            # Calculate N (radius of curvature in the prime vertical)
            N = a / np.sqrt(1 - e_sq * np.sin(lat_rad) ** 2)

            # Calculate ECEF coordinates
            x = (N + height) * np.cos(lat_rad) * np.cos(lon_rad)
            y = (N + height) * np.cos(lat_rad) * np.sin(lon_rad)
            z = (N * (1 - e_sq) + height) * np.sin(lat_rad)

        return x, y, z

    except Exception as e:
        logger.error(f"Error converting geodetic to ITRF: {str(e)}")
        import traceback

        traceback.print_exc()
        return None, None, None


def dms_to_decimal(dms_str):
    """
    Convert a coordinate string in DD:MM:SS.SS format to decimal degrees.

    Parameters
    ----------
    dms_str : str
        Coordinate string in the format "DD:MM:SS.SS" or "-DD:MM:SS.SS".

    Returns
    -------
    float
        Coordinate in decimal degrees.
    """
    try:
        # Check if the string is negative
        is_negative = dms_str.startswith("-")
        if is_negative:
            dms_str = dms_str[1:]  # Remove the negative sign

        # Split the string into degrees, minutes, and seconds
        parts = dms_str.split(":")
        if len(parts) != 3:
            raise ValueError(
                f"Invalid DMS format: {dms_str}. Expected format: DD:MM:SS.SS"
            )

        degrees = float(parts[0])
        minutes = float(parts[1])
        seconds = float(parts[2])

        # Convert to decimal degrees
        decimal = degrees + minutes / 60 + seconds / 3600

        # Apply negative sign if needed
        if is_negative:
            decimal = -decimal

        return decimal

    except Exception as e:
        logger.error(f"Error converting DMS to decimal: {str(e)}")
        raise


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


def test_extract_telescope_position():
    """
    Test the extract_telescope_position function with a sample metadata dictionary.
    """
    # Sample metadata dictionary
    metadata = {
        "axisnames": ["Right Ascension", "Declination", "Stokes", "Frequency"],
        "axisunits": ["rad", "rad", "", "Hz"],
        "defaultmask": "",
        "hasmask": False,
        "imagetype": "Intensity",
        "incr": [-2.08469883e-04, 2.08469883e-04, 1.00000000e00, 1.60000000e05],
        "masks": [],
        "messages": [
            "\nImage name       : solar_2014_11_03_06_12_50.20_125.995MHz.image\nObject name      : Sun\nImage type       : PagedImage\nImage quantity   : Intensity\nPixel mask(s)    : None\nRegion(s)        : None\nImage units      : Jy/beam\nRestoring Beam   : 408.885 arcsec, 303.362 arcsec, -67.7405 deg",
            "\nDirection reference : J2000\nSpectral  reference : Undefined\nVelocity  type      : RADIO\nTelescope           : MWA\nObserver            : DivyaOberoi\nDate observation    : 2014/11/03/06:12:50.200000\nTelescope position: [-2.55945e+06m, 5.09537e+06m, -2.84906e+06m] (ITRF)\n\nAxis Coord Type      Name             Proj Shape Tile   Coord value at pixel    Coord incr Units\n------------------------------------------------------------------------------------------------ \n0    0     Direction Right Ascension   SIN  1280  160  14:30:29.637   640.00 -4.300000e+01 arcsec\n1    0     Direction Declination       SIN  1280  160 -14.58.57.711   640.00  4.300000e+01 arcsec\n2    1     Stokes    Stokes                    4    1       I Q U V\n3    2     Spectral  Frequency                 1    1   1.25995e+08     0.00  1.600000e+05 Hz",
        ],
        "ndim": 4,
        "refpix": [640.0, 640.0, 0.0, 0.0],
        "refval": [-2.48493895e00, -2.61497402e-01, 1.00000000e00, 1.25995000e08],
        "restoringbeam": {
            "major": {"unit": "arcsec", "value": 408.88454881068},
            "minor": {"unit": "arcsec", "value": 303.36225356523596},
            "positionangle": {"unit": "deg", "value": -67.74046593535},
        },
        "shape": [1280, 1280, 4, 1],
        "tileshape": [160, 160, 1, 1],
        "unit": "Jy/beam",
    }

    # Test with the original metadata
    lat, lon, height, observatory = extract_telescope_position(metadata)

    # Test with metadata that has observatory directly in the dictionary
    metadata2 = metadata.copy()
    metadata2["observatory"] = "MWA"

    lat2, lon2, height2, observatory2 = extract_telescope_position(metadata2)

    return lat, lon, height, observatory


def main():
    """fits_file = "/home/soham/solarviewer/test_data/mwa_solar.fits"
    hpc_map, c1, c2 = convert_to_hpc(
        fits_file,
        lat="52:55:53.90",
        long="06:51:56.95",
        height=50.16,
        observatory="LOFAR",
        Stokes="Lfrac",
    )
    fig = plt.figure()
    ax = fig.add_subplot(111, projection=hpc_map)
    im = hpc_map.plot(axes=ax, cmap="gray", title=False)
    plt.savefig("hpc_map.png")"""

    # Example 1: Basic usage with default parameters
    """plot_helioprojective_map(fits_file)

    # Example 2: Customized plot
    plot_helioprojective_map(
        fits_file,
        output_file="custom_helioprojective_map.png",
        cmap="hot",
        figsize=(10, 10),
        dpi=150,
        show_limb=True,
        show_grid=False,
        observatory="LOFAR",  # This is passed to convert_to_hpc
    )"""


if __name__ == "__main__":
    # main()
    """imagename = "/home/soham/solarviewer/test_data/LOFAR_HBA_noisestorm.fits"
    from casatools import image as IA

    ia = IA()
    ia.open(imagename)
    metadata = ia.summary(list=False)
    ia.close()
    print(metadata)
    lat, lon, height, observatory = extract_telescope_position(metadata)
    print(f"Latitude: {lat}")
    print(f"Longitude: {lon}")
    print(f"Height: {height} meters")
    print(f"Observatory: {observatory}")"""
    import argparse

    # Create the argument parser
    parser = argparse.ArgumentParser(
        description="Convert a FITS file to helioprojective coordinates and save it as a FITS file"
    )

    # Add subparsers for different commands
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Subparser for the convert command
    convert_parser = subparsers.add_parser(
        "convert", help="Convert a FITS file to helioprojective coordinates"
    )
    convert_parser.add_argument("input_file", help="Path to the input FITS file")
    convert_parser.add_argument("output_file", help="Path to save the output FITS file")
    convert_parser.add_argument(
        "--stokes", default="I", help="Stokes parameter to use (default: I)"
    )
    convert_parser.add_argument(
        "--threshold", type=float, default=10, help="Threshold value (default: 10)"
    )
    convert_parser.add_argument(
        "--lat",
        default=None,
        help="Observer latitude (default: None (Example: -26:42:11.95, MWA))",
    )
    convert_parser.add_argument(
        "--long",
        default=None,
        help="Observer longitude (default: None (Example: 116:40:14.93, MWA))",
    )
    convert_parser.add_argument(
        "--height",
        type=float,
        default=None,
        help="Observer height in meters (default: None (Example: 377.8, MWA))",
    )
    convert_parser.add_argument(
        "--observatory", default="MWA", help="Observatory name (default: MWA)"
    )
    convert_parser.add_argument(
        "--no-overwrite",
        action="store_true",
        help="Do not overwrite the output file if it exists",
    )

    # Subparser for the itrf command
    itrf_parser = subparsers.add_parser(
        "itrf", help="Convert ITRF coordinates to geodetic coordinates"
    )
    itrf_parser.add_argument("x", type=float, help="X coordinate in meters")
    itrf_parser.add_argument("y", type=float, help="Y coordinate in meters")
    itrf_parser.add_argument("z", type=float, help="Z coordinate in meters")
    itrf_parser.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="Scale factor to apply to the coordinates (default: 1.0)",
    )

    # Subparser for the geodetic command
    geodetic_parser = subparsers.add_parser(
        "geodetic", help="Convert geodetic coordinates to ITRF coordinates"
    )
    geodetic_parser.add_argument(
        "lat", help="Latitude in the format DD:MM:SS.SS or decimal degrees"
    )
    geodetic_parser.add_argument(
        "lon", help="Longitude in the format DD:MM:SS.SS or decimal degrees"
    )
    geodetic_parser.add_argument(
        "height", type=float, help="Height in meters above the WGS84 ellipsoid"
    )

    # Subparser for the test command
    test_parser = subparsers.add_parser(
        "test", help="Run tests for the helioprojective module"
    )
    test_parser.add_argument(
        "--extract-position",
        action="store_true",
        help="Test the extract_telescope_position function",
    )

    # Parse the arguments
    args = parser.parse_args()

    # Execute the appropriate command
    if args.command == "convert":
        success = convert_and_save_hpc(
            args.input_file,
            args.output_file,
            Stokes=args.stokes,
            thres=args.threshold,
            lat=args.lat,
            long=args.long,
            height=args.height,
            observatory=args.observatory,
            overwrite=not args.no_overwrite,
        )

        if success:
            print("Conversion successful!")
        else:
            print("Conversion failed.")
            sys.exit(1)
    elif args.command == "itrf":
        lat, lon, height = itrf_to_geodetic(args.x, args.y, args.z, args.scale)
        if lat is not None:
            print(f"Latitude: {lat}")
            print(f"Longitude: {lon}")
            print(f"Height: {height} meters")
            print("\nUse these values in convert_to_hpc as:")
            print(f'lat="{lat}", long="{lon}", height={height}')
        else:
            print("ITRF conversion failed.")
            sys.exit(1)
    elif args.command == "geodetic":
        try:
            # Try to convert lat/lon to float if they are in decimal format
            try:
                lat = float(args.lat)
            except ValueError:
                lat = args.lat

            try:
                lon = float(args.lon)
            except ValueError:
                lon = args.lon

            x, y, z = geodetic_to_itrf(lat, lon, args.height)
            if x is not None:
                print("ITRF Coordinates:")
                print(f"X: {x:.3f} meters")
                print(f"Y: {y:.3f} meters")
                print(f"Z: {z:.3f} meters")
            else:
                print("Geodetic to ITRF conversion failed.")
                sys.exit(1)
        except Exception as e:
            print(f"Error: {str(e)}")
            sys.exit(1)
    elif args.command == "test":
        if args.extract_position:
            test_extract_telescope_position()
        else:
            print(
                "No test specified. Use --extract-position to test the extract_telescope_position function."
            )
            sys.exit(1)
    else:
        # Default behavior for backward compatibility
        if len(sys.argv) >= 3 and not sys.argv[1].startswith("-"):
            # Assume the first two arguments are input_file and output_file
            success = convert_and_save_hpc(
                sys.argv[1],
                sys.argv[2],
                # Parse any additional arguments
                **{
                    k.lstrip("-").replace("-", "_"): v
                    for k, v in zip(sys.argv[3::2], sys.argv[4::2])
                },
            )

            if success:
                print("Conversion successful!")
            else:
                print("Conversion failed.")
                sys.exit(1)
        else:
            parser.print_help()
            sys.exit(1)

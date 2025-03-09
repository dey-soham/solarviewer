# requires sunpy
import os
import sys

# Only set QT_QPA_PLATFORM to offscreen when running this script directly
# This prevents issues when importing this module from other scripts
if __name__ == "__main__":
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

import numpy as np, matplotlib.pyplot as plt, astropy.units as u, matplotlib.patches as patches
from astropy.coordinates import SkyCoord, EarthLocation
from astropy.time import Time
from astropy.io import fits
from datetime import timedelta
from matplotlib import rcParams
from casatools import image as IA
from casatasks import immath, exportfits
import math

try:
    from sunpy.net import Fido, attrs as a
    import sunpy.map
    from sunpy.coordinates import Helioprojective
    from sunpy.coordinates.sun import P

    sunpy_imported = True
except ImportError:
    print("sunpy is not installed or some components are missing")
    print("Please install sunpy with: pip install sunpy[all]")
    sunpy_imported = False

from .utils import estimate_rms_near_Sun, remove_pixels_away_from_sun


rcParams["axes.linewidth"] = 1.4
rcParams["font.size"] = 12


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
    if not sunpy_imported:
        print("Cannot convert to helioprojective coordinates without sunpy")
        return None, None, None

    single_stokes_flag = False
    try:
        ia_tool = IA()
        ia_tool.open(fits_file)
        psf = ia_tool.restoringbeam()
        csys = ia_tool.coordsys()
        ia_tool.close()
        # csys = "hpc"
    except Exception as e:
        raise RuntimeError(f"Failed to open image {fits_file}: {e}")

    # Read the FITS file
    hdu = fits.open(fits_file)
    header = hdu[0].header
    if header["SIMPLE"] == False:
        print("Error: FITS file is not a valid image")
        return None, None, None
    ndim = header["NAXIS"]
    if ndim < 2:
        single_stokes_flag = True

    if ndim > 2:
        stokes_map = {"I": 0, "Q": 1, "U": 2, "V": 3}
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

            print(f"FITS axes mapping to NumPy positions: {numpy_axis_map}")
            print(f"Spatial axes in FITS (0-indexed): {spatial_axes}")

            # Update the axes to their NumPy positions
            if stokes_axis is not None:
                original_stokes_axis = (
                    stokes_axis + 1
                )  # Save the original FITS axis number (1-indexed)
                stokes_axis = numpy_axis_map[stokes_axis]
            if freq_axis is not None:
                original_freq_axis = (
                    freq_axis + 1
                )  # Save the original FITS axis number (1-indexed)
                freq_axis = numpy_axis_map[freq_axis]

            if stokes_axis is not None and header[f"NAXIS{original_stokes_axis}"] == 1:
                single_stokes_flag = True

            data_all = hdu[0].data
            print(f"Data from FITS file: {data_all.shape}")
            if Stokes in ["I", "Q", "U", "V"]:
                idx = stokes_map.get(Stokes)
                if idx is None:
                    raise ValueError(f"Unknown Stokes parameter: {Stokes}")
                slice_list = [slice(None)] * ndim
                if stokes_axis is not None:
                    if single_stokes_flag:
                        if Stokes != "I":
                            raise RuntimeError(
                                "The image is single stokes, but the Stokes parameter is not 'I'."
                            )
                    slice_list[stokes_axis] = idx
                    print(
                        f"Stokes axis in FITS: {original_stokes_axis} (NumPy position: {stokes_axis})"
                    )
                    if freq_axis is not None:
                        print(
                            f"Frequency axis in FITS: {original_freq_axis} (NumPy position: {freq_axis})"
                        )
                if freq_axis is not None:
                    slice_list[freq_axis] = 0
                data = data_all[tuple(slice_list)]
            elif Stokes == "L":
                if stokes_axis is None:
                    raise RuntimeError("The image does not have a Stokes axis.")
                elif single_stokes_flag:
                    raise RuntimeError(
                        "The image is single stokes, but the Stokes parameter is not 'I'."
                    )
                slice_list_Q = [slice(None)] * ndim
                slice_list_U = [slice(None)] * ndim
                slice_list_Q[stokes_axis] = 1
                slice_list_U[stokes_axis] = 2
                slice_list_Q[freq_axis] = 0
                slice_list_U[freq_axis] = 0
                pix_Q = data_all[tuple(slice_list_Q)]
                pix_U = data_all[tuple(slice_list_U)]
                data = np.sqrt(pix_Q**2 + pix_U**2)
            elif Stokes == "Lfrac":
                if stokes_axis is None:
                    raise RuntimeError("The image does not have a Stokes axis.")
                elif single_stokes_flag:
                    raise RuntimeError(
                        "The image is single stokes, but the Stokes parameter is not 'I'."
                    )
                try:
                    immath(imagename=fits_file, outfile="temp_p_map.im", mode="lpoli")
                    p_rms = estimate_rms_near_Sun("temp_p_map.im", "I", rms_box)
                except Exception as e:
                    raise RuntimeError(f"Error generating polarization map: {e}")
                finally:
                    os.system("rm -rf temp_p_map.im")
                slice_list_Q = [slice(None)] * ndim
                slice_list_U = [slice(None)] * ndim
                slice_list_I = [slice(None)] * ndim
                slice_list_Q[stokes_axis] = 1
                slice_list_U[stokes_axis] = 2
                slice_list_I[stokes_axis] = 0
                slice_list_Q[freq_axis] = 0
                slice_list_U[freq_axis] = 0
                slice_list_I[freq_axis] = 0
                pix_Q = data_all[tuple(slice_list_Q)]
                pix_U = data_all[tuple(slice_list_U)]
                pix_I = data_all[tuple(slice_list_I)]
                L = np.sqrt(pix_Q**2 + pix_U**2)
                mask = L < (thres * p_rms)
                L[mask] = 0
                Lfrac = L / pix_I
                data = remove_pixels_away_from_sun(Lfrac, csys, 60)
            elif Stokes == "Vfrac":
                if stokes_axis is None:
                    raise RuntimeError("The image does not have a Stokes axis.")
                elif single_stokes_flag:
                    raise RuntimeError(
                        "The image is single stokes, but the Stokes parameter is not 'I'."
                    )
                slice_list_V = [slice(None)] * ndim
                slice_list_I = [slice(None)] * ndim
                slice_list_V[stokes_axis] = 3
                slice_list_I[stokes_axis] = 0
                slice_list_V[freq_axis] = 0
                slice_list_I[freq_axis] = 0
                pix_V = data_all[tuple(slice_list_V)]
                pix_I = data_all[tuple(slice_list_I)]
                v_rms = estimate_rms_near_Sun(fits_file, "V", rms_box)
                mask = np.abs(pix_V) < (thres * v_rms)
                pix_V[mask] = 0
                Vfrac = pix_V / pix_I
                data = remove_pixels_away_from_sun(Vfrac, csys, 60)
            elif Stokes == "Q/I":
                if stokes_axis is None:
                    raise RuntimeError("The image does not have a Stokes axis.")
                elif single_stokes_flag:
                    raise RuntimeError(
                        "The image is single stokes, but the Stokes parameter is not 'I'."
                    )
                q_rms = estimate_rms_near_Sun(fits_file, "Q", rms_box)
                slice_list_Q = [slice(None)] * ndim
                slice_list_I = [slice(None)] * ndim
                slice_list_Q[stokes_axis] = 1
                slice_list_I[stokes_axis] = 0
                slice_list_Q[freq_axis] = 0
                slice_list_I[freq_axis] = 0
                pix_Q = data_all[tuple(slice_list_Q)]
                mask = np.abs(pix_Q) < (thres * q_rms)
                pix_Q[mask] = 0
                pix_I = data_all[tuple(slice_list_I)]
                Q_I = pix_Q / pix_I
                data = remove_pixels_away_from_sun(Q_I, csys, 60)
            elif Stokes == "U/I":
                if stokes_axis is None:
                    raise RuntimeError("The image does not have a Stokes axis.")
                elif single_stokes_flag:
                    raise RuntimeError(
                        "The image is single stokes, but the Stokes parameter is not 'I'."
                    )
                u_rms = estimate_rms_near_Sun(fits_file, "U", rms_box)
                slice_list_U = [slice(None)] * ndim
                slice_list_I = [slice(None)] * ndim
                slice_list_U[stokes_axis] = 2
                slice_list_I[stokes_axis] = 0
                slice_list_U[freq_axis] = 0
                slice_list_I[freq_axis] = 0
                pix_U = data_all[tuple(slice_list_U)]
                mask = np.abs(pix_U) < (thres * u_rms)
                pix_U[mask] = 0
                pix_I = data_all[tuple(slice_list_I)]
                U_I = pix_U / pix_I
                data = remove_pixels_away_from_sun(U_I, csys, 60)
            elif Stokes == "U/V":
                if stokes_axis is None:
                    raise RuntimeError("The image does not have a Stokes axis.")
                elif single_stokes_flag:
                    raise RuntimeError(
                        "The image is single stokes, but the Stokes parameter is not 'I'."
                    )
                u_rms = estimate_rms_near_Sun(fits_file, "U", rms_box)
                slice_list_U = [slice(None)] * ndim
                slice_list_V = [slice(None)] * ndim
                slice_list_U[stokes_axis] = 2
                slice_list_V[stokes_axis] = 3
                slice_list_U[freq_axis] = 0
                slice_list_V[freq_axis] = 0
                pix_U = data_all[tuple(slice_list_U)]
                pix_V = data_all[tuple(slice_list_V)]
                mask = np.abs(pix_U) < (thres * u_rms)
                pix_U[mask] = 0
                U_V = pix_U / pix_V
                data = remove_pixels_away_from_sun(U_V, csys, 60)
            elif Stokes == "PANG":
                if stokes_axis is None:
                    raise RuntimeError("The image does not have a Stokes axis.")
                elif single_stokes_flag:
                    raise RuntimeError(
                        "The image is single stokes, but the Stokes parameter is not 'I'."
                    )
                slice_list_Q = [slice(None)] * ndim
                slice_list_U = [slice(None)] * ndim
                slice_list_Q[stokes_axis] = 1
                slice_list_U[stokes_axis] = 2
                slice_list_Q[freq_axis] = 0
                slice_list_U[freq_axis] = 0
                pix_Q = data_all[tuple(slice_list_Q)]
                pix_U = data_all[tuple(slice_list_U)]
                PANG = 0.5 * np.arctan2(pix_U, pix_Q) * 180 / np.pi
                data = PANG
            else:
                slice_list_I = [slice(None)] * ndim
                slice_list_I[stokes_axis] = 0
                slice_list_I[freq_axis] = 0
                data = data_all[tuple(slice_list_I)]

        except Exception as e:
            print(f"Error: {str(e)}")
            return None, None, None

        # Get frequency from the appropriate header keyword based on the original FITS axis
        if freq_axis is not None:
            frequency = header[f"CRVAL{original_freq_axis}"] * u.Hz
            print(f"Using frequency from CRVAL{original_freq_axis}: {frequency}")
        else:
            # Try to find frequency from other header keywords if freq_axis is not identified
            if "FREQ" in header:
                frequency = header["FREQ"] * u.Hz
                print(f"Using frequency from FREQ keyword: {frequency}")
            else:
                raise RuntimeError("Could not determine frequency from header")
        obstime = Time(header["date-obs"])
        if lat is not None and long is not None and height is not None:
            print(
                f"DEBUG: Using observer position from arguments LAT={lat}, LONG={long}, HEIGHT={height}"
            )
            POS = EarthLocation.from_geodetic(lon=long, lat=lat, height=height)
        else:
            try:
                print(f"No observer position provided... reading from header")
                x = header["OBSGEO-X"]
                y = header["OBSGEO-Y"]
                z = header["OBSGEO-Z"]
                if abs(x) > 1e6 and abs(y) > 1e6 and abs(z) > 1e6:
                    POS = EarthLocation.from_geocentric(x, y, z, unit=u.m)
                else:
                    print(f"Invalid telescope position in header")
                    ia_tool = IA()
                    ia_tool.open(fits_file)
                    metadata_dict = ia_tool.summary(list=False, verbose=True)
                    lat, long, height, observatory = extract_telescope_position(
                        metadata_dict
                    )
                    ia_tool.close()
                    POS = EarthLocation.from_geodetic(lon=long, lat=lat, height=height)
            except Exception as e:
                print(f"Error: {str(e)}")
                POS = None
            print(f"Observer position: {POS}")

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
        P_angle = P(obstime)
        print(f"Rotating by {P_angle}")

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
        new_header["FREQ"] = (frequency.to(u.Hz).value, "Frequency in Hz")
        new_header["FREQUNIT"] = ("Hz", "Frequency unit")

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

        # Add information about the Stokes parameter
        new_header["STOKES"] = (Stokes, "Stokes parameter")

        # Add beam information if available
        if psf:
            new_header["BMAJ"] = header["BMAJ"]
            new_header["BMIN"] = header["BMIN"]
            new_header["BPA"] = header["BPA"]

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

        map = sunpy.map.Map(data, new_header)
        try:
            map_rotated = map.rotate(P_angle)
        except Exception as e:
            print(f"Error rotating map: {e}. Trying another method.")
            try:
                P_angle_deg = P_angle.to(u.deg).value

                def deg_to_dms_str(deg):
                    # Determine the sign and work with the absolute value
                    sign = "-" if deg < 0 else ""
                    abs_deg = abs(deg)

                    # Extract degrees, minutes, and seconds
                    degrees = int(abs_deg)
                    minutes_full = (abs_deg - degrees) * 60
                    minutes = int(minutes_full)
                    seconds = (minutes_full - minutes) * 60

                    # Format string: you can adjust the precision of seconds as needed
                    print(f"Rotating by {sign}{degrees}d{minutes}m{seconds:.1f}s")
                    return f"{sign}{degrees}d{minutes}m{seconds:.1f}s"

                map_rotated = map.rotate(deg_to_dms_str(P_angle_deg))
            except Exception as e:
                print(f"Error rotating map: {e}")
                map_rotated = None

        return map_rotated, csys, psf
    elif ndim == 2:
        data = hdu[0].data
        try:
            freq = header["CRVAL3"] * u.Hz
        except Exception as e:
            print(f"Error getting frequency: {e}")
            freq = None
        try:
            obstime = Time(hdu[0].header["DATE-OBS"])
        except Exception as e:
            print(f"Error getting observation time: {e}")
            obstime = None
        if lat is not None and long is not None and height is not None:
            POS = EarthLocation.from_geodetic(lon=long, lat=lat, height=height)
        else:
            try:
                print("No observer position provided... reading from header")
                x = header["OBSGEO-X"]
                y = header["OBSGEO-Y"]
                z = header["OBSGEO-Z"]
                if abs(x) > 1e6 and abs(y) > 1e6 and abs(z) > 1e6:
                    POS = EarthLocation.from_geocentric(x, y, z, unit=u.m)
                else:
                    print(f"Invalid telescope position in header")
                    print(f"Checking if it is a known telescope ....")
                    ia_tool = IA()
                    ia_tool.open(fits_file)
                    metadata_dict = ia_tool.summary(list=False, verbose=True)
                    lat, long, height, observatory = extract_telescope_position(
                        metadata_dict
                    )
                    ia_tool.close()
                    POS = EarthLocation.from_geodetic(lon=long, lat=lat, height=height)
            except Exception as e:
                print(f"Error: {str(e)}")
                POS = None
            print(f"Observer position: {POS}")

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
        cdelta_1 = (np.abs(header["CDELT1"]) * u.deg).to(u.arcsec)
        cdelta_2 = (np.abs(header["CDELT2"]) * u.deg).to(u.arcsec)
        P_angle = P(obstime)
        print(f"Rotating by {P_angle}")
        new_header = sunpy.map.make_fitswcs_header(
            data,
            reference_coord,
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
            new_header["FREQ"] = (freq.to(u.Hz).value, "Frequency in Hz")
            new_header["FREQUNIT"] = ("Hz", "Frequency unit")
        except Exception as e:
            print(f"Error adding frequency information: {e}")
        new_header["WCSNAME"] = "Helioprojective"
        new_header["CTYPE1"] = "HPLN-TAN"
        new_header["CTYPE2"] = "HPLT-TAN"
        new_header["CUNIT1"] = "arcsec"
        new_header["CUNIT2"] = "arcsec"
        new_header["STOKES"] = (Stokes, "Stokes parameter")
        if psf:
            new_header["BMAJ"] = header["BMAJ"]
            new_header["BMIN"] = header["BMIN"]
            new_header["BPA"] = header["BPA"]
        map = sunpy.map.Map(data, new_header)
        try:
            map_rotated = map.rotate(P_angle)
        except Exception as e:
            print(f"Error rotating map: {e}. Trying another method.")
            try:
                P_angle_deg = P_angle.to(u.deg).value

                def deg_to_dms_str(deg):
                    # Determine the sign and work with the absolute value
                    sign = "-" if deg < 0 else ""
                    abs_deg = abs(deg)

                map_rotated = map.rotate(deg_to_dms_str(P_angle_deg))
            except Exception as e:
                print(f"Error rotating map: {e}")
                map_rotated = None

        return map_rotated, csys, psf
    else:
        return None, None, None


def convert_casaimage_to_fits(
    imagename=None, fitsname="temp.fits", dropdeg=False, overwrite=True
):
    """
    Convert a CASA image to a FITS file.
    """
    if imagename is None:
        raise ValueError("imagename is required")
    from casatasks import exportfits

    exportfits(imagename=imagename, fitsimage=fitsname, overwrite=True, dropdeg=dropdeg)
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
        print(f"Map saved to {output_file} with proper helioprojective coordinates")

        return map
    else:
        print("Failed to convert to helioprojective coordinates")
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
        print("Cannot save helioprojective map without sunpy")
        return False

    if map_obj is None:
        print("No map to save")
        return False

    try:
        # Save the map as a FITS file
        map_obj.save(output_file, overwrite=True)
        print(f"Helioprojective map saved to {output_file}")
        return True
    except Exception as e:
        print(f"Error saving helioprojective map: {str(e)}")
        return False


def convert_and_save_hpc(
    input_fits_file,
    output_fits_file,
    Stokes="I",
    thres=10,
    lat="-26:42:11.95",
    long="116:40:14.93",
    height=377.8,
    observatory="MWA",
    overwrite=True,
):
    """
    Convert a FITS file to helioprojective coordinates and save it as a new FITS file.

    Parameters
    ----------
    input_fits_file : str
        Path to the input FITS file.
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

    Returns
    -------
    bool
        True if the file was saved successfully, False otherwise.
    """
    if not sunpy_imported:
        print("Cannot convert to helioprojective coordinates without sunpy")
        return False

    try:
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

        if map_obj is None:
            print("Failed to convert to helioprojective coordinates")
            return False

        # Save the map as a FITS file
        map_obj.save(output_fits_file, overwrite=overwrite)
        print(f"Helioprojective map saved to {output_fits_file}")

        # Return the map object for further use if needed
        return True
    except Exception as e:
        print(f"Error converting and saving to helioprojective coordinates: {str(e)}")
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
            print(f"Error {str(e)}. Falling back to manual calculation.")
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
        print(f"Error converting ITRF to geodetic: {str(e)}")
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
            print(f"Error {str(e)}. Falling back to manual calculation.")
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
        print(f"Error converting geodetic to ITRF: {str(e)}")
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
        print(f"Error converting DMS to decimal: {str(e)}")
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
            print("Telescope position not found in metadata")
            return None, None, None, observatory

        # Extract the ITRF coordinates
        # Remove brackets and 'm' units, then split by comma
        telescope_position = telescope_position.replace("[", "").replace("]", "")
        coords = telescope_position.split(",")

        if len(coords) != 3:
            print(f"Invalid telescope position format: {telescope_position}")
            return None, None, None, observatory

        # Parse the coordinates
        x = float(coords[0].replace("m", "").strip())
        y = float(coords[1].replace("m", "").strip())
        z = float(coords[2].replace("m", "").strip())
        if abs(x) < 1e6 and abs(y) < 1e6 and abs(z) < 1e6:
            print(f"Invalid telescope position format: {telescope_position}")
            print(f"Checking if it is a known telescope ....")
            if observatory.upper() == "LOFAR":
                print(f"Observatory matched with LOFAR")
                lat = "52:55:53.90"
                lon = "06:51:56.95"
                height = 50.16
            elif observatory.upper() == "MWA":
                print(f"Observatory matched with MWA")
                lat = "-26:42:12.09"
                lon = "116:40:14.84"
                height = 375.75
            elif observatory.upper() == "MEERKAT":
                print(f"Observatory matched with MEERKAT")
                lat = "-30:42:47.36"
                lon = "21:26:38.09"
                height = 1050.82
            elif observatory.upper() == "GMRT" or observatory.upper() == "UGMRT":
                print(f"Observatory matched with uGMRT")
                lat = "19:05:26.21"
                lon = "74:02:59.90"
                height = 639.68
            else:
                print(f"Observatory not matched with database of known telescopes")
                return None, None, None, observatory
        else:
            # Convert ITRF coordinates to latitude, longitude, and height
            lat, lon, height = itrf_to_geodetic(x, y, z)

        return lat, lon, height, observatory

    except Exception as e:
        print(f"Error extracting telescope position: {str(e)}")
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
    print("\nTest 1: Original metadata")
    lat, lon, height, observatory = extract_telescope_position(metadata)

    # Print the results
    print("\nExtracted Telescope Position:")
    print(f"Latitude: {lat}")
    print(f"Longitude: {lon}")
    print(f"Height: {height} meters")
    print(f"Observatory: {observatory}")

    # Print the values to use in convert_to_hpc
    print("\nUse these values in convert_to_hpc as:")
    print(f'lat="{lat}", long="{lon}", height={height}, observatory="{observatory}"')

    # Test with metadata that has observatory directly in the dictionary
    print("\nTest 2: Metadata with observatory directly in the dictionary")
    metadata2 = metadata.copy()
    metadata2["observatory"] = "MWA"

    lat2, lon2, height2, observatory2 = extract_telescope_position(metadata2)

    # Print the results
    print("\nExtracted Telescope Position:")
    print(f"Latitude: {lat2}")
    print(f"Longitude: {lon2}")
    print(f"Height: {height2} meters")
    print(f"Observatory: {observatory2}")

    # Print the values to use in convert_to_hpc
    print("\nUse these values in convert_to_hpc as:")
    print(
        f'lat="{lat2}", long="{lon2}", height={height2}, observatory="{observatory2}"'
    )

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
        default="-26:42:11.95",
        help="Observer latitude (default: -26:42:11.95, MWA)",
    )
    convert_parser.add_argument(
        "--long",
        default="116:40:14.93",
        help="Observer longitude (default: 116:40:14.93, MWA)",
    )
    convert_parser.add_argument(
        "--height",
        type=float,
        default=377.8,
        help="Observer height in meters (default: 377.8, MWA)",
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
                print(f"ITRF Coordinates:")
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

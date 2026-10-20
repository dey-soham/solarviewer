"""
Noise (RMS) estimation and masking pixels far from the Sun.
"""

import numpy as np

from .dependencies import IA
from .fits_io import readable_image_path


def estimate_rms_near_Sun(imagename, stokes="I", box=(0, 200, 0, 130)):
    stokes_map = {"I": 0, "Q": 1, "U": 2, "V": 3}
    ia_tool = IA()
    ia_tool.open(readable_image_path(imagename))
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
    # Use astropy's WCS for coordinate conversion
    from astropy.wcs import WCS

    w = WCS(naxis=2)
    # Check if coordinate system uses SOLAR-X (arcsec) vs RA/Dec (radians)
    axis_names = list(csys.names())
    if "SOLAR-X" in axis_names or "SOLAR-Y" in axis_names:
        w.wcs.cdelt = csys.increment()["numeric"][0:2] / 3600.0  # arcsec -> degrees
    else:
        rad_to_deg = 180.0 / np.pi
        w.wcs.cdelt = (
            csys.increment()["numeric"][0:2] * rad_to_deg
        )  # radians -> degrees
    radius_deg = radius_arcmin / 60.0
    delta_deg = abs(w.wcs.cdelt[0])
    pixel_radius = radius_deg / delta_deg

    cx = pix.shape[0] / 2
    cy = pix.shape[1] / 2
    y, x = np.ogrid[: pix.shape[1], : pix.shape[0]]
    mask = (x - cx) ** 2 + (y - cy) ** 2 > pixel_radius**2
    pix[mask] = 0
    return pix

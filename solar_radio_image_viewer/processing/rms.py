"""
Noise (RMS) estimation and masking pixels far from the Sun.

Planes are indexed [x, y]; an RMS box is (x1, x2, y1, y2) in those pixels.
"""

import numpy as np

from .dependencies import IA
from .fits_io import readable_image_path
from .image_meta import casa_obs_time, sky_axes
from .stokes import casa_stokes_labels, plane_index, rms_in_box


def estimate_rms_near_Sun(imagename, stokes="I", box=(0, 200, 0, 130)):
    """RMS of the ``stokes`` plane (first channel) of an image inside ``box``.

    Reads only that plane, found by its Stokes label.
    """
    ia_tool = IA()
    ia_tool.open(readable_image_path(imagename))
    try:
        dimension_names = [str(name) for name in ia_tool.summary()["axisnames"]]
        shape = [int(n) for n in ia_tool.shape()]
        x_idx, y_idx = sky_axes(dimension_names)

        blc = [0] * len(shape)
        trc = [n - 1 if axis in (x_idx, y_idx) else 0 for axis, n in enumerate(shape)]
        if "Stokes" in dimension_names:
            stokes_idx = dimension_names.index("Stokes")
            labels = casa_stokes_labels(ia_tool.coordsys())
            blc[stokes_idx] = trc[stokes_idx] = plane_index(labels, stokes)
        else:
            plane_index(None, stokes)  # only Stokes I without a Stokes axis
        chunk = ia_tool.getchunk(blc=blc, trc=trc)
    finally:
        ia_tool.close()

    plane = chunk[
        tuple(slice(None) if axis in (x_idx, y_idx) else 0 for axis in range(len(shape)))
    ]
    return rms_in_box(plane, box)


def sun_pixel(csys, shape):
    """Pixel (x, y) of the Sun's centre in an image with coordinate system
    ``csys`` and [x, y] ``shape``, or None if it is not inside the image (or
    cannot be worked out)."""
    try:
        names = [str(name) for name in csys.names()]
        units = list(csys.units())
        world = list(csys.referencevalue()["numeric"])
        if "SOLAR-X" in names:
            world[0] = world[1] = 0.0  # helioprojective: the Sun is the origin
        else:
            import astropy.units as u
            from astropy.coordinates import get_sun

            obs_time = casa_obs_time(csys.epoch())
            if obs_time is None:
                return None
            sun = get_sun(obs_time)
            world[0] = sun.ra.to_value(u.Unit(units[0]))
            world[1] = sun.dec.to_value(u.Unit(units[1]))
        x, y = csys.topixel(world)["numeric"][:2]
    except Exception:
        return None
    if 0 <= x < shape[0] and 0 <= y < shape[1]:
        return float(x), float(y)
    return None


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

    # Around the Sun; around the image centre when the Sun is not in the image
    centre = sun_pixel(csys, pix.shape)
    cx, cy = centre if centre is not None else (pix.shape[0] / 2, pix.shape[1] / 2)
    x, y = np.ogrid[: pix.shape[0], : pix.shape[1]]
    mask = (x - cx) ** 2 + (y - cy) ** 2 > pixel_radius**2
    pix[mask] = 0
    return pix

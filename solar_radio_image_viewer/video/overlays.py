"""
Solar disk, beam and coordinate grid on video frames, drawn like the main
window draws them (styles from processing.plot_style), but worked out from
each frame's own FITS header, so they follow the frames.

Each function calls ``report(message)`` when it cannot draw, so the Create
Video window can say why something is missing.
"""

import logging

import numpy as np
from astropy.wcs import WCS
from matplotlib.patches import Circle, Ellipse

from .utils import coordinate_type

logger = logging.getLogger(__name__)

_DATE_KEYS = ("DATE-OBS", "DATE_OBS", "DATE", "STARTOBS", "T_OBS")


def _header_wcs(header):
    try:
        return WCS(header, naxis=2)
    except Exception:
        return None


def _pixel_scale_deg(wcs):
    """Size of a pixel along x in degrees (astropy WCS works in degrees)."""
    from astropy.wcs.utils import proj_plane_pixel_scales

    return float(abs(proj_plane_pixel_scales(wcs)[0]))


def solar_disk_pixels(header):
    """(x, y, radius) of the solar disk in pixels of the image with
    ``header``; raises ValueError saying why it can't be found.

    As in the main window: the centre is helioprojective (0, 0), or the
    Sun's RA/Dec at the observation time, and the radius the Sun's apparent
    radius from Earth at that time.
    """
    from astropy.time import Time
    from sunpy.coordinates.sun import angular_radius

    date = next((header.get(key) for key in _DATE_KEYS if header.get(key)), None)
    if not date:
        raise ValueError("no observation date in the header")
    wcs = _header_wcs(header)
    if wcs is None:
        raise ValueError("no coordinates in the header")

    when = Time(str(date))
    kind = coordinate_type(header)
    if kind == "hpc":
        x, y = wcs.wcs_world2pix(0.0, 0.0, 0)
    elif kind == "radec":
        from astropy.coordinates import get_sun

        sun = get_sun(when)
        x, y = wcs.wcs_world2pix(sun.ra.deg, sun.dec.deg, 0)
    else:
        raise ValueError("coordinates neither helioprojective nor RA/Dec")
    radius = angular_radius(when).to_value("deg") / _pixel_scale_deg(wcs)
    if not np.isfinite([x, y, radius]).all():
        raise ValueError("the Sun is not in this image's field of view")
    return float(x), float(y), float(radius)


def draw_solar_disk(ax, header, style, region_origin=(0, 0), report=None):
    """Draw the solar disk (and centre marker) on frame axes ``ax``."""
    try:
        x, y, radius = solar_disk_pixels(header)
    except Exception as e:
        if report:
            report(f"Solar disk not drawn: {e}")
        return
    x -= region_origin[0]
    y -= region_origin[1]
    ax.add_patch(
        Circle(
            (x, y),
            radius,
            fill=False,
            edgecolor=style["color"],
            linestyle=style["linestyle"],
            linewidth=style["linewidth"],
            alpha=style["alpha"],
        )
    )
    if style.get("show_center", True):
        cross = radius / 20
        for xs, ys in (([x - cross, x + cross], [y, y]), ([x, x], [y - cross, y + cross])):
            ax.plot(xs, ys, color=style["color"], linewidth=1.5, alpha=style["alpha"])


def beam_pixels(header):
    """(major, minor, angle) of the restoring beam in pixels and degrees
    (Ellipse angle); raises ValueError saying why there is none."""
    try:
        major, minor = float(header["BMAJ"]), float(header["BMIN"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("the image has no beam size (BMAJ/BMIN) in its header")
    position_angle = float(header.get("BPA", 0.0) or 0.0)
    wcs = _header_wcs(header)
    if wcs is None:
        raise ValueError("no coordinates to size the beam")
    scale = _pixel_scale_deg(wcs)
    return major / scale, minor / scale, position_angle - 90


def draw_beam(ax, header, style, report=None):
    """Draw the beam in the lower left corner of the view, as the main
    window does."""
    try:
        major, minor, angle = beam_pixels(header)
    except Exception as e:
        if report:
            report(f"Beam not drawn: {e}")
        return
    (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
    ax.add_patch(
        Ellipse(
            (x0 + (x1 - x0) * 0.05 + major / 2, y0 + (y1 - y0) * 0.05 + minor / 2),
            width=major,
            height=minor,
            angle=angle,
            fill=True,
            edgecolor=style.get("edgecolor", "black"),
            linewidth=style.get("linewidth", 1.5),
            facecolor=style.get("facecolor", "white"),
            alpha=style.get("alpha", 0.4),
        )
    )


def draw_grid(ax, style, report=None):
    """Coordinate grid (needs WCS axes, as in the main window)."""
    if not hasattr(ax, "coords"):
        if report:
            report("Grid not drawn: it needs WCS coordinates (Display tab)")
        return
    ax.coords.grid(
        True,
        color=style.get("color", "white"),
        alpha=style.get("alpha", 0.5),
        linestyle=style.get("linestyle", "--"),
    )

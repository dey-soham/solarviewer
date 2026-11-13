"""
The view (zoom and pan) of one image, kept for the image that replaces it
in its pane: the next file of a series, or the same image converted
(Jy/beam | K, RA/Dec | Solar XY).

``KeptView`` is what the pane showed, ``limits_for`` the axes limits that
show the same in the new image: the same pixels of an image of the same
size, the same part of the sky of one in the other sky frame.
``marks_map`` is where the pixels of the old image lie in the new one,
for what is marked on it (regions, annotations) to stay on the sky, and
``moved_boxes`` the marks moved by it. ``keeps_view`` wraps a method that
replaces the image with a conversion of it.
"""

import logging
import math
from functools import wraps
from typing import NamedTuple, Optional

import numpy as np

logger = logging.getLogger(__name__)

# Who saw the Sun, in the header of a helioprojective image
OBSERVER_KEYS = ("HGLN_OBS", "HGLT_OBS", "DSUN_OBS")
# marks_map's answer for an image that is only passed through
PASSING = "passing"


class KeptView(NamedTuple):
    """The view of an image, for the one that replaces it."""

    shape: tuple  # of the image, [x, y]
    xlim: tuple
    ylim: tuple
    # Of an image converted to another sky frame, during a step to the
    # next file (which is loaded as it is, then converted as this was)
    turned: bool
    kind: Optional[str]  # its sky frame (wcs_kind)
    wcs: object  # its 2D WCS, or None
    header: dict  # its FITS header, {} without one


def wcs_kind(wcs):
    """The sky frame of a 2D WCS: "radec", "solar", or None."""
    if wcs is None:
        return None
    try:
        ctype = str(wcs.wcs.ctype[0]).upper()
    except Exception:
        return None
    if ctype.startswith("RA"):
        return "radec"
    if ctype.startswith("HPLN") or ctype.startswith("SOLAR"):
        return "solar"
    return None


def unit_scales(wcs):
    """Degrees per world unit of the first two axes."""
    from astropy import units as u

    scales = []
    for cunit in list(wcs.wcs.cunit)[:2]:
        try:
            scales.append(float(u.Unit(str(cunit) or "deg").to(u.deg)))
        except Exception:
            scales.append(1.0)
    return scales


def _pixel_scales(wcs):
    """Degrees per pixel along x and y."""
    from astropy.wcs.utils import proj_plane_pixel_scales

    scales = proj_plane_pixel_scales(wcs)
    sx, sy = unit_scales(wcs)
    return abs(float(scales[0])) * sx, abs(float(scales[1])) * sy


def other_frame(lon, lat, kind, header):
    """The sky positions (lon, lat: degrees, numbers or arrays) of the sky
    frame ``kind`` in the other one: RA/Dec to helioprojective, or back
    (as arrays of the same shape). ``header`` is the
    helioprojective image's: its time, and its observer if it names one
    (else the Earth's centre), as the conversion of the image itself takes
    them (coordinates/helioprojective.py)."""
    import astropy.units as u
    from astropy.coordinates import GCRS, SkyCoord
    from astropy.time import Time
    from sunpy.coordinates import HeliographicStonyhurst, Helioprojective, sun

    obstime = Time(header["DATE-OBS"])
    if all(key in header for key in OBSERVER_KEYS):
        observer = SkyCoord(
            header["HGLN_OBS"] * u.deg,
            header["HGLT_OBS"] * u.deg,
            header["DSUN_OBS"] * u.m,
            frame=HeliographicStonyhurst(obstime=obstime),
        )
        place = observer.transform_to(GCRS(obstime=obstime)).cartesian.without_differentials()
        radec = GCRS(obstime=obstime, obsgeoloc=place)
        distance = header["DSUN_OBS"] * u.m
    else:
        observer = "earth"
        radec = GCRS(obstime=obstime)
        distance = sun.earth_distance(obstime)
    solar = Helioprojective(obstime=obstime, observer=observer)
    # At the Sun's distance: both frames look from the observer, so the
    # direction is the same at any, and a position needs one to be moved
    if kind == "radec":
        point = SkyCoord(lon * u.deg, lat * u.deg, distance=distance, frame=radec).transform_to(solar)
        return np.asarray(point.Tx.deg, dtype=float), np.asarray(point.Ty.deg, dtype=float)
    point = SkyCoord(lon * u.deg, lat * u.deg, distance=distance, frame=solar).transform_to(radec)
    return np.asarray(point.ra.deg, dtype=float), np.asarray(point.dec.deg, dtype=float)


def _whole(view):
    """Whether ``view`` is of the whole image (as it opens, or reset)."""
    full = ((-0.5, view.shape[0] - 0.5), (-0.5, view.shape[1] - 0.5))
    return all(
        abs(shown - whole) <= 0.01
        for limits, edges in zip((view.xlim, view.ylim), full)
        for shown, whole in zip(sorted(limits), edges)
    )


def _sky_map(view, wcs, header):
    """Where pixels of the image ``view`` was of are in the image of
    ``wcs``, which is in the other sky frame: a function of arrays of
    pixel positions (xs, ys), giving them as arrays (NaN off the sky)."""
    solar_header = header if wcs_kind(wcs) == "solar" else view.header
    old_units, new_units = unit_scales(view.wcs), unit_scales(wcs)

    def move(xs, ys):
        xs, ys = np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)
        lon, lat = view.wcs.pixel_to_world_values(xs, ys)
        lon, lat = other_frame(
            np.asarray(lon, dtype=float) * old_units[0],
            np.asarray(lat, dtype=float) * old_units[1],
            view.kind,
            solar_header,
        )
        x, y = wcs.world_to_pixel_values(lon / new_units[0], lat / new_units[1])
        return np.asarray(x, dtype=float), np.asarray(y, dtype=float)

    return move


def _nearby_size(view, shape):
    """Whether an image of ``shape`` is within a pixel or two in a
    hundred of the size of the one ``view`` was of."""
    return all(abs(new - old) <= 0.02 * old for new, old in zip(shape, view.shape))


def _same_sky(view, shape, wcs, header):
    """The limits that show, in the image of ``wcs`` (in the other sky
    frame than ``view``'s), the part of the sky ``view`` shows: about the
    same position, as wide and as high on the sky. None if that cannot be
    found, or none of the image is there."""
    try:
        centre = ((view.xlim[0] + view.xlim[1]) / 2, (view.ylim[0] + view.ylim[1]) / 2)
        x, y = _sky_map(view, wcs, header)([centre[0]], [centre[1]])
        x, y = float(x[0]), float(y[0])
        old, new = _pixel_scales(view.wcs), _pixel_scales(wcs)
        half_w = (view.xlim[1] - view.xlim[0]) / 2 * old[0] / new[0]
        half_h = (view.ylim[1] - view.ylim[0]) / 2 * old[1] / new[1]
    except Exception:
        logger.debug("Could not keep the view in the other sky frame", exc_info=True)
        return None
    if not all(math.isfinite(value) for value in (x, y, half_w, half_h)) or not (half_w and half_h):
        return None
    if (
        x + abs(half_w) < -0.5
        or x - abs(half_w) > shape[0] - 0.5
        or y + abs(half_h) < -0.5
        or y - abs(half_h) > shape[1] - 0.5
    ):
        return None
    return (x - half_w, x + half_w), (y - half_h, y + half_h)


def limits_for(view, shape, wcs=None, header=None):
    """The axes limits (xlim, ylim) that show, in an image of ``shape``
    (its 2D ``wcs`` and FITS ``header``), what ``view`` showed of the image
    before it, or None for the whole image:

    - an image of the same size in the same sky frame: the same limits;
    - a ``turned`` view (of an image reprojected to another sky frame,
      whose size changes by a pixel or two from one file to the next as
      the Sun's axis turns): the limits moved with the image's middle;
    - an image in the other sky frame (the same one, converted): the same
      part of the sky, unless the view was of the whole image.
    """
    shape, old_shape = tuple(shape), tuple(view.shape)
    kind = wcs_kind(wcs)
    if view.kind and kind and kind != view.kind:
        # (the file a step through converted images passes, as it is and
        # unseen, on the way to its own conversion: nobody's view)
        if view.turned or view.wcs is None or _whole(view):
            return None
        return _same_sky(view, shape, wcs, header or {})
    if shape == old_shape:
        return view.xlim, view.ylim
    if view.turned and _nearby_size(view, shape):
        dx, dy = (shape[0] - old_shape[0]) / 2, (shape[1] - old_shape[1]) / 2
        return (
            (view.xlim[0] + dx, view.xlim[1] + dx),
            (view.ylim[0] + dy, view.ylim[1] + dy),
        )
    return None


def marks_map(view, shape, wcs=None, header=None):
    """Where the pixels of the image ``view`` was of lie in the image of
    ``shape`` (its 2D ``wcs`` and FITS ``header``) that replaces it, for
    what is marked on it (regions, annotations) to stay where it is on
    the sky: a function of arrays of pixel positions (xs, ys), giving
    them as arrays. None where they lie as they did, and PASSING for the
    file a step through converted images loads as it is, unseen, before
    converting it: the marks wait for the converted image.

    As ``limits_for`` has the view: the same pixels in the same sky
    frame (moved with the image's middle for a ``turned`` view), the same
    sky positions in the other one.
    """
    shape, old_shape = tuple(shape), tuple(view.shape)
    kind = wcs_kind(wcs)
    if view.kind and kind and kind != view.kind:
        if view.turned:
            return PASSING
        return _sky_map(view, wcs, header or {}) if view.wcs is not None else None
    if shape != old_shape and view.turned and _nearby_size(view, shape):
        dx, dy = (shape[0] - old_shape[0]) / 2, (shape[1] - old_shape[1]) / 2
        return lambda xs, ys: (np.asarray(xs, dtype=float) + dx, np.asarray(ys, dtype=float) + dy)
    return None


def snap(value, within=1e-3):
    """``value`` as the integer it is within ``within`` of, else itself:
    a trip by way of the sky lands a hair off a pixel (and a whole
    degree), and statistics truncate."""
    nearest = round(value)
    return float(nearest) if abs(value - nearest) < within else value


def moved_boxes(move, boxes):
    """``boxes`` (each a centre x, y, a width and a height in pixels, and
    an angle in degrees anticlockwise) where ``move`` (a ``marks_map``)
    puts them: a list of the same, each turned and sized as the pixels
    under it are; None for one that cannot be placed."""
    if not boxes:
        return []
    xs, ys = [], []
    for cx, cy, width, height, angle in boxes:
        t = math.radians(angle)
        a, b = width / 2, height / 2
        # Its centre and the ends of its two half-axes
        xs += [cx, cx + a * math.cos(t), cx - b * math.sin(t)]
        ys += [cy, cy + a * math.sin(t), cy + b * math.cos(t)]
    xs, ys = move(xs, ys)
    moved = []
    for index, (_cx, _cy, width, height, angle) in enumerate(boxes):
        (cx, ax, bx), (cy, ay, by) = xs[3 * index : 3 * index + 3], ys[3 * index : 3 * index + 3]
        if not all(math.isfinite(value) for value in (cx, ax, bx, cy, ay, by)):
            moved.append(None)
            continue
        if width:
            angle = math.degrees(math.atan2(ay - cy, ax - cx))
        elif height:
            angle = math.degrees(math.atan2(by - cy, bx - cx)) - 90
        moved.append((
            float(cx),
            float(cy),
            2 * math.hypot(ax - cx, ay - cy),
            2 * math.hypot(bx - cx, by - cy),
            snap(angle),
        ))
    return moved


def keeps_view(method):
    """The image ``method`` (of a pane, taking no arguments) leaves shown,
    a conversion of the one it started with, keeps its view: see
    ``limits_for``. Inside a step to another file, the view the step keeps
    stays the one kept."""

    # (no arguments of its own either: a button's clicked signal then
    # leaves out its "checked", as it does for the method itself)
    @wraps(method)
    def wrapper(self):
        if getattr(self, "_step_view", None) is not None:
            return method(self)
        self._step_view = self._view_shown()
        try:
            return method(self)
        finally:
            self._step_view = None
            self._marks_carried = None

    return wrapper

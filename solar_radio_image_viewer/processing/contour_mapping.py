"""
Mapping contour lines from one image's pixels onto another's.

Contours are traced on the contour image's own pixel grid and their
vertices are moved through both images' WCS, rather than resampling the
contour image onto the other grid. Used by the main viewer and by video
frames, so this module must not import Qt.
"""

import contextlib

import numpy as np
from matplotlib.transforms import Transform


def contour_artists(cs):
    """The artists holding a ContourSet's paths (itself on Matplotlib >= 3.8)."""
    from matplotlib.collections import Collection

    return [cs] if isinstance(cs, Collection) else list(cs.collections)


def same_pixel_grid(wcs_a, wcs_b):
    """True if two display WCSs describe the same pixel grid."""
    return (
        list(wcs_a.wcs.ctype) == list(wcs_b.wcs.ctype)
        and np.allclose(wcs_a.wcs.crpix, wcs_b.wcs.crpix, rtol=0, atol=1e-3)
        and np.allclose(wcs_a.wcs.crval, wcs_b.wcs.crval, rtol=0, atol=1e-6)
        and np.allclose(wcs_a.wcs.cdelt, wcs_b.wcs.cdelt, rtol=1e-3, atol=0)
    )


def transformable_wcs(display_wcs, header):
    """Copy of a display WCS that astropy can transform between frames.

    SOLAR-X/Y (linear, in arcsec) becomes HPLN/HPLT-TAN in degrees, and the
    observation time and observer position are taken from ``header`` when
    it has them.
    """
    wcs = display_wcs.deepcopy()
    if "SOLAR" in wcs.wcs.ctype[0].upper():
        wcs.wcs.crval = np.asarray(display_wcs.wcs.crval) / 3600.0
        wcs.wcs.cdelt = np.asarray(display_wcs.wcs.cdelt) / 3600.0
        wcs.wcs.ctype = ["HPLN-TAN", "HPLT-TAN"]
    header = header or {}
    date_obs = header.get("DATE-OBS") or header.get("DATE_OBS")
    if date_obs:
        wcs.wcs.dateobs = str(date_obs)
    if wcs.wcs.ctype[0].startswith("HPLN"):
        for key in ("HGLN_OBS", "HGLT_OBS", "DSUN_OBS", "RSUN_REF"):
            if header.get(key) is not None:
                setattr(wcs.wcs.aux, key.lower(), float(header[key]))
    wcs.wcs.set()
    return wcs


def pixel_mapping(src_wcs, dst_wcs):
    """Function mapping (n, 2) pixel positions of ``src_wcs`` to ``dst_wcs``.

    Between helioprojective frames of different observers, points are taken
    to lie on a sphere through the Sun's centre as seen from the source's
    observer, so off-limb (coronal) emission maps as well.
    """
    from astropy.wcs.utils import pixel_to_pixel, wcs_to_celestial_frame

    observer = None
    try:
        from sunpy.coordinates import Helioprojective

        frame = wcs_to_celestial_frame(src_wcs)
        if isinstance(frame, Helioprojective):
            observer = frame.observer
            dst_frame = wcs_to_celestial_frame(dst_wcs)
            if isinstance(dst_frame, Helioprojective) and dst_frame.observer is not None:
                return _sun_fixed_mapping(src_wcs, dst_wcs, frame, dst_frame)
    except Exception:
        pass

    def mapping(xy):
        xy = np.asarray(xy, dtype=float).reshape(-1, 2)
        if not len(xy):
            return xy
        with _spherical_screen(observer):
            x, y = pixel_to_pixel(src_wcs, dst_wcs, xy[:, 0], xy[:, 1])
        return np.column_stack([x, y])

    return mapping


def _sun_fixed_mapping(src_wcs, dst_wcs, src_frame, dst_frame):
    """Pixel mapping between two helioprojective images in which points
    stay fixed relative to the Sun (Heliographic Stonyhurst: its centre,
    rotation axis and the direction of Earth), not in space.

    Solar images are compared that way: a point fixed in space would be
    seen from a direction that turns with Earth's orbit, about 1° a day, so
    the contours of an image taken months away would be squeezed and moved
    (by cos 70° between May and March). Differential rotation is not
    applied. For images taken at the same time this is the same as
    transforming between the frames directly.
    """
    from astropy.coordinates import SkyCoord
    from sunpy.coordinates import HeliographicStonyhurst

    def mapping(xy):
        xy = np.asarray(xy, dtype=float).reshape(-1, 2)
        if not len(xy):
            return xy
        world = src_wcs.pixel_to_world(xy[:, 0], xy[:, 1])
        with _spherical_screen(src_frame.observer):
            sun = world.transform_to(HeliographicStonyhurst(obstime=src_frame.obstime))
        # The same point on the Sun, at the other image's time
        moved = SkyCoord(
            sun.lon, sun.lat, sun.radius, frame=HeliographicStonyhurst(obstime=dst_frame.obstime)
        )
        x, y = dst_wcs.world_to_pixel(moved.transform_to(dst_frame))
        return np.column_stack([x, y])

    return mapping


@contextlib.contextmanager
def _spherical_screen(observer):
    """Context in which helioprojective points off the Sun lie on a sphere
    through its centre as seen from ``observer`` (nothing if None).

    Points also move with the Sun's centre, not stay fixed in space: the
    Sun drifts by up to about its radius a year around the solar system's
    centre of mass, which would shift contours of images taken far apart.
    """
    if observer is None:
        yield
        return
    from sunpy.coordinates import transform_with_sun_center

    try:
        from sunpy.coordinates import SphericalScreen

        screen = SphericalScreen(observer)
    except ImportError:
        # sunpy < 6; removed from Helioprojective in sunpy 7
        from sunpy.coordinates import Helioprojective

        screen = Helioprojective.assume_spherical_screen(observer)
    with screen, transform_with_sun_center():
        yield


def check_mapping(mapping, shape):
    """Raise if ``mapping`` fails on the corners and centre of an image."""
    nx, ny = shape
    probe = np.array(
        [[0, 0], [nx - 1, 0], [0, ny - 1], [nx - 1, ny - 1], [nx / 2, ny / 2]]
    )
    if not np.isfinite(mapping(probe)).any():
        raise ValueError("no point of the contour image maps onto the base image")


class PixelToPixel(Transform):
    """Matplotlib transform wrapping a pixel-to-pixel mapping function.

    WCSAxes.contour applies it once to all contour vertices."""

    input_dims = output_dims = 2

    def __init__(self, mapping):
        super().__init__()
        self._mapping = mapping

    def transform_non_affine(self, values):
        return self._mapping(values)


def mapping_between(src_wcs, dst_wcs, src_shape):
    """``pixel_mapping`` from ``src_wcs`` to ``dst_wcs``, using the observers'
    positions when the WCS have them and treating the frames as the same
    otherwise. ``src_shape`` is (nx, ny). Raises if no point maps."""
    mapping = pixel_mapping(src_wcs, dst_wcs)
    try:
        check_mapping(mapping, src_shape)
        return mapping
    except Exception:
        mapping = pixel_mapping(_bare(src_wcs), _bare(dst_wcs))
        check_mapping(mapping, src_shape)
        return mapping


def _bare(wcs):
    """Copy of a WCS without observation time and observer."""
    wcs = wcs.deepcopy()
    wcs.wcs.dateobs = ""
    wcs.wcs.mjdobs = np.nan
    for key in ("hgln_obs", "hglt_obs", "dsun_obs", "crln_obs"):
        try:
            setattr(wcs.wcs.aux, key, np.nan)
        except Exception:
            pass
    wcs.wcs.set()
    return wcs


@contextlib.contextmanager
def view_kept(ax):
    """Keep ``ax``'s view, data limits and autoscaling as they are.

    Matplotlib autoscales to each contour set, and contours from another
    image are traced in that image's pixels before being mapped, so drawing
    them would otherwise move the view.
    """
    xlim, ylim = ax.get_xlim(), ax.get_ylim()
    data_lim = ax.dataLim.frozen()
    autoscale = ax.get_autoscalex_on(), ax.get_autoscaley_on()
    try:
        yield
    finally:
        ax.dataLim.set(data_lim)
        ax.set_xlim(xlim)
        ax.set_ylim(ylim)
        ax.set_autoscalex_on(autoscale[0])
        ax.set_autoscaley_on(autoscale[1])


def draw_mapped_contours(ax, data, mapping=None, step=1, offset=(0, 0), **contour_kwargs):
    """Contour ``data`` (rows = y) on ``ax`` without changing the view.

    Contours are traced every ``step`` pixels of ``data``; their vertices
    are passed through ``mapping`` (``data`` pixels to ``ax`` data
    coordinates, None for the same grid) and then shifted by ``-offset``
    (e.g. the corner of a cropped region). Returns the ContourSet.
    """
    rows = np.arange(0, data.shape[0], step)
    cols = np.arange(0, data.shape[1], step)
    with view_kept(ax):
        cs = ax.contour(cols, rows, data[::step, ::step], **contour_kwargs)
    shift = np.asarray(offset, dtype=float)
    for artist in contour_artists(cs):
        for path in artist.get_paths():
            if len(path.vertices):
                vertices = path.vertices
                if mapping is not None:
                    vertices = mapping(vertices)
                path.vertices = vertices - shift
    return cs

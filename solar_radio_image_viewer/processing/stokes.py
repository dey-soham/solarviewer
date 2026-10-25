"""
Stokes parameters: which planes an image has, finding a plane by its label,
and the products shown from them (L, Lfrac, Vfrac, Q/I, U/I, U/V, PANG).

Images hold their polarization planes in any order and any subset: I, Q, U
and V; only I and V (common for solar radio images); or correlations (RR,
LL, XX, ...). A plane is always found by its label, never by its position.
A correlation plane is a product of its own, and an image of correlations
also yields the standard parameters its pairs make (I = (RR + LL) / 2 ...).

Planes are 2D arrays indexed [x, y] (CASA's order, drawn transposed), and an
RMS box is (x1, x2, y1, y2) in the same pixels.
"""

import logging
import numpy as np

from .dependencies import ASTROPY_AVAILABLE, CASA_AVAILABLE
from .fits_io import is_casa_image
from .image_meta import casa_info, fits_header

logger = logging.getLogger(__name__)

# The FITS STOKES axis codes (Greisen & Calabretta 2002, table 7)
FITS_STOKES_CODES = {
    1: "I",
    2: "Q",
    3: "U",
    4: "V",
    -1: "RR",
    -2: "LL",
    -3: "RL",
    -4: "LR",
    -5: "XX",
    -6: "YY",
    -7: "XY",
    -8: "YX",
}

STANDARD_STOKES = ("I", "Q", "U", "V")
CORRELATIONS = ("RR", "LL", "RL", "LR", "XX", "YY", "XY", "YX")

# The planes each product is computed from
PLANES_NEEDED = {
    "I": ("I",),
    "Q": ("Q",),
    "U": ("U",),
    "V": ("V",),
    **{label: (label,) for label in CORRELATIONS},
    "L": ("Q", "U"),
    "Lfrac": ("I", "Q", "U"),
    "Vfrac": ("I", "V"),
    "V/I": ("I", "V"),  # the contour dialog's name for Vfrac
    "Q/I": ("I", "Q"),
    "U/I": ("I", "U"),
    "U/V": ("U", "V"),
    "PANG": ("Q", "U"),
}


# The standard parameters an image of correlations yields, from which pair
# and how (only those made from real planes: U needs the imaginary part of
# the cross-hands, which an image does not hold)
FROM_CORRELATIONS = {
    "I": (
        (("RR", "LL"), lambda a, b: (a + b) / 2),
        (("XX", "YY"), lambda a, b: (a + b) / 2),
    ),
    "V": ((("RR", "LL"), lambda a, b: (a - b) / 2),),
    "Q": ((("XX", "YY"), lambda a, b: (a - b) / 2),),
}


class StokesUnavailable(RuntimeError):
    """The image has no plane with the Stokes label asked for."""


def planes_needed(stokes):
    """Labels of the planes the ``stokes`` product is computed from."""
    try:
        return PLANES_NEEDED[stokes]
    except KeyError:
        raise ValueError(f"Unknown Stokes parameter: {stokes}") from None


def missing_planes(stokes, available):
    """The planes the ``stokes`` product needs that are not among
    ``available`` (as from get_available_stokes)."""
    return [label for label in planes_needed(stokes) if label not in available]


def fallback_product(preferred, available):
    """The product to show of an image lacking the one asked for:
    ``preferred`` if the image has its planes, else the first of
    ``available`` (as from get_available_stokes: I before V before LL)."""
    if not missing_planes(preferred, available):
        return preferred
    return available[0]


def _correlation_pairs(label, labels):
    """The (pair, combine) entries of FROM_CORRELATIONS that make ``label``
    from planes among ``labels``."""
    return [
        (pair, combine)
        for pair, combine in FROM_CORRELATIONS.get(label, ())
        if all(plane in labels for plane in pair)
    ]


def offered_planes(labels):
    """The planes an image whose Stokes planes are ``labels`` offers: the
    standard parameters it has or its correlations make, in I, Q, U, V
    order, then its correlations in its own order. None for ``labels``
    (no Stokes axis) is a Stokes I image."""
    if labels is None:
        return ["I"]
    standard = [
        s for s in STANDARD_STOKES if s in labels or _correlation_pairs(s, labels)
    ]
    return standard + [label for label in labels if label in CORRELATIONS]


def read_planes(labels, stokes, read_plane):
    """{label: plane} of the planes the ``stokes`` product needs, from an
    image whose Stokes planes are ``labels`` (None: no Stokes axis), each
    read with ``read_plane(index)`` (index along the Stokes axis; None
    without one). A standard parameter the image lacks is made from its
    correlations (FROM_CORRELATIONS).

    Raises StokesUnavailable for a plane the image has not and cannot make.
    """

    def plane(label):
        if labels is None or label in labels:
            return read_plane(plane_index(labels, label))
        pairs = _correlation_pairs(label, labels)
        if not pairs:
            plane_index(labels, label)  # raises StokesUnavailable
        pair, combine = pairs[0]
        return combine(*(read_plane(labels.index(p)) for p in pair))

    return {label: plane(label) for label in planes_needed(stokes)}


def fits_stokes_labels(header):
    """Labels of the planes of a FITS image's STOKES axis, in order, or None
    if the image has no STOKES axis."""
    for axis in range(1, int(header.get("NAXIS", 0)) + 1):
        if str(header.get(f"CTYPE{axis}", "")).strip().upper() != "STOKES":
            continue
        crval = float(header.get(f"CRVAL{axis}", 1.0))
        cdelt = float(header.get(f"CDELT{axis}", 1.0) or 1.0)
        crpix = float(header.get(f"CRPIX{axis}", 1.0))
        codes = (
            int(round(crval + (k + 1 - crpix) * cdelt))
            for k in range(int(header.get(f"NAXIS{axis}", 1)))
        )
        return [FITS_STOKES_CODES.get(code, f"code {code}") for code in codes]
    return None


def fits_stokes_axis(header):
    """The FITS axis number (1-based) of the STOKES axis, or None."""
    for axis in range(1, int(header.get("NAXIS", 0)) + 1):
        if str(header.get(f"CTYPE{axis}", "")).strip().upper() == "STOKES":
            return axis
    return None


def casa_stokes_labels(csys):
    """Labels of the planes of the Stokes axis of a CASA coordinate system."""
    return [str(label) for label in csys.stokes()]


def plane_index(labels, stokes):
    """Index of the ``stokes`` plane among ``labels``."""
    if labels is None:
        if stokes == "I":
            return None
        raise StokesUnavailable(
            "The image has no Stokes axis, so only Stokes I is available."
        )
    try:
        return labels.index(stokes)
    except ValueError:
        raise StokesUnavailable(
            f"The image has no Stokes {stokes} plane "
            f"(its planes are {', '.join(labels)})."
        ) from None


def rms_in_box(plane, box):
    """RMS of ``plane`` inside ``box`` = (x1, x2, y1, y2), over its finite
    pixels (a reprojected image is blank round its edge); 0 for a box
    without any."""
    x1, x2, y1, y2 = box
    region = plane[x1:x2, y1:y2]
    region = region[np.isfinite(region)]
    if region.size == 0:
        return 0.0
    return np.sqrt(np.mean(region**2))


def uses_rms(stokes):
    """Whether the ``stokes`` product is thresholded against the RMS box."""
    return stokes not in STANDARD_STOKES and stokes not in CORRELATIONS and stokes != "L"


def stokes_product(stokes, planes, thres, rms_box, csys=None, sun_radius_arcmin=55):
    """The ``stokes`` product from ``planes`` ({label: [x, y] array}, those
    named by ``planes_needed``).

    Ratios and the polarization angle keep only pixels where their numerator
    (or the polarized intensity) exceeds ``thres`` times its RMS in
    ``rms_box``, and, given the image's coordinate system ``csys``, only
    pixels near the Sun.
    """
    from .rms import remove_pixels_away_from_sun

    def near_sun(pix):
        if csys is None:
            return pix
        return remove_pixels_away_from_sun(pix, csys, sun_radius_arcmin)

    def significant(pix):
        """``pix`` with values below the threshold set to 0 (a copy, in the
        plane's precision, at least float32)."""
        pix = np.array(pix, dtype=np.result_type(pix, np.float32))
        pix[np.abs(pix) < thres * rms_in_box(pix, rms_box)] = 0
        return pix

    if stokes in STANDARD_STOKES or stokes in CORRELATIONS:
        return planes[stokes]
    if stokes == "L":
        return np.sqrt(planes["Q"] ** 2 + planes["U"] ** 2)
    if stokes == "Lfrac":
        return near_sun(significant(np.sqrt(planes["Q"] ** 2 + planes["U"] ** 2)) / planes["I"])
    if stokes in ("Vfrac", "V/I"):
        return near_sun(significant(planes["V"]) / planes["I"])
    if stokes == "Q/I":
        return near_sun(significant(planes["Q"]) / planes["I"])
    if stokes == "U/I":
        return near_sun(significant(planes["U"]) / planes["I"])
    if stokes == "U/V":
        return near_sun(significant(planes["U"]) / planes["V"])
    if stokes == "PANG":
        q, u = planes["Q"], planes["U"]
        p_rms = np.sqrt(rms_in_box(q, rms_box) ** 2 + rms_in_box(u, rms_box) ** 2)
        pix = 0.5 * np.arctan2(u, q) * 180 / np.pi
        # Only where the polarized intensity is significant
        pix[np.sqrt(q**2 + u**2) < thres * p_rms] = np.nan
        pix = np.where(np.isinf(pix), np.nan, pix)
        return near_sun(pix)
    raise ValueError(f"Unknown Stokes parameter: {stokes}")


def get_available_stokes(imagename):
    """
    Detect available Stokes parameters from a CASA image or FITS file.

    Parameters:
        imagename : str
            Path to the CASA image directory or FITS file.

    Returns:
        list: The products the image's planes offer (offered_planes): the
        standard parameters in I, Q, U, V order, e.g. ["I"], ["I", "V"]
        or ["I", "Q", "U", "V"], then its correlations, e.g. ["LL"] or
        ["I", "V", "RR", "LL"]
    """
    labels = None
    if not is_casa_image(imagename) and ASTROPY_AVAILABLE:
        try:
            labels = fits_stokes_labels(fits_header(imagename))
        except Exception as e:
            logger.warning(f"Could not detect Stokes from FITS: {e}")
            return ["I"]
    elif CASA_AVAILABLE:
        try:
            info = casa_info(imagename)
            names = [str(name).lower() for name in info.summary.get("axisnames", [])]
            if "stokes" in names:
                labels = casa_stokes_labels(info.coordsys())
        except Exception as e:
            logger.warning(f"Could not detect Stokes from CASA image: {e}")
            return ["I"]

    # An image of planes the viewer does not know (code 7...) offers I, which
    # fails to load with a message naming the planes it has
    return offered_planes(labels) or ["I"]

"""Where the Sun is in a radio image, for the phase centre shift: the centre
of a 2-D Gaussian fitted to it, or the middle of its emission above a
threshold. Nothing here needs CASA or a file: the image is a 2-D array."""

from typing import NamedTuple, Optional, Tuple

import numpy as np
import scipy.ndimage as ndi
from scipy.optimize import curve_fit

SMOOTHING = 3.0  # pixels (sigma): the Gaussian is fitted to the image smoothed by this
WINDOW = 50  # pixels each side of the brightest one: the fit sees at least this much
AREA = 5.0  # the Sun's area is the emission above this many times the noise
MARGIN = 0.25  # of that area's size: the sky the fit sees round it
FIT_POINTS = 256  # a window wider than this is fitted on every 2nd, 3rd... pixel
SUN_DIAMETER = 32.0  # arcmin: the optical disk, the least the Sun spans in the radio
COMPACT = 0.5  # of that diameter: a fit narrower than this is not of the disk
PULLED = 0.125  # of that diameter: a fit this far from the middle of the emission is pulled by a bright source
FWHM = 2.0 * np.sqrt(2.0 * np.log(2.0))  # of a Gaussian, in sigmas


class GaussianFit(NamedTuple):
    """The Gaussian fitted to the Sun, in pixels (0-based). The widths (FWHM)
    are of the image itself, the smoothing taken out. ``off_middle`` is how
    far its centre is from the middle of the patch of emission it was fitted
    to (the middle of its area, the brightness not weighed in); NaN when no
    patch could be told from the noise, or the patch runs off the image."""

    x: float
    y: float
    fwhm_x: float
    fwhm_y: float
    off_middle: float = float("nan")


def off_source_noise(image) -> float:
    """The noise of ``image`` away from the Sun: the spread of its values
    about their median, the bright ones (the Sun, a burst) left out. Blank
    (NaN) pixels and a border of zeros are not counted. 0.0 when it cannot be
    told: a constant image, or one with no values."""
    values = np.asarray(image, dtype=np.float64)
    values = values[np.isfinite(values)]
    filled = values[values != 0]
    if filled.size:  # (zeros round the image are not sky)
        values = filled
    spread = 0.0
    for _ in range(10):
        if values.size < 2:
            break
        middle = np.median(values)
        spread = 1.4826 * float(np.median(np.abs(values - middle)))
        if spread == 0:
            break
        kept = values[np.abs(values - middle) < 3 * spread]
        if kept.size == values.size:
            break
        values = kept
    return spread


def whole_image_rms(image) -> float:
    """The root mean square of every value of ``image``, the Sun's included:
    what the threshold counted in before, kept for an image whose noise
    cannot be told. 1.0 for an image with no values."""
    values = np.asarray(image, dtype=np.float64)
    values = values[np.isfinite(values)]
    return float(np.sqrt(np.mean(values**2))) if values.size else 1.0


def threshold_noise(image) -> float:
    """What "x RMS" of the centre of mass counts in: the noise away from the
    Sun, or the whole image's RMS where that cannot be told."""
    noise = off_source_noise(image)
    return noise if noise > 0 else whole_image_rms(image)


def _gaussian(xy, amplitude, x0, y0, sigma_x, sigma_y, offset):
    x, y = xy
    return offset + amplitude * np.exp(-((x - x0) ** 2 / (2 * sigma_x**2) + (y - y0) ** 2 / (2 * sigma_y**2)))


def _sun_area(smoothed, start):
    """The patch of emission that holds the pixel ``start`` (x, y): above
    AREA times the noise. None when the noise cannot be told, or the pixel is
    not above it."""
    noise = off_source_noise(smoothed)
    if noise <= 0:
        return None
    labels, _ = ndi.label(smoothed > AREA * noise)
    label = labels[start[1], start[0]]
    return labels == label if label else None


def gaussian_centre(image) -> GaussianFit:
    """The 2-D Gaussian fitted to the Sun in ``image`` (a 2-D array).

    The fit starts from the brightest pixel of the smoothed image, and sees
    the whole patch of emission round it with some sky on every side (and
    never less than WINDOW pixels each way): with part of the Sun out of
    sight, nothing holds the Gaussian's width, and its centre drifts. A
    failed fit raises (RuntimeError or ValueError)."""
    data = np.nan_to_num(np.asarray(image, dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)
    if data.ndim != 2 or not data.any():
        raise ValueError("no image to fit")
    smoothed = ndi.gaussian_filter(data, sigma=SMOOTHING)
    y0, x0 = (int(i) for i in np.unravel_index(np.argmax(smoothed), smoothed.shape))
    ny, nx = smoothed.shape
    ya, yb, xa, xb = y0 - WINDOW, y0 + WINDOW, x0 - WINDOW, x0 + WINDOW
    guess = [float(x0), float(y0), 20.0, 20.0]
    middle = None
    area = _sun_area(smoothed, (x0, y0))
    if area is not None:
        ys, xs = np.nonzero(area)
        if xs.min() > 0 and ys.min() > 0 and xs.max() < nx - 1 and ys.max() < ny - 1:
            middle = (xs.mean(), ys.mean())  # (of a Sun cut by the image's edge it is not the Sun's)
        margin = int(np.ceil(MARGIN * max(np.ptp(xs), np.ptp(ys)))) + 1
        ya, yb = min(ya, ys.min() - margin), max(yb, ys.max() + margin + 1)
        xa, xb = min(xa, xs.min() - margin), max(xb, xs.max() + margin + 1)
        weights = smoothed[ys, xs]
        total = weights.sum()
        mean_x, mean_y = (weights * xs).sum() / total, (weights * ys).sum() / total
        guess = [
            mean_x,
            mean_y,
            max(np.sqrt((weights * (xs - mean_x) ** 2).sum() / total), 1.0),
            max(np.sqrt((weights * (ys - mean_y) ** 2).sum() / total), 1.0),
        ]
    ya, yb, xa, xb = max(0, ya), min(ny, yb), max(0, xa), min(nx, xb)
    step = max(1, int(np.ceil(max(yb - ya, xb - xa) / FIT_POINTS)))
    y_grid, x_grid = np.mgrid[ya:yb:step, xa:xb:step]
    region = smoothed[ya:yb:step, xa:xb:step]
    if area is None:
        offset = float(region.min())
    else:
        sky = region[~area[ya:yb:step, xa:xb:step]]
        offset = float(np.median(sky)) if sky.size else float(region.min())
    top = float(np.abs(region).max())
    size = float(max(yb - ya, xb - xa))
    low = [0.0, xa, ya, 0.5, 0.5, -top]
    high = [np.inf, xb, yb, size, size, top]
    start = [max(float(region.max()) - offset, top * 1e-6), *guess, offset]
    start = [min(max(value, lo), hi) for value, lo, hi in zip(start, low, high)]
    fitted, _ = curve_fit(
        lambda xy, *p: _gaussian(xy, *p).ravel(),
        (x_grid, y_grid),
        region.ravel(),
        p0=start,
        bounds=(low, high),
        maxfev=5000,
    )
    widths = [FWHM * np.sqrt(max(sigma**2 - SMOOTHING**2, 0.0)) for sigma in fitted[3:5]]
    off = float(np.hypot(fitted[1] - middle[0], fitted[2] - middle[1])) if middle else float("nan")
    return GaussianFit(float(fitted[1]), float(fitted[2]), float(widths[0]), float(widths[1]), off)


def centre_of_mass(image, threshold, within=None) -> Optional[Tuple[float, float]]:
    """The middle ``(x, y)`` of the emission of ``image`` above ``threshold``
    (and inside the mask ``within``, if given): of its area, the brightness
    not weighed in. None when nothing is above the threshold."""
    data = np.asarray(image, dtype=np.float64)
    above = data > threshold  # (False for a blank pixel)
    if within is not None:
        above &= within
    if not above.any():
        return None
    y, x = ndi.center_of_mass(above)
    return float(x), float(y)


def burst_sign(fit: GaussianFit, cell_arcsec) -> Optional[str]:
    """What tells that the Gaussian ``fit`` is not of the Sun's disk alone,
    in words for the user; None for a fit with no such sign, or without a
    pixel size (arcsec) to measure it by.

    Two signs, of a burst or of an active region far brighter than the disk:
    the fit is of a compact source (narrower than COMPACT of the disk, which
    no beam makes of the disk itself), or its centre is pulled away from the
    middle of the emission (by more than PULLED of the disk)."""
    if not cell_arcsec or not np.isfinite(cell_arcsec) or cell_arcsec <= 0:
        return None
    arcmin = cell_arcsec / 60.0
    wide, narrow = sorted((fit.fwhm_x * arcmin, fit.fwhm_y * arcmin), reverse=True)
    if np.sqrt(wide * narrow) < COMPACT * SUN_DIAMETER:
        return (
            f"The Gaussian fit found a source {_arcmin(wide)} by {_arcmin(narrow)} across, much smaller than "
            f"the Sun's disk ({SUN_DIAMETER:.0f}′): a burst or a bright active region, not the disk."
        )
    if np.isfinite(fit.off_middle) and fit.off_middle * arcmin > PULLED * SUN_DIAMETER:
        return (
            f"The Gaussian fit's centre is {_arcmin(fit.off_middle * arcmin)} from the middle of the Sun's "
            "emission: a burst or a bright region is pulling the fit towards it."
        )
    return None


BURST_ADVICE = (
    "The shift worked out from this fit would be off. A quiet image as the reference, or Centre of mass "
    "in place of the Gaussian fit, gives the disk's centre."
)


def _arcmin(value):
    return f"{value:.1f}′" if value < 10 else f"{value:.0f}′"

"""Radial normalisation of coronagraph images for display.

A calibrated coronagraph image falls off by more than an order of
magnitude from the occulter to the edge of the field, and no single colour
stretch shows the streamers at both ends. Three filters flatten that:

- ``ratio``: every pixel divided by the median brightness at its distance
  from the Sun.
- ``nrgf``: the departure from that median in units of the spread at that
  distance, the normalising radial graded filter of Morgan, Habbal and Woo
  (2006).
- ``fnrgf``: the same, but the mean and the spread at each distance are
  allowed to vary smoothly with position angle (a low-order Fourier series
  in angle, after Druckmüllerová, Morgan and Habbal, 2011). The broad
  streamer belt and the F corona, which are not round, are then part of
  the background, and the fine rays and fronts stand out everywhere.

The filters only change what is drawn: the image's values, statistics and
cursor readings stay in the file's unit.

The arrays here are in the viewer's layout, first axis x, second axis y.
"""

import warnings
import weakref

import numpy as np
from scipy.ndimage import uniform_filter1d

# mode -> (name in the Display tab and Presets menu, unit shown on the colorbar)
MODES = {
    "off": ("Off", None),
    "ratio": ("Divide by radial median", "relative to radial median"),
    "nrgf": ("Sigma from radial median", "sigma from radial median"),
    "fnrgf": ("Sigma from Fourier background (FNRGF)", "sigma from local background"),
}
SMOOTH_RINGS = 9  # the radial profiles are smoothed over this many rings (odd, so nothing shifts)
FOURIER_ORDER = 4  # harmonics in position angle for fnrgf
FOURIER_RING = 2  # pixels per ring for fnrgf (more pixels per fit)
MAD_TO_SIGMA = 1.4826  # median absolute deviation -> standard deviation, for a normal spread
FOURIER_BINS = 72  # angular bins per ring for fnrgf (5 degrees)
SIGMA_FLOOR = 0.01  # the spread is at least this fraction of the background (no blow-up where the background fits perfectly)


def _polar(shape, centre):
    xx, yy = np.meshgrid(np.arange(shape[0]) - centre[0], np.arange(shape[1]) - centre[1], indexing="ij")
    return np.hypot(xx, yy), np.arctan2(yy, xx)


def _rings(radius, width):
    """Each pixel's ring index, and the slices grouping the finite values
    of an image by ring: (rbin, order, bounds) so that values[order]
    [bounds[i]:bounds[i+1]] is ring i."""
    rbin = (radius / width).astype(np.intp)
    return rbin


def _smooth_profile(profile, good):
    """``profile`` (rows indexed by ring) with the rings that could not be
    measured filled from their neighbours and the rest smoothed."""
    index = np.arange(profile.shape[0])
    if good.sum() < 2:
        return np.where(good[:, None] if profile.ndim == 2 else good, profile, np.nan)
    out = np.empty_like(profile, dtype=np.float64)
    if profile.ndim == 1:
        return np.interp(index, index[good], uniform_filter1d(profile[good], SMOOTH_RINGS, mode="nearest"))
    for j in range(profile.shape[1]):
        out[:, j] = np.interp(index, index[good], uniform_filter1d(profile[good, j], SMOOTH_RINGS // 2 | 1, mode="nearest"))
    return out


def radial_profile(data, centre):
    """The median and standard deviation of ``data`` in rings one pixel
    wide around ``centre`` (x, y), smoothed, as arrays indexed by ring,
    with each pixel's ring index."""
    radius, _ = _polar(data.shape, centre)
    rbin = _rings(radius, 1)
    values = np.asarray(data, dtype=np.float64).ravel()
    flat = rbin.ravel()
    ok = np.isfinite(values)
    flat, values = flat[ok], values[ok]
    nb = int(rbin.max()) + 1
    median = np.full(nb, np.nan)
    sigma = np.full(nb, np.nan)
    order = np.argsort(flat, kind="stable")
    flat, values = flat[order], values[order]
    bounds = np.searchsorted(flat, np.arange(nb + 1))
    for i in range(nb):
        ring = values[bounds[i] : bounds[i + 1]]
        if ring.size >= 8:
            median[i] = np.median(ring)
            sigma[i] = ring.std()
    good = np.isfinite(median) & np.isfinite(sigma) & (sigma > 0)
    median = _smooth_profile(median, good)
    sigma = _smooth_profile(sigma, good)
    sigma = np.where(sigma > 0, sigma, np.nan)
    return median, sigma, rbin


def fourier_background(data, centre, order=FOURIER_ORDER, width=FOURIER_RING, bins=FOURIER_BINS):
    """The local mean and spread of ``data`` as smooth functions of
    distance and position angle. Each ring is cut into angular ``bins``;
    the median of each bin, then the median absolute departure from the
    fit, are fitted by a Fourier series in angle of the given ``order``
    (medians, so that thin rays, stars and the pylon do not pull the
    background); the coefficients are smoothed from ring to ring. A ring
    with too few bins for the fit (the corners of the frame outside the
    round field) keeps only its own level. Returns (mean, sigma) images."""
    from scipy import ndimage

    radius, theta = _polar(data.shape, centre)
    rbin = _rings(radius, width)
    nb = int(rbin.max()) + 1
    abin = np.minimum(((theta + np.pi) / (2 * np.pi) * bins).astype(np.intp), bins - 1)
    nterms = 2 * order + 1

    def fourier_basis(angle):
        basis = np.empty(angle.shape + (nterms,))
        basis[..., 0] = 1.0
        for k in range(1, order + 1):
            basis[..., 2 * k - 1] = np.cos(k * angle)
            basis[..., 2 * k] = np.sin(k * angle)
        return basis

    values = np.asarray(data, dtype=np.float64)
    finite = np.isfinite(values)
    labels = np.where(finite, rbin * bins + abin + 1, 0)
    index = np.arange(1, nb * bins + 1)
    counts = np.bincount(labels.ravel(), minlength=nb * bins + 1)[1:].reshape(nb, bins)
    centres = (np.arange(bins) + 0.5) / bins * 2 * np.pi - np.pi
    bin_basis = fourier_basis(centres)  # bins x nterms

    def solve(per_bin, w):
        """Fourier coefficients per ring from the bin values with weights
        ``w``, by the normal equations. A ring seen in too few directions
        (the corners of the frame outside the round field) gets no
        harmonics, just its own level. Returns (coef, rows that have one)."""
        filled = np.where(w > 0, per_bin, 0.0)
        gram = np.einsum("bi,bj,rb->rij", bin_basis, bin_basis, w)
        rhs = np.einsum("bi,rb->ri", bin_basis, filled * w)
        nbins = (w > 0).sum(axis=1)
        enough = nbins >= 2 * nterms
        coef = np.full((nb, nterms), np.nan)
        if enough.any():
            coef[enough] = np.linalg.solve(gram[enough], rhs[enough][..., None])[..., 0]
        partial = ~enough & (nbins >= 1)
        coef[partial] = 0.0
        coef[partial, 0] = rhs[partial, 0] / w[partial].sum(axis=1)
        return coef, enough | partial

    def fit(per_bin):
        """A robust Fourier fit per ring: bins whose value stands more than
        three spreads from the first fit (a wide ray or a planet) are left
        out of a second one."""
        usable = np.isfinite(per_bin) & (counts >= 3)
        w = usable.astype(float)
        coef, good = solve(per_bin, w)
        model = np.einsum("bi,ri->rb", bin_basis, np.nan_to_num(coef))
        residual = np.where(usable, per_bin - model, np.nan)
        with np.errstate(invalid="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # rings without a single bin
            scale = np.nanmedian(np.abs(residual), axis=1, keepdims=True) * MAD_TO_SIGMA
            keep = usable & ~(np.abs(residual) > 3 * scale)
        return solve(per_bin, keep.astype(float))

    medians = ndimage.median(values, labels, index).reshape(nb, bins)
    mean_coef, good = fit(medians)
    mean_coef = _smooth_profile(mean_coef, good)
    pixel_basis = fourier_basis(theta)
    rclip = np.clip(rbin, 0, nb - 1)
    mean = np.einsum("xyk,xyk->xy", pixel_basis, mean_coef[rclip])
    departures = np.where(finite, np.abs(values - mean), np.nan)
    spread = ndimage.median(departures, labels, index).reshape(nb, bins)
    sigma_coef, good_sigma = fit(spread)
    sigma_coef = _smooth_profile(sigma_coef, good_sigma)
    sigma = np.einsum("xyk,xyk->xy", pixel_basis, sigma_coef[rclip]) * MAD_TO_SIGMA
    sigma = np.maximum(sigma, SIGMA_FLOOR * np.abs(mean))
    return mean, np.where(sigma > 0, sigma, np.nan)


def apply(data, mode, centre):
    """``data`` filtered for display in ``mode`` ('ratio', 'nrgf' or
    'fnrgf'), as float32; 'off' returns ``data`` itself."""
    if mode == "off" or mode is None:
        return data
    if mode not in MODES:
        raise ValueError(f"unknown radial filter {mode!r}")
    values = np.asarray(data, dtype=np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        if mode == "fnrgf":
            mean, sigma = fourier_background(values, centre)
            out = (values - mean) / sigma
        else:
            median, sigma, rbin = radial_profile(values, centre)
            rbin = np.clip(rbin, 0, len(median) - 1)
            if mode == "ratio":
                out = values / median[rbin]
            else:
                out = (values - median[rbin]) / sigma[rbin]
    out[~np.isfinite(out)] = np.nan
    return out.astype(np.float32)


class RadialFilterCache:
    """The filtered version of one image, kept while the image, the mode
    and the centre are the same (the image is held by a weak reference,
    so a freed array's id cannot be mistaken for a new one's)."""

    def __init__(self):
        self._ref = None
        self._key = None
        self._result = None

    def get(self, data, mode, centre):
        key = (mode, tuple(round(float(c), 3) for c in centre), data.shape)
        if self._ref is not None and self._ref() is data and self._key == key:
            return self._result
        self._result = apply(data, mode, centre)
        self._ref = weakref.ref(data)
        self._key = key
        return self._result

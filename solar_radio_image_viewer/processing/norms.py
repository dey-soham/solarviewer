import numpy as np
from matplotlib.colors import Normalize
import warnings


class SqrtNorm(Normalize):
    """
    Square root normalization for enhancing faint features.

    Applies sqrt(x) scaling which compresses bright values and enhances faint ones.
    Commonly used in solar EUV imaging.
    """

    def __call__(self, value, clip=None):
        normed = super().__call__(value, clip)
        # Clip to avoid sqrt of negative values (can happen with edge cases)
        normed = np.clip(normed, 0, 1)
        # Handle masked arrays
        if hasattr(normed, "mask"):
            result = np.ma.sqrt(normed)
        else:
            result = np.sqrt(normed)
        return result


class AsinhNorm(Normalize):
    """
    Inverse hyperbolic sine (arcsinh) normalization.

    Similar to logarithmic scaling but handles zero and negative values gracefully.
    Excellent for high dynamic range solar images like AIA EUV channels.

    Parameters
    ----------
    linear_width : float, optional
        Controls the transition between linear and logarithmic behavior.
        Smaller values = more compression of bright features. Default is 1e-3.
    """

    def __init__(self, vmin=None, vmax=None, clip=False, linear_width=1e-3):
        super().__init__(vmin, vmax, clip)
        # Validate linear_width
        if linear_width <= 0:
            warnings.warn("linear_width must be positive, using default 1e-3")
            linear_width = 1e-3
        self.linear_width = linear_width

    def __call__(self, value, clip=None):
        normed = super().__call__(value, clip)
        # Protect against division by zero
        with np.errstate(invalid="ignore", divide="ignore"):
            denominator = np.arcsinh(1.0 / self.linear_width)
            if denominator == 0:
                return normed  # Fall back to linear
            result = np.arcsinh(normed / self.linear_width) / denominator
        # Handle any NaN that might result
        if hasattr(result, "mask"):
            return result
        return np.nan_to_num(result, nan=0.0, posinf=1.0, neginf=0.0)


class PowerNorm(Normalize):
    """
    Power-law (gamma) normalization.

    Applies x^gamma scaling. Gamma < 1 enhances faint features,
    gamma > 1 enhances bright features.

    Parameters
    ----------
    gamma : float, optional
        The power exponent. Default is 1.0 (linear).
        Common values: 0.5 (sqrt-like), 0.3 (strong faint enhancement)
    """

    def __init__(self, vmin=None, vmax=None, clip=False, gamma=1.0):
        super().__init__(vmin, vmax, clip)
        # Validate gamma
        if gamma <= 0:
            warnings.warn("gamma must be positive, using default 1.0")
            gamma = 1.0
        self.gamma = gamma

    def __call__(self, value, clip=None):
        normed = super().__call__(value, clip)
        # Clip to ensure non-negative values for power operation
        normed = np.clip(normed, 0, 1)
        # Use safe power operation
        with np.errstate(invalid="ignore"):
            result = np.power(normed, self.gamma)
        return np.nan_to_num(result, nan=0.0, posinf=1.0, neginf=0.0)


def zscale_samples(data, count):
    """About ``count`` values of ``data`` for ZScale (all of them, of an
    image with no more), blank (NaN, infinite or masked) ones left out.

    Of an image they are taken on a grid of rows and columns over the whole
    of it, as IRAF takes them. Every k-th value of the flattened image, which
    is how astropy picks its own, is one single column of an image whose
    height is a multiple of the count (600 samples of a 600 or 1200 pixel
    image): its left edge, with nothing of the Sun in it."""
    values = np.ma.masked_invalid(data, copy=False)
    if values.size <= count:
        return values.compressed()
    if values.ndim == 2:
        ny, nx = values.shape
        rows = min(ny, int(np.ceil(np.sqrt(count * ny / nx))))
        columns = min(nx, int(np.ceil(count / rows)))
        y = ((np.arange(rows) + 0.5) * ny / rows).astype(int)
        x = ((np.arange(columns) + 0.5) * nx / columns).astype(int)
        samples = values[np.ix_(y, x)].compressed()
        if 2 * samples.size >= count:
            return samples
    # Not an image, or one that is mostly blank where the grid falls:
    # evenly through what values it has
    values = values.compressed()
    if values.size <= count:
        return values
    return values[np.linspace(0, values.size - 1, count).astype(int)]


class ZScaleNorm(Normalize):
    """
    ZScale normalization (the IRAF algorithm, as DS9 and astropy have it).

    The display range is worked out from a sample of the image: a line is
    fitted to the sorted sample values, the outliers left out of the fit, and
    the range is the median plus and minus half the sample count times the
    line's slope over the contrast, kept within the sample's own range.

    The limits are those of ``astropy.visualization.ZScaleInterval``; the
    samples are picked here, on a grid over the whole image (``zscale_samples``).

    Parameters
    ----------
    contrast : float, optional
        Contrast parameter that adjusts the slope (default is 0.25).
    num_samples : int, optional
        Number of samples to use if the image is large (default is 600).
    max_iterations : int, optional
        Maximum iterations for sigma clipping (default is 5).
    krej : float, optional
        Rejection threshold in units of sigma (default is 2.5).
    min_npixels : int, optional
        Minimum number of pixels required to continue fitting (default is 5).
    """

    def __init__(
        self,
        vmin=None,
        vmax=None,
        clip=False,
        contrast=0.25,
        num_samples=600,
        max_iterations=5,
        krej=2.5,
        min_npixels=5,
    ):
        super().__init__(vmin, vmax, clip)
        # Validate parameters
        self.contrast = max(0.01, contrast)  # Prevent division by zero
        self.num_samples = max(10, num_samples)
        self.max_iterations = max(1, max_iterations)
        self.krej = max(0.5, krej)
        self.min_npixels = max(2, min_npixels)
        self._zscale_computed = False
        self._zmin = None
        self._zmax = None

    def _compute_zscale(self, data):
        """The ZScale limits of ``data``; (0, 1) for an image with no values,
        its smallest and largest value where the algorithm has nothing to go
        by (a handful of pixels, a constant image) or fails."""
        try:
            samples = zscale_samples(data, self.num_samples)
            if samples.size == 0:
                return 0, 1
            if samples.size < self.min_npixels:
                return float(samples.min()), float(samples.max())

            from astropy.visualization import ZScaleInterval

            # (n_samples: every sample given is used, none picked again)
            zmin, zmax = ZScaleInterval(
                n_samples=samples.size,
                contrast=self.contrast,
                krej=self.krej,
                max_iterations=self.max_iterations,
                min_npixels=self.min_npixels,
            ).get_limits(samples)

            # Ensure zmin < zmax
            if not zmin < zmax:
                zmin, zmax = samples.min(), samples.max()

            return float(zmin), float(zmax)

        except Exception as e:
            warnings.warn(f"ZScale computation failed: {e}, using min/max")
            flat = np.asarray(data).flatten()
            flat = flat[np.isfinite(flat)]
            if flat.size > 0:
                return float(np.min(flat)), float(np.max(flat))
            return 0, 1

    def fit(self, data):
        """Take the ZScale limits of ``data``, the image to be drawn, as the
        range. Done before anything else uses the norm: a colorbar made
        first would otherwise hand it its own ramp of values. Returns the
        norm."""
        self._zmin, self._zmax = self._compute_zscale(data)
        self._zscale_computed = True
        self.vmin = self._zmin
        self.vmax = self._zmax
        return self

    def __call__(self, value, clip=None):
        # Not fitted: the first image given (an image, not a scalar)
        if not self._zscale_computed and isinstance(value, np.ndarray):
            self.fit(value)
        return super().__call__(value, clip)


class HistEqNorm(Normalize):
    """
    Histogram equalization normalization.

    Enhances contrast by redistributing intensity values so that the
    cumulative distribution function becomes approximately linear.
    Useful for images with poor contrast or non-uniform intensity distribution.

    Parameters
    ----------
    n_bins : int, optional
        How many steps of the image's percentiles the mapping is made of
        (default is 256).
    """

    # The most pixels it is worked out from (an even sample of a larger
    # image), so that equalizing stays quick while the range is dragged
    MAX_SAMPLES = 500_000

    def __init__(self, vmin=None, vmax=None, clip=False, n_bins=256):
        super().__init__(vmin, vmax, clip)
        self.n_bins = max(16, min(4096, n_bins))  # Clamp to reasonable range
        self._hist_eq_computed = False
        # (edges, cdf): places in the range (0 at vmin, 1 at vmax) and the
        # share of the image's pixels in the range below each
        self._hist_eq_map = None

    def fit(self, data):
        """Equalize on ``data``, the image to be drawn: each colour of the
        map then goes to about as many of its pixels between vmin and vmax
        (those below vmin take the first colour, those above the last).
        Done before anything else uses the norm: a colorbar made first
        would otherwise hand it its own ramp of values. Returns the norm."""
        self._hist_eq_map = self._compute_hist_eq(data)
        self._hist_eq_computed = True
        return self

    def _compute_hist_eq(self, data):
        """The (edges, cdf) mapping of ``data``, or None to stay linear."""
        try:
            values = data
            if np.ndim(values) == 2 and np.size(values) > self.MAX_SAMPLES:
                # A grid over the whole image (no copy of all of it)
                step = int(np.ceil(np.sqrt(np.size(values) / self.MAX_SAMPLES)))
                values = values[::step, ::step]
            sample = np.ma.asarray(values, dtype=float).filled(np.nan).ravel()
            if sample.size > self.MAX_SAMPLES:  # (not an image)
                sample = sample[:: sample.size // self.MAX_SAMPLES]
            sample = sample[np.isfinite(sample)]
            if sample.size == 0:
                return None
            self.autoscale_None(sample)
            low, high = float(self.vmin), float(self.vmax)
            if not high > low:
                return None
            inside = np.sort(sample[(sample >= low) & (sample <= high)])
            if inside.size == 0:
                return None
            # The image's percentiles (not a histogram of bins of equal
            # width: a radio image's noise, a few pixels wide in a range up
            # to the Sun's brightness, would all fall in one bin), placed
            # in the range, between its ends
            levels = np.linspace(0.0, 1.0, self.n_bins + 1)
            percentiles = inside[np.round(levels * (inside.size - 1)).astype(int)]
            places = np.concatenate([[0.0], (percentiles - low) / (high - low), [1.0]])
            levels = np.concatenate([[0.0], levels, [1.0]])
            # One point per value: one many pixels share (a blank sky of
            # zeros) goes to the middle of its step
            edges, first = np.unique(places, return_index=True)
            counts = np.diff(np.append(first, places.size))
            cdf = np.add.reduceat(levels, first) / counts
            # Strictly rising, so that it can be turned back
            cdf = (cdf + 1e-6 * edges) / (1 + 1e-6)
            return edges, cdf
        except Exception as e:
            warnings.warn(f"Histogram equalization failed: {e}")
            return None

    def __call__(self, value, clip=None):
        # Not fitted: the first image given (an image, not a scalar)
        if not self._hist_eq_computed and isinstance(value, np.ndarray):
            self.fit(value)
        normed = super().__call__(value, clip)
        if self._hist_eq_map is None:
            return normed
        edges, cdf = self._hist_eq_map
        mapped = np.interp(np.ma.getdata(normed), edges, cdf)
        result = np.ma.array(mapped, mask=np.ma.getmask(normed))
        return result if np.ndim(normed) else result[()]

    def inverse(self, value):
        """The data value at each position of the colormap (0 to 1): the
        colorbar's labels are placed with it."""
        if self._hist_eq_map is None or not self.scaled():
            return super().inverse(value)
        edges, cdf = self._hist_eq_map
        position = np.interp(np.ma.getdata(value), cdf, edges)
        return self.vmin + position * (self.vmax - self.vmin)

    def even_ticks(self, count=6):
        """About ``count`` round values at even steps along a colorbar of
        this norm, for its labels (round values evenly apart in the data
        bunch where the image has few pixels, one over the other), or
        None before it is fitted."""
        if self._hist_eq_map is None or not self.scaled():
            return None
        low, high = sorted((float(self.vmin), float(self.vmax)))
        ticks = []
        for place in np.linspace(0, 1, max(2, count)):
            value = float(self.inverse(place))
            # Two figures if that stays within the range, else more
            for figures in (2, 3, 6):
                rounded = float(f"{value:.{figures}g}")
                if low <= rounded <= high:
                    break
            else:
                rounded = value
            if not ticks or rounded > ticks[-1]:
                ticks.append(rounded)
        return ticks

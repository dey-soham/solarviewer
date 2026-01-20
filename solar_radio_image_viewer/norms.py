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


class ZScaleNorm(Normalize):
    """
    ZScale normalization using iterative sigma clipping (IRAF algorithm).

    Automatically determines optimal display range by fitting a line to
    sorted pixel values and rejecting outliers. Widely used in astronomy.

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
        """Compute ZScale limits using iterative sigma clipping."""
        try:
            # Flatten data and remove NaNs/Infs
            flat_data = np.asarray(data).flatten()
            mask = np.isfinite(flat_data)
            flat_data = flat_data[mask]

            if flat_data.size == 0:
                return 0, 1

            if flat_data.size < self.min_npixels:
                return float(np.min(flat_data)), float(np.max(flat_data))

            # Sample the data if necessary
            if flat_data.size > self.num_samples:
                indices = np.linspace(0, flat_data.size - 1, self.num_samples).astype(
                    int
                )
                samples = np.sort(flat_data[indices])
            else:
                samples = np.sort(flat_data)

            # Iterative sigma clipping
            for _ in range(self.max_iterations):
                if len(samples) < self.min_npixels:
                    break

                x = np.arange(len(samples))
                try:
                    slope, intercept = np.polyfit(x, samples, 1)
                except (np.linalg.LinAlgError, ValueError):
                    break

                fitted = slope * x + intercept
                residuals = samples - fitted
                sigma = np.std(residuals)

                if sigma == 0:
                    break

                mask = np.abs(residuals) < self.krej * sigma
                if np.sum(mask) < self.min_npixels:
                    break

                new_samples = samples[mask]
                if new_samples.size == samples.size:
                    break
                samples = new_samples

            # Adjust slope by contrast parameter
            if slope != 0:
                slope /= self.contrast

            # Use median as pivot
            median_index = len(samples) // 2
            median_val = samples[median_index]

            # Compute zscale limits
            zmin = median_val - slope * median_index
            zmax = median_val + slope * (len(samples) - median_index)

            # Ensure limits are within data range
            zmin = max(zmin, samples[0])
            zmax = min(zmax, samples[-1])

            # Ensure zmin < zmax
            if zmin >= zmax:
                zmin = samples[0]
                zmax = samples[-1]

            return float(zmin), float(zmax)

        except Exception as e:
            warnings.warn(f"ZScale computation failed: {e}, using min/max")
            flat = np.asarray(data).flatten()
            flat = flat[np.isfinite(flat)]
            if flat.size > 0:
                return float(np.min(flat)), float(np.max(flat))
            return 0, 1

    def __call__(self, value, clip=None):
        if not self._zscale_computed and isinstance(value, np.ndarray):
            self._zmin, self._zmax = self._compute_zscale(value)
            self._zscale_computed = True
            self.vmin = self._zmin
            self.vmax = self._zmax
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
        Number of bins for the histogram (default is 256).
    """

    def __init__(self, vmin=None, vmax=None, clip=False, n_bins=256):
        super().__init__(vmin, vmax, clip)
        self.n_bins = max(16, min(4096, n_bins))  # Clamp to reasonable range
        self._hist_eq_computed = False
        self._hist_eq_map = None

    def _compute_hist_eq(self, data):
        """Compute histogram equalization mapping."""
        try:
            flat_data = np.asarray(data).flatten()
            flat_data = flat_data[np.isfinite(flat_data)]

            if flat_data.size == 0:
                return np.linspace(0, 1, self.n_bins), np.linspace(0, 1, self.n_bins)

            # Compute histogram and CDF
            hist, bin_edges = np.histogram(flat_data, bins=self.n_bins)

            # Avoid division by zero
            hist_sum = hist.sum()
            if hist_sum == 0:
                return np.linspace(0, 1, self.n_bins), np.linspace(0, 1, self.n_bins)

            cdf = hist.cumsum() / hist_sum
            bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2

            return bin_centers, cdf

        except Exception as e:
            warnings.warn(f"Histogram equalization failed: {e}")
            return np.linspace(0, 1, self.n_bins), np.linspace(0, 1, self.n_bins)

    def __call__(self, value, clip=None):
        try:
            if not self._hist_eq_computed and isinstance(value, np.ndarray):
                normed = super().__call__(value, clip)
                bin_centers, cdf = self._compute_hist_eq(normed)
                self._hist_eq_map = (bin_centers, cdf)
                self._hist_eq_computed = True
                return self._apply_hist_eq(normed, bin_centers, cdf)
            elif self._hist_eq_computed and self._hist_eq_map is not None:
                normed = super().__call__(value, clip)
                bin_centers, cdf = self._hist_eq_map
                return self._apply_hist_eq(normed, bin_centers, cdf)
            else:
                return super().__call__(value, clip)
        except Exception:
            return super().__call__(value, clip)

    def _apply_hist_eq(self, normed, bin_centers, cdf):
        """Apply histogram equalization mapping."""
        if np.isscalar(normed):
            idx = np.abs(bin_centers - normed).argmin()
            return cdf[idx]
        else:
            normed_flat = np.asarray(normed).flatten()
            result = np.interp(normed_flat, bin_centers, cdf)
            return result.reshape(normed.shape)

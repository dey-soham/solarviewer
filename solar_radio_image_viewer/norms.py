import numpy as np
from matplotlib.colors import Normalize


class SqrtNorm(Normalize):
    def __call__(self, value, clip=None):
        normed = super().__call__(value, clip)
        return np.sqrt(normed)


class AsinhNorm(Normalize):
    def __init__(self, vmin=None, vmax=None, clip=False, linear_width=1e-3):
        super().__init__(vmin, vmax, clip)
        self.linear_width = linear_width

    def __call__(self, value, clip=None):
        normed = super().__call__(value, clip)
        return np.arcsinh(normed / self.linear_width) / np.arcsinh(
            1.0 / self.linear_width
        )


class PowerNorm(Normalize):
    def __init__(self, vmin=None, vmax=None, clip=False, gamma=1.0):
        super().__init__(vmin, vmax, clip)
        self.gamma = gamma

    def __call__(self, value, clip=None):
        normed = super().__call__(value, clip)
        normed = np.clip(normed, 0, 1)
        return np.power(normed, self.gamma)


class ZScaleNorm(Normalize):
    """
    Improved ZScale normalization using iterative sigma clipping for robust linear fitting.

    Parameters
    ----------
    vmin, vmax : float, optional
        Minimum and maximum data values. If None, these will be computed from the data.
    clip : bool, optional
        If True, values outside the range [vmin, vmax] are clipped.
    contrast : float, optional
        Contrast parameter that adjusts the slope (default is 0.25).
    nsamples : int, optional
        Number of samples to use if the image is large (default is 600).
    max_iterations : int, optional
        Maximum iterations for sigma clipping (default is 5).
    krej : float, optional
        Rejection threshold in units of sigma (default is 2.5).
    min_npixels : int, optional
        Minimum number of pixels required to continue iterative fitting (default is 5).
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
        self.contrast = contrast
        self.num_samples = num_samples
        self.max_iterations = max_iterations
        self.krej = krej
        self.min_npixels = min_npixels
        self._zscale_computed = False
        self._zmin = None
        self._zmax = None

    def _compute_zscale(self, data):
        # Flatten data and remove NaNs
        flat_data = data.flatten()
        flat_data = flat_data[~np.isnan(flat_data)]
        if flat_data.size == 0:
            return 0, 1

        # Sample the data if necessary
        if flat_data.size > self.num_samples:
            indices = np.linspace(0, flat_data.size - 1, self.num_samples).astype(int)
            samples = np.sort(flat_data[indices])
        else:
            samples = np.sort(flat_data)

        # Start with the full sample and iteratively reject outliers
        for iteration in range(self.max_iterations):
            x = np.arange(len(samples))
            # Fit a line: y = slope * x + intercept
            slope, intercept = np.polyfit(x, samples, 1)
            # Compute residuals and sigma
            fitted = slope * x + intercept
            residuals = samples - fitted
            sigma = np.std(residuals)
            # Create mask for values within krej sigma
            mask = np.abs(residuals) < self.krej * sigma
            if np.sum(mask) < self.min_npixels:
                # Not enough points to continue rejection; break out
                break
            new_samples = samples[mask]
            # Stop if the sample doesn't change (i.e. converged)
            if new_samples.size == samples.size:
                break
            samples = new_samples

        # Adjust the slope by the contrast parameter (avoid division by zero)
        if slope != 0:
            slope /= self.contrast

        # Use the median as a pivot for scaling
        median_index = len(samples) // 2
        median_val = samples[median_index]

        # Compute tentative zscale limits
        zmin = median_val - slope * median_index
        zmax = median_val + slope * (len(samples) - median_index)

        # Ensure limits fall within the data range
        zmin = max(zmin, samples[0])
        zmax = min(zmax, samples[-1])
        return zmin, zmax

    def __call__(self, value, clip=None):
        # Compute zscale limits once for a given dataset
        if not self._zscale_computed and isinstance(value, np.ndarray):
            self._zmin, self._zmax = self._compute_zscale(value)
            self._zscale_computed = True
            self.vmin = self._zmin
            self.vmax = self._zmax
        return super().__call__(value, clip)


'''class ZScaleNorm(Normalize):
    """
    Implementation of the ZScale algorithm used in astronomical imaging.

    The algorithm is based on the IRAF implementation and uses a contrast parameter
    to determine the slope of the scaling function.

    Parameters
    ----------
    contrast : float, optional
        The contrast parameter for the zscale algorithm. Default is 0.25.
    num_samples : int, optional
        The number of samples to use for the zscale algorithm. Default is 600.
    """

    def __init__(
        self, vmin=None, vmax=None, clip=False, contrast=0.25, num_samples=600
    ):
        super().__init__(vmin, vmax, clip)
        self.contrast = contrast
        self.num_samples = num_samples
        self._zscale_computed = False
        self._zmin = None
        self._zmax = None

    def _compute_zscale(self, data):
        """Compute ZScale limits for the given data."""
        # Flatten the data and remove NaNs
        flat_data = data.flatten()
        flat_data = flat_data[~np.isnan(flat_data)]

        # If there's no valid data, return default values
        if flat_data.size == 0:
            return 0, 1

        # Sample the data if it's large
        if flat_data.size > self.num_samples:
            indices = np.linspace(0, flat_data.size - 1, self.num_samples).astype(int)
            samples = np.sort(flat_data[indices])
        else:
            samples = np.sort(flat_data)

        # Compute the median
        midpoint = samples.size // 2

        # Compute the slope of the linear fit using a subset of the data around the median
        i1 = max(0, int(midpoint - (midpoint * 0.25)))
        i2 = min(samples.size - 1, int(midpoint + (midpoint * 0.25)))
        subset = samples[i1:i2]

        # Fit a line to the sorted subset
        x = np.arange(subset.size)
        y = subset

        # Compute the slope using least squares
        if subset.size > 1:
            slope, intercept = np.polyfit(x, y, 1)
        else:
            slope = 0
            intercept = subset[0] if subset.size > 0 else 0

        # Adjust the slope based on the contrast parameter
        if slope != 0:
            slope = slope / self.contrast

        # Compute the zscale limits
        zmin = samples[0]
        zmax = samples[-1]

        # Adjust the limits based on the slope
        if slope != 0:
            zmin = max(zmin, samples[midpoint] - (midpoint * slope))
            zmax = min(zmax, samples[midpoint] + ((samples.size - midpoint) * slope))

        return zmin, zmax

    def __call__(self, value, clip=None):
        # If we haven't computed zscale limits yet, do it now
        if not self._zscale_computed and isinstance(value, np.ndarray):
            self._zmin, self._zmax = self._compute_zscale(value)
            self._zscale_computed = True

            # Update vmin and vmax
            self.vmin = self._zmin
            self.vmax = self._zmax

        return super().__call__(value, clip)'''


class HistEqNorm(Normalize):
    """
    Histogram equalization normalization.

    This normalization enhances contrast by redistributing intensity values
    so that the cumulative distribution function becomes approximately linear.

    Parameters
    ----------
    n_bins : int, optional
        Number of bins to use for the histogram. Default is 256.
    """

    def __init__(self, vmin=None, vmax=None, clip=False, n_bins=256):
        super().__init__(vmin, vmax, clip)
        self.n_bins = n_bins
        self._hist_eq_computed = False
        self._hist_eq_map = None

    def _compute_hist_eq(self, data):
        """Compute histogram equalization mapping for the given data."""
        # Flatten the data and remove NaNs
        flat_data = data.flatten()
        valid_mask = ~np.isnan(flat_data)
        flat_data = flat_data[valid_mask]

        # If there's no valid data, return identity mapping
        if flat_data.size == 0:
            return np.linspace(0, 1, self.n_bins)

        # Compute histogram and CDF
        hist, bin_edges = np.histogram(flat_data, bins=self.n_bins)
        cdf = hist.cumsum() / hist.sum()

        # Create mapping from original values to equalized values
        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2

        # Return the mapping
        return bin_centers, cdf

    def __call__(self, value, clip=None):
        # If we haven't computed histogram equalization yet, do it now
        if not self._hist_eq_computed and isinstance(value, np.ndarray):
            # First normalize the data to [0, 1] using the parent class
            normed = super().__call__(value, clip)

            # Compute histogram equalization
            bin_centers, cdf = self._compute_hist_eq(normed)
            self._hist_eq_map = (bin_centers, cdf)
            self._hist_eq_computed = True

            # Apply histogram equalization
            # For each pixel, find the closest bin and use its CDF value
            if np.isscalar(normed):
                idx = np.abs(bin_centers - normed).argmin()
                return cdf[idx]
            else:
                # For array input, we need to interpolate
                return np.interp(normed.flatten(), bin_centers, cdf).reshape(
                    normed.shape
                )
        elif self._hist_eq_computed:
            # If we've already computed the mapping, apply it
            normed = super().__call__(value, clip)
            bin_centers, cdf = self._hist_eq_map

            if np.isscalar(normed):
                idx = np.abs(bin_centers - normed).argmin()
                return cdf[idx]
            else:
                return np.interp(normed.flatten(), bin_centers, cdf).reshape(
                    normed.shape
                )
        else:
            # If we can't compute histogram equalization, just use linear normalization
            return super().__call__(value, clip)

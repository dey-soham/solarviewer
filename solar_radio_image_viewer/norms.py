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
        return np.arcsinh(normed / self.linear_width) / np.arcsinh(1.0 / self.linear_width)

class PowerNorm(Normalize):
    def __init__(self, vmin=None, vmax=None, clip=False, gamma=1.0):
        super().__init__(vmin, vmax, clip)
        self.gamma = gamma

    def __call__(self, value, clip=None):
        normed = super().__call__(value, clip)
        normed = np.clip(normed, 0, 1)
        return np.power(normed, self.gamma)


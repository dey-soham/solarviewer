"""
Analytic 2D source models used for fitting.
"""

import numpy as np


def twoD_gaussian(coords, amplitude, xo, yo, sigma_x, sigma_y, theta, offset):
    x, y = coords
    xo = float(xo)
    yo = float(yo)
    a = (np.cos(theta) ** 2) / (2 * sigma_x**2) + (np.sin(theta) ** 2) / (
        2 * sigma_y**2
    )
    b = -np.sin(2 * theta) / (4 * sigma_x**2) + np.sin(2 * theta) / (4 * sigma_y**2)
    c = (np.sin(theta) ** 2) / (2 * sigma_x**2) + (np.cos(theta) ** 2) / (
        2 * sigma_y**2
    )
    g = offset + amplitude * np.exp(
        -(a * ((x - xo) ** 2) + 2 * b * (x - xo) * (y - yo) + c * ((y - yo) ** 2))
    )
    return g.ravel()


def twoD_elliptical_ring(coords, amplitude, xo, yo, inner_r, outer_r, offset):
    x, y = coords
    dist2 = (x - xo) ** 2 + (y - yo) ** 2
    inner2 = inner_r**2
    outer2 = outer_r**2
    vals = np.full_like(dist2, offset, dtype=float)
    ring_mask = (dist2 >= inner2) & (dist2 <= outer2)
    vals[ring_mask] = offset + amplitude
    return vals.ravel()

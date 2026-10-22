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


def twoD_elliptical_ring(
    coords, amplitude, xo, yo, inner_r, outer_r, offset, edge_width=1.0
):
    """A flat ring between ``inner_r`` and ``outer_r`` around (xo, yo).

    Its edges rise over about ``edge_width`` pixels instead of jumping, so a
    least-squares fit can move the centre and radii (a sharp-edged ring has
    no gradient, and the fit never leaves its first guess).
    """
    x, y = coords
    r = np.hypot(x - float(xo), y - float(yo))

    def step(t):
        # Logistic step written with tanh, which does not overflow
        return 0.5 * (1.0 + np.tanh(t / (2.0 * edge_width)))

    ring = step(r - abs(inner_r)) * step(abs(outer_r) - r)
    return (offset + amplitude * ring).ravel()

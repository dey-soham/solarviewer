"""
Draw large images from a copy sized to the screen.

On every draw, Matplotlib colours every pixel of an image's array and then
shrinks the result to the screen pixels it covers. For a 4k image shown a
few hundred pixels wide, or zoomed in on a corner, that is most of the
redraw. :func:`draw_at_screen_resolution` makes an image draw from only the
part in view, sampled (nearest pixel) down to twice the resolution it has
on screen; once zoomed in that far it draws the full-resolution pixels.

The choice is made at draw time from the actual output size, so window
resizes, zooming, panning and saving at a higher dpi all get the right
resolution. The image's array itself is untouched, so cursor values and
everything else that reads it still see every pixel.
"""

import types

import numpy as np
from matplotlib.transforms import Bbox, TransformedBbox

# Draw at least this many source pixels per screen pixel before reducing
OVERSAMPLE = 2


def draw_at_screen_resolution(image):
    """Make the ``AxesImage`` ``image`` draw from a screen-sized copy."""
    image.make_image = types.MethodType(_make_image, image)


def _sample(n_in, n_out):
    """Indices of the source pixels at the centres of ``n_out`` equal blocks."""
    return np.floor((np.arange(n_out) + 0.5) * n_in / n_out).astype(int)


def _make_image(self, renderer, magnification=1.0, unsampled=False):
    try:
        view = None if unsampled else _view_block(self, magnification)
    except Exception:
        view = None
    if view is None:
        return type(self).make_image(self, renderer, magnification, unsampled)
    shown, bbox, out_bbox = view
    if self.get_clip_on():
        clip = self.get_clip_box() or self.axes.bbox
    else:
        clip = self.axes.figure.bbox
    return self._make_image(
        shown, bbox, out_bbox, clip, magnification, unsampled=unsampled
    )


def _view_block(image, magnification):
    """``(array, bbox, out_bbox)`` to draw instead of the whole image, or None
    if the whole image at full resolution is what should be drawn."""
    A = image._A
    if image.origin != "lower" or A is None or A.ndim != 2:
        return None

    rows, cols = A.shape
    x1, x2, y1, y2 = image.get_extent()
    dx, dy = (x2 - x1) / cols, (y2 - y1) / rows
    if not (dx > 0 and dy > 0):
        return None

    # Source pixels in view, plus one on each side for partly visible ones
    vx0, vx1 = sorted(image.axes.viewLim.intervalx)
    vy0, vy1 = sorted(image.axes.viewLim.intervaly)
    j0 = max(0, int(np.floor((vx0 - x1) / dx)) - 1)
    j1 = min(cols, int(np.ceil((vx1 - x1) / dx)) + 1)
    i0 = max(0, int(np.floor((vy0 - y1) / dy)) - 1)
    i1 = min(rows, int(np.ceil((vy1 - y1) / dy)) + 1)
    if j0 >= j1 or i0 >= i1:
        return None

    bbox = Bbox([[x1 + j0 * dx, y1 + i0 * dy], [x1 + j1 * dx, y1 + i1 * dy]])
    out_bbox = TransformedBbox(bbox, image.get_transform())
    out_w = out_bbox.width * magnification
    out_h = out_bbox.height * magnification
    if not (out_w > 0 and out_h > 0):
        return None

    factor = int(min((j1 - j0) / out_w, (i1 - i0) / out_h) / OVERSAMPLE)
    if factor < 2 and (i0, i1, j0, j1) == (0, rows, 0, cols):
        return None

    shown = A[i0:i1, j0:j1]
    if factor >= 2:
        shown = shown[
            np.ix_(
                _sample(i1 - i0, -(-(i1 - i0) // factor)),
                _sample(j1 - j0, -(-(j1 - j0) // factor)),
            )
        ]
    return shown, bbox, out_bbox

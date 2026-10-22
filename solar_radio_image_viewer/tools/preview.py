"""
Fast redraw of the dashed preview lines that follow the mouse.
"""

from matplotlib.transforms import Bbox


def blit_preview(canvas, ax, line, background, previous):
    """Draw ``line`` over the saved ``background`` of ``ax`` and repaint
    only the area it covers now and covered last time (``previous``).

    Repainting the whole axes on every mouse move is what made the preview
    lag on large and HiDPI windows. Returns the area to pass as ``previous``
    on the next call.
    """
    canvas.restore_region(background)
    ax.draw_artist(line)
    pad = line.get_linewidth() * ax.figure.dpi / 72 + 2
    box = line.get_window_extent(canvas.get_renderer()).padded(pad)
    area = Bbox.union([box, previous]) if previous is not None else box
    area = Bbox.intersection(area, ax.bbox)
    if area is not None:
        canvas.blit(area)
    return box

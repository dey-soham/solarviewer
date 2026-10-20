"""
Small geometry helpers shared by the interactive tools.
"""


def snap_to_axis(x1, y1, x2, y2):
    """Snap end point to horizontal or vertical based on which is closer"""
    dx = abs(x2 - x1)
    dy = abs(y2 - y1)
    if dx > dy:
        # Snap to horizontal
        return x2, y1
    else:
        # Snap to vertical
        return x1, y2

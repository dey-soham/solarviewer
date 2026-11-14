"""
Small geometry helpers shared by the interactive tools: snapping, boxes that
are turned and resized by their handles (the regions, the shape
annotations), and how near a point is to a line.
"""

import math

# The handles of a box: which of its sides each moves, (x side, y side) with
# 0 the low side, 1 the high side and None neither; the corners first
HANDLES = ((0, 0), (1, 0), (1, 1), (0, 1), (None, 0), (1, None), (None, 1), (0, None))
# The steps Shift turns a box, or points a line, by (degrees)
TURN_STEP = 15


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


def snapped_end(x0, y0, x, y, step=TURN_STEP):
    """(x, y) taken to the nearest direction from (x0, y0) that is a
    multiple of ``step`` degrees, as far away."""
    length = math.hypot(x - x0, y - y0)
    if not length:
        return x, y
    way = math.radians(round(math.degrees(math.atan2(y - y0, x - x0)) / step) * step)
    return x0 + length * math.cos(way), y0 + length * math.sin(way)


def even_box(x0, y0, x, y):
    """The box from (x0, y0) towards (x, y) with both sides as long as the
    longer way: a square, or the box of a circle."""
    side = max(abs(x - x0), abs(y - y0))
    return (x0, y0, x0 + math.copysign(side, x - x0), y0 + math.copysign(side, y - y0))


def turned(points, centre, angle):
    """``points`` turned ``angle`` degrees anticlockwise about ``centre``."""
    if not angle:
        return [(float(x), float(y)) for x, y in points]
    t = math.radians(angle)
    c, s = math.cos(t), math.sin(t)
    cx, cy = centre
    return [(cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c) for x, y in points]


def box_outline(corners, angle=0.0, round_=False, points=96):
    """The outline of the rectangle, or (``round_``) of the ellipse, in the
    box ``corners`` (x1, y1, x2, y2) turned by ``angle`` about its centre:
    its points in order, not closed."""
    x1, y1, x2, y2 = corners
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    if round_:
        a, b = abs(x2 - x1) / 2, abs(y2 - y1) / 2
        outline = [
            (cx + a * math.cos(2 * math.pi * i / points), cy + b * math.sin(2 * math.pi * i / points))
            for i in range(points)
        ]
    else:
        outline = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    return turned(outline, (cx, cy), angle)


def handle_points(corners, angle=0.0):
    """Where the handles of the box ``corners`` turned by ``angle`` are, in
    the order of HANDLES."""
    x1, y1, x2, y2 = corners
    xs, ys = (x1, x2, (x1 + x2) / 2), (y1, y2, (y1 + y2) / 2)
    points = [(xs[2 if hx is None else hx], ys[2 if hy is None else hy]) for hx, hy in HANDLES]
    return turned(points, (xs[2], ys[2]), angle)


def resized_box(corners, handle, x, y, angle=0.0, even=False):
    """The box ``corners`` (turned by ``angle``) with the sides that
    ``handle`` moves taken to (x, y) and the others where they are, low
    sides first. With ``even`` (and a corner handle) its sides stay the same
    length: as long as the mouse is far from the corner that stays, the
    further way."""
    x1, y1, x2, y2 = corners
    centre = ((x1 + x2) / 2, (y1 + y2) / 2)
    ((x, y),) = turned([(x, y)], centre, -angle)  # (into the box's own frame)
    xs, ys = [x1, x2], [y1, y2]
    hx, hy = HANDLES[handle]
    if even and hx is not None and hy is not None:
        fixed_x, fixed_y = xs[1 - hx], ys[1 - hy]
        _x0, _y0, x, y = even_box(fixed_x, fixed_y, x, y)
    if hx is not None:
        xs[hx] = x
    if hy is not None:
        ys[hy] = y
    box = (min(xs), min(ys), max(xs), max(ys))
    if not angle:
        return box
    # The box's new centre, back in the image, turned about the old one
    ((cx, cy),) = turned([((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)], centre, angle)
    half_w, half_h = (box[2] - box[0]) / 2, (box[3] - box[1]) / 2
    return (cx - half_w, cy - half_h, cx + half_w, cy + half_h)


def turned_angle(corners, angle, start, now, snap=False):
    """The angle of the box ``corners`` at ``angle`` once its round handle
    is dragged from ``start`` to ``now``: in TURN_STEP steps with ``snap``;
    in (-180, 180]."""
    cx, cy = (corners[0] + corners[2]) / 2, (corners[1] + corners[3]) / 2
    angle = angle + math.degrees(
        math.atan2(now[1] - cy, now[0] - cx) - math.atan2(start[1] - cy, start[0] - cx)
    )
    if snap:
        angle = round(angle / TURN_STEP) * TURN_STEP
    angle = -((-angle + 180) % 360 - 180)
    return 0.0 if abs(angle) < 1e-9 else angle


# How near a point is to a line: for picking with the mouse, on points
# already in pixels of the screen

def segment_distance(point, start, end):
    """How far ``point`` is from the segment ``start``–``end``."""
    px, py = point
    (ax, ay), (bx, by) = start, end
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if not length2 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def outline_distance(point, outline):
    """How far ``point`` is from the closed line through ``outline``."""
    return min(
        segment_distance(point, outline[i], outline[(i + 1) % len(outline)]) for i in range(len(outline))
    )


def inside(point, outline):
    """Whether ``point`` is inside the closed line through ``outline``."""
    x, y = point
    within = False
    for i in range(len(outline)):
        (x1, y1), (x2, y2) = outline[i], outline[(i + 1) % len(outline)]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            within = not within
    return within

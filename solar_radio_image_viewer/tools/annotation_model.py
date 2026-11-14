"""
The annotations of an image (text, arrows and shapes put on it for show):
what they are and how one is made, sized and moved. No Qt and no Matplotlib
here.

An annotation is a dictionary, placed in pixels of the image, kept in one of
three lists of its pane:

- ``text_annotations``: ``x``, ``y`` (where its first letter stands),
  ``text``, ``color``, ``fontsize``, ``fontweight``, ``fontstyle``,
  ``background`` (None for none), ``alpha``;
- ``arrow_annotations``: ``x1``, ``y1`` (its tail), ``x2``, ``y2`` (its
  head), ``color``, ``linewidth``, ``head_width``, ``alpha``;
- ``shape_annotations``: ``type`` (circle, ellipse, square or rectangle),
  ``center_x``, ``center_y``, ``width``, ``height``, ``unit`` (of the two
  sizes: pixels or arcsec), ``angle`` (degrees anticlockwise), ``color``,
  ``linestyle``, ``linewidth``, ``alpha``, ``fill``, ``facecolor``,
  ``label``.

They are drawn by ``shape_annotations.py``, carried to where they lie on
the sky when the image changes by ``core/rendering.py``, and copied into
videos by ``video/annotations.py``.
"""

import copy

from .geometry import box_outline

TEXT, ARROW, SHAPE = "text", "arrow", "shape"
# The pane's list each kind is kept in. They are drawn in this order, the
# arrows on top
LISTS = {SHAPE: "shape_annotations", TEXT: "text_annotations", ARROW: "arrow_annotations"}

# What the Annotate tool draws: its name -> (kind, shape type)
TOOLS = {
    "text": (TEXT, None),
    "arrow": (ARROW, None),
    "rectangle": (SHAPE, "rectangle"),
    "ellipse": (SHAPE, "ellipse"),
}
TOOL_NAMES = {"text": "Text", "arrow": "Arrow", "rectangle": "Rectangle", "ellipse": "Ellipse"}

COLORS = ["yellow", "white", "red", "cyan", "lime", "magenta", "orange", "blue", "black"]
LINE_STYLES = [("Solid", "-"), ("Dashed", "--"), ("Dotted", ":"), ("Dash-dot", "-.")]
SHAPE_TYPES = ["circle", "ellipse", "square", "rectangle"]
# The shapes with both sides always the same, and those that can be turned
EVEN = ("circle", "square")
TURNS = ("ellipse", "rectangle")
SOLAR_RADII = (1, 1.5, 2, 2.5, 3, 5, 10)

# How a new annotation looks until the user chooses otherwise
DEFAULT_COLOR = "yellow"
DEFAULTS = {
    TEXT: {
        "color": DEFAULT_COLOR,
        "fontsize": 12,
        "fontweight": "normal",
        "fontstyle": "normal",
        "background": None,
        "alpha": 1.0,
    },
    ARROW: {"color": DEFAULT_COLOR, "linewidth": 2.0, "head_width": 14, "alpha": 1.0},
    SHAPE: {
        "unit": "pixels",
        "color": DEFAULT_COLOR,
        "linestyle": "-",
        "linewidth": 1.5,
        "alpha": 0.9,
        "fill": False,
        "facecolor": "none",
        "angle": 0.0,
        "label": "",
    },
}
# What of an annotation is its look (the rest is where and what it is):
# the next one drawn takes the look of the last one changed. The colour is
# one for every kind
STYLE_KEYS = {
    TEXT: ("fontsize", "fontweight", "fontstyle", "background"),
    ARROW: ("linewidth", "head_width"),
    SHAPE: ("linestyle", "linewidth", "alpha", "fill", "facecolor"),
}


def describe(kind, annotation):
    """An annotation in a few words: for the status bar."""
    if kind == TEXT:
        words = str(annotation.get("text", "")).strip()
        return f"Text “{words if len(words) <= 24 else words[:23] + '…'}”"
    if kind == ARROW:
        return "Arrow"
    return str(annotation.get("type", "shape")).capitalize()


def style_of(kind, annotation):
    """The look of ``annotation`` (without its colour), for the next one of
    its kind."""
    return {key: annotation[key] for key in STYLE_KEYS[kind] if key in annotation}


def new_text(x, y, text, style=None):
    return {"x": float(x), "y": float(y), "text": text, **DEFAULTS[TEXT], **(style or {})}


def new_arrow(x1, y1, x2, y2, style=None):
    return {
        "x1": float(x1),
        "y1": float(y1),
        "x2": float(x2),
        "y2": float(y2),
        **DEFAULTS[ARROW],
        **(style or {}),
    }


def new_shape(shape_type, corners, style=None):
    """A ``shape_type`` in the box ``corners`` (x1, y1, x2, y2), in pixels."""
    x1, y1, x2, y2 = corners
    shape = {
        "type": shape_type,
        "center_x": (x1 + x2) / 2,
        "center_y": (y1 + y2) / 2,
        "width": abs(x2 - x1),
        "height": abs(y2 - y1),
        **DEFAULTS[SHAPE],
        **(style or {}),
    }
    shape["unit"] = "pixels"
    return shape


def snapshot(lists):
    """A copy of a pane's annotations ({kind: list}), to go back to."""
    return {kind: copy.deepcopy(items) for kind, items in lists.items()}


def shape_angle(shape):
    """How far a shape is turned: a circle or a square never is."""
    return float(shape.get("angle", 0.0) or 0.0) if shape.get("type") in TURNS else 0.0


def shape_size(shape, pixels_per_arcsec=1.0):
    """A shape's width and height in pixels of the image (its sizes may be
    in arcsec, each ``pixels_per_arcsec`` pixels)."""
    width = float(shape["width"])
    height = width if shape.get("type") in EVEN else float(shape.get("height", width))
    if shape.get("unit", "pixels") == "arcsec":
        width, height = width * pixels_per_arcsec, height * pixels_per_arcsec
    return width, height


def shape_box(shape, pixels_per_arcsec=1.0):
    """The box a shape fills, (x1, y1, x2, y2) in pixels, before it is turned."""
    width, height = shape_size(shape, pixels_per_arcsec)
    cx, cy = float(shape["center_x"]), float(shape["center_y"])
    return (cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2)


def set_shape_box(shape, corners, angle=None, pixels_per_arcsec=1.0):
    """Put ``shape`` in the box ``corners`` (and turn it to ``angle``, if
    given), its sizes kept in its own unit."""
    x1, y1, x2, y2 = corners
    width, height = abs(x2 - x1), abs(y2 - y1)
    if shape.get("type") in EVEN:
        width = height = max(width, height)
    if shape.get("unit", "pixels") == "arcsec" and pixels_per_arcsec:
        width, height = width / pixels_per_arcsec, height / pixels_per_arcsec
    shape["center_x"], shape["center_y"] = (x1 + x2) / 2, (y1 + y2) / 2
    shape["width"], shape["height"] = width, height
    if angle is not None and shape.get("type") in TURNS:
        shape["angle"] = float(angle)


def shape_outline(shape, pixels_per_arcsec=1.0):
    """A shape's outline as points (pixels of the image), not closed."""
    return box_outline(
        shape_box(shape, pixels_per_arcsec), shape_angle(shape), shape.get("type") in ("circle", "ellipse")
    )


def moved(kind, annotation, dx, dy):
    """``annotation`` moved by (dx, dy), in place."""
    keys = {
        TEXT: (("x", "y"),),
        ARROW: (("x1", "y1"), ("x2", "y2")),
        SHAPE: (("center_x", "center_y"),),
    }[kind]
    for kx, ky in keys:
        annotation[kx] = float(annotation[kx]) + dx
        annotation[ky] = float(annotation[ky]) + dy

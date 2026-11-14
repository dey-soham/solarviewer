"""
How the annotations of an image are drawn: its shapes (circle, ellipse,
square, rectangle), text and arrows, and the circles at solar radii.

What an annotation is is in ``tools/annotation_model.py``; the tool that
makes and changes them is in ``tools/annotations.py``.
"""

import logging
import math

from matplotlib.patches import Circle, Ellipse, Rectangle

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

def pixel_scales_deg(csys):
    """Degrees per pixel along x and y of the CASA coordinate system ``csys``,
    by the axes' own units (radians on the sky, arcsec on linear solar axes)."""
    from astropy import units as u

    increments = csys.increment()["numeric"][0:2]
    units = csys.units()[0:2]
    return [abs(float(v) * u.Unit(unit).to(u.deg)) for v, unit in zip(increments, units)]


def world_degrees(csys, x, y):
    """The world position in degrees of pixel (x, y) of ``csys``, by the
    axes' own units and the image's projection."""
    from astropy import units as u

    world = csys.toworld([x, y] + [0] * (csys.naxes() - 2))["numeric"][0:2]
    units = csys.units()[0:2]
    return [float(v) * u.Unit(unit).to(u.deg) for v, unit in zip(world, units)]


def _get_pixel_scale_deg(tab):
    """Return the pixel scale in degrees from the tab's WCS, or None."""
    if not tab.current_wcs:
        return None
    try:
        return pixel_scales_deg(tab.current_wcs)[0]
    except Exception:
        return None


def _arcsec_to_pixels(arcsec, tab):
    """Convert an arcsec value to pixels using the tab's WCS."""
    dx_deg = _get_pixel_scale_deg(tab)
    if dx_deg is None or dx_deg == 0:
        return arcsec  # fallback: treat as pixels
    return (arcsec / 3600.0) / dx_deg


def _mark(artist, kind, annotation):
    """``artist`` is the drawing of ``annotation`` (of ``kind``: shape,
    text or arrow): known as such, to be taken away or found again."""
    setattr(artist, f"_{kind}_annotation", True)
    artist._annotation = annotation
    return artist


def _annotation_artists(ax):
    return [
        artist
        for artist in list(ax.patches) + list(ax.texts)
        if any(getattr(artist, f"_{kind}_annotation", False) for kind in ("shape", "text", "arrow"))
    ]


def annotation_artists(ax, annotation):
    """The artists on ``ax`` that draw ``annotation``."""
    return [a for a in _annotation_artists(ax) if getattr(a, "_annotation", None) is annotation]


def _remove(ax, kind):
    for artist in _annotation_artists(ax):
        if getattr(artist, f"_{kind}_annotation", False):
            artist.remove()


def draw_shape_annotations(tab, ax):
    """Draw all shape annotations stored in *tab* onto matplotlib *ax*.

    Call this during every plot refresh. Old patches are removed first.
    """
    _remove(ax, "shape")
    for shape in getattr(tab, "shape_annotations", []):
        try:
            draw_shape(tab, ax, shape)
        except Exception as e:
            logger.warning(f"Failed to draw shape annotation: {e}")


def draw_text_annotations(tab, ax):
    """Draw all tracked text annotations onto *ax*."""
    _remove(ax, "text")
    for a in getattr(tab, "text_annotations", []):
        try:
            draw_text(ax, a)
        except Exception as e:
            logger.warning(f"Failed to draw text annotation: {e}")


def draw_arrow_annotations(tab, ax):
    """Draw all tracked arrow annotations onto *ax*."""
    _remove(ax, "arrow")
    for a in getattr(tab, "arrow_annotations", []):
        try:
            draw_arrow(ax, a)
        except Exception as e:
            logger.warning(f"Failed to draw arrow annotation: {e}")


def draw_annotations(tab, ax):
    """Every annotation of ``tab`` on ``ax``, in place of those drawn before."""
    draw_shape_annotations(tab, ax)
    draw_text_annotations(tab, ax)
    draw_arrow_annotations(tab, ax)


def redraw_annotation(tab, ax, kind, annotation):
    """One annotation (``kind``: shape, text or arrow) drawn again as it is
    now, in place of its drawing before. Returns its new artists."""
    for artist in annotation_artists(ax, annotation):
        artist.remove()
    if kind == "shape":
        return draw_shape(tab, ax, annotation)
    return draw_text(ax, annotation) if kind == "text" else draw_arrow(ax, annotation)


def draw_text(ax, a):
    """Draw the text annotation ``a``; returns its artists."""
    bbox_props = None
    if a.get("background"):
        bbox_props = dict(boxstyle="round,pad=0.3", facecolor=a["background"], alpha=0.7)
    t = ax.text(
        a["x"], a["y"], a["text"],
        color=a.get("color", "yellow"),
        fontsize=a.get("fontsize", 12),
        fontweight=a.get("fontweight", "normal"),
        fontstyle=a.get("fontstyle", "normal"),
        bbox=bbox_props,
        alpha=a.get("alpha", 1.0),
    )
    return [_mark(t, "text", a)]


def draw_arrow(ax, a):
    """Draw the arrow annotation ``a`` (its head exactly at its end);
    returns its artists."""
    ann = ax.annotate(
        "",
        xy=(a["x2"], a["y2"]),
        xytext=(a["x1"], a["y1"]),
        arrowprops=dict(
            arrowstyle="-|>",
            color=a.get("color", "red"),
            lw=a.get("linewidth", 2.0),
            mutation_scale=a.get("head_width", 8),
            shrinkA=0,
            shrinkB=0,
        ),
        alpha=a.get("alpha", 1.0),
    )
    return [_mark(ann, "arrow", a)]


def draw_shape(tab, ax, s):
    """Draw a single shape dict *s* onto *ax*; returns its artists."""
    cx = s["center_x"]
    cy = s["center_y"]
    unit = s.get("unit", "pixels")
    color = s.get("color", "white")
    linestyle = s.get("linestyle", "-")
    linewidth = s.get("linewidth", 1.5)
    alpha = s.get("alpha", 0.8)
    fill = s.get("fill", False)
    facecolor = s.get("facecolor", "none")
    angle = s.get("angle", 0.0)
    label = s.get("label", "")
    shape_type = s["type"]

    w = s["width"]
    h = s.get("height", w)

    if unit == "arcsec":
        w = _arcsec_to_pixels(w, tab)
        h = _arcsec_to_pixels(h, tab)

    if shape_type == "circle":
        patch = Circle(
            (cx, cy),
            radius=w / 2.0,
            fill=fill,
            facecolor=facecolor if fill else "none",
            edgecolor=color,
            linestyle=linestyle,
            linewidth=linewidth,
            alpha=alpha,
        )
    elif shape_type == "ellipse":
        patch = Ellipse(
            (cx, cy),
            width=w,
            height=h,
            angle=angle,
            fill=fill,
            facecolor=facecolor if fill else "none",
            edgecolor=color,
            linestyle=linestyle,
            linewidth=linewidth,
            alpha=alpha,
        )
    elif shape_type == "square":
        patch = Rectangle(
            (cx - w / 2.0, cy - w / 2.0),
            w,
            w,
            angle=0,
            fill=fill,
            facecolor=facecolor if fill else "none",
            edgecolor=color,
            linestyle=linestyle,
            linewidth=linewidth,
            alpha=alpha,
        )
    elif shape_type == "rectangle":
        # Turned about its centre (a Rectangle turns about its corner)
        t = math.radians(angle)
        corner = (
            cx - (w / 2.0) * math.cos(t) + (h / 2.0) * math.sin(t),
            cy - (w / 2.0) * math.sin(t) - (h / 2.0) * math.cos(t),
        )
        patch = Rectangle(
            corner,
            w,
            h,
            angle=angle,
            fill=fill,
            facecolor=facecolor if fill else "none",
            edgecolor=color,
            linestyle=linestyle,
            linewidth=linewidth,
            alpha=alpha,
        )
    else:
        return []

    artists = [_mark(patch, "shape", s)]
    ax.add_patch(patch)

    # Optional label
    if label:
        txt = ax.text(
            cx,
            cy,
            f"  {label}",
            color=color,
            fontsize=9,
            alpha=alpha,
            verticalalignment="center",
        )
        artists.append(_mark(txt, "shape", s))
    return artists


# ---------------------------------------------------------------------------
# Solar radii presets
# ---------------------------------------------------------------------------

def _resolve_solar_center(tab):
    """Return (center_x, center_y) in pixel coords: the solar disk's centre."""
    if tab.solar_disk_center is None:
        if tab.current_image_data is None:
            return None
        tab._compute_solar_disk_center(tab.current_image_data.shape)
    return tab.solar_disk_center


def solar_radius_circle(tab, multiplier):
    """A dashed circle at *multiplier* × 1 R☉ centred on the solar disk, as
    a shape annotation; None without an image, or with the Sun somewhere
    this image's sky does not reach."""
    center = _resolve_solar_center(tab)
    if center is None or not all(math.isfinite(float(v)) for v in center):
        return None
    tab._say_if_disk_is_a_guess("Circle")

    # The Sun's size on the image's date, or 960 arcsec without it
    if getattr(tab, "solar_disk_auto_compute", True):
        diam_arcmin = getattr(tab, "solar_disk_diameter_arcmin", 32.0)
        radius_arcsec = (diam_arcmin / 2.0) * 60.0 * multiplier
    else:
        radius_arcsec = 960.0 * multiplier

    return {
        "type": "circle",
        "center_x": center[0],
        "center_y": center[1],
        "width": radius_arcsec * 2.0,
        "height": radius_arcsec * 2.0,
        "unit": "arcsec",
        "color": "cyan",
        "linestyle": "--",
        "linewidth": 1.2,
        "alpha": 0.7,
        "fill": False,
        "facecolor": "none",
        "angle": 0.0,
        "label": "",
    }

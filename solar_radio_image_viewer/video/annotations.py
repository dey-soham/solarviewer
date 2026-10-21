"""
The main window's annotations (shapes, text and arrows) on video frames.

When copied from the main window they are tied to the sky: positions become
coordinates (helioprojective or RA/Dec, through the main window image's
WCS) and sizes arcseconds, so each frame draws them where they belong in
its own image, whatever its grid, region or pointing. An image without sky
coordinates keeps pixel positions.

No Qt here (frames are drawn in worker processes).
"""

import logging

logger = logging.getLogger(__name__)


def annotations_from_viewer(tab):
    """The annotations of main window tab ``tab`` (None if it has none),
    in sky coordinates where its image has them."""
    shapes = list(getattr(tab, "shape_annotations", []) or [])
    texts = list(getattr(tab, "text_annotations", []) or [])
    arrows = list(getattr(tab, "arrow_annotations", []) or [])
    if not (shapes or texts or arrows):
        return None

    frame, wcs, arcsec_per_pixel = "pixel", None, None
    display_wcs = getattr(tab, "_cached_wcs_obj", None)
    if display_wcs is not None:
        try:
            from astropy.wcs.utils import proj_plane_pixel_scales

            from ..processing.contour_mapping import transformable_wcs

            wcs = transformable_wcs(display_wcs, getattr(tab, "_cached_fits_header", None))
            ctype = wcs.wcs.ctype[0].upper()
            kind = "hpc" if ctype.startswith("HPLN") else "radec" if ctype.startswith("RA") else None
            if kind:
                frame = kind
                arcsec_per_pixel = float(abs(proj_plane_pixel_scales(wcs)[0])) * 3600
        except Exception as e:
            logger.warning(f"Annotations kept in pixels: {e}")

    def to_world(x, y):
        """A pixel position as sky coordinates (degrees), if there are any."""
        if frame == "pixel":
            return float(x), float(y)
        lon, lat = wcs.wcs_pix2world([[x, y]], 0)[0]
        return float(lon), float(lat)

    def size(value, unit):
        """A shape size in arcsec (in pixels without sky coordinates)."""
        if frame == "pixel":
            return float(value)
        return float(value) if unit == "arcsec" else float(value) * arcsec_per_pixel

    converted = {"frame": frame, "shapes": [], "texts": [], "arrows": []}
    for s in shapes:
        width = s["width"]
        converted["shapes"].append(
            dict(
                {k: v for k, v in s.items() if k not in ("center_x", "center_y", "width", "height", "unit")},
                center=to_world(s["center_x"], s["center_y"]),
                width=size(width, s.get("unit", "pixels")),
                height=size(s.get("height", width), s.get("unit", "pixels")),
            )
        )
    for t in texts:
        converted["texts"].append(
            dict({k: v for k, v in t.items() if k not in ("x", "y")}, position=to_world(t["x"], t["y"]))
        )
    for a in arrows:
        converted["arrows"].append(
            dict(
                {k: v for k, v in a.items() if k not in ("x1", "y1", "x2", "y2")},
                start=to_world(a["x1"], a["y1"]),
                end=to_world(a["x2"], a["y2"]),
            )
        )
    return converted


def annotation_counts(annotations):
    """E.g. "2 shapes, 1 text, 1 arrow"."""
    parts = []
    for key, one in (("shapes", "shape"), ("texts", "text"), ("arrows", "arrow")):
        n = len((annotations or {}).get(key, []))
        if n:
            parts.append(f"{n} {one}{'s' if n > 1 else ''}")
    return ", ".join(parts) or "none"


def draw_annotations(ax, annotations, header, region_origin=(0, 0), report=None):
    """Draw ``annotations`` (see annotations_from_viewer) on frame axes
    ``ax``, whose image has ``header`` (``region_origin``: the lower left
    corner of the region shown)."""
    from matplotlib.patches import Circle, Ellipse, Rectangle

    frame = annotations.get("frame", "pixel")
    x0, y0 = region_origin
    if frame == "pixel":
        def to_pixel(point):
            return point[0] - x0, point[1] - y0

        pixels_per_unit = 1.0
    else:
        from astropy.wcs import WCS
        from astropy.wcs.utils import proj_plane_pixel_scales

        from .utils import coordinate_type

        own = coordinate_type(header or {})
        if own != frame:
            if report:
                names = {"hpc": "helioprojective", "radec": "RA/Dec"}
                report(
                    f"Annotations not drawn: made on a {names[frame]} image, the "
                    f"frame is {names.get(own, 'without sky coordinates')}"
                )
            return
        wcs = WCS(header, naxis=2)
        pixels_per_unit = 1.0 / (float(abs(proj_plane_pixel_scales(wcs.celestial)[0])) * 3600)

        def to_pixel(point):
            x, y = wcs.wcs_world2pix([list(point)], 0)[0]
            return x - x0, y - y0

    # Annotations partly outside the image must not widen the view
    limits = ax.get_xlim(), ax.get_ylim()
    for s in annotations.get("shapes", []):
        try:
            cx, cy = to_pixel(s["center"])
            w, h = s["width"] * pixels_per_unit, s.get("height", s["width"]) * pixels_per_unit
            style = dict(
                fill=s.get("fill", False),
                facecolor=s.get("facecolor", "none") if s.get("fill") else "none",
                edgecolor=s.get("color", "white"),
                linestyle=s.get("linestyle", "-"),
                linewidth=s.get("linewidth", 1.5),
                alpha=s.get("alpha", 0.8),
            )
            kind = s["type"]
            if kind == "circle":
                patch = Circle((cx, cy), radius=w / 2.0, **style)
            elif kind == "ellipse":
                patch = Ellipse((cx, cy), width=w, height=h, angle=s.get("angle", 0.0), **style)
            elif kind == "square":
                patch = Rectangle((cx - w / 2.0, cy - w / 2.0), w, w, **style)
            elif kind == "rectangle":
                patch = Rectangle(
                    (cx - w / 2.0, cy - h / 2.0), w, h, angle=s.get("angle", 0.0), **style
                )
            else:
                continue
            ax.add_patch(patch)
            if s.get("label"):
                ax.text(
                    cx, cy, f"  {s['label']}", color=style["edgecolor"], fontsize=9,
                    alpha=style["alpha"], verticalalignment="center", clip_on=True,
                )
        except Exception as e:
            logger.warning(f"Shape annotation not drawn: {e}")
    for t in annotations.get("texts", []):
        try:
            x, y = to_pixel(t["position"])
            ax.text(
                x, y, t["text"],
                color=t.get("color", "yellow"),
                fontsize=t.get("fontsize", 12),
                fontweight=t.get("fontweight", "normal"),
                fontstyle=t.get("fontstyle", "normal"),
                bbox=(
                    dict(boxstyle="round,pad=0.3", facecolor=t["background"], alpha=0.7)
                    if t.get("background")
                    else None
                ),
                alpha=t.get("alpha", 1.0),
                clip_on=True,
            )
        except Exception as e:
            logger.warning(f"Text annotation not drawn: {e}")
    for a in annotations.get("arrows", []):
        try:
            ax.annotate(
                "",
                xy=to_pixel(a["end"]),
                xytext=to_pixel(a["start"]),
                arrowprops=dict(
                    arrowstyle="-|>",
                    color=a.get("color", "red"),
                    lw=a.get("linewidth", 2.0),
                    mutation_scale=a.get("head_width", 8),
                ),
                alpha=a.get("alpha", 1.0),
            )
        except Exception as e:
            logger.warning(f"Arrow annotation not drawn: {e}")
    ax.set_xlim(*limits[0])
    ax.set_ylim(*limits[1])


def has_annotations(annotations):
    return bool(annotations) and any(annotations.get(k) for k in ("shapes", "texts", "arrows"))


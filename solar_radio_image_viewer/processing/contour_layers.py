"""
Contour layers: the settings of one contour set each (source, Stokes,
levels, style), their names, levels and legend entries.

Shared by the main viewer and video frames, so this module must not import
Qt.
"""

import logging
import os
import uuid

import numpy as np

from .image_meta import casa_info, fits_header

logger = logging.getLogger(__name__)

MAX_CONTOUR_LAYERS = 10

# Default colours of new layers, in order: the Okabe-Ito palette, which stays
# distinguishable with the common kinds of colour blindness, plus white, grey
# and black; brightest first for dark solar images
CONTOUR_LAYER_COLORS = [
    "white",
    "#E69F00",
    "#56B4E9",
    "#F0E442",
    "#D55E00",
    "#009E73",
    "#CC79A7",
    "#0072B2",
    "#999999",
    "black",
]
CONTOUR_COLOR_NAMES = {
    "white": "White",
    "#E69F00": "Orange",
    "#56B4E9": "Sky blue",
    "#F0E442": "Yellow",
    "#D55E00": "Vermillion",
    "#009E73": "Bluish green",
    "#CC79A7": "Reddish purple",
    "#0072B2": "Blue",
    "#999999": "Grey",
    "black": "Black",
}

# Keys of a layer that hold loaded data rather than settings
_LAYER_RUNTIME_KEYS = ("contour_data", "contour_wcs", "loaded_path", "dim_w", "dim_h")

# Settings whose change means a layer's data must be loaded again
_LAYER_LOAD_KEYS = (
    "source",
    "external_image",
    "stokes",
    "threshold",
    "use_default_rms_region",
    "rms_box",
)


def new_contour_layer(index=0, **settings):
    """A contour layer: one contour set (source, Stokes, levels, style).

    ``index`` picks its default colour. Runtime keys (``contour_data``,
    ``contour_wcs``, ...) hold the loaded data and are filled in by
    :meth:`ContoursMixin.load_contour_data`.
    """
    color = CONTOUR_LAYER_COLORS[index % len(CONTOUR_LAYER_COLORS)]
    layer = {
        "id": uuid.uuid4().hex,
        "enabled": True,
        "label": "",  # empty: named after its source and Stokes
        "source": "same",
        "external_image": "",
        "stokes": "I",
        "threshold": 5.0,
        "pos_levels": [0.1, 0.3, 0.5, 0.7, 0.9],
        "neg_levels": [0.1, 0.3, 0.5, 0.7, 0.9],
        "level_type": "fraction",
        "pos_color": color,
        "neg_color": color,
        "pos_linewidth": 1.0,
        "neg_linewidth": 1.0,
        "pos_linestyle": "-",
        "neg_linestyle": "--",
        "show_labels": False,
        "use_default_rms_region": True,
        "rms_box": (0, 200, 0, 130),
        "contour_data": None,
        "contour_wcs": None,
        "loaded_path": None,
        "dim_h": 0,
        "dim_w": 0,
    }
    layer.update(settings)
    return layer


def default_contour_options():
    """Settings shared by all contour layers."""
    return {
        "show_legend": True,
        "legend_loc": "upper right",
        "legend_fontsize": 8,
        "legend_show_negative": False,  # entries for negative contours too
        "show_full_extent": True,  # If False, only contours within 1.5x the base image
        "downsample": True,
    }


def contour_layer_name(layer, image_name="Current image"):
    """The layer's label, or one made from its source and Stokes (a layer on
    the image shown itself is called ``image_name``)."""
    if layer.get("label"):
        return layer["label"]
    if layer.get("source") == "external" and layer.get("external_image"):
        source = _short_source_name(layer["external_image"])
    elif layer.get("source") == "series" and (
        layer.get("series_files") or layer.get("series_dir")
    ):
        series = layer.get("series_files") or []
        if series:
            source = _short_source_name(series[0])
        else:  # not scanned yet: the folder's name
            source = os.path.basename(os.path.normpath(layer["series_dir"])) or "Series"
    else:
        source = image_name
    return f"{source} · {layer.get('stokes', 'I')}"


def _short_source_name(path):
    """The telescope that took an image (from its header), else its file name."""
    try:
        if os.path.isdir(path):
            telescope = casa_info(path).csys_record.get("telescope", "")
        else:
            telescope = fits_header(path).get("TELESCOP", "")
        telescope = str(telescope).strip()
        if telescope and telescope.upper() not in ("UNKNOWN", "NONE"):
            return telescope
    except Exception:
        pass
    name = os.path.basename(os.path.normpath(path))
    for suffix in (".fits", ".fts", ".fit", ".image"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name if len(name) <= 24 else name[:23] + "…"


def contour_layer_settings(layer):
    """Copy of a layer without its loaded data."""
    return {k: v for k, v in layer.items() if k not in _LAYER_RUNTIME_KEYS}


def merge_contour_layers(old_layers, new_layers):
    """``new_layers`` (settings from the dialog), keeping the loaded data of
    layers whose source settings did not change."""
    old_by_id = {layer.get("id"): layer for layer in old_layers}
    def same_source(old, new):
        keys = _LAYER_LOAD_KEYS
        if old.get("use_default_rms_region", True) and new.get(
            "use_default_rms_region", True
        ):
            # The box is then worked out when loading, not set by the user
            keys = [k for k in keys if k != "rms_box"]
        return all(old.get(k) == new.get(k) for k in keys)

    merged = []
    for layer in new_layers:
        layer = dict(layer)
        old = old_by_id.get(layer.get("id"))
        if old is not None and same_source(old, layer):
            for key in _LAYER_RUNTIME_KEYS:
                layer[key] = old.get(key)
        else:
            layer.update({k: None for k in ("contour_data", "contour_wcs", "loaded_path")})
            layer.setdefault("dim_h", 0)
            layer.setdefault("dim_w", 0)
        merged.append(layer)
    return merged


def box_rms(contour_data, rms_box):
    """RMS of ``contour_data`` in ``rms_box`` (the std of all of it if the
    box can't be used): the noise that sigma levels are multiples of."""
    try:
        region = contour_data[rms_box[0] : rms_box[1], rms_box[2] : rms_box[3]]
        return np.sqrt(np.nanmean(region**2))
    except Exception as e:
        logger.warning(f"Could not calculate RMS from box, using std: {e}")
        return np.nanstd(contour_data)


def contour_levels(layer, contour_data, abs_max=None, rms=None):
    """(positive, negative) contour levels of ``layer`` for ``contour_data``:
    fractions of the peak, multiples of the RMS in its RMS box, or absolute.
    ``abs_max`` / ``rms`` give the peak / RMS to use instead of the data's.
    """
    if abs_max is None:
        abs_max = np.nanmax(np.abs(contour_data))
    if layer["level_type"] == "fraction":
        vmax = np.nanmax(contour_data)
        vmin = np.nanmin(contour_data)
        if vmax > 0:
            pos_levels = sorted([level * abs_max for level in layer["pos_levels"]])
        else:
            pos_levels = []

        if vmin < 0:
            neg_levels = sorted([level * abs_max for level in layer["neg_levels"]])
            neg_levels = [-level for level in reversed(neg_levels)]
        else:
            neg_levels = []

    elif layer["level_type"] == "sigma":
        # For sigma levels, calculate RMS from the RMS box region of the contour data
        # This gives a proper noise estimate without source contamination
        if rms is None:
            rms = box_rms(contour_data, layer.get("rms_box", (0, 200, 0, 130)))

        # Positive levels: level * rms (e.g., 3σ, 6σ, 9σ...)
        pos_levels = sorted([level * rms for level in layer["pos_levels"]])
        # Negative levels: -level * rms (e.g., -3σ, -6σ, -9σ...)
        # Must be in increasing order for matplotlib (-30, -20, -10)
        neg_levels = sorted([-level * rms for level in layer["neg_levels"]])

    else:
        pos_levels = sorted(layer["pos_levels"])
        neg_levels = sorted([-level for level in reversed(layer["neg_levels"])])
    return pos_levels, neg_levels


def legend_handles(layer, drawn, image_name="Current image"):
    """Legend entries for a layer's drawn positive/negative contours."""
    from matplotlib.lines import Line2D

    name = contour_layer_name(layer, image_name)
    handles = []
    for sign in drawn:
        handles.append(
            Line2D(
                [],
                [],
                color=layer.get(f"{sign}_color", "white"),
                linewidth=layer.get(f"{sign}_linewidth", 1.0),
                linestyle=layer.get(f"{sign}_linestyle", "-" if sign == "pos" else "--"),
                label=name if sign == "pos" else f"{name} (−)",
            )
        )
    return handles

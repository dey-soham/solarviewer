"""
Rendering the image tab: plot_image and its drawing steps.
"""

import logging
import os
import weakref

import matplotlib
import re

import numpy as np
from matplotlib.axes import Axes
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import LogNorm, Normalize
from matplotlib.patches import Ellipse

from ..processing.norms import AsinhNorm, HistEqNorm, PowerNorm, SqrtNorm, ZScaleNorm
from ..processing.image_meta import casa_info, casa_obs_time, fits_header
from ..processing.colormaps import ensure_solar_colormaps
from ..processing.contour_layers import contour_layer_settings
from .canvas import READOUT_HINT
from .display_resolution import draw_at_screen_resolution
from .presets import display_range
from ..processing.sky_view import (
    PASSING,
    KeptView,
    limits_for,
    marks_map,
    moved_boxes,
    snap,
    wcs_kind,
)
from ..processing.fits_io import is_fits, readable_image_path
from ..ui.background import Cancelled, run_in_background
from ..ui.busy import busy

logger = logging.getLogger(__name__)


from ..processing.plot_style import DEFAULT_PLOT_SETTINGS

_PAD_KEYS = ("pad_left", "pad_right", "pad_top", "pad_bottom")


def _spread_ticks(norm, vmin, vmax, most):
    """Up to ``most`` round values between ``vmin`` and ``vmax``, no two
    closer along a colorbar of ``norm`` than a fair share of it."""
    from matplotlib.ticker import MaxNLocator

    low, high = min(vmin, vmax), max(vmin, vmax)
    if not (high > low):
        return [low]
    candidates = [t for t in MaxNLocator(nbins=most * 2).tick_values(low, high) if low <= t <= high]
    kept, last = [], None
    for tick in candidates:
        try:
            place = float(norm(tick))
        except Exception:
            place = (tick - low) / (high - low)
        if last is None or place - last >= 1.0 / most:
            kept.append(tick)
            last = place
    return kept or [low, high]


def _plain_number(value):
    """``value`` in few plain figures: 6119, 552.7, -6.66, 0, 1.5e+06."""
    if value == 0:
        return "0"
    if 0.01 <= abs(value) < 1e5:
        text = f"{value:.4g}"
        return text if "e" not in text else f"{value:.0f}"
    return f"{value:.2g}"


_WCS_KEYS = re.compile(
    r"^(CTYPE|CRPIX|CRVAL|CDELT|CUNIT|CROTA|LONPOLE|LATPOLE|PC\d_\d|CD\d_\d)"
)


def _pixel_rotation(header):
    """The 2x2 PC matrix of a FITS ``header``'s first two axes (how the
    pixel grid is turned against the sky, as LASCO's is by its roll), or
    None when it is the identity or cannot be read. The reference pixel,
    value and increment say nothing of it, so a display WCS built from
    them alone needs it too. ``header`` may be a plain dict: only the
    coordinate keywords are read, so odd values elsewhere do no harm."""
    import warnings

    try:
        from astropy.io import fits
        from astropy.wcs import WCS

        keys = fits.Header()
        for key, value in dict(header).items():
            if _WCS_KEYS.match(str(key)) and isinstance(value, (int, float, str)):
                keys[key] = value
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            pc = np.asarray(WCS(keys).sub([1, 2]).wcs.get_pc(), dtype=float)
    except Exception:
        return None
    if pc.shape != (2, 2) or not np.all(np.isfinite(pc)):
        return None
    if np.allclose(pc, np.eye(2), atol=1e-9):
        return None
    return pc


def _strip_above(ax, gap_px, height_px):
    """An axes locator: a strip ``height_px`` tall and the width of ``ax``,
    ``gap_px`` over it as drawn (the box its image settles into, once
    its aspect is applied), in the figure's coordinates."""
    from matplotlib.transforms import Bbox

    def locate(_axes, _renderer):
        ax.apply_aspect()
        box = ax.bbox
        strip = Bbox.from_extents(
            box.x0, box.y1 + gap_px, box.x1, box.y1 + gap_px + height_px
        )
        return ax.figure.transSubfigure.inverted().transform_bbox(strip)

    return locate

# What the pill and Cancel dialog say of a filter being worked out
RADIAL_LABELS = {
    "ratio": "Dividing by the radial median…",
    "nrgf": "Working out the radial filter (NRGF)…",
    "fnrgf": "Working out the radial filter (FNRGF)…",
}

# The colorbar of a pane of a compare page: a strip over the image, its
# height and its gap from the image, in points
COMPACT_CB_HEIGHT_PT = 6
COMPACT_CB_GAP_PT = 5
COMPACT_CB_TICKS = 5  # at most this many values along it


class RenderingMixin:
    """Draws the current image: norm, WCS axes, title, colorbar, styling, beam, layout.

    plot_image() is the entry point; it reuses the existing image in place
    (_try_fast_redraw) when only the colormap/normalization changed.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``arrow_annotations``, ``beam_style``, ``canvas``, ``contour_layers``,
    ``current_image_data``, ``current_wcs``, ``draw_contours``,
    ``_draw_solar_disk``, ``figure``, ``grid_style``, ``imagename``,
    ``init_region_editor``, ``plot_settings``, ``psf``, ``shape_annotations``,
    ``show_beam_checkbox``, ``show_solar_disk_checkbox``,
    ``show_status_message``, ``solar_disk_style``, ``text_annotations``
    """

    def _get_render_state(self, tight_layout=False):
        """
        Capture the current structural state of the plot.
        Returns a dict of values that, if changed, require a full redraw.
        """
        state = {
            "data_id": (
                id(self.current_image_data)
                if self.current_image_data is not None
                else None
            ),
            "wcs_id": id(self.current_wcs) if self.current_wcs else None,
            "fits_flag": self._cached_fits_flag,
            "tight_layout": tight_layout,
            # Overlay states
            "show_beam": self.show_beam_checkbox.isChecked(),
            "show_grid": self.show_grid_checkbox.isChecked(),
            "show_solar_disk": self.show_solar_disk_checkbox.isChecked(),
            "show_contours": self.show_contours_checkbox.isChecked(),
            "compact": self.compact_plot,
            "pane_axes": self.pane_axes,
            "pane_colorbar": self.pane_colorbar,
            "radial_filter": self.radial_filter,
            "background_subtracted": self.background_subtracted,
        }

        # Settings snapshots (identity of loaded contour data tracked separately)
        state["contour_data_id"] = tuple(
            id(layer.get("contour_data")) for layer in self.contour_layers
        )
        state["contour_settings"] = str(
            [contour_layer_settings(layer) for layer in self.contour_layers]
        ) + str(self.contour_options)

        state["grid_settings"] = str(self.grid_style)
        state["beam_settings"] = str(self.beam_style)
        state["solar_disk_settings"] = str(self.solar_disk_style)
        state["plot_settings"] = str(self.plot_settings)
        state["shape_annotations"] = str(self.shape_annotations)
        state["text_annotations"] = str(self.text_annotations)
        state["arrow_annotations"] = str(self.arrow_annotations)

        return state

    # ------------------------------------------------------ radial filter
    @property
    def radial_filter(self):
        """How the drawn image is normalised radially: 'off', 'ratio',
        'nrgf' or 'fnrgf' (core/radial_filter.py). The data keep their
        values."""
        return getattr(self, "_radial_filter", "off")

    def _radial_centre(self):
        """The Sun's centre in the image's pixels (x, y): the reference
        pixel of the loaded coordinate system, else the middle."""
        data = self.current_image_data
        try:
            ref = self.current_wcs.referencepixel()["numeric"][0:2]
            cx, cy = float(ref[0]), float(ref[1])
            if 0 <= cx < data.shape[0] and 0 <= cy < data.shape[1]:
                return cx, cy
        except Exception:
            pass
        return (data.shape[0] - 1) / 2, (data.shape[1] - 1) / 2

    # The background plane a prepared coronagraph image carries (the F
    # corona and stray light under it, LASCO_CALIBRATION.md) can be taken
    # away from what is drawn, leaving the K corona
    @property
    def background_subtracted(self):
        return bool(getattr(self, "_subtract_background", False))

    def has_background(self):
        """Whether the file carries a background plane (BKGEXT in its
        header names the extension)."""
        header = getattr(self, "current_header", None) or {}
        return self.current_image_data is not None and bool(str(header.get("BKGEXT", "") or "").strip())

    def _background_key(self):
        return (self.imagename, getattr(self, "_downsample", 1) or 1, self.current_image_data.shape)

    def _background_read(self):
        """Whether the background plane of the image is at hand."""
        cached = getattr(self, "_background", None)
        return cached is not None and cached[0] == self._background_key()

    def _read_background(self, run=lambda fn, *args: fn(*args)):
        """Read the background plane and keep it, ``run(fn, *args)`` being
        how the reader is called."""
        from ..processing.image_data import read_background

        key = self._background_key()
        try:
            plane = run(read_background, self.imagename, key[1])
        except Cancelled:
            raise
        except Exception as e:
            logger.warning(f"Background plane of {self.imagename} not read: {e}")
            plane = None
        if plane is not None and plane.shape != key[2]:
            logger.warning(f"Background plane {plane.shape} does not fit the image {key[2]}")
            plane = None
        self._background = (key, plane)

    def background_data(self):
        """The background plane sampled like the image, read once per
        file; None when there is none or it does not fit the image."""
        if not self.has_background():
            return None
        if not self._background_read():
            self._read_background()
        return self._background[1]

    def _unfiltered_display_data(self, subtract=None):
        """The image, or the image less its background (``subtract``, by
        default as chosen)."""
        data = self.current_image_data
        if subtract is None:
            subtract = self.background_subtracted
        if data is None or not subtract:
            return data
        plane = self.background_data()
        if plane is None:
            return data
        cached = getattr(self, "_subtracted", None)
        if cached is None or cached[0]() is not data or cached[1] is not plane:
            cached = self._subtracted = (weakref.ref(data), plane, (data - plane).astype(np.float32))
        return cached[2]

    def display_data(self):
        """The array drawn: the image, less its background when that is
        subtracted, radially filtered when a filter is on."""
        data = self._unfiltered_display_data()
        if data is None or self.radial_filter == "off":
            return data
        mode, centre = self.radial_filter, self._radial_centre()
        cache = self._radial_filter_cache()
        if cache.holds(data, mode, centre):
            return cache.get(data, mode, centre)
        # Not prepared (see prepare_display_data)
        with busy(RADIAL_LABELS[mode], self):
            return cache.get(data, mode, centre)

    def _radial_filter_cache(self):
        from .radial_filter import RadialFilterCache

        cache = getattr(self, "_radial_cache", None)
        if cache is None:
            cache = self._radial_cache = RadialFilterCache()
        return cache

    def prepare_display_data(self, radial=None, subtract=None):
        """What drawing with these choices (by default the current ones) needs
        and is slow, done in the worker process, where the window keeps
        drawing and it can be cancelled (raising Cancelled): reading the
        background plane and the radial filter, which took 11 s on a
        4096 x 4096 image.

        This runs the event loop, so it comes before drawing, from what the
        user did: never from a paint, a resize or a draw callback. There,
        display_data() does the same work where it stands."""
        radial = self.radial_filter if radial is None else radial
        subtract = self.background_subtracted if subtract is None else subtract
        self._preparing_display = True
        try:
            if subtract and self.has_background() and not self._background_read():
                name = os.path.basename(self.imagename.rstrip("/"))
                label = f"Reading the background of {name}…"
                self._read_background(
                    lambda fn, *args: run_in_background(self, label, fn, *args)
                )
            if radial == "off":
                return
            from ..processing.radial_filter import apply

            cache = self._radial_filter_cache()
            # (Again if the image was replaced meanwhile)
            while self.current_image_data is not None:
                data = self._unfiltered_display_data(subtract)
                centre = self._radial_centre()
                if cache.holds(data, radial, centre):
                    break
                filtered = run_in_background(
                    self, RADIAL_LABELS[radial], apply, data, radial, centre
                )
                cache.put(data, radial, centre, filtered)
        finally:
            self._preparing_display = False
            if getattr(self, "_plot_after_preparing", False):
                self._plot_after_preparing = False
                self.schedule_plot(getattr(self, "_plot_done", None))

    def keep_display_filters(self):
        """After the image changed: prepare the filters that are on for it.
        Cancelling or a failure turns them off (the image they were on has
        gone). Returns None, or then what to tell the user when the drawing
        has ended, as its own messages would cover it."""
        try:
            self.prepare_display_data()
            return None
        except Cancelled:
            why = "Filter cancelled"
        except Exception as e:
            logger.error(f"Display filter failed: {e}")
            why = f"Filter failed ({e})"
        self.set_background_subtraction(False, redraw=False)
        self.set_radial_filter("off", redraw=False)
        return f"{why}: it is off, and the image is drawn as it is"

    def value_unit(self):
        """The unit of the image's values: the file's for a Stokes plane,
        none for a ratio (Vfrac, Q/I...), degrees for PANG."""
        from ..processing.stokes import product_unit

        unit = getattr(self, "_current_bunit", "") or ""
        combo = getattr(self, "stokes_combo", None)
        return product_unit(combo.currentText(), unit) if combo is not None else unit

    def display_unit(self):
        """The unit of what is drawn: the image's (less the background),
        or the filter's."""
        from .radial_filter import MODES

        if self.radial_filter != "off":
            return MODES[self.radial_filter][1]
        unit = self.value_unit()
        if self.background_subtracted and self.background_data() is not None:
            return f"{unit} less background" if unit else "less background"
        return unit

    def set_background_subtraction(self, on, redraw=True):
        """Draw the image less the background plane it carries (``on``),
        or as it is. Nothing happens to a file without one. The range is
        chosen afresh for what is now drawn. ``redraw`` False only records
        the choice (a preset draws itself)."""
        from .presets import compute_range

        on = bool(on) and self.has_background()
        changed = on != self.background_subtracted
        if redraw and changed:
            try:
                self.prepare_display_data(subtract=on)
            except Cancelled:
                self.show_status_message("Background subtraction cancelled")
                on, redraw = self.background_subtracted, False
            except Exception as e:
                logger.error(f"Background subtraction failed: {e}")
                self.show_status_message(f"Background subtraction failed: {e}")
                on, redraw = self.background_subtracted, False
        self._subtract_background = on
        box = getattr(self, "background_checkbox", None)
        if box is not None:
            box.blockSignals(True)
            box.setChecked(on)
            box.blockSignals(False)
        label = getattr(self, "range_unit_label", None)
        if label is not None and self.current_image_data is not None:
            label.setText(self.display_unit())
        self.show_radial_row()
        if not redraw or not changed or self.current_image_data is None:
            return
        with busy(self._draw_label(), self):
            data = self.display_data()
            if self.radial_filter == "off":
                vmin, vmax = compute_range(data, "p1_99")
            else:
                vmin, vmax = compute_range(data, {"ratio": "p1_99"}.get(self.radial_filter, (-1.5, 4.0)))
            self.set_range(vmin, vmax)
            if hasattr(self, "show_range_preset"):
                self.show_range_preset(None)
            stretch = self.stretch_combo.currentText() if hasattr(self, "stretch_combo") else "linear"
            cmap = self.cmap_combo.currentText() if hasattr(self, "cmap_combo") else "viridis"
            self.plot_image(vmin, vmax, stretch, cmap, self._current_gamma())
        if self.window():
            self.show_status_message("Background subtracted: the K corona is drawn" if on else "Background kept: the total brightness is drawn")

    def on_background_toggled(self, checked):
        """The Display tab's background switch."""
        self.set_background_subtraction(checked)

    def set_radial_filter(self, mode, redraw=True):
        """Draw the image normalised radially in ``mode`` (or not, 'off').
        The range is chosen afresh for what is now drawn, since the old
        one was in another unit. ``redraw`` False only records the choice
        (a preset draws itself)."""
        from .presets import compute_range
        from .radial_filter import MODES

        if mode not in MODES:
            raise ValueError(f"unknown radial filter {mode!r}")
        changed = mode != self.radial_filter
        if redraw and changed:
            try:
                self.prepare_display_data(radial=mode)
            except Cancelled:
                self.show_status_message("Radial filter cancelled")
                mode, redraw = self.radial_filter, False
            except Exception as e:
                logger.error(f"Radial filter failed: {e}")
                self.show_status_message(f"Radial filter failed: {e}")
                mode, redraw = self.radial_filter, False
        self._radial_filter = mode
        combo = getattr(self, "radial_combo", None)
        if combo is not None:
            combo.blockSignals(True)
            combo.setCurrentIndex(list(MODES).index(mode))
            combo.blockSignals(False)
        label = getattr(self, "range_unit_label", None)
        if label is not None and self.current_image_data is not None:
            label.setText(self.display_unit())
        self.show_radial_row()
        if not redraw or not changed or self.current_image_data is None:
            return
        with busy(self._draw_label(), self):
            data = self.display_data()
            # the sigma modes have a natural range; the ratio takes the data's;
            # a coronagraph image shown as it is needs its tails cut
            plain = "p1_99" if self.is_coronagraph() else "minmax"
            vmin, vmax = compute_range(data, {"off": plain, "ratio": "p1_99"}.get(mode, (-1.5, 4.0)))
            self.set_range(vmin, vmax)
            if hasattr(self, "show_range_preset"):
                self.show_range_preset(None)
            stretch = self.stretch_combo.currentText() if hasattr(self, "stretch_combo") else "linear"
            cmap = self.cmap_combo.currentText() if hasattr(self, "cmap_combo") else "viridis"
            self.plot_image(vmin, vmax, stretch, cmap, self._current_gamma())
        if self.window():
            name = MODES[mode][0]
            self.show_status_message(f"Radial filter: {name.lower() if mode != 'off' else 'off'}")

    def on_radial_filter_changed(self, index):
        """The Display tab's radial filter choice."""
        from .radial_filter import MODES

        self.set_radial_filter(list(MODES)[index])

    def is_coronagraph(self):
        """Whether the image shown is a coronagraph's (LASCO, COR), the
        images the radial filter is made for."""
        from .presets import detect_instrument

        if self.current_image_data is None:
            return False
        match = detect_instrument(getattr(self, "current_header", {}) or {}, getattr(self, "imagename", "") or "")
        if match is None:
            return False
        instrument, variant = match
        return instrument == "LASCO" or (instrument == "STEREO" and str(variant).upper().startswith("COR"))

    def show_radial_row(self):
        """The Display tab's radial filter row: shown for coronagraph
        images and while a filter is on, hidden otherwise (the Presets
        menu offers the filter for any image)."""
        row = getattr(self, "radial_row", None)
        if row is None:
            return
        row.setVisible(self.current_image_data is not None and (self.radial_filter != "off" or self.is_coronagraph()))
        background_row = getattr(self, "background_row", None)
        if background_row is not None:
            background_row.setVisible(self.has_background())

    def _draw_label(self):
        name = self.shown_name() if getattr(self, "imagename", "") else ""
        position = self.batch_position
        of = f" ({position[0]} of {position[1]})" if position is not None else ""
        return f"Drawing {name}{of}…" if name else f"Drawing{of}…"

    def plot_image(self, *args, **kwargs):
        """Draw the image (see _plot_image), with the wait cursor until it
        is on screen; the page hears of the range shown (the linked panes
        may take it, core/compare_sync.py)."""
        if getattr(self, "_preparing_display", False):
            # A timer, in the wait for what this needs: when that has ended
            self._plot_after_preparing = True
            return
        with busy(self._draw_label(), self):
            dropped = self.keep_display_filters()
            if dropped:
                # The range asked for is the filter's, not the image's
                args = (None, None) + args[2:]
                kwargs.pop("vmin_val", None)
                kwargs.pop("vmax_val", None)
            self._plot_image(*args, **kwargs)
        # The unit by the range boxes is that of what is drawn (the Stokes
        # product chosen, a filter)
        label = getattr(self, "range_unit_label", None)
        if label is not None and self.current_image_data is not None:
            label.setText(self.display_unit())
        page = getattr(self, "page", None)
        params = getattr(self, "_last_norm_params", None)
        if page is not None and params is not None and self.current_image_data is not None:
            page.sync.range_changed(self, params[0], params[1])
        if dropped:
            self._plot_done = None
            self.show_status_message(dropped)

    def _plot_image(
        self,
        vmin_val=None,
        vmax_val=None,
        stretch="linear",
        cmap="viridis",
        gamma=1.0,
        interpolation="nearest",
        tight_layout=False,
        preserve_view=True,
    ):
        if isinstance(cmap, str) and cmap not in matplotlib.colormaps:
            ensure_solar_colormaps()

        self._refresh_metadata_cache()

        # Use cached values
        fits_flag = self._cached_fits_flag
        header = self._cached_fits_header
        csys = self._cached_csys
        summary = self._cached_summary
        csys_record = self._cached_csys_record

        # Null-safe fallbacks for header and csys_record
        if header is None:
            header = {}
        if csys_record is None:
            csys_record = {}

        if self.current_image_data is None:
            return

        data = self.display_data()

        self._update_transposed_cache(data)
        transposed_data = self._cached_transposed

        stored_xlim = None
        stored_ylim = None
        if preserve_view and self.figure.axes:
            try:
                stored_xlim = self.figure.axes[0].get_xlim()
                stored_ylim = self.figure.axes[0].get_ylim()
            except Exception:
                stored_xlim = None
                stored_ylim = None

        # Determine vmin/vmax
        if vmin_val is None:
            vmin_val = np.nanmin(data)
        if vmax_val is None:
            vmax_val = np.nanmax(data)
        vmin_val, vmax_val = display_range(vmin_val, vmax_val)
        self._show_canvas_page()
        self._sync_levels(data, vmin_val, vmax_val)

        norm = self._make_norm(stretch, vmin_val, vmax_val, gamma)
        if self._try_fast_redraw(
            norm, cmap, stretch, gamma, vmin_val, vmax_val, tight_layout, preserve_view
        ):
            return

        # The state this drawing is of, taken now: a setting can change
        # while it is drawn (loading hands the event loop a turn, and a
        # compare page may set the pane compact then), and a drawing
        # recorded as what it is not would never be done again
        state = self._get_render_state(tight_layout=tight_layout)
        self.figure.clear()
        # Fresh norm for the new image (the one above may be attached to the old one)
        norm = self._make_norm(stretch, vmin_val, vmax_val, gamma)

        # Monkey patch removal if present
        if hasattr(self.figure, "_monkey_patched"):
            del self.figure._monkey_patched
            self.figure.clear = self.figure._original_clear

        wcs_obj = self._get_wcs_object(fits_flag, header, csys, summary)

        # The view of the image this one replaces (the file before it in
        # a series, or itself before it was converted), where this one
        # has it: sky_view's limits_for
        kept_view = getattr(self, "_step_view", None)
        if kept_view is not None and stored_xlim is None:
            limits = limits_for(kept_view, data.shape[:2], wcs_obj, header)
            if limits is not None:
                stored_xlim, stored_ylim = limits
        # ...and what is marked on it, where that lies on this one
        self._carry_marks(kept_view, data.shape[:2], wcs_obj, header)

        ax, im = self._draw_image_axes(
            wcs_obj,
            transposed_data,
            cmap,
            norm,
            interpolation,
            fits_flag,
            header,
            csys_record,
        )
        draw_at_screen_resolution(im)

        if stored_xlim is not None and stored_ylim is not None:
            ax.set_xlim(stored_xlim)
            ax.set_ylim(stored_ylim)

        self._set_plot_title(ax, fits_flag, header, csys_record)

        cb = self._add_colorbar(ax, im, stretch, vmin_val, vmax_val)

        self._apply_plot_customization(ax, cb)
        if self.compact_plot:
            self._make_compact(ax, cb, vmin_val, vmax_val)

        self._draw_beam(ax)

        self._draw_solar_disk(ax, data)

        # Draw shape, text, and arrow annotations
        from ..shape_annotations import (
            draw_arrow_annotations,
            draw_shape_annotations,
            draw_text_annotations,
        )

        draw_shape_annotations(self, ax)
        draw_text_annotations(self, ax)
        draw_arrow_annotations(self, ax)

        # Draw contours if enabled
        if (
            hasattr(self, "show_contours_checkbox")
            and self.show_contours_checkbox.isChecked()
        ):
            self.draw_contours(ax)

        self.init_region_editor(ax)
        self._init_box_zoom(ax)

        self._apply_layout_settings(tight_layout)

        # Draw now rather than when idle, so it happens under the wait cursor
        self.canvas.draw()

        if self.cursor_readout.state_label.text() == "no image":
            self.cursor_readout.set_state(READOUT_HINT)

        # Save state for next update
        self._last_rendered_state = state

        self._last_norm_params = (vmin_val, vmax_val, stretch, gamma)
        # What that range is of, as drawn (a view being converted names
        # its new unit before the new image is loaded)
        self._shown_key = self.range_key()

    @staticmethod
    def _make_norm(stretch, vmin, vmax, gamma):
        """Matplotlib normalization for a stretch name."""
        if stretch == "log":
            safe_min = max(vmin, 1e-8)
            safe_max = max(vmax, safe_min * 1.01)
            return LogNorm(vmin=safe_min, vmax=safe_max)
        elif stretch == "sqrt":
            return SqrtNorm(vmin=vmin, vmax=vmax)
        elif stretch == "arcsinh":
            return AsinhNorm(vmin=vmin, vmax=vmax)
        elif stretch == "power":
            return PowerNorm(vmin=vmin, vmax=vmax, gamma=gamma)
        elif stretch == "zscale":
            return ZScaleNorm(vmin=vmin, vmax=vmax, contrast=0.25, num_samples=600)
        elif stretch == "histeq":
            return HistEqNorm(vmin=vmin, vmax=vmax, n_bins=256)
        else:
            return Normalize(vmin=vmin, vmax=vmax)

    def _refresh_metadata_cache(self):
        """Reload FITS header / CASA coordsys for self.imagename if it changed."""
        # Lazy load image metadata - only refresh when imagename changes
        if self._cached_imagename != self.imagename:
            # Cache miss - need to reload metadata
            self._cached_fits_flag = False
            self._cached_fits_header = None
            self._cached_csys = None
            self._cached_summary = None
            self._cached_csys_record = None

            if is_fits(self.imagename):
                from astropy.io import fits

                try:
                    self._cached_fits_flag = True
                    self._cached_fits_header = dict(fits_header(self.imagename))
                except Exception:
                    try:
                        self._cached_fits_header = dict(fits.getheader(readable_image_path(self.imagename)))
                    except Exception as e2:
                        logger.error(f"Error getting header: {e2}")
                        self.show_status_message(f"Error getting header: {e2}")
                        self._cached_fits_flag = False

            # (No coordinate system loaded: CASA could not read the file, and
            # would take as long to say so again)
            if self._no_coordsys_for != self.imagename:
                try:
                    info = casa_info(self.imagename)
                    self._cached_csys = info.coordsys()
                    self._cached_summary = info.summary
                    self._cached_csys_record = info.csys_record
                except Exception as e:
                    logger.error(f"Error getting image metadata: {e}")
                    self.show_status_message(f"Error getting image metadata: {e}")

            self._cached_imagename = self.imagename

    def _update_transposed_cache(self, data):
        """Cache data.T."""
        if not hasattr(self, "_cached_data_id") or self._cached_data_id != id(data):
            self._cached_transposed = data.transpose()
            self._cached_data_id = id(data)

    def _try_fast_redraw(
        self, norm, cmap, stretch, gamma, fp_vmin, fp_vmax, tight_layout, preserve_view
    ):
        """Update the existing image in place if the plot structure is unchanged.

        Must run before figure.clear(). Returns True if the redraw was handled.
        """
        # FAST PATH OPTIMIZATION: Update existing plot if structure hasn't changed
        # MUST BE DONE BEFORE figure.clear()
        current_state = self._get_render_state(tight_layout=tight_layout)

        can_fast_path = (
            hasattr(self, "image_plot")
            and self.image_plot is not None
            and self.figure.axes
            and preserve_view
        )

        if can_fast_path:
            if self._last_rendered_state == current_state:
                # Success! Update and Return.
                try:
                    self.image_plot.set_cmap(cmap)
                    self.image_plot.set_norm(norm)
                    norm_params = (fp_vmin, fp_vmax, stretch, gamma)

                    if hasattr(self, "colorbar") and self.colorbar:
                        # update_normal gives the colorbar its ticks afresh,
                        # so a compare pane's strip is styled again
                        self.colorbar.update_normal(self.image_plot)
                        if self.compact_plot and self.pane_colorbar:
                            self._style_compact_colorbar(self.colorbar, fp_vmin, fp_vmax)
                        elif stretch in ("power", "histeq", "sqrt", "arcsinh", "log"):
                            try:
                                from ..processing.plot_style import drop_outside_ticks

                                drop_outside_ticks(self.colorbar, fp_vmin, fp_vmax)
                            except Exception:
                                pass

                    """if last_norm_params != norm_params:
                        if hasattr(self, "colorbar") and self.colorbar:
                            self.colorbar.update_normal(self.image_plot)
                            if stretch in ("power", "histeq", "sqrt", "arcsinh", "log"):
                                try:
                                    ticks = self.colorbar.ax.get_yticks()
                                    valid_ticks = [t for t in ticks if fp_vmin <= t <= fp_vmax]
                                    if len(valid_ticks) >= 2:
                                        self.colorbar.ax.set_yticks(valid_ticks)
                                except Exception:
                                    pass
                    """

                    # Cache new params
                    self._last_norm_params = norm_params
                    self._shown_key = self.range_key()

                    self.canvas.draw()
                    return True
                except Exception as e:
                    logger.debug(f"Fast path update failed: {e}")
            else:
                # Debugging Mismatch (Optional log, keeping it minimal)
                pass
        return False

    def _view_shown(self, turned=False):
        """The view of the image shown, for the one that replaces it to
        keep (sky_view's KeptView), or None without an image."""
        ax = self._nav_axes() if self.current_image_data is not None else None
        if ax is None:
            return None
        wcs = getattr(ax, "wcs", None)
        if wcs is not None and getattr(wcs, "naxis", 0) < 2:
            wcs = None
        return KeptView(
            self.current_image_data.shape[:2],
            ax.get_xlim(),
            ax.get_ylim(),
            turned,
            wcs_kind(wcs),
            wcs,
            dict(getattr(self, "current_header", None) or {}),
        )

    def _carry_marks(self, view, shape, wcs, header):
        """What is marked on the image the pane showed (``view``'s: the
        regions and the shape, text and arrow annotations, all placed in
        pixels) goes where it lies on the image drawn now, of ``shape``,
        ``wcs`` and ``header``: sky_view's marks_map. Once for a view; an
        image only passed through leaves the marks for the next."""
        self._passing_image = False
        if view is None or getattr(self, "_marks_carried", None) is view:
            return
        try:
            move = marks_map(view, shape, wcs, header)
            if move is PASSING:
                self._passing_image = True
                return
            self._marks_carried = view
            if move is not None:
                self._move_marks(move)
        except Exception:
            logger.warning("Could not keep the regions and annotations on the sky", exc_info=True)
            self._marks_carried = view

    def _move_marks(self, move):
        """The regions and annotations where ``move`` (a marks_map) puts
        them, turned and sized as the pixels under them are. One that
        cannot be placed stays as it is."""
        regions = list(getattr(self, "regions", []))
        boxes = [
            ((x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1, region.angle)
            for region in regions
            for x1, y1, x2, y2 in [region.corners]
        ]
        for region, box in zip(regions, moved_boxes(move, boxes)):
            if box is not None:
                cx, cy, width, height, angle = box
                region.corners = tuple(
                    snap(value)
                    for value in (cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2)
                )
                region.angle = angle

        shapes = list(getattr(self, "shape_annotations", []))
        boxes = []
        for shape in shapes:
            # (a size in arcsec stays: only how it lies changes)
            sized = shape.get("unit", "pixels") != "arcsec"
            width = shape["width"] if sized else 10.0
            height = shape.get("height", shape["width"]) if sized else 10.0
            turns = shape["type"] in ("ellipse", "rectangle")
            boxes.append((
                shape["center_x"],
                shape["center_y"],
                width,
                height if turns else width,
                shape.get("angle", 0.0) if turns else 0.0,
            ))
        for shape, box in zip(shapes, moved_boxes(move, boxes)):
            if box is None:
                continue
            cx, cy, width, height, angle = box
            shape["center_x"], shape["center_y"] = cx, cy
            if shape.get("unit", "pixels") != "arcsec":
                shape["width"] = width
                if "height" in shape:
                    shape["height"] = height if shape["type"] in ("ellipse", "rectangle") else width
            if shape["type"] == "square" and abs(angle) % 90 > 1e-6:
                # A square is drawn upright: turned, it is a rectangle
                shape["type"] = "rectangle"
                shape["height"] = shape["width"]
            if shape["type"] in ("ellipse", "rectangle"):
                shape["angle"] = angle

        texts = list(getattr(self, "text_annotations", []))
        arrows = list(getattr(self, "arrow_annotations", []))
        points = (
            [(text, "x", "y") for text in texts]
            + [(arrow, "x1", "y1") for arrow in arrows]
            + [(arrow, "x2", "y2") for arrow in arrows]
        )
        if points:
            xs, ys = move([mark[kx] for mark, kx, _ky in points], [mark[ky] for mark, _kx, ky in points])
            for (mark, kx, ky), x, y in zip(points, xs, ys):
                if np.isfinite(x) and np.isfinite(y):
                    mark[kx], mark[ky] = float(x), float(y)

    def _settle_marks(self, view):
        """A step through converted images ended on the file as it is (its
        conversion failed, or was cancelled): what was marked on the
        converted image before it goes where it lies on this one, and the
        regions are measured."""
        ax = self._nav_axes()
        if ax is None or view is None:
            return
        try:
            move = marks_map(
                view._replace(turned=False),
                self.current_image_data.shape[:2],
                getattr(ax, "wcs", None),
                getattr(self, "current_header", None) or {},
            )
            if callable(move):
                self._move_marks(move)
        except Exception:
            logger.warning("Could not keep the regions and annotations on the sky", exc_info=True)
        self.init_region_editor(ax)
        self.schedule_plot()

    def _get_wcs_object(self, fits_flag, header, csys, summary):
        """Build (and cache per current_wcs) a 2D astropy WCS for plotting."""
        # Cache the WCS object if current_wcs hasn't changed.
        wcs_obj = None
        if self.current_wcs:
            # (the object itself: an id can come round again once it is freed)
            if getattr(self, "_cached_wcs_of", None) is not self.current_wcs:
                try:
                    self._cached_wcs_obj = self._build_display_wcs(
                        self.current_wcs, fits_flag, header, csys, summary
                    )
                    self._cached_wcs_of = self.current_wcs
                except Exception as e:
                    logger.error(f"Error creating WCS: {e}")
                    self.show_status_message(f"Error creating WCS: {e}")
                    self._cached_wcs_obj = None
            wcs_obj = self._cached_wcs_obj
        return wcs_obj

    def _build_display_wcs(self, csys_tool, fits_flag, header, csys, summary):
        """2D astropy WCS for an image's pixel grid, from its CASA coordsys
        ``csys_tool`` (as loaded, so including any downsampling) and its FITS
        header / full coordsys / summary. None if the projection is unsupported.
        """
        from astropy.wcs import WCS

        ref_val = csys_tool.referencevalue()["numeric"][0:2]
        ref_pix = csys_tool.referencepixel()["numeric"][0:2]
        increment = csys_tool.increment()["numeric"][0:2]
        wcs_obj = WCS(naxis=2)
        wcs_obj.wcs.crpix = [
            ref_pix[0] + 1,
            ref_pix[1] + 1,
        ]  # CASA 0-indexed -> FITS 1-indexed
        temp_flag = False
        if fits_flag:
            if (
                header["CTYPE1"] == "HPLN-TAN"
                or header["CTYPE1"] == "RA---SIN"
                or header["CTYPE1"] == "RA---TAN"
            ):
                wcs_obj.wcs.crval = [
                    ref_val[0] * 180 / np.pi,
                    ref_val[1] * 180 / np.pi,
                ]
                wcs_obj.wcs.cdelt = [
                    increment[0] * 180 / np.pi,
                    increment[1] * 180 / np.pi,
                ]
                temp_flag = True
            elif header["CTYPE1"] == "SOLAR-X":
                wcs_obj.wcs.crval = ref_val
                wcs_obj.wcs.cdelt = increment
                # The axis unit (arcsec, as a rule): a linear axis gets none
                # by itself, and other panes need it to line up with this one
                try:
                    wcs_obj.wcs.cunit = [str(u) for u in csys_tool.units()[:2]]
                except Exception:
                    pass
                temp_flag = True
        if not temp_flag:
            if "Right Ascension" in summary["axisnames"]:
                wcs_obj.wcs.crval = [
                    ref_val[0] * 180 / np.pi,
                    ref_val[1] * 180 / np.pi,
                ]
                wcs_obj.wcs.cdelt = [
                    increment[0] * 180 / np.pi,
                    increment[1] * 180 / np.pi,
                ]
            else:
                wcs_obj.wcs.crval = ref_val
                wcs_obj.wcs.cdelt = increment
        try:
            if fits_flag:
                try:
                    wcs_obj.wcs.ctype = [
                        header["CTYPE1"],
                        header["CTYPE2"],
                    ]
                    rotation = _pixel_rotation(header)
                    if rotation is not None:
                        wcs_obj.wcs.pc = rotation
                except Exception as e:
                    logger.error(f"Error getting projection: {e}")
                    self.show_status_message(
                        f"Error getting projection: {e}"
                    )
                    wcs_obj = None
            elif (csys.projection()["type"] == "SIN") and (
                "Right Ascension" in summary["axisnames"]
            ):
                wcs_obj.wcs.ctype = [
                    "RA---SIN",
                    "DEC--SIN",
                ]
            elif (csys.projection()["type"] == "TAN") and (
                "Right Ascension" in summary["axisnames"]
            ):
                wcs_obj.wcs.ctype = [
                    "RA---TAN",
                    "DEC--TAN",
                ]

            else:
                projection = csys.projection()["type"]
                logger.error(f"Unsupported projection: {projection}")
                self.show_status_message(
                    f"Unsupported projection: {projection}"
                )
                wcs_obj = None

        except Exception as e:
            logger.error(f"Error getting projection: {e}")
            self.show_status_message(f"Error getting projection: {e}")
            wcs_obj = None
        return wcs_obj

    def _draw_image_axes(
        self,
        wcs_obj,
        transposed_data,
        cmap,
        norm,
        interpolation,
        fits_flag,
        header,
        csys_record,
    ):
        """Create the axes (WCS-aware if possible), draw the image, set axis labels."""
        # Plot with or without WCS
        if wcs_obj is not None:
            try:
                ax = self.figure.add_subplot(111, projection=wcs_obj)
                # Matplotlib's imshow directly: WCSAxes.imshow only adds
                # handling for PIL images, behind a version check that scans
                # every installed package's metadata on each call
                im = Axes.imshow(
                    ax,
                    transposed_data,
                    origin="lower",
                    cmap=cmap,
                    norm=norm,
                    interpolation=interpolation,
                )
                self.image_plot = im
                if fits_flag:
                    if header["CTYPE1"] == "HPLN-TAN":
                        ax.set_xlabel("Solar X")
                        ax.set_ylabel("Solar Y")
                    elif header["CTYPE1"] == "SOLAR-X":
                        ax.set_xlabel(f"Solar X ({header.get('CUNIT1', 'arcsec')})")
                        ax.set_ylabel(f"Solar Y ({header.get('CUNIT2', 'arcsec')})")
                    else:
                        ax.set_xlabel("Right Ascension (J2000)")
                        ax.set_ylabel("Declination (J2000)")
                elif wcs_obj.wcs.ctype[0] == "RA---SIN":
                    ax.set_xlabel("Right Ascension (J2000)")
                    ax.set_ylabel("Declination (J2000)")
                elif wcs_obj.wcs.ctype[0] == "SOLAR-X":
                    if csys_record["linear0"]["units"][0] == "arcsec":
                        ax.set_xlabel("Solar X (arcsec)")
                        ax.set_ylabel("Solar Y (arcsec)")
                    elif csys_record["linear0"]["units"][0] == "arcmin":
                        ax.set_xlabel("Solar X (arcmin)")
                        ax.set_ylabel("Solar Y (arcmin)")
                    elif csys_record["linear0"]["units"][0] == "deg":
                        ax.set_xlabel("Solar X (deg)")
                        ax.set_ylabel("Solar Y (deg)")
                    else:
                        ax.set_xlabel("Solar X")
                        ax.set_ylabel("Solar Y")
                elif wcs_obj.wcs.ctype[0] == "RA---TAN":
                    ax.set_xlabel("Right Ascension (J2000)")
                    ax.set_ylabel("Declination (J2000)")
                else:
                    ax.set_xlabel("Right Ascension (J2000)")
                    ax.set_ylabel("Declination (J2000)")
                if (
                    hasattr(self, "show_grid_checkbox")
                    and self.show_grid_checkbox.isChecked()
                ):
                    ax.coords.grid(
                        True,
                        color=self.grid_style.get("color", "white"),
                        alpha=self.grid_style.get("alpha", 0.5),
                        linestyle=self.grid_style.get("linestyle", "--"),
                    )
                else:
                    ax.coords.grid(False)
                if (
                    wcs_obj.wcs.ctype[0] == "RA---SIN"
                    or wcs_obj.wcs.ctype[0] == "RA---TAN"
                ):
                    ax.coords[0].set_major_formatter("hh:mm:ss.s")
                    ax.coords[1].set_major_formatter("dd:mm:ss")
                ax.tick_params(axis="both", which="major", labelsize=10)
            except Exception as e:
                logger.error(f"Error setting up WCS axes: {e}")
                self.show_status_message(f"Error setting up WCS axes: {e}")
                self.figure.clear()  # (the WCS axes may have been made)
                ax = self.figure.add_subplot(111)
                im = ax.imshow(
                    transposed_data,
                    origin="lower",
                    cmap=cmap,
                    norm=norm,
                    interpolation=interpolation,
                    aspect="auto",
                )
                self.image_plot = im
                ax.set_xlabel("Pixel X")
                ax.set_ylabel("Pixel Y")
        else:
            ax = self.figure.add_subplot(111)
            im = ax.imshow(
                transposed_data,
                origin="lower",
                cmap=cmap,
                norm=norm,
                interpolation=interpolation,
                aspect="auto",
            )
            self.image_plot = im
            ax.set_xlabel("Pixel X")
            ax.set_ylabel("Pixel Y")
        return ax, im

    def _set_plot_title(self, ax, fits_flag, header, csys_record):
        """Title with observation time, frequency / instrument."""
        # Display the image time in UTC and freq in MHz as a title
        if self.current_image_data is not None:
            # Get the image time and frequency
            try:
                temp_flag = False
                image_time = None
                image_freq = None
                if fits_flag:
                    try:
                        # Check both DATE-OBS (standard) and DATE_OBS (IRIS uses this)
                        image_time = (
                            header.get("DATE-OBS")
                            or header.get("DATE_OBS")
                            or header.get("STARTOBS")
                        )
                        # SOHO level 0 splits the date and the time
                        if header.get("TIME-OBS") and image_time and "T" not in str(image_time):
                            image_time = f"{image_time}T{header['TIME-OBS']}"
                        # Keep upto one decimal place if image_time seconds have more than one decimal place
                        if image_time and "T" in str(image_time):
                            date = image_time.split("T")[0]
                            time_str = image_time.split("T")[1]
                            time_parts = time_str.split(":")
                            seconds = time_parts[-1]
                            if "." in seconds:
                                seconds = seconds[:4]
                            image_time = (
                                f"{date}T{time_parts[0]}:{time_parts[1]}:{seconds}"
                            )
                        temp_flag = True
                    except Exception as e:
                        logger.error(f"Error getting image time: {e}")
                        self.show_status_message(f"Error getting image time: {e}")
                        image_time = None

                    try:
                        image_freq = header.get("FREQ")
                        if image_freq is not None:
                            freq_unit = header.get("FREQUNIT")
                            if freq_unit == "Hz":
                                image_freq = f"{image_freq * 1e-6:.2f} MHz"
                            else:
                                image_freq = f"{image_freq:.2f} {freq_unit}"
                    except Exception as e:
                        logger.error(f"Error getting image frequency: {e}")
                        self.show_status_message(f"Error getting image frequency: {e}")
                        image_freq = None

                if "spectral2" in csys_record and image_freq is None:
                    spectral2 = csys_record["spectral2"]
                    wcs = spectral2.get("wcs", {})
                    frequency_ref = wcs.get("crval", None)
                    frequency_unit = spectral2.get("unit", None)
                    if frequency_unit == "Hz":
                        image_freq = f"{frequency_ref * 1e-6:.2f} MHz"
                    else:
                        image_freq = f"{frequency_ref:.2f} {frequency_unit}"

                if not temp_flag:
                    t = casa_obs_time(csys_record)
                    if t is not None:
                        t.precision = 1
                        image_time = t.iso
                    else:
                        image_time = None

                if fits_flag:
                    if (
                        header.get("TELESCOP") == "SOHO"
                        and header.get("INSTRUME") == "LASCO"
                    ):
                        title = f"{image_time} | {header['TELESCOP']} {header['INSTRUME']} {header.get('DETECTOR', '')}"
                    elif (
                        header.get("TELESCOP") == "SOHO"
                        and header.get("INSTRUME") == "EIT"
                    ):
                        wl = header.get("WAVELNTH", "")
                        title = (
                            f"{image_time} | {header['TELESCOP']} {header['INSTRUME']} {wl} Å"
                            if wl
                            else f"{image_time} | {header['TELESCOP']} {header['INSTRUME']}"
                        )
                    elif (
                        header.get("TELESCOP") == "SOHO"
                        and header.get("INSTRUME") == "MDI"
                    ):
                        title = (
                            f"{image_time} | {header['TELESCOP']} {header['INSTRUME']}"
                        )
                    elif header.get("TELESCOP") == "SDO/AIA":
                        wl = header.get("WAVELNTH", "")  # (a converted image may lack it)
                        title = f"{image_time} | {header['TELESCOP']} {wl} $\\AA$" if wl else f"{image_time} | {header['TELESCOP']}"
                    elif header.get("TELESCOP") == "SDO/HMI":
                        title = f"{image_time} | {header['TELESCOP']}"
                    elif (
                        header.get("INSTRUME") == "SJI"
                        or header.get("TELESCOP") == "IRIS"
                    ):
                        # IRIS SJI
                        wl = header.get("TWAVE1", header.get("WAVELNTH", ""))
                        title = (
                            f"{image_time} | IRIS SJI {wl} Å"
                            if wl
                            else f"{image_time} | IRIS SJI"
                        )
                    elif "SUVI" in str(header.get("INSTRUME", "")):
                        # GOES SUVI
                        sat = header.get("TELESCOP", "GOES")
                        wl = header.get("WAVELNTH", "")
                        title = (
                            f"{image_time} | {sat} SUVI {wl} Å"
                            if wl
                            else f"{image_time} | {sat} SUVI"
                        )
                    elif "GONG" in str(header.get("TELESCOP", "")) or "GONG" in str(
                        header.get("INSTRUME", "")
                    ):
                        # GONG
                        title = f"{image_time} | GONG Magnetogram"
                    elif (
                        header.get("TELESCOP") == "STEREO"
                        and header.get("INSTRUME") == "SECCHI"
                    ):
                        # STEREO SECCHI (COR1, COR2, EUVI)
                        obs = header.get("OBSRVTRY", "STEREO").replace("_", "-")
                        det = header.get("DETECTOR", "")
                        title = f"{image_time} | {obs} {det}"

                    elif image_time is not None and image_freq is not None:
                        title = f"Time: {image_time} | Freq: {image_freq}"
                    elif image_time is not None and image_freq is None:
                        title = f"Time: {image_time}"
                    elif image_time is None and image_freq is not None:
                        title = f"Freq: {image_freq}"
                    else:
                        title = (
                            os.path.basename(self.imagename)
                            if self.imagename
                            else "No Image"
                        )

                elif image_time is not None and image_freq is None:
                    title = f"Time: {image_time}"
                elif image_time is None and image_freq is not None:
                    title = f"Freq: {image_freq}"
                elif image_time is not None and image_freq is not None:
                    title = f"Time: {image_time} | Freq: {image_freq}"
                else:
                    title = (
                        os.path.basename(self.imagename)
                        if self.imagename
                        else "No Image"
                    )
                self.plot_title = title
                ax.set_title("" if self.compact_plot else self._fit_title(title))
                if hasattr(self, "show_observation_time"):
                    self.show_observation_time(image_time)
            except Exception as e:
                logger.error(f"Error getting title: {e}")
                self.show_status_message(f"Error getting title: {e}")
                title = (
                    os.path.basename(self.imagename) if self.imagename else "No Image"
                )
                self.plot_title = title
                ax.set_title("" if self.compact_plot else self._fit_title(title))

    def _add_colorbar(self, ax, im, stretch, vmin_val, vmax_val):
        """Add the colorbar (dropping out-of-range ticks for non-linear
        stretches); a slim one, close to the image, for a compact plot."""
        if self.compact_plot:
            if not self.pane_colorbar:
                self.colorbar = None
                return None
            # A strip over the image, as wide as the image: placed by a
            # locator from the box the image settles into (its aspect is
            # fixed) each time it is drawn
            px = self.figure.dpi / 72.0
            cax = ax.inset_axes([0.0, 0.0, 1.0, 1.0])
            cax.set_axes_locator(_strip_above(ax, COMPACT_CB_GAP_PT * px, COMPACT_CB_HEIGHT_PT * px))
            cb = self.figure.colorbar(im, cax=cax, orientation="horizontal")
            if stretch in ("power", "histeq", "sqrt", "arcsinh", "log"):
                from ..processing.plot_style import drop_outside_ticks

                drop_outside_ticks(cb, vmin_val, vmax_val)
            self.colorbar = cb
            return cb
        # For non-linear stretches, filter ticks to only show those within data range
        if stretch in ("power", "histeq", "sqrt", "arcsinh", "log"):
            cb = self.figure.colorbar(
                im,
                ax=ax,
                aspect=30,
            )
            # Remove ticks that are outside the actual data range
            from ..processing.plot_style import drop_outside_ticks

            drop_outside_ticks(cb, vmin_val, vmax_val)
        else:
            cb = self.figure.colorbar(
                im,
                ax=ax,
                aspect=30,
            )
        self.colorbar = cb
        return cb

    def _make_compact(self, ax, cb, vmin_val, vmax_val):
        """The plot as a pane of a compare page shows it: the image alone
        by default (the pane's header says what the image is, so never a
        title), its ticks and labels when the page's Axes switch is on,
        and with the Colorbar switch a strip under it marked with the
        range's ends."""
        ax.set_title("")
        if not self.pane_axes:
            ax.set_xlabel("")
            ax.set_ylabel("")
            coords = getattr(ax, "coords", None)
            if coords is not None:
                for coord in coords:
                    coord.set_axislabel("")
                    coord.set_ticklabel_visible(False)
                    coord.set_ticks_visible(False)
            else:
                ax.set_xticks([])
                ax.set_yticks([])
        if cb is not None:
            self._style_compact_colorbar(cb, vmin_val, vmax_val)

    def _style_compact_colorbar(self, cb, vmin_val, vmax_val):
        """The strip's values over it: a few round ones within the range,
        spread along the strip (so not crowded where a stretch packs the
        values), in plain figures."""
        from matplotlib.ticker import FuncFormatter

        ax = self.image_plot.axes
        cb.set_label("")
        cb.ax.xaxis.set_ticks_position("top")
        cb.ax.xaxis.set_label_position("top")
        size_pt = max(7, self.plot_settings.get("colorbar_tick_fontsize", 10) - 2)
        ticks = self._ticks_that_fit(ax, cb, vmin_val, vmax_val, size_pt)
        cb.set_ticks(ticks)
        cb.ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _pos: _plain_number(value)))
        cb.ax.tick_params(labelsize=size_pt, length=2, pad=2)
        cb.ax.minorticks_off()
        # A value at an end of the strip kept within it
        labels = cb.ax.get_xticklabels()
        if ticks and labels:
            low, high = min(vmin_val, vmax_val), max(vmin_val, vmax_val)
            span = max(high - low, 1e-30)
            if (ticks[0] - low) / span < 0.05:
                labels[0].set_horizontalalignment("left")
            if (high - ticks[-1]) / span < 0.05:
                labels[-1].set_horizontalalignment("right")

    def _ticks_that_fit(self, ax, cb, vmin_val, vmax_val, size_pt):
        """As many of the strip's values as their labels have room for
        along the image's width, at ``size_pt``."""
        px = self.figure.dpi / 72.0
        width, height = self.figure.get_size_inches() * self.figure.dpi
        box = ax.get_position(original=True)
        shape = self.image_plot.get_array().shape if self.image_plot is not None else (1, 1)
        # The image's width once it settles into its box, keeping its aspect
        strip_px = min(box.width * width, box.height * height * shape[1] / max(shape[0], 1))
        ticks = [vmin_val]
        for most in range(COMPACT_CB_TICKS, 1, -1):
            ticks = _spread_ticks(cb.norm, vmin_val, vmax_val, most)
            widest = max(len(_plain_number(tick)) for tick in ticks) * 0.62 * size_pt * px
            if len(ticks) * (widest + 8 * px) <= strip_px:
                break
        return ticks

    def _axes_bottom_pt(self):
        """The room the tick labels and axis label under the axes take,
        in points."""
        ps = self.plot_settings
        return ps.get("axis_tick_fontsize", 10) * 1.4 + ps.get("axis_label_fontsize", 12) * 1.6 + 9

    def _apply_plot_customization(self, ax, cb):
        """Apply self.plot_settings: labels, fonts, colors, ticks, borders."""
        from ..processing.plot_style import apply_plot_settings
        from ..ui.styles import theme_manager

        def on_error(message):
            logger.error(f"{message}")
            self.show_status_message(message)

        apply_plot_settings(
            ax,
            cb,
            self.plot_settings,
            self.figure,
            dark=theme_manager.is_dark,
            bunit=self.display_unit() or None,
            on_error=on_error,
        )

    def _draw_beam(self, ax):
        """Draw the restoring beam ellipse if enabled."""
        # Draw beam if available
        if self.psf and self.show_beam_checkbox.isChecked():
            try:
                if isinstance(self.psf["major"]["value"], list):
                    major_deg = float(self.psf["major"]["value"][0]) / 3600.0
                else:
                    major_deg = float(self.psf["major"]["value"]) / 3600.0

                if isinstance(self.psf["minor"]["value"], list):
                    minor_deg = float(self.psf["minor"]["value"][0]) / 3600.0
                else:
                    minor_deg = float(self.psf["minor"]["value"]) / 3600.0

                if isinstance(self.psf["positionangle"]["value"], list):
                    pa_deg = float(self.psf["positionangle"]["value"][0]) - 90
                else:
                    pa_deg = float(self.psf["positionangle"]["value"]) - 90

                if self.current_wcs:
                    from ..shape_annotations import pixel_scales_deg

                    dx_deg = pixel_scales_deg(self.current_wcs)[0]
                else:
                    dx_deg = 1.0 / 3600

                major_pix = major_deg / dx_deg
                minor_pix = minor_deg / dx_deg

                xlim = ax.get_xlim()
                ylim = ax.get_ylim()
                view_width = xlim[1] - xlim[0]
                view_height = ylim[1] - ylim[0]
                margin_x = view_width * 0.05
                margin_y = view_height * 0.05
                beam_x = xlim[0] + margin_x + major_pix / 2
                beam_y = ylim[0] + margin_y + minor_pix / 2

                ellipse = Ellipse(
                    (beam_x, beam_y),
                    width=major_pix,
                    height=minor_pix,
                    angle=pa_deg,
                    fill=True,
                    edgecolor=self.beam_style.get("edgecolor", "black"),
                    linewidth=self.beam_style.get("linewidth", 1.5),
                    facecolor=self.beam_style.get("facecolor", "white"),
                    alpha=self.beam_style.get("alpha", 0.4),
                )
                ax.add_patch(ellipse)
                self.beam_properties = {
                    "major_pix": major_pix,
                    "minor_pix": minor_pix,
                    "pa_deg": pa_deg,
                    "margin": 0.05,
                }
            except Exception as e:
                logger.error(f"Error drawing beam: {e}")
                self.show_status_message(f"Error drawing beam: {e}")

    def _update_beam_position(self, ax):
        # Don't draw beam if no PSF or beam properties
        if (
            not self.psf
            or not hasattr(self, "beam_properties")
            or not self.beam_properties
        ):
            return

        for patch in list(ax.patches):
            if (
                isinstance(patch, Ellipse)
                and not getattr(patch, "_shape_annotation", False)
                and not getattr(patch, "_solar_disk", False)
            ):
                patch.remove()

        xlim = ax.get_xlim()
        ylim = ax.get_ylim()

        major_pix = self.beam_properties["major_pix"]
        minor_pix = self.beam_properties["minor_pix"]
        pa_deg = self.beam_properties["pa_deg"]

        view_width = xlim[1] - xlim[0]
        view_height = ylim[1] - ylim[0]

        margin_x = view_width * 0.05
        margin_y = view_height * 0.05

        beam_x = xlim[0] + margin_x + major_pix / 2
        beam_y = ylim[0] + margin_y + minor_pix / 2

        ellipse = Ellipse(
            (beam_x, beam_y),
            width=major_pix,
            height=minor_pix,
            angle=pa_deg,
            fill=True,
            edgecolor=self.beam_style.get("edgecolor", "black"),
            facecolor=self.beam_style.get("facecolor", "white"),
            linewidth=self.beam_style.get("linewidth", 1.5),
            alpha=self.beam_style.get("alpha", 0.4),
        )
        ax.add_patch(ellipse)

    # ---- A quick picture of a display range while it is dragged: the
    # image at a few hundred pixels a side (the norm is applied to every
    # pixel on each draw, and that is what takes the time), drawn over a
    # saved picture of everything else and blitted whole (never a part of
    # the canvas: at a fractional display scale that lands askew). The
    # overlays above the image are drawn again so they stay on top; the
    # colorbar waits for the full draw when the drag ends.
    PREVIEW_PIXELS = 400

    def begin_range_preview(self):
        """Start previewing: the image artist shows a reduced copy of its
        array in the same place, and the rest of the figure is saved.
        Returns False if there is no image."""
        im = self.image_plot
        if im is None or self.current_image_data is None or not self.figure.axes:
            return False
        full = im.get_array()
        extent = im.get_extent()
        step = max(1, int(np.ceil(max(full.shape[:2]) / self.PREVIEW_PIXELS)))
        if step > 1:
            self._swap_preview_array(im, full[::step, ::step], extent)
        ax = im.axes
        # The overlays drawn again above the image: not the axes' own
        # background patch, nor the WCS frame artist, which paints that
        # patch again (its grid, if on, stays under the preview)
        skip = {im, ax.patch}
        frame = getattr(getattr(ax, "coords", None), "frame", None)
        if frame is not None:
            skip.add(getattr(frame, "patch", None))
        above = [
            a
            for a in ax.get_children()
            if a not in skip
            and a.get_zorder() > im.get_zorder()
            and type(a).__name__ != "_WCSAxesArtist"
        ]
        try:
            # Drawn into the buffer only (not the Qt canvas's draw, which
            # would put the figure without its image on screen)
            im.set_visible(False)
            FigureCanvasAgg.draw(self.canvas)
            background = self.canvas.copy_from_bbox(self.figure.bbox)
        except Exception as e:
            logger.debug(f"Range preview without blitting: {e}")
            background = None
        finally:
            im.set_visible(True)
        self._range_preview = (full, extent, background, above)
        # The preview as it is now, so the buffer holds a whole picture
        # again before anything repaints the canvas
        self._blit_preview()
        return True

    def preview_range(self, vmin, vmax):
        """Draw the preview with this range (the stretch and gamma as set)."""
        if getattr(self, "_range_preview", None) is None:
            return
        vmin, vmax = display_range(vmin, vmax)
        norm = self._make_norm(self.stretch_combo.currentText(), vmin, vmax, self._current_gamma())
        self.image_plot.set_norm(norm)
        self._blit_preview()

    def _blit_preview(self):
        """The saved picture, the image and the overlays above it, on screen."""
        _full, _extent, background, above = self._range_preview
        if background is None:
            self.canvas.draw()
            return
        im = self.image_plot
        try:
            self.canvas.restore_region(background)
            ax = im.axes
            ax.draw_artist(im)
            for artist in above:
                ax.draw_artist(artist)
            self.canvas.blit(self.figure.bbox)
        except Exception as e:
            logger.debug(f"Range preview blit failed, drawing whole: {e}")
            self.canvas.draw()

    def end_range_preview(self):
        """The full image again (the caller draws it with the final range)."""
        preview = getattr(self, "_range_preview", None)
        if preview is None:
            return
        self._range_preview = None
        full, extent, _background, _above = preview
        self._swap_preview_array(self.image_plot, full, extent)

    @staticmethod
    def _swap_preview_array(im, array, extent):
        """Give ``im`` ``array`` over the same ``extent``: the view is not
        reset to the whole image (which setting an extent does when the
        axes autoscale)."""
        ax = im.axes
        was = ax.get_autoscale_on()
        ax.set_autoscale_on(False)
        try:
            im.set_data(array)
            im.set_extent(extent)
        finally:
            ax.set_autoscale_on(was)

    def _sync_levels(self, data, vmin_val, vmax_val):
        """The inspector's histogram shows this image and range."""
        levels = getattr(self, "levels", None)
        if levels is None:
            return
        # Known by a weak reference, not id(): a new array often gets a
        # freed one's id, and the histogram stayed the last image's
        known = getattr(self, "_levels_data_ref", None)
        if known is None or known() is not data:
            self._levels_data_ref = weakref.ref(data)
            levels.set_data(data, vmin_val, vmax_val)
        else:
            levels.set_range(vmin_val, vmax_val)

    def _apply_layout_settings(self, tight_layout):
        """Matplotlib's tight layout, if turned on in Plot Customization
        (set again whenever the canvas is resized); otherwise the custom
        padding set there, or margins sized for the fonts and the canvas
        when that padding is the default."""
        ps = self.plot_settings
        if ps.get("use_tight_layout", False):
            self.figure.tight_layout()
            return
        pads = {k: ps.get(k, DEFAULT_PLOT_SETTINGS[k]) for k in _PAD_KEYS}
        if all(pads[k] == DEFAULT_PLOT_SETTINGS[k] for k in _PAD_KEYS):
            pads.update(self._margins_for_fonts())
        self.figure.subplots_adjust(
            left=pads["pad_left"],
            right=pads["pad_right"],
            top=pads["pad_top"],
            bottom=pads["pad_bottom"],
            wspace=ps.get("pad_wspace", 0.2),
            hspace=ps.get("pad_hspace", 0.2),
        )

    def _fit_title(self, title):
        """``title`` on two lines ("Time: ...\\nFreq: ...") when the canvas
        is too narrow for it on one: Matplotlib centres a title over the
        axes and would cut a long one off at both sides."""
        width = self.figure.get_size_inches()[0] * self.figure.dpi
        if " | " in title and width < 1100:
            return title.replace(" | ", "\n", 1)
        return title

    def _margins_for_fonts(self):
        """Subplot margins (fractions of the figure) leaving room for the
        title above, the tick labels and axis label left and below, and the
        colorbar's tick labels and label on the right, at the current font
        sizes and canvas size."""
        ps = self.plot_settings
        width, height = self.figure.get_size_inches() * self.figure.dpi
        px = self.figure.dpi / 72.0  # pixels per point
        if self.compact_plot:
            # No title; the tick labels and axis labels only when the axes
            # are shown; over the image the colorbar strip and its values
            # when it is shown
            tick = ps.get("axis_tick_fontsize", 10)
            label = ps.get("axis_label_fontsize", 12)
            left_px = (tick * 0.65 * 8 + label * 2.0) * px + 12 if self.pane_axes else 6
            bottom_px = self._axes_bottom_pt() * px + 3 if self.pane_axes else 6
            top_px = 6
            if self.pane_colorbar:
                cb_tick = max(7, ps.get("colorbar_tick_fontsize", 10) - 2)
                top_px += (COMPACT_CB_GAP_PT + COMPACT_CB_HEIGHT_PT + 2 + cb_tick * 1.4) * px + 2
            return {
                "pad_left": min(0.35, left_px / max(width, 1)),
                "pad_right": max(0.6, 1 - 8 / max(width, 1)),
                "pad_top": max(0.6, 1 - top_px / max(height, 1)),
                "pad_bottom": min(0.4, bottom_px / max(height, 1)),
            }
        tick = ps.get("axis_tick_fontsize", 10)
        label = ps.get("axis_label_fontsize", 12)
        title = ps.get("title_fontsize", 12)
        cb_tick = ps.get("colorbar_tick_fontsize", 10)
        cb_label = ps.get("colorbar_label_fontsize", 10)
        axes = self.figure.axes[0] if self.figure.axes else None
        title_lines = axes.get_title().count("\n") + 1 if axes is not None else 1
        top_px = title * 1.5 * title_lines * px + 10
        bottom_px = (tick * 1.4 + label * 1.6) * px + 12
        left_px = (tick * 0.65 * 8 + label * 2.0) * px + 12
        right_px = (cb_tick * 0.6 * 6 + cb_label * 1.6) * px + 10
        return {
            "pad_left": min(0.35, left_px / max(width, 1)),
            "pad_right": max(0.6, 1 - right_px / max(width, 1)),
            "pad_top": max(0.7, 1 - top_px / max(height, 1)),
            "pad_bottom": min(0.3, bottom_px / max(height, 1)),
        }

    def relayout_for_size(self):
        """Margins again for a canvas of a new size (the resize hook)."""
        if self.current_image_data is not None and self.figure.axes:
            self._apply_layout_settings(False)

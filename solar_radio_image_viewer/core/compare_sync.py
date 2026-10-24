"""
Keeping the panes of a compare page together (core/compare.py).

Panes follow each other, each way switchable in the popover of the
Synced button, among the panes linked (the toggle in a pane's header)
whose coordinate frame is the moved pane's (RA/Dec with RA/Dec, Solar XY
with Solar XY; the sky position is what is matched, so an image in the
other frame keeps its own view):

- zoom & pan: when a pane's view settles, the others show the same sky
  area (the same centre and angular extent);
- the cursor crosshair: the mouse in one pane draws a crosshair at the
  same sky position in every linked pane, and the inspector lists the
  value under it in each;
- frame stepping: next, previous, first or last in one pane steps every
  linked pane through its own folder (whatever its frame);
- the intensity range: a range given to one pane goes to the linked
  panes in the same unit;
- a region drawn in one pane is drawn at the same sky position in the
  panes following it, each with its own statistics.
"""

import logging
import math

import numpy as np
from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QColor, QPen
from PyQt5.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..tools.preview import contrast_colors, halo_pen, measure_preview
from ..tools.preview import halo as halo_effects
from ..ui.widgets import kicker, kicker_row, link_button, rule, toggle_row

logger = logging.getLogger(__name__)

LETTERS = "ABCD"
FRAME_NAMES = {"radec": "RA/Dec", "solar": "Solar XY"}
# The ways panes follow each other: key, the popover's words, on at first
SYNC_WAYS = (
    ("view", "Zoom & pan", True),
    ("cursor", "Cursor crosshair", True),
    ("frames", "Frame stepping", False),
    ("range", "Intensity range", False),
)


# ------------------------------------------------------------ sky positions
def _axes_wcs(pane):
    """(the image's axes, its 2D WCS or None), or (None, None) without an image."""
    nav = getattr(pane, "_nav_axes", None)
    ax = nav() if nav is not None else None
    if ax is None:
        return None, None
    wcs = getattr(ax, "wcs", None)
    if wcs is None or getattr(wcs, "naxis", 0) < 2:
        return ax, None
    return ax, wcs


def frame_kind(pane):
    """"radec", "solar", or None for an image without a sky frame."""
    _ax, wcs = _axes_wcs(pane)
    if wcs is None:
        return None
    ctype = str(wcs.wcs.ctype[0]).upper()
    if ctype.startswith("RA"):
        return "radec"
    if ctype.startswith("HPLN") or ctype.startswith("SOLAR"):
        return "solar"
    return None


def _unit_scales(wcs):
    """Degrees per world unit of the first two axes."""
    from astropy import units as u

    scales = []
    for cunit in list(wcs.wcs.cunit)[:2]:
        try:
            scales.append(float(u.Unit(str(cunit) or "deg").to(u.deg)))
        except Exception:
            scales.append(1.0)
    return scales


def to_world(pane, x, y):
    """The sky position (lon, lat) in degrees at pixel (x, y), or None."""
    _ax, wcs = _axes_wcs(pane)
    if wcs is None:
        return None
    try:
        lon, lat = wcs.pixel_to_world_values(x, y)
        sx, sy = _unit_scales(wcs)
        lon, lat = float(lon) * sx, float(lat) * sy
    except Exception:
        return None
    if not (math.isfinite(lon) and math.isfinite(lat)):
        return None
    return lon, lat


def to_pixel(pane, lon, lat):
    """The pixel (x, y) at the sky position (lon, lat) in degrees, or None."""
    _ax, wcs = _axes_wcs(pane)
    if wcs is None:
        return None
    try:
        sx, sy = _unit_scales(wcs)
        x, y = wcs.world_to_pixel_values(lon / sx, lat / sy)
        x, y = float(x), float(y)
    except Exception:
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    return x, y


def pixel_scales(pane):
    """Degrees per pixel along x and y, or None."""
    from astropy.wcs.utils import proj_plane_pixel_scales

    _ax, wcs = _axes_wcs(pane)
    if wcs is None:
        return None
    try:
        scales = proj_plane_pixel_scales(wcs)
        sx, sy = _unit_scales(wcs)
        return abs(float(scales[0])) * sx, abs(float(scales[1])) * sy
    except Exception:
        return None


def value_at(pane, x, y):
    """The image's value at pixel (x, y), or None off the image."""
    data = pane.current_image_data
    if data is None:
        return None
    ix, iy = int(round(x)), int(round(y))
    if 0 <= ix < data.shape[0] and 0 <= iy < data.shape[1]:
        value = data[ix, iy]
        return None if value is np.ma.masked else float(value)
    return None


def format_value(value, unit):
    if value is None or not math.isfinite(value):
        return "—"
    text = f"{value:.4g}"
    return f"{text} {unit}" if unit else text


class _Point:
    """A mouse event's data position, for the region handlers."""

    def __init__(self, xdata, ydata):
        self.xdata, self.ydata = xdata, ydata


# ---------------------------------------------------------------- the sync
class PaneSync:
    """What keeps a page's panes together; the page calls it as things
    happen in a pane."""

    def __init__(self, page):
        self.page = page
        self.on = {key: default for key, _words, default in SYNC_WAYS}
        self._busy = False

    # ------------------------------------------------------------ who
    def can_follow(self, pane, leader):
        """Whether ``pane`` follows ``leader``: both linked, with images in
        the same sky frame."""
        if pane is leader or not (pane.linked and leader.linked):
            return False
        if pane.current_image_data is None or leader.current_image_data is None:
            return False
        kind = frame_kind(leader)
        return kind is not None and frame_kind(pane) == kind

    def followers(self, leader):
        return [pane for pane in self.page.panes if self.can_follow(pane, leader)]

    def in_sync(self):
        """The panes in sync with the active pane, the active pane among them."""
        active = self.page.active_pane
        if active is None or not active.linked or active.current_image_data is None:
            return []
        return [pane for pane in self.page.panes if pane is active or self.can_follow(pane, active)]

    # ----------------------------------------------------------- view
    def view_settled(self, leader):
        """``leader``'s view is still: the panes following it show the same
        sky area."""
        if not self.on["view"] or self._busy or not getattr(leader, "linked", False):
            return
        ax = leader._nav_axes()
        if ax is None:
            return
        xlim, ylim = ax.get_xlim(), ax.get_ylim()
        synced = getattr(leader, "_synced_view", None)
        if synced is not None and np.allclose(synced, (xlim, ylim), rtol=0, atol=1e-6):
            return  # the view this pane was given: an echo, not a move
        world = to_world(leader, (xlim[0] + xlim[1]) / 2, (ylim[0] + ylim[1]) / 2)
        scales = pixel_scales(leader)
        if world is None or scales is None:
            return
        half_w = abs(xlim[1] - xlim[0]) * scales[0] / 2
        half_h = abs(ylim[1] - ylim[0]) * scales[1] / 2
        self._busy = True
        try:
            for pane in self.followers(leader):
                centre = to_pixel(pane, *world)
                pane_scales = pixel_scales(pane)
                pane_ax = pane._nav_axes()
                if centre is None or pane_scales is None or pane_ax is None:
                    continue
                cx, cy = centre
                hw, hh = half_w / pane_scales[0], half_h / pane_scales[1]
                if not (hw > 0 and hh > 0):
                    continue
                pane._stop_view_anim()
                pane._set_view(pane_ax, (cx - hw, cx + hw), (cy - hh, cy + hh))
                pane._synced_view = (pane_ax.get_xlim(), pane_ax.get_ylim())
        finally:
            self._busy = False

    # --------------------------------------------------------- cursor
    def cursor_moved(self, leader, x, y):
        """The mouse is at pixel (x, y) of ``leader``: the crosshair in the
        panes following it, and the value under it in each pane."""
        page = self.page
        world = to_world(leader, x, y)
        values = {}
        for pane in page.panes:
            if pane is leader:
                position = (x, y)
            elif world is not None and self.can_follow(pane, leader):
                position = to_pixel(pane, *world)
            else:
                position = None
            if position is None:
                self._hide_crosshair(pane)
                values[pane] = None
                continue
            if self.on["cursor"] and leader.linked and (pane is leader or pane.linked):
                self._show_crosshair(pane, *position)
            else:
                self._hide_crosshair(pane)
            values[pane] = value_at(pane, *position)
        page.cursor_values.show_values(values)

    def cursor_left(self):
        for pane in self.page.panes:
            self._hide_crosshair(pane)
        self.page.cursor_values.clear()

    def canvas_drawn(self, pane):
        """A pane drawn afresh: its crosshair (a picture over it) is stale."""
        self._hide_crosshair(pane)

    # --------------------------------------------------------- frames
    def frame_stepped(self, leader, kind):
        """``leader`` stepped to another file: the linked panes step the
        same way through their own folders."""
        if not self.on["frames"] or self._busy or not leader.linked:
            return
        self._busy = True
        try:
            for pane in self.page.panes:
                if pane is leader or not pane.linked or not pane._file_list:
                    continue
                getattr(pane, f"_on_{kind}_file")()
        finally:
            self._busy = False

    # ---------------------------------------------------------- range
    def range_changed(self, leader, vmin, vmax):
        """``leader`` is shown with this range: the linked panes in the
        same unit take it."""
        if not self.on["range"] or self._busy or not leader.linked:
            return
        unit = getattr(leader, "_current_bunit", None)
        self._busy = True
        try:
            for pane in self.page.panes:
                if pane is leader or not pane.linked or pane.current_image_data is None:
                    continue
                if getattr(pane, "_current_bunit", None) != unit:
                    continue
                shown = getattr(pane, "_last_norm_params", None)
                if shown is not None and np.allclose(shown[:2], (vmin, vmax), rtol=1e-9, atol=0):
                    continue
                pane.show_range(vmin, vmax)
                pane.show_range_preset(None)
                pane.update_display()
        finally:
            self._busy = False

    # -------------------------------------------------------- regions
    def region_drawn(self, leader, shape, corners):
        """A ``shape`` ("rect" or "ellipse") spanning ``corners`` (x1, y1,
        x2, y2) of ``leader``: drawn at the same sky position in the panes
        following it, each taking its statistics; the page's tables follow."""
        page = self.page
        if self._busy:
            return
        self._remove_mirror(leader)
        if leader.linked and None not in corners:
            x1, y1, x2, y2 = corners
            first, second = to_world(leader, x1, y1), to_world(leader, x2, y2)
            if first is not None and second is not None:
                self._busy = True
                try:
                    for pane in self.followers(leader):
                        p1, p2 = to_pixel(pane, *first), to_pixel(pane, *second)
                        if p1 is None or p2 is None:
                            continue
                        self._draw_mirror(pane, shape, p1, p2)
                        handler = pane.on_rectangle if shape == "rect" else pane.on_ellipse
                        handler(_Point(*p1), _Point(*p2))
                finally:
                    self._busy = False
        page.regions_changed()

    def _draw_mirror(self, pane, shape, p1, p2):
        """The region as a dashed outline on ``pane`` (its own selector,
        if any, goes out of sight)."""
        from matplotlib.patches import Ellipse, Rectangle

        ax = pane._nav_axes()
        if ax is None:
            return
        self._remove_mirror(pane)
        (x1, y1), (x2, y2) = p1, p2
        line, halo = contrast_colors(pane)
        style = dict(
            fill=False,
            edgecolor=line,
            linewidth=1.2,
            linestyle="--",
            path_effects=halo_effects(halo, 1.2),
        )
        if shape == "rect":
            patch = Rectangle((min(x1, x2), min(y1, y2)), abs(x2 - x1), abs(y2 - y1), **style)
        else:
            patch = Ellipse(((x1 + x2) / 2, (y1 + y2) / 2), abs(x2 - x1), abs(y2 - y1), **style)
        ax.add_patch(patch)
        pane._mirror_patch = patch
        selector = getattr(pane, "roi_selector", None)
        if selector is not None:
            selector.set_visible(False)
        pane.canvas.draw_idle()

    @staticmethod
    def _remove_mirror(pane):
        patch = getattr(pane, "_mirror_patch", None)
        pane._mirror_patch = None
        if patch is not None:
            try:
                patch.remove()
                pane.canvas.draw_idle()
            except Exception:
                pass  # (the figure was cleared since)

    def _show_crosshair(self, pane, x, y):
        if pane._in_measurement_mode():
            return  # the ruler or profile draws there
        ax = pane._nav_axes()
        if ax is None:
            return
        xlim, ylim = ax.get_xlim(), ax.get_ylim()
        line, halo = contrast_colors(pane)
        pen = QPen(QColor(line), 1.0, Qt.SolidLine, Qt.FlatCap)
        colour = pen.color()
        colour.setAlphaF(0.85)
        pen.setColor(colour)
        measure_preview(pane).show_lines(
            ax,
            [((x, ylim[0]), (x, ylim[1]), pen), ((xlim[0], y), (xlim[1], y), pen)],
            halo_pen(halo, 1.0),
        )
        pane._crosshair_shown = True

    def _hide_crosshair(self, pane):
        if getattr(pane, "_crosshair_shown", False):
            pane._crosshair_shown = False
            if not pane._in_measurement_mode():
                measure_preview(pane).end()


# ------------------------------------------------------------- the widgets
class SyncPopover(QFrame):
    """The Synced button's popover: the ways panes follow each other,
    which panes are in sync, Sync all."""

    def __init__(self, page, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("Card")
        self._page = page
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        layout.addWidget(kicker("Panes follow each other on"))
        self.switches = {}
        for key, words, _default in SYNC_WAYS:
            switch = QCheckBox()
            switch.setObjectName("Switch")
            switch.setChecked(page.sync.on[key])
            switch.toggled.connect(lambda on, key=key: page.set_sync(key, on))
            self.switches[key] = switch
            layout.addWidget(toggle_row(switch, label=QLabel(words)))
        layout.addSpacing(2)
        layout.addWidget(rule())
        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        foot.setSpacing(6)
        in_sync = QLabel("In sync:")
        in_sync.setObjectName("Hint")
        foot.addWidget(in_sync)
        self._badges = QHBoxLayout()
        self._badges.setSpacing(4)
        foot.addLayout(self._badges)
        foot.addStretch()
        self.all_button = link_button("Sync all", page.sync_all)
        foot.addWidget(self.all_button)
        layout.addLayout(foot)

    def refresh(self):
        """The badges of the panes in sync, and whether Sync all has work."""
        while self._badges.count():
            widget = self._badges.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        page = self._page
        letters = [LETTERS[page.panes.index(pane)] for pane in page.sync.in_sync()]
        for letter in letters:
            badge = QLabel(letter)
            badge.setObjectName("PaneBadge")
            badge.setProperty("letter", letter)
            self._badges.addWidget(badge)
        if not letters:
            none = QLabel("none")
            none.setObjectName("Meta")
            self._badges.addWidget(none)
        for key, switch in self.switches.items():
            switch.blockSignals(True)
            switch.setChecked(page.sync.on[key])
            switch.blockSignals(False)
        self.all_button.setEnabled(any(not pane.linked for pane in page.panes))

    def show_above(self, button):
        self.refresh()
        self.adjustSize()
        self.move(button.mapToGlobal(QPoint(0, -self.sizeHint().height() - 6)))
        self.show()


class CursorValues(QFrame):
    """Pinned under the cursor readout while there is more than one pane:
    the value under the cursor in each pane."""

    def __init__(self, page, parent=None):
        super().__init__(parent)
        self.setObjectName("Pinned")
        self._page = page
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 10)
        layout.setSpacing(6)
        row, self.note = kicker_row("Values at cursor", "")
        layout.addWidget(row)
        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(12)
        self._grid.setVerticalSpacing(3)
        layout.addLayout(self._grid)
        self._cells = {}
        self.hide()

    def rebuild(self):
        """A cell per pane: its badge, what it shows, the value."""
        while self._grid.count():
            widget = self._grid.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._cells = {}
        from .compare import pane_caption

        for index, pane in enumerate(self._page.panes):
            cell = QWidget()
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(6)
            badge = QLabel(LETTERS[index])
            badge.setObjectName("PaneBadge")
            badge.setProperty("letter", LETTERS[index])
            cell_layout.addWidget(badge)
            caption = QLabel((pane_caption(pane).split(" · ")[0] or "")[:14])
            caption.setObjectName("Meta")
            cell_layout.addWidget(caption)
            cell_layout.addStretch()
            value = QLabel("—")
            value.setObjectName("Mono")
            cell_layout.addWidget(value)
            self._grid.addWidget(cell, index // 2, index % 2)
            self._cells[pane] = value
        self._grid.setColumnStretch(0, 1)
        self._grid.setColumnStretch(1, 1)
        self.clear()

    def show_values(self, values):
        """``values``: pane -> value (None off that image or out of sync)."""
        for pane, label in self._cells.items():
            label.setText(
                format_value(values.get(pane), getattr(pane, "_current_bunit", "") or "")
            )
        self._note_sync()

    def clear(self):
        for label in self._cells.values():
            label.setText("—")
        self._note_sync()

    def _note_sync(self):
        page = self._page
        out = [
            LETTERS[page.panes.index(pane)]
            for pane in page.panes
            if pane not in page.sync.in_sync()
        ]
        self.note.setText(f"{', '.join(out)} not in sync" if out else "")


def sync_button(page):
    """The canvas bar's Synced button, opening the popover."""
    button = QPushButton()
    button.setObjectName("NavButton")
    button.setToolTip("How the panes follow each other, and which do")
    # Never squeezed to a sliver in a crowded bar: room for the icon and A B C D
    button.setMinimumWidth(button.fontMetrics().horizontalAdvance("A B C D") + 40)
    button.clicked.connect(lambda: page.sync_popover.show_above(button))
    return button

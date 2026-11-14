"""
The Annotate tool: text, arrows and shapes put on the image with the mouse,
and changed there.

With the tool on (the pencil in the bar under the image, or A), a strip at
the image's corner says what a press on the image draws: a click places
text, typed where it goes; a drag draws an arrow or a shape. A click on an
annotation selects it: it is dragged to move it, by its handles to resize
or turn it, double-clicked to change it (a text's words where they are,
anything else in the properties window), and Delete removes it. Every
change can be undone.

What an annotation is is in ``tools/annotation_model.py``; what the tool
shows in ``tools/annotation_ui.py``; how annotations are drawn in
``shape_annotations.py``.
"""

import copy
import json
import logging
import math
import time

from matplotlib.backends.backend_agg import FigureCanvasAgg
from PyQt5.QtCore import QSettings, Qt
from PyQt5.QtGui import QCursor
from PyQt5.QtWidgets import QMenu, QMessageBox

from .annotation_model import (
    ARROW,
    DEFAULT_COLOR,
    EVEN,
    LISTS,
    SHAPE,
    SOLAR_RADII,
    TEXT,
    TOOL_NAMES,
    TOOLS,
    TURNS,
    describe,
    moved,
    new_arrow,
    new_shape,
    new_text,
    set_shape_box,
    shape_angle,
    shape_box,
    shape_outline,
    snapshot,
    style_of,
)
from .geometry import (
    HANDLES,
    even_box,
    inside,
    outline_distance,
    resized_box,
    segment_distance,
    snapped_end,
    turned,
    turned_angle,
)

logger = logging.getLogger(__name__)

# How many changes can be undone
HISTORY = 100
# A press this soon after typed text was kept by clicking away from it only
# ends the typing (seconds)
TYPING_ENDED_S = 0.3
HINT = (
    "Annotate: click to place text, or drag to draw an arrow or a shape; click an annotation "
    "to select it, double-click to change it"
)

# What the next annotation is drawn with and looks like: the same in every
# pane and tab, and kept between sessions
_SETTINGS_KEY = "annotations/next"
_shared = {"tool": "text", "color": DEFAULT_COLOR, "styles": {TEXT: {}, ARROW: {}, SHAPE: {}}}
_shared_read = False


def shared():
    """The tool and the look of the next annotation, read from the settings
    the first time."""
    global _shared_read
    if not _shared_read:
        _shared_read = True
        try:
            kept = json.loads(QSettings("SolarViewer", "SolarViewer").value(_SETTINGS_KEY, "") or "{}")
            if kept.get("tool") in TOOLS:
                _shared["tool"] = kept["tool"]
            if isinstance(kept.get("color"), str) and kept["color"]:
                _shared["color"] = kept["color"]
            for kind in (TEXT, ARROW, SHAPE):
                if isinstance(kept.get("styles", {}).get(kind), dict):
                    _shared["styles"][kind] = kept["styles"][kind]
        except Exception:
            logger.debug("The annotation settings could not be read", exc_info=True)
    return _shared


def _keep_shared():
    try:
        QSettings("SolarViewer", "SolarViewer").setValue(_SETTINGS_KEY, json.dumps(_shared))
    except Exception:
        logger.debug("The annotation settings could not be kept", exc_info=True)


def next_style(kind):
    """The look of the next annotation of ``kind``."""
    state = shared()
    return {**state["styles"][kind], "color": state["color"]}


class AnnotationsMixin:
    """A pane's annotations and the Annotate tool on its canvas.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``annotate_action``, ``arrow_annotations``, ``canvas``, ``_data_in_axes``,
    ``figure``, ``_nav_axes``, ``region_tool_changed``, ``shape_annotations``,
    ``share_tool``, ``show_status_message``, ``text_annotations``
    """

    annotating = False  # whether the Annotate tool is the one in use
    selected_annotation = None  # (kind, annotation)
    _annot_press_at = None
    _annot_live = None
    _annot_bar = None
    _annot_editor = None
    _annot_properties = None
    _annotation_layer = None

    def init_annotations(self):
        """The tool's layer over the canvas, and its ears on the mouse."""
        from .annotation_ui import AnnotationLayer

        self._annotation_layer = AnnotationLayer(self)
        self._annot_undo, self._annot_redo = [], []
        self._annot_editing = None  # the text annotation being typed again, or None for a new one
        self.canvas.mpl_connect("button_press_event", self._annot_press)
        self.canvas.mpl_connect("motion_notify_event", self._annot_motion)
        self.canvas.mpl_connect("button_release_event", self._annot_release)
        self.canvas.mpl_connect("key_press_event", self._annot_key)

    # ------------------------------------------------------------------
    # What the pane holds
    # ------------------------------------------------------------------

    def annotation_lists(self):
        return {kind: getattr(self, name) for kind, name in LISTS.items()}

    def annotation_count(self):
        return sum(len(items) for items in self.annotation_lists().values())

    def pixels_per_arcsec(self):
        """How many pixels of the image an arcsec is (1 without a
        coordinate system: sizes in arcsec are then taken as pixels)."""
        from ..shape_annotations import _get_pixel_scale_deg

        degrees = _get_pixel_scale_deg(self)
        return 1.0 if not degrees else 1.0 / (degrees * 3600.0)

    def add_text_annotation(self, x, y, text, **style):
        """Add text at (x, y), in pixels of the image; returns it."""
        annotation = new_text(x, y, text, {**next_style(TEXT), **style})
        return self._add_annotation(TEXT, annotation)

    def add_arrow_annotation(self, x1, y1, x2, y2, **style):
        """Add an arrow from (x1, y1) to its head at (x2, y2); returns it."""
        annotation = new_arrow(x1, y1, x2, y2, {**next_style(ARROW), **style})
        return self._add_annotation(ARROW, annotation)

    def add_shape_annotation(self, shape_type, corners, **style):
        """Add a ``shape_type`` in the box ``corners``; returns it."""
        annotation = new_shape(shape_type, corners, {**next_style(SHAPE), **style})
        return self._add_annotation(SHAPE, annotation)

    def _add_annotation(self, kind, annotation, select=False):
        self.annotation_checkpoint()
        getattr(self, LISTS[kind]).append(annotation)
        if select and self.annotating:
            self.selected_annotation = (kind, annotation)
        self._annot_changed()
        return annotation

    def add_radius_circle(self, multiplier):
        """A dashed circle ``multiplier`` solar radii round the Sun's centre."""
        from ..shape_annotations import solar_radius_circle

        shape = solar_radius_circle(self, multiplier)
        if shape is None:
            if self.current_image_data is not None:
                self.show_status_message("The Sun is not on this image's part of the sky: no circle was added")
            return None
        self._add_annotation(SHAPE, shape, select=True)
        self.show_status_message(
            f"Circle at {multiplier:g} R☉ added"
            + ("" if self.annotating else ". The Annotate tool (A) moves, changes or deletes it")
        )
        return shape

    # ------------------------------------------------------------------
    # Drawing them
    # ------------------------------------------------------------------

    def redraw_annotations(self):
        """The annotations drawn again as they are now; the image under
        them is not."""
        ax = self._nav_axes()
        if ax is None or self.current_image_data is None:
            return
        from ..shape_annotations import draw_annotations

        draw_annotations(self, ax)
        # (what the figure shows now: a plot with nothing else changed
        # need not draw it all again)
        state = getattr(self, "_last_rendered_state", None)
        if isinstance(state, dict):
            for name in LISTS.values():
                state[name] = str(getattr(self, name))
        self.canvas.draw_idle()

    def _annot_changed(self, by=None):
        """An annotation was added, changed or removed."""
        self.redraw_annotations()
        self._annot_refresh(by)

    def _annot_refresh(self, by=None):
        """The handles, the strip and the properties window, as the
        selection and the annotations are now (``by``: who changed them,
        and so knows)."""
        if self._annotation_layer is not None:
            self._annotation_layer.refresh()
        bar = self._annot_bar
        if bar is not None:
            selected = self.selected_annotation
            bar.set_selected(selected is not None)
            bar.set_color(selected[1].get("color", shared()["color"]) if selected else shared()["color"])
            bar.set_history(bool(self._annot_undo), bool(self._annot_redo), bool(self.annotation_count()))
            if bar.isVisible():
                bar.raise_()
        properties = self._annot_properties
        if properties is not None and properties is not by:
            properties.bind(self.selected_annotation)

    def annotation_text_box(self, annotation):
        """Where a text annotation is drawn, (x0, y0, x1, y1) in
        Matplotlib's display pixels; None if it is not drawn."""
        from ..shape_annotations import annotation_artists

        ax = self._nav_axes()
        if ax is None:
            return None
        for artist in annotation_artists(ax, annotation):
            try:
                box = artist.get_window_extent(self.canvas.get_renderer())
            except Exception:
                return None
            pad = 0.0
            if annotation.get("background"):
                # (its ground's padding: 0.3 of the letters' height)
                pad = 0.3 * float(annotation.get("fontsize", 12)) * self.figure.dpi / 72
            return (box.x0 - pad, box.y0 - pad, box.x1 + pad, box.y1 + pad)
        return None

    # ------------------------------------------------------------------
    # Undo
    # ------------------------------------------------------------------

    def annotation_checkpoint(self):
        """Remember the annotations as they are, before a change to them."""
        self._annot_undo.append(snapshot(self.annotation_lists()))
        del self._annot_undo[:-HISTORY]
        self._annot_redo.clear()

    def forget_annotation_history(self):
        """The annotations were placed afresh (another image, another
        frame): what they were before is no longer where to go back to."""
        self._annot_undo.clear()
        self._annot_redo.clear()

    def _annot_selected_index(self):
        selected = self.selected_annotation
        if selected is None:
            return None
        kind, annotation = selected
        for index, item in enumerate(getattr(self, LISTS[kind])):
            if item is annotation:
                return kind, index
        return None

    def _annot_restore(self, kept):
        index = self._annot_selected_index()
        for kind, name in LISTS.items():
            getattr(self, name)[:] = copy.deepcopy(kept[kind])
        self.selected_annotation = None
        if index is not None and index[1] < len(getattr(self, LISTS[index[0]])):
            self.selected_annotation = (index[0], getattr(self, LISTS[index[0]])[index[1]])

    def undo_annotation(self):
        if not self._annot_undo:
            self.show_status_message("Nothing to undo")
            return
        self._annot_redo.append(snapshot(self.annotation_lists()))
        self._annot_restore(self._annot_undo.pop())
        self._annot_changed()
        self.show_status_message("Annotation change undone")

    def redo_annotation(self):
        if not self._annot_redo:
            self.show_status_message("Nothing to redo")
            return
        self._annot_undo.append(snapshot(self.annotation_lists()))
        self._annot_restore(self._annot_redo.pop())
        self._annot_changed()
        self.show_status_message("Annotation change redone")

    # ------------------------------------------------------------------
    # The tool
    # ------------------------------------------------------------------

    @property
    def annotation_tool(self):
        """What a press on the image draws: text, arrow, rectangle or ellipse."""
        return shared()["tool"]

    def set_annotation_tool(self, name):
        """Turn the Annotate tool on, drawing ``name``."""
        if name not in TOOLS:
            return
        shared()["tool"] = name
        _keep_shared()
        if not self.annotating:
            self.annotate_action.setChecked(True)  # (-> _toggle_annotate_mode)
        else:
            self._show_annotation_bar()
            self.show_status_message(self._tool_hint())

    def _tool_hint(self):
        return {
            "text": "Text: click on the image where it goes, and type",
            "arrow": "Arrow: drag from its tail to its head",
            "rectangle": "Rectangle: drag to draw one (Shift: a square)",
            "ellipse": "Ellipse: drag to draw one (Shift: a circle)",
        }[self.annotation_tool] + "; click an annotation to select it, double-click to change it"

    def _toggle_annotate_mode(self, checked):
        self.annotating = checked
        self.region_tool_changed()  # (the regions are left be meanwhile)
        if checked:
            self._show_annotation_bar()
            self.canvas.setCursor(Qt.CrossCursor)
            page = getattr(self, "page", None)
            if page is None or page.active_pane is self:  # (not a pane that only takes the page's tool)
                self.canvas.setFocus()  # (for Delete, Enter and Esc)
                self.show_status_message(self._tool_hint() if self.current_image_data is not None else HINT)
            self.share_tool()  # (on a compare page, every pane's)
        else:
            self._end_typing()
            self._annot_press_at = None
            self._annot_live = None
            self.selected_annotation = None
            if self._annot_properties is not None:
                self._annot_properties.close()
        self.refresh_annotation_bar()
        self._annot_refresh()

    def _annotation_bar(self):
        if self._annot_bar is None:
            from .annotation_ui import AnnotationBar

            bar = self._annot_bar = AnnotationBar(self.canvas)
            bar.tool_chosen.connect(self.set_annotation_tool)
            bar.color_chosen.connect(self.set_annotation_color)
            bar.properties_asked.connect(self.show_annotation_properties)
            bar.delete_asked.connect(self.delete_annotation)
            bar.radius_asked.connect(self.add_radius_circle)
            bar.undo_asked.connect(self.undo_annotation)
            bar.redo_asked.connect(self.redo_annotation)
            bar.clear_asked.connect(self.clear_annotations)
        return self._annot_bar

    def _show_annotation_bar(self):
        bar = self._annotation_bar()
        bar.set_tool(self.annotation_tool)
        self.refresh_annotation_bar()

    def refresh_annotation_bar(self):
        """The strip shows while the tool is on: on a compare page, on the
        active pane alone."""
        bar = self._annot_bar
        if bar is None:
            return
        page = getattr(self, "page", None)
        wanted = self.annotating and (page is None or page.active_pane is self)
        if wanted:
            bar.set_tool(self.annotation_tool)
            bar.place()
        bar.setVisible(wanted)

    def annotation_overlays(self):
        """The tool's widgets over the canvas: kept out of pictures of it."""
        return [w for w in (self._annotation_layer, self._annot_bar) if w is not None]

    # ------------------------------------------------------------------
    # Selecting and changing
    # ------------------------------------------------------------------

    def select_annotation(self, kind=None, annotation=None):
        self.selected_annotation = (kind, annotation) if annotation is not None else None
        self._annot_refresh()

    def delete_annotation(self, selected=None):
        """Delete the selected annotation."""
        selected = selected or self.selected_annotation
        if selected is None:
            self.show_status_message("No annotation is selected")
            return
        kind, annotation = selected
        items = getattr(self, LISTS[kind])
        if not any(item is annotation for item in items):
            return
        self.annotation_checkpoint()
        items[:] = [item for item in items if item is not annotation]
        if self.selected_annotation is not None and self.selected_annotation[1] is annotation:
            self.selected_annotation = None
        self._annot_changed()
        self.show_status_message(f"{describe(kind, annotation)} deleted (Ctrl+Z brings it back)")

    def duplicate_annotation(self):
        selected = self.selected_annotation
        ax = self._nav_axes()
        if selected is None or ax is None:
            return
        kind, annotation = selected
        twin = copy.deepcopy(annotation)
        # A little to the right and down on screen
        dpr = self.canvas.device_pixel_ratio
        (x0, y0), (x1, y1) = ax.transData.inverted().transform([(0, 0), (14 * dpr, -14 * dpr)])
        moved(kind, twin, x1 - x0, y1 - y0)
        self.annotation_checkpoint()
        getattr(self, LISTS[kind]).append(twin)
        self.selected_annotation = (kind, twin)
        self._annot_changed()

    def clear_annotations(self):
        """Delete every annotation of this image, once the user agrees."""
        count = self.annotation_count()
        if not count:
            self.show_status_message("There are no annotations on this image")
            return
        if QMessageBox.question(
            self,
            "Delete annotations",
            f"Delete {'the annotation' if count == 1 else f'all {count} annotations'} on this image?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        self.annotation_checkpoint()
        for name in LISTS.values():
            getattr(self, name).clear()
        self.selected_annotation = None
        self._annot_changed()
        self.show_status_message("Annotations deleted (Ctrl+Z brings them back)")

    def set_annotation_color(self, color):
        """``color`` for the selected annotation, and for the next ones."""
        shared()["color"] = color
        _keep_shared()
        selected = self.selected_annotation
        if selected is not None and selected[1].get("color") != color:
            self.annotation_checkpoint()
            selected[1]["color"] = color
            self._annot_changed()
        else:
            self._annot_refresh()

    def annotation_edited(self, kind, annotation, by=None):
        """``annotation`` was changed in the properties window: it is drawn
        so, and the next of its kind will look like it."""
        state = shared()
        state["styles"][kind] = style_of(kind, annotation)
        if annotation.get("color"):
            state["color"] = annotation["color"]
        _keep_shared()
        self._annot_changed(by=by)

    def edit_annotation(self):
        """Change the selected annotation: a text's words where they are,
        anything else in the properties window."""
        selected = self.selected_annotation
        if selected is None:
            self.show_status_message("Click an annotation first, to select it")
        elif selected[0] == TEXT:
            self._type_text(selected[1]["x"], selected[1]["y"], selected[1])
        else:
            self.show_annotation_properties()

    def show_annotation_properties(self):
        from .annotation_ui import AnnotationProperties

        window = self._annot_properties
        if window is None:
            window = self._annot_properties = AnnotationProperties(self.window(), self)
            window.destroyed.connect(lambda: setattr(self, "_annot_properties", None))
        window.bind(self.selected_annotation)
        window.show()
        window.raise_()
        window.activateWindow()

    # ------------------------------------------------------------------
    # Typing text on the image
    # ------------------------------------------------------------------

    def _type_text(self, x, y, existing=None):
        """Show the field to type text at (x, y), pixels of the image: a
        new one, or ``existing`` again."""
        from ..shape_annotations import annotation_artists
        from .annotation_ui import TextEditor

        ax = self._nav_axes()
        if ax is None:
            return
        if self._annot_editor is None:
            self._annot_editor = TextEditor(self.canvas)
            self._annot_editor.finished.connect(self._typing_finished)
        editor = self._annot_editor
        editor.finish(True)
        look = existing if existing is not None else {**new_text(0, 0, ""), **next_style(TEXT)}
        self._annot_editing = existing
        self._annot_typing_at = (float(x), float(y))
        if existing is not None:
            # Its drawing goes while it is typed over
            for artist in annotation_artists(ax, existing):
                artist.set_visible(False)
            self.canvas.draw_idle()
        dpr = self.canvas.device_pixel_ratio
        (at,) = self._annotation_layer.to_widget([(x, y)])
        editor.start(
            at,
            existing["text"] if existing is not None else "",
            float(look.get("fontsize", 12)) * self.figure.dpi / 72 / dpr,
            bold=look.get("fontweight") == "bold",
            italic=look.get("fontstyle") == "italic",
        )

    def _end_typing(self, keep=True):
        if self._annot_editor is not None:
            self._annot_editor.finish(keep)

    def _typing_finished(self, keep):
        editor = self._annot_editor
        self._annot_typing_ended = time.monotonic()
        words = editor.text().strip()
        existing, self._annot_editing = self._annot_editing, None
        if existing is None:
            if keep and words:
                x, y = self._annot_typing_at
                annotation = new_text(x, y, words, next_style(TEXT))
                self.annotation_checkpoint()
                self.text_annotations.append(annotation)
                self.selected_annotation = (TEXT, annotation)
                self.show_status_message(
                    "Text added: drag it to move it, double-click to change it, Delete removes it"
                )
        elif keep and not words:
            self.delete_annotation((TEXT, existing))
        elif keep and words != existing.get("text"):
            self.annotation_checkpoint()
            existing["text"] = words
        self._annot_changed()
        if self.annotating:
            self.canvas.setFocus()

    # ------------------------------------------------------------------
    # Picking with the mouse
    # ------------------------------------------------------------------

    def _annot_tolerance(self):
        from .roi_stats import GRAB_PX

        return GRAB_PX * self.canvas.device_pixel_ratio

    def _annot_display(self, points):
        return [tuple(p) for p in self._nav_axes().transData.transform(points)]

    def _annot_hit(self, kind, annotation, point, tolerance):
        """Whether ``point`` (display pixels) is on ``annotation``."""
        if kind == ARROW:
            tail, head = self._annot_display(
                [(annotation["x1"], annotation["y1"]), (annotation["x2"], annotation["y2"])]
            )
            return segment_distance(point, tail, head) <= tolerance
        if kind == TEXT:
            box = self.annotation_text_box(annotation)
            if box is None:
                return False
            x0, y0, x1, y1 = box
            return x0 - 3 <= point[0] <= x1 + 3 and y0 - 3 <= point[1] <= y1 + 3
        outline = self._annot_display(shape_outline(annotation, self.pixels_per_arcsec()))
        if outline_distance(point, outline) <= tolerance:
            return True
        return bool(annotation.get("fill")) and inside(point, outline)

    def _annot_at(self, event):
        """The annotation under the mouse, the one drawn on top first:
        (kind, annotation) or None."""
        if event.x is None or self._nav_axes() is None:
            return None
        point, tolerance = (event.x, event.y), self._annot_tolerance()
        for kind in (ARROW, TEXT, SHAPE):
            for annotation in reversed(getattr(self, LISTS[kind])):
                try:
                    if self._annot_hit(kind, annotation, point, tolerance):
                        return kind, annotation
                except Exception:
                    logger.debug("An annotation could not be picked", exc_info=True)
        return None

    def _annot_grab(self, event):
        """What of the selected annotation the mouse at ``event`` would
        take: ("end", 0 or 1) of an arrow, ("handle", index) or
        ("turn", None) of a shape, ("move", None), or None."""
        from .roi_stats import GRAB_PX

        selected = self.selected_annotation
        if selected is None or event.x is None or self._nav_axes() is None:
            return None
        kind, annotation = selected
        point, tolerance = (event.x, event.y), self._annot_tolerance()
        if kind == ARROW:
            ends = self._annot_display(
                [(annotation["x1"], annotation["y1"]), (annotation["x2"], annotation["y2"])]
            )
            for index in (1, 0):  # (the head first, where they meet)
                if math.hypot(ends[index][0] - point[0], ends[index][1] - point[1]) <= tolerance + 2:
                    return ("end", index)
        elif kind == SHAPE:
            layer = self._annotation_layer
            corners, angle = shape_box(annotation, self.pixels_per_arcsec()), shape_angle(annotation)
            mouse = layer.from_display(*point)
            if annotation.get("type") in TURNS:
                knob, _foot = layer.turn_handle(corners, angle)
                if math.hypot(knob.x() - mouse.x(), knob.y() - mouse.y()) <= GRAB_PX + 2:
                    return ("turn", None)
            even = annotation.get("type") in EVEN
            near = [
                (math.hypot(at.x() - mouse.x(), at.y() - mouse.y()), index)
                for index, at in layer.shown_handles(corners, angle)
                if not even or index < 4
            ]
            if near and min(near)[0] <= GRAB_PX:
                return ("handle", min(near)[1])
        if self._annot_hit(kind, annotation, point, tolerance):
            return ("move", None)
        return None

    def annotation_cursor(self, event):
        """The cursor for the mouse at ``event`` with the Annotate tool;
        None with another tool."""
        if not self.annotating:
            return None
        press = self._annot_press_at
        grab = press["grab"] if press is not None else self._annot_grab(event)
        if grab is None:
            if press is None and self._annot_at(event) is not None:
                return Qt.PointingHandCursor
            return Qt.IBeamCursor if self.annotation_tool == "text" else Qt.CrossCursor
        if grab[0] == "move":
            return Qt.SizeAllCursor
        if grab[0] in ("end", "draw"):
            return Qt.CrossCursor
        if grab[0] == "turn":
            from .roi_stats import turn_cursor

            return turn_cursor(self.canvas.device_pixel_ratio)
        # The way the handle pulls on screen (an axis may run backwards)
        shape = self.selected_annotation[1]
        hx, hy = HANDLES[grab[1]]
        pull = (0 if hx is None else 2 * hx - 1, 0 if hy is None else 2 * hy - 1)
        ((dx, dy),) = turned([pull], (0.0, 0.0), shape_angle(shape))
        cx, cy = shape["center_x"], shape["center_y"]
        (px0, py0), (px1, py1) = self._annot_display([(cx, cy), (cx + dx, cy + dy)])
        way = math.degrees(math.atan2(py1 - py0, px1 - px0)) % 180
        return (Qt.SizeHorCursor, Qt.SizeBDiagCursor, Qt.SizeVerCursor, Qt.SizeFDiagCursor)[
            int((way + 22.5) // 45) % 4
        ]

    # ------------------------------------------------------------------
    # The mouse and the keys
    # ------------------------------------------------------------------

    def _annot_press(self, event):
        ax = self._nav_axes()
        if not self.annotating or ax is None or event.inaxes is not ax or self.current_image_data is None:
            return
        typing_ended = time.monotonic() - getattr(self, "_annot_typing_ended", 0.0) < TYPING_ENDED_S
        if self._annot_editor is not None and self._annot_editor.active():
            self._end_typing()
            typing_ended = True
        if event.button == 3:
            # (a right drag zooms to a box: the menu is for a click)
            self._annot_menu_press = (event.x, event.y)
            return
        if event.button != 1:
            return
        grab = self._annot_grab(event)
        if grab is None:
            hit = self._annot_at(event)
            if hit is not None:
                self.select_annotation(*hit)
                grab = ("move", None)
        if event.dblclick:
            if grab is not None:
                self.edit_annotation()
            return
        self._annot_press_at = dict(
            x=event.x,
            y=event.y,
            data=(event.xdata, event.ydata),
            grab=grab,
            dragging=False,
            only_ends_typing=typing_ended and grab is None,
        )

    def _annot_begin_drag(self, press):
        """The first move of a drag: on nothing, a new arrow or shape is
        begun; on the selected annotation, it is about to change. False if
        the drag is of nothing."""
        scale = self.pixels_per_arcsec()
        if press["grab"] is None:
            kind, shape_type = TOOLS[self.annotation_tool]
            if kind == TEXT or press["only_ends_typing"]:
                return False
            x0, y0 = press["data"]
            self.annotation_checkpoint()
            if kind == ARROW:
                annotation = new_arrow(x0, y0, x0, y0, next_style(ARROW))
            else:
                annotation = new_shape(shape_type, (x0, y0, x0, y0), next_style(SHAPE))
            getattr(self, LISTS[kind]).append(annotation)
            self.selected_annotation = (kind, annotation)
            press["grab"] = ("draw", None)
        else:
            self.annotation_checkpoint()
        kind, annotation = self.selected_annotation
        press["start"] = copy.deepcopy(annotation)
        if kind == SHAPE:
            press["box"] = (shape_box(annotation, scale), shape_angle(annotation))
        return True

    def _annot_motion(self, event):
        press = self._annot_press_at
        if press is None or event.x is None or not self.annotating:
            return
        from .roi_stats import CLICK_PX, shift_held

        if not press["dragging"]:
            if max(abs(event.x - press["x"]), abs(event.y - press["y"])) <= CLICK_PX:
                return
            if not self._annot_begin_drag(press):
                return
            press["dragging"] = True
        selected = self.selected_annotation
        if selected is None:
            return  # (deleted meanwhile)
        kind, annotation = selected
        x, y = (float(v) for v in self._data_in_axes(event))
        x0, y0 = press["data"]
        shift = shift_held(event)
        grab, start, scale = press["grab"], press["start"], self.pixels_per_arcsec()
        if grab[0] == "move":
            annotation.update(copy.deepcopy(start))
            moved(kind, annotation, x - x0, y - y0)
        elif grab[0] == "end" or (grab[0] == "draw" and kind == ARROW):
            index = 1 if grab[0] == "draw" else grab[1]
            other = (start["x1"], start["y1"]) if index == 1 else (start["x2"], start["y2"])
            if shift:
                x, y = snapped_end(other[0], other[1], x, y)
            annotation[f"x{index + 1}"], annotation[f"y{index + 1}"] = x, y
        elif grab[0] == "draw":
            box = even_box(x0, y0, x, y) if shift else (x0, y0, x, y)
            set_shape_box(annotation, box, pixels_per_arcsec=scale)
        elif grab[0] == "turn":
            corners, angle = press["box"]
            annotation["angle"] = turned_angle(corners, angle, (x0, y0), (x, y), snap=shift)
        else:
            corners, angle = press["box"]
            even = shift or annotation.get("type") in EVEN
            set_shape_box(annotation, resized_box(corners, grab[1], x, y, angle, even), pixels_per_arcsec=scale)
        self._annot_draw_live(kind, annotation)
        self._annotation_layer.refresh()

    def _annot_release(self, event):
        if event.button == 3:
            self._annot_menu_release(event)
            return
        press, self._annot_press_at = self._annot_press_at, None
        if press is None or event.button != 1 or not self.annotating:
            return
        if press["dragging"]:
            self._annot_live = None
            kind, annotation = self.selected_annotation or (None, None)
            drawn = press["grab"][0] == "draw"
            if annotation is None:
                pass
            elif drawn and not self._annot_big_enough(kind, annotation):
                # Too small to be meant: as if nothing had been drawn
                self._annot_restore(self._annot_undo.pop())
                self.selected_annotation = None
            elif annotation == press["start"]:
                self._annot_undo.pop()  # (moved back to where it was)
            elif drawn:
                self.show_status_message(
                    f"{describe(kind, annotation)} added: drag it or its handles, double-click "
                    "for its properties, Delete removes it"
                )
            self._annot_changed()
            return
        if press["grab"] is not None:
            kind, annotation = self.selected_annotation
            self.show_status_message(
                f"{describe(kind, annotation)} selected: drag it to move it"
                + (", its handles to resize it" if kind == SHAPE else ", its ends to point it" if kind == ARROW else "")
                + "; double-click to change it, Delete removes it, Esc deselects"
            )
        elif press["only_ends_typing"]:
            pass
        elif self.annotation_tool == "text":
            self.select_annotation(None)
            self._type_text(*press["data"])
        else:
            if self.selected_annotation is not None:
                self.select_annotation(None)
            self.show_status_message(self._tool_hint())

    def _annot_big_enough(self, kind, annotation):
        """Whether a drawn arrow or shape is more than a click across on
        screen."""
        from .roi_stats import CLICK_PX

        if kind == ARROW:
            (x1, y1), (x2, y2) = self._annot_display(
                [(annotation["x1"], annotation["y1"]), (annotation["x2"], annotation["y2"])]
            )
            return math.hypot(x2 - x1, y2 - y1) > 2 * CLICK_PX
        x1, y1, x2, y2 = shape_box(annotation, self.pixels_per_arcsec())
        (px1, py1), (px2, py2) = self._annot_display([(x1, y1), (x2, y2)])
        return min(abs(px2 - px1), abs(py2 - py1)) > CLICK_PX

    def _annot_menu_release(self, event):
        """A right click (not a drag, which zooms) on an annotation: its menu."""
        from .roi_stats import CLICK_PX

        pressed, self._annot_menu_press = getattr(self, "_annot_menu_press", None), None
        if not self.annotating or pressed is None or event.x is None:
            return
        if max(abs(event.x - pressed[0]), abs(event.y - pressed[1])) > CLICK_PX:
            return
        hit = self._annot_at(event)
        if hit is None:
            return
        self.select_annotation(*hit)
        menu = QMenu(self.canvas)
        if hit[0] == TEXT:
            menu.addAction("Edit text").triggered.connect(self.edit_annotation)
        menu.addAction("Properties…").triggered.connect(self.show_annotation_properties)
        menu.addAction("Duplicate").triggered.connect(self.duplicate_annotation)
        menu.addSeparator()
        menu.addAction("Delete").triggered.connect(lambda: self.delete_annotation())
        menu.exec_(QCursor.pos())

    def _annot_key(self, event):
        """With the Annotate tool: Delete or Backspace deletes the selected
        annotation, Enter changes it, Esc drops a drag or deselects."""
        if not self.annotating:
            return
        press = self._annot_press_at
        if event.key == "escape":
            if press is not None and press["dragging"]:
                self._annot_press_at = None
                self._annot_live = None
                self._annot_restore(self._annot_undo.pop())
                if press["grab"][0] == "draw":
                    self.selected_annotation = None
                self._annot_changed()
            elif self.selected_annotation is not None:
                self.select_annotation(None)
        elif press is not None:
            return
        elif event.key in ("delete", "backspace") and self.selected_annotation is not None:
            self.delete_annotation()
        elif event.key in ("enter", "return") and self.selected_annotation is not None:
            self.edit_annotation()
        else:
            return
        self.canvas.key_taken = True  # (not the window's key as well)

    # ------------------------------------------------------------------
    # Following the mouse: the annotation alone is drawn, over a picture
    # of the rest of the figure
    # ------------------------------------------------------------------

    def _annot_begin_live(self, annotation):
        from ..shape_annotations import annotation_artists

        ax = self._nav_axes()
        artists = annotation_artists(ax, annotation)
        background = None
        try:
            for artist in artists:
                artist.set_visible(False)
            # Into the buffer only (not the Qt canvas's draw, which would
            # put the figure without the annotation on screen)
            FigureCanvasAgg.draw(self.canvas)
            background = self.canvas.copy_from_bbox(self.figure.bbox)
        except Exception as e:
            logger.debug(f"Annotation drawn without blitting: {e}")
        finally:
            for artist in artists:
                artist.set_visible(True)
        self._annot_live = {"ax": ax, "background": background, "stale": False}

    def annotation_picture_stale(self):
        """The figure was drawn afresh: the picture under a dragged
        annotation is no longer what is behind it."""
        if self._annot_live is not None:
            self._annot_live["stale"] = True

    def _annot_draw_live(self, kind, annotation):
        from ..shape_annotations import redraw_annotation

        ax = self._nav_axes()
        live = self._annot_live
        if live is None or live["stale"] or live["ax"] is not ax:
            self._annot_begin_live(annotation)
            live = self._annot_live
        artists = redraw_annotation(self, ax, kind, annotation)
        if live["background"] is None:
            self.canvas.draw()
            return
        try:
            # (the whole figure: a part lands askew at a fractional scale)
            self.canvas.restore_region(live["background"])
            for artist in artists:
                ax.draw_artist(artist)
            self.canvas.blit(self.figure.bbox)
        except Exception as e:
            logger.debug(f"Annotation blit failed, drawing whole: {e}")
            self.canvas.draw()


class AnnotationCommandsMixin:
    """The main window's Image > Annotate commands, on the pane in use.

    Mixed into :class:`SolarRadioImageViewerApp`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``statusBar``, ``tab_widget``
    """

    def _annotation_pane(self, need_image=True):
        pane = self.tab_widget.currentWidget()
        if pane is None or (need_image and pane.current_image_data is None):
            self.statusBar().showMessage("Open an image first: annotations go on an image", 5000)
            return None
        return pane

    def annotate_with(self, tool):
        """Turn the Annotate tool on, drawing ``tool`` (text, arrow,
        rectangle, ellipse)."""
        pane = self._annotation_pane()
        if pane is not None:
            pane.set_annotation_tool(tool)
            pane.canvas.setFocus()

    def annotation_command(self, name, *args):
        """``name`` of the pane's annotation commands (add_radius_circle,
        edit_annotation, delete_annotation, clear_annotations,
        undo_annotation, redo_annotation)."""
        pane = self._annotation_pane()
        if pane is not None:
            getattr(pane, name)(*args)

    def setup_annotate_menu(self, menu):
        """Fill Image > Annotate."""
        shortcuts = {"text": "Ctrl+T", "arrow": "Ctrl+Shift+A"}
        tips = {
            "text": "Place text: click on the image where it goes, and type",
            "arrow": "Draw an arrow: drag from its tail to its head",
            "rectangle": "Draw a rectangle on the image",
            "ellipse": "Draw an ellipse on the image",
        }
        for tool, name in TOOL_NAMES.items():
            self._action(menu, name, lambda _on=False, tool=tool: self.annotate_with(tool), tips[tool],
                         shortcuts.get(tool))
        radii = menu.addMenu("Solar radius circle")
        for multiplier in SOLAR_RADII:
            self._action(
                radii, f"{multiplier:g} R☉",
                lambda _on=False, m=multiplier: self.annotation_command("add_radius_circle", m),
                f"Draw a circle at {multiplier:g} solar radii round the Sun's centre",
            )
        menu.addSeparator()
        self.annotation_edit_action = self._action(
            menu, "Edit selected…", lambda: self.annotation_command("edit_annotation"),
            "Change the selected annotation: its text, place and look")
        self.annotation_delete_action = self._action(
            menu, "Delete selected", lambda: self.annotation_command("delete_annotation"),
            "Delete the selected annotation (the Delete key, with the Annotate tool)")
        self.annotation_clear_action = self._action(
            menu, "Delete all…", lambda: self.annotation_command("clear_annotations"),
            "Delete every annotation on this image")
        menu.addSeparator()
        self.annotation_undo_action = self._action(
            menu, "Undo", lambda: self.annotation_command("undo_annotation"),
            "Undo the last change to the annotations", "Ctrl+Z")
        self.annotation_redo_action = self._action(
            menu, "Redo", lambda: self.annotation_command("redo_annotation"),
            "Redo the change to the annotations that was undone", ["Ctrl+Shift+Z", "Ctrl+Y"])
        menu.aboutToShow.connect(self._refresh_annotate_menu)

    def _refresh_annotate_menu(self):
        pane = self.tab_widget.currentWidget()
        has_image = pane is not None and pane.current_image_data is not None
        selected = has_image and pane.annotating and pane.selected_annotation is not None
        self.annotation_edit_action.setEnabled(bool(selected))
        self.annotation_delete_action.setEnabled(bool(selected))
        self.annotation_clear_action.setEnabled(bool(has_image and pane.annotation_count()))
        # (Undo and Redo stay enabled: their shortcuts must work unseen)


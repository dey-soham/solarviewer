"""
What the Annotate tool shows over and beside the image: the selected
annotation's outline and handles (a layer over the canvas), the strip of
what to draw at the canvas's corner, the field text is typed into where it
goes, and the window with an annotation's properties.

The annotations themselves are drawn by Matplotlib with the image
(``shape_annotations.py``); how the mouse works them is in
``tools/annotations.py``.
"""

from PyQt5.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PyQt5.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..dialogs.form import color_combo, field_label, form_block, swatch_icon
from ..ui.styles import SPACE_XS, set_hand_cursor, themed
from ..ui.widgets import button_bar, dialog_layout, empty_note, vrule
from .annotation_model import (
    ARROW,
    COLORS,
    EVEN,
    LINE_STYLES,
    SHAPE,
    SHAPE_TYPES,
    SOLAR_RADII,
    TEXT,
    TOOL_NAMES,
    TURNS,
    shape_angle,
    shape_box,
)
from .geometry import box_outline
from .preview import contrast_colors
from .roi_stats import CanvasOverlay

ICON_PX = 16
# The round handles at an arrow's ends (screen pixels)
END_RADIUS = 4.5
# The icons, as lines on a 16 px grid: ("line", points), ("closed", points),
# ("ellipse", cx, cy, rx, ry) or ("dot", cx, cy, radius)
ICONS = {
    "annotate": (
        ("closed", ((2.6, 13.4), (3.4, 10.2), (10.6, 3.0), (13.0, 5.4), (5.8, 12.6))),
        ("line", ((9.0, 4.6), (11.4, 7.0))),
    ),
    "text": (
        ("line", ((3.5, 5.0), (3.5, 3.5), (12.5, 3.5), (12.5, 5.0))),
        ("line", ((8.0, 3.5), (8.0, 12.5))),
        ("line", ((6.2, 12.5), (9.8, 12.5))),
    ),
    "arrow": (
        ("line", ((3.0, 13.0), (12.5, 3.5))),
        ("line", ((7.2, 3.5), (12.5, 3.5), (12.5, 8.8))),
    ),
    "rectangle": (("closed", ((2.5, 4.0), (13.5, 4.0), (13.5, 12.0), (2.5, 12.0))),),
    "ellipse": (("ellipse", 8.0, 8.0, 5.7, 4.3),),
    "properties": (
        ("line", ((2.5, 5.0), (13.5, 5.0))),
        ("dot", 6.0, 5.0, 1.9),
        ("line", ((2.5, 11.0), (13.5, 11.0))),
        ("dot", 10.5, 11.0, 1.9),
    ),
    "delete": (
        ("line", ((3.0, 4.6), (13.0, 4.6))),
        ("line", ((6.3, 4.6), (6.3, 2.8), (9.7, 2.8), (9.7, 4.6))),
        ("line", ((4.3, 4.6), (5.0, 13.2), (11.0, 13.2), (11.7, 4.6))),
    ),
    "more": (("dot", 3.5, 8.0, 1.2), ("dot", 8.0, 8.0, 1.2), ("dot", 12.5, 8.0, 1.2)),
}


def tool_icon(name, palette, size=ICON_PX):
    """The icon ``name`` of ICONS in the theme's colours, greyed while its
    button is disabled."""
    icon = QIcon()
    for mode, colour in ((QIcon.Normal, "text_strong"), (QIcon.Disabled, "disabled")):
        for scale in (1, 2):
            pixmap = QPixmap(size * scale, size * scale)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.scale(pixmap.width() / 16, pixmap.height() / 16)
            ink = QColor(palette[colour])
            painter.setPen(QPen(ink, 1.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            for mark in ICONS[name]:
                painter.setBrush(Qt.NoBrush)
                if mark[0] == "line":
                    painter.drawPolyline(QPolygonF([QPointF(x, y) for x, y in mark[1]]))
                elif mark[0] == "closed":
                    painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in mark[1]]))
                elif mark[0] == "ellipse":
                    painter.drawEllipse(QPointF(mark[1], mark[2]), mark[3], mark[4])
                else:
                    painter.setBrush(ink)
                    painter.drawEllipse(QPointF(mark[1], mark[2]), mark[3] - 0.7, mark[3] - 0.7)
            painter.end()
            icon.addPixmap(pixmap, mode)
    return icon


def radii_label(multiplier):
    return f"{multiplier:g} R☉"


# ---------------------------------------------------------------------------
# The selected annotation's outline and handles
# ---------------------------------------------------------------------------

class AnnotationLayer(CanvasOverlay):
    """With the Annotate tool, the selected annotation's handles over the
    canvas: a shape's box with the squares that resize it and the round one
    that turns it, the rounds at an arrow's two ends, a dashed frame round a
    text. Painted in Qt in the colours that contrast with the image; the
    Matplotlib figure is not drawn again for them."""

    def paintEvent(self, event):
        tab = self._tab
        selected = tab.selected_annotation if tab.annotating else None
        if selected is None or tab._nav_axes() is None:
            return
        kind, annotation = selected
        line, halo = (QColor(c) for c in contrast_colors(tab))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        try:
            if kind == SHAPE:
                self._shape(painter, annotation, line, halo)
            elif kind == ARROW:
                self._arrow(painter, annotation, line, halo)
            else:
                self._text(painter, annotation, line, halo)
        finally:
            painter.end()

    def _shape(self, painter, shape, line, halo):
        scale = self._tab.pixels_per_arcsec()
        corners, angle = shape_box(shape, scale), shape_angle(shape)
        if shape.get("type") in ("circle", "ellipse"):
            # The box the handles are on, faintly
            self._dashed(painter, QPolygonF(self.to_widget(box_outline(corners, angle))), line, halo)
        even = shape.get("type") in EVEN
        self.draw_handles(
            painter, corners, angle, line, halo,
            indexes=(0, 1, 2, 3) if even else None,
            knob=shape.get("type") in TURNS,
        )

    def _arrow(self, painter, arrow, line, halo):
        painter.setPen(QPen(halo, 1.0))
        painter.setBrush(line)
        for point in self.to_widget([(arrow["x1"], arrow["y1"]), (arrow["x2"], arrow["y2"])]):
            painter.drawEllipse(point, END_RADIUS, END_RADIUS)

    def _text(self, painter, text, line, halo):
        rect = self.text_rect(text)
        if rect is not None:
            self._dashed(painter, QPolygonF(rect.adjusted(-3, -3, 3, 3)), line, halo)

    def text_rect(self, text):
        """Where a text annotation is drawn, in this layer's pixels; None
        before it has been drawn."""
        box = self._tab.annotation_text_box(text)
        if box is None:
            return None
        x0, y0, x1, y1 = box
        return QRectF(self.from_display(x0, y1), self.from_display(x1, y0))

    @staticmethod
    def _dashed(painter, polygon, line, halo):
        """A closed dashed line in ``line`` over a soft ``halo``."""
        painter.setBrush(Qt.NoBrush)
        shadow = QColor(halo)
        shadow.setAlphaF(0.5)
        painter.setPen(QPen(shadow, 3.0))
        painter.drawPolygon(polygon)
        pen = QPen(line, 1.0, Qt.CustomDashLine)
        pen.setDashPattern([4, 3])
        painter.setPen(pen)
        painter.drawPolygon(polygon)


# ---------------------------------------------------------------------------
# The strip at the canvas's corner
# ---------------------------------------------------------------------------

class AnnotationBar(QFrame):
    """The Annotate tool's strip, at the top left corner of the image while
    the tool is on: what a press on the image draws (text, an arrow, a
    rectangle, an ellipse), the colour, and what can be done with the
    selected annotation (its properties, delete it)."""

    tool_chosen = pyqtSignal(str)
    color_chosen = pyqtSignal(str)
    properties_asked = pyqtSignal()
    delete_asked = pyqtSignal()
    radius_asked = pyqtSignal(float)
    undo_asked = pyqtSignal()
    redo_asked = pyqtSignal()
    clear_asked = pyqtSignal()

    MARGIN = 8  # from the canvas's corner

    TIPS = {
        "text": "Text: click on the image and type",
        "arrow": "Arrow: drag from its tail to its head (Shift: in 15° steps)",
        "rectangle": "Rectangle: drag to draw one (Shift: a square)",
        "ellipse": "Ellipse: drag to draw one (Shift: a circle)",
    }

    def __init__(self, canvas):
        super().__init__(canvas)
        self.setObjectName("CanvasTools")
        self.setCursor(Qt.ArrowCursor)
        self._color = COLORS[0]
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE_XS, SPACE_XS, SPACE_XS, SPACE_XS)
        layout.setSpacing(2)

        self.tool_buttons = {}
        for name in TOOL_NAMES:
            button = self._button(name, self.TIPS[name])
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.clicked.connect(lambda _on, name=name: self.tool_chosen.emit(name))
            self.tool_buttons[name] = button
            layout.addWidget(button)
        layout.addWidget(vrule(16))

        self.color_button = QToolButton()
        self.color_button.setFocusPolicy(Qt.NoFocus)
        self.color_button.setToolTip("Colour: of the selected annotation, and of the next ones")
        self.color_button.setIconSize(QSize(ICON_PX, ICON_PX))
        self.color_button.setPopupMode(QToolButton.InstantPopup)
        self.color_button.setMenu(self._color_menu())
        layout.addWidget(self.color_button)
        self.properties_button = self._button(
            "properties", "Properties of the selected annotation (or double-click it)"
        )
        self.properties_button.clicked.connect(self.properties_asked)
        layout.addWidget(self.properties_button)
        self.delete_button = self._button("delete", "Delete the selected annotation (Delete)")
        self.delete_button.clicked.connect(self.delete_asked)
        layout.addWidget(self.delete_button)
        layout.addWidget(vrule(16))

        self.more_button = self._button("more", "Solar radius circles, undo, delete all")
        self.more_button.setPopupMode(QToolButton.InstantPopup)
        self.more_button.setMenu(self._more_menu())
        layout.addWidget(self.more_button)

        themed(self, lambda palette: self._show_color(palette))
        self.set_selected(False)
        set_hand_cursor(self)
        self.hide()

    def _button(self, icon, tip):
        button = QToolButton()
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.NoFocus)  # (the image keeps the keys: Delete, Esc)
        button.setIconSize(QSize(ICON_PX, ICON_PX))
        button.setToolTip(tip)
        themed(button, lambda palette: button.setIcon(tool_icon(icon, palette)))
        return button

    def _color_menu(self):
        menu = QMenu(self)
        for color in COLORS:
            action = menu.addAction(color.capitalize())
            action.setData(color)
            action.triggered.connect(lambda _on, color=color: self.color_chosen.emit(color))
        menu.addSeparator()
        menu.addAction("Other…").triggered.connect(self._other_color)

        def swatches(palette):
            for action in menu.actions():
                if action.data():
                    action.setIcon(swatch_icon(action.data(), palette["text_faint"]))

        themed(menu, swatches)
        return menu

    def _other_color(self):
        color = QColorDialog.getColor(QColor(self._color), self.window(), "Annotation colour")
        if color.isValid():
            self.color_chosen.emit(color.name())

    def _more_menu(self):
        menu = QMenu(self)
        radii = menu.addMenu("Solar radius circle")
        for multiplier in SOLAR_RADII:
            action = radii.addAction(radii_label(multiplier))
            action.triggered.connect(lambda _on, m=multiplier: self.radius_asked.emit(float(m)))
        menu.addSeparator()
        # The keys are the window's own (Image > Annotate): these only name
        # them. Two live actions with one key would both be refused
        self.undo_action = menu.addAction("Undo")
        self.redo_action = menu.addAction("Redo")
        for action, keys, asked in (
            (self.undo_action, "Ctrl+Z", self.undo_asked),
            (self.redo_action, "Ctrl+Shift+Z", self.redo_asked),
        ):
            action.setShortcut(keys)
            action.setShortcutContext(Qt.WidgetShortcut)
            action.triggered.connect(asked)
        menu.addSeparator()
        self.clear_action = menu.addAction("Delete all annotations…")
        self.clear_action.triggered.connect(self.clear_asked)
        return menu

    def _show_color(self, palette):
        self.color_button.setIcon(swatch_icon(self._color, palette["text_faint"], ICON_PX))

    def set_tool(self, name):
        button = self.tool_buttons.get(name)
        if button is not None:
            button.setChecked(True)

    def set_color(self, color):
        from ..ui.styles import theme_manager

        self._color = color
        self._show_color(theme_manager.palette)

    def set_selected(self, selected):
        """Whether an annotation is selected: its buttons work then."""
        self.properties_button.setEnabled(selected)
        self.delete_button.setEnabled(selected)

    def set_history(self, undo, redo, any_annotations):
        self.undo_action.setEnabled(undo)
        self.redo_action.setEnabled(redo)
        self.clear_action.setEnabled(any_annotations)

    def place(self):
        """At the top left corner of the canvas, over everything on it."""
        self.adjustSize()
        self.move(self.MARGIN, self.MARGIN)
        self.raise_()

    # A press on the strip between its buttons is not one on the image
    def mousePressEvent(self, event):
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()

    def mouseMoveEvent(self, event):
        event.accept()

    def mouseDoubleClickEvent(self, event):
        event.accept()

    def wheelEvent(self, event):
        event.accept()


# ---------------------------------------------------------------------------
# Typing text where it goes
# ---------------------------------------------------------------------------

class TextEditor(QLineEdit):
    """The field a text annotation is typed into, on the image where the
    text will be. Enter keeps what was typed, as does a click elsewhere;
    Esc drops it."""

    finished = pyqtSignal(bool)  # whether to keep what was typed
    MIN_WIDTH = 150

    def __init__(self, canvas):
        super().__init__(canvas)
        self.setPlaceholderText("Type, then Enter")
        self.setCursor(Qt.IBeamCursor)
        self._done = True
        self.textChanged.connect(self._fit)
        self.hide()

    def start(self, at, text, pixel_size, bold=False, italic=False):
        """Show the field with ``text`` so that its first letter stands at
        ``at`` (the canvas's pixels), in letters ``pixel_size`` high."""
        self.setStyleSheet(
            f"font-size: {max(9, round(pixel_size))}px; font-weight: {700 if bold else 400}; "
            f"font-style: {'italic' if italic else 'normal'};"
        )
        self._done = False
        self.setText(text)
        self.ensurePolished()
        self._fit()
        metrics = self.fontMetrics()
        height = self.height()
        left = self.textMargins().left() + self._padding() + 2
        baseline = (height + metrics.ascent() - metrics.descent()) / 2
        self.move(round(at.x() - left), round(at.y() - baseline))
        self.show()
        self.raise_()
        self.setFocus(Qt.MouseFocusReason)
        self.selectAll()

    def _padding(self):
        from PyQt5.QtWidgets import QStyle, QStyleOptionFrame

        option = QStyleOptionFrame()
        self.initStyleOption(option)
        return self.style().subElementRect(QStyle.SE_LineEditContents, option, self).left()

    def _fit(self):
        metrics = self.fontMetrics()
        words = self.text() or self.placeholderText()
        height = max(self.sizeHint().height(), metrics.height() + 8)
        self.resize(max(self.MIN_WIDTH, metrics.horizontalAdvance(words) + 2 * self._padding() + 16), height)

    def active(self):
        return not self._done

    def finish(self, keep=True):
        if self._done:
            return
        self._done = True
        self.hide()
        self.finished.emit(keep)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.finish(True)
        elif event.key() == Qt.Key_Escape:
            self.finish(False)
        else:
            super().keyPressEvent(event)
        event.accept()  # (the image's keys are not for what is typed)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.finish(True)


# ---------------------------------------------------------------------------
# An annotation's properties
# ---------------------------------------------------------------------------

def _spin(low, high, value, step=1.0, decimals=1):
    box = QDoubleSpinBox()
    box.setRange(low, high)
    box.setDecimals(decimals)
    box.setSingleStep(step)
    box.setValue(value)
    box.setKeyboardTracking(False)  # (a number half typed moves nothing)
    return box


def _position(name=None):
    return _spin(-1e7, 1e7, 0.0, 1.0, 1)


def _choose_color(combo, color):
    """Show ``color`` in a colour choice, adding it if it is not one of
    its own (a colour picked from Other…)."""
    index = combo.findData(color)
    if index < 0 and color:
        from ..ui.styles import theme_manager

        combo.addItem(str(color), color)
        index = combo.count() - 1
        combo.setItemIcon(index, swatch_icon(color, theme_manager.palette["text_faint"]))
    combo.setCurrentIndex(max(index, 0))


class AnnotationProperties(QDialog):
    """The properties of the selected annotation: what it says or is, where
    it is in pixels of the image, and how it looks. It follows the
    selection, and what is changed in it shows on the image at once (and
    can be undone)."""

    def __init__(self, parent, tab):
        super().__init__(parent)
        self._tab = tab
        self._bound = None  # (kind, annotation)
        self._loading = False
        self._undoable = False  # whether this annotation's changes here have their undo step
        self.setWindowTitle("Annotation properties")
        layout = dialog_layout(self)
        self._stack = QStackedWidget()
        self._pages = {
            None: empty_note("Click an annotation on the image to see its properties."),
            TEXT: self._text_page(),
            ARROW: self._arrow_page(),
            SHAPE: self._shape_page(),
        }
        for page in self._pages.values():
            self._stack.addWidget(page)
        layout.addWidget(self._stack)
        layout.addStretch()
        box, self._buttons = button_bar(
            primary=("Close", self.close),
            extras=[("Delete", self._delete)],
        )
        layout.addWidget(box)
        set_hand_cursor(self)
        self.setAttribute(Qt.WA_DeleteOnClose)

    # -- the pages ---------------------------------------------------------

    def _page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        return page, layout

    def _text_page(self):
        page, layout = self._page()
        grid = form_block(layout)
        grid.addWidget(field_label("Text"), 0, 0)
        self.text_edit = QLineEdit()
        self.text_edit.textEdited.connect(self._changed)
        grid.addWidget(self.text_edit, 0, 1)

        grid = form_block(layout, "Position (pixels)", pairs=2)
        self.text_x, self.text_y = _position(), _position()
        for column, (name, box) in enumerate((("X", self.text_x), ("Y", self.text_y))):
            grid.addWidget(field_label(name), 0, 2 * column)
            grid.addWidget(box, 0, 2 * column + 1)
            box.valueChanged.connect(self._changed)

        grid = form_block(layout, "Style", pairs=2)
        self.text_color = color_combo(COLORS)
        self.text_size = QSpinBox()
        self.text_size.setRange(6, 72)
        self.text_weight = QComboBox()
        self.text_weight.addItems(["Normal", "Bold"])
        self.text_slant = QComboBox()
        self.text_slant.addItems(["Normal", "Italic"])
        self.text_ground = color_combo(["black", "white", "gray", "yellow", "red", "blue"])
        self.text_ground.insertItem(0, "None")
        rows = (
            ("Colour", self.text_color, "Font size", self.text_size),
            ("Weight", self.text_weight, "Style", self.text_slant),
            ("Background", self.text_ground, None, None),
        )
        self._rows(grid, rows)
        return page

    def _arrow_page(self):
        page, layout = self._page()
        grid = form_block(layout, "Position (pixels)", pairs=2)
        self.arrow_ends = [_position() for _ in range(4)]
        names = ("Tail X", "Tail Y", "Head X", "Head Y")
        for index, (name, box) in enumerate(zip(names, self.arrow_ends)):
            grid.addWidget(field_label(name), index // 2, 2 * (index % 2))
            grid.addWidget(box, index // 2, 2 * (index % 2) + 1)
            box.valueChanged.connect(self._changed)

        grid = form_block(layout, "Style", pairs=2)
        self.arrow_color = color_combo(COLORS)
        self.arrow_width = _spin(0.5, 10.0, 2.0, 0.5)
        self.arrow_head = QSpinBox()
        self.arrow_head.setRange(4, 60)
        rows = (
            ("Colour", self.arrow_color, "Line width", self.arrow_width),
            ("Head size", self.arrow_head, None, None),
        )
        self._rows(grid, rows)
        return page

    def _shape_page(self):
        page, layout = self._page()
        grid = form_block(layout, "Shape", pairs=2)
        self.shape_type = QComboBox()
        for name in SHAPE_TYPES:
            self.shape_type.addItem(name.capitalize(), name)
        self.shape_label = QLineEdit()
        self.shape_label.setPlaceholderText("Optional")
        self.shape_label.textEdited.connect(self._changed)
        self._rows(grid, (("Type", self.shape_type, "Label", self.shape_label),))

        grid = form_block(layout, "Position and size", pairs=2)
        self.shape_x, self.shape_y = _position(), _position()
        self.shape_width = _spin(0.1, 1e7, 100.0, 1.0)
        self.shape_height = _spin(0.1, 1e7, 100.0, 1.0)
        self.shape_unit = QComboBox()
        self.shape_unit.addItem("Pixels", "pixels")
        self.shape_unit.addItem("Arcsec", "arcsec")
        self.shape_unit.setToolTip("The unit of the width and height; the centre is in pixels")
        self.shape_turn = _spin(-360.0, 360.0, 0.0, 5.0)
        self.shape_height_label = field_label("Height")
        self.shape_turn_label = field_label("Angle (°)")
        rows = (
            ("Centre X", self.shape_x, "Centre Y", self.shape_y),
            ("Width", self.shape_width, self.shape_height_label, self.shape_height),
            ("Unit", self.shape_unit, self.shape_turn_label, self.shape_turn),
        )
        self._rows(grid, rows)
        # (not there for a circle or square: the fields keep their width)
        for widget in (self.shape_height_label, self.shape_height, self.shape_turn_label, self.shape_turn):
            policy = widget.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            widget.setSizePolicy(policy)

        grid = form_block(layout, "Style", pairs=2)
        self.shape_color = color_combo(COLORS)
        self.shape_line = QComboBox()
        for name, value in LINE_STYLES:
            self.shape_line.addItem(name, value)
        self.shape_line_width = _spin(0.5, 10.0, 1.5, 0.5)
        self.shape_opacity = _spin(0.05, 1.0, 0.9, 0.1, 2)
        self.shape_fill = QCheckBox("Fill")
        self.shape_fill_color = color_combo(COLORS, "cyan")
        self.shape_fill_label = field_label("Fill colour")
        rows = (
            ("Colour", self.shape_color, "Line style", self.shape_line),
            ("Line width", self.shape_line_width, "Opacity", self.shape_opacity),
            (None, self.shape_fill, self.shape_fill_label, self.shape_fill_color),
        )
        self._rows(grid, rows)
        return page

    def _rows(self, grid, rows):
        """Rows of two (name, field) pairs; every field's change is taken."""
        for row, (name1, field1, name2, field2) in enumerate(rows):
            for column, (name, field) in enumerate(((name1, field1), (name2, field2))):
                if field is None:
                    continue
                if name is not None:
                    label = name if isinstance(name, QWidget) else field_label(name)
                    grid.addWidget(label, row, 2 * column)
                grid.addWidget(field, row, 2 * column + 1)
                self._follow(field)

    def _follow(self, field):
        if isinstance(field, QComboBox):
            field.activated.connect(self._changed)
        elif isinstance(field, (QSpinBox, QDoubleSpinBox)):
            field.valueChanged.connect(self._changed)
        elif isinstance(field, QCheckBox):
            field.toggled.connect(self._changed)

    # -- the annotation shown ---------------------------------------------

    def bind(self, selected):
        """Show ``selected`` ((kind, annotation), or None), as it is now."""
        same = (
            selected is not None
            and self._bound is not None
            and selected[1] is self._bound[1]
        )
        if not same:
            self._undoable = False
        self._bound = selected
        kind = selected[0] if selected is not None else None
        self._stack.setCurrentWidget(self._pages[kind])
        self._buttons["Delete"].setEnabled(selected is not None)
        if selected is None:
            return
        self._loading = True
        try:
            self._load(*selected)
        finally:
            self._loading = False

    def _load(self, kind, a):
        if kind == TEXT:
            if self.text_edit.text() != a.get("text", ""):
                self.text_edit.setText(a.get("text", ""))
            self.text_x.setValue(a["x"])
            self.text_y.setValue(a["y"])
            _choose_color(self.text_color, a.get("color", "yellow"))
            self.text_size.setValue(int(a.get("fontsize", 12)))
            self.text_weight.setCurrentIndex(1 if a.get("fontweight") == "bold" else 0)
            self.text_slant.setCurrentIndex(1 if a.get("fontstyle") == "italic" else 0)
            if a.get("background"):
                _choose_color(self.text_ground, a["background"])
            else:
                self.text_ground.setCurrentIndex(0)
        elif kind == ARROW:
            for box, key in zip(self.arrow_ends, ("x1", "y1", "x2", "y2")):
                box.setValue(a[key])
            _choose_color(self.arrow_color, a.get("color", "red"))
            self.arrow_width.setValue(a.get("linewidth", 2.0))
            self.arrow_head.setValue(int(a.get("head_width", 10)))
        else:
            self.shape_type.setCurrentIndex(max(self.shape_type.findData(a.get("type")), 0))
            if self.shape_label.text() != a.get("label", ""):
                self.shape_label.setText(a.get("label", ""))
            self.shape_x.setValue(a["center_x"])
            self.shape_y.setValue(a["center_y"])
            self.shape_width.setValue(a["width"])
            self.shape_height.setValue(a.get("height", a["width"]))
            self.shape_unit.setCurrentIndex(max(self.shape_unit.findData(a.get("unit", "pixels")), 0))
            self.shape_turn.setValue(a.get("angle", 0.0))
            _choose_color(self.shape_color, a.get("color", "white"))
            self.shape_line.setCurrentIndex(max(self.shape_line.findData(a.get("linestyle", "-")), 0))
            self.shape_line_width.setValue(a.get("linewidth", 1.5))
            self.shape_opacity.setValue(a.get("alpha", 0.8))
            self.shape_fill.setChecked(bool(a.get("fill")))
            if a.get("fill") and a.get("facecolor") not in (None, "none"):
                _choose_color(self.shape_fill_color, a["facecolor"])
            self._show_shape_fields()

    def _show_shape_fields(self):
        shape_type = self.shape_type.currentData()
        for widget in (self.shape_height_label, self.shape_height):
            widget.setVisible(shape_type not in EVEN)
        for widget in (self.shape_turn_label, self.shape_turn):
            widget.setVisible(shape_type in TURNS)
        for widget in (self.shape_fill_label, self.shape_fill_color):
            widget.setVisible(self.shape_fill.isChecked())

    def _changed(self, *_args):
        """A field was changed by the user: the annotation takes it."""
        if self._loading or self._bound is None:
            return
        kind, a = self._bound
        if not self._undoable:
            self._tab.annotation_checkpoint()
            self._undoable = True
        if kind == TEXT:
            a.update(
                text=self.text_edit.text(),
                x=self.text_x.value(),
                y=self.text_y.value(),
                color=self.text_color.currentData(),
                fontsize=self.text_size.value(),
                fontweight=self.text_weight.currentText().lower(),
                fontstyle=self.text_slant.currentText().lower(),
                background=self.text_ground.currentData(),
            )
        elif kind == ARROW:
            for box, key in zip(self.arrow_ends, ("x1", "y1", "x2", "y2")):
                a[key] = box.value()
            a.update(
                color=self.arrow_color.currentData(),
                linewidth=self.arrow_width.value(),
                head_width=self.arrow_head.value(),
            )
        else:
            fill = self.shape_fill.isChecked()
            a.update(
                type=self.shape_type.currentData(),
                label=self.shape_label.text().strip(),
                center_x=self.shape_x.value(),
                center_y=self.shape_y.value(),
                width=self.shape_width.value(),
                height=self.shape_height.value(),
                unit=self.shape_unit.currentData(),
                angle=self.shape_turn.value(),
                color=self.shape_color.currentData(),
                linestyle=self.shape_line.currentData(),
                linewidth=self.shape_line_width.value(),
                alpha=self.shape_opacity.value(),
                fill=fill,
                facecolor=self.shape_fill_color.currentData() if fill else "none",
            )
            self._show_shape_fields()
        self._tab.annotation_edited(kind, a, by=self)

    def _delete(self):
        if self._bound is not None:
            self._tab.delete_annotation()

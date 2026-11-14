"""
The bar under the image: the tools at the left (one on at a time), the
file navigation in the middle, the zoom group at the right.
"""

import logging
from PyQt5.QtWidgets import (
    QAbstractButton,
    QAction,
    QActionGroup,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
)
from PyQt5.QtCore import QEvent, QPointF, QSize, Qt
from PyQt5.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from ..ui.icons import get_resource_path, launcher_icon, themed_icon
from ..ui.styles import SPACE_S, themed
from ..ui.widgets import segmented
from ..tools import (
    RegionMode,
)
from ..tools.annotation_ui import tool_icon

logger = logging.getLogger(__name__)

ICON_SIZE = QSize(16, 16)
NAV_ICON = 14  # the file navigation's icons, which share the bar's narrowest gap
NAV_WIDTH = 22  # of a file navigation button

# The file navigation's marks: lines on a 16px grid, drawn as the SVG icons are
NAV_MARKS = {
    "first": (((11, 4), (7, 8), (11, 12)), ((4.5, 4), (4.5, 12))),
    "prev": (((10, 4), (6, 8), (10, 12)),),
    "next": (((6, 4), (10, 8), (6, 12)),),
    "last": (((5, 4), (9, 8), (5, 12)), ((11.5, 4), (11.5, 12))),
}


def _nav_icon(marks, palette):
    """An icon of ``marks`` in the theme, greyed while its button is disabled."""
    icon = QIcon()
    for mode, colour in ((QIcon.Normal, "text_strong"), (QIcon.Disabled, "disabled")):
        for scale in (1, 2):
            pixmap = QPixmap(NAV_ICON * scale, NAV_ICON * scale)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.scale(pixmap.width() / 16, pixmap.height() / 16)
            painter.setPen(QPen(QColor(palette[colour]), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            for line in marks:
                painter.drawPolyline(*[QPointF(x, y) for x, y in line])
            painter.end()
            icon.addPixmap(pixmap, mode)
    return icon


def _least_width(layout):
    """(the least width ``layout`` needs, whether it holds anything shown):
    worked out from its widgets as they are now. Qt's own answer is kept
    from before a style change until the event loop has turned."""
    margins = layout.contentsMargins()
    total, shown = margins.left() + margins.right(), 0
    for index in range(layout.count()):
        item = layout.itemAt(index)
        widget, inner = item.widget(), item.layout()
        if widget is not None:
            if widget.isHidden():
                continue
            if widget.layout() is not None:
                own = widget.contentsMargins()  # (its border)
                width = _least_width(widget.layout())[0] + own.left() + own.right()
            else:
                width = widget.minimumSizeHint().width()
            width = min(max(width, widget.minimumWidth()), widget.maximumWidth())
        elif inner is not None:
            width, any_shown = _least_width(inner)
            if not any_shown:
                continue
        else:
            continue  # (a stretch)
        total += width
        shown += 1
    return total + max(0, shown - 1) * max(0, layout.spacing()), shown > 0


class _CanvasBar(QFrame):
    """The bar under the image. Too narrow for all it holds (a small
    window, a compare page's controls beside the rest), it gives way in
    steps rather than squeeze its buttons to slivers: first the grouped
    buttons stand closer (the ``tight`` look), then the ``optional``
    widgets go, a group at a time: those with a key, or a neighbour, that
    does the same. Wider again, they come back."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CanvasBar")
        self.optional = []  # groups of widgets, the first to go first
        self._step = 0
        self._needs = {}  # step -> the width the bar needs at it, where known
        self._fitting = False

    def _need(self):
        return _least_width(self.layout())[0]

    def _set_step(self, step):
        tight = step >= 1
        if bool(self.property("tight")) != tight:
            self.setProperty("tight", tight)
            for button in self.findChildren(QAbstractButton):
                self.style().unpolish(button)
                self.style().polish(button)
                # (a button keeps the size it worked out until it is told
                # its style changed)
                QApplication.sendEvent(button, QEvent(QEvent.StyleChange))
                button.updateGeometry()
        for index, group in enumerate(self.optional):
            for widget in group:
                widget.setVisible(step < index + 2)
        self._step = step
        self._needs[step] = self._need()

    def fit(self):
        if self._fitting or self.layout() is None:
            return
        self._fitting = True
        try:
            need = self._need()
            if self._needs.get(self._step) != need:
                # What it holds changed: what the other steps need is not known
                self._needs = {self._step: need}
            last = len(self.optional) + 1
            while self._needs[self._step] > self.width() and self._step < last:
                self._set_step(self._step + 1)
            # Back a step when that is known to fit, or not known (then it is tried)
            while self._step > 0 and self._needs.get(self._step - 1, 0) <= self.width():
                self._set_step(self._step - 1)
                if self._needs[self._step] > self.width():
                    self._set_step(self._step + 1)
                    break
        finally:
            self._fitting = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit()

    def showEvent(self, event):
        super().showEvent(event)
        self.fit()

    def event(self, event):
        done = super().event(event)
        if event.type() == QEvent.LayoutRequest:
            self.fit()
        return done


class FigureToolbarMixin:
    """Builds the bar under the image.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``_on_first_file``, ``_on_last_file``,
    ``_on_next_file``, ``_on_prev_file``, ``_show_filter_dialog``,
    ``_show_playlist_dialog``, ``_toggle_annotate_mode``, ``_toggle_profile_mode``,
    ``_toggle_ruler_mode``, ``reset_view``, ``set_region_mode``,
    ``zoom_60arcmin``, ``zoom_in``, ``zoom_out``
    """

    def _tool_action(self, icon, tip, slot=None, checkable=False):
        action = QAction(QIcon(icon), "", self)
        action.setToolTip(tip)
        if checkable:
            action.setCheckable(True)
        if slot is not None:
            action.triggered.connect(slot)
        return action

    @staticmethod
    def _nav_button(tip, slot, marks=None):
        button = QToolButton()
        button.setAutoRaise(True)
        button.setFixedWidth(NAV_WIDTH)
        button.setIconSize(QSize(NAV_ICON, NAV_ICON))
        button.setToolTip(tip)
        button.clicked.connect(slot)
        if marks is not None:
            themed(button, lambda palette: button.setIcon(_nav_icon(marks, palette)))
        return button

    @staticmethod
    def _tool_button(action):
        button = QToolButton()
        button.setDefaultAction(action)
        button.setIconSize(ICON_SIZE)
        button.setAutoRaise(True)
        return button

    def setup_figure_toolbar(self):
        """The bar under the image: tools, zoom, file navigation. Returns
        the widget."""
        bar = _CanvasBar()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(SPACE_S)
        action_group = QActionGroup(self)

        # The tools: pan (the default), regions, the ruler, the profile and
        # the annotations
        self.pan_action = self._tool_action(
            get_resource_path("assets/pan.png"),
            "Pan (H): drag, or middle-drag in any mode. Scroll to zoom, arrows to move",
            lambda: self.set_region_mode(RegionMode.PAN),
            checkable=True,
        )
        self.pan_action.setChecked(True)
        self.rect_action = self._tool_action(
            get_resource_path("assets/rectangle_selection.png"),
            "Rectangle (B): drag to draw a region (Shift: a square)",
            lambda: self.set_region_mode(RegionMode.RECTANGLE),
            checkable=True,
        )
        self.ellipse_action = self._tool_action(
            get_resource_path("assets/ellipse_selection.png"),
            "Ellipse (E): drag to draw a region (Shift: a circle)",
            lambda: self.set_region_mode(RegionMode.ELLIPSE),
            checkable=True,
        )
        self.ruler_action = self._tool_action(
            themed_icon("ruler.png"),
            "Ruler (L): measure the angular distance between two points",
            checkable=True,
        )
        self.ruler_action.toggled.connect(self._toggle_ruler_mode)
        self.profile_action = self._tool_action(
            themed_icon("profile.png"),
            "Profile (P): plot the flux along a line cut",
            checkable=True,
        )
        self.profile_action.toggled.connect(self._toggle_profile_mode)
        self.annotate_action = self._tool_action(
            QIcon(),
            "Annotate (A): put text, arrows and shapes on the image, and change them",
            checkable=True,
        )
        self.annotate_action.toggled.connect(self._toggle_annotate_mode)
        themed(bar, lambda palette: self.annotate_action.setIcon(tool_icon("annotate", palette)))
        tools = [
            self.pan_action,
            self.rect_action,
            self.ellipse_action,
            self.ruler_action,
            self.profile_action,
            self.annotate_action,
        ]
        for action in tools:
            action_group.addAction(action)
        layout.addWidget(segmented([self._tool_button(action) for action in tools]))
        # Room for the page's controls that go with the tools (see
        # core.compare.ImagePage, which moves them into the active pane's bar)
        self.page_tools_slot = QHBoxLayout()
        self.page_tools_slot.setContentsMargins(0, 0, 0, 0)
        self.page_tools_slot.setSpacing(SPACE_S)
        layout.addLayout(self.page_tools_slot)
        layout.addStretch()

        # File navigation, in the middle
        nav = QHBoxLayout()
        nav.setSpacing(0)
        self._first_file_btn = self._nav_button(
            "Go to first file", self._on_first_file, NAV_MARKS["first"]
        )
        self._prev_file_btn = self._nav_button(
            "Previous file in directory ([)", self._on_prev_file, NAV_MARKS["prev"]
        )
        self._file_pos_label = QLabel("—/—")
        self._file_pos_label.setObjectName("Meta")
        self._file_pos_label.setAlignment(Qt.AlignCenter)
        # Room for a count like 12/345, the text centred in it so a short
        # one leaves no gap before the next button
        self._file_pos_label.setMinimumWidth(
            self._file_pos_label.fontMetrics().horizontalAdvance("00/000") + 8
        )
        self._next_file_btn = self._nav_button(
            "Next file in directory (])", self._on_next_file, NAV_MARKS["next"]
        )
        self._last_file_btn = self._nav_button(
            "Go to last file", self._on_last_file, NAV_MARKS["last"]
        )
        self._filter_btn = self._nav_button("Set file filter pattern", self._show_filter_dialog)
        self._filter_btn.setIcon(QIcon(themed_icon("filter.svg")))
        self._playlist_btn = self._nav_button("File list: all the files in this directory", self._show_playlist_dialog)
        themed(
            self._playlist_btn,
            lambda _palette: self._playlist_btn.setIcon(launcher_icon("fd_list.svg", NAV_ICON)),
        )
        for button in (
            self._first_file_btn,
            self._prev_file_btn,
            self._next_file_btn,
            self._last_file_btn,
        ):
            button.setEnabled(False)
        for widget in (
            self._first_file_btn,
            self._prev_file_btn,
            self._file_pos_label,
            self._next_file_btn,
            self._last_file_btn,
            self._filter_btn,
            self._playlist_btn,
        ):
            nav.addWidget(widget)
        layout.addLayout(nav)
        layout.addStretch()

        # Zoom at the right: in, how far in, out, reset, one degree
        self.zoom_in_action = self._tool_action(
            get_resource_path("assets/zoom_in.png"), "Zoom in (+)", self.zoom_in
        )
        self.zoom_out_action = self._tool_action(
            get_resource_path("assets/zoom_out.png"), "Zoom out (-)", self.zoom_out
        )
        self.reset_view_action = self._tool_action(
            get_resource_path("assets/reset.png"),
            "Reset view (R)",
            lambda: self.reset_view(show_status_message=True, animate=True),
        )
        self.zoom_60arcmin_action = self._tool_action(
            get_resource_path("assets/zoom_60arcmin.png"), "1°×1° Zoom (1)", self.zoom_60arcmin
        )
        self.zoom_label = QLabel("—")
        self.zoom_label.setToolTip("How much of the image's width is in view")
        self.zoom_label.setMinimumWidth(self.zoom_label.fontMetrics().horizontalAdvance("0000%"))
        self.zoom_label.setAlignment(Qt.AlignCenter)
        zoom_buttons = [
            self._tool_button(self.zoom_in_action),
            self.zoom_label,
            self._tool_button(self.zoom_out_action),
            self._tool_button(self.reset_view_action),
            self._tool_button(self.zoom_60arcmin_action),
        ]
        layout.addWidget(segmented(zoom_buttons))
        # What goes first in a narrow window: the keys { } and 1 do the same
        bar.optional = [
            [self._first_file_btn, self._last_file_btn],
            [zoom_buttons[-1], self.zoom_label],
        ]
        # Room at the right end for the page's layout controls
        self.page_controls_slot = QHBoxLayout()
        self.page_controls_slot.setContentsMargins(0, 0, 0, 0)
        self.page_controls_slot.setSpacing(SPACE_S)
        layout.addLayout(self.page_controls_slot)
        return bar

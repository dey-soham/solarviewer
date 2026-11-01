"""
The bar under the image: the tools at the left (one on at a time), the
file navigation in the middle, the zoom group at the right.
"""

import logging
from PyQt5.QtWidgets import (
    QAction,
    QActionGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
)
from PyQt5.QtCore import QPointF, QSize, Qt
from PyQt5.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from ..ui.icons import get_resource_path, launcher_icon, themed_icon
from ..ui.styles import SPACE_S, themed
from ..ui.widgets import segmented
from ..tools import (
    RegionMode,
)

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


class FigureToolbarMixin:
    """Builds the bar under the image.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``_on_first_file``, ``_on_last_file``,
    ``_on_next_file``, ``_on_prev_file``, ``_show_filter_dialog``,
    ``_show_playlist_dialog``, ``_toggle_profile_mode``,
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
        bar = QFrame()
        bar.setObjectName("CanvasBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(SPACE_S)
        action_group = QActionGroup(self)

        # The tools: pan (the default), regions, the ruler and the profile
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
        tools = [
            self.pan_action,
            self.rect_action,
            self.ellipse_action,
            self.ruler_action,
            self.profile_action,
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
        self._playlist_btn = self._nav_button("Show all files in directory", self._show_playlist_dialog)
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
        # Room at the right end for the page's layout controls
        self.page_controls_slot = QHBoxLayout()
        self.page_controls_slot.setContentsMargins(0, 0, 0, 0)
        self.page_controls_slot.setSpacing(SPACE_S)
        layout.addLayout(self.page_controls_slot)
        return bar

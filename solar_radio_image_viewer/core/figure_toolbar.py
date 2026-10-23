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
    QPushButton,
    QToolButton,
)
from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QIcon
from ..ui.icons import get_resource_path, themed_icon
from ..ui.widgets import segmented
from ..tools import (
    RegionMode,
)

logger = logging.getLogger(__name__)

ICON_SIZE = QSize(16, 16)


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
    def _tool_button(action):
        button = QToolButton()
        button.setDefaultAction(action)
        button.setIconSize(ICON_SIZE)
        button.setAutoRaise(True)
        return button

    def setup_figure_toolbar(self, parent_layout):
        """Set up the bar under the image: tools, zoom, file navigation."""
        bar = QFrame()
        bar.setObjectName("CanvasBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(10)
        action_group = QActionGroup(self)

        # The tools: pan (the default), regions, the ruler and the profile
        self.pan_action = self._tool_action(
            get_resource_path("assets/pan.png"),
            "Pan: drag, or middle-drag in any mode. Scroll to zoom, arrows to move",
            lambda: self.set_region_mode(RegionMode.PAN),
            checkable=True,
        )
        self.pan_action.setChecked(True)
        self.rect_action = self._tool_action(
            get_resource_path("assets/rectangle_selection.png"),
            "Rectangle Select",
            lambda: self.set_region_mode(RegionMode.RECTANGLE),
            checkable=True,
        )
        self.ellipse_action = self._tool_action(
            get_resource_path("assets/ellipse_selection.png"),
            "Ellipse Select",
            lambda: self.set_region_mode(RegionMode.ELLIPSE),
            checkable=True,
        )
        self.ruler_action = self._tool_action(
            themed_icon("ruler.png"),
            "Measure Angular Distance (Select two points on the map)",
            checkable=True,
        )
        self.ruler_action.toggled.connect(self._toggle_ruler_mode)
        self.profile_action = self._tool_action(
            themed_icon("profile.png"),
            "Plot Flux Profile along a line cut",
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
        layout.addStretch()

        # File navigation, in the middle
        nav = QHBoxLayout()
        nav.setSpacing(0)
        self._first_file_btn = QPushButton("|◄")
        self._first_file_btn.setToolTip("Go to first file")
        self._first_file_btn.clicked.connect(self._on_first_file)
        self._prev_file_btn = QPushButton("◀")
        self._prev_file_btn.setToolTip("Previous file in directory ([)")
        self._prev_file_btn.clicked.connect(self._on_prev_file)
        self._file_pos_label = QLabel("—/—")
        self._file_pos_label.setObjectName("Meta")
        self._file_pos_label.setAlignment(Qt.AlignCenter)
        # Room for a count like 12/345, the text centred in it so a short
        # one leaves no gap before the next button
        self._file_pos_label.setMinimumWidth(
            self._file_pos_label.fontMetrics().horizontalAdvance("00/000") + 8
        )
        self._next_file_btn = QPushButton("▶")
        self._next_file_btn.setToolTip("Next file in directory (])")
        self._next_file_btn.clicked.connect(self._on_next_file)
        self._last_file_btn = QPushButton("►|")
        self._last_file_btn.setToolTip("Go to last file")
        self._last_file_btn.clicked.connect(self._on_last_file)
        self._filter_btn = QPushButton()
        self._filter_btn.setIcon(QIcon(themed_icon("filter.svg")))
        self._filter_btn.setIconSize(QSize(14, 14))
        self._filter_btn.setToolTip("Set file filter pattern")
        self._filter_btn.clicked.connect(self._show_filter_dialog)
        self._playlist_btn = QPushButton("☰")
        self._playlist_btn.setToolTip("Show all files in directory")
        self._playlist_btn.clicked.connect(self._show_playlist_dialog)
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
            if isinstance(widget, QPushButton):
                widget.setObjectName("NavButton")
            nav.addWidget(widget)
        layout.addLayout(nav)
        layout.addStretch()

        # Zoom at the right: in, how far in, out, reset, one degree
        self.zoom_in_action = self._tool_action(
            get_resource_path("assets/zoom_in.png"), "Zoom In (+)", self.zoom_in
        )
        self.zoom_out_action = self._tool_action(
            get_resource_path("assets/zoom_out.png"), "Zoom Out (-)", self.zoom_out
        )
        self.reset_view_action = self._tool_action(
            get_resource_path("assets/reset.png"),
            "Reset View (R)",
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


        parent_layout.addWidget(bar)

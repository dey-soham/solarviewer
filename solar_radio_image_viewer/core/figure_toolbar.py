"""
The toolbar under the image: tools, zoom and view buttons, TB and HPC, and
the file navigation buttons.
"""

import logging
from PyQt5.QtWidgets import (
    QWidget,
    QAction,
    QLabel,
    QPushButton,
    QToolBar,
    QActionGroup,
    QSizePolicy,
)
from PyQt5.QtCore import QSize
from PyQt5.QtGui import QIcon
from ..ui.icons import get_resource_path, themed_icon
from ..tools import (
    RegionMode,
)

logger = logging.getLogger(__name__)


class FigureToolbarMixin:
    """Builds the toolbar under the image.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``_on_first_file``, ``_on_last_file``,
    ``_on_next_file``, ``_on_prev_file``, ``_show_filter_dialog``,
    ``_show_playlist_dialog``, ``_toggle_profile_mode``,
    ``_toggle_ruler_mode``, ``_toggle_tb_mode``,
    ``launch_helioprojective_viewer``, ``reset_view``,
    ``set_region_mode``, ``show_plot_customization_dialog``,
    ``zoom_60arcmin``, ``zoom_in``, ``zoom_out``
    """

    def setup_figure_toolbar(self, parent_layout):
        """Set up the figure toolbar with zoom and other controls"""
        toolbar = QToolBar()
        toolbar.setIconSize(QSize(18, 18))
        action_group = QActionGroup(self)

        # Pan action (default)
        self.pan_action = QAction(
            QIcon(get_resource_path("assets/pan.png")),
            "",
            self,
        )
        self.pan_action.setToolTip(
            "Pan: drag, or middle-drag in any mode. Scroll to zoom, arrows to move"
        )
        self.pan_action.setCheckable(True)
        self.pan_action.setChecked(True)
        self.pan_action.triggered.connect(lambda: self.set_region_mode(RegionMode.PAN))
        action_group.addAction(self.pan_action)
        toolbar.addAction(self.pan_action)

        self.rect_action = QAction(
            QIcon(get_resource_path("assets/rectangle_selection.png")),
            "",
            self,
        )
        self.rect_action.setToolTip("Rectangle Select")
        self.rect_action.setCheckable(True)
        self.rect_action.setChecked(False)
        self.rect_action.triggered.connect(
            lambda: self.set_region_mode(RegionMode.RECTANGLE)
        )
        action_group.addAction(self.rect_action)
        toolbar.addAction(self.rect_action)

        # Ellipse selection action
        self.ellipse_action = QAction(
            QIcon(get_resource_path("assets/ellipse_selection.png")),
            "",
            self,
        )
        self.ellipse_action.setToolTip("Ellipse Select")
        self.ellipse_action.setCheckable(True)
        self.ellipse_action.setChecked(False)
        self.ellipse_action.triggered.connect(
            lambda: self.set_region_mode(RegionMode.ELLIPSE)
        )
        action_group.addAction(self.ellipse_action)

        self.zoom_in_action = QAction(
            QIcon(get_resource_path("assets/zoom_in.png")),
            "",
            self,
        )
        self.zoom_in_action.setToolTip("Zoom In (+)")
        self.zoom_in_action.triggered.connect(self.zoom_in)
        self.zoom_out_action = QAction(
            QIcon(get_resource_path("assets/zoom_out.png")),
            "",
            self,
        )
        self.zoom_out_action.setToolTip("Zoom Out (-)")
        self.zoom_out_action.triggered.connect(self.zoom_out)
        self.reset_view_action = QAction(
            QIcon(get_resource_path("assets/reset.png")),
            "",
            self,
        )
        self.reset_view_action.setToolTip("Reset View (R)")
        self.reset_view_action.triggered.connect(
            lambda: self.reset_view(show_status_message=True, animate=True)
        )
        self.zoom_60arcmin_action = QAction(
            QIcon(get_resource_path("assets/zoom_60arcmin.png")),
            "",
            self,
        )
        self.zoom_60arcmin_action.setToolTip("1°×1° Zoom (1)")
        self.zoom_60arcmin_action.triggered.connect(self.zoom_60arcmin)

        # Add customize plot action
        self.customize_plot_action = QAction(
            QIcon(get_resource_path("assets/settings.png")),
            "",
            self,
        )
        self.customize_plot_action.setToolTip("Customize Plot Appearance")
        self.customize_plot_action.triggered.connect(
            self.show_plot_customization_dialog
        )

        # Ruler/distance measurement action
        from ..ui.styles import get_icon_path

        self.ruler_action = QAction(
            QIcon(get_resource_path(f"assets/{get_icon_path('ruler.png')}")),
            "",
            self,
        )
        self.ruler_action.setToolTip(
            "Measure Angular Distance (Select two points on the map)"
        )
        self.ruler_action.setCheckable(True)
        self.ruler_action.setChecked(False)
        self.ruler_action.toggled.connect(
            self._toggle_ruler_mode
        )  # Use toggled instead of triggered
        action_group.addAction(
            self.ruler_action
        )  # Add to action group with rect/ellipse

        # Profile cut action
        self.profile_action = QAction(
            QIcon(get_resource_path(f"assets/{get_icon_path('profile.png')}")),
            "",
            self,
        )
        self.profile_action.setToolTip("Plot Flux Profile along a line cut")
        self.profile_action.setCheckable(True)
        self.profile_action.setChecked(False)
        self.profile_action.toggled.connect(self._toggle_profile_mode)
        action_group.addAction(self.profile_action)

        # Add drawing/measurement tools
        toolbar.addActions(
            [
                self.rect_action,
                self.ellipse_action,
                self.ruler_action,
                self.profile_action,
            ]
        )

        toolbar.addSeparator()

        # Add zoom and view tools
        toolbar.addActions(
            [
                self.zoom_in_action,
                self.zoom_out_action,
                self.reset_view_action,
                self.zoom_60arcmin_action,
                self.customize_plot_action,
            ]
        )

        # Add a spacer to push the helioprojective button to the right
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)

        # Brightness temperature and helioprojective toggles: checkable
        # buttons, filled in the accent colour while on
        self.tb_btn = QPushButton("TB")
        self.tb_btn.setToolTip(
            "Convert to Brightness Temperature (K) - requires Jy/beam units"
        )
        self.tb_btn.setCheckable(True)
        self.tb_btn.setEnabled(False)  # enabled when the units are Jy/beam
        self.tb_btn.clicked.connect(self._toggle_tb_mode)
        toolbar.addWidget(self.tb_btn)

        self.hpc_btn = QPushButton("HPC")
        self.hpc_btn.setToolTip("Convert to Helioprojective view")
        self.hpc_btn.clicked.connect(self.launch_helioprojective_viewer)
        toolbar.addWidget(self.hpc_btn)
        toolbar.addSeparator()

        # File navigation
        self._first_file_btn = QPushButton("|◄")
        self._first_file_btn.setToolTip("Go to first file")
        self._first_file_btn.clicked.connect(self._on_first_file)
        self._first_file_btn.setEnabled(False)
        toolbar.addWidget(self._first_file_btn)

        self._prev_file_btn = QPushButton("◀")
        self._prev_file_btn.setToolTip("Previous file in directory")
        self._prev_file_btn.clicked.connect(self._on_prev_file)
        self._prev_file_btn.setEnabled(False)
        toolbar.addWidget(self._prev_file_btn)

        self._next_file_btn = QPushButton("▶")
        self._next_file_btn.setToolTip("Next file in directory")
        self._next_file_btn.clicked.connect(self._on_next_file)
        self._next_file_btn.setEnabled(False)
        toolbar.addWidget(self._next_file_btn)

        self._last_file_btn = QPushButton("►|")
        self._last_file_btn.setToolTip("Go to last file")
        self._last_file_btn.clicked.connect(self._on_last_file)
        self._last_file_btn.setEnabled(False)
        toolbar.addWidget(self._last_file_btn)

        self._filter_btn = QPushButton()
        self._filter_btn.setIcon(QIcon(themed_icon("settings.png")))
        self._filter_btn.setIconSize(QSize(16, 16))
        self._filter_btn.setToolTip("Set file filter pattern")
        self._filter_btn.clicked.connect(self._show_filter_dialog)
        toolbar.addWidget(self._filter_btn)

        self._playlist_btn = QPushButton("☰")
        self._playlist_btn.setToolTip("Show all files in directory")
        self._playlist_btn.clicked.connect(self._show_playlist_dialog)
        toolbar.addWidget(self._playlist_btn)

        self._file_pos_label = QLabel("")
        self._file_pos_label.setObjectName("Hint")
        toolbar.addWidget(self._file_pos_label)
        for button in (
            self._first_file_btn,
            self._prev_file_btn,
            self._next_file_btn,
            self._last_file_btn,
            self._filter_btn,
            self._playlist_btn,
        ):
            button.setObjectName("NavButton")

        parent_layout.addWidget(toolbar)

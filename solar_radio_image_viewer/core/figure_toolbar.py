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
from ..ui.styles import (
    theme_manager,
)
from ..ui.icons import get_resource_path
from ..tools import (
    RegionMode,
)

logger = logging.getLogger(__name__)


class FigureToolbarMixin:
    """Builds the toolbar under the image and styles its buttons for the theme.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``_on_first_file``, ``_on_last_file``,
    ``_on_next_file``, ``_on_prev_file``, ``_show_filter_dialog``,
    ``_show_playlist_dialog``, ``_toggle_profile_mode``,
    ``_toggle_ruler_mode``, ``_toggle_tb_mode``,
    ``launch_helioprojective_viewer``, ``nav_toolbar``, ``reset_view``,
    ``set_region_mode``, ``show_plot_customization_dialog``,
    ``zoom_60arcmin``, ``zoom_in``, ``zoom_out``
    """

    def setup_figure_toolbar(self, parent_layout):
        """Set up the figure toolbar with zoom and other controls"""
        toolbar = QToolBar()
        toolbar.setIconSize(QSize(18, 18))
        toolbar.setFixedHeight(28)
        toolbar.setStyleSheet(
            """
            QToolBar { padding: 0px; margin: 0px; spacing: 1px; }
            QToolButton { padding: 2px 3px; margin: 0px; max-height: 24px; }
        """
        )
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

        # Step back and forward through the views zoomed and panned to
        self.view_back_action = QAction(self.nav_toolbar._icon("back.png"), "", self)
        self.view_back_action.setToolTip("Previous View")
        self.view_back_action.triggered.connect(lambda: self.nav_toolbar.back())
        self.view_forward_action = QAction(
            self.nav_toolbar._icon("forward.png"), "", self
        )
        self.view_forward_action.setToolTip("Next View")
        self.view_forward_action.triggered.connect(lambda: self.nav_toolbar.forward())

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

        # Add theme-aware vertical separator before zoom buttons

        self._profile_zoom_separator = QWidget()
        self._profile_zoom_separator.setFixedWidth(1)
        self._profile_zoom_separator.setFixedHeight(24)
        self._update_profile_zoom_separator_style()
        toolbar.addWidget(self._profile_zoom_separator)

        # Add zoom and view tools
        toolbar.addActions(
            [
                self.zoom_in_action,
                self.zoom_out_action,
                self.reset_view_action,
                self.zoom_60arcmin_action,
                self.view_back_action,
                self.view_forward_action,
                self.customize_plot_action,
            ]
        )

        # Add a spacer to push the helioprojective button to the right
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)

        # Add brightness temperature toggle button
        self.tb_btn = QPushButton("TB")
        self.tb_btn.setToolTip(
            "Convert to Brightness Temperature (K) - requires Jy/beam units"
        )
        self.tb_btn.setCheckable(True)
        self.tb_btn.setEnabled(
            False
        )  # Disabled by default, enabled when units are Jy/beam
        # Style will be applied by _update_special_button_styles()
        self.tb_btn.clicked.connect(self._toggle_tb_mode)
        toolbar.addWidget(self.tb_btn)

        # Add small spacer between TB and HPC buttons
        tb_hpc_spacer = QWidget()
        tb_hpc_spacer.setFixedWidth(2)
        toolbar.addWidget(tb_hpc_spacer)

        # Add helioprojective viewer button
        self.hpc_btn = QPushButton("HPC")
        self.hpc_btn.setToolTip("Convert to Helioprojective view")
        # Style will be applied by _update_special_button_styles()
        self.hpc_btn.clicked.connect(self.launch_helioprojective_viewer)
        toolbar.addWidget(self.hpc_btn)
        self._update_special_button_styles()

        # File navigation buttons - create first, style later so we can update on theme change
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

        self._filter_btn = QPushButton("⚙")
        self._filter_btn.setToolTip("Set file filter pattern")
        self._filter_btn.clicked.connect(self._show_filter_dialog)
        toolbar.addWidget(self._filter_btn)

        self._playlist_btn = QPushButton("☰")  # Use hamburger menu icon (smaller)
        self._playlist_btn.setToolTip("Show all files in directory")
        self._playlist_btn.clicked.connect(self._show_playlist_dialog)
        toolbar.addWidget(self._playlist_btn)

        self._file_pos_label = QLabel("")
        toolbar.addWidget(self._file_pos_label)

        # Apply initial styles (will also register theme callback)
        self._update_nav_button_styles()

        parent_layout.addWidget(toolbar)

    def _update_profile_zoom_separator_style(self):
        """Update the profile/zoom separator style based on current theme"""
        from ..ui.styles import theme_manager

        if not hasattr(self, "_profile_zoom_separator"):
            return

        # Get theme-appropriate separator color
        if theme_manager.is_dark:
            sep_color = "#546E7A"  # Blue-grey for dark theme
        else:
            sep_color = "#9E9E9E"  # Medium grey for light theme

        self._profile_zoom_separator.setStyleSheet(
            f"""
            QWidget {{
                background-color: {sep_color};
            }}
        """
        )

        # Register theme callback if not already done
        if not hasattr(self, "_separator_theme_callback_registered"):
            theme_manager.register_callback(
                lambda t: self._update_profile_zoom_separator_style(), owner=self
            )
            self._separator_theme_callback_registered = True

    def _update_nav_button_styles(self):
        """Update navigation button styles based on current theme"""
        from ..ui.styles import theme_manager

        if not hasattr(self, "_prev_file_btn"):
            return

        palette = theme_manager.palette

        # Get theme-appropriate colors
        if theme_manager.is_dark:
            btn_bg = palette.get("surface", "#455A64")
            btn_hover = palette.get("highlight", "#546E7A")
            btn_pressed = palette.get("base", "#37474F")
            btn_disabled_text = "#757575"
            btn_text = palette.get("text", "white")
            label_color = palette.get("text_secondary", "#B0BEC5")
        else:
            btn_bg = palette.get("surface", "#D7D3BF")
            btn_hover = palette.get("highlight", "#B8B5A2")
            btn_pressed = palette.get("base", "#ECEBDE")
            btn_disabled_text = "#9E9E9E"
            btn_text = palette.get("text", "#1a1a1a")
            label_color = palette.get("text", "#333333")

        nav_btn_style = f"""
            QPushButton {{
                background-color: {btn_bg};
                color: {btn_text};
                border: none;
                padding: 2px 8px;
                border-radius: 6px;
                min-width: 16px;
                min-height: 14px;
                font-weight: bold;
                font-size: 14px;
            }}
            QPushButton:hover {{
                background-color: {btn_hover};
            }}
            QPushButton:pressed {{
                background-color: {btn_pressed};
            }}
            QPushButton:disabled {{
                background-color: transparent;
                color: {btn_disabled_text};
            }}
        """

        # Style for larger icons (first, last, filter, playlist)
        nav_btn_style_large = nav_btn_style.replace(
            "font-size: 16px", "font-size: 14px"
        )
        nav_btn_style_xl = nav_btn_style.replace("font-size: 16px", "font-size: 16px")

        # Apply to navigation buttons - prev/next use base size, others use larger
        self._first_file_btn.setStyleSheet(nav_btn_style_large)
        self._prev_file_btn.setStyleSheet(nav_btn_style)
        self._next_file_btn.setStyleSheet(nav_btn_style)
        self._last_file_btn.setStyleSheet(nav_btn_style_large)
        self._filter_btn.setStyleSheet(nav_btn_style_xl)
        self._playlist_btn.setStyleSheet(nav_btn_style_xl)
        self._file_pos_label.setStyleSheet(
            f"color: {label_color}; padding: 0 5px; font-size: 14px;"
        )

        # Register theme callback if not already done
        if not hasattr(self, "_nav_theme_callback_registered"):
            theme_manager.register_callback(
                lambda t: self._update_nav_button_styles(), owner=self
            )
            self._nav_theme_callback_registered = True

    def _update_special_button_styles(self):
        """Update styles for the TB and HPC buttons based on current theme.

        These buttons have gradient backgrounds and need theme-aware disabled states.
        """
        # Determine disabled colors based on theme
        if theme_manager.is_dark:
            disabled_bg = "#4a4a6a"
            disabled_text = "#6b7280"
        else:
            disabled_bg = "#C5C5C5"
            disabled_text = "#8E8E8E"

        # TB Button (orange gradient)
        if hasattr(self, "tb_btn"):
            self.tb_btn.setStyleSheet(
                f"""
                QPushButton {{
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, 
                        stop:0 #f59e0b, stop:1 #fbbf24);
                    color: #1f2937;
                    border: none;
                    padding: 4px 12px;
                    border-radius: 6px;
                    min-width: 28px;
                    min-height: 20px;
                    font-size: 11pt;
                    font-weight: 600;
                }}
                QPushButton:hover {{
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, 
                        stop:0 #fbbf24, stop:1 #fcd34d);
                }}
                QPushButton:pressed, QPushButton:checked {{
                    background: #d97706;
                    color: white;
                }}
                QPushButton:disabled {{
                    background: {disabled_bg};
                    color: {disabled_text};
                }}
            """
            )

        # HPC Button (blue gradient)
        if hasattr(self, "hpc_btn"):
            self.hpc_btn.setStyleSheet(
                f"""
                QPushButton {{
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, 
                        stop:0 #3b82f6, stop:1 #60a5fa);
                    color: white;
                    border: none;
                    padding: 4px 12px;
                    border-radius: 6px;
                    min-width: 36px;
                    min-height: 20px;
                    font-size: 11pt;
                    font-weight: 600;
                }}
                QPushButton:hover {{
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, 
                        stop:0 #60a5fa, stop:1 #93c5fd);
                }}
                QPushButton:pressed {{
                    background: #1d4ed8;
                }}
                QPushButton:disabled {{
                    background: {disabled_bg};
                    color: {disabled_text};
                }}
            """
            )

        # NOAA Events Button (purple gradient)
        # Register theme callback if not already done
        if not hasattr(self, "_special_btn_theme_callback_registered"):
            theme_manager.register_callback(
                lambda t: self._update_special_button_styles(), owner=self
            )
            self._special_btn_theme_callback_registered = True

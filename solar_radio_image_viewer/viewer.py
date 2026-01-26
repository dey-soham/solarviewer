import sys
import os
import tempfile

import numpy as np
from importlib import resources as importlib_resources
import matplotlib

matplotlib.use("Qt5Agg")
import matplotlib.pyplot as plt
import matplotlib.style as mplstyle
from matplotlib.colors import Normalize, LogNorm
from matplotlib.patches import Ellipse
from matplotlib.figure import Figure
from matplotlib.widgets import RectangleSelector
from matplotlib.backends.backend_qt5agg import (
    FigureCanvasQTAgg as FigureCanvas,
    NavigationToolbar2QT as NavigationToolbar,
)
from matplotlib import rcParams

from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QAction,
    QFileDialog,
    QMessageBox,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QFrame,
    # QInputDialog,
    # QMenuBar,
    QMenu,
    QRadioButton,
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    # QPlainTextEdit,
    # QListWidget,
    QSpinBox,
    QCheckBox,
    QGridLayout,
    QStatusBar,
    QMenuBar,
    QGroupBox,
    QToolBar,
    QHeaderView,
    QFormLayout,
    QSplitter,
    # QListWidget,
    # QListWidgetItem,
    QActionGroup,
    QDoubleSpinBox,
    QToolButton,
    QTabBar,
    # QStyle,
    QSizePolicy,
)
from PyQt5.QtCore import Qt, QSettings, QSize, QTimer, pyqtSignal, QThread
from PyQt5.QtGui import QIcon, QIntValidator, QColor, QPalette
from PyQt5.QtWidgets import QStyledItemDelegate

# from PyQt5.QtGui import QColor, QPalette, QPainter

from .norms import SqrtNorm, AsinhNorm, PowerNorm, ZScaleNorm, HistEqNorm
from .utils import (
    # estimate_rms_near_Sun,
    # remove_pixels_away_from_sun,
    get_pixel_values_from_image,
    get_image_metadata,
    # twoD_gaussian,
    twoD_elliptical_ring,
    IA,
)
from .styles import (
    STYLESHEET,
    DARK_PALETTE,
    LIGHT_PALETTE,
    theme_manager,
    get_stylesheet,
    get_icon_path,
    set_hand_cursor,
)
from .dialogs import UpdateDialog
from .searchable_combobox import ColormapSelector
from astropy.time import Time
from .utils.update_checker import check_for_updates
from .version import __version__
from sunpy.map import Map


class CheckUpdateThread(QThread):
    """Background thread to check for updates on PyPI."""

    update_available = pyqtSignal(str)  # Emits latest version string

    def run(self):
        try:
            is_available, latest_ver, error = check_for_updates(__version__)
            if is_available and latest_ver:
                self.update_available.emit(latest_ver)
        except Exception:
            pass  # Silently fail for updates


class DisabledItemDelegate(QStyledItemDelegate):
    """Custom delegate that properly renders disabled items with grayed text."""

    def paint(self, painter, option, index):
        # Check if item is disabled
        if not (index.flags() & Qt.ItemIsEnabled):
            # Get disabled color from theme
            try:
                disabled_color = QColor(
                    theme_manager.palette.get("disabled", "#cccccc")
                )
            except:
                disabled_color = QColor("#cccccc")

            # Modify the palette to use disabled color for text
            option.palette.setColor(QPalette.Text, disabled_color)
            option.palette.setColor(QPalette.HighlightedText, disabled_color)

        super().paint(painter, option, index)


def update_matplotlib_theme():
    """Update matplotlib rcParams based on current theme."""
    palette = theme_manager.palette
    rcParams.update(theme_manager.matplotlib_params)


# Package root directory for fast resource loading
_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


def get_resource_path(relative_path):
    """Get absolute path to a package resource file.

    Uses os.path for speed (avoids slow pkg_resources import).

    Args:
        relative_path: Path relative to the package directory (e.g., 'assets/icon.png')

    Returns:
        Absolute path to the resource file
    """
    return os.path.join(_PACKAGE_DIR, relative_path)


def themed_icon(icon_name):
    """Get the full resource path for a theme-appropriate icon.

    Args:
        icon_name: Base icon filename (e.g., 'browse.png')

    Returns:
        Full resource path to the appropriate icon
    """
    themed_name = get_icon_path(icon_name)
    return get_resource_path(f"assets/{themed_name}")


# Initialize matplotlib with default theme
rcParams["axes.linewidth"] = 1.4
rcParams["font.size"] = 12
update_matplotlib_theme()
mplstyle.use("fast")


# For region selection modes
class RegionMode:
    RECTANGLE = 0
    ELLIPSE = 1


class SegmentedControl(QWidget):
    """A modern segmented control widget."""

    selectionChanged = pyqtSignal(int)  # Emits index of selected segment

    def __init__(self, options, parent=None):
        super().__init__(parent)
        self.options = options
        self._selected_index = 0
        self._buttons = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Create container for the pill shape
        self._container = QWidget()
        container_layout = QHBoxLayout(self._container)
        container_layout.setContentsMargins(2, 2, 2, 2)
        container_layout.setSpacing(1)

        for i, option in enumerate(options):
            btn = QPushButton(option)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked, idx=i: self._on_button_clicked(idx))
            self._buttons.append(btn)
            container_layout.addWidget(btn)

        # Select first by default
        if self._buttons:
            self._buttons[0].setChecked(True)

        layout.addWidget(self._container)
        self._apply_styles()

        # Register for theme changes
        theme_manager.register_callback(self._on_theme_changed)

    def _on_theme_changed(self, theme):
        """Update styling when theme changes."""
        self._apply_styles()

    def _apply_styles(self):
        """Apply button-style toggle styling matching app palette."""
        from .styles import theme_manager

        palette = theme_manager.palette
        is_dark = theme_manager.is_dark

        # Container styling - subtle pill background
        if is_dark:
            surface = palette.get("surface", "#16162a")
            border_style = "none"
            hover_bg = palette.get("button_hover", "#32325d")
        else:
            surface = "rgba(0, 0, 0, 0.03)"
            border_color = palette.get("border", "#d1d5db")
            border_style = f"1px solid {border_color}"
            hover_bg = "rgba(0, 0, 0, 0.06)"

        self._container.setStyleSheet(
            f"""
            QWidget {{
                background-color: {surface};
                border: {border_style};
                border-radius: 8px;
            }}
        """
        )

        # Button styling - matches app button palette
        text_color = palette.get("text", "#f0f0f5")
        text_secondary = palette.get("text_secondary", "#a0a0b0")
        highlight = palette.get("highlight", "#6366f1")
        highlight_hover = palette.get("highlight_hover", "#818cf8")

        for btn in self._buttons:
            btn.setStyleSheet(
                f"""
                QPushButton {{
                    background-color: transparent;
                    color: {text_secondary};
                    border: none;
                    border-radius: 5px;
                    padding: 4px 10px;
                    font-weight: 500;
                    font-size: 10pt;
                    min-width: 65px;
                    min-height: 22px;
                }}
                QPushButton:hover {{
                    color: {text_color};
                    background-color: {hover_bg};
                }}
                QPushButton:checked {{
                    background-color: {highlight};
                    color: #ffffff;
                    font-weight: 600;
                }}
                QPushButton:checked:hover {{
                    background-color: {highlight_hover};
                }}
            """
            )

    def _on_button_clicked(self, index):
        """Handle button click - ensure only one is selected."""
        if index == self._selected_index:
            # Re-check if clicking already selected (don't allow deselect)
            self._buttons[index].setChecked(True)
            return

        # Uncheck all others
        for i, btn in enumerate(self._buttons):
            btn.setChecked(i == index)

        self._selected_index = index
        self.selectionChanged.emit(index)

    def selectedIndex(self):
        """Return the currently selected index."""
        return self._selected_index

    def setSelectedIndex(self, index):
        """Set the selected index programmatically."""
        if 0 <= index < len(self._buttons):
            self._on_button_clicked(index)

    def selectedText(self):
        """Return the text of the selected option."""
        return self.options[self._selected_index]

    def updateTheme(self):
        """Update styling when theme changes."""
        self._apply_styles()


class SolarRadioImageTab(QWidget):
    def __init__(self, parent=None, tab_name=""):
        super().__init__(parent)
        self.setObjectName(tab_name)
        # Use current theme's stylesheet instead of hardcoded dark theme
        self.setStyleSheet(theme_manager.stylesheet)

        # Register theme change callback for icon updates
        theme_manager.register_callback(self._on_theme_change)

        self.stokes_combo = None
        self.current_image_data = None
        self.current_wcs = None
        self.current_contour_wcs = None
        self.psf = None
        self.current_roi = None
        self.roi_selector = None
        self.imagename = None
        self.solar_disk_center = None
        self.solar_disk_diameter_arcmin = 32.0
        # Solar disk style properties
        self.solar_disk_style = {
            "color": "white",
            "linestyle": "--",
            "linewidth": 1.8,
            "alpha": 0.6,
            "show_center": False,
        }

        # Beam style properties
        self.beam_style = {
            "edgecolor": "black",
            "facecolor": "white",
            "linewidth": 1.5,
            "alpha": 0.4,
        }

        # Grid style properties
        self.grid_style = {
            "color": "white",
            "linestyle": "--",
            "alpha": 0.5,
        }

        # Initialize RMS box values
        self.current_rms_box = [0, 200, 0, 130]

        # File navigation state
        self._file_list = []  # List of file paths in current directory
        self._file_list_index = -1  # Current position in file list
        self._file_filter_pattern = "*"  # Glob pattern for filtering
        self._file_base_dir = None  # Base directory of loaded file

        # Remote file navigation state
        self._remote_file_list = []  # List of RemoteFileInfo for remote navigation
        self._remote_base_dir = None  # Remote directory path
        self._is_remote_mode = False  # Whether current file is from remote

        # Ruler/distance measurement state
        self._ruler_mode = False
        self._ruler_start = None  # (x, y) pixel coords
        self._ruler_line = None  # matplotlib line object
        self._ruler_text = None  # matplotlib text annotation

        # Profile cut tool state
        self._profile_mode = False
        self._profile_method = "line"  # "line" or "radial"
        self._profile_start = None  # (x, y) pixel coords
        self._profile_line = None  # matplotlib line object
        self._profile_crosshairs = []  # crosshair line objects
        self._profile_preview_line = None  # live preview line

        # Brightness temperature map state
        self._tb_mode = False  # Currently showing TB map?
        self._tb_original_imagename = None  # Original file before TB/Flux conversion
        self._tb_original_unit = None  # Original unit before conversion
        self._tb_temp_file = None  # Path to TB/Flux temp file
        self._original_flux_data = None  # Store original flux data when in TB mode
        self._original_bunit = None  # Store original unit

        # HPC (Helioprojective) conversion state
        self._original_imagename = None  # Original RA/Dec file before HPC conversion
        self._hpc_temp_file = None  # Path to HPC temp file
        self._hpc_original_imagename = (
            None  # Original HPC file before RA/Dec conversion
        )
        self._radec_temp_file = None  # Path to RA/Dec temp file (from HPC->RA/Dec)

        # Contour coordinate transformation state
        self._contour_transformed_file = (
            None  # Path to coordinate-transformed contour file
        )
        self._contour_transformed_from = (
            None  # Original external image that was transformed
        )
        self._contour_was_transformed = (
            False  # Flag to skip reprojection when transformation aligned coordinates
        )

        # Track non-modal dialogs to prevent garbage collection
        self._open_dialogs = []

        # Unique ID for temp file naming (prevents collisions in multi-tab)
        import uuid

        self._temp_file_id = uuid.uuid4().hex[:8]

        # Image metadata cache for lazy WCS/colorbar computation
        # These are expensive to compute and only need to refresh when imagename changes
        self._cached_imagename = None  # Track which image is cached
        self._cached_fits_header = None  # FITS header cache
        self._cached_fits_flag = False  # Whether current image is FITS
        self._cached_csys = None  # CASA coordsys cache
        self._cached_summary = None  # CASA summary cache
        self._cached_csys_record = None  # CASA csys record cache

        # Cache for reprojected contours to avoid redundant heavy computations
        # Key: (base_image_path, contour_image_path, stokes, visualization_params...)
        # Value: (reprojected_data, extended_wcs, extended_shape, contour_offset)
        self._reproject_cache = {}

        # Debounce timers for UI responsiveness
        self._gamma_debounce_timer = None

        self.contour_settings = {
            "source": "same",
            "external_image": "",
            "stokes": "I",
            "pos_levels": [0.1, 0.3, 0.5, 0.7, 0.9],
            "neg_levels": [0.1, 0.3, 0.5, 0.7, 0.9],
            "levels": [0.1, 0.3, 0.5, 0.7, 0.9],
            "level_type": "fraction",
            "color": "white",
            "linewidth": 1.0,
            "pos_linestyle": "-",
            "neg_linestyle": "--",
            "linestyle": "-",
            "contour_data": None,
            "use_default_rms_region": True,
            "rms_box": (0, 200, 0, 130),
            "show_full_extent": False,  # If True, show full contour extent (Default: capped at 1.5x base image)
            "dim_h": 0,
            "dim_w": 0,
        }

        # Plot customization settings
        self.plot_settings = {
            "xlabel": "",
            "ylabel": "",
            "title": "",
            "colorbar_label": "",
            "axis_label_fontsize": 12,
            "axis_tick_fontsize": 10,
            "title_fontsize": 12,
            "colorbar_label_fontsize": 10,
            "colorbar_tick_fontsize": 10,
            "plot_bg_color": "auto",
            "figure_bg_color": "auto",
            "text_color": "auto",
            "tick_color": "auto",
            "border_color": "auto",
            "border_width": 1.0,
            "tick_direction": "out",
            "tick_length": 4,
            "tick_width": 1.0,
            # Padding settings
            "pad_left": 0.135,
            "pad_right": 1.0,
            "pad_top": 0.95,
            # "pad_bottom": 0.05,
            "pad_bottom": 0.08,
            "pad_wspace": 0.2,
            "pad_hspace": 0.2,
            "use_tight_layout": False,
        }

        # Rendering optimization state
        self._last_rendered_state = {}
        self.image_plot = None

        self.setup_ui()
        self._setup_file_navigation_shortcuts()
        self._set_button_cursors()

    def _setup_file_navigation_shortcuts(self):
        """Setup keyboard shortcuts for file navigation within this tab"""
        from PyQt5.QtWidgets import QShortcut
        from PyQt5.QtGui import QKeySequence

        # Use [ and ] for prev/next
        # Use { and } (Shift+[ Shift+]) for first/last
        prev_shortcut = QShortcut(QKeySequence("["), self)
        prev_shortcut.activated.connect(self._on_prev_file)

        next_shortcut = QShortcut(QKeySequence("]"), self)
        next_shortcut.activated.connect(self._on_next_file)

        first_shortcut = QShortcut(QKeySequence("{"), self)  # Shift+[
        first_shortcut.activated.connect(self._on_first_file)

        last_shortcut = QShortcut(QKeySequence("}"), self)  # Shift+]
        last_shortcut.activated.connect(self._on_last_file)

        # F11 for fullscreen toggle
        # fullscreen_shortcut = QShortcut(QKeySequence("F11"), self)
        # fullscreen_shortcut.activated.connect(self._toggle_fullscreen)

    def _set_button_cursors(self):
        """Set pointing hand cursor on all buttons and interactive widgets for better UX"""
        set_hand_cursor(self)

    def show_status_message(self, message):
        """Helper method to show messages in the status bar"""
        main_window = self.window()
        try:
            if main_window:
                main_window.statusBar().showMessage(message)
                QApplication.processEvents()
        except AttributeError:
            pass

    def setup_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(0)  # Splitter handles spacing

        # Create splitter for resizable panels
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(5)
        splitter.setChildrenCollapsible(True)

        # Left Control Panel
        control_panel = QWidget()
        control_panel.setMinimumWidth(250)
        control_panel.setMaximumWidth(700)
        control_layout = QVBoxLayout(control_panel)
        control_layout.setContentsMargins(0, 0, 10, 0)
        control_layout.setSpacing(15)
        self.create_file_controls(control_layout)
        control_layout.addStretch()
        self.create_display_controls(control_layout)
        splitter.addWidget(control_panel)

        # Center Figure Panel
        figure_panel = QWidget()
        figure_panel.setMinimumWidth(400)
        figure_layout = QVBoxLayout(figure_panel)
        figure_layout.setContentsMargins(0, 0, 0, 0)
        figure_layout.setSpacing(10)
        self.setup_canvas(figure_layout)
        self.setup_figure_toolbar(figure_layout)
        splitter.addWidget(figure_panel)

        # Right Stats Panel
        stats_panel = QWidget()
        stats_panel.setMinimumWidth(250)
        stats_panel.setMaximumWidth(700)
        stats_layout = QVBoxLayout(stats_panel)
        stats_layout.setContentsMargins(10, 0, 0, 0)
        stats_layout.setSpacing(15)
        self.create_stats_table(stats_layout)
        self.create_image_stats_table(stats_layout)
        self.create_noaa_events_button(stats_layout)
        self.create_coord_display(stats_layout)
        splitter.addWidget(stats_panel)

        # Set initial sizes (left: 300, center: stretch, right: 300)
        splitter.setSizes([300, 800, 300])

        main_layout.addWidget(splitter)

    def create_file_controls(self, parent_layout):
        group = QGroupBox("Image Selection")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)  # Tighter spacing for compact layout

        # Modern segmented control for file type selection
        toggle_layout = QHBoxLayout()
        self.file_type_toggle = SegmentedControl(["CASA Image", "FITS File"])
        toggle_layout.addWidget(self.file_type_toggle)
        toggle_layout.addStretch()
        layout.addLayout(toggle_layout)

        # For backward compatibility with radio button checks
        # Create hidden radio buttons that stay in sync with segmented control
        self.selection_type_group = QButtonGroup(self)
        self.radio_casa_image = QRadioButton()
        self.radio_fits_file = QRadioButton()
        self.radio_casa_image.setChecked(True)
        self.radio_casa_image.hide()
        self.radio_fits_file.hide()
        self.selection_type_group.addButton(self.radio_casa_image)
        self.selection_type_group.addButton(self.radio_fits_file)

        # Sync segmented control with hidden radio buttons
        def sync_selection(index):
            if index == 0:
                self.radio_casa_image.setChecked(True)
            else:
                self.radio_fits_file.setChecked(True)

        self.file_type_toggle.selectionChanged.connect(sync_selection)

        file_layout = QHBoxLayout()
        file_layout.setSpacing(8)
        self.dir_entry = QLineEdit()
        self.dir_entry.setPlaceholderText("Select image directory or FITS file...")
        self.dir_entry.setReadOnly(True)
        self.browse_btn = QPushButton()
        self.browse_btn.setObjectName("IconOnlyNBGButton")
        self.browse_btn.setIcon(QIcon(get_resource_path("assets/browse.png")))
        self.browse_btn.setIconSize(QSize(32, 32))
        self.browse_btn.setToolTip("Browse")
        self.browse_btn.setFixedSize(32, 32)
        self.browse_btn.clicked.connect(self.select_file_or_directory)
        self.browse_btn.setStyleSheet(
            """
        QPushButton {
            background-color: transparent;
            min-width: 0px;
            min-height: 0px;
            padding-left: 22px;
            padding-right: 22px;
            padding-top: 22px;
            padding-bottom: 18px;
            margin-top: -4px;
            border-radius: 8px;
        }
        QPushButton:hover {
            background-color: rgba(99, 102, 241, 0.2);
        }
        QPushButton:pressed {
            background-color: rgba(99, 102, 241, 0.35);
        }
        """
        )
        file_layout.addWidget(self.dir_entry, 1)
        file_layout.addWidget(self.browse_btn)
        layout.addLayout(file_layout)

        # Add a separator line
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        layout.addWidget(separator)

        # Row 1: Stokes + Threshold on same row
        stokes_thresh_layout = QHBoxLayout()
        stokes_thresh_layout.setSpacing(12)

        # Stokes dropdown
        stokes_label = QLabel("Stokes:")
        self.stokes_combo = QComboBox()
        self.stokes_combo.addItems(
            ["I", "Q", "U", "V", "L", "Lfrac", "Vfrac", "Q/I", "U/I", "U/V", "PANG"]
        )
        self.stokes_combo.currentTextChanged.connect(self.on_stokes_changed)

        # Use custom delegate to properly render disabled items with grayed text
        self.stokes_combo.setItemDelegate(DisabledItemDelegate(self.stokes_combo))

        stokes_thresh_layout.addStretch()
        # Threshold (sigma)
        thresh_label = QLabel("Thres (σ):")
        thresh_label.setToolTip("Threshold in units of sigma (RMS)")
        self.threshold_entry = QLineEdit("10")
        self.threshold_entry.setFixedWidth(50)
        self.threshold_entry.setToolTip("Threshold in units of sigma (RMS)")

        stokes_thresh_layout.addWidget(stokes_label)
        stokes_thresh_layout.addWidget(self.stokes_combo, 1)
        stokes_thresh_layout.addWidget(thresh_label)
        stokes_thresh_layout.addWidget(self.threshold_entry)
        layout.addLayout(stokes_thresh_layout)

        # Row 2: Fast Load toggle on left, RMS settings on right
        options_row = QHBoxLayout()
        options_row.setSpacing(8)
        options_row.setContentsMargins(0, 2, 0, 2)

        # Fast Load toggle (left side)
        self.downsample_toggle = QCheckBox()
        self.downsample_toggle.setObjectName("FastLoadToggle")
        self.downsample_toggle.setToolTip(
            "Enable fast preview mode for quick image browsing.\n"
            "Images are loaded at reduced resolution for faster display."
        )

        # Theme-aware Fast Load toggle style
        if theme_manager.is_dark:
            toggle_bg = "#3a3d4d"
            toggle_bg_hover = "#4a4d5d"
            toggle_border = "#4a4d5d"
            label_color = "#a5a8b8"
        else:
            toggle_bg = "#d0d0d0"
            toggle_bg_hover = "#c0c0c0"
            toggle_border = "#b0b0b0"
            label_color = "#555555"

        self.downsample_toggle.setStyleSheet(
            f"""
            QCheckBox#FastLoadToggle {{
                spacing: 0px;
            }}
            QCheckBox#FastLoadToggle::indicator {{
                width: 28px;
                height: 14px;
                border-radius: 7px;
                background-color: {toggle_bg};
                border: 1px solid {toggle_border};
            }}
            QCheckBox#FastLoadToggle::indicator:hover {{
                background-color: {toggle_bg_hover};
            }}
            QCheckBox#FastLoadToggle::indicator:checked {{
                background-color: #6366f1;
                border-color: #818cf8;
            }}
            QCheckBox#FastLoadToggle::indicator:checked:hover {{
                background-color: #7c7ff5;
            }}
        """
        )

        fast_icon = QLabel("⚡")
        self.fast_label = QLabel("Fast")
        self.fast_label.setStyleSheet(f"color: {label_color}; font-size: 10pt;")

        # Status badge
        self.downsample_status = QLabel("")
        self.downsample_status.setStyleSheet("background-color: transparent;")

        def update_fast_load_status(checked):
            if checked:
                self.downsample_status.setText("Preview")
                self.downsample_status.setStyleSheet(
                    """
                    QLabel {
                        background-color: rgba(16, 185, 129, 0.15);
                        color: #10b981;
                        font-weight: 600;
                        font-size: 9pt;
                        padding: 1px 6px;
                        border-radius: 3px;
                    }
                """
                )
            else:
                self.downsample_status.setText("")
                self.downsample_status.setStyleSheet("background-color: transparent;")

            # Invalidate contour data when downsample mode changes
            # This ensures contour data will be reloaded at the correct resolution
            self.contour_settings["contour_data"] = None
            self.current_contour_wcs = None

            # If image is already loaded, reload it with new downsample setting
            if self.current_image_data is not None and self.imagename:
                self.on_visualization_changed()
                self.reset_view()

        self.downsample_toggle.stateChanged.connect(update_fast_load_status)

        # Add Fast Load widgets to the left
        options_row.addWidget(self.downsample_toggle)
        options_row.addWidget(fast_icon)
        options_row.addWidget(self.fast_label)
        options_row.addWidget(self.downsample_status)

        # Stretch in the middle
        options_row.addStretch()

        # RMS Box settings on the right (label + icon)
        self.rms_label = QLabel("RMS")
        rms_label_color = "#a5a8b8" if theme_manager.is_dark else "#555555"
        self.rms_label.setStyleSheet(f"color: {rms_label_color}; font-size: 10pt;")
        self.rms_label.setToolTip("RMS Box Settings")

        self.rms_settings_btn = QPushButton()
        self.rms_settings_btn.setObjectName("IconOnlyNBGButton")
        self.rms_settings_btn.setIcon(QIcon(get_resource_path("assets/settings.png")))
        self.rms_settings_btn.setIconSize(QSize(20, 20))
        self.rms_settings_btn.setToolTip(
            "RMS Box Settings - Configure noise estimation region"
        )
        self.rms_settings_btn.setFixedSize(28, 28)
        self.rms_settings_btn.clicked.connect(self.show_rms_box_dialog)
        self.rms_settings_btn.setStyleSheet(
            """
            QPushButton {
                background-color: transparent;
                border-radius: 6px;
                padding: 4px;
            }
            QPushButton:hover {
                background-color: rgba(99, 102, 241, 0.2);
            }
            QPushButton:pressed {
                background-color: rgba(99, 102, 241, 0.35);
            }
        """
        )

        options_row.addWidget(self.rms_label)
        options_row.addWidget(self.rms_settings_btn)

        layout.addLayout(options_row)

        parent_layout.addWidget(group)

    def create_display_controls(self, parent_layout):
        group = QGroupBox("Display Settings")
        main_layout = QVBoxLayout(group)
        main_layout.setSpacing(8)  # Tighter spacing

        # Basic display settings - compact row
        form_layout = QFormLayout()
        radio_colormaps = [
            "viridis",
            "plasma",
            "inferno",
            "magma",
            "gist_heat",
            "hot",
            "CMRmap",
            "gnuplot2",
            "jet",
            "twilight",
        ]
        all_colormaps = sorted(plt.colormaps())
        self.cmap_combo = ColormapSelector(
            preferred_items=radio_colormaps, all_items=all_colormaps
        )
        self.cmap_combo.setCurrentText("viridis")
        self.cmap_combo.colormapSelected.connect(self.update_display)
        form_layout.addRow("Colormap:", self.cmap_combo)

        # Stretch function
        self.stretch_combo = QComboBox()
        self.stretch_combo.addItems(
            ["linear", "sqrt", "log", "arcsinh", "power", "zscale", "histeq"]
        )
        self.stretch_combo.setCurrentText("power")
        self.stretch_combo.currentIndexChanged.connect(self.on_stretch_changed)

        # Add tooltips for each stretch
        stretch_tooltips = {
            0: "Linear stretch - no transformation",
            1: "Square root stretch - enhances faint features",
            2: "Logarithmic stretch - enhances very faint features",
            3: "Arcsinh stretch - similar to log but handles negative values",
            4: "Power law stretch - adjustable using gamma",
            5: "ZScale stretch - automatic contrast based on image statistics",
            6: "Histogram equalization - enhances contrast by redistributing intensities",
        }
        for idx, tooltip in stretch_tooltips.items():
            self.stretch_combo.setItemData(idx, tooltip, Qt.ToolTipRole)

        form_layout.addRow("Stretch:", self.stretch_combo)
        main_layout.addLayout(form_layout)

        # Overlays subgroup
        overlays_group = QGroupBox("Overlays")
        overlays_layout = QVBoxLayout(overlays_group)
        overlays_layout.setSpacing(8)
        overlays_layout.setContentsMargins(8, 8, 8, 8)

        # Modern grid layout with fixed column widths for perfect alignment
        grid_layout = QGridLayout()
        grid_layout.setSpacing(6)
        grid_layout.setContentsMargins(0, 0, 0, 0)

        # Theme-aware toggle style for overlay checkboxes
        if theme_manager.is_dark:
            toggle_bg = "#3a3d4d"
            toggle_bg_hover = "#4a4d5d"
            toggle_border = "#4a4d5d"
            toggle_border_hover = "#5a5d6d"
            text_color = "#e0e0e0"
        else:
            toggle_bg = "#d0d0d0"
            toggle_bg_hover = "#c0c0c0"
            toggle_border = "#b0b0b0"
            toggle_border_hover = "#a0a0a0"
            text_color = "#333333"

        overlay_toggle_style = f"""
            QCheckBox {{
                spacing: 6px;
                font-size: 11pt;
                color: {text_color};
            }}
            QCheckBox::indicator {{
                width: 28px;
                height: 14px;
                border-radius: 7px;
                background-color: {toggle_bg};
                border: 1px solid {toggle_border};
            }}
            QCheckBox::indicator:hover {{
                background-color: {toggle_bg_hover};
                border-color: {toggle_border_hover};
            }}
            QCheckBox::indicator:checked {{
                background-color: #6366f1;
                border-color: #818cf8;
            }}
            QCheckBox::indicator:checked:hover {{
                background-color: #7c7ff5;
            }}
        """

        # Modern settings button style (works for both themes)
        settings_btn_style = """
            QPushButton {
                background-color: transparent;
                border-radius: 4px;
                padding: 2px;
                border: none;
            }
            QPushButton:hover {
                background-color: rgba(99, 102, 241, 0.2);
            }
            QPushButton:pressed {
                background-color: rgba(99, 102, 241, 0.35);
            }
        """

        # Row 0: Show Beam + settings | Show Grid + settings
        self.show_beam_checkbox = QCheckBox("Beam")
        self.show_beam_checkbox.setChecked(True)
        self.show_beam_checkbox.setStyleSheet(overlay_toggle_style)
        self.show_beam_checkbox.stateChanged.connect(self.on_checkbox_changed)

        self.beam_settings_button = QPushButton()
        self.beam_settings_button.setObjectName("IconOnlyNBGButton")
        self.beam_settings_button.setIcon(QIcon(themed_icon("settings.png")))
        self.beam_settings_button.setIconSize(QSize(18, 18))
        self.beam_settings_button.setToolTip("Beam Settings")
        self.beam_settings_button.setFixedSize(24, 24)
        self.beam_settings_button.setStyleSheet(settings_btn_style)
        self.beam_settings_button.clicked.connect(self.show_beam_settings)

        self.show_grid_checkbox = QCheckBox("Grid")
        self.show_grid_checkbox.setChecked(False)
        self.show_grid_checkbox.setStyleSheet(overlay_toggle_style)
        self.show_grid_checkbox.stateChanged.connect(self.on_checkbox_changed)

        self.grid_settings_button = QPushButton()
        self.grid_settings_button.setObjectName("IconOnlyNBGButton")
        self.grid_settings_button.setIcon(QIcon(themed_icon("settings.png")))
        self.grid_settings_button.setIconSize(QSize(18, 18))
        self.grid_settings_button.setToolTip("Grid Settings")
        self.grid_settings_button.setFixedSize(24, 24)
        self.grid_settings_button.setStyleSheet(settings_btn_style)
        self.grid_settings_button.clicked.connect(self.show_grid_settings)

        # Row 1: Solar Disk + settings | Contours + settings
        self.show_solar_disk_checkbox = QCheckBox("Solar Disk")
        self.show_solar_disk_checkbox.setStyleSheet(overlay_toggle_style)
        self.show_solar_disk_checkbox.stateChanged.connect(self.on_checkbox_changed)

        self.solar_disk_center_button = QPushButton()
        self.solar_disk_center_button.setObjectName("IconOnlyNBGButton")
        self.solar_disk_center_button.setIcon(QIcon(themed_icon("settings.png")))
        self.solar_disk_center_button.setIconSize(QSize(18, 18))
        self.solar_disk_center_button.setToolTip("Customize Solar Disk")
        self.solar_disk_center_button.setFixedSize(24, 24)
        self.solar_disk_center_button.setStyleSheet(settings_btn_style)
        self.solar_disk_center_button.clicked.connect(self.set_solar_disk_center)

        self.show_contours_checkbox = QCheckBox("Contours")
        self.show_contours_checkbox.setChecked(False)
        self.show_contours_checkbox.setStyleSheet(overlay_toggle_style)
        self.show_contours_checkbox.stateChanged.connect(self.on_checkbox_changed)

        self.contour_settings_button = QPushButton()
        self.contour_settings_button.setObjectName("IconOnlyNBGButton")
        self.contour_settings_button.setIcon(QIcon(themed_icon("settings.png")))
        self.contour_settings_button.setIconSize(QSize(18, 18))
        self.contour_settings_button.setToolTip("Contour Settings")
        self.contour_settings_button.setFixedSize(24, 24)
        self.contour_settings_button.setStyleSheet(settings_btn_style)
        self.contour_settings_button.clicked.connect(self.show_contour_settings)

        # Create left/right widget containers for tight grouping
        left_group_0 = QWidget()
        left_layout_0 = QHBoxLayout(left_group_0)
        left_layout_0.setContentsMargins(0, 0, 0, 0)
        left_layout_0.setSpacing(2)
        left_layout_0.addWidget(self.show_beam_checkbox)
        left_layout_0.addWidget(self.beam_settings_button)
        left_layout_0.addStretch()

        right_group_0 = QWidget()
        right_layout_0 = QHBoxLayout(right_group_0)
        right_layout_0.setContentsMargins(0, 0, 0, 0)
        right_layout_0.setSpacing(2)
        right_layout_0.addWidget(self.show_grid_checkbox)
        right_layout_0.addWidget(self.grid_settings_button)
        right_layout_0.addStretch()

        left_group_1 = QWidget()
        left_layout_1 = QHBoxLayout(left_group_1)
        left_layout_1.setContentsMargins(0, 0, 0, 0)
        left_layout_1.setSpacing(2)
        left_layout_1.addWidget(self.show_solar_disk_checkbox)
        left_layout_1.addWidget(self.solar_disk_center_button)
        left_layout_1.addStretch()

        right_group_1 = QWidget()
        right_layout_1 = QHBoxLayout(right_group_1)
        right_layout_1.setContentsMargins(0, 0, 0, 0)
        right_layout_1.setSpacing(2)
        right_layout_1.addWidget(self.show_contours_checkbox)
        right_layout_1.addWidget(self.contour_settings_button)
        right_layout_1.addStretch()

        # Add to grid - 2 columns, stretch only in the middle
        grid_layout.addWidget(left_group_0, 0, 0)
        grid_layout.addWidget(right_group_0, 0, 1)
        grid_layout.addWidget(left_group_1, 1, 0)
        grid_layout.addWidget(right_group_1, 1, 1)

        # Equal column stretch for alignment
        grid_layout.setColumnStretch(0, 1)
        grid_layout.setColumnStretch(1, 1)

        overlays_layout.addLayout(grid_layout)
        main_layout.addWidget(overlays_group)

        # Intensity Range subgroup
        intensity_group = QGroupBox("Intensity Range")
        intensity_layout = QVBoxLayout(intensity_group)
        intensity_layout.setSpacing(6)  # Compact spacing

        # Min/Max range
        range_layout = QHBoxLayout()
        self.vmin_entry = QLineEdit("0.0")
        self.vmin_entry.editingFinished.connect(self.update_display)
        self.vmax_entry = QLineEdit("1.0")
        self.vmax_entry.editingFinished.connect(self.update_display)
        range_layout.addWidget(QLabel("Min:"))
        range_layout.addWidget(self.vmin_entry)
        range_layout.addWidget(QLabel("Max:"))
        range_layout.addWidget(self.vmax_entry)
        intensity_layout.addLayout(range_layout)

        # Gamma control
        gamma_layout = QHBoxLayout()
        self.gamma_slider = QSlider(Qt.Horizontal)
        self.gamma_slider.setRange(1, 60)
        self.gamma_slider.setValue(20)
        self.gamma_slider.valueChanged.connect(self.update_gamma_value)
        self.gamma_slider.sliderReleased.connect(self._apply_gamma_change)
        self.gamma_entry = QLineEdit("1.0")
        self.gamma_entry.setFixedWidth(60)
        self.gamma_entry.editingFinished.connect(self.update_gamma_slider)
        gamma_layout.addWidget(QLabel("Gamma:"))
        gamma_layout.addWidget(self.gamma_slider)
        gamma_layout.addWidget(self.gamma_entry)
        intensity_layout.addLayout(gamma_layout)

        # Preset buttons - compact secondary actions
        preset_layout = QHBoxLayout()
        preset_layout.setSpacing(6)

        # Common compact style for preset buttons
        preset_btn_style = """
            QPushButton {
                padding: 3px 8px;
                font-size: 10pt;
                min-width: 45px;
                min-height: 22px;
            }
        """

        self.auto_minmax_button = QPushButton("Auto")
        self.auto_minmax_button.setStyleSheet(preset_btn_style)
        self.auto_minmax_button.clicked.connect(self.auto_minmax)

        self.auto_percentile_button = QPushButton("1-99%")
        self.auto_percentile_button.setStyleSheet(preset_btn_style)
        self.auto_percentile_button.clicked.connect(self.auto_percentile)

        self.auto_median_button = QPushButton("Med±3σ")
        self.auto_median_button.setStyleSheet(preset_btn_style)
        self.auto_median_button.clicked.connect(self.auto_median_rms)

        preset_layout.addWidget(self.auto_minmax_button)
        preset_layout.addWidget(self.auto_percentile_button)
        preset_layout.addWidget(self.auto_median_button)
        intensity_layout.addLayout(preset_layout)

        main_layout.addWidget(intensity_group)

        # Update Display button
        self.plot_button = QPushButton("Update Display")
        self.plot_button.setStyleSheet(
            """
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, 
                    stop:0 #4f46e5, stop:1 #6366f1);
                color: white; 
                padding: 6px 16px; 
                font-weight: 600;
                font-size: 11pt;
                letter-spacing: 0.5px;
                border-radius: 6px;
                border: none;
                min-height: 26px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, 
                    stop:0 #6366f1, stop:1 #818cf8);
            }
            QPushButton:pressed {
                background: #3730a3;
            }
            """
        )
        self.plot_button.clicked.connect(self.on_visualization_changed)
        main_layout.addWidget(self.plot_button)

        parent_layout.addWidget(group)

    def create_range_controls(self, parent_layout):
        group = QGroupBox("Intensity Range")
        layout = QVBoxLayout(group)
        range_layout = QHBoxLayout()
        self.vmin_entry = QLineEdit("0.0")
        self.vmin_entry.editingFinished.connect(self.update_display)
        self.vmax_entry = QLineEdit("1.0")
        self.vmax_entry.editingFinished.connect(self.update_display)
        range_layout.addWidget(QLabel("Min:"))
        range_layout.addWidget(self.vmin_entry)
        range_layout.addWidget(QLabel("Max:"))
        range_layout.addWidget(self.vmax_entry)
        gamma_layout = QHBoxLayout()
        self.gamma_slider = QSlider(Qt.Horizontal)
        self.gamma_slider.setRange(1, 60)
        self.gamma_slider.setValue(20)
        self.gamma_slider.valueChanged.connect(self.update_gamma_value)
        self.gamma_slider.sliderReleased.connect(self._apply_gamma_change)
        self.gamma_entry = QLineEdit("1.0")
        self.gamma_entry.setFixedWidth(60)
        self.gamma_entry.editingFinished.connect(self.update_gamma_slider)
        gamma_layout.addWidget(QLabel("Gamma:"))
        gamma_layout.addWidget(self.gamma_slider)
        gamma_layout.addWidget(self.gamma_entry)
        preset_layout = QHBoxLayout()
        self.auto_minmax_button = QPushButton("Auto")
        self.auto_minmax_button.clicked.connect(self.auto_minmax)
        self.auto_percentile_button = QPushButton("1-99%")
        self.auto_percentile_button.clicked.connect(self.auto_percentile)
        self.auto_median_button = QPushButton("Med±3σ")
        self.auto_median_button.clicked.connect(self.auto_median_rms)
        preset_layout.addWidget(self.auto_minmax_button)
        preset_layout.addWidget(self.auto_percentile_button)
        preset_layout.addWidget(self.auto_median_button)
        layout.addLayout(range_layout)
        layout.addLayout(gamma_layout)
        layout.addLayout(preset_layout)
        parent_layout.addWidget(group)

    def create_nav_controls(self, parent_layout):
        group = QGroupBox("Navigation")
        layout = QVBoxLayout(group)
        zoom_layout = QHBoxLayout()
        self.zoom_in_button = QPushButton()
        self.zoom_in_button.setObjectName("IconOnlyButton")
        self.zoom_in_button.setIcon(QIcon(get_resource_path("assets/zoom_in.png")))
        self.zoom_in_button.setIconSize(QSize(24, 24))
        self.zoom_in_button.setToolTip("Zoom In")
        # self.zoom_in_button.setFixedSize(32, 32)
        self.zoom_in_button.setFixedHeight(28)

        self.zoom_in_button.clicked.connect(self.zoom_in)
        self.zoom_out_button = QPushButton()
        self.zoom_out_button.setObjectName("IconOnlyButton")
        self.zoom_out_button.setIcon(QIcon(get_resource_path("assets/zoom_out.png")))
        self.zoom_out_button.setIconSize(QSize(24, 24))
        self.zoom_out_button.setToolTip("Zoom Out")
        # self.zoom_out_button.setFixedSize(32, 32)
        self.zoom_out_button.setFixedHeight(28)
        self.zoom_out_button.clicked.connect(self.zoom_out)
        self.reset_view_button = QPushButton()
        self.reset_view_button.setObjectName("IconOnlyButton")
        self.reset_view_button.setIcon(QIcon(get_resource_path("assets/reset.png")))
        self.reset_view_button.setIconSize(QSize(24, 24))
        self.reset_view_button.setToolTip("Reset View")
        # self.reset_view_button.setFixedSize(32, 32)
        self.reset_view_button.setFixedHeight(28)
        self.reset_view_button.clicked.connect(
            lambda: self.reset_view(show_status_message=True)
        )
        self.reset_view_button.setToolTip("Reset View")
        self.zoom_60arcmin_button = QPushButton("1°×1°")
        self.zoom_60arcmin_button.clicked.connect(self.zoom_60arcmin)
        self.zoom_60arcmin_button.setToolTip("1°×1° Zoom")
        self.zoom_60arcmin_button.setIconSize(QSize(24, 24))
        self.zoom_60arcmin_button.setFixedHeight(28)
        zoom_layout.addWidget(self.zoom_in_button)
        zoom_layout.addWidget(self.zoom_out_button)
        zoom_layout.addWidget(self.reset_view_button)
        zoom_layout.addWidget(self.zoom_60arcmin_button)
        layout.addLayout(zoom_layout)
        self.plot_button = QPushButton("Update Display")
        self.plot_button.setStyleSheet(
            """
            QPushButton {
                background-color: #3871DE; 
                color: white; 
                padding: 5px; 
                font-weight: bold;
                border-radius: 3px;
                min-height: 25px;
            }
            QPushButton:hover {
                background-color: #4A82EF;
            }
            """
        )
        self.plot_button.clicked.connect(self.on_visualization_changed)
        layout.addWidget(self.plot_button)
        parent_layout.addWidget(group)

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
        self.rect_action = QAction(
            QIcon(get_resource_path("assets/rectangle_selection.png")),
            "",
            self,
        )
        self.rect_action.setToolTip("Rectangle Select")
        self.rect_action.setCheckable(True)
        self.rect_action.setChecked(True)
        self.rect_action.triggered.connect(
            lambda: self.set_region_mode(RegionMode.RECTANGLE)
        )
        action_group.addAction(self.rect_action)

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
        self.zoom_in_action.setToolTip("Zoom In")
        self.zoom_in_action.triggered.connect(self.zoom_in)
        self.zoom_out_action = QAction(
            QIcon(get_resource_path("assets/zoom_out.png")),
            "",
            self,
        )
        self.zoom_out_action.setToolTip("Zoom Out")
        self.zoom_out_action.triggered.connect(self.zoom_out)
        self.reset_view_action = QAction(
            QIcon(get_resource_path("assets/reset.png")),
            "",
            self,
        )
        self.reset_view_action.setToolTip("Reset View")
        self.reset_view_action.triggered.connect(
            lambda: self.reset_view(show_status_message=True)
        )
        self.zoom_60arcmin_action = QAction(
            QIcon(get_resource_path("assets/zoom_60arcmin.png")),
            "",
            self,
        )
        self.zoom_60arcmin_action.setToolTip("1°×1° Zoom")
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
        from .styles import get_icon_path

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
        from .styles import theme_manager

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

        # Add theme-aware separator before file navigation buttons
        # self._hpc_nav_separator = QWidget()
        # self._hpc_nav_separator.setFixedWidth(1)
        # self._hpc_nav_separator.setFixedHeight(20)
        # self._update_hpc_nav_separator_style()
        # toolbar.addWidget(self._hpc_nav_separator)

        # File navigation buttons - create first, style later so we can update on theme change
        # self._first_file_btn = QPushButton("⏮")
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

        # self._last_file_btn = QPushButton("⏭")
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
        from .styles import theme_manager

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
                lambda t: self._update_profile_zoom_separator_style()
            )
            self._separator_theme_callback_registered = True

    def _update_hpc_nav_separator_style(self):
        """Update the HPC/navigation separator style based on current theme"""
        from .styles import theme_manager

        if not hasattr(self, "_hpc_nav_separator"):
            return

        # Get theme-appropriate separator color
        if theme_manager.is_dark:
            sep_color = "#546E7A"  # Blue-grey for dark theme
        else:
            sep_color = "#9E9E9E"  # Medium grey for light theme

        self._hpc_nav_separator.setStyleSheet(
            f"""
            QWidget {{
                background-color: {sep_color};
            }}
        """
        )

        # Register theme callback if not already done
        if not hasattr(self, "_hpc_nav_separator_theme_callback_registered"):
            theme_manager.register_callback(
                lambda t: self._update_hpc_nav_separator_style()
            )
            self._hpc_nav_separator_theme_callback_registered = True

    def _update_nav_button_styles(self):
        """Update navigation button styles based on current theme"""
        from .styles import theme_manager

        if not hasattr(self, "_prev_file_btn"):
            return

        palette = theme_manager.palette

        # Get theme-appropriate colors
        if theme_manager.is_dark:
            btn_bg = palette.get("surface", "#455A64")
            btn_hover = palette.get("highlight", "#546E7A")
            btn_pressed = palette.get("base", "#37474F")
            btn_disabled_bg = "#4a4a6a"
            btn_disabled_text = "#757575"
            btn_text = palette.get("text", "white")
            label_color = palette.get("text_secondary", "#B0BEC5")
        else:
            btn_bg = palette.get("surface", "#D7D3BF")
            btn_hover = palette.get("highlight", "#B8B5A2")
            btn_pressed = palette.get("base", "#ECEBDE")
            btn_disabled_bg = "#E0E0E0"
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
            theme_manager.register_callback(lambda t: self._update_nav_button_styles())
            self._nav_theme_callback_registered = True

    def _update_special_button_styles(self):
        """Update styles for TB, HPC, NOAA, and Helioviewer buttons based on current theme.

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
        if hasattr(self, "noaa_events_btn"):
            self.noaa_events_btn.setStyleSheet(
                f"""
                QPushButton {{
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, 
                        stop:0 #7c3aed, stop:1 #a78bfa);
                    color: white;
                    border: none;
                    padding: 5px 10px;
                    border-radius: 6px;
                    font-weight: 600;
                    font-size: 11pt;
                    min-height: 24px;
                }}
                QPushButton:hover {{
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, 
                        stop:0 #a78bfa, stop:1 #c4b5fd);
                }}
                QPushButton:pressed {{
                    background: #5b21b6;
                }}
                QPushButton:disabled {{
                    background: {disabled_bg};
                    color: {disabled_text};
                }}
            """
            )

        # Helioviewer Button (cyan gradient)
        if hasattr(self, "helioviewer_btn"):
            self.helioviewer_btn.setStyleSheet(
                f"""
                QPushButton {{
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, 
                        stop:0 #0ea5e9, stop:1 #38bdf8);
                    color: white;
                    border: none;
                    padding: 5px 10px;
                    border-radius: 6px;
                    font-weight: 600;
                    font-size: 11pt;
                    min-height: 24px;
                }}
                QPushButton:hover {{
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, 
                        stop:0 #38bdf8, stop:1 #7dd3fc);
                }}
                QPushButton:pressed {{
                    background: #0369a1;
                }}
                QPushButton:disabled {{
                    background: {disabled_bg};
                    color: {disabled_text};
                }}
            """
            )

        # Register theme callback if not already done
        if not hasattr(self, "_special_btn_theme_callback_registered"):
            theme_manager.register_callback(
                lambda t: self._update_special_button_styles()
            )
            self._special_btn_theme_callback_registered = True

    def show_noaa_events_for_current_image(self):
        """Show NOAA Solar Events for the current image date and auto-fetch events."""
        try:
            from .noaa_events.noaa_events_gui import NOAAEventsViewer
            from datetime import date, datetime
            import os

            if not hasattr(self, "imagename") or not self.imagename:
                from PyQt5.QtWidgets import QMessageBox

                QMessageBox.information(self, "Info", "No image is currently loaded.")
                return

            # Check if splash image
            if self.imagename.endswith("splash.fits"):
                from PyQt5.QtWidgets import QMessageBox

                QMessageBox.information(self, "Info", "Please load an image first.")
                return

            imagename = self.imagename
            extracted_date = None

            # Method 1: FITS header
            lower_name = imagename.lower()
            if (
                lower_name.endswith(".fits")
                or lower_name.endswith(".fts")
                or lower_name.endswith(".fit")
            ):
                try:
                    from astropy.io import fits

                    header = fits.getheader(imagename)

                    # Check DATE-OBS (standard), DATE_OBS (IRIS), and STARTOBS
                    image_time = (
                        header.get("DATE-OBS")
                        or header.get("DATE_OBS")
                        or header.get("STARTOBS")
                    )

                    # Special handling for SOHO (DATE-OBS + TIME-OBS)
                    if (
                        header.get("TELESCOP") == "SOHO"
                        and header.get("TIME-OBS")
                        and image_time
                    ):
                        image_time = f"{image_time}T{header['TIME-OBS']}"

                    if image_time:
                        image_time = str(image_time)
                        if "T" in image_time:
                            clean_str = (
                                image_time.replace("Z", "").split("+")[0].split(".")[0]
                            )
                            if "-" in clean_str[11:]:
                                clean_str = clean_str[:19]
                            try:
                                extracted_date = datetime.fromisoformat(
                                    clean_str
                                ).date()
                            except ValueError:
                                date_part = clean_str.split("T")[0]
                                if len(date_part) >= 10:
                                    extracted_date = datetime.strptime(
                                        date_part[:10], "%Y-%m-%d"
                                    ).date()
                        elif "-" in image_time and len(image_time) >= 10:
                            extracted_date = datetime.strptime(
                                image_time[:10], "%Y-%m-%d"
                            ).date()
                except Exception as fits_err:
                    print(f"[ERROR] FITS header read failed: {fits_err}")
                    self.show_status_message(f"FITS header read failed: {fits_err}")

            # Method 2: CASA image
            if extracted_date is None:
                is_casa_image = os.path.isdir(imagename) or (
                    not lower_name.endswith(".fits")
                    and not lower_name.endswith(".fts")
                    and not lower_name.endswith(".fit")
                )

                if is_casa_image:
                    try:
                        from casatools import image as IA
                        from astropy.time import Time

                        ia_tool = IA()
                        ia_tool.open(imagename)
                        csys_record = ia_tool.coordsys().torecord()
                        ia_tool.close()

                        if "obsdate" in csys_record:
                            obsdate = csys_record["obsdate"]
                            m0 = obsdate.get("m0", {})
                            time_value = m0.get("value", None)
                            time_unit = m0.get("unit", None)
                            refer = obsdate.get("refer", None)

                            if (refer == "UTC" or time_unit == "d") and time_value:
                                t = Time(time_value, format="mjd")
                                extracted_date = t.to_datetime().date()
                    except Exception as casa_err:
                        print(f"[ERROR] CASA date extraction failed: {casa_err}")
                        self.show_status_message(
                            f"CASA date extraction failed: {casa_err}"
                        )

            # Method 3: Filename parsing
            if extracted_date is None:
                import re

                patterns = [
                    r"(\d{4})(\d{2})(\d{2})",  # YYYYMMDD
                    r"(\d{4})-(\d{2})-(\d{2})",  # YYYY-MM-DD
                    r"(\d{4})\.(\d{2})\.(\d{2})",  # YYYY.MM.DD
                ]
                for pattern in patterns:
                    match = re.search(pattern, imagename)
                    if match:
                        try:
                            y, m, d = (
                                int(match.group(1)),
                                int(match.group(2)),
                                int(match.group(3)),
                            )
                            if 1990 < y < 2100 and 1 <= m <= 12 and 1 <= d <= 31:
                                extracted_date = date(y, m, d)
                                break
                        except (ValueError, IndexError):
                            continue

            if extracted_date is None:
                from PyQt5.QtWidgets import QMessageBox

                QMessageBox.warning(
                    self,
                    "Date Not Found",
                    "Could not extract date from the current image.\n\n"
                    "Supported formats:\n"
                    "• FITS files with DATE-OBS header\n"
                    "• CASA images with observation date\n"
                    "• Files with date in filename (YYYYMMDD)",
                )
                return

            # Get main window to store reference
            main_window = self.parent()
            if main_window and hasattr(main_window, "parent"):
                main_window = main_window.parent()

            # Create and show the viewer with the date
            viewer = NOAAEventsViewer(self, extracted_date)
            if main_window:
                main_window._noaa_events_viewer = viewer
            viewer.show()

            # Auto-fetch events for the extracted date
            viewer.fetch_data()

        except Exception as e:
            from PyQt5.QtWidgets import QMessageBox

            error_message = f"Error showing NOAA Events: {str(e)}"
            print(f"[ERROR] {error_message}")
            self.show_status_message(error_message)
            import traceback

            traceback.print_exc()
            QMessageBox.critical(self, "Error", error_message)

    def launch_helioprojective_viewer(self):
        """Toggle between HPC and RA/DEC views in main canvas"""
        if not hasattr(self, "imagename") or not self.imagename:
            self.show_status_message("No image loaded")
            return

        # Check if splash image
        if self.imagename.endswith("splash.fits"):
            self.show_status_message("Please load an image first")
            return

        # Check if we're currently in HPC mode - if so, revert to original
        if hasattr(self, "_original_imagename") and self._original_imagename:
            self._revert_to_radec_view()
            return

        # Check if we're viewing RA/Dec temp file from HPC conversion - revert to HPC
        if hasattr(self, "_hpc_original_imagename") and self._hpc_original_imagename:
            self._revert_to_hpc_from_radec()
            return

        try:
            from PyQt5.QtWidgets import QApplication, QMessageBox
            from PyQt5.QtCore import Qt
            import tempfile
            import os

            # Check if image is already in helioprojective coordinates (Solar-X/Y)
            # If so, convert TO RA/Dec instead of showing error
            if self._is_already_hpc():
                self._revert_to_radec_view()
                return

            QApplication.setOverrideCursor(Qt.WaitCursor)
            self.show_status_message("Converting to helioprojective coordinates...")
            QApplication.processEvents()

            # Import conversion function
            from .helioprojective import convert_and_save_hpc_all_stokes

            # Store original imagename before switching
            self._original_imagename = self.imagename

            # Get current parameters
            try:
                threshold = float(self.threshold_entry.text())
            except (ValueError, AttributeError):
                threshold = 10.0

            # Create temp file for HPC FITS
            temp_dir = tempfile.gettempdir()
            temp_hpc_file = os.path.join(
                temp_dir, f"solarviewer_hpc_temp_{self._temp_file_id}.fits"
            )

            # Convert all Stokes and save to temp file
            success = convert_and_save_hpc_all_stokes(
                input_fits_file=self.imagename,
                output_fits_file=temp_hpc_file,
                thres=threshold,
                overwrite=True,
            )

            if not success or not os.path.exists(temp_hpc_file):
                QApplication.restoreOverrideCursor()
                self._original_imagename = None  # Reset since conversion failed
                QMessageBox.warning(
                    self,
                    "Conversion Failed",
                    "Failed to convert to helioprojective coordinates.\n\n"
                    "Please ensure the image has valid RA/DEC coordinate information.",
                )
                return

            # Store temp file path for cleanup
            self._hpc_temp_file = temp_hpc_file

            # Clear old contour data (stale WCS would cause reprojection failures)
            self.contour_settings["contour_data"] = None
            self.current_contour_wcs = None

            # Load the HPC FITS file in the main canvas
            self.imagename = temp_hpc_file
            # Clear figure to prevent restoring old view limits
            self.figure.clear()
            self.on_visualization_changed(dir_load=True)
            self.update_tab_name_from_path(temp_hpc_file)

            # Refresh contours if enabled
            if (
                hasattr(self, "show_contours_checkbox")
                and self.show_contours_checkbox.isChecked()
            ):
                self.load_contour_data()
                self.schedule_plot()  # Redraw to show contours

            # Update button to show RA/DEC option
            if hasattr(self, "hpc_btn"):
                self.hpc_btn.setText("RA/DEC")
                self.hpc_btn.setToolTip("Revert to RA/DEC view")

            QApplication.restoreOverrideCursor()
            self.show_status_message(
                "Helioprojective view - click RA/DEC button to revert"
            )

        except Exception as e:
            import traceback

            traceback.print_exc()
            QApplication.restoreOverrideCursor()
            self._original_imagename = None  # Reset on error
            QMessageBox.critical(
                self,
                "Error",
                f"Error converting to helioprojective coordinates:\n\n{str(e)}",
            )

    def _revert_to_radec_view(self):
        """Revert from HPC view back to RA/DEC view"""
        import os
        import tempfile

        try:
            from PyQt5.QtWidgets import QApplication, QMessageBox
            from PyQt5.QtCore import Qt

            QApplication.setOverrideCursor(Qt.WaitCursor)
            self.show_status_message("Converting to RA/DEC view...")

            # Check if we have an original file to revert to
            has_original = (
                hasattr(self, "_original_imagename") and self._original_imagename
            )

            if has_original:
                # Revert to original file
                self.imagename = self._original_imagename
                self._original_imagename = None

                # Clean up temp HPC file
                if hasattr(self, "_hpc_temp_file") and self._hpc_temp_file:
                    if os.path.exists(self._hpc_temp_file):
                        try:
                            os.remove(self._hpc_temp_file)
                        except Exception:
                            pass
                    self._hpc_temp_file = None
            else:
                # No original - convert current HPC file to RA/Dec using the new function
                from .helioprojective import convert_hpc_to_radec

                # Create temp output file
                temp_dir = tempfile.gettempdir()
                self._radec_temp_file = os.path.join(
                    temp_dir, f"solarviewer_radec_temp_{self._temp_file_id}.fits"
                )

                # Store current as "original" for potential revert
                self._hpc_original_imagename = self.imagename

                success = convert_hpc_to_radec(
                    self.imagename, self._radec_temp_file, overwrite=True
                )

                if not success:
                    QApplication.restoreOverrideCursor()
                    QMessageBox.warning(
                        self, "Error", "Failed to convert HPC to RA/Dec"
                    )
                    return

                self.imagename = self._radec_temp_file

            # Clear old contour data (stale WCS would cause reprojection failures)
            self.contour_settings["contour_data"] = None
            self.current_contour_wcs = None

            # Reload image
            # Clear figure to prevent restoring old view limits
            self.figure.clear()
            self.on_visualization_changed(dir_load=True)
            self.update_tab_name_from_path(self.imagename)

            # Refresh contours if enabled
            if (
                hasattr(self, "show_contours_checkbox")
                and self.show_contours_checkbox.isChecked()
            ):
                self.load_contour_data()
                self.schedule_plot()  # Redraw to show contours

            # Update button back to Helioprojective
            if hasattr(self, "hpc_btn"):
                self.hpc_btn.setText("HPC")
                self.hpc_btn.setToolTip("Convert to Helioprojective view")

            QApplication.restoreOverrideCursor()
            self.show_status_message("RA/DEC view - Click HPC button to revert")

        except Exception as e:
            import traceback

            traceback.print_exc()
            QApplication.restoreOverrideCursor()
            self.show_status_message(f"Error reverting: {str(e)}")

    def _revert_to_hpc_from_radec(self):
        """Revert from RA/Dec temp file back to original HPC file"""
        import os
        from PyQt5.QtWidgets import QMessageBox

        if not self._hpc_original_imagename:
            return

        # Check if original HPC file still exists
        if not os.path.exists(self._hpc_original_imagename):
            QMessageBox.warning(
                self,
                "Error",
                f"Original HPC file no longer exists:\n{self._hpc_original_imagename}",
            )
            self._hpc_original_imagename = None
            self._radec_temp_file = None
            return

        try:
            from PyQt5.QtWidgets import QApplication
            from PyQt5.QtCore import Qt

            QApplication.setOverrideCursor(Qt.WaitCursor)
            self.show_status_message("Reverting to HPC view...")

            # Restore HPC file
            self.imagename = self._hpc_original_imagename
            self._hpc_original_imagename = None

            # Clean up temp RA/Dec file
            if hasattr(self, "_radec_temp_file") and self._radec_temp_file:
                if os.path.exists(self._radec_temp_file):
                    try:
                        os.remove(self._radec_temp_file)
                        # print(
                        #    f"[HPC] Removed temp RA/Dec file: {self._radec_temp_file}"
                        # )
                    except Exception:
                        pass
                self._radec_temp_file = None

            # Clear old contour data
            self.contour_settings["contour_data"] = None
            self.current_contour_wcs = None

            # Reload HPC image
            # Clear figure to prevent restoring old view limits
            self.figure.clear()
            self.on_visualization_changed(dir_load=True)
            self.update_tab_name_from_path(self.imagename)

            # Update button
            if hasattr(self, "hpc_btn"):
                self.hpc_btn.setText("RA/DEC")
                self.hpc_btn.setToolTip("Convert to RA/DEC view")

            QApplication.restoreOverrideCursor()
            self.show_status_message("Showing HPC view")

        except Exception as e:
            import traceback

            traceback.print_exc()
            QApplication.restoreOverrideCursor()
            self.show_status_message(f"Error reverting: {str(e)}")

    def _reset_hpc_state(self):
        """Reset HPC and TB state when loading a new image"""
        import os

        # Clear the original imagename reference for HPC
        self._original_imagename = None

        # Clean up temp HPC file if exists
        if hasattr(self, "_hpc_temp_file") and self._hpc_temp_file:
            if os.path.exists(self._hpc_temp_file):
                try:
                    os.remove(self._hpc_temp_file)
                except Exception:
                    pass
            self._hpc_temp_file = None

        # Clean up temp RA/Dec file if exists (from HPC->RA/Dec conversion)
        if hasattr(self, "_radec_temp_file") and self._radec_temp_file:
            if os.path.exists(self._radec_temp_file):
                try:
                    os.remove(self._radec_temp_file)
                except Exception:
                    pass
            self._radec_temp_file = None

        # Clear HPC original reference (for HPC->RA/Dec revert)
        self._hpc_original_imagename = None

        # Reset HPC button
        if hasattr(self, "hpc_btn"):
            self.hpc_btn.setText("HPC")
            self.hpc_btn.setToolTip("Convert to Helioprojective view")

        # Clear TB state as well
        self._tb_original_imagename = None
        self._tb_original_unit = None
        self._tb_mode = False

        # Clean up temp TB file if exists
        if hasattr(self, "_tb_temp_file") and self._tb_temp_file:
            if os.path.exists(self._tb_temp_file):
                try:
                    os.remove(self._tb_temp_file)
                    # print(
                    #    f"[TB] Cleaned up temp file on new image load: {self._tb_temp_file}"
                    # )
                except Exception:
                    pass
            self._tb_temp_file = None

        # Reset TB button
        if hasattr(self, "tb_btn"):
            self.tb_btn.setText("TB")
            self.tb_btn.setToolTip("Convert to Brightness Temperature (K)")
            self.tb_btn.setChecked(False)

    def _is_already_hpc(self):
        """Check if the current image is already in helioprojective coordinates (Solar-X/Y)"""
        try:
            # Check via CASA image tool
            ia_tool = IA()
            ia_tool.open(self.imagename)
            csys = ia_tool.coordsys()
            dimension_names = [n.upper() for n in csys.names()]
            ia_tool.close()

            # Check for Solar-X/Solar-Y in coordinate names
            if "SOLAR-X" in dimension_names or "HPLN-TAN" in dimension_names:
                return True

            # Also check FITS header if it's a FITS file
            if self.imagename.endswith(".fits") or self.imagename.endswith(".fts"):
                from astropy.io import fits

                header = fits.getheader(self.imagename)
                ctype1 = header.get("CTYPE1", "").upper()
                ctype2 = header.get("CTYPE2", "").upper()
                if (
                    "HPLN" in ctype1
                    or "HPLT" in ctype2
                    or "SOLAR" in ctype1
                    or "SOLAR" in ctype2
                ):
                    return True

            return False
        except Exception:
            return False

    def _get_image_dimensions(self, imagepath):
        """
        Get dimensions (height, width) of an image file (FITS or CASA).
        
        Parameters
        ----------
        imagepath : str
            Path to the image file or directory
            
        Returns
        -------
        tuple
            (height, width) in pixels, or (0, 0) on failure
        """
        height, width = 0, 0
        if not imagepath or not os.path.exists(imagepath):
            return height, width
            
        try:
            if os.path.isdir(imagepath):  # CASA image
                ia_tool = IA()
                ia_tool.open(imagepath)
                shape = ia_tool.shape()
                ia_tool.close()
                # CASA shape is usually [width, height, stokes, freq]
                if len(shape) >= 2:
                    width, height = shape[0], shape[1]
            else:  # FITS file
                from astropy.io import fits
                # Use headers to be fast, avoid loading full data
                header = fits.getheader(imagepath)
                # NAXIS1 is width (x), NAXIS2 is height (y)
                width = header.get("NAXIS1", 0)
                height = header.get("NAXIS2", 0)
        except Exception as e:
            print(f"[ERROR] Could not get image dimensions for {imagepath}: {e}")
            
        return height, width

    def _get_coordinate_system_from_image(self, imagepath):
        """
        Detect coordinate system from a FITS or CASA image.

        Parameters
        ----------
        imagepath : str
            Path to the FITS file or CASA image

        Returns
        -------
        str
            'hpc' for helioprojective, 'radec' for RA/Dec, 'unknown' otherwise
        """
        if not imagepath or not os.path.exists(imagepath):
            return "unknown"

        try:
            # For FITS files, check the header first (more reliable)
            if imagepath.endswith(".fits") or imagepath.endswith(".fts"):
                from astropy.io import fits

                header = fits.getheader(imagepath)
                ctype1 = header.get("CTYPE1", "").upper()
                ctype2 = header.get("CTYPE2", "").upper()

                # Check for HPC (Helioprojective)
                if (
                    "HPLN" in ctype1
                    or "HPLT" in ctype2
                    or "SOLAR" in ctype1
                    or "SOLAR" in ctype2
                ):
                    return "hpc"
                # Check for RA/Dec
                if "RA" in ctype1 or "DEC" in ctype2:
                    return "radec"
        except Exception as e:
            print(f"[ERROR] FITS header check failed: {e}")

        try:
            # For CASA images, use coordinate system info
            ia_tool = IA()
            ia_tool.open(imagepath)
            csys = ia_tool.coordsys()

            # Get direction reference code (e.g., 'J2000' for RA/Dec, 'SUN' for HPC)
            ref_code = csys.referencecode("direction")
            dimension_names = [n.upper() for n in csys.names()]

            ia_tool.close()

            # Check for Solar/Helioprojective (SUN reference frame)
            if ref_code and "SUN" in str(ref_code).upper():
                return "hpc"
            if "SOLAR-X" in dimension_names or "SOLAR-Y" in dimension_names:
                return "hpc"
            # Check for RA/Dec (J2000 reference frame)
            if ref_code and "J2000" in str(ref_code).upper():
                return "radec"
            if "RIGHT ASCENSION" in dimension_names or "DECLINATION" in dimension_names:
                return "radec"
        except Exception as e:
            print(f"[ERROR] CASA check failed: {e}")

        return "unknown"

    def create_stats_table(self, parent_layout):

        group = QGroupBox("Region Statistics")
        layout = QVBoxLayout(group)
        self.info_label = QLabel("No selection")
        self.info_label.setStyleSheet("font-style: italic; font-size: 10.5pt;")
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)
        self.stats_table = QTableWidget(6, 2)
        self.stats_table.setHorizontalHeaderLabels(["Metric", "Value"])
        self.stats_table.verticalHeader().setVisible(False)
        self.stats_table.setEditTriggers(QTableWidget.NoEditTriggers)
        # Make columns resizable - Metric fits content, Value stretches
        self.stats_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self.stats_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        # Allow user to resize columns manually
        self.stats_table.horizontalHeader().setSectionsMovable(False)
        for i in range(6):
            self.stats_table.setRowHeight(i, 24)
        headers = ["Min", "Max", "Mean", "Std Dev", "Sum", "RMS"]
        for row, label in enumerate(headers):
            self.stats_table.setItem(row, 0, QTableWidgetItem(label))
            self.stats_table.setItem(row, 1, QTableWidgetItem("−"))
            self.stats_table.item(row, 1).setTextAlignment(
                Qt.AlignRight | Qt.AlignVCenter
            )
        layout.addWidget(self.stats_table)

        # Histogram button - compact styling
        self.histogram_btn = QPushButton("📊 Histogram")
        self.histogram_btn.setToolTip("Show pixel value histogram for current ROI")
        self.histogram_btn.setStyleSheet(
            """
            QPushButton {
                padding: 4px 10px;
                font-size: 11pt;
                min-height: 24px;
            }
        """
        )
        self.histogram_btn.clicked.connect(self._show_roi_histogram)
        layout.addWidget(self.histogram_btn)

        parent_layout.addWidget(group)

    def create_image_stats_table(self, parent_layout):
        group = QGroupBox("Image Statistics")
        layout = QVBoxLayout(group)

        self.image_info_label = QLabel("Full image statistics")
        self.image_info_label.setStyleSheet("font-style: italic; font-size: 10.5pt;")
        self.image_info_label.setWordWrap(True)
        layout.addWidget(self.image_info_label)

        self.image_stats_table = QTableWidget(6, 2)
        self.image_stats_table.setHorizontalHeaderLabels(["Metric", "Value"])
        self.image_stats_table.verticalHeader().setVisible(False)
        self.image_stats_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.image_stats_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self.image_stats_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )
        for i in range(6):
            self.image_stats_table.setRowHeight(i, 24)

        headers = ["Max", "Min", "RMS", "Mean (RMS box)", "Pos. DR", "Neg. DR"]
        for row, label in enumerate(headers):
            self.image_stats_table.setItem(row, 0, QTableWidgetItem(label))
            self.image_stats_table.setItem(row, 1, QTableWidgetItem("−"))
            self.image_stats_table.item(row, 1).setTextAlignment(
                Qt.AlignRight | Qt.AlignVCenter
            )

        layout.addWidget(self.image_stats_table)
        parent_layout.addWidget(group)

    def create_noaa_events_button(self, parent_layout):
        """Create the NOAA Solar Events button in the right panel."""
        # Create horizontal layout for both buttons side-by-side
        buttons_layout = QHBoxLayout()

        # Solar Activity button (smaller, no icon)
        # self.noaa_events_btn = QPushButton("☀️")
        self.noaa_events_btn = QPushButton("☀️ Activity")
        self.noaa_events_btn.setToolTip(
            "View Solar Activity (Events, Active Regions, Conditions, CMEs)"
        )
        self.noaa_events_btn.setEnabled(False)  # Disabled by default
        self.noaa_events_btn.setMinimumWidth(80)
        # Style will be applied by _update_special_button_styles()
        self.noaa_events_btn.clicked.connect(self.show_noaa_events_for_current_image)
        buttons_layout.addWidget(self.noaa_events_btn)

        # self.helioviewer_btn = QPushButton("🌐")
        self.helioviewer_btn = QPushButton("🌐 Helioviewer")
        self.helioviewer_btn.setToolTip(
            "Browse Helioviewer images around current observation time"
        )
        self.helioviewer_btn.setEnabled(False)  # Disabled by default
        self.helioviewer_btn.setMinimumWidth(80)
        # Style will be applied by _update_special_button_styles()
        self.helioviewer_btn.clicked.connect(self.open_helioviewer_with_time)
        buttons_layout.addWidget(self.helioviewer_btn)

        # Add horizontal layout to parent
        parent_layout.addLayout(buttons_layout)

        # Apply theme-aware styles to special buttons (calls this after all 4 buttons exist)
        self._update_special_button_styles()

    def create_coord_display(self, parent_layout):
        group = QGroupBox("Cursor Position")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 8, 8, 8)
        self.coord_label = QLabel("RA: −\nDEC: −")
        self.coord_label.setAlignment(Qt.AlignCenter)
        self.coord_label.setStyleSheet("font-family: monospace; font-size: 10.5pt;")
        self.coord_label.setMinimumHeight(55)
        layout.addWidget(self.coord_label)
        parent_layout.addWidget(group)

    def open_helioviewer_with_time(self):
        """Open Helioviewer Browser with time range from current file (FITS or CASA)."""
        try:
            from .helioviewer_browser import HelioviewerBrowser
            from datetime import datetime, timedelta
            from PyQt5.QtCore import QDateTime
            from astropy.time import Time

            obs_datetime = None

            # Try FITS header (using same logic as update_figure_title)
            if hasattr(self, "current_header") and self.current_header:
                # Try multiple header keywords in order of preference
                for key in [
                    "DATE-OBS",
                    "DATE_OBS",
                    "DATE",
                    "TRECVD",
                    "T_OBS",
                    "SIMPLE_TIME",
                ]:
                    if key in self.current_header:
                        date_str = str(self.current_header[key]).strip()
                        try:
                            # ISO format with T separator
                            if "T" in date_str:
                                # Handle 'Z' timezone and fractional seconds
                                date_str_clean = date_str.replace("Z", "+00:00")
                                # Split off timezone if present
                                if "+" in date_str_clean:
                                    date_str_clean = date_str_clean.split("+")[0]
                                obs_datetime = datetime.fromisoformat(date_str_clean)
                            # Date with dashes
                            elif "-" in date_str and len(date_str) >= 10:
                                # Try various formats
                                for fmt in [
                                    "%Y-%m-%d %H:%M:%S.%f",
                                    "%Y-%m-%d %H:%M:%S",
                                    "%Y-%m-%d",
                                ]:
                                    try:
                                        obs_datetime = datetime.strptime(
                                            date_str[: len(fmt)], fmt
                                        )
                                        break
                                    except ValueError:
                                        continue
                            # Date with slashes
                            elif "/" in date_str:
                                for fmt in [
                                    "%Y/%m/%d %H:%M:%S.%f",
                                    "%Y/%m/%d %H:%M:%S",
                                    "%Y/%m/%d",
                                ]:
                                    try:
                                        obs_datetime = datetime.strptime(
                                            date_str[: len(fmt)], fmt
                                        )
                                        break
                                    except ValueError:
                                        continue

                            if obs_datetime:
                                break
                        except (ValueError, TypeError) as e:
                            print(f"[WARNING] Failed to parse {key}={date_str}: {e}")
                            self.show_status_message(
                                f"Failed to parse {key}={date_str}: {e}"
                            )
                            continue

            # Fallback: Try the simpler parsing method used by solar activity button
            # This handles some edge cases with fractional seconds that fail in some Python versions
            if not obs_datetime and hasattr(self, "imagename") and self.imagename:
                lower_name = self.imagename.lower()
                if (
                    lower_name.endswith(".fits")
                    or lower_name.endswith(".fts")
                    or lower_name.endswith(".fit")
                ):
                    try:
                        from astropy.io import fits as fits_fallback

                        header = fits_fallback.getheader(self.imagename)
                        image_time = (
                            header.get("DATE-OBS")
                            or header.get("DATE_OBS")
                            or header.get("STARTOBS")
                        )

                        # Special handling for SOHO (DATE-OBS + TIME-OBS)
                        if (
                            header.get("TELESCOP") == "SOHO"
                            and header.get("TIME-OBS")
                            and image_time
                        ):
                            image_time = f"{image_time}T{header['TIME-OBS']}"

                        if image_time:
                            image_time = str(image_time)
                            if "T" in image_time:
                                # More aggressive cleaning: remove Z, timezone, and fractional seconds
                                clean_str = (
                                    image_time.replace("Z", "")
                                    .split("+")[0]
                                    .split(".")[0]
                                )
                                # Handle case where there's a negative offset timezone
                                if "-" in clean_str[11:]:
                                    clean_str = clean_str[:19]
                                try:
                                    obs_datetime = datetime.fromisoformat(clean_str)
                                except ValueError:
                                    # Last resort: just extract date part
                                    date_part = clean_str.split("T")[0]
                                    if len(date_part) >= 10:
                                        obs_datetime = datetime.strptime(
                                            date_part[:10], "%Y-%m-%d"
                                        )
                            elif "-" in image_time and len(image_time) >= 10:
                                obs_datetime = datetime.strptime(
                                    image_time[:10], "%Y-%m-%d"
                                )
                    except Exception as fallback_err:
                        print(
                            f"[WARNING] Fallback time parsing also failed: {fallback_err}"
                        )
                        self.show_status_message(
                            f"Fallback time parsing failed: {fallback_err}"
                        )

            # Try CASA image metadata (using IA tool coordsys like the rest of viewer.py)
            if not obs_datetime and hasattr(self, "imagename") and self.imagename:
                if not (
                    self.imagename.endswith(".fits") or self.imagename.endswith(".fts")
                ):
                    try:
                        # Use IA tool to read CASA coordinate system
                        from casatools import image as IA
                        from astropy.time import Time

                        ia_tool = IA()
                        ia_tool.open(self.imagename)
                        csys = ia_tool.coordsys()
                        csys_record = csys.torecord()
                        ia_tool.close()

                        # Extract obsdate from coordinate system
                        if "obsdate" in csys_record:
                            obsdate = csys_record["obsdate"]
                            m0 = obsdate.get("m0", {})
                            mjd_value = m0.get("value", None)
                            if mjd_value is not None:
                                # CASA stores time as MJD
                                from astropy.time import Time

                                t = Time(mjd_value, format="mjd")
                                obs_datetime = t.datetime
                                # print(
                                #    f"Extracted CASA observation time: {obs_datetime}"
                                # )
                    except Exception as e:
                        print(f"[WARNING] Could not extract CASA observation time: {e}")
                        import traceback

                        traceback.print_exc()

            # Set time range if we found a time
            initial_start = None
            initial_end = None

            if obs_datetime:
                start_dt = obs_datetime - timedelta(minutes=30)
                end_dt = obs_datetime + timedelta(minutes=30)
                initial_start = QDateTime(start_dt)
                initial_end = QDateTime(end_dt)
                self.show_status_message(
                    f"Opening Helioviewer Browser for {obs_datetime} (±30 min)"
                )
            else:
                self.show_status_message(
                    "No observation time found in file, using default time range"
                )

            # Launch browser
            # browser = HelioviewerBrowser(self.parent(), initial_start=initial_start, initial_end=initial_end)
            browser = HelioviewerBrowser(
                self, initial_start=initial_start, initial_end=initial_end
            )
            browser.show()

        except Exception as e:
            from PyQt5.QtWidgets import QMessageBox
            import traceback

            traceback.print_exc()
            QMessageBox.warning(
                self, "Error", f"Could not open Helioviewer Browser: {str(e)}"
            )

    def update_tab_name_from_path(self, path):
        """Update the tab name to the basename of the given path"""
        if path:
            basename = os.path.basename(
                path.rstrip("/")
            )  # Remove trailing slash for directories

            # Get the main window
            main_window = self.window()
            if isinstance(main_window, SolarRadioImageViewerApp):
                # Get the tab widget
                tab_widget = main_window.tab_widget
                # Find our index in the tabs list
                try:
                    index = main_window.tabs.index(self)
                    tab_widget.setTabText(index, basename)
                except ValueError:
                    pass  # Not found in tabs list

    def select_file_or_directory(self):
        import time

        # Check if we're in remote mode - if so, use remote file browser
        main_window = self.window()
        if hasattr(main_window, "remote_connection") and main_window.remote_connection:
            if main_window.remote_connection.is_connected():
                # Use remote file browser instead of local dialog
                self._select_remote_file(main_window)
                return

        if self.radio_casa_image.isChecked():
            # Select CASA image directory
            directory = QFileDialog.getExistingDirectory(
                self,
                caption="Select an image",
                options=QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
            )
            if directory:
                start_time = time.time()
                self._reset_hpc_state()  # Clear HPC state for new image
                self.imagename = directory
                self.dir_entry.setText(directory)
                # Clear contour data so it gets reloaded for new image
                self.contour_settings["contour_data"] = None
                self.current_contour_wcs = None
                # Clear figure to prevent restoring old view limits
                self.figure.clear()
                self.on_visualization_changed(dir_load=True)
                # self.auto_minmax()
                self.update_tab_name_from_path(directory)  # Update tab name
                self.show_status_message(
                    f"Loaded {directory} in {time.time() - start_time:.2f} seconds"
                )
                # Scan directory for file navigation
                self._scan_directory_files()
        else:
            # Select FITS file
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "Select a FITS file",
                "",
                "FITS files (*.fits *.fts);; All files (*)",
            )
            if file_path:
                start_time = time.time()
                self._reset_hpc_state()  # Clear HPC state for new image
                self.imagename = file_path
                self.dir_entry.setText(file_path)
                # Clear contour data so it gets reloaded for new image
                self.contour_settings["contour_data"] = None
                self.current_contour_wcs = None
                # Clear figure to prevent restoring old view limits
                self.figure.clear()
                self.on_visualization_changed(dir_load=True)
                # self.auto_minmax()
                self.update_tab_name_from_path(file_path)  # Update tab name
                self.show_status_message(
                    f"Loaded {file_path} in {time.time() - start_time:.2f} seconds"
                )
                # Scan directory for file navigation
                self._scan_directory_files()

    def _select_remote_file(self, main_window):
        """
        Open the remote file browser when in remote mode.
        This is called by select_file_or_directory when connected to a remote server.
        """
        import time

        try:
            from .remote import RemoteFileBrowser

            # Determine if we're in CASA mode (directories) or FITS mode (files)
            casa_mode = self.radio_casa_image.isChecked()

            browser = RemoteFileBrowser(
                main_window.remote_connection,
                cache=main_window.remote_cache,
                parent=self,
                casa_mode=casa_mode,
            )

            # Connect to handle the selected file
            def on_file_selected(local_path):
                if not local_path or not os.path.exists(local_path):
                    return

                start_time = time.time()
                self._reset_hpc_state()
                self.imagename = local_path
                self.dir_entry.setText(local_path)
                self.contour_settings["contour_data"] = None
                self.current_contour_wcs = None
                self.figure.clear()
                self.on_visualization_changed(dir_load=True)
                self.update_tab_name_from_path(local_path)

                # Set remote mode - store remote directory info for navigation
                self._is_remote_mode = True
                self._remote_base_dir = browser.current_path

                # Populate remote file list for navigation
                self._populate_remote_file_list(
                    main_window, browser.current_path, casa_mode, local_path
                )

                elapsed = time.time() - start_time
                basename = os.path.basename(local_path)
                self.show_status_message(
                    f"Loaded remote file: {basename} ({elapsed:.2f}s)"
                )

            browser.fileSelected.connect(on_file_selected)
            browser.exec_()

        except Exception as e:
            from PyQt5.QtWidgets import QMessageBox

            QMessageBox.critical(
                self, "Remote Error", f"Failed to open remote browser: {e}"
            )

    def schedule_plot(self):
        # If a timer already exists and is active, stop it.
        if hasattr(self, "_plot_timer") and self._plot_timer.isActive():
            self._plot_timer.stop()
        else:
            self._plot_timer = QTimer(self)
            self._plot_timer.setSingleShot(True)
            # Use a lambda to call plot_image with current parameters.
            self._plot_timer.timeout.connect(
                lambda: self.plot_image(
                    float(self.vmin_entry.text()),
                    float(self.vmax_entry.text()),
                    self.stretch_combo.currentText(),
                    self.cmap_combo.currentText(),
                    float(self.gamma_entry.text()),
                )
            )
        self._plot_timer.start(50)  # 10ms delay

    def plot_data(self):
        self.on_visualization_changed()

    def update_display(self, colormap_name=None):
        """
        Fast visualization update - only redraws without reloading data.
        Use this for colormap, stretch, gamma, vmin/vmax changes.
        """
        if self.current_image_data is None:
            return
        self.on_visualization_changed(colormap_name=colormap_name, reload_data=False)

    def on_visualization_changed(
        self, colormap_name=None, dir_load=False, reload_data=True
    ):
        """
        Handle visualization parameter changes.

        Args:
            colormap_name: Optional colormap to use
            dir_load: Whether this is a directory load (applies presets)
            reload_data: If False, skip load_data and just redraw (faster for viz-only changes)
        """
        if not hasattr(self, "imagename") or not self.imagename:
            QMessageBox.warning(
                self, "No Image", "Please select a CASA image directory first!"
            )
            return

        if dir_load:
            self._reproject_cache = {}

        tight_layout = False
        """if dir_load:
            tight_layout = True
        else:
            tight_layout = False"""

        main_window = self.parent()
        # if main_window and hasattr(main_window, "statusBar"):
        if main_window:
            if reload_data:
                self.show_status_message("Loading ... Please wait")
            else:
                self.show_status_message("Updating display...")

        stokes = self.stokes_combo.currentText() if self.stokes_combo else "I"
        try:
            threshold = float(self.threshold_entry.text())
        except (ValueError, AttributeError):
            threshold = 10.0
            if hasattr(self, "threshold_entry"):
                self.threshold_entry.setText("10.0")

        try:
            try:
                vmin_val = float(self.vmin_entry.text())
                vmax_val = float(self.vmax_entry.text())
            except (ValueError, AttributeError):
                vmin_val = None
                vmax_val = None

            try:
                gamma = float(self.gamma_entry.text())
            except (ValueError, AttributeError):
                gamma = 1.0
                if hasattr(self, "gamma_entry"):
                    self.gamma_entry.setText("1.0")

            stretch = (
                self.stretch_combo.currentText()
                if hasattr(self, "stretch_combo")
                else "linear"
            )

            cmap = "viridis"
            if colormap_name and colormap_name in plt.colormaps():
                cmap = colormap_name
                self.cmap_combo.setCurrentText(cmap)
            elif hasattr(self, "cmap_combo"):
                cmap_text = self.cmap_combo.currentText()
                if cmap_text in plt.colormaps():
                    cmap = cmap_text
                else:
                    matches = [
                        cm for cm in plt.colormaps() if cmap_text.lower() in cm.lower()
                    ]
                    if matches:
                        cmap = matches[0]
                        self.cmap_combo.setCurrentText(cmap)
                    else:
                        print(
                            f"[WARNING] {cmap_text} colormap not available, using default"
                        )
                        if main_window:
                            self.show_status_message(
                                f"WARNING: {cmap_text} colormap not available, using default"
                            )
                        self.cmap_combo.setCurrentText("viridis")

            # Only reload data if needed (new image, stokes change, etc.)
            # Skip for visualization-only changes (colormap, stretch, gamma, vmin/vmax)
            if reload_data:
                # Validate Stokes selection when loading a new image
                if dir_load:
                    stokes = self._validate_and_switch_stokes(self.imagename, stokes)
                self.load_data(
                    self.imagename, stokes, threshold, auto_adjust_rms=dir_load
                )
            if dir_load:
                fname = os.path.basename(self.imagename).lower()
                if "hmi" in fname:
                    self.HMI_presets()
                elif "aia" in fname:
                    if "94" in fname:
                        self.aia_presets(wavelength=94)
                    elif "131" in fname:
                        self.aia_presets(wavelength=131)
                    elif "171" in fname:
                        self.aia_presets(wavelength=171)
                    elif "193" in fname:
                        self.aia_presets(wavelength=193)
                    elif "211" in fname:
                        self.aia_presets(wavelength=211)
                    elif "304" in fname:
                        self.aia_presets(wavelength=304)
                    elif "335" in fname:
                        self.aia_presets(wavelength=335)
                    elif "1600" in fname:
                        self.aia_presets(wavelength=1600)
                    elif "1700" in fname:
                        self.aia_presets(wavelength=1700)
                    elif "4500" in fname:
                        self.aia_presets(wavelength=4500)
                    else:
                        self.aia_presets(wavelength=171)
                elif "eit" in fname or "efz" in fname:
                    # SOHO EIT files
                    if "171" in fname:
                        self.EIT_presets(wavelength=171)
                    elif "195" in fname:
                        self.EIT_presets(wavelength=195)
                    elif "284" in fname:
                        self.EIT_presets(wavelength=284)
                    elif "304" in fname:
                        self.EIT_presets(wavelength=304)
                    else:
                        self.EIT_presets(wavelength=171)
                elif "lasco" in fname:
                    if "cal_2" in fname:
                        self.LASCO_presets(detector="C2")
                    elif "cal_3" in fname:
                        self.LASCO_presets(detector="C3")
                    else:
                        self.LASCO_presets(detector="C2")
                elif "iris" in fname or "sji" in fname:
                    # IRIS SJI files
                    if "1330" in fname:
                        self.IRIS_presets(wavelength=1330)
                    elif "1400" in fname:
                        self.IRIS_presets(wavelength=1400)
                    elif "2796" in fname:
                        self.IRIS_presets(wavelength=2796)
                    elif "2832" in fname:
                        self.IRIS_presets(wavelength=2832)
                    else:
                        self.IRIS_presets(wavelength=1330)
                elif "suvi" in fname:
                    # GOES SUVI files
                    if "094" in fname or "ci094" in fname:
                        self.SUVI_presets(wavelength=94)
                    elif "131" in fname:
                        self.SUVI_presets(wavelength=131)
                    elif "171" in fname:
                        self.SUVI_presets(wavelength=171)
                    elif "195" in fname:
                        self.SUVI_presets(wavelength=195)
                    elif "284" in fname:
                        self.SUVI_presets(wavelength=284)
                    elif "304" in fname:
                        self.SUVI_presets(wavelength=304)
                    else:
                        self.SUVI_presets(wavelength=171)
                elif "gong" in fname:
                    self.GONG_presets()
                elif "euvi" in fname or "eua" in fname or "eub" in fname:
                    # STEREO EUVI
                    self.STEREO_presets("EUVI")
                elif "cor1" in fname or "c1a" in fname or "c1b" in fname:
                    # STEREO COR1
                    self.STEREO_presets("COR1")
                elif "cor2" in fname or "c2a" in fname or "c2b" in fname:
                    # STEREO COR2
                    self.STEREO_presets("COR2")
                else:
                    vmin_val = float(np.nanmin(self.current_image_data))
                    vmax_val = float(np.nanmax(self.current_image_data))
                    self.set_range(vmin_val, vmax_val)
                    # Only call plot_image here - preset methods already call it internally
                    self.plot_image(
                        vmin_val,
                        vmax_val,
                        stretch,
                        cmap,
                        gamma,
                        tight_layout=tight_layout,
                        preserve_view=not dir_load,
                    )
                    # print(f"Plotting image with vmin={vmin_val}, vmax={vmax_val}")
            elif vmin_val is None or vmax_val is None:
                self.auto_minmax()
            else:
                self.plot_image(
                    vmin_val,
                    vmax_val,
                    stretch,
                    cmap,
                    gamma,
                    tight_layout=tight_layout,
                    preserve_view=not dir_load,
                )

            if main_window:
                if dir_load:
                    img_name = os.path.basename(self.imagename)
                    self.show_status_message(f"Loaded {img_name}")
                else:
                    self.show_status_message("Display updated")
        except Exception as e:
            if main_window:
                self.show_status_message(f"Error: {str(e)}")
            QMessageBox.critical(self, "Error", f"Failed to load/plot data: {str(e)}")

    def auto_minmax(self):
        if self.current_image_data is None:
            return

        data = self.current_image_data
        dmin = float(np.nanmin(data))
        dmax = float(np.nanmax(data))
        self.set_range(dmin, dmax)

        stretch = (
            self.stretch_combo.currentText()
            if hasattr(self, "stretch_combo")
            else "linear"
        )
        cmap = (
            self.cmap_combo.currentText() if hasattr(self, "cmap_combo") else "viridis"
        )
        try:
            gamma = float(self.gamma_entry.text())
        except (ValueError, AttributeError):
            gamma = 1.0

        self.plot_image(dmin, dmax, stretch, cmap, gamma)

        main_window = self.parent()
        if main_window:
            self.show_status_message(
                f"Set display range to min/max: [{dmin:.4g}, {dmax:.4g}]"
            )

    def auto_percentile(self):
        if self.current_image_data is None:
            return

        data = self.current_image_data
        p1 = np.nanpercentile(data, 1)
        p99 = np.nanpercentile(data, 99)
        self.set_range(p1, p99)

        stretch = (
            self.stretch_combo.currentText()
            if hasattr(self, "stretch_combo")
            else "linear"
        )
        cmap = (
            self.cmap_combo.currentText() if hasattr(self, "cmap_combo") else "viridis"
        )
        try:
            gamma = float(self.gamma_entry.text())
        except (ValueError, AttributeError):
            gamma = 1.0

        self.plot_image(p1, p99, stretch, cmap, gamma)

        main_window = self.parent()
        if main_window:
            self.show_status_message(
                f"Set display range to 1-99 percentile: [{p1:.4g}, {p99:.4g}]"
            )

    def auto_percentile_99(self):
        if self.current_image_data is None:
            return

        data = self.current_image_data
        p01 = np.nanpercentile(data, 0.1)
        p999 = np.nanpercentile(data, 99.9)
        self.set_range(p01, p999)
        stretch = (
            self.stretch_combo.currentText()
            if hasattr(self, "stretch_combo")
            else "linear"
        )
        cmap = (
            self.cmap_combo.currentText() if hasattr(self, "cmap_combo") else "viridis"
        )
        try:
            gamma = float(self.gamma_entry.text())
        except (ValueError, AttributeError):
            gamma = 1.0

        self.plot_image(p01, p999, stretch, cmap, gamma)
        main_window = self.parent()
        if main_window:
            self.show_status_message(
                f"Set display range to 0.1st and 99.9th percentiles: [{p01:.4g}, {p999:.4g}]"
            )

    def auto_percentile_95(self):
        if self.current_image_data is None:
            return

        data = self.current_image_data
        p5 = np.nanpercentile(data, 5)
        p95 = np.nanpercentile(data, 95)
        self.set_range(p5, p95)
        stretch = (
            self.stretch_combo.currentText()
            if hasattr(self, "stretch_combo")
            else "linear"
        )
        cmap = (
            self.cmap_combo.currentText() if hasattr(self, "cmap_combo") else "viridis"
        )
        try:
            gamma = float(self.gamma_entry.text())
        except (ValueError, AttributeError):
            gamma = 1.0
        self.plot_image(p5, p95, stretch, cmap, gamma)
        main_window = self.parent()
        if main_window:
            self.show_status_message(
                f"Set display range to 5th and 95th percentiles: [{p5:.4g}, {p95:.4g}]"
            )

    def auto_median_rms(self):
        if self.current_image_data is None:
            return

        data = self.current_image_data
        median_val = np.nanmedian(data)
        rms_val = np.sqrt(np.nanmean((data - median_val) ** 2))
        low = median_val - 3 * rms_val
        high = median_val + 3 * rms_val
        self.set_range(low, high)

        stretch = (
            self.stretch_combo.currentText()
            if hasattr(self, "stretch_combo")
            else "linear"
        )
        cmap = (
            self.cmap_combo.currentText() if hasattr(self, "cmap_combo") else "viridis"
        )
        try:
            gamma = float(self.gamma_entry.text())
        except (ValueError, AttributeError):
            gamma = 1.0

        self.plot_image(low, high, stretch, cmap, gamma)

        main_window = self.parent()
        if main_window:
            self.show_status_message(
                f"Set display range to median±3×RMS: [{low:.4g}, {high:.4g}]"
            )

    def HMI_presets(self):
        """HMI preset"""
        if self.current_image_data is None:
            return

        # data = self.current_image_data
        dmin = -1000
        dmax = 1000
        stretch = "linear"
        cmap = "gray"
        gamma = 1.0
        self.set_range(dmin, dmax)
        self.vmin_entry.setText(f"{dmin:.3f}")
        self.vmax_entry.setText(f"{dmax:.3f}")
        self.stretch_combo.setCurrentText(stretch)
        self.cmap_combo.setCurrentText(cmap)
        self.gamma_entry.setText(f"{gamma:.1f}")
        # self.update_gamma_slider()
        self.plot_image(dmin, dmax, stretch, cmap, gamma, interpolation="nearest")

        main_window = self.parent()
        if main_window:
            self.show_status_message(
                f"Set display range to [-1000, 1000], stretch to linear, colormap to gray"
            )

    def aia_presets(self, wavelength=171):
        """
        Apply AIA preset for the specified wavelength.

        Args:
            wavelength (int): AIA wavelength in Angstroms (94, 131, 171, 193, 211, 304, 335, 1600, 1700, 4500)
        """
        if self.current_image_data is None:
            return

        # Convert wavelength to string and ensure it's a supported value
        wavelength = str(wavelength)
        supported_wavelengths = [
            "94",
            "131",
            "171",
            "193",
            "211",
            "304",
            "335",
            "1600",
            "1700",
            "4500",
        ]
        if wavelength not in supported_wavelengths:
            wavelength = "171"  # Default to 171 if unsupported wavelength

        data = self.current_image_data
        dmin = 0.0
        dmax = float(np.nanmax(data))
        self.set_range(dmin, dmax)
        try:
            gamma = float(self.gamma_entry.text())
        except (ValueError, AttributeError):
            gamma = 1.0
        if wavelength == "4500":
            stretch = "linear"
        elif wavelength == "1700":
            stretch = "power"
            gamma = 0.7
        elif wavelength == "1600":
            stretch = "power"
            gamma = 0.55
        elif wavelength == "94" or wavelength == "304":
            stretch = "arcsinh"
        elif wavelength == "335":
            stretch = "power"
            gamma = 0.3
        else:
            stretch = "sqrt"
        # check if cmap available
        if f"sdoaia{wavelength}" in plt.colormaps():
            cmap = f"sdoaia{wavelength}"
        else:
            cmap = self.cmap_combo.currentText()
            print(f"[Warning] {cmap} colormap not available, using default")
            self.show_status_message(
                f"Warning: {cmap} colormap not available, using default"
            )

        self.stretch_combo.setCurrentText(stretch)
        self.cmap_combo.setCurrentText(cmap)
        self.gamma_entry.setText(f"{gamma:.1f}")
        # self.update_gamma_slider()
        self.vmin_entry.setText(f"{dmin:.3f}")
        self.vmax_entry.setText(f"{dmax:.3f}")
        self.plot_image(dmin, dmax, stretch, cmap, gamma, interpolation="nearest")

        main_window = self.parent()
        if main_window:
            self.show_status_message(
                f"Set display range to 0.0 and {dmax:.4g}, stretch to arcsinh, colormap to {cmap}"
            )

    def aia_presets_94(self):
        """AIA 94 Angstrom preset with specific colormap"""
        self.aia_presets(94)

    def aia_presets_131(self):
        """AIA 131 Angstrom preset with specific colormap"""
        self.aia_presets(131)

    def aia_presets_171(self):
        """AIA 171 Angstrom preset with specific colormap"""
        self.aia_presets(171)

    def aia_presets_193(self):
        """AIA 193 Angstrom preset with specific colormap"""
        self.aia_presets(193)

    def aia_presets_211(self):
        """AIA 211 Angstrom preset with specific colormap"""
        self.aia_presets(211)

    def aia_presets_304(self):
        """AIA 304 Angstrom preset with specific colormap"""
        self.aia_presets(304)

    def aia_presets_335(self):
        """AIA 335 Angstrom preset with specific colormap"""
        self.aia_presets(335)

    def aia_presets_1600(self):
        """AIA 1600 Angstrom preset with specific colormap"""
        self.aia_presets(1600)

    def aia_presets_1700(self):
        """AIA 1700 Angstrom preset with specific colormap"""
        self.aia_presets(1700)

    def aia_presets_4500(self):
        """AIA 4500 Angstrom preset with specific colormap"""
        self.aia_presets(4500)

    def EIT_presets(self, wavelength=171):
        """
        Apply SOHO EIT preset for the specified wavelength.

        Args:
            wavelength (int): EIT wavelength (171, 195, 284, 304)
        """
        if self.current_image_data is None:
            return

        wavelength = str(wavelength)
        supported_wavelengths = ["171", "195", "284", "304"]
        if wavelength not in supported_wavelengths:
            wavelength = "171"

        data = self.current_image_data
        valid_data = data[~np.isnan(data)]
        dmin = 0.0
        dmax = float(np.nanmax(valid_data))
        stretch = "sqrt"
        gamma = 1.0

        # Use SOHO EIT colormap if available
        cmap_name = f"sohoeit{wavelength}"
        if cmap_name in plt.colormaps():
            cmap = cmap_name
        else:
            cmap = "inferno"

        self.set_range(dmin, dmax)
        self.vmin_entry.setText(f"{dmin:.3f}")
        self.vmax_entry.setText(f"{dmax:.3f}")
        self.stretch_combo.setCurrentText(stretch)
        self.cmap_combo.setCurrentText(cmap)
        self.gamma_entry.setText(f"{gamma:.1f}")
        self.plot_image(dmin, dmax, stretch, cmap, gamma, interpolation="nearest")

        main_window = self.parent()
        if main_window:
            self.show_status_message(
                f"Set SOHO EIT {wavelength} preset: colormap={cmap}, stretch={stretch}"
            )

    def LASCO_presets(self, detector="C2"):
        """SOHO LASCO coronagraph preset"""
        if self.current_image_data is None:
            return

        data = self.current_image_data
        valid_data = data[~np.isnan(data)]
        dmin = float(np.nanmin(valid_data))
        dmax = float(np.nanmax(valid_data))
        stretch = "sqrt"
        gamma = 1.0

        # Use LASCO colormap if available
        if f"soholasco{detector}" in plt.colormaps():
            cmap = f"soholasco{detector}"
        else:
            cmap = "afmhot"

        self.set_range(dmin, dmax)
        self.vmin_entry.setText(f"{dmin:.3f}")
        self.vmax_entry.setText(f"{dmax:.3f}")
        self.stretch_combo.setCurrentText(stretch)
        self.cmap_combo.setCurrentText(cmap)
        self.gamma_entry.setText(f"{gamma:.1f}")
        self.plot_image(dmin, dmax, stretch, cmap, gamma, interpolation="nearest")

        main_window = self.parent()
        if main_window:
            self.show_status_message(
                f"Set SOHO LASCO preset: colormap={cmap}, stretch={stretch}"
            )

    def IRIS_presets(self, wavelength=1330):
        """
        Apply IRIS SJI preset for the specified wavelength.

        Args:
            wavelength (int): IRIS SJI wavelength (1330, 1400, 2796, 2832)
        """
        if self.current_image_data is None:
            return

        wavelength = str(wavelength)
        supported_wavelengths = ["1330", "1400", "2796", "2832"]
        if wavelength not in supported_wavelengths:
            wavelength = "1330"

        data = self.current_image_data
        valid_data = data[~np.isnan(data)]
        dmin = 0.0
        dmax = float(
            np.nanpercentile(valid_data, 99.5)
        )  # Use 99.5 percentile to avoid hot pixels
        stretch = "sqrt"
        gamma = 1.0

        # Use IRIS SJI colormap if available
        cmap_name = f"irissji{wavelength}"
        if cmap_name in plt.colormaps():
            cmap = cmap_name
        else:
            cmap = "viridis"

        self.set_range(dmin, dmax)
        self.vmin_entry.setText(f"{dmin:.3f}")
        self.vmax_entry.setText(f"{dmax:.3f}")
        self.stretch_combo.setCurrentText(stretch)
        self.cmap_combo.setCurrentText(cmap)
        self.gamma_entry.setText(f"{gamma:.1f}")
        self.plot_image(dmin, dmax, stretch, cmap, gamma, interpolation="nearest")

        main_window = self.parent()
        if main_window:
            self.show_status_message(
                f"Set IRIS SJI {wavelength} preset: colormap={cmap}, stretch={stretch}"
            )

    def SUVI_presets(self, wavelength=171):
        """
        Apply GOES SUVI preset for the specified wavelength.

        Args:
            wavelength (int): SUVI wavelength (94, 131, 171, 195, 284, 304)
        """
        if self.current_image_data is None:
            return

        data = self.current_image_data
        valid_data = data[~np.isnan(data)]
        dmin = 0.0
        dmax = float(np.nanpercentile(valid_data, 99.5))
        stretch = "sqrt"
        gamma = 1.0

        # SUVI doesn't have dedicated colormaps, use similar to AIA
        cmap_name = f"goes-rsuvi{wavelength}"
        if cmap_name in plt.colormaps():
            cmap = cmap_name
        else:
            cmap = "inferno"

        self.set_range(dmin, dmax)
        self.vmin_entry.setText(f"{dmin:.3f}")
        self.vmax_entry.setText(f"{dmax:.3f}")
        self.stretch_combo.setCurrentText(stretch)
        self.cmap_combo.setCurrentText(cmap)
        self.gamma_entry.setText(f"{gamma:.1f}")
        self.plot_image(dmin, dmax, stretch, cmap, gamma, interpolation="nearest")

        main_window = self.parent()
        if main_window:
            self.show_status_message(
                f"Set GOES SUVI {wavelength} preset: colormap={cmap}, stretch={stretch}"
            )

    def GONG_presets(self):
        """GONG magnetogram preset (similar to HMI)"""
        if self.current_image_data is None:
            return

        dmin = -1000
        dmax = 1000
        stretch = "linear"
        cmap = "gray"
        gamma = 1.0

        self.set_range(dmin, dmax)
        self.vmin_entry.setText(f"{dmin:.3f}")
        self.vmax_entry.setText(f"{dmax:.3f}")
        self.stretch_combo.setCurrentText(stretch)
        self.cmap_combo.setCurrentText(cmap)
        self.gamma_entry.setText(f"{gamma:.1f}")
        self.plot_image(dmin, dmax, stretch, cmap, gamma, interpolation="nearest")

        main_window = self.parent()
        if main_window:
            self.show_status_message(
                f"Set GONG magnetogram preset: [-1000, 1000], gray colormap"
            )

    def STEREO_presets(self, detector="EUVI"):
        """
        Apply STEREO SECCHI preset for the specified detector.

        Args:
            detector (str): STEREO detector (EUVI, COR1, COR2)
        """
        if self.current_image_data is None:
            return

        data = self.current_image_data
        valid_data = data[~np.isnan(data)] if np.any(np.isnan(data)) else data.flatten()

        detector = detector.upper()
        if detector == "EUVI":
            # EUVI: Range [680, 16428], use 1-99 percentile
            # dmin = float(np.nanpercentile(valid_data, 1))
            # dmax = float(np.nanpercentile(valid_data, 99))
            dmin = float(np.nanmin(valid_data))
            dmax = float(np.nanmax(valid_data))
            stretch = "sqrt"
            cmap = "sdoaia171" if "sdoaia171" in plt.colormaps() else "inferno"
        elif detector == "COR1":
            # COR1: Range [336, 8198], coronagraph
            dmin = float(np.nanpercentile(valid_data, 1))
            dmax = float(np.nanpercentile(valid_data, 99))
            stretch = "sqrt"
            cmap = "soholasco2" if "soholasco2" in plt.colormaps() else "afmhot"
        elif detector == "COR2":
            # COR2: Range [2052, 12564], coronagraph
            dmin = float(np.nanpercentile(valid_data, 1))
            dmax = float(np.nanpercentile(valid_data, 99))
            stretch = "sqrt"
            cmap = "soholasco2" if "soholasco2" in plt.colormaps() else "afmhot"
        else:
            # Default
            dmin = float(np.nanmin(valid_data))
            dmax = float(np.nanmax(valid_data))
            stretch = "linear"
            cmap = "viridis"

        gamma = 1.0
        self.set_range(dmin, dmax)
        self.vmin_entry.setText(f"{dmin:.3f}")
        self.vmax_entry.setText(f"{dmax:.3f}")
        self.stretch_combo.setCurrentText(stretch)
        self.cmap_combo.setCurrentText(cmap)
        self.gamma_entry.setText(f"{gamma:.1f}")
        self.plot_image(dmin, dmax, stretch, cmap, gamma, interpolation="nearest")

        main_window = self.parent()
        if main_window:
            self.show_status_message(
                f"Set STEREO {detector} preset: colormap={cmap}, stretch={stretch}"
            )

    def set_range(self, vmin_val, vmax_val):
        if self.current_image_data is None:
            return

        data = self.current_image_data
        dmin = float(np.nanmin(data))
        dmax = float(np.nanmax(data))
        rng = dmax - dmin
        if rng <= 0:
            return

        if vmin_val < dmin:
            vmin_val = dmin
        if vmax_val > dmax:
            vmax_val = dmax
        if vmax_val <= vmin_val:
            vmax_val = vmin_val + 1e-6

        self.vmin_entry.setText(f"{vmin_val:.3f}")
        self.vmax_entry.setText(f"{vmax_val:.3f}")

    def update_gamma_value(self):
        """Update gamma from slider - debounced for responsiveness."""
        gamma = self.gamma_slider.value() / 20.0
        self.gamma_entry.setText(f"{gamma:.1f}")

        # Only update plot if using power stretch
        if (
            self.current_image_data is not None
            and self.stretch_combo.currentText() == "power"
        ):
            # Use debouncing to avoid multiple redraws while dragging
            if self._gamma_debounce_timer is None:
                from PyQt5.QtCore import QTimer

                self._gamma_debounce_timer = QTimer()
                self._gamma_debounce_timer.setSingleShot(True)
                self._gamma_debounce_timer.timeout.connect(self._apply_gamma_change)

            # Only update the plot immediately if NOT dragging (e.g. keyboard or track click)
            # If dragging, we wait for sliderReleased
            if not self.gamma_slider.isSliderDown():
                # Restart the timer (30ms delay)
                self._gamma_debounce_timer.start(30)

    def _apply_gamma_change(self):
        """Actually apply the gamma change after debounce delay."""
        if self.current_image_data is None:
            return
        try:
            gamma = float(self.gamma_entry.text())
            vmin_val = float(self.vmin_entry.text())
            vmax_val = float(self.vmax_entry.text())
            stretch = self.stretch_combo.currentText()
            cmap = self.cmap_combo.currentText()
            self.plot_image(vmin_val, vmax_val, stretch, cmap, gamma)
        except ValueError:
            pass

    def update_gamma_slider(self):
        try:
            gamma = float(self.gamma_entry.text())
            if 0.1 <= gamma <= 3.0:
                self.gamma_slider.blockSignals(True)
                self.gamma_slider.setValue(int(gamma * 20))
                self.gamma_slider.blockSignals(False)

                if (
                    self.current_image_data is not None
                    and self.stretch_combo.currentText() == "power"
                ):
                    try:
                        vmin_val = float(self.vmin_entry.text())
                        vmax_val = float(self.vmax_entry.text())
                        stretch = self.stretch_combo.currentText()
                        cmap = self.cmap_combo.currentText()
                        self.plot_image(vmin_val, vmax_val, stretch, cmap, gamma)
                    except ValueError:
                        pass
        except ValueError:
            self.gamma_entry.setText("1.0")
            self.gamma_slider.setValue(20)

    def on_stretch_changed(self, index):
        self.update_gamma_slider_state()

        try:
            vmin_val = float(self.vmin_entry.text())
            vmax_val = float(self.vmax_entry.text())
        except (ValueError, AttributeError):
            if self.current_image_data is not None:
                vmin_val = float(np.nanmin(self.current_image_data))
                vmax_val = float(np.nanmax(self.current_image_data))
            else:
                return

        stretch = self.stretch_combo.currentText()
        cmap = self.cmap_combo.currentText()

        try:
            gamma = float(self.gamma_entry.text())
        except (ValueError, AttributeError):
            gamma = 1.0

        if self.current_image_data is not None:
            # self.plot_image(vmin_val, vmax_val, stretch, cmap, gamma)
            self.schedule_plot()
        main_window = self.parent()
        if main_window:
            self.show_status_message(f"Changed stretch to {stretch}")

    def update_gamma_slider_state(self):
        is_power = self.stretch_combo.currentText() == "power"
        self.gamma_slider.setEnabled(is_power)
        self.gamma_entry.setEnabled(is_power)

        if is_power:
            self.gamma_slider.setStyleSheet("")
            self.gamma_entry.setStyleSheet("")
        else:
            # Use global :disabled styles from styles.py
            self.gamma_slider.setStyleSheet("")
            self.gamma_entry.setStyleSheet("")

    def show_roi_stats(self, roi, ra_dec_info=""):
        if roi.size == 0:
            return

        rmin = np.nanmin(roi)
        rmax = np.nanmax(roi)
        rmean = np.nanmean(roi)
        rstd = np.nanstd(roi)
        rsum = np.nansum(roi)
        rrms = np.sqrt(np.nanmean(roi**2))

        # self.info_label.setText(f"ROI Stats: {roi.size} pixels{ra_dec_info}")
        self.info_label.setText(f"{ra_dec_info}")

        stats_values = [rmin, rmax, rmean, rstd, rsum, rrms]
        for i, val in enumerate(stats_values):
            self.stats_table.setItem(i, 1, QTableWidgetItem(f"{val:.6g}"))

        main_window = self.parent()
        if main_window:
            self.show_status_message(
                f"ROI selected: {roi.size} pixels, Mean={rmean:.4g}, Sum={rsum:.4g}, RMS={rrms:.4g}"
            )

    def _show_roi_histogram(self):
        """Show histogram of pixel values in current ROI with fitting options"""
        from scipy.optimize import curve_fit
        from scipy.stats import poisson

        if self.current_roi is None or self.current_image_data is None:
            QMessageBox.warning(self, "No ROI", "Please select a region first")
            return

        xlow, xhigh, ylow, yhigh = self.current_roi
        roi_data = self.current_image_data[xlow:xhigh, ylow:yhigh]
        roi_flat = roi_data.flatten()
        roi_flat = roi_flat[~np.isnan(roi_flat)]  # Remove NaNs

        if roi_flat.size == 0:
            QMessageBox.warning(
                self, "Empty ROI", "Selected region contains no valid data"
            )
            return

        # Create dialog - resizable
        dialog = QDialog(self)
        dialog.setWindowTitle("ROI Histogram")
        set_hand_cursor(dialog)
        dialog.resize(800, 650)
        dialog.setMinimumSize(600, 400)
        layout = QVBoxLayout(dialog)

        # Create figure
        fig = Figure(figsize=(8, 5))
        canvas = FigureCanvas(fig)
        layout.addWidget(canvas, stretch=1)  # Make canvas expand

        # Controls row 1
        controls_layout1 = QHBoxLayout()

        # Number of bins
        controls_layout1.addWidget(QLabel("Bins:"))
        bins_spin = QSpinBox()
        bins_spin.setRange(10, 500)
        bins_spin.setValue(50)
        controls_layout1.addWidget(bins_spin)

        # Log scale checkbox
        log_check = QCheckBox("Log Scale")
        controls_layout1.addWidget(log_check)

        # Show statistics checkbox
        stats_check = QCheckBox("Show Stats")
        stats_check.setChecked(True)
        controls_layout1.addWidget(stats_check)

        controls_layout1.addStretch()
        layout.addLayout(controls_layout1)

        # Controls row 2 - Fitting options
        controls_layout2 = QHBoxLayout()
        controls_layout2.addWidget(QLabel("Fit:"))

        gaussian_check = QCheckBox("Gaussian")
        controls_layout2.addWidget(gaussian_check)

        poisson_check = QCheckBox("Poisson")
        controls_layout2.addWidget(poisson_check)

        controls_layout2.addStretch()

        # Update button
        update_btn = QPushButton("Update")
        controls_layout2.addWidget(update_btn)

        layout.addLayout(controls_layout2)

        # Statistics info label
        stats_label = QLabel()
        stats_label.setStyleSheet("font-family: monospace; padding: 5px;")
        stats_label.setWordWrap(True)
        layout.addWidget(stats_label)

        # Fit results label
        fit_label = QLabel()
        fit_label.setStyleSheet(
            "font-family: monospace; padding: 5px; color: #1565C0; font-weight: bold;"
        )
        fit_label.setWordWrap(True)
        layout.addWidget(fit_label)

        # Close button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        def gaussian(x, amp, mean, sigma):
            return amp * np.exp(-((x - mean) ** 2) / (2 * sigma**2))

        def update_histogram():
            fig.clear()
            ax = fig.add_subplot(111)

            n_bins = bins_spin.value()
            use_log = log_check.isChecked()
            show_stats = stats_check.isChecked()
            fit_gaussian = gaussian_check.isChecked()
            fit_poisson = poisson_check.isChecked()

            # Calculate statistics
            mean_val = np.mean(roi_flat)
            median_val = np.median(roi_flat)
            std_val = np.std(roi_flat)
            min_val = np.min(roi_flat)
            max_val = np.max(roi_flat)

            # Plot histogram
            counts, bins_edges, patches = ax.hist(
                roi_flat,
                bins=n_bins,
                color="#2196F3",
                edgecolor="#1565C0",
                alpha=0.7,
                density=False,
            )

            # Bin centers for fitting
            bin_centers = (bins_edges[:-1] + bins_edges[1:]) / 2
            bin_width = bins_edges[1] - bins_edges[0]

            fit_results = []

            # Gaussian fit
            if fit_gaussian:
                try:
                    # Initial guesses
                    p0 = [np.max(counts), mean_val, std_val]
                    popt, pcov = curve_fit(
                        gaussian, bin_centers, counts, p0=p0, maxfev=5000
                    )
                    perr = np.sqrt(np.diag(pcov))

                    # Plot fit
                    x_fit = np.linspace(min_val, max_val, 300)
                    y_fit = gaussian(x_fit, *popt)
                    ax.plot(x_fit, y_fit, "r-", linewidth=2, label="Gaussian Fit")

                    # Calculate chi-square
                    y_pred = gaussian(bin_centers, *popt)
                    mask = counts > 0
                    chi2 = np.sum((counts[mask] - y_pred[mask]) ** 2 / counts[mask])
                    dof = np.sum(mask) - 3  # degrees of freedom
                    chi2_red = chi2 / dof if dof > 0 else np.nan

                    fit_results.append(
                        f"Gaussian: Amp={popt[0]:.4g}±{perr[0]:.2g}, "
                        f"Mean={popt[1]:.4g}±{perr[1]:.2g}, "
                        f"σ={popt[2]:.4g}±{perr[2]:.2g} | "
                        f"χ²={chi2:.2f}, Red. χ²={chi2_red:.3f}"
                    )
                except Exception as e:
                    fit_results.append(f"Gaussian fit failed: {str(e)}")

            # Poisson fit (for count-like data)
            if fit_poisson:
                try:
                    # Poisson is discrete - scale to match histogram
                    lambda_est = mean_val
                    if lambda_est > 0:
                        x_poisson = np.arange(max(0, int(min_val)), int(max_val) + 1)
                        y_poisson = (
                            poisson.pmf(x_poisson, lambda_est)
                            * len(roi_flat)
                            * bin_width
                        )
                        ax.plot(
                            x_poisson,
                            y_poisson,
                            "g-",
                            linewidth=2,
                            label=f"Poisson (λ={lambda_est:.2f})",
                        )

                        # Chi-square for Poisson
                        y_pred_p = (
                            poisson.pmf(bin_centers.astype(int), lambda_est)
                            * len(roi_flat)
                            * bin_width
                        )
                        mask = counts > 0
                        chi2_p = np.sum(
                            (counts[mask] - y_pred_p[mask]) ** 2 / counts[mask]
                        )
                        dof_p = np.sum(mask) - 1
                        chi2_red_p = chi2_p / dof_p if dof_p > 0 else np.nan

                        fit_results.append(
                            f"Poisson: λ={lambda_est:.4g} (from data mean) | "
                            f"χ²={chi2_p:.2f}, Red. χ²={chi2_red_p:.3f}"
                        )
                    else:
                        fit_results.append("Poisson fit: λ must be positive")
                except Exception as e:
                    fit_results.append(f"Poisson fit failed: {str(e)}")

            # Add statistics lines
            if show_stats:
                ax.axvline(
                    mean_val,
                    color="red",
                    linewidth=2,
                    linestyle="-",
                    label=f"Mean: {mean_val:.4g}",
                )
                ax.axvline(
                    median_val,
                    color="green",
                    linewidth=2,
                    linestyle="--",
                    label=f"Median: {median_val:.4g}",
                )
                ax.axvline(
                    mean_val - std_val,
                    color="orange",
                    linewidth=1.5,
                    linestyle=":",
                    label=f"Mean±σ",
                )
                ax.axvline(
                    mean_val + std_val, color="orange", linewidth=1.5, linestyle=":"
                )

            if fit_gaussian or fit_poisson or show_stats:
                ax.legend(loc="upper right", fontsize=9)

            if use_log:
                ax.set_yscale("log")

            ax.set_xlabel("Pixel Value")
            ax.set_ylabel("Count")
            ax.set_title(f"ROI Histogram ({roi_flat.size:,} pixels)")
            ax.grid(True, alpha=0.3)

            fig.tight_layout()
            canvas.draw()

            # Update stats label
            stats_text = (
                f"Min: {min_val:.6g}  |  Max: {max_val:.6g}  |  "
                f"Mean: {mean_val:.6g}  |  Median: {median_val:.6g}  |  "
                f"Std Dev: {std_val:.6g}  |  Pixels: {roi_flat.size:,}"
            )
            stats_label.setText(stats_text)

            # Update fit label
            if fit_results:
                fit_label.setText("\n".join(fit_results))
            else:
                fit_label.setText("")

        # Connect signals
        update_btn.clicked.connect(update_histogram)
        bins_spin.valueChanged.connect(update_histogram)
        log_check.stateChanged.connect(update_histogram)
        stats_check.stateChanged.connect(update_histogram)
        gaussian_check.stateChanged.connect(update_histogram)
        poisson_check.stateChanged.connect(update_histogram)

        # Initial plot
        update_histogram()

        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

    def _toggle_ruler_mode(self, checked):
        """Toggle ruler mode for distance measurement"""
        self._ruler_mode = checked
        if checked:
            self.show_status_message(
                "Ruler mode: Click first point, then click second point to measure"
            )
            # Disconnect ROI selector temporarily
            if self.roi_selector:
                self.roi_selector.set_active(False)
            # Connect mouse events for ruler (only click, no motion/release for drag)
            self._ruler_click_cid = self.canvas.mpl_connect(
                "button_press_event", self._ruler_on_click
            )
        else:
            self.show_status_message("Ruler mode off")
            # Reconnect ROI selector
            if self.roi_selector:
                self.roi_selector.set_active(True)
            # Disconnect ruler events
            if hasattr(self, "_ruler_click_cid"):
                self.canvas.mpl_disconnect(self._ruler_click_cid)
            # Clear ruler graphics
            self._clear_ruler()

    def _clear_ruler(self):
        """Clear ruler line and text from plot"""
        if self._ruler_line is not None:
            try:
                self._ruler_line.remove()
            except:
                pass
            self._ruler_line = None
        if self._ruler_text is not None:
            try:
                self._ruler_text.remove()
            except:
                pass
            self._ruler_text = None
        self._ruler_start = None
        self.canvas.draw_idle()

    def _ruler_on_click(self, event):
        """Handle mouse click for ruler - two clicks to measure"""
        if event.inaxes is None or event.button != 1:
            return

        if self._ruler_start is None:
            # First click - clear previous measurement first, then set start point
            # Clear graphics but don't reset _ruler_start yet
            if self._ruler_line is not None:
                try:
                    self._ruler_line.remove()
                except:
                    pass
                self._ruler_line = None
            if self._ruler_text is not None:
                try:
                    self._ruler_text.remove()
                except:
                    pass
                self._ruler_text = None

            # Now set start point
            self._ruler_start = (event.xdata, event.ydata)

            # Draw start point marker
            ax = event.inaxes
            (self._ruler_line,) = ax.plot(
                [event.xdata], [event.ydata], "ro", markersize=8
            )
            self.canvas.draw_idle()

            self.show_status_message(
                f"Start point set at ({event.xdata:.1f}, {event.ydata:.1f}) - click second point"
            )
        else:
            # Second click - set end point and calculate distance
            x1, y1 = self._ruler_start
            x2, y2 = event.xdata, event.ydata

            ax = event.inaxes

            # Draw line between points
            if self._ruler_line is not None:
                self._ruler_line.remove()
            (self._ruler_line,) = ax.plot(
                [x1, x2], [y1, y2], "r-", linewidth=2, marker="o", markersize=8
            )

            # Calculate distance
            distance_info = self._calculate_angular_distance(x1, y1, x2, y2)

            # Draw distance text at midpoint
            mid_x, mid_y = (x1 + x2) / 2, (y1 + y2) / 2
            self._ruler_text = ax.text(
                mid_x,
                mid_y,
                distance_info,
                fontsize=9,
                color="red",
                fontweight="bold",
                bbox=dict(boxstyle="round", facecolor="white", alpha=0.6),
            )

            self.canvas.draw_idle()

            # Show in status bar
            self.show_status_message(
                f"Distance: {distance_info} (end: {x2:.1f}, {y2:.1f})"
            )

            # Reset for next measurement
            self._ruler_start = None

    def _calculate_angular_distance(self, x1, y1, x2, y2):
        """Calculate angular distance between two pixel coordinates using WCS"""
        # Pixel distance
        pixel_dist = np.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        dx_px = abs(x2 - x1)
        dy_px = abs(y2 - y1)

        if self.current_wcs:
            try:
                # Get pixel scale from WCS
                increment = self.current_wcs.increment()["numeric"][0:2]
                # Convert from radians to arcsec
                scale_x = abs(increment[0]) * 180 / np.pi * 3600  # arcsec/pixel
                scale_y = abs(increment[1]) * 180 / np.pi * 3600  # arcsec/pixel

                # Calculate angular distance
                dx_arcsec = dx_px * scale_x
                dy_arcsec = dy_px * scale_y
                angular_dist = np.sqrt(dx_arcsec**2 + dy_arcsec**2)

                # Get world coordinates for both points
                world1 = self.current_wcs.toworld([x1, y1, 0, 0])["numeric"]
                world2 = self.current_wcs.toworld([x2, y2, 0, 0])["numeric"]
                ra1_deg = world1[0] * 180 / np.pi if world1[0] else None
                dec1_deg = world1[1] * 180 / np.pi if world1[1] else None
                ra2_deg = world2[0] * 180 / np.pi if world2[0] else None
                dec2_deg = world2[1] * 180 / np.pi if world2[1] else None

                # Professional terminal output
                print("\n" + "=" * 60)
                print("              LINE DISTANCE MEASUREMENT")
                print("=" * 60)
                print(f'  Coordinate System: WCS (scale: {scale_x:.4f}"/px)')
                print("-" * 60)
                print(
                    f"  {'Point':<12} {'X (px)':<12} {'Y (px)':<12} {'RA (deg)':<15} {'Dec (deg)':<15}"
                )
                print("-" * 60)
                if ra1_deg is not None and dec1_deg is not None:
                    print(
                        f"  {'Start':<12} {x1:<12.2f} {y1:<12.2f} {ra1_deg:<15.6f} {dec1_deg:<15.6f}"
                    )
                else:
                    print(
                        f"  {'Start':<12} {x1:<12.2f} {y1:<12.2f} {'N/A':<15} {'N/A':<15}"
                    )
                if ra2_deg is not None and dec2_deg is not None:
                    print(
                        f"  {'End':<12} {x2:<12.2f} {y2:<12.2f} {ra2_deg:<15.6f} {dec2_deg:<15.6f}"
                    )
                else:
                    print(
                        f"  {'End':<12} {x2:<12.2f} {y2:<12.2f} {'N/A':<15} {'N/A':<15}"
                    )
                print("-" * 60)
                print(f"  {'ΔX':<20} {dx_px:>12.2f} px    {dx_arcsec:>12.2f} arcsec")
                print(f"  {'ΔY':<20} {dy_px:>12.2f} px    {dy_arcsec:>12.2f} arcsec")
                print("-" * 60)
                print(
                    f"  {'Total Distance':<20} {pixel_dist:>12.2f} px    {angular_dist:>12.2f} arcsec"
                )
                if angular_dist >= 60:
                    print(f"  {'':<20} {'':<12}       {angular_dist/60:>12.2f} arcmin")
                print("=" * 60 + "\n")

                # Format output for display
                if angular_dist >= 60:
                    return f"{angular_dist / 60:.2f}' ({pixel_dist:.1f} px)"
                else:
                    return f'{angular_dist:.2f}" ({pixel_dist:.1f} px)'
            except Exception as e:
                print(f"[ERROR] WCS error: {e}")
                self.show_status_message(f"[Ruler] WCS error: {e}")
                return f"{pixel_dist:.1f} px (WCS error)"
        else:
            # Professional terminal output (pixel-only)
            print("\n" + "=" * 60)
            print("              LINE DISTANCE MEASUREMENT")
            print("=" * 60)
            print("  Coordinate System: Pixel")
            print("-" * 60)
            print(f"  {'Point':<12} {'X (px)':<15} {'Y (px)':<15}")
            print("-" * 60)
            print(f"  {'Start':<12} {x1:<15.2f} {y1:<15.2f}")
            print(f"  {'End':<12} {x2:<15.2f} {y2:<15.2f}")
            print("-" * 60)
            print(f"  {'ΔX':<20} {dx_px:>15.2f} px")
            print(f"  {'ΔY':<20} {dy_px:>15.2f} px")
            print("-" * 60)
            print(f"  {'Total Distance':<20} {pixel_dist:>15.2f} px")
            print("=" * 60 + "\n")

            return f"{pixel_dist:.1f} px"

    def _on_theme_change(self, theme):
        """Handle theme change - update icons"""
        from .styles import get_icon_path

        # Update ruler icon
        if hasattr(self, "ruler_action"):
            self.ruler_action.setIcon(
                QIcon(get_resource_path(f"assets/{get_icon_path('ruler.png')}"))
            )

        # Update profile icon
        if hasattr(self, "profile_action"):
            self.profile_action.setIcon(
                QIcon(get_resource_path(f"assets/{get_icon_path('profile.png')}"))
            )

        # Update RMS settings button icon
        if hasattr(self, "rms_settings_btn"):
            self.rms_settings_btn.setIcon(
                QIcon(get_resource_path(f"assets/{get_icon_path('settings.png')}"))
            )

        # Update beam settings button icon
        if hasattr(self, "beam_settings_button"):
            self.beam_settings_button.setIcon(
                QIcon(get_resource_path(f"assets/{get_icon_path('settings.png')}"))
            )

        # Update grid settings button icon
        if hasattr(self, "grid_settings_button"):
            self.grid_settings_button.setIcon(
                QIcon(get_resource_path(f"assets/{get_icon_path('settings.png')}"))
            )

        # Update gamma slider disabled styling for new theme
        if hasattr(self, "gamma_slider") and hasattr(self, "stretch_combo"):
            self.update_gamma_slider_state()

    def _toggle_tb_mode(self):
        """Toggle between flux (Jy/beam) and brightness temperature (K) view"""
        import tempfile
        import os
        from PyQt5.QtWidgets import QApplication

        if self.current_image_data is None:
            return

        # Check if we're viewing the TB/Flux temp file - only then revert
        # If we're viewing something else (e.g. HPC file), do fresh conversion
        is_viewing_temp = (
            hasattr(self, "_tb_temp_file")
            and self._tb_temp_file
            and self.imagename == self._tb_temp_file
        )

        if (
            is_viewing_temp
            and hasattr(self, "_tb_original_imagename")
            and self._tb_original_imagename
        ):
            self._revert_from_tb_mode()
            return

        # If we have stale TB state (not viewing temp file), clear it
        if hasattr(self, "_tb_original_imagename") and self._tb_original_imagename:
            # Clean up old temp file if exists
            if (
                hasattr(self, "_tb_temp_file")
                and self._tb_temp_file
                and os.path.exists(self._tb_temp_file)
            ):
                try:
                    os.remove(self._tb_temp_file)
                except:
                    pass
            self._tb_original_imagename = None
            self._tb_temp_file = None

        # Also clear stale HPC state when doing fresh conversion
        # This prevents issues when converting HPC K -> Jy/beam
        if hasattr(self, "_original_imagename") and self._original_imagename:
            # We're converting from an HPC file - clear HPC revert state
            if (
                hasattr(self, "_hpc_temp_file")
                and self._hpc_temp_file
                and self.imagename == self._hpc_temp_file
            ):
                # Currently viewing HPC temp - clear HPC state since we'll create new temp
                self._original_imagename = None
                # Update HPC button to reflect that we're now on an HPC-derived file
                # (revert not available since we're converting to new temp)
                if hasattr(self, "hpc_btn"):
                    self.hpc_btn.setText("HPC")
                    self.hpc_btn.setToolTip("Already in helioprojective coordinates")

        # Clear HPC->RA/Dec revert state when doing fresh conversion
        # This prevents trying to revert to a deleted flux temp file
        if hasattr(self, "_hpc_original_imagename") and self._hpc_original_imagename:
            # Only delete RA/Dec temp if we're NOT currently viewing it
            # (if we are viewing it, it will be the source for the new conversion)
            if hasattr(self, "_radec_temp_file") and self._radec_temp_file:
                if self.imagename != self._radec_temp_file:
                    # Safe to delete - not currently viewing this file
                    if os.path.exists(self._radec_temp_file):
                        try:
                            os.remove(self._radec_temp_file)
                        except:
                            pass
                self._radec_temp_file = None
            self._hpc_original_imagename = None

        # Determine which direction to convert based on current units
        current_bunit = getattr(self, "_current_bunit", "").strip().lower()
        is_kelvin = current_bunit == "k"
        is_jy_beam = "jy" in current_bunit and "beam" in current_bunit

        if not is_kelvin and not is_jy_beam:
            QMessageBox.warning(self, "Error", f"Cannot convert units: {current_bunit}")
            return

        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)

            if is_kelvin:
                # K -> Jy/beam (Flux conversion)
                self.show_status_message("Converting to flux (Jy/beam)...")
                QApplication.processEvents()

                from .utils import generate_flux_map

                # Store original imagename
                self._tb_original_imagename = self.imagename
                self._tb_original_unit = "K"

                # Create temp file
                temp_dir = tempfile.gettempdir()
                self._tb_temp_file = os.path.join(
                    temp_dir, f"solarviewer_flux_temp_{self._temp_file_id}.fits"
                )

                # Generate flux map - don't pass tb_data to avoid transpose issues
                flux_data, result = generate_flux_map(
                    self._tb_original_imagename, outfile=self._tb_temp_file
                )

                if flux_data is None:
                    QApplication.restoreOverrideCursor()
                    QMessageBox.warning(
                        self, "Error", f"Flux conversion failed: {result}"
                    )
                    self._tb_original_imagename = None
                    return

                self.show_status_message("Created temp flux file")

                # Load the flux FITS file
                self.imagename = self._tb_temp_file
                self._tb_mode = True
                self._current_bunit = "Jy/beam"

                # Reload and plot
                self.on_visualization_changed(dir_load=True)

                # Update button
                self.tb_btn.setText("TB")
                self.tb_btn.setToolTip("Revert to Brightness Temperature (K) view")
                self.tb_btn.setChecked(True)

                QApplication.restoreOverrideCursor()
                self.show_status_message("Showing flux (Jy/beam) - click TB to revert")

            else:
                # Jy/beam -> K (TB conversion)
                self.show_status_message("Converting to brightness temperature...")
                QApplication.processEvents()

                from .utils import generate_tb_map

                # Store original imagename
                self._tb_original_imagename = self.imagename
                self._tb_original_unit = "Jy/beam"

                # Create temp file for TB FITS
                temp_dir = tempfile.gettempdir()
                self._tb_temp_file = os.path.join(
                    temp_dir, f"solarviewer_tb_temp_{self._temp_file_id}.fits"
                )

                # Generate TB map using utils function - don't pass flux_data to avoid transpose issues
                tb_data, result = generate_tb_map(
                    self._tb_original_imagename, outfile=self._tb_temp_file
                )

                if tb_data is None:
                    QApplication.restoreOverrideCursor()
                    QMessageBox.warning(
                        self, "Error", f"TB conversion failed: {result}"
                    )
                    self._tb_original_imagename = None
                    return

                # Load the TB FITS file
                self.imagename = self._tb_temp_file
                self._tb_mode = True
                self._current_bunit = "K"

                # Reload and plot (use dir_load to avoid changing presets)
                self.on_visualization_changed(dir_load=True)

                # Update button
                self.tb_btn.setText("FLUX")
                self.tb_btn.setToolTip("Revert to Flux (Jy/beam) view")
                self.tb_btn.setChecked(True)

                QApplication.restoreOverrideCursor()
                self.show_status_message(
                    "Showing brightness temperature (K) - click FLUX to revert"
                )

        except Exception as e:
            import traceback

            traceback.print_exc()
            QApplication.restoreOverrideCursor()
            self._tb_original_imagename = None
            QMessageBox.critical(self, "Error", f"Conversion failed: {e}")

    def _revert_from_tb_mode(self):
        """Revert from converted mode to original image"""
        import os
        from PyQt5.QtWidgets import QApplication, QMessageBox

        # Store original unit before clearing
        original_unit = self._tb_original_unit or "Jy/beam"

        # Check if original file still exists
        if not self._tb_original_imagename:
            QMessageBox.warning(self, "Error", "No original image to revert to")
            return

        if not os.path.exists(self._tb_original_imagename):
            QMessageBox.warning(
                self,
                "Error",
                f"Original file no longer exists:\n{self._tb_original_imagename}",
            )
            self._tb_original_imagename = None
            self._tb_temp_file = None
            self._tb_mode = False
            self.tb_btn.setChecked(False)
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        self.show_status_message(f"Reverting to original ({original_unit})...")

        # Restore original imagename
        self.imagename = self._tb_original_imagename
        self._tb_original_imagename = None
        self._tb_original_unit = None
        self._tb_mode = False

        # Reload original image
        self.on_visualization_changed(dir_load=True)

        # Clean up temp file
        if (
            hasattr(self, "_tb_temp_file")
            and self._tb_temp_file
            and os.path.exists(self._tb_temp_file)
        ):
            try:
                os.remove(self._tb_temp_file)
            except:
                pass
            self._tb_temp_file = None

        # Update button based on original unit
        if original_unit == "K":
            self.tb_btn.setText("FLUX")
            self.tb_btn.setToolTip("Convert to Flux (Jy/beam) view")
            self.show_status_message("Showing brightness temperature (K)")
        else:
            self.tb_btn.setText("TB")
            self.tb_btn.setToolTip("Convert to Brightness Temperature (K)")
            self.show_status_message("Showing flux (Jy/beam)")

        self.tb_btn.setChecked(False)

        QApplication.restoreOverrideCursor()

    def _toggle_profile_mode(self, checked):
        """Toggle profile mode for flux profile cut"""
        self._profile_mode = checked
        if checked:
            # Show mode selection dialog
            self._show_profile_mode_dialog()
        else:
            self.show_status_message("Profile mode off")
            # Reconnect ROI selector
            if self.roi_selector:
                self.roi_selector.set_active(True)
            # Disconnect all profile events
            for attr in ["_profile_click_cid", "_profile_motion_cid"]:
                if hasattr(self, attr):
                    try:
                        self.canvas.mpl_disconnect(getattr(self, attr))
                    except:
                        pass
            # Clear profile graphics including center marker
            self._clear_profile()
            self._clear_crosshairs()
            # Also clear center marker
            if (
                hasattr(self, "_profile_center_marker")
                and self._profile_center_marker is not None
            ):
                try:
                    self._profile_center_marker.remove()
                except:
                    pass
                self._profile_center_marker = None
            # Clear preview line if any
            if (
                hasattr(self, "_profile_preview_line")
                and self._profile_preview_line is not None
            ):
                try:
                    self._profile_preview_line.remove()
                except:
                    pass
                self._profile_preview_line = None
            self.canvas.draw_idle()

    def _show_profile_mode_dialog(self):
        """Show dialog to select profile method"""
        from PyQt5.QtWidgets import (
            QDialog,
            QVBoxLayout,
            QHBoxLayout,
            QRadioButton,
            QPushButton,
            QLabel,
            QButtonGroup,
        )

        dialog = QDialog(self)
        dialog.setWindowTitle("Profile Cut Method")
        dialog.resize(300, 150)
        layout = QVBoxLayout(dialog)

        layout.addWidget(QLabel("Select profile cut method:"))

        # Radio buttons for method selection
        btn_group = QButtonGroup(dialog)

        line_radio = QRadioButton(
            "Line Mode - Click two points (hold Shift to snap H/V)"
        )
        line_radio.setChecked(self._profile_method == "line")
        btn_group.addButton(line_radio)
        layout.addWidget(line_radio)

        radial_radio = QRadioButton(
            "Radial Mode - Click center, set angle (plots both directions)"
        )
        radial_radio.setChecked(self._profile_method == "radial")
        btn_group.addButton(radial_radio)
        layout.addWidget(radial_radio)

        # Buttons
        btn_layout = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

        def on_ok():
            self._profile_method = "line" if line_radio.isChecked() else "radial"
            dialog.accept()
            self._activate_profile_mode()

        def on_cancel():
            dialog.reject()
            # Uncheck the profile action
            self.profile_action.setChecked(False)
            self._profile_mode = False

        ok_btn.clicked.connect(on_ok)
        cancel_btn.clicked.connect(on_cancel)
        dialog.rejected.connect(on_cancel)

        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

    def _activate_profile_mode(self):
        """Activate profile mode with selected method"""
        # Disconnect ROI selector
        if self.roi_selector:
            self.roi_selector.set_active(False)

        if self._profile_method == "line":
            self.show_status_message(
                "Line Profile: Click first point (Shift to snap H/V)"
            )
            # Connect click and motion events for line mode
            self._profile_click_cid = self.canvas.mpl_connect(
                "button_press_event", self._profile_line_on_click
            )
            self._profile_motion_cid = self.canvas.mpl_connect(
                "motion_notify_event", self._profile_line_on_motion
            )
        else:  # radial
            self.show_status_message("Radial Profile: Click center point")
            self._profile_click_cid = self.canvas.mpl_connect(
                "button_press_event", self._profile_radial_on_click
            )

    def _clear_profile(self):
        """Clear profile line from plot"""
        if self._profile_line is not None:
            try:
                self._profile_line.remove()
            except:
                pass
            self._profile_line = None
        self._profile_start = None
        self.canvas.draw_idle()

    def _clear_crosshairs(self):
        """Clear crosshair overlays"""
        for line in self._profile_crosshairs:
            try:
                line.remove()
            except:
                pass
        self._profile_crosshairs = []
        if self._profile_preview_line is not None:
            try:
                self._profile_preview_line.remove()
            except:
                pass
            self._profile_preview_line = None

    def _profile_line_on_click(self, event):
        """Handle click for line profile mode"""
        if event.inaxes is None or event.button != 1:
            return

        from PyQt5.QtWidgets import QApplication

        modifiers = QApplication.keyboardModifiers()
        shift_held = bool(modifiers & Qt.ShiftModifier)

        if self._profile_start is None:
            # First click - set start point
            self._clear_profile()
            self._profile_start = (event.xdata, event.ydata)

            ax = event.inaxes
            (self._profile_line,) = ax.plot(
                [event.xdata], [event.ydata], "go", markersize=8
            )
            self.canvas.draw_idle()

            self.show_status_message(
                f"Start: ({event.xdata:.1f}, {event.ydata:.1f}) - move mouse, click to set end (Shift for snap)"
            )
        else:
            # Second click - calculate end point (with shift snap if held)
            x1, y1 = self._profile_start
            x2, y2 = event.xdata, event.ydata

            if shift_held:
                x2, y2 = self._snap_to_axis(x1, y1, x2, y2)

            # Clear preview line
            if self._profile_preview_line is not None:
                self._profile_preview_line.remove()
                self._profile_preview_line = None

            # Show coordinate editor dialog
            self._show_profile_coordinate_dialog(x1, y1, x2, y2)

    def _profile_line_on_motion(self, event):
        """Handle mouse motion for live preview line"""
        if self._profile_start is None or event.inaxes is None:
            return

        from PyQt5.QtWidgets import QApplication

        modifiers = QApplication.keyboardModifiers()
        shift_held = bool(modifiers & Qt.ShiftModifier)

        x1, y1 = self._profile_start
        x2, y2 = event.xdata, event.ydata

        # Apply shift snap if held
        if shift_held:
            x2, y2 = self._snap_to_axis(x1, y1, x2, y2)

        ax = event.inaxes

        # Update or create preview line
        if self._profile_preview_line is None:
            (self._profile_preview_line,) = ax.plot(
                [x1, x2], [y1, y2], "g--", linewidth=1.5, alpha=0.7
            )
        else:
            self._profile_preview_line.set_data([x1, x2], [y1, y2])

        # Calculate and show distance
        pixel_dist = np.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        self.show_status_message(
            f"Distance: {pixel_dist:.1f} px | Shift for H/V snap | Click to confirm"
        )

        self.canvas.draw_idle()

    def _snap_to_axis(self, x1, y1, x2, y2):
        """Snap end point to horizontal or vertical based on which is closer"""
        dx = abs(x2 - x1)
        dy = abs(y2 - y1)
        if dx > dy:
            # Snap to horizontal
            return x2, y1
        else:
            # Snap to vertical
            return x1, y2

    def _profile_radial_on_click(self, event):
        """Handle click for radial profile mode - click center, then show dialog"""
        if event.inaxes is None or event.button != 1:
            return

        cx, cy = event.xdata, event.ydata

        # Draw center marker
        self._clear_profile()
        ax = event.inaxes
        (self._profile_line,) = ax.plot([cx], [cy], "r*", markersize=12)
        self.canvas.draw_idle()

        # Show radial input dialog
        self._show_radial_profile_dialog(cx, cy)

    def _show_profile_coordinate_dialog(self, x1, y1, x2, y2):
        """Show dialog to edit/confirm profile cut coordinates"""
        dialog = QDialog(self)
        dialog.setWindowTitle("Profile Cut Coordinates")
        dialog.resize(400, 200)
        layout = QVBoxLayout(dialog)

        # Instructions
        layout.addWidget(
            QLabel("Edit coordinates if needed, then click 'Extract Profile':")
        )

        # Coordinate inputs
        coord_layout = QGridLayout()

        coord_layout.addWidget(QLabel("Start X:"), 0, 0)
        x1_spin = QDoubleSpinBox()
        x1_spin.setRange(0, 10000)
        x1_spin.setDecimals(1)
        x1_spin.setValue(x1)
        coord_layout.addWidget(x1_spin, 0, 1)

        coord_layout.addWidget(QLabel("Start Y:"), 0, 2)
        y1_spin = QDoubleSpinBox()
        y1_spin.setRange(0, 10000)
        y1_spin.setDecimals(1)
        y1_spin.setValue(y1)
        coord_layout.addWidget(y1_spin, 0, 3)

        coord_layout.addWidget(QLabel("End X:"), 1, 0)
        x2_spin = QDoubleSpinBox()
        x2_spin.setRange(0, 10000)
        x2_spin.setDecimals(1)
        x2_spin.setValue(x2)
        coord_layout.addWidget(x2_spin, 1, 1)

        coord_layout.addWidget(QLabel("End Y:"), 1, 2)
        y2_spin = QDoubleSpinBox()
        y2_spin.setRange(0, 10000)
        y2_spin.setDecimals(1)
        y2_spin.setValue(y2)
        coord_layout.addWidget(y2_spin, 1, 3)

        layout.addLayout(coord_layout)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)

        extract_btn = QPushButton("Extract Profile")
        extract_btn.setDefault(True)
        btn_layout.addWidget(extract_btn)

        layout.addLayout(btn_layout)

        def do_extract():
            dialog.accept()
            # Get final coordinates
            fx1, fy1 = x1_spin.value(), y1_spin.value()
            fx2, fy2 = x2_spin.value(), y2_spin.value()

            # Draw final line on plot
            ax = self.figure.axes[0] if self.figure.axes else None
            if ax:
                if self._profile_line is not None:
                    self._profile_line.remove()
                (self._profile_line,) = ax.plot(
                    [fx1, fx2], [fy1, fy2], "g-", linewidth=2, marker="o", markersize=8
                )
                self.canvas.draw_idle()

            # Extract and show profile
            self._extract_and_show_profile(fx1, fy1, fx2, fy2)

        extract_btn.clicked.connect(do_extract)

        # Reset for next measurement
        self._profile_start = None

        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

    def _show_radial_profile_dialog(self, cx, cy):
        """Show dialog for radial profile - set angle and length from center"""
        dialog = QDialog(self)
        dialog.setWindowTitle("Radial Profile Settings")
        dialog.resize(450, 300)
        layout = QVBoxLayout(dialog)

        # Center coordinates (editable)
        center_layout = QHBoxLayout()
        center_layout.addWidget(QLabel("Center:"))
        cx_spin = QDoubleSpinBox()
        cx_spin.setRange(0, 10000)
        cx_spin.setDecimals(1)
        cx_spin.setValue(cx)
        cx_spin.setPrefix("X: ")
        center_layout.addWidget(cx_spin)
        cy_spin = QDoubleSpinBox()
        cy_spin.setRange(0, 10000)
        cy_spin.setDecimals(1)
        cy_spin.setValue(cy)
        cy_spin.setPrefix("Y: ")
        center_layout.addWidget(cy_spin)
        layout.addLayout(center_layout)

        # Angle input
        angle_layout = QHBoxLayout()
        angle_layout.addWidget(QLabel("Angle (deg):"))
        angle_spin = QDoubleSpinBox()
        angle_spin.setRange(0, 180)
        angle_spin.setDecimals(1)
        angle_spin.setValue(0)
        angle_spin.setToolTip(
            "0° = East-West, 90° = North-South (profile extends both ways from center)"
        )
        angle_layout.addWidget(angle_spin)
        layout.addLayout(angle_layout)

        # Quick angle buttons
        quick_layout = QHBoxLayout()
        quick_layout.addWidget(QLabel("Quick:"))
        for angle in [0, 45, 90, 135, 180]:
            btn = QPushButton(f"{angle}°")
            btn.setFixedWidth(45)
            btn.clicked.connect(lambda checked, a=angle: angle_spin.setValue(a))
            quick_layout.addWidget(btn)
        quick_layout.addStretch()
        layout.addLayout(quick_layout)

        # Length input
        length_layout = QHBoxLayout()
        length_layout.addWidget(QLabel("Length (pixels):"))
        length_spin = QDoubleSpinBox()
        length_spin.setRange(1, 5000)
        length_spin.setDecimals(1)
        length_spin.setValue(100)
        length_layout.addWidget(length_spin)
        layout.addLayout(length_layout)

        # Explanatory note
        note_label = QLabel("ℹ️ Length extends ±{} pixels from center (total {} pixels)")
        note_label.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(note_label)

        def update_note():
            L = length_spin.value()
            note_label.setText(
                f"ℹ️ Length extends ±{L:.0f} pixels from center (total {2 * L:.0f} pixels)"
            )

        length_spin.valueChanged.connect(update_note)
        update_note()

        # Preview update function (always shows preview)
        _updating_preview = [False]  # Use list to allow modification in nested function

        def update_preview():
            if _updating_preview[0]:
                return  # Prevent recursion
            _updating_preview[0] = True
            try:
                ax = self.figure.axes[0] if self.figure.axes else None
                if not ax:
                    return

                c_x, c_y = cx_spin.value(), cy_spin.value()
                angle = np.radians(angle_spin.value())
                length = length_spin.value()

                # Calculate BOTH endpoints (bidirectional from center)
                x1 = c_x - length * np.cos(angle)
                y1 = c_y - length * np.sin(angle)
                x2 = c_x + length * np.cos(angle)
                y2 = c_y + length * np.sin(angle)

                # Update line on plot
                if self._profile_preview_line is not None:
                    try:
                        self._profile_preview_line.remove()
                    except:
                        pass
                (self._profile_preview_line,) = ax.plot(
                    [x1, x2], [y1, y2], "r--", linewidth=1.5, alpha=0.7
                )
                # Mark center
                if (
                    hasattr(self, "_profile_center_marker")
                    and self._profile_center_marker is not None
                ):
                    try:
                        self._profile_center_marker.remove()
                    except:
                        pass
                (self._profile_center_marker,) = ax.plot(
                    [c_x], [c_y], "r*", markersize=12
                )
                self.canvas.draw_idle()
            finally:
                _updating_preview[0] = False

        # Connect spinboxes to update preview
        angle_spin.valueChanged.connect(update_preview)
        length_spin.valueChanged.connect(update_preview)
        cx_spin.valueChanged.connect(update_preview)
        cy_spin.valueChanged.connect(update_preview)

        # Initial preview
        update_preview()

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)

        extract_btn = QPushButton("Extract Profile")
        extract_btn.setDefault(True)
        btn_layout.addWidget(extract_btn)
        layout.addLayout(btn_layout)

        def do_extract():
            dialog.accept()
            c_x, c_y = cx_spin.value(), cy_spin.value()
            angle = np.radians(angle_spin.value())
            length = length_spin.value()

            # Calculate BOTH endpoints (bidirectional from center)
            x1 = c_x - length * np.cos(angle)
            y1 = c_y - length * np.sin(angle)
            x2 = c_x + length * np.cos(angle)
            y2 = c_y + length * np.sin(angle)

            # Clear preview line only (keep center marker)
            if self._profile_preview_line is not None:
                try:
                    self._profile_preview_line.remove()
                except:
                    pass
                self._profile_preview_line = None

            ax = self.figure.axes[0] if self.figure.axes else None
            if ax:
                if self._profile_line is not None:
                    try:
                        self._profile_line.remove()
                    except:
                        pass
                # Draw final line AND redraw center marker
                (self._profile_line,) = ax.plot(
                    [x1, x2], [y1, y2], "r-", linewidth=2, marker="o", markersize=8
                )
                # Redraw center marker on top
                if (
                    hasattr(self, "_profile_center_marker")
                    and self._profile_center_marker is not None
                ):
                    try:
                        self._profile_center_marker.remove()
                    except:
                        pass
                (self._profile_center_marker,) = ax.plot(
                    [c_x], [c_y], "r*", markersize=14, zorder=10
                )
                self.canvas.draw_idle()

            # Extract profile (radial mode - center at 0)
            self._extract_and_show_profile(x1, y1, x2, y2, is_radial=True)

        extract_btn.clicked.connect(do_extract)

        def on_reject():
            # Clear preview line on cancel
            if self._profile_preview_line is not None:
                try:
                    self._profile_preview_line.remove()
                except:
                    pass
                self._profile_preview_line = None
            if (
                hasattr(self, "_profile_center_marker")
                and self._profile_center_marker is not None
            ):
                try:
                    self._profile_center_marker.remove()
                except:
                    pass
                self._profile_center_marker = None
            self._clear_profile()
            self.canvas.draw_idle()

        dialog.rejected.connect(on_reject)

        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

    def _extract_and_show_profile(self, x1, y1, x2, y2, is_radial=False):
        """Extract flux profile along line and show dialog

        Args:
            is_radial: If True, distances are centered at 0 (from -half to +half)
        """
        from scipy.ndimage import map_coordinates

        if self.current_image_data is None:
            QMessageBox.warning(self, "No Data", "No image data loaded")
            return

        # Calculate number of points along the line
        pixel_dist = np.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        num_points = max(int(np.ceil(pixel_dist)), 2)

        # Create coordinates along the line
        x_coords = np.linspace(x1, x2, num_points)
        y_coords = np.linspace(y1, y2, num_points)

        # Extract profile using bilinear interpolation
        # Note: map_coordinates expects (row, col) = (y, x) for image data
        profile = map_coordinates(
            self.current_image_data, [x_coords, y_coords], order=1
        )

        # Calculate distance array
        if self.current_wcs:
            try:
                increment = self.current_wcs.increment()["numeric"][0:2]
                scale = abs(increment[0]) * 180 / np.pi * 3600  # arcsec/pixel

                if is_radial:
                    # Radial mode: center at 0, from -half_dist to +half_dist
                    half_dist = (pixel_dist * scale) / 2
                    distances = np.linspace(-half_dist, half_dist, num_points)
                else:
                    # Line mode: from 0 to end
                    distances = np.linspace(0, pixel_dist * scale, num_points)
                dist_unit = "arcsec"

                # Get WCS world coordinates for secondary axis
                # Convert pixel coords to world coords
                world_coords = []
                for px, py in zip(x_coords, y_coords):
                    world = self.current_wcs.toworld([px, py, 0, 0])["numeric"]
                    # world[0] and world[1] are RA, Dec in radians
                    ra_deg = world[0] * 180 / np.pi
                    dec_deg = world[1] * 180 / np.pi
                    world_coords.append((ra_deg, dec_deg))
                world_coords = np.array(world_coords)
                wcs_available = True
            except Exception as e:
                self.show_status_message(f"WCS conversion error: {e}")
                if is_radial:
                    half_dist = pixel_dist / 2
                    distances = np.linspace(-half_dist, half_dist, num_points)
                else:
                    distances = np.linspace(0, pixel_dist, num_points)
                dist_unit = "pixels"
                world_coords = None
                wcs_available = False
        else:
            if is_radial:
                half_dist = pixel_dist / 2
                distances = np.linspace(-half_dist, half_dist, num_points)
            else:
                distances = np.linspace(0, pixel_dist, num_points)
            dist_unit = "pixels"
            world_coords = None
            wcs_available = False

        self.show_status_message(
            f"Extracted {num_points} points from ({x1:.1f}, {y1:.1f}) to ({x2:.1f}, {y2:.1f})"
        )
        self.show_status_message(
            f"Flux range: {np.nanmin(profile):.4g} to {np.nanmax(profile):.4g}"
        )

        # Show profile dialog with dual x-axis (distance + WCS)
        self._show_profile_plot_dialog(
            profile, distances, dist_unit, world_coords, wcs_available, x1, y1, x2, y2
        )

    def _show_profile_plot_dialog(
        self, profile, distances, dist_unit, world_coords, wcs_available, x1, y1, x2, y2
    ):
        """Show dialog with profile plot - single plot with dual x-axes (distance + WCS)"""
        dialog = QDialog(self)
        dialog.setWindowTitle("Flux Profile")
        dialog.resize(800, 550)
        dialog.setMinimumSize(600, 400)
        layout = QVBoxLayout(dialog)

        # Create figure with single plot
        fig = Figure(figsize=(8, 5))
        canvas = FigureCanvas(fig)
        layout.addWidget(canvas, stretch=1)

        # Main plot with distance on bottom x-axis
        ax = fig.add_subplot(111)
        ax.plot(distances, profile, "b-", linewidth=1.5)
        ax.fill_between(distances, profile, alpha=0.3)
        ax.set_xlabel(f"Distance from start ({dist_unit})")
        ax.set_ylabel("Flux")
        ax.set_title(f"Profile from ({x1:.1f}, {y1:.1f}) to ({x2:.1f}, {y2:.1f})")
        ax.grid(True, alpha=0.3)

        # Calculate FWHM (only for suitable single-peak profiles)
        fwhm_value = None
        fwhm_left = None
        fwhm_right = None
        show_fwhm = False

        try:
            # Find max and baseline
            max_val = np.nanmax(profile)
            min_val = np.nanmin(profile)
            half_max = (max_val + min_val) / 2
            max_idx = np.nanargmax(profile)

            # Validation: Check if profile is suitable for FWHM
            # 1. Peak must be prominent (at least 20% above min)
            prominence = (max_val - min_val) / (abs(min_val) + 1e-10)
            if prominence < 0.2:
                raise ValueError("Peak not prominent enough")

            # 2. Peak should not be at the edges
            if max_idx < len(profile) * 0.05 or max_idx > len(profile) * 0.95:
                raise ValueError("Peak too close to edge")

            # 3. Count number of crossings through half-max (should be exactly 2 for FWHM)
            above_half = profile > half_max
            crossings = np.sum(np.diff(above_half.astype(int)) != 0)
            if crossings != 2:
                raise ValueError(f"Multiple peaks detected ({crossings} crossings)")

            # 4. Check monotonic decrease from peak to half-max on both sides
            # Left side: profile should generally decrease from max to left crossing
            left_half = profile[:max_idx]
            if len(left_half) > 3:
                # Check if left side is mostly increasing toward peak
                left_diffs = np.diff(left_half)
                if np.sum(left_diffs < 0) > len(left_diffs) * 0.5:
                    raise ValueError("Non-monotonic left side")

            # Right side: profile should generally decrease from max to right crossing
            right_half = profile[max_idx:]
            if len(right_half) > 3:
                # Check if right side is mostly decreasing from peak
                right_diffs = np.diff(right_half)
                if np.sum(right_diffs > 0) > len(right_diffs) * 0.5:
                    raise ValueError("Non-monotonic right side")

            # Find left crossing point (from max going left)
            left_indices = np.where(profile[:max_idx] <= half_max)[0]
            if len(left_indices) > 0:
                left_idx = left_indices[-1]
                # Linear interpolation for better accuracy
                if left_idx + 1 < len(profile):
                    denom = profile[left_idx + 1] - profile[left_idx]
                    if abs(denom) > 1e-10:
                        frac = (half_max - profile[left_idx]) / denom
                        fwhm_left = distances[left_idx] + frac * (
                            distances[left_idx + 1] - distances[left_idx]
                        )
                    else:
                        fwhm_left = distances[left_idx]
                else:
                    fwhm_left = distances[left_idx]

            # Find right crossing point (from max going right)
            right_indices = np.where(profile[max_idx:] <= half_max)[0]
            if len(right_indices) > 0:
                right_idx = max_idx + right_indices[0]
                # Linear interpolation for better accuracy
                if right_idx > 0:
                    denom = profile[right_idx] - profile[right_idx - 1]
                    if abs(denom) > 1e-10:
                        frac = (half_max - profile[right_idx - 1]) / denom
                        fwhm_right = distances[right_idx - 1] + frac * (
                            distances[right_idx] - distances[right_idx - 1]
                        )
                    else:
                        fwhm_right = distances[right_idx]
                else:
                    fwhm_right = distances[right_idx]

            # Calculate FWHM if both crossing points found
            if fwhm_left is not None and fwhm_right is not None:
                fwhm_value = abs(fwhm_right - fwhm_left)
                show_fwhm = True

                # Draw FWHM visualization
                ax.axhline(
                    y=half_max,
                    color="red",
                    linestyle="--",
                    alpha=0.7,
                    label=f"Half Max = {half_max:.4g}",
                )
                ax.plot(
                    [fwhm_left, fwhm_right],
                    [half_max, half_max],
                    "r-",
                    linewidth=2.5,
                    label=f"FWHM = {fwhm_value:.3f} {dist_unit}",
                )
                ax.plot(
                    [fwhm_left, fwhm_right], [half_max, half_max], "ro", markersize=8
                )

                # Add vertical lines at FWHM boundaries
                ax.axvline(x=fwhm_left, color="red", linestyle=":", alpha=0.5)
                ax.axvline(x=fwhm_right, color="red", linestyle=":", alpha=0.5)

                ax.legend(loc="best", fontsize=9)

                # Print to terminal professionally
                print("\n" + "=" * 50)
                print("         FLUX PROFILE FWHM MEASUREMENT")
                print("=" * 50)
                print(f"  {'Max Value':<20} {max_val:>15.6g}")
                print(f"  {'Min Value':<20} {min_val:>15.6g}")
                print(f"  {'Half Maximum':<20} {half_max:>15.6g}")
                print(f"  {'Left Position':<20} {fwhm_left:>15.3f} {dist_unit}")
                print(f"  {'Right Position':<20} {fwhm_right:>15.3f} {dist_unit}")
                print(f"  {'FWHM':<20} {fwhm_value:>15.3f} {dist_unit}")
                print("=" * 50 + "\n")
        except ValueError:
            # Profile not suitable for FWHM - silently skip
            pass
        except Exception:
            # Other errors - silently skip
            pass

        # Add secondary x-axis at top with WCS coordinates
        if wcs_available and world_coords is not None:
            ax2 = ax.twiny()  # Create twin axis sharing y-axis

            # Use RA or Dec based on which varies more
            ra_range = abs(world_coords[-1, 0] - world_coords[0, 0])
            dec_range = abs(world_coords[-1, 1] - world_coords[0, 1])

            if ra_range >= dec_range:
                wcs_vals = world_coords[:, 0]  # RA in degrees
                # Format as hours:min:sec for RA
                ax2.set_xlabel("RA (deg)")
            else:
                wcs_vals = world_coords[:, 1]  # Dec in degrees
                ax2.set_xlabel("Dec (deg)")

            # Set the same data limits as main axis but mapped to WCS values
            ax2.set_xlim(wcs_vals[0], wcs_vals[-1])

        fig.tight_layout()

        # Statistics with FWHM
        fwhm_str = f"FWHM: {fwhm_value:.3f} {dist_unit}" if fwhm_value else "FWHM: N/A"
        stats_text = (
            f"Min: {np.nanmin(profile):.6g}  |  Max: {np.nanmax(profile):.6g}  |  "
            f"Mean: {np.nanmean(profile):.6g}  |  Std: {np.nanstd(profile):.6g}  |  "
            f"{fwhm_str}  |  Points: {len(profile)}"
        )
        stats_label = QLabel(stats_text)
        stats_label.setStyleSheet("font-family: monospace; padding: 5px;")
        stats_label.setWordWrap(True)
        layout.addWidget(stats_label)

        # Buttons
        btn_layout = QHBoxLayout()

        # Save CSV button
        save_btn = QPushButton("Save CSV")

        def save_csv():
            from PyQt5.QtWidgets import QFileDialog

            filepath, _ = QFileDialog.getSaveFileName(
                dialog, "Save Profile", "", "CSV Files (*.csv)"
            )
            if filepath:
                with open(filepath, "w") as f:
                    f.write(
                        f"# Profile from ({x1:.1f}, {y1:.1f}) to ({x2:.1f}, {y2:.1f})\n"
                    )
                    f.write(f"Distance_{dist_unit},Flux\n")
                    for d, p in zip(distances, profile):
                        f.write(f"{d},{p}\n")
                self.show_status_message(f"Profile saved to {filepath}")

        save_btn.clicked.connect(save_csv)
        btn_layout.addWidget(save_btn)

        btn_layout.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

    def show_image_stats(self, rms_box=None):
        if self.current_image_data is None:
            return

        # Use the current RMS box if none is provided
        if rms_box is None:
            rms_box = self.current_rms_box

        data = self.current_image_data
        dmax = float(np.nanmax(data))
        dmin = float(np.nanmin(data))
        drms = np.sqrt(
            np.nanmean(data[rms_box[0] : rms_box[1], rms_box[2] : rms_box[3]] ** 2)
        )
        dmean_rms_box = np.nanmean(
            data[rms_box[0] : rms_box[1], rms_box[2] : rms_box[3]]
        )
        # Avoid divide-by-zero for splash images or uniform data
        if drms > 1e-10:
            positive_DR = dmax / drms
            negative_DR = dmin / drms
        else:
            positive_DR = np.nan
            negative_DR = np.nan

        # Update the image stats table
        stats_values = [dmax, dmin, drms, dmean_rms_box, positive_DR, negative_DR]
        for i, val in enumerate(stats_values):
            self.image_stats_table.setItem(i, 1, QTableWidgetItem(f"{val:.6g}"))

        # Update the RMS box info in the label
        h, w = data.shape
        rms_box_percent = (
            (rms_box[1] - rms_box[0]) * (rms_box[3] - rms_box[2]) / (h * w)
        ) * 100
        self.image_info_label.setText(
            f"Image: {h}x{w} px - RMS box: {rms_box_percent:.1f}%"
        )

        return dmax, dmin, drms, dmean_rms_box, positive_DR, negative_DR

    def set_region_mode(self, mode_id):
        self.region_mode = mode_id
        # Disable ruler mode when switching to ROI selection
        if hasattr(self, "_ruler_mode") and self._ruler_mode:
            self._toggle_ruler_mode(False)
            if hasattr(self, "ruler_action"):
                self.ruler_action.setChecked(False)
        # Re-init region editor if we have an axis
        if hasattr(self, "figure") and self.figure.axes:
            self.init_region_editor(self.figure.axes[0])

    def _toggle_roi_mode(self):
        """Toggle between Rectangle and Ellipse ROI selection modes"""
        if not hasattr(self, "region_mode") or self.region_mode == RegionMode.RECTANGLE:
            self.region_mode = RegionMode.ELLIPSE
            self.roi_mode_btn.setText("⬭")  # Ellipse icon
            self.roi_mode_btn.setToolTip("ROI Mode: Ellipse (click to toggle)")
            self.show_status_message("ROI Mode: Ellipse")
        else:
            self.region_mode = RegionMode.RECTANGLE
            self.roi_mode_btn.setText("▭")  # Rectangle icon
            self.roi_mode_btn.setToolTip("ROI Mode: Rectangle (click to toggle)")
            self.show_status_message("ROI Mode: Rectangle")

        # Re-initialize region editor with new mode
        if hasattr(self, "figure") and self.figure.axes:
            self.init_region_editor(self.figure.axes[0])

    def _load_fits_stokes(self, imagename, stokes, rms_box=(0, 200, 0, 130)):
        """
        Load a specific Stokes parameter (or derived parameter) from a FITS file.
        Handles FITS files with Stokes axis (like HPC converted files).
        """
        from astropy.io import fits

        hdul = fits.open(imagename, memmap=True)
        data = hdul[0].data
        header = hdul[0].header
        hdul.close()

        # Find Stokes axis
        ndim = header.get("NAXIS", 0)
        stokes_axis = None
        num_stokes = 1

        for i in range(1, ndim + 1):
            ctype = header.get(f"CTYPE{i}", "").upper()
            if ctype == "STOKES":
                stokes_axis = i
                num_stokes = header.get(f"NAXIS{i}", 1)
                break

        # Map Stokes names to indices
        stokes_map = {"I": 0, "Q": 1, "U": 2, "V": 3}

        # Handle different data shapes
        if data.ndim == 2:
            # 2D data - just return it for I, error for others
            if stokes in ["I", "Q", "U", "V"]:
                if stokes == "I":
                    return data
                else:
                    raise RuntimeError(f"No Stokes {stokes} available in 2D image")
            else:
                raise RuntimeError(f"Cannot calculate {stokes} from 2D image")

        # 3D data with Stokes axis
        if data.ndim == 3 and num_stokes > 1:
            # Stokes is first axis (axis 0 in numpy, axis 3 in FITS)
            def get_stokes_slice(s):
                idx = stokes_map.get(s)
                if idx is None or idx >= num_stokes:
                    raise RuntimeError(
                        f"Stokes {s} not available (only {num_stokes} Stokes)"
                    )
                return data[idx]

            if stokes in ["I", "Q", "U", "V"]:
                return get_stokes_slice(stokes).T  # Transpose to match CASA orientation

            # Calculate derived parameters
            if stokes == "L":
                Q = get_stokes_slice("Q")
                U = get_stokes_slice("U")
                return np.sqrt(Q**2 + U**2).T  # Transpose to match CASA orientation

            elif stokes == "Lfrac":
                I = get_stokes_slice("I")
                Q = get_stokes_slice("Q")
                U = get_stokes_slice("U")
                L = np.sqrt(Q**2 + U**2)
                # Calculate RMS for thresholding
                try:
                    rms_region = I[rms_box[0] : rms_box[1], rms_box[2] : rms_box[3]]
                    rms = np.sqrt(np.nanmean(rms_region**2))
                except Exception:
                    print(
                        "[WARNING] Failed to estimate RMS, using standard deviation instead"
                    )
                    rms = np.nanstd(I)
                # Mask low signal
                mask = np.abs(I) < 3 * rms
                result = L / np.abs(I)
                result[mask] = np.nan
                return result.T  # Transpose to match CASA orientation

            elif stokes == "Vfrac":
                I = get_stokes_slice("I")
                V = get_stokes_slice("V")
                try:
                    rms_region = I[rms_box[0] : rms_box[1], rms_box[2] : rms_box[3]]
                    rms = np.sqrt(np.nanmean(rms_region**2))
                except Exception:
                    print(
                        "[WARNING] Failed to estimate RMS, using standard deviation instead"
                    )
                    rms = np.nanstd(I)
                mask = np.abs(I) < 3 * rms
                result = V / np.abs(I)
                result[mask] = np.nan
                return result.T  # Transpose to match CASA orientation

            elif stokes == "Q/I":
                I = get_stokes_slice("I")
                Q = get_stokes_slice("Q")
                try:
                    rms_region = I[rms_box[0] : rms_box[1], rms_box[2] : rms_box[3]]
                    rms = np.sqrt(np.nanmean(rms_region**2))
                except Exception:
                    print(
                        "[WARNING] Failed to estimate RMS, using standard deviation instead"
                    )
                    rms = np.nanstd(I)
                mask = np.abs(I) < 3 * rms
                result = Q / np.abs(I)
                result[mask] = np.nan
                return result.T  # Transpose to match CASA orientation

            elif stokes == "U/I":
                I = get_stokes_slice("I")
                U = get_stokes_slice("U")
                try:
                    rms_region = I[rms_box[0] : rms_box[1], rms_box[2] : rms_box[3]]
                    rms = np.sqrt(np.nanmean(rms_region**2))
                except Exception:
                    print(
                        "[WARNING] Failed to estimate RMS, using standard deviation instead"
                    )
                    rms = np.nanstd(I)
                mask = np.abs(I) < 3 * rms
                result = U / np.abs(I)
                result[mask] = np.nan
                return result.T  # Transpose to match CASA orientation

            elif stokes == "PANG":
                Q = get_stokes_slice("Q")
                U = get_stokes_slice("U")
                # Polarization angle in degrees
                return (
                    0.5 * np.degrees(np.arctan2(U, Q))
                ).T  # Transpose to match CASA orientation

            else:
                raise RuntimeError(f"Unknown Stokes parameter: {stokes}")

        # 4D data (Stokes, Freq, DEC, RA) - take first frequency
        if data.ndim == 4:
            data_3d = data[:, 0, :, :]  # Take first frequency

            # Recursively handle as 3D
            # Re-create temporary FITS in memory - or just handle directly
            def get_stokes_4d(s):
                idx = stokes_map.get(s)
                if idx is None or idx >= data.shape[0]:
                    raise RuntimeError(f"Stokes {s} not available")
                return data[idx, 0, :, :]

            if stokes in ["I", "Q", "U", "V"]:
                return get_stokes_4d(stokes)

            if stokes == "L":
                Q = get_stokes_4d("Q")
                U = get_stokes_4d("U")
                return np.sqrt(Q**2 + U**2)

            elif stokes == "Lfrac":
                I = get_stokes_4d("I")
                Q = get_stokes_4d("Q")
                U = get_stokes_4d("U")
                L = np.sqrt(Q**2 + U**2)
                mask = np.abs(I) < 3 * np.nanstd(I)
                result = L / np.abs(I)
                result[mask] = np.nan
                return result

            elif stokes == "Vfrac":
                I = get_stokes_4d("I")
                V = get_stokes_4d("V")
                mask = np.abs(I) < 3 * np.nanstd(I)
                result = V / np.abs(I)
                result[mask] = np.nan
                return result

            elif stokes == "PANG":
                Q = get_stokes_4d("Q")
                U = get_stokes_4d("U")
                return 0.5 * np.degrees(np.arctan2(U, Q))

            else:
                raise RuntimeError(f"Unknown Stokes parameter for 4D data: {stokes}")

        # Fallback - just return the data as-is for Stokes I
        if stokes == "I":
            if data.ndim > 2:
                # Squeeze out extra dimensions
                return np.squeeze(data)
            return data

        raise RuntimeError(f"Cannot handle {stokes} for this FITS structure")

    def load_data(self, imagename, stokes, threshold, auto_adjust_rms=False):
        import time

        start_time = time.time()
        try:
            # Use the current RMS box when loading data
            from .utils import get_pixel_values_from_image

            # Calculate target size for fast load mode
            target_size = 0  # 0 = no downsampling
            if (
                hasattr(self, "downsample_toggle")
                and self.downsample_toggle.isChecked()
            ):
                target_size = 800  # Smart downsampling to ~800px max dimension

            pix, csys, psf = get_pixel_values_from_image(
                imagename,
                stokes,
                threshold,
                rms_box=tuple(self.current_rms_box),
                target_size=target_size,
            )

            self.current_image_data = pix
            self.current_wcs = csys
            self.psf = psf

            if pix is not None:
                height, width = pix.shape
                # Only auto adjust RMS box dimensions when loading a new image
                if auto_adjust_rms:
                    x1 = int(0.05 * width)
                    x2 = int(0.95 * width)
                    y1 = int(0.02 * height)
                    y2 = int(0.20 * height)
                    self.current_rms_box = [x1, x2, y1, y2]
                    self.contour_settings["rms_box"] = tuple(self.current_rms_box)

                # Update image stats when data is loaded
                try:
                    self.show_image_stats(rms_box=self.current_rms_box)
                except Exception as e:
                    print(f"Error showing image stats: {e}")
                    self.show_status_message(f"Error showing image stats: {e}")

        except Exception as e:
            from astropy.io import fits

            # Handle FITS files with Stokes axis (e.g., HPC converted files)
            try:
                pix = self._load_fits_stokes(
                    imagename, stokes, rms_box=tuple(self.current_rms_box)
                )
                self.current_image_data = pix
            except Exception as fits_err:
                print(f"[ERROR] Error loading FITS data: {fits_err}")
                self.show_status_message(f"Error loading FITS data: {fits_err}")
                try:
                    # Last resort: just get raw data
                    pix = fits.getdata(imagename)
                    self.current_image_data = pix
                except Exception as e2:
                    print(f"[ERROR] Error getting data: {e2}")
                    self.show_status_message(f"Error getting data: {e2}")
                    pix = None
            csys = None
            psf = None
            self.psf = None  # Clear PSF so beam from previous image doesn't persist

        if pix is not None:
            height, width = pix.shape
            self.solar_disk_center = (width // 2, height // 2)

            # Make sure RMS box is within image bounds
            if self.current_rms_box[1] > height:
                self.current_rms_box[1] = height
            if self.current_rms_box[3] > width:
                self.current_rms_box[3] = width

            # Update image stats when data is loaded
            try:
                self.show_image_stats(rms_box=self.current_rms_box)
            except Exception as e:
                print(f"[ERROR] Error showing image stats: {e}")
                self.show_status_message(f"Error showing image stats: {e}")

        # Store the FITS header for TB calculation
        try:
            from astropy.io import fits

            # Don't reset TB mode if we're loading the TB temp file
            is_tb_temp = (
                hasattr(self, "_tb_temp_file")
                and self._tb_temp_file
                and imagename == self._tb_temp_file
            )

            if imagename.endswith(".fits") or imagename.endswith(".fts"):
                with fits.open(imagename) as hdul:
                    self.current_header = dict(hdul[0].header)
                    bunit = self.current_header.get("BUNIT", "").lower()
                    self._current_bunit = self.current_header.get("BUNIT", "")

                    # Enable TB/Flux button if units are Jy/beam or K (not for TB temp file)
                    if hasattr(self, "tb_btn") and not is_tb_temp:
                        is_jy_beam = "jy" in bunit and "beam" in bunit
                        is_kelvin = bunit.strip().lower() == "k"
                        self.tb_btn.setEnabled(is_jy_beam or is_kelvin)

                        if is_kelvin:
                            self.tb_btn.setText("FLUX")
                            self.tb_btn.setToolTip("Convert to Flux (Jy/beam) view")
                        elif is_jy_beam:
                            self.tb_btn.setText("TB")
                            self.tb_btn.setToolTip(
                                "Convert to Brightness Temperature (K)"
                            )
                        else:
                            self.tb_btn.setText("FLUX")
                            self.tb_btn.setToolTip("Convert to Flux (Jy/beam) view")

                        # Reset TB mode when loading new image (not when loading TB temp)
                        if (
                            not hasattr(self, "_tb_original_imagename")
                            or not self._tb_original_imagename
                        ):
                            self._tb_mode = False
                            self.tb_btn.setChecked(False)

                    # Update HPC button based on coordinate system
                    if hasattr(self, "hpc_btn"):
                        ctype1 = self.current_header.get("CTYPE1", "").upper()
                        ctype2 = self.current_header.get("CTYPE2", "").upper()
                        is_hpc = (
                            "HPLN" in ctype1 or "HPLT" in ctype2 or "SOLAR" in ctype1
                        )

                        if is_hpc:
                            self.hpc_btn.setText("RA/DEC")
                            self.hpc_btn.setToolTip("Convert to RA/DEC coordinates")
                        else:
                            self.hpc_btn.setText("HPC")
                            self.hpc_btn.setToolTip("Convert to Helioprojective view")
            else:
                # Try to get header from CASA image
                try:
                    from casatools import image as IA

                    ia = IA()
                    ia.open(imagename)

                    # Get CASA image info
                    summary = ia.summary()
                    csys = ia.coordsys()

                    # Build header dict from CASA info
                    self.current_header = {}

                    # Get beam info
                    beam = ia.restoringbeam()
                    if beam and "major" in beam:
                        self.current_header["BMAJ"] = (
                            beam["major"]["value"] / 3600
                            if beam["major"]["unit"] == "arcsec"
                            else beam["major"]["value"]
                        )
                        self.current_header["BMIN"] = (
                            beam["minor"]["value"] / 3600
                            if beam["minor"]["unit"] == "arcsec"
                            else beam["minor"]["value"]
                        )

                    # Get frequency
                    if "refval" in dir(csys):
                        refval = csys.referencevalue()["numeric"]
                        for i, unit in enumerate(csys.units()):
                            if unit == "Hz":
                                self.current_header["CRVAL3"] = refval[i]
                                break

                    # Get units
                    bunit = ia.brightnessunit()
                    self.current_header["BUNIT"] = bunit
                    self._current_bunit = bunit

                    # Get coordinate names for HPC detection (before closing ia)
                    dimension_names = [n.upper() for n in csys.names()]

                    ia.close()

                    # Enable TB/Flux button if units are Jy/beam or K
                    if hasattr(self, "tb_btn"):
                        is_jy_beam = "jy" in bunit.lower() and "beam" in bunit.lower()
                        is_kelvin = bunit.strip().lower() == "k"
                        self.tb_btn.setEnabled(is_jy_beam or is_kelvin)

                        if is_kelvin:
                            self.tb_btn.setText("FLUX")
                            self.tb_btn.setToolTip("Convert to Flux (Jy/beam) view")
                        elif is_jy_beam:
                            self.tb_btn.setText("TB")
                            self.tb_btn.setToolTip(
                                "Convert to Brightness Temperature (K)"
                            )
                        else:
                            self.tb_btn.setText("TB")
                            self.tb_btn.setToolTip(
                                "Convert to Brightness Temperature (K)"
                            )

                        if (
                            not hasattr(self, "_tb_original_imagename")
                            or not self._tb_original_imagename
                        ):
                            self._tb_mode = False
                            self.tb_btn.setChecked(False)

                    # Update HPC button based on coordinate system
                    if hasattr(self, "hpc_btn"):
                        is_hpc = (
                            "SOLAR-X" in dimension_names
                            or "HPLN-TAN" in dimension_names
                        )

                        if is_hpc:
                            self.hpc_btn.setText("RA/DEC")
                            self.hpc_btn.setToolTip("Convert to RA/DEC coordinates")
                        else:
                            self.hpc_btn.setText("HPC")
                            self.hpc_btn.setToolTip("Convert to Helioprojective view")

                except Exception as casa_err:
                    print(f"[TB] CASA header extraction error: {casa_err}")
                    self.show_status_message(
                        f"[TB] CASA header extraction error: {casa_err}"
                    )
                    self.current_header = None
                    self._current_bunit = ""
                    if hasattr(self, "tb_btn"):
                        self.tb_btn.setEnabled(False)

        except Exception as e:
            print(f"[ERROR] Error loading header: {e}")
            self.show_status_message(f"[TB] Error loading header: {e}")
            self.current_header = None
            self._current_bunit = ""
            if hasattr(self, "tb_btn"):
                self.tb_btn.setEnabled(False)

        if not self.psf:
            self.show_beam_checkbox.setChecked(False)
            self.show_beam_checkbox.setEnabled(False)
        else:
            self.show_beam_checkbox.setEnabled(True)

        # Enable NOAA Events button when an image is loaded
        if hasattr(self, "noaa_events_btn"):
            self.noaa_events_btn.setEnabled(True)
        if hasattr(self, "helioviewer_btn"):
            self.helioviewer_btn.setEnabled(True)

        # self.plot_image()
        # self.schedule_plot()

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
        }

        # Settings snapshots
        exclude_keys = {
            "contour_data",
            "contour_wcs",
            "contour_header",
            "wcs",
            "header",
            "data",
        }
        cs_snap = {
            k: self.contour_settings[k]
            for k in self.contour_settings
            if k not in exclude_keys
        }

        # Track identity of heavy objects separately if needed (only if they exist)
        if "contour_data" in self.contour_settings:
            state["contour_data_id"] = id(self.contour_settings["contour_data"])
        else:
            state["contour_data_id"] = None

        state["contour_settings"] = str(cs_snap)

        state["grid_settings"] = str(self.grid_style)
        state["beam_settings"] = str(self.beam_style)
        state["solar_disk_settings"] = str(self.solar_disk_style)
        state["plot_settings"] = str(self.plot_settings)

        return state

    def plot_image(
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
        import time

        # Show wait cursor during plotting
        QApplication.setOverrideCursor(Qt.WaitCursor)

        # Lazy load image metadata - only refresh when imagename changes
        if self._cached_imagename != self.imagename:
            # Cache miss - need to reload metadata
            self._cached_fits_flag = False
            self._cached_fits_header = None
            self._cached_csys = None
            self._cached_summary = None
            self._cached_csys_record = None

            if self.imagename.endswith(".fits") or self.imagename.endswith(".fts"):
                from astropy.io import fits

                try:
                    self._cached_fits_flag = True
                    with fits.open(self.imagename, memmap=True) as hdul:
                        # Make a copy of header so we don't hold the file open
                        self._cached_fits_header = dict(hdul[0].header)
                except Exception as e:
                    try:
                        self._cached_fits_header = dict(fits.getheader(self.imagename))
                    except Exception as e2:
                        print(f"[ERROR] Error getting header: {e2}")
                        self.show_status_message(f"Error getting header: {e2}")
                        self._cached_fits_flag = False

            try:
                ia_tool = IA()
                ia_tool.open(self.imagename)
                self._cached_csys = ia_tool.coordsys()
                self._cached_summary = ia_tool.summary()
                self._cached_csys_record = ia_tool.coordsys().torecord()
                ia_tool.close()
            except Exception as e:
                print(f"[ERROR] Error getting image metadata: {e}")
                self.show_status_message(f"Error getting image metadata: {e}")

            self._cached_imagename = self.imagename

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

        start_time = time.time()
        if self.current_image_data is None:
            QApplication.restoreOverrideCursor()
            return

        data = self.current_image_data
        n_dims = len(data.shape)

        # Cache the transposed image if the current image hasn't changed.
        if not hasattr(self, "_cached_data_id") or self._cached_data_id != id(data):
            self._cached_transposed = data.transpose()
            self._cached_data_id = id(data)
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

        # Determine vmin/vmax CACHED for Fast Path
        # We need these values to compute Normalization
        fp_vmin = vmin_val
        if fp_vmin is None:
            fp_vmin = np.nanmin(data)
        fp_vmax = vmax_val
        if fp_vmax is None:
            fp_vmax = np.nanmax(data)
        if fp_vmax <= fp_vmin:
            fp_vmax = fp_vmin + 1e-6

        # Create the normalization object early for Fast Path
        if stretch == "log":
            safe_min = max(fp_vmin, 1e-8)
            safe_max = max(fp_vmax, safe_min * 1.01)
            norm = LogNorm(vmin=safe_min, vmax=safe_max)
        elif stretch == "sqrt":
            norm = SqrtNorm(vmin=fp_vmin, vmax=fp_vmax)
        elif stretch == "arcsinh":
            norm = AsinhNorm(vmin=fp_vmin, vmax=fp_vmax)
        elif stretch == "power":
            norm = PowerNorm(vmin=fp_vmin, vmax=fp_vmax, gamma=gamma)
        elif stretch == "zscale":
            norm = ZScaleNorm(
                vmin=fp_vmin, vmax=fp_vmax, contrast=0.25, num_samples=600
            )
        elif stretch == "histeq":
            norm = HistEqNorm(vmin=fp_vmin, vmax=fp_vmax, n_bins=256)
        else:
            norm = Normalize(vmin=fp_vmin, vmax=fp_vmax)

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
                    # last_norm_params = getattr(self, "_last_norm_params", None)

                    if hasattr(self, "colorbar") and self.colorbar:
                        self.colorbar.update_normal(self.image_plot)
                        if stretch in ("power", "histeq", "sqrt", "arcsinh", "log"):
                            try:
                                ticks = self.colorbar.ax.get_yticks()
                                valid_ticks = [
                                    t for t in ticks if fp_vmin <= t <= fp_vmax
                                ]
                                if len(valid_ticks) >= 2:
                                    self.colorbar.ax.set_yticks(valid_ticks)
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

                    self.canvas.draw()
                    QApplication.restoreOverrideCursor()
                    return
                except Exception as e:
                    print(f"Fast Path Update Failed: {e}")
            else:
                # Debugging Mismatch (Optional log, keeping it minimal)
                pass

        self.figure.clear()

        # Determine vmin/vmax
        if vmin_val is None:
            vmin_val = np.nanmin(data)
        if vmax_val is None:
            vmax_val = np.nanmax(data)
        if vmax_val <= vmin_val:
            vmax_val = vmin_val + 1e-6

        # Create the normalization object
        if stretch == "log":
            safe_min = max(vmin_val, 1e-8)
            safe_max = max(vmax_val, safe_min * 1.01)
            norm = LogNorm(vmin=safe_min, vmax=safe_max)
        elif stretch == "sqrt":
            norm = SqrtNorm(vmin=vmin_val, vmax=vmax_val)
        elif stretch == "arcsinh":
            norm = AsinhNorm(vmin=vmin_val, vmax=vmax_val)
        elif stretch == "power":
            norm = PowerNorm(vmin=vmin_val, vmax=vmax_val, gamma=gamma)
        elif stretch == "zscale":
            norm = ZScaleNorm(
                vmin=vmin_val, vmax=vmax_val, contrast=0.25, num_samples=600
            )
        elif stretch == "histeq":
            norm = HistEqNorm(vmin=vmin_val, vmax=vmax_val, n_bins=256)

        if vmin_val is None:
            vmin_val = fp_vmin
        if vmax_val is None:
            vmax_val = fp_vmax

        # Monkey patch removal if present
        if hasattr(self.figure, "_monkey_patched"):
            del self.figure._monkey_patched
            self.figure.clear = self.figure._original_clear

        # Cache the WCS object if current_wcs hasn't changed.
        wcs_obj = None
        if self.current_wcs:
            if (not hasattr(self, "_cached_wcs_id")) or (
                self._cached_wcs_id != id(self.current_wcs)
            ):
                try:
                    from astropy.wcs import WCS

                    ref_val = self.current_wcs.referencevalue()["numeric"][0:2]
                    ref_pix = self.current_wcs.referencepixel()["numeric"][0:2]
                    increment = self.current_wcs.increment()["numeric"][0:2]
                    self._cached_wcs_obj = WCS(naxis=2)
                    self._cached_wcs_obj.wcs.crpix = ref_pix
                    temp_flag = False
                    if fits_flag:
                        if (
                            header["CTYPE1"] == "HPLN-TAN"
                            or header["CTYPE1"] == "RA---SIN"
                            or header["CTYPE1"] == "RA---TAN"
                        ):
                            self._cached_wcs_obj.wcs.crval = [
                                ref_val[0] * 180 / np.pi,
                                ref_val[1] * 180 / np.pi,
                            ]
                            self._cached_wcs_obj.wcs.cdelt = [
                                increment[0] * 180 / np.pi,
                                increment[1] * 180 / np.pi,
                            ]
                            temp_flag = True
                        elif header["CTYPE1"] == "SOLAR-X":
                            self._cached_wcs_obj.wcs.crval = ref_val
                            self._cached_wcs_obj.wcs.cdelt = increment
                            temp_flag = True
                    if not temp_flag:
                        if "Right Ascension" in summary["axisnames"]:
                            self._cached_wcs_obj.wcs.crval = [
                                ref_val[0] * 180 / np.pi,
                                ref_val[1] * 180 / np.pi,
                            ]
                            self._cached_wcs_obj.wcs.cdelt = [
                                increment[0] * 180 / np.pi,
                                increment[1] * 180 / np.pi,
                            ]
                        else:
                            self._cached_wcs_obj.wcs.crval = ref_val
                            self._cached_wcs_obj.wcs.cdelt = increment
                    try:
                        if fits_flag:
                            try:
                                self._cached_wcs_obj.wcs.ctype = [
                                    header["CTYPE1"],
                                    header["CTYPE2"],
                                ]
                            except Exception as e:
                                print(f"[ERROR] Error getting projection: {e}")
                                self.show_status_message(
                                    f"Error getting projection: {e}"
                                )
                                self._cached_wcs_obj = None
                        elif (csys.projection()["type"] == "SIN") and (
                            "Right Ascension" in summary["axisnames"]
                        ):
                            self._cached_wcs_obj.wcs.ctype = [
                                "RA---SIN",
                                "DEC--SIN",
                            ]
                        elif (csys.projection()["type"] == "TAN") and (
                            "Right Ascension" in summary["axisnames"]
                        ):
                            self._cached_wcs_obj.wcs.ctype = [
                                "RA---TAN",
                                "DEC--TAN",
                            ]

                        else:
                            print(f"[ERROR] Error getting projection: {e}")
                            self.show_status_message(f"Error getting projection: {e}")
                            self._cached_wcs_obj = None

                    except Exception as e:
                        print(f"[ERROR] Error getting projection: {e}")
                        self.show_status_message(f"Error getting projection: {e}")
                        self._cached_wcs_obj = None
                    self._cached_wcs_id = id(self.current_wcs)
                except Exception as e:
                    print(f"[ERROR] Error creating WCS: {e}")
                    self.show_status_message(f"Error creating WCS: {e}")
                    self._cached_wcs_obj = None
            wcs_obj = self._cached_wcs_obj

        # Plot with or without WCS
        if wcs_obj is not None:
            try:
                ax = self.figure.add_subplot(111, projection=wcs_obj)
                im = ax.imshow(
                    transposed_data,
                    origin="lower",
                    cmap=cmap,
                    norm=norm,
                    interpolation=interpolation,
                    # aspect="auto",
                )
                self.image_plot = im
                if fits_flag:
                    if header["CTYPE1"] == "HPLN-TAN":
                        ax.set_xlabel("Solar X")
                        ax.set_ylabel("Solar Y")
                    elif header["CTYPE1"] == "SOLAR-X":
                        ax.set_xlabel(f"Solar X ({header['CUNIT1']})")
                        ax.set_ylabel(f"Solar Y ({header['CUNIT2']})")
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
                print(f"[ERROR] Error setting up WCS axes: {e}")
                self.show_status_message(f"Error setting up WCS axes: {e}")
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

        if stored_xlim is not None and stored_ylim is not None:
            ax.set_xlim(stored_xlim)
            ax.set_ylim(stored_ylim)

        # ax.set_title(os.path.basename(self.imagename) if self.imagename else "No Image")
        # Display the image time in UTC and freq in MHz as a title
        if self.current_image_data is not None:
            # Get the image time and frequency
            try:
                # ia = IA()
                # ia.open(self.imagename)
                # csys_record = ia.coordsys().torecord()
                # ia.close()
                # if self.imagename.endswith(".fits"):
                # from astropy.io import fits

                # with fits.open(self.imagename) as hdul:
                #    fits_header = hdul[0].header
                #    image_time = fits_header.get("DATE-OBS", None)
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
                        if header.get("TELESCOP") == "SOHO" and header.get("TIME-OBS"):
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
                        print(f"[ERROR] Error getting image time: {e}")
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
                        print(f"[ERROR] Error getting image frequency: {e}")
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
                    if "obsdate" in csys_record:
                        obsdate = csys_record["obsdate"]
                        m0 = obsdate.get("m0", {})
                        time_value = m0.get("value", None)
                        time_unit = m0.get("unit", None)
                        refer = obsdate.get("refer", None)
                        if refer == "UTC" or time_unit == "d":
                            t = Time(time_value, format="mjd")
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
                        title = f"{image_time} | {header['TELESCOP']} {header['WAVELNTH']} $\\AA$"
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
                ax.set_title(title)
            except Exception as e:
                print(f"[ERROR] Error getting title: {e}")
                self.show_status_message(f"Error getting title: {e}")
                title = (
                    os.path.basename(self.imagename) if self.imagename else "No Image"
                )
                ax.set_title(title)

            # Format the time and frequency as a title
        # For non-linear stretches, filter ticks to only show those within data range
        if stretch in ("power", "histeq", "sqrt", "arcsinh", "log"):
            cb = self.figure.colorbar(
                im,
                ax=ax,
                aspect=30,
            )
            # Remove ticks that are outside the actual data range
            ticks = cb.ax.get_yticks()
            valid_ticks = [t for t in ticks if vmin_val <= t <= vmax_val]
            if len(valid_ticks) >= 2:
                cb.ax.set_yticks(valid_ticks)
        else:
            cb = self.figure.colorbar(
                im,
                ax=ax,
                aspect=30,
            )
        self.colorbar = cb

        # Apply plot customization settings
        ps = self.plot_settings

        # Helper to resolve "auto" colors based on theme
        from .styles import theme_manager

        is_dark = theme_manager.is_dark
        theme_text = "#ffffff" if is_dark else "#000000"
        theme_tick = "#e0e0e0" if is_dark else "#333333"
        theme_border = "#ffffff" if is_dark else "#000000"

        # Get text color for labels and title
        text_color = ps.get("text_color", "auto")
        if text_color == "auto":
            text_color = theme_text  # Explicitly set based on theme

        # Get tick color for tick marks and tick labels (separate from text_color)
        tick_color = ps.get("tick_color", "auto")
        if tick_color == "auto":
            tick_color = theme_tick  # Explicitly set based on theme

        # Build label kwargs with color if specified
        label_kwargs = {"fontsize": ps.get("axis_label_fontsize", 12)}
        title_kwargs = {"fontsize": ps.get("title_fontsize", 12)}
        if text_color:
            label_kwargs["color"] = text_color
            title_kwargs["color"] = text_color

        # Apply custom labels and fontsizes
        # For WCS axes, we need to re-set the label to apply fontsize
        if ps.get("xlabel"):
            ax.set_xlabel(ps["xlabel"], **label_kwargs)
        else:
            # Get existing label and re-set with new fontsize
            current_xlabel = ax.get_xlabel()
            ax.set_xlabel(current_xlabel, **label_kwargs)

        if ps.get("ylabel"):
            ax.set_ylabel(ps["ylabel"], **label_kwargs)
        else:
            current_ylabel = ax.get_ylabel()
            ax.set_ylabel(current_ylabel, **label_kwargs)

        if ps.get("title"):
            ax.set_title(ps["title"], **title_kwargs)
        else:
            current_title = ax.get_title()
            ax.set_title(current_title, **title_kwargs)

        # Apply tick settings with separate tick color (tick marks only)
        tick_params = {
            "axis": "both",
            "which": "major",
            "labelsize": ps.get("axis_tick_fontsize", 10),
            "direction": ps.get("tick_direction", "in"),
            "length": ps.get("tick_length", 4),
            "width": ps.get("tick_width", 1.0),
        }
        if tick_color:
            tick_params["color"] = tick_color  # Only tick marks, not labels
        if text_color:
            tick_params["labelcolor"] = text_color  # Tick labels use text color
        ax.tick_params(**tick_params)

        # For WCSAxes, also apply tick mark colors via coords (not tick labels)
        if hasattr(ax, "coords"):
            try:
                for coord in ax.coords:
                    if tick_color:
                        coord.set_ticks(color=tick_color)
                    if text_color:
                        coord.set_ticklabel(color=text_color)
            except Exception as e:
                print(f"[ERROR] Error setting WCSAxes tick colors: {e}")
                self.show_status_message(f"Error setting WCSAxes tick colors: {e}")

        # Apply colorbar settings with separate tick color
        cb_tick_params = {
            "labelsize": ps.get("colorbar_tick_fontsize", 10),
            "direction": ps.get("tick_direction", "in"),
            "length": ps.get("tick_length", 4),
            "width": ps.get("tick_width", 1.0),
        }
        if tick_color:
            cb_tick_params["color"] = tick_color  # Only tick marks
        if text_color:
            cb_tick_params["labelcolor"] = text_color  # Tick labels use text color
        cb.ax.tick_params(**cb_tick_params)

        # Set colorbar label - use custom label or BUNIT from image
        colorbar_label = ps.get("colorbar_label", "")
        if (
            not colorbar_label
            and hasattr(self, "_current_bunit")
            and self._current_bunit
        ):
            colorbar_label = self._current_bunit

        if colorbar_label:
            label_kwargs = {"fontsize": ps.get("colorbar_label_fontsize", 10)}
            if text_color:
                label_kwargs["color"] = text_color
            cb.set_label(colorbar_label, **label_kwargs)

        # Apply text color to labels and title
        if text_color:
            ax.xaxis.label.set_color(text_color)
            ax.yaxis.label.set_color(text_color)
            ax.title.set_color(text_color)

        # Apply background colors
        plot_bg = ps.get("plot_bg_color", "auto")
        figure_bg = ps.get("figure_bg_color", "auto")

        # Resolve defaults from current theme (stored in rcParams)
        from matplotlib import rcParams

        if plot_bg == "auto":
            plot_bg = rcParams.get("axes.facecolor", "white")
        if figure_bg == "auto":
            figure_bg = rcParams.get("figure.facecolor", "white")

        if plot_bg:
            try:
                ax.set_facecolor(plot_bg)
            except ValueError:
                pass  # Invalid color, ignore

        if figure_bg:
            try:
                self.figure.patch.set_facecolor(figure_bg)
                self.figure.set_facecolor(figure_bg)
            except ValueError:
                pass  # Invalid color, ignore

        # Apply border (spine) color and width
        border_color = ps.get("border_color", "auto")
        border_width = ps.get("border_width", 1.0)

        if border_color == "auto":
            border_color = theme_border

        if border_color:

            for spine in ax.spines.values():
                spine.set_color(border_color)
            # For WCSAxes, also set frame color
            if hasattr(ax, "coords") and hasattr(ax.coords, "frame"):
                try:
                    ax.coords.frame.set_color(border_color)
                except Exception:
                    pass
            # Apply to colorbar borders
            for spine in cb.ax.spines.values():
                spine.set_color(border_color)

        # Apply border width to main axes
        for spine in ax.spines.values():
            spine.set_linewidth(border_width)
        # For WCSAxes frame width
        if hasattr(ax, "coords") and hasattr(ax.coords, "frame"):
            try:
                ax.coords.frame.set_linewidth(border_width)
            except Exception:
                pass
        # Apply border width to colorbar
        for spine in cb.ax.spines.values():
            spine.set_linewidth(border_width)

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
                    cdelt = self.current_wcs.increment()["numeric"][0:2]
                    if isinstance(cdelt, list):
                        cdelt = [float(c) for c in cdelt]
                    cdelt = np.array(cdelt) * 180 / np.pi
                    dx_deg = abs(cdelt[0])
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
                print(f"[ERROR] Error drawing beam: {e}")
                self.show_status_message(f"Error drawing beam: {e}")

        # Draw solar disk if enabled
        if (
            hasattr(self, "show_solar_disk_checkbox")
            and self.show_solar_disk_checkbox.isChecked()
        ):
            try:
                if self.solar_disk_center is None:
                    height, width = data.shape
                    self.solar_disk_center = (width // 2, height // 2)

                center_x, center_y = self.solar_disk_center

                if self.current_wcs:
                    radius_deg = (self.solar_disk_diameter_arcmin / 60.0) / 2.0
                    cdelt = self.current_wcs.increment()["numeric"][0:2]
                    if isinstance(cdelt, list):
                        cdelt = [float(c) for c in cdelt]
                    cdelt = np.array(cdelt) * 180 / np.pi
                    dx_deg = abs(cdelt[0])
                    radius_pix = radius_deg / dx_deg
                else:
                    radius_pix = min(data.shape) / 8

                circle = plt.Circle(
                    (center_x, center_y),
                    radius_pix,
                    fill=False,
                    edgecolor=self.solar_disk_style["color"],
                    linestyle=self.solar_disk_style["linestyle"],
                    linewidth=self.solar_disk_style["linewidth"],
                    alpha=self.solar_disk_style["alpha"],
                )
                ax.add_patch(circle)

                # Only draw the center marker if show_center is True
                if self.solar_disk_style.get("show_center", True):
                    cross_size = radius_pix / 20
                    ax.plot(
                        [center_x - cross_size, center_x + cross_size],
                        [center_y, center_y],
                        color=self.solar_disk_style["color"],
                        linewidth=1.5,
                        alpha=self.solar_disk_style["alpha"],
                    )
                    ax.plot(
                        [center_x, center_x],
                        [center_y - cross_size, center_y + cross_size],
                        color=self.solar_disk_style["color"],
                        linewidth=1.5,
                        alpha=self.solar_disk_style["alpha"],
                    )
            except Exception as e:
                print(f"[ERROR] Error drawing solar disk: {e}")
                self.show_status_message(f"Error drawing solar disk: {e}")

        # Draw contours if enabled
        if (
            hasattr(self, "show_contours_checkbox")
            and self.show_contours_checkbox.isChecked()
        ):
            self.draw_contours(ax)

        self.init_region_editor(ax)

        # Apply layout/padding settings from plot customization
        ps = self.plot_settings
        if ps.get("use_tight_layout", True) and tight_layout:
            self.figure.tight_layout()
        else:
            # Apply custom subplot adjust if not using tight layout
            self.figure.subplots_adjust(
                left=ps.get("pad_left", 0.135),
                right=ps.get("pad_right", 1.0),
                top=ps.get("pad_top", 0.95),
                # bottom=ps.get("pad_bottom", 0.05),
                bottom=ps.get("pad_bottom", 0.08),
                wspace=ps.get("pad_wspace", 0.2),
                hspace=ps.get("pad_hspace", 0.2),
            )

        # Instead of immediate draw, use draw_idle to coalesce multiple calls
        self.canvas.draw_idle()

        # Save state for next update
        self._last_rendered_state = self._get_render_state(tight_layout=tight_layout)

        # Recalculate defaults if they were None (same logic as start of function)
        _s_vmin = vmin_val if vmin_val is not None else np.nanmin(data)
        _s_vmax = vmax_val if vmax_val is not None else np.nanmax(data)
        if _s_vmax <= _s_vmin:
            _s_vmax = _s_vmin + 1e-6

        self._last_norm_params = (_s_vmin, _s_vmax, stretch, gamma)

        # Restore normal cursor
        QApplication.restoreOverrideCursor()

    def _update_beam_position(self, ax):
        # Don't draw beam if no PSF or beam properties
        if (
            not self.psf
            or not hasattr(self, "beam_properties")
            or not self.beam_properties
        ):
            return

        for patch in ax.patches:
            if isinstance(patch, Ellipse):
                patch.remove()

        xlim = ax.get_xlim()
        ylim = ax.get_ylim()

        major_pix = self.beam_properties["major_pix"]
        minor_pix = self.beam_properties["minor_pix"]
        pa_deg = self.beam_properties["pa_deg"]
        margin = self.beam_properties["margin"]

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

    def _update_solar_disk_position(self, ax):
        if (
            hasattr(self, "show_solar_disk_checkbox")
            and self.show_solar_disk_checkbox.isChecked()
        ):
            try:
                center_x, center_y = self.solar_disk_center
                if self.current_wcs:
                    radius_deg = (self.solar_disk_diameter_arcmin / 60.0) / 2.0
                    cdelt = self.current_wcs.increment()["numeric"][0:2]
                    if isinstance(cdelt, list):
                        cdelt = [float(c) for c in cdelt]
                    cdelt = np.array(cdelt) * 180 / np.pi
                    dx_deg = abs(cdelt[0])
                    radius_pix = radius_deg / dx_deg
                else:
                    radius_pix = min(self.current_image_data.shape) / 8

                circle = plt.Circle(
                    (center_x, center_y),
                    radius_pix,
                    fill=False,
                    edgecolor=self.solar_disk_style["color"],
                    linestyle=self.solar_disk_style["linestyle"],
                    linewidth=self.solar_disk_style["linewidth"],
                    alpha=self.solar_disk_style["alpha"],
                )
                ax.add_patch(circle)

                if self.solar_disk_style.get("show_center", True):
                    cross_size = radius_pix / 20
                    ax.plot(
                        [center_x - cross_size, center_x + cross_size],
                        [center_y, center_y],
                        color=self.solar_disk_style["color"],
                        linewidth=1.5,
                        alpha=self.solar_disk_style["alpha"],
                    )
                    ax.plot(
                        [center_x, center_x],
                        [center_y - cross_size, center_y + cross_size],
                        color=self.solar_disk_style["color"],
                        linewidth=1.5,
                        alpha=self.solar_disk_style["alpha"],
                    )
            except Exception as e:
                print(f"[ERROR] Error drawing solar disk: {e}")
                self.show_status_message(f"Error drawing solar disk: {e}")

    def on_stokes_changed(self, stokes):
        if not self.imagename:
            return

        main_window = self.parent()
        if main_window:
            self.show_status_message(
                f"Loading data for Stokes {stokes}... Please wait..."
            )
            QApplication.processEvents()

        try:
            threshold = float(self.threshold_entry.text())
        except (ValueError, AttributeError):
            threshold = 10.0
            if hasattr(self, "threshold_entry"):
                self.threshold_entry.setText("10.0")

        try:
            self.load_data(self.imagename, stokes, threshold)
        except RuntimeError as e:
            # Show warning dialog for missing Stokes data
            QMessageBox.warning(
                self,
                "Stokes Parameter Error",
                f"Cannot load Stokes {stokes} from this image:\n\n{str(e)}\n\nThe image may be single-Stokes (I only) or missing the required polarization data.",
            )
            # Revert to Stokes I if possible
            if self.stokes_combo.currentText() != "I":
                self.stokes_combo.blockSignals(True)
                self.stokes_combo.setCurrentText("I")
                self.stokes_combo.blockSignals(False)
            return
        except Exception as e:
            QMessageBox.warning(
                self,
                "Data Loading Error",
                f"Error loading data for Stokes {stokes}:\n\n{str(e)}\n\nThe image may be single-Stokes (I only) or missing the required polarization data.",
            )
            return

        data = self.current_image_data
        if data is not None:
            dmin = float(np.nanmin(data))
            dmax = float(np.nanmax(data))
            self.set_range(dmin, dmax)

            stretch = (
                self.stretch_combo.currentText()
                if hasattr(self, "stretch_combo")
                else "linear"
            )
            cmap = (
                self.cmap_combo.currentText()
                if hasattr(self, "cmap_combo")
                else "viridis"
            )
            try:
                gamma = float(self.gamma_entry.text())
            except (ValueError, AttributeError):
                gamma = 1.0

            # self.plot_image(dmin, dmax, stretch, cmap, gamma)
            self.schedule_plot()

            if main_window:
                self.show_status_message(
                    f"Stokes changed to {stokes}, display range: [{dmin:.4g}, {dmax:.4g}]"
                )

    def _update_stokes_combo_state(self, available_stokes):
        """
        Update the Stokes combo box to enable/disable items based on available Stokes.

        Args:
            available_stokes: List of available base Stokes, e.g., ["I"] or ["I", "Q", "U", "V"]
        """
        if not hasattr(self, "stokes_combo") or self.stokes_combo is None:
            return

        from PyQt5.QtGui import QBrush, QColor

        # Get theme-aware colors for disabled state - use palette for consistency
        try:
            from .styles import theme_manager

            palette = theme_manager.palette
            is_dark = theme_manager.is_dark
            # Use the same disabled color from palette as contour dialog
            disabled_color = QColor(palette.get("disabled", "#cccccc"))
            enabled_color = QColor(
                palette.get("text", "#ffffff" if is_dark else "#000000")
            )
        except ImportError:
            # Fallback colors
            disabled_color = QColor("#cccccc")
            enabled_color = QColor("#000000")

        # Derived parameters and their requirements
        # Parameters requiring Q: Q, Q/I
        # Parameters requiring U: U, U/I, U/V, L, Lfrac, PANG
        # Parameters requiring V: V, Vfrac, U/V
        requires_q = {"Q", "Q/I", "L", "Lfrac", "PANG"}
        requires_u = {"U", "U/I", "U/V", "L", "Lfrac", "PANG"}
        requires_v = {"V", "Vfrac", "U/V"}

        has_q = "Q" in available_stokes
        has_u = "U" in available_stokes
        has_v = "V" in available_stokes

        # Store current selection before modifying
        current_selection = self.stokes_combo.currentText()

        # Iterate through combo items and enable/disable based on requirements
        model = self.stokes_combo.model()
        for i in range(self.stokes_combo.count()):
            item_text = self.stokes_combo.itemText(i)

            # Check if this item can be enabled
            enabled = True
            if item_text in requires_q and not has_q:
                enabled = False
            if item_text in requires_u and not has_u:
                enabled = False
            if item_text in requires_v and not has_v:
                enabled = False

            # Set item enabled/disabled using model flags AND foreground color
            item = model.item(i)
            if item:
                if enabled:
                    item.setFlags(item.flags() | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    item.setData(QBrush(enabled_color), Qt.ForegroundRole)
                else:
                    # Remove both enabled and selectable flags
                    item.setFlags(
                        item.flags() & ~Qt.ItemIsEnabled & ~Qt.ItemIsSelectable
                    )
                    item.setData(QBrush(disabled_color), Qt.ForegroundRole)

        # Store available stokes for later reference
        self._available_stokes = available_stokes

    def _validate_and_switch_stokes(self, imagename, selected_stokes):
        """
        Check if selected Stokes is available; if not, warn user and switch to I.

        Args:
            imagename: Path to the image
            selected_stokes: Currently selected Stokes parameter

        Returns:
            str: The validated Stokes parameter to use
        """
        from .utils import get_available_stokes

        available_stokes = get_available_stokes(imagename)

        # Update combo box state
        self._update_stokes_combo_state(available_stokes)

        # Check if selected Stokes is valid
        requires_q = {"Q", "Q/I", "L", "Lfrac", "PANG"}
        requires_u = {"U", "U/I", "U/V", "L", "Lfrac", "PANG"}
        requires_v = {"V", "Vfrac", "U/V"}

        has_q = "Q" in available_stokes
        has_u = "U" in available_stokes
        has_v = "V" in available_stokes

        needs_switch = False
        missing_stokes = []

        if selected_stokes in requires_q and not has_q:
            needs_switch = True
            missing_stokes.append("Q")
        if selected_stokes in requires_u and not has_u:
            needs_switch = True
            missing_stokes.append("U")
        if selected_stokes in requires_v and not has_v:
            needs_switch = True
            missing_stokes.append("V")

        if needs_switch:
            # Show warning dialog
            QMessageBox.warning(
                self,
                "Stokes Parameter Unavailable",
                f"The selected Stokes parameter '{selected_stokes}' requires "
                f"polarization data ({', '.join(set(missing_stokes))}) that is not available "
                f"in this image.\n\n"
                f"Available Stokes: {', '.join(available_stokes)}\n\n"
                f"Switching to Stokes I.",
            )

            # Switch to Stokes I
            self.stokes_combo.blockSignals(True)
            self.stokes_combo.setCurrentText("I")
            self.stokes_combo.blockSignals(False)
            return "I"

        return selected_stokes

    def on_checkbox_changed(self):
        if not hasattr(self, "current_image_data") or self.current_image_data is None:
            return

        try:
            vmin_val = float(self.vmin_entry.text())
            vmax_val = float(self.vmax_entry.text())
        except (ValueError, AttributeError):
            vmin_val = None
            vmax_val = None

        try:
            gamma = float(self.gamma_entry.text())
        except (ValueError, AttributeError):
            gamma = 1.0

        stretch = (
            self.stretch_combo.currentText()
            if hasattr(self, "stretch_combo")
            else "linear"
        )
        cmap = (
            self.cmap_combo.currentText() if hasattr(self, "cmap_combo") else "viridis"
        )

        # Determine which checkbox was changed
        sender = self.sender()
        if sender == self.show_beam_checkbox:
            status = "enabled" if self.show_beam_checkbox.isChecked() else "disabled"
            self.show_status_message(f"Beam display {status}")
        elif sender == self.show_grid_checkbox:
            status = "enabled" if self.show_grid_checkbox.isChecked() else "disabled"
            self.show_status_message(f"Grid display {status}")
        elif sender == self.show_solar_disk_checkbox:
            status = (
                "enabled" if self.show_solar_disk_checkbox.isChecked() else "disabled"
            )
            self.show_status_message(f"Solar disk display {status}")
        elif sender == self.show_contours_checkbox:
            status = (
                "enabled" if self.show_contours_checkbox.isChecked() else "disabled"
            )
            self.show_status_message(f"Contours display {status}")

        self.schedule_plot()

    def add_text_annotation(
        self,
        x,
        y,
        text,
        color="yellow",
        fontsize=12,
        fontweight="normal",
        fontstyle="normal",
        background=None,
        alpha=1.0,
    ):
        """Add text annotation to the plot with customizable styling."""
        ax = self.figure.gca()
        bbox_props = None
        if background:
            bbox_props = dict(boxstyle="round,pad=0.3", facecolor=background, alpha=0.7)
        ax.text(
            x,
            y,
            text,
            color=color,
            fontsize=fontsize,
            fontweight=fontweight,
            fontstyle=fontstyle,
            bbox=bbox_props,
            alpha=alpha,
        )
        self.canvas.draw()

    def add_arrow_annotation(
        self,
        x1,
        y1,
        x2,
        y2,
        color="red",
        linewidth=2.0,
        head_width=8,
        head_length=10,
        alpha=1.0,
    ):
        """Add arrow annotation to the plot with customizable styling."""
        ax = self.figure.gca()
        ax.annotate(
            "",
            xy=(x2, y2),
            xytext=(x1, y1),
            arrowprops=dict(
                arrowstyle="-|>", color=color, lw=linewidth, mutation_scale=head_width
            ),
            alpha=alpha,
        )
        self.canvas.draw()

    def set_solar_disk_center(self):
        """Show non-modal solar disk settings dialog."""
        if self.current_image_data is None:
            QMessageBox.warning(self, "No Image", "Please load an image first.")
            return

        # If dialog already exists and is visible, just raise it
        if hasattr(self, "_solar_disk_dialog") and self._solar_disk_dialog is not None:
            try:
                if self._solar_disk_dialog.isVisible():
                    self._solar_disk_dialog.raise_()
                    self._solar_disk_dialog.activateWindow()
                    return
            except RuntimeError:
                self._solar_disk_dialog = None

        height, width = self.current_image_data.shape

        dialog = QDialog(self)
        dialog.setWindowTitle("Solar Disk Settings")
        set_hand_cursor(dialog)
        dialog.setMinimumWidth(400)
        layout = QVBoxLayout(dialog)

        # Store reference to prevent garbage collection
        self._solar_disk_dialog = dialog

        # Create tab widget for organizing settings
        tab_widget = QTabWidget()

        # Style tab (formerly Appearance tab)
        style_tab = QWidget()
        style_layout = QVBoxLayout(style_tab)

        # Color selection
        color_group = QGroupBox("Color")
        color_layout = QHBoxLayout(color_group)

        color_label = QLabel("Disk Color:")
        color_combo = QComboBox()
        colors = ["yellow", "white", "red", "green", "blue", "cyan", "magenta", "black"]
        for color in colors:
            color_combo.addItem(color)
        color_combo.setCurrentText(self.solar_disk_style["color"])
        color_layout.addWidget(color_label)
        color_layout.addWidget(color_combo)
        style_layout.addWidget(color_group)

        # Line style
        line_group = QGroupBox("Line Style")
        line_layout = QGridLayout(line_group)

        # Line style
        linestyle_label = QLabel("Line Style:")
        linestyle_combo = QComboBox()
        linestyles = [
            ("-", "Solid"),
            ("--", "Dashed"),
            (":", "Dotted"),
            ("-.", "Dash-dot"),
        ]
        for style_code, style_name in linestyles:
            linestyle_combo.addItem(style_name, style_code)

        # Set current line style
        current_style = self.solar_disk_style["linestyle"]
        for i in range(linestyle_combo.count()):
            if linestyle_combo.itemData(i) == current_style:
                linestyle_combo.setCurrentIndex(i)
                break

        line_layout.addWidget(linestyle_label, 0, 0)
        line_layout.addWidget(linestyle_combo, 0, 1)

        # Line width
        linewidth_label = QLabel("Line Width:")
        linewidth_spinbox = QDoubleSpinBox()
        linewidth_spinbox.setRange(0.5, 5.0)
        linewidth_spinbox.setSingleStep(0.5)
        linewidth_spinbox.setValue(self.solar_disk_style["linewidth"])
        line_layout.addWidget(linewidth_label, 1, 0)
        line_layout.addWidget(linewidth_spinbox, 1, 1)

        # Alpha/transparency
        alpha_label = QLabel("Opacity:")
        alpha_spinbox = QDoubleSpinBox()
        alpha_spinbox.setRange(0.1, 1.0)
        alpha_spinbox.setSingleStep(0.1)
        alpha_spinbox.setValue(self.solar_disk_style["alpha"])
        line_layout.addWidget(alpha_label, 2, 0)
        line_layout.addWidget(alpha_spinbox, 2, 1)

        style_layout.addWidget(line_group)

        # Center marker toggle
        center_marker_group = QGroupBox("Center Marker")
        center_marker_layout = QVBoxLayout(center_marker_group)

        # Add a checkbox to toggle the center marker
        show_center_checkbox = QCheckBox("Show center marker (+)")
        # Initialize checkbox state - if not in the dictionary, default to True
        if "show_center" not in self.solar_disk_style:
            self.solar_disk_style["show_center"] = True
        show_center_checkbox.setChecked(self.solar_disk_style["show_center"])
        center_marker_layout.addWidget(show_center_checkbox)

        style_layout.addWidget(center_marker_group)
        style_layout.addStretch()

        # Position tab
        position_tab = QWidget()
        position_layout = QVBoxLayout(position_tab)

        # Center coordinates
        center_group = QGroupBox("Disk Center")
        center_layout = QHBoxLayout(center_group)

        x_label = QLabel("X coordinate:")
        x_spinbox = QSpinBox()
        x_spinbox.setRange(0, width - 1)
        if self.solar_disk_center is not None:
            x_spinbox.setValue(self.solar_disk_center[0])
        else:
            x_spinbox.setValue(width // 2)
        center_layout.addWidget(x_label)
        center_layout.addWidget(x_spinbox)

        y_label = QLabel("Y coordinate:")
        y_spinbox = QSpinBox()
        y_spinbox.setRange(0, height - 1)
        if self.solar_disk_center is not None:
            y_spinbox.setValue(self.solar_disk_center[1])
        else:
            y_spinbox.setValue(height // 2)
        center_layout.addWidget(y_label)
        center_layout.addWidget(y_spinbox)

        position_layout.addWidget(center_group)

        # Diameter
        size_group = QGroupBox("Disk Size")
        size_layout = QHBoxLayout(size_group)
        diameter_label = QLabel("Diameter (arcmin):")
        diameter_spinbox = QSpinBox()
        diameter_spinbox.setRange(1, 100)
        diameter_spinbox.setValue(int(self.solar_disk_diameter_arcmin))
        size_layout.addWidget(diameter_label)
        size_layout.addWidget(diameter_spinbox)
        position_layout.addWidget(size_group)

        position_layout.addStretch()

        # Add tabs to tab widget - Style first, then Position
        tab_widget.addTab(style_tab, "Style")
        tab_widget.addTab(position_tab, "Configure")

        layout.addWidget(tab_widget)

        # Store reference to viewer for callbacks
        viewer = self

        def on_apply():
            """Apply settings without closing dialog."""
            try:
                if not viewer or viewer.current_image_data is None:
                    dialog.close()
                    return

                viewer.solar_disk_center = (x_spinbox.value(), y_spinbox.value())
                viewer.solar_disk_diameter_arcmin = float(diameter_spinbox.value())

                # Update style properties
                viewer.solar_disk_style["color"] = color_combo.currentText()
                viewer.solar_disk_style["linestyle"] = linestyle_combo.currentData()
                viewer.solar_disk_style["linewidth"] = linewidth_spinbox.value()
                viewer.solar_disk_style["alpha"] = alpha_spinbox.value()
                viewer.solar_disk_style["show_center"] = (
                    show_center_checkbox.isChecked()
                )

                viewer.schedule_plot()
                viewer.show_status_message("Solar disk settings applied")
            except RuntimeError:
                dialog.close()
            except Exception as e:
                try:
                    viewer.show_status_message(f"Error applying settings: {e}")
                except:
                    pass

        def on_close():
            dialog.close()

        def on_dialog_destroyed():
            if hasattr(viewer, "_solar_disk_dialog"):
                viewer._solar_disk_dialog = None

        # Create button box with Apply and Close
        button_box = QDialogButtonBox()
        apply_btn = button_box.addButton("Apply", QDialogButtonBox.ApplyRole)
        close_btn = button_box.addButton("Close", QDialogButtonBox.RejectRole)
        apply_btn.clicked.connect(on_apply)
        close_btn.clicked.connect(on_close)
        layout.addWidget(button_box)

        # Set up for non-modal behavior
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(on_dialog_destroyed)

        # Track dialog for garbage collection
        dialog.destroyed.connect(
            lambda: (
                self._open_dialogs.remove(dialog)
                if dialog in self._open_dialogs
                else None
            )
        )
        self._open_dialogs.append(dialog)

        dialog.show()

    def show_beam_settings(self):
        """Show non-modal beam settings dialog."""
        from .dialogs import BeamSettingsDialog

        # If dialog already exists and is visible, just raise it
        if (
            hasattr(self, "_beam_settings_dialog")
            and self._beam_settings_dialog is not None
        ):
            try:
                if self._beam_settings_dialog.isVisible():
                    self._beam_settings_dialog.raise_()
                    self._beam_settings_dialog.activateWindow()
                    return
            except RuntimeError:
                self._beam_settings_dialog = None

        dialog = BeamSettingsDialog(self, beam_style=self.beam_style)
        self._beam_settings_dialog = dialog

        # Set up for non-modal behavior
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(lambda: setattr(self, "_beam_settings_dialog", None))

        # Track dialog for garbage collection
        dialog.destroyed.connect(
            lambda: (
                self._open_dialogs.remove(dialog)
                if dialog in self._open_dialogs
                else None
            )
        )
        self._open_dialogs.append(dialog)

        dialog.show()

    def show_grid_settings(self):
        """Show non-modal grid settings dialog."""
        from .dialogs import GridSettingsDialog

        # If dialog already exists and is visible, just raise it
        if (
            hasattr(self, "_grid_settings_dialog")
            and self._grid_settings_dialog is not None
        ):
            try:
                if self._grid_settings_dialog.isVisible():
                    self._grid_settings_dialog.raise_()
                    self._grid_settings_dialog.activateWindow()
                    return
            except RuntimeError:
                self._grid_settings_dialog = None

        dialog = GridSettingsDialog(self, grid_style=self.grid_style)
        self._grid_settings_dialog = dialog

        # Set up for non-modal behavior
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(lambda: setattr(self, "_grid_settings_dialog", None))

        # Track dialog for garbage collection
        dialog.destroyed.connect(
            lambda: (
                self._open_dialogs.remove(dialog)
                if dialog in self._open_dialogs
                else None
            )
        )
        self._open_dialogs.append(dialog)

        dialog.show()

    def zoom_in(self):
        if self.current_image_data is None:
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        ax = self.figure.axes[0]
        xlim = ax.get_xlim()
        ylim = ax.get_ylim()

        xcenter = (xlim[0] + xlim[1]) / 2
        ycenter = (ylim[0] + ylim[1]) / 2

        width = (xlim[1] - xlim[0]) / 2
        height = (ylim[1] - ylim[0]) / 2

        ax.set_xlim(xcenter - width / 2, xcenter + width / 2)
        ax.set_ylim(ycenter - height / 2, ycenter + height / 2)

        self._update_beam_position(ax)
        # If solar disk checkbox is checked, draw the solar disk
        if self.show_solar_disk_checkbox.isChecked():
            self._update_solar_disk_position(ax)
        self.canvas.draw()
        QApplication.restoreOverrideCursor()
        self.show_status_message("Zoomed in")

    def zoom_out(self):
        if self.current_image_data is None:
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        ax = self.figure.axes[0]
        xlim = ax.get_xlim()
        ylim = ax.get_ylim()

        xcenter = (xlim[0] + xlim[1]) / 2
        ycenter = (ylim[0] + ylim[1]) / 2

        width = (xlim[1] - xlim[0]) * 2
        height = (ylim[1] - ylim[0]) * 2

        ax.set_xlim(xcenter - width / 2, xcenter + width / 2)
        ax.set_ylim(ycenter - height / 2, ycenter + height / 2)

        self._update_beam_position(ax)
        # If solar disk checkbox is checked, draw the solar disk
        if self.show_solar_disk_checkbox.isChecked():
            self._update_solar_disk_position(ax)
        self.canvas.draw()
        QApplication.restoreOverrideCursor()
        self.show_status_message("Zoomed out")

    def zoom_60arcmin(self):
        if self.current_image_data is None or self.current_wcs is None:
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            ax = self.figure.axes[0]
            xlim = ax.get_xlim()
            ylim = ax.get_ylim()
            xcenter = (xlim[0] + xlim[1]) / 2
            ycenter = (ylim[0] + ylim[1]) / 2

            cdelt = self.current_wcs.increment()["numeric"][0:2]
            if isinstance(cdelt, list):
                cdelt = [float(c) for c in cdelt]
            cdelt = np.array(cdelt) * 180 / np.pi
            arcmin_60_deg = 60.0 / 60.0
            pixels_x = arcmin_60_deg / abs(cdelt[0])
            pixels_y = arcmin_60_deg / abs(cdelt[1])

            ax.set_xlim(xcenter - pixels_x / 2, xcenter + pixels_x / 2)
            ax.set_ylim(ycenter - pixels_y / 2, ycenter + pixels_y / 2)

            self._update_beam_position(ax)
            # If solar disk checkbox is checked, draw the solar disk
            if self.show_solar_disk_checkbox.isChecked():
                self._update_solar_disk_position(ax)
            self.canvas.draw()
            self.show_status_message("Zoomed to 1°×1°")
        except Exception as e:
            print(f"[ERROR] Error in zoom_60arcmin: {e}")
            self.show_status_message(f"Error in zoom_60arcmin: {e}")
        finally:
            QApplication.restoreOverrideCursor()

    def init_region_editor(self, ax):
        from matplotlib.widgets import RectangleSelector, EllipseSelector

        if self.roi_selector:
            self.roi_selector.disconnect_events()
            self.roi_selector = None

        # Choose selector based on region mode
        if hasattr(self, "region_mode") and self.region_mode == RegionMode.ELLIPSE:
            self.roi_selector = EllipseSelector(
                ax, self.on_ellipse, useblit=True, button=[1], interactive=True
            )
        else:
            self.roi_selector = RectangleSelector(
                ax, self.on_rectangle, useblit=True, button=[1], interactive=True
            )
        self.canvas.mpl_connect("motion_notify_event", self.on_mouse_move)

    def on_rectangle(self, eclick, erelease):
        if self.current_image_data is None:
            return
        try:
            x1, x2 = int(eclick.xdata), int(erelease.xdata)
            y1, y2 = int(eclick.ydata), int(erelease.ydata)
        except:
            return

        xlow, xhigh = sorted([x1, x2])
        ylow, yhigh = sorted([y1, y2])

        xlow = max(0, xlow)
        ylow = max(0, ylow)
        xhigh = min(self.current_image_data.shape[0], xhigh)
        yhigh = min(self.current_image_data.shape[1], yhigh)

        self.current_roi = (xlow, xhigh, ylow, yhigh)
        roi = self.current_image_data[xlow:xhigh, ylow:yhigh]

        ra_dec_info = ""
        if self.current_wcs:
            try:
                from astropy.wcs import WCS
                from astropy.coordinates import SkyCoord
                import astropy.units as u

                ref_val = self.current_wcs.referencevalue()["numeric"][0:2]
                ref_pix = self.current_wcs.referencepixel()["numeric"][0:2]
                increment = self.current_wcs.increment()["numeric"][0:2]

                w = WCS(naxis=2)
                w.wcs.crpix = ref_pix
                w.wcs.crval = [ref_val[0] * 180 / np.pi, ref_val[1] * 180 / np.pi]
                w.wcs.cdelt = [increment[0] * 180 / np.pi, increment[1] * 180 / np.pi]

                ra1, dec1 = w.wcs_pix2world(xlow, ylow, 0)
                ra2, dec2 = w.wcs_pix2world(xhigh, yhigh, 0)

                coord1 = SkyCoord(ra=ra1 * u.degree, dec=dec1 * u.degree)
                coord2 = SkyCoord(ra=ra2 * u.degree, dec=dec2 * u.degree)

                center_ra = (ra1 + ra2) / 2
                center_dec = (dec1 + dec2) / 2
                center_coord = SkyCoord(
                    ra=center_ra * u.degree, dec=center_dec * u.degree
                )

                width = abs(ra2 - ra1) * u.degree
                height = abs(dec2 - dec1) * u.degree

                ra_dec_info = (
                    f"Center: RA={center_coord.ra.to_string(unit=u.hour, sep=':', precision=1)}, "
                    f"Dec={center_coord.dec.to_string(sep=':', precision=1)}"
                    f"\nSize: {width.to(u.arcsec):.1f} × {height.to(u.arcsec):.1f}"
                    # f"\nCorners: "
                    # f"\n  Bottom-Left: RA={coord1.ra.to_string(unit=u.hour, sep=':', precision=2)}, "
                    # f"Dec={coord1.dec.to_string(sep=':', precision=2)}"
                    # f"\n  Top-Right: RA={coord2.ra.to_string(unit=u.hour, sep=':', precision=2)}, "
                    # f"Dec={coord2.dec.to_string(sep=':', precision=2)}"
                )
            except Exception as e:
                ra_dec_info = f"\nRA/Dec conversion error: {str(e)}"

        self.show_roi_stats(roi, ra_dec_info)

    def on_ellipse(self, eclick, erelease):
        """Handle ellipse selection for ROI statistics"""
        if self.current_image_data is None:
            return
        try:
            x1, x2 = eclick.xdata, erelease.xdata
            y1, y2 = eclick.ydata, erelease.ydata
        except:
            return

        # Calculate ellipse parameters in display coordinates
        center_x = (x1 + x2) / 2
        center_y = (y1 + y2) / 2
        width = abs(x2 - x1)
        height = abs(y2 - y1)

        if width < 1 or height < 1:
            return

        # The displayed image is transposed from current_image_data.
        # Display: transposed_data[x_display, y_display] = current_image_data[y_display, x_display]
        # So matplotlib's xdata corresponds to axis 0 (rows) and ydata to axis 1 (cols) in current_image_data.
        # We need to swap coordinates for the mask.
        data_center_row = center_x  # display x -> data row (axis 0)
        data_center_col = center_y  # display y -> data col (axis 1)
        data_height = width  # display width -> data height (axis 0 extent)
        data_width = height  # display height -> data width (axis 1 extent)

        # Create ellipse mask on current_image_data
        h, w = self.current_image_data.shape  # h = rows (axis 0), w = cols (axis 1)
        row_coords, col_coords = np.ogrid[:h, :w]

        # Ellipse equation: ((col - center_col) / a)^2 + ((row - center_row) / b)^2 <= 1
        a = data_width / 2  # semi-axis along columns (axis 1)
        b = data_height / 2  # semi-axis along rows (axis 0)
        mask = ((col_coords - data_center_col) / a) ** 2 + (
            (row_coords - data_center_row) / b
        ) ** 2 <= 1

        # Get pixels within ellipse
        roi_data = self.current_image_data[mask]

        if roi_data.size == 0:
            return

        # Store ellipse ROI info as bounding box for compatibility
        # current_roi uses data coordinates: (row_low, row_high, col_low, col_high)
        row_low, row_high = int(data_center_row - b), int(data_center_row + b)
        col_low, col_high = int(data_center_col - a), int(data_center_col + a)
        self.current_roi = (
            max(0, row_low),
            min(h, row_high),
            max(0, col_low),
            min(w, col_high),
        )

        ra_dec_info = ""
        if self.current_wcs:
            try:
                from astropy.wcs import WCS
                from astropy.coordinates import SkyCoord
                import astropy.units as u

                ref_val = self.current_wcs.referencevalue()["numeric"][0:2]
                ref_pix = self.current_wcs.referencepixel()["numeric"][0:2]
                increment = self.current_wcs.increment()["numeric"][0:2]

                wcs = WCS(naxis=2)
                wcs.wcs.crpix = ref_pix
                wcs.wcs.crval = [ref_val[0] * 180 / np.pi, ref_val[1] * 180 / np.pi]
                wcs.wcs.cdelt = [increment[0] * 180 / np.pi, increment[1] * 180 / np.pi]

                # Convert display coordinates to world coordinates
                # center_x, center_y are in display coordinates (used for WCS)
                ra_center, dec_center = wcs.wcs_pix2world(center_x, center_y, 0)
                center_coord = SkyCoord(
                    ra=ra_center * u.degree, dec=dec_center * u.degree
                )

                # Angular size of ellipse (display width/height)
                angular_width = abs(width * increment[0] * 180 / np.pi) * 3600  # arcsec
                angular_height = (
                    abs(height * increment[1] * 180 / np.pi) * 3600
                )  # arcsec

                ra_dec_info = (
                    f"Center: RA={center_coord.ra.to_string(unit=u.hour, sep=':', precision=1)}, "
                    f"Dec={center_coord.dec.to_string(sep=':', precision=1)}"
                    f'\nSize: {angular_width:.1f}" × {angular_height:.1f}"'
                )
            except Exception as e:
                ra_dec_info = f"\nRA/Dec conversion error: {str(e)}"

        self.show_roi_stats(roi_data, ra_dec_info)

    def on_mouse_move(self, event):
        if not event.inaxes or self.current_image_data is None:
            return

        x, y = round(event.xdata), round(event.ydata)

        try:
            value = self.current_image_data[x, y]
            pixel_info = f"<b>Pixel:</b> X={x}, Y={y}<br><b>Value:</b> {value:.3g}"
        except (IndexError, TypeError):
            pixel_info = f"<b>Pixel:</b> X={x}, Y={y}"

        wcs_obj = getattr(self, "_cached_wcs_obj", None)
        if wcs_obj is not None:
            try:
                from astropy.coordinates import SkyCoord
                import astropy.units as u

                ra, dec = wcs_obj.wcs_pix2world(event.xdata, event.ydata, 0)
                coord = SkyCoord(ra=ra * u.degree, dec=dec * u.degree)
                ra_str = coord.ra.to_string(unit=u.hour, sep=":", precision=1)
                dec_str = coord.dec.to_string(sep=":", precision=1)

                coord_info = f"{pixel_info}<br><b>World:</b> RA={ra_str}, Dec={dec_str}"
                self.coord_label.setText(coord_info)
            except Exception as e:
                self.coord_label.setText(f"{pixel_info}<br><b>WCS Error:</b> {str(e)}")
        elif self.current_wcs:
            try:
                from astropy.wcs import WCS
                from astropy.coordinates import SkyCoord
                import astropy.units as u

                ref_val = self.current_wcs.referencevalue()["numeric"][0:2]
                ref_pix = self.current_wcs.referencepixel()["numeric"][0:2]
                increment = self.current_wcs.increment()["numeric"][0:2]

                w = WCS(naxis=2)
                w.wcs.crpix = ref_pix
                w.wcs.crval = [ref_val[0] * 180 / np.pi, ref_val[1] * 180 / np.pi]
                w.wcs.cdelt = [increment[0] * 180 / np.pi, increment[1] * 180 / np.pi]
                w.wcs.ctype = ["RA---SIN", "DEC--SIN"]

                ra, dec = w.wcs_pix2world(event.xdata, event.ydata, 0)
                coord = SkyCoord(ra=ra * u.degree, dec=dec * u.degree)
                ra_str = coord.ra.to_string(unit=u.hour, sep=":", precision=1)
                dec_str = coord.dec.to_string(sep=":", precision=1)

                coord_info = f"{pixel_info}<br><b>World:</b> RA={ra_str}, Dec={dec_str}"
                self.coord_label.setText(coord_info)
            except Exception as e:
                self.coord_label.setText(f"{pixel_info}<br><b>WCS Error:</b> {str(e)}")
        else:
            self.coord_label.setText(pixel_info)

    def setup_canvas(self, parent_layout):
        self.figure = Figure(figsize=(5, 5), dpi=100)
        self.canvas = FigureCanvas(self.figure)

        # Ensure canvas background matches theme to avoid white gaps when scaling
        self.canvas.setStyleSheet("background-color: transparent;")

        self.nav_toolbar = NavigationToolbar(self.canvas, self)
        # Make the matplotlib toolbar more compact
        self.nav_toolbar.setFixedHeight(24)
        self.nav_toolbar.setStyleSheet(
            """
            NavigationToolbar2QT { padding: 0px; margin: 0px; spacing: 1px; background-color: transparent; }
            QToolButton { padding: 2px; margin: 0px; }
        """
        )
        parent_layout.addWidget(self.nav_toolbar)
        parent_layout.addWidget(self.canvas, 1)

        self.current_image_data = None
        self.current_wcs = None
        self.psf = None
        self.current_roi = None
        self.roi_selector = None
        self.imagename = None

        self.solar_disk_center = None
        self.solar_disk_diameter_arcmin = 32.0

        self.canvas.mpl_connect("motion_notify_event", self.on_mouse_move)

    def show_contour_settings(self):
        """Show non-modal contour settings dialog."""
        from .dialogs import ContourSettingsDialog

        # If dialog already exists and is visible, just raise it
        if (
            hasattr(self, "_contour_settings_dialog")
            and self._contour_settings_dialog is not None
        ):
            try:
                if self._contour_settings_dialog.isVisible():
                    self._contour_settings_dialog.raise_()
                    self._contour_settings_dialog.activateWindow()
                    return
            except RuntimeError:
                self._contour_settings_dialog = None

        dialog = ContourSettingsDialog(self, self.contour_settings)
        self._contour_settings_dialog = dialog

        # Store reference to viewer
        viewer = self

        def on_apply():
            """Apply settings without closing dialog."""
            try:
                if not viewer:
                    dialog.close()
                    return
                viewer.contour_settings = dialog.get_settings()
                if viewer.show_contours_checkbox.isChecked():
                    viewer.load_contour_data()
                    viewer.on_visualization_changed()
                    
                    # Update dialog with captured dimensions
                    w = viewer.contour_settings.get("dim_w", 0)
                    h = viewer.contour_settings.get("dim_h", 0)
                    if hasattr(dialog, "update_dimensions_label"):
                        dialog.update_dimensions_label(w, h)

                viewer.show_status_message("Contour settings applied")
            except RuntimeError:
                dialog.close()
            except Exception as e:
                viewer.show_status_message(f"Error applying settings: {e}")

        def on_close():
            dialog.close()

        def on_dialog_destroyed():
            if hasattr(viewer, "_contour_settings_dialog"):
                viewer._contour_settings_dialog = None

        # Replace the standard button box with Apply/Close
        # The dialog already has a button box, so we need to modify it
        button_box = dialog.findChild(QDialogButtonBox)
        if button_box:
            button_box.clear()
            apply_btn = button_box.addButton("Apply", QDialogButtonBox.ApplyRole)
            close_btn = button_box.addButton("Close", QDialogButtonBox.RejectRole)
            apply_btn.clicked.connect(on_apply)
            close_btn.clicked.connect(on_close)

        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(on_dialog_destroyed)

        # Track dialog for garbage collection
        dialog.destroyed.connect(
            lambda: (
                self._open_dialogs.remove(dialog)
                if dialog in self._open_dialogs
                else None
            )
        )
        self._open_dialogs.append(dialog)

        dialog.show()

    def show_plot_customization_dialog(self):
        """Open a non-modal plot customization dialog."""
        from .dialogs import PlotCustomizationDialog

        # If dialog already exists and is visible, just raise it
        if (
            hasattr(self, "_plot_customization_dialog")
            and self._plot_customization_dialog is not None
        ):
            try:
                if self._plot_customization_dialog.isVisible():
                    self._plot_customization_dialog.raise_()
                    self._plot_customization_dialog.activateWindow()
                    return
            except RuntimeError:
                self._plot_customization_dialog = None

        dialog = PlotCustomizationDialog(self, self.plot_settings)
        self._plot_customization_dialog = dialog

        # Store reference to viewer
        viewer = self

        def on_apply():
            """Apply settings without closing dialog."""
            try:
                if not viewer:
                    dialog.close()
                    return
                viewer.plot_settings = dialog.get_settings()
                # Refresh the plot with new settings
                if viewer.current_image_data is not None:
                    try:
                        vmin_val = float(viewer.vmin_entry.text())
                        vmax_val = float(viewer.vmax_entry.text())
                        stretch = viewer.stretch_combo.currentText()
                        cmap = viewer.cmap_combo.currentText()
                        gamma = float(viewer.gamma_entry.text())
                        viewer.plot_image(vmin_val, vmax_val, stretch, cmap, gamma)
                    except (ValueError, AttributeError):
                        viewer.plot_image()
                viewer.show_status_message("Plot settings applied")
            except RuntimeError:
                dialog.close()
            except Exception as e:
                viewer.show_status_message(f"Error applying settings: {e}")

        def on_close():
            dialog.close()

        def on_dialog_destroyed():
            if hasattr(viewer, "_plot_customization_dialog"):
                viewer._plot_customization_dialog = None

        # Replace the standard button box with Apply/Close
        button_box = dialog.findChild(QDialogButtonBox)
        if button_box:
            button_box.clear()
            apply_btn = button_box.addButton("Apply", QDialogButtonBox.ApplyRole)
            close_btn = button_box.addButton("Close", QDialogButtonBox.RejectRole)
            apply_btn.clicked.connect(on_apply)
            close_btn.clicked.connect(on_close)

        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(on_dialog_destroyed)

        # Track dialog for garbage collection
        dialog.destroyed.connect(
            lambda: (
                self._open_dialogs.remove(dialog)
                if dialog in self._open_dialogs
                else None
            )
        )
        self._open_dialogs.append(dialog)

        dialog.show()

    def load_contour_data(self):
        try:
            if self.contour_settings["source"] == "external":
                h, w = self._get_image_dimensions(self.contour_settings["external_image"])
            else:
                h, w = self._get_image_dimensions(self.imagename)
            
            if h >0 and w >0:
                self.contour_settings["dim_h"] = h
                self.contour_settings["dim_w"] = w
                # use default region logic
                rms_x1 = int(w * 0.05)
                rms_y1 = int(h * 0.05)
                rms_x2 = int(w * 0.95)
                rms_y2 = int(h * 0.20)
                rms_box = (rms_x1, rms_x2, rms_y1, rms_y2)

                # Ensure valid region
                if rms_x2 < rms_x1 or rms_y2 < rms_y1:
                    print("[Warning] Invalid RMS region. Using default RMS region.")
                    rms_box = (30, 200, 30, 130)
            else:
                print("[Warning] Invalid image dimensions. Using default RMS region.")
                rms_box = (30, 200, 30, 130)

            # Calculate target size for fast load mode (must match load_data)
            target_size = 0  # 0 = no downsampling
            if (
                hasattr(self, "downsample_toggle")
                and self.downsample_toggle.isChecked()
            ):
                target_size = 800  # Smart downsampling to ~800px max dimension
                rms_box = (40, 400, 40, 160)

            if not self.contour_settings.get("use_default_rms_region", True):
                rms_box = self.contour_settings.get("rms_box", rms_box)
            else:
                self.contour_settings["rms_box"] = rms_box

            contour_csys = None

            # Validate contour Stokes before loading
            contour_stokes = self.contour_settings.get("stokes", "I")
            source_image = None

            if self.contour_settings["source"] == "same":
                source_image = self.imagename
            else:
                source_image = self.contour_settings.get("external_image")

            if source_image:
                from .utils import get_available_stokes

                available_stokes = get_available_stokes(source_image)

                # Check if contour Stokes is valid
                requires_q = {"Q", "Q/I", "L", "Lfrac", "PANG"}
                requires_u = {"U", "U/I", "V/I", "L", "Lfrac", "PANG"}
                requires_v = {"V", "Vfrac", "V/I"}

                has_q = "Q" in available_stokes
                has_u = "U" in available_stokes
                has_v = "V" in available_stokes

                needs_switch = False
                missing_stokes = []

                if contour_stokes in requires_q and not has_q:
                    needs_switch = True
                    missing_stokes.append("Q")
                if contour_stokes in requires_u and not has_u:
                    needs_switch = True
                    missing_stokes.append("U")
                if contour_stokes in requires_v and not has_v:
                    needs_switch = True
                    missing_stokes.append("V")

                if needs_switch:
                    # Show warning dialog and switch to Stokes I
                    QMessageBox.warning(
                        self,
                        "Contour Stokes Unavailable",
                        f"The contour Stokes parameter '{contour_stokes}' requires "
                        f"polarization data ({', '.join(set(missing_stokes))}) that is not available "
                        f"in the source image.\n\n"
                        f"Available Stokes: {', '.join(available_stokes)}\n\n"
                        f"Switching contour to Stokes I.",
                    )
                    self.contour_settings["stokes"] = "I"
                    contour_stokes = "I"

            if self.contour_settings["source"] == "same":
                if self.imagename:
                    stokes = self.contour_settings["stokes"]
                    threshold = self.contour_settings.get("threshold", 5.0)

                    if stokes in ["I", "Q", "U", "V"]:
                        pix, contour_csys, _ = get_pixel_values_from_image(
                            self.imagename,
                            stokes,
                            threshold,
                            rms_box,
                            target_size=target_size,
                        )
                        self.contour_settings["contour_data"] = pix
                    elif stokes in ["Q/I", "U/I", "V/I"]:
                        from .utils import estimate_rms_near_Sun

                        numerator_stokes = stokes.split("/")[0]
                        numerator_pix, contour_csys, _ = get_pixel_values_from_image(
                            self.imagename,
                            numerator_stokes,
                            threshold,
                            rms_box,
                            target_size=target_size,
                        )
                        denominator_pix, contour_csys, _ = get_pixel_values_from_image(
                            self.imagename,
                            "I",
                            threshold,
                            rms_box,
                            target_size=target_size,
                        )
                        # Estimate RMS of the numerator Stokes for noise thresholding
                        try:
                            num_rms = estimate_rms_near_Sun(
                                self.imagename, numerator_stokes, rms_box
                            )
                        except Exception:
                            print(
                                "[WARNING] Failed to estimate RMS, using standard deviation instead"
                            )
                            num_rms = np.nanstd(numerator_pix)

                        # Create mask: pixels below threshold * RMS are masked out
                        noise_mask = np.abs(numerator_pix) < (threshold * num_rms)
                        numerator_pix_masked = numerator_pix.copy()
                        numerator_pix_masked[noise_mask] = 0

                        # Division mask: avoid divide by zero
                        div_mask = denominator_pix != 0
                        ratio = np.zeros_like(numerator_pix)
                        ratio[div_mask] = (
                            numerator_pix_masked[div_mask] / denominator_pix[div_mask]
                        )
                        self.contour_settings["contour_data"] = ratio
                    elif stokes == "L":
                        q_pix, contour_csys, _ = get_pixel_values_from_image(
                            self.imagename,
                            "Q",
                            threshold,
                            rms_box,
                            target_size=target_size,
                        )
                        u_pix, contour_csys, _ = get_pixel_values_from_image(
                            self.imagename,
                            "U",
                            threshold,
                            rms_box,
                            target_size=target_size,
                        )
                        l_pix = np.sqrt(q_pix**2 + u_pix**2)
                        self.contour_settings["contour_data"] = l_pix
                    elif stokes == "Lfrac":
                        from .utils import estimate_rms_near_Sun

                        q_pix, contour_csys, _ = get_pixel_values_from_image(
                            self.imagename,
                            "Q",
                            threshold,
                            rms_box,
                            target_size=target_size,
                        )
                        u_pix, contour_csys, _ = get_pixel_values_from_image(
                            self.imagename,
                            "U",
                            threshold,
                            rms_box,
                            target_size=target_size,
                        )
                        i_pix, contour_csys, _ = get_pixel_values_from_image(
                            self.imagename,
                            "I",
                            threshold,
                            rms_box,
                            target_size=target_size,
                        )
                        l_pix = np.sqrt(q_pix**2 + u_pix**2)

                        # Estimate RMS for polarized intensity
                        try:
                            q_rms = estimate_rms_near_Sun(self.imagename, "Q", rms_box)
                            u_rms = estimate_rms_near_Sun(self.imagename, "U", rms_box)
                            l_rms = np.sqrt(q_rms**2 + u_rms**2)
                        except Exception:
                            print(
                                "[WARNING] Failed to estimate RMS, using standard deviation instead"
                            )
                            l_rms = np.nanstd(l_pix)

                        # Mask pixels below threshold
                        noise_mask = l_pix < (threshold * l_rms)
                        l_pix_masked = l_pix.copy()
                        l_pix_masked[noise_mask] = 0

                        # Division with zero protection
                        div_mask = i_pix != 0
                        lfrac = np.zeros_like(l_pix)
                        lfrac[div_mask] = l_pix_masked[div_mask] / i_pix[div_mask]
                        self.contour_settings["contour_data"] = lfrac
                    elif stokes == "PANG":
                        from .utils import estimate_rms_near_Sun

                        q_pix, contour_csys, _ = get_pixel_values_from_image(
                            self.imagename,
                            "Q",
                            threshold,
                            rms_box,
                            target_size=target_size,
                        )
                        u_pix, contour_csys, _ = get_pixel_values_from_image(
                            self.imagename,
                            "U",
                            threshold,
                            rms_box,
                            target_size=target_size,
                        )

                        # Calculate polarized intensity for thresholding
                        l_pix = np.sqrt(q_pix**2 + u_pix**2)

                        # Estimate RMS for polarized intensity
                        try:
                            q_rms = estimate_rms_near_Sun(self.imagename, "Q", rms_box)
                            u_rms = estimate_rms_near_Sun(self.imagename, "U", rms_box)
                            l_rms = np.sqrt(q_rms**2 + u_rms**2)
                        except Exception:
                            print(
                                "[WARNING] Failed to estimate RMS, using standard deviation instead"
                            )
                            l_rms = np.nanstd(l_pix)

                        # Polarization angle
                        pang = 0.5 * np.arctan2(u_pix, q_pix) * 180 / np.pi

                        # Mask pixels below threshold with NaN
                        noise_mask = l_pix < (threshold * l_rms)
                        pang[noise_mask] = np.nan
                        self.contour_settings["contour_data"] = pang
                    else:
                        pix, contour_csys, _ = get_pixel_values_from_image(
                            self.imagename,
                            "I",
                            threshold,
                            rms_box,
                            target_size=target_size,
                        )
                        self.contour_settings["contour_data"] = pix
                    self.current_contour_wcs = contour_csys
                else:
                    self.contour_settings["contour_data"] = None
                    self.current_contour_wcs = None
            else:
                external_image = self.contour_settings["external_image"]
                if external_image and os.path.exists(external_image):
                    stokes = self.contour_settings["stokes"]
                    threshold = self.contour_settings.get("threshold", 5.0)

                    # Check if we need to transform coordinate systems
                    # Detect coordinate systems of base image and contour image
                    base_csys_type = self._get_coordinate_system_from_image(
                        self.imagename
                    )
                    contour_csys_type = self._get_coordinate_system_from_image(
                        external_image
                    )

                    # Use cached transformed file if available and from same source
                    image_to_load = external_image

                    if (
                        self._contour_transformed_file
                        and self._contour_transformed_from == external_image
                        and os.path.exists(self._contour_transformed_file)
                    ):
                        # Check if transformation is still needed
                        transformed_csys = self._get_coordinate_system_from_image(
                            self._contour_transformed_file
                        )
                        if transformed_csys == base_csys_type:
                            image_to_load = self._contour_transformed_file
                            self.show_status_message(
                                f"Using cached transformed contour ({contour_csys_type}→{base_csys_type})"
                            )
                        else:
                            # Transformation no longer matches (base image changed), cleanup and retransform
                            try:
                                os.remove(self._contour_transformed_file)
                            except Exception:
                                pass
                            self._contour_transformed_file = None
                            self._contour_transformed_from = None

                    # Check if transformation is needed
                    if (
                        base_csys_type != "unknown"
                        and contour_csys_type != "unknown"
                        and base_csys_type != contour_csys_type
                        and image_to_load == external_image
                    ):

                        # Clear old cached file if external image changed
                        if self._contour_transformed_from != external_image:
                            if self._contour_transformed_file and os.path.exists(
                                self._contour_transformed_file
                            ):
                                try:
                                    os.remove(self._contour_transformed_file)
                                except Exception:
                                    pass
                            self._contour_transformed_file = None
                            self._contour_transformed_from = None

                        # Create temp file for coordinate-transformed contour
                        import tempfile
                        import uuid

                        temp_dir = tempfile.gettempdir()
                        temp_filename = (
                            f"sv_contour_transformed_{self._temp_file_id}.fits"
                        )
                        transformed_path = os.path.join(temp_dir, temp_filename)

                        transform_success = False

                        if base_csys_type == "hpc" and contour_csys_type == "radec":
                            # Convert RA/Dec contour to HPC (all Stokes for full flexibility)
                            self.show_status_message(
                                f"Transforming contour from RA/Dec to HPC..."
                            )
                            from .helioprojective import convert_and_save_hpc_all_stokes

                            transform_success = convert_and_save_hpc_all_stokes(
                                external_image, transformed_path, overwrite=True
                            )

                        elif base_csys_type == "radec" and contour_csys_type == "hpc":
                            # Convert HPC contour to RA/Dec
                            self.show_status_message(
                                f"Transforming contour from HPC to RA/Dec..."
                            )
                            from .helioprojective import convert_hpc_to_radec

                            transform_success = convert_hpc_to_radec(
                                external_image, transformed_path, overwrite=True
                            )

                        if transform_success and os.path.exists(transformed_path):
                            image_to_load = transformed_path
                            self._contour_transformed_file = transformed_path
                            self._contour_transformed_from = external_image
                            self.show_status_message(
                                f"Contour transformed ({contour_csys_type}→{base_csys_type})"
                            )
                        else:
                            print(
                                f"[ERROR] Transformation failed, using original: {external_image}"
                            )
                            self.show_status_message(
                                f"Coordinate transformation failed, using original contour"
                            )

                    elif (
                        contour_csys_type != "unknown"
                        and base_csys_type == contour_csys_type
                        and self._contour_transformed_from == external_image
                    ):
                        # Coordinates now match (base image changed back), cleanup temp file
                        if self._contour_transformed_file and os.path.exists(
                            self._contour_transformed_file
                        ):
                            try:
                                os.remove(self._contour_transformed_file)
                            except Exception:
                                pass
                        self._contour_transformed_file = None
                        self._contour_transformed_from = None

                    if stokes in ["I", "Q", "U", "V"]:
                        pix, contour_csys, _ = get_pixel_values_from_image(
                            image_to_load,
                            stokes,
                            threshold,
                            rms_box,
                            target_size=target_size,
                        )
                        self.contour_settings["contour_data"] = pix

                    elif stokes in ["Q/I", "U/I", "V/I"]:
                        from .utils import estimate_rms_near_Sun

                        numerator_stokes = stokes.split("/")[0]
                        numerator_pix, contour_csys, _ = get_pixel_values_from_image(
                            image_to_load,
                            numerator_stokes,
                            threshold,
                            rms_box,
                            target_size=target_size,
                        )
                        denominator_pix, contour_csys, _ = get_pixel_values_from_image(
                            image_to_load,
                            "I",
                            threshold,
                            rms_box,
                            target_size=target_size,
                        )
                        # Estimate RMS of the numerator Stokes for noise thresholding
                        try:
                            num_rms = estimate_rms_near_Sun(
                                image_to_load, numerator_stokes, rms_box
                            )
                        except Exception:
                            print(
                                "[WARNING] Failed to estimate RMS, using standard deviation instead"
                            )
                            num_rms = np.nanstd(numerator_pix)

                        # Create mask: pixels below threshold * RMS are masked out
                        noise_mask = np.abs(numerator_pix) < (threshold * num_rms)
                        numerator_pix_masked = numerator_pix.copy()
                        numerator_pix_masked[noise_mask] = 0

                        # Division mask: avoid divide by zero
                        div_mask = denominator_pix != 0
                        ratio = np.zeros_like(numerator_pix)
                        ratio[div_mask] = (
                            numerator_pix_masked[div_mask] / denominator_pix[div_mask]
                        )
                        self.contour_settings["contour_data"] = ratio
                    elif stokes == "L":
                        q_pix, contour_csys, _ = get_pixel_values_from_image(
                            image_to_load,
                            "Q",
                            threshold,
                            rms_box,
                            target_size=target_size,
                        )
                        u_pix, contour_csys, _ = get_pixel_values_from_image(
                            image_to_load,
                            "U",
                            threshold,
                            rms_box,
                            target_size=target_size,
                        )
                        l_pix = np.sqrt(q_pix**2 + u_pix**2)
                        self.contour_settings["contour_data"] = l_pix
                    elif stokes == "Lfrac":
                        from .utils import estimate_rms_near_Sun

                        q_pix, contour_csys, _ = get_pixel_values_from_image(
                            image_to_load,
                            "Q",
                            threshold,
                            rms_box,
                            target_size=target_size,
                        )
                        u_pix, contour_csys, _ = get_pixel_values_from_image(
                            image_to_load,
                            "U",
                            threshold,
                            rms_box,
                            target_size=target_size,
                        )
                        i_pix, contour_csys, _ = get_pixel_values_from_image(
                            image_to_load,
                            "I",
                            threshold,
                            rms_box,
                            target_size=target_size,
                        )
                        l_pix = np.sqrt(q_pix**2 + u_pix**2)

                        # Estimate RMS for polarized intensity
                        try:
                            q_rms = estimate_rms_near_Sun(image_to_load, "Q", rms_box)
                            u_rms = estimate_rms_near_Sun(image_to_load, "U", rms_box)
                            l_rms = np.sqrt(q_rms**2 + u_rms**2)
                        except Exception:
                            print(
                                "[WARNING] Failed to estimate RMS, using standard deviation instead"
                            )
                            l_rms = np.nanstd(l_pix)

                        # Mask pixels below threshold
                        noise_mask = l_pix < (threshold * l_rms)
                        l_pix_masked = l_pix.copy()
                        l_pix_masked[noise_mask] = 0

                        # Division with zero protection
                        div_mask = i_pix != 0
                        lfrac = np.zeros_like(l_pix)
                        lfrac[div_mask] = l_pix_masked[div_mask] / i_pix[div_mask]
                        self.contour_settings["contour_data"] = lfrac
                    elif stokes == "PANG":
                        from .utils import estimate_rms_near_Sun

                        q_pix, contour_csys, _ = get_pixel_values_from_image(
                            image_to_load,
                            "Q",
                            threshold,
                            rms_box,
                            target_size=target_size,
                        )
                        u_pix, contour_csys, _ = get_pixel_values_from_image(
                            image_to_load,
                            "U",
                            threshold,
                            rms_box,
                            target_size=target_size,
                        )

                        # Calculate polarized intensity for thresholding
                        l_pix = np.sqrt(q_pix**2 + u_pix**2)

                        # Estimate RMS for polarized intensity
                        try:
                            q_rms = estimate_rms_near_Sun(image_to_load, "Q", rms_box)
                            u_rms = estimate_rms_near_Sun(image_to_load, "U", rms_box)
                            l_rms = np.sqrt(q_rms**2 + u_rms**2)
                        except Exception:
                            print(
                                "[WARNING] Failed to estimate RMS, using standard deviation instead"
                            )
                            l_rms = np.nanstd(l_pix)

                        # Polarization angle
                        pang = 0.5 * np.arctan2(u_pix, q_pix) * 180 / np.pi

                        # Mask pixels below threshold with NaN
                        noise_mask = l_pix < (threshold * l_rms)
                        pang[noise_mask] = np.nan
                        self.contour_settings["contour_data"] = pang
                    else:
                        pix, contour_csys, _ = get_pixel_values_from_image(
                            image_to_load,
                            "I",
                            threshold,
                            rms_box,
                            target_size=target_size,
                        )
                        self.contour_settings["contour_data"] = pix
                    self.current_contour_wcs = contour_csys
                else:
                    print(
                        "[ERROR] load_contour_data: external image not found or empty"
                    )
                    self.contour_settings["contour_data"] = None
                    self.current_contour_wcs = None

            main_window = self.parent()
            if main_window:
                if self.contour_settings["contour_data"] is not None:
                    self.show_status_message(
                        f"Contour data loaded: {self.contour_settings['source']} image, Stokes {self.contour_settings['stokes']}"
                    )
                else:
                    self.show_status_message("Failed to load contour data")
        except Exception as e:
            print(f"[ERROR] Error loading contour data: {e}")
            self.show_status_message(f"Error loading contour data: {e}")
            self.contour_settings["contour_data"] = None

    def draw_contours(self, ax):
        main_window = self.window()
        if self.contour_settings["contour_data"] is None:
            self.load_contour_data()

        if self.contour_settings["contour_data"] is None:
            print(
                "[ERROR] draw_contours: contour_data is None after load_contour_data()"
            )
            return

        if self.current_contour_wcs is None:
            print("[ERROR] draw_contours: current_contour_wcs is None")
            return

        # Reset contour offset (will be set if extended reprojection is used)
        self._contour_offset = [0, 0]

        # OPTIMIZATION: Fast path for same-image contours
        # When contour source is the current image, skip expensive metadata loading
        # and reprojection since the data is already aligned
        is_same_image = self.contour_settings["source"] == "same"

        fits_flag = False
        header = None
        csys = None
        summary = None

        if is_same_image:
            # Use cached metadata from plot_image - no need to reload
            fits_flag = self._cached_fits_flag
            header = self._cached_fits_header or {}
            csys = self._cached_csys
            summary = self._cached_summary
            contour_imagename = self.imagename
        else:
            # External image - need to load metadata
            # Use transformed file if coordinate transformation was applied
            if (
                self._contour_transformed_file
                and os.path.exists(self._contour_transformed_file)
                and self._contour_transformed_from
                == self.contour_settings.get("external_image")
            ):
                contour_imagename = self._contour_transformed_file
            else:
                contour_imagename = self.contour_settings["external_image"]

            if contour_imagename.endswith(".fits") or contour_imagename.endswith(
                ".fts"
            ):

                from astropy.io import fits

                fits_flag = True
                try:
                    with fits.open(contour_imagename, memmap=True) as hdul:
                        header = dict(hdul[0].header)
                except Exception as e:
                    print(f"[ERROR] Error getting contour FITS header: {e}")
                    header = {}

            try:
                ia_tool = IA()
                ia_tool.open(contour_imagename)
                csys = ia_tool.coordsys()
                summary = ia_tool.summary()
                ia_tool.close()
            except Exception as e:
                print(f"[ERROR] Error getting metadata: {e}")
                self.show_status_message(f"Error getting metadata: {e}")
                return

        try:
            # Check if the contour and image projection match
            self.show_status_message("Drawing contours.... Please wait...")
            image_wcs_obj = None
            if hasattr(self, "_cached_wcs_obj"):
                image_wcs_obj = self._cached_wcs_obj

            different_projections = False
            different_increments = False

            # Calculate contour levels

            contour_data = self.contour_settings["contour_data"]
            abs_max = np.nanmax(np.abs(contour_data))

            if self.contour_settings["level_type"] == "fraction":
                vmax = np.nanmax(contour_data)
                vmin = np.nanmin(contour_data)
                if vmax > 0:
                    pos_levels = sorted(
                        [
                            level * abs_max
                            for level in self.contour_settings["pos_levels"]
                        ]
                    )
                else:
                    pos_levels = []

                if vmin < 0:
                    neg_levels = sorted(
                        [
                            level * abs_max
                            for level in self.contour_settings["neg_levels"]
                        ]
                    )
                    neg_levels = [-level for level in reversed(neg_levels)]
                else:
                    neg_levels = []

            elif self.contour_settings["level_type"] == "sigma":
                # For sigma levels, calculate RMS from the RMS box region of the contour data
                # This gives a proper noise estimate without source contamination
                rms_box = self.contour_settings.get("rms_box", (0, 200, 0, 130))

                try:
                    # Calculate RMS from the specified region
                    rms_region = contour_data[
                        rms_box[0] : rms_box[1], rms_box[2] : rms_box[3]
                    ]
                    rms = np.sqrt(np.nanmean(rms_region**2))
                except Exception as e:
                    # Fallback to using std of entire image if RMS box fails
                    print(f"[WARNING] Could not calculate RMS from box, using std: {e}")
                    self.show_status_message(
                        f"WARNING: Could not calculate RMS from box, using std: {e}"
                    )
                    rms = np.nanstd(contour_data)

                # Positive levels: level * rms (e.g., 3σ, 6σ, 9σ...)
                pos_levels = sorted(
                    [level * rms for level in self.contour_settings["pos_levels"]]
                )
                # Negative levels: -level * rms (e.g., -3σ, -6σ, -9σ...)
                # Must be in increasing order for matplotlib (-30, -20, -10)
                neg_levels = sorted(
                    [-level * rms for level in self.contour_settings["neg_levels"]]
                )

            else:
                pos_levels = sorted(self.contour_settings["pos_levels"])
                neg_levels = sorted(
                    [-level for level in reversed(self.contour_settings["neg_levels"])]
                )

            plot_default = False
            contour_wcs_obj = None  # Initialize before the condition block

            # Check if reprojection is needed (WCS differs between contour and image)
            # OPTIMIZATION: Skip reprojection check for same-image contours - they're already aligned
            needs_reprojection = False

            if (
                not is_same_image
                and self.current_contour_wcs is not None
                and image_wcs_obj is not None
            ):
                # Build the contour WCS object
                from astropy.wcs import WCS

                contour_wcs_obj = WCS(naxis=2)

                # For FITS files, we need to handle the WCS carefully:
                # - Use current_contour_wcs for CRPIX/CRVAL/CDELT (which is adjusted for downsampling)
                # - But for HPC FITS files, CASA misinterprets the CTYPE, so use header for CTYPE
                if fits_flag and header:
                    try:
                        # Get downsampling-adjusted values from CASA coordsys
                        ref_val = self.current_contour_wcs.referencevalue()["numeric"][
                            0:2
                        ]
                        ref_pix = self.current_contour_wcs.referencepixel()["numeric"][
                            0:2
                        ]
                        increment = self.current_contour_wcs.increment()["numeric"][0:2]

                        # Swap axes for numpy (row, col) = (y, x) order
                        contour_wcs_obj.wcs.crpix = [ref_pix[1], ref_pix[0]]

                        # Get CTYPE from FITS header (correct for HPC)
                        ctype1 = header.get("CTYPE1", "")
                        ctype2 = header.get("CTYPE2", "")
                        is_hpc = (
                            "HPLN" in ctype1
                            or "HPLT" in ctype1
                            or "HPLN" in ctype2
                            or "HPLT" in ctype2
                        )

                        if is_hpc:
                            # For HPC FITS files, CASA coordsys is in radians
                            # Convert to degrees for WCS reprojection
                            crval1 = ref_val[0] * 180.0 / np.pi
                            crval2 = ref_val[1] * 180.0 / np.pi
                            cdelt1 = increment[0] * 180.0 / np.pi
                            cdelt2 = increment[1] * 180.0 / np.pi
                        else:
                            # For RA/Dec, CASA coordsys is already in radians, convert to degrees
                            crval1 = ref_val[0] * 180.0 / np.pi
                            crval2 = ref_val[1] * 180.0 / np.pi
                            cdelt1 = increment[0] * 180.0 / np.pi
                            cdelt2 = increment[1] * 180.0 / np.pi

                        contour_wcs_obj.wcs.crval = [crval2, crval1]  # Swapped
                        contour_wcs_obj.wcs.cdelt = [cdelt2, cdelt1]  # Swapped
                        contour_wcs_obj.wcs.ctype = [
                            ctype2,
                            ctype1,
                        ]  # From header (correct HPC)
                    except Exception as e:

                        print(f"[ERROR] Failed to build WCS from FITS header: {e}")
                        # Fall back to CASA coordsys without FITS header
                        fits_flag = False

                if not fits_flag:
                    # Use CASA coordsys (for CASA images or as fallback)
                    ref_val = self.current_contour_wcs.referencevalue()["numeric"][0:2]
                    ref_pix = self.current_contour_wcs.referencepixel()["numeric"][0:2]
                    increment = self.current_contour_wcs.increment()["numeric"][0:2]

                    # IMPORTANT: CASA WCS stores in (RA, Dec) = (x, y) order
                    # But numpy arrays are (row, col) = (y, x)
                    # We need to SWAP the axes for proper alignment with reproject
                    contour_wcs_obj.wcs.crpix = [
                        ref_pix[1],
                        ref_pix[0],
                    ]  # Swap to (y, x)

                    if "Right Ascension" in summary["axisnames"]:
                        contour_wcs_obj.wcs.crval = [
                            ref_val[1] * 180 / np.pi,  # Dec first
                            ref_val[0] * 180 / np.pi,  # RA second
                        ]
                        contour_wcs_obj.wcs.cdelt = [
                            increment[1] * 180 / np.pi,
                            increment[0] * 180 / np.pi,
                        ]
                        contour_wcs_obj.wcs.ctype = ["DEC--SIN", "RA---SIN"]
                    else:
                        contour_wcs_obj.wcs.crval = [ref_val[1], ref_val[0]]
                        contour_wcs_obj.wcs.cdelt = [increment[1], increment[0]]

                # Set projection type for contour WCS (swapped to match axis order)
                if fits_flag:
                    try:
                        # Swap CTYPE order to match swapped axes
                        contour_wcs_obj.wcs.ctype = [header["CTYPE2"], header["CTYPE1"]]
                    except Exception as e:
                        print(f"[ERROR] Error getting projection type from FITS: {e}")
                        self.show_status_message(
                            f"Error getting projection type from FITS: {e}"
                        )
                        # Use swapped image ctype
                        contour_wcs_obj.wcs.ctype = [
                            image_wcs_obj.wcs.ctype[1],
                            image_wcs_obj.wcs.ctype[0],
                        ]
                elif (csys.projection()["type"] == "SIN") and (
                    "Right Ascension" in summary["axisnames"]
                ):
                    contour_wcs_obj.wcs.ctype = ["DEC--SIN", "RA---SIN"]  # Swapped
                elif (csys.projection()["type"] == "TAN") and (
                    "Right Ascension" in summary["axisnames"]
                ):
                    contour_wcs_obj.wcs.ctype = ["DEC--TAN", "RA---TAN"]  # Swapped
                else:
                    # Use swapped image ctype
                    contour_wcs_obj.wcs.ctype = [
                        image_wcs_obj.wcs.ctype[1],
                        image_wcs_obj.wcs.ctype[0],
                    ]

                # IMPORTANT: Also swap the image WCS to match!
                # Create a copy with swapped axes for reprojection
                image_wcs_swapped = WCS(naxis=2)
                image_wcs_swapped.wcs.crpix = [
                    image_wcs_obj.wcs.crpix[1],
                    image_wcs_obj.wcs.crpix[0],
                ]
                image_wcs_swapped.wcs.crval = [
                    image_wcs_obj.wcs.crval[1],
                    image_wcs_obj.wcs.crval[0],
                ]
                image_wcs_swapped.wcs.cdelt = [
                    image_wcs_obj.wcs.cdelt[1],
                    image_wcs_obj.wcs.cdelt[0],
                ]
                image_wcs_swapped.wcs.ctype = [
                    image_wcs_obj.wcs.ctype[1],
                    image_wcs_obj.wcs.ctype[0],
                ]

                # Use the swapped WCS for reprojection
                image_wcs_for_reproject = image_wcs_swapped

                # Check for different projections and warn user
                # Extract projection type from ctype (e.g., "SIN" from "RA---SIN" or "DEC--SIN")
                def get_projection_type(ctype):
                    if ctype and len(ctype) >= 3:
                        return ctype[-3:]  # Get last 3 chars (SIN, TAN, etc.)
                    return ""

                contour_proj = get_projection_type(contour_wcs_obj.wcs.ctype[0])
                image_proj = get_projection_type(image_wcs_obj.wcs.ctype[0])

                if contour_proj != image_proj:
                    different_projections = True
                    # Show warning dialog to user
                    QMessageBox.warning(
                        self,
                        "Projection Mismatch",
                        f"The contour image uses a different projection than the base image:\n\n"
                        f"Base image: {image_proj}\n"
                        f"Contour image: {contour_proj}\n\n"
                        f"Contours will be plotted but may not align perfectly.",
                    )

                # Check for different increments (pixel scales)
                contour_cdelt = np.abs(contour_wcs_obj.wcs.cdelt)
                image_cdelt = np.abs(image_wcs_obj.wcs.cdelt)
                scale_ratio_x = contour_cdelt[0] / image_cdelt[0]
                scale_ratio_y = contour_cdelt[1] / image_cdelt[1]

                if (np.abs(scale_ratio_x - 1) > 1e-3) or (
                    np.abs(scale_ratio_y - 1) > 1e-3
                ):
                    different_increments = True
                    needs_reprojection = True

                # Check for different reference pixels
                contour_crpix = np.array(contour_wcs_obj.wcs.crpix)
                image_crpix = np.array(image_wcs_obj.wcs.crpix)
                if np.any(np.abs(contour_crpix - image_crpix) > 1e-3):
                    needs_reprojection = True

                # Check for different reference values
                contour_crval = np.array(contour_wcs_obj.wcs.crval)
                image_crval = np.array(image_wcs_obj.wcs.crval)
                if np.any(np.abs(contour_crval - image_crval) > 1e-6):
                    needs_reprojection = True

                # Check if image shapes differ
                if contour_data.shape != self.current_image_data.shape:
                    needs_reprojection = True

            # Handle manual downsampling for "Same Image" or aligned images (no reprojection needed)
            # This ensures large images are still downsampled for performance
            if not needs_reprojection:
                self._contour_ds_factor = 1  # Default
                if self.contour_settings.get("downsample", True):
                    max_dim = max(contour_data.shape)
                    if max_dim > 2048:
                        factor = int(np.ceil(max_dim / 2048.0))
                        print(
                            f"[INFO] Downsampling aligned contour by {factor}x (original size: {contour_data.shape})"
                        )
                        # Simple slicing is sufficient for aligned data
                        contour_data = contour_data[::factor, ::factor]
                        self._contour_ds_factor = factor

            if needs_reprojection and contour_wcs_obj is not None:
                try:
                    from reproject import reproject_interp

                    if main_window:
                        self.show_status_message("Reprojecting.. Please wait...")

                    # print("Reprojecting contour data to match base image WCS...")
                    # print(f"  Contour data shape: {contour_data.shape}")
                    # print(f"  Image data shape: {self.current_image_data.shape}")
                    # print(f"  Contour WCS (swapped): crpix={contour_wcs_obj.wcs.crpix}, crval={contour_wcs_obj.wcs.crval}, cdelt={contour_wcs_obj.wcs.cdelt}, ctype={contour_wcs_obj.wcs.ctype}")
                    # print(f"  Image WCS (swapped):   crpix={image_wcs_for_reproject.wcs.crpix}, crval={image_wcs_for_reproject.wcs.crval}, cdelt={image_wcs_for_reproject.wcs.cdelt}, ctype={image_wcs_for_reproject.wcs.ctype}")

                    # Set the array shape in the WCS header for reproject
                    # Note: contour_data shape is (height, width) but WCS uses (NAXIS1=width, NAXIS2=height)
                    contour_wcs_obj.array_shape = contour_data.shape
                    image_wcs_for_reproject.array_shape = self.current_image_data.shape

                    # Calculate extended or clipped bounds based on show_full_extent setting
                    extended_shape = self.current_image_data.shape
                    contour_offset = [0, 0]  # Offset for contour plotting (y, x)
                    extended_wcs = image_wcs_for_reproject

                    show_full_extent = self.contour_settings.get(
                        "show_full_extent", False
                    )

                    # Always try to extend canvas (default: 1.5x cap, full extent: no cap)
                    try:
                        # Get world coordinates of contour image corners
                        contour_corners_pix = np.array(
                            [
                                [0, 0],
                                [contour_data.shape[1] - 1, 0],
                                [0, contour_data.shape[0] - 1],
                                [contour_data.shape[1] - 1, contour_data.shape[0] - 1],
                            ]
                        )
                        contour_corners_world = contour_wcs_obj.wcs_pix2world(
                            contour_corners_pix, 0
                        )
                        image_corners_pix = image_wcs_for_reproject.wcs_world2pix(
                            contour_corners_world, 0
                        )

                        # Calculate the extent of contour in base image coordinates
                        min_x = min(np.min(image_corners_pix[:, 0]), 0)
                        max_x = max(
                            np.max(image_corners_pix[:, 0]),
                            self.current_image_data.shape[1] - 1,
                        )
                        min_y = min(np.min(image_corners_pix[:, 1]), 0)
                        max_y = max(
                            np.max(image_corners_pix[:, 1]),
                            self.current_image_data.shape[0] - 1,
                        )

                        # Calculate extended dimensions
                        extended_width = int(np.ceil(max_x - min_x)) + 1
                        extended_height = int(np.ceil(max_y - min_y)) + 1

                        # Check for large reprojected matrix size when show_full_extent is enabled
                        if show_full_extent:
                            max_dim = max(extended_width, extended_height)
                            if max_dim > 5000:
                                reply = QMessageBox.warning(
                                    self,
                                    "Large Reprojection Size",
                                    f"The reprojected matrix size ({extended_width} x {extended_height}) "
                                    f"exceeds 5000 pixels in at least one dimension.\n\n"
                                    f"This may consume significant memory and take a long time to compute.\n\n"
                                    f"Do you want to continue?",
                                    QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No,
                                )
                                if reply == QMessageBox.No:
                                    self.show_status_message(
                                        "Contour drawing cancelled by user."
                                    )
                                    return

                        # Apply 1.5x cap by default (unless show_full_extent is enabled)
                        if not show_full_extent:
                            max_width = int(self.current_image_data.shape[1] * 1.5)
                            max_height = int(self.current_image_data.shape[0] * 1.5)

                            # Recalculate bounds if capped
                            if extended_width > max_width:
                                center_x = self.current_image_data.shape[1] / 2
                                min_x = center_x - max_width / 2
                                max_x = center_x + max_width / 2 - 1
                                extended_width = max_width
                            if extended_height > max_height:
                                center_y = self.current_image_data.shape[0] / 2
                                min_y = center_y - max_height / 2
                                max_y = center_y + max_height / 2 - 1
                                extended_height = max_height

                        extended_shape = (extended_height, extended_width)

                        # Calculate offset (how much the base image is shifted in the extended canvas)
                        # Use floats to preserve sub-pixel accuracy
                        contour_offset = [-min_y, -min_x]

                        # Adjust WCS reference pixel for the extended shape
                        extended_wcs = image_wcs_for_reproject.deepcopy()
                        extended_wcs.wcs.crpix = [
                            image_wcs_for_reproject.wcs.crpix[0] - min_x,
                            image_wcs_for_reproject.wcs.crpix[1] - min_y,
                        ]
                        extended_wcs.array_shape = extended_shape

                    except Exception as e:
                        if main_window:
                            self.show_status_message(
                                f"Could not compute extended bounds: {e}"
                            )
                        print(f"[ERROR] Could not compute extended bounds: {e}")
                        extended_wcs = image_wcs_for_reproject
                        extended_shape = self.current_image_data.shape
                        contour_offset = [0, 0]

                    # Store offset for contour drawing
                    self._contour_offset = contour_offset

                    # Check cache for reprojected data
                    # Create a comprehensive cache key
                    cache_key = (
                        self.imagename,  # Base image
                        contour_imagename,  # Contour image source
                        self.contour_settings.get(
                            "stokes", ""
                        ),  # Stokes parameter (crucial!)
                        contour_data.shape,  # Original contour shape
                        "fits_header" if fits_flag else "casa_csys",  # Source type
                        str(contour_wcs_obj.wcs.crval),  # Contour WCS params
                        str(contour_wcs_obj.wcs.cdelt),
                        str(
                            image_wcs_for_reproject.wcs.crval
                        ),  # Base image WCS params (includes downsampling state)
                        str(image_wcs_for_reproject.wcs.cdelt),
                        show_full_extent,  # Extent setting
                        self.contour_settings.get(
                            "downsample", True
                        ),  # Downsample setting
                    )

                    cached_result = self._reproject_cache.get(cache_key)

                    if cached_result is not None:
                        # cache hit
                        # print("Using cached reprojected contour data")
                        if main_window:
                            self.show_status_message(
                                "Using cached contour alignment..."
                            )
                        array, offset, ds_factor = cached_result
                        self._contour_offset = offset

                        # Apply downsampling factor to offset if needed for plotting calculations
                        # Note: The array itself is already downsampled, so we just use it.
                        # The offset is in base image pixels, which is correct for placement.
                    else:
                        # Cache miss - perform reprojection

                        # OPTIMIZATION: Downsample large arrays to save memory/time
                        # Contours don't need pixel-perfect 4k resolution
                        contour_ds_factor = 1
                        max_dim = max(extended_shape)
                        downsample_enabled = self.contour_settings.get(
                            "downsample", True
                        )

                        if downsample_enabled and max_dim > 2048:
                            contour_ds_factor = int(np.ceil(max_dim / 2048.0))
                            print(
                                f"[INFO] Downsampling contour reprojection by {contour_ds_factor}x (original size: {extended_shape})"
                            )

                            # Adjust shape
                            new_height = extended_shape[0] // contour_ds_factor
                            new_width = extended_shape[1] // contour_ds_factor
                            extended_shape = (new_height, new_width)

                            # Adjust WCS
                            extended_wcs.wcs.cdelt *= contour_ds_factor
                            extended_wcs.wcs.crpix /= contour_ds_factor

                        # Reproject the contour data to the extended/clipped WCS
                        # OPTIMIZATION: Don't calculate footprint unless needed (2x speedup)
                        array = reproject_interp(
                            (contour_data, contour_wcs_obj),
                            extended_wcs,
                            shape_out=extended_shape,
                            return_footprint=False,
                        )

                        # Store in cache
                        self._reproject_cache[cache_key] = (
                            array,
                            self._contour_offset,
                            contour_ds_factor,
                        )

                    # Replace the NaNs with zeros
                    array = np.nan_to_num(array, nan=0.0)

                    # Check if reprojection produced valid data
                    if np.all(array == 0):
                        # Re-run with footprint to diagnose coverage
                        if main_window:
                            self.show_status_message(
                                "Reprojection yielded zeros, checking overlap..."
                            )

                        _, footprint = reproject_interp(
                            (contour_data, contour_wcs_obj),
                            extended_wcs,
                            shape_out=extended_shape,
                            return_footprint=True,
                        )
                        footprint_pct = np.sum(footprint > 0) / footprint.size * 100
                        if main_window:
                            self.show_status_message(
                                "WARNING: Reprojection produced all zeros ..."
                            )
                        print(
                            "[WARNING] Reprojection produced all zeros - checking footprint coverage"
                        )
                        print(f"  Footprint coverage: {footprint_pct:.1f}%")
                        if footprint_pct < 1.0:
                            # Show warning dialog about no overlap
                            QMessageBox.warning(
                                self,
                                "No Coordinate Overlap",
                                "Contour reprojection produced no valid data (0% footprint).\n\n"
                                "This typically happens when the base image and contour image "
                                "are from different observation times, causing them to point at "
                                "different sky coordinates.\n\n"
                                "Please ensure both images are from the same or similar observation time.",
                            )
                            return  # Don't try to draw empty contours
                    else:

                        if main_window:
                            self.show_status_message(
                                f"Reprojection done.. plotting... Please wait..."
                            )

                    contour_data = array
                    reprojection_done = True

                    # Store factor for extent calculation
                    self._contour_ds_factor = (
                        contour_ds_factor if "contour_ds_factor" in locals() else 1
                    )
                    if "ds_factor" in locals():
                        self._contour_ds_factor = ds_factor

                except ImportError as e:
                    print(f"[ERROR] reproject library not available: {e}")
                    # print("Install with: pip install reproject")
                    QMessageBox.warning(
                        self,
                        "Reprojection Not Available",
                        "The 'reproject' library is required to align contours from images "
                        "with different pixel scales.\n\nInstall with: pip install reproject",
                    )
                    plot_default = True
                except Exception as e:
                    print(f"[ERROR] Error reprojecting contour data: {e}")
                    import traceback

                    traceback.print_exc()

                    # Fallback: try scipy-based scaling using pixel scale ratio
                    # This is useful when WCS reprojection fails (e.g., HPC observer=None)
                    try:
                        from scipy.ndimage import zoom, shift

                        self.show_status_message(
                            "WCS reprojection failed, trying pixel-scale fallback..."
                        )

                        # Calculate scale factors from CDELT
                        scale_x = (
                            contour_wcs_obj.wcs.cdelt[0]
                            / image_wcs_for_reproject.wcs.cdelt[0]
                        )
                        scale_y = (
                            contour_wcs_obj.wcs.cdelt[1]
                            / image_wcs_for_reproject.wcs.cdelt[1]
                        )

                        # Zoom to match pixel scales
                        zoomed = zoom(
                            contour_data,
                            [abs(scale_y), abs(scale_x)],
                            order=1,
                            mode="constant",
                            cval=0,
                        )

                        # Pad or crop to match target shape
                        target_shape = self.current_image_data.shape
                        result = np.zeros(target_shape)

                        # Calculate the offset to center the contour
                        # Use reference value difference to determine shift
                        dcrval = (
                            contour_wcs_obj.wcs.crval
                            - image_wcs_for_reproject.wcs.crval
                        )
                        offset_x = int(dcrval[0] / image_wcs_for_reproject.wcs.cdelt[0])
                        offset_y = int(dcrval[1] / image_wcs_for_reproject.wcs.cdelt[1])

                        # Calculate copy ranges
                        src_start_y = max(0, -offset_y)
                        src_end_y = min(zoomed.shape[0], target_shape[0] - offset_y)
                        src_start_x = max(0, -offset_x)
                        src_end_x = min(zoomed.shape[1], target_shape[1] - offset_x)

                        dst_start_y = max(0, offset_y)
                        dst_end_y = dst_start_y + (src_end_y - src_start_y)
                        dst_start_x = max(0, offset_x)
                        dst_end_x = dst_start_x + (src_end_x - src_start_x)

                        if src_end_y > src_start_y and src_end_x > src_start_x:
                            result[dst_start_y:dst_end_y, dst_start_x:dst_end_x] = (
                                zoomed[src_start_y:src_end_y, src_start_x:src_end_x]
                            )

                        contour_data = result
                        self.show_status_message("Fallback reprojection done")
                        print("[INFO] Fallback pixel-scale reprojection successful")
                    except Exception as fallback_e:
                        print(
                            f"[ERROR] Fallback reprojection also failed: {fallback_e}"
                        )
                        plot_default = True

            # For reprojected data, the output matches the base image orientation
            # so we transpose to match how the base image is displayed
            display_contour_data = contour_data.transpose()

            # Get offset for extended canvas positioning
            contour_offset = getattr(self, "_contour_offset", [0, 0])

            # Calculate extent for proper positioning when using extended canvas
            # extent = [left, right, bottom, top] in data coordinates

            ds_factor = getattr(self, "_contour_ds_factor", 1)

            # Apply extent if offset is non-zero OR downsampling is active (since pixels differ from base)
            if contour_offset != [0, 0] or ds_factor > 1:
                extent = [
                    -contour_offset[1],  # left (shifted by x offset)
                    -contour_offset[1]
                    + (
                        display_contour_data.shape[0] * ds_factor
                    ),  # right (add Width / shape[0])
                    -contour_offset[0],  # bottom (shifted by y offset)
                    -contour_offset[0]
                    + (
                        display_contour_data.shape[1] * ds_factor
                    ),  # top (add Height / shape[1])
                ]
            else:
                extent = None  # Use default (0 to shape)

            if pos_levels and len(pos_levels) > 0:

                try:
                    if extent:
                        # Create coordinate arrays for extended canvas
                        # NOTE: display_contour_data is Transposed (X, Y).
                        # So Rows (dim 0) = X-axis, Cols (dim 1) = Y-axis.
                        # We must map Rows to Vertical (y) and Cols to Horizontal (x).
                        # Vertical Axis should show X-range (extent[0], extent[1])
                        # Horizontal Axis should show Y-range (extent[2], extent[3])

                        y = np.linspace(
                            extent[0], extent[1], display_contour_data.shape[0]
                        )
                        x = np.linspace(
                            extent[2], extent[3], display_contour_data.shape[1]
                        )
                        cs_pos = ax.contour(
                            x,
                            y,
                            display_contour_data,
                            levels=pos_levels,
                            colors=self.contour_settings.get(
                                "pos_color", self.contour_settings["color"]
                            ),
                            linewidths=self.contour_settings.get(
                                "pos_linewidth", self.contour_settings["linewidth"]
                            ),
                            linestyles=self.contour_settings["pos_linestyle"],
                        )
                    else:
                        cs_pos = ax.contour(
                            display_contour_data,
                            levels=pos_levels,
                            colors=self.contour_settings.get(
                                "pos_color", self.contour_settings["color"]
                            ),
                            linewidths=self.contour_settings.get(
                                "pos_linewidth", self.contour_settings["linewidth"]
                            ),
                            linestyles=self.contour_settings["pos_linestyle"],
                            origin="lower",
                        )

                    # Add contour labels if enabled
                    if self.contour_settings.get("show_labels", False):
                        ax.clabel(cs_pos, inline=True, fontsize=8, fmt="%.2g")

                except Exception as e:
                    print(
                        f"[ERROR] Error drawing positive contours: {e}, levels: {pos_levels}"
                    )
                    self.show_status_message(
                        f"Error drawing positive contours: {e}, levels: {pos_levels}"
                    )

            if neg_levels and len(neg_levels) > 0:
                try:
                    if extent:
                        y = np.linspace(
                            extent[0], extent[1], display_contour_data.shape[0]
                        )
                        x = np.linspace(
                            extent[2], extent[3], display_contour_data.shape[1]
                        )

                        cs_neg = ax.contour(
                            x,
                            y,
                            display_contour_data,
                            levels=neg_levels,
                            colors=self.contour_settings.get(
                                "neg_color", self.contour_settings["color"]
                            ),
                            linewidths=self.contour_settings.get(
                                "neg_linewidth", self.contour_settings["linewidth"]
                            ),
                            linestyles=self.contour_settings["neg_linestyle"],
                        )
                    else:
                        cs_neg = ax.contour(
                            display_contour_data,
                            levels=neg_levels,
                            colors=self.contour_settings.get(
                                "neg_color", self.contour_settings["color"]
                            ),
                            linewidths=self.contour_settings.get(
                                "neg_linewidth", self.contour_settings["linewidth"]
                            ),
                            linestyles=self.contour_settings["neg_linestyle"],
                            origin="lower",
                        )

                    # Add contour labels if enabled
                    if self.contour_settings.get("show_labels", False):
                        ax.clabel(cs_neg, inline=True, fontsize=8, fmt="%.2g")

                except Exception as e:
                    print(
                        f"[ERROR] Error drawing negative contours: {e}, levels: {neg_levels}"
                    )
                    self.show_status_message(
                        f"Error drawing negative contours: {e}, levels: {neg_levels}"
                    )

            if main_window:

                self.show_status_message("Done. ")

        except Exception as e:
            print(f"[ERROR] Error drawing contours: {e}")
            import traceback

            traceback.print_exc()
            main_window = self.parent()
            if main_window:
                self.show_status_message(f"Error drawing contours: {str(e)}")

    # ==================== File Navigation Methods ====================

    def _scan_directory_files(self, rescan_only=False):
        """Scan the base directory for FITS/image files matching the filter pattern

        Args:
            rescan_only: If True, don't change the filter pattern (user manually set it)
        """
        import os
        import glob
        import fnmatch

        if not self.imagename:
            return

        # Determine base directory and file type
        if os.path.isdir(self.imagename):
            # CASA image - use parent directory
            self._file_base_dir = os.path.dirname(self.imagename)
            default_pattern = "*.image"
        else:
            # FITS file - use containing directory
            self._file_base_dir = os.path.dirname(self.imagename)
            if self.imagename.lower().endswith(".fts"):
                default_pattern = "*.fts"
            else:
                default_pattern = "*.fits"

        if not self._file_base_dir:
            self._file_base_dir = "."

        # Always update filter pattern to match current file's extension when opening a new file
        if not rescan_only:
            # Update filter pattern to match the extension of the currently opened file
            self._file_filter_pattern = default_pattern

        # Get all items in directory
        all_items = []
        try:
            for item in os.listdir(self._file_base_dir):
                item_path = os.path.join(self._file_base_dir, item)

                # Check if it matches the filter pattern
                if not fnmatch.fnmatch(item, self._file_filter_pattern):
                    continue

                # Include FITS files
                if item.lower().endswith((".fits", ".fts")):
                    all_items.append(item_path)
                # Include CASA images (directories that look like images)
                elif os.path.isdir(item_path) and item.endswith(".image"):
                    all_items.append(item_path)
        except Exception as e:
            print(f"[ERROR] Error scanning directory: {e}")
            self.show_status_message(f"Error scanning directory: {e}")
            self._file_list = []
            self._file_list_index = -1
            self._update_nav_buttons()
            return

        # Sort alphabetically
        self._file_list = sorted(all_items)

        # Find current file index
        current_path = os.path.abspath(self.imagename)
        self._file_list_index = -1
        for i, path in enumerate(self._file_list):
            if os.path.abspath(path) == current_path:
                self._file_list_index = i
                break

        self._update_nav_buttons()

    def _update_nav_buttons(self):
        """Update navigation button states and position label"""
        if not hasattr(self, "_prev_file_btn"):
            return

        # Enable/disable buttons based on position
        has_prev = self._file_list_index > 0
        has_next = (
            self._file_list_index >= 0
            and self._file_list_index < len(self._file_list) - 1
        )

        # First/Prev need previous files
        self._first_file_btn.setEnabled(has_prev)
        self._prev_file_btn.setEnabled(has_prev)

        # Next/Last need next files
        self._next_file_btn.setEnabled(has_next)
        self._last_file_btn.setEnabled(has_next)

        # Update position label
        if self._file_list and self._file_list_index >= 0:
            total = len(self._file_list)
            current = self._file_list_index + 1
            filename = os.path.basename(self._file_list[self._file_list_index])
            self._file_pos_label.setText(f"{current}/{total}")
            self._file_pos_label.setToolTip(
                f"File {current} of {total}: {filename}\nFilter: {self._file_filter_pattern}"
            )
        else:
            self._file_pos_label.setText("")
            self._file_pos_label.setToolTip("")

    def _populate_remote_file_list(
        self, main_window, remote_dir, casa_mode, current_local_path
    ):
        """Populate the file list from a remote directory for navigation (async)."""
        # Start async population - don't block UI
        from PyQt5.QtCore import QThread, pyqtSignal, QObject

        class PopulateThread(QThread):
            finished = pyqtSignal(list, int)  # entries, current_index
            error = pyqtSignal(str)

            def __init__(self, connection, remote_dir, casa_mode, current_basename):
                super().__init__()
                self.connection = connection
                self.remote_dir = remote_dir
                self.casa_mode = casa_mode
                self.current_basename = current_basename
                self._sftp = None

            def run(self):
                try:
                    import json
                    import shlex

                    # FAST PATH: Try executing a python script on the remote server
                    # This filters on the server side, avoiding huge loops for large directories
                    py_script = """
import os, json, sys, stat
path = sys.argv[1]
entries = []
casa_mode = %s

try:
    with os.scandir(path) as it:
        for e in it:
            name = e.name
            if name.startswith('.'): continue
            
            is_dir = e.is_dir()
            
            include = False
            if casa_mode:
                if is_dir and (name.endswith('.image') or name.endswith('.im')):
                    include = True
            else:
                is_fits = not is_dir and name.lower().endswith(('.fits', '.fts', '.fit'))
                if is_fits: include = True
            
            if include:
                s = e.stat()
                entries.append({
                    'n': name,
                    'd': is_dir,
                    's': s.st_size,
                    'm': s.st_mtime
                })
    print(json.dumps(entries))
except Exception:
    print("[]")
""" % (
                        "True" if self.casa_mode else "False"
                    )

                    # Try python3 first, then python
                    cmd = f"python3 -c {shlex.quote(py_script)} {shlex.quote(self.remote_dir)}"
                    stdin, stdout, stderr = self.connection._client.exec_command(cmd)

                    # Read output
                    out_data = stdout.read().decode("utf-8").strip()

                    entries = []
                    success = False

                    if out_data and out_data.startswith("["):
                        try:
                            raw_entries = json.loads(out_data)
                            from .remote.ssh_manager import RemoteFileInfo

                            for item in raw_entries:
                                full_path = os.path.join(self.remote_dir, item["n"])
                                info = RemoteFileInfo(
                                    name=item["n"],
                                    path=full_path,
                                    is_dir=item["d"],
                                    size=item["s"],
                                    mtime=item["m"],
                                )
                                entries.append(info)
                            success = True
                        except:
                            pass

                    # FALLBACK: If fast path failed (no python, etc), use slower SFTP loop
                    if not success:
                        if self.connection._client:
                            self._sftp = self.connection._client.open_sftp()
                        else:
                            self.error.emit("Not connected")
                            return

                        import stat
                        from .remote.ssh_manager import RemoteFileInfo

                        attrs = self._sftp.listdir_attr(self.remote_dir)

                        for attr in attrs:
                            name = attr.filename
                            if name.startswith("."):
                                continue

                            st_mode = attr.st_mode
                            is_dir = stat.S_ISDIR(st_mode)

                            # Pre-filter check
                            include_item = False
                            if self.casa_mode:
                                if is_dir and (
                                    name.endswith(".image") or name.endswith(".im")
                                ):
                                    include_item = True
                            else:
                                # Check extension for FITS files OR CASA images
                                is_fits = not is_dir and name.lower().endswith(
                                    (".fits", ".fts", ".fit")
                                )

                                if is_fits:
                                    include_item = True

                            if include_item:
                                full_path = os.path.join(self.remote_dir, name)
                                info = RemoteFileInfo(
                                    name=name,
                                    path=full_path,
                                    is_dir=is_dir,
                                    size=attr.st_size,
                                    mtime=attr.st_mtime,
                                )
                                entries.append(info)

                    # Sort
                    entries.sort(key=lambda x: x.name.lower())

                    # Find current index
                    current_idx = -1
                    for i, e in enumerate(entries):
                        if e.name == self.current_basename:
                            current_idx = i
                            break

                    self.finished.emit(entries, current_idx)

                except Exception as e:
                    self.error.emit(str(e))
                finally:
                    if self._sftp:
                        try:
                            self._sftp.close()
                        except:
                            pass

        try:
            if (
                not main_window.remote_connection
                or not main_window.remote_connection.is_connected()
            ):
                return

            current_basename = os.path.basename(current_local_path)

            # Store reference to thread to prevent garbage collection
            self._populate_thread = PopulateThread(
                main_window.remote_connection, remote_dir, casa_mode, current_basename
            )

            def on_finished(entries, current_idx):
                try:
                    self._remote_file_list = entries
                    self._file_list = [e.path for e in entries]
                    self._file_list_index = current_idx
                    self._update_nav_buttons()
                except RuntimeError:
                    pass  # Widget deleted

            def on_error(msg):
                print(f"[WARNING] Failed to populate remote file list: {msg}")
                self._remote_file_list = []
                self._file_list = []
                self._file_list_index = -1
                try:
                    self._update_nav_buttons()
                except RuntimeError:
                    pass

            self._populate_thread.finished.connect(on_finished)
            self._populate_thread.error.connect(on_error)
            self._populate_thread.start()

        except Exception as e:
            print(f"[WARNING] Failed to start remote file list population: {e}")
            self._remote_file_list = []
            self._file_list = []
            self._file_list_index = -1
            self._update_nav_buttons()

    def _load_remote_file(self, remote_path):
        """Download and load a remote file for navigation."""
        from pathlib import Path

        main_window = self.window()
        if not main_window or not hasattr(main_window, "remote_connection"):
            return False

        if (
            not main_window.remote_connection
            or not main_window.remote_connection.is_connected()
        ):
            return False

        try:
            from PyQt5.QtWidgets import QProgressDialog
            from PyQt5.QtCore import Qt, QEventLoop
            from .remote.download_thread import DownloadThread

            # Get the remote file info
            entry = main_window.remote_connection.get_file_info(remote_path)

            # Check cache first
            cached_path = main_window.remote_cache.get_cached_path(
                main_window.remote_connection._host,
                remote_path,
                entry.mtime,
                entry.size,
            )

            if cached_path:
                local_path = str(cached_path)
            else:
                # Download the file with progress dialog (NON-BLOCKING Main Thread)
                local_path = main_window.remote_cache.get_cache_path(
                    main_window.remote_connection._host,
                    remote_path,
                )

                # Create progress dialog
                progress = QProgressDialog(
                    f"Downloading {os.path.basename(remote_path)}...",
                    "Cancel",
                    0,
                    100,
                    self,
                )
                progress.setWindowTitle("Downloading")
                progress.setWindowModality(Qt.WindowModal)
                progress.setMinimumDuration(0)
                progress.setValue(0)

                # Setup Download Thread
                download_thread = DownloadThread(
                    main_window.remote_connection,
                    remote_path,
                    str(local_path),
                    is_directory=entry.is_dir,
                )

                # Store local variables to capture result
                download_result = {"success": False, "error": None, "path": None}

                # Create local event loop to wait for thread
                loop = QEventLoop()

                def on_progress(transferred, total):
                    if total > 0:
                        percent = int(100 * transferred / total)
                        progress.setValue(percent)
                        if total > 1024 * 1024:
                            progress.setLabelText(
                                f"Downloading {os.path.basename(remote_path)}...\n"
                                f"{transferred / (1024**2):.1f} / {total / (1024**2):.1f} MB"
                            )
                        else:
                            progress.setLabelText(
                                f"Downloading {os.path.basename(remote_path)}...\n"
                                f"{transferred / 1024:.1f} / {total / 1024:.1f} KB"
                            )

                def on_finished(path):
                    download_result["success"] = True
                    download_result["path"] = path
                    loop.quit()

                def on_error(msg):
                    download_result["success"] = False
                    download_result["error"] = msg
                    loop.quit()

                def on_cancel():
                    download_thread.cancel()
                    progress.setLabelText("Cancelling...")
                    progress.setCancelButtonText(None)  # Disable cancel button
                    # We don't quit loop yet, wait for thread to actually stop or finish
                    # But if we rely on loop.quit, we need to ensure thread finishes.
                    # As a fallback, we can forcefully quit loop if needed, but better to wait.

                download_thread.progress.connect(on_progress)
                download_thread.finished.connect(on_finished)
                download_thread.error.connect(on_error)
                progress.canceled.connect(on_cancel)

                # Start thread and show dialog
                download_thread.start()
                progress.show()

                # Wait for completion
                loop.exec_()

                progress.close()
                download_thread.wait()  # Ensure thread is joined

                if not download_result["success"]:
                    if download_result["error"]:
                        raise Exception(download_result["error"])
                    else:
                        raise InterruptedError("Download cancelled")  # User cancelled

                local_path = download_result["path"]

                # Mark as cached
                main_window.remote_cache.mark_cached(
                    main_window.remote_connection._host,
                    remote_path,
                    Path(local_path),
                    entry.mtime,
                    entry.size,
                )

            # Load the file
            self._reset_hpc_state()
            self.imagename = local_path
            self.dir_entry.setText(local_path)
            self.contour_settings["contour_data"] = None
            self.current_contour_wcs = None
            self.figure.clear()
            self.on_visualization_changed(dir_load=True)
            self.update_tab_name_from_path(local_path)

            return True

        except InterruptedError:
            # User cancelled the download
            self.show_status_message("Download cancelled")
            return False
        except Exception as e:
            print(f"[ERROR] Failed to load remote file: {e}")
            self.show_status_message(f"Failed to load remote file: {e}")
            return False

    def _on_prev_file(self):
        """Load the previous file in the file list"""
        if self._file_list_index <= 0:
            return

        new_index = self._file_list_index - 1
        new_path = self._file_list[new_index]
        self._file_list_index = new_index

        # Check if in remote mode
        if self._is_remote_mode:
            if self._load_remote_file(new_path):
                self._update_nav_buttons()
                self.show_status_message(f"Loaded {os.path.basename(new_path)}")
            return

        # Reset HPC state
        self._reset_hpc_state()

        # Load the file
        self.imagename = new_path
        self.dir_entry.setText(new_path)
        self.contour_settings["contour_data"] = None
        self.current_contour_wcs = None
        # Clear figure to prevent restoring old view limits
        self.figure.clear()
        self.on_visualization_changed(dir_load=True)
        self.update_tab_name_from_path(new_path)

        self._update_nav_buttons()
        self.show_status_message(f"Loaded {os.path.basename(new_path)}")

    def _on_next_file(self):
        """Load the next file in the file list"""
        if (
            self._file_list_index < 0
            or self._file_list_index >= len(self._file_list) - 1
        ):
            return

        new_index = self._file_list_index + 1
        new_path = self._file_list[new_index]
        self._file_list_index = new_index

        # Check if in remote mode
        if self._is_remote_mode:
            if self._load_remote_file(new_path):
                self._update_nav_buttons()
                self.show_status_message(f"Loaded {os.path.basename(new_path)}")
            return

        # Reset HPC state
        self._reset_hpc_state()

        # Load the file
        self.imagename = new_path
        self.dir_entry.setText(new_path)
        self.contour_settings["contour_data"] = None
        self.current_contour_wcs = None
        # Clear figure to prevent restoring old view limits
        self.figure.clear()
        self.on_visualization_changed(dir_load=True)
        self.update_tab_name_from_path(new_path)

        self._update_nav_buttons()
        self.show_status_message(f"Loaded {os.path.basename(new_path)}")

    def _on_first_file(self):
        """Load the first file in the file list"""
        if not self._file_list or self._file_list_index <= 0:
            return

        new_index = 0
        new_path = self._file_list[new_index]
        self._file_list_index = new_index

        # Check if in remote mode
        if self._is_remote_mode:
            if self._load_remote_file(new_path):
                self._update_nav_buttons()
                self.show_status_message(
                    f"Loaded first file: {os.path.basename(new_path)}"
                )
            return

        # Reset HPC state
        self._reset_hpc_state()

        # Load the file
        self.imagename = new_path
        self.dir_entry.setText(new_path)
        self.contour_settings["contour_data"] = None
        self.current_contour_wcs = None
        # Clear figure to prevent restoring old view limits
        self.figure.clear()
        self.on_visualization_changed(dir_load=True)
        self.update_tab_name_from_path(new_path)

        self._update_nav_buttons()
        self.show_status_message(f"Loaded first file: {os.path.basename(new_path)}")

    def _on_last_file(self):
        """Load the last file in the file list"""
        if not self._file_list or self._file_list_index >= len(self._file_list) - 1:
            return

        new_index = len(self._file_list) - 1
        new_path = self._file_list[new_index]
        self._file_list_index = new_index

        # Check if in remote mode
        if self._is_remote_mode:
            if self._load_remote_file(new_path):
                self._update_nav_buttons()
                self.show_status_message(
                    f"Loaded last file: {os.path.basename(new_path)}"
                )
            return

        # Reset HPC state
        self._reset_hpc_state()

        # Load the file
        self.imagename = new_path
        self.dir_entry.setText(new_path)
        self.contour_settings["contour_data"] = None
        self.current_contour_wcs = None
        # Clear figure to prevent restoring old view limits
        self.figure.clear()
        self.on_visualization_changed(dir_load=True)
        self.update_tab_name_from_path(new_path)

        self._update_nav_buttons()
        self.show_status_message(f"Loaded last file: {os.path.basename(new_path)}")

    '''def _toggle_fullscreen(self):
        """Toggle fullscreen mode for the main window"""
        main_window = self.window()
        if main_window:
            if main_window.isFullScreen():
                main_window.showNormal()
            else:
                main_window.showFullScreen()
    '''

    def _show_filter_dialog(self):
        """Show a dialog to set the file filter pattern"""
        from PyQt5.QtWidgets import QInputDialog
        import fnmatch

        pattern, ok = QInputDialog.getText(
            self,
            "File Filter Pattern",
            "Enter glob pattern to filter files:\n(e.g., *-image.fits, chunk_*.fits, *.image)",
            text=self._file_filter_pattern,
        )
        
        if ok:
            self._file_filter_pattern = pattern if pattern else "*"

            # Check if in remote mode
            if self._is_remote_mode and self._remote_file_list:
                # Filter the remote file list using the pattern
                filtered = [
                    e
                    for e in self._remote_file_list
                    if fnmatch.fnmatch(e.name, self._file_filter_pattern)
                ]
                self._file_list = [e.path for e in filtered]

                # Find current file in filtered list
                current_basename = (
                    os.path.basename(self.imagename) if self.imagename else ""
                )
                self._file_list_index = -1
                for i, e in enumerate(filtered):
                    if e.name == current_basename:
                        self._file_list_index = i
                        break

                self._update_nav_buttons()
            else:
                # Local mode - rescan directory
                self._scan_directory_files(rescan_only=True)

            if self._file_list:
                self.show_status_message(
                    f"Found {len(self._file_list)} files matching '{self._file_filter_pattern}'"
                )
            else:
                self.show_status_message(
                    f"No files found matching '{self._file_filter_pattern}'"
                )

    def _show_playlist_dialog(self):
        """Show a non-modal dialog listing all files in the current file list"""
        from PyQt5.QtWidgets import (
            QDialog,
            QVBoxLayout,
            QListWidget,
            QPushButton,
            QHBoxLayout,
            QLabel,
            QFrame,
        )

        # Check if file list exists
        if not self._file_list:
            self.show_status_message("No files found. Load an image first.")
            return

        # If dialog already exists and is visible, just raise it
        if hasattr(self, "_playlist_dialog") and self._playlist_dialog is not None:
            try:
                if self._playlist_dialog.isVisible():
                    self._playlist_dialog.raise_()
                    self._playlist_dialog.activateWindow()
                    return
            except RuntimeError:
                # Dialog was deleted, create a new one
                self._playlist_dialog = None

        dialog = QDialog(self)
        dialog.setWindowTitle("File List")
        set_hand_cursor(dialog)
        dialog.setMinimumSize(550, 700)

        # Store reference to prevent garbage collection
        self._playlist_dialog = dialog

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header with title and file count
        header_layout = QHBoxLayout()
        header_layout.setSpacing(12)

        title_label = QLabel("📋 File List")
        title_label.setObjectName("DialogTitle")
        header_layout.addWidget(title_label)

        header_layout.addStretch()

        info_badge = QLabel(
            f"Filter: {self._file_filter_pattern}  │  {len(self._file_list)} files"
        )
        info_badge.setObjectName("InfoBadge")
        header_layout.addWidget(info_badge)

        layout.addLayout(header_layout)

        # Search box
        search_frame = QFrame()
        search_frame.setObjectName("SearchFrame")
        search_layout = QHBoxLayout(search_frame)
        search_layout.setContentsMargins(10, 0, 10, 0)
        search_layout.setSpacing(8)
        search_layout.setAlignment(Qt.AlignVCenter)

        search_label = QLabel("🔍")
        search_label.setObjectName("SearchIcon")
        search_input = QLineEdit()
        search_input.setObjectName("SearchInput")
        search_input.setPlaceholderText("Search files...")
        search_input.setClearButtonEnabled(True)
        search_input.setFixedHeight(28)

        search_layout.addWidget(search_label, 0, Qt.AlignVCenter)
        search_layout.addWidget(search_input, 1, Qt.AlignVCenter)
        layout.addWidget(search_frame)

        # File list
        file_list_widget = QListWidget()
        file_list_widget.setObjectName("FileListWidget")

        # Store original file list for filtering
        original_items = []
        for i, path in enumerate(self._file_list):
            filename = os.path.basename(path)
            item_text = f"{i + 1}. {filename}"
            file_list_widget.addItem(item_text)
            original_items.append((i, item_text, path))

        # Search/filter function
        def filter_files(search_text):
            search_text = search_text.lower().strip()
            file_list_widget.clear()

            if not search_text:
                # Show all items
                for idx, item_text, path in original_items:
                    file_list_widget.addItem(item_text)
                # Restore selection
                if self._file_list_index >= 0:
                    file_list_widget.setCurrentRow(self._file_list_index)
            else:
                # Filter items
                for idx, item_text, path in original_items:
                    filename = os.path.basename(path).lower()
                    if search_text in filename:
                        file_list_widget.addItem(item_text)

        search_input.textChanged.connect(filter_files)

        # Highlight current file
        if self._file_list_index >= 0:
            file_list_widget.setCurrentRow(self._file_list_index)

        layout.addWidget(file_list_widget, stretch=1)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        open_btn = QPushButton("Open")
        open_btn.setObjectName("PrimaryButton")
        open_btn.setDefault(True)
        open_btn.setCursor(Qt.PointingHandCursor)

        close_btn = QPushButton("Close")
        close_btn.setCursor(Qt.PointingHandCursor)

        # Store references for use in callbacks
        viewer = self

        def on_open():
            """Handle opening a selected file with proper error handling"""
            try:
                # Check if viewer is still valid
                if not viewer or not hasattr(viewer, "_file_list"):
                    dialog.close()
                    return

                # Get selected item
                current_item = file_list_widget.currentItem()
                if not current_item:
                    viewer.show_status_message("Please select a file first.")
                    return

                # Extract original index from item text (format: "N. filename")
                item_text = current_item.text()
                try:
                    original_idx = (
                        int(item_text.split(".")[0]) - 1
                    )  # Convert to 0-based index
                except (ValueError, IndexError):
                    viewer.show_status_message("Error parsing file index.")
                    return

                if original_idx < 0 or original_idx >= len(viewer._file_list):
                    viewer.show_status_message("Invalid file selection.")
                    return

                new_path = viewer._file_list[original_idx]

                # Check if in remote mode
                if viewer._is_remote_mode:
                    # Remote mode - download and load
                    viewer._file_list_index = original_idx
                    if viewer._load_remote_file(new_path):
                        viewer._update_nav_buttons()
                        viewer.show_status_message(
                            f"Loaded {os.path.basename(new_path)}"
                        )
                        # Try to update dialog, but handle case where user closed it
                        try:
                            search_input.clear()
                            file_list_widget.setCurrentRow(original_idx)
                        except RuntimeError:
                            # Dialog was closed during download, that's okay
                            pass
                    return

                # Local mode - check if file still exists
                if not os.path.exists(new_path):
                    viewer.show_status_message(
                        f"File no longer exists: {os.path.basename(new_path)}"
                    )
                    return

                viewer._file_list_index = original_idx

                # Reset HPC state
                viewer._reset_hpc_state()

                # Load the file
                viewer.imagename = new_path
                viewer.dir_entry.setText(new_path)
                viewer.contour_settings["contour_data"] = None
                viewer.current_contour_wcs = None
                # Clear figure to prevent restoring old view limits
                viewer.figure.clear()
                viewer.on_visualization_changed(dir_load=True)
                viewer.update_tab_name_from_path(new_path)

                viewer._update_nav_buttons()
                viewer.show_status_message(f"Loaded {os.path.basename(new_path)}")

                # Clear search and refresh list to show current selection
                search_input.clear()

            except RuntimeError as e:
                # Viewer was deleted
                dialog.close()
            except Exception as e:
                try:
                    viewer.show_status_message(f"Error loading file: {str(e)}")
                except:
                    pass

        def on_double_click(item):
            on_open()

        def on_close():
            dialog.close()

        def on_dialog_destroyed():
            """Clean up reference when dialog is destroyed"""
            if hasattr(viewer, "_playlist_dialog"):
                viewer._playlist_dialog = None

        open_btn.clicked.connect(on_open)
        close_btn.clicked.connect(on_close)
        file_list_widget.itemDoubleClicked.connect(on_double_click)

        btn_layout.addStretch()
        btn_layout.addWidget(open_btn)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

        # Apply modern styling
        palette = theme_manager.palette
        window = palette["window"]
        base = palette["base"]
        surface = palette["surface"]
        border = palette["border"]
        border_light = palette.get("border_light", border)
        text = palette["text"]
        text_secondary = palette.get(
            "text_secondary", palette.get("disabled", "#888888")
        )
        highlight = palette["highlight"]
        highlight_hover = palette.get("highlight_hover", highlight)
        button = palette["button"]
        button_hover = palette["button_hover"]
        button_pressed = palette.get("button_pressed", button)
        disabled = palette.get("disabled", "#666666")
        gradient_start = palette.get("button_gradient_start", highlight)
        gradient_end = palette.get("button_gradient_end", highlight_hover)

        dialog.setStyleSheet(
            f"""
            QDialog {{
                background-color: {window};
            }}
            
            QLabel#DialogTitle {{
                font-size: 14pt;
                font-weight: 700;
                color: {text};
            }}
            
            QLabel#InfoBadge {{
                font-size: 10pt;
                color: {text_secondary};
                padding: 4px 10px;
                background-color: {surface};
                border: 1px solid {border};
                border-radius: 6px;
            }}
            
            QFrame#SearchFrame {{
                background-color: {surface};
                border: 1px solid {border};
                border-radius: 8px;
                min-height: 34px;
                max-height: 34px;
            }}
            
            QLabel#SearchIcon {{
                font-size: 12pt;
                padding: 0px;
                margin: 0px;
            }}
            
            QLineEdit#SearchInput {{
                padding: 4px 8px;
                font-size: 10pt;
                border: none;
                background-color: transparent;
                color: {text};
            }}
            
            QListWidget#FileListWidget {{
                border: 1px solid {border};
                border-radius: 10px;
                background-color: {base};
                color: {text};
                font-size: 10.5pt;
                outline: none;
                padding: 4px;
            }}
            
            QListWidget#FileListWidget::item {{
                padding: 6px 8px;
                border-radius: 4px;
                margin: 1px 2px;
            }}
            
            QListWidget#FileListWidget::item:selected {{
                background-color: {highlight};
                color: #ffffff;
            }}
            
            QListWidget#FileListWidget::item:hover:!selected {{
                background-color: {button_hover};
            }}
            
            QPushButton {{
                padding: 6px 16px;
                font-size: 10pt;
                font-weight: 500;
                border: 1px solid {border};
                border-radius: 6px;
                background-color: {button};
                color: {text};
                min-width: 70px;
                min-height: 24px;
            }}
            
            QPushButton:hover {{
                background-color: {button_hover};
                border-color: {highlight};
            }}
            
            QPushButton:pressed {{
                background-color: {button_pressed};
            }}
            
            QPushButton#PrimaryButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {gradient_start}, stop:1 {gradient_end});
                color: #ffffff;
                border: none;
                font-weight: 600;
            }}
            
            QPushButton#PrimaryButton:hover {{
                background-color: {highlight_hover};
            }}
            
            QLabel {{
                color: {text};
            }}
        """
        )

        # Set up dialog for non-modal behavior
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(on_dialog_destroyed)

        # Track dialog for garbage collection
        dialog.destroyed.connect(
            lambda: (
                self._open_dialogs.remove(dialog)
                if dialog in self._open_dialogs
                else None
            )
        )
        self._open_dialogs.append(dialog)

        # Show as non-modal
        dialog.show()

    def closeEvent(self, event):
        # Clean up HPC temp file if exists
        import os

        if hasattr(self, "_hpc_temp_file") and self._hpc_temp_file:
            if os.path.exists(self._hpc_temp_file):
                try:
                    os.remove(self._hpc_temp_file)
                except Exception:
                    pass
        # Clean up RA/Dec temp file if exists (from HPC->RA/Dec conversion)
        if hasattr(self, "_radec_temp_file") and self._radec_temp_file:
            if os.path.exists(self._radec_temp_file):
                try:
                    os.remove(self._radec_temp_file)
                except Exception:
                    pass
        # Clean up TB temp file if exists
        if hasattr(self, "_tb_temp_file") and self._tb_temp_file:
            if os.path.exists(self._tb_temp_file):
                try:
                    os.remove(self._tb_temp_file)
                except Exception:
                    pass
        # Clean up contour coordinate transformation temp file if exists
        if (
            hasattr(self, "_contour_transformed_file")
            and self._contour_transformed_file
        ):
            if os.path.exists(self._contour_transformed_file):
                try:
                    os.remove(self._contour_transformed_file)
                except Exception:
                    pass
        super().closeEvent(event)

    def reset_view(self, show_status_message=True):
        """Reset the view to show the full image with original limits"""
        if self.current_image_data is None:
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        ax = self.figure.axes[0]

        # Reset to show the full image
        ax.set_xlim(0, self.current_image_data.shape[0])
        ax.set_ylim(0, self.current_image_data.shape[1])

        self._update_beam_position(ax)
        # If solar disk checkbox is checked, draw the solar disk
        if self.show_solar_disk_checkbox.isChecked():
            self._update_solar_disk_position(ax)

        self.canvas.draw()
        QApplication.restoreOverrideCursor()
        if show_status_message:
            self.show_status_message("View reset")

    def show_rms_box_dialog(self):
        """Show a dialog for configuring the RMS box settings"""
        dialog = QDialog(self)
        dialog.setWindowTitle("RMS Box Settings")
        set_hand_cursor(dialog)
        dialog.setMinimumWidth(400)

        # Create layout
        layout = QVBoxLayout(dialog)

        # Add a description label
        description = QLabel(
            "Set the region used for RMS calculation. This affects dynamic range calculations, "
            "thresholding for derived Stokes parameters (Lfrac, Vfrac, Q/I, etc.), and other statistics. "
            "Changes will be applied to the current image and all future Stokes parameter selections."
        )
        description.setWordWrap(True)
        description.setStyleSheet("font-style: italic; opacity: 0.8;")
        layout.addWidget(description)

        # Create grid layout for RMS box inputs
        rms_grid = QGridLayout()
        rms_grid.setVerticalSpacing(10)
        rms_grid.setHorizontalSpacing(10)

        # Create input fields for RMS box coordinates
        self.dialog_rms_x1_entry = QLineEdit(str(self.current_rms_box[0]))
        self.dialog_rms_x2_entry = QLineEdit(str(self.current_rms_box[1]))
        self.dialog_rms_y1_entry = QLineEdit(str(self.current_rms_box[2]))
        self.dialog_rms_y2_entry = QLineEdit(str(self.current_rms_box[3]))

        # Add validators to ensure values are numeric
        max_val = 9999
        if hasattr(self, "current_image_data") and self.current_image_data is not None:
            height, width = self.current_image_data.shape
            self.dialog_rms_x1_entry.setValidator(QIntValidator(0, height - 1))
            self.dialog_rms_x2_entry.setValidator(QIntValidator(1, height))
            self.dialog_rms_y1_entry.setValidator(QIntValidator(0, width - 1))
            self.dialog_rms_y2_entry.setValidator(QIntValidator(1, width))
        else:
            self.dialog_rms_x1_entry.setValidator(QIntValidator(0, max_val))
            self.dialog_rms_x2_entry.setValidator(QIntValidator(1, max_val))
            self.dialog_rms_y1_entry.setValidator(QIntValidator(0, max_val))
            self.dialog_rms_y2_entry.setValidator(QIntValidator(1, max_val))

        # Create sliders for RMS box coordinates
        self.dialog_rms_x1_slider = QSlider(Qt.Horizontal)
        self.dialog_rms_x2_slider = QSlider(Qt.Horizontal)
        self.dialog_rms_y1_slider = QSlider(Qt.Horizontal)
        self.dialog_rms_y2_slider = QSlider(Qt.Horizontal)
        # Configure sliders
        if hasattr(self, "current_image_data") and self.current_image_data is not None:
            height, width = self.current_image_data.shape
            self.dialog_rms_x1_slider.setMaximum(height - 1)
            self.dialog_rms_x2_slider.setMaximum(height)
            self.dialog_rms_y1_slider.setMaximum(width - 1)
            self.dialog_rms_y2_slider.setMaximum(width)
        else:
            self.dialog_rms_x1_slider.setMaximum(max_val)
            self.dialog_rms_x2_slider.setMaximum(max_val)
            self.dialog_rms_y1_slider.setMaximum(max_val)
            self.dialog_rms_y2_slider.setMaximum(max_val)

        self.dialog_rms_x1_slider.setMinimum(0)
        self.dialog_rms_x2_slider.setMinimum(1)
        self.dialog_rms_y1_slider.setMinimum(0)
        self.dialog_rms_y2_slider.setMinimum(1)

        self.dialog_rms_x1_slider.setValue(self.current_rms_box[0])
        self.dialog_rms_x2_slider.setValue(self.current_rms_box[1])
        self.dialog_rms_y1_slider.setValue(self.current_rms_box[2])
        self.dialog_rms_y2_slider.setValue(self.current_rms_box[3])

        # Connect slider signals
        self.dialog_rms_x1_slider.valueChanged.connect(
            lambda v: self.dialog_rms_x1_entry.setText(str(v))
        )
        self.dialog_rms_x2_slider.valueChanged.connect(
            lambda v: self.dialog_rms_x2_entry.setText(str(v))
        )
        self.dialog_rms_y1_slider.valueChanged.connect(
            lambda v: self.dialog_rms_y1_entry.setText(str(v))
        )
        self.dialog_rms_y2_slider.valueChanged.connect(
            lambda v: self.dialog_rms_y2_entry.setText(str(v))
        )

        # Connect text entry signals
        self.dialog_rms_x1_entry.textChanged.connect(self.update_dialog_rms_box)
        self.dialog_rms_x2_entry.textChanged.connect(self.update_dialog_rms_box)
        self.dialog_rms_y1_entry.textChanged.connect(self.update_dialog_rms_box)
        self.dialog_rms_y2_entry.textChanged.connect(self.update_dialog_rms_box)

        # Add widgets to grid layout
        rms_grid.addWidget(QLabel("X1:"), 0, 0)
        rms_grid.addWidget(self.dialog_rms_x1_entry, 0, 1)
        rms_grid.addWidget(self.dialog_rms_x1_slider, 0, 2)

        rms_grid.addWidget(QLabel("X2:"), 1, 0)
        rms_grid.addWidget(self.dialog_rms_x2_entry, 1, 1)
        rms_grid.addWidget(self.dialog_rms_x2_slider, 1, 2)

        rms_grid.addWidget(QLabel("Y1:"), 2, 0)
        rms_grid.addWidget(self.dialog_rms_y1_entry, 2, 1)
        rms_grid.addWidget(self.dialog_rms_y1_slider, 2, 2)

        rms_grid.addWidget(QLabel("Y2:"), 3, 0)
        rms_grid.addWidget(self.dialog_rms_y2_entry, 3, 1)
        rms_grid.addWidget(self.dialog_rms_y2_slider, 3, 2)

        # Set column stretching to make sliders take most of the space
        rms_grid.setColumnStretch(2, 1)

        layout.addLayout(rms_grid)

        # Add buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)

        def on_apply():
            try:
                self.apply_dialog_rms_box(dialog)
            except RuntimeError:
                QMessageBox.warning(
                    dialog, "Error", "The target tab is no longer available."
                )
                dialog.close()

        button_box.accepted.connect(on_apply)
        button_box.rejected.connect(dialog.close)
        layout.addWidget(button_box)

        # Show the dialog as non-modal
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

    def update_dialog_rms_box(self):
        """Update RMS box sliders in the dialog when text entries change"""
        try:
            x1 = int(self.dialog_rms_x1_entry.text())
            x2 = int(self.dialog_rms_x2_entry.text())
            y1 = int(self.dialog_rms_y1_entry.text())
            y2 = int(self.dialog_rms_y2_entry.text())

            # Update sliders without triggering signals
            self.dialog_rms_x1_slider.blockSignals(True)
            self.dialog_rms_x2_slider.blockSignals(True)
            self.dialog_rms_y1_slider.blockSignals(True)
            self.dialog_rms_y2_slider.blockSignals(True)

            self.dialog_rms_x1_slider.setValue(x1)
            self.dialog_rms_x2_slider.setValue(x2)
            self.dialog_rms_y1_slider.setValue(y1)
            self.dialog_rms_y2_slider.setValue(y2)

            self.dialog_rms_x1_slider.blockSignals(False)
            self.dialog_rms_x2_slider.blockSignals(False)
            self.dialog_rms_y1_slider.blockSignals(False)
            self.dialog_rms_y2_slider.blockSignals(False)
        except ValueError:
            pass  # Ignore invalid input

    def apply_dialog_rms_box(self, dialog):
        """Apply the RMS box settings from the dialog and close it"""
        # Early return if no valid image is loaded or if it's the splash image
        is_splash = (
            hasattr(self, "imagename")
            and self.imagename
            and self.imagename.endswith("splash.fits")
        )
        if self.current_image_data is None or is_splash:
            QMessageBox.warning(
                self,
                "No Image Loaded",
                "Please load an image before changing RMS box settings.",
            )
            dialog.reject()
            return
        try:
            x1 = int(self.dialog_rms_x1_entry.text())
            x2 = int(self.dialog_rms_x2_entry.text())
            y1 = int(self.dialog_rms_y1_entry.text())
            y2 = int(self.dialog_rms_y2_entry.text())

            # Ensure x1 < x2 and y1 < y2
            if x1 >= x2 or y1 >= y2:
                QMessageBox.warning(
                    self, "Invalid RMS Box", "Please ensure that X1 < X2 and Y1 < Y2."
                )
                return

            # Ensure values are within image bounds
            if self.current_image_data is not None:
                height, width = self.current_image_data.shape
                if x2 > height or y2 > width:
                    QMessageBox.warning(
                        self,
                        "Invalid RMS Box",
                        f"RMS box exceeds image dimensions ({height}x{width}).",
                    )
                    return

            # Store the current RMS box values
            self.current_rms_box = [x1, x2, y1, y2]

            # Update the contour settings RMS box as well
            self.contour_settings["rms_box"] = tuple(self.current_rms_box)

            # Update image stats with new RMS box
            if self.current_image_data is not None:
                self.show_image_stats(rms_box=self.current_rms_box)

                # Reload the current image with the new RMS box
                # This will recalculate RMS for all Stokes parameters
                if hasattr(self, "imagename") and self.imagename:
                    current_stokes = self.stokes_combo.currentText()
                    try:
                        threshold = float(self.threshold_entry.text())
                    except (ValueError, AttributeError):
                        threshold = 10.0

                    # Show a status message
                    self.show_status_message(
                        f"Updating RMS box to [{x1}:{x2}, {y1}:{y2}] and recalculating..."
                    )

                    # Reload the data with the new RMS box
                    from .utils import get_pixel_values_from_image

                    pix, csys, psf = get_pixel_values_from_image(
                        self.imagename,
                        current_stokes,
                        threshold,
                        rms_box=tuple(self.current_rms_box),
                    )
                    self.current_image_data = pix
                    self.current_wcs = csys
                    self.psf = psf

                    # Update the plot
                    try:
                        vmin_val = float(self.vmin_entry.text())
                        vmax_val = float(self.vmax_entry.text())
                        stretch = self.stretch_combo.currentText()
                        cmap = self.cmap_combo.currentText()
                        gamma = float(self.gamma_entry.text())
                        self.plot_image(vmin_val, vmax_val, stretch, cmap, gamma)
                    except (ValueError, AttributeError):
                        self.plot_image()

                self.show_status_message(f"RMS box updated to [{x1}:{x2}, {y1}:{y2}]")

            # Close the dialog
            dialog.accept()
        except ValueError:
            QMessageBox.warning(
                self,
                "Invalid Input",
                "Please enter valid integer values for the RMS box coordinates.",
            )

    def _update_overlay_toggle_styles(self):
        """Update overlay checkbox and button styles based on current theme."""
        # Theme-aware toggle style for overlay checkboxes
        if theme_manager.is_dark:
            toggle_bg = "#3a3d4d"
            toggle_bg_hover = "#4a4d5d"
            toggle_border = "#4a4d5d"
            toggle_border_hover = "#5a5d6d"
            text_color = "#e0e0e0"
        else:
            toggle_bg = "#d0d0d0"
            toggle_bg_hover = "#c0c0c0"
            toggle_border = "#b0b0b0"
            toggle_border_hover = "#a0a0a0"
            text_color = "#333333"

        overlay_toggle_style = f"""
            QCheckBox {{
                spacing: 6px;
                font-size: 10.2pt;
                color: {text_color};
            }}
            QCheckBox::indicator {{
                width: :8px;
                height: 14px;
                border-radius: 7px;
                background-color: {toggle_bg};
                border: 1px solid {toggle_border};
            }}
            QCheckBox::indicator:hover {{
                background-color: {toggle_bg_hover};
                border-color: {toggle_border_hover};
            }}
            QCheckBox::indicator:checked {{
                background-color: #6366f1;
                border-color: #818cf8;
            }}
            QCheckBox::indicator:checked:hover {{
                background-color: #7c7ff5;
            }}
        """

        settings_btn_style = """
            QPushButton {
                background-color: transparent;
                border-radius: 4px;
                padding: 2px;
                border: none;
            }
            QPushButton:hover {
                background-color: rgba(99, 102, 241, 0.2);
            }
            QPushButton:pressed {
                background-color: rgba(99, 102, 241, 0.35);
            }
        """

        # Apply to overlay checkboxes
        for checkbox in [
            self.show_beam_checkbox,
            self.show_grid_checkbox,
            self.show_solar_disk_checkbox,
            self.show_contours_checkbox,
        ]:
            if hasattr(self, checkbox.objectName()) or checkbox:
                checkbox.setStyleSheet(overlay_toggle_style)

        # Apply to settings buttons
        for btn in [self.solar_disk_center_button, self.contour_settings_button]:
            if hasattr(self, btn.objectName()) or btn:
                btn.setStyleSheet(settings_btn_style)

        # Update Fast Load toggle style
        if hasattr(self, "downsample_toggle"):
            fast_toggle_style = f"""
                QCheckBox#FastLoadToggle {{
                    spacing: 0px;
                }}
                QCheckBox#FastLoadToggle::indicator {{
                    width: 28px;
                    height: 14px;
                    border-radius: 7px;
                    background-color: {toggle_bg};
                    border: 1px solid {toggle_border};
                }}
                QCheckBox#FastLoadToggle::indicator:hover {{
                    background-color: {toggle_bg_hover};
                }}
                QCheckBox#FastLoadToggle::indicator:checked {{
                    background-color: #6366f1;
                    border-color: #818cf8;
                }}
                QCheckBox#FastLoadToggle::indicator:checked:hover {{
                    background-color: #7c7ff5;
                }}
            """
            self.downsample_toggle.setStyleSheet(fast_toggle_style)

        # Update Fast and RMS labels
        label_color = "#a5a8b8" if theme_manager.is_dark else "#555555"
        if hasattr(self, "fast_label"):
            self.fast_label.setStyleSheet(f"color: {label_color}; font-size: 10pt;")
        if hasattr(self, "rms_label"):
            self.rms_label.setStyleSheet(f"color: {label_color}; font-size: 10pt;")

    def refresh_icons(self):
        """Refresh all icons to match the current theme."""
        # Update left panel buttons
        if hasattr(self, "browse_btn"):
            self.browse_btn.setIcon(QIcon(themed_icon("browse.png")))
        if hasattr(self, "solar_disk_center_button"):
            self.solar_disk_center_button.setIcon(QIcon(themed_icon("settings.png")))
        if hasattr(self, "contour_settings_button"):
            self.contour_settings_button.setIcon(QIcon(themed_icon("settings.png")))
        if hasattr(self, "beam_settings_button"):
            self.beam_settings_button.setIcon(QIcon(themed_icon("settings.png")))
        if hasattr(self, "grid_settings_button"):
            self.grid_settings_button.setIcon(QIcon(themed_icon("settings.png")))

        # Update overlay toggle styles for theme
        self._update_overlay_toggle_styles()

        # Update navigation buttons (if they exist in left panel)
        if hasattr(self, "zoom_in_button"):
            self.zoom_in_button.setIcon(QIcon(themed_icon("zoom_in.png")))
        if hasattr(self, "zoom_out_button"):
            self.zoom_out_button.setIcon(QIcon(themed_icon("zoom_out.png")))
        if hasattr(self, "reset_view_button"):
            self.reset_view_button.setIcon(QIcon(themed_icon("reset.png")))
        if hasattr(self, "zoom_60arcmin_button"):
            self.zoom_60arcmin_button.setIcon(QIcon(themed_icon("zoom_60arcmin.png")))

        # Update toolbar actions (in the figure toolbar)
        if hasattr(self, "zoom_in_action"):
            self.zoom_in_action.setIcon(QIcon(themed_icon("zoom_in.png")))
        if hasattr(self, "zoom_out_action"):
            self.zoom_out_action.setIcon(QIcon(themed_icon("zoom_out.png")))
        if hasattr(self, "reset_view_action"):
            self.reset_view_action.setIcon(QIcon(themed_icon("reset.png")))
        if hasattr(self, "zoom_60arcmin_action"):
            self.zoom_60arcmin_action.setIcon(QIcon(themed_icon("zoom_60arcmin.png")))
        if hasattr(self, "rect_action"):
            self.rect_action.setIcon(QIcon(themed_icon("rectangle_selection.png")))
        if hasattr(self, "ellipse_action"):
            self.ellipse_action.setIcon(QIcon(themed_icon("icons8-ellipse-90.png")))
        if hasattr(self, "info_action"):
            self.info_action.setIcon(QIcon(themed_icon("icons8-info-90.png")))
        if hasattr(self, "customize_plot_action"):
            self.customize_plot_action.setIcon(QIcon(themed_icon("settings.png")))
        # Update search button in colormap selector
        if hasattr(self, "colormap_selector") and hasattr(
            self.colormap_selector, "search_button"
        ):
            self.colormap_selector.search_button.setIcon(
                QIcon(themed_icon("search.png"))
            )

        # Recreate matplotlib NavigationToolbar to pick up new theme colors
        if hasattr(self, "nav_toolbar") and self.nav_toolbar:
            self._recreate_nav_toolbar()

    def _recreate_nav_toolbar(self):
        """Recreate the matplotlib NavigationToolbar to update icon colors for theme."""
        if not hasattr(self, "nav_toolbar") or not self.nav_toolbar:
            return

        # Get the parent layout
        parent = self.nav_toolbar.parent()
        layout = parent.layout() if parent else None

        if not layout:
            return

        # Find the toolbar's position in the layout
        toolbar_index = layout.indexOf(self.nav_toolbar)
        if toolbar_index < 0:
            return

        # Store reference and remove from layout
        old_toolbar = self.nav_toolbar
        layout.removeWidget(old_toolbar)

        # Disconnect from canvas events and destroy properly
        old_toolbar.setParent(None)
        old_toolbar.destroy()

        # Create new toolbar (will pick up current theme colors)
        self.nav_toolbar = NavigationToolbar(self.canvas, self)

        # Insert at same position
        layout.insertWidget(toolbar_index, self.nav_toolbar)
        
        # Ensure hand cursor is applied to the new toolbar buttons
        self._set_button_cursors()


class CustomTabBar(QTabBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Create the add tab button
        self.add_tab_button = QToolButton(self)
        self.add_tab_button.setIcon(QIcon(themed_icon("add_tab_default.png")))
        self.add_tab_button.setToolTip("Add new tab")
        self.add_tab_button.setFixedSize(32, 32)
        self.add_tab_button.setIconSize(QSize(32, 32))
        self.add_tab_button.setStyleSheet(
            """
            QToolButton {
                background-color: transparent;
                border: none;
                border-radius: 4px;
                margin: 2px 0px 2px 0px;
                padding: 0px;
            }
            */QToolButton:hover {
                background-color: #2D2D2D;
            }*/
            QToolButton:pressed {
                background-color: #3D3D3D;
            }
            """
        )

        # Connect hover events for the add button
        self.add_tab_button.enterEvent = self._handle_add_button_hover_enter
        self.add_tab_button.leaveEvent = self._handle_add_button_hover_leave

        # Enable expanding tabs to fill available space
        self.setExpanding(True)

        # Make sure the add button is visible and on top
        self.add_tab_button.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        # self.add_tab_button.setFocusPolicy(Qt.StrongFocus)
        self.add_tab_button.show()
        self.add_tab_button.raise_()

        # Initialize button position
        QTimer.singleShot(0, self.moveAddButton)

        # Apply current theme immediately (for correct startup colors)
        self.refresh_theme()

    # Add mouseDoubleClickEvent to handle tab editing
    def mouseDoubleClickEvent(self, event):
        """Handle double-click on a tab to edit its text using a dialog"""
        index = self.tabAt(event.pos())
        if index >= 0:
            current_text = self.tabText(index)

            # Use a modal dialog instead of in-place editing to avoid crashes
            from PyQt5.QtWidgets import QInputDialog

            new_text, ok = QInputDialog.getText(
                self, "Edit Tab Name", "Enter new tab name:", text=current_text
            )

            if ok and new_text:
                self.setTabText(index, new_text)

        super().mouseDoubleClickEvent(event)

    def _handle_add_button_hover_enter(self, event):
        self.add_tab_button.setIcon(QIcon(themed_icon("add_tab_hover.png")))

    def _handle_add_button_hover_leave(self, event):
        self.add_tab_button.setIcon(QIcon(themed_icon("add_tab_default.png")))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.moveAddButton()

    def tabLayoutChange(self):
        super().tabLayoutChange()
        self.moveAddButton()

    def moveAddButton(self):
        """Position the add button at the extreme right of the tab bar"""
        button_x = self.width() - self.add_tab_button.width() - 2
        button_y = (self.height() - self.add_tab_button.height()) // 2
        self.add_tab_button.move(button_x, button_y)
        self.add_tab_button.show()  # Ensure button is visible
        self.add_tab_button.raise_()  # Ensure button is on top

    def sizeHint(self):
        """Return a size that accounts for the add button at the right"""
        size = super().sizeHint()
        # Add extra space for the add button
        size.setWidth(
            size.width() + self.add_tab_button.width() + 720
        )  # 20px extra padding (increased from 10px)
        return size

    def tabSizeHint(self, index):
        """Calculate the size for each tab to distribute space evenly"""
        width = (
            self.width() - self.add_tab_button.width() - 40
        )  # Reserve more space for add button (20px instead of 10px)
        if self.count() > 0:
            tab_width = width // self.count()
            # Ensure minimum tab width with enough space for text
            return QSize(max(tab_width, 120), super().tabSizeHint(index).height())
        return super().tabSizeHint(index)

    def setTabText(self, index, text):
        """Override setTabText to ensure text is properly elided if too long"""
        # Call the parent implementation
        super().setTabText(index, text)

        # Get the current tab size
        tab_rect = self.tabRect(index)

        # Calculate available width for text (accounting for close button and padding)
        available_width = tab_rect.width() - 10  # 40px for close button and padding

        # If text is too long, elide it
        if self.fontMetrics().horizontalAdvance(text) > available_width:
            elided_text = self.fontMetrics().elidedText(
                text, Qt.ElideRight, available_width
            )
            # Call parent implementation again with elided text
            super().setTabText(index, elided_text)

    def refresh_theme(self):
        """Update tab bar styling and icons for current theme."""
        palette = theme_manager.palette
        is_dark = theme_manager.is_dark

        # Theme-aware colors
        if is_dark:
            tab_selected_bg = "#383838"
            tab_unselected_bg = "#252525"
            tab_hover_bg = "#404040"
            tab_border = "#484848"
            tab_border_unsel = "#353535"
            button_pressed = "#3D3D3D"
            tab_text_color = "#ffffff"
        else:
            tab_selected_bg = palette.get("surface", "#f5f5f5")
            tab_unselected_bg = palette.get("window", "#e8e8e8")
            tab_hover_bg = palette.get("button_hover", "#d0d0d0")
            tab_border = palette.get("border", "#b0b0b0")
            tab_border_unsel = palette.get("border", "#c0c0c0")
            button_pressed = palette.get("button_pressed", "#c0c0c0")
            tab_text_color = palette.get("text", "#1a1a1a")

        # Update add button icon - themed_icon handles light/dark switching
        self.add_tab_button.setIcon(QIcon(themed_icon("add_tab_default.png")))

        # Update add button styling
        self.add_tab_button.setStyleSheet(
            f"""
            QToolButton {{
                background-color: transparent;
                border: none;
                border-radius: 4px;
            }}
            QToolButton:pressed {{
                background-color: {button_pressed};
            }}
        """
        )

        # Update tab bar styling with theme colors
        close_default = themed_icon("close_tab_default.png")
        close_hover = themed_icon("close_tab_hover.png")

        self.setStyleSheet(
            f"""
            QTabBar::tab {{
                padding: 4px 12px 4px 8px;
                margin: 0px 0px 0px 0px;
                border-top-left-radius: 0px;
                border-top-right-radius: 0px;
                text-align: left;
                border-top: none;
                color: {tab_text_color};
            }}
            QTabBar::tab:selected {{
                background: {tab_selected_bg};
                border: 1px solid {tab_border};
                border-top: none;
            }}
            QTabBar::tab:!selected {{
                background: {tab_unselected_bg};
                border: 1px solid {tab_border_unsel};
                border-top: none;
            }}
            QTabBar::tab:hover {{
                background: {tab_hover_bg};
            }}
            QTabBar::close-button {{
                image: url("{close_default}");
                subcontrol-position: left;
                subcontrol-origin: margin;
                margin-left: 4px;
                width: 32px;
                height: 32px;
            }}
            QTabBar::close-button:hover {{
                image: url("{close_hover}");
            }}
        """
        )


class CustomTabWidget(QTabWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        # Use our custom tab bar
        self.setTabBar(CustomTabBar())

        # Get reference to the add button from our custom tab bar
        self.add_tab_button = self.tabBar().add_tab_button

        # Set the tab widget to use the entire available width
        self.setUsesScrollButtons(False)
        self.setElideMode(Qt.ElideRight)

        # Ensure the tab bar is visible
        self.tabBar().setVisible(True)

        # Tab widget pane styling is now handled by the main theme stylesheet

        # Make sure the add button is properly initialized
        QTimer.singleShot(100, self.ensureAddButtonVisible)

    def _handle_add_button_hover_enter(self, event):
        self.add_tab_button.setIcon(QIcon(themed_icon("add_tab_hover.png")))

    def _handle_add_button_hover_leave(self, event):
        self.add_tab_button.setIcon(QIcon(themed_icon("add_tab_default.png")))

    def resizeEvent(self, event):
        """Handle resize events to ensure tab bar is properly updated"""
        super().resizeEvent(event)
        # Force tab bar to update its layout
        self.tabBar().tabLayoutChange()
        # Ensure add button is visible after resize
        self.ensureAddButtonVisible()

    def ensureAddButtonVisible(self):
        """Make sure the add button is visible and on top"""
        if hasattr(self, "add_tab_button") and self.add_tab_button:
            self.add_tab_button.show()
            self.add_tab_button.raise_()


class SolarRadioImageViewerApp(QMainWindow):
    def __init__(self, imagename=None):
        super().__init__()

        # Initialize bundled fonts before any widgets are created
        theme_manager.initialize_fonts()

        self.setWindowTitle("SolarViewer")

        screen = QApplication.primaryScreen().availableGeometry()
        # Use 90% of available screen size, capped at reasonable maximums
        width = min(int(screen.width() * 0.90), 1920)
        height = min(int(screen.height() * 0.90), 1080)
        self.resize(width, height)

        # Use custom tab widget
        self.tab_widget = CustomTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.tabCloseRequested.connect(self.close_tab)
        self.tab_widget.add_tab_button.clicked.connect(self.handle_add_tab)

        self.setCentralWidget(self.tab_widget)
        self.tabs = []
        self.max_tabs = 10
        self.settings = QSettings("SolarViewer", "SolarViewer")
        self._open_dialogs = []  # Track non-modal dialogs to prevent garbage collection

        # Remote mode state
        self.remote_connection = None  # SSHConnection when connected
        self.remote_cache = None  # RemoteFileCache for downloaded files

        # Remote connection status indicator in status bar (clickable)
        self.remote_status_btn = QPushButton("🔌 Not Connected")
        self.remote_status_btn.setFlat(True)
        self.remote_status_btn.setCursor(Qt.PointingHandCursor)
        self.remote_status_btn.setToolTip("Click to connect to a remote server")
        self.remote_status_btn.clicked.connect(self._on_remote_status_clicked)
        self._apply_remote_status_style(connected=False)
        self.statusBar().addPermanentWidget(self.remote_status_btn)

        # Update Notification Button (Hidden by default)
        self.update_btn = QPushButton("🚀 Update Available")
        self.update_btn.setFlat(True)
        self.update_btn.setCursor(Qt.PointingHandCursor)
        self._update_update_btn_style()  # Set initial style
        self.update_btn.setVisible(False)
        self.update_btn.clicked.connect(self._on_update_clicked)
        self.statusBar().addPermanentWidget(self.update_btn)

        # Start update check in background
        self._update_thread = CheckUpdateThread()
        self._update_thread.update_available.connect(self._on_update_available)
        self._update_thread.start()

        # Register for theme changes
        theme_manager.register_callback(self._on_theme_changed)

        self.statusBar().showMessage("Ready")
        self.create_menus()
        
        # Set hand cursor for all interactive elements programmatically
        self._set_hand_cursor_recursive(self)

        first_tab = self.add_new_tab("Tab1")
        if imagename and os.path.exists(imagename):
            first_tab.imagename = imagename
            first_tab.dir_entry.setText(imagename)
            # Delay to setup the tab before calling on_visualization_changed
            QTimer.singleShot(
                20, lambda: first_tab.on_visualization_changed(dir_load=True)
            )

            # first_tab.on_visualization_changed(dir_load=True)
            first_tab.update_tab_name_from_path(imagename)
            # Scan directory for file navigation (delay to ensure UI is ready)
            QTimer.singleShot(100, first_tab._scan_directory_files)
            # first_tab.auto_minmax()
        """else:
            first_tab.imagename = get_resource_path("assets/splash.fits")
            QTimer.singleShot(
                20,
                lambda: first_tab.on_visualization_changed(
                    dir_load=True, colormap_name="inferno"
                ),
            )"""

        # Ensure add button is visible after initialization
        QTimer.singleShot(200, self.ensureAddButtonVisible)

    def _update_update_btn_style(self):
        """Update the update notification button style based on theme."""
        if not hasattr(self, "update_btn"):
            return

        is_dark = theme_manager.is_dark

        if is_dark:
            # Current style that looks good in dark mode
            color = "#10b981"  # Emerald 500
            bg_hover = "rgba(16, 185, 129, 0.1)"
            border_color = "#10b981"
        else:
            # Darker green for light theme (Emerald 600)
            color = "#059669"
            bg_hover = "rgba(5, 150, 105, 0.1)"
            border_color = "#059669"

        self.update_btn.setStyleSheet(
            f"""
            QPushButton {{
                color: {color};
                font-weight: bold;
                padding: 0 4px;
                border: 1px solid {border_color};
                border-radius: 4px;
                background-color: transparent;
            }}
            QPushButton:hover {{
                background-color: {bg_hover};
            }}
        """
        )

    def _on_update_available(self, latest_ver):
        """Show the update button when an update is found."""
        self.update_btn.setText(f"🚀 Update Available: {latest_ver}")
        self.update_btn.setToolTip(
            f"A new version ({latest_ver}) is available! Click to learn more."
        )
        self.update_btn.setVisible(True)
        self.latest_version = latest_ver

    def _on_update_clicked(self):
        """Show the software update dialog."""
        self.show_update_dialog()

    def ensureAddButtonVisible(self):
        """Make sure the add button is visible and on top"""
        if (
            hasattr(self.tab_widget, "add_tab_button")
            and self.tab_widget.add_tab_button
        ):
            self.tab_widget.add_tab_button.show()
            self.tab_widget.add_tab_button.raise_()

    def close_tab(self, index):
        """Close the tab at the given index"""
        if len(self.tabs) <= 1:
            QMessageBox.warning(
                self, "Cannot Close", "At least one tab must remain open."
            )
            return

        if index >= 0 and index < len(self.tabs):
            # Clean up temp files before removing tab
            tab = self.tabs[index]
            import os

            if hasattr(tab, "_hpc_temp_file") and tab._hpc_temp_file:
                if os.path.exists(tab._hpc_temp_file):
                    try:
                        os.remove(tab._hpc_temp_file)
                    except:
                        pass
            if hasattr(tab, "_tb_temp_file") and tab._tb_temp_file:
                if os.path.exists(tab._tb_temp_file):
                    try:
                        os.remove(tab._tb_temp_file)
                    except:
                        pass

            self.tab_widget.removeTab(index)
            del self.tabs[index]

    def close_current_tab(self):
        """Close the currently active tab"""
        current_idx = self.tab_widget.currentIndex()
        self.close_tab(current_idx)

    def handle_add_tab(self):
        """Handle the add tab button click"""
        if len(self.tabs) >= self.max_tabs:
            QMessageBox.warning(
                self,
                "Maximum Tabs Reached",
                f"Cannot create more than {self.max_tabs} tabs.",
            )
            return

        tab_count = len(self.tabs) + 1
        tab_name = f"Tab{tab_count}"
        self.add_new_tab(tab_name)

        # Ensure add button is visible after adding a new tab
        QTimer.singleShot(100, self.ensureAddButtonVisible)

    def show_log_console(self):
        """Show the embedded log console dialog."""
        try:
            from .log_console import LogConsole

            console = LogConsole.get_instance()
            console.show()
            console.raise_()
            console.activateWindow()
        except ImportError:
            self.show_status_message("Error: Log Console module not found")
        except Exception as e:
            print(f"[ERROR] Failed to open log console: {e}")
            self.show_status_message(f"Error opening log console: {e}")

    def create_menus(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("&File")

        open_act = QAction("Open Solar Radio Image...", self)
        open_act.setShortcut("Ctrl+O")
        open_act.setStatusTip("Open a Solar Radio Image directory")
        open_act.triggered.connect(self.select_directory)
        file_menu.addAction(open_act)

        open_fits_act = QAction("Open FITS File...", self)
        open_fits_act.setShortcut("Ctrl+Shift+O")
        open_fits_act.setStatusTip("Open a FITS file")
        open_fits_act.triggered.connect(self.select_fits_file)
        file_menu.addAction(open_fits_act)

        file_menu.addSeparator()

        # Remote mode actions
        self.connect_remote_act = QAction("🌐 Connect to Remote Server...", self)
        self.connect_remote_act.setShortcut("Ctrl+Shift+R")
        self.connect_remote_act.setStatusTip("Connect to a remote server via SSH")
        self.connect_remote_act.triggered.connect(self.show_remote_connection_dialog)
        file_menu.addAction(self.connect_remote_act)

        self.browse_remote_act = QAction("📁 Open Remote FITS File...", self)
        self.browse_remote_act.setStatusTip(
            "Open a FITS file from the connected remote server"
        )
        self.browse_remote_act.triggered.connect(self.show_remote_file_browser)
        self.browse_remote_act.setVisible(False)  # Hidden until connected
        file_menu.addAction(self.browse_remote_act)

        self.disconnect_remote_act = QAction("❌ Disconnect from Remote", self)
        self.disconnect_remote_act.setStatusTip("Disconnect from the remote server")
        self.disconnect_remote_act.triggered.connect(self.disconnect_remote)
        self.disconnect_remote_act.setVisible(False)  # Hidden until connected
        file_menu.addAction(self.disconnect_remote_act)

        self.clear_cache_act = QAction("🗑️ Manage Cache...", self)
        self.clear_cache_act.setStatusTip(
            "View and manage cached data (remote files, NOAA events, etc.)"
        )
        self.clear_cache_act.triggered.connect(self.show_cache_manager)
        file_menu.addAction(self.clear_cache_act)

        # Update cache size when menu shows
        file_menu.aboutToShow.connect(self._update_cache_menu_item)

        file_menu.addSeparator()

        export_act = QAction("Export Figure", self)
        export_act.setShortcut("Ctrl+E")
        export_act.setStatusTip("Export current figure as image file")
        export_act.triggered.connect(self.export_data)
        file_menu.addAction(export_act)

        export_data_act = QAction("Export as FITS", self)
        export_data_act.setShortcut("Ctrl+F")
        export_data_act.setStatusTip("Export current data as FITS file")
        export_data_act.triggered.connect(self.export_as_fits)
        file_menu.addAction(export_data_act)

        export_casa_act = QAction("Export as CASA Image", self)
        export_casa_act.setStatusTip("Export/convert current data to CASA image format")
        export_casa_act.triggered.connect(self.export_as_casa_image)
        file_menu.addAction(export_casa_act)

        export_tb_act = QAction("Export TB Map as FITS", self)
        export_tb_act.setStatusTip(
            "Convert and save brightness temperature map as FITS file"
        )
        export_tb_act.triggered.connect(self.save_tb_map_as_fits)
        file_menu.addAction(export_tb_act)

        export_hpc_fits_act = QAction("Export as HPC FITS", self)
        export_hpc_fits_act.setShortcut("Ctrl+H")
        export_hpc_fits_act.setStatusTip(
            "Export current image as helioprojective FITS file"
        )
        export_hpc_fits_act.triggered.connect(self.export_as_hpc_fits)
        file_menu.addAction(export_hpc_fits_act)

        file_menu.addSeparator()
        exit_act = QAction("Exit", self)
        exit_act.setShortcut("Ctrl+Q")
        exit_act.setStatusTip("Exit the application")
        exit_act.triggered.connect(self.close)
        file_menu.addAction(exit_act)

        # View menu with theme toggle
        view_menu = menubar.addMenu("&View")

        # Theme toggle action
        self.theme_action = QAction("🌙 Switch to Light Mode", self)
        self.theme_action.setShortcut("Ctrl+D")
        self.theme_action.setStatusTip("Toggle between dark and light themes")
        self.theme_action.triggered.connect(self.toggle_theme)
        view_menu.addAction(self.theme_action)

        # Fullscreen toggle action
        self.fullscreen_action = QAction("Toggle Fullscreen", self)
        self.fullscreen_action.setShortcut("F11")
        self.fullscreen_action.setStatusTip("Toggle fullscreen mode")
        self.fullscreen_action.triggered.connect(self._toggle_fullscreen)
        view_menu.addAction(self.fullscreen_action)

        # Log Console action
        self.log_console_action = QAction("Show Log Console", self)
        self.log_console_action.setShortcut("Ctrl+L")
        self.log_console_action.setStatusTip("Show application log console")
        self.log_console_action.triggered.connect(self.show_log_console)
        view_menu.addAction(self.log_console_action)

        view_menu.addSeparator()

        # Preferences action
        preferences_action = QAction("⚙️ Preferences...", self)
        # preferences_action.setShortcut("Ctrl+,")
        preferences_action.setStatusTip("Open application preferences (UI scale, etc.)")
        preferences_action.triggered.connect(self._open_preferences_dialog)
        view_menu.addAction(preferences_action)

        # Update action text based on current theme
        self._update_theme_action_text()

        # Register for theme changes
        theme_manager.register_callback(self._on_theme_changed)

        tools_menu = menubar.addMenu("&Tools")
        from .dialogs import BatchProcessDialog, ImageInfoDialog

        # batch_act = QAction("Batch Processing", self)
        # batch_act.setShortcut("Ctrl+B")
        # batch_act.setStatusTip("Process multiple images in batch mode")
        # batch_act.triggered.connect(self.show_batch_dialog)
        # tools_menu.addAction(batch_act)

        metadata_act = QAction("Image Metadata", self)
        metadata_act.setShortcut("Ctrl+M")
        metadata_act.setStatusTip("View detailed metadata for the current image")
        metadata_act.triggered.connect(self.show_metadata)
        tools_menu.addAction(metadata_act)

        # Add Solar Phase Shift option
        from .dialogs import PhaseShiftDialog

        phase_shift_act = QAction("Solar Phase Center Shift", self)
        phase_shift_act.setShortcut("Ctrl+P")
        phase_shift_act.setStatusTip("Shift solar center to phase center")
        phase_shift_act.triggered.connect(self.show_phase_shift_dialog)
        tools_menu.addAction(phase_shift_act)        
        
        # Add Batch HPC Conversion option
        from .dialogs import HPCBatchConversionDialog
        batch_hpc_act = QAction("Batch HPC Conversion", self)
        batch_hpc_act.setStatusTip(
            "Convert multiple files to helioprojective coordinates"
        )
        batch_hpc_act.triggered.connect(self.show_batch_hpc_dialog)
        tools_menu.addAction(batch_hpc_act)

        # Add Create Video action
        create_video_act = QAction("Create &Video", self)
        create_video_act.setStatusTip("Create video from sequence of FITS files")
        create_video_act.triggered.connect(self.show_create_video_dialog)
        tools_menu.addAction(create_video_act)

        tools_menu.addSeparator()

        # Add NOAA Solar Events Viewer
        noaa_events_act = QAction("Solar Activity Viewer", self)
        noaa_events_act.setStatusTip("View solar activity for a specific date")
        noaa_events_act.triggered.connect(self.show_noaa_events_viewer)
        tools_menu.addAction(noaa_events_act)

        # Add Helioviewer Browser
        helioviewer_action = QAction("Helioviewer Browser", self)
        helioviewer_action.setStatusTip("Browse solar images from Helioviewer")
        helioviewer_action.triggered.connect(self.open_helioviewer_browser)
        tools_menu.addAction(helioviewer_action)        
        
        # Dynamic Spectrum Viewer
        dynamic_spectrum_act = QAction("Dynamic Spectrum Viewer", self)
        dynamic_spectrum_act.setStatusTip("View and clean dynamic spectra FITS files")
        dynamic_spectrum_act.triggered.connect(self._launch_dynamic_spectrum_viewer)
        tools_menu.addAction(dynamic_spectrum_act)

        region_menu = menubar.addMenu("&Region")
        subimg_act = QAction("Export Sub-Image (ROI)", self)
        subimg_act.setShortcut("Ctrl+S")
        subimg_act.setStatusTip("Export the selected region as a new CASA image")
        subimg_act.triggered.connect(self.save_sub_image)
        region_menu.addAction(subimg_act)

        export_roi_act = QAction("Export ROI as Region", self)
        export_roi_act.setShortcut("Ctrl+R")
        export_roi_act.setStatusTip("Export the selected region as a CASA region file")
        export_roi_act.triggered.connect(self.export_casa_region)
        region_menu.addAction(export_roi_act)

        fitting_menu = menubar.addMenu("F&itting")
        gauss_act = QAction("Fit 2D Gaussian", self)
        gauss_act.setShortcut("Ctrl+G")
        gauss_act.setStatusTip("Fit a 2D Gaussian to the selected region")
        gauss_act.triggered.connect(self.fit_2d_gaussian)
        fitting_menu.addAction(gauss_act)
        ring_act = QAction("Fit Elliptical Ring", self)
        # ring_act.setShortcut("Ctrl+L")
        ring_act.setStatusTip("Fit an elliptical ring to the selected region")
        ring_act.triggered.connect(self.fit_2d_ring)
        fitting_menu.addAction(ring_act)

        annot_menu = menubar.addMenu("&Annotations")
        text_act = QAction("Add Text Annotation", self)
        text_act.setShortcut("Ctrl+T")
        text_act.setStatusTip("Add text annotation to the image")
        text_act.triggered.connect(self.add_text_annotation)
        annot_menu.addAction(text_act)

        arrow_act = QAction("Add Arrow Annotation", self)
        arrow_act.setShortcut("Ctrl+A")
        arrow_act.setStatusTip("Add arrow annotation to the image")
        arrow_act.triggered.connect(self.add_arrow_annotation)
        annot_menu.addAction(arrow_act)

        preset_menu = menubar.addMenu("Presets")
        auto_minmax_act = QAction("Auto Min/Max", self)
        auto_minmax_act.setShortcut("F5")
        auto_minmax_act.setStatusTip("Set display range to data min/max")
        auto_minmax_act.triggered.connect(self.auto_minmax)
        preset_menu.addAction(auto_minmax_act)
        auto_percentile_act = QAction("Auto Percentile (1%,99%)", self)
        auto_percentile_act.setShortcut("F6")
        auto_percentile_act.setStatusTip(
            "Set display range to 1st and 99th percentiles"
        )
        auto_percentile_act.triggered.connect(self.auto_percentile)
        preset_menu.addAction(auto_percentile_act)
        auto_percentile_act_99 = QAction("Auto Percentile (0.1%,99.9%)", self)
        auto_percentile_act_99.setStatusTip(
            "Set display range to 0.1st and 99.9th percentiles"
        )
        auto_percentile_act_99.triggered.connect(self.auto_percentile_99)
        auto_percentile_act_95 = QAction("Auto Percentile (5%,95%)", self)
        auto_percentile_act_95.setStatusTip(
            "Set display range to 5th and 95th percentiles"
        )
        auto_percentile_act_95.triggered.connect(self.auto_percentile_95)
        preset_menu.addAction(auto_percentile_act_99)
        preset_menu.addAction(auto_percentile_act_95)
        auto_median_rms_act = QAction("Auto Median ± 3×RMS", self)
        auto_median_rms_act.setShortcut("F7")
        auto_median_rms_act.setStatusTip("Set display range to median ± 3×RMS")
        auto_median_rms_act.triggered.connect(self.auto_median_rms)
        preset_menu.addAction(auto_median_rms_act)

        # aia_presets_act = QAction("AIA Presets", self)
        # aia_presets_act.setShortcut("F8")
        # aia_presets_act.setStatusTip("Set display range to AIA presets")
        # Create submenu for AIA presets
        aia_presets_submenu = QMenu("AIA Presets", self)

        # Create actions for each AIA wavelength
        aia_94_act = QAction("94 Å", self)
        aia_94_act.setStatusTip("Set display range and colormap for AIA 94 Å")
        aia_94_act.triggered.connect(self.aia_presets_94)
        aia_presets_submenu.addAction(aia_94_act)

        aia_131_act = QAction("131 Å", self)
        aia_131_act.setStatusTip("Set display range and colormap for AIA 131 Å")
        aia_131_act.triggered.connect(self.aia_presets_131)
        aia_presets_submenu.addAction(aia_131_act)

        aia_171_act = QAction("171 Å", self)
        aia_171_act.setStatusTip("Set display range and colormap for AIA 171 Å")
        aia_171_act.triggered.connect(self.aia_presets_171)
        aia_171_act.setShortcut("F8")
        aia_presets_submenu.addAction(aia_171_act)

        aia_193_act = QAction("193 Å", self)
        aia_193_act.setStatusTip("Set display range and colormap for AIA 193 Å")
        aia_193_act.triggered.connect(self.aia_presets_193)
        aia_presets_submenu.addAction(aia_193_act)

        aia_211_act = QAction("211 Å", self)
        aia_211_act.setStatusTip("Set display range and colormap for AIA 211 Å")
        aia_211_act.triggered.connect(self.aia_presets_211)
        aia_presets_submenu.addAction(aia_211_act)

        aia_304_act = QAction("304 Å", self)
        aia_304_act.setStatusTip("Set display range and colormap for AIA 304 Å")
        aia_304_act.triggered.connect(self.aia_presets_304)
        aia_presets_submenu.addAction(aia_304_act)

        aia_335_act = QAction("335 Å", self)
        aia_335_act.setStatusTip("Set display range and colormap for AIA 335 Å")
        aia_335_act.triggered.connect(self.aia_presets_335)
        aia_presets_submenu.addAction(aia_335_act)

        aia_1600_act = QAction("1600 Å", self)
        aia_1600_act.setStatusTip("Set display range and colormap for AIA 1600 Å")
        aia_1600_act.triggered.connect(self.aia_presets_1600)
        aia_presets_submenu.addAction(aia_1600_act)

        aia_1700_act = QAction("1700 Å", self)
        aia_1700_act.setStatusTip("Set display range and colormap for AIA 1700 Å")
        aia_1700_act.triggered.connect(self.aia_presets_1700)
        aia_presets_submenu.addAction(aia_1700_act)

        aia_4500_act = QAction("4500 Å", self)
        aia_4500_act.setStatusTip("Set display range and colormap for AIA 4500 Å")
        aia_4500_act.triggered.connect(self.aia_presets_4500)
        aia_presets_submenu.addAction(aia_4500_act)

        # Add the submenu to the presets menu
        preset_menu.addMenu(aia_presets_submenu)

        hmi_preset_act = QAction("HMI Preset", self)
        hmi_preset_act.setShortcut("F9")
        hmi_preset_act.setStatusTip("Set display range to HMI preset")
        hmi_preset_act.triggered.connect(self.HMI_presets)
        preset_menu.addAction(hmi_preset_act)

        # SOHO/EIT Presets submenu
        eit_presets_submenu = QMenu("SOHO/EIT Presets", self)
        for wl in ["171", "195", "284", "304"]:
            act = QAction(f"{wl} Å", self)
            act.setStatusTip(f"Set preset for SOHO EIT {wl} Å")
            act.triggered.connect(lambda checked, w=wl: self.EIT_presets(int(w)))
            eit_presets_submenu.addAction(act)
        preset_menu.addMenu(eit_presets_submenu)

        # SOHO/LASCO Preset
        lasco_preset_act = QAction("SOHO/LASCO Preset", self)
        lasco_preset_act.setStatusTip("Set display range for LASCO coronagraph")
        lasco_preset_act.triggered.connect(self.LASCO_presets)
        preset_menu.addAction(lasco_preset_act)

        # IRIS SJI Presets submenu
        iris_presets_submenu = QMenu("IRIS SJI Presets", self)
        for wl in ["1330", "1400", "2796", "2832"]:
            act = QAction(f"{wl} Å", self)
            act.setStatusTip(f"Set preset for IRIS SJI {wl} Å")
            act.triggered.connect(lambda checked, w=wl: self.IRIS_presets(int(w)))
            iris_presets_submenu.addAction(act)
        preset_menu.addMenu(iris_presets_submenu)

        # GOES SUVI Presets submenu
        suvi_presets_submenu = QMenu("GOES SUVI Presets", self)
        for wl in ["94", "131", "171", "195", "284", "304"]:
            act = QAction(f"{wl} Å", self)
            act.setStatusTip(f"Set preset for GOES SUVI {wl} Å")
            act.triggered.connect(lambda checked, w=wl: self.SUVI_presets(int(w)))
            suvi_presets_submenu.addAction(act)
        preset_menu.addMenu(suvi_presets_submenu)

        # STEREO SECCHI Presets submenu
        stereo_presets_submenu = QMenu("STEREO Presets", self)
        stereo_euvi_act = QAction("EUVI", self)
        stereo_euvi_act.setStatusTip("Set preset for STEREO EUVI")
        stereo_euvi_act.triggered.connect(lambda: self.STEREO_presets("EUVI"))
        stereo_presets_submenu.addAction(stereo_euvi_act)
        stereo_cor1_act = QAction("COR1", self)
        stereo_cor1_act.setStatusTip("Set preset for STEREO COR1")
        stereo_cor1_act.triggered.connect(lambda: self.STEREO_presets("COR1"))
        stereo_presets_submenu.addAction(stereo_cor1_act)
        stereo_cor2_act = QAction("COR2", self)
        stereo_cor2_act.setStatusTip("Set preset for STEREO COR2")
        stereo_cor2_act.triggered.connect(lambda: self.STEREO_presets("COR2"))
        stereo_presets_submenu.addAction(stereo_cor2_act)
        preset_menu.addMenu(stereo_presets_submenu)

        # GONG Preset
        # gong_preset_act = QAction("GONG Preset", self)
        # gong_preset_act.setStatusTip("Set display range for GONG magnetogram")
        # gong_preset_act.triggered.connect(self.GONG_presets)
        # preset_menu.addAction(gong_preset_act)

        tabs_menu = menubar.addMenu("&Tabs")
        new_tab_act = QAction("Add New Tab", self)
        new_tab_act.setShortcut("Ctrl+N")
        new_tab_act.setStatusTip("Add a new tab for comparing images")
        new_tab_act.triggered.connect(self.handle_add_tab)
        tabs_menu.addAction(new_tab_act)
        close_tab_act = QAction("Close Current Tab", self)
        close_tab_act.setShortcut("Ctrl+W")
        close_tab_act.setStatusTip("Close the current tab")
        close_tab_act.triggered.connect(self.close_current_tab)
        tabs_menu.addAction(close_tab_act)

        # Add Data Download menu after File menu
        download_menu = menubar.addMenu("&Download")

        # GUI Downloader action
        gui_downloader_action = QAction("Non-radio Solar Data Downloader", self)
        gui_downloader_action.setStatusTip(
            "Launch the graphical interface for downloading non-radio solar data"
        )
        gui_downloader_action.triggered.connect(self.launch_data_downloader_gui)
        download_menu.addAction(gui_downloader_action)

        # Radio Data Downloader action
        radio_downloader_action = QAction("Radio Solar Data Downloader", self)
        radio_downloader_action.setStatusTip(
            "Download radio solar data (Learmonth, etc.) and convert to FITS"
        )
        radio_downloader_action.triggered.connect(self.launch_radio_data_downloader_gui)
        download_menu.addAction(radio_downloader_action)

        # CLI Downloader action
        """cli_downloader_action = QAction("Solar Data Downloader (CLI)", self)
        cli_downloader_action.setStatusTip(
            "Launch the command-line interface for downloading solar data"
        )
        cli_downloader_action.triggered.connect(self.launch_data_downloader_cli)
        download_menu.addAction(cli_downloader_action)"""
        help_menu = menubar.addMenu("&Help")

        """tutorial_act = QAction("Tutorial", self)
        tutorial_act.setStatusTip("Open comprehensive tutorial and user guide")
        tutorial_act.triggered.connect(self.show_tutorial)
        help_menu.addAction(tutorial_act)"""

        shortcuts_act = QAction("Keyboard Shortcuts", self)
        shortcuts_act.setShortcut("F1")
        shortcuts_act.setStatusTip("Show keyboard shortcuts")
        shortcuts_act.triggered.connect(self.show_keyboard_shortcuts)
        help_menu.addAction(shortcuts_act)

        update_act = QAction("Check for Updates...", self)
        update_act.setStatusTip("Check for application updates")
        update_act.triggered.connect(self.show_update_dialog)
        help_menu.addAction(update_act)

        about_act = QAction("About", self)
        about_act.setStatusTip("Show information about this application")
        about_act.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_act)


        # Tools menu
        # tools_menu = self.menuBar().addMenu("&Tools")

        # Add Create Video option to Tools menu
        # create_video_action = QAction("Create &Video", self)
        # create_video_action.setStatusTip("Create video from sequence of FITS files")
        # create_video_action.triggered.connect(self.show_create_video_dialog)
        # tools_menu.addAction(create_video_action)

        # Add Batch HPC Conversion option

        # Add LOFAR Tools submenu
        tools_menu.addSeparator()
        lofar_menu = QMenu("LOFAR Tools", self)

        # Calibration Table Visualizer
        caltable_act = QAction("Calibration Table Visualizer", self)
        caltable_act.setStatusTip(
            "Visualize bandpass, selfcal, and crossphase calibration tables"
        )
        caltable_act.triggered.connect(self._launch_caltable_visualizer)
        lofar_menu.addAction(caltable_act)

        # Log Viewer
        log_viewer_act = QAction("Log Viewer", self)
        log_viewer_act.setStatusTip("View and filter log files")
        log_viewer_act.triggered.connect(self._launch_log_viewer)
        lofar_menu.addAction(log_viewer_act)

        # Create Dynamic Spectra
        lofar_menu.addSeparator()
        create_ds_act = QAction("Create Dynamic Spectra...", self)
        create_ds_act.setStatusTip("Create dynamic spectra FITS from MS files")
        create_ds_act.triggered.connect(self._launch_create_dynamic_spectra)
        lofar_menu.addAction(create_ds_act)

        tools_menu.addMenu(lofar_menu)

    def add_new_tab(self, name):
        if len(self.tabs) >= self.max_tabs:
            QMessageBox.warning(
                self, "Tab Limit", f"Maximum of {self.max_tabs} tabs allowed."
            )
            return None

        new_tab = SolarRadioImageTab(self, name)
        self.tabs.append(new_tab)
        self.tab_widget.addTab(new_tab, name)
        self.tab_widget.setCurrentWidget(new_tab)

        # Refresh icons to match current theme
        new_tab.refresh_icons()
        
        # Apply hand cursors to the new tab
        self._set_hand_cursor_recursive(new_tab)

        # Ensure add button is visible after adding a new tab
        QTimer.singleShot(100, self.ensureAddButtonVisible)

        return new_tab

    def toggle_theme(self):
        """Toggle between dark and light themes."""
        new_theme = theme_manager.toggle_theme()
        self.statusBar().showMessage(
            f"Switched to {'Light' if new_theme == 'light' else 'Dark'} mode"
        )

    def _toggle_fullscreen(self):
        """Toggle fullscreen mode for the main window."""
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _open_preferences_dialog(self):
        """Open the application preferences dialog (non-modal)."""
        # Reuse existing dialog or create new one
        if (
            hasattr(self, "_preferences_dialog")
            and self._preferences_dialog is not None
        ):
            try:
                self._preferences_dialog.raise_()
                self._preferences_dialog.activateWindow()
                return
            except RuntimeError:
                # Dialog was deleted
                self._preferences_dialog = None

        from .dialogs import PreferencesDialog

        self._preferences_dialog = PreferencesDialog(self)
        self._preferences_dialog.setAttribute(Qt.WA_DeleteOnClose)
        self._preferences_dialog.destroyed.connect(
            lambda: setattr(self, "_preferences_dialog", None)
        )
        self._preferences_dialog.show()

    def _on_theme_changed(self, new_theme):
        """Handle theme change events."""
        # Update matplotlib rcParams
        update_matplotlib_theme()

        # Update Qt application palette BEFORE refreshing icons
        # This is critical for matplotlib's NavigationToolbar which reads icon
        # colors from the palette's foreground role
        self._update_application_palette()

        # Update theme action text
        self._update_theme_action_text()

        # Update stylesheet and icons for all tabs
        stylesheet = get_stylesheet(theme_manager.palette, theme_manager.is_dark)
        for tab in self.tabs:
            tab.setStyleSheet(stylesheet)
            tab.refresh_icons()
            # if hasattr(tab, '_available_stokes') and tab._available_stokes:
            #    tab._update_stokes_combo_state(tab._available_stokes)

        # Refresh tab bar theme (colors and icons)
        if hasattr(self, "tab_widget") and hasattr(
            self.tab_widget.tabBar(), "refresh_theme"
        ):
            self.tab_widget.tabBar().refresh_theme()

        # Refresh all matplotlib plots
        self.refresh_all_plots()

        # Re-apply hand cursors to any recreated or theme-affected widgets
        self._set_hand_cursor_recursive(self)

    def _set_hand_cursor_recursive(self, widget):
        """Recursively set PointingHandCursor for all buttons and interactive widgets."""
        set_hand_cursor(widget)

        # Update notification button style
        self._update_update_btn_style()

    def _update_theme_action_text(self):
        """Update theme toggle action text based on current theme."""
        if theme_manager.is_dark:
            self.theme_action.setText("☀️ Switch to Light Mode")
        else:
            self.theme_action.setText("🌙 Switch to Dark Mode")

    def _update_application_palette(self):
        """Update the Qt application palette to match the current theme.

        This is critical for matplotlib's NavigationToolbar which reads icon
        colors from the palette's foreground role (WindowText color).
        """
        palette = theme_manager.palette
        qt_palette = QPalette()
        qt_palette.setColor(QPalette.Window, QColor(palette["window"]))
        qt_palette.setColor(QPalette.WindowText, QColor(palette["text"]))
        qt_palette.setColor(QPalette.Base, QColor(palette["base"]))
        qt_palette.setColor(QPalette.AlternateBase, QColor(palette["surface"]))
        qt_palette.setColor(QPalette.Text, QColor(palette["text"]))
        qt_palette.setColor(QPalette.Button, QColor(palette["button"]))
        qt_palette.setColor(QPalette.ButtonText, QColor(palette["text"]))
        qt_palette.setColor(QPalette.Highlight, QColor(palette["highlight"]))
        qt_palette.setColor(QPalette.HighlightedText, Qt.white)
        qt_palette.setColor(QPalette.Link, QColor(palette["highlight"]))
        qt_palette.setColor(
            QPalette.Disabled, QPalette.Text, QColor(palette["disabled"])
        )
        qt_palette.setColor(
            QPalette.Disabled, QPalette.ButtonText, QColor(palette["disabled"])
        )

        app = QApplication.instance()
        if app:
            app.setPalette(qt_palette)
            app.setStyleSheet(theme_manager.stylesheet)

    def refresh_all_plots(self):
        """Refresh all matplotlib plots to apply new theme colors."""
        palette = theme_manager.palette
        is_dark = theme_manager.is_dark

        # Use plot-specific colors for light mode, otherwise use standard palette
        if is_dark:
            fig_bg = palette["window"]
            axes_bg = palette["base"]
            text_color = palette["text"]
        else:
            fig_bg = palette.get("plot_bg", "#ffffff")
            axes_bg = palette.get("plot_bg", "#ffffff")
            text_color = palette.get("plot_text", "#1a1a1a")

        for tab in self.tabs:
            if hasattr(tab, "figure") and tab.figure:
                # Update figure background
                tab.figure.set_facecolor(fig_bg)

                # Update axes if present
                for ax in tab.figure.get_axes():
                    ax.set_facecolor(axes_bg)
                    ax.tick_params(colors=text_color)
                    ax.xaxis.label.set_color(text_color)
                    ax.yaxis.label.set_color(text_color)
                    ax.title.set_color(text_color)
                    for spine in ax.spines.values():
                        spine.set_color(text_color)

                    # Handle WCSAxes
                    # WCSAxes use ax.coords for coordinate handling instead of standard xaxis/yaxis
                    if hasattr(ax, "coords"):
                        try:
                            # Update axis labels and tick labels for each coordinate
                            for coord in ax.coords:
                                # Get current label text and set with new color
                                current_label = coord.get_axislabel()
                                if current_label:
                                    coord.set_axislabel(current_label, color=text_color)
                                coord.set_ticklabel(color=text_color)
                                coord.set_ticks(color=text_color)

                            # Update frame (border) color for WCSAxes
                            if hasattr(ax.coords, "frame"):
                                ax.coords.frame.set_color(text_color)
                        except Exception as e:
                            print(f"[ERROR] Error updating WCSAxes colors: {e}")
                            self.show_status_message(
                                f"Error updating WCSAxes colors: {e}"
                            )

                # Redraw canvas
                if hasattr(tab, "canvas") and tab.canvas:
                    tab.canvas.draw_idle()

    def select_directory(self):
        """Select a CASA image directory from the menu"""
        current_tab = self.tab_widget.currentWidget()
        if current_tab:
            # Set the radio button to CASA image
            current_tab.radio_casa_image.setChecked(True)
            # Call the select_file_or_directory method
            current_tab.select_file_or_directory()

    def select_fits_file(self):
        """Select a FITS file from the menu"""
        current_tab = self.tab_widget.currentWidget()
        if current_tab:
            # Set the radio button to FITS file
            current_tab.radio_fits_file.setChecked(True)
            # Call the select_file_or_directory method
            current_tab.select_file_or_directory()

    def auto_minmax(self):
        current_tab = self.tab_widget.currentWidget()
        if current_tab:
            current_tab.auto_minmax()

    def auto_percentile(self):
        current_tab = self.tab_widget.currentWidget()
        if current_tab:
            current_tab.auto_percentile()

    def auto_percentile_99(self):
        current_tab = self.tab_widget.currentWidget()
        if current_tab:
            current_tab.auto_percentile_99()

    def auto_percentile_95(self):
        current_tab = self.tab_widget.currentWidget()
        if current_tab:
            current_tab.auto_percentile_95()

    def auto_median_rms(self):
        current_tab = self.tab_widget.currentWidget()
        if current_tab:
            current_tab.auto_median_rms()

    def aia_presets(self, wavelength=171):
        """
        Apply AIA preset for the specified wavelength.

        Args:
            wavelength (int): AIA wavelength in Angstroms (94, 131, 171, 193, 211, 304, 335, 1600, 1700, 4500)
        """
        current_tab = self.tab_widget.currentWidget()
        if current_tab:
            current_tab.aia_presets(wavelength)

    def HMI_presets(self):
        """HMI preset"""
        current_tab = self.tab_widget.currentWidget()
        if current_tab:
            current_tab.HMI_presets()

    def EIT_presets(self, wavelength=171):
        """SOHO EIT preset"""
        current_tab = self.tab_widget.currentWidget()
        if current_tab:
            current_tab.EIT_presets(wavelength)

    def LASCO_presets(self, detector="C2"):
        """SOHO LASCO preset"""
        current_tab = self.tab_widget.currentWidget()
        if current_tab:
            current_tab.LASCO_presets(detector)

    def IRIS_presets(self, wavelength=1330):
        """IRIS SJI preset"""
        current_tab = self.tab_widget.currentWidget()
        if current_tab:
            current_tab.IRIS_presets(wavelength)

    def SUVI_presets(self, wavelength=171):
        """GOES SUVI preset"""
        current_tab = self.tab_widget.currentWidget()
        if current_tab:
            current_tab.SUVI_presets(wavelength)

    def GONG_presets(self):
        """GONG magnetogram preset"""
        current_tab = self.tab_widget.currentWidget()
        if current_tab:
            current_tab.GONG_presets()

    def STEREO_presets(self, detector="EUVI"):
        """STEREO SECCHI preset"""
        current_tab = self.tab_widget.currentWidget()
        if current_tab:
            current_tab.STEREO_presets(detector)

    def export_data(self):
        current_tab = self.tab_widget.currentWidget()
        if not current_tab:
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Figure",
            "",
            "PNG Files (*.png);;PDF Files (*.pdf);;All Files (*)",
        )
        if path:
            current_tab.figure.savefig(path, dpi=300, bbox_inches="tight")
            QMessageBox.information(self, "Exported", f"Figure saved to {path}")

    def export_as_fits(self):
        """Export current image data as FITS file with all metadata preserved."""
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or current_tab.current_image_data is None:
            QMessageBox.warning(self, "No Data", "No image data to export")
            return

        try:
            import os
            from astropy.io import fits

            path, _ = QFileDialog.getSaveFileName(
                self, "Export as FITS", "", "FITS Files (*.fits);;All Files (*)"
            )
            if not path:
                return

            # Check if source is a CASA image (directory)
            if hasattr(current_tab, "imagename") and current_tab.imagename:
                source_image = current_tab.imagename
            else:
                source_image = None

            # If source is a CASA image directory, use casatask exportfits
            if source_image and os.path.isdir(source_image):
                try:
                    import subprocess
                    import sys

                    # Run exportfits in subprocess with stokes='all'
                    source_image_abs = os.path.abspath(source_image)
                    path_abs = os.path.abspath(path)

                    script = f"""
import sys
from casatasks import exportfits
try:
    exportfits(imagename="{source_image_abs}", fitsimage="{path_abs}", overwrite=True, stokeslast=True)
    print("SUCCESS")
except Exception as e:
    print(f"ERROR: {{e}}", file=sys.stderr)
    sys.exit(1)
"""
                    result = subprocess.run(
                        [sys.executable, "-c", script],
                        capture_output=True,
                        text=True,
                        cwd=os.getcwd() if os.access(os.getcwd(), os.W_OK) else tempfile.gettempdir(),
                    )

                    if result.returncode != 0:
                        raise RuntimeError(f"exportfits failed: {result.stderr}")

                    QMessageBox.information(
                        self, "Exported", f"CASA image exported to {path}"
                    )
                    return
                except Exception as e:
                    QMessageBox.critical(
                        self, "Export Error", f"Failed to export CASA image: {str(e)}"
                    )
                    return

            # For FITS files, copy the original with all data and metadata
            if source_image and (
                source_image.endswith(".fits") or source_image.endswith(".fts")
            ):
                try:
                    import shutil

                    # If saving to a different location, copy the original file
                    if os.path.abspath(source_image) != os.path.abspath(path):
                        shutil.copy2(source_image, path)
                        QMessageBox.information(
                            self, "Exported", f"FITS file exported to {path}"
                        )
                    else:
                        QMessageBox.warning(
                            self,
                            "Same File",
                            "Source and destination are the same file.",
                        )
                    return
                except Exception as e:
                    # Fall back to creating from current data if copy fails
                    pass

            # Fallback: create FITS from current_image_data with header
            # Get the original header if available
            original_header = None
            if hasattr(current_tab, "current_header") and current_tab.current_header:
                original_header = current_tab.current_header

            # Create HDU with data
            if original_header:
                # Convert dict to FITS Header if needed
                if isinstance(original_header, dict):
                    header = fits.Header()
                    for key, value in original_header.items():
                        # Skip problematic keys and ensure valid FITS keywords
                        if key in ["SIMPLE", "BITPIX", "NAXIS", "EXTEND", ""]:
                            continue
                        if key.startswith("NAXIS"):
                            continue
                        try:
                            # Handle COMMENT and HISTORY specially
                            if key in ["COMMENT", "HISTORY"]:
                                if isinstance(value, list):
                                    for v in value:
                                        header[key] = str(v)
                                else:
                                    header[key] = str(value)
                            else:
                                header[key] = value
                        except (ValueError, KeyError):
                            # Skip keys that can't be added
                            pass
                    hdu = fits.PrimaryHDU(current_tab.current_image_data, header=header)
                else:
                    # Already a FITS header
                    hdu = fits.PrimaryHDU(
                        current_tab.current_image_data, header=original_header
                    )
            else:
                # No header available, create basic HDU
                hdu = fits.PrimaryHDU(current_tab.current_image_data)

            # Add HISTORY entry
            hdu.header.add_history("Exported with SolarViewer")

            hdul = fits.HDUList([hdu])
            hdul.writeto(path, overwrite=True)
            QMessageBox.information(self, "Exported", f"Data saved to {path}")
        except ImportError:
            QMessageBox.warning(
                self,
                "Missing Dependency",
                "Astropy is required for FITS export. Please install it.",
            )
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export: {str(e)}")

    def export_as_casa_image(self):
        """Export/convert current image to CASA image format."""
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or current_tab.current_image_data is None:
            QMessageBox.warning(self, "No Data", "No image data to export")
            return

        try:
            import os
            import subprocess
            import sys

            # Get source image path
            if hasattr(current_tab, "imagename") and current_tab.imagename:
                source_image = current_tab.imagename
            else:
                QMessageBox.warning(self, "No Source", "No source image file available")
                return

            # Suggest a default output name based on source
            default_name = (
                os.path.splitext(os.path.basename(source_image))[0] + ".image"
            )

            # Ask user for output path and name
            output_image, _ = QFileDialog.getSaveFileName(
                self,
                "Export as CASA Image",
                default_name,
                "CASA Image (*.image);;All Files (*)",
            )
            if not output_image:
                return

            # Ensure it has .image extension
            if not output_image.endswith(".image"):
                output_image += ".image"

            # Check if output already exists
            if os.path.exists(output_image):
                reply = QMessageBox.question(
                    self,
                    "Overwrite?",
                    f"'{output_image}' already exists. Do you want to overwrite it?",
                    QMessageBox.Yes | QMessageBox.No,
                )
                if reply == QMessageBox.No:
                    return
                import shutil

                shutil.rmtree(output_image)

            # If source is already a CASA image, copy it
            if os.path.isdir(source_image):
                import shutil

                shutil.copytree(source_image, output_image)
                QMessageBox.information(
                    self, "Exported", f"CASA image exported to {output_image}"
                )
                return

            # If source is a FITS file, use importfits via subprocess
            if source_image.endswith(".fits") or source_image.endswith(".fts"):
                # Check coordinate system
                import tempfile

                fits_to_import = source_image
                is_hpc_converted = False

                try:
                    from astropy.io import fits as afits

                    with afits.open(source_image) as hdul:
                        header = hdul[0].header
                        ctype1 = header.get("CTYPE1", "").upper()
                        ctype2 = header.get("CTYPE2", "").upper()

                        # Check for helioprojective coordinates
                        if (
                            "HPLN" in ctype1
                            or "HPLN" in ctype2
                            or "HPLT" in ctype1
                            or "HPLT" in ctype2
                        ):
                            # Convert HPC to RA/Dec first
                            from .helioprojective import convert_hpc_to_radec

                            temp_radec_file = os.path.join(
                                tempfile.gettempdir(),
                                f"solarviewer_radec_export_{os.getpid()}.fits",
                            )

                            QApplication.setOverrideCursor(Qt.WaitCursor)
                            success = convert_hpc_to_radec(
                                source_image, temp_radec_file, overwrite=True
                            )
                            QApplication.restoreOverrideCursor()

                            if not success:
                                QMessageBox.warning(
                                    self,
                                    "Conversion Failed",
                                    "Failed to convert helioprojective coordinates to RA/Dec.",
                                )
                                return

                            fits_to_import = temp_radec_file
                            is_hpc_converted = True

                        # Check for other unsupported coordinate systems
                        elif not (
                            "RA" in ctype1 or "DEC" in ctype2 or "GLON" not in ctype1
                        ):
                            # Block galactic, ecliptic, and other non-celestial coordinates
                            unsupported_types = []
                            if "GLON" in ctype1 or "GLAT" in ctype2:
                                unsupported_types.append("Galactic")
                            elif "ELON" in ctype1 or "ELAT" in ctype2:
                                unsupported_types.append("Ecliptic")
                            elif "SLON" in ctype1 or "SLAT" in ctype2:
                                unsupported_types.append("Supergalactic")
                            else:
                                unsupported_types.append(f"{ctype1}/{ctype2}")

                            if unsupported_types:
                                QMessageBox.warning(
                                    self,
                                    "Unsupported Coordinates",
                                    f"This FITS file uses {', '.join(unsupported_types)} coordinates.\n\n"
                                    "Only RA/Dec (celestial) and helioprojective (Solar-X/Y) "
                                    "coordinate systems are supported for CASA export.",
                                )
                                return
                except Exception as e:
                    pass  # If we can't check, proceed anyway

                # Run importfits
                fits_to_import_abs = os.path.abspath(fits_to_import)
                output_image_abs = os.path.abspath(output_image)

                script = f"""
import sys
from casatasks import importfits
try:
    importfits(fitsimage="{fits_to_import_abs}", imagename="{output_image_abs}", overwrite=True)
    print("SUCCESS")
except Exception as e:
    print(f"ERROR: {{e}}", file=sys.stderr)
    sys.exit(1)
"""
                result = subprocess.run(
                    [sys.executable, "-c", script],
                    capture_output=True,
                    text=True,
                    cwd=os.getcwd() if os.access(os.getcwd(), os.W_OK) else tempfile.gettempdir(),
                )

                # Clean up temp file if we created one
                if is_hpc_converted and os.path.exists(fits_to_import):
                    try:
                        os.remove(fits_to_import)
                    except:
                        pass

                if result.returncode != 0:
                    raise RuntimeError(f"importfits failed: {result.stderr}")

                if is_hpc_converted:
                    QMessageBox.information(
                        self,
                        "Exported",
                        f"CASA image exported to:\n{output_image}\n\n"
                        "Note: Helioprojective coordinates were converted to RA/Dec.",
                    )
                else:
                    QMessageBox.information(
                        self,
                        "Exported",
                        f"FITS converted to CASA image: {output_image}",
                    )
                return

            QMessageBox.warning(
                self, "Unsupported", "Source must be a FITS file or CASA image"
            )

        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export: {str(e)}")

    def save_tb_map_as_fits(self):
        """Save brightness temperature map as FITS file"""
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or current_tab.current_image_data is None:
            QMessageBox.warning(self, "No Data", "No image data loaded")
            return

        if not hasattr(current_tab, "imagename") or not current_tab.imagename:
            QMessageBox.warning(self, "No Image", "No image file loaded")
            return

        # Check if TB conversion is available (units must be Jy/beam)
        bunit = getattr(current_tab, "_current_bunit", "").lower()
        if "k" in bunit:
            # Already in temperature units
            QMessageBox.information(
                self,
                "Already TB",
                "Current image is already in temperature units (K).\n"
                "Use 'Export Data as FITS' to save it.",
            )
            return

        if not ("jy" in bunit and "beam" in bunit):
            QMessageBox.warning(
                self,
                "Invalid Units",
                f"TB conversion requires Jy/beam units.\n"
                f"Current units: {bunit or 'unknown'}",
            )
            return

        try:
            from .utils import generate_tb_map

            # Get save path
            default_name = os.path.basename(current_tab.imagename)
            if default_name.endswith(".fits") or default_name.endswith(".fts"):
                default_name = default_name.rsplit(".", 1)[0] + "_TB.fits"
            else:
                default_name = default_name + "_TB.fits"

            path, _ = QFileDialog.getSaveFileName(
                self,
                "Save TB Map as FITS",
                default_name,
                "FITS Files (*.fits);;All Files (*)",
            )

            if not path:
                return

            QApplication.setOverrideCursor(Qt.WaitCursor)

            # Generate and save TB map
            tb_data, result = generate_tb_map(
                current_tab.imagename,
                outfile=path,
                flux_data=current_tab.current_image_data,
            )

            QApplication.restoreOverrideCursor()

            if tb_data is None:
                QMessageBox.warning(self, "Error", f"TB conversion failed: {result}")
            else:
                QMessageBox.information(self, "Saved", f"TB map saved to:\n{path}")

        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Error", f"Failed to save TB map: {str(e)}")

    def show_batch_dialog(self):
        from .dialogs import BatchProcessDialog

        dialog = BatchProcessDialog(self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(
            lambda: (
                self._open_dialogs.remove(dialog)
                if dialog in self._open_dialogs
                else None
            )
        )
        self._open_dialogs.append(dialog)
        dialog.show()

    def show_metadata(self):
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or not current_tab.imagename:
            QMessageBox.warning(self, "No Image", "No image loaded")
            return

        try:
            metadata = get_image_metadata(current_tab.imagename)
            from .dialogs import ImageInfoDialog
            dialog = ImageInfoDialog(
                self, metadata=metadata, imagename=current_tab.imagename
            )
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            dialog.destroyed.connect(
                lambda: (
                    self._open_dialogs.remove(dialog)
                    if dialog in self._open_dialogs
                    else None
                )
            )
            self._open_dialogs.append(dialog)
            dialog.show()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to get metadata: {str(e)}")

    def save_sub_image(self):
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or not current_tab.current_roi:
            QMessageBox.warning(self, "No ROI", "Please select a region first")
            return

        if not current_tab.imagename:
            QMessageBox.warning(self, "No Image", "No image loaded")
            return

        output_dir, _ = QFileDialog.getSaveFileName(
            self, "Save Subimage As", "", "CASA Image (*);;All Files (*)"
        )

        if output_dir:
            try:
                ia_tool = IA()
                ia_tool.open(current_tab.imagename)

                if isinstance(current_tab.current_roi, tuple):
                    xlow, xhigh, ylow, yhigh = current_tab.current_roi
                    region_dict = (
                        "box[["
                        + str(xlow)
                        + "pix, "
                        + str(ylow)
                        + "pix],["
                        + str(xhigh)
                        + "pix, "
                        + str(yhigh)
                        + "pix]]"
                    )

                    ia_tool.subimage(outfile=output_dir, region=region_dict)
                else:
                    QMessageBox.information(
                        self,
                        "Not Implemented",
                        "Subimage for polygon/circle ROI not implemented yet",
                    )
                    return

                ia_tool.close()
                QMessageBox.information(
                    self, "Success", f"Subimage saved to {output_dir}"
                )
            except Exception as e:
                QMessageBox.critical(
                    self, "Error", f"Failed to create subimage: {str(e)}"
                )

    def export_casa_region(self):
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or not current_tab.current_roi:
            QMessageBox.warning(self, "No ROI", "Please select a region first")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Region", "", "CASA Region (*.crtf);;All Files (*)"
        )

        if path:
            try:
                with open(path, "w") as f:
                    f.write("#CRTFv0\n")

                if isinstance(current_tab.current_roi, tuple):
                    xlow, xhigh, ylow, yhigh = current_tab.current_roi
                    with open(path, "a") as f:
                        f.write(
                            f"box[[{xlow}pix, {ylow}pix], [{xhigh}pix, {yhigh}pix]]\n"
                        )
                else:
                    with open(path, "a") as f:
                        f.write("# Complex region - simplified representation\n")
                        f.write("circle[[512pix, 512pix], 100pix]\n")

                QMessageBox.information(self, "Success", f"Region saved to {path}")
            except Exception as e:
                QMessageBox.critical(
                    self, "Error", f"Failed to export region: {str(e)}"
                )

    def fit_2d_gaussian(self):
        """Fit a 2D Gaussian to the current image using CASA's imfit task."""
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or not current_tab.imagename:
            QMessageBox.warning(self, "No Image", "Please load an image first.")
            return

        # Require ROI selection - check if selector exists and is visible
        has_visible_roi = False
        if (
            current_tab.current_roi
            and isinstance(current_tab.current_roi, tuple)
            and current_tab.roi_selector
        ):

            # Check if selector's artist is visible
            # Both RectangleSelector and EllipseSelector have 'to_draw' artist
            if hasattr(current_tab.roi_selector, "to_draw"):
                has_visible_roi = current_tab.roi_selector.to_draw.get_visible()
            # Fallback for older matplotlib versions or if to_draw is missing
            elif hasattr(current_tab.roi_selector, "artists"):
                has_visible_roi = any(
                    a.get_visible() for a in current_tab.roi_selector.artists
                )

        if not has_visible_roi:
            QMessageBox.warning(
                self,
                "No ROI",
                "Please select a region of interest (ROI) first.\n\nUse the ROI tool to draw a box around the source.",
            )
            return

        imagename = current_tab.imagename

        # Get current Stokes from left panel (may be in tab or main app)
        # CASA imfit only accepts standard Stokes: I, Q, U, V
        stokes = "I"
        if hasattr(self, "stokes_combo") and self.stokes_combo:
            selected_stokes = self.stokes_combo.currentText()
            if selected_stokes in ["I", "Q", "U", "V"]:
                stokes = selected_stokes
            # For derived parameters (L, Lfrac, etc), fall back to I
        elif hasattr(current_tab, "stokes_combo") and current_tab.stokes_combo:
            selected_stokes = current_tab.stokes_combo.currentText()
            if selected_stokes in ["I", "Q", "U", "V"]:
                stokes = selected_stokes

        # Ask user about zero-level offset
        reply = QMessageBox.question(
            self,
            "Fitting Options",
            "Fit zero-level offset (background)?\n\n"
            "Yes: Fit a constant background level\n"
            "No: Assume zero background",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        dooff = reply == QMessageBox.Yes

        try:
            import subprocess
            import sys
            import tempfile
            import os
            import json

            # Box format: 'x1,y1,x2,y2' (pixel coordinates)
            box = ""
            if current_tab.current_roi and isinstance(current_tab.current_roi, tuple):
                xlow, xhigh, ylow, yhigh = current_tab.current_roi

                # Shrink by 1 pixel safety margin
                safe_xlow = xlow + 1
                safe_ylow = ylow + 1
                safe_xhigh = xhigh - 1 - 1  # Exclusive -> Inclusive (-1) -> Shrink (-1)
                safe_yhigh = yhigh - 1 - 1  # Exclusive -> Inclusive (-1) -> Shrink (-1)

                # Clamp to image dimensions to prevent out-of-bounds errors
                if current_tab.current_image_data is not None:
                    ny, nx = current_tab.current_image_data.shape
                    safe_xlow = max(0, min(safe_xlow, nx - 1))
                    safe_xhigh = max(0, min(safe_xhigh, nx - 1))
                    safe_ylow = max(0, min(safe_ylow, ny - 1))
                    safe_yhigh = max(0, min(safe_yhigh, ny - 1))

                # Ensure valid bounds (don't cross over or be invalid)
                if safe_xhigh <= safe_xlow:
                    QMessageBox.warning(
                        self, "Invalid ROI", "Invalid ROI: xhigh <= xlow"
                    )
                    return
                if safe_yhigh <= safe_ylow:
                    QMessageBox.warning(
                        self, "Invalid ROI", "Invalid ROI: yhigh <= ylow"
                    )
                    return

                box = f"{safe_ylow},{safe_xlow},{safe_yhigh},{safe_xhigh}"  # box uses col,row order (x,y)

            # Create temp file for results
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as f:
                result_file = f.name

            # Show wait cursor during fitting
            QApplication.setOverrideCursor(Qt.WaitCursor)
            self.statusBar().showMessage("Fitting Gaussian ... Please wait")
            QApplication.processEvents()

            # Run imfit in a subprocess to avoid casatasks/PyQt5 conflicts
            dooff_str = "True" if dooff else "False"
            script = f'''
import json
import numpy as np
from casatasks import imfit

imagename = "{imagename}"
box_param = "{box}"
result_file = "{result_file}"
dooff = {dooff_str}
stokes_param = "{stokes}"

def safe_get(d, keys, default=None):
    """Safely extract nested dictionary values."""
    try:
        result = d
        for key in keys:
            if isinstance(result, (list, np.ndarray)):
                result = result[int(key)]
            elif isinstance(result, dict):
                result = result[key]
            else:
                return default
        # Convert numpy types to Python types
        if isinstance(result, np.ndarray):
            result = result.tolist()
            if len(result) == 1:
                result = result[0]
        if hasattr(result, 'item'):
            result = result.item()
        return result
    except:
        return default

try:
    # Use box parameter (allows stokes) instead of region (doesn't allow stokes)
    fit_results = imfit(
        imagename=imagename,
        box=box_param,
        dooff=dooff,
        stokes=stokes_param,
    )
    
    
    # Extract the data we need (convert numpy types to Python types)
    output = {{"success": True, "results": {{}}}}
    
    if fit_results and "results" in fit_results:
        results = fit_results["results"]
        if "component0" in results:
            comp = results["component0"]
            
            # Position
            direction = comp.get("shape", {{}}).get("direction", {{}})
            output["results"]["ra_rad"] = float(safe_get(direction, ["m0", "value"], 0))
            output["results"]["dec_rad"] = float(safe_get(direction, ["m1", "value"], 0))
            # Position errors - CASA uses direction.error.longitude (RA) and direction.error.latitude (Dec)
            # CASA stores these in arcsec (check unit field to confirm)
            ra_err = safe_get(direction, ["error", "longitude", "value"])
            ra_err_unit = safe_get(direction, ["error", "longitude", "unit"], "arcsec")
            if ra_err is not None:
                # Store in arcsec - convert if needed based on unit
                if ra_err_unit == "rad":
                    output["results"]["ra_err_arcsec"] = float(ra_err) * 180 / 3.14159265359 * 3600
                else:  # assume arcsec
                    output["results"]["ra_err_arcsec"] = float(ra_err)
            
            dec_err = safe_get(direction, ["error", "latitude", "value"])
            dec_err_unit = safe_get(direction, ["error", "latitude", "unit"], "arcsec")
            if dec_err is not None:
                if dec_err_unit == "rad":
                    output["results"]["dec_err_arcsec"] = float(dec_err) * 180 / 3.14159265359 * 3600
                else:  # assume arcsec
                    output["results"]["dec_err_arcsec"] = float(dec_err)
            
            # Shape
            shape = comp.get("shape", {{}})
            output["results"]["major_arcsec"] = float(safe_get(shape, ["majoraxis", "value"], 0))
            output["results"]["minor_arcsec"] = float(safe_get(shape, ["minoraxis", "value"], 0))
            output["results"]["pa_deg"] = float(safe_get(shape, ["positionangle", "value"], 0))
            
            # Shape errors - CASA uses "majoraxiserror", "minoraxiserror", "positionangleerror"
            maj_err = safe_get(shape, ["majoraxiserror", "value"])
            if maj_err is not None:
                output["results"]["major_err"] = float(maj_err)
            
            min_err = safe_get(shape, ["minoraxiserror", "value"])
            if min_err is not None:
                output["results"]["minor_err"] = float(min_err)
                
            pa_err = safe_get(shape, ["positionangleerror", "value"])
            if pa_err is not None:
                output["results"]["pa_err"] = float(pa_err)
            
            # Flux - handle array values
            flux_val = safe_get(comp, ["flux", "value"])
            if flux_val is not None:
                if isinstance(flux_val, (list, tuple, np.ndarray)):
                    flux_val = float(flux_val[0]) if len(flux_val) > 0 else 0
                output["results"]["flux"] = float(flux_val)
            output["results"]["flux_unit"] = str(safe_get(comp, ["flux", "unit"], "Jy"))
            
            # Flux error - CASA stores as flux.error array
            flux_err = safe_get(comp, ["flux", "error"])
            if flux_err is not None:
                if isinstance(flux_err, (list, tuple, np.ndarray)):
                    flux_err = float(flux_err[0]) if len(flux_err) > 0 else None
                if flux_err is not None:
                    output["results"]["flux_err"] = float(flux_err)
            
            # Peak flux - CASA stores as peak.value (float) and peak.error (float)
            peak_val = safe_get(comp, ["peak", "value"])
            if peak_val is not None:
                output["results"]["peak_flux"] = float(peak_val)
                output["results"]["peak_unit"] = str(safe_get(comp, ["peak", "unit"], "Jy/beam"))
                # Peak error is a float
                peak_err = safe_get(comp, ["peak", "error"])
                if peak_err is not None:
                    output["results"]["peak_err"] = float(peak_err)
            
    # Deconvolved - uses same key structure as 'results'
    if fit_results and "deconvolved" in fit_results:
        deconv_comp = safe_get(fit_results, ["deconvolved", "component0", "shape"])
        if deconv_comp:
            output["results"]["deconv_major"] = float(safe_get(deconv_comp, ["majoraxis", "value"], 0))
            output["results"]["deconv_minor"] = float(safe_get(deconv_comp, ["minoraxis", "value"], 0))
            output["results"]["deconv_pa"] = float(safe_get(deconv_comp, ["positionangle", "value"], 0))
            # Deconvolved errors
            dmaj_err = safe_get(deconv_comp, ["majoraxiserror", "value"])
            if dmaj_err is not None:
                output["results"]["deconv_major_err"] = float(dmaj_err)
            
            dmin_err = safe_get(deconv_comp, ["minoraxiserror", "value"])
            if dmin_err is not None:
                output["results"]["deconv_minor_err"] = float(dmin_err)
            
            dpa_err = safe_get(deconv_comp, ["positionangleerror", "value"])
            if dpa_err is not None:
                output["results"]["deconv_pa_err"] = float(dpa_err)
    
    # Zero offset - CASA stores value in zerooff.value (ndarray) and error in zeroofferr.value (ndarray)
    zerooff = fit_results.get("zerooff", {{}}) if fit_results else {{}}
    if zerooff:
        offset_val = safe_get(zerooff, ["value"])
        if offset_val is not None:
            if isinstance(offset_val, (list, tuple, np.ndarray)):
                offset_val = float(offset_val[0]) if len(offset_val) > 0 else 0
            output["results"]["offset"] = float(offset_val)
        output["results"]["offset_unit"] = str(zerooff.get("unit", ""))
    
    # Zero offset error - stored in separate zeroofferr dict
    zeroofferr = fit_results.get("zeroofferr", {{}}) if fit_results else {{}}
    if zeroofferr:
        offset_err = safe_get(zeroofferr, ["value"])
        if offset_err is not None:
            if isinstance(offset_err, (list, tuple, np.ndarray)):
                offset_err = float(offset_err[0]) if len(offset_err) > 0 else None
            if offset_err is not None:
                output["results"]["offset_err"] = float(offset_err)
    
    with open(result_file, "w") as f:
        json.dump(output, f)
        
except Exception as e:
    import traceback
    with open(result_file, "w") as f:
        json.dump({{"success": False, "error": str(e), "traceback": traceback.format_exc()}}, f)
'''

            # Run the script in a subprocess
            env = os.environ.copy()
            # Clear Qt plugin paths to avoid conflicts
            for key in ["QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"]:
                if key in env:
                    del env[key]

            imagename_abs = os.path.abspath(imagename)
            result_file_abs = os.path.abspath(result_file)

            script = script.replace(f'imagename = "{imagename}"', f'imagename = "{imagename_abs}"')
            script = script.replace(f'result_file = "{result_file}"', f'result_file = "{result_file_abs}"')

            process = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                env=env,
                timeout=120,  # 2 minute timeout
                cwd=os.getcwd() if os.access(os.getcwd(), os.W_OK) else tempfile.gettempdir(),
            )

            # Check if subprocess had errors
            if process.returncode != 0:
                QApplication.restoreOverrideCursor()
                error_msg = process.stderr or process.stdout or "Unknown error"
                QMessageBox.warning(
                    self, "Fit Error", f"imfit subprocess failed:\n{error_msg}"
                )
                if os.path.exists(result_file):
                    os.remove(result_file)
                return

            # Read results from temp file
            if not os.path.exists(result_file):
                QApplication.restoreOverrideCursor()
                QMessageBox.warning(self, "Fit Error", "No results from fitting")
                return

            with open(result_file, "r") as f:
                output = json.load(f)

            os.remove(result_file)

            if not output.get("success", False):
                QApplication.restoreOverrideCursor()
                error = output.get("error", "Unknown error")
                tb = output.get("traceback", "")
                print(f"[ERROR] Fitting failed: {error}\n{tb}")
                QMessageBox.warning(self, "Fit Error", f"Fitting failed: {error}")
                return

            results = output.get("results", {})
            if not results:
                QApplication.restoreOverrideCursor()
                QMessageBox.warning(
                    self, "Fit Failed", "No components fitted in Gaussian fit"
                )
                return

            # Helper function for smart unit formatting
            def format_smart_arcsec(value_arcsec):
                """Convert arcsec to appropriate unit (arcsec, arcmin, or deg)"""
                if abs(value_arcsec) >= 3600:
                    return f"{value_arcsec / 3600:.3f}°"
                elif abs(value_arcsec) >= 60:
                    return f"{value_arcsec / 60:.3f}'"
                else:
                    return f'{value_arcsec:.3f}"'

            # Extract results
            ra_rad = results.get("ra_rad", 0)
            dec_rad = results.get("dec_rad", 0)
            ra_deg = np.degrees(ra_rad)
            dec_deg = np.degrees(dec_rad)
            # Errors already in arcsec from subprocess
            ra_err_arcsec = results.get("ra_err_arcsec")
            dec_err_arcsec = results.get("dec_err_arcsec")

            major_arcsec = results.get("major_arcsec", 0)
            minor_arcsec = results.get("minor_arcsec", 0)
            pa_deg = results.get("pa_deg", 0)

            flux = results.get("flux", 0)
            flux_unit = results.get("flux_unit", "Jy")
            flux_err = results.get("flux_err")
            peak_flux = results.get("peak_flux")
            peak_unit = results.get("peak_unit", flux_unit)
            peak_err = results.get("peak_err")

            # Errors for shape
            major_err = results.get("major_err")
            minor_err = results.get("minor_err")
            pa_err = results.get("pa_err")

            deconv_major = results.get("deconv_major", 0)
            deconv_minor = results.get("deconv_minor", 0)
            deconv_pa = results.get("deconv_pa")
            deconv_major_err = results.get("deconv_major_err")
            deconv_minor_err = results.get("deconv_minor_err")
            deconv_pa_err = results.get("deconv_pa_err")

            offset = results.get("offset", 0)
            offset_unit = results.get("offset_unit", "")
            offset_err = results.get("offset_err")

            # Convert to HMS/DMS
            from astropy.coordinates import SkyCoord
            import astropy.units as u

            coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg)
            ra_hms = coord.ra.to_string(unit=u.hourangle, sep=":", precision=2)
            dec_dms = coord.dec.to_string(unit=u.deg, sep=":", precision=2)

            # Get pixel coordinates if available
            ra_pix = None
            dec_pix = None
            if current_tab.current_wcs:
                try:
                    # Try with just RA/Dec (2 axes) first, then 4 axes
                    try:
                        pix = current_tab.current_wcs.topixel([ra_rad, dec_rad])[
                            "numeric"
                        ]
                    except:
                        # Use 4 axes but suppress warning by using valid stokes index 1
                        pix = current_tab.current_wcs.topixel([ra_rad, dec_rad, 1, 1])[
                            "numeric"
                        ]
                    ra_pix = pix[0]
                    dec_pix = pix[1]
                except:
                    pass

            # Beam parameters
            beam_major = 0
            beam_minor = 0
            beam_pa = 0
            if current_tab.psf:
                try:
                    if isinstance(current_tab.psf["major"]["value"], list):
                        beam_major = float(current_tab.psf["major"]["value"][0])
                    else:
                        beam_major = float(current_tab.psf["major"]["value"])

                    if isinstance(current_tab.psf["minor"]["value"], list):
                        beam_minor = float(current_tab.psf["minor"]["value"][0])
                    else:
                        beam_minor = float(current_tab.psf["minor"]["value"])

                    if isinstance(current_tab.psf["positionangle"]["value"], list):
                        beam_pa = float(current_tab.psf["positionangle"]["value"][0])
                    else:
                        beam_pa = float(current_tab.psf["positionangle"]["value"])
                except:
                    pass

            # Helper to format value with error
            def fmt_with_err(val_str, err, unit=""):
                if err is not None:
                    return f"{val_str} ± {err:.3f}{unit}"
                return val_str

            # Professional terminal output
            print("\n" + "=" * 70)
            print("           2D GAUSSIAN FIT RESULTS")
            print("=" * 70)
            print("-" * 70)
            print(f"  {'Parameter':<25} {'Value':>25} {'Error':>15}")
            print("-" * 70)

            # Position - show errors on deg rows with smart units
            print(f"  {'RA (HMS)':<25} {ra_hms:>25}")
            print(f"  {'Dec (DMS)':<25} {dec_dms:>25}")
            ra_err_str = (
                f"± {format_smart_arcsec(ra_err_arcsec)}" if ra_err_arcsec else ""
            )
            dec_err_str = (
                f"± {format_smart_arcsec(dec_err_arcsec)}" if dec_err_arcsec else ""
            )
            print(f"  {'RA (deg)':<25} {ra_deg:>25.6f} {ra_err_str:>15}")
            print(f"  {'Dec (deg)':<25} {dec_deg:>25.6f} {dec_err_str:>15}")
            if ra_pix is not None:
                print(f"  {'RA (pix)':<25} {ra_pix:>25.2f}")
                print(f"  {'Dec (pix)':<25} {dec_pix:>25.2f}")

            print("-" * 70)
            print("  IMAGE COMPONENT SIZE (convolved with beam):")
            major_str = format_smart_arcsec(major_arcsec)
            minor_str = format_smart_arcsec(minor_arcsec)
            major_err_str = f'± {major_err:.3f}"' if major_err else ""
            minor_err_str = f'± {minor_err:.3f}"' if minor_err else ""
            pa_err_str = f"± {pa_err:.2f}" if pa_err else ""
            print(f"  {'Major axis FWHM':<25} {major_str:>25} {major_err_str:>15}")
            print(f"  {'Minor axis FWHM':<25} {minor_str:>25} {minor_err_str:>15}")
            print(f"  {'Position Angle (deg)':<25} {pa_deg:>25.2f} {pa_err_str:>15}")

            if beam_major > 0:
                print("-" * 70)
                print("  CLEAN BEAM SIZE:")
                print(
                    f"  {'Major axis FWHM':<25} {format_smart_arcsec(beam_major):>25}"
                )
                print(
                    f"  {'Minor axis FWHM':<25} {format_smart_arcsec(beam_minor):>25}"
                )
                print(f"  {'Position Angle (deg)':<25} {beam_pa:>25.2f}")

            deconv_info = ""
            deconv_theta_info = ""
            if deconv_major > 0 or deconv_minor > 0:
                print("-" * 70)
                print("  IMAGE COMPONENT SIZE (deconvolved from beam):")
                if deconv_major > 0:
                    deconv_maj_str = format_smart_arcsec(deconv_major)
                    deconv_maj_err_str = (
                        f'± {deconv_major_err:.3f}"' if deconv_major_err else ""
                    )
                    print(
                        f"  {'Major axis FWHM':<25} {deconv_maj_str:>25} {deconv_maj_err_str:>15}"
                    )
                else:
                    print(f"  {'Major axis FWHM':<25} {'Unresolved':>25}")
                if deconv_minor > 0:
                    deconv_min_str = format_smart_arcsec(deconv_minor)
                    deconv_min_err_str = (
                        f'± {deconv_minor_err:.3f}"' if deconv_minor_err else ""
                    )
                    print(
                        f"  {'Minor axis FWHM':<25} {deconv_min_str:>25} {deconv_min_err_str:>15}"
                    )
                else:
                    print(f"  {'Minor axis FWHM':<25} {'Unresolved':>25}")
                if deconv_pa is not None:
                    deconv_pa_err_str = (
                        f"± {deconv_pa_err:.2f}" if deconv_pa_err else ""
                    )
                    print(
                        f"  {'Position Angle (deg)':<25} {deconv_pa:>25.2f} {deconv_pa_err_str:>15}"
                    )
                else:
                    print(f"  {'Position Angle (deg)':<25} {'N/A':>25}")

                # Build deconv info for GUI
                deconv_maj_str = (
                    format_smart_arcsec(deconv_major) if deconv_major > 0 else "Unres"
                )
                deconv_min_str = (
                    format_smart_arcsec(deconv_minor) if deconv_minor > 0 else "Unres"
                )
                deconv_info = f"\nDeconv: {deconv_maj_str} × {deconv_min_str}"
                if deconv_pa is not None:
                    deconv_theta_info = f"\nDeconv PA: {deconv_pa:.1f}°"

            print("-" * 70)
            print("  FLUX:")
            flux_str = f"{flux:.4g} {flux_unit}"
            flux_err_str = f"± {flux_err:.4g}" if flux_err else ""
            print(f"  {'Integrated':<25} {flux_str:>25} {flux_err_str:>15}")

            if peak_flux is not None:
                peak_str = f"{peak_flux:.4g} {peak_unit}"
                peak_err_str = f"± {peak_err:.4g}" if peak_err else ""
                print(f"  {'Peak':<25} {peak_str:>25} {peak_err_str:>15}")

            if offset != 0 or offset_err:
                offset_str = f"{offset:.4g} {offset_unit}"
                offset_err_str = f"± {offset_err:.4g}" if offset_err else ""
                print(
                    f"  {'Zero-level offset':<25} {offset_str:>25} {offset_err_str:>15}"
                )

            print("=" * 70 + "\n")

            QApplication.restoreOverrideCursor()

            # GUI message (compact version)
            msg = (
                f"2D Gaussian Fit:\n"
                f"Position: {ra_hms}, {dec_dms}\n"
                f"Size: {format_smart_arcsec(major_arcsec)} × {format_smart_arcsec(minor_arcsec)}\n"
                f"PA: {pa_deg:.1f}°\n"
                f"Flux: {flux:.4g} {flux_unit}"
                f"{deconv_info}"
                f"{deconv_theta_info}"
            )

            QMessageBox.information(self, "Fit Result", msg)
            self.statusBar().showMessage("Gaussian fit completed", 3000)

        except subprocess.TimeoutExpired:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(
                self, "Timeout", "Gaussian fit took too long to complete"
            )
        except Exception as e:
            QApplication.restoreOverrideCursor()
            import traceback

            traceback.print_exc()
            QMessageBox.warning(self, "Fit Error", f"Gaussian fit failed: {str(e)}")

    def fit_2d_ring(self):
        from scipy.optimize import curve_fit

        current_tab = self.tab_widget.currentWidget()
        if not current_tab or current_tab.current_image_data is None:
            QMessageBox.warning(self, "No Data", "Load data first.")
            return

        data = current_tab.current_image_data
        roi_offset = (0, 0)  # Offset for ROI coordinates

        # Check for visible ROI
        has_visible_roi = False
        if (
            current_tab.current_roi
            and isinstance(current_tab.current_roi, tuple)
            and current_tab.roi_selector
        ):
            if hasattr(current_tab.roi_selector, "to_draw"):
                has_visible_roi = current_tab.roi_selector.to_draw.get_visible()
            elif hasattr(current_tab.roi_selector, "artists"):
                has_visible_roi = any(
                    a.get_visible() for a in current_tab.roi_selector.artists
                )

        if not has_visible_roi:
            QMessageBox.warning(
                self,
                "No ROI",
                "Please select a region of interest (ROI) first.\n\nUse the ROI tool to draw a box around the source.",
            )
            return

        xlow, xhigh, ylow, yhigh = current_tab.current_roi
        data = data[xlow:xhigh, ylow:yhigh]
        roi_offset = (ylow, xlow)  # Store offset for coordinate conversion
        if data.size == 0:
            QMessageBox.warning(self, "Invalid ROI", "ROI contains no data")
            return

        ny, nx = data.shape
        x = np.arange(nx)
        y = np.arange(ny)
        xmesh, ymesh = np.meshgrid(x, y)
        coords = np.vstack((xmesh.ravel(), ymesh.ravel()))
        data_flat = data.ravel()

        guess = [np.nanmax(data), nx / 2, ny / 2, nx / 6, nx / 3, np.nanmedian(data)]

        try:
            popt, pcov = curve_fit(twoD_elliptical_ring, coords, data_flat, p0=guess)
            perr = np.sqrt(np.diag(pcov))

            # Get absolute pixel coordinates (accounting for ROI offset)
            x0_px = popt[1] + roi_offset[0]
            y0_px = popt[2] + roi_offset[1]
            inner_r_px = abs(popt[3])
            outer_r_px = abs(popt[4])

            # Try WCS conversion
            use_wcs = False
            wcs_info = ""
            if current_tab.current_wcs:
                try:
                    # Get pixel scale from WCS (radians -> arcsec)
                    increment = current_tab.current_wcs.increment()["numeric"][0:2]
                    scale_x = abs(increment[0]) * 180 / np.pi * 3600  # arcsec/pixel
                    scale_y = abs(increment[1]) * 180 / np.pi * 3600  # arcsec/pixel
                    avg_scale = (scale_x + scale_y) / 2

                    # Convert to world coordinates
                    world = current_tab.current_wcs.toworld([x0_px, y0_px, 0, 0])[
                        "numeric"
                    ]
                    ra_deg = world[0] * 180 / np.pi if world[0] else None
                    dec_deg = world[1] * 180 / np.pi if world[1] else None

                    # Convert radii to arcsec
                    inner_r_arcsec = inner_r_px * avg_scale
                    outer_r_arcsec = outer_r_px * avg_scale
                    width_arcsec = outer_r_arcsec - inner_r_arcsec

                    use_wcs = True
                    wcs_info = f'WCS (scale: {avg_scale:.4f}"/px)'
                except Exception as wcs_err:
                    print(
                        f"[INFO] WCS conversion failed, using pixel coordinates: {wcs_err}"
                    )

            # Professional terminal output
            print("\n" + "=" * 60)
            print("           2D ELLIPTICAL RING FIT RESULTS")
            print("=" * 60)
            if use_wcs:
                print(f"  Coordinate System: {wcs_info}")
                print("-" * 60)
                print(f"  {'Parameter':<20} {'Value':>15} {'Error':>15}")
                print("-" * 60)
                print(f"  {'Amplitude':<20} {popt[0]:>15.4g} {perr[0]:>15.4g}")
                if ra_deg is not None:
                    print(f"  {'RA (deg)':<20} {ra_deg:>15.6f}")
                if dec_deg is not None:
                    print(f"  {'Dec (deg)':<20} {dec_deg:>15.6f}")
                print(f"  {'X0 (pixel)':<20} {x0_px:>15.2f} {perr[1]:>15.2f}")
                print(f"  {'Y0 (pixel)':<20} {y0_px:>15.2f} {perr[2]:>15.2f}")
                print(f"  {'Inner R (arcsec)':<20} {inner_r_arcsec:>15.3f}")
                print(f"  {'Outer R (arcsec)':<20} {outer_r_arcsec:>15.3f}")
                print(f"  {'Ring Width (arcsec)':<20} {width_arcsec:>15.3f}")
                print(f"  {'Inner R (pixel)':<20} {inner_r_px:>15.2f} {perr[3]:>15.2f}")
                print(f"  {'Outer R (pixel)':<20} {outer_r_px:>15.2f} {perr[4]:>15.2f}")
                print(f"  {'Offset':<20} {popt[5]:>15.4g} {perr[5]:>15.4g}")
            else:
                print("  Coordinate System: Pixel")
                print("-" * 60)
                print(f"  {'Parameter':<20} {'Value':>15} {'Error':>15}")
                print("-" * 60)
                print(f"  {'Amplitude':<20} {popt[0]:>15.4g} {perr[0]:>15.4g}")
                print(f"  {'X0 (pixel)':<20} {x0_px:>15.2f} {perr[1]:>15.2f}")
                print(f"  {'Y0 (pixel)':<20} {y0_px:>15.2f} {perr[2]:>15.2f}")
                print(f"  {'Inner R (pixel)':<20} {inner_r_px:>15.2f} {perr[3]:>15.2f}")
                print(f"  {'Outer R (pixel)':<20} {outer_r_px:>15.2f} {perr[4]:>15.2f}")
                print(f"  {'Ring Width (pixel)':<20} {outer_r_px - inner_r_px:>15.2f}")
                print(f"  {'Offset':<20} {popt[5]:>15.4g} {perr[5]:>15.4g}")
            print("=" * 60 + "\n")

            # GUI message (compact version)
            if use_wcs:
                msg = (
                    f"2D Ring Fit (WCS):\n"
                    f"Amp={popt[0]:.4g}±{perr[0]:.4g}\n"
                    f"Center: ({x0_px:.1f}, {y0_px:.1f}) px\n"
                    f'Inner R={inner_r_arcsec:.2f}", Outer R={outer_r_arcsec:.2f}"\n'
                    f'Ring Width={width_arcsec:.2f}"'
                )
            else:
                msg = (
                    f"2D Ring Fit (Pixel):\n"
                    f"Amp={popt[0]:.4g}±{perr[0]:.4g}\n"
                    f"X0={x0_px:.2f}±{perr[1]:.2f}, Y0={y0_px:.2f}±{perr[2]:.2f}\n"
                    f"Inner R={inner_r_px:.2f}±{perr[3]:.2f}, Outer R={outer_r_px:.2f}±{perr[4]:.2f}"
                )

            QMessageBox.information(self, "Fit Result", msg)

        except Exception as e:
            QMessageBox.warning(self, "Fit Error", f"Ring fit failed: {str(e)}")

    def add_text_annotation(self):
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or current_tab.current_image_data is None:
            QMessageBox.warning(self, "No Data", "Load data first.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Add Text Annotation")
        set_hand_cursor(dialog)
        dialog.setMinimumWidth(350)
        layout = QVBoxLayout(dialog)

        # Position group
        pos_group = QGroupBox("Position")
        pos_layout = QGridLayout(pos_group)
        pos_layout.addWidget(QLabel("X:"), 0, 0)
        x_pos = QLineEdit("100")
        pos_layout.addWidget(x_pos, 0, 1)
        pos_layout.addWidget(QLabel("Y:"), 0, 2)
        y_pos = QLineEdit("100")
        pos_layout.addWidget(y_pos, 0, 3)
        layout.addWidget(pos_group)

        # Text group
        text_group = QGroupBox("Text")
        text_layout = QVBoxLayout(text_group)
        text_input = QLineEdit("Annotation")
        text_layout.addWidget(text_input)
        layout.addWidget(text_group)

        # Style group
        style_group = QGroupBox("Style")
        style_layout = QGridLayout(style_group)

        # Color
        style_layout.addWidget(QLabel("Color:"), 0, 0)
        color_combo = QComboBox()
        color_combo.addItems(
            [
                "yellow",
                "white",
                "red",
                "cyan",
                "lime",
                "magenta",
                "orange",
                "blue",
                "black",
            ]
        )
        style_layout.addWidget(color_combo, 0, 1)

        # Font size
        style_layout.addWidget(QLabel("Font Size:"), 0, 2)
        fontsize_spin = QSpinBox()
        fontsize_spin.setRange(6, 48)
        fontsize_spin.setValue(12)
        style_layout.addWidget(fontsize_spin, 0, 3)

        # Font weight
        style_layout.addWidget(QLabel("Weight:"), 1, 0)
        weight_combo = QComboBox()
        weight_combo.addItems(["normal", "bold"])
        style_layout.addWidget(weight_combo, 1, 1)

        # Font style
        style_layout.addWidget(QLabel("Style:"), 1, 2)
        fontstyle_combo = QComboBox()
        fontstyle_combo.addItems(["normal", "italic"])
        style_layout.addWidget(fontstyle_combo, 1, 3)

        # Background
        style_layout.addWidget(QLabel("Background:"), 2, 0)
        bg_combo = QComboBox()
        bg_combo.addItems(["None", "black", "white", "gray", "yellow", "red", "blue"])
        style_layout.addWidget(bg_combo, 2, 1)

        layout.addWidget(style_group)

        # Store tab reference for callback
        tab_ref = current_tab

        def on_accept():
            try:
                # Check if tab still exists
                if tab_ref not in [
                    self.tab_widget.widget(i) for i in range(self.tab_widget.count())
                ]:
                    QMessageBox.warning(
                        self, "Error", "The target tab has been closed."
                    )
                    dialog.close()
                    return
                x = int(x_pos.text())
                y = int(y_pos.text())
                text = text_input.text()
                color = color_combo.currentText()
                fontsize = fontsize_spin.value()
                fontweight = weight_combo.currentText()
                fontstyle = fontstyle_combo.currentText()
                background = (
                    bg_combo.currentText() if bg_combo.currentText() != "None" else None
                )
                tab_ref.add_text_annotation(
                    x,
                    y,
                    text,
                    color=color,
                    fontsize=fontsize,
                    fontweight=fontweight,
                    fontstyle=fontstyle,
                    background=background,
                )
                dialog.close()
            except ValueError:
                QMessageBox.warning(
                    self, "Invalid Input", "Please enter valid numeric coordinates"
                )
            except RuntimeError:
                QMessageBox.warning(
                    self, "Error", "The target tab is no longer available."
                )
                dialog.close()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(on_accept)
        buttons.rejected.connect(dialog.close)
        layout.addWidget(buttons)

        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(
            lambda: (
                self._open_dialogs.remove(dialog)
                if dialog in self._open_dialogs
                else None
            )
        )
        self._open_dialogs.append(dialog)
        dialog.show()

    def add_arrow_annotation(self):
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or current_tab.current_image_data is None:
            QMessageBox.warning(self, "No Data", "Load data first.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Add Arrow Annotation")
        set_hand_cursor(dialog)
        dialog.setMinimumWidth(350)
        layout = QVBoxLayout(dialog)

        # Position group
        pos_group = QGroupBox("Position")
        pos_layout = QGridLayout(pos_group)
        pos_layout.addWidget(QLabel("Start X:"), 0, 0)
        x1_pos = QLineEdit("100")
        pos_layout.addWidget(x1_pos, 0, 1)
        pos_layout.addWidget(QLabel("Start Y:"), 0, 2)
        y1_pos = QLineEdit("100")
        pos_layout.addWidget(y1_pos, 0, 3)
        pos_layout.addWidget(QLabel("End X:"), 1, 0)
        x2_pos = QLineEdit("150")
        pos_layout.addWidget(x2_pos, 1, 1)
        pos_layout.addWidget(QLabel("End Y:"), 1, 2)
        y2_pos = QLineEdit("150")
        pos_layout.addWidget(y2_pos, 1, 3)
        layout.addWidget(pos_group)

        # Style group
        style_group = QGroupBox("Style")
        style_layout = QGridLayout(style_group)

        # Color
        style_layout.addWidget(QLabel("Color:"), 0, 0)
        color_combo = QComboBox()
        color_combo.addItems(
            [
                "red",
                "yellow",
                "white",
                "cyan",
                "lime",
                "magenta",
                "orange",
                "blue",
                "black",
            ]
        )
        style_layout.addWidget(color_combo, 0, 1)

        # Line width
        style_layout.addWidget(QLabel("Line Width:"), 0, 2)
        linewidth_spin = QDoubleSpinBox()
        linewidth_spin.setRange(0.5, 10.0)
        linewidth_spin.setValue(2.0)
        linewidth_spin.setSingleStep(0.5)
        style_layout.addWidget(linewidth_spin, 0, 3)

        # Head size
        style_layout.addWidget(QLabel("Head Size:"), 1, 0)
        headsize_spin = QSpinBox()
        headsize_spin.setRange(4, 30)
        headsize_spin.setValue(10)
        style_layout.addWidget(headsize_spin, 1, 1)

        layout.addWidget(style_group)

        # Store tab reference for callback
        tab_ref = current_tab

        def on_accept():
            try:
                # Check if tab still exists
                if tab_ref not in [
                    self.tab_widget.widget(i) for i in range(self.tab_widget.count())
                ]:
                    QMessageBox.warning(
                        self, "Error", "The target tab has been closed."
                    )
                    dialog.close()
                    return
                x1 = int(x1_pos.text())
                y1 = int(y1_pos.text())
                x2 = int(x2_pos.text())
                y2 = int(y2_pos.text())
                color = color_combo.currentText()
                linewidth = linewidth_spin.value()
                head_width = headsize_spin.value()
                tab_ref.add_arrow_annotation(
                    x1,
                    y1,
                    x2,
                    y2,
                    color=color,
                    linewidth=linewidth,
                    head_width=head_width,
                )
                dialog.close()
            except ValueError:
                QMessageBox.warning(
                    self, "Invalid Input", "Please enter valid numeric coordinates"
                )
            except RuntimeError:
                QMessageBox.warning(
                    self, "Error", "The target tab is no longer available."
                )
                dialog.close()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(on_accept)
        buttons.rejected.connect(dialog.close)
        layout.addWidget(buttons)

        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(
            lambda: (
                self._open_dialogs.remove(dialog)
                if dialog in self._open_dialogs
                else None
            )
        )
        self._open_dialogs.append(dialog)
        dialog.show()

    def show_update_dialog(self):
        """Show the update check dialog (non-modal)."""
        # Check if already open
        for dialog in self._open_dialogs:
            if isinstance(dialog, UpdateDialog):
                dialog.raise_()
                dialog.activateWindow()
                return

        dialog = UpdateDialog(self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(
            lambda: (
                self._open_dialogs.remove(dialog)
                if dialog in self._open_dialogs
                else None
            )
        )
        self._open_dialogs.append(dialog)
        dialog.show()

    def show_about_dialog(self):
        """Show a modern, professional about dialog."""
        from .styles import theme_manager
        from PyQt5.QtGui import QPixmap

        is_dark = theme_manager.is_dark

        dialog = QDialog(self)
        dialog.setWindowTitle("About SolarViewer")
        # dialog.setFixedSize(510, 580)
        dialog.resize(510, 580)

        layout = QVBoxLayout(dialog)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        # Header section with gradient background
        header = QFrame()
        if is_dark:
            header.setStyleSheet(
                """
                QFrame {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                        stop:0 #4c1d95, stop:1 #7c3aed);
                    border-radius: 0px;
                }
            """
            )
        else:
            header.setStyleSheet(
                """
                QFrame {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                        stop:0 #7c3aed, stop:1 #a78bfa);
                    border-radius: 0px;
                }
            """
            )
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(30, 25, 30, 20)
        header_layout.setSpacing(10)

        icon_label = QLabel()
        try:
            icon_path = get_resource_path("assets/icon.png")
            pixmap = QPixmap(icon_path)
            icon_label.setPixmap(
                pixmap.scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        except Exception:
            icon_label.setText("☀️")
            icon_label.setStyleSheet("font-size: 48pt;")
        icon_label.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(icon_label)

        # App title
        title = QLabel("SolarViewer")
        title.setStyleSheet("font-size: 26pt; font-weight: bold; color: white;")
        title.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(title)

        # Version badge
        from . import __version__

        version_container = QHBoxLayout()
        version_container.addStretch()
        version_label = QLabel(f"v{__version__}")
        version_label.setStyleSheet(
            """
            QLabel {
                background-color: rgba(255, 255, 255, 0.2);
                color: white;
                padding: 5px 14px;
                border-radius: 12px;
                font-size: 11pt;
                font-weight: bold;
            }
        """
        )
        version_container.addWidget(version_label)
        version_container.addStretch()
        header_layout.addLayout(version_container)

        layout.addWidget(header)

        # Content section
        content = QFrame()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(30, 25, 30, 20)
        content_layout.setSpacing(16)

        # Description
        desc = QLabel("A visualization tool for CASA and FITS solar radio images")
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignCenter)
        desc.setStyleSheet("font-size: 12pt;")
        content_layout.addWidget(desc)

        # Feature pills
        features_layout = QHBoxLayout()
        features_layout.setSpacing(10)
        features_layout.addStretch()

        feature_style = f"""
            QLabel {{
                background-color: {'rgba(124, 58, 237, 0.15)' if is_dark else 'rgba(124, 58, 237, 0.1)'};
                color: {'#a78bfa' if is_dark else '#7c3aed'};
                padding: 6px 14px;
                border-radius: 14px;
                font-size: 10pt;
            }}
        """

        for feature in ["Multi-tab", "Stokes", "Fitting", "Contours", "Remote"]:
            pill = QLabel(feature)
            pill.setStyleSheet(feature_style)
            features_layout.addWidget(pill)

        features_layout.addStretch()
        content_layout.addLayout(features_layout)

        content_layout.addSpacing(12)

        # Divider
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(
            f"background-color: {'#3a3a4a' if is_dark else '#e0e0e0'};"
        )
        content_layout.addWidget(divider)

        content_layout.addSpacing(8)

        # Author section
        author_label = QLabel("Developed by")
        author_label.setAlignment(Qt.AlignCenter)
        author_label.setStyleSheet(
            f"font-size: 11pt; color: {'#888' if is_dark else '#666'};"
        )
        content_layout.addWidget(author_label)

        author_name = QLabel("Soham Dey")
        author_name.setAlignment(Qt.AlignCenter)
        author_name.setStyleSheet("font-size: 15pt; font-weight: bold;")
        content_layout.addWidget(author_name)

        # Contact links
        links_layout = QHBoxLayout()
        links_layout.setSpacing(20)
        links_layout.addStretch()

        link_style = f"""
            QLabel {{
                color: {'#a78bfa' if is_dark else '#7c3aed'};
                font-size: 11pt;
            }}
            QLabel:hover {{
                text-decoration: underline;
            }}
        """

        email_link = QLabel("📧 sohamd943@gmail.com")
        email_link.setStyleSheet(link_style)
        email_link.setCursor(Qt.PointingHandCursor)
        links_layout.addWidget(email_link)

        links_layout.addStretch()
        content_layout.addLayout(links_layout)

        content_layout.addSpacing(12)

        # Keyboard shortcut hint
        hint = QLabel("Press F1 for keyboard shortcuts")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet(
            f"font-size: 10pt; font-style: italic; color: {'#666' if is_dark else '#888'};"
        )
        content_layout.addWidget(hint)

        content_layout.addStretch()

        # Close button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(100)
        close_btn.setStyleSheet(
            """
            QPushButton {
                padding: 8px 20px;
                font-size: 11pt;
            }
        """
        )
        close_btn.clicked.connect(dialog.accept)
        close_btn.setDefault(True)
        btn_layout.addWidget(close_btn)
        btn_layout.addStretch()
        content_layout.addLayout(btn_layout)

        layout.addWidget(content, 1)

        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(
            lambda: (
                self._open_dialogs.remove(dialog)
                if dialog in self._open_dialogs
                else None
            )
        )
        self._open_dialogs.append(dialog)
        dialog.show()

    def show_tutorial(self):
        """Show the comprehensive tutorial dialog."""
        from .tutorial import TutorialDialog

        dialog = TutorialDialog(self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(
            lambda: (
                self._open_dialogs.remove(dialog)
                if dialog in self._open_dialogs
                else None
            )
        )
        self._open_dialogs.append(dialog)
        dialog.show()

    def show_keyboard_shortcuts(parent=None):
        """Show keyboard shortcuts dialog with modern, professional UI design."""
        from PyQt5.QtWidgets import QGridLayout, QScrollArea, QFrame, QLineEdit
        from PyQt5.QtGui import QFont
        from PyQt5.QtCore import QSize

        dialog = QDialog(parent)
        dialog.setWindowTitle("Keyboard Shortcuts")
        dialog.setMinimumSize(850, 600)

        main_layout = QVBoxLayout(dialog)
        main_layout.setSpacing(16)
        main_layout.setContentsMargins(24, 20, 24, 20)

        # Header with search
        header_layout = QHBoxLayout()

        title = QLabel("⌨️ Keyboard Shortcuts")
        title.setStyleSheet("font-size: 16pt; font-weight: bold;")
        header_layout.addWidget(title)

        header_layout.addStretch()

        # Search filter
        search_box = QLineEdit()
        search_box.setPlaceholderText("🔍 Filter shortcuts...")
        search_box.setFixedWidth(200)
        search_box.setStyleSheet(
            """
            QLineEdit {
                padding: 6px 12px;
                border-radius: 15px;
            }
        """
        )
        header_layout.addWidget(search_box)

        main_layout.addLayout(header_layout)

        # Scroll area for content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(20)
        scroll_layout.setContentsMargins(0, 0, 10, 0)

        # Shortcut categories with icons
        categories = [
            (
                "📁",
                "File Operations",
                [
                    ("Ctrl+O", "Open CASA Image"),
                    ("Ctrl+Shift+O", "Open FITS File"),
                    ("Ctrl+Shift+R", "Connect to Remote Server"),
                    ("Ctrl+E", "Export Figure"),
                    ("Ctrl+F", "Export as FITS"),
                    ("Ctrl+Shift+N", "Fast Viewer"),
                    ("Ctrl+Q", "Exit"),
                ],
            ),
            (
                "🧭",
                "Navigation & View",
                [
                    ("R", "Reset View"),
                    ("1", "1°×1° Zoom"),
                    ("+  or  =", "Zoom In"),
                    ("-", "Zoom Out"),
                    ("Space  or  Enter", "Update Display"),
                    ("Ctrl+D", "Toggle Dark/Light Theme"),
                    ("Ctrl+L", "Log Console"),
                    ("F11", "Toggle Fullscreen"),
                ],
            ),
            (
                "🎨",
                "Display Presets",
                [
                    ("F5", "Auto Min/Max"),
                    ("F6", "Auto Percentile (1-99%)"),
                    ("F7", "Median ± 3×RMS"),
                    ("F8", "AIA Presets"),
                    ("F9", "HMI Presets"),
                ],
            ),
            (
                "🔧",
                "Tools & Analysis",
                [
                    ("Ctrl+P", "Phase Center Shift"),
                    ("Ctrl+M", "Image Metadata"),
                    ("Ctrl+G", "Fit 2D Gaussian"),
                    # ("Ctrl+L", "Fit Ring Model"),
                ],
            ),
            (
                "✂️",
                "Region & Annotation",
                [
                    ("Ctrl+S", "Export Sub-Image"),
                    ("Ctrl+R", "Export Region"),
                    ("Ctrl+T", "Add Text Annotation"),
                    ("Ctrl+A", "Add Arrow"),
                ],
            ),
            (
                "📂",
                "File Navigation",
                [
                    ("[", "Previous File in Directory"),
                    ("]", "Next File in Directory"),
                    ("{", "First File"),
                    ("}", "Last File"),
                ],
            ),
            (
                "📑",
                "Tab Management",
                [
                    ("Ctrl+N", "New Tab"),
                    ("Ctrl+W", "Close Current Tab"),
                    ("Ctrl+Tab", "Switch to Next Tab"),
                    ("Ctrl+Shift+Tab", "Switch to Previous Tab"),
                ],
            ),
        ]

        # Get theme for styling
        from .styles import theme_manager

        is_dark = theme_manager.is_dark

        # Define theme-aware badge styles
        if is_dark:
            badge_style = """
                QLabel {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 #4a4a5a, stop:1 #3a3a4a);
                    color: #f0f0f5;
                    padding: 4px 10px;
                    border-radius: 5px;
                    font-family: 'SF Mono', 'Consolas', 'Monaco', monospace;
                    font-weight: bold;
                    font-size: 10pt;
                    border: 1px solid #5a5a6a;
                    border-bottom: 2px solid #2a2a3a;
                }
            """
            separator_color = "#888"
        else:
            badge_style = """
                QLabel {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 #ffffff, stop:1 #e8e8ec);
                    color: #1f2937;
                    padding: 4px 10px;
                    border-radius: 5px;
                    font-family: 'SF Mono', 'Consolas', 'Monaco', monospace;
                    font-weight: bold;
                    font-size: 10pt;
                    border: 1px solid #c0c0c8;
                    border-bottom: 2px solid #a0a0a8;
                }
            """
            separator_color = "#666"

        def create_key_badge(key_text):
            """Create a styled keyboard key badge."""
            container = QWidget()
            layout = QHBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(4)

            # Handle "or" alternatives first (like "+ or =")
            if "  or  " in key_text:
                alternatives = key_text.split("  or  ")
                for j, alt in enumerate(alternatives):
                    if j > 0:
                        or_label = QLabel("or")
                        or_label.setStyleSheet(
                            f"color: {separator_color}; font-size: 10pt; margin: 0 6px;"
                        )
                        layout.addWidget(or_label)
                    badge = QLabel(alt.strip())
                    badge.setStyleSheet(badge_style)
                    layout.addWidget(badge)
            # Handle compound keys like "Ctrl+O"
            elif "+" in key_text and len(key_text) > 1:
                parts = key_text.split("+")
                for i, part in enumerate(parts):
                    part = part.strip()
                    if not part:
                        continue
                    if i > 0:
                        plus = QLabel("+")
                        plus.setStyleSheet(
                            f"font-weight: bold; color: {separator_color}; font-size: 11pt;"
                        )
                        layout.addWidget(plus)
                    badge = QLabel(part)
                    badge.setStyleSheet(badge_style)
                    layout.addWidget(badge)
            else:
                # Single key
                badge = QLabel(key_text.strip())
                badge.setStyleSheet(badge_style)
                layout.addWidget(badge)

            layout.addStretch()
            return container

        # Store all shortcut widgets for filtering
        all_shortcut_widgets = []

        def create_category_section(icon, name, shortcuts):
            """Create a styled category section."""
            section = QFrame()
            if is_dark:
                section.setStyleSheet(
                    """
                    QFrame {
                        background-color: rgba(255, 255, 255, 0.03);
                        border-radius: 10px;
                        padding: 8px;
                    }
                """
                )
                header_style = """
                    font-size: 12pt;
                    font-weight: bold;
                    color: #a78bfa;
                    padding-bottom: 6px;
                    border-bottom: 1px solid rgba(167, 139, 250, 0.3);
                """
            else:
                section.setStyleSheet(
                    """
                    QFrame {
                        background-color: rgba(0, 0, 0, 0.03);
                        border-radius: 10px;
                        padding: 8px;
                    }
                """
                )
                header_style = """
                    font-size: 12pt;
                    font-weight: bold;
                    color: #7c3aed;
                    padding-bottom: 6px;
                    border-bottom: 1px solid rgba(124, 58, 237, 0.3);
                """

            layout = QVBoxLayout(section)
            layout.setContentsMargins(16, 12, 16, 12)
            layout.setSpacing(10)

            # Category header
            header = QLabel(f"{icon}  {name}")
            header.setStyleSheet(header_style)
            layout.addWidget(header)

            # Shortcuts grid
            grid = QGridLayout()
            grid.setSpacing(8)
            grid.setColumnMinimumWidth(0, 200)

            shortcut_rows = []
            for row, (key, action) in enumerate(shortcuts):
                key_widget = create_key_badge(key)
                action_label = QLabel(action)
                # Use palette text color for theme compatibility
                action_label.setStyleSheet("font-size: 10.5pt;")

                grid.addWidget(key_widget, row, 0)
                grid.addWidget(action_label, row, 1)

                # Store for filtering
                shortcut_rows.append(
                    (key_widget, action_label, key.lower(), action.lower())
                )

            layout.addLayout(grid)
            return section, shortcut_rows

        # Create all category sections
        for icon, name, shortcuts in categories:
            section, rows = create_category_section(icon, name, shortcuts)
            scroll_layout.addWidget(section)
            all_shortcut_widgets.extend([(section, rows)])

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        main_layout.addWidget(scroll, 1)

        # Filter function
        def filter_shortcuts(text):
            text = text.lower()
            for section, rows in all_shortcut_widgets:
                visible_count = 0
                for key_widget, action_label, key_text, action_text in rows:
                    visible = not text or text in key_text or text in action_text
                    key_widget.setVisible(visible)
                    action_label.setVisible(visible)
                    if visible:
                        visible_count += 1
                section.setVisible(visible_count > 0)

        search_box.textChanged.connect(filter_shortcuts)

        # Footer with close button
        footer = QHBoxLayout()
        footer.addStretch()

        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(100)
        close_btn.setStyleSheet(
            """
            QPushButton {
                padding: 8px 20px;
                font-size: 11pt;
            }
        """
        )
        close_btn.clicked.connect(dialog.accept)
        close_btn.setDefault(True)
        footer.addWidget(close_btn)

        main_layout.addLayout(footer)

        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

    def keyPressEvent(self, event):
        if (
            event.key() == Qt.Key_Space
            or event.key() == Qt.Key_Return
            or event.key() == Qt.Key_Enter
        ):
            current_tab = self.tab_widget.currentWidget()
            if current_tab:
                current_tab.schedule_plot()
                self.statusBar().showMessage("Plot updated")
        elif event.key() == Qt.Key_R:
            current_tab = self.tab_widget.currentWidget()
            if current_tab:
                current_tab.reset_view()
        elif event.key() == Qt.Key_1:
            current_tab = self.tab_widget.currentWidget()
            if current_tab:
                current_tab.zoom_60arcmin()
        elif event.key() == Qt.Key_Plus or event.key() == Qt.Key_Equal:
            current_tab = self.tab_widget.currentWidget()
            if current_tab:
                current_tab.zoom_in()
        elif event.key() == Qt.Key_Minus:
            current_tab = self.tab_widget.currentWidget()
            if current_tab:
                current_tab.zoom_out()
        elif event.key() == Qt.Key_F5:
            current_tab = self.tab_widget.currentWidget()
            if current_tab:
                current_tab.auto_minmax()
        elif event.key() == Qt.Key_F6:
            current_tab = self.tab_widget.currentWidget()
            if current_tab:
                current_tab.auto_percentile()
        elif event.key() == Qt.Key_F7:
            current_tab = self.tab_widget.currentWidget()
            if current_tab:
                current_tab.auto_median_rms()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        try:
            casa_logs = [
                f
                for f in os.listdir(".")
                if f.startswith("casa-") and f.endswith(".log")
            ]
            for log in casa_logs:
                try:
                    os.remove(log)
                except:
                    pass
        except:
            pass
        super().closeEvent(event)

        # Ensure the entire application (including Log Console) quits
        from PyQt5.QtWidgets import QApplication

        QApplication.instance().quit()


    def launch_data_downloader_gui(self):
        """Launch the Solar Data Downloader GUI."""
        try:
            from .solar_data_downloader import launch_gui as launch_downloader_gui

            # Try to extract datetime from current tab
            initial_datetime = self._get_current_tab_datetime()
            launch_downloader_gui(self, initial_datetime=initial_datetime)
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to launch Solar Data Downloader GUI: {str(e)}\n\n"
                "Please make sure all required dependencies are installed.",
            )

    def launch_radio_data_downloader_gui(self):
        """Launch the Radio Solar Data Downloader GUI."""
        try:
            from .radio_data_downloader import launch_gui as launch_radio_downloader_gui

            # Try to extract datetime from current tab
            initial_datetime = self._get_current_tab_datetime()
            launch_radio_downloader_gui(self, initial_datetime=initial_datetime)
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to launch Radio Data Downloader GUI: {str(e)}\n\n"
                "Please make sure all required dependencies are installed.",
            )

    def _get_current_tab_datetime(self):
        """Extract observation datetime from the current tab's image, if available."""
        from datetime import datetime

        try:
            # Get the current tab
            current_tab = self.tab_widget.currentWidget()
            if not current_tab:
                return None

            obs_datetime = None

            # Method 1: Try FITS header
            if hasattr(current_tab, "current_header") and current_tab.current_header:
                header = current_tab.current_header
                for key in ["DATE-OBS", "DATE_OBS", "DATE", "STARTOBS", "T_OBS"]:
                    if key in header:
                        date_str = str(header[key]).strip()
                        try:
                            if "T" in date_str:
                                # Clean the string: remove Z, timezone, and fractional seconds
                                clean_str = (
                                    date_str.replace("Z", "")
                                    .split("+")[0]
                                    .split(".")[0]
                                )
                                if "-" in clean_str[11:]:
                                    clean_str = clean_str[:19]
                                obs_datetime = datetime.fromisoformat(clean_str)
                                break
                            elif "-" in date_str and len(date_str) >= 10:
                                obs_datetime = datetime.strptime(
                                    date_str[:10], "%Y-%m-%d"
                                )
                                break
                        except (ValueError, TypeError):
                            continue

            # Method 2: Try CASA image metadata (if no FITS datetime found)
            if (
                not obs_datetime
                and hasattr(current_tab, "imagename")
                and current_tab.imagename
            ):
                imagename = current_tab.imagename
                # Check if it's a CASA image (directory or non-FITS file)
                if not (
                    imagename.lower().endswith(".fits")
                    or imagename.lower().endswith(".fts")
                ):
                    try:
                        from casatools import image as IA
                        from astropy.time import Time

                        ia_tool = IA()
                        ia_tool.open(imagename)
                        csys_record = ia_tool.coordsys().torecord()
                        ia_tool.close()

                        if "obsdate" in csys_record:
                            obsdate = csys_record["obsdate"]
                            m0 = obsdate.get("m0", {})
                            mjd_value = m0.get("value", None)
                            if mjd_value is not None:
                                t = Time(mjd_value, format="mjd")
                                obs_datetime = t.datetime
                    except Exception:
                        pass  # CASA not available or failed

            return obs_datetime
        except Exception:
            return None

    def launch_data_downloader_cli(self):
        """Launch the Solar Data Downloader CLI in a new terminal window."""
        try:
            import subprocess
            import sys
            import os

            # Get the path to the CLI script and its directory
            cli_script = os.path.join(
                os.path.dirname(__file__),
                "solar_data_downloader",
                "solar_data_downloader_cli.py",
            )
            cli_dir = os.path.dirname(cli_script)

            # Make sure the script is executable
            os.chmod(cli_script, 0o755)

            # Get the current Python interpreter path and virtual environment
            python_path = sys.executable
            venv_path = os.path.dirname(os.path.dirname(python_path))
            activate_script = os.path.join(venv_path, "bin", "activate")

            self.show_status_message("Launching Radio Data Downloader CLI...")

            # Check if package directory is writable for the shell script
            if os.access(cli_dir, os.W_OK):
                temp_script = os.path.join(cli_dir, "run_cli.sh")
            else:
                temp_script = os.path.join(os.path.expanduser("~"), f".run_solar_cli_{os.getpid()}.sh")
            with open(temp_script, "w") as f:
                f.write(
                    f"""#!/bin/bash
source "{activate_script}"
python3 "{cli_script}"
read -p "Press Enter to close..."
"""
                )
            os.chmod(temp_script, 0o755)

            # Determine the terminal command based on the platform
            if sys.platform.startswith("linux"):
                # First, let's check which terminals are available
                available_terminals = []
                for term in ["xfce4-terminal", "gnome-terminal", "konsole", "xterm"]:
                    try:
                        result = subprocess.run(
                            ["which", term], capture_output=True, text=True
                        )
                        if result.returncode == 0:
                            available_terminals.append(term)
                    except Exception:
                        continue

                self.show_status_message(
                    f"Found terminals: {', '.join(available_terminals)}"
                )

                if not available_terminals:
                    raise Exception(
                        "No terminal emulators found. Please install gnome-terminal, konsole, xfce4-terminal, or xterm."
                    )

                # Try to launch using the first available terminal
                terminal = available_terminals[0]
                try:
                    if terminal == "gnome-terminal":
                        cmd = [terminal, "--", "bash", temp_script]
                    elif terminal == "konsole":
                        cmd = [terminal, "--separate", "--", "bash", temp_script]
                    elif terminal == "xfce4-terminal":
                        cmd = [terminal, "--command", f"bash {temp_script}"]
                    else:  # xterm and others
                        cmd = [terminal, "-e", f"bash {temp_script}"]

                    self.show_status_message(f"Launching in {terminal}...")
                    launch_cwd = os.getcwd() if os.access(os.getcwd(), os.W_OK) else os.path.expanduser("~")
                    process = subprocess.Popen(cmd, cwd=launch_cwd)

                    # Wait a bit to see if the process starts successfully
                    try:
                        process.wait(timeout=1)
                        if process.returncode is not None and process.returncode != 0:
                            raise Exception(f"Terminal {terminal} failed to start")
                    except subprocess.TimeoutExpired:
                        # Process is still running after 1 second, which is good
                        self.statusBar().showMessage(f"Launched CLI in {terminal}")
                        print(f"[INFO] Successfully launched CLI in {terminal}")
                        return

                except Exception as term_error:
                    print(f"[ERROR] Error launching {terminal}: {str(term_error)}")
                    self.show_status_message(
                        f"Error launching {terminal}: {str(term_error)}"
                    )
                    raise Exception(f"Failed to launch {terminal}: {str(term_error)}")

            elif sys.platform == "darwin":  # macOS
                subprocess.Popen(["open", "-a", "Terminal", temp_script])
            else:
                raise Exception(f"Unsupported platform: {sys.platform}")

        except Exception as e:
            error_msg = (
                f"Failed to launch Solar Data Downloader CLI: {str(e)}\n\n"
                "Please try running the CLI directly from a terminal:\n"
                f"cd {cli_dir} && source {activate_script} && python3 {cli_script}"
            )
            print(f"[ERROR] {error_msg}")  # Print to console for debugging
            QMessageBox.critical(self, "Error", error_msg)

    def export_as_hpc_fits(self):
        """Export the current image as a helioprojective FITS file"""
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or not current_tab.imagename:
            QMessageBox.warning(self, "No Image", "No image loaded to export")
            return

        # Check if the image is already in helioprojective coordinates
        if current_tab._is_already_hpc():
            QMessageBox.information(
                self,
                "Already in Helioprojective",
                "This image is already in helioprojective (Solar-X/Y) coordinates.\n\n"
                "No conversion is needed. You can save it directly using File → Save As FITS.",
            )
            return

        try:
            # Get the output filename from user
            path, _ = QFileDialog.getSaveFileName(
                self,
                "Export as Helioprojective FITS",
                "",
                "FITS Files (*.fits);;All Files (*)",
            )

            if path:
                # Get current Stokes parameter and threshold
                stokes = (
                    current_tab.stokes_combo.currentText()
                    if current_tab.stokes_combo
                    else "I"
                )
                try:
                    threshold = float(current_tab.threshold_entry.text())
                except (ValueError, AttributeError):
                    threshold = 10.0

                # Show progress in status bar
                self.statusBar().showMessage(
                    "Converting to helioprojective coordinates..."
                )
                QApplication.processEvents()

                # Call convert_and_save_hpc
                from .helioprojective import convert_and_save_hpc

                success = convert_and_save_hpc(
                    current_tab.imagename,
                    path,
                    Stokes=stokes,
                    thres=threshold,
                    overwrite=True,
                )

                if success:
                    QMessageBox.information(
                        self,
                        "Success",
                        f"Image exported as helioprojective FITS to:\n{path}",
                    )
                    self.statusBar().showMessage(
                        f"Exported helioprojective FITS to {path}"
                    )
                else:
                    QMessageBox.critical(
                        self,
                        "Export Failed",
                        "Failed to export image as helioprojective FITS",
                    )
                    self.statusBar().showMessage("Export failed")

        except Exception as e:
            QMessageBox.critical(
                self,
                "Export Error",
                f"Error exporting as helioprojective FITS:\n{str(e)}",
            )
            self.statusBar().showMessage("Export error")

    def aia_presets_94(self):
        """AIA 94 Angstrom preset with specific colormap"""
        self.aia_presets(94)

    def aia_presets_131(self):
        """AIA 131 Angstrom preset with specific colormap"""
        self.aia_presets(131)

    def aia_presets_171(self):
        """AIA 171 Angstrom preset with specific colormap"""
        self.aia_presets(171)

    def aia_presets_193(self):
        """AIA 193 Angstrom preset with specific colormap"""
        self.aia_presets(193)

    def aia_presets_211(self):
        """AIA 211 Angstrom preset with specific colormap"""
        self.aia_presets(211)

    def aia_presets_304(self):
        """AIA 304 Angstrom preset with specific colormap"""
        self.aia_presets(304)

    def aia_presets_335(self):
        """AIA 335 Angstrom preset with specific colormap"""
        self.aia_presets(335)

    def aia_presets_1600(self):
        """AIA 1600 Angstrom preset with specific colormap"""
        self.aia_presets(1600)

    def aia_presets_1700(self):
        """AIA 1700 Angstrom preset with specific colormap"""
        self.aia_presets(1700)

    def aia_presets_4500(self):
        """AIA 4500 Angstrom preset with specific colormap"""
        self.aia_presets(4500)

    def show_phase_shift_dialog(self):
        """Show the dialog for shifting solar center to phase center"""
        current_tab = self.tab_widget.currentWidget()

        # Get current image name if available
        imagename = None
        if current_tab and hasattr(current_tab, "imagename") and current_tab.imagename:
            imagename = current_tab.imagename

        # Import and show the dialog
        from .dialogs import PhaseShiftDialog

        dialog = PhaseShiftDialog(self, imagename)

        # Store tab reference for callback
        tab_ref = current_tab

        def on_finished(result):
            try:
                # Check if tab still exists and refresh if accepted
                if result == QDialog.Accepted and tab_ref:
                    if tab_ref in [
                        self.tab_widget.widget(i)
                        for i in range(self.tab_widget.count())
                    ]:
                        if tab_ref.imagename:
                            tab_ref.on_visualization_changed()
            except RuntimeError:
                pass  # Tab was deleted, ignore

        dialog.finished.connect(on_finished)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(
            lambda: (
                self._open_dialogs.remove(dialog)
                if dialog in self._open_dialogs
                else None
            )
        )
        self._open_dialogs.append(dialog)
        dialog.show()

    def show_create_video_dialog(self):
        """Show the dialog for creating videos from FITS files"""
        try:
            # Get current image name if available
            current_tab = self.tab_widget.currentWidget()
            current_file = current_tab.imagename if current_tab else None

            # Import and show dialog
            from .video_dialog import VideoCreationDialog

            # Store reference to prevent garbage collection
            self._video_dialog = VideoCreationDialog(self, current_file)
            self._video_dialog.show()
        except Exception as e:
            # Show error message with details
            from PyQt5.QtWidgets import QMessageBox

            error_message = f"Error opening video creation dialog: {str(e)}"
            print(f"[ERROR] {error_message}")  # Log to console
            QMessageBox.critical(self, "Error", error_message)

    def show_noaa_events_viewer(self):
        """Show the NOAA Solar Events Viewer."""
        try:
            from .noaa_events import show_noaa_events_viewer
            from datetime import date, datetime

            # Try to get date from currently open FITS file
            initial_date = None
            current_tab = self.tab_widget.currentWidget()
            if current_tab and hasattr(current_tab, "header") and current_tab.header:
                header = current_tab.header
                # Try common date keywords
                for key in ["DATE-OBS", "DATE_OBS", "DATE", "T_OBS"]:
                    if key in header:
                        date_str = header[key]
                        try:
                            # Parse various date formats
                            if "T" in str(date_str):
                                dt = datetime.fromisoformat(
                                    str(date_str).replace("Z", "+00:00").split("+")[0]
                                )
                                initial_date = dt.date()
                            elif "-" in str(date_str):
                                initial_date = datetime.strptime(
                                    str(date_str)[:10], "%Y-%m-%d"
                                ).date()
                            elif "/" in str(date_str):
                                initial_date = datetime.strptime(
                                    str(date_str)[:10], "%Y/%m/%d"
                                ).date()
                            if initial_date:
                                break
                        except (ValueError, TypeError):
                            continue

            # Show the viewer
            self._noaa_events_viewer = show_noaa_events_viewer(self, initial_date)

        except Exception as e:
            from PyQt5.QtWidgets import QMessageBox

            error_message = f"Error opening NOAA Events Viewer: {str(e)}"
            print(f"[ERROR] {error_message}")
            QMessageBox.critical(self, "Error", error_message)

    def show_noaa_events_for_current_image(self):
        """Show NOAA Solar Events for the current image date and auto-fetch events."""
        try:
            from .noaa_events.noaa_events_gui import NOAAEventsViewer
            from datetime import date, datetime
            import os

            # Get the current tab
            current_tab = self.tab_widget.currentWidget()
            if (
                not current_tab
                or not hasattr(current_tab, "imagename")
                or not current_tab.imagename
            ):
                from PyQt5.QtWidgets import QMessageBox

                QMessageBox.information(self, "Info", "No image is currently loaded.")
                return

            imagename = current_tab.imagename
            extracted_date = None

            # Method 1: FITS header
            lower_name = imagename.lower()
            if (
                lower_name.endswith(".fits")
                or lower_name.endswith(".fts")
                or lower_name.endswith(".fit")
            ):
                try:
                    from astropy.io import fits

                    header = fits.getheader(imagename)

                    # Check DATE-OBS (standard), DATE_OBS (IRIS), and STARTOBS
                    image_time = (
                        header.get("DATE-OBS")
                        or header.get("DATE_OBS")
                        or header.get("STARTOBS")
                    )

                    # Special handling for SOHO (DATE-OBS + TIME-OBS)
                    if (
                        header.get("TELESCOP") == "SOHO"
                        and header.get("TIME-OBS")
                        and image_time
                    ):
                        image_time = f"{image_time}T{header['TIME-OBS']}"

                    if image_time:
                        image_time = str(image_time)
                        if "T" in image_time:
                            clean_str = (
                                image_time.replace("Z", "").split("+")[0].split(".")[0]
                            )
                            if "-" in clean_str[11:]:
                                clean_str = clean_str[:19]
                            try:
                                extracted_date = datetime.fromisoformat(
                                    clean_str
                                ).date()
                            except ValueError:
                                date_part = clean_str.split("T")[0]
                                if len(date_part) >= 10:
                                    extracted_date = datetime.strptime(
                                        date_part[:10], "%Y-%m-%d"
                                    ).date()
                        elif "-" in image_time and len(image_time) >= 10:
                            extracted_date = datetime.strptime(
                                image_time[:10], "%Y-%m-%d"
                            ).date()
                except Exception as fits_err:
                    print(f"[ERROR] FITS header read failed: {fits_err}")
                    self.show_status_message(f"FITS header read failed: {fits_err}")

            # Method 2: CASA image
            if extracted_date is None:
                is_casa_image = os.path.isdir(imagename) or (
                    not lower_name.endswith(".fits")
                    and not lower_name.endswith(".fts")
                    and not lower_name.endswith(".fit")
                )

                if is_casa_image:
                    try:
                        from casatools import image as IA
                        from astropy.time import Time

                        ia_tool = IA()
                        ia_tool.open(imagename)
                        csys_record = ia_tool.coordsys().torecord()
                        ia_tool.close()

                        if "obsdate" in csys_record:
                            obsdate = csys_record["obsdate"]
                            m0 = obsdate.get("m0", {})
                            time_value = m0.get("value", None)
                            time_unit = m0.get("unit", None)
                            refer = obsdate.get("refer", None)

                            if (refer == "UTC" or time_unit == "d") and time_value:
                                t = Time(time_value, format="mjd")
                                extracted_date = t.to_datetime().date()
                    except Exception as casa_err:
                        print(f"[ERROR] CASA date extraction failed: {casa_err}")
                        self.show_status_message(
                            f"CASA date extraction failed: {casa_err}"
                        )

            # Method 3: Filename parsing
            if extracted_date is None:
                import re

                patterns = [
                    r"(\d{4})(\d{2})(\d{2})",  # YYYYMMDD
                    r"(\d{4})-(\d{2})-(\d{2})",  # YYYY-MM-DD
                    r"(\d{4})\.(\d{2})\.(\d{2})",  # YYYY.MM.DD
                ]
                for pattern in patterns:
                    match = re.search(pattern, imagename)
                    if match:
                        try:
                            y, m, d = (
                                int(match.group(1)),
                                int(match.group(2)),
                                int(match.group(3)),
                            )
                            if 1990 < y < 2100 and 1 <= m <= 12 and 1 <= d <= 31:
                                extracted_date = date(y, m, d)
                                break
                        except (ValueError, IndexError):
                            continue

            if extracted_date is None:
                from PyQt5.QtWidgets import QMessageBox

                QMessageBox.warning(
                    self,
                    "Date Not Found",
                    "Could not extract date from the current image.\n\n"
                    "Supported formats:\n"
                    "• FITS files with DATE-OBS header\n"
                    "• CASA images with observation date\n"
                    "• Files with date in filename (YYYYMMDD)",
                )
                return

            # Create and show the viewer with the date
            self._noaa_events_viewer = NOAAEventsViewer(self, extracted_date)
            self._noaa_events_viewer.show()

            # Auto-fetch events for the extracted date
            self._noaa_events_viewer.fetch_events()

        except Exception as e:
            from PyQt5.QtWidgets import QMessageBox

            error_message = f"Error showing NOAA Events: {str(e)}"
            print(f"[ERROR] {error_message}")
            import traceback

            traceback.print_exc()
            QMessageBox.critical(self, "Error", error_message)

    def show_batch_hpc_dialog(self):
        """Show the dialog for batch conversion to helioprojective coordinates"""
        try:
            # Get current image name if available
            current_tab = self.tab_widget.currentWidget()
            current_file = current_tab.imagename if current_tab else None

            # Import and show dialog
            from .dialogs import HPCBatchConversionDialog

            dialog = HPCBatchConversionDialog(self, current_file)
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            dialog.destroyed.connect(
                lambda: (
                    self._open_dialogs.remove(dialog)
                    if dialog in self._open_dialogs
                    else None
                )
            )
            self._open_dialogs.append(dialog)
            dialog.show()
        except Exception as e:
            # Show error message with details
            error_message = f"Error opening batch HPC conversion dialog: {str(e)}"
            print(f"[ERROR] {error_message}")  # Log to console
            QMessageBox.critical(self, "Error", error_message)

    def open_helioviewer_browser(self):
        """Open Helioviewer browser window."""
        try:
            from .helioviewer_browser import HelioviewerBrowser

            browser = HelioviewerBrowser(self)
            browser.show()
        except Exception as e:
            from PyQt5.QtWidgets import QMessageBox

            QMessageBox.critical(
                self, "Error", f"Failed to open Helioviewer Browser:\n{str(e)}"
            )

    # ==================== LOFAR Tools Launchers ====================
    # These tools are launched as separate processes to avoid memory conflicts
    # between python-casacore and casatasks/casatools

    def _get_clean_qt_env(self):
        """Get environment with Qt plugin paths cleared to avoid opencv conflicts."""
        import os

        env = os.environ.copy()
        # Remove Qt plugin paths that opencv-python sets, which conflict with PyQt5
        for key in ["QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"]:
            if key in env:
                del env[key]
        return env

    def _get_current_theme(self):
        """Get current theme name for passing to subprocess."""
        return theme_manager.current_theme

    def _launch_dynamic_spectrum_viewer(self):
        """Launch the Dynamic Spectrum Viewer as a separate process."""
        import subprocess
        import sys
        import os

        try:
            current_theme = self._get_current_theme()
            # Launch as separate process to avoid casacore/casatasks conflicts
            script = f"""
import sys
import os
# Clear opencv Qt paths before importing PyQt5
for key in ['QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH']:
    if key in os.environ:
        del os.environ[key]

from PyQt5.QtWidgets import QApplication
from solar_radio_image_viewer.from_simpl.view_dynamic_spectra_GUI import MainWindow
from solar_radio_image_viewer.from_simpl.simpl_theme import apply_theme, setup_high_dpi

setup_high_dpi()
app = QApplication(sys.argv)
apply_theme(app, "{current_theme}")
window = MainWindow(theme="{current_theme}")
window.show()
sys.exit(app.exec_())
"""
            env = self._get_clean_qt_env()
            launch_cwd = os.getcwd() if os.access(os.getcwd(), os.W_OK) else os.path.expanduser("~")
            subprocess.Popen(
                [sys.executable, "-c", script],
                start_new_session=True,
                env=env,
                cwd=launch_cwd,
            )
            self.statusBar().showMessage("Dynamic Spectrum Viewer launched", 3000)
        except Exception as e:
            error_message = f"Error launching Dynamic Spectrum Viewer: {str(e)}"
            print(f"[ERROR] {error_message}")
            import traceback

            traceback.print_exc()
            QMessageBox.critical(self, "Error", error_message)

    def _launch_caltable_visualizer(self):
        """Launch the Calibration Table Visualizer as a separate process."""
        import subprocess
        import sys
        import os

        try:
            current_theme = self._get_current_theme()
            # Launch as separate process to avoid casacore/casatasks conflicts
            script = f"""
import sys
import os
# Clear opencv Qt paths before importing PyQt5
for key in ['QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH']:
    if key in os.environ:
        del os.environ[key]

from PyQt5.QtWidgets import QApplication
from solar_radio_image_viewer.from_simpl.caltable_visualizer import VisualizationApp
from solar_radio_image_viewer.from_simpl.simpl_theme import apply_theme, setup_high_dpi

setup_high_dpi()
app = QApplication(sys.argv)
apply_theme(app, "{current_theme}")
window = VisualizationApp()
window.show()
sys.exit(app.exec_())
"""
            env = self._get_clean_qt_env()
            launch_cwd = os.getcwd() if os.access(os.getcwd(), os.W_OK) else os.path.expanduser("~")
            subprocess.Popen(
                [sys.executable, "-c", script],
                start_new_session=True,
                env=env,
                cwd=launch_cwd,
            )
            self.statusBar().showMessage("Calibration Table Visualizer launched", 3000)
        except Exception as e:
            error_message = f"Error launching Calibration Table Visualizer: {str(e)}"
            print(f"[ERROR] {error_message}")
            import traceback

            traceback.print_exc()
            QMessageBox.critical(self, "Error", error_message)

    def _launch_log_viewer(self):
        """Launch the Log Viewer as a separate process."""
        import subprocess
        import sys
        import os

        try:
            current_theme = self._get_current_theme()
            # Launch as separate process for consistency
            script = f"""
import sys
import os
# Clear opencv Qt paths before importing PyQt5
for key in ['QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH']:
    if key in os.environ:
        del os.environ[key]

from PyQt5.QtWidgets import QApplication
from solar_radio_image_viewer.from_simpl.pipeline_logger_gui import PipelineLoggerGUI
from solar_radio_image_viewer.from_simpl.simpl_theme import apply_theme, setup_high_dpi

setup_high_dpi()
app = QApplication(sys.argv)
apply_theme(app, "{current_theme}")
window = PipelineLoggerGUI(theme="{current_theme}")
window.show()
sys.exit(app.exec_())
"""
            env = self._get_clean_qt_env()
            launch_cwd = os.getcwd() if os.access(os.getcwd(), os.W_OK) else os.path.expanduser("~")
            subprocess.Popen(
                [sys.executable, "-c", script],
                start_new_session=True,
                env=env,
                cwd=launch_cwd,
            )
            self.statusBar().showMessage("Log Viewer launched", 3000)
        except Exception as e:
            error_message = f"Error launching Log Viewer: {str(e)}"
            print(f"[ERROR] {error_message}")
            import traceback

            traceback.print_exc()
            QMessageBox.critical(self, "Error", error_message)

    def _launch_create_dynamic_spectra(self):
        """Launch the Dynamic Spectra creation dialog as a separate process."""
        import subprocess
        import sys
        import os

        try:
            current_theme = self._get_current_theme()
            # Launch as separate process to avoid casacore/casatasks conflicts
            script = f"""
import sys
import os
# Clear opencv Qt paths before importing PyQt5
for key in ['QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH']:
    if key in os.environ:
        del os.environ[key]

from PyQt5.QtWidgets import QApplication
from solar_radio_image_viewer.from_simpl.dynamic_spectra_dialog import DynamicSpectraDialog
from solar_radio_image_viewer.from_simpl.simpl_theme import apply_theme, setup_high_dpi

setup_high_dpi()
app = QApplication(sys.argv)
apply_theme(app, "{current_theme}")
dialog = DynamicSpectraDialog(theme="{current_theme}")
dialog.show()
sys.exit(app.exec_())
"""
            env = self._get_clean_qt_env()
            launch_cwd = os.getcwd() if os.access(os.getcwd(), os.W_OK) else os.path.expanduser("~")
            subprocess.Popen(
                [sys.executable, "-c", script],
                start_new_session=True,
                env=env,
                cwd=launch_cwd,
            )
            self.statusBar().showMessage("Dynamic Spectra Creator launched", 3000)
        except Exception as e:
            error_message = f"Error launching Dynamic Spectra Creator: {str(e)}"
            print(f"[ERROR] {error_message}")
            import traceback

            traceback.print_exc()
            QMessageBox.critical(self, "Error", error_message)

    # =========================================================================
    # Remote Mode Methods
    # =========================================================================

    def show_remote_connection_dialog(self):
        """Show the SSH connection dialog."""
        try:
            from .remote import ConnectionDialog, RemoteFileCache
            from PyQt5.QtCore import Qt

            # Check if dialog is already open
            for dialog in self._open_dialogs:
                if isinstance(dialog, ConnectionDialog):
                    dialog.raise_()
                    dialog.activateWindow()
                    return

            dialog = ConnectionDialog(self)
            dialog.connectionEstablished.connect(self._on_remote_connected)

            # Clean up when closed
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            dialog.setModal(
                False
            )  # Override default modality set in ConnectionDialog.__init__
            dialog.destroyed.connect(
                lambda: (
                    self._open_dialogs.remove(dialog)
                    if dialog in self._open_dialogs
                    else None
                )
            )

            self._open_dialogs.append(dialog)
            dialog.show()

        except ImportError as e:
            QMessageBox.warning(
                self,
                "Missing Dependency",
                f"Remote mode requires paramiko. Install it with:\n\npip install paramiko\n\nError: {e}",
            )
        except Exception as e:
            QMessageBox.critical(
                self, "Error", f"Failed to open connection dialog: {e}"
            )

    def _on_remote_connected(self, connection):
        """Handle successful remote connection."""
        from .remote import RemoteFileCache

        self.remote_connection = connection
        if self.remote_cache is None:
            self.remote_cache = RemoteFileCache()

        self._update_remote_menu_state()
        self._update_remote_status_indicator()

        # Start periodic health check
        self._start_connection_health_timer()

    def show_remote_file_browser(self):
        """Show the remote file browser dialog."""
        if not self.remote_connection or not self.remote_connection.is_connected():
            QMessageBox.warning(
                self, "Not Connected", "Please connect to a remote server first."
            )
            return

        try:
            from .remote import RemoteFileBrowser

            browser = RemoteFileBrowser(
                self.remote_connection,
                cache=self.remote_cache,
                parent=self,
            )
            browser.fileSelected.connect(self._on_remote_file_selected)
            browser.exec_()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open remote browser: {e}")

    def _on_remote_file_selected(self, local_path: str):
        """Handle file selected from remote browser (now downloaded locally)."""
        if not local_path or not os.path.exists(local_path):
            return

        # Get the current tab and load the file
        current_tab = self.tab_widget.currentWidget()
        if current_tab:
            import time

            start_time = time.time()

            current_tab._reset_hpc_state()
            current_tab.imagename = local_path
            current_tab.dir_entry.setText(local_path)
            current_tab.contour_settings["contour_data"] = None
            current_tab.current_contour_wcs = None
            current_tab.figure.clear()
            current_tab.on_visualization_changed(dir_load=True)
            current_tab.update_tab_name_from_path(local_path)

            elapsed = time.time() - start_time
            basename = os.path.basename(local_path)
            self.statusBar().showMessage(
                f"Loaded remote file: {basename} ({elapsed:.2f}s)", 5000
            )

            current_tab._scan_directory_files()

    def disconnect_remote(self):
        """Disconnect from the remote server."""
        # Stop health check timer
        self._stop_connection_health_timer()

        if self.remote_connection:
            try:
                host_info = self.remote_connection.connection_info
                self.remote_connection.disconnect()
                self.statusBar().showMessage(f"Disconnected from {host_info}", 3000)
            except Exception as e:
                print(f"[WARNING] Error disconnecting: {e}")
            finally:
                self.remote_connection = None

        self._update_remote_menu_state()
        self._update_remote_status_indicator()

    def _update_remote_menu_state(self):
        """Update the enabled state of remote menu items."""
        connected = (
            self.remote_connection is not None and self.remote_connection.is_connected()
        )

        # Update menu actions
        if hasattr(self, "connect_remote_act"):
            self.connect_remote_act.setEnabled(not connected)
            if connected:
                self.connect_remote_act.setText(
                    f"🌐 Connected: {self.remote_connection.connection_info}"
                )
            else:
                self.connect_remote_act.setText("🌐 Connect to Remote Server...")

        if hasattr(self, "browse_remote_act"):
            self.browse_remote_act.setVisible(connected)

        if hasattr(self, "disconnect_remote_act"):
            self.disconnect_remote_act.setVisible(connected)

    def _update_remote_status_indicator(self):
        """Update the permanent status bar indicator for remote connection."""
        if hasattr(self, "remote_status_btn"):
            connected = self.remote_connection and self.remote_connection.is_connected()
            if connected:
                self.remote_status_btn.setText(
                    f"🌐 {self.remote_connection.connection_info}"
                )
                self.remote_status_btn.setToolTip(
                    "Click to disconnect from remote server"
                )
            else:
                self.remote_status_btn.setText("🔌 Not Connected")
                self.remote_status_btn.setToolTip("Click to connect to a remote server")
            self._apply_remote_status_style(connected)

    def _apply_remote_status_style(self, connected: bool):
        """Apply styling to the remote status button based on connection state."""
        if connected:
            self.remote_status_btn.setStyleSheet(
                """
                QPushButton {
                    color: #4CAF50;
                    font-weight: bold;
                    padding: 4px 10px;
                    margin-right: 5px;
                    background-color: rgba(76, 175, 80, 0.15);
                    border: 1px solid #4CAF50;
                    border-radius: 4px;
                    min-height: 16px;
                }
                QPushButton:hover {
                    background-color: rgba(244, 67, 54, 0.15);
                    border-color: #F44336;
                    color: #F44336;
                }
            """
            )
        else:
            self.remote_status_btn.setStyleSheet(
                """
                QPushButton {
                    color: #9E9E9E;
                    font-weight: bold;
                    padding: 4px 10px;
                    margin-right: 5px;
                    background-color: transparent;
                    border: 1px solid #757575;
                    border-radius: 4px;
                    min-height: 16px;
                }
                QPushButton:hover {
                    background-color: rgba(33, 150, 243, 0.15);
                    border-color: #2196F3;
                    color: #2196F3;
                }
            """
            )

    def _on_remote_status_clicked(self):
        """Handle click on the remote status button."""
        if self.remote_connection and self.remote_connection.is_connected():
            # Already connected - disconnect
            self.disconnect_remote()
        else:
            # Not connected - show connection dialog
            self.show_remote_connection_dialog()

    def _start_connection_health_timer(self):
        """Start a timer to periodically check connection health."""
        if not hasattr(self, "_health_check_timer"):
            from PyQt5.QtCore import QTimer

            self._health_check_timer = QTimer(self)  # Parent to self
            self._health_check_timer.timeout.connect(self._check_connection_health)

        # Check every 5 seconds
        self._health_check_timer.start(5000)
        # print("[SSH] Health check timer started (5s interval)")

    def _stop_connection_health_timer(self):
        """Stop the connection health check timer."""
        if hasattr(self, "_health_check_timer"):
            self._health_check_timer.stop()
            # print("[SSH] Health check timer stopped")

    def show_cache_manager(self):
        """Show cache management dialog with options to clear different cache types."""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QGroupBox, QCheckBox
        from pathlib import Path
        import shutil
        
        # Check if dialog is already open
        if hasattr(self, "_cache_manager_dialog") and self._cache_manager_dialog:
            self._cache_manager_dialog.raise_()
            self._cache_manager_dialog.activateWindow()
            return

        dialog = QDialog(self)
        self._cache_manager_dialog = dialog  # Keep reference
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.finished.connect(lambda: setattr(self, "_cache_manager_dialog", None))
        
        dialog.setWindowTitle("Cache Manager")
        set_hand_cursor(dialog)
        dialog.setMinimumWidth(500)
        layout = QVBoxLayout(dialog)
        
        # Header
        header = QLabel("<h3>🗑️ Cache Manager</h3>")
        header.setStyleSheet("color: #2196F3;")
        layout.addWidget(header)
        
        desc = QLabel("Select which caches to clear. This action cannot be undone.")
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #666; margin-bottom: 10px;")
        layout.addWidget(desc)
        
        # Calculate cache sizes
        cache_info = []
        
        # 1. Remote Cache
        remote_size = 0
        remote_count = 0
        try:
            if self.remote_cache is None:
                from .remote import RemoteFileCache
                self.remote_cache = RemoteFileCache()
            remote_size, remote_count = self.remote_cache.get_cache_size()
        except Exception:
            pass
        cache_info.append(("remote", "📁 Remote Files Cache", remote_size, remote_count, 
                          "Downloaded files from remote SSH servers"))
        
        # 2. NOAA Events Cache
        noaa_size = 0
        noaa_count = 0
        try:
            noaa_cache_dir = Path.home() / ".cache" / "solarviewer" / "noaa_events"
            if noaa_cache_dir.exists():
                for f in noaa_cache_dir.rglob("*"):
                    if f.is_file():
                        noaa_size += f.stat().st_size
                        noaa_count += 1
        except Exception:
            pass
        cache_info.append(("noaa", "🌞 Solar Activity Cache", noaa_size, noaa_count,
                          "Solar events, active regions, conditions, and spectrographs"))

        # 3. Helioviewer / API JSON Cache
        json_size = 0
        json_count = 0
        try:
            root_cache = Path.home() / ".cache" / "solarviewer"
            if root_cache.exists():
                for f in root_cache.glob("*.json"):
                    if f.is_file():
                        json_size += f.stat().st_size
                        json_count += 1
        except Exception:
            pass
        if json_size > 0:
            cache_info.append(("json", "📄 API Metadata Cache", json_size, json_count,
                              "Cached JSON responses from Helioviewer and other APIs"))

        # 4. Sunpy Data
        sunpy_size = 0
        sunpy_count = 0
        try:
            # Check both ~/sunpy and ~/.sunpy
            for p in [Path.home() / "sunpy", Path.home() / ".sunpy"]:
                if p.exists():
                    for f in p.rglob("*"):
                        if f.is_file():
                            sunpy_size += f.stat().st_size
                            sunpy_count += 1
        except Exception:
            pass
        if sunpy_size > 0:
            cache_info.append(("sunpy", "🛰️ Sunpy Data Cache", sunpy_size, sunpy_count,
                              "Downloaded AIA pointing tables, .nc files, etc."))
        
        # 5. Old solarviewer_cache (if exists)
        old_cache_size = 0
        old_cache_count = 0
        try:
            old_cache_dir = Path.home() / ".solarviewer_cache"
            if old_cache_dir.exists():
                for f in old_cache_dir.rglob("*"):
                    if f.is_file():
                        old_cache_size += f.stat().st_size
                        old_cache_count += 1
        except Exception:
            pass
        if old_cache_size > 0:
            cache_info.append(("old", "📦 Legacy Cache", old_cache_size, old_cache_count,
                              "Old cache location (~/.solarviewer_cache)"))
        
        # Create checkboxes for each cache type
        checkboxes = {}
        total_size = 0
        
        for key, name, size, count, description in cache_info:
            if size > 0:  # Only show categories that have data
                group = QGroupBox()
                group.setStyleSheet("QGroupBox { border: 1px solid #444; border-radius: 4px; padding: 10px; margin-top: 8px; }")
                group_layout = QVBoxLayout(group)
                
                # Checkbox with name and size
                cb = QCheckBox(f"{name} ({self._format_size(size)}, {count} files)")
                cb.setChecked(False)
                checkboxes[key] = cb
                group_layout.addWidget(cb)
                
                # Description
                desc_label = QLabel(description)
                desc_label.setStyleSheet("color: #888; font-size: 11px; margin-left: 20px;")
                group_layout.addWidget(desc_label)
                
                layout.addWidget(group)
                total_size += size
        
        if not checkboxes:
            no_cache = QLabel("All caches are currently empty.")
            no_cache.setStyleSheet("color: #888; font-style: italic; margin: 20px;")
            no_cache.setAlignment(Qt.AlignCenter)
            layout.addWidget(no_cache)
        else:
            # Total size
            total_label = QLabel(f"<b>Total: {self._format_size(total_size)}</b>")
            total_label.setStyleSheet("margin-top: 10px; font-size: 14px;")
            layout.addWidget(total_label)
        
        # Buttons
        btn_layout = QHBoxLayout()
        
        select_all_btn = QPushButton("Select All")
        select_all_btn.clicked.connect(lambda: [cb.setChecked(True) for cb in checkboxes.values() if cb.isEnabled()])
        btn_layout.addWidget(select_all_btn)
        
        btn_layout.addStretch()
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)
        
        clear_btn = QPushButton("🗑️ Clear Selected")
        clear_btn.setStyleSheet("background-color: #F44336; color: white; font-weight: bold; padding: 6px 16px;")
        
        def do_clear():
            # Get list of selected cache keys
            selected_keys = [k for k, cb in checkboxes.items() if cb.isChecked()]
            
            if not selected_keys:
                self.statusBar().showMessage("⚠️ No cache categories selected to clear", 5000)
                return
            
            # Confirm before clearing
            reply = QMessageBox.question(
                self,
                "Confirm Clear",
                f"Are you sure you want to clear the selected caches ({len(selected_keys)})?\n\nThis action cannot be undone.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply != QMessageBox.Yes:
                return
            
            cleared = []
            
            if "remote" in selected_keys:
                try:
                    if self.remote_cache:
                        self.remote_cache.clear_cache()
                    else:
                        from .remote import RemoteFileCache
                        RemoteFileCache().clear_cache()
                    
                    from .remote import RemoteFileBrowser
                    if hasattr(RemoteFileBrowser, "_listing_cache"):
                        RemoteFileBrowser._listing_cache.clear()
                    cleared.append("Remote Files")
                except Exception as e: print(f"[Cache] Error clearing remote: {e}")
            
            if "noaa" in selected_keys:
                try:
                    noaa_cache_dir = Path.home() / ".cache" / "solarviewer" / "noaa_events"
                    if noaa_cache_dir.exists():
                        shutil.rmtree(noaa_cache_dir)
                    cleared.append("Solar Activity")
                except Exception as e: print(f"[Cache] Error clearing NOAA: {e}")

            if "json" in selected_keys:
                try:
                    root_cache = Path.home() / ".cache" / "solarviewer"
                    if root_cache.exists():
                        for f in root_cache.glob("*.json"):
                            f.unlink()
                    cleared.append("API Metadata")
                except Exception as e: print(f"[Cache] Error clearing JSON: {e}")

            if "sunpy" in selected_keys:
                try:
                    for p in [Path.home() / "sunpy", Path.home() / ".sunpy"]:
                        if p.exists():
                            shutil.rmtree(p)
                    cleared.append("Sunpy Data")
                except Exception as e: print(f"[Cache] Error clearing Sunpy: {e}")
            
            if "old" in selected_keys:
                try:
                    old_cache_dir = Path.home() / ".solarviewer_cache"
                    if old_cache_dir.exists():
                        shutil.rmtree(old_cache_dir)
                    cleared.append("Legacy Cache")
                except Exception as e: print(f"[Cache] Error clearing legacy: {e}")
            
            if cleared:
                self.statusBar().showMessage(f"🗑️ Cleared: {', '.join(cleared)}", 5000)
                QMessageBox.information(self, "Cache Cleared", f"Successfully cleared:\n• " + "\n• ".join(cleared))
                self._update_cache_menu_item()
                dialog.accept() 
            else:
                QMessageBox.warning(self, "Error", "Failed to clear selected caches.")
        
        clear_btn.clicked.connect(do_clear)
        btn_layout.addWidget(clear_btn)
        
        layout.addLayout(btn_layout)
        dialog.show()  # Non-modal
        dialog.raise_()
        dialog.activateWindow()

    def _format_size(self, size_bytes: int) -> str:
        """Format bytes to human readable string."""
        if size_bytes == 0:
            return "0 B"

        power = 1024
        n = 0
        power_labels = {0: "", 1: "K", 2: "M", 3: "G", 4: "T"}
        while size_bytes >= power:
            size_bytes /= power
            n += 1

        return f"{size_bytes:.1f} {power_labels[n]}B"

    def _update_cache_menu_item(self):
        """Update the Manage Cache menu item with current total cache size."""
        if not hasattr(self, "clear_cache_act"):
            return

        try:
            from pathlib import Path
            total_size = 0
            
            # 1. Main solarviewer cache (~/.cache/solarviewer)
            # This includes remote files, noaa events, and json metadata
            try:
                root_cache = Path.home() / ".cache" / "solarviewer"
                if root_cache.exists():
                    for f in root_cache.rglob("*"):
                        if f.is_file():
                            total_size += f.stat().st_size
            except Exception: pass

            # 2. Sunpy cache
            try:
                for p in [Path.home() / "sunpy", Path.home() / ".sunpy"]:
                    if p.exists():
                        for f in p.rglob("*"):
                            if f.is_file():
                                total_size += f.stat().st_size
            except Exception: pass

            # 3. Legacy cache
            try:
                old_cache = Path.home() / ".solarviewer_cache"
                if old_cache.exists():
                    for f in old_cache.rglob("*"):
                        if f.is_file():
                            total_size += f.stat().st_size
            except Exception: pass
            
            size_str = self._format_size(total_size)
            self.clear_cache_act.setText(f"🗑️ Manage Cache ({size_str})")

        except Exception as e:
            # Fallback if something goes wrong
            print(f"[WARNING] Failed to update cache size: {e}")
            self.clear_cache_act.setText("🗑️ Manage Cache...")

    def _check_connection_health(self):
        """Periodically check connection health and auto-reconnect if needed."""
        # print("[SSH] Health check running...")  # Debug

        if not self.remote_connection:
            # print("[SSH] Health check: No remote connection")
            return

        # Run ping in background thread to avoid blocking UI
        from PyQt5.QtCore import QThread, pyqtSignal

        # If previous ping is still running after 10s, assume connection is dead
        if (
            hasattr(self, "_ping_thread")
            and self._ping_thread
            and self._ping_thread.isRunning()
        ):
            if hasattr(self, "_ping_start_time"):
                import time

                elapsed = time.time() - self._ping_start_time
                if elapsed > 10:
                    # print(f"[SSH] Health check: Ping stuck for {elapsed:.1f}s - connection appears dead")
                    # Connection is dead - the ping is stuck
                    try:
                        self._ping_thread.terminate()
                        self._ping_thread.wait(1000)
                    except:
                        pass
                    # Trigger reconnect
                    if self.remote_connection:
                        self.remote_connection._connected = False
                        self.statusBar().showMessage(
                            "⚠️ Remote connection lost - click status to reconnect",
                            10000,
                        )
                        self._update_remote_status_indicator()
                        self._update_remote_menu_state()
                    return
            # print("[SSH] Health check: Previous ping still running, skipping")
            return

        class PingThread(QThread):
            result = pyqtSignal(bool)

            def __init__(self, connection):
                super().__init__()
                self.connection = connection

            def run(self):
                import socket

                try:
                    # Quick passive check first
                    if not self.connection._connected or not self.connection._client:
                        self.result.emit(False)
                        return

                    transport = self.connection._client.get_transport()
                    if transport is None or not transport.is_active():
                        self.result.emit(False)
                        return

                    # Set socket timeout temporarily
                    sock = transport.sock
                    old_timeout = sock.gettimeout()
                    sock.settimeout(3.0)  # 3 second timeout

                    try:
                        # Try to open a new SFTP channel - this requires server response
                        # If network is down, this will timeout
                        sftp = self.connection._client.open_sftp()
                        sftp.close()  # Close it immediately
                        self.result.emit(True)
                    except socket.timeout:
                        print("[SSH] Ping timeout - connection appears dead")
                        self.result.emit(False)
                    except Exception as e:
                        print(f"[SSH] Ping failed: {e}")
                        self.result.emit(False)
                    finally:
                        sock.settimeout(old_timeout)

                except Exception as e:
                    print(f"[SSH] Ping error: {e}")
                    self.result.emit(False)

        def on_ping_result(is_alive):
            if not is_alive:
                print("[SSH] Health check: Connection lost, attempting reconnect...")
                if self.remote_connection and self.remote_connection.ensure_connected():
                    print("[SSH] Health check: Reconnected successfully")
                    self.statusBar().showMessage("🔄 Remote connection restored", 5000)
                else:
                    print("[SSH] Health check: Reconnect failed")
                    self.statusBar().showMessage(
                        "⚠️ Remote connection lost - click status to reconnect", 10000
                    )

                self._update_remote_status_indicator()
                self._update_remote_menu_state()

        # print("[SSH] Health check: Pinging connection in background...")
        import time

        self._ping_start_time = time.time()
        self._ping_thread = PingThread(self.remote_connection)
        self._ping_thread.result.connect(on_ping_result)
        self._ping_thread.start()

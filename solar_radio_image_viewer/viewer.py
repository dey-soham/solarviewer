import os
import threading

import numpy as np

import matplotlib

matplotlib.use("Qt5Agg")
import matplotlib.pyplot as plt
import matplotlib.style as mplstyle
from matplotlib.backends.backend_qt5agg import (
    NavigationToolbar2QT as NavigationToolbar,
)
from matplotlib import rcParams

from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QAction,
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
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QToolBar,
    QHeaderView,
    QFormLayout,
    QSplitter,
    # QListWidget,
    # QListWidgetItem,
    QActionGroup,
    QSizePolicy,
)
from PyQt5.QtCore import Qt, QSettings, QSize, QTimer, pyqtSignal, QThread
from PyQt5.QtGui import QIcon, QColor, QPalette

# from PyQt5.QtGui import QColor, QPalette, QPainter

from .processing.metadata import get_image_metadata
from .ui.styles import (
    theme_manager,
    get_stylesheet,
    set_hand_cursor,
)
from .dialogs import UpdateDialog
from .ui.searchable_combobox import ColormapSelector
from .ui.icons import get_resource_path, themed_icon
from .ui.segmented_control import SegmentedControl
from .ui.custom_tabs import CustomTabWidget
from .ui.item_delegates import DisabledItemDelegate
from .utils.update_checker import check_for_updates
from .version import __version__
from .tools import (
    RegionMode,
    RoiStatsMixin,
    RulerMixin,
    ProfileMixin,
    TbModeMixin,
    AnnotationsMixin,
    AnnotationDialogsMixin,
    FittingMixin,
    HpcViewMixin,
    SolarDiskMixin,
    ImageStatsMixin,
    SolarEventsMixin,
)
from .core.presets import display_range
from .processing.colormaps import ensure_solar_colormaps
from .core import (
    CanvasMixin,
    ContoursMixin,
    FileNavigationMixin,
    PresetsMixin,
    RenderingMixin,
    ExportsMixin,
    ExternalToolsMixin,
    PresetMenuMixin,
    RemoteModeMixin,
)
from .processing.fits_io import readable_image_path
from .processing.contour_layers import default_contour_options, new_contour_layer
from .processing.image_meta import casa_info, fits_header
from .processing.prefetch import image_prefetcher
from .processing.plot_style import (
    DEFAULT_BEAM_STYLE,
    DEFAULT_GRID_STYLE,
    DEFAULT_PLOT_SETTINGS,
    DEFAULT_SOLAR_DISK_STYLE,
    default_style,
)


def _preload_display_modules():
    """Import modules that drawing the first image otherwise waits ~1 s for:
    astropy.wcs (plot axes) and sunpy's coordinates (solar disk size)."""
    try:
        import astropy.wcs  # noqa: F401
        import sunpy.coordinates.sun  # noqa: F401
    except Exception:
        pass


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



def update_matplotlib_theme():
    """Update matplotlib rcParams based on current theme."""
    palette = theme_manager.palette
    rcParams.update(theme_manager.matplotlib_params)



# Initialize matplotlib with default theme
rcParams["axes.linewidth"] = 1.4
rcParams["font.size"] = 12
update_matplotlib_theme()
mplstyle.use("fast")



class SolarRadioImageTab(
    RenderingMixin,
    CanvasMixin,
    ContoursMixin,
    FileNavigationMixin,
    PresetsMixin,
    RoiStatsMixin,
    RulerMixin,
    ProfileMixin,
    TbModeMixin,
    AnnotationsMixin,
    HpcViewMixin,
    SolarDiskMixin,
    ImageStatsMixin,
    SolarEventsMixin,
    QWidget,
):
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
        self.psf = None
        self.current_roi = None
        self.roi_selector = None
        self.imagename = None
        self.solar_disk_center = None
        self.solar_disk_diameter_arcmin = 32.0
        self.solar_disk_auto_compute = True
        # Solar disk style properties
        self.solar_disk_style = default_style(DEFAULT_SOLAR_DISK_STYLE)

        # Annotations (tracked for remove/clear support)
        self.shape_annotations = []
        self.text_annotations = []
        self.arrow_annotations = []

        # Beam style properties
        self.beam_style = default_style(DEFAULT_BEAM_STYLE)

        # Grid style properties
        self.grid_style = default_style(DEFAULT_GRID_STYLE)

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

        # Coordinate-transformed copies of contour images, by source path
        self._contour_conversions = {}

        # Track non-modal dialogs to prevent garbage collection
        self._open_dialogs = []

        # Unique ID for temp file naming (prevents collisions in multi-tab);
        # it carries the process id, so leftovers of closed sessions can be
        # told apart (processing.temp_files)
        from .processing.temp_files import session_id

        self._temp_file_id = session_id()

        # Image metadata cache for lazy WCS/colorbar computation
        # These are expensive to compute and only need to refresh when imagename changes
        self._cached_imagename = None  # Track which image is cached
        self._cached_fits_header = None  # FITS header cache
        self._cached_fits_flag = False  # Whether current image is FITS
        self._cached_csys = None  # CASA coordsys cache
        self._cached_summary = None  # CASA summary cache
        self._cached_csys_record = None  # CASA csys record cache

        # Debounce timers for UI responsiveness
        self._gamma_debounce_timer = None

        # Contour layers (up to MAX_CONTOUR_LAYERS) and options shared by all
        self.contour_layers = [new_contour_layer()]
        self.contour_options = default_contour_options()

        # Plot customization settings
        self.plot_settings = default_style(DEFAULT_PLOT_SETTINGS)

        # Rendering optimization state
        self._last_rendered_state = {}
        self.image_plot = None

        # View navigation: zoom/pan glides, drag panning, toolbar history
        self._zoom_timer = QTimer()
        self._zoom_timer.timeout.connect(self._perform_zoom_step)
        self._view_anim = None
        self._is_panning = False
        self._nav_record_timer = QTimer()
        self._nav_record_timer.setSingleShot(True)
        self._nav_record_timer.setInterval(400)
        self._nav_record_timer.timeout.connect(self._record_view)

        # Default mode
        self.region_mode = RegionMode.PAN

        self.setup_ui()
        self._setup_file_navigation_shortcuts()
        self._set_button_cursors()

        # F11 for fullscreen toggle
        # fullscreen_shortcut = QShortcut(QKeySequence("F11"), self)
        # fullscreen_shortcut.activated.connect(self._toggle_fullscreen)

    def _set_button_cursors(self):
        """Set pointing hand cursor on all buttons and interactive widgets for better UX"""
        set_hand_cursor(self)

    def show_status_message(self, message, timeout=0):
        """Helper method to show messages in the status bar"""
        main_window = self.window()
        try:
            if main_window:
                main_window.statusBar().showMessage(message, timeout)
                QApplication.processEvents()
        except AttributeError:
            pass

    def setup_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(0)

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
        layout.setSpacing(8)

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
            self.invalidate_contours()

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
            QCheckBox:disabled {{
                color: #4a4a6a;
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
            QCheckBox::indicator:disabled {{
                background-color: {toggle_bg};
                border-color: {toggle_border};
                opacity: 0.5;
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
        self.show_beam_checkbox = QCheckBox("")
        self.show_beam_label = QLabel("Beam")
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
        left_layout_0.addWidget(self.show_beam_label)
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
        self.auto_minmax_button.clicked.connect(
            lambda: self.apply_auto_range("minmax")
        )

        self.auto_percentile_button = QPushButton("1-99%")
        self.auto_percentile_button.setStyleSheet(preset_btn_style)
        self.auto_percentile_button.clicked.connect(
            lambda: self.apply_auto_range("p1_99")
        )

        self.auto_median_button = QPushButton("Med±3σ")
        self.auto_median_button.setStyleSheet(preset_btn_style)
        self.auto_median_button.clicked.connect(
            lambda: self.apply_auto_range("median_rms")
        )

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
        self.auto_minmax_button.clicked.connect(
            lambda: self.apply_auto_range("minmax")
        )
        self.auto_percentile_button = QPushButton("1-99%")
        self.auto_percentile_button.clicked.connect(
            lambda: self.apply_auto_range("p1_99")
        )
        self.auto_median_button = QPushButton("Med±3σ")
        self.auto_median_button.clicked.connect(
            lambda: self.apply_auto_range("median_rms")
        )
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
            lambda: self.reset_view(show_status_message=True, animate=True)
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

        # Pan action (default)
        self.pan_action = QAction(
            QIcon(get_resource_path("assets/pan.png")),
            "",
            self,
        )
        self.pan_action.setToolTip("Pan")
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
            lambda: self.reset_view(show_status_message=True, animate=True)
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
        from .ui.styles import get_icon_path

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
        from .ui.styles import theme_manager

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
        from .ui.styles import theme_manager

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
        from .ui.styles import theme_manager

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
        self.coord_label = QLabel("No image loaded")
        self.coord_label.setAlignment(Qt.AlignCenter)
        self.coord_label.setStyleSheet("font-family: monospace; font-size: 10.5pt;")
        self.coord_label.setMinimumHeight(55)
        layout.addWidget(self.coord_label)
        parent_layout.addWidget(group)

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
            requested = colormap_name or getattr(
                getattr(self, "cmap_combo", None), "currentText", lambda: ""
            )()
            if requested and requested not in plt.colormaps():
                ensure_solar_colormaps()
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
                # Let a background read of this file finish before reading it here
                image_prefetcher.settle(self.imagename)
                # Validate Stokes selection when loading a new image
                if dir_load:
                    stokes = self._validate_and_switch_stokes(self.imagename, stokes)
                elif getattr(self, "_range_stokes", stokes) != stokes:
                    # The Min/Max boxes hold the range of the previously shown
                    # Stokes product, which means nothing for this one
                    vmin_val = vmax_val = None
                self.load_data(
                    self.imagename, stokes, threshold, auto_adjust_rms=dir_load
                )
                self._range_stokes = stokes
            if dir_load:
                mw = self.window()
                auto_presets = (
                    hasattr(mw, "auto_preset_act") and mw.auto_preset_act.isChecked()
                )
                is_preset_matched = False

                if auto_presets:
                    is_preset_matched = self._auto_apply_instrument_preset()

                if not is_preset_matched:
                    # Reset to defaults if the previous load was a preset
                    if getattr(self, "_was_preset_image", False):
                        if hasattr(self, "cmap_combo"):
                            self.cmap_combo.blockSignals(True)
                            self.cmap_combo.setCurrentText("viridis")
                            self.cmap_combo.blockSignals(False)
                        if hasattr(self, "stretch_combo"):
                            self.stretch_combo.blockSignals(True)
                            self.stretch_combo.setCurrentText("linear")
                            self.stretch_combo.blockSignals(False)
                        if hasattr(self, "gamma_entry"):
                            self.gamma_entry.blockSignals(True)
                            self.gamma_entry.setText("1.0")
                            self.gamma_entry.blockSignals(False)
                        cmap = "viridis"
                        stretch = "linear"
                        gamma = 1.0

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

                self._was_preset_image = is_preset_matched
            elif vmin_val is None or vmax_val is None:
                self.apply_auto_range("minmax")
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
            if dir_load:
                # After the caller has rescanned the directory
                QTimer.singleShot(0, self._prefetch_neighbour_files)
        except Exception as e:
            if main_window:
                self.show_status_message(f"Error: {str(e)}")
            QMessageBox.critical(self, "Error", f"Failed to load/plot data: {str(e)}")

    def set_range(self, vmin_val, vmax_val):
        if self.current_image_data is None:
            return

        data = self.current_image_data
        finite = np.isfinite(data).any()
        dmin = float(np.nanmin(data)) if finite else float("nan")
        dmax = float(np.nanmax(data)) if finite else float("nan")
        if not dmax > dmin:
            # Constant or fully masked map: don't keep the previous range
            low, high = display_range(dmin, dmax)
            self.vmin_entry.setText(f"{low:.3f}")
            self.vmax_entry.setText(f"{high:.3f}")
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

    def _on_theme_change(self, theme):
        """Handle theme change - update icons"""
        from .ui.styles import get_icon_path

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

    def _load_fits_stokes(self, imagename, stokes, rms_box=(0, 200, 0, 130)):
        """
        Load a specific Stokes parameter (or derived parameter) from a FITS file.
        Handles FITS files with Stokes axis (like HPC converted files).
        """
        from astropy.io import fits

        hdul = fits.open(readable_image_path(imagename), memmap=True)
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

    def _load_target_size(self):
        """Max dimension to downsample loads to in fast load mode, 0 for none."""
        if hasattr(self, "downsample_toggle") and self.downsample_toggle.isChecked():
            return 800  # Smart downsampling to ~800px max dimension
        return 0

    def load_data(self, imagename, stokes, threshold, auto_adjust_rms=False):
        # Clear WCS cache so new images don't accidentally reuse old WCS objects
        # if the new dictionary falls on the same memory address (id)
        # self._cached_wcs_obj = None
        # self._cached_wcs_id = None
        import time

        start_time = time.time()
        try:
            # Use the current RMS box when loading data
            from .processing.image_data import get_pixel_values_from_image

            target_size = self._load_target_size()

            image_prefetcher.settle(imagename)
            prefetched = image_prefetcher.take(
                imagename, stokes, threshold, tuple(self.current_rms_box), target_size
            )
            if prefetched is not None:
                pix, csys, psf = prefetched
            else:
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
                    for layer in self.contour_layers:
                        if layer["source"] == "same":
                            layer["rms_box"] = tuple(self.current_rms_box)

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
                    pix = fits.getdata(readable_image_path(imagename))
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
            # For HPC/SOLAR-X or HPLN images, disk center is at world (0,0)
            _is_hpc = self._is_already_hpc()
            if _is_hpc and self.current_wcs:
                try:
                    # Use CASA coordsys to find pixel of world (0,0)
                    # Use reference values for non-spatial axes (freq, Stokes)
                    ref_world = list(self.current_wcs.referencevalue()["numeric"])
                    ref_world[0] = 0.0  # SOLAR-X / HPLN = 0
                    ref_world[1] = 0.0  # SOLAR-Y / HPLT = 0
                    pix_origin = self.current_wcs.topixel(ref_world)["numeric"]
                    cx, cy = float(pix_origin[0]), float(pix_origin[1])
                    self.solar_disk_center = (cx, cy)
                except Exception:
                    self.solar_disk_center = (width // 2, height // 2)
            else:
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
                self.current_header = dict(fits_header(imagename))
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
                    # Get CASA image info
                    info = casa_info(imagename)
                    csys = info.coordsys()

                    # Build header dict from CASA info
                    self.current_header = {}

                    # Get beam info
                    beam = info.beam
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
                    bunit = info.brightness_unit
                    self.current_header["BUNIT"] = bunit
                    self._current_bunit = bunit

                    # Get coordinate names for HPC detection (before closing ia)
                    dimension_names = [n.upper() for n in csys.names()]

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
            self.show_beam_label.setEnabled(False)
        else:
            self.show_beam_checkbox.setEnabled(True)
            self.show_beam_label.setEnabled(True)

        # Enable NOAA Events button when an image is loaded
        if hasattr(self, "noaa_events_btn"):
            self.noaa_events_btn.setEnabled(True)
        if hasattr(self, "helioviewer_btn"):
            self.helioviewer_btn.setEnabled(True)

        # self.plot_image()
        # self.schedule_plot()

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
            self._range_stokes = stokes
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
            from .ui.styles import theme_manager

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

        # Changing the current item's flags/colour makes QComboBox emit
        # currentTextChanged, which would reload and replot the image twice.
        # Nothing is selected here, so keep the combo quiet while updating.
        was_blocked = self.stokes_combo.blockSignals(True)
        try:
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
        finally:
            self.stokes_combo.blockSignals(was_blocked)

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
        from .processing.stokes import get_available_stokes

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
                from PyQt5 import sip

                if sip.isdeleted(dialog):
                    return

                if not viewer:
                    try:
                        dialog.close()
                    except RuntimeError:
                        pass
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
                # Dialog was likely deleted during long-running plot update
                pass
            except Exception as e:
                if viewer:
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

    '''def _toggle_fullscreen(self):
        """Toggle fullscreen mode for the main window"""
        main_window = self.window()
        if main_window:
            if main_window.isFullScreen():
                main_window.showNormal()
            else:
                main_window.showFullScreen()
    '''

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
        # Clean up coordinate-transformed contour images
        if hasattr(self, "_contour_conversions"):
            self.remove_contour_conversions()
        super().closeEvent(event)

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
        if hasattr(self, "zoom_60arcmin_action"):
            self.zoom_60arcmin_action.setIcon(QIcon(themed_icon("zoom_60arcmin.png")))
        if hasattr(self, "reset_view_action"):
            self.reset_view_action.setIcon(QIcon(themed_icon("reset.png")))
        if hasattr(self, "pan_action"):
            self.pan_action.setIcon(QIcon(themed_icon("pan.png")))
        if hasattr(self, "rect_action"):
            self.rect_action.setIcon(QIcon(themed_icon("rectangle_selection.png")))
        if hasattr(self, "ellipse_action"):
            self.ellipse_action.setIcon(QIcon(themed_icon("ellipse_selection.png")))
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



class SolarRadioImageViewerApp(
    AnnotationDialogsMixin,
    FittingMixin,
    ExportsMixin,
    ExternalToolsMixin,
    PresetMenuMixin,
    RemoteModeMixin,
    QMainWindow,
):
    def __init__(self, imagename=None, fast_preview=False):
        super().__init__()

        self._fast_preview = fast_preview

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

        # Import what the first image will need while the user picks one
        threading.Thread(target=_preload_display_modules, daemon=True).start()

        # Register for theme changes
        theme_manager.register_callback(self._on_theme_changed)

        # Enable drag and drop
        self.setAcceptDrops(True)

        self.statusBar().showMessage("Ready")
        self.create_menus()

        # Set hand cursor for all interactive elements programmatically
        self._set_hand_cursor_recursive(self)

        first_tab = self.add_new_tab("Tab1")

        # Apply fast preview flag from CLI
        if self._fast_preview and hasattr(first_tab, "downsample_toggle"):
            first_tab.downsample_toggle.setChecked(True)

        if imagename and os.path.exists(imagename):
            first_tab.imagename = imagename
            first_tab.dir_entry.setText(imagename)

            # Sync CASA/FITS toggle with the loaded file type
            if os.path.isdir(imagename):
                first_tab.file_type_toggle.setSelectedIndex(0)  # CASA
            else:
                first_tab.file_type_toggle.setSelectedIndex(1)  # FITS

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

    def dragEnterEvent(self, event):
        """Handle drag enter events for file dropping."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        """Handle drop events to load images/files."""
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()

            # Use QTimer to delay processing slightly so the drag operation completes properly
            from PyQt5.QtCore import QTimer

            QTimer.singleShot(10, lambda: self._process_dropped_urls(urls))

            event.acceptProposedAction()

    def _process_dropped_urls(self, urls):
        """Process dropped URLs and open them in tabs."""
        for i, url in enumerate(urls):
            if not url.isLocalFile():
                continue

            file_path = url.toLocalFile()
            if not os.path.exists(file_path):
                continue

            # If there's only one tab and it's empty, use it. Otherwise add tab.
            current_tab = self.tab_widget.currentWidget()
            if current_tab and not current_tab.imagename and i == 0:
                target_tab = current_tab
            else:
                basename = os.path.basename(file_path.rstrip("/"))
                target_tab = self.add_new_tab(basename)

            # Now load the image into the tab
            if os.path.isdir(file_path):
                target_tab.file_type_toggle.setSelectedIndex(
                    0
                )  # CASA (auto-syncs radio_casa_image)
            else:
                target_tab.file_type_toggle.setSelectedIndex(
                    1
                )  # FITS (auto-syncs radio_fits_file)

            # Reset remote mode for target tab when receiving local dropped file
            target_tab._is_remote_mode = False
            target_tab._remote_file_list = []
            target_tab._remote_base_dir = None

            target_tab.imagename = file_path
            target_tab.dir_entry.setText(file_path)

            # Update view
            target_tab.on_visualization_changed(dir_load=True)
            target_tab.update_tab_name_from_path(file_path)

            # Scan directory
            from PyQt5.QtCore import QTimer

            QTimer.singleShot(100, target_tab._scan_directory_files)

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
        self._create_file_menu(menubar)
        self._create_view_menu(menubar)
        tools_menu = self._create_tools_menu(menubar)
        self._create_region_menu(menubar)
        self._create_fitting_menu(menubar)
        self._create_annotations_menu(menubar)
        self._create_presets_menu(menubar)
        self._create_tabs_menu(menubar)
        self._create_download_menu(menubar)
        self._create_help_menu(menubar)
        self._create_lofar_menu(tools_menu)

    def _create_file_menu(self, menubar):
        """File menu: open, remote, export, quit."""
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

    def _create_view_menu(self, menubar):
        """View menu: theme, fullscreen, metadata, scroll zoom."""
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

        metadata_act = QAction("Image Metadata", self)
        metadata_act.setShortcut("Ctrl+M")
        metadata_act.setStatusTip("View detailed metadata for the current image")
        metadata_act.triggered.connect(self.show_metadata)
        view_menu.addAction(metadata_act)

        # Interactive Scroll Zoom toggle action
        self.scroll_zoom_action = QAction(
            "Interactive Scroll Zoom", self, checkable=True
        )
        self.scroll_zoom_action.setChecked(True)
        self.scroll_zoom_action.setStatusTip(
            "Enable/disable zooming with the mouse wheel"
        )
        view_menu.addAction(self.scroll_zoom_action)

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

    def _create_tools_menu(self, menubar):
        """Tools menu (the LOFAR submenu is added by _create_lofar_menu)."""
        tools_menu = menubar.addMenu("&Tools")

        # batch_act = QAction("Batch Processing", self)
        # batch_act.setShortcut("Ctrl+B")
        # batch_act.setStatusTip("Process multiple images in batch mode")
        # batch_act.triggered.connect(self.show_batch_dialog)
        # tools_menu.addAction(batch_act)

        # Add Solar Phase Shift option

        phase_shift_act = QAction("Solar Phase Center Shift", self)
        phase_shift_act.setShortcut("Ctrl+P")
        phase_shift_act.setStatusTip("Shift solar center to phase center")
        phase_shift_act.triggered.connect(self.show_phase_shift_dialog)
        tools_menu.addAction(phase_shift_act)

        # Batch Processing Submenu
        batch_process_submenu = QMenu("Batch Processing", self)
        tools_menu.addMenu(batch_process_submenu)

        # Add Coordinate Transformation option to submenu

        batch_transform_act = QAction("Coordinate Transformation", self)
        batch_transform_act.setStatusTip(
            "Transform multiple files between RA/DEC and helioprojective coordinates"
        )
        batch_transform_act.triggered.connect(
            self.show_coordinate_transformation_dialog
        )
        batch_process_submenu.addAction(batch_transform_act)

        # Add Solar Phase Center Shift option to batch processing submenu
        batch_phase_shift_act = QAction("Solar Phase Center Shift", self)
        batch_phase_shift_act.setStatusTip("Batch shift solar center to phase center")
        batch_phase_shift_act.triggered.connect(
            lambda: self.show_phase_shift_dialog(batch_mode=True)
        )
        batch_process_submenu.addAction(batch_phase_shift_act)

        # Add Unit Conversion option to batch processing submenu
        batch_unit_conv_act = QAction("Unit Conversion", self)
        batch_unit_conv_act.setStatusTip(
            "Batch convert units between Flux Density and Brightness Temperature"
        )
        batch_unit_conv_act.triggered.connect(self.show_unit_conversion_dialog)
        batch_process_submenu.addAction(batch_unit_conv_act)

        # Add Format Conversion option to batch processing submenu

        batch_format_conv_act = QAction("Format Conversion", self)
        batch_format_conv_act.setStatusTip(
            "Batch convert between CASA Images and FITS files"
        )
        batch_format_conv_act.triggered.connect(self.show_format_conversion_dialog)
        batch_process_submenu.addAction(batch_format_conv_act)

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
        dynamic_spectrum_act.triggered.connect(
            lambda: self._launch_pipeline_tool(
                "dynamic_spectrum_viewer", "Dynamic Spectrum Viewer"
            )
        )
        tools_menu.addAction(dynamic_spectrum_act)
        return tools_menu

    def _create_region_menu(self, menubar):
        """Region menu."""
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

    def _create_fitting_menu(self, menubar):
        """Fitting menu."""
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

    def _create_annotations_menu(self, menubar):
        """Annotations menu (text, arrows, shapes)."""
        annot_menu = menubar.addMenu("&Annotations")
        text_act = QAction("Add Text Annotation", self)
        text_act.setShortcut("Ctrl+T")
        text_act.setStatusTip("Add text annotation to the image")
        text_act.triggered.connect(self.show_add_text_annotation_dialog)
        annot_menu.addAction(text_act)

        arrow_act = QAction("Add Arrow Annotation", self)
        arrow_act.setShortcut("Ctrl+A")
        arrow_act.setStatusTip("Add arrow annotation to the image")
        arrow_act.triggered.connect(self.show_add_arrow_annotation_dialog)
        annot_menu.addAction(arrow_act)

        # Shape annotations (circle, ellipse, square, rectangle) + solar radii presets
        from .shape_annotations import setup_shape_menu

        setup_shape_menu(self, annot_menu)

    def _create_presets_menu(self, menubar):
        """Presets menu; entries come from core/presets.py."""
        preset_menu = menubar.addMenu("Presets")

        self.auto_preset_act = QAction(
            "Auto-apply Instrument Presets", self, checkable=True
        )
        self.auto_preset_act.setChecked(True)
        self.auto_preset_act.setStatusTip(
            "Automatically apply colormaps and limits for known instruments (AIA, LASCO, etc.)"
        )
        preset_menu.addAction(self.auto_preset_act)
        preset_menu.addSeparator()

        self._add_preset_actions(preset_menu)

    def _create_tabs_menu(self, menubar):
        """Tabs menu."""
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

    def _create_download_menu(self, menubar):
        """Download menu."""
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

    def _create_help_menu(self, menubar):
        """Help menu."""
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

    def _create_lofar_menu(self, tools_menu):
        """LOFAR Tools submenu at the end of the Tools menu."""
        # Add LOFAR Tools submenu
        tools_menu.addSeparator()
        lofar_menu = QMenu("LOFAR Tools", self)

        # Calibration Table Visualizer
        caltable_act = QAction("Calibration Table Visualizer", self)
        caltable_act.setStatusTip(
            "Visualize bandpass, selfcal, and crossphase calibration tables"
        )
        caltable_act.triggered.connect(
            lambda: self._launch_pipeline_tool(
                "caltable_visualizer", "Calibration Table Visualizer"
            )
        )
        lofar_menu.addAction(caltable_act)

        # Log Viewer
        log_viewer_act = QAction("Log Viewer", self)
        log_viewer_act.setStatusTip("View and filter log files")
        log_viewer_act.triggered.connect(
            lambda: self._launch_pipeline_tool("log_viewer", "Log Viewer")
        )
        lofar_menu.addAction(log_viewer_act)

        # Create Dynamic Spectra
        lofar_menu.addSeparator()
        create_ds_act = QAction("Create Dynamic Spectra...", self)
        create_ds_act.setStatusTip("Create dynamic spectra FITS from MS files")
        create_ds_act.triggered.connect(
            lambda: self._launch_pipeline_tool(
                "create_dynamic_spectra", "Dynamic Spectra Creator"
            )
        )
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
        from .ui.styles import theme_manager
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

        # Shortcut categories
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
        from .ui.styles import theme_manager

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
        elif event.key() == Qt.Key_Escape:
            # Leave a text field so the view keys work again
            current_tab = self.tab_widget.currentWidget()
            if current_tab:
                current_tab.canvas.setFocus()
        elif (
            self.tab_widget.currentWidget() is not None
            and self.tab_widget.currentWidget().handle_view_key(event)
        ):
            pass
        elif event.key() == Qt.Key_F5:
            current_tab = self.tab_widget.currentWidget()
            if current_tab:
                current_tab.apply_auto_range("minmax")
        elif event.key() == Qt.Key_F6:
            current_tab = self.tab_widget.currentWidget()
            if current_tab:
                current_tab.apply_auto_range("p1_99")
        elif event.key() == Qt.Key_F7:
            current_tab = self.tab_widget.currentWidget()
            if current_tab:
                current_tab.apply_auto_range("median_rms")
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        image_prefetcher.shutdown()
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

    def show_phase_shift_dialog(self, batch_mode=False):
        """Show the dialog for shifting solar center to phase center"""
        current_tab = self.tab_widget.currentWidget()

        # Get current image name if available
        imagename = None
        if current_tab and hasattr(current_tab, "imagename") and current_tab.imagename:
            imagename = current_tab.imagename

        # Import and show the dialog
        from .dialogs import PhaseShiftDialog

        dialog = PhaseShiftDialog(self, imagename, batch_mode=batch_mode)

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
            from .video.dialog import VideoCreationDialog

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
            from datetime import datetime

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

    def show_unit_conversion_dialog(self):
        """Show the dialog for batch unit conversion"""
        try:
            from .dialogs import UnitConversionDialog

            dialog = UnitConversionDialog(self)
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
            error_message = f"Error opening unit conversion dialog: {str(e)}"
            print(f"[ERROR] {error_message}")  # Log to console
            QMessageBox.critical(self, "Error", error_message)

    def show_format_conversion_dialog(self):
        """Show the dialog for batch format conversion (CASA \u2194 FITS)"""
        try:
            from .dialogs import FormatConversionDialog

            dialog = FormatConversionDialog(self)
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
            error_message = f"Error opening format conversion dialog: {str(e)}"
            print(f"[ERROR] {error_message}")  # Log to console
            QMessageBox.critical(self, "Error", error_message)

    def show_coordinate_transformation_dialog(self):
        """Show the dialog for batch coordinate transformation"""
        try:
            # Get current image name if available
            current_tab = self.tab_widget.currentWidget()
            current_file = current_tab.imagename if current_tab else None

            # Import and show dialog
            from .dialogs import CoordinateTransformationDialog

            dialog = CoordinateTransformationDialog(self, current_file)
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
            error_message = f"Error opening coordinate transformation dialog: {str(e)}"
            print(f"[ERROR] {error_message}")  # Log to console
            QMessageBox.critical(self, "Error", error_message)



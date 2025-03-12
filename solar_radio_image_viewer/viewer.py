import sys
import os
import numpy as np
import pkg_resources
import matplotlib

matplotlib.use("Qt5Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize, LogNorm
from matplotlib.patches import Ellipse
from matplotlib.figure import Figure
from matplotlib.widgets import RectangleSelector
from matplotlib.backends.backend_qt5agg import (
    FigureCanvasQTAgg as FigureCanvas,
    NavigationToolbar2QT as NavigationToolbar,
)
from matplotlib import rcParams
from scipy.optimize import curve_fit


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
    QInputDialog,
    QMenuBar,
    QMenu,
    QRadioButton,
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QPlainTextEdit,
    QListWidget,
    QSpinBox,
    QCheckBox,
    QGridLayout,
    QStatusBar,
    QGroupBox,
    QToolBar,
    QHeaderView,
    QFormLayout,
    QSplitter,
    QListWidget,
    QListWidgetItem,
    QActionGroup,
    QDoubleSpinBox,
    QToolButton,
    QTabBar,
    QStyle,
    QSizePolicy,
)
from PyQt5.QtCore import Qt, QSettings, QSize, QTimer
from PyQt5.QtGui import QIcon, QColor, QPalette, QPainter, QIntValidator

from .norms import SqrtNorm, AsinhNorm, PowerNorm
from .utils import (
    estimate_rms_near_Sun,
    remove_pixels_away_from_sun,
    get_pixel_values_from_image,
    get_image_metadata,
    twoD_gaussian,
    twoD_elliptical_ring,
    IA,
)
from .styles import STYLESHEET, DARK_PALETTE
from .searchable_combobox import ColormapSelector
from astropy.time import Time
from .solar_data_downloader import launch_gui as launch_downloader_gui

rcParams["axes.linewidth"] = 1.4
rcParams["font.size"] = 12


# For region selection modes
class RegionMode:
    RECTANGLE = 0


class SolarRadioImageTab(QWidget):
    def __init__(self, parent=None, tab_name=""):
        super().__init__(parent)
        self.setObjectName(tab_name)
        self.setStyleSheet(STYLESHEET)

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

        # Initialize RMS box values
        self.current_rms_box = [0, 200, 0, 130]

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
        }

        self.setup_ui()

    def show_status_message(self, message):
        """Helper method to show messages in the status bar"""
        main_window = self.window()
        if main_window and hasattr(main_window, "statusBar"):
            main_window.statusBar().showMessage(message)

    def setup_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(15)

        # Left Control Panel
        control_panel = QWidget()
        control_panel.setFixedWidth(350)
        control_layout = QVBoxLayout(control_panel)
        control_layout.setContentsMargins(0, 0, 0, 0)
        control_layout.setSpacing(15)
        self.create_file_controls(control_layout)
        self.create_display_controls(control_layout)
        main_layout.addWidget(control_panel)

        # Center Figure Panel
        figure_panel = QWidget()
        figure_layout = QVBoxLayout(figure_panel)
        figure_layout.setContentsMargins(0, 0, 0, 0)
        figure_layout.setSpacing(10)
        self.setup_canvas(figure_layout)
        self.setup_figure_toolbar(figure_layout)
        main_layout.addWidget(figure_panel, 1)

        # Right Stats Panel
        stats_panel = QWidget()
        stats_panel.setFixedWidth(350)
        stats_layout = QVBoxLayout(stats_panel)
        stats_layout.setContentsMargins(0, 0, 0, 0)
        stats_layout.setSpacing(15)
        self.create_stats_table(stats_layout)
        self.create_image_stats_table(stats_layout)
        self.create_coord_display(stats_layout)
        main_layout.addWidget(stats_panel)

    def create_file_controls(self, parent_layout):
        group = QGroupBox("Image Selection")
        layout = QVBoxLayout(group)
        layout.setSpacing(10)

        # Add radio buttons for selection type
        selection_type_layout = QHBoxLayout()
        self.selection_type_group = QButtonGroup(self)

        self.radio_casa_image = QRadioButton("CASA Image")
        self.radio_fits_file = QRadioButton("FITS File")
        self.radio_casa_image.setChecked(True)  # Default to CASA image

        self.selection_type_group.addButton(self.radio_casa_image)
        self.selection_type_group.addButton(self.radio_fits_file)

        selection_type_layout.addWidget(self.radio_casa_image)
        selection_type_layout.addWidget(self.radio_fits_file)
        selection_type_layout.addStretch()

        layout.addLayout(selection_type_layout)

        file_layout = QHBoxLayout()
        file_layout.setSpacing(8)
        self.dir_entry = QLineEdit()
        self.dir_entry.setPlaceholderText("Select image directory or FITS file...")
        browse_btn = QPushButton()
        browse_btn.setObjectName("IconOnlyNBGButton")
        browse_btn.setIcon(
            QIcon(
                pkg_resources.resource_filename(
                    "solar_radio_image_viewer", "assets/browse.png"
                )
            )
        )
        browse_btn.setIconSize(QSize(32, 32))
        browse_btn.setToolTip("Browse")
        browse_btn.setFixedSize(32, 32)
        browse_btn.clicked.connect(self.select_file_or_directory)
        browse_btn.setStyleSheet(
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
        }
        QPushButton:hover {
            background-color: #484848;
        }
        QPushButton:pressed {
            background-color: #303030;
        }
        """
        )
        file_layout.addWidget(self.dir_entry, 1)
        file_layout.addWidget(browse_btn)
        layout.addLayout(file_layout)
        stokes_layout = QFormLayout()
        stokes_layout.setSpacing(10)
        stokes_layout.setVerticalSpacing(10)
        stokes_layout.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.stokes_combo = QComboBox()
        self.stokes_combo.addItems(
            ["I", "Q", "U", "V", "L", "Lfrac", "Vfrac", "Q/I", "U/I", "U/V"]
        )
        self.stokes_combo.currentTextChanged.connect(self.on_stokes_changed)
        stokes_layout.addRow("Stokes Parameter:", self.stokes_combo)
        self.threshold_entry = QLineEdit("10")
        stokes_layout.addRow("Threshold (σ):", self.threshold_entry)

        # Add RMS Box Settings button
        rms_box_btn = QPushButton("RMS Box Settings...")
        rms_box_btn.clicked.connect(self.show_rms_box_dialog)
        stokes_layout.addRow("", rms_box_btn)

        layout.addLayout(stokes_layout)
        parent_layout.addWidget(group)

    def create_display_controls(self, parent_layout):
        group = QGroupBox("Display Settings")
        main_layout = QVBoxLayout(group)

        # Basic display settings
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
        self.cmap_combo.colormapSelected.connect(self.on_visualization_changed)
        form_layout.addRow("Colormap:", self.cmap_combo)

        self.stretch_combo = QComboBox()
        self.stretch_combo.addItems(["linear", "sqrt", "log", "arcsinh", "power"])
        self.stretch_combo.setCurrentText("power")
        self.stretch_combo.currentIndexChanged.connect(self.on_stretch_changed)
        form_layout.addRow("Stretch:", self.stretch_combo)
        main_layout.addLayout(form_layout)

        # Overlays subgroup
        overlays_group = QGroupBox("Overlays")
        overlays_layout = QVBoxLayout(overlays_group)

        # Grid layout for all overlay controls
        grid_layout = QGridLayout()
        grid_layout.setSpacing(10)

        # Basic display options - left side
        self.show_beam_checkbox = QCheckBox("Show Beam")
        self.show_beam_checkbox.setChecked(True)
        self.show_beam_checkbox.stateChanged.connect(self.on_checkbox_changed)

        # Basic display options - right side
        self.show_grid_checkbox = QCheckBox("Show Grid")
        self.show_grid_checkbox.setChecked(False)
        self.show_grid_checkbox.stateChanged.connect(self.on_checkbox_changed)

        # Solar disk controls with settings button
        self.show_solar_disk_checkbox = QCheckBox("Solar Disk")
        self.show_solar_disk_checkbox.stateChanged.connect(self.on_checkbox_changed)
        self.solar_disk_center_button = QPushButton()
        self.solar_disk_center_button.setObjectName("IconOnlyNBGButton")
        self.solar_disk_center_button.setIcon(
            QIcon(
                pkg_resources.resource_filename(
                    "solar_radio_image_viewer", "assets/settings.png"
                )
            )
        )
        self.solar_disk_center_button.setIconSize(QSize(24, 24))
        self.solar_disk_center_button.setToolTip(
            "Customize Solar Disk (center, size, appearance)"
        )
        self.solar_disk_center_button.setFixedSize(32, 32)
        self.solar_disk_center_button.clicked.connect(self.set_solar_disk_center)

        # Vertical line separator
        vline_1 = QFrame()
        vline_1.setFrameShape(QFrame.VLine)
        vline_1.setFrameShadow(QFrame.Sunken)
        vline_1.setStyleSheet(
            """
            QFrame {
                color: transparent;
                border: none;
                background-color: transparent;
                width: 2px;
            }
        """
        )
        vline_2 = QFrame()
        vline_2.setFrameShape(QFrame.VLine)
        vline_2.setFrameShadow(QFrame.Sunken)
        vline_2.setStyleSheet(
            """
            QFrame {
                color: transparent;
                border: none;
                background-color: transparent;
                width: 2px;
            }
        """
        )

        # Contour controls with settings button
        self.show_contours_checkbox = QCheckBox("Contours")
        self.show_contours_checkbox.setChecked(False)
        self.show_contours_checkbox.stateChanged.connect(self.on_checkbox_changed)
        self.contour_settings_button = QPushButton()
        self.contour_settings_button.setObjectName("IconOnlyNBGButton")
        self.contour_settings_button.setIcon(
            QIcon(
                pkg_resources.resource_filename(
                    "solar_radio_image_viewer", "assets/settings.png"
                )
            )
        )
        self.contour_settings_button.setIconSize(QSize(24, 24))
        self.contour_settings_button.setToolTip("Contour Settings")
        self.contour_settings_button.setFixedSize(32, 32)
        self.contour_settings_button.clicked.connect(self.show_contour_settings)

        # Add widgets to grid layout
        # Row 0: Basic display options
        grid_layout.addWidget(self.show_beam_checkbox, 0, 0)
        grid_layout.addWidget(vline_1, 0, 2)
        grid_layout.addWidget(self.show_grid_checkbox, 0, 3)

        # Row 1: Solar disk and Contours
        grid_layout.addWidget(self.show_solar_disk_checkbox, 1, 0)
        grid_layout.addWidget(self.solar_disk_center_button, 1, 1)
        grid_layout.addWidget(vline_2, 1, 2)  # Same vertical line spans both rows
        grid_layout.addWidget(self.show_contours_checkbox, 1, 3)
        grid_layout.addWidget(self.contour_settings_button, 1, 4)

        # Set column stretch to ensure proper spacing
        grid_layout.setColumnStretch(0, 1)  # Left side checkboxes
        grid_layout.setColumnStretch(1, 0)  # Left side button
        grid_layout.setColumnStretch(2, 0)  # Vertical line
        grid_layout.setColumnStretch(3, 1)  # Right side checkboxes
        grid_layout.setColumnStretch(4, 0)  # Right side button

        overlays_layout.addLayout(grid_layout)
        main_layout.addWidget(overlays_group)

        # Intensity Range subgroup
        intensity_group = QGroupBox("Intensity Range")
        intensity_layout = QVBoxLayout(intensity_group)

        # Min/Max range
        range_layout = QHBoxLayout()
        self.vmin_entry = QLineEdit("0.0")
        self.vmin_entry.editingFinished.connect(self.on_visualization_changed)
        self.vmax_entry = QLineEdit("1.0")
        self.vmax_entry.editingFinished.connect(self.on_visualization_changed)
        range_layout.addWidget(QLabel("Min:"))
        range_layout.addWidget(self.vmin_entry)
        range_layout.addWidget(QLabel("Max:"))
        range_layout.addWidget(self.vmax_entry)
        intensity_layout.addLayout(range_layout)

        # Gamma control
        gamma_layout = QHBoxLayout()
        self.gamma_slider = QSlider(Qt.Horizontal)
        self.gamma_slider.setRange(1, 100)
        self.gamma_slider.setValue(10)
        self.gamma_slider.valueChanged.connect(self.update_gamma_value)
        self.gamma_entry = QLineEdit("1.0")
        self.gamma_entry.setFixedWidth(60)
        self.gamma_entry.editingFinished.connect(self.update_gamma_slider)
        gamma_layout.addWidget(QLabel("Gamma:"))
        gamma_layout.addWidget(self.gamma_slider)
        gamma_layout.addWidget(self.gamma_entry)
        intensity_layout.addLayout(gamma_layout)

        # Preset buttons
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
        intensity_layout.addLayout(preset_layout)

        main_layout.addWidget(intensity_group)

        # Update Display button
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
        main_layout.addWidget(self.plot_button)

        parent_layout.addWidget(group)

    def create_range_controls(self, parent_layout):
        group = QGroupBox("Intensity Range")
        layout = QVBoxLayout(group)
        range_layout = QHBoxLayout()
        self.vmin_entry = QLineEdit("0.0")
        self.vmin_entry.editingFinished.connect(self.on_visualization_changed)
        self.vmax_entry = QLineEdit("1.0")
        self.vmax_entry.editingFinished.connect(self.on_visualization_changed)
        range_layout.addWidget(QLabel("Min:"))
        range_layout.addWidget(self.vmin_entry)
        range_layout.addWidget(QLabel("Max:"))
        range_layout.addWidget(self.vmax_entry)
        gamma_layout = QHBoxLayout()
        self.gamma_slider = QSlider(Qt.Horizontal)
        self.gamma_slider.setRange(1, 100)
        self.gamma_slider.setValue(10)
        self.gamma_slider.valueChanged.connect(self.update_gamma_value)
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
        self.zoom_in_button.setIcon(
            QIcon(
                pkg_resources.resource_filename(
                    "solar_radio_image_viewer", "assets/zoom_in.png"
                )
            )
        )
        self.zoom_in_button.setIconSize(QSize(24, 24))
        self.zoom_in_button.setToolTip("Zoom In")
        self.zoom_in_button.setFixedSize(32, 32)
        self.zoom_in_button.clicked.connect(self.zoom_in)
        self.zoom_out_button = QPushButton()
        self.zoom_out_button.setObjectName("IconOnlyButton")
        self.zoom_out_button.setIcon(
            QIcon(
                pkg_resources.resource_filename(
                    "solar_radio_image_viewer", "assets/zoom_out.png"
                )
            )
        )
        self.zoom_out_button.setIconSize(QSize(24, 24))
        self.zoom_out_button.setToolTip("Zoom Out")
        self.zoom_out_button.setFixedSize(32, 32)
        self.zoom_out_button.clicked.connect(self.zoom_out)
        self.reset_view_button = QPushButton()
        self.reset_view_button.setObjectName("IconOnlyButton")
        self.reset_view_button.setIcon(
            QIcon(
                pkg_resources.resource_filename(
                    "solar_radio_image_viewer", "assets/reset.png"
                )
            )
        )
        self.reset_view_button.setIconSize(QSize(24, 24))
        self.reset_view_button.setToolTip("Reset View")
        self.reset_view_button.setFixedSize(32, 32)
        self.reset_view_button.clicked.connect(self.reset_view)
        self.reset_view_button.setToolTip("Reset View")
        self.zoom_60arcmin_button = QPushButton("1°×1°")
        self.zoom_60arcmin_button.clicked.connect(self.zoom_60arcmin)
        self.zoom_60arcmin_button.setToolTip("1°×1° Zoom")
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
        toolbar.setIconSize(QSize(24, 24))
        action_group = QActionGroup(self)
        self.rect_action = QAction(
            QIcon(
                pkg_resources.resource_filename(
                    "solar_radio_image_viewer", "assets/rectangle_selection.png"
                )
            ),
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
        self.zoom_in_action = QAction(
            QIcon(
                pkg_resources.resource_filename(
                    "solar_radio_image_viewer", "assets/zoom_in.png"
                )
            ),
            "",
            self,
        )
        self.zoom_in_action.setToolTip("Zoom In")
        self.zoom_in_action.triggered.connect(self.zoom_in)
        self.zoom_out_action = QAction(
            QIcon(
                pkg_resources.resource_filename(
                    "solar_radio_image_viewer", "assets/zoom_out.png"
                )
            ),
            "",
            self,
        )
        self.zoom_out_action.setToolTip("Zoom Out")
        self.zoom_out_action.triggered.connect(self.zoom_out)
        self.reset_view_action = QAction(
            QIcon(
                pkg_resources.resource_filename(
                    "solar_radio_image_viewer", "assets/reset.png"
                )
            ),
            "",
            self,
        )
        self.reset_view_action.setToolTip("Reset View")
        self.reset_view_action.triggered.connect(self.reset_view)
        self.zoom_60arcmin_action = QAction(
            QIcon(
                pkg_resources.resource_filename(
                    "solar_radio_image_viewer", "assets/zoom_60arcmin.png"
                )
            ),
            "",
            self,
        )
        self.zoom_60arcmin_action.setToolTip("1°×1° Zoom")
        self.zoom_60arcmin_action.triggered.connect(self.zoom_60arcmin)
        toolbar.addActions(
            [
                self.rect_action,
                self.zoom_in_action,
                self.zoom_out_action,
                self.reset_view_action,
                self.zoom_60arcmin_action,
            ]
        )

        # Add a spacer to push the helioprojective button to the right
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)

        # Add helioprojective viewer button
        hpc_btn = QPushButton("Helioprojective")
        hpc_btn.setToolTip("Open in Helioprojective Viewer")
        hpc_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #2196F3;
                color: white;
                border: none;
                padding: 5px 10px;
                border-radius: 4px;
                min-width: 100px;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
            QPushButton:pressed {
                background-color: #0D47A1;
            }
            QPushButton:disabled {
                background-color: #BDBDBD;
            }
        """
        )
        hpc_btn.clicked.connect(self.launch_helioprojective_viewer)
        toolbar.addWidget(hpc_btn)

        parent_layout.addWidget(toolbar)

    def launch_helioprojective_viewer(self):
        """Launch the helioprojective viewer with the current image"""
        if not hasattr(self, "imagename") or not self.imagename:
            self.show_status_message("No image loaded")
            return

        try:
            # Import the viewer class
            from .helioprojective_viewer import HelioProjectiveViewer
            from PyQt5.QtWidgets import QApplication

            # Create and show the viewer
            viewer = HelioProjectiveViewer(
                imagename=self.imagename,
                stokes=(
                    self.stokes_combo.currentText()
                    if hasattr(self, "stokes_combo")
                    else "I"
                ),
                threshold=10,  # Default threshold
                rms_box=(0, 200, 0, 130),  # Default RMS box
                parent=self,
            )
            viewer.show()

            # Keep a reference to prevent garbage collection
            if not hasattr(self, "_hpc_viewers"):
                self._hpc_viewers = []
            self._hpc_viewers.append(viewer)

            self.show_status_message(
                f"Launched helioprojective viewer for {self.imagename}"
            )
        except Exception as e:
            import traceback

            traceback.print_exc()
            error_msg = f"Error launching helioprojective viewer: {str(e)}"
            self.show_status_message(error_msg)
            print(error_msg)

    def create_stats_table(self, parent_layout):
        group = QGroupBox("Region Statistics")
        layout = QVBoxLayout(group)
        self.info_label = QLabel("No selection")
        self.info_label.setStyleSheet(
            "color: #BBB; font-style: italic; font-size: 11pt;"
        )
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)
        self.stats_table = QTableWidget(6, 2)
        self.stats_table.setHorizontalHeaderLabels(["Metric", "Value"])
        self.stats_table.verticalHeader().setVisible(False)
        self.stats_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.stats_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(6):
            self.stats_table.setRowHeight(i, 30)
        self.stats_table.setColumnWidth(1, 180)
        headers = ["Min", "Max", "Mean", "Std Dev", "Sum", "RMS"]
        for row, label in enumerate(headers):
            self.stats_table.setItem(row, 0, QTableWidgetItem(label))
            self.stats_table.setItem(row, 1, QTableWidgetItem("−"))
            self.stats_table.item(row, 1).setTextAlignment(
                Qt.AlignRight | Qt.AlignVCenter
            )
        layout.addWidget(self.stats_table)
        parent_layout.addWidget(group)

    def create_image_stats_table(self, parent_layout):
        group = QGroupBox("Image Statistics")
        layout = QVBoxLayout(group)

        self.image_info_label = QLabel("Full image statistics")
        self.image_info_label.setStyleSheet(
            "color: #BBB; font-style: italic; font-size: 11pt;"
        )
        self.image_info_label.setWordWrap(True)
        layout.addWidget(self.image_info_label)

        self.image_stats_table = QTableWidget(6, 2)
        self.image_stats_table.setHorizontalHeaderLabels(["Metric", "Value"])
        self.image_stats_table.verticalHeader().setVisible(False)
        self.image_stats_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.image_stats_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch
        )
        for i in range(6):
            self.image_stats_table.setRowHeight(i, 30)
        self.image_stats_table.setColumnWidth(1, 180)

        headers = ["Max", "Min", "RMS", "Mean (RMS box)", "Pos. DR", "Neg. DR"]
        for row, label in enumerate(headers):
            self.image_stats_table.setItem(row, 0, QTableWidgetItem(label))
            self.image_stats_table.setItem(row, 1, QTableWidgetItem("−"))
            self.image_stats_table.item(row, 1).setTextAlignment(
                Qt.AlignRight | Qt.AlignVCenter
            )

        layout.addWidget(self.image_stats_table)
        parent_layout.addWidget(group)

    def create_coord_display(self, parent_layout):
        group = QGroupBox("Cursor Position")
        layout = QVBoxLayout(group)
        self.coord_label = QLabel("RA: −\nDEC: −")
        self.coord_label.setAlignment(Qt.AlignCenter)
        self.coord_label.setStyleSheet("font-family: monospace; font-size: 12pt;")
        self.coord_label.setMinimumHeight(70)
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

    def select_file_or_directory(self):
        import time

        if self.radio_casa_image.isChecked():
            # Select CASA image directory
            directory = QFileDialog.getExistingDirectory(
                self,
                caption="Select an image",
                options=QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
            )
            if directory:
                start_time = time.time()
                self.imagename = directory
                self.dir_entry.setText(directory)
                self.on_visualization_changed(dir_load=True)
                self.reset_view(show_status_message=False)
                # self.auto_minmax()
                self.update_tab_name_from_path(directory)  # Update tab name
                self.show_status_message(
                    f"Loaded {directory} in {time.time() - start_time:.2f} seconds"
                )
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
                self.imagename = file_path
                self.dir_entry.setText(file_path)
                self.on_visualization_changed(dir_load=True)
                self.reset_view(show_status_message=False)
                # self.auto_minmax()
                self.update_tab_name_from_path(file_path)  # Update tab name
                self.show_status_message(
                    f"Loaded {file_path} in {time.time() - start_time:.2f} seconds"
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
        self._plot_timer.start(10)  # 10ms delay

    def plot_data(self):
        self.on_visualization_changed()

    def on_visualization_changed(self, colormap_name=None, dir_load=False):
        if not hasattr(self, "imagename") or not self.imagename:
            QMessageBox.warning(
                self, "No Image", "Please select a CASA image directory first!"
            )
            return

        main_window = self.parent()
        if main_window and hasattr(main_window, "statusBar"):
            main_window.statusBar().showMessage("Loading data...")
            QApplication.processEvents()

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
                        self.cmap_combo.setCurrentText("viridis")

            self.load_data(self.imagename, stokes, threshold)
            if dir_load:
                vmin_val = float(np.nanmin(self.current_image_data))
                vmax_val = float(np.nanmax(self.current_image_data))
                self.set_range(vmin_val, vmax_val)
                self.plot_image(vmin_val, vmax_val, stretch, cmap, gamma)
                print(f"Plotting image with vmin={vmin_val}, vmax={vmax_val}")
            elif vmin_val is None or vmax_val is None:
                self.auto_minmax()
            else:
                self.plot_image(vmin_val, vmax_val, stretch, cmap, gamma)

            if main_window and hasattr(main_window, "statusBar"):
                img_name = os.path.basename(self.imagename)
                main_window.statusBar().showMessage(
                    f"Loaded {img_name}, Stokes {stokes}, Threshold {threshold}"
                )
        except Exception as e:
            if main_window and hasattr(main_window, "statusBar"):
                main_window.statusBar().showMessage(f"Error: {str(e)}")
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
        if main_window and hasattr(main_window, "statusBar"):
            main_window.statusBar().showMessage(
                f"Set display range to min/max: [{dmin:.4g}, {dmax:.4g}]"
            )

    def auto_percentile(self):
        if self.current_image_data is None:
            return

        data = self.current_image_data
        p1 = np.percentile(data, 1)
        p99 = np.percentile(data, 99)
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
        if main_window and hasattr(main_window, "statusBar"):
            main_window.statusBar().showMessage(
                f"Set display range to 1-99 percentile: [{p1:.4g}, {p99:.4g}]"
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
        if main_window and hasattr(main_window, "statusBar"):
            main_window.statusBar().showMessage(
                f"Set display range to median±3×RMS: [{low:.4g}, {high:.4g}]"
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
        gamma = self.gamma_slider.value() / 10.0
        self.gamma_entry.setText(f"{gamma:.1f}")

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

    def update_gamma_slider(self):
        try:
            gamma = float(self.gamma_entry.text())
            if 0.1 <= gamma <= 10.0:
                self.gamma_slider.blockSignals(True)
                self.gamma_slider.setValue(int(gamma * 10))
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
            self.gamma_slider.setValue(10)

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
        if main_window and hasattr(main_window, "statusBar"):
            main_window.statusBar().showMessage(f"Changed stretch to {stretch}")

    def update_gamma_slider_state(self):
        is_power = self.stretch_combo.currentText() == "power"
        self.gamma_slider.setEnabled(is_power)
        self.gamma_entry.setEnabled(is_power)

        if is_power:
            self.gamma_slider.setStyleSheet("")
            self.gamma_entry.setStyleSheet("")
        else:
            self.gamma_slider.setStyleSheet("background-color: #555555;")
            self.gamma_entry.setStyleSheet("background-color: #555555;")

    def show_roi_stats(self, roi, ra_dec_info=""):
        if roi.size == 0:
            return

        rmin = np.nanmin(roi)
        rmax = np.nanmax(roi)
        rmean = np.nanmean(roi)
        rstd = np.nanstd(roi)
        rsum = np.nansum(roi)
        rrms = np.sqrt(np.nanmean(roi**2))

        self.info_label.setText(f"ROI Stats: {roi.size} pixels{ra_dec_info}")

        stats_values = [rmin, rmax, rmean, rstd, rsum, rrms]
        for i, val in enumerate(stats_values):
            self.stats_table.setItem(i, 1, QTableWidgetItem(f"{val:.6g}"))

        main_window = self.parent()
        if main_window and hasattr(main_window, "statusBar"):
            main_window.statusBar().showMessage(
                f"ROI selected: {roi.size} pixels, Mean={rmean:.4g}, Sum={rsum:.4g}, RMS={rrms:.4g}"
            )

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
        positive_DR = dmax / drms
        negative_DR = dmin / drms

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
            f"Full image ({h}×{w} pixels) - RMS box: {rms_box_percent:.1f}% of image"
        )

        return dmax, dmin, drms, dmean_rms_box, positive_DR, negative_DR

    def set_region_mode(self, mode_id):
        self.region_mode = RegionMode.RECTANGLE
        self.plot_image()

    def load_data(self, imagename, stokes, threshold):
        import time

        start_time = time.time()

        # Use the current RMS box when loading data
        from .utils import get_pixel_values_from_image

        pix, csys, psf = get_pixel_values_from_image(
            imagename, stokes, threshold, rms_box=tuple(self.current_rms_box)
        )

        self.current_image_data = pix
        self.current_wcs = csys
        self.psf = psf

        if pix is not None:
            height, width = pix.shape
            self.solar_disk_center = (width // 2, height // 2)

            # Make sure RMS box is within image bounds
            if self.current_rms_box[1] > height:
                self.current_rms_box[1] = height
            if self.current_rms_box[3] > width:
                self.current_rms_box[3] = width

            # Update image stats when data is loaded
            self.show_image_stats(rms_box=self.current_rms_box)

        if not self.psf:
            self.show_beam_checkbox.setChecked(False)
            self.show_beam_checkbox.setEnabled(False)
        else:
            self.show_beam_checkbox.setEnabled(True)

        # self.plot_image()
        # self.schedule_plot()
        print(f"Time taken to load data: {time.time() - start_time:.2f} seconds")

    def plot_image(
        self, vmin_val=None, vmax_val=None, stretch="linear", cmap="viridis", gamma=1.0
    ):
        import time

        fits_flag = False
        if self.imagename.endswith(".fits") or self.imagename.endswith(".fts"):
            from astropy.io import fits

            fits_flag = True
            hdul = fits.open(self.imagename)
            header = hdul[0].header

        try:
            ia_tool = IA()
            ia_tool.open(self.imagename)
            csys = ia_tool.coordsys()
            summary = ia_tool.summary()
            csys_record = ia_tool.coordsys().torecord()
            ia_tool.close()
        except Exception as e:
            print(f"Error getting image metadata: {e}")
            return

        start_time = time.time()
        if self.current_image_data is None:
            return

        data = self.current_image_data
        n_dims = len(data.shape)

        # Cache the transposed image if the current image hasn't changed.
        if not hasattr(self, "_cached_data_id") or self._cached_data_id != id(data):
            self._cached_transposed = data.transpose()
            self._cached_data_id = id(data)
        transposed_data = self._cached_transposed

        # Remove the call to show_image_stats here since we only want to show stats when data is loaded
        # self.show_image_stats()

        stored_xlim = None
        stored_ylim = None
        if self.figure.axes:
            try:
                stored_xlim = self.figure.axes[0].get_xlim()
                stored_ylim = self.figure.axes[0].get_ylim()
            except Exception:
                stored_xlim = None
                stored_ylim = None

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
        else:
            norm = Normalize(vmin=vmin_val, vmax=vmax_val)

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
                                print(f"Error getting projection: {e}")
                                self._cached_wcs_obj = None
                        elif (csys.projection()["type"] == "SIN") and (
                            "Right Ascension" in summary["axisnames"]
                        ):
                            print("SIN projection")
                            self._cached_wcs_obj.wcs.ctype = [
                                "RA---SIN",
                                "DEC--SIN",
                            ]
                        elif (csys.projection()["type"] == "TAN") and (
                            "Right Ascension" in summary["axisnames"]
                        ):
                            print("TAN projection")
                            self._cached_wcs_obj.wcs.ctype = [
                                "RA---TAN",
                                "DEC--TAN",
                            ]

                        else:
                            print(f"Error getting projection: {e}")
                            self._cached_wcs_obj = None

                    except Exception as e:
                        print(f"Error getting projection: {e}")
                        self._cached_wcs_obj = None
                    self._cached_wcs_id = id(self.current_wcs)
                except Exception as e:
                    print(f"Error creating WCS: {e}")
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
                    interpolation="none",
                )
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
                    ax.coords.grid(True, color="white", alpha=0.5, linestyle="--")
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
                print(f"Error setting up WCS axes: {e}")
                ax = self.figure.add_subplot(111)
                im = ax.imshow(
                    transposed_data,
                    origin="lower",
                    cmap=cmap,
                    norm=norm,
                    interpolation="none",
                )
                ax.set_xlabel("Pixel X")
                ax.set_ylabel("Pixel Y")
        else:
            ax = self.figure.add_subplot(111)
            im = ax.imshow(
                transposed_data,
                origin="lower",
                cmap=cmap,
                norm=norm,
                interpolation="none",
            )
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
                        image_time = header.get("DATE-OBS")
                        if header["TELESCOP"] == "SOHO":
                            image_time = f"{image_time}T{header['TIME-OBS']}"
                        temp_flag = True
                    except Exception as e:
                        print(f"Error getting image time: {e}")
                        image_time = None

                if "spectral2" in csys_record:
                    spectral2 = csys_record["spectral2"]
                    wcs = spectral2.get("wcs", {})
                    frequency_ref = wcs.get("crval", None)
                    frequency_unit = spectral2.get("unit", None)
                    if frequency_unit == "Hz":
                        image_freq = f"{frequency_ref * 1e-6:.2f} MHz"
                    else:
                        image_freq = f"{frequency_ref:.2f} {frequency_unit}"
                else:
                    image_freq = None

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
                    if header["TELESCOP"] == "SOHO" and header["INSTRUME"] == "LASCO":
                        title = f"{image_time} | {header['TELESCOP']} {header['INSTRUME']} {header['DETECTOR']}"
                    elif header["TELESCOP"] == "SOHO" and header["INSTRUME"] == "EIT":
                        title = (
                            f"{image_time} | {header['TELESCOP']} {header['INSTRUME']}"
                        )
                    elif header["TELESCOP"] == "SOHO" and header["INSTRUME"] == "MDI":
                        title = (
                            f"{image_time} | {header['TELESCOP']} {header['INSTRUME']}"
                        )
                    elif header["TELESCOP"] == "SDO/AIA":
                        title = f"{image_time} | {header['TELESCOP']} {header['WAVELNTH']} $\\AA$"
                    elif header["TELESCOP"] == "SDO/HMI":
                        title = f"{image_time} | {header['TELESCOP']}"

                    elif image_time is not None and image_freq is not None:
                        title = f"Time: {image_time} | Freq: {image_freq}"

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
                print(f"Error getting title: {e}")
                title = (
                    os.path.basename(self.imagename) if self.imagename else "No Image"
                )
                ax.set_title(title)

            # Format the time and frequency as a title

        self.figure.colorbar(im, ax=ax, label="Data")

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
                    edgecolor="black",
                    linewidth=1.5,
                    facecolor="white",
                    alpha=0.4,
                )
                ax.add_patch(ellipse)
                self.beam_properties = {
                    "major_pix": major_pix,
                    "minor_pix": minor_pix,
                    "pa_deg": pa_deg,
                    "margin": 0.05,
                }
            except Exception as e:
                print(f"Error drawing beam: {e}")

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
                print(f"Error drawing solar disk: {e}")

        # Draw contours if enabled
        if (
            hasattr(self, "show_contours_checkbox")
            and self.show_contours_checkbox.isChecked()
        ):
            self.draw_contours(ax)

        self.init_region_editor(ax)

        # Instead of immediate draw, use draw_idle to coalesce multiple calls
        self.canvas.draw_idle()

        print(f"Time taken to plot image: {time.time() - start_time:.2f} seconds")

    def _update_beam_position(self, ax):
        if not hasattr(self, "beam_properties") or not self.beam_properties:
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
            edgecolor="black",
            facecolor="white",
            linewidth=1.5,
            alpha=0.4,
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
                print(f"Error drawing solar disk: {e}")

    def on_stokes_changed(self, stokes):
        if not self.imagename:
            return

        main_window = self.parent()
        if main_window and hasattr(main_window, "statusBar"):
            main_window.statusBar().showMessage(f"Loading data for Stokes {stokes}...")
            QApplication.processEvents()

        try:
            threshold = float(self.threshold_entry.text())
        except (ValueError, AttributeError):
            threshold = 10.0
            if hasattr(self, "threshold_entry"):
                self.threshold_entry.setText("10.0")

        self.load_data(self.imagename, stokes, threshold)

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

            if main_window and hasattr(main_window, "statusBar"):
                main_window.statusBar().showMessage(
                    f"Stokes changed to {stokes}, display range: [{dmin:.4g}, {dmax:.4g}]"
                )

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

    def add_text_annotation(self, x, y, text):
        ax = self.figure.gca()
        ax.text(x, y, text, color="yellow", fontsize=10)
        self.canvas.draw()

    def add_arrow_annotation(self, x1, y1, x2, y2):
        ax = self.figure.gca()
        ax.arrow(x1, y1, x2 - x1, y2 - y1, color="red", width=0.3)
        self.canvas.draw()

    def set_solar_disk_center(self):
        if self.current_image_data is None:
            QMessageBox.warning(self, "No Image", "Please load an image first.")
            return

        height, width = self.current_image_data.shape

        dialog = QDialog(self)
        dialog.setWindowTitle("Solar Disk Settings")
        dialog.setMinimumWidth(400)  # Set minimum width to prevent text cutoff
        layout = QVBoxLayout(dialog)

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

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        if dialog.exec_() == QDialog.Accepted:
            self.solar_disk_center = (x_spinbox.value(), y_spinbox.value())
            self.solar_disk_diameter_arcmin = float(diameter_spinbox.value())

            # Update style properties
            self.solar_disk_style["color"] = color_combo.currentText()
            self.solar_disk_style["linestyle"] = linestyle_combo.currentData()
            self.solar_disk_style["linewidth"] = linewidth_spinbox.value()
            self.solar_disk_style["alpha"] = alpha_spinbox.value()
            self.solar_disk_style["show_center"] = show_center_checkbox.isChecked()

            self.schedule_plot()
            self.show_status_message("Solar disk settings updated")

    def zoom_in(self):
        if self.current_image_data is None:
            return

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
        self.show_status_message("Zoomed in")

    def zoom_out(self):
        if self.current_image_data is None:
            return

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
        self.show_status_message("Zoomed out")

    def zoom_60arcmin(self):
        if self.current_image_data is None or self.current_wcs is None:
            return

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
            print(f"Error in zoom_60arcmin: {e}")
            self.show_status_message(f"Error in zoom_60arcmin: {e}")

    def init_region_editor(self, ax):
        if self.roi_selector:
            self.roi_selector.disconnect_events()
            self.roi_selector = None

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
                    f"\nRegion Center: RA={center_coord.ra.to_string(unit=u.hour, sep=':', precision=2)}, "
                    f"Dec={center_coord.dec.to_string(sep=':', precision=2)}"
                    f"\nAngular Size: {width.to(u.arcsec):.2f} × {height.to(u.arcsec):.2f}"
                    f"\nCorners: "
                    f"\n  Bottom-Left: RA={coord1.ra.to_string(unit=u.hour, sep=':', precision=2)}, "
                    f"Dec={coord1.dec.to_string(sep=':', precision=2)}"
                    f"\n  Top-Right: RA={coord2.ra.to_string(unit=u.hour, sep=':', precision=2)}, "
                    f"Dec={coord2.dec.to_string(sep=':', precision=2)}"
                )
            except Exception as e:
                ra_dec_info = f"\nRA/Dec conversion error: {str(e)}"

        self.show_roi_stats(roi, ra_dec_info)

    def on_mouse_move(self, event):
        if not event.inaxes or self.current_image_data is None:
            return

        x, y = int(event.xdata), int(event.ydata)

        try:
            value = self.current_image_data[x, y]
            pixel_info = f"<b>Pixel:</b> X={x}, Y={y}<br><b>Value:</b> {value:.3g}"
        except (IndexError, TypeError):
            pixel_info = f"<b>Pixel:</b> X={x}, Y={y}"

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
                w.wcs.ctype = ["RA---SIN", "DEC--SIN"]

                ra, dec = w.wcs_pix2world(x, y, 0)
                coord = SkyCoord(ra=ra * u.degree, dec=dec * u.degree)
                ra_str = coord.ra.to_string(unit=u.hour, sep=":", precision=2)
                dec_str = coord.dec.to_string(sep=":", precision=2)

                coord_info = f"{pixel_info}<br><b>World:</b> RA={ra_str}, Dec={dec_str}"
                self.coord_label.setText(coord_info)
            except Exception as e:
                self.coord_label.setText(f"{pixel_info}<br><b>WCS Error:</b> {str(e)}")
        else:
            self.coord_label.setText(pixel_info)

    def setup_canvas(self, parent_layout):
        self.figure = Figure(figsize=(5, 5), dpi=100)
        self.canvas = FigureCanvas(self.figure)
        self.nav_toolbar = NavigationToolbar(self.canvas, self)
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
        from .dialogs import ContourSettingsDialog

        dialog = ContourSettingsDialog(self, self.contour_settings)
        if dialog.exec_() == QDialog.Accepted:
            self.contour_settings = dialog.get_settings()
            if self.show_contours_checkbox.isChecked():
                self.load_contour_data()
                self.on_visualization_changed()

    def load_contour_data(self):
        try:
            rms_box = (0, 200, 0, 130)
            if not self.contour_settings.get("use_default_rms_region", True):
                rms_box = self.contour_settings.get("rms_box", rms_box)

            contour_csys = None
            if self.contour_settings["source"] == "same":
                if self.imagename:
                    stokes = self.contour_settings["stokes"]
                    threshold = 5.0

                    if stokes in ["I", "Q", "U", "V"]:
                        pix, contour_csys, _ = get_pixel_values_from_image(
                            self.imagename, stokes, threshold, rms_box
                        )
                        self.contour_settings["contour_data"] = pix
                    elif stokes in ["Q/I", "U/I", "V/I"]:
                        numerator_stokes = stokes.split("/")[0]
                        numerator_pix, contour_csys, _ = get_pixel_values_from_image(
                            self.imagename, numerator_stokes, threshold, rms_box
                        )
                        denominator_pix, contour_csys, _ = get_pixel_values_from_image(
                            self.imagename, "I", threshold, rms_box
                        )
                        mask = denominator_pix != 0
                        ratio = np.zeros_like(numerator_pix)
                        ratio[mask] = numerator_pix[mask] / denominator_pix[mask]
                        self.contour_settings["contour_data"] = ratio
                    elif stokes == "L":
                        q_pix, contour_csys, _ = get_pixel_values_from_image(
                            self.imagename, "Q", threshold, rms_box
                        )
                        u_pix, contour_csys, _ = get_pixel_values_from_image(
                            self.imagename, "U", threshold, rms_box
                        )
                        l_pix = np.sqrt(q_pix**2 + u_pix**2)
                        self.contour_settings["contour_data"] = l_pix
                    elif stokes == "Lfrac":
                        q_pix, contour_csys, _ = get_pixel_values_from_image(
                            self.imagename, "Q", threshold, rms_box
                        )
                        u_pix, contour_csys, _ = get_pixel_values_from_image(
                            self.imagename, "U", threshold, rms_box
                        )
                        i_pix, contour_csys, _ = get_pixel_values_from_image(
                            self.imagename, "I", threshold, rms_box
                        )
                        l_pix = np.sqrt(q_pix**2 + u_pix**2)
                        mask = i_pix != 0
                        lfrac = np.zeros_like(l_pix)
                        lfrac[mask] = l_pix[mask] / i_pix[mask]
                        self.contour_settings["contour_data"] = lfrac
                    elif stokes == "PANG":
                        q_pix, contour_csys, _ = get_pixel_values_from_image(
                            self.imagename, "Q", threshold, rms_box
                        )
                        u_pix, contour_csys, _ = get_pixel_values_from_image(
                            self.imagename, "U", threshold, rms_box
                        )
                        pang = 0.5 * np.arctan2(u_pix, q_pix) * 180 / np.pi
                        self.contour_settings["contour_data"] = pang
                    else:
                        pix, contour_csys, _ = get_pixel_values_from_image(
                            self.imagename, "I", threshold, rms_box
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
                    threshold = 5.0

                    if stokes in ["I", "Q", "U", "V"]:
                        pix, contour_csys, _ = get_pixel_values_from_image(
                            external_image, stokes, threshold, rms_box
                        )
                        self.contour_settings["contour_data"] = pix
                    elif stokes in ["Q/I", "U/I", "V/I"]:
                        numerator_stokes = stokes.split("/")[0]
                        numerator_pix, contour_csys, _ = get_pixel_values_from_image(
                            external_image, numerator_stokes, threshold, rms_box
                        )
                        denominator_pix, contour_csys, _ = get_pixel_values_from_image(
                            external_image, "I", threshold, rms_box
                        )
                        mask = denominator_pix != 0
                        ratio = np.zeros_like(numerator_pix)
                        ratio[mask] = numerator_pix[mask] / denominator_pix[mask]
                        self.contour_settings["contour_data"] = ratio
                    elif stokes == "L":
                        q_pix, contour_csys, _ = get_pixel_values_from_image(
                            external_image, "Q", threshold, rms_box
                        )
                        u_pix, contour_csys, _ = get_pixel_values_from_image(
                            external_image, "U", threshold, rms_box
                        )
                        l_pix = np.sqrt(q_pix**2 + u_pix**2)
                        self.contour_settings["contour_data"] = l_pix
                    elif stokes == "Lfrac":
                        q_pix, contour_csys, _ = get_pixel_values_from_image(
                            external_image, "Q", threshold, rms_box
                        )
                        u_pix, contour_csys, _ = get_pixel_values_from_image(
                            external_image, "U", threshold, rms_box
                        )
                        i_pix, contour_csys, _ = get_pixel_values_from_image(
                            external_image, "I", threshold, rms_box
                        )
                        l_pix = np.sqrt(q_pix**2 + u_pix**2)
                        mask = i_pix != 0
                        lfrac = np.zeros_like(l_pix)
                        lfrac[mask] = l_pix[mask] / i_pix[mask]
                        self.contour_settings["contour_data"] = lfrac
                    elif stokes == "PANG":
                        q_pix, contour_csys, _ = get_pixel_values_from_image(
                            external_image, "Q", threshold, rms_box
                        )
                        u_pix, contour_csys, _ = get_pixel_values_from_image(
                            external_image, "U", threshold, rms_box
                        )
                        pang = 0.5 * np.arctan2(u_pix, q_pix) * 180 / np.pi
                        self.contour_settings["contour_data"] = pang
                    else:
                        pix, contour_csys, _ = get_pixel_values_from_image(
                            external_image, "I", threshold, rms_box
                        )
                        self.contour_settings["contour_data"] = pix
                    self.current_contour_wcs = contour_csys
                else:
                    self.contour_settings["contour_data"] = None
                    self.current_contour_wcs = None

            main_window = self.parent()
            if main_window and hasattr(main_window, "statusBar"):
                if self.contour_settings["contour_data"] is not None:
                    main_window.statusBar().showMessage(
                        f"Contour data loaded: {self.contour_settings['source']} image, Stokes {self.contour_settings['stokes']}"
                    )
                else:
                    main_window.statusBar().showMessage("Failed to load contour data")
        except Exception as e:
            print(f"Error loading contour data: {e}")
            self.contour_settings["contour_data"] = None
            main_window = self.parent()
            if main_window and hasattr(main_window, "statusBar"):
                main_window.statusBar().showMessage(
                    f"Error loading contour data: {str(e)}"
                )

    def draw_contours(self, ax):
        if self.contour_settings["contour_data"] is None:
            self.load_contour_data()
            print("Contour data loaded")

        if self.contour_settings["contour_data"] is None:
            return

        if self.current_contour_wcs is None:
            return

        fits_flag = False
        if self.contour_settings["source"] == "same":
            contour_imagename = self.imagename
        else:
            contour_imagename = self.contour_settings["external_image"]

        if contour_imagename.endswith(".fits") or contour_imagename.endswith(".fts"):
            from astropy.io import fits

            fits_flag = True
            hdul = fits.open(contour_imagename)
            header = hdul[0].header

        try:
            ia_tool = IA()
            ia_tool.open(contour_imagename)
            csys = ia_tool.coordsys()
            summary = ia_tool.summary()
            ia_tool.close()
        except Exception as e:
            print(f"Error getting metadata: {e}")
            return

        try:
            # Check if the contour and image projection match
            print("Drawing contours........")
            image_wcs_obj = None
            if hasattr(self, "_cached_wcs_obj"):
                image_wcs_obj = self._cached_wcs_obj

            different_projections = False
            different_increments = False

            if self.current_contour_wcs is not None and image_wcs_obj is not None:
                # Check for different projections
                from astropy.wcs import WCS

                contour_wcs_obj = WCS(naxis=2)

                # set up contour wcs
                ref_val = self.current_contour_wcs.referencevalue()["numeric"][0:2]
                ref_pix = self.current_contour_wcs.referencepixel()["numeric"][0:2]
                increment = self.current_contour_wcs.increment()["numeric"][0:2]

                contour_wcs_obj.wcs.crpix = ref_pix

                if "Right Ascension" in summary["axisnames"]:
                    contour_wcs_obj.wcs.crval = [
                        ref_val[0] * 180 / np.pi,
                        ref_val[1] * 180 / np.pi,
                    ]
                    contour_wcs_obj.wcs.cdelt = [
                        increment[0] * 180 / np.pi,
                        increment[1] * 180 / np.pi,
                    ]
                else:
                    contour_wcs_obj.wcs.crval = ref_val
                    contour_wcs_obj.wcs.cdelt = increment

                # Set projection type
                if fits_flag:
                    try:
                        contour_wcs_obj.wcs.ctype = [
                            header["CTYPE1"],
                            header["CTYPE2"],
                        ]
                    except Exception as e:
                        print(f"Error getting projection type: {e}")

                elif (csys.projection()["type"] == "SIN") and (
                    "Right Ascension" in summary["axisnames"]
                ):
                    contour_wcs_obj.wcs.ctype = [
                        "RA---SIN",
                        "DEC--SIN",
                    ]
                elif (csys.projection()["type"] == "TAN") and (
                    "Right Ascension" in summary["axisnames"]
                ):
                    contour_wcs_obj.wcs.ctype = [
                        "RA---TAN",
                        "DEC--TAN",
                    ]
                else:
                    print("Warning: Unrecognized projection type")
                    contour_wcs_obj = None

                if contour_wcs_obj is not None:
                    if contour_wcs_obj.wcs.ctype[0] != image_wcs_obj.wcs.ctype[0]:
                        different_projections = True
                        print("Warning: Different projections for contour and image")

                    # Check for different increments
                    contour_cdelt = np.abs(contour_wcs_obj.wcs.cdelt)
                    image_cdelt = np.abs(image_wcs_obj.wcs.cdelt)
                    scale_ratio_x = contour_cdelt[0] / image_cdelt[0]
                    scale_ratio_y = contour_cdelt[1] / image_cdelt[1]

                    if (np.abs(scale_ratio_x - 1) > 1e-3) or (
                        np.abs(scale_ratio_y - 1) > 1e-3
                    ):
                        different_increments = True
                        print("Warning: Different increments for contour and image")

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
                mean = np.nanmean(contour_data)
                std = np.nanstd(contour_data)
                pos_levels = sorted(
                    [
                        mean + level * std
                        for level in self.contour_settings["pos_levels"]
                    ]
                )
                neg_levels = sorted(
                    [
                        -(mean - level * std)
                        for level in reversed(self.contour_settings["neg_levels"])
                    ]
                )
            else:
                pos_levels = sorted(self.contour_settings["pos_levels"])
                neg_levels = sorted(
                    [-level for level in reversed(self.contour_settings["neg_levels"])]
                )

            plot_default = False

            if (
                different_projections == False
                and different_increments == True
                and contour_wcs_obj is not None
                and fits_flag == True
            ):
                try:
                    import sunpy.map
                    from sunpy.coordinates import Helioprojective
                    import astropy.units as u
                    from astropy.coordinates import SkyCoord
                    from astropy.time import Time
                    from astropy.wcs.utils import wcs_to_celestial_frame

                    print("Using SunPy for contour overlay")

                    # Create a SunPy Map for the contour data
                    contour_header = contour_wcs_obj.to_header()

                    # Add necessary FITS keywords if missing
                    if "NAXIS1" not in contour_header:
                        contour_header["NAXIS1"] = contour_data.shape[1]
                    if "NAXIS2" not in contour_header:
                        contour_header["NAXIS2"] = contour_data.shape[0]

                    # Add observer information to the header
                    # This is crucial for fixing the observer error
                    if "DATE-OBS" not in contour_header:
                        contour_header["DATE-OBS"] = Time.now().isot
                    if "RSUN_REF" not in contour_header:
                        contour_header["RSUN_REF"] = (
                            695700000.0  # Solar radius in meters
                        )
                    if "DSUN_OBS" not in contour_header:
                        contour_header["DSUN_OBS"] = 1.496e11  # 1 AU in meters

                    # Add coordinate system information if missing
                    if "HGLT_OBS" not in contour_header:
                        contour_header["HGLT_OBS"] = (
                            0.0  # Heliographic latitude of observer
                        )
                    if "HGLN_OBS" not in contour_header:
                        contour_header["HGLN_OBS"] = (
                            0.0  # Heliographic longitude of observer
                        )

                    # Create the contour map
                    contour_map = sunpy.map.Map(contour_data, contour_header)

                    # Create a SunPy Map for the image data
                    image_header = image_wcs_obj.to_header()

                    if "NAXIS1" not in image_header:
                        image_header["NAXIS1"] = self.current_image_data.shape[1]
                    if "NAXIS2" not in image_header:
                        image_header["NAXIS2"] = self.current_image_data.shape[0]

                    # Add the same observer information to the image header
                    if "DATE-OBS" not in image_header:
                        image_header["DATE-OBS"] = contour_header.get(
                            "DATE-OBS", Time.now().isot
                        )
                    if "RSUN_REF" not in image_header:
                        image_header["RSUN_REF"] = contour_header.get(
                            "RSUN_REF", 695700000.0
                        )
                    if "DSUN_OBS" not in image_header:
                        image_header["DSUN_OBS"] = contour_header.get(
                            "DSUN_OBS", 1.496e11
                        )
                    if "HGLT_OBS" not in image_header:
                        image_header["HGLT_OBS"] = contour_header.get("HGLT_OBS", 0.0)
                    if "HGLN_OBS" not in image_header:
                        image_header["HGLN_OBS"] = contour_header.get("HGLN_OBS", 0.0)

                    # Create the image map
                    imagemap = sunpy.map.Map(self.current_image_data, image_header)

                    # Reproject the contour map to match the image map's coordinate system
                    print("Reprojecting contour map")

                    try:
                        # Try SunPy's built-in reprojection
                        reprojected_map = contour_map.reproject_to(
                            imagemap.wcs,
                            shape_out=self.current_image_data.shape,
                        )
                        contour_data = reprojected_map.data
                        print("Reprojected contour data using SunPy")

                    except Exception as e:
                        print(
                            f"Error reprojecting contour data: {e}\nTrying reproject_interp .... "
                        )
                        try:
                            from reproject import reproject_interp

                            # Reproject the contour data to the image WCS
                            array, footprint = reproject_interp(
                                (contour_data, contour_wcs_obj),
                                image_wcs_obj,
                                shape_out=self.current_image_data.shape,
                            )

                            # Replace the NaNs with zeros
                            array = np.nan_to_num(array, nan=0.0)

                            print("Reprojected contour data")
                            contour_data = array
                        except Exception as e:
                            print(f"Error reprojecting contour data: {e}")
                            plot_default = True

                    contour_wcs_obj = None
                except ImportError as e:
                    print(f"Failed to reproject contour data: {e}")
                    plot_default = True

            if pos_levels and len(pos_levels) > 0:
                try:
                    ax.contour(
                        contour_data.transpose(),
                        levels=pos_levels,
                        colors=self.contour_settings["color"],
                        linewidths=self.contour_settings["linewidth"],
                        linestyles=self.contour_settings["pos_linestyle"],
                        origin="lower",
                    )
                except Exception as e:
                    print(f"Error drawing positive contours: {e}, levels: {pos_levels}")

            if neg_levels and len(neg_levels) > 0:
                try:
                    ax.contour(
                        contour_data.transpose(),
                        levels=neg_levels,
                        colors=self.contour_settings["color"],
                        linewidths=self.contour_settings["linewidth"],
                        linestyles=self.contour_settings["neg_linestyle"],
                        origin="lower",
                    )
                except Exception as e:
                    print(f"Error drawing negative contours: {e}, levels: {neg_levels}")

        except Exception as e:
            print(f"Error drawing contours: {e}")
            import traceback

            traceback.print_exc()
            main_window = self.parent()
            if main_window and hasattr(main_window, "statusBar"):
                main_window.statusBar().showMessage(f"Error drawing contours: {str(e)}")

    def closeEvent(self, event):
        super().closeEvent(event)

    def reset_view(self, show_status_message=True):
        """Reset the view to show the full image with original limits"""
        if self.current_image_data is None:
            return

        ax = self.figure.axes[0]

        # Reset to show the full image
        ax.set_xlim(0, self.current_image_data.shape[0])
        ax.set_ylim(0, self.current_image_data.shape[1])

        self._update_beam_position(ax)
        # If solar disk checkbox is checked, draw the solar disk
        if self.show_solar_disk_checkbox.isChecked():
            self._update_solar_disk_position(ax)
        self.canvas.draw()
        if show_status_message:
            self.show_status_message("Reseted plot to full image")

    def show_rms_box_dialog(self):
        """Show a dialog for configuring the RMS box settings"""
        dialog = QDialog(self)
        dialog.setWindowTitle("RMS Box Settings")
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
        description.setStyleSheet("color: #BBB; font-style: italic;")
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
        button_box.accepted.connect(lambda: self.apply_dialog_rms_box(dialog))
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        # Show the dialog
        dialog.exec_()

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


class CustomTabBar(QTabBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Create the add tab button
        self.add_tab_button = QToolButton(self)
        self.add_tab_button.setIcon(
            QIcon(
                pkg_resources.resource_filename(
                    "solar_radio_image_viewer", "assets/add_tab_default.png"
                )
            )
        )
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

        # Set tab bar properties for better dark theme appearance
        self.setStyleSheet(
            """
            QTabBar::tab {
                padding: 4px 12px 4px 8px;  /* Increased left padding for larger close button */
                margin: 0px 0px 0px 0px;  /* Removed top margin to eliminate white space */
                border-top-left-radius: 0px;
                border-top-right-radius: 0px;
                text-align: left;  /* Align text to the left */
                border-top: none;  /* Remove top border to prevent any white line */
            }
            QTabBar::tab:selected {
                background: #383838;
                border: 1px solid #484848;
                border-top: none;  /* Remove top border */
            }
            QTabBar::tab:!selected {
                background: #252525;
                border: 1px solid #353535;
                border-top: none;  /* Remove top border */
            }
            QTabBar::tab:hover {
                background: #404040;
            }
            QTabBar::close-button {
                image: url("""
            + pkg_resources.resource_filename(
                "solar_radio_image_viewer", "assets/close_tab_default.png"
            )
            + """);
                subcontrol-position: left;  /* Changed from right to left */
                subcontrol-origin: margin;  /* Position relative to the margin */
                margin-left: 4px;  /* Increased margin to the left of the close button */
                width: 32px;  /* Increase width of close button */
                height: 32px;  /* Increase height of close button */
            }
            QTabBar::close-button:hover {
                image: url("""
            + pkg_resources.resource_filename(
                "solar_radio_image_viewer", "assets/close_tab_hover.png"
            )
            + """);
            }
            """
        )

        # Enable expanding tabs to fill available space
        self.setExpanding(True)

        # Make sure the add button is visible and on top
        self.add_tab_button.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        # self.add_tab_button.setFocusPolicy(Qt.StrongFocus)
        self.add_tab_button.show()
        self.add_tab_button.raise_()

        # Initialize button position
        QTimer.singleShot(0, self.moveAddButton)

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
        self.add_tab_button.setIcon(
            QIcon(
                pkg_resources.resource_filename(
                    "solar_radio_image_viewer", "assets/add_tab_hover.png"
                )
            )
        )

    def _handle_add_button_hover_leave(self, event):
        self.add_tab_button.setIcon(
            QIcon(
                pkg_resources.resource_filename(
                    "solar_radio_image_viewer", "assets/add_tab_default.png"
                )
            )
        )

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

        # Set tab widget properties for better dark theme appearance
        self.setStyleSheet(
            """
            QTabWidget::pane {
                border: 1px solid #484848;
                background: #2D2D2D;
            }
            """
        )

        # Make sure the add button is properly initialized
        QTimer.singleShot(100, self.ensureAddButtonVisible)

    def _handle_add_button_hover_enter(self, event):
        self.add_tab_button.setIcon(
            QIcon(
                pkg_resources.resource_filename(
                    "solar_radio_image_viewer", "assets/add_tab_hover.png"
                )
            )
        )

    def _handle_add_button_hover_leave(self, event):
        self.add_tab_button.setIcon(
            QIcon(
                pkg_resources.resource_filename(
                    "solar_radio_image_viewer", "assets/add_tab_default.png"
                )
            )
        )

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
        self.setWindowTitle("Solar Radio Image Viewer")
        self.resize(1400, 800)

        # Use custom tab widget
        self.tab_widget = CustomTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.tabCloseRequested.connect(self.close_tab)
        self.tab_widget.add_tab_button.clicked.connect(self.handle_add_tab)

        self.setCentralWidget(self.tab_widget)
        self.tabs = []
        self.max_tabs = 10
        self.settings = QSettings("SolarRadioImageViewer", "ImageViewer")

        self.statusBar().showMessage("Ready")
        self.create_menus()

        first_tab = self.add_new_tab("Tab1")
        if imagename and os.path.exists(imagename):
            first_tab.imagename = imagename
            first_tab.dir_entry.setText(imagename)
            first_tab.on_visualization_changed(dir_load=True)
            # first_tab.auto_minmax()

        # Ensure add button is visible after initialization
        QTimer.singleShot(200, self.ensureAddButtonVisible)

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

        export_act = QAction("Export Figure", self)
        export_act.setShortcut("Ctrl+E")
        export_act.setStatusTip("Export current figure as image file")
        export_act.triggered.connect(self.export_data)
        file_menu.addAction(export_act)

        export_data_act = QAction("Export Data as FITS", self)
        export_data_act.setShortcut("Ctrl+F")
        export_data_act.setStatusTip("Export current data as FITS file")
        export_data_act.triggered.connect(self.export_as_fits)
        file_menu.addAction(export_data_act)

        # Add after the export_data_act action in create_menus method
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

        tools_menu = menubar.addMenu("&Tools")
        from .dialogs import BatchProcessDialog, ImageInfoDialog

        batch_act = QAction("Batch Processing", self)
        batch_act.setShortcut("Ctrl+B")
        batch_act.setStatusTip("Process multiple images in batch mode")
        batch_act.triggered.connect(self.show_batch_dialog)
        tools_menu.addAction(batch_act)

        metadata_act = QAction("Image Metadata", self)
        metadata_act.setShortcut("Ctrl+M")
        metadata_act.setStatusTip("View detailed metadata for the current image")
        metadata_act.triggered.connect(self.show_metadata)
        tools_menu.addAction(metadata_act)

        napari_act = QAction("Fast Viewer (Napari)", self)
        napari_act.setShortcut("Ctrl+Shift+N")
        napari_act.setStatusTip("Launch the Napari-based fast image viewer")
        napari_act.triggered.connect(self.launch_napari_viewer)
        tools_menu.addAction(napari_act)

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
        ring_act.setShortcut("Ctrl+L")
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
        auto_median_rms_act = QAction("Auto Median ± 3×RMS", self)
        auto_median_rms_act.setShortcut("F7")
        auto_median_rms_act.setStatusTip("Set display range to median ± 3×RMS")
        auto_median_rms_act.triggered.connect(self.auto_median_rms)
        preset_menu.addAction(auto_median_rms_act)

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
        gui_downloader_action = QAction("Solar Data Downloader (GUI)", self)
        gui_downloader_action.setStatusTip(
            "Launch the graphical interface for downloading solar data"
        )
        gui_downloader_action.triggered.connect(self.launch_data_downloader_gui)
        download_menu.addAction(gui_downloader_action)

        # CLI Downloader action
        cli_downloader_action = QAction("Solar Data Downloader (CLI)", self)
        cli_downloader_action.setStatusTip(
            "Launch the command-line interface for downloading solar data"
        )
        cli_downloader_action.triggered.connect(self.launch_data_downloader_cli)
        download_menu.addAction(cli_downloader_action)
        help_menu = menubar.addMenu("&Help")
        shortcuts_act = QAction("Keyboard Shortcuts", self)
        shortcuts_act.setShortcut("F1")
        shortcuts_act.setStatusTip("Show keyboard shortcuts")
        shortcuts_act.triggered.connect(self.show_keyboard_shortcuts)
        help_menu.addAction(shortcuts_act)
        about_act = QAction("About", self)
        about_act.setStatusTip("Show information about this application")
        about_act.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_act)

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

        # Ensure add button is visible after adding a new tab
        QTimer.singleShot(100, self.ensureAddButtonVisible)

        return new_tab

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

    def auto_median_rms(self):
        current_tab = self.tab_widget.currentWidget()
        if current_tab:
            current_tab.auto_median_rms()

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
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or current_tab.current_image_data is None:
            QMessageBox.warning(self, "No Data", "No image data to export")
            return

        try:
            from astropy.io import fits

            path, _ = QFileDialog.getSaveFileName(
                self, "Export as FITS", "", "FITS Files (*.fits);;All Files (*)"
            )
            if path:
                hdu = fits.PrimaryHDU(current_tab.current_image_data)
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

    def show_batch_dialog(self):
        from .dialogs import BatchProcessDialog

        dialog = BatchProcessDialog(self)
        dialog.exec_()

    def show_metadata(self):
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or not current_tab.imagename:
            QMessageBox.warning(self, "No Image", "No image loaded")
            return

        try:
            metadata = get_image_metadata(current_tab.imagename)
            from .dialogs import ImageInfoDialog

            dialog = ImageInfoDialog(self, metadata)
            dialog.exec_()
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
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or current_tab.current_image_data is None:
            QMessageBox.warning(self, "No Data", "Load data first.")
            return

        data = current_tab.current_image_data

        if current_tab.current_roi and isinstance(current_tab.current_roi, tuple):
            xlow, xhigh, ylow, yhigh = current_tab.current_roi
            data = data[xlow:xhigh, ylow:yhigh]
            if data.size == 0:
                QMessageBox.warning(self, "Invalid ROI", "ROI contains no data")
                return

        ny, nx = data.shape
        x = np.arange(nx)
        y = np.arange(ny)
        xmesh, ymesh = np.meshgrid(x, y)
        coords = np.vstack((xmesh.ravel(), ymesh.ravel()))
        data_flat = data.ravel()

        guess = [np.nanmax(data), nx / 2, ny / 2, nx / 4, ny / 4, 0, np.nanmedian(data)]

        try:
            popt, pcov = curve_fit(twoD_gaussian, coords, data_flat, p0=guess)
            perr = np.sqrt(np.diag(pcov))

            msg = (
                f"2D Gaussian Fit:\n"
                f"Amp={popt[0]:.4g}±{perr[0]:.4g}\n"
                f"X0={popt[1]:.2f}±{perr[1]:.2f}, Y0={popt[2]:.2f}±{perr[2]:.2f}\n"
                f"SigmaX={popt[3]:.2f}±{perr[3]:.2f}, SigmaY={popt[4]:.2f}±{perr[4]:.2f}\n"
                f"Theta={popt[5]:.2f}±{perr[5]:.2f}, Offset={popt[6]:.4g}±{perr[6]:.4g}"
            )

            QMessageBox.information(self, "Fit Result", msg)

            if (
                QMessageBox.question(
                    self,
                    "Overlay Fit",
                    "Would you like to overlay the fit on the image?",
                    QMessageBox.Yes | QMessageBox.No,
                )
                == QMessageBox.Yes
            ):

                fitted_data = twoD_gaussian(coords, *popt).reshape(data.shape)

                fig = Figure(figsize=(10, 5))
                canvas = FigureCanvas(fig)

                ax1 = fig.add_subplot(121)
                ax1.imshow(data.transpose(), origin="lower", cmap="viridis")
                ax1.set_title("Original Data")

                ax2 = fig.add_subplot(122)
                ax2.imshow(fitted_data.transpose(), origin="lower", cmap="viridis")
                ax2.set_title("Gaussian Fit")

                fig.tight_layout()

                dialog = QDialog(self)
                dialog.setWindowTitle("Gaussian Fit Comparison")
                layout = QVBoxLayout(dialog)
                layout.addWidget(canvas)

                buttons = QDialogButtonBox(QDialogButtonBox.Ok)
                buttons.accepted.connect(dialog.accept)
                layout.addWidget(buttons)

                dialog.setLayout(layout)
                dialog.exec_()

        except Exception as e:
            QMessageBox.warning(self, "Fit Error", f"Gaussian fit failed: {str(e)}")

    def fit_2d_ring(self):
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or current_tab.current_image_data is None:
            QMessageBox.warning(self, "No Data", "Load data first.")
            return

        data = current_tab.current_image_data

        if current_tab.current_roi and isinstance(current_tab.current_roi, tuple):
            xlow, xhigh, ylow, yhigh = current_tab.current_roi
            data = data[xlow:xhigh, ylow:yhigh]
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

            msg = (
                f"2D Elliptical Ring Fit:\n"
                f"Amp={popt[0]:.4g}±{perr[0]:.4g}\n"
                f"X0={popt[1]:.2f}±{perr[1]:.2f}, Y0={popt[2]:.2f}±{perr[2]:.2f}\n"
                f"Inner R={popt[3]:.2f}±{perr[3]:.2f}, Outer R={popt[4]:.2f}±{perr[4]:.2f}\n"
                f"Offset={popt[5]:.4g}±{perr[5]:.4g}"
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
        layout = QVBoxLayout(dialog)

        form_layout = QGridLayout()
        form_layout.addWidget(QLabel("X Position:"), 0, 0)
        x_pos = QLineEdit("100")
        form_layout.addWidget(x_pos, 0, 1)
        form_layout.addWidget(QLabel("Y Position:"), 1, 0)
        y_pos = QLineEdit("100")
        form_layout.addWidget(y_pos, 1, 1)
        form_layout.addWidget(QLabel("Text:"), 2, 0)
        text_input = QLineEdit("Annotation")
        form_layout.addWidget(text_input, 2, 1)
        layout.addLayout(form_layout)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec_() == QDialog.Accepted:
            try:
                x = int(x_pos.text())
                y = int(y_pos.text())
                text = text_input.text()
                current_tab.add_text_annotation(x, y, text)
            except ValueError:
                QMessageBox.warning(
                    self, "Invalid Input", "Please enter valid numeric coordinates"
                )

    def add_arrow_annotation(self):
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or current_tab.current_image_data is None:
            QMessageBox.warning(self, "No Data", "Load data first.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Add Arrow Annotation")
        layout = QVBoxLayout(dialog)

        form_layout = QGridLayout()
        form_layout.addWidget(QLabel("Start X:"), 0, 0)
        x1_pos = QLineEdit("100")
        form_layout.addWidget(x1_pos, 0, 1)
        form_layout.addWidget(QLabel("Start Y:"), 1, 0)
        y1_pos = QLineEdit("100")
        form_layout.addWidget(y1_pos, 1, 1)
        form_layout.addWidget(QLabel("End X:"), 2, 0)
        x2_pos = QLineEdit("150")
        form_layout.addWidget(x2_pos, 2, 1)
        form_layout.addWidget(QLabel("End Y:"), 3, 0)
        y2_pos = QLineEdit("150")
        form_layout.addWidget(y2_pos, 3, 1)
        layout.addLayout(form_layout)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec_() == QDialog.Accepted:
            try:
                x1 = int(x1_pos.text())
                y1 = int(y1_pos.text())
                x2 = int(x2_pos.text())
                y2 = int(y2_pos.text())
                current_tab.add_arrow_annotation(x1, y1, x2, y2)
            except ValueError:
                QMessageBox.warning(
                    self, "Invalid Input", "Please enter valid numeric coordinates"
                )

    def show_about_dialog(self):
        about_text = """
        <h2>Solar Radio Image Viewer</h2>
        <p>A tool for visualizing and analyzing CASA radio astronomy images.</p>
        
        <h3>Features:</h3>
        <ul>
            <li>Multi-tab interface for comparing images</li>
            <li>Various color maps and stretches including power-law normalization</li>
            <li>Region selection and statistics</li>
            <li>2D model fitting (Gaussian and Elliptical Ring)</li>
            <li>Annotations</li>
            <li>Batch processing</li>
            <li>Dark theme for comfortable viewing</li>
            <li>Keyboard shortcuts for efficient workflow</li>
        </ul>
        
        <p>Press F1 for keyboard shortcuts</p>
        
        <h3>Author Information:</h3>
        <p>Developed by: Soham Dey</p>
        <p>Email: sohamd943@gmail.com</p>
        <p>Date: March 2025</p>
        
        <p><small>Version 1.0</small></p>
        """
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("About Solar Radio Image Viewer")
        msg_box.setTextFormat(Qt.RichText)
        msg_box.setText(about_text)
        msg_box.setStandardButtons(QMessageBox.Ok)
        msg_box.exec_()

    def show_keyboard_shortcuts(parent=None):
        from PyQt5.QtWidgets import QTextBrowser

        dialog = QDialog(parent)
        dialog.setWindowTitle("Keyboard Shortcuts")

        layout = QVBoxLayout(dialog)

        html_content = """
        <html>
        <head>
        <meta charset="utf-8">
        <style>
            body {
                background-color: #1E1E22;
                color: #E0E0E0;
                font-family: 'Segoe UI', Arial, sans-serif;
                margin: 0;
                padding: 20px;
            }
            h3 {
                text-align: center;
                margin-bottom: 20px;
                font-size: 18pt;
            }
            h4 {
                color: #64B5F6;
                margin: 10px 0 5px 0;
                font-size: 14pt;
                border-bottom: 1px solid #333;
                padding-bottom: 5px;
            }
            .container {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
                gap: 20px;
            }
            table {
                width: 100%;
                border-collapse: collapse;
            }
            td {
                padding: 8px;
            }
            td:first-child {
                font-weight: bold;
                color: #BB86FC;
            }
            code {
                background-color: #2A2A2E;
                padding: 2px 6px;
                border-radius: 4px;
                font-family: 'Courier New', monospace;
            }
        </style>
        </head>
        <body>
        <h3>Keyboard Shortcuts</h3>
        <div class="container">
            <div class="section">
                <h4>File Operations</h4>
                <table>
                    <tr><td><code>Ctrl+O</code></td><td>Open CASA Image</td></tr>
                    <tr><td><code>Ctrl+Shift+O</code></td><td>Open FITS File</td></tr>
                    <tr><td><code>Ctrl+E</code></td><td>Export Figure</td></tr>
                    <tr><td><code>Ctrl+F</code></td><td>Export as FITS</td></tr>
                    <tr><td><code>Ctrl+Shift+N</code></td><td>Fast Viewer (Napari)</td></tr>
                    <tr><td><code>Ctrl+Q</code></td><td>Exit</td></tr>
                </table>
            </div>
            <div class="section">
                <h4>Navigation &amp; View</h4>
                <table>
                    <tr><td><code>R</code></td><td>Reset View</td></tr>
                    <tr><td><code>1</code></td><td>1°×1° Zoom</td></tr>
                    <tr><td><code>+/=</code></td><td>Zoom In</td></tr>
                    <tr><td><code>-</code></td><td>Zoom Out</td></tr>
                    <tr><td><code>Space/Enter</code></td><td>Update Plot</td></tr>
                </table>
            </div>
            <div class="section">
                <h4>Display Presets &amp; Tools</h4>
                <table>
                    <tr><td><code>F5</code></td><td>Auto Min/Max</td></tr>
                    <tr><td><code>F6</code></td><td>Auto Percentile</td></tr>
                    <tr><td><code>F7</code></td><td>Auto Median±3×RMS</td></tr>
                    <tr><td><code>Ctrl+B</code></td><td>Batch Processing</td></tr>
                    <tr><td><code>Ctrl+M</code></td><td>Image Metadata</td></tr>
                </table>
            </div>
            <div class="section">
                <h4>Region, Analysis &amp; Annotations</h4>
                <table>
                    <tr><td><code>Ctrl+S</code></td><td>Export Sub-Image</td></tr>
                    <tr><td><code>Ctrl+R</code></td><td>Export ROI</td></tr>
                    <tr><td><code>Ctrl+G</code></td><td>Fit 2D Gaussian</td></tr>
                    <tr><td><code>Ctrl+L</code></td><td>Fit Ring</td></tr>
                    <tr><td><code>Ctrl+T</code></td><td>Add Text</td></tr>
                    <tr><td><code>Ctrl+A</code></td><td>Add Arrow</td></tr>
                </table>
            </div>
            <div class="section">
                <h4>Tab Management</h4>
                <table>
                    <tr><td><code>Ctrl+N</code></td><td>New Tab</td></tr>
                    <tr><td><code>Ctrl+W</code></td><td>Close Tab</td></tr>
                    <tr><td><code>←</code></td><td>Previous Tab</td></tr>
                    <tr><td><code>→</code></td><td>Next Tab</td></tr>
                </table>
            </div>
        </div>
        </body>
        </html>
        """

        text_browser = QTextBrowser(dialog)
        text_browser.setOpenExternalLinks(False)
        text_browser.setHtml(html_content)
        layout.addWidget(text_browser)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok)
        button_box.accepted.connect(dialog.accept)
        layout.addWidget(button_box)

        dialog.resize(480, 720)
        dialog.exec_()

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
        elif event.key() == Qt.Key_Left:
            current_idx = self.tab_widget.currentIndex()
            if current_idx > 0:
                self.tab_widget.setCurrentIndex(current_idx - 1)
        elif event.key() == Qt.Key_Right:
            current_idx = self.tab_widget.currentIndex()
            if current_idx < self.tab_widget.count() - 1:
                self.tab_widget.setCurrentIndex(current_idx + 1)
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

    def launch_napari_viewer(self):
        """Launch the Napari-based fast image viewer"""
        try:
            from .napari_viewer import NapariViewer

            # Get the current tab and check if it has an image loaded
            current_tab = self.tab_widget.currentWidget()
            imagename = None
            if (
                current_tab
                and hasattr(current_tab, "imagename")
                and current_tab.imagename
            ):
                imagename = current_tab.imagename

            # Create and show the Napari viewer with the current image if available
            self.napari_viewer = NapariViewer(imagename)

            self.statusBar().showMessage("Napari viewer launched", 3000)
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to launch Napari viewer: {str(e)}",
            )

    def launch_data_downloader_gui(self):
        """Launch the Solar Data Downloader GUI."""
        try:
            launch_downloader_gui(self)
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to launch Solar Data Downloader GUI: {str(e)}\n\n"
                "Please make sure all required dependencies are installed.",
            )

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

            print(f"Using Python interpreter: {python_path}")
            print(f"Virtual environment path: {venv_path}")
            print(f"CLI directory: {cli_dir}")
            print(f"CLI script path: {cli_script}")

            # Create a shell script to activate venv and run the CLI
            temp_script = os.path.join(cli_dir, "run_cli.sh")
            with open(temp_script, "w") as f:
                f.write(
                    f"""#!/bin/bash
source "{activate_script}"
cd "{cli_dir}"
python3 "{cli_script}"
read -p "Press Enter to close..."
"""
                )
            os.chmod(temp_script, 0o755)

            # Determine the terminal command based on the platform
            if sys.platform.startswith("linux"):
                # First, let's check which terminals are available
                available_terminals = []
                for term in ["gnome-terminal", "konsole", "xfce4-terminal", "xterm"]:
                    try:
                        result = subprocess.run(
                            ["which", term], capture_output=True, text=True
                        )
                        if result.returncode == 0:
                            available_terminals.append(term)
                    except Exception:
                        continue

                print(f"Available terminals: {available_terminals}")

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

                    print(f"Attempting to launch with command: {' '.join(cmd)}")
                    process = subprocess.Popen(cmd)

                    # Wait a bit to see if the process starts successfully
                    try:
                        process.wait(timeout=1)
                        if process.returncode is not None and process.returncode != 0:
                            raise Exception(f"Terminal {terminal} failed to start")
                    except subprocess.TimeoutExpired:
                        # Process is still running after 1 second, which is good
                        self.statusBar().showMessage(f"Launched CLI in {terminal}")
                        print(f"Successfully launched CLI in {terminal}")
                        return

                except Exception as term_error:
                    print(f"Error launching {terminal}: {str(term_error)}")
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
            print(error_msg)  # Print to console for debugging
            QMessageBox.critical(self, "Error", error_msg)

    def export_as_hpc_fits(self):
        """Export the current image as a helioprojective FITS file"""
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or not current_tab.imagename:
            QMessageBox.warning(self, "No Image", "No image loaded to export")
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

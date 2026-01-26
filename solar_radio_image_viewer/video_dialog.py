#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
from PyQt5.QtWidgets import (
    QDialog,
    QMainWindow,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QFileDialog,
    QGroupBox,
    QComboBox,
    QCheckBox,
    QSpinBox,
    QDoubleSpinBox,
    QProgressBar,
    QMessageBox,
    QRadioButton,
    QButtonGroup,
    QApplication,
    QFrame,
    QTabWidget,
    QScrollArea,
    QWidget,
    QProgressDialog,
    QFormLayout,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt5.QtGui import QIcon
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
import numpy as np
import glob
import time
import threading
import multiprocessing
import psutil
import shutil
from contextlib import contextmanager

import matplotlib.style as mplstyle

mplstyle.use("fast")


@contextmanager
def wait_cursor():
    """Context manager to show a wait cursor"""
    QApplication.setOverrideCursor(Qt.WaitCursor)
    try:
        yield
    finally:
        QApplication.restoreOverrideCursor()


try:
    from .create_video import (
        create_video,
        VideoProgress,
        load_fits_data,
        apply_visualization,
        format_timestamp,
        get_norm,
    )
    from .norms import (
        SqrtNorm,
        AsinhNorm,
        PowerNorm,
        ZScaleNorm,
        HistEqNorm,
    )
except ImportError:
    from create_video import (
        create_video,
        VideoProgress,
        load_fits_data,
        apply_visualization,
        format_timestamp,
        get_norm,
    )
    from norms import (
        SqrtNorm,
        AsinhNorm,
        PowerNorm,
        ZScaleNorm,
        HistEqNorm,
    )


from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT


class PreviewWindow(QMainWindow):
    """
    Separate window for previewing images
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preview")
        self.resize(600, 500)

        # Central widget and layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # Matplotlib figure
        self.figure = Figure(figsize=(5, 4), dpi=100)
        self.canvas = FigureCanvas(self.figure)

        # Toolbar
        self.toolbar = NavigationToolbar2QT(self.canvas, self)

        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas)

        # Ensure cleanup on close?
        # Actually we want to keep it alive via the parent reference but just hide/show
        # self.setAttribute(Qt.WA_DeleteOnClose) # Don't delete, just hide

    def closeEvent(self, event):
        # Just hide instead of closing/destroying to keep state
        # user can re-open via button
        self.hide()
        event.ignore()


class VideoCreationDialog(QDialog):
    """
    Dialog for creating videos from FITS files
    """

    def __init__(self, parent=None, current_file=None):
        super().__init__(parent)
        # Force window behavior to ensure maximize button works
        self.setWindowFlags(
            Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint
        )
        self.parent = parent
        self.current_file = current_file
        self.progress_tracker = None
        self.preview_image = None
        self.reference_image = None

        self.setWindowTitle("Create Video")
        self.resize(650, 700)

        try:
            from .styles import set_hand_cursor
        except ImportError:
            from styles import set_hand_cursor

        self.setup_ui()
        set_hand_cursor(self)

        # Initialize the separate preview window FIRST (before any methods that access figure)
        self._preview_window = PreviewWindow(self)
        self._preview_window.show()

        # Initialize stretch controls
        self.update_gamma_controls()

        # Initialize range mode controls (for default "Auto per Frame")
        self.toggle_range_mode(self.range_mode_combo.currentIndex())

        # Initialize with current file if provided
        if current_file:
            dir_path = os.path.dirname(current_file)
            # If file has no directory (just filename), use CWD
            if not dir_path:
                dir_path = os.getcwd()
            self.input_directory_edit.setText(dir_path)
            file_ext = os.path.splitext(current_file)[1]
            self.input_pattern_edit.setText(f"*{file_ext}")

            # Set reference image to current file (use absolute path)
            abs_file = (
                os.path.join(dir_path, os.path.basename(current_file))
                if not os.path.isabs(current_file)
                else current_file
            )
            self.reference_image = abs_file
            self.reference_image_edit.setText(abs_file)

            # Set default output file
            self.output_file_edit.setText(os.path.join(dir_path, "output_video.mp4"))

            # Load visualization settings from parent if available
            if hasattr(parent, "colormap") and parent.colormap:
                idx = self.colormap_combo.findText(parent.colormap)
                if idx >= 0:
                    self.colormap_combo.setCurrentIndex(idx)

            if hasattr(parent, "stretch_type") and parent.stretch_type:
                idx = self.stretch_combo.findText(parent.stretch_type.capitalize())
                if idx >= 0:
                    self.stretch_combo.setCurrentIndex(idx)

            if hasattr(parent, "gamma") and parent.gamma:
                self.gamma_spinbox.setValue(parent.gamma)

            if hasattr(parent, "vmin") and parent.vmin:
                self.vmin_spinbox.setValue(parent.vmin)

            if hasattr(parent, "vmax") and parent.vmax:
                self.vmax_spinbox.setValue(parent.vmax)

            # Auto-scan for files
            self.preview_input_files()
        else:
            # No file provided - use CWD as default
            cwd = os.getcwd()
            if os.path.isdir(cwd):
                self.input_directory_edit.setText(cwd)
                self.output_file_edit.setText(os.path.join(cwd, "output_video.mp4"))
                # Check if CWD has any FITS files to set pattern
                fits_in_cwd = glob.glob(os.path.join(cwd, "*.fits")) + glob.glob(
                    os.path.join(cwd, "*.fts")
                )
                if fits_in_cwd:
                    self.input_pattern_edit.setText("*.fits")
                    # Auto-scan for files
                    self.preview_input_files()

        # Update preview if we have a valid reference image
        if self.reference_image:
            self.update_preview(self.reference_image)

    @property
    def figure(self):
        return self._preview_window.figure

    @property
    def canvas(self):
        return self._preview_window.canvas

    def show_preview_window(self):
        """Show the preview window if it's hidden or raise it"""
        self._preview_window.show()
        self._preview_window.raise_()
        self._preview_window.activateWindow()

    def setup_ui(self):
        """Set up the UI elements"""
        self.setSizeGripEnabled(True)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # Create tab widget
        self.tab_widget = QTabWidget()

        # Create tabs
        input_tab = QWidget()
        display_tab = QWidget()
        overlay_tab = QWidget()
        region_tab = QWidget()  # New tab for region selection
        output_tab = QWidget()

        # Set up tab layouts
        input_layout = QVBoxLayout(input_tab)
        display_layout = QVBoxLayout(display_tab)
        overlay_layout = QVBoxLayout(overlay_tab)
        region_layout = QVBoxLayout(region_tab)  # Layout for the new region tab
        output_layout = QVBoxLayout(output_tab)

        # Create the preview control section
        preview_controls_group = QGroupBox("Preview Controls")
        preview_controls_layout = QHBoxLayout(preview_controls_group)
        preview_controls_layout.setContentsMargins(10, 8, 10, 8)
        preview_controls_layout.setSpacing(12)

        # Add "Show Preview Window" button
        show_preview_btn = QPushButton("Show Preview")
        show_preview_btn.setMinimumWidth(140)
        show_preview_btn.clicked.connect(self.show_preview_window)
        preview_controls_layout.addWidget(show_preview_btn)

        # Add "Update Preview" button
        update_preview_btn = QPushButton("Update")
        update_preview_btn.setMinimumWidth(140)
        update_preview_btn.clicked.connect(self.update_preview_from_reference)
        preview_controls_layout.addWidget(update_preview_btn)

        preview_controls_layout.addStretch()

        # Add "Contour Mode" checkbox
        self.contour_video_enabled = QCheckBox("Contour Mode")
        self.contour_video_enabled.setChecked(False)
        self.contour_video_enabled.stateChanged.connect(self.toggle_contour_mode)
        preview_controls_layout.addWidget(self.contour_video_enabled)

        # Add preview controls to the main layout first
        main_layout.addWidget(preview_controls_group)

        # ------ Input Tab ------
        # Create a scroll area for the input tab
        input_scroll = QScrollArea()
        input_scroll.setWidgetResizable(True)
        input_scroll_content = QWidget()
        input_layout = QVBoxLayout(input_scroll_content)
        input_layout.setContentsMargins(12, 12, 12, 12)
        input_layout.setSpacing(12)
        input_scroll.setWidget(input_scroll_content)

        # Input pattern section
        input_group = QGroupBox("Input Files")
        input_group_layout = QGridLayout(input_group)
        input_group_layout.setContentsMargins(12, 16, 12, 12)
        input_group_layout.setHorizontalSpacing(10)
        input_group_layout.setVerticalSpacing(8)

        # 1. Directory field
        input_group_layout.addWidget(QLabel("Input Directory:"), 0, 0)
        self.input_directory_edit = QLineEdit()
        input_group_layout.addWidget(self.input_directory_edit, 0, 1)

        browse_dir_btn = QPushButton("Browse")
        browse_dir_btn.clicked.connect(
            lambda: (
                (
                    self.input_directory_edit.setText(os.getcwd())
                    if not self.input_directory_edit.text()
                    else None
                ),
                self.browse_input_directory(),
            )
        )
        input_group_layout.addWidget(browse_dir_btn, 0, 2)

        # 2. Pattern field
        input_group_layout.addWidget(QLabel("File Pattern:"), 1, 0)
        self.input_pattern_edit = QLineEdit()
        self.input_pattern_edit.setPlaceholderText("e.g., *.fits or *_171*.fits")
        input_group_layout.addWidget(self.input_pattern_edit, 1, 1)

        # Scan button
        scan_btn = QPushButton("Scan")
        scan_btn.clicked.connect(self.preview_input_files)
        input_group_layout.addWidget(scan_btn, 1, 2)

        # File sorting
        input_group_layout.addWidget(QLabel("Sort Files By:"), 2, 0)
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["Filename", "Date/Time", "Extension"])
        input_group_layout.addWidget(self.sort_combo, 2, 1)

        # Stokes parameter
        input_group_layout.addWidget(QLabel("Stokes Parameter:"), 3, 0)
        self.stokes_combo = QComboBox()
        self.stokes_combo.addItems(["I", "Q", "U", "V"])
        self.stokes_combo.currentIndexChanged.connect(self.update_preview_settings)
        input_group_layout.addWidget(self.stokes_combo, 3, 1)

        input_layout.addWidget(input_group)

        # Files found status (separate from the group) - use theme-compatible styling
        self.files_found_label = QLabel("No files found yet")
        self.files_found_label.setObjectName("StatusLabel")
        input_layout.addWidget(self.files_found_label)
        input_layout.addStretch()

        # ------ Display Tab ------
        # Create a scroll area for the display tab
        display_scroll = QScrollArea()
        display_scroll.setWidgetResizable(True)
        display_scroll_content = QWidget()
        display_layout = QVBoxLayout(display_scroll_content)
        display_layout.setContentsMargins(12, 12, 12, 12)
        display_layout.setSpacing(12)
        display_scroll.setWidget(display_scroll_content)

        # Reference image for display settings
        reference_group = QGroupBox("Reference Image")
        reference_layout = QGridLayout(reference_group)
        reference_layout.setContentsMargins(12, 16, 12, 12)
        reference_layout.setHorizontalSpacing(10)
        reference_layout.setVerticalSpacing(8)

        reference_layout.addWidget(QLabel("Reference Image:"), 0, 0)
        self.reference_image_edit = QLineEdit()
        self.reference_image_edit.setReadOnly(True)  # Make it read-only
        reference_layout.addWidget(self.reference_image_edit, 0, 1)

        browse_reference_btn = QPushButton("Browse")
        browse_reference_btn.clicked.connect(self.browse_reference_image)
        reference_layout.addWidget(browse_reference_btn, 0, 2)

        display_layout.addWidget(reference_group)

        # Visualization settings section
        viz_group = QGroupBox("Visualization")
        viz_form = QFormLayout(viz_group)
        viz_form.setContentsMargins(12, 16, 12, 12)
        viz_form.setVerticalSpacing(8)
        viz_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        # Colormap
        self.colormap_combo = QComboBox()
        colormaps = sorted(
            [cmap for cmap in plt.colormaps() if not cmap.endswith("_r")]
        )
        self.colormap_combo.addItems(colormaps)
        idx = self.colormap_combo.findText("viridis")
        if idx >= 0:
            self.colormap_combo.setCurrentIndex(idx)
        self.colormap_combo.currentIndexChanged.connect(self.update_preview_settings)
        viz_form.addRow("Colormap:", self.colormap_combo)

        # Stretch
        self.stretch_combo = QComboBox()
        self.stretch_combo.addItems(
            [
                "Linear",
                "Log",
                "Sqrt",
                "Power",
                "Arcsinh",
                "ZScale",
                "Histogram Equalization",
            ]
        )
        self.stretch_combo.setItemData(
            0, "Linear stretch - no transformation", Qt.ToolTipRole
        )
        self.stretch_combo.setItemData(
            1, "Logarithmic stretch - enhances very faint features", Qt.ToolTipRole
        )
        self.stretch_combo.setItemData(
            2, "Square root stretch - enhances faint features", Qt.ToolTipRole
        )
        self.stretch_combo.setItemData(
            3, "Power law stretch - adjustable using gamma", Qt.ToolTipRole
        )
        self.stretch_combo.setItemData(
            4,
            "Arcsinh stretch - similar to log but handles negative values",
            Qt.ToolTipRole,
        )
        self.stretch_combo.setItemData(
            5,
            "ZScale stretch - automatic contrast based on image statistics",
            Qt.ToolTipRole,
        )
        self.stretch_combo.setItemData(
            6,
            "Histogram equalization - enhances contrast by redistributing intensities",
            Qt.ToolTipRole,
        )
        self.stretch_combo.currentIndexChanged.connect(self.update_preview_settings)
        self.stretch_combo.currentIndexChanged.connect(self.update_gamma_controls)
        viz_form.addRow("Stretch:", self.stretch_combo)

        # Gamma (for power stretch)
        self.gamma_spinbox = QDoubleSpinBox()
        self.gamma_spinbox.setRange(0.1, 10.0)
        self.gamma_spinbox.setSingleStep(0.1)
        self.gamma_spinbox.setValue(1.0)
        self.gamma_spinbox.valueChanged.connect(self.update_preview_settings)
        viz_form.addRow("Gamma:", self.gamma_spinbox)

        # Colorbar option
        self.colorbar_check = QCheckBox("Show Colorbar")
        self.colorbar_check.setChecked(True)
        self.colorbar_check.stateChanged.connect(self.update_preview_settings)
        viz_form.addRow("", self.colorbar_check)

        display_layout.addWidget(viz_group)

        # Range settings section
        range_group = QGroupBox("Range Scaling")
        range_form = QFormLayout(range_group)
        range_form.setContentsMargins(12, 16, 12, 12)
        range_form.setVerticalSpacing(8)
        range_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        # Range mode selection
        self.range_mode_combo = QComboBox()
        self.range_mode_combo.addItems(["Fixed Range", "Auto per Frame", "Global Auto"])
        self.range_mode_combo.setCurrentIndex(1)  # Default to "Auto per Frame"
        self.range_mode_combo.setToolTip(
            "Fixed Range: Use the min/max values specified below for all frames\n"
            "Auto per Frame: Calculate min/max independently for each frame based on percentiles\n"
            "Global Auto: Calculate min/max once from all frames based on percentiles"
        )
        self.range_mode_combo.currentIndexChanged.connect(self.toggle_range_mode)
        range_form.addRow("Mode:", self.range_mode_combo)

        # Add explanatory label
        """self.range_explanation_label = QLabel(
            "Auto Per Frame: Min/max calculated independently for each frame"
        )
        self.range_explanation_label.setObjectName("SecondaryText")
        range_form.addRow("", self.range_explanation_label)"""

        # Min/Max values in a horizontal layout
        minmax_widget = QWidget()
        minmax_layout = QHBoxLayout(minmax_widget)
        minmax_layout.setContentsMargins(0, 0, 0, 0)
        minmax_layout.setSpacing(10)

        minmax_layout.addWidget(QLabel("Min:"))
        self.vmin_spinbox = QDoubleSpinBox()
        self.vmin_spinbox.setRange(-1e10, 1e10)
        self.vmin_spinbox.setDecimals(2)
        self.vmin_spinbox.setValue(0)
        self.vmin_spinbox.valueChanged.connect(self.update_preview_settings)
        minmax_layout.addWidget(self.vmin_spinbox)

        minmax_layout.addWidget(QLabel("Max:"))
        self.vmax_spinbox = QDoubleSpinBox()
        self.vmax_spinbox.setRange(-1e10, 1e10)
        self.vmax_spinbox.setDecimals(2)
        self.vmax_spinbox.setValue(3000)
        self.vmax_spinbox.valueChanged.connect(self.update_preview_settings)
        minmax_layout.addWidget(self.vmax_spinbox)

        range_form.addRow("Values:", minmax_widget)

        display_layout.addWidget(range_group)

        # Frame settings section
        frame_group = QGroupBox("Frame Settings")
        frame_form = QFormLayout(frame_group)
        frame_form.setContentsMargins(12, 16, 12, 12)
        frame_form.setVerticalSpacing(8)
        frame_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        # Frame resize in a horizontal layout
        size_widget = QWidget()
        size_layout = QHBoxLayout(size_widget)
        size_layout.setContentsMargins(0, 0, 0, 0)
        size_layout.setSpacing(10)

        size_layout.addWidget(QLabel("Width:"))
        self.width_spinbox = QSpinBox()
        self.width_spinbox.setRange(0, 7680)
        self.width_spinbox.setValue(0)
        self.width_spinbox.setSpecialValueText("Original")
        size_layout.addWidget(self.width_spinbox)

        size_layout.addWidget(QLabel("Height:"))
        self.height_spinbox = QSpinBox()
        self.height_spinbox.setRange(0, 4320)
        self.height_spinbox.setValue(0)
        self.height_spinbox.setSpecialValueText("Original")
        size_layout.addWidget(self.height_spinbox)

        frame_form.addRow("Size:", size_widget)

        display_layout.addWidget(frame_group)

        # WCS Coordinates section
        wcs_group = QGroupBox("Coordinate System")
        wcs_form = QFormLayout(wcs_group)
        wcs_form.setContentsMargins(12, 16, 12, 12)
        wcs_form.setVerticalSpacing(8)

        self.wcs_coords_check = QCheckBox("Show WCS Coordinates in Video")
        self.wcs_coords_check.setChecked(True)
        self.wcs_coords_check.setToolTip(
            "Display RA/Dec or Solar-X/Solar-Y coordinates instead of pixels"
        )
        wcs_form.addRow("", self.wcs_coords_check)

        self.wcs_info_label = QLabel("Coordinates will be detected from FITS header")
        self.wcs_info_label.setObjectName("SecondaryText")
        wcs_form.addRow("", self.wcs_info_label)

        display_layout.addWidget(wcs_group)

        # Add preset buttons similar to main application
        presets_group = QGroupBox("Display Presets")
        presets_layout = QGridLayout(presets_group)

        # Auto range presets
        auto_minmax_btn = QPushButton("Auto Min/Max")
        auto_minmax_btn.clicked.connect(self.apply_auto_minmax)
        presets_layout.addWidget(auto_minmax_btn, 0, 0)

        auto_percentile_btn = QPushButton("Auto Percentile (1-99%)")
        auto_percentile_btn.clicked.connect(self.apply_auto_percentile)
        presets_layout.addWidget(auto_percentile_btn, 0, 1)

        """auto_median_btn = QPushButton("Auto Median±3×RMS")
        auto_median_btn.clicked.connect(self.apply_auto_median_rms)
        presets_layout.addWidget(auto_median_btn, 1, 0)"""

        # AIA/HMI Presets
        aia_preset_btn = QPushButton("AIA 171Å Preset")
        aia_preset_btn.clicked.connect(self.apply_aia_preset)
        presets_layout.addWidget(aia_preset_btn, 1, 0)

        hmi_preset_btn = QPushButton("HMI Preset")
        hmi_preset_btn.clicked.connect(self.apply_hmi_preset)
        presets_layout.addWidget(hmi_preset_btn, 1, 1)

        display_layout.addWidget(presets_group)
        display_layout.addStretch()

        # ------ Region Tab ------
        # Create a scroll area for the region tab
        region_scroll = QScrollArea()
        region_scroll.setWidgetResizable(True)
        region_scroll_content = QWidget()
        region_layout = QVBoxLayout(region_scroll_content)
        region_layout.setContentsMargins(12, 12, 12, 12)
        region_layout.setSpacing(12)
        region_scroll.setWidget(region_scroll_content)

        region_group = QGroupBox("Region Selection")
        region_main_layout = QVBoxLayout(region_group)
        region_main_layout.setContentsMargins(12, 16, 12, 12)
        region_main_layout.setSpacing(12)

        # Enable region selection
        self.region_enabled = QCheckBox("Enable Region Selection (Zoomed Video)")
        self.region_enabled.setChecked(False)
        self.region_enabled.stateChanged.connect(self.toggle_region_controls)
        region_main_layout.addWidget(self.region_enabled)

        # Coordinate inputs using form layout
        coord_form = QFormLayout()
        coord_form.setVerticalSpacing(8)
        coord_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        # X range
        x_range_widget = QWidget()
        x_range_layout = QHBoxLayout(x_range_widget)
        x_range_layout.setContentsMargins(0, 0, 0, 0)
        x_range_layout.setSpacing(8)

        self.x_min_spinbox = QSpinBox()
        self.x_min_spinbox.setRange(0, 10000)
        self.x_min_spinbox.setValue(0)
        self.x_min_spinbox.valueChanged.connect(self.update_region_preview)
        x_range_layout.addWidget(self.x_min_spinbox)
        x_range_layout.addWidget(QLabel("to"))
        self.x_max_spinbox = QSpinBox()
        self.x_max_spinbox.setRange(0, 10000)
        self.x_max_spinbox.setValue(1000)
        self.x_max_spinbox.valueChanged.connect(self.update_region_preview)
        x_range_layout.addWidget(self.x_max_spinbox)
        x_range_layout.addWidget(QLabel("px"))
        x_range_layout.addStretch()
        coord_form.addRow("X Range:", x_range_widget)

        # Y range
        y_range_widget = QWidget()
        y_range_layout = QHBoxLayout(y_range_widget)
        y_range_layout.setContentsMargins(0, 0, 0, 0)
        y_range_layout.setSpacing(8)

        self.y_min_spinbox = QSpinBox()
        self.y_min_spinbox.setRange(0, 10000)
        self.y_min_spinbox.setValue(0)
        self.y_min_spinbox.valueChanged.connect(self.update_region_preview)
        y_range_layout.addWidget(self.y_min_spinbox)
        y_range_layout.addWidget(QLabel("to"))
        self.y_max_spinbox = QSpinBox()
        self.y_max_spinbox.setRange(0, 10000)
        self.y_max_spinbox.setValue(1000)
        self.y_max_spinbox.valueChanged.connect(self.update_region_preview)
        y_range_layout.addWidget(self.y_max_spinbox)
        y_range_layout.addWidget(QLabel("px"))
        y_range_layout.addStretch()
        coord_form.addRow("Y Range:", y_range_widget)

        region_main_layout.addLayout(coord_form)

        # Interactive selection button
        select_from_preview_btn = QPushButton("Select Region from Preview...")
        select_from_preview_btn.clicked.connect(self.select_region_from_preview)
        region_main_layout.addWidget(select_from_preview_btn)

        region_layout.addWidget(region_group)

        # Presets group
        presets_group = QGroupBox("Quick Presets")
        presets_layout = QHBoxLayout(presets_group)
        presets_layout.setContentsMargins(12, 16, 12, 12)
        presets_layout.setSpacing(10)

        center_25_btn = QPushButton("Center 25%")
        center_25_btn.clicked.connect(lambda: self.set_region_preset(0.25))
        presets_layout.addWidget(center_25_btn)

        center_50_btn = QPushButton("Center 50%")
        center_50_btn.clicked.connect(lambda: self.set_region_preset(0.5))
        presets_layout.addWidget(center_50_btn)

        center_75_btn = QPushButton("Center 75%")
        center_75_btn.clicked.connect(lambda: self.set_region_preset(0.75))
        presets_layout.addWidget(center_75_btn)

        region_layout.addWidget(presets_group)

        # Help text at the bottom
        help_label = QLabel(
            "Create a video focused on a specific region of interest. "
            "The selected region will be shown with a red rectangle in the preview."
        )
        help_label.setWordWrap(True)
        help_label.setObjectName("SecondaryText")
        region_layout.addWidget(help_label)

        # Initially disable the region controls
        self.toggle_region_controls(False)

        region_layout.addStretch()

        # ------ Overlay Tab ------
        # Create a scroll area for the overlay tab
        overlay_scroll = QScrollArea()
        overlay_scroll.setWidgetResizable(True)
        overlay_scroll_content = QWidget()
        overlay_layout = QVBoxLayout(overlay_scroll_content)
        overlay_layout.setContentsMargins(12, 12, 12, 12)
        overlay_layout.setSpacing(12)
        overlay_scroll.setWidget(overlay_scroll_content)

        # Overlay settings section
        overlay_group = QGroupBox("Overlay Settings")
        overlay_main_layout = QHBoxLayout(overlay_group)
        overlay_main_layout.setContentsMargins(12, 16, 12, 12)

        # Use a form layout for cleaner organization
        overlay_form = QFormLayout()
        overlay_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        # Timestamp, Frame number, and Filename as checkboxes with labels
        self.timestamp_check = QCheckBox("Add Timestamp")
        self.timestamp_check.setChecked(True)
        self.timestamp_check.setToolTip("Show date/time information from FITS header")
        overlay_form.addRow("", self.timestamp_check)

        self.frame_number_check = QCheckBox("Add Frame Number")
        self.frame_number_check.setChecked(False)
        self.frame_number_check.setToolTip("Show frame counter (e.g., Frame: 1/100)")
        overlay_form.addRow("", self.frame_number_check)

        self.filename_check = QCheckBox("Add Filename")
        self.filename_check.setChecked(False)
        self.filename_check.setToolTip("Show source filename in the video frame")
        overlay_form.addRow("", self.filename_check)

        overlay_main_layout.addLayout(overlay_form)
        overlay_layout.addWidget(overlay_group)

        # Min/Max Timeline section
        timeline_group = QGroupBox("Min/Max Timeline")
        timeline_layout = QVBoxLayout(timeline_group)
        timeline_layout.setContentsMargins(12, 16, 12, 12)
        timeline_layout.setSpacing(8)

        self.minmax_timeline_check = QCheckBox("Show Min/Max Timeline Plot")
        self.minmax_timeline_check.setChecked(False)
        self.minmax_timeline_check.setToolTip(
            "Display a continuous line plot showing min/max values for all frames"
        )
        timeline_layout.addWidget(self.minmax_timeline_check)

        # Position selector
        """position_widget = QWidget()
        position_layout = QHBoxLayout(position_widget)
        position_layout.setContentsMargins(0, 0, 0, 0)
        position_layout.setSpacing(10)

        position_layout.addWidget(QLabel("Position:"))
        self.timeline_position_combo = QComboBox()
        self.timeline_position_combo.addItems([
            "Bottom Left", "Bottom Right", "Top Left", "Top Right"
        ])
        self.timeline_position_combo.setCurrentIndex(0)
        position_layout.addWidget(self.timeline_position_combo)
        position_layout.addStretch()
        timeline_layout.addWidget(position_widget)"""

        # Data source selector (for contour mode)
        source_widget = QWidget()
        source_layout = QHBoxLayout(source_widget)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.setSpacing(10)

        source_layout.addWidget(QLabel("Data Source:"))
        self.timeline_source_combo = QComboBox()
        self.timeline_source_combo.addItems(["Colormap", "Contours"])
        self.timeline_source_combo.setCurrentIndex(0)
        self.timeline_source_combo.setToolTip(
            "Select which data to plot (applies in contour mode)"
        )
        source_layout.addWidget(self.timeline_source_combo)
        source_layout.addStretch()
        timeline_layout.addWidget(source_widget)

        # Log scale option
        self.timeline_log_scale_check = QCheckBox("Use Log Scale")
        self.timeline_log_scale_check.setChecked(False)
        self.timeline_log_scale_check.setToolTip("Plot values on logarithmic scale")
        timeline_layout.addWidget(self.timeline_log_scale_check)

        timeline_info = QLabel(
            "Plots min (blue) and max (orange) pixel values over time"
        )
        timeline_info.setObjectName("SecondaryText")
        timeline_layout.addWidget(timeline_info)

        overlay_layout.addWidget(timeline_group)

        # Add spacer to push controls to the top
        overlay_layout.addStretch()

        # ------ Output Tab ------
        # Create a scroll area for the output tab
        output_scroll = QScrollArea()
        output_scroll.setWidgetResizable(True)
        output_scroll_content = QWidget()
        output_layout = QVBoxLayout(output_scroll_content)
        output_layout.setContentsMargins(12, 12, 12, 12)
        output_layout.setSpacing(12)
        output_scroll.setWidget(output_scroll_content)

        # File output section
        file_group = QGroupBox("Output File")
        file_form = QFormLayout(file_group)
        file_form.setContentsMargins(12, 16, 12, 12)
        file_form.setVerticalSpacing(8)
        file_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        file_widget = QWidget()
        file_layout = QHBoxLayout(file_widget)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(8)

        self.output_file_edit = QLineEdit()
        file_layout.addWidget(self.output_file_edit)

        browse_output_btn = QPushButton("Browse")
        browse_output_btn.clicked.connect(self.browse_output_file)
        file_layout.addWidget(browse_output_btn)

        file_form.addRow("Path:", file_widget)
        output_layout.addWidget(file_group)

        # Video settings section
        video_group = QGroupBox("Video Settings")
        video_form = QFormLayout(video_group)
        video_form.setContentsMargins(12, 16, 12, 12)
        video_form.setVerticalSpacing(8)
        video_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.fps_spinbox = QSpinBox()
        self.fps_spinbox.setRange(1, 60)
        self.fps_spinbox.setValue(10)
        video_form.addRow("Frames Per Second:", self.fps_spinbox)

        quality_widget = QWidget()
        quality_layout = QHBoxLayout(quality_widget)
        quality_layout.setContentsMargins(0, 0, 0, 0)
        quality_layout.setSpacing(10)

        self.quality_spinbox = QSpinBox()
        self.quality_spinbox.setRange(1, 10)
        self.quality_spinbox.setValue(10)
        self.quality_spinbox.setToolTip(
            "Higher values mean better quality but larger file size"
        )
        quality_layout.addWidget(self.quality_spinbox)

        quality_label = QLabel("(1=low, 10=high)")
        quality_label.setObjectName("SecondaryText")
        quality_layout.addWidget(quality_label)
        quality_layout.addStretch()

        video_form.addRow("Quality:", quality_widget)
        output_layout.addWidget(video_group)

        # Performance section
        perf_group = QGroupBox("Performance")
        perf_form = QFormLayout(perf_group)
        perf_form.setContentsMargins(12, 16, 12, 12)
        perf_form.setVerticalSpacing(8)
        perf_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.multiprocessing_check = QCheckBox("Use Multiprocessing")
        self.multiprocessing_check.setChecked(True)
        perf_form.addRow("", self.multiprocessing_check)

        max_cores = multiprocessing.cpu_count()
        self.cores_spinbox = QSpinBox()
        self.cores_spinbox.setRange(1, max_cores)
        self.cores_spinbox.setValue(max(1, max_cores - 1))
        self.cores_spinbox.setEnabled(self.multiprocessing_check.isChecked())
        perf_form.addRow("CPU Cores:", self.cores_spinbox)

        self.multiprocessing_check.stateChanged.connect(
            lambda state: self.cores_spinbox.setEnabled(state == Qt.Checked)
        )

        output_layout.addWidget(perf_group)
        output_layout.addStretch()

        # ------ Contours Tab ------
        # Create a scroll area for the contours tab
        contours_scroll = QScrollArea()
        contours_scroll.setWidgetResizable(True)
        contours_scroll_content = QWidget()
        contours_layout = QVBoxLayout(contours_scroll_content)
        contours_layout.setContentsMargins(12, 12, 12, 12)
        contours_layout.setSpacing(12)
        contours_scroll.setWidget(contours_scroll_content)

        # Enable contours
        contours_enable_group = QGroupBox("Contour Video")
        contours_enable_layout = QVBoxLayout(contours_enable_group)
        contours_enable_layout.setContentsMargins(12, 16, 12, 12)
        contours_enable_layout.setSpacing(8)

        # Mode selector
        mode_widget = QWidget()
        mode_layout = QHBoxLayout(mode_widget)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(10)

        mode_layout.addWidget(QLabel("Mode:"))
        self.contour_mode_combo = QComboBox()
        self.contour_mode_combo.addItems(
            [
                "A: Fixed base, evolving contours",
                "B: Fixed contours, evolving colormap",
                "C: Both evolve",
            ]
        )
        self.contour_mode_combo.currentIndexChanged.connect(self.update_contour_mode_ui)
        self.contour_mode_combo.currentIndexChanged.connect(
            self.update_create_button_state
        )
        mode_layout.addWidget(self.contour_mode_combo)
        mode_layout.addStretch()

        contours_enable_layout.addWidget(mode_widget)
        contours_layout.addWidget(contours_enable_group)

        # File inputs group (changes based on mode)
        self.contour_files_group = QGroupBox("File Selection")
        contour_files_layout = QFormLayout(self.contour_files_group)
        contour_files_layout.setContentsMargins(12, 16, 12, 12)
        contour_files_layout.setVerticalSpacing(8)
        contour_files_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        # Base image file (for mode A)
        base_file_widget = QWidget()
        base_file_layout = QHBoxLayout(base_file_widget)
        base_file_layout.setContentsMargins(0, 0, 0, 0)
        base_file_layout.setSpacing(8)
        self.contour_base_file_edit = QLineEdit()
        self.contour_base_file_edit.setPlaceholderText("Select base image file...")
        self.contour_base_file_edit.textChanged.connect(self.update_create_button_state)
        base_file_layout.addWidget(self.contour_base_file_edit)
        self.contour_base_file_btn = QPushButton("Browse")
        self.contour_base_file_btn.clicked.connect(self.browse_contour_base_file)
        base_file_layout.addWidget(self.contour_base_file_btn)
        contour_files_layout.addRow("Base Image:", base_file_widget)

        # Contour directory (for modes A and C)
        contour_dir_widget = QWidget()
        contour_dir_layout = QHBoxLayout(contour_dir_widget)
        contour_dir_layout.setContentsMargins(0, 0, 0, 0)
        contour_dir_layout.setSpacing(8)
        self.contour_dir_edit = QLineEdit()
        self.contour_dir_edit.setPlaceholderText("Select contour files directory...")
        self.contour_dir_edit.textChanged.connect(self.scan_contour_files)
        self.contour_dir_edit.textChanged.connect(self.update_create_button_state)
        contour_dir_layout.addWidget(self.contour_dir_edit)
        self.contour_dir_btn = QPushButton("Browse")
        self.contour_dir_btn.clicked.connect(self.browse_contour_directory)
        contour_dir_layout.addWidget(self.contour_dir_btn)
        contour_files_layout.addRow("Contour Directory:", contour_dir_widget)

        # Contour file pattern
        self.contour_pattern_widget = QWidget()
        contour_pattern_layout = QHBoxLayout(self.contour_pattern_widget)
        contour_pattern_layout.setContentsMargins(0, 0, 0, 0)
        contour_pattern_layout.setSpacing(8)
        self.contour_dir_pattern_edit = QLineEdit("*.fits")
        self.contour_dir_pattern_edit.textChanged.connect(self.scan_contour_files)
        contour_pattern_layout.addWidget(self.contour_dir_pattern_edit)
        self.contour_files_count_label = QLabel("")
        self.contour_files_count_label.setObjectName("StatusLabel")
        contour_pattern_layout.addWidget(self.contour_files_count_label)
        contour_files_layout.addRow("Contour Pattern:", self.contour_pattern_widget)

        # Fixed contour file (for mode B)
        fixed_contour_widget = QWidget()
        fixed_contour_layout = QHBoxLayout(fixed_contour_widget)
        fixed_contour_layout.setContentsMargins(0, 0, 0, 0)
        fixed_contour_layout.setSpacing(8)
        self.contour_fixed_file_edit = QLineEdit()
        self.contour_fixed_file_edit.setPlaceholderText("Select fixed contour file...")
        self.contour_fixed_file_edit.textChanged.connect(
            self.update_create_button_state
        )
        fixed_contour_layout.addWidget(self.contour_fixed_file_edit)
        self.contour_fixed_file_btn = QPushButton("Browse")
        self.contour_fixed_file_btn.clicked.connect(self.browse_contour_fixed_file)
        fixed_contour_layout.addWidget(self.contour_fixed_file_btn)
        contour_files_layout.addRow("Fixed Contour:", fixed_contour_widget)

        # Colormap directory (for modes B and C)
        colormap_dir_widget = QWidget()
        colormap_dir_layout = QHBoxLayout(colormap_dir_widget)
        colormap_dir_layout.setContentsMargins(0, 0, 0, 0)
        colormap_dir_layout.setSpacing(8)
        self.contour_colormap_dir_edit = QLineEdit()
        self.contour_colormap_dir_edit.setPlaceholderText(
            "Select colormap files directory..."
        )
        self.contour_colormap_dir_edit.textChanged.connect(self.scan_colormap_files)
        self.contour_colormap_dir_edit.textChanged.connect(
            self.update_create_button_state
        )
        colormap_dir_layout.addWidget(self.contour_colormap_dir_edit)
        self.contour_colormap_dir_btn = QPushButton("Browse")
        self.contour_colormap_dir_btn.clicked.connect(
            self.browse_contour_colormap_directory
        )
        colormap_dir_layout.addWidget(self.contour_colormap_dir_btn)
        contour_files_layout.addRow("Colormap Directory:", colormap_dir_widget)

        # Colormap file pattern
        self.colormap_pattern_widget = QWidget()
        colormap_pattern_layout = QHBoxLayout(self.colormap_pattern_widget)
        colormap_pattern_layout.setContentsMargins(0, 0, 0, 0)
        colormap_pattern_layout.setSpacing(8)
        self.contour_colormap_pattern_edit = QLineEdit("*.fits")
        self.contour_colormap_pattern_edit.textChanged.connect(self.scan_colormap_files)
        colormap_pattern_layout.addWidget(self.contour_colormap_pattern_edit)
        self.colormap_files_count_label = QLabel("")
        self.colormap_files_count_label.setObjectName("StatusLabel")
        colormap_pattern_layout.addWidget(self.colormap_files_count_label)
        contour_files_layout.addRow("Colormap Pattern:", self.colormap_pattern_widget)

        contours_layout.addWidget(self.contour_files_group)

        # Contour settings group
        contour_settings_group = QGroupBox("Contour Settings")
        contour_settings_layout = QFormLayout(contour_settings_group)
        contour_settings_layout.setContentsMargins(12, 16, 12, 12)
        contour_settings_layout.setVerticalSpacing(8)
        contour_settings_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        # Level type
        self.contour_level_type_combo = QComboBox()
        self.contour_level_type_combo.addItems(
            ["Fraction of Max", "Sigma (RMS)", "Absolute"]
        )
        contour_settings_layout.addRow("Level Type:", self.contour_level_type_combo)

        # Positive levels
        self.contour_pos_levels_edit = QLineEdit("0.1, 0.3, 0.5, 0.7, 0.9")
        self.contour_pos_levels_edit.setToolTip(
            "Comma-separated list of positive contour levels"
        )
        contour_settings_layout.addRow("Positive Levels:", self.contour_pos_levels_edit)

        # Negative levels
        self.contour_neg_levels_edit = QLineEdit("0.1, 0.3, 0.5, 0.7, 0.9")
        self.contour_neg_levels_edit.setToolTip(
            "Comma-separated list of negative contour levels"
        )
        contour_settings_layout.addRow("Negative Levels:", self.contour_neg_levels_edit)

        # Colors
        color_widget = QWidget()
        color_layout = QHBoxLayout(color_widget)
        color_layout.setContentsMargins(0, 0, 0, 0)
        color_layout.setSpacing(10)

        color_layout.addWidget(QLabel("Pos:"))
        self.contour_pos_color_combo = QComboBox()
        self.contour_pos_color_combo.addItems(
            ["white", "red", "yellow", "green", "cyan", "blue", "magenta"]
        )
        color_layout.addWidget(self.contour_pos_color_combo)

        color_layout.addWidget(QLabel("Neg:"))
        self.contour_neg_color_combo = QComboBox()
        self.contour_neg_color_combo.addItems(
            ["cyan", "blue", "red", "yellow", "green", "white", "magenta"]
        )
        color_layout.addWidget(self.contour_neg_color_combo)
        color_layout.addStretch()

        contour_settings_layout.addRow("Colors:", color_widget)

        # Line width
        self.contour_linewidth_spin = QDoubleSpinBox()
        self.contour_linewidth_spin.setRange(0.1, 5.0)
        self.contour_linewidth_spin.setValue(1.0)
        self.contour_linewidth_spin.setSingleStep(0.1)
        contour_settings_layout.addRow("Line Width:", self.contour_linewidth_spin)

        contours_layout.addWidget(contour_settings_group)
        contours_layout.addStretch()

        # Initially disable contour controls
        self.toggle_contour_video_controls(False)
        self.update_contour_mode_ui(0)

        # Add tabs to tab widget (Contours is second when in Contour Mode)
        self.tab_widget.addTab(input_scroll, "Input")
        self.tab_widget.addTab(contours_scroll, "Input")
        self.tab_widget.addTab(display_scroll, "Display")
        self.tab_widget.addTab(region_scroll, "Region")
        self.tab_widget.addTab(overlay_scroll, "Overlays")
        self.tab_widget.addTab(output_scroll, "Output")

        # Add the tab widget to the main layout (after the preview)
        main_layout.addWidget(self.tab_widget)

        # Hide Contours tab by default (shown when Contour Mode checkbox is checked)
        self.tab_widget.setTabVisible(1, False)

        # Buttons at the bottom
        button_layout = QHBoxLayout()
        button_layout.setSpacing(12)
        button_layout.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setMinimumWidth(100)
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)

        self.create_btn = QPushButton("Create Video")
        self.create_btn.setMinimumWidth(120)
        self.create_btn.setObjectName("PrimaryButton")
        self.create_btn.clicked.connect(self.create_video)
        self.create_btn.setEnabled(False)
        button_layout.addWidget(self.create_btn)

        main_layout.addLayout(button_layout)

    def toggle_range_mode(self, index):
        """Enable/disable controls based on the range mode selection"""
        # Update the explanation label
        if index == 0:  # Fixed Range
            """self.range_explanation_label.setText(
                "Fixed Range: Same min/max values used for all frames"
            )"""
            # Enable min/max spinboxes
            self.vmin_spinbox.setEnabled(True)
            self.vmax_spinbox.setEnabled(True)

        elif index == 1:  # Auto Per Frame
            """self.range_explanation_label.setText(
                "Auto Per Frame: Min/max calculated independently for each frame"
            )"""
            # Disable min/max spinboxes (they'll be updated for reference only)
            self.vmin_spinbox.setEnabled(False)
            self.vmax_spinbox.setEnabled(False)

        else:  # Global Auto
            """self.range_explanation_label.setText(
                "Global Auto: Min/max calculated once from all frames"
            )"""
            # Disable min/max spinboxes
            self.vmin_spinbox.setEnabled(False)
            self.vmax_spinbox.setEnabled(False)

        # Update preview with new settings
        self.update_preview()

    def get_smart_start_directory(self, *extra_paths):
        """
        Get a smart starting directory for file/directory dialogs.

        Checks multiple sources in priority order:
        1. Any extra paths passed as arguments (e.g., current field value)
        2. Contour base file directory (if set)
        3. Reference image directory
        4. Input directory from Input tab
        5. Current file from main viewer
        6. Current working directory (CWD)
        7. Fall back to home directory

        Returns the first valid directory found.
        """
        candidates = list(extra_paths) + [
            (
                self.contour_base_file_edit.text().strip()
                if hasattr(self, "contour_base_file_edit")
                else None
            ),
            self.reference_image,
            (
                self.input_directory_edit.text().strip()
                if hasattr(self, "input_directory_edit")
                else None
            ),
            self.current_file,
            os.getcwd(),  # CWD as fallback before home
        ]

        for path in candidates:
            if path:
                # If it's a file, get its directory
                if os.path.isfile(path):
                    dir_path = os.path.dirname(path)
                else:
                    dir_path = path

                if dir_path and os.path.isdir(dir_path):
                    return dir_path

        return os.path.expanduser("~")

    def browse_input_directory(self):
        """Browse for input directory"""
        start_dir = self.get_smart_start_directory(self.input_directory_edit.text())

        directory = QFileDialog.getExistingDirectory(
            self, "Select Input Directory", start_dir
        )

        if directory:
            self.input_directory_edit.setText(directory)

            # Set default output file if not already set
            if not self.output_file_edit.text():
                self.output_file_edit.setText(
                    os.path.join(directory, "output_video.mp4")
                )

            # Preview files if pattern is already set
            if self.input_pattern_edit.text():
                self.preview_input_files()

    def preview_input_files(self):
        """Preview the files matching the input pattern"""
        with wait_cursor():
            directory = self.input_directory_edit.text()
            pattern = self.input_pattern_edit.text()

            if not directory or not pattern:
                QMessageBox.warning(
                    self,
                    "Incomplete Input",
                    "Please specify both directory and pattern.",
                )
                return

            full_pattern = os.path.join(directory, pattern)

            # Find matching files
            files = glob.glob(full_pattern)

            if not files:
                self.files_found_label.setText("No files found matching the pattern")
                self.create_btn.setEnabled(False)
                self.create_btn.setToolTip("No files found matching the pattern")
                QMessageBox.warning(
                    self,
                    "No Files Found",
                    f"No files match the pattern: {full_pattern}",
                )
                return

            # Validate extensions
            invalid_extensions = []
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext not in [".fits", ".fts"]:
                    invalid_extensions.append(os.path.basename(f))

            if invalid_extensions:
                self.files_found_label.setText(
                    f"Found {len(files)} files, but {len(invalid_extensions)} have invalid extensions"
                )
                self.create_btn.setEnabled(False)
                self.create_btn.setToolTip("Invalid file extensions found")

                # Show first few invalid files
                msg = f"Found {len(invalid_extensions)} files with invalid extensions.\nOnly .fits and .fts files are supported."
                if len(invalid_extensions) > 5:
                    msg += (
                        f"\n\nExamples:\n" + "\n".join(invalid_extensions[:5]) + "\n..."
                    )
                else:
                    msg += f"\n\nFiles:\n" + "\n".join(invalid_extensions)

                QMessageBox.warning(self, "Invalid File Extensions", msg)
                return

            # Update label with file count
            self.files_found_label.setText(
                f"Found {len(files)} files matching the pattern"
            )
            self.create_btn.setEnabled(True)
            self.create_btn.setToolTip("")

            # Always update the reference list and force a preview refresh
            # Use first file as reference if no reference is set, or if we want to refresh
            if not self.reference_image or self.reference_image not in files:
                self.reference_image = files[0]
                self.reference_image_edit.setText(self.reference_image)

            # Explicitly update preview to satisfy user request "Refresh the figure, each time user presses 'preview files'"
            self.update_preview(self.reference_image)

    def browse_reference_image(self):
        """Browse for a reference image to use for preview and settings"""
        if self.reference_image:
            start_dir = os.path.dirname(self.reference_image)
        elif self.input_directory_edit.text():
            start_dir = self.input_directory_edit.text()
        elif self.current_file:
            start_dir = os.path.dirname(self.current_file)
        else:
            start_dir = os.path.expanduser("~")

        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "Select Reference Image",
            start_dir,
            "FITS Files (*.fits *.fit);;All Files (*.*)",
        )

        if filepath:
            self.reference_image = filepath
            self.reference_image_edit.setText(filepath)
            self.update_preview(filepath)

    def browse_output_file(self):
        """Browse for output file"""
        if self.output_file_edit.text():
            start_dir = os.path.dirname(self.output_file_edit.text())
        elif self.input_directory_edit.text():
            start_dir = self.input_directory_edit.text()
        elif self.current_file:
            start_dir = os.path.dirname(self.current_file)
        else:
            start_dir = os.path.expanduser("~")

        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "Save Video As",
            start_dir,
            "MP4 Files (*.mp4);;AVI Files (*.avi);;GIF Files (*.gif);;All Files (*.*)",
        )

        if filepath:
            self.output_file_edit.setText(filepath)

    def update_preview_from_reference(self):
        """Load and update the preview from the reference image"""
        if self.reference_image:
            self.update_preview(self.reference_image)
        else:
            QMessageBox.warning(
                self, "No Reference Image", "Please select a reference image first."
            )

    def update_preview_settings(self):
        """Update the preview with new settings"""
        self.update_preview()

    @wait_cursor()
    def update_preview(self, preview_file=None):
        """Update the preview image"""
        try:
            # Clear the figure
            self.figure.clear()
            ax = self.figure.add_subplot(111)

            if not preview_file and self.reference_image:
                preview_file = self.reference_image

            if preview_file:
                # Load the data
                data, header = load_fits_data(
                    preview_file, stokes=self.stokes_combo.currentText()
                )

                if data is not None:
                    original_data = data.copy()  # Save original data for region overlay

                    # Apply region selection if enabled
                    if self.region_enabled.isChecked():
                        x_min = self.x_min_spinbox.value()
                        x_max = self.x_max_spinbox.value()
                        y_min = self.y_min_spinbox.value()
                        y_max = self.y_max_spinbox.value()

                        # Ensure proper order of min/max
                        x_min, x_max = min(x_min, x_max), max(x_min, x_max)
                        y_min, y_max = min(y_min, y_max), max(y_min, y_max)

                        # Check boundaries
                        x_min = max(0, min(x_min, data.shape[1] - 1))
                        x_max = max(0, min(x_max, data.shape[1] - 1))
                        y_min = max(0, min(y_min, data.shape[0] - 1))
                        y_max = max(0, min(y_max, data.shape[0] - 1))

                        # Extract the region
                        data = data[y_min : y_max + 1, x_min : x_max + 1]

                    # Determine vmin/vmax based on range mode
                    range_mode = self.range_mode_combo.currentIndex()

                    if range_mode == 0:  # Fixed Range
                        vmin = self.vmin_spinbox.value()
                        vmax = self.vmax_spinbox.value()
                    else:  # Auto
                        vmin = np.nanpercentile(data, 0)
                        vmax = np.nanpercentile(data, 100)

                        # Update spinboxes for reference (without triggering events)
                        self.vmin_spinbox.blockSignals(True)
                        self.vmax_spinbox.blockSignals(True)
                        self.vmin_spinbox.setValue(vmin)
                        self.vmax_spinbox.setValue(vmax)
                        self.vmin_spinbox.blockSignals(False)
                        self.vmax_spinbox.blockSignals(False)

                    # Ensure min/max are proper
                    if vmin >= vmax:
                        vmax = vmin + 1.0

                    # Apply visualization settings
                    stretch = self.stretch_combo.currentText().lower()
                    gamma = self.gamma_spinbox.value()
                    cmap = self.colormap_combo.currentText()

                    # Create the appropriate normalization
                    norm = get_norm(stretch, vmin, vmax, gamma)

                    # Decide whether to show the full image or the region
                    display_data = data

                    # Show title with filename and region info if applicable
                    title = os.path.basename(preview_file)
                    if self.region_enabled.isChecked():
                        region_dims = f"{data.shape[1]}×{data.shape[0]}"
                        title += f" - Region: {region_dims} pixels"

                    title += f"\nRange: [{vmin:.1f}, {vmax:.1f}]"
                    ax.set_title(title, fontsize=10)

                    # Display the image
                    im = ax.imshow(
                        display_data,
                        cmap=cmap,
                        norm=norm,
                        origin="lower",
                        interpolation="none",
                    )

                    # If region is enabled and showing the preview,
                    # draw a red rectangle to indicate the region
                    if self.region_enabled.isChecked():
                        # Show the full image with a rectangle for the region
                        # Store current axes for restoring after showing full image
                        ax.set_xticks([])
                        ax.set_yticks([])

                        # Add a second axes to show full image with region overlay
                        # overlay_ax = self.figure.add_axes([0.65, 0.65, 0.3, 0.3])
                        overlay_ax = self.figure.add_axes([0.05, 0.05, 0.3, 0.3])
                        overlay_ax.imshow(
                            original_data,
                            cmap=cmap,
                            norm=norm,
                            origin="lower",
                            interpolation="none",
                        )

                        # Draw region rectangle on the overlay
                        x_min = self.x_min_spinbox.value()
                        x_max = self.x_max_spinbox.value()
                        y_min = self.y_min_spinbox.value()
                        y_max = self.y_max_spinbox.value()

                        # Ensure proper order
                        x_min, x_max = min(x_min, x_max), max(x_min, x_max)
                        y_min, y_max = min(y_min, y_max), max(y_min, y_max)

                        from matplotlib.patches import Rectangle

                        overlay_ax.add_patch(
                            Rectangle(
                                (x_min, y_min),
                                x_max - x_min,
                                y_max - y_min,
                                fill=False,
                                edgecolor="red",
                                linewidth=2,
                            )
                        )

                        # Turn off overlay axis labels and ticks
                        overlay_ax.set_xticks([])
                        overlay_ax.set_yticks([])
                        overlay_ax.set_title("Region Location", fontsize=8)

                    # Add colorbar if checked
                    if self.colorbar_check.isChecked():
                        cbar = self.figure.colorbar(im, ax=ax)

                    self.preview_image = preview_file

                    # Turn off axis labels
                    ax.set_xticks([])
                    ax.set_yticks([])
                else:
                    ax.text(
                        0.5,
                        0.5,
                        "Could not load preview image",
                        ha="center",
                        va="center",
                        transform=ax.transAxes,
                    )
            else:
                ax.text(
                    0.5,
                    0.5,
                    "No preview image available\nSelect a reference image first",
                    ha="center",
                    va="center",
                    transform=ax.transAxes,
                )

            # Refresh the canvas
            self.canvas.draw()

        except RuntimeError as re:
            # Handle specific Qt/Matplotlib runtime errors (deleted objects)
            print(f"RuntimeError updating preview: {re}")
            try:
                # If the figure/canvas is corrupted, try to recreate the figure content cleanly
                # Don't try self.figure.clear() if it caused the error

                # We interpret "wrapped C/C++ object of type QAction has been deleted"
                # as a sign that the toolbar or canvas state is invalid.
                # Simplest recovery is to just show an error text on a fresh axes if possible,
                # or just log it and return to avoid crashing.

                # Check if we can access the figure at all
                if hasattr(self, "figure"):
                    # Try to reset the figure completely
                    self.figure = Figure(figsize=(5, 4), dpi=100)
                    self.canvas.figure = self.figure
                    self.canvas.draw()

                    # Try to show error on new figure
                    ax = self.figure.add_subplot(111)
                    ax.text(
                        0.5, 0.5, "Preview Error (Recovered)", ha="center", va="center"
                    )
                    self.canvas.draw()
            except Exception as e2:
                print(f"Could not recover from preview error: {e2}")

        except Exception as e:
            print(f"Error updating preview: {e}")
            try:
                # Clear the figure
                self.figure.clear()
                ax = self.figure.add_subplot(111)
                ax.text(
                    0.5,
                    0.5,
                    f"Error loading preview: {str(e)}",
                    ha="center",
                    va="center",
                    transform=ax.transAxes,
                )
                self.canvas.draw()
            except Exception as e2:
                print(f"Error in exception handler: {e2}")

    def create_video(self):
        """Create a video from the selected files"""
        try:
            # Initialize matching_files - will be set differently for contour mode
            matching_files = []

            # Check if we're in contour mode - skip Input tab validation
            if not self.contour_video_enabled.isChecked():
                # Get input files from Input tab
                input_dir = self.input_directory_edit.text()
                if not input_dir or not os.path.isdir(input_dir):
                    QMessageBox.warning(
                        self,
                        "Invalid Directory",
                        "The specified input directory does not exist.",
                    )
                    return

                input_pattern = self.input_pattern_edit.text()
                input_path = os.path.join(input_dir, input_pattern)

                # Verify files exist
                matching_files = glob.glob(input_path)
                if not matching_files:
                    QMessageBox.warning(
                        self,
                        "No Files Found",
                        f"No files match the pattern: {input_path}",
                    )
                    return

                # Validate extensions
                invalid_extensions = []
                for f in matching_files:
                    ext = os.path.splitext(f)[1].lower()
                    if ext not in [".fits", ".fts"]:
                        invalid_extensions.append(os.path.basename(f))

                if invalid_extensions:
                    # Show first few invalid files
                    msg = f"Found {len(invalid_extensions)} files with invalid extensions.\nOnly .fits and .fts files are supported."
                    if len(invalid_extensions) > 5:
                        msg += (
                            f"\n\nExamples:\n"
                            + "\n".join(invalid_extensions[:5])
                            + "\n..."
                        )
                    else:
                        msg += f"\n\nFiles:\n" + "\n".join(invalid_extensions)

                    QMessageBox.warning(self, "Invalid File Extensions", msg)
                    return

                # Sort the files based on selected method
                sort_method = self.sort_combo.currentText().lower()
                if sort_method == "filename":
                    matching_files.sort()
                elif sort_method == "date/time":
                    matching_files.sort(key=os.path.getmtime)
                elif sort_method == "extension":
                    matching_files.sort(key=lambda x: os.path.splitext(x)[1])

            # Get output file
            output_file = self.output_file_edit.text().strip()
            if not output_file:
                QMessageBox.warning(
                    self,
                    "No Output File",
                    "Please specify an output file for the video.",
                )
                return

            # Normalize path (handle ~ and make absolute)
            output_file = os.path.abspath(os.path.expanduser(output_file))
            self.output_file_edit.setText(output_file)

            # Check if output directory is writable
            output_dir = os.path.dirname(output_file)
            if not output_dir:
                output_dir = os.getcwd()

            if not os.path.isdir(output_dir):
                try:
                    os.makedirs(output_dir)
                except OSError:
                    QMessageBox.critical(
                        self,
                        "Error",
                        f"Could not create output directory: {output_dir}",
                    )
                    return

            if not os.access(output_dir, os.W_OK):
                QMessageBox.critical(
                    self,
                    "Permission Denied",
                    f"Output directory is not writable: {output_dir}",
                )
                return

            # Confirm overwrite if file exists
            if os.path.exists(output_file):
                reply = QMessageBox.question(
                    self,
                    "Confirm Overwrite",
                    f"File already exists:\n{output_file}\n\nDo you want to overwrite it?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply == QMessageBox.No:
                    return

            # Get display options
            display_options = {
                "stokes": self.stokes_combo.currentText(),
                "colormap": self.colormap_combo.currentText(),
                "stretch": self.stretch_combo.currentText().lower(),
                "gamma": self.gamma_spinbox.value(),
                "range_mode": self.range_mode_combo.currentIndex(),  # 0: Fixed Range, 1: Auto Per Frame, 2: Global Auto
                "vmin": self.vmin_spinbox.value(),
                "vmax": self.vmax_spinbox.value(),
                "colorbar": self.colorbar_check.isChecked(),
                "width": self.width_spinbox.value(),
                "height": self.height_spinbox.value(),
                "wcs_enabled": self.wcs_coords_check.isChecked(),
            }

            # Get overlay options
            overlay_options = {
                "timestamp": self.timestamp_check.isChecked(),
                "frame_number": self.frame_number_check.isChecked(),
                "filename": self.filename_check.isChecked(),
                "minmax_timeline_enabled": self.minmax_timeline_check.isChecked(),
                "timeline_position": 0,  # Position selector removed - using default bottom dock
                "timeline_source": self.timeline_source_combo.currentIndex(),  # 0=Colormap, 1=Contours
                "timeline_log_scale": self.timeline_log_scale_check.isChecked(),
            }

            # Get region selection options
            region_options = {
                "region_enabled": self.region_enabled.isChecked(),
                "x_min": self.x_min_spinbox.value(),
                "x_max": self.x_max_spinbox.value(),
                "y_min": self.y_min_spinbox.value(),
                "y_max": self.y_max_spinbox.value(),
            }

            # Ensure proper order of min/max values
            if region_options["region_enabled"]:
                region_options["x_min"], region_options["x_max"] = min(
                    region_options["x_min"], region_options["x_max"]
                ), max(region_options["x_min"], region_options["x_max"])
                region_options["y_min"], region_options["y_max"] = min(
                    region_options["y_min"], region_options["y_max"]
                ), max(region_options["y_min"], region_options["y_max"])

            # Get contour video options
            contour_options = {
                "contour_video_enabled": self.contour_video_enabled.isChecked(),
                "contour_mode": self.contour_mode_combo.currentIndex(),  # 0=A, 1=B, 2=C
                "base_file": self.contour_base_file_edit.text().strip(),
                "contour_files": getattr(self, "_cached_contour_files", []),
                "fixed_contour_file": self.contour_fixed_file_edit.text().strip(),
                "colormap_files": getattr(self, "_cached_colormap_files", []),
                "level_type": ["fraction", "sigma", "absolute"][
                    self.contour_level_type_combo.currentIndex()
                ],
                "pos_levels": self._parse_levels(self.contour_pos_levels_edit.text()),
                "neg_levels": self._parse_levels(self.contour_neg_levels_edit.text()),
                "pos_color": self.contour_pos_color_combo.currentText(),
                "neg_color": self.contour_neg_color_combo.currentText(),
                "linewidth": self.contour_linewidth_spin.value(),
            }

            # Check system resources
            # 1. Disk Space
            try:
                total, used, free = shutil.disk_usage(output_dir)
                if free < 500 * 1024 * 1024:  # Less than 500MB
                    reply = QMessageBox.warning(
                        self,
                        "Low Disk Space",
                        f"Free disk space on {output_dir} is low ({free / (1024*1024):.1f} MB).\nVideo creation might fail or produce incomplete files.\n\nDo you want to continue?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No,
                    )
                    if reply == QMessageBox.No:
                        return
            except Exception as e:
                print(f"Warning: Could not check disk space: {e}")

            # 2. Memory
            try:
                vm = psutil.virtual_memory()
                # Estimate memory needed: ~100MB per core overhead + frame size
                # Very rough estimate, but good for catching extreme cases
                estimated_needed = 1024 * 1024 * 1024  # 1GB base
                if self.multiprocessing_check.isChecked():
                    estimated_needed += (
                        self.cores_spinbox.value() * 200 * 1024 * 1024
                    )  # 200MB per core

                if vm.available < estimated_needed:
                    reply = QMessageBox.warning(
                        self,
                        "Low Memory",
                        f"System memory is low ({vm.available / (1024*1024*1024):.1f} GB available).\nUsing {self.cores_spinbox.value()} cores might cause system instability.\n\nDo you want to continue?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No,
                    )
                    if reply == QMessageBox.No:
                        return
            except Exception as e:
                print(f"Warning: Could not check memory: {e}")

            # Get video options
            video_options = {
                "fps": self.fps_spinbox.value(),
                "quality": self.quality_spinbox.value(),
            }

            # Additional validation for dimensions
            width = self.width_spinbox.value()
            height = self.height_spinbox.value()

            if width > 0 and width % 2 != 0:
                QMessageBox.warning(
                    self,
                    "Invalid Width",
                    "Video width must be an even number.",
                )
                return

            if height > 0 and height % 2 != 0:
                QMessageBox.warning(
                    self,
                    "Invalid Height",
                    "Video height must be an even number.",
                )
                return

            # Create progress dialog
            progress_dialog = QProgressDialog(
                "Creating video...",
                "Cancel",
                0,
                1000,  # Use 1000 as maximum (100 * scale factor of 10)
                self,
            )
            progress_dialog.setWindowTitle("Creating Video")
            progress_dialog.setWindowModality(Qt.WindowModal)
            print(f"Created progress dialog with range: 0-1000")
            progress_dialog.show()
            self.progress_dialog = progress_dialog  # Store as class member

            # Merge all options
            options = {
                **display_options,
                **overlay_options,
                **region_options,
                **contour_options,
                **video_options,
            }

            # Create the video
            from solar_radio_image_viewer.create_video import (
                create_video as create_video_function,
            )

            # Determine which files to use based on contour mode
            video_files = matching_files  # Default: use Input tab files

            if options.get("contour_video_enabled", False):
                contour_mode = options.get("contour_mode", 0)
                base_file = options.get("base_file", "")
                contour_files = options.get("contour_files", [])
                colormap_files = options.get("colormap_files", [])
                fixed_contour = options.get("fixed_contour_file", "")

                if contour_mode == 0:  # Mode A: Fixed base + evolving contours
                    # The base image is displayed repeatedly, contours evolve
                    if not base_file or not os.path.exists(base_file):
                        QMessageBox.warning(
                            self, "Error", "Contour Mode A requires a base image file"
                        )
                        return
                    if not contour_files:
                        QMessageBox.warning(
                            self, "Error", "Contour Mode A requires a contour directory"
                        )
                        return
                    # Create list of base_file repeated for each contour frame
                    video_files = [base_file] * len(contour_files)
                    print(
                        f"Mode A: {len(contour_files)} contour frames, base image: {os.path.basename(base_file)}"
                    )

                elif contour_mode == 1:  # Mode B: Fixed contours + evolving colormap
                    # Colormap images evolve, fixed contour overlaid on each
                    if not fixed_contour or not os.path.exists(fixed_contour):
                        QMessageBox.warning(
                            self,
                            "Error",
                            "Contour Mode B requires a fixed contour file",
                        )
                        return
                    if not colormap_files:
                        QMessageBox.warning(
                            self,
                            "Error",
                            "Contour Mode B requires a colormap directory",
                        )
                        return
                    video_files = colormap_files
                    print(
                        f"Mode B: {len(colormap_files)} colormap frames, fixed contour: {os.path.basename(fixed_contour)}"
                    )

                elif contour_mode == 2:  # Mode C: Both evolve
                    # Both colormap and contours evolve frame by frame
                    if not colormap_files:
                        QMessageBox.warning(
                            self,
                            "Error",
                            "Contour Mode C requires a colormap directory",
                        )
                        return
                    if not contour_files:
                        QMessageBox.warning(
                            self, "Error", "Contour Mode C requires a contour directory"
                        )
                        return
                    # Match file counts
                    if len(colormap_files) != len(contour_files):
                        QMessageBox.warning(
                            self,
                            "Warning",
                            f"Colormap ({len(colormap_files)}) and contour ({len(contour_files)}) file counts don't match. Using minimum.",
                        )
                        min_count = min(len(colormap_files), len(contour_files))
                        colormap_files = colormap_files[:min_count]
                        options["contour_files"] = contour_files[:min_count]
                    video_files = colormap_files
                    print(
                        f"Mode C: {len(colormap_files)} colormap frames, {len(contour_files)} contour frames"
                    )

                print(
                    f"Contour Mode {contour_mode}: Using {len(video_files)} files for video"
                )

            # Use a worker thread for video creation
            self.worker = VideoWorker(
                video_files,
                output_file,
                options,
                progress_dialog,
                self.cores_spinbox.value(),
            )
            self.worker.finished.connect(self.on_video_creation_finished)
            self.worker.error.connect(self.on_video_creation_error)

            # Disable the create button while processing
            self.create_btn.setEnabled(False)
            self.create_btn.setText("Creating Video...")

            # Start the worker thread
            self.worker.start()

        except Exception as e:
            # Close the progress dialog
            if hasattr(self, "progress_dialog"):
                self.progress_dialog.setValue(
                    1000
                )  # Use 1000 instead of 100 to match our scale factor of 10
                self.progress_dialog.close()

            QMessageBox.critical(
                self,
                "Error",
                f"Error creating video: {str(e)}",
            )

    def on_video_creation_finished(self, output_file):
        """Handle successful video creation"""
        # Re-enable the create button
        self.create_btn.setEnabled(True)
        self.create_btn.setText("Create Video")

        # Close the progress dialog
        if hasattr(self, "progress_dialog"):
            self.progress_dialog.setValue(
                1000
            )  # Use 1000 instead of 100 to match our scale factor of 10
            self.progress_dialog.close()

        QMessageBox.information(
            self,
            "Video Created",
            f"Video successfully created: {output_file}",
        )
        # self.accept()  # Keep dialog open after creation

    def on_video_creation_error(self, error_message):
        """Handle error in video creation"""
        # Re-enable the create button
        self.create_btn.setEnabled(True)
        self.create_btn.setText("Create Video")

        QMessageBox.critical(
            self,
            "Error Creating Video",
            f"Error creating video: {error_message}",
        )

    @wait_cursor()
    def select_region_from_preview(self, *args):
        """Let the user select a region from the preview image"""
        if not self.reference_image:
            QMessageBox.warning(
                self, "No Preview", "Please load a reference image first."
            )
            return

        try:
            # Enable region selection
            self.region_enabled.setChecked(True)

            # Create a separate figure/canvas for selection to avoid parenting issues
            from matplotlib.figure import Figure as MplFigure
            from matplotlib.backends.backend_qt5agg import (
                FigureCanvasQTAgg as MplCanvas,
            )

            selection_figure = MplFigure(figsize=(5, 4), dpi=100)
            selection_canvas = MplCanvas(selection_figure)
            ax = selection_figure.add_subplot(111)

            # Load data
            data, _ = load_fits_data(
                self.reference_image, stokes=self.stokes_combo.currentText()
            )

            if data is None:
                return

            # Display image for selection
            stretch = self.stretch_combo.currentText().lower()
            gamma = self.gamma_spinbox.value()
            cmap = self.colormap_combo.currentText()

            # Determine vmin/vmax
            if self.range_mode_combo.currentIndex() == 0:  # Fixed
                vmin = self.vmin_spinbox.value()
                vmax = self.vmax_spinbox.value()
            else:  # Auto
                vmin = np.nanpercentile(data, 0)
                vmax = np.nanpercentile(data, 100)

            # Create normalization
            norm = get_norm(stretch, vmin, vmax, gamma)

            # Display the image
            ax.imshow(
                data, cmap=cmap, norm=norm, origin="lower", interpolation="nearest"
            )

            ax.set_title("Click and drag to select region", fontsize=10)

            # Add interactive rectangle selector
            from matplotlib.widgets import RectangleSelector

            def onselect(eclick, erelease):
                """Handle region selection event"""
                # Get coordinates in data space
                x1, y1 = eclick.xdata, eclick.ydata
                x2, y2 = erelease.xdata, erelease.ydata

                # Check for None (outside axes)
                if None in (x1, y1, x2, y2):
                    return

                # Ensure proper min/max
                x_min, x_max = int(min(x1, x2)), int(max(x1, x2))
                y_min, y_max = int(min(y1, y2)), int(max(y1, y2))

                # Update spinboxes with selected region
                self.x_min_spinbox.setValue(max(0, x_min))
                self.x_max_spinbox.setValue(min(data.shape[1] - 1, x_max))
                self.y_min_spinbox.setValue(max(0, y_min))
                self.y_max_spinbox.setValue(min(data.shape[0] - 1, y_max))

                # We don't update the main preview here to keep things fast/safe
                # The main preview will update when the dialog closes via the changed spinboxes if needed
                # or we can update it explicitly at the end

            # Draw rectangle selector
            rect_selector = RectangleSelector(
                ax,
                onselect,
                useblit=True,
                button=[1],  # Left mouse button only
                minspanx=5,
                minspany=5,
                spancoords="pixels",
                interactive=True,
                props=dict(facecolor="none", edgecolor="red", linewidth=2),
            )

            # Need to keep a reference to prevent garbage collection
            self._rect_selector = rect_selector

            # Show message
            """status_text = ax.text(
                0.5,
                0.02,
                "Click and drag to select region, then close this window",
                transform=ax.transAxes,
                ha="center",
                va="bottom",
                bbox=dict(boxstyle="round", fc="white", alpha=0.8),
            )"""

            # Refresh canvas
            selection_canvas.draw()

            # Create a modal dialog to use for selection
            selector_dialog = QDialog(self)
            selector_dialog.setWindowTitle("Select Region")
            selector_layout = QVBoxLayout(selector_dialog)

            # Add the canvas to the dialog
            from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT

            toolbar = NavigationToolbar2QT(selection_canvas, selector_dialog)
            selector_layout.addWidget(toolbar)
            selector_layout.addWidget(selection_canvas)

            # Add instructions
            instructions = QLabel(
                "Click and drag to select a region. Use toolbar to pan/zoom if needed. "
                "Close this dialog when finished."
            )
            instructions.setWordWrap(True)
            selector_layout.addWidget(instructions)

            # Add done button
            done_btn = QPushButton("Done")
            done_btn.clicked.connect(selector_dialog.accept)
            selector_layout.addWidget(done_btn)

            # Set a reasonable size
            selector_dialog.resize(800, 600)

            # Execute dialog
            selector_dialog.exec_()

            # Clean up selector to break circular references
            self._rect_selector = None

            # Update the preview after dialog closes
            self.update_preview()

        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not select region: {str(e)}")

    def apply_auto_minmax(self):
        """Apply Auto Min/Max preset to the display range"""
        if not self.reference_image:
            return

        data, _ = load_fits_data(
            self.reference_image, stokes=self.stokes_combo.currentText()
        )
        if data is None:
            return

        # Calculate min/max, excluding NaN values
        vmin = np.nanmin(data)
        vmax = np.nanmax(data)

        # Update spinboxes
        self.range_mode_combo.setCurrentIndex(0)  # Switch to fixed range
        self.vmin_spinbox.setValue(vmin)
        self.vmax_spinbox.setValue(vmax)

        # Update preview
        self.update_preview()

    def apply_auto_percentile(self):
        """Apply Auto Percentile preset to the display range"""
        if not self.reference_image:
            return

        data, _ = load_fits_data(
            self.reference_image, stokes=self.stokes_combo.currentText()
        )
        if data is None:
            return

        # Calculate 1st and 99th percentiles
        vmin = np.nanpercentile(data, 1)
        vmax = np.nanpercentile(data, 99)

        # Update spinboxes
        self.range_mode_combo.setCurrentIndex(0)  # Switch to fixed range
        self.vmin_spinbox.setValue(np.nanpercentile(data, 1))
        self.vmax_spinbox.setValue(np.nanpercentile(data, 99))

        # Update preview
        self.update_preview()

    def apply_auto_median_rms(self):
        """Apply Auto Median ± 3×RMS preset to the display range"""
        if not self.reference_image:
            return

        data, _ = load_fits_data(
            self.reference_image, stokes=self.stokes_combo.currentText()
        )
        if data is None:
            return

        # Calculate median and RMS
        median = np.nanmedian(data)
        rms = np.sqrt(np.nanmean(np.square(data - median)))

        # Set range to median ± 3×RMS
        vmin = median - 3 * rms
        vmax = median + 3 * rms

        # Update spinboxes
        self.range_mode_combo.setCurrentIndex(0)  # Switch to fixed range
        self.vmin_spinbox.setValue(vmin)
        self.vmax_spinbox.setValue(vmax)

        # Update preview
        self.update_preview()

    def apply_aia_preset(self):
        """Apply AIA 171Å preset to the display"""
        # Set colormap to SDO-AIA 171
        idx = self.colormap_combo.findText("sdoaia171")
        if idx >= 0:
            self.colormap_combo.setCurrentIndex(idx)
        else:
            # Fallback to similar colormap
            idx = self.colormap_combo.findText("hot")
            if idx >= 0:
                self.colormap_combo.setCurrentIndex(idx)

        # Set stretch to log
        idx = self.stretch_combo.findText("Log")
        if idx >= 0:
            self.stretch_combo.setCurrentIndex(idx)

        # Update preview
        self.update_preview()

    def apply_hmi_preset(self):
        """Apply HMI preset to the display"""
        # Set colormap to gray for HMI
        idx = self.colormap_combo.findText("gray")
        if idx >= 0:
            self.colormap_combo.setCurrentIndex(idx)

        # Set stretch to linear
        idx = self.stretch_combo.findText("Linear")
        if idx >= 0:
            self.stretch_combo.setCurrentIndex(idx)

        # Update preview
        self.update_preview()

    def set_region_preset(self, percentage):
        """Set the region to a centered area covering the given percentage of the image.

        Parameters
        ----------
        percentage : float
            Percentage of the image to cover (0.0 to 1.0)
        """
        if not self.reference_image:
            QMessageBox.warning(
                self, "No Reference Image", "Please select a reference image first."
            )
            return

        try:
            # Load the reference image data
            data, _ = load_fits_data(
                self.reference_image, stokes=self.stokes_combo.currentText()
            )

            if data is None:
                return

            # Get image dimensions
            height, width = data.shape

            # Calculate the size of the region
            region_width = int(width * percentage)
            region_height = int(height * percentage)

            # Calculate the center of the image
            center_x = width // 2
            center_y = height // 2

            # Calculate region boundaries
            x_min = center_x - region_width // 2
            x_max = center_x + region_width // 2
            y_min = center_y - region_height // 2
            y_max = center_y + region_height // 2

            # Ensure region is within image boundaries
            x_min = max(0, x_min)
            x_max = min(width - 1, x_max)
            y_min = max(0, y_min)
            y_max = min(height - 1, y_max)

            # Update spinboxes
            self.x_min_spinbox.setValue(x_min)
            self.x_max_spinbox.setValue(x_max)
            self.y_min_spinbox.setValue(y_min)
            self.y_max_spinbox.setValue(y_max)

            # Ensure region selection is enabled
            self.region_enabled.setChecked(True)

            # Update preview
            self.update_preview()

        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not set region preset: {str(e)}")

    def toggle_region_controls(self, enabled):
        """Enable or disable region controls"""
        self.x_min_spinbox.setEnabled(enabled)
        self.x_max_spinbox.setEnabled(enabled)
        self.y_min_spinbox.setEnabled(enabled)
        self.y_max_spinbox.setEnabled(enabled)
        self.update_region_preview()

    def toggle_contour_mode(self, enabled):
        """Toggle Contour Mode - shows/hides Input and Contours tabs"""
        # Input tab is at index 0, Contours tab is at index 1
        self.tab_widget.setTabVisible(0, not enabled)
        self.tab_widget.setTabVisible(1, enabled)

        # Also toggle the contour video controls
        self.toggle_contour_video_controls(enabled)

        # Update create button state based on contour mode files
        self.update_create_button_state()

        # If enabling contour mode, switch to Contours tab
        if enabled:
            self.tab_widget.setCurrentIndex(1)
        else:
            self.tab_widget.setCurrentIndex(0)

    def update_create_button_state(self):
        """Update Create Video button state based on current mode and inputs"""
        if self.contour_video_enabled.isChecked():
            # In contour mode, check if required contour files are specified
            mode = self.contour_mode_combo.currentIndex()
            has_valid_input = False
            missing_fields = []

            if mode == 0:  # Mode A: Fixed base + evolving contours
                base_file = self.contour_base_file_edit.text().strip()
                contour_dir = self.contour_dir_edit.text().strip()
                if not base_file or not os.path.exists(base_file):
                    missing_fields.append("Base Image")
                if not contour_dir or not os.path.isdir(contour_dir):
                    missing_fields.append("Contour Directory")
                has_valid_input = len(missing_fields) == 0

            elif mode == 1:  # Mode B: Fixed contour + evolving colormap
                fixed_contour = self.contour_fixed_file_edit.text().strip()
                colormap_dir = self.contour_colormap_dir_edit.text().strip()
                if not fixed_contour or not os.path.exists(fixed_contour):
                    missing_fields.append("Fixed Contour File")
                if not colormap_dir or not os.path.isdir(colormap_dir):
                    missing_fields.append("Colormap Directory")
                has_valid_input = len(missing_fields) == 0

            elif mode == 2:  # Mode C: Both evolve
                contour_dir = self.contour_dir_edit.text().strip()
                colormap_dir = self.contour_colormap_dir_edit.text().strip()
                if not contour_dir or not os.path.isdir(contour_dir):
                    missing_fields.append("Contour Directory")
                if not colormap_dir or not os.path.isdir(colormap_dir):
                    missing_fields.append("Colormap Directory")
                has_valid_input = len(missing_fields) == 0

            self.create_btn.setEnabled(has_valid_input)
            if not has_valid_input:
                self.create_btn.setToolTip(f"Missing: {', '.join(missing_fields)}")
            else:
                self.create_btn.setToolTip("")
        else:
            # Normal mode - enable button (existing validation on create)
            self.create_btn.setToolTip("")

    def toggle_contour_video_controls(self, enabled):
        """Enable or disable contour video controls"""
        self.contour_mode_combo.setEnabled(enabled)
        self.contour_files_group.setEnabled(enabled)
        if enabled:
            self.update_contour_mode_ui(self.contour_mode_combo.currentIndex())

    def update_contour_mode_ui(self, index):
        """Update visibility of contour file inputs based on mode"""
        # Mode A: Base image + contour directory + contour pattern
        # Mode B: Fixed contour + colormap directory + colormap pattern
        # Mode C: Contour directory + contour pattern + colormap directory + colormap pattern

        # Get parent widgets for each row
        base_file_row = self.contour_base_file_edit.parent()
        contour_dir_row = self.contour_dir_edit.parent()
        fixed_contour_row = self.contour_fixed_file_edit.parent()
        colormap_dir_row = self.contour_colormap_dir_edit.parent()

        if index == 0:  # Mode A: Fixed base + evolving contours
            base_file_row.setVisible(True)
            contour_dir_row.setVisible(True)
            self.contour_pattern_widget.setVisible(True)
            fixed_contour_row.setVisible(False)
            colormap_dir_row.setVisible(False)
            self.colormap_pattern_widget.setVisible(False)
        elif index == 1:  # Mode B: Fixed contours + evolving colormap
            base_file_row.setVisible(False)
            contour_dir_row.setVisible(False)
            self.contour_pattern_widget.setVisible(False)
            fixed_contour_row.setVisible(True)
            colormap_dir_row.setVisible(True)
            self.colormap_pattern_widget.setVisible(True)
        else:  # Mode C: Both evolve
            base_file_row.setVisible(False)
            contour_dir_row.setVisible(True)
            self.contour_pattern_widget.setVisible(True)
            fixed_contour_row.setVisible(False)
            colormap_dir_row.setVisible(True)
            self.colormap_pattern_widget.setVisible(True)

    def browse_contour_base_file(self):
        """Browse for base image file"""
        start_dir = self.get_smart_start_directory(
            self.contour_base_file_edit.text().strip()
        )
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Base Image File",
            start_dir,
            "FITS Files (*.fits *.fts);;All Files (*)",
        )
        if file_path:
            self.contour_base_file_edit.setText(file_path)

    def browse_contour_directory(self):
        """Browse for contour files directory"""
        start_dir = self.get_smart_start_directory(self.contour_dir_edit.text().strip())
        directory = QFileDialog.getExistingDirectory(
            self, "Select Contour Files Directory", start_dir
        )
        if directory:
            self.contour_dir_edit.setText(directory)

    def browse_contour_fixed_file(self):
        """Browse for fixed contour file"""
        start_dir = self.get_smart_start_directory(
            self.contour_fixed_file_edit.text().strip()
        )
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Fixed Contour File",
            start_dir,
            "FITS Files (*.fits *.fts);;All Files (*)",
        )
        if file_path:
            self.contour_fixed_file_edit.setText(file_path)

    def browse_contour_colormap_directory(self):
        """Browse for colormap files directory"""
        start_dir = self.get_smart_start_directory(
            self.contour_colormap_dir_edit.text().strip()
        )
        directory = QFileDialog.getExistingDirectory(
            self, "Select Colormap Files Directory", start_dir
        )
        if directory:
            self.contour_colormap_dir_edit.setText(directory)

    def scan_contour_files(self):
        """Scan contour directory and update file count label"""
        directory = self.contour_dir_edit.text().strip()
        pattern = self.contour_dir_pattern_edit.text().strip() or "*.fits"

        if not directory or not os.path.isdir(directory):
            self.contour_files_count_label.setText("")
            self._cached_contour_files = []
            return

        full_pattern = os.path.join(directory, pattern)
        files = sorted(glob.glob(full_pattern))
        self._cached_contour_files = files

        if files:
            self.contour_files_count_label.setText(f"✓ {len(files)} files")
            self.contour_files_count_label.setStyleSheet("color: green;")
        else:
            self.contour_files_count_label.setText("No files found")
            self.contour_files_count_label.setStyleSheet("color: red;")

    def scan_colormap_files(self):
        """Scan colormap directory and update file count label"""
        directory = self.contour_colormap_dir_edit.text().strip()
        pattern = self.contour_colormap_pattern_edit.text().strip() or "*.fits"

        if not directory or not os.path.isdir(directory):
            self.colormap_files_count_label.setText("")
            self._cached_colormap_files = []
            return

        full_pattern = os.path.join(directory, pattern)
        files = sorted(glob.glob(full_pattern))
        self._cached_colormap_files = files

        if files:
            self.colormap_files_count_label.setText(f"✓ {len(files)} files")
            self.colormap_files_count_label.setStyleSheet("color: green;")
        else:
            self.colormap_files_count_label.setText("No files found")
            self.colormap_files_count_label.setStyleSheet("color: red;")

    def update_region_preview(self):
        """Update the preview when region controls change"""
        if self.region_enabled.isChecked():
            self.update_preview()

    def closeEvent(self, event):
        """Handle dialog close event"""
        # Stop worker thread if running
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(1000)  # Wait up to 1 second for thread to finish
        event.accept()

    def reject(self):
        """Handle dialog rejection (Cancel button or Esc key)"""
        # Stop worker thread if running
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(1000)  # Wait up to 1 second for thread to finish
        super().reject()

    def update_gamma_controls(self):
        """Enable/disable gamma controls based on the selected stretch type"""
        stretch = self.stretch_combo.currentText().lower()
        enable_gamma = stretch == "power"

        self.gamma_spinbox.setEnabled(enable_gamma)

    def _parse_levels(self, text):
        """Parse comma-separated level values from text"""
        try:
            levels = []
            for part in text.split(","):
                part = part.strip()
                if part:
                    levels.append(float(part))
            return levels
        except ValueError:
            return [0.1, 0.3, 0.5, 0.7, 0.9]  # Default levels


# Worker thread for video creation
class VideoWorker(QThread):
    finished = pyqtSignal(str)  # Signal emitted when video creation is complete
    error = pyqtSignal(str)  # Signal emitted when an error occurs
    progress = pyqtSignal(int)  # Signal emitted to update progress
    status_update = pyqtSignal(str)  # Signal emitted to update status message

    def __init__(self, files, output_file, options, progress_dialog, cpu_count):
        super().__init__()
        self.files = files
        self.output_file = output_file
        self.options = options
        self.progress_dialog = progress_dialog
        self.is_cancelled = False
        self.in_global_stats_phase = False
        self.processing_complete = False  # Flag to indicate when processing is complete

        # Fix for progress display - multiply progress values by 10
        self.progress_scale_factor = 10  # Factor to scale progress values

        # Add multiprocessing options to the options dictionary
        self.options["use_multiprocessing"] = True
        self.options["cpu_count"] = cpu_count
        print(f"Enabling multiprocessing with {cpu_count} cores")

        # Connect signals
        self.progress.connect(self.progress_dialog.setValue)
        print("Connected progress signal to progress_dialog.setValue")

        # Add a debug print to each progress value emitted
        def debug_progress_value(value):
            print(f"Progress value received by dialog: {value}")
            self.progress_dialog.setValue(value)

        # Replace the standard connection with our debug version
        self.progress.disconnect(self.progress_dialog.setValue)
        self.progress.connect(debug_progress_value)

        # Connect status update signal
        self.status_update.connect(self.update_progress_title)

        self.cpu_count = cpu_count

        # For time-based progress tracking
        self.start_time = None
        self.frame_start_time = None
        self.avg_frame_time = None
        self.total_time_estimate = None

        # For global stats phase
        self.stats_progress_thread = None

        # Progress tracker state
        self.frames_processed = 0
        self.total_frames = len(files)
        self.progress_update_interval = 0.25  # seconds between progress updates

    def update_progress_title(self, message):
        """Update the progress dialog title with current status"""
        if hasattr(self, "progress_dialog") and self.progress_dialog:
            self.progress_dialog.setLabelText(message)

    def update_progress_continuously(self):
        """Thread function to update progress continuously based on time"""
        last_update_time = time.time()
        pulsing_progress = 0

        while not self.is_cancelled and hasattr(self, "progress_dialog"):
            current_time = time.time()

            # Exit the loop if processing is complete
            if self.processing_complete:
                # Allow setting to 100% when complete
                self.progress.emit(
                    1000
                )  # Use 1000 instead of 100 to match our scale factor of 10
                break

            # Update at most every progress_update_interval seconds
            if current_time - last_update_time >= self.progress_update_interval:
                last_update_time = current_time

                # If no frames have been processed yet, assume we're still in initialization
                # or global stats phase - show pulsing progress indicator
                if self.frames_processed == 0:
                    # Create pulsing effect from 1-20%
                    elapsed = current_time - self.start_time
                    pulsing_progress = 5 + 15 * (
                        (elapsed % 3) / 3
                    )  # 3-second cycle from 5-20%
                    scaled_progress = int(pulsing_progress * self.progress_scale_factor)
                    # print(
                    #     f"Pulsing progress: {pulsing_progress}% - Scaled: {scaled_progress}"
                    # )
                    self.progress.emit(scaled_progress)

            # Sleep for a short time to avoid consuming too much CPU
            time.sleep(0.1)

    def run(self):
        try:
            # Directly use the create_video function instead of trying to import it
            from solar_radio_image_viewer.create_video import (
                create_video as create_video_function,
            )

            # Record start time
            self.start_time = time.time()

            # Show immediate initial progress
            self.progress.emit(0)
            print("Video creation started - emitting initial progress: 0")

            # Start a separate thread to update progress continuously
            progress_thread = threading.Thread(target=self.update_progress_continuously)
            progress_thread.daemon = True
            progress_thread.start()

            # Configure progress callback that works with both phases
            def update_progress(current_frame, total_frames):
                if self.is_cancelled:
                    return False

                # DIRECT FIX: Set progress directly based on frame count
                progress_percent = min(99, int(100 * current_frame / total_frames))
                scaled_progress = (
                    progress_percent * 10
                )  # Scale to match our progress dialog range (0-1000)

                # Add debugging output
                # if (
                #     current_frame % 20 == 0 or current_frame == total_frames - 1
                # ):  # Print every 20 frames or last frame
                # print(
                #     f"Frame {current_frame}/{total_frames} - Progress: {progress_percent}% - Scaled: {scaled_progress}"
                # )

                self.progress.emit(scaled_progress)

                # Update frames processed count for reference
                self.frames_processed = current_frame + 1

                # Let the progress thread handle the progress update
                return not self.progress_dialog.wasCanceled()

            # Create the video
            self.status_update.emit("Creating video...")
            print(f"Starting video creation process with {self.cpu_count} cores")
            create_video_function(
                self.files,
                self.output_file,
                self.options,
                progress_callback=update_progress,
            )

            # Set processing complete flag
            self.processing_complete = True
            print("Video creation complete - setting progress to 1000 (100%)")

            # Small delay to ensure the progress thread sees the completed flag
            time.sleep(0.2)

            # Ensure progress reaches 100% when complete
            self.progress.emit(
                1000
            )  # Use 1000 instead of 100 to match our scale factor of 10

            # Check if cancelled before emitting signal
            if not self.is_cancelled:
                # Emit finished signal
                print(f"Emitting finished signal with output file: {self.output_file}")
                self.finished.emit(self.output_file)
            else:
                print("Video creation was cancelled")

        except Exception as e:
            print(f"Error in video creation: {str(e)}")
            # Set processing complete to stop the progress thread
            self.processing_complete = True
            if not self.is_cancelled:
                # Emit error signal
                self.error.emit(str(e))

    def cancel(self):
        """Cancel the worker thread"""
        self.is_cancelled = True
        self.processing_complete = (
            True  # Also mark as complete to stop the progress thread
        )

        # Update progress to 100%
        if hasattr(self, "progress_dialog") and self.progress_dialog:
            self.progress_dialog.setValue(
                1000
            )  # Use 1000 instead of 100 to match our scale factor of 10


if __name__ == "__main__":
    app = QApplication(sys.argv)
    dialog = VideoCreationDialog()
    dialog.show()
    sys.exit(app.exec_())

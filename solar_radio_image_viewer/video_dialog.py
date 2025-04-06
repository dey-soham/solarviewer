#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
from PyQt5.QtWidgets import (
    QDialog,
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


class VideoCreationDialog(QDialog):
    """
    Dialog for creating videos from FITS files
    """

    def __init__(self, parent=None, current_file=None):
        super().__init__(parent)
        self.parent = parent
        self.current_file = current_file
        self.progress_tracker = None
        self.preview_image = None
        self.reference_image = None

        self.setWindowTitle("Create Video")
        self.resize(900, 1200)

        # Set up the UI
        self.setup_ui()

        # Initialize stretch controls
        self.update_gamma_controls()

        # Initialize with current file if provided
        if current_file:
            dir_path = os.path.dirname(current_file)
            self.input_directory_edit.setText(dir_path)
            file_ext = os.path.splitext(current_file)[1]
            self.input_pattern_edit.setText(f"*{file_ext}")

            # Set reference image to current file
            self.reference_image = current_file
            self.reference_image_edit.setText(current_file)

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

        # Update preview if we have a valid reference image
        if self.reference_image:
            self.update_preview(self.reference_image)

    def setup_ui(self):
        """Set up the UI elements"""
        main_layout = QVBoxLayout(self)

        # Create tab widget
        tab_widget = QTabWidget()

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

        # Create the preview section first so it's available to all tabs
        preview_group = QGroupBox("Preview")
        preview_layout = QVBoxLayout(preview_group)

        # Matplotlib figure for preview
        self.figure = Figure(figsize=(5, 4), dpi=100)
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setMinimumHeight(150)
        preview_layout.addWidget(self.canvas)

        # Add "Update Preview" button under the preview
        update_preview_btn = QPushButton("Update Preview")
        update_preview_btn.clicked.connect(self.update_preview_from_reference)
        preview_layout.addWidget(update_preview_btn)

        # Add preview to the main layout first, so it's always visible
        main_layout.addWidget(preview_group)

        # ------ Input Tab ------
        # Create a scroll area for the input tab
        input_scroll = QScrollArea()
        input_scroll.setWidgetResizable(True)
        input_scroll_content = QWidget()
        input_layout = QVBoxLayout(input_scroll_content)
        input_scroll.setWidget(input_scroll_content)

        # Input pattern section
        input_group = QGroupBox("Input Files")
        input_group_layout = QGridLayout(input_group)

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

        # Preview input pattern button
        preview_pattern_btn = QPushButton("Preview Files")
        preview_pattern_btn.clicked.connect(self.preview_input_files)
        input_group_layout.addWidget(preview_pattern_btn, 1, 2)

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

        # Files found label
        self.files_found_label = QLabel("No files found yet")
        input_group_layout.addWidget(self.files_found_label, 4, 0, 1, 3)

        input_layout.addWidget(input_group)

        # ------ Display Tab ------
        # Create a scroll area for the display tab
        display_scroll = QScrollArea()
        display_scroll.setWidgetResizable(True)
        display_scroll_content = QWidget()
        display_layout = QVBoxLayout(display_scroll_content)
        display_scroll.setWidget(display_scroll_content)

        # Reference image for display settings
        reference_group = QGroupBox("Reference Image")
        reference_layout = QGridLayout(reference_group)

        reference_layout.addWidget(QLabel("Reference Image:"), 0, 0)
        self.reference_image_edit = QLineEdit()
        self.reference_image_edit.setReadOnly(True)  # Make it read-only
        reference_layout.addWidget(self.reference_image_edit, 0, 1)

        browse_reference_btn = QPushButton("Browse")
        browse_reference_btn.clicked.connect(self.browse_reference_image)
        reference_layout.addWidget(browse_reference_btn, 0, 2)

        display_layout.addWidget(reference_group)

        # Display settings section
        display_group = QGroupBox("Display Settings")
        # Create a horizontal layout to hold two columns
        display_main_layout = QHBoxLayout(display_group)

        # Create left column (for color/stretch controls)
        left_column = QVBoxLayout()
        left_form_layout = QFormLayout()
        left_form_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        # Colormap
        self.colormap_combo = QComboBox()
        colormaps = sorted(
            [cmap for cmap in plt.colormaps() if not cmap.endswith("_r")]
        )
        self.colormap_combo.addItems(colormaps)
        # Set default to viridis
        idx = self.colormap_combo.findText("viridis")
        if idx >= 0:
            self.colormap_combo.setCurrentIndex(idx)
        self.colormap_combo.currentIndexChanged.connect(self.update_preview_settings)
        left_form_layout.addRow("Colormap:", self.colormap_combo)

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
        left_form_layout.addRow("Stretch:", self.stretch_combo)

        # Gamma (for power stretch)
        self.gamma_spinbox = QDoubleSpinBox()
        self.gamma_spinbox.setRange(0.1, 10.0)
        self.gamma_spinbox.setSingleStep(0.1)
        self.gamma_spinbox.setValue(1.0)
        self.gamma_spinbox.valueChanged.connect(self.update_preview_settings)
        left_form_layout.addRow("Gamma:", self.gamma_spinbox)

        # Range mode selection
        self.range_mode_combo = QComboBox()
        self.range_mode_combo.addItems(["Fixed Range", "Auto per Frame", "Global Auto"])
        self.range_mode_combo.setToolTip(
            "Fixed Range: Use the min/max values specified below for all frames\n"
            "Auto per Frame: Calculate min/max independently for each frame based on percentiles\n"
            "Global Auto: Calculate min/max once from all frames based on percentiles"
        )
        self.range_mode_combo.currentIndexChanged.connect(self.toggle_range_mode)
        left_form_layout.addRow("Range Scaling:", self.range_mode_combo)

        # Add explanatory label
        self.range_explanation_label = QLabel(
            "Fixed Range: Same min/max values used for all frames"
        )
        self.range_explanation_label.setStyleSheet("color: gray; font-style: italic;")
        left_form_layout.addRow("", self.range_explanation_label)

        # Min/Max values
        self.vmin_spinbox = QDoubleSpinBox()
        self.vmin_spinbox.setRange(-1e10, 1e10)
        self.vmin_spinbox.setDecimals(2)
        self.vmin_spinbox.setValue(0)
        self.vmin_spinbox.valueChanged.connect(self.update_preview_settings)
        left_form_layout.addRow("Min Value:", self.vmin_spinbox)

        self.vmax_spinbox = QDoubleSpinBox()
        self.vmax_spinbox.setRange(-1e10, 1e10)
        self.vmax_spinbox.setDecimals(2)
        self.vmax_spinbox.setValue(3000)
        self.vmax_spinbox.valueChanged.connect(self.update_preview_settings)
        left_form_layout.addRow("Max Value:", self.vmax_spinbox)

        # Add the form layout to the left column
        left_column.addLayout(left_form_layout)

        # Create right column (for frame size and other controls)
        right_column = QVBoxLayout()
        right_form_layout = QFormLayout()
        right_form_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        # Frame resize
        self.width_spinbox = QSpinBox()
        self.width_spinbox.setRange(0, 7680)  # Up to 8K resolution
        self.width_spinbox.setValue(0)
        self.width_spinbox.setSpecialValueText("Original")
        right_form_layout.addRow("Frame Width:", self.width_spinbox)

        self.height_spinbox = QSpinBox()
        self.height_spinbox.setRange(0, 4320)  # Up to 8K resolution
        self.height_spinbox.setValue(0)
        self.height_spinbox.setSpecialValueText("Original")
        right_form_layout.addRow("Frame Height:", self.height_spinbox)

        # Colorbar options
        self.colorbar_check = QCheckBox("Show Colorbar")
        self.colorbar_check.setChecked(True)
        self.colorbar_check.stateChanged.connect(self.update_preview_settings)
        right_form_layout.addRow("", self.colorbar_check)

        # Add the form layout to the right column
        right_column.addLayout(right_form_layout)
        right_column.addStretch()  # Add stretch to align with left column

        # Add both columns to the main layout
        display_main_layout.addLayout(left_column, 2)  # Give left column more space
        display_main_layout.addLayout(right_column, 1)

        display_layout.addWidget(display_group)

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

        auto_median_btn = QPushButton("Auto Median±3×RMS")
        auto_median_btn.clicked.connect(self.apply_auto_median_rms)
        presets_layout.addWidget(auto_median_btn, 1, 0)

        # AIA/HMI Presets
        aia_preset_btn = QPushButton("AIA 171Å Preset")
        aia_preset_btn.clicked.connect(self.apply_aia_preset)
        presets_layout.addWidget(aia_preset_btn, 1, 1)

        hmi_preset_btn = QPushButton("HMI Preset")
        hmi_preset_btn.clicked.connect(self.apply_hmi_preset)
        presets_layout.addWidget(hmi_preset_btn, 2, 0)

        display_layout.addWidget(presets_group)

        # ------ Region Tab ------
        # Create a scroll area for the region tab
        region_scroll = QScrollArea()
        region_scroll.setWidgetResizable(True)
        region_scroll_content = QWidget()
        region_layout = QVBoxLayout(region_scroll_content)
        region_scroll.setWidget(region_scroll_content)

        region_group = QGroupBox("Region Selection (Zoomed Video)")
        region_main_layout = QVBoxLayout(region_group)

        # Enable region selection
        self.region_enabled = QCheckBox("Enable Region Selection (Zoomed Video)")
        self.region_enabled.setChecked(False)
        self.region_enabled.stateChanged.connect(self.toggle_region_controls)
        region_main_layout.addWidget(self.region_enabled)

        # Coordinate inputs in two columns
        coord_layout = QHBoxLayout()

        # X range
        x_layout = QFormLayout()
        x_range_widget = QWidget()
        x_range_layout = QHBoxLayout(x_range_widget)
        x_range_layout.setContentsMargins(0, 0, 0, 0)

        self.x_min_spinbox = QSpinBox()
        self.x_min_spinbox.setRange(0, 10000)
        self.x_min_spinbox.setValue(0)
        self.x_min_spinbox.valueChanged.connect(self.update_region_preview)
        x_range_layout.addWidget(self.x_min_spinbox)

        x_range_layout.addWidget(QLabel("to"))

        self.x_max_spinbox = QSpinBox()
        self.x_max_spinbox.setRange(0, 10000)
        self.x_max_spinbox.setValue(100)
        self.x_max_spinbox.valueChanged.connect(self.update_region_preview)
        x_range_layout.addWidget(self.x_max_spinbox)

        x_layout.addRow("X Range (pixels):", x_range_widget)

        # Y range
        y_layout = QFormLayout()
        y_range_widget = QWidget()
        y_range_layout = QHBoxLayout(y_range_widget)
        y_range_layout.setContentsMargins(0, 0, 0, 0)

        self.y_min_spinbox = QSpinBox()
        self.y_min_spinbox.setRange(0, 10000)
        self.y_min_spinbox.setValue(0)
        self.y_min_spinbox.valueChanged.connect(self.update_region_preview)
        y_range_layout.addWidget(self.y_min_spinbox)

        y_range_layout.addWidget(QLabel("to"))

        self.y_max_spinbox = QSpinBox()
        self.y_max_spinbox.setRange(0, 10000)
        self.y_max_spinbox.setValue(100)
        self.y_max_spinbox.valueChanged.connect(self.update_region_preview)
        y_range_layout.addWidget(self.y_max_spinbox)

        y_layout.addRow("Y Range (pixels):", y_range_widget)

        coord_layout.addLayout(x_layout)
        coord_layout.addLayout(y_layout)

        region_main_layout.addLayout(coord_layout)

        # Region Presets in a horizontal layout
        preset_layout = QHBoxLayout()
        preset_layout.addWidget(QLabel("Region Presets:"))

        # Add region preset buttons
        center_25_btn = QPushButton("Center 25%")
        center_25_btn.clicked.connect(lambda: self.set_region_preset(0.25))
        preset_layout.addWidget(center_25_btn)

        center_50_btn = QPushButton("Center 50%")
        center_50_btn.clicked.connect(lambda: self.set_region_preset(0.5))
        preset_layout.addWidget(center_50_btn)

        center_75_btn = QPushButton("Center 75%")
        center_75_btn.clicked.connect(lambda: self.set_region_preset(0.75))
        preset_layout.addWidget(center_75_btn)

        region_main_layout.addLayout(preset_layout)

        # Help text
        help_label = QLabel(
            "This feature allows you to create a video focused on a specific region of interest. "
            "The selected region will be shown with a red rectangle in the preview."
        )
        help_label.setWordWrap(True)
        region_main_layout.addWidget(help_label)

        # Select from preview button
        select_from_preview_btn = QPushButton("Select Region from Preview")
        select_from_preview_btn.clicked.connect(self.select_region_from_preview)
        region_main_layout.addWidget(select_from_preview_btn)

        # Initially disable the region controls
        self.toggle_region_controls(False)

        region_layout.addWidget(region_group)
        region_layout.addStretch()

        # ------ Overlay Tab ------
        # Create a scroll area for the overlay tab
        overlay_scroll = QScrollArea()
        overlay_scroll.setWidgetResizable(True)
        overlay_scroll_content = QWidget()
        overlay_layout = QVBoxLayout(overlay_scroll_content)
        overlay_scroll.setWidget(overlay_scroll_content)

        # Overlay settings section
        overlay_group = QGroupBox("Overlay Settings")
        overlay_main_layout = QHBoxLayout(overlay_group)

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

        # Add spacer to push controls to the top
        overlay_layout.addStretch()

        # ------ Output Tab ------
        # Create a scroll area for the output tab
        output_scroll = QScrollArea()
        output_scroll.setWidgetResizable(True)
        output_scroll_content = QWidget()
        output_layout = QVBoxLayout(output_scroll_content)
        output_scroll.setWidget(output_scroll_content)

        # Output file section
        output_group = QGroupBox("Output Settings")
        output_main_layout = QHBoxLayout(output_group)

        # Left column for filename
        left_output_layout = QVBoxLayout()
        file_layout = QHBoxLayout()

        file_layout.addWidget(QLabel("Output File:"))
        self.output_file_edit = QLineEdit()
        file_layout.addWidget(self.output_file_edit)

        browse_output_btn = QPushButton("Browse")
        browse_output_btn.clicked.connect(self.browse_output_file)
        file_layout.addWidget(browse_output_btn)

        left_output_layout.addLayout(file_layout)

        # Create two columns for the rest of the controls
        left_column = QFormLayout()
        right_column = QFormLayout()

        # Left column controls
        # FPS
        self.fps_spinbox = QSpinBox()
        self.fps_spinbox.setRange(1, 60)
        self.fps_spinbox.setValue(10)
        left_column.addRow("Frames Per Second:", self.fps_spinbox)

        # Video Quality
        self.quality_spinbox = QSpinBox()
        self.quality_spinbox.setRange(1, 10)
        self.quality_spinbox.setValue(10)
        self.quality_spinbox.setToolTip(
            "Higher values mean better quality but larger file size (1-10)"
        )
        left_column.addRow("Video Quality:", self.quality_spinbox)

        # Add quality explanation as tooltip
        quality_explanation = QLabel("1 = lowest quality, 10 = highest quality")
        quality_explanation.setStyleSheet("color: gray; font-style: italic;")
        left_column.addRow("", quality_explanation)

        # Right column controls
        # Multiprocessing
        self.multiprocessing_check = QCheckBox("Use Multiprocessing")
        self.multiprocessing_check.setChecked(True)
        right_column.addRow("", self.multiprocessing_check)

        # CPU cores
        max_cores = multiprocessing.cpu_count()
        self.cores_spinbox = QSpinBox()
        self.cores_spinbox.setRange(1, max_cores)
        self.cores_spinbox.setValue(
            max(1, max_cores - 1)
        )  # Default to all but one core
        self.cores_spinbox.setEnabled(self.multiprocessing_check.isChecked())
        right_column.addRow("CPU Cores:", self.cores_spinbox)

        # Connect multiprocessing checkbox to cores spinbox
        self.multiprocessing_check.stateChanged.connect(
            lambda state: self.cores_spinbox.setEnabled(state == Qt.Checked)
        )

        # Add both form layouts to a horizontal layout
        settings_layout = QHBoxLayout()
        settings_layout.addLayout(left_column)
        settings_layout.addLayout(right_column)

        # Add the file layout and settings layout to the left column
        left_output_layout.addLayout(settings_layout)

        # Add the left column to the main layout
        output_main_layout.addLayout(left_output_layout)

        output_layout.addWidget(output_group)

        # Add tabs to tab widget
        tab_widget.addTab(input_scroll, "Input")
        tab_widget.addTab(display_scroll, "Display")
        tab_widget.addTab(region_scroll, "Region")
        tab_widget.addTab(overlay_scroll, "Overlays")
        tab_widget.addTab(output_scroll, "Output")

        # Add the tab widget to the main layout (after the preview)
        main_layout.addWidget(tab_widget)

        # Buttons at the bottom
        button_layout = QHBoxLayout()

        self.create_btn = QPushButton("Create Video")
        self.create_btn.clicked.connect(self.create_video)
        button_layout.addWidget(self.create_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)

        main_layout.addLayout(button_layout)

    def toggle_range_mode(self, index):
        """Enable/disable controls based on the range mode selection"""
        # Update the explanation label
        if index == 0:  # Fixed Range
            self.range_explanation_label.setText(
                "Fixed Range: Same min/max values used for all frames"
            )
            # Enable min/max spinboxes
            self.vmin_spinbox.setEnabled(True)
            self.vmax_spinbox.setEnabled(True)

            # Update visual style to indicate active controls
            self.vmin_spinbox.setStyleSheet("background-color: #f0f0f0;")
            self.vmax_spinbox.setStyleSheet("background-color: #f0f0f0;")

        elif index == 1:  # Auto Per Frame
            self.range_explanation_label.setText(
                "Auto Per Frame: Min/max calculated independently for each frame"
            )
            # Disable min/max spinboxes (they'll be updated for reference only)
            self.vmin_spinbox.setEnabled(False)
            self.vmax_spinbox.setEnabled(False)

            # Update visual style
            self.vmin_spinbox.setStyleSheet("")
            self.vmax_spinbox.setStyleSheet("")

        else:  # Global Auto
            self.range_explanation_label.setText(
                "Global Auto: Min/max calculated once from all frames"
            )
            # Disable min/max spinboxes
            self.vmin_spinbox.setEnabled(False)
            self.vmax_spinbox.setEnabled(False)

            # Update visual style
            self.vmin_spinbox.setStyleSheet("")
            self.vmax_spinbox.setStyleSheet("")

        # Update preview with new settings
        self.update_preview()

    def browse_input_directory(self):
        """Browse for input directory"""
        if self.input_directory_edit.text():
            start_dir = self.input_directory_edit.text()
        elif self.current_file:
            start_dir = os.path.dirname(self.current_file)
        else:
            start_dir = os.path.expanduser("~")

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
        directory = self.input_directory_edit.text()
        pattern = self.input_pattern_edit.text()

        if not directory or not pattern:
            QMessageBox.warning(
                self, "Incomplete Input", "Please specify both directory and pattern."
            )
            return

        full_pattern = os.path.join(directory, pattern)

        # Find matching files
        files = glob.glob(full_pattern)

        if not files:
            self.files_found_label.setText("No files found matching the pattern")
            QMessageBox.warning(
                self, "No Files Found", f"No files match the pattern: {full_pattern}"
            )
            return

        # Update label with file count
        self.files_found_label.setText(f"Found {len(files)} files matching the pattern")

        # Set first file as reference if no reference is set
        if not self.reference_image:
            self.reference_image = files[0]
            self.reference_image_edit.setText(self.reference_image)
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
                        overlay_ax = self.figure.add_axes([0.65, 0.65, 0.3, 0.3])
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

        except Exception as e:
            print(f"Error updating preview: {e}")
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

    def create_video(self):
        """Create a video from the selected files"""
        try:
            # Get input files
            input_dir = self.input_directory_edit.text()
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

            # Sort the files based on selected method
            sort_method = self.sort_combo.currentText().lower()
            if sort_method == "filename":
                matching_files.sort()
            elif sort_method == "date/time":
                matching_files.sort(key=os.path.getmtime)
            elif sort_method == "extension":
                matching_files.sort(key=lambda x: os.path.splitext(x)[1])

            # Get output file
            output_file = self.output_file_edit.text()
            if not output_file:
                QMessageBox.warning(
                    self,
                    "No Output File",
                    "Please specify an output file for the video.",
                )
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
            }

            # Get overlay options
            overlay_options = {
                "timestamp": self.timestamp_check.isChecked(),
                "frame_number": self.frame_number_check.isChecked(),
                "filename": self.filename_check.isChecked(),
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

            # Get video options
            video_options = {
                "fps": self.fps_spinbox.value(),
                "quality": self.quality_spinbox.value(),
            }

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
                **video_options,
            }

            # Create the video
            from solar_radio_image_viewer.create_video import (
                create_video as create_video_function,
            )

            # Use a worker thread for video creation
            self.worker = VideoWorker(
                matching_files,
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
        self.accept()

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

    def select_region_from_preview(self):
        """Let the user select a region from the preview image"""
        if not self.reference_image or not hasattr(self, "figure"):
            QMessageBox.warning(
                self, "No Preview", "Please load a reference image first."
            )
            return

        try:
            # Enable region selection
            self.region_enabled.setChecked(True)

            # Clear the figure
            self.figure.clear()
            ax = self.figure.add_subplot(111)

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
                x1, y1 = int(min(eclick.xdata, erelease.xdata))
                x2, y2 = int(max(eclick.xdata, erelease.xdata))
                y1, y2 = int(min(eclick.ydata, erelease.ydata)), int(
                    max(eclick.ydata, erelease.ydata)
                )

                # Update spinboxes with selected region
                self.x_min_spinbox.setValue(max(0, x1))
                self.x_max_spinbox.setValue(min(data.shape[1] - 1, x2))
                self.y_min_spinbox.setValue(max(0, y1))
                self.y_max_spinbox.setValue(min(data.shape[0] - 1, y2))

                # Update preview
                self.update_preview()

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
            status_text = ax.text(
                0.5,
                0.02,
                "Click and drag to select region, then close this window",
                transform=ax.transAxes,
                ha="center",
                va="bottom",
                bbox=dict(boxstyle="round", fc="white", alpha=0.8),
            )

            # Refresh canvas
            self.canvas.draw()

            # Create a modal dialog to use for selection
            selector_dialog = QDialog(self)
            selector_dialog.setWindowTitle("Select Region")
            selector_layout = QVBoxLayout(selector_dialog)

            # Add the canvas to the dialog
            from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT

            toolbar = NavigationToolbar2QT(self.canvas, selector_dialog)
            selector_layout.addWidget(toolbar)
            selector_layout.addWidget(self.canvas)

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

        if enable_gamma:
            self.gamma_spinbox.setStyleSheet("")
        else:
            self.gamma_spinbox.setStyleSheet("background-color: #e0e0e0;")


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
                    print(
                        f"Pulsing progress: {pulsing_progress}% - Scaled: {scaled_progress}"
                    )
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
                # This is a more straightforward approach than trying to calculate times
                progress_percent = min(99, int(100 * current_frame / total_frames))
                scaled_progress = (
                    progress_percent * 10
                )  # Scale to match our progress dialog range (0-1000)

                # Add debugging output
                if (
                    current_frame % 20 == 0 or current_frame == total_frames - 1
                ):  # Print every 20 frames or last frame
                    print(
                        f"Frame {current_frame}/{total_frames} - Progress: {progress_percent}% - Scaled: {scaled_progress}"
                    )

                self.progress.emit(scaled_progress)

                # Update frames processed count for reference
                self.frames_processed = current_frame + 1

                # Let the progress thread handle the progress update
                return not self.progress_dialog.wasCanceled()

            # Create the video
            self.status_update.emit("Creating video...")
            print("Starting video creation process")
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

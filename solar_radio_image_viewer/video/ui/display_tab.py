"""
Display tab: colours, range, stretch, size and coordinates.
"""

from PyQt5.QtWidgets import (
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QGroupBox,
    QComboBox,
    QCheckBox,
    QSpinBox,
    QDoubleSpinBox,
    QMessageBox,
    QScrollArea,
    QWidget,
    QFormLayout,
)
from PyQt5.QtCore import Qt
import matplotlib.pyplot as plt
import numpy as np
from ...processing.colormaps import ensure_solar_colormaps
from ..coordinates import COORDINATE_NAMES, COORDINATES
from .helpers import (
    SIZE_PRESETS,
)


class DisplayTabMixin:
    """Display tab: colours, range, stretch, size and coordinates. Mixed into VideoCreationDialog."""

    def _create_display_tab(self, display_layout):
        """Display tab: colormap, stretch, range, frame size, presets. Returns the tab's scroll area."""
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
        ensure_solar_colormaps()
        colormaps = sorted(
            [cmap for cmap in plt.colormaps() if not cmap.endswith("_r")]
        )
        self.colormap_combo.addItems(colormaps)
        idx = self.colormap_combo.findText("viridis")
        if idx >= 0:
            self.colormap_combo.setCurrentIndex(idx)
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
        self.stretch_combo.currentIndexChanged.connect(self.update_gamma_controls)
        viz_form.addRow("Stretch:", self.stretch_combo)

        # Gamma (for power stretch)
        self.gamma_spinbox = QDoubleSpinBox()
        self.gamma_spinbox.setRange(0.1, 10.0)
        self.gamma_spinbox.setSingleStep(0.1)
        self.gamma_spinbox.setValue(1.0)
        viz_form.addRow("Gamma:", self.gamma_spinbox)

        # Colorbar option
        self.colorbar_check = QCheckBox("Show Colorbar")
        self.colorbar_check.setChecked(True)
        viz_form.addRow("", self.colorbar_check)

        self.difference_combo = QComboBox()
        for text, mode in (
            ("None", "none"),
            ("Running (each frame minus the previous)", "running"),
            ("Base (each frame minus the first)", "base"),
        ):
            self.difference_combo.addItem(text, mode)
        self.difference_combo.setToolTip(
            "Difference movies show what changes. Auto ranges become symmetric\n"
            "about zero; a diverging colormap (e.g. RdBu_r, coolwarm) suits them.\n"
            "A running difference starts at the second frame."
        )
        viz_form.addRow("Difference:", self.difference_combo)

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
        minmax_layout.addWidget(self.vmin_spinbox)

        minmax_layout.addWidget(QLabel("Max:"))
        self.vmax_spinbox = QDoubleSpinBox()
        self.vmax_spinbox.setRange(-1e10, 1e10)
        self.vmax_spinbox.setDecimals(2)
        self.vmax_spinbox.setValue(3000)
        minmax_layout.addWidget(self.vmax_spinbox)

        range_form.addRow("Values:", minmax_widget)

        # Percentile range used by the two Auto modes
        percentile_widget = QWidget()
        percentile_layout = QHBoxLayout(percentile_widget)
        percentile_layout.setContentsMargins(0, 0, 0, 0)
        percentile_layout.setSpacing(10)
        percentile_layout.addWidget(QLabel("Low:"))
        self.lower_percentile_spinbox = QDoubleSpinBox()
        self.lower_percentile_spinbox.setRange(0, 100)
        self.lower_percentile_spinbox.setDecimals(1)
        self.lower_percentile_spinbox.setSingleStep(0.5)
        self.lower_percentile_spinbox.setSuffix(" %")
        self.lower_percentile_spinbox.setValue(0)
        percentile_layout.addWidget(self.lower_percentile_spinbox)
        percentile_layout.addWidget(QLabel("High:"))
        self.upper_percentile_spinbox = QDoubleSpinBox()
        self.upper_percentile_spinbox.setRange(0, 100)
        self.upper_percentile_spinbox.setDecimals(1)
        self.upper_percentile_spinbox.setSingleStep(0.5)
        self.upper_percentile_spinbox.setSuffix(" %")
        self.upper_percentile_spinbox.setValue(100)
        percentile_layout.addWidget(self.upper_percentile_spinbox)
        percentile_widget.setToolTip(
            "Auto modes scale each frame (or all frames) from this percentile\n"
            "range of the pixel values; 0-100 % is the full min-max range"
        )
        range_form.addRow("Percentiles:", percentile_widget)

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
        self.width_spinbox.setSpecialValueText("Auto")
        size_layout.addWidget(self.width_spinbox)

        size_layout.addWidget(QLabel("Height:"))
        self.height_spinbox = QSpinBox()
        self.height_spinbox.setRange(0, 4320)
        self.height_spinbox.setValue(0)
        self.height_spinbox.setSpecialValueText("Auto")
        size_layout.addWidget(self.height_spinbox)

        size_widget.setToolTip(
            "Frame size in pixels. Auto follows the image's shape: with both on\n"
            "Auto the longer side is 1000 px; set one and the other follows."
        )
        frame_form.addRow("Size:", size_widget)

        self.size_preset_combo = QComboBox()
        for text, height in SIZE_PRESETS:
            self.size_preset_combo.addItem(text, height)
        self.size_preset_combo.setToolTip(
            "Frame height in pixels; the width follows the image's shape"
        )
        self.size_preset_combo.activated.connect(self._apply_size_preset)
        frame_form.addRow("Preset:", self.size_preset_combo)

        display_layout.addWidget(frame_group)

        # WCS Coordinates section
        wcs_group = QGroupBox("Coordinate System")
        wcs_form = QFormLayout(wcs_group)
        wcs_form.setContentsMargins(12, 16, 12, 12)
        wcs_form.setVerticalSpacing(8)

        self.coordinates_combo = QComboBox()
        for key in COORDINATES:
            self.coordinates_combo.addItem(COORDINATE_NAMES[key], key)
        self.coordinates_combo.setToolTip(
            "Show every frame in these coordinates, converting images that are\n"
            "in the other system as the main window's HPC / RA-Dec button does\n"
            "(rotated to solar north for helioprojective). Converted images are\n"
            "kept while this window is open, so each is converted once."
        )
        wcs_form.addRow("Video in:", self.coordinates_combo)

        self.wcs_coords_check = QCheckBox("Show WCS Coordinates in Video")
        self.wcs_coords_check.setChecked(True)
        self.wcs_coords_check.setToolTip(
            "Display RA/Dec or Solar-X/Solar-Y coordinates instead of pixels"
        )
        wcs_form.addRow("", self.wcs_coords_check)

        self.wcs_info_label = QLabel("Axes are labelled in the video's coordinates")
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
        return display_scroll

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

        # The percentile range applies to the Auto modes only
        self.lower_percentile_spinbox.setEnabled(index != 0)
        self.upper_percentile_spinbox.setEnabled(index != 0)

        # Update preview with new settings
        self.schedule_preview()

    def apply_auto_minmax(self):
        """Apply Auto Min/Max preset to the display range"""
        if not self.reference_image:
            return

        data = self._load_reference()
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
        self.schedule_preview()

    def apply_auto_percentile(self):
        """Apply Auto Percentile preset to the display range"""
        if not self.reference_image:
            return

        data = self._load_reference()
        if data is None:
            return

        # Calculate 1st and 99th percentiles
        vmin, vmax = np.nanpercentile(data, [1, 99])

        # Update spinboxes
        self.range_mode_combo.setCurrentIndex(0)  # Switch to fixed range
        self.vmin_spinbox.setValue(vmin)
        self.vmax_spinbox.setValue(vmax)

        # Update preview
        self.schedule_preview()

    def apply_auto_median_rms(self):
        """Apply Auto Median ± 3×RMS preset to the display range"""
        if not self.reference_image:
            return

        data = self._load_reference()
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
        self.schedule_preview()

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
        self.schedule_preview()

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
        self.schedule_preview()

    def update_gamma_controls(self):
        """Enable/disable gamma controls based on the selected stretch type"""
        stretch = self.stretch_combo.currentText().lower()
        enable_gamma = stretch == "power"

        self.gamma_spinbox.setEnabled(enable_gamma)

    def _load_reference(self):
        """Data of the reference (previewed) image as the video shows it, in
        the video's coordinates; None (and a message) if it can't be read."""
        from ..coordinates import ConversionError
        from ..creator import load_frame_data

        try:
            data, _ = load_frame_data(self.reference_image, self._video_options())
        except ConversionError as e:
            QMessageBox.warning(self, "Cannot Convert", str(e))
            return None
        return data

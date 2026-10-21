"""
Region tab: the part of the image shown.
"""

from PyQt5.QtWidgets import (
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QGroupBox,
    QCheckBox,
    QSpinBox,
    QMessageBox,
    QScrollArea,
    QWidget,
    QFormLayout,
)


class RegionTabMixin:
    """Region tab: the part of the image shown. Mixed into VideoCreationDialog."""

    def _create_region_tab(self, region_layout):
        """Region tab: optional sub-region selection. Returns the tab's scroll area."""
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
        x_range_layout.addWidget(self.x_min_spinbox)
        x_range_layout.addWidget(QLabel("to"))
        self.x_max_spinbox = QSpinBox()
        self.x_max_spinbox.setRange(0, 10000)
        self.x_max_spinbox.setValue(1000)
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
        y_range_layout.addWidget(self.y_min_spinbox)
        y_range_layout.addWidget(QLabel("to"))
        self.y_max_spinbox = QSpinBox()
        self.y_max_spinbox.setRange(0, 10000)
        self.y_max_spinbox.setValue(1000)
        y_range_layout.addWidget(self.y_max_spinbox)
        y_range_layout.addWidget(QLabel("px"))
        y_range_layout.addStretch()
        coord_form.addRow("Y Range:", y_range_widget)

        region_main_layout.addLayout(coord_form)

        drag_hint = QLabel(
            "Or drag a box on the preview, and use the zoom buttons above it "
            "(zoom in / out, whole image, 1°×1°) as in the main window."
        )
        drag_hint.setWordWrap(True)
        drag_hint.setObjectName("SecondaryText")
        region_main_layout.addWidget(drag_hint)

        self.track_rotation_check = QCheckBox("Follow solar rotation")
        self.track_rotation_check.setToolTip(
            "Move the region with the Sun's differential rotation from the first\n"
            "frame, so a feature stays in view (helioprojective images, on the disk)"
        )
        region_main_layout.addWidget(self.track_rotation_check)

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
            "Make a video of only part of the image. Region pixels are those of "
            "the images in the video's coordinates (Display tab)."
        )
        help_label.setWordWrap(True)
        help_label.setObjectName("SecondaryText")
        region_layout.addWidget(help_label)

        # Initially disable the region controls
        self.toggle_region_controls(False)

        region_layout.addStretch()
        return region_scroll

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
            data = self._load_reference()

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
            self.schedule_preview()

        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not set region preset: {str(e)}")

    def toggle_region_controls(self, enabled):
        """Enable or disable region controls"""
        self.x_min_spinbox.setEnabled(enabled)
        self.x_max_spinbox.setEnabled(enabled)
        self.y_min_spinbox.setEnabled(enabled)
        self.y_max_spinbox.setEnabled(enabled)

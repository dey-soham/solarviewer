"""
The image tab's side panels: Image Selection and Display Settings on the
left, the statistics and Cursor Position panels on the right.
"""

import logging
import matplotlib.pyplot as plt
from PyQt5.QtWidgets import (
    QWidget,
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
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QFormLayout,
)
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QIcon
from ..ui.styles import (
    theme_manager,
)
from ..ui.searchable_combobox import ColormapSelector
from ..ui.icons import get_resource_path, themed_icon
from ..ui.item_delegates import DisabledItemDelegate

logger = logging.getLogger(__name__)

# Compact buttons for actions on the statistics panels
_SECONDARY_BUTTON_STYLE = """
    QPushButton {
        padding: 4px 10px;
        font-size: 11pt;
        min-height: 24px;
    }
"""

def _min_width_for_label(button):
    """Make the stylesheet's minimum width of ``button`` that of its label.

    The application's stylesheet gives every button the same min-width,
    which Qt then uses as the button's minimum instead of its label's width:
    in a narrow panel the label is cut off. (A minimum set in code would be
    overwritten whenever the stylesheet is applied again.)
    """
    button.ensurePolished()
    label = button.fontMetrics().horizontalAdvance(button.text())
    button.setStyleSheet(
        button.styleSheet() + f"\nQPushButton {{ min-width: {label}px; }}"
    )

def _toggle_colors():
    """(track, track hovered, border, border hovered, text) colours of the
    switch-style checkboxes in the current theme."""
    if theme_manager.is_dark:
        return "#3a3d4d", "#4a4d5d", "#4a4d5d", "#5a5d6d", "#e0e0e0"
    return "#d0d0d0", "#c0c0c0", "#b0b0b0", "#a0a0a0", "#333333"

def _overlay_toggle_style():
    """Style of the Overlays switches (Beam, Grid, Solar Disk, Contours).
    10pt like the preset buttons: at 11pt the two columns no longer fit the
    panel's default width, and the settings buttons were cut off."""
    bg, bg_hover, border, border_hover, text = _toggle_colors()
    return f"""
        QCheckBox {{
            spacing: 5px;
            font-size: 10pt;
            color: {text};
        }}
        QCheckBox:disabled {{
            color: #4a4a6a;
        }}
        QCheckBox::indicator {{
            width: 28px;
            height: 14px;
            border-radius: 7px;
            background-color: {bg};
            border: 1px solid {border};
        }}
        QCheckBox::indicator:hover {{
            background-color: {bg_hover};
            border-color: {border_hover};
        }}
        QCheckBox::indicator:checked {{
            background-color: #6366f1;
            border-color: #818cf8;
        }}
        QCheckBox::indicator:checked:hover {{
            background-color: #7c7ff5;
        }}
        QCheckBox::indicator:disabled {{
            background-color: {bg};
            border-color: {border};
        }}
    """

# The small settings (gear) buttons next to the Overlays switches
_SETTINGS_BUTTON_STYLE = """
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


class PanelsMixin:
    """Builds the tab's side panels and keeps their styles in step with the theme.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``_apply_gamma_change``, ``_on_threshold_edited``,
    ``_show_roi_histogram``, ``apply_auto_range``, ``current_image_data``,
    ``imagename``, ``invalidate_contours``, ``on_checkbox_changed``,
    ``on_stokes_changed``, ``on_stretch_changed``,
    ``on_visualization_changed``, ``open_helioviewer_with_time``,
    ``reset_view``, ``select_file_or_directory``, ``set_solar_disk_center``,
    ``show_beam_settings``, ``show_contour_settings``, ``show_grid_settings``,
    ``show_noaa_events_for_current_image``, ``show_rms_box_dialog``,
    ``update_display``, ``update_gamma_slider``, ``update_gamma_value``
    """

    def create_file_controls(self, parent_layout):
        group = QGroupBox("Image Selection")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        file_layout = QHBoxLayout()
        file_layout.setSpacing(8)
        self.dir_entry = QLineEdit()
        self.dir_entry.setPlaceholderText("FITS file or CASA image")
        self.dir_entry.setReadOnly(True)
        self.browse_btn = QPushButton()
        self.browse_btn.setObjectName("IconOnlyNBGButton")
        self.browse_btn.setIcon(QIcon(get_resource_path("assets/browse.png")))
        self.browse_btn.setIconSize(QSize(32, 32))
        self.browse_btn.setToolTip("Open a FITS file or CASA image (Ctrl+O)")
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
        threshold_tip = (
            "Threshold, in units of the RMS (σ) measured in the RMS box.\n"
            "Lfrac, Vfrac, Q/I, U/I and U/V: pixels whose numerator is below "
            "threshold × RMS are set to 0.\n"
            "PANG: pixels whose polarized intensity is below it are left blank.\n"
            "I, Q, U, V and L are shown as they are."
        )
        thresh_label.setToolTip(threshold_tip)
        self.threshold_entry = QLineEdit("10")
        self.threshold_entry.setFixedWidth(50)
        self.threshold_entry.setToolTip(threshold_tip)
        self.threshold_entry.editingFinished.connect(self._on_threshold_edited)

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
        rms_tip = (
            "RMS box: where the noise (RMS) is measured, by default a band "
            "along the bottom of the image.\nThe RMS sets the threshold of the "
            "derived Stokes products, the image statistics (RMS, dynamic range) "
            "and contour levels in σ.\nClick the gear to change the box."
        )
        self.rms_label.setToolTip(rms_tip)

        self.rms_settings_btn = QPushButton()
        self.rms_settings_btn.setObjectName("IconOnlyNBGButton")
        self.rms_settings_btn.setIcon(QIcon(themed_icon("settings.png")))
        self.rms_settings_btn.setIconSize(QSize(20, 20))
        self.rms_settings_btn.setToolTip(rms_tip)
        self.rms_settings_btn.setAccessibleName("RMS box settings")
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

        overlay_toggle_style = _overlay_toggle_style()
        settings_btn_style = _SETTINGS_BUTTON_STYLE

        # Row 0: Show Beam + settings | Show Grid + settings
        self.show_beam_checkbox = QCheckBox("")
        self.show_beam_checkbox.setAccessibleName("Beam")
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
        # (no min-width: it would let a narrow panel squeeze the labels)
        preset_btn_style = """
            QPushButton {
                padding: 3px 6px;
                font-size: 10pt;
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

        # Display changes apply as they are made; this reads the image again,
        # which is rarely needed: a small button like the presets
        self.plot_button = QPushButton("Reload Image")
        self.plot_button.setToolTip("Read the image again from disk")
        self.plot_button.setStyleSheet(preset_btn_style)
        self.plot_button.clicked.connect(self.on_visualization_changed)
        reload_row = QHBoxLayout()
        reload_row.addStretch()
        reload_row.addWidget(self.plot_button)
        main_layout.addLayout(reload_row)

        parent_layout.addWidget(group)

    def create_stats_table(self, parent_layout):

        group = QGroupBox("Region Statistics")
        layout = QVBoxLayout(group)
        self.info_label = QLabel(
            "No selection. Draw a rectangle or ellipse on the image."
        )
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
        self.histogram_btn.setStyleSheet(_SECONDARY_BUTTON_STYLE)
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

        # Short labels, so the values keep their room in a narrow panel
        headers = ["Max", "Min", "RMS", "Box mean", "Pos. DR", "Neg. DR"]
        tips = {
            "RMS": "RMS inside the RMS box",
            "Box mean": "Mean inside the RMS box",
            "Pos. DR": "Positive dynamic range: Max / RMS",
            "Neg. DR": "Negative dynamic range: Min / RMS",
        }
        for row, label in enumerate(headers):
            item = QTableWidgetItem(label)
            if label in tips:
                item.setToolTip(tips[label])
            self.image_stats_table.setItem(row, 0, item)
            self.image_stats_table.setItem(row, 1, QTableWidgetItem("−"))
            self.image_stats_table.item(row, 1).setTextAlignment(
                Qt.AlignRight | Qt.AlignVCenter
            )
        self.image_stats_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        layout.addWidget(self.image_stats_table)
        self.create_noaa_events_button(layout)
        parent_layout.addWidget(group)

    def create_noaa_events_button(self, parent_layout):
        """Solar Activity and Helioviewer buttons for the image's observation time."""
        # Create horizontal layout for both buttons side-by-side
        buttons_layout = QHBoxLayout()

        # Plain text: Qt measures emoji narrower than they are drawn, and
        # clipped the labels when the panel is narrow
        self.noaa_events_btn = QPushButton("Activity")
        self.noaa_events_btn.setToolTip(
            "View Solar Activity (Events, Active Regions, Conditions, CMEs)"
        )
        self.noaa_events_btn.setEnabled(False)  # Disabled by default
        self.noaa_events_btn.setStyleSheet(_SECONDARY_BUTTON_STYLE)
        self.noaa_events_btn.clicked.connect(self.show_noaa_events_for_current_image)
        _min_width_for_label(self.noaa_events_btn)
        buttons_layout.addWidget(self.noaa_events_btn)

        self.helioviewer_btn = QPushButton("Helioviewer")
        self.helioviewer_btn.setToolTip(
            "Browse Helioviewer images around current observation time"
        )
        self.helioviewer_btn.setEnabled(False)  # Disabled by default
        self.helioviewer_btn.setStyleSheet(_SECONDARY_BUTTON_STYLE)
        self.helioviewer_btn.clicked.connect(self.open_helioviewer_with_time)
        _min_width_for_label(self.helioviewer_btn)
        buttons_layout.addWidget(self.helioviewer_btn)

        # Add horizontal layout to parent
        parent_layout.addLayout(buttons_layout)

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

    def _update_overlay_toggle_styles(self):
        """Update overlay checkbox and button styles based on current theme."""
        for checkbox in (
            self.show_beam_checkbox,
            self.show_grid_checkbox,
            self.show_solar_disk_checkbox,
            self.show_contours_checkbox,
        ):
            checkbox.setStyleSheet(_overlay_toggle_style())
        for btn in (
            self.beam_settings_button,
            self.grid_settings_button,
            self.solar_disk_center_button,
            self.contour_settings_button,
        ):
            btn.setStyleSheet(_SETTINGS_BUTTON_STYLE)

        # Update Fast Load toggle style
        if hasattr(self, "downsample_toggle"):
            toggle_bg, toggle_bg_hover, toggle_border, _, _ = _toggle_colors()
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

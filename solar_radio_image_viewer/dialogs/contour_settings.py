from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QRadioButton,
    QLineEdit,
    QLabel,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QGridLayout,
    QDialogButtonBox,
    QComboBox,
    QCheckBox,
    QFileDialog,
    QButtonGroup,
    QWidget,
    QFrame,
    QTabWidget,
)
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import Qt
import os

from ..ui.icons import get_resource_path
from ..ui.styles import set_hand_cursor




class ContourSettingsDialog(QDialog):
    """Dialog for configuring contour settings with a more compact layout."""

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self.setWindowTitle("Contour Settings")
        self.settings = settings.copy() if settings else {}

        # Import theme manager for theme-aware styling
        try:
            from ..ui.styles import theme_manager
        except ImportError:
            from ..ui.styles import theme_manager

        # Get colors directly from the palette for consistency
        palette = theme_manager.palette
        is_dark = theme_manager.is_dark

        border_color = palette["border"]
        surface_color = palette["surface"]
        base_color = palette["base"]
        disabled_color = palette["disabled"]
        button_hover = palette["button_hover"]
        button_pressed = palette["button_pressed"]
        text_color = palette["text"]
        highlight_color = palette["highlight"]

        # Set stylesheet BEFORE creating widgets so styles apply correctly
        self.setStyleSheet(
            f"""
            QGroupBox {{
                background-color: {surface_color};
                border: 1px solid {border_color};
                border-radius: 10px;
                margin-top: 16px;
                padding: 15px;
                padding-top: 10px;
                font-weight: bold;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 15px;
                top: 2px;
                padding: 2px 12px;
                background-color: {surface_color};
                color: {highlight_color};
                border-radius: 4px;
            }}
            QLineEdit {{
                background-color: {base_color};
                color: {text_color};
                padding: 5px;
                border: 1px solid {border_color};
                border-radius: 6px;
            }}
            QLineEdit:focus {{
                border-color: {highlight_color};
                border-width: 2px;
            }}
            QLineEdit:disabled {{
                background-color: {surface_color};
                color: {disabled_color};
            }}
            QComboBox {{
                background-color: {base_color};
                color: {text_color};
                padding: 5px;
                border: 1px solid {border_color};
                border-radius: 6px;
            }}
            QComboBox:hover {{
                border-color: {highlight_color};
            }}
            QComboBox:disabled {{
                background-color: {surface_color};
                color: {disabled_color};
            }}
            QRadioButton {{
                color: {text_color};
            }}
            QRadioButton::indicator {{
                width: 18px;
                height: 18px;
                border: 2px solid {border_color};
                border-radius: 9px;
                background-color: {base_color};
            }}
            QRadioButton::indicator:checked {{
                background-color: {highlight_color};
                border-color: {highlight_color};
            }}
            QRadioButton:disabled {{
                color: {disabled_color};
            }}
            QSpinBox, QDoubleSpinBox {{
                background-color: {base_color};
                color: {text_color};
                border: 1px solid {border_color};
                border-radius: 6px;
            }}
            QSpinBox:disabled, QDoubleSpinBox:disabled {{
                background-color: {surface_color};
                color: {disabled_color};
            }}
            QCheckBox {{
                color: {text_color};
            }}
            QCheckBox::indicator {{
                width: 18px;
                height: 18px;
                border: 2px solid {border_color};
                border-radius: 4px;
                background-color: {base_color};
            }}
            QCheckBox::indicator:checked {{
                background-color: {highlight_color};
                border-color: {highlight_color};
            }}
            QLabel {{
                color: {text_color};
            }}
            QLabel:disabled {{
                color: {disabled_color};
            }}
        """
        )

        # Store theme colors for use in browse button
        self._hover_bg = button_hover
        self._pressed_bg = button_pressed

        self.setup_ui()
        set_hand_cursor(self)

    def setup_ui(self):
        from PyQt5.QtGui import QPixmap, QColor

        # Get theme colors for styling
        try:
            from ..ui.styles import theme_manager
        except ImportError:
            from ..ui.styles import theme_manager

        palette = theme_manager.palette
        is_dark = theme_manager.is_dark
        highlight_color = palette["highlight"]
        border_color = palette["border"]
        surface_color = palette["surface"]
        text_secondary = palette.get("text_secondary", palette["disabled"])

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(12, 12, 12, 12)

        # ========== TAB WIDGET ==========
        self.tab_widget = QTabWidget()

        # ========== TAB 1: SOURCE ==========
        source_tab = QWidget()
        source_layout = QVBoxLayout(source_tab)
        source_layout.setSpacing(20)
        source_layout.setContentsMargins(20, 20, 20, 20)

        # Source selection
        source_row = QHBoxLayout()
        source_row.setSpacing(20)

        source_label = QLabel("Source:")
        source_label.setStyleSheet("font-weight: 600;")
        source_row.addWidget(source_label)

        self.same_image_radio = QRadioButton("Current Image")
        self.external_image_radio = QRadioButton("External")
        if self.settings.get("source") == "external":
            self.external_image_radio.setChecked(True)
        else:
            self.same_image_radio.setChecked(True)
        source_row.addWidget(self.same_image_radio)
        source_row.addWidget(self.external_image_radio)
        source_row.addStretch()
        source_layout.addLayout(source_row)

        # Stokes selection with noise threshold on same row
        stokes_row = QHBoxLayout()
        stokes_row.setSpacing(20)

        stokes_label = QLabel("Stokes:")
        stokes_label.setStyleSheet("font-weight: 600;")
        stokes_row.addWidget(stokes_label)

        self.stokes_combo = QComboBox()
        self.stokes_combo.addItems(
            ["I", "Q", "U", "V", "Q/I", "U/I", "V/I", "L", "Lfrac", "PANG"]
        )
        self.stokes_combo.setMinimumWidth(100)
        current_stokes = self.settings.get("stokes", "I")
        self.stokes_combo.setCurrentText(current_stokes)
        stokes_row.addWidget(self.stokes_combo)

        stokes_row.addSpacing(30)

        # Noise threshold for derived Stokes parameters (Q/I, U/I, V/I, Lfrac, PANG)
        self.threshold_label = QLabel("Threshold (σ):")
        self.threshold_label.setStyleSheet("font-weight: 600;")
        self.threshold_label.setToolTip(
            "Pixels with signal below this many σ (noise RMS) are masked.\n"
            "Only applies to derived parameters: Q/I, U/I, V/I, Lfrac, Vfrac, PANG."
        )
        stokes_row.addWidget(self.threshold_label)

        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.0, 50.0)
        self.threshold_spin.setSingleStep(0.5)
        self.threshold_spin.setDecimals(1)
        self.threshold_spin.setValue(self.settings.get("threshold", 5.0))
        self.threshold_spin.setMinimumWidth(70)
        self.threshold_spin.setToolTip(
            "Noise threshold in units of σ (RMS noise).\n"
            "Default: 5.0 (pixels below 5σ are masked)"
        )
        stokes_row.addWidget(self.threshold_spin)

        stokes_row.addStretch()
        source_layout.addLayout(stokes_row)

        # Connect stokes combo to update threshold visibility
        self.stokes_combo.currentTextChanged.connect(self._update_threshold_visibility)

        # External file options
        self.external_group = QWidget()
        external_layout = QVBoxLayout(self.external_group)
        external_layout.setSpacing(12)
        external_layout.setContentsMargins(0, 12, 0, 0)

        # Separator line
        sep_line = QFrame()
        sep_line.setFrameShape(QFrame.HLine)
        sep_line.setStyleSheet(f"background-color: {border_color};")
        sep_line.setFixedHeight(1)
        external_layout.addWidget(sep_line)

        # File type
        file_type_row = QHBoxLayout()
        file_type_row.setSpacing(20)

        file_type_label = QLabel("File Type:")
        file_type_label.setStyleSheet("font-weight: 600;")
        file_type_row.addWidget(file_type_label)

        self.radio_casa_image = QRadioButton("CASA Image")
        self.radio_fits_file = QRadioButton("FITS File")
        self.radio_casa_image.setChecked(True)
        file_type_row.addWidget(self.radio_casa_image)
        file_type_row.addWidget(self.radio_fits_file)
        file_type_row.addStretch()
        external_layout.addLayout(file_type_row)

        # File path
        path_row = QHBoxLayout()
        path_row.setSpacing(12)

        path_label = QLabel("Path:")
        path_label.setStyleSheet("font-weight: 600;")
        path_row.addWidget(path_label)

        self.file_path_edit = QLineEdit(self.settings.get("external_image", ""))
        self.file_path_edit.setPlaceholderText("Select file or directory...")
        self.file_path_edit.setReadOnly(True)
        path_row.addWidget(self.file_path_edit, 1)

        self.browse_button = QPushButton("Browse...")
        self.browse_button.clicked.connect(self.browse_file)
        path_row.addWidget(self.browse_button)
        external_layout.addLayout(path_row)

        # Store icon references for theme switching
        self.browse_icon_light = QIcon(get_resource_path("assets/browse.png"))
        self.browse_icon_dark = QIcon(get_resource_path("assets/browse_light.png"))

        source_layout.addWidget(self.external_group)
        source_layout.addStretch()

        # Connections
        self.file_type_button_group = QButtonGroup()
        self.file_type_button_group.addButton(self.radio_casa_image)
        self.file_type_button_group.addButton(self.radio_fits_file)

        self.external_image_radio.toggled.connect(self.update_external_options)
        self.radio_casa_image.toggled.connect(self.update_placeholder_text)
        self.radio_fits_file.toggled.connect(self.update_placeholder_text)
        self.same_image_radio.toggled.connect(self._update_stokes_for_current_source)
        self.external_image_radio.toggled.connect(
            self._update_stokes_for_current_source
        )

        self.update_external_options(self.external_image_radio.isChecked())
        self.update_placeholder_text()
        self._update_stokes_for_current_source()
        self._update_threshold_visibility()

        self.tab_widget.addTab(source_tab, "Source")

        # ========== TAB 2: LEVELS ==========
        levels_tab = QWidget()
        levels_layout = QVBoxLayout(levels_tab)
        levels_layout.setSpacing(20)
        levels_layout.setContentsMargins(20, 20, 20, 20)

        # Level type
        type_row = QHBoxLayout()
        type_row.setSpacing(20)

        type_label = QLabel("Level Type:")
        type_label.setStyleSheet("font-weight: 600;")
        type_row.addWidget(type_label)

        self.level_type_combo = QComboBox()
        self.level_type_combo.addItems(["fraction", "sigma", "absolute"])
        current_level_type = self.settings.get("level_type", "fraction")
        self.level_type_combo.setCurrentText(current_level_type)
        self.level_type_combo.setMinimumWidth(120)
        type_row.addWidget(self.level_type_combo)
        type_row.addStretch()
        levels_layout.addLayout(type_row)

        # Presets
        preset_row = QHBoxLayout()
        preset_row.setSpacing(12)

        presets_label = QLabel("Presets:")
        presets_label.setStyleSheet("font-weight: 600;")
        preset_row.addWidget(presets_label)

        self.preset_5_btn = QPushButton("5 Levels")
        self.preset_5_btn.clicked.connect(lambda: self._apply_level_preset("5levels"))
        preset_row.addWidget(self.preset_5_btn)

        self.preset_3_btn = QPushButton("3 Levels")
        self.preset_3_btn.clicked.connect(lambda: self._apply_level_preset("3levels"))
        preset_row.addWidget(self.preset_3_btn)

        self.preset_dense_btn = QPushButton("Dense")
        self.preset_dense_btn.clicked.connect(lambda: self._apply_level_preset("dense"))
        preset_row.addWidget(self.preset_dense_btn)

        preset_row.addStretch()
        levels_layout.addLayout(preset_row)

        # Level values
        pos_row = QHBoxLayout()
        pos_row.setSpacing(12)
        pos_label = QLabel("Positive:")
        pos_label.setStyleSheet("font-weight: 600;")
        pos_label.setMinimumWidth(70)
        pos_row.addWidget(pos_label)

        self.pos_levels_edit = QLineEdit(
            ", ".join(
                str(l)
                for l in self.settings.get("pos_levels", [0.1, 0.3, 0.5, 0.7, 0.9])
            )
        )
        self.pos_levels_edit.setPlaceholderText("e.g., 0.1, 0.3, 0.5, 0.7, 0.9")
        pos_row.addWidget(self.pos_levels_edit)
        levels_layout.addLayout(pos_row)

        neg_row = QHBoxLayout()
        neg_row.setSpacing(12)
        neg_label = QLabel("Negative:")
        neg_label.setStyleSheet("font-weight: 600;")
        neg_label.setMinimumWidth(70)
        neg_row.addWidget(neg_label)

        self.neg_levels_edit = QLineEdit(
            ", ".join(
                str(l)
                for l in self.settings.get("neg_levels", [0.1, 0.3, 0.5, 0.7, 0.9])
            )
        )
        self.neg_levels_edit.setPlaceholderText("e.g., 0.1, 0.3, 0.5, 0.7, 0.9")
        neg_row.addWidget(self.neg_levels_edit)
        levels_layout.addLayout(neg_row)

        levels_layout.addStretch()

        self.level_type_combo.currentTextChanged.connect(self.on_level_type_changed)

        self.tab_widget.addTab(levels_tab, "Levels")

        # ========== TAB 3: APPEARANCE ==========
        appearance_tab = QWidget()
        appearance_layout = QVBoxLayout(appearance_tab)
        appearance_layout.setSpacing(16)
        appearance_layout.setContentsMargins(20, 20, 20, 20)

        colors = [
            "white",
            "black",
            "red",
            "green",
            "blue",
            "yellow",
            "cyan",
            "magenta",
            "orange",
            "lime",
        ]

        # Helper to create color combo
        def create_color_combo(default_color):
            combo = QComboBox()
            for color in colors:
                combo.addItem(color)
                idx = combo.count() - 1
                pixmap = QPixmap(16, 16)
                pixmap.fill(QColor(color))
                combo.setItemIcon(idx, QIcon(pixmap))
            combo.setCurrentText(default_color)
            combo.setMinimumWidth(110)
            return combo

        # ===== Positive Contours Section =====
        pos_header = QLabel("Positive Contours")
        pos_header.setStyleSheet("font-weight: 600; font-size: 11pt;")
        appearance_layout.addWidget(pos_header)

        pos_row = QHBoxLayout()
        pos_row.setSpacing(16)

        pos_color_label = QLabel("Color:")
        pos_row.addWidget(pos_color_label)
        self.pos_color_combo = create_color_combo(
            self.settings.get("pos_color", self.settings.get("color", "white"))
        )
        pos_row.addWidget(self.pos_color_combo)

        pos_row.addSpacing(10)

        pos_width_label = QLabel("Width:")
        pos_row.addWidget(pos_width_label)
        self.pos_linewidth_spin = QDoubleSpinBox()
        self.pos_linewidth_spin.setRange(0.5, 5.0)
        self.pos_linewidth_spin.setSingleStep(0.5)
        self.pos_linewidth_spin.setValue(
            self.settings.get("pos_linewidth", self.settings.get("linewidth", 1.0))
        )
        self.pos_linewidth_spin.setMinimumWidth(70)
        pos_row.addWidget(self.pos_linewidth_spin)

        pos_row.addSpacing(10)

        pos_style_label = QLabel("Style:")
        pos_row.addWidget(pos_style_label)
        self.pos_linestyle_combo = QComboBox()
        self.pos_linestyle_combo.addItems(["─", "- -", "-·-", "···"])
        linestyle_map = {"-": 0, "--": 1, "-.": 2, ":": 3}
        current_pos = self.settings.get("pos_linestyle", "-")
        self.pos_linestyle_combo.setCurrentIndex(linestyle_map.get(current_pos, 0))
        self.pos_linestyle_combo.setMinimumWidth(90)
        pos_row.addWidget(self.pos_linestyle_combo)

        pos_row.addStretch()
        appearance_layout.addLayout(pos_row)

        # ===== Negative Contours Section =====
        neg_header = QLabel("Negative Contours")
        neg_header.setStyleSheet("font-weight: 600; font-size: 11pt;")
        appearance_layout.addWidget(neg_header)

        neg_row = QHBoxLayout()
        neg_row.setSpacing(16)

        neg_color_label = QLabel("Color:")
        neg_row.addWidget(neg_color_label)
        self.neg_color_combo = create_color_combo(
            self.settings.get("neg_color", self.settings.get("color", "white"))
        )
        neg_row.addWidget(self.neg_color_combo)

        neg_row.addSpacing(10)

        neg_width_label = QLabel("Width:")
        neg_row.addWidget(neg_width_label)
        self.neg_linewidth_spin = QDoubleSpinBox()
        self.neg_linewidth_spin.setRange(0.5, 5.0)
        self.neg_linewidth_spin.setSingleStep(0.5)
        self.neg_linewidth_spin.setValue(
            self.settings.get("neg_linewidth", self.settings.get("linewidth", 1.0))
        )
        self.neg_linewidth_spin.setMinimumWidth(70)
        neg_row.addWidget(self.neg_linewidth_spin)

        neg_row.addSpacing(10)

        neg_style_label = QLabel("Style:")
        neg_row.addWidget(neg_style_label)
        self.neg_linestyle_combo = QComboBox()
        self.neg_linestyle_combo.addItems(["─", "- -", "-·-", "···"])
        current_neg = self.settings.get("neg_linestyle", "--")
        self.neg_linestyle_combo.setCurrentIndex(linestyle_map.get(current_neg, 1))
        self.neg_linestyle_combo.setMinimumWidth(90)
        neg_row.addWidget(self.neg_linestyle_combo)

        neg_row.addStretch()
        appearance_layout.addLayout(neg_row)

        # ===== Options Section =====
        appearance_layout.addSpacing(8)

        options_header = QLabel("Options")
        options_header.setStyleSheet("font-weight: 600; font-size: 11pt;")
        appearance_layout.addWidget(options_header)

        options_row = QHBoxLayout()
        options_row.setSpacing(30)

        self.show_labels_checkbox = QCheckBox("Contour labels")
        self.show_labels_checkbox.setChecked(self.settings.get("show_labels", False))
        self.show_labels_checkbox.setToolTip(
            "Display level values on the contour lines"
        )
        options_row.addWidget(self.show_labels_checkbox)

        self.show_full_extent_checkbox = QCheckBox("Full extent")
        self.show_full_extent_checkbox.setChecked(
            self.settings.get("show_full_extent", True)
        )
        self.show_full_extent_checkbox.setToolTip(
            "Show contours beyond 1.5x image boundaries"
        )
        options_row.addWidget(self.show_full_extent_checkbox)

        self.downsample_checkbox = QCheckBox("Downsample")
        self.downsample_checkbox.setChecked(self.settings.get("downsample", True))
        self.downsample_checkbox.setToolTip(
            "Trace contours on at most 2048x2048 pixels of large contour images (faster)"
        )
        options_row.addWidget(self.downsample_checkbox)

        options_row.addStretch()
        appearance_layout.addLayout(options_row)

        appearance_layout.addStretch()

        self.tab_widget.addTab(appearance_tab, "Appearance")

        # ========== TAB 4: RMS ==========
        rms_tab = QWidget()
        rms_layout = QVBoxLayout(rms_tab)
        rms_layout.setSpacing(20)
        rms_layout.setContentsMargins(20, 20, 20, 20)

        # Default checkbox
        self.use_default_rms_box = QCheckBox("Use default region")
        self.use_default_rms_box.setChecked(
            self.settings.get("use_default_rms_region", True)
        )
        self.use_default_rms_box.setStyleSheet("font-weight: 500;")
        self.use_default_rms_box.stateChanged.connect(self.toggle_rms_inputs)
        rms_layout.addWidget(self.use_default_rms_box)

        # Info
        info_label = QLabel(
            "Define a source-free region for RMS/noise calculation (σ levels)."
        )
        info_label.setStyleSheet(f"color: {text_secondary}; font-size: 10pt;")
        rms_layout.addWidget(info_label)

        # Custom region inputs
        self.rms_inputs_container = QWidget()
        rms_grid = QGridLayout(self.rms_inputs_container)
        rms_grid.setSpacing(16)
        rms_grid.setContentsMargins(0, 8, 0, 0)

        self.rms_xmin_label = QLabel("X min:")
        rms_grid.addWidget(self.rms_xmin_label, 0, 0)
        self.rms_xmin = QSpinBox()
        self.rms_xmin.setRange(0, 10000)
        self.rms_xmin.setValue(self.settings.get("rms_box", (0, 200, 0, 130))[0])
        rms_grid.addWidget(self.rms_xmin, 0, 1)

        self.rms_xmax_label = QLabel("X max:")
        rms_grid.addWidget(self.rms_xmax_label, 0, 2)
        self.rms_xmax = QSpinBox()
        self.rms_xmax.setRange(0, 10000)
        self.rms_xmax.setValue(self.settings.get("rms_box", (0, 200, 0, 130))[1])
        rms_grid.addWidget(self.rms_xmax, 0, 3)

        self.rms_ymin_label = QLabel("Y min:")
        rms_grid.addWidget(self.rms_ymin_label, 1, 0)
        self.rms_ymin = QSpinBox()
        self.rms_ymin.setRange(0, 10000)
        self.rms_ymin.setValue(self.settings.get("rms_box", (0, 200, 0, 130))[2])
        rms_grid.addWidget(self.rms_ymin, 1, 1)

        self.rms_ymax_label = QLabel("Y max:")
        rms_grid.addWidget(self.rms_ymax_label, 1, 2)
        self.rms_ymax = QSpinBox()
        self.rms_ymax.setRange(0, 10000)
        self.rms_ymax.setValue(self.settings.get("rms_box", (0, 200, 0, 130))[3])
        rms_grid.addWidget(self.rms_ymax, 1, 3)

        w = self.settings.get("dim_w", 0)
        h = self.settings.get("dim_h", 0)
        dim_text = f"{w} x {h}" if w > 0 and h > 0 else "Unknown"
        self.rms_dims_label = QLabel(f"Contour Dimensions: {dim_text}")
        self.rms_dims_label.setStyleSheet(f"color: {text_secondary}; font-size: 9pt;")
        rms_grid.addWidget(self.rms_dims_label, 2, 0, 1, 4, Qt.AlignLeft)

        rms_layout.addWidget(self.rms_inputs_container)
        rms_layout.addStretch()

        self.toggle_rms_inputs()

        self.tab_widget.addTab(rms_tab, "RMS")

        main_layout.addWidget(self.tab_widget)

        # ========== BUTTON BOX ==========
        button_row = QHBoxLayout()
        button_row.setSpacing(12)

        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: transparent;
                border: none;
                color: {text_secondary};
                font-size: 10pt;
                padding: 6px 10px;
            }}
            QPushButton:hover {{
                color: {highlight_color};
            }}
        """
        )
        reset_btn.clicked.connect(self._reset_to_defaults)
        button_row.addWidget(reset_btn)

        button_row.addStretch()

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        ok_button = button_box.button(QDialogButtonBox.Ok)
        if ok_button:
            ok_button.setText("Apply")
            ok_button.setMinimumWidth(100)
            ok_button.setStyleSheet(
                f"""
                QPushButton {{
                    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, 
                        stop:0 {palette.get('button_gradient_start', highlight_color)}, 
                        stop:1 {palette.get('button_gradient_end', palette.get('highlight_hover', highlight_color))});
                    color: #ffffff;
                    border: none;
                    border-radius: 6px;
                    padding: 8px 20px;
                    font-weight: 600;
                }}
                QPushButton:hover {{
                    background-color: {palette.get('highlight_hover', highlight_color)};
                }}
            """
            )

        cancel_button = button_box.button(QDialogButtonBox.Cancel)
        if cancel_button:
            cancel_button.setText("Close")
            cancel_button.setMinimumWidth(80)

        button_row.addWidget(button_box)
        main_layout.addLayout(button_row)

    def toggle_rms_inputs(self):
        """Update the enabled state and visual appearance of RMS inputs."""
        use_default = self.use_default_rms_box.isChecked()

        # Hide/show the RMS inputs container
        if hasattr(self, "rms_inputs_container"):
            self.rms_inputs_container.setVisible(not use_default)

    def update_external_options(self, enabled):
        """Update the enabled state and visual appearance of external options."""
        # print(f"update_external_options called with enabled={enabled}")

        # Explicitly disable/enable each widget
        self.radio_casa_image.setEnabled(enabled)
        self.radio_fits_file.setEnabled(enabled)
        self.file_path_edit.setEnabled(enabled)
        self.browse_button.setEnabled(enabled)

        # Also set the parent group
        self.external_group.setEnabled(enabled)

        # print(f"  radio_casa_image.isEnabled() = {self.radio_casa_image.isEnabled()}")
        # print(f"  radio_fits_file.isEnabled() = {self.radio_fits_file.isEnabled()}")

    def _update_browse_icon(self):
        """Update browse button icon based on current palette (light/dark mode)."""
        # Check if we're in light or dark mode by examining window color
        palette = self.palette()
        window_color = palette.color(palette.Window)
        # If window color is light (high luminance), use dark icon
        luminance = (
            0.299 * window_color.red()
            + 0.587 * window_color.green()
            + 0.114 * window_color.blue()
        )
        if luminance > 128:
            # Light mode - use dark icon
            if hasattr(self, "browse_icon_dark"):
                self.browse_button.setIcon(self.browse_icon_dark)
        else:
            # Dark mode - use light icon
            if hasattr(self, "browse_icon_light"):
                self.browse_button.setIcon(self.browse_icon_light)

    def on_level_type_changed(self, level_type):
        """Update default levels when level type changes."""
        # Define default levels for each type
        defaults = {
            "fraction": {
                "pos": [0.1, 0.3, 0.5, 0.7, 0.9],
                "neg": [0.1, 0.3, 0.5, 0.7, 0.9],
            },
            "sigma": {
                "pos": [3, 6, 9, 12, 15, 20, 25, 30],
                "neg": [3, 6, 9, 12, 15, 20, 25, 30],
            },
            "absolute": {
                "pos": [50, 100, 500, 1000, 5000, 10000],
                "neg": [50, 100, 500, 1000, 5000, 10000],
            },
        }

        if level_type in defaults:
            pos_levels = defaults[level_type]["pos"]
            neg_levels = defaults[level_type]["neg"]
            self.pos_levels_edit.setText(", ".join(str(l) for l in pos_levels))
            self.neg_levels_edit.setText(", ".join(str(l) for l in neg_levels))

    def _apply_level_preset(self, preset_name):
        """Apply a predefined level preset based on current level type."""
        level_type = self.level_type_combo.currentText()

        presets = {
            "fraction": {
                "5levels": ([0.1, 0.3, 0.5, 0.7, 0.9], [0.1, 0.3, 0.5, 0.7, 0.9]),
                "3levels": ([0.3, 0.6, 0.9], [0.3, 0.6, 0.9]),
                "dense": (
                    [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9],
                    [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9],
                ),
            },
            "sigma": {
                "5levels": ([3, 6, 10, 15, 20], [3, 6, 10, 15, 20]),
                "3levels": ([3, 10, 20], [3, 10, 20]),
                "dense": ([3, 5, 7, 10, 15, 20, 30, 50], [3, 5, 7, 10, 15, 20, 30, 50]),
            },
            "absolute": {
                "5levels": (
                    [100, 500, 1000, 5000, 10000],
                    [100, 500, 1000, 5000, 10000],
                ),
                "3levels": ([100, 1000, 10000], [100, 1000, 10000]),
                "dense": (
                    [50, 100, 200, 500, 1000, 2000, 5000, 10000],
                    [50, 100, 200, 500, 1000, 2000, 5000, 10000],
                ),
            },
        }

        if level_type in presets and preset_name in presets[level_type]:
            pos, neg = presets[level_type][preset_name]
            self.pos_levels_edit.setText(", ".join(str(l) for l in pos))
            self.neg_levels_edit.setText(", ".join(str(l) for l in neg))

    def _reset_to_defaults(self):
        """Reset all dialog settings to default values."""
        # Source
        self.same_image_radio.setChecked(True)
        self.file_path_edit.clear()
        self.radio_casa_image.setChecked(True)

        # Stokes
        self.stokes_combo.setCurrentText("I")

        # Levels
        self.level_type_combo.setCurrentText("fraction")
        self.pos_levels_edit.setText("0.1, 0.3, 0.5, 0.7, 0.9")
        self.neg_levels_edit.setText("0.1, 0.3, 0.5, 0.7, 0.9")

        # Appearance
        self.pos_color_combo.setCurrentText("white")
        self.neg_color_combo.setCurrentText("white")
        self.pos_linewidth_spin.setValue(1.0)
        self.neg_linewidth_spin.setValue(1.0)
        self.pos_linestyle_combo.setCurrentIndex(0)  # Solid
        self.neg_linestyle_combo.setCurrentIndex(1)  # Dashed
        if hasattr(self, "show_labels_checkbox"):
            self.show_labels_checkbox.setChecked(False)
        self.show_full_extent_checkbox.setChecked(True)
        if hasattr(self, "downsample_checkbox"):
            self.downsample_checkbox.setChecked(True)

        # RMS
        self.use_default_rms_box.setChecked(True)

    def update_placeholder_text(self):

        if self.radio_casa_image.isChecked():
            self.file_path_edit.setPlaceholderText("Select CASA image directory...")
        else:
            self.file_path_edit.setPlaceholderText("Select FITS file...")

    def browse_file(self):
        # Check if remote mode is active
        main_window = None
        parent = self.parent()
        if parent:
            # parent could be ImageViewer, we need MainWindow
            if hasattr(parent, "window"):
                main_window = parent.window()
            elif hasattr(parent, "remote_connection"):
                main_window = parent

        is_remote = (
            main_window is not None
            and hasattr(main_window, "remote_connection")
            and main_window.remote_connection is not None
            and main_window.remote_connection.is_connected()
        )

        if is_remote:
            # Use remote file browser
            from ..remote.remote_file_browser import RemoteFileBrowser
            from ..remote.file_cache import RemoteFileCache

            casa_mode = self.radio_casa_image.isChecked()
            cache = getattr(main_window, "remote_cache", None) or RemoteFileCache()

            browser = RemoteFileBrowser(
                main_window.remote_connection,
                cache=cache,
                parent=self,
                casa_mode=casa_mode,
            )

            def on_file_selected(path):
                self.file_path_edit.setText(path)
                self._update_stokes_combo_for_external(path)

            browser.fileSelected.connect(on_file_selected)
            browser.exec_()
        else:
            # Use local file dialog
            if self.radio_casa_image.isChecked():
                # Select CASA image directory
                directory = QFileDialog.getExistingDirectory(
                    self, "Select a CASA Image Directory"
                )
                if directory:
                    self.file_path_edit.setText(directory)
                    # Update stokes combo based on external image
                    self._update_stokes_combo_for_external(directory)
            else:
                # Select FITS file
                file_path, _ = QFileDialog.getOpenFileName(
                    self, "Select a FITS file", "", "FITS files (*.fits);;All files (*)"
                )
                if file_path:
                    self.file_path_edit.setText(file_path)
                    # Update stokes combo based on external image
                    self._update_stokes_combo_for_external(file_path)

    def _update_stokes_combo_state(self, available_stokes):
        """
        Update the Stokes combo box to enable/disable items based on available Stokes.

        Args:
            available_stokes: List of available base Stokes, e.g., ["I"] or ["I", "Q", "U", "V"]
        """
        from PyQt5.QtGui import QBrush, QColor

        # Get theme-aware colors for disabled state
        try:
            from ..ui.styles import theme_manager

            palette = theme_manager.palette
            is_dark = theme_manager.is_dark
            disabled_color = QColor(palette.get("disabled", "#cccccc"))
            enabled_color = QColor(
                palette.get("text", "#ffffff" if is_dark else "#000000")
            )
        except ImportError:
            disabled_color = QColor("#cccccc")
            enabled_color = QColor("#000000")

        # Derived parameters and their requirements
        requires_q = {"Q", "Q/I", "L", "Lfrac", "PANG"}
        requires_u = {"U", "U/I", "V/I", "L", "Lfrac", "PANG"}
        requires_v = {"V", "V/I"}

        has_q = "Q" in available_stokes
        has_u = "U" in available_stokes
        has_v = "V" in available_stokes

        # Iterate through combo items and enable/disable based on requirements
        model = self.stokes_combo.model()
        for i in range(self.stokes_combo.count()):
            item_text = self.stokes_combo.itemText(i)

            enabled = True
            if item_text in requires_q and not has_q:
                enabled = False
            if item_text in requires_u and not has_u:
                enabled = False
            if item_text in requires_v and not has_v:
                enabled = False

            item = model.item(i)
            if item:
                if enabled:
                    item.setFlags(item.flags() | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    item.setData(QBrush(enabled_color), Qt.ForegroundRole)
                else:
                    item.setFlags(
                        item.flags() & ~Qt.ItemIsEnabled & ~Qt.ItemIsSelectable
                    )
                    item.setData(QBrush(disabled_color), Qt.ForegroundRole)

    def _update_stokes_combo_for_external(self, imagepath):
        """Update stokes combo based on an external image file."""
        if not imagepath or not os.path.exists(imagepath):
            return
        try:
            from ..processing.stokes import get_available_stokes

            available_stokes = get_available_stokes(imagepath)
            self._update_stokes_combo_state(available_stokes)
        except Exception as e:
            print(f"[WARNING] Could not detect Stokes from {imagepath}: {e}")

    def _update_stokes_for_current_source(self):
        """Update stokes combo based on current source selection."""
        if self.same_image_radio.isChecked():
            # Use parent viewer's image
            parent = self.parent()
            if parent and hasattr(parent, "imagename") and parent.imagename:
                try:
                    from ..processing.stokes import get_available_stokes

                    available_stokes = get_available_stokes(parent.imagename)
                    self._update_stokes_combo_state(available_stokes)
                except Exception as e:
                    print(f"[WARNING] Could not detect Stokes: {e}")
        else:
            # Use external image path
            external_path = self.file_path_edit.text()
            if external_path:
                self._update_stokes_combo_for_external(external_path)

    def update_dimensions_label(self, w, h):
        """Update the image dimensions label and settings."""
        try:
            if hasattr(self, "rms_dims_label") and self.rms_dims_label:
                dim_text = f"{w} x {h}" if w > 0 and h > 0 else "Unknown"
                self.rms_dims_label.setText(f"Contour Dimensions: {dim_text}")
        except RuntimeError:
            # Handle case where QLabel has been deleted
            pass
            
        # Also update internal settings to reflect current state
        try:
            self.settings["dim_w"] = w
            self.settings["dim_h"] = h
        except (RuntimeError, AttributeError):
            pass

    def _update_threshold_visibility(self):
        """Show/hide threshold controls based on selected Stokes parameter."""
        # Derived Stokes parameters that use thresholding
        derived_stokes = {"Q/I", "U/I", "V/I", "Lfrac", "Vfrac", "PANG"}
        current_stokes = self.stokes_combo.currentText()

        # Enable threshold controls only for derived parameters
        is_derived = current_stokes in derived_stokes
        self.threshold_label.setEnabled(is_derived)
        self.threshold_spin.setEnabled(is_derived)

    def get_settings(self):
        settings = {}
        settings["source"] = (
            "external" if self.external_image_radio.isChecked() else "same"
        )
        settings["external_image"] = self.file_path_edit.text()
        settings["stokes"] = self.stokes_combo.currentText()
        settings["threshold"] = self.threshold_spin.value()
        settings["level_type"] = self.level_type_combo.currentText()
        try:
            pos_levels_text = self.pos_levels_edit.text()
            settings["pos_levels"] = [
                float(level.strip())
                for level in pos_levels_text.split(",")
                if level.strip()
            ]
        except ValueError:
            settings["pos_levels"] = [0.1, 0.3, 0.5, 0.7, 0.9]
        try:
            neg_levels_text = self.neg_levels_edit.text()
            settings["neg_levels"] = [
                float(level.strip())
                for level in neg_levels_text.split(",")
                if level.strip()
            ]
        except ValueError:
            settings["neg_levels"] = [0.1, 0.3, 0.5, 0.7, 0.9]
        settings["levels"] = settings["pos_levels"]
        settings["use_default_rms_region"] = self.use_default_rms_box.isChecked()
        settings["rms_box"] = (
            self.rms_xmin.value(),
            self.rms_xmax.value(),
            self.rms_ymin.value(),
            self.rms_ymax.value(),
        )
        settings["color"] = (
            self.pos_color_combo.currentText()
        )  # For backward compatibility
        settings["linewidth"] = (
            self.pos_linewidth_spin.value()
        )  # For backward compatibility
        settings["pos_color"] = self.pos_color_combo.currentText()
        settings["neg_color"] = self.neg_color_combo.currentText()
        settings["pos_linewidth"] = self.pos_linewidth_spin.value()
        settings["neg_linewidth"] = self.neg_linewidth_spin.value()

        # Convert linestyle display names back to matplotlib format
        linestyle_values = ["-", "--", "-.", ":"]
        pos_idx = self.pos_linestyle_combo.currentIndex()
        neg_idx = self.neg_linestyle_combo.currentIndex()
        settings["pos_linestyle"] = (
            linestyle_values[pos_idx] if 0 <= pos_idx < len(linestyle_values) else "-"
        )
        settings["neg_linestyle"] = (
            linestyle_values[neg_idx] if 0 <= neg_idx < len(linestyle_values) else "--"
        )
        settings["linestyle"] = settings["pos_linestyle"]

        settings["show_labels"] = (
            self.show_labels_checkbox.isChecked()
            if hasattr(self, "show_labels_checkbox")
            else False
        )
        settings["show_full_extent"] = self.show_full_extent_checkbox.isChecked()
        settings["downsample"] = (
            self.downsample_checkbox.isChecked()
            if hasattr(self, "downsample_checkbox")
            else True
        )
        if "contour_data" in self.settings:
            settings["contour_data"] = self.settings["contour_data"]
        else:
            settings["contour_data"] = None
        return settings



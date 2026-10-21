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
    QListWidget,
    QListWidgetItem,
    QAbstractItemView,
)
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import Qt
import os

from ..core.contours import (
    CONTOUR_COLOR_NAMES,
    CONTOUR_LAYER_COLORS,
    MAX_CONTOUR_LAYERS,
    contour_layer_name,
    contour_layer_settings,
    default_contour_options,
    new_contour_layer,
)
from ..ui.icons import get_resource_path
from ..ui.styles import set_hand_cursor

# Offered after the default palette; not all of these suit colour-blind viewers
EXTRA_COLORS = [
    ("Red", "red"),
    ("Green", "green"),
    ("Pure blue", "blue"),
    ("Cyan", "cyan"),
    ("Magenta", "magenta"),
    ("Lime", "lime"),
]

LEGEND_POSITIONS = [
    ("Upper right", "upper right"),
    ("Upper left", "upper left"),
    ("Lower right", "lower right"),
    ("Lower left", "lower left"),
    ("Best", "best"),
]


class ContourSettingsDialog(QDialog):
    """Dialog for configuring contour layers (up to MAX_CONTOUR_LAYERS).

    The list on the left holds the layers; the tabs edit the selected one.
    """

    def __init__(self, parent=None, layers=None, options=None):
        super().__init__(parent)
        self.setWindowTitle("Contour Settings")
        self.layers = [contour_layer_settings(layer) for layer in (layers or [])]
        if not self.layers:
            self.layers = [contour_layer_settings(new_contour_layer())]
        self.options = dict(default_contour_options(), **(options or {}))
        self._current_row = 0
        self._loading_layer = False
        # Widgets are built from the first layer's settings
        self.settings = self.layers[0]

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
            QListWidget {{
                background-color: {base_color};
                color: {text_color};
                border: 1px solid {border_color};
                border-radius: 6px;
                padding: 4px;
            }}
            QListWidget::item {{
                padding: 6px 4px;
                border-radius: 4px;
            }}
            QListWidget::item:selected {{
                background-color: {highlight_color};
                color: #ffffff;
            }}
        """
        )

        # Store theme colors for use in browse button
        self._hover_bg = button_hover
        self._pressed_bg = button_pressed

        self.setup_ui()
        set_hand_cursor(self)

    def setup_ui(self):
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

        # Layer label (legend text)
        label_row = QHBoxLayout()
        label_row.setSpacing(20)
        layer_label_label = QLabel("Label:")
        layer_label_label.setStyleSheet("font-weight: 600;")
        label_row.addWidget(layer_label_label)
        self.layer_label_edit = QLineEdit(self.settings.get("label", ""))
        self.layer_label_edit.setPlaceholderText(
            contour_layer_name(dict(self.settings, label=""))
        )
        self.layer_label_edit.setToolTip(
            "Name of this layer in the legend.\n"
            "Leave empty to name it after its source and Stokes."
        )
        label_row.addWidget(self.layer_label_edit, 1)
        source_layout.addLayout(label_row)

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

        # Helper to create color combo: the default palette, then the extras
        def create_color_combo(default_color):
            combo = QComboBox()
            for color in CONTOUR_LAYER_COLORS:
                self._add_color_item(combo, CONTOUR_COLOR_NAMES[color], color)
            combo.insertSeparator(combo.count())
            for name, color in EXTRA_COLORS:
                self._add_color_item(combo, name, color)
            self._set_color(combo, default_color)
            combo.setMinimumWidth(140)
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

        body_row = QHBoxLayout()
        body_row.setSpacing(12)
        body_row.addWidget(self._build_layer_panel(text_secondary))
        body_row.addWidget(self.tab_widget, 1)
        main_layout.addLayout(body_row)

        # ========== OPTIONS SHARED BY ALL LAYERS ==========
        legend_row = QHBoxLayout()
        legend_row.setSpacing(12)

        self.show_legend_checkbox = QCheckBox("Show legend")
        self.show_legend_checkbox.setChecked(self.options.get("show_legend", True))
        self.show_legend_checkbox.setToolTip("Show a legend naming each contour layer")
        legend_row.addWidget(self.show_legend_checkbox)

        self.legend_loc_combo = QComboBox()
        for text, value in LEGEND_POSITIONS:
            self.legend_loc_combo.addItem(text, value)
        index = self.legend_loc_combo.findData(self.options.get("legend_loc", "upper right"))
        self.legend_loc_combo.setCurrentIndex(max(index, 0))
        self.legend_loc_combo.setToolTip("Where the legend is placed on the image")
        legend_row.addWidget(self.legend_loc_combo)

        self.legend_fontsize_label = QLabel("Font size:")
        legend_row.addWidget(self.legend_fontsize_label)
        self.legend_fontsize_spin = QSpinBox()
        self.legend_fontsize_spin.setRange(5, 24)
        self.legend_fontsize_spin.setValue(int(self.options.get("legend_fontsize", 8)))
        self.legend_fontsize_spin.setToolTip("Size of the legend text, in points")
        legend_row.addWidget(self.legend_fontsize_spin)

        self.legend_negative_checkbox = QCheckBox("Include negative contours")
        self.legend_negative_checkbox.setChecked(
            self.options.get("legend_show_negative", False)
        )
        self.legend_negative_checkbox.setToolTip(
            "Also list each layer's negative (dashed) contours in the legend"
        )
        legend_row.addWidget(self.legend_negative_checkbox)
        legend_row.addStretch()

        legend_controls = (
            self.legend_loc_combo,
            self.legend_fontsize_label,
            self.legend_fontsize_spin,
            self.legend_negative_checkbox,
        )

        def enable_legend_controls(enabled):
            for widget in legend_controls:
                widget.setEnabled(enabled)

        self.show_legend_checkbox.toggled.connect(enable_legend_controls)
        enable_legend_controls(self.show_legend_checkbox.isChecked())
        main_layout.addLayout(legend_row)

        shared_row = QHBoxLayout()
        shared_row.setSpacing(20)

        self.show_full_extent_checkbox = QCheckBox("Full extent")
        self.show_full_extent_checkbox.setChecked(
            self.options.get("show_full_extent", True)
        )
        self.show_full_extent_checkbox.setToolTip(
            "Show contours beyond 1.5x image boundaries"
        )
        shared_row.addWidget(self.show_full_extent_checkbox)

        self.downsample_checkbox = QCheckBox("Downsample")
        self.downsample_checkbox.setChecked(self.options.get("downsample", True))
        self.downsample_checkbox.setToolTip(
            "Trace contours on at most 2048x2048 pixels of large contour images (faster)"
        )
        shared_row.addWidget(self.downsample_checkbox)

        shared_row.addStretch()
        main_layout.addLayout(shared_row)

        # ========== BUTTON BOX ==========
        button_row = QHBoxLayout()
        button_row.setSpacing(12)

        reset_btn = QPushButton("Reset Layer")
        reset_btn.setToolTip("Reset the selected layer's settings to the defaults")
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
        """Reset the selected layer's settings to default values."""
        color = CONTOUR_LAYER_COLORS[self._current_row % len(CONTOUR_LAYER_COLORS)]
        self.layer_label_edit.clear()

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
        self._set_color(self.pos_color_combo, color)
        self._set_color(self.neg_color_combo, color)
        self.pos_linewidth_spin.setValue(1.0)
        self.neg_linewidth_spin.setValue(1.0)
        self.pos_linestyle_combo.setCurrentIndex(0)  # Solid
        self.neg_linestyle_combo.setCurrentIndex(1)  # Dashed
        if hasattr(self, "show_labels_checkbox"):
            self.show_labels_checkbox.setChecked(False)

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
        """Update the image dimensions label."""
        try:
            if hasattr(self, "rms_dims_label") and self.rms_dims_label:
                dim_text = f"{w} x {h}" if w > 0 and h > 0 else "Unknown"
                self.rms_dims_label.setText(f"Contour Dimensions: {dim_text}")
        except RuntimeError:
            # Handle case where QLabel has been deleted
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

    # ---------- Layers ----------

    def _build_layer_panel(self, text_secondary):
        """The layer list with Add / Duplicate / Remove."""
        panel = QWidget()
        panel.setFixedWidth(280)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        header = QLabel("Layers")
        header.setStyleSheet("font-weight: 600; font-size: 11pt;")
        layout.addWidget(header)

        self.layer_list = QListWidget()
        self.layer_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.layer_list.setToolTip(
            "Contour layers drawn on the image.\n"
            "Tick to show, untick to hide; select one to edit it."
        )
        layout.addWidget(self.layer_list, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.add_layer_btn = QPushButton("+ Add")
        self.add_layer_btn.setToolTip("Add a contour layer")
        self.add_layer_btn.clicked.connect(self._add_layer)
        buttons.addWidget(self.add_layer_btn)
        self.duplicate_layer_btn = QPushButton("Duplicate")
        self.duplicate_layer_btn.setToolTip(
            "Copy the selected layer, e.g. to show another Stokes of the same file"
        )
        self.duplicate_layer_btn.clicked.connect(self._duplicate_layer)
        buttons.addWidget(self.duplicate_layer_btn)
        self.remove_layer_btn = QPushButton("Remove")
        self.remove_layer_btn.clicked.connect(self._remove_layer)
        buttons.addWidget(self.remove_layer_btn)
        layout.addLayout(buttons)

        self.layer_count_label = QLabel()
        self.layer_count_label.setStyleSheet(f"color: {text_secondary}; font-size: 9pt;")
        layout.addWidget(self.layer_count_label)

        for layer in self.layers:
            self.layer_list.addItem(self._new_layer_item(layer))
        self.layer_list.setCurrentRow(0)
        self.layer_list.currentRowChanged.connect(self._on_layer_selected)
        self.layer_list.itemChanged.connect(self._on_layer_item_changed)
        self._update_layer_buttons()

        # Keep the list entry's name and colour in step with the edits
        self.layer_label_edit.textChanged.connect(self._on_layer_edited)
        self.stokes_combo.currentTextChanged.connect(self._on_layer_edited)
        self.file_path_edit.textChanged.connect(self._on_layer_edited)
        self.same_image_radio.toggled.connect(self._on_layer_edited)
        self.pos_color_combo.currentIndexChanged.connect(self._on_pos_color_changed)
        return panel

    def _new_layer_item(self, layer):
        item = QListWidgetItem()
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        self._show_layer_on_item(item, layer)
        return item

    def _show_layer_on_item(self, item, layer):
        from PyQt5.QtGui import QColor, QPainter, QPixmap

        self.layer_list.blockSignals(True)
        item.setText(contour_layer_name(layer))
        # Colour swatch with an outline, so white and black show on any theme
        pixmap = QPixmap(14, 14)
        pixmap.fill(QColor(layer.get("pos_color", "white")))
        painter = QPainter(pixmap)
        painter.setPen(QColor(128, 128, 128))
        painter.drawRect(0, 0, 13, 13)
        painter.end()
        item.setIcon(QIcon(pixmap))
        item.setCheckState(Qt.Checked if layer.get("enabled", True) else Qt.Unchecked)
        self.layer_list.blockSignals(False)

    def _update_layer_buttons(self):
        count = len(self.layers)
        full = count >= MAX_CONTOUR_LAYERS
        self.add_layer_btn.setEnabled(not full)
        self.duplicate_layer_btn.setEnabled(not full)
        self.remove_layer_btn.setEnabled(count > 1)
        self.layer_count_label.setText(
            f"{count} of {MAX_CONTOUR_LAYERS} layers"
            + (" (maximum reached)" if full else "")
        )

    def _unused_color(self):
        used = {layer.get(key) for layer in self.layers for key in ("pos_color", "neg_color")}
        for color in CONTOUR_LAYER_COLORS:
            if color not in used:
                return color
        return CONTOUR_LAYER_COLORS[len(self.layers) % len(CONTOUR_LAYER_COLORS)]

    def _append_layer(self, layer):
        self._store_current_layer()
        self.layers.append(layer)
        self.layer_list.addItem(self._new_layer_item(layer))
        self.layer_list.setCurrentRow(len(self.layers) - 1)
        self._update_layer_buttons()

    def _add_layer(self):
        if len(self.layers) >= MAX_CONTOUR_LAYERS:
            return
        color = self._unused_color()
        layer = contour_layer_settings(
            new_contour_layer(pos_color=color, neg_color=color)
        )
        self._append_layer(layer)
        self.tab_widget.setCurrentIndex(0)

    def _duplicate_layer(self):
        if len(self.layers) >= MAX_CONTOUR_LAYERS:
            return
        self._store_current_layer()
        color = self._unused_color()
        layer = dict(self.layers[self._current_row])
        layer.update(
            id=new_contour_layer()["id"], label="", pos_color=color, neg_color=color
        )
        self._append_layer(layer)
        # Most often the copy is for another Stokes of the same source
        self.tab_widget.setCurrentIndex(0)
        self.stokes_combo.setFocus()

    def _remove_layer(self):
        if len(self.layers) <= 1:
            return
        row = self._current_row
        del self.layers[row]
        self._loading_layer = True  # the removed layer must not be stored back
        self.layer_list.takeItem(row)
        self._loading_layer = False
        self._current_row = min(row, len(self.layers) - 1)
        self.layer_list.setCurrentRow(self._current_row)
        self._load_layer_into_widgets(self.layers[self._current_row])
        self._update_layer_buttons()

    def _on_layer_selected(self, row):
        if self._loading_layer or row < 0 or row >= len(self.layers):
            return
        self._store_current_layer()
        self._current_row = row
        self._load_layer_into_widgets(self.layers[row])

    def _on_layer_item_changed(self, item):
        row = self.layer_list.row(item)
        if 0 <= row < len(self.layers):
            self.layers[row]["enabled"] = item.checkState() == Qt.Checked

    def _on_pos_color_changed(self, *_):
        """Negative contours keep matching the positive ones unless they were
        given a colour of their own."""
        if not self._loading_layer and 0 <= self._current_row < len(self.layers):
            layer = self.layers[self._current_row]
            if self._color(self.neg_color_combo) == layer.get("pos_color"):
                self._set_color(self.neg_color_combo, self._color(self.pos_color_combo))
        self._on_layer_edited()

    @staticmethod
    def _add_color_item(combo, name, color):
        from PyQt5.QtGui import QColor, QPainter, QPixmap

        pixmap = QPixmap(16, 16)
        pixmap.fill(QColor(color))
        painter = QPainter(pixmap)
        painter.setPen(QColor(128, 128, 128))
        painter.drawRect(0, 0, 15, 15)
        painter.end()
        combo.addItem(QIcon(pixmap), name, color)

    def _set_color(self, combo, color):
        """Select ``color`` (a Matplotlib colour) in a colour combo, adding it
        if it isn't offered (e.g. a colour from an older version)."""
        index = combo.findData(color)
        if index < 0:
            self._add_color_item(combo, str(color), color)
            index = combo.count() - 1
        combo.setCurrentIndex(index)

    @staticmethod
    def _color(combo):
        return combo.currentData() or combo.currentText()

    def _on_layer_edited(self, *_):
        if self._loading_layer:
            return
        self._store_current_layer()

    def _store_current_layer(self):
        """Save the widgets into the selected layer and refresh its list entry."""
        if self._loading_layer or not (0 <= self._current_row < len(self.layers)):
            return
        layer = self._layer_from_widgets(self.layers[self._current_row])
        self.layers[self._current_row] = layer
        item = self.layer_list.item(self._current_row)
        if item is not None:
            self._show_layer_on_item(item, layer)

    def _load_layer_into_widgets(self, layer):
        """Show ``layer``'s settings in the tabs."""
        self._loading_layer = True
        try:
            self.layer_label_edit.setText(layer.get("label", ""))
            self.layer_label_edit.setPlaceholderText(
                contour_layer_name(dict(layer, label=""))
            )

            external = layer.get("source") == "external"
            self.external_image_radio.setChecked(external)
            self.same_image_radio.setChecked(not external)
            path = layer.get("external_image", "")
            if path:
                if os.path.isdir(path):
                    self.radio_casa_image.setChecked(True)
                else:
                    self.radio_fits_file.setChecked(True)
            self.file_path_edit.setText(path)
            self._update_stokes_for_current_source()
            self.stokes_combo.setCurrentText(layer.get("stokes", "I"))
            self.threshold_spin.setValue(layer.get("threshold", 5.0))

            # Without its signal: changing the type resets the levels
            self.level_type_combo.blockSignals(True)
            self.level_type_combo.setCurrentText(layer.get("level_type", "fraction"))
            self.level_type_combo.blockSignals(False)
            self.pos_levels_edit.setText(
                ", ".join(str(l) for l in layer.get("pos_levels", []))
            )
            self.neg_levels_edit.setText(
                ", ".join(str(l) for l in layer.get("neg_levels", []))
            )

            linestyle_map = {"-": 0, "--": 1, "-.": 2, ":": 3}
            self._set_color(self.pos_color_combo, layer.get("pos_color", "white"))
            self._set_color(self.neg_color_combo, layer.get("neg_color", "white"))
            self.pos_linewidth_spin.setValue(layer.get("pos_linewidth", 1.0))
            self.neg_linewidth_spin.setValue(layer.get("neg_linewidth", 1.0))
            self.pos_linestyle_combo.setCurrentIndex(
                linestyle_map.get(layer.get("pos_linestyle", "-"), 0)
            )
            self.neg_linestyle_combo.setCurrentIndex(
                linestyle_map.get(layer.get("neg_linestyle", "--"), 1)
            )
            self.show_labels_checkbox.setChecked(layer.get("show_labels", False))

            self.use_default_rms_box.setChecked(
                layer.get("use_default_rms_region", True)
            )
            rms_box = layer.get("rms_box", (0, 200, 0, 130))
            for spin, value in zip(
                (self.rms_xmin, self.rms_xmax, self.rms_ymin, self.rms_ymax), rms_box
            ):
                spin.setValue(value)
            self.toggle_rms_inputs()
            self.update_dimensions_label(layer.get("dim_w", 0), layer.get("dim_h", 0))
        finally:
            self._loading_layer = False

    def _layer_from_widgets(self, layer):
        """``layer`` updated with the settings shown in the tabs."""
        settings = dict(layer)
        settings["label"] = self.layer_label_edit.text().strip()
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
        settings["use_default_rms_region"] = self.use_default_rms_box.isChecked()
        settings["rms_box"] = (
            self.rms_xmin.value(),
            self.rms_xmax.value(),
            self.rms_ymin.value(),
            self.rms_ymax.value(),
        )
        settings["pos_color"] = self._color(self.pos_color_combo)
        settings["neg_color"] = self._color(self.neg_color_combo)
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
        settings["show_labels"] = self.show_labels_checkbox.isChecked()
        return settings

    def get_layers(self):
        """The layers' settings, the selected one as currently edited."""
        self._store_current_layer()
        return [dict(layer) for layer in self.layers]

    def get_options(self):
        """Settings shared by all layers."""
        return {
            "show_legend": self.show_legend_checkbox.isChecked(),
            "legend_loc": self.legend_loc_combo.currentData(),
            "legend_fontsize": self.legend_fontsize_spin.value(),
            "legend_show_negative": self.legend_negative_checkbox.isChecked(),
            "show_full_extent": self.show_full_extent_checkbox.isChecked(),
            "downsample": self.downsample_checkbox.isChecked(),
        }

    def update_layer_dimensions(self, layers):
        """Record the image dimensions found when the layers were loaded."""
        dims = {layer.get("id"): layer for layer in layers}
        for layer in self.layers:
            loaded = dims.get(layer.get("id"))
            if loaded is not None:
                layer["dim_w"] = loaded.get("dim_w", 0)
                layer["dim_h"] = loaded.get("dim_h", 0)
        if 0 <= self._current_row < len(self.layers):
            layer = self.layers[self._current_row]
            self.update_dimensions_label(layer.get("dim_w", 0), layer.get("dim_h", 0))

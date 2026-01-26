from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QRadioButton,
    QLineEdit,
    QLabel,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QGridLayout,
    QFormLayout,
    QDialogButtonBox,
    QComboBox,
    QCheckBox,
    QFileDialog,
    QMessageBox,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QButtonGroup,
    QWidget,
    QProgressDialog,
    QFrame,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QProgressBar,
)
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import Qt, QSize
import sys
import numpy as np
import os
import multiprocessing
import glob
from PyQt5.QtWidgets import QApplication, QMessageBox
from .utils.update_checker import check_for_updates
from .version import __version__
from .styles import theme_manager, set_hand_cursor
import subprocess


def _get_resource_path(relative_path):
    """Get absolute path to resource, working for dev and PyInstaller."""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


import uuid
import traceback
import time


# Standalone function for multiprocessing
def process_single_file_hpc(args):
    """Process a single file for HPC conversion - standalone function for multiprocessing

    Parameters:
    -----------
    args : tuple
        Tuple containing (input_file, output_path, stokes, process_id)

    Returns:
    --------
    dict
        Result dictionary with processing outcome
    """
    input_file, output_path, stokes, process_id = args

    try:
        result = {
            "input_file": input_file,
            "output_path": output_path,
            "stokes": stokes,
            "success": False,
            "error": None,
            "skipped": False,  # True if already HPC and just copied
        }

        # Check if file is already in HPC coordinates
        if is_already_hpc(input_file):
            # Just copy the file instead of converting
            import shutil

            try:
                if os.path.isdir(input_file):
                    # CASA image - copy directory
                    if os.path.exists(output_path):
                        shutil.rmtree(output_path)
                    shutil.copytree(input_file, output_path)
                else:
                    # FITS file - copy file
                    shutil.copy2(input_file, output_path)
                result["success"] = True
                result["skipped"] = True
                return result
            except Exception as e:
                result["error"] = f"Copy failed: {str(e)}"
                return result

        # Import the function here to ensure we have it in the subprocess
        from .helioprojective import convert_and_save_hpc

        # Generate a unique file suffix for this process to avoid conflicts
        temp_suffix = f"_proc_{process_id}_{uuid.uuid4().hex[:8]}"

        # Convert file with unique temp file handling
        success = convert_and_save_hpc(
            input_file,
            output_path,
            Stokes=stokes,
            overwrite=True,
            temp_suffix=temp_suffix,
        )

        result["success"] = success
        return result
    except Exception as e:
        result["error"] = str(e)
        result["traceback"] = traceback.format_exc()
        return result


def is_already_hpc(imagepath):
    """Check if image is already in helioprojective coordinates (Solar-X/Y or HPLN/HPLT).

    This is a standalone function for use in multiprocessing.

    Parameters:
    -----------
    imagepath : str
        Path to the image file (FITS) or directory (CASA image)

    Returns:
    --------
    bool
        True if already in HPC coordinates, False otherwise
    """
    if not imagepath or not os.path.exists(imagepath):
        return False

    try:
        # For FITS files, check the header
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
                return True

        # For CASA images, check coordinate system
        if os.path.isdir(imagepath):
            try:
                from casatools import image as IA

                ia_tool = IA()
                ia_tool.open(imagepath)
                csys = ia_tool.coordsys()
                dimension_names = [n.upper() for n in csys.names()]
                ia_tool.close()

                if "SOLAR-X" in dimension_names or "SOLAR-Y" in dimension_names:
                    return True
                if "HPLN-TAN" in dimension_names or "HPLT-TAN" in dimension_names:
                    return True
            except Exception:
                pass

        return False
    except Exception:
        return False


class ContourSettingsDialog(QDialog):
    """Dialog for configuring contour settings with a more compact layout."""

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self.setWindowTitle("Contour Settings")
        self.settings = settings.copy() if settings else {}

        # Import theme manager for theme-aware styling
        try:
            from .styles import theme_manager
        except ImportError:
            from styles import theme_manager

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
            from .styles import theme_manager
        except ImportError:
            from styles import theme_manager

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
        self.browse_icon_light = QIcon(_get_resource_path("assets/browse.png"))
        self.browse_icon_dark = QIcon(_get_resource_path("assets/browse_light.png"))

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
            self.settings.get("show_full_extent", False)
        )
        self.show_full_extent_checkbox.setToolTip(
            "Show contours beyond 1.5x image boundaries (uses more memory)"
        )
        options_row.addWidget(self.show_full_extent_checkbox)

        self.downsample_checkbox = QCheckBox("Downsample")
        self.downsample_checkbox.setChecked(self.settings.get("downsample", True))
        self.downsample_checkbox.setToolTip(
            "Automatically downsample large contour images to 2048x2048 for faster reprojection"
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
        self.show_full_extent_checkbox.setChecked(False)
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
            from .remote.remote_file_browser import RemoteFileBrowser
            from .remote.file_cache import RemoteFileCache

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
            from .styles import theme_manager

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
            from .utils import get_available_stokes

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
                    from .utils import get_available_stokes

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
        if hasattr(self, "rms_dims_label"):
            dim_text = f"{w} x {h}" if w > 0 and h > 0 else "Unknown"
            self.rms_dims_label.setText(f"Contour Dimensions: {dim_text}")
            
        # Also update internal settings to reflect current state
        self.settings["dim_w"] = w
        self.settings["dim_h"] = h

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


class BatchProcessDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Batch Processing")
        self.setMinimumWidth(500)
        self.setStyleSheet("background-color: #484848; color: #ffffff;")
        self.image_list = QListWidget()
        self.add_button = QPushButton("Add Image")
        self.remove_button = QPushButton("Remove Selected")
        self.threshold_spin = QSpinBox()
        self.threshold_spin.setRange(1, 9999)
        self.threshold_spin.setValue(10)
        lbl_thresh = QLabel("Threshold:")
        self.run_button = QPushButton("Run Process")
        button_box = QDialogButtonBox(QDialogButtonBox.Close)
        button_box.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.image_list)
        ctrl_layout = QHBoxLayout()
        ctrl_layout.addWidget(self.add_button)
        ctrl_layout.addWidget(self.remove_button)
        layout.addLayout(ctrl_layout)
        thr_layout = QHBoxLayout()
        thr_layout.addWidget(lbl_thresh)
        thr_layout.addWidget(self.threshold_spin)
        layout.addLayout(thr_layout)
        layout.addWidget(self.run_button)
        layout.addWidget(button_box)
        self.add_button.clicked.connect(self.add_image)
        self.remove_button.clicked.connect(self.remove_image)
        self.run_button.clicked.connect(self.run_process)
        set_hand_cursor(self)

    def add_image(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Select a CASA Image Directory"
        )
        if directory:
            self.image_list.addItem(directory)

    def remove_image(self):
        for item in self.image_list.selectedItems():
            self.image_list.takeItem(self.image_list.row(item))

    def run_process(self):
        threshold = self.threshold_spin.value()
        results = []
        for i in range(self.image_list.count()):
            imagename = self.image_list.item(i).text()
            try:
                from .utils import get_pixel_values_from_image

                pix, _, _ = get_pixel_values_from_image(imagename, "I", threshold)
                flux = float(np.sum(pix))
                results.append(f"{imagename}: threshold={threshold}, flux={flux:.2f}")
            except Exception as e:
                results.append(f"{imagename}: ERROR - {str(e)}")
        QMessageBox.information(self, "Batch Results", "\n".join(results))


class ImageInfoDialog(QDialog):
    """Professional metadata display dialog with organized sections."""

    def __init__(self, parent=None, info_text="", metadata=None, imagename=None):
        super().__init__(parent)
        self.setWindowTitle("Image Metadata")
        self.setMinimumSize(700, 600)
        self.metadata = metadata
        self.info_text = info_text
        self.imagename = imagename

        self.setup_ui()
        set_hand_cursor(self)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)

        # Source Info
        if hasattr(self, "imagename") and self.imagename:
            source_container = QWidget()
            source_layout = QHBoxLayout(source_container)
            source_layout.setContentsMargins(0, 5, 0, 10)
            
            from .styles import theme_manager
            palette = theme_manager.palette
            
            source_label = QLabel(f"📄 {os.path.basename(self.imagename)}")
            source_label.setStyleSheet(f"""
                font-size: 11pt; 
                font-weight: 600; 
                color: {palette.get('text_secondary', '#888888')};
                background: {palette.get('button', '#444444')};
                padding: 4px 12px;
                border-radius: 6px;
            """)
            source_layout.addWidget(source_label)
            source_layout.addStretch()
            layout.addWidget(source_container)

        # Check if we have structured metadata (dict) or plain text
        if isinstance(self.metadata, dict) and self.metadata:
            self._create_structured_view(layout)
        elif self.info_text:
            # Handle legacy plain text or formatted text
            if isinstance(self.info_text, dict):
                self.metadata = self.info_text
                self._create_structured_view(layout)
            else:
                self._create_text_view(layout, self.info_text)
        else:
            self._create_text_view(layout, "No metadata available")

        # Button row
        button_layout = QHBoxLayout()

        # Copy button
        copy_btn = QPushButton("📋 Copy to Clipboard")
        copy_btn.clicked.connect(self._copy_to_clipboard)
        button_layout.addWidget(copy_btn)

        button_layout.addStretch()

        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        close_btn.setDefault(True)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

    def _create_structured_view(self, parent_layout):
        """Create tabbed view for structured metadata."""
        tab_widget = QTabWidget()

        section_info = [
            ("observation", "📅 Observation", "Observation details"),
            ("spectral", "📡 Spectral", "Frequency and wavelength information"),
            ("beam", "🎯 Beam", "Synthesized beam properties"),
            ("image", "🖼️ Image", "Image dimensions and coordinates"),
            ("processing", "⚙️ Processing", "Data processing information"),
        ]

        for section_key, title, tooltip in section_info:
            if section_key in self.metadata and self.metadata[section_key]:
                tab = self._create_section_table(self.metadata[section_key])
                tab_widget.addTab(tab, title)
                tab_widget.setTabToolTip(tab_widget.count() - 1, tooltip)

        # Add raw header tab if available
        if "raw_header" in self.metadata and self.metadata["raw_header"]:
            raw_tab = self._create_raw_header_view(self.metadata["raw_header"])
            tab_widget.addTab(raw_tab, "📄 All Headers")
            tab_widget.setTabToolTip(
                tab_widget.count() - 1, "Complete FITS header information"
            )

        parent_layout.addWidget(tab_widget)

    def _create_section_table(self, section_data):
        """Create a styled table for a metadata section."""
        table = QTableWidget()
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["Property", "Value"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setAlternatingRowColors(True)

        # Populate table
        table.setRowCount(len(section_data))
        for row, (key, value) in enumerate(section_data.items()):
            key_item = QTableWidgetItem(str(key))
            value_item = QTableWidgetItem(str(value))

            # Make key bold
            font = key_item.font()
            font.setBold(True)
            key_item.setFont(font)

            table.setItem(row, 0, key_item)
            table.setItem(row, 1, value_item)

        # Adjust row heights
        table.resizeRowsToContents()

        return table

    def _create_raw_header_view(self, raw_header):
        """Create a searchable view for raw header data."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(5, 5, 5, 5)

        # Search box
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("🔍 Search:"))
        search_edit = QLineEdit()
        search_edit.setPlaceholderText("Filter headers...")
        search_layout.addWidget(search_edit)
        layout.addLayout(search_layout)

        # Table
        table = QTableWidget()
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["Keyword", "Value"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setAlternatingRowColors(True)

        # Store data for filtering
        self._raw_header_data = list(raw_header.items())
        self._raw_table = table

        # Populate table
        self._populate_raw_table("")

        # Connect search
        search_edit.textChanged.connect(self._filter_raw_header)

        layout.addWidget(table)
        return widget

    def _populate_raw_table(self, filter_text):
        """Populate raw header table with optional filtering."""
        filter_text = filter_text.lower()
        filtered_data = [
            (k, v)
            for k, v in self._raw_header_data
            if filter_text in k.lower() or filter_text in str(v).lower()
        ]

        self._raw_table.setRowCount(len(filtered_data))
        for row, (key, value) in enumerate(filtered_data):
            key_item = QTableWidgetItem(str(key))
            value_item = QTableWidgetItem(str(value))

            font = key_item.font()
            font.setFamily("monospace")
            key_item.setFont(font)
            value_item.setFont(font)

            self._raw_table.setItem(row, 0, key_item)
            self._raw_table.setItem(row, 1, value_item)

        self._raw_table.resizeRowsToContents()

    def _filter_raw_header(self, text):
        """Filter raw header table based on search text."""
        self._populate_raw_table(text)

    def _create_text_view(self, parent_layout, text):
        """Create simple text view for plain text metadata."""
        text_area = QPlainTextEdit()
        text_area.setReadOnly(True)
        text_area.setPlainText(text)

        # Use monospace font for better alignment
        font = text_area.font()
        font.setFamily("monospace")
        text_area.setFont(font)

        parent_layout.addWidget(text_area)

    def _copy_to_clipboard(self):
        """Copy metadata to clipboard as text."""
        from PyQt5.QtWidgets import QApplication

        clipboard = QApplication.clipboard()

        if self.metadata:
            # Format structured metadata as text
            from .utils import format_metadata_text

            text = format_metadata_text(self.metadata)
        else:
            text = self.info_text

        clipboard.setText(text)

        # Show confirmation (brief)
        if hasattr(self, "parentWidget") and self.parentWidget():
            pass  # Could show status message


class PhaseShiftDialog(QDialog):
    """Dialog for configuring and executing solar phase center shifting."""

    def __init__(self, parent=None, imagename=None):
        super().__init__(parent)
        self.setWindowTitle("Solar Phase Center Shift")
        self.setMinimumSize(550, 600)
        self.resize(650, 650)
        self.imagename = imagename

        # Set the dialog size to match the parent window if available
        """if parent and parent.size().isValid():
            self.resize(parent.size())
            # Center the dialog relative to the parent
            self.move(
                parent.frameGeometry().topLeft()
                + parent.rect().center()
                - self.rect().center()
            )"""

        self.setup_ui()
        set_hand_cursor(self)

    def setup_ui(self):
        from .move_phasecenter import SolarPhaseCenter

        # Theme setup
        try:
            from .styles import theme_manager
        except ImportError:
            from styles import theme_manager

        palette = theme_manager.palette
        text_secondary = palette.get("text_secondary", palette["disabled"])

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # Header description
        description = QLabel(
            "This tool relocates the quiet Sun disk to the actual solar center."
        )
        description.setWordWrap(True)
        description.setStyleSheet(
            f"font-style: italic; color: {text_secondary}; padding: 4px 0;"
        )
        main_layout.addWidget(description)

        # ========== TAB WIDGET ==========
        self.tab_widget = QTabWidget()
        from PyQt5.QtWidgets import QScrollArea

        # ========== TAB 1: FILES ==========
        files_tab = QWidget()
        files_layout = QVBoxLayout(files_tab)
        files_layout.setSpacing(16)
        files_layout.setContentsMargins(16, 16, 16, 16)

        # Mode Selection
        mode_group = QGroupBox("Processing Mode")
        mode_layout = QHBoxLayout(mode_group)
        mode_layout.setSpacing(20)

        self.single_mode_radio = QRadioButton("Single File")
        self.batch_mode_radio = QRadioButton("Batch Processing")
        self.single_mode_radio.setChecked(True)

        mode_layout.addWidget(self.single_mode_radio)
        mode_layout.addWidget(self.batch_mode_radio)
        mode_layout.addStretch()
        files_layout.addWidget(mode_group)

        # Connect mode radios
        self.single_mode_radio.toggled.connect(self.update_mode_ui)
        self.batch_mode_radio.toggled.connect(self.update_mode_ui)

        # Input Settings Group
        self.input_group = QGroupBox("Input Location")
        input_group_layout = QVBoxLayout(self.input_group)
        input_group_layout.setSpacing(12)

        # SINGLE MODE: Image Selection
        self.single_file_widget = QWidget()
        single_file_layout = QHBoxLayout(self.single_file_widget)
        single_file_layout.setContentsMargins(0, 0, 0, 0)

        img_label = QLabel("Image:")
        img_label.setMinimumWidth(70)
        img_label.setStyleSheet("font-weight: 600;")
        single_file_layout.addWidget(img_label)

        self.image_path_edit = QLineEdit(self.imagename or "")
        self.image_path_edit.setPlaceholderText("Select input image (FITS/CASA)...")
        single_file_layout.addWidget(self.image_path_edit, 1)

        self.browse_button = QPushButton("Browse...")
        self.browse_button.clicked.connect(self.browse_image)
        single_file_layout.addWidget(self.browse_button)

        input_group_layout.addWidget(self.single_file_widget)

        # BATCH MODE: Reference & Pattern
        self.batch_file_widget = QWidget()
        batch_file_layout = QVBoxLayout(self.batch_file_widget)
        batch_file_layout.setContentsMargins(0, 0, 0, 0)
        batch_file_layout.setSpacing(12)

        # Reference Image
        ref_row = QHBoxLayout()
        ref_label = QLabel("Ref Image:")
        ref_label.setMinimumWidth(70)
        ref_label.setStyleSheet("font-weight: 600;")
        ref_row.addWidget(ref_label)

        self.reference_image_edit = QLineEdit("")
        self.reference_image_edit.setPlaceholderText(
            "Reference image for phase center calculation"
        )
        ref_row.addWidget(self.reference_image_edit, 1)

        self.reference_browse_button = QPushButton("Browse...")
        self.reference_browse_button.clicked.connect(self.browse_reference_image)
        ref_row.addWidget(self.reference_browse_button)
        batch_file_layout.addLayout(ref_row)

        # Input Pattern
        pat_row = QHBoxLayout()
        pat_label = QLabel("Pattern:")
        pat_label.setMinimumWidth(70)
        pat_label.setStyleSheet("font-weight: 600;")
        pat_row.addWidget(pat_label)

        self.input_pattern_edit = QLineEdit("")
        self.input_pattern_edit.setPlaceholderText("e.g., /path/to/images/*.fits")
        pat_row.addWidget(self.input_pattern_edit, 1)

        # Scan button
        scan_btn = QPushButton("🔍")
        scan_btn.setFixedWidth(36)
        scan_btn.setToolTip("Scan for matching files")
        scan_btn.clicked.connect(self.scan_files)
        pat_row.addWidget(scan_btn)

        self.input_pattern_button = QPushButton("Browse...")
        self.input_pattern_button.clicked.connect(self.browse_input_pattern)
        pat_row.addWidget(self.input_pattern_button)
        batch_file_layout.addLayout(pat_row)

        # File count label
        self.files_count_label = QLabel("")
        self.files_count_label.setStyleSheet(
            f"color: {text_secondary}; font-style: italic; padding-left: 70px;"
        )
        batch_file_layout.addWidget(self.files_count_label)

        input_group_layout.addWidget(self.batch_file_widget)
        files_layout.addWidget(self.input_group)
        files_layout.addStretch()

        # Wrap Files Tab in Scroll Area
        files_scroll = QScrollArea()
        files_scroll.setWidgetResizable(True)
        files_scroll.setFrameShape(QFrame.NoFrame)
        files_scroll.setWidget(files_tab)
        self.tab_widget.addTab(files_scroll, "📁 Files")

        # ========== TAB 2: OPTIONS ==========
        options_tab = QWidget()
        options_layout = QVBoxLayout(options_tab)
        options_layout.setSpacing(16)
        options_layout.setContentsMargins(16, 16, 16, 16)

        # Stokes Settings
        stokes_group = QGroupBox("Stokes Parameters")
        stokes_layout = QVBoxLayout(stokes_group)
        stokes_layout.setSpacing(12)

        # Mode
        stokes_mode_row = QHBoxLayout()
        stokes_mode_row.setSpacing(20)
        st_mode_label = QLabel("Mode:")
        st_mode_label.setStyleSheet("font-weight: 600;")
        stokes_mode_row.addWidget(st_mode_label)

        self.single_stokes_radio = QRadioButton("Single Stokes")
        self.full_stokes_radio = QRadioButton("Full Stokes")
        self.single_stokes_radio.setChecked(True)
        stokes_mode_row.addWidget(self.single_stokes_radio)
        stokes_mode_row.addWidget(self.full_stokes_radio)
        stokes_mode_row.addStretch()
        stokes_layout.addLayout(stokes_mode_row)

        # Parameter selection
        param_row = QHBoxLayout()
        param_row.setSpacing(20)
        # param_label = QLabel("Parameter:")
        # param_label.setStyleSheet("font-weight: 600;")
        # param_row.addWidget(param_label)
        self.stokes_label = QLabel("Parameter:")
        self.stokes_label.setStyleSheet("font-weight: 600;")
        param_row.addWidget(self.stokes_label)

        self.stokes_combo = QComboBox()
        self.stokes_combo.addItems(["I", "Q", "U", "V"])
        self.stokes_combo.setMinimumWidth(100)
        param_row.addWidget(self.stokes_combo)
        param_row.addStretch()
        stokes_layout.addLayout(param_row)

        # Connect stokes radios
        self.single_stokes_radio.toggled.connect(self.update_stokes_mode)
        self.full_stokes_radio.toggled.connect(self.update_stokes_mode)

        options_layout.addWidget(stokes_group)

        # Method Settings
        method_group = QGroupBox("Detection Method")
        method_layout = QVBoxLayout(method_group)
        method_layout.setSpacing(12)

        # Method selection
        met_sel_row = QHBoxLayout()
        met_sel_row.setSpacing(20)
        met_label = QLabel("Method:")
        met_label.setStyleSheet("font-weight: 600;")
        met_sel_row.addWidget(met_label)

        self.gaussian_method_radio = QRadioButton("Gaussian Fitting")
        self.com_method_radio = QRadioButton("Center of Mass")
        self.gaussian_method_radio.setChecked(True)
        met_sel_row.addWidget(self.gaussian_method_radio)
        met_sel_row.addWidget(self.com_method_radio)
        met_sel_row.addStretch()
        method_layout.addLayout(met_sel_row)

        # Sigma threshold
        sigma_row = QHBoxLayout()
        sigma_row.setSpacing(20)
        self.sigma_label = QLabel("Sigma:")
        self.sigma_label.setStyleSheet("font-weight: 600;")
        sigma_row.addWidget(self.sigma_label)

        self.sigma_spinbox = QDoubleSpinBox()
        self.sigma_spinbox.setRange(1.0, 20.0)
        self.sigma_spinbox.setValue(10.0)
        self.sigma_spinbox.setSingleStep(0.5)
        self.sigma_spinbox.setToolTip(
            "Threshold multiplier for center-of-mass detection"
        )
        sigma_row.addWidget(self.sigma_spinbox)
        sigma_row.addStretch()
        method_layout.addLayout(sigma_row)

        # Initially disable sigma
        # self.sigma_label.setEnabled(False)
        # self.sigma_spinbox.setEnabled(False)
        self.sigma_label.setVisible(False)
        self.sigma_spinbox.setVisible(False)

        # Connect method radios
        self.gaussian_method_radio.toggled.connect(self._update_method_options)
        self.com_method_radio.toggled.connect(self._update_method_options)

        # Visual centering option
        self.visual_center_check = QCheckBox("Create visually centered image")
        self.visual_center_check.setChecked(True)
        self.visual_center_check.setToolTip(
            "Shifts pixel data so Sun appears at image center"
        )
        method_layout.addWidget(self.visual_center_check)

        options_layout.addWidget(method_group)

        # Multiprocessing Options (Batch Only)
        self.multiprocessing_widget = QWidget()
        mp_layout = QVBoxLayout(self.multiprocessing_widget)
        mp_layout.setContentsMargins(0, 0, 0, 0)

        mp_group = QGroupBox("Performance")
        mp_group_layout = QVBoxLayout(mp_group)

        self.multiprocessing_check = QCheckBox("Use multiprocessing")
        self.multiprocessing_check.setChecked(True)
        mp_group_layout.addWidget(self.multiprocessing_check)

        cores_row = QHBoxLayout()
        cores_row.setSpacing(20)
        cores_label = QLabel("CPU cores:")
        cores_label.setStyleSheet("font-weight: 600;")
        cores_row.addWidget(cores_label)

        self.cores_spinbox = QSpinBox()
        self.cores_spinbox.setRange(1, multiprocessing.cpu_count())
        self.cores_spinbox.setValue(max(1, multiprocessing.cpu_count() - 1))
        self.cores_spinbox.setSingleStep(1)
        cores_row.addWidget(self.cores_spinbox)
        cores_row.addStretch()
        mp_group_layout.addLayout(cores_row)

        mp_layout.addWidget(mp_group)

        # Connect MP check
        self.multiprocessing_check.toggled.connect(self.cores_spinbox.setEnabled)

        options_layout.addWidget(self.multiprocessing_widget)
        options_layout.addStretch()

        # Wrap Options Tab in Scroll Area
        options_scroll = QScrollArea()
        options_scroll.setWidgetResizable(True)
        options_scroll.setFrameShape(QFrame.NoFrame)
        options_scroll.setWidget(options_tab)
        self.tab_widget.addTab(options_scroll, "⚙️ Options")

        # ========== TAB 3: OUTPUT ==========
        output_tab = QWidget()
        output_layout = QVBoxLayout(output_tab)
        output_layout.setSpacing(16)
        output_layout.setContentsMargins(16, 16, 16, 16)

        out_group = QGroupBox("Output Settings")
        out_layout = QVBoxLayout(out_group)
        out_layout.setSpacing(12)

        # SINGLE OUTPUT
        self.single_output_widget = QWidget()
        single_out_layout = QHBoxLayout(self.single_output_widget)
        single_out_layout.setContentsMargins(0, 0, 0, 0)

        out_label = QLabel("File:")
        out_label.setMinimumWidth(70)
        out_label.setStyleSheet("font-weight: 600;")
        single_out_layout.addWidget(out_label)

        self.output_path_edit = QLineEdit("")
        self.output_path_edit.setPlaceholderText("Default: centered_{input_name}.fits")
        single_out_layout.addWidget(self.output_path_edit, 1)

        self.output_browse_button = QPushButton("Browse...")
        self.output_browse_button.clicked.connect(self.browse_output)
        single_out_layout.addWidget(self.output_browse_button)
        out_layout.addWidget(self.single_output_widget)

        # BATCH OUTPUT
        self.batch_output_widget = QWidget()
        batch_out_layout = QHBoxLayout(self.batch_output_widget)
        batch_out_layout.setContentsMargins(0, 0, 0, 0)

        out_pat_label = QLabel("Pattern:")
        out_pat_label.setMinimumWidth(70)
        out_pat_label.setStyleSheet("font-weight: 600;")
        batch_out_layout.addWidget(out_pat_label)

        self.output_pattern_edit = QLineEdit("")
        self.output_pattern_edit.setPlaceholderText("e.g., centered/centered_*.fits")
        batch_out_layout.addWidget(self.output_pattern_edit, 1)

        self.output_pattern_button = QPushButton("Browse Dir...")
        self.output_pattern_button.clicked.connect(self.browse_output_dir)
        batch_out_layout.addWidget(self.output_pattern_button)
        out_layout.addWidget(self.batch_output_widget)

        output_layout.addWidget(out_group)

        # Pattern help
        help_group = QGroupBox("Pattern Help")
        help_layout = QVBoxLayout(help_group)
        help_text = QLabel(
            "Use <b>*</b> as a placeholder for the original filename.\n\n"
            "<b>Example:</b> Input <code>sun_2024.fits</code> with pattern <code>centered_*.fits</code>\n"
            "→ Output: <code>centered_sun_2024.fits</code>"
        )
        help_text.setWordWrap(True)
        help_text.setStyleSheet(f"color: {text_secondary}; line-height: 1.5;")
        help_layout.addWidget(help_text)
        output_layout.addWidget(help_group)
        output_layout.addStretch()

        # Wrap Output Tab in Scroll Area
        output_scroll = QScrollArea()
        output_scroll.setWidgetResizable(True)
        output_scroll.setFrameShape(QFrame.NoFrame)
        output_scroll.setWidget(output_tab)
        self.tab_widget.addTab(output_scroll, "💾 Output")

        main_layout.addWidget(self.tab_widget, 1)

        # ========== STATUS PANEL ==========
        status_group = QGroupBox("Status / Results")
        status_layout = QVBoxLayout(status_group)
        status_layout.setContentsMargins(10, 12, 10, 10)

        self.status_text = QPlainTextEdit()
        self.status_text.setReadOnly(True)
        self.status_text.setPlaceholderText("Status and results will appear here")
        self.status_text.setMinimumHeight(25)
        self.status_text.setMaximumHeight(150)
        status_layout.addWidget(self.status_text, 1)

        main_layout.addWidget(status_group)

        main_layout.addWidget(status_group)

        # Progress Bar (initially hidden)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)

        # Dialog Buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.apply_phase_shift)
        button_box.rejected.connect(self.reject)

        self.ok_button = button_box.button(QDialogButtonBox.Ok)
        self.ok_button.setText("Apply Shift")

        self.cancel_button = button_box.button(QDialogButtonBox.Cancel)
        # We'll use the cancel button for aborting operations too

        main_layout.addWidget(button_box)

        # Initialize UI state
        self.update_mode_ui()

    def update_mode_ui(self):
        """Update UI components based on the selected mode"""
        single_mode = self.single_mode_radio.isChecked()

        # Update visibility of widgets
        self.single_file_widget.setVisible(single_mode)
        self.batch_file_widget.setVisible(not single_mode)
        self.single_output_widget.setVisible(single_mode)
        self.batch_output_widget.setVisible(not single_mode)

        # Multiprocessing options only visible in batch mode
        self.multiprocessing_widget.setVisible(not single_mode)

        # Update button text
        if single_mode:
            self.ok_button.setText("Apply Shift")
        else:
            self.ok_button.setText("Apply Batch Shift")

    def update_stokes_mode(self):
        """Update UI based on selected Stokes mode"""
        single_stokes = self.single_stokes_radio.isChecked()
        self.stokes_label.setVisible(single_stokes)
        self.stokes_combo.setVisible(single_stokes)

    def _update_method_options(self):
        """Enable/disable sigma threshold based on detection method selection"""
        use_com = self.com_method_radio.isChecked()
        self.sigma_label.setVisible(use_com)
        self.sigma_spinbox.setVisible(use_com)

    def browse_image(self):
        """Browse for input image file (FITS or CASA image directory)"""
        # Ask user which type of image to select
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Select Image Type")
        msg_box.setText("What type of image do you want to select?")
        fits_btn = msg_box.addButton("FITS", QMessageBox.ActionRole)
        casa_btn = msg_box.addButton("CASA Image", QMessageBox.ActionRole)
        msg_box.addButton(QMessageBox.Cancel)
        msg_box.exec_()

        file_path = None
        if msg_box.clickedButton() == fits_btn:
            file_path, _ = QFileDialog.getOpenFileName(
                self, "Select FITS File", "", "FITS Files (*.fits *.fts);;All Files (*)"
            )
        elif msg_box.clickedButton() == casa_btn:
            file_path = QFileDialog.getExistingDirectory(
                self, "Select CASA Image Directory"
            )

        if file_path:
            self.image_path_edit.setText(file_path)
            self.imagename = file_path

            # Set default output filename: centered_{input_basename}.fits
            file_dir = os.path.dirname(file_path)
            file_name = os.path.basename(file_path)
            # Remove extension and add centered_ prefix
            base_name = os.path.splitext(file_name)[0]
            # Handle CASA image directories - strip .image or .im suffix
            if os.path.isdir(file_path):
                base_name = file_name
                # Remove common CASA extensions
                for ext in [".image", ".im"]:
                    if base_name.endswith(ext):
                        base_name = base_name[: -len(ext)]
                        break
            output_path = os.path.join(file_dir, f"centered_{base_name}.fits")
            self.output_path_edit.setText(output_path)

    def browse_input_pattern(self):
        """Browse for directory and help set input pattern"""
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select Directory for Input Files"
        )
        if dir_path:
            # Set a default pattern in the selected directory
            self.input_pattern_edit.setText(os.path.join(dir_path, "*.fits"))

            # Set a default output pattern in a 'centered' subdirectory
            output_dir = os.path.join(dir_path, "centered")
            self.output_pattern_edit.setText(
                os.path.join(output_dir, "centered_*.fits")
            )

    def browse_output_dir(self):
        """Browse for output directory for batch processing"""
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select Directory for Output Files"
        )
        if dir_path:
            # Preserve the filename pattern but update the directory
            pattern = os.path.basename(self.output_pattern_edit.text())
            if not pattern:
                pattern = "shifted_*.fits"
            self.output_pattern_edit.setText(os.path.join(dir_path, pattern))

    def browse_ms(self):
        """Browse for MS file"""
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select Measurement Set Directory"
        )
        if dir_path:
            self.ms_path_edit.setText(dir_path)

    def browse_output(self):
        """Browse for output file location (FITS only)"""
        # Get suggested filename from input if available
        suggested = ""
        if self.image_path_edit.text():
            input_name = os.path.basename(self.image_path_edit.text())
            base_name = os.path.splitext(input_name)[0]
            if os.path.isdir(self.image_path_edit.text()):
                base_name = input_name
                # Remove common CASA extensions
                for ext in [".image", ".im"]:
                    if base_name.endswith(ext):
                        base_name = base_name[: -len(ext)]
                        break
            suggested = f"centered_{base_name}.fits"

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Output As", suggested, "FITS Files (*.fits)"
        )
        if file_path:
            # Ensure .fits extension
            if not file_path.endswith(".fits"):
                file_path = file_path + ".fits"
            self.output_path_edit.setText(file_path)

    def browse_reference_image(self):
        """Browse for reference image file for batch processing"""
        # Ask user which type of image to select
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Select Reference Image Type")
        msg_box.setText("What type of image do you want to select as reference?")
        fits_btn = msg_box.addButton("FITS", QMessageBox.ActionRole)
        casa_btn = msg_box.addButton("CASA Image", QMessageBox.ActionRole)
        msg_box.addButton(QMessageBox.Cancel)
        msg_box.exec_()

        file_path = None
        if msg_box.clickedButton() == fits_btn:
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "Select FITS Reference",
                "",
                "FITS Files (*.fits *.fts);;All Files (*)",
            )
        elif msg_box.clickedButton() == casa_btn:
            file_path = QFileDialog.getExistingDirectory(
                self, "Select CASA Reference Directory"
            )

        if file_path:
            self.reference_image_edit.setText(file_path)

            # Set default input pattern in the same directory
            if not self.input_pattern_edit.text():
                file_dir = os.path.dirname(file_path)
                if msg_box.clickedButton() == fits_btn:
                    self.input_pattern_edit.setText(os.path.join(file_dir, "*.fits"))
                elif msg_box.clickedButton() == casa_btn:
                    self.input_pattern_edit.setText(os.path.join(file_dir, "*.image"))

            # Update default output pattern if needed
            if not self.output_pattern_edit.text():
                file_dir = os.path.dirname(file_path)
                output_dir = os.path.join(file_dir, "centered")
                self.output_pattern_edit.setText(
                    os.path.join(output_dir, "centered_*.fits")
                )

    def scan_files(self):
        """Scan for matched files and display count inline"""
        input_pattern = self.input_pattern_edit.text()
        if not input_pattern:
            self.files_count_label.setText("Please enter a pattern")
            return

        try:
            matched_files = sorted(glob.glob(input_pattern))
            count = len(matched_files)

            # Format text
            if count == 0:
                text = "No files found"
                color = "#FF6B6B"  # Light red
            else:
                text = f"Found {count} files"
                color = "#69F0AE"  # Light green (matching HPC dialog style)

            self.files_count_label.setText(text)
            self.files_count_label.setStyleSheet(
                f"color: {color}; font-style: italic; padding-left: 70px;"
            )

        except Exception as e:
            self.files_count_label.setText(f"Error: {str(e)}")
            self.files_count_label.setStyleSheet("color: #FF6B6B; padding-left: 70px;")

    def _get_matching_files(self):
        """Helper to get files matching the input pattern"""
        pattern = self.input_pattern_edit.text()
        if not pattern:
            return []
        return sorted(glob.glob(pattern))

    def apply_phase_shift(self):
        """Apply the phase shift to the image(s)"""
        import os
        from .move_phasecenter import SolarPhaseCenter
        from PyQt5.QtWidgets import QProgressDialog, QApplication, QMessageBox
        from PyQt5.QtCore import Qt
        import multiprocessing
        import time

        # Check if we're in batch mode or single file mode
        batch_mode = self.batch_mode_radio.isChecked()

        # Check if we're processing full Stokes
        full_stokes = self.full_stokes_radio.isChecked()

        # Validate inputs
        if batch_mode:
            if not self.reference_image_edit.text():
                QMessageBox.warning(
                    self,
                    "Input Error",
                    "Please select a reference image for phase center calculation",
                )
                return
            if not self.input_pattern_edit.text():
                QMessageBox.warning(
                    self, "Input Error", "Please specify a pattern for files to process"
                )
                return
        else:
            if not self.image_path_edit.text():
                QMessageBox.warning(self, "Input Error", "Please select an input image")
                return

        try:
            # Create SolarPhaseCenter instance
            spc = SolarPhaseCenter(msname=None)

            # Determine common parameters based on mode
            if batch_mode:
                reference_image = self.reference_image_edit.text()
                input_pattern = self.input_pattern_edit.text()
                output_pattern = self.output_pattern_edit.text() or None
                matching_files = glob.glob(input_pattern)

                if not matching_files:
                    QMessageBox.warning(
                        self, "Input Error", f"No files match: {input_pattern}"
                    )
                    return

                self.status_text.appendPlainText(f"Found {len(matching_files)} files.")
            else:
                reference_image = self.image_path_edit.text()
                # Single mode input list
                matching_files = [reference_image]
                # Default output pattern for single file
                output_pattern = self.output_path_edit.text() or None

            # 1. Coordinate check and Phase Result Calculation
            is_hpc = self._is_helioprojective(reference_image)

            use_gaussian = self.gaussian_method_radio.isChecked()
            try:
                self.status_text.appendPlainText(
                    f"Calculating phase shift from: {reference_image}..."
                )
                phase_result = spc.cal_solar_phaseshift(
                    imagename=reference_image,
                    fit_gaussian=use_gaussian,
                    sigma=self.sigma_spinbox.value(),
                )
            except Exception as e:
                QMessageBox.critical(
                    self,
                    "Calculation Error",
                    f"Failed to calculate phase shift:\n{str(e)}",
                )
                return

            # Handle HPC override or display info
            if is_hpc:
                phase_result["true_ra"] = 0.0
                phase_result["true_dec"] = 0.0
                phase_result["is_hpc"] = True
                self.status_text.appendPlainText(
                    "Detected HPC. Target: (0, 0) Solar-X/Y"
                )
            else:
                phase_result["is_hpc"] = False
                self.status_text.appendPlainText(
                    f"True Position: RA={phase_result.get('true_ra', 'N/A'):.4f}, DEC={phase_result.get('true_dec', 'N/A'):.4f}"
                )

            # Check if shift needed
            if not phase_result.get("needs_shift", False):
                self.status_text.appendPlainText("No significant phase shift needed.")
                if (
                    QMessageBox.question(
                        self,
                        "No Shift Needed",
                        "Solar center appears aligned. Process anyway?",
                        QMessageBox.Yes | QMessageBox.No,
                    )
                    == QMessageBox.No
                ):
                    return

            # 2. Check Overwrite (Single Mode only, for safety)
            if not batch_mode and output_pattern:
                if not output_pattern.endswith(".fits"):
                    output_pattern += ".fits"
                files_to_check = []
                stokes_params = (
                    ["I", "Q", "U", "V"] if full_stokes else ["I"]
                )  # Simplified check

                for s in stokes_params:
                    # Rough check logic, assuming output_pattern is full path
                    fname = output_pattern
                    if full_stokes:
                        root, ext = os.path.splitext(output_pattern)
                        fname = f"{root}_{s}{ext}"
                    files_to_check.append(fname)

                existing = [f for f in files_to_check if os.path.exists(f)]
                if existing:
                    msg = "Overwrite existing files?\n\n" + "\n".join(existing[:5])
                    if len(existing) > 5:
                        msg += "\n..."
                    if (
                        QMessageBox.question(
                            self, "Overwrite?", msg, QMessageBox.Yes | QMessageBox.No
                        )
                        == QMessageBox.No
                    ):
                        return

            # 3. Prepare Tasks
            visual_center = self.visual_center_check.isChecked()
            use_multiprocessing = self.multiprocessing_check.isChecked() and batch_mode
            if not batch_mode:
                use_multiprocessing = False  # Force synchronous for single file

            stokes_list = (
                ["I", "Q", "U", "V"]
                if full_stokes
                else [self.stokes_combo.currentText()]
            )
            if full_stokes:
                self.status_text.appendPlainText("Processing Full Stokes (I,Q,U,V)")

            tasks = []
            for fpath in matching_files:
                for stokes in stokes_list:
                    # Construct task tuple matching process_single_file_phase_shift wrapper
                    task = (
                        fpath,
                        phase_result.get("true_ra"),
                        phase_result.get("true_dec"),
                        stokes,
                        output_pattern,
                        visual_center,
                        phase_result,
                    )
                    tasks.append(task)

            total_tasks = len(tasks)
            self.status_text.appendPlainText(
                f"Prepared {total_tasks} processing tasks."
            )

            # 4. Progress UI Setup (Embedded)
            self.progress_bar.setVisible(True)
            self.progress_bar.setRange(0, total_tasks)
            self.progress_bar.setValue(0)
            self.ok_button.setEnabled(False)  # Disable Apply
            self.input_group.setEnabled(False)  # Disable inputs
            self.tab_widget.setEnabled(False)  # Disable tabs
            self.cancel_button.disconnect()  # Disconnect default reject
            self.cancel_button.clicked.connect(self._cancel_processing)

            self._processing_cancelled = False
            QApplication.processEvents()

            results = []
            start_time = time.time()

            # 5. Execution Loop
            try:
                if use_multiprocessing and len(matching_files) > 1:
                    max_cores = self.cores_spinbox.value()
                    self.status_text.appendPlainText(
                        f"Using Multiprocessing ({max_cores} cores)"
                    )

                    self.pool = multiprocessing.Pool(processes=max_cores)
                    async_results = [
                        self.pool.apply_async(process_single_file_phase_shift, (t,))
                        for t in tasks
                    ]
                    self.pool.close()

                    while any(not r.ready() for r in async_results):
                        QApplication.processEvents()
                        if self._processing_cancelled:
                            self.pool.terminate()
                            self.pool.join()
                            self.status_text.appendPlainText("Processing canceled.")
                            break

                        completed = sum(1 for r in async_results if r.ready())
                        self.progress_bar.setValue(completed)
                        time.sleep(0.05)

                    if not self._processing_cancelled:
                        self.progress_bar.setValue(total_tasks)
                        results = [r.get() for r in async_results]

                    self.pool = None

                else:
                    # Synchronous
                    for i, task in enumerate(tasks):
                        QApplication.processEvents()
                        if self._processing_cancelled:
                            self.status_text.appendPlainText("Processing canceled.")
                            results = []  # Incomplete
                            break

                        # Call wrapper directly
                        res = process_single_file_phase_shift(task)
                        results.append(res)

                        self.progress_bar.setValue(i + 1)

            except Exception as e:
                import traceback

                traceback.print_exc()
                QMessageBox.critical(self, "Processing Failed", f"Error: {e}")
                # Don't return yet, ensuring cleanup happens

            finally:
                # Cleanup UI state
                self.progress_bar.setVisible(False)
                self.ok_button.setEnabled(True)
                self.input_group.setEnabled(True)
                self.tab_widget.setEnabled(True)

                # Reconnect cancel to reject (close dialog)
                try:
                    self.cancel_button.clicked.disconnect()
                except:
                    pass
                self.cancel_button.clicked.connect(self.reject)

            # 6. Results Summary
            if self._processing_cancelled:
                return  # Canceled

            elapsed = time.time() - start_time
            success_count = sum(1 for r in results if r[0])
            errors = [f"{os.path.basename(r[1])}: {r[2]}" for r in results if not r[0]]

            self.status_text.appendPlainText("-" * 20)
            self.status_text.appendPlainText(f"Completed in {elapsed:.1f}s")
            self.status_text.appendPlainText(f"Success: {success_count}/{total_tasks}")

            if errors:
                self.status_text.appendPlainText("Errors occurred:")
                for err in errors[:10]:
                    self.status_text.appendPlainText(f" - {err}")
                if len(errors) > 10:
                    self.status_text.appendPlainText(f" ...and {len(errors)-10} more.")
                QMessageBox.warning(
                    self,
                    "Partial Success",
                    f"Done with {len(errors)} errors. Check log.",
                )
            elif success_count > 0:
                QMessageBox.information(
                    self, "Done", f"Successfully processed {success_count} files."
                )
            else:
                self.status_text.appendPlainText("No files processed.")

        except Exception as e:
            self.status_text.appendPlainText(f"Detailed Error: {str(e)}")
            import traceback

            traceback.print_exc()

    def _cancel_processing(self):
        """Signal processing cancellation"""
        self._processing_cancelled = True
        self.status_text.appendPlainText("Stopping... please wait.")
        self.cancel_button.setEnabled(False)  # Prevent double click

    def _is_helioprojective(self, imagepath):
        """Check if image is in helioprojective coordinates (Solar-X/Y)"""
        import os

        if not imagepath or not os.path.exists(imagepath):
            return False

        try:
            # For FITS files, check the header
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
                    return True

            # For CASA images, check coordinate system
            if os.path.isdir(imagepath):
                try:
                    from casatools import image as IA

                    ia_tool = IA()
                    ia_tool.open(imagepath)
                    csys = ia_tool.coordsys()
                    dimension_names = [n.upper() for n in csys.names()]
                    ia_tool.close()

                    if "SOLAR-X" in dimension_names or "SOLAR-Y" in dimension_names:
                        return True
                    if "HPLN-TAN" in dimension_names or "HPLT-TAN" in dimension_names:
                        return True
                except Exception:
                    pass

            return False
        except Exception:
            return False

    def showEvent(self, event):
        """Handle the show event to ensure correct sizing"""
        super().showEvent(event)

        # Ensure the dialog size matches the parent when shown
        if self.parent() and self.parent().size().isValid():
            # Set size to match parent
            # self.resize(self.parent().size())

            # Center relative to parent
            self.move(
                self.parent().frameGeometry().topLeft()
                + self.parent().rect().center()
                - self.rect().center()
            )


class HPCBatchConversionDialog(QDialog):
    """Dialog for batch conversion of images to helioprojective coordinates."""

    def __init__(self, parent=None, current_file=None):
        super().__init__(parent)
        self.setWindowTitle("Batch Conversion to Helioprojective Coordinates")
        self.setMinimumSize(600, 500)
        self.resize(750, 700)
        self.parent = parent
        self.current_file = current_file

        # Import theme manager for theme-aware styling
        try:
            from .styles import theme_manager
        except ImportError:
            from styles import theme_manager

        # Get colors directly from the palette for consistency
        palette = theme_manager.palette
        is_dark = theme_manager.is_dark

        border_color = palette["border"]
        surface_color = palette["surface"]
        base_color = palette["base"]
        disabled_color = palette["disabled"]
        text_color = palette["text"]
        highlight_color = palette["highlight"]
        text_secondary = palette.get("text_secondary", disabled_color)

        # Apply theme-aware stylesheet
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
                padding: 6px 10px;
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
                padding: 5px 10px;
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
                spacing: 8px;
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
                padding: 4px 8px;
            }}
            QSpinBox:disabled, QDoubleSpinBox:disabled {{
                background-color: {surface_color};
                color: {disabled_color};
            }}
            QCheckBox {{
                color: {text_color};
                spacing: 8px;
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
                padding: 4px 8px;
                border-radius: 4px;
            }}
            QListWidget::item:selected {{
                background-color: {highlight_color};
            }}
            QPlainTextEdit {{
                background-color: {base_color};
                color: {text_color};
                border: 1px solid {border_color};
                border-radius: 6px;
                padding: 8px;
            }}
            QPushButton {{
                padding: 6px 16px;
                border-radius: 6px;
            }}
        """
        )

        # Store theme colors for later use
        self._highlight_color = highlight_color
        self._text_secondary = text_secondary

        self.setup_ui()
        set_hand_cursor(self)

    def setup_ui(self):
        """Set up the dialog UI with a modern tabbed layout."""
        try:
            from .styles import theme_manager
        except ImportError:
            from styles import theme_manager

        palette = theme_manager.palette
        highlight_color = palette["highlight"]
        text_secondary = palette.get("text_secondary", palette["disabled"])
        border_color = palette["border"]

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # Header description
        header = QLabel(
            "Convert multiple FITS/CASA images to helioprojective coordinates (HPC) in batch."
        )
        header.setWordWrap(True)
        header.setStyleSheet(
            f"font-style: italic; color: {text_secondary}; padding: 4px 0;"
        )
        main_layout.addWidget(header)

        # ========== TAB WIDGET ==========
        self.tab_widget = QTabWidget()

        # Import QScrollArea for scrollable tabs
        from PyQt5.QtWidgets import QScrollArea

        # ========== TAB 1: FILES ==========
        files_tab = QWidget()
        files_layout = QVBoxLayout(files_tab)
        files_layout.setSpacing(16)
        files_layout.setContentsMargins(16, 16, 16, 16)

        # Input directory row
        dir_group = QGroupBox("Input Location")
        dir_group_layout = QVBoxLayout(dir_group)
        dir_group_layout.setSpacing(12)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(12)
        dir_label = QLabel("Directory:")
        dir_label.setStyleSheet("font-weight: 600;")
        dir_label.setMinimumWidth(70)
        dir_row.addWidget(dir_label)

        self.dir_edit = QLineEdit()
        if self.current_file:
            self.dir_edit.setText(os.path.dirname(self.current_file))
        self.dir_edit.setPlaceholderText("Select input directory...")
        dir_row.addWidget(self.dir_edit, 1)

        self.dir_browse_btn = QPushButton("Browse...")
        self.dir_browse_btn.clicked.connect(self.browse_directory)
        dir_row.addWidget(self.dir_browse_btn)
        dir_group_layout.addLayout(dir_row)

        # File pattern row
        pattern_row = QHBoxLayout()
        pattern_row.setSpacing(12)
        pattern_label = QLabel("Pattern:")
        pattern_label.setStyleSheet("font-weight: 600;")
        pattern_label.setMinimumWidth(70)
        pattern_row.addWidget(pattern_label)

        self.pattern_edit = QLineEdit()
        if self.current_file:
            file_ext = os.path.splitext(self.current_file)[1]
            self.pattern_edit.setText(f"*{file_ext}")
        else:
            self.pattern_edit.setText("*.fits")
        self.pattern_edit.setPlaceholderText("e.g., *.fits, sun_*.fits")
        pattern_row.addWidget(self.pattern_edit, 1)

        # Scan button - scans and shows count without opening preview
        scan_btn = QPushButton("🔍")
        scan_btn.setFixedWidth(36)
        scan_btn.setToolTip("Scan for matching files")
        scan_btn.clicked.connect(self.scan_files)
        pattern_row.addWidget(scan_btn)

        preview_btn = QPushButton("Preview")
        preview_btn.setToolTip("Show list of files matching the pattern")
        preview_btn.clicked.connect(self.preview_files)
        pattern_row.addWidget(preview_btn)
        dir_group_layout.addLayout(pattern_row)

        # File count label (shown after scanning)
        self.files_count_label = QLabel("")
        self.files_count_label.setStyleSheet(
            f"color: {text_secondary}; font-style: italic; padding-left: 70px;"
        )
        dir_group_layout.addWidget(self.files_count_label)

        files_layout.addWidget(dir_group)
        files_layout.addStretch()

        # Wrap in scroll area
        files_scroll = QScrollArea()
        files_scroll.setWidgetResizable(True)
        files_scroll.setFrameShape(QFrame.NoFrame)
        files_scroll.setWidget(files_tab)
        self.tab_widget.addTab(files_scroll, "📁 Files")

        # ========== TAB 2: OPTIONS ==========
        options_tab = QWidget()
        options_layout = QVBoxLayout(options_tab)
        options_layout.setSpacing(16)
        options_layout.setContentsMargins(16, 16, 16, 16)

        # Stokes Settings
        stokes_group = QGroupBox("Stokes Parameters")
        stokes_layout = QVBoxLayout(stokes_group)
        stokes_layout.setSpacing(12)

        # Mode selection
        mode_row = QHBoxLayout()
        mode_row.setSpacing(20)
        mode_label = QLabel("Mode:")
        mode_label.setStyleSheet("font-weight: 600;")
        mode_row.addWidget(mode_label)

        self.single_stokes_radio = QRadioButton("Single Stokes")
        self.single_stokes_radio.setToolTip(
            "Convert only one Stokes parameter per file"
        )
        self.full_stokes_radio = QRadioButton("Full Stokes (I, Q, U, V)")
        self.full_stokes_radio.setToolTip("Convert all Stokes parameters for each file")
        self.single_stokes_radio.setChecked(True)
        mode_row.addWidget(self.single_stokes_radio)
        mode_row.addWidget(self.full_stokes_radio)
        mode_row.addStretch()
        stokes_layout.addLayout(mode_row)

        # Stokes selection
        param_row = QHBoxLayout()
        param_row.setSpacing(20)
        param_label = QLabel("Parameter:")
        param_label.setStyleSheet("font-weight: 600;")
        param_row.addWidget(param_label)

        self.stokes_combo = QComboBox()
        self.stokes_combo.addItems(["I", "Q", "U", "V"])
        self.stokes_combo.setMinimumWidth(80)
        param_row.addWidget(self.stokes_combo)
        param_row.addStretch()
        stokes_layout.addLayout(param_row)

        # Connect mode radios
        self.single_stokes_radio.toggled.connect(self.update_stokes_mode)
        self.full_stokes_radio.toggled.connect(self.update_stokes_mode)

        options_layout.addWidget(stokes_group)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background-color: {border_color};")
        sep.setFixedHeight(1)
        options_layout.addWidget(sep)

        # Performance Settings
        perf_group = QGroupBox("Performance")
        perf_layout = QVBoxLayout(perf_group)
        perf_layout.setSpacing(12)

        self.multiprocessing_check = QCheckBox(
            "Use multiprocessing for faster conversion"
        )
        self.multiprocessing_check.setChecked(True)
        perf_layout.addWidget(self.multiprocessing_check)

        cores_row = QHBoxLayout()
        cores_row.setSpacing(12)
        cores_label = QLabel("CPU Cores:")
        cores_label.setStyleSheet("font-weight: 600;")
        cores_row.addWidget(cores_label)

        self.cores_spinbox = QSpinBox()
        self.cores_spinbox.setRange(1, multiprocessing.cpu_count())
        self.cores_spinbox.setValue(max(1, multiprocessing.cpu_count() - 1))
        self.cores_spinbox.setMinimumWidth(70)
        cores_row.addWidget(self.cores_spinbox)

        cores_hint = QLabel(f"(max: {multiprocessing.cpu_count()})")
        cores_hint.setStyleSheet(f"color: {text_secondary};")
        cores_row.addWidget(cores_hint)
        cores_row.addStretch()
        perf_layout.addLayout(cores_row)

        self.multiprocessing_check.toggled.connect(self.cores_spinbox.setEnabled)

        options_layout.addWidget(perf_group)
        options_layout.addStretch()

        # Wrap in scroll area
        options_scroll = QScrollArea()
        options_scroll.setWidgetResizable(True)
        options_scroll.setFrameShape(QFrame.NoFrame)
        options_scroll.setWidget(options_tab)
        self.tab_widget.addTab(options_scroll, "⚙️ Options")

        # ========== TAB 3: OUTPUT ==========
        output_tab = QWidget()
        output_layout = QVBoxLayout(output_tab)
        output_layout.setSpacing(16)
        output_layout.setContentsMargins(16, 16, 16, 16)

        # Output location
        out_group = QGroupBox("Output Location")
        out_group_layout = QVBoxLayout(out_group)
        out_group_layout.setSpacing(12)

        out_dir_row = QHBoxLayout()
        out_dir_row.setSpacing(12)
        out_dir_label = QLabel("Directory:")
        out_dir_label.setStyleSheet("font-weight: 600;")
        out_dir_label.setMinimumWidth(70)
        out_dir_row.addWidget(out_dir_label)

        self.output_dir_edit = QLineEdit()
        if self.current_file:
            # Default to input_dir/hpc/
            self.output_dir_edit.setText(
                os.path.join(os.path.dirname(self.current_file), "hpc")
            )
        self.output_dir_edit.setPlaceholderText("Select output directory...")
        out_dir_row.addWidget(self.output_dir_edit, 1)

        self.output_dir_btn = QPushButton("Browse...")
        self.output_dir_btn.clicked.connect(self.browse_output_directory)
        out_dir_row.addWidget(self.output_dir_btn)
        out_group_layout.addLayout(out_dir_row)

        # Output pattern row
        out_pattern_row = QHBoxLayout()
        out_pattern_row.setSpacing(12)
        out_pattern_label = QLabel("Pattern:")
        out_pattern_label.setStyleSheet("font-weight: 600;")
        out_pattern_label.setMinimumWidth(70)
        out_pattern_row.addWidget(out_pattern_label)

        self.output_pattern_edit = QLineEdit("hpc_*.fits")
        self.output_pattern_edit.setPlaceholderText("e.g., hpc_*.fits")
        out_pattern_row.addWidget(self.output_pattern_edit, 1)
        out_group_layout.addLayout(out_pattern_row)

        output_layout.addWidget(out_group)

        # Pattern help
        help_group = QGroupBox("Pattern Help")
        help_layout = QVBoxLayout(help_group)

        help_text = QLabel(
            "Use <b>*</b> as a placeholder for the original filename.\n\n"
            "<b>Example:</b> Input <code>sun_2024.fits</code> with pattern <code>hpc_*.fits</code>\n"
            "→ Output: <code>hpc_sun_2024.fits</code>"
        )
        help_text.setWordWrap(True)
        help_text.setStyleSheet(f"color: {text_secondary}; line-height: 1.5;")
        help_layout.addWidget(help_text)

        output_layout.addWidget(help_group)
        output_layout.addStretch()

        # Wrap in scroll area
        output_scroll = QScrollArea()
        output_scroll.setWidgetResizable(True)
        output_scroll.setFrameShape(QFrame.NoFrame)
        output_scroll.setWidget(output_tab)
        self.tab_widget.addTab(output_scroll, "💾 Output")

        main_layout.addWidget(self.tab_widget, 1)

        # ========== STATUS PANEL (ALWAYS VISIBLE) ==========
        status_group = QGroupBox("Status")
        status_layout = QVBoxLayout(status_group)
        status_layout.setContentsMargins(10, 12, 10, 10)

        self.status_text = QPlainTextEdit()
        self.status_text.setReadOnly(True)
        self.status_text.setPlaceholderText(
            "Conversion status and results will appear here..."
        )
        self.status_text.setMinimumHeight(25)
        self.status_text.setMaximumHeight(150)
        status_layout.addWidget(self.status_text, 1)

        # Progress Bar (initially hidden)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(True)
        status_layout.addWidget(self.progress_bar)

        main_layout.addWidget(status_group)

        # ========== DIALOG BUTTONS ==========
        button_layout = QHBoxLayout()
        button_layout.setSpacing(12)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok_button = button_box.button(QDialogButtonBox.Ok)
        self.ok_button.setText("Convert")
        self.ok_button.setMinimumWidth(100)

        self.cancel_button = button_box.button(QDialogButtonBox.Cancel)
        # We'll re-purpose the cancel button during processing

        button_box.accepted.connect(self.convert_files)
        button_box.rejected.connect(self.reject)

        button_layout.addStretch()
        button_layout.addWidget(button_box)

        main_layout.addLayout(button_layout)

    def browse_directory(self):
        """Browse for input directory"""
        current_dir = self.dir_edit.text()
        if not current_dir and self.current_file:
            current_dir = os.path.dirname(self.current_file)
        if not current_dir:
            current_dir = os.path.expanduser("~")

        directory = QFileDialog.getExistingDirectory(
            self, "Select Input Directory", current_dir
        )

        if directory:
            self.dir_edit.setText(directory)

            # Set output directory to input_dir/hpc/ by default
            if not self.output_dir_edit.text() or self.output_dir_edit.text().endswith(
                "/hpc"
            ):
                self.output_dir_edit.setText(os.path.join(directory, "hpc"))

            # Preview files if pattern is already set
            self.preview_files()

    def browse_output_directory(self):
        """Browse for output directory"""
        current_dir = self.output_dir_edit.text()
        if not current_dir:
            current_dir = self.dir_edit.text()
        if not current_dir and self.current_file:
            current_dir = os.path.dirname(self.current_file)
        if not current_dir:
            current_dir = os.path.expanduser("~")

        directory = QFileDialog.getExistingDirectory(
            self, "Select Output Directory", current_dir
        )

        if directory:
            self.output_dir_edit.setText(directory)

    def update_stokes_mode(self):
        """Update UI based on selected Stokes mode"""
        single_stokes = self.single_stokes_radio.isChecked()
        self.stokes_combo.setEnabled(single_stokes)

    def scan_files(self):
        """Scan for matching files and show count (without opening preview)"""
        input_dir = self.dir_edit.text()
        pattern = self.pattern_edit.text()

        if not input_dir:
            self.files_count_label.setText("⚠️ Select a directory first")
            return

        try:
            input_pattern = os.path.join(input_dir, pattern)
            matching_files = glob.glob(input_pattern)
            count = len(matching_files)

            if count == 0:
                self.files_count_label.setText("⚠️ No files found")
                self.status_text.setPlainText(f"No files found matching: {pattern}")
            else:
                self.files_count_label.setText(
                    f"✓ {count} file{'s' if count != 1 else ''} found"
                )
                self.status_text.setPlainText(
                    f"Found {count} files matching the pattern."
                )
        except Exception as e:
            self.files_count_label.setText(f"⚠️ Error: {str(e)}")

    def preview_files(self):
        """Show files that match the pattern in a popup dialog"""
        input_dir = self.dir_edit.text()
        pattern = self.pattern_edit.text()

        if not input_dir:
            QMessageBox.warning(
                self, "No Directory", "Please select an input directory first."
            )
            return

        try:
            # Get matching files
            input_pattern = os.path.join(input_dir, pattern)
            matching_files = sorted(glob.glob(input_pattern))

            if not matching_files:
                QMessageBox.information(
                    self,
                    "No Files Found",
                    f"No files found matching pattern:\n{input_pattern}",
                )
                return

            # Show files in a popup dialog
            preview_dialog = QDialog(self)
            preview_dialog.setWindowTitle(f"Preview: {len(matching_files)} files")
            preview_dialog.setMinimumSize(500, 400)

            layout = QVBoxLayout(preview_dialog)
            layout.setSpacing(12)
            layout.setContentsMargins(16, 16, 16, 16)

            info_label = QLabel(
                f"Found <b>{len(matching_files)}</b> files matching <code>{pattern}</code>"
            )
            layout.addWidget(info_label)

            file_list = QListWidget()
            file_list.setAlternatingRowColors(True)
            for file_path in matching_files:
                basename = os.path.basename(file_path)
                item = QListWidgetItem(basename)
                item.setToolTip(file_path)
                file_list.addItem(item)
            layout.addWidget(file_list, 1)

            close_btn = QPushButton("Close")
            close_btn.clicked.connect(preview_dialog.accept)
            btn_layout = QHBoxLayout()
            btn_layout.addStretch()
            btn_layout.addWidget(close_btn)
            layout.addLayout(btn_layout)

            preview_dialog.exec_()

            self.status_text.setPlainText(
                f"Found {len(matching_files)} files matching the pattern."
            )

        except Exception as e:
            self.status_text.setPlainText(f"Error previewing files: {str(e)}")
            traceback.print_exc()

    def _get_matching_files(self):
        """Get list of files matching the current pattern"""
        input_dir = self.dir_edit.text()
        pattern = self.pattern_edit.text()

        if not input_dir:
            return []

        input_pattern = os.path.join(input_dir, pattern)
        return sorted(glob.glob(input_pattern))

    def convert_files(self):
        """Convert the selected files to helioprojective coordinates"""
        # Get input files from pattern
        files_to_process = self._get_matching_files()

        if not files_to_process:
            QMessageBox.warning(
                self,
                "No Files Found",
                "No files match the pattern. Please check your input settings.",
            )
            return

        self.status_text.appendPlainText(f"Processing {len(files_to_process)} files.")

        # Get output directory and pattern
        output_dir = self.output_dir_edit.text()
        output_pattern = self.output_pattern_edit.text()

        if not output_dir:
            QMessageBox.warning(
                self,
                "Output Directory Missing",
                "Please specify an output directory.",
            )
            return

        # Create output directory if it doesn't exist
        if not os.path.exists(output_dir):
            try:
                os.makedirs(output_dir)
                self.status_text.appendPlainText(
                    f"Created output directory: {output_dir}"
                )
            except OSError as e:
                QMessageBox.critical(
                    self,
                    "Cannot Create Directory",
                    f"Failed to create output directory:\n{output_dir}\n\nError: {e}",
                )
                return

        # Get processing options
        use_multiprocessing = self.multiprocessing_check.isChecked()
        max_cores = self.cores_spinbox.value() if use_multiprocessing else 1
        full_stokes = self.full_stokes_radio.isChecked()
        stokes_param = self.stokes_combo.currentText() if not full_stokes else None

        # Import modules needed for processing
        import multiprocessing
        import time
        from .helioprojective import convert_and_save_hpc

        # Setup Progress UI
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.ok_button.setEnabled(False)
        self.tab_widget.setEnabled(False)
        try:
            self.cancel_button.clicked.disconnect()
        except:
            pass
        self.cancel_button.clicked.connect(self._cancel_processing)
        self.cancel_button.setEnabled(True)
        self._processing_cancelled = False

        QApplication.processEvents()

        # Use a worker thread or process for conversion
        try:
            self.status_text.appendPlainText("Starting batch conversion...")

            # Initialize counters
            success_count = 0
            error_count = 0
            completed_count = 0
            pool = None
            results = []

            # Multi-stokes requires different handling
            if full_stokes:
                stokes_list = ["I", "Q", "U", "V"]

                if use_multiprocessing and len(files_to_process) > 1:
                    # Prepare arguments for multiprocessing
                    self.status_text.appendPlainText(
                        f"Using multiprocessing with {max_cores} cores"
                    )

                    # Create task list - each task is (input_file, output_path, stokes, process_id)
                    tasks = []
                    for i, input_file in enumerate(files_to_process):
                        base_filename = os.path.basename(input_file)
                        process_id = i  # Use file index as part of process ID

                        if "*" in output_pattern:
                            output_filename = output_pattern.replace(
                                "*", os.path.splitext(base_filename)[0]
                            )
                        else:
                            output_filename = (
                                f"{os.path.splitext(base_filename)[0]}_{output_pattern}"
                            )

                        output_path = os.path.join(output_dir, output_filename)

                        # Create tasks for each stokes parameter
                        for stokes in stokes_list:
                            stokes_output = output_path.replace(
                                ".fits", f"_{stokes}.fits"
                            )
                            task = (
                                input_file,
                                stokes_output,
                                stokes,
                                f"{process_id}_{stokes}",
                            )
                            tasks.append(task)

                    # Set up progress tracking
                    total_tasks = len(tasks)
                    self.progress_bar.setRange(0, total_tasks)

                    # Create process pool and start processing
                    pool = multiprocessing.Pool(processes=max_cores)

                    # Start asynchronous processing with our standalone function
                    result_objects = pool.map_async(process_single_file_hpc, tasks)
                    pool.close()  # No more tasks will be submitted

                    # Monitor progress while processing
                    while not result_objects.ready():
                        if self._processing_cancelled:
                            pool.terminate()
                            self.status_text.appendPlainText(
                                "Operation canceled by user."
                            )
                            break
                        time.sleep(0.1)  # Short sleep to prevent UI blocking
                        QApplication.processEvents()

                    # Get results if not canceled
                    if not self._processing_cancelled:
                        results = result_objects.get()

                        # Process results
                        file_results = {}  # Group results by input file

                        for result in results:
                            input_file = result["input_file"]
                            basename = os.path.basename(input_file)

                            if basename not in file_results:
                                file_results[basename] = {"success": 0, "errors": []}

                            if result["success"]:
                                file_results[basename]["success"] += 1
                                self.status_text.appendPlainText(
                                    f"  - Stokes {result['stokes']}: Converted successfully"
                                )
                            else:
                                error_msg = result["error"] or "Unknown error"
                                file_results[basename]["errors"].append(
                                    f"Stokes {result['stokes']}: {error_msg}"
                                )
                                self.status_text.appendPlainText(
                                    f"  - Stokes {result['stokes']}: Error: {error_msg}"
                                )

                        # Count overall successes
                        for basename, res in file_results.items():
                            if res["success"] == len(stokes_list):
                                success_count += 1
                            elif res["success"] > 0:
                                success_count += 0.5  # Partial success
                                error_count += 0.5
                            else:
                                error_count += 1

                            # Log each file's summary
                            self.status_text.appendPlainText(
                                f"File {basename}: {res['success']}/{len(stokes_list)} stokes parameters processed successfully"
                            )
                            if res["errors"]:
                                for err in res["errors"]:
                                    self.status_text.appendPlainText(
                                        f"  - Error: {err}"
                                    )

                        # Update progress to completion
                        self.progress_bar.setValue(total_tasks)
                else:
                    # Sequential processing for multi-stokes
                    for i, input_file in enumerate(files_to_process):
                        # Check if canceled
                        if self._processing_cancelled:
                            self.status_text.appendPlainText(
                                "Operation canceled by user."
                            )
                            break

                        # Get output filename
                        base_filename = os.path.basename(input_file)
                        if "*" in output_pattern:
                            output_filename = output_pattern.replace(
                                "*", os.path.splitext(base_filename)[0]
                            )
                        else:
                            output_filename = (
                                f"{os.path.splitext(base_filename)[0]}_{output_pattern}"
                            )

                        output_path = os.path.join(output_dir, output_filename)

                        # Update progress dialog
                        self.progress_bar.setValue(i)

                        QApplication.processEvents()

                        self.status_text.appendPlainText(
                            f"Processing {i+1}/{len(files_to_process)}: {base_filename}"
                        )

                        stokes_success = 0
                        for stokes in stokes_list:
                            # Create stokes-specific output filename
                            stokes_output = output_path.replace(
                                ".fits", f"_{stokes}.fits"
                            )

                            try:
                                # Convert file with a unique temp suffix
                                temp_suffix = f"_seq_{i}_{stokes}"
                                result = process_single_file_hpc(
                                    (
                                        input_file,
                                        stokes_output,
                                        stokes,
                                        f"_seq_{i}_{stokes}",
                                    )
                                )
                                success = result["success"]

                                if success:
                                    stokes_success += 1
                                    self.status_text.appendPlainText(
                                        f"  - Stokes {stokes}: Converted successfully"
                                    )
                                else:
                                    self.status_text.appendPlainText(
                                        f"  - Stokes {stokes}: Conversion failed"
                                    )

                            except Exception as e:
                                self.status_text.appendPlainText(
                                    f"  - Stokes {stokes}: Error: {str(e)}"
                                )

                        if stokes_success == len(stokes_list):
                            success_count += 1
                        elif stokes_success > 0:
                            success_count += 0.5  # Partial success
                            error_count += 0.5
                        else:
                            error_count += 1
            else:
                # Single stokes processing
                if use_multiprocessing and len(files_to_process) > 1:
                    # Prepare arguments for multiprocessing
                    self.status_text.appendPlainText(
                        f"Using multiprocessing with {max_cores} cores"
                    )

                    # Create task list - each task is (input_file, output_path, stokes, process_id)
                    tasks = []
                    for i, input_file in enumerate(files_to_process):
                        base_filename = os.path.basename(input_file)

                        if "*" in output_pattern:
                            output_filename = output_pattern.replace(
                                "*", os.path.splitext(base_filename)[0]
                            )
                        else:
                            output_filename = (
                                f"{os.path.splitext(base_filename)[0]}_{output_pattern}"
                            )

                        output_path = os.path.join(output_dir, output_filename)
                        task = (input_file, output_path, stokes_param, i)
                        tasks.append(task)

                    # Set up progress tracking
                    total_tasks = len(tasks)
                    self.progress_bar.setRange(0, total_tasks)

                    # Create process pool
                    pool = multiprocessing.Pool(processes=max_cores)

                    # Start asynchronous processing
                    result_objects = pool.map_async(process_single_file_hpc, tasks)
                    pool.close()  # No more tasks will be submitted

                    # Monitor progress while processing
                    while not result_objects.ready():
                        if self._processing_cancelled:
                            pool.terminate()
                            self.status_text.appendPlainText(
                                "Operation canceled by user."
                            )
                            break
                        time.sleep(0.1)  # Short sleep to prevent UI blocking
                        QApplication.processEvents()

                    # Process results if not canceled
                    if not self._processing_cancelled:
                        results = result_objects.get()

                        # Process results
                        for result in results:
                            basename = os.path.basename(result["input_file"])

                            if result["success"]:
                                success_count += 1
                                self.status_text.appendPlainText(
                                    f"  - {basename}: Converted successfully"
                                )
                            else:
                                error_count += 1
                                error_msg = result["error"] or "Unknown error"
                                self.status_text.appendPlainText(
                                    f"  - {basename}: Error: {error_msg}"
                                )

                        # Update progress to completion
                        self.progress_bar.setValue(total_tasks)
                else:
                    # Sequential processing for single stokes
                    for i, input_file in enumerate(files_to_process):
                        # Check if canceled
                        if self._processing_cancelled:
                            self.status_text.appendPlainText(
                                "Operation canceled by user."
                            )
                            break

                        # Get output filename
                        base_filename = os.path.basename(input_file)
                        if "*" in output_pattern:
                            output_filename = output_pattern.replace(
                                "*", os.path.splitext(base_filename)[0]
                            )
                        else:
                            output_filename = (
                                f"{os.path.splitext(base_filename)[0]}_{output_pattern}"
                            )

                        output_path = os.path.join(output_dir, output_filename)

                        # Update progress dialog
                        self.progress_bar.setValue(i)

                        QApplication.processEvents()

                        self.status_text.appendPlainText(
                            f"Processing {i+1}/{len(files_to_process)}: {base_filename}"
                        )

                        try:
                            # Convert file with a unique temp suffix
                            temp_suffix = f"_seq_{i}"
                            result = process_single_file_hpc(
                                (input_file, output_path, stokes_param, f"_seq_{i}")
                            )
                            success = result["success"]

                            if success:
                                success_count += 1
                                self.status_text.appendPlainText(
                                    "  - Converted successfully"
                                )
                            else:
                                error_count += 1
                                self.status_text.appendPlainText(
                                    "  - Conversion failed"
                                )

                        except Exception as e:
                            error_count += 1
                            self.status_text.appendPlainText(f"  - Error: {str(e)}")

            # Complete the progress
            self.progress_bar.setValue(self.progress_bar.maximum())

            # Show completion message
            summary = (
                f"Batch conversion completed:\n"
                f"Total files: {len(files_to_process)}\n"
                f"Successfully converted: {success_count}\n"
                f"Failed: {error_count}"
            )

            self.status_text.appendPlainText("\n" + summary)
            QMessageBox.information(self, "Conversion Complete", summary)

        except Exception as e:
            self.status_text.appendPlainText(f"Error in batch processing: {str(e)}")
            self.status_text.appendPlainText(traceback.format_exc())
            QMessageBox.critical(self, "Error", f"Error in batch processing: {str(e)}")
        finally:
            # Clean up multiprocessing pool if it exists
            if pool is not None:
                pool.terminate()
                pool.join()

            # Close progress dialog and re-enable button
            self.progress_bar.setVisible(False)
            self.ok_button.setEnabled(True)
            self.tab_widget.setEnabled(True)

            # Reconnect cancel button to reject
            try:
                self.cancel_button.clicked.disconnect()
            except:
                pass
            self.cancel_button.clicked.connect(self.reject)

    def _cancel_processing(self):
        """Signal processing cancellation"""
        self._processing_cancelled = True
        self.status_text.appendPlainText("Stopping... please wait.")
        self.cancel_button.setEnabled(False)


class PlotCustomizationDialog(QDialog):
    """Dialog for customizing plot appearance (labels, fonts, colors)."""

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self.setWindowTitle("Plot Customization")
        self.setMinimumWidth(660)
        self.setMaximumHeight(1280)
        self.settings = settings.copy() if settings else {}
        self.setup_ui()
        set_hand_cursor(self)

    def setup_ui(self):
        from PyQt5.QtWidgets import QTabWidget

        outer_layout = QVBoxLayout(self)
        outer_layout.setSpacing(8)
        outer_layout.setContentsMargins(10, 10, 10, 10)

        # Create tab widget for organized sections
        tab_widget = QTabWidget()

        # ===== TAB 1: TEXT & LABELS =====
        text_tab = QWidget()
        text_layout = QVBoxLayout(text_tab)
        text_layout.setSpacing(8)

        # Labels Section
        labels_group = QGroupBox("Labels")
        labels_layout = QGridLayout(labels_group)
        labels_layout.setSpacing(8)

        labels_layout.addWidget(QLabel("X-Axis:"), 0, 0)
        self.xlabel_edit = QLineEdit(self.settings.get("xlabel", ""))
        self.xlabel_edit.setPlaceholderText("Auto")
        labels_layout.addWidget(self.xlabel_edit, 0, 1)

        labels_layout.addWidget(QLabel("Y-Axis:"), 0, 2)
        self.ylabel_edit = QLineEdit(self.settings.get("ylabel", ""))
        self.ylabel_edit.setPlaceholderText("Auto")
        labels_layout.addWidget(self.ylabel_edit, 0, 3)

        labels_layout.addWidget(QLabel("Title:"), 1, 0)
        self.title_edit = QLineEdit(self.settings.get("title", ""))
        self.title_edit.setPlaceholderText("Auto")
        labels_layout.addWidget(self.title_edit, 1, 1)

        labels_layout.addWidget(QLabel("Colorbar:"), 1, 2)
        self.colorbar_label_edit = QLineEdit(self.settings.get("colorbar_label", ""))
        self.colorbar_label_edit.setPlaceholderText("e.g., Jy/beam")
        labels_layout.addWidget(self.colorbar_label_edit, 1, 3)

        text_layout.addWidget(labels_group)

        # Font Sizes Section (compact grid)
        fonts_group = QGroupBox("Font Sizes")
        fonts_layout = QGridLayout(fonts_group)
        fonts_layout.setSpacing(8)

        fonts_layout.addWidget(QLabel("Axis Labels:"), 0, 0)
        self.axis_label_size = QSpinBox()
        self.axis_label_size.setRange(1, 50)
        self.axis_label_size.setValue(self.settings.get("axis_label_fontsize", 12))
        fonts_layout.addWidget(self.axis_label_size, 0, 1)

        fonts_layout.addWidget(QLabel("Axis Ticks:"), 0, 2)
        self.axis_tick_size = QSpinBox()
        self.axis_tick_size.setRange(1, 50)
        self.axis_tick_size.setValue(self.settings.get("axis_tick_fontsize", 10))
        fonts_layout.addWidget(self.axis_tick_size, 0, 3)

        fonts_layout.addWidget(QLabel("Title:"), 1, 0)
        self.title_size = QSpinBox()
        self.title_size.setRange(1, 50)
        self.title_size.setValue(self.settings.get("title_fontsize", 12))
        fonts_layout.addWidget(self.title_size, 1, 1)

        fonts_layout.addWidget(QLabel("Colorbar:"), 1, 2)
        self.colorbar_label_size = QSpinBox()
        self.colorbar_label_size.setRange(1, 50)
        self.colorbar_label_size.setValue(
            self.settings.get("colorbar_label_fontsize", 10)
        )
        fonts_layout.addWidget(self.colorbar_label_size, 1, 3)

        fonts_layout.addWidget(QLabel("Colorbar Ticks:"), 2, 0)
        self.colorbar_tick_size = QSpinBox()
        self.colorbar_tick_size.setRange(1, 50)
        self.colorbar_tick_size.setValue(
            self.settings.get("colorbar_tick_fontsize", 10)
        )
        fonts_layout.addWidget(self.colorbar_tick_size, 2, 1)

        # Scale buttons
        scale_layout = QHBoxLayout()
        scale_layout.addWidget(QLabel("Scale All:"))
        scale_down_btn = QPushButton("-")
        scale_down_btn.setFixedWidth(30)
        scale_down_btn.clicked.connect(self._scale_fonts_down)
        scale_up_btn = QPushButton("+")
        scale_up_btn.setFixedWidth(30)
        scale_up_btn.clicked.connect(self._scale_fonts_up)
        scale_layout.addWidget(scale_down_btn)
        scale_layout.addWidget(scale_up_btn)
        scale_layout.addStretch()
        fonts_layout.addLayout(scale_layout, 2, 2, 1, 2)

        text_layout.addWidget(fonts_group)
        text_layout.addStretch()

        tab_widget.addTab(text_tab, "Text")

        # ===== TAB 2: COLORS & STYLE =====
        style_tab = QWidget()
        style_layout = QVBoxLayout(style_tab)
        style_layout.setSpacing(8)

        # Colors Section (compact 2-column grid)
        colors_group = QGroupBox("Colors")
        colors_layout = QGridLayout(colors_group)
        colors_layout.setSpacing(6)

        # Row 0: Plot BG, Figure BG
        colors_layout.addWidget(QLabel("Plot BG:"), 0, 0)
        self.plot_bg_color = self.settings.get("plot_bg_color", "auto")
        self.plot_bg_preview = QLabel()
        self.plot_bg_preview.setFixedSize(20, 20)
        self._update_color_preview(self.plot_bg_preview, self.plot_bg_color)
        self.plot_bg_btn = QPushButton("...")
        self.plot_bg_btn.setFixedWidth(30)
        self.plot_bg_btn.clicked.connect(self._pick_plot_bg_color)
        self.plot_bg_auto_btn = QPushButton("A")
        self.plot_bg_auto_btn.setFixedWidth(25)
        self.plot_bg_auto_btn.setToolTip("Auto")
        self.plot_bg_auto_btn.clicked.connect(lambda: self._set_plot_bg_auto())
        plot_bg_row = QHBoxLayout()
        plot_bg_row.addWidget(self.plot_bg_preview)
        plot_bg_row.addWidget(self.plot_bg_btn)
        plot_bg_row.addWidget(self.plot_bg_auto_btn)
        colors_layout.addLayout(plot_bg_row, 0, 1)

        colors_layout.addWidget(QLabel("Figure BG:"), 0, 2)
        self.figure_bg_color = self.settings.get("figure_bg_color", "auto")
        self.figure_bg_preview = QLabel()
        self.figure_bg_preview.setFixedSize(20, 20)
        self._update_color_preview(self.figure_bg_preview, self.figure_bg_color)
        self.figure_bg_btn = QPushButton("...")
        self.figure_bg_btn.setFixedWidth(30)
        self.figure_bg_btn.clicked.connect(self._pick_figure_bg_color)
        self.figure_bg_auto_btn = QPushButton("A")
        self.figure_bg_auto_btn.setFixedWidth(25)
        self.figure_bg_auto_btn.setToolTip("Auto")
        self.figure_bg_auto_btn.clicked.connect(lambda: self._set_figure_bg_auto())
        figure_bg_row = QHBoxLayout()
        figure_bg_row.addWidget(self.figure_bg_preview)
        figure_bg_row.addWidget(self.figure_bg_btn)
        figure_bg_row.addWidget(self.figure_bg_auto_btn)
        colors_layout.addLayout(figure_bg_row, 0, 3)

        # Row 1: Text, Tick
        colors_layout.addWidget(QLabel("Text:"), 1, 0)
        self.text_color = self.settings.get("text_color", "auto")
        self.text_color_preview = QLabel()
        self.text_color_preview.setFixedSize(20, 20)
        self._update_color_preview(self.text_color_preview, self.text_color)
        self.text_color_btn = QPushButton("...")
        self.text_color_btn.setFixedWidth(30)
        self.text_color_btn.clicked.connect(self._pick_text_color)
        self.text_color_auto_btn = QPushButton("A")
        self.text_color_auto_btn.setFixedWidth(25)
        self.text_color_auto_btn.setToolTip("Auto")
        self.text_color_auto_btn.clicked.connect(self._set_text_color_auto)
        text_color_row = QHBoxLayout()
        text_color_row.addWidget(self.text_color_preview)
        text_color_row.addWidget(self.text_color_btn)
        text_color_row.addWidget(self.text_color_auto_btn)
        colors_layout.addLayout(text_color_row, 1, 1)

        colors_layout.addWidget(QLabel("Ticks:"), 1, 2)
        self.tick_color = self.settings.get("tick_color", "auto")
        self.tick_color_preview = QLabel()
        self.tick_color_preview.setFixedSize(20, 20)
        self._update_color_preview(self.tick_color_preview, self.tick_color)
        self.tick_color_btn = QPushButton("...")
        self.tick_color_btn.setFixedWidth(30)
        self.tick_color_btn.clicked.connect(self._pick_tick_color)
        self.tick_color_auto_btn = QPushButton("A")
        self.tick_color_auto_btn.setFixedWidth(25)
        self.tick_color_auto_btn.setToolTip("Auto")
        self.tick_color_auto_btn.clicked.connect(self._set_tick_color_auto)
        tick_color_row = QHBoxLayout()
        tick_color_row.addWidget(self.tick_color_preview)
        tick_color_row.addWidget(self.tick_color_btn)
        tick_color_row.addWidget(self.tick_color_auto_btn)
        colors_layout.addLayout(tick_color_row, 1, 3)

        # Row 2: Border color + width
        colors_layout.addWidget(QLabel("Border:"), 2, 0)
        self.border_color = self.settings.get("border_color", "auto")
        self.border_color_preview = QLabel()
        self.border_color_preview.setFixedSize(20, 20)
        self._update_color_preview(self.border_color_preview, self.border_color)
        self.border_color_btn = QPushButton("...")
        self.border_color_btn.setFixedWidth(30)
        self.border_color_btn.clicked.connect(self._pick_border_color)
        self.border_color_auto_btn = QPushButton("A")
        self.border_color_auto_btn.setFixedWidth(25)
        self.border_color_auto_btn.setToolTip("Auto")
        self.border_color_auto_btn.clicked.connect(self._set_border_color_auto)
        border_color_row = QHBoxLayout()
        border_color_row.addWidget(self.border_color_preview)
        border_color_row.addWidget(self.border_color_btn)
        border_color_row.addWidget(self.border_color_auto_btn)
        colors_layout.addLayout(border_color_row, 2, 1)

        colors_layout.addWidget(QLabel("Border Width:"), 2, 2)
        self.border_width = QDoubleSpinBox()
        self.border_width.setRange(0.5, 5.0)
        self.border_width.setSingleStep(0.5)
        self.border_width.setValue(self.settings.get("border_width", 1.0))
        colors_layout.addWidget(self.border_width, 2, 3)

        style_layout.addWidget(colors_group)

        # Tick Marks Section (compact)
        ticks_group = QGroupBox("Tick Marks")
        ticks_layout = QGridLayout(ticks_group)
        ticks_layout.setSpacing(8)

        ticks_layout.addWidget(QLabel("Direction:"), 0, 0)
        self.tick_direction = QComboBox()
        self.tick_direction.addItems(["in", "out"])
        self.tick_direction.setCurrentText(self.settings.get("tick_direction", "out"))
        ticks_layout.addWidget(self.tick_direction, 0, 1)

        ticks_layout.addWidget(QLabel("Length:"), 0, 2)
        self.tick_length = QSpinBox()
        self.tick_length.setRange(1, 20)
        self.tick_length.setValue(self.settings.get("tick_length", 4))
        ticks_layout.addWidget(self.tick_length, 0, 3)

        ticks_layout.addWidget(QLabel("Width:"), 1, 0)
        self.tick_width = QDoubleSpinBox()
        self.tick_width.setRange(0.5, 5.0)
        self.tick_width.setSingleStep(0.5)
        self.tick_width.setValue(self.settings.get("tick_width", 1.0))
        ticks_layout.addWidget(self.tick_width, 1, 1)

        style_layout.addWidget(ticks_group)
        style_layout.addStretch()

        tab_widget.addTab(style_tab, "Style")

        # ===== TAB 3: PADDING =====
        padding_tab = QWidget()
        padding_layout = QVBoxLayout(padding_tab)
        padding_layout.setSpacing(8)

        # Subplot Margins Section
        margins_group = QGroupBox("Plot Margins (0.0 - 1.0)")
        margins_layout = QGridLayout(margins_group)
        margins_layout.setSpacing(10)

        # Left
        margins_layout.addWidget(QLabel("Left:"), 0, 0)
        self.pad_left = QDoubleSpinBox()
        self.pad_left.setRange(0.0, 0.5)
        self.pad_left.setSingleStep(0.01)
        self.pad_left.setDecimals(2)
        self.pad_left.setValue(self.settings.get("pad_left", 0.135))
        margins_layout.addWidget(self.pad_left, 0, 1)

        # Right
        margins_layout.addWidget(QLabel("Right:"), 0, 2)
        self.pad_right = QDoubleSpinBox()
        self.pad_right.setRange(0.5, 1.0)
        self.pad_right.setSingleStep(0.01)
        self.pad_right.setDecimals(2)
        self.pad_right.setValue(self.settings.get("pad_right", 1.0))
        margins_layout.addWidget(self.pad_right, 0, 3)

        # Top
        margins_layout.addWidget(QLabel("Top:"), 1, 0)
        self.pad_top = QDoubleSpinBox()
        self.pad_top.setRange(0.5, 1.0)
        self.pad_top.setSingleStep(0.01)
        self.pad_top.setDecimals(2)
        self.pad_top.setValue(self.settings.get("pad_top", 0.95))
        margins_layout.addWidget(self.pad_top, 1, 1)

        # Bottom
        margins_layout.addWidget(QLabel("Bottom:"), 1, 2)
        self.pad_bottom = QDoubleSpinBox()
        self.pad_bottom.setRange(0.0, 0.5)
        self.pad_bottom.setSingleStep(0.01)
        self.pad_bottom.setDecimals(2)
        self.pad_bottom.setValue(self.settings.get("pad_bottom", 0.05))
        margins_layout.addWidget(self.pad_bottom, 1, 3)

        # Wspace (width space between subplots)
        margins_layout.addWidget(QLabel("Wspace:"), 2, 0)
        self.pad_wspace = QDoubleSpinBox()
        self.pad_wspace.setRange(0.0, 0.5)
        self.pad_wspace.setSingleStep(0.01)
        self.pad_wspace.setDecimals(2)
        self.pad_wspace.setValue(self.settings.get("pad_wspace", 0.2))
        self.pad_wspace.setToolTip("Width space between subplots")
        margins_layout.addWidget(self.pad_wspace, 2, 1)

        # Hspace (height space between subplots)
        margins_layout.addWidget(QLabel("Hspace:"), 2, 2)
        self.pad_hspace = QDoubleSpinBox()
        self.pad_hspace.setRange(0.0, 0.5)
        self.pad_hspace.setSingleStep(0.01)
        self.pad_hspace.setDecimals(2)
        self.pad_hspace.setValue(self.settings.get("pad_hspace", 0.2))
        self.pad_hspace.setToolTip("Height space between subplots")
        margins_layout.addWidget(self.pad_hspace, 2, 3)

        padding_layout.addWidget(margins_group)

        # Tight Layout Option
        tight_group = QGroupBox("Layout Options")
        tight_layout_grid = QGridLayout(tight_group)

        self.use_tight_layout = QCheckBox("Use Tight Layout")
        self.use_tight_layout.setChecked(self.settings.get("use_tight_layout", False))
        self.use_tight_layout.setToolTip("Automatically adjust margins for best fit")
        tight_layout_grid.addWidget(self.use_tight_layout, 0, 0)

        padding_layout.addWidget(tight_group)
        padding_layout.addStretch()

        tab_widget.addTab(padding_tab, "Padding")

        outer_layout.addWidget(tab_widget)

        # Bottom buttons row
        buttons_layout = QHBoxLayout()
        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.clicked.connect(self.reset_to_defaults)
        buttons_layout.addWidget(reset_btn)
        buttons_layout.addStretch()

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        buttons_layout.addWidget(button_box)

        outer_layout.addLayout(buttons_layout)

    def _update_color_preview(self, label, color):
        """Update the color preview label."""
        if color == "auto" or color == "transparent":
            label.setStyleSheet("background-color: #888888; border: 1px solid #555555;")
            label.setText("A" if color == "auto" else "T")
            label.setAlignment(Qt.AlignCenter)
        else:
            label.setStyleSheet(
                f"background-color: {color}; border: 1px solid #555555;"
            )
            label.setText("")

    def _pick_plot_bg_color(self):
        """Open color picker for plot background."""
        from PyQt5.QtWidgets import QColorDialog, QPushButton
        from PyQt5.QtGui import QColor

        initial = (
            QColor(self.plot_bg_color)
            if self.plot_bg_color not in ("auto", "transparent")
            else QColor("#ffffff")
        )
        dialog = QColorDialog(initial, self)
        dialog.setWindowTitle("Select Plot Background Color")
        dialog.setOption(QColorDialog.DontUseNativeDialog, True)

        # Hide the "Pick Screen Color" button
        for btn in dialog.findChildren(QPushButton):
            if "pick" in btn.text().lower() or "screen" in btn.text().lower():
                btn.hide()

        if dialog.exec_() == QColorDialog.Accepted:
            self.plot_bg_color = dialog.selectedColor().name()
            self._update_color_preview(self.plot_bg_preview, self.plot_bg_color)

    def _pick_figure_bg_color(self):
        """Open color picker for figure background."""
        from PyQt5.QtWidgets import QColorDialog, QPushButton
        from PyQt5.QtGui import QColor

        initial = (
            QColor(self.figure_bg_color)
            if self.figure_bg_color not in ("auto", "transparent")
            else QColor("#ffffff")
        )
        dialog = QColorDialog(initial, self)
        dialog.setWindowTitle("Select Figure Background Color")
        dialog.setOption(QColorDialog.DontUseNativeDialog, True)

        # Hide the "Pick Screen Color" button
        for btn in dialog.findChildren(QPushButton):
            if "pick" in btn.text().lower() or "screen" in btn.text().lower():
                btn.hide()

        if dialog.exec_() == QColorDialog.Accepted:
            self.figure_bg_color = dialog.selectedColor().name()
            self._update_color_preview(self.figure_bg_preview, self.figure_bg_color)

    def _set_plot_bg_auto(self):
        """Set plot background to auto."""
        self.plot_bg_color = "auto"
        self._update_color_preview(self.plot_bg_preview, "auto")

    def _set_figure_bg_auto(self):
        """Set figure background to auto."""
        self.figure_bg_color = "auto"
        self._update_color_preview(self.figure_bg_preview, "auto")

    def _pick_text_color(self):
        """Open color picker for text color."""
        from PyQt5.QtWidgets import QColorDialog, QPushButton
        from PyQt5.QtGui import QColor

        initial = (
            QColor(self.text_color)
            if self.text_color not in ("auto", "transparent")
            else QColor("#ffffff")
        )
        dialog = QColorDialog(initial, self)
        dialog.setWindowTitle("Select Text Color")
        dialog.setOption(QColorDialog.DontUseNativeDialog, True)

        # Hide the "Pick Screen Color" button
        for btn in dialog.findChildren(QPushButton):
            if "pick" in btn.text().lower() or "screen" in btn.text().lower():
                btn.hide()

        if dialog.exec_() == QColorDialog.Accepted:
            self.text_color = dialog.selectedColor().name()
            self._update_color_preview(self.text_color_preview, self.text_color)

    def _set_text_color_auto(self):
        """Set text color to auto."""
        self.text_color = "auto"
        self._update_color_preview(self.text_color_preview, "auto")

    def _pick_tick_color(self):
        """Open color picker for tick color."""
        from PyQt5.QtWidgets import QColorDialog, QPushButton
        from PyQt5.QtGui import QColor

        initial = (
            QColor(self.tick_color)
            if self.tick_color not in ("auto", "transparent")
            else QColor("#ffffff")
        )
        dialog = QColorDialog(initial, self)
        dialog.setWindowTitle("Select Tick Color")
        dialog.setOption(QColorDialog.DontUseNativeDialog, True)

        # Hide the "Pick Screen Color" button
        for btn in dialog.findChildren(QPushButton):
            if "pick" in btn.text().lower() or "screen" in btn.text().lower():
                btn.hide()

        if dialog.exec_() == QColorDialog.Accepted:
            self.tick_color = dialog.selectedColor().name()
            self._update_color_preview(self.tick_color_preview, self.tick_color)

    def _set_tick_color_auto(self):
        """Set tick color to auto."""
        self.tick_color = "auto"
        self._update_color_preview(self.tick_color_preview, "auto")

    def _pick_border_color(self):
        """Open color picker for border color."""
        from PyQt5.QtWidgets import QColorDialog, QPushButton
        from PyQt5.QtGui import QColor

        initial = (
            QColor(self.border_color)
            if self.border_color not in ("auto", "transparent")
            else QColor("#ffffff")
        )
        dialog = QColorDialog(initial, self)
        dialog.setWindowTitle("Select Border Color")
        dialog.setOption(QColorDialog.DontUseNativeDialog, True)

        # Hide the "Pick Screen Color" button
        for btn in dialog.findChildren(QPushButton):
            if "pick" in btn.text().lower() or "screen" in btn.text().lower():
                btn.hide()

        if dialog.exec_() == QColorDialog.Accepted:
            self.border_color = dialog.selectedColor().name()
            self._update_color_preview(self.border_color_preview, self.border_color)

    def _set_border_color_auto(self):
        """Set border color to auto."""
        self.border_color = "auto"
        self._update_color_preview(self.border_color_preview, "auto")

    def _scale_fonts_up(self):
        """Increase all font sizes by 1."""
        self.axis_label_size.setValue(min(self.axis_label_size.value() + 1, 28))
        self.axis_tick_size.setValue(min(self.axis_tick_size.value() + 1, 24))
        self.title_size.setValue(min(self.title_size.value() + 1, 32))
        self.colorbar_label_size.setValue(min(self.colorbar_label_size.value() + 1, 24))
        self.colorbar_tick_size.setValue(min(self.colorbar_tick_size.value() + 1, 20))

    def _scale_fonts_down(self):
        """Decrease all font sizes by 1."""
        self.axis_label_size.setValue(max(self.axis_label_size.value() - 1, 6))
        self.axis_tick_size.setValue(max(self.axis_tick_size.value() - 1, 6))
        self.title_size.setValue(max(self.title_size.value() - 1, 8))
        self.colorbar_label_size.setValue(max(self.colorbar_label_size.value() - 1, 6))
        self.colorbar_tick_size.setValue(max(self.colorbar_tick_size.value() - 1, 6))

    def reset_to_defaults(self):
        """Reset all settings to defaults."""
        self.xlabel_edit.clear()
        self.ylabel_edit.clear()
        self.title_edit.clear()
        self.colorbar_label_edit.clear()
        self.axis_label_size.setValue(12)
        self.axis_tick_size.setValue(10)
        self.title_size.setValue(12)
        self.colorbar_label_size.setValue(10)
        self.colorbar_tick_size.setValue(10)
        self.plot_bg_color = "auto"
        self.figure_bg_color = "auto"
        self.text_color = "auto"
        self.tick_color = "auto"
        self.border_color = "auto"
        self._update_color_preview(self.plot_bg_preview, "auto")
        self._update_color_preview(self.figure_bg_preview, "auto")
        self._update_color_preview(self.text_color_preview, "auto")
        self._update_color_preview(self.tick_color_preview, "auto")
        self._update_color_preview(self.border_color_preview, "auto")
        self.tick_direction.setCurrentText("out")
        self.tick_length.setValue(4)
        self.tick_width.setValue(1.0)
        self.border_width.setValue(1.0)
        # Padding defaults
        self.pad_left.setValue(0.135)
        self.pad_right.setValue(1.0)
        self.pad_top.setValue(0.95)
        self.pad_bottom.setValue(0.05)
        self.pad_wspace.setValue(0.2)
        self.pad_hspace.setValue(0.2)
        self.use_tight_layout.setChecked(False)

    def get_settings(self):
        """Return the current settings as a dictionary."""
        return {
            "xlabel": self.xlabel_edit.text(),
            "ylabel": self.ylabel_edit.text(),
            "title": self.title_edit.text(),
            "colorbar_label": self.colorbar_label_edit.text(),
            "axis_label_fontsize": self.axis_label_size.value(),
            "axis_tick_fontsize": self.axis_tick_size.value(),
            "title_fontsize": self.title_size.value(),
            "colorbar_label_fontsize": self.colorbar_label_size.value(),
            "colorbar_tick_fontsize": self.colorbar_tick_size.value(),
            "plot_bg_color": self.plot_bg_color,
            "figure_bg_color": self.figure_bg_color,
            "text_color": self.text_color,
            "tick_color": self.tick_color,
            "border_color": self.border_color,
            "border_width": self.border_width.value(),
            "tick_direction": self.tick_direction.currentText(),
            "tick_length": self.tick_length.value(),
            "tick_width": self.tick_width.value(),
            # Padding settings
            "pad_left": self.pad_left.value(),
            "pad_right": self.pad_right.value(),
            "pad_top": self.pad_top.value(),
            "pad_bottom": self.pad_bottom.value(),
            "pad_wspace": self.pad_wspace.value(),
            "pad_hspace": self.pad_hspace.value(),
            "use_tight_layout": self.use_tight_layout.isChecked(),
        }


class BeamSettingsDialog(QDialog):
    """Dialog for configuring beam display settings."""

    def __init__(self, parent=None, beam_style=None):
        super().__init__(parent)
        self.setWindowTitle("Beam Settings")
        self.setMinimumWidth(350)
        self.beam_style = beam_style or {}
        self.parent_viewer = parent
        self.setup_ui()
        set_hand_cursor(self)

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # Color selection
        color_group = QGroupBox("Colors")
        color_layout = QGridLayout(color_group)

        # Edge color
        edge_label = QLabel("Edge Color:")
        self.edge_combo = QComboBox()
        colors = [
            "black",
            "white",
            "red",
            "green",
            "blue",
            "cyan",
            "magenta",
            "yellow",
            "gray",
        ]
        for color in colors:
            self.edge_combo.addItem(color)
        self.edge_combo.setCurrentText(self.beam_style.get("edgecolor", "black"))
        color_layout.addWidget(edge_label, 0, 0)
        color_layout.addWidget(self.edge_combo, 0, 1)

        # Face color
        face_label = QLabel("Fill Color:")
        self.face_combo = QComboBox()
        for color in colors:
            self.face_combo.addItem(color)
        self.face_combo.setCurrentText(self.beam_style.get("facecolor", "white"))
        color_layout.addWidget(face_label, 1, 0)
        color_layout.addWidget(self.face_combo, 1, 1)
        layout.addWidget(color_group)

        # Style settings
        style_group = QGroupBox("Style")
        style_layout = QGridLayout(style_group)

        # Line width
        linewidth_label = QLabel("Edge Width:")
        self.linewidth_spinbox = QDoubleSpinBox()
        self.linewidth_spinbox.setRange(0.5, 5.0)
        self.linewidth_spinbox.setSingleStep(0.5)
        self.linewidth_spinbox.setValue(self.beam_style.get("linewidth", 1.5))
        style_layout.addWidget(linewidth_label, 0, 0)
        style_layout.addWidget(self.linewidth_spinbox, 0, 1)

        # Opacity
        alpha_label = QLabel("Opacity:")
        self.alpha_spinbox = QDoubleSpinBox()
        self.alpha_spinbox.setRange(0.1, 1.0)
        self.alpha_spinbox.setSingleStep(0.1)
        self.alpha_spinbox.setValue(self.beam_style.get("alpha", 0.4))
        style_layout.addWidget(alpha_label, 1, 0)
        style_layout.addWidget(self.alpha_spinbox, 1, 1)
        layout.addWidget(style_group)

        # Create button box with Apply and Close
        button_box = QDialogButtonBox()
        apply_btn = button_box.addButton("Apply", QDialogButtonBox.ApplyRole)
        close_btn = button_box.addButton("Close", QDialogButtonBox.RejectRole)
        apply_btn.clicked.connect(self.on_apply)
        close_btn.clicked.connect(self.close)
        layout.addWidget(button_box)

    def on_apply(self):
        """Apply settings without closing dialog."""
        try:
            if self.parent_viewer:
                self.parent_viewer.beam_style["edgecolor"] = (
                    self.edge_combo.currentText()
                )
                self.parent_viewer.beam_style["facecolor"] = (
                    self.face_combo.currentText()
                )
                self.parent_viewer.beam_style["linewidth"] = (
                    self.linewidth_spinbox.value()
                )
                self.parent_viewer.beam_style["alpha"] = self.alpha_spinbox.value()
                self.parent_viewer.schedule_plot()
                self.parent_viewer.show_status_message("Beam settings applied")
        except RuntimeError:
            self.close()
        except Exception as e:
            if self.parent_viewer:
                self.parent_viewer.show_status_message(f"Error applying settings: {e}")

    def get_settings(self):
        """Return the current settings."""
        return {
            "edgecolor": self.edge_combo.currentText(),
            "facecolor": self.face_combo.currentText(),
            "linewidth": self.linewidth_spinbox.value(),
            "alpha": self.alpha_spinbox.value(),
        }


class GridSettingsDialog(QDialog):
    """Dialog for configuring grid display settings."""

    def __init__(self, parent=None, grid_style=None):
        super().__init__(parent)
        self.setWindowTitle("Grid Settings")
        self.setMinimumWidth(350)
        self.grid_style = grid_style or {}
        self.parent_viewer = parent
        self.setup_ui()
        set_hand_cursor(self)

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # Color selection
        color_group = QGroupBox("Color")
        color_layout = QHBoxLayout(color_group)

        color_label = QLabel("Grid Color:")
        self.color_combo = QComboBox()
        colors = [
            "white",
            "black",
            "red",
            "green",
            "blue",
            "cyan",
            "magenta",
            "yellow",
            "gray",
        ]
        for color in colors:
            self.color_combo.addItem(color)
        self.color_combo.setCurrentText(self.grid_style.get("color", "white"))
        color_layout.addWidget(color_label)
        color_layout.addWidget(self.color_combo)
        layout.addWidget(color_group)

        # Style settings
        style_group = QGroupBox("Style")
        style_layout = QGridLayout(style_group)

        # Line style
        linestyle_label = QLabel("Line Style:")
        self.linestyle_combo = QComboBox()
        linestyles = [
            ("-", "Solid"),
            ("--", "Dashed"),
            (":", "Dotted"),
            ("-.", "Dash-dot"),
        ]
        for style_code, style_name in linestyles:
            self.linestyle_combo.addItem(style_name, style_code)

        # Set current line style
        current_style = self.grid_style.get("linestyle", "--")
        for i in range(self.linestyle_combo.count()):
            if self.linestyle_combo.itemData(i) == current_style:
                self.linestyle_combo.setCurrentIndex(i)
                break

        style_layout.addWidget(linestyle_label, 0, 0)
        style_layout.addWidget(self.linestyle_combo, 0, 1)

        # Opacity
        alpha_label = QLabel("Opacity:")
        self.alpha_spinbox = QDoubleSpinBox()
        self.alpha_spinbox.setRange(0.1, 1.0)
        self.alpha_spinbox.setSingleStep(0.1)
        self.alpha_spinbox.setValue(self.grid_style.get("alpha", 0.5))
        style_layout.addWidget(alpha_label, 1, 0)
        style_layout.addWidget(self.alpha_spinbox, 1, 1)
        layout.addWidget(style_group)

        # Create button box with Apply and Close
        button_box = QDialogButtonBox()
        apply_btn = button_box.addButton("Apply", QDialogButtonBox.ApplyRole)
        close_btn = button_box.addButton("Close", QDialogButtonBox.RejectRole)
        apply_btn.clicked.connect(self.on_apply)
        close_btn.clicked.connect(self.close)
        layout.addWidget(button_box)

    def on_apply(self):
        """Apply settings without closing dialog."""
        try:
            if self.parent_viewer:
                self.parent_viewer.grid_style["color"] = self.color_combo.currentText()
                self.parent_viewer.grid_style["linestyle"] = (
                    self.linestyle_combo.currentData()
                )
                self.parent_viewer.grid_style["alpha"] = self.alpha_spinbox.value()
                self.parent_viewer.schedule_plot()
                self.parent_viewer.show_status_message("Grid settings applied")
        except RuntimeError:
            self.close()
        except Exception as e:
            if self.parent_viewer:
                self.parent_viewer.show_status_message(f"Error applying settings: {e}")

    def get_settings(self):
        """Return the current settings."""
        return {
            "color": self.color_combo.currentText(),
            "linestyle": self.linestyle_combo.currentData(),
            "alpha": self.alpha_spinbox.value(),
        }


class PreferencesDialog(QDialog):
    """Dialog for application preferences including UI scale settings."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(400)

        # Import theme manager for styling
        try:
            from .styles import theme_manager
        except ImportError:
            from styles import theme_manager

        palette = theme_manager.palette
        is_dark = theme_manager.is_dark

        # Get current settings
        from PyQt5.QtCore import QSettings

        self.settings = QSettings("SolarViewer", "SolarViewer")

        self.setup_ui()
        set_hand_cursor(self)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)

        # UI Scale group
        scale_group = QGroupBox("UI Scale")
        scale_layout = QVBoxLayout(scale_group)

        # Description label
        desc_label = QLabel(
            "Adjust the UI scale factor for high-DPI displays.\n"
            "Default is 1.0 (100%). Increase for larger UI elements."
        )
        desc_label.setWordWrap(True)
        scale_layout.addWidget(desc_label)

        # Scale slider with value display
        slider_layout = QHBoxLayout()

        # Scale slider - use 5% increments (values 50-200 representing 0.50-2.00)
        from PyQt5.QtWidgets import QSlider

        self.scale_slider = QSlider(Qt.Horizontal)
        self.scale_slider.setMinimum(50)  # 0.5x
        self.scale_slider.setMaximum(200)  # 2.0x
        self.scale_slider.setSingleStep(5)  # 5% increments when using arrows
        self.scale_slider.setPageStep(10)  # 10% increments when clicking track
        self.scale_slider.setTickInterval(25)  # Tick marks every 25%
        self.scale_slider.setTickPosition(QSlider.TicksBelow)

        # Load current scale value
        current_scale = self.settings.value("ui_scale_factor", 1.0, type=float)
        self.scale_slider.setValue(int(current_scale * 100))

        # Spinbox for precise value entry
        self.scale_spinbox = QDoubleSpinBox()
        self.scale_spinbox.setRange(0.5, 2.0)
        self.scale_spinbox.setSingleStep(0.05)
        self.scale_spinbox.setDecimals(2)
        self.scale_spinbox.setSuffix("x")
        self.scale_spinbox.setValue(current_scale)
        self.scale_spinbox.setFixedWidth(75)

        # Connect slider and spinbox to sync values
        self.scale_slider.valueChanged.connect(self._on_slider_changed)
        self.scale_spinbox.valueChanged.connect(self._on_spinbox_changed)

        slider_layout.addWidget(QLabel("0.5x"))
        slider_layout.addWidget(self.scale_slider, 1)
        slider_layout.addWidget(QLabel("2.0x"))
        slider_layout.addWidget(self.scale_spinbox)

        scale_layout.addLayout(slider_layout)

        # Preset buttons row
        preset_layout = QHBoxLayout()
        preset_layout.addWidget(QLabel("Presets:"))

        presets = [
            ("75%", 0.75),
            ("100%", 1.0),
            ("125%", 1.25),
            ("150%", 1.5),
            ("175%", 1.75),
        ]
        for label, value in presets:
            btn = QPushButton(label)
            btn.setFixedWidth(50)
            btn.clicked.connect(lambda checked, v=value: self._set_scale(v))
            preset_layout.addWidget(btn)
        preset_layout.addStretch()
        scale_layout.addLayout(preset_layout)

        layout.addWidget(scale_group)

        # Restart notice
        notice_frame = QFrame()
        notice_frame.setFrameShape(QFrame.StyledPanel)
        '''notice_frame.setStyleSheet("""
            QFrame {
                background-color: rgba(255, 193, 7, 0.15);
                border: 1px solid rgba(255, 193, 7, 0.5);
                border-radius: 6px;
                padding: 8px;
            }
            QLabel {
                color: inherit;
            }
        """)'''
        notice_layout = QHBoxLayout(notice_frame)
        notice_layout.setContentsMargins(10, 8, 10, 8)
        notice_icon = QLabel("⚠️")
        notice_icon.setStyleSheet("font-size: 16px;")
        notice_text = QLabel(
            "Scale changes require application restart to take effect."
        )
        notice_layout.addWidget(notice_icon)
        notice_layout.addWidget(notice_text, 1)
        layout.addWidget(notice_frame)

        layout.addStretch()

        # Button box
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self._save_and_close)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _on_slider_changed(self, value):
        """Update spinbox when slider value changes."""
        scale = value / 100.0
        # Block signals to prevent feedback loop
        self.scale_spinbox.blockSignals(True)
        self.scale_spinbox.setValue(scale)
        self.scale_spinbox.blockSignals(False)

    def _on_spinbox_changed(self, value):
        """Update slider when spinbox value changes."""
        # Block signals to prevent feedback loop
        self.scale_slider.blockSignals(True)
        self.scale_slider.setValue(int(value * 100))
        self.scale_slider.blockSignals(False)

    def _set_scale(self, value):
        """Set scale from preset button."""
        self.scale_spinbox.setValue(value)  # This will trigger slider update via signal

    def _save_and_close(self):
        """Save settings and close dialog."""
        scale = self.scale_spinbox.value()  # Use spinbox for more precise value
        current_scale = self.settings.value("ui_scale_factor", 1.0, type=float)

        self.settings.setValue("ui_scale_factor", scale)

        # If scale changed, offer to restart
        if abs(scale - current_scale) > 0.01:
            reply = QMessageBox.question(
                self,
                "Restart Required",
                f"UI scale changed to {scale:.2f}x.\n\n"
                "The application needs to restart for changes to take effect.\n\n"
                "Would you like to restart now?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )

            if reply == QMessageBox.Yes:
                # Restart the application
                import sys
                import os

                # Accept dialog first
                self.accept()

                # Get the application instance and quit
                app = QApplication.instance()
                if app:
                    # Schedule restart
                    from PyQt5.QtCore import QTimer

                    QTimer.singleShot(100, lambda: self._do_restart())
                return

        self.accept()

    def _do_restart(self):
        """Perform application restart."""
        import sys
        import os

        # Get the current executable and arguments
        python = sys.executable
        args = sys.argv[:]

        # Quit current app
        app = QApplication.instance()
        if app:
            app.quit()

        # Start new instance
        os.execv(python, [python] + args)


def process_single_file_phase_shift(args):
    """
    Wrapper for multiprocessing: instantiates SolarPhaseCenter and processes file.

    Parameters
    ----------
    args : tuple
        Tuple containing (file_path, ra, dec, stokes, output_pattern, visual_center, phase_result)

    Returns
    -------
    tuple
        (success, file_path, error_message)
    """
    try:
        from .move_phasecenter import SolarPhaseCenter

        # Instantiate fresh for this process
        spc = SolarPhaseCenter()
        return spc.process_single_file(args)
    except Exception as e:
        import traceback

        traceback.print_exc()
        # Try to extract filename from args if possible
        filename = args[0] if args and len(args) > 0 else "Unknown"
        return (False, filename, f"Process Error: {str(e)}")


from PyQt5.QtCore import QThread, pyqtSignal, Qt
from .utils.update_checker import check_for_updates
from .version import __version__
import subprocess
import sys
from PyQt5.QtWidgets import QLabel, QMessageBox, QFrame


class UpdateCheckThread(QThread):
    """Thread to check for updates in background."""

    update_checked = pyqtSignal(
        bool, str, str
    )  # (is_available, latest_version, error_msg)

    def run(self):
        is_avail, latest, error = check_for_updates(__version__)
        self.update_checked.emit(is_avail, latest, error)


class UpdateDialog(QDialog):
    """Dialog to check for application updates."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Check for Updates")
        self.setMinimumWidth(400)

        # Theme styling
        from .styles import theme_manager

        self.theme = theme_manager
        self.palette = theme_manager.palette

        # Apply base dialog styling
        self.setStyleSheet(
            f"""
            QDialog {{
                background-color: {self.palette['window']};
                color: {self.palette['text']};
            }}
            QLabel {{
                color: {self.palette['text']};
                font-size: 11pt;
            }}
        """
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        # Header Area
        header_layout = QHBoxLayout()
        header_layout.setSpacing(16)

        # Icon (Text-based placeholder for now)
        icon_label = QLabel("🔄")
        icon_label.setStyleSheet(
            f"""
            font-size: 32pt;
            color: {self.palette['highlight']};
        """
        )
        header_layout.addWidget(icon_label)

        # Title/Subtitle
        title_layout = QVBoxLayout()
        title_layout.setSpacing(4)

        title_label = QLabel("Software Update")
        title_label.setStyleSheet(
            f"""
            font-size: 14pt;
            font-weight: bold;
            color: {self.palette['text']};
        """
        )

        subtitle_label = QLabel("Check for new versions of SolarViewer")
        subtitle_label.setStyleSheet(
            f"""
            color: {self.palette['text_secondary']};
            font-size: 10.2pt;
        """
        )

        title_layout.addWidget(title_label)
        title_layout.addWidget(subtitle_label)
        header_layout.addLayout(title_layout)
        header_layout.addStretch()

        layout.addLayout(header_layout)

        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setFrameShadow(QFrame.Sunken)
        divider.setStyleSheet(
            f"background-color: {self.palette['border_light']}; max-height: 1px;"
        )
        layout.addWidget(divider)

        # Info Grid
        info_container = QWidget()
        info_container.setStyleSheet(
            f"""
            QWidget {{
                background-color: {self.palette['surface']};
                border-radius: 8px;
                border: 1px solid {self.palette['border']};
            }}
            QLabel {{
                background-color: transparent;
                border: none;
            }}
        """
        )
        info_grid = QGridLayout(info_container)
        info_grid.setContentsMargins(16, 16, 16, 16)
        info_grid.setVerticalSpacing(12)
        info_grid.setHorizontalSpacing(16)

        # Current Version
        self.current_ver_label = QLabel(f"{__version__}")
        self.current_ver_label.setStyleSheet(
            "font-family: monospace; font-weight: bold;"
        )

        lbl_current = QLabel("Current Version:")
        lbl_current.setStyleSheet(f"color: {self.palette['text_secondary']};")

        info_grid.addWidget(lbl_current, 0, 0)
        info_grid.addWidget(self.current_ver_label, 0, 1)

        # Latest Version
        self.latest_ver_label = QLabel("Checking...")
        self.latest_ver_label.setStyleSheet(
            f"color: {self.palette['text_secondary']}; font-style: italic;"
        )

        lbl_latest = QLabel("Latest Version:")
        lbl_latest.setStyleSheet(f"color: {self.palette['text_secondary']};")

        info_grid.addWidget(lbl_latest, 1, 0)
        info_grid.addWidget(self.latest_ver_label, 1, 1)

        layout.addWidget(info_container)

        # Status Message area
        self.status_label = QLabel("Connecting to update server...")
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet(
            f"""
            color: {self.palette['text_secondary']};
            padding: 8px;
            font-style: italic;
        """
        )
        layout.addWidget(self.status_label)

        layout.addStretch()

        # Buttons
        button_row = QHBoxLayout()
        button_row.addStretch()

        self.close_btn = QPushButton("Close")
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {self.palette['surface']};
                border: 1px solid {self.palette['border']};
                border-radius: 6px;
                color: {self.palette['text']};
                padding: 8px 16px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {self.palette['window']};
                border-color: {self.palette['text_secondary']};
            }}
        """
        )
        self.close_btn.clicked.connect(self.accept)
        button_row.addWidget(self.close_btn)

        self.update_btn = QPushButton("Update Now")
        self.update_btn.setCursor(Qt.PointingHandCursor)
        self.update_btn.setVisible(False)
        self.update_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {self.palette['highlight']};
                border: none;
                border-radius: 6px;
                color: #ffffff;
                padding: 8px 20px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {self.palette['highlight_hover']};
            }}
            QPushButton:disabled {{
                background-color: {self.palette['disabled']};
                color: {self.palette['text_secondary']};
            }}
        """
        )
        self.update_btn.clicked.connect(self.start_update)
        button_row.addWidget(self.update_btn)

        layout.addLayout(button_row)

        # Start check
        self.check_thread = UpdateCheckThread()
        self.check_thread.update_checked.connect(self.on_check_finished)
        self.check_thread.start()

    def on_check_finished(self, is_available, latest_version, error_msg):
        # Safeguard: If dialog is closed/hidden, don't update UI
        if not self.isVisible():
            return

        if error_msg:
            self.status_label.setText(f"Error: {error_msg}")
            self.status_label.setStyleSheet(
                f"color: {self.palette['error']}; font-weight: 500;"
            )
            self.latest_ver_label.setText("Unknown")
        else:
            self.latest_ver_label.setText(f"{latest_version}")
            self.latest_ver_label.setStyleSheet(
                "font-family: monospace; font-weight: bold;"
            )

            if is_available:
                self.status_label.setText(
                    "🚀 A new version of SolarViewer is available!"
                )
                self.status_label.setStyleSheet(
                    f"color: {self.palette['highlight']}; font-weight: bold;"
                )
                self.update_btn.setVisible(True)
                self.update_btn.setEnabled(True)

                # Highlight the latest version label
                self.latest_ver_label.setStyleSheet(
                    f"""
                    font-family: monospace; 
                    font-weight: bold; 
                    color: {self.palette['success']};
                """
                )
            else:
                self.status_label.setText("✅ You are using the latest version.")
                self.status_label.setStyleSheet(
                    f"color: {self.palette['success']}; font-weight: 500;"
                )

    def start_update(self):
        """Install update via pip."""
        reply = QMessageBox.question(
            self,
            "Confirm Update",
            "The application will update via pip and needs to restart.\n\nContinue with update?",
            QMessageBox.Yes | QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            self.status_label.setText("📦 Installing update... Please wait.")
            self.status_label.setStyleSheet(
                f"color: {self.palette['text']}; font-weight: 500;"
            )
            self.update_btn.setEnabled(False)
            self.update_btn.setText("Updating...")
            self.repaint()  # Force redraw

            try:
                import os

                venv_path = sys.prefix
                activate_script = os.path.join(venv_path, "bin", "activate")

                if os.path.exists(activate_script):
                    # Run via shell to support sourcing.
                    cmd = f'. "{activate_script}" && pip install --upgrade solarviewer'
                    print(f"[INFO] Executing: {cmd}")

                    # Use Popen to capture and print output in real-time
                    launch_cwd = os.getcwd() if os.access(os.getcwd(), os.W_OK) else os.path.expanduser("~")
                    process = subprocess.Popen(
                        cmd,
                        shell=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        cwd=launch_cwd,
                    )

                    # Stream output to main stdout (which Log Console captures)
                    for line in process.stdout:
                        print(line, end="")
                        QApplication.processEvents()  # Keep UI responsive

                    process.wait()
                    if process.returncode != 0:
                        raise subprocess.CalledProcessError(process.returncode, cmd)

                else:
                    # Fallback if no standard activate script found
                    cmd = [
                        sys.executable,
                        "-m",
                        "pip",
                        "install",
                        "--upgrade",
                        "solarviewer",
                    ]
                    print(f"[INFO] Executing: {' '.join(cmd)}")

                    launch_cwd = os.getcwd() if os.access(os.getcwd(), os.W_OK) else os.path.expanduser("~")
                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        cwd=launch_cwd,
                    )

                    for line in process.stdout:
                        print(line, end="")
                        QApplication.processEvents()

                    process.wait()
                    if process.returncode != 0:
                        raise subprocess.CalledProcessError(process.returncode, cmd)

                QMessageBox.information(
                    self,
                    "Update Successful",
                    "Update installed successfully.\n\nPlease restart the application to apply changes.",
                )
                self.accept()

            except subprocess.CalledProcessError as e:
                self.status_label.setText("❌ Update failed.")
                self.status_label.setStyleSheet(
                    f"color: {self.palette['error']}; font-weight: bold;"
                )
                QMessageBox.critical(
                    self, "Update Failed", f"Could not install update:\n{e}"
                )
                self.update_btn.setEnabled(True)
                self.update_btn.setText("Update Now")
            except Exception as e:
                self.status_label.setText("❌ Error occurred.")
                self.status_label.setStyleSheet(
                    f"color: {self.palette['error']}; font-weight: bold;"
                )
                QMessageBox.critical(
                    self, "Error", f"An unexpected error occurred:\n{e}"
                )
                self.update_btn.setEnabled(True)
                self.update_btn.setText("Update Now")

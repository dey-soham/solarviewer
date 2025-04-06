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
    QPlainTextEdit,
    QButtonGroup,
    QWidget,
)
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import Qt, QSize
import pkg_resources
import numpy as np
import os
import multiprocessing
import glob


class ContourSettingsDialog(QDialog):
    """Dialog for configuring contour settings with a more compact layout."""

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self.setWindowTitle("Contour Settings")
        self.settings = settings.copy() if settings else {}
        self.setup_ui()
        self.setStyleSheet(
            """
            QGroupBox {
                border: 1px solid #555555;
                border-radius: 3px;
                margin-top: 0.5em;
                padding-top: 0.5em;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px 0 3px;
            }
            QLineEdit {
                padding: 5px;
                border: 1px solid #555555;
                border-radius: 2px;
                background: #2D2D2D;
            }
            QLineEdit:disabled {
                background: #383838;
                color: #888888;
            }
            QComboBox {
                padding: 5px;
                border: 1px solid #555555;
                border-radius: 2px;
                background: #2D2D2D;
            }
            QComboBox:disabled {
                background: #383838;
                color: #888888;
            }
        """
        )

    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # Top row: Source selection and Stokes parameter side by side
        top_layout = QHBoxLayout()
        top_layout.setSpacing(10)

        # Source selection group
        source_group = QGroupBox("Contour Source")
        source_layout = QVBoxLayout(source_group)
        source_layout.setSpacing(10)
        source_layout.setContentsMargins(10, 15, 10, 10)

        # Main radio buttons for source selection
        source_radio_layout = QHBoxLayout()
        source_radio_layout.setSpacing(20)
        self.same_image_radio = QRadioButton("Current Image")
        self.external_image_radio = QRadioButton("External Image")
        if self.settings.get("source") == "external":
            self.external_image_radio.setChecked(True)
        else:
            self.same_image_radio.setChecked(True)
        source_radio_layout.addWidget(self.same_image_radio)
        source_radio_layout.addWidget(self.external_image_radio)
        source_radio_layout.addStretch()
        source_layout.addLayout(source_radio_layout)

        # External image options in a subgroup
        self.external_group = (
            QWidget()
        )  # Changed from QGroupBox to QWidget for better visual
        external_layout = QVBoxLayout(self.external_group)
        external_layout.setSpacing(8)
        external_layout.setContentsMargins(20, 0, 0, 0)  # Add left indent

        # Radio buttons for file type selection
        file_type_layout = QHBoxLayout()
        file_type_layout.setSpacing(20)
        self.radio_casa_image = QRadioButton("CASA Image")
        self.radio_fits_file = QRadioButton("FITS File")
        self.radio_casa_image.setChecked(True)
        file_type_layout.addWidget(self.radio_casa_image)
        file_type_layout.addWidget(self.radio_fits_file)
        file_type_layout.addStretch()
        external_layout.addLayout(file_type_layout)

        # Browse layout
        browse_layout = QHBoxLayout()
        browse_layout.setSpacing(8)
        self.file_path_edit = QLineEdit(self.settings.get("external_image", ""))
        self.file_path_edit.setPlaceholderText("Select CASA image directory...")
        self.file_path_edit.setMinimumWidth(
            250
        )  # Set minimum width for better appearance

        self.browse_button = QPushButton()
        self.browse_button.setObjectName("IconOnlyNBGButton")
        self.browse_button.setIcon(
            QIcon(
                pkg_resources.resource_filename(
                    "solar_radio_image_viewer", "assets/browse.png"
                )
            )
        )
        self.browse_button.setIconSize(QSize(24, 24))
        self.browse_button.setToolTip("Browse")
        self.browse_button.setFixedSize(32, 32)
        self.browse_button.clicked.connect(self.browse_file)
        self.browse_button.setStyleSheet(
            """
            QPushButton {
                background-color: transparent;
                border: none;
                padding: 4px;
            }
            QPushButton:hover {
                background-color: #484848;
            }
            QPushButton:pressed {
                background-color: #303030;
            }
            QPushButton:disabled {
                background-color: transparent;
            }
        """
        )

        browse_layout.addWidget(self.file_path_edit)
        browse_layout.addWidget(self.browse_button)
        external_layout.addLayout(browse_layout)

        source_layout.addWidget(self.external_group)
        top_layout.addWidget(source_group)

        # Stokes parameter group
        stokes_group = QGroupBox("Stokes Parameter")
        stokes_layout = QHBoxLayout(stokes_group)
        stokes_layout.setContentsMargins(10, 15, 10, 10)

        stokes_label = QLabel("Stokes:")
        stokes_label.setFixedWidth(50)  # Fixed width for alignment
        self.stokes_combo = QComboBox()
        self.stokes_combo.addItems(
            ["I", "Q", "U", "V", "Q/I", "U/I", "V/I", "L", "Lfrac", "PANG"]
        )
        self.stokes_combo.setFixedWidth(80)
        current_stokes = self.settings.get("stokes", "I")
        self.stokes_combo.setCurrentText(current_stokes)

        stokes_layout.addWidget(stokes_label)
        stokes_layout.addWidget(self.stokes_combo)
        stokes_layout.addStretch()

        # Set fixed size for stokes group to match source group height
        stokes_group.setFixedHeight(source_group.sizeHint().height())
        stokes_group.setMinimumWidth(200)  # Set minimum width
        top_layout.addWidget(stokes_group)

        main_layout.addLayout(top_layout)

        # Create button group for CASA/FITS selection
        self.file_type_button_group = QButtonGroup()
        self.file_type_button_group.addButton(self.radio_casa_image)
        self.file_type_button_group.addButton(self.radio_fits_file)

        # Connect signals for enabling/disabling external options
        self.external_image_radio.toggled.connect(self.update_external_options)
        self.radio_casa_image.toggled.connect(self.update_placeholder_text)
        self.radio_fits_file.toggled.connect(self.update_placeholder_text)

        # Initially update states
        self.update_external_options(self.external_image_radio.isChecked())
        self.update_placeholder_text()

        # Middle row: Contour Levels and Appearance side by side
        mid_layout = QHBoxLayout()

        # Contour Levels group with a form layout
        levels_group = QGroupBox("Contour Levels")
        levels_layout = QFormLayout(levels_group)
        self.level_type_combo = QComboBox()
        self.level_type_combo.addItems(["fraction", "absolute", "sigma"])
        current_level_type = self.settings.get("level_type", "fraction")
        self.level_type_combo.setCurrentText(current_level_type)
        levels_layout.addRow("Level Type:", self.level_type_combo)
        self.pos_levels_edit = QLineEdit(
            ", ".join(
                str(level)
                for level in self.settings.get("pos_levels", [0.1, 0.3, 0.5, 0.7, 0.9])
            )
        )
        levels_layout.addRow("Positive Levels:", self.pos_levels_edit)
        self.neg_levels_edit = QLineEdit(
            ", ".join(
                str(level)
                for level in self.settings.get("neg_levels", [0.1, 0.3, 0.5, 0.7, 0.9])
            )
        )
        levels_layout.addRow("Negative Levels:", self.neg_levels_edit)
        mid_layout.addWidget(levels_group)

        # Appearance group with a form layout
        appearance_group = QGroupBox("Appearance")
        appearance_layout = QFormLayout(appearance_group)
        self.color_combo = QComboBox()
        self.color_combo.addItems(
            ["white", "black", "red", "green", "blue", "yellow", "cyan", "magenta"]
        )
        current_color = self.settings.get("color", "white")
        self.color_combo.setCurrentText(current_color)
        appearance_layout.addRow("Color:", self.color_combo)
        self.linewidth_spin = QDoubleSpinBox()
        self.linewidth_spin.setRange(0.1, 5.0)
        self.linewidth_spin.setSingleStep(0.1)
        self.linewidth_spin.setValue(self.settings.get("linewidth", 1.0))
        appearance_layout.addRow("Line Width:", self.linewidth_spin)
        self.pos_linestyle_combo = QComboBox()
        self.pos_linestyle_combo.addItems(["-", "--", "-.", ":"])
        current_pos_linestyle = self.settings.get("pos_linestyle", "-")
        self.pos_linestyle_combo.setCurrentText(current_pos_linestyle)
        appearance_layout.addRow("Positive Style:", self.pos_linestyle_combo)
        self.neg_linestyle_combo = QComboBox()
        self.neg_linestyle_combo.addItems(["-", "--", "-.", ":"])
        current_neg_linestyle = self.settings.get("neg_linestyle", "--")
        self.neg_linestyle_combo.setCurrentText(current_neg_linestyle)
        appearance_layout.addRow("Negative Style:", self.neg_linestyle_combo)
        mid_layout.addWidget(appearance_group)

        main_layout.addLayout(mid_layout)

        # Bottom row: RMS Calculation Region in a compact grid layout
        rms_group = QGroupBox("RMS Calculation Region")
        rms_layout = QGridLayout(rms_group)
        self.use_default_rms_box = QCheckBox("Use default RMS region")
        self.use_default_rms_box.setChecked(
            self.settings.get("use_default_rms_region", True)
        )
        self.use_default_rms_box.stateChanged.connect(self.toggle_rms_inputs)
        rms_layout.addWidget(self.use_default_rms_box, 0, 0, 1, 4)
        # Arrange X min and Y min side by side, then X max and Y max
        rms_layout.addWidget(QLabel("X min:"), 1, 0)
        self.rms_xmin = QSpinBox()
        self.rms_xmin.setRange(0, 10000)
        self.rms_xmin.setValue(self.settings.get("rms_box", (0, 200, 0, 130))[0])
        rms_layout.addWidget(self.rms_xmin, 1, 1)
        rms_layout.addWidget(QLabel("Y min:"), 1, 2)
        self.rms_ymin = QSpinBox()
        self.rms_ymin.setRange(0, 10000)
        self.rms_ymin.setValue(self.settings.get("rms_box", (0, 200, 0, 130))[2])
        rms_layout.addWidget(self.rms_ymin, 1, 3)
        rms_layout.addWidget(QLabel("X max:"), 2, 0)
        self.rms_xmax = QSpinBox()
        self.rms_xmax.setRange(0, 10000)
        self.rms_xmax.setValue(self.settings.get("rms_box", (0, 200, 0, 130))[1])
        rms_layout.addWidget(self.rms_xmax, 2, 1)
        rms_layout.addWidget(QLabel("Y max:"), 2, 2)
        self.rms_ymax = QSpinBox()
        self.rms_ymax.setRange(0, 10000)
        self.rms_ymax.setValue(self.settings.get("rms_box", (0, 200, 0, 130))[3])
        rms_layout.addWidget(self.rms_ymax, 2, 3)
        main_layout.addWidget(rms_group)

        # Initialize RMS inputs state
        self.toggle_rms_inputs()

        # Button box at the bottom
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        main_layout.addWidget(button_box)

    def toggle_rms_inputs(self):
        """Update the enabled state and visual appearance of RMS inputs."""
        enabled = not self.use_default_rms_box.isChecked()

        # Create a widget list for consistent state management
        rms_inputs = [self.rms_xmin, self.rms_xmax, self.rms_ymin, self.rms_ymax]

        # Update enabled state for all inputs
        for widget in rms_inputs:
            widget.setEnabled(enabled)

        # Apply greyed out style to all widgets
        opacity = "1.0" if enabled else "0.5"
        disabled_style = f"""
            QSpinBox {{
                opacity: {opacity};
                background-color: {'#2D2D2D' if enabled else '#383838'};
                color: {'#FFFFFF' if enabled else '#888888'};
                border: 1px solid {'#555555' if enabled else '#484848'};
            }}
            QSpinBox:disabled {{
                color: #888888;
            }}
            QLabel {{
                opacity: {opacity};
                color: {'#FFFFFF' if enabled else '#888888'};
            }}
        """

        # Apply the style to the RMS group's grid layout
        grid_layout = self.rms_xmin.parent().layout()
        for i in range(grid_layout.count()):
            widget = grid_layout.itemAt(i).widget()
            if widget and widget is not self.use_default_rms_box:
                widget.setStyleSheet(disabled_style)

    def update_external_options(self, enabled):
        """Update the enabled state and visual appearance of external options."""
        # Update the entire external group
        self.external_group.setEnabled(enabled)

        # Apply greyed out style to all widgets in the external group
        opacity = "1.0" if enabled else "0.5"
        disabled_style = f"""
            QWidget:disabled {{
                color: #888888;
            }}
            QRadioButton {{
                opacity: {opacity};
            }}
            QLineEdit {{
                opacity: {opacity};
                background-color: {'#2D2D2D' if enabled else '#383838'};
            }}
        """
        self.external_group.setStyleSheet(disabled_style)

    def update_placeholder_text(self):
        if self.radio_casa_image.isChecked():
            self.file_path_edit.setPlaceholderText("Select CASA image directory...")
        else:
            self.file_path_edit.setPlaceholderText("Select FITS file...")

    def browse_file(self):
        if self.radio_casa_image.isChecked():
            # Select CASA image directory
            directory = QFileDialog.getExistingDirectory(
                self, "Select a CASA Image Directory"
            )
            if directory:
                self.file_path_edit.setText(directory)
        else:
            # Select FITS file
            file_path, _ = QFileDialog.getOpenFileName(
                self, "Select a FITS file", "", "FITS files (*.fits);;All files (*)"
            )
            if file_path:
                self.file_path_edit.setText(file_path)

    def get_settings(self):
        settings = {}
        settings["source"] = (
            "external" if self.external_image_radio.isChecked() else "same"
        )
        settings["external_image"] = self.file_path_edit.text()
        settings["stokes"] = self.stokes_combo.currentText()
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
        settings["color"] = self.color_combo.currentText()
        settings["linewidth"] = self.linewidth_spin.value()
        settings["pos_linestyle"] = self.pos_linestyle_combo.currentText()
        settings["neg_linestyle"] = self.neg_linestyle_combo.currentText()
        settings["linestyle"] = settings["pos_linestyle"]
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
    def __init__(self, parent=None, info_text=""):
        super().__init__(parent)
        self.setWindowTitle("Image Metadata / Info")
        self.setMinimumSize(500, 400)
        layout = QVBoxLayout(self)
        self.text_area = QPlainTextEdit()
        self.text_area.setReadOnly(True)
        self.text_area.setPlainText(info_text)
        button_box = QDialogButtonBox(QDialogButtonBox.Ok)
        button_box.accepted.connect(self.accept)
        layout.addWidget(self.text_area)
        layout.addWidget(button_box)
        self.setLayout(layout)


class PhaseShiftDialog(QDialog):
    """Dialog for configuring and executing solar phase center shifting."""

    def __init__(self, parent=None, imagename=None):
        super().__init__(parent)
        self.setWindowTitle("Solar Phase Center Shifting")
        self.setMinimumSize(1000, 800)
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

    def setup_ui(self):
        from .move_phasecenter import SolarPhaseCenter

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # Add a description at the top
        description = QLabel(
            "This tool shifts the coordinate system so that the solar center aligns with the image phase center. "
            "This is useful for properly aligning solar observations in heliographic coordinates."
        )
        description.setWordWrap(True)
        description.setStyleSheet("color: #BBB; font-style: italic;")
        main_layout.addWidget(description)

        # Mode selection: Single file or batch processing
        mode_container = QWidget()
        mode_container_layout = QHBoxLayout(mode_container)
        mode_container_layout.setContentsMargins(0, 0, 0, 0)

        mode_group = QGroupBox("Processing Mode")
        mode_layout = QHBoxLayout(mode_group)
        mode_layout.setContentsMargins(10, 15, 10, 10)

        self.single_mode_radio = QRadioButton("Single File")
        self.batch_mode_radio = QRadioButton("Batch Processing")
        self.single_mode_radio.setChecked(True)

        mode_layout.addWidget(self.single_mode_radio)
        mode_layout.addWidget(self.batch_mode_radio)
        mode_layout.addStretch(1)

        # Stokes parameter selection - moved next to mode selection
        stokes_group = QGroupBox("Stokes Parameter")
        stokes_group_layout = QVBoxLayout(stokes_group)
        stokes_group_layout.setContentsMargins(10, 15, 10, 10)

        # Add radio buttons for Stokes mode selection
        stokes_mode_layout = QHBoxLayout()
        self.single_stokes_radio = QRadioButton("Single Stokes")
        self.full_stokes_radio = QRadioButton("Full Stokes")
        self.single_stokes_radio.setChecked(True)
        stokes_mode_layout.addWidget(self.single_stokes_radio)
        stokes_mode_layout.addWidget(self.full_stokes_radio)
        stokes_mode_layout.addStretch(1)
        stokes_group_layout.addLayout(stokes_mode_layout)

        # Add stokes combo box for selection
        stokes_select_layout = QHBoxLayout()
        self.stokes_combo = QComboBox()
        self.stokes_combo.addItems(["I", "Q", "U", "V"])
        stokes_select_layout.addWidget(self.stokes_combo)
        stokes_select_layout.addStretch(1)
        stokes_group_layout.addLayout(stokes_select_layout)

        # Connect stokes mode radios to update UI
        self.single_stokes_radio.toggled.connect(self.update_stokes_mode)
        self.full_stokes_radio.toggled.connect(self.update_stokes_mode)

        # Add the two groups to the container
        mode_container_layout.addWidget(mode_group, 1)
        mode_container_layout.addWidget(stokes_group, 1)
        main_layout.addWidget(mode_container)

        # Connect mode radios to update UI
        self.single_mode_radio.toggled.connect(self.update_mode_ui)
        self.batch_mode_radio.toggled.connect(self.update_mode_ui)

        # Input and Output options in two columns
        io_container = QWidget()
        io_layout = QHBoxLayout(io_container)
        io_layout.setContentsMargins(0, 0, 0, 0)
        io_layout.setSpacing(15)

        # Input options group (left column)
        self.input_group = QGroupBox("Input Settings")
        input_layout = QVBoxLayout(self.input_group)
        input_layout.setSpacing(10)
        input_layout.setContentsMargins(10, 15, 10, 10)

        # Single file mode controls
        self.single_file_widget = QWidget()
        single_file_layout = QFormLayout(self.single_file_widget)
        single_file_layout.setContentsMargins(0, 0, 0, 0)
        single_file_layout.setVerticalSpacing(8)

        # Image selection
        image_layout = QHBoxLayout()
        self.image_path_edit = QLineEdit(self.imagename or "")
        self.image_path_edit.setReadOnly(True)
        self.browse_button = QPushButton("Browse...")
        self.browse_button.clicked.connect(self.browse_image)
        image_layout.addWidget(self.image_path_edit, 1)
        image_layout.addWidget(self.browse_button)
        single_file_layout.addRow("Image:", image_layout)

        # Batch mode controls
        self.batch_file_widget = QWidget()
        batch_file_layout = QFormLayout(self.batch_file_widget)
        batch_file_layout.setContentsMargins(0, 0, 0, 0)
        batch_file_layout.setVerticalSpacing(8)

        # Reference image selection for batch mode
        reference_image_layout = QHBoxLayout()
        self.reference_image_edit = QLineEdit("")
        self.reference_image_edit.setReadOnly(True)
        self.reference_image_edit.setPlaceholderText(
            "Select reference image for phase center calculation"
        )
        self.reference_browse_button = QPushButton("Browse...")
        self.reference_browse_button.clicked.connect(self.browse_reference_image)
        reference_image_layout.addWidget(self.reference_image_edit, 1)
        reference_image_layout.addWidget(self.reference_browse_button)
        batch_file_layout.addRow("Reference Image:", reference_image_layout)

        # Input pattern selection
        input_pattern_layout = QHBoxLayout()
        self.input_pattern_edit = QLineEdit("")
        self.input_pattern_edit.setPlaceholderText("e.g., /path/to/images/*.fits")
        self.input_pattern_button = QPushButton("Browse...")
        self.input_pattern_button.clicked.connect(self.browse_input_pattern)
        input_pattern_layout.addWidget(self.input_pattern_edit, 1)
        input_pattern_layout.addWidget(self.input_pattern_button)
        batch_file_layout.addRow("Apply To Pattern:", input_pattern_layout)

        # MS File selection (optional) - common for both modes
        ms_layout = QHBoxLayout()
        self.ms_path_edit = QLineEdit("")
        self.ms_path_edit.setPlaceholderText(
            "Optional MS file for phase center calculation"
        )
        self.ms_browse_button = QPushButton("Browse...")
        self.ms_browse_button.clicked.connect(self.browse_ms)
        ms_layout.addWidget(self.ms_path_edit, 1)
        ms_layout.addWidget(self.ms_browse_button)

        # Add widgets to input layout
        input_layout.addWidget(self.single_file_widget)
        input_layout.addWidget(self.batch_file_widget)
        self.batch_file_widget.setVisible(False)

        # Add MS file row directly to the input layout
        ms_form_container = QWidget()
        ms_form_layout = QFormLayout(ms_form_container)
        ms_form_layout.setContentsMargins(0, 0, 0, 0)
        ms_form_layout.setVerticalSpacing(8)
        ms_form_layout.addRow("MS File (optional):", ms_layout)
        input_layout.addWidget(ms_form_container)

        # Output options group (right column)
        output_group = QGroupBox("Output Settings")
        output_layout = QVBoxLayout(output_group)
        output_layout.setSpacing(10)
        output_layout.setContentsMargins(10, 15, 10, 10)

        # Single file output
        self.single_output_widget = QWidget()
        single_output_layout = QFormLayout(self.single_output_widget)
        single_output_layout.setContentsMargins(0, 0, 0, 0)
        single_output_layout.setVerticalSpacing(8)

        # Output file selection for single file
        output_file_layout = QHBoxLayout()
        self.output_path_edit = QLineEdit("")
        self.output_path_edit.setPlaceholderText("Leave empty to modify input image")
        self.output_browse_button = QPushButton("Browse...")
        self.output_browse_button.clicked.connect(self.browse_output)
        output_file_layout.addWidget(self.output_path_edit, 1)
        output_file_layout.addWidget(self.output_browse_button)
        single_output_layout.addRow("Output File:", output_file_layout)
        output_layout.addWidget(self.single_output_widget)

        # Batch file output
        self.batch_output_widget = QWidget()
        batch_output_layout = QVBoxLayout(self.batch_output_widget)
        batch_output_layout.setContentsMargins(0, 0, 0, 0)
        batch_output_layout.setSpacing(8)

        # Output pattern for batch mode
        output_pattern_form = QFormLayout()
        output_pattern_form.setVerticalSpacing(8)
        output_pattern_layout = QHBoxLayout()
        self.output_pattern_edit = QLineEdit("shifted_*.fits")
        self.output_pattern_edit.setPlaceholderText("e.g., shifted_*.fits")
        self.output_pattern_button = QPushButton("Browse Directory...")
        self.output_pattern_button.clicked.connect(self.browse_output_dir)
        output_pattern_layout.addWidget(self.output_pattern_edit, 1)
        output_pattern_layout.addWidget(self.output_pattern_button)
        output_pattern_form.addRow("Output Pattern:", output_pattern_layout)
        batch_output_layout.addLayout(output_pattern_form)

        # Add a help text for pattern
        pattern_help = QLabel(
            "Use * in the pattern as a placeholder for the original filename."
        )
        pattern_help.setStyleSheet("color: #BBB; font-style: italic;")
        batch_output_layout.addWidget(pattern_help)

        output_layout.addWidget(self.batch_output_widget)
        self.batch_output_widget.setVisible(False)

        # Add the input and output groups to the container
        io_layout.addWidget(self.input_group, 1)
        io_layout.addWidget(output_group, 1)
        main_layout.addWidget(io_container)

        # Method settings and Visual centering in one row
        method_container = QWidget()
        method_container_layout = QHBoxLayout(method_container)
        method_container_layout.setContentsMargins(0, 0, 0, 0)
        method_container_layout.setSpacing(15)

        # Method options group
        method_group = QGroupBox("Method Settings")
        method_layout = QVBoxLayout(method_group)
        method_layout.setSpacing(10)
        method_layout.setContentsMargins(10, 15, 10, 10)

        # Gaussian fitting option
        self.fit_gaussian_check = QCheckBox("Use Gaussian fitting for solar center")
        self.fit_gaussian_check.setChecked(False)
        method_layout.addWidget(self.fit_gaussian_check)

        # Sigma threshold for center-of-mass
        sigma_layout = QHBoxLayout()
        sigma_layout.addWidget(QLabel("Sigma threshold for center-of-mass:"))
        self.sigma_spinbox = QDoubleSpinBox()
        self.sigma_spinbox.setRange(1.0, 20.0)
        self.sigma_spinbox.setValue(10.0)
        self.sigma_spinbox.setSingleStep(0.5)
        sigma_layout.addWidget(self.sigma_spinbox)
        sigma_layout.addStretch()
        method_layout.addLayout(sigma_layout)

        # Visual centering option
        self.visual_center_check = QCheckBox(
            "Create a visually centered image (moves pixel data)"
        )
        self.visual_center_check.setChecked(False)
        method_layout.addWidget(self.visual_center_check)

        # Multiprocessing option for batch mode
        self.multiprocessing_check = QCheckBox(
            "Use multiprocessing for batch operations (faster)"
        )
        self.multiprocessing_check.setChecked(True)
        self.multiprocessing_check.setToolTip(
            "Enable parallel processing for batch operations"
        )
        method_layout.addWidget(self.multiprocessing_check)

        # CPU cores selection
        cores_layout = QHBoxLayout()
        cores_layout.addWidget(QLabel("Number of CPU cores to use:"))
        self.cores_spinbox = QSpinBox()
        self.cores_spinbox.setRange(1, multiprocessing.cpu_count())
        self.cores_spinbox.setValue(
            max(1, multiprocessing.cpu_count() - 1)
        )  # Default to N-1 cores
        self.cores_spinbox.setSingleStep(1)
        self.cores_spinbox.setToolTip(
            f"Maximum: {multiprocessing.cpu_count()} cores available"
        )
        cores_layout.addWidget(self.cores_spinbox)
        cores_layout.addStretch()
        method_layout.addLayout(cores_layout)

        # Connect multiprocessing checkbox to enable/disable cores spinbox
        self.multiprocessing_check.toggled.connect(self.cores_spinbox.setEnabled)

        # Add the method group to the container (full width)
        method_container_layout.addWidget(method_group)
        main_layout.addWidget(method_container)

        # Add a status text area
        status_group = QGroupBox("Status / Results")
        status_layout = QVBoxLayout(status_group)
        status_layout.setContentsMargins(10, 15, 10, 10)

        self.status_text = QPlainTextEdit()
        self.status_text.setReadOnly(True)
        self.status_text.setPlaceholderText("Status and results will appear here")
        self.status_text.setMinimumHeight(100)
        status_layout.addWidget(self.status_text)

        main_layout.addWidget(status_group)

        # Add dialog buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.apply_phase_shift)
        button_box.rejected.connect(self.reject)
        main_layout.addWidget(button_box)

        # Set the Ok button text based on mode
        self.ok_button = button_box.button(QDialogButtonBox.Ok)
        self.ok_button.setText("Apply Shift")

        # Apply consistent styling to the dialog
        self.setStyleSheet(
            """
            QGroupBox {
                border: 1px solid #555555;
                border-radius: 3px;
                margin-top: 0.5em;
                padding-top: 0.5em;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px 0 3px;
            }
            QLabel {
                margin-top: 2px;
                margin-bottom: 2px;
            }
            QRadioButton, QCheckBox {
                min-height: 20px;
            }
        """
        )

    def update_mode_ui(self):
        """Update UI components based on the selected mode"""
        single_mode = self.single_mode_radio.isChecked()

        # Update visibility of widgets
        self.single_file_widget.setVisible(single_mode)
        self.batch_file_widget.setVisible(not single_mode)
        self.single_output_widget.setVisible(single_mode)
        self.batch_output_widget.setVisible(not single_mode)

        # Update button text
        if single_mode:
            self.ok_button.setText("Apply Shift")
        else:
            self.ok_button.setText("Apply Batch Shift")

    def update_stokes_mode(self):
        """Update UI based on selected Stokes mode"""
        single_stokes = self.single_stokes_radio.isChecked()
        self.stokes_combo.setEnabled(single_stokes)

    def browse_image(self):
        """Browse for input image file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Image File", "", "FITS Files (*.fits);;CASA Images (*)"
        )
        if file_path:
            self.image_path_edit.setText(file_path)
            self.imagename = file_path

            # Set default output filename pattern
            if not self.output_path_edit.text():
                file_dir = os.path.dirname(file_path)
                file_name = os.path.basename(file_path)
                output_path = os.path.join(file_dir, f"shifted_{file_name}")
                self.output_path_edit.setText(output_path)

    def browse_input_pattern(self):
        """Browse for directory and help set input pattern"""
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select Directory for Input Files"
        )
        if dir_path:
            # Set a default pattern in the selected directory
            self.input_pattern_edit.setText(os.path.join(dir_path, "*.fits"))

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
        """Browse for output file location"""
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Output As", "", "FITS Files (*.fits);;CASA Images (*)"
        )
        if file_path:
            self.output_path_edit.setText(file_path)

    def browse_reference_image(self):
        """Browse for reference image file for batch processing"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Reference Image", "", "FITS Files (*.fits);;CASA Images (*)"
        )
        if file_path:
            self.reference_image_edit.setText(file_path)

            # Set default input pattern in the same directory
            if not self.input_pattern_edit.text():
                file_dir = os.path.dirname(file_path)
                self.input_pattern_edit.setText(os.path.join(file_dir, "*.fits"))

    def apply_phase_shift(self):
        """Apply the phase shift to the image(s)"""
        import os
        from .move_phasecenter import SolarPhaseCenter

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
            # Get common parameters
            msname = self.ms_path_edit.text() or None

            # Create SolarPhaseCenter instance - removing cellsize and imsize parameters
            spc = SolarPhaseCenter(msname=msname)

            # Determine Stokes parameter to use
            if full_stokes:
                stokes_list = ["I", "Q", "U", "V"]
                self.status_text.appendPlainText(
                    "Processing all Stokes parameters: I, Q, U, V"
                )
            else:
                stokes_list = [self.stokes_combo.currentText()]
                self.status_text.appendPlainText(
                    f"Processing Stokes {self.stokes_combo.currentText()}"
                )

            if batch_mode:
                # Batch processing mode
                reference_image = self.reference_image_edit.text()
                input_pattern = self.input_pattern_edit.text()
                output_pattern = (
                    self.output_pattern_edit.text()
                    if self.output_pattern_edit.text()
                    else None
                )

                self.status_text.appendPlainText(
                    f"Using reference image: {reference_image}"
                )
                self.status_text.appendPlainText(
                    f"Processing files matching pattern: {input_pattern}"
                )
                if output_pattern:
                    self.status_text.appendPlainText(
                        f"Output pattern: {output_pattern}"
                    )
                else:
                    self.status_text.appendPlainText(
                        f"Will modify input files in-place"
                    )

                # First calculate phase shift from the reference image
                self.status_text.appendPlainText(
                    f"Calculating solar center position using reference image: {reference_image}"
                )

                # Check if any files match the pattern
                matching_files = glob.glob(input_pattern)
                if not matching_files:
                    QMessageBox.warning(
                        self,
                        "Input Error",
                        f"No files found matching pattern: {input_pattern}",
                    )
                    return

                self.status_text.appendPlainText(
                    f"Found {len(matching_files)} files matching the pattern"
                )

                # Calculate phase shift based on the reference image
                ra, dec, needs_shift = spc.cal_solar_phaseshift(
                    imagename=reference_image,
                    fit_gaussian=self.fit_gaussian_check.isChecked(),
                    sigma=self.sigma_spinbox.value(),
                )

                self.status_text.appendPlainText(
                    f"Calculated solar center: RA = {ra} deg, DEC = {dec} deg"
                )

                if not needs_shift:
                    self.status_text.appendPlainText(
                        "No phase shift needed. Solar center is already aligned with phase center."
                    )
                    result = QMessageBox.question(
                        self,
                        "No Shift Needed",
                        "No phase shift is needed as the solar center is already aligned. Proceed anyway?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No,
                    )
                    if result == QMessageBox.No:
                        return

                # Apply to all files
                visual_center = self.visual_center_check.isChecked()
                use_multiprocessing = self.multiprocessing_check.isChecked()
                max_processes = (
                    self.cores_spinbox.value() if use_multiprocessing else None
                )

                for stokes in stokes_list:
                    self.status_text.appendPlainText(f"\nProcessing Stokes {stokes}...")

                    if use_multiprocessing:
                        self.status_text.appendPlainText(
                            f"Using multiprocessing with {max_processes} CPU cores"
                        )

                    results = spc.apply_shift_to_multiple_fits(
                        ra=ra,
                        dec=dec,
                        input_pattern=input_pattern,
                        output_pattern=output_pattern,
                        stokes=stokes,
                        visual_center=visual_center,
                        use_multiprocessing=use_multiprocessing,
                        max_processes=max_processes,
                    )

                    if visual_center:
                        self.status_text.appendPlainText(
                            "Visually centered images were also created with '_centered' suffix."
                        )

                    self.status_text.appendPlainText(
                        f"Successfully processed {results[0]} out of {results[1]} files for Stokes {stokes}"
                    )

                QMessageBox.information(
                    self,
                    "Success",
                    f"Batch processing completed: {results[0]} out of {results[1]} files processed successfully.",
                )
                self.accept()

            else:
                # Single file mode
                imagename = self.image_path_edit.text()

                # Calculate phase shift
                self.status_text.appendPlainText("Calculating solar center position...")
                ra, dec, needs_shift = spc.cal_solar_phaseshift(
                    imagename=imagename,
                    fit_gaussian=self.fit_gaussian_check.isChecked(),
                    sigma=self.sigma_spinbox.value(),
                )

                self.status_text.appendPlainText(
                    f"Calculated solar center: RA = {ra} deg, DEC = {dec} deg"
                )

                if not needs_shift:
                    self.status_text.appendPlainText(
                        "No phase shift needed. Solar center is already aligned with phase center."
                    )
                    result = QMessageBox.question(
                        self,
                        "No Shift Needed",
                        "No phase shift is needed as the solar center is already aligned. Proceed anyway?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No,
                    )
                    if result == QMessageBox.No:
                        return

                # Process all requested Stokes parameters
                for stokes_param in stokes_list:
                    output_file = self.output_path_edit.text() or imagename

                    # For multi-Stokes mode, append stokes parameter to filename if output is specified
                    if (
                        full_stokes
                        and self.output_path_edit.text()
                        and len(stokes_list) > 1
                    ):
                        base, ext = os.path.splitext(output_file)
                        stokes_output_file = f"{base}_{stokes_param}{ext}"
                    else:
                        stokes_output_file = output_file

                    self.status_text.appendPlainText(
                        f"\nProcessing Stokes {stokes_param}..."
                    )
                    self.status_text.appendPlainText(
                        f"Output file: {stokes_output_file}"
                    )

                    # If output is different from input, make a copy
                    if stokes_output_file != imagename:
                        import shutil

                        if os.path.isdir(imagename):
                            os.system(f"rm -rf {stokes_output_file}")
                            os.system(f"cp -r {imagename} {stokes_output_file}")
                        else:
                            shutil.copy(imagename, stokes_output_file)
                        target = stokes_output_file
                    else:
                        target = imagename

                    self.status_text.appendPlainText(
                        f"Applying phase shift to {target}..."
                    )

                    result = spc.shift_phasecenter(
                        imagename=target, ra=ra, dec=dec, stokes=stokes_param
                    )

                    if result == 0:
                        self.status_text.appendPlainText(
                            "Phase shift successfully applied."
                        )

                        # Create visually centered image if requested
                        if self.visual_center_check.isChecked():
                            # Generate output filename for visually centered image
                            if stokes_output_file == imagename:
                                # If modifying in place, create a separate centered file
                                base_path = os.path.splitext(target)[0]
                                ext = os.path.splitext(target)[1]
                                visual_output = f"{base_path}_centered{ext}"
                            else:
                                # If already creating a new file, derive from that filename
                                base_path = os.path.splitext(stokes_output_file)[0]
                                ext = os.path.splitext(stokes_output_file)[1]
                                visual_output = f"{base_path}_centered{ext}"

                            try:
                                # Get the reference pixel values from the shifted image
                                from astropy.io import fits

                                header = fits.getheader(target)
                                crpix1 = int(header["CRPIX1"])
                                crpix2 = int(header["CRPIX2"])

                                self.status_text.appendPlainText(
                                    f"Creating visually centered image: {visual_output}"
                                )

                                # Create the visually centered image
                                success = spc.visually_center_image(
                                    target, visual_output, crpix1, crpix2
                                )

                                if success:
                                    self.status_text.appendPlainText(
                                        "Visually centered image created successfully."
                                    )
                                else:
                                    self.status_text.appendPlainText(
                                        "Failed to create visually centered image."
                                    )
                            except Exception as vis_error:
                                self.status_text.appendPlainText(
                                    f"Error creating visually centered image: {str(vis_error)}"
                                )
                    elif result == 1:
                        self.status_text.appendPlainText("Phase shift not needed.")
                    else:
                        self.status_text.appendPlainText(
                            f"Error applying phase shift for Stokes {stokes_param}."
                        )

                QMessageBox.information(
                    self,
                    "Success",
                    f"Solar phase center shift completed successfully for {len(stokes_list)} Stokes parameters.",
                )
                self.accept()

        except Exception as e:
            import traceback

            self.status_text.appendPlainText(f"Error: {str(e)}")
            self.status_text.appendPlainText(traceback.format_exc())
            QMessageBox.critical(self, "Error", f"An error occurred: {str(e)}")

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

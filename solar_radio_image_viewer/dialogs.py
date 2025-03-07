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

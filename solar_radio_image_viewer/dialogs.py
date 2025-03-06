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
    QPlainTextEdit
)
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import Qt

class ContourSettingsDialog(QDialog):
    """Dialog for configuring contour settings with a more compact layout."""

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self.setWindowTitle("Contour Settings")
        self.settings = settings.copy() if settings else {}
        self.setup_ui()

    def setup_ui(self):
        main_layout = QVBoxLayout(self)

        # Top row: Source selection and Stokes parameter side by side
        top_layout = QHBoxLayout()

        # Source selection group
        source_group = QGroupBox("Contour Source")
        source_layout = QHBoxLayout(source_group)
        self.same_image_radio = QRadioButton("Current Image")
        self.external_image_radio = QRadioButton("External Image")
        if self.settings.get("source") == "external":
            self.external_image_radio.setChecked(True)
        else:
            self.same_image_radio.setChecked(True)
        source_layout.addWidget(self.same_image_radio)
        source_layout.addWidget(self.external_image_radio)
        self.file_path_edit = QLineEdit(self.settings.get("external_image", ""))
        self.file_path_edit.setPlaceholderText("Select external image directory")
        self.browse_button = QPushButton("Browse")
        self.browse_button.setIcon(QIcon.fromTheme("document-open"))
        self.browse_button.clicked.connect(self.browse_file)
        source_layout.addWidget(self.file_path_edit)
        top_layout.addWidget(source_group)

        # Stokes parameter group
        stokes_group = QGroupBox("Stokes Parameter")
        stokes_layout = QHBoxLayout(stokes_group)
        self.stokes_combo = QComboBox()
        self.stokes_combo.addItems(["I", "Q", "U", "V", "Q/I", "U/I", "V/I", "L", "Lfrac", "PANG"])
        current_stokes = self.settings.get("stokes", "I")
        self.stokes_combo.setCurrentText(current_stokes)
        stokes_layout.addWidget(QLabel("Stokes:"))
        stokes_layout.addWidget(self.stokes_combo)
        top_layout.addWidget(stokes_group)

        main_layout.addLayout(top_layout)

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
            ", ".join(str(level) for level in self.settings.get("pos_levels", [0.1, 0.3, 0.5, 0.7, 0.9]))
        )
        levels_layout.addRow("Positive Levels:", self.pos_levels_edit)
        self.neg_levels_edit = QLineEdit(
            ", ".join(str(level) for level in self.settings.get("neg_levels", [0.1, 0.3, 0.5, 0.7, 0.9]))
        )
        levels_layout.addRow("Negative Levels:", self.neg_levels_edit)
        mid_layout.addWidget(levels_group)

        # Appearance group with a form layout
        appearance_group = QGroupBox("Appearance")
        appearance_layout = QFormLayout(appearance_group)
        self.color_combo = QComboBox()
        self.color_combo.addItems(["white", "black", "red", "green", "blue", "yellow", "cyan", "magenta"])
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
        self.use_default_rms_box.setChecked(self.settings.get("use_default_rms_region", True))
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

        # Button box at the bottom
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        main_layout.addWidget(button_box)

    def toggle_rms_inputs(self):
        enabled = not self.use_default_rms_box.isChecked()
        self.rms_xmin.setEnabled(enabled)
        self.rms_xmax.setEnabled(enabled)
        self.rms_ymin.setEnabled(enabled)
        self.rms_ymax.setEnabled(enabled)

    def browse_file(self):
        external_image_path = QFileDialog.getExistingDirectory(self, "Select CASA Image Directory")
        if external_image_path:
            self.file_path_edit.setText(external_image_path)
            self.external_image_radio.setChecked(True)

    def get_settings(self):
        settings = {}
        settings["source"] = "external" if self.external_image_radio.isChecked() else "same"
        settings["external_image"] = self.file_path_edit.text()
        settings["stokes"] = self.stokes_combo.currentText()
        settings["level_type"] = self.level_type_combo.currentText()
        try:
            pos_levels_text = self.pos_levels_edit.text()
            settings["pos_levels"] = [float(level.strip()) for level in pos_levels_text.split(",") if level.strip()]
        except ValueError:
            settings["pos_levels"] = [0.1, 0.3, 0.5, 0.7, 0.9]
        try:
            neg_levels_text = self.neg_levels_edit.text()
            settings["neg_levels"] = [float(level.strip()) for level in neg_levels_text.split(",") if level.strip()]
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
        directory = QFileDialog.getExistingDirectory(self, "Select a CASA Image Directory")
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


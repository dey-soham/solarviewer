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
    QApplication,
)
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import Qt, QSize
import sys
import numpy as np
import os

from ..styles import theme_manager, set_hand_cursor


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
                from ..processing.image_data import get_pixel_values_from_image

                pix, _, _ = get_pixel_values_from_image(imagename, "I", threshold)
                flux = float(np.sum(pix))
                results.append(f"{imagename}: threshold={threshold}, flux={flux:.2f}")
            except Exception as e:
                results.append(f"{imagename}: ERROR - {str(e)}")
        QMessageBox.information(self, "Batch Results", "\n".join(results))



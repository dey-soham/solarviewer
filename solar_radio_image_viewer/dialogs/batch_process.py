"""
Batch Processing: the flux of each image in a list, measured in a worker
process, with progress and Stop.
"""

import os

import numpy as np
from PyQt5.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
)

from ..ui.batch import BatchWindow


def measure_flux(task):
    """Worker: (whether it worked, what to report for the image)."""
    imagename, threshold = task
    try:
        from ..processing.image_data import get_pixel_values_from_image

        pix, _, _ = get_pixel_values_from_image(imagename, "I", threshold)
        flux = float(np.sum(pix))
        return True, f"threshold={threshold}, flux={flux:.2f}"
    except Exception as e:
        return False, f"ERROR - {str(e)}"


class BatchProcessDialog(BatchWindow):
    VERB = "Run Process"
    RUNNING = "Processing"
    NOUN = ("image", "images")
    SUMMARY_ROWS = ("Threshold",)

    def __init__(self, parent=None):
        super().__init__(parent, "Batch Processing")
        self._results = []
        layout, _ = self.block("Images")
        self.image_list = QListWidget()
        layout.addWidget(self.image_list)
        self.add_button = QPushButton("Add Image")
        self.add_button.setAutoDefault(False)
        self.remove_button = QPushButton("Remove Selected")
        self.remove_button.setAutoDefault(False)
        ctrl_layout = QHBoxLayout()
        ctrl_layout.addWidget(self.add_button)
        ctrl_layout.addWidget(self.remove_button)
        layout.addLayout(ctrl_layout)
        self.threshold_spin = QSpinBox()
        self.threshold_spin.setRange(1, 9999)
        self.threshold_spin.setValue(10)
        thr_layout = QHBoxLayout()
        thr_layout.addWidget(self.row_label("Threshold"))
        thr_layout.addWidget(self.threshold_spin)
        thr_layout.addStretch()
        layout.addLayout(thr_layout)
        self.run_button = self.ok_button
        self.cancel_button.setText("Close")
        self.add_button.clicked.connect(self.add_image)
        self.remove_button.clicked.connect(self.remove_image)
        self.threshold_spin.valueChanged.connect(self.update_summary)
        self.finish_form()

    def add_image(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Select a CASA Image Directory"
        )
        if directory:
            self.image_list.addItem(directory)
            self.update_summary()

    def remove_image(self):
        for item in self.image_list.selectedItems():
            self.image_list.takeItem(self.image_list.row(item))
        self.update_summary()

    def _images(self):
        return [self.image_list.item(i).text() for i in range(self.image_list.count())]

    def summary(self):
        images = self._images()
        n = len(images)
        info = {
            "count": (n, "image" if n == 1 else "images"),
            "sub": "with their total flux",
            "rows": {"Threshold": str(self.threshold_spin.value())},
            "files": images,
        }
        if not images:
            info["problem"] = "Add the images to process"
        return info

    def run(self):
        if not self.ready():
            return
        threshold = self.threshold_spin.value()
        self.start_log()
        self._results = []
        self.start_batch(measure_flux, [(image, threshold) for image in self._images()])

    def run_process(self):
        self.run()

    def batch_result(self, task, value):
        worked, report = value
        self._results.append(f"{task[0]}: {report}")
        self.log(f"{os.path.basename(task[0].rstrip('/'))}: {report}", "" if worked else "failed")
        return worked

    def batch_error(self, task, message):
        self._results.append(f"{task[0]}: ERROR - {message}")
        super().batch_error(task, message)

    def batch_finished(self, aborted):
        if not aborted:
            QMessageBox.information(self, "Batch Results", "\n".join(self._results))

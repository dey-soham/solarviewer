"""
Dynamic Spectra Dialog

GUI dialog for creating dynamic spectra from MS files.
Uses the make_dynamic_spectra.py processing functions.
"""

import os
import sys
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QFileDialog,
    QSpinBox,
    QDoubleSpinBox,
    QGroupBox,
    QProgressBar,
    QTextEdit,
    QCheckBox,
    QMessageBox,
    QApplication,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont

from .simpl_theme import apply_theme, get_theme_from_args
from ..styles import set_hand_cursor


class ProcessingThread(QThread):
    """Thread for running dynamic spectra processing in isolated subprocess."""

    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, params):
        super().__init__()
        self.params = params

    def run(self):
        """Run make_dynamic_spectra in a separate subprocess to avoid casacore/Qt conflicts."""
        import subprocess
        import json

        try:
            # Build command line arguments for make_dynamic_spectra.py
            script_path = os.path.join(
                os.path.dirname(__file__), "make_dynamic_spectra.py"
            )

            cmd = [
                sys.executable,
                script_path,
                "--indir",
                self.params["indir"],
                "--outfits",
                self.params["outfits"],
                "--binwidth",
                str(self.params["binwidth"]),
                "--ncpu",
                str(self.params["ncpu"]),
                "--uvmin",
                str(self.params["uvmin"]),
                "--uvmax",
                str(self.params["uvmax"]),
            ]

            # Add optional frequency range
            if self.params.get("startfreq") and self.params.get("endfreq"):
                cmd.extend(["--startfreq", str(self.params["startfreq"])])
                cmd.extend(["--endfreq", str(self.params["endfreq"])])

            # Add plot options
            if self.params.get("saveplot"):
                cmd.append("--saveplot")
                if self.params.get("plot_filename"):
                    cmd.extend(["--plotfile", self.params["plot_filename"]])

            self.progress.emit(f"Running: {os.path.basename(script_path)}")
            self.progress.emit(f"Input: {self.params['indir']}")
            self.progress.emit(f"Output: {self.params['outfits']}")
            self.progress.emit("")

            # Run in subprocess (completely isolates casacore from Qt)
            launch_cwd = os.getcwd() if os.access(os.getcwd(), os.W_OK) else os.path.expanduser("~")
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=launch_cwd,
            )

            # Stream output in real-time
            for line in iter(process.stdout.readline, ""):
                if line:
                    self.progress.emit(line.rstrip())

            process.wait()

            if process.returncode == 0:
                self.finished.emit(True, f"Success! Created: {self.params['outfits']}")
            else:
                self.finished.emit(
                    False, f"Processing failed with exit code {process.returncode}"
                )

        except Exception as e:
            self.finished.emit(False, f"Error: {str(e)}")


class DynamicSpectraDialog(QDialog):
    """Dialog for creating dynamic spectra from MS files."""

    def __init__(self, parent=None, theme="dark"):
        super().__init__(parent)
        self.theme = theme
        self.processing_thread = None
        self._setup_ui()
        set_hand_cursor(self)

    def _setup_ui(self):
        self.setWindowTitle("Create Dynamic Spectra")
        self.setMinimumSize(600, 500)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Input/Output Group
        io_group = QGroupBox("Input / Output")
        io_layout = QGridLayout(io_group)

        # Input directory
        io_layout.addWidget(QLabel("MS Directory:"), 0, 0)
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("Directory containing MS files...")
        io_layout.addWidget(self.input_edit, 0, 1)
        self.input_browse_btn = QPushButton("Browse...")
        self.input_browse_btn.clicked.connect(self._browse_input)
        io_layout.addWidget(self.input_browse_btn, 0, 2)

        # Output FITS file
        io_layout.addWidget(QLabel("Output FITS:"), 1, 0)
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Output FITS file path...")
        self.output_edit.setText("dynamic_spectrum.fits")
        io_layout.addWidget(self.output_edit, 1, 1)
        self.output_browse_btn = QPushButton("Browse...")
        self.output_browse_btn.clicked.connect(self._browse_output)
        io_layout.addWidget(self.output_browse_btn, 1, 2)

        layout.addWidget(io_group)

        # Parameters Group
        params_group = QGroupBox("Processing Parameters")
        params_layout = QGridLayout(params_group)

        # Time binwidth
        params_layout.addWidget(QLabel("Time Bin Width (s):"), 0, 0)
        self.binwidth_spin = QDoubleSpinBox()
        self.binwidth_spin.setRange(0.1, 60.0)
        self.binwidth_spin.setValue(1.0)
        self.binwidth_spin.setDecimals(1)
        params_layout.addWidget(self.binwidth_spin, 0, 1)

        # Number of CPUs
        params_layout.addWidget(QLabel("CPU Cores:"), 0, 2)
        self.ncpu_spin = QSpinBox()
        self.ncpu_spin.setRange(1, os.cpu_count() or 8)
        self.ncpu_spin.setValue(min(10, os.cpu_count() or 8))
        params_layout.addWidget(self.ncpu_spin, 0, 3)

        # UV range
        params_layout.addWidget(QLabel("UV Min (λ):"), 1, 0)
        self.uvmin_spin = QDoubleSpinBox()
        self.uvmin_spin.setRange(0, 10000)
        self.uvmin_spin.setValue(130.0)
        params_layout.addWidget(self.uvmin_spin, 1, 1)

        params_layout.addWidget(QLabel("UV Max (λ):"), 1, 2)
        self.uvmax_spin = QDoubleSpinBox()
        self.uvmax_spin.setRange(0, 10000)
        self.uvmax_spin.setValue(500.0)
        params_layout.addWidget(self.uvmax_spin, 1, 3)

        # Frequency range (optional)
        params_layout.addWidget(QLabel("Start Freq (MHz):"), 2, 0)
        self.startfreq_spin = QDoubleSpinBox()
        self.startfreq_spin.setRange(0, 1000)
        self.startfreq_spin.setValue(0)
        self.startfreq_spin.setSpecialValueText("Auto")
        params_layout.addWidget(self.startfreq_spin, 2, 1)

        params_layout.addWidget(QLabel("End Freq (MHz):"), 2, 2)
        self.endfreq_spin = QDoubleSpinBox()
        self.endfreq_spin.setRange(0, 1000)
        self.endfreq_spin.setValue(0)
        self.endfreq_spin.setSpecialValueText("Auto")
        params_layout.addWidget(self.endfreq_spin, 2, 3)

        layout.addWidget(params_group)

        # Options Group
        options_group = QGroupBox("Options")
        options_layout = QHBoxLayout(options_group)

        self.save_plot_check = QCheckBox("Save Plot")
        self.save_plot_check.setChecked(True)
        options_layout.addWidget(self.save_plot_check)

        options_layout.addStretch()

        layout.addWidget(options_group)

        # Progress section
        progress_group = QGroupBox("Progress")
        progress_layout = QVBoxLayout(progress_group)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # Indeterminate
        self.progress_bar.setVisible(False)
        progress_layout.addWidget(self.progress_bar)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(120)
        self.log_text.setFont(QFont("Consolas", 9))
        progress_layout.addWidget(self.log_text)

        layout.addWidget(progress_group)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.run_btn = QPushButton("Create Dynamic Spectra")
        self.run_btn.setMinimumWidth(180)
        self.run_btn.clicked.connect(self._run_processing)
        button_layout.addWidget(self.run_btn)

        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.close)
        button_layout.addWidget(self.close_btn)

        layout.addLayout(button_layout)

    def _browse_input(self):
        dir_path = QFileDialog.getExistingDirectory(
            self,
            "Select MS Directory",
            self.input_edit.text() or os.path.expanduser("~"),
        )
        if dir_path:
            self.input_edit.setText(dir_path)
            # Auto-set output path
            if (
                not self.output_edit.text()
                or self.output_edit.text() == "dynamic_spectrum.fits"
            ):
                self.output_edit.setText(
                    os.path.join(dir_path, "dynamic_spectrum.fits")
                )

    def _browse_output(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save FITS File",
            self.output_edit.text() or "dynamic_spectrum.fits",
            "FITS Files (*.fits);;All Files (*)",
        )
        if file_path:
            self.output_edit.setText(file_path)

    def _run_processing(self):
        # Validate input
        if not self.input_edit.text():
            QMessageBox.warning(self, "Missing Input", "Please select an MS directory.")
            return

        if not os.path.isdir(self.input_edit.text()):
            QMessageBox.warning(
                self, "Invalid Input", "The specified directory does not exist."
            )
            return

        if not self.output_edit.text():
            QMessageBox.warning(
                self, "Missing Output", "Please specify an output FITS file path."
            )
            return

        # Check for MS files
        import glob

        ms_files = glob.glob(os.path.join(self.input_edit.text(), "*.MS"))
        if not ms_files:
            QMessageBox.warning(
                self, "No MS Files", "No .MS files found in the specified directory."
            )
            return

        # Prepare parameters
        params = {
            "indir": self.input_edit.text(),
            "outfits": self.output_edit.text(),
            "binwidth": self.binwidth_spin.value(),
            "ncpu": self.ncpu_spin.value(),
            "uvmin": self.uvmin_spin.value(),
            "uvmax": self.uvmax_spin.value(),
            "startfreq": (
                self.startfreq_spin.value() if self.startfreq_spin.value() > 0 else None
            ),
            "endfreq": (
                self.endfreq_spin.value() if self.endfreq_spin.value() > 0 else None
            ),
            "saveplot": self.save_plot_check.isChecked(),
            "plot_filename": (
                self.output_edit.text().replace(".fits", ".png")
                if self.save_plot_check.isChecked()
                else None
            ),
        }

        # Start processing
        self.log_text.clear()
        self.log_text.append(f"Found {len(ms_files)} MS files")
        self.log_text.append(f"Output: {params['outfits']}")
        self.log_text.append("Starting processing...")

        self.progress_bar.setVisible(True)
        self.run_btn.setEnabled(False)

        self.processing_thread = ProcessingThread(params)
        self.processing_thread.progress.connect(self._on_progress)
        self.processing_thread.finished.connect(self._on_finished)
        self.processing_thread.start()

    def _on_progress(self, message):
        self.log_text.append(message)
        # Auto-scroll to bottom
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _on_finished(self, success, message):
        self.progress_bar.setVisible(False)
        self.run_btn.setEnabled(True)

        self.log_text.append("")
        self.log_text.append(f"{'✓' if success else '✗'} {message}")

        if success:
            QMessageBox.information(self, "Success", message)
        else:
            QMessageBox.warning(self, "Processing Failed", message)


def main():
    """Main entry point for standalone dialog."""
    for key in ["QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"]:
        if key in os.environ:
            del os.environ[key]

    # Apply high DPI scaling
    from .simpl_theme import setup_high_dpi
    setup_high_dpi()

    app = QApplication(sys.argv)

    # Get theme from command line
    from .simpl_theme import apply_theme, get_theme_from_args
    from ..styles import set_hand_cursor
    theme = get_theme_from_args()
    apply_theme(app, theme)

    dialog = DynamicSpectraDialog(theme=theme)
    dialog.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

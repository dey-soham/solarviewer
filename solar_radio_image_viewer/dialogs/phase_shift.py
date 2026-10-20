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
    QDialogButtonBox,
    QComboBox,
    QCheckBox,
    QFileDialog,
    QMessageBox,
    QPlainTextEdit,
    QWidget,
    QFrame,
    QTabWidget,
    QProgressBar,
)
import os

from ..ui.styles import set_hand_cursor

import multiprocessing
import glob

class PhaseShiftDialog(QDialog):
    """Dialog for configuring and executing solar phase center shifting."""

    def __init__(self, parent=None, imagename=None, batch_mode=False):
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
        if batch_mode:
            self.batch_mode_radio.setChecked(True)
        set_hand_cursor(self)

    def setup_ui(self):

        # Theme setup
        try:
            from ..ui.styles import theme_manager
        except ImportError:
            from ..ui.styles import theme_manager

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
        from ..coordinates.move_phasecenter import SolarPhaseCenter
        from PyQt5.QtWidgets import QApplication, QMessageBox
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
        from ..coordinates.move_phasecenter import SolarPhaseCenter

        # Instantiate fresh for this process
        spc = SolarPhaseCenter()
        return spc.process_single_file(args)
    except Exception as e:
        import traceback

        traceback.print_exc()
        # Try to extract filename from args if possible
        filename = args[0] if args and len(args) > 0 else "Unknown"
        return (False, filename, f"Process Error: {str(e)}")



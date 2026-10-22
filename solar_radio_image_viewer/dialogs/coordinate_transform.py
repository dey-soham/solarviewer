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
    QDialogButtonBox,
    QComboBox,
    QCheckBox,
    QFileDialog,
    QMessageBox,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QWidget,
    QFrame,
    QTabWidget,
    QProgressBar,
)
import os

from ..processing.fits_io import is_fits
from ..ui.batch import BatchDialogMixin
from ..ui.styles import set_hand_cursor

import multiprocessing
import glob
import uuid
import traceback

def process_single_file_coordsys(args):
    """Process a single file for coordinate transformation - standalone function for multiprocessing

    Parameters:
    -----------
    args : tuple
        Tuple containing (input_file, output_path, stokes, process_id, target_mode)
        target_mode: "HPC" or "RADEC"

    Returns:
    --------
    dict
        Result dictionary with processing outcome
    """
    input_file, output_path, stokes, process_id, target_mode = args

    try:
        result = {
            "input_file": input_file,
            "output_path": output_path,
            "stokes": stokes,
            "success": False,
            "error": None,
            "skipped": False,  # True if already in target format and just copied
        }

        # Check coordinate system
        already_hpc = is_already_hpc(input_file)
        
        # Decide if we need to convert or just copy
        should_copy = False
        if target_mode == "HPC" and already_hpc:
            should_copy = True
        elif target_mode == "RADEC" and not already_hpc:
            should_copy = True
            
        if should_copy:
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

        # Import transformation functions
        from ..coordinates.helioprojective import convert_and_save_hpc, convert_hpc_to_radec

        # The conversions write FITS, which needs the extension
        if not os.path.splitext(output_path)[1]:
            output_path += ".fits"
            result["output_path"] = output_path

        # Generate a unique file suffix for this process to avoid conflicts
        temp_suffix = f"_proc_{process_id}_{uuid.uuid4().hex[:8]}"

        if target_mode == "HPC":
            # Convert RA/DEC to HPC
            success = convert_and_save_hpc(
                input_file,
                output_path,
                Stokes=stokes,
                overwrite=True,
                temp_suffix=temp_suffix,
            )
        else:
            # Convert HPC to RA/DEC
            # convert_hpc_to_radec handles saving to output_path
            success = convert_hpc_to_radec(
                input_file,
                output_path,
                overwrite=True
            )

        result["success"] = success
        if not success and result["error"] is None:
            result["error"] = f"Transformation to {target_mode} failed (check file headers for valid WCS info)"
            
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
        if is_fits(imagepath):
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



class CoordinateTransformationDialog(BatchDialogMixin, QDialog):
    """Dialog for batch coordinate transformation of images (RA/DEC <-> HPC)."""

    def __init__(self, parent=None, current_file=None):
        super().__init__(parent)
        self.setWindowTitle("Batch Coordinate Transformation")
        self.setMinimumSize(600, 550)
        self.resize(750, 650)
        self.parent = parent
        self.current_file = current_file

        # Import theme manager for theme-aware styling
        try:
            from ..ui.styles import theme_manager
        except ImportError:
            from ..ui.styles import theme_manager

        # Get colors directly from the palette for consistency
        palette = theme_manager.palette

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
            from ..ui.styles import theme_manager
        except ImportError:
            from ..ui.styles import theme_manager

        palette = theme_manager.palette
        text_secondary = palette.get("text_secondary", palette["disabled"])
        border_color = palette["border"]

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # Header description
        header = QLabel(
            "Convert multiple FITS/CASA images between RA/DEC and Helioprojective (HPC) coordinates."
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

        # Target System Group
        sys_group = QGroupBox("Target Coordinate System")
        sys_layout = QVBoxLayout(sys_group)
        sys_layout.setSpacing(12)

        sys_row = QHBoxLayout()
        sys_label = QLabel("Target System:")
        sys_label.setMinimumWidth(100)
        sys_label.setStyleSheet("font-weight: 600;")
        sys_row.addWidget(sys_label)

        self.target_system_combo = QComboBox()
        self.target_system_combo.addItems([
            "Helioprojective (HPC)",
            "Celestial (RA/DEC)"
        ])
        self.target_system_combo.currentIndexChanged.connect(self._update_default_output_dir)
        sys_row.addWidget(self.target_system_combo, 1)
        sys_layout.addLayout(sys_row)
        options_layout.addWidget(sys_group)

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

        button_box.accepted.connect(self.convert_files)
        button_box.rejected.connect(self.cancel_or_abort)  # Abort while converting

        button_layout.addStretch()
        button_layout.addWidget(button_box)

        main_layout.addLayout(button_layout)

    def _update_default_output_dir(self):
        """Update default output directory based on transformation direction"""
        input_dir = self.dir_edit.text()
        if not input_dir:
            return
            
        if self.target_system_combo.currentText() == "Helioprojective (HPC)":
            target_sub = "hpc"
            pattern = "hpc_*.fits"
        else:
            target_sub = "radec"
            pattern = "radec_*.fits"
            
        # Update output directory if it matches previous default or is empty
        current_out = self.output_dir_edit.text()
        if not current_out or current_out.endswith("/hpc") or current_out.endswith("/radec"):
            self.output_dir_edit.setText(os.path.join(input_dir, target_sub))
            self.output_pattern_edit.setText(pattern)

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
            self._update_default_output_dir()

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
        stokes_list = (
            ["I", "Q", "U", "V"] if full_stokes else [self.stokes_combo.currentText()]
        )
        target_mode = "HPC" if self.target_system_combo.currentText() == "Helioprojective (HPC)" else "RADEC"

        # One task per file and Stokes: (input_file, output_path, stokes, process_id, target_mode)
        tasks = []
        for i, input_file in enumerate(files_to_process):
            base_filename = os.path.splitext(os.path.basename(input_file))[0]
            if "*" in output_pattern:
                output_filename = output_pattern.replace("*", base_filename)
            else:
                output_filename = f"{base_filename}_{output_pattern}"
            output_path = os.path.join(output_dir, output_filename)
            for stokes in stokes_list:
                if full_stokes:
                    root, ext = os.path.splitext(output_path)
                    stokes_output = f"{root}_{stokes}{ext}"
                else:
                    stokes_output = output_path
                tasks.append((input_file, stokes_output, stokes, f"{i}_{stokes}", target_mode))

        self.status_text.appendPlainText("Starting batch conversion...")
        if use_multiprocessing and len(files_to_process) > 1:
            self.status_text.appendPlainText(
                f"Using multiprocessing with {max_cores} cores"
            )
        self._stokes_per_file = len(stokes_list)
        self._results = {input_file: [] for input_file in files_to_process}
        self.start_batch(
            process_single_file_coordsys,
            tasks,
            processes=max_cores,
            disable=[self.tab_widget],
        )

    def _task_name(self, input_file, stokes):
        name = os.path.basename(input_file)
        return f"{name}, Stokes {stokes}" if self._stokes_per_file > 1 else name

    def batch_result(self, task, result):
        self._results[task[0]].append(result["success"])
        name = self._task_name(task[0], task[2])
        if result["success"]:
            if result.get("skipped"):
                self.status_text.appendPlainText(
                    f"  - {name}: already in the target coordinates, copied"
                )
            else:
                self.status_text.appendPlainText(f"  - {name}: Converted successfully")
        else:
            error = result["error"] or "Unknown error"
            self.status_text.appendPlainText(f"  - {name}: Error: {error}")

    def batch_error(self, task, message):
        self._results[task[0]].append(False)
        self.status_text.appendPlainText(
            f"  - {self._task_name(task[0], task[2])}: Error: {message}"
        )

    def batch_finished(self, aborted):
        if aborted:
            return
        converted = sum(
            1
            for done in self._results.values()
            if len(done) == self._stokes_per_file and all(done)
        )
        partly = sum(1 for done in self._results.values() if any(done) and not all(done))
        summary = (
            f"Batch conversion completed:\n"
            f"Total files: {len(self._results)}\n"
            f"Successfully converted: {converted}\n"
            + (f"Partly converted: {partly}\n" if partly else "")
            + f"Failed: {len(self._results) - converted - partly}"
        )
        self.status_text.appendPlainText("\n" + summary)
        QMessageBox.information(self, "Conversion Complete", summary)

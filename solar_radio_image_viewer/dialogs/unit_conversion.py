from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
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
import multiprocessing
import os

from ..ui.batch import BatchDialogMixin
from ..ui.styles import set_hand_cursor


class UnitConversionDialog(BatchDialogMixin, QDialog):
    """Dialog for batch unit conversion between Flux Density and Brightness Temperature."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Batch Unit Conversion")
        self.setMinimumSize(600, 550)
        self.resize(750, 650)

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

        self.setup_ui(text_secondary)
        set_hand_cursor(self)

    def setup_ui(self, text_secondary):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # Header description
        header = QLabel(
            "Batch process multiple FITS/CASA files between Flux Density (Jy/beam) and Brightness Temperature (K)."
        )
        header.setWordWrap(True)
        header.setStyleSheet(
            f"font-style: italic; color: {text_secondary}; padding: 4px 0;"
        )
        main_layout.addWidget(header)

        # ========== TAB WIDGET ==========
        self.tab_widget = QTabWidget()
        from PyQt5.QtWidgets import QScrollArea

        # ========== TAB 1: FILES ==========
        files_tab = QWidget()
        files_layout = QVBoxLayout(files_tab)
        files_layout.setSpacing(16)
        files_layout.setContentsMargins(16, 16, 16, 16)

        input_group = QGroupBox("Input Location")
        input_layout = QVBoxLayout(input_group)
        input_layout.setSpacing(12)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(12)
        dir_label = QLabel("Directory:")
        dir_label.setStyleSheet("font-weight: 600;")
        dir_label.setMinimumWidth(70)
        dir_row.addWidget(dir_label)

        self.dir_edit = QLineEdit()
        self.dir_edit.setPlaceholderText("Select input directory...")
        dir_row.addWidget(self.dir_edit, 1)

        self.dir_browse_btn = QPushButton("Browse...")
        self.dir_browse_btn.clicked.connect(self.browse_directory)
        dir_row.addWidget(self.dir_browse_btn)
        input_layout.addLayout(dir_row)

        pattern_row = QHBoxLayout()
        pattern_row.setSpacing(12)
        pattern_label = QLabel("Pattern:")
        pattern_label.setStyleSheet("font-weight: 600;")
        pattern_label.setMinimumWidth(70)
        pattern_row.addWidget(pattern_label)

        self.pattern_edit = QLineEdit("*.fits")
        self.pattern_edit.setPlaceholderText("e.g., *.fits, sun_*.fits")
        pattern_row.addWidget(self.pattern_edit, 1)

        scan_btn = QPushButton("🔍")
        scan_btn.setFixedWidth(36)
        scan_btn.setToolTip("Scan for matching files")
        scan_btn.clicked.connect(self.scan_files)
        pattern_row.addWidget(scan_btn)

        preview_btn = QPushButton("Preview")
        preview_btn.setToolTip("Show list of files matching the pattern")
        preview_btn.clicked.connect(self.preview_files)
        pattern_row.addWidget(preview_btn)
        
        input_layout.addLayout(pattern_row)

        self.files_count_label = QLabel("")
        self.files_count_label.setStyleSheet(
            f"color: {text_secondary}; font-style: italic; padding-left: 82px;"
        )
        input_layout.addWidget(self.files_count_label)
        
        files_layout.addWidget(input_group)
        files_layout.addStretch()

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

        settings_group = QGroupBox("Conversion Settings")
        settings_layout = QVBoxLayout(settings_group)
        settings_layout.setSpacing(12)

        unit_row = QHBoxLayout()
        unit_label = QLabel("Target Unit:")
        unit_label.setMinimumWidth(80)
        unit_label.setStyleSheet("font-weight: 600;")
        unit_row.addWidget(unit_label)

        self.target_unit_combo = QComboBox()
        self.target_unit_combo.addItems([
            "Flux Density (Jy/beam)",
            "Brightness Temperature (K)"
        ])
        self.target_unit_combo.currentIndexChanged.connect(self._update_default_output_dir)
        unit_row.addWidget(self.target_unit_combo, 1)
        settings_layout.addLayout(unit_row)
        options_layout.addWidget(settings_group)

        mp_group = QGroupBox("Performance")
        mp_layout = QVBoxLayout(mp_group)
        mp_layout.setSpacing(12)

        self.multiprocessing_check = QCheckBox("Use multiprocessing")
        self.multiprocessing_check.setChecked(True)
        mp_layout.addWidget(self.multiprocessing_check)

        cores_row = QHBoxLayout()
        cores_row.setSpacing(20)
        cores_label = QLabel("CPU cores:")
        cores_label.setStyleSheet("font-weight: 600;")
        cores_row.addWidget(cores_label)

        self.cores_spinbox = QSpinBox()
        self.cores_spinbox.setRange(1, multiprocessing.cpu_count())
        self.cores_spinbox.setValue(max(1, multiprocessing.cpu_count() - 1))
        cores_row.addWidget(self.cores_spinbox)
        cores_row.addStretch()
        mp_layout.addLayout(cores_row)
        
        self.multiprocessing_check.toggled.connect(self.cores_spinbox.setEnabled)
        options_layout.addWidget(mp_group)
        options_layout.addStretch()

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

        output_group = QGroupBox("Destination Location")
        out_layout = QVBoxLayout(output_group)
        out_layout.setSpacing(12)

        out_dir_row = QHBoxLayout()
        out_dir_row.setSpacing(12)
        out_dir_label = QLabel("Directory:")
        out_dir_label.setStyleSheet("font-weight: 600;")
        out_dir_label.setMinimumWidth(70)
        out_dir_row.addWidget(out_dir_label)

        self.out_dir_edit = QLineEdit("")
        self.out_dir_edit.setPlaceholderText("Subdirectory name or full path")
        out_dir_row.addWidget(self.out_dir_edit, 1)

        self.out_dir_browse_btn = QPushButton("Browse...")
        self.out_dir_browse_btn.clicked.connect(self.browse_output_directory)
        out_dir_row.addWidget(self.out_dir_browse_btn)
        out_layout.addLayout(out_dir_row)

        out_pat_row = QHBoxLayout()
        out_pat_row.setSpacing(12)
        out_pat_label = QLabel("Pattern:")
        out_pat_label.setStyleSheet("font-weight: 600;")
        out_pat_label.setMinimumWidth(70)
        out_pat_row.addWidget(out_pat_label)

        self.output_pattern_edit = QLineEdit("conv_*.fits")
        self.output_pattern_edit.setPlaceholderText("e.g., conv_*.fits")
        out_pat_row.addWidget(self.output_pattern_edit, 1)
        out_layout.addLayout(out_pat_row)

        help_group = QGroupBox("Pattern Help")
        help_layout = QVBoxLayout(help_group)
        help_text = QLabel(
            "Use <b>*</b> as a placeholder for the original filename."
        )
        help_text.setWordWrap(True)
        help_text.setStyleSheet(f"color: {text_secondary}; line-height: 1.5;")
        help_layout.addWidget(help_text)

        output_layout.addWidget(output_group)
        output_layout.addWidget(help_group)
        output_layout.addStretch()

        output_scroll = QScrollArea()
        output_scroll.setWidgetResizable(True)
        output_scroll.setFrameShape(QFrame.NoFrame)
        output_scroll.setWidget(output_tab)
        self.tab_widget.addTab(output_scroll, "💾 Output")

        main_layout.addWidget(self.tab_widget, 1)

        # ========== STATUS SECTION ==========
        status_group = QGroupBox("Status / Results")
        status_layout = QVBoxLayout(status_group)
        status_layout.setContentsMargins(10, 12, 10, 10)

        self.status_text = QPlainTextEdit()
        self.status_text.setReadOnly(True)
        self.status_text.setMinimumHeight(80)
        self.status_text.setMaximumHeight(150)
        status_layout.addWidget(self.status_text)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        status_layout.addWidget(self.progress_bar)
        main_layout.addWidget(status_group)

        # ========== BUTTONS ==========
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok_button = button_box.button(QDialogButtonBox.Ok)
        self.ok_button.setText("Run Conversion")
        self.cancel_button = button_box.button(QDialogButtonBox.Cancel)
        self.cancel_button.setText("Close")
        
        self.ok_button.clicked.connect(self.run_conversion)
        self.cancel_button.clicked.connect(self.cancel_or_abort)
        main_layout.addWidget(button_box)

    def _update_default_output_dir(self):
        """Update default output directory based on target unit"""
        input_dir = self.dir_edit.text()
        if not input_dir:
            return
            
        target_unit_text = self.target_unit_combo.currentText()
        is_target_tb = "Brightness Temperature" in target_unit_text
        
        target_sub = "temperature" if is_target_tb else "flux"
        pattern = "tb_*.fits" if is_target_tb else "flux_*.fits"
            
        current_out = self.out_dir_edit.text()
        if not current_out or current_out.endswith("/temperature") or current_out.endswith("/flux") or current_out.endswith("\\temperature") or current_out.endswith("\\flux"):
            self.out_dir_edit.setText(os.path.join(input_dir, target_sub))
            self.output_pattern_edit.setText(pattern)

    def browse_directory(self):
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select Input Directory", self.dir_edit.text()
        )
        if dir_path:
            self.dir_edit.setText(dir_path)
            self._update_default_output_dir()
            self.scan_files()

    def browse_output_directory(self):
        current = self.out_dir_edit.text()
        if not os.path.isabs(current) and self.dir_edit.text():
            current = os.path.join(self.dir_edit.text(), current)
            
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select Output Directory", current
        )
        if dir_path:
            self.out_dir_edit.setText(dir_path)

    def scan_files(self):
        import glob
        dir_path = self.dir_edit.text()
        pattern = self.pattern_edit.text()
        
        if not dir_path or not pattern:
            self.files_count_label.setText("⚠️ Please specify directory and pattern")
            self.files_count_label.setStyleSheet("color: #f44336; font-weight: bold; padding-left: 82px;")
            return []
            
        try:
            full_pattern = os.path.join(dir_path, pattern)
            files = sorted(glob.glob(full_pattern))
            
            if files:
                self.files_count_label.setText(f"✓ Found {len(files)} matching files")
                self.files_count_label.setStyleSheet("color: #4CAF50; font-weight: bold; padding-left: 82px;")
                self.status_text.setPlainText(f"Found {len(files)} files matching the pattern.")
            else:
                self.files_count_label.setText("⚠️ No matching files found")
                self.files_count_label.setStyleSheet("color: #f44336; font-weight: bold; padding-left: 82px;")
                self.status_text.setPlainText(f"No files found matching: {pattern}")
                
            return files
        except Exception as e:
            self.files_count_label.setText(f"⚠️ Error: {str(e)}")
            return []

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
            import glob
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
            import traceback
            traceback.print_exc()

    def log(self, message):
        self.status_text.appendPlainText(message)
        # Scroll to bottom
        scrollbar = self.status_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def run_conversion(self):
        files = self.scan_files()
        if not files:
            QMessageBox.warning(self, "Error", "No input files found. Please check your directory and pattern.")
            return

        out_dir = self.out_dir_edit.text()
        out_pattern = self.output_pattern_edit.text()
        
        target_unit_text = self.target_unit_combo.currentText()
        is_target_tb = "Brightness Temperature" in target_unit_text
        use_mp = self.multiprocessing_check.isChecked()
        cores = self.cores_spinbox.value()

        if not out_pattern:
            QMessageBox.warning(self, "Error", "Please specify output pattern.")
            return

        self.status_text.clear()
        self.log(f"Starting conversion of {len(files)} files...")
        self.log(f"Target: {'Brightness Temperature (K)' if is_target_tb else 'Flux Density (Jy/beam)'}")

        tasks = []
        for f in files:
            basename = os.path.basename(f)
            
            # Determine absolute output directory
            abs_out_dir = out_dir
            if not os.path.isabs(out_dir):
                abs_out_dir = os.path.join(os.path.dirname(f), out_dir)

            # The output is FITS: "conv_*.fits" makes a.fits into conv_a.fits
            name = os.path.splitext(basename)[0]
            outfile_name = out_pattern.replace("*", name) if "*" in out_pattern else f"{out_pattern}_{name}"
            if not os.path.splitext(outfile_name)[1]:
                outfile_name += ".fits"
            outfile = os.path.join(abs_out_dir, outfile_name)
            
            if not os.path.exists(abs_out_dir):
                try:
                    os.makedirs(abs_out_dir, exist_ok=True)
                except Exception as e:
                    self.log(f"Warning: Could not create dir {abs_out_dir}: {e}")
            
            tasks.append((f, outfile, is_target_tb))

        if use_mp:
            self.log(f"Using multiprocessing ({cores} cores)")
        self._success_count = 0
        self.start_batch(
            _unit_conversion_worker,
            tasks,
            processes=cores if use_mp else 1,
            disable=[self.tab_widget],
        )

    def batch_result(self, task, value):
        success, in_f, msg = value
        if success:
            self._success_count += 1
        self.log(msg)

    def batch_finished(self, aborted):
        if aborted:
            return
        total = self.progress_bar.maximum()
        self.log(f"Batch processing complete. Successfully processed: {self._success_count}/{total}")
        QMessageBox.information(
            self,
            "Batch Complete",
            f"Conversion finished.\nSuccessfully processed: {self._success_count}/{total} files.",
        )

def _unit_conversion_worker(args):
    """Worker function for unit conversion batch processing."""
    infile, outfile, is_target_tb = args
    import os
    import shutil
    try:
        from ..processing.metadata import get_image_metadata
        from ..processing.maps import generate_tb_map, generate_flux_map
        
        # Read metadata to check current unit
        meta = get_image_metadata(infile)
        current_unit = ""
        if "image" in meta and "Units" in meta["image"]:
            current_unit = meta["image"]["Units"].strip().lower()
            
        if not current_unit:
            # Fallback if metadata fails
            return False, infile, f"Skip {os.path.basename(infile)}: Could not read units"

        is_current_tb = current_unit == "k"
        is_current_flux = "jy" in current_unit and "beam" in current_unit

        # Check if already in target unit
        if (is_target_tb and is_current_tb) or (not is_target_tb and is_current_flux):
            # Just copy the file
            if os.path.isdir(infile):
                if os.path.exists(outfile):
                    shutil.rmtree(outfile)
                shutil.copytree(infile, outfile)
            else:
                shutil.copy2(infile, outfile)
            return True, infile, f"Copy {os.path.basename(infile)}: Already in desired unit"
            
        elif is_target_tb and is_current_flux:
            # Convert Jy/beam to K
            tb_data, result = generate_tb_map(infile, outfile=outfile)
            if tb_data is None:
                return False, infile, f"Error {os.path.basename(infile)}: {result}"
            return True, infile, f"Convert {os.path.basename(infile)} -> K"
            
        elif not is_target_tb and is_current_tb:
            # Convert K to Jy/beam
            flux_data, result = generate_flux_map(infile, outfile=outfile)
            if flux_data is None:
                return False, infile, f"Error {os.path.basename(infile)}: {result}"
            return True, infile, f"Convert {os.path.basename(infile)} -> Jy/beam"
            
        else:
            return False, infile, f"Skip {os.path.basename(infile)}: Unsupported unit '{current_unit}'"
            
    except Exception as e:
        return False, infile, f"Failed {os.path.basename(infile)}: {str(e)}"


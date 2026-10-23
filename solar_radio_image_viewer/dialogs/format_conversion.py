from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QLineEdit,
    QLabel,
    QPushButton,
    QSpinBox,
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
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import QTimer
import multiprocessing
import os

from ..ui.batch import BatchDialogMixin
from ..ui.icons import themed_icon
from ..ui.widgets import button_bar, section
from ..ui.styles import set_hand_cursor, set_tone


from ..ui.geometry import fit_to_screen


class FormatConversionDialog(BatchDialogMixin, QDialog):
    """Dialog for batch format conversion between CASA Images and FITS files."""

    def __init__(self, parent=None, current_file=None):
        super().__init__(parent)
        self.setWindowTitle("Batch Format Conversion")
        self.setMinimumSize(600, 500)
        fit_to_screen(self, 750, 650)
        self.parent = parent
        self.current_file = current_file

        try:
            from ..ui.styles import theme_manager
        except ImportError:
            from ..ui.styles import theme_manager
            
        palette = theme_manager.palette
        text_secondary = palette.get("text_secondary", palette["disabled"])
        
        self.setup_ui(text_secondary)
        set_hand_cursor(self)

    def setup_ui(self, text_secondary):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # Header description
        header = QLabel(
            "Batch convert multiple files between CASA Image format and standard FITS files."
        )
        header.setWordWrap(True)
        header.setObjectName("Hint")
        main_layout.addWidget(header)

        # ========== TAB WIDGET ==========
        self.tab_widget = QTabWidget()
        from PyQt5.QtWidgets import QScrollArea

        # ========== TAB 1: FILES ==========
        files_tab = QWidget()
        files_layout = QVBoxLayout(files_tab)
        files_layout.setSpacing(16)
        files_layout.setContentsMargins(16, 16, 16, 16)

        input_group, input_layout = section("Input Location")
        input_layout.setSpacing(12)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(12)
        dir_label = QLabel("Directory:")
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
        input_layout.addLayout(dir_row)

        pattern_row = QHBoxLayout()
        pattern_row.setSpacing(12)
        pattern_label = QLabel("Pattern:")
        pattern_label.setMinimumWidth(70)
        pattern_row.addWidget(pattern_label)

        self.pattern_edit = QLineEdit("*.fits")
        self.pattern_edit.setPlaceholderText("e.g., *.fits, *.image, sun_*")
        pattern_row.addWidget(self.pattern_edit, 1)

        scan_btn = QPushButton()
        scan_btn.setIcon(QIcon(themed_icon("search.png")))
        scan_btn.setObjectName("IconOnlyButton")
        scan_btn.setToolTip("Scan for matching files")
        scan_btn.clicked.connect(self.scan_files)
        pattern_row.addWidget(scan_btn)

        preview_btn = QPushButton("Preview")
        preview_btn.setToolTip("Show list of files matching the pattern")
        preview_btn.clicked.connect(self.preview_files)
        pattern_row.addWidget(preview_btn)
        
        input_layout.addLayout(pattern_row)

        self.files_count_label = QLabel("")
        self.files_count_label.setObjectName("Hint")
        input_layout.addWidget(self.files_count_label)
        
        files_layout.addWidget(input_group)
        files_layout.addStretch()

        files_scroll = QScrollArea()

        files_scroll.setSizeAdjustPolicy(QScrollArea.AdjustToContents)
        files_scroll.setWidgetResizable(True)
        files_scroll.setFrameShape(QFrame.NoFrame)
        files_scroll.setWidget(files_tab)
        self.tab_widget.addTab(files_scroll, "Files")

        # ========== TAB 2: OPTIONS ==========
        options_tab = QWidget()
        options_layout = QVBoxLayout(options_tab)
        options_layout.setSpacing(16)
        options_layout.setContentsMargins(16, 16, 16, 16)

        settings_group, settings_layout = section("Target Format Settings")
        settings_layout.setSpacing(12)

        format_row = QHBoxLayout()
        format_label = QLabel("Convert To:")
        format_label.setMinimumWidth(80)
        format_row.addWidget(format_label)

        self.target_format_combo = QComboBox()
        self.target_format_combo.addItems([
            "CASA Image",
            "FITS File"
        ])
        self.target_format_combo.currentIndexChanged.connect(self._update_default_output_dir)
        format_row.addWidget(self.target_format_combo, 1)
        settings_layout.addLayout(format_row)
        options_layout.addWidget(settings_group)

        mp_group, mp_layout = section("Performance")
        mp_layout.setSpacing(12)

        self.multiprocessing_check = QCheckBox("Use multiprocessing")
        self.multiprocessing_check.setChecked(True)
        mp_layout.addWidget(self.multiprocessing_check)

        cores_row = QHBoxLayout()
        cores_row.setSpacing(20)
        cores_label = QLabel("CPU cores:")
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

        options_scroll.setSizeAdjustPolicy(QScrollArea.AdjustToContents)
        options_scroll.setWidgetResizable(True)
        options_scroll.setFrameShape(QFrame.NoFrame)
        options_scroll.setWidget(options_tab)
        self.tab_widget.addTab(options_scroll, "Options")

        # ========== TAB 3: OUTPUT ==========
        output_tab = QWidget()
        output_layout = QVBoxLayout(output_tab)
        output_layout.setSpacing(16)
        output_layout.setContentsMargins(16, 16, 16, 16)

        out_group, out_layout = section("Output Settings")
        out_layout.setSpacing(12)

        out_dir_row = QHBoxLayout()
        out_dir_row.setSpacing(12)
        out_dir_label = QLabel("Directory:")
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
        out_pat_label.setMinimumWidth(70)
        out_pat_row.addWidget(out_pat_label)

        self.output_pattern_edit = QLineEdit("*.image")
        self.output_pattern_edit.setPlaceholderText("e.g. *.image or *.fits")
        out_pat_row.addWidget(self.output_pattern_edit, 1)
        out_layout.addLayout(out_pat_row)

        help_group, help_layout = section("Pattern Help")
        help_text = QLabel(
            "Use <b>*</b> as a placeholder for the original filename (e.g., target_*.fits)."
        )
        help_text.setWordWrap(True)
        help_text.setObjectName("Hint")
        help_layout.addWidget(help_text)
        
        output_layout.addWidget(out_group)
        output_layout.addWidget(help_group)
        output_layout.addStretch()

        output_scroll = QScrollArea()

        output_scroll.setSizeAdjustPolicy(QScrollArea.AdjustToContents)
        output_scroll.setWidgetResizable(True)
        output_scroll.setFrameShape(QFrame.NoFrame)
        output_scroll.setWidget(output_tab)
        self.tab_widget.addTab(output_scroll, "Output")

        main_layout.addWidget(self.tab_widget)

        # ========== STATUS PANEL (ALWAYS VISIBLE) ==========
        status_group = QGroupBox("Status")
        status_layout = QVBoxLayout(status_group)
        status_layout.setContentsMargins(10, 12, 10, 10)

        self.status_text = QPlainTextEdit()
        self.status_text.setReadOnly(True)
        self.status_text.setPlaceholderText("Ready. Select input directory and target format.")
        self.status_text.setMinimumHeight(self.status_text.fontMetrics().lineSpacing() * 4 + 12)
        status_layout.addWidget(self.status_text)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(True)
        status_layout.addWidget(self.progress_bar)
        
        main_layout.addWidget(status_group, 1)

        box, buttons = button_bar(
            primary=("Run Conversion", self.run_conversion),
            secondary=("Cancel", self.cancel_or_abort),  # aborts while converting
        )
        self.ok_button = buttons["Run Conversion"]
        self.cancel_button = buttons["Cancel"]
        main_layout.addWidget(box)
        
        # Trigger initial autofill
        if self.current_file:
            QTimer.singleShot(100, self._update_default_output_dir)

    def _update_default_output_dir(self):
        """Update default output directory based on target format"""
        input_dir = self.dir_edit.text()
        if not input_dir:
            return
            
        target_format = self.target_format_combo.currentText()
        is_target_fits = target_format == "FITS File"
        
        target_sub = "converted_fits" if is_target_fits else "converted_casa"
        pattern = "*.fits" if is_target_fits else "*.image"
            
        current_out = self.out_dir_edit.text()
        if not current_out or current_out.endswith("/converted_fits") or current_out.endswith("/converted_casa") or current_out.endswith("\\converted_fits") or current_out.endswith("\\converted_casa"):
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
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select Output Directory", self.out_dir_edit.text() or self.dir_edit.text()
        )
        if dir_path:
            self.out_dir_edit.setText(dir_path)

    def scan_files(self):
        import glob
        dir_path = self.dir_edit.text()
        pattern = self.pattern_edit.text()
        
        if not dir_path or not pattern:
            self.files_count_label.setText("Please specify directory and pattern")
            set_tone(self.files_count_label, "error")
            return []
            
        try:
            full_pattern = os.path.join(dir_path, pattern)
            files = sorted(glob.glob(full_pattern))
            
            if files:
                self.files_count_label.setText(f"Found {len(files)} matching files")
                set_tone(self.files_count_label, "success")
                self.status_text.setPlainText(f"Found {len(files)} files matching the pattern.")
            else:
                self.files_count_label.setText("No matching files found")
                set_tone(self.files_count_label, "error")
                self.status_text.setPlainText(f"No files found matching: {pattern}")
                
            return files
        except Exception as e:
            self.files_count_label.setText(f"Error: {str(e)}")
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
            input_pattern = os.path.join(input_dir, pattern)
            matching_files = sorted(glob.glob(input_pattern))

            if not matching_files:
                QMessageBox.information(
                    self,
                    "No Files Found",
                    f"No files found matching pattern:\n{input_pattern}",
                )
                return

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
        except Exception as e:
            self.status_text.setPlainText(f"Error previewing files: {str(e)}")

    def log(self, message):
        self.status_text.appendPlainText(message)
        scrollbar = self.status_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def run_conversion(self):
        files = self.scan_files()
        if not files:
            QMessageBox.warning(self, "Error", "No input files found. Please check your directory and pattern.")
            return

        out_dir = self.out_dir_edit.text()
        out_pattern = self.output_pattern_edit.text()
        
        target_format = self.target_format_combo.currentText()
        is_target_fits = target_format == "FITS File"
        use_mp = self.multiprocessing_check.isChecked()
        cores = self.cores_spinbox.value()

        if not out_pattern:
            QMessageBox.warning(self, "Error", "Please specify output pattern.")
            return

        self.status_text.clear()
        self.log(f"Starting formatting of {len(files)} files...")
        self.log(f"Target format: {'FITS' if is_target_fits else 'CASA Image'}")

        tasks = []
        for f in files:
            basename = os.path.basename(f)
            
            # Strip extension carefully for matching dynamic pattern replacement
            name, ext = os.path.splitext(basename)
            if os.path.isdir(f) and not ext:  # Some CASA images have no ext or `.image`
                name = basename
            
            # Determine absolute output directory
            abs_out_dir = out_dir
            if not os.path.isabs(out_dir):
                abs_out_dir = os.path.join(os.path.dirname(f), out_dir)
                
            outfile_name = out_pattern.replace("*", name) if "*" in out_pattern else f"{out_pattern}_{name}"
            outfile = os.path.join(abs_out_dir, outfile_name)
            
            if not os.path.exists(abs_out_dir):
                try:
                    os.makedirs(abs_out_dir, exist_ok=True)
                except Exception as e:
                    self.log(f"Warning: Could not create dir {abs_out_dir}: {e}")
            
            tasks.append((f, outfile, is_target_fits))

        if use_mp:
            self.log(f"Using multiprocessing ({cores} cores)")
        self._success_count = 0
        self.start_batch(
            _format_conversion_worker,
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

def _format_conversion_worker(args):
    """Worker function for format conversion batch processing via isolated casatasks subprocesses."""
    infile, outfile, is_target_fits = args
    import os
    import shutil

    from ..processing.casa_tasks import CasaTaskError, run_casa_task

    try:
        infile_abs = os.path.abspath(infile)
        outfile_abs = os.path.abspath(outfile)
        
        # Check source format simply by looking at whether it's a file or directory
        # CASA images are directories (with table structure inside)
        # FITS files are always individual files.
        is_source_fits = os.path.isfile(infile_abs)
        is_source_casa = os.path.isdir(infile_abs)
        
        if is_target_fits and is_source_fits:
            shutil.copy2(infile_abs, outfile_abs)
            return True, infile, f"Skip (already FITS) -> Copied {os.path.basename(infile)}"
            
        if not is_target_fits and is_source_casa:
            if os.path.exists(outfile_abs):
                shutil.rmtree(outfile_abs)
            shutil.copytree(infile_abs, outfile_abs)
            return True, infile, f"Skip (already CASA) -> Copied {os.path.basename(infile)}"

        # In a separate process
        if is_target_fits:
            # We are exporting CASA -> FITS
            if os.path.exists(outfile_abs):
                os.remove(outfile_abs)
            task = dict(task="exportfits", imagename=infile_abs, fitsimage=outfile_abs)
        else:
            # We are importing FITS -> CASA
            if os.path.exists(outfile_abs):
                shutil.rmtree(outfile_abs)
            task = dict(task="importfits", fitsimage=infile_abs, imagename=outfile_abs)
        try:
            run_casa_task(overwrite=True, **task)
        except CasaTaskError as e:
            return False, infile, f"Error converting {os.path.basename(infile)}: {e}"

        return True, infile, f"Converted {os.path.basename(infile)} -> {'FITS' if is_target_fits else 'CASA'}"
        
    except Exception as e:
        return False, infile, f"Failed {os.path.basename(infile)}: {str(e)}"



"""
Input tab: the images (a series or one image).
"""

import os
import posixpath
from PyQt5.QtWidgets import (
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QFileDialog,
    QGroupBox,
    QComboBox,
    QMessageBox,
    QScrollArea,
    QWidget,
    QRadioButton,
    QButtonGroup,
)
import glob
from ..creator import (
    observation_sort_key,
)
from ...processing.fits_io import is_casa_image
from ...ui.open_image import choose_image
from .helpers import (
    wait_cursor,
    is_image,
)


class InputTabMixin:
    """Input tab: the images (a series or one image). Mixed into VideoCreationDialog."""

    def _create_input_tab(self, input_layout):
        """Input tab: directory, file pattern, sorting, Stokes. Returns the tab's scroll area."""
        # ------ Input Tab ------
        # Create a scroll area for the input tab
        input_scroll = QScrollArea()
        input_scroll.setWidgetResizable(True)
        input_scroll_content = QWidget()
        input_layout = QVBoxLayout(input_scroll_content)
        input_layout.setContentsMargins(12, 12, 12, 12)
        input_layout.setSpacing(12)
        input_scroll.setWidget(input_scroll_content)

        # Input pattern section
        input_group = QGroupBox("Input Files")
        input_group_layout = QGridLayout(input_group)
        input_group_layout.setContentsMargins(12, 16, 12, 12)
        input_group_layout.setHorizontalSpacing(10)
        input_group_layout.setVerticalSpacing(8)

        # Images on this computer or on a server (ui.remote_source)
        input_layout.addLayout(self._create_images_on_row())

        # 0. Image: a series of files, or one image for every frame
        source_row = QHBoxLayout()
        self.image_series_radio = QRadioButton("Image series (folder + pattern)")
        self.image_single_radio = QRadioButton("Single image")
        self.image_series_radio.setChecked(True)
        self.image_single_radio.setToolTip(
            "The same image in every frame; the frames then follow the first\n"
            "contour layer that is a series"
        )
        source_group = QButtonGroup(self)
        source_group.addButton(self.image_series_radio)
        source_group.addButton(self.image_single_radio)
        source_row.addWidget(self.image_series_radio)
        source_row.addWidget(self.image_single_radio)
        source_row.addStretch()
        input_layout.addLayout(source_row)

        # 1. Directory field
        input_group_layout.addWidget(QLabel("Input Directory:"), 0, 0)
        self.input_directory_edit = QLineEdit()
        input_group_layout.addWidget(self.input_directory_edit, 0, 1)

        browse_dir_btn = QPushButton("Browse")
        browse_dir_btn.clicked.connect(
            lambda: (
                (
                    self.input_directory_edit.setText(os.getcwd())
                    if not self.input_directory_edit.text()
                    else None
                ),
                self.browse_input_directory(),
            )
        )
        input_group_layout.addWidget(browse_dir_btn, 0, 2)

        # 2. Pattern field
        input_group_layout.addWidget(QLabel("File Pattern:"), 1, 0)
        self.input_pattern_edit = QLineEdit()
        self.input_pattern_edit.setPlaceholderText("e.g., *.fits, *_171*.fits or *.image (CASA)")
        input_group_layout.addWidget(self.input_pattern_edit, 1, 1)

        # Scan button
        scan_btn = QPushButton("Scan")
        scan_btn.clicked.connect(self.preview_input_files)
        input_group_layout.addWidget(scan_btn, 1, 2)

        # File sorting
        input_group_layout.addWidget(QLabel("Sort Files By:"), 2, 0)
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["Filename", "Date/Time", "Extension"])
        input_group_layout.addWidget(self.sort_combo, 2, 1)

        # Stokes parameter
        input_group_layout.addWidget(QLabel("Stokes Parameter:"), 3, 0)
        self.stokes_combo = QComboBox()
        self.stokes_combo.addItems(["I", "Q", "U", "V"])
        input_group_layout.addWidget(self.stokes_combo, 3, 1)

        input_layout.addWidget(input_group)

        self.single_image_group = QGroupBox("Single Image")
        single_layout = QHBoxLayout(self.single_image_group)
        self.single_image_edit = QLineEdit()
        self.single_image_edit.setPlaceholderText("FITS or CASA image shown in every frame")
        single_layout.addWidget(self.single_image_edit, 1)
        single_browse = QPushButton("Browse")
        single_browse.setToolTip("Choose a FITS file or CASA image")
        single_browse.clicked.connect(self._browse_single_image)
        single_layout.addWidget(single_browse)
        input_layout.addWidget(self.single_image_group)

        def show_image_source(series):
            input_group.setEnabled(series)
            self.single_image_group.setEnabled(not series)

        self.image_series_radio.toggled.connect(show_image_source)
        show_image_source(True)

        # Files found status (separate from the group) - use theme-compatible styling
        self.files_found_label = QLabel("No files found yet")
        self.files_found_label.setObjectName("StatusLabel")
        input_layout.addWidget(self.files_found_label)
        input_layout.addStretch()
        return input_scroll

    def get_smart_start_directory(self, *extra_paths):
        """
        Get a smart starting directory for file/directory dialogs.

        Checks multiple sources in priority order:
        1. Any extra paths passed as arguments (e.g., current field value)
        2. Contour base file directory (if set)
        3. Reference image directory
        4. Input directory from Input tab
        5. Current file from main viewer
        6. Current working directory (CWD)
        7. Fall back to home directory

        Returns the first valid directory found.
        """
        candidates = list(extra_paths) + [
            self.reference_image,
            (
                self.input_directory_edit.text().strip()
                if hasattr(self, "input_directory_edit")
                else None
            ),
            self.current_file,
            os.getcwd(),  # CWD as fallback before home
        ]

        for path in candidates:
            if path:
                # If it's a file, get its directory
                if os.path.isfile(path):
                    dir_path = os.path.dirname(path)
                else:
                    dir_path = path

                if dir_path and os.path.isdir(dir_path):
                    return dir_path

        return os.path.expanduser("~")

    def browse_input_directory(self):
        """Browse for input directory"""
        if self.remote is not None:
            folder = self._pick_remote("folder", self.input_directory_edit.text())
            if folder:
                self.input_directory_edit.setText(folder)
                self.schedule_preview()
            return
        start_dir = self.get_smart_start_directory(self.input_directory_edit.text())

        directory = QFileDialog.getExistingDirectory(
            self, "Select Input Directory", start_dir
        )

        if directory:
            self.input_directory_edit.setText(directory)

            # Set default output file if not already set
            if not self.output_file_edit.text():
                self.output_file_edit.setText(
                    os.path.join(directory, "output_video.mp4")
                )

            # Preview files if pattern is already set
            if self.input_pattern_edit.text():
                self.preview_input_files()

    def preview_input_files(self):
        """Find the images matching the input pattern and preview them."""
        if self.remote is not None:
            self._forget_remote_lists()
            self.files_found_label.setText(f"Reading the folder on {self.remote.label}…")
            self.schedule_preview(delay=0)
            return
        with wait_cursor():
            if not self.input_directory_edit.text() or not self.input_pattern_edit.text():
                QMessageBox.warning(
                    self,
                    "Incomplete Input",
                    "Please specify both directory and pattern.",
                )
                return
            files, problem = self._input_files()
            if problem:
                self.files_found_label.setText(problem[0])
                self.create_btn.setEnabled(False)
                self.create_btn.setToolTip(problem[1])
                QMessageBox.warning(self, *problem)
                return

            casa = sum(is_casa_image(f) for f in files)
            self.files_found_label.setText(
                f"Found {len(files)} images matching the pattern"
                + (f" ({casa} CASA)" if casa else "")
            )
            self.create_btn.setEnabled(True)
            self.create_btn.setToolTip("")

            # The first image is previewed unless one of them already is
            if not self.reference_image or self.reference_image not in files:
                self.reference_image = files[0]
                self.reference_image_edit.setText(self.reference_image)
            self.update_preview(self.reference_image)

    def browse_reference_image(self):
        """Browse for a reference image to use for preview and settings"""
        if self.reference_image:
            start_dir = os.path.dirname(self.reference_image)
        elif self.input_directory_edit.text():
            start_dir = self.input_directory_edit.text()
        elif self.current_file:
            start_dir = os.path.dirname(self.current_file)
        else:
            start_dir = os.path.expanduser("~")

        filepath = choose_image(self, "Select Reference Image", start_dir)

        if filepath:
            self.reference_image = filepath
            self.reference_image_edit.setText(filepath)
            self.update_preview(filepath)

    def _input_files(self):
        """(files, problem): the Input tab's files in the chosen order, or
        ([], a message saying why there are none)."""
        input_dir = self.input_directory_edit.text()
        if self.remote is not None:
            return self._remote_input_files(input_dir)
        if not input_dir or not os.path.isdir(input_dir):
            return [], (
                "Invalid Directory",
                "The specified input directory does not exist.",
            )
        if not self.input_pattern_edit.text().strip():
            return [], ("No Pattern", "Enter a file pattern, e.g. *.fits or *.image")
        input_path = os.path.join(input_dir, self.input_pattern_edit.text())
        files = glob.glob(input_path)
        if not files:
            return [], ("No Files Found", f"No files match the pattern: {input_path}")
        invalid = [os.path.basename(f) for f in files if not is_image(f)]
        if invalid:
            msg = (
                f"Found {len(invalid)} files that are not images.\n"
                "FITS files and CASA images are supported."
            )
            if len(invalid) > 5:
                msg += "\n\nExamples:\n" + "\n".join(invalid[:5]) + "\n..."
            else:
                msg += "\n\nFiles:\n" + "\n".join(invalid)
            return [], ("Invalid File Extensions", msg)

        sort_method = self.sort_combo.currentText().lower()
        if sort_method == "filename":
            files.sort()
        elif sort_method == "date/time":
            # Observation time (header or file name), not file dates
            files.sort(key=observation_sort_key)
        elif sort_method == "extension":
            files.sort(key=lambda x: os.path.splitext(x)[1])
        return files, None

    def _remote_input_files(self, folder):
        """_input_files for images on a server."""
        pattern = self.input_pattern_edit.text().strip()
        if not folder:
            return [], ("No Folder", "Choose the folder of the images on the server.")
        if not pattern:
            return [], ("No Pattern", "Enter a file pattern, e.g. *.fits or *.image")
        files = self._remote_list(folder, pattern)
        if files is None:
            return [], ("Reading the Server", f"Reading {folder} on {self.remote.label}…")
        if not files:
            return [], ("No Files Found", f"No images match {pattern} in {folder} on the server")
        sort_method = self.sort_combo.currentText().lower()
        if sort_method == "date/time":
            files = sorted(files, key=self._remote_sort_key)
        elif sort_method == "extension":
            files = sorted(files, key=lambda x: posixpath.splitext(x)[1])
        else:
            files = sorted(files)
        return files, None

    def _browse_single_image(self):
        if self.remote is not None:
            path = self._pick_remote("file", posixpath.dirname(self.single_image_edit.text()))
            if path:
                self.single_image_edit.setText(path)
            return
        path = choose_image(
            self,
            "Select the Image",
            self.get_smart_start_directory(self.single_image_edit.text()),
        )
        if path:
            self.single_image_edit.setText(path)

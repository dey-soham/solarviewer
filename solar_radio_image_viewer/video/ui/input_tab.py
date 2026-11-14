"""
Input tab: the images (a series or one image).
"""

import os
import posixpath
from PyQt5.QtWidgets import (
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLineEdit,
    QPushButton,
    QFileDialog,
    QGroupBox,
    QComboBox,
    QMessageBox,
    QScrollArea,
    QWidget,
    QButtonGroup,
)
from ...ui.activity import ActivityLabel
from ...ui.open_image import choose_image
from ...ui.widgets import PathEdit, segmented
from .helpers import hint


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
        input_layout.setContentsMargins(0, 12, 8, 12)
        input_layout.setSpacing(12)
        input_scroll.setWidget(input_scroll_content)

        # Input pattern section
        input_group = QGroupBox("Image series")
        input_group_layout = QGridLayout(input_group)
        input_group_layout.setContentsMargins(12, 4, 12, 4)
        input_group_layout.setHorizontalSpacing(8)
        input_group_layout.setVerticalSpacing(8)

        top_grid = QGridLayout()
        top_grid.setHorizontalSpacing(8)
        top_grid.setVerticalSpacing(8)
        top_grid.setColumnStretch(1, 1)

        # Images on this computer or on a server (ui.remote_source), and
        # starting from the main window's view
        images_on_row = self._create_images_on_row()
        use_view_btn = QPushButton("Use current view")
        use_view_btn.setToolTip(
            "Take the files, colormap, stretch, range, visible area and\n"
            "contours of the image open in the main window"
        )
        use_view_btn.setEnabled(self._main_window is not None)
        use_view_btn.clicked.connect(self._use_current_view)
        images_on_row.addWidget(use_view_btn)
        top_grid.addWidget(hint("Images on"), 0, 0)
        top_grid.addLayout(images_on_row, 0, 1)

        # 0. Image: a series of files, or one image for every frame (the
        # names are the keys of saved settings files)
        self.image_series_radio = QPushButton("Image series")
        self.image_single_radio = QPushButton("Single image")
        self.image_series_radio.setToolTip(
            "A folder and a file pattern: one frame for each image found"
        )
        self.image_single_radio.setToolTip(
            "The same image in every frame; the frames then follow the first\n"
            "contour layer that is a series"
        )
        source_group = QButtonGroup(self)
        for button in (self.image_series_radio, self.image_single_radio):
            button.setCheckable(True)
            button.setAutoDefault(False)
            source_group.addButton(button)
        self.image_series_radio.setChecked(True)
        self.image_series_radio.toggled.connect(self.schedule_preview)
        source_row = QHBoxLayout()
        source_row.addWidget(
            segmented([self.image_series_radio, self.image_single_radio], role="HeaderSegmented")
        )
        source_row.addStretch()
        top_grid.addWidget(hint("Show"), 1, 0)
        top_grid.addLayout(source_row, 1, 1)

        # Stokes parameter (of either kind of image)
        self.stokes_combo = QComboBox()
        self.stokes_combo.addItems(["I", "Q", "U", "V"])
        stokes_row = QHBoxLayout()
        stokes_row.addWidget(self.stokes_combo)
        stokes_row.addStretch()
        top_grid.addWidget(hint("Stokes"), 2, 0)
        top_grid.addLayout(stokes_row, 2, 1)
        input_layout.addLayout(top_grid)

        # 1. Directory field
        input_group_layout.addWidget(hint("Folder"), 0, 0)
        self.input_directory_edit = PathEdit()
        input_group_layout.addWidget(self.input_directory_edit, 0, 1)

        browse_dir_btn = QPushButton("Browse…")
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
        input_group_layout.addWidget(hint("Pattern"), 1, 0)
        self.input_pattern_edit = QLineEdit()
        self.input_pattern_edit.setPlaceholderText("For example *.fits, *_171*.fits or *.image (CASA)")
        input_group_layout.addWidget(self.input_pattern_edit, 1, 1)

        # Scan button
        scan_btn = QPushButton("Scan")
        scan_btn.clicked.connect(self.preview_input_files)
        input_group_layout.addWidget(scan_btn, 1, 2)

        # File sorting
        input_group_layout.addWidget(hint("Sort by"), 2, 0)
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["Filename", "Date/Time", "Extension"])
        input_group_layout.addWidget(self.sort_combo, 2, 1)

        input_layout.addWidget(input_group)

        self.single_image_group = QGroupBox("Single image")
        single_layout = QHBoxLayout(self.single_image_group)
        single_layout.setContentsMargins(12, 4, 12, 4)
        single_layout.setSpacing(8)
        self.single_image_edit = PathEdit()
        self.single_image_edit.setPlaceholderText("FITS or CASA image shown in every frame")
        single_layout.addWidget(self.single_image_edit, 1)
        single_browse = QPushButton("Browse…")
        single_browse.setToolTip("Choose a FITS file or CASA image")
        single_browse.clicked.connect(self._browse_single_image)
        single_layout.addWidget(single_browse)
        input_layout.addWidget(self.single_image_group)

        def show_image_source(series):
            input_group.setVisible(series)
            self.single_image_group.setVisible(not series)
            self.files_found_label.setVisible(series)

        # Files found: a line of muted text under the group (a framed status
        # line would read as one more field)
        self.files_found_label = ActivityLabel("No files found yet")
        self.files_found_label.label.setObjectName("SecondaryText")
        input_layout.addWidget(self.files_found_label)
        input_layout.addStretch()

        self.image_series_radio.toggled.connect(show_image_source)
        show_image_source(True)
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
            self, "Select input folder", start_dir
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
        """Find the images matching the input pattern and preview them: reads
        the folder again, on the planner's thread (see _on_scanned)."""
        if self.remote is not None:
            self.files_found_label.start(f"Reading the folder on {self.remote.label}…")
        elif not self.input_directory_edit.text() or not self.input_pattern_edit.text():
            QMessageBox.warning(
                self,
                "Incomplete input",
                "Enter both a folder and a file pattern.",
            )
            return
        else:
            self.files_found_label.start("Scanning the folder…")
        self._scanning = True
        self._planner.forget()
        self.schedule_preview(delay=0)

    def _on_scanned(self, plan):
        """Say what a plan found in the Input tab's folder, after a Scan (for
        a server, after any plan: it was read anew)."""
        scanning, self._scanning = self._scanning, False
        label = self.files_found_label
        if self.remote is not None:
            if plan.input_files:
                label.stop(
                    f"Found {len(plan.input_files)} images matching the pattern on {self.remote.label}"
                )
            elif scanning:
                label.stop(plan.input_problem[0] if plan.input_problem else "")
            return
        if not scanning:
            return
        files = plan.input_files
        if not files:
            problem = plan.input_problem or plan.problem
            if not problem:
                label.stop("")
                return
            label.stop(problem[0])
            self.create_btn.setEnabled(False)
            self.create_btn.setToolTip(problem[1])
            QMessageBox.warning(self, *problem)
            return
        label.stop(
            f"Found {len(files)} images matching the pattern"
            + (f" ({plan.casa} CASA)" if plan.casa else "")
        )
        # The first image is previewed unless one of them already is
        if not self.reference_image or self.reference_image not in files:
            self.reference_image = files[0]
            self.reference_image_edit.setText(self.reference_image)
        if self.reference_image in self._preview_files:
            slider = self.preview_panel.slider
            slider.blockSignals(True)
            slider.setValue(self._preview_files.index(self.reference_image))
            slider.blockSignals(False)

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

        filepath = choose_image(self, "Select reference image", start_dir)

        if filepath:
            self.reference_image = filepath
            self.reference_image_edit.setText(filepath)
            self.update_preview(filepath)

    def _browse_single_image(self):
        if self.remote is not None:
            path = self._pick_remote("file", posixpath.dirname(self.single_image_edit.text()))
            if path:
                self.single_image_edit.setText(path)
            return
        path = choose_image(
            self,
            "Select the image",
            self.get_smart_start_directory(self.single_image_edit.text()),
        )
        if path:
            self.single_image_edit.setText(path)

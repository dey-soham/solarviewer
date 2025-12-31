#!/usr/bin/env python3
"""
Basic napari viewer for Fits/CASA images.
"""

import os
import sys
import numpy as np
import napari
from PyQt5.QtWidgets import (
    QApplication,
    QFileDialog,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QLabel,
    QComboBox,
    QRadioButton,
    QButtonGroup,
    QMessageBox,
    QGroupBox,
    QLineEdit,
)
from PyQt5.QtCore import Qt
from .utils import get_pixel_values_from_image


class NapariViewer(QWidget):
    def __init__(self, imagename=None):
        super().__init__()
        self.viewer = None
        self.current_image_data = None
        self.current_wcs = None
        self.psf = None
        self.image_layer = None
        self.imagename = imagename

        self.init_ui()

        # If an image was provided, load it
        if self.imagename:
            self.load_data(self.imagename)
            self.plot_image()

    def init_ui(self):
        self.setWindowTitle("Napari Image Viewer")
        self.resize(1200, 800)

        # Main layout
        main_layout = QHBoxLayout()
        self.setLayout(main_layout)

        # Left column for controls
        left_column = QVBoxLayout()
        main_layout.addLayout(left_column, 1)

        # Right column for napari viewer
        right_column = QVBoxLayout()
        main_layout.addLayout(right_column, 3)

        # File selection group
        file_group = QGroupBox("File Selection")
        file_layout = QVBoxLayout()

        # Add help button
        help_layout = QHBoxLayout()
        help_button = QPushButton("Help")
        help_button.clicked.connect(self.show_help)
        help_layout.addStretch()
        help_layout.addWidget(help_button)
        left_column.addLayout(help_layout)

        # Radio buttons for file type selection
        radio_layout = QVBoxLayout()  # Changed to vertical layout for better spacing
        self.radio_fits = QRadioButton("FITS File")
        self.radio_casa_image = QRadioButton("CASA Image")
        self.radio_fits.setChecked(True)

        self.file_type_group = QButtonGroup()
        self.file_type_group.addButton(self.radio_fits)
        self.file_type_group.addButton(self.radio_casa_image)

        radio_layout.addWidget(self.radio_fits)
        radio_layout.addWidget(self.radio_casa_image)
        file_layout.addLayout(radio_layout)

        # File selection button
        self.file_button = QPushButton("Open Image")
        self.file_button.setMinimumHeight(40)  # Make button taller
        self.file_button.clicked.connect(self.select_file_or_directory)
        file_layout.addWidget(self.file_button)

        # Add a spacer for better layout
        file_layout.addStretch()

        file_group.setLayout(file_layout)
        left_column.addWidget(file_group)

        # Add current file display
        file_info_group = QGroupBox("Current File")
        file_info_layout = QVBoxLayout()
        self.file_label = QLabel("No file loaded")
        self.file_label.setWordWrap(True)
        file_info_layout.addWidget(self.file_label)
        file_info_group.setLayout(file_info_layout)
        left_column.addWidget(file_info_group)

        # Add a spacer at the bottom of the left column
        left_column.addStretch()

        # Display controls group
        display_group = QGroupBox("Display Controls")
        display_layout = QVBoxLayout()

        # Stokes parameter selection
        stokes_layout = QHBoxLayout()
        self.stokes_label = QLabel("Stokes:")
        stokes_layout.addWidget(self.stokes_label)

        self.stokes_combo = QComboBox()
        self.stokes_combo.addItems(
            ["I", "Q", "U", "V", "L", "Lfrac", "Vfrac", "Q/I", "U/I", "U/V"]
        )
        self.stokes_combo.currentTextChanged.connect(self.on_stokes_changed)
        stokes_layout.addWidget(self.stokes_combo)
        display_layout.addLayout(stokes_layout)

        # Add threshold textbox
        threshold_layout = QHBoxLayout()
        self.threshold_label = QLabel("Threshold:")
        threshold_layout.addWidget(self.threshold_label)
        self.threshold_textbox = QLineEdit("10.0")
        threshold_layout.addWidget(self.threshold_textbox)
        self.threshold_textbox.editingFinished.connect(
            lambda: self.on_threshold_changed(self.threshold_textbox.text())
        )
        display_layout.addLayout(threshold_layout)

        # Add a spacer for better layout
        display_layout.addStretch()

        display_group.setLayout(display_layout)
        right_column.addWidget(display_group)

        # Image statistics group
        stats_group = QGroupBox("Image Statistics")
        stats_layout = QVBoxLayout()

        self.stats_label = QLabel("No image loaded")
        stats_layout.addWidget(self.stats_label)

        stats_group.setLayout(stats_layout)
        right_column.addWidget(stats_group)

        # Add a spacer at the bottom of the right column
        right_column.addStretch()

        # Initialize napari viewer
        self.init_napari()

    def init_napari(self):
        """Initialize the napari viewer"""
        self.viewer = napari.Viewer(show=False)
        self.viewer.window.add_dock_widget(self, area="bottom")
        self.viewer.show()

    def select_file_or_directory(self):
        """Open a file dialog to select a FITS file or CASA image directory"""
        if self.radio_casa_image.isChecked():
            # Select CASA image directory
            directory = QFileDialog.getExistingDirectory(
                self, "Select a CASA Image Directory"
            )
            if directory:
                try:
                    self.load_data(directory)
                    self.plot_image()
                except Exception as e:
                    QMessageBox.critical(
                        self, "Error", f"Failed to load CASA image: {str(e)}"
                    )
        else:
            # Select FITS file
            file_path, _ = QFileDialog.getOpenFileName(
                self, "Select a FITS file", "", "FITS files (*.fits);;All files (*)"
            )
            if file_path:
                try:
                    self.load_data(file_path)
                    self.plot_image()
                except Exception as e:
                    QMessageBox.critical(
                        self, "Error", f"Failed to load FITS file: {str(e)}"
                    )

    def load_data(self, imagename, threshold=10):
        """Load data from a FITS file or CASA image directory"""
        stokes = self.stokes_combo.currentText()

        try:
            pix, csys, psf = get_pixel_values_from_image(imagename, stokes, threshold)
            pix = pix.transpose()
            pix = np.flip(pix, axis=0)

            self.current_image_data = pix
            self.current_wcs = csys
            self.psf = psf
            self.imagename = imagename  # Store the imagename for later use

            # Update file label
            self.file_label.setText(
                f"File: {os.path.basename(imagename)}\nType: {'CASA Image' if os.path.isdir(imagename) else 'FITS File'}\nStokes: {stokes}"
            )

            # Update window title with filename
            self.viewer.title = (
                f"Solar Radio Image Viewer (Napari) - {os.path.basename(imagename)}"
            )

        except Exception as e:
            print(f"Error loading data: {e}")
            raise

    def plot_image(self):
        """Display the image in napari"""
        if self.current_image_data is None:
            return

        data = self.current_image_data
        cmap = "yellow"

        # Remove existing layer if it exists
        if self.image_layer is not None:
            self.viewer.layers.remove(self.image_layer)

        # Add the new image layer
        self.image_layer = self.viewer.add_image(
            data,
            name="Image",
            colormap=cmap,
        )

        # Update statistics
        self.update_statistics()

        # Reset view
        self.viewer.reset_view()

    def update_statistics(self):
        """Update the statistics display"""
        if self.current_image_data is None:
            self.stats_label.setText("No image loaded")
            return

        data = self.current_image_data
        min_val = np.nanmin(data)
        max_val = np.nanmax(data)
        mean_val = np.nanmean(data)
        median_val = np.nanmedian(data)
        std_val = np.nanstd(data[0:200, 0:200])
        rms_val = np.sqrt(np.nanmean(data[0:200, 0:200] ** 2))
        positive_DR = max_val / rms_val
        negative_DR = min_val / rms_val

        stats_text = (
            f"Min: {min_val:.4g}                Max: {max_val:.4g}              Mean: {mean_val:.4g}                Median: {median_val:.4g}\n"
            f"Std Dev: {std_val:.4g}            RMS: {rms_val:.4g}\n"
            f"Positive DR: {positive_DR:.4g}    Negative DR: {negative_DR:.4g}\n"
            f"Shape: {data.shape}"
        )

        self.stats_label.setText(stats_text)

    def on_stokes_changed(self, stokes):
        """Handle changes to the Stokes parameter"""
        if self.current_image_data is not None and hasattr(self, "imagename"):
            try:
                self.load_data(self.imagename)
                self.plot_image()
            except Exception as e:
                print(f"Error updating Stokes parameter: {e}")
                QMessageBox.critical(
                    self, "Error", f"Failed to update Stokes parameter: {str(e)}"
                )

    def on_threshold_changed(self, text):
        """Handle changes to the threshold textbox"""
        try:
            if text == "":
                threshold = 10.0
            elif float(text) < 0:
                print("Invalid threshold value")
                QMessageBox.critical(
                    self,
                    "Error",
                    "Invalid threshold value. Please enter a valid number.",
                )
                return
            else:
                threshold = float(text)
            self.load_data(self.imagename, threshold=threshold)
            self.plot_image()
        except ValueError:
            print("Invalid threshold value")
            QMessageBox.critical(
                self, "Error", "Invalid threshold value. Please enter a valid number."
            )

    def show_help(self):
        """Display help information about the Napari viewer"""
        help_text = """
<h2>Napari Fast Viewer Help</h2>

<h3>Overview</h3>
<p>The Napari Fast Viewer is a lightweight tool for quickly visualizing solar radio images.</p>

<h3>Features</h3>
<ul>
    <li><b>Fast Loading:</b> Quickly loads and displays FITS and CASA images</li>
    <li><b>Stokes Parameters:</b> View different Stokes parameters (I, Q, U, V, etc.)</li>
    <li><b>Threshold Control:</b> Adjust threshold for better visualization</li>
    <li><b>Basic Statistics:</b> View basic image statistics</li>
</ul>

<h3>Usage</h3>
<ol>
    <li>Select a file using the "Select File" button</li>
    <li>Choose a Stokes parameter from the dropdown</li>
    <li>Adjust the threshold value if needed</li>
</ol>

<h3>Command Line Usage</h3>
<p>You can launch this viewer directly from the command line:</p>
<pre>
solarviewer -f [image_file]
sv --fast [image_file]
</pre>
"""
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Napari Viewer Help")
        msg_box.setTextFormat(Qt.RichText)
        msg_box.setText(help_text)
        msg_box.setStandardButtons(QMessageBox.Ok)
        msg_box.exec_()


def main(imagename=None):
    """Main function to run the application"""
    # Check if QApplication already exists (when called from main app)
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
        standalone = True
    else:
        standalone = False

    viewer = NapariViewer(imagename)

    # Only exit if running standalone
    if standalone:
        sys.exit(app.exec_())
    else:
        app.exec_()


if __name__ == "__main__":
    main()

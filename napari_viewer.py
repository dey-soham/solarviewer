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
)

# Try to import CASA tools
try:
    from casatools import image as IA

    CASA_AVAILABLE = True
except ImportError:
    print(
        "WARNING: CASA tools not found. This application requires CASA to be installed."
    )
    CASA_AVAILABLE = False
    IA = None


def get_pixel_values_from_image(imagename, stokes="I", threshold=5.0):

    if not CASA_AVAILABLE:
        raise RuntimeError("CASA is not available")

    stokes_map = {"I": 0, "Q": 1, "U": 2, "V": 3}

    try:
        ia_tool = IA()
        ia_tool.open(imagename)
        print("before summary")

        summary = ia_tool.summary()
        print("after summary")
        dimension_names = np.array(summary.get("axisnames"))
        dimension_shapes = summary.get("shape")

        data = ia_tool.getchunk()
        psf = ia_tool.restoringbeam()
        csys = ia_tool.coordsys()

        # Check for Stokes axis
        if "Stokes" in dimension_names:
            stokes_idx = int(np.where(dimension_names == "Stokes")[0][0])
            single_stokes = dimension_shapes[stokes_idx] == 1
        else:
            stokes_idx = None
            single_stokes = True

        # Check for Frequency axis
        if "Frequency" in dimension_names:
            freq_idx = int(np.where(dimension_names == "Frequency")[0][0])
        else:
            freq_idx = None

        # Extract the requested Stokes parameter
        n_dims = len(data.shape)
        if stokes in ["I", "Q", "U", "V"]:
            idx = stokes_map.get(stokes)
            if idx is None:
                raise ValueError(f"Unknown Stokes parameter: {stokes}")

            slice_list = [slice(None)] * n_dims
            if stokes_idx is not None:
                if single_stokes and stokes != "I":
                    raise RuntimeError(
                        "The image is single stokes, but the Stokes parameter is not 'I'."
                    )
                slice_list[stokes_idx] = idx

            if freq_idx is not None:
                slice_list[freq_idx] = 0

            pix = data[tuple(slice_list)]
        else:
            raise ValueError(f"Unsupported Stokes parameter: {stokes}")

    except Exception as e:
        if "ia_tool" in locals():
            ia_tool.close()
        raise RuntimeError(f"Error reading image: {e}")

    ia_tool.close()
    pix = pix.transpose()
    pix = np.flip(pix, axis=0)
    return pix, csys, psf


class NapariViewer(QWidget):
    def __init__(self):
        super().__init__()
        self.viewer = None
        self.current_image_data = None
        self.current_wcs = None
        self.psf = None
        self.image_layer = None

        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("Napari Image Viewer")
        self.setGeometry(100, 100, 1200, 800)

        # Create main layout
        main_layout = (
            QHBoxLayout()
        )  # Changed to horizontal layout for side-by-side columns

        # Left column - File selection controls
        left_column = QVBoxLayout()

        # File selection group
        file_group = QGroupBox("File Selection")
        file_layout = QVBoxLayout()

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

        # Right column - Display controls
        right_column = QVBoxLayout()

        # Display controls group
        display_group = QGroupBox("Display Controls")
        display_layout = QVBoxLayout()

        # Stokes parameter selection
        stokes_layout = QHBoxLayout()
        self.stokes_label = QLabel("Stokes:")
        stokes_layout.addWidget(self.stokes_label)

        self.stokes_combo = QComboBox()
        self.stokes_combo.addItems(["I", "Q", "U", "V"])
        self.stokes_combo.currentTextChanged.connect(self.on_stokes_changed)
        stokes_layout.addWidget(self.stokes_combo)
        display_layout.addLayout(stokes_layout)

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

        # Add columns to main layout
        main_layout.addLayout(left_column, 1)  # 1 is the stretch factor
        main_layout.addLayout(right_column, 2)  # 2 is the stretch factor (wider)

        # Set the layout
        self.setLayout(main_layout)

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

    def load_data(self, imagename):
        """Load data from a FITS file or CASA image directory"""
        stokes = self.stokes_combo.currentText()
        threshold = 3.0  # Default threshold

        try:
            pix, csys, psf = get_pixel_values_from_image(imagename, stokes, threshold)
            self.current_image_data = pix
            self.current_wcs = csys
            self.psf = psf
            self.imagename = imagename  # Store the imagename for later use

            # Update file label
            self.file_label.setText(
                f"File: {os.path.basename(imagename)}\nType: {'CASA Image' if os.path.isdir(imagename) else 'FITS File'}\nStokes: {stokes}"
            )

            # Update window title with filename
            self.viewer.title = f"Napari Viewer - {os.path.basename(imagename)}"

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
        min_val = data.min()
        max_val = data.max()
        mean_val = data.mean()
        median_val = np.median(data)
        std_val = data.std()

        stats_text = (
            f"Min: {min_val:.4g}\n"
            f"Max: {max_val:.4g}\n"
            f"Mean: {mean_val:.4g}\n"
            f"Median: {median_val:.4g}\n"
            f"Std Dev: {std_val:.4g}\n"
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


def main():
    """Main function to run the application"""
    app = QApplication(sys.argv)
    viewer = NapariViewer()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

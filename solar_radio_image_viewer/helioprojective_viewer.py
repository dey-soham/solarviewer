#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import tempfile
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib import rcParams
from matplotlib.colors import Normalize, LogNorm, PowerNorm
import matplotlib.patches as patches

from PyQt5.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
    QSlider,
    QCheckBox,
    QGroupBox,
    QFormLayout,
    QMessageBox,
    QSizePolicy,
    QStatusBar,
    QToolBar,
    QAction,
    QToolButton,
    QSpinBox,
    QDoubleSpinBox,
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QIcon

# Try to import sunpy
try:
    import sunpy
    import sunpy.map
    from sunpy.coordinates import frames
    import astropy.units as u
    from astropy.coordinates import SkyCoord

    SUNPY_AVAILABLE = True
except ImportError:
    SUNPY_AVAILABLE = False
    print(
        "Warning: sunpy is not available. Please install sunpy for helioprojective coordinates."
    )

# Try to import CASA tools (casatasks run via subprocess)
try:
    from casatools import image as IA

    # Note: casatasks (exportfits) is now run via subprocess - see convert_casaimage_to_fits()

    CASA_AVAILABLE = True
except ImportError:
    CASA_AVAILABLE = False
    print(
        "Warning: CASA tools are not available. Please ensure CASA is properly installed."
    )


def convert_casaimage_to_fits(
    imagename=None, fitsname=None, dropdeg=False, overwrite=True
):
    """Convert a CASA image to a FITS file using subprocess to avoid crashes."""
    if not CASA_AVAILABLE:
        print("Error: CASA tools are not available")
        return None

    if imagename is None:
        print("Error: No input image specified")
        return None
    
    try:
        imagename = os.path.abspath(imagename)
        if fitsname is not None:
            fitsname = os.path.abspath(fitsname)

        if fitsname is None:
            # Create absolute path for default temp fits in writable directory
            import tempfile

            fitsname = os.path.join(
                tempfile.gettempdir(), f"temp_{os.getpid()}_{os.path.basename(imagename)}.fits"
            )

        import subprocess
        import sys

        # Run exportfits in a separate process to avoid segfault
        script = f"""
import sys
from casatasks import exportfits
try:
    exportfits(imagename="{imagename}", fitsimage="{fitsname}", dropdeg={dropdeg}, overwrite={overwrite})
    print("SUCCESS")
except Exception as e:
    print(f"ERROR: {{e}}", file=sys.stderr)
    sys.exit(1)
"""

        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=os.getcwd() if os.access(os.getcwd(), os.W_OK) else tempfile.gettempdir(),
        )

        if result.returncode != 0:
            print(f"Error in exportfits: {result.stderr}")
            return None

        if os.path.exists(fitsname):
            return fitsname
        else:
            print(f"Error: Failed to create FITS file {fitsname}")
            return None
    except Exception as e:
        print(f"Error in convert_casaimage_to_fits: {str(e)}")
        return None


# Import the helioprojective conversion functions
try:
    from .helioprojective import convert_to_hpc
    from .styles import theme_manager, get_stylesheet
except ImportError:
    try:
        # For direct script execution
        from helioprojective import convert_to_hpc
        from styles import theme_manager, get_stylesheet
    except ImportError:
        print("Error: Could not import helioprojective conversion functions")
        sys.exit(1)


def update_hpc_matplotlib_theme():
    """Update matplotlib rcParams based on current theme."""
    rcParams.update(theme_manager.matplotlib_params)


rcParams["axes.linewidth"] = 1.4
rcParams["font.size"] = 12
update_hpc_matplotlib_theme()


class HelioProjectiveViewer(QMainWindow):
    """
    A separate window for displaying images in helioprojective coordinates.
    """

    def __init__(
        self,
        imagename=None,
        stokes="I",
        threshold=10,
        rms_box=(0, 200, 0, 130),
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Helioprojective Viewer")
        self.resize(1280, 720)

        # Store parameters
        self.imagename = imagename
        self.stokes = stokes
        self.threshold = threshold
        self.rms_box = rms_box
        self.parent = parent

        # Initialize variables
        self.helioprojective_map = None
        self.psf = None
        self.temp_fits_file = None
        self.colormap = "viridis"
        self.stretch = "linear"
        self.gamma = 1.0
        self.vmin = None
        self.vmax = None
        self.show_grid = True
        self.show_limb = True
        self.show_beam = True
        self.show_colorbar = True

        # Apply current theme stylesheet
        self.setStyleSheet(get_stylesheet(theme_manager.palette, theme_manager.is_dark))

        # Register for theme changes
        theme_manager.register_callback(self._on_theme_changed)

        # Set up the UI
        self.setup_ui()
        set_hand_cursor(self)

        # Load and display the image if provided
        if imagename:
            # Use QTimer to load image after UI is set up
            QTimer.singleShot(100, self.load_image)

    def setup_ui(self):
        """Set up the user interface"""
        # Create central widget and layout
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QHBoxLayout(self.central_widget)

        # Create left panel for controls
        self.left_panel = QWidget()
        self.left_layout = QVBoxLayout(self.left_panel)
        self.left_panel.setMaximumWidth(400)

        # Create right panel for figure
        self.right_panel = QWidget()
        self.right_layout = QVBoxLayout(self.right_panel)

        # Add panels to main layout
        self.main_layout.addWidget(self.left_panel)
        self.main_layout.addWidget(self.right_panel, 1)  # Right panel should expand

        # Create controls
        self.create_display_controls()
        self.create_overlay_controls()

        # Add a spacer to push controls to the top
        self.left_layout.addStretch(1)

        # Create figure and canvas
        self.figure = Figure(figsize=(8, 6), dpi=100)
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setMinimumHeight(400)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Create navigation toolbar
        self.toolbar = NavigationToolbar(self.canvas, self)

        # Add figure and toolbar to right panel
        self.right_layout.addWidget(self.toolbar)
        self.right_layout.addWidget(self.canvas, 1)  # Canvas should expand vertically

        # Create status bar
        self.statusbar = QStatusBar(self)
        self.setStatusBar(self.statusbar)
        self.statusbar.showMessage("Ready")

    def create_display_controls(self):
        """Create controls for display settings"""
        # Create group box for display controls
        display_group = QGroupBox("Display Settings")
        display_layout = QFormLayout(display_group)

        # Colormap selection
        self.cmap_combo = QComboBox()
        for cmap in sorted(plt.colormaps()):
            self.cmap_combo.addItem(cmap)
        self.cmap_combo.setCurrentText(self.colormap)
        self.cmap_combo.currentTextChanged.connect(self.on_display_changed)
        display_layout.addRow("Colormap:", self.cmap_combo)

        # Stretch function
        self.stretch_combo = QComboBox()
        self.stretch_combo.addItems(["linear", "sqrt", "log", "power"])
        self.stretch_combo.setCurrentText(self.stretch)
        self.stretch_combo.currentTextChanged.connect(self.on_display_changed)
        display_layout.addRow("Stretch:", self.stretch_combo)

        # Gamma (for power stretch)
        gamma_layout = QHBoxLayout()
        self.gamma_slider = QSlider(Qt.Horizontal)
        self.gamma_slider.setRange(1, 100)
        self.gamma_slider.setValue(int(self.gamma * 10))
        self.gamma_slider.valueChanged.connect(self.on_gamma_changed)

        self.gamma_entry = QLineEdit(str(self.gamma))
        self.gamma_entry.setMaximumWidth(50)
        self.gamma_entry.returnPressed.connect(self.on_gamma_entry_changed)

        gamma_layout.addWidget(self.gamma_slider)
        gamma_layout.addWidget(self.gamma_entry)
        display_layout.addRow("Gamma:", gamma_layout)

        # Min/Max values
        range_layout = QHBoxLayout()
        self.vmin_entry = QLineEdit()
        self.vmin_entry.setMaximumWidth(80)
        self.vmax_entry = QLineEdit()
        self.vmax_entry.setMaximumWidth(80)

        range_layout.addWidget(QLabel("Min:"))
        range_layout.addWidget(self.vmin_entry)
        range_layout.addWidget(QLabel("Max:"))
        range_layout.addWidget(self.vmax_entry)
        display_layout.addRow("Range:", range_layout)

        # Auto-scale buttons
        scale_layout = QHBoxLayout()
        self.auto_minmax_btn = QPushButton("Min/Max")
        self.auto_minmax_btn.clicked.connect(self.auto_minmax)
        self.auto_percentile_btn = QPushButton("99.5%")
        self.auto_percentile_btn.clicked.connect(self.auto_percentile)
        self.auto_median_btn = QPushButton("Median±5σ")
        self.auto_median_btn.clicked.connect(self.auto_median_rms)

        scale_layout.addWidget(self.auto_minmax_btn)
        scale_layout.addWidget(self.auto_percentile_btn)
        scale_layout.addWidget(self.auto_median_btn)
        display_layout.addRow("Auto-scale:", scale_layout)

        # Add the group to the left panel
        self.left_layout.addWidget(display_group)

    def create_overlay_controls(self):
        """Create controls for overlay settings"""
        # Create group box for overlay controls
        overlay_group = QGroupBox("Overlay Settings")
        overlay_layout = QFormLayout(overlay_group)

        # Grid lines
        self.show_grid_checkbox = QCheckBox("Show Grid")
        self.show_grid_checkbox.setChecked(self.show_grid)
        self.show_grid_checkbox.stateChanged.connect(self.on_overlay_changed)
        overlay_layout.addRow(self.show_grid_checkbox)

        # Solar limb
        self.show_limb_checkbox = QCheckBox("Show Solar Limb")
        self.show_limb_checkbox.setChecked(self.show_limb)
        self.show_limb_checkbox.stateChanged.connect(self.on_overlay_changed)
        overlay_layout.addRow(self.show_limb_checkbox)

        # Beam
        self.show_beam_checkbox = QCheckBox("Show Beam")
        self.show_beam_checkbox.setChecked(self.show_beam)
        self.show_beam_checkbox.stateChanged.connect(self.on_overlay_changed)
        overlay_layout.addRow(self.show_beam_checkbox)

        # Colorbar
        self.show_colorbar_checkbox = QCheckBox("Show Colorbar")
        self.show_colorbar_checkbox.setChecked(self.show_colorbar)
        self.show_colorbar_checkbox.stateChanged.connect(self.on_overlay_changed)
        overlay_layout.addRow(self.show_colorbar_checkbox)

        # Add the group to the left panel
        self.left_layout.addWidget(overlay_group)

    def load_image(self):
        """Load and convert the image to helioprojective coordinates"""
        if not SUNPY_AVAILABLE:
            self.show_status_message("Sunpy is not available. Please install sunpy.")
            return

        if not self.imagename:
            self.show_status_message("No image specified.")
            return

        try:
            self.show_status_message(f"Loading image: {self.imagename}")

            # If it's a CASA image, convert it to FITS first
            if os.path.isdir(self.imagename):
                try:
                    self.show_status_message(
                        f"Converting CASA image to FITS: {self.imagename}"
                    )
                    temp_fits = convert_casaimage_to_fits(imagename=self.imagename)
                    if temp_fits is None:
                        raise RuntimeError("Failed to convert CASA image to FITS")
                    self.temp_fits_file = temp_fits
                    fits_file = temp_fits
                    self.show_status_message(
                        f"CASA image converted to FITS: {temp_fits}"
                    )
                except Exception as e:
                    self.show_status_message(
                        f"Error converting CASA image to FITS: {str(e)}"
                    )
                    print(f"Error converting CASA image to FITS: {str(e)}")
                    return
            else:
                fits_file = self.imagename

            # Convert to helioprojective coordinates
            self.show_status_message(f"Converting to helioprojective coordinates...")

            # Make sure the rms_box is a tuple of integers
            if isinstance(self.rms_box, list):
                self.rms_box = tuple(self.rms_box)

            # Convert to helioprojective coordinates
            self.helioprojective_map, csys, self.psf = convert_to_hpc(
                fits_file=fits_file,
                Stokes=self.stokes,
                thres=self.threshold,
                rms_box=self.rms_box,
            )

            if self.helioprojective_map is None:
                self.show_status_message(
                    "Failed to convert to helioprojective coordinates."
                )
                return

            # Ensure required metadata is present and valid
            """if not hasattr(self.helioprojective_map, "meta"):
                self.helioprojective_map.meta = {}

            # Fix any 'None' string values in metadata
            for key in ["telescop", "instrume", "detector"]:
                if self.helioprojective_map.meta.get(key) in ["None", "none", None]:
                    self.helioprojective_map.meta[key] = "Unknown"

            # Auto-scale the image
            self.auto_percentile()"""

            # Plot the image
            self.plot_image()

            self.show_status_message(
                "Image loaded and converted to helioprojective coordinates."
            )
        except Exception as e:
            import traceback

            traceback.print_exc()
            error_msg = f"Error loading image: {str(e)}"
            self.show_status_message(error_msg)
            QMessageBox.critical(self, "Error", error_msg)

    def plot_image(self):
        """Plot the helioprojective map"""
        if self.helioprojective_map is None:
            self.show_status_message("No helioprojective map available to plot.")
            return

        try:
            # Clear the figure
            self.figure.clear()

            # Create a subplot with the helioprojective map projection
            try:
                ax = self.figure.add_subplot(111, projection=self.helioprojective_map)
            except Exception as e:
                print(f"Error creating subplot with projection: {str(e)}")
                ax = self.figure.add_subplot(111)

            # Apply stretch function
            data = self.helioprojective_map.data.copy()

            # Handle NaN values
            if np.isnan(data).any():
                data = np.nan_to_num(data, nan=0.0)

            # Apply stretch
            if self.stretch == "sqrt":
                norm = PowerNorm(0.5, vmin=self.vmin, vmax=self.vmax)
            elif self.stretch == "log":
                norm = LogNorm(
                    vmin=max(1e-10, self.vmin) if self.vmin else 1e-10, vmax=self.vmax
                )
            elif self.stretch == "power":
                norm = PowerNorm(self.gamma, vmin=self.vmin, vmax=self.vmax)
            else:  # linear
                norm = Normalize(vmin=self.vmin, vmax=self.vmax)

            # Plot the map
            try:
                im = self.helioprojective_map.plot(
                    axes=ax,
                    cmap=self.colormap,
                    norm=norm,
                    title=False,
                )
            except Exception as e:
                print(f"Error using sunpy plot: {str(e)}, falling back to imshow")
                im = ax.imshow(
                    data,
                    cmap=self.colormap,
                    norm=norm,
                    origin="lower",
                    aspect="equal",
                )

            # Set axis labels
            ax.set_xlabel("Helioprojective Longitude (arcsec)")
            ax.set_ylabel("Helioprojective Latitude (arcsec)")

            # Set title with observation information
            try:
                if hasattr(self.helioprojective_map, "wavelength") and hasattr(
                    self.helioprojective_map, "date"
                ):
                    wavelength_str = f"{self.helioprojective_map.wavelength.value:.2f} {self.helioprojective_map.wavelength.unit}"
                    title = f"Helioprojective Coordinate Map\n{wavelength_str} - {self.helioprojective_map.date.strftime('%Y-%m-%d %H:%M:%S')}"
                    ax.set_title(title, fontsize=12)
                else:
                    ax.set_title("Helioprojective Coordinate Map", fontsize=12)
            except Exception as e:
                print(f"Error setting title: {str(e)}")
                ax.set_title("Helioprojective Coordinate Map", fontsize=12)

            # Draw grid if enabled
            if self.show_grid:
                ax.grid(True, color="white", linestyle="--", alpha=0.5)

            # Draw solar limb if enabled
            if self.show_limb:
                try:
                    self.helioprojective_map.draw_limb(
                        axes=ax, color="white", alpha=0.5, linewidth=1
                    )
                except Exception as e:
                    print(f"Error drawing limb: {str(e)}")

            # Draw PSF beam if enabled
            if self.show_beam and self.psf:
                try:
                    self._draw_beam(ax)
                except Exception as e:
                    print(f"Error drawing beam: {str(e)}")

            # Draw colorbar if enabled
            if self.show_colorbar:
                try:
                    self.figure.colorbar(im, ax=ax, label="Intensity")
                except Exception as e:
                    print(f"Error drawing colorbar: {str(e)}")

            # Update the canvas
            self.canvas.draw()

            self.show_status_message("Helioprojective map plotted successfully.")
        except Exception as e:
            import traceback

            traceback.print_exc()
            self.show_status_message(f"Error plotting image: {str(e)}")

    def _draw_beam(self, ax):
        """Draw the PSF beam on the plot"""
        if not self.psf:
            return

        try:
            # Get beam properties
            if isinstance(self.psf["major"]["value"], list):
                major_deg = float(self.psf["major"]["value"][0])
            else:
                major_deg = float(self.psf["major"]["value"])

            if isinstance(self.psf["minor"]["value"], list):
                minor_deg = float(self.psf["minor"]["value"][0])
            else:
                minor_deg = float(self.psf["minor"]["value"])

            if isinstance(self.psf["positionangle"]["value"], list):
                pa_deg = float(self.psf["positionangle"]["value"][0]) - 90
            else:
                pa_deg = float(self.psf["positionangle"]["value"]) - 90

            # Convert beam size to arcseconds
            major_arcsec = major_deg * 3600
            minor_arcsec = minor_deg * 3600

            # Get the current axis limits in arcseconds
            xlim = ax.get_xlim()
            ylim = ax.get_ylim()
            view_width = xlim[1] - xlim[0]
            view_height = ylim[1] - ylim[0]
            margin_x = view_width * 0.05
            margin_y = view_height * 0.05

            # Position the beam in the bottom-left corner
            beam_x = xlim[0] + margin_x + major_arcsec / 2
            beam_y = ylim[0] + margin_y + minor_arcsec / 2

            # Create the beam ellipse
            beam = patches.Ellipse(
                (beam_x, beam_y),
                major_arcsec,
                minor_arcsec,
                angle=pa_deg,
                fc="white",
                ec="black",
                alpha=0.7,
            )
            ax.add_patch(beam)

            # Add text with beam size
            ax.text(
                beam_x,
                beam_y + minor_arcsec,
                f"{major_arcsec:.1f}″×{minor_arcsec:.1f}″",
                ha="center",
                va="bottom",
                color="white",
                fontsize=8,
            )
        except Exception as e:
            print(f"Error drawing beam: {str(e)}")

    def on_display_changed(self):
        """Handle changes to display settings"""
        self.colormap = self.cmap_combo.currentText()
        self.stretch = self.stretch_combo.currentText()

        # Update gamma controls visibility
        self.gamma_slider.setEnabled(self.stretch == "power")
        self.gamma_entry.setEnabled(self.stretch == "power")

        # Update the plot
        self.plot_image()

    def on_overlay_changed(self):
        """Handle changes to overlay settings"""
        self.show_grid = self.show_grid_checkbox.isChecked()
        self.show_limb = self.show_limb_checkbox.isChecked()
        self.show_beam = self.show_beam_checkbox.isChecked()
        self.show_colorbar = self.show_colorbar_checkbox.isChecked()

        # Update the plot
        self.plot_image()

    def on_gamma_changed(self):
        """Handle changes to the gamma slider"""
        self.gamma = self.gamma_slider.value() / 10.0
        self.gamma_entry.setText(f"{self.gamma:.1f}")

        if self.stretch == "power":
            self.plot_image()

    def on_gamma_entry_changed(self):
        """Handle changes to the gamma entry field"""
        try:
            gamma = float(self.gamma_entry.text())
            if gamma > 0:
                self.gamma = gamma
                self.gamma_slider.setValue(int(gamma * 10))

                if self.stretch == "power":
                    self.plot_image()
        except ValueError:
            # Restore the previous value
            self.gamma_entry.setText(f"{self.gamma:.1f}")

    def auto_minmax(self):
        """Auto-scale using min/max values"""
        if self.helioprojective_map is None:
            return

        data = self.helioprojective_map.data
        self.vmin = float(np.nanmin(data))
        self.vmax = float(np.nanmax(data))

        self.vmin_entry.setText(f"{self.vmin:.6g}")
        self.vmax_entry.setText(f"{self.vmax:.6g}")

        self.plot_image()
        self.show_status_message(
            f"Auto-scaled to min/max: {self.vmin:.6g} - {self.vmax:.6g}"
        )

    def auto_percentile(self):
        """Auto-scale using percentile values"""
        if self.helioprojective_map is None:
            return

        data = self.helioprojective_map.data
        self.vmin = float(np.nanpercentile(data, 0.5))
        self.vmax = float(np.nanpercentile(data, 99.5))

        self.vmin_entry.setText(f"{self.vmin:.6g}")
        self.vmax_entry.setText(f"{self.vmax:.6g}")

        self.plot_image()
        self.show_status_message(
            f"Auto-scaled to 0.5-99.5 percentile: {self.vmin:.6g} - {self.vmax:.6g}"
        )

    def auto_median_rms(self):
        """Auto-scale using median ± 5σ"""
        if self.helioprojective_map is None:
            return

        data = self.helioprojective_map.data
        median = float(np.nanmedian(data))
        rms = float(np.nanstd(data))

        self.vmin = median - 5 * rms
        self.vmax = median + 5 * rms

        self.vmin_entry.setText(f"{self.vmin:.6g}")
        self.vmax_entry.setText(f"{self.vmax:.6g}")

        self.plot_image()
        self.show_status_message(
            f"Auto-scaled to median±5σ: {self.vmin:.6g} - {self.vmax:.6g}"
        )

    def show_status_message(self, message):
        """Show a message in the status bar"""
        self.statusbar.showMessage(message)
        print(message)

    def _on_theme_changed(self, new_theme):
        """Handle theme change events."""
        # Update matplotlib rcParams
        update_hpc_matplotlib_theme()

        # Update window stylesheet
        self.setStyleSheet(get_stylesheet(theme_manager.palette, theme_manager.is_dark))

        # Refresh the plot with new theme colors
        if hasattr(self, "figure") and self.figure and self.helioprojective_map:
            palette = theme_manager.palette
            is_dark = theme_manager.is_dark

            # Use plot-specific colors for light mode
            if is_dark:
                fig_bg = palette["window"]
                axes_bg = palette["base"]
                text_color = palette["text"]
            else:
                fig_bg = palette.get("plot_bg", "#ffffff")
                axes_bg = palette.get("plot_bg", "#ffffff")
                text_color = palette.get("plot_text", "#1a1a1a")

            self.figure.set_facecolor(fig_bg)
            for ax in self.figure.get_axes():
                ax.set_facecolor(axes_bg)
                ax.tick_params(colors=text_color)
                ax.xaxis.label.set_color(text_color)
                ax.yaxis.label.set_color(text_color)
                ax.title.set_color(text_color)
                for spine in ax.spines.values():
                    spine.set_color(text_color)
            self.canvas.draw_idle()

    def closeEvent(self, event):
        """Handle window close event"""
        # Unregister theme callback
        theme_manager.unregister_callback(self._on_theme_changed)

        # Clean up temporary files
        if self.temp_fits_file and os.path.exists(self.temp_fits_file):
            try:
                os.remove(self.temp_fits_file)
                print(f"Removed temporary file: {self.temp_fits_file}")
            except Exception as e:
                print(f"Error removing temporary file: {str(e)}")

        # Accept the close event
        event.accept()


def main():
    """Main function for standalone execution"""
    import argparse
    from PyQt5.QtWidgets import QApplication

    # Create argument parser with detailed help
    parser = argparse.ArgumentParser(
        description="""
Solar Radio Image Helioprojective Viewer

A standalone viewer for displaying solar radio images in helioprojective coordinates.
This tool can handle both FITS files and CASA images, and provides interactive
visualization with various display options.

Examples:
    heliosv myimage.fits
    heliosv myimage.image --stokes I
    heliosv myimage.fits --threshold 5 --rms-box 0 200 0 130
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "imagename", help="Path to the input image (FITS file or CASA image directory)"
    )

    parser.add_argument(
        "--stokes",
        default="I",
        choices=[
            "I",
            "Q",
            "U",
            "V",
            "L",
            "Lfrac",
            "Vfrac",
            "Q/I",
            "U/I",
            "U/V",
            "PANG",
        ],
        help="Stokes parameter to display (default: I)",
    )

    parser.add_argument(
        "--threshold",
        type=float,
        default=10,
        help="Threshold value for polarization calculations (default: 10)",
    )

    parser.add_argument(
        "--rms-box",
        type=int,
        nargs=4,
        default=[0, 200, 0, 130],
        metavar=("X1", "X2", "Y1", "Y2"),
        help="RMS box coordinates as X1 X2 Y1 Y2 (default: 0 200 0 130)",
    )

    # Parse arguments
    args = parser.parse_args()

    # Check if image exists
    if not os.path.exists(args.imagename):
        print(f"Error: Image not found: {args.imagename}")
        sys.exit(1)

    # Create Qt application
    app = QApplication(sys.argv)

    # Create and show the viewer
    viewer = HelioProjectiveViewer(
        imagename=args.imagename,
        stokes=args.stokes,
        threshold=args.threshold,
        rms_box=args.rms_box,
    )
    viewer.show()

    # Start the event loop
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

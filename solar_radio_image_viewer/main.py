#!/usr/bin/env python3
import sys
import argparse
import os
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QPalette, QColor
from PyQt5.QtCore import Qt

from .viewer import SolarRadioImageViewerApp
from .styles import DARK_PALETTE, STYLESHEET


def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="Solar Radio Image Viewer - A tool for visualizing and analyzing solar radio images",
        epilog="""
Viewer Types:
  Standard Viewer: Full-featured viewer with comprehensive analysis tools, 
                  coordinate systems, region selection, and statistical analysis.
  
  Napari Viewer:   Lightweight, fast viewer for quick visualization of images.
                  Offers basic functionality with faster loading times.

Examples:
  solarviewer                      # Launch standard viewer
  solarviewer image.fits           # Open image.fits in standard viewer
  solarviewer -f                   # Launch fast Napari viewer
  solarviewer -f image.fits        # Open image.fits in Napari viewer
  sv --fast image.fits             # Same as above using short command
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "-f",
        "--fast",
        action="store_true",
        help="Launch the fast Napari viewer instead of the standard viewer",
    )
    parser.add_argument(
        "imagename",
        nargs="?",
        default=None,
        help="Path to the image file to open (FITS or CASA format)",
    )

    # Add version information
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version="Solar Radio Image Viewer 1.0",
        help="Show the application version and exit",
    )

    args = parser.parse_args()

    # Check if the specified image file exists
    if args.imagename and not os.path.exists(args.imagename):
        print(f"Error: Image file '{args.imagename}' not found.")
        print("Please provide a valid path to an image file.")
        sys.exit(1)

    # Initialize the application
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Apply dark palette
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(DARK_PALETTE["window"]))
    palette.setColor(QPalette.WindowText, QColor(DARK_PALETTE["text"]))
    palette.setColor(QPalette.Base, QColor(DARK_PALETTE["base"]))
    palette.setColor(QPalette.AlternateBase, QColor(DARK_PALETTE["button"]))
    palette.setColor(QPalette.Text, QColor(DARK_PALETTE["text"]))
    palette.setColor(QPalette.Button, QColor(DARK_PALETTE["button"]))
    palette.setColor(QPalette.ButtonText, QColor(DARK_PALETTE["text"]))
    palette.setColor(QPalette.Highlight, QColor(DARK_PALETTE["highlight"]))
    palette.setColor(QPalette.HighlightedText, Qt.white)
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)

    # Launch the appropriate viewer
    if args.fast:
        # Launch the Napari viewer
        from .napari_viewer import main as napari_main

        napari_main(args.imagename)
    else:
        # Launch the standard viewer
        window = SolarRadioImageViewerApp(args.imagename)
        window.resize(1600, 720)
        window.show()
        sys.exit(app.exec_())


if __name__ == "__main__":
    main()

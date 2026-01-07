#!/usr/bin/env python3
# Suppress CASA warnings (C++ level) before any imports
import os
os.environ.setdefault('CASA_LOGLEVEL', 'ERROR')
os.environ['CASARC'] = '/dev/null'  # Prevent CASA config loading

import sys
import argparse
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QPalette, QColor
from PyQt5.QtCore import Qt, QSettings

# Import theme manager FIRST, before viewer
from .styles import theme_manager, ThemeManager
from . import __version__


def apply_theme(app, theme_mgr):
    """Apply the current theme to the application."""
    palette = theme_mgr.palette
    is_dark = theme_mgr.is_dark
    
    # Apply Qt palette
    qt_palette = QPalette()
    qt_palette.setColor(QPalette.Window, QColor(palette["window"]))
    qt_palette.setColor(QPalette.WindowText, QColor(palette["text"]))
    qt_palette.setColor(QPalette.Base, QColor(palette["base"]))
    qt_palette.setColor(QPalette.AlternateBase, QColor(palette["surface"]))
    qt_palette.setColor(QPalette.Text, QColor(palette["text"]))
    qt_palette.setColor(QPalette.Button, QColor(palette["button"]))
    qt_palette.setColor(QPalette.ButtonText, QColor(palette["text"]))
    qt_palette.setColor(QPalette.Highlight, QColor(palette["highlight"]))
    qt_palette.setColor(QPalette.HighlightedText, Qt.white)
    qt_palette.setColor(QPalette.Link, QColor(palette["highlight"]))
    qt_palette.setColor(QPalette.Disabled, QPalette.Text, QColor(palette["disabled"]))
    qt_palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(palette["disabled"]))
    
    app.setPalette(qt_palette)
    app.setStyleSheet(theme_mgr.stylesheet)


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
    parser.add_argument(
        "--light",
        action="store_true",
        help="Start with light theme instead of dark theme",
    )

    # Add version information
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"SolarViewer {__version__}",
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
    
    # Load saved theme preference BEFORE importing viewer
    settings = QSettings("SolarViewer", "SolarViewer")
    saved_theme = settings.value("theme", ThemeManager.DARK)
    
    # Command line --light flag overrides saved preference
    if args.light:
        saved_theme = ThemeManager.LIGHT
    
    # Set initial theme BEFORE importing viewer (so matplotlib rcParams are correct)
    # Use internal method to avoid triggering callbacks before viewer is loaded
    theme_manager._current_theme = saved_theme
    
    # Now import viewer - it will use the correct theme for matplotlib rcParams
    from .viewer import SolarRadioImageViewerApp, update_matplotlib_theme
    
    # Ensure matplotlib is updated with the correct theme
    update_matplotlib_theme()
    
    # Apply theme to application
    apply_theme(app, theme_manager)
    
    # Register theme change callback to update app
    def on_theme_change(new_theme):
        apply_theme(app, theme_manager)
        update_matplotlib_theme()
        # Save theme preference
        settings.setValue("theme", new_theme)
    
    theme_manager.register_callback(on_theme_change)

    # Launch the appropriate viewer
    if args.fast:
        # Launch the Napari viewer
        from .napari_viewer import main as napari_main

        napari_main(args.imagename)
    else:
        # Launch the standard viewer
        window = SolarRadioImageViewerApp(args.imagename)
        # Note: Window sizing is handled in SolarRadioImageViewerApp.__init__
        # using screen-aware sizing (90% of available screen, capped at 1920x1080)
        window.show()
        sys.exit(app.exec_())


if __name__ == "__main__":
    main()


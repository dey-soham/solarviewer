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

# Globally suppress Astropy's "Invalid 'BLANK' keyword" warning
import warnings
try:
    from astropy.io.fits.verify import VerifyWarning
    warnings.filterwarnings('ignore', category=VerifyWarning, message=".*Invalid 'BLANK' keyword.*")
except ImportError:
    pass


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


# Threaded Loading Implementation
from PyQt5.QtCore import QThread, pyqtSignal

class LoaderThread(QThread):
    """Background thread to load heavy modules and initialize the viewer."""
    progress = pyqtSignal(int)
    message = pyqtSignal(str)
    finished_loading = pyqtSignal(object)  # Returns the created window
    error = pyqtSignal(str) # Signal for loading errors
    
    def __init__(self, imagename, args_fast):
        super().__init__()
        self.imagename = imagename
        self.args_fast = args_fast
        
    def run(self):
        try:
            if self.args_fast:
                return # Fast mode handled separately
            
            self.message.emit("Loading core libraries...")
            self.progress.emit(90)
            
            # Heavy imports happen here
            from .viewer import SolarRadioImageViewerApp
            self.progress.emit(95)
            from .viewer import update_matplotlib_theme
            self.message.emit("Initializing interface...")
            self.progress.emit(98)
            self.message.emit("Finalizing...")
            
            # Return the imported class, not the instance
            self.finished_loading.emit(SolarRadioImageViewerApp)
            
        except Exception as e:
            import traceback
            error_details = "".join(traceback.format_exception(None, e, e.__traceback__))
            print(f"Error in loader thread: {e}")
            self.error.emit(error_details)


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

    # Enable HiDPI scaling for high-resolution displays
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    
    pre_settings = QSettings("SolarViewer", "SolarViewer")
    ui_scale_factor = pre_settings.value("ui_scale_factor", 1.0, type=float)
    if ui_scale_factor != 1.0:
        os.environ["QT_SCALE_FACTOR"] = str(ui_scale_factor)
    
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
    theme_manager._current_theme = saved_theme
    
    # Handle Fast Mode (Napari) - Skip complex loading
    if args.fast:
        from .napari_viewer import main as napari_main
        napari_main(args.imagename)
        return

    # === STANDARD VIEWER LAUNCH SEQUENCE ===
    
    # 1. Show Splash Screen (Imports are fast due to pkg_resources removal)
    from .splash import ModernSplashScreen
    splash = ModernSplashScreen(version=__version__)
    splash.show()
    app.processEvents()
    
    # 2. Setup Loading Thread
    loader = LoaderThread(args.imagename, args.fast)
    
    # Container for the window (to be populated by thread callback)
    window_container = {'window': None}
    
    def on_progress(val):
        splash.set_progress(val)
        
    def on_message(msg):
        splash.show_message(msg)
        
    from PyQt5.QtWidgets import QMessageBox

    def on_failure(error_msg):
        """Handle startup failure by showing an error dialog."""
        splash.hide() # Hide splash so it doesn't cover the error
        
        msg = QMessageBox()
        
        # Apply theme stylesheet with specific override for QTextEdit (details box)
        custom_style = theme_manager.stylesheet + """
        QTextEdit {
            background-color: %s;
            color: %s;
            border: 1px solid %s;
            font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
            font-size: 10pt;
        }
        QLabel {
            font-family: 'Inter', system-ui, sans-serif;
            font-size: 11pt;
            color: %s;
        }
        QPushButton {
            font-family: 'Inter', system-ui, sans-serif;
            font-size: 10pt;
            font-weight: 500;
        }
        """ % (
            theme_manager.palette['base'], 
            theme_manager.palette['text'],
            theme_manager.palette['border'],
            theme_manager.palette['text']
        )
        msg.setStyleSheet(custom_style)
        
        msg.setIcon(QMessageBox.Critical)
        msg.setWindowTitle("Startup Error")
        msg.setText("SolarViewer failed to start.")
        msg.setInformativeText("An error occurred while initializing the application.")
        msg.setDetailedText(error_msg)
        msg.setStandardButtons(QMessageBox.Ok)
        msg.exec_()
        sys.exit(1)

    def on_finished(ViewerClass):
        # This runs in the main thread
        try:
            splash.set_progress(95)
            splash.show_message("Building interface...")
            QApplication.processEvents()
            
            # Apply theme BEFORE creating window to ensure correct initialization
            apply_theme(app, theme_manager)
            
            # Instantiate the main window now that classes are imported and theme is active
            win = ViewerClass(args.imagename)
            window_container['window'] = win
            
            # Setup theme callback
            def on_theme_change(new_theme):
                apply_theme(app, theme_manager)
                settings.setValue("theme", new_theme)
            theme_manager.register_callback(on_theme_change)
            
            splash.set_progress(100)
            splash.show_message("Ready!")
            
            # Finish splash sequence
            splash.finish(win)
            win.show()
            
        except Exception as e:
            import traceback
            error_details = "".join(traceback.format_exception(None, e, e.__traceback__))
            print(f"[ERROR] Startup failed: {e}")
            on_failure(error_details)
        
    loader.progress.connect(on_progress)
    loader.message.connect(on_message)
    loader.finished_loading.connect(on_finished)
    loader.error.connect(on_failure) # Connect the new error signal
    
    # 3. Start Loading
    loader.start()
    
    # 4. Enter Main Event Loop
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()


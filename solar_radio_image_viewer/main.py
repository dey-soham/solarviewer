#!/usr/bin/env python3
import logging
import os

os.environ.setdefault("CASA_LOGLEVEL", "ERROR")
os.environ["CASARC"] = "/dev/null"  # Prevent CASA config loading
# Skip the system's OpenSSL configuration file (its provider and policy
# settings), which made HTTPS fail with ssl.SSLError on some Linux systems with
# Python 3.11+ (see INSTALLATION.md). Certificates are still checked. An
# OPENSSL_CONF set by the user or the system is kept.
os.environ.setdefault("OPENSSL_CONF", "/dev/null")

import sys
import argparse
from PyQt5.QtCore import QSettings

# Import theme manager FIRST, before viewer
from .ui.styles import theme_manager, ThemeManager
from .ui.app import create_app, apply_theme
from .ui.errors import show_error
from . import __version__

import warnings
from .crash_handler import (
    install_crash_handler,
    set_main_window,
    show_native_crash_reports,
    watch_for_native_crashes,
)

logger = logging.getLogger(__name__)

# Enable crash reporting immediately
install_crash_handler()

# Suppress Numpy 2.0+ warning about invalid values in string formatting
warnings.filterwarnings(
    "ignore",
    category=RuntimeWarning,
    message=".*invalid value encountered in do_format.*",
)
# Astropy's "Invalid 'BLANK' keyword", here and in the worker processes, which
# import this module too. (By its text: naming its class would import astropy
# before the splash shows.)
warnings.filterwarnings("ignore", message=".*Invalid 'BLANK' keyword.*")


# Threaded Loading Implementation
from PyQt5.QtCore import QThread, pyqtSignal


class LoaderThread(QThread):
    """Background thread to load heavy modules and initialize the viewer."""

    progress = pyqtSignal(int)
    message = pyqtSignal(str)
    finished_loading = pyqtSignal(object)  # Returns the created window
    error = pyqtSignal(str)  # Signal for loading errors
    # What holds a step up from outside, how far it is, and a hint; "" when
    # it no longer does (CASA looking for newer data of its own)
    waiting = pyqtSignal(str, str, str)

    def __init__(self, imagename):
        super().__init__()
        self.imagename = imagename

    def _step(self, message, percent):
        """Say what is imported next, and how many of the steps are done."""
        self.message.emit(message)
        self.progress.emit(percent)

    def _casa_told(self, event, what, megabytes):
        """CASA asks its servers for newer data, or downloads them
        (processing.casa_data): the splash says so while it lasts."""
        from .processing import casa_data

        self.waiting.emit(*casa_data.describe(event, what, megabytes))

    def run(self):
        try:
            self._step("Loading Astropy…", 0)
            try:
                import astropy.io.fits  # noqa: F401
            except ImportError:
                pass

            self._step("Reading Astropy's time tables…", 15)

            # Astropy's time tables: those it has, never fetched while a time
            # is converted (as drawing an image does, on the window's thread)
            from .processing.astropy_tables import use_local_tables

            use_local_tables()

            self._step("Loading Matplotlib…", 30)
            import matplotlib.backends.backend_qt5agg  # noqa: F401

            self._step("Loading CASA tools…", 45)
            # Imported, CASA first looks for newer data of its own (once a
            # day) and downloads them: a minute at times, of which it says
            # nothing itself
            from .processing import casa_data

            with casa_data.told(self._casa_told):
                from .processing import dependencies  # noqa: F401
            self.waiting.emit("", "", "")

            self._step("Loading the viewer…", 60)
            from .viewer import SolarRadioImageViewerApp

            # Return the imported class, not the instance
            self.finished_loading.emit(SolarRadioImageViewerApp)

        except Exception as e:
            import traceback

            error_details = "".join(
                traceback.format_exception(None, e, e.__traceback__)
            )
            logging.getLogger(__name__).error(f"Error in loader thread: {e}")
            self.error.emit(error_details)


def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="SolarViewer - A tool for visualizing and analyzing solar radio images and more.. ",
        epilog="""
  Full-featured viewer with comprehensive analysis tools, 
                  coordinate systems, region selection, and statistical analysis.

Examples:
  solarviewer                      # Launch standard viewer
  solarviewer image.fits           # Open image.fits in standard viewer
  sv image.fits                    # Same as above using short command
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "imagename",
        nargs="?",
        default=None,
        help="Path to the image file to open (FITS or CASA format)",
    )
    parser.add_argument(
        "-f",
        "--fast",
        action="store_true",
        help="Start with fast preview mode enabled",
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

    # Desktop Integration
    parser.add_argument(
        "--install",
        action="store_true",
        help="Install desktop integration (menu shortcut and icon)",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Uninstall desktop integration",
    )

    args = parser.parse_args()

    # Handle Desktop Integration
    if args.install:
        from .install_utils import install_desktop_integration

        success = install_desktop_integration()
        sys.exit(0 if success else 1)

    if args.uninstall:
        from .install_utils import uninstall_desktop_integration

        success = uninstall_desktop_integration()
        sys.exit(0 if success else 1)

    # Check if the specified image file exists
    if args.imagename and not os.path.exists(args.imagename):
        print(f"Error: Image file '{args.imagename}' not found.")
        print("Please provide a valid path to an image file.")
        sys.exit(1)

    # Crash reports of earlier sessions that crashed in native code (a
    # segfault, an abort), made before this session writes to the log; and
    # this session's stacks written down should it crash so
    native_crash_reports = watch_for_native_crashes()

    # The application: scaled for the display, themed (the saved theme, or
    # light with --light) and fonted, before the viewer is imported so
    # Matplotlib's colours are right from the start
    app = create_app(ThemeManager.LIGHT if args.light else None)
    settings = QSettings("SolarViewer", "SolarViewer")

    # === STANDARD VIEWER LAUNCH SEQUENCE ===

    # 1. Show Splash Screen, first of all: nothing else is on screen yet
    # (Imports are fast due to pkg_resources removal)
    from .splash import ModernSplashScreen

    splash = ModernSplashScreen(version=__version__)
    splash.show()
    app.processEvents()

    # === Initialize Log Console ===
    # Everything printed or logged also goes to the log file
    from . import app_logging

    app_logging.setup()

    # Redirect stdout/stderr to the console window
    # Initialize AFTER theme is set so it picks up the correct style
    from .log_console import LogConsole

    LogConsole.get_instance()

    # 2. Setup Loading Thread
    loader = LoaderThread(args.imagename)

    # Container for the window (to be populated by thread callback)
    window_container = {"window": None}

    def on_progress(val):
        splash.set_progress(val)

    def on_message(msg):
        splash.show_message(msg)

    def on_failure(error_msg):
        """Handle startup failure by showing an error dialog."""
        splash.hide()  # Hide splash so it doesn't cover the error

        show_error(
            None,
            "Startup error",
            "SolarViewer failed to start. Check that all dependencies are installed "
            "and try again; the details say what went wrong.",
            error_msg,
        )
        sys.exit(1)

    def on_finished(ViewerClass):
        # This runs in the main thread
        try:
            splash.step("Applying the theme…", 72)

            # Matplotlib is loaded now: give it the theme's colours too
            apply_theme(app)

            # Instantiate the main window now that classes are imported and theme is active
            win = ViewerClass(args.imagename, fast_preview=args.fast, on_stage=splash.step)
            window_container["window"] = win

            # Remember the theme chosen in the View menu
            theme_manager.register_callback(
                lambda new_theme: settings.setValue("theme", new_theme), owner=win
            )

            # Show the window first: splash.finish waits for it to appear
            splash.step("Opening the window…", 99)
            win.show()
            splash.finish(win)

            # From now on, unhandled exceptions are reported but not fatal
            set_main_window(win)
            show_native_crash_reports(native_crash_reports)

            # What CASA's look for newer data cost this start, and how to
            # turn it off: in the log to read at leisure
            from .processing import casa_data

            headline, advice = casa_data.start_note()
            if headline:
                logger.info(f"{headline}. {advice}")
                win.statusBar().showMessage(
                    f"{headline}. The log (View > Log console) says how to turn that off.", 30000
                )

            # sunpy's solar colormaps take ~1 s to import; load them in the
            # background now that the window is up (they load on first use
            # anyway if something needs them sooner)
            import threading

            from .processing.colormaps import ensure_solar_colormaps

            threading.Thread(
                target=ensure_solar_colormaps, name="solar-colormaps", daemon=True
            ).start()

            # Temporary files left by sessions that crashed or were killed,
            # and cache entries long unused (processing.temp_files)
            from .processing.temp_files import clean_temp_files_in_background

            clean_temp_files_in_background()

            # Newer time tables for astropy, where its own are old (used from
            # the next start)
            from .processing.astropy_tables import refresh_in_background

            refresh_in_background()

            # An image named on the command line: with the window up and the
            # splash gone, so that what loading it says can be seen
            win.open_start_image()

        except Exception as e:
            import traceback

            error_details = "".join(
                traceback.format_exception(None, e, e.__traceback__)
            )
            logger.error(f"Startup failed: {e}")
            on_failure(error_details)

    loader.progress.connect(on_progress)
    loader.message.connect(on_message)
    loader.waiting.connect(splash.set_waiting)
    loader.finished_loading.connect(on_finished)
    loader.error.connect(on_failure)  # Connect the new error signal

    # 3. Start Loading
    loader.start()

    # 4. Enter Main Event Loop
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

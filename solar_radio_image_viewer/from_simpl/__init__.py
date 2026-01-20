"""
SIMPL GUI Tools for Solar Radio Image Viewer
=============================================

This module provides launcher functions for GUI tools originally developed
for the SIMPL LOFAR pipeline, adapted for use within Solar Radio Image Viewer.

Tools included:
- Dynamic Spectrum Viewer: View and clean dynamic spectra FITS files
- Calibration Table Visualizer: Visualize bandpass/selfcal/crossphase tables
- Dynamic Spectra Creator: Generate dynamic spectra from MS files
- Log Viewer: View and filter pipeline log files
"""

# Track which tools are available based on dependencies
DYNAMIC_SPECTRUM_AVAILABLE = False
CALTABLE_VISUALIZER_AVAILABLE = False
DYNAMIC_SPECTRA_CREATOR_AVAILABLE = False
LOG_VIEWER_AVAILABLE = False

# Check dependencies for each tool
try:
    import cv2
    import numpy as np
    from astropy.io import fits

    DYNAMIC_SPECTRUM_AVAILABLE = True
except ImportError as e:
    print(f"[from_simpl] Dynamic Spectrum Viewer not available: {e}")

try:
    import seaborn
    from casacore.tables import table

    CALTABLE_VISUALIZER_AVAILABLE = True
except ImportError as e:
    print(f"[from_simpl] Calibration Table Visualizer not available: {e}")
    print("  Install with: pip install python-casacore seaborn")

try:
    from casacore.tables import table
    from astropy.io import fits

    DYNAMIC_SPECTRA_CREATOR_AVAILABLE = True
except ImportError as e:
    print(f"[from_simpl] Dynamic Spectra Creator not available: {e}")
    print("  Install with: pip install python-casacore astropy")

# Log viewer just needs PyQt5 which is always available
LOG_VIEWER_AVAILABLE = True


def launch_dynamic_spectrum_viewer(parent=None, fits_file=None):
    """
    Launch the Dynamic Spectrum Viewer window.

    Parameters
    ----------
    parent : QWidget, optional
        Parent widget for the viewer window
    fits_file : str, optional
        Path to a FITS file to open automatically

    Returns
    -------
    MainWindow
        The viewer window instance
    """
    if not DYNAMIC_SPECTRUM_AVAILABLE:
        from PyQt5.QtWidgets import QMessageBox

        QMessageBox.warning(
            parent,
            "Missing Dependencies",
            "Dynamic Spectrum Viewer requires: opencv-python, numpy, astropy\n\n"
            "Install with: pip install opencv-python astropy",
        )
        return None

    from .view_dynamic_spectra_GUI import MainWindow

    window = MainWindow()
    window.show()

    # Open file if provided
    if fits_file:
        # The MainWindow has an openFile method we can use
        # But we need to load the file programmatically
        window._load_fits_file(fits_file)

    return window


def launch_caltable_visualizer(parent=None):
    """
    Launch the Calibration Table Visualizer window.

    Parameters
    ----------
    parent : QWidget, optional
        Parent widget for the viewer window

    Returns
    -------
    VisualizationApp
        The visualizer window instance
    """
    if not CALTABLE_VISUALIZER_AVAILABLE:
        from PyQt5.QtWidgets import QMessageBox

        QMessageBox.warning(
            parent,
            "Missing Dependencies",
            "Calibration Table Visualizer requires: casacore, seaborn\n\n"
            "Install with: pip install python-casacore seaborn",
        )
        return None

    from .caltable_visualizer import VisualizationApp

    window = VisualizationApp()
    window.show()
    return window


def launch_log_viewer(parent=None, log_file=None):
    """
    Launch the Log Viewer window.

    Parameters
    ----------
    parent : QWidget, optional
        Parent widget for the viewer window
    log_file : str, optional
        Path to a log file to open automatically

    Returns
    -------
    PipelineLoggerGUI
        The log viewer window instance
    """
    from .pipeline_logger_gui import PipelineLoggerGUI

    window = PipelineLoggerGUI()
    window.show()

    if log_file:
        # Load the log file using the internal method pattern
        window.log_monitor.set_log_file(log_file)
        # Parse and load the log file
        import os
        from .pipeline_logger_gui import LogRecord

        if os.path.exists(log_file):
            with open(log_file, "r") as f:
                for line in f:
                    try:
                        parts = line.strip().split(" - ", 3)
                        if len(parts) >= 4:
                            timestamp, level, name, message = parts
                            log_record = LogRecord(level, name, message, timestamp)
                            window.log_model.add_log(log_record)
                    except Exception:
                        pass

    return window


def launch_dynamic_spectra_dialog(parent=None):
    """
    Launch dialog to create dynamic spectra from MS files.

    Parameters
    ----------
    parent : QWidget, optional
        Parent widget for the dialog

    Returns
    -------
    DynamicSpectraDialog
        The dialog instance
    """
    if not DYNAMIC_SPECTRA_CREATOR_AVAILABLE:
        from PyQt5.QtWidgets import QMessageBox

        QMessageBox.warning(
            parent,
            "Missing Dependencies",
            "Dynamic Spectra Creator requires: casacore, astropy\n\n"
            "Install with: pip install python-casacore astropy",
        )
        return None

    from .dynamic_spectra_dialog import DynamicSpectraDialog

    dialog = DynamicSpectraDialog(parent)
    dialog.exec_()
    return dialog

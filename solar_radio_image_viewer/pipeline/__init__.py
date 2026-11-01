"""
SIMPL GUI Tools for Solar Radio Image Viewer
=============================================

This module provides launcher functions for GUI tools originally developed
for the SIMPL LOFAR pipeline, adapted for use within Solar Radio Image Viewer.

Tools included:
- Dynamic Spectrum Viewer: View and clean dynamic spectra FITS files
- Calibration Table Visualizer: Visualize bandpass/selfcal/crossphase tables
- Dynamic Spectra Creator: Generate dynamic spectra from MS files (the dialog
  needs no casacore: only the machine that runs the engine does)
- Log Viewer: View and filter pipeline log files
"""

from importlib.util import find_spec

# Check dependencies for each tool: finding them, not importing them, which
# would take every tool over a second before it can show anything (and load
# casacore here, which must stay out of SolarViewer's process)
_missing = [m for m in ("numpy", "pyqtgraph", "astropy") if find_spec(m) is None]
DYNAMIC_SPECTRUM_AVAILABLE = not _missing
if _missing:
    print(f"[pipeline] Dynamic Spectrum Viewer not available: no {', '.join(_missing)}")

_missing = [m for m in ("seaborn", "casacore") if find_spec(m) is None]
CALTABLE_VISUALIZER_AVAILABLE = not _missing
if _missing:
    print(f"[pipeline] Calibration Table Visualizer not available: no {', '.join(_missing)}")
    print("  Install with: pip install python-casacore seaborn")

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
            "Dynamic Spectrum Viewer requires: numpy, astropy, pyqtgraph\n\n"
            "Install with: pip install astropy pyqtgraph",
        )
        return None

    from .view_dynamic_spectra_GUI import MainWindow

    window = MainWindow()
    window.show()

    # Open file if provided
    if fits_file:
        # Once the window is on screen, so what it says while opening shows
        from PyQt5.QtCore import QTimer

        QTimer.singleShot(0, lambda: window.openFile(fits_file))

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
        window.openFile(log_file)

    return window

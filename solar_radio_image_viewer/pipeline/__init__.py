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

import sys
from importlib.util import find_spec

_PACKAGES = {"casacore": "python-casacore"}  # modules whose package has another name


def install_hint(missing, platform=None) -> str:
    """The commands that install the ``missing`` modules here, one to a line.
    python-casacore has no wheels for macOS: pip would compile it there,
    which needs the casacore C++ libraries, so on a Mac it is taken from
    conda-forge, which has it built."""
    names = [_PACKAGES.get(module, module) for module in missing]
    lines = []
    if (platform or sys.platform) == "darwin" and "python-casacore" in names:
        names.remove("python-casacore")
        lines.append("conda install -c conda-forge python-casacore")
    if names:
        lines.append("pip install " + " ".join(names))
    return "\n".join(lines)


# Check dependencies for each tool: finding them, not importing them, which
# would take every tool over a second before it can show anything (and load
# casacore here, which must stay out of SolarViewer's process)
_missing = [m for m in ("numpy", "pyqtgraph", "astropy") if find_spec(m) is None]
DYNAMIC_SPECTRUM_AVAILABLE = not _missing
if _missing:
    print(f"[pipeline] Dynamic Spectrum Viewer not available: no {', '.join(_missing)}")

# (python-casacore is not installed with SolarViewer on macOS: see pyproject.toml)
CALTABLE_MISSING = [m for m in ("casacore", "seaborn") if find_spec(m) is None]
CALTABLE_VISUALIZER_AVAILABLE = not CALTABLE_MISSING
if CALTABLE_MISSING:
    print(f"[pipeline] Calibration Table Visualizer not available: no {', '.join(CALTABLE_MISSING)}")
    print("  Install with: " + install_hint(CALTABLE_MISSING).replace("\n", "  and  "))



def caltable_missing_text(platform=None) -> str:
    """Why the Calibration Table Visualizer cannot start here, and what
    installs what it lacks; "" when it can."""
    if not CALTABLE_MISSING:
        return ""
    text = (
        f"Calibration Table Visualizer requires: {', '.join(CALTABLE_MISSING)}\n\n"
        f"Install with:\n{install_hint(CALTABLE_MISSING, platform)}"
    )
    if (platform or sys.platform) == "darwin" and "casacore" in CALTABLE_MISSING:
        text += (
            "\n\npython-casacore is not installed with SolarViewer on a Mac. It needs "
            "SolarViewer in a conda environment: see “macOS (Apple Silicon)” in INSTALLATION.md."
        )
    return text


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

        QMessageBox.warning(parent, "Missing Dependencies", caltable_missing_text())
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

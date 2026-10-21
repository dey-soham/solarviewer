"""
Small helpers of the Create Video window: output formats, frame size
presets, durations, the wait cursor, and images of the main window.
"""

import os
from PyQt5.QtWidgets import (
    QApplication,
)
from PyQt5.QtCore import Qt
from contextlib import contextmanager
from ...processing.fits_io import is_casa_image


OUTPUT_FORMATS = [
    ("MP4 (H.264)", ".mp4"),
    ("WebM (VP9)", ".webm"),
    ("GIF", ".gif"),
    ("AVI", ".avi"),
    ("PNG frames", ".png"),
]


SIZE_PRESETS = [
    ("Auto (1000 px)", 0),
    ("720 px tall", 720),
    ("1080 px tall", 1080),
    ("1440 px tall", 1440),
    ("2160 px tall (4K)", 2160),
]


def _format_duration(seconds):
    """'0.3 s', '45 s', '3 min', '1 h 20 min'."""
    if seconds < 10:
        return f"{seconds:.1f} s"
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60} min"


@contextmanager
def wait_cursor():
    """Context manager to show a wait cursor"""
    QApplication.setOverrideCursor(Qt.WaitCursor)
    try:
        yield
    finally:
        QApplication.restoreOverrideCursor()


def is_image(path):
    """Whether ``path`` is an image the video can show: a FITS file (also
    compressed) or a CASA image."""
    from ...processing.fits_io import FITS_SUFFIXES

    return is_casa_image(path) or (
        os.path.isfile(path) and path.lower().endswith(FITS_SUFFIXES)
    )


def viewer_image(main_window):
    """(image file, coordinates) shown in the main window's current tab: the
    original file and "hpc" / "radec" when it shows a converted copy of it
    (its HPC / RA-Dec button), else (its file, "native")."""
    tab_widget = getattr(main_window, "tab_widget", None)
    tab = tab_widget.currentWidget() if tab_widget is not None else None
    name = getattr(tab, "imagename", None)
    if not name:
        return None, "native"
    if name == getattr(tab, "_hpc_temp_file", None) and getattr(
        tab, "_original_imagename", None
    ):
        return tab._original_imagename, "hpc"
    if name == getattr(tab, "_radec_temp_file", None) and getattr(
        tab, "_hpc_original_imagename", None
    ):
        return tab._hpc_original_imagename, "radec"
    return name, "native"


"""
The images opened recently (File > Open Recent), and the folder the last one
came from, kept in the application's settings between sessions; and, in a
list of their own, the dynamic spectra opened in the spectrum viewer.
"""

import os

from PyQt5.QtCore import QSettings

MAX_RECENT = 30


def _settings():
    return QSettings("SolarViewer", "SolarViewer")


def _stored(key="recent_images"):
    value = _settings().value(key, [])
    if isinstance(value, str):  # QSettings returns a one-item list as a string
        value = [value]
    return [str(path) for path in value or []]


def recent_images():
    """The recently opened images that still exist, most recent first."""
    return [path for path in _stored() if os.path.exists(path)]


def remember_image(path):
    """Put the local image ``path`` (FITS file or CASA image folder) first in
    the recent list, and remember its folder."""
    path = os.path.abspath(path.rstrip("/\\") or path)
    # (files gone since are dropped first, so they do not take up the slots)
    paths = [path] + [p for p in _stored() if p != path and os.path.exists(p)]
    settings = _settings()
    settings.setValue("recent_images", paths[:MAX_RECENT])
    settings.setValue("last_image_folder", os.path.dirname(path))


def forget_image(path):
    """Take ``path`` off the recent list (it could not be opened)."""
    settings = _settings()
    settings.setValue("recent_images", [p for p in _stored() if p != path])


def clear_recent_images():
    _settings().remove("recent_images")


def recent_spectra():
    """The dynamic spectra opened recently that still exist, most recent first."""
    return [path for path in _stored("recent_spectra") if os.path.exists(path)]


def remember_spectrum(path):
    """Put the local dynamic spectrum ``path`` first in its recent list."""
    path = os.path.abspath(path)
    stored = _stored("recent_spectra")
    paths = [path] + [p for p in stored if p != path and os.path.exists(p)]
    _settings().setValue("recent_spectra", paths[:MAX_RECENT])


def forget_spectrum(path):
    """Take ``path`` off the recent spectra (it could not be opened)."""
    path = os.path.abspath(path)
    _settings().setValue("recent_spectra", [p for p in _stored("recent_spectra") if p != path])


def clear_recent_spectra():
    _settings().remove("recent_spectra")


def last_image_folder():
    """The folder of the last image opened, if it still exists, else ""."""
    folder = str(_settings().value("last_image_folder", "") or "")
    return folder if os.path.isdir(folder) else ""

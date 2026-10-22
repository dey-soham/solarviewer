"""
The images opened recently (File > Open Recent), and the folder the last one
came from, kept in the application's settings between sessions.
"""

import os

from PyQt5.QtCore import QSettings

MAX_RECENT = 10


def _settings():
    return QSettings("SolarViewer", "SolarViewer")


def _stored():
    value = _settings().value("recent_images", [])
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
    paths = [path] + [p for p in _stored() if p != path]
    settings = _settings()
    settings.setValue("recent_images", paths[:MAX_RECENT])
    settings.setValue("last_image_folder", os.path.dirname(path))


def forget_image(path):
    """Take ``path`` off the recent list (it could not be opened)."""
    settings = _settings()
    settings.setValue("recent_images", [p for p in _stored() if p != path])


def clear_recent_images():
    _settings().remove("recent_images")


def last_image_folder():
    """The folder of the last image opened, if it still exists, else ""."""
    folder = str(_settings().value("last_image_folder", "") or "")
    return folder if os.path.isdir(folder) else ""

"""
Paths to package resources (icons, fonts) and theme-aware icon lookup.
"""

import os

# Package root (solar_radio_image_viewer/), where assets/ lives
PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_resource_path(*parts):
    """Get absolute path to a package resource file.

    Uses os.path for speed (avoids slow pkg_resources import).

    Args:
        *parts: Path relative to the package directory, as one string
            (e.g., 'assets/icon.png') or several components.

    Returns:
        Absolute path to the resource file
    """
    return os.path.join(PACKAGE_DIR, *parts)


def themed_icon(icon_name):
    """Get the full resource path for a theme-appropriate icon.

    Args:
        icon_name: Base icon filename (e.g., 'browse.png')

    Returns:
        Full resource path to the appropriate icon
    """
    from .styles import get_icon_path

    themed_name = get_icon_path(icon_name)
    return get_resource_path(f"assets/{themed_name}")


def launcher_icon(icon_name, size=16):
    """A QIcon of the theme's ``icon_name`` that also fades when its
    button is disabled (Qt draws a vector icon the same either way)."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QIcon, QPainter, QPixmap

    icon = QIcon(themed_icon(icon_name))
    pixmap = icon.pixmap(size * 2, size * 2)
    faded = QPixmap(pixmap.size())
    faded.fill(Qt.transparent)
    painter = QPainter(faded)
    painter.setOpacity(0.4)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    icon.addPixmap(faded, QIcon.Disabled)
    return icon

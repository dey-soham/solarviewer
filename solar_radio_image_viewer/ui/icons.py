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
    """A QIcon of the theme's ``icon_name`` that also greys when its
    button is disabled (Qt draws a vector icon the same either way): an
    SVG icon is redrawn in the palette's disabled colour, like disabled
    text, and any other icon fades."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QIcon, QPainter, QPixmap

    path = themed_icon(icon_name)
    if path.endswith(".svg"):
        # Built from pixmaps alone: an icon read from the SVG file would
        # draw the file afresh for any size it has no pixmap for, with
        # Qt's own idea of disabled
        icon = QIcon()
        for pixels in (size, size * 2):
            normal = _svg_pixmap(path, pixels)
            greyed = _svg_pixmap(path, pixels, _disabled_colour())
            if normal is None or greyed is None:
                break
            icon.addPixmap(normal, QIcon.Normal)
            icon.addPixmap(greyed, QIcon.Disabled)
        if not icon.isNull():
            return icon
    icon = QIcon(path)
    pixmap = icon.pixmap(size * 2, size * 2)
    faded = QPixmap(pixmap.size())
    faded.fill(Qt.transparent)
    painter = QPainter(faded)
    painter.setOpacity(0.4)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    icon.addPixmap(faded, QIcon.Disabled)
    return icon


def _disabled_colour():
    from .styles import theme_manager

    return theme_manager.palette["disabled"]


def _svg_pixmap(path, size, colour=None):
    """The SVG at ``path`` drawn ``size`` pixels square, every colour in
    it replaced by ``colour`` when one is given; None if it cannot be
    drawn."""
    import re

    from PyQt5.QtCore import QByteArray, Qt
    from PyQt5.QtGui import QPainter, QPixmap

    try:
        from PyQt5.QtSvg import QSvgRenderer

        with open(path, encoding="utf-8") as handle:
            markup = handle.read()
    except (ImportError, OSError):
        return None
    if colour:
        markup = re.sub(r"#[0-9a-fA-F]{3,8}\b", colour, markup)
    renderer = QSvgRenderer(QByteArray(markup.encode("utf-8")))
    if not renderer.isValid():
        return None
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter)
    painter.end()
    return pixmap


# The playback buttons' shapes on a 16 px square: filled polygons
_BAR_LEFT = ((3, 3), (5, 3), (5, 13), (3, 13))
_BAR_RIGHT = ((11, 3), (13, 3), (13, 13), (11, 13))
TRANSPORT_SHAPES = {
    "first": (_BAR_LEFT, ((13, 3), (6, 8), (13, 13))),
    "previous": (((12, 3), (4, 8), (12, 13)),),
    "play": (((4, 2.5), (13, 8), (4, 13.5)),),
    "pause": (((4, 3), (7, 3), (7, 13), (4, 13)), ((9, 3), (12, 3), (12, 13), (9, 13))),
    "next": (((4, 3), (12, 8), (4, 13)),),
    "last": (((3, 3), (10, 8), (3, 13)), _BAR_RIGHT),
}


def transport_icon(kind, palette):
    """The icon of a playback button (``first``, ``previous``, ``play``,
    ``pause``, ``next``, ``last``) in the theme's colours, greyed when its
    button is disabled. Painted, so it follows a theme change when asked
    again."""
    from PyQt5.QtCore import QPointF, Qt
    from PyQt5.QtGui import QColor, QIcon, QPainter, QPixmap, QPolygonF

    icon = QIcon()
    for mode, key in ((QIcon.Normal, "text_strong"), (QIcon.Disabled, "disabled")):
        for scale in (1, 2):
            pixmap = QPixmap(16 * scale, 16 * scale)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.scale(scale, scale)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(palette[key]))
            for shape in TRANSPORT_SHAPES[kind]:
                painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in shape]))
            painter.end()
            icon.addPixmap(pixmap, mode)
    return icon

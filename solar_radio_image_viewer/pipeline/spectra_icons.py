"""
The dynamic spectrum viewer's line icons (the Nocturne handoff's), drawn
in the theme's colours: muted, brighter when hovered or chosen, the
accent when on.
"""

from PyQt5.QtCore import QByteArray, Qt
from PyQt5.QtGui import QIcon, QPainter, QPixmap

# The inside of each 24x24 SVG (stroked, no fill)
_SHAPES = {
    "pan": '<path d="M8 13V5a1.5 1.5 0 013 0v7M11 11V4a1.5 1.5 0 013 0v8M14 11V6a1.5 1.5 0 013 0v9'
           'M8 13l-2-2a1.5 1.5 0 00-2 2l4 6c1 1.5 2.5 2 4.5 2h2a5 5 0 005-5v-4"/>',
    "mask": '<rect x="4" y="5" width="16" height="14" rx="1" stroke-dasharray="3 2"/><path d="M4 5l16 14"/>',
    "keep": '<rect x="4" y="5" width="16" height="14" rx="1" stroke-dasharray="3 2"/><path d="M8 12l3 3 5-6"/>',
    "profile": '<path d="M3 18c3 0 3-10 6-10s3 6 6 6 3-8 6-8"/>',
    "drift": '<path d="M4 4c4 2 10 8 16 16"/><circle cx="4" cy="4" r="1.5"/><circle cx="20" cy="20" r="1.5"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/>',
    "zoom_in": '<circle cx="11" cy="11" r="6"/><path d="M16 16l4 4M8 11h6M11 8v6"/>',
    "zoom_out": '<circle cx="11" cy="11" r="6"/><path d="M16 16l4 4M8 11h6"/>',
    "reset": '<path d="M20 12a8 8 0 11-2.3-5.7M20 4v4h-4"/>',
    "clock": '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>',
}


def _pixmap(name, colour, size):
    from PyQt5.QtSvg import QSvgRenderer

    width = "1.8" if name == "clock" else "1.6"
    markup = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
        f'fill="none" stroke="{colour}" stroke-width="{width}" stroke-linecap="round" '
        f'stroke-linejoin="round">{_SHAPES[name]}</svg>'
    )
    renderer = QSvgRenderer(QByteArray(markup.encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter)
    painter.end()
    return pixmap


def icon(name, palette, size=16):
    """The icon ``name`` in ``palette``'s colours: text_secondary, the
    text when hovered (Active), the accent when checked (On), greyed when
    disabled."""
    result = QIcon()
    for pixels in (size, size * 2):
        result.addPixmap(_pixmap(name, palette["text_secondary"], pixels), QIcon.Normal, QIcon.Off)
        result.addPixmap(_pixmap(name, palette["text"], pixels), QIcon.Active, QIcon.Off)
        result.addPixmap(_pixmap(name, palette["highlight"], pixels), QIcon.Normal, QIcon.On)
        result.addPixmap(_pixmap(name, palette["highlight"], pixels), QIcon.Active, QIcon.On)
        result.addPixmap(_pixmap(name, palette["disabled"], pixels), QIcon.Disabled, QIcon.Off)
    return result


def colormap_swatch(name, width=36, height=8):
    """A small gradient of the colormap ``name`` (left: low), rounded."""
    import matplotlib.pyplot as plt
    import numpy as np
    from PyQt5.QtCore import QRectF
    from PyQt5.QtGui import QColor, QLinearGradient, QPainterPath

    colours = plt.get_cmap(name)(np.linspace(0, 1, 9))
    scale = 2
    pixmap = QPixmap(width * scale, height * scale)
    pixmap.setDevicePixelRatio(scale)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    gradient = QLinearGradient(0, 0, width, 0)
    for i, (r, g, b, _a) in enumerate(colours):
        gradient.setColorAt(i / (len(colours) - 1), QColor.fromRgbF(r, g, b))
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, width, height), 2, 2)
    painter.fillPath(path, gradient)
    painter.end()
    return QIcon(pixmap)

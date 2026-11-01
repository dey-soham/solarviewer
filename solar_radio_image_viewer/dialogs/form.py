"""
The pieces of a settings dialog's flat form, as the batch windows and the
inspector have them: blocks of rows under a kicker, each row named in the
Hint role, and values as a name over a monospace number.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PyQt5.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout

from ..ui.styles import FONT_SMALL, SPACE_L, SPACE_M, SPACE_S, SPACE_XS, themed
from ..ui.widgets import kicker


def field_label(text):
    """A row's name at its left, as BatchWindow.row_label."""
    label = QLabel(text)
    label.setObjectName("Hint")
    metrics = label.fontMetrics()
    width = max(metrics.horizontalAdvance(text), metrics.horizontalAdvance("Reference"))
    label.setMinimumWidth(width + SPACE_S)
    return label


def form_block(layout, title=None, pairs=1, longest=""):
    """A block added to ``layout``: ``title`` as a kicker over a grid of
    rows with ``pairs`` of (name, field) side by side. ``longest`` is the
    longest name in the blocks of a window, so that their fields line up.
    Returns the grid."""
    block = QVBoxLayout()
    block.setSpacing(SPACE_S)
    if title:
        block.addWidget(kicker(title))
    grid = QGridLayout()
    grid.setHorizontalSpacing(SPACE_M)
    grid.setVerticalSpacing(SPACE_S)
    for pair in range(pairs):
        grid.setColumnStretch(2 * pair + 1, 1)
        if longest:
            grid.setColumnMinimumWidth(2 * pair, field_label(longest).minimumWidth())
    block.addLayout(grid)
    layout.addLayout(block)
    return grid


def swatch_icon(color, outline, size=14):
    """``color`` as a square with an outline, so that white and black show
    on any ground."""
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(color))
    painter = QPainter(pixmap)
    painter.setPen(QColor(outline))
    painter.drawRect(0, 0, size - 1, size - 1)
    painter.end()
    return QIcon(pixmap)


def follow_swatches(combo):
    """Keep the swatches of ``combo``'s items (their data is the colour) in
    the theme's outline."""

    def outline(palette):
        for index in range(combo.count()):
            color = combo.itemData(index)
            if color:
                combo.setItemIcon(index, swatch_icon(color, palette["text_faint"]))

    themed(combo, outline)


def color_combo(colors, current=None):
    """A choice of Matplotlib colour names, each with its swatch: the name
    is the item's data, the text its capitalised form."""
    combo = QComboBox()
    for color in colors:
        combo.addItem(color.capitalize(), color)
    combo.setCurrentIndex(max(combo.findData(current), 0))
    follow_swatches(combo)
    return combo


def canvas_ground(canvas):
    """Keep ``canvas``'s own ground, white by Matplotlib's choice, in the
    theme's: the figure can fall a pixel short of the widget, showing it."""
    themed(canvas, lambda palette: canvas.setPalette(QPalette(QColor(palette["window"]))))


def stat_row(rows, layout):
    """``rows`` [(name, value), ...] added to ``layout`` as columns of a name
    over a monospace value, as the inspector's Max and RMS. Returns the
    value labels by name."""
    row = QHBoxLayout()
    row.setSpacing(SPACE_L)
    values = {}
    for name, value in rows:
        column = QVBoxLayout()
        column.setSpacing(SPACE_XS // 2)
        key = QLabel(name)
        key.setObjectName("Meta")
        column.addWidget(key)
        values[name] = QLabel(value)
        values[name].setObjectName("Mono")
        values[name].setStyleSheet(f"font-size: {FONT_SMALL}pt;")
        values[name].setTextInteractionFlags(Qt.TextSelectableByMouse)
        column.addWidget(values[name])
        row.addLayout(column)
    row.addStretch()
    layout.addLayout(row)
    return values

"""
Window sizes that fit the screen they open on.
"""

from PyQt5.QtCore import QSize
from PyQt5.QtWidgets import QApplication


def available_size(widget=None):
    """The free area of the screen ``widget`` (or its window) is on, or of
    the primary screen."""
    screen = None
    if widget is not None:
        window = widget.window()
        parent = window.parentWidget()
        anchor = parent.window() if parent is not None else window
        try:
            screen = anchor.screen()
        except AttributeError:  # older Qt
            screen = None
    if screen is None:
        screen = QApplication.primaryScreen()
    if screen is None:  # no display at all
        return QSize(1280, 800)
    return screen.availableGeometry().size()


def fit_to_screen(window, width, height, fraction=0.9):
    """Size ``window`` to ``width`` x ``height``, or to ``fraction`` of the
    free screen area if that is smaller: a window laid out for a big
    monitor opens no larger than a laptop screen. A minimum size the
    window set that would not fit is lowered too, so it can still be
    shrunk to fit."""
    free = available_size(window)
    max_w = int(free.width() * fraction)
    max_h = int(free.height() * fraction)
    minimum = window.minimumSize()
    if minimum.width() > max_w or minimum.height() > max_h:
        window.setMinimumSize(min(minimum.width(), max_w), min(minimum.height(), max_h))
    window.resize(min(width, max_w), min(height, max_h))

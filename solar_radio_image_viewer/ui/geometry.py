"""
Window sizes that fit the screen they open on.
"""

import os

from PyQt5.QtCore import QSize
from PyQt5.QtWidgets import QApplication

# Desktops whose window manager (GNOME's, and those built on it) fastens a
# modal dialog to the window it was opened from
_FASTENING = ("gnome", "cinnamon", "pantheon")


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


def fit_width_to_text(widget, sample):
    """Give ``widget`` (a line edit or label) the width of ``sample`` in its
    font, plus room for the frame: a field sized for "0.000" rather than
    a pixel count that is wrong at another font size."""
    from PyQt5.QtWidgets import QAbstractSpinBox

    extra = 44 if isinstance(widget, QAbstractSpinBox) else 20  # frame, buttons
    widget.setFixedWidth(widget.fontMetrics().horizontalAdvance(sample) + extra)


def fastens_dialogs(platform=None, env=None):
    """Whether this desktop fastens a modal dialog to the window it was
    opened from, so that it cannot be moved by itself: dragging it drags
    that window (GNOME's "attach modal dialogs"). Only on X11: there Qt
    tells the desktop that a dialog is modal; on Wayland Qt 5 does not, and
    the dialog moves freely."""
    if platform is None:
        platform = QApplication.platformName()
    if platform != "xcb":
        return False
    env = os.environ if env is None else env
    desktops = (env.get("XDG_CURRENT_DESKTOP", "") + ":" + env.get("XDG_SESSION_DESKTOP", "")).lower()
    return any(name in desktops for name in _FASTENING)


def move_alone(dialog):
    """Let the modal ``dialog``, about to be shown, be moved by itself
    where the desktop would fasten it to the window it was opened from
    (``fastens_dialogs``): X11 is told of no such window. The dialog is
    then modal for the whole application: it stays above all its windows,
    which take no input meanwhile, as before. Its Qt parent is kept (it
    still opens over it). For a window worked in for a while, like a file
    browser; call it from ``setVisible``, before the dialog shows."""
    if not fastens_dialogs():
        return
    dialog.winId()  # (its window exists from here on)
    handle = dialog.windowHandle()
    if handle is not None and handle.transientParent() is not None:
        handle.setTransientParent(None)

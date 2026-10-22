"""
Modal dialogs that move on their own.

GNOME attaches a modal dialog to the window it belongs to: it opens glued to
that window's title bar and can't be moved apart from it. The modal dialogs
of a window passed to ``free_modal_dialogs`` are let go of it as they open:
still modal and opened over it, but moved around freely, as the other
windows of the application are. Short questions (message boxes, input
prompts) and file pickers stay attached, as is usual for them.
"""

from PyQt5.QtCore import QEvent, QObject
from PyQt5.QtWidgets import QApplication, QDialog, QFileDialog, QInputDialog, QMessageBox

from .qt_exit import before_qt_exit

_ROOT = "freesModalDialogs"  # (property of the windows registered)
_ATTACHED = (QMessageBox, QInputDialog, QFileDialog)


class _Releaser(QObject):
    def eventFilter(self, obj, event):
        if (
            event.type() == QEvent.Show
            and isinstance(obj, QDialog)
            and not isinstance(obj, _ATTACHED)
            and obj.isWindow()
            and obj.isModal()
            and _registered(obj.parentWidget())
        ):
            handle = obj.windowHandle()
            if handle is not None:
                handle.setTransientParent(None)
        return False


def _registered(widget):
    while widget is not None:
        if widget.property(_ROOT):
            return True
        widget = widget.parentWidget()
    return False


_releaser = None


def free_modal_dialogs(window):
    """Let the modal dialogs opened from ``window`` (or its children) be
    moved apart from it."""
    global _releaser
    window.setProperty(_ROOT, True)
    app = QApplication.instance()
    if _releaser is None and app is not None:
        _releaser = _Releaser(app)
        app.installEventFilter(_releaser)
        # Destroying windows at exit must not run this filter (see qt_exit)
        before_qt_exit(_stop_releasing)


def _stop_releasing():
    app = QApplication.instance()
    if _releaser is not None and app is not None:
        app.removeEventFilter(_releaser)

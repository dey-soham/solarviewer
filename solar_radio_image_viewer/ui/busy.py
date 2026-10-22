"""
Showing that the app is working on something.

Loading and drawing run on the GUI thread, so nothing on screen changes until
they finish: the wait cursor has to reach the screen before the work starts.
:func:`busy` sets it and lets Qt show it first. A dialog that opens while busy
(an error message, say) gets the normal cursor back until it closes.

Where a ``with`` block does not fit, begin_busy() and end_busy() do the same
in two calls, which must pair up like setOverrideCursor/restoreOverrideCursor.
"""

from contextlib import contextmanager

from PyQt5.QtCore import QEvent, QEventLoop, QObject, Qt
from PyQt5.QtWidgets import QApplication, QDialog

from .qt_exit import before_qt_exit

_depth = 0


class _DialogCursor(QObject):
    """While busy, takes the wait cursor away for as long as a dialog is shown."""

    def __init__(self):
        super().__init__()
        self._lifted = {}  # dialog -> how many wait cursors it lifted

    def eventFilter(self, obj, event):
        if isinstance(obj, QDialog):
            if event.type() == QEvent.Show and obj not in self._lifted:
                lifted = 0
                while QApplication.overrideCursor() is not None:
                    QApplication.restoreOverrideCursor()
                    lifted += 1
                self._lifted[obj] = lifted
            elif event.type() == QEvent.Hide and obj in self._lifted:
                for _ in range(self._lifted.pop(obj)):
                    QApplication.setOverrideCursor(Qt.WaitCursor)
        return False

    def release(self):
        """Put back the cursors of dialogs still open when the work ends."""
        for lifted in self._lifted.values():
            for _ in range(lifted):
                QApplication.setOverrideCursor(Qt.WaitCursor)
        self._lifted.clear()


_dialog_cursor = None


def begin_busy():
    """Show the wait cursor until the matching end_busy(). Nests."""
    global _depth, _dialog_cursor
    QApplication.setOverrideCursor(Qt.WaitCursor)
    _depth += 1
    if _depth == 1:
        _dialog_cursor = _DialogCursor()
        QApplication.instance().installEventFilter(_dialog_cursor)
        # Let the cursor change reach the screen before the work blocks
        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)


def end_busy():
    """End a begin_busy(). Does nothing without one, e.g. on an error path
    that can be reached before the begin_busy()."""
    global _depth, _dialog_cursor
    if _depth == 0:
        return
    _depth -= 1
    if _depth == 0:
        QApplication.instance().removeEventFilter(_dialog_cursor)
        _dialog_cursor.release()
        _dialog_cursor = None
    QApplication.restoreOverrideCursor()


def reset():
    """Forget every begin_busy() left open, after an unexpected error cut
    the work short (the crash handler removes the cursors themselves)."""
    global _depth, _dialog_cursor
    if _dialog_cursor is not None:
        QApplication.instance().removeEventFilter(_dialog_cursor)
        _dialog_cursor = None
    _depth = 0


# A begin_busy() left open at exit leaves its event filter on the
# application, which destroying the windows must not run (see qt_exit)
before_qt_exit(reset)


@contextmanager
def busy():
    """Show the wait cursor while the ``with`` block runs. Nests."""
    begin_busy()
    try:
        yield
    finally:
        end_busy()

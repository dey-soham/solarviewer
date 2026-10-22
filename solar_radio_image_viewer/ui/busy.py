"""
Showing that the app is working on something.

Loading and drawing run on the GUI thread, so nothing on screen changes until
they finish: the wait cursor has to reach the screen before the work starts.
:func:`busy` sets it and lets Qt show it first. A dialog that opens while busy
(an error message, say) gets the normal cursor back until it closes.
"""

from contextlib import contextmanager

from PyQt5.QtCore import QEvent, QEventLoop, QObject, Qt
from PyQt5.QtWidgets import QApplication, QDialog

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


@contextmanager
def busy():
    """Show the wait cursor while the ``with`` block runs. Nests."""
    global _depth, _dialog_cursor
    app = QApplication.instance()
    QApplication.setOverrideCursor(Qt.WaitCursor)
    _depth += 1
    if _depth == 1:
        _dialog_cursor = _DialogCursor()
        app.installEventFilter(_dialog_cursor)
        # Let the cursor change reach the screen before the work blocks
        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
    try:
        yield
    finally:
        _depth -= 1
        if _depth == 0:
            app.removeEventFilter(_dialog_cursor)
            _dialog_cursor.release()
            _dialog_cursor = None
        QApplication.restoreOverrideCursor()

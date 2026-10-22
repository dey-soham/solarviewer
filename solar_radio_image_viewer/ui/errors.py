"""
Error dialogs that say what went wrong and what to try, in plain words, with
the technical detail (the exception, a traceback) behind "Show Details".
"""

from PyQt5.QtWidgets import QMessageBox


def show_error(parent, title, message, detail=None):
    """A modal error dialog: ``message`` for the user, ``detail`` (an
    exception or text) for a bug report."""
    box = QMessageBox(QMessageBox.Critical, title, message, QMessageBox.Ok, parent)
    if detail:
        box.setDetailedText(str(detail))
    box.exec_()

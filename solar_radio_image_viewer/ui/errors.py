"""
Error dialogs that say what went wrong and what to try, in plain words, with
the technical detail (the exception, a traceback) behind "Show Details".
"""

from PyQt5.QtWidgets import QMessageBox


def word_details_button(box):
    """Sentence case on the details button of ``box``, after its
    setDetailedText: Qt writes "Show Details..." back on every press."""
    for button in box.buttons():
        if box.buttonRole(button) == QMessageBox.ActionRole:
            button.setText("Show details…")
            button.clicked.connect(
                lambda: button.setText(
                    "Hide details…" if button.text().startswith("Hide") else "Show details…"
                )
            )


def show_error(parent, title, message, detail=None):
    """A modal error dialog: ``title`` (what failed) over ``message`` for
    the user, ``detail`` (an exception or text) for a bug report. The title
    is in the box as well as on its title bar, which some desktops do not
    show."""
    box = QMessageBox(QMessageBox.Critical, title, title, QMessageBox.Ok, parent)
    box.setInformativeText(message)
    if detail:
        box.setDetailedText(str(detail))
        word_details_button(box)
    box.setDefaultButton(QMessageBox.Ok)  # (not Show Details, which comes first)
    box.exec_()

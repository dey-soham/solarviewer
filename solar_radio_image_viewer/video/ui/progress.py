"""
A dialog for one long job that can be cancelled.
"""

from collections import namedtuple

from PyQt5.QtCore import QThread, QTimer, Qt, pyqtSignal
from PyQt5.QtWidgets import QDialog, QDialogButtonBox, QProgressBar, QVBoxLayout

from ...ui.activity import ActivityLabel


class WorkDialog(QDialog):
    """What a job is doing, how far it got, and Cancel.

    QProgressDialog hides as soon as Cancel is pressed, while the job may
    take seconds to stop. This one stays, saying so, until ``finish()``: the
    job's owner calls that when the job has really ended. Cancel, Escape and
    closing the dialog all ask the job to stop (``cancel_requested``).
    """

    cancel_requested = pyqtSignal()

    def __init__(self, title, text, parent=None, cancelling_text="Cancelling…"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModal)
        self.setMinimumWidth(420)
        self.cancelling = False
        self._cancelling_text = cancelling_text
        self._finished = False
        layout = QVBoxLayout(self)
        self.status = ActivityLabel(text, self)
        self.status.label.setWordWrap(True)
        layout.addWidget(self.status)
        self.bar = QProgressBar(self)
        self.bar.setRange(0, 0)
        layout.addWidget(self.bar)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def set_text(self, text):
        if not self.cancelling:
            self.status.setText(text)

    def set_progress(self, done=0, total=0):
        """``done`` of ``total``; with no ``total``, a bar that says only
        that the job is going on."""
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(min(done, total))
        else:
            self.bar.setRange(0, 0)

    def reject(self):
        if self._finished:
            super().reject()
        elif not self.cancelling:
            self.cancelling = True
            self.status.start(self._cancelling_text)
            self.bar.hide()
            self.buttons.setEnabled(False)
            self.cancel_requested.emit()

    def closeEvent(self, event):
        if self._finished:
            super().closeEvent(event)
        else:
            event.ignore()
            self.reject()

    def finish(self):
        """The job has ended: close."""
        self._finished = True
        self.status.stop()
        self.accept()


_jobs = set()  # those running: a QThread must outlive its run, whatever became of its window


class Job(QThread):
    """Runs ``work(progress)`` in the background, ``progress(text, done=0,
    total=0)`` saying how it goes."""

    done = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str, int, int)

    def __init__(self, work):
        super().__init__()
        self.work = work
        self.finished.connect(self._ended)

    def start(self):
        _jobs.add(self)
        super().start()

    def _ended(self):
        _jobs.discard(self)

    def _report(self, text, done=0, total=0):
        self.progress.emit(text, done, total)

    def run(self):
        try:
            self.done.emit(self.work(self._report))
        except Exception as e:
            self.failed.emit(str(e))


Outcome = namedtuple("Outcome", "result error cancelled")


def run_with_dialog(parent, title, text, work, cancelling_text="Cancelling…"):
    """Run ``work(progress, cancelled)`` on a thread behind a modal WorkDialog
    and wait for it to end, the window drawing; an Outcome. ``progress`` as
    for Job (a text of "" leaves the text as it is); ``work`` looks at
    ``cancelled()`` as often as it can, as the dialog stays until it returns
    or raises. Nothing in ``work`` may touch a widget or QPixmap."""
    dialog = WorkDialog(title, text, parent, cancelling_text)
    state = {"result": None, "error": None, "cancelled": False}

    def show(new_text, done, total):
        if new_text:
            dialog.set_text(new_text)
        dialog.set_progress(done, total)

    job = Job(lambda progress: work(progress, lambda: state["cancelled"]))
    job.progress.connect(show)
    job.done.connect(lambda result: state.update(result=result))
    job.failed.connect(lambda error: state.update(error=error))
    job.finished.connect(dialog.finish)
    dialog.cancel_requested.connect(lambda: state.update(cancelled=True))
    QTimer.singleShot(0, job.start)  # (finish must come after exec_)
    dialog.exec_()
    dialog.deleteLater()
    return Outcome(state["result"], state["error"], state["cancelled"])

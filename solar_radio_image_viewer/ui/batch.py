"""
Running a batch of files without freezing the window.

A dialog hands :class:`BatchRunner` a worker function (defined at module
level, so it can be sent to another process) and one task per file. The
tasks run in worker processes: the window stays responsive, and Abort stops
them at once, even in the middle of a file. Results come back through Qt
signals, one per task, in task order.

:class:`BatchDialogMixin` does the rest for the batch dialogs: progress bar,
the Cancel button turning into Abort, and closing the window aborting.
"""

import multiprocessing

from PyQt5.QtCore import QCoreApplication, QThread, pyqtSignal

from ..processing.workers import process_pool


class BatchRunner(QThread):
    """Runs ``worker(task)`` for every task, in up to ``processes`` worker
    processes.

    ``result(index, value)`` is emitted as each task finishes, in task order,
    with what the worker returned; ``error(index, message)`` instead if the
    worker raised. QThread's ``finished`` follows the last one, and
    ``aborted`` then says whether abort() stopped the batch early.
    """

    result = pyqtSignal(int, object)
    error = pyqtSignal(int, str)

    def __init__(self, worker, tasks, processes=1, preload=(), parent=None):
        super().__init__(parent)
        self.worker = worker
        self.tasks = list(tasks)
        self.processes = max(1, min(int(processes), len(self.tasks) or 1))
        self.preload = preload
        self.aborted = False
        # Quitting the app stops the batch (a running QThread must not be destroyed)
        QCoreApplication.instance().aboutToQuit.connect(self.stop)

    def abort(self):
        """Stop the batch: tasks still running are killed, and no further
        results are emitted."""
        self.aborted = True

    def stop(self):
        """Abort and wait until the workers are gone."""
        self.abort()
        self.wait()

    def run(self):
        if not self.tasks:
            return
        pool = process_pool(self.processes, self.preload)
        try:
            results = pool.imap(self.worker, self.tasks)
            for index in range(len(self.tasks)):
                while not self.aborted:
                    try:
                        value = results.next(timeout=0.1)
                    except multiprocessing.TimeoutError:
                        continue  # still working: check for abort again
                    except Exception as e:
                        self.error.emit(index, f"{type(e).__name__}: {e}")
                    else:
                        self.result.emit(index, value)
                    break
                if self.aborted:
                    break
        finally:
            if self.aborted:
                pool.terminate()
            else:
                pool.close()
            pool.join()


class BatchDialogMixin:
    """Runs a batch for a QDialog that has ``ok_button``, ``cancel_button``,
    ``progress_bar`` and ``status_text`` (a QPlainTextEdit).

    While a batch runs, Cancel reads "Abort" and stops it; closing the window
    stops it too, and closes the window once the workers are gone. Connect
    the Cancel button to ``cancel_or_abort``. The dialog implements
    ``batch_result(task, value)`` and ``batch_finished(aborted)``, and may
    override ``batch_error(task, message)``.
    """

    _batch = None
    _close_after_batch = False

    def start_batch(self, worker, tasks, processes=1, disable=()):
        """Run ``worker`` on ``tasks``; ``disable`` lists the widgets to keep
        disabled while it runs."""
        self._batch_disabled = [w for w in disable if w.isEnabled()]
        for widget in self._batch_disabled:
            widget.setEnabled(False)
        self.ok_button.setEnabled(False)
        self.cancel_button.setText("Abort")
        self.cancel_button.setEnabled(True)
        self.progress_bar.setRange(0, len(tasks))
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self._close_after_batch = False

        runner = BatchRunner(worker, tasks, processes, parent=self)
        runner.result.connect(self._on_batch_result)
        runner.error.connect(self._on_batch_error)
        runner.finished.connect(self._on_batch_finished)
        self._batch = runner
        runner.start()

    def batch_running(self):
        return self._batch is not None

    def abort_batch(self):
        if self._batch is not None and not self._batch.aborted:
            self._batch.abort()
            self.status_text.appendPlainText("Stopping...")
            self.cancel_button.setEnabled(False)

    def cancel_or_abort(self):
        """The Cancel button: Abort while a batch runs, else Close."""
        if self._batch is not None:
            self.abort_batch()
        else:
            self.reject()

    def reject(self):
        # Esc and the window's close button: stop the batch first
        if self._batch is not None:
            self._close_after_batch = True
            self.abort_batch()
            return
        super().reject()

    def batch_error(self, task, message):
        self.status_text.appendPlainText(f"Error: {message}")

    def _log_to_end(self):
        bar = self.status_text.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_batch_result(self, index, value):
        self.progress_bar.setValue(index + 1)
        self.batch_result(self._batch.tasks[index], value)
        self._log_to_end()

    def _on_batch_error(self, index, message):
        self.progress_bar.setValue(index + 1)
        self.batch_error(self._batch.tasks[index], message)
        self._log_to_end()

    def _on_batch_finished(self):
        runner, self._batch = self._batch, None
        runner.wait()  # "finished" comes just before the thread ends
        for widget in self._batch_disabled:
            widget.setEnabled(True)
        self.ok_button.setEnabled(True)
        self.cancel_button.setText("Close")
        self.cancel_button.setEnabled(True)
        if runner.aborted:
            self.status_text.appendPlainText("Batch processing aborted by user.")
        if self._close_after_batch:
            super().reject()
        else:
            self.batch_finished(runner.aborted)
            self._log_to_end()
        runner.deleteLater()

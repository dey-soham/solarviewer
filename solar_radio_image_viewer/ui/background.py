"""
Reading and converting images in a worker process, the window still drawing,
with a Cancel button.

Reads and conversions can take seconds (a large cube, a compressed file, a
reprojection), and CASA tools are not thread-safe, so they run in a separate
process (as the prefetcher's reads do, see processing/prefetch.py). Meanwhile
the calling code waits in a local event loop: the window keeps repainting,
and input to it is held back as while the work blocked it. If the work takes
more than a moment, a dialog says what is happening, and its Cancel stops the
worker at once.

Jobs are module-level functions without Qt (from ``processing`` or
``coordinates``), with picklable arguments and results. Without a running
application (scripts), or off the GUI thread, they run in the calling thread.
"""

import logging
import threading
import time
from concurrent.futures import wait
from concurrent.futures.process import BrokenProcessPool

from PyQt5.QtCore import QEventLoop, Qt, QTimer
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QProgressBar,
    QVBoxLayout,
)

from ..processing.workers import import_modules, process_pool_executor

logger = logging.getLogger(__name__)

# How long work runs before its dialog shows: shorter work shows only the
# wait cursor
SHOW_AFTER_MS = 1000
# What a new worker imports straight away, so that the first job does not
# wait for it
WARM_MODULES = (
    "astropy.io.fits",
    "solar_radio_image_viewer.processing.image_data",
    "solar_radio_image_viewer.processing.maps",
)

_executor = None
_worker_ran = False  # whether a worker has finished a job (it can start here)
_in_process = False  # worker processes cannot start here: run jobs inline


class Cancelled(Exception):
    """The user cancelled the work."""


def run_in_background(parent, label, fn, *args, **kwargs):
    """Run ``fn(*args, **kwargs)`` in the worker process and return its
    result, or raise its exception, the window drawing meanwhile.

    ``label`` (e.g. "Loading sun.fits…") is shown if the work takes a while;
    Cancel stops the worker and raises :class:`Cancelled`.
    """
    global _in_process
    if _in_process or not _gui_thread():
        return fn(*args, **kwargs)
    try:
        future = _submit(fn, *args, **kwargs)
    except Exception as e:
        logger.warning(
            f"Could not start a worker process ({e}); "
            "working in the window's process instead"
        )
        _in_process = True
        return fn(*args, **kwargs)
    try:
        return wait_for(parent, label, future, on_cancel=_restart_worker)
    except BrokenProcessPool:
        _discard_worker()
        if not _worker_ran:
            logger.warning(
                "Worker processes stop at once here; "
                "working in the window's process instead"
            )
            _in_process = True
            return fn(*args, **kwargs)
        raise RuntimeError(
            "The background process stopped unexpectedly while working on this "
            "(it may have run out of memory)."
        ) from None


def wait_for(parent, label, future, on_cancel=None):
    """Wait for ``future`` (a ``concurrent.futures.Future``) and return its
    result, keeping the window drawing.

    If it takes longer than SHOW_AFTER_MS, a dialog shows ``label`` with a
    Cancel button, which calls ``on_cancel()`` (if given) and raises
    :class:`Cancelled`.
    """
    if not future.done() and _gui_thread():
        # At first the window repaints, and user input waits until the work
        # is done, as it did while work blocked the window. (Short passes:
        # an event loop that leaves input waiting spins while input is queued.)
        app = QApplication.instance()
        deadline = time.monotonic() + SHOW_AFTER_MS / 1000
        while not future.done() and time.monotonic() < deadline:
            app.processEvents(QEventLoop.ExcludeUserInputEvents)
            wait([future], timeout=0.01)
        if not future.done():
            _wait_with_dialog(parent, label, future, on_cancel)
    return future.result()


def warm_up():
    """Start the worker and have it import what jobs need."""
    if _in_process:
        return
    try:
        _submit(import_modules, WARM_MODULES)
    except Exception as e:
        logger.warning(f"Could not start a worker process: {e}")


def shutdown():
    """Stop the worker (the application is closing)."""
    _discard_worker()


class _WaitDialog(QDialog):
    """What is being worked on, a busy bar and Cancel."""

    def __init__(self, parent, label):
        super().__init__(parent.window() if parent is not None else None)
        self.setWindowTitle("Please Wait")
        self.setWindowModality(Qt.ApplicationModal)
        self.setMinimumWidth(340)
        layout = QVBoxLayout(self)
        text = QLabel(label)
        text.setWordWrap(True)
        layout.addWidget(text)
        bar = QProgressBar()
        bar.setRange(0, 0)  # busy: how far along it is is not known
        bar.setTextVisible(False)
        bar.setAccessibleName("Working")
        layout.addWidget(bar)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def _wait_with_dialog(parent, label, future, on_cancel):
    """Wait in an event loop, the (modal) dialog holding off other input."""
    loop = QEventLoop()
    poll = QTimer()
    poll.timeout.connect(lambda: future.done() and loop.quit())
    poll.start(10)
    dialog = _WaitDialog(parent, label)
    cancelled = []
    # Cancel, Escape and closing the dialog all reject it
    dialog.rejected.connect(lambda: (cancelled.append(True), loop.quit()))
    dialog.show()
    try:
        while not future.done() and not cancelled:
            loop.exec_()
    finally:
        poll.stop()
        dialog.hide()
        dialog.deleteLater()
    if not future.done():
        if on_cancel is not None:
            on_cancel()
        raise Cancelled()


def _gui_thread():
    """Whether this is the GUI thread of a running application."""
    return (
        QApplication.instance() is not None
        and threading.current_thread() is threading.main_thread()
    )


def _submit(fn, *args, **kwargs):
    """``fn`` sent to the worker (started if need be), as a Future."""
    global _executor
    for attempt in (1, 2):
        if _executor is None:
            _executor = process_pool_executor(1)
        try:
            future = _executor.submit(fn, *args, **kwargs)
        except (BrokenProcessPool, RuntimeError):
            # A worker that stopped (or a pool shut down): start afresh once
            _discard_worker()
            if attempt == 2:
                raise
            continue
        future.add_done_callback(_note_finished)
        return future


def _note_finished(future):
    global _worker_ran
    if not future.cancelled() and not isinstance(future.exception(), BrokenProcessPool):
        _worker_ran = True


def _restart_worker():
    """Stop the worker at once (Cancel), and start a fresh one for later jobs."""
    _discard_worker()
    warm_up()


def _discard_worker():
    """Kill the worker, if any, and forget it."""
    global _executor
    executor, _executor = _executor, None
    if executor is None:
        return
    kill_workers = getattr(executor, "kill_workers", None)  # Python 3.14+
    if kill_workers is not None:
        kill_workers()
        return
    # Before 3.14 there is no public way; shutdown() alone would let the
    # running job finish
    for process in list((executor._processes or {}).values()):
        process.kill()
    executor.shutdown(wait=False, cancel_futures=True)

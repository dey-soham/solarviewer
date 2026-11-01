"""
Running a CASA task (exportfits, importfits, imsubimage, ...) or a reprojection
in a separate Python process.

CASA tasks crash or clash with PyQt5 when run in the GUI's own process, so
each runs in a fresh interpreter. Its arguments travel as JSON on stdin and
are never pasted into Python source, so any file name is safe. The process
runs in a temporary folder of its own, where CASA's ``<task>.last`` files and
its other temporary files go: they are removed with it, even if it is stopped
(not if what started it is killed: nothing is left to remove them).

On Linux the process dies with the thread that started it, so that killing a
worker (Stop in a batch, Cancel of ``ui.background``) ends the task it ran: that
thread only waits for the process, so it outlives it unless it is killed.

A reprojection needs sunpy, which needs sqlite3, which cannot be imported once
casatools is (its own sqlite hides the system's): all that imports casatools at
its top goes through ``processing.dependencies``, which imports sqlite3 first.

This waits for the process, so the window runs it through
``ui.background.run_in_thread``: :class:`CasaTask` is what its Cancel can stop.

Also here, the export jobs that use CASA tools, which are not thread-safe:
they go to the worker process of ``ui.background``, so no Qt in this file.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading

HPC_MODULE = "solar_radio_image_viewer.coordinates.helioprojective"

# Set for a task's process, to the pid of what started it. A task it starts
# stays in its process group, for stopping the outer task to end both.
_IN_TASK = "SOLARVIEWER_IN_CASA_TASK"

_SCRIPT = """
import ctypes, importlib, json, os, signal, sys, tempfile

if sys.platform == "linux":
    PR_SET_PDEATHSIG = 1
    try:
        ctypes.CDLL(None).prctl(PR_SET_PDEATHSIG, ctypes.c_ulong(signal.SIGKILL))
    except Exception:
        pass  # it only runs on, as elsewhere
    if os.getppid() != int(os.environ["SOLARVIEWER_IN_CASA_TASK"]):
        sys.exit(1)  # what started it died before that took hold

import sqlite3  # before casatools, or it cannot be imported at all
import solar_radio_image_viewer  # sends CASA's log to the null device

request = json.load(sys.stdin)
module = importlib.import_module(request["module"])
tempfile.tempdir = os.getcwd()  # (after the imports: caches stay where they are)
try:
    result = getattr(module, request["task"])(**request["kwargs"])
except Exception as e:
    print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))
    sys.exit(1)
try:
    print(json.dumps({"ok": True, "result": result}))
except (TypeError, ValueError):
    print(json.dumps({"ok": True, "result": None}))
"""


class CasaTaskError(RuntimeError):
    """A CASA task failed, or its process did."""


class CasaTask:
    """One run of a CASA task (or a function of ``module``), which another
    thread can stop."""

    def __init__(self, task, timeout, kwargs, module="casatasks"):
        self.task = task
        self.timeout = timeout
        self.kwargs = kwargs
        self.module = module
        self.stopped = False
        self._process = None
        self._lock = threading.Lock()

    def run(self):
        """Run the task and return its result (see :func:`run_casa_task`)."""
        folder = tempfile.mkdtemp(prefix="solarviewer_task_")
        try:
            return self._run_in(folder)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def _run_in(self, folder):
        with self._lock:
            if self.stopped:
                raise CasaTaskError(f"{self.task} was stopped")
            process = self._process = subprocess.Popen(
                [sys.executable, "-c", _SCRIPT],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=folder,
                env=dict(os.environ, MPLBACKEND="Agg", **{_IN_TASK: str(os.getpid())}),
                start_new_session=_IN_TASK not in os.environ,
            )
        request = json.dumps(
            {"module": self.module, "task": self.task, "kwargs": self.kwargs}
        )
        try:
            stdout, stderr = process.communicate(request, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            self._kill(process)
            process.communicate()
            raise
        # The reply is the last JSON line: CASA may print lines of its own first
        reply = None
        for line in reversed(stdout.splitlines()):
            try:
                reply = json.loads(line)
                break
            except ValueError:
                continue
        if not isinstance(reply, dict) or "ok" not in reply:
            detail = (stderr or stdout).strip()[-500:]
            raise CasaTaskError(
                f"{self.task} failed: {detail or f'exit code {process.returncode}'}"
            )
        if not reply["ok"]:
            raise CasaTaskError(f"{self.task} failed: {reply['error']}")
        return reply["result"]

    def stop(self):
        """End the task's process and those it started, if it runs. What it
        had written stays."""
        with self._lock:
            self.stopped = True
            process = self._process
        if process is not None and process.poll() is None:
            self._kill(process)
            process.wait()

    @staticmethod
    def _kill(process):
        if _IN_TASK in os.environ:
            process.kill()
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass  # it has just ended


def run_casa_task(task, timeout=None, **kwargs):
    """Run ``casatasks.<task>(**kwargs)`` in a separate Python process.

    Paths in ``kwargs`` should be absolute (the task runs in the temporary
    folder). Returns the task's result if it is JSON-serializable, else None.
    Raises CasaTaskError if the task fails.
    """
    return CasaTask(task, timeout, kwargs).run()


def write_sub_image(imagename, outfile, region):
    """Write the part of ``imagename`` in ``region`` to the CASA image ``outfile``."""
    from .dependencies import IA
    from .fits_io import readable_image_path

    ia_tool = IA()
    ia_tool.open(readable_image_path(imagename))
    try:
        ia_tool.subimage(outfile=outfile, region=region).done()
    finally:
        ia_tool.close()


def write_tb_map_from(imagename, outfile, flux_data):
    """Write the brightness temperature map of ``flux_data`` to ``outfile``
    (see generate_tb_map). Returns None, or the error message: the map is not
    sent back."""
    from .maps import generate_tb_map

    tb_data, result = generate_tb_map(imagename, outfile=outfile, flux_data=flux_data)
    return result if tb_data is None else None


def read_contour_stokes(imagename, stokes, threshold, rms_box, target_size):
    """Contour data for a Stokes product: (pixels, coordsys record), as a
    worker can send them back."""
    from .image_data import get_pixel_values_from_image

    pix, csys, _ = get_pixel_values_from_image(
        imagename, stokes, threshold, rms_box, target_size=target_size
    )
    return pix, csys.torecord()

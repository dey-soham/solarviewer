"""
Working out a video's frames: which images, in what order, at what times.

That lists folders and reads a header per image (over SFTP for a server, with
casatools for a CASA image), and it is asked for after every change of a
setting. So it runs off the GUI thread, and what was read is kept: a new
colormap does not read a thing again, and neither does the frame slider, which
does not get here at all.

CASA images are read in a worker process, as casatools is not thread-safe.
"""

import glob
import logging
import os
import posixpath
import threading
import time
from collections import deque, namedtuple
from concurrent.futures import FIRST_COMPLETED, Future, InvalidStateError, wait
from concurrent.futures.process import BrokenProcessPool
from datetime import datetime

from PyQt5.QtCore import QObject, pyqtSignal

from ...processing.fits_io import is_casa_image
from ...processing.workers import process_pool_executor
from ...ui.background import Cancelled
from ..creator import (
    apply_difference,
    file_times,
    frame_plan,
    observation_time,
    time_sort_key,
)
from .helpers import is_image

logger = logging.getLogger(__name__)

# Progress of a plan that takes less than this is not worth showing
QUIET_S = 0.15

# What the settings say about the frames, read on the GUI thread
PlanSpec = namedtuple(
    "PlanSpec",
    "single single_path folder pattern sort start end every layers excluded "
    "difference remote",
)


class PlanResult:
    """What came of a plan.

    ``files`` and ``problem`` (title, message) as _video_files returns them,
    ``updates`` for the video's options, ``plan`` as creator.frame_plan gives
    it; ``input_files`` and ``input_problem`` are what the Input tab's folder
    and pattern gave on their own, and ``casa`` how many of those are CASA
    images."""

    def __init__(self, problem=None, input_problem=None):
        self.problem = problem
        self.input_problem = input_problem
        self.files = []
        self.updates = {}
        self.plan = None
        self.input_files = []
        self.casa = 0


class _Superseded(Exception):
    """Nobody is waiting for this plan any more."""


class _Job:
    def __init__(self, spec, request_id=None, future=None):
        self.spec = spec
        self.request_id = request_id
        self.future = future
        self.begun = time.monotonic()


def _signature(path):
    try:
        info = os.stat(path)
    except OSError:
        return None
    return info.st_mtime_ns, info.st_size


def _server_sort_key(path, when):
    return (0, when.timestamp(), path) if when else (1, 0, path)


class FramePlanner(QObject):
    """Plans frames on a thread of its own: the preview's newest request, and
    those someone is waiting for (:meth:`plan_now`)."""

    planned = pyqtSignal(int, object)  # request id, PlanResult
    status = pyqtSignal(int, str, int, int)  # request id, text, done, total

    def __init__(self):
        super().__init__()
        self._condition = threading.Condition()
        self._wanted = None  # the preview's newest request
        self._urgent = deque()
        self._working = None  # the job being planned
        self._stopped = False
        self._forget_all = False
        self._last = None  # (key, PlanResult) of the plan last made
        self._times = {}  # local path -> (signature, time)
        self._server_times = {}  # server path -> time (None: unknown, also a failed read)
        self._lists = {}  # (folder, pattern) on the server -> (paths, error)
        self._pool = None  # for CASA images, started when there are any
        self._thread = threading.Thread(target=self._run, name="video-frame-plan", daemon=True)
        self._thread.start()

    def request(self, request_id, spec):
        """Plan for the preview (``planned`` gives the result); replaces a
        request not finished yet."""
        with self._condition:
            self._wanted = _Job(spec, request_id)
            self._condition.notify()

    def plan_now(self, spec):
        """Plan for someone who waits for it: a Future of the PlanResult, which
        cancel() abandons. It raises Cancelled if the planner stops first."""
        future = Future()
        with self._condition:
            if self._stopped:
                future.set_exception(Cancelled())
            else:
                self._urgent.append(_Job(spec, future=future))
                self._condition.notify()
        return future

    def forget(self):
        """Read everything again next time (e.g. new files, Scan)."""
        with self._condition:
            self._forget_all = True

    def stop(self):
        with self._condition:
            self._stopped = True
            jobs = list(self._urgent) + [self._working]
            self._urgent.clear()
            self._condition.notify()
        # Now, not when the thread gets to it: it may be in a slow read
        for job in jobs:
            if job is not None and job.future is not None:
                self._settle(job.future, exception=Cancelled())
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)

    def _run(self):
        while True:
            with self._condition:
                while not (self._stopped or self._wanted or self._urgent):
                    self._condition.wait()
                if self._stopped:
                    return
                if self._urgent:
                    job = self._urgent.popleft()
                else:
                    job, self._wanted = self._wanted, None
                self._working = job
                if self._forget_all:
                    self._forget_all = False
                    self._last = None
                    self._times.clear()
                    self._server_times.clear()
                    self._lists.clear()
            try:
                result = self._plan(job)
            except _Superseded:
                continue
            except Exception as e:
                logger.error(f"Could not work out the frames: {e}")
                if job.future is not None:
                    self._settle(job.future, exception=e)
                    continue
                result = PlanResult(problem=("Could Not Read the Frames", str(e)))
            if job.future is not None:
                self._settle(job.future, result=result)
            elif not self._stopped:
                self.planned.emit(job.request_id, result)

    @staticmethod
    def _settle(future, result=None, exception=None):
        try:
            if exception is not None:
                future.set_exception(exception)
            else:
                future.set_result(result)
        except InvalidStateError:
            pass  # cancelled meanwhile

    def _superseded(self, job):
        if self._stopped:
            return True
        return job.future.cancelled() if job.future is not None else self._wanted is not None

    def _check(self, job):
        if self._superseded(job):
            raise _Superseded()

    def _say(self, job, text, done=0, total=0):
        if job.request_id is not None and time.monotonic() - job.begun >= QUIET_S:
            self.status.emit(job.request_id, text, done, total)

    def _plan(self, job):
        spec = job.spec
        # A file added or removed shows in its folder's time, so one stat a
        # folder tells that the plan still holds. (Scan reads all again, for
        # images changed in place.)
        key = repr(
            (
                spec._replace(excluded=sorted(spec.excluded), remote=id(spec.remote)),
                self._folder_times(spec),
            )
        )
        if self._last is not None and self._last[0] == key:
            return self._last[1]
        result = self._work_out(job)
        self._check(job)
        self._last = (key, result)
        return result

    def _folder_times(self, spec):
        if spec.remote is not None:
            return None
        paths = [spec.single_path] if spec.single else [spec.folder]
        paths += [layer.get("series_dir", "") for layer in spec.layers if layer.get("source") == "series"]
        return [_signature(path) for path in paths]

    def _work_out(self, job):
        spec = job.spec
        result = PlanResult()
        if spec.single:
            path = spec.single_path
            on_disk = spec.remote.is_image if spec.remote is not None else is_image
            if spec.remote is not None:
                self._say(job, f"Checking the image on {spec.remote.label}…")
            if not path or not on_disk(path):
                result.problem = ("No Image", "Choose the image shown in every frame.")
                return result
            image_files, is_series = [path], False
        else:
            image_files, problem = self._input_files(job)
            if problem:
                result.problem = result.input_problem = problem
                return result
            is_series = True
            result.input_files = image_files
            if spec.remote is None:
                result.casa = sum(is_casa_image(f) for f in image_files)

        bounds = []
        for text, name in ((spec.start, "From"), (spec.end, "To")):
            try:
                bounds.append(datetime.fromisoformat(text.replace("T", " ")) if text else None)
            except ValueError:
                result.problem = ("Invalid Time", f"{name}: use YYYY-MM-DD HH:MM:SS")
                return result

        scanned = []
        for layer in spec.layers:
            layer = dict(layer)
            if layer.get("source") == "series":
                folder = layer.get("series_dir", "")
                pattern = layer.get("series_pattern") or "*.fits"
                if spec.remote is not None:
                    found, error = self._server_list(job, folder, pattern)
                    if error:
                        result.problem = ("Could Not Read the Folder", error)
                        return result
                    layer["series_files"] = self._sorted_by_time(job, found)
                else:
                    path = os.path.join(folder, pattern)
                    layer["series_files"] = self._sorted_by_time(job, glob.glob(path))
                    if layer.get("enabled", True) and not layer["series_files"]:
                        result.problem = ("No Contour Files", f"No files match {path}")
                        return result
            scanned.append(layer)

        plan = frame_plan(
            image_files,
            is_series,
            scanned,
            bounds[0],
            bounds[1],
            spec.every,
            times_of=lambda paths: self._times_of(job, paths),
        )
        if not plan["image_files"]:
            result.problem = ("No Frames", "No frames in the chosen time range.")
            return result
        result.plan = plan

        keep = [i for i, f in enumerate(plan["driver_files"]) if f not in spec.excluded]
        if not keep:
            result.problem = ("No Frames", "All frames are unticked in the Frames tab.")
            return result
        layers = []
        for layer in plan["layers"]:
            layer = dict(layer)
            if "frame_files" in layer:
                layer["frame_files"] = [layer["frame_files"][i] for i in keep]
            layer.pop("series_files", None)  # not needed by the frames
            layers.append(layer)
        files, options = apply_difference(
            [plan["image_files"][i] for i in keep],
            {"difference": spec.difference, "contour_layers": layers},
        )
        if not files:
            result.problem = ("No Frames", "A running difference needs at least two frames.")
            return result
        result.files = files
        result.updates = options
        return result

    def _input_files(self, job):
        """(files, problem): the Input tab's files in the chosen order, or
        ([], a message saying why there are none)."""
        spec = job.spec
        if spec.remote is not None:
            return self._server_input_files(job)
        if not spec.folder or not os.path.isdir(spec.folder):
            return [], (
                "Invalid Directory",
                "The specified input directory does not exist.",
            )
        if not spec.pattern.strip():
            return [], ("No Pattern", "Enter a file pattern, e.g. *.fits or *.image")
        input_path = os.path.join(spec.folder, spec.pattern)
        files = glob.glob(input_path)
        if not files:
            return [], ("No Files Found", f"No files match the pattern: {input_path}")
        invalid = [os.path.basename(f) for f in files if not is_image(f)]
        if invalid:
            msg = (
                f"Found {len(invalid)} files that are not images.\n"
                "FITS files and CASA images are supported."
            )
            if len(invalid) > 5:
                msg += "\n\nExamples:\n" + "\n".join(invalid[:5]) + "\n..."
            else:
                msg += "\n\nFiles:\n" + "\n".join(invalid)
            return [], ("Invalid File Extensions", msg)

        if spec.sort == "filename":
            files.sort()
        elif spec.sort == "date/time":
            # Observation time (header or file name), not file dates
            files = self._sorted_by_time(job, files)
        elif spec.sort == "extension":
            files.sort(key=lambda x: os.path.splitext(x)[1])
        return files, None

    def _server_input_files(self, job):
        """_input_files for images on a server."""
        spec = job.spec
        folder, pattern = spec.folder, spec.pattern.strip()
        if not folder:
            return [], ("No Folder", "Choose the folder of the images on the server.")
        if not pattern:
            return [], ("No Pattern", "Enter a file pattern, e.g. *.fits or *.image")
        files, error = self._server_list(job, folder, pattern)
        if error:
            return [], ("Could Not Read the Folder", error)
        if not files:
            return [], ("No Files Found", f"No images match {pattern} in {folder} on the server")
        if spec.sort == "date/time":
            files = self._sorted_by_time(job, files)
        elif spec.sort == "extension":
            files = sorted(files, key=lambda x: posixpath.splitext(x)[1])
        else:
            files = sorted(files)
        return files, None

    def _server_list(self, job, folder, pattern):
        """(the images matching ``pattern`` in server ``folder``, an error or
        None). A failure is kept too, until asked to read again."""
        key = (folder, pattern)
        if key not in self._lists:
            remote = job.spec.remote
            self._say(job, f"Reading {folder} on {remote.label}…")
            try:
                paths = [p for p in remote.glob(folder, pattern) if remote.is_image(p)]
                self._lists[key] = (paths, None)
            except Exception as e:
                self._lists[key] = ([], f"Could not read {folder}: {e}")
        self._check(job)
        return self._lists[key]

    def _sorted_by_time(self, job, paths):
        times = self._times_of(job, paths)
        by_time = _server_sort_key if job.spec.remote is not None else time_sort_key
        return [p for _, p in sorted(zip((by_time(p, t) for p, t in zip(paths, times)), paths))]

    def _times_of(self, job, paths):
        """Observation time (datetime or None) of each of ``paths``."""
        if job.spec.remote is not None:
            return self._times_on_server(job, paths)
        return self._local_times(job, paths)

    def _times_on_server(self, job, paths):
        remote = job.spec.remote
        todo = [p for p in paths if p not in self._server_times]
        if todo:
            headers = remote.headers(
                todo,
                progress=lambda done, total: self._say(
                    job, f"Reading headers: {done} of {total}", done, total
                ),
                cancelled=lambda: self._superseded(job),
            )
            self._check(job)
            for path in todo:
                self._server_times[path] = observation_time(headers.get(path), path)
        return [self._server_times[p] for p in paths]

    def _local_times(self, job, paths):
        fits, casa = [], []
        for path in paths:
            signature = _signature(path)
            known = self._times.get(path)
            if known is None or known[0] != signature:
                (casa if is_casa_image(path) else fits).append((path, signature))
        self._check(job)
        for count, (path, signature) in enumerate(fits, 1):
            self._check(job)
            self._times[path] = (signature, file_times([path])[0])
            self._say(job, f"Reading headers: {count} of {len(fits)}", count, len(fits))
        if casa:
            self._read_casa_times(job, casa)
        return [self._times[p][1] for p in paths]

    def _read_casa_times(self, job, items):
        # A worker that died (a crash in casatools, out of memory) takes the
        # pool with it, and what it had not read: those get a new one, once
        lost = self._read_in_worker(job, items, len(items))
        if lost:
            lost = self._read_in_worker(job, lost, len(items))
        for path, signature in lost:
            self._times[path] = (signature, observation_time(None, path))

    def _read_in_worker(self, job, items, total):
        """Read the times of ``items``, the last of ``total`` CASA images; the
        items a dead worker took with it."""
        self._check(job)
        if self._pool is None:
            self._pool = process_pool_executor(
                1, preload=["solar_radio_image_viewer.video.creator"]
            )
        futures, lost = {}, []
        for item in items:
            try:
                futures[self._pool.submit(file_times, [item[0]])] = item
            except BrokenProcessPool:
                lost.append(item)
        pending, count = set(futures), total - len(items)
        try:
            while pending:
                self._check(job)
                done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                for future in done:
                    path, signature = futures[future]
                    try:
                        when = future.result()[0]
                    except BrokenProcessPool:
                        lost.append(futures[future])
                        continue
                    except Exception as e:
                        logger.warning(f"{os.path.basename(path)}: {e}")
                        when = observation_time(None, path)
                    self._times[path] = (signature, when)
                    count += 1
                    self._say(job, f"Reading CASA images: {count} of {total}", count, total)
        finally:
            for future in pending:
                future.cancel()
            if lost:
                self._pool.shutdown(wait=False, cancel_futures=True)
                self._pool = None
        return lost

"""
Downloads the frames of a plan for the Helioviewer browser. Six threads live
as long as the loader and take frames from one queue; each frame comes from
the disk cache when it is there, else from Helioviewer in chunks, so that a
cancel returns within one chunk rather than at the end of the request. The
picture crosses to the GUI thread as a ``QImage``; the ``QPixmap`` is made
there, as Qt wants. The frames of a load are kept in memory up to
``MEMORY_BYTES``; past that, the ones furthest from the frame shown are
dropped and, when shown again, read from disk by the same threads (the GUI
thread decodes nothing): ``pixmap_of`` answers None and the frame comes back
through ``frame_ready`` a moment later. ``memory_note`` says so before a
load, never silently.

Cancel drains the queue and lets the frames under way stop at their next
chunk. Close waits up to a second for the threads, then parks the ones still
in a request (nothing can interrupt one), to end on their own.
"""

import json
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from PyQt5.QtCore import QObject, QThread, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap

from . import helioviewer_api as hv
from .helioviewer_cache import PNG_MAGIC, FrameCache
from .helioviewer_data import FAILED, LOADING, PLANNED, READY
from .helioviewer_lwa import MEMORY_BYTES  # one budget for the browser's frames, whatever they are

logger = logging.getLogger(__name__)

WORKERS = 6
CHUNK_BYTES = 64 * 1024  # of a download, between looks at the stop flag
BYTES_PER_PIXEL = 4  # a QPixmap of a PNG, as Qt holds it
CLOSE_WAIT_MS = 1000  # a closing window waits this long for the threads, no more
PREFETCH = 3  # frames each side of the one shown read back from disk ahead of a step

_parked: List[QThread] = []  # threads that outlived their loader, kept until they end


class Stopped(Exception):
    """The load was cancelled while this frame was being read."""


def _park(thread):
    """Keep ``thread`` until it ends by itself (a request cannot be interrupted)."""
    _parked[:] = [t for t in _parked if t.isRunning()]
    if thread.isRunning():
        _parked.append(thread)


def parked_threads() -> List[QThread]:
    _parked[:] = [t for t in _parked if t.isRunning()]
    return list(_parked)


# ---------------------------------------------------------------- reading one frame


def read_body(response, stopped: Optional[threading.Event] = None) -> bytes:
    """The whole body of a streamed ``response``, in chunks of
    ``CHUNK_BYTES``; raises ``Stopped`` as soon as ``stopped`` is set."""
    iter_content = getattr(response, "iter_content", None)
    if iter_content is None:
        return response.content
    chunks = []
    for chunk in iter_content(CHUNK_BYTES):
        if stopped is not None and stopped.is_set():
            raise Stopped()
        if chunk:
            chunks.append(chunk)
    return b"".join(chunks)


def download(url: str, stopped: Optional[threading.Event] = None) -> bytes:
    """The bytes at ``url``, read in chunks on the shared session."""
    response = hv.get(url, stream=True)
    try:
        return read_body(response, stopped)
    finally:
        close = getattr(response, "close", None)
        if close is not None:
            try:
                close()
            except Exception:
                pass


def server_said(data: bytes) -> str:
    """Helioviewer's complaint when it sends JSON instead of a picture."""
    try:
        answer = json.loads(data[:2000].decode("utf-8", "replace"))
    except ValueError:
        return ""
    if isinstance(answer, dict):
        return str(answer.get("error") or answer.get("message") or "")
    return ""


def plain_error(e: Exception) -> str:
    """What went wrong, as a sentence for the window."""
    import requests

    if isinstance(e, Stopped):
        return "Cancelled"
    if isinstance(e, requests.Timeout):
        return "Helioviewer took too long to answer"
    if isinstance(e, requests.ConnectionError):
        return "Helioviewer could not be reached"
    if isinstance(e, requests.HTTPError):
        code = getattr(getattr(e, "response", None), "status_code", None)
        return f"Helioviewer answered HTTP {code}" if code else "Helioviewer refused the request"
    text = str(e).strip()
    if isinstance(e, (hv.HelioviewerError, OSError, ValueError)) and text:
        return text
    return f"The image could not be loaded: {text or type(e).__name__}"


def fetch(frame, cache: Optional[FrameCache], stopped: Optional[threading.Event] = None):
    """``(QImage, path)`` of ``frame``: from the cache when it is there, else
    downloaded and kept (``path`` is None when the disk takes no more)."""
    path = cache.find(frame) if cache is not None else None
    image = QImage()
    if path is not None:
        try:
            data = path.read_bytes()
        except OSError as e:
            logger.info("Could not read the cached frame %s: %s", path, e)
            data = b""
        if image.loadFromData(data):
            return image, path
        logger.warning("The cached frame %s could not be read: downloading it again", path)
        try:
            path.unlink()
        except OSError:
            pass
    data = download(frame.url, stopped)
    if not data.startswith(PNG_MAGIC):
        said = server_said(data)
        raise hv.HelioviewerError(said and f"Helioviewer: {said}" or "Helioviewer sent no image")
    if stopped is not None and stopped.is_set():
        raise Stopped()
    path = cache.put(frame, data) if cache is not None else None
    if not image.loadFromData(data):
        raise hv.HelioviewerError("The image could not be read")
    return image, path


# ---------------------------------------------------------------- the pool


@dataclass
class _Load:
    plan: object
    total: int
    stopped: threading.Event = field(default_factory=threading.Event)
    pending: int = 0
    done: int = 0
    failed: int = 0
    finished: bool = False


@dataclass
class _Task:
    frame: object
    load: _Load
    reload: bool = False  # a ready frame read from its file again, not part of a load's count


class _Worker(QThread):
    """One of the pool: takes frames from the queue until it takes None."""

    done = pyqtSignal(object, object, object, str)  # task, QImage or None, path or None, error ("" if stopped)

    def __init__(self, tasks, cache):
        super().__init__()
        self.tasks = tasks
        self.cache = cache

    def run(self):
        while True:
            task = self.tasks.get()
            if task is None:
                return
            if task.load.stopped.is_set():
                self.done.emit(task, None, None, "")
                continue
            try:
                image, path = fetch(task.frame, self.cache, task.load.stopped)
            except Stopped:
                self.done.emit(task, None, None, "")
            except Exception as e:
                if not isinstance(e, (hv.HelioviewerError, OSError, ValueError)):
                    logger.exception("Frame %s of %s failed", task.frame.index, task.frame.layers)
                self.done.emit(task, None, None, plain_error(e))
            else:
                self.done.emit(task, image, path, "")


def frame_bytes(shape) -> int:
    """What a frame of ``shape`` (anything with ``width`` and ``height``) takes in memory."""
    return max(1, int(shape.width)) * max(1, int(shape.height)) * BYTES_PER_PIXEL


def capacity(shape, budget: int = MEMORY_BYTES) -> int:
    """How many frames of ``shape`` the budget holds at once (one at least)."""
    return max(1, int(budget) // frame_bytes(shape))


def memory_note(plan, budget: int = MEMORY_BYTES) -> str:
    """What the summary says before a load that will not all fit in memory, or ""."""
    count = len(plan.downloads)
    held = capacity(plan.view, budget)
    if count == 0 or held >= count:
        return ""
    return (
        f"Holds {held} of {count} frames in memory ({budget / 1024 ** 3:.1f} GB); "
        "the rest are read from disk as they are shown."
    )


class FrameLoader(QObject):
    """The browser's downloads: ``start(plan)`` queues the plan's frames,
    nearest ``at`` first; each comes back through ``frame_ready`` with its
    pixmap set (its duplicates too), or ``frame_failed`` with its error;
    ``progress`` counts them and ``finished`` says when the load is over
    (True if cancelled). One loader serves every load of a window."""

    frame_ready = pyqtSignal(object)  # a Frame, state READY
    frame_failed = pyqtSignal(object)  # a Frame, state FAILED, error set
    progress = pyqtSignal(int, int, int)  # done, failed, of how many downloads
    finished = pyqtSignal(bool)  # cancelled

    def __init__(self, cache: Optional[FrameCache] = None, workers: int = WORKERS, memory_bytes: int = MEMORY_BYTES, parent=None, trim: bool = True):
        super().__init__(parent)
        import queue

        self.cache = cache if cache is not None else FrameCache()
        self.worker_count = max(1, int(workers))
        self.memory_bytes = int(memory_bytes)
        self._tasks = queue.Queue()
        self._workers: List[_Worker] = []
        self._load: Optional[_Load] = None
        self._reloads = _Load(plan=None, total=0)  # the re-reads from disk, stopped only when closed
        self._reloading: set = set()  # indices of frames a worker is reading again
        self._frames: List = []  # the current plan's frames, by index
        self._held: "OrderedDict[int, object]" = OrderedDict()  # index → Frame whose pixmap is in memory
        self._current = 0  # the frame shown: the last to be dropped
        self._closed = False
        # A window makes its loader as it opens: the cache is cut back to its
        # limit then, on a thread, so a full disk never waits on a load
        self.trimmer: Optional[threading.Thread] = self.cache.trim_in_background() if trim else None

    # ------------------------------------------------------------ loads

    @property
    def busy(self) -> bool:
        return self._load is not None and not self._load.finished

    def thread_count(self) -> int:
        return sum(1 for worker in self._workers if worker.isRunning())

    def start(self, plan, at: Optional[datetime] = None):
        """Load ``plan``'s frames, those nearest ``at`` first. A load under way is cancelled."""
        if self._closed:
            raise RuntimeError("The loader is closed")
        if self.busy:
            self.cancel()
        frames = plan.order(at)
        self._adopt(plan.frames)
        self._current = 0
        if at is not None and plan.frames:
            nearest = plan.nearest(at)
            self._current = nearest.index if nearest is not None else 0
        load = _Load(plan=plan, total=len(frames), pending=len(frames))
        self._load = load
        if not frames:
            load.finished = True
            self.progress.emit(0, 0, 0)
            self.finished.emit(False)
            return
        self._ensure_workers()
        for frame in frames:
            self._set_state(frame, LOADING)
            self._tasks.put(_Task(frame, load))
        self.progress.emit(0, 0, load.total)

    def _adopt(self, frames):
        """``frames`` are the plan from now on: pixmaps of frames no longer in
        it go, those still held (a retry's) stay counted."""
        keep = {id(frame) for frame in frames}
        for frame in self._frames:
            if id(frame) not in keep and frame.pixmap is not None:
                frame.pixmap = None
        self._frames = list(frames)
        self._held.clear()
        self._reloading.clear()
        for frame in self._frames:
            if frame.duplicate_of is None and frame.pixmap is not None:
                self._held[frame.index] = frame

    def release(self):
        """Let every frame's pixmap go (the window shows something else now)."""
        if self.busy:
            self.cancel()
        for frame in self._frames:
            frame.pixmap = None
        self._frames = []
        self._held.clear()
        self._reloading.clear()

    def cancel(self):
        """Stop the load: the queue is emptied, the frames under way stop at
        their next chunk, and what is ready stays ready."""
        load = self._load
        if load is None or load.finished:
            return
        load.stopped.set()
        load.finished = True
        for task in self._drain():
            if task.load is load:
                load.pending -= 1  # (never started: nothing will report it)
                self._set_state(task.frame, PLANNED)
            elif task.reload:
                self._tasks.put(task)  # a re-read is not the load's: it goes on
        self.finished.emit(True)

    def close(self, wait_ms: int = CLOSE_WAIT_MS):
        """Cancel, tell the threads to end, wait ``wait_ms`` in all for them,
        and park the ones still in a request."""
        self.cancel()
        self._closed = True
        self._reloads.stopped.set()
        self._drain()
        for frame in self._frames:
            if frame.state == LOADING:  # (under way: its result will be dropped)
                frame.state = PLANNED
        for _ in self._workers:
            self._tasks.put(None)
        deadline = time.monotonic() + max(0, wait_ms) / 1000
        for worker in self._workers:
            left = max(0, int((deadline - time.monotonic()) * 1000))
            if not worker.wait(left):
                _park(worker)
        self._workers = []
        self._held.clear()

    def _drain(self):
        import queue

        drained = []
        while True:
            try:
                drained.append(self._tasks.get_nowait())
            except queue.Empty:
                return [task for task in drained if task is not None]

    def _ensure_workers(self):
        self._workers = [worker for worker in self._workers if worker.isRunning()]
        while len(self._workers) < self.worker_count:
            worker = _Worker(self._tasks, self.cache)
            worker.done.connect(self._on_done)
            worker.start()
            self._workers.append(worker)

    # ------------------------------------------------------------ results, on the GUI thread

    def _duplicates(self, frame):
        return [other for other in self._frames if other.duplicate_of == frame.index]

    def _set_state(self, frame, state: str, error: str = ""):
        for each in [frame] + self._duplicates(frame):
            each.state = state
            each.error = error

    def _on_done(self, task, image, path, error):
        if task.reload:
            self._reloaded(task.frame, image, error)
            return
        load, frame = task.load, task.frame
        load.pending -= 1
        if load is not self._load or load.finished or self._closed:
            if frame.state == LOADING:
                self._set_state(frame, PLANNED)
            return
        if image is None and not error:
            self._set_state(frame, PLANNED)
        elif image is None:
            load.failed += 1
            self._set_state(frame, FAILED, error)
            for each in [frame] + self._duplicates(frame):
                self.frame_failed.emit(each)
        else:
            load.done += 1
            pixmap = QPixmap.fromImage(image)
            frame.path = str(path) if path is not None else None
            self._set_state(frame, READY)
            for each in self._duplicates(frame):
                each.path = frame.path
            self._hold(frame, pixmap)
            for each in [frame] + self._duplicates(frame):
                self.frame_ready.emit(each)
        self.progress.emit(load.done, load.failed, load.total)
        if load.pending <= 0:
            load.finished = True
            self.finished.emit(False)

    def _reloaded(self, frame, image, error):
        """A frame read from its file again, on a worker: held and announced."""
        self._reloading.discard(frame.index)
        if self._closed or not (0 <= frame.index < len(self._frames)) or self._frames[frame.index] is not frame:
            return
        if image is None:
            if error:
                logger.warning("The cached frame %s could not be read again: %s", frame.path, error)
                self._set_state(frame, FAILED, error)
                for each in [frame] + self._duplicates(frame):
                    self.frame_failed.emit(each)
            return
        self._hold(frame, QPixmap.fromImage(image), keep=True)  # (asked for: another frame makes room)
        for each in [frame] + self._duplicates(frame):
            self.frame_ready.emit(each)

    # ------------------------------------------------------------ memory

    def set_current(self, index: int):
        """The frame shown now: it and its neighbours are the last dropped
        from memory, and the ones about to be shown are read ahead."""
        self._current = int(index)
        if not self._frames:
            return
        # Read ahead within the budget: never so many that they push out the frame shown
        ahead = min(PREFETCH, max(0, (self._capacity(self._frames[0]) - 1) // 2))
        for step in range(1, ahead + 1):
            for index in (self._current + step, self._current - step):
                if 0 <= index < len(self._frames):
                    self._reread(self._frames[index])

    def held_count(self) -> int:
        return len(self._held)

    def _capacity(self, frame) -> int:
        return capacity(frame, self.memory_bytes)

    def _hold(self, frame, pixmap, keep: bool = False):
        """Keep ``pixmap`` as the frame's (and its duplicates'), dropping the
        frame furthest from the one shown when the budget is full: the
        newcomer itself if it is that one, unless ``keep`` (it is wanted now)."""
        for each in [frame] + self._duplicates(frame):
            each.pixmap = pixmap
        self._held[frame.index] = frame
        self._held.move_to_end(frame.index)
        limit = self._capacity(frame)
        while len(self._held) > limit:
            victim = self._furthest(exclude=frame.index if keep else None)
            if victim is None:
                break
            self._drop(victim)

    def _furthest(self, exclude: Optional[int] = None):
        """The held frame furthest from the one shown that can be read again."""
        best, distance = None, -1
        for index, held in self._held.items():
            if index == exclude or not held.path:
                continue
            far = abs(index - self._current)
            if far > distance:
                best, distance = held, far
        return best

    def _drop(self, frame):
        self._held.pop(frame.index, None)
        for each in [frame] + self._duplicates(frame):
            each.pixmap = None

    def _original(self, frame):
        if frame.duplicate_of is not None and 0 <= frame.duplicate_of < len(self._frames):
            return self._frames[frame.duplicate_of]
        return frame

    def _reread(self, frame) -> bool:
        """Have a worker read a ready frame's file again when its pixmap was
        dropped; True if that is under way (now or already)."""
        original = self._original(frame)
        if original.pixmap is not None or original.state != READY or not original.path or self._closed:
            return False
        if not (0 <= original.index < len(self._frames)) or self._frames[original.index] is not original:
            return False  # not this loader's frame (any more)
        if original.index not in self._reloading:
            self._reloading.add(original.index)
            self._ensure_workers()
            self._tasks.put(_Task(original, self._reloads, reload=True))
        return True

    def pixmap_of(self, frame):
        """The frame's pixmap from memory, or None: when it is ready but was
        dropped, a worker reads its file again and ``frame_ready`` says when
        it is back (and held, in place of a frame further from the one
        shown). None too if the frame is not ready."""
        if frame is None:
            return None
        original = self._original(frame)
        if original.pixmap is not None:
            self._held[original.index] = original
            self._held.move_to_end(original.index)
            return original.pixmap
        self._reread(original)
        return None

    def rereading(self, frame) -> bool:
        """Whether ``frame``'s pixmap is on its way back from disk."""
        return self._original(frame).index in self._reloading

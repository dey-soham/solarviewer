"""
Read the images next to the open one in the background.

While one image is on screen, a worker process reads its neighbours in the
file list with the same settings, so stepping to them skips the read. CASA
tools are not thread-safe, hence a process rather than a thread.

A prefetched result is used only when every input to the read matches and
the file is unchanged since it was read; otherwise the viewer reads the file
itself, exactly as it would without prefetching.
"""

import os
import threading
from concurrent.futures.process import BrokenProcessPool

from .image_data import read_image
from .image_meta import file_signature
from .workers import process_pool_executor


class ImagePrefetcher:
    """Keeps background reads of a few images, keyed by every read input."""

    def __init__(self):
        self._executor = None
        self._jobs = {}  # key -> Future
        self._lock = threading.Lock()

    @staticmethod
    def _key(imagename, stokes, threshold, rms_box, target_size):
        return (
            os.path.abspath(imagename),
            stokes,
            float(threshold),
            tuple(rms_box),
            target_size,
        )

    def _submit(self, imagename, stokes, threshold, rms_box, target_size):
        if self._executor is None:
            self._executor = process_pool_executor(1)
        try:
            return self._executor.submit(
                read_image, imagename, stokes, threshold, tuple(rms_box), target_size
            )
        except (BrokenProcessPool, RuntimeError):
            self._executor = None
            return None

    def schedule(self, paths, stokes, threshold, rms_box, target_size):
        """Read ``paths`` in the background, in order, dropping other reads."""
        wanted = {
            self._key(p, stokes, threshold, rms_box, target_size): p for p in paths
        }
        with self._lock:
            for key in list(self._jobs):
                if key not in wanted:
                    self._jobs.pop(key).cancel()
            for key, path in wanted.items():
                if key not in self._jobs:
                    future = self._submit(
                        path, stokes, threshold, rms_box, target_size
                    )
                    if future is not None:
                        self._jobs[key] = future

    def settle(self, imagename, wait=None):
        """Cancel queued reads of ``imagename`` and wait out a running one,
        so the caller does not read the file while the worker is.
        ``wait(future)``, if given, does the waiting (the window's background
        wait, which may raise ui.background.Cancelled)."""
        path = os.path.abspath(imagename)
        with self._lock:
            futures = [(k, f) for k, f in self._jobs.items() if k[0] == path]
            for key, future in futures:
                if future.cancel():
                    del self._jobs[key]
        for key, future in futures:
            if not future.cancelled():
                try:
                    if wait is not None:
                        wait(future)
                    # Readers of the header and coordsys run before take()
                    self._result(future, imagename)
                except BrokenProcessPool:
                    # The read went with its worker: the caller reads the file
                    with self._lock:
                        self._jobs.pop(key, None)
                    self._executor = None

    def take(self, imagename, stokes, threshold, rms_box, target_size):
        """The :class:`ImageRead` read in the background for exactly these
        inputs, or None if there is none (or the file changed since)."""
        key = self._key(imagename, stokes, threshold, rms_box, target_size)
        with self._lock:
            future = self._jobs.pop(key, None)
        if future is None or (not future.done() and future.cancel()):
            return None
        return self._result(future, imagename)

    def _result(self, future, imagename):
        """Wait for ``future``; its :class:`ImageRead` if it read the file as
        it is now (also caching the file's metadata), else None."""
        try:
            read = future.result()
        except BrokenProcessPool:
            self._executor = None
            return None
        except Exception:
            return None
        if read.signature is None or read.signature != file_signature(imagename):
            return None
        read.remember()
        return read

    def shutdown(self):
        with self._lock:
            self._jobs.clear()
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)


image_prefetcher = ImagePrefetcher()

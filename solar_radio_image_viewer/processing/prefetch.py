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

from .image_meta import (
    casa_info,
    coordsys_from_record,
    file_signature,
    fits_header,
    remember_casa_info,
    remember_fits_header,
)
from .fits_io import is_fits
from .workers import process_pool_executor


def _read(imagename, stokes, threshold, rms_box, target_size):
    """Worker side: what the viewer's load reads for ``imagename``."""
    from .image_data import get_pixel_values_from_image

    signature = file_signature(imagename)
    pix, csys, psf = get_pixel_values_from_image(
        imagename, stokes, threshold, rms_box=rms_box, target_size=target_size
    )
    # Both already cached by the read above, so no further opens here
    info = casa_info(imagename)
    header = None
    if is_fits(imagename):
        try:
            header = fits_header(imagename)
        except Exception:
            pass
    return signature, pix, csys.torecord(), psf, info, header


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
                _read, imagename, stokes, threshold, tuple(rms_box), target_size
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

    def settle(self, imagename):
        """Cancel queued reads of ``imagename`` and wait out a running one,
        so the caller does not read the file while the worker is."""
        path = os.path.abspath(imagename)
        with self._lock:
            futures = [(k, f) for k, f in self._jobs.items() if k[0] == path]
            for key, future in futures:
                if future.cancel():
                    del self._jobs[key]
        for _key, future in futures:
            if not future.cancelled():
                # Readers of the header and coordsys run before take()
                self._result(future, imagename)

    def take(self, imagename, stokes, threshold, rms_box, target_size):
        """``(pix, csys, psf)`` read in the background for exactly these inputs,
        or None if there is none (or the file changed since)."""
        key = self._key(imagename, stokes, threshold, rms_box, target_size)
        with self._lock:
            future = self._jobs.pop(key, None)
        if future is None or (not future.done() and future.cancel()):
            return None
        result = self._result(future, imagename)
        if result is None:
            return None
        pix, csys_record, psf = result
        return pix, coordsys_from_record(csys_record), psf

    def _result(self, future, imagename):
        """Wait for ``future``; ``(pix, csys_record, psf)`` if it read the file
        as it is now (also caching the file's metadata), else None."""
        try:
            signature, pix, csys_record, psf, info, header = future.result()
        except BrokenProcessPool:
            self._executor = None
            return None
        except Exception:
            return None
        if signature is None or signature != file_signature(imagename):
            return None

        remember_casa_info(signature, info)
        if header is not None:
            remember_fits_header(signature, header)
        return pix, csys_record, psf

    def shutdown(self):
        with self._lock:
            self._jobs.clear()
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)


image_prefetcher = ImagePrefetcher()

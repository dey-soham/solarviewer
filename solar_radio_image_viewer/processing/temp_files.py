"""
Removing SolarViewer's leftover temporary files.

Each window session writes temporary images (HPC / RA-Dec views, Tb and
flux maps, converted contour images, video frame conversions) and deletes
them when it closes; a crash or a killed process leaves them behind, and
the shared cache of readable FITS copies (fits_io.CACHE_DIR) is never
emptied. clean_temp_files removes files of SolarViewer processes that are
no longer running, and keeps the cache within an age and a size.

Session file names carry the process id ("p<pid>-"), see session_id.
"""

import logging
import os
import re
import shutil
import tempfile
import time
import uuid

logger = logging.getLogger(__name__)

# Cache entries unused for longer, and the cache size kept to
CACHE_MAX_AGE_DAYS = 30
CACHE_MAX_BYTES = 2 * 1024**3

# Session files with names from before process ids were in them
OLD_SESSION_FILES_AGE_HOURS = 24

# Temporary files and folders of a SolarViewer process, and its process id
_SESSION_ITEM = re.compile(
    r"^(?:sv_contour_transformed_|solarviewer_(?:hpc|radec|tb|flux)_temp_|solarviewer-video-)"
    r"p(?P<pid>\d+)-"
    r"|^temp_hpc(?:_all)?.*_(?P<pid2>\d+)\.fits$"
    r"|^solarviewer_radec_export_(?P<pid3>\d+)\.fits$"
)
# The same, named before they carried a process id
_OLD_SESSION_ITEM = re.compile(
    r"^(?:sv_contour_transformed_|solarviewer_(?:hpc|radec|tb|flux)_temp_)[0-9a-f]{8}[_.]"
    r"|^solarviewer-video-(?!p\d+-)"
)


def session_id():
    """A unique id for one session's temporary file names: "p<pid>-<hex>",
    so the files of processes no longer running can be told apart."""
    return f"p{os.getpid()}-{uuid.uuid4().hex[:8]}"


def _running(pid):
    try:
        import psutil

        return psutil.pid_exists(pid)
    except ImportError:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


def _size(path):
    if os.path.isdir(path) and not os.path.islink(path):
        return sum(
            os.path.getsize(os.path.join(root, name))
            for root, _dirs, files in os.walk(path)
            for name in files
            if os.path.exists(os.path.join(root, name))
        )
    return os.path.getsize(path)


def _remove(path):
    """Remove a file or folder; its size in bytes (0 if it couldn't be)."""
    try:
        size = _size(path)
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        return size
    except OSError as e:
        logger.debug(f"Could not remove {path}: {e}")
        return 0


def clean_temp_files(
    temp_dir=None,
    cache_dir=None,
    cache_max_age_days=CACHE_MAX_AGE_DAYS,
    cache_max_bytes=CACHE_MAX_BYTES,
    now=None,
):
    """Remove leftover temporary files: those of SolarViewer processes no
    longer running (old-style names: after a day), and cache entries unused
    for ``cache_max_age_days`` or beyond ``cache_max_bytes`` (least
    recently used first). Returns (items removed, bytes freed)."""
    from .fits_io import CACHE_DIR

    temp_dir = temp_dir or tempfile.gettempdir()
    cache_dir = cache_dir or CACHE_DIR
    now = time.time() if now is None else now
    removed = freed = 0

    try:
        names = os.listdir(temp_dir)
    except OSError:
        names = []
    for name in names:
        path = os.path.join(temp_dir, name)
        match = _SESSION_ITEM.match(name)
        if match:
            pid = int(match.group("pid") or match.group("pid2") or match.group("pid3"))
            if pid == os.getpid() or _running(pid):
                continue
        elif _OLD_SESSION_ITEM.match(name):
            try:
                if now - os.path.getmtime(path) < OLD_SESSION_FILES_AGE_HOURS * 3600:
                    continue
            except OSError:
                continue
        else:
            continue
        size = _remove(path)
        if size or not os.path.exists(path):
            removed += 1
            freed += size

    # The cache: one folder per source file, used when its file was last
    # read (fits_io touches it)
    entries = []
    try:
        for name in os.listdir(cache_dir):
            path = os.path.join(cache_dir, name)
            try:
                used = max(
                    [os.path.getmtime(path)]
                    + [os.path.getmtime(os.path.join(path, f)) for f in os.listdir(path)]
                    if os.path.isdir(path)
                    else [os.path.getmtime(path)]
                )
                entries.append([used, _size(path), path])
            except OSError:
                continue
    except OSError:
        pass
    entries.sort()  # least recently used first
    total = sum(size for _, size, _ in entries)
    for used, size, path in entries:
        if now - used > cache_max_age_days * 86400 or total > cache_max_bytes:
            freed_here = _remove(path)
            if freed_here or not os.path.exists(path):
                removed += 1
                freed += freed_here
                total -= size
    if removed:
        logger.info(f"Removed {removed} leftover temporary files ({freed / 1024**2:.0f} MB)")
    return removed, freed


def clean_temp_files_in_background():
    """clean_temp_files in a background thread (at start up)."""
    import threading

    thread = threading.Thread(target=clean_temp_files, name="clean-temp-files", daemon=True)
    thread.start()
    return thread

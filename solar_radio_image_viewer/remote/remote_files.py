"""
Images on an SSH server, used on this computer.

The server needs nothing but SSH (SFTP): its folders are listed and
matched against patterns, FITS headers (for observation times and
coordinates) are read from the start of the files without fetching the
images, and images are fetched into the RemoteFileCache when they are
needed, several at a time. Threads borrow SFTP channels from a pool the
RemoteFiles owns (one channel can't be used by two threads at once, and a
channel closed by whichever thread happens to collect it can corrupt the
connection's stream).
"""

import contextlib
import fnmatch
import gzip
import json
import logging
import os
import posixpath
import queue
import socket
import stat
import threading
from concurrent.futures import ThreadPoolExecutor

import paramiko

from .file_cache import RemoteFileCache

logger = logging.getLogger(__name__)

FITS_SUFFIXES = (".fits", ".fts", ".fit", ".fits.fz", ".fz", ".fits.gz", ".fts.gz")
BLOCK = 2880  # FITS block size


class Cancelled(Exception):
    """A fetch stopped by its ``cancelled`` callable."""


class RemoteFiles:
    """The image files of an SSH server (``connection``, an SSHConnection)."""

    def __init__(self, connection, cache=None, workers=4):
        self.connection = connection
        self.cache = cache or RemoteFileCache()
        self.workers = workers
        self._label = connection.connection_info
        self._channels = queue.LifoQueue()  # idle SFTP channels
        self._all_channels = []
        self._lock = threading.Lock()
        self._stats = {}  # remote path -> (mtime, size, is_dir)
        self._casa = {}  # remote folder -> is a CASA image
        self._headers = {}  # (path, mtime, size) -> header text
        self._headers_file = os.path.join(str(self.cache.cache_dir), "headers.json")
        self._load_headers()

    @property
    def host(self):
        return self.connection._host

    @property
    def label(self):
        """E.g. "user@server:22" (as when connected)."""
        return self._label

    # ---------- SFTP ----------

    @contextlib.contextmanager
    def _sftp(self):
        """An SFTP channel of the pool, for this thread until the block ends."""
        try:
            sftp = self._channels.get_nowait()
        except queue.Empty:
            self.connection.ensure_connected()
            sftp = self.connection._client.get_transport().open_sftp_client()
            with self._lock:
                self._all_channels.append(sftp)
        broken = False
        try:
            yield sftp
        except (EOFError, socket.error, paramiko.SSHException):
            broken = True  # the channel (or connection) failed
            raise
        finally:
            if broken or sftp.sock.closed:
                self._drop(sftp)
            else:
                self._channels.put(sftp)

    def _drop(self, sftp):
        with self._lock:
            if sftp in self._all_channels:
                self._all_channels.remove(sftp)
        try:
            sftp.close()
        except Exception:
            pass

    def close(self):
        """Close the SFTP channels (the connection stays open)."""
        with self._lock:
            channels, self._all_channels = list(self._all_channels), []
        for sftp in channels:
            try:
                sftp.close()
            except Exception:
                pass
        self._channels = queue.LifoQueue()

    def _stat(self, path, refresh=False):
        """(mtime, size, is_dir) of ``path`` on the server."""
        if refresh or path not in self._stats:
            with self._sftp() as sftp:
                attr = sftp.stat(path)
            self._stats[path] = (attr.st_mtime, attr.st_size, stat.S_ISDIR(attr.st_mode))
        return self._stats[path]

    # ---------- Listing ----------

    def listdir(self, directory):
        """[(name, path, is_dir)] in ``directory``, sorted by name."""
        entries = []
        with self._sftp() as sftp:
            listing = sftp.listdir_attr(directory)
        for attr in listing:
            path = posixpath.join(directory, attr.filename)
            is_dir = stat.S_ISDIR(attr.st_mode)
            self._stats[path] = (attr.st_mtime, attr.st_size, is_dir)
            entries.append((attr.filename, path, is_dir))
        return sorted(entries)

    def is_dir(self, path):
        try:
            return self._stat(path)[2]
        except OSError:
            return False

    def is_casa_image(self, path):
        """A folder holding an image table."""
        if path not in self._casa:
            try:
                with self._sftp() as sftp:
                    sftp.stat(posixpath.join(path, "table.info"))
                self._casa[path] = True
            except OSError:
                self._casa[path] = False
        return self._casa[path]

    def is_image(self, path):
        """A FITS file or a CASA image on the server."""
        try:
            is_dir = self._stat(path)[2]
        except OSError:
            return False
        if is_dir:
            return self.is_casa_image(path)
        return path.lower().endswith(FITS_SUFFIXES)

    def glob(self, directory, pattern):
        """Paths in ``directory`` whose names match ``pattern`` (sorted)."""
        return [
            path for name, path, _ in self.listdir(directory) if fnmatch.fnmatch(name, pattern)
        ]

    # ---------- Headers ----------

    def _load_headers(self):
        try:
            with open(self._headers_file) as f:
                saved = json.load(f)
            self._headers = {tuple(json.loads(k)): v for k, v in saved.items()}
        except (OSError, ValueError, TypeError):
            self._headers = {}

    def _save_headers(self):
        with self._lock:
            data = {json.dumps(list(k)): v for k, v in self._headers.items()}
        try:
            os.makedirs(os.path.dirname(self._headers_file), exist_ok=True)
            partial = f"{self._headers_file}.{os.getpid()}"
            with open(partial, "w") as f:
                json.dump(data, f)
            os.replace(partial, self._headers_file)
        except OSError as e:
            logger.debug(f"Headers not saved: {e}")

    def header(self, path):
        """The FITS header of image ``path`` on the server (with the image
        extension's cards when the image isn't in the primary HDU), read
        without fetching the image; for a CASA image the header of its
        fetched copy. None if it can't be read."""
        from astropy.io import fits

        try:
            mtime, size, is_dir = self._stat(path)
        except OSError:
            return None
        key = (self.host, path, mtime, size)
        text = self._headers.get(key)
        if text is None:
            try:
                if is_dir:
                    from ..processing.fits_io import fits_image_path

                    text = fits.getheader(fits_image_path(self.fetch([path])[path])).tostring()
                else:
                    text = self._read_header(path)
            except Exception as e:
                logger.warning(f"Header of {path} not read: {e}")
                return None
            with self._lock:
                self._headers[key] = text
        return fits.Header.fromstring(text)

    def _read_header(self, path):
        """Header text from the start of a FITS file on the server."""
        from astropy.io import fits

        with self._sftp() as sftp, sftp.open(path, "rb") as raw:
            stream = gzip.GzipFile(fileobj=raw) if path.lower().endswith(".gz") else raw

            def next_header():
                data = b""
                while True:
                    block = stream.read(BLOCK)
                    if len(block) < BLOCK:
                        raise ValueError("no END card")
                    data += block
                    cards = [block[i : i + 80] for i in range(0, BLOCK, 80)]
                    if any(c.startswith(b"END") and c[3:].strip() == b"" for c in cards):
                        return fits.Header.fromstring(data.decode("ascii", "replace"))

            primary = next_header()
            if primary.get("NAXIS", 0) >= 2 or not primary.get("EXTEND", False):
                return primary.tostring()
            # The image is in an extension (e.g. tile-compressed): the primary
            # HDU has no data, so the next header follows at once
            image = next_header()
            if image.get("ZIMAGE"):
                # Tile-compressed: the image's dimensions are the Z keywords
                # (its WCS, time and unit are plain keywords already)
                for n in range(int(image.get("ZNAXIS", 0)) + 1):
                    key = "ZNAXIS" + (str(n) if n else "")
                    if key in image:
                        image["NAXIS" + (str(n) if n else "")] = image[key]
            for card in primary.cards:
                if card.keyword not in image and card.keyword not in (
                    "SIMPLE", "BITPIX", "NAXIS", "EXTEND", "CHECKSUM", "DATASUM", "",
                ):
                    image.append(card)
            return image.tostring()

    def headers(self, paths, progress=None, cancelled=None):
        """{path: header or None} of ``paths``, read in parallel."""
        found = {p: None for p in paths}
        todo = []
        for p in paths:
            try:
                mtime, size, _ = self._stat(p)
                if (self.host, p, mtime, size) in self._headers:
                    found[p] = self.header(p)
                    continue
            except OSError:
                continue
            todo.append(p)
        if todo:
            done = 0
            with ThreadPoolExecutor(self.workers) as pool:
                for path, head in zip(todo, pool.map(self.header, todo)):
                    found[path] = head
                    done += 1
                    if progress:
                        progress(done, len(todo))
                    if cancelled and cancelled():
                        break
            self._save_headers()
        return found

    # ---------- Fetching ----------

    def cached(self, path):
        """The local copy of ``path`` if it is cached and up to date."""
        try:
            mtime, size, _ = self._stat(path)
        except OSError:
            return None
        local = self.cache.get_cached_path(self.host, path, mtime, size)
        return str(local) if local else None

    def fetch(self, paths, progress=None, cancelled=None):
        """{path: local copy} of ``paths`` (files or CASA folders), fetched
        into the cache (several at a time) unless cached already.

        ``progress(done_bytes, total_bytes, done_files, total_files)`` is
        told how far it got; ``cancelled()`` returning True stops it (raises
        Cancelled; files fetched so far stay cached)."""
        paths = list(dict.fromkeys(paths))
        local = {}
        todo = []
        for path in paths:
            mtime, size, is_dir = self._stat(path, refresh=True)
            copy = self.cached(path)
            if copy:
                local[path] = copy
            else:
                if is_dir:
                    size = self._dir_size(path)
                todo.append((path, size, is_dir, mtime))
        total_bytes = sum(t[1] for t in todo)
        state = {"bytes": 0, "files": 0}

        def report(nbytes=0, files=0):
            with self._lock:
                state["bytes"] += nbytes
                state["files"] += files
                done_bytes, done_files = state["bytes"], state["files"]
            if progress:
                progress(done_bytes, total_bytes, done_files, len(todo))

        def one(item):
            path, size, is_dir, mtime = item
            if cancelled and cancelled():
                raise Cancelled()
            target = self.cache.get_cache_path(self.host, path)
            os.makedirs(os.path.dirname(str(target)), exist_ok=True)
            if is_dir:
                self._get_dir(path, str(target), report, cancelled)
            else:
                self._get_file(path, str(target), report, cancelled)
            with self._lock:
                self.cache.mark_cached(self.host, path, target, mtime, self._stats[path][1])
            report(files=1)
            return path, str(target)

        if todo:
            if progress:
                progress(0, total_bytes, 0, len(todo))
            with ThreadPoolExecutor(min(self.workers, len(todo))) as pool:
                for path, copy in pool.map(one, todo):
                    local[path] = copy
        return local

    def _get_file(self, path, target, report, cancelled):
        partial = f"{target}.{os.getpid()}.{threading.get_ident()}.part"
        seen = [0]

        def callback(transferred, _total):
            report(nbytes=transferred - seen[0])
            seen[0] = transferred
            if cancelled and cancelled():
                raise Cancelled()

        try:
            with self._sftp() as sftp:
                sftp.get(path, partial, callback=callback)
            os.replace(partial, target)
        except BaseException:
            if os.path.exists(partial):
                os.remove(partial)
            raise

    def _get_dir(self, path, target, report, cancelled):
        import shutil

        partial = f"{target}.{os.getpid()}.{threading.get_ident()}.part"
        os.makedirs(partial, exist_ok=True)
        try:
            with self._sftp() as sftp:
                self._copy_tree(sftp, path, partial, report, cancelled)
            if os.path.exists(target):
                shutil.rmtree(target)
            os.replace(partial, target)
        except BaseException:
            shutil.rmtree(partial, ignore_errors=True)
            raise

    def _copy_tree(self, sftp, path, target, report, cancelled):
        for attr in sftp.listdir_attr(path):
            remote = posixpath.join(path, attr.filename)
            local = os.path.join(target, attr.filename)
            if stat.S_ISDIR(attr.st_mode):
                os.makedirs(local, exist_ok=True)
                self._copy_tree(sftp, remote, local, report, cancelled)
            else:
                if cancelled and cancelled():
                    raise Cancelled()
                seen = [0]

                def callback(transferred, _total, seen=seen):
                    report(nbytes=transferred - seen[0])
                    seen[0] = transferred

                sftp.get(remote, local, callback=callback)

    def _dir_size(self, path):
        total = 0
        with self._sftp() as sftp:
            listing = sftp.listdir_attr(path)
        for attr in listing:
            if stat.S_ISDIR(attr.st_mode):
                total += self._dir_size(posixpath.join(path, attr.filename))
            else:
                total += attr.st_size
        return total

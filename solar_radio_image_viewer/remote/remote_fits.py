"""
FITS files on an SSH server, read in pieces.

Only the bytes a view needs come over SFTP: the headers, the tables (e.g.
the time and frequency axes), and slices of the primary image, each read at
its offset in the file; other large extensions (e.g. an image of the
noise) are read only if asked for. Slices are kept in the local cache, so each is read from the server
once. Compressed files can't be read in pieces (NotSliceable): they are
fetched whole instead.
"""

import contextlib
import hashlib
import json
import logging
import os
import shlex
import threading
import time
import zlib
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

import numpy as np

from .remote_files import BLOCK, Cancelled

logger = logging.getLogger(__name__)

GAP = 1024  # read through gaps this small rather than ask for each piece
DENSE = 256  # columns this close (bytes) are read as whole spans of a row
PIECE = 8 * 1024 * 1024  # most bytes per readv (progress and cancelling between)
MIN_PIECE = 256 * 1024  # fewest bytes per readv when splitting reads over channels
BATCH = 4096  # ranges per readv
MAX_RANGES = 300_000  # most separate ranges one slice may need
CONCURRENT = 256  # requests in flight (faster than sftp.get on a slow link)
PIECES_LIMIT = 4 * 1024**3  # local cache of slices, all files together
SMALL = 16 * 1024**2  # extensions read whole when opening, if not tables

_DTYPES = {8: "u1", 16: ">i2", 32: ">i4", 64: ">i8", -32: ">f4", -64: ">f8"}
_COMPRESSED = (".gz", ".fz", ".bz2", ".zip", ".z")

_report = threading.local()


class NotSliceable(Exception):
    """The file can't be read in pieces (compressed, or no 2-D primary image)."""


@contextlib.contextmanager
def reporting(progress=None, cancelled=None):
    """Reads in this thread, until the block ends, tell ``progress(done_bytes,
    total_bytes)`` how far they got and stop (raise Cancelled) when
    ``cancelled()`` returns True."""
    before = getattr(_report, "state", None)
    _report.state = (progress, cancelled)
    try:
        yield
    finally:
        _report.state = before


def current_reporting():
    """(progress, cancelled) set by ``reporting`` in this thread, or Nones."""
    return getattr(_report, "state", None) or (None, None)


def block_stats(x, skip):
    """Mean and maximum (NaN ignored) of each block of ``skip`` rows of ``x``
    ((time, freq), float32; the last block may be shorter)."""
    starts = np.arange(0, x.shape[0], skip)
    finite = np.isfinite(x)
    sums = np.add.reduceat(np.where(finite, x, 0), starts, axis=0, dtype=np.float64)
    counts = np.add.reduceat(finite, starts, axis=0, dtype=np.int64)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = (sums / counts).astype(np.float32)
    peak = np.fmax.reduceat(np.where(finite, x, np.nan), starts, axis=0).astype(np.float32)
    return mean, peak


# Run on the server (python3 and numpy only): the overview's statistics of
# every sample, read where the file is; only the result crosses the network.
# (block_stats, written out again: the server has none of this package.)
_STATS_SCRIPT = r"""
import sys, warnings, zlib
import numpy as np
warnings.simplefilter("ignore")
path, offset, dtype, n0, n1, time_dim, skip, scale, zero, blank = sys.argv[1:11]
offset, n0, n1, time_dim, skip = int(offset), int(n0), int(n1), int(time_dim), int(skip)
scale, zero = float(scale), float(zero)
blank = None if blank == "none" else int(blank)
a = np.memmap(path, dtype=np.dtype(dtype), mode="r", offset=offset, shape=(n0, n1))
nt, nf = (n0, n1) if time_dim == 0 else (n1, n0)
nb = -(-nt // skip)
mean = np.empty((nb, nf), np.float32)
peak = np.empty((nb, nf), np.float32)
per = max(1, (16000000 // nf) // skip)
for b0 in range(0, nb, per):
    b1 = min(b0 + per, nb)
    s, e = b0 * skip, min(b1 * skip, nt)
    raw = a[s:e, :] if time_dim == 0 else a[:, s:e].T
    x = raw.astype(np.float32)
    if blank is not None:
        x[raw == blank] = np.nan
    if scale != 1 or zero != 0:
        x = x * np.float32(scale) + np.float32(zero)
    finite = np.isfinite(x)
    starts = np.arange(0, e - s, skip)
    sums = np.add.reduceat(np.where(finite, x, 0), starts, axis=0, dtype=np.float64)
    counts = np.add.reduceat(finite, starts, axis=0, dtype=np.int64)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean[b0:b1] = sums / counts
    peak[b0:b1] = np.fmax.reduceat(np.where(finite, x, np.nan), starts, axis=0)
    sys.stderr.write("P %d %d\n" % (e, nt))
    sys.stderr.flush()
out = zlib.compress(mean.tobytes() + peak.tobytes(), 1)
sys.stdout.buffer.write(("%d %d %d\n" % (nb, nf, len(out))).encode())
sys.stdout.buffer.write(out)
sys.stdout.buffer.flush()
"""


def _padded(size):
    return -(-size // BLOCK) * BLOCK


def _image_axes(header):
    """The axes of an image header (numpy order) when it is 2-D once
    leading axes of length 1 are dropped, else None."""
    naxis = header.get("NAXIS", 0)
    if naxis < 2:
        return None
    axes = [header.get(f"NAXIS{i}", 0) for i in range(naxis, 0, -1)]
    while len(axes) > 2 and axes[0] == 1:
        axes.pop(0)
    return axes if len(axes) == 2 and all(axes) else None


def _data_size(header):
    """Bytes of an HDU's data (unpadded)."""
    naxis = header.get("NAXIS", 0)
    if not naxis:
        return 0
    groups = header.get("GROUPS") and header.get("NAXIS1") == 0
    count = 1
    for i in range(2 if groups else 1, naxis + 1):
        count *= header[f"NAXIS{i}"]
    return abs(header["BITPIX"]) // 8 * header.get("GCOUNT", 1) * (header.get("PCOUNT", 0) + count)


class RemoteFits:
    """FITS file ``path`` on the server of ``files`` (a RemoteFiles).

    ``hdul`` stands in for astropy's HDUList: ``hdul[0].data`` is the primary
    image as a RemoteArray; table and small extensions are astropy HDUs,
    large image extensions RemoteHDUs (their data a RemoteArray, if 2-D).
    """

    def __init__(self, files, path):
        if path.lower().endswith(_COMPRESSED):
            raise NotSliceable("the file is compressed")
        self.files = files
        self.path = path
        mtime, self.size, is_dir = files._stat(path, refresh=True)
        if is_dir:
            raise NotSliceable("it is a folder")
        key = hashlib.sha1(f"{files.host}\0{path}\0{mtime}\0{self.size}".encode()).hexdigest()
        self._dir = os.path.join(str(files.cache.cache_dir), getattr(files.cache, "PIECES_DIR", "pieces"), key)
        os.makedirs(self._dir, exist_ok=True)
        self._open_channels()

        self._hdus = self._walk()  # [(header, header offset, data offset, data size)]
        if any(h.get("ZIMAGE") for h, *_ in self._hdus):
            raise NotSliceable("its image is tile-compressed")
        # The spectrum: the first 2-D image, primary or extension
        spectrum = next(
            (
                i
                for i, (h, *_) in enumerate(self._hdus)
                if _image_axes(h) and (i == 0 or str(h.get("XTENSION", "")).strip() == "IMAGE")
            ),
            None,
        )
        if spectrum is None:
            raise NotSliceable("it has no 2-D image")
        primary = self._hdus[0][0]
        self._spectrum = spectrum
        self._tables = self._read_tables()
        hdus = [RemoteHDU(primary, RemoteArray(self, primary, self._hdus[0][2]) if spectrum == 0 else None)]
        read = iter(self._tables[1:] if self._tables else [])
        for i, (header, _, data_offset, size) in enumerate(self._hdus[1:], 1):
            if i != spectrum and self._read_whole(header, size):
                hdus.append(next(read))
            else:
                try:
                    data = RemoteArray(self, header, data_offset)
                except NotSliceable:
                    data = None
                hdus.append(RemoteHDU(header, data))
        self.data = hdus[spectrum].data  # the spectrum, read in pieces
        self.hdul = RemoteHDUList(hdus, self._tables)

    def _open_channels(self):
        """Open the SFTP channels reads share, in the background (opening
        one can take seconds on a distant server)."""
        n = max(1, self.files.workers) - len(getattr(self.files, "_all_channels", ()))
        if n <= 0:
            return
        barrier = threading.Barrier(n)

        def hold():
            # Each thread keeps its channel until all are open, so each opens one
            with contextlib.suppress(Exception), self.files._sftp():
                barrier.wait(timeout=30)

        for _ in range(n):
            threading.Thread(target=hold, daemon=True).start()

    # ---------- Structure ----------

    def _walk(self):
        """Every HDU's header and where its data is, from the cache or read."""
        from astropy.io import fits

        saved = os.path.join(self._dir, "hdus.json")
        try:
            with open(saved) as f:
                return [(fits.Header.fromstring(t), *rest) for t, *rest in json.load(f)]
        except (OSError, ValueError):
            pass
        hdus = []
        offset = 0
        with self.files._sftp() as sftp, sftp.open(self.path, "rb") as f:
            while offset < self.size:
                text, length = self._header_at(f, offset)
                header = fits.Header.fromstring(text)
                size = _data_size(header)
                hdus.append((header, offset, offset + length, size))
                offset += length + _padded(size)
        if not hdus:
            raise NotSliceable("no FITS header found")
        self._save(saved, json.dumps([[h.tostring(), *rest] for h, *rest in hdus]).encode())
        return hdus

    def _header_at(self, f, offset):
        """(header text, length in bytes) of the header starting at ``offset``."""
        f.seek(offset)
        data = b""
        while True:
            block = f.read(4 * BLOCK)
            if not block:
                raise NotSliceable("a header has no END card")
            start = len(data)
            data += block
            if data[:8] not in (b"SIMPLE  ", b"XTENSION"):
                raise NotSliceable("not a FITS file")
            for i in range(start - start % 80, len(data) - 79, 80):
                card = data[i : i + 80]
                if card.startswith(b"END") and card[3:].strip() == b"":
                    length = _padded(i + 80)
                    return data[:length].decode("ascii", "replace"), length

    def _read_whole(self, header, size):
        """Whether an extension is read whole when opening: tables (the
        viewer's axes are tables) and small ones."""
        return header.get("XTENSION", "").strip() in ("BINTABLE", "TABLE") or size <= SMALL

    def _read_tables(self):
        """The extensions read whole (_read_whole), as a local FITS file, opened."""
        ranges = [
            (header_offset, min(self.size, data_offset + _padded(size)) - header_offset)
            for i, (header, header_offset, data_offset, size) in enumerate(self._hdus[1:], 1)
            if i != self._spectrum and self._read_whole(header, size)
        ]
        if not ranges:
            return None
        from astropy.io import fits

        local = os.path.join(self._dir, "extensions.fits")
        if not os.path.exists(local):
            body = self.read_ranges(ranges)
            self._save(local, fits.PrimaryHDU().header.tostring().encode() + bytes(body))
        return fits.open(local, memmap=False)

    # ---------- Reading ----------

    def read_ranges(self, ranges):
        """The bytes of ``ranges`` [(offset, length)] (sorted) one after another.

        Batches of ranges are read on several SFTP channels at once (on a
        distant server one channel spends most of its time waiting)."""
        total = sum(n for _, n in ranges)
        workers = max(1, self.files.workers)
        size = min(PIECE, max(MIN_PIECE, -(-total // (2 * workers))))
        pieces = []  # split so the channels share the work
        for offset, length in ranges:
            while length > 0:
                n = min(length, size)
                pieces.append((offset, n))
                offset += n
                length -= n
        batches = []  # (first piece, end piece, where in the output)
        i = at = 0
        while i < len(pieces):
            j, nbytes = i, 0
            while j < len(pieces) and j - i < BATCH and (nbytes < size or j == i):
                nbytes += pieces[j][1]
                j += 1
            batches.append((i, j, at))
            at += nbytes
            i = j

        out = bytearray(total)
        view = memoryview(out)
        progress, cancelled = getattr(_report, "state", None) or (None, None)
        stop = threading.Event()

        def read(batch):
            i, j, at = batch
            if stop.is_set():
                raise Cancelled()
            with self.files._sftp() as sftp, sftp.open(self.path, "rb") as f:
                try:
                    blocks = f.readv(pieces[i:j], max_concurrent_prefetch_requests=CONCURRENT)
                except TypeError:  # paramiko < 3.3
                    blocks = f.readv(pieces[i:j])
                for (_, n), block in zip(pieces[i:j], blocks):
                    if len(block) != n:
                        raise OSError(f"{self.path}: short read")
                    view[at : at + n] = block
                    at += n
            return at - batch[2]

        # Progress is told here, in the calling thread (it may update a dialog)
        done = 0
        if progress:
            progress(0, total)
        with ThreadPoolExecutor(min(workers, len(batches)) or 1) as pool:
            pending = {pool.submit(read, b) for b in batches}
            try:
                while pending:
                    finished, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                    for future in finished:
                        done += future.result()
                    if progress and finished:
                        progress(done, total)
                    if cancelled and cancelled():
                        raise Cancelled()
            finally:
                stop.set()
                for future in pending:
                    future.cancel()
        return out

    # ---------- Cache ----------

    def piece(self, key, make):
        """``make()`` (an array), kept in the cache under ``key``."""
        name = hashlib.sha1(repr(key).encode()).hexdigest() + ".npy"
        path = os.path.join(self._dir, name)
        try:
            value = np.load(path)
            os.utime(path)  # recently used
            return value
        except (OSError, ValueError):
            pass
        value = make()
        partial = f"{path}.{os.getpid()}.{threading.get_ident()}.part"
        try:
            with open(partial, "wb") as f:
                np.save(f, value)
            os.replace(partial, path)
            _prune(os.path.dirname(self._dir))
        except OSError as e:
            logger.debug(f"Slice not cached: {e}")
            with contextlib.suppress(OSError):
                os.remove(partial)
        return value

    def _save(self, path, data):
        partial = f"{path}.{os.getpid()}.part"
        with open(partial, "wb") as f:
            f.write(data)
        os.replace(partial, path)


def _prune(root, limit=PIECES_LIMIT):
    """Remove the least recently used slices until the cache fits ``limit``."""
    files = []
    for folder in os.scandir(root):
        if folder.is_dir():
            for entry in os.scandir(folder.path):
                if entry.name.endswith(".npy"):
                    st = entry.stat()
                    files.append((st.st_mtime, st.st_size, entry.path))
    total = sum(size for _, size, _ in files)
    for _, size, path in sorted(files):
        if total <= limit:
            break
        with contextlib.suppress(OSError):
            os.remove(path)
            total -= size


class RemoteArray:
    """The 2-D primary image of a RemoteFits, sliced like a numpy array
    (slices only, positive steps). Values come back scaled, as float32."""

    ndim = 2
    dtype = np.dtype(np.float32)

    def __init__(self, fits_file, header, offset):
        axes = [header[f"NAXIS{i}"] for i in range(header["NAXIS"], 0, -1)]
        while len(axes) > 2 and axes[0] == 1:
            axes.pop(0)  # e.g. a Stokes axis of length 1
        if len(axes) != 2:
            raise NotSliceable("its image isn't 2-D")
        bitpix = header["BITPIX"]
        if bitpix not in _DTYPES:
            raise NotSliceable(f"BITPIX {bitpix}")
        self._file = fits_file
        self._offset = offset
        self._raw = np.dtype(_DTYPES[bitpix])
        self._scale = header.get("BSCALE", 1.0)
        self._zero = header.get("BZERO", 0.0)
        self._blank = header.get("BLANK") if bitpix > 0 else None
        self.shape = tuple(axes)
        self.size = axes[0] * axes[1]
        self.nbytes = self.size * self._raw.itemsize

    def min_step(self, axis):
        """The smallest step along ``axis`` a slice of the whole image can
        take without needing too many separate reads (rows are read whole,
        so along axis 0 any step is fine)."""
        if axis == 0:
            return 1
        per_row = max(1, MAX_RANGES // self.shape[0])
        return max(1, -(-self.shape[1] // per_row))

    def block_stats(self, time_dim, skip):
        """(mean, max) of each block of ``skip`` time samples, every sample
        counted ((blocks, freq) float32), computed on the server: kept in
        the cache. Progress and cancelling as set by ``reporting``. Raises
        NotSliceable when the server can't (no python3 with numpy)."""
        key = ("overview", self._offset, time_dim, skip)
        both = self._file.piece(key, lambda: np.stack(self._server_block_stats(time_dim, skip)))
        return both[0], both[1]

    def _server_block_stats(self, time_dim, skip):
        progress, cancelled = current_reporting()
        raw = self._raw.str  # e.g. ">f4"
        args = [
            self._file.path, self._offset, raw, self.shape[0], self.shape[1], time_dim, skip,
            self._scale, self._zero, "none" if self._blank is None else int(self._blank),
        ]
        command = "python3 -c " + shlex.quote(_STATS_SCRIPT) + " " + " ".join(
            shlex.quote(str(a)) for a in args
        )
        connection = self._file.files.connection
        connection.ensure_connected()
        channel = connection._client.get_transport().open_session()
        try:
            channel.exec_command(command)
            out = bytearray()
            err = b""
            messages = []
            total = self.nbytes
            while True:
                if cancelled and cancelled():
                    raise Cancelled()
                busy = False
                while channel.recv_ready():
                    out += channel.recv(1 << 20)
                    busy = True
                while channel.recv_stderr_ready():
                    err += channel.recv_stderr(65536)
                    busy = True
                *lines, err = err.split(b"\n")
                for line in lines:
                    parts = line.split()
                    if len(parts) == 3 and parts[0] == b"P":
                        if progress:
                            done, nt = int(parts[1]), int(parts[2])
                            progress(total * done // max(nt, 1), total)
                    elif line.strip():
                        messages.append(line.decode("utf-8", "replace"))
                if not busy:
                    if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                        break
                    time.sleep(0.02)
            status = channel.recv_exit_status()
        finally:
            channel.close()
        if status != 0:
            raise NotSliceable(
                "the server can't compute it: " + (messages[-1] if messages else f"exit {status}")
            )
        head, _, body = bytes(out).partition(b"\n")
        nb, nf, size = (int(v) for v in head.split())
        values = np.frombuffer(zlib.decompress(body[:size]), np.float32)
        mean, peak = values[: nb * nf].reshape(nb, nf), values[nb * nf :].reshape(nb, nf)
        return mean.copy(), peak.copy()

    def __array__(self, dtype=None, copy=None):
        data = self[:, :]
        return data if dtype is None else data.astype(dtype, copy=False)

    def __getitem__(self, key):
        return self.read(key)

    def read(self, key, cache=True):
        """``self[key]``; with ``cache`` False not kept in the local cache
        (e.g. reading the whole file once to save it)."""
        if not isinstance(key, tuple):
            key = (key,)
        key = key + (slice(None),) * (2 - len(key))
        if len(key) != 2 or not all(isinstance(k, slice) for k in key):
            raise TypeError("RemoteArray takes slices only")
        rows = range(*key[0].indices(self.shape[0]))
        cols = range(*key[1].indices(self.shape[1]))
        if rows.step < 0 or cols.step < 0:
            raise TypeError("RemoteArray takes positive steps only")
        if not rows or not cols:
            return np.empty((len(rows), len(cols)), np.float32)
        key = (rows.start, rows.stop, rows.step, cols.start, cols.stop, cols.step)
        if not cache:
            return self._read(rows, cols)
        # (the offset tells the file's images apart)
        return self._file.piece(("data", self._offset) + key, lambda: self._read(rows, cols))

    def _read(self, rows, cols):
        isz = self._raw.itemsize
        n1 = self.shape[1]
        r = np.arange(rows.start, rows.stop, rows.step, dtype=np.int64)
        dense = cols.step * isz <= DENSE
        if dense:
            # Each row's span of the columns, strided here
            span = cols[-1] - cols[0] + 1
            offsets = self._offset + (r * n1 + cols[0]) * isz
            length = span * isz
        else:
            if len(rows) * len(cols) > MAX_RANGES:
                raise ValueError("slice too sparse to read over SSH")
            c = np.arange(cols.start, cols.stop, cols.step, dtype=np.int64)
            offsets = (self._offset + (r[:, None] * n1 + c[None, :]) * isz).ravel()
            length = isz

        # Merge ranges with small gaps between them into fewer reads
        ends = offsets + length
        starts_group = np.concatenate([[True], offsets[1:] - ends[:-1] > GAP])
        group = np.cumsum(starts_group) - 1
        first = np.flatnonzero(starts_group)
        last = np.concatenate([first[1:] - 1, [len(offsets) - 1]])
        group_start, group_end = offsets[first], ends[last]
        buffer_at = np.concatenate([[0], np.cumsum(group_end - group_start)[:-1]])
        data = self._file.read_ranges(list(zip(group_start.tolist(), (group_end - group_start).tolist())))
        values = np.frombuffer(data, self._raw)
        at = (buffer_at[group] + offsets - group_start[group]) // isz

        if dense:
            raw = np.empty((len(rows), len(cols)), self._raw)
            for i, start in enumerate(at.tolist()):
                raw[i] = values[start : start + span : cols.step]
        else:
            raw = values[at].reshape(len(rows), len(cols))
        out = raw.astype(np.float32)
        if self._blank is not None:
            out[raw == self._blank] = np.nan
        if self._scale != 1.0 or self._zero != 0.0:
            out *= np.float32(self._scale)
            out += np.float32(self._zero)
        return out


class RemoteHDU:
    """The primary HDU of a RemoteFits: its header, its image (RemoteArray)."""

    def __init__(self, header, data):
        self.header = header
        self.data = data
        self.name = str(header.get("EXTNAME", "PRIMARY")).upper()


class RemoteHDUList(list):
    """Like astropy's HDUList for the viewer: indexing, iterating, close()."""

    def __init__(self, hdus, tables):
        super().__init__(hdus)
        self._tables = tables

    def close(self):
        if self._tables is not None:
            self._tables.close()

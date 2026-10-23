"""
Messages to the main window from SolarViewer's tools, which run as separate
processes (e.g. the Dynamic Spectrum Viewer), over a local socket. A tool
learns the socket's name from SOLARVIEWER_LINK in its environment.

Messages are JSON lines. From a tool: {"hello": "<tool>", "pid": n} as it
starts, keeping its socket open for what follows; {"show_time": "<UTC ISO
time>", "freq_mhz": f} shows, in the current tab, the image nearest that
time (of those, the one nearest frequency ``f``, if given): of its folder,
or of the frequency folder beside it nearest ``f`` (pipelines writing
final_images/FREQ_*MHz/); {"open_image": path} opens an image here. To a
tool that said hello: {"open": path, "raise": true}, {"theme": "light"},
{"close": true}.
"""

import itertools
import json
import os
import posixpath
import re
import time
from datetime import datetime

from PyQt5 import sip
from PyQt5.QtCore import QObject, QThread, QTimer, pyqtSignal
from PyQt5.QtNetwork import QLocalServer

ENV = "SOLARVIEWER_LINK"
_link_numbers = itertools.count(1)

# Images this close in time (s) count as taken together (e.g. one per frequency)
SAME_TIME = 1.0
_FREQ_IN_NAME = re.compile(r"(\d+(?:\.\d+)?)\s*MHz", re.IGNORECASE)
_FITS = (".fits", ".fts", ".fit", ".fits.gz", ".fz")


class _Times(QThread):
    """Observation times of a tab's files, read in the background."""

    done = pyqtSignal(object)
    progress = pyqtSignal(str)

    def __init__(self, work, parent=None):
        super().__init__(parent)
        self._work = work

    def run(self):
        try:
            self.done.emit(self._work(self.progress.emit))
        except Exception as e:
            self.done.emit(e)


def _name_frequency(path):
    """Frequency (MHz) in a file name, e.g. "..._FREQ_36.81MHz.fits", or None."""
    match = _FREQ_IN_NAME.search(os.path.basename(path))
    return float(match.group(1)) if match else None


def _header_frequency(header):
    """Frequency (MHz) of an image from its header, or None."""
    if header is None:
        return None
    for i in range(1, int(header.get("NAXIS", 0)) + 1):
        if "FREQ" in str(header.get(f"CTYPE{i}", "")).upper():
            try:
                return float(header[f"CRVAL{i}"]) / 1e6  # Hz
            except (KeyError, TypeError, ValueError):
                return None
    for key in ("FREQ", "RESTFRQ", "OBSFREQ"):
        if header.get(key):
            try:
                return float(header[key]) / 1e6
            except (TypeError, ValueError):
                pass
    return None


class ToolLink(QObject):
    """The main window's end: a local server the tools send messages to."""

    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        # One name per window: a process may make more than one
        self.name = f"solarviewer-{os.getpid()}-{next(_link_numbers)}"
        self._times = {}  # (remote, folder files) -> (times, frequencies)
        self._listings = {}  # (kind, remote, folder) -> listing
        self._request = 0  # the latest request (earlier ones are dropped)
        self._jobs = []
        self._remote_files = None
        self._tools = {}  # tool name -> its socket, from its hello on
        self._starting = {}  # tool name -> until when a launched one may say hello
        self._held = {}  # tool name -> message for it once it says hello
        QLocalServer.removeServer(self.name)  # left by a crashed run
        self.server = QLocalServer(self)
        if not self.server.listen(self.name):
            raise OSError(self.server.errorString())
        self.server.newConnection.connect(self._accept)

    def _accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.readyRead.connect(lambda socket=socket: self._read(socket))
            socket.disconnected.connect(lambda socket=socket: self._gone(socket))
            socket.disconnected.connect(socket.deleteLater)
            if socket.bytesAvailable():
                self._read(socket)  # sent before this end was listening

    def _gone(self, socket):
        for tool, held in list(self._tools.items()):
            if held is socket:
                del self._tools[tool]

    # ---------- Tools that stay connected ----------

    def note_launch(self, tool, path=None, seconds=20):
        """A ``tool`` was just started (opening ``path``): for ``seconds`` a
        message for it is held until it says hello, rather than starting
        another."""
        self._starting[tool] = (time.monotonic() + seconds, path)

    def has(self, tool):
        """Whether ``tool`` is connected (it said hello and is still there)."""
        socket = self._tools.get(tool)
        return socket is not None and not sip.isdeleted(socket) and socket.isValid()

    def send(self, tool, message):
        """Send ``message`` to ``tool``; False if it is not connected."""
        if not self.has(tool):
            return False
        socket = self._tools[tool]
        socket.write((json.dumps(message) + "\n").encode())
        return socket.waitForBytesWritten(1000)

    def deliver(self, tool, message):
        """Send ``message`` to ``tool``, or hold it for one just started;
        False if neither: the tool must be started (with the message)."""
        if self.send(tool, message):
            return True
        until, path = self._starting.get(tool, (0, None))
        if time.monotonic() < until:
            if message.get("open") != path:  # (that one it opens as it starts)
                self._held[tool] = message
            return True
        return False

    def broadcast(self, message):
        for tool in list(self._tools):
            self.send(tool, message)

    def close_tools(self):
        """Close the tools' windows (the main window is closing)."""
        self.broadcast({"close": True})

    def _hello(self, tool, socket):
        self._tools[tool] = socket
        self._starting.pop(tool, None)
        held = self._held.pop(tool, None)
        if held is not None:
            self.send(tool, held)

    def _read(self, socket):
        messages = []
        while not sip.isdeleted(socket) and socket.canReadLine():
            try:
                messages.append(json.loads(bytes(socket.readLine()).decode()))
            except ValueError:
                continue
        # Handled once back in the event loop: showing an image may fetch it
        # (with a progress dialog running events), during which this socket
        # is deleted when the tool disconnects
        for message in messages:
            if "hello" in message:
                self._hello(str(message["hello"]), socket)
            if "show_time" in message:
                QTimer.singleShot(
                    0,
                    lambda m=message: self.show_time(m["show_time"], m.get("freq_mhz")),
                )
            if "open_image" in message:
                QTimer.singleShot(
                    0, lambda m=message: self._open_image(str(m["open_image"]))
                )

    def _open_image(self, path):
        """Show an image a tool was given (it belongs here), in front."""
        window = self.main_window
        if os.path.exists(path):
            window.open_path(path)
        if window.isMinimized():
            window.showNormal()
        window.raise_()
        window.activateWindow()

    def _status(self, text, timeout=8000):
        self.main_window.statusBar().showMessage(text, timeout)

    # ---------- Showing the image nearest a time ----------

    def show_time(self, iso, freq_mhz=None):
        """Show the image nearest UTC ``iso`` (and of those, nearest
        ``freq_mhz``): of the current tab's folder, or, when the folders
        beside it are one per frequency (e.g. final_images/FREQ_36.81MHz/),
        of the one nearest ``freq_mhz``."""
        try:
            target = datetime.fromisoformat(iso)
        except ValueError:
            return
        tab = self.main_window.tab_widget.currentWidget()
        if self._use_server_folder(tab, iso, freq_mhz):
            return  # (asked again once the server's folder is listed)
        files = list(getattr(tab, "_file_list", None) or [])
        if not files:
            self._status(
                "To see the image at a spectrum's time, open an image from its folder first"
            )
            return
        remote = bool(getattr(tab, "_is_remote_mode", False))
        current = getattr(tab, "_file_list_index", -1)
        reference = files[current] if 0 <= current < len(files) else files[0]
        self._request += 1
        request = self._request
        self._status("Finding the image...", 0)
        job = _Times(
            lambda progress: self._candidates(remote, files, reference, freq_mhz, progress),
            self,
        )
        job.progress.connect(lambda text: self._status(text, 0))
        job.done.connect(
            lambda result: self._on_candidates(job, request, tab, files, result, target, freq_mhz)
        )
        self._jobs.append(job)
        job.start()

    def _use_server_folder(self, tab, iso, freq_mhz):
        """When the tab shows a cached copy of a server image but steps
        through the local cache (e.g. opened from its cached path), list the
        server's folder for it instead, and ask for ``iso`` again when that
        is done. True if it did."""
        window = self.main_window
        connection = getattr(window, "remote_connection", None)
        cache = getattr(window, "remote_cache", None)
        image = getattr(tab, "imagename", "") or ""
        if getattr(tab, "_is_remote_mode", False) or not image or cache is None:
            return False
        if connection is None or not connection.is_connected():
            return False
        source = cache.remote_source(image)
        if source is None or source[0] != connection.host:
            return False
        folder = posixpath.dirname(source[1])
        self._status(f"Listing {folder} on the server...", 0)
        tab._is_remote_mode = True
        tab._remote_base_dir = folder
        tab._file_list = []
        tab._populate_remote_file_list(window, folder, os.path.isdir(image), image)
        started = time.time()

        def check():
            if tab._file_list:
                self.show_time(iso, freq_mhz)
            elif time.time() - started < 60:
                QTimer.singleShot(200, check)
            else:
                self._status(f"Could not list {folder} on the server")

        QTimer.singleShot(200, check)
        return True

    # (These run in the background: listing and headers may be on a server.)

    def _candidates(self, remote, files, reference, freq_mhz, progress):
        """(folder, images, times, frequencies) to choose from: the tab's
        folder, or the frequency folder beside it nearest ``freq_mhz``."""
        path = posixpath if remote else os.path
        folder = path.dirname(reference)
        images = files
        if freq_mhz is not None and _name_frequency(path.basename(folder)) is not None:
            siblings = self._frequency_folders(remote, path.dirname(folder))
            if siblings:
                nearest = min(siblings, key=lambda item: abs(item[0] - freq_mhz))[1]
                if nearest != folder:
                    folder = nearest
                    images = self._images_in(remote, folder)
                    reference = images[len(images) // 2] if images else None
        if not images:
            return folder, [], [], []
        key = (remote, tuple(images))
        if key not in self._times:
            self._times[key] = self._read_times(remote, images, reference, progress)
        times, freqs = self._times[key]
        # Images without a frequency of their own have their folder's
        folder_freq = _name_frequency(path.basename(folder))
        freqs = [folder_freq if f is None else f for f in freqs]
        return folder, images, times, freqs

    def _frequency_folders(self, remote, parent):
        """[(MHz, folder)] of the folders in ``parent`` named for a frequency."""
        key = ("folders", remote, parent)
        if key not in self._listings:
            entries = self._listdir(remote, parent)
            self._listings[key] = [
                (_name_frequency(name), p)
                for name, p, is_dir in entries
                if is_dir and _name_frequency(name) is not None
            ]
        return self._listings[key]

    def _images_in(self, remote, folder):
        """The images (FITS files, CASA images) in ``folder``, sorted."""
        key = ("images", remote, folder)
        if key not in self._listings:
            self._listings[key] = sorted(
                p
                for name, p, is_dir in self._listdir(remote, folder)
                if name.lower().endswith(_FITS) or (is_dir and name.endswith(".image"))
            )
        return self._listings[key]

    def _listdir(self, remote, folder):
        """[(name, path, is_dir)] in local or server ``folder``."""
        if remote:
            return self._server_files().listdir(folder)
        try:
            return [(e.name, e.path, e.is_dir()) for e in os.scandir(folder)]
        except OSError:
            return []

    def _read_times(self, remote, files, reference, progress):
        """(times, frequencies) of ``files``: from their names when every
        name has a time that agrees with ``reference``'s header (reading
        thousands of headers over SSH takes minutes), else their headers."""
        from ..video.creator import observation_time

        names = [observation_time(None, p) for p in files]
        freqs = [_name_frequency(p) for p in files]
        if all(names):
            header = self._headers(remote, [reference]).get(reference)
            real = observation_time(header, None) if header is not None else None
            named = names[files.index(reference)]
            if real is None or abs((real - named).total_seconds()) <= 60:
                return names, freqs
        headers = self._headers(
            remote,
            files,
            lambda done, total: progress(f"Reading the times of the images: {done} of {total}"),
        )
        times = [observation_time(headers.get(p), p) for p in files]
        freqs = [f if f is not None else _header_frequency(headers.get(p)) for p, f in zip(files, freqs)]
        return times, freqs

    def _headers(self, remote, files, progress=None):
        """{path: FITS header or None} of local or server ``files``."""
        if remote:
            return self._server_files().headers(files, progress=progress)
        from astropy.io import fits

        from ..processing.fits_io import fits_image_path

        headers = {}
        for n, path in enumerate(files, 1):
            try:
                headers[path] = fits.getheader(fits_image_path(path))
            except Exception:
                headers[path] = None
            if progress and n % 50 == 0:
                progress(n, len(files))
        return headers

    def _server_files(self):
        """RemoteFiles of the main window's server (its headers are cached)."""
        from ..remote.remote_files import RemoteFiles

        connection = self.main_window.remote_connection
        if self._remote_files is None or self._remote_files.connection is not connection:
            self._remote_files = RemoteFiles(connection, self.main_window.remote_cache)
        return self._remote_files

    # ---------- Back here ----------

    def _on_candidates(self, job, request, tab, files, result, target, freq_mhz):
        self._jobs.remove(job)
        job.deleteLater()
        if request != self._request:
            return  # a later click came in
        if isinstance(result, Exception):
            self._status(f"Could not find the image: {result}")
            return
        if getattr(tab, "_file_list", None) != files:
            return  # the tab moved on meanwhile
        folder, images, times, freqs = result
        known = [(abs((t - target).total_seconds()), i) for i, t in enumerate(times) if t]
        if not known:
            self._status(f"The images in {folder} have no observation times")
            return
        gap, index = min(known)
        # Several images at that time (e.g. one per frequency): the one
        # nearest the frequency asked for
        together = [i for g, i in known if g - gap <= SAME_TIME]
        if freq_mhz is not None and len(together) > 1:
            with_freq = [i for i in together if freqs[i] is not None]
            if with_freq:
                index = min(with_freq, key=lambda i: abs(freqs[i] - freq_mhz))
        if images is files:
            if index != tab._file_list_index:
                tab._load_file_index(index)
        else:
            self._open_in_folder(tab, folder, images, index)
        name = os.path.basename(images[index])
        freq = f", {freqs[index]:.2f} MHz" if freqs[index] is not None else ""
        self._status(f"{name}: {gap:.0f} s from {target:%H:%M:%S}{freq}")
        window = self.main_window
        window.raise_()
        window.activateWindow()

    def _open_in_folder(self, tab, folder, images, index):
        """Load ``images[index]`` in ``tab``, which then steps through ``folder``."""
        window = self.main_window
        tab._file_list = list(images)
        if getattr(tab, "_is_remote_mode", False):
            tab._remote_base_dir = folder
            tab._remote_file_list = []
            tab._load_file_index(index)
            # The tab's own listing of the folder (for its filter and playlist)
            tab._populate_remote_file_list(
                window, folder, images[index].endswith(".image"), tab.imagename
            )
        else:
            tab._file_base_dir = folder
            tab._load_file_index(index)
            tab._scan_directory_files(rescan_only=True)

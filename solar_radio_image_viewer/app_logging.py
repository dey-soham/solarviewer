"""
The application's log.

Modules log with ``logging.getLogger(__name__)``. Once :func:`setup` has run
(at startup), the package's records at INFO and above are written to
``sys.stderr``, which the log window shows, and every line written to stdout
or stderr, logged or printed, is also kept in a log file:
~/.solarviewer/logs/solarviewer.log, rotated at 1 MB, with 3 old files kept.
"""

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FILE = Path.home() / ".solarviewer" / "logs" / "solarviewer.log"

_installed = False


class _LineFile:
    """Appends whole lines of output to the rotating log file, with the time.
    Of a line rewritten with carriage returns (a progress bar) only its final
    state is kept."""

    def __init__(self, path):
        self.handler = RotatingFileHandler(
            path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        self.handler.setFormatter(
            logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S")
        )
        self._partial = ""
        self._lock = threading.Lock()

    def write(self, text):
        with self._lock:
            lines = (self._partial + text).split("\n")
            self._partial = lines.pop()
        for line in lines:
            line = line.rsplit("\r", 1)[-1].rstrip()
            if line:
                self.handler.handle(logging.makeLogRecord({"msg": line}))


class _Tee:
    """A stream (stdout or stderr) that also copies what is written to it to
    the log file."""

    def __init__(self, stream, line_file):
        self._stream = stream
        self._line_file = line_file

    def write(self, text):
        try:
            self._line_file.write(text)
        except Exception:
            pass  # the log file must never break output
        return self._stream.write(text) if self._stream else len(text)

    def flush(self):
        if self._stream:
            self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


class _StderrHandler(logging.StreamHandler):
    """Writes to whatever ``sys.stderr`` is when a record comes (the log
    window replaces it after start-up)."""

    @property
    def stream(self):
        return sys.stderr

    @stream.setter
    def stream(self, value):
        pass


def setup():
    """Start the application's log (once): log records to stderr, and all
    output to the log file."""
    global _installed
    if _installed:
        return
    _installed = True

    package = logging.getLogger("solar_radio_image_viewer")
    package.setLevel(logging.INFO)
    handler = _StderrHandler()
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    package.addHandler(handler)
    package.propagate = False  # not twice if a library configures the root logger

    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        line_file = _LineFile(LOG_FILE)
    except OSError as e:
        package.warning(f"No log file ({e}); the log window still works")
        return
    sys.stdout = _Tee(sys.stdout, line_file)
    sys.stderr = _Tee(sys.stderr, line_file)

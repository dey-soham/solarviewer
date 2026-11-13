"""
Dynamic Spectra Dialog

Turns Measurement Sets (a folder of subband MS, one MS with all channels, or
several) into a dynamic spectrum FITS file, on this computer or on an SSH
server (directly or through Slurm).

The science and the reading of the MS are in ``make_dynamic_spectra.py``, a
single file run in a subprocess here (so casacore never meets Qt) or piped to
the server's container; the ssh, Slurm and container mechanics are in
``remote_dynspec.py``. This file is the interface only: the module-level
functions at the top are its logic (no Qt), the classes below are the window.
"""

import json
import math
import os
import posixpath
import queue
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta

from PyQt5.QtCore import QSettings, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from . import install_hint
from ..remote.ssh_manager import UnknownHostKey, trust_host_key
from ..ui.activity import Spinner, begin_activity
from ..ui.background import Cancelled, run_in_thread
from ..ui.styles import set_hand_cursor, set_tone
from ..ui.widgets import button_bar, dialog_layout, kicker, link_button, rule, segmented

ENGINE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "make_dynamic_spectra.py")

MAX_AUTO_COLUMNS = 2048  # the engine's cap on frequency columns when it averages
MANY_MS = 16  # from this many subbands, "auto" is one column per subband
# (both mirror make_dynamic_spectra.MAX_COLUMNS / MANY_SUBBANDS; a test compares them)
DIRECT_SERVER_CPUS = 8  # processes for a direct run on a shared login node
DEFAULT_CHUNK_MB = 128
SLURM_DEFAULT_CPUS = 8
_MJD_EPOCH = datetime(1858, 11, 17)
_TERMINAL = ("DONE", "FAILED", "CANCELLED")
SCAN_CLOCK_AFTER_S = 3  # a scan shows how long it has been going after this long
# How long "Opening the viewer…" lasts: the viewer gives no sign when it is up,
# and its window takes this long at worst
VIEWER_START_MS = 4000

# the run state of the window (one place decides what may be pressed)
IDLE, STARTING, LOCAL, REMOTE, DOWNLOADING = "idle", "starting", "local", "remote", "downloading"


# ----------------------------------------------------------------------------
# Logic (no Qt)
# ----------------------------------------------------------------------------


def parse_svds_line(line):
    """(kind, value) of one line of the engine's output.

    kind is INFO or SUMMARY (value a dict), PROGRESS (value (done, total)),
    WARN, ERROR or RESULT (value the text), or TEXT (any other line, value
    the line). A malformed protocol line is returned as TEXT.
    """
    line = line.rstrip("\r\n")
    if not line.startswith("SVDS "):
        return "TEXT", line
    kind, _, rest = line[5:].partition(" ")
    rest = rest.strip()
    try:
        if kind in ("INFO", "SUMMARY"):
            value = json.loads(rest)
            if isinstance(value, dict):
                return kind, value
        elif kind == "PROGRESS":
            done, total = rest.split()[:2]
            return kind, (int(float(done)), int(float(total)))
        elif kind in ("WARN", "ERROR", "RESULT"):
            return kind, rest
    except (ValueError, TypeError):
        pass
    return "TEXT", line


class RunLog:
    """What a run has said so far, fed line by line (the same for a local
    process and a server job)."""

    # casacore prints these from every process, harmlessly (an old leap-second table)
    # (also when casacore prefixes them with a timestamp and SEVERE/INFO/WARN, and
    # for the continuation lines that carry only the second half of the message)
    NOISE = re.compile(
        r"MeasTable::|Leap second table|TAI_UTC|Until the table is updated"
        r"|times and coordinates derived from UTC could be wrong"
        r"|\(see the CASA documentation or your system admin\)"
    )

    def __init__(self):
        self.done = 0
        self.total = 0
        self.info = {}
        self.warnings = []
        self.errors = []
        self.result = None
        self.noise_seen = False

    def feed(self, line):
        """Take in ``line``; the text to show in the log for it, or None
        (progress lines are not shown)."""
        kind, value = parse_svds_line(line)
        if kind == "PROGRESS":
            done, total = value
            if total >= self.total:
                self.total = total
                self.done = max(self.done, min(done, total))
            return None
        if kind == "INFO":
            self.info.update(value)
            return "Info: " + ", ".join(f"{k} = {_short(v)}" for k, v in value.items())
        if kind == "WARN":
            self.warnings.append(value)
            return "Warning: " + value
        if kind == "ERROR":
            self.errors.append(value)
            return "ERROR: " + value
        if kind == "RESULT":
            self.result = value
            return "Result: " + value
        if self.NOISE.search(value):
            if self.noise_seen:
                return None
            self.noise_seen = True
            return ("Note: casacore says its leap-second table is out of date; "
                    "this does not change the result (repeats are hidden).")
        return value if value.strip() else None


def run_stage_text(log):
    """What a run is doing where no percentage says it (``log`` a RunLog), or
    "": the rows take the bar to 100%, and the writing comes after them."""
    stage = log.info.get("stage")
    if stage == "writing":
        return "Writing the FITS file…"
    if stage == "plotting":
        return "Drawing the plot…"
    if log.total > 0:
        return "Finishing the spectrum…" if log.done >= log.total else ""
    if stage == "reading":
        return f"Opening {log.info.get('found', 'the')} Measurement Set(s)…"
    if stage in ("inputs", "plan"):
        return "Preparing the data…"
    return ""


def _short(value):
    text = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
    return text if len(text) <= 80 else text[:77] + "…"


class ProgressTracker:
    """Estimates the time left from progress reports (done of total)."""

    WINDOW = 45.0  # seconds of recent speed the estimate follows

    def __init__(self, start=None):
        self.start = time.monotonic() if start is None else start
        self.samples = []  # (time, done)
        self.total = 0

    def update(self, done, total, now=None):
        now = time.monotonic() if now is None else now
        self.total = total
        if not self.samples or done != self.samples[-1][1]:
            self.samples.append((now, done))
        cutoff = now - self.WINDOW
        while len(self.samples) > 2 and self.samples[1][0] < cutoff:
            self.samples.pop(0)

    def fraction(self):
        if self.total <= 0 or not self.samples:
            return None
        return min(1.0, self.samples[-1][1] / self.total)

    def eta(self, now=None):
        """Seconds left, or None while it can't be told (nothing done yet)."""
        if self.total <= 0 or not self.samples:
            return None
        done = self.samples[-1][1]
        if done <= 0:
            return None
        if done >= self.total:
            return 0.0
        now = time.monotonic() if now is None else now
        t0, d0 = self.samples[0]
        t1, d1 = self.samples[-1]
        if len(self.samples) < 2 and t1 - self.start < 3.0:
            return None  # too early (or a job picked up half way) to tell
        if d1 > d0 and t1 > t0:
            rate = (d1 - d0) / (t1 - t0)  # recent speed
        else:
            rate = done / max(t1 - self.start, 1e-6)  # since the start
        if rate <= 0:
            return None
        return max(0.0, (self.total - done) / rate - (now - t1))


def format_size(nbytes):
    """E.g. 2.9 GB (powers of 1000, as file managers show)."""
    value = float(nbytes or 0)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            return f"{value:.0f} {unit}" if unit in ("B", "kB") else f"{value:.1f} {unit}"
        value /= 1000.0


def format_duration(seconds):
    """E.g. 45 s, 3 min 20 s, 1 h 05 min."""
    seconds = int(round(max(0.0, seconds)))
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min {seconds % 60:02d} s" if seconds % 60 else f"{seconds // 60} min"
    hours, rest = divmod(seconds, 3600)
    return f"{hours} h {rest // 60:02d} min"


def format_clock(seconds):
    """mm:ss, or h:mm:ss."""
    seconds = int(max(0, seconds))
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60:02d}:{rest % 60:02d}"


def format_count(n):
    n = float(n or 0)
    for unit, size in (("billion", 1e9), ("million", 1e6)):
        if n >= size:
            return f"{n / size:.1f} {unit}"
    return f"{n:,.0f}"


def mjd_seconds_to_text(seconds):
    """UTC as 2023-03-10 10:00:00 for MJD seconds."""
    try:
        return (_MJD_EPOCH + timedelta(seconds=float(seconds))).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, ValueError, TypeError):
        return "?"


def parse_time_text(text):
    """MJD seconds for an ISO time ("2023-03-10T10:00:00", a space, a date
    alone or a trailing Z are fine) or a number (MJD seconds; below 1e6 MJD
    days, as the engine reads it); None if blank.
    Raises ValueError with a message for the user."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        value = None
    if value is not None:
        if not math.isfinite(value) or value < 0:
            raise ValueError("not a time; use e.g. 2023-03-10T10:00:00 (UTC) or MJD seconds")
        return value * 86400.0 if value < 1e6 else value  # MJD days or seconds, as the engine reads it
    clean = text.rstrip("Zz").replace(" ", "T")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return (datetime.strptime(clean, fmt) - _MJD_EPOCH).total_seconds()
        except ValueError:
            continue
    raise ValueError("not a time; use e.g. 2023-03-10T10:00:00 (UTC) or MJD seconds")


def split_paths(text):
    """The paths in the Data field: separated by ";" or new lines, quotes and
    blanks dropped."""
    paths = []
    for part in re.split(r"[;\n]", text or ""):
        part = part.strip().strip("'\"").strip()
        if part:
            paths.append(part)
    return paths


def is_ms_name(path):
    """Whether ``path`` is named like a Measurement Set (.ms in any case)."""
    return path.rstrip("/\\").lower().endswith(".ms")


def default_output_path(paths, remote=False, writable=True, home=None):
    """The output FITS path offered for ``paths``: next to the data, or in
    ``home`` when the data's folder isn't writable."""
    mod = posixpath if remote else os.path
    if not paths:
        return ""
    first = paths[0].rstrip("/\\")
    if is_ms_name(first) or len(paths) > 1:
        folder = mod.dirname(first)
        stem = mod.basename(first)
        if is_ms_name(first):
            stem = stem[:-3]
        if len(paths) > 1:
            stem = mod.basename(folder) or stem
    else:
        folder, stem = first, mod.basename(first)
    stem = stem or "dynamic_spectrum"
    if not writable and home:
        folder = home
    return mod.join(folder or home or ".", f"{stem}_dynspec.fits")


def default_download_dir():
    """Where a downloaded result goes unless told otherwise: ~/Downloads if it exists."""
    home = os.path.expanduser("~")
    folder = os.path.join(home, "Downloads")
    return folder if os.path.isdir(folder) else home


def plot_path_for(fits_path, remote=False):
    """The PNG beside ``fits_path`` (its extension replaced, whatever its case)."""
    mod = posixpath if remote else os.path
    return mod.splitext(fits_path)[0] + ".png"


def _first(d, *names, default=None):
    for name in names:
        if name in d and d[name] is not None:
            return d[name]
    return default


def _range(d, lo_names, hi_names, pair_names):
    """(lo, hi) from separate keys or a two-item list, or (None, None)."""
    pair = _first(d, *pair_names)
    if isinstance(pair, (list, tuple)) and len(pair) == 2:
        return pair[0], pair[1]
    return _first(d, *lo_names), _first(d, *hi_names)


def normalize_summary(raw):
    """The engine's ``--list`` summary as one dict the window reads:

    count, items (name, path, nrow, nchan, nspw, fmin, fmax in MHz, tmin,
    tmax in MJD seconds, bytes), nchan, nspw, fmin, fmax, tmin, tmax, bytes,
    rows, ncorr. Missing facts are None or 0.
    """
    raw = raw or {}
    entries = _first(raw, "ms", "ms_list", "items", "files", default=[]) or []
    items = []
    for e in entries:
        fmin, fmax = _range(
            e, ("fmin_mhz", "freq_min_mhz", "fmin"), ("fmax_mhz", "freq_max_mhz", "fmax"),
            ("freq_range_mhz", "freq_mhz", "freq_range"),
        )
        tmin, tmax = _range(
            e, ("tmin_mjds", "tmin", "time_min", "tstart"), ("tmax_mjds", "tmax", "time_max", "tend"),
            ("time_range_mjd_s", "time_range", "time_mjd_s"),
        )
        items.append(
            {
                "name": _first(e, "name", default=os.path.basename(str(e.get("path", "")))),
                "path": _first(e, "path", default=""),
                "nrow": int(_first(e, "nrow", "nrows", "rows", default=0) or 0),
                "nchan": int(_first(e, "nchan", "nchans", "channels", default=0) or 0),
                "nspw": int(_first(e, "nspw", default=1) or 1),
                "fmin": fmin,
                "fmax": fmax,
                "tmin": tmin,
                "tmax": tmax,
                "bytes": int(_first(e, "bytes", "size", default=0) or 0),
            }
        )
    fmin, fmax = _range(
        raw, ("fmin_mhz", "freq_min_mhz", "fmin"), ("fmax_mhz", "freq_max_mhz", "fmax"),
        ("freq_range_mhz", "freq_mhz", "freq_range"),
    )
    tmin, tmax = _range(
        raw, ("tmin_mjds", "tmin", "time_min"), ("tmax_mjds", "tmax", "time_max"),
        ("time_range_mjd_s", "time_range", "time_mjd_s"),
    )
    if fmin is None:
        lows = [i["fmin"] for i in items if i["fmin"] is not None]
        highs = [i["fmax"] for i in items if i["fmax"] is not None]
        fmin, fmax = (min(lows), max(highs)) if lows and highs else (None, None)
    if tmin is None:
        lows = [i["tmin"] for i in items if i["tmin"] is not None]
        highs = [i["tmax"] for i in items if i["tmax"] is not None]
        tmin, tmax = (min(lows), max(highs)) if lows and highs else (None, None)
    return {
        "count": int(_first(raw, "count", "n_ms", "nms", default=len(items)) or len(items)),
        "items": items,
        "nchan": int(_first(raw, "nchan", "total_channels", "nchan_total",
                            default=sum(i["nchan"] for i in items)) or 0),
        "nspw": int(_first(raw, "nspw", "total_spw", default=sum(i["nspw"] for i in items)) or 0),
        "fmin": fmin,
        "fmax": fmax,
        "tmin": tmin,
        "tmax": tmax,
        "bytes": int(_first(raw, "bytes", "total_bytes", default=sum(i["bytes"] for i in items)) or 0),
        "rows": int(_first(raw, "est_rows", "rows", "nrow", "total_rows",
                           default=sum(i["nrow"] for i in items)) or 0),
        "ncorr": int(_first(raw, "ncorr", "n_corr", "ncorrelations", default=0) or 0),
        "failed": [
            f"{os.path.basename(str(f.get('path', f)))}: {f.get('error', '')}" if isinstance(f, dict) else str(f)
            for f in (raw.get("failed") or [])
        ],
    }


def effective_chanavg(freqres, spin_value):
    """What the window sends as --chanavg: the number above 1 when averaging is
    chosen; 1 for "Every channel" (that switches the engine's column cap off);
    otherwise None (auto: the engine averages only when there are very many)."""
    if spin_value and int(spin_value) > 1:
        return int(spin_value)
    return 1 if freqres == "channel" else None


def _plan_units(summary):
    """Channels of each distinct unit (MS or spectral window) as the engine
    sees them: inputs at the same centre frequency (a run cut in time) share
    their columns, so count once."""
    seen = {}
    for n, item in enumerate(summary["items"]):
        spw = max(1, item.get("nspw") or 1)
        if item.get("fmin") is not None and item.get("fmax") is not None:
            key = int(round((item["fmin"] + item["fmax"]) / 2.0 * 1e3))  # centre, kHz
        else:
            key = ("input", n)
        if key not in seen:
            seen[key] = (item.get("nchan") or 0, spw)
    units = []
    for nchan, spw in seen.values():
        base, extra = divmod(nchan, spw)
        units += [base + (1 if i < extra else 0) for i in range(spw)]
    if not units:
        n = max(summary["nspw"], summary["count"], 1)
        units = [max(1, (summary["nchan"] or n) // n)] * n
    return units


def auto_freq_plan(summary, freqres="auto", chanavg=None):
    """(columns, text): how many frequency columns the output will have for
    the chosen resolution, and a sentence saying so (as the engine decides:
    chanavg None or 0 is auto, a number averages exactly that many channels)."""
    units = _plan_units(summary)
    if freqres == "subband" or (freqres == "auto" and len(units) >= MANY_MS):
        text = "one column per subband" if summary["count"] > 1 else "one column per spectral window"
        return len(units), f"{len(units)} columns, {text}"

    def columns(factor):
        return sum(-(-n // factor) for n in units)

    if chanavg and int(chanavg) >= 1:
        factor = int(chanavg)
    else:
        factor = 1
        while columns(factor) > MAX_AUTO_COLUMNS:
            factor = max(factor + 1, -(-factor * columns(factor) // MAX_AUTO_COLUMNS))
    cols = columns(factor)
    if factor == 1:
        return cols, f"{cols} columns, every channel kept"
    return cols, f"{cols} columns, {factor} channels averaged into one"


def describe_summary(summary, freqres="auto", chanavg=None):
    """[(label, text)] rows for the data card."""
    count = summary["count"]
    if count == 1:
        item = summary["items"][0] if summary["items"] else {}
        spw = item.get("nspw", summary["nspw"])
        layout = f"One MS with {summary['nchan'] or item.get('nchan', 0)} channels"
        if spw > 1:
            layout += f" in {spw} spectral windows"
        layout += " (all frequencies in one file)"
    else:
        layout = f"{count} Measurement Sets (one frequency band each)"
        if summary["items"]:
            nch = {i["nchan"] for i in summary["items"]}
            if len(nch) == 1:
                layout += f", {nch.pop()} channels each"
    rows = [("Layout", layout)]
    if summary["fmin"] is not None and summary["fmax"] is not None:
        rows.append(("Frequency", f"{summary['fmin']:.3f} to {summary['fmax']:.3f} MHz"))
    if summary["tmin"] is not None and summary["tmax"] is not None:
        rows.append(
            (
                "Time",
                f"{mjd_seconds_to_text(summary['tmin'])} to "
                f"{mjd_seconds_to_text(summary['tmax'])[11:]} UTC, "
                f"{format_duration(summary['tmax'] - summary['tmin'])}",
            )
        )
    size = format_size(summary["bytes"]) if summary["bytes"] else "?"
    if summary["rows"]:
        size += f", {format_count(summary['rows'])} rows"
    if summary["ncorr"]:
        size += f", {summary['ncorr']} correlations"
    rows.append(("Size", size))
    rows.append(("Spectrum", auto_freq_plan(summary, freqres, chanavg)[1]))
    if summary.get("failed"):
        n = len(summary["failed"])
        rows.append(("Skipped", f"{n} could not be read: {summary['failed'][0]}" + (" …" if n > 1 else "")))
    return rows


def _path_in(text):
    """The path an error message names (the last quoted one, or one after a colon)."""
    quoted = re.findall(r"'(/[^']+|[^']*\.[^']*)'", text)
    if quoted:
        return re.sub(r"\.part[\w.-]*$", "", quoted[-1])
    m = re.search(r":\s*(/\S.*)$", text.strip().splitlines()[-1] if text.strip() else "")
    return m.group(1).strip() if m else ""


def _is_write_error(low):
    return ".part" in low or "writing" in low or "write" in low


def _casacore_missing(low):
    """Whether the text says python-casacore itself is missing or unimportable
    (not just any message that mentions casacore)."""
    if "casacore" not in low:
        return False
    if "no module named" in low or "modulenotfounderror" in low:
        return True
    if "importerror" in low or "cannot import" in low or "can't import" in low \
            or "could not import" in low or "failed to import" in low:
        return True
    if "not installed" in low or "not available" in low or "is missing" in low:
        return True
    return False


def _permission_text(text):
    path = _path_in(text)
    where = f": {path}" if path else "."
    if _is_write_error(text.lower()):
        return f"Permission denied: you are not allowed to write there{where}"
    return f"Permission denied: you are not allowed to read that location{where}"


def friendly_scan_error(text, remote=False):
    """A message for the user from whatever the scan said."""
    text = (text or "").strip() or "Could not read the data."
    low = text.lower()
    if _casacore_missing(low):
        return (
            "python-casacore is not installed for this Python, so Measurement Sets "
            f"cannot be read on this computer. Install it ({install_hint(['casacore'])}), "
            "or switch to Server above to read them there."
        )
    if "permission denied" in low:
        return _permission_text(text)
    if "no ms" in low or "no measurement" in low or "no readable" in low:
        return (
            "No Measurement Sets found there. Pick a folder holding MS "
            "(names ending .ms or .MS, any case) or an MS itself."
        )
    if "no such file" in low or "does not exist" in low or "not found" in low:
        path = _path_in(text)
        return f"That path does not exist{' on the server' if remote else ''}" + (f": {path}" if path else ".")
    return _dedupe_error(text)


def _dedupe_error(text):
    """'Authentication failed: Authentication failed.' -> 'Authentication failed.'"""
    head, sep, tail = text.partition(": ")
    if sep and head.strip().rstrip(".").lower() == tail.strip().rstrip(".").lower():
        return tail.strip()
    return text


def friendly_job_error(text):
    """A message for a run that failed (not for a scan): the engine's own
    words, with only the well-known cases made plainer."""
    text = (text or "").strip() or "see the log"
    low = text.lower()
    if _casacore_missing(low) or "permission denied" in low:
        return friendly_scan_error(text)
    return text


_FAILURE_HINTS = (
    ("TIMEOUT", "The Slurm time limit was reached. Raise the time limit (Advanced) and run again."),
    ("OUT_OF_MEMORY", "The job ran out of memory. Raise the Slurm memory, or lower the CPUs or the read block size (Advanced)."),
    ("OUT OF MEMORY", "The job ran out of memory. Raise the Slurm memory, or lower the CPUs or the read block size (Advanced)."),
    ("NODE_FAIL", "The compute node failed; running again usually works."),
    ("KILLED BY SIGNAL 9", "The process was killed, most likely for using too much memory. Lower the CPUs or the read block size, or ask Slurm for more memory."),
    ("SIGKILL", "The process was killed, most likely for using too much memory. Lower the CPUs or the read block size, or ask Slurm for more memory."),
)


def failure_hint(reason):
    """What to try after a failure with ``reason`` (the server's words), or ""."""
    up = (reason or "").upper()
    for key, hint in _FAILURE_HINTS:
        if key in up:
            return hint
    return ""


def suggest_time_limit(est_seconds):
    """A Slurm time limit "HH:MM:SS" with room over the estimate."""
    secs = max(3600.0, 3.0 * (est_seconds or 0))
    secs = min(secs, 48 * 3600.0)
    half_hours = int(-(-secs // 1800))
    hours, half = divmod(half_hours, 2)
    return f"{hours:02d}:{30 * half:02d}:00"


_TIME_LIMIT = re.compile(r"^(?:(\d+)-)?(\d{1,3}):([0-5]\d):([0-5]\d)$")


def validate_params(p, summary=None):
    """[(field, message, level)] for the settings ``p`` (dict as built by the
    window); level "error" blocks the run, "warning" does not. ``summary`` is
    the normalised scan, if any, to check the ranges against the data."""
    out = []

    def err(field, msg):
        out.append((field, msg, "error"))

    def warn(field, msg):
        out.append((field, msg, "warning"))

    remote = p.get("remote", False)
    paths = p.get("data") or []
    if not paths:
        err("data", "Choose the data: a folder of Measurement Sets or an MS.")
    for path in paths:
        if remote and not path.startswith("/"):
            err("data", f"On the server, give the full path starting with / ({path}).")
            break
        if "\0" in path or "\n" in path:
            err("data", "The path has characters that can't be used.")
            break
    output = (p.get("output") or "").strip()
    if not output:
        err("output", "Choose where to save the FITS file.")
    elif "\0" in output or "\n" in output:
        err("output", "The output path has characters that can't be used.")
    elif remote and not output.startswith("/"):
        err("output", "On the server, give the full path starting with /.")
    elif not output.lower().endswith((".fits", ".fit", ".fts")):
        warn("output", "The output name does not end in .fits; the viewer may not list it.")

    if not p.get("binwidth") or p["binwidth"] <= 0:
        err("binwidth", "The time bin width must be above zero.")
    uvmin, uvmax = p.get("uvmin", 0.0), p.get("uvmax", 0.0)
    if uvmin < 0 or uvmax < 0:
        err("uvmin", "The UV range can't be negative.")
    elif uvmax > 0 and uvmax <= uvmin:  # 0 means no limit on that side
        err("uvmax", "UV max must be larger than UV min (or 0 for no upper limit).")

    fstart, fend = p.get("startfreq"), p.get("endfreq")
    if fstart is not None and fend is not None and fend <= fstart:
        err("startfreq", "The end frequency must be above the start frequency.")
    elif summary and summary.get("fmin") is not None:
        lo, hi = summary["fmin"], summary["fmax"]
        if (fstart is not None and fstart > hi) or (fend is not None and fend < lo):
            err("startfreq", f"The data covers {lo:.3f} to {hi:.3f} MHz; that range holds none of it.")

    tstart = tend = None
    for field, key in (("tstart", "tstart"), ("tend", "tend")):
        try:
            value = parse_time_text(p.get(key))
        except ValueError as e:
            err(field, f"Time range: {e}")
            continue
        if field == "tstart":
            tstart = value
        else:
            tend = value
    if tstart is not None and tend is not None and tend <= tstart:
        err("tstart", "The end time must be after the start time.")
    elif summary and summary.get("tmin") is not None:
        if (tstart is not None and tstart > summary["tmax"]) or (
            tend is not None and tend < summary["tmin"]
        ):
            err(
                "tstart",
                f"The data runs from {mjd_seconds_to_text(summary['tmin'])} to "
                f"{mjd_seconds_to_text(summary['tmax'])} UTC; that range holds none of it.",
            )

    if p.get("chanavg") is not None and p["chanavg"] < 1:
        err("chanavg", "Channel averaging must be 1 or more.")
    if p.get("chunkmb", DEFAULT_CHUNK_MB) < 8:
        err("chunkmb", "The chunk size must be at least 8 MB.")
    if p.get("mode") == "slurm":
        m = _TIME_LIMIT.match((p.get("time_limit") or "").strip())
        if not m:
            err("time_limit", "Slurm time limit: use hours:minutes:seconds, e.g. 04:00:00.")
        elif not any(int(g or 0) for g in m.groups()):
            err("time_limit", "The Slurm time limit must be more than zero.")
        if p.get("ncpu", 0) < 1:
            err("ncpu", "Choose how many CPUs to request from Slurm.")
    if p.get("download") and remote and not (p.get("download_dir") or "").strip():
        err("download_dir", "Choose the folder to download the result to.")
    return out


def check_local_output(output):
    """(field, message) when the output file can't be written on this
    computer (folder missing or read-only, name too long), else None. Checked
    before a long run, not after it."""
    folder = os.path.dirname(output) or "."
    if len(os.path.basename(output)) > 255:
        return "output", "The output file name is too long."
    if not os.path.isdir(folder):
        return "output", f"The output folder does not exist: {folder}"
    if not os.access(folder, os.W_OK | os.X_OK):
        return "output", f"You are not allowed to write to the output folder: {folder}"
    return None


def build_engine_args(params):
    """The engine's command line (after the script) for ``params``, the same
    keys as remote_dynspec.submit: indir or ms, outfits, binwidth, ncpu, uvmin,
    uvmax (0 = no limit), startfreq, endfreq, tstart, tend, pol, freqres,
    chanavg (None = auto), chunkmb, saveplot, plotfile, overwrite."""
    args = []
    if params.get("ms"):
        args += ["--ms"] + [str(m) for m in params["ms"]]
    else:
        args += ["--indir", str(params["indir"])]
    args += ["--outfits", str(params["outfits"])]
    for key in ("binwidth", "ncpu", "uvmin", "uvmax"):
        if params.get(key) is not None:
            args += [f"--{key}", str(params[key])]
    for key in ("startfreq", "endfreq", "tstart", "tend", "pol", "freqres"):
        if params.get(key) not in (None, ""):
            args += [f"--{key}", str(params[key])]
    if (params.get("chanavg") or 0) >= 1:  # None / 0 = auto: the engine's own column cap
        args += ["--chanavg", str(int(params["chanavg"]))]
    if params.get("chunkmb") and int(params["chunkmb"]) != DEFAULT_CHUNK_MB:
        args += ["--chunkmb", str(int(params["chunkmb"]))]
    if params.get("saveplot"):
        args.append("--saveplot")
        if params.get("plotfile"):
            args += ["--plotfile", str(params["plotfile"])]
    if params.get("overwrite"):
        args.append("--overwrite")
    return args


def local_command(params, python=None, script=None):
    """argv to run the engine on this computer."""
    return [python or sys.executable, script or ENGINE_PATH] + build_engine_args(params)


def local_cpu_count():
    try:
        return len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return os.cpu_count() or 1


def _output_lines(proc, timeout):
    """The lines ``proc`` writes, until it has ended. A child that outlives it
    with the pipe open, or a worker stuck on a dead disk, holds up a read but
    not this: the read is on a thread of its own. Raises TimeoutExpired."""
    deadline = time.monotonic() + timeout
    lines = queue.Queue()

    def read():
        try:
            for line in proc.stdout:
                lines.put(line)
        finally:
            proc.stdout.close()
            lines.put(None)

    threading.Thread(target=read, daemon=True).start()
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            raise subprocess.TimeoutExpired(proc.args, timeout)
        ended = proc.poll() is not None
        try:
            line = lines.get(timeout=min(left, 1 if ended else 0.1))
        except queue.Empty:
            if ended:
                return
            continue
        if line is None:
            proc.wait(max(deadline - time.monotonic(), 0))
            return
        yield line


def local_scan(paths, python=None, script=None, timeout=180, on_start=None, on_progress=None):
    """The engine's ``--list`` summary (raw dict) for local ``paths``.
    Raises RuntimeError with a message for the user.

    ``on_start(process)`` is given the process, to stop it when its answer is
    no longer wanted; ``on_progress(done, total)`` follows the MS read."""
    for path in paths:
        if not os.path.exists(path):
            raise RuntimeError(f"That path does not exist: {path}")
        if not os.access(path, os.R_OK):
            raise RuntimeError(f"Permission denied: {path}")
    args = ["--indir", paths[0]] if len(paths) == 1 else ["--ms"] + list(paths)
    cmd = [python or sys.executable, script or ENGINE_PATH, "--list"] + args
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
        bufsize=1, cwd=_safe_cwd(), env=dict(os.environ, PYTHONUNBUFFERED="1"),
    )
    if on_start is not None:
        on_start(proc)
    summary = None
    problems = []
    other = []
    try:
        for line in _output_lines(proc, timeout):
            kind, value = parse_svds_line(line)
            if kind == "SUMMARY":
                summary = value
            elif kind == "ERROR":
                problems.append(value)
            elif kind == "PROGRESS":
                if on_progress is not None:
                    on_progress(*value)
            elif kind == "TEXT" and value.strip():
                other.append(value.strip())
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(1)
        except subprocess.TimeoutExpired:
            pass  # it cannot die until the disk answers: nothing to wait for
        raise RuntimeError("Reading the data took too long; is the disk reachable?")
    if summary is None:
        raise RuntimeError(friendly_scan_error(" ".join(problems) or " ".join(other[-3:])))
    return summary


def _safe_cwd():
    cwd = os.getcwd() if os.access(os.getcwd(), os.W_OK) else os.path.expanduser("~")
    return cwd


def viewer_command(theme, path):
    """argv that opens ``path`` in the Dynamic Spectrum Viewer."""
    return [
        sys.executable, "-m", "solar_radio_image_viewer.pipeline.launch",
        "dynamic_spectrum_viewer", theme, path,
    ]


def viewer_env():
    """The environment for the viewer process: as the main window launches
    tools (no Qt plugin paths from other packages), the package importable."""
    env = os.environ.copy()
    for key in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        env.pop(key, None)
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    parts = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
    if root not in parts:
        env["PYTHONPATH"] = os.pathsep.join([root] + parts)
    return env


# ----------------------------------------------------------------------------
# Background work
# ----------------------------------------------------------------------------

_ALIVE = set()  # running threads, kept referenced until they end


class _Task(QThread):
    """``fn()`` on a thread; ``result`` carries its value, ``failed`` its error."""

    result = pyqtSignal(object)
    failed = pyqtSignal(object)
    note = pyqtSignal(object)  # for ``fn`` to say how it is going

    def __init__(self, fn):
        super().__init__()
        self._fn = fn
        _ALIVE.add(self)
        self.finished.connect(lambda: _ALIVE.discard(self))

    def run(self):
        try:
            value = self._fn()
        except BaseException as e:  # reported, never lost with the thread
            self.failed.emit(e)
        else:
            self.result.emit(value)


def _close_channel(stream):
    """Close the ssh channel behind a paramiko stream (best effort)."""
    try:
        stream.channel.close()
    except Exception:
        pass


class _ServerScan(QThread):
    """Finds the software on the server, reads the data's summary and where
    the output can go; each part is reported as ``stage(name, value)`` as it
    is known, so the window fills in step by step."""

    stage = pyqtSignal(str, object)

    def __init__(self, remote, conn, engine_source, paths, runtime, hints, want_partitions,
                 current=None):
        super().__init__()
        self.remote, self.conn, self.engine_source = remote, conn, engine_source
        self.paths, self.runtime, self.hints = paths, runtime, hints
        self.want_partitions = want_partitions
        self.current = current or (lambda: True)  # False once the window has moved on
        _ALIVE.add(self)
        self.finished.connect(lambda: _ALIVE.discard(self))

    def run(self):
        remote, conn = self.remote, self.conn
        runtime = self.runtime
        if runtime is None:
            probe = self.paths[0] if len(self.paths) == 1 and is_ms_name(self.paths[0]) else None
            try:
                runtime = remote.detect_runtime(
                    conn, probe_ms=probe, hints=tuple(self.hints),
                    on_candidate=lambda *c: self.stage.emit("candidate", c),
                )
            except Exception as e:
                self.stage.emit("runtime_error", e)
                return
        self.stage.emit("runtime", runtime)
        if not self.current():
            return
        try:
            raw = remote.inspect_remote(
                conn, runtime, self.engine_source,
                self.paths[0] if len(self.paths) == 1 else list(self.paths),
            )
        except Exception as e:
            self.stage.emit("summary_error", e)
            return
        self.stage.emit("summary", raw)
        if not self.current():
            return
        try:
            first = self.paths[0].rstrip("/")
            folder = posixpath.dirname(first) if is_ms_name(first) or len(self.paths) > 1 else first
            home = ""
            try:
                home = conn.get_home_directory() or ""
            except Exception:
                pass
            cmd = f"if test -w {shlex.quote(folder)}; then printf %s yes; else printf %s no; fi"
            _, stdout, _ = conn.exec_command(cmd, timeout=20)
            try:
                writable = stdout.read().decode(errors="replace").strip() == "yes"
            finally:
                _close_channel(stdout)
            self.stage.emit("outdir", {"writable": writable, "home": home})
        except Exception:
            pass
        if self.want_partitions and self.current():
            try:
                self.stage.emit("partitions", remote.list_partitions(conn))
            except Exception:
                self.stage.emit("partitions", [])


class LocalRunner(QThread):
    """Runs the engine in a subprocess of its own (its own process group, so
    cancelling stops the worker processes too) and reports its lines."""

    line = pyqtSignal(str)
    exited = pyqtSignal(int, bool)  # return code, cancelled

    def __init__(self, cmd):
        super().__init__()
        self.cmd = cmd
        self._proc = None
        self._cancelled = False
        self._lock = threading.Lock()
        _ALIVE.add(self)
        self.finished.connect(lambda: _ALIVE.discard(self))

    def run(self):
        try:
            proc = subprocess.Popen(
                self.cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                errors="replace", bufsize=1, cwd=_safe_cwd(), start_new_session=(os.name != "nt"),
                env=dict(os.environ, PYTHONUNBUFFERED="1"),
            )
        except OSError as e:
            self.line.emit(f"SVDS ERROR could not start: {e}")
            self.exited.emit(1, False)
            return
        with self._lock:
            self._proc = proc
            stop_now = self._cancelled  # Stop was pressed before the process existed
        if stop_now:
            self._terminate(proc)
        for text in iter(proc.stdout.readline, ""):
            self.line.emit(text.rstrip())
        proc.wait()
        self.exited.emit(proc.returncode, self._cancelled)

    def cancel(self):
        """Stop the process and everything it started (also if it is not
        started yet: it is stopped as soon as it is)."""
        with self._lock:
            self._cancelled = True
            proc = self._proc
        if proc is not None:
            self._terminate(proc)

    def _terminate(self, proc):
        if proc.poll() is not None:
            return
        self._signal(proc, signal.SIGTERM if os.name != "nt" else None)
        timer = threading.Timer(4.0, self._kill)
        timer.daemon = True
        timer.start()

    def _kill(self):
        proc = self._proc
        if proc is not None and proc.poll() is None:
            self._signal(proc, getattr(signal, "SIGKILL", None))

    @staticmethod
    def _signal(proc, sig):
        try:
            if os.name == "nt" or sig is None:
                proc.terminate()
            else:
                os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass


class _Fetch(QThread):
    """Downloads a finished job's result."""

    progress = pyqtSignal(object, object)
    result = pyqtSignal(object)
    failed = pyqtSignal(object)

    def __init__(self, remote, conn, handle, local_dir, include_plot):
        super().__init__()
        self.remote, self.conn, self.handle = remote, conn, handle
        self.local_dir, self.include_plot = local_dir, include_plot
        self.stop = False
        _ALIVE.add(self)
        self.finished.connect(lambda: _ALIVE.discard(self))

    def run(self):
        try:
            paths = self.remote.fetch_result(
                self.conn, self.handle, self.local_dir, include_plot=self.include_plot,
                progress=lambda *a: self.progress.emit(a[0], a[1] if len(a) > 1 else 0),
                cancelled=lambda: self.stop,
            )
        except BaseException as e:
            self.failed.emit(e)
        else:
            self.result.emit(paths)


# ----------------------------------------------------------------------------
# Window
# ----------------------------------------------------------------------------

ORG, APP = "SolarViewer", "DynamicSpectraCreator"


def _settings():
    try:
        return QSettings(ORG, APP)
    except Exception:
        return None


def _get(key, default=None, kind=None):
    s = _settings()
    try:
        if s is not None and s.contains(key):
            return s.value(key, default, type=kind) if kind else s.value(key, default)
    except Exception:
        pass
    return default


def _job_id_of(text):
    try:
        return json.loads(text).get("job_id")
    except (ValueError, AttributeError, TypeError):
        return None


def _put(key, value):
    s = _settings()
    try:
        if s is not None:
            s.setValue(key, value)
    except Exception:
        pass


class DynamicSpectraDialog(QDialog):
    """Create a dynamic spectrum from Measurement Sets, here or on a server."""

    def __init__(self, parent=None, theme="dark"):
        super().__init__(parent)
        self.theme = theme
        self._remote_mod = None  # remote_dynspec, imported when first needed
        self._remote_error = ""
        self._conn = None
        self._conn_connecting = False
        self._runtime = None
        self._partitions = None
        self._summary = None
        self._scan_error = ""
        self._scanning = False
        self._scan_token = 0
        self._server_home = ""
        self._engine_source = None
        self._output_touched = False
        self._recommendation = None  # (mode, reason, est_seconds)
        self._mode_touched = False
        self._runner = None
        self._handle = None
        self._offset = 0
        self._poll_busy = False
        self._unknown_polls = 0
        self._unknown_reason = ""  # why the job's state can't be read right now (not a failure)
        self._fetch = None
        self._runlog = RunLog()
        self._tracker = None
        self._started = 0.0
        self._state = IDLE  # IDLE, STARTING, LOCAL, REMOTE, DOWNLOADING: the one run state
        self._run_token = 0  # bumped by every start and every cancel; callbacks of an old one are dropped
        self._closed = False
        self._closing = False
        self._scan_busy = False  # a scan thread is running
        self._scan_again = False  # ... and the data changed meanwhile
        self._partitions_asked = False
        self._outdir_redirected = False
        self._time_touched = False
        self._slurm_state = ""
        self._slurm_reason = ""
        self._offline_since = None
        self._offline_why = ""
        self._note = None  # (text, tone) an action wants shown under the progress line
        self._seed_elapsed = False
        self._submitting = False
        self._stopping = False
        self._stop_requested = False
        self._stop_unconfirmed = None  # why Stop could not be confirmed (str), else None
        self._unknown_since = None
        self._unknown_node = None  # (job node, this node) while the state is unreadable from another node
        self._gave_up = False  # checking was stopped: the job cannot be found
        self._last_progress = time.monotonic()
        self._cancelled_jobs = set()  # jobs the submit thread has already cancelled
        self._remote_result = None
        self._last_container = ""
        self._result_path = None
        self._cancel_requested = False
        self._raw_summary = None
        self._params = {}
        self._p = {}
        self._final_status = False
        self._resume_handle = None
        self._connect_try = 0  # bumped by every silent connect and its Cancel
        self._scan_proc = None  # the local scan's process
        self._scan_doing = ""  # what the scan under way is at, for the clock
        self._scan_begun = time.monotonic()
        self._scan_clock = QTimer(self)
        self._scan_clock.timeout.connect(self._scan_tick)
        self._start_text = ""  # what the start under way is at, for the clock
        self._viewer_opening = None  # the Activity of a viewer on its way
        self._viewer_timer = QTimer(self)
        self._viewer_timer.setSingleShot(True)
        self._viewer_timer.timeout.connect(self._viewer_started)
        self._setup_ui()
        self._load_settings()
        self._wire()
        set_hand_cursor(self)
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._scan_timer = QTimer(self)
        self._scan_timer.setSingleShot(True)
        self._scan_timer.timeout.connect(self._scan_now)
        QTimer.singleShot(0, self._startup)

    # ---------------------------- building --------------------------------

    def _card(self, title):
        frame = QFrame()
        frame.setObjectName("Card")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(8)
        lay.addWidget(kicker(title))
        return frame, lay

    @staticmethod
    def _hint(text):
        label = QLabel(text)
        label.setObjectName("Hint")
        label.setWordWrap(True)
        return label

    def _setup_ui(self):
        self.setWindowTitle("Create dynamic spectra")
        self.resize(*self._default_size())
        self.setMinimumSize(820, 520)
        outer = dialog_layout(self)

        # --- Run on
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(QLabel("Run on"))
        self.local_btn = QPushButton("This computer")
        self.server_btn = QPushButton("Server")
        for b in (self.local_btn, self.server_btn):
            b.setCheckable(True)
        self.local_btn.setChecked(True)
        self.run_group = QButtonGroup(self)
        self.run_group.setExclusive(True)
        self.run_group.addButton(self.local_btn)
        self.run_group.addButton(self.server_btn)
        row.addWidget(segmented([self.local_btn, self.server_btn]))
        self.conn_spinner = Spinner(14)
        row.addWidget(self.conn_spinner)
        self.conn_label = QLabel("")
        self.conn_label.setObjectName("Hint")
        row.addWidget(self.conn_label, 1)
        self.connect_btn = link_button("Connect…")
        row.addWidget(self.connect_btn)
        outer.addLayout(row)

        self.resume_bar = QFrame()
        self.resume_bar.setObjectName("Card")
        rl = QHBoxLayout(self.resume_bar)
        rl.setContentsMargins(12, 6, 12, 6)
        self.resume_label = QLabel("")
        self.resume_label.setWordWrap(True)
        rl.addWidget(self.resume_label, 1)
        self.resume_btn = QPushButton("Re-attach")
        self.forget_btn = QPushButton("Forget")
        rl.addWidget(self.resume_btn)
        rl.addWidget(self.forget_btn)
        self.resume_bar.setVisible(False)
        outer.addWidget(self.resume_bar)

        # --- Setup area (scrolls on a small screen)
        self.setup = QWidget()
        cols = QHBoxLayout(self.setup)
        cols.setContentsMargins(0, 0, 0, 0)
        cols.setSpacing(12)
        left = QVBoxLayout()
        left.setSpacing(10)
        right = QVBoxLayout()
        right.setSpacing(10)
        cols.addLayout(left, 1)
        cols.addLayout(right, 1)

        # Data
        card, lay = self._card("Data")
        drow = QHBoxLayout()
        self.data_edit = QLineEdit()
        self.data_edit.setPlaceholderText("Measurement Sets")
        self.data_edit.setToolTip("A folder of Measurement Sets, one MS, or several separated by ;")
        self.data_edit.setClearButtonEnabled(True)
        drow.addWidget(self.data_edit, 1)
        self.browse_btn = QPushButton("Browse…")
        self.browse_menu = QMenu(self)
        self.act_folder = self.browse_menu.addAction("Folder of Measurement Sets…")
        self.act_files = self.browse_menu.addAction("One or more MS…")
        drow.addWidget(self.browse_btn)
        lay.addLayout(drow)
        self.scan_label = QLabel("Choose the data to see what it holds.")
        self.scan_label.setObjectName("Hint")
        self.scan_label.setWordWrap(True)
        self.scan_spinner = Spinner(14)
        scan_row = QHBoxLayout()
        scan_row.setSpacing(6)
        scan_row.addWidget(self.scan_spinner, 0, Qt.AlignTop)
        scan_row.addWidget(self.scan_label, 1)
        lay.addLayout(scan_row)
        self.summary_grid = QGridLayout()
        self.summary_grid.setHorizontalSpacing(12)
        self.summary_grid.setVerticalSpacing(4)
        self.summary_grid.setColumnStretch(1, 1)
        lay.addLayout(self.summary_grid)
        left.addWidget(card)

        # Output
        card, lay = self._card("Output")
        orow = QHBoxLayout()
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("FITS file to write")
        orow.addWidget(self.output_edit, 1)
        self.output_browse_btn = QPushButton("Browse…")
        orow.addWidget(self.output_browse_btn)
        lay.addLayout(orow)
        self.output_where = QLabel("")
        self.output_where.setObjectName("Hint")
        lay.addWidget(self.output_where)
        self.plot_check = QCheckBox("Also save a plot (PNG)")
        self.plot_check.setChecked(True)
        lay.addWidget(self.plot_check)
        self.download_box = QWidget()
        dl = QVBoxLayout(self.download_box)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(6)
        self.download_check = QCheckBox("Download the result to this computer when done")
        self.download_check.setChecked(True)
        dl.addWidget(self.download_check)
        drow2 = QHBoxLayout()
        self.download_edit = QLineEdit()
        self.download_edit.setPlaceholderText("Local folder for the download")
        drow2.addWidget(self.download_edit, 1)
        self.download_browse_btn = QPushButton("Browse…")
        drow2.addWidget(self.download_browse_btn)
        dl.addLayout(drow2)
        lay.addWidget(self.download_box)
        left.addWidget(card)

        # Where it runs (server)
        self.exec_card, lay = self._card("How to run on the server")
        mrow = QHBoxLayout()
        self.now_btn = QPushButton("Run now")
        self.slurm_btn = QPushButton("Slurm job")
        for b in (self.now_btn, self.slurm_btn):
            b.setCheckable(True)
        self.now_btn.setChecked(True)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_group.addButton(self.now_btn)
        self.mode_group.addButton(self.slurm_btn)
        mrow.addWidget(segmented([self.now_btn, self.slurm_btn]))
        mrow.addStretch()
        lay.addLayout(mrow)
        self.mode_reason = QLabel("")
        self.mode_reason.setObjectName("Hint")
        self.mode_reason.setWordWrap(True)
        lay.addWidget(self.mode_reason)
        self.runtime_label = QLabel("")
        self.runtime_label.setObjectName("Hint")
        self.runtime_label.setWordWrap(True)
        lay.addWidget(self.runtime_label)
        left.addWidget(self.exec_card)
        left.addStretch()

        # Settings
        card, lay = self._card("Settings")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        lay.addLayout(grid)

        def spin(lo, hi, value, decimals=1, suffix="", special=None, step=None):
            w = QDoubleSpinBox()
            w.setRange(lo, hi)
            w.setDecimals(decimals)
            w.setValue(value)
            if suffix:
                w.setSuffix(suffix)
            if special:
                w.setSpecialValueText(special)
            if step:
                w.setSingleStep(step)
            w.setKeyboardTracking(False)
            return w

        grid.addWidget(QLabel("Time bin"), 0, 0)
        self.binwidth_spin = spin(0.01, 3600.0, 1.0, 2, " s", step=0.5)
        grid.addWidget(self.binwidth_spin, 0, 1)
        grid.addWidget(QLabel("Polarization"), 0, 2)
        self.pol_combo = QComboBox()
        for label, value in (
            ("Stokes I", "I"), ("XX", "XX"), ("YY", "YY"), ("XY", "XY"), ("YX", "YX"),
        ):
            self.pol_combo.addItem(label, value)
        grid.addWidget(self.pol_combo, 0, 3)

        grid.addWidget(QLabel("UV min"), 1, 0)
        self.uvmin_spin = spin(0, 1e6, 130.0, 0, " lambda", special="No limit")
        grid.addWidget(self.uvmin_spin, 1, 1)
        grid.addWidget(QLabel("UV max"), 1, 2)
        self.uvmax_spin = spin(0, 1e6, 500.0, 0, " lambda", special="No limit")
        grid.addWidget(self.uvmax_spin, 1, 3)
        self.uv_all_check = QCheckBox("Use all baselines (no UV cut)")
        grid.addWidget(self.uv_all_check, 2, 0, 1, 4)
        uvhint = self._hint(
            "Only baselines with lengths between these limits (in wavelengths) are "
            "used; 0 is no limit. The default, 130 to 500 lambda, is tuned for LOFAR "
            "solar data: short baselines see the whole Sun, so they keep the total "
            "flux. For another array, set a range that fits its baselines or use all."
        )
        grid.addWidget(uvhint, 3, 0, 1, 4)

        grid.addWidget(QLabel("Frequency"), 4, 0)
        self.startfreq_spin = spin(0, 1e5, 0, 3, " MHz", special="Auto")
        grid.addWidget(self.startfreq_spin, 4, 1)
        grid.addWidget(QLabel("to"), 4, 2, Qt.AlignRight)
        self.endfreq_spin = spin(0, 1e5, 0, 3, " MHz", special="Auto")
        grid.addWidget(self.endfreq_spin, 4, 3)
        grid.addWidget(QLabel("Time (UTC)"), 5, 0)
        self.tstart_edit = QLineEdit()
        self.tstart_edit.setPlaceholderText("Auto")
        grid.addWidget(self.tstart_edit, 5, 1)
        grid.addWidget(QLabel("to"), 5, 2, Qt.AlignRight)
        self.tend_edit = QLineEdit()
        self.tend_edit.setPlaceholderText("Auto")
        grid.addWidget(self.tend_edit, 5, 3)
        grid.addWidget(
            self._hint(
                "Frequency and time range are optional; one bound alone is fine. "
                "Times: 2023-03-10T10:00:00 (UTC) or MJD seconds."
            ),
            6, 0, 1, 4,
        )

        grid.addWidget(QLabel("Columns"), 7, 0)
        self.freqres_combo = QComboBox()
        for label, value in (
            ("Auto", "auto"), ("One column per subband", "subband"),
            ("Every channel", "channel"),
        ):
            self.freqres_combo.addItem(label, value)
        grid.addWidget(self.freqres_combo, 7, 1, 1, 3)
        grid.addWidget(QLabel("Average"), 8, 0)
        self.chanavg_spin = QSpinBox()
        self.chanavg_spin.setRange(1, 65536)
        self.chanavg_spin.setSuffix(" channels")
        self.chanavg_spin.setSpecialValueText("Auto")
        grid.addWidget(self.chanavg_spin, 8, 1)
        grid.addWidget(QLabel("CPUs"), 8, 2)
        self.ncpu_spin = QSpinBox()
        self.ncpu_spin.setRange(0, 4096)
        self.ncpu_spin.setSpecialValueText("Auto")
        grid.addWidget(self.ncpu_spin, 8, 3)
        self.cpu_hint = self._hint("")
        grid.addWidget(self.cpu_hint, 9, 0, 1, 4)
        right.addWidget(card)

        # Advanced
        self.adv_btn = QPushButton("Advanced")
        self.adv_btn.setObjectName("SectionHeader")
        self.adv_btn.setCheckable(True)
        right.addWidget(self.adv_btn)
        self.adv_box = QFrame()
        self.adv_box.setObjectName("Card")
        ag = QGridLayout(self.adv_box)
        ag.setContentsMargins(12, 10, 12, 12)
        ag.setHorizontalSpacing(10)
        ag.setVerticalSpacing(8)
        ag.setColumnStretch(1, 1)
        ag.addWidget(QLabel("Read block per CPU"), 0, 0)
        self.chunk_spin = QSpinBox()
        self.chunk_spin.setRange(8, 65536)
        self.chunk_spin.setSuffix(" MB")
        self.chunk_spin.setValue(DEFAULT_CHUNK_MB)
        ag.addWidget(self.chunk_spin, 0, 1)
        self.overwrite_check = QCheckBox("Replace existing output files (FITS and PNG) without asking")
        ag.addWidget(self.overwrite_check, 1, 0, 1, 2)
        self.server_adv = QWidget()
        sg = QGridLayout(self.server_adv)
        sg.setContentsMargins(0, 0, 0, 0)
        sg.setHorizontalSpacing(10)
        sg.setVerticalSpacing(8)
        sg.setColumnStretch(1, 1)
        sg.addWidget(QLabel("Container (.sif) or python"), 0, 0)
        self.container_edit = QLineEdit()
        self.container_edit.setPlaceholderText("Auto-detected; e.g. /project/.../image.sif")
        sg.addWidget(self.container_edit, 0, 1)
        sg.addWidget(QLabel("Slurm partition"), 1, 0)
        self.partition_combo = QComboBox()
        self.partition_combo.setEditable(True)
        self.partition_combo.addItem("(cluster default)", "")
        sg.addWidget(self.partition_combo, 1, 1)
        sg.addWidget(QLabel("Slurm memory"), 2, 0)
        self.mem_spin = QSpinBox()
        self.mem_spin.setRange(0, 4096)
        self.mem_spin.setSuffix(" GB")
        self.mem_spin.setSpecialValueText("Default")
        sg.addWidget(self.mem_spin, 2, 1)
        sg.addWidget(QLabel("Slurm time limit"), 3, 0)
        self.time_edit = QLineEdit("04:00:00")
        self.time_edit.setPlaceholderText("HH:MM:SS")
        sg.addWidget(self.time_edit, 3, 1)
        ag.addWidget(self.server_adv, 2, 0, 1, 2)
        self.adv_box.setVisible(False)
        right.addWidget(self.adv_box)
        right.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setWidget(self.setup)
        outer.addWidget(scroll, 1)
        outer.addWidget(rule())  # (where the settings go under the footer)

        # --- Progress
        self.problem_label = QLabel("")
        self.problem_label.setWordWrap(True)
        self.problem_label.setVisible(False)
        outer.addWidget(self.problem_label)

        prow = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)
        prow.addWidget(self.progress_bar, 1)
        outer.addLayout(prow)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)  # paths can be copied
        self.log_toggle = link_button("Show log")
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("Console")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setMinimumHeight(110)
        self.log_view.setMaximumHeight(200)
        self.log_view.setVisible(False)
        outer.addWidget(self.log_view)

        box, buttons = button_bar(
            primary=("Create dynamic spectrum", self._create),
            secondary=("Close", self.close),
        )
        self.run_btn = buttons["Create dynamic spectrum"]
        self.close_btn = buttons["Close"]
        # Stop and Open come and go, so they sit beside the bar, not in it
        bottom = QHBoxLayout()
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self._stop)
        self.open_btn = QPushButton("Open in dynamic spectrum viewer")
        self.open_btn.clicked.connect(self._open_viewer)
        self.retry_btn = QPushButton("Retry download")
        self.retry_btn.clicked.connect(self._retry_download)
        # offered when the server stops answering about the job for good
        self.forget_job_btn = QPushButton("Forget job")
        self.forget_job_btn.clicked.connect(self._forget_lost_job)
        self.keep_btn = QPushButton("Keep checking")
        self.keep_btn.clicked.connect(self._keep_checking)
        self.stop_btn.setVisible(False)
        self.open_btn.setVisible(False)
        self.retry_btn.setVisible(False)
        self.forget_job_btn.setVisible(False)
        self.keep_btn.setVisible(False)
        bottom.addWidget(self.stop_btn)
        bottom.addWidget(self.forget_job_btn)
        bottom.addWidget(self.keep_btn)
        bottom.addWidget(self.open_btn)
        bottom.addWidget(self.retry_btn)
        bottom.addWidget(self.status_label, 1)
        bottom.addWidget(self.log_toggle)
        bottom.addWidget(box)
        outer.addLayout(bottom)

        self._fields = {
            "data": self.data_edit, "output": self.output_edit,
            "binwidth": self.binwidth_spin, "uvmin": self.uvmin_spin,
            "uvmax": self.uvmax_spin, "startfreq": self.startfreq_spin,
            "endfreq": self.endfreq_spin, "tstart": self.tstart_edit,
            "tend": self.tend_edit, "chanavg": self.chanavg_spin,
            "chunkmb": self.chunk_spin, "time_limit": self.time_edit,
            "ncpu": self.ncpu_spin, "download_dir": self.download_edit,
        }
        order = [
            self.local_btn, self.server_btn, self.data_edit, self.browse_btn,
            self.output_edit, self.output_browse_btn, self.plot_check,
            self.download_check, self.download_edit, self.download_browse_btn,
            self.now_btn, self.slurm_btn, self.binwidth_spin, self.pol_combo,
            self.uvmin_spin, self.uvmax_spin, self.uv_all_check, self.startfreq_spin, self.endfreq_spin,
            self.tstart_edit, self.tend_edit, self.freqres_combo, self.chanavg_spin,
            self.ncpu_spin, self.adv_btn, self.chunk_spin, self.overwrite_check,
            self.container_edit, self.partition_combo, self.mem_spin, self.time_edit,
            self.run_btn,
        ]
        for a, b in zip(order, order[1:]):
            self.setTabOrder(a, b)

        # The form's labels, as on every kicker form
        for label in self.setup.findChildren(QLabel):
            if not label.objectName():
                label.setObjectName("Hint")
                set_tone(label, None)  # (sized by the rows, so polished already)

        tips = {
            self.binwidth_spin: "Width of one time pixel in the spectrum. Larger is smaller and smoother.",
            self.pol_combo: "Stokes I is the mean of the parallel hands (XX and YY, or RR and LL) that are unflagged.",
            self.startfreq_spin: "Lowest frequency to include; Auto keeps everything.",
            self.endfreq_spin: "Highest frequency to include; Auto keeps everything.",
            self.tstart_edit: "First time to include (blank = from the start of the data).",
            self.tend_edit: "Last time to include (blank = to the end of the data).",
            self.freqres_combo: "Auto: one column per subband for many MS, otherwise the channels (averaged if there are very many).",
            self.chanavg_spin: "Average this many channels into one column. Auto lets the engine average only when there are very many (over 2048 columns); Every channel never averages. Ignored for one column per subband.",
            self.ncpu_spin: "Processes working in parallel. Auto uses what this machine or job offers.",
            self.chunk_spin: "Data block read at once by each process (memory is about 3 times this per process); lower it if memory is short.",
            self.uv_all_check: "Do not cut by baseline length at all (sends 0 for both limits).",
            self.container_edit: "The Singularity/Apptainer image (.sif) with casacore on the server, or a python path. Remembered per server.",
            self.time_edit: "Wall-time limit asked from Slurm.",
            self.mem_spin: "Memory asked from Slurm (Default = the cluster's).",
            self.download_check: "Copy the FITS file (and plot) to this computer once the job finishes.",
        }
        for w, t in tips.items():
            w.setToolTip(t)

    def _wire(self):
        self.local_btn.clicked.connect(lambda: self._set_run_on("local"))
        self.server_btn.clicked.connect(lambda: self._set_run_on("server"))
        self.connect_btn.clicked.connect(self._connect_clicked)
        self.browse_btn.clicked.connect(self._browse_data)
        self.act_folder.triggered.connect(self._browse_folder)
        self.act_files.triggered.connect(self._browse_files)
        self.data_edit.textChanged.connect(self._data_changed)
        self.data_edit.returnPressed.connect(self._scan_soon)
        self.output_edit.textEdited.connect(lambda _: setattr(self, "_output_touched", True))
        self.time_edit.textEdited.connect(lambda _: setattr(self, "_time_touched", True))
        self.uv_all_check.toggled.connect(self._uv_all_toggled)
        self.output_edit.textChanged.connect(self._update_output_hint)
        self.output_browse_btn.clicked.connect(self._browse_output)
        self.download_browse_btn.clicked.connect(self._browse_download)
        self.download_check.toggled.connect(lambda on: self.download_edit.setEnabled(on))
        self.freqres_combo.currentIndexChanged.connect(self._resolution_changed)
        self.chanavg_spin.valueChanged.connect(self._resolution_changed)
        self.ncpu_spin.valueChanged.connect(self._cpus_changed)
        self.adv_btn.toggled.connect(self._toggle_advanced)
        self.log_toggle.clicked.connect(self._toggle_log)
        self.now_btn.clicked.connect(lambda: self._mode_chosen("direct"))
        self.slurm_btn.clicked.connect(lambda: self._mode_chosen("slurm"))
        self.container_edit.editingFinished.connect(self._container_changed)
        self.resume_btn.clicked.connect(self._resume)
        self.forget_btn.clicked.connect(self._forget_job)
        for w in (self.startfreq_spin, self.endfreq_spin, self.tstart_edit, self.tend_edit):
            sig = w.editingFinished if isinstance(w, QLineEdit) else w.valueChanged
            sig.connect(self._clear_problem)
        self._toggle_advanced(False)

    # ---------------------------- settings --------------------------------

    def _default_size(self):
        """The first size of the window: wide enough for the settings column
        without a scrollbar, and inside the screen."""
        width, height = 1000, 740
        try:
            from PyQt5.QtWidgets import QApplication

            screen = QApplication.primaryScreen()
            if screen is not None:
                avail = screen.availableGeometry()
                width, height = min(width, avail.width() - 40), min(height, avail.height() - 60)
        except Exception:
            pass
        return max(width, 820), max(height, 520)

    @staticmethod
    def _num(key, default, cast=float, lo=None, hi=None):
        """A number from the saved settings; the default if it is missing,
        not a number or not finite (a hand-edited or older file never stops
        the window from opening)."""
        try:
            value = float(_get(key, default))
        except (TypeError, ValueError):
            return default
        if not math.isfinite(value):
            return default
        if lo is not None:
            value = max(lo, value)
        if hi is not None:
            value = min(hi, value)
        return cast(value)

    def _load_settings(self):
        num = self._num
        self.binwidth_spin.setValue(num("binwidth", 1.0, float, 0.01, 3600.0))
        self.uvmin_spin.setValue(num("uvmin", 130.0, float, 0.0, 1e6))
        self.uvmax_spin.setValue(num("uvmax", 500.0, float, 0.0, 1e6))
        self.uv_all_check.setChecked(bool(_get("uv_all", False, bool)))
        self._uv_all_toggled(self.uv_all_check.isChecked())
        i = self.pol_combo.findData(_get("pol", "I"))
        self.pol_combo.setCurrentIndex(max(0, i))
        i = self.freqres_combo.findData(_get("freqres", "auto"))
        self.freqres_combo.setCurrentIndex(max(0, i))
        self.chanavg_spin.setValue(num("chanavg", 1, int, 1, 65536))
        self.ncpu_spin.setValue(num("ncpu_local", 0, int, 0, 4096))
        self.chunk_spin.setValue(num("chunkmb", DEFAULT_CHUNK_MB, int, 8, 65536))
        self.plot_check.setChecked(_get("saveplot", True, bool))
        self.download_check.setChecked(_get("download", True, bool))
        self.download_edit.setText(str(_get("download_dir", default_download_dir())))
        self.download_edit.setEnabled(self.download_check.isChecked())
        self.mem_spin.setValue(num("slurm_mem", 0, int, 0, 4096))
        limit = str(_get("slurm_time", "04:00:00"))
        self.time_edit.setText(limit if _TIME_LIMIT.match(limit.strip()) else "04:00:00")
        self._pref_run_on = _get("run_on", "local")
        self._pref_partition = _get("slurm_partition", "")

    def _save_settings(self):
        _put("binwidth", self.binwidth_spin.value())
        _put("uvmin", self.uvmin_spin.value())
        _put("uvmax", self.uvmax_spin.value())
        _put("uv_all", self.uv_all_check.isChecked())
        _put("pol", self.pol_combo.currentData())
        _put("freqres", self.freqres_combo.currentData())
        _put("chanavg", self.chanavg_spin.value())
        _put("ncpu_server" if self._is_server() else "ncpu_local", self.ncpu_spin.value())
        _put("chunkmb", self.chunk_spin.value())
        _put("saveplot", self.plot_check.isChecked())
        _put("download", self.download_check.isChecked())
        _put("download_dir", self.download_edit.text())
        _put("slurm_mem", self.mem_spin.value())
        _put("slurm_time", self.time_edit.text())
        _put("slurm_partition", self._partition())
        _put("run_on", "server" if self._is_server() else "local")
        _put("last_data_server" if self._is_server() else "last_data_local", self.data_edit.text())

    def _uv_all_toggled(self, on):
        for w in (self.uvmin_spin, self.uvmax_spin):
            w.setEnabled(not on)

    def _host_key(self):
        host = getattr(self._conn, "host", "") if self._conn is not None else ""
        return f"container/{host}" if host else None

    # ---------------------------- startup / connection --------------------

    def _startup(self):
        env = os.environ.get("SOLARVIEWER_SSH")
        want_server = False
        params = None
        if env:
            try:
                params = json.loads(env)
                want_server = True
            except (ValueError, TypeError):
                params = None
        if want_server:
            self._set_run_on("server", startup_params=params)
        else:
            if self._pref_run_on == "server":
                self.connect_btn.setText("Connect…")
            self._set_run_on("local")

    def _remote(self):
        """remote_dynspec, or None (paramiko may be missing; local mode still works)."""
        if self._remote_mod is None and not self._remote_error:
            try:
                from . import remote_dynspec

                self._remote_mod = remote_dynspec
            except Exception as e:
                self._remote_error = str(e)
        return self._remote_mod

    def _is_server(self):
        return self.server_btn.isChecked()

    def _connected(self):
        try:
            return self._conn is not None and self._conn.is_connected()
        except Exception:
            return False

    def _server_unavailable_text(self):
        text = f"Could not load the server module: {self._remote_error}"
        if "paramiko" in self._remote_error.lower():
            text += "\n\nRunning on a server needs the paramiko package (pip install paramiko)."
        return text

    def _set_run_on(self, kind, startup_params=None):
        if self._state != IDLE:
            self._sync_run_on_buttons()
            return
        if kind == "local":
            self._drop_connect_attempt()  # or its answer would bring the Server view back
        if kind == "server":
            if self._remote() is None:
                QMessageBox.warning(self, "Server not available", self._server_unavailable_text())
                kind = "local"
            elif not self._connected():
                self._switch_view("server")
                if startup_params is not None:
                    self._connect_silently(startup_params)
                else:
                    self._connect_dialog()
                return
        self._switch_view(kind)
        self._scan_soon()

    def _sync_run_on_buttons(self):
        (self.server_btn if self._last_view == "server" else self.local_btn).setChecked(True)

    _last_view = "local"

    def _switch_view(self, kind):
        """Show the controls for ``kind`` ("local" or "server")."""
        previous = self._last_view
        if previous != kind:
            _put("last_data_server" if previous == "server" else "last_data_local",
                 self.data_edit.text())
            if previous == "server":
                _put("ncpu_server", self.ncpu_spin.value())
            else:
                _put("ncpu_local", self.ncpu_spin.value())
        self._last_view = kind
        if previous != kind:
            self._hide_resume()  # an offer made for the other view or server is not valid here
        self._scan_token += 1
        self._scanning = False
        server = kind == "server"
        (self.server_btn if server else self.local_btn).setChecked(True)
        self.exec_card.setVisible(server)
        self.download_box.setVisible(server)
        self.server_adv.setVisible(server)
        self.overwrite_check.setVisible(True)
        self.browse_btn.setMenu(None if server else self.browse_menu)
        self.data_edit.blockSignals(True)
        self.data_edit.setText(str(_get("last_data_server" if server else "last_data_local", "")))
        self.data_edit.blockSignals(False)
        self.ncpu_spin.blockSignals(True)
        self.ncpu_spin.setValue(
            self._num("ncpu_server", SLURM_DEFAULT_CPUS, int, 0, 4096) if server
            else self._num("ncpu_local", 0, int, 0, 4096)
        )
        self.ncpu_spin.blockSignals(False)
        self._summary = None
        self._scan_error = ""
        self._output_touched = False
        self._outdir_redirected = False
        self._clear_recommendation()
        self._fill_summary(None)
        self._end_local_scan()
        if not self.data_edit.text():
            self._set_scan_text("Choose the data to see what it holds.")
        elif server and not self._connected():
            self._set_scan_text("")  # read once connected
        else:
            self._scan_begun = time.monotonic()
            self._scan_wait("Reading the data…")
        self._cpus_changed()
        self._update_connection_label()
        self._update_output_hint()
        self._update_state()

    def _update_connection_label(self):
        self.conn_spinner.setVisible(self._is_server() and self._conn_connecting)
        if self._is_server():
            if self._conn_connecting:
                self.conn_label.setText("Connecting…")
                self.connect_btn.setText("Cancel")
                self.connect_btn.setVisible(True)
            elif self._connected():
                c = self._conn
                self.conn_label.setText(f"Connected to {c.username}@{c.host}")
                self.connect_btn.setText("Change…")
                self.connect_btn.setVisible(True)
            else:
                self.conn_label.setText("Not connected")
                self.connect_btn.setText("Connect…")
                self.connect_btn.setVisible(True)
            host = getattr(self._conn, "host", "") if self._connected() else ""
            self.server_btn.setText(f"Server ({host})" if host else "Server")
        else:
            self.conn_label.setText("")
            self.connect_btn.setText("Connect to a server…")
            self.connect_btn.setVisible(True)
            self.server_btn.setText(
                f"Server ({self._conn.host})" if self._connected() else "Server"
            )

    def _connect_silently(self, params):
        """Reuse the main window's server (keys or agent; no password asked)."""
        self._conn_connecting = True
        self._connect_try += 1
        token = self._connect_try
        self._update_connection_label()

        def work():
            from ..remote.ssh_manager import SSHConnection

            c = SSHConnection()
            c.connect(
                params.get("host"), port=params.get("port"),
                username=params.get("username"), key_path=params.get("key_path"),
            )
            return c

        task = _Task(work)
        task.result.connect(lambda c, t=token: self._silently_connected(t, c))
        task.failed.connect(lambda e, t=token: t == self._connect_try and self._silent_failed(e))
        task.start()

    def _silently_connected(self, token, conn):
        if token == self._connect_try:
            self._connected_ok(conn)
        else:  # cancelled meanwhile: nobody will use this connection
            _Task(conn.disconnect).start()

    def _drop_connect_attempt(self):
        """Whatever a silent connect reports later is dropped."""
        self._connect_try += 1
        self._conn_connecting = False

    def _connect_clicked(self):
        if self._conn_connecting:
            self._drop_connect_attempt()
            if self._is_server():
                self._update_connection_label()
                self._set_scan_text("Cancelled. Use Connect… to sign in.")
                self._update_state()
                return
        self._connect_dialog()  # in the Local view the button says just this

    def _silent_failed(self, error):
        self._conn_connecting = False
        self._scan_timer.stop()  # (nothing to scan yet, and it would cover this)
        self._update_connection_label()
        self._set_scan_text(
            f"Could not reuse the main window's server connection ({error}). "
            "Use Connect… to sign in.", "warning",
        )
        self._update_state()

    def _connect_dialog(self):
        if self._remote() is None:
            QMessageBox.warning(self, "Server not available", self._server_unavailable_text())
            return
        try:
            from ..remote import ConnectionDialog
        except ImportError as e:
            QMessageBox.warning(self, "Server not available", str(e))
            return
        dialog = ConnectionDialog(self)
        env = os.environ.get("SOLARVIEWER_SSH")
        if env and self._conn is None:
            try:
                dialog.connect_to(json.loads(env))
            except (ValueError, TypeError, AttributeError):
                pass
        if dialog.exec_() == QDialog.Accepted and dialog.get_connection() is not None:
            self._connected_ok(dialog.get_connection())
        else:
            if not self._connected():
                self._switch_view("local")
                self._scan_soon()

    def _connected_ok(self, conn):
        self._conn = conn
        self._conn_connecting = False
        self._runtime = None
        self._partitions = None
        self._partitions_asked = False
        self._server_home = ""
        self._hide_resume()
        container = _get(self._host_key() or "container/-", "")
        if container:
            self.container_edit.setText(str(container))
        else:
            self.container_edit.clear()
        self._last_container = self.container_edit.text().strip()
        self._switch_view("server")
        self._offer_resume()
        self._scan_soon()

    # ---------------------------- data and scan ---------------------------

    def _data_changed(self, _text=None):
        """The text of the Data field changed: whatever a scan of the old text
        reports is dropped (the token), and a new scan follows a pause."""
        self._scan_token += 1
        self._end_local_scan()
        self._summary = None
        self._scan_error = ""
        self._clear_recommendation()
        if not self._output_touched:
            self.output_edit.blockSignals(True)
            self.output_edit.clear()
            self.output_edit.blockSignals(False)
            self._update_output_hint()
        self._fill_summary(None)
        have = bool(self.data_edit.text().strip())
        self._scanning = have
        if have:
            self._scan_begun = time.monotonic()
            self._scan_wait("Reading the data…")
        else:
            self._set_scan_text("Choose the data to see what it holds.")
        self._final_status = False
        self._scan_timer.start(1000 if self._is_server() else 600)
        self._update_state()

    def _scan_soon(self):
        self._scan_timer.start(50)

    def _clear_recommendation(self):
        self._recommendation = None
        self.mode_reason.setText("")

    def _set_scan_text(self, text, tone=None):
        """Say how a scan ended, or what to do next: nothing is under way."""
        self._scan_doing = ""
        self._scan_clock.stop()
        self.scan_spinner.setVisible(False)
        self._show_scan_text(text, tone)

    def _show_scan_text(self, text, tone=None):
        self.scan_label.setText(text)
        self.scan_label.setObjectName("Hint" if tone is None else "")
        set_tone(self.scan_label, tone)
        self.scan_label.setVisible(bool(text))

    def _scan_wait(self, text):
        """Say what the scan is at: it turns, and counts the time, until
        _set_scan_text() says how it went."""
        self._scan_doing = text
        self.scan_spinner.setVisible(True)
        if not self._scan_clock.isActive():
            self._scan_clock.start(1000)
        self._scan_tick()

    def _scan_tick(self):
        waited = time.monotonic() - self._scan_begun
        clock = f" ({format_duration(waited)})" if waited >= SCAN_CLOCK_AFTER_S else ""
        self._show_scan_text(self._scan_doing + clock)

    def _scan_progress(self, token, done, total):
        if token == self._scan_token and total > 1:
            self._scan_wait(f"Reading the data… {done} of {total} Measurement Sets")

    def _end_local_scan(self):
        """Stop a scan whose answer is no longer wanted, so that the next one
        does not wait for it."""
        proc, self._scan_proc = self._scan_proc, None
        if proc is not None:
            proc.terminate()

    def _paths(self):
        """The data paths; on this computer made absolute (~ expanded), so the
        scan, the check and the engine all see the same place."""
        paths = split_paths(self.data_edit.text())
        if not self._is_server():
            paths = [os.path.abspath(os.path.expanduser(p)) for p in paths]
        return paths

    def _scan_now(self):
        paths = self._paths()
        self._scan_token += 1
        token = self._scan_token
        self._summary = None
        if not paths:
            self._scanning = False
            self._set_scan_text("Choose the data to see what it holds.")
            self._update_state()
            return
        self._scanning = True
        self._scan_error = ""
        if self._scan_busy:
            # one scan at a time (each is a process, on the server a container
            # start on a shared node); this one follows when it is over
            self._scan_again = True
            self._end_local_scan()
            self._scan_wait("Reading the data…")
            self._update_state()
            return
        self._scan_begun = time.monotonic()
        self._update_state()
        if not self._is_server():
            self._scan_wait("Reading the data…")

            def started(proc, t=token):
                self._scan_proc = proc
                if t != self._scan_token:  # the data changed as it started
                    self._end_local_scan()

            task = _Task(lambda: local_scan(
                paths, on_start=started, on_progress=lambda *n: task.note.emit(n)))
            task.note.connect(lambda n, t=token: self._scan_progress(t, *n))
            task.result.connect(lambda raw, t=token: self._scan_done(t, raw))
            task.failed.connect(lambda e, t=token: self._scan_failed(t, e))
            self._start_scan_thread(task)
            return
        remote = self._remote()
        if not self._connected() or remote is None:
            self._scanning = False
            self._set_scan_text("Connect to a server to read data there.", "warning")
            self._update_state()
            return
        bad = [p for p in paths if not p.startswith("/")]
        if bad:
            self._scanning = False
            self._scan_error = "On the server, give the full path starting with /."
            self._set_scan_text(self._scan_error, "error")
            self._update_state()
            return
        if self._engine_source is None:
            try:
                with open(ENGINE_PATH, "r", encoding="utf-8") as f:
                    self._engine_source = f.read()
            except OSError as e:
                self._scan_failed(token, e)
                return
        hints = [h for h in (self.container_edit.text().strip(),) if h]
        self._scan_wait(
            "Looking for the software on the server…" if self._runtime is None
            else "Reading the data on the server…"
        )
        scan = _ServerScan(
            remote, self._conn, self._engine_source, paths, self._runtime, hints,
            self._partitions is None and not self._partitions_asked,
            current=lambda t=token: t == self._scan_token,
        )
        scan.stage.connect(lambda name, value, t=token: self._server_stage(t, name, value))
        self._start_scan_thread(scan)

    def _start_scan_thread(self, thread):
        self._scan_busy = True
        thread.finished.connect(self._scan_thread_ended)
        thread.start()

    def _scan_thread_ended(self):
        self._scan_busy = False
        self._scan_proc = None
        if self._scan_again and not self._closed:
            self._scan_again = False
            self._scan_now()

    def _server_stage(self, token, name, value):
        if token != self._scan_token:
            return
        if name == "candidate":
            path, number, total = value
            self._scan_wait(f"Trying {posixpath.basename(path)} on the server ({number} of {total})…")
        elif name == "runtime":
            self._runtime = value
            self._show_runtime(value)
            self._scan_wait("Reading the data on the server…")
        elif name == "runtime_error":
            self._scanning = False
            self._runtime = None
            fixes = getattr(value, "fixes", None) or []
            details = getattr(value, "details", "") or str(value)
            text = "The software for reading Measurement Sets was not found on the server.\n" + details
            if fixes:
                text += "\n" + "\n".join(f"- {f}" for f in fixes)
            self._scan_error = text
            self._set_scan_text(text, "error")
            self.runtime_label.setText("No container found. Enter its path under Advanced.")
            if not self.adv_btn.isChecked():
                self.adv_btn.setChecked(True)
            self._update_state()
        elif name == "summary":
            self._scan_done(token, value)
        elif name == "summary_error":
            self._scan_failed(token, value)
        elif name == "outdir":
            self._server_home = value.get("home", "")
            if not value.get("writable", True) and self._server_home:
                if not self._output_touched:
                    self._fill_default_output(writable=False)
            self._update_output_hint()
        elif name == "partitions":
            self._partitions_asked = True
            self._partitions = value
            self._fill_partitions(value)

    def _show_runtime(self, runtime):
        if runtime.kind == "container":
            text = f"Using container {runtime.container}"
        else:
            text = f"Using {runtime.python} on the server (no container)"
        self.runtime_label.setText(text)
        key = self._host_key()
        if key and runtime.kind == "container" and runtime.container:
            _put(key, runtime.container)

    def _scan_done(self, token, raw):
        if token != self._scan_token:
            return
        self._scanning = False
        self._raw_summary = raw
        try:
            self._summary = normalize_summary(raw)
        except Exception as e:  # an unexpected shape of the summary
            self._scan_failed(token, e)
            return
        if self._summary["count"] == 0:
            self._scan_failed(token, RuntimeError("no ms found"))
            return
        self._set_scan_text("", None)
        self._fill_summary(self._summary)
        if not self._output_touched:
            self._fill_default_output()
        self._recommend()
        self._update_cpu_hint()
        self._update_state()

    def _scan_failed(self, token, error):
        if token != self._scan_token:
            return
        self._scanning = False
        self._summary = None
        self._scan_error = friendly_scan_error(str(error), remote=self._is_server())
        self._set_scan_text(self._scan_error, "error")
        self._clear_recommendation()
        if not self._output_touched:
            self.output_edit.blockSignals(True)
            self.output_edit.clear()
            self.output_edit.blockSignals(False)
            self._update_output_hint()
        self._fill_summary(None)
        self._update_state()

    def _fill_summary(self, summary):
        while self.summary_grid.count():
            item = self.summary_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        if not summary:
            return
        rows = describe_summary(
            summary, self.freqres_combo.currentData(), self.chanavg_spin.value()
        )
        for r, (label, text) in enumerate(rows):
            name = QLabel(label)
            name.setObjectName("Hint")
            name.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            value = QLabel(text)
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.summary_grid.addWidget(name, r, 0)
            self.summary_grid.addWidget(value, r, 1)

    def _resolution_changed(self, *_):
        self.chanavg_spin.setEnabled(self.freqres_combo.currentData() != "subband")
        if self._summary:
            self._fill_summary(self._summary)

    def _fill_partitions(self, partitions):
        current = self._pref_partition or self._partition()
        self.partition_combo.blockSignals(True)
        self.partition_combo.clear()
        self.partition_combo.addItem("(cluster default)", "")
        for entry in partitions or []:
            try:
                name, limit, state = entry[0], entry[1], entry[2]
            except (IndexError, TypeError):
                continue
            self.partition_combo.addItem(f"{name}  (limit {limit}, {state})", name)
        i = self.partition_combo.findData(current)
        self.partition_combo.setCurrentIndex(max(0, i))
        self.partition_combo.blockSignals(False)

    def _partition(self):
        text = self.partition_combo.currentText()
        data = self.partition_combo.currentData()
        idx = self.partition_combo.currentIndex()
        if idx >= 0 and self.partition_combo.itemText(idx) == text:
            return data or ""
        return text.strip()  # typed by hand

    def _container_changed(self):
        text = self.container_edit.text().strip()
        if not self._is_server() or text == self._last_container:
            return  # editingFinished also fires when focus just passes through
        self._last_container = text
        key = self._host_key()
        if key:
            _put(key, text)
        self._runtime = None
        self._scan_soon()

    # ---------------------------- output and mode -------------------------

    def _fill_default_output(self, writable=None):
        paths = self._paths()
        remote = self._is_server()
        if remote:
            if writable is None:
                writable = True
            home = self._server_home
        else:
            home = os.path.expanduser("~")
            first = paths[0].rstrip("/\\") if paths else ""
            folder = os.path.dirname(first) if is_ms_name(first) or len(paths) > 1 else first
            writable = os.access(folder or ".", os.W_OK)
        self._outdir_redirected = not writable and bool(home)
        self.output_edit.blockSignals(True)
        self.output_edit.setText(default_output_path(paths, remote, writable, home))
        self.output_edit.blockSignals(False)
        self._update_output_hint()

    def _update_output_hint(self, *_):
        text = self.output_edit.text().strip()
        if not text:
            self.output_where.setText("")
            return
        if self._is_server():
            where = f"saved on the server ({getattr(self._conn, 'host', '')})"
        else:
            where = "saved on this computer"
        if self._outdir_redirected and not self._output_touched:
            where += ". The data folder is read-only, so the file goes to your home folder."
        self.output_where.setText(where)

    def _recommend(self):
        self._clear_recommendation()
        if not self._is_server() or not self._summary:
            return
        remote = self._remote()
        try:
            self._recommendation = remote.recommend_mode(self._summary_raw_for_mode(), self._user_cpus())
        except Exception:
            self._recommendation = None
        if self._recommendation:
            mode, reason, est = self._recommendation
            if est and "estimate" not in reason.lower():
                reason += f"  (a rough estimate: about {format_duration(est)}, give or take a factor of 2)"
            self.mode_reason.setText(reason)
            if not self._mode_touched:
                (self.slurm_btn if mode == "slurm" else self.now_btn).setChecked(True)
            if est and not self._time_touched:
                self.time_edit.setText(suggest_time_limit(est))
        self._cpus_changed()

    def _summary_raw_for_mode(self):
        """The summary as the engine gave it (recommend_mode reads that)."""
        return self._raw_summary

    def _user_cpus(self):
        n = self.ncpu_spin.value()
        return n or None

    def _mode(self):
        return "slurm" if self.slurm_btn.isChecked() else "direct"

    def _mode_chosen(self, mode):
        self._mode_touched = True
        self._cpus_changed()
        self._update_state()

    def _cpus_changed(self, *_):
        server = self._is_server()
        slurm = server and self._mode() == "slurm"
        self.ncpu_spin.setSpecialValueText("" if slurm else "Auto")
        self.ncpu_spin.setMinimum(1 if slurm else 0)
        self.ncpu_spin.setSuffix(" requested" if slurm else "")
        for w in (self.mem_spin, self.time_edit, self.partition_combo):
            w.setEnabled(slurm)
            w.setToolTip(w.toolTip() if slurm else "Only used for Slurm jobs.")
        self._update_cpu_hint()

    def _update_cpu_hint(self):
        server = self._is_server()
        auto = self.ncpu_spin.value() == 0
        if server and self._mode() == "slurm":
            text = "CPUs asked from Slurm; the job uses all of them."
        elif server:
            text = (
                f"Auto = up to {DIRECT_SERVER_CPUS} processes (the login node is shared)"
                if auto else ""
            )
        else:
            text = f"Auto = all {local_cpu_count()} cores of this computer" if auto else ""
        self.cpu_hint.setText(text)
        self.cpu_hint.setVisible(bool(text))

    def _toggle_advanced(self, on):
        self.adv_box.setVisible(on)
        self.adv_btn.setText(("v  " if on else ">  ") + "Advanced")

    def _toggle_log(self):
        show = not self.log_view.isVisible()
        self.log_view.setVisible(show)
        self.log_toggle.setText("Hide log" if show else "Show log")

    # ---------------------------- browsing --------------------------------

    def _browse_data(self):
        if self._is_server():
            self._browse_remote_folder()
        else:
            self.browse_menu.exec_(self.browse_btn.mapToGlobal(self.browse_btn.rect().bottomLeft()))

    def _start_dir(self):
        paths = self._paths()
        for p in paths:
            d = p if os.path.isdir(p) else os.path.dirname(p)
            if os.path.isdir(d):
                return d
        return str(_get("last_browse", os.path.expanduser("~")))

    def _browse_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Select the folder of Measurement Sets", self._start_dir())
        if d:
            _put("last_browse", d)
            self.data_edit.setText(d)

    def _browse_files(self):
        """One or more MS (they are folders: the dialog is told to pick several)."""
        dialog = QFileDialog(self, "Select Measurement Sets", self._start_dir())
        dialog.setFileMode(QFileDialog.Directory)
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        dialog.setOption(QFileDialog.ShowDirsOnly, False)
        for view in dialog.findChildren(QListView) + dialog.findChildren(QTreeView):
            view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        if dialog.exec_() == QDialog.Accepted:
            chosen = dialog.selectedFiles()
            if chosen:
                _put("last_browse", os.path.dirname(chosen[0]))
                self.data_edit.setText("; ".join(chosen))

    def _browse_remote_folder(self):
        if not self._connected():
            self._connect_dialog()
            return
        try:
            from ..remote import RemoteFileBrowser, RemoteFileCache
        except ImportError as e:
            QMessageBox.warning(self, "Server not available", str(e))
            return
        picked = []
        browser = RemoteFileBrowser(self._conn, cache=RemoteFileCache(), parent=self, pick="folder")
        browser.setWindowTitle("Choose the folder of Measurement Sets (or one MS) on the server")
        browser.pathPicked.connect(picked.append)
        browser.exec_()
        if picked:
            self.data_edit.setText(picked[0])

    def _browse_output(self):
        if self._is_server():
            self._browse_remote_output()
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save FITS file", self.output_edit.text() or "dynamic_spectrum.fits",
            "FITS files (*.fits);;All files (*)",
        )
        if path:
            self._output_touched = True
            self.output_edit.setText(path)

    def _browse_remote_output(self):
        """Pick the server folder for the output; the file name is kept."""
        if not self._connected():
            self._connect_dialog()
            return
        try:
            from ..remote import RemoteFileBrowser, RemoteFileCache
        except ImportError as e:
            QMessageBox.warning(self, "Server not available", str(e))
            return
        picked = []
        browser = RemoteFileBrowser(self._conn, cache=RemoteFileCache(), parent=self, pick="folder")
        browser.setWindowTitle("Choose the server folder for the output file")
        browser.pathPicked.connect(picked.append)
        browser.exec_()
        if picked:
            name = posixpath.basename(self.output_edit.text().strip()) or "dynamic_spectrum.fits"
            self._output_touched = True
            self.output_edit.setText(posixpath.join(picked[0], name))

    def _browse_download(self):
        d = QFileDialog.getExistingDirectory(
            self, "Download the result to", self.download_edit.text() or os.path.expanduser("~")
        )
        if d:
            self.download_edit.setText(d)

    # ---------------------------- state -----------------------------------

    def _gather(self):
        """The settings as a dict (see validate_params / build_engine_args)."""
        server = self._is_server()
        start = self.startfreq_spin.value()
        end = self.endfreq_spin.value()
        all_uv = self.uv_all_check.isChecked()
        output = self.output_edit.text().strip()
        if output and not server:
            output = os.path.abspath(os.path.expanduser(output))
        freqres = self.freqres_combo.currentData()
        return {
            "remote": server,
            "data": self._paths(),
            "output": output,
            "binwidth": self.binwidth_spin.value(),
            "uvmin": 0.0 if all_uv else self.uvmin_spin.value(),  # 0 = no limit
            "uvmax": 0.0 if all_uv else self.uvmax_spin.value(),
            "startfreq": start if start > 0 else None,
            "endfreq": end if end > 0 else None,
            "tstart": self.tstart_edit.text().strip(),
            "tend": self.tend_edit.text().strip(),
            "pol": self.pol_combo.currentData(),
            "freqres": freqres,
            "chanavg": effective_chanavg(freqres, self.chanavg_spin.value()),
            "ncpu": self.ncpu_spin.value(),
            "chunkmb": self.chunk_spin.value(),
            "saveplot": self.plot_check.isChecked(),
            "overwrite": self.overwrite_check.isChecked(),
            "mode": self._mode() if server else "local",
            "time_limit": self.time_edit.text().strip(),
            "download": self.download_check.isChecked(),
            "download_dir": self.download_edit.text().strip(),
        }

    def _can_create(self):
        """(ok, reason): whether Create can be pressed now, and if not why."""
        if self._state != IDLE:
            return False, ""
        if self._is_server() and self._conn_connecting:
            return False, "Connecting to the server…"
        if self._is_server() and not self._connected():
            return False, "Connect to a server first."
        if not self._paths():
            return False, "Choose the data first."
        if self._scanning:
            return False, "Reading the data…"
        if self._summary is None:
            return False, "The data could not be read (see above)."
        if self._is_server() and self._runtime is None:
            return False, "No software for reading the data was found on the server."
        return True, ""

    def _update_state(self):
        """The one place that decides what can be pressed, from the run state."""
        ok, reason = self._can_create()
        self.run_btn.setEnabled(ok)
        self.run_btn.setToolTip(reason)
        busy = self._state != IDLE
        self.setup.setEnabled(not busy)
        self.local_btn.setEnabled(not busy)
        self.server_btn.setEnabled(not busy)
        self.connect_btn.setEnabled(not busy)
        self.resume_bar.setEnabled(not busy)
        if not busy and not self._final_status:
            self._set_status(reason)

    def _show_problems(self, problems):
        errors = [p for p in problems if p[2] == "error"]
        shown = errors or problems
        if not shown:
            self._clear_problem()
            return
        self.problem_label.setText("\n".join(p[1] for p in shown))
        set_tone(self.problem_label, "error" if errors else "warning")
        self.problem_label.setVisible(True)
        if errors:
            widget = self._fields.get(errors[0][0])
            if widget is not None:
                widget.setFocus()

    def _clear_problem(self, *_):
        self.problem_label.setVisible(False)

    # ---------------------------- running ---------------------------------

    def _create(self):
        if self._state != IDLE:  # a second click, Enter, ...: one start at a time
            return
        p = self._gather()
        problems = validate_params(p, self._summary)
        self._show_problems(problems)
        if any(level == "error" for _, _, level in problems):
            return
        ok, reason = self._can_create()
        if not ok:
            self._set_status(reason)
            return
        self._save_settings()
        self._run_token += 1
        token = self._run_token
        self._final_status = False
        self._submitting = False
        self.retry_btn.setVisible(False)
        self._enter(STARTING)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)
        self._set_status("Checking the output location…")
        if p["remote"]:
            task = _Task(lambda: self._remote_check(p))
            task.result.connect(lambda res, t=token: self._after_check(t, p, res))
            task.failed.connect(lambda e, t=token: self._start_failed(t, f"Could not reach the server: {e}"))
            task.start()
        else:  # a network filesystem can be slow: not on the GUI thread
            task = _Task(lambda: self._local_check(p))
            task.result.connect(lambda res, t=token: self._after_check(t, p, res))
            task.failed.connect(lambda e, t=token: self._start_failed(t, f"Could not check the output location: {e}"))
            task.start()

    @staticmethod
    def _output_files(p):
        """The files a run writes: the FITS and, if asked, the PNG."""
        files = [p["output"]]
        if p.get("saveplot"):
            files.append(plot_path_for(p["output"], p["remote"]))
        return files

    def _local_check(self, p):
        """On this computer (in a thread, a network filesystem can be slow):
        which output files exist and whether the output can be written."""
        problem = check_local_output(p["output"]) if p["output"] else None
        folder = os.path.dirname(p["output"]) or "."
        if problem and "does not exist" in problem[1]:
            return {"exists": [], "folder": "nodir", "dir": folder}
        if problem and "not allowed" in problem[1]:
            return {"exists": [], "folder": "nowrite", "dir": folder}
        if problem:
            return {"exists": [], "folder": "ok", "error": problem[1]}
        return {"exists": [f for f in self._output_files(p) if os.path.exists(f)], "folder": "ok", "dir": folder}

    def _remote_check(self, p):
        """On the server (in a thread): which output files exist, and whether
        their folder can be written."""
        exists = []
        sftp = self._conn.open_sftp()
        try:
            for path in self._output_files(p):
                try:
                    sftp.stat(path)
                    exists.append(path)
                except IOError:
                    pass
        finally:
            try:
                sftp.close()
            except Exception:
                pass
        folder = posixpath.dirname(p["output"]) or "/"
        state = "ok"
        try:
            q = shlex.quote(folder)
            cmd = (f"if test ! -d {q}; then printf %s nodir; elif test ! -w {q}; "
                   "then printf %s nowrite; else printf %s ok; fi")
            _, stdout, _ = self._conn.exec_command(cmd, timeout=20)
            try:
                state = stdout.read().decode(errors="replace").strip() or "ok"
            finally:
                _close_channel(stdout)
        except Exception:
            pass  # the engine checks again before it computes
        return {"exists": exists, "folder": state, "dir": folder}

    def _after_check(self, token, p, res):
        if token != self._run_token or self._state != STARTING or self._closed:
            return
        where = "on the server" if p["remote"] else "on this computer"
        folder = res.get("dir") or os.path.dirname(p["output"])
        if res.get("error"):
            self._start_failed(token, res["error"])
            return
        if res.get("folder") == "nodir":
            self._start_failed(token, f"The output folder does not exist {where}: {folder}")
            return
        if res.get("folder") == "nowrite":
            self._start_failed(token, f"You are not allowed to write to the output folder {where}: {folder}")
            return
        exists = list(res.get("exists") or [])
        if exists and not p["overwrite"]:
            names = "\n".join(exists)
            self.progress_bar.setVisible(False)  # the question waits for the user, not for work
            self._set_status("The output already exists.")
            reply = QMessageBox.question(
                self, "File exists",
                f"These files already exist {where}:\n\n{names}\n\nReplace them?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if token != self._run_token or self._state != STARTING:
                return
            if reply != QMessageBox.Yes:
                self._cancel_start("Not started: the output already exists.")
                return
            p["overwrite"] = True
        self._begin(p)

    def _engine_params(self, p):
        params = {
            "outfits": p["output"],
            "binwidth": p["binwidth"],
            "uvmin": p["uvmin"],
            "uvmax": p["uvmax"],
            "startfreq": p["startfreq"],
            "endfreq": p["endfreq"],
            "tstart": p["tstart"] or None,
            "tend": p["tend"] or None,
            "pol": p["pol"],
            "freqres": p["freqres"],
            "chanavg": p["chanavg"],
            "chunkmb": p["chunkmb"],
            "saveplot": p["saveplot"],
            "plotfile": plot_path_for(p["output"], p["remote"]) if p["saveplot"] else None,
            "overwrite": p["overwrite"],
        }
        if len(p["data"]) == 1:
            params["indir"] = p["data"][0]
        else:
            params["ms"] = p["data"]
        mode = p["mode"]
        if mode == "slurm":
            params["ncpu"] = p["ncpu"]
        elif p["remote"]:
            params["ncpu"] = p["ncpu"] or DIRECT_SERVER_CPUS
        else:
            params["ncpu"] = p["ncpu"]  # 0 = the engine's auto
        return params

    def _begin(self, p):
        self._runlog = RunLog()
        self._tracker = ProgressTracker()
        self._started = time.monotonic()
        self._result_path = None
        self._remote_result = None
        self._handle = None  # nothing to stop or poll until the server has answered
        self._cancel_requested = False
        self._final_status = False
        self._reset_run_view()
        self.log_view.clear()
        self.open_btn.setVisible(False)
        self.retry_btn.setVisible(False)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)
        self._clear_problem()
        params = self._engine_params(p)
        self._params = params
        self._p = p
        if not p["remote"]:
            cmd = local_command(params)
            self._log("Running on this computer: " + " ".join(shlex.quote(c) for c in cmd))
            self._runner = LocalRunner(cmd)
            self._runner.line.connect(self._local_line)
            self._runner.exited.connect(self._local_exited)
            self._enter(LOCAL)
            self._set_status("Starting…")
            self._runner.start()
            return
        remote = self._remote()
        slurm = None
        if p["mode"] == "slurm":
            slurm = remote.SlurmOptions(
                partition=self._partition() or None, cpus=p["ncpu"],
                mem_gb=self.mem_spin.value() or None, time_limit=p["time_limit"], extra=[],
            )
        self._submitting = True
        self._start_text = "Submitting to the server…" if slurm else "Starting on the server…"
        self._set_status(self._start_text)
        conn, runtime, source = self._conn, self._runtime, self._engine_source
        token = self._run_token

        def submit():
            handle = remote.submit(
                conn, runtime, source, params, mode=p["mode"], slurm=slurm,
                overwrite=p["overwrite"],
            )
            # saved at once, so that a window closed now still finds the job again
            self._persist_job(handle)
            if token != self._run_token:  # cancelled or closed while submitting
                try:
                    if remote.cancel(conn, handle):
                        self._cancelled_jobs.add(handle.job_id)
                        self._forget_persisted(handle)
                except Exception:
                    pass
            return handle

        task = _Task(submit)
        task.result.connect(lambda h, t=token: self._submitted(t, h))
        task.failed.connect(lambda e, t=token: self._start_failed(t, self._submit_error(e)))
        task.start()

    @staticmethod
    def _submit_error(e):
        if isinstance(e, FileExistsError):
            return f"The output already exists on the server: {e}"
        return f"Could not start the job: {e}"

    def _reset_run_view(self):
        self._slurm_state = ""
        self._slurm_reason = ""
        self._offline_since = None
        self._unknown_polls = 0
        self._unknown_reason = ""
        self._unknown_since = None
        self._unknown_node = None
        self._gave_up = False
        self._last_progress = time.monotonic()
        self._stopping = False
        self._stop_requested = False
        self._stop_unconfirmed = None
        self._note = None
        self._seed_elapsed = False
        self._show_lost_buttons(False)

    def _start_failed(self, token, text):
        if token != self._run_token or self._closed:
            return
        self._submitting = False
        self._fail_start(text)

    def _fail_start(self, text):
        self._final_status = True
        self._leave()
        self.progress_bar.setVisible(False)
        self._set_status(text, "error")
        self._log("ERROR: " + text)
        self.log_view.setVisible(True)
        self.log_toggle.setText("Hide log")

    def _cancel_start(self, text):
        """Give up before the run exists (Stop pressed, or the output not to be replaced)."""
        self._run_token += 1  # whatever the check or the submit reports later is dropped
        self._submitting = False
        self._final_status = True
        self._leave()
        self.progress_bar.setVisible(False)
        self._set_status(text)

    def _enter(self, state):
        self._state = state
        self.stop_btn.setVisible(state != IDLE)
        self.stop_btn.setText("Cancel" if state == STARTING else "Stop")
        self.stop_btn.setEnabled(True)
        self._show_lost_buttons(False)
        self.close_btn.setText("Close")
        if state in (STARTING, LOCAL, REMOTE):
            self._tick_timer.start(1000)
        else:
            self._tick_timer.stop()
        self._update_state()

    def _leave(self):
        self._state = IDLE
        self._poll_timer.stop()
        self._tick_timer.stop()
        self._stopping = False
        self.stop_btn.setVisible(False)
        self._show_lost_buttons(False)
        self._update_state()

    def _log(self, text):
        self.log_view.appendPlainText(text)

    def _set_status(self, text, tone=None):
        self.status_label.setText(text)
        set_tone(self.status_label, tone)

    # --- local

    def _local_line(self, line):
        shown = self._runlog.feed(line)
        if shown is not None:
            self._log(shown)
        self._progress_update()

    def _local_exited(self, code, cancelled):
        if self._closed or self._state != LOCAL:
            return
        self._progress_update()
        out = self._params["outfits"]
        self._runner = None
        if code == 0:  # finished, whatever Stop did a moment before
            self._finish_ok(self._runlog.result or out)
        elif cancelled or self._stop_requested:
            self._finish_fail("Stopped.", cancelled=True)
        else:
            why = self._runlog.errors[-1] if self._runlog.errors else f"the process ended with code {code}"
            self._finish_fail(friendly_job_error(why))

    # --- remote

    def _submitted(self, token, handle):
        if token != self._run_token or self._state != STARTING or self._closed:
            # Cancelled meanwhile. The submit thread stops the job when it saw the
            # cancel in time; if Stop came after that check, stop it here.
            if handle is not None and handle.job_id not in self._cancelled_jobs:
                self._cancel_late_job(handle)
            return
        self._submitting = False
        self._handle = handle
        self._offset = 0
        self._unknown_polls = 0
        self._log(f"Job {handle.job_id} started on {handle.host} ({handle.mode}). Log: {handle.log_path}")
        self._enter(REMOTE)
        self._tick()
        self._poll_timer.start(2000)
        self._poll()

    def _cancel_late_job(self, handle):
        """A job that started after Stop: cancel it, and say what happened."""
        remote, conn = self._remote(), self._conn
        self._cancelled_jobs.add(handle.job_id)  # one attempt at a time

        def done(ok, why=""):
            if ok:
                self._forget_persisted(handle)
                text = f"Job {handle.job_id} had just started on the server and was stopped."
            else:
                self._cancelled_jobs.discard(handle.job_id)
                text = (f"Job {handle.job_id} started on the server after Stop and could not be "
                        "stopped" + (f" ({why})" if why else "") + "; check it on the server.")
            if not self._closed:
                self._log(text)
                if self._state == IDLE:
                    self._set_status(text, None if ok else "warning")

        task = _Task(lambda: remote.cancel(conn, handle))
        task.result.connect(lambda ok: done(bool(ok)))
        task.failed.connect(lambda e: done(False, str(e)))
        task.start()

    @staticmethod
    def _remote_hint(handle):
        return ("You can close this window: the job keeps running on the server "
                "and can be picked up again from this dialog.")

    def _poll(self):
        if self._poll_busy or self._handle is None or self._state != REMOTE:
            return
        self._poll_busy = True
        remote, conn, handle, offset = self._remote(), self._conn, self._handle, self._offset

        def work():
            try:
                return remote.poll(conn, handle, offset)
            except Exception:
                try:
                    conn.ensure_connected()
                except Exception:
                    pass
                return remote.poll(conn, handle, offset)

        task = _Task(work)
        task.result.connect(lambda status, h=handle: self._polled(status, h))
        task.failed.connect(self._poll_failed)
        task.start()

    def _go_offline(self, why=""):
        if self._offline_since is None:
            self._offline_since = time.monotonic()
        self._offline_why = why

    def _poll_failed(self, error):
        self._poll_busy = False
        if self._closed or self._state != REMOTE:
            return
        self._go_offline(str(error))
        self._maybe_give_up(self._offline_since)
        self._tick()

    def _polled(self, status, handle=None):
        self._poll_busy = False
        if self._closed or self._state != REMOTE or (handle is not None and handle is not self._handle):
            return
        self._offset = status.offset
        if status.new_lines or (status.total and status.done > self._runlog.done):
            self._last_progress = time.monotonic()
        for line in status.new_lines:
            shown = self._runlog.feed(line)
            if shown is not None:
                self._log(shown)
        if status.total:
            self._runlog.total = max(self._runlog.total, status.total)
            self._runlog.done = max(self._runlog.done, status.done)
        for w in status.warnings or []:
            if w not in self._runlog.warnings:
                self._runlog.warnings.append(w)
                self._log("Warning: " + w)
        state = status.state
        if self._seed_elapsed and status.elapsed_s:
            self._seed_elapsed = False  # a job picked up again keeps its real age
            self._started = time.monotonic() - max(0.0, float(status.elapsed_s))
        if state == "UNKNOWN":
            self._unknown_polls += 1
            self._unknown_reason = (status.reason or "").strip()
            if self._unknown_since is None:
                self._unknown_since = time.monotonic()
            self._unknown_node = self._node_mismatch(status)
            if self._unknown_polls >= 3 and not self._unknown_reason:
                self._go_offline("the job's state can't be read")
                self._maybe_give_up(self._offline_since)
            elif not self._unknown_node:
                # "gone", "no record", ...: the job may not exist any more, so this
                # is not polled for ever
                self._maybe_give_up(self._unknown_since)
        else:
            self._unknown_polls = 0
            self._unknown_reason = ""
            self._unknown_since = None
            self._unknown_node = None
            self._gave_up = False
            self._show_lost_buttons(False)
            self._offline_since = None
            reason = (status.reason or "").strip()
            self._slurm_state = state
            self._slurm_reason = "" if reason.lower() in ("none", "n/a") else reason
        if state == "PENDING":
            self._poll_timer.setInterval(5000)
            self.progress_bar.setRange(0, 0)
        elif state == "RUNNING":
            self._poll_timer.setInterval(2000)
            self._progress_update()
        elif state in _TERMINAL:
            self._progress_update()
            self._remote_done(status)
            return
        self._tick()

    # an unreadable job state is waited for this long before checking stops
    GIVE_UP_S = 180
    # a job on another login node with no log activity this long gets a hint
    STALE_HINT_S = 300

    @staticmethod
    def _node_mismatch(status):
        """(job node, this node) when an UNKNOWN state is only because this
        connection is on another login node than the job; else None."""
        reason = (status.reason or "")
        low = reason.lower()
        text_match = re.search(
            r"runs on (?:login node )?(\S+?)(?:,| but)\s+(?:this one is|this connection is on)\s+(\S+?)(?:[:;,]|\s|$)",
            reason, re.I)
        if not (getattr(status, "other_node", False) or text_match):
            return None
        job = getattr(status, "node", "") or (text_match.group(1) if text_match else "")
        here = text_match.group(2) if text_match else "another node"
        if not job and "runs on" not in low:
            return None
        return (job or "another node", here)

    def _maybe_give_up(self, since):
        if since is None or self._gave_up or self._state != REMOTE:
            return
        if time.monotonic() - since < self.GIVE_UP_S:
            return
        self._gave_up = True
        self._poll_timer.stop()
        self._show_lost_buttons(True)
        self._log("Checking stopped: the job's state could not be read for %s (%s). The job is still "
                  "listed; Forget job or Keep checking." % (
                      format_duration(time.monotonic() - since),
                      self._unknown_reason or self._offline_why or "no answer"))

    def _show_lost_buttons(self, on):
        self.forget_job_btn.setVisible(bool(on))
        self.keep_btn.setVisible(bool(on))

    def _keep_checking(self):
        if self._state != REMOTE or self._handle is None:
            return
        self._gave_up = False
        self._unknown_since = time.monotonic()
        if self._offline_since is not None:
            self._offline_since = time.monotonic()
        self._show_lost_buttons(False)
        self._log("Checking again.")
        self._poll_timer.start(2000)
        self._tick()
        self._poll()

    def _forget_lost_job(self):
        handle = self._handle
        if self._state != REMOTE or handle is None:
            return
        self._forget_persisted(handle)
        self._finish_fail(
            f"Stopped tracking job {handle.job_id}: its state could not be read "
            f"({self._unknown_reason or self._offline_why or 'no answer'}). If it still runs on the "
            "server, it is not stopped.", cancelled=True)

    def _remote_done(self, status):
        self._poll_timer.stop()
        handle = self._handle
        if status.state == "DONE":
            path = status.result_path or self._runlog.result or handle.out_fits
            self._remote_result = path
            late_stop = self._stop_requested and self._stop_unconfirmed is not None
            if late_stop:
                self._log("The job finished: " + self._unconfirmed_note())
            if self._p["download"]:
                self._download(path)
            else:
                self._forget_persisted(handle)
                self._finish_ok(path, remote=True)
                if late_stop:
                    self._set_status(self.status_label.text() + "\n" + self._unconfirmed_note(),
                                     self.status_label.property("tone"))
        elif status.state == "CANCELLED" and self._stop_requested:
            self._forget_persisted(handle)
            self._finish_fail("Stopped.", cancelled=True)
        elif status.state == "FAILED" and self._stop_requested and self._stop_unconfirmed is None:
            # Stop was pressed and not refused: the engine reporting a failure is
            # what a kill looks like
            self._forget_persisted(handle)
            self._finish_fail("Stopped.", cancelled=True)
        else:
            self._forget_persisted(handle)
            reason = (status.reason or "").strip()
            errors = self._runlog.errors
            hint = failure_hint(reason)
            if hint and not errors:
                why = reason  # the scheduler's own words (TIMEOUT, OUT_OF_MEMORY, ...)
            elif errors:
                why = errors[-1]
                hint = hint or failure_hint(why)
            else:
                why = reason or "see the log"
            lead = "The job was cancelled on the server: " if status.state == "CANCELLED" else "The job failed: "
            text = lead + friendly_job_error(why) + (("\n" + hint) if hint else "")
            if self._stop_requested and self._stop_unconfirmed is not None:
                text += "\n" + self._unconfirmed_note()
            self._finish_fail(text)

    def _unconfirmed_note(self):
        return ("Stop was pressed, but the server never confirmed it (%s), so this is the job's own "
                "final state." % self._stop_unconfirmed)

    def _download(self, remote_path):
        self._remote_result = remote_path
        self._enter(DOWNLOADING)
        self._set_status("Downloading the result…")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # the first bytes can be a while: the connection may be busy
        self._fetch = _Fetch(
            self._remote(), self._conn, self._handle, self._p["download_dir"],
            self._p["saveplot"],
        )
        self._fetch.progress.connect(self._download_progress)
        self._fetch.result.connect(lambda paths: self._downloaded(paths, remote_path))
        self._fetch.failed.connect(self._download_failed)
        self._fetch.start()

    def _download_progress(self, done, total):
        if total and self._state == DOWNLOADING and not self._stopping:
            if self.progress_bar.maximum() == 0:
                self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(int(1000 * done / total))
            self._set_status(f"Downloading: {format_size(done)} of {format_size(total)}")

    def _downloaded(self, paths, remote_path):
        self._fetch = None
        if self._closed:
            return
        self._forget_persisted(self._handle)
        fits = next((p for p in paths if p.lower().endswith((".fits", ".fit", ".fts"))), None)
        for p in paths:
            self._log("Downloaded: " + p)
        self._finish_ok(fits or (paths[0] if paths else remote_path), remote=not fits)

    def _download_failed(self, error):
        self._fetch = None
        if self._closed:
            return
        cancelled = type(error).__name__.lstrip("_") == "Cancelled"
        where = self._remote_result or getattr(self._handle, "out_fits", "")
        self._finish_fail(
            "Download stopped. The result is still on the server: " + where if cancelled
            else f"The result is ready on the server ({where}) but the download failed: {error}",
            cancelled=cancelled,
        )
        self.retry_btn.setVisible(bool(self._handle is not None and self._remote_result))

    def _retry_download(self):
        if self._state != IDLE or self._handle is None or not self._remote_result:
            return
        self.retry_btn.setVisible(False)
        self._final_status = False
        self._p["download_dir"] = self.download_edit.text().strip() or self._p.get("download_dir", "")
        self._log("Retrying the download.")
        self._download(self._remote_result)

    def _stop(self):
        if self._state == STARTING:
            note = ("Cancelled. A job that was already being submitted is being stopped on the server."
                    if self._submitting else "Cancelled before the job started.")
            self._cancel_start(note)
        elif self._state == LOCAL and self._runner is not None:
            self._begin_local_stop()
        elif self._state == REMOTE and self._handle is not None:
            if self._stopping:
                return  # a stop (or its host-key question) is already under way
            reply = QMessageBox.question(
                self, "Stop the job",
                "Stop the job on the server? Nothing already written is kept.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes or self._state != REMOTE:
                return
            self.stop_btn.setEnabled(False)
            self._stopping = True
            self._stop_requested = True
            self._stop_unconfirmed = None
            self.progress_bar.setRange(0, 0)
            self._tick()
            self._cancel_job(self._handle, retried=False)
        elif self._state == DOWNLOADING and self._fetch is not None:
            self._begin_download_stop()

    def _begin_local_stop(self):
        self._stopping = True
        self._stop_requested = True
        self.stop_btn.setEnabled(False)
        self.progress_bar.setRange(0, 0)  # a percentage standing still would mislead
        self._runner.cancel()
        self._tick()

    def _begin_download_stop(self):
        self._fetch.stop = True
        self._stopping = True
        self.stop_btn.setEnabled(False)
        self.progress_bar.setRange(0, 0)
        self._set_status("Stopping the download…")

    def _cancel_job(self, handle, retried):
        remote, conn = self._remote(), self._conn
        task = _Task(lambda: remote.cancel(conn, handle))
        task.result.connect(lambda ok, h=handle: self._stop_answered(h, ok, ""))
        task.failed.connect(lambda e, h=handle, r=retried: self._stop_failed(h, e, r))
        task.start()

    @staticmethod
    def _unknown_key_in(error):
        """The UnknownHostKey behind ``error`` (a RemoteError chains the cause), or None."""
        seen = 0
        while error is not None and seen < 8:
            if isinstance(error, UnknownHostKey):
                return error
            error = error.__cause__ or error.__context__
            seen += 1
        return None

    def _stop_failed(self, handle, error, retried):
        key = self._unknown_key_in(error) if isinstance(error, BaseException) else None
        if key is None or retried or handle is not self._handle or self._state != REMOTE:
            self._stop_answered(handle, False, self._stop_error_text(error, key, retried, handle))
            return
        node = (getattr(handle, "node", "") or key.hostname)
        if not self._confirm_node_key(node, key):
            self._stop_answered(handle, False, self._declined_text(node))
            return
        if handle is not self._handle or self._state != REMOTE:
            return  # the job ended or the window moved on while the question was open: trust nothing
        try:
            trust_host_key(key.hostname, key.key)  # only after the explicit click
        except Exception as e:
            self._stop_answered(handle, False, f"the key of {node} could not be saved: {e}")
            return
        self._log(f"Trusted the host key of {node}; stopping the job there.")
        self._cancel_job(handle, retried=True)

    @staticmethod
    def _declined_text(node):
        return (f"the job runs on login node {node} and its key was not trusted, so it was not "
                f"stopped. It is still tracked: reconnect until this session lands on {node}, or "
                f"connect to {node} directly, then press Stop again")

    def _stop_error_text(self, error, key, retried, handle):
        if key is not None:
            node = getattr(handle, "node", "") or key.hostname
            return (f"the key of {node} ({key.fingerprint}) is still not trusted. It is still tracked: "
                    f"reconnect until this session lands on {node}, or connect to {node} directly, "
                    "then press Stop again")
        return str(error)

    def _node_key_box(self, node, key):
        """The question about an unknown job-node host key, shown like the connection
        dialog shows an unknown server (not yet run)."""
        key_type = key.key.get_name()
        kind = next((k for k in ("ed25519", "ecdsa") if k in key_type), "rsa")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Unknown server")
        box.setText(
            f"SolarViewer does not know the server {key.hostname} yet.\n\n"
            f"Its {key_type} key fingerprint is:\n{key.fingerprint}\n\n"
            "Continue only if this matches the server's fingerprint (on the "
            f"server: ssh-keygen -lf /etc/ssh/ssh_host_{kind}_key.pub).")
        box.setInformativeText(
            f"Why you are asked: the job runs on the login node {node} only, and this connection is "
            f"on another one, so the job can only be stopped by connecting to {node} itself. "
            "Nothing is trusted unless you click the first button.")
        yes = box.addButton("Trust this key and stop the job", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.setDefaultButton(box.buttons()[-1])
        box._trust_button = yes
        return box

    def _confirm_node_key(self, node, key):
        box = self._node_key_box(node, key)
        box.exec_()
        return box.clickedButton() is box._trust_button

    def _stop_answered(self, handle, ok, why):
        if ok or handle is not self._handle or self._state != REMOTE:
            return  # the next poll sees the job end
        self._stopping = False
        self._stop_unconfirmed = why or "no answer"
        self.stop_btn.setEnabled(True)
        text = "Could not confirm that the job stopped" + (f": {why}" if why else "") + "; check it on the server."
        self._log(text)
        self._note = (text, "warning")
        self._tick()

    # --- progress display

    def _progress_update(self):
        log = self._runlog
        if log.total > 0:
            self._tracker.update(log.done, log.total)
        if self._stopping or 0 < log.total <= log.done:
            self.progress_bar.setRange(0, 0)  # what comes now has no percentage
        elif log.total > 0:
            if self.progress_bar.maximum() == 0:
                self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(int(1000 * (self._tracker.fraction() or 0)))
        self._tick()

    def _run_status(self):
        """(text, tone) of a running job, composed from everything known: the
        one place the status line is written while a run is going."""
        remote = self._state == REMOTE
        log = self._runlog
        elapsed = time.monotonic() - self._started
        if self._stopping:
            return f"Stopping… Elapsed {format_clock(elapsed)}", None
        pending = remote and self._slurm_state == "PENDING"
        stage = run_stage_text(log)
        parts = []
        if pending:
            parts.append("Waiting in the Slurm queue" + (f" ({self._slurm_reason})" if self._slurm_reason else ""))
        elif stage:
            parts.append(stage)
        elif log.total > 0:
            parts.append(f"{100 * log.done / log.total:.0f}%")
        elif remote:
            parts.append("Running on the server, preparing the data")
        else:
            parts.append("Starting…")
        parts.append(f"Elapsed {format_clock(elapsed)}")
        if log.total > 0 and not pending:
            eta = self._tracker.eta() if self._tracker else None
            if eta is not None and log.done < log.total:
                parts.append(f"about {format_duration(eta)} left")
        if log.warnings:
            parts.append(f"{len(log.warnings)} warning(s), see the log")
        lines = [" - ".join(parts)]
        tone = None
        if remote and not self._gave_up:
            lines.append("You can close this window; the job stays queued." if pending
                         else self._remote_hint(self._handle))
        if remote and self._unknown_reason:
            if self._unknown_node:
                # the job runs on the other login node: it is not affected by what
                # this connection cannot see
                job, here = self._unknown_node
                lines.append(f"Cannot read the job's state: the job runs on login node {job}, this one "
                             f"is {here}; the job itself is not affected; still checking.")
                idle = time.monotonic() - self._last_progress
                if idle >= self.STALE_HINT_S:
                    lines.append(f"No progress for {int(idle // 60)} min: the log has not changed. "
                                 f"Connect to {job} to see or stop the job.")
            elif self._gave_up:
                lines.append(f"Cannot find the job: {self._unknown_reason}. Checking has stopped; the job "
                             "may be gone. It stays listed until you choose: Forget job, or Keep checking.")
            else:
                lines.append(f"Cannot read the job's state: {self._unknown_reason}. Still checking; the "
                             "job may have ended or been removed.")
            tone = "warning"
        elif remote and self._offline_since is not None:
            gone = time.monotonic() - self._offline_since
            why = f" ({self._offline_why})" if self._offline_why else ""
            if self._gave_up:
                lines.append(f"No answer from the server for {format_duration(gone)}{why}. Checking has "
                             "stopped; the job stays listed until you choose: Forget job, or Keep checking.")
            else:
                lines.append(f"No answer from the server for {format_duration(gone)}{why}; still trying.")
            tone = "warning"
        if self._note:
            lines.append(self._note[0])
            tone = self._note[1] or tone
        return "\n".join(lines), tone

    def _tick(self):
        if self._state == STARTING and self._submitting:
            elapsed = time.monotonic() - self._started
            self._set_status(f"{self._start_text} Elapsed {format_clock(elapsed)}")
        if self._state not in (LOCAL, REMOTE):
            return
        self._set_status(*self._run_status())

    # --- ending

    def _finish_ok(self, path, remote=False):
        self._final_status = True
        self._leave()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(1000)
        self._result_path = None if remote else path
        info = self._runlog.info
        try:
            failed = int(info.get("failed") or 0)
        except (TypeError, ValueError):
            failed = 0
        saved = f"Saved {path}" if not remote else f"The result is on the server: {path}"
        plot = ""
        if (self._p or {}).get("saveplot"):
            plot = plot_path_for(path, remote)
            if not remote and not os.path.exists(plot):
                plot = ""
        if plot:
            saved += f"\nPlot: {plot}"
        if failed:
            names = [str(n) for n in (info.get("failed_inputs") or [])]
            total = info.get("inputs") or info.get("n_inputs") or (self._summary or {}).get("count")
            of = f" of {total}" if total else ""
            text = f"Done - {failed}{of} inputs failed (their columns are left blank).\n{saved}"
            if names:
                text += "\nFailed: " + ", ".join(names[:5]) + (" …" if len(names) > 5 else "")
            text += "\nThe log has the reason for each."
            tone = "warning"
        else:
            text = "Done. " + saved
            tone = "success"
        self._set_status(text, tone)
        self._log(text)
        if failed:
            self.log_view.setVisible(True)
            self.log_toggle.setText("Hide log")
        self.open_btn.setVisible(not remote)
        self.open_btn.setEnabled(not remote)
        if not remote:
            self.open_btn.setFocus()

    def _finish_fail(self, text, cancelled=False):
        self._final_status = True
        self._leave()
        self.progress_bar.setVisible(False)
        self._set_status(text if cancelled else "Failed. " + text, None if cancelled else "error")
        self._log(text)
        if not cancelled:
            self.log_view.setVisible(True)
            self.log_toggle.setText("Hide log")

    # ---------------------------- persistence of jobs ---------------------

    def _persisted(self):
        raw = _get("pending_jobs", "[]")
        try:
            data = json.loads(raw)
            return [d for d in data if isinstance(d, str)]
        except (ValueError, TypeError):
            return []

    def _persist_job(self, handle):
        jobs = [t for t in self._persisted() if _job_id_of(t) != handle.job_id]
        jobs.append(handle.to_json())
        _put("pending_jobs", json.dumps(jobs[-20:]))

    def _forget_persisted(self, handle):
        if handle is None:
            return
        keep = []
        for text in self._persisted():
            try:
                if json.loads(text).get("job_id") == handle.job_id:
                    continue
            except (ValueError, AttributeError):
                continue
            keep.append(text)
        _put("pending_jobs", json.dumps(keep))

    def _offer_resume(self):
        """A job of this server started earlier and not yet collected is offered."""
        remote = self._remote()
        if remote is None or not self._connected():
            return
        host = self._conn.host
        found = []
        for text in self._persisted():
            try:
                h = remote.JobHandle.from_json(text)
            except Exception:
                continue
            if getattr(h, "host", None) == host:
                found.append(h)
        if not found:
            return
        conn = self._conn

        def check():
            try:
                existing = {h.job_id for h in remote.list_jobs(conn)}
            except Exception:
                existing = None
            alive = [h for h in found if existing is None or h.job_id in existing]
            return alive

        task = _Task(check)
        task.result.connect(lambda alive, c=conn: self._resume_found(alive, c))
        task.start()

    def _hide_resume(self):
        self._resume_handle = None
        self.resume_bar.setVisible(False)

    @staticmethod
    def _age_text(handle):
        try:
            age = time.time() - float(handle.submitted_at)
        except (TypeError, ValueError):
            return ""
        if age < 0 or age > 10 * 365 * 86400:
            return ""
        return f", started about {format_duration(age)} ago" if age >= 60 else ", started just now"

    def _resume_found(self, alive, conn=None):
        conn = conn if conn is not None else self._conn
        host = getattr(self._conn, "host", None)
        # an answer for another server, or for a view we have left, is not shown
        if not alive or not self._is_server() or conn is not self._conn or not host:
            return
        alive = [h for h in alive if getattr(h, "host", None) == host]
        if not alive:
            return

        def when(h):
            try:
                return float(h.submitted_at or 0)
            except (TypeError, ValueError):
                return 0.0

        self._resume_handle = max(alive, key=when)
        extra = f" (+{len(alive) - 1} more)" if len(alive) > 1 else ""
        self.resume_label.setText(
            f"A job from an earlier session is on {host}{extra}: "
            f"{os.path.basename(str(self._resume_handle.out_fits))}"
            f"{self._age_text(self._resume_handle)}. Re-attach to follow it and collect the "
            "result, or Forget to drop it from this list."
        )
        self.resume_bar.setVisible(True)

    def _resume(self):
        handle = getattr(self, "_resume_handle", None)
        if handle is None or self._state != IDLE:
            return
        if getattr(handle, "host", None) != getattr(self._conn, "host", None) or not self._connected():
            self._hide_resume()
            return
        self.resume_bar.setVisible(False)
        self._run_token += 1
        self._runlog = RunLog()
        self._tracker = ProgressTracker()
        self._started = time.monotonic()
        self._cancel_requested = False
        self._final_status = False
        self._handle = handle
        self._offset = 0
        self._reset_run_view()
        self._seed_elapsed = True  # Elapsed starts from the job's age on the server
        self._p = {
            "download": self.download_check.isChecked(),
            "download_dir": self.download_edit.text().strip() or default_download_dir(),
            "saveplot": bool(getattr(handle, "out_png", None)),
            "remote": True,
        }
        self._params = {"outfits": handle.out_fits}
        self.log_view.clear()
        self.retry_btn.setVisible(False)
        self.open_btn.setVisible(False)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)
        self._enter(REMOTE)
        self._log(f"Re-attached to job {handle.job_id} on {handle.host}.")
        self._tick()
        self._poll_timer.start(2000)
        self._poll()

    def _forget_job(self):
        handle = getattr(self, "_resume_handle", None)
        self._forget_persisted(handle)
        self._hide_resume()
        self._offer_resume()  # the "+n more" that the bar did not name

    # ---------------------------- viewer / closing ------------------------

    def _open_viewer(self):
        path = self._result_path
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "Open in viewer", "The result file is not available on this computer.")
            return
        try:
            subprocess.Popen(
                viewer_command(self.theme, os.path.abspath(path)), start_new_session=True,
                env=viewer_env(), cwd=_safe_cwd(),
            )
        except Exception as e:
            QMessageBox.critical(self, "Open in viewer", f"Could not start the viewer:\n{e}")
            return
        # A second press would start a second viewer before the first is up
        self.open_btn.setEnabled(False)
        self._viewer_opening = begin_activity("Opening the viewer…", self, blocking=False)
        self._viewer_timer.start(VIEWER_START_MS)

    def _viewer_started(self):
        self._viewer_timer.stop()
        if self._viewer_opening is not None:
            self._viewer_opening.finish()
            self._viewer_opening = None
            self.open_btn.setEnabled(True)

    def reject(self):
        """Esc and the window's close go the same way as the Close button."""
        if self._closing:
            super().reject()
        else:
            self.close()

    def _confirm_close(self):
        """Whether the window may close; what a close has to stop is stopped."""
        if self._state == LOCAL:
            reply = QMessageBox.question(
                self, "Stop and close?",
                "A run is in progress on this computer. Stop it and close?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return False
            if self._state == LOCAL and self._runner is not None:
                runner = self._runner
                self._begin_local_stop()
                return self._waited_for(runner, "Stopping the run…", 6000)
        elif self._state == STARTING:
            # a submit in flight is stopped by its own thread (the token), and
            # kept in the saved list until it is
            self._run_token += 1
        elif self._state == DOWNLOADING and self._fetch is not None:
            fetch = self._fetch
            self._begin_download_stop()
            return self._waited_for(fetch, "Stopping the download…", 3000)
        # a server job is left running; it is in the saved list for next time
        return True

    def _waited_for(self, thread, label, ms):
        """Whether ``thread`` was waited for, the window alive meanwhile. Not
        if Cancel is pressed: the window stays, and says how the stop ends."""
        try:
            run_in_thread(self, label, thread.wait, ms)
        except Cancelled:
            return False
        return True

    def closeEvent(self, event):
        if self._closing:
            event.accept()
            return
        if not self._confirm_close():
            event.ignore()
            return
        self._closed = True
        self._drop_connect_attempt()
        self._scan_token += 1  # late scan answers are dropped
        self._end_local_scan()
        self._poll_timer.stop()
        self._tick_timer.stop()
        self._scan_timer.stop()
        self._scan_clock.stop()
        self._viewer_started()
        self._save_settings()
        self._closing = True
        try:
            super().closeEvent(event)  # (QDialog's calls reject(), which hides)
        finally:
            self._closing = False

    def showEvent(self, event):
        super().showEvent(event)
        if self._closed:  # shown again after a close: timers and state agree again
            self._closed = False
            self._closing = False
            self._poll_busy = False
            if self._scanning:  # the close dropped the scan's answer
                self._scan_soon()
            if self._state in (LOCAL, REMOTE):
                self._tick_timer.start(1000)
            if self._state == REMOTE:
                self._poll_timer.start(2000)
                self._poll()
            elif self._state == STARTING:
                # the start was cancelled by the close (its token is stale)
                self._cancel_start("Cancelled when the window was closed.")
            elif self._state == DOWNLOADING and self._fetch is not None and getattr(self._fetch, "stop", False):
                self._fetch = None
                self._finish_fail("Stopped.", cancelled=True)
            elif self._state == LOCAL and (self._runner is None or not self._runner.isRunning()):
                self._runner = None  # the close stopped the run
                self._finish_fail("Stopped.", cancelled=True)


def main():
    """Main entry point for standalone dialog."""
    import argparse

    from ..ui.app import create_app
    from ..ui.styles import theme_manager

    for key in ["QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"]:
        if key in os.environ:
            del os.environ[key]

    parser = argparse.ArgumentParser(description="Create dynamic spectra")
    parser.add_argument("--theme", choices=["dark", "light"], default=None)
    args = parser.parse_args()

    app = create_app(args.theme)
    dialog = DynamicSpectraDialog(theme=theme_manager.current_theme)
    dialog.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

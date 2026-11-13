"""
Run the dynamic-spectra engine (make_dynamic_spectra.py) on an SSH server.

No Qt here: the dialog drives these functions. They talk through an
SSHConnection (remote/ssh_manager.py), pipe the engine over ssh stdin into
the server's casacore-capable python (usually inside a Singularity/Apptainer
container) and run it either detached on the login node ("direct") or through
Slurm ("slurm").

Safety rules kept throughout: the only things written on the server are a new
job directory under ~/.solarviewer/dynspec_jobs/ and the requested outputs;
nothing is ever deleted, moved or overwritten (the engine itself refuses to
overwrite unless asked); every interpolated value is shell-quoted; a job is
only ever signalled after checking that the process / Slurm job is ours.
"""

import json
import logging
import os
import posixpath
import random
import re
import shlex
import socket
import signal
import string
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import List, Optional

JOBS_SUBDIR = ".solarviewer/dynspec_jobs"
JOB_NAME = "solarviewer-dynspec"
logger = logging.getLogger(__name__)

# Throughput of the engine reading LOFAR-style MS (3.0 GB each, LofarStMan, on
# the cluster's network file system), in input bytes per second per worker
# process, and the number of workers up to which more cpus still help.
# Measured on spider.surfsara.nl:
#  - 244 subband MS through Slurm, 8 cpus: 735.2 GB in 1352.6 s = 543.6 MB/s
#    = about 68 MB/s per cpu, scaling linear up to 8 cpus;
#  - 2 subband MS (6.03 GB) on the login node: ncpu 2 took 41.4 s, ncpu 8
#    took 54.6 s. That run was limited by its two tasks (only two workers can
#    be busy), not by the file system.
# recommend_mode() uses bytes / (THROUGHPUT_BYTES_PER_S_PER_CPU * workers),
# workers = min(cpus, SCALING_CPUS, number of tasks where known).
THROUGHPUT_BYTES_PER_S_PER_CPU = 68e6
SCALING_CPUS = 8
ESTIMATE_FACTOR = 2  # the estimate is rough: reality was within this factor
# A job is "direct" (login node) only below these limits
DIRECT_MAX_BYTES = 10e9
DIRECT_MAX_SECONDS = 300.0
DIRECT_MAX_CPUS = 8

_LOG_CHUNK = 1 << 20  # most log bytes one poll() returns
_CHECK_TIMEOUT = 120
_INSPECT_TIMEOUT = 300


class RuntimeUnavailable(Exception):
    """No working casacore python was found on the server."""

    def __init__(self, message, details="", fixes=None):
        super().__init__(message)
        self.details = details
        self.fixes = list(fixes or [])


class RemoteError(RuntimeError):
    """A remote command failed (the message says how)."""


# ---------------------------------------------------------------- helpers


def _check_text(value, what="value"):
    """``value`` as str, refusing newlines and NUL (never valid in our paths)."""
    value = str(value)
    if "\n" in value or "\r" in value or "\x00" in value:
        raise ValueError("%s must not contain newlines or NUL: %r" % (what, value))
    return value


def _abs_path(value, what="path"):
    value = _check_text(value, what)
    if not value.startswith("/"):
        raise ValueError("%s must be an absolute path on the server: %r" % (what, value))
    return value


def _q(value):
    return shlex.quote(_check_text(value))


_LOCK_GUARD = threading.Lock()
_FALLBACK_LOCK = threading.RLock()


def _conn_lock(conn):
    """The lock that lets one command at a time use ``conn`` (sshd allows few
    sessions per connection, and every command is a session)."""
    lock = getattr(conn, "_dynspec_lock", None)
    if lock is None:
        with _LOCK_GUARD:
            lock = getattr(conn, "_dynspec_lock", None)
            if lock is None:
                lock = threading.RLock()
                try:
                    conn._dynspec_lock = lock
                except Exception:
                    lock = _FALLBACK_LOCK
    return lock


def _close_channels(*streams):
    """Close the channel behind each paramiko stream (never raises): an
    unclosed session channel stays counted against the server's limit."""
    for s in streams:
        channel = getattr(s, "channel", None)
        if channel is not None:
            try:
                channel.close()
            except Exception:
                pass


class _Using:
    """Marks ``conn`` as in use by a long operation (a file transfer): _run()
    must not tear the transport down under it, however its own command fared."""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        with _LOCK_GUARD:
            n = getattr(self.conn, "_dynspec_users", 0)
            try:
                self.conn._dynspec_users = n + 1
            except Exception:
                pass
        return self

    def __exit__(self, *exc):
        with _LOCK_GUARD:
            n = getattr(self.conn, "_dynspec_users", 0)
            try:
                self.conn._dynspec_users = max(0, n - 1)
            except Exception:
                pass
        return False


def _in_use(conn):
    return getattr(conn, "_dynspec_users", 0) > 0


def _is_connection_error(e):
    """Whether ``e`` (from running a command) says the connection is bad: a
    socket / EOF error, or an SSH library error (paramiko's, or our
    SSHConnectionError). A TypeError or ValueError from bad arguments is not."""
    if isinstance(e, (OSError, EOFError)):  # socket.error, ConnectionError, ...
        return True
    for c in type(e).__mro__:
        if c.__module__.split(".")[0] == "paramiko" or c.__name__ == "SSHConnectionError":
            return True
    return False


def _rebuild(conn):
    """Replace the connection's transport (not just test it: the transport can
    look alive while the server refuses new channels). True when ``conn`` is
    usable again. A transport that a file transfer is using is left alone
    (False: the caller reports the failure instead)."""
    if _in_use(conn):
        return False
    lost = getattr(conn, "mark_lost", None)
    if lost is not None:
        try:
            lost()
        except Exception:
            pass
    ensure = getattr(conn, "ensure_connected", None)
    if ensure is None:
        return False
    try:
        return bool(ensure())
    except Exception as e:
        logger.warning("reconnect failed: %s", e)
        return False


def _run(conn, script, timeout=60, stdin_data=None, retry=True):
    """Run ``script`` with sh on the server: (exit status, output bytes with
    stderr merged in).

    One command at a time per connection and its channel is always closed. When
    the command cannot be run (dropped transport, refused or timed-out channel
    open) the transport is rebuilt; with ``retry`` the command is then run once
    more (never with ``retry=False``: the caller cannot know it did not run).
    After a timeout the transport is dropped so that the next call rebuilds it.
    Only genuine connection errors cause a rebuild, and never while a file
    transfer is using the transport."""
    cmd = "sh -c " + shlex.quote("exec 2>&1; " + script)
    lock = _conn_lock(conn)
    if not lock.acquire(timeout=timeout + 5):
        raise TimeoutError("another command is still running on this connection")
    try:
        last = None
        for attempt in range(2 if retry else 1):
            stdin = stdout = None
            try:
                stdin, stdout, _ = conn.exec_command(cmd, timeout=timeout)
                if stdin_data is not None:
                    if isinstance(stdin_data, str):
                        stdin_data = stdin_data.encode("utf-8")
                    stdin.write(stdin_data)
                    stdin.flush()
                stdin.channel.shutdown_write()
                out = stdout.read()
                rc = stdout.channel.recv_exit_status()
                return rc, out
            except socket.timeout as e:
                _close_channels(stdin, stdout)
                lost = getattr(conn, "mark_lost", None)
                if lost is not None and not _in_use(conn):
                    try:
                        lost()
                    except Exception:
                        pass
                raise TimeoutError("remote command timed out after %ss" % timeout) from e
            except Exception as e:
                if not _is_connection_error(e):
                    raise  # bad arguments etc.: rebuilding the transport would not help
                last = e
            finally:
                _close_channels(stdin, stdout)
            usable = _rebuild(conn)
            if attempt == 0 and retry and usable:
                continue
            if not usable:
                why = getattr(conn, "last_error", "")
                if why:
                    last = "%s (reconnect: %s)" % (last, why)
            break
        raise RemoteError("connection to the server failed: %s" % (last,)) from (
            last if isinstance(last, BaseException) else None)
    finally:
        lock.release()


def _text(data):
    return data.decode("utf-8", "replace")


# Directories a container has of its own: binding one of these (or one of
# their immediate children) over it would hide the container's files
_SYSTEM_ROOTS = ("usr", "bin", "sbin", "lib", "lib32", "lib64", "libx32", "etc", "opt",
                 "var", "root", "boot", "run", "srv")


def _bind_ok(root):
    return ":" not in root and "," not in root  # separators in singularity's --bind


def _bind_roots(paths, strict=False):
    """Top two directory levels of absolute ``paths`` (e.g. /project/lofarsw),
    for container --bind. Paths below a system directory (/usr, /opt, ...) get
    three levels bound (their immediate children would hide the container's own
    files), or the path itself when it is shorter; a root containing ':' or ','
    (which singularity would read as separators) falls back to its first level.
    With ``strict`` (the user's input and output paths) a path that cannot be
    covered by any bind raises ValueError instead of being left unbound; without
    it (link targets found on the server) such a path is skipped."""
    roots = []
    for p in paths:
        if not p or not p.startswith("/"):
            continue
        parts = [x for x in p.split("/") if x]
        if not parts or parts[0] in ("tmp", "proc", "dev", "sys"):
            continue
        if parts[0] in _SYSTEM_ROOTS:
            # a bare system directory would hide the container's own files
            candidates = ["/" + "/".join(parts[:3])] if len(parts) >= 2 else []
        else:
            candidates = ["/" + "/".join(parts[:2]), "/" + parts[0]]
        root = next((c for c in candidates if _bind_ok(c)), None)
        if root is None:
            if strict:
                if not candidates:
                    raise ValueError("%s is a system directory of the container and cannot be "
                                     "bound: choose a directory below it" % p)
                raise ValueError(
                    "%s contains ':' or ',', which singularity/apptainer --bind cannot take: "
                    "rename the directory (or give a path without those characters, e.g. a "
                    "symbolic link to it)" % p)
            continue
        if root not in roots:
            roots.append(root)
    return roots


def _real_binds(conn, paths):
    """Bind roots of ``paths`` and of where symbolic links in them (or the
    paths themselves, or the entries of a directory) point: a container only
    sees what is bound, and links often lead to another file system."""
    paths = [p for p in paths if p]
    roots = _bind_roots(paths, strict=True)
    if not paths:
        return roots
    script = "; ".join(
        "readlink -f -- %(p)s; for f in %(p)s/*; do [ -L \"$f\" ] && readlink -f -- \"$f\"; done" % {"p": shlex.quote(p)}
        for p in paths)
    try:
        rc, out = _run(conn, "(%s) 2>/dev/null | head -n 500" % script, timeout=60)
    except (TimeoutError, RemoteError):
        return roots
    for r in _bind_roots(_text(out).splitlines()):
        if r not in roots:
            roots.append(r)
    return roots


def _home(conn):
    sftp = conn.open_sftp()
    try:
        return sftp.normalize(".")
    finally:
        sftp.close()


# ---------------------------------------------------------------- runtime


@dataclass
class Runtime:
    """How to start a casacore python on the server."""

    kind: str = "python"  # "container" or "python"
    container: Optional[str] = None
    python: str = "python3"
    binds: List[str] = field(default_factory=list)
    tool: str = ""  # "apptainer" or "singularity" (container only)

    def command(self, extra_binds=()):
        """argv prefix; append "-" (script on stdin) and the engine arguments."""
        if self.kind != "container":
            return [self.python]
        binds = []
        for b in list(self.binds) + list(extra_binds):
            if b and b not in binds:
                binds.append(b)
        argv = [self.tool or "singularity", "exec"]
        for b in binds:
            argv += ["--bind", b]
        return argv + [self.container, self.python or "python3"]

    def describe(self):
        if self.kind == "container":
            return "%s (%s)" % (self.container, self.tool or "singularity")
        return self.python


_CHECK_SCRIPT = r'''
import os, sys
try:
    import numpy
    import astropy.io.fits
    import astropy.time
    import casacore.tables as ct
    import casacore.quanta as q
    q.quantity("1km").get_value("m")
    probe = sys.argv[1] if len(sys.argv) > 1 else ""
    if probe:
        if not os.path.exists(os.path.join(probe, "table.dat")) and os.path.isdir(probe):
            for n in sorted(os.listdir(probe)):
                if n.lower().endswith(".ms") and os.path.exists(os.path.join(probe, n, "table.dat")):
                    probe = os.path.join(probe, n)
                    break
        t = ct.table(os.path.join(probe, "SPECTRAL_WINDOW"), ack=False, readonly=True)
        t.nrows()
        t.close()
    print("SVCHECK OK numpy=%s python=%s" % (numpy.__version__, sys.version.split()[0]))
except BaseException as e:
    print("SVCHECK FAIL %s: %s" % (type(e).__name__, str(e).replace(chr(10), " ")[:300]))
    sys.exit(1)
'''


def _classify_failure(label, text, is_container):
    """(detail line, [fixes]) for the output of a failed candidate check."""
    low = text.lower()
    tail = " ".join(text.strip().splitlines()[-3:])[:300]
    if "svcheck nofile" in low:
        return ("%s: file not readable" % label,
                ["Check the path and that your account can read it: %s" % label])
    if "_array_api" in low or "multiarray failed" in low or "numpy.core" in low:
        return ("%s: numpy/casacore ABI mismatch (%s)" % (label, tail),
                ["numpy/casacore ABI mismatch: python-casacore was built against a different numpy major "
                 "version: use the Singularity container that ships casacore, or "
                 "install a matching pair (e.g. pip install 'numpy<2' python-casacore)"])
    if "no module named 'casacore'" in low or "no module named casacore" in low:
        return ("%s: python-casacore is not installed (%s)" % (label, tail),
                ["Use a container image with python-casacore, or install it "
                 "(pip install python-casacore)"])
    if "no module named 'astropy'" in low or "no module named astropy" in low:
        return ("%s: astropy is not installed (%s)" % (label, tail),
                ["The engine writes the FITS file with astropy: use a container image or "
                 "python that has it, or install it (pip install astropy)"])
    if "svcheck untrusted" in low:
        return ("%s: not run (not owned by you or root, or writable by others)" % label,
                ["Give the image explicitly (the Container field) or set SOLARVIEWER_SIF: "
                 "images found in a directory are only run when owned by you or root and "
                 "not writable by group or others"])
    if "no module named 'numpy'" in low:
        return ("%s: numpy is not installed" % label, ["Install numpy in that python"])
    if "no such file" in low or "not found" in low:
        return ("%s: not found (%s)" % (label, tail),
                ["Check the path of %s" % label])
    return ("%s: failed (%s)" % (label, tail or "no output"), [])


def detect_runtime(conn, probe_ms=None, hints=(), on_candidate=None):
    """First WORKING runtime on the server. A candidate counts only if it
    imports casacore and opens a real table (``probe_ms``/SPECTRAL_WINDOW, or
    calls into the casacore library when no probe is given).

    Order: ``hints`` (container .sif files or python executables / virtualenv
    directories, given by the user), $SOLARVIEWER_SIF, the .sif files of the
    directories listed in $SOLARVIEWER_SIF_DIRS (listed, never searched; only
    run when owned by you or root and not writable by group/others), then bare
    python3. Nothing is discovered by scanning the data directories: an image
    found there would be run with your rights.

    on_candidate(path, n, total), if given, is called before each candidate is
    tried: starting a container on a shared node can take minutes."""
    probe = _abs_path(probe_ms, "probe_ms") if probe_ms else ""
    rc, out = _run(
        conn,
        'printf "%s\\n" "$HOME" "$SOLARVIEWER_SIF" "$SOLARVIEWER_SIF_DIRS"; '
        "command -v apptainer; command -v singularity",
        timeout=30,
    )
    lines = _text(out).split("\n")
    lines += [""] * 5
    home, env_sif, env_dirs = lines[0], lines[1].strip(), lines[2].strip()
    tools = [x.strip() for x in lines[3:5] if x.strip().startswith("/")]
    tool = posixpath.basename(tools[0]) if tools else ""

    def expand(p):
        p = p.strip()
        if p == "~":
            return home
        if p.startswith("~/"):
            return posixpath.join(home, p[2:])
        return p

    candidates = []  # (kind, path, must be owned by us/root and not writable by others)

    def add(kind, path, vet=False):
        if path and not any(c[:2] == (kind, path) for c in candidates):
            candidates.append((kind, path, vet))

    for h in hints:
        h = expand(_check_text(h, "hint"))
        if h.lower().endswith((".sif", ".simg", ".img")):
            add("container", h)
        else:
            add("python", h)
    if env_sif:
        add("container", expand(env_sif))
    dirs = [expand(d) for d in env_dirs.split(":") if d.strip()]
    for d in dirs:
        if "\n" in d or "\x00" in d:
            continue
        rc, out = _run(conn, "ls -1 -- %s 2>/dev/null | head -n 200" % shlex.quote(d), timeout=60)
        for name in _text(out).split("\n"):
            if name.endswith(".sif"):
                add("container", posixpath.join(d, name), vet=True)
    add("python", "python3")

    binds = _real_binds(conn, [probe] if probe else [])
    details, fixes = [], []
    for number, (kind, path, vet) in enumerate(candidates, 1):
        if on_candidate is not None:
            on_candidate(path, number, len(candidates))
        if kind == "container":
            if not tool:
                details.append("%s: no singularity/apptainer on PATH" % path)
                if "Ask the administrators for singularity/apptainer, or give a python with casacore" not in fixes:
                    fixes.append("Ask the administrators for singularity/apptainer, "
                                 "or give a python with casacore")
                continue
            rt = Runtime("container", path, "python3", binds, tool)
            argv = rt.command()
            q = shlex.quote(path)
            vetting = ""
            if vet:  # found by listing a directory: only run what only we could have put there
                vetting = (
                    "{ [ -O %(q)s ] || [ \"$(stat -c %%u -- %(q)s 2>/dev/null)\" = 0 ]; } && "
                    "case \"$(ls -ldL -- %(q)s | cut -c1-10)\" in ?????w????|????????w?) false;; esac "
                    "|| { echo SVCHECK UNTRUSTED; exit 5; }; " % {"q": q})
            script = "[ -r %s ] || { echo SVCHECK NOFILE; exit 4; }; %s%s - %s" % (
                q, vetting, " ".join(shlex.quote(a) for a in argv), shlex.quote(probe))
        else:
            py = path
            if py != "python3":
                # a virtualenv directory means its bin/python
                script0 = "[ -d %s ] && echo DIR" % shlex.quote(py)
                rc, o = _run(conn, script0, timeout=30)
                if b"DIR" in o:
                    py = posixpath.join(py, "bin", "python")
                elif not posixpath.basename(py).startswith("python"):
                    details.append("%s: not a python executable or virtualenv directory" % py)
                    continue
            rt = Runtime("python", None, py, binds, tool)
            script = "[ -x \"$(command -v %s)\" ] || { echo 'SVCHECK NOFILE'; exit 4; }; %s - %s" % (
                shlex.quote(py), shlex.quote(py), shlex.quote(probe))
        try:
            rc, out = _run(conn, script, timeout=_CHECK_TIMEOUT, stdin_data=_CHECK_SCRIPT)
        except (TimeoutError, RemoteError) as e:
            details.append("%s: %s" % (path, e))
            continue
        text = _text(out)
        if rc == 0 and "SVCHECK OK" in text:
            return rt
        d, f = _classify_failure(path, text, kind == "container")
        details.append(d)
        for x in f:
            if x not in fixes:
                fixes.append(x)
    if not fixes:
        fixes.append("Give the path of a Singularity/Apptainer .sif with python-casacore "
                     "(or set SOLARVIEWER_SIF on the server)")
    raise RuntimeUnavailable(
        "No python with a working casacore was found on the server",
        details="\n".join(details), fixes=fixes)


# ---------------------------------------------------------------- slurm


@dataclass
class SlurmOptions:
    partition: Optional[str] = None
    cpus: int = 4
    mem_gb: Optional[int] = 8
    time_limit: str = "02:00:00"
    extra: List[str] = field(default_factory=list)


def list_partitions(conn):
    """[(name, time limit, state)] from sinfo; [] where there is no Slurm."""
    try:
        rc, out = _run(conn, "sinfo -h -o '%P|%l|%a' 2>/dev/null", timeout=30)
    except (TimeoutError, RemoteError):
        return []
    result, seen = [], set()
    if rc != 0:
        return []
    for line in _text(out).splitlines():
        parts = line.strip().split("|")
        if len(parts) != 3 or not parts[0]:
            continue
        name = parts[0].rstrip("*")
        if name not in seen:
            seen.add(name)
            result.append((name, parts[1], parts[2]))
    return result


# ---------------------------------------------------------------- inspect


def _path_args(paths_or_dir):
    if isinstance(paths_or_dir, (list, tuple)):
        paths = [_abs_path(p, "MS path") for p in paths_or_dir]
        if not paths:
            raise ValueError("no input given")
        return ["--ms"] + paths, paths
    p = _abs_path(paths_or_dir, "input directory")
    return ["--indir=" + p], [p]


def _svds_lines(text, tag):
    prefix = "SVDS %s " % tag
    return [l[len(prefix):] for l in text.splitlines() if l.startswith(prefix)]


def inspect_remote(conn, runtime, engine_source, paths_or_dir):
    """Summary dict of the input MS on the server (engine --list)."""
    args, paths = _path_args(paths_or_dir)
    argv = runtime.command(extra_binds=_real_binds(conn, paths)) + ["-", "--list"] + args
    rc, out = _run(conn, " ".join(shlex.quote(a) for a in argv), timeout=_INSPECT_TIMEOUT,
                   stdin_data=engine_source)
    text = _text(out)
    errors = _svds_lines(text, "ERROR")
    summaries = _svds_lines(text, "SUMMARY")
    if summaries:
        try:
            return json.loads(summaries[-1])
        except ValueError as e:
            raise RemoteError("unreadable summary from the server: %s" % e)
    if errors:
        raise RemoteError(errors[-1])
    low = text.lower()
    if "permission denied" in low:
        raise RemoteError("permission denied reading the input on the server")
    if "no such file" in low:
        raise RemoteError("input path not found on the server")
    said = [l for l in text.strip().splitlines() if not l.startswith("SVDS ")]
    raise RemoteError("inspection failed (exit %s): %s" % (
        rc, " ".join(said[-3:])[:400] or "no output"))


def _summary_bytes(summary):
    for key in ("total_bytes", "bytes"):
        if isinstance(summary.get(key), (int, float)):
            return float(summary[key])
    totals = summary.get("totals")
    if isinstance(totals, dict):
        for key in ("bytes", "total_bytes"):
            if isinstance(totals.get(key), (int, float)):
                return float(totals[key])
    for key in ("ms", "files", "inputs"):
        items = summary.get(key)
        if isinstance(items, list):
            return float(sum(i.get("bytes", 0) for i in items if isinstance(i, dict)))
    return 0.0


def _summary_tasks(summary):
    """Number of independent tasks (one per MS) the summary shows, or None.
    A single MS counts as 2: its row blocks can be read by two workers."""
    count = summary.get("count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        for key in ("ms", "files", "inputs"):
            if isinstance(summary.get(key), list) and summary[key]:
                count = len(summary[key])
                break
        else:
            return None
    return max(count, 2)


def recommend_mode(summary, user_cpus=None):
    """(mode, reason, estimated seconds). "direct" (login node) only for small
    jobs: <= DIRECT_MAX_BYTES of input and an estimate <= DIRECT_MAX_SECONDS on
    <= DIRECT_MAX_CPUS cpus; otherwise "slurm". The estimate is rough (see
    ESTIMATE_FACTOR) and does not include the Slurm queue and start-up time."""
    nbytes = _summary_bytes(summary)
    cpus = max(1, int(user_cpus or DIRECT_MAX_CPUS))
    tasks = _summary_tasks(summary)
    gb = nbytes / 1e9

    def estimate(n):
        workers = min(n, SCALING_CPUS, tasks or n)
        return nbytes / (THROUGHPUT_BYTES_PER_S_PER_CPU * workers)

    direct_cpus = min(cpus, DIRECT_MAX_CPUS)
    est = estimate(direct_cpus)
    rough = "rough estimate, +-%dx" % ESTIMATE_FACTOR
    if nbytes <= DIRECT_MAX_BYTES and est <= DIRECT_MAX_SECONDS:
        return ("direct", "%.1f GB of input, about %d s on %d cpus (%s): small enough "
                "for the login node" % (gb, round(est), direct_cpus, rough), est)
    est = estimate(cpus)
    return ("slurm", "%.1f GB of input, about %d min on %d cpus (%s, without the time "
            "waiting in the queue and about a minute to start): submit it to the "
            "Slurm queue instead of loading the login node"
            % (gb, round(est / 60), cpus, rough), est)


# ---------------------------------------------------------------- jobs


@dataclass
class JobHandle:
    job_id: str
    mode: str
    remote_dir: str
    log_path: str
    out_fits: str
    out_png: Optional[str]
    pid_or_slurm_id: str  # direct: "<pid>:<start ticks>"; slurm: the job id
    host: str
    submitted_at: float  # server clock, seconds since the epoch
    # direct jobs: the login node the job runs on (``hostname`` there). The
    # server may be a load-balanced pair of nodes (every new connection can land
    # on either) while only the job's own node has its process. "" when not
    # known (handles made by older versions, Slurm jobs).
    node: str = ""

    def to_json(self):
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text):
        data = json.loads(text) if isinstance(text, str) else dict(text)
        names = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in names})


@dataclass
class JobStatus:
    state: str = "UNKNOWN"
    done: int = 0
    total: int = 0
    new_lines: List[str] = field(default_factory=list)
    offset: int = 0
    reason: str = ""
    elapsed_s: float = 0.0
    result_path: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    # direct jobs: the login node the job runs on, and whether this connection
    # is on another one (then the process cannot be looked at from here: the
    # state comes from the files on the shared file system only)
    node: str = ""
    other_node: bool = False


# Every engine option a params dict can carry -> the engine's flag. Complete
# against make_dynamic_spectra's argparse (a test checks it); only --showplot,
# --list and --maxrows are deliberately not forwarded.
ENGINE_OPTIONS = {
    "indir": "--indir",
    "ms": "--ms",
    "outfits": "--outfits",
    "binwidth": "--binwidth",
    "ncpu": "--ncpu",
    "uvmin": "--uvmin",
    "uvmax": "--uvmax",
    "startfreq": "--startfreq",
    "endfreq": "--endfreq",
    "tstart": "--tstart",
    "tend": "--tend",
    "pol": "--pol",
    "freqres": "--freqres",
    "chanavg": "--chanavg",
    "chunkmb": "--chunkmb",
    "datacolumn": "--datacolumn",
    "uvstep": "--uvstep",
    "autocorr": "--autocorr",
    "saveplot": "--saveplot",
    "plotfile": "--plotfile",
    "overwrite": "--overwrite",
}
# options that take a value and are sent when the params have one (0 counts:
# uvmin/uvmax 0 mean "no limit" to the engine)
_VALUE_OPTIONS = ("binwidth", "ncpu", "uvmin", "uvmax", "startfreq", "endfreq", "tstart",
                  "tend", "pol", "freqres", "chanavg", "chunkmb", "datacolumn", "uvstep")


def _engine_args(params, overwrite, ncpu):
    """(Engine CLI argument list (unquoted), output FITS, output PNG or None)."""
    p = dict(params)
    out_fits = _abs_path(p.get("outfits"), "outfits")
    args = []
    if p.get("ms"):
        args += [ENGINE_OPTIONS["ms"]] + [_abs_path(m, "MS path") for m in p["ms"]]
    elif p.get("indir"):
        args.append(ENGINE_OPTIONS["indir"] + "=" + _abs_path(p["indir"], "indir"))
    else:
        raise ValueError("params need 'indir' or 'ms'")
    args.append(ENGINE_OPTIONS["outfits"] + "=" + out_fits)
    p["ncpu"] = ncpu
    for name in _VALUE_OPTIONS:
        v = p.get(name)
        if v is None or v == "":
            continue
        if name == "chanavg":
            # None/0 is "auto"; 1 is what the dialog shows for "no averaging
            # requested". The engine reads any explicit factor as "no cap".
            try:
                if int(v) <= 1:
                    continue
            except (TypeError, ValueError):
                raise ValueError("chanavg must be a whole number: %r" % (v,))
        args.append("%s=%s" % (ENGINE_OPTIONS[name], _check_text(v, name)))
    if p.get("autocorr"):
        args.append(ENGINE_OPTIONS["autocorr"])
    out_png = None
    if p.get("saveplot"):
        out_png = _abs_path(p["plotfile"], "plotfile") if p.get("plotfile") \
            else posixpath.splitext(out_fits)[0] + ".png"
        args += [ENGINE_OPTIONS["saveplot"], ENGINE_OPTIONS["plotfile"] + "=" + out_png]
    if overwrite:
        args.append(ENGINE_OPTIONS["overwrite"])
    return args, out_fits, out_png


def _new_job_id(host):
    host = re.sub(r"[^A-Za-z0-9]+", "", host)[:20] or "host"
    rand = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))
    return "%s-%s-%s" % (host, time.strftime("%Y%m%dT%H%M%S"), rand)


_TIME_RE = re.compile(r"^(\d{1,3}-)?\d{1,3}:\d{2}:\d{2}$")
_PART_RE = re.compile(r"^[A-Za-z0-9_.,-]+$")
_SBATCH_ID_RE = re.compile(r"^(\d+)(;.*)?$")


def _sftp_exists(sftp, path):
    try:
        sftp.stat(path)
        return True
    except (IOError, OSError):
        return False


def _write_new(sftp, path, data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    f = sftp.open(path, "wb")
    try:
        f.write(data)
    finally:
        f.close()


def _write_files(conn, files):
    """Write {path: data} as new files, one retry over a fresh SFTP session."""
    last = None
    for _ in range(2):
        try:
            sftp = conn.open_sftp()
            try:
                for path, data in files.items():
                    _write_new(sftp, path, data)
            finally:
                sftp.close()
            return None
        except Exception as e:
            last = e
    return last


def _server_cpus(conn):
    """Cpus this account can use on the server's login node (nproc honours
    the affinity mask; OMP_* variables of the login shell are ignored), or 2
    when it cannot be told."""
    try:
        rc, out = _run(conn, "env -u OMP_NUM_THREADS -u OMP_THREAD_LIMIT nproc 2>/dev/null "
                       "|| nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null", timeout=30)
        n = int(_text(out).split()[0])
        return n if n >= 1 else 2
    except Exception:
        return 2


def _slurm_job_name(job_id):
    return "%s-%s" % (JOB_NAME, job_id)


def _adopt_direct(conn, jobdir):
    """(pid, start ticks, server time, node) of a detached job that did start in
    ``jobdir`` (its run.sh writes the node, start ticks and pid, the pid last),
    or None. The values come from those files: they are the job's own, whichever
    login node this connection landed on."""
    script = (
        "cd %s 2>/dev/null || exit 0; p=$(cat pid 2>/dev/null); "
        "printf '@@NOW %%s\\n' \"$(date +%%s)\"; printf '@@PID %%s\\n' \"$p\"; "
        "printf '@@NODE %%s\\n' \"$(cat node 2>/dev/null)\"; "
        "printf '@@START %%s\\n' \"$(cat start 2>/dev/null)\"; "
        "printf '@@CUR %%s\\n' \"$(%s)\"; "
        "[ -n \"$p\" ] && printf '@@STAT %%s\\n' \"$(cat /proc/$p/stat 2>/dev/null)\"; exit 0"
    ) % (_q(jobdir), _HOSTNAME)
    rc, out = _run(conn, script, timeout=60)
    info = _parse_sections(out)[0]
    pid = info.get("PID", "").strip()
    if not pid.isdigit():
        return None
    node = info.get("NODE", "").strip() or info.get("CUR", "").strip()
    start = info.get("START", "").strip()
    if not start.isdigit():  # a run.sh without the file: only this node can tell
        fields = _stat_fields(info.get("STAT", ""))
        start = fields["start"] if fields else "0"
    return pid, start, float(info.get("NOW") or 0), node


def _adopt_slurm(conn, job_id, jobdir):
    """(Slurm job id, server time) of the job submitted under this client's
    ``job_id`` (carried in its job name), or None."""
    name = _slurm_job_name(job_id)
    script = (
        "printf '@@NOW %%s\\n' \"$(date +%%s)\"; u=$(id -un); "
        "id=$(squeue -h -u \"$u\" -n %(n)s -o %%i 2>/dev/null | head -n 1); "
        "[ -n \"$id\" ] || id=$(sacct -n -P -X -u \"$u\" --name=%(n)s -o JobID 2>/dev/null | head -n 1); "
        "[ -n \"$id\" ] || id=$(ls -1 %(d)s 2>/dev/null | sed -n 's/^slurm-\\([0-9][0-9]*\\)\\.log$/\\1/p' | head -n 1); "
        "printf '@@SID %%s\\n' \"$id\"; exit 0"
    ) % {"n": shlex.quote(name), "d": _q(jobdir)}
    rc, out = _run(conn, script, timeout=60)
    info = _parse_sections(out)[0]
    sid = info.get("SID", "").strip()
    if not sid.isdigit():
        return None
    return sid, float(info.get("NOW") or 0)


def _nice_prefix_script():
    """sh lines that set $NICE to nice/ionice (best effort, whichever exist)."""
    return (
        "NICE=\n"
        "command -v nice >/dev/null 2>&1 && NICE=\"nice -n 10\"\n"
        "if command -v ionice >/dev/null 2>&1 && ionice -c3 true >/dev/null 2>&1; "
        "then NICE=\"$NICE ionice -c3\"; fi\n"
    )


def submit(conn, runtime, engine_source, params, mode="direct", slurm=None, overwrite=False):
    """Start a job on the server and return its JobHandle.

    ``params`` are the engine options (see ENGINE_OPTIONS); a missing or zero
    chanavg is not sent (engine auto). A direct job always gets an explicit
    ncpu (the given one, or the server's cpus, at most DIRECT_MAX_CPUS) and runs
    under nice/ionice. If the launch cannot be confirmed (connection lost,
    timeout) the server is searched for a job with this client's job id: one
    found is adopted and returned, never started a second time."""
    if mode not in ("direct", "slurm"):
        raise ValueError("mode must be 'direct' or 'slurm'")
    slurm = slurm or SlurmOptions()
    if mode == "slurm":
        if not _TIME_RE.match(str(slurm.time_limit)):
            raise ValueError("bad time limit: %r" % (slurm.time_limit,))
        if slurm.partition and not _PART_RE.match(slurm.partition):
            raise ValueError("bad partition name: %r" % (slurm.partition,))
        cpus = int(slurm.cpus)
        if cpus < 1:
            raise ValueError("cpus must be >= 1")
        if slurm.mem_gb is not None and int(slurm.mem_gb) < 1:
            raise ValueError("mem_gb must be >= 1")
        extra = [_check_text(x, "slurm option") for x in slurm.extra]
        ncpu = cpus
    else:
        ncpu = int(params.get("ncpu") or 0)
        _abs_path(params.get("outfits"), "outfits")  # (validated before the server is asked)
        available = _server_cpus(conn)
        ncpu = min(ncpu, available, DIRECT_MAX_CPUS) if ncpu > 0 else min(DIRECT_MAX_CPUS, available)
    args, out_fits, out_png = _engine_args(params, overwrite, ncpu)
    inputs = list(params.get("ms") or [params.get("indir")])
    binds = _real_binds(conn, inputs + [posixpath.dirname(out_fits)] + ([posixpath.dirname(out_png)] if out_png else []))
    argv = runtime.command(extra_binds=binds) + ["-"] + args
    engine_cmd = " ".join(shlex.quote(a) for a in argv)

    sftp = conn.open_sftp()
    try:
        out_dir = posixpath.dirname(out_fits)
        if not _sftp_exists(sftp, out_dir):
            raise FileNotFoundError("output directory does not exist on the server: %s" % out_dir)
        for existing in (out_fits, out_png):
            if existing and _sftp_exists(sftp, existing) and not overwrite:
                raise FileExistsError("output file already exists on the server: %s" % existing)
        home = sftp.normalize(".")
        base = posixpath.join(home, ".solarviewer")
        jobs = posixpath.join(base, "dynspec_jobs")
        for d in (base, jobs):
            if not _sftp_exists(sftp, d):
                sftp.mkdir(d, 0o700)
        job_id = _new_job_id(conn.host)
        jobdir = posixpath.join(jobs, job_id)
        if _sftp_exists(sftp, jobdir):
            raise FileExistsError("job directory already exists: %s" % jobdir)
        sftp.mkdir(jobdir, 0o700)
        _write_new(sftp, posixpath.join(jobdir, "engine.py"), engine_source)
        engine_py = posixpath.join(jobdir, "engine.py")
        if mode == "direct":
            log = posixpath.join(jobdir, "log.txt")
            run_sh = (
                "#!/bin/sh\n"
                "# SolarViewer dynamic spectra job %s\n"
                "cd %s || exit 1\n"
                "(%s) > node 2>/dev/null\n"
                "s=$(cat /proc/$$/stat 2>/dev/null); set -- ${s##*)}; echo \"${20}\" > start\n"
                "echo $$ > pid\n"
                "%s"
                "PYTHONUNBUFFERED=1 $NICE %s < %s >> %s 2>&1\n"
                "rc=$?\n"
                "echo $rc > exitcode.tmp && mv exitcode.tmp exitcode\n"
                "exit $rc\n"
            ) % (job_id, _q(jobdir), _HOSTNAME, _nice_prefix_script(), engine_cmd, _q(engine_py), _q(log))
        else:
            run_sh = (
                "#!/bin/sh\n"
                "# SolarViewer dynamic spectra job %s\n"
                "export PYTHONUNBUFFERED=1\n"
                "%s < %s\n"
            ) % (job_id, engine_cmd, _q(engine_py))
        _write_new(sftp, posixpath.join(jobdir, "run.sh"), run_sh)
        # the job's identity, before anything runs: a launch that cannot be
        # confirmed leaves this (and the pid file / the Slurm job name) behind
        _write_new(sftp, posixpath.join(jobdir, "submitting.json"),
                   json.dumps({"job_id": job_id, "mode": mode, "out_fits": out_fits,
                               "out_png": out_png, "host": conn.host}))
    finally:
        sftp.close()

    run_path = posixpath.join(jobdir, "run.sh")
    adopted = None
    failure = None
    node = ""
    if mode == "direct":
        script = (
            "cd %s && (setsid nohup sh %s </dev/null >/dev/null 2>&1 &); "
            "i=0; while [ ! -s pid ] && [ $i -lt 50 ]; do sleep 0.1; i=$((i+1)); done; "
            "printf '@@NOW %%s\\n' \"$(date +%%s)\"; printf '@@PID %%s\\n' \"$(cat pid 2>/dev/null)\"; "
            "printf '@@NODE %%s\\n' \"$(cat node 2>/dev/null)\"; "
            "printf '@@START %%s\\n' \"$(cat start 2>/dev/null)\"; "
            "printf '@@CUR %%s\\n' \"$(%s)\"; "
            "printf '@@STAT %%s\\n' \"$(cat /proc/$(cat pid 2>/dev/null)/stat 2>/dev/null)\""
        ) % (_q(jobdir), _q(run_path), _HOSTNAME)
        log_path = log
        try:
            rc, out = _run(conn, script, timeout=60, retry=False)
            info = _parse_sections(out)[0]
            pid = info.get("PID", "").strip()
            if not pid.isdigit():
                failure = RemoteError("the job did not start: %s" % _text(out)[-300:])
                adopted = None
            else:
                start = info.get("START", "").strip()
                if not start.isdigit():
                    fields = _stat_fields(info.get("STAT", ""))
                    start = fields["start"] if fields else "0"
                adopted = (pid, start, float(info.get("NOW", "0") or 0),
                           info.get("NODE", "").strip() or info.get("CUR", "").strip())
        except (TimeoutError, RemoteError) as e:
            adopted = _find_started(lambda: _adopt_direct(conn, jobdir), e)
            failure = RemoteError("could not start the job: %s; no job with id %s was found "
                                  "on the server" % (e, job_id))
        if adopted:
            handle_id, submitted, node = "%s:%s" % (adopted[0], adopted[1]), adopted[2], adopted[3]
    else:
        cmd = ["sbatch", "--parsable", "--job-name=" + _slurm_job_name(job_id),
               "--chdir=" + jobdir, "--output=" + posixpath.join(jobdir, "slurm-%j.log"),
               "--cpus-per-task=%d" % cpus, "--ntasks=1", "--time=" + slurm.time_limit]
        if slurm.mem_gb is not None:
            cmd.append("--mem=%dG" % int(slurm.mem_gb))
        if slurm.partition:
            cmd.append("--partition=" + slurm.partition)
        cmd += extra + [run_path]
        script = "date +%%s; %s" % " ".join(shlex.quote(c) for c in cmd)
        try:
            rc, out = _run(conn, script, timeout=60, retry=False)
            lines = _text(out).strip().splitlines()
            # the id is the first line of digits after the time (warnings from
            # sbatch or a site wrapper may come before or after it)
            sid = next((m.group(1) for m in map(_SBATCH_ID_RE.match, (l.strip() for l in lines[1:])) if m), "")
            if rc != 0 or not sid:
                failure = RemoteError("sbatch failed: %s" % " ".join(lines[1:])[:400])
                if rc == 0 or sid:  # the output is unclear: the job may be queued
                    adopted = _find_started(lambda: _adopt_slurm(conn, job_id, jobdir), failure)
            else:
                adopted = (sid, float(lines[0]) if lines[0].isdigit() else 0.0)
        except (TimeoutError, RemoteError) as e:
            adopted = _find_started(lambda: _adopt_slurm(conn, job_id, jobdir), e)
            failure = RemoteError("could not submit the job: %s; no job with id %s was found "
                                  "on the server" % (e, job_id))
        if adopted:
            handle_id, submitted = adopted[0], adopted[1]
            log_path = posixpath.join(jobdir, "slurm-%s.log" % handle_id)
    if not adopted:
        raise failure

    handle = JobHandle(job_id, mode, jobdir, log_path, out_fits, out_png, handle_id,
                       conn.host, submitted, node)
    err = _write_files(conn, {posixpath.join(jobdir, "job.json"): handle.to_json()})
    if err is not None:  # the job runs; only list_jobs() after a restart misses it
        logger.warning("could not write job.json in %s: %s", jobdir, err)
    return handle


def _find_started(lookup, failure):
    """What ``lookup`` finds of a job whose launch ``failure`` left uncertain.
    None when the server confirms there is none; a RemoteError (not a blind
    retry) when the server cannot be asked."""
    try:
        return lookup()
    except (TimeoutError, RemoteError) as e:
        raise RemoteError(
            "could not tell whether the job started (%s); then the server could not be "
            "asked either (%s). Check the Jobs list before submitting again" % (failure, e)) from e


# ---------------------------------------------------------------- status


def _parse_sections(data):
    """({tag: value}, log bytes) from a poll/launch script's output."""
    marker = b"@@LOG\n"
    log = b""
    if data.startswith(marker):
        head, log = b"", data[len(marker):]
    elif b"\n" + marker in data:
        head, log = data.split(b"\n" + marker, 1)
    else:
        head = data
    info = {}
    for line in _text(head).splitlines():
        if line.startswith("@@"):
            tag, _, value = line[2:].partition(" ")
            info[tag] = value
    return info, log


def _stat_fields(stat_text):
    """{"start", "pgrp", "sid", "state"} from the text of /proc/PID/stat."""
    if ")" not in stat_text:
        return None
    rest = stat_text.rsplit(")", 1)[1].split()
    if len(rest) < 20:
        return None
    return {"state": rest[0], "pgrp": rest[2], "sid": rest[3], "start": rest[19]}


_NODE_FRESH_S = 90  # a log written within this many seconds is a sign of life


def _short_node(name):
    return (name or "").strip().lower().split(".")[0]


def _same_node(a, b):
    """Whether two host names (``hostname`` output, short or fully qualified) are the same node."""
    return bool(_short_node(a)) and _short_node(a) == _short_node(b)


# Never ``hostname -f``: it resolves the name through DNS, which on a login node
# with a slow or broken resolver stalls every poll and submit for ~5 s per call
# (and the nodes are only ever compared by short name, see _same_node)
_HOSTNAME = "hostname 2>/dev/null || uname -n 2>/dev/null"


def _split_direct(handle):
    pid, _, start = handle.pid_or_slurm_id.partition(":")
    return pid, start


_SLURM_STATES = {
    "PENDING": "PENDING", "CONFIGURING": "PENDING", "REQUEUED": "PENDING",
    "RESV_DEL_HOLD": "PENDING", "SUSPENDED": "PENDING", "STOPPED": "PENDING",
    "REQUEUE_HOLD": "PENDING", "REQUEUE_FED": "PENDING", "SPECIAL_EXIT": "PENDING",
    "RUNNING": "RUNNING", "COMPLETING": "RUNNING", "RESIZING": "RUNNING",
    "SIGNALING": "RUNNING", "STAGE_OUT": "RUNNING",
    "COMPLETED": "DONE",
    "CANCELLED": "CANCELLED",
}


def _map_slurm(state):
    state = state.strip().split()[0] if state.strip() else ""
    if not state:
        return None
    if state in _SLURM_STATES:
        return _SLURM_STATES[state]
    if state.startswith("CANCELLED"):
        return "CANCELLED"
    return "FAILED"  # FAILED, TIMEOUT, OUT_OF_MEMORY, NODE_FAIL, BOOT_FAIL, DEADLINE


def _signal_text(n):
    try:
        return "%d (%s)" % (n, signal.Signals(n).name)
    except ValueError:
        return str(n)


def _exit_text(status):
    """"exit status N" / "killed by signal N" for a shell exit status."""
    if status > 128:
        return "killed by signal %s (exit status %d)" % (_signal_text(status - 128), status)
    return "exit status %d" % status


def _slurm_reason(sacct_state, exitcode, uid, err):
    """Why a Slurm job failed or was cancelled, from sacct's State and ExitCode
    ("code:signal") and the last SVDS ERROR line. The first word of the text is
    the Slurm state, for callers that want to attach a hint to it."""
    word = (sacct_state.split() or [""])[0]
    parts = []
    if word == "TIMEOUT":
        parts.append("TIMEOUT: the job reached its Slurm time limit and was stopped")
    elif word in ("OUT_OF_MEMORY", "OOM"):
        parts.append("OUT_OF_MEMORY: the job used more memory than it requested")
    elif word == "NODE_FAIL":
        parts.append("NODE_FAIL: the compute node failed")
    elif word.startswith("CANCELLED"):
        who = sacct_state.split(" by ")[1].strip() if " by " in sacct_state else ""
        if who and uid and who == uid:
            parts.append("CANCELLED: cancelled by you")
        elif who:
            parts.append("CANCELLED: cancelled by uid %s (not this session's user: an "
                         "administrator or another job manager)" % who)
        else:
            parts.append("CANCELLED")
    elif word:
        parts.append("Slurm state %s" % word)
    code, _, sig = exitcode.partition(":")
    if code.isdigit() and int(code) > 0:
        parts.append(_exit_text(int(code)) if int(code) > 128 else "exit code %s" % code)
    if sig.isdigit() and int(sig) > 0:
        parts.append("killed by signal %s" % _signal_text(int(sig)))
    if err:
        parts.append("last engine error: %s" % err)
    return "; ".join(parts)


_TS = r"(?:\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:[.,]\d+)?Z?|\d\d:\d\d:\d\d(?:\.\d+)?)"
_END = r"(?=SVDS [A-Z]+ |$)"
# casacore's record: "2026-10-31 06:22:03<TAB>SEVERE<TAB>MeasTable::dUTC(Double) (file
# ..., line 4290)[+]<TAB>text", a worker's record sometimes cut by another's
_CASACORE_NOISE = re.compile(
    r"(?:" + _TS + r"\s+)?(?:SEVERE\s+)?MeasTable::dUTC.*?" + _END
    + r"|(?:\+\s*)?Leap second table.*?" + _END
    + r"|(?:\+\s*)?Until the table is updated.*?" + _END
    + r"|(?:\+\s*)?times and coordinates derived from UTC could be wrong by 1s or more\.?"
    + r"|(?:" + _TS + r"\s+)?SEVERE\s*" + _END
    + r"|" + _TS + r"\s*(?=SVDS [A-Z]+ )")
# what a cut record leaves: a bare timestamp, SEVERE, or a continuation mark
_NOISE_REMAINS = re.compile(r"(?:" + _TS + r"\s*(?:SEVERE)?|SEVERE)\s*\+?|\+")
_NOISE_WORDS = ("SEVERE", "SVDS ", "MeasTable", "Leap second table", "Until the table is updated",
                "times and coordinates derived from UTC")
_SLURM_OWN_ERROR = re.compile(r"^(?:\[[^\]]*\]\s+)?error: \*\*\* (?:JOB|STEP) .*\*\*\*\s*$")


def _slurm_own_error(line):
    """Slurm's own "error: *** JOB n ON node CANCELLED AT t DUE TO TIME LIMIT ***"
    line (written into the job's output), without its time stamp, or ""."""
    line = line.strip()
    if not _SLURM_OWN_ERROR.match(line):
        return ""
    return re.sub(r"^\[[^\]]*\]\s+", "", line)[len("error: "):].strip("* ").strip()


def strip_casacore_noise(line):
    """``line`` without casacore's leap-second table complaints (printed by every
    worker, sometimes glued to the engine's own lines, sometimes cut by another
    worker's output so that only a bare timestamp is left); "" if nothing is
    left."""
    if any(w in line for w in _NOISE_WORDS):
        line = _CASACORE_NOISE.sub("", line).strip()
    if _NOISE_REMAINS.fullmatch(line.strip()):
        return ""
    return line


def _poll_script(handle, offset):
    jd, log = handle.remote_dir, handle.log_path

    def val(tag, cmd):  # printf, not echo: dash's echo interprets backslashes
        return "printf '@@%s %%s\\n' \"$(%s)\"" % (tag, cmd)

    ql = _q(log)
    # the engine's lines are found even when casacore text is glued before them
    tail = "tail -c 200000 %s 2>/dev/null" % ql

    def last(marker):
        return "%s | grep -a '%s' | tail -n 1 | sed 's/^.*\\(%s\\)/\\1/'" % (tail, marker, marker)

    parts = ["cd %s || exit 3" % _q(jd)]
    if handle.mode == "direct":
        pid, _ = _split_direct(handle)
        if not pid.isdigit():
            raise ValueError("bad pid in handle")
        # Process first: once it is seen gone, its exit code and result are
        # already on disk, so what is read below cannot be older than that
        parts.append(val("STAT", "cat /proc/%s/stat 2>/dev/null" % pid))
        parts.append(val("CUR", _HOSTNAME))  # the node this connection is on
        parts.append(val("JNODE", "cat node 2>/dev/null"))  # the node the job runs on
        parts.append(val("EXIT", "cat exitcode 2>/dev/null"))
        parts.append(val("ENDT", "stat -c %Y exitcode 2>/dev/null"))
    parts += [
        val("NOW", "date +%s"),
        val("LOGM", "stat -c %%Y %s 2>/dev/null" % ql),
        val("CANC", "[ -e cancelled ] && echo 1"),
        val("PROG", "%s | grep -ao 'SVDS PROGRESS [0-9][0-9]* [0-9][0-9]*' | tail -n 1" % tail),
        val("RES", last("SVDS RESULT ")),
        val("ERR", last("SVDS ERROR ")),
    ]
    if handle.mode != "direct":
        sid = handle.pid_or_slurm_id
        if not sid.isdigit():
            raise ValueError("bad Slurm job id in handle")
        parts += [
            val("SQ", "squeue -h -j %s -o '%%T|%%r' 2>/dev/null | head -n 1" % sid),
            val("SA", "sacct -n -P -X -j %s -o State,ExitCode 2>/dev/null | head -n 1" % sid),
            val("UID", "id -u"),
            val("SLERR", "%s | grep -a 'error: \\*\\*\\* JOB ' | tail -n 1" % tail),
        ]
    parts += ["echo @@LOG", "tail -c +%d %s 2>/dev/null | head -c %d" % (
        int(offset) + 1, ql, _LOG_CHUNK)]
    return "; ".join(parts)


def poll(conn, handle, offset=0):
    """JobStatus of ``handle`` plus the log lines after byte ``offset``.

    Only complete lines are consumed (a partial last line is read again next
    time) unless the job has ended. Never raises for a lost connection: the
    state is then UNKNOWN and the offset unchanged, and the next call tries to
    reach the server again (the connection is rebuilt, not just re-tested).
    ``reason`` of a FAILED or CANCELLED job says why (Slurm state, exit status
    or signal, the engine's last error); casacore's noise lines are left out of
    ``new_lines``."""
    offset = int(offset)
    try:
        rc, out = _run(conn, _poll_script(handle, offset), timeout=60)
    except (TimeoutError, RemoteError) as e:
        return JobStatus("UNKNOWN", offset=offset, reason="no answer from the server: %s" % e)
    if rc == 3:
        return JobStatus("UNKNOWN", offset=offset, reason="job directory is gone: %s" % handle.remote_dir)
    info, log = _parse_sections(out)
    now = float(info.get("NOW") or 0)
    st = JobStatus(offset=offset)

    prog = info.get("PROG", "").split()  # SVDS PROGRESS d t
    if len(prog) == 4 and prog[2].isdigit() and prog[3].isdigit():
        st.done, st.total = int(prog[2]), int(prog[3])
    res = info.get("RES", "").strip()
    result = res[len("SVDS RESULT "):].strip() if res.startswith("SVDS RESULT ") else None
    err = info.get("ERR", "").strip()
    err = err[len("SVDS ERROR "):] if err.startswith("SVDS ERROR ") else ""
    cancelled = info.get("CANC", "").strip() == "1"
    ended = None  # server epoch at which it ended

    if handle.mode == "direct":
        pid, start = _split_direct(handle)
        exitcode = info.get("EXIT", "").strip()
        fields = _stat_fields(info.get("STAT", ""))
        alive = bool(fields) and fields["start"] == start and fields["state"] != "Z"
        # The process exists only on the login node the job started on (pid,
        # /proc and process group are node-local), while the job directory is
        # shared. /proc decides only on that node and with known start ticks.
        job_node = (handle.node or info.get("JNODE", "")).strip()
        cur_node = info.get("CUR", "").strip()
        st.node = job_node
        other = bool(job_node and cur_node) and not _same_node(job_node, cur_node)
        st.other_node = other
        conclusive = bool(job_node and cur_node) and not other and start.isdigit() and start != "0"
        logm = float(info.get("LOGM") or 0)
        fresh = bool(logm) and now - logm <= _NODE_FRESH_S
        if exitcode.lstrip("-").isdigit():
            ended = float(info.get("ENDT") or now)
            if int(exitcode) == 0:
                st.state = "DONE"
            elif cancelled:
                st.state = "CANCELLED"
                st.reason = "cancelled (%s)" % _exit_text(int(exitcode))
            else:
                st.state = "FAILED"
                st.reason = "; ".join(x for x in (
                    "engine stopped: %s" % _exit_text(int(exitcode)),
                    err and "last engine error: %s" % err) if x)
        elif conclusive and alive:
            st.state = "RUNNING"
        elif cancelled:
            st.state, ended = "CANCELLED", float(info.get("LOGM") or now)
            st.reason = "cancelled"
        elif conclusive:
            if result:
                st.state, ended = "DONE", float(info.get("LOGM") or now)
            else:
                st.state = "FAILED"
                st.reason = err or "the process is gone without a result (killed, or the server restarted)"
                ended = float(info.get("LOGM") or now)
        elif fresh:
            # not this node's process (or its identity is unknown): the log
            # still being written says it runs
            st.state = "RUNNING"
        elif result:
            st.state, ended = "DONE", float(info.get("LOGM") or now)
        else:
            # Never FAILED from here: nothing says the job is dead
            st.state = "UNKNOWN"
            silent = "no output for %d s" % int(now - logm) if logm and now else "no output yet"
            where = ""
            if other:
                where = ("the job runs on %s but this connection is on %s: connect to %s to "
                         "see or stop it; " % (job_node, cur_node, job_node))
            elif not job_node:
                where = "the login node the job runs on is not known; "
            elif not (start.isdigit() and start != "0"):
                where = "the job's process identity is not known; "
            st.reason = "%s%s and no exit code written yet" % (where, silent)
            if err:
                st.reason += "; last engine error: %s" % err
    else:
        sq = info.get("SQ", "").strip()
        sa = info.get("SA", "").strip()
        uid = info.get("UID", "").strip()
        state = None
        if sq:
            sstate, _, why = sq.partition("|")
            state = _map_slurm(sstate)
            if state == "PENDING":
                st.reason = why.strip()
        elif sa:
            sstate, _, exitcode = sa.partition("|")
            state = _map_slurm(sstate)
            if state in ("FAILED", "CANCELLED"):
                st.reason = _slurm_reason(sstate, exitcode, uid, err)
            ended = float(info.get("LOGM") or now)
        slerr = _slurm_own_error(info.get("SLERR", ""))
        if state in ("FAILED", "CANCELLED") and slerr and slerr not in st.reason:
            st.reason = "%s; Slurm said: %s" % (st.reason, slerr) if st.reason else slerr
        if state is None:  # Slurm no longer knows it (or there is none)
            ended = float(info.get("LOGM") or now)
            if result:
                state = "DONE"
            elif err:
                state, st.reason = "FAILED", err
            elif slerr:
                state, st.reason = ("FAILED" if "TIME LIMIT" in slerr.upper() else "CANCELLED"), slerr
            else:
                state = "UNKNOWN"
                st.reason = "Slurm has no record of job %s" % handle.pid_or_slurm_id
        if state == "CANCELLED" and not st.reason:
            st.reason = "cancelled"
        st.state = state

    finished = st.state in ("DONE", "FAILED", "CANCELLED")
    if st.state == "DONE":
        st.result_path = result or handle.out_fits
        if st.total:
            st.done = st.total
    # complete lines only, unless nothing more will come
    if log:
        cut = log.rfind(b"\n") + 1
        if len(log) >= _LOG_CHUNK and cut == 0:
            cut = len(log)
        if finished:
            cut = len(log)
        chunk = log[:cut]
        st.offset = offset + len(chunk)
        for line in _text(chunk).splitlines():
            line = strip_casacore_noise(line)
            if not line or (handle.mode != "direct" and _slurm_own_error(line)):
                continue
            st.new_lines.append(line)
            if line.startswith("SVDS WARN "):
                st.warnings.append(line[len("SVDS WARN "):])
    if ended is not None and handle.submitted_at:
        st.elapsed_s = max(0.0, ended - handle.submitted_at)
    elif handle.submitted_at and now:
        st.elapsed_s = max(0.0, now - handle.submitted_at)
    return st


# ---------------------------------------------------------------- cancel


def _marker(text, *words):
    """The first of ``words`` that a line of ``text`` consists of (or starts
    with, for lines carrying detail)."""
    for line in text.splitlines():
        head = line.split(" ", 1)[0].strip()
        if head in words:
            return head, line[len(head):].strip()
    return None, ""


def connect_job_node(conn, handle, node=None):
    """A new connection to the login node a direct job runs on (``node`` or
    ``handle.node``; the caller closes it with disconnect()), made like ``conn``
    but to that node's own host name instead of the load-balanced one. A
    ``connect_node(node)`` method of ``conn`` is used when it has one. RemoteError
    naming the node when that is not possible (unknown node, host key not
    trusted yet, node unreachable)."""
    node = (node or handle.node or "").strip()
    if not node:
        raise RemoteError("the login node this job runs on is not known")
    try:
        hook = getattr(conn, "connect_node", None)
        if hook is not None:
            return hook(node)
        params = getattr(conn, "connect_params", None)
        if not params:
            raise RemoteError("no connection parameters to connect to %s with" % node)
        new = type(conn)()
        new.connect(**dict(params, host=node, username=params.get("username") or conn.username))
        return new
    except RemoteError:
        raise
    except Exception as e:
        raise RemoteError("cannot connect to %s, the login node the job runs on: %s. Connect "
                          "to %s directly to see or stop the job" % (node, e, node)) from e


def _close_node_conn(c):
    for name in ("disconnect", "close"):
        f = getattr(c, name, None)
        if f is not None:
            try:
                f()
            except Exception:
                pass
            return


def cancel(conn, handle):
    """Stop the job started by ``handle``. True only when the job is verified
    not running (it was gone, or the signal / scancel took effect). False when
    it could not be stopped or is not this job's; RemoteError (with the reason)
    when the server could not be asked, squeue could not tell, or the job runs on
    another login node that cannot be reached (a direct job's process exists only
    on its own node: the connection is moved there, never a blind "gone").
    Nothing is signalled unless the process / Slurm job is verified to be this
    job's; the "cancelled" marker is written only after a signal was sent."""
    try:
        return _cancel(conn, handle)
    except TimeoutError as e:
        raise RemoteError("the server did not answer in time: %s; the job may still be "
                          "running" % e) from e


def _cancel(conn, handle):
    jd = handle.remote_dir
    if handle.mode == "slurm":
        sid = handle.pid_or_slurm_id
        if not sid.isdigit():
            return False
        user = shlex.quote(conn.username)
        name = shlex.quote(JOB_NAME)
        # squeue's own status and message are kept: an empty answer only means
        # "gone" when squeue itself succeeded
        script = (
            "out=$(squeue -h -j %(sid)s -o '%%j|%%u' 2>&1); rc=$?\n"
            "if [ $rc -ne 0 ]; then\n"
            "  case \"$out\" in *nvalid\\ job\\ id*) printf 'GONE\\n';;\n"
            "    *) printf 'UNKNOWN %%s\\n' \"$out\";; esac\n"
            "elif [ -z \"$out\" ]; then printf 'GONE\\n'\n"
            "else\n"
            "  info=$(printf '%%s\\n' \"$out\" | head -n 1)\n"
            "  case \"$info\" in\n"
            "    %(name)s'|'%(user)s|%(name)s-*'|'%(user)s)\n"
            "      if sc=$(scancel %(sid)s 2>&1); then touch %(cancelled)s; printf 'SENT\\n'; "
            "else printf 'FAILED %%s\\n' \"$sc\"; fi;;\n"
            "    *) printf 'MISMATCH %%s\\n' \"$info\";;\n"
            "  esac\n"
            "fi\n"
        ) % {"sid": sid, "name": name, "user": user, "cancelled": _q(posixpath.join(jd, "cancelled"))}
        rc, out = _run(conn, script, timeout=60)
        word, detail = _marker(_text(out), "SENT", "GONE", "UNKNOWN", "FAILED", "MISMATCH")
        if word in ("SENT", "GONE"):
            return True
        if word == "UNKNOWN":
            raise RemoteError("squeue could not say whether job %s is still running: %s" % (sid, detail[:300]))
        if word == "MISMATCH":
            raise RemoteError("Slurm job %s is not this job (it is %s): not cancelled" % (sid, detail[:200]))
        raise RemoteError("scancel of job %s failed: %s" % (sid, (detail or _text(out))[:300]))

    pid, start = _split_direct(handle)
    if not pid.isdigit() or not start.isdigit():
        return False
    if start == "0":
        raise RemoteError("the identity of the job's process is not known (adopted without start "
                          "ticks): it cannot be verified, so it is not signalled")
    user = conn.username
    script = r'''
P=%(pid)s
EXPECT=%(node)s
[ -n "$EXPECT" ] || EXPECT=$(cat %(nodefile)s 2>/dev/null)
CUR=$(%(hostname)s)
norm() { printf %%s "$1" | tr 'A-Z' 'a-z' | cut -d. -f1; }
ok() {
  s=$(cat /proc/$P/stat 2>/dev/null) || return 1
  case "$s" in *")"*) ;; *) return 1;; esac
  set -- ${s##*)}
  [ "${20}" = %(start)s ] || return 1
  [ "$(stat -c %%U /proc/$P 2>/dev/null)" = %(user)s ] || return 1
  tr '\0' ' ' < /proc/$P/cmdline 2>/dev/null | grep -qF -- %(runsh)s || return 1
  GRP=$3; SID=$4
  return 0
}
if [ -e %(exitcode)s ]; then echo GONE; exit 0; fi
if [ -n "$EXPECT" ] && [ "$(norm "$EXPECT")" != "$(norm "$CUR")" ]; then
  echo "WRONGNODE $EXPECT $CUR"; exit 0
fi
if ! ok; then
  if [ -n "$EXPECT" ]; then echo GONE; else echo "UNVERIFIED $CUR"; fi
  exit 0
fi
if [ "$GRP" = "$P" ] && [ "$SID" = "$P" ]; then kill -TERM -$P; else kill -TERM $P; fi && touch %(cancelled)s
i=0; while [ $i -lt 50 ] && ok; do sleep 0.1; i=$((i+1)); done
if ok; then
  if [ "$GRP" = "$P" ] && [ "$SID" = "$P" ]; then kill -KILL -$P; else kill -KILL $P; fi && touch %(cancelled)s
  sleep 0.5
fi
if ok; then echo ALIVE; else echo STOPPED; fi
''' % {
        "pid": pid, "start": start, "user": shlex.quote(user),
        "node": shlex.quote(handle.node or ""), "hostname": _HOSTNAME,
        "nodefile": _q(posixpath.join(jd, "node")),
        "exitcode": _q(posixpath.join(jd, "exitcode")),
        "runsh": shlex.quote(posixpath.join(jd, "run.sh")),
        "cancelled": _q(posixpath.join(jd, "cancelled")),
    }
    rc, out = _run(conn, script, timeout=60)
    word, detail = _marker(_text(out), "STOPPED", "GONE", "ALIVE", "WRONGNODE", "UNVERIFIED")
    if word == "WRONGNODE":
        parts = detail.split()
        node = handle.node or (parts[0] if parts else "")
        here = parts[1] if len(parts) > 1 else "another node"
        pinned = None
        try:
            pinned = connect_job_node(conn, handle, node)
            rc, out = _run(pinned, script, timeout=60)
        except RemoteError as e:
            raise RemoteError("the job runs on %s but this connection is on %s: %s" % (node, here, e)) from e
        finally:
            if pinned is not None:
                _close_node_conn(pinned)
        word, detail = _marker(_text(out), "STOPPED", "GONE", "ALIVE", "WRONGNODE", "UNVERIFIED")
        if word == "WRONGNODE":
            raise RemoteError("the job runs on %s, but connecting to it reached %s: the job was "
                              "not stopped" % (node, detail.split()[-1] if detail else "another node"))
    if word == "UNVERIFIED":
        raise RemoteError("the job's process is not on %s and the login node it runs on is not "
                          "known, so it cannot be told whether it still runs elsewhere: it was "
                          "not stopped" % (detail or "this node"))
    return word in ("STOPPED", "GONE")


# ---------------------------------------------------------------- fetch


def _free_name(path):
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    i = 1
    while os.path.exists("%s_%d%s" % (stem, i, ext)):
        i += 1
    return "%s_%d%s" % (stem, i, ext)


class _Cancelled(Exception):
    pass


def fetch_result(conn, handle, local_dir, include_plot=True, progress=None, cancelled=None):
    """Copy the result (and plot) to ``local_dir``; returns the local paths.
    Existing local files are never replaced (a numeric suffix is added)."""
    os.makedirs(local_dir, exist_ok=True)
    remote = [handle.out_fits]
    if include_plot and handle.out_png:
        remote.append(handle.out_png)
    # Its own SFTP session, opened between commands (never during one), and the
    # transport marked as in use: a poll that fails meanwhile must not rebuild
    # (close) the transport under the transfer.
    lock = _conn_lock(conn)
    if not lock.acquire(timeout=_CHECK_TIMEOUT):
        raise TimeoutError("another command is still running on this connection")
    try:
        sftp = conn.open_sftp()
        using = _Using(conn)
        using.__enter__()
    finally:
        lock.release()
    paths = []
    try:
        sizes = {}
        for r in remote:
            try:
                sizes[r] = sftp.stat(r).st_size
            except (IOError, OSError):
                if r == handle.out_fits:
                    raise FileNotFoundError("result not found on the server: %s" % r)
        remote = [r for r in remote if r in sizes]
        total = sum(sizes.values())
        base = 0
        for r in remote:
            target = _free_name(os.path.join(local_dir, posixpath.basename(r)))
            partial = target + ".part"
            while os.path.exists(partial):
                partial += "x"

            def cb(done, _t, base=base):
                if progress:
                    progress(base + done, total)
                if cancelled and cancelled():
                    raise _Cancelled()

            try:
                sftp.get(r, partial, callback=cb)
                if os.path.getsize(partial) != sizes[r]:
                    raise RemoteError("size mismatch after transfer of %s" % r)
                target = _free_name(target)  # (another file may have appeared)
                os.rename(partial, target)
            except BaseException:
                if os.path.exists(partial):
                    os.remove(partial)  # our own partial file
                raise
            base += sizes[r]
            paths.append(target)
    finally:
        try:
            sftp.close()
        finally:
            using.__exit__(None, None, None)
    return paths


# ---------------------------------------------------------------- list


_JOB_STAMP = re.compile(r"-(\d{8}T\d{6})-")
_JOB_JSON_MAX = 65536
LIST_JOBS_MAX = 50


def list_jobs(conn, limit=LIST_JOBS_MAX):
    """The newest ``limit`` jobs found in ~/.solarviewer/dynspec_jobs (read
    only), newest first. Job directory names carry their start time, so only
    those of the newest jobs are opened (one SFTP read each, of at most 64 KB)."""
    sftp = conn.open_sftp()
    jobs = []
    try:
        root = posixpath.join(sftp.normalize("."), JOBS_SUBDIR)
        try:
            names = sftp.listdir(root)
        except (IOError, OSError):
            return []

        def stamp(name):
            m = _JOB_STAMP.search(name)
            return (m.group(1) if m else "", name)

        for name in sorted(names, key=stamp, reverse=True)[:max(1, int(limit))]:
            try:
                f = sftp.open(posixpath.join(root, name, "job.json"), "rb")
                try:
                    jobs.append(JobHandle.from_json(f.read(_JOB_JSON_MAX).decode("utf-8")))
                finally:
                    f.close()
            except Exception:
                continue
    finally:
        sftp.close()
    jobs.sort(key=lambda j: j.submitted_at, reverse=True)
    return jobs

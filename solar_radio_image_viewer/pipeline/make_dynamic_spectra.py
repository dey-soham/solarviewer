"""
Create Dynamic Spectra from Measurement Sets

Turns one or more Measurement Sets (MS) into a dynamic spectrum FITS file
(time x frequency) that the SolarViewer dynamic spectra viewer reads.

Inputs may be
  - a directory holding MS (names ending .ms in any case), e.g. LOFAR data with
    one MS per subband, or several MS cut in time,
  - one MS that holds all channels (and possibly several spectral windows),
  - an explicit list of MS (--ms).

For every sample (row, channel) the amplitude |vis| is taken from the chosen
polarisation (default: Stokes-I amplitude, the mean of the parallel hands that
are not flagged), flagged samples and samples outside the uv range [uvmin,
uvmax] (in wavelengths of that channel) are dropped, and the rest is averaged
into one global time grid (bin width --binwidth) and into frequency columns.
A uv bound of 0 (or less) is off; --uvmin 0 --uvmax 0 uses every baseline (and
then UVW is not read at all).  The defaults 130 / 500 wavelengths are tuned for
the LOFAR core-to-remote baselines; other arrays need their own values or 0 0.
The rows of an antenna with itself (autocorrelations, ANTENNA1 = ANTENNA2) are
not baselines and are left out: they are the total power of one antenna, far
above the baselines' amplitudes, and a few of them in the mean would take it
over.  --autocorr makes the spectrum from those rows alone instead (the uv
range does not apply to them, and only they are read).
Work is split in tasks (MS, row range) of at least about 16 MB of data, so one big
MS keeps all CPUs busy as well as many small ones.

Frequency columns (--freqres)
  auto     16 or more MS (subband data)  -> one column per MS / spectral window
           fewer                         -> channels, averaged by an integer
                                            factor so that there are at most
                                            2048 columns (stated in the log)
  subband  one column per MS / spectral window (mean over its channels in the
           requested frequency range)
  channel  keep every channel (--chanavg N averages N neighbouring channels);
           a result too large for memory is refused, not averaged silently

Line protocol on stdout (for programs that run this script, e.g. on a server):
  SVDS INFO <json>      inputs, grid, plan, final statistics
  SVDS PROGRESS d t     rows done / rows in total
  SVDS WARN <text>, SVDS ERROR <text>
  SVDS SUMMARY <json>   answer to --list
  SVDS RESULT <path>    last line on success
Exit codes: 0 done (also when some inputs failed: one SVDS WARN each and
"failed" / "failed_inputs" in the last SVDS INFO), 1 failed (no output),
2 usage error or output exists (no --overwrite), 130 / 143 / 129 stopped by
SIGINT / SIGTERM / SIGHUP (workers terminated, temporary files removed).
An existing FITS or PNG is never replaced without --overwrite; both are checked
before any data is read, and published atomically (no-clobber link).

Usage:
  python make_dynamic_spectra.py --indir /path/to/ms_dir --outfits ds.fits
  python make_dynamic_spectra.py --ms big.ms --outfits ds.fits --ncpu 8 --pol XX
  python make_dynamic_spectra.py --indir /path/to/ms_dir --list

Measurements (LOFAR HBA MS on the SURF Spider login node, inside the container
with casacore 3.4 / numpy 1.23; one MS = 5.5 M rows, 16 channels, 4 correlations,
LofarStMan storage; 200k-row blocks):
  DATA getcol (4 corr)     ~860k rows/s   getcolslice XX only   ~590k rows/s
  FLAG getcol              ~1.25M rows/s  getcolslice XX only   ~690k rows/s
  UVW  getcol              ~280k rows/s   (computed on the fly by LofarStMan;
                                           this is the dominant cost)
  getcol speed vs block    20k rows 390k/s, 100k rows 865k/s, 400k rows 880k/s
  so on LofarStMan the whole correlation axis is read and sliced afterwards;
  for other storage managers only the needed correlations/channels are read
  with getcolslice (less I/O).  See BENCH_NOTES below for scaling with ncpu.
  Standard storage (local synthetic MS, 256 chan x 4 corr, 128 channels and XX/YY
  wanted): getcol + slice 0.15 s, getcolslice 0.07 s per 40k rows, so getcolslice
  is used for every storage manager except LofarStMan.
  Whole run, one LOFAR MS, 1.6 M rows, 16 chan, 1 worker: 10.6 s reading UVW of
  every row, 5.5 s with UVW read every 4 s and interpolated (--uvstep; result
  agrees with a per-row reference to 6e-8 relative).  Same-file workers on
  LofarStMan scale badly (mmap on the shared file: 1/2/4/8 workers 5.5/5.8/4.6/8.6 s),
  different files scale linearly (4 MS: 1/2/4 workers 27.8 (cold)/9.7/4.7 s), so
  LOFAR MS get one task each when there are enough of them.  Threads do not
  scale (GIL).  Chunk size: >= 64 MB all equal; small chunks cost speed.
"""

import os

# The engine parallelises with processes: keep BLAS/OpenMP from starting a thread
# pool per worker (they spin and burn CPU).  Must be set before numpy is imported.
for _k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_k, "1")

import re
import sys
import json
import time
import errno
import uuid
import atexit
import signal
import socket
import ctypes
import argparse
import logging
import threading
import traceback
import concurrent.futures
import multiprocessing

import numpy as np

C_LIGHT = 299792458.0
MAX_COLUMNS = 2048  # most frequency columns kept in "auto" channel mode
MANY_SUBBANDS = 16  # at or above this many distinct subbands: one column each
TASK_MAX_ROWS = 20000000  # a task reads at most this many rows
MIN_TASK_BYTES = 16 * 1024 ** 2  # ... and (unless the MS is smaller) at least about this much data
UV_STEP_S = 4.0  # uv distances are read this often in time and interpolated between
MAX_ARRAY_BYTES = 6 * 1024 ** 3  # refuse a dynamic spectrum larger than this
BYTES_PER_CELL = 20  # sums float64 + counts int64 + result float32 per (time, column) cell
ACC_BYTES_TOTAL = 8 * 1024 ** 3  # all workers' per-task accumulators together (16 B per cell)
TIME_TOL_S = 1e-5  # TIME values this close below a bin edge belong to the next bin
FREQ_TOL_HZ = 1.0  # channel centres closer than this are the same column

CORR_NAMES = {
    1: "I", 2: "Q", 3: "U", 4: "V",
    5: "RR", 6: "RL", 7: "LR", 8: "LL",
    9: "XX", 10: "XY", 11: "YX", 12: "YY",
}
POL_CHOICES = ["I", "XX", "YY", "XY", "YX", "RR", "LL", "RL", "LR", "Q", "U", "V"]


class EngineError(Exception):
    """A failure with a message for the user and an exit code."""

    def __init__(self, message, code=1):
        Exception.__init__(self, message)
        self.code = code


# ------------------------------ Reporting ----------------------------------
def _json_default(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


class Reporter:
    """Line protocol on stdout (protocol=True) or a logger (Python API)."""

    def __init__(self, protocol=True, logger=None):
        self.protocol = protocol
        self.logger = logger or logging.getLogger("create_dynamic_spectra")
        self._last_progress = 0.0

    def _out(self, line):
        print(line, flush=True)

    def info(self, obj):
        if self.protocol:
            self._out("SVDS INFO " + json.dumps(obj, default=_json_default))
        else:
            self.logger.info(json.dumps(obj, default=_json_default))

    def log(self, text):
        if self.protocol:
            self._out(text)
        else:
            self.logger.info(text)

    def warn(self, text):
        if self.protocol:
            self._out("SVDS WARN " + text)
        else:
            self.logger.warning(text)

    def error(self, text):
        if self.protocol:
            self._out("SVDS ERROR " + text)
        else:
            self.logger.error(text)

    def progress(self, done, total, force=False):
        now = time.time()
        if force or now - self._last_progress >= 2.0:
            self._last_progress = now
            if self.protocol:
                self._out("SVDS PROGRESS %d %d" % (done, total))
            else:
                self.logger.info("progress %d / %d rows" % (done, total))

    def summary(self, obj):
        self._out("SVDS SUMMARY " + json.dumps(obj, default=_json_default))

    def result(self, path):
        if self.protocol:
            self._out("SVDS RESULT " + path)


# --------------------------- Small helpers ---------------------------------
def _tables():
    """casacore.tables, imported on first use (the numerics need no casacore)."""
    from casacore import tables

    return tables


def extract_sb_number(filename):
    """Integer following '_SB' in the file name (for sorting), or -1."""
    m = re.search(r"_SB(\d+)_", filename)
    return int(m.group(1)) if m else -1


def available_cpus():
    """CPUs this process may use: affinity, Slurm allocation and cgroup quota."""
    try:
        n = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        n = os.cpu_count() or 1
    for var in ("SLURM_CPUS_PER_TASK", "SLURM_CPUS_ON_NODE", "SLURM_JOB_CPUS_PER_NODE"):
        try:
            m = re.match(r"\d+", os.environ[var])  # SLURM_JOB_CPUS_PER_NODE can be "8(x2)"
            if m:
                n = min(n, int(m.group(0)))
        except KeyError:
            pass
    try:
        with open("/sys/fs/cgroup/cpu.max") as f:
            quota, period = f.read().split()[:2]
        if quota != "max":
            n = min(n, max(1, int(np.ceil(int(quota) / float(period)))))
    except (OSError, ValueError):
        try:
            with open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us") as f:
                quota = int(f.read())
            with open("/sys/fs/cgroup/cpu/cpu.cfs_period_us") as f:
                period = int(f.read())
            if quota > 0 and period > 0:
                n = min(n, max(1, int(np.ceil(quota / float(period)))))
        except (OSError, ValueError):
            pass
    return max(1, n)


def parse_time(value):
    """MJD seconds from a number (MJD seconds, or MJD days if below 1e6) or ISO text."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float, np.number)):
        v = float(value)
    else:
        text = str(value).strip()
        try:
            v = float(text)
        except ValueError:
            from astropy.time import Time

            # a UTC offset (Z, +00:00, +0200) is applied, not rejected
            m = re.match(r"^(.*[T ]\d\d:\d\d(?::\d\d(?:\.\d+)?)?)\s*(Z|[+-]\d\d:?\d\d)$", text)
            shift = 0.0
            if m:
                text, off = m.group(1), m.group(2)
                if off != "Z":
                    sign = -1.0 if off[0] == "-" else 1.0
                    digits = off[1:].replace(":", "")
                    shift = sign * (int(digits[:2]) * 3600 + int(digits[2:]) * 60)
            try:
                return float(Time(text.replace("T", " "), scale="utc").mjd) * 86400.0 - shift
            except Exception:
                raise EngineError("cannot read time %r (use 2023-03-10T10:00:00 or MJD seconds)" % text, 2)
    return v * 86400.0 if v < 1e6 else v


def _dir_bytes(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


# ---------------------- Signals, workers, temporary files ------------------
STOP_SIGNALS = [getattr(signal, n) for n in ("SIGINT", "SIGTERM", "SIGHUP") if hasattr(signal, n)]


class Interrupted(BaseException):
    """Raised by the signal handler: the run was stopped by signal signum."""

    def __init__(self, signum):
        BaseException.__init__(self, signum)
        self.signum = signum


def install_signal_handlers():
    """Turn SIGINT / SIGTERM / SIGHUP into Interrupted so that workers are
    terminated and temporary files removed on the way out.  A second signal
    kills the process at once (default action)."""
    def handler(signum, frame):
        for sig in STOP_SIGNALS:
            signal.signal(sig, signal.SIG_DFL)
        raise Interrupted(signum)

    for sig in STOP_SIGNALS:
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):  # not the main thread
            pass


def _die_with_parent(parent_pid):
    """In a worker: end when the parent process dies (no orphaned workers).

    Linux: PR_SET_PDEATHSIG; everywhere: a thread that watches the parent pid.
    """
    if sys.platform.startswith("linux"):
        try:
            ctypes.CDLL(None, use_errno=True).prctl(1, int(signal.SIGKILL), 0, 0, 0)  # PR_SET_PDEATHSIG
        except Exception:
            pass
    if os.getppid() != parent_pid:  # the parent died before we got here
        os._exit(1)

    def watch():
        while True:
            time.sleep(1.0)
            if os.getppid() != parent_pid:
                os._exit(1)

    threading.Thread(target=watch, daemon=True).start()


def _worker_setup(parent_pid):
    """Signal and parent-death setup of a forked worker.  The parent decides
    about Ctrl-C and terminates the workers itself (SIGTERM: default action)."""
    for sig in STOP_SIGNALS:
        signal.signal(sig, signal.SIG_IGN if sig == signal.SIGINT else signal.SIG_DFL)
    _die_with_parent(parent_pid)


def _abort_pool(pool):
    """Stop a process pool now: terminate (then kill) the workers, drop queued tasks."""
    procs = list((getattr(pool, "_processes", None) or {}).values())
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    try:
        pool.shutdown(wait=False, cancel_futures=True)
    except TypeError:  # Python < 3.9
        pool.shutdown(wait=False)
    except Exception:
        pass
    deadline = time.time() + 3.0
    for p in procs:
        try:
            p.join(max(0.0, deadline - time.time()))
            if p.is_alive():
                p.kill()
                p.join(1.0)
        except Exception:
            pass


_TEMPS = set()  # temporary files this process created (removed at exit, on errors and signals)


def _rm_temps():
    for path in list(_TEMPS):
        try:
            os.unlink(path)
        except OSError:
            pass
        _TEMPS.discard(path)


atexit.register(_rm_temps)


def temp_name(final):
    """A name for the temporary file of final, next to it and unique per host,
    process and call (two Slurm nodes with the same pid cannot collide)."""
    d, b = os.path.split(final)
    host = re.sub(r"[^A-Za-z0-9]", "", socket.gethostname())[:16] or "host"
    return os.path.join(d, "%s.part-%s-%d-%s" % (b, host, os.getpid(), uuid.uuid4().hex[:8]))


def check_output_path(path, what="output"):
    """Fail now (exit 2) instead of after the computation: path must be a file
    name in an existing directory where we can create files, with a valid length."""
    d = os.path.dirname(path) or "."
    if os.path.isdir(path):
        raise EngineError("%s path is a directory: %s" % (what, path), 2)
    if not os.path.isdir(d):
        raise EngineError("%s directory does not exist: %s" % (what, d), 2)
    tmp = temp_name(path)
    if len(os.path.basename(tmp).encode("utf-8", "replace")) > 255:
        raise EngineError("%s file name is too long: %s" % (what, os.path.basename(path)), 2)
    try:
        os.close(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except OSError as e:
        raise EngineError("cannot create files in %s (%s): %s" % (d, e.strerror or e, what), 2)
    try:
        os.unlink(tmp)
    except OSError:
        pass


def publish_file(tmp, final, overwrite):
    """Move the finished temporary file tmp to final.

    overwrite: atomic replace.  Otherwise a no-clobber publish: a hard link
    (fails if final exists, even if it appeared while we were computing), then
    the temporary name is removed; where hard links do not work the name is
    claimed with O_EXCL first.  Raises EngineError (code 2) when final exists.
    """
    def exists():
        return EngineError("output exists, use --overwrite: %s" % final, 2)

    if overwrite:
        os.replace(tmp, final)
        _TEMPS.discard(tmp)
        return
    try:
        os.link(tmp, final)
    except FileExistsError:
        raise exists()
    except OSError as e:
        if e.errno == errno.EEXIST:
            raise exists()
        try:
            os.close(os.open(final, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
        except FileExistsError:
            raise exists()
        try:
            os.replace(tmp, final)
        except BaseException:
            try:
                os.unlink(final)  # the empty placeholder we made
            except OSError:
                pass
            raise
        _TEMPS.discard(tmp)
        return
    try:
        os.unlink(tmp)
    except OSError:
        pass
    _TEMPS.discard(tmp)


# ----------------------------- MS discovery --------------------------------
def is_ms(path):
    """True if path is a casacore table directory (an MS has table.dat)."""
    return os.path.isdir(path) and os.path.isfile(os.path.join(path, "table.dat"))


def _ms_name(path):
    return path.rstrip("/").lower().endswith(".ms")


def discover_ms(path_or_paths):
    """MS found under a directory, or given explicitly, as absolute paths.

    A directory is searched (one level) for MS whose names end in .ms (any
    case); a directory that is itself an MS is returned as it is.  Explicit
    paths must be MS.  Order of first appearance, duplicates removed.
    """
    if isinstance(path_or_paths, (str, bytes, os.PathLike)):
        path_or_paths = [path_or_paths]
    found, seen = [], set()

    def add(p):
        key = os.path.realpath(p)
        if key not in seen:
            seen.add(key)
            found.append(os.path.abspath(p))

    for p in path_or_paths:
        p = os.fspath(p)
        if not os.path.exists(p):
            raise EngineError("path does not exist: %s" % p)
        if is_ms(p):
            add(p)
            continue
        if not os.path.isdir(p):
            raise EngineError("not a directory or Measurement Set: %s" % p)
        children = sorted(os.listdir(p))
        named = [c for c in children if _ms_name(c) and is_ms(os.path.join(p, c))]
        if not named:  # no .ms suffix: accept any child that is an MS
            named = [c for c in children if is_ms(os.path.join(p, c))
                     and os.path.isdir(os.path.join(p, c, "SPECTRAL_WINDOW"))]
        for c in named:
            add(os.path.join(p, c))
    return found


# ------------------------------ MS metadata --------------------------------
def ms_info(path, datacolumn="DATA", with_bytes=False):
    """Metadata of one MS: rows, time range, spectral windows, correlations."""
    T = _tables()
    info = {"path": os.path.abspath(path), "name": os.path.basename(path.rstrip("/"))}
    if with_bytes:
        info["bytes"] = _dir_bytes(path)
    sb = extract_sb_number(info["name"] + "_")
    info["sb"] = sb if sb >= 0 else None
    tb = T.table(path, ack=False, readonly=True)
    try:
        cols = tb.colnames()
        info["nrow"] = int(tb.nrows())
        info["datacol_fallback"] = False
        if datacolumn in cols:
            info["datacol"] = datacolumn
        elif datacolumn == "DATA" and "CORRECTED_DATA" in cols:
            info["datacol"] = "CORRECTED_DATA"
            info["datacol_fallback"] = True
        else:
            raise EngineError("%s: no %s column" % (info["name"], datacolumn))
        info["has_flag"] = "FLAG" in cols
        info["has_flagrow"] = "FLAG_ROW" in cols
        info["telescope"] = ""
        try:
            obs_path = os.path.join(path, "OBSERVATION")
            if os.path.isdir(obs_path):
                obs = T.table(obs_path, ack=False, readonly=True)
                try:
                    if obs.nrows():
                        info["telescope"] = str(obs.getcell("TELESCOPE_NAME", 0)).strip()
                finally:
                    obs.close()
        except Exception:
            pass
        has_ddid = "DATA_DESC_ID" in cols
        try:
            info["dm"] = str(tb.getdminfo(info["datacol"]).get("TYPE", ""))
        except Exception:
            info["dm"] = ""
        # time range (chunked: the column can be large)
        tmin, tmax = np.inf, -np.inf
        for s in range(0, info["nrow"], 5000000):
            t = tb.getcol("TIME", startrow=s, nrow=min(5000000, info["nrow"] - s))
            if len(t):
                tmin, tmax = min(tmin, float(t.min())), max(tmax, float(t.max()))
        info["tmin"], info["tmax"] = tmin, tmax
        # data descriptions
        spw_tab = T.table(os.path.join(path, "SPECTRAL_WINDOW"), ack=False, readonly=True)
        try:
            nspw = spw_tab.nrows()
            spw_freq = [np.asarray(spw_tab.getcell("CHAN_FREQ", i), dtype=np.float64) for i in range(nspw)]
        finally:
            spw_tab.close()
        ddmap = {}  # ddid -> (spw, pol)
        dd_path = os.path.join(path, "DATA_DESCRIPTION")
        if os.path.isdir(dd_path):
            dd_tab = T.table(dd_path, ack=False, readonly=True)
            try:
                spw_ids = dd_tab.getcol("SPECTRAL_WINDOW_ID")
                pol_ids = dd_tab.getcol("POLARIZATION_ID")
                for i in range(dd_tab.nrows()):
                    ddmap[i] = (int(spw_ids[i]), int(pol_ids[i]))
            finally:
                dd_tab.close()
        else:
            ddmap[0] = (0, 0)
        used = set(ddmap)
        if has_ddid and len(ddmap) > 1:
            used = set()
            for s in range(0, info["nrow"], 5000000):
                dd = tb.getcol("DATA_DESC_ID", startrow=s, nrow=min(5000000, info["nrow"] - s))
                used.update(int(x) for x in np.unique(dd))
            used &= set(ddmap)
        info["multi_dd"] = has_ddid and len(used) > 1
        # correlations per polarization setup
        pol_corr = {}
        pol_path = os.path.join(path, "POLARIZATION")
        if os.path.isdir(pol_path):
            p_tab = T.table(pol_path, ack=False, readonly=True)
            try:
                for i in range(p_tab.nrows()):
                    pol_corr[i] = [int(c) for c in p_tab.getcell("CORR_TYPE", i)]
            finally:
                p_tab.close()
        first = None
        dds = {}
        for d in sorted(used):
            spw, pol = ddmap[d]
            freq = spw_freq[spw]
            if pol not in pol_corr:  # no POLARIZATION table: take the shape of the data
                if first is None:
                    first = tb.getcell(info["datacol"], 0).shape[-1] if info["nrow"] else 4
                pol_corr[pol] = [9, 10, 11, 12] if first == 4 else ([9, 12] if first == 2 else [9])
            dds[d] = {"spw": spw, "pol": pol, "freq": freq, "corr": pol_corr[pol]}
        info["dd"] = dds
        info["nspw"] = len(set(v["spw"] for v in dds.values()))
        info["nchan"] = int(sum(len(spw_freq[s]) for s in set(v["spw"] for v in dds.values())))
        info["uniform"] = len(set((len(v["freq"]), len(v["corr"])) for v in dds.values())) <= 1
        allf = np.concatenate([v["freq"] for v in dds.values()]) if dds else np.zeros(0)
        info["fmin"] = float(allf.min()) if len(allf) else float("nan")
        info["fmax"] = float(allf.max()) if len(allf) else float("nan")
    finally:
        tb.close()
    return info


def _sort_infos(infos):
    """Subband order: by _SBnnn_ when every MS has one, else by lowest frequency."""
    if infos and all(i["sb"] is not None for i in infos):
        return sorted(infos, key=lambda i: (i["sb"], i["name"]))
    return sorted(infos, key=lambda i: (i["fmin"], i["name"]))


def short_error(e):
    """One readable line for an exception (casacore errors are long and cryptic)."""
    text = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    if isinstance(e, ImportError) or "No module named" in text and "casacore" in text:
        return "python-casacore is not installed here (%s)" % text
    if "non-existing row" in text or "SSMIndex" in text:
        return "table has no rows or is damaged (%s)" % text
    if "does not exist" in text or "No such file" in text:
        return "table cannot be opened (%s)" % text
    return text


def _info_task(args):
    try:
        return ms_info(*args), None
    except Exception as e:
        return None, short_error(e)


def _init_info_worker(parent_pid):
    _worker_setup(parent_pid)


def _read_infos(paths, rep=None, datacolumn="DATA", with_bytes=False, max_workers=4, progress=None):
    """Metadata of every MS (in parallel when there are many: opening a table
    on a network disk takes ~0.1-0.4 s), sorted; plus the (path, error) failures.
    At most max_workers processes (default 4: this also runs on shared login nodes).
    progress(done, total), if given, is called as each MS is read."""
    jobs = [(p, datacolumn, with_bytes) for p in paths]

    def gather(results):
        gathered = []
        for result in results:
            gathered.append(result)
            if progress is not None:
                progress(len(gathered), len(jobs))
        return gathered

    nproc = max(1, min(int(max_workers or 4), available_cpus(), len(jobs)))
    results = None
    if nproc > 1 and len(jobs) >= 4:
        pool = None
        try:
            ctx = multiprocessing.get_context("fork")
            pool = concurrent.futures.ProcessPoolExecutor(
                max_workers=nproc, mp_context=ctx, initializer=_init_info_worker, initargs=(os.getpid(),))
            results = gather(pool.map(_info_task, jobs))
            pool.shutdown(wait=True)
        except Exception:
            results = None
            if pool is not None:
                _abort_pool(pool)
        except BaseException:
            if pool is not None:
                _abort_pool(pool)
            raise
    if results is None:
        results = gather(_info_task(j) for j in jobs)
    infos, failed = [], []
    for (p, _, _), (info, err) in zip(jobs, results):
        if info is not None:
            infos.append(info)
        else:
            failed.append((p, err))
            if rep is not None:
                rep.warn("cannot read %s: %s" % (p, err))
    return _sort_infos(infos), failed


def summarize(paths, datacolumn="DATA", max_workers=4, progress=None):
    """Fast overview of the inputs (no data read): dict as printed by --list."""
    paths = discover_ms(paths)
    infos, failed = _read_infos(paths, None, datacolumn, with_bytes=True, max_workers=max_workers,
                                progress=progress)
    ms_list = []
    for i in infos:
        ms_list.append({
            "name": i["name"], "path": i["path"], "nrow": i["nrow"],
            "nchan": i["nchan"], "nspw": i["nspw"],
            "fmin_mhz": i["fmin"] / 1e6, "fmax_mhz": i["fmax"] / 1e6,
            "tmin_mjds": i["tmin"], "tmax_mjds": i["tmax"],
            "bytes": i["bytes"],
            "ncorr": max([len(v["corr"]) for v in i["dd"].values()] or [0]),
            "corr": sorted(set(CORR_NAMES.get(c, str(c)) for v in i["dd"].values() for c in v["corr"])),
            "storage": i["dm"],
        })
    tot_rows = sum(m["nrow"] for m in ms_list)
    return {
        "count": len(ms_list),
        "failed": [{"path": p, "error": e} for p, e in failed],
        "ms": ms_list,
        "total_rows": tot_rows,
        "total_bytes": sum(m["bytes"] for m in ms_list),
        "total_channels": sum(m["nchan"] for m in ms_list),
        "fmin_mhz": min([m["fmin_mhz"] for m in ms_list] or [float("nan")]),
        "fmax_mhz": max([m["fmax_mhz"] for m in ms_list] or [float("nan")]),
        "tmin_mjds": min([m["tmin_mjds"] for m in ms_list] or [float("nan")]),
        "tmax_mjds": max([m["tmax_mjds"] for m in ms_list] or [float("nan")]),
        "ncorr": max([m["ncorr"] for m in ms_list] or [0]),
        "est_rows": tot_rows,
    }


# ------------------------- Numerics (no MS I/O) ----------------------------
def time_grid(tmin, tmax, binwidth):
    """(t0, nbins): t0 = tmin floored to a multiple of binwidth (MJD seconds)."""
    if not binwidth > 0:
        raise ValueError("binwidth must be positive")
    t0 = np.floor((tmin + TIME_TOL_S) / binwidth) * binwidth
    nbins = int(np.floor((tmax - t0 + TIME_TOL_S) / binwidth)) + 1
    return float(t0), max(nbins, 1)


def bin_index(times, t0, binwidth):
    """Index of the global time bin of each time.

    TIME is about 5e9 s, where a float64 resolves only ~1e-6 s: a time that
    sits exactly on a bin edge (cadence = binwidth, e.g. 0.1 s) would land on
    either side at random, giving empty and double bins.  Times within
    TIME_TOL_S (10 us) below an edge therefore count to the bin above it.
    """
    return np.floor((np.asarray(times, dtype=np.float64) - t0 + TIME_TOL_S) / binwidth).astype(np.int64)


def pick_correlations(corr_types, pol="I"):
    """Indices (into the correlation axis) used for polarisation pol.

    I: the parallel hands present (XX and/or YY, else RR and/or LL, else a
    Stokes I column); anything else: just that product when it exists.
    """
    names = [CORR_NAMES.get(int(c), "?") for c in corr_types]
    pol = pol.upper()
    if pol == "I":
        for pair in (("XX", "YY"), ("RR", "LL"), ("I",)):
            idx = sorted(names.index(n) for n in pair if n in names)  # ascending: the slicer needs it
            if idx:
                return idx
        return []
    return [names.index(pol)] if pol in names else []


def uv_mask(uvdist, wavelengths, uvmin=None, uvmax=None):
    """(nrow, nchan) mask of samples inside the uv range, per channel wavelength.

    uvdist in metres, wavelengths in metres; a bound of None or <= 0 is off.
    """
    lam = np.asarray(uvdist, dtype=np.float64)[:, None] / np.asarray(wavelengths, dtype=np.float64)[None, :]
    ok = np.ones(lam.shape, dtype=bool)
    if uvmin is not None and uvmin > 0:
        ok &= lam >= uvmin
    if uvmax is not None and uvmax > 0:
        ok &= lam <= uvmax
    return ok


def sample_values(amp, flag, rowok=None):
    """Amplitude per sample from (nrow, nchan, ncorr) amplitudes and flags.

    Returns (value, valid): the mean over the unflagged, finite correlations
    and whether any was usable.  amp may also be the complex data (|.| is taken
    one correlation at a time, which saves the full-size temporary).  flag may
    be None; rowok (nrow) drops rows.

    The correlation axis is tiny (1-4): reducing over it with numpy's axis
    reductions was the largest cost of a run, so it is a loop of plain 2-D
    operations (same result bit for bit, about 5x faster).
    """
    ncorr = amp.shape[2]
    is_complex = np.iscomplexobj(amp)
    num = den = None
    for k in range(ncorr):
        a = np.abs(amp[..., k]) if is_complex else amp[..., k]
        ok = np.isfinite(a)
        if flag is not None:
            ok &= ~flag[..., k]
        if rowok is not None:
            ok &= rowok[:, None]
        v = np.where(ok, a, a.dtype.type(0))
        if ncorr == 1:
            return v.astype(np.float32, copy=False), ok
        if num is None:
            num, den = v, ok.astype(np.int8)
        else:
            num += v
            den += ok
    valid = den > 0
    return np.divide(num, np.maximum(den, 1), dtype=np.float64).astype(np.float32), valid


def reduce_samples(bins, values, valid, starts):
    """Sum and count of valid samples per (time bin, channel group).

    bins (n) are global time bin indices, values/valid are (n, nchan), starts
    are the first channel of each channel group.  Returns (bins_unique, S, N)
    with S float64 sums and N int64 counts, shape (len(bins_unique), ngroups).
    """
    ng = len(starts)
    n = len(bins)
    if n == 0:
        return np.zeros(0, np.int64), np.zeros((0, ng)), np.zeros((0, ng), np.int64)
    v = np.where(valid, values, 0).astype(np.float32, copy=False)
    if ng == values.shape[1]:
        rs, rn = v, valid.astype(np.int32)
    else:
        rs = np.add.reduceat(v, starts, axis=1)
        rn = np.add.reduceat(valid, starts, axis=1, dtype=np.int32)
    bins = np.asarray(bins)
    if n > 1 and np.any(bins[1:] < bins[:-1]):
        order = np.argsort(bins, kind="stable")
        bins, rs, rn = bins[order], rs[order], rn[order]
    seg = np.concatenate(([0], np.flatnonzero(bins[1:] != bins[:-1]) + 1))
    S = np.add.reduceat(rs, seg, axis=0, dtype=np.float64)
    N = np.add.reduceat(rn, seg, axis=0, dtype=np.int64)
    return bins[seg], S, N


def add_block(S, N, bins, cols, s, n):
    """Add a (bins, groups) block into the global (Ntime, Ncol) sums and counts.

    Bins and columns are each unique, so the fancy-index += is exact.
    """
    if len(bins):
        S[bins[:, None], cols[None, :]] += s
        N[bins[:, None], cols[None, :]] += n


def finalize(S, N):
    """Mean amplitude (float32) with NaN where nothing was valid."""
    out = np.full(S.shape, np.nan, dtype=np.float32)
    np.divide(S, N, out=out, where=N > 0, casting="same_kind")  # no full-size temporaries
    return out


def _merge_centres(centre_lists, tol=FREQ_TOL_HZ):
    """Global ascending column frequencies and, per list, the column of each centre."""
    sizes = [len(c) for c in centre_lists]
    if sum(sizes) == 0:
        return np.zeros(0), [np.zeros(0, np.int64) for _ in sizes]
    allc = np.concatenate(centre_lists)
    order = np.argsort(allc, kind="stable")
    srt = allc[order]
    new = np.concatenate(([True], np.diff(srt) > tol))
    cid = np.cumsum(new) - 1
    ncol = int(cid[-1]) + 1
    freqs = np.array([srt[cid == k].mean() for k in range(ncol)]) if ncol < 50000 else srt[new]
    col_of = np.empty(len(allc), np.int64)
    col_of[order] = cid
    out, k = [], 0
    for sz in sizes:
        out.append(col_of[k:k + sz])
        k += sz
    return freqs, out


def _group_centres(freq, c0, c1, factor):
    starts = np.arange(0, c1 - c0, factor)
    counts = np.diff(np.concatenate((starts, [c1 - c0])))
    centres = np.add.reduceat(freq[c0:c1], starts) / counts
    return starts, centres


def plan_columns(unit_freqs, mode="auto", chanavg=0, startfreq=None, endfreq=None,
                 max_cols=MAX_COLUMNS, many=MANY_SUBBANDS):
    """Assign channels of every input (MS / spectral window) to frequency columns.

    unit_freqs: list of 1-D channel frequency arrays in Hz.  startfreq/endfreq
    in MHz (inclusive, either may be None).  Returns (plans, col_freq_hz,
    mode, factor): plans[i] is None for an input outside the range, else a dict
    with c0, c1 (channel range used), starts (first channel of each group,
    relative to c0) and cols (global column of each group); col_freq_hz is
    ascending.  Inputs of the same centre frequency (e.g. an observation cut
    in time) share their columns.  An input is used when any channel is inside
    the range, and only the channels inside count (also in subband mode, where
    the column is the mean frequency of those channels).  The 2048-column cap
    (averaging by an integer factor) applies to mode "auto" only; "channel"
    keeps every channel unless chanavg is given.
    """
    requested = mode
    lo = -np.inf if startfreq is None else startfreq * 1e6
    hi = np.inf if endfreq is None else endfreq * 1e6
    freqs = [np.asarray(f, dtype=np.float64) for f in unit_freqs]
    inside = [(len(f) > 0) and bool(np.any((f >= lo) & (f <= hi))) for f in freqs]
    if mode == "auto":
        centres = set(int(round(f.mean() / 1e3)) for f, ok in zip(freqs, inside) if ok)
        mode = "subband" if len(centres) >= many else "channel"
    plans = [None] * len(freqs)
    use = []
    if mode == "subband":
        for i, f in enumerate(freqs):
            if inside[i]:
                sel = np.flatnonzero((f >= lo) & (f <= hi))
                use.append((i, int(sel[0]), int(sel[-1]) + 1))
        factor = 0
        cents = [np.array([freqs[i][c0:c1].mean()]) for i, c0, c1 in use]
        col_freq, cols = _merge_centres(cents)
        for (i, c0, c1), c in zip(use, cols):
            plans[i] = {"c0": c0, "c1": c1, "starts": np.array([0]), "cols": c}
        return plans, col_freq, mode, factor
    for i, f in enumerate(freqs):
        if inside[i]:
            sel = np.flatnonzero((f >= lo) & (f <= hi))
            use.append((i, int(sel[0]), int(sel[-1]) + 1))

    def build(factor):
        gs = [_group_centres(freqs[i], c0, c1, factor) for i, c0, c1 in use]
        col_freq, cols = _merge_centres([g[1] for g in gs])
        return gs, col_freq, cols

    if chanavg and chanavg > 0:
        factor = int(chanavg)
        gs, col_freq, cols = build(factor)
    else:
        factor = 1
        gs, col_freq, cols = build(factor)
        while requested == "auto" and len(col_freq) > max_cols:
            factor = max(factor + 1, int(np.ceil(factor * len(col_freq) / float(max_cols))))
            gs, col_freq, cols = build(factor)
    for (i, c0, c1), g, c in zip(use, gs, cols):
        plans[i] = {"c0": c0, "c1": c1, "starts": g[0], "cols": c}
    return plans, col_freq, mode, factor


# ------------------------------ MS reading ---------------------------------
def _read_block(tb, col, start, n, c0, c1, nchan, corr_idx, ncorr, lofar):
    """(n, c1-c0, len(corr_idx)) block of DATA/FLAG rows start..start+n.

    LofarStMan stores whole rows, where getcol is faster than getcolslice (see
    the measurements at the top), so everything is read and sliced.  Other
    storage managers read only the channels and correlations needed.
    """
    corr_idx = list(corr_idx)
    all_corr = corr_idx == list(range(ncorr))
    all_chan = c0 == 0 and c1 == nchan
    if lofar or (all_corr and all_chan):
        a = tb.getcol(col, startrow=start, nrow=n)
        if not all_chan:
            a = a[:, c0:c1]
        if not all_corr:
            a = a[:, :, corr_idx]
        return a
    lo, hi = corr_idx[0], corr_idx[-1]
    inc = 1
    if len(corr_idx) > 1:
        inc = corr_idx[1] - corr_idx[0]
        if corr_idx != list(range(lo, hi + 1, inc)):  # irregular: read the span
            sub = [i - lo for i in corr_idx]
            a = tb.getcolslice(col, [c0, lo], [c1 - 1, hi], [1, 1], startrow=start, nrow=n)
            return a[:, :, sub]
    return tb.getcolslice(col, [c0, lo], [c1 - 1, hi], [1, inc], startrow=start, nrow=n)


def _read_rows(tb, col, rows, c0, c1, nchan, corr_idx, ncorr, lofar):
    """Like _read_block for an arbitrary list of rows (runs of consecutive rows)."""
    brk = np.flatnonzero(np.diff(rows) != 1) + 1
    runs = np.split(rows, brk)
    parts = []
    if len(runs) <= 32:
        for r in runs:
            parts.append(_read_block(tb, col, int(r[0]), len(r), c0, c1, nchan, corr_idx, ncorr, lofar))
    else:
        sub = tb.selectrows(rows)
        try:
            a = sub.getcol(col)
        finally:
            sub.close()
        a = a[:, c0:c1]
        return a[:, :, list(corr_idx)]
    return np.concatenate(parts, axis=0) if len(parts) > 1 else parts[0]


def slot_layout(t):
    """(starts, size) of the time slots of a block of rows, or None if irregular.

    Regular: times never decrease and every slot (rows of equal TIME) except
    possibly the first and last holds the same number of rows.
    """
    n = len(t)
    if n < 8 or np.any(t[1:] < t[:-1]):
        return None
    starts = np.concatenate(([0], np.flatnonzero(t[1:] != t[:-1]) + 1))
    if len(starts) < 6:
        return None
    sizes = np.diff(np.concatenate((starts, [n])))
    m = int(np.median(sizes[1:-1]))
    if m < 1 or np.any(sizes[1:-1] != m) or sizes[0] > m or sizes[-1] > m:
        return None
    return starts, m


def interp_slots(samples, sample_slots, slots):
    """Linear interpolation in time slot between sampled slots, per baseline.

    samples (nsample, m) at the increasing slot numbers sample_slots; returns
    (len(slots), m) for slot numbers inside that range.
    """
    hi = np.clip(np.searchsorted(sample_slots, slots, side="left"), 1, len(sample_slots) - 1)
    lo = hi - 1
    w = ((slots - sample_slots[lo]) / (sample_slots[hi] - sample_slots[lo]).astype(np.float64))[:, None]
    return samples[lo] * (1.0 - w) + samples[hi] * w


def uv_distance(tb, start, n, t, step_s=UV_STEP_S):
    """sqrt(u^2 + v^2) in metres of rows start..start+n.

    The UVW of an interferometer changes slowly with time, and some storage
    managers (LofarStMan) compute it row by row, which was over half of the run
    time.  When the rows are in regular time slots (every slot the same baselines
    in the same order, checked from ANTENNA1/2) and the cadence is finer than
    step_s, UVW is read for every k-th slot only (k*cadence <= step_s) and
    interpolated linearly (error ~ 1e-7 relative at 4 s); otherwise, and with
    step_s <= 0, every row is read.  Interpolation is only trusted where UVW is
    smooth: the slot times must be evenly spaced (a time gap inside the block
    reads everything), and one extra slot between every two sampled ones is
    read and compared with the interpolated value (a phase-centre / field change
    reads everything).
    """
    def full():
        uvw = tb.getcol("UVW", startrow=start, nrow=n)
        return np.hypot(uvw[:, 0], uvw[:, 1])

    lay = slot_layout(t) if step_s and step_s > 0 else None
    if lay is None:
        return full()
    starts, m = lay
    cadence = float(np.median(np.diff(t[starts])))
    k = int(step_s / cadence) if cadence > 0 else 1
    if k < 2:
        return full()
    # full slots are consecutive rows; the first/last slot may be partial
    f0 = 0 if starts[1] - starts[0] == m else 1
    fl = len(starts) - 1 if (n - starts[-1]) == m else len(starts) - 2
    nf = fl - f0 + 1
    if nf < 4:
        return full()
    ts = t[starts[f0:fl + 1]]
    if np.any(np.abs(np.diff(ts) - cadence) > 0.05 * cadence):  # a time gap or irregular cadence
        return full()
    a1 = tb.getcol("ANTENNA1", startrow=start, nrow=n)
    a2 = tb.getcol("ANTENNA2", startrow=start, nrow=n)
    r0 = int(starts[f0])
    blk1 = a1[r0:r0 + nf * m].reshape(nf, m)
    blk2 = a2[r0:r0 + nf * m].reshape(nf, m)
    if np.any(blk1 != blk1[0]) or np.any(blk2 != blk2[0]):
        return full()
    sample = np.unique(np.concatenate((np.arange(0, nf, k), [nf - 1])))
    rows = np.empty((len(sample), m))
    for i, sl in enumerate(sample):
        uvw = tb.getcol("UVW", startrow=start + r0 + int(sl) * m, nrow=m)
        rows[i] = np.hypot(uvw[:, 0], uvw[:, 1])
    mids = (sample[:-1] + sample[1:]) // 2  # check slot between every two sampled ones
    mids = mids[mids > sample[:-1]]
    if len(mids):
        check = np.empty((len(mids), m))
        for i, sl in enumerate(mids):
            uvw = tb.getcol("UVW", startrow=start + r0 + int(sl) * m, nrow=m)
            check[i] = np.hypot(uvw[:, 0], uvw[:, 1])
        est = interp_slots(rows, sample, mids)
        if np.any(np.abs(est - check) > 1e-4 * np.maximum(check, 1.0)):
            return full()
    out = np.empty(n)
    out[r0:r0 + nf * m] = interp_slots(rows, sample, np.arange(nf)).reshape(-1)
    for a, b in ((0, r0), (r0 + nf * m, n)):  # partial slots at the ends
        if b > a:
            uvw = tb.getcol("UVW", startrow=start + a, nrow=b - a)
            out[a:b] = np.hypot(uvw[:, 0], uvw[:, 1])
    return out


class _Counter:
    """Rows done: shared memory between worker processes, or a plain counter."""

    def __init__(self, shared=None):
        self.shared = shared
        self.n = 0

    def add(self, k):
        if self.shared is not None:
            with self.shared.get_lock():
                self.shared.value += k
        else:
            self.n += k

    def get(self):
        return self.shared.value if self.shared is not None else self.n


_COUNTER = _Counter()
_TABLES = {}


def _init_worker(shared, parent_pid=None):
    global _COUNTER
    _COUNTER = _Counter(shared)
    if parent_pid is not None:
        _worker_setup(parent_pid)


def _open(path):
    tb = _TABLES.get(path)
    if tb is None:
        while len(_TABLES) >= 4:  # keep a few open tables only
            _TABLES.pop(next(iter(_TABLES))).close()
        tb = _TABLES[path] = _tables().table(path, ack=False, readonly=True)
    return tb


def _close_all():
    for tb in _TABLES.values():
        try:
            tb.close()
        except Exception:
            pass
    _TABLES.clear()


def _row_info(tb, task, start, n):
    """TIME, usable-row mask, DATA_DESC_ID (or None), time bin and the number
    of usable autocorrelation rows of rows start..start+n.

    The mask keeps the baselines (two different antennas), or with
    task["autocorr"] the autocorrelations alone; the number counts the
    autocorrelations among the rows that are usable otherwise, whichever way
    (left out, or the ones used)."""
    t = tb.getcol("TIME", startrow=start, nrow=n)
    ok = np.ones(n, bool)
    ts, te = task["tsel"]
    if ts is not None:
        ok &= t >= ts
    if te is not None:
        ok &= t <= te
    if task["has_flagrow"]:
        ok &= ~tb.getcol("FLAG_ROW", startrow=start, nrow=n).astype(bool)
    dd = tb.getcol("DATA_DESC_ID", startrow=start, nrow=n) if task["multi_dd"] else None
    if dd is not None:
        ok &= np.isin(dd, list(task["dd"]))
    bins = bin_index(t, task["t0"], task["binwidth"])
    ok &= (bins >= 0) & (bins < task["nbins"])
    auto = tb.getcol("ANTENNA1", startrow=start, nrow=n) == tb.getcol("ANTENNA2", startrow=start, nrow=n)
    nauto = int((ok & auto).sum())
    ok &= auto if task.get("autocorr") else ~auto
    return t, ok, dd, bins, nauto


def _unique_sorted(b):
    """Sorted unique values of an int array (cheap when it is already sorted, as
    the TIME column of an MS usually is)."""
    if len(b) > 1 and bool(np.all(b[1:] >= b[:-1])):
        return b[np.concatenate(([True], b[1:] != b[:-1]))]
    return np.unique(b)


def run_task(task):
    """Accumulate rows [start, stop) of one MS; returns per-ddid sums and counts.

    task keys: path, start, stop, datacol, dm, nchan_max, multi_dd, uniform, dd
    ({ddid: plan}), t0, binwidth, nbins, tsel, uv, chunk_bytes, has_flag,
    has_flagrow, acc_bytes, autocorr (optional: the autocorrelations alone).
    Result: {"blocks": {ddid: (b_first, cols, S, N)}, "stats": {...}}.
    """
    tb = _open(task["path"])
    start, stop = task["start"], task["stop"]
    nrow = stop - start
    plans = task["dd"]
    autocorr = bool(task.get("autocorr"))
    stats = {"samples": 0, "flagged": 0, "uvcut": 0, "rows": 0, "autorows": 0}
    # first pass over the small columns: which time bins does this task touch?
    touched = []
    for cs in range(0, nrow, 2000000):
        _, rowok, _, bins, nauto = _row_info(tb, task, start + cs, min(2000000, nrow - cs))
        stats["autorows"] += nauto  # (counted here: this pass sees every row, also of a task with nothing to use)
        if rowok.any():
            touched.append(_unique_sorted(bins[rowok]))
    if not touched:
        _COUNTER.add(nrow)
        return {"blocks": {}, "stats": stats}
    ubins = _unique_sorted(np.concatenate(touched)) if len(touched) > 1 else touched[0]
    nb = len(ubins)  # accumulators cover the bins actually touched, not the span between them
    need = sum(nb * len(p["starts"]) * 16 for p in plans.values())  # float64 sums + int64 counts
    limit = task.get("acc_bytes", ACC_BYTES_TOTAL)
    if need > limit:
        raise EngineError("time bins x frequency columns too large for one worker (%.1f GB, limit %.1f GB); "
                          "use a larger --binwidth or --chanavg, or fewer --ncpu" % (need / 1e9, limit / 1e9))
    local = {d: (np.zeros((nb, len(p["starts"]))), np.zeros((nb, len(p["starts"])), np.int64))
             for d, p in plans.items()}
    uvmin, uvmax = task["uv"]
    # (an autocorrelation has no uv distance to cut by)
    use_uv = not autocorr and ((uvmin is not None and uvmin > 0) or (uvmax is not None and uvmax > 0))
    lofar = task["dm"].lower().startswith("lofar")
    # bytes per row read: data (8) + flag (1) per channel and correlation
    row_bytes = 9.0 * max((p["nchan"] * p["ncorr"]) if lofar else
                          ((p["c1"] - p["c0"]) * len(p["corr_idx"])) for p in plans.values())
    crows = int(max(1000, task["chunk_bytes"] // max(row_bytes, 1)))
    for cs in range(0, nrow, crows):
        ce = min(nrow, cs + crows)
        a0 = start + cs
        t, ok, dd, bins, _ = _row_info(tb, task, a0, ce - cs)
        if not ok.any():
            _COUNTER.add(ce - cs)
            continue
        if use_uv:
            uvd = uv_distance(tb, a0, ce - cs, t, task["uvstep"])
        cube = {}  # whole-chunk reads shared by all ddids when shapes agree
        for d, p in plans.items():
            sel = ok if dd is None else ok & (dd == d)
            if not sel.any():
                continue
            idx = None if (sel.all()) else np.flatnonzero(sel)
            ncorr, nchan = p["ncorr"], p["nchan"]
            # The autocorrelations are a few rows in every time slot (60 of 1830
            # for 60 antennas): those rows alone are read, not the block
            few = autocorr and idx is not None and 2 * len(idx) <= ce - cs
            if (idx is None or task["uniform"]) and not few:
                key = (p["c0"], p["c1"], tuple(p["corr_idx"]))
                if key not in cube:
                    cube[key] = (
                        _read_block(tb, task["datacol"], a0, ce - cs, p["c0"], p["c1"], nchan, p["corr_idx"], ncorr, lofar),
                        _read_block(tb, "FLAG", a0, ce - cs, p["c0"], p["c1"], nchan, p["corr_idx"], ncorr, lofar)
                        if task["has_flag"] else None,
                    )
                data, flag = cube[key]
                if idx is not None:
                    data = data[idx]
                    flag = flag[idx] if flag is not None else None
            else:  # shapes differ between spectral windows, or few rows are wanted: read these rows only
                rows = a0 + idx
                data = _read_rows(tb, task["datacol"], rows, p["c0"], p["c1"], nchan, p["corr_idx"], ncorr, lofar)
                flag = (_read_rows(tb, "FLAG", rows, p["c0"], p["c1"], nchan, p["corr_idx"], ncorr, lofar)
                        if task["has_flag"] else None)
            val, valid = sample_values(data, flag)
            nsamp = valid.size
            nvalid = int(valid.sum())
            if use_uv:
                u = uvd if idx is None else uvd[idx]
                valid &= uv_mask(u, C_LIGHT / p["freq"][p["c0"]:p["c1"]], uvmin, uvmax)
            stats["samples"] += nsamp
            stats["flagged"] += nsamp - nvalid
            stats["uvcut"] += nvalid - int(valid.sum())
            stats["rows"] += valid.shape[0]
            b = bins if idx is None else bins[idx]
            ub, S, N = reduce_samples(b, val, valid, p["starts"])
            if len(ub):
                pos = np.searchsorted(ubins, ub)
                local[d][0][pos] += S
                local[d][1][pos] += N
        _COUNTER.add(ce - cs)
    blocks = {}
    for d, (S, N) in local.items():
        keep = np.flatnonzero(N.sum(axis=1) > 0)
        if len(keep):
            blocks[d] = (ubins[keep], plans[d]["cols"], S[keep], N[keep])
    return {"blocks": blocks, "stats": stats}


def _run_task_safe(task):
    """run_task with errors returned instead of raised (keeps the pool alive)."""
    try:
        return {"ok": True, "path": task["path"], "res": run_task(task)}
    except Exception as e:
        return {"ok": False, "path": task["path"], "error": "%s: %s" % (type(e).__name__, short_error(e)),
                "rows": task["stop"] - task["start"], "tb": traceback.format_exc()}


# --------------------------------- Planning --------------------------------
def _row_bytes(info):
    """Bytes read per row of this MS (data 8 + flag 1 per channel and correlation)."""
    lofar = info["dm"].lower().startswith("lofar")  # whole rows are read there
    return 9.0 * max(((p["nchan"] * p["ncorr"]) if lofar else ((p["c1"] - p["c0"]) * len(p["corr_idx"])))
                     for p in info["_plan"].values())


def make_tasks(infos, grid, args, ncpu):
    """Split the work in (MS, row range) tasks; returns (tasks, total_rows)."""
    t0, nbins = grid
    rows_total = sum(i["_nrow_used"] for i in infos if i["_plan"])
    chunk_bytes = int(args["chunkmb"] * 1024 * 1024)
    acc_bytes = int(max(512 * 1024 ** 2, ACC_BYTES_TOTAL // max(ncpu, 1)))
    tasks = []
    nused = sum(1 for j in infos if j["_plan"])
    for i in infos:
        if not i["_plan"]:
            continue
        nrow = i["_nrow_used"]
        # about 4 tasks per worker, but not smaller than MIN_TASK_BYTES of this MS's
        # row size: the floor is in bytes, not rows, so a wide MS of few rows
        # (186k rows x 1024 channels, or 46k rows x 512) still feeds every worker
        min_rows = max(1000, int(MIN_TASK_BYTES / max(_row_bytes(i), 1.0)))
        target = int(min(TASK_MAX_ROWS, max(rows_total / (4.0 * max(ncpu, 1)), min_rows)))
        ntask = max(1, int(np.ceil(nrow / float(target))))
        if i["dm"].lower().startswith("lofar"):
            # LofarStMan maps the file: several processes on one file slow each other
            # down badly (see the measurements above): one task per MS when there are
            # enough MS to keep every worker busy, else at most two
            ntask = 1 if nused >= ncpu else min(ntask, 2)
        edges = np.linspace(0, nrow, ntask + 1).astype(np.int64)
        for k in range(ntask):
            if edges[k + 1] > edges[k]:
                tasks.append({
                    "path": i["path"], "start": int(edges[k]), "stop": int(edges[k + 1]),
                    "datacol": i["datacol"], "dm": i["dm"], "multi_dd": i["multi_dd"],
                    "uniform": i["uniform"], "dd": i["_plan"],
                    "t0": t0, "binwidth": args["binwidth"], "nbins": nbins,
                    "tsel": (args["tstart"], args["tend"]),
                    "uv": (args["uvmin"], args["uvmax"]), "uvstep": args["uvstep"],
                    "autocorr": bool(args.get("autocorr")),
                    "chunk_bytes": chunk_bytes, "acc_bytes": acc_bytes,
                    "has_flag": i["has_flag"], "has_flagrow": i["has_flagrow"],
                })
    return tasks, rows_total


def _prepare(infos, args, rep):
    """Frequency plan, pol choice and time grid; fills _plan/_nrow_used in infos."""
    units = []  # (info, ddid)
    for i in infos:
        for d in sorted(i["dd"]):
            units.append((i, d))
    plans, col_freq, mode, factor = plan_columns(
        [i["dd"][d]["freq"] for i, d in units], args["freqres"], args["chanavg"],
        args["startfreq"], args["endfreq"])
    for i in infos:
        i["_plan"] = {}
        i["_nrow_used"] = i["nrow"] if not args.get("maxrows") else min(i["nrow"], int(args["maxrows"]))
    skipped_pol = []
    empty = [i["name"] for i in infos if i["_nrow_used"] == 0]
    if empty:
        rep.warn("no rows in %d MS, skipped: %s" % (len(empty), ", ".join(empty[:5]) + (" ..." if len(empty) > 5 else "")))
    for (i, d), p in zip(units, plans):
        if p is None or i["_nrow_used"] == 0:
            continue
        dd = i["dd"][d]
        cidx = pick_correlations(dd["corr"], args["pol"])
        if not cidx:
            skipped_pol.append("%s (has %s)" % (i["name"], ",".join(CORR_NAMES.get(c, "?") for c in dd["corr"])))
            continue
        i["_plan"][d] = {
            "c0": p["c0"], "c1": p["c1"], "starts": p["starts"], "cols": p["cols"],
            "freq": dd["freq"], "corr_idx": cidx, "ncorr": len(dd["corr"]),
            "nchan": len(dd["freq"]),
        }
    if skipped_pol:
        rep.warn("polarisation %s not present in: %s" % (args["pol"], "; ".join(skipped_pol[:5])
                                                         + (" ..." if len(skipped_pol) > 5 else "")))
    used = [i for i in infos if i["_plan"]]
    if not used:
        raise EngineError("nothing to process: no data in the chosen frequency range / polarisation "
                          "(or every Measurement Set is empty)")
    # drop the columns that ended up unused (polarisation missing) and renumber
    cols_used = np.unique(np.concatenate([p["cols"] for i in used for p in i["_plan"].values()]))
    remap = np.full(len(col_freq), -1, np.int64)
    remap[cols_used] = np.arange(len(cols_used))
    for i in used:
        for p in i["_plan"].values():
            p["cols"] = remap[p["cols"]]
    col_freq = col_freq[cols_used]
    # time grid from the data actually selected
    tmin = min(i["tmin"] for i in used)
    tmax = max(i["tmax"] for i in used)
    if args["tstart"] is not None:
        tmin = max(tmin, args["tstart"])
    if args["tend"] is not None:
        tmax = min(tmax, args["tend"])
    if tmin > tmax:
        raise EngineError("no data in the requested time range")
    t0, nbins = time_grid(tmin, tmax, args["binwidth"])
    return used, col_freq, mode, factor, (t0, nbins)


def _make_pool(ncpu, shared):
    try:
        ctx = multiprocessing.get_context("fork")
    except ValueError:
        return None
    return concurrent.futures.ProcessPoolExecutor(
        max_workers=ncpu, mp_context=ctx, initializer=_init_worker, initargs=(shared, os.getpid()))


def compute(infos, args, rep):
    """Run everything up to the dynamic spectrum.

    Returns (times_mjd_s, spectrum float32 (Ntime, Nfreq), freq_hz, stats).  An
    input with a failing task is listed in stats["failed_inputs"] (with the rows
    lost); the run only raises when no task of any input finished.
    """
    used, col_freq, mode, factor, grid = _prepare(infos, args, rep)
    t0, nbins = grid
    ncol = len(col_freq)
    if nbins * ncol * BYTES_PER_CELL > MAX_ARRAY_BYTES:
        raise EngineError("the dynamic spectrum would be %d x %d: use a larger --binwidth or --chanavg"
                          % (nbins, ncol))
    fallback = [i["name"] for i in used if i.get("datacol_fallback")]
    if fallback:
        rep.warn("DATA column missing in %d MS (first: %s): using CORRECTED_DATA"
                 % (len(fallback), fallback[0]))
    if mode == "channel" and args["freqres"] == "auto" and factor > 1 and not args["chanavg"]:
        rep.log("averaging %d channels per column to stay under %d columns (use --chanavg to choose)"
                % (factor, MAX_COLUMNS))
    ncpu_avail = available_cpus()
    ncpu_req = args["ncpu"] if args["ncpu"] and args["ncpu"] > 0 else ncpu_avail
    ncpu = ncpu_req
    if ncpu > ncpu_avail:
        rep.warn("limiting ncpu from %d to the %d available here" % (ncpu, ncpu_avail))
        ncpu = ncpu_avail
    tasks, rows_total = make_tasks(used, grid, args, ncpu)
    if len(tasks) < ncpu:
        rep.log("only %d task(s) for %d workers: using %d worker(s)" % (len(tasks), ncpu, max(1, len(tasks))))
    ncpu = max(1, min(ncpu, len(tasks)))
    datacols = sorted(set(i["datacol"] for i in used))
    rep.info({
        "stage": "plan", "ms": len(used), "tasks": len(tasks), "rows": rows_total,
        "ncpu": ncpu, "ncpu_requested": int(ncpu_req), "freqres": mode, "chanavg": factor,
        "ntime": nbins, "nfreq": ncol, "datacol": datacols,
        "t0_mjds": t0, "binwidth": args["binwidth"], "pol": args["pol"],
        "fmin_mhz": float(col_freq[0] / 1e6), "fmax_mhz": float(col_freq[-1] / 1e6),
        "autocorr": bool(args.get("autocorr")),
    })
    S = np.zeros((nbins, ncol), np.float64)
    N = np.zeros((nbins, ncol), np.int64)
    stats = {"samples": 0, "flagged": 0, "uvcut": 0, "rows": 0, "autorows": 0}
    failed = {}  # path -> {"path", "name", "error", "tasks", "rows"} (first error kept)
    succeeded = set()  # paths with at least one finished task
    shared = None
    pool = None
    if ncpu > 1:
        try:
            ctx = multiprocessing.get_context("fork")
            shared = ctx.Value("q", 0)
        except ValueError:
            rep.warn("fork is not available here: running on one CPU")
            ncpu = 1
    counter = _Counter(shared)

    def merge(out):
        if not out["ok"]:
            f = failed.get(out["path"])
            if f is None:
                failed[out["path"]] = {"path": out["path"], "name": os.path.basename(out["path"]),
                                       "error": out["error"], "tasks": 1, "rows": out["rows"]}
                rep.warn("%s failed: %s" % (os.path.basename(out["path"]), out["error"]))
            else:
                f["tasks"] += 1
                f["rows"] += out["rows"]
            return
        succeeded.add(out["path"])
        res = out["res"]
        for k in stats:
            stats[k] += res["stats"][k]
        for b, cols, s, n in res["blocks"].values():
            add_block(S, N, b, cols, s, n)

    t_start = time.time()
    finished = False
    try:
        if ncpu == 1:
            global _COUNTER
            _COUNTER = counter
            for task in tasks:
                merge(_run_task_safe(task))
                rep.progress(counter.get(), rows_total)
        else:
            _close_all()
            pool = _make_pool(ncpu, shared)
            futs = [pool.submit(_run_task_safe, t) for t in tasks]
            pending = set(futs)
            last_beat = 0.0
            while pending:
                done, pending = concurrent.futures.wait(
                    pending, timeout=1.0, return_when=concurrent.futures.FIRST_COMPLETED)
                for f in done:
                    try:
                        out = f.result()
                    except Exception as e:  # a worker died (e.g. out of memory)
                        raise EngineError("a worker process failed: %s (try fewer --ncpu or a smaller --chunkmb)" % e)
                    merge(out)
                rep.progress(min(counter.get(), rows_total), rows_total, force=time.time() - last_beat > 5.0)
                if time.time() - last_beat > 5.0:
                    last_beat = time.time()
        finished = True
    finally:
        if pool is not None:
            if finished:
                pool.shutdown(wait=True)
            else:  # error or signal: stop the workers now, do not wait for the queue
                _abort_pool(pool)
        _close_all()
    rep.progress(rows_total, rows_total, force=True)
    stats["seconds"] = time.time() - t_start
    stats["failed"] = len(failed)
    stats["failed_paths"] = sorted(failed)
    stats["failed_inputs"] = [{"path": f["path"], "error": f["error"], "tasks": f["tasks"], "rows": f["rows"]}
                              for f in failed.values()]
    stats["failed_rows"] = sum(f["rows"] for f in failed.values())
    stats["rows_total"] = rows_total
    stats["nms"] = len(used)
    stats["mode"] = mode
    stats["chanavg"] = factor
    stats["datacols"] = datacols
    stats["telescope"] = next((i["telescope"] for i in used if i.get("telescope")), "")
    stats["autocorr"] = bool(args.get("autocorr"))
    first = next(iter(failed.values()), None)
    if first is not None and not succeeded:  # every input failed completely
        raise EngineError("all %d inputs failed, first cause: %s: %s" % (len(used), first["name"], first["error"]))
    if (N > 0).sum() == 0:
        extra = " (%d inputs failed, first cause: %s)" % (len(failed), first["error"]) if first else ""
        if stats["autocorr"] and not stats["autorows"]:
            raise EngineError("no autocorrelations in the data (no row has the same antenna twice): the spectrum "
                              "can only be made from the baselines (without --autocorr; in the window, "
                              "\"Use only autocorrelations\" unticked)" + extra)
        if stats["autocorr"]:
            raise EngineError("no valid samples (every autocorrelation is flagged)" + extra)
        if stats["autorows"] and not stats["rows"]:
            raise EngineError("the data holds autocorrelations only (no row has two different antennas): make the "
                              "spectrum from them with --autocorr (in the window, \"Use only autocorrelations\")" + extra)
        raise EngineError("no valid samples (everything flagged or outside the uv range)" + extra)
    if stats["autorows"] and not stats["autocorr"]:
        rep.log("left out %d autocorrelation rows (an antenna with itself); --autocorr, in the window "
                "\"Use only autocorrelations\", makes the spectrum from them instead" % stats["autorows"])
    times = t0 + (np.arange(nbins) + 0.5) * args["binwidth"]
    return times, finalize(S, N), col_freq, stats


# --------------------------------- Output ----------------------------------
def write_fits(times_s, spectrum, freq_hz, outfits, params, stats, overwrite=False):
    """Write the FITS file the dynamic spectra viewer reads.

    Written to a unique temporary name next to outfits and published atomically;
    without overwrite an existing outfits (also one that appeared meanwhile) is
    never replaced (EngineError, code 2).  The temporary file never outlives the
    call, also on errors and signals.
    """
    from astropy.io import fits
    from astropy.time import Time

    time_mjd = times_s / 86400.0
    t = Time(time_mjd, format="mjd", scale="utc")
    t.precision = 3
    utc = np.array(t.iso)
    hdu = fits.PrimaryHDU(np.ascontiguousarray(spectrum, dtype=np.float32))
    hdr = hdu.header
    hdr["EXTNAME"] = "DYNAMIC_SPECTRUM"
    autocorr = bool(params.get("autocorr"))
    hdr["BUNIT"] = ("Amplitude", "uncalibrated |autocorrelation|" if autocorr else "uncalibrated |visibility|")
    bw = float(params["binwidth"])
    hdr["DATE-OBS"] = (Time((times_s[0] - bw / 2) / 86400.0, format="mjd", scale="utc").isot, "start of first bin (UTC)")
    hdr["DATE-END"] = (Time((times_s[-1] + bw / 2) / 86400.0, format="mjd", scale="utc").isot, "end of last bin (UTC)")
    hdr["TIMEREF"] = ("bin centre", "TIME_AXIS = time bin centres")
    tele = str(stats.get("telescope") or "")
    if tele and tele.isascii() and tele.isprintable():
        hdr["TELESCOP"] = tele
    cols = [c for c in stats.get("datacols", []) if str(c).isprintable()]
    if cols:
        hdr["DATACOL"] = (",".join(cols), "MS column read")
    hdr["COMMENT"] = "Axis0 = time (see TIME_AXIS), Axis1 = frequency (see FREQ_AXIS)"
    hdr["BINWIDTH"] = (float(params["binwidth"]), "time bin width (s)")
    hdr["UVMIN"] = (float(params["uvmin"] or 0.0), "min uv distance (wavelengths), 0 = off")
    hdr["UVMAX"] = (float(params["uvmax"] or 0.0), "max uv distance (wavelengths), 0 = off")
    hdr["AUTOCORR"] = (autocorr, "T = autocorrelations only, F = baselines only")
    hdr["NAUTOROW"] = (int(stats.get("autorows", 0)),
                       "autocorrelation rows used" if autocorr else "autocorrelation rows left out")
    hdr["POL"] = (str(params["pol"]), "polarisation (I = mean of parallel hands)")
    hdr["NMS"] = (int(stats["nms"]), "number of Measurement Sets used")
    hdr["NFAILED"] = (int(stats["failed"]), "inputs that failed (cells are NaN)")
    hdr["NFAILROW"] = (int(stats.get("failed_rows", 0)), "MS rows lost in failed tasks")
    hdr["FREQRES"] = (str(stats["mode"]), "frequency columns: subband or channel")
    hdr["CHANAVG"] = (int(stats["chanavg"]), "channels averaged per column")
    hdr["NSAMPLES"] = (int(stats["samples"]), "samples read (row x channel)")
    hdr["NFLAGGED"] = (int(stats["flagged"]), "samples dropped as flagged")
    hdr["NUVCUT"] = (int(stats["uvcut"]), "samples dropped by the uv range")
    hdr.add_history("Dynamic spectrum created with SolarViewer")
    time_hdu = fits.BinTableHDU.from_columns([
        fits.Column(name="TIME_MJD", format="D", unit="d", array=time_mjd),
        fits.Column(name="UTC", format="23A", array=utc),
    ])
    time_hdu.name = "TIME_AXIS"
    freq_hdu = fits.BinTableHDU.from_columns([
        fits.Column(name="FREQ_MHz", format="D", unit="MHz", array=np.asarray(freq_hz, np.float64) / 1e6),
    ])
    freq_hdu.name = "FREQ_AXIS"
    tmp = temp_name(outfits)
    _TEMPS.add(tmp)
    try:
        fits.HDUList([hdu, time_hdu, freq_hdu]).writeto(tmp, overwrite=False)
        publish_file(tmp, outfits, overwrite)
    except BaseException:
        try:
            os.unlink(tmp)  # our own temporary file (its name is unique)
        except OSError:
            pass
        _TEMPS.discard(tmp)
        raise


def _pad_nan(a, mult, axis):
    """a with NaN appended along axis up to a multiple of mult (the last partial block)."""
    extra = (-a.shape[axis]) % mult
    if not extra:
        return a
    shape = list(a.shape)
    shape[axis] = extra
    return np.concatenate([a.astype(np.float64, copy=False), np.full(shape, np.nan)], axis=axis)


def _block_axis(x, f):
    """Mean of x in blocks of f (the last block may be shorter)."""
    x = np.asarray(x, dtype=np.float64)
    if f == 1:
        return x
    return np.nanmean(_pad_nan(x, f, 0).reshape(-1, f), axis=1)


def block_mean(a, max_t=2000, max_f=1000):
    """nanmean down-sampling of (time, freq) for plotting (partial last blocks kept)."""
    ft = max(1, int(np.ceil(a.shape[0] / float(max_t))))
    ff = max(1, int(np.ceil(a.shape[1] / float(max_f))))
    if ft == 1 and ff == 1:
        return a, ft, ff
    b = _pad_nan(_pad_nan(a, ft, 0), ff, 1)
    b = b.reshape(b.shape[0] // ft, ft, b.shape[1] // ff, ff)
    with np.errstate(all="ignore"):
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            return np.nanmean(b, axis=(1, 3)), ft, ff


def display_available():
    """True if a plot window can be opened here (Linux needs DISPLAY or WAYLAND_DISPLAY)."""
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return True


PLOT_FORMATS = ("png", "pdf", "svg", "jpg", "jpeg", "tif", "tiff", "eps", "ps")


def plot_format(path):
    """matplotlib format for a plot file name (from its real extension); raises
    EngineError (code 2) for an extension matplotlib cannot be trusted with."""
    ext = os.path.splitext(str(path))[1].lower().lstrip(".")
    if ext not in PLOT_FORMATS:
        raise EngineError("unsupported plot file type %r (use one of: %s): %s"
                          % ("." + ext if ext else "(none)", ", ".join("." + f for f in PLOT_FORMATS), path), 2)
    return ext


def make_plot(times_s, spectrum, freq_hz, path=None, show=False, fmt=None, title="Dynamic Spectrum"):
    """Save and/or show the dynamic spectrum (matplotlib is imported here only).

    Saving always uses the Agg backend (works without a display).  Showing needs
    a display: without one this raises RuntimeError instead of letting Qt abort
    the whole process.  path is written as given (no atomic publishing here);
    fmt is the file format (default: from the extension of path).
    """
    import matplotlib

    if show and not display_available():
        raise RuntimeError("no display: the plot cannot be shown here")
    if not show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.dates import DateFormatter, date2num
    from astropy.time import Time

    if show:
        try:  # a previous save may have selected Agg
            plt.switch_backend(matplotlib.rcParamsDefault["backend"])
        except Exception:
            pass
    img, ft, ff = block_mean(spectrum)
    tt = _block_axis(times_s, ft)
    fr = _block_axis(freq_hz, ff) / 1e6
    x = date2num(Time(tt / 86400.0, format="mjd", scale="utc").to_datetime())
    fig, ax = plt.subplots(figsize=(10, 6))
    good = img[np.isfinite(img)]
    vmin, vmax = (np.percentile(good, [1, 99]) if good.size else (None, None))
    if len(fr) > 1:
        mesh = ax.pcolormesh(x, fr, img.T, shading="nearest", cmap="viridis", vmin=vmin, vmax=vmax)
    else:
        mesh = ax.pcolormesh(x, [fr[0] - 0.5, fr[0] + 0.5], np.vstack([img.T, img.T]),
                             shading="auto", cmap="viridis", vmin=vmin, vmax=vmax)
    ax.set_xlabel("Time (UTC)")
    ax.set_ylabel("Frequency (MHz)")
    ax.set_title(title)
    fig.colorbar(mesh, label="Amplitude")
    ax.xaxis.set_major_formatter(DateFormatter("%Y-%m-%d\n%H:%M:%S"))
    fig.autofmt_xdate()
    if path:
        fig.savefig(path, format=fmt or plot_format(path), dpi=150, bbox_inches="tight")
    if show:
        plt.show()
    plt.close(fig)


# ------------------------------- Entry points ------------------------------
DEFAULTS = dict(
    binwidth=1.0, ncpu=0, uvmin=130.0, uvmax=500.0, startfreq=None, endfreq=None,
    tstart=None, tend=None, pol="I", freqres="auto", chanavg=0, chunkmb=128.0,
    datacolumn="DATA", maxrows=0, uvstep=UV_STEP_S, autocorr=False,
)


def check_options(args, rep):
    """Validate (and normalise) the numeric options; usage errors exit with code 2."""
    for k in ("tstart", "tend"):
        args[k] = parse_time(args[k])
    for k in ("binwidth", "uvmin", "uvmax", "chunkmb", "startfreq", "endfreq", "tstart", "tend", "uvstep"):
        v = args[k]
        if v is not None and not np.isfinite(v):
            raise EngineError("%s must be a finite number, not %s" % (k, v), 2)
    if args["startfreq"] is not None and args["endfreq"] is not None and args["startfreq"] > args["endfreq"]:
        raise EngineError("startfreq is above endfreq", 2)
    for k in ("startfreq", "endfreq"):
        if args[k] is not None and args[k] < 0:
            raise EngineError("invalid frequency bound %s=%s" % (k, args[k]), 2)
    for k in ("tstart", "tend"):
        if args[k] is not None and args[k] < 0:
            raise EngineError("invalid time %s (negative)" % k, 2)
    if args["tstart"] is not None and args["tend"] is not None and args["tstart"] > args["tend"]:
        raise EngineError("tstart is after tend", 2)
    if not args["binwidth"] > 0:
        raise EngineError("binwidth must be positive", 2)
    if not args["chunkmb"] > 0:
        raise EngineError("chunkmb must be positive", 2)
    args["ncpu"] = int(args["ncpu"] or 0)
    if args["ncpu"] < 0:
        raise EngineError("ncpu must be 0 (automatic) or more", 2)
    args["autocorr"] = bool(args.get("autocorr"))
    if args["autocorr"]:  # nothing to cut by: an autocorrelation has no uv distance
        args["uvmin"] = args["uvmax"] = 0.0
    for k in ("uvmin", "uvmax"):  # 0 (or less) = no limit on that side
        if args[k] is not None and args[k] < 0:
            rep.warn("%s=%g is negative: treated as no limit (use 0)" % (k, args[k]))
            args[k] = 0.0
    if args["uvmin"] and args["uvmax"] and args["uvmin"] > args["uvmax"]:
        raise EngineError("uvmin is above uvmax", 2)
    args["pol"] = str(args["pol"]).upper()
    if args["pol"] not in POL_CHOICES:
        raise EngineError("pol must be one of %s" % ", ".join(POL_CHOICES), 2)
    if args["freqres"] not in ("auto", "subband", "channel"):
        raise EngineError("freqres must be auto, subband or channel", 2)
    try:
        ok = float(args["chanavg"]) == int(args["chanavg"]) and int(args["chanavg"]) >= 0
    except (TypeError, ValueError, OverflowError):
        ok = False
    if not ok:
        raise EngineError("chanavg must be a whole number, 0 (automatic) or more, not %s" % (args["chanavg"],), 2)
    args["chanavg"] = int(args["chanavg"])


def run(inputs, outfits, rep, saveplot=False, plotfile=None, showplot=False,
        overwrite=False, defer_show=None, **opts):
    """Everything: discover, plan, compute, write.  Returns the FITS path.

    Options and outputs are checked before any data is read: an existing FITS or
    PNG without overwrite, an unwritable directory or a bad name are errors at
    once (code 2).  A plotfile implies saveplot.  Raises EngineError (code 2 for
    usage errors and existing outputs, else 1).  If some inputs fail the output is
    still written (see the final "done" INFO); if all fail there is no output.
    defer_show: list to which the show-plot action is appended instead of being
    run at the end (the CLI shows the window after printing the result).
    """
    args = dict(DEFAULTS)
    args.update({k: v for k, v in opts.items() if k in DEFAULTS})
    check_options(args, rep)
    if str(os.fspath(outfits)).endswith(("/", os.sep)):
        raise EngineError("output must be a file name, not a directory: %s" % outfits, 2)
    outfits = os.path.abspath(outfits)
    saveplot = bool(saveplot or plotfile)
    pf = os.path.abspath(plotfile or (os.path.splitext(outfits)[0] + ".png")) if saveplot else None
    if pf == outfits:
        raise EngineError("the plot file is the FITS file: %s" % pf, 2)
    pfmt = plot_format(pf) if pf else None
    existing = [p for p in (outfits, pf) if p and os.path.lexists(p)]
    if existing and not overwrite:
        raise EngineError("output exists, use --overwrite: %s" % ", ".join(existing), 2)
    check_output_path(outfits, "output")
    if pf:
        check_output_path(pf, "plot")
    paths = discover_ms(inputs)
    if not paths:
        raise EngineError("no Measurement Sets found (names ending .ms in any case) in %s" % (inputs,))
    rep.info({"stage": "reading", "found": len(paths)})
    infos, failed = _read_infos(paths, rep, args["datacolumn"], max_workers=min(4, args["ncpu"] or 4))
    if not infos:
        raise EngineError("none of the %d Measurement Sets could be read, first cause: %s: %s"
                          % (len(paths), os.path.basename(failed[0][0].rstrip("/")), failed[0][1]))
    rep.info({"stage": "inputs", "found": len(paths), "readable": len(infos), "unreadable": len(failed),
              "first": infos[0]["name"], "last": infos[-1]["name"],
              "rows": sum(i["nrow"] for i in infos)})
    times, spec, freq, stats = compute(infos, args, rep)
    stats["failed"] += len(failed)
    stats["failed_inputs"] = [{"path": p, "error": e} for p, e in failed] + stats["failed_inputs"]
    rep.info({"stage": "writing"})
    write_fits(times, spec, freq, outfits, args, stats, overwrite=overwrite)
    title = "Dynamic Spectrum (autocorrelations)" if args["autocorr"] else "Dynamic Spectrum"
    if pf:
        rep.info({"stage": "plotting"})
        tmp = temp_name(pf)
        _TEMPS.add(tmp)
        try:
            make_plot(times, spec, freq, tmp, False, fmt=pfmt, title=title)
            publish_file(tmp, pf, overwrite)
            rep.log("plot saved as %s" % pf)
        except Exception as e:
            rep.warn("plot failed: %s" % e)
        finally:
            if tmp in _TEMPS:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                _TEMPS.discard(tmp)
    rep.info({"stage": "done", "ntime": spec.shape[0], "nfreq": spec.shape[1], "failed": stats["failed"],
              "failed_inputs": stats["failed_inputs"], "failed_rows": stats["failed_rows"],
              "nms": stats["nms"], "seconds": round(stats["seconds"], 2), "samples": stats["samples"],
              "flagged": stats["flagged"], "uvcut": stats["uvcut"],
              "autocorr": stats["autocorr"], "autorows": stats["autorows"],
              "filled_fraction": float(np.isfinite(spec).mean())})
    if showplot:
        def show():
            try:
                make_plot(times, spec, freq, None, True, title=title)
            except Exception as e:
                rep.warn("plot not shown: %s" % e)

        if defer_show is not None:
            defer_show.append(show)
        else:
            show()
    return outfits


def run_dynamic_spectra(indir, outfits, binwidth=1.0, ncpu=10, uvmin=130.0, uvmax=500.0,
                        startfreq=None, endfreq=None, saveplot=False, showplot=False,
                        logger=None, plot_filename=None, overwrite=True, **new_kwargs):
    """Create the dynamic spectrum FITS from MS in a directory (or a list of MS).

    Python API of the command line tool (see the module docstring); extra
    keyword arguments are the other options (pol, freqres, chanavg, tstart,
    tend, chunkmb, datacolumn, autocorr).  ncpu is limited to the CPUs available here.
    Returns the FITS path, or None on failure (logged).
    """
    rep = Reporter(protocol=False, logger=logger)
    try:
        return run(indir, outfits, rep, saveplot=saveplot, plotfile=plot_filename,
                   showplot=showplot, overwrite=overwrite, binwidth=binwidth, ncpu=ncpu,
                   uvmin=uvmin, uvmax=uvmax, startfreq=startfreq, endfreq=endfreq, **new_kwargs)
    except Exception as e:
        rep.logger.error("Error creating dynamic spectra: %s" % e)
        return None


def build_parser():
    p = argparse.ArgumentParser(description="Create a dynamic spectrum FITS from Measurement Sets.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--indir", help="directory holding MS (names ending .ms in any case), or one MS")
    g.add_argument("--ms", nargs="+", help="explicit MS paths")
    p.add_argument("--outfits", default=None, help="output FITS file")
    p.add_argument("--binwidth", type=float, default=1.0, help="time bin width in seconds (default 1)")
    p.add_argument("--ncpu", type=int, default=0,
                   help="worker processes (0 = all CPUs this machine / job allows; with --list: at most 4 when 0)")
    p.add_argument("--uvmin", type=float, default=130.0, help="minimum uv distance in wavelengths (0 = off; default %g is tuned for LOFAR)" % DEFAULTS["uvmin"])
    p.add_argument("--uvmax", type=float, default=500.0, help="maximum uv distance in wavelengths (0 = off; --uvmin 0 --uvmax 0 uses all baselines)")
    p.add_argument("--autocorr", action="store_true",
                   help="make the spectrum from the autocorrelations alone (rows of an antenna with itself, "
                        "left out otherwise); the uv range is not used")
    p.add_argument("--startfreq", type=float, default=None, help="lowest frequency in MHz")
    p.add_argument("--endfreq", type=float, default=None, help="highest frequency in MHz")
    p.add_argument("--tstart", default=None, help="start time: ISO (2023-03-10T10:00:00) or MJD seconds")
    p.add_argument("--tend", default=None, help="end time: ISO or MJD seconds")
    p.add_argument("--pol", default="I", type=str.upper, choices=POL_CHOICES,
                   help="I = mean of the parallel hands (default), or one product")
    p.add_argument("--freqres", default="auto", choices=["auto", "subband", "channel"],
                   help="frequency columns: one per MS/spectral window, or channels")
    p.add_argument("--chanavg", type=int, default=0,
                   help="average N channels per column (N >= 1; 0 = automatic, the default: only --freqres auto averages, to at most 2048 columns; negative is an error)")
    p.add_argument("--chunkmb", type=float, default=128.0, help="data block per worker in MB (memory is ~3x this per worker)")
    p.add_argument("--uvstep", type=float, default=UV_STEP_S,
                   help="read UVW every this many seconds and interpolate (0 = every row; default %g)" % UV_STEP_S)
    p.add_argument("--datacolumn", default="DATA", help="column to read (default DATA)")
    p.add_argument("--saveplot", action="store_true", help="save a PNG next to the FITS")
    p.add_argument("--plotfile", default=None, help="plot file name, type from its extension: .png .pdf .svg .jpg .tif .eps (implies --saveplot)")
    p.add_argument("--showplot", action="store_true", help="show the plot in a window")
    p.add_argument("--overwrite", action="store_true", help="replace an existing FITS / PNG (never done otherwise)")
    p.add_argument("--list", action="store_true", help="only print a summary of the inputs")
    p.add_argument("--maxrows", type=int, default=0, help=argparse.SUPPRESS)  # benchmarking: rows per MS
    return p


def main(argv=None):
    p = build_parser()
    try:
        a = p.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    rep = Reporter(protocol=True)
    install_signal_handlers()
    inputs = a.indir if a.indir else a.ms
    try:
        if a.list:
            s = summarize(inputs, a.datacolumn, max_workers=a.ncpu if a.ncpu > 0 else 4,
                          progress=rep.progress)
            if not s["count"]:
                if s["failed"]:
                    f = s["failed"][0]
                    raise EngineError("cannot read any Measurement Set (%s): %s"
                                      % (os.path.basename(f["path"].rstrip("/")), f["error"]))
                raise EngineError("no readable Measurement Sets found in %s" % (inputs,))
            rep.summary(s)
            return 0
        if not a.outfits:
            raise EngineError("--outfits is required", 2)
        opts = {k: getattr(a, k) for k in DEFAULTS}
        deferred = []
        out = run(inputs, a.outfits, rep, saveplot=a.saveplot, plotfile=a.plotfile,
                  showplot=a.showplot, overwrite=a.overwrite, defer_show=deferred, **opts)
        rep.result(out)
        for show in deferred:
            show()
        return 0
    except EngineError as e:
        rep.error(str(e))
        return e.code
    except Interrupted as e:
        name = signal.Signals(e.signum).name if hasattr(signal, "Signals") else str(e.signum)
        rep.error("interrupted by %s" % name)
        return 128 + e.signum
    except KeyboardInterrupt:
        rep.error("interrupted by SIGINT")
        return 130
    except Exception as e:
        traceback.print_exc()
        rep.error("%s: %s" % (type(e).__name__, e))
        return 1
    finally:
        _rm_temps()


if __name__ == "__main__":
    sys.exit(main())

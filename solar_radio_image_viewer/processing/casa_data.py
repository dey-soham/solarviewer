"""
What CASA does about its own data while its tools are imported, told to a
listener.

CASA keeps the tables it computes with (the "measures": Earth orientation,
leap seconds, ephemerides; and the rest of its "casarundata") in
``~/.casa/data``. When casatools is first imported in a process it looks,
once a day, for newer ones on its servers and downloads what is new before
the import goes on (casaconfig's ``do_auto_updates``). That can hold the
start-up for a minute or more, and CASA says nothing meanwhile.

Inside ``told`` the slow steps of that (asking a server what it has, the
download) are reported to a listener, the splash screen's; what they took is
kept for a note after the start (``start_note``).

Nothing of what CASA does is changed: its own functions run, wrapped. With a
casaconfig laid out differently nothing is reported, and it runs as ever.
"""

import functools
import importlib
import inspect
import logging
import tarfile
import time
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# CASA's own switches for its daily look for newer data, and their file
SWITCHES = ("data_auto_update = False", "measures_auto_update = False")
CONFIG_FILE = "~/.casa/config.py"
HOW_TO_STOP = f"To turn that off, put {SWITCHES[0]} and {SWITCHES[1]} in {CONFIG_FILE}."
# Shown while the start waits for it
HINT = "CASA looks for newer data once a day, as it starts. " + HOW_TO_STOP

CHECK, DOWNLOAD, IDLE = "check", "download", "idle"

# casaconfig's functions that wait for a server: (module, function, which)
_SLOW = (
    ("casaconfig.private.data_available", "data_available", CHECK),
    ("casaconfig.private.measures_available", "measures_available", CHECK),
    ("casaconfig.private.do_untar_url", "do_untar_url", DOWNLOAD),
)

# What this process's start waited for
downloads = []  # (what, megabytes unpacked, seconds)
checks = []  # seconds, of each time a server was asked what it has


def data_name(file_name):
    """What a file CASA downloads holds, in words: its measures tables
    (``WSRT_Measures_….ztar``) or the rest of its data (``casarundata-…``)."""
    return "measures tables" if "measures" in str(file_name).lower() else "data"


def describe(event, what="", megabytes=0.0):
    """``(what holds the start up, how far it is, a hint)`` for an event
    heard in ``told``; empty strings when nothing holds it up any more."""
    if event == CHECK:
        return "Checking for newer CASA data…", "", HINT
    if event == DOWNLOAD:
        return f"Downloading CASA’s {what or 'data'}…", f"{megabytes:.0f} MB" if megabytes >= 1 else "", HINT
    return "", "", ""


def duration(seconds):
    """``seconds`` as "48 s" or "1 min 12 s"."""
    seconds = int(round(seconds))
    return f"{seconds} s" if seconds < 60 else f"{seconds // 60} min {seconds % 60} s"


def start_note(least=5.0):
    """``(what CASA's look for newer data cost this start, how to turn it
    off)``, a sentence each without its full stop and with: for the log and
    the status bar once the window is up. Empty strings if nothing was
    downloaded and it took under ``least`` seconds."""
    if downloads:
        what = " and ".join(dict.fromkeys(name for name, _megabytes, _seconds in downloads))
        megabytes = sum(m for _name, m, _seconds in downloads)
        size = f"{megabytes:.0f} MB in " if megabytes >= 1 else ""
        took = duration(sum(s for _name, _megabytes, s in downloads) + sum(checks))
        did = f"CASA downloaded new {what} as it started ({size}{took})"
    elif sum(checks) >= least:
        did = f"CASA took {duration(sum(checks))} to look for newer data as it started"
    else:
        return "", ""
    return did, (
        f"It looks once a day. {HOW_TO_STOP} "
        f"(Make the file if it is not there. CASA’s data then stay as they are until "
        f"“python -m casaconfig --update-all” is run.)"
    )


def _checking(original, report, state):
    """``original`` (asks a server what it has), reported while it runs."""

    @functools.wraps(original)
    def wrapper(*args, **kwargs):
        outer = not state["depth"]  # (measures_available calls itself, for each of its sites)
        started = time.monotonic()
        state["depth"] += 1
        report(CHECK)
        try:
            return original(*args, **kwargs)
        finally:
            state["depth"] -= 1
            if outer:
                checks.append(time.monotonic() - started)
            if not state["depth"]:
                report(IDLE)

    return wrapper


def _downloading(original, report, state):
    """``original`` (casaconfig's ``do_untar_url``: downloads an archive and
    unpacks it as it comes), reported while it runs with the megabytes
    unpacked so far. Those are counted where casaconfig lets a caller look
    at each file unpacked, its ``extraction_filter``; what that filter
    decides is not changed."""
    try:
        signature = inspect.signature(original)
    except (TypeError, ValueError):
        signature = None

    @functools.wraps(original)
    def wrapper(*args, **kwargs):
        # (the archive's name: the second argument)
        what = data_name(kwargs.get("tarfile_name", args[1] if len(args) > 1 else ""))
        count = {"bytes": 0, "told": 0.0}
        try:
            bound = signature.bind(*args, **kwargs)
            if "extraction_filter" in signature.parameters:
                # (None is what casaconfig itself replaces with this)
                keep = bound.arguments.get("extraction_filter") or getattr(
                    tarfile, "data_filter", lambda member, path: member
                )

                def counted(member, path):
                    kept = keep(member, path)
                    try:
                        if kept is not None:
                            count["bytes"] += int(getattr(kept, "size", 0) or 0)
                        now = time.monotonic()
                        if now - count["told"] >= 0.2:
                            count["told"] = now
                            report(DOWNLOAD, what, count["bytes"] / 1e6)
                    except Exception:  # (counting must never stop the unpacking)
                        pass
                    return kept

                bound.arguments["extraction_filter"] = counted
                args, kwargs = bound.args, bound.kwargs
        except Exception:  # a casaconfig with other arguments: no megabytes
            pass
        started = time.monotonic()
        state["depth"] += 1
        report(DOWNLOAD, what, 0.0)
        try:
            result = original(*args, **kwargs)
            downloads.append((what, count["bytes"] / 1e6, time.monotonic() - started))
            return result
        finally:
            state["depth"] -= 1
            if not state["depth"]:
                report(IDLE)

    return wrapper


@contextmanager
def told(listener):
    """Inside, ``listener(event, what, megabytes)`` hears what CASA's look
    for newer data does (as casatools is imported there):

    - ``CHECK``: a server is asked what it has;
    - ``DOWNLOAD``: ``what`` ("measures tables", "data") is being
      downloaded, ``megabytes`` of it unpacked so far (repeated as they
      grow);
    - ``IDLE``: neither any more.

    The listener is called on the thread that imports; what it raises is
    dropped.
    """
    state = {"depth": 0}

    def report(event, what="", megabytes=0.0):
        try:
            listener(event, what, megabytes)
        except Exception:
            logger.debug("A listener of CASA's data update failed", exc_info=True)

    wrapped = []
    try:
        import casaconfig

        for module_name, name, kind in _SLOW:
            try:
                # (casaconfig itself imports some of them only when it downloads)
                module = importlib.import_module(module_name)
            except Exception:
                continue
            original = getattr(module, name, None)
            if not callable(original):
                continue
            wrapper = (_checking if kind == CHECK else _downloading)(original, report, state)
            # (casaconfig's functions import these when called, from either place)
            for owner in (module, casaconfig):
                if getattr(owner, name, None) is original:
                    setattr(owner, name, wrapper)
                    wrapped.append((owner, name, original))
    except Exception:  # CASA is not installed, or its configuration does not load
        pass
    try:
        yield
    finally:
        for owner, name, original in reversed(wrapped):
            setattr(owner, name, original)

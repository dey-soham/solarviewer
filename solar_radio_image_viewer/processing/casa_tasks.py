"""
Running a CASA task (exportfits, importfits, imsubimage, ...) in a separate
Python process.

CASA tasks crash or clash with PyQt5 when run in the GUI's own process, so
each runs in a fresh interpreter. Its arguments travel as JSON on stdin and
are never pasted into Python source, so any file name is safe. The process
runs in the temporary folder, where CASA's ``<task>.last`` files go.
"""

import json
import subprocess
import sys
import tempfile

_SCRIPT = """
import json, sys
import solar_radio_image_viewer  # sends CASA's log to the null device
import casatasks

request = json.load(sys.stdin)
try:
    result = getattr(casatasks, request["task"])(**request["kwargs"])
except Exception as e:
    print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))
    sys.exit(1)
try:
    print(json.dumps({"ok": True, "result": result}))
except (TypeError, ValueError):
    print(json.dumps({"ok": True, "result": None}))
"""


class CasaTaskError(RuntimeError):
    """A CASA task failed, or its process did."""


def run_casa_task(task, timeout=None, **kwargs):
    """Run ``casatasks.<task>(**kwargs)`` in a separate Python process.

    Paths in ``kwargs`` should be absolute (the task runs in the temporary
    folder). Returns the task's result if it is JSON-serializable, else None.
    Raises CasaTaskError if the task fails.
    """
    process = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        input=json.dumps({"task": task, "kwargs": kwargs}),
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=tempfile.gettempdir(),
    )
    # The reply is the last JSON line: CASA may print lines of its own first
    reply = None
    for line in reversed(process.stdout.splitlines()):
        try:
            reply = json.loads(line)
            break
        except ValueError:
            continue
    if not isinstance(reply, dict) or "ok" not in reply:
        detail = (process.stderr or process.stdout).strip()[-500:]
        raise CasaTaskError(
            f"{task} failed: {detail or f'exit code {process.returncode}'}"
        )
    if not reply["ok"]:
        raise CasaTaskError(f"{task} failed: {reply['error']}")
    return reply["result"]

import sys
import os
import platform
import re
import traceback
import subprocess
from datetime import datetime
from pathlib import Path
from .version import __version__

CRASH_REPORTS_DIR = Path.home() / ".solarviewer" / "crash_reports"
# How much of the log a report keeps (a GitHub issue takes 65,536 characters)
LOG_TAIL_CHARS = 15000


def get_library_versions():
    """Collect versions of key scientific libraries."""
    libraries = [
        "PyQt5",
        "numpy",
        "astropy",
        "sunpy",
        "casatools",
        "casatasks",
        "scipy",
        "matplotlib",
        "pandas",
    ]
    versions = {}
    for lib in libraries:
        try:
            import importlib
            module = importlib.import_module(lib)
            
            # Strategy 1: __version__ (Standard)
            if hasattr(module, "__version__"):
                versions[lib] = str(module.__version__)
            # Strategy 2: version_string() or version() (CASA specific)
            elif hasattr(module, "version_string") and callable(module.version_string):
                versions[lib] = str(module.version_string())
            elif hasattr(module, "version") and callable(module.version):
                versions[lib] = str(module.version())
            # Strategy 3: PyQt5 specific
            elif lib == "PyQt5":
                from PyQt5.QtCore import PYQT_VERSION_STR
                versions[lib] = PYQT_VERSION_STR
            else:
                # Strategy 4: importlib.metadata (Python 3.8+)
                try:
                    from importlib.metadata import version
                    versions[lib] = version(lib)
                except Exception:
                    versions[lib] = "unknown"
        except ImportError:
            versions[lib] = "not installed"
        except Exception as e:
            versions[lib] = f"error: {str(e)}"
    return versions


def get_system_info():
    """Collect OS and hardware information."""
    import shutil
    import threading

    info = {
        "OS": f"{platform.system()} {platform.release()} ({platform.version()})",
        "Architecture": platform.machine(),
        "Processor": platform.processor(),
        "CPU Cores": os.cpu_count(),
        "Active Threads": threading.active_count(),
        "Python Version": sys.version,
        "Executable": sys.executable,
        "Virtual Env": "Yes" if sys.prefix != sys.base_prefix else "No",
        "Python Prefix": sys.prefix,
        "CWD": os.getcwd(),
    }

    # Disk usage
    try:
        home_usage = shutil.disk_usage(str(Path.home()))
        root_usage = shutil.disk_usage("/")
        info["Disk (Home)"] = f"{home_usage.free // (1024**3)} GB free / {home_usage.total // (1024**3)} GB total"
        info["Disk (Root)"] = f"{root_usage.free // (1024**3)} GB free / {root_usage.total // (1024**3)} GB total"
    except Exception:
        pass

    # Memory info
    if platform.system() == "Linux":
        try:
            mem = subprocess.check_output(["free", "-h"]).decode("utf-8")
            info["Memory Row"] = mem.split("\n")[1] if len(mem.split("\n")) > 1 else "unknown"
        except Exception:
            pass

    return info


# Environment variables that change how SolarViewer, Qt, CASA or the numeric
# libraries behave; the rest are left out of crash reports
_REPORTED_ENV_PREFIXES = (
    "QT_", "XDG_SESSION_TYPE", "WAYLAND_DISPLAY", "DISPLAY", "LANG", "LC_",
    "CASA", "OPENSSL_CONF", "MPLBACKEND", "OMP_", "MKL_", "OPENBLAS_",
    "CONDA_DEFAULT_ENV", "VIRTUAL_ENV", "PYTHONHASHSEED",
)
_SECRET_WORDS = ("PASSWORD", "SECRET", "KEY", "TOKEN", "AUTH", "COOKIE", "PASS")


def get_environment_info():
    """The environment variables that affect the application (values of any
    that look secret masked)."""
    env_info = {}
    for k, v in sorted(os.environ.items()):
        if not k.startswith(_REPORTED_ENV_PREFIXES):
            continue
        env_info[k] = "********" if any(w in k.upper() for w in _SECRET_WORDS) else v
    return env_info


def scrub(text):
    """``text`` with the home folder (which names the user) written as ~, and
    user@host addresses (SSH servers, e-mail) as <user>@<host>."""
    text = text.replace(str(Path.home()), "~")
    return re.sub(r"\b[\w.-]+@[\w-]+(\.[\w-]+)*\b", "<user>@<host>", text)


def generate_crash_report(exctype, value, tb, fatal=True):
    """Generate a detailed crash report and save it to a file.

    ``fatal`` records whether the application exited because of the error.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report_filename = f"crash_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"

    CRASH_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = CRASH_REPORTS_DIR / report_filename
    # Several non-fatal errors can happen within the same second
    n = 2
    while report_path.exists():
        report_path = CRASH_REPORTS_DIR / report_filename.replace(".md", f"_{n}.md")
        n += 1

    # Get traceback
    traceback_details = "".join(traceback.format_exception(exctype, value, tb))

    # The application's recent output, from its log file (the log window's
    # text must not be read here: the error may come from another thread)
    logs = "Logs unavailable."
    try:
        from .app_logging import LOG_FILE

        with open(LOG_FILE, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 4 * LOG_TAIL_CHARS))  # UTF-8: up to 4 bytes a character
            logs = f.read().decode("utf-8", errors="replace")
    except OSError:
        pass

    # Build report
    report_lines = [
        "# SolarViewer Crash Report",
        f"\n**Timestamp:** {timestamp}",
        f"**App Version:** {__version__}",
        f"**Fatal:** {'Yes' if fatal else 'No (the application kept running)'}",
        "\n## 1. Exception Details",
        "```python",
        f"{traceback_details}",
        "```",
        "\n## 2. System Information",
        "| Field | Value |",
        "| :--- | :--- |",
    ]

    sys_info = get_system_info()
    for k, v in sys_info.items():
        if k != "Memory Row":
            report_lines.append(f"| {k} | {v} |")

    # Memory Guide Section
    if "Memory Row" in sys_info:
        report_lines.append("\n### Memory Diagnostics")
        report_lines.append("| Total | Used | Free | Shared | Buff/Cache | Available |")
        report_lines.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
        
        # Parse free -h row: Mem: total used free shared buff/cache available
        parts = sys_info["Memory Row"].split()
        if len(parts) >= 7:
            report_lines.append(f"| {' | '.join(parts[1:])} |")
        else:
            report_lines.append(f"| {sys_info['Memory Row']} |")

    report_lines.append("\n## 3. Library Versions")
    report_lines.append("| Library | Version |")
    report_lines.append("| :--- | :--- |")

    for k, v in get_library_versions().items():
        report_lines.append(f"| {k} | {v} |")

    report_lines.append("\n## 4. Environment Variables")
    report_lines.append("Those that affect Qt, CASA and the numeric libraries.\n")
    report_lines.append("| Variable | Value |")
    report_lines.append("| :--- | :--- |")
    for k, v in get_environment_info().items():
        report_lines.append(f"| {k} | {v} |")

    report_lines.append("\n## 5. Loaded Modules")
    modules = sorted(list(set(name.split(".")[0] for name in sys.modules.keys())))
    report_lines.append("`" + ", ".join(modules) + "`")

    report_lines.append("\n## 6. Application Logs")
    report_lines.append("```")
    # The end of the log: short enough to paste into an issue
    report_lines.append(logs[-LOG_TAIL_CHARS:])
    report_lines.append("```")

    # Paths, the user name and host names are left out of what users share
    report_content = scrub("\n".join(report_lines))

    try:
        with open(report_path, "w") as f:
            f.write(report_content)
        if fatal:
            print("\nCRITICAL: SolarViewer encountered an unhandled exception.")
        else:
            print("\n[ERROR] SolarViewer encountered an unhandled exception (continuing).")
        print(f"A detailed crash report has been saved to: {report_path}")
    except Exception as e:
        print(f"CRITICAL: Application crashed and failed to save report: {e}")
        print(traceback_details)

    return report_path


# Once the main window is up (see set_main_window), unhandled exceptions are
# reported and shown to the user, but the application keeps running.
_main_window = None
# Exception signature -> {"count": n, "path": report path}, so an error that
# repeats (e.g. raised from a timer) is reported once rather than every time.
_reported = {}
_error_dialog = None
# Carries errors from worker threads to the main thread (see set_main_window)
_notifier = None


def set_main_window(window):
    """Make unhandled exceptions non-fatal from now on.

    Call once the main window is shown. Before that (during startup) an
    unhandled exception still exits the application after writing a report.
    """
    global _main_window, _notifier
    _main_window = window
    if window is not None and _notifier is None:
        from PyQt5.QtCore import QObject, pyqtSignal

        class _Notifier(QObject):
            # Delivered on the main thread (queued) when emitted from a worker
            error = pyqtSignal(object, object, object, object)

        _notifier = _Notifier()
        _notifier.error.connect(_show_error)


def _exception_signature(exctype, tb):
    """Identify an error by its type and the line that raised it."""
    frames = traceback.extract_tb(tb)
    last = frames[-1] if frames else None
    return (
        exctype.__name__,
        last.filename if last else "",
        last.lineno if last else 0,
    )


def _restore_cursor():
    """Undo any wait cursors left behind by the interrupted action."""
    try:
        from PyQt5.QtWidgets import QApplication

        while QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()
        from .ui.busy import reset as reset_busy

        reset_busy()
    except Exception:
        pass


def _notify_user(exctype, value, tb, report_path):
    """Show a non-blocking message about the error (one at a time)."""
    global _error_dialog
    if _error_dialog is not None:
        return  # Already showing one; the report is saved regardless

    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QMessageBox

    dialog = QMessageBox(_main_window)
    dialog.setIcon(QMessageBox.Warning)
    dialog.setWindowTitle("Unexpected Error")
    dialog.setText(
        "Something went wrong. SolarViewer is still running, but the last "
        "action may not have completed."
    )
    dialog.setInformativeText(
        f"{exctype.__name__}: {value}\n\nA crash report was saved to:\n{report_path}"
    )
    dialog.setDetailedText("".join(traceback.format_exception(exctype, value, tb)))
    dialog.setTextInteractionFlags(Qt.TextSelectableByMouse)
    dialog.setStandardButtons(QMessageBox.Ok)
    dialog.setWindowModality(Qt.NonModal)
    dialog.setAttribute(Qt.WA_DeleteOnClose)

    def on_finished(_result):
        global _error_dialog
        _error_dialog = None

    dialog.finished.connect(on_finished)
    _error_dialog = dialog
    dialog.show()


def _show_error(exctype, value, tb, report_path):
    """Main thread only: reset the cursor and show the error dialog."""
    _restore_cursor()
    _notify_user(exctype, value, tb, report_path)


def _handle_nonfatal(exctype, value, tb):
    import threading

    on_main_thread = threading.current_thread() is threading.main_thread()

    signature = _exception_signature(exctype, tb)
    entry = _reported.get(signature)
    if entry is not None:
        entry["count"] += 1
        print(
            f"[ERROR] {exctype.__name__}: {value} "
            f"(repeated {entry['count']}x; report: {entry['path']})"
        )
        if on_main_thread:
            _restore_cursor()
        return

    report_path = generate_crash_report(exctype, value, tb, fatal=False)
    _reported[signature] = {"count": 1, "path": report_path}
    # Qt widgets may only be touched from the main thread. Errors escaping a
    # QThread arrive here on that worker thread, so hand them over.
    if on_main_thread:
        _show_error(exctype, value, tb, report_path)
    elif _notifier is not None:
        _notifier.error.emit(exctype, value, tb, report_path)


def crash_excepthook(exctype, value, tb):
    """The sys.excepthook that catches all crashes."""
    # Run original hook if it's a KeyboardInterrupt (so Ctrl+C still works normally)
    if issubclass(exctype, KeyboardInterrupt):
        sys.__excepthook__(exctype, value, tb)
        return

    if _main_window is not None:
        try:
            _handle_nonfatal(exctype, value, tb)
            return
        except Exception:
            # Reporting itself failed; fall back to the fatal path below
            traceback.print_exc()

    # Generate and save report
    generate_crash_report(exctype, value, tb)

    # Exit the app
    sys.exit(1)


def thread_excepthook(args):
    """Catch unhandled exceptions in non-main threads (Python 3.8+).

    Without this, any exception that escapes a QThread.run() (e.g. a
    paramiko socket timeout or EOFError during SFTP transfer) kills the
    thread silently — sys.excepthook is never called and no crash report
    is generated.
    """
    # Ignore SystemExit and KeyboardInterrupt in threads
    if args.exc_type is not None and issubclass(
        args.exc_type, (SystemExit, KeyboardInterrupt)
    ):
        return

    # Report it (non-fatal: don't exit the app), and show it once the main
    # window is up
    try:
        if _main_window is not None:
            _handle_nonfatal(args.exc_type, args.exc_value, args.exc_traceback)
        else:
            generate_crash_report(
                args.exc_type, args.exc_value, args.exc_traceback, fatal=False
            )
    except Exception:
        # Last resort: at least print the traceback
        traceback.print_exception(args.exc_type, args.exc_value, args.exc_traceback)


def install_crash_handler():
    """Enable the crash handler for both main and worker threads."""
    sys.excepthook = crash_excepthook

    # Also catch unhandled exceptions in non-main threads (Python 3.8+)
    import threading

    if hasattr(threading, "excepthook"):
        threading.excepthook = thread_excepthook

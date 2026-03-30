import sys
import os
import platform
import traceback
import subprocess
import json
from datetime import datetime
from pathlib import Path
from .version import __version__


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
                except:
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
    except:
        pass

    # Memory info
    if platform.system() == "Linux":
        try:
            mem = subprocess.check_output(["free", "-h"]).decode("utf-8")
            info["Memory Row"] = mem.split("\n")[1] if len(mem.split("\n")) > 1 else "unknown"
        except:
            pass

    return info


def get_environment_info():
    """Collect filtered environment variables."""
    blacklist = {"PASSWORD", "SECRET", "KEY", "TOKEN", "AUTH", "COOKIE", "SUDO", "PASS"}
    env_info = {}
    for k, v in os.environ.items():
        if any(word in k.upper() for word in blacklist):
            env_info[k] = "********"
        else:
            env_info[k] = v
    return env_info


def generate_crash_report(exctype, value, tb):
    """Generate a detailed crash report and save it to a file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report_filename = f"crash_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"

    # Define crash reports directory
    reports_dir = Path.home() / ".solarviewer" / "crash_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / report_filename

    # Get traceback
    traceback_details = "".join(traceback.format_exception(exctype, value, tb))

    # Get application logs (if LogConsole is initialized)
    logs = "Log console not initialized or logs unavailable."
    try:
        from .log_console import LogConsole

        console = LogConsole.get_instance()
        if console:
            logs = console.text_edit.toPlainText()
    except:
        pass

    # Build report
    report_lines = [
        "# SolarViewer Crash Report",
        f"\n**Timestamp:** {timestamp}",
        f"**App Version:** {__version__}",
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
    report_lines.append("| Variable | Value |")
    report_lines.append("| :--- | :--- |")
    for k, v in get_environment_info().items():
        report_lines.append(f"| {k} | {v} |")

    report_lines.append("\n## 5. Loaded Modules")
    modules = sorted(list(set(name.split(".")[0] for name in sys.modules.keys())))
    report_lines.append("`" + ", ".join(modules) + "`")

    report_lines.append("\n## 6. Application Logs")
    report_lines.append("```")
    report_lines.append(logs[-50000:])  # Last 50k chars of logs
    report_lines.append("```")

    report_content = "\n".join(report_lines)

    try:
        with open(report_path, "w") as f:
            f.write(report_content)
        print(f"\nCRITICAL: SolarViewer encountered an unhandled exception.")
        print(f"A detailed crash report has been saved to: {report_path}")
    except Exception as e:
        print(f"CRITICAL: Application crashed and failed to save report: {e}")
        print(traceback_details)

    return report_path


def crash_excepthook(exctype, value, tb):
    """The sys.excepthook that catches all crashes."""
    # Run original hook if it's a KeyboardInterrupt (so Ctrl+C still works normally)
    if issubclass(exctype, KeyboardInterrupt):
        sys.__excepthook__(exctype, value, tb)
        return

    # Generate and save report
    generate_crash_report(exctype, value, tb)

    # Exit the app
    sys.exit(1)


def install_crash_handler():
    """Enable the crash hander."""
    sys.excepthook = crash_excepthook

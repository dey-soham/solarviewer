"""
The one QApplication every SolarViewer window runs in.

``create_app`` sets up display scaling, creates the application, loads the
bundled fonts and applies the theme; ``apply_theme`` restyles a running
application (it is also registered to run on every theme change). The main
window and each tool launched on its own (``viewds``, ``viewlogs``,
``viewcaltable``, ``heliobrowser``, ``viewsolaractivity``...) all go
through here, so they are scaled, themed and fonted alike.
"""

import math
import os
import re
import subprocess
import sys
from contextlib import contextmanager

from PyQt5.QtCore import QSettings, Qt, qInstallMessageHandler
from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import QApplication

from .activity import start_indicator
from .styles import theme_manager

SETTINGS = ("SolarViewer", "SolarViewer")
LOWEST_UI_SCALE = 0.5
UI_SCALE_STEP = 0.05

# The name of the desktop file install_utils writes, so that the desktop shows
# SolarViewer's name and icon for its windows
DESKTOP_FILE_NAME = "solarviewer"

# Preferences' "Window system": on GNOME, Qt 5 runs on X11 (through XWayland)
# unless told otherwise, which GNOME 46 stretches, blurred, on a scaled screen
WINDOW_SYSTEMS = ("auto", "x11", "wayland")
WAYLAND = "wayland;xcb"  # X11 if Wayland does not work out
X11 = "xcb"

# Qt tells a display's scale only once an application exists, which is too late
# to choose QT_SCALE_FACTOR, so a short-lived process asks it first
_MEASURE_DISPLAY_SCALE = """
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QGuiApplication
QGuiApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
policy = getattr(Qt, "HighDpiScaleFactorRoundingPolicy", None)
if policy is not None:
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(policy.PassThrough)
app = QGuiApplication([])
print(app.platformName(), min(screen.devicePixelRatio() for screen in app.screens()))
"""

_MUTTER_STATE = [
    "gdbus", "call", "--session", "--timeout", "1",
    "--dest", "org.gnome.Mutter.DisplayConfig",
    "--object-path", "/org/gnome/Mutter/DisplayConfig",
    "--method", "org.gnome.Mutter.DisplayConfig.GetCurrentState",
]
# A logical monitor in GetCurrentState's answer: (x, y, scale, transform,
# primary, [monitors], properties). Nothing else there starts with two numbers.
_LOGICAL_MONITOR = re.compile(
    r"\((-?\d+), (-?\d+), (\d+(?:\.\d+)?(?:[eE][-+]?\d+)?), (?:uint32 )?\d+, (?:true|false), \["
)

# Said by Qt 5 on every start on GNOME, and by its Wayland plugin, both harmless
_QT_NOISE = (
    "Ignoring XDG_SESSION_TYPE=wayland on Gnome",
    "QSocketNotifier: Can only be used with threads started with QThread",
)
# Said on Wayland each time a window already open is brought up again
_WAYLAND_NOISE = ("Wayland does not support QWindow::requestActivate()",)

# The application's property holding the QT_SCALE_FACTOR it started with
# (the variable itself is not left in the environment)
UI_SCALE_PROPERTY = "solarviewerUiScale"


def saved_theme():
    """The theme chosen in the main window's View menu (dark by default)."""
    theme = QSettings(*SETTINGS).value("theme", theme_manager.DARK)
    return theme if theme in (theme_manager.DARK, theme_manager.LIGHT) else theme_manager.DARK


def smallest_ui_scale(display):
    """The smallest scale Preferences can give on a display of scale ``display``.

    Qt 5 paints windows in pieces, Preferences included, when the display's
    scale times this one comes to less than 1. So 0.95 is fine on a 2x display,
    where it makes 1.9, and is not on a 1x display.
    """
    steps = math.ceil(1 / (display * UI_SCALE_STEP) - 1e-9)
    return min(1.0, max(LOWEST_UI_SCALE, round(steps * UI_SCALE_STEP, 2)))


def display_scale():
    """The scale of the running application's displays (of the lowest, if they
    differ), leaving out the one chosen in Preferences.

    On Wayland Qt 5 draws at a whole scale and GNOME shrinks that to the
    screen's (2 for a 1.75x screen), so there GNOME's own scale counts.
    """
    chosen = ui_scale_in_use()
    scale = min((s.devicePixelRatio() for s in QApplication.screens()), default=chosen) / chosen
    if QApplication.platformName() == "wayland":
        scale = min([scale] + (monitor_scales() or []))
    return scale


def ui_scale_in_use():
    """The QT_SCALE_FACTOR the running application started with (1 if none)."""
    app = QApplication.instance()
    chosen = app.property(UI_SCALE_PROPERTY) if app is not None else None
    if chosen is None:  # an application not made by create_app
        chosen = _scale_factor(os.environ.get("QT_SCALE_FACTOR"))
    return float(chosen)


def _scale_factor(value):
    try:
        return float(value) if value else 1.0
    except ValueError:
        return 1.0


def monitor_scales():
    """The scale GNOME gives each screen, asked over D-Bus (some 5 ms, at most
    1.5 s if GNOME does not answer), or None where that cannot be found out (not GNOME, no gdbus, no answer)."""
    try:
        answer = subprocess.run(_MUTTER_STATE, capture_output=True, text=True, timeout=1.5)
    except (OSError, subprocess.SubprocessError):
        return None
    if answer.returncode != 0:
        return None
    scales = [float(match.group(3)) for match in _LOGICAL_MONITOR.finditer(answer.stdout)]
    return scales or None


def _remote(env):
    return any(env.get(name) for name in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"))


def _wayland_socket(env, exists=os.path.exists):
    """Whether the Wayland display named in ``env`` is there."""
    name = env.get("WAYLAND_DISPLAY")
    if not name:
        return False
    return exists(os.path.join(env.get("XDG_RUNTIME_DIR", ""), name))


def choose_platform(env, choice, scales, argv=(), linux=True, exists=os.path.exists):
    """The Qt platform to start on, or None to leave it to Qt.

    ``env`` is the environment, ``choice`` the "window_system" preference and
    ``scales`` a function giving the screens' scales (or None), asked only on
    GNOME. A platform the user or a script already chose is never changed.
    """
    named = any(arg in ("-platform", "--platform") for arg in argv)
    if env.get("QT_QPA_PLATFORM") or named or not linux:
        return None
    wayland = (
        env.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        and not _remote(env)
        and _wayland_socket(env, exists)
    )
    if choice == "x11":
        return X11 if env.get("DISPLAY") else None
    if choice == "wayland":
        return WAYLAND if wayland else None
    # Elsewhere Qt picks Wayland itself; on GNOME only a scaled screen gains
    desktops = (env.get("XDG_CURRENT_DESKTOP", "") + ":" + env.get("XDG_SESSION_DESKTOP", "")).lower()
    if not wayland or "gnome" not in desktops:
        return None
    found = scales()
    if found and any(abs(scale - 1) > 0.01 for scale in found):
        return WAYLAND
    return None


def saved_window_system():
    """The "Window system" chosen in Preferences: auto, x11 or wayland."""
    value = QSettings(*SETTINGS).value("window_system", "auto")
    return value if value in WINDOW_SYSTEMS else "auto"


def _measure_display_scale(platform=None, scales=None):
    """The display's scale before there is an application, on ``platform``
    (as the application will run). On Wayland, GNOME's ``scales`` count if
    lower. 1 if it cannot be found out, with which a scale below 1 is not
    applied: safe on any display."""
    env = {name: value for name, value in os.environ.items() if name != "QT_SCALE_FACTOR"}
    if platform:
        env["QT_QPA_PLATFORM"] = platform
    try:
        answer = subprocess.run(
            [sys.executable, "-c", _MEASURE_DISPLAY_SCALE],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        name, measured = answer.stdout.split()[-2:]
        measured = float(measured)
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return 1.0
    if name == "wayland":
        measured = min([measured] + list(scales or monitor_scales() or []))
    return measured


def _setup_scaling(platform=None, scales=None):
    """Display scaling, before the QApplication exists.

    The system's scale factor is used as it is (a 1.5x or 1.15x screen is
    not rounded to 1x or 2x), times the scale chosen in Preferences.
    """
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    policy = getattr(Qt, "HighDpiScaleFactorRoundingPolicy", None)
    if policy is not None:
        QApplication.setHighDpiScaleFactorRoundingPolicy(policy.PassThrough)
    ui_scale = QSettings(*SETTINGS).value("ui_scale_factor", 1.0, type=float)
    # A value saved by an older version, or before the display changed, must
    # not lock the user out of the application
    if ui_scale < 1.0:
        ui_scale = max(ui_scale, smallest_ui_scale(_measure_display_scale(platform, scales)))
    if ui_scale != 1.0:
        os.environ["QT_SCALE_FACTOR"] = str(ui_scale)


def create_app(theme=None, argv=None):
    """The application, created if there is none yet, in ``theme`` ("dark",
    "light", or None for the saved choice), with fonts and styling applied.
    The styling follows every later theme change on its own."""
    app = QApplication.instance()
    if app is None:
        argv = sys.argv if argv is None else argv
        found = []

        def scales():
            found[:] = monitor_scales() or []
            return found

        platform = choose_platform(
            os.environ, saved_window_system(), scales, argv, sys.platform.startswith("linux")
        )
        users_scale = os.environ.get("QT_SCALE_FACTOR")
        _setup_scaling(platform, found)
        in_use = _scale_factor(os.environ.get("QT_SCALE_FACTOR"))
        try:
            with _on_platform(platform):
                app = QApplication(argv)
        finally:
            # Qt has read it. Left in the environment, it would scale what the
            # application opens, and a restart would keep it in place of the
            # scale it works out afresh.
            if users_scale is None:
                os.environ.pop("QT_SCALE_FACTOR", None)
            else:
                os.environ["QT_SCALE_FACTOR"] = users_scale
        app.setProperty(UI_SCALE_PROPERTY, in_use)
        if app.platformName() == "wayland":
            qInstallMessageHandler(_quiet_wayland)
        app.setStyle("Fusion")
        app.setDesktopFileName(DESKTOP_FILE_NAME)
    theme_manager._current_theme = theme or saved_theme()
    theme_manager.initialize_fonts()
    apply_theme(app)
    theme_manager.register_callback(lambda _theme: apply_theme(app), owner=app)
    start_indicator()
    return app


@contextmanager
def _on_platform(platform):
    """Qt started on ``platform`` (None: Qt's own choice). The variable that
    says so is gone again once it has started, so that what the application
    opens (a browser, a file manager) is not told it; a SolarViewer tool it
    starts makes the same choice through here."""
    if platform:
        os.environ["QT_QPA_PLATFORM"] = platform
    previous = qInstallMessageHandler(_quiet_start)
    try:
        yield
    finally:
        qInstallMessageHandler(previous)
        if platform:
            os.environ.pop("QT_QPA_PLATFORM", None)


def _quiet_start(_kind, context, message, noise=_QT_NOISE):
    if not any(line in message for line in noise):
        category = getattr(context, "category", None)
        if category and category != "default":
            message = f"{category}: {message}"
        print(message, file=sys.stderr)


def _quiet_wayland(kind, context, message):
    _quiet_start(kind, context, message, _QT_NOISE + _WAYLAND_NOISE)


def restart_application():
    """Close SolarViewer and start it again as it was started."""
    # The command as it was typed: sys.argv loses a "python -m"
    python = sys.executable
    args = sys.orig_argv[1:]
    QSettings(*SETTINGS).sync()  # (exec does not wait for QSettings to write)

    def start_new_instance():
        os.execv(python, [python] + args)

    app = QApplication.instance()
    if app:
        # Connected last, so that what is already waiting for the quit
        # (the batch runner, a pip update) still runs. Closing the windows
        # stops the workers and removes the tabs' temporary files.
        app.aboutToQuit.connect(start_new_instance)
        app.closeAllWindows()
        app.quit()
    else:
        start_new_instance()


def apply_theme(app=None):
    """Style ``app`` (the running application) in the current theme: the Qt
    palette, the stylesheet and, if Matplotlib is loaded, its colours."""
    app = app or QApplication.instance()
    if app is None:
        return
    palette = theme_manager.palette
    qt_palette = QPalette()
    colors = {
        QPalette.Window: palette["window"],
        QPalette.WindowText: palette["text"],
        QPalette.Base: palette["base"],
        QPalette.AlternateBase: palette["surface"],
        QPalette.Text: palette["text"],
        QPalette.Button: palette["button"],
        QPalette.ButtonText: palette["text"],
        QPalette.Highlight: palette["highlight"],
        QPalette.HighlightedText: palette["highlight_text"],
        QPalette.Link: palette["link"],
        QPalette.LinkVisited: palette["link"],
        QPalette.ToolTipBase: palette["surface_elevated"],
        QPalette.ToolTipText: palette["text"],
        QPalette.PlaceholderText: palette["text_secondary"],
    }
    for role, color in colors.items():
        qt_palette.setColor(role, QColor(color))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        qt_palette.setColor(QPalette.Disabled, role, QColor(palette["disabled"]))
    app.setPalette(qt_palette)
    app.setStyleSheet(theme_manager.stylesheet)

    matplotlib = sys.modules.get("matplotlib")
    if matplotlib is not None:
        matplotlib.rcParams.update(theme_manager.matplotlib_params)

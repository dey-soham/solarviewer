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
import subprocess
import sys

from PyQt5.QtCore import QSettings, Qt
from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import QApplication

from .activity import start_indicator
from .styles import theme_manager

SETTINGS = ("SolarViewer", "SolarViewer")
LOWEST_UI_SCALE = 0.5
UI_SCALE_STEP = 0.05

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
print(min(screen.devicePixelRatio() for screen in app.screens()))
"""


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
    differ), leaving out the one chosen in Preferences."""
    chosen = float(os.environ.get("QT_SCALE_FACTOR", 1))
    return min((s.devicePixelRatio() for s in QApplication.screens()), default=chosen) / chosen


def _measure_display_scale():
    """The display's scale before there is an application. 1 if it cannot be
    found out, with which a scale below 1 is not applied: safe on any display."""
    env = {name: value for name, value in os.environ.items() if name != "QT_SCALE_FACTOR"}
    try:
        answer = subprocess.run(
            [sys.executable, "-c", _MEASURE_DISPLAY_SCALE],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return float(answer.stdout.split()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return 1.0


def _setup_scaling():
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
        ui_scale = max(ui_scale, smallest_ui_scale(_measure_display_scale()))
    if ui_scale != 1.0:
        os.environ["QT_SCALE_FACTOR"] = str(ui_scale)


def create_app(theme=None, argv=None):
    """The application, created if there is none yet, in ``theme`` ("dark",
    "light", or None for the saved choice), with fonts and styling applied.
    The styling follows every later theme change on its own."""
    app = QApplication.instance()
    if app is None:
        _setup_scaling()
        app = QApplication(sys.argv if argv is None else argv)
        app.setStyle("Fusion")
    theme_manager._current_theme = theme or saved_theme()
    theme_manager.initialize_fonts()
    apply_theme(app)
    theme_manager.register_callback(lambda _theme: apply_theme(app), owner=app)
    start_indicator()
    return app


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

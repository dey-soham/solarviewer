"""
The one QApplication every SolarViewer window runs in.

``create_app`` sets up display scaling, creates the application, loads the
bundled fonts and applies the theme; ``apply_theme`` restyles a running
application (it is also registered to run on every theme change). The main
window and each tool launched on its own (``viewds``, ``viewlogs``,
``viewcaltable``, ``heliobrowser``, ``viewsolaractivity``...) all go
through here, so they are scaled, themed and fonted alike.
"""

import os
import sys

from PyQt5.QtCore import QSettings, Qt
from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import QApplication

from .styles import theme_manager

SETTINGS = ("SolarViewer", "SolarViewer")


def saved_theme():
    """The theme chosen in the main window's View menu (dark by default)."""
    theme = QSettings(*SETTINGS).value("theme", theme_manager.DARK)
    return theme if theme in (theme_manager.DARK, theme_manager.LIGHT) else theme_manager.DARK


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
        QPalette.HighlightedText: "#ffffff",
        QPalette.Link: palette["highlight"],
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

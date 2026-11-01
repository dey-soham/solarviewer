"""
Run one of the SIMPL pipeline GUI tools as a standalone process.

SolarViewer launches these in a separate process to avoid memory conflicts
between python-casacore and casatasks/casatools:

    python -m solar_radio_image_viewer.pipeline.launch <tool> [theme] [file]

A ``file`` is opened in the tool as it appears (the Dynamic Spectrum Viewer).
"""

import os
import sys

# tool name -> (module in pipeline/, window class, accepts a ``theme`` kwarg)
TOOLS = {
    "dynamic_spectrum_viewer": ("view_dynamic_spectra_GUI", "MainWindow", True),
    "caltable_visualizer": ("caltable_visualizer", "VisualizationApp", False),
    "log_viewer": ("pipeline_logger_gui", "PipelineLoggerGUI", True),
    "create_dynamic_spectra": (
        "dynamic_spectra_dialog",
        "DynamicSpectraDialog",
        True,
    ),
}
# (as the tools' own windows are titled: the small window hands over to them)
TITLES = {
    "dynamic_spectrum_viewer": "Dynamic Spectra Viewer",
    "caltable_visualizer": "Bandpass and Crossphase Visualizer",
    "log_viewer": "LOFAR Pipeline Logger",
    "create_dynamic_spectra": "Create Dynamic Spectra",
}


def _starting_window(title):
    """A small empty window for the busy pill until the tool's own is up."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication, QStyle, QWidget

    window = QWidget()
    window.setWindowTitle(title)
    window.resize(460, 120)
    screen = QApplication.primaryScreen().availableGeometry()
    window.setGeometry(QStyle.alignedRect(Qt.LeftToRight, Qt.AlignCenter, window.size(), screen))
    window.show()
    QApplication.processEvents()
    return window


def parse_args(argv):
    """(tool, theme, file to open or None) from the command line."""
    if not argv or argv[0] not in TOOLS:
        sys.exit(f"usage: launch.py {{{','.join(TOOLS)}}} [theme] [file]")
    tool = argv[0]
    theme = argv[1] if len(argv) > 1 and argv[1] else "dark"
    path = argv[2] if len(argv) > 2 and argv[2] else None
    return tool, theme, path


def main(argv=None):
    tool, theme, path = parse_args(sys.argv[1:] if argv is None else argv)

    # Clear opencv Qt paths before importing PyQt5
    for key in ["QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"]:
        if key in os.environ:
            del os.environ[key]

    import importlib

    from PyQt5.QtCore import QTimer

    from ..ui.app import apply_theme, create_app
    from ..ui.busy import busy

    # The application first, as the tool takes a second or more to import
    # and nothing can show before there is one
    app = create_app(theme)
    module_name, class_name, takes_theme = TOOLS[tool]
    starting = _starting_window(TITLES[tool])
    with busy(f"Starting {TITLES[tool]}…", starting):
        window_class = getattr(
            importlib.import_module(f".{module_name}", __package__), class_name
        )
        apply_theme(app)  # (Matplotlib's colours, now that it is loaded)
        window = window_class(theme=theme) if takes_theme else window_class()
    window.show()
    starting.close()
    if path and hasattr(window, "openFile"):
        # Once the window is on screen, so its wait cursor and messages show
        QTimer.singleShot(0, lambda: window.openFile(path))
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

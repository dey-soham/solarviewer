"""
Run one of the SIMPL pipeline GUI tools as a standalone process.

SolarViewer launches these in a separate process to avoid memory conflicts
between python-casacore and casatasks/casatools:

    python -m solar_radio_image_viewer.pipeline.launch <tool> [theme]
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


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in TOOLS:
        sys.exit(f"usage: launch.py {{{','.join(TOOLS)}}} [theme]")
    tool = argv[0]
    theme = argv[1] if len(argv) > 1 else "dark"

    # Clear opencv Qt paths before importing PyQt5
    for key in ["QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"]:
        if key in os.environ:
            del os.environ[key]

    import importlib

    from PyQt5.QtWidgets import QApplication

    from .simpl_theme import apply_theme, setup_high_dpi

    module_name, class_name, takes_theme = TOOLS[tool]
    window_class = getattr(
        importlib.import_module(f".{module_name}", __package__), class_name
    )

    setup_high_dpi()
    app = QApplication(sys.argv)
    apply_theme(app, theme)
    window = window_class(theme=theme) if takes_theme else window_class()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

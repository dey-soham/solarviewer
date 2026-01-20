# __init__.py
# Suppress CASA warnings (C++ level) before any imports
import os as _os

_os.environ.setdefault("CASA_LOGLEVEL", "ERROR")

# Import version directly
from .version import __version__

# Lazy import mapping
_modules = {
    "viewer": ["SolarRadioImageViewerApp", "SolarRadioImageTab"],
    "utils": None,
    "norms": None,
    "styles": None,
    "dialogs": None,
    "searchable_combobox": None,
    "create_video": ["VideoProgress"],
    "video_dialog": ["VideoCreationDialog"],
}


def __getattr__(name):
    # Check if the name is a sub-module
    if name in _modules:
        import importlib

        return importlib.import_module(f".{name}", __package__)

    # Check if the name is an attribute inside a sub-module
    for mod_name, attrs in _modules.items():
        if attrs and name in attrs:
            import importlib

            module = importlib.import_module(f".{mod_name}", __package__)
            return getattr(module, name)

    raise AttributeError(f"module {__name__} has no attribute {name}")


# Define __all__ for 'from X import *' support (though avoid using it for speed)
__all__ = [
    "SolarRadioImageViewerApp",
    "SolarRadioImageTab",
    "VideoProgress",
    "VideoCreationDialog",
]

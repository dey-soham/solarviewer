# __init__.py
# Suppress CASA warnings (C++ level) before any imports
import os as _os

_os.environ.setdefault("CASA_LOGLEVEL", "ERROR")


def _discard_casa_log():
    """Keep CASA from writing a casa-<time>.log into the current folder.

    casatools opens its log file when first imported, at the path in
    casaconfig's configuration. SolarViewer does not keep CASA's log (see
    processing.dependencies), so the path is set to the null device first.
    Scripts run in a subprocess import this package for the same reason.
    """
    import sys

    if "casatools" in sys.modules:
        return
    try:
        import casaconfig.config as casa_config
    except Exception:
        return  # CASA not installed, or its configuration failed to load
    casa_config.logfile = _os.devnull


_discard_casa_log()

# Import version directly
from .version import __version__ as __version__  # re-exported

# Lazy import mapping
_modules = {
    "viewer": ["SolarRadioImageViewerApp", "SolarRadioImageTab"],
    "utils": None,
    "dialogs": None,
    "ui": None,
    "processing": None,
    "coordinates": None,
    "video": None,
    "video.dialog": ["VideoCreationDialog"],
}

# Modules moved during the v1.3 restructuring. Importing the
# old path still works but returns the new module with a DeprecationWarning.
_MOVED_MODULES = {
    "norms": "processing.norms",
    "create_video": "video.creator",
    "video_dialog": "video.dialog",
    "video_utils": "video.utils",
    "helioprojective": "coordinates.helioprojective",
    "helioprojective_viewer": "coordinates.helioprojective_viewer",
    "move_phasecenter": "coordinates.move_phasecenter",
    "from_simpl": "pipeline",
    "styles": "ui.styles",
    "searchable_combobox": "ui.searchable_combobox",
}


class _MovedModuleFinder:
    """Import hook that aliases old module paths to their new locations."""

    def find_spec(self, fullname, path, target=None):
        prefix = __name__ + "."
        if not fullname.startswith(prefix):
            return None
        old = fullname[len(prefix) :]
        # Match the module itself or a submodule of a moved package
        for old_name, new_name in _MOVED_MODULES.items():
            if old == old_name or old.startswith(old_name + "."):
                new = new_name + old[len(old_name) :]
                break
        else:
            return None
        import importlib.util

        return importlib.util.spec_from_loader(fullname, _MovedModuleLoader(new))


class _MovedModuleLoader:
    def __init__(self, new):
        self.new = f"{__name__}.{new}"

    def create_module(self, spec):
        import importlib
        import warnings

        warnings.warn(
            f"{spec.name} has moved to {self.new}", DeprecationWarning, stacklevel=2
        )
        return importlib.import_module(self.new)

    def exec_module(self, module):
        pass


import sys as _sys

if not any(isinstance(f, _MovedModuleFinder) for f in _sys.meta_path):
    # First, so a moved package's submodules resolve to the new modules
    # instead of being loaded a second time through the aliased package path
    _sys.meta_path.insert(0, _MovedModuleFinder())


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
    "VideoCreationDialog",
]

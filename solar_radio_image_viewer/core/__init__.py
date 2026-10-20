"""
Core viewer building blocks for SolarViewer.

Like ``tools/``, each module holds a mixin class combined into the main
classes in ``viewer.py``:

- ``SolarRadioImageTab``: CanvasMixin, ContoursMixin, FileNavigationMixin,
  PresetsMixin
- ``SolarRadioImageViewerApp``: ExportsMixin, ExternalToolsMixin,
  PresetMenuMixin, RemoteModeMixin
"""

from .canvas import CanvasMixin
from .contours import ContoursMixin
from .exports import ExportsMixin
from .external_tools import ExternalToolsMixin
from .file_navigation import FileNavigationMixin
from .presets import PresetMenuMixin, PresetsMixin
from .remote_mode import RemoteModeMixin

__all__ = [
    "CanvasMixin",
    "ContoursMixin",
    "FileNavigationMixin",
    "PresetsMixin",
    "PresetMenuMixin",
    "ExportsMixin",
    "ExternalToolsMixin",
    "RemoteModeMixin",
]

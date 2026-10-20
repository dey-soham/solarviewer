"""
Core viewer building blocks for SolarViewer.

Like ``tools/``, each module holds a mixin class combined into the main
classes in ``viewer.py``:

- ``SolarRadioImageTab``: CanvasMixin, ContoursMixin, FileNavigationMixin
- ``SolarRadioImageViewerApp``: ExportsMixin, ExternalToolsMixin,
  RemoteModeMixin
"""

from .canvas import CanvasMixin
from .contours import ContoursMixin
from .exports import ExportsMixin
from .external_tools import ExternalToolsMixin
from .file_navigation import FileNavigationMixin
from .remote_mode import RemoteModeMixin

__all__ = [
    "CanvasMixin",
    "ContoursMixin",
    "FileNavigationMixin",
    "ExportsMixin",
    "ExternalToolsMixin",
    "RemoteModeMixin",
]

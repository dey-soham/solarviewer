"""
Interactive analysis tools for SolarViewer.

Each module provides a mixin class holding one tool's methods. The mixins are
combined into the main classes in ``viewer.py``:

- ``SolarRadioImageTab``: RoiStatsMixin, RulerMixin, ProfileMixin,
  TbModeMixin, AnnotationsMixin
- ``SolarRadioImageViewerApp``: AnnotationDialogsMixin, FittingMixin

Mixins have no ``__init__``; the state they use (e.g. ``self._ruler_mode``,
``self.canvas``) is still initialised in the host class.
"""

from .roi_stats import RegionMode, RoiStatsMixin
from .ruler import RulerMixin
from .profile import ProfileMixin
from .tb_mode import TbModeMixin
from .annotations import AnnotationsMixin, AnnotationDialogsMixin
from .fitting import FittingMixin

__all__ = [
    "RegionMode",
    "RoiStatsMixin",
    "RulerMixin",
    "ProfileMixin",
    "TbModeMixin",
    "AnnotationsMixin",
    "AnnotationDialogsMixin",
    "FittingMixin",
]

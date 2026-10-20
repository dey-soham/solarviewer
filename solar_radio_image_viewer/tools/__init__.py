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

from .annotations import AnnotationDialogsMixin, AnnotationsMixin
from .fitting import FittingMixin
from .profile import ProfileMixin
from .roi_stats import RegionMode, RoiStatsMixin
from .ruler import RulerMixin
from .tb_mode import TbModeMixin

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

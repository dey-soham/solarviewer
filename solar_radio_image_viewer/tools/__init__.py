"""
Interactive analysis tools for SolarViewer.

Each module provides a mixin class holding one tool's methods. The mixins are
combined into the main classes in ``viewer.py``:

- ``SolarRadioImageTab``: RoiStatsMixin, RulerMixin, ProfileMixin,
  TbModeMixin, AnnotationsMixin, HpcViewMixin, SolarDiskMixin,
  ImageStatsMixin, SolarEventsMixin
- ``SolarRadioImageViewerApp``: AnnotationCommandsMixin, FittingMixin

Mixins have no ``__init__``; the state they use (e.g. ``self._ruler_mode``,
``self.canvas``) is still initialised in the host class.
"""

from .annotations import AnnotationCommandsMixin, AnnotationsMixin
from .fitting import FittingMixin
from .hpc_view import HpcViewMixin
from .image_stats import ImageStatsMixin
from .profile import ProfileMixin
from .roi_stats import RegionMode, RoiStatsMixin
from .ruler import RulerMixin
from .solar_disk import SolarDiskMixin
from .solar_events import SolarEventsMixin
from .tb_mode import TbModeMixin

__all__ = [
    "RegionMode",
    "RoiStatsMixin",
    "RulerMixin",
    "ProfileMixin",
    "TbModeMixin",
    "AnnotationsMixin",
    "AnnotationCommandsMixin",
    "FittingMixin",
    "HpcViewMixin",
    "SolarDiskMixin",
    "ImageStatsMixin",
    "SolarEventsMixin",
]

"""Utility modules for solarviewer (HTTP rate limiting, sessions, updates).

Image-processing helpers that used to live in ``utils.py`` are now in the
``processing`` package; the old names are still importable from here for
backwards compatibility but emit a DeprecationWarning.
"""

# Import rate limiting utilities from local module
from .rate_limiter import (
    RateLimitedSession,
    CachedSession,
    get_global_session,
)

# Old utils.py name -> processing submodule that now provides it
_MOVED_TO_PROCESSING = {
    "IA": "dependencies",
    "CASA_AVAILABLE": "dependencies",
    "ASTROPY_AVAILABLE": "dependencies",
    "run_immath_subprocess": "immath",
    "get_available_stokes": "stokes",
    "estimate_rms_near_Sun": "rms",
    "remove_pixels_away_from_sun": "rms",
    "get_pixel_values_from_image": "image_data",
    "get_image_metadata": "metadata",
    "format_metadata_text": "metadata",
    "twoD_gaussian": "models",
    "twoD_elliptical_ring": "models",
    "generate_tb_map": "maps",
    "generate_flux_map": "maps",
}


def __getattr__(name):
    if name in _MOVED_TO_PROCESSING:
        import importlib
        import warnings

        module = f"solar_radio_image_viewer.processing.{_MOVED_TO_PROCESSING[name]}"
        warnings.warn(
            f"solar_radio_image_viewer.utils.{name} has moved to {module}",
            DeprecationWarning,
            stacklevel=2,
        )
        return getattr(importlib.import_module(module), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "RateLimitedSession",
    "CachedSession",
    "get_global_session",
]

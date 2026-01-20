"""Utility modules for solarviewer."""

# Import rate limiting utilities from local module
from .rate_limiter import (
    RateLimitedSession,
    CachedSession,
    get_global_session,
)

# Re-export all utilities from parent utils.py module
import sys
from pathlib import Path

parent_utils_path = Path(__file__).parent.parent / "utils.py"
if parent_utils_path.exists():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_parent_utils", str(parent_utils_path)
    )
    if spec and spec.loader:
        _parent_utils = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_parent_utils)

        # Re-export all public functions from parent utils
        for name in dir(_parent_utils):
            if not name.startswith("_"):
                globals()[name] = getattr(_parent_utils, name)

__all__ = [
    "RateLimitedSession",
    "CachedSession",
    "get_global_session",
    "get_pixel_values_from_image",  # From parent utils.py
]

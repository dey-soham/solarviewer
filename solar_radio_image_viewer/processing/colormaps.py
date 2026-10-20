"""
Solar colormaps (sdoaia171, soholasco2, goes-rsuvi94, ...) from sunpy.

Importing ``sunpy.visualization.colormaps`` registers them with matplotlib but
takes about a second (it pulls in sunpy's coordinates and pandas), so it is
done on first use, or in the background once the main window is shown,
instead of at startup.
"""

import threading

_lock = threading.Lock()
_loaded = False


def ensure_solar_colormaps():
    """Register sunpy's solar colormaps with matplotlib (only the first call
    does any work). Returns False if sunpy is not available."""
    global _loaded
    if _loaded:
        return True
    with _lock:
        if not _loaded:
            try:
                import sunpy.visualization.colormaps  # noqa: F401  (registers)
            except ImportError:
                return False
            _loaded = True
    return True

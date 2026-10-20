"""
Optional heavy dependencies (CASA, astropy) and their availability flags.

Import ``IA``, ``CASA_AVAILABLE``, ``u`` and ``ASTROPY_AVAILABLE`` from here
rather than importing casatools directly, so CASA logging is configured once.
"""

# Try to import CASA tools (casatasks now run via subprocess)
try:
    # Suppress CASA logging warnings before importing casatools
    import os as _os

    _os.environ["CASA_LOGLEVEL"] = "ERROR"
    _os.environ["CASARC"] = "/dev/null"

    from casatools import image as IA

    # Note: casatasks (immath) is now run via subprocess - see run_immath_subprocess()

    # Configure CASA logging to suppress warnings
    try:
        from casatools import logsink

        _casalog = logsink()
        _casalog.setlogfile("/dev/null")  # Redirect CASA logs to null
        _casalog.setglobal(True)
        # Filter out WARN and INFO level messages
        _casalog.filter("ERROR")
    except Exception:
        pass

    CASA_AVAILABLE = True
except ImportError:
    print(
        "WARNING: CASA tools not found. This application requires CASA to be installed."
    )
    CASA_AVAILABLE = False
    IA = None

try:
    import astropy.units as u

    ASTROPY_AVAILABLE = True
except ImportError:
    print("WARNING: astropy not found. Some functionality will be limited.")
    ASTROPY_AVAILABLE = False
    u = None

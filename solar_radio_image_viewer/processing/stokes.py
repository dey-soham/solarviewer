"""
Stokes parameter discovery for FITS and CASA images.
"""

from .dependencies import ASTROPY_AVAILABLE, CASA_AVAILABLE, IA
from .fits_io import readable_image_path


def get_available_stokes(imagename):
    """
    Detect available Stokes parameters from a CASA image or FITS file.

    Parameters:
        imagename : str
            Path to the CASA image directory or FITS file.

    Returns:
        list: Available Stokes parameters, e.g., ["I"] or ["I", "Q", "U", "V"]
    """
    all_stokes = ["I", "Q", "U", "V"]

    # Check if it's a FITS file
    is_fits = imagename.lower().endswith(".fits") or imagename.lower().endswith(".fts")

    if is_fits and ASTROPY_AVAILABLE:
        try:
            from astropy.io import fits

            with fits.open(readable_image_path(imagename), memmap=True) as hdul:
                header = hdul[0].header
                ndim = header.get("NAXIS", 0)

                # Find Stokes axis
                for i in range(1, ndim + 1):
                    ctype = header.get(f"CTYPE{i}", "").lower()
                    if ctype == "stokes":
                        num_stokes = header.get(f"NAXIS{i}", 1)
                        return all_stokes[:num_stokes]

                # No Stokes axis found - assume single Stokes I
                return ["I"]
        except Exception as e:
            print(f"[WARNING] Could not detect Stokes from FITS: {e}")
            return ["I"]

    elif CASA_AVAILABLE:
        try:
            ia_tool = IA()
            ia_tool.open(readable_image_path(imagename))
            summary = ia_tool.summary()
            ia_tool.close()

            dimension_names = summary.get("axisnames")
            dimension_shapes = summary.get("shape")

            if dimension_names is None:
                return ["I"]

            # Convert to list for case-insensitive search
            dimension_names_lower = [str(name).lower() for name in dimension_names]

            # Find Stokes axis
            if "stokes" in dimension_names_lower:
                stokes_idx = dimension_names_lower.index("stokes")
                num_stokes = dimension_shapes[stokes_idx]
                return all_stokes[:num_stokes]

            # No Stokes axis found - assume single Stokes I
            return ["I"]
        except Exception as e:
            print(f"[WARNING] Could not detect Stokes from CASA image: {e}")
            return ["I"]

    # Fallback
    return ["I"]

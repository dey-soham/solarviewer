"""
Videos in helioprojective or RA/Dec coordinates, whatever the images are in.

Frames are converted the way the main window converts an image (its HPC /
RA-Dec button): RA/Dec images to helioprojective with all Stokes kept, and
helioprojective ones back to RA/Dec. Converted files are kept in a cache
folder, shared by the preview, the worker processes and later videos, so
each image is converted once.
"""

import hashlib
import logging
import os
import shutil
import tempfile
from contextlib import contextmanager

from ..processing.fits_io import fits_image_path
from .utils import coordinate_type

logger = logging.getLogger(__name__)

# Video coordinate choices: the images' own, helioprojective, RA/Dec
COORDINATES = ("native", "hpc", "radec")
COORDINATE_NAMES = {
    "native": "As in the images",
    "hpc": "Helioprojective (Solar X/Y)",
    "radec": "RA/Dec",
}


class ConversionError(Exception):
    """A frame that can't be converted to the chosen coordinates."""


def frame_file(path, options):
    """The FITS file to read for frame image ``path``: ``path`` itself (or
    its FITS export, for a CASA image), or a copy converted to
    ``options["coordinates"]``. Raises ConversionError."""
    target = options.get("coordinates") or "native"
    if target == "native":
        return fits_image_path(path)
    cache_dir = options.get("coordinate_cache")
    if not cache_dir:
        raise ConversionError("no folder for converted images")
    return converted_file(path, target, cache_dir)


def converted_file(path, target, cache_dir):
    """``path`` in coordinates ``target`` ("hpc" or "radec"): ``path``
    itself (as FITS) if it already is, else the converted copy in
    ``cache_dir`` (made now if needed)."""
    from ..processing.image_meta import fits_header

    try:
        source = fits_image_path(path)
        own = coordinate_type(fits_header(source))
    except Exception as e:
        raise ConversionError(f"cannot read {os.path.basename(os.path.normpath(path))} ({e})")
    if own == target:
        return source
    if own not in ("hpc", "radec"):
        raise ConversionError(f"{os.path.basename(path)} has no sky coordinates to convert")

    stat = os.stat(source)
    key = f"{os.path.abspath(path)}|{stat.st_mtime_ns}|{stat.st_size}|{target}"
    name = hashlib.sha1(key.encode()).hexdigest()[:20] + ".fits"
    out = os.path.join(cache_dir, name)
    if os.path.exists(out):
        return out

    os.makedirs(cache_dir, exist_ok=True)
    # Written under a name of its own, then renamed: other processes may be
    # converting the same image
    partial = os.path.join(cache_dir, f".{os.getpid()}-{name}")
    try:
        if target == "hpc":
            from ..coordinates.helioprojective import convert_and_save_hpc_all_stokes

            done = convert_and_save_hpc_all_stokes(source, partial, overwrite=True)
        else:
            from ..coordinates.helioprojective import convert_hpc_to_radec

            done = convert_hpc_to_radec(source, partial, overwrite=True)
    except Exception as e:
        done = False
        logger.warning(f"Converting {path} failed: {e}")
    if not done or not os.path.exists(partial):
        if os.path.exists(partial):
            os.remove(partial)
        raise ConversionError(
            f"{os.path.basename(path)} could not be converted to "
            f"{'helioprojective' if target == 'hpc' else 'RA/Dec'} coordinates"
            + (" (RA/Dec to helioprojective needs the telescope's position)" if target == "hpc" else "")
        )
    os.replace(partial, out)
    return out


@contextmanager
def conversion_cache(options):
    """Make sure ``options`` has a folder for converted frames while in the
    block; a folder made here is removed afterwards."""
    cache_dir = options.get("coordinate_cache")
    if (options.get("coordinates") or "native") == "native" or (
        cache_dir and os.path.isdir(cache_dir)
    ):
        yield options
        return
    cache_dir = tempfile.mkdtemp(prefix=f"solarviewer-video-p{os.getpid()}-")
    try:
        yield dict(options, coordinate_cache=cache_dir)
    finally:
        shutil.rmtree(cache_dir, ignore_errors=True)

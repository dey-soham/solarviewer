"""
Make FITS files whose image is not in the primary HDU readable everywhere.

Many EUV products (e.g. GOES SUVI L2, SDO/AIA and HMI from JSOC) store the
image tile-compressed in extension 1 with an empty primary HDU. CASA cannot
read those, and most of SolarViewer reads ``hdul[0]``. ``readable_image_path``
returns a cached, uncompressed single-HDU copy for such files and the original
path for everything else, so readers can call it unconditionally.
"""

import hashlib
import os
import re
import tempfile

FITS_SUFFIXES = (".fits", ".fts", ".fit", ".fits.fz", ".fz", ".fits.gz", ".fts.gz")

CACHE_DIR = os.path.join(tempfile.gettempdir(), "solarviewer_fits_cache")

# (abspath, mtime_ns, size) -> path returned for it, so an unchanged file is
# not reopened on every call
_resolved = {}


def _image_hdu_index(hdul):
    """Index of the HDU holding the image, or None if it is the primary HDU."""
    if hdul[0].header.get("NAXIS", 0) >= 2:
        return None
    for i, hdu in enumerate(hdul[1:], start=1):
        if hdu.is_image and hdu.header.get("NAXIS", 0) >= 2:
            return i
    return None


def readable_image_path(path):
    """Return a path whose image data is in the primary HDU of a plain FITS file.

    For CASA images, non-FITS files and FITS files that already have their
    image in the primary HDU this is ``path`` itself. Otherwise the image HDU
    is written (decompressed) to a cache file with the same base name, which
    is reused while the source file is unchanged.
    """
    if not path or not os.path.isfile(path) or not path.lower().endswith(FITS_SUFFIXES):
        return path

    try:
        stat = os.stat(path)
    except OSError:
        return path
    signature = (os.path.abspath(path), stat.st_mtime_ns, stat.st_size)
    resolved = _resolved.get(signature)
    if resolved is not None:
        if resolved == signature[0]:
            return path
        if os.path.exists(resolved):
            return resolved

    resolved = _resolve(path, stat)
    if resolved is not None:
        _resolved[signature] = resolved
        return path if resolved == signature[0] else resolved
    return path


def _resolve(path, stat):
    """Absolute path of ``path`` itself, or of its cached single-HDU copy.

    None if the file could not be read.
    """
    from astropy.io import fits

    try:
        with fits.open(path, memmap=True) as hdul:
            index = _image_hdu_index(hdul)
            if index is None:
                return os.path.abspath(path)

            key = f"{os.path.abspath(path)}|{stat.st_mtime_ns}|{stat.st_size}"
            name = os.path.basename(path)
            for suffix in (".fz", ".gz"):
                if name.lower().endswith(suffix):
                    name = name[: -len(suffix)]
            if not name.lower().endswith((".fits", ".fts", ".fit")):
                name += ".fits"
            cache_dir = os.path.join(
                CACHE_DIR, hashlib.sha1(key.encode()).hexdigest()[:16]
            )
            cached = os.path.join(cache_dir, name)
            if os.path.exists(cached):
                return cached

            hdu = hdul[index]
            header = hdu.header.copy()
            for keyword in ("XTENSION", "PCOUNT", "GCOUNT", "EXTNAME"):
                header.remove(keyword, ignore_missing=True)
            # Keep primary-header metadata that the image header lacks
            for card in hdul[0].header.cards:
                if card.keyword not in header and card.keyword not in (
                    "SIMPLE",
                    "BITPIX",
                    "NAXIS",
                    "EXTEND",
                    "CHECKSUM",
                    "DATASUM",
                    "",
                ):
                    header.append(card)

            os.makedirs(cache_dir, exist_ok=True)
            # Per process, so a background read of the same file cannot clash
            tmp = f"{cached}.{os.getpid()}.part"
            fits.PrimaryHDU(data=hdu.data, header=header).writeto(tmp, overwrite=True)
            os.replace(tmp, cached)
            return cached
    except Exception as e:
        print(f"[WARNING] Could not prepare {path} for reading: {e}")
        return None


def is_casa_image(path):
    """Whether ``path`` is a CASA image (a directory holding an image table)."""
    return bool(path) and os.path.isfile(os.path.join(path, "table.info"))


# CASA image signature -> its FITS export, as for _resolved
_exported = {}


def fits_image_path(path):
    """A plain FITS file with ``path``'s image in the primary HDU: for a CASA
    image a FITS export of it (all axes, Stokes not moved last; cached
    while the image is unchanged), else ``readable_image_path(path)``.

    For code that reads images with astropy only (such as videos); the main
    window reads CASA images with casatools instead.
    """
    if not is_casa_image(path):
        return readable_image_path(path)
    from .image_meta import file_signature

    signature = file_signature(path)
    exported = _exported.get(signature)
    if exported and os.path.exists(exported):
        return exported

    key = repr(signature)
    name = os.path.basename(os.path.normpath(path)) + ".fits"
    cache_dir = os.path.join(CACHE_DIR, hashlib.sha1(key.encode()).hexdigest()[:16])
    cached = os.path.join(cache_dir, name)
    if not os.path.exists(cached):
        os.makedirs(cache_dir, exist_ok=True)
        # Per process, so a background export of the same image cannot clash
        tmp = f"{cached}.{os.getpid()}.part.fits"
        _export_casa_image(path, tmp)
        os.replace(tmp, cached)
    _exported[signature] = cached
    return cached


def casa_observation_date(path):
    """Observation date (ISO string) of CASA image ``path``, or None, read
    from its coordinates without exporting the image."""
    from casatools import image

    tool = image()
    try:
        tool.open(path)
        obsdate = tool.coordsys().torecord().get("obsdate") or {}
    finally:
        tool.close()
    mjd = (obsdate.get("m0") or {}).get("value")
    if mjd is None:
        return None
    from astropy.time import Time

    return Time(mjd, format="mjd").isot

# Keywords CASA writes itself from the image's coordinates and beam; when
# also in an image's extra keywords (miscinfo, e.g. copied from a FITS
# header) CASA refuses to export it
_RESERVED_KEYWORD = re.compile(
    r"^(SIMPLE|BITPIX|NAXIS\d*|EXTEND|BSCALE|BZERO|BLANK|CTYPE\d+|CRVAL\d+|CRPIX\d+"
    r"|CDELT\d+|CROTA\d*|CUNIT\d+|PC\d+_\d+|CD\d+_\d+|PV\d+_\d+|PS\d+_\d+"
    r"|LONPOLE|LATPOLE|EQUINOX|EPOCH|RADESYS|BMAJ|BMIN|BPA|BUNIT|BTYPE|DATE-OBS"
    r"|OBSGEO-[XYZ]|TELESCOP|OBSERVER|RESTFRQ|RESTFREQ|SPECSYS|VELREF)$",
    re.IGNORECASE,
)


def _export_casa_image(path, fits_path):
    """Write CASA image ``path`` to FITS file ``fits_path``, leaving out
    extra keywords that clash with those written from the image itself
    (``path`` is not changed)."""
    from casatools import image

    tool = image()
    tool.open(path)
    try:
        misc = tool.miscinfo()
        if not any(_RESERVED_KEYWORD.match(key) for key in misc):
            tool.tofits(fits_path, overwrite=True, stokeslast=False)
            return
        copy = tool.subimage()  # in memory
        try:
            copy.setmiscinfo({k: v for k, v in misc.items() if not _RESERVED_KEYWORD.match(k)})
            copy.tofits(fits_path, overwrite=True, stokeslast=False)
        finally:
            copy.close()
    finally:
        tool.close()

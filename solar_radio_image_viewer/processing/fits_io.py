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
import tempfile

FITS_SUFFIXES = (".fits", ".fts", ".fit", ".fits.fz", ".fz", ".fits.gz", ".fts.gz")

CACHE_DIR = os.path.join(tempfile.gettempdir(), "solarviewer_fits_cache")


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

    from astropy.io import fits

    try:
        with fits.open(path, memmap=True) as hdul:
            index = _image_hdu_index(hdul)
            if index is None:
                return path

            stat = os.stat(path)
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
            tmp = cached + ".part"
            fits.PrimaryHDU(data=hdu.data, header=header).writeto(tmp, overwrite=True)
            os.replace(tmp, cached)
            return cached
    except Exception as e:
        print(f"[WARNING] Could not prepare {path} for reading: {e}")
        return path

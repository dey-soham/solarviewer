"""
Header and coordinate-system reads shared by everything that loads an image.

Opening a file just to look at its header, Stokes axis or coordinate names
used to happen several times per load (once per reader). The readers here
cache what one open returns, keyed by the file's signature (path, mtime and
size), so a file is read again only after it changes on disk. Callers always
get their own copy, so changing what they get back cannot leak into the cache.
"""

import copy
import os
import threading
from collections import OrderedDict

from .fits_io import readable_image_path

_MAX_ENTRIES = 32
_lock = threading.Lock()
_fits_headers = OrderedDict()
_casa_infos = OrderedDict()


def file_signature(path):
    """Identity of ``path``'s current contents, or None if it cannot be read.

    For a CASA image (a directory) this covers every file inside it, so
    rewriting the pixels or the header changes the signature.
    """
    try:
        if os.path.isdir(path):
            entries = []
            for root, _dirs, files in os.walk(path):
                for name in files:
                    full = os.path.join(root, name)
                    stat = os.stat(full)
                    entries.append(
                        (os.path.relpath(full, path), stat.st_mtime_ns, stat.st_size)
                    )
            return (os.path.abspath(path), tuple(sorted(entries)))
        stat = os.stat(path)
        return (os.path.abspath(path), stat.st_mtime_ns, stat.st_size)
    except (OSError, TypeError):
        return None


def _cache_get(cache, signature):
    if signature is None:
        return None
    with _lock:
        value = cache.get(signature)
        if value is not None:
            cache.move_to_end(signature)
        return value


def _cache_put(cache, signature, value):
    if signature is None:
        return
    with _lock:
        cache[signature] = value
        cache.move_to_end(signature)
        while len(cache) > _MAX_ENTRIES:
            cache.popitem(last=False)


def coordsys_from_record(record):
    """A new ``casatools.coordsys`` built from ``coordsys.torecord()`` output."""
    from casatools import coordsys

    csys = coordsys()
    csys.fromrecord(copy.deepcopy(record))
    return csys


class CasaInfo:
    """What ``ia.open`` of an image reports: summary, coordsys, beam, units."""

    def __init__(self, summary, csys_record, beam, brightness_unit):
        self.summary = summary
        self.csys_record = csys_record
        self.beam = beam
        self.brightness_unit = brightness_unit

    @classmethod
    def from_image_tool(cls, ia_tool, summary=None):
        """Collect the info from an open ``casatools.image``."""
        return cls(
            summary if summary is not None else ia_tool.summary(),
            ia_tool.coordsys().torecord(),
            ia_tool.restoringbeam(),
            ia_tool.brightnessunit(),
        )

    def coordsys(self):
        """A new ``casatools.coordsys`` equal to the image's."""
        return coordsys_from_record(self.csys_record)

    def copy(self):
        return copy.deepcopy(self)


def remember_casa_info(signature, info):
    """Store info read elsewhere (e.g. while reading pixels) for ``signature``."""
    _cache_put(_casa_infos, signature, info)


def remember_fits_header(signature, header):
    _cache_put(_fits_headers, signature, header)


def casa_info(imagename):
    """:class:`CasaInfo` for ``imagename``, opening the image only on a cache miss.

    Raises whatever opening the image raises.
    """
    signature = file_signature(imagename)
    info = _cache_get(_casa_infos, signature)
    if info is None:
        from .dependencies import IA

        ia_tool = IA()
        ia_tool.open(readable_image_path(imagename))
        try:
            info = CasaInfo.from_image_tool(ia_tool)
        finally:
            ia_tool.close()
        _cache_put(_casa_infos, signature, info)
    return info.copy()


def fits_header(imagename):
    """Primary-HDU header (an astropy ``Header``) of ``imagename``'s readable copy.

    Same as ``fits.getheader(readable_image_path(imagename))``, but the file
    is opened only on a cache miss. Raises whatever opening it raises.
    """
    signature = file_signature(imagename)
    header = _cache_get(_fits_headers, signature)
    if header is None:
        from astropy.io import fits

        with fits.open(readable_image_path(imagename), memmap=True) as hdul:
            header = hdul[0].header.copy()
        _cache_put(_fits_headers, signature, header)
    return header.copy()

"""
Small least-recently-used cache for reprojected contour arrays.
"""

from collections import OrderedDict

import numpy as np


class ReprojectionCache:
    """Dict-like LRU cache bounded by entry count and total array size.

    Values are tuples whose numpy arrays count towards ``max_bytes``. The
    least recently used entries are dropped first; a ``get`` hit counts as a
    use.
    """

    def __init__(self, max_entries=16, max_bytes=512 * 1024**2):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._items = OrderedDict()
        self._sizes = {}

    @staticmethod
    def _nbytes(value):
        items = value if isinstance(value, tuple) else (value,)
        return sum(v.nbytes for v in items if isinstance(v, np.ndarray))

    @property
    def nbytes(self):
        return sum(self._sizes.values())

    def get(self, key, default=None):
        if key not in self._items:
            return default
        self._items.move_to_end(key)
        return self._items[key]

    def __setitem__(self, key, value):
        if key in self._items:
            del self._items[key]
        self._items[key] = value
        self._sizes[key] = self._nbytes(value)
        # Always keep the newest entry, even if it alone exceeds max_bytes
        while len(self._items) > 1 and (
            len(self._items) > self.max_entries or self.nbytes > self.max_bytes
        ):
            old, _ = self._items.popitem(last=False)
            del self._sizes[old]

    def __contains__(self, key):
        return key in self._items

    def __len__(self):
        return len(self._items)

    def clear(self):
        self._items.clear()
        self._sizes.clear()

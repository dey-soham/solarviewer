"""What the viewer keeps of a radial filter (processing/radial_filter.py)."""

import weakref

from ..processing.radial_filter import MODES, apply  # noqa: F401


class RadialFilterCache:
    """The filtered version of one image, kept while the image, the mode
    and the centre are the same (the image is held by a weak reference,
    so a freed array's id cannot be mistaken for a new one's)."""

    def __init__(self):
        self._ref = None
        self._key = None
        self._result = None

    @staticmethod
    def _make_key(data, mode, centre):
        return (mode, tuple(round(float(c), 3) for c in centre), data.shape)

    def holds(self, data, mode, centre):
        """Whether this filtering of ``data`` is at hand."""
        return (
            self._ref is not None
            and self._ref() is data
            and self._key == self._make_key(data, mode, centre)
        )

    def put(self, data, mode, centre, result):
        """Keep ``result``, the filtering of ``data``."""
        self._result = result
        self._ref = weakref.ref(data)
        self._key = self._make_key(data, mode, centre)

    def get(self, data, mode, centre):
        if not self.holds(data, mode, centre):
            self.put(data, mode, centre, apply(data, mode, centre))
        return self._result

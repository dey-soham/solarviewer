"""
Video creation from sequences of FITS/CASA images.

- ``creator``: frame rendering and encoding (``create_video``)
- ``dialog``: the Create Video dialog (``VideoCreationDialog``)
- ``utils``: coordinate detection, WCS axes, min/max timeline, contour videos

Submodules are imported explicitly; this ``__init__`` stays empty so that
multiprocessing workers importing ``video.creator`` don't pull in Qt.
"""

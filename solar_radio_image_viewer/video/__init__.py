"""
Video creation from sequences of FITS/CASA images.

- ``creator``: frame rendering and encoding (``create_video``)
- ``dialog``: the Create Video dialog (``VideoCreationDialog``), made of the
  tabs, preview and worker in ``ui``
- ``utils``: coordinate detection, WCS axes, contour layers of frames
- ``coordinates``, ``overlays``, ``annotations``, ``lightcurve``: frames in
  other coordinates; solar disk, beam and grid; main window annotations; the
  line plot

Submodules are imported explicitly; this ``__init__`` stays empty so that
multiprocessing workers importing ``video.creator`` don't pull in Qt.
"""

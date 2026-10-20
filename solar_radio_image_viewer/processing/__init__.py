"""
Image processing and computation for SolarViewer that does not depend on Qt.

Modules here must not import PyQt5 or matplotlib's Qt backend, so they can be
run in subprocesses, scripts and tests without a GUI:

- ``dependencies``: optional CASA / astropy imports and availability flags
- ``image_data``: read a Stokes plane's pixels from FITS/CASA images
- ``metadata``: extract and format image metadata
- ``stokes``: list the Stokes parameters an image provides
- ``rms``: RMS estimation and masking far from the Sun
- ``maps``: brightness temperature <-> flux density maps
- ``models``: analytic 2D models used for fitting
- ``immath`` / ``imfit``: CASA tasks run in a subprocess

Submodules are imported explicitly (e.g. ``from .processing.rms import ...``);
this ``__init__`` imports nothing, so ``python -m`` runners stay lightweight.
"""

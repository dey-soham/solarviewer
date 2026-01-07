# SolarViewer Release Notes

## Version 1.1.0

### User Interface & Experience
*   **UI Modernization**: Implemented a comprehensive visual overhaul with updated color schemes and styling.
*   **Visual Feedback**: Enhanced the Stokes parameter selector to provide clear visual indicators for disabled options.

### Data Handling & Performance
*   **FITS Compatibility**: Added robust support for single-Stokes image processing.
*   **Downsampling Mode**: Introduced a high-performance downsampling mode for rapid previewing of large-scale FITS and CASA datasets.

### Bug Fixes & Stability
*   **Metadata Parsing**: Improved date parsing reliability for FITS headers.
*   **General Stability**: Implemented various stability improvements and performance optimizations.
*   **General**: Other quality of life improvements.

## Version 1.0.3
*   **Cursor Position**: Fixed cursor position mismatch between displayed coordinates and matplotlib toolbar
*   **World Coordinates**: World coordinates now match exactly with matplotlib's axis display
*   **Dynamic Spectra**: Applied same cursor coordinate fix to dynamic spectra viewer
*   **Helioviewer Panel**: Improved instrument selection with hierarchical tree view grouped by observatory
*   **Helioviewer Panel**: Added wavelength descriptions and temperature info for AIA channels
*   **Thread Safety**: Fixed crash when closing windows while downloads are in progress
*   **NOAA Events**: Added proper thread cleanup to prevent "QThread destroyed while running" errors
*   **Downloaders**: Added closeEvent cleanup to radio and solar data downloader GUIs
*   **General**: Other quality of life improvements

## Version 1.0.2
*   **Video Creation**: Fixed contours not plotting in video creation multi processing
*   **Gaussian Fitting**: Added support for deconvolved source sizes
*   **General**: Other quality of life improvements

## Version 1.0.1

### Usability Enhancements
*   **Non-Modal Interface**: Converted key dialogs (Profiles, Histograms, Annotations, Settings) to non-modal windows, enabling simultaneous interaction with the main viewer and analysis tools.
*   **Settings Management**: Standardized application configuration storage under "SolarViewer".
*   **Shortcut Resolution**: Resolved conflict for `Ctrl+T`; now dedicated exclusively to Text Annotations.

### Analysis & Measurements
*   **WCS Integration**: Analysis tools (Fitting, Ruler, Profiles) now default to World Coordinate System units (arcsec/degrees) when WCS data is available, with pixel fallback.
*   **Flux Profiling**: Implemented automatic Full Width at Half Maximum (FWHM) calculation with smart validation and visual indicators for suitable peaks.
*   **Enhanced Reporting**: Standardized terminal output for Gaussian/Ring fits and Ruler measurements to provide formatted, professional data presentation including RA/Dec coordinates.

### Visualization Tools
*   **Advanced Annotations**: Added comprehensive styling options for Text (color, font, background) and Arrow (color, width, head size) annotations.

## Version 1.0.0

v1.0.0 is finally here! This release represents a lot of work on making the viewer faster, more stable, and actually capable of creating good quality videos.

### Video Creator
I've added a proper suite for making videos from your solar data.
- **Detached Preview**: The preview window now pops out so you can resize it and actually see what you're doing.
- **Timeline & Colorbar**: Fixed the issues where the timeline wouldn't scale nicely or the colorbar looked off.
- **Overlays**: timestamps and filenames now render correctly on the frames.

### Better FITS Support
- **Contours**: You can now overlay contours on FITS images.
- **Phase Center**: Added the ability to shift the solar center to the phase center if your alignment is off.
- **HPC Overlays**: Helioprojective transformation and overlays are fully working now.

### Data Downloads
You don't need to leave the app to get context data anymore. I've added a browser to download SDO (AIA/HMI), IRIS, and SOHO data directly from the menu.

### Speed
- Plotting feels much snappier.
- Integrated "Napari Viewer" for when you need to load and view really large image datasets without the app crawling to a halt.

### Quality of Life
- **Auto RMS**: just select a region and it calculates the RMS for you.
- **Image Stats**: added a side panel that shows real-time stats for the current image.
- **Dark Mode**: added a dark palette because my eyes were hurting during late night coding/viewing sessions.
- **Tabs**: Tab names auto-update based on what you load, and you can rename them if you want.
- **Cleaner Logs**: Moved all those informational messages to the status bar so they don't flood your terminal.

### Key Fixes
- Fixed the crash when closing the Helioviewer browser ("QThread: Destroyed while thread is still running").
- Fixed a bug where the solar disk would just disappear if you reset the zoom.
- Fixed rotation issues in HPC transformations.
- Suppressed a bunch of noisy CASA warnings.

### Installation

To install:
```bash
pip install .
```

If you need the full feature set (LOFAR tools, etc):
```bash
pip install .[full]
```

### Contributors
- Soham Dey

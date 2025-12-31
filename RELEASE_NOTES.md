# SolarViewer v1.0.0

v1.0.0 is finally here! This release represents a lot of work on making the viewer faster, more stable, and actually capable of creating good quality videos.

## What's New

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

## Quality of Life

- **Auto RMS**: just select a region and it calculates the RMS for you.
- **Image Stats**: added a side panel that shows real-time stats for the current image.
- **Dark Mode**: added a dark palette because my eyes were hurting during late night coding/viewing sessions.
- **Tabs**: Tab names auto-update based on what you load, and you can rename them if you want.
- **Cleaner Logs**: Moved all those informational messages to the status bar so they don't flood your terminal.

## Key Fixes

- Fixed the crash when closing the Helioviewer browser ("QThread: Destroyed while thread is still running").
- Fixed a bug where the solar disk would just disappear if you reset the zoom.
- Fixed rotation issues in HPC transformations.
- Suppressed a bunch of noisy CASA warnings.

## Installation

To install:
```bash
pip install .
```

If you need the full feature set (LOFAR tools, etc):
```bash
pip install .[full]
```

## Contributors
- Soham Dey

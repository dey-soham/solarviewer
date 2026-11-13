# SolarViewer Release Notes

## Version 1.3.0

### Navigating the Image
*   **Smoother Pan and Zoom**: Dragging moves the image directly and zooms glide. While the view moves, a picture of it is shown, so it stays smooth on slow machines; newly exposed edges show a low-resolution overview.
*   **Box Zoom**: Right-drag a box to zoom to it.
*   **Keys After Clicking**: R, 1, +, - and the arrow keys work after clicking the image; Esc leaves a text field.
*   **Measuring**: Mouse pan and zoom pause while the Ruler or Profile tool is measuring.
*   **Keyboard Shortcuts** (F1): The list is read from the menus, so it shows exactly the shortcuts that work, plus the image's keys and mouse gestures.
*   **Conventional Shortcuts**: Ctrl+S now saves the figure (Ctrl+E still works). Export Sub-Image moved to Ctrl+Shift+S, Export as FITS to Ctrl+Shift+F and Add Arrow to Ctrl+Shift+A.
*   **Zoom Kept**: The part of the sky in view stays in view when stepping to the next image ([ and ]), changing the unit, and switching between RA/Dec and Solar XY.
*   **Regions and Annotations on the Sky**: Regions and annotations keep their place on the sky across the RA/Dec and Solar XY switch.

### Opening Images
*   **One Open Item**: File → Open Image (Ctrl+O) opens FITS files and CASA images alike, as do the contour, video and phase-shift dialogs.
*   **Open Recent**: File → Open Recent lists the last ten images; the Open dialog starts in the last image's folder.
*   **Unreadable Files**: A file that cannot be read now says so, instead of leaving the previous image on screen under the new name.
*   **Busy Cursor**: The wait cursor shows while an image loads or draws.

### Image Loading & Display
*   **Compressed FITS Support**: Files that store the image in a compressed extension (e.g. GOES SUVI L2, SDO/AIA and HMI from JSOC) now load with proper solar coordinates, titles and units instead of raw pixels.
*   **Colour Range Fixes**: Constant or fully masked maps (e.g. Lfrac/Vfrac/PANG below threshold) no longer keep the previous product's colour range, and **Reload Image** after a Stokes change now resets the range.
*   **LASCO Presets**: LASCO presets now use the LASCO colormaps; the menu offers separate C2 and C3 presets.
*   **Faster Loading**: Only the planes a Stokes product needs are read, not the whole cube.
*   **Reset**: Reset, by the intensity range, puts back the range, stretch, gamma and colormap the image opened with.
*   **Keep for Next Images**: Holds the intensity range while stepping through a time series; an image in another unit or Stokes product is still scaled afresh.
*   **Range Histogram**: The histogram widens as a marker is pushed past its edge and comes back once the marker is let go, so a marker dragged far out by accident is easy to place again. Double-click it for the image's whole range.

### Stokes Products
*   **Planes by Label**: Stokes planes are found by their labels. On an image with only I and V, V is shown (it used to appear as Q), and a missing plane is named in a clear message.
*   **Derived Products**: Lfrac, Vfrac, Q/I, U/I, U/V and PANG work on non-square images and on images without a frequency axis, and keep pixels near the Sun rather than near the image centre.
*   **Contours**: Vfrac and U/V contours used to show Stokes I, and V/I contours were refused on images with only I and V.

### Analysis
*   **Gaussian Fit**: The fit box was sent with x and y swapped; the fit and the sub-image and region exports also use full-resolution pixels in Fast mode.
*   **Ring Fit**: The ring fit now finds rings away from the box centre.
*   **Solar Disk**: Images without an observation date no longer place the disk at the Sun's position in 1858; when the Sun's position is unknown the status bar says the disk is at the image centre.
*   **Profiles**: The profile plot says why it shows no FWHM.

### Create Dynamic Spectra
*   **Runs on Servers**: Apps → Create Dynamic Spectra (also `createds`) can run on an SSH server (Run on = Server) inside the server's container or Python, directly or as a Slurm job for large sets. Only the machine that runs the engine needs python-casacore; Server mode needs paramiko here.
*   **Inputs**: Accepts `.ms` and `.MS` folders, one Measurement Set per subband or a single multi-channel one.
*   **Safe Outputs**: An existing FITS or PNG is never replaced unless "Replace existing output files (FITS and PNG) without asking" is ticked, and the dialog asks first. If some inputs fail, the rest is still written and the dialog reports how many failed.

### Radio Spectra
*   **More Sources to Download**: The Radio Data Downloader now also gets OVRO-LWA spectra (only the time range asked for is read from its daily file) and the radio receivers of Wind/WAVES, STEREO/WAVES, Parker Solar Probe/FIELDS and Solar Orbiter/RPW from NASA's SPDF. They are written as FITS in the files' own units, with each mission's rules of use in the header.
*   **Logarithmic Frequency Axis**: In the Dynamic Spectrum Viewer, View → Frequency axis offers Automatic, Linear and Logarithmic. Automatic is logarithmic where the channels span more than a decade and crowd toward the low end (the spacecraft receivers), so the lowest channels are no longer squeezed out of the picture. The choice is remembered.
*   **kHz**: Spectra below 1 MHz read in kHz on the axis, in the hover readout and in drift rates, and can now be zoomed.
*   **Gaps**: Channels that widen steadily are drawn joined; the upper ones used to be stripes with blank gaps between them. A band or a time with no data is still left blank.
*   **Open Spectrum from Solar Activity**: A spectrum downloaded through Solar Activity's "Get e-CALLISTO FITS…" opens in the Dynamic Spectrum Viewer when done; it used to fail with a line in the log.

### Helioviewer Browser
*   **Start and End**: The browser takes a Start and an End time (UTC). The span buttons set the range about its middle, Custom keeps it as typed, and the arrows (Alt+Left, Alt+Right) move it by its own length. Frames outside the range are left out.
*   **Nearest Image**: A range without images says how far off the nearest one is, and offers to go there.
*   **Last Range**: With no image time to start from, the browser reopens on the last range used.

### Updates
*   **Every Kind of Install**: Help → Check for updates updates SolarViewer the way it was installed: a release with pip or uv (also in a conda environment), an install from a git URL from the same URL, and a git checkout with `git pull --ff-only`. The command is shown before it runs.
*   **Honest Results**: The dialog says what was done. An installer that ends well without changing the installed version is reported as not having updated.
*   **What Is New**: The release notes from your version to the new one (for a checkout, the new commits) are shown before updating.
*   **Startup Check**: "Check for updates when SolarViewer starts" (also in Preferences) turns the startup check off, and "Skip this version" stops the announcement of that version.

### Installing on a Mac
*   **Apple Silicon**: `pip install solarviewer` no longer installs `python-casacore` on macOS, where it had to be compiled and usually failed; everything installed is ready-built (use Python 3.12). Only the Calibration Table Visualizer and Create Dynamic Spectra with Run on = This computer need it: they say so when it is missing, and INSTALLATION.md shows how to take it from conda-forge.

### Batch Dialogs and Servers
*   **Responsive Batches**: Format, unit and coordinate conversion and the phase shift run in worker processes: the window stays responsive, and Abort stops at once.
*   **Output Names**: Unit conversion no longer names its output `conv_a.fits.fits`, and full-Stokes coordinate conversion no longer writes all four planes to one file when the name has no extension.
*   **SSH Host Keys**: The server's key is checked against `~/.ssh/known_hosts` and the servers trusted in SolarViewer. A new server's fingerprint is shown for you to confirm before any password is sent, and a changed key is refused.
*   **Reconnecting**: A lost server connection is tried again after growing waits (10 seconds to 5 minutes) rather than every 5 seconds, and not at all once the server refuses the login: repeated refused logins can get an address banned on a shared server.

### Knowing What Is Going On
*   **Busy Pill**: Anything that takes more than a moment says what it is doing in a small pill low in the window, with a count where one is known. It keeps moving even while the window is held up (X11).
*   **Fewer Freezes**: Radial filters, exports, reprojection, fitting, table and log loading, server reads and the Helioviewer browser now work in the background, the window drawing, with a Cancel that works.
*   **Faster Start**: The window appears before the first image loads, and separate tools open a window at once while they load.

### Stability
*   **No More Crash-on-Error**: Unexpected errors no longer close the application. A crash report is still saved and a warning is shown, and the viewer keeps running.
*   **CASA Logs**: CASA no longer writes `casa-*.log` files into the working folder, and closing SolarViewer no longer deletes CASA logs of your own.
*   **File Names**: Exports and conversions work with spaces and quotes in file names (no shell commands are built from them).
*   **Closed Tabs**: Closing a tab frees its image and removes its temporary files.
*   **Log File**: Everything the log window shows is also kept in `~/.solarviewer/logs/solarviewer.log`, and crash reports include its last lines.
*   **Scroll-Zoom Crash**: Fixed a crash when switching files or redrawing during a scroll-zoom/pan animation.
*   **Remote Downloads**: Stalled SSH/SFTP downloads are now detected instead of hanging.
*   **Dialogs**: Fixed the Unit Conversion and Format Conversion dialogs crashing on open.
*   **SunPy Cache**: Clearing the Sunpy Data Cache names what it deletes (`~/sunpy` and `~/.sunpy`, SunPy's own folders, which also hold data downloaded outside SolarViewer) and asks first.
*   **Desktop Install**: The desktop install no longer replaces its own launcher, or another program's `sv`, in `~/.local/bin`: only links SolarViewer made are replaced or removed.

### Fixes
*   **2D Gaussian Fit**: A fit that finds no component now shows an error instead of an all-zero result.
*   **Solar Data Downloader CLI**: Fixed the launcher, which always failed.
*   **Titles**: Removed a spurious "Error getting title" message for FITS files without time or frequency.
*   **Beam**: The beam is drawn at the right angle after a Solar XY image is switched to RA/Dec; it kept its Solar XY angle.

### Under the Hood
*   **Restructured Codebase**: The 17,000-line viewer was split into focused packages (`core`, `tools`, `dialogs`, `processing`, `coordinates`, `video`, `ui`, `pipeline`). Old import paths still work but emit a deprecation warning.
*   **Packaging**: Moved from `setup.py` to `pyproject.toml`. The dependencies are now those the code uses: `requests`, `packaging`, `pandas`, `parfive`, `aiohttp` and `psutil` are declared, and the unused `reproject`, `dask`, `zarr`, `tifffile`, `qtpy` and `pyqt5-sip` are no longer required. OpenCV is no longer needed either: the Helioviewer browser now writes MP4 (H.264) with imageio, as Create Video does. The `setuptools<81` pin is gone (SolarViewer stopped using `pkg_resources` in 1.2.0).
*   **Dependencies**: `cdflib` is new (the spacecraft receivers' CDF files), and `python-casacore` is required everywhere but macOS.
*   **Cleanup**: Renamed `from_simpl` to `pipeline` and removed the unused `learmonth-py` scripts.

## Version 1.2.5

### Visualization & Annotations
*   **Shape Annotations**: Added shape annotations (Circle, Ellipse, Square, Rectangle) with support for customizable styling.
*   **Solar Radii Presets**: Quickly overlay 1-10 R☉ circles.

### Core Viewer Enhancements
*   **Drag & Drop**: Effortlessly open CASA images and FITS files by dragging them directly into the viewer.
*   **Interactive Interaction**: Added **Scroll-to-Zoom** support and a dedicated **Panning Mode** for more intuitive navigation in the main viewer.
*   **Fast Preview Mode**: Use the new `-f` command-line flag to launch the viewer in fast preview mode immediately from the terminal.

### Analysis & Processing
*   **Batch Conversion**: The batch processor now supports both **File Format conversion** and **Unit conversion** (e.g., Jy/beam to K).
*   **Dynamic Spectra 2.0**: Completely ported the dynamic spectra viewer from Matplotlib to **PyQtGraph**. This architectural shift provides hardware acceleration, enabling smoother interaction.

### Quality of Life & Bug Fixes
*   **Cursor States**: Fixed a persistent bug where the cursor would remain as a "panning hand" or "ROI crosshair" when switching to Ruler or Flux Profile tools.
*   **General**: Implemented several UI polishings and stability improvements across all modules.

## Version 1.2.4

### Dynamic Spectra Viewer
*   **Fix**: Standardized internal data orientation to ensure correct axis mapping (Time vs Frequency).
*   **Fix**: Resolved a crash (`NameError`) when using the **Extend Mask** feature.


## Version 1.2.3

### Radio Data Downloader
*   **Metadata Fix**: Corrected a bug in Learmonth/RSTN data conversion where FITS headers were generated with swapped Frequency/Time labels.
*   **Automated Cleanup**: Intermediate data files (.srs, .fit, .fit.gz) are now automatically removed after successful FITS conversion.

## Version 1.2.2

### Dynamic Spectra Viewer
*   **Advanced Masking**: Added **Extend Mask** (Global Frequency Masking) and **Clear Masks** features for effective RFI mitigation across time steps.
*   **UI Redesign**: Overhauled the user interface to enhance workflow and visual clarity.
*   **Large File Support**: Optimized handling of large FITS files for smoother performance.
*   **Metadata Viewer**: Enhanced the FITS header viewer layout for improved readability.
*   **Fix**: Resolved an issue where manual masks were stripped when toggling bandpass normalization.

### Helioviewer Browser
*   **Enhanced Visualization**: Added **Crop**, **Zoom**, and **Diff Map** (Base and Running) capabilities for detailed event analysis.
*   **Navigation**: Added a seekbar for intuitive time navigation.
*   **Export Fix**: Corrected timestamp sizing in exported GIF and MP4 videos to automatically adjust to frame size.
*   **Keyboard Shortcuts**: Implemented keyboard shortcuts for improved accessibility and faster navigation.

### Data Access & Caching
*   **Solar Activity**:
    *   Added **Radio Spectra** support for Wind/WAVES, STEREO, ORFEES, NDA, HIRAS, and YAMAGAWA instruments.
    *   Implemented robust **Caching Support** to accelerate subsequent data loads.
    *   Added smart **Cache Management** with policy-based retention.
*   **Radio Downloader**: Added support for downloading **e-Callisto** FITS files.

### Infrastructure & Platform
*   **Python 3.11 Compatibility**: Resolved Numpy warnings and SSL errors for compatibility with upcoming Python versions.
*   **MacOS Support**: Fixed working directory resolution to ensure temporary operations use correct writable locations.
*   **LOFAR Tools**: Added **HiDPI** support for crisp rendering on high-resolution displays.
*   **General**: Deprecated and removed the legacy Napari viewer.

### General Improvements
*   **Quality of Life**: Implemented various bug fixes and performance enhancements.

## Version 1.2.1

### Changes & Improvements
*   **Remote Dialog**: Made the remote file dialog non-modal for better workflow.
*   **Cache Display**: Added cache volume usage display in the menu.
*   **CASA Support**: Fixed cache size calculation for CASA images (directory-based).
*   **Contour RMS**: Fixed sigma contour RMS box logic to dynamically auto-calculate based on **source** contour data dimensions.

## Version 1.2.0

### Features & Infrastructure
*   **Remote Support**: Added comprehensive support for remote file access and operations.
*   **Desktop Integration**: Application can now be installed as a desktop application using `sv --install`.
*   **In-App Updates**: Added support for checking and performing updates directly within the application.
*   **Log Console**: Integrated a log console for real-time application feedback.
*   **Splash Screen**: Added a splash screen for improved startup experience.

### Visualization & Contours
*   **Contour Reprojection**: Added support for contour plotting on the entire extension after reprojection.
*   **Coordinate Conversion**: Automatic conversion between coordinate systems (HPC and RA/DEC) for contour overplots.
*   **Beam & Grid**: Added consolidated customization settings for beam and grid visualization.

### Performance & UI
*   **Plotting Performance**: Significantly decreased rendering time for FITS plotting, colormap adjustments, and stretch/preset changes.
*   **Contour Optimization**: Added contour downsampling support to accelerate contour loading after reprojection.
*   **High DPI Support**: Added support for High DPI displays with user-adjustable UI scaling in Preferences.
*   **UI Consistency**: Standardized the design and layout of many dialogs for a consistent user experience.

### Bug Fixes & General
*   **Phase Center**: Fixed issues with solar phase center shifting.
*   **General**: Other quality of life improvements.


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

### Contributors
- Soham Dey

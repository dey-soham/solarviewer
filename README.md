<h1 align="center">🌞 SolarViewer</h1>

<p align="center">
  <strong>A comprehensive Python toolkit for visualizing and analyzing solar radio images</strong>
</p>

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white" alt="Python 3.10+"></a>
  <a href="https://pypi.org/project/solarviewer/"><img src="https://img.shields.io/pypi/v/solarviewer?color=blue&logo=pypi&logoColor=white" alt="PyPI version"></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License: MIT"></a>
  <a href="https://github.com/dey-soham/solarviewer"><img src="https://img.shields.io/github/stars/dey-soham/solarviewer?style=social" alt="GitHub stars"></a>
</p>

<p align="center">
  <a href="#-features">Features</a> •
  <a href="#-installation">Installation</a> •
  <a href="#-quick-start">Quick Start</a> •
  <a href="#-command-line-interface">CLI</a> •
  <a href="#-documentation">Documentation</a> •
  <a href="#-contributing">Contributing</a>
</p>

---

## ✨ Features

**SolarViewer** is a feature-rich desktop application designed for solar physics research. It provides a full-featured multi-tab interface with comprehensive analysis tools, including helioprojective coordinate support for FITS and CASA image formats with specialized tools for radio astronomy.

### 📊 Analysis & Visualization

- **Statistical Analysis** — Detailed statistics for images and selected regions
- **2D Gaussian Fitting** — Fit Gaussian profiles to radio sources
- **Elliptical Ring Fitting** — Model ring-shaped emission features
- **Region Selection** — Rectangle and ellipse tools for region-of-interest analysis
- **Multiple Colormaps** — Choose from scientific colormaps with linear, log, sqrt, and custom stretches
- **Contour Overlays** — Overlay multi-wavelength contours (e.g., radio on EUV) with automatic coordinate reprojection
- **Stokes Parameters** — Full polarization support (I, Q, U, V, L, Lfrac, Vfrac, PANG)

### 🌐 Data Access & Downloads
 
- **Remote Access (SSH/SFTP)** — Browse and open files directly from remote servers with local caching
- **Helioviewer Browser** — Browse and download images from NASA's Helioviewer API with time-series playback
- **Solar Data Downloader** — Download data from SDO/AIA, IRIS, SOHO, GOES SUVI, STEREO, and GONG
- **Radio Data Downloader** — Access solar radio observation archives
- **Solar Activity Viewer** — Browse solar events (flares, CMEs, active regions), view context images and radio spectra, and plot GOES X-ray light curves
 
### 🎬 Video Creation
 
- **Time-lapse Videos** — Create MP4 videos from image sequences
- **Contour Overlays** — Overlay radio contours on EUV/optical base images
- **Custom Annotations** — Add timestamps, colorbars, and min/max plots
- **Preview Mode** — Real-time preview before rendering
 
### 🔧 Advanced Tools
 
- **Log Console** — Internal console to view application logs and debugging information
- **Dynamic Spectra Viewer** — Advanced viewer for radio spectra with RFI masking (ROI/Global), bandpass normalization, and cross-section analysis
- **LOFAR/SIMPL Support** — Calibration table visualizer and pipeline log viewer
- **Coordinate Transformations** — Convert between RA/Dec and helioprojective coordinates
- **Phase Center Tool** — Shift image phase centers for radio interferometry data
- **Export Options** — Export to FITS, CASA image, PNG, and region files

---

## 📦 Installation

**💡** Facing issues? See the [Troubleshooting Guide](INSTALLATION.md#troubleshooting).


### Prerequisites

- Python 3.10 or higher
- pip package manager
- **CASA data directory**: The `~/.casa/data` folder must exist for CASA to work properly. Create it with:
  ```bash
  mkdir -p ~/.casa/data
  ```

> **Note**: No other manual installation is required — all dependencies are installed automatically via pip.

### Recommended: Virtual Environment

It is highly recommended to install SolarViewer in a virtual environment to avoid conflicts with system packages.

```bash
# Create a virtual environment
python3 -m venv ~/.sv

# Using uv
# uv venv ~/.sv -p 3.13

# Using conda
# conda create -p ~/.sv python=3.13
```

```bash
# Activate the environment
source ~/.sv/bin/activate

# Using conda:
# conda activate ~/.sv
```

Once the virtual environment is active, proceed with the installation below. 

**💡** After running `sv --install` (see Desktop Integration below), you won't need to manually activate the environment to launch the application!

### Install from PyPI

```bash
pip install solarviewer

# Using uv
# uv pip install solarviewer
```

### Install from Source

```bash
git clone https://github.com/dey-soham/solarviewer.git
cd solarviewer
pip install -e .
```

### Desktop Integration

After installation, you can create a desktop entry and icon (Linux) or an application bundle (macOS) to launch SolarViewer from your application menu:

```bash
# Install desktop shortcuts and icons
solarviewer --install
# or
sv --install
```

To remove the desktop integration later:

```bash
solarviewer --uninstall
```
 
#### 💡 Optimal CASA Configuration
 
To prevent CASA from auto-updating and to disable telemetry, we recommend adding these configurations:
 
<details>
<summary>Click to view recommended settings</summary>
 
**`~/.casa/config.py`**
```python
datapath=["~/.casa/data"]
measurespath="~/.casa/data"
measures_auto_update=False
data_auto_update=False
nologfile=True
telemetry_enabled = False
crashreporter_enabled = False
```
 
**`~/.casa/casainit.py`**
```python
# CASA Initialization script to bypass updates
try:
    from casatasks.private.testmodes import bypass_casa_updates
    bypass_casa_updates(True)
    print("CASA auto-updates have been disabled via casainit.py")
except:
    pass
```
 
**`~/.casarc`**
```
logfile: /dev/null
EnableTelemetry: False
```
 
</details>
 
### Dependencies

<details>
<summary>View core dependencies</summary>

| Package | Version | Purpose |
|---------|---------|---------|
| PyQt5 | ≥5.15.0 | GUI framework |
| matplotlib | ≥3.5.0 | Plotting and visualization |
| numpy | ≥1.20.0 | Numerical operations |
| astropy | ≥5.0.0 | FITS handling, coordinates |
| scipy | ≥1.7.0 | Scientific computing |
| sunpy | ≥5.0.0 | Solar physics tools |
| casatools | ≥6.4.0 | CASA image support |
| casatasks | ≥6.4.0 | CASA tasks |

</details>

---

## 🚀 Quick Start

### Launch SolarViewer

```bash
solarviewer
# or
sv

# Open a specific file
solarviewer path/to/image.fits
```

### LOFAR Tools

```bash
viewcaltable       # Calibration table visualizer
viewlogs           # Pipeline log viewer
```

### Other Tools

```bash
viewsolaractivity  # Solar events browser
heliobrowser       # Helioviewer browser
viewds             # Dynamic spectra viewer
```
---

## 💻 Command Line Interface

### Command Line Interface (`solarviewer` / `sv`)

```bash
solarviewer [OPTIONS] [IMAGEFILE]

Options:
  --install         Install desktop integration
  --uninstall       Uninstall desktop integration
  --light           Start with light theme
  -v, --version     Show version and exit
  -h, --help        Show help message
```

---

## 📚 Documentation

### User Interface Overview

<details>
<summary><b>SolarViewer Controls</b></summary>

#### File Controls
- **Open Directory** — Load a folder of solar radio images
- **Open FITS File** — Load a single FITS file
- **Export Figure** — Save current view as image
- **Export as FITS** — Export data as FITS file

#### Display Controls
- **Colormap** — Choose visualization colormap
- **Stretch** — Linear, log, sqrt, power-law options
- **Gamma** — Adjust power-law exponent
- **Min/Max** — Manual or auto display range

#### Region Tools
- **Rectangle/Ellipse Selection** — Select regions for analysis
- **Export Region** — Save as CASA region file
- **Export Sub-image** — Extract region as new image

#### Analysis Tools
- **Fit 2D Gaussian** — Gaussian source fitting
- **Fit Elliptical Ring** — Ring model fitting
- **Image Statistics** — Full image statistics
- **Region Statistics** — Selected region statistics

</details>

---

## 🏗️ Project Structure

```
solarviewer/
├── solar_radio_image_viewer/
│   ├── main.py                 # Entry point
│   ├── install_utils.py        # Desktop integration (install/uninstall)
│   ├── viewer.py               # Standard viewer
│   ├── assets/                 # Icons and resources
│   ├── helioprojective.py      # Coordinate conversions
│   ├── helioprojective_viewer.py
│   ├── helioviewer_browser.py  # Helioviewer API browser
│   ├── video_dialog.py         # Video creation UI
│   ├── create_video.py         # Video rendering
│   ├── video_utils.py          # Video utilities
│   ├── noaa_events/            # Solar events browser
│   ├── solar_data_downloader/  # SDO/AIA, IRIS, etc.
│   ├── radio_data_downloader/  # Radio data archives
│   ├── solar_context/          # Real-time solar data
│   ├── remote/                 # Remote file access (SSH/SFTP)
│   ├── from_simpl/             # LOFAR/SIMPL tools
│   ├── learmonth-py/           # Learmonth data downloader
│   ├── move_phasecenter.py     # Phase center correction tool
│   ├── tutorial.py             # Tutorial
│   ├── dialogs.py              # Application dialogs
│   ├── splash.py               # Splash screen
│   ├── log_console.py          # Internal log viewer
│   ├── searchable_combobox.py  # Custom combobox widget
│   ├── norms.py                # Image normalization
│   ├── utils.py                # Utility functions
│   ├── utils/                  # Additional utilities (updater, limiter)
│   ├── version.py              # Version information
│   └── styles.py               # UI themes (light/dark)
├── resources/                  # Desktop integration resources
├── setup.py
├── requirements.txt
├── README.md
├── LICENSE
└── RELEASE_NOTES.md
```

---

## 🤝 Contributing

Contributions are welcome! Whether you're fixing bugs, adding features, or improving documentation, we appreciate your help.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## 📄 License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.

---

## 🙏 Acknowledgments

This project builds on the excellent work of the solar physics and radio astronomy communities:

### Core Libraries
- [**SunPy**](https://sunpy.org/) — Solar physics data analysis in Python
- [**Astropy**](https://www.astropy.org/) — Core astronomy library for FITS, coordinates, and units
- [**CASA**](https://casa.nrao.edu/) — Common Astronomy Software Applications for radio astronomy

### GUI & Visualization
- [**PyQt5**](https://www.riverbankcomputing.com/software/pyqt/) — Python bindings for Qt GUI framework
- [**Matplotlib**](https://matplotlib.org/) — Publication-quality plotting
- [**NumPy**](https://numpy.org/) — Fundamental package for scientific computing
- [**SciPy**](https://scipy.org/) — Scientific algorithms and mathematics

### Data Sources & APIs
- [**Helioviewer**](https://helioviewer.org/) — NASA/ESA solar image browser and API
- [**SolarMonitor**](https://solarmonitor.org/) — Real-time solar activity monitoring
- [**NOAA SWPC**](https://www.swpc.noaa.gov/) — Space Weather Prediction Center solar event data
- [**SDO/AIA**](https://sdo.gsfc.nasa.gov/) — Solar Dynamics Observatory
- [**JSOC**](http://jsoc.stanford.edu/) — Joint Science Operations Center for SDO data
- [**VSO**](https://sdac.virtualsolar.org/) — Virtual Solar Observatory

### Community
- The solar physics group at the National Centre for Radio Astrophysics for feedback and testing
- Deepan Patra for designing app icon and logo
- Atul Mohan for contributing to the download codebase and providing helpful feedback

---

## 👨‍💻 Author

**Soham Dey** — [sohamd943@gmail.com](mailto:sohamd943@gmail.com) — [@dey-soham](https://github.com/dey-soham)

---

<p align="center">
  <sub>Built with ❤️ for solar physics research</sub>
</p>

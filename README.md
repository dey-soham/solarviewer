<h1 align="center">🌞 SolarViewer</h1>

<p align="center">
  <strong>A comprehensive Python toolkit for visualizing and analyzing solar radio images</strong>
</p>

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.7%2B-blue?logo=python&logoColor=white" alt="Python 3.7+"></a>
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

**SolarViewer** is a feature-rich desktop application designed for solar physics research. It supports FITS and CASA image formats with specialized tools for radio astronomy.

### 🔭 Multiple Viewer Modes

| Viewer | Description |
|--------|-------------|
| **Standard Viewer** | Full-featured multi-tab interface with comprehensive analysis tools, including helioprojective coordinate support |
| **Napari Viewer** | Lightweight, fast viewer for quick visualization |

### 📊 Analysis & Visualization

- **Statistical Analysis** — Detailed statistics for images and selected regions
- **2D Gaussian Fitting** — Fit Gaussian profiles to radio sources
- **Elliptical Ring Fitting** — Model ring-shaped emission features
- **Region Selection** — Rectangle and ellipse tools for region-of-interest analysis
- **Multiple Colormaps** — Choose from scientific colormaps with linear, log, sqrt, and custom stretches
- **Stokes Parameters** — Full polarization support (I, Q, U, V, L, Lfrac, Vfrac, PANG)

### 🌐 Data Access & Downloads

- **Helioviewer Browser** — Browse and download images from NASA's Helioviewer API with time-series playback
- **Solar Data Downloader** — Download data from SDO/AIA, IRIS, SOHO, GOES SUVI, STEREO, and GONG
- **Radio Data Downloader** — Access solar radio observation archives
- **Solar Activity Viewer** — Browse solar events (flares, CMEs, active regions) with real-time data

### 🎬 Video Creation

- **Time-lapse Videos** — Create MP4 videos from image sequences
- **Contour Overlays** — Overlay radio contours on EUV/optical base images
- **Custom Annotations** — Add timestamps, colorbars, and min/max plots
- **Preview Mode** — Real-time preview before rendering

### 🔧 Advanced Tools

- **LOFAR/SIMPL Support** — Calibration table visualizer, dynamic spectra viewer, pipeline log viewer
- **Coordinate Transformations** — Convert between RA/Dec and helioprojective coordinates
- **Phase Center Tool** — Shift image phase centers for radio interferometry data
- **Export Options** — Export to FITS, CASA image, PNG, and region files

---

## 📦 Installation

### Prerequisites

- Python 3.7 or higher
- pip package manager
- **CASA data directory**: The `~/.casa/data` folder must exist for CASA to work properly. Create it with:
  ```bash
  mkdir -p ~/.casa/data
  ```

> **Note**: No other manual installation is required — all dependencies are installed automatically via pip.

### Install from PyPI

```bash
pip install solarviewer
```

### Install from Source

```bash
git clone https://github.com/dey-soham/solarviewer.git
cd solarviewer
pip install -e .
```

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
| napari | ≥0.4.16 | Fast image viewer |

</details>

---

## 🚀 Quick Start

### Launch the Viewer

```bash
# Standard viewer
solarviewer
# or
sv

# Open a specific file
solarviewer path/to/image.fits

# Fast Napari viewer
solarviewer -f path/to/image.fits
```

### LOFAR Tools

```bash
viewcaltable       # Calibration table visualizer
viewds             # Dynamic spectra viewer
viewlogs           # Pipeline log viewer
```

### Other Tools

```bash
viewsolaractivity  # Solar events browser
heliobrowser       # Helioviewer browser
```
---

## 💻 Command Line Interface

### Standard Viewer (`solarviewer` / `sv`)

```bash
solarviewer [OPTIONS] [IMAGEFILE]

Options:
  -f, --fast    Launch fast Napari viewer
  --light       Start with light theme
  -v, --version Show version and exit
  -h, --help    Show help message
```

---

## 📚 Documentation

### User Interface Overview

<details>
<summary><b>Standard Viewer Controls</b></summary>

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
│   ├── viewer.py               # Standard viewer
│   ├── helioprojective.py      # Coordinate conversions
│   ├── helioprojective_viewer.py
│   ├── helioviewer_browser.py  # Helioviewer API browser
│   ├── napari_viewer.py        # Fast viewer
│   ├── video_dialog.py         # Video creation UI
│   ├── create_video.py         # Video rendering
│   ├── noaa_events/            # Solar events browser
│   ├── solar_data_downloader/  # SDO/AIA, IRIS, etc.
│   ├── radio_data_downloader/  # Radio data archives
│   ├── solar_context/          # Real-time solar data
│   ├── from_simpl/             # LOFAR/SIMPL tools
│   ├── utils.py                # Utility functions
│   └── styles.py               # UI themes (light/dark)
├── setup.py
├── requirements.txt
└── README.md
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
- [**Napari**](https://napari.org/) — Fast n-dimensional image viewer

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

---

## 👨‍💻 Author

**Soham Dey** — [sohamd943@gmail.com](mailto:sohamd943@gmail.com) — [@dey-soham](https://github.com/dey-soham)

---

<p align="center">
  <sub>Built with ❤️ for solar physics research</sub>
</p>

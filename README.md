# Solar Radio Image Viewer

A comprehensive tool for visualizing and analyzing solar radio images in FITS and CASA formats.

![Solar Radio Image Viewer](https://github.com/dey-soham/solarviewer/raw/main/docs/images/screenshot.png)

## Developer

- Soham Dey [sohamd943@gmail.com](https://github.com/dey-soham)

## Features

### Standard Viewer
- **Multi-tab Interface**: Compare multiple images side by side
- **Comprehensive Analysis Tools**: Statistical analysis, region selection, and measurements
- **Coordinate Systems**: Support for multiple coordinate systems including helioprojective
- **Region Selection**: Rectangle selection tool for detailed analysis of specific regions
- **Statistical Analysis**: Detailed statistics for the entire image and selected regions
- **Visualization Controls**: Adjustable color maps, scaling, and display options
- **Export Options**: Export images, data, and regions in various formats

### Fast Napari Viewer
- **Lightweight Interface**: Quick loading and visualization of images
- **Basic Analysis**: View basic image statistics
- **Stokes Parameters**: View different Stokes parameters (I, Q, U, V, etc.)
- **Threshold Control**: Adjust threshold for better visualization

## Installation

### Prerequisites
- Python 3.7 or higher
- pip package manager

### Install from PyPI
```bash
pip install solar-image-viewer
```

### Install from Source
```bash
git clone https://github.com/yourusername/solarviewer.git
cd solarviewer
pip install -e .
```

## Usage

### Command Line Interface

#### Standard Viewer
```bash
# Launch the standard viewer
solarviewer

# Open a specific image in the standard viewer
solarviewer path/to/image.fits
```

#### Fast Napari Viewer
```bash
# Launch the fast Napari viewer
solarviewer -f
# or
sv --fast

# Open a specific image in the Napari viewer
solarviewer -f path/to/image.fits
# or
sv --fast path/to/image.fits
```

### Help
```bash
# Display help information
solarviewer --help
```

## User Interface Guide

### Standard Viewer

#### File Controls
- **Open Directory**: Load a directory containing solar radio images
- **Open FITS File**: Load a specific FITS file
- **Export Figure**: Export the current figure as an image file
- **Export Data as FITS**: Export the current data as a FITS file

#### Display Controls
- **Colormap**: Select from various colormaps for visualization
- **Stretch**: Choose between linear, log, sqrt, and power-law stretches
- **Gamma**: Adjust gamma value for power-law stretch
- **Min/Max**: Set display range manually or use auto-scaling options

#### Region Controls
- **Rectangle Selection**: Select a rectangular region for detailed analysis
- **Export Region**: Export the selected region as a CASA region file
- **Export Sub-Image**: Export the selected region as a new CASA image

#### Analysis Tools
- **Fit 2D Gaussian**: Fit a 2D Gaussian to the selected region
- **Fit Elliptical Ring**: Fit an elliptical ring to the selected region
- **Image Statistics**: View detailed statistics for the entire image
- **Region Statistics**: View statistics for the selected region

### Fast Napari Viewer

#### File Controls
- **Select File**: Load a FITS or CASA image file

#### Display Controls
- **Stokes Parameter**: Select the Stokes parameter to display
- **Threshold**: Set the threshold value for visualization

#### Statistics
- View basic statistics for the loaded image

## Development

### Project Structure
```
solarviewer/
├── solar_radio_image_viewer/
│   ├── __init__.py
│   ├── main.py           # Entry point
│   ├── viewer.py         # Standard viewer implementation
│   ├── napari_viewer.py  # Fast Napari viewer implementation
│   ├── utils.py          # Utility functions
│   ├── dialogs.py        # Dialog implementations
│   └── styles.py         # UI styles
├── setup.py              # Package setup
└── README.md             # This file
```

### Contributing
Contributions are welcome! Please feel free to submit a Pull Request.

## License
This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments
- [Napari](https://napari.org/) for the fast viewer implementation
- [Astropy](https://www.astropy.org/) for astronomical calculations
- [CASA](https://casa.nrao.edu/) for radio astronomy tools 
- [SunPy](https://sunpy.org/) for solar physics tools

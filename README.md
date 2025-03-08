# Napari Image Viewer

A simple viewer for FITS files and CASA images using napari, a multi-dimensional image viewer for Python.

## Features

- View FITS files and CASA image directories with napari's powerful visualization capabilities
- Select different Stokes parameters (I, Q, U, V)
- Choose from various colormaps
- Interactive zooming and panning
- Image statistics display
- Contrast adjustment controls

## Requirements

- Python 3.7+
- CASA (Common Astronomy Software Applications) for reading FITS files and CASA images
- Dependencies listed in `requirements.txt`

## Installation

1. Clone this repository:
   ```
   git clone <repository-url>
   cd <repository-directory>
   ```

2. Install the required dependencies:
   ```
   pip install -r requirements.txt
   ```

3. Make sure CASA is installed and available in your Python environment.

## Usage

Run the napari viewer:

```
python napari_viewer.py
```

### Controls

#### File Selection (Left Panel)
- **File Type**: Choose between FITS file or CASA image directory
- **Open Image**: Click to select a file or directory based on your selection
- **Current File**: Displays information about the currently loaded file

#### Display Controls (Right Panel)
- **Stokes**: Select the Stokes parameter to display (I, Q, U, V)
- **Colormap**: Choose from various colormaps for visualization
- **Contrast**: Adjust image contrast using Min/Max or Percentile scaling
- **Image Statistics**: View basic statistics about the current image

### Napari Controls

- **Zoom**: Mouse wheel or +/- keys
- **Pan**: Click and drag
- **Reset View**: Home key
- **Full Screen**: F key

## Notes

This is a basic viewer that demonstrates how to use napari to visualize astronomical images. It uses a simplified version of the data loading functionality from the main solar radio image viewer.

## License

[MIT License](LICENSE) 
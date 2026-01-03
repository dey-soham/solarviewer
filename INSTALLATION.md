# Installation Guide

This guide describes how to install SolarViewer and set it up as a native desktop application on your system.

## Prerequisites

- Python 3.7 or higher
- `pip` (Python package installer)

## 1. Install Codebase

First, install the python package and its dependencies. It is recommended to use a virtual environment.

```bash
# create virtual environment (optional but recommended)
python3 -m venv venv
source venv/bin/activate  # On Linux/macOS
# venv\Scripts\activate   # On Windows

# Install the package
pip install .
```

After this step, you can run the application from the terminal using:
```bash
solarviewer
# or
sv
```

## 2. Desktop Integration

You can install SolarViewer as a native desktop application (with an icon in your system launcher) using the provided scripts.

### Linux

To add SolarViewer to your applications menu (Gnome, KDE, XFCE, etc.):

```bash
python3 scripts/install_desktop.py
```

This will:
- Install the application icon to `~/.local/share/icons`.
- Create a `.desktop` entry in `~/.local/share/applications` pointing to your installation.

You can now search for "SolarViewer" in your system menu.

### macOS

To create a native macOS Application bundle (`.app`):

```bash
python3 scripts/install_mac.py
```

This will:
- Create `SolarViewer.app` in your `~/Applications` folder.
- Generate a high-quality icon.
- Create a launcher that uses your installed python environment.

You can now launch SolarViewer from Spotlight search or Finder.

## Troubleshooting

- **Icon not showing?** Try logging out and back in, or running `update-desktop-database ~/.local/share/applications` (on Linux).
- **Command not found?** Ensure your python environment is active or the `bin` directory is in your PATH. The desktop scripts try to automatically detect the correct executable path.

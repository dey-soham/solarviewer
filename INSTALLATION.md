# Installation Guide

This guide describes how to install SolarViewer and set it up as a native desktop application on your system.

> **⚠️ Note**: SolarViewer currently supports **Linux** and **macOS**. Windows is **not** supported.

## Prerequisites

- **OS**: Linux or macOS
- **Python**: 3.10 or higher (on a Mac: 3.12, see [macOS](#macos-apple-silicon))
- **CASA**: A valid CASA data directory at `~/.casa/data`

## 1. Install Codebase

It is **highly recommended** to install SolarViewer in a dedicated virtual environment. This prevents conflicts with your system Python and other packages.

### Step 1: Create Virtual Environment

```bash
# Create a virtual environment named '.sv' in your home directory
python3 -m venv ~/.sv

# Using uv:
# uv venv ~/.sv -p 3.13

# Using conda:
# conda create -p ~/.sv python=3.13

# On a Mac, use Python 3.12 in any of these (see "macOS (Apple Silicon)" below)
```

```bash
# Activate the environment
source ~/.sv/bin/activate

# Using conda:
# conda activate ~/.sv
```

### Step 2: Install Package

```bash
# Option A: Install from PyPI (Recommended)
pip install solarviewer

# Using uv:
# uv pip install solarviewer
```

```bash
# Option B: Install from Source (for development)
git clone https://github.com/dey-soham/solarviewer.git
cd solarviewer
pip install .
```

After this step, you can run the application from the terminal using `solarviewer` or `sv` **while the environment is active**.

### macOS (Apple Silicon)

**Use Python 3.12.** CASA (`casatools`) publishes its Apple Silicon builds for Python 3.12 on macOS 14 (Sonoma) or newer, and for Python 3.13 only on macOS 15 or newer. With another Python, pip finds no CASA to install.

```bash
uv venv ~/.sv -p 3.12            # or: python3.12 -m venv ~/.sv
source ~/.sv/bin/activate
uv pip install solarviewer       # or: pip install solarviewer
```

Everything is installed ready-built; nothing is compiled. `python-casacore` is **not** installed on macOS: it has no ready-built package for macOS on PyPI, so pip would have to compile it, which fails unless the casacore C++ libraries are installed. SolarViewer does not need it to open, process and analyse images: CASA carries its own casacore. Two tools do need it:

- the **Calibration Table Visualizer**;
- **Create Dynamic Spectra** with **Run on = This computer** (with **Run on = Server** this Mac needs nothing more).

Both say so when it is missing. To use them, take python-casacore from conda-forge, which has it built for Apple Silicon, and install SolarViewer into the same environment:

```bash
conda create -p ~/.sv -c conda-forge python=3.12 python-casacore
conda activate ~/.sv
pip install solarviewer
```

(or, in a conda environment that already has SolarViewer: `conda install -c conda-forge python-casacore`).

Older Macs: for macOS 13 on Apple Silicon and for Intel Macs, CASA's last builds are 6.6.5 for Python 3.10. SolarViewer is not tested there.

## 2. Desktop Integration

To make SolarViewer behave like a native application (launch from Start Menu/Spotlight, no need to manually activate virtual environment), run the built-in installation tool:

```bash
# Install desktop shortcuts and icons
sv --install
```

### What this does:

*   **Linux**:
    *   Installs the application icon to `~/.local/share/icons`.
    *   Creates a `.desktop` entry in `~/.local/share/applications`.
    *   Updates your shell configuration (`.bashrc` / `.zshrc`) to ensure `~/.local/bin` is in your PATH.
*   **macOS**:
    *   Creates a generic `SolarViewer.app` bundle in `~/Applications`.
    *   Configures the bundle to use your specific virtual environment automatically.

### Verification

*   **Linux**: Open your application launcher (Super/Windows key) and search for "SolarViewer".
*   **macOS**: Open Spotlight (Cmd+Space) and search for "SolarViewer".

## 3. Optimal CASA Configuration

To prevent CASA from auto-updating and to disable telemetry/crash reporting, we recommend adding the following configurations to your home directory.

> **⚠️ Important**: If these configurations are not set, `solarviewer` may take a long time to load as CASA attempts to perform background updates. The splash screen says so while it happens ("Checking for newer CASA data", "Downloading CASA's measures tables"), and once the window is up the log (View > Log console) says how long it took. With the updates turned off, CASA's data stay as they are until `python -m casaconfig --update-all` is run.

### `~/.casa/config.py`
Add or create this file with:
```python
datapath=["~/.casa/data"]
measurespath="~/.casa/data"
measures_auto_update=False
data_auto_update=False
nologfile=True
telemetry_enabled = False
crashreporter_enabled = False
```

### `~/.casa/casainit.py`
Add or create this file with:
```python
# CASA Initialization script to bypass updates
try:
    from casatasks.private.testmodes import bypass_casa_updates
    bypass_casa_updates(True)
    print("CASA auto-updates have been disabled via casainit.py")
except:
    pass
```

### `~/.casarc`
Add or create this file with:
```
logfile: /dev/null
EnableTelemetry: False
```

## Uninstallation

To remove the desktop integration:

```bash
sv --uninstall
```

To remove the application entirely, simply delete the virtual environment:

```bash
rm -rf ~/.sv
```

## Troubleshooting
  
### **Icon not showing?**
*   **Linux**:
    *   Try logging out and back in.
    *   Or run: `update-desktop-database ~/.local/share/applications`
*   **macOS**:
    *   Restart Finder.
    *   Or run `killall Dock` in the terminal to refresh the icon cache.

### **Create Dynamic Spectra and python-casacore**
Create Dynamic Spectra reads Measurement Sets with python-casacore, but only on the machine that runs the engine. With **Run on = Server**, the engine runs inside the server's container (or Python) and this machine needs only `paramiko` (installed with SolarViewer); with **Run on = This computer** it needs python-casacore here. The dialog says so if it is missing. The same window starts from the command line with `createds`. On Linux, python-casacore is installed with SolarViewer (the Calibration Table Visualizer needs it too); on macOS it is not, see [macOS (Apple Silicon)](#macos-apple-silicon).

### **`No matching distribution found for casatools` (macOS)**
The Python of the environment has no CASA build for this Mac. Make the environment with Python 3.12 (see [macOS (Apple Silicon)](#macos-apple-silicon)).

### **Build Error: `Failed to build python-casacore` (macOS)**
SolarViewer 1.3 and later do not install python-casacore on macOS, so this comes up only with an older SolarViewer, or when python-casacore is installed with pip on a Mac (`pip install python-casacore`, or `pip install "solarviewer[casacore]"`). An error like `Call to scikit_build_core.build.build_wheel failed` or `Casacore: unable to find the header file casa/aips.h` means the wrapper cannot find the underlying C++ libraries.

**Fix 1: Install via Conda (Recommended)**
This is the most reliable method as it installs pre-compiled binaries, avoiding all compilation issues.
```bash
conda install -c conda-forge python-casacore
```

**Fix 2: Install via Homebrew**
If you prefer Homebrew, you must manually install all dependencies and set compiler search paths. This is common on Apple Silicon.

1. Install Homebrew (if not already installed):
   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```
2. Tap repository and install dependencies:
   ```bash
   brew tap ska-sa/tap
   brew install casacore boost boost-python3 cfitsio wcslib
   ```
3. Set environment variables to help the compiler find headers and libraries:
   ```bash
   export CPLUS_INCLUDE_PATH="$(brew --prefix)/include:$(brew --prefix boost)/include:$(brew --prefix boost-python3)/include"
   export LIBRARY_PATH="$(brew --prefix)/lib:$(brew --prefix boost)/lib:$(brew --prefix boost-python3)/lib"
   ```
4. Install SolarViewer together with python-casacore, built from source:
   ```bash
   pip install --no-binary python-casacore "solarviewer[casacore]"
   ```

### **Environment conflicts in Conda**
If packages from your user site-packages or `PYTHONPATH` are picked up instead of the ones in your Conda environment, isolate the environment before running SolarViewer:
```bash
export PYTHONNOUSERSITE=1
unset PYTHONPATH
```

### **Command `solarviewer` or `sv` not found?**
*   Ensure your virtual environment is active:
    ```bash
    source ~/.sv/bin/activate
    ```
*   If you already ran `sv --install` but the command still isn't found in a **new terminal**:
    *   Restart your terminal session.
    *   Check if `~/.local/bin` is in your PATH:
        ```bash
        echo $PATH
        ```
    *   If not, add this to your `~/.bashrc` or `~/.zshrc`:
        ```bash
        export PATH="$HOME/.local/bin:$PATH"
        ```

### **SSL Error**
We have included an automatic fix for `ssl.SSLError` on Linux/Python 3.11+: SolarViewer starts with `OPENSSL_CONF=/dev/null`, so OpenSSL skips the system's configuration file (certificates are still checked). If you need your own OpenSSL configuration (for example a FIPS or company policy), set `OPENSSL_CONF` yourself and SolarViewer keeps it. If you still encounter issues, try running with:
```bash
OPENSSL_CONF=/dev/null solarviewer
```

To make this permanent, add it to your shell configuration (`~/.bashrc` or `~/.zshrc`):
```bash
export OPENSSL_CONF=/dev/null
```

### **Qt xcb error (WSL/Linux)**
If you see the error: `Could not load the Qt platform plugin "xcb" even though it was found`, it usually means your Linux system is missing some low-level graphical libraries required by Qt.

**Fix (Ubuntu/Debian-based WSL):**
```bash
sudo apt-get update && sudo apt-get install -y \
    libxcb-xinerama0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
    libxcb-render-util0 libxcb-xkb1 libxkbcommon-x11-0 libdbus-1-3 \
    libxcb-cursor0 libgl1 libegl1 libx11-xcb1 \
    libfontconfig1 libice6 libsm6 libxext6 libxrender1 libxtst6 libxcb-util1
```

**Fix (Fedora-based WSL):**
```bash
sudo dnf install libxcb-devel libxkbcommon-x11 libX11-xcb libxcb-util
```

#### **Still having issues?**
The error can also occur if WSL cannot connect to your Windows display.

1.  **Test if libraries are okay:**
    Run with `offscreen` mode. If this doesn't crash, your libraries are fine, but your display setup is broken:
    ```bash
    QT_QPA_PLATFORM=offscreen solarviewer
    ```

2.  **Check Display Connection (for Windows 10 / No WSLg):**
    If you are not on Windows 11 (or don't have WSLg enabled), you must run an X Server (like **VcXsrv**) on Windows and set your `DISPLAY` variable:
    ```bash
    export DISPLAY=$(grep -m 1 nameserver /etc/resolv.conf | awk '{print $2}'):0.0
    export LIBGL_ALWAYS_INDIRECT=1
    ```

# Installation Guide

This guide describes how to install SolarViewer and set it up as a native desktop application on your system.

> **⚠️ Note**: SolarViewer currently supports **Linux** and **macOS**. Windows is **not** supported.

## Prerequisites

- **OS**: Linux or macOS
- **Python**: 3.10 or higher
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

### **Build Error: `Failed to build python-casacore` (macOS)**
If you see an error like `Call to scikit_build_core.build.build_wheel failed` or `Casacore: unable to find the header file casa/aips.h`, it means the wrapper cannot find the underlying C++ libraries. This is common on Apple Silicon Macs.

**Fix:** Install the C++ `casacore` libraries via Homebrew:
```bash
brew install casacore
```
Then try installing SolarViewer again.

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
We have included an automatic fix for `ssl.SSLError` on Linux/Python 3.11+. If you still encounter issues, try running with:
```bash
OPENSSL_CONF=/dev/null solarviewer
```

To make this permanent, add it to your shell configuration (`~/.bashrc` or `~/.zshrc`):
```bash
export OPENSSL_CONF=/dev/null
```

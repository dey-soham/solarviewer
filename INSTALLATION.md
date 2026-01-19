# Installation Guide

This guide describes how to install SolarViewer and set it up as a native desktop application on your system.

> **⚠️ Note**: SolarViewer currently supports **Linux** and **macOS**. Windows is **not** supported.

## Prerequisites

- **OS**: Linux or macOS
- **Python**: 3.9 or higher
- **CASA**: A valid CASA data directory at `~/.casa/data`

## 1. Install Codebase

It is **highly recommended** to install SolarViewer in a dedicated virtual environment. This prevents conflicts with your system Python and other packages.

### Step 1: Create Virtual Environment

```bash
# Create a virtual environment named '.sv' in your home directory
python3 -m venv ~/.sv

# Activate the environment
source ~/.sv/bin/activate
```

### Step 2: Install Package

```bash
# Option A: Install from PyPI (Recommended)
pip install solarviewer

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

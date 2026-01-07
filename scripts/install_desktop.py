#!/usr/bin/env python3
import os
import shutil
import sys
from pathlib import Path


def install_desktop_integration():
    """Install the desktop entry and icon for SolarViewer."""
    print("Installing SolarViewer desktop integration...")

    # Define paths
    project_root = Path(__file__).parent.parent.absolute()
    resources_dir = project_root / "resources"
    assets_dir = project_root / "solar_radio_image_viewer" / "assets"

    desktop_template = resources_dir / "solarviewer.desktop"
    icon_source = assets_dir / "icon.png"

    # Destination paths (user specific)
    home = Path.home()
    applications_dir = home / ".local" / "share" / "applications"
    icons_dir = home / ".local" / "share" / "icons" / "hicolor" / "128x128" / "apps"

    # Ensure directories exist
    applications_dir.mkdir(parents=True, exist_ok=True)
    icons_dir.mkdir(parents=True, exist_ok=True)

    # 1. Install Icon
    target_icon_path = icons_dir / "solarviewer.png"
    if icon_source.exists():
        shutil.copy2(icon_source, target_icon_path)
        print(f"Icon installed to: {target_icon_path}")
    else:
        print(f"Warning: Icon not found at {icon_source}")
        return False

    # 2. Find Executable
    executable_path = shutil.which("solarviewer")
    if not executable_path:
        # Fallback: try to find it in the current python environment's bin
        potential_path = Path(sys.prefix) / "bin" / "solarviewer"
        if potential_path.exists():
            executable_path = str(potential_path)

    if not executable_path:
        print(
            "Error: Could not find 'solarviewer' executable. Please ensure it is installed."
        )
        return False

    print(f"Found executable at: {executable_path}")

    # 3. Create and Install Desktop File
    if desktop_template.exists():
        with open(desktop_template, "r") as f:
            content = f.read()

        # Replace placeholders/update Exec
        # The template has Exec=solarviewer %F. We replace 'solarviewer' with the full path if needed,
        # but usually using the full path is safer to avoid PATH issues in desktop launchers.
        # However, we need to preserve %F

        updated_content = content.replace("Exec=solarviewer", f"Exec={executable_path}")
        updated_content = updated_content.replace(
            "Icon=solarviewer", f"Icon={target_icon_path}"
        )

        target_desktop_path = applications_dir / "solarviewer.desktop"
        with open(target_desktop_path, "w") as f:
            f.write(updated_content)

        print(f"Desktop entry installed to: {target_desktop_path}")

    else:
        print(f"Error: Desktop template not found at {desktop_template}")
        return False

    # 4. Update desktop database and icon cache (optional but recommended)
    try:
        from subprocess import run, DEVNULL

        run(
            ["update-desktop-database", str(applications_dir)],
            stdout=DEVNULL,
            stderr=DEVNULL,
        )
        print("Desktop database updated.")
    except Exception:
        pass  # Not critical

    try:
        from subprocess import run, DEVNULL

        icons_base = home / ".local" / "share" / "icons" / "hicolor"
        run(["gtk-update-icon-cache", str(icons_base)], stdout=DEVNULL, stderr=DEVNULL)
        print("Icon cache updated.")
    except Exception:
        pass  # Not critical

    print(
        "\nInstallation complete! You should now see 'SolarViewer' in your application menu."
    )
    return True


if __name__ == "__main__":
    install_desktop_integration()

#!/usr/bin/env python3
import os
import sys
import shutil
from pathlib import Path


def uninstall_desktop_integration():
    """Uninstall the desktop entry and icon for SolarViewer."""
    print("Uninstalling SolarViewer desktop integration...")

    # Destination paths (user specific)
    home = Path.home()
    
    # 1. Remove Desktop File
    applications_dir = home / ".local" / "share" / "applications"
    desktop_file = applications_dir / "solarviewer.desktop"
    
    if desktop_file.exists():
        desktop_file.unlink()
        print(f"Removed: {desktop_file}")
    else:
        print(f"Not found: {desktop_file}")

    # 2. Remove Icon
    icons_dir = home / ".local" / "share" / "icons" / "hicolor" / "128x128" / "apps"
    icon_file = icons_dir / "solarviewer.png"
    
    if icon_file.exists():
        icon_file.unlink()
        print(f"Removed: {icon_file}")
    else:
        print(f"Not found: {icon_file}")

    # 3. Remove Symlinks
    bin_dir = home / ".local" / "bin"
    links = ["solarviewer", "sv"]
    
    for link_name in links:
        target_link = bin_dir / link_name
        if target_link.exists() or target_link.is_symlink():
            try:
                target_link.unlink()
                print(f"Removed symlink: {target_link}")
            except Exception as e:
                print(f"Error removing {target_link}: {e}")
        else:
            print(f"Not found: {target_link}")

    # 4. Update desktop database and icon cache
    try:
        from subprocess import run, DEVNULL

        if applications_dir.exists():
            run(
                ["update-desktop-database", str(applications_dir)],
                stdout=DEVNULL,
                stderr=DEVNULL,
            )
            print("Desktop database updated.")
    except Exception:
        pass

    try:
        from subprocess import run, DEVNULL
        
        icons_base = home / ".local" / "share" / "icons" / "hicolor"
        if icons_base.exists():
            run(["gtk-update-icon-cache", str(icons_base)], stdout=DEVNULL, stderr=DEVNULL)
            print("Icon cache updated.")
    except Exception:
        pass

    print("\nUninstallation complete!")
    print("Note: The virtual environment and source code were NOT removed.")


if __name__ == "__main__":
    uninstall_desktop_integration()

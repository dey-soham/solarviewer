#!/usr/bin/env python3
"""
Script to uninstall SolarViewer macOS Application and symlinks.
This script should be run on macOS.
"""
import os
import sys
import shutil
from pathlib import Path


def uninstall_mac_app():
    print("Uninstalling SolarViewer app and integration...")

    # Define paths
    home = Path.home()
    
    # 1. Remove .app Bundle
    install_dir = home / "Applications"
    app_name = "SolarViewer.app"
    app_bundle = install_dir / app_name
    
    if app_bundle.exists():
        print(f"Removing {app_bundle}...")
        try:
            shutil.rmtree(app_bundle)
            print("App bundle removed.")
        except Exception as e:
            print(f"Error removing app bundle: {e}")
    else:
        print(f"App bundle not found at {app_bundle}")

    # 2. Remove Symlinks
    bin_dir = home / ".local" / "bin"
    links = ["solarviewer", "sv"]
    
    for link_name in links:
        target_link = bin_dir / link_name
        if target_link.exists() or target_link.is_symlink():
            try:
                target_link.unlink()
                print(f"Removed symlink: {target_link}")
            except Exception as e:
                print(f"Error removing symlink {target_link}: {e}")
        else:
            print(f"Symlink not found: {target_link}")

    print("\nUninstallation complete!")
    print("Note: The virtual environment and source code were NOT removed.")


if __name__ == "__main__":
    if sys.platform != "darwin":
        print("Warning: This script is intended for macOS.")
        response = input("Do you want to continue anyway? (y/n): ")
        if response.lower() != 'y':
            sys.exit(1)
            
    uninstall_mac_app()

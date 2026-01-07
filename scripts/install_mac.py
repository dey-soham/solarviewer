#!/usr/bin/env python3
"""
Script to install SolarViewer as a macOS Application (.app bundle).
This script should be run on macOS.
"""
import os
import sys
import shutil
import subprocess
from pathlib import Path

def install_mac_app():
    print("Creating SolarViewer.app bundle...")

    # Define paths
    project_root = Path(__file__).parent.parent.absolute()
    assets_dir = project_root / "solar_radio_image_viewer" / "assets"
    icon_source = assets_dir / "icon.png"
    
    # Target paths
    # Prefer user applications folder
    install_dir = Path.home() / "Applications"
    install_dir.mkdir(exist_ok=True)
    
    app_name = "SolarViewer.app"
    app_bundle = install_dir / app_name
    
    contents_dir = app_bundle / "Contents"
    macos_dir = contents_dir / "MacOS"
    resources_dir = contents_dir / "Resources"
    
    # 1. Create Directory Structure
    if app_bundle.exists():
        print(f"Removing existing {app_bundle}...")
        shutil.rmtree(app_bundle)
        
    macos_dir.mkdir(parents=True, exist_ok=True)
    resources_dir.mkdir(parents=True, exist_ok=True)
    
    # 2. Create Info.plist
    # We locate the python executable and the solarviewer entry point
    python_executable = sys.executable
    
    # Try to find 'solarviewer' in the same bin dir as python executable
    bin_dir = Path(python_executable).parent
    solarviewer_exec = bin_dir / "solarviewer"
    
    if not solarviewer_exec.exists():
         # Fallback: assume it's in path or ask user to ensure it's installed
         # But for the bundle we usually want absolute paths or a wrapping script
         solarviewer_exec = shutil.which("solarviewer")
         
    if not solarviewer_exec:
        print("Error: Could not find 'solarviewer' executable. Please ensure it is installed (pip install .).")
        return False

    info_plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>SolarViewer</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundleIdentifier</key>
    <string>com.dey-soham.solarviewer</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>SolarViewer</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>LSMinimumSystemVersion</key>
    <string>10.13</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
"""
    with open(contents_dir / "Info.plist", "w") as f:
        f.write(info_plist_content)

    # 3. Create Launcher Script (The executable)
    # This script sets up the environment and calls the actual python entry point
    launcher_content = f"""#!/bin/bash
export PATH="{bin_dir}:$PATH"
"{python_executable}" -m solar_radio_image_viewer.main "$@"
"""
    launcher_path = macos_dir / "SolarViewer"
    with open(launcher_path, "w") as f:
        f.write(launcher_content)
    
    # Make executable
    launcher_path.chmod(0o755)
    
    # 4. Handle Icon (Convert PNG to ICNS)
    # macOS requires .icns. We can use 'iconutil' if available (native mac tool)
    if icon_source.exists():
        iconset_dir = resources_dir / "AppIcon.iconset"
        iconset_dir.mkdir(exist_ok=True)
        
        # We need to create various sizes for the iconset
        # Using sips (scriptable image processing system) built-in on macOS
        
        sizes = [16, 32, 128, 256, 512]
        try:
            for size in sizes:
                subprocess.run([
                    "sips", "-Z", str(size),
                    str(icon_source), 
                    "--out", str(iconset_dir / f"icon_{size}x{size}.png")
                ], check=True, stdout=subprocess.DEVNULL)
                
                # Create @2x files (same size logically, but double pixels)
                subprocess.run([
                    "sips", "-Z", str(size * 2),
                    str(icon_source), 
                    "--out", str(iconset_dir / f"icon_{size}x{size}@2x.png")
                ], check=True, stdout=subprocess.DEVNULL)
            
            # Convert iconset to icns
            subprocess.run([
                "iconutil", "-c", "icns", 
                str(iconset_dir), 
                "-o", str(resources_dir / "AppIcon.icns")
            ], check=True)
            
            # Cleanup iconset
            shutil.rmtree(iconset_dir)
            print("Generated AppIcon.icns")
            
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("Warning: Could not convert icon using sips/iconutil. Copying PNG as fallback (might not work as expected).")
            shutil.copy2(icon_source, resources_dir / "icon.png")
    else:
        print(f"Warning: Icon source not found at {icon_source}")

    print(f"\nSuccess! SolarViewer.app created at: {app_bundle}")
    print("You can now open it from Finder or Spotlight.")
    return True

if __name__ == "__main__":
    if sys.platform != "darwin":
        print("Warning: This script is intended for macOS.")
        response = input("Do you want to continue anyway? (y/n): ")
        if response.lower() != 'y':
            sys.exit(1)
            
    install_mac_app()

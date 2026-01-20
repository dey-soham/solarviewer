import sys
import os
import shutil
import subprocess
from pathlib import Path
from .version import __version__

# Inlined desktop template
DESKTOP_TEMPLATE = f"""[Desktop Entry]
Version={__version__}
Type=Application
Name=SolarViewer
Comment=Visualize and analyze solar radio images
Exec={{exec_path}} %F
Icon={{icon_path}}
Terminal=false
Categories=Science;Astronomy;Education;
Keywords=Solar;Radio;Astronomy;Image;Viewer;
StartupNotify=true
MimeType=image/fits;
"""


def install_desktop_integration():
    """Dispatch installation based on platform."""
    if sys.platform == "darwin":
        return _install_mac()
    elif sys.platform.startswith("linux"):
        return _install_linux()
    else:
        print(f"Platform {sys.platform} is not supported for desktop integration.")
        return False


def uninstall_desktop_integration():
    """Dispatch uninstallation based on platform."""
    if sys.platform == "darwin":
        return _uninstall_mac()
    elif sys.platform.startswith("linux"):
        return _uninstall_linux()
    else:
        print(f"Platform {sys.platform} is not supported for desktop integration.")
        return False


# ==========================================
# Linux Implementation
# ==========================================


def _install_linux():
    print("Installing SolarViewer desktop integration (Linux)...")

    # Define paths
    # Assuming this file is in solar_radio_image_viewer/install_utils.py
    package_dir = Path(__file__).parent
    assets_dir = package_dir / "assets"
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
        # Fallback if running from source without package structure or different layout
        print(f"Warning: Icon not found at {icon_source}")
        # Try to find it relative to current working directory if developed locally
        alt_icon = Path.cwd() / "solar_radio_image_viewer" / "assets" / "icon.png"
        if alt_icon.exists():
            shutil.copy2(alt_icon, target_icon_path)
            print(f"Icon installed to: {target_icon_path}")
        else:
            print("Could not find icon file.")
            # We proceed anyway, but icon will be missing

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
    content = DESKTOP_TEMPLATE.format(
        exec_path=executable_path, icon_path=target_icon_path
    )
    target_desktop_path = applications_dir / "solarviewer.desktop"

    with open(target_desktop_path, "w") as f:
        f.write(content)

    print(f"Desktop entry installed to: {target_desktop_path}")

    # 4. Update desktop database and icon cache
    try:
        from subprocess import run, DEVNULL

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
        run(["gtk-update-icon-cache", str(icons_base)], stdout=DEVNULL, stderr=DEVNULL)
        print("Icon cache updated.")
    except Exception:
        pass

    # 5. Create Symlinks in ~/.local/bin and configure PATH
    _setup_local_bin_symlinks(executable_path)

    print(
        "\nInstallation complete! You should now see 'SolarViewer' in your application menu."
    )
    return True


def _uninstall_linux():
    print("Uninstalling SolarViewer desktop integration (Linux)...")
    home = Path.home()

    # 1. Remove Desktop File
    applications_dir = home / ".local" / "share" / "applications"
    desktop_file = applications_dir / "solarviewer.desktop"
    if desktop_file.exists():
        desktop_file.unlink()
        print(f"Removed: {desktop_file}")

    # 2. Remove Icon
    icons_dir = home / ".local" / "share" / "icons" / "hicolor" / "128x128" / "apps"
    icon_file = icons_dir / "solarviewer.png"
    if icon_file.exists():
        icon_file.unlink()
        print(f"Removed: {icon_file}")

    # 3. Remove Symlinks
    _remove_local_bin_symlinks()

    # 4. Update databases
    try:
        from subprocess import run, DEVNULL

        if applications_dir.exists():
            run(
                ["update-desktop-database", str(applications_dir)],
                stdout=DEVNULL,
                stderr=DEVNULL,
            )
    except Exception:
        pass

    try:
        from subprocess import run, DEVNULL

        icons_base = home / ".local" / "share" / "icons" / "hicolor"
        if icons_base.exists():
            run(
                ["gtk-update-icon-cache", str(icons_base)],
                stdout=DEVNULL,
                stderr=DEVNULL,
            )
    except Exception:
        pass

    print("\nUninstallation complete!")
    return True


# ==========================================
# macOS Implementation
# ==========================================


def _install_mac():
    print("Creating SolarViewer.app bundle (macOS)...")

    package_dir = Path(__file__).parent
    assets_dir = package_dir / "assets"
    icon_source = assets_dir / "icon.png"

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
    python_executable = sys.executable
    solarviewer_exec = shutil.which("solarviewer")

    if not solarviewer_exec:
        # Fallback relative to python exec
        potential = Path(sys.prefix) / "bin" / "solarviewer"
        if potential.exists():
            solarviewer_exec = str(potential)

    if not solarviewer_exec:
        print("Error: Could not find 'solarviewer' executable.")
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
    <string>{__version__}</string>
    <key>CFBundleVersion</key>
    <string>{__version__}</string>
    <key>LSMinimumSystemVersion</key>
    <string>10.13</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
"""
    with open(contents_dir / "Info.plist", "w") as f:
        f.write(info_plist_content)

    # 3. Create Launcher Script
    # Calculate bin dir
    bin_dir = Path(python_executable).parent
    launcher_content = f"""#!/bin/bash
export PATH="{bin_dir}:$PATH"
"{python_executable}" -m solar_radio_image_viewer.main "$@"
"""
    launcher_path = macos_dir / "SolarViewer"
    with open(launcher_path, "w") as f:
        f.write(launcher_content)
    launcher_path.chmod(0o755)

    # 4. Handle Icon (Convert PNG to ICNS)
    # Reuse previous logic: use sips/iconutil or copy png
    if icon_source.exists():
        _create_mac_icon(icon_source, resources_dir)
    else:
        # Try finding it elsewhere
        alt_icon = Path.cwd() / "solar_radio_image_viewer" / "assets" / "icon.png"
        if alt_icon.exists():
            _create_mac_icon(alt_icon, resources_dir)
        else:
            print("Warning: Icon files not found.")

    print(f"\nSuccess! SolarViewer.app created at: {app_bundle}")

    # 5. Create Symlinks and update PATH
    _setup_local_bin_symlinks(solarviewer_exec)
    return True


def _create_mac_icon(icon_source, resources_dir):
    try:
        iconset_dir = resources_dir / "AppIcon.iconset"
        iconset_dir.mkdir(exist_ok=True)

        sizes = [16, 32, 128, 256, 512]
        for size in sizes:
            subprocess.run(
                [
                    "sips",
                    "-Z",
                    str(size),
                    "-s",
                    "format",
                    "png",
                    str(icon_source),
                    "--out",
                    str(iconset_dir / f"icon_{size}x{size}.png"),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            subprocess.run(
                [
                    "sips",
                    "-Z",
                    str(size * 2),
                    "-s",
                    "format",
                    "png",
                    str(icon_source),
                    "--out",
                    str(iconset_dir / f"icon_{size}x{size}@2x.png"),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        subprocess.run(
            [
                "iconutil",
                "-c",
                "icns",
                str(iconset_dir),
                "-o",
                str(resources_dir / "AppIcon.icns"),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )

        shutil.rmtree(iconset_dir)
        print("Generated AppIcon.icns")
    except Exception:
        print(
            "Warning: Could not enable custom icon (requires sips/iconutil). Using default."
        )
        shutil.copy2(icon_source, resources_dir / "icon.png")


def _uninstall_mac():
    print("Uninstalling SolarViewer.app (macOS)...")
    home = Path.home()
    app_bundle = home / "Applications" / "SolarViewer.app"

    if app_bundle.exists():
        shutil.rmtree(app_bundle)
        print("App bundle removed.")

    _remove_local_bin_symlinks()
    print("Uninstallation complete!")
    return True


# ==========================================
# Shared Helpers
# ==========================================


def _setup_local_bin_symlinks(executable_path):
    home = Path.home()
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    links = ["solarviewer", "sv"]
    for link_name in links:
        target_link = bin_dir / link_name
        try:
            if target_link.exists() or target_link.is_symlink():
                target_link.unlink()

            target_link.symlink_to(executable_path)
            print(f"Created symlink: {target_link} -> {executable_path}")
        except Exception as e:
            print(f"Warning: Could not create symlink {target_link}: {e}")

    # Auto-configure PATH if needed
    if str(bin_dir) not in os.environ["PATH"]:
        print(f"\nWarning: {bin_dir} is not in your PATH.")

        shell = os.environ.get("SHELL", "")
        config_file = None

        if "zsh" in shell:
            config_file = home / ".zshrc"
        elif "bash" in shell:
            if sys.platform == "darwin":
                config_file = home / ".bash_profile"
            else:
                config_file = home / ".bashrc"

        if config_file:
            print(f"Attempting to add to {config_file}...")
            export_cmd = 'export PATH="$HOME/.local/bin:$PATH"'

            already_configured = False
            if config_file.exists():
                try:
                    with open(config_file, "r") as f:
                        if export_cmd in f.read():
                            already_configured = True
                except Exception:
                    pass

            if not already_configured:
                try:
                    with open(config_file, "a") as f:
                        f.write(f"\n# Added by SolarViewer installer\n{export_cmd}\n")
                    print(f"Successfully added ~/.local/bin to {config_file}")
                    print(
                        f"Please restart your shell or run 'source {config_file}' to apply changes."
                    )
                except Exception as e:
                    print(f"Could not update {config_file}: {e}")
            else:
                print(
                    f"Configuration already exists in {config_file} but is not active."
                )


def _remove_local_bin_symlinks():
    home = Path.home()
    bin_dir = home / ".local" / "bin"
    links = ["solarviewer", "sv"]
    for link_name in links:
        target_link = bin_dir / link_name
        if target_link.exists() or target_link.is_symlink():
            try:
                target_link.unlink()
                print(f"Removed symlink: {target_link}")
            except Exception:
                pass

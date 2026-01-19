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

    # 5. Create Symlinks in ~/.local/bin
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

    # Check if ~/.local/bin is in PATH
    if str(bin_dir) not in os.environ["PATH"]:
        print(f"\nWarning: {bin_dir} is not in your PATH.")
        
        # Auto-configure PATH
        shell = os.environ.get("SHELL", "")
        config_file = None
        
        if "zsh" in shell:
            config_file = home / ".zshrc"
        elif "bash" in shell:
            config_file = home / ".bashrc"
        
        if config_file:
            print(f"Attempting to add to {config_file}...")
            export_cmd = 'export PATH="$HOME/.local/bin:$PATH"'
            
            # Check if already present
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
                    print(f"Please restart your shell or run 'source {config_file}' to apply changes.")
                except Exception as e:
                    print(f"Could not automatically update {config_file}: {e}")
            else:
                print(f"Configuration already exists in {config_file} but is not active. Please restart your shell.")
        else:
            print(
                f"You may need to add it to run '{links[0]}' or '{links[1]}' from the terminal."
            )

    print(
        "\nInstallation complete! You should now see 'SolarViewer' in your application menu."
    )
    return True


if __name__ == "__main__":
    install_desktop_integration()

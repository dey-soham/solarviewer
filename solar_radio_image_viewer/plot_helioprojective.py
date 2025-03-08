#!/usr/bin/env python
"""
Simple script to load and plot a helioprojective FITS file.
"""

import sys
import matplotlib.pyplot as plt
import numpy as np

try:
    import sunpy.map
    from sunpy.coordinates import Helioprojective
    import astropy.units as u
except ImportError:
    print("Error: sunpy is required to run this script.")
    print("Please install it with: pip install sunpy[all]")
    sys.exit(1)


def plot_helioprojective_fits(
    fits_file, output_image=None, cmap="viridis", show_limb=True, show_grid=True
):
    """
    Load and plot a helioprojective FITS file.

    Parameters
    ----------
    fits_file : str
        Path to the helioprojective FITS file.
    output_image : str, optional
        Path to save the output image. If None, the image will be displayed.
    cmap : str, optional
        Colormap to use for the plot. Default is 'viridis'.
    show_limb : bool, optional
        Whether to show the solar limb. Default is True.
    show_grid : bool, optional
        Whether to show coordinate grid lines. Default is True.
    """
    try:
        # Load the helioprojective map
        hpc_map = sunpy.map.Map(fits_file)

        # Fix the wavelength unit if it's not recognized
        try:
            wavelength = hpc_map.wavelength
        except ValueError:
            # Try to fix the unit in the metadata
            if "waveunit" in hpc_map.meta:
                waveunit = hpc_map.meta["waveunit"].strip().lower()
                if waveunit == "mhz":
                    hpc_map.meta["waveunit"] = "MHz"
                elif waveunit == "ghz":
                    hpc_map.meta["waveunit"] = "GHz"
                elif waveunit == "khz":
                    hpc_map.meta["waveunit"] = "kHz"
                elif waveunit == "hz":
                    hpc_map.meta["waveunit"] = "Hz"

        # Create a figure
        fig = plt.figure(figsize=(10, 10))

        # Create a subplot with the helioprojective projection
        ax = fig.add_subplot(111, projection=hpc_map)

        # Plot the map
        im = hpc_map.plot(axes=ax, cmap=cmap, title=False)

        # Add a colorbar
        plt.colorbar(im, ax=ax, label="Intensity")

        # Add grid lines if requested
        if show_grid:
            ax.grid(color="white", linestyle="--", alpha=0.5)

        # Add the solar limb if requested
        if show_limb:
            hpc_map.draw_limb(axes=ax, color="white", alpha=0.5, linewidth=1.5)

        # Set title with observation information
        try:
            wavelength_str = f"{hpc_map.wavelength.value:.2f} {hpc_map.wavelength.unit}"
        except (ValueError, AttributeError):
            # If wavelength is not available, use the raw value from the header
            if "wavelnth" in hpc_map.meta and "waveunit" in hpc_map.meta:
                wavelength_str = (
                    f"{hpc_map.meta['wavelnth']} {hpc_map.meta['waveunit']}"
                )
            else:
                wavelength_str = "Unknown"

        title = f"Helioprojective Map\n{wavelength_str} - {hpc_map.date.strftime('%Y-%m-%d %H:%M:%S')}"
        ax.set_title(title, fontsize=14)

        # Add coordinate labels
        ax.set_xlabel("Helioprojective Longitude (arcsec)", fontsize=12)
        ax.set_ylabel("Helioprojective Latitude (arcsec)", fontsize=12)

        # Adjust layout
        plt.tight_layout()

        # Save or display the image
        if output_image:
            plt.savefig(output_image, dpi=300, bbox_inches="tight")
            print(f"Image saved to {output_image}")
        else:
            plt.show()

    except Exception as e:
        print(f"Error: {str(e)}")
        import traceback

        traceback.print_exc()
        return False

    return True


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Plot a helioprojective FITS file")
    parser.add_argument("fits_file", help="Path to the helioprojective FITS file")
    parser.add_argument(
        "--output",
        help="Path to save the output image (if not provided, the image will be displayed)",
    )
    parser.add_argument(
        "--cmap", default="viridis", help="Colormap to use (default: viridis)"
    )
    parser.add_argument(
        "--no-limb", action="store_true", help="Do not show the solar limb"
    )
    parser.add_argument(
        "--no-grid", action="store_true", help="Do not show coordinate grid lines"
    )

    args = parser.parse_args()

    plot_helioprojective_fits(
        args.fits_file,
        output_image=args.output,
        cmap=args.cmap,
        show_limb=not args.no_limb,
        show_grid=not args.no_grid,
    )

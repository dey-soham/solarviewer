import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from astropy.io import fits
import os
import math
from astropy.time import Time
import colorsys


def load_lofar_logo(fits_path, center_size=70):
    """Load and process the LOFAR FITS file as a logo."""
    try:
        print(f"Attempting to load LOFAR image from: {fits_path}")
        with fits.open(fits_path) as hdul:
            data = hdul[0].data
            print(f"LOFAR data shape: {data.shape}")
            print(f"LOFAR data type: {data.dtype}")

            # Handle multi-dimensional data
            if data.ndim > 2:
                print(f"[Debug] Found {data.ndim}D data, extracting 2D slice")
                data = data[0, 0]  # Take first channel/frame if multi-dimensional

            # data = np.rot90(data, k=1)
            # data = np.flip(data, axis=0)

            rms = np.std(data[0:100, 0:100])
            print(f"[Debug] RMS: {rms}")
            mask = data > rms * 10
            data[~mask] = 0

            # transform like origin='lower'
            data = np.flip(data, axis=0)

            # Get the center coordinates
            height, width = data.shape
            print(f"[Debug] Image dimensions: {width}x{height}")
            center_y, center_x = height // 2, width // 2
            half_size = center_size // 2

            # Extract the central region
            center_data = data[
                center_y - half_size : center_y + half_size,
                center_x - half_size : center_x + half_size,
            ]
            print(f"Extracted center region shape: {center_data.shape}")

            # Scale the data to max value of 255
            max_val = np.max(center_data)
            print(f"Original max value: {max_val}")
            normalized = (center_data / max_val * 255).astype(np.uint8)
            print(
                f"Normalized data range: {np.min(normalized)} to {np.max(normalized)}"
            )

            # Create PIL Image
            img = Image.fromarray(normalized)
            print("Successfully created PIL Image from LOFAR data")
            return img
    except Exception as e:
        print(f"Error loading LOFAR image: {e}")
        import traceback

        traceback.print_exc()
        return None


def create_gradient_background(width, height, color1=(25, 0, 51), color2=(51, 0, 102)):
    """Creates a vertical gradient background."""
    background = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(background)

    for y in range(height):
        r = int(color1[0] + (color2[0] - color1[0]) * y / height)
        g = int(color1[1] + (color2[1] - color1[1]) * y / height)
        b = int(color1[2] + (color2[2] - color1[2]) * y / height)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    return background


def create_glow_effect(img, radius=10):
    """Creates a glowing effect by blurring and overlaying."""
    glow = img.copy()
    for _ in range(3):  # Apply multiple times for stronger effect
        glow = glow.filter(ImageFilter.GaussianBlur(radius))
    return Image.blend(img, glow, 0.5)


def create_splash_fits(
    output_path="solar_radio_image_viewer/assets/splash.fits",
    lofar_path="/home/soham/solarviewer/test_data/LOFAR_HBA_noisestorm.fits",
):
    """
    Creates a FITS file with an enhanced splash screen image.
    The image contains the LOFAR logo and text "Solar Radio Image Viewer" with fancy visual effects.
    """
    width, height = 1024, 1024
    text_color = (255, 255, 255)  # White
    glow_color = (255, 165, 0)  # Orange for glow

    # Create base array for the final image
    final_array = np.zeros((height, width), dtype=np.float32)

    # Load and process LOFAR logo
    print("\nProcessing LOFAR logo...")
    lofar_img = load_lofar_logo(lofar_path)
    if lofar_img:
        print("LOFAR image loaded successfully")
        # Size for the logo display
        display_size = 600
        lofar_img = lofar_img.resize(
            (display_size, display_size), Image.Resampling.LANCZOS
        )

        # Convert to numpy array and enhance contrast
        lofar_array = np.array(lofar_img)
        # p2, p98 = np.percentile(lofar_array, (2, 98))
        # lofar_array = np.clip(lofar_array, p2, p98)
        # lofar_array = (lofar_array - p2) / (p98 - p2) * 255

        # Position for the logo
        logo_x = (width - display_size) // 2
        logo_y = height // 3 - display_size // 2 + 75

        # Insert LOFAR data into final array
        final_array[logo_y : logo_y + display_size, logo_x : logo_x + display_size] = (
            lofar_array
        )
        print(f"[Debug] Inserted LOFAR data at position ({logo_x}, {logo_y})")
        print(
            f"[Debug] LOFAR data range in final array: {np.min(lofar_array)} to {np.max(lofar_array)}"
        )

    # Create a PIL image for text rendering
    text_img = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(text_img)

    # --- Draw Text ---
    try:
        font_path = "arial.ttf"
        title_font_size = 80
        subtitle_font_size = 30
        title_font = ImageFont.truetype(font_path, title_font_size)
        subtitle_font = ImageFont.truetype(font_path, subtitle_font_size)
    except IOError:
        print(f"Font {font_path} not found, using default PIL font.")
        title_font = ImageFont.load_default()
        subtitle_font = ImageFont.load_default()

    # Main title
    text = "Solar Radio Image Viewer"
    if hasattr(title_font, "getbbox"):
        text_bbox = draw.textbbox((0, 0), text, font=title_font)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]
    else:
        text_width, text_height = draw.textsize(text, font=title_font)

    text_x = (width - text_width) // 2
    # text_y = height // 2  # Positioned below the logo
    text_y = height - height // 3  # Positioned below the logo

    # Draw main text
    draw.text((text_x, text_y), text, font=title_font, fill=255)

    # Add subtitle
    subtitle = "Visualizing the Sun's Radio Emissions"
    if hasattr(subtitle_font, "getbbox"):
        subtitle_bbox = draw.textbbox((0, 0), subtitle, font=subtitle_font)
        subtitle_width = subtitle_bbox[2] - subtitle_bbox[0]
    else:
        subtitle_width, _ = draw.textsize(subtitle, font=subtitle_font)

    subtitle_x = (width - subtitle_width) // 2
    subtitle_y = text_y + text_height + 20
    draw.text((subtitle_x, subtitle_y), subtitle, font=subtitle_font, fill=200)

    # Convert text image to array and add to final array
    text_array = np.array(text_img).astype(np.float32)

    # Add text where LOFAR image is not present (where final_array is 0)
    text_mask = final_array == 0
    final_array[text_mask] = text_array[text_mask]

    print(f"[Debug] Final array stats:")
    print(f"[Debug] Min value: {np.min(final_array)}")
    print(f"[Debug] Max value: {np.max(final_array)}")
    print(f"[Debug] Mean value: {np.mean(final_array)}")
    print(f"[Debug] Number of non-zero pixels: {np.count_nonzero(final_array)}")

    # Rotate the array for FITS orientation
    final_array = np.rot90(
        final_array, k=3
    )  # Rotate 270 degrees clockwise instead of 90

    # --- Create FITS file ---
    hdu = fits.PrimaryHDU(final_array)
    hdu.header["SOFTWARE"] = "Solar Radio Image Viewer Splash Generator"
    hdu.header["CONTENT"] = "Enhanced Splash Screen with LOFAR solar image"
    hdu.header["AUTHOR"] = "Soham Dey"
    hdu.header["DATE-OBS"] = Time.now().fits
    hdu.header["BUNIT"] = "Jy/beam"
    hdu.header["TELESCOP"] = "Solar Radio Image Viewer"

    # Ensure output directory exists
    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f"Created directory: {output_dir}")

    try:
        hdu.writeto(output_path, overwrite=True)
        print(f"Enhanced splash screen FITS file created successfully at {output_path}")
    except Exception as e:
        print(f"Error writing FITS file: {e}")


if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    output_fits_path = os.path.join(
        project_root, "solar_radio_image_viewer", "assets", "splash.fits"
    )

    assets_dir = os.path.join(project_root, "solar_radio_image_viewer", "assets")
    if not os.path.exists(assets_dir):
        os.makedirs(assets_dir)
        print(f"Created assets directory: {assets_dir}")

    create_splash_fits(output_path=output_fits_path)

import drms, time, os, glob, warnings
import sunpy
from parfive import Downloader
from sunpy.map import Map

# from sunpy.instr.aia import aiaprep  # This import is no longer available in sunpy 6.0.5
# In newer versions of sunpy, aiaprep has been moved to the aiapy package
try:
    from aiapy.calibrate import register, update_pointing, correct_degradation
    from aiapy.psf import deconvolve as aia_deconvolve

    HAS_AIAPY = True
except ImportError:
    # If aiapy is not installed, we'll provide a helpful message
    HAS_AIAPY = False
    # Only print warning once when module is first loaded
    import sys
    if not hasattr(sys, '_aiapy_warning_shown'):
        print(
            "\n" + "="*60 + "\n"
            "WARNING: aiapy package not found.\n"
            "Level 1.5 calibration (pointing, degradation correction) will NOT be applied.\n"
            "To install: pip install aiapy\n"
            + "="*60 + "\n"
        )
        sys._aiapy_warning_shown = True
from astropy.io import fits
from datetime import datetime, timedelta
import astropy.units as u  # Import astropy units for use throughout the code

# Configure SunPy download timeout (300 seconds = 5 minutes)
# This helps with large batch downloads that may take longer
try:
    sunpy.config.set("downloads", "timeout", 300)
except Exception:
    pass  # Ignore if config doesn't support this setting

# Default download settings for Fido
DEFAULT_MAX_CONN = 4  # Maximum simultaneous connections
DEFAULT_MAX_SPLITS = 5  # Maximum splits per file
DEFAULT_MAX_RETRIES = 5  # Number of retry attempts for failed downloads (increased from 3)
DEFAULT_TIMEOUT = 120  # Timeout in seconds per file download


def robust_fido_fetch(
    result,
    output_dir,
    max_conn=DEFAULT_MAX_CONN,
    max_splits=DEFAULT_MAX_SPLITS,
    max_retries=DEFAULT_MAX_RETRIES,
    timeout=DEFAULT_TIMEOUT,
    progress=True,
):
    """
    Robust Fido.fetch wrapper with retry logic, timeout, and configurable connection settings.

    This function handles common download issues like timeouts and partial downloads
    by using a custom parfive Downloader with better settings and automatically
    retrying failed downloads.

    Args:
        result: Fido search result to download
        output_dir (str): Directory to save downloaded files
        max_conn (int): Maximum simultaneous connections (default: 4)
        max_splits (int): Maximum splits per file for parallel download (default: 5)
        max_retries (int): Number of retry attempts for failed downloads (default: 5)
        timeout (int): Timeout in seconds for each file download (default: 120)
        progress (bool): Show download progress bar (default: True)

    Returns:
        parfive.Results: Downloaded file results
    """
    from sunpy.net import Fido
    import aiohttp
    
    # Create timeout configuration for aiohttp (used by parfive internally)
    timeout_config = aiohttp.ClientTimeout(total=timeout, connect=30)

    # Create a custom downloader with better settings for bulk downloads
    # Note: parfive uses aiohttp, timeout is passed via overwrite parameter in Fido.fetch
    downloader = Downloader(max_conn=max_conn, max_splits=max_splits, progress=progress)

    print(f"Starting download with settings: max_conn={max_conn}, retries={max_retries}, timeout={timeout}s")

    # Initial fetch attempt
    downloaded = Fido.fetch(result, path=output_dir + "/{file}", downloader=downloader)

    # Retry failed downloads
    retry_count = 0
    while downloaded.errors and retry_count < max_retries:
        retry_count += 1
        failed_count = len(downloaded.errors)
        print(
            f"\nRetrying {failed_count} failed downloads (attempt {retry_count}/{max_retries})..."
        )
        
        # Add small delay between retries to help with rate limiting
        import time as time_module
        time_module.sleep(2)

        # Create a fresh downloader for retry with reduced connections
        retry_downloader = Downloader(
            max_conn=max(1, max_conn // 2),  # Reduce connections on retry
            max_splits=max_splits, 
            progress=progress
        )
        downloaded = Fido.fetch(downloaded, downloader=retry_downloader)

    # Report final status
    if downloaded.errors:
        print(
            f"\n" + "="*60 + "\n"
            f"WARNING: {len(downloaded.errors)} files failed to download after {max_retries} retries\n"
            + "="*60
        )
        for error in downloaded.errors[:5]:  # Show first 5 errors
            print(f"  - {error}")
        if len(downloaded.errors) > 5:
            print(f"  ... and {len(downloaded.errors) - 5} more errors")
        print("\nTroubleshooting tips:")
        print("  - Check VSO status: https://sdac.virtualsolar.org/cgi/health_report")
        print("  - Try a smaller time range")
        print("  - The data provider may be temporarily unavailable")
    else:
        print(f"\nSuccessfully downloaded {len(downloaded)} files.")

    return downloaded


"""
Solar Data Download and Calibration Module

This module provides functionality to download and process data from various solar observatories:
- SDO/AIA (Atmospheric Imaging Assembly)
- SDO/HMI (Helioseismic and Magnetic Imager)
- IRIS (Interface Region Imaging Spectrograph)
- SOHO (Solar and Heliospheric Observatory)

It can be used as a standalone script or imported as a module in other Python scripts.

Functions:
    - get_key: Find a key in a dictionary by its value
    - aiaexport: Generate an export command for AIA data
    - download_aia: Download and process AIA data for a given time range
    - get_time_list: Generate a list of timestamps within a given range
    - download_aia_with_fido: Download AIA data using SunPy's Fido client
    - download_hmi: Download and process HMI data
    - download_hmi_with_fido: Download HMI data using Fido
    - download_iris: Download IRIS data
    - download_soho: Download SOHO data (EIT, LASCO, MDI)
    - download_goes_suvi: Download GOES SUVI data
    - download_stereo: Download STEREO SECCHI data
    - download_gong: Download GONG magnetogram data

Notes:
    - This module uses the DRMS client to access JSOC data for SDO instruments
    - An email address is technically optional for small requests but recommended
    - For large requests, an email address is required for notification when data is ready
    - Alternative download methods include using the SunPy Fido client or directly 
      downloading from https://sdo.gsfc.nasa.gov/data/
"""

# AIA Series Constants
AIA_SERIES = {
    "12s": "aia.lev1_euv_12s",
    "24s": "aia.lev1_uv_24s",
    "1h": "aia.lev1_vis_1h",
}

# HMI Series Constants
# Note: For Fido downloads, M_ and B_ series both use LOS_magnetic_field physobs
HMI_SERIES = {
    "45s": "hmi.M_45s",  # LOS magnetogram (45s cadence)
    "720s": "hmi.M_720s",  # LOS magnetogram (12 min cadence)
    "B_45s": "hmi.B_45s",  # Line-of-sight magnetogram (same as M_)
    "B_720s": "hmi.B_720s",  # Line-of-sight magnetogram (12 min)
    "Ic_45s": "hmi.Ic_45s",  # Continuum intensity
    "Ic_720s": "hmi.Ic_720s",  # Continuum intensity (12 min)
    "V_45s": "hmi.V_45s",  # LOS velocity
    "V_720s": "hmi.V_720s",  # LOS velocity (12 min)
}

# Wavelength Options by Cadence
WAVELENGTHS = {
    "12s": ["94", "131", "171", "193", "211", "304", "335"],
    "24s": ["1600", "1700"],
    "1h": ["4500"],
}


def get_key(val, my_dict):
    """
    Find a key in a dictionary by its value.

    Args:
        val: The value to search for
        my_dict: The dictionary to search in

    Returns:
        The key corresponding to the value, or None if not found
    """
    for key, value in my_dict.items():
        if val == value:
            return key
    return None


def aiaexport(wavelength, cadence, start_time, end_time):
    """
    Generate an export command for AIA data.

    Args:
        wavelength (str): AIA wavelength (e.g., '171', '1600')
        cadence (str): Time cadence ('12s', '24s', or '1h')
        start_time (str): Start time in 'YYYY.MM.DD_HH:MM:SS' format
        end_time (str): End time in 'YYYY.MM.DD_HH:MM:SS' format

    Returns:
        str: The export command string or None if invalid parameters
    """
    # Validate wavelength for the given cadence
    if cadence not in AIA_SERIES:
        print(f"Error: Invalid cadence '{cadence}'. Use '12s', '24s', or '1h'.")
        return None

    if wavelength not in WAVELENGTHS[cadence]:
        print(f"Error: {wavelength}Å image not available for {cadence} cadence")
        return None

    # Calculate duration between start and end times
    try:
        start_dt = datetime.strptime(start_time.replace("_", " "), "%Y.%m.%d %H:%M:%S")
        end_dt = datetime.strptime(end_time.replace("_", " "), "%Y.%m.%d %H:%M:%S")
        duration_seconds = (end_dt - start_dt).total_seconds()

        # Convert to a duration string (e.g., "1h", "30m", "3600s")
        if duration_seconds >= 3600:
            duration_str = f"{int(duration_seconds / 3600)}h"
        elif duration_seconds >= 60:
            duration_str = f"{int(duration_seconds / 60)}m"
        else:
            duration_str = f"{int(duration_seconds)}s"
    except (ValueError, TypeError):
        # Default to 1 hour if parsing fails
        duration_str = "1h"

    # Format time for the export command
    time_utc = start_time + "_UTC"

    # Create export command with calculated duration
    export_cmd = (
        f"{AIA_SERIES[cadence]}[{time_utc}/{duration_str}@{cadence}][{wavelength}]"
    )
    return export_cmd


def get_time_list(start_time, end_time, interval_seconds=0.5):
    """
    Generate a list of timestamps within a given range.

    Args:
        start_time (str): Start time in 'YYYY.MM.DD HH:MM:SS' format
        end_time (str): End time in 'YYYY.MM.DD HH:MM:SS' format
        interval_seconds (float): Time interval between timestamps in seconds

    Returns:
        list: List of timestamps in 'HH:MM:SS' format
    """
    stt = datetime.strptime(start_time, "%Y.%m.%d %H:%M:%S")
    ett = datetime.strptime(end_time, "%Y.%m.%d %H:%M:%S")
    time_list = []

    while stt <= ett:
        tm = datetime.strftime(stt, "%Y.%m.%d %H:%M:%S").split(" ")[-1]
        time_list.append(tm)
        stt += timedelta(seconds=interval_seconds)

    return time_list


def download_aia(
    wavelength,
    cadence,
    start_time,
    end_time,
    output_dir,
    email=None,
    interval_seconds=0.5,
    skip_calibration=False,
):
    """
    Download and process AIA data for a given time range.

    Args:
        wavelength (str): AIA wavelength (e.g., '171', '1600')
        cadence (str): Time cadence ('12s', '24s', or '1h')
        start_time (str): Start time in 'YYYY.MM.DD HH:MM:SS' format
        end_time (str): End time in 'YYYY.MM.DD HH:MM:SS' format
        output_dir (str): Directory to save downloaded files
        email (str, optional): Email for DRMS client. Recommended for reliability.
                               Small requests may work without an email, but large requests
                               require an email for notification when data is ready.
        interval_seconds (float, optional): Time interval between images
        skip_calibration (bool, optional): If True, skip Level 1.5 calibration even if aiapy is available

    Returns:
        list: Paths to downloaded Level 1.5 FITS files (or Level 1.0 if calibration is skipped/unavailable)

    Notes:
        Alternative download methods if you don't want to provide an email:
        1. Use SunPy's Fido client (import sunpy.net; from sunpy.net import Fido, attrs)
        2. Download directly from https://sdo.gsfc.nasa.gov/data/
    """
    # Check if we can perform calibration
    can_calibrate = HAS_AIAPY and not skip_calibration

    # Create output directory if it doesn't exist
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)

    # Create temp directory for downloads
    temp_dir = os.path.join(output_dir, "temp")
    if not os.path.isdir(temp_dir):
        os.makedirs(temp_dir)

    # Initialize DRMS client
    # DRMS 0.9.0+ requires an email for all export requests
    if email is None:
        print("Error: Email is required for DRMS downloads.")
        print("Please provide an email address with the --email option,")
        print("or use --use-fido for downloads without email requirement.")
        return []

    client = drms.Client(email=email)

    # Format start and end times for export command - YYYY.MM.DD_HH:MM:SS format required by DRMS
    start_time_fmt = start_time.replace(" ", "_")
    end_time_fmt = end_time.replace(" ", "_")

    # Create export command with proper duration
    export_cmd = aiaexport(
        wavelength=wavelength,
        cadence=cadence,
        start_time=start_time_fmt,
        end_time=end_time_fmt,
    )
    if export_cmd is None:
        return []

    # Request data export
    print(f"Requesting data export with command: {export_cmd}")
    try:
        response = client.export(export_cmd, method="url", protocol="fits")

        # Wait for export to be ready
        print("Waiting for JSOC export to be ready...")
        response.wait()

        if response.status != 0:
            print(f"Export failed with status {response.status}")
            print("Try using the --use-fido option as an alternative download method.")
            return []

        # Get list of files to download
        export_data = response.data
        if export_data is None or len(export_data) == 0:
            print("No data returned from JSOC export.")
            print("Try using the --use-fido option as an alternative download method.")
            return []

        print(f"Export ready. Found {len(export_data)} files. Downloading...")

    except Exception as e:
        print(f"Error during data export: {str(e)}")
        print("Try using the --use-fido option as an alternative download method.")
        return []

    # Filter to only download image files (not spikes)
    # Export returns both .image_lev1.fits and .spikes.fits files
    image_data = export_data[export_data["filename"].str.contains("image_lev1")]

    if len(image_data) == 0:
        print("No image files found in export (only spike files).")
        return []

    print(
        f"Downloading {len(image_data)} image files (filtered from {len(export_data)} total)..."
    )

    # Download all image files from the export
    downloaded_files = []
    for idx in image_data.index:
        try:
            # Get the actual filename from the export data
            original_filename = export_data.loc[idx, "filename"]

            # Define output files for Level 1.0 and Level 1.5
            level1_file = os.path.join(output_dir, original_filename)
            level1_5_file = os.path.join(
                output_dir, original_filename.replace(".fits", "_lev1.5.fits")
            )

            # Skip if already processed
            output_file = level1_5_file if can_calibrate else level1_file
            if os.path.isfile(output_file):
                downloaded_files.append(output_file)
                continue

            # Download the file using keyword argument for index
            response.download(temp_dir, index=idx)
            temp_files = glob.glob(os.path.join(temp_dir, "*.fits"))
            if not temp_files:
                print(f"Warning: No file downloaded for index {idx}")
                continue

            temp_file = temp_files[0]
            os.rename(temp_file, level1_file)
            print(f"Downloaded: {os.path.basename(level1_file)}")

            if can_calibrate:
                try:
                    print(f"Processing {os.path.basename(level1_file)} to Level 1.5...")
                    aia_map = Map(level1_file)
                    warnings.filterwarnings("ignore")
                    lev1_5map = register(aia_map)
                    lev1_5map.save(level1_5_file)
                    os.remove(level1_file)
                    print(f"Processed: {os.path.basename(level1_5_file)}")
                    output_file = level1_5_file
                except Exception as e:
                    print(f"Error during Level 1.5 calibration: {str(e)}")
                    print(
                        f"Using Level 1.0 file instead: {os.path.basename(level1_file)}"
                    )
                    output_file = level1_file
            else:
                print(f"Downloaded Level 1.0 file: {os.path.basename(level1_file)}")
                if not HAS_AIAPY:
                    print("For Level 1.5 calibration, install aiapy: pip install aiapy")
                output_file = level1_file

            downloaded_files.append(output_file)

        except Exception as e:
            print(f"Error downloading file {idx}: {str(e)}")
            continue

    # Clean up temp directory
    if os.path.exists(temp_dir):
        for file in glob.glob(os.path.join(temp_dir, "*")):
            os.remove(file)
        os.rmdir(temp_dir)

    return downloaded_files


def main():
    """
    Main function to run when the script is executed directly.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Download and process data from solar observatories",
        epilog="""
Instruments and typical parameters:
  - AIA: --instrument aia --wavelength 171 --cadence 12s
  - HMI: --instrument hmi --series 45s (or B_45s, Ic_720s, etc.)
  - IRIS: --instrument iris --obs-type SJI --wavelength 1400
  - SOHO/EIT: --instrument soho --soho-instrument EIT --wavelength 195
  - SOHO/LASCO: --instrument soho --soho-instrument LASCO --detector C2
        
Troubleshooting:
  - If you get 'Bad record-set subset specification' errors, try using --use-fido
  - For 'email required' errors, either provide --email or use --use-fido
  - If downloads fail, try a smaller time range between start and end times
        """,
    )

    # General arguments
    parser.add_argument(
        "--instrument",
        type=str,
        default="aia",
        choices=["aia", "hmi", "iris", "soho"],
        help="Observatory instrument to download data from",
    )
    parser.add_argument(
        "--start-time",
        type=str,
        required=True,
        help="Start time in YYYY.MM.DD HH:MM:SS format",
    )
    parser.add_argument(
        "--end-time",
        type=str,
        required=True,
        help="End time in YYYY.MM.DD HH:MM:SS format",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="./solar_data",
        help="Directory to save downloaded files",
    )
    parser.add_argument(
        "--email",
        type=str,
        help="Email for DRMS client. Recommended for reliability. Required for large requests.",
    )
    parser.add_argument(
        "--skip-calibration",
        action="store_true",
        help="Skip calibration steps even if available",
    )
    parser.add_argument(
        "--use-fido",
        action="store_true",
        help="Use SunPy's Fido client instead of DRMS (no email required)",
    )

    # AIA-specific arguments
    parser.add_argument(
        "--wavelength", type=str, help="Wavelength or channel (instrument-specific)"
    )
    parser.add_argument(
        "--cadence",
        type=str,
        default="12s",
        help="Time cadence for AIA (12s, 24s, or 1h)",
    )

    # HMI-specific arguments
    parser.add_argument(
        "--series",
        type=str,
        help="Series for HMI (45s, 720s, B_45s, B_720s, Ic_45s, Ic_720s)",
    )

    # IRIS-specific arguments
    parser.add_argument(
        "--obs-type",
        type=str,
        default="SJI",
        help="IRIS observation type (SJI or raster)",
    )

    # SOHO-specific arguments
    parser.add_argument(
        "--soho-instrument",
        type=str,
        choices=["EIT", "LASCO", "MDI"],
        help="SOHO instrument (EIT, LASCO, or MDI)",
    )
    parser.add_argument(
        "--detector", type=str, help="Detector for SOHO/LASCO (C1, C2, C3)"
    )

    args = parser.parse_args()

    try:
        # Handle downloading based on selected instrument
        if args.instrument.lower() == "aia":
            # AIA data download
            if args.use_fido:
                if not args.wavelength:
                    print("Error: --wavelength is required for AIA data")
                    return 1

                downloaded_files = download_aia_with_fido(
                    wavelength=args.wavelength,
                    start_time=args.start_time,
                    end_time=args.end_time,
                    output_dir=args.output_dir,
                    skip_calibration=args.skip_calibration,
                )
            else:
                if not args.wavelength:
                    print("Error: --wavelength is required for AIA data")
                    return 1

                downloaded_files = download_aia(
                    wavelength=args.wavelength,
                    cadence=args.cadence,
                    start_time=args.start_time,
                    end_time=args.end_time,
                    output_dir=args.output_dir,
                    email=args.email,
                    skip_calibration=args.skip_calibration,
                )

        elif args.instrument.lower() == "hmi":
            # HMI data download
            if not args.series:
                print("Error: --series is required for HMI data")
                return 1

            if args.use_fido:
                downloaded_files = download_hmi_with_fido(
                    series=args.series,
                    start_time=args.start_time,
                    end_time=args.end_time,
                    output_dir=args.output_dir,
                    skip_calibration=args.skip_calibration,
                )
            else:
                downloaded_files = download_hmi(
                    series=args.series,
                    start_time=args.start_time,
                    end_time=args.end_time,
                    output_dir=args.output_dir,
                    email=args.email,
                    skip_calibration=args.skip_calibration,
                )

        elif args.instrument.lower() == "iris":
            # IRIS data download (only Fido is supported)
            downloaded_files = download_iris(
                start_time=args.start_time,
                end_time=args.end_time,
                output_dir=args.output_dir,
                obs_type=args.obs_type,
                wavelength=args.wavelength,
                skip_calibration=args.skip_calibration,
            )

        elif args.instrument.lower() == "soho":
            # SOHO data download (only Fido is supported)
            if not args.soho_instrument:
                print("Error: --soho-instrument is required for SOHO data")
                return 1

            downloaded_files = download_soho(
                instrument=args.soho_instrument,
                start_time=args.start_time,
                end_time=args.end_time,
                output_dir=args.output_dir,
                wavelength=args.wavelength,
                detector=args.detector,
                skip_calibration=args.skip_calibration,
            )

        else:
            print(f"Error: Unsupported instrument: {args.instrument}")
            return 1

        # Report download results
        instrument_name = args.instrument.upper()
        if args.instrument.lower() == "soho" and args.soho_instrument:
            instrument_name = f"SOHO/{args.soho_instrument}"

        print(
            f"Download complete. Downloaded {len(downloaded_files)} {instrument_name} files to {args.output_dir}"
        )

    except Exception as e:
        print(f"Error: {str(e)}")
        print("\nTroubleshooting tips:")
        print("1. Try using the --use-fido option if you're having issues with DRMS")
        print("2. Make sure your time format is correct (YYYY.MM.DD HH:MM:SS)")
        print("3. Try a smaller time range between start and end times")
        print("4. If using DRMS, consider providing an email with --email")
        print("5. If using Fido, ensure you have the latest sunpy version installed")
        print("   You can update with: pip install --upgrade sunpy")
        return 1

    return 0


def download_aia_with_fido(
    wavelength,
    start_time,
    end_time,
    output_dir,
    skip_calibration=False,
    apply_psf=False,
    apply_degradation=True,
    apply_exposure_norm=True,
):
    """
    Alternative download function using SunPy's Fido client which doesn't require an email.

    Args:
        wavelength (str): AIA wavelength (e.g., '171', '1600')
        start_time (str): Start time in 'YYYY.MM.DD HH:MM:SS' format
        end_time (str): End time in 'YYYY.MM.DD HH:MM:SS' format
        output_dir (str): Directory to save downloaded files
        skip_calibration (bool, optional): If True, skip Level 1.5 calibration
        apply_psf (bool, optional): If True, apply PSF deconvolution (slow, ~30-60s/image)
        apply_degradation (bool, optional): If True, apply time-dependent degradation correction
        apply_exposure_norm (bool, optional): If True, normalize by exposure time

    Returns:
        list: Paths to downloaded Level 1.5 FITS files (or Level 1.0 if calibration is skipped/unavailable)
    """
    try:
        import sunpy.net
        from sunpy.net import Fido, attrs as a
    except ImportError:
        print("Error: SunPy not installed or not properly configured.")
        return []

    # Create output directory if it doesn't exist
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)

    # Check if we can perform calibration
    can_calibrate = HAS_AIAPY and not skip_calibration

    # Parse the time strings
    start_dt = datetime.strptime(start_time, "%Y.%m.%d %H:%M:%S")
    end_dt = datetime.strptime(end_time, "%Y.%m.%d %H:%M:%S")

    # Convert wavelength string to integer
    wl_int = int(wavelength)

    print(f"Searching for AIA {wavelength}Å data from {start_time} to {end_time}")
    
    # Show calibration status upfront
    if can_calibrate:
        print(f"  Calibration: ENABLED (aiapy available)")
    else:
        print(f"  Calibration: DISABLED {'(aiapy not installed)' if not HAS_AIAPY else '(skipped by user)'}")

    try:
        # Create the query with correct unit import
        result = Fido.search(
            a.Time(start_dt, end_dt),
            a.Instrument("AIA"),
            a.Wavelength(wl_int * u.angstrom),  # Now using the correct astropy units
        )

        if len(result) == 0 or len(result[0]) == 0:
            print("\n" + "="*60)
            print("NO DATA FOUND")
            print("="*60)
            print(f"  Time range: {start_time} to {end_time}")
            print(f"  Instrument: AIA, Wavelength: {wavelength}Å")
            print("\nPossible causes:")
            print("  - Data may not exist for this time range")
            print("  - VSO provider may be temporarily unavailable")
            print("  - Check VSO status: https://sdac.virtualsolar.org/cgi/health_report")
            if end_dt > datetime.now():
                print("  - NOTE: End time is in the future!")
            return []

        print(f"Found {len(result[0])} files. Downloading...")

        # Download the files with retry logic
        downloaded = robust_fido_fetch(result, output_dir)
    except Exception as e:
        print(f"Error during Fido search/fetch: {str(e)}")
        print("Check your search parameters and ensure sunpy is properly installed.")
        return []

    downloaded_files = []

    # Process the downloaded files if calibration is requested
    for file_path in downloaded:
        file_path = str(file_path)
        # Determine output file names
        base_name = os.path.basename(file_path)
        level1_5_file = os.path.join(
            output_dir, f"{base_name.replace('lev1','lev1_5')}"
        )
        output_file = level1_5_file if can_calibrate else file_path

        if can_calibrate and not os.path.isfile(level1_5_file):
            try:
                # Convert to level 1.5 using aiapy calibration
                # Order: 1) update_pointing, 2) PSF deconvolve, 3) register, 4) correct_degradation
                print(f"Processing {base_name} to Level 1.5...")
                aia_map = Map(file_path)
                warnings.filterwarnings("ignore")

                # Step 1: Update pointing information from JSOC
                try:
                    aia_map = update_pointing(aia_map)
                    print(f"  - Updated pointing for {base_name}")
                except Exception as e:
                    print(f"  - Warning: Could not update pointing: {e}")

                # Step 2: PSF deconvolution (MUST be done on Level 1 before registration)
                if apply_psf:
                    try:
                        print(
                            f"  - Applying PSF deconvolution (this may take 30-60 seconds)..."
                        )
                        aia_map = aia_deconvolve(aia_map, iterations=25)
                        print(f"  - Applied PSF deconvolution (25 iterations)")
                    except Exception as e:
                        print(f"  - Warning: Could not apply PSF deconvolution: {e}")

                # Step 3: Register (rotate, scale to 0.6"/px, center sun)
                lev1_5map = register(aia_map)
                print(f"  - Registered (rotated, scaled, centered)")

                # Step 4: Correct for time-dependent degradation
                if apply_degradation:
                    try:
                        lev1_5map = correct_degradation(lev1_5map)
                        print(f"  - Applied degradation correction")
                    except Exception as e:
                        print(
                            f"  - Warning: Could not apply degradation correction: {e}"
                        )

                # Step 5: Normalize by exposure time
                if apply_exposure_norm and lev1_5map.exposure_time.value > 0:
                    lev1_5map = lev1_5map / lev1_5map.exposure_time
                    print(f"  - Normalized by exposure time")

                lev1_5map.save(level1_5_file)

                print(f"Successfully processed: {os.path.basename(level1_5_file)}")
                output_file = level1_5_file
                os.remove(file_path)
            except Exception as e:
                print(f"Error during Level 1.5 calibration: {str(e)}")
                print(f"Using Level 1.0 file instead: {base_name}")
                output_file = file_path
        else:
            print(f"Downloaded Level 1.0 file: {base_name}")
            if not HAS_AIAPY:
                print("For Level 1.5 calibration, install aiapy: pip install aiapy")

        downloaded_files.append(output_file)

    return downloaded_files


def hmiexport(series, start_time, end_time):
    """
    Generate an export command for HMI data.

    Args:
        series (str): HMI series (e.g., 'M_45s', 'B_45s', 'Ic_720s')
        start_time (str): Start time in 'YYYY.MM.DD_HH:MM:SS' format
        end_time (str): End time in 'YYYY.MM.DD_HH:MM:SS' format

    Returns:
        str: The export command string or None if invalid parameters
    """
    # Validate series
    if series not in HMI_SERIES.keys():
        print(
            f"Error: Invalid HMI series '{series}'. Use one of: {', '.join(HMI_SERIES.keys())}"
        )
        return None

    # Calculate duration between start and end times
    try:
        start_dt = datetime.strptime(start_time.replace("_", " "), "%Y.%m.%d %H:%M:%S")
        end_dt = datetime.strptime(end_time.replace("_", " "), "%Y.%m.%d %H:%M:%S")
        duration_seconds = (end_dt - start_dt).total_seconds()

        # Convert to a duration string (e.g., "1h", "30m", "3600s")
        if duration_seconds >= 3600:
            duration_str = f"{int(duration_seconds / 3600)}h"
        elif duration_seconds >= 60:
            duration_str = f"{int(duration_seconds / 60)}m"
        else:
            duration_str = f"{int(duration_seconds)}s"
    except (ValueError, TypeError):
        # Default to 1 hour if parsing fails
        duration_str = "1h"

    # Format time for the export command
    time_utc = start_time + "_UTC"

    # Create export command with calculated duration
    export_cmd = f"{HMI_SERIES[series]}[{time_utc}/{duration_str}]"
    return export_cmd


def download_hmi(
    series,
    start_time,
    end_time,
    output_dir,
    email=None,
    interval_seconds=45.0,
    skip_calibration=False,
):
    """
    Download and process HMI data for a given time range.

    Args:
        series (str): HMI series type ('45s', '720s', 'B_45s', 'B_720s', 'Ic_45s', 'Ic_720s')
        start_time (str): Start time in 'YYYY.MM.DD HH:MM:SS' format
        end_time (str): End time in 'YYYY.MM.DD HH:MM:SS' format
        output_dir (str): Directory to save downloaded files
        email (str, optional): Email for DRMS client. Recommended for reliability.
                               Small requests may work without an email, but large requests
                               require an email for notification when data is ready.
        interval_seconds (float, optional): Time interval between images.
                                           Default is 45.0 seconds for '45s' series.
                                           For '720s' series, consider using 720.0.
        skip_calibration (bool, optional): If True, skip calibration steps

    Returns:
        list: Paths to downloaded FITS files

    Notes:
        HMI data calibration is different from AIA. For proper scientific analysis,
        consider using the SunPy or additional HMI-specific tools to further calibrate the data.
    """
    # Create output directory if it doesn't exist
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)

    # Create temp directory for downloads
    temp_dir = os.path.join(output_dir, "temp")
    if not os.path.isdir(temp_dir):
        os.makedirs(temp_dir)

    # Initialize DRMS client
    # DRMS 0.9.0+ requires an email for all export requests
    if email is None:
        print("Error: Email is required for DRMS downloads.")
        print("Please provide an email address with the --email option,")
        print("or use --use-fido for downloads without email requirement.")
        return []

    client = drms.Client(email=email)

    # Format start and end times for export command
    start_time_fmt = start_time.replace(" ", "_")
    end_time_fmt = end_time.replace(" ", "_")

    # Create export command with proper duration
    export_cmd = hmiexport(
        series=series, start_time=start_time_fmt, end_time=end_time_fmt
    )
    if export_cmd is None:
        return []

    # Request data export
    print(f"Requesting data export with command: {export_cmd}")
    try:
        response = client.export(export_cmd, method="url", protocol="fits")

        # Wait for export to be ready
        print("Waiting for JSOC export to be ready...")
        response.wait()

        if response.status != 0:
            print(f"Export failed with status {response.status}")
            print("Try using the --use-fido option as an alternative download method.")
            return []

        # Get list of files to download
        export_data = response.data
        if export_data is None or len(export_data) == 0:
            print("No data returned from JSOC export.")
            print("Try using the --use-fido option as an alternative download method.")
            return []

        print(f"Export ready. Found {len(export_data)} files. Downloading...")

    except Exception as e:
        print(f"Error during data export: {str(e)}")
        print("Try using the --use-fido option as an alternative download method.")
        return []

    # Download all files from the export
    downloaded_files = []
    for idx in export_data.index:
        try:
            # Get the actual filename from the export data
            original_filename = export_data.loc[idx, "filename"]
            output_file = os.path.join(output_dir, original_filename)

            # Skip if already exists
            if os.path.isfile(output_file):
                downloaded_files.append(output_file)
                continue

            # Download the file using keyword argument for index
            response.download(temp_dir, index=idx)
            temp_files = glob.glob(os.path.join(temp_dir, "*.fits"))
            if not temp_files:
                print(f"Warning: No file downloaded for index {idx}")
                continue

            temp_file = temp_files[0]
            os.rename(temp_file, output_file)
            print(f"Downloaded: {os.path.basename(output_file)}")
            downloaded_files.append(output_file)

        except Exception as e:
            print(f"Error downloading file {idx}: {str(e)}")
            continue

    # Apply calibration if requested
    if not skip_calibration and downloaded_files:
        calibrated_files = []
        for file_path in downloaded_files:
            try:
                lvl1_map = Map(file_path)
                print(f"Processing {os.path.basename(file_path)} to Level 1.5...")
                lvl1_5_map = update_hmi_pointing(lvl1_map)
                lvl1_5_map_output_file = file_path.replace(".fits", "_lvl1.5.fits")
                lvl1_5_map.save(lvl1_5_map_output_file, filetype="fits")
                print(f"Processed: {os.path.basename(lvl1_5_map_output_file)}")
                os.remove(file_path)
                calibrated_files.append(lvl1_5_map_output_file)
            except Exception as e:
                print(f"Error calibrating {file_path}: {str(e)}")
                calibrated_files.append(file_path)
        downloaded_files = calibrated_files

    # Clean up temp directory
    if os.path.exists(temp_dir):
        for file in glob.glob(os.path.join(temp_dir, "*")):
            os.remove(file)
        os.rmdir(temp_dir)

    return downloaded_files


def download_hmi_with_fido(
    series,
    start_time,
    end_time,
    output_dir,
    skip_calibration=False,
):
    """
    Alternative download function for HMI data using SunPy's Fido client which doesn't require an email.

    Args:
        series (str): HMI series ('45s', '720s', 'B_45s', 'B_720s', 'Ic_45s', 'Ic_720s')
        start_time (str): Start time in 'YYYY.MM.DD HH:MM:SS' format
        end_time (str): End time in 'YYYY.MM.DD HH:MM:SS' format
        output_dir (str): Directory to save downloaded files
        skip_calibration (bool, optional): If True, skip calibration steps

    Returns:
        list: Paths to downloaded FITS files
    """
    try:
        import sunpy.net
        from sunpy.net import Fido, attrs as a
    except ImportError:
        print("Error: SunPy not installed or not properly configured.")
        return []

    # Create output directory if it doesn't exist
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)

    # Parse the time strings
    start_dt = datetime.strptime(start_time, "%Y.%m.%d %H:%M:%S")
    end_dt = datetime.strptime(end_time, "%Y.%m.%d %H:%M:%S")

    # Map series to physobs for Fido queries
    # Note: M_ and B_ series both correspond to LOS_magnetic_field
    # Vector magnetograms are not directly available via simple Fido queries
    physobs = None
    if "V_" in series:
        physobs = "LOS_velocity"
    elif "Ic_" in series:
        physobs = "intensity"  # continuum intensity
    else:  # M_ and B_ series both use LOS magnetic field
        physobs = "LOS_magnetic_field"

    # Determine cadence
    if "45s" in series:
        sample = 45 * u.second
    else:  # 720s
        sample = 720 * u.second

    print(f"Searching for HMI {series} data from {start_time} to {end_time}")

    try:
        # Create the query
        if physobs:
            result = Fido.search(
                a.Time(start_dt, end_dt),
                a.Instrument("HMI"),
                a.Physobs(physobs),
                a.Sample(sample),
            )
        else:
            # Fallback to just instrument and time if physobs mapping is unclear
            result = Fido.search(
                a.Time(start_dt, end_dt), a.Instrument("HMI"), a.Sample(sample)
            )

        if len(result) == 0 or len(result[0]) == 0:
            print("\n" + "="*60)
            print("NO DATA FOUND")
            print("="*60)
            print(f"  Time range: {start_time} to {end_time}")
            print(f"  Instrument: HMI, Series: {series}")
            print("\nPossible causes:")
            print("  - Data may not exist for this time range")
            print("  - VSO provider may be temporarily unavailable")
            print("  - Check VSO status: https://sdac.virtualsolar.org/cgi/health_report")
            if end_dt > datetime.now():
                print("  - NOTE: End time is in the future!")
            return []

        print(f"Found {len(result[0])} files. Downloading...")

        # Download the files with retry logic
        downloaded = robust_fido_fetch(result, output_dir)
    except Exception as e:
        print(f"Error during Fido search/fetch: {str(e)}")
        print("Check your search parameters and ensure sunpy is properly installed.")
        return []

    downloaded_files = [str(file_path) for file_path in downloaded]
    
    # Show calibration status
    print(f"  Calibration: {'ENABLED' if not skip_calibration else 'DISABLED (skipped by user)'}")
    
    if not skip_calibration:
        for file_path in downloaded_files:
            lvl1_map = Map(file_path)
            print(f"Processing {os.path.basename(file_path)} to Level 1.5...")
            lvl1_5_map = update_hmi_pointing(lvl1_map)
            lvl1_5_map_output_file = os.path.join(
                output_dir, f"{os.path.basename(file_path)}_lvl1.5.fits"
            )
            lvl1_5_map.save(lvl1_5_map_output_file, filetype="fits")
            print(f"Successfully processed {os.path.basename(file_path)} to Level 1.5")
            print(f"Deleting {file_path}")
            os.remove(file_path)
    print(f"Successfully downloaded {len(downloaded_files)} HMI files.")
    return downloaded_files


def update_hmi_pointing(hmi_map):
    """
    Calibrate HMI Level-1 data to Level-1.5.

    This function performs comprehensive HMI calibration including:
    1. Roll angle correction (CROTA2) - rotate to solar north up
    2. Re-centering to disk center (update CRPIX)
    3. Plate scale normalization to 0.5 arcsec/pixel (HMI standard)

    For HMI Level-1 data, the basic calibrations (dark subtraction, flat-fielding,
    bad pixel correction) have already been applied by the JSOC pipeline.

    Parameters:
        hmi_map (sunpy.map.Map): Input Level-1 HMI map.

    Returns:
        sunpy.map.Map: A new map with Level-1.5 calibration applied.
    """
    import copy

    # Target plate scale for HMI Level-1.5 (0.5 arcsec/pixel)
    TARGET_SCALE = 0.5  # arcsec/pixel

    # Get current metadata
    meta = copy.deepcopy(hmi_map.meta)

    # Step 1: Get rotation angle and rotate to solar north
    current_crota = float(meta.get("CROTA2", 0.0))

    # Step 2: Calculate re-centering offset
    # CRPIX should be at the center of the sun (CRVAL = 0, 0 in helioprojective)
    # Current sun center in pixels
    current_crpix1 = float(meta.get("CRPIX1", meta.get("NAXIS1", 4096) / 2))
    current_crpix2 = float(meta.get("CRPIX2", meta.get("NAXIS2", 4096) / 2))

    # Target center (middle of image)
    naxis1 = int(meta.get("NAXIS1", 4096))
    naxis2 = int(meta.get("NAXIS2", 4096))
    target_crpix1 = (naxis1 + 1) / 2.0
    target_crpix2 = (naxis2 + 1) / 2.0

    # Step 3: Get current plate scale
    current_cdelt1 = abs(float(meta.get("CDELT1", TARGET_SCALE)))
    current_cdelt2 = abs(float(meta.get("CDELT2", TARGET_SCALE)))

    print(
        f"  - Current CROTA2: {current_crota:.4f}°, CDELT: {current_cdelt1:.4f}×{current_cdelt2:.4f} arcsec/px"
    )
    print(
        f"  - Current CRPIX: ({current_crpix1:.1f}, {current_crpix2:.1f}), Target: ({target_crpix1:.1f}, {target_crpix2:.1f})"
    )

    # Perform rotation to remove roll angle
    if abs(current_crota) > 0.01:  # Only rotate if significant
        rotated_map = hmi_map.rotate(
            angle=-current_crota * u.deg, recenter=True, order=3
        )
        print(f"  - Rotated by {-current_crota:.4f}° to remove roll angle")
    else:
        rotated_map = hmi_map
        print(f"  - No significant rotation needed (CROTA2 = {current_crota:.4f}°)")

    # Check if plate scale normalization is needed
    scale_factor = current_cdelt1 / TARGET_SCALE
    if abs(scale_factor - 1.0) > 0.01:  # Only rescale if different by >1%
        # Calculate new dimensions
        new_naxis1 = int(naxis1 * scale_factor)
        new_naxis2 = int(naxis2 * scale_factor)
        new_dimensions = [new_naxis1, new_naxis2] * u.pixel

        # Resample to target plate scale
        try:
            calibrated_map = rotated_map.resample(new_dimensions)
            print(
                f"  - Rescaled to {TARGET_SCALE} arcsec/px ({naxis1}→{new_naxis1} pixels)"
            )
        except Exception as e:
            print(f"  - Warning: Could not rescale: {e}")
            calibrated_map = rotated_map
    else:
        calibrated_map = rotated_map
        print(f"  - Plate scale already at ~{TARGET_SCALE} arcsec/px")

    print(f"  - HMI Level-1.5 calibration complete")

    return calibrated_map


def download_iris(
    start_time,
    end_time,
    output_dir,
    obs_type="SJI",  # "SJI" for slit-jaw images or "raster" for spectrograph data
    wavelength=None,  # For SJI: 1330, 1400, 2796, 2832
    skip_calibration=False,
):
    """
    Download IRIS (Interface Region Imaging Spectrograph) data for a given time range.

    IRIS data is not available through DRMS/JSOC, so this function uses SunPy's Fido client.

    Args:
        start_time (str): Start time in 'YYYY.MM.DD HH:MM:SS' format
        end_time (str): End time in 'YYYY.MM.DD HH:MM:SS' format
        output_dir (str): Directory to save downloaded files
        obs_type (str): Type of observation - "SJI" for slit-jaw images or "raster" for spectral data
        wavelength (int, optional): For SJI, specify wavelength (1330, 1400, 2796, 2832)
        skip_calibration (bool, optional): If True, skip calibration steps

    Returns:
        list: Paths to downloaded FITS files
    """
    try:
        import sunpy.net
        from sunpy.net import Fido, attrs as a
    except ImportError:
        print("Error: SunPy not installed or not properly configured.")
        return []

    # Create output directory if it doesn't exist
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)

    # Parse the time strings
    start_dt = datetime.strptime(start_time, "%Y.%m.%d %H:%M:%S")
    end_dt = datetime.strptime(end_time, "%Y.%m.%d %H:%M:%S")

    print(f"Searching for IRIS {obs_type} data from {start_time} to {end_time}")

    try:
        # Create the query based on observation type
        if obs_type.lower() == "sji":
            if wavelength is not None:
                # SJI with specific wavelength
                wl = int(wavelength) * u.angstrom
                result = Fido.search(
                    a.Time(start_dt, end_dt),
                    a.Instrument("IRIS"),
                    a.Wavelength(wl),
                )
            else:
                # Any SJI
                result = Fido.search(
                    a.Time(start_dt, end_dt),
                    a.Instrument("IRIS"),
                    a.Physobs("intensity"),
                )
        else:
            # Spectral/raster data
            result = Fido.search(
                a.Time(start_dt, end_dt),
                a.Instrument("IRIS"),
                a.Physobs("intensity"),
                a.Level(2),
            )

        if len(result) == 0 or len(result[0]) == 0:
            print("No data found for the specified parameters.")
            return []

        print(f"Found {len(result[0])} files. Downloading...")

        # Download the files with retry logic
        downloaded = robust_fido_fetch(result, output_dir)
    except Exception as e:
        print(f"Error during Fido search/fetch: {str(e)}")
        print("Check your search parameters and ensure sunpy is properly installed.")
        return []

    downloaded_files = [str(file_path) for file_path in downloaded]

    # Post-process IRIS files for solarviewer compatibility
    # IRIS SJI files are 3D data cubes that need to be converted to 2D FITS
    # IRIS raster files come as tar.gz archives that need extraction
    if not skip_calibration:
        import tarfile

        # First, extract any tar.gz files (raster data comes as archives)
        extracted_files = []
        for file_path in downloaded_files:
            if file_path.endswith(".tar.gz") or file_path.endswith(".tar"):
                try:
                    print(f"Extracting archive: {os.path.basename(file_path)}...")
                    with tarfile.open(file_path, "r:*") as tar:
                        tar.extractall(path=output_dir)
                        for member in tar.getmembers():
                            if member.isfile():
                                extracted_path = os.path.join(output_dir, member.name)
                                extracted_files.append(extracted_path)
                                print(f"  - Extracted: {member.name}")
                    os.remove(file_path)  # Remove the archive after extraction
                except Exception as e:
                    print(f"Warning: Could not extract {file_path}: {e}")
                    extracted_files.append(file_path)
            else:
                extracted_files.append(file_path)

        downloaded_files = extracted_files

        processed_files = []
        for file_path in downloaded_files:
            try:
                base_name = os.path.basename(file_path)
                print(f"Processing {base_name}...")

                with fits.open(file_path) as hdu:
                    data = hdu[0].data
                    header_orig = hdu[0].header.copy()

                    # Check if 3D (time series) and extract all frames
                    if data is not None and data.ndim == 3:
                        n_frames = data.shape[0]
                        print(f"  - Found {n_frames} frames, extracting all...")

                        base_no_ext = (
                            base_name.replace(".fits.gz", "")
                            .replace(".fits", "")
                            .replace(".gz", "")
                        )

                        for frame_idx in range(n_frames):
                            header = header_orig.copy()
                            data_2d = data[frame_idx]

                            # Update header for 2D data
                            header["NAXIS"] = 2
                            header["NAXIS1"] = data_2d.shape[1]
                            header["NAXIS2"] = data_2d.shape[0]
                            header["FRAME"] = frame_idx
                            if "NAXIS3" in header:
                                del header["NAXIS3"]

                            # Add coordinate units if missing
                            if header.get("CUNIT1") is None and header.get("CTYPE1"):
                                header["CUNIT1"] = "arcsec"
                            if header.get("CUNIT2") is None and header.get("CTYPE2"):
                                header["CUNIT2"] = "arcsec"

                            # Create output filename with frame number
                            out_name = f"iris_{base_no_ext}_frame{frame_idx:03d}.fits"
                            output_file = os.path.join(output_dir, out_name)

                            # Save as 2D FITS
                            hdu_out = fits.PrimaryHDU(data_2d, header=header)
                            hdu_out.header.add_history(
                                "IRIS frame extracted by SolarViewer"
                            )
                            hdu_out.writeto(output_file, overwrite=True)

                            processed_files.append(output_file)

                        print(f"  - Saved {n_frames} frames as individual FITS files")

                        # Remove original compressed file
                        if file_path.endswith(".gz"):
                            os.remove(file_path)
                    else:
                        # 2D data, just decompress if needed
                        if file_path.endswith(".gz"):
                            out_name = base_name.replace(".gz", "")
                            output_file = os.path.join(output_dir, out_name)
                            hdu_out = fits.PrimaryHDU(data, header=header_orig)
                            hdu_out.header.add_history("Decompressed by SolarViewer")
                            hdu_out.writeto(output_file, overwrite=True)
                            os.remove(file_path)
                            processed_files.append(output_file)
                        else:
                            processed_files.append(file_path)
            except Exception as e:
                print(f"Warning: Could not process {base_name}: {e}")
                processed_files.append(file_path)

        downloaded_files = processed_files
        print("Note: IRIS data is Level 2 (pre-calibrated).")
        print("  - SJI files: 2D image frames extracted for solarviewer")
        print("  - Raster files: 3D spectroscopic data (requires specialized tools)")

    print(f"Successfully downloaded {len(downloaded_files)} IRIS files.")
    return downloaded_files


def download_soho(
    instrument,
    start_time,
    end_time,
    output_dir,
    wavelength=None,
    detector=None,
    skip_calibration=False,
):
    """
    Download SOHO (Solar and Heliospheric Observatory) data for a given time range.

    SOHO data is not available through DRMS/JSOC, so this function uses SunPy's Fido client.

    Args:
        instrument (str): SOHO instrument ('EIT', 'LASCO', 'MDI')
        start_time (str): Start time in 'YYYY.MM.DD HH:MM:SS' format
        end_time (str): End time in 'YYYY.MM.DD HH:MM:SS' format
        output_dir (str): Directory to save downloaded files
        wavelength (int, optional): For EIT, wavelength in Angstroms (171, 195, 284, 304)
        detector (str, optional): For LASCO, detector name ('C1', 'C2', 'C3')
        skip_calibration (bool, optional): If True, skip calibration steps

    Returns:
        list: Paths to downloaded FITS files
    """
    try:
        import sunpy.net
        from sunpy.net import Fido, attrs as a
    except ImportError:
        print("Error: SunPy not installed or not properly configured.")
        return []

    # Create output directory if it doesn't exist
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)

    # Parse the time strings
    start_dt = datetime.strptime(start_time, "%Y.%m.%d %H:%M:%S")
    end_dt = datetime.strptime(end_time, "%Y.%m.%d %H:%M:%S")

    # Validate and normalize instrument name
    instrument = instrument.upper()
    if instrument not in ["EIT", "LASCO", "MDI"]:
        print(
            f"Error: Invalid SOHO instrument '{instrument}'. Use 'EIT', 'LASCO', or 'MDI'."
        )
        return []

    print(f"Searching for SOHO/{instrument} data from {start_time} to {end_time}")

    try:
        # Build query based on instrument
        query_args = [
            a.Time(start_dt, end_dt),
            a.Instrument(instrument),
        ]

        # Add instrument-specific parameters
        if instrument == "EIT":
            # Use SDAC provider which works more reliably for EIT
            query_args.append(a.Provider("SDAC"))
            # For EIT wavelength filtering, use a range to improve matching
            if wavelength is not None:
                wl = int(wavelength)
                # Use a small tolerance range for wavelength matching
                query_args.append(
                    a.Wavelength((wl - 1) * u.angstrom, (wl + 1) * u.angstrom)
                )
        elif instrument == "LASCO" and detector is not None:
            query_args.append(a.Detector(detector.upper()))

        result = Fido.search(*query_args)

        # Count total files across all result tables
        total_files = sum(len(r) for r in result) if len(result) > 0 else 0

        if total_files == 0:
            # Try again without wavelength filter for EIT
            if instrument == "EIT" and wavelength is not None:
                print(f"No exact wavelength match, searching all EIT data...")
                result = Fido.search(
                    a.Time(start_dt, end_dt),
                    a.Instrument("EIT"),
                    a.Provider("SDAC"),
                )
                total_files = sum(len(r) for r in result) if len(result) > 0 else 0

            if total_files == 0:
                print("No data found for the specified parameters.")
                return []

        print(f"Found {total_files} files. Downloading...")

        # Download the files with retry logic
        downloaded = robust_fido_fetch(result, output_dir)
    except Exception as e:
        print(f"Error during Fido search/fetch: {str(e)}")
        print("Check your search parameters and ensure sunpy is properly installed.")
        return []

    downloaded_files = [str(file_path) for file_path in downloaded]

    # Fix files without .fits extension (SOHO/EIT files from SDAC often lack proper extension)
    fixed_files = []
    for file_path in downloaded_files:
        if (
            not file_path.endswith(".fits")
            and not file_path.endswith(".fits.gz")
            and not file_path.endswith(".fts")
        ):
            # Check if it's actually a FITS file
            try:
                with fits.open(file_path) as hdu:
                    # It's a valid FITS file, rename it
                    new_path = file_path + ".fits"
                    os.rename(file_path, new_path)
                    print(
                        f"Renamed {os.path.basename(file_path)} -> {os.path.basename(new_path)}"
                    )
                    fixed_files.append(new_path)
            except Exception:
                # Not a FITS file or can't open, keep original
                fixed_files.append(file_path)
        else:
            fixed_files.append(file_path)
    downloaded_files = fixed_files

    if not skip_calibration and not downloaded_files:
        print("Warning: No files were downloaded to calibrate.")
        return []

    # Calibration for SOHO data
    if not skip_calibration and len(downloaded_files) > 0:
        calibrated_files = []

        if instrument == "EIT":
            print("Performing EIT Level 1.5 calibration...")
            for file_path in downloaded_files:
                try:
                    eit_map = Map(file_path)
                    base_name = os.path.basename(file_path)
                    print(f"Processing {base_name}...")

                    # Step 1: Rotate to solar north using SC_ROLL (EIT-specific)
                    crota = float(
                        eit_map.meta.get(
                            "sc_roll",
                            eit_map.meta.get("crota", eit_map.meta.get("crota2", 0.0)),
                        )
                    )
                    if abs(crota) > 0.01:
                        # Convert to float and use NaN for missing pixels (displays as transparent)
                        import numpy as np

                        float_data = eit_map.data.astype(np.float64)
                        eit_map = Map(float_data, eit_map.meta)
                        eit_map = eit_map.rotate(
                            angle=-crota * u.deg, recenter=True, missing=np.nan
                        )
                        print(f"  - Rotated {-crota:.2f}° to solar north")

                    # Step 2: Fix WCS metadata for solarviewer compatibility
                    # EIT uses "Solar-X/Solar-Y" which needs to be HPLN-TAN/HPLT-TAN
                    meta = eit_map.meta.copy()
                    if meta.get("ctype1", "").lower() in ["solar-x", "solar_x", ""]:
                        meta["ctype1"] = "HPLN-TAN"
                        meta["ctype2"] = "HPLT-TAN"
                    if meta.get("cunit1") is None:
                        meta["cunit1"] = "arcsec"
                        meta["cunit2"] = "arcsec"
                    # Negate CDELT1 to correct Solar-X direction after rotation
                    meta["cdelt1"] = -abs(meta.get("cdelt1", 2.63))
                    eit_map = Map(eit_map.data, meta)
                    print(f"  - Fixed WCS (HPLN-TAN, arcsec, Solar-X corrected)")

                    # Step 3: Normalize by exposure time
                    exptime = (
                        eit_map.exposure_time.value
                        if hasattr(eit_map, "exposure_time")
                        else 0
                    )
                    if exptime > 0:
                        eit_map = eit_map / eit_map.exposure_time
                        print(f"  - Normalized by exposure time ({exptime:.2f}s)")

                    # Save calibrated file
                    output_file = os.path.join(output_dir, f"eit_lev1_5_{base_name}")
                    # Ensure .fits extension
                    if not output_file.endswith(".fits"):
                        output_file = output_file + ".fits"
                    eit_map.save(output_file, overwrite=True)
                    print(f"  - Saved as {os.path.basename(output_file)}")

                    # Remove original
                    if os.path.exists(output_file) and file_path != output_file:
                        os.remove(file_path)
                        calibrated_files.append(output_file)
                    else:
                        calibrated_files.append(output_file)
                except Exception as e:
                    print(
                        f"Warning: Could not calibrate {os.path.basename(file_path)}: {e}"
                    )
                    calibrated_files.append(file_path)
            downloaded_files = calibrated_files

        elif instrument == "LASCO":
            print("Performing LASCO basic calibration...")
            for file_path in downloaded_files:
                try:
                    lasco_map = Map(file_path)
                    base_name = os.path.basename(file_path)

                    # LASCO calibration is complex (stray light, F-corona, vignetting)
                    # We do basic exposure normalization
                    exptime = (
                        lasco_map.exposure_time.value
                        if hasattr(lasco_map, "exposure_time")
                        else 0
                    )
                    if exptime > 0:
                        lasco_map = lasco_map / lasco_map.exposure_time

                        output_file = os.path.join(output_dir, f"lasco_cal_{base_name}")
                        lasco_map.save(output_file, overwrite=True)
                        print(f"Calibrated {base_name} (exposure normalized)")

                        if os.path.exists(output_file):
                            os.remove(file_path)
                            calibrated_files.append(output_file)
                        else:
                            calibrated_files.append(file_path)
                    else:
                        calibrated_files.append(file_path)
                except Exception as e:
                    print(
                        f"Warning: Could not process {os.path.basename(file_path)}: {e}"
                    )
                    calibrated_files.append(file_path)
            downloaded_files = calibrated_files
            print("Note: For full LASCO calibration (stray light, F-corona removal),")
            print("      additional specialized tools are required.")

    print(f"Successfully downloaded {len(downloaded_files)} SOHO/{instrument} files.")
    return downloaded_files


def download_goes_suvi(
    start_time,
    end_time,
    output_dir,
    wavelength=None,
    level="2",
):
    """
    Download GOES SUVI (Solar Ultraviolet Imager) data for a given time range.

    SUVI is the EUV imager on the GOES-16/17/18 satellites, providing
    similar coverage to SDO/AIA but from geostationary orbit.

    Args:
        start_time (str): Start time in 'YYYY.MM.DD HH:MM:SS' format
        end_time (str): End time in 'YYYY.MM.DD HH:MM:SS' format
        output_dir (str): Directory to save downloaded files
        wavelength (int, optional): Wavelength in Angstroms (94, 131, 171, 195, 284, 304)
        level (str): Data level ('1b' or '2')

    Returns:
        list: Paths to downloaded FITS files
    """
    try:
        from sunpy.net import Fido, attrs as a
    except ImportError:
        print("Error: SunPy not installed or not properly configured.")
        return []

    # Create output directory if it doesn't exist
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)

    # Parse the time strings
    start_dt = datetime.strptime(start_time, "%Y.%m.%d %H:%M:%S")
    end_dt = datetime.strptime(end_time, "%Y.%m.%d %H:%M:%S")

    print(f"Searching for GOES SUVI data from {start_time} to {end_time}")

    try:
        # Build query
        query_args = [
            a.Time(start_dt, end_dt),
            a.Instrument("SUVI"),
            a.Level(level),
        ]

        if wavelength is not None:
            wl = int(wavelength)
            query_args.append(
                a.Wavelength((wl - 1) * u.angstrom, (wl + 1) * u.angstrom)
            )

        result = Fido.search(*query_args)

        # Count total files across all result tables
        total_files = sum(len(r) for r in result) if len(result) > 0 else 0

        if total_files == 0:
            print("No data found for the specified parameters.")
            return []

        print(f"Found {total_files} files. Downloading...")

        # Download the files with retry logic
        downloaded = robust_fido_fetch(result, output_dir)
    except Exception as e:
        print(f"Error during Fido search/fetch: {str(e)}")
        return []

    # Post-process SUVI files to fix WCS for solarviewer compatibility
    # SUVI files store data in compressed HDU 1, but solarviewer reads HDU 0
    downloaded_files = []
    for file_path in downloaded:
        file_path = str(file_path)
        try:
            # Use sunpy to load the file correctly (handles compressed HDU)
            suvi_map = Map(file_path)

            # Create output filename
            base_name = os.path.basename(file_path)
            processed_file = os.path.join(output_dir, f"processed_{base_name}")

            # Save as standard FITS with WCS in HDU 0
            suvi_map.save(processed_file, overwrite=True)

            # Remove original compressed file
            if os.path.exists(processed_file) and processed_file != file_path:
                os.remove(file_path)
                print(f"Processed {base_name} for solarviewer compatibility")
                downloaded_files.append(processed_file)
            else:
                downloaded_files.append(file_path)
        except Exception as e:
            print(f"Warning: Could not process {os.path.basename(file_path)}: {e}")
            downloaded_files.append(file_path)

    print(f"Successfully downloaded {len(downloaded_files)} GOES SUVI files.")
    return downloaded_files


def download_stereo(
    start_time,
    end_time,
    output_dir,
    spacecraft="A",
    instrument="EUVI",
    wavelength=None,
):
    """
    Download STEREO SECCHI data for a given time range.

    STEREO consists of two spacecraft (A and B) providing stereoscopic
    views of solar activity. STEREO-B lost contact in 2014.

    Args:
        start_time (str): Start time in 'YYYY.MM.DD HH:MM:SS' format
        end_time (str): End time in 'YYYY.MM.DD HH:MM:SS' format
        output_dir (str): Directory to save downloaded files
        spacecraft (str): 'A' or 'B' (B only available until 2014)
        instrument (str): 'EUVI' (EUV), 'COR1' (inner coronagraph),
                         'COR2' (outer coronagraph), 'HI1', 'HI2' (heliospheric imagers)
        wavelength (int, optional): For EUVI - 171, 195, 284, or 304 Angstroms

    Returns:
        list: Paths to downloaded FITS files
    """
    try:
        from sunpy.net import Fido, attrs as a
    except ImportError:
        print("Error: SunPy not installed or not properly configured.")
        return []

    # Create output directory if it doesn't exist
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)

    # Parse the time strings
    start_dt = datetime.strptime(start_time, "%Y.%m.%d %H:%M:%S")
    end_dt = datetime.strptime(end_time, "%Y.%m.%d %H:%M:%S")

    # Normalize inputs
    spacecraft = spacecraft.upper()
    instrument = instrument.upper()
    source = f"STEREO_{spacecraft}"

    print(f"Searching for {source}/{instrument} data from {start_time} to {end_time}")

    try:
        # Build query
        query_args = [
            a.Time(start_dt, end_dt),
            a.Source(source),
            a.Instrument("SECCHI"),
            a.Detector(instrument),
        ]

        if instrument == "EUVI" and wavelength is not None:
            wl = int(wavelength)
            query_args.append(
                a.Wavelength((wl - 1) * u.angstrom, (wl + 1) * u.angstrom)
            )

        result = Fido.search(*query_args)

        # Count total files across all result tables
        total_files = sum(len(r) for r in result) if len(result) > 0 else 0

        if total_files == 0:
            print("No data found for the specified parameters.")
            return []

        print(f"Found {total_files} files. Downloading...")

        # Download the files with retry logic
        downloaded = robust_fido_fetch(result, output_dir)
    except Exception as e:
        print(f"Error during Fido search/fetch: {str(e)}")
        return []

    downloaded_files = [str(file_path) for file_path in downloaded]
    print(
        f"Successfully downloaded {len(downloaded_files)} {source}/{instrument} files."
    )
    return downloaded_files


def download_gong(
    start_time,
    end_time,
    output_dir,
):
    """
    Download GONG (Global Oscillation Network Group) magnetogram data.

    GONG provides ground-based magnetogram observations with continuous
    coverage from a network of stations around the world.

    Args:
        start_time (str): Start time in 'YYYY.MM.DD HH:MM:SS' format
        end_time (str): End time in 'YYYY.MM.DD HH:MM:SS' format
        output_dir (str): Directory to save downloaded files

    Returns:
        list: Paths to downloaded FITS files
    """
    try:
        from sunpy.net import Fido, attrs as a
    except ImportError:
        print("Error: SunPy not installed or not properly configured.")
        return []

    # Create output directory if it doesn't exist
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)

    # Parse the time strings
    start_dt = datetime.strptime(start_time, "%Y.%m.%d %H:%M:%S")
    end_dt = datetime.strptime(end_time, "%Y.%m.%d %H:%M:%S")

    print(f"Searching for GONG magnetogram data from {start_time} to {end_time}")

    try:
        # GONG data query
        result = Fido.search(
            a.Time(start_dt, end_dt),
            a.Instrument("GONG"),
            a.Physobs("LOS_magnetic_field"),
        )

        # Count total files across all result tables
        total_files = sum(len(r) for r in result) if len(result) > 0 else 0

        if total_files == 0:
            print("No data found for the specified parameters.")
            return []

        print(f"Found {total_files} files. Downloading...")

        # Download the files with retry logic
        downloaded = robust_fido_fetch(result, output_dir)
    except Exception as e:
        print(f"Error during Fido search/fetch: {str(e)}")
        return []

    downloaded_files = [str(file_path) for file_path in downloaded]
    print(f"Successfully downloaded {len(downloaded_files)} GONG magnetogram files.")
    return downloaded_files


if __name__ == "__main__":
    import sys

    sys.exit(main())

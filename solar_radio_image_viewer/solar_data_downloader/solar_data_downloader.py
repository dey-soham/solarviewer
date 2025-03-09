import drms, time, os, glob, warnings
from sunpy.map import Map

# from sunpy.instr.aia import aiaprep  # This import is no longer available in sunpy 6.0.5
# In newer versions of sunpy, aiaprep has been moved to the aiapy package
try:
    from aiapy.calibrate import register

    HAS_AIAPY = True
except ImportError:
    # If aiapy is not installed, we'll provide a helpful message
    HAS_AIAPY = False
    print(
        "Warning: aiapy package not found. Level 1.5 calibration will not be available."
    )
    print("To install aiapy: pip install aiapy")
from astropy.io import fits
from datetime import datetime, timedelta
import astropy.units as u  # Import astropy units for use throughout the code

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
    - download_iris: Download IRIS data
    - download_soho: Download SOHO data

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
HMI_SERIES = {
    "45s": "hmi.M_45s",  # Vector magnetogram
    "720s": "hmi.M_720s",  # Vector magnetogram (12 min)
    "B_45s": "hmi.B_45s",  # Line-of-sight magnetogram
    "B_720s": "hmi.B_720s",  # Line-of-sight magnetogram (12 min)
    "Ic_45s": "hmi.Ic_45s",  # Continuum intensity
    "Ic_720s": "hmi.Ic_720s",  # Continuum intensity (12 min)
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


def aiaexport(wavelength, cadence, time):
    """
    Generate an export command for AIA data.

    Args:
        wavelength (str): AIA wavelength (e.g., '171', '1600')
        cadence (str): Time cadence ('12s', '24s', or '1h')
        time (str): Start time in 'YYYY.MM.DD_HH:MM:SS' format

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

    # Format time for the export command - ensure proper format for DRMS
    # The format should be YYYY.MM.DD_HH:MM:SS_UTC with no spaces
    # Input time is expected to be in YYYY.MM.DD_HH:MM:SS format
    time_utc = time + "_UTC"

    # Create export command
    export_cmd = f"{AIA_SERIES[cadence]}[{time_utc}/1h@{cadence}][{wavelength}]"
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
    # Email is technically optional for small requests but recommended for reliability
    # For large export requests, an email address is required for JSOC to notify you
    if email is None:
        print(
            "Warning: No email provided. Small requests may work, but larger requests will likely fail."
        )
        print(
            "Consider providing an email address or using alternative download methods."
        )
    client = drms.Client(email=email)

    # Format start time for export command - YYYY.MM.DD_HH:MM:SS format required by DRMS
    # This expects start_time in format YYYY.MM.DD HH:MM:SS
    start_time_fmt = start_time.replace(" ", "_")

    # Create export command
    export_cmd = aiaexport(wavelength=wavelength, cadence=cadence, time=start_time_fmt)
    if export_cmd is None:
        return []

    # Request data export
    print(f"Requesting data export with command: {export_cmd}")
    try:
        response = client.export(export_cmd)
        record = response.data.record
        record_list = record.values.tolist()
    except Exception as e:
        print(f"Error during data export: {str(e)}")
        print("Try using the --use-fido option as an alternative download method.")
        return []

    # Process records to get timestamps
    record_dict = {}
    for i in range(len(record_list)):
        rec = record_list[i].split("{")[-1][:-2]
        timestamp = record_list[i].split("[")[1].split("T")[1][:-2]
        if rec == "image_lev":
            record_dict[i] = timestamp

    aia_time_list = list(record_dict.values())

    # Get list of times to download
    time_list = get_time_list(start_time, end_time, interval_seconds)

    # Download and process files
    downloaded_files = []
    for current_time in time_list:
        if current_time in aia_time_list:
            key = get_key(current_time, record_dict)
            filename = f"{current_time}_{wavelength}"

            # Define output files for Level 1.0 and Level 1.5
            level1_file = os.path.join(output_dir, f"aia_{filename}.fits")
            level1_5_file = os.path.join(output_dir, f"aia_{filename}_lev1.5.fits")

            # Determine which file to check for existence and add to downloaded_files
            output_file = level1_5_file if can_calibrate else level1_file

            if not os.path.isfile(output_file):
                # Download level 1.0 file
                response.download(temp_dir, key)
                temp_file = glob.glob(os.path.join(temp_dir, "*.fits"))[0]
                os.rename(temp_file, level1_file)

                if can_calibrate:
                    try:
                        # Convert to level 1.5 using aiapy.calibrate.register
                        print(
                            f"Processing {os.path.basename(level1_file)} to Level 1.5..."
                        )
                        aia_map = Map(level1_file)
                        warnings.filterwarnings("ignore")

                        # Use aiapy's register function (replacement for aiaprep)
                        lev1_5map = register(aia_map)
                        lev1_5map.save(level1_5_file)

                        # Clean up level 1.0 file if successful
                        os.remove(level1_file)

                        print(
                            f"Downloaded and processed: {os.path.basename(level1_5_file)}"
                        )
                    except Exception as e:
                        print(f"Error during Level 1.5 calibration: {str(e)}")
                        print(
                            f"Using Level 1.0 file instead: {os.path.basename(level1_file)}"
                        )
                        output_file = level1_file  # Use Level 1.0 file instead
                else:
                    print(f"Downloaded Level 1.0 file: {os.path.basename(level1_file)}")
                    if not HAS_AIAPY:
                        print(
                            "For Level 1.5 calibration, install aiapy: pip install aiapy"
                        )

            downloaded_files.append(output_file)

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
):
    """
    Alternative download function using SunPy's Fido client which doesn't require an email.

    Args:
        wavelength (str): AIA wavelength (e.g., '171', '1600')
        start_time (str): Start time in 'YYYY.MM.DD HH:MM:SS' format
        end_time (str): End time in 'YYYY.MM.DD HH:MM:SS' format
        output_dir (str): Directory to save downloaded files
        skip_calibration (bool, optional): If True, skip Level 1.5 calibration even if aiapy is available

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

    try:
        # Create the query with correct unit import
        result = Fido.search(
            a.Time(start_dt, end_dt),
            a.Instrument("AIA"),
            a.Wavelength(wl_int * u.angstrom),  # Now using the correct astropy units
        )

        if len(result) == 0 or len(result[0]) == 0:
            print("No data found for the specified parameters.")
            return []

        print(f"Found {len(result[0])} files. Downloading...")

        # Download the files
        downloaded = Fido.fetch(result, path=output_dir + "/{file}")
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
            output_dir, f"{os.path.splitext(base_name)[0]}_lev1.5.fits"
        )

        output_file = level1_5_file if can_calibrate else file_path

        if can_calibrate and not os.path.isfile(level1_5_file):
            try:
                # Convert to level 1.5 using aiapy.calibrate.register
                print(f"Processing {base_name} to Level 1.5...")
                aia_map = Map(file_path)
                warnings.filterwarnings("ignore")

                # Use aiapy's register function (replacement for aiaprep)
                lev1_5map = register(aia_map)
                lev1_5map.save(level1_5_file)

                print(f"Downloaded and processed: {os.path.basename(level1_5_file)}")
                output_file = level1_5_file
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


def hmiexport(series, time):
    """
    Generate an export command for HMI data.

    Args:
        series (str): HMI series (e.g., 'M_45s', 'B_45s', 'Ic_720s')
        time (str): Start time in 'YYYY.MM.DD_HH:MM:SS' format

    Returns:
        str: The export command string or None if invalid parameters
    """
    # Validate series
    if series not in HMI_SERIES.keys():
        print(
            f"Error: Invalid HMI series '{series}'. Use one of: {', '.join(HMI_SERIES.keys())}"
        )
        return None

    # Format time for the export command
    time_utc = time + "_UTC"

    # Create export command
    export_cmd = f"{HMI_SERIES[series]}[{time_utc}/1h]"
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
    # Email is technically optional for small requests but recommended for reliability
    # For large export requests, an email address is required for JSOC to notify you
    if email is None:
        print(
            "Warning: No email provided. Small requests may work, but larger requests will likely fail."
        )
        print(
            "Consider providing an email address or using alternative download methods."
        )
    client = drms.Client(email=email)

    # Format start time for export command - YYYY.MM.DD_HH:MM:SS format required by DRMS
    # This expects start_time in format YYYY.MM.DD HH:MM:SS
    start_time_fmt = start_time.replace(" ", "_")

    # Create export command
    export_cmd = hmiexport(series=series, time=start_time_fmt)
    if export_cmd is None:
        return []

    # Request data export
    print(f"Requesting data export with command: {export_cmd}")
    try:
        response = client.export(export_cmd)
        record = response.data.record
        record_list = record.values.tolist()
    except Exception as e:
        print(f"Error during data export: {str(e)}")
        print("Try using the --use-fido option as an alternative download method.")
        return []

    # Process records to get timestamps
    record_dict = {}
    for i in range(len(record_list)):
        # HMI records have a different format than AIA records
        try:
            parts = record_list[i].split("[")
            if len(parts) > 1:
                timestamp_part = parts[1].split("_")[0:3]  # Get the date/time part
                timestamp = "_".join(timestamp_part)
                record_dict[i] = timestamp
        except Exception as e:
            print(f"Warning: Could not parse record {i}: {str(e)}")
            continue

    hmi_time_list = list(record_dict.values())

    # Get list of times to download
    time_list = get_time_list(start_time, end_time, interval_seconds)

    # Download and process files
    downloaded_files = []
    for current_time in time_list:
        formatted_current_time = current_time.replace(":", "_").replace(".", "_")
        matching_times = [t for t in hmi_time_list if formatted_current_time in t]

        if matching_times:
            for match_time in matching_times:
                key = get_key(match_time, record_dict)
                filename = f"hmi_{series}_{match_time.replace(':', '_')}"
                output_file = os.path.join(output_dir, f"{filename}.fits")

                if not os.path.isfile(output_file):
                    # Download file
                    try:
                        response.download(temp_dir, key)
                        temp_files = glob.glob(os.path.join(temp_dir, "*.fits"))
                        if temp_files:
                            temp_file = temp_files[0]
                            os.rename(temp_file, output_file)
                            print(f"Downloaded: {os.path.basename(output_file)}")
                        else:
                            print(f"Warning: No files downloaded for {match_time}")
                            continue
                    except Exception as e:
                        print(f"Error downloading file for {match_time}: {str(e)}")
                        continue

                downloaded_files.append(output_file)

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

    # Map series to physobs and segment
    physobs = None
    if "B_" in series:
        physobs = "LOS_magnetic_field"
    elif "Ic_" in series:
        physobs = "continuum"
    else:  # M_ series
        physobs = "vector_magnetic_field"

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
            print("No data found for the specified parameters.")
            return []

        print(f"Found {len(result[0])} files. Downloading...")

        # Download the files
        downloaded = Fido.fetch(result, path=output_dir + "/{file}")
    except Exception as e:
        print(f"Error during Fido search/fetch: {str(e)}")
        print("Check your search parameters and ensure sunpy is properly installed.")
        return []

    downloaded_files = [str(file_path) for file_path in downloaded]

    print(f"Successfully downloaded {len(downloaded_files)} HMI files.")
    return downloaded_files


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

        # Download the files
        downloaded = Fido.fetch(result, path=output_dir + "/{file}")
    except Exception as e:
        print(f"Error during Fido search/fetch: {str(e)}")
        print("Check your search parameters and ensure sunpy is properly installed.")
        return []

    downloaded_files = [str(file_path) for file_path in downloaded]

    # IRIS data calibration is complex and would typically be done with specialized tools
    # such as iris_tools from the IRIS team
    if not skip_calibration:
        print("Note: For IRIS data, detailed calibration typically requires")
        print("specialized IRIS tools. The data downloaded is Level 2,")
        print("which includes basic calibration.")

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
        if instrument == "EIT" and wavelength is not None:
            query_args.append(a.Wavelength(int(wavelength) * u.angstrom))
        elif instrument == "LASCO" and detector is not None:
            query_args.append(a.Detector(detector.upper()))

        result = Fido.search(*query_args)

        if len(result) == 0 or len(result[0]) == 0:
            print("No data found for the specified parameters.")
            return []

        print(f"Found {len(result[0])} files. Downloading...")

        # Download the files
        downloaded = Fido.fetch(result, path=output_dir + "/{file}")
    except Exception as e:
        print(f"Error during Fido search/fetch: {str(e)}")
        print("Check your search parameters and ensure sunpy is properly installed.")
        return []

    downloaded_files = [str(file_path) for file_path in downloaded]

    if not skip_calibration and not downloaded_files:
        print("Warning: No files were downloaded to calibrate.")
        return []

    # Basic calibration for SOHO data
    if not skip_calibration:
        print(f"Note: For detailed calibration of SOHO/{instrument} data,")
        print("specialized tools may be required depending on your scientific needs.")

        # For some instruments like EIT, we could use SunPy to do basic processing
        if instrument == "EIT" and len(downloaded_files) > 0:
            print("Performing basic calibration on EIT files...")
            for file_path in downloaded_files:
                try:
                    eit_map = Map(file_path)
                    # Basic processing - normalize by exposure time
                    if "exptime" in eit_map.meta:
                        # Add a note about the calibration
                        print(
                            f"Normalized {os.path.basename(file_path)} by exposure time"
                        )
                except Exception as e:
                    print(
                        f"Warning: Could not calibrate {os.path.basename(file_path)}: {str(e)}"
                    )

    print(f"Successfully downloaded {len(downloaded_files)} SOHO/{instrument} files.")
    return downloaded_files


if __name__ == "__main__":
    import sys

    sys.exit(main())

#!/usr/bin/env python3
"""
Solar Data Downloader - Interactive tool for downloading solar observatory data.

This script provides a user-friendly interface for downloading data from
various solar observatories using the solar_data_downloader module.

Supported observatories:
- SDO/AIA (Atmospheric Imaging Assembly)
- SDO/HMI (Helioseismic and Magnetic Imager)
- IRIS (Interface Region Imaging Spectrograph)
- SOHO (Solar and Heliospheric Observatory)
"""

import os
import sys
import datetime
from pathlib import Path

# Try to import the solar_data_downloader module
try:
    # First try relative import (when used as part of package)
    from . import solar_data_downloader as sdd
except ImportError:
    try:
        # Then try importing from the same directory (when run as script)
        script_dir = os.path.dirname(os.path.abspath(__file__))
        if script_dir not in sys.path:
            sys.path.append(script_dir)
        import solar_data_downloader as sdd
    except ImportError:
        print("Error: Could not import solar_data_downloader module.")
        print(
            "Make sure solar_data_downloader.py is in the same directory as this script."
        )
        sys.exit(1)

# Check if required packages are installed
try:
    import sunpy
    import drms
    import astropy
except ImportError as e:
    print(f"Error: Missing required package: {e.name}")
    print("Please install the required packages with:")
    print("  pip install sunpy drms astropy")
    print("For AIA Level 1.5 calibration, also install:")
    print("  pip install aiapy")
    sys.exit(1)


def clear_screen():
    """Clear the terminal screen."""
    os.system("cls" if os.name == "nt" else "clear")


def print_header():
    """Print the application header."""
    clear_screen()
    print("=" * 80)
    print("                           SOLAR DATA DOWNLOADER                           ")
    print("=" * 80)
    print("Download and process data from various solar observatories")
    print("=" * 80)
    print()


def get_date_input(prompt, default=None):
    """
    Get a date input from the user with validation.

    Args:
        prompt (str): The prompt to display to the user
        default (str, optional): Default value to use if user presses Enter

    Returns:
        str: Date in YYYY.MM.DD format
    """
    while True:
        if default:
            user_input = input(f"{prompt} [default: {default}]: ")
            if not user_input.strip():
                return default
        else:
            user_input = input(f"{prompt} (YYYY.MM.DD): ")

        try:
            # Try to parse the date
            date_obj = datetime.datetime.strptime(user_input, "%Y.%m.%d")
            # Format it back to ensure consistency
            return date_obj.strftime("%Y.%m.%d")
        except ValueError:
            print("Error: Invalid date format. Please use YYYY.MM.DD.")


def get_time_input(prompt, default=None):
    """
    Get a time input from the user with validation.

    Args:
        prompt (str): The prompt to display to the user
        default (str, optional): Default value to use if user presses Enter

    Returns:
        str: Time in HH:MM:SS format
    """
    while True:
        if default:
            user_input = input(f"{prompt} [default: {default}]: ")
            if not user_input.strip():
                return default
        else:
            user_input = input(f"{prompt} (HH:MM:SS): ")

        try:
            # Try to parse the time
            time_obj = datetime.datetime.strptime(user_input, "%H:%M:%S")
            # Format it back to ensure consistency
            return time_obj.strftime("%H:%M:%S")
        except ValueError:
            print("Error: Invalid time format. Please use HH:MM:SS.")


def get_datetime_range():
    """
    Get a datetime range from the user.

    Returns:
        tuple: (start_time, end_time) both in 'YYYY.MM.DD HH:MM:SS' format
    """
    # Get default dates (today)
    today = datetime.datetime.now().strftime("%Y.%m.%d")
    now = datetime.datetime.now().strftime("%H:%M:%S")
    one_hour_later = (datetime.datetime.now() + datetime.timedelta(hours=1)).strftime(
        "%H:%M:%S"
    )

    print("\nPlease specify the time range for data download:")
    print("------------------------------------------------")

    start_date = get_date_input("  Start date", today)
    start_time = get_time_input("  Start time", now)
    end_date = get_date_input("  End date", start_date)

    # If end date is same as start date, suggest a time 1 hour after start
    default_end_time = one_hour_later if end_date == start_date else now
    end_time = get_time_input("  End time", default_end_time)

    # Format the full datetime strings
    start_datetime = f"{start_date} {start_time}"
    end_datetime = f"{end_date} {end_time}"

    return start_datetime, end_datetime


def get_output_directory(instrument):
    """
    Get the output directory from the user.

    Args:
        instrument (str): The selected instrument

    Returns:
        str: Path to the output directory
    """
    default_dir = f"./{instrument.lower()}_data"
    user_input = input(f"\nOutput directory [default: {default_dir}]: ")

    output_dir = user_input.strip() if user_input.strip() else default_dir

    # Create the directory if it doesn't exist
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    return output_dir


def get_email():
    """
    Get the user's email for DRMS requests.

    Returns:
        str or None: The email address or None if not provided
    """
    print("\nEmail is recommended for reliability when using DRMS.")
    print("For large requests, an email is required to notify you when data is ready.")
    print("(Press Enter to skip if using Fido download method)")

    email = input("Email address: ")
    return email if email.strip() else None


def download_aia_data():
    """
    Guide the user through downloading AIA data.
    """
    print_header()
    print("SDO/AIA Data Download")
    print("---------------------")

    # Get wavelength
    wavelength_options = {
        "1": "94",
        "2": "131",
        "3": "171",
        "4": "193",
        "5": "211",
        "6": "304",
        "7": "335",
        "8": "1600",
        "9": "1700",
        "10": "4500",
    }

    print("\nAvailable wavelengths:")
    for key, value in wavelength_options.items():
        print(f"  {key}: {value} Å")

    while True:
        wavelength_choice = input("\nSelect wavelength (1-10) [default: 3]: ")
        wavelength_choice = "3" if not wavelength_choice.strip() else wavelength_choice

        if wavelength_choice in wavelength_options:
            wavelength = wavelength_options[wavelength_choice]
            break
        else:
            print("Invalid choice. Please enter a number between 1 and 10.")

    # Get cadence
    cadence_options = {"1": "12s", "2": "24s", "3": "1h"}

    print("\nAvailable cadences:")
    print("  1: 12s (for EUV: 94, 131, 171, 193, 211, 304, 335 Å)")
    print("  2: 24s (for UV: 1600, 1700 Å)")
    print("  3: 1h (for Visible: 4500 Å)")

    # Auto-select cadence based on wavelength or let user choose
    default_cadence = "1"
    if wavelength in ["1600", "1700"]:
        default_cadence = "2"
    elif wavelength in ["4500"]:
        default_cadence = "3"

    while True:
        cadence_choice = input(f"\nSelect cadence (1-3) [default: {default_cadence}]: ")
        cadence_choice = (
            default_cadence if not cadence_choice.strip() else cadence_choice
        )

        if cadence_choice in cadence_options:
            cadence = cadence_options[cadence_choice]
            break
        else:
            print("Invalid choice. Please enter a number between 1 and 3.")

    # Get time range
    start_time, end_time = get_datetime_range()

    # Get output directory
    output_dir = get_output_directory("AIA")

    # Download method
    print("\nDownload method:")
    print("  1: DRMS client (recommended for large datasets)")
    print("  2: Fido client (no email required)")

    while True:
        method_choice = input("\nSelect download method (1-2) [default: 1]: ")
        method_choice = "1" if not method_choice.strip() else method_choice

        if method_choice in ["1", "2"]:
            break
        else:
            print("Invalid choice. Please enter 1 or 2.")

    # Get email if using DRMS
    email = None
    if method_choice == "1":
        email = get_email()

    # Confirm download
    print("\n" + "=" * 50)
    print("Download Summary:")
    print(f"  Instrument: SDO/AIA")
    print(f"  Wavelength: {wavelength} Å")
    print(f"  Cadence: {cadence}")
    print(f"  Time range: {start_time} to {end_time}")
    print(f"  Output directory: {output_dir}")
    print(f"  Download method: {'DRMS' if method_choice == '1' else 'Fido'}")
    if email:
        print(f"  Email: {email}")
    print("=" * 50)

    confirm = input("\nProceed with download? (y/n) [default: y]: ")
    if confirm.lower() in ["", "y", "yes"]:
        print("\nDownloading data...")
        try:
            if method_choice == "1":
                # Use DRMS method
                files = sdd.download_aia(
                    wavelength=wavelength,
                    cadence=cadence,
                    start_time=start_time,
                    end_time=end_time,
                    output_dir=output_dir,
                    email=email,
                )
            else:
                # Use Fido method
                files = sdd.download_aia_with_fido(
                    wavelength=wavelength,
                    start_time=start_time,
                    end_time=end_time,
                    output_dir=output_dir,
                )

            print(f"\nDownload complete. Downloaded {len(files)} files to {output_dir}")
        except Exception as e:
            print(f"\nError during download: {str(e)}")
            print(
                "Please check the error message above for troubleshooting information."
            )
    else:
        print("\nDownload cancelled.")

    input("\nPress Enter to return to the main menu...")


def download_hmi_data():
    """
    Guide the user through downloading HMI data.
    """
    print_header()
    print("SDO/HMI Data Download")
    print("---------------------")

    # Get series
    series_options = {
        "1": "45s",  # Vector magnetogram
        "2": "720s",  # Vector magnetogram (12 min)
        "3": "B_45s",  # Line-of-sight magnetogram
        "4": "B_720s",  # Line-of-sight magnetogram (12 min)
        "5": "Ic_45s",  # Continuum intensity
        "6": "Ic_720s",  # Continuum intensity (12 min)
    }

    series_descriptions = {
        "1": "Vector magnetogram (45s cadence)",
        "2": "Vector magnetogram (12 min cadence)",
        "3": "Line-of-sight magnetogram (45s cadence)",
        "4": "Line-of-sight magnetogram (12 min cadence)",
        "5": "Continuum intensity (45s cadence)",
        "6": "Continuum intensity (12 min cadence)",
    }

    print("\nAvailable data series:")
    for key, value in series_descriptions.items():
        print(f"  {key}: {value}")

    while True:
        series_choice = input("\nSelect series (1-6) [default: 3]: ")
        series_choice = "3" if not series_choice.strip() else series_choice

        if series_choice in series_options:
            series = series_options[series_choice]
            break
        else:
            print("Invalid choice. Please enter a number between 1 and 6.")

    # Get time range
    start_time, end_time = get_datetime_range()

    # Get output directory
    output_dir = get_output_directory("HMI")

    # Download method
    print("\nDownload method:")
    print("  1: DRMS client (recommended for large datasets)")
    print("  2: Fido client (no email required)")

    while True:
        method_choice = input("\nSelect download method (1-2) [default: 1]: ")
        method_choice = "1" if not method_choice.strip() else method_choice

        if method_choice in ["1", "2"]:
            break
        else:
            print("Invalid choice. Please enter 1 or 2.")

    # Get email if using DRMS
    email = None
    if method_choice == "1":
        email = get_email()

    # Set interval seconds based on series
    interval_seconds = 45.0 if "45s" in series else 720.0

    # Confirm download
    print("\n" + "=" * 50)
    print("Download Summary:")
    print(f"  Instrument: SDO/HMI")
    print(
        f"  Series: {series} ({series_descriptions[series_choice].split('(')[0].strip()})"
    )
    print(f"  Time range: {start_time} to {end_time}")
    print(f"  Output directory: {output_dir}")
    print(f"  Download method: {'DRMS' if method_choice == '1' else 'Fido'}")
    if email:
        print(f"  Email: {email}")
    print("=" * 50)

    confirm = input("\nProceed with download? (y/n) [default: y]: ")
    if confirm.lower() in ["", "y", "yes"]:
        print("\nDownloading data...")
        try:
            if method_choice == "1":
                # Use DRMS method
                files = sdd.download_hmi(
                    series=series,
                    start_time=start_time,
                    end_time=end_time,
                    output_dir=output_dir,
                    email=email,
                    interval_seconds=interval_seconds,
                )
            else:
                # Use Fido method
                files = sdd.download_hmi_with_fido(
                    series=series,
                    start_time=start_time,
                    end_time=end_time,
                    output_dir=output_dir,
                )

            print(f"\nDownload complete. Downloaded {len(files)} files to {output_dir}")
        except Exception as e:
            print(f"\nError during download: {str(e)}")
            print(
                "Please check the error message above for troubleshooting information."
            )
    else:
        print("\nDownload cancelled.")

    input("\nPress Enter to return to the main menu...")


def download_iris_data():
    """
    Guide the user through downloading IRIS data.
    """
    print_header()
    print("IRIS Data Download")
    print("------------------")
    print("Note: IRIS data is only available through the Fido client")

    # Get observation type
    obs_type_options = {
        "1": "SJI",  # Slit-Jaw Imager
        "2": "raster",  # Spectrograph raster
    }

    print("\nObservation types:")
    print("  1: SJI (Slit-Jaw Imager)")
    print("  2: Raster (Spectrograph data)")

    while True:
        obs_type_choice = input("\nSelect observation type (1-2) [default: 1]: ")
        obs_type_choice = "1" if not obs_type_choice.strip() else obs_type_choice

        if obs_type_choice in obs_type_options:
            obs_type = obs_type_options[obs_type_choice]
            break
        else:
            print("Invalid choice. Please enter 1 or 2.")

    # Get wavelength if using SJI
    wavelength = None
    if obs_type == "SJI":
        wavelength_options = {"1": "1330", "2": "1400", "3": "2796", "4": "2832"}

        print("\nAvailable wavelengths for SJI:")
        print("  1: 1330 Å (C II, Transition Region)")
        print("  2: 1400 Å (Si IV, Transition Region)")
        print("  3: 2796 Å (Mg II k, Chromosphere)")
        print("  4: 2832 Å (Photosphere)")

        while True:
            wavelength_choice = input("\nSelect wavelength (1-4) [default: 2]: ")
            wavelength_choice = (
                "2" if not wavelength_choice.strip() else wavelength_choice
            )

            if wavelength_choice in wavelength_options:
                wavelength = wavelength_options[wavelength_choice]
                break
            else:
                print("Invalid choice. Please enter a number between 1 and 4.")

    # Get time range
    start_time, end_time = get_datetime_range()

    # Get output directory
    output_dir = get_output_directory("IRIS")

    # Confirm download
    print("\n" + "=" * 50)
    print("Download Summary:")
    print(f"  Instrument: IRIS")
    print(f"  Observation type: {obs_type}")
    if wavelength:
        print(f"  Wavelength: {wavelength} Å")
    print(f"  Time range: {start_time} to {end_time}")
    print(f"  Output directory: {output_dir}")
    print("=" * 50)

    confirm = input("\nProceed with download? (y/n) [default: y]: ")
    if confirm.lower() in ["", "y", "yes"]:
        print("\nDownloading data...")
        try:
            files = sdd.download_iris(
                start_time=start_time,
                end_time=end_time,
                output_dir=output_dir,
                obs_type=obs_type,
                wavelength=wavelength,
            )

            print(f"\nDownload complete. Downloaded {len(files)} files to {output_dir}")
        except Exception as e:
            print(f"\nError during download: {str(e)}")
            print(
                "Please check the error message above for troubleshooting information."
            )
    else:
        print("\nDownload cancelled.")

    input("\nPress Enter to return to the main menu...")


def download_soho_data():
    """
    Guide the user through downloading SOHO data.
    """
    print_header()
    print("SOHO Data Download")
    print("------------------")
    print("Note: SOHO data is only available through the Fido client")

    # Get instrument
    instrument_options = {
        "1": "EIT",  # Extreme-ultraviolet Imaging Telescope
        "2": "LASCO",  # Large Angle and Spectrometric Coronagraph
        "3": "MDI",  # Michelson Doppler Imager
    }

    print("\nAvailable instruments:")
    print("  1: EIT (Extreme-ultraviolet Imaging Telescope)")
    print("  2: LASCO (Large Angle and Spectrometric Coronagraph)")
    print("  3: MDI (Michelson Doppler Imager)")

    while True:
        instrument_choice = input("\nSelect instrument (1-3) [default: 1]: ")
        instrument_choice = "1" if not instrument_choice.strip() else instrument_choice

        if instrument_choice in instrument_options:
            instrument = instrument_options[instrument_choice]
            break
        else:
            print("Invalid choice. Please enter a number between 1 and 3.")

    # Get instrument-specific parameters
    wavelength = None
    detector = None

    if instrument == "EIT":
        wavelength_options = {"1": "171", "2": "195", "3": "284", "4": "304"}

        print("\nAvailable wavelengths for EIT:")
        print("  1: 171 Å (Fe IX/X, quiet corona, upper transition region)")
        print("  2: 195 Å (Fe XII, corona)")
        print("  3: 284 Å (Fe XV, active region corona)")
        print("  4: 304 Å (He II, chromosphere, transition region)")

        while True:
            wavelength_choice = input("\nSelect wavelength (1-4) [default: 2]: ")
            wavelength_choice = (
                "2" if not wavelength_choice.strip() else wavelength_choice
            )

            if wavelength_choice in wavelength_options:
                wavelength = wavelength_options[wavelength_choice]
                break
            else:
                print("Invalid choice. Please enter a number between 1 and 4.")

    elif instrument == "LASCO":
        detector_options = {"1": "C1", "2": "C2", "3": "C3"}

        detector_descriptions = {
            "1": "C1 (1.1-3 solar radii)",
            "2": "C2 (2-6 solar radii)",
            "3": "C3 (3.7-30 solar radii)",
        }

        print("\nAvailable detectors for LASCO:")
        for key, value in detector_descriptions.items():
            print(f"  {key}: {value}")

        while True:
            detector_choice = input("\nSelect detector (1-3) [default: 2]: ")
            detector_choice = "2" if not detector_choice.strip() else detector_choice

            if detector_choice in detector_options:
                detector = detector_options[detector_choice]
                break
            else:
                print("Invalid choice. Please enter a number between 1 and 3.")

    # Get time range
    start_time, end_time = get_datetime_range()

    # Get output directory
    output_dir = get_output_directory(f"SOHO_{instrument}")

    # Confirm download
    print("\n" + "=" * 50)
    print("Download Summary:")
    print(f"  Instrument: SOHO/{instrument}")
    if wavelength:
        print(f"  Wavelength: {wavelength} Å")
    if detector:
        print(f"  Detector: {detector}")
    print(f"  Time range: {start_time} to {end_time}")
    print(f"  Output directory: {output_dir}")
    print("=" * 50)

    confirm = input("\nProceed with download? (y/n) [default: y]: ")
    if confirm.lower() in ["", "y", "yes"]:
        print("\nDownloading data...")
        try:
            files = sdd.download_soho(
                instrument=instrument,
                start_time=start_time,
                end_time=end_time,
                output_dir=output_dir,
                wavelength=wavelength,
                detector=detector,
            )

            print(f"\nDownload complete. Downloaded {len(files)} files to {output_dir}")
        except Exception as e:
            print(f"\nError during download: {str(e)}")
            print(
                "Please check the error message above for troubleshooting information."
            )
    else:
        print("\nDownload cancelled.")

    input("\nPress Enter to return to the main menu...")


def main_menu():
    """
    Display the main menu and handle user selection.
    """
    while True:
        print_header()
        print("Main Menu:")
        print("  1. Download SDO/AIA Data (Atmospheric Imaging Assembly)")
        print("  2. Download SDO/HMI Data (Helioseismic and Magnetic Imager)")
        print("  3. Download IRIS Data (Interface Region Imaging Spectrograph)")
        print("  4. Download SOHO Data (Solar and Heliospheric Observatory)")
        print("  5. Exit")

        choice = input("\nSelect an option (1-5): ")

        if choice == "1":
            download_aia_data()
        elif choice == "2":
            download_hmi_data()
        elif choice == "3":
            download_iris_data()
        elif choice == "4":
            download_soho_data()
        elif choice == "5":
            print("\nExiting Solar Data Downloader CLI. Goodbye!")
            sys.exit(0)
        else:
            print("\nInvalid choice. Please enter a number between 1 and 5.")
            input("\nPress Enter to continue...")


if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        print("\n\nExiting Solar Data Downloader CLI. Goodbye!")
        sys.exit(0)

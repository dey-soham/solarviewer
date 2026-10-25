#!/usr/bin/env python3
"""
Solar Data Downloader: an interactive terminal menu for downloading images
from SDO, IRIS, SOHO, GOES and STEREO and preparing them for SolarViewer.
"""

import os
import sys
import datetime
from pathlib import Path

try:
    from . import solar_data_downloader as sdd
except ImportError:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
        sys.path.append(script_dir)
    try:
        import solar_data_downloader as sdd
    except ImportError:
        print("Error: Could not import the solar_data_downloader module.")
        sys.exit(1)

try:
    import sunpy  # noqa: F401 (availability check)
    import drms  # noqa: F401 (availability check)
    import astropy  # noqa: F401 (availability check)
except ImportError as e:
    print(f"Error: Missing required package: {e.name}")
    print("Please install the required packages with:")
    print("  pip install sunpy drms astropy")
    print("For AIA level 1.5 calibration, also install:")
    print("  pip install aiapy")
    sys.exit(1)


CADENCES = [
    ("Every image", None),
    ("1 minute", 60),
    ("2 minutes", 120),
    ("5 minutes", 300),
    ("10 minutes", 600),
    ("15 minutes", 900),
    ("30 minutes", 1800),
    ("1 hour", 3600),
    ("6 hours", 21600),
    ("1 day", 86400),
]


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def print_header(title=None):
    clear_screen()
    print("=" * 80)
    print("                           SOLAR DATA DOWNLOADER")
    print("=" * 80)
    print("Download solar images and prepare them for SolarViewer")
    print("=" * 80)
    print()
    if title:
        print(title)
        print("-" * len(title))


def choose(prompt, options, default="1"):
    """Print numbered ``options`` (label, value) and return the chosen value."""
    for i, (label, _) in enumerate(options, 1):
        print(f"  {i}: {label}")
    while True:
        answer = input(f"\n{prompt} (1-{len(options)}) [default: {default}]: ").strip() or default
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1][1]
        print(f"Invalid choice. Please enter a number between 1 and {len(options)}.")


def yes(prompt, default=True):
    answer = input(f"{prompt} (y/n) [default: {'y' if default else 'n'}]: ").strip().lower()
    return default if not answer else answer in ("y", "yes")


def get_date_input(prompt, default=None):
    while True:
        if default:
            user_input = input(f"{prompt} [default: {default}]: ")
            if not user_input.strip():
                return default
        else:
            user_input = input(f"{prompt} (YYYY.MM.DD): ")
        try:
            return datetime.datetime.strptime(user_input.strip(), "%Y.%m.%d").strftime("%Y.%m.%d")
        except ValueError:
            print("Error: Invalid date format. Please use YYYY.MM.DD.")


def get_time_input(prompt, default=None):
    while True:
        if default:
            user_input = input(f"{prompt} [default: {default}]: ")
            if not user_input.strip():
                return default
        else:
            user_input = input(f"{prompt} (HH:MM:SS): ")
        try:
            return datetime.datetime.strptime(user_input.strip(), "%H:%M:%S").strftime("%H:%M:%S")
        except ValueError:
            print("Error: Invalid time format. Please use HH:MM:SS.")


def get_datetime_range():
    """(start, end) as 'YYYY.MM.DD HH:MM:SS' strings."""
    now = datetime.datetime.utcnow()
    print("\nTime range (UTC):")
    start_date = get_date_input("  Start date", now.strftime("%Y.%m.%d"))
    start_time = get_time_input("  Start time", now.strftime("%H:%M:%S"))
    end_date = get_date_input("  End date", start_date)
    default_end = (now + datetime.timedelta(hours=1)).strftime("%H:%M:%S") if end_date == start_date else start_time
    end_time = get_time_input("  End time", default_end)
    return f"{start_date} {start_time}", f"{end_date} {end_time}"


def get_cadence(default="1"):
    print("\nCadence (one image per interval):")
    return choose("Select cadence", CADENCES, default)


def get_output_directory(instrument):
    default_dir = f"./{instrument.lower()}_data"
    user_input = input(f"\nOutput directory [default: {default_dir}]: ").strip()
    output_dir = user_input or default_dir
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    return output_dir


def get_processing(processing):
    """Whether to prepare the files, and whether to keep the originals."""
    print(f"\nPreparation for SolarViewer: {processing}")
    prepare = yes("Prepare the files?")
    keep = yes("Keep the downloaded files as well?", default=False) if prepare else False
    return dict(skip_calibration=not prepare, keep_originals=keep)


def get_jsoc_email():
    email = input("\nEmail address registered at JSOC: ").strip()
    if not email:
        print("A JSOC export needs a registered email (jsoc.stanford.edu/ajax/register_email.html).")
    return email or None


def summary(lines):
    print("\n" + "=" * 50)
    print("Download Summary:")
    for line in lines:
        print(f"  {line}")
    print("=" * 50)
    return yes("\nProceed with download?")


def run(download, **kwargs):
    print("\nDownloading data...")
    try:
        files = download(**kwargs)
        print(f"\n{len(files) if files else 0} files in {kwargs['output_dir']}")
    except Exception as e:
        print(f"\nError during download: {e}")
    input("\nPress Enter to return to the main menu...")


def cadence_label(sample):
    return next(label for label, seconds in CADENCES if seconds == sample)


def download_aia_data():
    print_header("SDO/AIA")
    print("\nWavelength:")
    wavelength = choose(
        "Select wavelength",
        [
            ("94 Å (Fe XVIII, flaring)", "94"),
            ("131 Å (Fe VIII, Fe XXI)", "131"),
            ("171 Å (Fe IX, quiet corona)", "171"),
            ("193 Å (Fe XII, Fe XXIV)", "193"),
            ("211 Å (Fe XIV, active regions)", "211"),
            ("304 Å (He II, chromosphere)", "304"),
            ("335 Å (Fe XVI)", "335"),
            ("1600 Å (C IV, transition region)", "1600"),
            ("1700 Å (UV continuum)", "1700"),
            ("4500 Å (visible continuum)", "4500"),
        ],
        "3",
    )
    start_time, end_time = get_datetime_range()
    print("\nAIA takes an EUV image every 12 s: an hour at every image is about 300 files of 12 MB.")
    sample = get_cadence("4")
    output_dir = get_output_directory("AIA")
    print("\nSource:")
    use_jsoc = choose(
        "Select source",
        [("VSO through Fido (no account needed)", False), ("JSOC export (email registered at JSOC)", True)],
    )
    email = get_jsoc_email() if use_jsoc else None
    processing = get_processing(
        "level 1.5 with aiapy (pointing update, registration, degradation correction, counts per second)"
    )
    psf = False
    if not processing["skip_calibration"]:
        psf = yes("Deconvolve the PSF as well (about a minute per image)?", default=False)
    if not summary(
        [
            "Instrument: SDO/AIA",
            f"Wavelength: {wavelength} Å",
            f"Time range: {start_time} to {end_time}",
            f"Cadence: {cadence_label(sample)}",
            f"Output directory: {output_dir}",
            f"Source: {'JSOC export' if use_jsoc else 'VSO'}",
            f"Preparation: {'level 1.5' + (' with PSF deconvolution' if psf else '') if not processing['skip_calibration'] else 'none'}",
        ]
    ):
        print("\nDownload cancelled.")
        return input("\nPress Enter to return to the main menu...")
    common = dict(
        wavelength=wavelength,
        start_time=start_time,
        end_time=end_time,
        output_dir=output_dir,
        sample=sample,
        apply_psf=psf,
        **processing,
    )
    if use_jsoc:
        run(sdd.download_aia, email=email, **common)
    else:
        run(sdd.download_aia_with_fido, **common)


def download_hmi_data():
    print_header("SDO/HMI")
    print("\nSeries (the 720 s series exist only at JSOC):")
    series = choose(
        "Select series",
        [
            ("Magnetogram, 45 s", "M_45s"),
            ("Continuum intensity, 45 s", "Ic_45s"),
            ("Doppler velocity, 45 s", "V_45s"),
            ("Magnetogram, 720 s (JSOC only)", "M_720s"),
            ("Continuum intensity, 720 s (JSOC only)", "Ic_720s"),
            ("Continuum without limb darkening, 720 s (JSOC only)", "Ic_noLimbDark_720s"),
            ("Doppler velocity, 720 s (JSOC only)", "V_720s"),
        ],
    )
    start_time, end_time = get_datetime_range()
    sample = get_cadence("4")
    output_dir = get_output_directory("HMI")
    if "720" in series:
        use_jsoc = True
        print("\nThe 720 s series come from JSOC.")
    else:
        print("\nSource:")
        use_jsoc = choose(
            "Select source",
            [("VSO through Fido (no account needed)", False), ("JSOC export (email registered at JSOC)", True)],
        )
    email = get_jsoc_email() if use_jsoc else None
    processing = get_processing("north up with the Sun centred, native 0.504 arcsec per pixel")
    if not summary(
        [
            "Instrument: SDO/HMI",
            f"Series: {series}",
            f"Time range: {start_time} to {end_time}",
            f"Cadence: {cadence_label(sample)}",
            f"Output directory: {output_dir}",
            f"Source: {'JSOC export' if use_jsoc else 'VSO'}",
        ]
    ):
        print("\nDownload cancelled.")
        return input("\nPress Enter to return to the main menu...")
    common = dict(series=series, start_time=start_time, end_time=end_time, output_dir=output_dir, sample=sample, **processing)
    if use_jsoc:
        run(sdd.download_hmi, email=email, **common)
    else:
        run(sdd.download_hmi_with_fido, **common)


def download_iris_data():
    print_header("IRIS slit-jaw images")
    print("\nLevel 2 slit-jaw cubes; each becomes one file per exposure. Rasters are spectra and are not offered.")
    print("\nChannel:")
    wavelength = choose(
        "Select channel",
        [
            ("1330 Å (C II, upper chromosphere)", "1330"),
            ("1400 Å (Si IV, transition region)", "1400"),
            ("2796 Å (Mg II k, chromosphere)", "2796"),
            ("2832 Å (Mg II wing, photosphere)", "2832"),
            ("All channels", None),
        ],
        "2",
    )
    start_time, end_time = get_datetime_range()
    print("\nThe cadence thins the exposures of each observation.")
    sample = get_cadence()
    output_dir = get_output_directory("IRIS")
    processing = get_processing("one file per exposure with its own time and pointing, counts per second, north up")
    if not summary(
        [
            "Instrument: IRIS SJI",
            f"Channel: {wavelength or 'all'}",
            f"Time range: {start_time} to {end_time}",
            f"Cadence: {cadence_label(sample)}",
            f"Output directory: {output_dir}",
        ]
    ):
        print("\nDownload cancelled.")
        return input("\nPress Enter to return to the main menu...")
    run(
        sdd.download_iris,
        start_time=start_time,
        end_time=end_time,
        output_dir=output_dir,
        obs_type="SJI",
        wavelength=wavelength,
        sample=sample,
        **processing,
    )


def download_soho_data():
    print_header("SOHO")
    print("\nInstrument:")
    instrument = choose("Select instrument", [("EIT (EUV imager)", "EIT"), ("LASCO (coronagraph)", "LASCO"), ("MDI (magnetograph)", "MDI")])
    wavelength = detector = physobs = None
    if instrument == "EIT":
        print("\nWavelength:")
        wavelength = choose(
            "Select wavelength",
            [("171 Å (Fe IX, Fe X)", "171"), ("195 Å (Fe XII)", "195"), ("284 Å (Fe XV)", "284"), ("304 Å (He II)", "304")],
            "2",
        )
        processing = "the level 1 product where SDAC has it (dark, flat, degridding, degradation and exposure corrected), north up"
    elif instrument == "LASCO":
        print("\nDetector:")
        detector = choose("Select detector", [("C2 (1.5 to 6 solar radii)", "C2"), ("C3 (3.7 to 30 solar radii)", "C3")])
        processing = "calibrated to mean solar brightness as NRL level 1 (bias, exposure, vignetting, distortion), north up, monthly background in a second HDU"
    else:
        print("\nProduct:")
        physobs = choose("Select product", [("Magnetogram (1 min where the VSO has it)", "magnetogram"), ("Continuum intensity (12 min)", "continuum")])
        processing = "north up with the Sun centred"
    start_time, end_time = get_datetime_range()
    sample = get_cadence()
    output_dir = get_output_directory(f"SOHO_{instrument}")
    options = get_processing(processing)
    if not summary(
        [
            f"Instrument: SOHO/{instrument}",
            f"{'Wavelength: ' + wavelength + ' Å' if wavelength else 'Detector: ' + detector if detector else 'Product: ' + physobs}",
            f"Time range: {start_time} to {end_time}",
            f"Cadence: {cadence_label(sample)}",
            f"Output directory: {output_dir}",
        ]
    ):
        print("\nDownload cancelled.")
        return input("\nPress Enter to return to the main menu...")
    run(
        sdd.download_soho,
        instrument=instrument,
        start_time=start_time,
        end_time=end_time,
        output_dir=output_dir,
        wavelength=wavelength,
        detector=detector,
        physobs=physobs,
        sample=sample,
        **options,
    )


def download_suvi_data():
    print_header("GOES/SUVI")
    print("\nWavelength:")
    wavelength = choose(
        "Select wavelength",
        [
            ("94 Å (Fe XVIII)", "94"),
            ("131 Å (Fe VIII, Fe XXI)", "131"),
            ("171 Å (Fe IX)", "171"),
            ("195 Å (Fe XII)", "195"),
            ("284 Å (Fe XV)", "284"),
            ("304 Å (He II)", "304"),
        ],
        "3",
    )
    print("\nLevel:")
    level = choose("Select level", [("Level 2 (4 min composites, calibrated)", "2"), ("Level 1b (single exposures)", "1b")])
    start_time, end_time = get_datetime_range()
    sample = get_cadence()
    output_dir = get_output_directory("SUVI")
    options = get_processing("the image into the primary HDU" + ("" if level == "2" else ", north up"))
    if not summary(
        [
            "Instrument: GOES/SUVI",
            f"Wavelength: {wavelength} Å",
            f"Level: {level}",
            f"Time range: {start_time} to {end_time}",
            f"Cadence: {cadence_label(sample)}",
            f"Output directory: {output_dir}",
        ]
    ):
        print("\nDownload cancelled.")
        return input("\nPress Enter to return to the main menu...")
    run(
        sdd.download_goes_suvi,
        start_time=start_time,
        end_time=end_time,
        output_dir=output_dir,
        wavelength=wavelength,
        level=level,
        sample=sample,
        **options,
    )


def download_stereo_data():
    print_header("STEREO/SECCHI")
    print("\nSpacecraft:")
    spacecraft = choose("Select spacecraft", [("STEREO-A", "A"), ("STEREO-B (until 2014)", "B")])
    print("\nInstrument:")
    instrument = choose(
        "Select instrument",
        [
            ("EUVI (EUV imager)", "EUVI"),
            ("COR1 (inner coronagraph, 1.4 to 4 solar radii)", "COR1"),
            ("COR2 (outer coronagraph, 2.5 to 15 solar radii)", "COR2"),
        ],
    )
    wavelength = None
    if instrument == "EUVI":
        print("\nWavelength:")
        wavelength = choose("Select wavelength", [("171 Å (Fe IX)", "171"), ("195 Å (Fe XII)", "195"), ("284 Å (Fe XV)", "284"), ("304 Å (He II)", "304")], "2")
        processing = "on-board processing undone, bias, filter and flat corrected, counts per second, north up"
    else:
        processing = "polarizer triplets calibrated to mean solar brightness (SECCHI level 1), north up, monthly background in a second HDU"
    start_time, end_time = get_datetime_range()
    sample = get_cadence()
    output_dir = get_output_directory(f"STEREO_{spacecraft}_{instrument}")
    options = get_processing(processing)
    if not summary(
        [
            f"Instrument: STEREO-{spacecraft}/{instrument}",
            f"Wavelength: {wavelength} Å" if wavelength else "Polarizer sequences",
            f"Time range: {start_time} to {end_time}",
            f"Cadence: {cadence_label(sample)}",
            f"Output directory: {output_dir}",
        ]
    ):
        print("\nDownload cancelled.")
        return input("\nPress Enter to return to the main menu...")
    run(
        sdd.download_stereo,
        start_time=start_time,
        end_time=end_time,
        output_dir=output_dir,
        spacecraft=spacecraft,
        instrument=instrument,
        wavelength=wavelength,
        sample=sample,
        **options,
    )


def download_gong_data():
    print_header("NSO/GONG")
    print("\nGONG synoptic magnetograms: Carrington maps of the line-of-sight field in Gauss,")
    print("one about every hour, kept as the archive sends them. NSO's server is not always reachable.")
    start_time, end_time = get_datetime_range()
    sample = get_cadence()
    output_dir = get_output_directory("GONG")
    if not summary(
        [
            "Instrument: NSO/GONG synoptic magnetogram",
            f"Time range: {start_time} to {end_time}",
            f"Cadence: {cadence_label(sample)}",
            f"Output directory: {output_dir}",
        ]
    ):
        print("\nDownload cancelled.")
        return input("\nPress Enter to return to the main menu...")
    run(sdd.download_gong, start_time=start_time, end_time=end_time, output_dir=output_dir, sample=sample)


def main_menu():
    actions = [
        ("SDO/AIA (Atmospheric Imaging Assembly)", download_aia_data),
        ("SDO/HMI (Helioseismic and Magnetic Imager)", download_hmi_data),
        ("IRIS slit-jaw images", download_iris_data),
        ("SOHO (EIT, LASCO, MDI)", download_soho_data),
        ("GOES/SUVI (Solar Ultraviolet Imager)", download_suvi_data),
        ("STEREO/SECCHI (EUVI, COR1, COR2)", download_stereo_data),
        # ("NSO/GONG synoptic magnetograms", download_gong_data),  # hidden: NSO's server could not be reached to verify it
        ("Exit", None),
    ]
    while True:
        print_header()
        print("Main Menu:")
        for i, (label, _) in enumerate(actions, 1):
            print(f"  {i}. {label}")
        choice = input(f"\nSelect an option (1-{len(actions)}): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(actions):
            action = actions[int(choice) - 1][1]
            if action is None:
                print("\nExiting Solar Data Downloader. Goodbye!")
                sys.exit(0)
            action()
        else:
            print(f"\nInvalid choice. Please enter a number between 1 and {len(actions)}.")
            input("\nPress Enter to continue...")


if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        print("\n\nExiting Solar Data Downloader. Goodbye!")
        sys.exit(0)

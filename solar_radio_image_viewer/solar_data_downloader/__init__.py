"""
Solar Data Downloader

Downloads images from SDO (AIA, HMI), IRIS, SOHO (EIT, LASCO, MDI), GOES
(SUVI) and STEREO (EUVI, COR1, COR2) and prepares them for SolarViewer.

- solar_data_downloader.py: the download and preparation functions
- lasco_calibration.py: LASCO C2/C3 level 0.5 to level 1 (mean solar
  brightness) with NRL's monthly background in a second HDU; also a
  command line, see LASCO_CALIBRATION.md
- secchi_calibration.py: STEREO COR1/COR2 polarizer triplets to total
  brightness in mean solar brightness with the SECCHI team's monthly
  background in a second HDU, EUVI corrected to DN/s; also a command
  line, see SECCHI_CALIBRATION.md
- solar_data_downloader_cli.py: an interactive terminal menu
- solar_data_downloader_gui.py: the window
"""

# Importing these takes seconds (sunpy, aiapy), so the package itself imports
# nothing: the viewer's menu reaches launch_gui at once, and it can show the wait
_FUNCTIONS = {
    "calibrate_lasco_file": ("lasco_calibration", "calibrate_file"),
    "calibrate_secchi_files": ("secchi_calibration", "calibrate_files"),
    **{
        name: ("solar_data_downloader", name)
        for name in (
            "download_aia",
            "download_aia_with_fido",
            "download_goes_suvi",
            "download_gong",
            "download_hmi",
            "download_hmi_with_fido",
            "download_iris",
            "download_soho",
            "download_stereo",
            "prepare_files",
        )
    },
}
_LIBRARIES = ("numpy", "astropy.io.fits", "astropy.units", "sunpy", "drms", __name__ + ".solar_data_downloader")

__all__ = [*sorted(_FUNCTIONS), "launch_gui"]


def __getattr__(name):
    if name not in _FUNCTIONS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    module, function = _FUNCTIONS[name]
    return getattr(import_module(f"{__name__}.{module}"), function)


def launch_gui(parent=None, initial_datetime=None):
    """Show the Solar Data Downloader window (see the function of that name
    in solar_data_downloader_gui), or nothing if its loading is cancelled."""
    from ..ui.download_parts import load_downloader

    gui = load_downloader(
        parent, "Loading the solar data downloader…", _LIBRARIES, __name__ + ".solar_data_downloader_gui"
    )
    return gui and gui.launch_gui(parent, initial_datetime=initial_datetime)

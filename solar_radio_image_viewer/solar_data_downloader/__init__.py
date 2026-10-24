"""
Solar Data Downloader

Downloads images from SDO (AIA, HMI), IRIS, SOHO (EIT, LASCO, MDI), GOES
(SUVI) and STEREO (EUVI, COR1, COR2) and prepares them for SolarViewer.

- solar_data_downloader.py: the download and preparation functions
- lasco_calibration.py: LASCO C2/C3 level 0.5 to level 1 (mean solar
  brightness) with NRL's monthly background in a second HDU; also a
  command line, see LASCO_CALIBRATION.md
- solar_data_downloader_cli.py: an interactive terminal menu
- solar_data_downloader_gui.py: the window
"""

from .solar_data_downloader import (
    download_aia,
    download_aia_with_fido,
    download_goes_suvi,
    download_gong,
    download_hmi,
    download_hmi_with_fido,
    download_iris,
    download_soho,
    download_stereo,
    prepare_files,
)

from .lasco_calibration import calibrate_file as calibrate_lasco_file
from .solar_data_downloader_gui import launch_gui

__all__ = [
    "calibrate_lasco_file",
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
    "launch_gui",
]

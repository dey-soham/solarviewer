"""
Solar Data Downloader

Downloads images from SDO (AIA, HMI), IRIS, SOHO (EIT, LASCO, MDI), GOES
(SUVI) and STEREO (EUVI, COR1, COR2) and prepares them for SolarViewer.

- solar_data_downloader.py: the download and preparation functions
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

from .solar_data_downloader_gui import launch_gui

__all__ = [
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

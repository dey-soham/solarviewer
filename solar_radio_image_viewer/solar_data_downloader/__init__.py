"""
Solar Data Downloader Package

This package provides tools for downloading and processing solar data from various observatories:
- SDO/AIA (Atmospheric Imaging Assembly)
- SDO/HMI (Helioseismic and Magnetic Imager)
- IRIS (Interface Region Imaging Spectrograph)
- SOHO (Solar and Heliospheric Observatory)

The package includes:
- Core downloader module (solar_data_downloader.py)
- Command-line interface (solar_data_downloader_cli.py)
- Graphical user interface (solar_data_downloader_gui.py)
"""

from .solar_data_downloader import (
    download_aia,
    download_aia_with_fido,
    download_hmi,
    download_hmi_with_fido,
    download_iris,
    download_soho,
)

from .solar_data_downloader_gui import launch_gui

__all__ = [
    "download_aia",
    "download_aia_with_fido",
    "download_hmi",
    "download_hmi_with_fido",
    "download_iris",
    "download_soho",
    "launch_gui",
]

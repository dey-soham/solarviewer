"""
Radio Data Downloader Package

Downloads solar radio dynamic spectra and writes them as FITS files for the
Dynamic Spectrum Viewer:
- RSTN spectrographs: Learmonth, San Vito, Palehua, Sagamore Hill, Holloman
- the e-CALLISTO network of spectrometers

The package includes:
- Core downloader module (radio_data_downloader.py)
- Graphical user interface (radio_data_downloader_gui.py)
"""

# Importing these takes a second or more (pandas, scipy), so the package
# itself imports nothing: the viewer's menu reaches launch_gui at once, and it
# can show the wait
_FUNCTIONS = ("download_learmonth", "srs_to_dataframe", "dataframe_to_fits")
_LIBRARIES = ("numpy", "pandas", "scipy.interpolate", "scipy.signal", __name__ + ".radio_data_downloader")

__all__ = [*_FUNCTIONS, "launch_gui"]


def __getattr__(name):
    if name not in _FUNCTIONS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(__name__ + ".radio_data_downloader"), name)


def launch_gui(parent=None, initial_datetime=None):
    """Show the Radio Data Downloader window (see the function of that name
    in radio_data_downloader_gui), or nothing if its loading is cancelled."""
    from ..ui.download_parts import load_downloader

    gui = load_downloader(
        parent, "Loading the radio data downloader…", _LIBRARIES, __name__ + ".radio_data_downloader_gui"
    )
    return gui and gui.launch_gui(parent, initial_datetime=initial_datetime)

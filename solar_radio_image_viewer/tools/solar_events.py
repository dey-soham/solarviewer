"""
Context lookups for the current image: NOAA events and Helioviewer.
"""

import logging
from ..processing.fits_io import is_fits
from ..processing.image_meta import casa_info, casa_obs_time, fits_header
from ..ui.background import Cancelled, run_in_thread
from ..ui.busy import busy

logger = logging.getLogger(__name__)


def helioviewer_reachable():
    """Whether the Helioviewer API answers."""
    import requests

    try:
        requests.get("https://api.helioviewer.org/v2/", timeout=3)
    except requests.RequestException:
        return False
    return True


class SolarEventsMixin:
    """NOAA solar events and Helioviewer lookups for the current image's time.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``current_header``, ``imagename``, ``parent``, ``show_status_message``
    """

    def _observation_datetime(self):
        """When the current image was taken, as a datetime, or None: from its
        header (as the plot title reads it, so a LASCO date with slashes and a
        separate TIME-OBS count), its CASA coordinate system, or a date in
        its file name."""
        from ..video.creator import observation_time

        imagename = getattr(self, "imagename", None)
        if not imagename:
            return None
        header = None
        if is_fits(imagename):
            try:
                header = fits_header(imagename)
            except Exception as e:
                logger.error(f"FITS header read failed: {e}")
        else:
            try:
                t = casa_obs_time(casa_info(imagename).csys_record)
                if t is not None:
                    return t.to_datetime()
            except Exception as e:
                logger.warning(f"Could not read the CASA observation time: {e}")
        return observation_time(header, imagename)

    def show_noaa_events_for_current_image(self):
        """Show NOAA Solar Events for the current image date and auto-fetch events."""
        try:
            if not hasattr(self, "imagename") or not self.imagename:
                from PyQt5.QtWidgets import QMessageBox

                QMessageBox.information(self, "Info", "No image is currently loaded.")
                return

            # Check if splash image
            if self.imagename.endswith("splash.fits"):
                from PyQt5.QtWidgets import QMessageBox

                QMessageBox.information(self, "Info", "Please load an image first.")
                return

            with busy("Reading the observation date…", self):
                when = self._observation_datetime()
            extracted_date = when.date() if when is not None else None

            if extracted_date is None:
                from PyQt5.QtWidgets import QMessageBox

                QMessageBox.warning(
                    self,
                    "Date not found",
                    "Could not extract date from the current image.\n\n"
                    "Supported formats:\n"
                    "• FITS files with DATE-OBS header\n"
                    "• CASA images with observation date\n"
                    "• Files with date in filename (YYYYMMDD)",
                )
                return

            # Get main window to store reference
            main_window = self.window()

            # Create and show the viewer with the date
            with busy("Opening NOAA events…", self):
                from ..noaa_events.noaa_events_gui import NOAAEventsViewer

                viewer = NOAAEventsViewer(self, extracted_date)
                if main_window:
                    main_window._noaa_events_viewer = viewer
                viewer.show()

            # Auto-fetch events for the extracted date
            viewer.fetch_data()

        except Exception as e:
            from PyQt5.QtWidgets import QMessageBox

            error_message = f"Error showing NOAA Events: {str(e)}"
            logger.error(f"{error_message}")
            self.show_status_message(error_message)
            import traceback

            traceback.print_exc()
            QMessageBox.critical(self, "Error", error_message)

    def open_helioviewer_with_time(self):
        """Open Helioviewer Browser with time range from current file (FITS or CASA)."""
        try:
            from PyQt5.QtWidgets import QMessageBox

            # Fast connection check
            try:
                reachable = run_in_thread(
                    self, "Checking the connection to Helioviewer…", helioviewer_reachable
                )
            except Cancelled:
                self.show_status_message("Cancelled")
                return
            if not reachable:
                QMessageBox.warning(
                    self,
                    "Connection error",
                    "Could not connect to the Helioviewer API.\nPlease check your internet connection and try again.",
                )
                return

            from datetime import timedelta

            from PyQt5.QtCore import QDateTime

            with busy("Opening the Helioviewer browser…", self):
                from ..helioviewer_browser import HelioviewerBrowser

                obs_datetime = self._observation_datetime()

                # Set time range if we found a time
                initial_start = None
                initial_end = None

                if obs_datetime:
                    start_dt = obs_datetime - timedelta(minutes=30)
                    end_dt = obs_datetime + timedelta(minutes=30)
                    initial_start = QDateTime(start_dt)
                    initial_end = QDateTime(end_dt)
                    self.show_status_message(
                        f"Opening the Helioviewer browser for {obs_datetime} (±30 min)"
                    )
                else:
                    self.show_status_message(
                        "No observation time found in file, using default time range"
                    )

                # Launch browser
                browser = HelioviewerBrowser(
                    self, initial_start=initial_start, initial_end=initial_end
                )
                browser.show()

        except Exception as e:
            import traceback

            from PyQt5.QtWidgets import QMessageBox

            traceback.print_exc()
            QMessageBox.warning(
                self, "Error", f"Could not open the Helioviewer browser: {str(e)}"
            )

"""
Context lookups for the current image: NOAA events and Helioviewer.
"""

import logging
from datetime import datetime, time

from ..processing.fits_io import is_fits
from ..processing.image_meta import casa_info, casa_obs_time, fits_header
from ..ui.background import Cancelled, run_in_thread
from ..ui.busy import busy

logger = logging.getLogger(__name__)

HELIOVIEWER_SPAN_S = 30 * 60  # the browser opens on this much each side of the image's time


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
        its file name; a plain date if the source gives no time."""
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
        return observation_time(header, imagename, plain_date=True)

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

            if when is None:
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

            # At the image's date and time; fetches the lists if they are not here
            with busy("Opening NOAA events…", self):
                from ..noaa_events.noaa_events_gui import show_noaa_events_viewer

                viewer = show_noaa_events_viewer(self, when)
                if main_window:
                    main_window._noaa_events_viewer = viewer

        except Exception as e:
            from PyQt5.QtWidgets import QMessageBox

            error_message = f"Error showing NOAA Events: {str(e)}"
            logger.error(f"{error_message}")
            self.show_status_message(error_message)
            import traceback

            traceback.print_exc()
            QMessageBox.critical(self, "Error", error_message)

    def radio_field(self):
        """The image's field ``(x1, y1, x2, y2)`` in arcsec, for a solar
        (helioprojective) image; None for any other, or without a sky frame."""
        from ..core import compare_sync

        data = getattr(self, "current_image_data", None)
        if data is None or compare_sync.frame_kind(self) != "solar":
            return None
        height, width = data.shape[:2]
        one = compare_sync.to_world(self, -0.5, -0.5)
        other = compare_sync.to_world(self, width - 0.5, height - 0.5)
        if one is None or other is None:
            return None
        return one[0] * 3600, one[1] * 3600, other[0] * 3600, other[1] * 3600

    def open_helioviewer_with_time(self):
        """Open the Helioviewer browser on the image's time: its images half
        an hour each side, the image's field boxed and its time marked."""
        import os

        from PyQt5.QtWidgets import QMessageBox

        from .. import helioviewer_api as hv

        try:
            reachable = run_in_thread(self, "Checking the connection to Helioviewer…", hv.helioviewer_reachable)
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
        try:
            with busy("Opening the Helioviewer browser…", self):
                from ..helioviewer_browser import open_helioviewer

                when = self._observation_datetime()
                if when is not None and not isinstance(when, datetime):
                    when = datetime.combine(when, time())
                mark = None
                if when is not None:
                    mark = ("Radio image", when)
                    self.show_status_message(f"Opening the Helioviewer browser around {when:%Y-%m-%d %H:%M:%S} UTC")
                else:
                    self.show_status_message("The image has no observation time: the browser opens on the last range used")
                name = getattr(self, "imagename", None) or ""
                open_helioviewer(
                    self,
                    at=when,
                    span=HELIOVIEWER_SPAN_S if when is not None else None,
                    fov=self.radio_field(),
                    mark=mark,
                    mark_tip=os.path.basename(name),
                )
        except Exception as e:
            logger.error("Could not open the Helioviewer browser: %s", e, exc_info=True)
            from ..ui.errors import show_error

            show_error(self, "Could not open the Helioviewer browser", "The window could not be opened.", e)

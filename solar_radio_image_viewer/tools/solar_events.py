"""
Context lookups for the current image: NOAA events and Helioviewer.
"""

import logging
from ..processing.fits_io import is_fits, readable_image_path
from ..processing.image_meta import casa_obs_time

logger = logging.getLogger(__name__)


class SolarEventsMixin:
    """NOAA solar events and Helioviewer lookups for the current image's time.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``current_header``, ``imagename``, ``parent``, ``show_status_message``
    """

    def show_noaa_events_for_current_image(self):
        """Show NOAA Solar Events for the current image date and auto-fetch events."""
        try:
            from datetime import date, datetime

            from ..noaa_events.noaa_events_gui import NOAAEventsViewer

            if not hasattr(self, "imagename") or not self.imagename:
                from PyQt5.QtWidgets import QMessageBox

                QMessageBox.information(self, "Info", "No image is currently loaded.")
                return

            # Check if splash image
            if self.imagename.endswith("splash.fits"):
                from PyQt5.QtWidgets import QMessageBox

                QMessageBox.information(self, "Info", "Please load an image first.")
                return

            imagename = self.imagename
            extracted_date = None

            # Method 1: FITS header
            if is_fits(imagename):
                try:
                    from astropy.io import fits

                    header = fits.getheader(readable_image_path(imagename))

                    # Check DATE-OBS (standard), DATE_OBS (IRIS), and STARTOBS
                    image_time = (
                        header.get("DATE-OBS")
                        or header.get("DATE_OBS")
                        or header.get("STARTOBS")
                    )

                    # Special handling for SOHO (DATE-OBS + TIME-OBS)
                    if (
                        header.get("TELESCOP") == "SOHO"
                        and header.get("TIME-OBS")
                        and image_time
                    ):
                        image_time = f"{image_time}T{header['TIME-OBS']}"

                    if image_time:
                        image_time = str(image_time)
                        if "T" in image_time:
                            clean_str = (
                                image_time.replace("Z", "").split("+")[0].split(".")[0]
                            )
                            if "-" in clean_str[11:]:
                                clean_str = clean_str[:19]
                            try:
                                extracted_date = datetime.fromisoformat(
                                    clean_str
                                ).date()
                            except ValueError:
                                date_part = clean_str.split("T")[0]
                                if len(date_part) >= 10:
                                    extracted_date = datetime.strptime(
                                        date_part[:10], "%Y-%m-%d"
                                    ).date()
                        elif "-" in image_time and len(image_time) >= 10:
                            extracted_date = datetime.strptime(
                                image_time[:10], "%Y-%m-%d"
                            ).date()
                except Exception as fits_err:
                    logger.error(f"FITS header read failed: {fits_err}")
                    self.show_status_message(f"FITS header read failed: {fits_err}")

            # Method 2: CASA image
            if extracted_date is None:
                is_casa_image = not is_fits(imagename)

                if is_casa_image:
                    try:
                        from astropy.time import Time
                        from casatools import image as IA

                        ia_tool = IA()
                        ia_tool.open(readable_image_path(imagename))
                        csys_record = ia_tool.coordsys().torecord()
                        ia_tool.close()

                        if "obsdate" in csys_record:
                            obsdate = csys_record["obsdate"]
                            m0 = obsdate.get("m0", {})
                            time_value = m0.get("value", None)
                            time_unit = m0.get("unit", None)
                            refer = obsdate.get("refer", None)

                            if (refer == "UTC" or time_unit == "d") and time_value:
                                t = Time(time_value, format="mjd")
                                extracted_date = t.to_datetime().date()
                    except Exception as casa_err:
                        logger.error(f"CASA date extraction failed: {casa_err}")
                        self.show_status_message(
                            f"CASA date extraction failed: {casa_err}"
                        )

            # Method 3: Filename parsing
            if extracted_date is None:
                import re

                patterns = [
                    r"(\d{4})(\d{2})(\d{2})",  # YYYYMMDD
                    r"(\d{4})-(\d{2})-(\d{2})",  # YYYY-MM-DD
                    r"(\d{4})\.(\d{2})\.(\d{2})",  # YYYY.MM.DD
                ]
                for pattern in patterns:
                    match = re.search(pattern, imagename)
                    if match:
                        try:
                            y, m, d = (
                                int(match.group(1)),
                                int(match.group(2)),
                                int(match.group(3)),
                            )
                            if 1990 < y < 2100 and 1 <= m <= 12 and 1 <= d <= 31:
                                extracted_date = date(y, m, d)
                                break
                        except (ValueError, IndexError):
                            continue

            if extracted_date is None:
                from PyQt5.QtWidgets import QMessageBox

                QMessageBox.warning(
                    self,
                    "Date Not Found",
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
            import requests
            from PyQt5.QtWidgets import QMessageBox

            # Fast connection check
            try:
                requests.get("https://api.helioviewer.org/v2/", timeout=3)
            except requests.RequestException:
                QMessageBox.warning(
                    self,
                    "Connection Error",
                    "Could not connect to the Helioviewer API.\nPlease check your internet connection and try again.",
                )
                return

            from datetime import datetime, timedelta

            from PyQt5.QtCore import QDateTime

            from ..helioviewer_browser import HelioviewerBrowser

            obs_datetime = None

            # Try FITS header (using same logic as update_figure_title)
            if hasattr(self, "current_header") and self.current_header:
                # Try multiple header keywords in order of preference
                for key in [
                    "DATE-OBS",
                    "DATE_OBS",
                    "DATE",
                    "TRECVD",
                    "T_OBS",
                    "SIMPLE_TIME",
                ]:
                    if key in self.current_header:
                        date_str = str(self.current_header[key]).strip()
                        try:
                            # ISO format with T separator
                            if "T" in date_str:
                                # Handle 'Z' timezone and fractional seconds
                                date_str_clean = date_str.replace("Z", "+00:00")
                                # Split off timezone if present
                                if "+" in date_str_clean:
                                    date_str_clean = date_str_clean.split("+")[0]
                                obs_datetime = datetime.fromisoformat(date_str_clean)
                            # Date with dashes
                            elif "-" in date_str and len(date_str) >= 10:
                                # Try various formats
                                for fmt in [
                                    "%Y-%m-%d %H:%M:%S.%f",
                                    "%Y-%m-%d %H:%M:%S",
                                    "%Y-%m-%d",
                                ]:
                                    try:
                                        obs_datetime = datetime.strptime(
                                            date_str[: len(fmt)], fmt
                                        )
                                        break
                                    except ValueError:
                                        continue
                            # Date with slashes
                            elif "/" in date_str:
                                for fmt in [
                                    "%Y/%m/%d %H:%M:%S.%f",
                                    "%Y/%m/%d %H:%M:%S",
                                    "%Y/%m/%d",
                                ]:
                                    try:
                                        obs_datetime = datetime.strptime(
                                            date_str[: len(fmt)], fmt
                                        )
                                        break
                                    except ValueError:
                                        continue

                            if obs_datetime:
                                break
                        except (ValueError, TypeError) as e:
                            logger.warning(f"Failed to parse {key}={date_str}: {e}")
                            self.show_status_message(
                                f"Failed to parse {key}={date_str}: {e}"
                            )
                            continue

            # Fallback: Try the simpler parsing method used by solar activity button
            # This handles some edge cases with fractional seconds that fail in some Python versions
            if not obs_datetime and hasattr(self, "imagename") and self.imagename:
                if is_fits(self.imagename):
                    try:
                        from astropy.io import fits as fits_fallback

                        header = fits_fallback.getheader(readable_image_path(self.imagename))
                        image_time = (
                            header.get("DATE-OBS")
                            or header.get("DATE_OBS")
                            or header.get("STARTOBS")
                        )

                        # Special handling for SOHO (DATE-OBS + TIME-OBS)
                        if (
                            header.get("TELESCOP") == "SOHO"
                            and header.get("TIME-OBS")
                            and image_time
                        ):
                            image_time = f"{image_time}T{header['TIME-OBS']}"

                        if image_time:
                            image_time = str(image_time)
                            if "T" in image_time:
                                # More aggressive cleaning: remove Z, timezone, and fractional seconds
                                clean_str = (
                                    image_time.replace("Z", "")
                                    .split("+")[0]
                                    .split(".")[0]
                                )
                                # Handle case where there's a negative offset timezone
                                if "-" in clean_str[11:]:
                                    clean_str = clean_str[:19]
                                try:
                                    obs_datetime = datetime.fromisoformat(clean_str)
                                except ValueError:
                                    # Last resort: just extract date part
                                    date_part = clean_str.split("T")[0]
                                    if len(date_part) >= 10:
                                        obs_datetime = datetime.strptime(
                                            date_part[:10], "%Y-%m-%d"
                                        )
                            elif "-" in image_time and len(image_time) >= 10:
                                obs_datetime = datetime.strptime(
                                    image_time[:10], "%Y-%m-%d"
                                )
                    except Exception as fallback_err:
                        print(
                            f"[WARNING] Fallback time parsing also failed: {fallback_err}"
                        )
                        self.show_status_message(
                            f"Fallback time parsing failed: {fallback_err}"
                        )

            # Try CASA image metadata (using IA tool coordsys like the rest of viewer.py)
            if not obs_datetime and hasattr(self, "imagename") and self.imagename:
                if not (
                    is_fits(self.imagename)
                ):
                    try:
                        # Use IA tool to read CASA coordinate system
                        from casatools import image as IA

                        ia_tool = IA()
                        ia_tool.open(readable_image_path(self.imagename))
                        csys = ia_tool.coordsys()
                        csys_record = csys.torecord()
                        ia_tool.close()

                        # Extract obsdate from coordinate system
                        t = casa_obs_time(csys_record)
                        if t is not None:
                            obs_datetime = t.datetime
                    except Exception as e:
                        logger.warning(f"Could not extract CASA observation time: {e}")
                        import traceback

                        traceback.print_exc()

            # Set time range if we found a time
            initial_start = None
            initial_end = None

            if obs_datetime:
                start_dt = obs_datetime - timedelta(minutes=30)
                end_dt = obs_datetime + timedelta(minutes=30)
                initial_start = QDateTime(start_dt)
                initial_end = QDateTime(end_dt)
                self.show_status_message(
                    f"Opening Helioviewer Browser for {obs_datetime} (±30 min)"
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
                self, "Error", f"Could not open Helioviewer Browser: {str(e)}"
            )

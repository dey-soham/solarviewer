"""
Opening the main window's tool windows: batch conversions, phase shift,
Create Video, the solar events viewer, metadata and the log.
"""

import logging
from PyQt5.QtWidgets import (
    QMessageBox,
    QDialog,
)
from PyQt5.QtCore import Qt
from ..processing.metadata import get_image_metadata
from ..ui.errors import show_error

logger = logging.getLogger(__name__)


class WindowDialogsMixin:
    """Opens the tool windows from the menus (each kept until it is closed).

    Mixed into :class:`SolarRadioImageViewerApp`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``_open_dialogs``, ``tab_widget``
    """

    def show_log_console(self):
        """Show the embedded log console dialog."""
        try:
            from ..log_console import LogConsole

            console = LogConsole.get_instance()
            console.show()
            console.raise_()
            console.activateWindow()
        except ImportError:
            self.statusBar().showMessage("Error: Log Console module not found")
        except Exception as e:
            logger.error(f"Failed to open log console: {e}")
            self.statusBar().showMessage(f"Error opening log console: {e}")

    def show_batch_dialog(self):
        from ..dialogs import BatchProcessDialog

        dialog = BatchProcessDialog(self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(
            lambda: (
                self._open_dialogs.remove(dialog)
                if dialog in self._open_dialogs
                else None
            )
        )
        self._open_dialogs.append(dialog)
        dialog.show()

    def show_metadata(self):
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or not current_tab.imagename:
            QMessageBox.warning(self, "No Image", "No image loaded")
            return

        try:
            metadata = get_image_metadata(current_tab.imagename)
            from ..dialogs import ImageInfoDialog

            dialog = ImageInfoDialog(
                self, metadata=metadata, imagename=current_tab.imagename
            )
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            dialog.destroyed.connect(
                lambda: (
                    self._open_dialogs.remove(dialog)
                    if dialog in self._open_dialogs
                    else None
                )
            )
            self._open_dialogs.append(dialog)
            dialog.show()
        except Exception as e:
            import traceback

            show_error(
                self,
                "Metadata Unavailable",
                f"Could not read the image's metadata: {e}",
                traceback.format_exc(),
            )

    def show_phase_shift_dialog(self, batch_mode=False):
        """Show the dialog for shifting solar center to phase center"""
        current_tab = self.tab_widget.currentWidget()

        # Get current image name if available
        imagename = None
        if current_tab and hasattr(current_tab, "imagename") and current_tab.imagename:
            imagename = current_tab.imagename

        # Import and show the dialog
        from ..dialogs import PhaseShiftDialog

        dialog = PhaseShiftDialog(self, imagename, batch_mode=batch_mode)

        # Store tab reference for callback
        tab_ref = current_tab

        def on_finished(result):
            try:
                # Check if tab still exists and refresh if accepted
                if result == QDialog.Accepted and tab_ref:
                    if tab_ref in self.all_panes():
                        if tab_ref.imagename:
                            tab_ref.on_visualization_changed()
            except RuntimeError:
                pass  # Tab was deleted, ignore

        dialog.finished.connect(on_finished)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(
            lambda: (
                self._open_dialogs.remove(dialog)
                if dialog in self._open_dialogs
                else None
            )
        )
        self._open_dialogs.append(dialog)
        dialog.show()

    def show_create_video_dialog(self):
        """Show the dialog for creating videos from FITS files"""
        try:
            # Get current image name if available
            current_tab = self.tab_widget.currentWidget()
            current_file = current_tab.imagename if current_tab else None

            # Import and show dialog
            from ..video.dialog import VideoCreationDialog

            # Store reference to prevent garbage collection
            self._video_dialog = VideoCreationDialog(self, current_file)
            self._video_dialog.show()
        except Exception as e:
            # Show error message with details
            from PyQt5.QtWidgets import QMessageBox

            error_message = f"Error opening video creation dialog: {str(e)}"
            logger.error(f"{error_message}")
            QMessageBox.critical(self, "Error", error_message)

    def show_noaa_events_viewer(self):
        """Show the NOAA Solar Events Viewer."""
        try:
            from ..noaa_events import show_noaa_events_viewer
            from datetime import datetime

            # Try to get date from currently open FITS file
            initial_date = None
            current_tab = self.tab_widget.currentWidget()
            if current_tab and hasattr(current_tab, "header") and current_tab.header:
                header = current_tab.header
                # Try common date keywords
                for key in ["DATE-OBS", "DATE_OBS", "DATE", "T_OBS"]:
                    if key in header:
                        date_str = header[key]
                        try:
                            # Parse various date formats
                            if "T" in str(date_str):
                                dt = datetime.fromisoformat(
                                    str(date_str).replace("Z", "+00:00").split("+")[0]
                                )
                                initial_date = dt.date()
                            elif "-" in str(date_str):
                                initial_date = datetime.strptime(
                                    str(date_str)[:10], "%Y-%m-%d"
                                ).date()
                            elif "/" in str(date_str):
                                initial_date = datetime.strptime(
                                    str(date_str)[:10], "%Y/%m/%d"
                                ).date()
                            if initial_date:
                                break
                        except (ValueError, TypeError):
                            continue

            # Show the viewer
            self._noaa_events_viewer = show_noaa_events_viewer(self, initial_date)

        except Exception as e:
            from PyQt5.QtWidgets import QMessageBox

            error_message = f"Error opening NOAA Events Viewer: {str(e)}"
            logger.error(f"{error_message}")
            QMessageBox.critical(self, "Error", error_message)

    def show_unit_conversion_dialog(self):
        """Show the dialog for batch unit conversion"""
        try:
            from ..dialogs import UnitConversionDialog

            dialog = UnitConversionDialog(self)
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            dialog.destroyed.connect(
                lambda: (
                    self._open_dialogs.remove(dialog)
                    if dialog in self._open_dialogs
                    else None
                )
            )
            self._open_dialogs.append(dialog)
            dialog.show()
        except Exception as e:
            error_message = f"Error opening unit conversion dialog: {str(e)}"
            logger.error(f"{error_message}")
            QMessageBox.critical(self, "Error", error_message)

    def show_format_conversion_dialog(self):
        """Show the dialog for batch format conversion (CASA \u2194 FITS)"""
        try:
            from ..dialogs import FormatConversionDialog

            dialog = FormatConversionDialog(self)
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            dialog.destroyed.connect(
                lambda: (
                    self._open_dialogs.remove(dialog)
                    if dialog in self._open_dialogs
                    else None
                )
            )
            self._open_dialogs.append(dialog)
            dialog.show()
        except Exception as e:
            error_message = f"Error opening format conversion dialog: {str(e)}"
            logger.error(f"{error_message}")
            QMessageBox.critical(self, "Error", error_message)

    def show_coordinate_transformation_dialog(self):
        """Show the dialog for batch coordinate transformation"""
        try:
            # Get current image name if available
            current_tab = self.tab_widget.currentWidget()
            current_file = current_tab.imagename if current_tab else None

            # Import and show dialog
            from ..dialogs import CoordinateTransformationDialog

            dialog = CoordinateTransformationDialog(self, current_file)
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            dialog.destroyed.connect(
                lambda: (
                    self._open_dialogs.remove(dialog)
                    if dialog in self._open_dialogs
                    else None
                )
            )
            self._open_dialogs.append(dialog)
            dialog.show()
        except Exception as e:
            # Show error message with details
            error_message = f"Error opening coordinate transformation dialog: {str(e)}"
            logger.error(f"{error_message}")
            QMessageBox.critical(self, "Error", error_message)

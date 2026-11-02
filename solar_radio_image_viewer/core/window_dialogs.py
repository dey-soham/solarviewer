"""
Opening the main window's tool windows: batch conversions, phase shift,
Create Video, the solar events viewer, metadata and the log.
"""

import logging
import os
import traceback

from PyQt5.QtWidgets import (
    QMessageBox,
    QDialog,
)
from PyQt5.QtCore import Qt
from ..processing.metadata import get_image_metadata
from ..ui.background import Cancelled, run_in_background
from ..ui.busy import busy
from ..ui.errors import show_error

logger = logging.getLogger(__name__)

OPEN_FAILED = "The window could not be opened. Try again; the details say what went wrong."


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
            self.statusBar().showMessage("Could not open the log console: its module is missing")
        except Exception as e:
            logger.error(f"Failed to open log console: {e}")
            self.statusBar().showMessage(f"Could not open the log console: {e}")

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
            QMessageBox.warning(self, "No image", "No image loaded")
            return

        try:
            name = os.path.basename(current_tab.imagename.rstrip("/"))
            metadata = run_in_background(
                self,
                f"Reading the metadata of {name}…",
                get_image_metadata,
                current_tab.imagename,
            )
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
        except Cancelled:
            self.statusBar().showMessage("Cancelled")
        except Exception as e:
            show_error(
                self,
                "Metadata unavailable",
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
            with busy("Opening Create video…", self):
                from ..video.dialog import VideoCreationDialog

                # Store reference to prevent garbage collection
                self._video_dialog = VideoCreationDialog(self, current_file)
                self._video_dialog.show()
        except Exception:
            logger.exception("Could not open Create video")
            show_error(self, "Could not open Create video", OPEN_FAILED, traceback.format_exc())

    def show_noaa_events_viewer(self):
        """Show the NOAA Solar Events Viewer."""
        try:
            from ..noaa_events import show_noaa_events_viewer

            # The date and time of the open image, if there is one
            current_tab = self.tab_widget.currentWidget()
            with busy("Reading the observation date…", self):
                when = current_tab._observation_datetime() if current_tab else None

            # Show the viewer
            self._noaa_events_viewer = show_noaa_events_viewer(self, when)

        except Exception:
            logger.exception("Could not open the solar activity viewer")
            show_error(self, "Could not open the solar activity viewer", OPEN_FAILED, traceback.format_exc())

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
        except Exception:
            logger.exception("Could not open Convert units")
            show_error(self, "Could not open Convert units", OPEN_FAILED, traceback.format_exc())

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
        except Exception:
            logger.exception("Could not open Convert image format")
            show_error(self, "Could not open Convert image format", OPEN_FAILED, traceback.format_exc())

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
        except Exception:
            logger.exception("Could not open Transform coordinates")
            show_error(self, "Could not open Transform coordinates", OPEN_FAILED, traceback.format_exc())

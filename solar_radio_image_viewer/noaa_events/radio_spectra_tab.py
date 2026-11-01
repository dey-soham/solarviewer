"""
The solar events viewer's Radio Spectra tab: dynamic spectra of the day
from the radio observatories, fetched in the background.
"""

import logging
import os
from datetime import datetime
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QScrollArea,
    QMessageBox,
    QDialog,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPixmap
from ..ui.activity import ActivityLabel
from ..ui.download_parts import abandon
from ..ui.errors import show_error
from ..ui.geometry import fit_to_screen
from ..ui.styles import SPACE_M, SPACE_S
from ..ui.widgets import button_bar, dialog_layout

from .widgets import ClickableLabel, source_note
from .workers import DHSpectraWorker
logger = logging.getLogger(__name__)

NO_DATA_TEXT = {
    'wind': "No WIND/WAVES data",
    'stereo_a': "No STEREO-A data",
    'stereo_b': "No STEREO-B data",
    'norp': "No Nobeyama data",
    'orfees': "No ORFEES data",
    'nda': "No NDA data",
    'yamagawa': "No Yamagawa data",
    'hiras': "No HiRAS data",
}
EMPTY_HEIGHT = 48  # of a spectrum's place while it has no image: room for its spinner
SCROLL_HEIGHT = 180  # of a place that scrolls, holding its image and scroll bar


class RadioSpectraTabMixin:
    """Builds the Radio Spectra tab and fills it as spectra arrive.

    Mixed into :class:`NOAAEventsViewer`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``_cleanup_viewer``, ``dh_worker``, ``hiras_card``, ``hiras_image_label``,
    ``hiras_save_btn``, ``image_viewers``,
    ``nda_image_label``, ``nda_save_btn``, ``norp_image_label``,
    ``norp_save_btn``, ``orfees_image_label``, ``orfees_save_btn``,
    ``selected_date``, ``stereo_a_image_label``, ``stereo_a_save_btn``, ``stereo_b_card``,
    ``stereo_b_image_label``, ``stereo_b_save_btn``, ``tabs``,
    ``wind_image_label``, ``wind_save_btn``, ``yamagawa_card``,
    ``yamagawa_image_label``, ``yamagawa_save_btn``
    """

    def _create_radio_spectra_tab(self):
        """Radio Spectra tab: one card per instrument."""

        # Tab 6: Radio Spectra (DH-band)
        spectra_tab = QWidget()
        spectra_layout = QVBoxLayout(spectra_tab)
        spectra_layout.setContentsMargins(0, SPACE_M, 0, 0)
        spectra_layout.setSpacing(SPACE_S)

        # Status, progress and the fetch button, under the Fetch above
        spectra_controls = QHBoxLayout()
        spectra_controls.setSpacing(SPACE_S)
        self.spectra_status_label = QLabel(
            "Press Fetch spectra to download the day's radio spectra."
        )
        self.spectra_status_label.setWordWrap(True)
        self.spectra_status_label.setObjectName("Hint")
        spectra_controls.addWidget(self.spectra_status_label, 1)

        self.spectra_activity = ActivityLabel()
        self.spectra_activity.hide()
        spectra_controls.addWidget(self.spectra_activity)

        self.cancel_spectra_btn = QPushButton("Cancel")
        self.cancel_spectra_btn.clicked.connect(self.cancel_dh_spectra)
        self.cancel_spectra_btn.hide()
        spectra_controls.addWidget(self.cancel_spectra_btn)

        self.fetch_spectra_btn = QPushButton("Fetch spectra")
        self.fetch_spectra_btn.setToolTip(
            "Download WIND/WAVES and STEREO dynamic spectra for this date"
        )
        self.fetch_spectra_btn.clicked.connect(self.fetch_dh_spectra)
        spectra_controls.addWidget(self.fetch_spectra_btn)
        spectra_layout.addLayout(spectra_controls)

        # Scrollable content area for spectra images
        spectra_scroll = QScrollArea()
        spectra_scroll.setWidgetResizable(True)
        spectra_scroll.setFrameShape(QFrame.NoFrame)

        spectra_content = QWidget()
        spectra_content_layout = QVBoxLayout(spectra_content)
        spectra_content_layout.setContentsMargins(0, 0, 0, 0)
        spectra_content_layout.setSpacing(SPACE_M)

        # One card per instrument
        self._add_spectrum_card(
            spectra_content_layout,
            "wind",
            "WIND/WAVES 24 h calibrated (14 kHz–14 MHz)",
            "WIND/WAVES",
            "WIND/WAVES",
        )
        self._add_spectrum_card(
            spectra_content_layout,
            "stereo_a",
            "STEREO-A S/WAVES (10 kHz–16 MHz)",
            "STEREO-A",
            "STEREO-A",
        )
        self._add_spectrum_card(
            spectra_content_layout,
            "stereo_b",
            "STEREO-B S/WAVES (10 kHz–16 MHz)",
            "STEREO-B",
            "STEREO-B",
            keep_card=True,
            hidden=True,
        )  # STEREO-B lost contact in 2014
        self._add_spectrum_card(
            spectra_content_layout,
            "norp",
            "Nobeyama Radio Polarimeter (NORP), full day",
            "Nobeyama",
            "Nobeyama NORP",
        )
        self._add_spectrum_card(
            spectra_content_layout,
            "yamagawa",
            "NICT Yamagawa spectrograph",
            "Yamagawa",
            "Yamagawa",
            keep_card=True,
            scroll=True,
        )
        self._add_spectrum_card(
            spectra_content_layout,
            "hiras",
            "NICT HiRAS spectrograph",
            "HiRAS",
            "HiRAS",
            keep_card=True,
            scroll=True,
        )
        self._add_spectrum_card(
            spectra_content_layout,
            "orfees",
            "ORFEES (Nançay) spectrograph",
            "ORFEES",
            "ORFEES",
        )
        self._add_spectrum_card(
            spectra_content_layout,
            "nda",
            "NDA (Nançay) spectrograph",
            "NDA",
            "NDA",
        )

        spectra_content_layout.addStretch()
        spectra_scroll.setWidget(spectra_content)
        spectra_layout.addWidget(spectra_scroll)

        # Credits label
        spectra_credits_label = source_note(
            'Data sources: <a href="https://cdaw.gsfc.nasa.gov/">CDAW NASA</a>, '
            '<a href="https://solar.nro.nao.ac.jp/norp/">NoRP</a>, '
            '<a href="https://rsdb.obs-nancay.fr/">Nançay</a>, '
            '<a href="https://solobs.nict.go.jp/radio/cgi-bin/MainDisplay.pl">NICT</a>'
        )
        spectra_layout.addWidget(spectra_credits_label)

        self.tabs.addTab(spectra_tab, "Radio spectra")

    def _add_spectrum_card(
        self,
        parent_layout,
        key,
        title,
        save_name,
        viewer_title,
        scroll=False,
        hidden=False,
        keep_card=False,
    ):
        """Add a themed card with a title, a Save button and a clickable image
        label for one radio spectrograph. Creates ``self.<key>_save_btn`` and
        ``self.<key>_image_label`` (and ``self.<key>_scroll`` / ``self.<key>_card``
        when ``scroll`` / ``keep_card`` are set)."""
        card = QFrame()
        if keep_card:
            setattr(self, f"{key}_card", card)
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(SPACE_M, SPACE_S, SPACE_M, SPACE_S)

        header = QHBoxLayout()
        title_label = QLabel(title)
        title_label.setObjectName("SectionTitle")
        header.addWidget(title_label)
        header.addStretch()
        save_btn = QPushButton("Save")
        save_btn.setToolTip(f"Save {save_name} image as PNG")
        save_btn.clicked.connect(lambda checked: self.save_spectra_image(key))
        save_btn.setEnabled(False)
        setattr(self, f"{key}_save_btn", save_btn)
        header.addWidget(save_btn)
        layout.addLayout(header)

        if scroll:
            scroll_area = QScrollArea()
            scroll_area.setWidgetResizable(True)
            scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll_area.setFixedHeight(EMPTY_HEIGHT)
            scroll_area.setFrameShape(QFrame.NoFrame)
            setattr(self, f"{key}_scroll", scroll_area)

        image_label = ClickableLabel("Press Fetch spectra to see this one")
        image_label.setAlignment(Qt.AlignCenter)
        if not scroll:
            image_label.setMinimumHeight(EMPTY_HEIGHT)
        image_label.setScaledContents(False)
        image_label.setObjectName("Hint")
        image_label.setCursor(Qt.PointingHandCursor)
        image_label.setToolTip("Click to view full resolution")
        image_label.clicked.connect(lambda: self.show_local_image(key, viewer_title))
        setattr(self, f"{key}_image_label", image_label)

        if scroll:
            scroll_area.setWidget(image_label)
            layout.addWidget(scroll_area)
        else:
            layout.addWidget(image_label)
        if hidden:
            card.hide()
        parent_layout.addWidget(card)

    def _spectrum_labels(self):
        return [
            self.wind_image_label, self.stereo_a_image_label, self.stereo_b_image_label,
            self.norp_image_label, self.orfees_image_label, self.nda_image_label,
            self.yamagawa_image_label, self.hiras_image_label
        ]

    def clear_dh_spectra_ui(self):
        """Reset the radio spectra UI labels to initial state."""
        labels = self._spectrum_labels()
        buttons = [
            self.wind_save_btn, self.stereo_a_save_btn, self.stereo_b_save_btn,
            self.norp_save_btn, self.orfees_save_btn, self.nda_save_btn,
            self.yamagawa_save_btn, self.hiras_save_btn
        ]
        
        for label in labels:
            label.stop_loading()
            label.clear()
            label.setText("Press Fetch spectra to see this one")
            
        for btn in buttons:
            btn.setEnabled(False)

        self.yamagawa_scroll.setFixedHeight(EMPTY_HEIGHT)
        self.hiras_scroll.setFixedHeight(EMPTY_HEIGHT)
        self.spectra_status_label.setText("Press Fetch spectra to download the day's radio spectra.")
        self.spectra_status_label.setToolTip("")
        self._spectra_paths = {}

    def fetch_dh_spectra(self):
        """Fetch DH-band dynamic spectra (WIND/WAVES & STEREO) for the selected date."""

        if self.dh_worker and self.dh_worker.isRunning():
            return

        # Get selected date
        day = self.selected_date()
        if day is None:
            return
        selected_date = datetime(day.year, day.month, day.day, 12, 0, 0)

        # Update UI
        self.fetch_spectra_btn.setEnabled(False)
        self.cancel_spectra_btn.show()
        self.spectra_activity.show()
        self.spectra_activity.start("Starting…")
        self.spectra_status_label.setText(f"Fetching DH spectra for {day:%Y-%m-%d}…")

        # Clear previous images
        for label in self._spectrum_labels():
            label.start_loading("Loading…")
        
        # Hide irrelevant NICT card immediately
        from datetime import date
        NICT_TRANSITION_DATE = date(2016, 7, 11)
        if selected_date.date() >= NICT_TRANSITION_DATE:
            self.yamagawa_card.show()
            self.hiras_card.hide()
        else:
            self.yamagawa_card.hide()
            self.hiras_card.show()

        # Show/Hide STEREO-B based on mission timeline (lost contact Oct 2014)
        if day.year <= 2014:
            self.stereo_b_card.show()
        else:
            self.stereo_b_card.hide()

        # Start worker
        self.dh_worker = DHSpectraWorker(selected_date)
        self.dh_worker.progress.connect(self.on_dh_spectra_progress)
        self.dh_worker.result_ready.connect(self.on_dh_partial_ready)
        self.dh_worker.source_ended.connect(self.on_dh_source_ended)
        self.dh_worker.sources_done.connect(self.on_dh_sources_done)
        self.dh_worker.done.connect(self.on_dh_spectra_ready)
        self._spectra_paths = {}  # Initialize/Reset
        self._spectra_date = day
        self.dh_worker.start()

    def _spectra_ended(self):
        self.fetch_spectra_btn.setEnabled(True)
        self.cancel_spectra_btn.hide()
        self.spectra_activity.stop()
        self.spectra_activity.hide()
        for label in self._spectrum_labels():
            label.stop_loading()

    def stop_dh_spectra(self):
        """Stop a running fetch, if any, nothing more said about it. True if
        there was one."""
        if not self.dh_worker or not self.dh_worker.isRunning():
            return False
        abandon(self.dh_worker)
        self.dh_worker = None
        self._spectra_ended()
        return True

    def cancel_dh_spectra(self):
        """The Cancel button: what has arrived stays."""
        if self.stop_dh_spectra():
            shown = len(self._spectra_paths)
            self.spectra_status_label.setText(f"Cancelled ({shown} spectrograph(s) loaded)")
            for label in self._spectrum_labels():
                if not label.pixmap() and not label.text():  # was still waiting
                    label.setText("Press Fetch spectra to see this one")

    def on_dh_source_ended(self, source):
        """The card of a source that gave nothing stops waiting now: the last
        source can be minutes away."""
        if self.dh_worker is not None:
            self._say_no_data(source)

    def _say_no_data(self, source):
        label = getattr(self, f"{source}_image_label")
        if not label.pixmap():
            label.stop_loading()
            label.setText(f"{NO_DATA_TEXT[source]} available for this date")

    def on_dh_sources_done(self, done, total):
        if self.dh_worker is not None:
            self.spectra_activity.start(f"{done} of {total} sources")

    def on_dh_spectra_progress(self, message: str):
        """Handle progress updates from DH spectra worker."""
        if self.dh_worker is not None:
            self.spectra_status_label.setText(message)

    def on_dh_partial_ready(self, source: str, path: str):
        """Display a single spectrograph as soon as it's ready."""
        from PyQt5.QtGui import QPixmap
        import os

        if self.dh_worker is None or not path or not os.path.exists(path):
            return

        self._spectra_paths[source] = path
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return

        # Map source to its UI components
        ui_map = {
            'wind': (self.wind_image_label, self.wind_save_btn),
            'stereo_a': (self.stereo_a_image_label, self.stereo_a_save_btn),
            'stereo_b': (self.stereo_b_image_label, self.stereo_b_save_btn),
            'norp': (self.norp_image_label, self.norp_save_btn),
            'orfees': (self.orfees_image_label, self.orfees_save_btn),
            'nda': (self.nda_image_label, self.nda_save_btn),
            'yamagawa': (self.yamagawa_image_label, self.yamagawa_save_btn),
            'hiras': (self.hiras_image_label, self.hiras_save_btn),
        }
        
        label, btn = ui_map.get(source, (None, None))
        
        if label:
            label.stop_loading()
            if source in ['yamagawa', 'hiras']:
                # Zoom in: fit to the available height in the card and allow horizontal scrolling
                # Scaled to ~150 to stay within the scroll area without vertical scroll
                scaled = pixmap.scaledToHeight(150, Qt.SmoothTransformation)
                getattr(self, f"{source}_scroll").setFixedHeight(SCROLL_HEIGHT)
            else:
                parent = label.parent()
                if parent:
                    available_width = parent.width() - 40
                    if available_width < 600 or available_width > 1000:
                        available_width = 848
                else:
                    available_width = 848 # Fallback
                
                scaled = pixmap.scaledToWidth(
                    max(400, min(available_width, pixmap.width())),
                    Qt.SmoothTransformation
                )
            label.setPixmap(scaled)
            if btn:
                btn.setEnabled(True)

    def on_dh_spectra_ready(self, results: dict, errors: list):
        """Handle completed DH spectra fetch - final cleanup."""
        if self.dh_worker is None:
            return  # cancelled
        self._spectra_ended()

        # Update labels for any missing data
        sources = ['wind', 'stereo_a', 'norp', 'orfees', 'nda']
        # Handle station-specific NICT
        day = self._spectra_date
        from datetime import date
        if day >= date(2016, 7, 11):
            sources.append('yamagawa')
        else:
            sources.append('hiras')
        
        # Stero-B check
        if day.year <= 2014:
            sources.append('stereo_b')

        found_count = 0
        for s in sources:
            if results.get(s) and os.path.exists(results[s]):
                found_count += 1
            else:
                self._say_no_data(s)

        status_text = f"Loaded {found_count} spectrograph(s)"
        if errors:
            status_text += f" ({len(errors)} errors)"
            self.spectra_status_label.setToolTip("\n".join(errors))
        else:
            self.spectra_status_label.setToolTip("")
            
        self.spectra_status_label.setText(status_text)

    def save_spectra_image(self, source: str):
        """Save the spectrogram image to a user-selected location."""
        from PyQt5.QtWidgets import QFileDialog
        
        if not hasattr(self, '_spectra_paths') or not self._spectra_paths.get(source):
            QMessageBox.warning(self, "No image", "No image available to save.")
            return

        source_path = self._spectra_paths[source]
        if not os.path.exists(source_path):
            QMessageBox.warning(self, "No image", "The downloaded image file was not found.")
            return

        # Suggest a filename based on source and date
        date_str = f"{self._spectra_date:%Y%m%d}"
        source_names = {
            'wind': 'WIND_WAVES', 
            'stereo_a': 'STEREO_A', 
            'stereo_b': 'STEREO_B', 
            'norp': 'Nobeyama_NORP',
            'orfees': 'Nancay_ORFEES',
            'nda': 'Nancay_NDA',
            'yamagawa': 'NICT_Yamagawa',
            'hiras': 'NICT_HiRAS'
        }
        suggested_name = f"{source_names.get(source, source)}_{date_str}.png"

        # Open save dialog
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save spectrogram",
            suggested_name,
            "PNG Images (*.png);;All Files (*)"
        )

        if save_path:
            # Converted: WIND/WAVES is kept as the JPEG the archive sends
            if QImage(source_path).save(save_path, "PNG"):
                QMessageBox.information(self, "Image saved", f"Image saved to:\n{save_path}")
            else:
                show_error(self, "Could not save the image", f"The image could not be saved to:\n{save_path}")

    def show_local_image(self, source: str, title: str):
        """Open a local spectra image in a full-resolution dialog viewer."""
        if not hasattr(self, '_spectra_paths') or not self._spectra_paths.get(source):
            return  # No image available

        image_path = self._spectra_paths[source]
        if not os.path.exists(image_path):
            return

        # Create a dialog to show the full image
        dialog = QDialog(None)  # Use None as parent for independent window
        dialog.setWindowTitle(f"{title}: full resolution")
        dialog.setWindowFlags(
            Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint
        )
        fit_to_screen(dialog, 1280, 900)
        dialog.setAttribute(Qt.WA_DeleteOnClose)

        layout = dialog_layout(dialog)

        # Scrollable image
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("ImageWell")

        img_label = QLabel()
        img_label.setAlignment(Qt.AlignCenter)

        # Load the full-resolution image
        pixmap = QPixmap(image_path)
        if not pixmap.isNull():
            img_label.setPixmap(pixmap)
            img_label.adjustSize()
        else:
            img_label.setText("The image could not be read")

        scroll.setWidget(img_label)
        layout.addWidget(scroll)

        buttons, _ = button_bar(secondary=("Close", dialog.accept))
        layout.addWidget(buttons)

        # Keep reference and show
        self.image_viewers.append(dialog)
        dialog.finished.connect(lambda result, d=dialog: self._cleanup_viewer(d))
        dialog.show()

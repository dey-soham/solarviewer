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
    QProgressBar,
    QDialog,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from ..ui.styles import theme_manager

from .widgets import ClickableLabel
from .workers import DHSpectraWorker
logger = logging.getLogger(__name__)


class RadioSpectraTabMixin:
    """Builds the Radio Spectra tab and fills it as spectra arrive.

    Mixed into :class:`NOAAEventsViewer`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``_cleanup_viewer``, ``date_edit``, ``hiras_card``, ``hiras_image_label``,
    ``hiras_save_btn``, ``image_viewers``, ``is_dark_theme``,
    ``nda_image_label``, ``nda_save_btn``, ``norp_image_label``,
    ``norp_save_btn``, ``orfees_image_label``, ``orfees_save_btn``,
    ``stereo_a_image_label``, ``stereo_a_save_btn``, ``stereo_b_card``,
    ``stereo_b_image_label``, ``stereo_b_save_btn``, ``tabs``,
    ``wind_image_label``, ``wind_save_btn``, ``yamagawa_card``,
    ``yamagawa_image_label``, ``yamagawa_save_btn``
    """

    def _create_radio_spectra_tab(self):
        """Radio Spectra tab: one card per instrument."""

        # Tab 6: Radio Spectra (DH-band)
        spectra_tab = QWidget()
        spectra_layout = QVBoxLayout(spectra_tab)
        spectra_layout.setContentsMargins(16, 12, 16, 12)
        spectra_layout.setSpacing(12)

        # Fetch button and progress
        spectra_controls = QHBoxLayout()
        self.fetch_spectra_btn = QPushButton("Fetch Spectra")
        self.fetch_spectra_btn.setToolTip(
            "Download WIND/WAVES and STEREO dynamic spectra for this date"
        )
        self.fetch_spectra_btn.clicked.connect(self.fetch_dh_spectra)
        spectra_controls.addWidget(self.fetch_spectra_btn)

        self.spectra_progress = QProgressBar()
        self.spectra_progress.setRange(0, 0)  # Indeterminate
        self.spectra_progress.hide()
        spectra_controls.addWidget(self.spectra_progress)

        spectra_controls.addStretch()
        spectra_layout.addLayout(spectra_controls)

        # Status label
        self.spectra_status_label = QLabel(
            "Click 'Fetch Spectra' to download radio spectra."
        )
        self.spectra_status_label.setWordWrap(True)
        self.spectra_status_label.setObjectName("Hint")
        spectra_layout.addWidget(self.spectra_status_label)

        # Scrollable content area for spectra images
        spectra_scroll = QScrollArea()
        spectra_scroll.setWidgetResizable(True)
        spectra_scroll.setFrameShape(QFrame.NoFrame)

        spectra_content = QWidget()
        spectra_content_layout = QVBoxLayout(spectra_content)
        spectra_content_layout.setSpacing(16)

        # One card per instrument
        self._add_spectrum_card(
            spectra_content_layout,
            "wind",
            "WIND/WAVES 24h Calibrated (14 kHz - 14 MHz)",
            (99, 102, 241),
            "WIND/WAVES",
            "WIND/WAVES",
        )
        self._add_spectrum_card(
            spectra_content_layout,
            "stereo_a",
            "STEREO-A S/WAVES (10 kHz - 16 MHz)",
            (16, 185, 129),
            "STEREO-A",
            "STEREO-A",
        )
        self._add_spectrum_card(
            spectra_content_layout,
            "stereo_b",
            "STEREO-B S/WAVES (10 kHz - 16 MHz)",
            (251, 146, 60),
            "STEREO-B",
            "STEREO-B",
            keep_card=True,
            hidden=True,
        )  # STEREO-B lost contact in 2014
        self._add_spectrum_card(
            spectra_content_layout,
            "norp",
            "Nobeyama Radio Polarimeter (NORP) Full-Day",
            (59, 130, 246),
            "Nobeyama",
            "Nobeyama NORP",
            label_text="\n\n\n\n\n\n\n\n\n\nNo data loaded",
            alignment=Qt.AlignTop | Qt.AlignHCenter,
            min_height=400,
        )
        self._add_spectrum_card(
            spectra_content_layout,
            "yamagawa",
            "NICT Yamagawa Spectrograph",
            (20, 184, 166),
            "Yamagawa",
            "Yamagawa",
            min_height=None,
            keep_card=True,
            scroll=True,
        )
        self._add_spectrum_card(
            spectra_content_layout,
            "hiras",
            "NICT HiRAS Spectrograph",
            (14, 165, 233),
            "HiRAS",
            "HiRAS",
            min_height=None,
            keep_card=True,
            scroll=True,
        )
        self._add_spectrum_card(
            spectra_content_layout,
            "orfees",
            "ORFEES (Nançay) Spectrograph",
            (168, 85, 247),
            "ORFEES",
            "ORFEES",
        )
        self._add_spectrum_card(
            spectra_content_layout,
            "nda",
            "NDA (Nançay) Spectrograph",
            (236, 72, 153),
            "NDA",
            "NDA",
        )

        spectra_content_layout.addStretch()
        spectra_scroll.setWidget(spectra_content)
        spectra_layout.addWidget(spectra_scroll)

        # Credits label
        spectra_credits_label = QLabel(
            'Data Sources: <a href="https://cdaw.gsfc.nasa.gov/">CDAW NASA</a>, '
            '<a href="https://solar.nro.nao.ac.jp/norp/">NoRP</a>, '
            '<a href="https://rsdb.obs-nancay.fr/">Nançay</a>, '
            '<a href="https://solobs.nict.go.jp/radio/cgi-bin/MainDisplay.pl">NICT</a>'
        )
        spectra_credits_label.setOpenExternalLinks(True)
        spectra_credits_label.setObjectName("Hint")
        spectra_layout.addWidget(spectra_credits_label)

        self.tabs.addTab(spectra_tab, "Radio Spectra")

    def _add_spectrum_card(
        self,
        parent_layout,
        key,
        title,
        accent_rgb,
        save_name,
        viewer_title,
        label_text="No data loaded",
        alignment=Qt.AlignCenter,
        min_height=200,
        scroll=False,
        hidden=False,
        keep_card=False,
    ):
        """Add a themed card with a title, a Save button and a clickable image
        label for one radio spectrograph. Creates ``self.<key>_save_btn`` and
        ``self.<key>_image_label`` (and ``self.<key>_scroll`` / ``self.<key>_card``
        when ``scroll`` / ``keep_card`` are set)."""
        palette = theme_manager.palette
        rgb = ", ".join(str(v) for v in accent_rgb)
        card = QFrame()
        if keep_card:
            setattr(self, f"{key}_card", card)
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)

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
            scroll_area.setMinimumHeight(180)  # Scaled image + scrollbar
            scroll_area.setFrameShape(QFrame.NoFrame)
            setattr(self, f"{key}_scroll", scroll_area)

        image_label = ClickableLabel(label_text)
        image_label.setAlignment(alignment)
        if not scroll:
            image_label.setMinimumHeight(min_height)
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

    def clear_dh_spectra_ui(self):
        """Reset the radio spectra UI labels to initial state."""
        labels = [
            self.wind_image_label, self.stereo_a_image_label, self.stereo_b_image_label,
            self.norp_image_label, self.orfees_image_label, self.nda_image_label,
            self.yamagawa_image_label, self.hiras_image_label
        ]
        buttons = [
            self.wind_save_btn, self.stereo_a_save_btn, self.stereo_b_save_btn,
            self.norp_save_btn, self.orfees_save_btn, self.nda_save_btn,
            self.yamagawa_save_btn, self.hiras_save_btn
        ]
        
        for label in labels:
            label.clear()
            if label is self.norp_image_label:
                label.setText("\n\n\n\n\n\n\n\n\n\nNo data loaded")
            else:
                label.setText("No data loaded")
            label.setToolTip("Click 'Fetch Spectra' to load")
            
        for btn in buttons:
            btn.setEnabled(False)
            
        self.spectra_status_label.setText("Click 'Fetch Spectra' to load radio spectra.")
        self.spectra_status_label.setToolTip("")

    def fetch_dh_spectra(self):
        """Fetch DH-band dynamic spectra (WIND/WAVES & STEREO) for the selected date."""

        if hasattr(self, "dh_worker") and self.dh_worker and self.dh_worker.isRunning():
            return

        # Get selected date
        qdate = self.date_edit.date()
        selected_date = datetime(qdate.year(), qdate.month(), qdate.day(), 12, 0, 0)

        # Update UI
        self.fetch_spectra_btn.setEnabled(False)
        self.spectra_progress.show()
        self.spectra_status_label.setText(f"Fetching DH spectra for {qdate.toString('yyyy-MM-dd')}...")

        # Clear previous images
        self.wind_image_label.setText("Loading...")
        self.stereo_a_image_label.setText("Loading...")
        self.stereo_b_image_label.setText("Loading...")
        self.norp_image_label.setText("\n\n\n\n\n\n\n\n\n\nLoading...")
        self.orfees_image_label.setText("Loading...")
        self.nda_image_label.setText("Loading...")
        self.yamagawa_image_label.setText("Loading...")
        self.hiras_image_label.setText("Loading...")
        
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
        if qdate.year() <= 2014:
            self.stereo_b_card.show()
        else:
            self.stereo_b_card.hide()

        # Start worker
        self.dh_worker = DHSpectraWorker(selected_date)
        self.dh_worker.progress.connect(self.on_dh_spectra_progress)
        self.dh_worker.result_ready.connect(self.on_dh_partial_ready)
        self.dh_worker.finished.connect(self.on_dh_spectra_ready)
        self._spectra_paths = {}  # Initialize/Reset
        self.dh_worker.start()

    def on_dh_spectra_progress(self, message: str):
        """Handle progress updates from DH spectra worker."""
        self.spectra_status_label.setText(message)

    def on_dh_partial_ready(self, source: str, path: str):
        """Display a single spectrograph as soon as it's ready."""
        from PyQt5.QtGui import QPixmap
        import os

        if not path or not os.path.exists(path):
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
            if source in ['yamagawa', 'hiras']:
                # Zoom in: fit to the available height in the card and allow horizontal scrolling
                # The scroll area minimum height is 180, so we scale to ~150 to stay within it without vertical scroll
                scaled = pixmap.scaledToHeight(150, Qt.SmoothTransformation)
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
        self.fetch_spectra_btn.setEnabled(True)
        self.spectra_progress.hide()

        # Update labels for any missing data
        sources = ['wind', 'stereo_a', 'norp', 'orfees', 'nda']
        # Handle station-specific NICT
        qdate = self.date_edit.date()
        from datetime import date
        if date(qdate.year(), qdate.month(), qdate.day()) >= date(2016, 7, 11):
            sources.append('yamagawa')
        else:
            sources.append('hiras')
        
        # Stero-B check
        if qdate.year() <= 2014:
            sources.append('stereo_b')

        found_count = 0
        for s in sources:
            if results.get(s) and os.path.exists(results[s]):
                found_count += 1
            else:
                # If it's not ready, set the "No data" text
                label_map = {
                    'wind': (self.wind_image_label, "No WIND/WAVES data"),
                    'stereo_a': (self.stereo_a_image_label, "No STEREO-A data"),
                    'stereo_b': (self.stereo_b_image_label, "No STEREO-B data"),
                    'norp': (self.norp_image_label, "\n\n\n\n\n\n\n\n\n\nNo Nobeyama data"),
                    'orfees': (self.orfees_image_label, "No ORFEES data"),
                    'nda': (self.nda_image_label, "No NDA data"),
                    'yamagawa': (self.yamagawa_image_label, "No Yamagawa data"),
                    'hiras': (self.hiras_image_label, "No HiRAS data"),
                }
                label, text = label_map.get(s, (None, ""))
                if label and not label.pixmap():
                    label.setText(f"{text} available for this date")

        status_text = f"Loaded {found_count} spectrograph(s)"
        if errors:
            status_text += f" ({len(errors)} errors)"
            self.spectra_status_label.setToolTip("\n".join(errors))
        else:
            self.spectra_status_label.setToolTip("")
            
        self.spectra_status_label.setText(status_text)

    def on_dh_spectra_error(self, error_msg: str):
        """Handle DH spectra fetch error."""
        self.fetch_spectra_btn.setEnabled(True)
        self.spectra_progress.hide()
        self.spectra_status_label.setText(f"Error: {error_msg}")
        self.wind_image_label.setText("Error fetching data")
        self.stereo_a_image_label.setText("Error fetching data")
        self.wind_save_btn.setEnabled(False)
        self.stereo_a_save_btn.setEnabled(False)
        self.stereo_b_save_btn.setEnabled(False)
        self.norp_save_btn.setEnabled(False)

    def save_spectra_image(self, source: str):
        """Save the spectrogram image to a user-selected location."""
        from PyQt5.QtWidgets import QFileDialog
        
        if not hasattr(self, '_spectra_paths') or not self._spectra_paths.get(source):
            QMessageBox.warning(self, "Error", "No image available to save.")
            return

        source_path = self._spectra_paths[source]
        if not os.path.exists(source_path):
            QMessageBox.warning(self, "Error", "Source image file not found.")
            return

        # Suggest a filename based on source and date
        qdate = self.date_edit.date()
        date_str = qdate.toString("yyyyMMdd")
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
            "Save Spectrogram",
            suggested_name,
            "PNG Images (*.png);;All Files (*)"
        )

        if save_path:
            import shutil
            try:
                shutil.copy2(source_path, save_path)
                QMessageBox.information(self, "Saved", f"Image saved to:\n{save_path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save image:\n{str(e)}")

    def show_local_image(self, source: str, title: str):
        """Open a local spectra image in a full-resolution dialog viewer."""
        if not hasattr(self, '_spectra_paths') or not self._spectra_paths.get(source):
            return  # No image available

        image_path = self._spectra_paths[source]
        if not os.path.exists(image_path):
            return

        # Create a dialog to show the full image
        dialog = QDialog(None)  # Use None as parent for independent window
        dialog.setWindowTitle(f"{title} - Full Resolution")
        dialog.setWindowFlags(
            Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint
        )
        dialog.resize(1280, 900)
        dialog.setAttribute(Qt.WA_DeleteOnClose)

        layout = QVBoxLayout(dialog)

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
            img_label.setText("Failed to load image")

        scroll.setWidget(img_label)
        layout.addWidget(scroll)

        # Close button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        # Keep reference and show
        self.image_viewers.append(dialog)
        dialog.finished.connect(lambda result, d=dialog: self._cleanup_viewer(d))
        dialog.show()

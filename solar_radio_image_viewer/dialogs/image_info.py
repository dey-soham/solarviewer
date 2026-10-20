from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QRadioButton,
    QLineEdit,
    QLabel,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QGridLayout,
    QFormLayout,
    QDialogButtonBox,
    QComboBox,
    QCheckBox,
    QFileDialog,
    QMessageBox,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QButtonGroup,
    QWidget,
    QProgressDialog,
    QFrame,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QProgressBar,
    QApplication,
)
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import Qt, QSize
import sys
import numpy as np
import os

from ..ui.styles import theme_manager, set_hand_cursor


class ImageInfoDialog(QDialog):
    """Professional metadata display dialog with organized sections."""

    def __init__(self, parent=None, info_text="", metadata=None, imagename=None):
        super().__init__(parent)
        self.setWindowTitle("Image Metadata")
        self.setMinimumSize(700, 600)
        self.metadata = metadata
        self.info_text = info_text
        self.imagename = imagename

        self.setup_ui()
        set_hand_cursor(self)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)

        # Source Info
        if hasattr(self, "imagename") and self.imagename:
            source_container = QWidget()
            source_layout = QHBoxLayout(source_container)
            source_layout.setContentsMargins(0, 5, 0, 10)
            
            from ..ui.styles import theme_manager
            palette = theme_manager.palette
            
            source_label = QLabel(f"📄 {os.path.basename(self.imagename)}")
            source_label.setStyleSheet(f"""
                font-size: 11pt; 
                font-weight: 600; 
                color: {palette.get('text_secondary', '#888888')};
                background: {palette.get('button', '#444444')};
                padding: 4px 12px;
                border-radius: 6px;
            """)
            source_layout.addWidget(source_label)
            source_layout.addStretch()
            layout.addWidget(source_container)

        # Check if we have structured metadata (dict) or plain text
        if isinstance(self.metadata, dict) and self.metadata:
            self._create_structured_view(layout)
        elif self.info_text:
            # Handle legacy plain text or formatted text
            if isinstance(self.info_text, dict):
                self.metadata = self.info_text
                self._create_structured_view(layout)
            else:
                self._create_text_view(layout, self.info_text)
        else:
            self._create_text_view(layout, "No metadata available")

        # Button row
        button_layout = QHBoxLayout()

        # Copy button
        copy_btn = QPushButton("📋 Copy to Clipboard")
        copy_btn.clicked.connect(self._copy_to_clipboard)
        button_layout.addWidget(copy_btn)

        button_layout.addStretch()

        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        close_btn.setDefault(True)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

    def _create_structured_view(self, parent_layout):
        """Create tabbed view for structured metadata."""
        tab_widget = QTabWidget()

        section_info = [
            ("observation", "Observation", "Observation details"),
            ("spectral", "Spectral", "Frequency and wavelength information"),
            ("beam", "Beam", "Synthesized beam properties"),
            ("image", "Image", "Image dimensions and coordinates"),
            ("processing", "Processing", "Data processing information"),
        ]

        for section_key, title, tooltip in section_info:
            if section_key in self.metadata and self.metadata[section_key]:
                tab = self._create_section_table(self.metadata[section_key])
                tab_widget.addTab(tab, title)
                tab_widget.setTabToolTip(tab_widget.count() - 1, tooltip)

        # Add raw header tab if available
        if "raw_header" in self.metadata and self.metadata["raw_header"]:
            raw_tab = self._create_raw_header_view(self.metadata["raw_header"])
            tab_widget.addTab(raw_tab, "All")
            tab_widget.setTabToolTip(
                tab_widget.count() - 1, "Complete FITS header information"
            )

        parent_layout.addWidget(tab_widget)

    def _create_section_table(self, section_data):
        """Create a styled table for a metadata section."""
        table = QTableWidget()
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["Property", "Value"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setAlternatingRowColors(True)

        # Populate table
        table.setRowCount(len(section_data))
        for row, (key, value) in enumerate(section_data.items()):
            key_item = QTableWidgetItem(str(key))
            value_item = QTableWidgetItem(str(value))

            # Make key bold
            font = key_item.font()
            font.setBold(True)
            key_item.setFont(font)

            table.setItem(row, 0, key_item)
            table.setItem(row, 1, value_item)

        # Adjust row heights
        table.resizeRowsToContents()

        return table

    def _create_raw_header_view(self, raw_header):
        """Create a searchable view for raw header data."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(5, 5, 5, 5)

        # Search box
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("🔍 Search:"))
        search_edit = QLineEdit()
        search_edit.setPlaceholderText("Filter headers...")
        search_layout.addWidget(search_edit)
        layout.addLayout(search_layout)

        # Table
        table = QTableWidget()
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["Keyword", "Value"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setAlternatingRowColors(True)

        # Store data for filtering
        self._raw_header_data = list(raw_header.items())
        self._raw_table = table

        # Populate table
        self._populate_raw_table("")

        # Connect search
        search_edit.textChanged.connect(self._filter_raw_header)

        layout.addWidget(table)
        return widget

    def _populate_raw_table(self, filter_text):
        """Populate raw header table with optional filtering."""
        filter_text = filter_text.lower()
        filtered_data = [
            (k, v)
            for k, v in self._raw_header_data
            if filter_text in k.lower() or filter_text in str(v).lower()
        ]

        self._raw_table.setRowCount(len(filtered_data))
        for row, (key, value) in enumerate(filtered_data):
            key_item = QTableWidgetItem(str(key))
            value_item = QTableWidgetItem(str(value))

            font = key_item.font()
            font.setFamily("monospace")
            key_item.setFont(font)
            value_item.setFont(font)

            self._raw_table.setItem(row, 0, key_item)
            self._raw_table.setItem(row, 1, value_item)

        self._raw_table.resizeRowsToContents()

    def _filter_raw_header(self, text):
        """Filter raw header table based on search text."""
        self._populate_raw_table(text)

    def _create_text_view(self, parent_layout, text):
        """Create simple text view for plain text metadata."""
        text_area = QPlainTextEdit()
        text_area.setReadOnly(True)
        text_area.setPlainText(text)

        # Use monospace font for better alignment
        font = text_area.font()
        font.setFamily("monospace")
        text_area.setFont(font)

        parent_layout.addWidget(text_area)

    def _copy_to_clipboard(self):
        """Copy metadata to clipboard as text."""
        from PyQt5.QtWidgets import QApplication

        clipboard = QApplication.clipboard()

        if self.metadata:
            # Format structured metadata as text
            from ..processing.metadata import format_metadata_text

            text = format_metadata_text(self.metadata)
        else:
            text = self.info_text

        clipboard.setText(text)

        # Show confirmation (brief)
        if hasattr(self, "parentWidget") and self.parentWidget():
            pass  # Could show status message



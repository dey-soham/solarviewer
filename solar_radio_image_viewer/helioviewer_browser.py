#!/usr/bin/env python3
"""
Helioviewer Browser - Time-series viewer for solar images from Helioviewer API.
"""

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QLabel, QPushButton, QDateTimeEdit, QSpinBox, QListWidget,
    QScrollArea, QFrame, QProgressBar, QComboBox, QFileDialog,
    QMessageBox, QDialog, QListWidgetItem, QApplication, QGroupBox,
    QTreeWidget, QTreeWidgetItem, QHeaderView
)
from PyQt5.QtCore import Qt, QDateTime, QThread, pyqtSignal, QTimer, QSize
from PyQt5.QtGui import QPixmap, QImage, QIcon
from datetime import datetime, timedelta
from collections import OrderedDict
import requests
from typing import List, Dict, Optional, Tuple
import os

# Global list to keep threads alive if window is closed while they are running
# This prevents "QThread: Destroyed while thread is still running"
_active_threads = []

# Known instrument cadences (in seconds) - Based on official mission documentation
INSTRUMENT_CADENCES = {
    # SDO/AIA - 12s for EUV, 24s for UV, 3600s for visible
    'AIA 94': 12,
    'AIA 131': 12,
    'AIA 171': 12,
    'AIA 193': 12,
    'AIA 211': 12,
    'AIA 304': 12,
    'AIA 335': 12,
    'AIA 1600': 24,
    'AIA 1700': 24,
    'AIA 4500': 3600,
    
    # SDO/HMI - 45s for both magnetogram and continuum
    'HMI magnetogram': 45,
    'HMI continuum': 45,
    
    # SOHO/EIT - 12 minutes (720s) for all wavelengths
    'EIT 171': 720,
    'EIT 195': 720,
    'EIT 284': 720,
    'EIT 304': 720,
    
    # SOHO/MDI - 96 minutes
    'MDI magnetogram': 96,
    'MDI continuum': 96,
    
    # SOHO/LASCO - 30 minutes for both C2 and C3
    'LASCO': 1800,
    'LASCO C2': 1800,
    'LASCO C3': 1800,
    
    # STEREO/SECCHI EUVI - 2.5 minutes (150s)
    'EUVI': 150,
    'EUVI 171': 150,
    'EUVI 195': 150,
    'EUVI 284': 150,
    'EUVI 304': 150,
    
    # STEREO/SECCHI Coronagraphs
    'COR1': 300,   # 5 minutes
    'COR2': 900,   # 15 minutes
    
    # GOES/SUVI - 10s for all channels
    'SUVI': 10,
    'SUVI 94': 10,
    'SUVI 131': 10,
    'SUVI 171': 10,
    'SUVI 195': 10,
    'SUVI 284': 10,
    'SUVI 304': 10,
    
    # Solar Orbiter/EUI
    'EUI': 600,
    'FSI': 600,    # Full Sun Imager - 10 minutes
    'HRI': 300,    # High Resolution Imager - 5 minutes
    
    # Legacy/Other
    'SXT': 60,     # Yohkoh SXT
}

# Whitelist: Only instruments with verified cadence AND FOV parameters
# Using patterns that match API-returned names
VERIFIED_INSTRUMENTS = [
    # SDO - 100% verified
    'AIA 94', 'AIA 131', 'AIA 171', 'AIA 193', 'AIA 211', 'AIA 304', 'AIA 335',
    'AIA 1600', 'AIA 1700', 'AIA 4500',
    'HMI magnetogram', 'HMI continuum',
    
    # SOHO - 100% verified
    'EIT 171', 'EIT 195', 'EIT 284', 'EIT 304',
    'LASCO/C2 white-light', 'LASCO/C3 white-light',  # API format
    
    # STEREO - 100% verified (both A and B)
    'SECCHI/EUVI 171', 'SECCHI/EUVI 195', 'SECCHI/EUVI 284', 'SECCHI/EUVI 304',
    'SECCHI/COR1 white-light', 'SECCHI/COR2 white-light',
    
    # GOES/SUVI - 100% verified
    'SUVI 94', 'SUVI 131', 'SUVI 171', 'SUVI 195', 'SUVI 284', 'SUVI 304',
]


def fetch_all_instruments() -> List[Tuple[str, str, str, str]]:
    """Fetch all available instruments from Helioviewer API."""
    try:
        url = 'https://api.helioviewer.org/v2/getDataSources/'
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        instruments = []
        
        for observatory, obs_data in sorted(data.items()):
            if not isinstance(obs_data, dict):
                continue
            
            for instrument, inst_data in sorted(obs_data.items()):
                if not isinstance(inst_data, dict):
                    continue
                
                # At this level, keys are either measurements (dict with metadata) or detectors (dict of measurements)
                for key, value in sorted(inst_data.items()):
                    if not isinstance(value, dict):
                        continue
                    
                    # Check if this is a measurement (has 'sourceId') or a detector level
                    if 'sourceId' in value:
                        # This is a measurement directly under instrument
                        measurement = key
                        source_id = value['sourceId']
                        # Layer format is [sourceId,visible,opacity]
                        layer = f'[{source_id},1,100]'
                        name = f'{instrument} {measurement}'
                        
                        # Determine cadence - try exact match first, then instrument family
                        cadence = INSTRUMENT_CADENCES.get(
                            name,  # Try exact: "AIA 171"
                            INSTRUMENT_CADENCES.get(
                                instrument,  # Try instrument: "AIA"
                                60  # Default: 60s
                            )
                        )
                        
                        description = f'{observatory} - {name}'
                        instruments.append((name, layer, observatory, cadence))
                    else:
                        # This is a detector level, iterate measurements
                        detector = key
                        for measurement, meas_data in sorted(value.items()):
                            if not isinstance(meas_data, dict):
                                continue
                            if 'sourceId' not in meas_data:
                                continue
                            
                            source_id = meas_data['sourceId']
                            # Layer format is [sourceId,visible,opacity]
                            layer = f'[{source_id},1,100]'
                            name = f'{instrument}/{detector} {measurement}'
                            
                            # Determine cadence - try exact match first, then detector, then instrument
                            cadence = INSTRUMENT_CADENCES.get(
                                name,  # Try exact: "SECCHI/EUVI 171"
                                INSTRUMENT_CADENCES.get(
                                    f'{detector} {measurement}',  # Try: "EUVI 171"
                                    INSTRUMENT_CADENCES.get(
                                        detector,  # Try: "EUVI"
                                        INSTRUMENT_CADENCES.get(instrument, 60)  # Default: 60s
                                    )
                                )
                            )
                            
                            description = f'{observatory} - {name}'
                            instruments.append((name, layer, observatory, cadence))
        
        print(f"Loaded {len(instruments)} instruments from Helioviewer")
        
        # Filter to only verified instruments
        verified = []
        for name, layer, obs, cad in instruments:
            # Check if instrument name is in verified list
            # Also check without observatory prefix for STEREO instruments
            if name in VERIFIED_INSTRUMENTS:
                verified.append((name, layer, obs, cad))
                continue
            
            # For STEREO, also accept if base name matches (e.g., "EUVI 171" from "SECCHI/EUVI 171")
            if '/' in name:
                base_name = name.split('/')[-1]  # Get "EUVI 171" from "SECCHI/EUVI 171"
                if base_name in ['EUVI 171', 'EUVI 195', 'EUVI 284', 'EUVI 304', 'COR1 white-light', 'COR2 white-light']:
                    verified.append((name, layer, obs, cad))
        
        print(f"Filtered to {len(verified)} verified instruments (with known cadence & FOV)")
        return verified
    except Exception as e:
        print(f"Error fetching instruments: {e}")
        import traceback
        traceback.print_exc()
        # Fallback to essential instruments
        from .solar_context.context_images import ESSENTIAL_INSTRUMENTS
        return ESSENTIAL_INSTRUMENTS


class ImageDownloader(QThread):
    """Thread to download a single image."""
    finished = pyqtSignal(str, QPixmap)  # instrument, pixmap
    error = pyqtSignal(str, str)  # instrument, error_msg
    
    def __init__(self, instrument_name, layer_path, timestamp, width=1000, height=1000):
        super().__init__()
        self.instrument_name = instrument_name
        self.layer_path = layer_path
        self.timestamp = timestamp
        self.width = width
        self.height = height
        self.running = True
    
    def stop(self):
        """Signal the thread to stop."""
        self.running = False
    
    def run(self):
        try:
            # Determine imageScale based on instrument
            layer_lower = self.layer_path.lower()
            
            # Parse instrument from layer (format: [sourceId,1,100])
            # Need to determine instrument type from name passed in
            inst_lower = self.instrument_name.lower()
            
            # Base imageScale for 1000x1000 images
            # Solar disk instruments - tight fit (2.0-4.0)
            if any(x in inst_lower for x in ['aia', 'hmi', 'eit', 'euvi', 'suvi']):
                if 'hmi continuum' in inst_lower:
                    base_image_scale = 3.0  # HMI continuum - slightly wider
                elif 'hmi' in inst_lower:
                    base_image_scale = 2.5  # HMI magnetogram
                else:
                    base_image_scale = 2.5  # AIA, EIT, EUVI, SUVI - fits solar disk in 1000px
            
            # Coronagraphs - wide field
            elif 'lasco' in inst_lower and 'c2' in inst_lower:
                base_image_scale = 12.0  # LASCO C2: fits up to 6 Rsun in 1000px
            elif 'lasco' in inst_lower and 'c3' in inst_lower:
                base_image_scale = 58.0  # LASCO C3: fits up to 30 Rsun in 1000px 
            elif any(x in inst_lower for x in ['cor1', 'cor2']):
                if 'cor1' in inst_lower:
                    base_image_scale = 6.0  # COR1: ~1.4-4 Rsun
                else:
                    base_image_scale = 15.0  # COR2: ~2.5-15 Rsun
            
            # Other instruments
            else:
                base_image_scale = 4.0  # Default moderate zoom
            
            # Adjust imageScale to maintain FOV for different image sizes
            # Formula: new_scale = base_scale * (1000 / actual_size)
            size_factor = 1000.0 / self.width
            image_scale = str(base_image_scale * size_factor)
            
            # Build Helioviewer URL
            from urllib.parse import urlencode
            base_url = "https://api.helioviewer.org/v2/takeScreenshot/"
            params = {
                'date': self.timestamp.toString("yyyy-MM-ddTHH:mm:ss") + "Z",
                'imageScale': image_scale,
                'layers': self.layer_path,
                'x0': '0',
                'y0': '0',
                'width': str(self.width),
                'height': str(self.height),
                'display': 'true',
                'watermark': 'false'
            }
            url = f"{base_url}?{urlencode(params)}"
            
            if not self.running:
                return
            
            # Download image
            response = requests.get(url, timeout=60)
            response.raise_for_status()
            
            if not self.running:
                return
            
            # Convert to QPixmap
            qimage = QImage()
            if qimage.loadFromData(response.content):
                pixmap = QPixmap.fromImage(qimage)
                if self.running:
                    self.finished.emit(self.instrument_name, pixmap)
            else:
                if self.running:
                    self.error.emit(self.instrument_name, "Failed to load image data")
                
        except Exception as e:
            if self.running:
                self.error.emit(self.instrument_name, str(e))


class FrameLoader(QThread):
    """Thread to load all instruments for multiple frames in background."""
    frame_loaded = pyqtSignal(int, dict)  # frame_index, {instrument: pixmap}
    progress = pyqtSignal(int, int)  # current, total
    
    def __init__(self, timestamps, instruments_data, width=1000, height=1000):
        super().__init__()
        self.timestamps = timestamps
        self.instruments_data = instruments_data  # List of (name, layer_path, observatory, desc)
        self.width = width
        self.height = height
        self.running = True
    
    def run(self):
        total_frames = len(self.timestamps)
        for frame_idx, timestamp in enumerate(self.timestamps):
            if not self.running:
                break
                
            frame_data = {}
            for instrument_name, layer_path, observatory, description in self.instruments_data:
                if not self.running:
                    break
                
                try:
                    # Determine base imageScale for 1000x1000
                    inst_lower = instrument_name.lower()
                    
                    # Solar disk instruments - tight fit
                    if any(x in inst_lower for x in ['aia', 'hmi', 'eit', 'euvi', 'suvi']):
                        if 'hmi continuum' in inst_lower:
                            base_image_scale = 3.0
                        elif 'hmi' in inst_lower:
                            base_image_scale = 2.5
                        else:
                            base_image_scale = 2.5
                    
                    # Coronagraphs
                    elif 'lasco' in inst_lower and 'c2' in inst_lower:
                        base_image_scale = 12.0
                    elif 'lasco' in inst_lower and 'c3' in inst_lower:
                        base_image_scale = 58.0
                    elif any(x in inst_lower for x in ['cor1', 'cor2']):
                        base_image_scale = 6.0 if 'cor1' in inst_lower else 15.0
                    else:
                        base_image_scale = 4.0
                    
                    # Adjust imageScale to maintain FOV
                    size_factor = 1000.0 / self.width
                    image_scale = str(base_image_scale * size_factor)
                    
                    # Build URL
                    from urllib.parse import urlencode
                    base_url = "https://api.helioviewer.org/v2/takeScreenshot/"
                    params = {
                        'date': timestamp.toString("yyyy-MM-ddTHH:mm:ss") + "Z",
                        'imageScale': image_scale,
                        'layers': layer_path,
                        'x0': '0',
                        'y0': '0',
                        'width': str(self.width),
                        'height': str(self.height),
                        'display': 'true',
                        'watermark': 'false'
                    }
                    url = f"{base_url}?{urlencode(params)}"
                    
                    # Download
                    response = requests.get(url, timeout=60)
                    response.raise_for_status()
                    
                    # Convert to pixmap
                    qimage = QImage()
                    if qimage.loadFromData(response.content):
                        frame_data[instrument_name] = QPixmap.fromImage(qimage)
                        
                except Exception as e:
                    print(f"Error loading {instrument_name} for frame {frame_idx}: {e}")
            
            self.frame_loaded.emit(frame_idx, frame_data)
            self.progress.emit(frame_idx + 1, total_frames)
    
    def stop(self):
        self.running = False


class FullImageDialog(QDialog):
    """Dialog to show full resolution image."""
    def __init__(self, parent, pixmap, title):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1200, 1200)
        
        layout = QVBoxLayout(self)
        
        # Scroll area for large image
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        
        label = QLabel()
        label.setPixmap(pixmap)
        label.setAlignment(Qt.AlignCenter)
        
        scroll.setWidget(label)
        layout.addWidget(scroll)
        
        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)


class HelioviewerBrowser(QMainWindow):
    """Main browser window for Helioviewer time-series."""
    
    def __init__(self, parent=None, initial_start=None, initial_end=None):
        super().__init__(parent)
        self.setWindowTitle("🌐 Helioviewer Browser")
        self.resize(1600, 1000)
        
        # Data storage
        self.frames = OrderedDict()  # {frame_index: {instrument: pixmap}}
        self.timestamps = []
        self.current_frame = 0
        self.playing = False
        self.frame_loaders = []
        
        # Download queue management for parallel loading
        self.download_queue = []  # Queue of (frame_idx, instrument_data)
        self.active_downloads = 0
        self.max_concurrent_downloads = 4
        
        # Store initial times
        self.initial_start = initial_start
        self.initial_end = initial_end
        
        # Animation timer
        self.animation_timer = QTimer()
        self.animation_timer.timeout.connect(self.next_frame)
        
        # Flag to prevent updates during close
        self._closing = False
        
        self.init_ui()
    
    def init_ui(self):
        """Initialize the user interface."""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        # Top panel - Time range controls
        self.create_time_controls(main_layout)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)
        
        # Splitter for left/right panels
        splitter = QSplitter(Qt.Horizontal)
        
        # Left panel - Instrument filter
        self.create_instrument_panel(splitter)
        
        # Right panel - Image grid
        self.create_image_panel(splitter)
        
        splitter.setSizes([300, 1300])
        main_layout.addWidget(splitter, 1)
        
        # Bottom panel - Animation controls
        self.create_animation_controls(main_layout)
    
    def create_time_controls(self, parent_layout):
        """Create time range input controls."""
        group = QGroupBox("Time Range")
        layout = QHBoxLayout(group)
        
        # Start time
        layout.addWidget(QLabel("Start:"))
        self.start_datetime = QDateTimeEdit()
        self.start_datetime.setCalendarPopup(True)
        self.start_datetime.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        # Default to yesterday 10:00 UTC or initial_start if provided
        if hasattr(self, 'initial_start') and self.initial_start:
            self.start_datetime.setDateTime(self.initial_start)
        else:
            default_start = QDateTime.currentDateTimeUtc().addDays(-1)
            default_start.setTime(default_start.time().fromString("10:00:00", "HH:mm:ss"))
            self.start_datetime.setDateTime(default_start)
        layout.addWidget(self.start_datetime)
        
        # End time
        layout.addWidget(QLabel("End:"))
        self.end_datetime = QDateTimeEdit()
        self.end_datetime.setCalendarPopup(True)
        self.end_datetime.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        if hasattr(self, 'initial_end') and self.initial_end:
            self.end_datetime.setDateTime(self.initial_end)
        else:
            default_end = self.start_datetime.dateTime().addSecs(3600)  # 1 hour later
            self.end_datetime.setDateTime(default_end)
        layout.addWidget(self.end_datetime)
        
        # Interval
        layout.addWidget(QLabel("Interval (seconds):"))
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(10, 3600)
        self.interval_spin.setValue(30)  # Default 30 seconds
        self.interval_spin.setSuffix(" s")
        layout.addWidget(self.interval_spin)

        layout.addStretch()
        
        # Load button
        # Add load emoji
        self.load_button = QPushButton("📥 Load")
        self.load_button.clicked.connect(self.load_time_series)
        self.load_button.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #2196F3, stop:1 #1976D2);
                color: white;
                padding: 8px 16px;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #42A5F5, stop:1 #2196F3);
            }
        """)
        layout.addWidget(self.load_button)
        
        parent_layout.addWidget(group)
    
    def create_instrument_panel(self, parent_splitter):
        """Create left panel with instrument filter (tree view grouped by observatory)."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # Header with icon
        header = QLabel("Select Instrument 🛰️")
        header.setStyleSheet("padding: 5px;")
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)
        
        # Load all instruments button
        '''refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.setToolTip("Reload available instruments from Helioviewer API")
        refresh_btn.clicked.connect(self.load_all_instruments)
        layout.addWidget(refresh_btn)'''
        
        # Instrument tree - grouped by observatory
        self.instrument_tree = QTreeWidget()
        self.instrument_tree.setHeaderLabels(["Instrument", "⏱️"])
        self.instrument_tree.setColumnCount(2)
        self.instrument_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.instrument_tree.header().setStretchLastSection(False)
        self.instrument_tree.setColumnWidth(1, 45)  # Narrow cadence column
        self.instrument_tree.header().setDefaultAlignment(Qt.AlignCenter)
        self.instrument_tree.setAlternatingRowColors(True)
        self.instrument_tree.setRootIsDecorated(True)
        self.instrument_tree.setAnimated(True)
        self.instrument_tree.setIndentation(20)
        
        # Style the tree
        self.instrument_tree.setStyleSheet("""
            QTreeWidget {
                border: 1px solid #555;
                border-radius: 4px;
            }
            QTreeWidget::item {
                padding: 4px 2px;
            }
            QTreeWidget::item:selected {
                background: #1976D2;
                color: white;
            }
            QTreeWidget::branch:has-children:!has-siblings:closed,
            QTreeWidget::branch:closed:has-children:has-siblings {
                border-image: none;
                image: url(data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTYiIGhlaWdodD0iMTYiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+PHBhdGggZD0iTTYgNGw0IDQtNCA0IiBzdHJva2U9IiM4ODgiIGZpbGw9Im5vbmUiLz48L3N2Zz4=);
            }
            QTreeWidget::branch:open:has-children:!has-siblings,
            QTreeWidget::branch:open:has-children:has-siblings {
                border-image: none;
                image: url(data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTYiIGhlaWdodD0iMTYiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+PHBhdGggZD0iTTQgNmw0IDQgNC00IiBzdHJva2U9IiM4ODgiIGZpbGw9Im5vbmUiLz48L3N2Zz4=);
            }
        """)
        
        # Load instruments
        self.load_all_instruments()
        
        # Connect selection change
        self.instrument_tree.itemSelectionChanged.connect(self.on_instrument_selected)
        layout.addWidget(self.instrument_tree)
        
        # Image size control
        layout.addSpacing(10)
        size_group = QGroupBox("📐 Image Size")
        size_layout = QHBoxLayout(size_group)
        size_layout.setContentsMargins(8, 8, 8, 8)
        self.image_size_spin = QSpinBox()
        self.image_size_spin.setRange(100, 4000)
        self.image_size_spin.setValue(800)
        self.image_size_spin.setSuffix(" px")
        self.image_size_spin.setToolTip("Image dimensions (100-4000 pixels)")
        size_layout.addWidget(self.image_size_spin)
        layout.addWidget(size_group)
        
        parent_splitter.addWidget(panel)
        
        # Keep reference for compatibility
        self.instrument_list = self.instrument_tree
    
    def load_all_instruments(self):
        """Load all available instruments from Helioviewer, grouped by observatory."""
        self.instrument_tree.clear()
        self.all_instruments = fetch_all_instruments()
        
        # Observatory info with icons
        observatory_info = {
            'SDO': ('🌟', 'Solar Dynamics Observatory'),
            'SOHO': ('☀️', 'Solar and Heliospheric Observatory'),
            'STEREO_A': ('🅰️', 'STEREO Ahead'),
            'STEREO_B': ('🅱️', 'STEREO Behind'),
            'GOES-16': ('🛸', 'GOES-16 Satellite'),
            'GOES-17': ('🛸', 'GOES-17 Satellite'),
            'GOES-18': ('🛸', 'GOES-18 Satellite'),
        }
        
        # Wavelength descriptions
        wavelength_descriptions = {
            '94': 'Fe XVIII - 6.3 MK',
            '131': 'Fe VIII/XXI - 0.4/10 MK',
            '171': 'Fe IX - 0.6 MK',
            '193': 'Fe XII/XXIV - 1.2/20 MK',
            '195': 'Fe XII - 1.2 MK',
            '211': 'Fe XIV - 2.0 MK',
            '284': 'Fe XV - 2.0 MK',
            '304': 'He II - 0.05 MK',
            '335': 'Fe XVI - 2.5 MK',
            '1600': 'C IV + cont - 0.1 MK',
            '1700': 'Continuum - 5000 K',
            '4500': 'Continuum - 5000 K',
            'magnetogram': 'Magnetic Field',
            'continuum': 'White Light',
            'white-light': 'Coronagraph',
        }
        
        # Group instruments by observatory
        obs_groups = {}
        for nickname, layer_path, observatory, cadence in self.all_instruments:
            if observatory not in obs_groups:
                obs_groups[observatory] = []
            obs_groups[observatory].append((nickname, layer_path, cadence))
        
        # Create tree items
        for observatory in sorted(obs_groups.keys()):
            icon, full_name = observatory_info.get(observatory, ('🔭', observatory))
            
            # Create observatory node
            obs_item = QTreeWidgetItem()
            obs_item.setText(0, f"{icon} {observatory}")
            obs_item.setToolTip(0, full_name)
            obs_item.setFirstColumnSpanned(True)
            
            # Make it bold
            font = obs_item.font(0)
            font.setBold(True)
            obs_item.setFont(0, font)
            
            # Add instruments under this observatory
            for nickname, layer_path, cadence in sorted(obs_groups[observatory]):
                # Format cadence
                if cadence < 60:
                    cad_str = f"{cadence}s"
                elif cadence < 3600:
                    cad_str = f"{cadence//60}m"
                else:
                    cad_str = f"{cadence//3600}h"
                
                # Get wavelength description
                desc = ""
                for wave, wave_desc in wavelength_descriptions.items():
                    if wave in nickname.lower() or wave in nickname:
                        desc = wave_desc
                        break
                
                # Create instrument item
                inst_item = QTreeWidgetItem()
                display_name = nickname
                if desc:
                    display_name = f"{nickname}  •  {desc}"
                inst_item.setText(0, display_name)
                inst_item.setText(1, cad_str)
                inst_item.setTextAlignment(1, Qt.AlignCenter)
                inst_item.setData(0, Qt.UserRole, (nickname, layer_path, observatory, cadence))
                inst_item.setToolTip(0, f"{nickname}\nCadence: {cad_str}\n{desc}" if desc else f"{nickname}\nCadence: {cad_str}")
                
                obs_item.addChild(inst_item)
            
            self.instrument_tree.addTopLevelItem(obs_item)
        
        # Expand all by default
        self.instrument_tree.expandAll()
    
    def get_selected_instruments(self):
        """Get currently selected instrument (single selection from tree)."""
        selected_items = self.instrument_tree.selectedItems()
        instruments = []
        for item in selected_items:
            data = item.data(0, Qt.UserRole)
            if data:  # Only instrument items have data, not observatory headers
                instruments.append(data)
        return instruments
    
    def set_all_instruments(self, checked):
        """Not used in single-selection mode."""
        pass
    
    def on_instrument_selected(self):
        """Handle instrument selection change."""
        self.update_interval_for_instrument()
        if self.frames:
            self.display_current_frame()
    
    def update_interval_for_instrument(self):
        """Update interval spinner based on selected instrument's cadence."""
        selected = self.get_selected_instruments()
        if selected:
            nickname, layer_path, observatory, cadence = selected[0]
            # Set interval to instrument cadence
            self.interval_spin.setValue(int(cadence))
    
    def create_image_panel(self, parent_splitter):
        """Create right panel with image grid."""
        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        
        # Container widget
        self.image_container = QWidget()
        self.image_grid = QVBoxLayout(self.image_container)
        self.image_grid.setAlignment(Qt.AlignTop | Qt.AlignHCenter)  # Center horizontally
        
        # Placeholder
        placeholder = QLabel("Load a time series to display images")
        placeholder.setStyleSheet("color: #888; padding: 50px;")
        placeholder.setAlignment(Qt.AlignCenter)
        self.image_grid.addWidget(placeholder)
        
        scroll.setWidget(self.image_container)
        parent_splitter.addWidget(scroll)
    
    def create_animation_controls(self, parent_layout):
        """Create bottom panel with animation controls."""
        group = QGroupBox("Animation Controls")
        layout = QHBoxLayout(group)
        
        # First button
        self.first_btn = QPushButton("|◄")
        self.first_btn.setToolTip("First Frame")
        self.first_btn.clicked.connect(self.first_frame)
        self.first_btn.setEnabled(False)
        layout.addWidget(self.first_btn)
        
        # Previous button
        self.prev_btn = QPushButton("◄")
        self.prev_btn.setToolTip("Previous Frame")
        self.prev_btn.clicked.connect(self.prev_frame)
        self.prev_btn.setEnabled(False)
        layout.addWidget(self.prev_btn)
        
        # Play/Pause button
        self.play_btn = QPushButton("▶")
        self.play_btn.setToolTip("Play")
        self.play_btn.clicked.connect(self.toggle_play)
        self.play_btn.setEnabled(False)
        layout.addWidget(self.play_btn)
        
        # Next button
        self.next_btn = QPushButton("►")
        self.next_btn.setToolTip("Next Frame")
        self.next_btn.clicked.connect(self.next_frame)
        self.next_btn.setEnabled(False)
        layout.addWidget(self.next_btn)
        
        # Last button
        self.last_btn = QPushButton("►|")
        self.last_btn.setToolTip("Last Frame")
        self.last_btn.clicked.connect(self.last_frame)
        self.last_btn.setEnabled(False)
        layout.addWidget(self.last_btn)
        
        layout.addSpacing(20)
        
        # Frame indicator
        self.frame_label = QLabel("Frame: 0/0")
        layout.addWidget(self.frame_label)
        
        layout.addSpacing(20)
        
        # Speed control
        layout.addWidget(QLabel("Speed:"))
        self.speed_combo = QComboBox()
        self.speed_combo.addItems(["0.25x", "0.5x", "1x", "2x", "4x", "8x", "16x"])
        self.speed_combo.setCurrentText("1x")
        self.speed_combo.currentIndexChanged.connect(self.on_speed_changed)
        layout.addWidget(self.speed_combo)
        
        layout.addStretch()
        
        # Save current frame
        save_frame_btn = QPushButton("💾 Save Frame")
        save_frame_btn.clicked.connect(self.save_current_frame)
        layout.addWidget(save_frame_btn)
        
        # Export animation
        export_btn = QPushButton("🎬 Export Animation")
        export_btn.clicked.connect(self.export_animation)
        layout.addWidget(export_btn)
        
        # Batch download
        batch_btn = QPushButton("⬇️ Batch Download")
        batch_btn.clicked.connect(self.batch_download)
        layout.addWidget(batch_btn)
        
        parent_layout.addWidget(group)
    
    def on_speed_changed(self):
        """Handle speed change - update animation timer if playing."""
        if self.playing:
            # Recalculate interval and restart timer
            speed_text = self.speed_combo.currentText()
            speed = float(speed_text.replace('x', ''))
            interval_ms = int(0.5 * 1000 / speed)  # Base: 2 fps
            self.animation_timer.setInterval(interval_ms)
    
    def on_instrument_filter_changed(self):
        """Handle instrument filter change."""
        if self.frames:
            self.display_current_frame()
    
    def load_time_series(self):
        """Load time series based on user inputs."""
        start = self.start_datetime.dateTime()
        end = self.end_datetime.dateTime()
        interval_sec = self.interval_spin.value()
        
        # Validate
        if start >= end:
            QMessageBox.warning(self, "Invalid Range", "Start time must be before end time.")
            return
        
        # Generate timestamps
        self.timestamps = []
        current = start
        while current <= end:
            self.timestamps.append(current)
            current = current.addSecs(interval_sec)
        
        # Limit to 4000 frames
        if len(self.timestamps) > 4000:
            QMessageBox.warning(
                self, "Too Many Frames",
                f"Time range generates {len(self.timestamps)} frames. Limiting to first 4000."
            )
            self.timestamps = self.timestamps[:4000]
        
        # Clear old data
        self.frames.clear()
        self.current_frame = 0
        
        # Get selected instruments
        selected_instruments = self.get_selected_instruments()
        if not selected_instruments:
            QMessageBox.warning(self, "No Instruments", "Please select an instrument to proceed.")
            return
        
        # Show progress
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, len(self.timestamps))
        self.progress_bar.setValue(0)
        self.load_button.setEnabled(False)
        
        # Load first frame immediately (synchronously)
        self.load_frame_sync(0, selected_instruments)
        
        # Queue remaining frames for parallel download
        if len(self.timestamps) > 1:
            for frame_idx in range(1, len(self.timestamps)):
                for inst_data in selected_instruments:
                    self.download_queue.append((frame_idx, inst_data))
            
            # Start initial batch of downloads
            self._process_frame_download_queue()
        else:
            self.on_loading_finished()
    
    def load_frame_sync(self, frame_idx, instruments):
        """Load a frame synchronously (for first frame)."""
        frame_data = {}
        timestamp = self.timestamps[frame_idx]
        
        for nickname, layer_path, observatory, description in instruments:
            downloader = ImageDownloader(nickname, layer_path, timestamp)
            downloader.run()  # Run synchronously
            # Note: This won't emit signals, need to handle differently
        
        # For now, start async download for first frame too
        self.frames[frame_idx] = {}
        downloaders = []
        
        for nickname, layer_path, observatory, description in instruments:
            downloader = ImageDownloader(nickname, layer_path, timestamp, 
                                        width=self.image_size_spin.value(),
                                        height=self.image_size_spin.value())
            downloader.finished.connect(
                lambda inst, pix, idx=frame_idx: self.on_image_downloaded(idx, inst, pix)
            )
            downloader.error.connect(
                lambda inst, err, idx=frame_idx: self.on_parallel_download_error(idx, inst, err)
            )
            downloaders.append(downloader)
            self.frame_loaders.append(downloader)  # Track for cleanup on close
            downloader.start()
        
        # Enable controls
        self.enable_controls()
    
    def _process_frame_download_queue(self):
        """Start next frame downloads if under concurrent limit."""
        while self.active_downloads < self.max_concurrent_downloads and self.download_queue:
            frame_idx, inst_data = self.download_queue.pop(0)
            nickname, layer_path, observatory, description = inst_data
            timestamp = self.timestamps[frame_idx]
            
            self.active_downloads += 1
            downloader = ImageDownloader(
                nickname, layer_path, timestamp,
                width=self.image_size_spin.value(),
                height=self.image_size_spin.value()
            )
            downloader.finished.connect(
                lambda inst, pix, idx=frame_idx: self.on_parallel_image_downloaded(idx, inst, pix)
            )
            downloader.error.connect(
                lambda inst, err, idx=frame_idx: self.on_parallel_download_error(idx, inst, err)
            )
            downloader.finished.connect(self._on_frame_download_finished)
            self.frame_loaders.append(downloader)
            downloader.start()
    
    def on_parallel_image_downloaded(self, frame_idx, instrument, pixmap):
        """Handle parallel image download completion."""
        if self._closing:
            return
            
        if frame_idx not in self.frames:
            self.frames[frame_idx] = {}
        
        self.frames[frame_idx][instrument] = pixmap
        
        # Update progress (count completed frames)
        completed_frames = len(self.frames)
        self.progress_bar.setValue(completed_frames)
        
        # If this is the current frame, update display
        if frame_idx == self.current_frame:
            self.display_current_frame()
    
    def on_parallel_download_error(self, frame_idx, instrument, error):
        """Handle parallel download error."""
        if self._closing:
            return
        print(f"Error downloading {instrument} for frame {frame_idx}: {error}")
    
    def _on_frame_download_finished(self):
        """Handle download thread finish and start next in queue."""
        if self._closing:
            return
            
        self.active_downloads -= 1
        if self.active_downloads < 0:
            self.active_downloads = 0
        
        # Process next in queue
        self._process_frame_download_queue()
        
        # Check if all downloads are complete
        if self.active_downloads == 0 and len(self.download_queue) == 0:
            self.on_loading_finished()
    
    def on_image_downloaded(self, frame_idx, instrument, pixmap):
        """Handle single image download completion."""
        if self._closing:
            return
            
        if frame_idx not in self.frames:
            self.frames[frame_idx] = {}
        
        self.frames[frame_idx][instrument] = pixmap
        
        # If this is the current frame, update display
        if frame_idx == self.current_frame:
            self.display_current_frame()
    
    def on_background_frame_loaded(self, frame_idx, frame_data):
        """Handle background frame loading."""
        # Adjust index (background loader starts from index 1)
        actual_idx = frame_idx + 1
        self.frames[actual_idx] = frame_data
    
    def on_loading_progress(self, current, total):
        """Update progress bar."""
        self.progress_bar.setValue(current + 1)  # +1 for the first frame loaded synchronously
    
    def on_loading_finished(self):
        """Handle loading completion."""
        self.progress_bar.setVisible(False)
        self.load_button.setEnabled(True)
        QMessageBox.information(
            self, "Loading Complete",
            f"Loaded {len(self.frames)} frames with selected instruments."
        )
    
    def enable_controls(self):
        """Enable animation controls."""
        has_frames = len(self.timestamps) > 0
        self.first_btn.setEnabled(has_frames)
        self.prev_btn.setEnabled(has_frames)
        self.play_btn.setEnabled(has_frames)
        self.next_btn.setEnabled(has_frames)
        self.last_btn.setEnabled(has_frames)
        
        if has_frames:
            self.display_current_frame()
    
    def display_current_frame(self):
        """Display the current frame's image (single instrument, fullscreen)."""
        # Clear grid
        while self.image_grid.count():
            child = self.image_grid.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        
        if self.current_frame >= len(self.timestamps):
            return
        
        timestamp = self.timestamps[self.current_frame]
        frame_data = self.frames.get(self.current_frame, {})
        
        # Get selected instrument (single selection)  
        selected = self.get_selected_instruments()
        if not selected:
            placeholder = QLabel("Please select an instrument from the list")
            placeholder.setStyleSheet("color: #888; padding: 50px;")
            placeholder.setAlignment(Qt.AlignCenter)
            self.image_grid.addWidget(placeholder)
            return
        
        nickname, layer_path, observatory, cadence = selected[0]
        
        # Title with timestamp and instrument
        title = QLabel(f"<h2>{nickname} ({observatory})</h2>")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color: #2196F3; padding: 5px;")
        self.image_grid.addWidget(title)
        
        time_label = QLabel(f"<h3>{timestamp.toString('yyyy-MM-dd HH:mm:ss')} UTC</h3>")
        time_label.setAlignment(Qt.AlignCenter)
        time_label.setStyleSheet("color: #666; padding: 5px;")
        self.image_grid.addWidget(time_label)
        
        # Fullscreen image
        pixmap = frame_data.get(nickname)
        
        img_label = QLabel()
        img_label.setAlignment(Qt.AlignCenter)
        
        if pixmap:
            # Display at original size (1000x1000)
            img_label.setPixmap(pixmap)
            img_label.setCursor(Qt.PointingHandCursor)
            img_label.mousePressEvent = lambda e, p=pixmap, n=nickname: self.show_full_image(p, n)
        else:
            img_label.setText("Loading...")
            img_label.setStyleSheet("color: #666; padding: 100px;")
        
        # Add with stretch to center vertically
        self.image_grid.addWidget(img_label, 1)
        
        # Update frame label
        self.frame_label.setText(f"Frame: {self.current_frame + 1}/{len(self.timestamps)}")
    
    def create_image_card(self, instrument_name, pixmap, timestamp):
        """Create a card widget for an image."""
        card = QFrame()
        card.setFrameStyle(QFrame.Box | QFrame.Raised)
        card.setLineWidth(1)
        card.setStyleSheet("background: #2b2b2b; border-radius: 8px;")
        
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 8, 8, 8)
        
        # Instrument name
        name_label = QLabel(f"<b>{instrument_name}</b>")
        name_label.setStyleSheet("color: #2196F3;")
        name_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(name_label)
        
        # Time
        time_label = QLabel(timestamp.toString("HH:mm:ss") + " UTC")
        time_label.setStyleSheet("color: #888;")
        time_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(time_label)
        
        # Image
        img_label = QLabel()
        img_label.setFixedSize(250, 250)
        img_label.setAlignment(Qt.AlignCenter)
        img_label.setStyleSheet("background: #000; border: 1px solid #555;")
        
        if pixmap:
            scaled = pixmap.scaled(250, 250, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            img_label.setPixmap(scaled)
            img_label.setCursor(Qt.PointingHandCursor)
            img_label.mousePressEvent = lambda e, p=pixmap, n=instrument_name: self.show_full_image(p, n)
        else:
            img_label.setText("Loading...")
            img_label.setStyleSheet("background: #000; color: #666;")
        
        layout.addWidget(img_label)
        
        return card
    
    def show_full_image(self, pixmap, title):
        """Show full resolution image in dialog."""
        dialog = FullImageDialog(self, pixmap, title)
        dialog.exec_()
    
    # Animation controls
    def first_frame(self):
        """Jump to first frame."""
        self.current_frame = 0
        self.display_current_frame()
    
    def prev_frame(self):
        """Go to previous frame."""
        if self.current_frame > 0:
            self.current_frame -= 1
            self.display_current_frame()
    
    def next_frame(self):
        """Go to next frame."""
        if self.current_frame < len(self.timestamps) - 1:
            self.current_frame += 1
            self.display_current_frame()
        else:
            # Loop back to start
            self.current_frame = 0
            self.display_current_frame()
    
    def last_frame(self):
        """Jump to last frame."""
        self.current_frame = len(self.timestamps) - 1
        self.display_current_frame()
    
    def toggle_play(self):
        """Toggle animation playback."""
        if not self.playing:
            self.playing = True
            self.play_btn.setText("❚❚")
            self.play_btn.setToolTip("Pause")
            
            # Get speed
            speed_text = self.speed_combo.currentText()
            speed = float(speed_text.replace('x', ''))
            interval_ms = int(0.5 * 1000 / speed)  # Base: 2 fps
            
            self.animation_timer.start(interval_ms)
        else:
            self.pause_animation()
    
    def pause_animation(self):
        """Pause animation."""
        self.playing = False
        self.play_btn.setText("▶")
        self.play_btn.setToolTip("Play")
        self.animation_timer.stop()
    
    # Export/Save functions
    def save_current_frame(self):
        """Save current frame as PNG."""
        if not self.frames or self.current_frame >= len(self.timestamps):
            QMessageBox.warning(self, "No Frame", "No frame to save.")
            return
        
        # Get selected instrument
        selected = self.get_selected_instruments()
        if not selected:
            QMessageBox.warning(self, "No Instrument", "Please select an instrument.")
            return
        
        nickname, _, _, _ = selected[0]
        timestamp = self.timestamps[self.current_frame]
        
        # Get pixmap for current frame
        frame_data = self.frames.get(self.current_frame, {})
        pixmap = frame_data.get(nickname)
        
        if not pixmap:
            QMessageBox.warning(self, "No Image", "Current frame image not loaded yet.")
            return
        
        # Create default filename
        safe_name = nickname.replace('/', '_').replace(' ', '_')
        default_name = f"helioviewer_{safe_name}_{timestamp.toString('yyyyMMdd_HHmmss')}.png"
        
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Frame", default_name, "PNG Image (*.png)"
        )
        
        if file_path:
            try:
                if pixmap.save(file_path, "PNG"):
                    QMessageBox.information(
                        self, "Save Successful",
                        f"Frame saved to:\n{file_path}"
                    )
                else:
                    QMessageBox.warning(
                        self, "Save Failed",
                        "Failed to save image. Please check file path and permissions."
                    )
            except Exception as e:
                QMessageBox.critical(
                    self, "Error",
                    f"Error saving frame:\n{str(e)}"
                )
    
    def export_animation(self):
        """Export animation as GIF or MP4."""
        if len(self.frames) < 2:
            QMessageBox.warning(self, "Insufficient Frames", "Need at least 2 frames for animation.")
            return
        
        # Get selected instrument
        selected = self.get_selected_instruments()
        if not selected:
            QMessageBox.warning(self, "No Instrument", "Please select an instrument.")
            return
        
        nickname, _, _, _ = selected[0]
        
        # Ask for format
        from PyQt5.QtWidgets import QInputDialog, QProgressDialog, QCheckBox
        formats = ["GIF", "MP4"]
        format_choice, ok = QInputDialog.getItem(
            self, "Export Format", "Choose export format:", formats, 0, False
        )
        
        if not ok:
            return
        
        # Ask about timestamp overlay
        timestamp_dialog = QMessageBox(self)
        timestamp_dialog.setWindowTitle("Timestamp Overlay")
        timestamp_dialog.setText("Include timestamp on each frame?")
        timestamp_dialog.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        timestamp_dialog.setDefaultButton(QMessageBox.Yes)
        include_timestamp = (timestamp_dialog.exec_() == QMessageBox.Yes)
        
        # Get save path
        safe_name = nickname.replace('/', '_').replace(' ', '_')
        start_time = self.timestamps[0].toString('yyyyMMdd_HHmmss')
        ext = "gif" if format_choice == "GIF" else "mp4"
        default_name = f"helioviewer_{safe_name}_{start_time}.{ext}"
        
        file_filter = "GIF Image (*.gif)" if format_choice == "GIF" else "MP4 Video (*.mp4)"
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Animation", default_name, file_filter
        )
        
        if not file_path:
            return
        
        # Collect frames
        frames_to_export = []
        for idx in sorted(self.frames.keys()):
            frame_data = self.frames[idx]
            pixmap = frame_data.get(nickname)
            if pixmap:
                frames_to_export.append(pixmap)
        
        if len(frames_to_export) < 2:
            QMessageBox.warning(
                self, "Insufficient Frames",
                f"Only {len(frames_to_export)} frame(s) loaded for {nickname}. Need at least 2."
            )
            return
        
        # Show progress dialog
        progress = QProgressDialog("Exporting animation...", "Cancel", 0, len(frames_to_export), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        
        # Get animation speed from UI
        speed_text = self.speed_combo.currentText()
        speed = float(speed_text.replace('x', ''))
        # Calculate frame duration in ms (base: 2 fps at 1x speed = 500ms)
        frame_duration_ms = int(500 / speed)
        
        try:
            if format_choice == "GIF":
                self._export_gif(frames_to_export, file_path, progress, frame_duration_ms, include_timestamp)
            else:
                # For MP4, convert to FPS
                fps = 1000.0 / frame_duration_ms
                self._export_mp4(frames_to_export, file_path, progress, fps, include_timestamp)
            
            if not progress.wasCanceled():
                QMessageBox.information(
                    self, "Export Successful",
                    f"Animation exported to:\n{file_path}\n\n"
                    f"Frames: {len(frames_to_export)}"
                )
        except Exception as e:
            progress.close()
            QMessageBox.critical(
                self, "Export Failed",
                f"Error exporting animation:\n{str(e)}"
            )
        finally:
            progress.close()
    
    def _add_timestamp_to_pixmap(self, pixmap, timestamp_text):
        """Add timestamp overlay to a pixmap."""
        from PyQt5.QtGui import QPainter, QFont, QColor, QPen
        from PyQt5.QtCore import Qt, QRect
        
        # Create a copy to avoid modifying original
        result = QPixmap(pixmap)
        painter = QPainter(result)
        
        # Set up font
        font = QFont("Arial", 12, QFont.Bold)
        painter.setFont(font)
        
        # Draw background rectangle for text
        text_rect = painter.fontMetrics().boundingRect(timestamp_text)
        padding = 15
        bg_rect = QRect(
            10,
            result.height() - text_rect.height() - padding * 2 - 10,
            text_rect.width() + padding * 2,
            text_rect.height() + padding * 2
        )
        
        # Semi-transparent black background
        painter.fillRect(bg_rect, QColor(0, 0, 0, 180))
        
        # Draw white text
        painter.setPen(QPen(QColor(255, 255, 255)))
        painter.drawText(
            bg_rect.adjusted(padding, padding, -padding, -padding),
            Qt.AlignLeft | Qt.AlignVCenter,
            timestamp_text
        )
        
        painter.end()
        return result
    
    def _export_gif(self, frames, file_path, progress, duration_ms, include_timestamp=False):
        """Export frames as animated GIF."""
        try:
            from PIL import Image
        except ImportError:
            raise Exception("PIL/Pillow is required for GIF export. Install with: pip install Pillow")
        
        # Convert QPixmaps to PIL Images
        pil_images = []
        for i, pixmap in enumerate(frames):
            if progress.wasCanceled():
                return
            progress.setValue(i)
            
            # Add timestamp if requested
            if include_timestamp and i < len(self.timestamps):
                timestamp_text = self.timestamps[i].toString('yyyy-MM-dd HH:mm:ss') + ' UTC'
                pixmap = self._add_timestamp_to_pixmap(pixmap, timestamp_text)
            
            # Convert QPixmap to PIL Image via QImage
            qimage = pixmap.toImage()
            qimage = qimage.convertToFormat(QImage.Format_RGB888)
            
            width = qimage.width()
            height = qimage.height()
            ptr = qimage.bits()
            ptr.setsize(height * width * 3)
            
            import numpy as np
            arr = np.frombuffer(ptr, np.uint8).reshape((height, width, 3))
            pil_img = Image.fromarray(arr, 'RGB')
            pil_images.append(pil_img)
        
        # Save as animated GIF with user-selected speed
        progress.setLabelText("Saving GIF...")
        pil_images[0].save(
            file_path,
            save_all=True,
            append_images=pil_images[1:],
            duration=duration_ms,
            loop=0
        )
        progress.setValue(len(frames))
    
    def _export_mp4(self, frames, file_path, progress, fps, include_timestamp=False):
        """Export frames as MP4 video."""
        try:
            import cv2
            import numpy as np
        except ImportError:
            raise Exception("OpenCV is required for MP4 export. Install with: pip install opencv-python")
        
        # Get frame dimensions from first frame
        first_pixmap = frames[0]
        width = first_pixmap.width()
        height = first_pixmap.height()
        
        # Create video writer with user-selected FPS
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(file_path, fourcc, fps, (width, height))
        
        try:
            for i, pixmap in enumerate(frames):
                if progress.wasCanceled():
                    return
                progress.setValue(i)
                
                # Add timestamp if requested
                if include_timestamp and i < len(self.timestamps):
                    timestamp_text = self.timestamps[i].toString('yyyy-MM-dd HH:mm:ss') + ' UTC'
                    pixmap = self._add_timestamp_to_pixmap(pixmap, timestamp_text)
                
                # Convert QPixmap to numpy array
                qimage = pixmap.toImage()
                qimage = qimage.convertToFormat(QImage.Format_RGB888)
                
                width = qimage.width()
                height = qimage.height()
                ptr = qimage.bits()
                ptr.setsize(height * width * 3)
                arr = np.frombuffer(ptr, np.uint8).reshape((height, width, 3))
                
                # OpenCV uses BGR, Qt uses RGB
                arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
                out.write(arr)
            
            progress.setValue(len(frames))
        finally:
            out.release()
    
    def batch_download(self):
        """Batch download all frames."""
        if not self.frames:
            QMessageBox.warning(self, "No Data", "No frames to download.")
            return
        
        # Get selected instrument
        selected = self.get_selected_instruments()
        if not selected:
            QMessageBox.warning(self, "No Instrument", "Please select an instrument.")
            return
        
        nickname, _, _, _ = selected[0]
        
        dir_path = QFileDialog.getExistingDirectory(self, "Select Download Directory")
        if not dir_path:
            return
        
        # Create subdirectory with timestamp
        from datetime import datetime
        safe_name = nickname.replace('/', '_').replace(' ', '_')
        timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
        batch_dir = os.path.join(dir_path, f"helioviewer_{safe_name}_{timestamp_str}")
        
        try:
            os.makedirs(batch_dir, exist_ok=True)
        except Exception as e:
            QMessageBox.critical(
                self, "Error",
                f"Failed to create directory:\n{str(e)}"
            )
            return
        
        # Show progress
        from PyQt5.QtWidgets import QProgressDialog
        progress = QProgressDialog(
            "Downloading frames...", "Cancel", 0, len(self.frames), self
        )
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        
        saved_count = 0
        metadata_lines = []
        metadata_lines.append(f"Helioviewer Batch Download")
        metadata_lines.append(f"Instrument: {nickname}")
        metadata_lines.append(f"Download Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        metadata_lines.append(f"Total Frames: {len(self.frames)}")
        metadata_lines.append("")
        metadata_lines.append("Frame Index | Timestamp | Filename | Status")
        metadata_lines.append("-" * 80)
        
        try:
            for idx in sorted(self.frames.keys()):
                if progress.wasCanceled():
                    break
                
                progress.setValue(saved_count)
                
                frame_data = self.frames[idx]
                pixmap = frame_data.get(nickname)
                
                if pixmap:
                    timestamp = self.timestamps[idx]
                    filename = f"frame_{idx:04d}_{timestamp.toString('yyyyMMdd_HHmmss')}.png"
                    file_path = os.path.join(batch_dir, filename)
                    
                    if pixmap.save(file_path, "PNG"):
                        saved_count += 1
                        status = "OK"
                    else:
                        status = "FAILED"
                else:
                    timestamp = self.timestamps[idx] if idx < len(self.timestamps) else "Unknown"
                    filename = f"frame_{idx:04d}_missing.png"
                    status = "MISSING"
                
                metadata_lines.append(
                    f"{idx:4d} | {timestamp.toString('yyyy-MM-dd HH:mm:ss') if hasattr(timestamp, 'toString') else timestamp} | {filename} | {status}"
                )
            
            # Save metadata file
            metadata_path = os.path.join(batch_dir, "_metadata.txt")
            with open(metadata_path, 'w') as f:
                f.write('\n'.join(metadata_lines))
            
            progress.setValue(len(self.frames))
            
            if not progress.wasCanceled():
                QMessageBox.information(
                    self, "Batch Download Complete",
                    f"Successfully saved {saved_count}/{len(self.frames)} frames to:\n{batch_dir}\n\n"
                    f"Metadata saved to: _metadata.txt"
                )
        except Exception as e:
            progress.close()
            QMessageBox.critical(
                self, "Error",
                f"Error during batch download:\n{str(e)}"
            )
        finally:
            progress.close()
    
    def closeEvent(self, event):
        """Handle window close."""
        # Set closing flag first to prevent signal handlers from updating UI
        self._closing = True
        
        # Clear download queue first to prevent new downloads from starting
        self.download_queue.clear()
        
        # Stop all loaders (both ImageDownloader and FrameLoader)
        for loader in self.frame_loaders:
            if hasattr(loader, 'stop'):
                loader.stop()
        
        # Wait for all threads to finish with a timeout
        remaining_threads = []
        for loader in self.frame_loaders:
            if loader.isRunning():
                # Wait up to 1 second per thread (fast cleanup)
                if not loader.wait(1000):
                    print(f"[WARNING] Thread {loader} did not stop in time, moving to background")
                    remaining_threads.append(loader)
        
        # Move stuck threads to global list to prevent GC crash
        if remaining_threads:
            global _active_threads
            _active_threads.extend(remaining_threads)
            
            # Clean up global list of finished threads
            _active_threads = [t for t in _active_threads if t.isRunning()]
            
        self.frame_loaders.clear()
        self.pause_animation()
        event.accept()


def main():
    import sys
    app = QApplication(sys.argv)
    window = HelioviewerBrowser()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
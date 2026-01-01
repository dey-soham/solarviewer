#!/usr/bin/env python3
"""
Solar Activity Viewer GUI - Comprehensive solar context data display.
"""

import sys
import os
import re
from datetime import datetime, date
from typing import Optional, List

# Qt imports
try:
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QPushButton, QDateEdit, QTableWidget, QTableWidgetItem,
        QHeaderView, QGroupBox, QSplitter, QFrame, QScrollArea,
        QSizePolicy, QMessageBox, QProgressBar, QDialog, QTextBrowser,
        QTabWidget
    )
    from PyQt5.QtCore import Qt, QDate, QThread, pyqtSignal, QUrl, QSize
    from PyQt5.QtGui import QFont, QColor, QPalette, QIcon, QPixmap
    from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
except ImportError:
    print("PyQt5 is required. Install with: pip install PyQt5")
    sys.exit(1)

try:
    # Try relative imports (when run as module)
    from . import noaa_events as ne
    from ..styles import theme_manager
except ImportError:
    # Fallback for standalone execution
    # Add project root to path to allow absolute imports
    import sys
    import os
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(current_dir, "../../"))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
        
    from solar_radio_image_viewer.noaa_events import noaa_events as ne
    from solar_radio_image_viewer.styles import theme_manager
import requests

class ClickableLabel(QLabel):
    """QLabel that emits a clicked signal."""
    clicked = pyqtSignal()
    def mouseReleaseEvent(self, event):
        self.clicked.emit()

class FullImageViewer(QDialog):
    """Dialog to view high-resolution image."""
    def __init__(self, parent, title, page_url):
        super().__init__(parent)
        self.setWindowTitle(f"{title} - High Resolution")
        self.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.resize(1920, 1080)
        self.page_url = page_url
        
        layout = QVBoxLayout(self)
        
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True) # Start resizable, maybe set False when huge image loads?
        self.scroll.setStyleSheet("background-color: #222;")
        
        self.img_label = QLabel("Resolving high-resolution image URL...")
        self.img_label.setAlignment(Qt.AlignCenter)
        self.img_label.setStyleSheet("color: #ccc; font-weight: bold;")
        
        self.scroll.setWidget(self.img_label)
        layout.addWidget(self.scroll)
        
        # Close btn
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        btn_layout.addWidget(close)
        layout.addLayout(btn_layout)
        
        # Start Resolve
        self.resolve_url()
        
    def resolve_url(self):
        self.resolver = ImageUrlResolver(self.page_url)
        self.resolver.found.connect(self.on_url_found)
        self.resolver.start()
    
    def closeEvent(self, event):
        """Clean up threads when dialog is closed."""
        # Stop resolver thread if running
        if hasattr(self, 'resolver') and self.resolver is not None:
            if self.resolver.isRunning():
                self.resolver.quit()
                self.resolver.wait(1000)
        # Stop downloader thread if running
        if hasattr(self, 'downloader') and self.downloader is not None:
            if self.downloader.isRunning():
                self.downloader.quit()
                self.downloader.wait(1000)
        super().closeEvent(event)
        
    def on_url_found(self, full_url):
        try:
            if not self.isVisible() and not self.parent(): return
            
            if not full_url:
                self.img_label.setText("Failed to resolve high-res image.")
                return
                
            self.img_label.setText("Loading... Please wait")
            
            # Download
            self.downloader = ImageLoader(full_url)
            self.downloader.loaded.connect(self.on_image_loaded)
            self.downloader.error.connect(self.on_image_error)
            self.downloader.start()
        except RuntimeError:
            pass
    
    def on_image_error(self, error_msg):
        """Handle image download error safely."""
        try:
            if self.isVisible():
                self.img_label.setText(f"Error: {error_msg}")
        except RuntimeError:
            pass  # Widget was deleted
        
    def on_image_loaded(self, data):
        try:
            if not self.isVisible(): return
            
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                self.img_label.setPixmap(pixmap)
                self.img_label.adjustSize()
                # If huge, maybe enable scrollbars
                self.scroll.setWidgetResizable(True) # If true, it shrinks image to fit? No, QLabel usually expands.
                # To scroll, widgetResizable is complicated.
                # If we want scroll, setWidgetResizable(False) implies widget dictates size.
                if pixmap.width() > self.scroll.width() or pixmap.height() > self.scroll.height():
                     self.scroll.setWidgetResizable(False) # Let label be big
                else:
                     self.scroll.setWidgetResizable(True) # Center it
            else:
                self.img_label.setText("Failed to load image data.")
        except RuntimeError:
            pass

class ImageUrlResolver(QThread):
    found = pyqtSignal(str)
    def __init__(self, page_url):
        super().__init__()
        self.url = page_url
    def run(self):
        from ..solar_context import context_images as ci
        url = ci.resolve_full_image_url(self.url)
        self.found.emit(url)


class ImageLoader(QThread):
    """
    Thread to download image data. 
    If page_url is provided, it tries to resolve the High-Res image first.
    Otherwise (or if resolve fails), it falls back to the direct url (thumbnail).
    """
    loaded = pyqtSignal(bytes)
    error = pyqtSignal(str)
    
    def __init__(self, url, page_url=None):
        super().__init__()
        self.url = url
        self.page_url = page_url
        
    def run(self):
        try:
            target_url = self.url
            # Try to resolve high-res if page_url available
            # BUT: Skip for Helioviewer URLs (they're already direct image URLs)
            if self.page_url and 'helioviewer.org' not in self.page_url:
                try:
                    from ..solar_context import context_images as ci
                    resolved = ci.resolve_full_image_url(self.page_url)
                    if resolved:
                        target_url = resolved
                except Exception as e:
                    print(f"Failed to resolve high-res: {e}")
            
            import requests
            response = requests.get(target_url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
            if response.status_code == 200:
                self.loaded.emit(response.content)
            else:
                self.error.emit(f"HTTP {response.status_code}")
        except Exception as e:
            self.error.emit(str(e))


class FetchWorker(QThread):
    """Worker thread for fetching events, active regions, conditions, CMEs, and images."""
    finished = pyqtSignal(object, object, object, object, object)  # (events, active_regions, conditions, cmes, images) tuple
    error = pyqtSignal(str)
    
    def __init__(self, event_date: date):
        super().__init__()
        self.event_date = event_date
    
    
    def run(self):
        try:
            # Fetch solar events
            events = ne.fetch_and_parse_events(self.event_date)
            
            # Fetch active regions
            active_regions = None
            try:
                from ..solar_context import active_regions as ar
                active_regions = ar.fetch_and_parse_active_regions(self.event_date)
            except Exception as ar_err:
                print(f"Active regions fetch failed: {ar_err}")
            
            # Fetch solar conditions for the selected date
            conditions = None
            try:
                from ..solar_context import realtime_data as rt
                conditions = rt.fetch_conditions_for_date(self.event_date)
            except Exception as cond_err:
                print(f"Solar conditions fetch failed: {cond_err}")
            
            # Fetch CME alerts
            cmes = None
            try:
                from ..solar_context import cme_alerts as cme
                cmes = cme.fetch_and_parse_cme_events(self.event_date)
            except Exception as cme_err:
                print(f"CME fetch failed: {cme_err}")

            # Fetch Context Images URLs
            images = []
            try:
                from ..solar_context import context_images as ci
                images = ci.fetch_context_images(self.event_date)
            except Exception as img_err:
                print(f"Context images fetch failed: {img_err}")

            self.finished.emit(events, active_regions, conditions, cmes, images)
        except Exception as e:
            self.error.emit(str(e))


class GOESPlotWorker(QThread):
    """Worker thread for fetching and plotting GOES X-ray flux."""
    finished = pyqtSignal(object)
    error = pyqtSignal(str)
    
    def __init__(self, event_date: date):
        super().__init__()
        self.event_date = event_date
        
    def run(self):
        try:
            from sunpy.net import Fido, attrs as a
            from sunpy.timeseries import TimeSeries
            import matplotlib.pyplot as plt
            
            # Define time range for the full day
            t_start = datetime.combine(self.event_date, datetime.min.time())
            t_end = datetime.combine(self.event_date, datetime.max.time())
            
            # Search for GOES XRS data
            # Use a.Resolution.flx1s (1-second data) if possible, or avg1m (1-minute)
            # print(f"Searching for GOES data for {self.event_date}...")
            res = Fido.search(a.Time(t_start, t_end), a.Instrument('GOES'))
            
            if len(res) == 0:
                raise Exception("No GOES X-ray data found for this date.")
                
            # Filter results to get the "best" single file
            # 1. Prefer High Cadence (flx1s) over Average (avg1m)
            
            # Simple conversion to astropy table to sort/filter
            # tbl = res[0]
            
            # Searching for 'flx1s' first
            res_high = Fido.search(a.Time(t_start, t_end), a.Instrument('GOES'), a.Resolution('flx1s'))
            
            if len(res_high) > 0:
                res = res_high
            else:
                 pass # Fallback to whatever we found (likely 1m)

            # If we still have multiple satellites (e.g. 16 and 18), pick one.
            # Converting to list of rows and picking the first one is safest to avoid downloading 4 files.
            
            # Slice the UnifiedResponse to keep only the first row of the first provider results
            best_result = res[0, 0]
            
            # print(f"Downloading the first available match: {best_result}")
            files = Fido.fetch(best_result)
            
            if not files:
                raise Exception("Failed to download GOES data file.")
                
            # Load TimeSeries
            ts = TimeSeries(files)
            
            # Concatenate if multiple files (though usually one per day/search)
            if isinstance(ts, list):
                if len(ts) > 1:
                     # TODO: Concatenate the TimeSeries objects
                     pass
            
            self.finished.emit(ts)
            
        except Exception as e:
            self.error.emit(str(e))


class CollapsibleSection(QWidget):
    """A collapsible section widget with header and content."""
    toggled = pyqtSignal(bool)
    
    def __init__(self, title: str, icon: str = "", count: int = 0, parent=None):
        super().__init__(parent)
        self.is_collapsed = False
        
        # Allow expanding vertically
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Header
        self.header = QPushButton()
        
        # Theme-aware styling
        palette = theme_manager.palette
        is_dark = theme_manager.is_dark
        
        if is_dark:
            bg_normal = "rgba(128, 128, 128, 0.12)"
            bg_hover = "rgba(128, 128, 128, 0.18)"
            bg_pressed = "rgba(128, 128, 128, 0.1)"
            border = "none"
            text_color = palette['text']
        else:
            # Light theme: Use distinct solid colors
            bg_normal = palette['button']  # Distinct from window background
            bg_hover = palette['button_hover']
            bg_pressed = palette['button_pressed']
            border = f"1px solid {palette['border']}"
            text_color = palette['text']
            
        self.header.setStyleSheet(f"""
            QPushButton {{
                text-align: left;
                padding: 12px 16px;
                font-weight: 600;
                border: {border};
                border-radius: 8px;
                background-color: {bg_normal};
                color: {text_color};
            }}
            QPushButton:hover {{
                background-color: {bg_hover};
                border-color: {palette['highlight']};
            }}
            QPushButton:pressed {{
                background-color: {bg_pressed};
            }}
        """)
        self.update_header(title, icon, count)
        self.header.clicked.connect(self.toggle)
        layout.addWidget(self.header)
        
        # Content container
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 5, 0, 5)
        layout.addWidget(self.content)
        
        self.title = title
        self.icon = icon
    
    def update_header(self, title: str, icon: str = "", count: int = 0):
        arrow = "▼" if not self.is_collapsed else "▶"
        count_str = f" [{count}]" if count > 0 else ""
        self.header.setText(f"{arrow}  {icon} {title} {count_str}")
    
    def toggle(self):
        self.is_collapsed = not self.is_collapsed
        self.content.setVisible(not self.is_collapsed)
        self.update_header(self.title, self.icon, 
                          getattr(self, '_count', 0))
        
        # Update size policy based on state
        if self.is_collapsed:
            self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        else:
            self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
            
        self.toggled.emit(self.is_collapsed)
    
    def set_count(self, count: int):
        self._count = count
        self.update_header(self.title, self.icon, count)
    
    def add_widget(self, widget):
        self.content_layout.addWidget(widget)


class EventTable(QTableWidget):
    """Custom table widget for displaying events."""
    
    def __init__(self, columns: list, parent=None):
        super().__init__(parent)
        self.setColumnCount(len(columns))
        self.setHorizontalHeaderLabels(columns)
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.verticalHeader().setVisible(False)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setSortingEnabled(True)
        self.setShowGrid(False)
        
        # Allow table to grow
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        # Better row height
        self.verticalHeader().setDefaultSectionSize(32)
        
        # Modern table styling handled by global stylesheet
        pass
    
    def add_event_row(self, values: list, colors: dict = None):
        """Add a row with optional cell coloring."""
        # Temporarily disable sorting to prevent row movement during insertion
        sorting_enabled = self.isSortingEnabled()
        self.setSortingEnabled(False)
        
        row = self.rowCount()
        self.insertRow(row)
        for col, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setTextAlignment(Qt.AlignCenter)
            if colors and col in colors:
                item.setForeground(QColor(colors[col]))
            self.setItem(row, col, item)
        
        # Re-enable sorting
        self.setSortingEnabled(sorting_enabled)


class NOAAEventsViewer(QMainWindow):
    """Main Solar Activity Viewer window - displays events, active regions, conditions, and CMEs."""
    
    def __init__(self, parent=None, initial_date: Optional[date] = None):
        super().__init__(parent)
        self.setWindowTitle("☀️ Solar Activity Viewer")
        self.resize(1000, 800)
        
        # Network Manager for image downloading
        self.nam = QNetworkAccessManager(self)
        self.image_downloads = {}  # Keep references to replies
        self.image_viewers = []    # Keep references to open image windows
        
        self.worker = None
        self.goes_worker = None
        self.events = []
        
        # Initial load state to manage cursor
        self._initial_load = False
        if initial_date:
            self._initial_load = True
            from PyQt5.QtWidgets import QApplication
            from PyQt5.QtCore import Qt
            QApplication.setOverrideCursor(Qt.WaitCursor)
            
        # Detect theme (dark vs light)
        self.is_dark_theme = theme_manager.is_dark
        self.setStyleSheet(theme_manager.stylesheet)
        
        # Improve Light Theme: Override window background to use 'base' (lighter) instead of 'window' (muddy)
        if not self.is_dark_theme:
            palette = theme_manager.palette
            # Use base color for main window and dialogs to reduce the heavy beige look
            # Use 'window' color (darker beige) for panels/containers to create hierarchy
            light_overrides = f"""
                QMainWindow, QDialog {{
                    background-color: {palette['base']};
                }}
                QTabWidget::pane {{
                    background-color: {palette['plot_bg']};
                    border: 1px solid {palette['border']};
                }}
            """
            self.setStyleSheet(theme_manager.stylesheet + light_overrides)
        
        self.init_ui()
        
        # Set initial date
        if initial_date:
            self.date_edit.setDate(QDate(initial_date.year, initial_date.month, initial_date.day))
        else:
            # Default to yesterday
            yesterday = QDate.currentDate().addDays(-1)
            self.date_edit.setDate(yesterday)
    
    def closeEvent(self, event):
        """Clean up worker threads when window is closed."""
        # Stop fetch worker if running
        if hasattr(self, 'worker') and self.worker is not None:
            if self.worker.isRunning():
                self.worker.quit()
                self.worker.wait(2000)
        # Stop GOES plot worker if running
        if hasattr(self, 'goes_worker') and self.goes_worker is not None:
            if self.goes_worker.isRunning():
                self.goes_worker.quit()
                self.goes_worker.wait(2000)
        super().closeEvent(event)
    
    def init_ui(self):
        """Initialize the user interface."""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(16)
        layout.setContentsMargins(20, 16, 20, 16)
        
        # Modern button styles from theme_manager
        
        # Top bar: date selection
        top_bar = QHBoxLayout()
        top_bar.setSpacing(12)
        
        date_label = QLabel("Date:")
        date_label.setStyleSheet("font-weight: bold;")
        top_bar.addWidget(date_label)
        
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy.MM.dd")
        self.date_edit.setMaximumDate(QDate.currentDate())
        # Styles handled by global stylesheet
        top_bar.addWidget(self.date_edit)
        
        # Get date from current tab button
        self.get_date_btn = QPushButton("📅 From Tab")
        self.get_date_btn.setToolTip("Get date from currently open image/FITS file")
        self.get_date_btn.clicked.connect(self.get_date_from_parent_tab)
        if not self.parent():
            self.get_date_btn.setEnabled(False)
            self.get_date_btn.setToolTip("Not available in independent mode")
        top_bar.addWidget(self.get_date_btn)

        top_bar.addStretch()

        self.progress = QProgressBar()
        self.progress.setMaximumWidth(150)
        self.progress.setMaximum(0)  # Indeterminate
        self.progress.hide()
        top_bar.addWidget(self.progress)
        
        self.fetch_btn = QPushButton("🔍 Fetch")
        self.fetch_btn.setObjectName("PrimaryButton")
        self.fetch_btn.clicked.connect(self.fetch_data)
        top_bar.addWidget(self.fetch_btn)
        
       
        layout.addLayout(top_bar)
        
        # Modern summary bar
        self.summary_frame = QFrame()
        
        # Use simple gradient based on palette
        palette = theme_manager.palette
        highlight = palette['highlight']
        
        # Convert hex to rgba for transparent gradient
        self.summary_frame.setStyleSheet(f"""
            QFrame {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {highlight}1A,
                    stop:1 {highlight}0D);
                border-radius: 10px;
                border: 1px solid {highlight}33;
            }}
        """)
        summary_layout = QHBoxLayout(self.summary_frame)
        summary_layout.setContentsMargins(20, 16, 20, 16)
        
        self.summary_label = QLabel("Select a date and click 'Fetch Events' to view solar activity.")
        self.summary_label.setStyleSheet("font-weight: 500;")
        self.summary_label.setWordWrap(True)
        summary_layout.addWidget(self.summary_label)
        
        layout.addWidget(self.summary_frame)
        
        # Modern tab styling handled by global stylesheet
        self.tabs = QTabWidget()
        
        # Tab 1: Solar Events (existing content)
        events_tab = QWidget()
        events_layout = QVBoxLayout(events_tab)
        events_layout.setContentsMargins(24, 24, 24, 24)
        events_layout.setSpacing(24)
        
        # X-ray Flares section
        self.xray_section = CollapsibleSection("X-ray Flares", "☀️")
        self.xray_table = EventTable(["Time (UT)", "Class", "Peak Flux", "Region", "Duration", "Observatory"])
        self.xray_section.add_widget(self.xray_table)
        events_layout.addWidget(self.xray_section)
        
        # Optical Flares section
        self.optical_section = CollapsibleSection("Optical Flares (H-alpha)", "🔥")
        self.optical_table = EventTable(["Time (UT)", "Class", "Location", "Region", "Notes", "Observatory"])
        self.optical_section.add_widget(self.optical_table)
        events_layout.addWidget(self.optical_section)
        
        # Radio Events section
        self.radio_section = CollapsibleSection("Radio Events", "📻")
        self.radio_table = EventTable(["Type", "Time (UT)", "Frequency", "Particulars", "Region", "Observatory"])
        self.radio_section.add_widget(self.radio_table)
        events_layout.addWidget(self.radio_section)
        
        # Connect signals for dynamic layout
        self.xray_section.toggled.connect(self.update_events_layout_logic)
        self.optical_section.toggled.connect(self.update_events_layout_logic)
        self.radio_section.toggled.connect(self.update_events_layout_logic)
        
        # Dynamic spacer - stays hidden unless all sections are collapsed
        self.events_bottom_spacer = QWidget()
        self.events_bottom_spacer.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        events_layout.addWidget(self.events_bottom_spacer)
        
        # Initial logic check
        self.update_events_layout_logic()
        
        # Make events tab scrollable
        events_scroll = QScrollArea()
        events_scroll.setWidgetResizable(True)
        events_scroll.setFrameShape(QFrame.NoFrame)
        events_scroll.setWidget(events_tab)
        self.tabs.addTab(events_scroll, "☀️ Solar Events")
        
        # Tab 2: Active Regions
        ar_tab = QWidget()
        ar_layout = QVBoxLayout(ar_tab)
        ar_layout.setContentsMargins(24, 24, 24, 24)
        ar_layout.setSpacing(24)
        
        # Active regions table
        self.ar_table = EventTable([
            "AR#", "Location", "Area", "McIntosh", "Mag Type", 
            "C%", "M%", "X%", "Risk Level"
        ])
        ar_layout.addWidget(self.ar_table)
        
        # AR info label
        self.ar_info_label = QLabel("Fetch data to view active sunspot regions and flare probabilities.")
        self.ar_info_label.setWordWrap(True)
        self.ar_info_label.setStyleSheet(f"color: {theme_manager.palette['text']}; font-style: italic; padding: 10px; font-weight: light; opacity: 0.4;")
        ar_layout.addWidget(self.ar_info_label)

        
        #ar_layout.addStretch()
        
        # Make AR tab scrollable
        ar_scroll = QScrollArea()
        ar_scroll.setWidgetResizable(True)
        ar_scroll.setFrameShape(QFrame.NoFrame)
        ar_scroll.setWidget(ar_tab)
        self.tabs.addTab(ar_scroll, "🌡️ Active Regions")
        
        # Tab 3: Solar Conditions (Real-time data)
        conditions_tab = QWidget()
        conditions_layout = QVBoxLayout(conditions_tab)
        conditions_layout.setContentsMargins(24, 24, 24, 24)
        conditions_layout.setSpacing(24)
        
        # Geomagnetic Activity Card - modern styling
        geo_card = QFrame()
        if self.is_dark_theme:
            geo_card.setStyleSheet("""
                QFrame {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                        stop:0 rgba(99, 102, 241, 0.12),
                        stop:1 rgba(99, 102, 241, 0.06));
                    border-radius: 12px;
                    border: 1px solid rgba(99, 102, 241, 0.25);
                }
            """)
        else:
            palette = theme_manager.palette
            geo_card.setStyleSheet(f"""
                QFrame {{
                    background-color: {palette['surface']};
                    border-radius: 12px;
                    border: 1px solid {palette['border']};
                }}
            """)
        geo_layout = QVBoxLayout(geo_card)
        geo_layout.setContentsMargins(20, 20, 20, 20)
        
        geo_title = QLabel("🧭 Geomagnetic Activity (Daily)")
        geo_title.setStyleSheet("font-weight: bold;")
        geo_layout.addWidget(geo_title)
        
        self.geo_ap_label = QLabel("Ap Index: —")
        self.geo_kp_max_label = QLabel("Kp max: —")
        self.geo_kp_avg_label = QLabel("Kp avg: —")
        self.geo_kp_vals_label = QLabel("3-hour Kp values: —")
        self.geo_storm_label = QLabel("Storm Level: —")
        
        for lbl in [self.geo_ap_label, self.geo_kp_max_label, self.geo_kp_avg_label, self.geo_storm_label, self.geo_kp_vals_label]:
            lbl.setStyleSheet("padding-left: 10px;")
            geo_layout.addWidget(lbl)
        
        conditions_layout.addWidget(geo_card)
        
        # Solar Wind Card - modern styling
        self.wind_card = QFrame()
        if self.is_dark_theme:
            self.wind_card.setStyleSheet("""
                QFrame {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                        stop:0 rgba(16, 185, 129, 0.12),
                        stop:1 rgba(16, 185, 129, 0.06));
                    border-radius: 12px;
                    border: 1px solid rgba(16, 185, 129, 0.25);
                }
            """)
        else:
            palette = theme_manager.palette
            self.wind_card.setStyleSheet(f"""
                QFrame {{
                    background-color: {palette['surface']};
                    border-radius: 12px;
                    border: 1px solid {palette['border']};
                }}
            """)
        wind_layout = QVBoxLayout(self.wind_card)
        wind_layout.setContentsMargins(20, 20, 20, 20)
        
        wind_title = QLabel("💨 Solar Wind (Real-time)")
        wind_title.setStyleSheet("font-weight: bold;")
        wind_layout.addWidget(wind_title)
        
        self.sw_speed_label = QLabel("Speed: — km/s")
        self.sw_density_label = QLabel("Density: — p/cm³")
        self.sw_temp_label = QLabel("Temperature: — K")
        self.sw_status_label = QLabel("Status: —")
        
        for lbl in [self.sw_speed_label, self.sw_density_label, self.sw_temp_label, self.sw_status_label]:
             lbl.setStyleSheet("padding-left: 10px;")
             wind_layout.addWidget(lbl)
             
        conditions_layout.addWidget(self.wind_card)
        self.wind_card.hide() # Only show when available
        
        # F10.7 Flux card - modern styling
        f107_card = QFrame()
        if self.is_dark_theme:
            f107_card.setStyleSheet("""
                QFrame {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                        stop:0 rgba(251, 146, 60, 0.12),
                        stop:1 rgba(251, 146, 60, 0.06));
                    border-radius: 12px;
                    border: 1px solid rgba(251, 146, 60, 0.25);
                }
            """)
        else:
            palette = theme_manager.palette
            f107_card.setStyleSheet(f"""
                QFrame {{
                    background-color: {palette['surface']};
                    border-radius: 12px;
                    border: 1px solid {palette['border']};
                }}
            """)
        f107_layout = QVBoxLayout(f107_card)
        f107_layout.setContentsMargins(20, 20, 20, 20)
        
        f107_title = QLabel("☀️ Solar Indices (Daily)")
        f107_title.setStyleSheet("font-weight: bold;")
        f107_layout.addWidget(f107_title)
        
        self.f107_value_label = QLabel("Flux: — sfu")
        self.sunspot_area_label = QLabel("Sunspot Area: —")
        #self.xray_bg_label = QLabel("X-Ray Background: —")
        self.f107_activity_label = QLabel("Activity Level: —")
        
        #for lbl in [self.f107_value_label, self.sunspot_area_label, self.xray_bg_label, self.f107_activity_label]:
        for lbl in [self.f107_value_label, self.sunspot_area_label, self.f107_activity_label]:
            lbl.setStyleSheet("padding-left: 10px;")
            f107_layout.addWidget(lbl)
            
        # Add GOES Plot Button
        self.plot_goes_btn = QPushButton("📈 Plot GOES X-ray Flux")
        self.plot_goes_btn.setToolTip("Plot the GOES X-ray light curve for this date")

        self.plot_goes_btn.clicked.connect(self.plot_goes_xray)
        f107_layout.addWidget(self.plot_goes_btn)
        self.plot_goes_btn.setEnabled(True)
        
        conditions_layout.addWidget(f107_card)
        
        # Conditions info label - theme-aware
        self.conditions_info_label = QLabel("⚡ Real-time solar conditions from NOAA SWPC")
        self.conditions_info_label.setWordWrap(True)
        self.conditions_info_label.setStyleSheet(f"color: {theme_manager.palette['text']}; font-style: italic; padding: 10px; font-weight: light; opacity: 0.4;")
        conditions_layout.addWidget(self.conditions_info_label)
        
        conditions_layout.addStretch()
        
        # Make conditions tab scrollable
        conditions_scroll = QScrollArea()
        conditions_scroll.setWidgetResizable(True)
        conditions_scroll.setFrameShape(QFrame.NoFrame)
        conditions_scroll.setWidget(conditions_tab)
        self.tabs.addTab(conditions_scroll, "⚡ Solar Conditions")
        
        # Tab 4: CME Alerts
        cme_tab = QWidget()
        cme_layout = QVBoxLayout(cme_tab)
        cme_layout.setContentsMargins(24, 24, 24, 24)
        cme_layout.setSpacing(24)
        
        # CME table
        self.cme_table = EventTable([
            "Time (UT)", "Speed (km/s)", "Source", "Width", "Earth Dir.", "Est. Arrival"
        ])
        cme_layout.addWidget(self.cme_table)
        
        # CME info label - theme-aware
        self.cme_info_label = QLabel("🚀 CME data from NASA DONKI (±3 days from selected date)")
        self.cme_info_label.setWordWrap(True)
        self.cme_info_label.setStyleSheet(f"color: {theme_manager.palette['text']}; font-style: italic; padding: 10px; font-weight: light; opacity: 0.4;")
        cme_layout.addWidget(self.cme_info_label)
        
        # cme_layout.addStretch()
        
        # Make CME tab scrollable
        cme_scroll = QScrollArea()
        cme_scroll.setWidgetResizable(True)
        cme_scroll.setFrameShape(QFrame.NoFrame)
        cme_scroll.setWidget(cme_tab)
        self.tabs.addTab(cme_scroll, "🚀 CME Alerts")

        # Tab 5: Context Images
        images_tab = QWidget()
        images_layout = QVBoxLayout(images_tab)
        
        images_scroll = QScrollArea()
        images_scroll.setWidgetResizable(True)
        images_scroll_content = QWidget()
        self.images_grid = QVBoxLayout(images_scroll_content) # Use VBox for list of cards or Grid
        self.images_grid.setSpacing(16)
        self.images_grid.setContentsMargins(24, 24, 24, 24)
        
        images_scroll.setWidget(images_scroll_content)
        images_layout.addWidget(images_scroll)
        
        self.tabs.addTab(images_tab, "📷 Context Images")
        
        layout.addWidget(self.tabs)
    
    def update_events_layout_logic(self, *args):
        """Show/hide bottom spacer based on whether any section is open."""
        any_open = not (self.xray_section.is_collapsed and 
                        self.optical_section.is_collapsed and 
                        self.radio_section.is_collapsed)
        
        # If any section is open, hide spacer so the open section can expand
        # If all are closed, show spacer to push headers to the top
        if hasattr(self, 'events_bottom_spacer'):
            self.events_bottom_spacer.setVisible(not any_open)

    def fetch_data(self):
        """Start fetching data for the selected date."""
        # Ensure imports are available for whole scope
        from PyQt5.QtWidgets import QApplication
        from PyQt5.QtCore import Qt

        if self.worker and self.worker.isRunning():
            return
            
        # Show busy cursor immediately
        # If this is the initial load, cursor was already set in __init__
        if getattr(self, '_initial_load', False):
            self._initial_load = False 
        else:
            QApplication.setOverrideCursor(Qt.WaitCursor)
        
        qdate = self.date_edit.date()
        selected_date = date(qdate.year(), qdate.month(), qdate.day())
        
        self.date_edit.setEnabled(False)
        self.fetch_btn.setEnabled(False)
        self.summary_label.setText(f"Fetching data for {selected_date}...")
        self.progress.show()
        
        QApplication.processEvents()  # Force UI update immediately
        
        # Clean up old worker if exists
        if self.worker is not None:
            self.worker.finished.disconnect()
            self.worker.error.disconnect()
            self.worker.deleteLater()
        
        self.worker = FetchWorker(selected_date)
        self.worker.finished.connect(self.on_fetch_finished)
        self.worker.error.connect(self.on_fetch_error)
        self.worker.start()
    
    def on_fetch_finished(self, events, active_regions, conditions, cmes, images):
        """Handle fetched data."""
        try:
            # Check for validity
            if not self.isVisible() and not self.parent():
                 return # Window closed

            # Restore cursor
            from PyQt5.QtWidgets import QApplication
            QApplication.restoreOverrideCursor()
            
            self.date_edit.setEnabled(True)
            self.fetch_btn.setEnabled(True)
            self.fetch_btn.setText("🔍 Fetch")
            self.progress.hide()
            self.events = events
            
            # Display events
            self.display_events(events)
            
            # Display active regions
            self.display_active_regions(active_regions)
            
            # Display conditions
            self.display_solar_conditions(conditions)
            
            # Display CMEs
            self.display_cme_events(cmes)
            
            # Display images
            self.display_context_images(images)
            
            # Update comprehensive summary
            self._update_comprehensive_summary(events, active_regions, conditions, cmes)
            
        except RuntimeError:
            # Widget deleted during update
            pass

    def _update_comprehensive_summary(self, events, active_regions, conditions, cmes):
        """Update the main summary label with a comprehensive overview of all data."""
        summary_parts = []
        
        # 1. Active Regions
        ar_count = len(active_regions) if active_regions else 0
        if ar_count > 0:
            summary_parts.append(f"Regions: {ar_count}")
        elif active_regions is not None:
             summary_parts.append("Regions: 0")

        # 2. Sunspots & Flux (from conditions)
        if conditions and conditions.f107_flux:
            ssn = conditions.f107_flux.sunspot_number
            flux = conditions.f107_flux.flux_value
            summary_parts.append(f"Sunspots: {ssn}")
            summary_parts.append(f"Flux: {flux:.0f} sfu")
        
        # 3. Solar Flares (from events)
        if events:
            categories = ne.categorize_events(events)
            xray = categories.get("xray", [])
            stats = ne.get_event_statistics(events)
            max_class = stats.get("max_xray_class", None)
            
            flare_part = f"Flares: {len(xray)}"
            if max_class:
                flare_part += f" (Max: {max_class})"
            summary_parts.append(flare_part)
        else:
             summary_parts.append("Flares: 0")

        # 4. CMEs
        if cmes:
            cme_count = len(cmes)
            earth_directed = sum(1 for cme in cmes if cme.is_earth_directed)
            cme_text = f"CMEs: {cme_count}"
            if earth_directed > 0:
                cme_text += f" (🌍 {earth_directed})"
            summary_parts.append(cme_text)
        elif cmes is not None:
             summary_parts.append("CMEs: 0")

        if not summary_parts:
            self.summary_label.setText("No data available for this date.")
        else:
            self.summary_label.setText(" | ".join(summary_parts))

    
    def on_fetch_error(self, error_msg):
        """Handle fetch error."""
        try:
             # Check validity
            if not self.isVisible() and not self.parent(): return
            
            from PyQt5.QtWidgets import QApplication
            from PyQt5.QtWidgets import QMessageBox
            QApplication.restoreOverrideCursor()
            
            self.date_edit.setEnabled(True)
            self.fetch_btn.setEnabled(True)
            self.fetch_btn.setText("🔍 Fetch")
            self.progress.hide()
            
            self.summary_label.setText(f"Error fetching data: {error_msg}")
            QMessageBox.critical(self, "Fetch Error", f"Failed to fetch data")
        except RuntimeError:
            pass
    
    def clear_tables(self):
        """Clear all event tables."""
        self.xray_table.setRowCount(0)
        self.optical_table.setRowCount(0)
        self.radio_table.setRowCount(0)
        self.ar_table.setRowCount(0)
        self.cme_table.setRowCount(0)
        self.xray_section.set_count(0)
        self.optical_section.set_count(0)
        self.radio_section.set_count(0)
    
    def display_events(self, events):
        """Display events in categorized tables."""
        self.clear_tables()
        
        if events is None:
            self.summary_label.setText("No data could be fetched.")
            return

        categories = ne.categorize_events(events)
        stats = ne.get_event_statistics(events)
        
        # Update summary - MOVED to _update_comprehensive_summary
        xray_count = len(categories["xray"])
        optical_count = len(categories["optical"])
        radio_count = len(categories["radio"])
        # max_class = stats.get("max_xray_class", "—")
        
        # summary_parts = []
        # if xray_count > 0:
        #     max_note = f" (max: {max_class})" if max_class else ""
        #     summary_parts.append(f"📊 {xray_count} X-ray flare{'s' if xray_count > 1 else ''}{max_note}")
        # if optical_count > 0:
        #     summary_parts.append(f"{optical_count} Optical")
        # if radio_count > 0:
        #     summary_parts.append(f"{radio_count} Radio")
        
        # if summary_parts:
        #     self.summary_label.setText(" | ".join(summary_parts))
        # else:
        #     self.summary_label.setText("No significant events recorded for this date.")
        
        # Populate X-ray table
        self.xray_section.set_count(xray_count)
        for event in sorted(categories["xray"], key=lambda e: e.begin_time or "9999"):
            duration = f"{event.duration_minutes} min" if event.duration_minutes else "—"
            flare_class = event.flare_class or "—"
            peak_flux = event.particulars.split()[1] if len(event.particulars.split()) > 1 else "—"
            
            color_col = {}
            if event.flare_class_letter in ["M", "X"]:
                color_col[1] = event.flare_class_color
            
            self.xray_table.add_event_row([
                event.time_range,
                flare_class,
                peak_flux,
                event.active_region or "—",
                duration,
                event.observatory_name,
            ], color_col)
        
        # Populate Optical table
        self.optical_section.set_count(optical_count)
        for event in sorted(categories["optical"], key=lambda e: e.begin_time or "9999"):
            optical_class = event.optical_class or "—"
            notes_parts = event.particulars.split()[1:] if event.particulars else []
            notes = " ".join(notes_parts) if notes_parts else "—"
            
            self.optical_table.add_event_row([
                event.time_range,
                optical_class,
                event.location_or_freq,
                event.active_region or "—",
                notes,
                event.observatory_name,
            ])
        
        # Populate Radio table
        self.radio_section.set_count(radio_count)
        for event in sorted(categories["radio"], key=lambda e: e.begin_time or "9999"):
            type_name = ne.EVENT_TYPES.get(event.event_type, {}).get("name", event.event_type)
            
            self.radio_table.add_event_row([
                event.event_type,
                event.time_range,
                event.location_or_freq,
                event.particulars,
                event.active_region or "—",
                event.observatory_name,
            ])
        
        # Resize columns to fit contents and scroll to top
        self.xray_table.resizeColumnsToContents()
        self.optical_table.resizeColumnsToContents()
        self.radio_table.resizeColumnsToContents()
        self.xray_table.scrollToTop()
        self.optical_table.scrollToTop()
        self.radio_table.scrollToTop()
    
    def display_active_regions(self, regions):
        """Display active regions in the AR table."""
        self.ar_table.setRowCount(0)
        
        if regions is None or len(regions) == 0:
            self.ar_info_label.setText("No active regions data available for this date.")
            self.ar_info_label.show()
            return
        
        # self.ar_info_label.hide()
        self.ar_info_label.setText(f"Found {len(regions)} active regions.")
        
        # Color coding for risk levels
        risk_colors = {
            "Very High": "#F44336",  # Red
            "High": "#FF9800",       # Orange
            "Moderate": "#FFC107",   # Amber
            "Low": "#4CAF50",        # Green
            "Quiet": "#9E9E9E",      # Grey
        }
        
        for region in sorted(regions, key=lambda r: r.area, reverse=True):
            # Format probabilities
            c_prob = f"{region.prob_c}%" if region.prob_c is not None else "—"
            m_prob = f"{region.prob_m}%" if region.prob_m is not None else "—"
            x_prob = f"{region.prob_x}%" if region.prob_x is not None else "—"
            
            risk = region.flare_risk_level
            risk_color = risk_colors.get(risk, "#9E9E9E")
            
            # Add row with color for risk level column
            color_col = {8: risk_color}
            
            # Also color M% and X% if they're significant
            if region.prob_m and region.prob_m >= 20:
                color_col[6] = "#FF9800"
            if region.prob_x and region.prob_x >= 5:
                color_col[7] = "#F44336"
            
            self.ar_table.add_event_row([
                f"AR{region.noaa_number}",
                region.location,
                str(region.area),
                region.mcintosh_class,
                region.mag_type,
                c_prob,
                m_prob,
                x_prob,
                risk,
            ], color_col)
        
        self.ar_table.resizeColumnsToContents()
        self.ar_table.scrollToTop()
    
    def display_solar_conditions(self, conditions):
        """Display solar conditions for the selected date."""
        if conditions is None:
            self.conditions_info_label.setText("⚠️ Unable to fetch solar conditions data")
            self.geo_ap_label.setText("Ap Index: —")
            self.geo_kp_max_label.setText("Kp max: —")
            self.geo_kp_avg_label.setText("Kp avg: —")
            self.geo_kp_vals_label.setText("3-hour Kp/Ap values: —")
            self.geo_storm_label.setText("Storm Level: —")
            self.wind_card.hide()
            return
        
        # Update title label to show data source
        self.conditions_info_label.setText(f"📊 {conditions.data_source}")
        
        # 1. Geomagnetic Data (Kp)
        if conditions.kp_index:
            kp = conditions.kp_index
            self.geo_ap_label.setText(f"Ap Index: {kp.ap_value}")
            self.geo_kp_max_label.setText(f"Kp max: {kp.kp_max:.0f}")
            self.geo_kp_avg_label.setText(f"Kp avg: {kp.kp_avg:.1f}")
            self.geo_kp_vals_label.setText(f"8 Kp values: {', '.join([f'{v:.0f}' for v in kp.kp_values])}")
            self.geo_kp_vals_label.setStyleSheet("padding-left: 10px; color: #888;")
            
            self.geo_storm_label.setText(f"Storm Level: {kp.storm_level}")
            self.geo_storm_label.setStyleSheet(f"padding-left: 10px; color: {kp.color_code}; font-weight: bold;")
        else:
            self.geo_ap_label.setText("Ap Index: —")
            self.geo_kp_max_label.setText("Kp max: —")
            self.geo_kp_avg_label.setText("Kp avg: —")
            self.geo_kp_vals_label.setText("No geomagnetic data for this date")
            self.geo_storm_label.setText("Storm Level: Data unavailable")
        
        # 2. Solar Wind Data (Real-time only)
        if hasattr(conditions, 'solar_wind') and conditions.solar_wind:
            sw = conditions.solar_wind
            self.wind_card.show()
            self.sw_speed_label.setText(f"Speed: {sw.speed:.0f} km/s")
            self.sw_density_label.setText(f"Density: {sw.density:.1f} p/cm³")
            self.sw_temp_label.setText(f"Temperature: {sw.temperature:.0f} K")
            
            status_color = "#888"
            status_text = sw.speed_category
            if status_text == "High": status_color = "#F44336"
            elif status_text == "Elevated": status_color = "#FF9800"
            elif status_text == "Normal": status_color = "#4CAF50"
            
            self.sw_status_label.setText(f"Status: {status_text} Speed")
            self.sw_status_label.setStyleSheet(f"padding-left: 10px; color: {status_color}; font-weight: bold;")
        else:
            self.wind_card.hide()
        
        # F10.7 Flux (historical daily data)
        if conditions.f107_flux:
            f107 = conditions.f107_flux
            self.f107_value_label.setText(f"10.7cm Flux: {f107.flux_value:.1f} sfu (Sunspot #: {f107.sunspot_number})")
            
            area = getattr(f107, 'sunspot_area', '—')
            area_str = f"{area} (10⁻⁶ Hemis.)" if area != '—' else "—"
            self.sunspot_area_label.setText(f"Sunspot Area: {area_str}")
            
            '''bg = getattr(f107, 'xray_background', '—')
            bg_text = bg
            bg_color = "#888" # Default gray
            
            if bg == '*':
                bg_text = "N/A"
                bg_color = "#4CAF50" # Green
            elif bg and bg[0] in ['A', 'B']:
                bg_color = "#4CAF50" # Green for A/B
            elif bg and bg.startswith('C'):
                bg_color = "#FF9800" # Orange for C
            elif bg and bg.startswith('M'):
                bg_color = "#F44336" # Red for M
            elif bg and bg.startswith('X'):
                bg_color = "#9C27B0" # Purple for X
                
            self.xray_bg_label.setText(f"X-Ray Background: {bg_text}")
            self.xray_bg_label.setStyleSheet(f"padding-left: 10px; color: {bg_color}; font-weight: bold;")'''
            
            # Color-code activity level
            activity_colors = {
                "Very Low": "#2196F3",
                "Low": "#4CAF50",
                "Moderate": "#FFC107",
                "Elevated": "#FF9800",
                "High": "#F44336",
                "Very High": "#9C27B0",
            }
            color = activity_colors.get(f107.activity_level, "#9E9E9E")
            self.f107_activity_label.setText(f"Activity Level: {f107.activity_level}")
            self.f107_activity_label.setStyleSheet(f"padding-left: 10px; color: {color}; font-weight: bold;")
        else:
            self.f107_value_label.setText("10.7cm Flux: — sfu")
            self.sunspot_area_label.setText("Sunspot Area: —")
            #self.xray_bg_label.setText("X-Ray Background: —")
            self.f107_activity_label.setText("Activity Level: Data unavailable")
    
    def display_cme_events(self, cmes):
        """Display CME events in the CME table."""
        self.cme_table.setRowCount(0)
        
        if cmes is None or len(cmes) == 0:
            self.cme_info_label.setText("🚀 No CME activity detected in the ±3 day range for this date.")
            self.cme_info_label.show()
            return
        
        self.cme_info_label.setText(f"🚀 Found {len(cmes)} CME events (±3 days from selected date)")
        
        for cme in cmes:
            # Format columns
            time_str = cme.start_time.strftime("%Y-%m-%d %H:%M")
            speed_str = f"{cme.speed:.0f}"
            width_str = f"{cme.half_angle:.0f}°" if cme.half_angle else "—"
            earth_str = "🌍 Yes" if cme.is_earth_directed else "No"
            arrival_str = cme.arrival_str
            
            # Color coding
            color_col = {}
            
            # Color Earth-directed column
            if cme.is_earth_directed:
                color_col[4] = "#FF9800"  # Orange for Earth-directed
                if cme.speed >= 1000:
                    color_col[4] = "#F44336"  # Red for fast Earth-directed
            
            # Color speed column based on category
            speed_colors = {
                "Slow": "#4CAF50",
                "Moderate": "#FFC107",
                "Fast": "#FF9800",
                "Extreme": "#F44336",
            }
            color_col[1] = speed_colors.get(cme.speed_category, "#9E9E9E")
            
            self.cme_table.add_event_row([
                time_str,
                speed_str,
                cme.source_location,
                width_str,
                earth_str,
                arrival_str,
            ], color_col)
        
        self.cme_table.resizeColumnsToContents()
        self.cme_table.scrollToTop()
    
    def get_date_from_parent_tab(self):
        """Extract date from the currently open tab in the parent viewer.
        
        Uses the same logic as the viewer's figure title date extraction.
        """
        try:
            # Get parent main window
            parent = self.parent()
            if parent is None:
                QMessageBox.information(self, "Info", "No parent viewer found. Please open an image first.")
                return
            
            # Try to get current tab
            current_tab = None
            if hasattr(parent, 'tab_widget'):
                current_tab = parent.tab_widget.currentWidget()
            
            if current_tab is None:
                QMessageBox.information(self, "Info", "No image is currently open.")
                return
            
            extracted_date = None
            image_time = None
            imagename = getattr(current_tab, 'imagename', None)
            
            # Method 1: Try FITS header from tab attribute first
            header = None
            if hasattr(current_tab, 'header') and current_tab.header:
                header = current_tab.header
            
            # Method 1b: If no header attribute, read FITS/FTS file directly
            if header is None and imagename:
                lower_name = imagename.lower()
                if lower_name.endswith('.fits') or lower_name.endswith('.fts') or lower_name.endswith('.fit'):
                    try:
                        from astropy.io import fits
                        header = fits.getheader(imagename)
                    except Exception as fits_err:
                        print(f"FITS header read failed: {fits_err}")
            
            # Extract date from header
            if header is not None:
                # Check DATE-OBS (standard), DATE_OBS (IRIS), and STARTOBS
                image_time = header.get("DATE-OBS") or header.get("DATE_OBS") or header.get("STARTOBS")
                
                # Special handling for SOHO (DATE-OBS + TIME-OBS)
                if header.get("TELESCOP") == "SOHO" and header.get("TIME-OBS") and image_time:
                    image_time = f"{image_time}T{header['TIME-OBS']}"
                
                if image_time:
                    extracted_date = self._parse_date_string(str(image_time))
            
            # Method 2: CASA image - read csys_record directly from file (like viewer.py)
            if extracted_date is None and imagename:
                # Check if it's a CASA image (directory, not .fits/.fts)
                lower_name = imagename.lower()
                is_casa_image = os.path.isdir(imagename) or (
                    not lower_name.endswith('.fits') and 
                    not lower_name.endswith('.fts') and 
                    not lower_name.endswith('.fit')
                )
                
                if is_casa_image:
                    try:
                        from casatools import image as IA
                        ia_tool = IA()
                        ia_tool.open(imagename)
                        csys_record = ia_tool.coordsys().torecord()
                        ia_tool.close()
                        
                        if "obsdate" in csys_record:
                            obsdate = csys_record["obsdate"]
                            m0 = obsdate.get("m0", {})
                            time_value = m0.get("value", None)
                            time_unit = m0.get("unit", None)
                            refer = obsdate.get("refer", None)
                            
                            if (refer == "UTC" or time_unit == "d") and time_value:
                                from astropy.time import Time
                                t = Time(time_value, format="mjd")
                                extracted_date = t.to_datetime().date()
                    except Exception as casa_err:
                        print(f"CASA date extraction failed: {casa_err}")
            
            # Method 3: Try filename parsing (e.g., 20231002_image.fits)
            if extracted_date is None and imagename:
                filename = imagename
                # Try various date patterns in filename
                patterns = [
                    r'(\d{4})(\d{2})(\d{2})',  # YYYYMMDD
                    r'(\d{4})-(\d{2})-(\d{2})',  # YYYY-MM-DD
                    r'(\d{4})\.(\d{2})\.(\d{2})',  # YYYY.MM.DD
                ]
                for pattern in patterns:
                    match = re.search(pattern, filename)
                    if match:
                        try:
                            y, m, d = int(match.group(1)), int(match.group(2)), int(match.group(3))
                            if 1990 < y < 2100 and 1 <= m <= 12 and 1 <= d <= 31:
                                extracted_date = date(y, m, d)
                                break
                        except (ValueError, IndexError):
                            continue
            
            if extracted_date:
                self.date_edit.setDate(QDate(extracted_date.year, extracted_date.month, extracted_date.day))
                self.summary_label.setText(f"Date set to {extracted_date} from current image.")
            else:
                QMessageBox.information(self, "Info", 
                    "Could not extract date from the current image.\n\n"
                    "Supported formats:\n"
                    "• FITS files with DATE-OBS header\n"
                    "• CASA images with observation date\n"
                    "• Files with date in filename (YYYYMMDD)")
                
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Error extracting date: {str(e)}")

    def plot_goes_xray(self):
        """Fetch and plot GOES X-ray flux for the selected date."""
        if hasattr(self, 'goes_worker') and self.goes_worker and self.goes_worker.isRunning():
            return
            
        qdate = self.date_edit.date()
        selected_date = date(qdate.year(), qdate.month(), qdate.day())
        
        # Save current summary to restore later
        self.previous_summary = self.summary_label.text()
        self.summary_label.setText(f"Fetching GOES data for {selected_date}...")
        self.progress.show()
        self.plot_goes_btn.setEnabled(False)
        
        from PyQt5.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.WaitCursor)
        
        self.goes_worker = GOESPlotWorker(selected_date)
        self.goes_worker.finished.connect(self.on_goes_plot_ready)
        self.goes_worker.error.connect(self.on_goes_plot_error)
        self.goes_worker.start()
        
    def on_goes_plot_ready(self, ts):
        """Handle ready GOES data."""
        try:
            # Check validity
            if not self.isVisible() and not self.parent(): return
            
            from PyQt5.QtWidgets import QApplication
            QApplication.restoreOverrideCursor()
            self.progress.hide()
            self.plot_goes_btn.setEnabled(True)
            
            # Restore previous summary
            if hasattr(self, 'previous_summary'):
                self.summary_label.setText(self.previous_summary)
            else:
                self.summary_label.setText(f"GOES data loaded for {self.date_edit.date().toString('yyyy-MM-dd')}")
            
            ts_list = ts if isinstance(ts, list) else [ts]
            if not ts_list: return
            
            import matplotlib.pyplot as plt
            import numpy as np
            from pandas.plotting import register_matplotlib_converters
            register_matplotlib_converters()
            
            # Create figure
            fig, ax = plt.subplots(figsize=(12, 6))
            
            # Try native SunPy TimeSeries.plot() first, fallback to manual plotting if it fails
            # (e.g., due to xarray multi-dimensional indexing deprecation in newer versions)
            try:
                for t in ts_list:
                    t.plot(axes=ax)
            except (IndexError, TypeError, ValueError) as plot_err:
                # Fallback: manual plotting with proper GOES styling
                plt.close(fig)  # Close the failed figure
                fig, ax = plt.subplots(figsize=(12, 6))
                
                # Color scheme for GOES channels
                colors = {'xrsa': '#1f77b4', 'xrsb': '#d62728'}  # Blue for short, Red for long
                labels = {'xrsa': 'GOES 0.5-4 Å', 'xrsb': 'GOES 1-8 Å'}
                
                for t in ts_list:
                    # Convert to DataFrame to avoid xarray multi-dimensional indexing deprecation
                    df = t.to_dataframe()
                    
                    # Only plot the actual flux columns (xrsa and xrsb), not quality flags
                    for col in ['xrsa', 'xrsb']:
                        if col in df.columns:
                            data = df[col].values
                            # Filter out invalid values (zeros, negatives, NaN)
                            valid_mask = (data > 0) & np.isfinite(data)
                            times = df.index[valid_mask]
                            values = data[valid_mask]
                            ax.plot(times, values, 
                                   color=colors.get(col, 'gray'),
                                   label=labels.get(col, col),
                                   linewidth=1.0)
                
                # Set logarithmic scale for Y-axis (essential for GOES plots)
                ax.set_yscale('log')
                
                # Set Y-axis limits and flare classification levels
                ax.set_ylim(1e-9, 1e-3)
                
                # Add flare classification horizontal lines and labels
                flare_levels = {
                    'A': 1e-8,
                    'B': 1e-7,
                    'C': 1e-6,
                    'M': 1e-5,
                    'X': 1e-4,
                }
                for flare_class, level in flare_levels.items():
                    ax.axhline(y=level, color='gray', linestyle='--', alpha=0.5, linewidth=0.8)
                    ax.text(ax.get_xlim()[1], level, f' {flare_class}', 
                           va='center', ha='left', fontsize=10, color='gray')
                
                # Labels and formatting
                ax.set_xlabel('Time (UTC)')
                ax.set_ylabel('Flux (W/m²)')
                ax.legend(loc='upper right')
                ax.grid(True, alpha=0.3, which='both')
            
            ax.set_title(f"GOES X-ray Flux - {self.date_edit.date().toString('yyyy-MM-dd')}")
            
            plt.tight_layout()
            plt.show(block=False) 
            
        except RuntimeError:
            pass
        except Exception as e:
            QMessageBox.warning(self, "Plot Error", f"Failed to plot GOES data:\n{str(e)}")

    def on_goes_plot_error(self, error_msg):
        """Handle GOES fetch error."""
        from PyQt5.QtWidgets import QApplication
        QApplication.restoreOverrideCursor()
        self.progress.hide()
        self.plot_goes_btn.setEnabled(True)
        # Restore previous summary
        if hasattr(self, 'previous_summary'):
            self.summary_label.setText(self.previous_summary)
        else:
            self.summary_label.setText(f"Error fetching GOES data")
        QMessageBox.warning(self, "GOES Error", f"Failed to fetch GOES data:\n{error_msg}")
    
    def _parse_date_string(self, date_str: str) -> Optional[date]:
        """Parse various date string formats."""
        if not date_str:
            return None
        
        date_str = str(date_str).strip()
        
        try:
            # ISO format with time (2023-10-02T12:30:00)
            if 'T' in date_str:
                # Clean up the date string for parsing
                clean_str = date_str.replace('Z', '').split('+')[0].split('.')[0]
                # Handle potential timezone info
                if '-' in clean_str[11:]:  # Timezone like -05:00 after time
                    clean_str = clean_str[:19]
                try:
                    dt = datetime.fromisoformat(clean_str)
                    return dt.date()
                except ValueError:
                    # Fallback: just extract date part
                    date_part = clean_str.split('T')[0]
                    if len(date_part) >= 10:
                        return datetime.strptime(date_part[:10], '%Y-%m-%d').date()
            
            # YYYY-MM-DD
            if '-' in date_str and len(date_str) >= 10:
                return datetime.strptime(date_str[:10], '%Y-%m-%d').date()
            
            # YYYY/MM/DD
            if '/' in date_str and len(date_str) >= 10:
                return datetime.strptime(date_str[:10], '%Y/%m/%d').date()
            
            # YYYYMMDD (8 digits)
            if date_str.isdigit() and len(date_str) >= 8:
                return datetime.strptime(date_str[:8], '%Y%m%d').date()
            
            # MJD (Modified Julian Date)
            if date_str.replace('.', '').isdigit():
                mjd = float(date_str)
                if 40000 < mjd < 100000:  # Valid MJD range
                    from astropy.time import Time
                    t = Time(mjd, format='mjd')
                    return t.to_datetime().date()
        except (ValueError, TypeError, ImportError):
            pass
        
        return None
   
    def display_context_images(self, images):
        """Display context images."""
        # Clear existing content from the grid layout
        while self.images_grid.count():
            item = self.images_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                pass
        
        # Cancel any pending downloads and reset queue
        self.image_downloads = {} # Active ones
        self.download_queue = []  # Waiting ones
        self.active_downloads = 0
        
        if not images:
            no_data = QLabel("Failed to retrieve context images for this date.")
            no_data.setAlignment(Qt.AlignCenter)
            self.images_grid.addWidget(no_data)
            return

        palette = theme_manager.palette
        header = QLabel("Solar Context Imagery (Helioviewer.org / SolarMonitor.org / NASA SDO / SOHO)")
        header.setStyleSheet(f"color: {palette['text']}; padding: 10px; opacity: 0.4;")
        self.images_grid.addWidget(header)
        
        # Create a card for each image
        for img in images:
            card = QFrame()
            # Theme-aware card styling
            bg = palette['surface'] if not theme_manager.is_dark else "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(80, 80, 80, 0.1), stop:1 rgba(80, 80, 80, 0.2))"
            border = f"1px solid {palette['border']}" if not theme_manager.is_dark else "1px solid rgba(128, 128, 128, 0.3)"
            
            card.setStyleSheet(f"""
                QFrame {{
                    background: {bg};
                    border-radius: 8px;
                    border: {border};
                }}
                QLabel {{ color: {palette['text']}; }}
            """)
            card_layout = QHBoxLayout(card)
            card_layout.setContentsMargins(10, 10, 10, 10)
            
            # Image container - LARGER thumbnails
            img_container = QFrame()
            img_container.setFixedSize(320, 320)  # Increased from 222
            img_container.setStyleSheet("background: #000; border: 1px solid #555; border-radius: 4px;")
            img_container_layout = QVBoxLayout(img_container)
            img_container_layout.setContentsMargins(0,0,0,0)
            
            # Use ClickableLabel
            img_label = ClickableLabel("Queued...")
            img_label.setAlignment(Qt.AlignCenter)
            img_label.setStyleSheet("color: #aaa; border: none; background: transparent;")
            img_label.setToolTip("Click to view High Resolution Image")
            img_label.setCursor(Qt.PointingHandCursor)
            
            # Connect click to viewer
            img_label.clicked.connect(lambda i=img: self.show_high_res_image(i))
            
            img_container_layout.addWidget(img_label)
            
            card_layout.addWidget(img_container)
            
            # Info container
            info_layout = QVBoxLayout()
            title = ClickableLabel(img.title) 
            title.clicked.connect(lambda i=img: self.show_high_res_image(i))
            title.setCursor(Qt.PointingHandCursor)
            title.setStyleSheet("font-weight: bold; color: #2196F3;")
            
            instrument_lbl = QLabel(f"Instrument: {img.instrument}")
            instrument_lbl.setStyleSheet("font-weight: bold; color: #555;")
            
            desc = QLabel(img.description)
            desc.setWordWrap(True)
            desc.setStyleSheet("color: #666;")
            
            # Credits label instead of View Source Page link
            credits_lbl = QLabel(f"Credits: {img.credits}")
            credits_lbl.setStyleSheet("color: #666; font-style: italic;")
            
            info_layout.addWidget(title)
            info_layout.addWidget(instrument_lbl)
            info_layout.addWidget(desc)
            info_layout.addWidget(credits_lbl)
            
            # Add save button for high-res image
            save_btn = QPushButton("💾 Save High-Res")
            save_btn.setStyleSheet("""
                QPushButton {
                    padding: 4px 12px;
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgba(33, 150, 243, 0.9),
                        stop:1 rgba(25, 118, 210, 0.9));
                    border: none;
                    border-radius: 4px;
                    color: white;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgba(33, 150, 243, 1.0),
                        stop:1 rgba(25, 118, 210, 1.0));
                }
            """)
            save_btn.clicked.connect(lambda checked, i=img: self.save_high_res_image(i))
            info_layout.addWidget(save_btn)
            info_layout.addStretch()
            
            card_layout.addLayout(info_layout)
            self.images_grid.addWidget(card)
            
            # Add to download queue instead of starting immediately
            self.download_queue.append((img.thumb_url, img_label, img.page_url))
            
        self.images_grid.addStretch()
        
        # Start processing queue
        self._process_download_queue()
        
    def show_high_res_image(self, img_obj):
        """Open dialog to show full resolution image."""
        # Use None as parent to make it an independent window
        viewer = FullImageViewer(None, img_obj.title, img_obj.page_url)
        viewer.setAttribute(Qt.WA_DeleteOnClose) # Cleanup on close
        
        # Keep reference to prevent GC
        self.image_viewers.append(viewer)
        viewer.finished.connect(lambda result, v=viewer: self._cleanup_viewer(v))
        
        viewer.show() # Non-blocking
        
    def _cleanup_viewer(self, viewer):
        """Safely remove viewer reference."""
        try:
            if viewer in self.image_viewers:
                self.image_viewers.remove(viewer)
        except RuntimeError:
            pass
    
    def save_high_res_image(self, img_obj):
        """Save high resolution image as PNG."""
        from PyQt5.QtWidgets import QFileDialog, QMessageBox, QProgressDialog
        from PyQt5.QtCore import Qt
        import requests
        
        # Ask user for save location (PNG only)
        default_name = f"{img_obj.title.replace(' ', '_')}_{self.date_edit.date().toString('yyyyMMdd')}.png"
        
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save High Resolution Image",
            default_name,
            "PNG Image (*.png)"
        )
        
        if not file_path:
            return  # User cancelled
        
        # Ensure .png extension
        if not file_path.endswith('.png'):
            file_path += '.png'
        
        # Show progress dialog
        progress = QProgressDialog("Downloading high resolution image...", "Cancel", 0, 0, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        progress.show()
        QApplication.processEvents()
        
        try:
            # Download high-res image
            response = requests.get(img_obj.page_url, timeout=60)
            response.raise_for_status()
            
            progress.setLabelText("Saving image...")
            QApplication.processEvents()
            
            # Save as PNG
            with open(file_path, 'wb') as f:
                f.write(response.content)
            
            progress.close()
            QMessageBox.information(self, "Success", f"Image saved to:\n{file_path}")
            
        except Exception as e:
            progress.close()
            QMessageBox.critical(self, "Error", f"Failed to save image:\n{str(e)}")
        
    def _process_download_queue(self):
        """Start next downloads if under limit."""
        MAX_CONCURRENT = 4
        
        while self.active_downloads < MAX_CONCURRENT and self.download_queue:
            url, label, page_url = self.download_queue.pop(0)
            self.active_downloads += 1
            label.setText("Loading...")
            self._start_download(url, label, page_url)
            
    def _start_download(self, url, label, page_url):
        loader = ImageLoader(url, page_url)
        loader.loaded.connect(lambda data, l=label: self._on_image_loaded(data, l))
        loader.error.connect(lambda err, l=label: self._on_image_error(err, l))
        
        # Cleanup and process next on finish
        loader.finished.connect(self._on_download_finished)
        
        # Keep reference
        self.image_downloads[id(loader)] = loader
        loader.start()

    def _on_download_finished(self):
        """Handle download thread finish (cleanup and next)."""
        try:
            # Check validity
            if not self.isVisible() and not self.parent(): return
            
            sender = self.sender()
            if sender:
                self.image_downloads.pop(id(sender), None)
            
            self.active_downloads -= 1
            if self.active_downloads < 0: self.active_downloads = 0
            
            self._process_download_queue()
        except RuntimeError:
            pass

    def _on_image_loaded(self, data, label):
        """Handle image download completion."""
        try:
            # Check if label is still valid (not deleted c++ object)
            if not label: return
            
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                label.setPixmap(pixmap.scaled(QSize(320, 320), Qt.KeepAspectRatio, Qt.SmoothTransformation))
                label.setText("") 
            else:
                label.setText("Format Error")
        except RuntimeError:
            # Widget deleted, ignore
            pass
            
    def _on_image_error(self, error_msg, label):
        """Handle download error."""
        try:
            if not label: return
            
            # shorten error message
            short = "Connection Error" if "101" in error_msg or "Unreachable" in str(error_msg) else "Error"
            label.setText(f"{short}\nRetrying..." if "101" in error_msg else f"{short}")
            if "101" in error_msg:
                 # Maybe retry? For now just show error.
                 pass
        except RuntimeError:
            pass


    def set_date_from_fits(self, fits_date: Optional[date]):
        """Set the date from a FITS file's DATE-OBS."""
        if fits_date:
            self.date_edit.setDate(QDate(fits_date.year, fits_date.month, fits_date.day))


def show_noaa_events_viewer(parent=None, initial_date: Optional[date] = None):
    """
    Show the NOAA Events Viewer dialog.
    
    Args:
        parent: Parent widget
        initial_date: Optional initial date (e.g., from FITS header)
    
    Returns:
        The viewer window instance
    """
    viewer = NOAAEventsViewer(parent, initial_date)
    viewer.show()
    
    # If initial date provided, auto-fetch
    if initial_date:
        viewer.fetch_data()
        
    return viewer


def main():
    import argparse
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="NOAA Solar Events Viewer")
    parser.add_argument("--theme", choices=["light", "dark"], default="dark", 
                        help="Set application theme (light or dark)")
    args = parser.parse_args()
    
    # Setup application
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    # Apply theme
    if args.theme == "light":
        theme_manager.set_theme(theme_manager.LIGHT)
    else:
        theme_manager.set_theme(theme_manager.DARK)
        
    # Apply detailed palette to application (replicates main.py logic)
    palette = theme_manager.palette
    qt_palette = QPalette()
    qt_palette.setColor(QPalette.Window, QColor(palette["window"]))
    qt_palette.setColor(QPalette.WindowText, QColor(palette["text"]))
    qt_palette.setColor(QPalette.Base, QColor(palette["base"]))
    qt_palette.setColor(QPalette.AlternateBase, QColor(palette["surface"]))
    qt_palette.setColor(QPalette.Text, QColor(palette["text"]))
    qt_palette.setColor(QPalette.Button, QColor(palette["button"]))
    qt_palette.setColor(QPalette.ButtonText, QColor(palette["text"]))
    qt_palette.setColor(QPalette.Highlight, QColor(palette["highlight"]))
    qt_palette.setColor(QPalette.HighlightedText, Qt.white)
    qt_palette.setColor(QPalette.Link, QColor(palette["highlight"]))
    qt_palette.setColor(QPalette.Disabled, QPalette.Text, QColor(palette["disabled"]))
    qt_palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(palette["disabled"]))
    
    app.setPalette(qt_palette)
    app.setStyleSheet(theme_manager.stylesheet)
        
    viewer = NOAAEventsViewer()
    viewer.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()

"""
Pipeline Logger GUI
==================

A PyQt5-based graphical user interface for displaying and monitoring logs
from the LOFAR Solar Imaging Pipeline.

Features:
- Real-time log updates
- Filtering by log level
- Search functionality
- Color-coded log entries by severity
- Auto-scrolling with pause option
- Log file viewing and management

This GUI can be used while the pipeline is running to monitor progress,
or after a pipeline run to analyze logs.

Author: Soham Dey
Date: April 2025
"""

import os
import sys
import time
import logging
from queue import Queue, Empty
from datetime import datetime
import threading
try:
    from ..styles import set_hand_cursor
except ImportError:
    from styles import set_hand_cursor

from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QComboBox,
    QPushButton,
    QLineEdit,
    QLabel,
    QCheckBox,
    QSplitter,
    QTextEdit,
    QStatusBar,
    QAction,
    QFileDialog,
    QStyle,
    QStyledItemDelegate,
    QMenu,
    QToolBar,
    QAbstractItemView,
    QMessageBox,
    QTabWidget,
    QGroupBox,
    QRadioButton,
    QGridLayout,
    QShortcut,
    QDialog,
    QFrame,
    QScrollArea,
)
from PyQt5.QtCore import (
    Qt,
    QThread,
    pyqtSignal,
    QTimer,
    QSize,
    QPoint,
    QRect,
    QSortFilterProxyModel,
)
from PyQt5.QtGui import (
    QIcon,
    QColor,
    QTextCursor,
    QFont,
    QPalette,
    QTextCharFormat,
    QBrush,
    QKeySequence,
)


class LogTableWidget(QTableWidget):
    """Custom QTableWidget that prevents horizontal auto-scrolling during navigation."""

    def scrollTo(self, index, hint=QAbstractItemView.EnsureVisible):
        # Capture current horizontal scroll position
        h_val = self.horizontalScrollBar().value()
        # Perform standard scroll (handles vertical positioning smoothly)
        super().scrollTo(index, hint)
        # Force horizontal scrollbar back to its original position
        self.horizontalScrollBar().setValue(h_val)


# Local LogRecord class - compatible with any log format
class LogRecord:
    """Simple log record class for storing parsed log entries."""

    def __init__(self, level, name, message, timestamp=None):
        self.level = level
        self.name = name
        self.message = message
        self.timestamp = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class LogMonitorThread(QThread):
    """Thread for monitoring log queue and emitting signals when new logs arrive."""

    log_received = pyqtSignal(object)  # Signal emitted when new log is received

    def __init__(self, log_queue, parent=None, log_file=None):
        super().__init__(parent)
        self.log_queue = log_queue
        self.log_file = log_file
        self.running = True
        self.last_file_position = 0
        self.last_file_check = 0
        self.file_check_interval = 0.5  # Check file every 0.5 seconds

    def run(self):
        """Main thread loop to check for new logs."""
        while self.running:
            try:
                # Get log record from queue with timeout
                log_record = self.log_queue.get(block=True, timeout=0.1)
                self.log_received.emit(log_record)
            except Empty:
                # No logs in queue, check if we should monitor file directly
                current_time = time.time()
                if (
                    self.log_file
                    and os.path.exists(self.log_file)
                    and (current_time - self.last_file_check)
                    >= self.file_check_interval
                ):
                    self.last_file_check = current_time
                    self._check_log_file()

                # Small sleep to reduce CPU usage
                time.sleep(0.01)
            except Exception as e:
                print(f"Error in log monitor thread: {e}")

    def _check_log_file(self):
        """Check if the log file has changed and process new entries."""
        try:
            # Get the file size
            file_size = os.path.getsize(self.log_file)

            # If file size has increased, read the new content
            if file_size > self.last_file_position:
                with open(self.log_file, "r") as f:
                    # Move to the last position we read
                    f.seek(self.last_file_position)

                    # Read new lines
                    new_lines = f.readlines()

                    # Update position
                    self.last_file_position = file_size

                    # Process each new line
                    for line in new_lines:
                        try:
                            # Parse log line - assumes format: TIMESTAMP - LEVEL - NAME - MESSAGE
                            parts = line.strip().split(" - ", 3)
                            if len(parts) >= 4:
                                timestamp, level, name, message = parts
                                log_record = LogRecord(level, name, message, timestamp)
                                self.log_received.emit(log_record)
                        except Exception as e:
                            print(f"Error parsing log line: {e}")
        except Exception as e:
            print(f"Error checking log file: {e}")

    def set_log_file(self, log_file):
        """Set the log file to monitor."""
        self.log_file = log_file
        if os.path.exists(log_file):
            self.last_file_position = os.path.getsize(log_file)
        else:
            self.last_file_position = 0

    def stop(self):
        """Stop the thread."""
        self.running = False
        self.wait()


class LogLevelDelegate(QStyledItemDelegate):
    """Custom delegate for rendering log level cells with background colors."""

    # Theme-aware level colors
    LEVEL_COLORS_DARK = {
        "DEBUG": (
            QColor(80, 80, 100),
            QColor(200, 200, 200),
        ),  # Muted blue bg, light text
        "INFO": (
            QColor(50, 80, 50),
            QColor(180, 255, 180),
        ),  # Dark green bg, light green text
        "WARNING": (
            QColor(120, 100, 40),
            QColor(255, 240, 100),
        ),  # Dark yellow bg, bright yellow text
        "ERROR": (
            QColor(120, 50, 50),
            QColor(255, 150, 150),
        ),  # Dark red bg, light red text
        "CRITICAL": (
            QColor(150, 40, 40),
            QColor(255, 100, 100),
        ),  # Darker red bg, bright red text
    }

    LEVEL_COLORS_LIGHT = {
        "DEBUG": (
            QColor(200, 200, 220),
            QColor(60, 60, 80),
        ),  # Light gray bg, dark text
        "INFO": (
            QColor(200, 240, 200),
            QColor(20, 80, 20),
        ),  # Light green bg, dark green text
        "WARNING": (
            QColor(255, 240, 180),
            QColor(120, 90, 0),
        ),  # Light yellow bg, dark yellow text
        "ERROR": (
            QColor(255, 200, 200),
            QColor(150, 30, 30),
        ),  # Light red bg, dark red text
        "CRITICAL": (QColor(255, 150, 150), QColor(120, 0, 0)),  # Red bg, dark red text
    }

    def __init__(self, theme="dark", parent=None):
        super().__init__(parent)
        self.theme = theme
        self.level_colors = (
            self.LEVEL_COLORS_DARK if theme == "dark" else self.LEVEL_COLORS_LIGHT
        )

    def set_theme(self, theme):
        """Update the theme and colors."""
        self.theme = theme
        self.level_colors = (
            self.LEVEL_COLORS_DARK if theme == "dark" else self.LEVEL_COLORS_LIGHT
        )

    def paint(self, painter, option, index):
        """Custom painting for log level cells."""
        level = index.data()
        if level in self.level_colors:
            bg_color, text_color = self.level_colors[level]

            # Fill background
            if option.state & QStyle.State_Selected:
                # When selected, blend the level color with the selection color or just use selection
                # For high contrast, we use the selection color
                painter.fillRect(option.rect, option.palette.highlight())
                painter.setPen(option.palette.highlightedText().color())
                
                # Draw a small indicator of the level color on the left
                indicator_rect = QRect(option.rect.left(), option.rect.top(), 4, option.rect.height())
                painter.fillRect(indicator_rect, bg_color)
            else:
                painter.fillRect(option.rect, bg_color)
                painter.setPen(text_color)

            # Set up rect for text with padding
            text_rect = QRect(option.rect)
            text_rect.setLeft(text_rect.left() + 4)

            # Draw text
            painter.drawText(text_rect, Qt.AlignVCenter, level)
        else:
            # Fall back to default for unknown levels
            super().paint(painter, option, index)


class MessageDelegate(QStyledItemDelegate):
    """Custom delegate for syntax highlighting using HTML and QTextDocument."""

    def __init__(self, theme="dark", parent=None):
        super().__init__(parent)
        self.theme = theme
        import re

        # Professional syntax highlighting patterns (priority-ordered - first match wins)
        # More specific patterns come first to avoid false positives
        self.patterns = [
            # 1. Quoted strings FIRST - prevent highlighting inside quotes
            (re.compile(r'("[^"]*"|\'[^\']*\')'), "string"),
            # 2. Log levels / status keywords (very specific)
            (
                re.compile(
                    r"\b(SUCCESS|FAILED|FAILURE|ERROR|WARNING|CRITICAL|INFO|DEBUG|COMPLETE|COMPLETED|DONE|OK|PASS|PASSED)\b",
                    re.IGNORECASE,
                ),
                "keyword",
            ),
            # 3. Timestamps - multiple formats
            # ISO format: 2025-06-13T10:53:47.579Z
            (
                re.compile(
                    r"\b(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\b"
                ),
                "timestamp",
            ),
            # CASACORE format: 25-Aug-2014/12:42:00.268
            (
                re.compile(
                    r"\b(\d{1,2}-[A-Za-z]{3}-\d{4}/\d{2}:\d{2}:\d{2}(?:\.\d+)?)\b"
                ),
                "timestamp",
            ),
            # Time only: 10:53:47.579
            (re.compile(r"\b(\d{2}:\d{2}:\d{2}(?:\.\d+)?)\b"), "timestamp"),
            # Date only: 2025-06-13 or 25-Aug-2014
            (
                re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}-[A-Za-z]{3}-\d{4})\b"),
                "timestamp",
            ),
            # 4. File paths (require depth or known extensions)
            (
                re.compile(
                    r"(/[\w.-]+(?:/[\w.-]+)+|/[\w.-]+\.(?:ms|MS|fits|FITS|log|txt|py|sh|conf|json|yaml|yml|csv|h5|hdf5))"
                ),
                "path",
            ),
            # 5. LOFAR-specific: Station names with antenna type
            (re.compile(r"\b([CR]S\d{3}(?:HBA\d?|LBA)?)\b", re.IGNORECASE), "station"),
            # 6. LOFAR-specific: Subbands (SB000-SB999)
            (re.compile(r"\b(SB\d{1,3})\b", re.IGNORECASE), "subband"),
            # 7. IP addresses and ports
            (
                re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d+)?)\b"),
                "number",
            ),
            # 8. Numbers with units - SHORT forms (space optional)
            # Note: Order matters - 'ms' before 's' so milliseconds matches first
            (
                re.compile(
                    r"\b(\d+(?:\.\d+)?\s*(?:MHz|kHz|Hz|GHz|THz|ms|μs|us|ns|s|h|min|m|km|Jy|mJy|μJy|uJy|dB|dBm|MB|GB|TB|KB|px|deg|rad|arcsec|arcmin|λ|%|Mλ|kλ))\b",
                    re.IGNORECASE,
                ),
                "number",
            ),
            # 9. Numbers with units - LONG forms (time)
            (
                re.compile(
                    r"\b(\d+(?:\.\d+)?\s*(?:seconds?|minutes?|hours?|days?|milliseconds?|microseconds?|nanoseconds?))\b",
                    re.IGNORECASE,
                ),
                "number",
            ),
            # 10. Numbers with units - LONG forms (frequency/data)
            (
                re.compile(
                    r"\b(\d+(?:\.\d+)?\s*(?:hertz|megahertz|gigahertz|kilohertz|bytes?|kilobytes?|megabytes?|gigabytes?|terabytes?))\b",
                    re.IGNORECASE,
                ),
                "number",
            ),
            # 11. Numbers with units - LONG forms (spatial)
            (
                re.compile(
                    r"\b(\d+(?:\.\d+)?\s*(?:meters?|kilometres?|kilometers?|pixels?|degrees?|radians?|wavelengths?))\b",
                    re.IGNORECASE,
                ),
                "number",
            ),
            # 12. Scientific notation (1.23e-4)
            (re.compile(r"\b(\d+\.\d+[eE][+-]?\d+)\b"), "number"),
            # 13. Percentages and ratios
            (re.compile(r"\b(\d+(?:\.\d+)?%|\d+/\d+)\b"), "number"),
            # 14. UUID/hash patterns (common in logs)
            (
                re.compile(
                    r"\b([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})\b",
                    re.IGNORECASE,
                ),
                "hash",
            ),
            (re.compile(r"\b([a-f0-9]{32,64})\b", re.IGNORECASE), "hash"),
            # 15. Function/method names (word followed by parentheses)
            (re.compile(r"\b([a-zA-Z_]\w*)\("), "function"),
        ]
        self._html_cache = (
            {}
        )  # Cache for generated HTML - must init before _update_colors
        self._update_colors()

    def _update_colors(self):
        """Set colors based on theme (as hex strings for HTML)."""
        if self.theme == "dark":
            self.colors = {
                "path": "#64b4ff",  # Light blue - file paths
                "number": "#ffb464",  # Bright orange - numbers with units
                "station": "#dc96ff",  # Magenta/purple - LOFAR stations
                "string": "#dcdc64",  # Yellow - quoted strings
                "subband": "#96dc96",  # Light green - subbands
                "keyword": "#ff6b6b",  # Light red - log levels/status
                "timestamp": "#78c878",  # Green - timestamps
                "hash": "#a0a0a0",  # Gray - UUIDs/hashes
                "function": "#64dcdc",  # Cyan - function names
                "default": "#c8c8c8",  # Light gray - regular text
            }
        else:
            self.colors = {
                "path": "#0000b4",  # Blue
                "number": "#b46400",  # Orange
                "station": "#8c008c",  # Purple
                "string": "#786400",  # Dark yellow
                "subband": "#007800",  # Green
                "keyword": "#c80000",  # Red
                "timestamp": "#006400",  # Dark green
                "hash": "#606060",  # Gray
                "function": "#006464",  # Dark cyan
                "default": "#1e1e1e",  # Dark gray
            }
        self._html_cache.clear()  # Clear cache on theme change

    def set_theme(self, theme):
        """Update the theme and colors."""
        self.theme = theme
        self._update_colors()

    def _text_to_html(self, text):
        """Convert text to HTML with syntax highlighting. Uses cache for performance."""
        if text in self._html_cache:
            return self._html_cache[text]

        import html

        # Find all matches
        matches = []
        for pattern, ptype in self.patterns:
            for match in pattern.finditer(text):
                matches.append((match.start(), match.end(), ptype))

        # Sort by position and remove overlaps
        matches.sort(key=lambda x: x[0])
        non_overlapping = []
        last_end = 0
        for start, end, ptype in matches:
            if start >= last_end:
                non_overlapping.append((start, end, ptype))
                last_end = end

        # Build HTML
        html_parts = []
        pos = 0
        for start, end, ptype in non_overlapping:
            # Add unhighlighted text before match (escaped)
            if start > pos:
                html_parts.append(html.escape(text[pos:start]))
            # Add highlighted match
            color = self.colors[ptype]
            html_parts.append(
                f'<span style="color:{color}">{html.escape(text[start:end])}</span>'
            )
            pos = end

        # Add remaining text
        if pos < len(text):
            html_parts.append(html.escape(text[pos:]))

        result = "".join(html_parts) if html_parts else html.escape(text)

        # Cache the result (limit cache size)
        if len(self._html_cache) > 5000:
            self._html_cache.clear()
        self._html_cache[text] = result

        return result

    def paint(self, painter, option, index):
        """Paint cell using QTextDocument for proper HTML rendering."""
        from PyQt5.QtWidgets import QStyle
        from PyQt5.QtGui import QTextDocument, QAbstractTextDocumentLayout

        text = index.data()
        if not text:
            super().paint(painter, option, index)
            return

        # Save painter state
        painter.save()

        # Draw background
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        else:
            painter.fillRect(option.rect, option.palette.base())

        # Create QTextDocument with HTML
        doc = QTextDocument()
        doc.setDefaultFont(option.font)

        # Set text color based on selection state
        if option.state & QStyle.State_Selected:
            # When selected, use plain text with explicit selection color for readability
            import html
            color = option.palette.highlightedText().color().name()
            doc.setHtml(f'<div style="color: {color}">{html.escape(text)}</div>')
        else:
            # Normal state: use syntax-highlighted HTML
            html_content = self._text_to_html(text)
            doc.setHtml(
                f'<span style="color:{self.colors["default"]}">{html_content}</span>'
            )

        # Set up clipping
        painter.setClipRect(option.rect)

        # Translate to cell position with padding
        painter.translate(
            option.rect.left() + 4,
            option.rect.top() + (option.rect.height() - doc.size().height()) / 2,
        )

        # Draw the document
        doc.drawContents(painter)

        # Restore painter state
        painter.restore()

    def sizeHint(self, option, index):
        """Return size hint based on text width."""
        from PyQt5.QtGui import QTextDocument
        from PyQt5.QtCore import QSize

        text = index.data()
        if not text:
            return super().sizeHint(option, index)

        doc = QTextDocument()
        doc.setDefaultFont(option.font)
        doc.setPlainText(text)

        return QSize(int(doc.idealWidth()) + 10, int(doc.size().height()))


class LogTableModel:
    """Model for storing and managing log entries."""

    COLUMNS = ["Timestamp", "Level", "Source", "Message"]

    def __init__(self, table_widget, theme="dark"):
        self.table = table_widget
        self.theme = theme
        self.logs = []
        self.filtered_logs = []
        self.filter_level = "DEBUG"  # Show all by default
        self.filter_text = ""
        self.use_regex = False  # Regex search mode
        self._regex_pattern = None  # Compiled regex pattern

        # Set up table
        self.setup_table()

    def setup_table(self):
        """Configure the table widget."""
        self.table.setColumnCount(len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)

        # Set stretch for message column
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # Timestamp
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # Level
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # Source
        header.setSectionResizeMode(
            3, QHeaderView.ResizeToContents
        )  # Message - allow scroll

        # Set custom delegate for level column (theme-aware)
        self.level_delegate = LogLevelDelegate(theme=self.theme)
        self.table.setItemDelegateForColumn(1, self.level_delegate)

        # Set custom delegate for message column (syntax highlighting)
        self.message_delegate = MessageDelegate(theme=self.theme)
        self.table.setItemDelegateForColumn(3, self.message_delegate)

        # Enable horizontal scrolling for long messages
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)

        # Prevent auto-scrolling horizontally when selecting rows
        # self.table.setAutoScroll(False)

        # Enable auto-scrolling (LogTableWidget will handle horizontal constraint)
        self.table.setAutoScroll(True)

        # Selection behavior
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)

    def add_log(self, log_record):
        """Add a new log record to the model."""
        # Create a dict for the log to store all data
        log_entry = {
            "timestamp": log_record.timestamp,
            "level": log_record.level,
            "source": log_record.name,
            "message": log_record.message,
        }

        self.logs.append(log_entry)

        # Check if this log should be displayed based on current filters
        if self._matches_filter(log_entry):
            self.filtered_logs.append(log_entry)
            self._add_to_table(log_entry)

    def _add_to_table(self, log_entry):
        """Add a log entry to the table widget."""
        row = self.table.rowCount()
        self.table.insertRow(row)

        # Add cells
        self.table.setItem(row, 0, QTableWidgetItem(log_entry["timestamp"]))
        self.table.setItem(row, 1, QTableWidgetItem(log_entry["level"]))
        self.table.setItem(row, 2, QTableWidgetItem(log_entry["source"]))
        self.table.setItem(row, 3, QTableWidgetItem(log_entry["message"]))

    def clear(self):
        """Clear all logs from the model and table."""
        self.logs = []
        self.filtered_logs = []
        self.table.setRowCount(0)

    def set_filter_level(self, level):
        """Set the minimum log level filter."""
        self.filter_level = level
        self._apply_filters()

    def set_filter_text(self, text, use_regex=False):
        """Set the text filter."""
        self.filter_text = text.lower()
        self.use_regex = use_regex
        if use_regex and text:
            try:
                import re

                self._regex_pattern = re.compile(text, re.IGNORECASE)
            except re.error:
                self._regex_pattern = None
        else:
            self._regex_pattern = None
        self._apply_filters()

    def _matches_filter(self, log_entry):
        """Check if a log entry matches the current filters."""
        # Check level filter
        level_idx = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"].index(
            log_entry["level"]
        )
        min_level_idx = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"].index(
            self.filter_level
        )
        if level_idx < min_level_idx:
            return False

        # Check text filter
        if self.filter_text:
            if self.use_regex and self._regex_pattern:
                # Regex search
                if not (
                    self._regex_pattern.search(log_entry["message"])
                    or self._regex_pattern.search(log_entry["source"])
                ):
                    return False
            else:
                # Plain text search
                if not (
                    self.filter_text in log_entry["message"].lower()
                    or self.filter_text in log_entry["source"].lower()
                ):
                    return False

        return True

    def _apply_filters(self):
        """Apply current filters to all logs and update the table."""
        # Save current selection if any
        selected_rows = [
            index.row() for index in self.table.selectionModel().selectedRows()
        ]
        selected_log = self.filtered_logs[selected_rows[0]] if selected_rows else None

        # Clear and rebuild filtered logs
        self.filtered_logs = [log for log in self.logs if self._matches_filter(log)]

        # Rebuild table
        self._refresh_table()

        # Restore selection if possible
        if selected_log and selected_log in self.filtered_logs:
            new_row = self.filtered_logs.index(selected_log)
            self.table.selectRow(new_row)

            # Ensure visible
            self.table.scrollToItem(self.table.item(new_row, 0))

    def _refresh_table(self):
        """Rebuild the table from current filtered_logs."""
        self.table.setRowCount(0)
        for log_entry in self.filtered_logs:
            self._add_to_table(log_entry)

    def export_logs(self, filename):
        """Export logs to a CSV file."""
        with open(filename, "w") as f:
            # Write header
            f.write(",".join([f'"{col}"' for col in self.COLUMNS]) + "\n")

            # Write data
            for log in self.logs:
                message = log["message"].replace('"', '""')  # Escape quotes
                row = [
                    f'"{log["timestamp"]}"',
                    f'"{log["level"]}"',
                    f'"{log["source"]}"',
                    f'"{message}"',
                ]
                f.write(",".join(row) + "\n")


class PipelineLoggerGUI(QMainWindow):
    """Main window for the Pipeline Logger GUI application."""

    def __init__(self, theme="dark"):
        super().__init__()
        self.theme = theme
        self.help_dialog = None

        # Initialize empty queue for logs
        self.log_queue = Queue()

        # Initialize UI
        self.setWindowTitle("LOFAR Pipeline Logger")
        self.setMinimumSize(800, 600)

        # Create UI components
        self._create_ui()

        # Set up keyboard shortcuts
        self._setup_shortcuts()
        set_hand_cursor(self)

        # Set up log monitor with no initial log file
        self.log_monitor = LogMonitorThread(self.log_queue)
        self.log_monitor.log_received.connect(self.on_log_received)

        # Set up auto-refresh timer
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self._check_file_size)

        # Start monitoring
        self.log_monitor.start()

        # Set initial status
        self.status_bar.showMessage("Ready - No log file selected", 5000)

    def _create_ui(self):
        """Create the user interface components."""
        # Create central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # Create toolbar
        self._create_toolbar()

        # Create log filter controls
        filter_layout = self._create_filter_controls()
        main_layout.addLayout(filter_layout)

        # Create log table
        self.log_table = LogTableWidget()
        self.log_model = LogTableModel(self.log_table, theme=self.theme)
        main_layout.addWidget(self.log_table)

        # Context menu for table
        self.log_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.log_table.customContextMenuRequested.connect(self._show_context_menu)
        
        # Create status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

        # Auto-scroll checkbox on status bar
        self.auto_scroll_check = QCheckBox("Auto-scroll")
        self.auto_scroll_check.setChecked(True)
        self.status_bar.addPermanentWidget(self.auto_scroll_check)

        # Auto-refresh checkbox on status bar
        self.auto_refresh_check = QCheckBox("Auto-refresh")
        self.auto_refresh_check.setChecked(False)
        self.auto_refresh_check.setToolTip(
            "Automatically check for file changes every 2 seconds"
        )
        self.auto_refresh_check.stateChanged.connect(self._toggle_auto_refresh)
        self.status_bar.addPermanentWidget(self.auto_refresh_check)

        # Log count on status bar
        self.log_count_label = QLabel("0 logs")
        self.status_bar.addPermanentWidget(self.log_count_label)

    def _create_toolbar(self):
        """Create the main toolbar."""
        self.toolbar = QToolBar("Main Toolbar")
        self.toolbar.setIconSize(QSize(24, 24))
        self.addToolBar(self.toolbar)

        # File actions
        self.action_open = QAction("Open Log File", self)
        self.action_open.setIcon(self.style().standardIcon(QStyle.SP_DialogOpenButton))
        self.action_open.triggered.connect(self._open_log_file)
        self.toolbar.addAction(self.action_open)

        self.action_export = QAction("Export", self)
        self.action_export.setIcon(
            self.style().standardIcon(QStyle.SP_DialogSaveButton)
        )
        self.action_export.triggered.connect(self._export_logs)
        self.toolbar.addAction(self.action_export)

        self.action_refresh = QAction("Refresh", self)
        self.action_refresh.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.action_refresh.triggered.connect(self._refresh_monitor)
        self.toolbar.addAction(self.action_refresh)

        # Scroll navigation
        self.toolbar.addSeparator()

        self.action_scroll_top = QAction("⏫", self)
        self.action_scroll_top.triggered.connect(self._scroll_to_top)
        self.toolbar.addAction(self.action_scroll_top)
        # Add tooltip
        self.action_scroll_top.setToolTip("Scroll to top of log")

        self.action_scroll_bottom = QAction("⏬", self)
        self.action_scroll_bottom.triggered.connect(self._scroll_to_bottom)
        self.toolbar.addAction(self.action_scroll_bottom)
        # Add tooltip
        self.action_scroll_bottom.setToolTip("Scroll to bottom of log")

        # Error navigation
        self.toolbar.addSeparator()

        self.action_prev_error = QAction("◀ Error", self)
        self.action_prev_error.triggered.connect(self._jump_to_prev_error)
        self.toolbar.addAction(self.action_prev_error)
        # Add tooltip
        self.action_prev_error.setToolTip("Jump to previous error")

        self.action_next_error = QAction("Error ▶", self)
        self.action_next_error.triggered.connect(self._jump_to_next_error)
        self.toolbar.addAction(self.action_next_error)
        # Add tooltip
        self.action_next_error.setToolTip("Jump to next error")

        # Help button
        self.toolbar.addSeparator()
        self.action_help = QAction("?", self)
        self.action_help.triggered.connect(self._show_help_dialog)
        self.toolbar.addAction(self.action_help)

    def _setup_shortcuts(self):
        """Set up keyboard shortcuts."""
        # File operations
        QShortcut(QKeySequence("Ctrl+O"), self, self._open_log_file)
        QShortcut(QKeySequence("Ctrl+E"), self, self._export_logs)
        QShortcut(QKeySequence("Ctrl+Q"), self, self.close)

        # Search
        QShortcut(QKeySequence("Ctrl+F"), self, self._focus_search)
        QShortcut(QKeySequence("Escape"), self, self._clear_filters)
        QShortcut(QKeySequence("F5"), self, self._refresh_monitor)

        # Scroll navigation
        QShortcut(QKeySequence("Home"), self, self._scroll_to_top)
        QShortcut(QKeySequence("End"), self, self._scroll_to_bottom)

        # Error navigation
        QShortcut(QKeySequence("Ctrl+."), self, self._jump_to_next_error)
        QShortcut(QKeySequence("Ctrl+,"), self, self._jump_to_prev_error)

        # Tail mode toggle
        QShortcut(QKeySequence("Ctrl+T"), self, self._toggle_tail_mode)

        # Help
        QShortcut(QKeySequence("F1"), self, self._show_help_dialog)

    def _focus_search(self):
        """Focus the search input box."""
        self.search_input.setFocus()
        self.search_input.selectAll()

    def _scroll_to_top(self):
        """Scroll to the first row."""
        if self.log_table.rowCount() > 0:
            self.log_table.scrollToTop()
            self.log_table.selectRow(0)

    def _scroll_to_bottom(self):
        """Scroll to the last row."""
        if self.log_table.rowCount() > 0:
            self.log_table.scrollToBottom()
            self.log_table.selectRow(self.log_table.rowCount() - 1)

    def _jump_to_next_error(self):
        """Jump to the next ERROR or CRITICAL log entry."""
        current_row = self.log_table.currentRow()
        for i in range(current_row + 1, len(self.log_model.filtered_logs)):
            if self.log_model.filtered_logs[i]["level"] in ("ERROR", "CRITICAL"):
                self.log_table.selectRow(i)
                self.log_table.scrollTo(self.log_table.model().index(i, 0))
                self._update_error_status(i)
                return
        # Wrap around to start
        for i in range(0, current_row):
            if self.log_model.filtered_logs[i]["level"] in ("ERROR", "CRITICAL"):
                self.log_table.selectRow(i)
                self.log_table.scrollTo(self.log_table.model().index(i, 0))
                self._update_error_status(i)
                return
        self.status_bar.showMessage("No errors found", 2000)

    def _jump_to_prev_error(self):
        """Jump to the previous ERROR or CRITICAL log entry."""
        current_row = self.log_table.currentRow()
        for i in range(current_row - 1, -1, -1):
            if self.log_model.filtered_logs[i]["level"] in ("ERROR", "CRITICAL"):
                self.log_table.selectRow(i)
                self.log_table.scrollTo(self.log_table.model().index(i, 0))
                self._update_error_status(i)
                return
        # Wrap around to end
        for i in range(len(self.log_model.filtered_logs) - 1, current_row, -1):
            if self.log_model.filtered_logs[i]["level"] in ("ERROR", "CRITICAL"):
                self.log_table.selectRow(i)
                self.log_table.scrollTo(self.log_table.model().index(i, 0))
                self._update_error_status(i)
                return
        self.status_bar.showMessage("No errors found", 2000)

    def _update_error_status(self, current_idx):
        """Update status bar with error position."""
        error_indices = [
            i
            for i, log in enumerate(self.log_model.filtered_logs)
            if log["level"] in ("ERROR", "CRITICAL")
        ]
        if error_indices:
            pos = error_indices.index(current_idx) + 1
            total = len(error_indices)
            self.status_bar.showMessage(f"Error {pos} of {total}", 3000)

    def _toggle_tail_mode(self):
        """Toggle tail mode on/off."""
        self.tail_check.setChecked(not self.tail_check.isChecked())

    def _on_tail_mode_changed(self, state):
        """Handle tail mode checkbox change."""
        self.tail_lines_spin.setEnabled(state == Qt.Checked)
        if state == Qt.Checked:
            self._apply_tail_filter()
        else:
            # Restore full view
            self.log_model._apply_filters()

    def _apply_tail_filter(self):
        """Apply tail mode filtering to show only last N lines."""
        if self.tail_check.isChecked():
            n = self.tail_lines_spin.value()
            # Get the last N filtered logs
            self.log_model.filtered_logs = self.log_model.filtered_logs[-n:]
            self.log_model._refresh_table()
            self.log_table.scrollToBottom()
            self._update_status()

    def _show_help_dialog(self):
        """Show help dialog."""
        is_dark = self.theme == "dark"
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Help - Pipeline Logger")
        dialog.resize(550, 650)
        
        main_layout = QVBoxLayout(dialog)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # Check if already open
        if self.help_dialog is not None:
            self.help_dialog.raise_()
            self.help_dialog.activateWindow()
            return

        # Header section with gradient
        header = QFrame()
        if is_dark:
            header.setStyleSheet("""
                QFrame {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                        stop:0 #312e81, stop:1 #4338ca);
                    border-top-left-radius: 2px;
                    border-top-right-radius: 2px;
                }
            """)
        else:
            header.setStyleSheet("""
                QFrame {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                        stop:0 #4f46e5, stop:1 #6366f1);
                    border-top-left-radius: 2px;
                    border-top-right-radius: 2px;
                }
            """)
        
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(25, 20, 25, 20)
        header_layout.setSpacing(8)
        
        title = QLabel("LOFAR Pipeline Log Viewer")
        title.setStyleSheet("font-size: 18pt; font-weight: bold; color: white;")
        header_layout.addWidget(title)
        
        subtitle = QLabel("Real-time monitoring and analysis of pipeline logs")
        subtitle.setStyleSheet("font-size: 11pt; color: rgba(255, 255, 255, 0.85);")
        header_layout.addWidget(subtitle)
        
        main_layout.addWidget(header)
        
        # Scrollable content area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(25, 20, 25, 20)
        content_layout.setSpacing(20)
        
        def add_section(title_text):
            label = QLabel(title_text)
            label.setStyleSheet(f"""
                font-size: 12pt; 
                font-weight: bold; 
                letter-spacing: 0.5px;
                color: {'#818cf8' if is_dark else '#4f46e5'};
                margin-top: 5px;
            """)
            content_layout.addWidget(label)
            
            line = QFrame()
            line.setFixedHeight(1)
            line.setStyleSheet(f"background-color: {'#2d2d4a' if is_dark else '#e0e0e0'};")
            content_layout.addWidget(line)

        # Features section
        add_section("FEATURES")
        features = [
            ("📊 Real-time Log Monitoring", "Automatic updates as pipeline runs"),
            ("🔍 Advanced Filtering", "Filter by level and source"),
            ("⌨️ Search & Regex", "Fast search with regular expression support"),
            ("📜 Tail Mode", "Keep view focused on the latest N log entries"),
            ("🚨 Error Navigation", "Quickly jump between errors and critical logs"),
            ("📤 Data Export", "Save log history to CSV for external analysis")
        ]
        
        feat_grid = QGridLayout()
        feat_grid.setSpacing(12)
        for i, (f_title, f_desc) in enumerate(features):
            f_item = QLabel(f"<b>{f_title}</b>: {f_desc}")
            f_item.setWordWrap(True)
            f_item.setStyleSheet("font-size: 10.5pt;")
            feat_grid.addWidget(f_item, i // 2, i % 2)
        content_layout.addLayout(feat_grid)
        
        # Keyboard Shortcuts
        add_section("KEYBOARD SHORTCUTS")
        
        shortcuts = [
            ("Ctrl+O", "Open log file"), ("F5", "Refresh log"),
            ("Ctrl+F", "Focus search"), ("Escape", "Clear filters"),
            ("Ctrl+T", "Toggle tail mode"), ("Ctrl+E", "Export to CSV"),
            ("Home", "Scroll to top"), ("End", "Scroll to bottom"),
            ("Ctrl+.", "Next error"), ("Ctrl+,", "Previous error")
        ]
        
        shot_grid = QGridLayout()
        shot_grid.setColumnStretch(1, 1)
        shot_grid.setColumnStretch(3, 1)
        shot_grid.setSpacing(15)
        
        key_bg = "#2d2d4a" if is_dark else "#f3f4f6"
        key_text = "#e2e8f0" if is_dark else "#4b5563"
        key_border = "#4338ca" if is_dark else "#d1d5db"
        
        for i, (key, desc) in enumerate(shortcuts):
            row, col = i % 5, (i // 5) * 2
            
            key_pill = QLabel(key)
            key_pill.setAlignment(Qt.AlignCenter)
            key_pill.setStyleSheet(f"""
                background-color: {key_bg};
                color: {key_text};
                border: 1px solid {key_border};
                border-radius: 4px;
                padding: 3px 8px;
                font-family: 'Courier New', monospace;
                font-weight: bold;
                font-size: 10pt;
            """)
            
            desc_label = QLabel(desc)
            desc_label.setStyleSheet("font-size: 10.5pt;")
            
            shot_grid.addWidget(key_pill, row, col)
            shot_grid.addWidget(desc_label, row, col + 1)
        
        content_layout.addLayout(shot_grid)
        
        content_layout.addStretch()
        
        scroll.setWidget(content_widget)
        main_layout.addWidget(scroll)
        
        # Bottom bar
        bottom_bar = QFrame()
        bottom_bar.setFixedHeight(50)
        bottom_bar.setStyleSheet(f"background-color: {'#16162a' if is_dark else '#f9fafb'}; border-top: 1px solid {'#2d2d4a' if is_dark else '#e5e7eb'};")
        bottom_layout = QHBoxLayout(bottom_bar)
        
        footer = QLabel("SolarViewer Log Utilities • v1.0.0")
        footer.setStyleSheet(f"color: {'#888' if is_dark else '#666'}; font-size: 9pt;")
        bottom_layout.addWidget(footer)
        bottom_layout.addStretch()
        
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(100)
        close_btn.clicked.connect(dialog.close)
        bottom_layout.addWidget(close_btn)
        
        main_layout.addWidget(bottom_bar)
        
        # Make non-modal
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        def on_close():
            self.help_dialog = None
        dialog.destroyed.connect(on_close)
        
        self.help_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _create_filter_controls(self):
        """Create controls for filtering logs."""
        filter_layout = QHBoxLayout()

        # Log level filter
        level_label = QLabel("Min Level:")
        self.level_combo = QComboBox()
        self.level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
        self.level_combo.setCurrentText("INFO")  # Default to INFO
        self.level_combo.currentTextChanged.connect(self._on_filter_changed)

        filter_layout.addWidget(level_label)
        filter_layout.addWidget(self.level_combo)

        # Search filter
        search_label = QLabel("Search:")
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search in logs...")
        self.search_input.textChanged.connect(self._on_search_changed)

        filter_layout.addWidget(search_label)
        filter_layout.addWidget(self.search_input)

        # Regex toggle
        self.regex_check = QCheckBox("Regex")
        self.regex_check.setChecked(False)
        self.regex_check.stateChanged.connect(self._on_regex_changed)
        filter_layout.addWidget(self.regex_check)

        # Tail mode
        filter_layout.addWidget(QLabel("|"))
        self.tail_check = QCheckBox("Tail")
        self.tail_check.setChecked(False)
        self.tail_check.stateChanged.connect(self._on_tail_mode_changed)
        filter_layout.addWidget(self.tail_check)

        from PyQt5.QtWidgets import QSpinBox

        self.tail_lines_spin = QSpinBox()
        self.tail_lines_spin.setRange(10, 10000)
        self.tail_lines_spin.setValue(100)
        self.tail_lines_spin.setSuffix(" lines")
        self.tail_lines_spin.setEnabled(False)
        self.tail_lines_spin.valueChanged.connect(self._apply_tail_filter)
        filter_layout.addWidget(self.tail_lines_spin)

        # Clear button
        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(self._clear_filters)
        filter_layout.addWidget(clear_button)

        return filter_layout

    def _on_filter_changed(self, level):
        """Handler for when the level filter changes."""
        self.log_model.set_filter_level(level)
        self._update_status()

    def _on_search_changed(self, text=None):
        """Handler for when the search text changes."""
        # Always get text from search input (ignore signal argument which may be int)
        text = self.search_input.text()
        use_regex = self.regex_check.isChecked()
        self.log_model.set_filter_text(text, use_regex=use_regex)
        self._update_status()

    def _on_regex_changed(self, state):
        """Handler for when the regex checkbox changes."""
        self._on_search_changed()

    def _clear_filters(self):
        """Clear all filters."""
        self.level_combo.setCurrentText("DEBUG")
        self.search_input.clear()
        self.regex_check.setChecked(False)
        self.tail_check.setChecked(False)

    def on_log_received(self, log_record):
        """Handler for when a new log record is received."""
        # Add to model
        self.log_model.add_log(log_record)

        # Auto-scroll if enabled
        if self.auto_scroll_check.isChecked():
            self.log_table.scrollToBottom()

        # Update status
        self._update_status()

    def _update_status(self):
        """Update the status bar with current information."""
        total = len(self.log_model.logs)
        filtered = len(self.log_model.filtered_logs)

        if total == filtered:
            self.log_count_label.setText(f"{total} logs")
        else:
            self.log_count_label.setText(f"{filtered} / {total} logs")

    def _clear_logs(self):
        """Clear all logs from the display."""
        reply = QMessageBox.question(
            self,
            "Clear Logs",
            "Are you sure you want to clear all logs?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            self.log_model.clear()
            self._update_status()

    def _export_logs(self):
        """Export logs to a CSV file."""
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export Logs", "", "CSV Files (*.csv);;All Files (*)"
        )

        if filename:
            try:
                self.log_model.export_logs(filename)
                self.status_bar.showMessage(f"Logs exported to {filename}", 5000)
            except Exception as e:
                QMessageBox.warning(self, "Export Error", f"Error exporting logs: {e}")

    def _open_log_file(self):
        """Open an existing log file."""
        filename, _ = QFileDialog.getOpenFileName(
            self, "Open Log File", "", "Log Files (*.log *.log*);;All Files (*)"
        )

        if filename:
            try:
                # Clear current logs
                self.log_model.clear()

                # Update the log monitor to watch this file
                self.log_monitor.set_log_file(filename)

                # Parse and load the log file
                with open(filename, "r") as f:
                    for line in f:
                        try:
                            # Parse log line - assumes format: TIMESTAMP - LEVEL - NAME - MESSAGE
                            parts = line.strip().split(" - ", 3)
                            if len(parts) >= 4:
                                timestamp, level, name, message = parts
                                log_record = LogRecord(level, name, message, timestamp)
                                self.log_model.add_log(log_record)
                        except Exception as e:
                            print(f"Error parsing log line: {e}")

                self.status_bar.showMessage(f"Monitoring log file: {filename}", 5000)
                self._update_status()
            except Exception as e:
                QMessageBox.warning(self, "Open Error", f"Error opening log file: {e}")

    def _show_context_menu(self, pos):
        """Show context menu for the log table."""
        menu = QMenu(self)

        # Get selected rows
        selected_rows = [
            index.row() for index in self.log_table.selectionModel().selectedRows()
        ]

        if selected_rows:
            copy_action = menu.addAction("Copy Selected")
            copy_action.triggered.connect(self._copy_selected_logs)

            menu.addSeparator()

        menu.addAction(self.action_export)

        menu.exec_(self.log_table.mapToGlobal(pos))

    def _copy_selected_logs(self):
        """Copy selected log entries to clipboard."""
        selected_rows = sorted(
            set(index.row() for index in self.log_table.selectionModel().selectedRows())
        )

        if not selected_rows:
            return

        text = []
        for row in selected_rows:
            log_entry = self.log_model.filtered_logs[row]
            text.append(
                f"{log_entry['timestamp']} - {log_entry['level']} - {log_entry['source']} - {log_entry['message']}"
            )

        QApplication.clipboard().setText("\n".join(text))
        self.status_bar.showMessage(
            f"Copied {len(text)} log entries to clipboard", 3000
        )

    def _start_pipeline(self):
        """Start the main pipeline script."""
        # This is a placeholder method that could be used to start the pipeline
        # For now, just show a message
        QMessageBox.information(
            self,
            "Start Pipeline",
            "This feature is not implemented yet. In the future, it could be used to launch the pipeline directly from the GUI.",
        )

    def _refresh_monitor(self):
        """Refresh the log monitor by reloading the current log file."""
        if not self.log_monitor.log_file or not os.path.exists(
            self.log_monitor.log_file
        ):
            self.status_bar.showMessage("No log file to refresh", 3000)
            return

        # Remember the log file path
        log_file = self.log_monitor.log_file

        # Temporarily disable auto-scroll during bulk load
        was_auto_scroll = self.auto_scroll_check.isChecked()
        self.auto_scroll_check.setChecked(False)

        # Clear current logs
        self.log_model.clear()

        # Reload the file
        try:
            with open(log_file, "r") as f:
                for line in f:
                    try:
                        parts = line.strip().split(" - ", 3)
                        if len(parts) >= 4:
                            timestamp, level, name, message = parts
                            log_record = LogRecord(level, name, message, timestamp)
                            self.log_model.add_log(log_record)
                    except Exception:
                        pass

            # Update file position for monitoring
            self.log_monitor.last_file_position = os.path.getsize(log_file)

            self.status_bar.showMessage(f"Refreshed: {log_file}", 5000)
        except Exception as e:
            self.status_bar.showMessage(f"Error refreshing: {e}", 5000)

        # Restore auto-scroll and scroll to bottom
        self.auto_scroll_check.setChecked(was_auto_scroll)
        if was_auto_scroll:
            self.log_table.scrollToBottom()

        self._update_status()

    def _check_file_size(self):
        """Check if the log file has changed and process new entries."""
        try:
            # Get the file size
            file_size = os.path.getsize(self.log_monitor.log_file)

            # If file size has increased, read the new content
            if file_size > self.log_monitor.last_file_position:
                with open(self.log_monitor.log_file, "r") as f:
                    # Move to the last position we read
                    f.seek(self.log_monitor.last_file_position)

                    # Read new lines
                    new_lines = f.readlines()

                    # Update position
                    self.log_monitor.last_file_position = file_size

                    # Process each new line
                    for line in new_lines:
                        try:
                            # Parse log line - assumes format: TIMESTAMP - LEVEL - NAME - MESSAGE
                            parts = line.strip().split(" - ", 3)
                            if len(parts) >= 4:
                                timestamp, level, name, message = parts
                                log_record = LogRecord(level, name, message, timestamp)
                                self.log_model.add_log(log_record)
                        except Exception as e:
                            print(f"Error parsing log line: {e}")
        except Exception as e:
            print(f"Error checking log file: {e}")

    def _toggle_auto_refresh(self, state):
        """Toggle auto-refresh timer on/off."""
        if state == Qt.Checked:
            # Start timer to check file every 2 seconds
            self.refresh_timer.start(2000)
            self.status_bar.showMessage("Auto-refresh enabled", 2000)
        else:
            # Stop timer
            self.refresh_timer.stop()
            self.status_bar.showMessage("Auto-refresh disabled", 2000)

    def closeEvent(self, event):
        """Handle window close event."""
        # Stop the log monitor thread
        if hasattr(self, "log_monitor"):
            self.log_monitor.stop()

        # Stop the refresh timer
        if hasattr(self, "refresh_timer"):
            self.refresh_timer.stop()

        # Accept the close event
        event.accept()


def main():
    """Entry point for viewlogs command."""
    import argparse
    import os

    parser = argparse.ArgumentParser(
        description="LOFAR Pipeline Log Viewer - View and analyze pipeline log files",
        usage="viewlogs [logfile] [--theme {dark,light}]",
    )
    parser.add_argument(
        "logfile", nargs="?", default=None, help="Log file to open (optional)"
    )
    parser.add_argument(
        "--theme",
        "-t",
        type=str,
        choices=["dark", "light"],
        default="dark",
        help="Color theme (dark or light, default: dark)",
    )
    args = parser.parse_args()

    # Apply high DPI scaling
    from solar_radio_image_viewer.from_simpl.simpl_theme import setup_high_dpi
    setup_high_dpi()

    app = QApplication(sys.argv)

    # Set application style
    app.setStyle("Fusion")

    # Apply theme
    from solar_radio_image_viewer.from_simpl.simpl_theme import apply_theme
    apply_theme(app, args.theme)

    # Create and show the main window with theme
    main_window = PipelineLoggerGUI(theme=args.theme)
    main_window.show()

    # Open log file if specified
    if args.logfile:
        logfile_path = os.path.abspath(args.logfile)
        if os.path.exists(logfile_path):
            # Load the log file (same logic as _open_log_file)
            main_window.log_model.clear()
            main_window.log_monitor.set_log_file(logfile_path)

            # Parse and load existing logs
            with open(logfile_path, "r") as f:
                for line in f:
                    try:
                        parts = line.strip().split(" - ", 3)
                        if len(parts) >= 4:
                            timestamp, level, name, message = parts
                            log_record = LogRecord(level, name, message, timestamp)
                            main_window.log_model.add_log(log_record)
                    except Exception:
                        pass

            main_window.status_bar.showMessage(f"Loaded: {logfile_path}", 5000)
            main_window._update_status()
        else:
            print(f"Warning: Log file not found: {logfile_path}")

    # Start the application
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

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
import threading
import time
from contextlib import nullcontext
from queue import Queue, Empty
from datetime import datetime
from ..ui.activity import begin_activity
from ..ui.background import Cancelled, run_in_thread
from ..ui.busy import busy
from ..ui.styles import set_hand_cursor

from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTableView,
    QHeaderView,
    QComboBox,
    QPushButton,
    QLineEdit,
    QLabel,
    QCheckBox,
    QStatusBar,
    QAction,
    QFileDialog,
    QStyle,
    QStyledItemDelegate,
    QMenu,
    QToolBar,
    QAbstractItemView,
    QMessageBox,
    QGridLayout,
    QShortcut,
    QDialog,
    QFrame,
    QScrollArea,
)
from PyQt5.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    Qt,
    QThread,
    pyqtSignal,
    QTimer,
    QSize,
    QRect,
)
from PyQt5.QtGui import (
    QColor,
    QKeySequence,
)


from ..ui.geometry import fit_to_screen

LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
SEARCH_PAUSE_MS = 250  # the search runs once typing pauses this long
FILTER_BUSY_LINES = 500000  # filtering a log this long is a noticeable wait
LOAD_REPORT_LINES = 20000  # a load reports and looks for Cancel this often

_ALIVE = set()  # running threads, which can outlive the window on a dead disk


class LogTableWidget(QTableView):
    """Custom QTableView that prevents horizontal auto-scrolling during navigation."""

    def rowCount(self):
        return self.model().rowCount()

    def currentRow(self):
        return self.currentIndex().row()

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


def parse_log_line(line):
    """The LogRecord of a line (format: TIMESTAMP - LEVEL - NAME - MESSAGE),
    or None for a line of any other form."""
    parts = line.strip().split(" - ", 3)
    if len(parts) >= 4:
        timestamp, level, name, message = parts
        return LogRecord(level, name, message, timestamp)
    return None


def text_lines(raw):
    """The lines in ``raw``, bytes up to a line feed: as when a file is read
    as text, a carriage return alone ends a line too."""
    text = raw.decode("utf-8", "replace")
    if "\r" not in text:
        return [text]
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def log_entry(log_record):
    """A LogRecord as the model keeps it."""
    return {
        "timestamp": log_record.timestamp,
        "level": log_record.level,
        "source": log_record.name,
        "message": log_record.message,
    }


class LogFileLoader(QThread):
    """Reads a whole log file and hands over its entries at the end, so what
    the window shows stays as it is until the new file is there, and Cancel
    (``stop``) leaves it alone. ``task`` is the Activity to report to."""

    loaded = pyqtSignal(object, object)  # entries, how many bytes were read
    failed = pyqtSignal(object)

    def __init__(self, path, task):
        super().__init__()
        self.path = path
        self.task = task
        self.stop = False
        _ALIVE.add(self)
        self.finished.connect(lambda: _ALIVE.discard(self))

    def run(self):
        try:
            megabytes = max(1, os.path.getsize(self.path) // 1000000)
            self.task.update(done=0, total=megabytes)
            entries = []
            position = 0
            count = 0
            with open(self.path, "rb") as f:
                for raw in f:
                    position += len(raw)
                    for line in text_lines(raw):
                        record = parse_log_line(line)
                        if record is not None:
                            entries.append(log_entry(record))
                    count += 1
                    if count % LOAD_REPORT_LINES == 0:
                        if self.stop:
                            return
                        self.task.update(done=position // 1000000)
        except Exception as e:
            self.failed.emit(e)
        else:
            self.loaded.emit(entries, position)


class LogMonitorThread(QThread):
    """Thread for monitoring log queue and emitting signals when new logs arrive."""

    log_received = pyqtSignal(object)  # Signal emitted when new log is received
    # New lines of the file, together: a line at a time would flood the window
    logs_received = pyqtSignal(object, object)  # generation, list of LogRecord

    def __init__(self, log_queue, parent=None, log_file=None):
        super().__init__(parent)
        self.log_queue = log_queue
        self.log_file = log_file
        self.running = True
        self.last_file_position = 0
        self.last_file_check = 0
        self.file_check_interval = 0.5  # Check file every 0.5 seconds
        self.following = True  # False: the file is left alone (Auto-refresh off)
        self.generation = 0  # bumped when another file or position is set
        self._lock = threading.Lock()  # never held during a read
        _ALIVE.add(self)
        self.finished.connect(lambda: _ALIVE.discard(self))

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
                    self.following
                    and self.log_file
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
            with self._lock:
                generation = self.generation
                log_file = self.log_file
                position = self.last_file_position

            # If file size has increased, read the new content
            if os.path.getsize(log_file) > position:
                with open(log_file, "rb") as f:
                    # Move to the last position we read
                    f.seek(position)

                    # Read new lines
                    new_lines = f.readlines()
                    position = f.tell()

                with self._lock:
                    if generation != self.generation:
                        return  # another file now: these lines belong to no one
                    self.last_file_position = position

                records = [parse_log_line(line) for raw in new_lines for line in text_lines(raw)]
                records = [record for record in records if record is not None]
                if records:
                    self.logs_received.emit(generation, records)
        except Exception as e:
            print(f"Error checking log file: {e}")

    def set_log_file(self, log_file):
        """Set the log file to monitor."""
        self.follow(log_file, os.path.getsize(log_file) if os.path.exists(log_file) else 0)

    def follow(self, log_file, position):
        """Monitor ``log_file`` from byte ``position`` on."""
        with self._lock:
            self.log_file = log_file
            self.last_file_position = position
            self.generation += 1

    def stop(self):
        """Stop the thread. A read stuck on a dead network disk does not hold
        up the window closing: the thread ends when the read does."""
        self.running = False
        self.wait(2000)


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
        from PyQt5.QtGui import QTextDocument

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

        text = index.data()
        if not text:
            return super().sizeHint(option, index)

        doc = QTextDocument()
        doc.setDefaultFont(option.font)
        doc.setPlainText(text)

        return QSize(int(doc.idealWidth()) + 10, int(doc.size().height()))


class LogRows(QAbstractTableModel):
    """The rows of the table: the filtered logs of a LogTableModel. Nothing
    per cell, so that a log of millions of lines costs the window nothing."""

    KEYS = ("timestamp", "level", "source", "message")

    def __init__(self, log_model):
        super().__init__()
        self.log_model = log_model

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.log_model.filtered_logs)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.KEYS)

    def data(self, index, role=Qt.DisplayRole):
        if role in (Qt.DisplayRole, Qt.EditRole):
            return self.log_model.filtered_logs[index.row()][self.KEYS[index.column()]]
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.log_model.COLUMNS[section]
        return super().headerData(section, orientation, role)

    def flags(self, index):
        # A cell opens in an editor to copy a part of it; nothing is changed
        return super().flags(index) | Qt.ItemIsEditable


class LogTableModel:
    """Model for storing and managing log entries."""

    COLUMNS = ["Timestamp", "Level", "Source", "Message"]

    def __init__(self, table_widget, theme="dark"):
        self.table = table_widget
        self.theme = theme
        self.logs = []
        self.filtered_logs = []
        self._longest_added = 0  # longest message added since the table was rebuilt
        self.filter_level = "DEBUG"  # Show all by default
        self.filter_text = ""
        self.use_regex = False  # Regex search mode
        self._regex_pattern = None  # Compiled regex pattern

        # Set up table
        self.setup_table()

    def setup_table(self):
        """Configure the table widget."""
        self.rows = LogRows(self)
        self.table.setModel(self.rows)

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
        self.add_logs([log_record])

    def add_logs(self, log_records):
        """Add new log records to the model, in one update of the table."""
        entries = [log_entry(log_record) for log_record in log_records]
        self.logs.extend(entries)

        # Check which of these logs should be displayed based on current filters
        shown = [entry for entry in entries if self._matches_filter(entry)]
        if shown:
            first = len(self.filtered_logs)
            self.rows.beginInsertRows(QModelIndex(), first, first + len(shown) - 1)
            self.filtered_logs.extend(shown)
            self.rows.endInsertRows()
            # (measuring the rows is slow in a long log: only when it can matter)
            longest = max(len(entry["message"]) for entry in shown)
            if longest > self._longest_added:
                self._longest_added = longest
                self.table.resizeColumnsToContents()

    def set_logs(self, entries):
        """Replace all logs with ``entries``."""
        self.logs = entries
        self.filtered_logs = [log for log in entries if self._matches_filter(log)]
        self._refresh_table()

    def clear(self):
        """Clear all logs from the model and table."""
        self.logs = []
        self.filtered_logs = []
        self._refresh_table()

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
        if log_entry["level"] not in LEVELS:
            return False  # a line that only looks like a log line
        level_idx = LEVELS.index(log_entry["level"])
        min_level_idx = LEVELS.index(self.filter_level)
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
            self.table.scrollTo(self.rows.index(new_row, 0))

    def _refresh_table(self):
        """Rebuild the table from current filtered_logs."""
        self._longest_added = 0
        self.rows.beginResetModel()
        self.rows.endResetModel()


def write_logs_csv(filename, logs, stop=None):
    """Write ``logs`` to a CSV file, until the event ``stop`` is set."""
    with open(filename, "w") as f:
        # Write header
        f.write(",".join([f'"{col}"' for col in LogTableModel.COLUMNS]) + "\n")

        # Write data
        for number, log in enumerate(logs):
            if stop is not None and number % LOAD_REPORT_LINES == 0 and stop.is_set():
                return
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
        self._loader = None  # the load under way
        self._load_task = None

        # Initialize empty queue for logs
        self.log_queue = Queue()

        # Initialize UI
        self.setWindowTitle("LOFAR Pipeline Logger")
        self.setMinimumSize(640, 480)
        fit_to_screen(self, 1100, 750)

        # Create UI components
        self._create_ui()

        # Set up keyboard shortcuts
        self._setup_shortcuts()
        set_hand_cursor(self)

        # Set up log monitor with no initial log file
        self.log_monitor = LogMonitorThread(self.log_queue)
        self.log_monitor.log_received.connect(self.on_log_received)
        self.log_monitor.logs_received.connect(self.on_logs_received)

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

        # Stops a load; a load shows in the pill
        self.cancel_load_button = QPushButton("Cancel")
        self.cancel_load_button.setToolTip("Stop reading the log file")
        self.cancel_load_button.clicked.connect(self._cancel_load)
        self.cancel_load_button.setVisible(False)
        self.status_bar.addPermanentWidget(self.cancel_load_button)

        # Auto-scroll checkbox on status bar
        self.auto_scroll_check = QCheckBox("Auto-scroll")
        self.auto_scroll_check.setChecked(True)
        self.status_bar.addPermanentWidget(self.auto_scroll_check)

        # Auto-refresh checkbox on status bar
        self.auto_refresh_check = QCheckBox("Auto-refresh")
        self.auto_refresh_check.setChecked(True)
        self.auto_refresh_check.setToolTip("Show new lines as the log file grows")
        self.auto_refresh_check.stateChanged.connect(self._toggle_auto_refresh)
        self.status_bar.addPermanentWidget(self.auto_refresh_check)

        # Log count on status bar
        self.log_count_label = QLabel("0 logs")
        self.status_bar.addPermanentWidget(self.log_count_label)

    def _create_toolbar(self):
        """Create the main toolbar."""
        # Plain labels: the same look in both themes, no stock icons
        self.toolbar = QToolBar("Main Toolbar")
        self.toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.toolbar.setMovable(False)
        self.addToolBar(self.toolbar)

        def action(text, slot, tip):
            act = QAction(text, self)
            act.setToolTip(tip)
            act.triggered.connect(slot)
            self.toolbar.addAction(act)
            return act

        self.action_open = action("Open", self._open_log_file, "Open a log file (Ctrl+O)")
        self.action_export = action("Export", self._export_logs, "Save the log as CSV (Ctrl+E)")
        self.action_refresh = action("Refresh", self._refresh_monitor, "Read the log file again (F5)")
        self.toolbar.addSeparator()
        self.action_scroll_top = action("Top", self._scroll_to_top, "Scroll to the top (Home)")
        self.action_scroll_bottom = action("Bottom", self._scroll_to_bottom, "Scroll to the bottom (End)")
        self.toolbar.addSeparator()
        self.action_prev_error = action("Previous error", self._jump_to_prev_error, "Jump to the previous error (Ctrl+,)")
        self.action_next_error = action("Next error", self._jump_to_next_error, "Jump to the next error (Ctrl+.)")
        self.toolbar.addSeparator()
        self.action_help = action("Help", self._show_help_dialog, "Features and shortcuts")

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
            with self._filtering():
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

        header = QFrame()
        header.setObjectName("Banner")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(25, 20, 25, 20)
        header_layout.setSpacing(8)
        
        title = QLabel("LOFAR Pipeline Log Viewer")
        title.setObjectName("DialogTitle")
        header_layout.addWidget(title)
        
        subtitle = QLabel("Real-time monitoring and analysis of pipeline logs")
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
            label.setObjectName("SectionTitle")
            content_layout.addWidget(label)
            line = QFrame()
            line.setObjectName("Rule")
            content_layout.addWidget(line)

        # Features section
        add_section("Features")
        features = [
            ("Real-time Log Monitoring", "Automatic updates as pipeline runs"),
            ("Advanced Filtering", "Filter by level and source"),
            ("⌨️ Search & Regex", "Fast search with regular expression support"),
            ("Tail Mode", "Keep view focused on the latest N log entries"),
            ("Error Navigation", "Quickly jump between errors and critical logs"),
            ("Data Export", "Save log history to CSV for external analysis")
        ]
        
        feat_grid = QGridLayout()
        feat_grid.setSpacing(12)
        for i, (f_title, f_desc) in enumerate(features):
            f_item = QLabel(f"<b>{f_title}</b>: {f_desc}")
            f_item.setWordWrap(True)
            feat_grid.addWidget(f_item, i // 2, i % 2)
        content_layout.addLayout(feat_grid)
        
        # Keyboard Shortcuts
        add_section("Keyboard Shortcuts")
        
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
        
        for i, (key, desc) in enumerate(shortcuts):
            row, col = i % 5, (i // 5) * 2
            
            key_pill = QLabel(key)
            key_pill.setAlignment(Qt.AlignCenter)
            key_pill.setObjectName("Key")
            
            desc_label = QLabel(desc)
            
            shot_grid.addWidget(key_pill, row, col)
            shot_grid.addWidget(desc_label, row, col + 1)
        
        content_layout.addLayout(shot_grid)
        
        content_layout.addStretch()
        
        scroll.setWidget(content_widget)
        main_layout.addWidget(scroll)
        
        # Bottom bar
        bottom_bar = QFrame()
        bottom_layout = QHBoxLayout(bottom_bar)
        
        footer = QLabel("SolarViewer Log Utilities • v1.0.0")
        footer.setObjectName("Hint")
        bottom_layout.addWidget(footer)
        bottom_layout.addStretch()
        
        close_btn = QPushButton("Close")
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
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_search)

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
        with self._filtering():
            self.log_model.set_filter_level(level)
        self._update_status()

    def _filtering(self, total=None):
        """The wait cursor and pill for filtering ``total`` lines (by default
        the log shown), if that is enough for it to be a wait."""
        if total is None:
            total = len(self.log_model.logs)
        if total < FILTER_BUSY_LINES:
            return nullcontext()
        return busy(f"Filtering {total:,} log lines…", self)

    def _on_search_changed(self, text=None):
        """Handler for when the search text changes: it waits for a pause in
        the typing, every key would filter the whole log."""
        self._search_timer.start(SEARCH_PAUSE_MS)

    def _apply_search(self):
        self._search_timer.stop()
        # Always get text from search input
        text = self.search_input.text()
        use_regex = self.regex_check.isChecked()
        with self._filtering():
            self.log_model.set_filter_text(text, use_regex=use_regex)
        self._update_status()

    def _on_regex_changed(self, state):
        """Handler for when the regex checkbox changes."""
        self._apply_search()

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

    def on_logs_received(self, generation, log_records):
        """Handler for new lines of the log file."""
        if generation != self.log_monitor.generation:
            return  # sent just before another file was opened
        self.log_model.add_logs(log_records)
        if self.auto_scroll_check.isChecked():
            self.log_table.scrollToBottom()
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
            stop = threading.Event()
            try:
                run_in_thread(
                    self,
                    f"Exporting to {os.path.basename(filename)}…",
                    write_logs_csv,
                    filename,
                    list(self.log_model.logs),
                    stop,
                )
                self.status_bar.showMessage(f"Logs exported to {filename}", 5000)
            except Cancelled:
                stop.set()
                self.status_bar.showMessage(f"Cancelled: {filename} is incomplete", 5000)
            except Exception as e:
                QMessageBox.warning(self, "Export Error", f"Error exporting logs: {e}")

    def _open_log_file(self):
        """Open an existing log file."""
        filename, _ = QFileDialog.getOpenFileName(
            self, "Open Log File", "", "Log Files (*.log *.log*);;All Files (*)"
        )

        if filename:
            self.openFile(filename)

    def openFile(self, filename):
        """Show a log file and follow it as it grows."""
        self._load(
            filename,
            f"Monitoring log file: {filename}",
            lambda e: QMessageBox.warning(self, "Open Error", f"Error opening log file: {e}"),
        )

    def _load(self, path, done_text, fail, to_bottom=False):
        """Read ``path`` in the background; meanwhile the window works as
        before. A load under way gives way to this one."""
        self._end_load()
        self._load_task = begin_activity(
            f"Reading {os.path.basename(path)} (MB)…", self, blocking=False
        )
        loader = LogFileLoader(path, self._load_task)
        loader.loaded.connect(
            lambda entries, position: loader is self._loader
            and self._loaded(path, entries, position, done_text, to_bottom)
        )
        loader.failed.connect(lambda e: loader is self._loader and self._load_failed(e, fail))
        self._loader = loader
        self.cancel_load_button.setVisible(True)
        loader.start()

    def _loaded(self, path, entries, position, done_text, to_bottom):
        self._end_load()
        with self._filtering(len(entries)):
            self.log_model.set_logs(entries)
        self.log_monitor.follow(path, position)
        if to_bottom:
            self.log_table.scrollToBottom()
        self.status_bar.showMessage(done_text, 5000)
        self._update_status()

    def _load_failed(self, error, fail):
        self._end_load()
        fail(error)

    def _end_load(self):
        """Forget the load under way; if it still runs it stops by itself, or
        with the read that holds it up."""
        loader, self._loader = self._loader, None
        if loader is not None:
            loader.stop = True
        task, self._load_task = self._load_task, None
        if task is not None:
            task.finish()
        self.cancel_load_button.setVisible(False)

    def _cancel_load(self):
        self._end_load()
        self.status_bar.showMessage("Cancelled", 3000)

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

        log_file = self.log_monitor.log_file
        self._load(
            log_file,
            f"Refreshed: {log_file}",
            lambda e: self.status_bar.showMessage(f"Error refreshing: {e}", 5000),
            to_bottom=self.auto_scroll_check.isChecked(),
        )

    def _toggle_auto_refresh(self, state):
        """Follow the log file, or leave it; new lines wait until it is on."""
        self.log_monitor.following = state == Qt.Checked
        if state == Qt.Checked:
            self.status_bar.showMessage("Auto-refresh enabled", 2000)
        else:
            self.status_bar.showMessage("Auto-refresh disabled", 2000)

    def closeEvent(self, event):
        """Handle window close event."""
        self._end_load()
        self._search_timer.stop()

        # Stop the log monitor thread
        if hasattr(self, "log_monitor"):
            with busy("Closing the log viewer…", self):
                self.log_monitor.stop()

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
        default=None,
        help="Color theme (dark or light; default: the one SolarViewer uses)",
    )
    args = parser.parse_args()

    from ..ui.app import create_app
    from ..ui.styles import theme_manager

    app = create_app(args.theme)
    main_window = PipelineLoggerGUI(theme=theme_manager.current_theme)
    main_window.show()

    # Open log file if specified
    if args.logfile:
        logfile_path = os.path.abspath(args.logfile)
        if os.path.exists(logfile_path):
            # Once the window is up, so that it shows the load
            QTimer.singleShot(0, lambda: main_window.openFile(logfile_path))
        else:
            print(f"Warning: Log file not found: {logfile_path}")

    # Start the application
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

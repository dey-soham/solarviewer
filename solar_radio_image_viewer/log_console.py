import sys
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QTextEdit,
    QPushButton,
    QApplication,
    QLabel,
    QWidget,
)
from PyQt5.QtCore import Qt, QObject, pyqtSignal, QSize
from PyQt5.QtGui import QFont, QTextCursor


# Thread-safe signal emitter for logging
class LogSignal(QObject):
    text_written = pyqtSignal(str)


class StreamRedirector:
    """Redirects writes to a stream (stdout/stderr) to a signal, while keeping the original stream."""

    def __init__(self, original_stream, signal, prefix=""):
        self.original_stream = original_stream
        self.signal = signal
        self.prefix = prefix

    def write(self, text):
        try:
            # Write to original stream first (terminal)
            if self.original_stream:
                self.original_stream.write(text)
                self.original_stream.flush()

            # Emit to GUI
            if text:
                self.signal.text_written.emit(text)
        except Exception:
            # Prevent logging errors from crashing the app
            pass

    def flush(self):
        try:
            if self.original_stream:
                self.original_stream.flush()
        except Exception:
            pass

    def isatty(self):
        # Pretend to be a tty if the original was one
        return getattr(self.original_stream, "isatty", lambda: False)()


class LogConsole(QDialog):
    """
    A persistent dialog that displays captured stdout/stderr logs.
    """

    _instance = None
    MAX_BUFFER_CHARS = 15000000  # ~15MB text limit to prevent OOM

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Log Console")
        self.resize(800, 600)

        # Force Window behavior to ensure Maximize works on all WMs
        self.setWindowFlags(
            Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint
        )

        # Setup layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Log display area
        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setLineWrapMode(
            QTextEdit.NoWrap
        )  # No wrap for log lines usually better

        # Set Monospace font
        font = QFont("Consolas, 'Courier New', monospace")
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(10)
        self.text_edit.setFont(font)

        layout.addWidget(self.text_edit)

        # Button bar
        btn_bar = QWidget()
        btn_layout = QHBoxLayout(btn_bar)
        btn_layout.setContentsMargins(8, 8, 8, 8)

        self.btn_clear = QPushButton("Clear")
        self.btn_clear.clicked.connect(self.clear_logs)

        self.btn_copy = QPushButton("Copy All")
        self.btn_copy.clicked.connect(self.copy_logs)

        self.btn_close = QPushButton("Hide")
        self.btn_close.clicked.connect(self.hide)

        self.auto_scroll_btn = QPushButton("Auto-scroll: ON")
        self.auto_scroll_btn.setCheckable(True)
        self.auto_scroll_btn.setChecked(True)
        self.auto_scroll_btn.clicked.connect(self.toggle_autoscroll)

        btn_layout.addWidget(self.btn_clear)
        btn_layout.addWidget(self.btn_copy)
        btn_layout.addWidget(self.auto_scroll_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_close)

        layout.addWidget(btn_bar)

        # Setup redirection
        self.log_signal = LogSignal()
        self.log_signal.text_written.connect(self.append_text)

        # Store original streams
        self.stdout_orig = sys.stdout
        self.stderr_orig = sys.stderr

        # Redirect
        sys.stdout = StreamRedirector(self.stdout_orig, self.log_signal)
        sys.stderr = StreamRedirector(self.stderr_orig, self.log_signal)

        self.apply_theme()
        
        try:
            from .styles import set_hand_cursor
            set_hand_cursor(self)
        except ImportError:
            pass

        # Register for theme updates
        try:
            from .styles import theme_manager

            theme_manager.register_callback(self.apply_theme)
        except ImportError:
            pass

        # Track auto-scroll state
        self.auto_scroll = True
        
        # For handling carriage returns (progress bar updates)
        # Track if last output was a progress line that should be overwritten
        self._last_line_is_progress = False

    def toggle_autoscroll(self):
        self.auto_scroll = self.auto_scroll_btn.isChecked()
        self.auto_scroll_btn.setText(
            f"Auto-scroll: {'ON' if self.auto_scroll else 'OFF'}"
        )
        if self.auto_scroll:
            self.text_edit.moveCursor(self.text_edit.textCursor().End)

    def append_text(self, text):
        # Safety check for massive single chunks
        if len(text) > 1000000:
            text = text[:1000000] + "\n... [truncated massive log chunk] ...\n"

        # Handle carriage returns for progress bar updates
        # If text starts with \r, it's meant to overwrite the current line
        if '\r' in text and '\n' not in text:
            # This is a progress update - overwrite last line
            self._overwrite_last_line(text.replace('\r', ''))
            self._last_line_is_progress = True
        elif text == '\n' and self._last_line_is_progress:
            # Newline after progress - just mark that progress is done
            self._last_line_is_progress = False
        else:
            # Normal text - just append
            # If last was progress and this has content, add newline first
            cursor = self.text_edit.textCursor()
            cursor.movePosition(QTextCursor.End)
            cursor.insertText(text)
            self._last_line_is_progress = False

            if self.auto_scroll:
                self.text_edit.setTextCursor(cursor)
                self.text_edit.ensureCursorVisible()
    
    def _overwrite_last_line(self, text):
        """Overwrite the last line in the text edit (for progress bars)."""
        cursor = self.text_edit.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.movePosition(QTextCursor.StartOfBlock, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        cursor.insertText(text)
        
        if self.auto_scroll:
            self.text_edit.setTextCursor(cursor)
            self.text_edit.ensureCursorVisible()

    def clear_logs(self):
        self.text_edit.clear()
        self._last_line_is_progress = False

    def copy_logs(self):
        self.text_edit.selectAll()
        self.text_edit.copy()
        cursor = self.text_edit.textCursor()
        cursor.clearSelection()
        self.text_edit.setTextCursor(cursor)

    def apply_theme(self, *args):
        """Apply current theme colors."""
        try:
            from .styles import theme_manager

            self.setStyleSheet(theme_manager.stylesheet)

            # Specific styling for console text area
            palette = theme_manager.palette
            is_dark = theme_manager.is_dark

            # Darker background for console than standard input
            console_bg = "#0d0d15" if is_dark else "#fcfcfc"
            console_text = palette["text"]
            border_color = palette["border"]

            self.text_edit.setStyleSheet(
                f"""
                QTextEdit {{
                    background-color: {console_bg};
                    color: {console_text};
                    border: 1px solid {border_color};
                    border-radius: 4px;
                    font-family: 'Consolas', 'Courier New', monospace;
                    padding: 4px;
                }}
            """
            )
        except ImportError:
            pass

    def closeEvent(self, event):
        # Don't destroy, just hide
        event.ignore()
        self.hide()

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
from PyQt5.QtGui import QFont


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

        # Register for theme updates
        try:
            from .styles import theme_manager

            theme_manager.register_callback(self.apply_theme)
        except ImportError:
            pass

        # Track auto-scroll state
        self.auto_scroll = True

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

        cursor = self.text_edit.textCursor()
        cursor.movePosition(cursor.End)
        cursor.insertText(text)

        if self.auto_scroll:
            self.text_edit.setTextCursor(cursor)
            self.text_edit.ensureCursorVisible()

    def clear_logs(self):
        self.text_edit.clear()

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

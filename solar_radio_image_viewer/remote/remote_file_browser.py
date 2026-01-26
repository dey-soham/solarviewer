"""
Remote File Browser Dialog for SolarViewer.

Provides a file browser interface for navigating remote directories
via SFTP and selecting FITS files to open.
"""

import os
from pathlib import Path
from typing import Optional, List, Callable

from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QTreeWidget,
    QTreeWidgetItem,
    QPushButton,
    QLabel,
    QLineEdit,
    QProgressBar,
    QMessageBox,
    QCheckBox,
    QSplitter,
    QFrame,
    QHeaderView,
    QAbstractItemView,
    QWidget,
    QCompleter,
)
from PyQt5.QtCore import Qt, pyqtSignal, QThread, QTimer, QStringListModel
from PyQt5.QtGui import QIcon

from .ssh_manager import SSHConnection, SSHConnectionError, RemoteFileInfo
from .file_cache import RemoteFileCache


from .download_thread import DownloadThread


class ListDirectoryThread(QThread):
    """Thread for listing directories without blocking UI."""

    finished = pyqtSignal(list)  # list of RemoteFileInfo
    error = pyqtSignal(str)  # error message

    def __init__(
        self,
        connection: SSHConnection,
        path: str,
        show_hidden: bool = False,
        fits_only: bool = False,
    ):
        super().__init__()
        self.connection = connection
        self.path = path
        self.show_hidden = show_hidden
        self.fits_only = fits_only
        self._cancelled = False
        self._sftp = None  # Thread's own SFTP channel

    def cancel(self):
        """Request cancellation of the listing operation."""
        self._cancelled = True
        # Try to close our SFTP channel to interrupt blocking operation
        if self._sftp:
            try:
                self._sftp.close()
            except:
                pass

    def run(self):
        try:
            # FAST PATH: Try executing a python script on the remote server
            try:
                import json
                import shlex

                # Script tailored to this view's flags (show_hidden, fits_only)
                py_script = """
import os, json, sys, stat
path = sys.argv[1]
entries = []
show_hidden = %s
fits_only = %s

try:
    with os.scandir(path) as it:
        for e in it:
            name = e.name
            
            # Skip hidden files unless requested
            if not show_hidden and name.startswith('.'):
                continue
            
            is_dir = e.is_dir()
            
            # Filter for FITS files if requested
            if fits_only and not is_dir:
                if not name.lower().endswith(('.fits', '.fts', '.fit')):
                    continue
            
            s = e.stat()
            entries.append({
                'n': name,
                'd': is_dir,
                's': s.st_size,
                'm': s.st_mtime
            })
            
    print(json.dumps(entries))
except Exception:
    print("[]")
""" % (
                    "True" if self.show_hidden else "False",
                    "True" if self.fits_only else "False",
                )

                # Try python3 first
                cmd = f"python3 -c {shlex.quote(py_script)} {shlex.quote(self.path)}"
                stdin, stdout, stderr = self.connection._client.exec_command(cmd)

                # Check for cancellation before reading
                if self._cancelled:
                    return

                out_data = stdout.read().decode("utf-8").strip()

                if out_data and out_data.startswith("["):
                    try:
                        raw_entries = json.loads(out_data)
                        entries = []
                        for item in raw_entries:
                            if self._cancelled:
                                return

                            full_path = os.path.join(self.path, item["n"])
                            info = RemoteFileInfo(
                                name=item["n"],
                                path=full_path,
                                is_dir=item["d"],
                                size=item["s"],
                                mtime=item["m"],
                            )
                            entries.append(info)

                        # Sort and emit
                        entries.sort(key=lambda x: (not x.is_dir, x.name.lower()))
                        self.finished.emit(entries)
                        return  # Success!
                    except:
                        pass  # Fallback to SFTP
            except Exception:
                pass  # Fallback to SFTP

            # FALLBACK: Use SFTP loop (slow for large dirs)
            # Create our own SFTP channel for this thread
            if self.connection._client:
                self._sftp = self.connection._client.open_sftp()
            else:
                raise SSHConnectionError("SSH client not connected")

            # List directory using our own SFTP channel
            import stat

            entries = []
            attrs = self._sftp.listdir_attr(self.path)

            for attr in attrs:
                if self._cancelled:
                    break

                name = attr.filename

                # Skip hidden files if not requested
                if not self.show_hidden and name.startswith("."):
                    continue

                is_dir = stat.S_ISDIR(attr.st_mode)
                full_path = os.path.join(self.path, name)

                info = RemoteFileInfo(
                    name=name,
                    path=full_path,
                    is_dir=is_dir,
                    size=attr.st_size,
                    mtime=attr.st_mtime,
                )

                # Filter for FITS files if requested
                if self.fits_only and not is_dir and not info.is_fits:
                    continue

                entries.append(info)

            if not self._cancelled:
                # Sort: directories first, then alphabetically
                entries.sort(key=lambda x: (not x.is_dir, x.name.lower()))
                self.finished.emit(entries)

        except Exception as e:
            if not self._cancelled:
                self.error.emit(str(e))
        finally:
            # Always close our SFTP channel
            if self._sftp:
                try:
                    self._sftp.close()
                except:
                    pass
                self._sftp = None


class AutocompleteThread(QThread):
    """Thread for fetching autocomplete suggestions without blocking UI."""

    finished = pyqtSignal(list)  # list of path suggestions

    def __init__(
        self,
        connection: SSHConnection,
        parent_path: str,
        partial_name: str = "",
    ):
        super().__init__()
        self.connection = connection
        self.parent_path = parent_path
        self.partial_name = partial_name.lower()
        self._cancelled = False
        self._sftp = None

    def cancel(self):
        """Request cancellation."""
        self._cancelled = True
        if self._sftp:
            try:
                self._sftp.close()
            except:
                pass

    def run(self):
        try:
            if not self.connection._client:
                return

            self._sftp = self.connection._client.open_sftp()

            import stat

            suggestions = []
            for attr in self._sftp.listdir_attr(self.parent_path):
                if self._cancelled:
                    break

                name = attr.filename

                # Skip hidden files unless partial starts with .
                if name.startswith(".") and not self.partial_name.startswith("."):
                    continue

                # Only include directories for path completion
                if not stat.S_ISDIR(attr.st_mode):
                    continue

                # Filter by partial name
                if self.partial_name and not name.lower().startswith(self.partial_name):
                    continue

                # Build full path suggestion
                if self.parent_path == "/":
                    full_path = f"/{name}"
                else:
                    full_path = f"{self.parent_path}/{name}"

                suggestions.append(full_path)

            if not self._cancelled:
                suggestions.sort(key=str.lower)
                self.finished.emit(suggestions)

        except Exception:
            # Silently fail - autocomplete is a convenience feature
            pass
        finally:
            if self._sftp:
                try:
                    self._sftp.close()
                except:
                    pass
                self._sftp = None


class RemoteFileBrowser(QDialog):
    """
    File browser dialog for navigating remote directories via SFTP.

    Signals:
        fileSelected(str): Emitted with local path when a file is downloaded and ready
    """

    fileSelected = pyqtSignal(str)  # local path to downloaded file

    # Class-level variable to remember last browsed directory per host
    _last_paths: dict = {}  # {host: last_path}

    # Class-level flag to track if there's a pending operation that may be blocking
    _has_pending_operation: bool = False

    # Class-level cache for directory listings: {(host, path): (entries, timestamp)}
    _listing_cache: dict = {}
    _cache_ttl: int = 60  # Cache TTL in seconds

    # Class-level list to keep track of active download threads to prevent garbage collection
    _active_downloads: List[QThread] = []

    @classmethod
    def _remove_active_thread(cls, thread):
        """Remove a thread from the active downloads list."""
        if thread in cls._active_downloads:
            cls._active_downloads.remove(thread)

    def __init__(
        self,
        connection: SSHConnection,
        cache: Optional[RemoteFileCache] = None,
        parent=None,
        casa_mode: bool = False,
    ):
        super().__init__(parent)

        # CASA mode selects directories, FITS mode selects files
        self.casa_mode = casa_mode
        mode_str = "CASA Images" if casa_mode else "FITS Files"
        self.setWindowTitle(f"Browse Remote {mode_str} - {connection.connection_info}")
        self.setMinimumSize(500, 500)
        self.resize(800, 600)
        self.setModal(True)

        self.connection = connection
        self.cache = cache or RemoteFileCache()
        self.current_path = "/"
        self._download_thread: Optional[DownloadThread] = None
        self._list_thread: Optional[ListDirectoryThread] = None
        self._autocomplete_thread: Optional[AutocompleteThread] = None
        self._autocomplete_timer: Optional[QTimer] = None

        self._setup_ui()
        self._apply_styles()
        
        try:
            from ..styles import set_hand_cursor
            set_hand_cursor(self)
        except ImportError:
            pass
# Load initial directory - use last path if available
        QTimer.singleShot(100, self._load_initial_directory)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # Header with title and connection info
        header_layout = QHBoxLayout()
        header_layout.setSpacing(12)

        title_label = QLabel("📁 Remote Browser")
        title_label.setObjectName("DialogTitle")
        title_label.setStyleSheet("font-size: 14pt;")
        header_layout.addWidget(title_label)

        header_layout.addStretch()

        connection_label = QLabel(f"🔗 {self.connection.connection_info}")
        connection_label.setObjectName("ConnectionInfo")
        header_layout.addWidget(connection_label)

        layout.addLayout(header_layout)

        # Breadcrumb navigation
        self.breadcrumb_layout = QHBoxLayout()
        self.breadcrumb_layout.setSpacing(0)
        self.breadcrumb_layout.setContentsMargins(8, 2, 8, 2)
        self.breadcrumb_widget = QWidget()
        self.breadcrumb_widget.setObjectName("BreadcrumbWidget")
        self.breadcrumb_widget.setLayout(self.breadcrumb_layout)
        layout.addWidget(self.breadcrumb_widget)

        # Navigation toolbar
        nav_layout = QHBoxLayout()
        nav_layout.setSpacing(8)

        self.home_btn = QPushButton("🏠")
        self.home_btn.setObjectName("NavButton")
        self.home_btn.setFixedSize(32, 32)
        self.home_btn.setToolTip("Go to home directory")
        self.home_btn.setCursor(Qt.PointingHandCursor)
        self.home_btn.clicked.connect(self._load_home_directory)
        nav_layout.addWidget(self.home_btn)

        self.up_btn = QPushButton("⬆️")
        self.up_btn.setObjectName("NavButton")
        self.up_btn.setFixedSize(32, 32)
        self.up_btn.setToolTip("Go up one directory")
        self.up_btn.setCursor(Qt.PointingHandCursor)
        self.up_btn.clicked.connect(self._go_up)
        nav_layout.addWidget(self.up_btn)

        self.path_edit = QLineEdit()
        self.path_edit.setObjectName("PathInput")
        self.path_edit.setPlaceholderText("Enter path...")
        self.path_edit.setFixedHeight(32)
        self.path_edit.returnPressed.connect(self._on_path_entered)

        # Setup autocomplete
        self._autocomplete_model = QStringListModel()
        self._path_completer = QCompleter(self._autocomplete_model, self)
        self._path_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._path_completer.setCompletionMode(QCompleter.PopupCompletion)
        self._path_completer.setMaxVisibleItems(10)

        popup = self._path_completer.popup()
        popup.setStyleSheet("QListView { font-size: 10pt; }")

        self.path_edit.setCompleter(self._path_completer)
        self.path_edit.textChanged.connect(self._on_path_text_changed)

        # Debounce timer for autocomplete
        self._autocomplete_timer = QTimer()
        self._autocomplete_timer.setSingleShot(True)
        self._autocomplete_timer.timeout.connect(self._fetch_autocomplete)

        nav_layout.addWidget(self.path_edit)

        self.refresh_btn = QPushButton("🔄")
        self.refresh_btn.setObjectName("NavButton")
        self.refresh_btn.setFixedSize(32, 32)
        self.refresh_btn.setToolTip("Refresh directory (bypass cache)")
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.clicked.connect(lambda: self._refresh(force_refresh=True))
        nav_layout.addWidget(self.refresh_btn)

        layout.addLayout(nav_layout)

        # File tree
        self.tree = QTreeWidget()
        self.tree.setObjectName("FileTree")
        self.tree.setHeaderLabels(["Name", "Size", "Modified"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)

        # Configure column sizes
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        self.tree.setColumnWidth(1, 100)
        self.tree.setColumnWidth(2, 150)

        layout.addWidget(self.tree, stretch=1)

        # Options bar
        options_frame = QFrame()
        options_frame.setObjectName("OptionsFrame")
        options_layout = QHBoxLayout(options_frame)
        options_layout.setContentsMargins(12, 8, 12, 8)
        options_layout.setSpacing(16)

        self.show_hidden_cb = QCheckBox("Show hidden files")
        self.show_hidden_cb.setCursor(Qt.PointingHandCursor)
        self.show_hidden_cb.stateChanged.connect(self._refresh)
        options_layout.addWidget(self.show_hidden_cb)

        self.fits_only_cb = QCheckBox("FITS files only")
        self.fits_only_cb.setChecked(True)
        self.fits_only_cb.setCursor(Qt.PointingHandCursor)
        self.fits_only_cb.stateChanged.connect(self._refresh)
        options_layout.addWidget(self.fits_only_cb)

        options_layout.addStretch()

        layout.addWidget(options_frame)

        # Progress area (hidden by default)
        self.progress_frame = QFrame()
        self.progress_frame.setObjectName("ProgressFrame")
        progress_layout = QVBoxLayout(self.progress_frame)
        progress_layout.setContentsMargins(12, 12, 12, 12)
        progress_layout.setSpacing(8)

        self.progress_label = QLabel("Downloading...")
        self.progress_label.setObjectName("ProgressLabel")
        progress_layout.addWidget(self.progress_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("ModernProgressBar")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(True)
        progress_layout.addWidget(self.progress_bar)

        self.progress_frame.hide()
        layout.addWidget(self.progress_frame)

        # Status bar
        status_layout = QHBoxLayout()
        status_layout.setSpacing(8)

        self.status_label = QLabel("")
        self.status_label.setObjectName("StatusLabel")
        status_layout.addWidget(self.status_label)

        status_layout.addStretch()

        self.cache_info_label = QLabel("")
        self.cache_info_label.setObjectName("CacheInfo")
        status_layout.addWidget(self.cache_info_label)
        self._update_cache_info()

        layout.addLayout(status_layout)

        # Action buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)

        # Loading cancel button (hidden by default)
        self.loading_cancel_btn = QPushButton("⛔ Cancel Loading")
        self.loading_cancel_btn.setObjectName("CancelLoadingButton")
        self.loading_cancel_btn.setToolTip("Cancel the current directory listing")
        self.loading_cancel_btn.setCursor(Qt.PointingHandCursor)
        self.loading_cancel_btn.clicked.connect(self._cancel_listing)
        self.loading_cancel_btn.setVisible(False)
        button_layout.addWidget(self.loading_cancel_btn)

        # Browse Local button - allow selecting local files
        self.browse_local_btn = QPushButton("📁 Browse Local")
        self.browse_local_btn.setToolTip("Browse local filesystem instead")
        self.browse_local_btn.setCursor(Qt.PointingHandCursor)
        self.browse_local_btn.clicked.connect(self._browse_local)
        button_layout.addWidget(self.browse_local_btn)

        button_layout.addStretch()

        # Go Into button - for navigating into directories in CASA mode
        self.go_into_btn = QPushButton("📂 Go Into")
        self.go_into_btn.setToolTip("Navigate into the selected directory")
        self.go_into_btn.setEnabled(False)
        self.go_into_btn.setCursor(Qt.PointingHandCursor)
        self.go_into_btn.clicked.connect(self._go_into_selected)
        if self.casa_mode:
            button_layout.addWidget(self.go_into_btn)

        self.cancel_btn = QPushButton("Close")
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)

        self.open_btn = QPushButton("Select" if self.casa_mode else "Open")
        self.open_btn.setObjectName("PrimaryButton")
        self.open_btn.setEnabled(False)
        self.open_btn.setDefault(True)
        self.open_btn.setCursor(Qt.PointingHandCursor)
        self.open_btn.clicked.connect(self._open_selected)
        button_layout.addWidget(self.open_btn)

        layout.addLayout(button_layout)

    def _apply_styles(self):
        """Apply modern styling to the dialog using theme_manager."""
        try:
            from ..styles import theme_manager
        except ImportError:
            from styles import theme_manager

        palette = theme_manager.palette
        is_dark = theme_manager.is_dark

        # Colors from palette
        window = palette["window"]
        base = palette["base"]
        surface = palette["surface"]
        surface_elevated = palette.get("surface_elevated", surface)
        border = palette["border"]
        border_light = palette.get("border_light", border)
        text = palette["text"]
        text_secondary = palette.get(
            "text_secondary", palette.get("disabled", "#888888")
        )
        highlight = palette["highlight"]
        highlight_hover = palette.get("highlight_hover", highlight)
        button = palette["button"]
        button_hover = palette["button_hover"]
        button_pressed = palette.get("button_pressed", button)
        disabled = palette.get("disabled", "#666666")
        shadow = palette.get("shadow", "rgba(0,0,0,0.2)")

        # Gradient for primary button
        gradient_start = palette.get("button_gradient_start", highlight)
        gradient_end = palette.get("button_gradient_end", highlight_hover)

        self.setStyleSheet(
            f"""
            /* Dialog background */
            QDialog {{
                background-color: {window};
            }}
            
            /* Header title */
            QLabel#DialogTitle {{
                font-size: 16pt;
                font-weight: 700;
                color: {text};
                padding: 4px 0;
            }}
            
            QLabel#ConnectionInfo {{
                font-size: 10pt;
                color: {text_secondary};
                padding: 4px 8px;
                background-color: {surface};
                border: 1px solid {border};
                border-radius: 6px;
            }}
            
            /* Breadcrumb widget */
            QWidget#BreadcrumbWidget {{
                background-color: {surface};
                border: 1px solid {border};
                border-radius: 6px;
                max-height: 28px;
            }}
            
            QWidget#BreadcrumbWidget QPushButton {{
                background-color: transparent;
                border: none;
                color: {text_secondary};
                font-size: 10pt;
                padding: 2px 4px;
                margin: 0px;
                border-radius: 4px;
                min-width: 0px;
                min-height: 0px;
            }}
            
            QWidget#BreadcrumbWidget QPushButton:hover {{
                background-color: {button_hover};
                color: {text};
            }}
            
            QWidget#BreadcrumbWidget QLabel {{
                color: {text_secondary};
                font-size: 10pt;
                padding: 0px 2px;
            }}
            
            /* Navigation buttons */
            QPushButton#NavButton {{
                background-color: {surface};
                border: 1px solid {border};
                border-radius: 8px;
                font-size: 14pt;
                padding: 0px;
            }}
            
            QPushButton#NavButton:hover {{
                background-color: {button_hover};
                border-color: {highlight};
            }}
            
            QPushButton#NavButton:pressed {{
                background-color: {button_pressed};
            }}
            
            QPushButton#NavButton:disabled {{
                color: {disabled};
                border-color: {border};
            }}
            
            /* Path input */
            QLineEdit#PathInput {{
                padding: 4px 10px;
                font-size: 10pt;
                border: 1px solid {border};
                border-radius: 6px;
                background-color: {base};
                color: {text};
            }}
            
            QLineEdit#PathInput:focus {{
                border-color: {highlight};
                border-width: 2px;
                background-color: {surface_elevated};
            }}
            
            /* File tree */
            QTreeWidget#FileTree {{
                border: 1px solid {border};
                border-radius: 10px;
                background-color: {base};
                color: {text};
                font-size: 11pt;
                outline: none;
            }}
            
            QTreeWidget#FileTree::item {{
                padding: 8px 6px;
                border-bottom: 1px solid {border_light};
            }}
            
            QTreeWidget#FileTree::item:selected {{
                background-color: {highlight};
                color: #ffffff;
                border-radius: 4px;
            }}
            
            QTreeWidget#FileTree::item:hover:!selected {{
                background-color: {button_hover};
            }}
            
            QHeaderView::section {{
                background-color: {surface};
                color: {text};
                font-weight: 600;
                font-size: 10pt;
                padding: 10px 8px;
                border: none;
                border-bottom: 2px solid {highlight};
            }}
            
            /* Options frame */
            QFrame#OptionsFrame {{
                background-color: {surface};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            
            QFrame#OptionsFrame QCheckBox {{
                font-size: 10pt;
                color: {text};
                spacing: 8px;
            }}
            
            QFrame#OptionsFrame QCheckBox::indicator {{
                width: 18px;
                height: 18px;
                border: 2px solid {border};
                border-radius: 4px;
                background-color: {base};
            }}
            
            QFrame#OptionsFrame QCheckBox::indicator:checked {{
                background-color: {highlight};
                border-color: {highlight};
            }}
            
            QFrame#OptionsFrame QCheckBox::indicator:hover {{
                border-color: {highlight};
            }}
            
            /* Progress frame */
            QFrame#ProgressFrame {{
                background-color: {surface};
                border: 1px solid {border};
                border-radius: 10px;
            }}
            
            QLabel#ProgressLabel {{
                font-size: 11pt;
                font-weight: 500;
                color: {text};
            }}
            
            QProgressBar#ModernProgressBar {{
                border: none;
                border-radius: 8px;
                background-color: {border};
                text-align: center;
                font-size: 10pt;
                font-weight: 600;
                color: {text};
                min-height: 20px;
            }}
            
            QProgressBar#ModernProgressBar::chunk {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {gradient_start}, stop:1 {gradient_end});
                border-radius: 8px;
            }}
            
            /* Status labels */
            QLabel#StatusLabel {{
                font-size: 10pt;
                color: {text_secondary};
            }}
            
            QLabel#CacheInfo {{
                font-size: 9pt;
                color: {text_secondary};
                padding: 2px 8px;
                background-color: {surface};
                border-radius: 4px;
            }}
            
            /* Action buttons */
            QPushButton {{
                padding: 5px 14px;
                font-size: 10pt;
                font-weight: 500;
                border: 1px solid {border};
                border-radius: 6px;
                background-color: {button};
                color: {text};
                min-width: 70px;
                min-height: 26px;
            }}
            
            QPushButton:hover {{
                background-color: {button_hover};
                border-color: {highlight};
            }}
            
            QPushButton:pressed {{
                background-color: {button_pressed};
            }}
            
            QPushButton:disabled {{
                color: {disabled};
                background-color: {surface};
                border-color: {border};
            }}
            
            /* Primary action button */
            QPushButton#PrimaryButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {gradient_start}, stop:1 {gradient_end});
                color: #ffffff;
                border: none;
                font-weight: 600;
                letter-spacing: 0.3px;
            }}
            
            QPushButton#PrimaryButton:hover {{
                background-color: {highlight_hover};
            }}
            
            QPushButton#PrimaryButton:disabled {{
                background-color: {disabled};
                color: {border_light};
            }}
            
            /* Cancel loading button */
            QPushButton#CancelLoadingButton {{
                background-color: transparent;
                border: 1px solid {palette.get('error', '#ef4444')};
                color: {palette.get('error', '#ef4444')};
                font-weight: 500;
            }}
            
            QPushButton#CancelLoadingButton:hover {{
                background-color: {palette.get('error', '#ef4444')};
                color: #ffffff;
            }}
        """
        )

    def _update_cache_info(self):
        """Update cache size display."""
        size, count = self.cache.get_cache_size()
        if count > 0:
            if size > 1024 * 1024 * 1024:
                size_str = f"{size / (1024**3):.1f} GB"
            elif size > 1024 * 1024:
                size_str = f"{size / (1024**2):.1f} MB"
            else:
                size_str = f"{size / 1024:.1f} KB"
            self.cache_info_label.setText(f"Cache: {count} files, {size_str}")
        else:
            self.cache_info_label.setText("")

    def _update_breadcrumbs(self):
        """Update the breadcrumb navigation bar with clickable path segments."""
        # Clear existing breadcrumbs
        while self.breadcrumb_layout.count():
            item = self.breadcrumb_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Split path into segments
        path = self.current_path.rstrip("/")
        if not path:
            path = "/"

        segments = path.split("/")

        # Build cumulative paths for each segment
        cumulative_path = ""
        for i, segment in enumerate(segments):
            if i == 0:
                # Root
                cumulative_path = "/"
                display_name = "🏠 /"
            else:
                cumulative_path = f"{cumulative_path.rstrip('/')}/{segment}"
                display_name = segment

            # Create clickable button
            btn = QPushButton(display_name)
            btn.setFlat(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton { text-align: left; padding: 2px 4px; } QPushButton:hover { background-color: palette(mid); }"
            )

            # Store path for this button
            target_path = cumulative_path
            btn.clicked.connect(lambda checked, p=target_path: self._navigate_to(p))

            self.breadcrumb_layout.addWidget(btn)

            # Add separator (except for last item)
            if i < len(segments) - 1:
                sep = QLabel(" › ")
                sep.setStyleSheet("color: gray;")
                self.breadcrumb_layout.addWidget(sep)

        # Add stretch to push everything left
        self.breadcrumb_layout.addStretch()

    def _load_initial_directory(self):
        """Navigate to last used directory or home if not available."""
        host = self.connection._host

        # Use last path if available
        if host in RemoteFileBrowser._last_paths:
            self._navigate_to(RemoteFileBrowser._last_paths[host])
        else:
            # Use cached home directory if available, otherwise start at root
            # This avoids a blocking get_home_directory() call
            if hasattr(self.connection, "_last_home") and self.connection._last_home:
                self._navigate_to(self.connection._last_home)
            else:
                # Start at root - non-blocking
                self._navigate_to("/")

    def _load_home_directory(self):
        """Navigate to home directory."""
        # Use cached home if available for instant navigation
        if hasattr(self.connection, "_last_home") and self.connection._last_home:
            self._navigate_to(self.connection._last_home)
        else:
            # Fetch home directory in background thread
            self._fetch_home_async()

    def _fetch_home_async(self):
        """Fetch home directory asynchronously."""
        from PyQt5.QtCore import QThread, pyqtSignal

        class HomeThread(QThread):
            result = pyqtSignal(str)

            def __init__(self, connection):
                super().__init__()
                self.connection = connection

            def run(self):
                try:
                    home = self.connection.get_home_directory()
                    self.result.emit(home)
                except:
                    self.result.emit("/")

        def on_home_fetched(home_path):
            self._navigate_to(home_path)

        self._home_thread = HomeThread(self.connection)
        self._home_thread.result.connect(on_home_fetched)
        self._home_thread.start()

    def _navigate_to(self, path: str):
        """Navigate to a specific directory."""
        self.current_path = path

        # Set flag to prevent autocomplete from triggering
        self._navigating = True
        self.path_edit.setText(path)
        self._navigating = False

        # Remember this path for next time
        host = self.connection._host
        RemoteFileBrowser._last_paths[host] = path

        # Update breadcrumb navigation
        self._update_breadcrumbs()

        self._refresh()

    def _go_up(self):
        """Navigate to parent directory."""
        parent = os.path.dirname(self.current_path.rstrip("/"))
        if not parent:
            parent = "/"
        self._navigate_to(parent)

    def _on_path_entered(self):
        """Handle manual path entry."""
        path = self.path_edit.text().strip()
        if path:
            self._navigate_to(path)

    def _on_path_text_changed(self, text: str):
        """Handle path text changes - trigger autocomplete with debounce."""
        # Don't autocomplete if text is empty or doesn't contain /
        if not text or "/" not in text:
            return

        # Don't trigger during path navigation (when we programmatically set the text)
        if hasattr(self, "_navigating") and self._navigating:
            return

        # Start/restart debounce timer
        if self._autocomplete_timer:
            self._autocomplete_timer.start(300)  # 300ms debounce

    def _fetch_autocomplete(self):
        """Fetch autocomplete suggestions from remote server."""
        text = self.path_edit.text().strip()
        if not text or not self.connection or not self.connection.is_connected():
            return

        # Parse text into parent directory and partial name
        if text.endswith("/"):
            parent_path = text.rstrip("/") or "/"
            partial_name = ""
        else:
            parent_path = os.path.dirname(text)
            partial_name = os.path.basename(text)
            if not parent_path:
                parent_path = "/"

        # Check cache first - use the existing _listing_cache
        cache_key = (
            self.connection._host,
            parent_path,
            True,
            False,
        )  # show_hidden=True, fits_only=False
        if cache_key in RemoteFileBrowser._listing_cache:
            import time

            entries, timestamp = RemoteFileBrowser._listing_cache[cache_key]
            if time.time() - timestamp < RemoteFileBrowser._cache_ttl:
                # Filter entries locally
                suggestions = []
                for entry in entries:
                    if entry.is_dir:
                        if not partial_name or entry.name.lower().startswith(
                            partial_name.lower()
                        ):
                            suggestions.append(entry.path)
                self._on_autocomplete_finished(suggestions)
                return

        # Cancel any existing autocomplete thread and wait for it
        if self._autocomplete_thread is not None:
            if self._autocomplete_thread.isRunning():
                try:
                    self._autocomplete_thread.finished.disconnect()
                except:
                    pass
                self._autocomplete_thread.cancel()
                # Wait briefly for thread to finish (non-blocking timeout)
                self._autocomplete_thread.wait(100)  # 100ms max wait
                # If still running after wait, let it finish in background
                # but don't start a new thread this cycle
                if self._autocomplete_thread.isRunning():
                    return
            # Clean up finished thread
            self._autocomplete_thread = None

        # Start new autocomplete thread
        self._autocomplete_thread = AutocompleteThread(
            self.connection,
            parent_path,
            partial_name,
        )
        self._autocomplete_thread.finished.connect(self._on_autocomplete_finished)
        self._autocomplete_thread.start()

    def _on_autocomplete_finished(self, suggestions: list):
        """Handle autocomplete results."""
        if not suggestions:
            return

        # Update the completer model
        self._autocomplete_model.setStringList(suggestions)

        # Show the completer popup if we have suggestions
        if len(suggestions) > 0 and self.path_edit.hasFocus():
            self._path_completer.complete()

    def _refresh(self, force_refresh: bool = False):
        """Refresh current directory listing asynchronously.

        Args:
            force_refresh: If True, bypass cache and fetch fresh data
        """
        # Check connection before attempting operations
        if not self.connection or not self.connection.is_connected():
            self.status_label.setText("⚠️ Not connected")
            return

        # Cancel any existing listing operation (don't wait - just mark cancelled)
        if self._list_thread and self._list_thread.isRunning():
            try:
                self._list_thread.finished.disconnect()
                self._list_thread.error.disconnect()
            except:
                pass
            self._list_thread.cancel()
            # Mark that we have a pending operation
            RemoteFileBrowser._has_pending_operation = True

        # If there was a pending operation from before and thread is done, refresh SFTP
        if RemoteFileBrowser._has_pending_operation:
            if self._list_thread is None or not self._list_thread.isRunning():
                try:
                    self.connection.refresh_sftp()
                    RemoteFileBrowser._has_pending_operation = False
                except:
                    pass  # Will try again next time

        # Check cache first (unless force refresh)
        if not force_refresh:
            cache_key = (
                self.connection._host,
                self.current_path,
                self.show_hidden_cb.isChecked(),
                self.fits_only_cb.isChecked(),
            )
            if cache_key in RemoteFileBrowser._listing_cache:
                entries, timestamp = RemoteFileBrowser._listing_cache[cache_key]
                import time

                if time.time() - timestamp < RemoteFileBrowser._cache_ttl:
                    # Use cached entries
                    self.tree.clear()
                    self._on_list_finished(entries, from_cache=True)
                    return

        self.tree.clear()
        self.status_label.setText("⏳ Loading...")

        # Disable UI during loading
        self._set_loading_state(True)

        # Start async listing
        self._list_thread = ListDirectoryThread(
            self.connection,
            self.current_path,
            show_hidden=self.show_hidden_cb.isChecked(),
            fits_only=self.fits_only_cb.isChecked(),
        )
        self._list_thread.finished.connect(self._on_list_finished)
        self._list_thread.error.connect(self._on_list_error)
        self._list_thread.start()

    def _set_loading_state(self, loading: bool):
        """Enable/disable UI elements during loading."""
        self.up_btn.setEnabled(not loading)
        self.home_btn.setEnabled(not loading)
        self.refresh_btn.setEnabled(not loading)
        self.path_edit.setEnabled(not loading)
        self.show_hidden_cb.setEnabled(not loading)
        self.fits_only_cb.setEnabled(not loading)
        self.open_btn.setEnabled(not loading and False)  # Also check selection

        # Show/update loading cancel button
        if loading:
            self.loading_cancel_btn.setVisible(True)
            self.status_label.setText("⏳ Loading... (click Cancel to abort)")
        else:
            self.loading_cancel_btn.setVisible(False)

    def _cancel_listing(self):
        """Cancel the current directory listing operation."""
        if self._list_thread and self._list_thread.isRunning():
            self._list_thread.cancel()
            self._list_thread.wait(1000)
            self._set_loading_state(False)
            self.status_label.setText("Cancelled")

    def reject(self):
        """Override reject to cancel any running operations before closing."""
        # Cancel listing thread if running
        if self._list_thread and self._list_thread.isRunning():
            # Disconnect signals so results are ignored
            try:
                self._list_thread.finished.disconnect()
                self._list_thread.error.disconnect()
            except:
                pass
            self._list_thread.cancel()
            # Don't wait - just let it finish in background

        # Cancel download thread if running
        if self._download_thread and self._download_thread.isRunning():
            try:
                self._download_thread.finished.disconnect()
                self._download_thread.error.disconnect()
                self._download_thread.progress.disconnect()
            except:
                pass
            # Don't wait - just let it finish in background
            RemoteFileBrowser._has_pending_operation = True

        super().reject()

    def _on_list_finished(self, entries: list, from_cache: bool = False):
        """Handle successful directory listing."""
        self._set_loading_state(False)

        # Store in cache if this was a fresh fetch
        if not from_cache:
            import time

            cache_key = (
                self.connection._host,
                self.current_path,
                self.show_hidden_cb.isChecked(),
                self.fits_only_cb.isChecked(),
            )
            RemoteFileBrowser._listing_cache[cache_key] = (entries, time.time())

        from datetime import datetime

        for entry in entries:
            item = QTreeWidgetItem()

            # Name with icon
            if entry.is_dir:
                item.setText(0, f"📁 {entry.name}")
            elif entry.is_fits:
                item.setText(0, f"🔭 {entry.name}")
            else:
                item.setText(0, f"📄 {entry.name}")

            # Size
            if entry.is_dir:
                item.setText(1, "<DIR>")
            else:
                size = entry.size
                if size > 1024 * 1024 * 1024:
                    item.setText(1, f"{size / (1024**3):.1f} GB")
                elif size > 1024 * 1024:
                    item.setText(1, f"{size / (1024**2):.1f} MB")
                elif size > 1024:
                    item.setText(1, f"{size / 1024:.1f} KB")
                else:
                    item.setText(1, f"{size} B")

            # Modified time
            mtime = datetime.fromtimestamp(entry.mtime)
            item.setText(2, mtime.strftime("%Y-%m-%d %H:%M"))

            # Store file info
            item.setData(0, Qt.UserRole, entry)

            self.tree.addTopLevelItem(item)

        cache_indicator = " (cached)" if from_cache else ""
        self.status_label.setText(f"{len(entries)} items{cache_indicator}")

    def _on_list_error(self, error_msg: str):
        """Handle directory listing error."""
        self._set_loading_state(False)
        self.status_label.setText(f"❌ Error: {error_msg}")
        QMessageBox.warning(self, "Error", f"Failed to list directory:\n{error_msg}")

    def _on_selection_changed(self):
        """Update UI when selection changes."""
        items = self.tree.selectedItems()
        if items:
            entry: RemoteFileInfo = items[0].data(0, Qt.UserRole)
            if entry is None:  # Safeguard against missing data
                self.open_btn.setEnabled(False)
                if self.casa_mode:
                    self.go_into_btn.setEnabled(False)
                return
            if self.casa_mode:
                # In CASA mode, enable Select for directories (CASA images are directories)
                self.open_btn.setEnabled(entry.is_dir)
                # Enable Go Into for any directory
                self.go_into_btn.setEnabled(entry.is_dir)
            else:
                # In FITS mode, enable for FITS files only
                self.open_btn.setEnabled(not entry.is_dir and entry.is_fits)
        else:
            self.open_btn.setEnabled(False)
            if self.casa_mode:
                self.go_into_btn.setEnabled(False)

    def _go_into_selected(self):
        """Navigate into the selected directory (CASA mode)."""
        items = self.tree.selectedItems()
        if not items:
            return

        entry: RemoteFileInfo = items[0].data(0, Qt.UserRole)
        if entry is None:  # Safeguard
            return
        if entry.is_dir:
            self._navigate_to(entry.path)

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int):
        """Handle double-click on item."""
        entry: RemoteFileInfo = item.data(0, Qt.UserRole)
        if entry is None:  # Safeguard
            return

        if entry.is_dir:
            self._navigate_to(entry.path)
        elif entry.is_fits:
            self._download_and_open(entry)

    def _browse_local(self):
        """Open native file dialog to browse local filesystem."""
        from PyQt5.QtWidgets import QFileDialog

        if self.casa_mode:
            # CASA mode: select directory
            local_path = QFileDialog.getExistingDirectory(
                self,
                "Select Local CASA Image Directory",
                os.path.expanduser("~"),
                QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
            )
        else:
            # FITS mode: select file
            local_path, _ = QFileDialog.getOpenFileName(
                self,
                "Select Local FITS File",
                os.path.expanduser("~"),
                "FITS Files (*.fits *.fts *.fit);;All Files (*)",
            )

        if local_path:
            self.fileSelected.emit(local_path)
            self.accept()

    def _open_selected(self):
        """Open the selected file or directory (in CASA mode)."""
        items = self.tree.selectedItems()
        if not items:
            return

        entry: RemoteFileInfo = items[0].data(0, Qt.UserRole)
        if entry is None:  # Safeguard
            return
        if self.casa_mode:
            if entry.is_dir:
                self._download_and_open(entry)
        else:
            if not entry.is_dir:
                self._download_and_open(entry)

    def _download_and_open(self, entry: RemoteFileInfo):
        """Download a file and emit the signal when ready."""
        # Check cache first
        cached_path = self.cache.get_cached_path(
            self.connection._host,
            entry.path,
            entry.mtime,
            entry.size,
        )

        if cached_path:
            self.status_label.setText(f"Using cached: {cached_path.name}")
            self.fileSelected.emit(str(cached_path))
            self.accept()
            return

        # Need to download
        local_path = self.cache.get_cache_path(
            self.connection._host,
            entry.path,
        )

        # Show progress
        self.progress_frame.show()
        self.progress_label.setText(f"Downloading {entry.name}...")
        self.progress_bar.setValue(0)
        self.open_btn.setEnabled(False)

        # Start download thread
        self._download_thread = DownloadThread(
            self.connection,
            entry.path,
            str(local_path),
            is_directory=entry.is_dir,
        )
        self._download_thread.progress.connect(self._on_download_progress)
        self._download_thread.finished.connect(self._on_download_finished)
        self._download_thread.error.connect(self._on_download_error)

        # Add to active downloads to prevent GC
        RemoteFileBrowser._active_downloads.append(self._download_thread)

        # Connect cleanup logic (independent of dialog instance)
        thread_ref = self._download_thread
        cleanup_lambda = lambda _: RemoteFileBrowser._remove_active_thread(thread_ref)
        self._download_thread.finished.connect(cleanup_lambda)
        self._download_thread.error.connect(cleanup_lambda)

        self._download_thread.start()

        # Store entry for marking cache
        self._current_download_entry = entry

    def _on_download_progress(self, transferred: int, total: int):
        """Update download progress (works for both files and directories)."""
        if total > 0:
            percent = int(100 * transferred / total)
            self.progress_bar.setValue(percent)

            if total > 1024 * 1024:
                self.progress_label.setText(
                    f"Downloading... {transferred / (1024**2):.1f} / {total / (1024**2):.1f} MB"
                )
            else:
                self.progress_label.setText(
                    f"Downloading... {transferred / 1024:.1f} / {total / 1024:.1f} KB"
                )

    def _on_download_finished(self, local_path: str):
        """Handle download completion."""
        self.progress_frame.hide()

        entry = self._current_download_entry

        # Mark as cached
        self.cache.mark_cached(
            self.connection._host,
            entry.path,
            Path(local_path),
            entry.mtime,
            entry.size,
        )

        self._update_cache_info()
        self.status_label.setText(f"Downloaded: {os.path.basename(local_path)}")

        # Emit signal and close
        self.fileSelected.emit(local_path)
        self.accept()

        # Remove from active list
        if self._download_thread in RemoteFileBrowser._active_downloads:
            RemoteFileBrowser._active_downloads.remove(self._download_thread)
            self._download_thread = None

    def _on_download_error(self, error_msg: str):
        """Handle download error."""
        self.progress_frame.hide()
        self.open_btn.setEnabled(True)
        self.status_label.setText(f"Error: {error_msg}")

        # Remove from active list
        if self._download_thread in RemoteFileBrowser._active_downloads:
            RemoteFileBrowser._active_downloads.remove(self._download_thread)
            self._download_thread = None

        QMessageBox.warning(
            self, "Download Error", f"Failed to download file: {error_msg}"
        )

    def closeEvent(self, event):
        """Handle dialog close - clean up threads without blocking."""
        # Cancel and cleanup download thread (non-blocking)
        if self._download_thread and self._download_thread.isRunning():
            try:
                # Disconnect UI callbacks specifically
                self._download_thread.finished.disconnect(self._on_download_finished)
                self._download_thread.error.disconnect(self._on_download_error)
                self._download_thread.progress.disconnect(self._on_download_progress)
            except:
                pass
            # Don't wait - let it finish in background
            RemoteFileBrowser._has_pending_operation = True

        # Cancel listing thread (non-blocking)
        if self._list_thread and self._list_thread.isRunning():
            try:
                self._list_thread.finished.disconnect()
                self._list_thread.error.disconnect()
            except:
                pass
            self._list_thread.cancel()

        # Cleanup home thread if running
        if (
            hasattr(self, "_home_thread")
            and self._home_thread
            and self._home_thread.isRunning()
        ):
            try:
                self._home_thread.result.disconnect()
            except:
                pass
            # Don't wait - let it finish

        # Cancel autocomplete thread if running
        if self._autocomplete_thread and self._autocomplete_thread.isRunning():
            try:
                self._autocomplete_thread.finished.disconnect()
            except:
                pass
            self._autocomplete_thread.cancel()
            self._autocomplete_thread.wait(500)  # Wait up to 500ms for clean shutdown

        # Stop autocomplete timer
        if self._autocomplete_timer:
            self._autocomplete_timer.stop()

        super().closeEvent(event)

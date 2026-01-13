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
)
from PyQt5.QtCore import Qt, pyqtSignal, QThread, QTimer
from PyQt5.QtGui import QIcon

from .ssh_manager import SSHConnection, SSHConnectionError, RemoteFileInfo
from .file_cache import RemoteFileCache


class DownloadThread(QThread):
    """Thread for downloading files without blocking UI."""
    
    progress = pyqtSignal(int, int)  # bytes_transferred, total_bytes
    finished = pyqtSignal(str)  # local_path
    error = pyqtSignal(str)  # error message
    
    def __init__(
        self,
        connection: SSHConnection,
        remote_path: str,
        local_path: str,
        is_directory: bool = False,
    ):
        super().__init__()
        self.connection = connection
        self.remote_path = remote_path
        self.local_path = local_path
        self.is_directory = is_directory
    
    def run(self):
        try:
            if self.is_directory:
                result = self.connection.download_directory(
                    self.remote_path,
                    self.local_path,
                    progress_callback=self._progress,
                )
            else:
                result = self.connection.download_file(
                    self.remote_path,
                    self.local_path,
                    progress_callback=self._progress,
                )
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))
    
    def _progress(self, transferred: int, total: int):
        self.progress.emit(transferred, total)


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
            # Create our own SFTP channel for this thread
            if self.connection._client:
                self._sftp = self.connection._client.open_sftp()
            else:
                raise SSHConnectionError("SSH client not connected")
            
            # List directory using our own SFTP channel
            import stat
            entries = []
            for attr in self._sftp.listdir_attr(self.path):
                if self._cancelled:
                    break
                    
                name = attr.filename
                
                # Skip hidden files if not requested
                if not self.show_hidden and name.startswith('.'):
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
        self.setMinimumSize(700, 500)
        self.setModal(True)
        
        self.connection = connection
        self.cache = cache or RemoteFileCache()
        self.current_path = "/"
        self._download_thread: Optional[DownloadThread] = None
        self._list_thread: Optional[ListDirectoryThread] = None
        
        self._setup_ui()
        self._apply_styles()
        
        # Load initial directory - use last path if available
        QTimer.singleShot(100, self._load_initial_directory)
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        
        # Path bar
        path_layout = QHBoxLayout()
        
        self.home_btn = QPushButton("🏠")
        self.home_btn.setFixedSize(32, 28)
        self.home_btn.setToolTip("Go to home directory")
        self.home_btn.clicked.connect(self._load_home_directory)
        path_layout.addWidget(self.home_btn)
        
        self.up_btn = QPushButton("⬆️")
        self.up_btn.setFixedSize(32, 28)
        self.up_btn.setToolTip("Go up one directory")
        self.up_btn.clicked.connect(self._go_up)
        path_layout.addWidget(self.up_btn)
        
        self.path_edit = QLineEdit()
        self.path_edit.returnPressed.connect(self._on_path_entered)
        path_layout.addWidget(self.path_edit)
        
        self.refresh_btn = QPushButton("🔄")
        self.refresh_btn.setFixedSize(32, 28)
        self.refresh_btn.setToolTip("Refresh directory")
        self.refresh_btn.clicked.connect(self._refresh)
        path_layout.addWidget(self.refresh_btn)
        
        layout.addLayout(path_layout)
        
        # File tree
        self.tree = QTreeWidget()
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
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        self.tree.setColumnWidth(1, 100)
        self.tree.setColumnWidth(2, 150)
        
        layout.addWidget(self.tree)
        
        # Options
        options_layout = QHBoxLayout()
        
        self.show_hidden_cb = QCheckBox("Show hidden files")
        self.show_hidden_cb.stateChanged.connect(self._refresh)
        options_layout.addWidget(self.show_hidden_cb)
        
        self.fits_only_cb = QCheckBox("FITS files only")
        self.fits_only_cb.setChecked(True)
        self.fits_only_cb.stateChanged.connect(self._refresh)
        options_layout.addWidget(self.fits_only_cb)
        
        options_layout.addStretch()
        
        layout.addLayout(options_layout)
        
        # Progress bar (hidden by default)
        self.progress_frame = QFrame()
        progress_layout = QVBoxLayout(self.progress_frame)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        
        self.progress_label = QLabel("Downloading...")
        progress_layout.addWidget(self.progress_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        progress_layout.addWidget(self.progress_bar)
        
        self.progress_frame.hide()
        layout.addWidget(self.progress_frame)
        
        # Status label
        self.status_label = QLabel("")
        layout.addWidget(self.status_label)
        
        # Buttons
        button_layout = QHBoxLayout()
        
        self.cache_info_label = QLabel("")
        button_layout.addWidget(self.cache_info_label)
        self._update_cache_info()
        
        # Loading cancel button (hidden by default)
        self.loading_cancel_btn = QPushButton("⛔ Cancel Loading")
        self.loading_cancel_btn.setToolTip("Cancel the current directory listing")
        self.loading_cancel_btn.clicked.connect(self._cancel_listing)
        self.loading_cancel_btn.setVisible(False)
        button_layout.addWidget(self.loading_cancel_btn)
        
        button_layout.addStretch()
        
        # Go Into button - for navigating into directories in CASA mode
        self.go_into_btn = QPushButton("📂 Go Into")
        self.go_into_btn.setToolTip("Navigate into the selected directory")
        self.go_into_btn.setEnabled(False)
        self.go_into_btn.clicked.connect(self._go_into_selected)
        if self.casa_mode:
            button_layout.addWidget(self.go_into_btn)
        
        self.cancel_btn = QPushButton("Close")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)
        
        self.open_btn = QPushButton("Select" if self.casa_mode else "Open")
        self.open_btn.setEnabled(False)
        self.open_btn.setDefault(True)
        self.open_btn.clicked.connect(self._open_selected)
        button_layout.addWidget(self.open_btn)
        
        layout.addLayout(button_layout)
    
    def _apply_styles(self):
        """Apply styling to the dialog using theme_manager for consistency."""
        try:
            from ..styles import theme_manager
        except ImportError:
            from styles import theme_manager
        
        palette = theme_manager.palette
        border = palette["border"]
        surface = palette["surface"]
        base = palette["base"]
        highlight = palette["highlight"]
        text = palette["text"]
        button = palette["button"]
        button_hover = palette["button_hover"]
        
        self.setStyleSheet(f"""
            QTreeWidget {{
                border: 1px solid {border};
                border-radius: 4px;
                background-color: {base};
                color: {text};
            }}
            QTreeWidget::item {{
                padding: 4px;
            }}
            QTreeWidget::item:selected {{
                background-color: {highlight};
                color: #ffffff;
            }}
            QTreeWidget::item:hover {{
                background-color: {button_hover};
            }}
            QLineEdit {{
                padding: 6px;
                border: 1px solid {border};
                border-radius: 4px;
                background-color: {base};
                color: {text};
            }}
            QLineEdit:focus {{
                border-color: {highlight};
                border-width: 2px;
            }}
            QPushButton {{
                padding: 6px 12px;
                border-radius: 4px;
                border: 1px solid {border};
                background-color: {button};
                color: {text};
            }}
            QPushButton:hover {{
                border-color: {highlight};
                background-color: {button_hover};
            }}
            QPushButton:disabled {{
                color: {palette['disabled']};
            }}
            QProgressBar {{
                border: 1px solid {border};
                border-radius: 4px;
                text-align: center;
                background-color: {surface};
            }}
            QProgressBar::chunk {{
                background-color: {highlight};
                border-radius: 3px;
            }}
            QLabel {{
                color: {text};
            }}
        """)
    
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
    
    def _load_initial_directory(self):
        """Navigate to last used directory or home if not available."""
        host = self.connection._host
        
        # Use last path if available, otherwise try to get home directory
        if host in RemoteFileBrowser._last_paths:
            self._navigate_to(RemoteFileBrowser._last_paths[host])
        else:
            # Get home directory - this is a quick call
            try:
                home = self.connection.get_home_directory()
                self._navigate_to(home)
            except:
                self._navigate_to("/")
    
    def _load_home_directory(self):
        """Navigate to home directory."""
        try:
            home = self.connection.get_home_directory()
            self._navigate_to(home)
        except SSHConnectionError as e:
            self._navigate_to("/")
    
    def _navigate_to(self, path: str):
        """Navigate to a specific directory."""
        self.current_path = path
        self.path_edit.setText(path)
        
        # Remember this path for next time
        host = self.connection._host
        RemoteFileBrowser._last_paths[host] = path
        
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
    
    def _refresh(self):
        """Refresh current directory listing asynchronously."""
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
    
    def _on_list_finished(self, entries: list):
        """Handle successful directory listing."""
        self._set_loading_state(False)
        
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
        
        self.status_label.setText(f"{len(entries)} items")
    
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
        if entry.is_dir:
            self._navigate_to(entry.path)
    
    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int):
        """Handle double-click on item."""
        entry: RemoteFileInfo = item.data(0, Qt.UserRole)
        
        if entry.is_dir:
            # Always navigate into directories on double-click
            # Use Select button to download CASA image directories
            self._navigate_to(entry.path)
        elif entry.is_fits:
            # Download and open FITS file
            self._download_and_open(entry)
    
    def _open_selected(self):
        """Open the selected file or directory (in CASA mode)."""
        items = self.tree.selectedItems()
        if not items:
            return
        
        entry: RemoteFileInfo = items[0].data(0, Qt.UserRole)
        if self.casa_mode:
            # In CASA mode, we want directories
            if entry.is_dir:
                self._download_and_open(entry)
        else:
            # In FITS mode, we want files
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
        self._download_thread.finished.connect(
            lambda path: self._on_download_finished(path, entry)
        )
        self._download_thread.error.connect(self._on_download_error)
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
    
    def _on_download_finished(self, local_path: str, entry: RemoteFileInfo):
        """Handle download completion."""
        self.progress_frame.hide()
        
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
    
    def _on_download_error(self, error_msg: str):
        """Handle download error."""
        self.progress_frame.hide()
        self.open_btn.setEnabled(True)
        self.status_label.setText(f"Error: {error_msg}")
        QMessageBox.warning(self, "Download Error", f"Failed to download file: {error_msg}")
    
    def closeEvent(self, event):
        """Handle dialog close."""
        # Cancel any ongoing download
        if self._download_thread and self._download_thread.isRunning():
            self._download_thread.terminate()
            self._download_thread.wait()
        super().closeEvent(event)

"""
Main-window remote (SSH) mode: connection, browser, status, cache.
"""

import os

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QMessageBox,
)

from ..ui.styles import set_hand_cursor


class RemoteModeMixin:
    """SSH connection, remote file browser, status indicator, health check and
    cache manager.

    Lives in core/ rather than remote/ so importing it doesn't pull in paramiko
    at startup.

    Mixed into :class:`SolarRadioImageViewerApp`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``browse_remote_act``, ``clear_cache_act``, ``connect_remote_act``,
    ``disconnect_remote_act``, ``_open_dialogs``, ``remote_status_btn``,
    ``statusBar``, ``tab_widget``
    """

    def show_remote_connection_dialog(self):
        """Show the SSH connection dialog."""
        try:
            from PyQt5.QtCore import Qt

            from ..remote import ConnectionDialog

            # Check if dialog is already open
            for dialog in self._open_dialogs:
                if isinstance(dialog, ConnectionDialog):
                    dialog.raise_()
                    dialog.activateWindow()
                    return

            dialog = ConnectionDialog(self)
            dialog.connectionEstablished.connect(self._on_remote_connected)

            # Clean up when closed
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            dialog.setModal(
                False
            )  # Override default modality set in ConnectionDialog.__init__
            dialog.destroyed.connect(
                lambda: (
                    self._open_dialogs.remove(dialog)
                    if dialog in self._open_dialogs
                    else None
                )
            )

            self._open_dialogs.append(dialog)
            dialog.show()

        except ImportError as e:
            QMessageBox.warning(
                self,
                "Missing Dependency",
                f"Remote mode requires paramiko. Install it with:\n\npip install paramiko\n\nError: {e}",
            )
        except Exception as e:
            QMessageBox.critical(
                self, "Error", f"Failed to open connection dialog: {e}"
            )

    def _on_remote_connected(self, connection):
        """Handle successful remote connection."""
        from ..remote import RemoteFileCache

        self.remote_connection = connection
        if self.remote_cache is None:
            self.remote_cache = RemoteFileCache()

        self._update_remote_menu_state()
        self._update_remote_status_indicator()

        # Start periodic health check
        self._start_connection_health_timer()

    def show_remote_file_browser(self):
        """Show the remote file browser dialog."""
        if not self.remote_connection or not self.remote_connection.is_connected():
            QMessageBox.warning(
                self, "Not Connected", "Please connect to a remote server first."
            )
            return

        try:
            from ..remote import RemoteFileBrowser

            browser = RemoteFileBrowser(
                self.remote_connection,
                cache=self.remote_cache,
                parent=self,
            )
            browser.fileSelected.connect(self._on_remote_file_selected)
            browser.exec_()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open remote browser: {e}")

    def _on_remote_file_selected(self, local_path: str):
        """Handle file selected from remote browser (now downloaded locally)."""
        if not local_path or not os.path.exists(local_path):
            return

        # Get the current tab and load the file
        current_tab = self.tab_widget.currentWidget()
        if current_tab:
            import time

            start_time = time.time()

            current_tab._reset_hpc_state()
            current_tab.imagename = local_path
            current_tab.dir_entry.setText(local_path)
            current_tab.contour_settings["contour_data"] = None
            current_tab.current_contour_wcs = None
            current_tab.figure.clear()
            current_tab.on_visualization_changed(dir_load=True)
            current_tab.update_tab_name_from_path(local_path)

            elapsed = time.time() - start_time
            basename = os.path.basename(local_path)
            self.statusBar().showMessage(
                f"Loaded remote file: {basename} ({elapsed:.2f}s)", 5000
            )

            current_tab._scan_directory_files()

    def disconnect_remote(self):
        """Disconnect from the remote server."""
        # Stop health check timer
        self._stop_connection_health_timer()

        if self.remote_connection:
            try:
                host_info = self.remote_connection.connection_info
                self.remote_connection.disconnect()
                self.statusBar().showMessage(f"Disconnected from {host_info}", 3000)
            except Exception as e:
                print(f"[WARNING] Error disconnecting: {e}")
            finally:
                self.remote_connection = None

        self._update_remote_menu_state()
        self._update_remote_status_indicator()

    def _update_remote_menu_state(self):
        """Update the enabled state of remote menu items."""
        connected = (
            self.remote_connection is not None and self.remote_connection.is_connected()
        )

        # Update menu actions
        if hasattr(self, "connect_remote_act"):
            self.connect_remote_act.setEnabled(not connected)
            if connected:
                self.connect_remote_act.setText(
                    f"🌐 Connected: {self.remote_connection.connection_info}"
                )
            else:
                self.connect_remote_act.setText("🌐 Connect to Remote Server...")

        if hasattr(self, "browse_remote_act"):
            self.browse_remote_act.setVisible(connected)

        if hasattr(self, "disconnect_remote_act"):
            self.disconnect_remote_act.setVisible(connected)

    def _update_remote_status_indicator(self):
        """Update the permanent status bar indicator for remote connection."""
        if hasattr(self, "remote_status_btn"):
            connected = self.remote_connection and self.remote_connection.is_connected()
            if connected:
                self.remote_status_btn.setText(
                    f"🌐 {self.remote_connection.connection_info}"
                )
                self.remote_status_btn.setToolTip(
                    "Click to disconnect from remote server"
                )
            else:
                self.remote_status_btn.setText("🔌 Not Connected")
                self.remote_status_btn.setToolTip("Click to connect to a remote server")
            self._apply_remote_status_style(connected)

    def _apply_remote_status_style(self, connected: bool):
        """Apply styling to the remote status button based on connection state."""
        if connected:
            self.remote_status_btn.setStyleSheet("""
                QPushButton {
                    color: #4CAF50;
                    font-weight: bold;
                    padding: 4px 10px;
                    margin-right: 5px;
                    background-color: rgba(76, 175, 80, 0.15);
                    border: 1px solid #4CAF50;
                    border-radius: 4px;
                    min-height: 16px;
                }
                QPushButton:hover {
                    background-color: rgba(244, 67, 54, 0.15);
                    border-color: #F44336;
                    color: #F44336;
                }
            """)
        else:
            self.remote_status_btn.setStyleSheet("""
                QPushButton {
                    color: #9E9E9E;
                    font-weight: bold;
                    padding: 4px 10px;
                    margin-right: 5px;
                    background-color: transparent;
                    border: 1px solid #757575;
                    border-radius: 4px;
                    min-height: 16px;
                }
                QPushButton:hover {
                    background-color: rgba(33, 150, 243, 0.15);
                    border-color: #2196F3;
                    color: #2196F3;
                }
            """)

    def _on_remote_status_clicked(self):
        """Handle click on the remote status button."""
        if self.remote_connection and self.remote_connection.is_connected():
            # Already connected - disconnect
            self.disconnect_remote()
        else:
            # Not connected - show connection dialog
            self.show_remote_connection_dialog()

    def _start_connection_health_timer(self):
        """Start a timer to periodically check connection health."""
        if not hasattr(self, "_health_check_timer"):
            from PyQt5.QtCore import QTimer

            self._health_check_timer = QTimer(self)  # Parent to self
            self._health_check_timer.timeout.connect(self._check_connection_health)

        # Check every 5 seconds
        self._health_check_timer.start(5000)

    def _stop_connection_health_timer(self):
        """Stop the connection health check timer."""
        if hasattr(self, "_health_check_timer"):
            self._health_check_timer.stop()

    def show_cache_manager(self):
        """Show cache management dialog with options to clear different cache types."""
        import shutil
        from pathlib import Path

        from PyQt5.QtWidgets import (
            QCheckBox,
            QDialog,
            QGroupBox,
            QHBoxLayout,
            QLabel,
            QPushButton,
            QVBoxLayout,
        )

        # Check if dialog is already open
        if hasattr(self, "_cache_manager_dialog") and self._cache_manager_dialog:
            self._cache_manager_dialog.raise_()
            self._cache_manager_dialog.activateWindow()
            return

        dialog = QDialog(self)
        self._cache_manager_dialog = dialog  # Keep reference
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.finished.connect(lambda: setattr(self, "_cache_manager_dialog", None))

        dialog.setWindowTitle("Cache Manager")
        set_hand_cursor(dialog)
        dialog.setMinimumWidth(500)
        layout = QVBoxLayout(dialog)

        # Header
        header = QLabel("<h3>🗑️ Cache Manager</h3>")
        header.setStyleSheet("color: #2196F3;")
        layout.addWidget(header)

        desc = QLabel("Select which caches to clear. This action cannot be undone.")
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #666; margin-bottom: 10px;")
        layout.addWidget(desc)

        # Calculate cache sizes
        cache_info = []

        # 1. Remote Cache
        remote_size = 0
        remote_count = 0
        try:
            if self.remote_cache is None:
                from ..remote import RemoteFileCache

                self.remote_cache = RemoteFileCache()
            remote_size, remote_count = self.remote_cache.get_cache_size()
        except Exception:
            pass
        cache_info.append(
            (
                "remote",
                "📁 Remote Files Cache",
                remote_size,
                remote_count,
                "Downloaded files from remote SSH servers",
            )
        )

        # 2. NOAA Events Cache
        noaa_size = 0
        noaa_count = 0
        try:
            noaa_cache_dir = Path.home() / ".cache" / "solarviewer" / "noaa_events"
            if noaa_cache_dir.exists():
                for f in noaa_cache_dir.rglob("*"):
                    if f.is_file():
                        noaa_size += f.stat().st_size
                        noaa_count += 1
        except Exception:
            pass
        cache_info.append(
            (
                "noaa",
                "🌞 Solar Activity Cache",
                noaa_size,
                noaa_count,
                "Solar events, active regions, conditions, and spectrographs",
            )
        )

        # 3. Helioviewer / API JSON Cache
        json_size = 0
        json_count = 0
        try:
            root_cache = Path.home() / ".cache" / "solarviewer"
            if root_cache.exists():
                for f in root_cache.glob("*.json"):
                    if f.is_file():
                        json_size += f.stat().st_size
                        json_count += 1
        except Exception:
            pass
        if json_size > 0:
            cache_info.append(
                (
                    "json",
                    "📄 API Metadata Cache",
                    json_size,
                    json_count,
                    "Cached JSON responses from Helioviewer and other APIs",
                )
            )

        # 4. Sunpy Data
        sunpy_size = 0
        sunpy_count = 0
        try:
            # Check both ~/sunpy and ~/.sunpy
            for p in [Path.home() / "sunpy", Path.home() / ".sunpy"]:
                if p.exists():
                    for f in p.rglob("*"):
                        if f.is_file():
                            sunpy_size += f.stat().st_size
                            sunpy_count += 1
        except Exception:
            pass
        if sunpy_size > 0:
            cache_info.append(
                (
                    "sunpy",
                    "🛰️ Sunpy Data Cache",
                    sunpy_size,
                    sunpy_count,
                    "Downloaded AIA pointing tables, .nc files, etc.",
                )
            )

        # 5. Old solarviewer_cache (if exists)
        old_cache_size = 0
        old_cache_count = 0
        try:
            old_cache_dir = Path.home() / ".solarviewer_cache"
            if old_cache_dir.exists():
                for f in old_cache_dir.rglob("*"):
                    if f.is_file():
                        old_cache_size += f.stat().st_size
                        old_cache_count += 1
        except Exception:
            pass
        if old_cache_size > 0:
            cache_info.append(
                (
                    "old",
                    "📦 Legacy Cache",
                    old_cache_size,
                    old_cache_count,
                    "Old cache location (~/.solarviewer_cache)",
                )
            )

        # Create checkboxes for each cache type
        checkboxes = {}
        total_size = 0

        for key, name, size, count, description in cache_info:
            if size > 0:  # Only show categories that have data
                group = QGroupBox()
                group.setStyleSheet(
                    "QGroupBox { border: 1px solid #444; border-radius: 4px; padding: 10px; margin-top: 8px; }"
                )
                group_layout = QVBoxLayout(group)

                # Checkbox with name and size
                cb = QCheckBox(f"{name} ({self._format_size(size)}, {count} files)")
                cb.setChecked(False)
                checkboxes[key] = cb
                group_layout.addWidget(cb)

                # Description
                desc_label = QLabel(description)
                desc_label.setStyleSheet(
                    "color: #888; font-size: 11px; margin-left: 20px;"
                )
                group_layout.addWidget(desc_label)

                layout.addWidget(group)
                total_size += size

        if not checkboxes:
            no_cache = QLabel("All caches are currently empty.")
            no_cache.setStyleSheet("color: #888; font-style: italic; margin: 20px;")
            no_cache.setAlignment(Qt.AlignCenter)
            layout.addWidget(no_cache)
        else:
            # Total size
            total_label = QLabel(f"<b>Total: {self._format_size(total_size)}</b>")
            total_label.setStyleSheet("margin-top: 10px; font-size: 14px;")
            layout.addWidget(total_label)

        # Buttons
        btn_layout = QHBoxLayout()

        select_all_btn = QPushButton("Select All")
        select_all_btn.clicked.connect(
            lambda: [
                cb.setChecked(True) for cb in checkboxes.values() if cb.isEnabled()
            ]
        )
        btn_layout.addWidget(select_all_btn)

        btn_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)

        clear_btn = QPushButton("🗑️ Clear Selected")
        clear_btn.setStyleSheet(
            "background-color: #F44336; color: white; font-weight: bold; padding: 6px 16px;"
        )

        def do_clear():
            # Get list of selected cache keys
            selected_keys = [k for k, cb in checkboxes.items() if cb.isChecked()]

            if not selected_keys:
                self.statusBar().showMessage(
                    "⚠️ No cache categories selected to clear", 5000
                )
                return

            # Confirm before clearing
            reply = QMessageBox.question(
                self,
                "Confirm Clear",
                f"Are you sure you want to clear the selected caches ({len(selected_keys)})?\n\nThis action cannot be undone.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )

            if reply != QMessageBox.Yes:
                return

            cleared = []

            if "remote" in selected_keys:
                try:
                    if self.remote_cache:
                        self.remote_cache.clear_cache()
                    else:
                        from ..remote import RemoteFileCache

                        RemoteFileCache().clear_cache()

                    from ..remote import RemoteFileBrowser

                    if hasattr(RemoteFileBrowser, "_listing_cache"):
                        RemoteFileBrowser._listing_cache.clear()
                    cleared.append("Remote Files")
                except Exception as e:
                    print(f"[Cache] Error clearing remote: {e}")

            if "noaa" in selected_keys:
                try:
                    noaa_cache_dir = (
                        Path.home() / ".cache" / "solarviewer" / "noaa_events"
                    )
                    if noaa_cache_dir.exists():
                        shutil.rmtree(noaa_cache_dir)
                    cleared.append("Solar Activity")
                except Exception as e:
                    print(f"[Cache] Error clearing NOAA: {e}")

            if "json" in selected_keys:
                try:
                    root_cache = Path.home() / ".cache" / "solarviewer"
                    if root_cache.exists():
                        for f in root_cache.glob("*.json"):
                            f.unlink()
                    cleared.append("API Metadata")
                except Exception as e:
                    print(f"[Cache] Error clearing JSON: {e}")

            if "sunpy" in selected_keys:
                try:
                    for p in [Path.home() / "sunpy", Path.home() / ".sunpy"]:
                        if p.exists():
                            shutil.rmtree(p)
                    cleared.append("Sunpy Data")
                except Exception as e:
                    print(f"[Cache] Error clearing Sunpy: {e}")

            if "old" in selected_keys:
                try:
                    old_cache_dir = Path.home() / ".solarviewer_cache"
                    if old_cache_dir.exists():
                        shutil.rmtree(old_cache_dir)
                    cleared.append("Legacy Cache")
                except Exception as e:
                    print(f"[Cache] Error clearing legacy: {e}")

            if cleared:
                self.statusBar().showMessage(f"🗑️ Cleared: {', '.join(cleared)}", 5000)
                QMessageBox.information(
                    self,
                    "Cache Cleared",
                    "Successfully cleared:\n• " + "\n• ".join(cleared),
                )
                self._update_cache_menu_item()
                dialog.accept()
            else:
                QMessageBox.warning(self, "Error", "Failed to clear selected caches.")

        clear_btn.clicked.connect(do_clear)
        btn_layout.addWidget(clear_btn)

        layout.addLayout(btn_layout)
        dialog.show()  # Non-modal
        dialog.raise_()
        dialog.activateWindow()

    def _format_size(self, size_bytes: int) -> str:
        """Format bytes to human readable string."""
        if size_bytes == 0:
            return "0 B"

        power = 1024
        n = 0
        power_labels = {0: "", 1: "K", 2: "M", 3: "G", 4: "T"}
        while size_bytes >= power:
            size_bytes /= power
            n += 1

        return f"{size_bytes:.1f} {power_labels[n]}B"

    def _update_cache_menu_item(self):
        """Update the Manage Cache menu item with current total cache size."""
        if not hasattr(self, "clear_cache_act"):
            return

        try:
            from pathlib import Path

            total_size = 0

            # 1. Main solarviewer cache (~/.cache/solarviewer)
            # This includes remote files, noaa events, and json metadata
            try:
                root_cache = Path.home() / ".cache" / "solarviewer"
                if root_cache.exists():
                    for f in root_cache.rglob("*"):
                        if f.is_file():
                            total_size += f.stat().st_size
            except Exception:
                pass

            # 2. Sunpy cache
            try:
                for p in [Path.home() / "sunpy", Path.home() / ".sunpy"]:
                    if p.exists():
                        for f in p.rglob("*"):
                            if f.is_file():
                                total_size += f.stat().st_size
            except Exception:
                pass

            # 3. Legacy cache
            try:
                old_cache = Path.home() / ".solarviewer_cache"
                if old_cache.exists():
                    for f in old_cache.rglob("*"):
                        if f.is_file():
                            total_size += f.stat().st_size
            except Exception:
                pass

            size_str = self._format_size(total_size)
            self.clear_cache_act.setText(f"🗑️ Manage Cache ({size_str})")

        except Exception as e:
            # Fallback if something goes wrong
            print(f"[WARNING] Failed to update cache size: {e}")
            self.clear_cache_act.setText("🗑️ Manage Cache...")

    def _check_connection_health(self):
        """Periodically check connection health and auto-reconnect if needed."""
        # print("[SSH] Health check running...")  # Debug

        if not self.remote_connection:
            # print("[SSH] Health check: No remote connection")
            return

        # Run ping in background thread to avoid blocking UI
        from PyQt5.QtCore import QThread, pyqtSignal

        # If previous ping is still running after 10s, assume connection is dead
        if (
            hasattr(self, "_ping_thread")
            and self._ping_thread
            and self._ping_thread.isRunning()
        ):
            if hasattr(self, "_ping_start_time"):
                import time

                elapsed = time.time() - self._ping_start_time
                if elapsed > 10:
                    # print(f"[SSH] Health check: Ping stuck for {elapsed:.1f}s - connection appears dead")
                    # Connection is dead - the ping is stuck
                    try:
                        self._ping_thread.terminate()
                        self._ping_thread.wait(1000)
                    except:
                        pass
                    # Trigger reconnect
                    if self.remote_connection:
                        self.remote_connection._connected = False
                        self.statusBar().showMessage(
                            "⚠️ Remote connection lost - click status to reconnect",
                            10000,
                        )
                        self._update_remote_status_indicator()
                        self._update_remote_menu_state()
                    return
            # print("[SSH] Health check: Previous ping still running, skipping")
            return

        class PingThread(QThread):
            result = pyqtSignal(bool)

            def __init__(self, connection):
                super().__init__()
                self.connection = connection

            def run(self):
                import socket

                try:
                    # Quick passive check first
                    if not self.connection._connected or not self.connection._client:
                        self.result.emit(False)
                        return

                    transport = self.connection._client.get_transport()
                    if transport is None or not transport.is_active():
                        self.result.emit(False)
                        return

                    # Set socket timeout temporarily
                    sock = transport.sock
                    old_timeout = sock.gettimeout()
                    sock.settimeout(3.0)  # 3 second timeout

                    try:
                        # Try to open a new SFTP channel - this requires server response
                        # If network is down, this will timeout
                        sftp = self.connection._client.open_sftp()
                        sftp.close()  # Close it immediately
                        self.result.emit(True)
                    except socket.timeout:
                        print("[SSH] Ping timeout - connection appears dead")
                        self.result.emit(False)
                    except Exception as e:
                        print(f"[SSH] Ping failed: {e}")
                        self.result.emit(False)
                    finally:
                        sock.settimeout(old_timeout)

                except Exception as e:
                    print(f"[SSH] Ping error: {e}")
                    self.result.emit(False)

        def on_ping_result(is_alive):
            if not is_alive:
                print("[SSH] Health check: Connection lost, attempting reconnect...")
                if self.remote_connection and self.remote_connection.ensure_connected():
                    print("[SSH] Health check: Reconnected successfully")
                    self.statusBar().showMessage("🔄 Remote connection restored", 5000)
                else:
                    print("[SSH] Health check: Reconnect failed")
                    self.statusBar().showMessage(
                        "⚠️ Remote connection lost - click status to reconnect", 10000
                    )

                self._update_remote_status_indicator()
                self._update_remote_menu_state()

        # print("[SSH] Health check: Pinging connection in background...")
        import time

        self._ping_start_time = time.time()
        self._ping_thread = PingThread(self.remote_connection)
        self._ping_thread.result.connect(on_ping_result)
        self._ping_thread.start()

"""
Main-window remote (SSH) mode: connection, browser, status, cache.
"""

import logging
import os
import shutil
import threading
import traceback
from pathlib import Path

from PyQt5.QtCore import QObject, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QMessageBox,
)

from ..ui.activity import track_thread
from ..ui.background import Cancelled, run_in_thread
from ..ui.errors import show_error
from ..ui.styles import set_hand_cursor, set_tone

logger = logging.getLogger(__name__)

# The caches: walking them can take a while (many files, a slow disk), and
# clearing them longer, so these run on a thread. Nothing here touches Qt.


# Seconds the health check waits after its 1st, 2nd... failed try to
# connect again before the next (the last one from then on): a server
# that is down, or a laptop off the network, is not asked every 5 s
RECONNECT_WAITS = (10, 20, 40, 80, 160, 300)


def _wait_text(seconds):
    return f"{seconds} s" if seconds < 120 else f"{seconds // 60} min"


def _folder_size(folder, pattern="*"):
    """(bytes, files) in ``folder``, below it too, of names like ``pattern``."""
    size = count = 0
    if folder.exists():
        for f in folder.rglob(pattern):
            if f.is_file():
                size += f.stat().st_size
                count += 1
    return size, count


def _cache_root():
    return Path.home() / ".cache" / "solarviewer"


def _pieces_dir():
    return _cache_root() / "remote" / "pieces"


def _masks_dir():
    return _cache_root() / "dynspec_masks"


def _sunpy_dirs():
    # Check both ~/sunpy and ~/.sunpy
    return [Path.home() / "sunpy", Path.home() / ".sunpy"]


def _legacy_dir():
    return Path.home() / ".solarviewer_cache"


def _helioviewer_dir():
    """The Helioviewer browser's frames and FITS files (under ``$XDG_CACHE_HOME`` when that is set)."""
    from .. import helioviewer_api

    return helioviewer_api.cache_root()


def _helioviewer_apart():
    """Whether the Helioviewer folder lies outside the main cache (counted on its own then)."""
    try:
        _helioviewer_dir().relative_to(_cache_root())
    except ValueError:
        return True
    return False


def _measure_caches(remote_cache):
    """[(key, name, bytes, files, description)] of each cache."""

    def size_of(*folders, pattern="*"):
        size = count = 0
        try:
            for folder in folders:
                found = _folder_size(folder, pattern)
                size += found[0]
                count += found[1]
        except Exception:
            pass
        return size, count

    try:
        remote_size, remote_count = remote_cache.get_cache_size()
    except Exception:
        remote_size = remote_count = 0
    cache_info = [
        (
            "remote",
            "Remote Files Cache",
            remote_size,
            remote_count,
            "Downloaded files from remote SSH servers",
        ),
        # Slices of dynamic spectra read from servers, and their masks
        (
            "pieces",
            "Dynamic Spectrum Slices",
            *size_of(_pieces_dir()),
            "Parts of dynamic spectra read from servers (read again when needed)",
        ),
        (
            "masks",
            "Dynamic Spectrum Masks",
            *size_of(_masks_dir(), pattern="*.json"),
            "Masks drawn on dynamic spectra (clearing forgets them)",
        ),
        (
            "noaa",
            "Solar Activity Cache",
            *size_of(_cache_root() / "noaa_events"),
            "Solar events, active regions, conditions, and spectrographs",
        ),
    ]

    # The Helioviewer browser's frames (PNG with a JSON sidecar each) and FITS files
    helioviewer_size, helioviewer_count = size_of(_helioviewer_dir())
    if helioviewer_size > 0:
        cache_info.append(
            (
                "helioviewer",
                "Helioviewer Frames",
                helioviewer_size,
                helioviewer_count,
                "Images from the Helioviewer browser, kept to 2 GB (downloaded again when needed)",
            )
        )

    sunpy_size, sunpy_count = size_of(*_sunpy_dirs())
    if sunpy_size > 0:
        cache_info.append(
            (
                "sunpy",
                "Sunpy Data Cache",
                sunpy_size,
                sunpy_count,
                "Everything in ~/sunpy and ~/.sunpy, SunPy's own folders: AIA pointing "
                "tables, .nc files, and any data downloaded with SunPy outside SolarViewer",
            )
        )

    # Old solarviewer_cache (if exists)
    old_cache_size, old_cache_count = size_of(_legacy_dir())
    if old_cache_size > 0:
        cache_info.append(
            (
                "old",
                "Legacy Cache",
                old_cache_size,
                old_cache_count,
                "Old cache location (~/.solarviewer_cache)",
            )
        )
    return cache_info


# Each cache, as named once cleared
_CLEARED_NAMES = {
    "remote": "Remote Files",
    "pieces": "Dynamic Spectrum Slices",
    "masks": "Dynamic Spectrum Masks",
    "noaa": "Solar Activity",
    "helioviewer": "Helioviewer Frames",
    "sunpy": "Sunpy Data",
    "old": "Legacy Cache",
}


def _sunpy_folders_text(format_size):
    """The SunPy folders that clearing deletes, a line each with what is
    in it ("" if there are none), for the question asked first."""
    lines = []
    for folder in _sunpy_dirs():
        if not folder.exists():
            continue
        try:
            size, count = _folder_size(folder)
            held = f"{format_size(size)}, {count} file{'s' * (count != 1)}"
        except OSError:
            held = "size unknown"
        lines.append(f"• {folder}  ({held})")
    return "\n".join(lines)


def _clear_cache(key, remote_cache):
    """Clear the cache ``key``; True if that went well."""
    try:
        if key == "remote":
            remote_cache.clear_cache()
        else:
            folders = {
                "pieces": [_pieces_dir()],
                "masks": [_masks_dir()],
                "noaa": [_cache_root() / "noaa_events"],
                "helioviewer": [_helioviewer_dir()],
                "sunpy": _sunpy_dirs(),
                "old": [_legacy_dir()],
            }[key]
            for folder in folders:
                if folder.exists():
                    shutil.rmtree(folder)
        return True
    except Exception as e:
        logger.error(f"Error clearing the {key} cache: {e}")
        return False


def _total_cache_size():
    """Bytes in all the caches."""
    total_size = 0
    # 1. Main solarviewer cache (~/.cache/solarviewer): this includes remote
    # files, noaa events, and the Helioviewer frames unless $XDG_CACHE_HOME
    # puts them elsewhere. 2. Sunpy cache. 3. Legacy cache.
    folders = [_cache_root(), *_sunpy_dirs(), _legacy_dir()]
    if _helioviewer_apart():
        folders.append(_helioviewer_dir())
    for folder in folders:
        try:
            total_size += _folder_size(folder)[0]
        except Exception:
            pass
    return total_size


def _disconnect(connection):
    """Close ``connection``, on a thread: closing waits on the network."""
    try:
        connection.disconnect()
    except Exception as e:
        logger.warning(f"Error disconnecting: {e}")


class _Measured(QObject):
    """Brings the size found on a thread back to the window."""

    done = pyqtSignal(object)  # bytes (Qt's int stops at 2 GB), or None


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
            show_error(
                self,
                "Missing dependency",
                "Remote mode requires paramiko. Install it with:\n\npip install paramiko",
                str(e),
            )
        except Exception:
            show_error(
                self,
                "Could not open the connection dialog",
                "The window could not be opened. Try again; the details say what went wrong.",
                traceback.format_exc(),
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
        """Show the remote file browser dialog (a connection lost since can
        be made again from it)."""
        if not self.remote_connection:
            QMessageBox.warning(
                self, "Not connected", "Please connect to a remote server first."
            )
            return

        try:
            from ..remote import RemoteFileBrowser

            # Either kind of image: a FITS file or a CASA image folder
            browser = RemoteFileBrowser(
                self.remote_connection,
                cache=self.remote_cache,
                parent=self,
                casa_mode=None,
                can_switch_server=True,
            )
            browser.fileSelected.connect(
                lambda local_path, is_remote: self._on_remote_file_selected(
                    local_path, is_remote, browser
                )
            )
            browser.serverChanged.connect(self._on_remote_server_changed)
            browser.exec_()
            # (it may have reconnected)
            self._update_remote_menu_state()
            self._update_remote_status_indicator()

        except Exception:
            show_error(
                self,
                "Could not open the remote browser",
                "The window could not be opened. Check the connection and try again.",
                traceback.format_exc(),
            )

    def _on_remote_server_changed(self, connection):
        """The remote browser connected to another server: it replaces the
        one connected to."""
        old = self.remote_connection
        if old is not None and old is not connection:
            self._stop_connection_health_timer()
            threading.Thread(target=_disconnect, args=(old,), daemon=True).start()
        self._on_remote_connected(connection)

    def _on_remote_file_selected(self, local_path: str, is_remote=False, browser=None):
        """Handle file selected from remote browser (now downloaded locally)."""
        if not local_path or not os.path.exists(local_path):
            return
        from ..processing.fits_io import SPECTRUM, fits_kind

        if fits_kind(local_path) == SPECTRUM:
            self.open_spectrum(local_path)
            return

        # Into the active pane (as its own Open)
        current_tab = self.tab_widget.currentWidget()
        if current_tab:
            folder = browser.current_path if browser is not None else None
            current_tab.show_remote_image(self, local_path, is_remote and browser is not None, folder)

    def disconnect_remote(self):
        """Disconnect from the remote server."""
        # Stop health check timer
        self._stop_connection_health_timer()

        if self.remote_connection:
            host_info = self.remote_connection.connection_info
            threading.Thread(
                target=_disconnect, args=(self.remote_connection,), daemon=True
            ).start()
            self.statusBar().showMessage(f"Disconnected from {host_info}", 3000)
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
                    f"Connected: {self.remote_connection.connection_info}"
                )
            else:
                self.connect_remote_act.setText("Connect…")

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
                    self.remote_connection.connection_info
                )
                self.remote_status_btn.setToolTip(
                    "Click to disconnect from remote server"
                )
            else:
                self.remote_status_btn.setText("Not connected")
                self.remote_status_btn.setToolTip("Click to connect to a remote server")
            self.remote_status_btn.set_on(connected)

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

        # (a new connection: nothing has failed yet)
        self._reconnect_failures = 0
        self._reconnect_not_before = 0.0
        # Check every 5 seconds
        self._health_check_timer.start(5000)

    def _stop_connection_health_timer(self):
        """Stop the connection health check timer."""
        if hasattr(self, "_health_check_timer"):
            self._health_check_timer.stop()

    def show_cache_manager(self):
        """Show cache management dialog with options to clear different cache types."""
        from PyQt5.QtWidgets import (
            QCheckBox,
            QDialog,
            QFrame,
            QHBoxLayout,
            QLabel,
            QPushButton,
            QVBoxLayout,
        )

        from ..ui.widgets import dialog_layout

        # Check if dialog is already open
        if hasattr(self, "_cache_manager_dialog") and self._cache_manager_dialog:
            self._cache_manager_dialog.raise_()
            self._cache_manager_dialog.activateWindow()
            return

        # Calculate cache sizes
        if self.remote_cache is None:
            from ..remote import RemoteFileCache

            self.remote_cache = RemoteFileCache()
        try:
            cache_info = run_in_thread(
                self, "Measuring the caches…", _measure_caches, self.remote_cache
            )
        except Cancelled:
            self.statusBar().showMessage("Cancelled", 3000)
            return

        dialog = QDialog(self)
        self._cache_manager_dialog = dialog  # Keep reference
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.finished.connect(lambda: setattr(self, "_cache_manager_dialog", None))

        dialog.setWindowTitle("Cache manager")
        set_hand_cursor(dialog)
        dialog.setMinimumWidth(500)
        layout = dialog_layout(dialog)

        desc = QLabel("Select which caches to clear. This action cannot be undone.")
        desc.setWordWrap(True)
        desc.setObjectName("Hint")
        layout.addWidget(desc)

        # Create checkboxes for each cache type
        checkboxes = {}
        total_size = 0

        for key, name, size, count, description in cache_info:
            if size > 0:  # Only show categories that have data
                group = QFrame()
                group.setObjectName("Card")
                group_layout = QVBoxLayout(group)
                group_layout.setContentsMargins(12, 8, 12, 8)
                group_layout.setSpacing(4)

                # Checkbox with name and size
                files = "1 file" if count == 1 else f"{count} files"
                cb = QCheckBox(f"{name} ({self._format_size(size)}, {files})")
                cb.setChecked(False)
                checkboxes[key] = cb
                group_layout.addWidget(cb)

                # Description
                desc_label = QLabel(description)
                desc_label.setObjectName("Hint")
                desc_label.setWordWrap(True)
                desc_label.setIndent(cb.sizeHint().height())  # under the name, past the box
                group_layout.addWidget(desc_label)

                layout.addWidget(group)
                total_size += size

        if not checkboxes:
            no_cache = QLabel("All caches are currently empty.")
            no_cache.setObjectName("Hint")
            no_cache.setAlignment(Qt.AlignCenter)
            layout.addWidget(no_cache)

        layout.addStretch()

        # Buttons
        btn_layout = QHBoxLayout()

        select_all_btn = QPushButton("Select all")
        select_all_btn.setAutoDefault(False)
        select_all_btn.clicked.connect(
            lambda: [
                cb.setChecked(True) for cb in checkboxes.values() if cb.isEnabled()
            ]
        )
        btn_layout.addWidget(select_all_btn)

        if checkboxes:
            total_label = QLabel(f"Total: {self._format_size(total_size)}")
            total_label.setObjectName("Hint")
            btn_layout.addWidget(total_label)

        btn_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setAutoDefault(False)
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)

        clear_btn = QPushButton("Clear selected")
        clear_btn.setAutoDefault(False)
        set_tone(clear_btn, "error")

        def do_clear():
            # Get list of selected cache keys
            selected_keys = [k for k, cb in checkboxes.items() if cb.isChecked()]

            if not selected_keys:
                self.statusBar().showMessage(
                    "No cache categories selected to clear", 5000
                )
                return

            # Confirm before clearing
            reply = QMessageBox.question(
                self,
                "Clear the selected caches?",
                f"Are you sure you want to clear the selected caches ({len(selected_keys)})?\n\nThis action cannot be undone.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )

            if reply != QMessageBox.Yes:
                return
            if "masks" in selected_keys:
                reply = QMessageBox.warning(
                    self,
                    "Clear the masks?",
                    "Clearing the dynamic spectrum masks forgets the masks drawn on "
                    "every spectrum (File > Save Masks keeps a copy). Clear them?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    selected_keys.remove("masks")
                    if not selected_keys:
                        return
            if "sunpy" in selected_keys:
                # These are SunPy's folders, not SolarViewer's alone: say
                # which, and what else is in them
                folders = _sunpy_folders_text(self._format_size)
                reply = QMessageBox.warning(
                    self,
                    "Delete SunPy's folders?",
                    "Clearing the Sunpy Data Cache deletes everything in:\n\n"
                    f"{folders}\n\n"
                    "These are SunPy's own folders. Data downloaded with SunPy outside "
                    "SolarViewer (Fido, sample data) is kept there by default and is "
                    "deleted too. Delete them?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                ) if folders else QMessageBox.Yes
                if reply != QMessageBox.Yes:
                    selected_keys.remove("sunpy")
                    if not selected_keys:
                        return

            cleared = []
            try:
                for number, key in enumerate(selected_keys, 1):
                    name = _CLEARED_NAMES[key]
                    clearing = f"Clearing {name} ({number} of {len(selected_keys)})…"
                    if run_in_thread(dialog, clearing, _clear_cache, key, self.remote_cache):
                        cleared.append(name)
            except Cancelled:
                # A deletion under way cannot be stopped; the rest is spared
                self.statusBar().showMessage(
                    f"Stopped: {name} is still being cleared, the caches after it are kept",
                    8000,
                )
                self._update_cache_menu_item()
                dialog.reject()
                return

            if "remote" in selected_keys:
                from ..remote import RemoteFileBrowser

                if hasattr(RemoteFileBrowser, "_listing_cache"):
                    RemoteFileBrowser._listing_cache.clear()

            if cleared:
                self.statusBar().showMessage(f"Cleared: {', '.join(cleared)}", 5000)
                QMessageBox.information(
                    self,
                    "Caches cleared",
                    "Successfully cleared:\n• " + "\n• ".join(cleared),
                )
                self._update_cache_menu_item()
                dialog.accept()
            else:
                QMessageBox.warning(self, "Could not clear the caches", "The selected caches were not cleared. Check that no other window is using them and try again.")

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
        """Update the Manage Cache menu item with current total cache size.
        Called as the menu opens, which must not wait for that: the item says
        the last size found until the new one is in."""
        if not hasattr(self, "clear_cache_act") or getattr(self, "_cache_measurer", None):
            return

        def measure():
            try:
                total_size = _total_cache_size()
            except Exception as e:
                # Fallback if something goes wrong
                logger.warning(f"Failed to update cache size: {e}")
                total_size = None
            try:
                self._cache_measurer.done.emit(total_size)
            except RuntimeError:
                pass  # (the window has gone)

        self._cache_measurer = _Measured(self)
        self._cache_measurer.done.connect(self._show_cache_size)
        threading.Thread(target=measure, name="cache-size", daemon=True).start()

    def _show_cache_size(self, total_size):
        self._cache_measurer.deleteLater()
        self._cache_measurer = None
        if total_size is None:
            self.clear_cache_act.setText("Manage cache…")
        else:
            self.clear_cache_act.setText(f"Manage cache ({self._format_size(total_size)})")

    def _check_connection_health(self):
        """Periodically check connection health and auto-reconnect if needed."""

        if not self.remote_connection:
            return

        # Run ping in background thread to avoid blocking UI
        from PyQt5.QtCore import QThread, pyqtSignal

        # A reconnection is under way
        if getattr(self, "_reconnect_thread", None) is not None:
            return

        # A download receiving data shows the connection alive. (A ping would
        # wait behind its data: over a slow link longer than the 10 s below,
        # and closing the connection then would end the download.)
        if self.remote_connection.receiving():
            return

        # If previous ping is still running after 10s, assume connection is dead
        if (
            hasattr(self, "_ping_thread")
            and self._ping_thread
            and self._ping_thread.isRunning()
        ):
            if hasattr(self, "_ping_start_time"):
                import time

                elapsed = time.time() - self._ping_start_time
                if elapsed > 10 and not self._ping_thread.given_up:
                    # Connection is dead - the ping is stuck. Closing the
                    # connection ends whatever waits on it, the ping too,
                    # which then reports it lost and a reconnect follows.
                    # (Never terminate() the thread: killed while waiting on
                    # a Python lock, it leaves the lock pointing at its dead
                    # stack, and the next release of that lock crashes.)
                    self._ping_thread.given_up = True
                    self._ping_thread.connection.mark_lost()
                    if self.remote_connection:
                        self.statusBar().showMessage(
                            "Remote connection lost - click status to reconnect",
                            10000,
                        )
                        self._update_remote_status_indicator()
                        self._update_remote_menu_state()
            return

        class PingThread(QThread):
            result = pyqtSignal(bool)

            def __init__(self, connection):
                super().__init__()
                self.connection = connection
                self.given_up = False  # (the connection was closed under it)

            def run(self):
                try:
                    # Quick passive check first
                    if not self.connection.is_connected():
                        self.result.emit(False)
                        return

                    transport = self.connection.transport
                    if transport is None or not transport.is_active():
                        self.result.emit(False)
                        return

                    # A request the server answers itself, as ssh's
                    # ServerAliveInterval sends: the answer shows the
                    # connection alive, and nothing is started there. (An
                    # SFTP channel starts a process, which a busy login node
                    # can take longer than the 10 s allowed to do.)
                    transport.global_request("keepalive@openssh.com", wait=True)
                    alive = transport.is_active()
                    if not alive:
                        logger.warning("[SSH] Ping failed: the connection closed")
                    self.result.emit(alive)

                except Exception as e:
                    logger.warning(f"[SSH] Ping error: {e}")
                    self.result.emit(False)

        connection = self.remote_connection

        def on_ping_result(is_alive):
            if connection is not self.remote_connection:
                return  # (another server was connected meanwhile: this one was closed)
            if is_alive:
                self._reconnect_failures = 0
                self._reconnect_not_before = 0.0
            elif self._may_reconnect(connection):
                logger.warning("[SSH] Health check: Connection lost, attempting reconnect...")
                self._reconnect_in_background()

        import time

        self._ping_start_time = time.time()
        self._ping_thread = PingThread(self.remote_connection)
        self._ping_thread.result.connect(on_ping_result)
        self._ping_thread.start()

    def _may_reconnect(self, connection):
        """Whether the health check may connect again by itself now: not
        once the server has refused the login (only the user can log in
        again, and a login repeated every few seconds locks an account on
        a shared server), and after a failed try only once its wait is
        over (RECONNECT_WAITS)."""
        import time

        if getattr(connection, "login_refused", ""):
            return False
        return time.monotonic() >= getattr(self, "_reconnect_not_before", 0.0)

    def _reconnect_in_background(self):
        """Connect again to the server, in a thread: it can take as long as
        the connection's timeout, too long for the window to wait."""
        connection = self.remote_connection
        if connection is None or getattr(self, "_reconnect_thread", None) is not None:
            return
        from PyQt5.QtCore import QThread, pyqtSignal

        class ReconnectThread(QThread):
            done = pyqtSignal(bool)

            def run(self):
                try:
                    ok = connection.ensure_connected()
                except Exception as e:
                    logger.warning(f"[SSH] Reconnect error: {e}")
                    ok = False
                self.done.emit(bool(ok))

        def on_done(ok):
            if connection is not self.remote_connection:
                return  # (another server was connected meanwhile)
            if ok:
                logger.info("[SSH] Health check: Reconnected successfully")
                self._reconnect_failures = 0
                self._reconnect_not_before = 0.0
                self.statusBar().showMessage("Remote connection restored", 5000)
            elif getattr(connection, "login_refused", ""):
                logger.warning("[SSH] Health check: the server refused the login; not trying again")
                self.statusBar().showMessage(
                    "Remote connection lost, and the server refused the login: "
                    "not trying again - click status to connect",
                    0,
                )
            else:
                import time

                failures = self._reconnect_failures = getattr(self, "_reconnect_failures", 0) + 1
                wait = RECONNECT_WAITS[min(failures, len(RECONNECT_WAITS)) - 1]
                self._reconnect_not_before = time.monotonic() + wait
                logger.warning(f"[SSH] Health check: Reconnect failed; next try in {wait} s")
                self.statusBar().showMessage(
                    f"Remote connection lost - trying again in {_wait_text(wait)} "
                    "(click status to reconnect)",
                    10000,
                )
            self._update_remote_status_indicator()
            self._update_remote_menu_state()

        def on_finished():
            self._reconnect_thread = None

        thread = ReconnectThread()
        thread.done.connect(on_done)
        thread.finished.connect(on_finished)  # (kept until it has ended)
        self._reconnect_thread = thread
        track_thread(thread, f"Reconnecting to {connection.connection_info}…", self)
        self.remote_status_btn.setText("Reconnecting…")
        thread.start()

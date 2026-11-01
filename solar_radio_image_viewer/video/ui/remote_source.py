"""
Images on an SSH server (the main window's connection): choosing them and
fetching files (the planner reads the server's folders and headers). The
server needs only SSH; the video is made on this computer.
"""

import os
import posixpath

from PyQt5.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
)

from ...ui.background import Cancelled, run_in_thread
from .progress import run_with_dialog

def _open_server(connection, cache, remote, folder):
    """(RemoteFiles, its cache, the folder to start in) for ``connection``.
    Off the GUI thread: this loads the saved headers, and without a ``folder``
    asks the server for its home folder."""
    from ...remote.file_cache import RemoteFileCache
    from ...remote.remote_files import RemoteFiles

    cache = cache or RemoteFileCache()
    remote = remote or RemoteFiles(connection, cache)
    if folder is None:
        try:
            folder = connection.get_home_directory()
        except Exception:
            folder = "/"
    return remote, cache, folder


def _cached_copies(remote, paths):
    """{path: its local copy, or None}; may ask the server about each."""
    return {path: remote.cached(path) for path in paths}


class RemoteSourceMixin:
    """Images on a server: "Images on" in the Input tab, the server's
    folders and headers, pickers and fetching. Mixed into VideoCreationDialog."""

    def _create_images_on_row(self):
        row = QHBoxLayout()
        self.images_on_combo = QComboBox()
        self.images_on_combo.addItem("This computer", "local")
        self.images_on_combo.addItem("Server", "server")
        self.images_on_combo.setToolTip(
            "Images on an SSH server (the main window's connection; the server\n"
            "needs nothing but SSH): folders and headers are read there, the\n"
            "images fetched when needed, and the video made on this computer"
        )
        row.addWidget(self.images_on_combo)
        self.connect_server_btn = QPushButton("Connect…")
        self.connect_server_btn.setToolTip("Connect to a server (as in the main window)")
        self.connect_server_btn.clicked.connect(self._connect_server)
        row.addWidget(self.connect_server_btn)
        row.addStretch()

        self.remote = None  # RemoteFiles while the images are on a server
        self.remote_connection = self.remote_cache = None  # for the contour dialog
        self.images_on_combo.currentIndexChanged.connect(self._on_images_on_changed)
        self._update_images_on()
        return row

    # ---------- The connection ----------

    def _main_connection(self):
        connection = getattr(self._main_window, "remote_connection", None)
        try:
            return connection if connection is not None and connection.is_connected() else None
        except Exception:
            return None

    def _update_images_on(self):
        connection = self._main_connection()
        self.images_on_combo.setItemText(
            1, f"Server ({connection.connection_info})" if connection else "Server (not connected)"
        )
        self.connect_server_btn.setVisible(connection is None and self._main_window is not None)
        self.images_on_combo.blockSignals(True)
        self.images_on_combo.setCurrentIndex(1 if self.remote else 0)
        self.images_on_combo.blockSignals(False)

    def _connect_server(self):
        """Connect as the main window does, and use the connection."""
        from ...remote import ConnectionDialog

        dialog = ConnectionDialog(self)

        def connected(connection):
            if hasattr(self._main_window, "_on_remote_connected"):
                self._main_window._on_remote_connected(connection)
            self._update_images_on()
            self.images_on_combo.setCurrentIndex(1)

        dialog.connectionEstablished.connect(connected)
        dialog.exec_()

    def _on_images_on_changed(self, *_):
        server = self.images_on_combo.currentData() == "server"
        connection = self._main_connection() if server else None
        if server and connection is None:
            self.images_on_combo.blockSignals(True)
            self.images_on_combo.setCurrentIndex(0)
            self.images_on_combo.blockSignals(False)
            if self._main_window is not None:
                self._connect_server()
            else:
                QMessageBox.information(
                    self, "Not connected", "Open this window from the main window to use a server."
                )
            return
        self._use_server(connection)

    def _use_server(self, connection, folder=None):
        """Images on the server of ``connection`` (None: this computer).
        False if cancelled: things stay as they were."""
        if connection is None:
            if self.remote is not None:
                self.remote.close()
            self.remote = None
            self.remote_connection = self.remote_cache = None
        else:
            try:
                remote, cache, folder = self._open_connection(connection, folder)
            except Cancelled:
                self.preview_panel.show_idle("Cancelled")
                self._update_images_on()
                return False
            if self.remote is not None and self.remote is not remote:
                self.remote.close()
            self.remote = remote
            self.remote_connection, self.remote_cache = connection, cache
        if folder is not None:
            self.input_directory_edit.setText(folder)
        self._planner.forget()
        self._preview_renderer.remote = self.remote
        self._end_render_all()
        self._update_images_on()
        self.preview_panel.show_remote(self.remote is not None)
        self.schedule_preview()
        return True

    def _open_connection(self, connection, folder):
        """(RemoteFiles, its cache, folder to start in: ``folder``, or the one
        last browsed, or the home folder). Raises Cancelled."""
        from ...remote import RemoteFileBrowser

        current = self.remote if self.remote and self.remote.connection is connection else None
        if folder is None:
            folder = RemoteFileBrowser._last_paths.get(connection.host) or None
        cache = getattr(self._main_window, "remote_cache", None)
        if current is not None and folder is not None:
            return current, self.remote_cache, folder
        return run_in_thread(
            self,
            f"Connecting to {connection.connection_info}…",
            _open_server,
            connection,
            cache,
            current,
            folder,
        )

    def _take_main_window_source(self, tab):
        """When the main window shows an image of a server (its remote
        browser), the images here are that server's, in its folder: the
        image, None if it is not on a server, False if connecting was
        cancelled."""
        folder = getattr(tab, "_remote_base_dir", None)
        connection = self._main_connection()
        if not (getattr(tab, "_is_remote_mode", False) and folder and connection):
            return None
        if not self._use_server(connection, folder):
            return False
        name = os.path.basename(getattr(tab, "imagename", "") or "")
        if name:
            self.input_pattern_edit.setText("*" + os.path.splitext(name)[1])
            self.reference_image = posixpath.join(folder, name)
            self.reference_image_edit.setText(self.reference_image)
        return self.reference_image

    # ---------- Picking and fetching ----------

    def _pick_remote(self, kind, start=None):
        """A server path chosen in the remote browser (``kind``: "folder" or
        "file"), or None."""
        from ...remote import RemoteFileBrowser

        if start:
            RemoteFileBrowser._last_paths[self.remote.host] = start
        browser = RemoteFileBrowser(
            self.remote.connection, cache=self.remote.cache, parent=self, pick=kind
        )
        picked = []
        browser.pathPicked.connect(picked.append)
        browser.exec_()
        return picked[0] if picked else None

    def _fetch_now(self, paths, what="images"):
        """{path: local copy} of server ``paths``, fetched now (with a
        progress dialog); None if cancelled or failed."""
        remote = self.remote
        try:
            copies = run_in_thread(
                self, f"Looking for {what} on {remote.label}…", _cached_copies, remote, paths
            )
        except Cancelled:
            self.preview_panel.show_idle("Cancelled")
            return None
        except Exception as e:
            QMessageBox.warning(self, "Could not fetch", str(e))
            return None
        if all(copies.values()):
            return copies

        def work(progress, cancelled):
            def report(done, total, files, count):
                progress(
                    f"Fetching {what}: {files} of {count} "
                    f"({done / 1e6:.0f} of {total / 1e6:.0f} MB)",
                    int(done / 1e6),
                    int(total / 1e6),
                )

            return remote.fetch(
                paths,
                progress=report,
                cancelled=cancelled,
                checking=lambda done, total: progress(
                    f"Checking {what} on {remote.label}: {done} of {total}", done, total
                ),
            )

        outcome = run_with_dialog(
            self, "Fetching from the server", f"Fetching {what} from {remote.label}…", work
        )
        if outcome.cancelled:
            self.preview_panel.show_idle("Cancelled")
            return None
        if outcome.error:
            QMessageBox.warning(self, "Could not fetch", outcome.error)
        return outcome.result

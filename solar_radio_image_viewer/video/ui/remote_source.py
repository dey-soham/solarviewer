"""
Images on an SSH server (the main window's connection): choosing them,
reading the server's folders and headers in the background, and fetching
files. The server needs only SSH; the video is made on this computer.
"""

import os
import posixpath
import time

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
)


class _Job(QThread):
    """Runs ``work(progress)`` in the background."""

    done = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, work):
        super().__init__()
        self.work = work

    def run(self):
        try:
            self.done.emit(self.work(self.progress.emit))
        except Exception as e:
            self.failed.emit(str(e))


class RemoteSourceMixin:
    """Images on a server: "Images on" in the Input tab, the server's
    folders and headers, pickers and fetching. Mixed into VideoCreationDialog."""

    def _create_images_on_row(self):
        row = QHBoxLayout()
        row.addWidget(QLabel("Images on:"))
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
        self._remote_lists = {}  # (folder, pattern) -> image paths
        self._remote_jobs = {}  # (folder, pattern) -> _Job
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

    def _connect_server(self):
        """Connect as the main window does, and use the connection."""
        from ...remote import ConnectionDialog

        dialog = ConnectionDialog(self._main_window)

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
                    self, "Not Connected", "Open this window from the main window to use a server."
                )
            return
        self._use_server(connection)

    def _use_server(self, connection, folder=None):
        """Images on the server of ``connection`` (None: this computer)."""
        from ...remote.file_cache import RemoteFileCache
        from ...remote.remote_files import RemoteFiles

        if connection is None:
            if self.remote is not None:
                self.remote.close()
            self.remote = None
            self.remote_connection = self.remote_cache = None
        else:
            cache = getattr(self._main_window, "remote_cache", None) or RemoteFileCache()
            if self.remote is None or self.remote.connection is not connection:
                if self.remote is not None:
                    self.remote.close()
                self.remote = RemoteFiles(connection, cache)
            self.remote_connection, self.remote_cache = connection, cache
            if folder is None:
                folder = self._remote_start_folder()
        if folder is not None:
            self.input_directory_edit.setText(folder)
        self._remote_lists.clear()
        self._preview_renderer.remote = self.remote
        self._preview_renderer.render_all = False
        self.images_on_combo.blockSignals(True)
        self.images_on_combo.setCurrentIndex(1 if self.remote else 0)
        self.images_on_combo.blockSignals(False)
        self._update_images_on()
        self.preview_panel.show_remote(self.remote is not None)
        self.schedule_preview()

    def _remote_start_folder(self):
        """The server folder last browsed, or the home folder."""
        from ...remote import RemoteFileBrowser

        last = RemoteFileBrowser._last_paths.get(self.remote.host)
        if last:
            return last
        try:
            return self.remote.connection.get_home_directory()
        except Exception:
            return "/"

    def _take_main_window_source(self, tab):
        """When the main window shows an image of a server (its remote
        browser), the images here are that server's, in its folder."""
        folder = getattr(tab, "_remote_base_dir", None)
        connection = self._main_connection()
        if not (getattr(tab, "_is_remote_mode", False) and folder and connection):
            return None
        self._use_server(connection, folder)
        name = os.path.basename(getattr(tab, "imagename", "") or "")
        if name:
            self.input_pattern_edit.setText("*" + os.path.splitext(name)[1])
            self.reference_image = posixpath.join(folder, name)
            self.reference_image_edit.setText(self.reference_image)
        return self.reference_image

    # ---------- Folders and headers ----------

    def _remote_list(self, folder, pattern):
        """The images matching ``pattern`` in server ``folder``, or None
        while they are read (in the background; the preview then updates)."""
        key = (folder, pattern)
        if key in self._remote_lists:
            return self._remote_lists[key]
        if key not in self._remote_jobs:
            remote = self.remote

            def work(progress):
                progress(f"Reading {folder} on {remote.label}…")
                paths = [p for p in remote.glob(folder, pattern) if remote.is_image(p)]
                remote.headers(
                    paths, progress=lambda done, total: progress(f"Reading headers: {done} of {total}")
                )
                return paths

            job = _Job(work)
            job.progress.connect(self.preview_panel.status_label.setText)
            job.done.connect(lambda paths, key=key: self._on_remote_listed(key, paths))
            job.failed.connect(lambda error, key=key: self._on_remote_listed(key, None, error))
            self._remote_jobs[key] = job
            job.start()
        return None

    def _on_remote_listed(self, key, paths, error=None):
        self._remote_jobs.pop(key, None)
        if error is not None:
            self.preview_panel.status_label.setText(f"Could not read {key[0]}: {error}")
            self._remote_lists[key] = []
        else:
            self._remote_lists[key] = paths
            if key == (self.input_directory_edit.text(), self.input_pattern_edit.text().strip()):
                self.files_found_label.setText(
                    f"Found {len(paths)} images matching the pattern on {self.remote.label}"
                )
        self.schedule_preview(delay=0)

    def _forget_remote_lists(self):
        """Read the server's folders again (e.g. new files)."""
        self._remote_lists.clear()

    def _remote_times_of(self, paths):
        from ..remote_video import remote_times

        return remote_times(self.remote, paths)

    def _remote_sort_key(self, path):
        when = self._remote_times_of([path])[0]
        return (0, when.timestamp(), path) if when else (1, 0, path)

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
        missing = [p for p in paths if not remote.cached(p)]
        if not missing:
            return {p: remote.cached(p) for p in paths}
        dialog = QProgressDialog(f"Fetching {what} from {remote.label}…", "Cancel", 0, 0, self)
        dialog.setWindowTitle("Fetching from the Server")
        dialog.setMinimumDuration(300)
        state = {}

        def work(progress):
            def report(done, total, files, count):
                progress(f"{files} of {count} ({done / 1e6:.0f} of {total / 1e6:.0f} MB)")

            return remote.fetch(paths, progress=report, cancelled=lambda: state.get("cancel"))

        job = _Job(work)
        job.progress.connect(lambda text: dialog.setLabelText(f"Fetching {what}: {text}"))
        job.done.connect(lambda result: state.update(result=result))
        job.failed.connect(lambda error: state.update(error=error))
        dialog.canceled.connect(lambda: state.update(cancel=True))
        job.start()
        while job.isRunning():
            QApplication.processEvents()
            time.sleep(0.02)
        dialog.close()
        if "error" in state and not state.get("cancel"):
            QMessageBox.warning(self, "Could Not Fetch", state["error"])
        return state.get("result")

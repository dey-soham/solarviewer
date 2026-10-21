"""
Opening files and stepping through a directory (local or remote).
"""

import os

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QFileDialog,
    QLineEdit,
)

from ..ui.styles import set_hand_cursor, theme_manager


class FileNavigationMixin:
    """File selection, directory scanning, prev/next/first/last, filter and playlist.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``contour_settings``, ``dir_entry``, ``figure``, ``_file_pos_label``,
    ``_first_file_btn``, ``_last_file_btn``, ``_next_file_btn``,
    ``on_visualization_changed``, ``_open_dialogs``, ``_prev_file_btn``,
    ``_load_target_size``, ``radio_casa_image``, ``_reset_hpc_state``,
    ``show_status_message``, ``stokes_combo``, ``threshold_entry``,
    ``update_tab_name_from_path``, ``window``
    """

    def _setup_file_navigation_shortcuts(self):
        """Setup keyboard shortcuts for file navigation within this tab"""
        from PyQt5.QtGui import QKeySequence
        from PyQt5.QtWidgets import QShortcut

        # Use [ and ] for prev/next
        # Use { and } (Shift+[ Shift+]) for first/last
        prev_shortcut = QShortcut(QKeySequence("["), self)
        prev_shortcut.activated.connect(self._on_prev_file)

        next_shortcut = QShortcut(QKeySequence("]"), self)
        next_shortcut.activated.connect(self._on_next_file)

        first_shortcut = QShortcut(QKeySequence("{"), self)  # Shift+[
        first_shortcut.activated.connect(self._on_first_file)

        last_shortcut = QShortcut(QKeySequence("}"), self)  # Shift+]
        last_shortcut.activated.connect(self._on_last_file)

    def select_file_or_directory(self):
        import time

        # Check if we're in remote mode - if so, use remote file browser
        main_window = self.window()
        if hasattr(main_window, "remote_connection") and main_window.remote_connection:
            if main_window.remote_connection.is_connected():
                # Use remote file browser instead of local dialog
                self._select_remote_file(main_window)
                return

        if self.radio_casa_image.isChecked():
            # Select CASA image directory
            directory = QFileDialog.getExistingDirectory(
                self,
                caption="Select an image",
                options=QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
            )
            if directory:
                start_time = time.time()
                self._reset_hpc_state()  # Clear HPC state for new image
                self.imagename = directory
                self.dir_entry.setText(directory)
                # Clear contour data so it gets reloaded for new image
                self.contour_settings["contour_data"] = None
                self.current_contour_wcs = None
                # Clear figure to prevent restoring old view limits
                self.figure.clear()
                self.on_visualization_changed(dir_load=True)
                # self.auto_minmax()
                self.update_tab_name_from_path(directory)  # Update tab name
                self.show_status_message(
                    f"Loaded {directory} in {time.time() - start_time:.2f} seconds"
                )
                # Scan directory for file navigation
                self._scan_directory_files()
        else:
            # Select FITS file
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "Select a FITS file",
                "",
                "FITS files (*.fits *.fts);; All files (*)",
            )
            if file_path:
                start_time = time.time()
                self._reset_hpc_state()  # Clear HPC state for new image
                self.imagename = file_path
                self.dir_entry.setText(file_path)
                # Clear contour data so it gets reloaded for new image
                self.contour_settings["contour_data"] = None
                self.current_contour_wcs = None
                # Clear figure to prevent restoring old view limits
                self.figure.clear()
                self.on_visualization_changed(dir_load=True)
                # self.auto_minmax()
                self.update_tab_name_from_path(file_path)  # Update tab name
                self.show_status_message(
                    f"Loaded {file_path} in {time.time() - start_time:.2f} seconds"
                )
                # Scan directory for file navigation
                self._scan_directory_files()

    def _select_remote_file(self, main_window):
        """
        Open the remote file browser when in remote mode.
        This is called by select_file_or_directory when connected to a remote server.
        """
        import time

        try:
            from ..remote import RemoteFileBrowser

            # Determine if we're in CASA mode (directories) or FITS mode (files)
            casa_mode = self.radio_casa_image.isChecked()

            browser = RemoteFileBrowser(
                main_window.remote_connection,
                cache=main_window.remote_cache,
                parent=self,
                casa_mode=casa_mode,
            )

            # Connect to handle the selected file
            def on_file_selected(local_path, is_remote):
                if not local_path or not os.path.exists(local_path):
                    return

                start_time = time.time()
                self._reset_hpc_state()
                self.imagename = local_path
                self.dir_entry.setText(local_path)
                self.contour_settings["contour_data"] = None
                self.current_contour_wcs = None
                self.figure.clear()
                self.on_visualization_changed(dir_load=True)
                self.update_tab_name_from_path(local_path)

                # Set mode based on what was selected
                self._is_remote_mode = is_remote

                if is_remote:
                    self._remote_base_dir = browser.current_path
                    # Populate remote file list for navigation
                    self._populate_remote_file_list(
                        main_window, browser.current_path, casa_mode, local_path
                    )

                    elapsed = time.time() - start_time
                    basename = os.path.basename(local_path)
                    self.show_status_message(
                        f"Loaded remote file: {basename} ({elapsed:.2f}s)"
                    )
                else:
                    # Clear remote state if we selected a local file
                    self._remote_base_dir = None
                    self._remote_file_list = []

                    # Scan local directory for navigation
                    self._scan_directory_files()

                    elapsed = time.time() - start_time
                    basename = os.path.basename(local_path)
                    self.show_status_message(
                        f"Loaded local file: {basename} ({elapsed:.2f}s)"
                    )

            browser.fileSelected.connect(on_file_selected)
            browser.exec_()

        except Exception as e:
            from PyQt5.QtWidgets import QMessageBox

            QMessageBox.critical(
                self, "Remote Error", f"Failed to open remote browser: {e}"
            )

    def _scan_directory_files(self, rescan_only=False):
        """Scan the base directory for FITS/image files matching the filter pattern

        Args:
            rescan_only: If True, don't change the filter pattern (user manually set it)
        """
        import fnmatch
        import os

        if not self.imagename:
            return

        # Ensure we're in local mode when scanning local directory
        self._is_remote_mode = False
        if hasattr(self, "_remote_file_list"):
            self._remote_file_list = []
        if hasattr(self, "_remote_base_dir"):
            self._remote_base_dir = None

        # Determine base directory and file type
        if os.path.isdir(self.imagename):
            # CASA image - use parent directory
            self._file_base_dir = os.path.dirname(self.imagename)
            default_pattern = "*.image"
        else:
            # FITS file - use containing directory
            self._file_base_dir = os.path.dirname(self.imagename)
            if self.imagename.lower().endswith(".fts"):
                default_pattern = "*.fts"
            else:
                default_pattern = "*.fits"

        if not self._file_base_dir:
            self._file_base_dir = "."

        # Always update filter pattern to match current file's extension when opening a new file
        if not rescan_only:
            # Update filter pattern to match the extension of the currently opened file
            self._file_filter_pattern = default_pattern

        # Get all items in directory
        all_items = []
        try:
            for item in os.listdir(self._file_base_dir):
                item_path = os.path.join(self._file_base_dir, item)

                # Check if it matches the filter pattern
                if not fnmatch.fnmatch(item, self._file_filter_pattern):
                    continue

                # Include FITS files
                if item.lower().endswith((".fits", ".fts")):
                    all_items.append(item_path)
                # Include CASA images (directories that look like images)
                elif os.path.isdir(item_path) and item.endswith(".image"):
                    all_items.append(item_path)
        except Exception as e:
            print(f"[ERROR] Error scanning directory: {e}")
            self.show_status_message(f"Error scanning directory: {e}")
            self._file_list = []
            self._file_list_index = -1
            self._update_nav_buttons()
            return

        # Sort alphabetically
        self._file_list = sorted(all_items)

        # Find current file index
        current_path = os.path.abspath(self.imagename)
        self._file_list_index = -1
        for i, path in enumerate(self._file_list):
            if os.path.abspath(path) == current_path:
                self._file_list_index = i
                break

        self._update_nav_buttons()

    def _prefetch_neighbour_files(self):
        """Start reading the files either side of the current one in the
        background, with the settings the next load will use."""
        from ..processing.prefetch import image_prefetcher

        index = getattr(self, "_file_list_index", -1)
        if getattr(self, "_is_remote_mode", False) or index < 0:
            return
        stokes = self.stokes_combo.currentText() if self.stokes_combo else "I"
        if stokes == "Lfrac":
            return  # its read writes a temporary image in the working directory
        try:
            threshold = float(self.threshold_entry.text())
        except (ValueError, AttributeError):
            return
        neighbours = [
            self._file_list[i]
            for i in (index + 1, index - 1)
            if 0 <= i < len(self._file_list)
        ]
        image_prefetcher.schedule(
            neighbours,
            stokes,
            threshold,
            tuple(self.current_rms_box),
            self._load_target_size(),
        )

    def _update_nav_buttons(self):
        """Update navigation button states and position label"""
        if not hasattr(self, "_prev_file_btn"):
            return

        # Enable/disable buttons based on position
        has_prev = self._file_list_index > 0
        has_next = (
            self._file_list_index >= 0
            and self._file_list_index < len(self._file_list) - 1
        )

        # First/Prev need previous files
        self._first_file_btn.setEnabled(has_prev)
        self._prev_file_btn.setEnabled(has_prev)

        # Next/Last need next files
        self._next_file_btn.setEnabled(has_next)
        self._last_file_btn.setEnabled(has_next)

        # Update position label
        total = len(self._file_list)
        if total > 0:
            if self._file_list_index >= 0:
                current = self._file_list_index + 1
                filename = os.path.basename(self._file_list[self._file_list_index])
                self._file_pos_label.setText(f"{current}/{total}")
                self._file_pos_label.setToolTip(
                    f"File {current} of {total}: {filename}\nFilter: {self._file_filter_pattern}"
                )
            else:
                # Current file not in filtered list
                self._file_pos_label.setText(f"-/{total}")
                self._file_pos_label.setToolTip(
                    f"Current file excluded by filter\n{total} matching files found\nFilter: {self._file_filter_pattern}"
                )
        else:
            self._file_pos_label.setText("")
            self._file_pos_label.setToolTip(
                f"No files match filter: {self._file_filter_pattern}"
                if self._file_filter_pattern != "*"
                else "No files found"
            )

    def _populate_remote_file_list(
        self, main_window, remote_dir, casa_mode, current_local_path
    ):
        """Populate the file list from a remote directory for navigation (async)."""
        # Start async population - don't block UI
        from PyQt5.QtCore import QThread, pyqtSignal

        class PopulateThread(QThread):
            finished = pyqtSignal(list, int)  # entries, current_index
            error = pyqtSignal(str)

            def __init__(self, connection, remote_dir, casa_mode, current_basename):
                super().__init__()
                self.connection = connection
                self.remote_dir = remote_dir
                self.casa_mode = casa_mode
                self.current_basename = current_basename
                self._sftp = None

            def run(self):
                try:
                    import json
                    import shlex

                    # FAST PATH: Try executing a python script on the remote server
                    # This filters on the server side, avoiding huge loops for large directories
                    py_script = """
import os, json, sys, stat
path = sys.argv[1]
entries = []
casa_mode = %s

try:
    with os.scandir(path) as it:
        for e in it:
            name = e.name
            if name.startswith('.'): continue
            
            is_dir = e.is_dir()
            
            include = False
            if casa_mode:
                if is_dir and (name.endswith('.image') or name.endswith('.im')):
                    include = True
            else:
                is_fits = not is_dir and name.lower().endswith(('.fits', '.fts', '.fit'))
                if is_fits: include = True
            
            if include:
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
""" % ("True" if self.casa_mode else "False")

                    # Try python3 first, then python
                    cmd = f"python3 -c {shlex.quote(py_script)} {shlex.quote(self.remote_dir)}"
                    stdin, stdout, stderr = self.connection._client.exec_command(cmd)

                    # Read output
                    out_data = stdout.read().decode("utf-8").strip()

                    entries = []
                    success = False

                    if out_data and out_data.startswith("["):
                        try:
                            raw_entries = json.loads(out_data)
                            from ..remote.ssh_manager import RemoteFileInfo

                            for item in raw_entries:
                                full_path = os.path.join(self.remote_dir, item["n"])
                                info = RemoteFileInfo(
                                    name=item["n"],
                                    path=full_path,
                                    is_dir=item["d"],
                                    size=item["s"],
                                    mtime=item["m"],
                                )
                                entries.append(info)
                            success = True
                        except:
                            pass

                    # FALLBACK: If fast path failed (no python, etc), use slower SFTP loop
                    if not success:
                        if self.connection._client:
                            self._sftp = self.connection._client.open_sftp()
                        else:
                            self.error.emit("Not connected")
                            return

                        import stat

                        from ..remote.ssh_manager import RemoteFileInfo

                        attrs = self._sftp.listdir_attr(self.remote_dir)

                        for attr in attrs:
                            name = attr.filename
                            if name.startswith("."):
                                continue

                            st_mode = attr.st_mode
                            is_dir = stat.S_ISDIR(st_mode)

                            # Pre-filter check
                            include_item = False
                            if self.casa_mode:
                                if is_dir and (
                                    name.endswith(".image") or name.endswith(".im")
                                ):
                                    include_item = True
                            else:
                                # Check extension for FITS files OR CASA images
                                is_fits = not is_dir and name.lower().endswith(
                                    (".fits", ".fts", ".fit")
                                )

                                if is_fits:
                                    include_item = True

                            if include_item:
                                full_path = os.path.join(self.remote_dir, name)
                                info = RemoteFileInfo(
                                    name=name,
                                    path=full_path,
                                    is_dir=is_dir,
                                    size=attr.st_size,
                                    mtime=attr.st_mtime,
                                )
                                entries.append(info)

                    # Sort
                    entries.sort(key=lambda x: x.name.lower())

                    # Find current index
                    current_idx = -1
                    for i, e in enumerate(entries):
                        if e.name == self.current_basename:
                            current_idx = i
                            break

                    self.finished.emit(entries, current_idx)

                except Exception as e:
                    self.error.emit(str(e))
                finally:
                    if self._sftp:
                        try:
                            self._sftp.close()
                        except:
                            pass

        try:
            if (
                not main_window.remote_connection
                or not main_window.remote_connection.is_connected()
            ):
                return

            current_basename = os.path.basename(current_local_path)

            # Store reference to thread to prevent garbage collection
            self._populate_thread = PopulateThread(
                main_window.remote_connection, remote_dir, casa_mode, current_basename
            )

            def on_finished(entries, current_idx):
                try:
                    self._remote_file_list = entries
                    self._file_list = [e.path for e in entries]
                    self._file_list_index = current_idx
                    self._update_nav_buttons()
                except RuntimeError:
                    pass  # Widget deleted

            def on_error(msg):
                print(f"[WARNING] Failed to populate remote file list: {msg}")
                self._remote_file_list = []
                self._file_list = []
                self._file_list_index = -1
                try:
                    self._update_nav_buttons()
                except RuntimeError:
                    pass

            self._populate_thread.finished.connect(on_finished)
            self._populate_thread.error.connect(on_error)
            self._populate_thread.start()

        except Exception as e:
            print(f"[WARNING] Failed to start remote file list population: {e}")
            self._remote_file_list = []
            self._file_list = []
            self._file_list_index = -1
            self._update_nav_buttons()

    def _load_remote_file(self, remote_path):
        """Download and load a remote file for navigation."""
        from pathlib import Path

        main_window = self.window()
        if not main_window or not hasattr(main_window, "remote_connection"):
            return False

        if (
            not main_window.remote_connection
            or not main_window.remote_connection.is_connected()
        ):
            return False

        try:
            from PyQt5.QtCore import QEventLoop, Qt, QTimer
            from PyQt5.QtWidgets import QProgressDialog

            from ..remote.download_thread import DownloadThread

            # Get the remote file info
            entry = main_window.remote_connection.get_file_info(remote_path)

            # Check cache first
            cached_path = main_window.remote_cache.get_cached_path(
                main_window.remote_connection._host,
                remote_path,
                entry.mtime,
                entry.size,
            )

            if cached_path:
                local_path = str(cached_path)
            else:
                # Download the file with progress dialog (NON-BLOCKING Main Thread)
                local_path = main_window.remote_cache.get_cache_path(
                    main_window.remote_connection._host,
                    remote_path,
                )

                # Create progress dialog
                progress = QProgressDialog(
                    f"Downloading {os.path.basename(remote_path)}...",
                    "Cancel",
                    0,
                    100,
                    self,
                )
                progress.setWindowTitle("Downloading")
                progress.setWindowModality(Qt.WindowModal)
                progress.setMinimumDuration(0)
                progress.setValue(0)

                # Setup Download Thread
                download_thread = DownloadThread(
                    main_window.remote_connection,
                    remote_path,
                    str(local_path),
                    is_directory=entry.is_dir,
                )

                # Store local variables to capture result
                download_result = {"success": False, "error": None, "path": None}

                # Create local event loop to wait for thread
                loop = QEventLoop()

                def on_progress(transferred, total):
                    if total > 0:
                        percent = int(100 * transferred / total)
                        progress.setValue(percent)
                        if total > 1024 * 1024:
                            progress.setLabelText(
                                f"Downloading {os.path.basename(remote_path)}...\n"
                                f"{transferred / (1024**2):.1f} / {total / (1024**2):.1f} MB"
                            )
                        else:
                            progress.setLabelText(
                                f"Downloading {os.path.basename(remote_path)}...\n"
                                f"{transferred / 1024:.1f} / {total / 1024:.1f} KB"
                            )

                def on_finished(path):
                    download_result["success"] = True
                    download_result["path"] = path
                    loop.quit()

                def on_error(msg):
                    download_result["success"] = False
                    download_result["error"] = msg
                    loop.quit()

                def on_cancel():
                    download_thread.cancel()
                    progress.setLabelText("Cancelling...")
                    progress.setCancelButtonText(None)  # Disable cancel button
                    # We don't quit loop yet, wait for thread to actually stop or finish
                    # But if we rely on loop.quit, we need to ensure thread finishes.
                    # As a fallback, we can forcefully quit loop if needed, but better to wait.

                download_thread.progress.connect(on_progress)
                download_thread.finished.connect(on_finished)
                download_thread.error.connect(on_error)
                progress.canceled.connect(on_cancel)

                # Safety watchdog: if download thread terminates without
                # emitting finished/error (e.g. crash in native code, OS
                # kill, etc.), force-quit the event loop so the UI doesn't
                # hang forever.
                safety_timer = QTimer()

                def check_thread_health():
                    if download_thread.isFinished() and loop.isRunning():
                        download_result["success"] = False
                        download_result["error"] = (
                            "Download thread terminated unexpectedly. "
                            "The connection may have been lost."
                        )
                        loop.quit()

                safety_timer.timeout.connect(check_thread_health)
                safety_timer.start(2000)  # Check every 2 seconds

                # Start thread and show dialog
                download_thread.start()
                progress.show()

                # Wait for completion
                loop.exec_()

                safety_timer.stop()
                progress.close()
                download_thread.wait()  # Ensure thread is joined

                if not download_result["success"]:
                    if download_result["error"]:
                        raise Exception(download_result["error"])
                    else:
                        raise InterruptedError("Download cancelled")  # User cancelled

                local_path = download_result["path"]

                # Mark as cached
                main_window.remote_cache.mark_cached(
                    main_window.remote_connection._host,
                    remote_path,
                    Path(local_path),
                    entry.mtime,
                    entry.size,
                )

            # Load the file
            self._reset_hpc_state()
            self.imagename = local_path
            self.dir_entry.setText(local_path)
            self.contour_settings["contour_data"] = None
            self.current_contour_wcs = None
            self.figure.clear()
            self.on_visualization_changed(dir_load=True)
            self.update_tab_name_from_path(local_path)

            return True

        except InterruptedError:
            # User cancelled the download
            self.show_status_message("Download cancelled")
            return False
        except Exception as e:
            print(f"[ERROR] Failed to load remote file: {e}")
            self.show_status_message(f"Failed to load remote file: {e}")
            return False

    def _on_prev_file(self):
        """Load the previous file in the file list"""
        if self._file_list_index <= 0:
            return

        new_index = self._file_list_index - 1
        new_path = self._file_list[new_index]
        self._file_list_index = new_index

        # Check if in remote mode
        if self._is_remote_mode:
            if self._load_remote_file(new_path):
                self._update_nav_buttons()
                self.show_status_message(f"Loaded {os.path.basename(new_path)}")
            return

        # Reset HPC state
        self._reset_hpc_state()

        # Load the file
        self.imagename = new_path
        self.dir_entry.setText(new_path)
        self.contour_settings["contour_data"] = None
        self.current_contour_wcs = None
        # Clear figure to prevent restoring old view limits
        self.figure.clear()
        self.on_visualization_changed(dir_load=True)
        self.update_tab_name_from_path(new_path)

        self._update_nav_buttons()
        self.show_status_message(f"Loaded {os.path.basename(new_path)}")

    def _on_next_file(self):
        """Load the next file in the file list"""
        if (
            self._file_list_index < 0
            or self._file_list_index >= len(self._file_list) - 1
        ):
            return

        new_index = self._file_list_index + 1
        new_path = self._file_list[new_index]
        self._file_list_index = new_index

        # Check if in remote mode
        if self._is_remote_mode:
            if self._load_remote_file(new_path):
                self._update_nav_buttons()
                self.show_status_message(f"Loaded {os.path.basename(new_path)}")
            return

        # Reset HPC state
        self._reset_hpc_state()

        # Load the file
        self.imagename = new_path
        self.dir_entry.setText(new_path)
        self.contour_settings["contour_data"] = None
        self.current_contour_wcs = None
        # Clear figure to prevent restoring old view limits
        self.figure.clear()
        self.on_visualization_changed(dir_load=True)
        self.update_tab_name_from_path(new_path)

        self._update_nav_buttons()
        self.show_status_message(f"Loaded {os.path.basename(new_path)}")

    def _on_first_file(self):
        """Load the first file in the file list"""
        if not self._file_list or self._file_list_index <= 0:
            return

        new_index = 0
        new_path = self._file_list[new_index]
        self._file_list_index = new_index

        # Check if in remote mode
        if self._is_remote_mode:
            if self._load_remote_file(new_path):
                self._update_nav_buttons()
                self.show_status_message(
                    f"Loaded first file: {os.path.basename(new_path)}"
                )
            return

        # Reset HPC state
        self._reset_hpc_state()

        # Load the file
        self.imagename = new_path
        self.dir_entry.setText(new_path)
        self.contour_settings["contour_data"] = None
        self.current_contour_wcs = None
        # Clear figure to prevent restoring old view limits
        self.figure.clear()
        self.on_visualization_changed(dir_load=True)
        self.update_tab_name_from_path(new_path)

        self._update_nav_buttons()
        self.show_status_message(f"Loaded first file: {os.path.basename(new_path)}")

    def _on_last_file(self):
        """Load the last file in the file list"""
        if not self._file_list or self._file_list_index >= len(self._file_list) - 1:
            return

        new_index = len(self._file_list) - 1
        new_path = self._file_list[new_index]
        self._file_list_index = new_index

        # Check if in remote mode
        if self._is_remote_mode:
            if self._load_remote_file(new_path):
                self._update_nav_buttons()
                self.show_status_message(
                    f"Loaded last file: {os.path.basename(new_path)}"
                )
            return

        # Reset HPC state
        self._reset_hpc_state()

        # Load the file
        self.imagename = new_path
        self.dir_entry.setText(new_path)
        self.contour_settings["contour_data"] = None
        self.current_contour_wcs = None
        # Clear figure to prevent restoring old view limits
        self.figure.clear()
        self.on_visualization_changed(dir_load=True)
        self.update_tab_name_from_path(new_path)

        self._update_nav_buttons()
        self.show_status_message(f"Loaded last file: {os.path.basename(new_path)}")

    def _show_filter_dialog(self):
        """Show a dialog to set the file filter pattern"""
        import fnmatch

        from PyQt5.QtWidgets import QInputDialog

        pattern, ok = QInputDialog.getText(
            self,
            "File Filter Pattern",
            "Enter glob pattern to filter files:\n(e.g., *-image.fits, chunk_*.fits, *.image)",
            text=self._file_filter_pattern,
        )

        if ok:
            self._file_filter_pattern = pattern if pattern else "*"

            # Check if in remote mode
            if self._is_remote_mode and self._remote_file_list:
                # Filter the remote file list using the pattern
                filtered = [
                    e
                    for e in self._remote_file_list
                    if fnmatch.fnmatch(e.name, self._file_filter_pattern)
                ]
                self._file_list = [e.path for e in filtered]

                # Find current file in filtered list
                current_basename = (
                    os.path.basename(self.imagename) if self.imagename else ""
                )
                self._file_list_index = -1
                for i, e in enumerate(filtered):
                    if e.name == current_basename:
                        self._file_list_index = i
                        break

                self._update_nav_buttons()
            else:
                # Local mode - rescan directory
                self._scan_directory_files(rescan_only=True)

            if self._file_list:
                self.show_status_message(
                    f"Found {len(self._file_list)} files matching '{self._file_filter_pattern}'"
                )
            else:
                self.show_status_message(
                    f"No files found matching '{self._file_filter_pattern}'"
                )

            # If playlist dialog is open, refresh it to show new filtered list
            if hasattr(self, "_playlist_dialog") and self._playlist_dialog:
                try:
                    if self._playlist_dialog.isVisible():
                        # Close and re-open to refresh with new filter/list
                        # This is the simplest way to ensure all widgets (badge, list) are updated
                        # We use QTimer to avoid potential issues with closing/opening in the same event
                        QTimer.singleShot(
                            0, lambda: self._show_playlist_dialog(force_refresh=True)
                        )
                except RuntimeError:
                    self._playlist_dialog = None

    def _show_playlist_dialog(self, force_refresh=False):
        """Show a non-modal dialog listing all files in the current file list"""
        from PyQt5.QtWidgets import (
            QDialog,
            QFrame,
            QHBoxLayout,
            QLabel,
            QListWidget,
            QPushButton,
            QVBoxLayout,
        )

        # Check if file list exists
        if not self._file_list and not self._is_remote_mode:
            self.show_status_message("No files found. Load an image first.")
            return

        # If dialog already exists and is visible, just raise it (unless force_refresh is True)
        if hasattr(self, "_playlist_dialog") and self._playlist_dialog is not None:
            try:
                if self._playlist_dialog.isVisible():
                    if force_refresh:
                        self._playlist_dialog.close()
                    else:
                        self._playlist_dialog.raise_()
                        self._playlist_dialog.activateWindow()
                        return
            except RuntimeError:
                # Dialog was deleted, create a new one
                self._playlist_dialog = None

        dialog = QDialog(self)
        dialog.setWindowTitle("File List")
        set_hand_cursor(dialog)
        dialog.setMinimumSize(550, 700)

        # Store reference to prevent garbage collection
        self._playlist_dialog = dialog

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header with title and file count
        header_layout = QHBoxLayout()
        header_layout.setSpacing(12)

        title_label = QLabel("📋 File List")
        title_label.setObjectName("DialogTitle")
        header_layout.addWidget(title_label)

        header_layout.addStretch()

        info_badge = QLabel(
            f"Filter: {self._file_filter_pattern}  │  {len(self._file_list)} files"
        )
        info_badge.setObjectName("InfoBadge")
        header_layout.addWidget(info_badge)

        layout.addLayout(header_layout)

        # Search box
        search_frame = QFrame()
        search_frame.setObjectName("SearchFrame")
        search_layout = QHBoxLayout(search_frame)
        search_layout.setContentsMargins(10, 0, 10, 0)
        search_layout.setSpacing(8)
        search_layout.setAlignment(Qt.AlignVCenter)

        search_label = QLabel("🔍")
        search_label.setObjectName("SearchIcon")
        search_input = QLineEdit()
        search_input.setObjectName("SearchInput")
        search_input.setPlaceholderText("Search files...")
        search_input.setClearButtonEnabled(True)
        search_input.setFixedHeight(28)

        search_layout.addWidget(search_label, 0, Qt.AlignVCenter)
        search_layout.addWidget(search_input, 1, Qt.AlignVCenter)
        layout.addWidget(search_frame)

        # File list
        file_list_widget = QListWidget()
        file_list_widget.setObjectName("FileListWidget")

        # Store original file list for filtering
        original_items = []
        for i, path in enumerate(self._file_list):
            filename = os.path.basename(path)
            item_text = f"{i + 1}. {filename}"
            file_list_widget.addItem(item_text)
            original_items.append((i, item_text, path))

        # Search/filter function
        def filter_files(search_text):
            search_text = search_text.lower().strip()
            file_list_widget.clear()

            if not search_text:
                # Show all items
                for idx, item_text, path in original_items:
                    file_list_widget.addItem(item_text)
                # Restore selection
                if self._file_list_index >= 0:
                    file_list_widget.setCurrentRow(self._file_list_index)
            else:
                # Filter items
                for idx, item_text, path in original_items:
                    filename = os.path.basename(path).lower()
                    if search_text in filename:
                        file_list_widget.addItem(item_text)

        search_input.textChanged.connect(filter_files)

        # Highlight current file
        if self._file_list_index >= 0:
            file_list_widget.setCurrentRow(self._file_list_index)

        layout.addWidget(file_list_widget, stretch=1)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        open_btn = QPushButton("Open")
        open_btn.setObjectName("PrimaryButton")
        open_btn.setDefault(True)
        open_btn.setCursor(Qt.PointingHandCursor)

        close_btn = QPushButton("Close")
        close_btn.setCursor(Qt.PointingHandCursor)

        # Store references for use in callbacks
        viewer = self

        def on_open():
            """Handle opening a selected file with proper error handling"""
            try:
                # Check if viewer is still valid
                if not viewer or not hasattr(viewer, "_file_list"):
                    dialog.close()
                    return

                # Get selected item
                current_item = file_list_widget.currentItem()
                if not current_item:
                    viewer.show_status_message("Please select a file first.")
                    return

                # Extract original index from item text (format: "N. filename")
                item_text = current_item.text()
                try:
                    original_idx = (
                        int(item_text.split(".")[0]) - 1
                    )  # Convert to 0-based index
                except (ValueError, IndexError):
                    viewer.show_status_message("Error parsing file index.")
                    return

                if original_idx < 0 or original_idx >= len(viewer._file_list):
                    viewer.show_status_message("Invalid file selection.")
                    return

                new_path = viewer._file_list[original_idx]

                # Check if in remote mode
                if viewer._is_remote_mode:
                    # Remote mode - download and load
                    viewer._file_list_index = original_idx
                    if viewer._load_remote_file(new_path):
                        viewer._update_nav_buttons()
                        viewer.show_status_message(
                            f"Loaded {os.path.basename(new_path)}"
                        )
                        # Try to update dialog, but handle case where user closed it
                        try:
                            search_input.clear()
                            file_list_widget.setCurrentRow(original_idx)
                        except RuntimeError:
                            # Dialog was closed during download, that's okay
                            pass
                    return

                # Local mode - check if file still exists
                if not os.path.exists(new_path):
                    viewer.show_status_message(
                        f"File no longer exists: {os.path.basename(new_path)}"
                    )
                    return

                viewer._file_list_index = original_idx

                # Reset HPC state
                viewer._reset_hpc_state()

                # Load the file
                viewer.imagename = new_path
                viewer.dir_entry.setText(new_path)
                viewer.contour_settings["contour_data"] = None
                viewer.current_contour_wcs = None
                # Clear figure to prevent restoring old view limits
                viewer.figure.clear()
                viewer.on_visualization_changed(dir_load=True)
                viewer.update_tab_name_from_path(new_path)

                viewer._update_nav_buttons()
                viewer.show_status_message(f"Loaded {os.path.basename(new_path)}")

                # Clear search and refresh list to show current selection
                search_input.clear()

            except RuntimeError as e:
                # Viewer was deleted
                dialog.close()
            except Exception as e:
                try:
                    viewer.show_status_message(f"Error loading file: {str(e)}")
                except:
                    pass

        def on_double_click(item):
            on_open()

        def on_close():
            dialog.close()

        def on_dialog_destroyed():
            """Clean up reference when dialog is destroyed"""
            if getattr(viewer, "_playlist_dialog", None) == dialog:
                viewer._playlist_dialog = None

        open_btn.clicked.connect(on_open)
        close_btn.clicked.connect(on_close)
        file_list_widget.itemDoubleClicked.connect(on_double_click)

        btn_layout.addStretch()
        btn_layout.addWidget(open_btn)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

        # Apply modern styling
        palette = theme_manager.palette
        window = palette["window"]
        base = palette["base"]
        surface = palette["surface"]
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
        gradient_start = palette.get("button_gradient_start", highlight)
        gradient_end = palette.get("button_gradient_end", highlight_hover)

        dialog.setStyleSheet(f"""
            QDialog {{
                background-color: {window};
            }}
            
            QLabel#DialogTitle {{
                font-size: 14pt;
                font-weight: 700;
                color: {text};
            }}
            
            QLabel#InfoBadge {{
                font-size: 10pt;
                color: {text_secondary};
                padding: 4px 10px;
                background-color: {surface};
                border: 1px solid {border};
                border-radius: 6px;
            }}
            
            QFrame#SearchFrame {{
                background-color: {surface};
                border: 1px solid {border};
                border-radius: 8px;
                min-height: 34px;
                max-height: 34px;
            }}
            
            QLabel#SearchIcon {{
                font-size: 12pt;
                padding: 0px;
                margin: 0px;
            }}
            
            QLineEdit#SearchInput {{
                padding: 4px 8px;
                font-size: 10pt;
                border: none;
                background-color: transparent;
                color: {text};
            }}
            
            QListWidget#FileListWidget {{
                border: 1px solid {border};
                border-radius: 10px;
                background-color: {base};
                color: {text};
                font-size: 10.5pt;
                outline: none;
                padding: 4px;
            }}
            
            QListWidget#FileListWidget::item {{
                padding: 6px 8px;
                border-radius: 4px;
                margin: 1px 2px;
            }}
            
            QListWidget#FileListWidget::item:selected {{
                background-color: {highlight};
                color: #ffffff;
            }}
            
            QListWidget#FileListWidget::item:hover:!selected {{
                background-color: {button_hover};
            }}
            
            QPushButton {{
                padding: 6px 16px;
                font-size: 10pt;
                font-weight: 500;
                border: 1px solid {border};
                border-radius: 6px;
                background-color: {button};
                color: {text};
                min-width: 70px;
                min-height: 24px;
            }}
            
            QPushButton:hover {{
                background-color: {button_hover};
                border-color: {highlight};
            }}
            
            QPushButton:pressed {{
                background-color: {button_pressed};
            }}
            
            QPushButton#PrimaryButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {gradient_start}, stop:1 {gradient_end});
                color: #ffffff;
                border: none;
                font-weight: 600;
            }}
            
            QPushButton#PrimaryButton:hover {{
                background-color: {highlight_hover};
            }}
            
            QLabel {{
                color: {text};
            }}
        """)

        # Set up dialog for non-modal behavior
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(on_dialog_destroyed)

        # Track dialog for garbage collection
        dialog.destroyed.connect(
            lambda: (
                self._open_dialogs.remove(dialog)
                if dialog in self._open_dialogs
                else None
            )
        )
        self._open_dialogs.append(dialog)

        # Show as non-modal
        dialog.show()

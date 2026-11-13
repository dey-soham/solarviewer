"""
Opening files and stepping through a directory (local or remote).
"""

import logging
from contextlib import nullcontext
import os
import time

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QLineEdit,
)

from ..processing.fits_io import SPECTRUM, fits_kind, is_fits
from ..ui.activity import track_thread
from ..ui.background import Cancelled, run_in_thread
from ..ui.errors import show_error
from ..ui.open_image import choose_image
from ..ui.recent_files import forget_image, last_image_folder, remember_image
from ..ui.styles import SPACE_S, set_hand_cursor
from ..ui.widgets import dialog_layout

logger = logging.getLogger(__name__)


from ..ui.geometry import fit_to_screen

# A server silent for this long (s) while listing a folder is given up on
LISTING_TIMEOUT = 60
# A key's step this soon after the last one ended (s) came in behind it
STEP_BEHIND_S = 0.015


def _is_image_table(path):
    """Whether the folder ``path`` is a CASA image, however it is named."""
    try:
        with open(os.path.join(path, "table.info")) as f:
            return "Image" in f.readline()
    except (OSError, UnicodeDecodeError):
        return False


def _own_pattern(path):
    """The glob of what is named like ``path``: by its extension, as written
    (.FITS, .fit, .fits.gz, .im), or anything if it has none."""
    stem, extension = os.path.splitext(os.path.basename(path))
    if extension.lower() in (".gz", ".fz"):
        extension = os.path.splitext(stem)[1] + extension
    return f"*{extension}"


def _images_in_folder(folder, pattern):
    """The FITS files and CASA images in ``folder`` matching the glob
    ``pattern``, sorted. On a thread: a network disk can take its time."""
    import fnmatch

    images = []
    for item in os.listdir(folder):
        item_path = os.path.join(folder, item)

        # Check if it matches the filter pattern
        if not fnmatch.fnmatch(item, pattern):
            continue

        # Include FITS files
        if is_fits(item_path):
            images.append(item_path)
        # Include CASA images (a Measurement Set is a table too)
        elif _is_image_table(item_path):
            images.append(item_path)
    # Sort alphabetically
    return sorted(images)


class FileNavigationMixin:
    """File selection, directory scanning, prev/next/first/last, filter and playlist.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``contour_layers``, ``dir_entry``, ``figure``, ``_file_pos_label``,
    ``_first_file_btn``, ``_last_file_btn``, ``_next_file_btn``,
    ``on_visualization_changed``, ``_open_dialogs``, ``_prev_file_btn``,
    ``_load_target_size``, ``_reset_hpc_state``,
    ``show_status_message``, ``stokes_combo``, ``threshold_entry``,
    ``update_tab_name_from_path``, ``window``
    """

    # Remote folder listings still running (one replaced by a newer listing
    # runs on to its end), kept from the garbage collector until they end:
    # a QThread dropped while it runs aborts the app
    _remote_listings: set = set()
    # The download cancelled last, which stops only at its next data, so
    # kept likewise (see _await_ending_download)
    _ending_download = None
    _last_step_end = 0.0  # when the last next/previous step ended

    def _stepped(self, kind):
        """The page hears of a step ("prev", "next", "first", "last"): the
        panes following this one step too (core/compare_sync.py). The
        keys [ ] { } are the page's, for its active pane."""
        page = getattr(self, "page", None)
        if page is not None:
            page.sync.frame_stepped(self, kind)

    def select_file_or_directory(self):
        # Check if we're in remote mode - if so, use remote file browser
        main_window = self.window()
        if hasattr(main_window, "remote_connection") and main_window.remote_connection:
            if main_window.remote_connection.is_connected():
                # Use remote file browser instead of local dialog
                self._select_remote_file(main_window)
                return

        # The current image's folder, else that of the last image opened
        start_dir = last_image_folder()
        if self.imagename and not self._is_remote_mode:
            start_dir = os.path.dirname(self.imagename.rstrip("/"))
        path = choose_image(self, directory=start_dir)
        if path:
            self.open_path(path)

    def open_path(self, path):
        """Open ``path`` where it belongs: an image in this tab, a dynamic
        spectrum in the Dynamic Spectrum Viewer (see the window's
        ``open_path``)."""
        main_window = self.window()
        if hasattr(main_window, "open_path"):
            return main_window.open_path(path, tab=self)
        return self.open_image_path(path)

    def open_image_path(self, path):
        """Show the local image ``path`` (a FITS file or CASA image) in this
        tab. Returns whether it loaded (None if the user cancelled); if so it
        goes first in Open Recent."""
        import time

        start_time = time.time()
        self._reset_hpc_state()  # Clear HPC state for new image
        self._is_remote_mode = False
        self._remote_file_list = []
        self._remote_base_dir = None
        self.imagename = path
        self.dir_entry.setText(path)
        # Clear contour data so it gets reloaded for new image
        self.invalidate_contours()
        # Clear figure to prevent restoring old view limits
        self.figure.clear()
        # In a page, the image opens to the frame and view of the panes it
        # follows (once its folder is scanned: a conversion renames it)
        page = getattr(self, "page", None)
        with page.sync.joining(self) if page is not None else nullcontext():
            loaded = self.on_visualization_changed(dir_load=True)
            self.update_tab_name_from_path(path)  # Update tab name
            if loaded:
                remember_image(path)
                self.show_status_message(
                    f"Loaded {path} in {time.time() - start_time:.2f} seconds"
                )
            elif loaded is False:  # unreadable (None: the user cancelled)
                forget_image(path)
            # Scan directory for file navigation
            self._scan_directory_files()
        return loaded

    def _select_remote_file(self, main_window):
        """
        Open the remote file browser when in remote mode.
        This is called by select_file_or_directory when connected to a remote server.
        """
        self.browse_remote(
            main_window,
            lambda local_path, is_remote, folder: self.show_remote_image(
                main_window, local_path, is_remote, folder
            ),
            parent=self,
        )

    @staticmethod
    def browse_remote(main_window, on_image, parent=None):
        """The remote file browser: ``on_image(local_path, is_remote,
        folder)`` with the image chosen (downloaded); a dynamic spectrum goes
        to the Dynamic Spectrum Viewer."""
        try:
            from ..remote import RemoteFileBrowser

            # Either kind of image: a FITS file or a CASA image folder
            browser = RemoteFileBrowser(
                main_window.remote_connection,
                cache=main_window.remote_cache,
                parent=parent or main_window,
                casa_mode=None,
                can_switch_server=hasattr(main_window, "_on_remote_server_changed"),
            )
            if hasattr(main_window, "_on_remote_server_changed"):
                browser.serverChanged.connect(main_window._on_remote_server_changed)

            def on_file_selected(local_path, is_remote):
                if not local_path or not os.path.exists(local_path):
                    return
                if fits_kind(local_path) == SPECTRUM:
                    main_window.open_spectrum(local_path)
                    return
                on_image(local_path, is_remote, browser.current_path)

            browser.fileSelected.connect(on_file_selected)
            browser.exec_()
            # (it may have reconnected)
            for update in ("_update_remote_menu_state", "_update_remote_status_indicator"):
                if hasattr(main_window, update):
                    getattr(main_window, update)()

        except Exception:
            import traceback

            show_error(
                parent or main_window,
                "Could not open the remote browser",
                "The window could not be opened. Check the connection and try again.",
                traceback.format_exc(),
            )

    def show_remote_image(self, main_window, local_path, is_remote, folder):
        """Show ``local_path``, downloaded from the server's ``folder`` (or a
        local file picked in the remote browser, ``is_remote`` False), then
        step through that folder. Returns what on_visualization_changed
        does (True once shown)."""
        import time

        start_time = time.time()
        self._reset_hpc_state()
        self.imagename = local_path
        self.dir_entry.setText(local_path)
        self.invalidate_contours()
        self.figure.clear()
        # In a page, the image opens to the frame and view of the panes it
        # follows
        page = getattr(self, "page", None)
        with page.sync.joining(self) if page is not None else nullcontext():
            loaded = self.on_visualization_changed(dir_load=True)
            self.update_tab_name_from_path(local_path)

        # Set mode based on what was selected
        self._is_remote_mode = is_remote
        elapsed = time.time() - start_time
        basename = os.path.basename(local_path)
        if is_remote:
            self._remote_base_dir = folder
            # Populate remote file list for navigation
            self._populate_remote_file_list(
                main_window, folder, os.path.isdir(local_path), local_path
            )
            self.show_status_message(f"Loaded remote file: {basename} ({elapsed:.2f}s)")
        else:
            # Clear remote state if we selected a local file
            self._remote_base_dir = None
            self._remote_file_list = []

            # Scan local directory for navigation
            self._scan_directory_files()
            self.show_status_message(f"Loaded local file: {basename} ({elapsed:.2f}s)")
        return loaded

    def _scan_directory_files(self, rescan_only=False):
        """Scan the base directory for FITS/image files matching the filter pattern

        Args:
            rescan_only: If True, don't change the filter pattern (user manually set it)
        """
        import os

        if not self.imagename:
            return

        # Ensure we're in local mode when scanning local directory
        self._is_remote_mode = False
        if hasattr(self, "_remote_file_list"):
            self._remote_file_list = []
        if hasattr(self, "_remote_base_dir"):
            self._remote_base_dir = None

        # The folder holding the image (a CASA image is a folder, and a path
        # to one may end in a slash)
        imagename = self.imagename.rstrip("/")
        self._file_base_dir = os.path.dirname(imagename)
        default_pattern = _own_pattern(imagename)

        if not self._file_base_dir:
            self._file_base_dir = "."

        # Always update filter pattern to match current file's extension when opening a new file
        if not rescan_only:
            # Update filter pattern to match the extension of the currently opened file
            self._file_filter_pattern = default_pattern

        # Get all items in directory
        self._scanning_folder = True
        try:
            self._file_list = run_in_thread(
                self,
                f"Listing {os.path.basename(self._file_base_dir.rstrip('/')) or self._file_base_dir}…",
                _images_in_folder,
                self._file_base_dir,
                self._file_filter_pattern,
            )
        except Exception as e:
            if isinstance(e, Cancelled):
                self.show_status_message("Listing the folder cancelled")
            else:
                logger.error(f"Error scanning directory: {e}")
                self.show_status_message(f"Error scanning directory: {e}")
            self._file_list = []
            self._file_list_index = -1
            self._update_nav_buttons()
            return
        finally:
            self._scanning_folder = False

        # Find current file index
        current_path = os.path.abspath(self.imagename)
        self._file_list_index = -1
        for i, path in enumerate(self._file_list):
            if os.path.abspath(path) == current_path:
                self._file_list_index = i
                break

        self._update_nav_buttons()
        if getattr(self, "_prefetch_after_scan", False):
            self._prefetch_after_scan = False
            self._prefetch_neighbour_files()

    def _prefetch_neighbour_files(self):
        """Start reading the files either side of the current one in the
        background, with the settings the next load will use."""
        from ..processing.prefetch import image_prefetcher

        if getattr(self, "_scanning_folder", False):
            self._prefetch_after_scan = True  # (of the folder it is listing)
            return
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
            self.sun_limit(),
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
        self, main_window, remote_dir, casa_mode, current_local_path, on_done=None
    ):
        """Populate the file list from a remote directory for navigation
        (async). ``on_done()``, if given, is called when that has ended,
        however it went."""
        # Start async population - don't block UI
        from PyQt5.QtCore import QThread, pyqtSignal

        class PopulateThread(QThread):
            listed = pyqtSignal(list, int)  # entries, current_index
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
                    try:
                        stdin, stdout, stderr = self.connection.exec_command(
                            cmd, timeout=LISTING_TIMEOUT
                        )

                        # Read output
                        out_data = stdout.read().decode("utf-8").strip()
                    except Exception:
                        out_data = ""  # (a server that runs no commands: SFTP only)

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
                        except Exception:
                            pass

                    # FALLBACK: If fast path failed (no python, etc), use slower SFTP loop
                    if not success:
                        if self.connection.is_connected():
                            self._sftp = self.connection.open_sftp()
                            self._sftp.get_channel().settimeout(LISTING_TIMEOUT)
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

                    self.listed.emit(entries, current_idx)

                except Exception as e:
                    self.error.emit(str(e))
                finally:
                    if self._sftp:
                        try:
                            self._sftp.close()
                        except Exception:
                            pass

        try:
            if (
                not main_window.remote_connection
                or not main_window.remote_connection.is_connected()
            ):
                if on_done is not None:
                    on_done()
                return

            current_basename = os.path.basename(current_local_path)

            thread = PopulateThread(
                main_window.remote_connection, remote_dir, casa_mode, current_basename
            )
            self._populate_thread = thread

            def current():
                """Whether this is the newest listing (an older one may end
                after it), of the folder the tab still steps through."""
                return thread is self._populate_thread and self._remote_base_dir == remote_dir

            def on_listed(entries, current_idx):
                if not current():
                    return
                try:
                    self._remote_file_list = entries
                    self._file_list = [e.path for e in entries]
                    self._file_list_index = current_idx
                    self._update_nav_buttons()
                except RuntimeError:
                    pass  # Widget deleted

            def on_error(msg):
                if not current():
                    return
                logger.warning(f"Failed to populate remote file list: {msg}")
                self._remote_file_list = []
                self._file_list = []
                self._file_list_index = -1
                try:
                    self._update_nav_buttons()
                    self.show_status_message(f"Could not list {remote_dir}: {msg}")
                except RuntimeError:
                    pass

            thread.listed.connect(on_listed)
            thread.error.connect(on_error)
            track_thread(thread, f"Listing {remote_dir}…", self)
            FileNavigationMixin._remote_listings.add(thread)
            thread.finished.connect(
                lambda t=thread: FileNavigationMixin._remote_listings.discard(t)
            )
            if on_done is not None:
                thread.finished.connect(on_done)
            thread.start()

        except Exception as e:
            logger.warning(f"Failed to start remote file list population: {e}")
            self._remote_file_list = []
            self._file_list = []
            self._file_list_index = -1
            self._update_nav_buttons()
            if on_done is not None:
                on_done()

    def _load_remote_file(self, remote_path):
        """Download and load a remote file for navigation. Returns what
        on_visualization_changed does (True once shown, None if the user
        cancelled), False if it could not be downloaded."""
        # The download's dialog leaves the file list's window usable
        if getattr(self, "_loading_remote", False):
            self.show_status_message("Still loading the last image")
            return False
        self._loading_remote = True
        try:
            return self._fetch_and_show_remote_file(remote_path)
        finally:
            self._loading_remote = False

    def _fetch_and_show_remote_file(self, remote_path):
        from pathlib import Path

        main_window = self.window()
        if not main_window or not hasattr(main_window, "remote_connection"):
            return False

        if (
            not main_window.remote_connection
            or not main_window.remote_connection.is_connected()
        ):
            self.show_status_message("Not connected to the server")
            return False

        try:
            from PyQt5.QtCore import QEventLoop, Qt, QTimer
            from PyQt5.QtWidgets import QProgressDialog

            from ..remote.download_thread import DownloadThread

            self._await_ending_download()

            # Get the remote file info (on a thread: with the window held up
            # here, the health check could not notice a dead connection)
            name = os.path.basename(remote_path)
            entry = run_in_thread(
                self,
                f"Checking {name} on the server…",
                main_window.remote_connection.get_file_info,
                remote_path,
            )

            # Check cache first
            cached_path = main_window.remote_cache.get_cached_path(
                main_window.remote_connection.host,
                remote_path,
                entry.mtime,
                entry.size,
            )

            if cached_path:
                local_path = str(cached_path)
            else:
                # Download the file with progress dialog (NON-BLOCKING Main Thread)
                local_path = main_window.remote_cache.get_cache_path(
                    main_window.remote_connection.host,
                    remote_path,
                )

                # Create progress dialog
                progress = QProgressDialog(
                    f"Downloading {name}…",
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
                                f"Downloading {name}…\n"
                                f"{transferred / (1024**2):.1f} / {total / (1024**2):.1f} MB"
                            )
                        else:
                            progress.setLabelText(
                                f"Downloading {name}…\n"
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
                    # At once: the thread sees this only when data next
                    # arrives, which on a stalled link may be never
                    download_thread.cancel()
                    download_result["cancelled"] = True
                    loop.quit()

                download_thread.progress.connect(on_progress)
                download_thread.phase.connect(progress.setLabelText)
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
                # (Closing the dialog is also heard as Cancel)
                progress.canceled.disconnect(on_cancel)
                download_thread.progress.disconnect(on_progress)
                progress.close()
                if download_result.get("cancelled"):
                    FileNavigationMixin._ending_download = download_thread
                else:
                    download_thread.wait()  # Ensure thread is joined

                if not download_result["success"]:
                    if download_result["error"]:
                        raise Exception(download_result["error"])
                    else:
                        raise InterruptedError("Download cancelled")  # User cancelled

                local_path = download_result["path"]

                # Mark as cached
                main_window.remote_cache.mark_cached(
                    main_window.remote_connection.host,
                    remote_path,
                    Path(local_path),
                    entry.mtime,
                    entry.size,
                )

            # Load the file: a dynamic spectrum in its own viewer
            if fits_kind(local_path) == SPECTRUM:
                return main_window.open_spectrum(local_path)
            self._reset_hpc_state()
            self.imagename = local_path
            self.dir_entry.setText(local_path)
            self.invalidate_contours()
            self.figure.clear()
            shown = self.on_visualization_changed(dir_load=True)
            self.update_tab_name_from_path(local_path)

            return shown

        except InterruptedError:
            # User cancelled the download
            self.show_status_message("Download cancelled")
            return None
        except Cancelled:
            self.show_status_message("Cancelled")
            return None
        except Exception as e:
            logger.error(f"Failed to load remote file: {e}")
            self.show_status_message(f"Failed to load remote file: {e}")
            return False

    def _await_ending_download(self):
        """Wait for the download cancelled last, if it runs yet: it replaces
        the shared SFTP channel when it stops, under any transfer started
        before that, and it writes to the same cached file. Cancelling this
        wait raises Cancelled."""
        from ..remote.download_thread import TRANSFER_STALL_TIMEOUT

        thread = FileNavigationMixin._ending_download
        if thread is None or thread.isFinished():
            return
        # (A stalled link ends it at its timeout, then a reconnect)
        limit_ms = (TRANSFER_STALL_TIMEOUT + 60) * 1000
        if not run_in_thread(self, "Stopping the last download…", thread.wait, limit_ms):
            raise RuntimeError("the last download has not stopped yet, try again in a moment")

    def _load_file_index(self, new_index, done="Loaded"):
        """Load file ``new_index`` of the file list (as next/previous do);
        once it is shown, the status bar says ``done`` and its name. A
        dynamic spectrum in the list goes to the Dynamic Spectrum Viewer and
        the tab keeps its image."""
        new_path = self._file_list[new_index]
        if not self._is_remote_mode and fits_kind(new_path) == SPECTRUM:
            main_window = self.window()
            if hasattr(main_window, "open_spectrum"):
                return main_window.open_spectrum(new_path)
            return False
        before = (self._file_list_index, self.imagename)
        self._file_list_index = new_index

        # What the step keeps of how the image is looked at: the view (zoom
        # and pan, and with it where the regions and annotations lie), the
        # sky frame and unit it was converted to, and with the range lock
        # on the display (read here: a converted image is loaded twice, as
        # the file is and then converted)
        frame, unit, unit_first = self._converted_view()
        self._step_view = self._view_shown(turned=frame is not None)
        self._step_display = self._shown_display() if self.range_locked else None
        # The file as it is stays unseen when a converted view follows it
        held = (frame or unit) and self.canvas.updatesEnabled()
        if held:
            self.canvas.setUpdatesEnabled(False)
        try:
            if self._is_remote_mode:
                shown = self._load_remote_file(new_path)
                if not shown and self.imagename == before[1]:
                    self._file_list_index = before[0]  # the image stayed
            else:
                self._reset_hpc_state()
                self.imagename = new_path
                self.dir_entry.setText(new_path)
                self.invalidate_contours()
                # Cleared: the view of the image before is put back by the
                # drawing itself, where the new one fits it (_step_view)
                self.figure.clear()
                shown = self.on_visualization_changed(dir_load=True)
                self.update_tab_name_from_path(new_path)
            if shown:
                self._convert_as_before(frame, unit, unit_first)
        finally:
            view, self._step_view = self._step_view, None
            self._step_display = None
            self._marks_carried = None
            # (still on the file as it is: its conversion did not come)
            unconverted, self._passing_image = getattr(self, "_passing_image", False), False
            if held:
                self.canvas.setUpdatesEnabled(True)
                self.canvas.update()

        if unconverted and shown:
            self._settle_marks(view)
        self._update_nav_buttons()
        if shown:
            self.show_status_message(self.with_range_note(f"{done} {os.path.basename(new_path)}"))
        return shown

    def _converted_view(self):
        """(sky frame, unit, unit first) the image shown was converted to
        with the RA/Dec | Solar XY and Jy/beam | K controls: "radec" or
        "solar", "jy" or "k", each None where it is shown as the file is;
        and, converted both ways, whether the unit was converted first."""
        native = getattr(self, "_native_view", None)
        if native is None or self.current_image_data is None:
            return None, None, False
        kind, scale = self._view_kind()
        frame = kind if kind and native[0] and kind != native[0] else None
        unit = scale if scale and native[1] and scale != native[1] else None
        # (the unit's conversion is shown itself when it came last)
        unit_first = bool(frame and unit) and self.imagename != getattr(self, "_tb_temp_file", None)
        return frame, unit, unit_first

    def _convert_as_before(self, frame, unit, unit_first=False):
        """The image just loaded, converted to the sky frame and unit the
        one before it was shown in (``_converted_view``), as the controls
        would, and in the order they did: each control then goes back the
        way it went for the image before. A conversion that fails says so
        in the status bar, and the other is not tried; one cancelled
        leaves the image as the file is, and the next step then converts
        nothing."""
        conversions = [(self._to_frame, frame), (self._to_unit, unit)]
        if unit_first:
            conversions.reverse()
        for convert, wanted in conversions:
            if wanted and not convert(wanted):
                return

    def _to_frame(self, frame):
        """Show the image in the sky frame ``frame``; whether it is."""
        kind = self._view_kind()[0]
        if kind and kind != frame and self.hpc_btn.isEnabled():
            quiet, self._quiet_conversion = getattr(self, "_quiet_conversion", False), True
            try:
                self.launch_helioprojective_viewer()
            finally:
                self._quiet_conversion = quiet
        return self._view_kind()[0] == frame

    def _to_unit(self, unit):
        """Show the image in ``unit`` ("jy" or "k"); whether it is."""
        scale = self._view_kind()[1]
        if scale and scale != unit and self.tb_btn.isEnabled():
            self._toggle_tb_mode()
        return self._view_kind()[1] == unit

    def _step_file(self, start, step, done="Loaded"):
        """Load the first image from ``start`` on, ``step`` places at a time:
        next/previous step over dynamic spectra, which have their own viewer."""
        index = start
        while 0 <= index < len(self._file_list):
            if self._is_remote_mode or fits_kind(self._file_list[index]) != SPECTRUM:
                return self._load_file_index(index, done)
            index += step
        return False

    def _behind_last_step(self):
        """Whether this is a key's step that came in behind the last one: the
        repeats of a held key wait out a slow image, and would go on stepping
        long after it is let go. (Each click of a button counts.)"""
        now = time.monotonic()
        behind = self.sender() is None and now - self._last_step_end < STEP_BEHIND_S
        if behind:
            self._last_step_end = now
        return behind

    def _step_and_tell(self, start, step, kind, done="Loaded"):
        """Step, and the panes following this one step too unless the user
        cancelled. Returns what _step_file does; the _on_*_file methods
        return False when there was nothing to do, so None always means
        cancelled."""
        # (the panes that step with this one take its range once they
        # have: compare_sync's stepping and frame_stepped)
        page = getattr(self, "page", None)
        with page.sync.stepping(self) if page is not None else nullcontext():
            shown = self._step_file(start, step, done)
        if shown is not None:
            self._stepped(kind)
        if kind in ("prev", "next"):
            self._last_step_end = time.monotonic()
        return shown

    def _on_prev_file(self):
        """Load the previous file in the file list"""
        if self._file_list_index <= 0 or self._behind_last_step():
            return False
        return self._step_and_tell(self._file_list_index - 1, -1, "prev")

    def _on_next_file(self):
        """Load the next file in the file list"""
        if (
            self._file_list_index < 0
            or self._file_list_index >= len(self._file_list) - 1
            or self._behind_last_step()
        ):
            return False
        return self._step_and_tell(self._file_list_index + 1, 1, "next")

    def _on_first_file(self):
        """Load the first file in the file list"""
        if not self._file_list or self._file_list_index <= 0:
            return False
        return self._step_and_tell(0, 1, "first", "Loaded first file:")

    def _on_last_file(self):
        """Load the last file in the file list"""
        if not self._file_list or self._file_list_index >= len(self._file_list) - 1:
            return False
        return self._step_and_tell(len(self._file_list) - 1, -1, "last", "Loaded last file:")

    def _show_filter_dialog(self):
        """Show a dialog to set the file filter pattern"""
        import fnmatch

        from PyQt5.QtWidgets import QInputDialog

        pattern, ok = QInputDialog.getText(
            self,
            "File filter pattern",
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
            QHBoxLayout,
            QLabel,
            QListWidget,
            QPushButton,
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
        dialog.setWindowTitle("File list")
        set_hand_cursor(dialog)
        fit_to_screen(dialog, 550, 700)

        # Store reference to prevent garbage collection
        self._playlist_dialog = dialog

        layout = dialog_layout(dialog)

        # Search box, with the filter and file count beside it
        header_layout = QHBoxLayout()
        header_layout.setSpacing(SPACE_S)

        search_input = QLineEdit()
        search_input.setPlaceholderText("Search files…")
        search_input.setClearButtonEnabled(True)
        header_layout.addWidget(search_input, 1)

        info_badge = QLabel(
            f"Filter: {self._file_filter_pattern} · {len(self._file_list)} files"
        )
        info_badge.setObjectName("Badge")
        header_layout.addWidget(info_badge)

        layout.addLayout(header_layout)

        # File list
        file_list_widget = QListWidget()

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
        btn_layout.setSpacing(SPACE_S)

        open_btn = QPushButton("Open")
        open_btn.setObjectName("PrimaryButton")
        open_btn.setDefault(True)
        open_btn.setCursor(Qt.PointingHandCursor)

        close_btn = QPushButton("Close")
        close_btn.setAutoDefault(False)
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

                # A local file may have gone since the list was made
                if not viewer._is_remote_mode and not os.path.exists(new_path):
                    viewer.show_status_message(
                        f"File no longer exists: {os.path.basename(new_path)}"
                    )
                    return

                remote = viewer._is_remote_mode
                if viewer._load_file_index(original_idx):
                    # Clear search and refresh list to show current selection
                    try:
                        search_input.clear()
                        if remote:
                            file_list_widget.setCurrentRow(original_idx)
                    except RuntimeError:
                        # Dialog was closed during the download, that's okay
                        pass

            except RuntimeError:
                # Viewer was deleted
                dialog.close()
            except Exception as e:
                try:
                    viewer.show_status_message(f"Error loading file: {str(e)}")
                except Exception:
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
        btn_layout.addWidget(close_btn)
        btn_layout.addWidget(open_btn)

        layout.addLayout(btn_layout)

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

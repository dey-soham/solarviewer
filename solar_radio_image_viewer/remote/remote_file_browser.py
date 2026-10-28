"""
The file browser of SolarViewer's remote mode: a folder of the SSH server,
or of this computer, to open an image from.

One path bar to click or type in; the connection, places and recent
folders on the left; the folder's files in the middle; the selected file's
details on the right, its FITS header read only when asked; and an Open
button that names what it opens. A server's files are fetched into the
RemoteFileCache before they are opened. The pieces are in browser_parts.
"""

import os
import posixpath
import sys
import time
from pathlib import Path
from typing import List, Optional

from PyQt5.QtCore import QSettings, QSize, QStringListModel, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QIcon, QKeySequence
from PyQt5.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QCompleter,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QShortcut,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .browser_parts import (
    CASA,
    ENTRY_ROLE,
    FITS,
    FOLDERS,
    IMAGES,
    KIND_ROLE,
    MODIFIED,
    SPECTRUM,
    DetailsPanel,
    FileListModel,
    FileListView,
    HeaderDialog,
    PathBar,
    PlacesPanel,
    casa_named,
    count_text,
    fits_named,
    header_kind,
    header_rows,
    icon,
    pixmap,
    progress_text,
)
from .download_thread import DownloadThread
from .file_cache import RemoteFileCache
from .ssh_manager import RemoteFileInfo, SSHConnection, SSHConnectionError
from ..ui.geometry import fit_to_screen
from ..ui.styles import set_hand_cursor, theme_manager
from ..ui.widgets import segmented


def _base_finished(thread):
    """QThread's own finished signal of ``thread``, whose class may have
    a ``finished`` of its own (with its result)."""
    return QThread.finished.__get__(thread, QThread)


def _silence(thread):
    """Disconnect ``thread``'s own result signals (not QThread's finished)."""
    for name in ("finished", "error", "done", "failed", "result", "progress"):
        if name in type(thread).__dict__:
            try:
                getattr(thread, name).disconnect()
            except (TypeError, RuntimeError):
                pass


def _deleted(obj):
    from PyQt5 import sip

    return sip.isdeleted(obj)


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
            except Exception:
                pass

    def run(self):
        try:
            # FAST PATH: Try executing a python script on the remote server
            try:
                import json
                import shlex

                # Uses os.scandir (Python 3.5+, faster) with os.listdir fallback (Python 3.4)
                # Uses "OK:" prefix to distinguish success from script failure.
                # A folder holding a CASA table gets its type ('t', from its
                # table.info), a CASA image the size of its whole folder.
                py_script = """
import os, json, sys, stat
path = sys.argv[1]
entries = []
show_hidden = %s
fits_only = %s

def table_type(folder):
    try:
        with open(os.path.join(folder, 'table.info')) as f:
            line = f.readline()
    except (IOError, OSError, UnicodeError):
        return ''
    key, _, value = line.partition('=')
    return value.strip() if key.strip() == 'Type' else ''

def tree_size(folder):
    total = 0
    for root, dirs, files in os.walk(folder):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total

def add_entry(name, is_dir, size, mtime):
    if not show_hidden and name.startswith('.'):
        return
    if fits_only and not is_dir:
        if not name.lower().endswith(('.fits', '.fts', '.fit')):
            return
    entry = {'n': name, 'd': is_dir, 's': size, 'm': mtime}
    if is_dir:
        full = os.path.join(path, name)
        entry['t'] = table_type(full)
        if entry['t'] == 'Image':
            entry['s'] = tree_size(full)
    entries.append(entry)

try:
    # Fast path: os.scandir (Python 3.5+) - single syscall per entry
    try:
        scanner = os.scandir(path)
    except AttributeError:
        scanner = None

    if scanner is not None:
        for e in scanner:
            try:
                s = e.stat()
                add_entry(e.name, e.is_dir(), s.st_size, s.st_mtime)
            except OSError:
                continue  # (a broken link)
    else:
        # Fallback: os.listdir + os.stat (Python 3.4)
        for name in os.listdir(path):
            full = os.path.join(path, name)
            try:
                s = os.stat(full)
            except OSError:
                continue
            add_entry(name, stat.S_ISDIR(s.st_mode), s.st_size, s.st_mtime)

    sys.stdout.write("OK:" + json.dumps(entries))
except Exception as exc:
    sys.stderr.write(str(exc))
    sys.exit(1)
""" % (
                    "True" if self.show_hidden else "False",
                    "True" if self.fits_only else "False",
                )

                # Try python3 first
                cmd = f"python3 -c {shlex.quote(py_script)} {shlex.quote(self.path)}"
                stdin, stdout, stderr = self.connection.exec_command(cmd)

                # Check for cancellation before reading
                if self._cancelled:
                    return

                out_data = stdout.read().decode("utf-8").strip()
                stderr.read()
                exit_status = stdout.channel.recv_exit_status()

                if exit_status == 0 and out_data.startswith("OK:"):
                    try:
                        json_str = out_data[3:]  # Strip "OK:" prefix
                        raw_entries = json.loads(json_str)
                        entries = []
                        for item in raw_entries:
                            if self._cancelled:
                                return

                            full_path = posixpath.join(self.path, item["n"])
                            info = RemoteFileInfo(
                                name=item["n"],
                                path=full_path,
                                is_dir=item["d"],
                                size=item["s"],
                                mtime=item["m"],
                                table_type=item.get("t") if item["d"] else None,
                            )
                            entries.append(info)

                        # Sort and emit
                        entries.sort(key=lambda x: (not x.is_dir, x.name.lower()))
                        self.finished.emit(entries)
                        return  # Success!
                    except Exception as e:
                        print(f"[SSH-List] Fast path JSON parse failed: {e}")
                else:
                    print("[SSH-List] Fast path failed, falling back to SFTP")
            except Exception as e:
                print(f"[SSH-List] Fast path exception: {e}, falling back to SFTP")

            # FALLBACK: Use SFTP loop (slow for large dirs)
            print(f"[SSH-List] SFTP fallback: listing {self.path}")
            # Create our own SFTP channel for this thread
            if self.connection.is_connected():
                self._sftp = self.connection.open_sftp()
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
                full_path = posixpath.join(self.path, name)

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
            print(f"[SSH-List] ERROR: {e}")
            if not self._cancelled:
                self.error.emit(str(e))
        finally:
            if self._sftp:
                try:
                    self._sftp.close()
                except Exception:
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
            except Exception:
                pass

    def run(self):
        try:
            if not self.connection.is_connected():
                return

            self._sftp = self.connection.open_sftp()

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
                except Exception:
                    pass
                self._sftp = None


def _table_type(folder):
    """The type of CASA table the local ``folder`` holds ("Image"...), or ""."""
    try:
        with open(os.path.join(folder, "table.info"), errors="replace") as f:
            line = f.readline()
    except OSError:
        return ""
    key, _, value = line.partition("=")
    return value.strip() if key.strip() == "Type" else ""


def _tree_size(folder):
    total = 0
    for root, _dirs, files in os.walk(folder):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def list_local(path, cancelled=lambda: False):
    """The entries of the local folder ``path``, as a server's are listed:
    a folder holding a CASA table has its type, a CASA image the size of
    its whole folder."""
    entries = []
    with os.scandir(path) as scanner:
        for item in scanner:
            if cancelled():
                break
            try:
                is_dir = item.is_dir()
                info = item.stat()
            except OSError:
                continue  # (a broken link)
            table, size = None, info.st_size
            if is_dir:
                table = _table_type(item.path)
                if table == "Image":
                    size = _tree_size(item.path)
            entries.append(RemoteFileInfo(item.name, item.path, is_dir, size, info.st_mtime, table))
    return entries


class LocalListThread(QThread):
    """Lists a folder of this computer (a network mount can be slow)."""

    finished = pyqtSignal(list)  # list of RemoteFileInfo
    error = pyqtSignal(str)

    def __init__(self, path):
        super().__init__()
        self.path = path
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            entries = list_local(self.path, lambda: self._cancelled)
        except OSError as e:
            if not self._cancelled:
                self.error.emit(e.strerror or str(e))
            return
        if not self._cancelled:
            self.finished.emit(entries)


class HeaderThread(QThread):
    """Reads the FITS header at the start of a file (of the server with
    ``connection``, else of this computer), 2880-byte block by block to
    its END card: never the image."""

    done = pyqtSignal(str)  # the header's text
    failed = pyqtSignal(str)

    def __init__(self, path, connection=None):
        super().__init__()
        self.path = path
        self.connection = connection

    def run(self):
        from .remote_files import read_header

        gzipped = self.path.lower().endswith(".gz")
        try:
            if self.connection is None:
                with open(self.path, "rb") as raw:
                    text = read_header(raw, gzipped=gzipped)
            else:
                sftp = self.connection.open_sftp()
                try:
                    with sftp.open(self.path, "rb") as raw:
                        text = read_header(raw, gzipped=gzipped)
                finally:
                    sftp.close()
        except Exception as e:
            self.failed.emit(str(e) or type(e).__name__)
            return
        self.done.emit(text)


class ReconnectThread(QThread):
    """Connects again with the parameters the connection was made with."""

    done = pyqtSignal(bool)

    def __init__(self, connection):
        super().__init__()
        self.connection = connection

    def run(self):
        try:
            ok = self.connection.reconnect()
        except Exception as e:
            self.connection.last_error = str(e)
            ok = False
        self.done.emit(bool(ok))


class HomeThread(QThread):
    """Finds the server's home folder."""

    result = pyqtSignal(str)

    def __init__(self, connection):
        super().__init__()
        self.connection = connection

    def run(self):
        try:
            home = self.connection.get_home_directory()
        except Exception:
            home = ""
        self.result.emit(home or "/")


class RemoteFileBrowser(QDialog):
    """
    File browser dialog for navigating remote directories via SFTP.

    Signals:
        fileSelected(str, bool): the local path of the image chosen (a
            server's fetched into the cache first) and whether it came
            from the server
        pathPicked(str): with pick="folder" / "file", the server path
            chosen; nothing is fetched
        serverChanged(object): the SSHConnection made with "Switch server…"
            (only offered with ``can_switch_server``)
    """

    fileSelected = pyqtSignal(str, bool)  # (local_path, is_remote)
    # With pick="folder" / "file": the server path chosen, nothing downloaded
    pathPicked = pyqtSignal(str)
    serverChanged = pyqtSignal(object)

    # Class-level variable to remember last browsed directory per host
    _last_paths: dict = {}  # {host: last_path}

    # Class-level cache for directory listings: {(user@host:port, path): (entries, timestamp)}
    _listing_cache: dict = {}
    _cache_ttl: int = 60  # Cache TTL in seconds

    # Class-level list to keep track of active download threads to prevent garbage collection
    _active_downloads: List[QThread] = []
    # The other threads still running, kept from the garbage collector
    _running: set = set()

    # For the session: headers read and what they showed a file to be,
    # {(source, path, mtime, size): header text / kind}
    _headers: dict = {}
    _kinds: dict = {}
    _order = (MODIFIED, Qt.DescendingOrder)  # the list's sort
    _last_local = ""  # the local folder last shown

    RECENT = 6  # recent folders kept, per server and for this computer

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
        casa_mode: Optional[bool] = None,
        pick: Optional[str] = None,
        can_switch_server: bool = False,
    ):
        super().__init__(parent)
        # "folder": choose a folder; "file": choose an image (a FITS file or
        # a CASA image folder); both emit pathPicked with the server path
        # instead of downloading anything
        self.pick = pick
        # What opens: True CASA images, False FITS files, None any image
        self.casa_mode = casa_mode
        self.can_switch_server = can_switch_server
        self.connection = connection
        self.cache = cache or RemoteFileCache()
        self.source = "remote"  # or "local"
        self.current_path = "/"  # the server folder shown
        self._local_path = ""  # this computer's
        self._back = {"remote": [], "local": []}
        self._select_after = None  # a path to select once its folder is listed
        self._list_token = 0
        self._list_thread = None
        self._list_failed = False
        self._fallback_home = False
        self._download_thread: Optional[DownloadThread] = None
        self._download_entry = None
        self._download_target = None
        self._reading = None  # the key of the header being read
        self._header_errors = {}
        self._reconnect_thread = None
        self._home_thread = None
        self._autocomplete_thread: Optional[AutocompleteThread] = None
        self._mine = []  # the threads started here
        self._connected_seen = connection.is_connected()

        self.setModal(True)
        self.setMinimumSize(760, 480)
        fit_to_screen(self, 1040, 660)
        self._build()
        self._update_title()
        self._apply_icons()
        theme_manager.register_callback(self._on_theme_changed, self)
        set_hand_cursor(self)

        self._auto_timer = QTimer(self)  # reads a header once the selection rests
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(self._read_header)
        self._autocomplete_timer = QTimer(self)
        self._autocomplete_timer.setSingleShot(True)
        self._autocomplete_timer.timeout.connect(self._complete)
        self._status_timer = QTimer(self)  # notices a connection lost or back
        self._status_timer.timeout.connect(self._poll_connection)
        self._status_timer.start(3000)

        self._update_connection()
        self._update_cache()
        QTimer.singleShot(0, self._load_initial_directory)

    # ------------------------------------------------------------ layout ----

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_toolbar())
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.places = PlacesPanel()
        self.places.placeChosen.connect(self._on_place)
        self.places.unpinRequested.connect(self._toggle_pin)
        self.places.reconnectRequested.connect(self._reconnect)
        self.places.switchRequested.connect(self._switch_server)
        self.places.clearCacheRequested.connect(self._clear_cache)
        body.addWidget(self.places)
        body.addWidget(self._build_list(), 1)
        self.details = DetailsPanel()
        self.details.readRequested.connect(self._read_header)
        self.details.fullHeaderRequested.connect(self._show_full_header)
        self.details.auto_switch.setChecked(
            QSettings("SolarViewer", "SolarViewer").value("remote_browser/auto_header", False, type=bool)
        )
        self.details.auto_switch.toggled.connect(self._on_auto_toggled)
        body.addWidget(self.details)
        layout.addLayout(body, 1)
        layout.addWidget(self._build_footer())

    def _tool(self, name, tip, slot):
        button = QToolButton()
        button.setAutoRaise(True)
        button.setFixedSize(28, 28)
        button.setIconSize(QSize(15, 15))
        button.setToolTip(tip)
        button.setCursor(Qt.PointingHandCursor)
        button.setFocusPolicy(Qt.NoFocus)
        button.clicked.connect(slot)
        self._icons.append((button, name))
        return button

    def _build_toolbar(self):
        self._icons = []
        bar = QFrame()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(48)
        row = QHBoxLayout(bar)
        row.setContentsMargins(14, 0, 14, 0)
        row.setSpacing(10)

        # The same dialog on the server's files or this computer's
        self.remote_button = QPushButton("Remote")
        self.local_button = QPushButton("Local")
        group = QButtonGroup(self)
        for button, source in ((self.remote_button, "remote"), (self.local_button, "local")):
            button.setCheckable(True)
            button.setAutoDefault(False)
            button.setFocusPolicy(Qt.NoFocus)
            group.addButton(button)
            button.clicked.connect(lambda _checked, s=source: self._set_source(s))
        self.remote_button.setChecked(True)
        self.remote_button.setToolTip("The server's files")
        self.local_button.setToolTip("This computer's files")
        self.source_switch = segmented([self.remote_button, self.local_button], role="HeaderSegmented")
        # (picking a server path, there is nothing to take from here)
        self.source_switch.setVisible(self.pick is None)
        row.addWidget(self.source_switch)

        nav = QHBoxLayout()
        nav.setSpacing(2)
        self.back_button = self._tool("back", "Back (Alt+Left)", self._go_back)
        self.up_button = self._tool("up", "Up one level (Alt+Up)", self._go_up)
        self.home_button = self._tool("home", "Home", self._go_home)
        for button in (self.back_button, self.up_button, self.home_button):
            nav.addWidget(button)
        row.addLayout(nav)

        self.path_bar = PathBar()
        self.path_bar.pathChosen.connect(self._on_path_chosen)
        self.path_bar.edit.textEdited.connect(lambda _text: self._autocomplete_timer.start(250))
        self._completions = QStringListModel(self)
        self._completer = QCompleter(self._completions, self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.setMaxVisibleItems(10)
        self._completer.activated[str].connect(self._on_path_chosen)
        self.path_bar.edit.setCompleter(self._completer)
        row.addWidget(self.path_bar, 1)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter this folder")
        self.filter_edit.setFixedWidth(170)
        self.filter_edit.setClearButtonEnabled(True)
        self._search_action = self.filter_edit.addAction(QIcon(), QLineEdit.LeadingPosition)
        self.filter_edit.textChanged.connect(self._on_filter)
        self.filter_edit.installEventFilter(self)
        row.addWidget(self.filter_edit)
        self.refresh_button = self._tool("refresh", "Refresh (F5)", lambda: self._refresh(force=True))
        row.addWidget(self.refresh_button)
        self.toolbar = bar

        for keys, slot in (
            ("Ctrl+L", self.path_bar.start_editing),
            ("Alt+Left", self._go_back),
            ("Alt+Up", self._go_up),
            ("F5", lambda: self._refresh(force=True)),
            ("Ctrl+F", self._focus_filter),
        ):
            QShortcut(QKeySequence(keys), self, slot)
        return bar

    def _build_list(self):
        column = QWidget()
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.model = FileListModel(self)
        self.view = FileListView()
        self.view.setModel(self.model)
        sort_column, order = RemoteFileBrowser._order
        self.model.order = (sort_column, order)
        self.view.header().setSortIndicator(sort_column, order)
        self.view.setSortingEnabled(True)
        self.view.header().sortIndicatorChanged.connect(self._on_sorted)
        self.view.selectionModel().selectionChanged.connect(lambda *_: self._on_selection_changed())
        self.view.openRequested.connect(self._open_index)
        self.view.upRequested.connect(self._go_up)
        self.view.customContextMenuRequested.connect(self._list_menu)
        layout.addWidget(self.view, 1)

        strip = QFrame()
        strip.setObjectName("CanvasBar")
        row = QHBoxLayout(strip)
        row.setContentsMargins(16, 8, 16, 8)
        row.setSpacing(16)
        self.images_only = QCheckBox("Images only")
        self.images_only.setObjectName("Switch")
        self.images_only.setToolTip("Show only folders and the images SolarViewer opens: FITS files, CASA images and spectra")
        self.images_only.setChecked(True)
        self.images_only.toggled.connect(self._on_filter_switches)
        row.addWidget(self.images_only)
        self.show_hidden = QCheckBox("Hidden files")
        self.show_hidden.setObjectName("Switch")
        self.show_hidden.setToolTip("Show files and folders whose names start with a dot")
        self.show_hidden.toggled.connect(self._on_filter_switches)
        row.addWidget(self.show_hidden)
        row.addStretch()
        self.count_label = QLabel()
        self.count_label.setObjectName("Meta")
        row.addWidget(self.count_label)
        layout.addWidget(strip)
        self.list_column = column
        return column

    def _build_footer(self):
        footer = QFrame()
        footer.setObjectName("CanvasBar")
        footer.setFixedHeight(52)
        self.footer_pages = QStackedLayout(footer)
        self.footer_pages.setContentsMargins(0, 0, 0, 0)

        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(16, 0, 16, 0)
        row.setSpacing(8)
        key = "⌘L" if sys.platform == "darwin" else "Ctrl+L"
        self._hint = f"Double-click a folder to open it · {key} to type a path"
        self.hint_label = QLabel(self._hint)
        self.hint_label.setObjectName("Meta")
        row.addWidget(self.hint_label, 1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("GhostButton")
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        row.addWidget(self.cancel_button)
        self.open_button = QPushButton("Open")
        self.open_button.setObjectName("PrimaryButton")
        self.open_button.setDefault(True)
        self.open_button.clicked.connect(self._open_selected)
        row.addWidget(self.open_button)
        self.footer_pages.addWidget(page)

        # While a file is fetched
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(16, 0, 16, 0)
        row.setSpacing(10)
        progress = QVBoxLayout()
        progress.setSpacing(5)
        progress.addStretch()
        line = QHBoxLayout()
        line.setSpacing(8)
        self.download_label = QLabel()
        self.download_label.setObjectName("Meta")
        line.addWidget(self.download_label, 1)
        self.download_amount = QLabel()
        self.download_amount.setObjectName("DetailMono")
        self.download_amount.setProperty("muted", "true")
        line.addWidget(self.download_amount)
        progress.addLayout(line)
        self.download_bar = QProgressBar()
        self.download_bar.setObjectName("ThinProgress")
        self.download_bar.setTextVisible(False)
        progress.addWidget(self.download_bar)
        progress.addStretch()
        row.addLayout(progress, 1)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setAutoDefault(False)
        self.stop_button.clicked.connect(self._stop_download)
        row.addWidget(self.stop_button)
        self.footer_pages.addWidget(page)
        return footer

    def _apply_icons(self):
        palette = theme_manager.palette
        for button, name in self._icons:
            button.setIcon(icon(name, palette))
        search = QIcon()
        for ratio in (1, 2):
            search.addPixmap(pixmap("search", palette["text_secondary"], 13, ratio))
        self._search_action.setIcon(search)

    def _on_theme_changed(self, _theme=None):
        self._apply_icons()
        self._refresh_details()
        self.view.viewport().update()

    def _update_title(self):
        if self.pick == "folder":
            title = "Choose a folder on the server"
        elif self.pick == "file":
            title = "Choose an image on the server"
        else:
            title = "Open remote image" if self.source == "remote" else "Open local image"
        self.setWindowTitle(title)

    # ------------------------------------------------------- where we are ----

    def _folder(self):
        return self.current_path if self.source == "remote" else self._local_path

    def _set_folder(self, path):
        if self.source == "remote":
            self.current_path = path
        else:
            self._local_path = path

    def _paths(self):
        """posixpath on the server, os.path here."""
        return posixpath if self.source == "remote" else os.path

    def _source_key(self):
        """Whose files are shown, for the places, recent folders and headers
        kept: "local", or the server's host (and port, if not 22: a tunnel's)."""
        if self.source == "local":
            return "local"
        port = self.connection.port
        return f"remote-{self.connection.host}" + ("" if port in (22, None) else f"-{port}")

    def _server_key(self):
        """The server, for the listing cache (a host may serve as several)."""
        connection = self.connection
        return f"{connection.username}@{connection.host}:{connection.port}"

    def _normalise(self, path):
        if self.source == "local":
            return os.path.normpath(os.path.abspath(os.path.expanduser(path)))
        return posixpath.normpath("/" + path.lstrip("/"))

    def _load_initial_directory(self):
        """The folder last shown on this server (its home folder if that
        is gone), else its home folder."""
        last = RemoteFileBrowser._last_paths.get(self.connection.host)
        if last:
            self._navigate(last, remember=False, fallback=True)
        elif self.connection.last_home:
            self._navigate(self.connection.last_home, remember=False)
        elif self.connection.is_connected():
            self.view.set_empty_text("Loading…")
            self._fetch_home(remember=False)
        else:
            self._navigate("/", remember=False)

    def _navigate(self, path, remember=True, select=None, fallback=False):
        """Show the folder ``path`` (of the server or of this computer, as
        shown), with ``select`` selected once it is listed; ``fallback``:
        the home folder instead if it cannot be listed."""
        path = self._normalise(path)
        self._fallback_home = fallback
        previous = self._folder()
        if remember and previous and previous != path:
            stack = self._back[self.source]
            stack.append(previous)
            del stack[:-50]
        self._set_folder(path)
        if self.source == "remote":
            RemoteFileBrowser._last_paths[self.connection.host] = path
        else:
            RemoteFileBrowser._last_local = path
        self._select_after = select
        self.path_bar.set_path(path, self._paths().split)
        self.filter_edit.blockSignals(True)
        self.filter_edit.clear()
        self.filter_edit.blockSignals(False)
        self.model.text = ""
        self.model.set_entries([])  # (the last folder's files go at once)
        self._on_selection_changed()
        self._update_nav()
        self._update_places()
        self._refresh()

    def _on_path_chosen(self, text):
        """A folder clicked in the path bar, or a path typed there: a FITS
        file's opens its folder with it selected."""
        paths = self._paths()
        text = text.strip()
        if self.source == "remote" and text.startswith("~") and self.connection.last_home:
            text = self.connection.last_home + text[1:]
        if not paths.isabs(os.path.expanduser(text) if self.source == "local" else text):
            text = paths.join(self._folder(), text)
        path = self._normalise(text)
        folder, name = paths.split(path)
        if name and fits_named(name):
            self._navigate(folder, select=path)
            return
        # Up to a folder above: the one come from, in it, selected
        current, separator = self._folder(), paths.sep
        child = None
        prefix = path.rstrip(separator) + separator
        if current != path and current.startswith(prefix):
            child = prefix + current[len(prefix):].split(separator)[0]
        self._navigate(path, select=child)

    def _update_nav(self):
        folder = self._folder()
        self.back_button.setEnabled(bool(self._back[self.source]))
        self.up_button.setEnabled(bool(folder) and self._paths().split(folder)[0] != folder)

    def _go_back(self):
        stack = self._back[self.source]
        if stack:
            self._navigate(stack.pop(), remember=False)

    def _go_up(self):
        folder = self._folder()
        parent = self._paths().split(folder)[0]
        if parent and parent != folder:
            self._navigate(parent, select=folder)

    def _go_home(self):
        if self.source == "local":
            self._navigate(os.path.expanduser("~"))
        elif self.connection.last_home:
            self._navigate(self.connection.last_home)
        elif self.connection.is_connected():
            self._fetch_home()

    def _fetch_home(self, remember=True):
        """Find the server's home folder and go there (``remember``: with
        the folder shown kept for Back)."""
        if self._home_thread is not None:
            return
        thread = HomeThread(self.connection)
        thread.result.connect(lambda home: self._on_home(home, remember))
        self._home_thread = thread
        self._start(thread)

    def _on_home(self, home, remember):
        if _deleted(self):
            return
        self._home_thread = None
        if self.source == "remote":
            self._navigate(home, remember=remember)

    def _set_source(self, source):
        """Browse the server's files ("remote") or this computer's ("local")."""
        self.remote_button.setChecked(source == "remote")
        self.local_button.setChecked(source == "local")
        if source == self.source:
            return
        self._cancel_listing()
        self._auto_timer.stop()
        self.source = source
        self._update_title()
        if source == "local":
            start = self._local_path or RemoteFileBrowser._last_local
            if not start or not os.path.isdir(start):
                last = QSettings("SolarViewer", "SolarViewer").value("last_image_folder", "")
                start = last if last and os.path.isdir(str(last)) else os.path.expanduser("~")
            self._navigate(str(start), remember=False)
        else:
            self._navigate(self.current_path, remember=False)

    def _focus_filter(self):
        self.filter_edit.setFocus(Qt.ShortcutFocusReason)
        self.filter_edit.selectAll()

    def eventFilter(self, watched, event):
        from PyQt5.QtCore import QEvent

        if watched is self.filter_edit and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Down):
                # Into the list, at its first match
                if self.model.rowCount():
                    self.view.setFocus()
                    if not self.view.selectionModel().selectedRows():
                        self.view.setCurrentIndex(self.model.index(0, 0))
                return True
            if event.key() == Qt.Key_Escape and self.filter_edit.text():
                self.filter_edit.clear()
                return True  # (not closing the dialog)
        return super().eventFilter(watched, event)

    # ----------------------------------------------------------- listing ----

    def _start(self, thread):
        """Start ``thread``, kept alive until it ends (closing the dialog
        does not wait for it)."""
        RemoteFileBrowser._running.add(thread)
        _base_finished(thread).connect(lambda t=thread: RemoteFileBrowser._running.discard(t))
        self._mine = [t for t in self._mine if t.isRunning()] + [thread]
        thread.start()

    def _cancel_listing(self):
        thread = self._list_thread
        self._list_thread = None
        self._list_token += 1
        if thread is not None and thread.isRunning():
            _silence(thread)
            thread.cancel()

    def _refresh(self, force: bool = False):
        """List the folder shown (a server's from the listing cache, unless
        ``force``d or older than a minute)."""
        self._cancel_listing()
        token = self._list_token
        folder = self._folder()
        if self.source == "remote":
            if not self.connection.is_connected():
                self._listing_failed(token, "", disconnected=True)
                return
            cached = RemoteFileBrowser._listing_cache.get((self._server_key(), folder))
            if cached is not None and not force and time.time() - cached[1] < self._cache_ttl:
                self._listed(token, cached[0], fresh=False)
                return
            thread = ListDirectoryThread(self.connection, folder, show_hidden=True, fits_only=False)
        else:
            thread = LocalListThread(folder)
        thread.finished.connect(lambda entries: self._listed(token, entries))
        thread.error.connect(lambda message: self._listing_failed(token, message))
        self._list_thread = thread
        self._start(thread)
        self.count_label.setText("Loading…")
        if not self.model.rowCount():
            self.view.set_empty_text("Loading…")

    def _listed(self, token, entries, fresh=True):
        if _deleted(self) or token != self._list_token:
            return
        self._list_thread = None
        self._list_failed = False
        self._fallback_home = False
        folder = self._folder()
        paths = self._paths()
        if fresh and self.source == "remote":
            RemoteFileBrowser._listing_cache[(self._server_key(), folder)] = (entries, time.time())
        # Inside a CASA image (its path typed): show it in its folder instead
        parent, name = paths.split(folder)
        if casa_named(name) and parent != folder and any(
            e.name == "table.info" and not e.is_dir for e in entries
        ):
            stack = self._back[self.source]
            if stack and stack[-1] == parent:
                stack.pop()
            self._navigate(parent, remember=False, select=folder)
            return
        selected = self._select_after or self._selected_path()
        self._select_after = None
        known = {}
        for entry in entries:
            kind = RemoteFileBrowser._kinds.get(self._header_key(entry))
            if kind is not None:
                known[entry.path] = kind
        self.model.set_entries(entries, self._cached_paths(entries), known)
        self._reselect(selected)
        self._update_counts()
        if self._connected_seen != self._is_connected():
            self._update_connection()

    def _listing_failed(self, token, message, disconnected=False):
        if _deleted(self) or token != self._list_token:
            return
        self._list_thread = None
        if self._fallback_home and not disconnected and self.connection.is_connected():
            # (the folder remembered is gone, or not this server's)
            self._fallback_home = False
            home = self.connection.last_home
            if home and home != self._folder():
                self._navigate(home, remember=False)
            else:
                self._fetch_home(remember=False)
            return
        self._list_failed = True
        self.model.set_entries([])
        self._reselect(None)
        self.count_label.setText("")
        if self.source == "remote" and (disconnected or not self.connection.is_connected()):
            self.view.set_empty_text("Not connected. Reconnect (on the left) to see the server’s files.")
        else:
            self.view.set_empty_text(f"Couldn’t open this folder: {message}")
        self._update_connection()

    def _cached_paths(self, entries):
        """The server files (and CASA images) with a copy in the cache."""
        if self.source != "remote":
            return set()
        host = self.connection.host
        return {
            e.path for e in entries
            if (not e.is_dir or casa_named(e.name) or e.table_type == "Image")
            and self.cache.is_cached(host, e.path, e.mtime, e.size)
        }

    def _on_filter(self, text):
        selected = self._selected_path()
        self.model.text = text.strip()
        self.model.refilter()
        self._reselect(selected)
        self._update_counts()

    def _on_filter_switches(self):
        selected = self._selected_path()
        self.model.images_only = self.images_only.isChecked()
        self.model.show_hidden = self.show_hidden.isChecked()
        self.model.refilter()
        self._reselect(selected)
        self._update_counts()

    def _on_sorted(self, column, order):
        RemoteFileBrowser._order = (column, order)

    def _update_counts(self):
        if self._list_failed:
            return
        counts = self.model.counts()
        self.count_label.setText(count_text(counts, bool(self.model.text)))
        if self.model.rowCount():
            self.view.set_empty_text("")
        elif self.model.text:
            self.view.set_empty_text(f"Nothing in this folder matches “{self.model.text}”.")
        elif counts["others_hidden"]:
            others = counts["others_hidden"]
            files = "1 other file" if others == 1 else f"{others} other files"
            self.view.set_empty_text(f"No images here. Turn off Images only to see {files}.")
        elif counts["hidden"]:
            self.view.set_empty_text("Only hidden files here. Turn on Hidden files to see them.")
        else:
            self.view.set_empty_text("This folder is empty.")

    # --------------------------------------------------------- selection ----

    def _selected(self):
        """(entry, kind) of the selected row, or (None, None)."""
        rows = self.view.selectionModel().selectedRows()
        if not rows:
            return None, None
        return rows[0].data(ENTRY_ROLE), rows[0].data(KIND_ROLE)

    def _selected_path(self):
        entry, _kind = self._selected()
        return entry.path if entry is not None else None

    def _reselect(self, path):
        row = self.model.row_of(path) if path else None
        if row is not None:
            index = self.model.index(row, 0)
            self.view.setCurrentIndex(index)
            self.view.scrollTo(index)
        else:
            self.view.clearSelection()
        # (a model reset clears the selection without saying so)
        self._on_selection_changed()

    def _on_selection_changed(self):
        entry, kind = self._selected()
        self._auto_timer.stop()
        self._refresh_details()
        self._update_open_button()
        if (
            entry is not None
            and kind in (FITS, SPECTRUM)
            and self.details.auto_switch.isChecked()
            and self._header_key(entry) not in RemoteFileBrowser._headers
            and self._reading != self._header_key(entry)
        ):
            self._auto_timer.start(300)

    def _openable(self, kind):
        if self.casa_mode is True:
            return kind == CASA
        if self.casa_mode is False:
            return kind in (FITS, SPECTRUM)
        return kind in IMAGES

    def _update_open_button(self):
        entry, kind = self._selected()
        hint = self._hint
        metrics = self.open_button.fontMetrics()

        def named(text, name):
            return text.format(metrics.elidedText(name, Qt.ElideMiddle, 320))

        if self.pick == "folder":
            if entry is not None and kind in FOLDERS:
                text, enabled = named("Use folder “{}”", entry.name), True
            else:
                text, enabled = "Use this folder", not self._list_failed
        elif entry is None:
            text, enabled = "Choose" if self.pick == "file" else "Open", False
        elif kind in FOLDERS:
            text, enabled = named("Open folder “{}”", entry.name), True
        elif self._openable(kind):
            text, enabled = named("Choose {}" if self.pick == "file" else "Open {}", entry.name), True
        else:
            text, enabled = "Open", False
            hint = (
                "Only FITS files, CASA images and spectra open here"
                if self.casa_mode is None
                else ("Only CASA images open here" if self.casa_mode else "Only FITS files open here")
            )
        self.open_button.setText(text)
        self.open_button.setEnabled(enabled)
        self.open_button.setToolTip(entry.path if entry is not None else "")
        self.hint_label.setText(hint)

    # ----------------------------------------------------------- details ----

    def _header_key(self, entry):
        return (self._source_key(), entry.path, entry.mtime, entry.size)

    def _refresh_details(self):
        entry, kind = self._selected()
        if entry is None:
            self.details.show_nothing()
            return
        cached = None
        if self.source == "remote" and (not entry.is_dir or kind == CASA):
            cached = self.model.is_cached(entry.path)
        self.details.show_entry(entry, kind, cached, self._paths().dirname(entry.path))
        if kind not in (FITS, SPECTRUM):
            self.details.show_header("hidden")
            return
        key = self._header_key(entry)
        text = RemoteFileBrowser._headers.get(key)
        if text is not None:
            try:
                self.details.show_header("read", header_rows(text))
            except Exception as e:
                self.details.show_header("failed", error=str(e))
        elif self._reading == key:
            self.details.show_header("reading")
        elif key in self._header_errors:
            self.details.show_header("failed", error=self._header_errors[key])
        else:
            self.details.show_header("unread")

    def _read_header(self):
        """Read the selected FITS file's header (only its first blocks)."""
        entry, kind = self._selected()
        if entry is None or kind not in (FITS, SPECTRUM):
            return
        key = self._header_key(entry)
        if key in RemoteFileBrowser._headers or self._reading == key:
            self._refresh_details()
            return
        if self.source == "remote" and not self.connection.is_connected():
            self._header_errors[key] = "not connected"
            self._refresh_details()
            return
        self._header_errors.pop(key, None)
        self._reading = key
        thread = HeaderThread(entry.path, self.connection if self.source == "remote" else None)
        thread.done.connect(lambda text: self._header_read(key, text))
        thread.failed.connect(lambda error: self._header_failed(key, error))
        self._start(thread)
        self._refresh_details()

    def _header_read(self, key, text):
        if _deleted(self):
            return
        RemoteFileBrowser._headers[key] = text
        if self._reading == key:
            self._reading = None
        try:
            kind = header_kind(text)
        except Exception:
            kind = None
        path = key[1]
        row = self.model.row_of(path) if key[0] == self._source_key() else None
        if kind is not None:
            RemoteFileBrowser._kinds[key] = kind
            if row is not None and self.model.index(row, 0).data(KIND_ROLE) != kind:
                selected = self._selected_path()
                self.model.set_kind(path, kind)
                self._reselect(selected)
                self._update_counts()
                return
        self._refresh_details()

    def _header_failed(self, key, error):
        if _deleted(self):
            return
        self._header_errors[key] = error
        if self._reading == key:
            self._reading = None
        self._refresh_details()

    def _show_full_header(self):
        entry, _kind = self._selected()
        text = RemoteFileBrowser._headers.get(self._header_key(entry)) if entry is not None else None
        if text is not None:
            HeaderDialog(entry.name, text, self).exec_()

    def _on_auto_toggled(self, on):
        QSettings("SolarViewer", "SolarViewer").setValue("remote_browser/auto_header", bool(on))
        self._on_selection_changed()

    # -------------------------------------------------- places and recent ----

    def _stored(self, kind):
        value = QSettings("SolarViewer", "SolarViewer").value(f"remote_browser/{kind}/{self._source_key()}", [])
        if isinstance(value, str):  # (a list of one comes back as a string)
            value = [value]
        return [str(path) for path in value or []]

    def _store(self, kind, paths):
        QSettings("SolarViewer", "SolarViewer").setValue(f"remote_browser/{kind}/{self._source_key()}", paths)

    def _update_places(self):
        if self.source == "local":
            home = os.path.expanduser("~")
        else:
            home = self.connection.last_home or "~"  # (found when clicked)
        folder = self._folder()
        self.places.set_places(home, self._stored("places"), folder)
        self.places.set_recent(self._stored("recent"), folder)

    def _on_place(self, path):
        if path == "~":
            self._go_home()
        else:
            self._navigate(path)

    def _toggle_pin(self, path):
        pinned = self._stored("places")
        self._store("places", [p for p in pinned if p != path] if path in pinned else pinned + [path])
        self._update_places()

    def _remember_recent(self, folder):
        recent = [folder] + [p for p in self._stored("recent") if p != folder]
        self._store("recent", recent[: self.RECENT])

    def _list_menu(self, position):
        index = self.view.indexAt(position)
        menu = QMenu(self)
        clipboard = QApplication.clipboard()
        if index.isValid():
            entry, kind = index.data(ENTRY_ROLE), index.data(KIND_ROLE)
            if kind in FOLDERS:
                menu.addAction("Open", lambda: self._navigate(entry.path))
                pinned = entry.path in self._stored("places")
                menu.addAction(
                    "Unpin from Places" if pinned else "Pin to Places",
                    lambda: self._toggle_pin(entry.path),
                )
            elif self._openable(kind) and self.pick != "folder":
                menu.addAction(
                    "Choose" if self.pick == "file" else "Open", lambda: self._open_entry(entry, kind)
                )
            if kind in (FITS, SPECTRUM):
                menu.addAction("Read header", self._read_header)
            menu.addAction("Copy path", lambda: clipboard.setText(entry.path))
        else:
            folder = self._folder()
            pinned = folder in self._stored("places")
            menu.addAction(
                "Unpin this folder" if pinned else "Pin this folder to Places",
                lambda: self._toggle_pin(folder),
            )
            menu.addAction("Copy path", lambda: clipboard.setText(folder))
        menu.exec_(self.view.viewport().mapToGlobal(position))

    # ------------------------------------------------- connection, cache ----

    def _is_connected(self):
        return self.connection.is_connected()

    def _update_connection(self):
        connection = self.connection
        self._connected_seen = connection.is_connected()
        if self._reconnect_thread is not None:
            self.places.show_disconnected("", reconnecting=True)
        elif self._connected_seen:
            detail = f"{connection.username} · port {connection.port}" if connection.username else f"port {connection.port}"
            self.places.show_connected(connection.host, detail, self.can_switch_server)
        else:
            error = (connection.last_error or "").strip()
            if "timed out" in error.lower():
                reason = f"Timed out after {connection.timeout:.0f} s"
            elif error:
                reason = error.splitlines()[0][:90]
            else:
                reason = "The server stopped answering."
            self.places.show_disconnected(reason)

    def _poll_connection(self):
        connected = self.connection.is_connected()
        if connected == self._connected_seen:
            return
        self._update_connection()
        if connected and self.source == "remote" and self._list_failed:
            self._refresh(force=True)

    def _reconnect(self):
        if self._reconnect_thread is not None:
            return
        thread = ReconnectThread(self.connection)
        thread.done.connect(self._reconnected)
        self._reconnect_thread = thread
        self._start(thread)
        self._update_connection()

    def _reconnected(self, ok):
        if _deleted(self):
            return
        self._reconnect_thread = None
        self._update_connection()
        if ok and self.source == "remote":
            self._refresh(force=True)

    def _switch_server(self):
        """Connect to another server and browse it (the main window takes
        the new connection: serverChanged)."""
        from .connection_dialog import ConnectionDialog

        made = []
        dialog = ConnectionDialog(self)
        dialog.connectionEstablished.connect(made.append)
        dialog.exec_()
        if not made or made[0] is self.connection:
            return
        self._cancel_listing()
        self._auto_timer.stop()
        self.connection = made[0]
        self._back["remote"] = []
        self.serverChanged.emit(self.connection)
        self._update_connection()
        if self.source != "remote":
            self._set_source("remote")
            return
        self.current_path = "/"
        self._load_initial_directory()

    def _update_cache(self):
        size, count = self.cache.get_cache_size()
        self.places.set_cache(size, count)

    def _clear_cache(self):
        size, count = self.cache.get_cache_size()
        if not count:
            return
        from .browser_parts import size_text

        files = "1 file" if count == 1 else f"{count} files"
        answer = QMessageBox.question(
            self, "Clear Cache",
            f"Delete the copies of {files} ({size_text(size)}) fetched from servers?\n\n"
            "They are fetched again when next opened.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.cache.clear_cache()
        self.model.set_cached(set())
        self._update_cache()
        self._refresh_details()

    # ----------------------------------------------------------- opening ----

    def _open_index(self, index):
        if index.isValid():
            self._open_entry(index.data(ENTRY_ROLE), index.data(KIND_ROLE))

    def _open_selected(self):
        entry, kind = self._selected()
        if self.pick == "folder":
            folder = entry.path if entry is not None and kind in FOLDERS else self._folder()
            if not self._list_failed or folder != self._folder():
                self._pick(folder, folder)
            return
        if entry is not None:
            self._open_entry(entry, kind)

    def _open_entry(self, entry, kind):
        """Open a folder (go into it) or an image (fetched first if it is
        the server's), or pick it."""
        if kind in FOLDERS:
            self._navigate(entry.path)
            return
        if self.pick == "folder" or not self._openable(kind):
            return
        folder = self._paths().dirname(entry.path)
        if self.pick == "file":
            self._pick(entry.path, folder)
            return
        self._remember_recent(folder)
        if self.source == "local":
            self.fileSelected.emit(entry.path, False)  # is_remote = False
            self.accept()
            return
        self._download_and_open(entry)

    def _pick(self, path, folder):
        self._remember_recent(folder)
        self.pathPicked.emit(path)
        self.accept()

    def _download_and_open(self, entry: RemoteFileInfo):
        """Fetch ``entry`` into the cache (unless a current copy is there),
        then say so and close."""
        host = self.connection.host
        self.current_path = posixpath.dirname(entry.path) or "/"
        cached_path = self.cache.get_cached_path(host, entry.path, entry.mtime, entry.size)
        if cached_path:
            self.fileSelected.emit(str(cached_path), True)  # is_remote = True
            self.accept()
            return
        if not self.connection.is_connected():
            self._update_connection()
            QMessageBox.warning(
                self, "Not Connected",
                "The connection to the server was lost. Reconnect (on the left) and try again.",
            )
            return
        # (A stopped download opens a new SFTP channel itself, in its thread:
        # opening one here would hold the window still while the server is slow)
        local_path = self.cache.get_cache_path(host, entry.path)
        thread = DownloadThread(self.connection, entry.path, str(local_path), is_directory=entry.is_dir)
        thread.progress.connect(self._on_download_progress)
        thread.finished.connect(self._on_download_finished)
        thread.error.connect(self._on_download_error)
        # Kept from the garbage collector until it ends, whatever becomes of this
        RemoteFileBrowser._active_downloads.append(thread)
        _base_finished(thread).connect(lambda t=thread: RemoteFileBrowser._remove_active_thread(t))
        self._download_thread = thread
        self._download_entry = entry
        self._download_target = local_path
        self._set_downloading(True)
        thread.start()

    def _set_downloading(self, downloading):
        """The footer's progress and Stop while a file is fetched, with the
        rest of the dialog held still."""
        self.footer_pages.setCurrentIndex(1 if downloading else 0)
        for widget in (self.toolbar, self.places, self.list_column, self.details):
            widget.setEnabled(not downloading)
        if downloading:
            name = self._download_entry.name
            self.download_label.setText(
                f"Downloading {self.download_label.fontMetrics().elidedText(name, Qt.ElideMiddle, 360)}"
            )
            self.download_amount.setText("")
            self.download_bar.setRange(0, 0)  # (moving until the size is known)
            self.stop_button.setFocus()
        else:
            self.view.setFocus()

    def _on_download_progress(self, transferred: int, total: int):
        """Update download progress (works for both files and directories)."""
        if _deleted(self) or total <= 0:
            return
        self.download_bar.setRange(0, 1000)
        self.download_bar.setValue(int(1000 * transferred / total))
        self.download_amount.setText(progress_text(transferred, total))

    def _on_download_finished(self, local_path: str):
        """Mark the copy cached, say so and close."""
        if _deleted(self):
            return
        entry = self._download_entry
        self._download_thread = None
        self.cache.mark_cached(self.connection.host, entry.path, Path(local_path), entry.mtime, entry.size)
        self.fileSelected.emit(local_path, True)
        self.accept()

    def _on_download_error(self, error_msg: str):
        if _deleted(self):
            return
        entry = self._download_entry
        self._download_thread = None
        self._set_downloading(False)
        self._update_connection()
        QMessageBox.warning(self, "Download Failed", f"Couldn’t fetch {entry.name}:\n\n{error_msg}")

    def _stop_download(self):
        """Stop fetching: what was fetched so far is deleted once the
        transfer has stopped."""
        thread = self._download_thread
        if thread is None:
            return
        self._download_thread = None
        _silence(thread)
        thread.cancel()
        cache, target, entry = self.cache, self._download_target, self._download_entry
        _base_finished(thread).connect(
            lambda: RemoteFileBrowser._discard_partial(cache, target, entry.is_dir, entry.name)
        )
        self._set_downloading(False)

    @staticmethod
    def _discard_partial(cache, local_path, is_directory, name):
        """Delete what a stopped download left in ``cache``: never anything
        outside it, a copy it lists, or a file being fetched again."""
        import shutil

        path = Path(local_path) / name if is_directory else Path(local_path)
        try:
            root = cache.cache_dir.resolve()
            resolved = path.resolve()
            if root not in resolved.parents or not resolved.exists():
                return
            if cache.remote_source(resolved) is not None:
                return
            if any(
                t.isRunning() and Path(t.local_path).resolve() == Path(local_path).resolve()
                for t in RemoteFileBrowser._active_downloads
            ):
                return
            if resolved.is_dir():
                shutil.rmtree(resolved)
            else:
                resolved.unlink()
        except OSError:
            pass

    # ---------------------------------------------------------- the path ----

    def _complete(self):
        """Offer the folders that complete the path typed."""
        edit = self.path_bar.edit
        text = edit.text().strip()
        if not text or not self.path_bar.editing:
            return
        if self.source == "local":
            text = os.path.expanduser(text)
            if text.endswith(os.sep):
                parent, partial = text, ""
            else:
                parent, partial = os.path.split(text)
            try:
                names = sorted(
                    (e.path for e in os.scandir(parent or os.sep)
                     if e.is_dir() and e.name.lower().startswith(partial.lower())
                     and (partial.startswith(".") or not e.name.startswith("."))),
                    key=str.lower,
                )
            except OSError:
                return
            self._show_completions(names[:200])
            return
        if text.endswith("/"):
            parent, partial = text.rstrip("/") or "/", ""
        else:
            parent, partial = posixpath.split(text)
            parent = parent or "/"
        cached = RemoteFileBrowser._listing_cache.get((self._server_key(), posixpath.normpath(parent)))
        if cached is not None and time.time() - cached[1] < self._cache_ttl:
            self._show_completions([
                e.path for e in cached[0]
                if e.is_dir and e.name.lower().startswith(partial.lower())
                and (partial.startswith(".") or not e.name.startswith("."))
            ])
            return
        if not self.connection.is_connected():
            return
        previous = self._autocomplete_thread
        if previous is not None and previous.isRunning():
            _silence(previous)
            previous.cancel()
        thread = AutocompleteThread(self.connection, parent, partial)
        thread.finished.connect(self._show_completions)
        self._autocomplete_thread = thread
        self._start(thread)

    def _show_completions(self, paths):
        if _deleted(self) or not self.path_bar.editing:
            return
        self._completions.setStringList(paths)
        if paths and self.path_bar.edit.hasFocus():
            self._completer.complete()

    # ----------------------------------------------------------- closing ----

    def _cleanup_threads(self):
        """Stop what is running for this dialog: its results are not wanted
        (a file being fetched is stopped and its part deleted)."""
        for timer in (self._auto_timer, self._autocomplete_timer, self._status_timer):
            timer.stop()
        if self._download_thread is not None:
            self._stop_download()
        self._cancel_listing()
        for thread in self._mine:
            if thread.isRunning():
                _silence(thread)
                if hasattr(thread, "cancel"):
                    thread.cancel()
        self._mine = []
        self._reconnect_thread = None
        self._home_thread = None

    def accept(self):
        """Override accept to perform cleanup first."""
        self._cleanup_threads()
        super().accept()

    def reject(self):
        """Override reject to perform cleanup first (stopping a download)."""
        self._cleanup_threads()
        super().reject()

    def closeEvent(self, event):
        """Handle dialog close events."""
        self._cleanup_threads()
        super().closeEvent(event)

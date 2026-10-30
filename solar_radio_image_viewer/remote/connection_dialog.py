"""
SSH Connection Dialog for SolarViewer.

Saved servers in a sidebar, one form (server, how to sign in, the profile's
name), and a footer that shows every action and its result: Test, Cancel and
Connect. Connecting and testing run in a thread (they can take the whole
timeout), the footer showing it with Cancel to abort.
"""

import dataclasses
import getpass
import json
import os
import re
import threading
import time
import traceback
from pathlib import Path
from typing import List, Optional

from PyQt5.QtCore import QPointF, QRectF, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..ui.styles import theme_manager
from ..ui.widgets import kicker, rule, segmented
from .ssh_manager import (
    ConnectionProfile,
    SSHConnection,
    SSHConnectionError,
    UnknownHostKey,
    trust_host_key,
)

try:
    import paramiko
except ImportError:  # (connecting then says so)
    paramiko = None

try:
    import keyring
except ImportError:  # (passwords and passphrases are then not remembered)
    keyring = None

KEYRING_SERVICE = "solarviewer-ssh"
ERROR_TEXT = "#e08a7c"  # error text under a field (the palette's error is for dots)
FORM_LABEL_WIDTH = 76

# [user@]host[:port]
_TARGET = re.compile(r"^(?:(?P<user>[^@\s:]+)@)?(?P<host>\[[^\]\s]+\]|[^@\s:]+)(?::(?P<port>\d+))?$")


def _palette(key):
    return theme_manager.palette[key]


def _relative_time(stamp):
    """"Used 3 days ago" for a time.time() ``stamp`` (0: never)."""
    if not stamp:
        return "Never used"
    seconds = max(0, time.time() - stamp)
    for size, unit in ((86400 * 365, "year"), (86400 * 30, "month"), (86400 * 7, "week"),
                       (86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size:
            n = int(seconds // size)
            return f"Used {n} {unit}{'s' if n != 1 else ''} ago"
    return "Used just now"


def _keyring_get(name):
    if keyring is None:
        return None
    try:
        return keyring.get_password(KEYRING_SERVICE, name)
    except Exception:
        return None


def _keyring_set(name, secret):
    if keyring is None:
        return
    try:
        keyring.set_password(KEYRING_SERVICE, name, secret)
    except Exception:
        pass


def _keyring_delete(name):
    if keyring is None:
        return
    try:
        keyring.delete_password(KEYRING_SERVICE, name)
    except Exception:
        pass


def _agent_keys():
    """The number of keys the SSH agent holds, or None without an agent."""
    if paramiko is None:
        return None
    agent = None
    try:
        agent = paramiko.Agent()
        return len(agent.get_keys())
    except Exception:
        return None
    finally:
        if agent is not None:
            try:
                agent.close()
            except Exception:
                pass


def _default_key_path():
    """~/.ssh/id_ed25519, or id_rsa when that is the only key present."""
    ssh = Path.home() / ".ssh"
    ed25519, rsa = ssh / "id_ed25519", ssh / "id_rsa"
    if not ed25519.exists() and rsa.exists():
        return str(rsa)
    return str(ed25519)


def _key_needs_passphrase(path):
    """Whether the private key file at ``path`` is encrypted."""
    if paramiko is None:
        return False
    for name in ("Ed25519Key", "ECDSAKey", "RSAKey", "DSSKey"):
        cls = getattr(paramiko, name, None)
        if cls is None:
            continue
        try:
            cls.from_private_key_file(path)
            return False
        except paramiko.PasswordRequiredException:
            return True
        except Exception:
            continue
    return False


def _short_reason(error, params, timeout):
    """A short reason for a failed connection, for the footer."""
    text = str(error).strip() or type(error).__name__
    lowered = text.lower()
    user = params.get("username") or getpass.getuser()
    if "timed out" in lowered or "timeout" in lowered:
        return f"Timed out after {timeout:.0f} s"
    if any(w in lowered for w in ("name or service", "nodename", "getaddrinfo", "resolve", "not known")):
        return "Host not found"
    if "refused" in lowered or "unable to connect" in lowered:
        return "Connection refused"
    if "authentication" in lowered or "auth" in lowered:
        if params.get("password"):
            return f"Password rejected for {user}"
        if params.get("key_path"):
            return f"Key rejected for {user}"
        return f"No key accepted for {user}"
    if "changed since" in lowered:
        return "Server key has changed"
    first = text.splitlines()[0]
    first = re.sub(r"^(SSH error|Connection failed):\s*", "", first)
    return first if len(first) <= 60 else first[:57] + "…"


class _ConnectThread(QThread):
    """Connects with ``params`` (the form's), in a thread: connecting can take
    the whole timeout, longer over a stalled network. A test then finds the
    home folder (timing that round trip) and disconnects."""

    succeeded = pyqtSignal(object)  # the SSHConnection (a test: the home folder)
    failed = pyqtSignal(object)  # the exception (UnknownHostKey: to confirm)

    def __init__(self, params, timeout, test):
        super().__init__()
        self.params = params
        self.timeout = timeout
        self.test = test
        self.rtt_ms = 0
        self.connection = SSHConnection()
        self._cancelled = False

    def run(self):
        connection = self.connection
        try:
            connection.connect(**self.params, timeout=self.timeout)
            if self.test:
                started = time.perf_counter()
                result = connection.get_home_directory()
                self.rtt_ms = int((time.perf_counter() - started) * 1000)
            else:
                result = connection
        except Exception as e:
            e.formatted = traceback.format_exc()
            connection.disconnect()
            if not self._cancelled:
                self.failed.emit(e)
            return
        if self.test or self._cancelled:
            connection.disconnect()
        if not self._cancelled:
            self.succeeded.emit(result)

    def cancel(self):
        """Its result is not wanted: the connection is closed as soon as
        there is one, which ends a wait for the server. (Reaching the server
        cannot be interrupted; it takes at most the timeout.)"""
        self._cancelled = True
        threading.Thread(target=self._close, name="ssh-connect-cancel", daemon=True).start()

    def _close(self):
        while not self.isFinished():
            self.connection.abort()
            time.sleep(0.05)
        self.connection.disconnect()  # (made, and sent, just before cancel)


class _Mark(QWidget):
    """A 7px dot, or a check, in one colour (a profile's state, a result's)."""

    def __init__(self, colour=None, check=False, size=7, parent=None):
        super().__init__(parent)
        self._colour = QColor(colour or "#3f424d")
        self._check = check
        self._size = size
        self.setFixedSize(size if not check else 12, 12)

    def set_state(self, colour, check=False):
        self._colour = QColor(colour)
        self._check = check
        self.setFixedWidth(12 if check else self._size)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self._check:
            painter.setPen(QPen(self._colour, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPolyline(QPointF(1.5, 6.5), QPointF(4.8, 9.5), QPointF(10.2, 3))
        else:
            painter.setPen(Qt.NoPen)
            painter.setBrush(self._colour)
            d = self._size
            painter.drawEllipse(QRectF(0, (self.height() - d) / 2, d, d))
        painter.end()


class _ProfileRow(QFrame):
    """One saved server: a status dot, its name over a muted line, and a ⋯
    menu on hover. Click selects it, double-click connects."""

    clicked = pyqtSignal(int)
    doubleClicked = pyqtSignal(int)
    renameRequested = pyqtSignal(int)
    duplicateRequested = pyqtSignal(int)
    deleteRequested = pyqtSignal(int)
    copyRequested = pyqtSignal(int)

    def __init__(self, index, profile, selected, state, parent=None):
        super().__init__(parent)
        self.index = index
        self.setObjectName("ProfileRow")
        self.setProperty("selected", "true" if selected else "false")
        self.setCursor(Qt.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 7, 6, 7)
        layout.setSpacing(8)

        colour = {"connected": _palette("status_on"), "failed": _palette("status_off")}.get(
            state, _palette("border_light")
        )
        layout.addWidget(_Mark(colour), 0, Qt.AlignTop | Qt.AlignVCenter)

        text = QVBoxLayout()
        text.setSpacing(1)
        self.name = QLabel(profile.name)
        self.name.setObjectName("ProfileName")
        host = profile.host.rsplit("@", 1)[-1]
        second = (
            f"{profile.username + '@' if profile.username else ''}{host}"
            if selected
            else _relative_time(profile.last_used)
        )
        self.sub = QLabel()
        self.sub.setObjectName("ProfileSub")
        self.sub.setText(self.sub.fontMetrics().elidedText(second, Qt.ElideRight, 140))
        self.sub.setToolTip(second if selected else "")
        text.addWidget(self.name)
        text.addWidget(self.sub)
        layout.addLayout(text, 1)

        self.more = QToolButton()
        self.more.setObjectName("RowMore")
        self.more.setText("⋯")
        self.more.setCursor(Qt.PointingHandCursor)
        self.more.setFixedSize(22, 22)
        self.more.setToolTip("More")
        policy = self.more.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.more.setSizePolicy(policy)
        self.more.clicked.connect(self._menu)
        self.more.setVisible(selected)
        self._selected = selected
        layout.addWidget(self.more, 0, Qt.AlignVCenter)

    def _menu(self, pos=None):
        menu = QMenu(self)
        menu.addAction("Connect", lambda: self.doubleClicked.emit(self.index))
        menu.addSeparator()
        menu.addAction("Rename", lambda: self.renameRequested.emit(self.index))
        menu.addAction("Duplicate", lambda: self.duplicateRequested.emit(self.index))
        menu.addAction("Copy ssh command", lambda: self.copyRequested.emit(self.index))
        menu.addSeparator()
        menu.addAction("Delete…", lambda: self.deleteRequested.emit(self.index))
        menu.exec_(pos or self.more.mapToGlobal(self.more.rect().bottomLeft()))

    def contextMenuEvent(self, event):
        self._menu(event.globalPos())


    def enterEvent(self, event):
        self.more.show()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.more.setVisible(getattr(self, "_selected", False))
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.index)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.doubleClicked.emit(self.index)


class _ImportDialog(QDialog):
    """Pick hosts of ~/.ssh/config to add as profiles."""

    def __init__(self, hosts, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import from ~/.ssh/config")
        self.setMinimumWidth(340)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Add these hosts as saved servers:"))
        self.list = QListWidget()
        for name in hosts:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self.list.addItem(item)
        layout.addWidget(self.list)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        add = QPushButton("Add")
        add.setObjectName("PrimaryButton")
        add.setDefault(True)
        add.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(add)
        layout.addLayout(row)

    def chosen(self):
        return [
            self.list.item(i).text()
            for i in range(self.list.count())
            if self.list.item(i).checkState() == Qt.Checked
        ]


class ConnectionDialog(QDialog):
    """
    Dialog for configuring SSH connections.

    Signals:
        connectionEstablished(SSHConnection): Emitted when connection succeeds
    """

    connectionEstablished = pyqtSignal(object)  # SSHConnection

    PROFILES_FILE = Path.home() / ".config" / "solarviewer" / "ssh_profiles.json"
    STATE_FILE = Path.home() / ".config" / "solarviewer" / "ssh_dialog.json"

    AUTH_METHODS = (("agent", "SSH agent"), ("key", "Key file"), ("password", "Password"))

    # Connections being made, kept until their threads end (closing the
    # dialog does not wait for them)
    _running: set = set()
    # Names of the profiles connected during this session (their dot is green)
    _connected: set = set()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect to remote server")
        self.setFixedSize(760, 500)
        self.setModal(True)

        self._ssh_connection: Optional[SSHConnection] = None
        self._attempt: Optional[_ConnectThread] = None  # connecting now
        self._profiles: List[ConnectionProfile] = []
        self._selected = -1  # the profile's index in _profiles, -1: a new connection
        self._loading = False  # the form being filled: its changes are not edits
        self._passphrase = ""  # of the key file, asked on connect (this session)
        self._agent_count = None
        self._load_profiles()

        self._build()
        self._apply_style()
        self._rebuild_sidebar()
        self._agent_count = _agent_keys()
        self._select_initial()

        try:
            from ..ui.styles import set_hand_cursor
            set_hand_cursor(self)
        except ImportError:
            pass

    # ------------------------------------------------------------------ UI

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        body = QHBoxLayout()
        body.setSpacing(0)
        body.addWidget(self._build_sidebar())
        self.form = self._build_form()
        body.addWidget(self.form, 1)
        root.addLayout(body, 1)
        root.addWidget(self._build_footer())

    def _build_sidebar(self):
        self.sidebar = QFrame()
        self.sidebar.setObjectName("ConnSidebar")
        self.sidebar.setFixedWidth(208)
        layout = QVBoxLayout(self.sidebar)
        layout.setContentsMargins(10, 14, 10, 12)
        layout.setSpacing(6)

        heading = kicker("Saved servers")
        heading.setContentsMargins(6, 0, 0, 0)
        layout.addWidget(heading)

        self.rows_host = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_host)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(1)
        scroll = QScrollArea()
        scroll.setObjectName("ConnRows")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(self.rows_host)
        layout.addWidget(scroll, 1)

        layout.addWidget(rule())
        self.new_btn = QPushButton("+  New connection")
        self.new_btn.setObjectName("NewConnection")
        self.new_btn.setCursor(Qt.PointingHandCursor)
        self.new_btn.setAutoDefault(False)
        self.new_btn.clicked.connect(lambda: self._select(-1))
        layout.addWidget(self.new_btn)

        note = QLabel(
            f'Reads hosts from ~/.ssh/config · <a href="import" style="color: '
            f'{_palette("link")}; text-decoration: none;">Import</a>'
        )
        note.setObjectName("Meta")
        note.setWordWrap(True)
        note.setContentsMargins(6, 4, 0, 0)
        note.setCursor(Qt.PointingHandCursor)
        note.linkActivated.connect(lambda _l: self._import_ssh_config())
        layout.addWidget(note)
        return self.sidebar

    def _field(self, mono=True, placeholder=""):
        edit = QLineEdit()
        edit.setFixedHeight(30)
        if mono:
            edit.setObjectName("MonoEdit")
        edit.setPlaceholderText(placeholder)
        return edit

    def _label(self, text, width=FORM_LABEL_WIDTH):
        label = QLabel(text)
        label.setObjectName("FormLabel")
        if width:
            label.setFixedWidth(width)
        return label

    def _error_label(self):
        label = QLabel()
        label.setObjectName("FieldError")
        label.setWordWrap(True)
        label.hide()
        return label

    def _hint(self, text=""):
        label = QLabel(text)
        label.setObjectName("Meta")
        label.setWordWrap(True)
        return label

    def _build_form(self):
        form = QFrame()
        layout = QVBoxLayout(form)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(18)

        # --- Server
        server = QVBoxLayout()
        server.setSpacing(8)
        server.addWidget(kicker("Server"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        self.host_edit = self._field(placeholder="hostname or user@hostname")
        self.port_edit = self._field(placeholder="22")
        self.port_edit.setText("22")
        self.port_edit.setFixedWidth(72)
        self.username_edit = self._field(placeholder=getpass.getuser())
        grid.addWidget(self._label("Host"), 0, 0)
        grid.addWidget(self.host_edit, 0, 1)
        port_label = self._label("Port", None)
        grid.addWidget(port_label, 0, 2)
        grid.addWidget(self.port_edit, 0, 3)
        grid.addWidget(self._label("Username"), 1, 0)
        grid.addWidget(self.username_edit, 1, 1, 1, 3)
        grid.setColumnStretch(1, 1)
        server.addLayout(grid)
        self.host_error = self._error_label()
        server.addWidget(self.host_error)
        server.addWidget(
            self._hint(
                "Paste user@host:port into Host and the fields fill themselves. "
                f"Empty username uses {getpass.getuser()}."
            )
        )
        layout.addLayout(server)

        # --- Sign in with
        auth = QVBoxLayout()
        auth.setSpacing(8)
        auth.addWidget(kicker("Sign in with"))
        self.auth_group = QButtonGroup(self)
        self.auth_group.setExclusive(True)
        self.auth_buttons = {}
        buttons = []
        for key, text in self.AUTH_METHODS:
            button = QPushButton(text)
            button.setCheckable(True)
            button.setAutoDefault(False)
            button.setFixedHeight(24)
            self.auth_group.addButton(button)
            self.auth_buttons[key] = button
            buttons.append(button)
        track = segmented(buttons, role="HeaderSegmented")
        track.setFixedHeight(28)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(track)
        row.addStretch()
        auth.addLayout(row)
        self.auth_group.buttonClicked.connect(lambda _b: self._on_auth_changed())

        self.auth_stack = QStackedWidget()
        # agent
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        self.agent_status = self._hint()
        box.addWidget(self.agent_status)
        box.addStretch()
        self.auth_stack.addWidget(page)
        # key file
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)
        key_row = QHBoxLayout()
        key_row.setSpacing(8)
        self.key_path_edit = self._field(placeholder=_default_key_path())
        self.browse_key_btn = QPushButton("Browse…")
        self.browse_key_btn.setFixedHeight(30)
        self.browse_key_btn.setAutoDefault(False)
        self.browse_key_btn.clicked.connect(self._browse_key_file)
        key_row.addWidget(self.key_path_edit, 1)
        key_row.addWidget(self.browse_key_btn)
        box.addLayout(key_row)
        self.key_error = self._error_label()
        box.addWidget(self.key_error)
        box.addWidget(
            self._hint(
                "Passphrase is asked on connect and kept in the system keyring."
                if keyring is not None
                else "Passphrase is asked on connect (install keyring to remember it)."
            )
        )
        box.addStretch()
        self.auth_stack.addWidget(page)
        # password
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(8)
        self.password_edit = self._field(placeholder="Password")
        self.password_edit.setEchoMode(QLineEdit.Password)
        box.addWidget(self.password_edit)
        self.password_error = self._error_label()
        box.addWidget(self.password_error)
        self.remember_switch = QCheckBox("Remember in system keyring")
        self.remember_switch.setObjectName("Switch")
        if keyring is None:
            self.remember_switch.setEnabled(False)
            self.remember_switch.setToolTip("Install the keyring package to remember passwords.")
        box.addWidget(self.remember_switch)
        box.addStretch()
        self.auth_stack.addWidget(page)
        auth.addWidget(self.auth_stack)
        layout.addLayout(auth, 1)

        # --- Profile
        layout.addWidget(rule())
        profile = QHBoxLayout()
        profile.setSpacing(8)
        profile.addWidget(self._label("Name"))
        self.name_edit = self._field(mono=False, placeholder="Name of the saved server")
        profile.addWidget(self.name_edit, 1)
        self.save_switch = QCheckBox("Save to list")
        self.save_switch.setObjectName("Switch")
        self.save_switch.setChecked(True)
        profile.addWidget(self.save_switch)
        layout.addLayout(profile)

        # Edits: parse a pasted target, follow the host, clear a test result
        self.host_edit.textEdited.connect(self._on_host_edited)
        self.host_edit.editingFinished.connect(self._parse_target)
        self.host_edit.textChanged.connect(self._sync_name_placeholder)
        for edit in (
            self.host_edit, self.port_edit, self.username_edit,
            self.key_path_edit, self.password_edit, self.name_edit,
        ):
            edit.textChanged.connect(self._on_edited)
        self.remember_switch.toggled.connect(self._on_edited)
        self.port_edit.editingFinished.connect(self._validate_port)
        return form

    def _build_footer(self):
        footer = QFrame()
        footer.setObjectName("ConnFooter")
        footer.setFixedHeight(52)
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(8)

        self.test_btn = QPushButton("Test connection")
        self.test_btn.setObjectName("LinkButton")
        self.test_btn.setAutoDefault(False)
        self.test_btn.clicked.connect(self._test_connection)
        layout.addWidget(self.test_btn)

        self.result_mark = _Mark()
        self.result_mark.hide()
        layout.addWidget(self.result_mark)
        self.result_label = QLabel()
        self.result_label.setObjectName("Meta")
        self.result_label.setTextFormat(Qt.PlainText)
        layout.addWidget(self.result_label, 1)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("GhostButton")
        self.cancel_btn.setAutoDefault(False)
        self.cancel_btn.clicked.connect(self._cancel)
        layout.addWidget(self.cancel_btn)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("PrimaryButton")
        self.connect_btn.setDefault(True)
        self.connect_btn.clicked.connect(self._connect)
        layout.addWidget(self.connect_btn)
        return footer

    def _apply_style(self):
        p = theme_manager.palette
        self.setStyleSheet(f"""
            QFrame#ConnSidebar {{
                background-color: {p['surface']};
                border-right: 1px solid {p['border_light']};
            }}
            QFrame#ConnFooter {{
                border-top: 1px solid {p['border_light']};
            }}
            QScrollArea#ConnRows, QScrollArea#ConnRows > QWidget > QWidget {{
                background: transparent;
            }}
            QFrame#ProfileRow {{
                background: transparent;
                border-radius: 6px;
            }}
            QFrame#ProfileRow:hover {{
                background-color: {p['hover']};
            }}
            QFrame#ProfileRow[selected="true"] {{
                background-color: {p['window']};
                border-left: 2px solid {p['highlight']};
                border-top-left-radius: 0px;
                border-bottom-left-radius: 0px;
            }}
            QLabel#ProfileName {{
                font-size: 12px;
                color: {p['text']};
                background: transparent;
            }}
            QLabel#ProfileSub {{
                font-size: 11px;
                color: {p['text_secondary']};
                background: transparent;
            }}
            QToolButton#RowMore {{
                border: none;
                border-radius: 4px;
                background: transparent;
                color: {p['text_dim']};
                font-size: 14px;
            }}
            QToolButton#RowMore:hover {{
                background-color: {p['pressed']};
                color: {p['text']};
            }}
            QPushButton#NewConnection {{
                text-align: left;
                border: none;
                background: transparent;
                color: {p['link']};
                font-size: 12px;
                font-weight: 400;
                padding: 7px 6px;
                border-radius: 6px;
            }}
            QPushButton#NewConnection:hover {{
                background-color: {p['hover']};
            }}
            QPushButton#NewConnection[selected="true"] {{
                background-color: {p['window']};
            }}
            QLabel#FormLabel {{
                font-size: 12px;
                color: {p['text_dim']};
            }}
            QLabel#FieldError {{
                font-size: 11px;
                color: {ERROR_TEXT};
            }}
            QLineEdit#MonoEdit {{
                font-family: monospace;
                font-size: 12px;
            }}
        """)

    # ------------------------------------------------------------ sidebar

    def _profile_state(self, profile):
        if profile.name in self._connected:
            return "connected"
        if profile.last_failed:
            return "failed"
        return "idle"

    def _rebuild_sidebar(self):
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)  # (gone at once; deleteLater waits)
                widget.deleteLater()
        for i, profile in enumerate(self._profiles):
            row = _ProfileRow(i, profile, i == self._selected, self._profile_state(profile))
            row.clicked.connect(self._select)
            row.doubleClicked.connect(self._double_clicked)
            row.renameRequested.connect(self._rename_profile)
            row.duplicateRequested.connect(self._duplicate_profile)
            row.deleteRequested.connect(self._delete_profile)
            row.copyRequested.connect(self._copy_ssh_command)
            self.rows_layout.addWidget(row)
        self.rows_layout.addStretch()
        self.new_btn.setProperty("selected", "true" if self._selected < 0 else "false")
        self.new_btn.style().unpolish(self.new_btn)
        self.new_btn.style().polish(self.new_btn)

    def _select_initial(self):
        last = self._load_state().get("last_profile")
        index = next((i for i, p in enumerate(self._profiles) if p.name == last), -1)
        self._select(index)

    def _select(self, index):
        """Load profile ``index`` (-1: a new connection) into the form."""
        if self._attempt is not None:
            return
        self._selected = index
        self._loading = True
        self._clear_errors()
        try:
            if index < 0:
                self._fill_new()
            else:
                self._fill(self._profiles[index])
        finally:
            self._loading = False
        self._set_result("idle")
        self._rebuild_sidebar()
        self._update_auth_page()

    def _double_clicked(self, index):
        self._select(index)
        self._connect()

    def _fill_new(self):
        self.host_edit.clear()
        self.port_edit.setText("22")
        self.username_edit.clear()
        self.key_path_edit.setText(_default_key_path())
        self.password_edit.clear()
        self.remember_switch.setChecked(False)
        self.name_edit.clear()
        self.save_switch.setChecked(True)
        self._passphrase = ""
        self._set_auth("agent" if (self._agent_count or 0) > 0 else "key")

    def _fill(self, profile):
        self.host_edit.setText(profile.host)
        self.port_edit.setText(str(profile.port or 22))
        self.username_edit.setText(profile.username)
        self.key_path_edit.setText(profile.key_path or _default_key_path())
        self.password_edit.clear()
        self.remember_switch.setChecked(False)
        self.name_edit.setText(profile.name)
        self.save_switch.setChecked(True)
        self._passphrase = ""
        auth = profile.auth_type if profile.auth_type in self.auth_buttons else "key"
        self._set_auth(auth)
        if auth == "password":
            saved = _keyring_get(self._password_account(profile.username, profile.host, profile.port))
            if saved:
                self.password_edit.setText(saved)
                self.remember_switch.setChecked(True)

    # -------------------------------------------------------------- auth

    def _auth(self):
        for key, button in self.auth_buttons.items():
            if button.isChecked():
                return key
        return "agent"

    def _set_auth(self, key):
        self.auth_buttons[key].setChecked(True)
        self._update_auth_page()

    def _on_auth_changed(self):
        self._update_auth_page()
        self._on_edited()

    def _update_auth_page(self):
        auth = self._auth()
        self.auth_stack.setCurrentIndex([k for k, _ in self.AUTH_METHODS].index(auth))
        if auth == "agent":
            n = self._agent_count
            if n is None:
                self.agent_status.setText("No agent found")
            else:
                self.agent_status.setText(
                    f"Agent running · {n} key{'s' if n != 1 else ''} loaded"
                )

    def _browse_key_file(self):
        start = self.key_path_edit.text().strip() or str(Path.home() / ".ssh")
        path, _ = QFileDialog.getOpenFileName(
            self, "Select SSH private key", os.path.expanduser(start), "All Files (*)"
        )
        if path:
            self.key_path_edit.setText(path)

    # -------------------------------------------------------- form events

    def _on_host_edited(self, text):
        """A paste (many characters at once) of user@host:port is split."""
        previous = getattr(self, "_last_host_text", "")
        self._last_host_text = text
        if abs(len(text) - len(previous)) > 1 and ("@" in text or ":" in text):
            self._parse_target(pasted=True)

    def _parse_target(self, pasted=False):
        """Split user@host:port in Host into the three fields. The host
        loses its user either way; the Username box keeps its own value
        unless it is empty or the text was just pasted."""
        text = self.host_edit.text().strip()
        match = _TARGET.match(text)
        if not match or (match.group("user") is None and match.group("port") is None):
            return
        self.host_edit.blockSignals(True)
        self.host_edit.setText(match.group("host"))
        self.host_edit.blockSignals(False)
        if match.group("user") and (pasted or not self.username_edit.text().strip()):
            self.username_edit.setText(match.group("user"))
        if match.group("port"):
            self.port_edit.setText(match.group("port"))
        self._sync_name_placeholder()
        self._on_edited()

    def _sync_name_placeholder(self, *_):
        self.name_edit.setPlaceholderText(self.host_edit.text().strip() or "Name of the saved server")

    def _on_edited(self, *_):
        """Any change after a test clears its result."""
        if self._loading:
            return
        if self._attempt is None:
            self._set_result("idle")
        for label in (self.host_error, self.key_error, self.password_error):
            if label.isVisible():
                label.hide()

    def _show_error(self, label, text):
        label.setText(text)
        label.show()

    def _clear_errors(self):
        for label in (self.host_error, self.key_error, self.password_error):
            label.hide()

    def _validate_port(self):
        text = self.port_edit.text().strip()
        if text and not (text.isdigit() and 1 <= int(text) <= 65535):
            self._show_error(self.host_error, "Port must be a number from 1 to 65535.")
            return False
        return True

    def _validate(self):
        """Show what is wrong under its field; whether the form is fine."""
        self._clear_errors()
        ok = True
        if not self.host_edit.text().strip():
            self._show_error(self.host_error, "Enter a host.")
            ok = False
        elif not self._validate_port():
            ok = False
        auth = self._auth()
        if auth == "key":
            path = os.path.expanduser(self.key_path_edit.text().strip())
            if not path:
                self._show_error(self.key_error, "Choose a key file.")
                ok = False
            elif not os.path.isfile(path):
                self._show_error(self.key_error, "No such key file.")
                ok = False
        elif auth == "password" and not self.password_edit.text():
            self._show_error(self.password_error, "Enter the password.")
            ok = False
        return ok

    # ----------------------------------------------------------- profiles

    def _load_profiles(self):
        """Load saved connection profiles."""
        self._profiles = []
        if not self.PROFILES_FILE.exists():
            return
        fields = {f.name for f in dataclasses.fields(ConnectionProfile)} - {"password"}
        try:
            with open(self.PROFILES_FILE, "r") as f:
                data = json.load(f)
            for entry in data:
                profile = ConnectionProfile(**{k: v for k, v in entry.items() if k in fields})
                if profile.auth_type == "key" and not profile.key_path:
                    profile.auth_type = "agent"  # (it used the agent or default keys)
                self._profiles.append(profile)
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
            self._profiles = []

    def _save_profiles(self):
        """Save connection profiles to disk (never their passwords)."""
        self.PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = []
        for p in self._profiles:
            d = dataclasses.asdict(p)
            d.pop("password", None)
            data.append(d)
        with open(self.PROFILES_FILE, "w") as f:
            json.dump(data, f, indent=2)

    def _load_state(self):
        try:
            with open(self.STATE_FILE, "r") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_state(self, **state):
        try:
            self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(self.STATE_FILE, "w") as f:
                json.dump({**self._load_state(), **state}, f)
        except OSError:
            pass

    @staticmethod
    def _password_account(username, host, port):
        return f"{username or getpass.getuser()}@{host}:{port or 22}"

    def _port_value(self):
        text = self.port_edit.text().strip()
        return int(text) if text.isdigit() else 22

    def _profile_name(self):
        return self.name_edit.text().strip() or self.host_edit.text().strip()

    def _create_profile(self, name, previous=None) -> ConnectionProfile:
        """A profile from the form's values."""
        return ConnectionProfile(
            name=name,
            host=self.host_edit.text().strip(),
            port=self._port_value(),
            username=self.username_edit.text().strip(),
            auth_type=self._auth(),
            key_path=self.key_path_edit.text().strip() if self._auth() == "key" else "",
            last_used=previous.last_used if previous else 0.0,
            last_failed=previous.last_failed if previous else False,
        )

    def _save_form_profile(self):
        """Save or update the profile the form describes; its index."""
        name = self._profile_name()
        previous = None
        index = self._selected
        if index < 0:
            index = next((i for i, p in enumerate(self._profiles) if p.name == name), -1)
        if index >= 0:
            previous = self._profiles[index]
            self._profiles[index] = self._create_profile(name, previous)
        else:
            self._profiles.append(self._create_profile(name))
            index = len(self._profiles) - 1
        self._selected = index
        self._save_profiles()
        return index

    def _rename_profile(self, index):
        profile = self._profiles[index]
        name, ok = QInputDialog.getText(self, "Rename", "Name:", text=profile.name)
        name = name.strip()
        if ok and name:
            profile.name = name
            self._save_profiles()
            if index == self._selected:
                self.name_edit.setText(name)
            self._rebuild_sidebar()

    def _duplicate_profile(self, index):
        copy = dataclasses.replace(self._profiles[index], last_used=0.0, last_failed=False)
        names = {p.name for p in self._profiles}
        base = f"{copy.name} copy"
        copy.name, n = base, 2
        while copy.name in names:
            copy.name, n = f"{base} {n}", n + 1
        self._profiles.insert(index + 1, copy)
        self._save_profiles()
        self._select(index + 1)

    def _delete_profile(self, index):
        profile = self._profiles[index]
        answer = QMessageBox.question(
            self,
            "Delete saved server?",
            f"Delete '{profile.name}' from the list?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        if profile.auth_type == "password":
            _keyring_delete(self._password_account(profile.username, profile.host, profile.port))
        del self._profiles[index]
        self._save_profiles()
        if index == self._selected:
            self._select(-1)
        else:
            if index < self._selected:
                self._selected -= 1
            self._rebuild_sidebar()

    def _copy_ssh_command(self, index):
        p = self._profiles[index]
        parts = ["ssh"]
        if p.port and p.port != 22:
            parts += ["-p", str(p.port)]
        if p.auth_type == "key" and p.key_path:
            parts += ["-i", p.key_path]
        parts.append(f"{p.username + '@' if p.username else ''}{p.host.rsplit('@', 1)[-1]}")
        QApplication.clipboard().setText(" ".join(parts))
        self._set_result("idle")
        self.result_label.setText("Copied: " + " ".join(parts))

    def keyPressEvent(self, event):
        """Delete removes the selected server (not while typing in a field)."""
        if (
            event.key() == Qt.Key_Delete
            and self._attempt is None
            and self._selected >= 0
            and not isinstance(self.focusWidget(), QLineEdit)
        ):
            self._delete_profile(self._selected)
            return
        super().keyPressEvent(event)

    def _import_ssh_config(self):
        config_path = Path.home() / ".ssh" / "config"
        if paramiko is None or not config_path.exists():
            self._set_result("failed", "No ~/.ssh/config found", "")
            return
        try:
            config = paramiko.SSHConfig.from_path(str(config_path))
        except Exception as e:
            self._set_result("failed", "Could not read ~/.ssh/config", str(e))
            return
        existing = {p.name for p in self._profiles}
        hosts = [
            h for h in config.get_hostnames()
            if not any(c in h for c in "*?!") and h not in existing
        ]
        if not hosts:
            self._set_result("idle")
            self.result_label.setText("No new hosts in ~/.ssh/config")
            return
        dialog = _ImportDialog(hosts, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        for name in dialog.chosen():
            entry = config.lookup(name)
            identity = entry.get("identityfile") or []
            key_path = identity[0] if identity else ""
            self._profiles.append(
                ConnectionProfile(
                    name=name,
                    host=name,
                    port=int(entry.get("port") or 22),
                    username=entry.get("user") or "",
                    auth_type="key" if key_path else "agent",
                    key_path=key_path,
                )
            )
        self._save_profiles()
        self._rebuild_sidebar()

    # ----------------------------------------------------------- results

    def _set_result(self, state, message="", detail=""):
        """The footer's line: idle, testing, connecting, ok or failed."""
        self._result_state = state
        busy = state in ("testing", "connecting")
        self.test_btn.setVisible(not busy and state != "connecting")
        self.test_btn.setText("Retry" if state == "failed" else "Test connection")
        self.connect_btn.setEnabled(not busy)
        self.cancel_btn.setText("Cancel")
        self.sidebar.setEnabled(not busy)
        self.form.setEnabled(not busy)
        p = theme_manager.palette
        if state == "idle":
            self.result_mark.hide()
        elif busy:
            self.result_mark.set_state(p["highlight"])
            self.result_mark.show()
        elif state == "ok":
            self.result_mark.set_state(p["status_on"], check=True)
            self.result_mark.show()
        else:
            self.result_mark.set_state(p["status_off"])
            self.result_mark.show()
        self.result_label.setText(message if state != "idle" else "")
        self.result_label.setToolTip(detail)
        self.result_label.setStyleSheet(
            f"color: {p['text_strong']};" if state == "failed" else ""
        )

    # ------------------------------------------------------------ connect

    def _get_connection_params(self) -> dict:
        """Get connection parameters from form."""
        params = {"host": self.host_edit.text().strip(), "port": self._port_value()}
        username = self.username_edit.text().strip()
        if username:
            params["username"] = username
        auth = self._auth()
        if auth == "password":
            params["password"] = self.password_edit.text()
        elif auth == "key":
            params["key_path"] = os.path.expanduser(self.key_path_edit.text().strip())
            if self._passphrase:
                params["passphrase"] = self._passphrase
        return params

    def connect_to(self, params):
        """Fill in ``params`` (host, port, username, key_path, as
        SSHConnection.connect_params gives them) and connect straight away."""
        self._select(-1)
        self._loading = True
        try:
            self.host_edit.setText(params.get("host") or "")
            self.port_edit.setText(str(int(params["port"])) if params.get("port") else "22")
            self.username_edit.setText(params.get("username") or "")
            self.key_path_edit.setText(params.get("key_path") or _default_key_path())
            self._passphrase = params.get("passphrase") or ""
            if params.get("password"):
                self.password_edit.setText(params["password"])
                self._set_auth("password")
            else:
                self._set_auth("key" if params.get("key_path") else "agent")
            self.save_switch.setChecked(False)
        finally:
            self._loading = False
        self._connect()

    def _test_connection(self):
        """Test the connection without closing the dialog."""
        self._start(test=True)

    def _connect(self):
        """Establish connection and close dialog."""
        if self._attempt is None:
            self._start(test=False)

    def _ask_passphrase(self, path):
        """The key's passphrase (the keyring's, or asked), or None to stop."""
        saved = _keyring_get(f"key:{path}")
        if saved:
            self._passphrase = saved
            return True
        text, ok = QInputDialog.getText(
            self, "Passphrase", f"Passphrase for {os.path.basename(path)}:", QLineEdit.Password
        )
        if not ok:
            return False
        self._passphrase = text
        return True

    def _start(self, test, params=None):
        """Connect with ``params`` (the form's if None) in a thread; for a
        ``test``, only say whether it works."""
        if params is None:
            self._parse_target()
            if not self._validate():
                return
            if paramiko is None:
                self._set_result("failed", "paramiko is not installed", "pip install paramiko")
                return
            if self._auth() == "key":
                path = os.path.expanduser(self.key_path_edit.text().strip())
                if not self._passphrase and _key_needs_passphrase(path):
                    if not self._ask_passphrase(path):
                        return
            params = self._get_connection_params()
        try:
            thread = _ConnectThread(params, timeout=10.0 if test else 30.0, test=test)
        except SSHConnectionError as e:
            self._set_result("failed", _short_reason(e, params, 10.0), str(e))
            return
        thread.succeeded.connect(self._on_succeeded)
        thread.failed.connect(self._on_failed)
        ConnectionDialog._running.add(thread)
        thread.finished.connect(lambda t=thread: ConnectionDialog._running.discard(t))
        self._attempt = thread
        thread.auth = "password" if params.get("password") else (
            "key" if params.get("key_path") else "agent"
        )
        if test:
            self._set_result("testing", f"Testing {params['host']}…")
        else:
            self._set_result("connecting", f"Connecting to {params['host']}…")
        self.cancel_btn.setEnabled(True)
        thread.start()

    def _remember_secrets(self, thread):
        """After a success: keep the password / passphrase in the keyring."""
        params = thread.params
        if params.get("password") and self.remember_switch.isChecked():
            _keyring_set(
                self._password_account(params.get("username"), params["host"], params.get("port")),
                params["password"],
            )
        if params.get("passphrase") and params.get("key_path"):
            _keyring_set(f"key:{params['key_path']}", params["passphrase"])

    def _mark_profile(self, failed, used=False):
        """Note on the selected profile how its attempt went."""
        if not 0 <= self._selected < len(self._profiles):
            return
        profile = self._profiles[self._selected]
        profile.last_failed = failed
        if used:
            profile.last_used = time.time()
            self._connected.add(profile.name)
        self._save_profiles()

    def _on_succeeded(self, result):
        thread = self.sender()
        if thread is not self._attempt:
            return
        self._attempt = None
        auth_word = {"agent": "agent key", "key": "key", "password": "password"}[thread.auth]
        if thread.test:
            self._remember_secrets(thread)
            self._mark_profile(failed=False)
            self._set_result("ok", f"Reachable · {thread.rtt_ms} ms · {auth_word} accepted")
            self._rebuild_sidebar()
            return
        self._remember_secrets(thread)
        if self.save_switch.isChecked():
            self._save_form_profile()
        self._mark_profile(failed=False, used=True)
        self._save_state(last_profile=self._profiles[self._selected].name
                         if 0 <= self._selected < len(self._profiles) else None)
        self._ssh_connection = result
        self._set_result("idle")
        self.connectionEstablished.emit(result)
        self.accept()

    def _on_failed(self, error):
        thread = self.sender()
        if thread is not self._attempt:
            return
        self._attempt = None
        test, params, timeout = thread.test, thread.params, thread.timeout
        if isinstance(error, UnknownHostKey):
            if self._trust(error):
                trust_host_key(error.hostname, error.key)
                self._start(test, params)
                return
            self._set_result("failed", "Server key not trusted", str(error))
            return
        self._mark_profile(failed=True)
        self._passphrase = "" if params.get("passphrase") else self._passphrase
        detail = getattr(error, "formatted", None) or str(error)
        self._set_result("failed", _short_reason(error, params, timeout), detail)
        self._rebuild_sidebar()

    def _trust(self, e):
        """Whether the user trusts the key of a server not known yet, shown
        to confirm before anything is sent to it."""
        key_type = e.key.get_name()  # ssh-ed25519, ecdsa-sha2-nistp256, ssh-rsa
        kind = next((k for k in ("ed25519", "ecdsa") if k in key_type), "rsa")
        answer = QMessageBox.question(
            self,
            "Unknown Server",
            f"SolarViewer does not know the server {e.hostname} yet.\n\n"
            f"Its {key_type} key fingerprint is:\n{e.fingerprint}\n\n"
            "Continue only if this matches the server's fingerprint (on the "
            f"server: ssh-keygen -lf /etc/ssh/ssh_host_{kind}_key.pub).\n\n"
            "Trust this server and connect?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def _cancel(self):
        """Stop connecting or testing, or (when not) close the dialog."""
        if self._attempt is not None:
            self._abandon()
            self._set_result("idle")
        else:
            self.reject()

    def _abandon(self):
        """Drop the connection being made; its thread ends by itself."""
        thread, self._attempt = self._attempt, None
        if thread is None:
            return
        for signal in (thread.succeeded, thread.failed):
            try:
                signal.disconnect()
            except TypeError:
                pass
        thread.cancel()

    def reject(self):
        """Close (Cancel when not connecting, Escape, the title bar's close),
        dropping a connection being made."""
        self._abandon()
        super().reject()

    def get_connection(self) -> Optional[SSHConnection]:
        """Get the established connection (after dialog accepted)."""
        return self._ssh_connection

"""
SSH Connection Dialog for SolarViewer.

Provides a dialog for configuring and establishing SSH connections,
with support for saving connection profiles. Connecting runs in a thread
(it can take the whole timeout), the dialog showing it with Stop.
"""

from pathlib import Path
from typing import Optional, List
import json
import threading
import time

from ..ui.widgets import button_bar
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QComboBox,
    QLabel,
    QFileDialog,
    QGroupBox,
    QRadioButton,
    QButtonGroup,
    QSpinBox,
    QMessageBox,
    QFrame,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal

from ..ui.download_parts import Spinner
from .ssh_manager import (
    ConnectionProfile,
    SSHConnection,
    SSHConnectionError,
    UnknownHostKey,
    trust_host_key,
)


class _ConnectThread(QThread):
    """Connects with ``params`` (the form's), in a thread: connecting can take
    the whole timeout, longer over a stalled network. A test then finds the
    home folder and disconnects."""

    succeeded = pyqtSignal(object)  # the SSHConnection (a test: the home folder)
    failed = pyqtSignal(object)  # the exception (UnknownHostKey: to confirm)

    def __init__(self, params, timeout, test):
        super().__init__()
        self.params = params
        self.timeout = timeout
        self.test = test
        self.connection = SSHConnection()
        self._cancelled = False

    def run(self):
        connection = self.connection
        try:
            connection.connect(**self.params, timeout=self.timeout)
            result = connection.get_home_directory() if self.test else connection
        except Exception as e:
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


class ConnectionDialog(QDialog):
    """
    Dialog for configuring SSH connections.

    Signals:
        connectionEstablished(SSHConnection): Emitted when connection succeeds
    """

    connectionEstablished = pyqtSignal(object)  # SSHConnection

    PROFILES_FILE = Path.home() / ".config" / "solarviewer" / "ssh_profiles.json"

    # Connections being made, kept until their threads end (closing the
    # dialog does not wait for them)
    _running: set = set()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect to Remote Server")
        self.setMinimumWidth(450)
        self.setModal(True)

        self._ssh_connection: Optional[SSHConnection] = None
        self._attempt: Optional[_ConnectThread] = None  # connecting now
        self._profiles: List[ConnectionProfile] = []
        self._load_profiles()

        self._setup_ui()
        # (450 at least, and wide enough for the buttons at larger scales)
        self.setMinimumWidth(max(450, self.minimumSizeHint().width()))

        try:
            from ..ui.styles import set_hand_cursor
            set_hand_cursor(self)
        except ImportError:
            pass

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Saved profiles section
        profiles_group = self.profiles_group = QGroupBox("Saved Profiles")
        profiles_layout = QHBoxLayout(profiles_group)

        self.profile_combo = QComboBox()
        self.profile_combo.addItem("-- New Connection --")
        for profile in self._profiles:
            self.profile_combo.addItem(profile.name)
        self.profile_combo.currentIndexChanged.connect(self._on_profile_selected)
        profiles_layout.addWidget(self.profile_combo, stretch=1)

        self.delete_profile_btn = QPushButton("Delete")
        self.delete_profile_btn.setEnabled(False)
        self.delete_profile_btn.clicked.connect(self._delete_profile)
        profiles_layout.addWidget(self.delete_profile_btn)

        layout.addWidget(profiles_group)

        # Connection settings
        settings_group = self.settings_group = QGroupBox("Connection Settings")
        form_layout = QFormLayout(settings_group)

        # Host
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("hostname or user@hostname")
        form_layout.addRow("Host:", self.host_edit)

        # Port
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(22)
        form_layout.addRow("Port:", self.port_spin)

        # Username
        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("(optional, defaults to current user)")
        form_layout.addRow("Username:", self.username_edit)

        layout.addWidget(settings_group)

        # Authentication section
        auth_group = self.auth_group = QGroupBox("Authentication")
        auth_layout = QVBoxLayout(auth_group)

        # Auth type selection
        auth_type_layout = QHBoxLayout()
        self.auth_button_group = QButtonGroup(self)

        self.key_auth_radio = QRadioButton("SSH Key")
        self.key_auth_radio.setChecked(True)
        self.auth_button_group.addButton(self.key_auth_radio, 0)
        auth_type_layout.addWidget(self.key_auth_radio)

        self.password_auth_radio = QRadioButton("Password")
        self.auth_button_group.addButton(self.password_auth_radio, 1)
        auth_type_layout.addWidget(self.password_auth_radio)

        auth_type_layout.addStretch()
        auth_layout.addLayout(auth_type_layout)

        # Key file selection
        self.key_frame = QFrame()
        key_layout = QHBoxLayout(self.key_frame)
        key_layout.setContentsMargins(0, 0, 0, 0)

        self.key_path_edit = QLineEdit()
        self.key_path_edit.setPlaceholderText("(optional, uses SSH agent/default keys)")
        key_layout.addWidget(self.key_path_edit)

        self.browse_key_btn = QPushButton("Browse...")
        self.browse_key_btn.clicked.connect(self._browse_key_file)
        key_layout.addWidget(self.browse_key_btn)

        auth_layout.addWidget(self.key_frame)

        # Password field
        self.password_frame = QFrame()
        password_layout = QHBoxLayout(self.password_frame)
        password_layout.setContentsMargins(0, 0, 0, 0)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setPlaceholderText("Enter password")
        password_layout.addWidget(self.password_edit)

        auth_layout.addWidget(self.password_frame)
        self.password_frame.hide()

        # Connect auth type toggle
        self.key_auth_radio.toggled.connect(self._on_auth_type_changed)

        layout.addWidget(auth_group)

        # Status: what happened, led by a spinner while connecting
        status_row = QHBoxLayout()
        self.spinner = Spinner(max(12, int(self.fontMetrics().capHeight() * 0.9)))
        status_row.addWidget(self.spinner, 0, Qt.AlignVCenter)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        status_row.addWidget(self.status_label, 1)
        layout.addLayout(status_row)

        # Buttons (Cancel reads Stop while connecting)
        box, buttons = button_bar(
            primary=("Connect", self._connect),
            secondary=("Cancel", self._cancel),
            extras=[
                ("Test Connection", self._test_connection),
                ("Save Profile", self._save_profile),
            ],
        )
        self.test_btn = buttons["Test Connection"]
        self.save_profile_btn = buttons["Save Profile"]
        self.cancel_btn = buttons["Cancel"]
        self.connect_btn = buttons["Connect"]
        layout.addWidget(box)

    def _on_auth_type_changed(self, key_auth_selected: bool):
        """Toggle between key and password authentication UI."""
        self.key_frame.setVisible(key_auth_selected)
        self.password_frame.setVisible(not key_auth_selected)

    def _browse_key_file(self):
        """Browse for SSH key file."""
        default_path = str(Path.home() / ".ssh")
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select SSH Private Key",
            default_path,
            "All Files (*)",
        )
        if file_path:
            self.key_path_edit.setText(file_path)

    def _load_profiles(self):
        """Load saved connection profiles."""
        if self.PROFILES_FILE.exists():
            try:
                with open(self.PROFILES_FILE, "r") as f:
                    data = json.load(f)
                self._profiles = [ConnectionProfile(**p) for p in data]
            except (json.JSONDecodeError, KeyError, TypeError):
                self._profiles = []

    def _save_profiles(self):
        """Save connection profiles to disk."""
        self.PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = []
        for p in self._profiles:
            d = {
                "name": p.name,
                "host": p.host,
                "port": p.port,
                "username": p.username,
                "auth_type": p.auth_type,
                "key_path": p.key_path,
            }
            # Don't save passwords
            data.append(d)

        with open(self.PROFILES_FILE, "w") as f:
            json.dump(data, f, indent=2)

    def _on_profile_selected(self, index: int):
        """Load selected profile into form."""
        self.delete_profile_btn.setEnabled(index > 0)

        if index == 0:
            # New connection - clear fields
            self.host_edit.clear()
            self.port_spin.setValue(22)
            self.username_edit.clear()
            self.key_auth_radio.setChecked(True)
            self.key_path_edit.clear()
            self.password_edit.clear()
        else:
            # Load profile
            profile = self._profiles[index - 1]
            self.host_edit.setText(profile.host)
            self.port_spin.setValue(profile.port)
            self.username_edit.setText(profile.username)

            if profile.auth_type == "password":
                self.password_auth_radio.setChecked(True)
            else:
                self.key_auth_radio.setChecked(True)
                self.key_path_edit.setText(profile.key_path)

    def _save_profile(self):
        """Save current settings as a profile."""
        host = self.host_edit.text().strip()
        if not host:
            QMessageBox.warning(self, "Error", "Please enter a host first.")
            return

        # Generate profile name
        username = self.username_edit.text().strip()
        if username:
            name = f"{username}@{host}"
        else:
            name = host

        # Check for existing profile with same name
        for i, p in enumerate(self._profiles):
            if p.name == name:
                reply = QMessageBox.question(
                    self,
                    "Overwrite Profile?",
                    f"Profile '{name}' already exists. Overwrite?",
                    QMessageBox.Yes | QMessageBox.No,
                )
                if reply == QMessageBox.Yes:
                    self._profiles[i] = self._create_profile(name)
                    self._save_profiles()
                    self.status_label.setText(f"Profile '{name}' updated.")
                return

        # Add new profile
        profile = self._create_profile(name)
        self._profiles.append(profile)
        self._save_profiles()

        # Update combo
        self.profile_combo.addItem(name)
        self.profile_combo.setCurrentIndex(self.profile_combo.count() - 1)

        self.status_label.setText(f"Profile '{name}' saved.")

    def _create_profile(self, name: str) -> ConnectionProfile:
        """Create a profile from current form values."""
        return ConnectionProfile(
            name=name,
            host=self.host_edit.text().strip(),
            port=self.port_spin.value(),
            username=self.username_edit.text().strip(),
            auth_type="password" if self.password_auth_radio.isChecked() else "key",
            key_path=self.key_path_edit.text().strip(),
        )

    def _delete_profile(self):
        """Delete the selected profile."""
        index = self.profile_combo.currentIndex()
        if index <= 0:
            return

        name = self.profile_combo.currentText()
        reply = QMessageBox.question(
            self,
            "Delete Profile?",
            f"Delete profile '{name}'?",
            QMessageBox.Yes | QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            del self._profiles[index - 1]
            self._save_profiles()
            self.profile_combo.removeItem(index)
            self.profile_combo.setCurrentIndex(0)
            self.status_label.setText(f"Profile '{name}' deleted.")

    def _get_connection_params(self) -> dict:
        """Get connection parameters from form."""
        params = {
            "host": self.host_edit.text().strip(),
            "port": self.port_spin.value(),
        }

        username = self.username_edit.text().strip()
        if username:
            params["username"] = username

        if self.password_auth_radio.isChecked():
            params["password"] = self.password_edit.text()
        else:
            key_path = self.key_path_edit.text().strip()
            if key_path:
                params["key_path"] = key_path

        return params

    def connect_to(self, params):
        """Fill in ``params`` (host, port, username, key_path, as
        SSHConnection.connect_params gives them) and connect straight away."""
        self.host_edit.setText(params.get("host") or "")
        if params.get("port"):
            self.port_spin.setValue(int(params["port"]))
        self.username_edit.setText(params.get("username") or "")
        self.key_auth_radio.setChecked(True)
        self.key_path_edit.setText(params.get("key_path") or "")
        self._connect()

    def _test_connection(self):
        """Test the connection without closing the dialog."""
        self._start(test=True)

    def _connect(self):
        """Establish connection and close dialog."""
        self._start(test=False)

    def _start(self, test, params=None):
        """Connect with ``params`` (the form's if None) in a thread; for a
        ``test``, only say whether it works."""
        if params is None:
            params = self._get_connection_params()
        if not params["host"]:
            self.status_label.setText("Please enter a host.")
            return
        try:
            thread = _ConnectThread(params, timeout=10.0 if test else 30.0, test=test)
        except SSHConnectionError as e:
            self.status_label.setText(str(e))
            return
        thread.succeeded.connect(self._on_succeeded)
        thread.failed.connect(self._on_failed)
        ConnectionDialog._running.add(thread)
        thread.finished.connect(lambda t=thread: ConnectionDialog._running.discard(t))
        self._attempt = thread
        doing = "Testing the connection to" if test else "Connecting to"
        self._set_connecting(True, f"{doing} {params['host']}…")
        thread.start()

    def _on_succeeded(self, result):
        thread = self.sender()
        if thread is not self._attempt:
            return
        self._attempt = None
        if thread.test:
            self._set_connecting(False, f"Connected successfully. Home folder: {result}")
            return
        self._set_connecting(False, "")
        self._ssh_connection = result
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
            self._set_connecting(False, "Not connected: the server's key was not trusted.")
            return
        text = str(error).strip() or type(error).__name__
        if "timed out" in text.lower():
            text = f"Connection failed: {params['host']} did not answer within {timeout:.0f} s."
        elif not isinstance(error, SSHConnectionError):
            text = f"Connection failed: {text}"
        # (an SSHConnectionError says what failed)
        self._set_connecting(False, text)

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

    def _set_connecting(self, connecting, text):
        """The form and buttons locked while connecting (only Stop works),
        and the status line."""
        for widget in (
            self.profiles_group,
            self.settings_group,
            self.auth_group,
            self.test_btn,
            self.save_profile_btn,
            self.connect_btn,
        ):
            widget.setEnabled(not connecting)
        self.cancel_btn.setText("Stop" if connecting else "Cancel")
        self.spinner.setVisible(connecting)
        self.status_label.setText(text)

    def _cancel(self):
        """Stop connecting, or (when not) close the dialog."""
        if self._attempt is not None:
            self._abandon()
            self._set_connecting(False, "Stopped.")
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

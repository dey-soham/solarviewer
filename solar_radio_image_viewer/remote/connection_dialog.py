"""
SSH Connection Dialog for SolarViewer.

Provides a dialog for configuring and establishing SSH connections,
with support for saving connection profiles.
"""

import os
from pathlib import Path
from typing import Optional, List
import json

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
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QIcon

from .ssh_manager import SSHConnection, SSHConnectionError, ConnectionProfile


class ConnectionDialog(QDialog):
    """
    Dialog for configuring SSH connections.

    Signals:
        connectionEstablished(SSHConnection): Emitted when connection succeeds
    """

    connectionEstablished = pyqtSignal(object)  # SSHConnection

    PROFILES_FILE = Path.home() / ".config" / "solarviewer" / "ssh_profiles.json"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect to Remote Server")
        self.setMinimumWidth(450)
        self.setModal(True)

        self._ssh_connection: Optional[SSHConnection] = None
        self._profiles: List[ConnectionProfile] = []
        self._load_profiles()

        self._setup_ui()
        self._apply_styles()
        
        try:
            from ..styles import set_hand_cursor
            set_hand_cursor(self)
        except ImportError:
            pass

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Saved profiles section
        profiles_group = QGroupBox("Saved Profiles")
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
        settings_group = QGroupBox("Connection Settings")
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
        auth_group = QGroupBox("Authentication")
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

        # Status label
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # Buttons
        button_layout = QHBoxLayout()

        self.test_btn = QPushButton("Test Connection")
        self.test_btn.clicked.connect(self._test_connection)
        button_layout.addWidget(self.test_btn)

        self.save_profile_btn = QPushButton("Save Profile")
        self.save_profile_btn.clicked.connect(self._save_profile)
        button_layout.addWidget(self.save_profile_btn)

        button_layout.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setDefault(True)
        self.connect_btn.clicked.connect(self._connect)
        button_layout.addWidget(self.connect_btn)

        layout.addLayout(button_layout)

    def _apply_styles(self):
        """Apply styling to the dialog using theme_manager for consistency."""
        try:
            from ..styles import theme_manager
        except ImportError:
            from styles import theme_manager

        palette = theme_manager.palette
        border = palette["border"]
        border_light = palette.get("border_light", border)
        surface = palette["surface"]
        base = palette["base"]
        highlight = palette["highlight"]
        text = palette["text"]

        self.setStyleSheet(
            f"""
            QGroupBox {{
                font-weight: bold;
                border: 1px solid {border};
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 12px;
                background-color: {surface};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 10px;
                padding: 0 5px;
                color: {highlight};
            }}
            QLineEdit, QSpinBox, QComboBox {{
                padding: 6px;
                border: 1px solid {border};
                border-radius: 4px;
                background-color: {base};
                color: {text};
            }}
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
                border-color: {highlight};
                border-width: 2px;
            }}
            QPushButton {{
                padding: 6px 16px;
                border-radius: 4px;
                border: 1px solid {border};
            }}
            QPushButton:hover {{
                border-color: {highlight};
            }}
            QRadioButton {{
                color: {text};
            }}
        """
        )

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

    def _test_connection(self):
        """Test the connection without closing the dialog."""
        params = self._get_connection_params()

        if not params["host"]:
            self.status_label.setText("❌ Please enter a host.")
            return

        self.status_label.setText("⏳ Testing connection...")
        self.test_btn.setEnabled(False)
        self.connect_btn.setEnabled(False)

        # Force UI update
        from PyQt5.QtWidgets import QApplication

        QApplication.processEvents()

        try:
            conn = SSHConnection()
            conn.connect(**params, timeout=10.0)

            home_dir = conn.get_home_directory()
            self.status_label.setText(
                f"✅ Connected successfully!\nHome directory: {home_dir}"
            )
            conn.disconnect()

        except SSHConnectionError as e:
            self.status_label.setText(f"❌ Connection failed: {e}")
        except Exception as e:
            self.status_label.setText(f"❌ Error: {e}")
        finally:
            self.test_btn.setEnabled(True)
            self.connect_btn.setEnabled(True)

    def _connect(self):
        """Establish connection and close dialog."""
        params = self._get_connection_params()

        if not params["host"]:
            self.status_label.setText("❌ Please enter a host.")
            return

        self.status_label.setText("⏳ Connecting...")
        self.test_btn.setEnabled(False)
        self.connect_btn.setEnabled(False)

        from PyQt5.QtWidgets import QApplication

        QApplication.processEvents()

        try:
            self._ssh_connection = SSHConnection()
            self._ssh_connection.connect(**params, timeout=30.0)

            self.connectionEstablished.emit(self._ssh_connection)
            self.accept()

        except SSHConnectionError as e:
            self.status_label.setText(f"❌ Connection failed: {e}")
            self._ssh_connection = None
            self.test_btn.setEnabled(True)
            self.connect_btn.setEnabled(True)
        except Exception as e:
            self.status_label.setText(f"❌ Error: {e}")
            self._ssh_connection = None
            self.test_btn.setEnabled(True)
            self.connect_btn.setEnabled(True)

    def get_connection(self) -> Optional[SSHConnection]:
        """Get the established connection (after dialog accepted)."""
        return self._ssh_connection

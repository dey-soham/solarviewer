from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QRadioButton,
    QLineEdit,
    QLabel,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QGridLayout,
    QFormLayout,
    QDialogButtonBox,
    QComboBox,
    QCheckBox,
    QFileDialog,
    QMessageBox,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QButtonGroup,
    QWidget,
    QProgressDialog,
    QFrame,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QProgressBar,
    QApplication,
)
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import Qt, QSize
import sys
import numpy as np
import os

from ..styles import theme_manager, set_hand_cursor

from PyQt5.QtCore import QThread, pyqtSignal
import subprocess
from ..utils.update_checker import check_for_updates
from ..version import __version__

class UpdateCheckThread(QThread):
    """Thread to check for updates in background."""

    update_checked = pyqtSignal(
        bool, str, str
    )  # (is_available, latest_version, error_msg)

    def run(self):
        is_avail, latest, error = check_for_updates(__version__)
        self.update_checked.emit(is_avail, latest, error)


class UpdateDialog(QDialog):
    """Dialog to check for application updates."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Check for Updates")
        self.setMinimumWidth(400)

        # Theme styling
        from ..styles import theme_manager

        self.theme = theme_manager
        self.palette = theme_manager.palette

        # Apply base dialog styling
        self.setStyleSheet(
            f"""
            QDialog {{
                background-color: {self.palette['window']};
                color: {self.palette['text']};
            }}
            QLabel {{
                color: {self.palette['text']};
                font-size: 11pt;
            }}
        """
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        # Header Area
        header_layout = QHBoxLayout()
        header_layout.setSpacing(16)

        # Icon (Text-based placeholder for now)
        icon_label = QLabel("🔄")
        icon_label.setStyleSheet(
            f"""
            font-size: 32pt;
            color: {self.palette['highlight']};
        """
        )
        header_layout.addWidget(icon_label)

        # Title/Subtitle
        title_layout = QVBoxLayout()
        title_layout.setSpacing(4)

        title_label = QLabel("Software Update")
        title_label.setStyleSheet(
            f"""
            font-size: 14pt;
            font-weight: bold;
            color: {self.palette['text']};
        """
        )

        subtitle_label = QLabel("Check for new versions of SolarViewer")
        subtitle_label.setStyleSheet(
            f"""
            color: {self.palette['text_secondary']};
            font-size: 10.2pt;
        """
        )

        title_layout.addWidget(title_label)
        title_layout.addWidget(subtitle_label)
        header_layout.addLayout(title_layout)
        header_layout.addStretch()

        layout.addLayout(header_layout)

        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setFrameShadow(QFrame.Sunken)
        divider.setStyleSheet(
            f"background-color: {self.palette['border_light']}; max-height: 1px;"
        )
        layout.addWidget(divider)

        # Info Grid
        info_container = QWidget()
        info_container.setStyleSheet(
            f"""
            QWidget {{
                background-color: {self.palette['surface']};
                border-radius: 8px;
                border: 1px solid {self.palette['border']};
            }}
            QLabel {{
                background-color: transparent;
                border: none;
            }}
        """
        )
        info_grid = QGridLayout(info_container)
        info_grid.setContentsMargins(16, 16, 16, 16)
        info_grid.setVerticalSpacing(12)
        info_grid.setHorizontalSpacing(16)

        # Current Version
        self.current_ver_label = QLabel(f"{__version__}")
        self.current_ver_label.setStyleSheet(
            "font-family: monospace; font-weight: bold;"
        )

        lbl_current = QLabel("Current Version:")
        lbl_current.setStyleSheet(f"color: {self.palette['text_secondary']};")

        info_grid.addWidget(lbl_current, 0, 0)
        info_grid.addWidget(self.current_ver_label, 0, 1)

        # Latest Version
        self.latest_ver_label = QLabel("Checking...")
        self.latest_ver_label.setStyleSheet(
            f"color: {self.palette['text_secondary']}; font-style: italic;"
        )

        lbl_latest = QLabel("Latest Version:")
        lbl_latest.setStyleSheet(f"color: {self.palette['text_secondary']};")

        info_grid.addWidget(lbl_latest, 1, 0)
        info_grid.addWidget(self.latest_ver_label, 1, 1)

        layout.addWidget(info_container)

        # Status Message area
        self.status_label = QLabel("Connecting to update server...")
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet(
            f"""
            color: {self.palette['text_secondary']};
            padding: 8px;
            font-style: italic;
        """
        )
        layout.addWidget(self.status_label)

        layout.addStretch()

        # Buttons
        button_row = QHBoxLayout()
        button_row.addStretch()

        self.close_btn = QPushButton("Close")
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {self.palette['surface']};
                border: 1px solid {self.palette['border']};
                border-radius: 6px;
                color: {self.palette['text']};
                padding: 8px 16px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {self.palette['window']};
                border-color: {self.palette['text_secondary']};
            }}
        """
        )
        self.close_btn.clicked.connect(self.accept)
        button_row.addWidget(self.close_btn)

        self.update_btn = QPushButton("Update Now")
        self.update_btn.setCursor(Qt.PointingHandCursor)
        self.update_btn.setVisible(False)
        self.update_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {self.palette['highlight']};
                border: none;
                border-radius: 6px;
                color: #ffffff;
                padding: 8px 20px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {self.palette['highlight_hover']};
            }}
            QPushButton:disabled {{
                background-color: {self.palette['disabled']};
                color: {self.palette['text_secondary']};
            }}
        """
        )
        self.update_btn.clicked.connect(self.start_update)
        button_row.addWidget(self.update_btn)

        layout.addLayout(button_row)

        # Start check
        self.check_thread = UpdateCheckThread()
        self.check_thread.update_checked.connect(self.on_check_finished)
        self.check_thread.start()

    def on_check_finished(self, is_available, latest_version, error_msg):
        # Safeguard: If dialog is closed/hidden, don't update UI
        if not self.isVisible():
            return

        if error_msg:
            self.status_label.setText(f"Error: {error_msg}")
            self.status_label.setStyleSheet(
                f"color: {self.palette['error']}; font-weight: 500;"
            )
            self.latest_ver_label.setText("Unknown")
        else:
            self.latest_ver_label.setText(f"{latest_version}")
            self.latest_ver_label.setStyleSheet(
                "font-family: monospace; font-weight: bold;"
            )

            if is_available:
                self.status_label.setText(
                    "🚀 A new version of SolarViewer is available!"
                )
                self.status_label.setStyleSheet(
                    f"color: {self.palette['highlight']}; font-weight: bold;"
                )
                self.update_btn.setVisible(True)
                self.update_btn.setEnabled(True)

                # Highlight the latest version label
                self.latest_ver_label.setStyleSheet(
                    f"""
                    font-family: monospace; 
                    font-weight: bold; 
                    color: {self.palette['success']};
                """
                )
            else:
                self.status_label.setText("✅ You are using the latest version.")
                self.status_label.setStyleSheet(
                    f"color: {self.palette['success']}; font-weight: 500;"
                )

    def start_update(self):
        """Install update via pip."""
        reply = QMessageBox.question(
            self,
            "Confirm Update",
            "The application will update via pip and needs to restart.\n\nContinue with update?",
            QMessageBox.Yes | QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            self.status_label.setText("📦 Installing update... Please wait.")
            self.status_label.setStyleSheet(
                f"color: {self.palette['text']}; font-weight: 500;"
            )
            self.update_btn.setEnabled(False)
            self.update_btn.setText("Updating...")
            self.repaint()  # Force redraw

            try:
                import os

                venv_path = sys.prefix
                activate_script = os.path.join(venv_path, "bin", "activate")

                if os.path.exists(activate_script):
                    # Run via shell to support sourcing.
                    cmd = f'. "{activate_script}" && pip install --upgrade solarviewer'
                    print(f"[INFO] Executing: {cmd}")

                    # Use Popen to capture and print output in real-time
                    launch_cwd = os.getcwd() if os.access(os.getcwd(), os.W_OK) else os.path.expanduser("~")
                    process = subprocess.Popen(
                        cmd,
                        shell=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        cwd=launch_cwd,
                    )

                    # Stream output to main stdout (which Log Console captures)
                    for line in process.stdout:
                        print(line, end="")
                        QApplication.processEvents()  # Keep UI responsive

                    process.wait()
                    if process.returncode != 0:
                        raise subprocess.CalledProcessError(process.returncode, cmd)

                else:
                    # Fallback if no standard activate script found
                    cmd = [
                        sys.executable,
                        "-m",
                        "pip",
                        "install",
                        "--upgrade",
                        "solarviewer",
                    ]
                    print(f"[INFO] Executing: {' '.join(cmd)}")

                    launch_cwd = os.getcwd() if os.access(os.getcwd(), os.W_OK) else os.path.expanduser("~")
                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        cwd=launch_cwd,
                    )

                    for line in process.stdout:
                        print(line, end="")
                        QApplication.processEvents()

                    process.wait()
                    if process.returncode != 0:
                        raise subprocess.CalledProcessError(process.returncode, cmd)

                QMessageBox.information(
                    self,
                    "Update Successful",
                    "Update installed successfully.\n\nPlease restart the application to apply changes.",
                )
                self.accept()

            except subprocess.CalledProcessError as e:
                self.status_label.setText("❌ Update failed.")
                self.status_label.setStyleSheet(
                    f"color: {self.palette['error']}; font-weight: bold;"
                )
                QMessageBox.critical(
                    self, "Update Failed", f"Could not install update:\n{e}"
                )
                self.update_btn.setEnabled(True)
                self.update_btn.setText("Update Now")
            except Exception as e:
                self.status_label.setText("❌ Error occurred.")
                self.status_label.setStyleSheet(
                    f"color: {self.palette['error']}; font-weight: bold;"
                )
                QMessageBox.critical(
                    self, "Error", f"An unexpected error occurred:\n{e}"
                )
                self.update_btn.setEnabled(True)
                self.update_btn.setText("Update Now")


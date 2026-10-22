import logging
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QGridLayout,
    QMessageBox,
    QWidget,
    QFrame,
    QApplication,
)
from PyQt5.QtCore import QProcess, Qt, QThread, pyqtSignal
import os
import sys

from ..utils.update_checker import check_for_updates
from ..version import __version__

logger = logging.getLogger(__name__)

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
        self._pip = None  # the QProcess installing the update

        # Theme styling
        from ..ui.styles import theme_manager

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

    # The pip of the Python running SolarViewer installs into its environment
    # (a virtualenv's python needs no activation); no shell is involved
    PIP_COMMAND = [sys.executable, "-m", "pip", "install", "--upgrade", "solarviewer"]

    def start_update(self):
        """Install the update with pip, in the background."""
        reply = QMessageBox.question(
            self,
            "Confirm Update",
            "The application will update via pip and needs to restart.\n\nContinue with update?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self.status_label.setText("📦 Installing update... Please wait.")
        self.status_label.setStyleSheet(
            f"color: {self.palette['text']}; font-weight: 500;"
        )
        self.update_btn.setEnabled(False)
        self.update_btn.setText("Updating...")

        process = QProcess(self)
        process.setProcessChannelMode(QProcess.MergedChannels)
        cwd = os.getcwd()
        process.setWorkingDirectory(
            cwd if os.access(cwd, os.W_OK) else os.path.expanduser("~")
        )
        process.readyReadStandardOutput.connect(self._show_pip_output)
        process.finished.connect(self._on_update_finished)
        process.errorOccurred.connect(self._on_update_error)
        self._pip = process
        # Quitting must not cut pip off half-way through an install
        QApplication.instance().aboutToQuit.connect(self._wait_for_pip)
        logger.info(f"Executing: {' '.join(self.PIP_COMMAND)}")
        process.start(self.PIP_COMMAND[0], self.PIP_COMMAND[1:])

    def _show_pip_output(self):
        """pip's output, to the log window and the log file."""
        if self._pip is not None:
            text = bytes(self._pip.readAllStandardOutput()).decode(errors="replace")
            print(text, end="")

    def _wait_for_pip(self):
        if self._pip is not None:
            self._pip.waitForFinished(-1)

    def _on_update_finished(self, exit_code, exit_status):
        self._show_pip_output()
        self._pip = None
        if exit_status == QProcess.NormalExit and exit_code == 0:
            QMessageBox.information(
                self,
                "Update Successful",
                "Update installed successfully.\n\nPlease restart the application to apply changes.",
            )
            self.accept()
        else:
            self._update_failed(
                f"pip stopped with exit code {exit_code}. Its output is in the "
                "log (View > Show Log Console)."
            )

    def _on_update_error(self, error):
        if error == QProcess.FailedToStart and self._pip is not None:
            self._pip = None
            self._update_failed(f"Could not start pip ({sys.executable} -m pip).")

    def _update_failed(self, reason):
        self.status_label.setText("❌ Update failed.")
        self.status_label.setStyleSheet(
            f"color: {self.palette['error']}; font-weight: bold;"
        )
        QMessageBox.critical(self, "Update Failed", f"Could not install update:\n{reason}")
        self.update_btn.setEnabled(True)
        self.update_btn.setText("Update Now")

    def reject(self):
        # Closing mid-install would stop pip half-way
        if self._pip is not None:
            self.status_label.setText("📦 Installing update... please wait until it finishes.")
            return
        super().reject()

import logging
from ..ui.activity import ActivityLabel
from ..ui.busy import begin_busy, end_busy
from ..ui.widgets import button_bar, dialog_layout
from .form import field_label, form_block
from ..ui.styles import set_tone
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QApplication,
)
from PyQt5.QtCore import QEvent, QProcess, Qt, QThread, pyqtSignal
import os
import re
import sys

from ..utils.update_checker import check_for_updates
from ..version import __version__

logger = logging.getLogger(__name__)

# Checks still running: a closed dialog must not take its QThread with it
_running_checks = set()


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
        self.setWindowTitle("Check for updates")
        self.setMinimumWidth(400)
        self._pip = None  # the QProcess installing the update
        self._finishing = False  # quitting, the window held until pip ends

        layout = dialog_layout(self)

        info_grid = form_block(layout)

        # Current Version
        self.current_ver_label = QLabel(f"{__version__}")
        self.current_ver_label.setObjectName("Mono")

        info_grid.addWidget(field_label("Current version"), 0, 0)
        info_grid.addWidget(self.current_ver_label, 0, 1)

        # Latest Version
        self.latest_ver_label = QLabel("Checking…")
        self.latest_ver_label.setObjectName("Mono")

        info_grid.addWidget(field_label("Latest version"), 1, 0)
        info_grid.addWidget(self.latest_ver_label, 1, 1)

        # Status Message area
        self.status = ActivityLabel()
        self.status_label = self.status.label
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("Hint")
        layout.addWidget(self.status)

        # pip's latest line while it installs
        self.pip_line = QLabel()
        self.pip_line.setTextFormat(Qt.PlainText)
        self.pip_line.setObjectName("Meta")
        self.pip_line.hide()
        layout.addWidget(self.pip_line)

        layout.addStretch()

        box, buttons = button_bar(
            primary=("Update now", self.start_update), secondary=("Close", self.reject)
        )
        self.close_btn = buttons["Close"]
        self.update_btn = buttons["Update now"]
        # (out of the box until there is an update: a box shows its buttons again
        # each time it lays them out)
        self.button_box = box
        box.removeButton(self.update_btn)
        layout.addWidget(box)

        # Start check
        self.status.start("Checking for a new version…")
        self.check_thread = UpdateCheckThread()
        self.check_thread.update_checked.connect(self.on_check_finished)
        thread = self.check_thread
        _running_checks.add(thread)

        def forget():
            thread.wait()  # "finished" comes just before the thread ends
            _running_checks.discard(thread)

        thread.finished.connect(forget)
        thread.start()

    def on_check_finished(self, is_available, latest_version, error_msg):
        # Safeguard: If dialog is closed/hidden, don't update UI
        if not self.isVisible():
            return

        self.status.stop()
        if error_msg:
            logger.warning("Update check failed: %s", error_msg)
            self.status_label.setText("Could not reach PyPI. Check the network and try again.")
            self.status_label.setToolTip(error_msg)
            set_tone(self.status_label, "error")
            self.latest_ver_label.setText("Unknown")
        else:
            self.latest_ver_label.setText(f"{latest_version}")

            if is_available:
                self.status_label.setText("A new version of SolarViewer is available.")
                set_tone(self.status_label, "success")
                set_tone(self.latest_ver_label, "success")
                self.button_box.addButton(self.update_btn, QDialogButtonBox.AcceptRole)
                self.update_btn.setDefault(True)
                self.update_btn.setEnabled(True)
            else:
                self.status_label.setText("You are using the latest version.")
                set_tone(self.status_label, "success")
        self.adjustSize()  # (a long message would be clipped in the opening size)

    # The pip of the Python running SolarViewer installs into its environment
    # (a virtualenv's python needs no activation); no shell is involved
    PIP_COMMAND = [sys.executable, "-m", "pip", "install", "--upgrade", "solarviewer"]

    def start_update(self):
        """Install the update with pip, in the background."""
        reply = QMessageBox.question(
            self,
            "Confirm update",
            "The application will update via pip and needs to restart.\n\nContinue with update?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self.status.start("Installing the update…")
        set_tone(self.status_label, None)
        self.update_btn.setEnabled(False)
        self.update_btn.setText("Updating…")
        self.close_btn.setEnabled(False)

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
        if self.parent() is not None:
            self.parent().installEventFilter(self)
        logger.info(f"Executing: {' '.join(self.PIP_COMMAND)}")
        process.start(self.PIP_COMMAND[0], self.PIP_COMMAND[1:])

    def _show_pip_output(self):
        """pip's output, to the log window and the log file; its latest line
        here, so that a long install is seen to move."""
        if self._pip is not None:
            text = bytes(self._pip.readAllStandardOutput()).decode(errors="replace")
            print(text, end="")
            lines = [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]
            if lines:
                width = self.pip_line.width() if self.pip_line.isVisible() else self.width() - 48
                self.pip_line.setText(
                    self.pip_line.fontMetrics().elidedText(lines[-1], Qt.ElideMiddle, width)
                )
                self.pip_line.setToolTip(lines[-1])
                self.pip_line.show()

    def eventFilter(self, watched, event):
        # Closing the main window quits, which would hold it frozen until pip
        # ends: it stays open and usable instead, as this dialog does
        if (
            event.type() == QEvent.Close
            and watched is self.parent()
            and self._pip is not None
        ):
            self.show()
            self.raise_()
            self.reject()
            event.ignore()
            return True
        return super().eventFilter(watched, event)

    def _wait_for_pip(self):
        """Hold a quit that did not come through the main window's close."""
        if self._pip is not None:
            self.show()
            self.raise_()
            self.status.start("Finishing the update before closing…")
            begin_busy("Finishing the update before closing…", self)
            self._finishing = True
            # (The event loop has ended, so the line is painted here)
            pip = self._pip
            while not pip.waitForFinished(100) and pip.state() != QProcess.NotRunning:
                self._show_pip_output()
                self.pip_line.repaint()
            self._stop_finishing()

    def _stop_finishing(self):
        """The label ends with pip: its outcome comes up inside the wait."""
        if self._finishing:
            self._finishing = False
            end_busy()

    def _on_update_finished(self, exit_code, exit_status):
        self._stop_finishing()
        self._show_pip_output()
        self._pip = None
        self.pip_line.hide()
        self.close_btn.setEnabled(True)
        if exit_status == QProcess.NormalExit and exit_code == 0:
            self.status.stop("Update installed.")
            set_tone(self.status_label, "success")
            QMessageBox.information(
                self,
                "Update successful",
                "Update installed successfully.\n\nPlease restart the application to apply changes.",
            )
            self.accept()
        else:
            self._update_failed(
                f"pip stopped with exit code {exit_code}. Its output is in the "
                "log (View > Log Console)."
            )

    def _on_update_error(self, error):
        if error == QProcess.FailedToStart and self._pip is not None:
            self._stop_finishing()
            self._pip = None
            self.close_btn.setEnabled(True)
            self._update_failed(f"Could not start pip ({sys.executable} -m pip).")

    def _update_failed(self, reason):
        self.status.stop("Update failed.")
        set_tone(self.status_label, "error")
        QMessageBox.critical(self, "Update failed", f"Could not install update:\n{reason}")
        self.update_btn.setEnabled(True)
        self.update_btn.setText("Update now")

    def reject(self):
        # Closing mid-install would stop pip half-way
        if self._pip is not None:
            self.status_label.setText("Installing the update… closing has to wait until it finishes.")
            return
        super().reject()

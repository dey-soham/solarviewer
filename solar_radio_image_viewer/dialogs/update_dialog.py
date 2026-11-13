import logging
from ..ui.activity import ActivityLabel
from ..ui.app import SETTINGS, restart_application
from ..ui.busy import begin_busy, end_busy
from ..ui.widgets import button_bar, dialog_layout, kicker, link_button
from .form import field_label, form_block
from ..ui.styles import SPACE_S, set_tone
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QApplication,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import QEvent, QProcess, QProcessEnvironment, QSettings, QTimer, QUrl, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QDesktopServices
import os
import re

from ..utils import update_checker as updates
from ..version import __version__

logger = logging.getLogger(__name__)

NOTES_HEIGHT = 260  # the most the notes take before they scroll

# Checks still running: a closed dialog must not take its QThread with it
_running_checks = set()


class UpdateCheckThread(QThread):
    """Thread to check for updates in background."""

    update_checked = pyqtSignal(object, object)  # (how it is installed, what is newer)

    def run(self):
        try:
            install, update = updates.look()
        except Exception as e:  # reading how it is installed went wrong
            logger.warning("Could not check for updates: %s", e, exc_info=True)
            install = updates.Install(version=__version__)
            update = updates.Update(error=f"Error checking for updates: {e}")
        self.update_checked.emit(install, update)


class UpdateDialog(QDialog):
    """Dialog to check for application updates: what is installed and how,
    what is newer and what is new in it, and the update itself, by the tool
    this copy was installed with."""

    updated = pyqtSignal()  # an update was installed
    skipped = pyqtSignal(str)  # the user does not want to hear of this one again

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Check for updates")
        self.setMinimumWidth(480)
        self._pip = None  # the QProcess installing the update
        self._steps = []  # the commands still to run
        self._output = []  # what the installer printed
        self._before = ""  # what was installed when the update began
        self._finishing = False  # quitting, the window held until pip ends
        self.install = updates.Install(version=__version__)
        self.update = updates.Update()
        self.settings = QSettings(*SETTINGS)

        layout = dialog_layout(self)

        info_grid = form_block(layout, longest="Current version")

        # Current Version
        self.current_ver_label = QLabel(f"{__version__}")
        self.current_ver_label.setObjectName("Mono")

        info_grid.addWidget(field_label("Current version"), 0, 0)
        info_grid.addWidget(self.current_ver_label, 0, 1)

        # How this copy was installed: it is updated the same way
        self.how_label = QLabel("")
        self.how_label.setWordWrap(True)
        self.how_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        info_grid.addWidget(field_label("Installed as"), 1, 0, Qt.AlignTop)
        info_grid.addWidget(self.how_label, 1, 1)

        # Latest Version
        self.latest_ver_label = QLabel("Checking…")
        self.latest_ver_label.setObjectName("Mono")

        info_grid.addWidget(field_label("Latest version"), 2, 0)
        info_grid.addWidget(self.latest_ver_label, 2, 1)

        # Status Message area
        self.status = ActivityLabel()
        self.status_label = self.status.label
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("Hint")
        layout.addWidget(self.status)

        # What is new: the release notes, or the commits
        self.notes_block = QWidget()
        notes_layout = QVBoxLayout(self.notes_block)
        notes_layout.setContentsMargins(0, 0, 0, 0)
        notes_layout.setSpacing(SPACE_S)
        head = QHBoxLayout()
        head.addWidget(kicker("What's new"))
        head.addStretch()
        self.notes_link = link_button("Read on GitHub", self._open_notes)
        head.addWidget(self.notes_link)
        notes_layout.addLayout(head)
        self.notes_view = QTextBrowser()
        self.notes_view.setOpenExternalLinks(True)
        notes_layout.addWidget(self.notes_view)
        self.notes_missing = QLabel("")
        self.notes_missing.setObjectName("Hint")
        self.notes_missing.setWordWrap(True)
        notes_layout.addWidget(self.notes_missing)
        self.notes_block.hide()
        layout.addWidget(self.notes_block)

        # What Update now runs, or what to do when this copy cannot update itself
        self.command_block = QWidget()
        command_layout = QVBoxLayout(self.command_block)
        command_layout.setContentsMargins(0, 0, 0, 0)
        command_layout.setSpacing(2)
        self.command_hint = QLabel("")
        self.command_hint.setObjectName("Hint")
        self.command_hint.setWordWrap(True)
        command_layout.addWidget(self.command_hint)
        self.command_label = QLabel("")
        self.command_label.setObjectName("Mono")
        self.command_label.setWordWrap(True)
        self.command_label.setTextFormat(Qt.PlainText)
        self.command_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        command_layout.addWidget(self.command_label)
        self.command_block.hide()
        layout.addWidget(self.command_block)

        # pip's latest line while it installs
        self.pip_line = QLabel()
        self.pip_line.setTextFormat(Qt.PlainText)
        self.pip_line.setObjectName("Meta")
        self.pip_line.hide()
        layout.addWidget(self.pip_line)

        layout.addStretch()

        self.startup_switch = QCheckBox("Check for updates when SolarViewer starts")
        self.startup_switch.setObjectName("Switch")
        self.startup_switch.setToolTip(
            "Off: SolarViewer asks nothing at startup. Help > Check for updates still looks when you open it."
        )
        self.startup_switch.setChecked(self.settings.value(updates.CHECK_AT_STARTUP, True, type=bool))
        self.startup_switch.toggled.connect(lambda on: self.settings.setValue(updates.CHECK_AT_STARTUP, on))
        layout.addWidget(self.startup_switch)

        box, buttons = button_bar(
            primary=("Update now", self.start_update),
            secondary=("Close", self.reject),
            extras=[("Skip this version", self.skip_version), ("Restart now", self.restart)],
        )
        self.close_btn = buttons["Close"]
        self.update_btn = buttons["Update now"]
        self.skip_btn = buttons["Skip this version"]
        self.skip_btn.setToolTip("Do not announce this one at startup again; a later one is announced")
        self.restart_btn = buttons["Restart now"]
        self.restart_btn.setToolTip("Close SolarViewer and start it again, as it was started")
        # (out of the box until there is an update: a box shows its buttons again
        # each time it lays them out)
        self.button_box = box
        for button in (self.update_btn, self.skip_btn, self.restart_btn):
            box.removeButton(button)
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

    def on_check_finished(self, install, update):
        # Safeguard: If dialog is closed/hidden, don't update UI
        if not self.isVisible():
            return

        self.install, self.update = install, update
        self.how_label.setText(install.describe())
        self.status.stop()
        if update.error:
            logger.warning("Update check failed: %s", update.error)
            self.status_label.setText("Could not ask for the latest version. Check the network and try again.")
            self.status_label.setToolTip(update.error)
            set_tone(self.status_label, "error")
            self.latest_ver_label.setText("Unknown")
        elif not update.available:
            self.latest_ver_label.setText(update.latest or "Unknown")
            self.status_label.setText(update.message)
            set_tone(self.status_label, "success" if update.latest else "warning")
            if install.blocked and install.by_hand:
                self._show_by_hand(install.by_hand)
        else:
            self.latest_ver_label.setText(update.latest)
            set_tone(self.latest_ver_label, "success")
            self._show_notes(update)
            blocked = update.blocked or install.blocked
            if blocked:
                # Said as it is: there is something newer, and why it is not installed from here
                self.status_label.setText(f"{update.message} {blocked}.")
                set_tone(self.status_label, "warning")
                self._show_by_hand(install.by_hand)
            else:
                self.status_label.setText(update.message)
                set_tone(self.status_label, "success")
                self._show_command("Update now runs:", install.command_text())
                self.button_box.addButton(self.update_btn, QDialogButtonBox.AcceptRole)
                self.update_btn.setDefault(True)
                self.update_btn.setEnabled(True)
            self.button_box.addButton(self.skip_btn, QDialogButtonBox.ResetRole)
            self.skip_btn.setEnabled(self.settings.value(updates.SKIPPED, "") != update.latest)
        self._fit()

    def _fit(self):
        """As tall as its text needs at the width it has. (adjustSize() goes
        by the width the text would like, and a wrapped command or path is
        then cut short.)"""
        for label in self.findChildren(QLabel):
            label.ensurePolished()  # (one just shown still has the font it was made with)
        layout = self.layout()
        layout.activate()
        self.resize(self.width(), layout.totalHeightForWidth(self.width()))

    def _show_notes(self, update):
        """What is new, or that the notes do not say; always the way to read more."""
        self.notes_view.setVisible(bool(update.notes))
        self.notes_missing.setVisible(not update.notes)
        if update.notes:
            self.notes_view.setMarkdown(update.notes)
            # As tall as what it says, up to a limit: three commits are not
            # given the room of a release's notes
            document = self.notes_view.document()
            document.setTextWidth(max(self.notes_view.viewport().width(), self.minimumWidth() - 80))
            height = int(document.size().height()) + 2 * self.notes_view.frameWidth() + 12
            self.notes_view.setFixedHeight(max(64, min(height, NOTES_HEIGHT)))
        else:
            self.notes_missing.setText(
                f"The release notes have no entry for {update.latest} yet."
                if self.install.kind == updates.RELEASE
                else "The new commits could not be listed from here."
            )
        self.notes_link.setVisible(bool(update.notes_url))
        self.notes_block.show()

    def _show_command(self, hint, text):
        """A line of words, and under it a command (either may be missing)."""
        self.command_hint.setText(hint)
        self.command_hint.setVisible(bool(hint))
        self.command_label.setText(text)
        self.command_label.setVisible(bool(text))
        self.command_block.setVisible(bool(hint or text))

    def _show_by_hand(self, by_hand):
        """What to do instead of Update now: its words, then the command if it names one."""
        words, _, command = by_hand.partition("\n")
        self._show_command(words, command)

    def _open_notes(self):
        if self.update.notes_url:
            QDesktopServices.openUrl(QUrl(self.update.notes_url))

    def skip_version(self):
        """This one is not announced at startup again (a later one is)."""
        self.settings.setValue(updates.SKIPPED, self.update.latest)
        self.skip_btn.setEnabled(False)
        self.status_label.setText(f"{self.update.latest} will not be announced at startup again.")
        set_tone(self.status_label, None)
        self.skipped.emit(self.update.latest)

    def start_update(self):
        """Install the update in the background, with the tool this copy was installed with."""
        commands = self.install.commands()
        if not commands:
            return
        reply = QMessageBox.question(
            self,
            "Confirm update",
            "SolarViewer will be updated with:\n\n"
            f"{self.install.command_text()}\n\n"
            "It needs a restart afterwards. Continue?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._before = updates.state(self.install)
        self._steps = [list(command) for command in commands]
        self._output = []
        self.status.start("Installing the update…")
        set_tone(self.status_label, None)
        self.update_btn.setEnabled(False)
        self.update_btn.setText("Updating…")
        self.skip_btn.setEnabled(False)
        self.close_btn.setEnabled(False)
        # Quitting must not cut pip off half-way through an install
        QApplication.instance().aboutToQuit.connect(self._wait_for_pip)
        if self.parent() is not None:
            self.parent().installEventFilter(self)
        self._run_next_step()

    def _run_next_step(self):
        """The next of the update's commands; no shell is involved."""
        command = self._steps.pop(0)
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("GIT_TERMINAL_PROMPT", "0")  # git fails rather than wait for a password nobody can type
        env.insert("PIP_DISABLE_PIP_VERSION_CHECK", "1")
        env.insert("PYTHONUNBUFFERED", "1")
        process.setProcessEnvironment(env)
        cwd = os.getcwd()
        process.setWorkingDirectory(
            cwd if os.access(cwd, os.W_OK) else os.path.expanduser("~")
        )
        process.readyReadStandardOutput.connect(self._show_pip_output)
        process.finished.connect(self._on_update_finished)
        process.errorOccurred.connect(self._on_update_error)
        self._pip = process
        logger.info(f"Executing: {' '.join(command)}")
        process.start(command[0], command[1:])

    def _show_pip_output(self):
        """pip's output, to the log window and the log file; its latest line
        here, so that a long install is seen to move."""
        if self._pip is not None:
            text = bytes(self._pip.readAllStandardOutput()).decode(errors="replace")
            print(text, end="")
            self._output.append(text)
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
            # (The event loop has ended, so the line is painted here. Each
            # command that ends starts the next: the wait is for the last)
            while self._pip is not None:
                pip = self._pip
                if not pip.waitForFinished(100) and pip.state() == QProcess.NotRunning and self._pip is pip:
                    break  # it never started, and has said so
                self._show_pip_output()
                self.pip_line.repaint()
            self._stop_finishing()

    def _stop_finishing(self):
        """The label ends with pip: its outcome comes up inside the wait."""
        was = self._finishing
        if self._finishing:
            self._finishing = False
            end_busy()
        return was

    def _on_update_finished(self, exit_code, exit_status):
        self._show_pip_output()
        worked = exit_status == QProcess.NormalExit and exit_code == 0
        if worked and self._steps:
            self._run_next_step()
            return
        quitting = self._stop_finishing()
        self._pip = None
        self._steps = []
        self.pip_line.hide()
        self.close_btn.setEnabled(True)
        output = "".join(self._output)
        if not worked:
            self._update_failed(updates.failure_reason(output, exit_code))
            return
        # Ending without an error is not an update: what is installed must have changed
        changed, text = updates.outcome(self.install, self.update, self._before)
        if not changed:
            self._update_failed(text)
            return
        if updates.needs_reinstall(self.install, self._before, updates.state(self.install)):
            text += " Its requirements changed too: run pip install -e . in the checkout."
        self.status.stop(text)
        set_tone(self.status_label, "success")
        self.command_block.hide()
        self.current_ver_label.setText(f"{__version__} (running until the restart)")
        for button in (self.update_btn, self.skip_btn):
            self.button_box.removeButton(button)
            button.hide()
        self.updated.emit()
        if quitting:
            # The window goes with the quit: the outcome is said before it does
            QMessageBox.information(
                self,
                "Update successful",
                f"{text}\n\nStart SolarViewer again to use it.",
            )
            self.accept()
            return
        self.status_label.setText(f"{text} Restart SolarViewer to use it.")
        self.button_box.addButton(self.restart_btn, QDialogButtonBox.AcceptRole)
        self.restart_btn.setObjectName("PrimaryButton")
        self.restart_btn.setDefault(True)
        self.restart_btn.show()
        self._fit()

    def _on_update_error(self, error):
        if error == QProcess.FailedToStart and self._pip is not None:
            program = self._pip.program()
            self._stop_finishing()
            self._pip = None
            self._steps = []
            self.close_btn.setEnabled(True)
            self._update_failed(f"Could not start {program}.")

    def _update_failed(self, reason):
        self.status.stop(f"Update failed. {reason}")
        set_tone(self.status_label, "error")
        QMessageBox.critical(
            self,
            "Update failed",
            f"SolarViewer was not updated.\n\n{reason}\n\nThe installer's output is in the log (View > Log Console).",
        )
        self.update_btn.setEnabled(True)
        self.update_btn.setText("Update now")
        self.skip_btn.setEnabled(self.settings.value(updates.SKIPPED, "") != self.update.latest)
        self._fit()

    def restart(self):
        """Close SolarViewer and start it again as it was started."""
        self.accept()
        QTimer.singleShot(100, restart_application)

    def reject(self):
        # Closing mid-install would stop pip half-way
        if self._pip is not None:
            self.status_label.setText("Installing the update… closing has to wait until it finishes.")
            return
        super().reject()

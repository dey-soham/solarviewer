from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QDoubleSpinBox,
    QComboBox,
    QMessageBox,
    QApplication,
)
from PyQt5.QtCore import Qt
import os
import sys

from ..ui.app import display_scale, saved_window_system, smallest_ui_scale
from ..ui.geometry import fit_width_to_text
from ..ui.widgets import button_bar, dialog_layout
from ..ui.styles import SPACE_M, SPACE_XS, set_hand_cursor


WINDOW_SYSTEM_CHOICES = (
    ("Automatic", "auto"),
    ("X11 (XWayland)", "x11"),
    ("Wayland", "wayland"),
)


class PreferencesDialog(QDialog):
    """Dialog for application preferences including UI scale settings."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(400)

        # Get current settings
        from PyQt5.QtCore import QSettings

        self.settings = QSettings("SolarViewer", "SolarViewer")
        self.lowest_scale = smallest_ui_scale(display_scale())

        self.setup_ui()
        set_hand_cursor(self)

    def setup_ui(self):
        layout = dialog_layout(self)

        # UI Scale group
        scale_group = QGroupBox("UI scale")
        scale_layout = QVBoxLayout(scale_group)
        scale_layout.setContentsMargins(SPACE_M, SPACE_XS, SPACE_M, SPACE_XS)

        # Description label
        desc_label = QLabel(
            "Adjust the UI scale factor for high-DPI displays.\n"
            "Default is 1.0 (100%). Increase for larger UI elements.\n"
            f"The smallest this display can show is {self.lowest_scale:.2f}x."
        )
        desc_label.setObjectName("Hint")
        desc_label.setWordWrap(True)
        scale_layout.addWidget(desc_label)

        # Scale slider with value display
        slider_layout = QHBoxLayout()

        # Scale slider - use 5% increments (values are percentages, up to 200)
        from PyQt5.QtWidgets import QSlider

        self.scale_slider = QSlider(Qt.Horizontal)
        self.scale_slider.setMinimum(round(self.lowest_scale * 100))
        self.scale_slider.setMaximum(200)  # 2.0x
        self.scale_slider.setSingleStep(5)  # 5% increments when using arrows
        self.scale_slider.setPageStep(10)  # 10% increments when clicking track
        self.scale_slider.setTickInterval(25)  # Tick marks every 25%
        self.scale_slider.setTickPosition(QSlider.TicksBelow)

        # Load current scale value
        current_scale = self.settings.value("ui_scale_factor", 1.0, type=float)
        self.scale_slider.setValue(round(current_scale * 100))

        # Spinbox for precise value entry
        self.scale_spinbox = QDoubleSpinBox()
        self.scale_spinbox.setRange(self.lowest_scale, 2.0)
        self.scale_spinbox.setSingleStep(0.05)
        self.scale_spinbox.setDecimals(2)
        self.scale_spinbox.setSuffix("x")
        self.scale_spinbox.setValue(current_scale)
        fit_width_to_text(self.scale_spinbox, "2.00x")

        # Connect slider and spinbox to sync values
        self.scale_slider.valueChanged.connect(self._on_slider_changed)
        self.scale_spinbox.valueChanged.connect(self._on_spinbox_changed)

        slider_layout.addWidget(QLabel(f"{self.lowest_scale:.2f}x"))
        slider_layout.addWidget(self.scale_slider, 1)
        slider_layout.addWidget(QLabel("2.00x"))
        slider_layout.addWidget(self.scale_spinbox)

        scale_layout.addLayout(slider_layout)

        # Preset buttons row
        preset_layout = QHBoxLayout()
        presets_label = QLabel("Presets")
        presets_label.setObjectName("Hint")
        preset_layout.addWidget(presets_label)

        presets = [
            ("75%", 0.75),
            ("100%", 1.0),
            ("125%", 1.25),
            ("150%", 1.5),
            ("175%", 1.75),
        ]
        for label, value in presets:
            if value < self.lowest_scale:
                continue
            btn = QPushButton(label)
            btn.clicked.connect(lambda checked, v=value: self._set_scale(v))
            preset_layout.addWidget(btn)
        preset_layout.addStretch()
        scale_layout.addLayout(preset_layout)

        layout.addWidget(scale_group)

        if sys.platform.startswith("linux"):
            layout.addWidget(self._window_system_group())

        # Restart notice
        notice_text = QLabel("Changes here take effect after the application restarts.")
        notice_text.setObjectName("Hint")
        notice_text.setWordWrap(True)
        layout.addWidget(notice_text)

        layout.addStretch()

        # Button box
        box, _ = button_bar(primary=("OK", self._save_and_close), secondary=("Cancel", self.reject))
        layout.addWidget(box)

    def _window_system_group(self):
        """Wayland or X11, for the next start."""
        group = QGroupBox("Window system")
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(SPACE_M, SPACE_XS, SPACE_M, SPACE_XS)

        self.window_system = QComboBox()
        for label, value in WINDOW_SYSTEM_CHOICES:
            self.window_system.addItem(label, value)
        self.window_system.setCurrentIndex(self.window_system.findData(saved_window_system()))
        row = QHBoxLayout()
        row.addWidget(self.window_system)
        now = {"wayland": "Wayland", "xcb": "X11"}.get(QApplication.platformName())
        if now:
            in_use = QLabel(f"In use now: {now}")
            in_use.setObjectName("Hint")
            row.addWidget(in_use)
        row.addStretch()
        group_layout.addLayout(row)

        hint = QLabel(
            "Wayland gives sharper text on scaled screens, but windows get a plain "
            "title bar and cannot bring themselves to the front. Automatic uses "
            "Wayland on GNOME when a screen is scaled."
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        group_layout.addWidget(hint)
        if os.environ.get("QT_QPA_PLATFORM"):
            # (set by the user: the application never leaves its own there)
            overruled = QLabel("QT_QPA_PLATFORM is set where SolarViewer starts, and wins over this.")
            overruled.setObjectName("Hint")
            overruled.setWordWrap(True)
            group_layout.addWidget(overruled)
        return group

    def _on_slider_changed(self, value):
        """Update spinbox when slider value changes."""
        scale = value / 100.0
        # Block signals to prevent feedback loop
        self.scale_spinbox.blockSignals(True)
        self.scale_spinbox.setValue(scale)
        self.scale_spinbox.blockSignals(False)

    def _on_spinbox_changed(self, value):
        """Update slider when spinbox value changes."""
        # Block signals to prevent feedback loop
        self.scale_slider.blockSignals(True)
        self.scale_slider.setValue(round(value * 100))
        self.scale_slider.blockSignals(False)

    def _set_scale(self, value):
        """Set scale from preset button."""
        self.scale_spinbox.setValue(value)  # This will trigger slider update via signal

    def _save_and_close(self):
        """Save settings and close dialog."""
        scale = self.scale_spinbox.value()  # Use spinbox for more precise value
        # (what is in use: a saved value too low for this display is not)
        current_scale = max(
            self.settings.value("ui_scale_factor", 1.0, type=float), self.lowest_scale
        )

        self.settings.setValue("ui_scale_factor", scale)
        changes = []
        if abs(scale - current_scale) > 0.01:
            changes.append(f"UI scale changed to {scale:.2f}x.")
        if hasattr(self, "window_system"):
            system = self.window_system.currentData()
            if system != saved_window_system():
                changes.append(f"Window system changed to {self.window_system.currentText()}.")
            self.settings.setValue("window_system", system)

        # If something changed, offer to restart
        if changes:
            reply = QMessageBox.question(
                self,
                "Restart required",
                " ".join(changes) + "\n\n"
                "The application needs to restart for changes to take effect.\n\n"
                "Would you like to restart now?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )

            if reply == QMessageBox.Yes:
                # Restart the application

                # Accept dialog first
                self.accept()

                # Get the application instance and quit
                app = QApplication.instance()
                if app:
                    # Schedule restart
                    from PyQt5.QtCore import QTimer

                    QTimer.singleShot(100, lambda: self._do_restart())
                return

        self.accept()

    def _do_restart(self):
        """Perform application restart."""
        import sys
        import os

        # The command as it was typed: sys.argv loses a "python -m"
        python = sys.executable
        args = sys.orig_argv[1:]
        self.settings.sync()  # (exec does not wait for QSettings to write)

        def start_new_instance():
            os.execv(python, [python] + args)

        app = QApplication.instance()
        if app:
            # Connected last, so that what is already waiting for the quit
            # (the batch runner, a pip update) still runs. Closing the windows
            # stops the workers and removes the tabs' temporary files.
            app.aboutToQuit.connect(start_new_instance)
            app.closeAllWindows()
            app.quit()
        else:
            start_new_instance()



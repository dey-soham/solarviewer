from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QDoubleSpinBox,
    QDialogButtonBox,
    QMessageBox,
    QFrame,
    QApplication,
)
from PyQt5.QtCore import Qt

from ..ui.styles import set_hand_cursor


class PreferencesDialog(QDialog):
    """Dialog for application preferences including UI scale settings."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(400)

        # Import theme manager for styling
        try:
            from ..ui.styles import theme_manager
        except ImportError:
            from ..ui.styles import theme_manager

        palette = theme_manager.palette
        is_dark = theme_manager.is_dark

        # Get current settings
        from PyQt5.QtCore import QSettings

        self.settings = QSettings("SolarViewer", "SolarViewer")

        self.setup_ui()
        set_hand_cursor(self)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)

        # UI Scale group
        scale_group = QGroupBox("UI Scale")
        scale_layout = QVBoxLayout(scale_group)

        # Description label
        desc_label = QLabel(
            "Adjust the UI scale factor for high-DPI displays.\n"
            "Default is 1.0 (100%). Increase for larger UI elements."
        )
        desc_label.setWordWrap(True)
        scale_layout.addWidget(desc_label)

        # Scale slider with value display
        slider_layout = QHBoxLayout()

        # Scale slider - use 5% increments (values 50-200 representing 0.50-2.00)
        from PyQt5.QtWidgets import QSlider

        self.scale_slider = QSlider(Qt.Horizontal)
        self.scale_slider.setMinimum(50)  # 0.5x
        self.scale_slider.setMaximum(200)  # 2.0x
        self.scale_slider.setSingleStep(5)  # 5% increments when using arrows
        self.scale_slider.setPageStep(10)  # 10% increments when clicking track
        self.scale_slider.setTickInterval(25)  # Tick marks every 25%
        self.scale_slider.setTickPosition(QSlider.TicksBelow)

        # Load current scale value
        current_scale = self.settings.value("ui_scale_factor", 1.0, type=float)
        self.scale_slider.setValue(int(current_scale * 100))

        # Spinbox for precise value entry
        self.scale_spinbox = QDoubleSpinBox()
        self.scale_spinbox.setRange(0.5, 2.0)
        self.scale_spinbox.setSingleStep(0.05)
        self.scale_spinbox.setDecimals(2)
        self.scale_spinbox.setSuffix("x")
        self.scale_spinbox.setValue(current_scale)
        self.scale_spinbox.setFixedWidth(75)

        # Connect slider and spinbox to sync values
        self.scale_slider.valueChanged.connect(self._on_slider_changed)
        self.scale_spinbox.valueChanged.connect(self._on_spinbox_changed)

        slider_layout.addWidget(QLabel("0.5x"))
        slider_layout.addWidget(self.scale_slider, 1)
        slider_layout.addWidget(QLabel("2.0x"))
        slider_layout.addWidget(self.scale_spinbox)

        scale_layout.addLayout(slider_layout)

        # Preset buttons row
        preset_layout = QHBoxLayout()
        preset_layout.addWidget(QLabel("Presets:"))

        presets = [
            ("75%", 0.75),
            ("100%", 1.0),
            ("125%", 1.25),
            ("150%", 1.5),
            ("175%", 1.75),
        ]
        for label, value in presets:
            btn = QPushButton(label)
            btn.setFixedWidth(50)
            btn.clicked.connect(lambda checked, v=value: self._set_scale(v))
            preset_layout.addWidget(btn)
        preset_layout.addStretch()
        scale_layout.addLayout(preset_layout)

        layout.addWidget(scale_group)

        # Restart notice
        notice_frame = QFrame()
        notice_frame.setFrameShape(QFrame.StyledPanel)
        '''notice_frame.setStyleSheet("""
            QFrame {
                background-color: rgba(255, 193, 7, 0.15);
                border: 1px solid rgba(255, 193, 7, 0.5);
                border-radius: 6px;
                padding: 8px;
            }
            QLabel {
                color: inherit;
            }
        """)'''
        notice_layout = QHBoxLayout(notice_frame)
        notice_layout.setContentsMargins(10, 8, 10, 8)
        notice_icon = QLabel("⚠️")
        notice_icon.setStyleSheet("font-size: 16px;")
        notice_text = QLabel(
            "Scale changes require application restart to take effect."
        )
        notice_layout.addWidget(notice_icon)
        notice_layout.addWidget(notice_text, 1)
        layout.addWidget(notice_frame)

        layout.addStretch()

        # Button box
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self._save_and_close)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

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
        self.scale_slider.setValue(int(value * 100))
        self.scale_slider.blockSignals(False)

    def _set_scale(self, value):
        """Set scale from preset button."""
        self.scale_spinbox.setValue(value)  # This will trigger slider update via signal

    def _save_and_close(self):
        """Save settings and close dialog."""
        scale = self.scale_spinbox.value()  # Use spinbox for more precise value
        current_scale = self.settings.value("ui_scale_factor", 1.0, type=float)

        self.settings.setValue("ui_scale_factor", scale)

        # If scale changed, offer to restart
        if abs(scale - current_scale) > 0.01:
            reply = QMessageBox.question(
                self,
                "Restart Required",
                f"UI scale changed to {scale:.2f}x.\n\n"
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

        # Get the current executable and arguments
        python = sys.executable
        args = sys.argv[:]

        # Quit current app
        app = QApplication.instance()
        if app:
            app.quit()

        # Start new instance
        os.execv(python, [python] + args)



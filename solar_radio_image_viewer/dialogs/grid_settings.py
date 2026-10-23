from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QLabel,
    QDoubleSpinBox,
    QGridLayout,
    QComboBox,
)

from ..ui.widgets import button_bar
from ..ui.styles import set_hand_cursor


class GridSettingsDialog(QDialog):
    """Dialog for configuring grid display settings."""

    def __init__(self, parent=None, grid_style=None):
        super().__init__(parent)
        self.setWindowTitle("Grid Settings")
        self.setMinimumWidth(350)
        self.grid_style = grid_style or {}
        self.parent_viewer = parent
        self.setup_ui()
        set_hand_cursor(self)

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # Color selection
        color_group = QGroupBox("Color")
        color_layout = QHBoxLayout(color_group)

        color_label = QLabel("Grid Color:")
        self.color_combo = QComboBox()
        colors = [
            "white",
            "black",
            "red",
            "green",
            "blue",
            "cyan",
            "magenta",
            "yellow",
            "gray",
        ]
        for color in colors:
            self.color_combo.addItem(color)
        self.color_combo.setCurrentText(self.grid_style.get("color", "white"))
        color_layout.addWidget(color_label)
        color_layout.addWidget(self.color_combo)
        layout.addWidget(color_group)

        # Style settings
        style_group = QGroupBox("Style")
        style_layout = QGridLayout(style_group)

        # Line style
        linestyle_label = QLabel("Line Style:")
        self.linestyle_combo = QComboBox()
        linestyles = [
            ("-", "Solid"),
            ("--", "Dashed"),
            (":", "Dotted"),
            ("-.", "Dash-dot"),
        ]
        for style_code, style_name in linestyles:
            self.linestyle_combo.addItem(style_name, style_code)

        # Set current line style
        current_style = self.grid_style.get("linestyle", "--")
        for i in range(self.linestyle_combo.count()):
            if self.linestyle_combo.itemData(i) == current_style:
                self.linestyle_combo.setCurrentIndex(i)
                break

        style_layout.addWidget(linestyle_label, 0, 0)
        style_layout.addWidget(self.linestyle_combo, 0, 1)

        # Opacity
        alpha_label = QLabel("Opacity:")
        self.alpha_spinbox = QDoubleSpinBox()
        self.alpha_spinbox.setRange(0.1, 1.0)
        self.alpha_spinbox.setSingleStep(0.1)
        self.alpha_spinbox.setValue(self.grid_style.get("alpha", 0.5))
        style_layout.addWidget(alpha_label, 1, 0)
        style_layout.addWidget(self.alpha_spinbox, 1, 1)
        layout.addWidget(style_group)
        box, _ = button_bar(primary=("Apply", self.on_apply), secondary=("Close", self.close))
        layout.addWidget(box)

    def on_apply(self):
        """Apply settings without closing dialog."""
        try:
            if self.parent_viewer:
                self.parent_viewer.grid_style["color"] = self.color_combo.currentText()
                self.parent_viewer.grid_style["linestyle"] = (
                    self.linestyle_combo.currentData()
                )
                self.parent_viewer.grid_style["alpha"] = self.alpha_spinbox.value()
                self.parent_viewer.schedule_plot()
                self.parent_viewer.show_status_message("Grid settings applied")
        except RuntimeError:
            self.close()
        except Exception as e:
            if self.parent_viewer:
                self.parent_viewer.show_status_message(f"Error applying settings: {e}")

    def get_settings(self):
        """Return the current settings."""
        return {
            "color": self.color_combo.currentText(),
            "linestyle": self.linestyle_combo.currentData(),
            "alpha": self.alpha_spinbox.value(),
        }



from PyQt5.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QComboBox,
)

from ..ui.widgets import button_bar, dialog_layout
from ..ui.styles import set_hand_cursor
from .form import color_combo, field_label, form_block


class GridSettingsDialog(QDialog):
    """Dialog for configuring grid display settings."""

    def __init__(self, parent=None, grid_style=None):
        super().__init__(parent)
        self.setWindowTitle("Grid settings")
        self.grid_style = grid_style or {}
        self.parent_viewer = parent
        self.setup_ui()
        set_hand_cursor(self)

    def setup_ui(self):
        layout = dialog_layout(self)
        style_layout = form_block(layout)

        # Color selection
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
        self.color_combo = color_combo(colors, self.grid_style.get("color", "white"))
        style_layout.addWidget(field_label("Colour"), 0, 0)
        style_layout.addWidget(self.color_combo, 0, 1)

        # Line style
        linestyle_label = field_label("Line style")
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

        style_layout.addWidget(linestyle_label, 1, 0)
        style_layout.addWidget(self.linestyle_combo, 1, 1)

        # Opacity
        alpha_label = field_label("Opacity")
        self.alpha_spinbox = QDoubleSpinBox()
        self.alpha_spinbox.setRange(0.1, 1.0)
        self.alpha_spinbox.setSingleStep(0.1)
        self.alpha_spinbox.setValue(self.grid_style.get("alpha", 0.5))
        style_layout.addWidget(alpha_label, 2, 0)
        style_layout.addWidget(self.alpha_spinbox, 2, 1)
        box, _ = button_bar(primary=("Apply", self.on_apply), secondary=("Close", self.close))
        layout.addWidget(box)

    def on_apply(self):
        """Apply settings without closing dialog."""
        try:
            if self.parent_viewer:
                self.parent_viewer.grid_style["color"] = self.color_combo.currentData()
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
            "color": self.color_combo.currentData(),
            "linestyle": self.linestyle_combo.currentData(),
            "alpha": self.alpha_spinbox.value(),
        }



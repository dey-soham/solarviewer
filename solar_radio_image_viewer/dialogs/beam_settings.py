from PyQt5.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
)

from ..ui.widgets import button_bar, dialog_layout
from ..ui.styles import set_hand_cursor
from .form import color_combo, field_label, form_block


class BeamSettingsDialog(QDialog):
    """Dialog for configuring beam display settings."""

    def __init__(self, parent=None, beam_style=None):
        super().__init__(parent)
        self.setWindowTitle("Beam settings")
        self.beam_style = beam_style or {}
        self.parent_viewer = parent
        self.setup_ui()
        set_hand_cursor(self)

    def setup_ui(self):
        layout = dialog_layout(self)

        # Color selection
        color_layout = form_block(layout, "Colours")

        # Edge color
        edge_label = field_label("Edge colour")
        colors = [
            "black",
            "white",
            "red",
            "green",
            "blue",
            "cyan",
            "magenta",
            "yellow",
            "gray",
        ]
        self.edge_combo = color_combo(colors, self.beam_style.get("edgecolor", "black"))
        color_layout.addWidget(edge_label, 0, 0)
        color_layout.addWidget(self.edge_combo, 0, 1)

        # Face color
        face_label = field_label("Fill colour")
        self.face_combo = color_combo(colors, self.beam_style.get("facecolor", "white"))
        color_layout.addWidget(face_label, 1, 0)
        color_layout.addWidget(self.face_combo, 1, 1)

        # Style settings
        style_layout = form_block(layout, "Style")

        # Line width
        linewidth_label = field_label("Edge width")
        self.linewidth_spinbox = QDoubleSpinBox()
        self.linewidth_spinbox.setRange(0.5, 5.0)
        self.linewidth_spinbox.setSingleStep(0.5)
        self.linewidth_spinbox.setValue(self.beam_style.get("linewidth", 1.5))
        style_layout.addWidget(linewidth_label, 0, 0)
        style_layout.addWidget(self.linewidth_spinbox, 0, 1)

        # Opacity
        alpha_label = field_label("Opacity")
        self.alpha_spinbox = QDoubleSpinBox()
        self.alpha_spinbox.setRange(0.1, 1.0)
        self.alpha_spinbox.setSingleStep(0.1)
        self.alpha_spinbox.setValue(self.beam_style.get("alpha", 0.4))
        style_layout.addWidget(alpha_label, 1, 0)
        style_layout.addWidget(self.alpha_spinbox, 1, 1)
        box, _ = button_bar(primary=("Apply", self.on_apply), secondary=("Close", self.close))
        layout.addWidget(box)

    def on_apply(self):
        """Apply settings without closing dialog."""
        try:
            if self.parent_viewer:
                self.parent_viewer.beam_style["edgecolor"] = (
                    self.edge_combo.currentData()
                )
                self.parent_viewer.beam_style["facecolor"] = (
                    self.face_combo.currentData()
                )
                self.parent_viewer.beam_style["linewidth"] = (
                    self.linewidth_spinbox.value()
                )
                self.parent_viewer.beam_style["alpha"] = self.alpha_spinbox.value()
                self.parent_viewer.schedule_plot()
                self.parent_viewer.show_status_message("Beam settings applied")
        except RuntimeError:
            self.close()
        except Exception as e:
            if self.parent_viewer:
                self.parent_viewer.show_status_message(f"Error applying settings: {e}")

    def get_settings(self):
        """Return the current settings."""
        return {
            "edgecolor": self.edge_combo.currentData(),
            "facecolor": self.face_combo.currentData(),
            "linewidth": self.linewidth_spinbox.value(),
            "alpha": self.alpha_spinbox.value(),
        }



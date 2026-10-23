"""
Solar disk style: the controls of the main window's Solar Disk Settings
Style tab, and a dialog with only them (used by the Create Video window).
"""

from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ..ui.widgets import button_bar
from ..ui.styles import set_hand_cursor

_COLORS = ["yellow", "white", "red", "green", "blue", "cyan", "magenta", "black"]
_LINESTYLES = [("-", "Solid"), ("--", "Dashed"), (":", "Dotted"), ("-.", "Dash-dot")]


class SolarDiskStyleWidget(QWidget):
    """Colour, line style, width, opacity and centre marker of the disk."""

    def __init__(self, style, parent=None):
        super().__init__(parent)
        from ..processing.plot_style import DEFAULT_SOLAR_DISK_STYLE

        # A style without show_center shows the centre (as the main window did)
        style = {**DEFAULT_SOLAR_DISK_STYLE, **style, "show_center": style.get("show_center", True)}
        style_layout = QVBoxLayout(self)
        style_layout.setContentsMargins(0, 0, 0, 0)

        # Color selection
        color_group = QGroupBox("Color")
        color_layout = QHBoxLayout(color_group)
        self.color_combo = QComboBox()
        self.color_combo.addItems(_COLORS)
        if self.color_combo.findText(style["color"]) < 0:
            self.color_combo.addItem(style["color"])
        self.color_combo.setCurrentText(style["color"])
        color_layout.addWidget(QLabel("Disk Color:"))
        color_layout.addWidget(self.color_combo)
        style_layout.addWidget(color_group)

        # Line style
        line_group = QGroupBox("Line Style")
        line_layout = QGridLayout(line_group)
        self.linestyle_combo = QComboBox()
        for style_code, style_name in _LINESTYLES:
            self.linestyle_combo.addItem(style_name, style_code)
        index = self.linestyle_combo.findData(style["linestyle"])
        if index >= 0:
            self.linestyle_combo.setCurrentIndex(index)
        line_layout.addWidget(QLabel("Line Style:"), 0, 0)
        line_layout.addWidget(self.linestyle_combo, 0, 1)

        # Line width
        self.linewidth_spinbox = QDoubleSpinBox()
        self.linewidth_spinbox.setRange(0.5, 5.0)
        self.linewidth_spinbox.setSingleStep(0.5)
        self.linewidth_spinbox.setValue(style["linewidth"])
        line_layout.addWidget(QLabel("Line Width:"), 1, 0)
        line_layout.addWidget(self.linewidth_spinbox, 1, 1)

        # Alpha/transparency
        self.alpha_spinbox = QDoubleSpinBox()
        self.alpha_spinbox.setRange(0.1, 1.0)
        self.alpha_spinbox.setSingleStep(0.1)
        self.alpha_spinbox.setValue(style["alpha"])
        line_layout.addWidget(QLabel("Opacity:"), 2, 0)
        line_layout.addWidget(self.alpha_spinbox, 2, 1)
        style_layout.addWidget(line_group)

        # Center marker toggle
        center_marker_group = QGroupBox("Center Marker")
        center_marker_layout = QVBoxLayout(center_marker_group)
        self.show_center_checkbox = QCheckBox("Show center marker (+)")
        self.show_center_checkbox.setChecked(style.get("show_center", True))
        center_marker_layout.addWidget(self.show_center_checkbox)
        style_layout.addWidget(center_marker_group)

    def get_style(self):
        return {
            "color": self.color_combo.currentText(),
            "linestyle": self.linestyle_combo.currentData(),
            "linewidth": self.linewidth_spinbox.value(),
            "alpha": self.alpha_spinbox.value(),
            "show_center": self.show_center_checkbox.isChecked(),
        }


class SolarDiskStyleDialog(QDialog):
    """Solar disk style alone, applied to ``parent.solar_disk_style`` like
    BeamSettingsDialog applies the beam style."""

    def __init__(self, parent=None, solar_disk_style=None):
        super().__init__(parent)
        self.setWindowTitle("Solar Disk Style")
        self.setMinimumWidth(350)
        self.parent_viewer = parent
        layout = QVBoxLayout(self)
        self.style_widget = SolarDiskStyleWidget(solar_disk_style or {}, self)
        layout.addWidget(self.style_widget)
        box, _ = button_bar(primary=("Apply", self.on_apply), secondary=("Close", self.close))
        layout.addWidget(box)
        set_hand_cursor(self)

    def on_apply(self):
        """Apply settings without closing dialog."""
        if self.parent_viewer:
            self.parent_viewer.solar_disk_style.update(self.get_settings())
            self.parent_viewer.schedule_plot()
            self.parent_viewer.show_status_message("Solar disk settings applied")

    def get_settings(self):
        return self.style_widget.get_style()

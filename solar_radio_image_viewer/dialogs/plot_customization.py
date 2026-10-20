from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QLineEdit,
    QLabel,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QGridLayout,
    QDialogButtonBox,
    QComboBox,
    QCheckBox,
    QWidget,
)
from PyQt5.QtCore import Qt

from ..ui.styles import set_hand_cursor


class PlotCustomizationDialog(QDialog):
    """Dialog for customizing plot appearance (labels, fonts, colors)."""

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self.setWindowTitle("Plot Customization")
        self.setMinimumWidth(660)
        self.setMaximumHeight(1280)
        self.settings = settings.copy() if settings else {}
        self.setup_ui()
        set_hand_cursor(self)

    def setup_ui(self):
        from PyQt5.QtWidgets import QTabWidget

        outer_layout = QVBoxLayout(self)
        outer_layout.setSpacing(8)
        outer_layout.setContentsMargins(10, 10, 10, 10)

        # Create tab widget for organized sections
        tab_widget = QTabWidget()

        # ===== TAB 1: TEXT & LABELS =====
        text_tab = QWidget()
        text_layout = QVBoxLayout(text_tab)
        text_layout.setSpacing(8)

        # Labels Section
        labels_group = QGroupBox("Labels")
        labels_layout = QGridLayout(labels_group)
        labels_layout.setSpacing(8)

        labels_layout.addWidget(QLabel("X-Axis:"), 0, 0)
        self.xlabel_edit = QLineEdit(self.settings.get("xlabel", ""))
        self.xlabel_edit.setPlaceholderText("Auto")
        labels_layout.addWidget(self.xlabel_edit, 0, 1)

        labels_layout.addWidget(QLabel("Y-Axis:"), 0, 2)
        self.ylabel_edit = QLineEdit(self.settings.get("ylabel", ""))
        self.ylabel_edit.setPlaceholderText("Auto")
        labels_layout.addWidget(self.ylabel_edit, 0, 3)

        labels_layout.addWidget(QLabel("Title:"), 1, 0)
        self.title_edit = QLineEdit(self.settings.get("title", ""))
        self.title_edit.setPlaceholderText("Auto")
        labels_layout.addWidget(self.title_edit, 1, 1)

        labels_layout.addWidget(QLabel("Colorbar:"), 1, 2)
        self.colorbar_label_edit = QLineEdit(self.settings.get("colorbar_label", ""))
        self.colorbar_label_edit.setPlaceholderText("e.g., Jy/beam")
        labels_layout.addWidget(self.colorbar_label_edit, 1, 3)

        text_layout.addWidget(labels_group)

        # Font Sizes Section (compact grid)
        fonts_group = QGroupBox("Font Sizes")
        fonts_layout = QGridLayout(fonts_group)
        fonts_layout.setSpacing(8)

        fonts_layout.addWidget(QLabel("Axis Labels:"), 0, 0)
        self.axis_label_size = QSpinBox()
        self.axis_label_size.setRange(1, 50)
        self.axis_label_size.setValue(self.settings.get("axis_label_fontsize", 12))
        fonts_layout.addWidget(self.axis_label_size, 0, 1)

        fonts_layout.addWidget(QLabel("Axis Ticks:"), 0, 2)
        self.axis_tick_size = QSpinBox()
        self.axis_tick_size.setRange(1, 50)
        self.axis_tick_size.setValue(self.settings.get("axis_tick_fontsize", 10))
        fonts_layout.addWidget(self.axis_tick_size, 0, 3)

        fonts_layout.addWidget(QLabel("Title:"), 1, 0)
        self.title_size = QSpinBox()
        self.title_size.setRange(1, 50)
        self.title_size.setValue(self.settings.get("title_fontsize", 12))
        fonts_layout.addWidget(self.title_size, 1, 1)

        fonts_layout.addWidget(QLabel("Colorbar:"), 1, 2)
        self.colorbar_label_size = QSpinBox()
        self.colorbar_label_size.setRange(1, 50)
        self.colorbar_label_size.setValue(
            self.settings.get("colorbar_label_fontsize", 10)
        )
        fonts_layout.addWidget(self.colorbar_label_size, 1, 3)

        fonts_layout.addWidget(QLabel("Colorbar Ticks:"), 2, 0)
        self.colorbar_tick_size = QSpinBox()
        self.colorbar_tick_size.setRange(1, 50)
        self.colorbar_tick_size.setValue(
            self.settings.get("colorbar_tick_fontsize", 10)
        )
        fonts_layout.addWidget(self.colorbar_tick_size, 2, 1)

        # Scale buttons
        scale_layout = QHBoxLayout()
        scale_layout.addWidget(QLabel("Scale All:"))
        scale_down_btn = QPushButton("-")
        scale_down_btn.setFixedWidth(30)
        scale_down_btn.clicked.connect(self._scale_fonts_down)
        scale_up_btn = QPushButton("+")
        scale_up_btn.setFixedWidth(30)
        scale_up_btn.clicked.connect(self._scale_fonts_up)
        scale_layout.addWidget(scale_down_btn)
        scale_layout.addWidget(scale_up_btn)
        scale_layout.addStretch()
        fonts_layout.addLayout(scale_layout, 2, 2, 1, 2)

        text_layout.addWidget(fonts_group)
        text_layout.addStretch()

        tab_widget.addTab(text_tab, "Text")

        # ===== TAB 2: COLORS & STYLE =====
        style_tab = QWidget()
        style_layout = QVBoxLayout(style_tab)
        style_layout.setSpacing(8)

        # Colors Section (compact 2-column grid)
        colors_group = QGroupBox("Colors")
        colors_layout = QGridLayout(colors_group)
        colors_layout.setSpacing(6)

        # Row 0: Plot BG, Figure BG
        colors_layout.addWidget(QLabel("Plot BG:"), 0, 0)
        self.plot_bg_color = self.settings.get("plot_bg_color", "auto")
        self.plot_bg_preview = QLabel()
        self.plot_bg_preview.setFixedSize(20, 20)
        self._update_color_preview(self.plot_bg_preview, self.plot_bg_color)
        self.plot_bg_btn = QPushButton("...")
        self.plot_bg_btn.setFixedWidth(30)
        self.plot_bg_btn.clicked.connect(self._pick_plot_bg_color)
        self.plot_bg_auto_btn = QPushButton("A")
        self.plot_bg_auto_btn.setFixedWidth(25)
        self.plot_bg_auto_btn.setToolTip("Auto")
        self.plot_bg_auto_btn.clicked.connect(lambda: self._set_plot_bg_auto())
        plot_bg_row = QHBoxLayout()
        plot_bg_row.addWidget(self.plot_bg_preview)
        plot_bg_row.addWidget(self.plot_bg_btn)
        plot_bg_row.addWidget(self.plot_bg_auto_btn)
        colors_layout.addLayout(plot_bg_row, 0, 1)

        colors_layout.addWidget(QLabel("Figure BG:"), 0, 2)
        self.figure_bg_color = self.settings.get("figure_bg_color", "auto")
        self.figure_bg_preview = QLabel()
        self.figure_bg_preview.setFixedSize(20, 20)
        self._update_color_preview(self.figure_bg_preview, self.figure_bg_color)
        self.figure_bg_btn = QPushButton("...")
        self.figure_bg_btn.setFixedWidth(30)
        self.figure_bg_btn.clicked.connect(self._pick_figure_bg_color)
        self.figure_bg_auto_btn = QPushButton("A")
        self.figure_bg_auto_btn.setFixedWidth(25)
        self.figure_bg_auto_btn.setToolTip("Auto")
        self.figure_bg_auto_btn.clicked.connect(lambda: self._set_figure_bg_auto())
        figure_bg_row = QHBoxLayout()
        figure_bg_row.addWidget(self.figure_bg_preview)
        figure_bg_row.addWidget(self.figure_bg_btn)
        figure_bg_row.addWidget(self.figure_bg_auto_btn)
        colors_layout.addLayout(figure_bg_row, 0, 3)

        # Row 1: Text, Tick
        colors_layout.addWidget(QLabel("Text:"), 1, 0)
        self.text_color = self.settings.get("text_color", "auto")
        self.text_color_preview = QLabel()
        self.text_color_preview.setFixedSize(20, 20)
        self._update_color_preview(self.text_color_preview, self.text_color)
        self.text_color_btn = QPushButton("...")
        self.text_color_btn.setFixedWidth(30)
        self.text_color_btn.clicked.connect(self._pick_text_color)
        self.text_color_auto_btn = QPushButton("A")
        self.text_color_auto_btn.setFixedWidth(25)
        self.text_color_auto_btn.setToolTip("Auto")
        self.text_color_auto_btn.clicked.connect(self._set_text_color_auto)
        text_color_row = QHBoxLayout()
        text_color_row.addWidget(self.text_color_preview)
        text_color_row.addWidget(self.text_color_btn)
        text_color_row.addWidget(self.text_color_auto_btn)
        colors_layout.addLayout(text_color_row, 1, 1)

        colors_layout.addWidget(QLabel("Ticks:"), 1, 2)
        self.tick_color = self.settings.get("tick_color", "auto")
        self.tick_color_preview = QLabel()
        self.tick_color_preview.setFixedSize(20, 20)
        self._update_color_preview(self.tick_color_preview, self.tick_color)
        self.tick_color_btn = QPushButton("...")
        self.tick_color_btn.setFixedWidth(30)
        self.tick_color_btn.clicked.connect(self._pick_tick_color)
        self.tick_color_auto_btn = QPushButton("A")
        self.tick_color_auto_btn.setFixedWidth(25)
        self.tick_color_auto_btn.setToolTip("Auto")
        self.tick_color_auto_btn.clicked.connect(self._set_tick_color_auto)
        tick_color_row = QHBoxLayout()
        tick_color_row.addWidget(self.tick_color_preview)
        tick_color_row.addWidget(self.tick_color_btn)
        tick_color_row.addWidget(self.tick_color_auto_btn)
        colors_layout.addLayout(tick_color_row, 1, 3)

        # Row 2: Border color + width
        colors_layout.addWidget(QLabel("Border:"), 2, 0)
        self.border_color = self.settings.get("border_color", "auto")
        self.border_color_preview = QLabel()
        self.border_color_preview.setFixedSize(20, 20)
        self._update_color_preview(self.border_color_preview, self.border_color)
        self.border_color_btn = QPushButton("...")
        self.border_color_btn.setFixedWidth(30)
        self.border_color_btn.clicked.connect(self._pick_border_color)
        self.border_color_auto_btn = QPushButton("A")
        self.border_color_auto_btn.setFixedWidth(25)
        self.border_color_auto_btn.setToolTip("Auto")
        self.border_color_auto_btn.clicked.connect(self._set_border_color_auto)
        border_color_row = QHBoxLayout()
        border_color_row.addWidget(self.border_color_preview)
        border_color_row.addWidget(self.border_color_btn)
        border_color_row.addWidget(self.border_color_auto_btn)
        colors_layout.addLayout(border_color_row, 2, 1)

        colors_layout.addWidget(QLabel("Border Width:"), 2, 2)
        self.border_width = QDoubleSpinBox()
        self.border_width.setRange(0.5, 5.0)
        self.border_width.setSingleStep(0.5)
        self.border_width.setValue(self.settings.get("border_width", 1.0))
        colors_layout.addWidget(self.border_width, 2, 3)

        style_layout.addWidget(colors_group)

        # Tick Marks Section (compact)
        ticks_group = QGroupBox("Tick Marks")
        ticks_layout = QGridLayout(ticks_group)
        ticks_layout.setSpacing(8)

        ticks_layout.addWidget(QLabel("Direction:"), 0, 0)
        self.tick_direction = QComboBox()
        self.tick_direction.addItems(["in", "out"])
        self.tick_direction.setCurrentText(self.settings.get("tick_direction", "out"))
        ticks_layout.addWidget(self.tick_direction, 0, 1)

        ticks_layout.addWidget(QLabel("Length:"), 0, 2)
        self.tick_length = QSpinBox()
        self.tick_length.setRange(1, 20)
        self.tick_length.setValue(self.settings.get("tick_length", 4))
        ticks_layout.addWidget(self.tick_length, 0, 3)

        ticks_layout.addWidget(QLabel("Width:"), 1, 0)
        self.tick_width = QDoubleSpinBox()
        self.tick_width.setRange(0.5, 5.0)
        self.tick_width.setSingleStep(0.5)
        self.tick_width.setValue(self.settings.get("tick_width", 1.0))
        ticks_layout.addWidget(self.tick_width, 1, 1)

        style_layout.addWidget(ticks_group)
        style_layout.addStretch()

        tab_widget.addTab(style_tab, "Style")

        # ===== TAB 3: PADDING =====
        padding_tab = QWidget()
        padding_layout = QVBoxLayout(padding_tab)
        padding_layout.setSpacing(8)

        # Subplot Margins Section
        margins_group = QGroupBox("Plot Margins (0.0 - 1.0)")
        margins_layout = QGridLayout(margins_group)
        margins_layout.setSpacing(10)

        # Left
        margins_layout.addWidget(QLabel("Left:"), 0, 0)
        self.pad_left = QDoubleSpinBox()
        self.pad_left.setRange(0.0, 0.5)
        self.pad_left.setSingleStep(0.01)
        self.pad_left.setDecimals(2)
        self.pad_left.setValue(self.settings.get("pad_left", 0.135))
        margins_layout.addWidget(self.pad_left, 0, 1)

        # Right
        margins_layout.addWidget(QLabel("Right:"), 0, 2)
        self.pad_right = QDoubleSpinBox()
        self.pad_right.setRange(0.5, 1.0)
        self.pad_right.setSingleStep(0.01)
        self.pad_right.setDecimals(2)
        self.pad_right.setValue(self.settings.get("pad_right", 1.0))
        margins_layout.addWidget(self.pad_right, 0, 3)

        # Top
        margins_layout.addWidget(QLabel("Top:"), 1, 0)
        self.pad_top = QDoubleSpinBox()
        self.pad_top.setRange(0.5, 1.0)
        self.pad_top.setSingleStep(0.01)
        self.pad_top.setDecimals(2)
        self.pad_top.setValue(self.settings.get("pad_top", 0.95))
        margins_layout.addWidget(self.pad_top, 1, 1)

        # Bottom
        margins_layout.addWidget(QLabel("Bottom:"), 1, 2)
        self.pad_bottom = QDoubleSpinBox()
        self.pad_bottom.setRange(0.0, 0.5)
        self.pad_bottom.setSingleStep(0.01)
        self.pad_bottom.setDecimals(2)
        self.pad_bottom.setValue(self.settings.get("pad_bottom", 0.05))
        margins_layout.addWidget(self.pad_bottom, 1, 3)

        # Wspace (width space between subplots)
        margins_layout.addWidget(QLabel("Wspace:"), 2, 0)
        self.pad_wspace = QDoubleSpinBox()
        self.pad_wspace.setRange(0.0, 0.5)
        self.pad_wspace.setSingleStep(0.01)
        self.pad_wspace.setDecimals(2)
        self.pad_wspace.setValue(self.settings.get("pad_wspace", 0.2))
        self.pad_wspace.setToolTip("Width space between subplots")
        margins_layout.addWidget(self.pad_wspace, 2, 1)

        # Hspace (height space between subplots)
        margins_layout.addWidget(QLabel("Hspace:"), 2, 2)
        self.pad_hspace = QDoubleSpinBox()
        self.pad_hspace.setRange(0.0, 0.5)
        self.pad_hspace.setSingleStep(0.01)
        self.pad_hspace.setDecimals(2)
        self.pad_hspace.setValue(self.settings.get("pad_hspace", 0.2))
        self.pad_hspace.setToolTip("Height space between subplots")
        margins_layout.addWidget(self.pad_hspace, 2, 3)

        padding_layout.addWidget(margins_group)

        # Tight Layout Option
        tight_group = QGroupBox("Layout Options")
        tight_layout_grid = QGridLayout(tight_group)

        self.use_tight_layout = QCheckBox("Use Tight Layout")
        self.use_tight_layout.setChecked(self.settings.get("use_tight_layout", False))
        self.use_tight_layout.setToolTip("Automatically adjust margins for best fit")
        tight_layout_grid.addWidget(self.use_tight_layout, 0, 0)

        padding_layout.addWidget(tight_group)
        padding_layout.addStretch()

        tab_widget.addTab(padding_tab, "Padding")

        outer_layout.addWidget(tab_widget)

        # Bottom buttons row
        buttons_layout = QHBoxLayout()
        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.clicked.connect(self.reset_to_defaults)
        buttons_layout.addWidget(reset_btn)
        buttons_layout.addStretch()

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        buttons_layout.addWidget(button_box)

        outer_layout.addLayout(buttons_layout)

    def _update_color_preview(self, label, color):
        """Update the color preview label."""
        if color == "auto" or color == "transparent":
            label.setStyleSheet("background-color: #888888; border: 1px solid #555555;")
            label.setText("A" if color == "auto" else "T")
            label.setAlignment(Qt.AlignCenter)
        else:
            label.setStyleSheet(
                f"background-color: {color}; border: 1px solid #555555;"
            )
            label.setText("")

    def _pick_plot_bg_color(self):
        """Open color picker for plot background."""
        from PyQt5.QtWidgets import QColorDialog, QPushButton
        from PyQt5.QtGui import QColor

        initial = (
            QColor(self.plot_bg_color)
            if self.plot_bg_color not in ("auto", "transparent")
            else QColor("#ffffff")
        )
        dialog = QColorDialog(initial, self)
        dialog.setWindowTitle("Select Plot Background Color")
        dialog.setOption(QColorDialog.DontUseNativeDialog, True)

        # Hide the "Pick Screen Color" button
        for btn in dialog.findChildren(QPushButton):
            if "pick" in btn.text().lower() or "screen" in btn.text().lower():
                btn.hide()

        if dialog.exec_() == QColorDialog.Accepted:
            self.plot_bg_color = dialog.selectedColor().name()
            self._update_color_preview(self.plot_bg_preview, self.plot_bg_color)

    def _pick_figure_bg_color(self):
        """Open color picker for figure background."""
        from PyQt5.QtWidgets import QColorDialog, QPushButton
        from PyQt5.QtGui import QColor

        initial = (
            QColor(self.figure_bg_color)
            if self.figure_bg_color not in ("auto", "transparent")
            else QColor("#ffffff")
        )
        dialog = QColorDialog(initial, self)
        dialog.setWindowTitle("Select Figure Background Color")
        dialog.setOption(QColorDialog.DontUseNativeDialog, True)

        # Hide the "Pick Screen Color" button
        for btn in dialog.findChildren(QPushButton):
            if "pick" in btn.text().lower() or "screen" in btn.text().lower():
                btn.hide()

        if dialog.exec_() == QColorDialog.Accepted:
            self.figure_bg_color = dialog.selectedColor().name()
            self._update_color_preview(self.figure_bg_preview, self.figure_bg_color)

    def _set_plot_bg_auto(self):
        """Set plot background to auto."""
        self.plot_bg_color = "auto"
        self._update_color_preview(self.plot_bg_preview, "auto")

    def _set_figure_bg_auto(self):
        """Set figure background to auto."""
        self.figure_bg_color = "auto"
        self._update_color_preview(self.figure_bg_preview, "auto")

    def _pick_text_color(self):
        """Open color picker for text color."""
        from PyQt5.QtWidgets import QColorDialog, QPushButton
        from PyQt5.QtGui import QColor

        initial = (
            QColor(self.text_color)
            if self.text_color not in ("auto", "transparent")
            else QColor("#ffffff")
        )
        dialog = QColorDialog(initial, self)
        dialog.setWindowTitle("Select Text Color")
        dialog.setOption(QColorDialog.DontUseNativeDialog, True)

        # Hide the "Pick Screen Color" button
        for btn in dialog.findChildren(QPushButton):
            if "pick" in btn.text().lower() or "screen" in btn.text().lower():
                btn.hide()

        if dialog.exec_() == QColorDialog.Accepted:
            self.text_color = dialog.selectedColor().name()
            self._update_color_preview(self.text_color_preview, self.text_color)

    def _set_text_color_auto(self):
        """Set text color to auto."""
        self.text_color = "auto"
        self._update_color_preview(self.text_color_preview, "auto")

    def _pick_tick_color(self):
        """Open color picker for tick color."""
        from PyQt5.QtWidgets import QColorDialog, QPushButton
        from PyQt5.QtGui import QColor

        initial = (
            QColor(self.tick_color)
            if self.tick_color not in ("auto", "transparent")
            else QColor("#ffffff")
        )
        dialog = QColorDialog(initial, self)
        dialog.setWindowTitle("Select Tick Color")
        dialog.setOption(QColorDialog.DontUseNativeDialog, True)

        # Hide the "Pick Screen Color" button
        for btn in dialog.findChildren(QPushButton):
            if "pick" in btn.text().lower() or "screen" in btn.text().lower():
                btn.hide()

        if dialog.exec_() == QColorDialog.Accepted:
            self.tick_color = dialog.selectedColor().name()
            self._update_color_preview(self.tick_color_preview, self.tick_color)

    def _set_tick_color_auto(self):
        """Set tick color to auto."""
        self.tick_color = "auto"
        self._update_color_preview(self.tick_color_preview, "auto")

    def _pick_border_color(self):
        """Open color picker for border color."""
        from PyQt5.QtWidgets import QColorDialog, QPushButton
        from PyQt5.QtGui import QColor

        initial = (
            QColor(self.border_color)
            if self.border_color not in ("auto", "transparent")
            else QColor("#ffffff")
        )
        dialog = QColorDialog(initial, self)
        dialog.setWindowTitle("Select Border Color")
        dialog.setOption(QColorDialog.DontUseNativeDialog, True)

        # Hide the "Pick Screen Color" button
        for btn in dialog.findChildren(QPushButton):
            if "pick" in btn.text().lower() or "screen" in btn.text().lower():
                btn.hide()

        if dialog.exec_() == QColorDialog.Accepted:
            self.border_color = dialog.selectedColor().name()
            self._update_color_preview(self.border_color_preview, self.border_color)

    def _set_border_color_auto(self):
        """Set border color to auto."""
        self.border_color = "auto"
        self._update_color_preview(self.border_color_preview, "auto")

    def _scale_fonts_up(self):
        """Increase all font sizes by 1."""
        self.axis_label_size.setValue(min(self.axis_label_size.value() + 1, 28))
        self.axis_tick_size.setValue(min(self.axis_tick_size.value() + 1, 24))
        self.title_size.setValue(min(self.title_size.value() + 1, 32))
        self.colorbar_label_size.setValue(min(self.colorbar_label_size.value() + 1, 24))
        self.colorbar_tick_size.setValue(min(self.colorbar_tick_size.value() + 1, 20))

    def _scale_fonts_down(self):
        """Decrease all font sizes by 1."""
        self.axis_label_size.setValue(max(self.axis_label_size.value() - 1, 6))
        self.axis_tick_size.setValue(max(self.axis_tick_size.value() - 1, 6))
        self.title_size.setValue(max(self.title_size.value() - 1, 8))
        self.colorbar_label_size.setValue(max(self.colorbar_label_size.value() - 1, 6))
        self.colorbar_tick_size.setValue(max(self.colorbar_tick_size.value() - 1, 6))

    def reset_to_defaults(self):
        """Reset all settings to defaults."""
        self.xlabel_edit.clear()
        self.ylabel_edit.clear()
        self.title_edit.clear()
        self.colorbar_label_edit.clear()
        self.axis_label_size.setValue(12)
        self.axis_tick_size.setValue(10)
        self.title_size.setValue(12)
        self.colorbar_label_size.setValue(10)
        self.colorbar_tick_size.setValue(10)
        self.plot_bg_color = "auto"
        self.figure_bg_color = "auto"
        self.text_color = "auto"
        self.tick_color = "auto"
        self.border_color = "auto"
        self._update_color_preview(self.plot_bg_preview, "auto")
        self._update_color_preview(self.figure_bg_preview, "auto")
        self._update_color_preview(self.text_color_preview, "auto")
        self._update_color_preview(self.tick_color_preview, "auto")
        self._update_color_preview(self.border_color_preview, "auto")
        self.tick_direction.setCurrentText("out")
        self.tick_length.setValue(4)
        self.tick_width.setValue(1.0)
        self.border_width.setValue(1.0)
        # Padding defaults
        self.pad_left.setValue(0.135)
        self.pad_right.setValue(1.0)
        self.pad_top.setValue(0.95)
        self.pad_bottom.setValue(0.05)
        self.pad_wspace.setValue(0.2)
        self.pad_hspace.setValue(0.2)
        self.use_tight_layout.setChecked(False)

    def get_settings(self):
        """Return the current settings as a dictionary."""
        return {
            "xlabel": self.xlabel_edit.text(),
            "ylabel": self.ylabel_edit.text(),
            "title": self.title_edit.text(),
            "colorbar_label": self.colorbar_label_edit.text(),
            "axis_label_fontsize": self.axis_label_size.value(),
            "axis_tick_fontsize": self.axis_tick_size.value(),
            "title_fontsize": self.title_size.value(),
            "colorbar_label_fontsize": self.colorbar_label_size.value(),
            "colorbar_tick_fontsize": self.colorbar_tick_size.value(),
            "plot_bg_color": self.plot_bg_color,
            "figure_bg_color": self.figure_bg_color,
            "text_color": self.text_color,
            "tick_color": self.tick_color,
            "border_color": self.border_color,
            "border_width": self.border_width.value(),
            "tick_direction": self.tick_direction.currentText(),
            "tick_length": self.tick_length.value(),
            "tick_width": self.tick_width.value(),
            # Padding settings
            "pad_left": self.pad_left.value(),
            "pad_right": self.pad_right.value(),
            "pad_top": self.pad_top.value(),
            "pad_bottom": self.pad_bottom.value(),
            "pad_wspace": self.pad_wspace.value(),
            "pad_hspace": self.pad_hspace.value(),
            "use_tight_layout": self.use_tight_layout.isChecked(),
        }



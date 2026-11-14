from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QComboBox,
    QCheckBox,
    QWidget,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon, QPixmap

from ..ui.widgets import button_bar, dialog_layout, segmented
from ..ui.styles import SPACE_M, set_hand_cursor, theme_manager, themed
from .form import field_label, form_block, swatch_icon

SWATCH_SIZE = 14
_CHOOSE = "choose"  # the colour drop-downs' Choose… item
# The longest row name of all three tabs, so their fields line up
LONGEST = "Figure background"


class PlotCustomizationDialog(QDialog):
    """Dialog for customizing plot appearance (labels, fonts, colors)."""

    def __init__(self, parent=None, settings=None, tight_layout_default=False):
        super().__init__(parent)
        self._tight_layout_default = tight_layout_default  # (for Reset)
        self.setWindowTitle("Plot style")
        self.setMaximumHeight(1280)
        self.settings = settings.copy() if settings else {}
        self._color_combos = {}  # the colour rows' drop-downs by setting
        self.setup_ui()
        themed(self, self._paint_swatches)
        set_hand_cursor(self)

    def setup_ui(self):
        from PyQt5.QtWidgets import QTabWidget

        outer_layout = dialog_layout(self)

        # Create tab widget for organized sections
        tab_widget = QTabWidget()

        # ===== TAB 1: TEXT & LABELS =====
        text_tab = QWidget()
        text_layout = self._tab_layout(text_tab)

        # Labels Section
        labels_layout = form_block(text_layout, "Labels", pairs=2, longest=LONGEST)

        labels_layout.addWidget(field_label("X axis"), 0, 0)
        self.xlabel_edit = QLineEdit(self.settings.get("xlabel", ""))
        self.xlabel_edit.setPlaceholderText("Auto")
        labels_layout.addWidget(self.xlabel_edit, 0, 1)

        labels_layout.addWidget(field_label("Y axis"), 0, 2)
        self.ylabel_edit = QLineEdit(self.settings.get("ylabel", ""))
        self.ylabel_edit.setPlaceholderText("Auto")
        labels_layout.addWidget(self.ylabel_edit, 0, 3)

        labels_layout.addWidget(field_label("Title"), 1, 0)
        self.title_edit = QLineEdit(self.settings.get("title", ""))
        self.title_edit.setPlaceholderText("Auto")
        labels_layout.addWidget(self.title_edit, 1, 1)

        labels_layout.addWidget(field_label("Colorbar"), 1, 2)
        self.colorbar_label_edit = QLineEdit(self.settings.get("colorbar_label", ""))
        self.colorbar_label_edit.setPlaceholderText("e.g., Jy/beam")
        labels_layout.addWidget(self.colorbar_label_edit, 1, 3)

        # Font Sizes Section (compact grid)
        fonts_layout = form_block(text_layout, "Font sizes", pairs=2, longest=LONGEST)

        fonts_layout.addWidget(field_label("Axis labels"), 0, 0)
        self.axis_label_size = QSpinBox()
        self.axis_label_size.setRange(1, 50)
        self.axis_label_size.setValue(self.settings.get("axis_label_fontsize", 12))
        fonts_layout.addWidget(self.axis_label_size, 0, 1)

        fonts_layout.addWidget(field_label("Axis ticks"), 0, 2)
        self.axis_tick_size = QSpinBox()
        self.axis_tick_size.setRange(1, 50)
        self.axis_tick_size.setValue(self.settings.get("axis_tick_fontsize", 10))
        fonts_layout.addWidget(self.axis_tick_size, 0, 3)

        fonts_layout.addWidget(field_label("Title"), 1, 0)
        self.title_size = QSpinBox()
        self.title_size.setRange(1, 50)
        self.title_size.setValue(self.settings.get("title_fontsize", 12))
        fonts_layout.addWidget(self.title_size, 1, 1)

        fonts_layout.addWidget(field_label("Colorbar"), 1, 2)
        self.colorbar_label_size = QSpinBox()
        self.colorbar_label_size.setRange(1, 50)
        self.colorbar_label_size.setValue(
            self.settings.get("colorbar_label_fontsize", 10)
        )
        fonts_layout.addWidget(self.colorbar_label_size, 1, 3)

        fonts_layout.addWidget(field_label("Colorbar ticks"), 2, 0)
        self.colorbar_tick_size = QSpinBox()
        self.colorbar_tick_size.setRange(1, 50)
        self.colorbar_tick_size.setValue(
            self.settings.get("colorbar_tick_fontsize", 10)
        )
        fonts_layout.addWidget(self.colorbar_tick_size, 2, 1)

        # Scale buttons
        fonts_layout.addWidget(field_label("Scale all"), 2, 2)
        scale_down_btn = QPushButton("\u2212")
        scale_down_btn.setToolTip("All font sizes one point smaller")
        scale_down_btn.clicked.connect(self._scale_fonts_down)
        scale_up_btn = QPushButton("+")
        scale_up_btn.setToolTip("All font sizes one point larger")
        scale_up_btn.clicked.connect(self._scale_fonts_up)
        scale_layout = QHBoxLayout()
        scale_layout.addWidget(segmented([scale_down_btn, scale_up_btn]))
        scale_layout.addStretch()
        fonts_layout.addLayout(scale_layout, 2, 3)

        text_layout.addStretch()

        tab_widget.addTab(text_tab, "Text")

        # ===== TAB 2: COLORS & STYLE =====
        style_tab = QWidget()
        style_layout = self._tab_layout(style_tab)

        # Colors Section (compact 2-column grid)
        colors_layout = form_block(style_layout, "Colours", pairs=2, longest=LONGEST)

        # Row 0: Plot BG, Figure BG
        colors_layout.addWidget(field_label("Plot background"), 0, 0)
        colors_layout.addWidget(self._color_choice("plot_bg_color", "Plot background colour"), 0, 1)
        colors_layout.addWidget(field_label("Figure background"), 0, 2)
        colors_layout.addWidget(self._color_choice("figure_bg_color", "Figure background colour"), 0, 3)

        # Row 1: Text, Tick
        colors_layout.addWidget(field_label("Text"), 1, 0)
        colors_layout.addWidget(self._color_choice("text_color", "Text colour"), 1, 1)
        colors_layout.addWidget(field_label("Ticks"), 1, 2)
        colors_layout.addWidget(self._color_choice("tick_color", "Tick colour"), 1, 3)

        # Row 2: Border color + width
        colors_layout.addWidget(field_label("Border"), 2, 0)
        colors_layout.addWidget(self._color_choice("border_color", "Border colour"), 2, 1)
        colors_layout.addWidget(field_label("Border width"), 2, 2)
        self.border_width = QDoubleSpinBox()
        self.border_width.setRange(0.5, 5.0)
        self.border_width.setSingleStep(0.5)
        self.border_width.setValue(self.settings.get("border_width", 1.0))
        colors_layout.addWidget(self.border_width, 2, 3)

        # Tick Marks Section (compact)
        ticks_layout = form_block(style_layout, "Tick marks", pairs=2, longest=LONGEST)

        ticks_layout.addWidget(field_label("Direction"), 0, 0)
        self.tick_direction = QComboBox()
        self.tick_direction.addItem("In", "in")
        self.tick_direction.addItem("Out", "out")
        self.tick_direction.setCurrentIndex(
            self.tick_direction.findData(self.settings.get("tick_direction", "out"))
        )
        ticks_layout.addWidget(self.tick_direction, 0, 1)

        ticks_layout.addWidget(field_label("Length"), 0, 2)
        self.tick_length = QSpinBox()
        self.tick_length.setRange(1, 20)
        self.tick_length.setValue(self.settings.get("tick_length", 4))
        ticks_layout.addWidget(self.tick_length, 0, 3)

        ticks_layout.addWidget(field_label("Width"), 1, 0)
        self.tick_width = QDoubleSpinBox()
        self.tick_width.setRange(0.5, 5.0)
        self.tick_width.setSingleStep(0.5)
        self.tick_width.setValue(self.settings.get("tick_width", 1.0))
        ticks_layout.addWidget(self.tick_width, 1, 1)

        style_layout.addStretch()

        tab_widget.addTab(style_tab, "Style")

        # ===== TAB 3: PADDING =====
        padding_tab = QWidget()
        padding_layout = self._tab_layout(padding_tab)

        # Subplot Margins Section
        margins_layout = form_block(padding_layout, "Margins (fraction of the figure)", pairs=2, longest=LONGEST)

        # Left
        margins_layout.addWidget(field_label("Left"), 0, 0)
        self.pad_left = QDoubleSpinBox()
        self.pad_left.setRange(0.0, 0.5)
        self.pad_left.setSingleStep(0.01)
        self.pad_left.setDecimals(3)
        self.pad_left.setValue(self.settings.get("pad_left", 0.135))
        margins_layout.addWidget(self.pad_left, 0, 1)

        # Right
        margins_layout.addWidget(field_label("Right"), 0, 2)
        self.pad_right = QDoubleSpinBox()
        self.pad_right.setRange(0.5, 1.0)
        self.pad_right.setSingleStep(0.01)
        self.pad_right.setDecimals(3)  # (all as Left, which needs three)
        self.pad_right.setValue(self.settings.get("pad_right", 1.0))
        margins_layout.addWidget(self.pad_right, 0, 3)

        # Top
        margins_layout.addWidget(field_label("Top"), 1, 0)
        self.pad_top = QDoubleSpinBox()
        self.pad_top.setRange(0.5, 1.0)
        self.pad_top.setSingleStep(0.01)
        self.pad_top.setDecimals(3)
        self.pad_top.setValue(self.settings.get("pad_top", 0.95))
        margins_layout.addWidget(self.pad_top, 1, 1)

        # Bottom
        margins_layout.addWidget(field_label("Bottom"), 1, 2)
        self.pad_bottom = QDoubleSpinBox()
        self.pad_bottom.setRange(0.0, 0.5)
        self.pad_bottom.setSingleStep(0.01)
        self.pad_bottom.setDecimals(3)
        self.pad_bottom.setValue(self.settings.get("pad_bottom", 0.08))
        margins_layout.addWidget(self.pad_bottom, 1, 3)

        # Wspace (width space between subplots)
        margins_layout.addWidget(field_label("Horizontal gap"), 2, 0)
        self.pad_wspace = QDoubleSpinBox()
        self.pad_wspace.setRange(0.0, 0.5)
        self.pad_wspace.setSingleStep(0.01)
        self.pad_wspace.setDecimals(3)
        self.pad_wspace.setValue(self.settings.get("pad_wspace", 0.2))
        self.pad_wspace.setToolTip("Width space between subplots, as a fraction of their width")
        margins_layout.addWidget(self.pad_wspace, 2, 1)

        # Hspace (height space between subplots)
        margins_layout.addWidget(field_label("Vertical gap"), 2, 2)
        self.pad_hspace = QDoubleSpinBox()
        self.pad_hspace.setRange(0.0, 0.5)
        self.pad_hspace.setSingleStep(0.01)
        self.pad_hspace.setDecimals(3)
        self.pad_hspace.setValue(self.settings.get("pad_hspace", 0.2))
        self.pad_hspace.setToolTip("Height space between subplots, as a fraction of their height")
        margins_layout.addWidget(self.pad_hspace, 2, 3)

        # Tight Layout Option
        self.use_tight_layout = QCheckBox("Fit margins automatically")
        self.use_tight_layout.setObjectName("Switch")
        self.use_tight_layout.setChecked(self.settings.get("use_tight_layout", False))
        self.use_tight_layout.setToolTip("Automatically adjust margins for best fit (tight layout)")
        padding_layout.addWidget(self.use_tight_layout)
        padding_layout.addStretch()

        tab_widget.addTab(padding_tab, "Padding")

        outer_layout.addWidget(tab_widget)

        box, _ = button_bar(
            primary=("Apply", self.accept),
            secondary=("Close", self.reject),
            extras=[("Reset to defaults", self.reset_to_defaults)],
        )
        outer_layout.addWidget(box)

    @staticmethod
    def _tab_layout(tab):
        """A tab's layout, its blocks clear of the tab bar."""
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, SPACE_M, 0, 0)
        layout.setSpacing(SPACE_M)
        return layout

    def _color_choice(self, attr, title):
        """A colour row's drop-down, as the other dialogs' colours: Auto,
        the colour chosen (if any), and Choose… for another, which opens
        the colour picker titled ``title``. What it shows is kept in
        ``self.<attr>``."""
        setattr(self, attr, self.settings.get(attr, "auto"))
        combo = QComboBox()
        self._color_combos[attr] = combo
        combo.activated.connect(lambda index: self._color_chosen(attr, title, index))
        self._fill_color_choice(attr)
        return combo

    def _fill_color_choice(self, attr):
        """``attr``'s drop-down with its colour current."""
        combo, color = self._color_combos[attr], getattr(self, attr)
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Auto", "auto")
        combo.setItemData(0, "Follow the theme", Qt.ToolTipRole)
        if color == "transparent":
            combo.addItem("Transparent", "transparent")
        elif color != "auto":
            combo.addItem(color, color)
        combo.addItem("Choose…", _CHOOSE)
        combo.setCurrentIndex(max(combo.findData(color), 0))
        combo.blockSignals(False)
        self._paint_swatches(theme_manager.palette)

    def _color_chosen(self, attr, title, index):
        combo = self._color_combos[attr]
        if combo.itemData(index) == _CHOOSE:
            color = self._pick_color(getattr(self, attr), title)
            if color:
                setattr(self, attr, color)
        else:
            setattr(self, attr, combo.itemData(index))
        self._fill_color_choice(attr)

    def _paint_swatches(self, palette):
        """Each colour's swatch; an empty one for auto and transparent,
        which have none to show, and a blank for Choose…, so the names
        line up."""
        blank = QPixmap(SWATCH_SIZE, SWATCH_SIZE)
        blank.fill(Qt.transparent)
        for combo in self._color_combos.values():
            for index in range(combo.count()):
                color = combo.itemData(index)
                if color == _CHOOSE:
                    combo.setItemIcon(index, QIcon(blank))
                    continue
                fill = palette["surface"] if color in ("auto", "transparent") else color
                combo.setItemIcon(index, swatch_icon(fill, palette["text_faint"], SWATCH_SIZE))

    def _pick_color(self, color, title):
        """The colour chosen starting from ``color``, or None."""
        from PyQt5.QtWidgets import QColorDialog
        from PyQt5.QtGui import QColor

        initial = (
            QColor(color)
            if color not in ("auto", "transparent")
            else QColor(theme_manager.palette["plot_bg"])
        )
        dialog = QColorDialog(initial, self)
        dialog.setWindowTitle(title)
        dialog.setOption(QColorDialog.DontUseNativeDialog, True)

        # Hide the "Pick Screen Color" button
        for btn in dialog.findChildren(QPushButton):
            if "pick" in btn.text().lower() or "screen" in btn.text().lower():
                btn.hide()

        if dialog.exec_() == QColorDialog.Accepted:
            return dialog.selectedColor().name()
        return None

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
        for attr in self._color_combos:
            setattr(self, attr, "auto")
            self._fill_color_choice(attr)
        self.tick_direction.setCurrentIndex(self.tick_direction.findData("out"))
        self.tick_length.setValue(4)
        self.tick_width.setValue(1.0)
        self.border_width.setValue(1.0)
        # Padding defaults
        self.pad_left.setValue(0.135)
        self.pad_right.setValue(1.0)
        self.pad_top.setValue(0.95)
        self.pad_bottom.setValue(0.08)
        self.pad_wspace.setValue(0.2)
        self.pad_hspace.setValue(0.2)
        self.use_tight_layout.setChecked(self._tight_layout_default)

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
            "tick_direction": self.tick_direction.currentData(),
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



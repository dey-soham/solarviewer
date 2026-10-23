"""
The image tab's controls around the canvas: the top bar (file, Stokes,
threshold, colormap, stretch), the inspector at the right (the Display
and Statistics tabs, the cursor readout pinned at its foot, Reload and
Helioviewer under that) and the empty canvas shown before an image.
"""

import logging
import matplotlib.pyplot as plt
from PyQt5.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QFrame,
    QCheckBox,
    QGridLayout,
    QHeaderView,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QButtonGroup,
)
from PyQt5.QtCore import Qt, QSize, QTimer
from PyQt5.QtGui import QIcon
from ..ui.cursor_readout import CursorReadout
from ..ui.empty_canvas import EmptyCanvas
from ..ui.geometry import fit_width_to_text
from ..ui.histogram import LevelsHistogram
from ..ui.searchable_combobox import ColormapSelector
from ..ui.icons import get_resource_path, themed_icon
from ..ui.stokes_picker import StokesPicker
from ..ui.styles import set_tone
from ..ui.widgets import kicker, kicker_row, link_button, segmented, toggle_row, vrule

logger = logging.getLogger(__name__)

# The display-range presets: button text, apply_auto_range mode, tooltip
RANGE_PRESETS = (
    ("Auto", "minmax", "The image's minimum to its maximum (F5)"),
    ("1–99%", "p1_99", "The 1st to the 99th percentile (F6)"),
    ("Med±3σ", "median_rms", "The median, three times the RMS either side (F7)"),
)


def _fit_rows(table):
    """Make ``table`` exactly as tall as its rows: no blank rows below them
    in a tall window, no scrolling in a short one."""
    row_height = table.fontMetrics().height() + 8
    for row in range(table.rowCount()):
        table.setRowHeight(row, row_height)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    table.horizontalHeader().setVisible(False)
    table.setFixedHeight(row_height * table.rowCount() + 2 * table.frameWidth())
    table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)


def _key_value_table(rows, tips=None):
    """A table of label / value rows in the KeyValue role: no frame, no
    header, the values right-aligned, as tall as its rows."""
    table = QTableWidget(len(rows), 2)
    table.setObjectName("KeyValue")
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setSelectionMode(QTableWidget.NoSelection)
    table.setFocusPolicy(Qt.NoFocus)
    table.setShowGrid(False)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    for row, label in enumerate(rows):
        item = QTableWidgetItem(label)
        if tips and label in tips:
            item.setToolTip(tips[label])
        table.setItem(row, 0, item)
        table.setItem(row, 1, QTableWidgetItem("−"))
        table.item(row, 1).setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    _fit_rows(table)
    return table


def _scroll(widget):
    """``widget`` in a scroll area, scrolling vertically when the window
    is too short for all of it."""
    area = QScrollArea()
    area.setWidget(widget)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    return area


def _block(title, aside=None):
    """A block of the inspector: a kicker over a layout to fill. Returns
    (widget, layout, aside label or None)."""
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    if aside is None:
        layout.addWidget(kicker(title))
        label = None
    else:
        row, label = kicker_row(title, aside)
        layout.addWidget(row)
    return widget, layout, label


def _settings_button(tip, slot):
    button = QPushButton()
    button.setObjectName("IconOnlyNBGButton")
    button.setIcon(QIcon(themed_icon("settings.png")))
    button.setIconSize(QSize(14, 14))
    button.setToolTip(tip)
    button.clicked.connect(slot)
    return button


class PanelsMixin:
    """Builds the tab's controls around the canvas.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``_apply_gamma_change``, ``_on_threshold_edited``,
    ``_show_roi_histogram``, ``_toggle_tb_mode``, ``apply_auto_range``,
    ``current_image_data``, ``imagename``, ``invalidate_contours``,
    ``launch_helioprojective_viewer``, ``on_checkbox_changed``,
    ``on_stokes_changed``, ``on_stretch_changed``,
    ``on_visualization_changed``, ``open_helioviewer_with_time``,
    ``open_path``, ``reset_view``, ``select_file_or_directory``,
    ``set_solar_disk_center``, ``show_beam_settings``,
    ``show_contour_settings``, ``show_grid_settings``,
    ``show_noaa_events_for_current_image``,
    ``show_plot_customization_dialog``, ``show_rms_box_dialog``,
    ``update_display``, ``update_gamma_slider``, ``update_gamma_value``
    """

    # ------------------------------------------------------------ top bar
    def create_top_bar(self, parent_layout):
        """The bar above the image: the file, Stokes, the threshold, the
        colormap and the stretch."""
        bar = QFrame()
        bar.setObjectName("TopBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 7, 14, 7)
        layout.setSpacing(14)

        # The file: typed or pasted and entered, or chosen with the icon
        self.dir_entry = QLineEdit()
        self.dir_entry.setPlaceholderText("FITS file or CASA image")
        self.dir_entry.setToolTip(
            "The image shown. Type or paste a path and press Enter, or click "
            "the icon to choose one (Ctrl+O)"
        )
        self.dir_entry.setClearButtonEnabled(False)
        self.dir_entry.setMinimumWidth(self.dir_entry.fontMetrics().averageCharWidth() * 44)
        self.dir_entry.returnPressed.connect(self._open_entered_path)
        self.browse_action = self.dir_entry.addAction(
            QIcon(themed_icon("browse.png")), QLineEdit.LeadingPosition
        )
        self.browse_action.setToolTip("Open a FITS file or CASA image (Ctrl+O)")
        self.browse_action.triggered.connect(self.select_file_or_directory)
        layout.addWidget(self.dir_entry, 1)
        layout.addWidget(vrule())

        # Stokes
        stokes_label = QLabel("Stokes")
        stokes_label.setObjectName("Hint")
        self.stokes_picker = StokesPicker()
        self.stokes_combo = self.stokes_picker.combo
        self.stokes_combo.currentTextChanged.connect(self.on_stokes_changed)
        layout.addWidget(stokes_label)
        layout.addWidget(self.stokes_picker)

        # Threshold (sigma)
        thresh_label = QLabel("Threshold")
        thresh_label.setObjectName("Hint")
        threshold_tip = (
            "Threshold, in units of the RMS (σ) measured in the RMS box.\n"
            "Lfrac, Vfrac, Q/I, U/I and U/V: pixels whose numerator is below "
            "threshold × RMS are set to 0.\n"
            "PANG: pixels whose polarized intensity is below it are left blank.\n"
            "I, Q, U, V and L are shown as they are."
        )
        thresh_label.setToolTip(threshold_tip)
        self.threshold_entry = QLineEdit("10")
        fit_width_to_text(self.threshold_entry, "0000")
        self.threshold_entry.setToolTip(threshold_tip)
        self.threshold_entry.editingFinished.connect(self._on_threshold_edited)
        sigma = QLabel("σ")
        sigma.setObjectName("Hint")
        layout.addWidget(thresh_label)
        layout.addWidget(self.threshold_entry)
        layout.addWidget(sigma)
        layout.addWidget(vrule())

        # Colormap and stretch
        radio_colormaps = [
            "viridis",
            "plasma",
            "inferno",
            "magma",
            "gist_heat",
            "hot",
            "CMRmap",
            "gnuplot2",
            "jet",
            "twilight",
        ]
        all_colormaps = sorted(plt.colormaps())
        self.cmap_combo = ColormapSelector(
            preferred_items=radio_colormaps, all_items=all_colormaps
        )
        self.cmap_combo.setCurrentText("viridis")
        self.cmap_combo.setToolTip("Colormap")
        self.cmap_combo.colormapSelected.connect(self.update_display)
        layout.addWidget(self.cmap_combo)

        self.stretch_combo = QComboBox()
        self.stretch_combo.addItems(
            ["linear", "sqrt", "log", "arcsinh", "power", "zscale", "histeq"]
        )
        self.stretch_combo.setCurrentText("power")
        self.stretch_combo.setToolTip("Stretch")
        self.stretch_combo.currentIndexChanged.connect(self.on_stretch_changed)
        stretch_tooltips = {
            0: "Linear stretch - no transformation",
            1: "Square root stretch - enhances faint features",
            2: "Logarithmic stretch - enhances very faint features",
            3: "Arcsinh stretch - similar to log but handles negative values",
            4: "Power law stretch - adjustable using gamma",
            5: "ZScale stretch - automatic contrast based on image statistics",
            6: "Histogram equalization - enhances contrast by redistributing intensities",
        }
        for idx, tooltip in stretch_tooltips.items():
            self.stretch_combo.setItemData(idx, tooltip, Qt.ToolTipRole)
        layout.addWidget(self.stretch_combo)
        layout.addStretch(2)

        parent_layout.addWidget(bar)

    def _open_entered_path(self):
        """Open the path typed or pasted into the file field."""
        import os

        path = os.path.expanduser(self.dir_entry.text().strip().strip("'\""))
        if path and os.path.exists(path):
            self.open_path(path)
        elif path:
            self.show_status_message(f"No such file or image: {path}")

    # ---------------------------------------------------------- inspector
    def create_inspector(self):
        """The column at the right: the Display and Statistics tabs, the
        cursor readout pinned under them, Reload and Helioviewer at the
        foot. Returns the widget."""
        inspector = QFrame()
        inspector.setObjectName("Inspector")
        layout = QVBoxLayout(inspector)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.inspector_tabs = QTabWidget()
        self.inspector_tabs.setObjectName("InspectorTabs")
        self.inspector_tabs.tabBar().setFocusPolicy(Qt.NoFocus)
        self.inspector_tabs.addTab(_scroll(self._display_page()), "Display")
        self.inspector_tabs.addTab(_scroll(self._statistics_page()), "Statistics")
        layout.addWidget(self.inspector_tabs, 1)

        self.cursor_readout = CursorReadout()
        self._create_conversion_buttons()
        layout.addWidget(self.cursor_readout)

        footer = QFrame()
        footer.setObjectName("CanvasBar")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(10, 8, 14, 8)
        footer_layout.setSpacing(6)
        # Display changes apply as they are made; this reads the image
        # again, which is rarely needed
        self.plot_button = link_button("Reload image", self.on_visualization_changed)
        self.plot_button.setToolTip("Read the image again from disk")
        footer_layout.addWidget(self.plot_button)
        footer_layout.addStretch()
        self.helioviewer_btn = QPushButton("Helioviewer")
        self.helioviewer_btn.setToolTip(
            "Browse Helioviewer images around current observation time"
        )
        self.helioviewer_btn.setEnabled(False)  # until an image is loaded
        self.helioviewer_btn.clicked.connect(self.open_helioviewer_with_time)
        footer_layout.addWidget(self.helioviewer_btn)
        layout.addWidget(footer)

        return inspector

    def _create_conversion_buttons(self):
        """The brightness-temperature and helioprojective conversions,
        beside the readout lines they change (their text is set by the
        loaders and the conversions themselves: TB / FLUX, HPC / RA/DEC)."""
        self.tb_btn = QPushButton("TB")
        self.tb_btn.setToolTip(
            "Convert to Brightness Temperature (K) - requires Jy/beam units"
        )
        self.tb_btn.setCheckable(True)
        self.tb_btn.setEnabled(False)  # enabled when the units are Jy/beam
        self.tb_btn.clicked.connect(self._toggle_tb_mode)
        self.cursor_readout.set_value_button(self.tb_btn)

        self.hpc_btn = QPushButton("HPC")
        self.hpc_btn.setToolTip("Convert to Helioprojective view")
        self.hpc_btn.clicked.connect(self.launch_helioprojective_viewer)
        self.cursor_readout.set_world_button(self.hpc_btn)

    def _display_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 16, 14, 16)
        layout.setSpacing(18)
        layout.addWidget(self._overlays_block())
        layout.addWidget(self._intensity_block())
        layout.addWidget(self._loading_block())
        layout.addStretch()
        return page

    def _overlays_block(self):
        block, layout, _ = _block("Overlays")
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)

        self.show_beam_checkbox = QCheckBox("")
        self.show_beam_checkbox.setAccessibleName("Beam")
        self.show_beam_label = QLabel("Beam")
        self.show_beam_checkbox.setChecked(True)
        self.show_beam_checkbox.setObjectName("Switch")
        self.show_beam_checkbox.stateChanged.connect(self.on_checkbox_changed)
        self.beam_settings_button = _settings_button("Beam Settings", self.show_beam_settings)

        self.show_grid_checkbox = QCheckBox("Grid")
        self.show_grid_checkbox.setChecked(False)
        self.show_grid_checkbox.setObjectName("Switch")
        self.show_grid_checkbox.stateChanged.connect(self.on_checkbox_changed)
        self.grid_settings_button = _settings_button("Grid Settings", self.show_grid_settings)

        self.show_solar_disk_checkbox = QCheckBox("Solar disk")
        self.show_solar_disk_checkbox.setObjectName("Switch")
        self.show_solar_disk_checkbox.stateChanged.connect(self.on_checkbox_changed)
        self.solar_disk_center_button = _settings_button(
            "Customize Solar Disk", self.set_solar_disk_center
        )

        self.show_contours_checkbox = QCheckBox("Contours")
        self.show_contours_checkbox.setChecked(False)
        self.show_contours_checkbox.setObjectName("Switch")
        self.show_contours_checkbox.stateChanged.connect(self.on_checkbox_changed)
        self.contour_settings_button = _settings_button(
            "Contour Settings", self.show_contour_settings
        )

        grid.addWidget(
            toggle_row(self.show_beam_checkbox, self.beam_settings_button, self.show_beam_label),
            0, 0,
        )
        grid.addWidget(toggle_row(self.show_grid_checkbox, self.grid_settings_button), 0, 1)
        grid.addWidget(
            toggle_row(self.show_solar_disk_checkbox, self.solar_disk_center_button), 1, 0
        )
        grid.addWidget(
            toggle_row(self.show_contours_checkbox, self.contour_settings_button), 1, 1
        )
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        return block

    def _intensity_block(self):
        block, layout, self.range_preset_label = _block("Intensity range", "")

        # The histogram, the range draggable on it
        self.levels = LevelsHistogram()
        self.levels.dragStarted.connect(self._on_levels_grabbed)
        self.levels.rangeEdited.connect(self._on_levels_dragged)
        self.levels.rangeChanged.connect(self._on_levels_set)
        # The image follows a drag as a quick preview (rendering's
        # begin_range_preview), drawn at most every so often, not on
        # every mouse event
        self._levels_timer = QTimer(self)
        self._levels_timer.setSingleShot(True)
        self._levels_timer.setInterval(40)
        self._levels_timer.timeout.connect(self._draw_levels_preview)
        self._levels_range = None
        layout.addWidget(self.levels)

        # Min and Max, their names above them
        fields = QGridLayout()
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setHorizontalSpacing(8)
        fields.setVerticalSpacing(3)
        self.vmin_entry = QLineEdit("0.0")
        self.vmin_entry.editingFinished.connect(self._on_range_typed)
        self.vmax_entry = QLineEdit("1.0")
        self.vmax_entry.editingFinished.connect(self._on_range_typed)
        for column, (name, entry) in enumerate((("Min", self.vmin_entry), ("Max", self.vmax_entry))):
            label = QLabel(name)
            label.setObjectName("Meta")
            fields.addWidget(label, 0, column)
            fields.addWidget(entry, 1, column)
        layout.addLayout(fields)

        # Gamma
        gamma_layout = QHBoxLayout()
        gamma_layout.setSpacing(10)
        gamma_label = QLabel("Gamma")
        gamma_label.setObjectName("Hint")
        self.gamma_slider = QSlider(Qt.Horizontal)
        self.gamma_slider.setRange(1, 60)
        self.gamma_slider.setValue(20)
        self.gamma_slider.valueChanged.connect(self.update_gamma_value)
        self.gamma_slider.sliderReleased.connect(self._apply_gamma_change)
        self.gamma_entry = QLineEdit("1.0")
        self.gamma_entry.setObjectName("Mono")
        self.gamma_entry.setAlignment(Qt.AlignRight)
        fit_width_to_text(self.gamma_entry, "0.00")
        self.gamma_entry.editingFinished.connect(self.update_gamma_slider)
        gamma_layout.addWidget(gamma_label)
        gamma_layout.addWidget(self.gamma_slider, 1)
        gamma_layout.addWidget(self.gamma_entry)
        layout.addLayout(gamma_layout)

        # The presets, the one last applied outlined
        self._preset_group = QButtonGroup(self)
        self._preset_group.setExclusive(True)
        buttons = []
        for text, mode, tip in RANGE_PRESETS:
            button = QPushButton(text)
            button.setCheckable(True)
            button.setToolTip(tip)
            button.clicked.connect(lambda _=False, m=mode: self.apply_auto_range(m))
            self._preset_group.addButton(button)
            buttons.append(button)
        self.auto_minmax_button, self.auto_percentile_button, self.auto_median_button = buttons
        layout.addWidget(segmented(buttons, stretch=True))
        return block

    def _on_range_typed(self):
        """A range typed in is no preset's."""
        self.show_range_preset(None)
        self.update_display()

    def _on_levels_grabbed(self):
        self._levels_preview = self.begin_range_preview()

    def _on_levels_dragged(self, vmin, vmax):
        """The range dragged on the histogram: the fields follow at once,
        the image's preview shortly after."""
        self.show_range(vmin, vmax)
        self.show_range_preset(None)
        self._levels_range = (vmin, vmax)
        if not self._levels_timer.isActive():
            self._levels_timer.start()

    def _draw_levels_preview(self):
        if self._levels_range is None:
            return
        if getattr(self, "_levels_preview", False):
            self.preview_range(*self._levels_range)
        else:
            self.update_display()

    def _on_levels_set(self, vmin, vmax):
        """The marker let go: the full image takes the range now."""
        self._levels_timer.stop()
        self._levels_range = None
        self.end_range_preview()
        self.show_range(vmin, vmax)
        self.update_display()

    def show_range_preset(self, mode):
        """Mark the preset ``mode`` (an apply_auto_range mode, or None) as
        the one the range came from."""
        self._preset_group.setExclusive(False)
        for (text, preset_mode, _tip), button in zip(RANGE_PRESETS, self._preset_group.buttons()):
            button.setChecked(preset_mode == mode)
            if preset_mode == mode:
                self.range_preset_label.setText(text)
        self._preset_group.setExclusive(True)
        if mode is None:
            self.range_preset_label.setText("")

    def _loading_block(self):
        block, layout, _ = _block("Loading")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.downsample_toggle = QCheckBox("Fast load")
        self.downsample_toggle.setObjectName("Switch")
        self.downsample_toggle.setToolTip(
            "Enable fast preview mode for quick image browsing.\n"
            "Images are loaded at reduced resolution for faster display."
        )
        # Says "preview" while the image shown is the reduced one
        self.downsample_status = QLabel("")
        self.downsample_status.setObjectName("Hint")
        set_tone(self.downsample_status, "success")

        def update_fast_load_status(checked):
            self.downsample_status.setText("preview" if checked else "")
            # Contour data is reloaded at the new resolution
            self.invalidate_contours()
            if self.current_image_data is not None and self.imagename:
                self.on_visualization_changed()
                self.reset_view()

        self.downsample_toggle.stateChanged.connect(update_fast_load_status)
        row.addWidget(self.downsample_toggle)
        row.addWidget(self.downsample_status)
        row.addStretch()

        rms_tip = (
            "RMS box: where the noise (RMS) is measured, by default a band "
            "along the bottom of the image.\nThe RMS sets the threshold of the "
            "derived Stokes products, the image statistics (RMS, dynamic range) "
            "and contour levels in σ."
        )
        self.rms_settings_btn = link_button("RMS box…", self.show_rms_box_dialog)
        self.rms_settings_btn.setToolTip(rms_tip)
        self.rms_settings_btn.setAccessibleName("RMS box settings")
        row.addWidget(self.rms_settings_btn)
        layout.addLayout(row)

        style_row = QHBoxLayout()
        style_row.setSpacing(8)
        style_hint = QLabel("Title, labels, fonts, colours, margins")
        style_hint.setObjectName("Hint")
        style_row.addWidget(style_hint)
        style_row.addStretch()
        self.plot_style_btn = link_button("Plot style…", self.show_plot_customization_dialog)
        self.plot_style_btn.setToolTip("Customize Plot Appearance")
        style_row.addWidget(self.plot_style_btn)
        layout.addLayout(style_row)
        return block

    def _statistics_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 16, 14, 16)
        layout.setSpacing(18)

        # The region: its statistics appear with the first region drawn
        block, block_layout, _ = _block("Region")
        self.info_label = QLabel("No selection. Draw a rectangle or ellipse on the image.")
        self.info_label.setObjectName("Hint")
        self.info_label.setWordWrap(True)
        block_layout.addWidget(self.info_label)
        self.stats_table = _key_value_table(["Min", "Max", "Mean", "Std Dev", "Sum", "RMS"])
        self.stats_table.hide()
        block_layout.addWidget(self.stats_table)
        self.histogram_btn = QPushButton("Histogram")
        self.histogram_btn.setToolTip("Show pixel value histogram for current ROI")
        self.histogram_btn.clicked.connect(self._show_roi_histogram)
        self.histogram_btn.hide()
        row = QHBoxLayout()
        row.addWidget(self.histogram_btn)
        row.addStretch()
        block_layout.addLayout(row)
        layout.addWidget(block)

        # The whole image: its statistics appear with the image
        block, block_layout, _ = _block("Image")
        self.image_info_label = QLabel("Loads with the image")
        self.image_info_label.setObjectName("Hint")
        self.image_info_label.setWordWrap(True)
        block_layout.addWidget(self.image_info_label)
        # Short labels, so the values keep their room in a narrow panel
        self.image_stats_table = _key_value_table(
            ["Max", "Min", "RMS", "Box mean", "Pos. DR", "Neg. DR"],
            tips={
                "RMS": "RMS inside the RMS box",
                "Box mean": "Mean inside the RMS box",
                "Pos. DR": "Positive dynamic range: Max / RMS",
                "Neg. DR": "Negative dynamic range: Min / RMS",
            },
        )
        self.image_stats_table.hide()
        block_layout.addWidget(self.image_stats_table)
        self.noaa_events_btn = QPushButton("Activity")
        self.noaa_events_btn.setToolTip(
            "View Solar Activity (Events, Active Regions, Conditions, CMEs)"
        )
        self.noaa_events_btn.setEnabled(False)  # until an image is loaded
        self.noaa_events_btn.clicked.connect(self.show_noaa_events_for_current_image)
        row = QHBoxLayout()
        row.addWidget(self.noaa_events_btn)
        row.addStretch()
        block_layout.addLayout(row)
        layout.addWidget(block)
        layout.addStretch()
        return page

    # -------------------------------------------------------- empty canvas
    def create_empty_canvas(self):
        """What the canvas shows before an image: how to open one, and the
        recent files. Returns the widget."""
        self.empty_canvas = EmptyCanvas()
        self.empty_canvas.openRequested.connect(self.select_file_or_directory)
        self.empty_canvas.pathRequested.connect(self.open_path)
        self.empty_canvas.allRecentRequested.connect(self._show_recent_menu)
        return self.empty_canvas

    def _show_recent_menu(self):
        """File > Open Recent, at the pointer."""
        from PyQt5.QtGui import QCursor

        menu = getattr(self.window(), "recent_menu", None)
        if menu is not None:
            menu.exec_(QCursor.pos())

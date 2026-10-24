"""
The image tab's controls around the canvas: the top bar (file, Stokes,
threshold, colormap, stretch), the inspector at the right (a header with
the image's unit and frame and the observation-context launchers, the
Display and Statistics tabs, the cursor readout pinned at its foot) and
the empty canvas shown before an image.
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
    QButtonGroup,
    QMenu,
)
from PyQt5.QtCore import Qt, QSize, QTimer
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QGraphicsOpacityEffect
from ..ui.conversion_control import ConversionControl
from ..ui.cursor_readout import CursorReadout
from ..ui.empty_canvas import EmptyCanvas
from ..ui.geometry import fit_width_to_text
from ..ui.histogram import LevelsHistogram
from ..ui.searchable_combobox import ColormapSelector
from ..ui.icons import get_resource_path, launcher_icon, themed_icon
from ..ui.stokes_picker import StokesPicker
from ..ui.styles import set_tone
from ..ui.widgets import kicker, kicker_row, link_button, rule, segmented, toggle_row, vrule

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


def _clock(time):
    """``time`` ("2023-03-10T13:24:07.3") as a clock reading ("13:24")."""
    text = str(time)
    if "T" in text:
        text = text.split("T", 1)[1]
    elif " " in text:
        text = text.split(" ", 1)[1]
    return text[:5] if len(text) >= 5 and text[2] == ":" else str(time)


def _block(title, aside=None):
    """A block of the inspector: a kicker over a layout to fill. Returns
    (widget, layout, aside label or None)."""
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
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
    button.setIcon(QIcon(themed_icon("gear_off.svg")))
    button.setIconSize(QSize(15, 15))
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
    def create_top_bar(self):
        """The bar above the image: the file, Stokes, the threshold, the
        colormap and the stretch. Returns the widget."""
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
        return bar

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
        """This pane's parts of the inspector, for its page to stack (see
        core.compare.ImagePage): the header, the Display and Statistics
        pages, the cursor readout pinned at the foot."""
        self.inspector_header = self._inspector_header()
        self.display_page = _scroll(self._display_page())
        self.statistics_page = _scroll(self._statistics_page())
        self.cursor_readout = CursorReadout()
        self.inspector_tabs = None  # the page's Display/Statistics tabs
        self.show_image_context(None)

    def _inspector_header(self):
        """One row at the top of the inspector: what the image is in (its
        unit, its coordinate frame; the other segment converts it), and
        the two windows about its observation time. Faded while there is
        no image."""
        header = QFrame()
        header.setObjectName("InspectorHeader")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(14, 0, 10, 0)
        layout.setSpacing(8)

        # The loaders set the action these offer ("TB" / "FLUX", "HPC" /
        # "RA/DEC"), which is the other state than the one on
        self.tb_btn = ConversionControl(
            ("Jy/beam", "K"),
            {"TB": 0, "FLUX": 1},
            "Convert the image between flux density (Jy/beam) and brightness "
            "temperature (K). Needs beam information.",
            "Brightness temperature needs the image in Jy/beam or K, with "
            "beam information.",
        )
        self.tb_btn.setEnabled(False)  # enabled when the units are Jy/beam or K
        self.tb_btn.clicked.connect(self._toggle_tb_mode)
        layout.addWidget(self.tb_btn)

        self.hpc_btn = ConversionControl(
            ("RA/Dec", "Solar XY"),
            {"HPC": 0, "RA/DEC": 1},
            "Reproject the image between equatorial (RA/Dec) and helioprojective "
            "(Solar X/Y) coordinates.",
            "Reprojection needs an image with world coordinates.",
        )
        self.hpc_btn.setEnabled(False)  # enabled with an image (loading)
        self.hpc_btn.clicked.connect(self.launch_helioprojective_viewer)
        layout.addWidget(self.hpc_btn)
        layout.addStretch()

        # The observation's context, in windows of their own
        launchers = QHBoxLayout()
        launchers.setSpacing(4)
        self.noaa_events_btn = QPushButton()
        self.noaa_events_btn.setObjectName("LauncherButton")
        self.noaa_events_btn.setIcon(launcher_icon("activity.svg"))
        self.noaa_events_btn.setIconSize(QSize(15, 15))
        self.noaa_events_btn.setAccessibleName("Solar activity")
        self.noaa_events_btn.setEnabled(False)  # until an image is loaded
        self.noaa_events_btn.clicked.connect(self.show_noaa_events_for_current_image)
        self.helioviewer_btn = QPushButton()
        self.helioviewer_btn.setObjectName("LauncherButton")
        self.helioviewer_btn.setIcon(launcher_icon("sun.svg"))
        self.helioviewer_btn.setIconSize(QSize(15, 15))
        self.helioviewer_btn.setAccessibleName("Helioviewer")
        self.helioviewer_btn.setEnabled(False)
        self.helioviewer_btn.clicked.connect(self.open_helioviewer_with_time)
        launchers.addWidget(self.noaa_events_btn)
        launchers.addWidget(self.helioviewer_btn)
        layout.addLayout(launchers)

        self._header_opacity = QGraphicsOpacityEffect(header)
        header.setGraphicsEffect(self._header_opacity)
        return header

    def show_image_context(self, time):
        """What the inspector says about the image shown: the header lit
        (faded with none), the launchers' tooltips naming the observation
        time, the range's unit, the readout's frame name, Reload offered."""
        if not hasattr(self, "cursor_readout"):
            return  # (the inspector is still being built)
        loaded = self.current_image_data is not None
        self._header_opacity.setOpacity(1.0 if loaded else 0.45)
        if not loaded:
            self.tb_btn.clear()
            self.hpc_btn.clear()
        when = f" around {_clock(time)} UT" if time else ""
        self.noaa_events_btn.setToolTip(
            f"Solar activity{when} — NOAA events, active regions, CMEs"
        )
        self.helioviewer_btn.setToolTip(f"SDO/SOHO images{when}")
        self.range_unit_label.setText(
            (getattr(self, "_current_bunit", "") or "") if loaded else ""
        )
        self.cursor_readout.set_frame_name(self.hpc_btn.current_label() if loaded else "")
        self.plot_button.setEnabled(loaded)
        if not loaded:
            self.show_image_summary(None)
        self.observation_time = time if loaded else None
        if getattr(self, "page", None) is not None:
            self.page.pane_changed(self)

    def show_observation_time(self, time):
        self.show_image_context(time)

    def _display_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 16, 14, 16)
        layout.setSpacing(20)
        layout.addWidget(self._overlays_block())
        layout.addWidget(self._intensity_block())
        layout.addWidget(self._loading_block())
        layout.addWidget(self._image_block())
        layout.addStretch()
        return page

    def _image_block(self):
        """A summary of the image after Loading: its size and RMS box,
        three of its statistics, and the way to the rest."""
        block = QWidget()
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(rule())
        layout.addSpacing(6)
        row, self.image_summary_meta = kicker_row("Image", "")
        layout.addWidget(row)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(2)
        self.image_summary_values = {}
        for column, name in enumerate(("Max", "RMS", "Pos. DR")):
            label = QLabel(name)
            label.setObjectName("Meta")
            value = QLabel("—")
            value.setObjectName("Mono")
            grid.addWidget(label, 0, column)
            grid.addWidget(value, 1, column)
            self.image_summary_values[name] = value
        layout.addLayout(grid)
        self.image_summary_more = QLabel()
        self.image_summary_more.setObjectName("Meta")
        self.image_summary_more.setTextFormat(Qt.RichText)
        self.image_summary_more.linkActivated.connect(self._show_statistics_tab)
        self._set_summary_link()
        layout.addWidget(self.image_summary_more)
        return block

    def _show_statistics_tab(self, _link=None):
        if self.inspector_tabs is not None:
            self.inspector_tabs.setCurrentIndex(1)

    def _set_summary_link(self):
        from ..ui.styles import theme_manager

        self.image_summary_more.setText(
            "Full statistics in the "
            f'<a href="statistics" style="color:{theme_manager.palette["link"]};'
            'text-decoration:none">Statistics</a> tab'
        )

    def show_image_summary(self, summary):
        """``summary``: (height, width, rms box %, max, rms, positive DR),
        or None with no image."""
        if summary is None:
            self.image_summary_meta.setText("")
            for value in self.image_summary_values.values():
                value.setText("—")
            return
        h, w, box_percent, dmax, drms, pos_dr = summary
        self.image_summary_meta.setText(f"{w}×{h} px · RMS box {box_percent:.1f}%")
        for name, value in (("Max", dmax), ("RMS", drms), ("Pos. DR", pos_dr)):
            self.image_summary_values[name].setText(f"{value:.4g}")

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

        self._overlay_rows = [
            toggle_row(self.show_beam_checkbox, self.beam_settings_button, self.show_beam_label),
            toggle_row(self.show_grid_checkbox, self.grid_settings_button),
            toggle_row(self.show_solar_disk_checkbox, self.solar_disk_center_button),
            toggle_row(self.show_contours_checkbox, self.contour_settings_button),
        ]
        for index, row in enumerate(self._overlay_rows):
            grid.addWidget(row, index // 2, index % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        return block

    def _intensity_block(self):
        block, layout, self.range_unit_label = _block("Intensity range", "")

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
        for (_text, preset_mode, _tip), button in zip(RANGE_PRESETS, self._preset_group.buttons()):
            button.setChecked(preset_mode == mode)
        self._preset_group.setExclusive(True)

    def _loading_block(self):
        block, layout, _ = _block("Loading")
        layout.setSpacing(8)

        def link_row(left, link):
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(left)
            row.addStretch()
            row.addWidget(link)
            layout.addLayout(row)

        # Fast load, and where the noise is measured
        fast = QWidget()
        fast_layout = QHBoxLayout(fast)
        fast_layout.setContentsMargins(0, 0, 0, 0)
        fast_layout.setSpacing(8)
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
        fast_layout.addWidget(self.downsample_toggle)
        fast_layout.addWidget(self.downsample_status)
        self.rms_settings_btn = link_button("RMS box…", self.show_rms_box_dialog)
        self.rms_settings_btn.setToolTip(
            "RMS box: where the noise (RMS) is measured, by default a band "
            "along the bottom of the image.\nThe RMS sets the threshold of the "
            "derived Stokes products, the image statistics (RMS, dynamic range) "
            "and contour levels in σ."
        )
        self.rms_settings_btn.setAccessibleName("RMS box settings")
        link_row(fast, self.rms_settings_btn)

        # The plot's look
        style_hint = QLabel("Title, labels, fonts, colours, margins")
        style_hint.setObjectName("Hint")
        self.plot_style_btn = link_button("Plot style…", self.show_plot_customization_dialog)
        self.plot_style_btn.setToolTip("Customize Plot Appearance")
        link_row(style_hint, self.plot_style_btn)

        # Display changes apply as they are made; this reads the image
        # again, which is rarely needed
        reload_hint = QLabel("Re-read the file from disk")
        reload_hint.setObjectName("Hint")
        self.plot_button = link_button("Reload image", self.on_visualization_changed)
        self.plot_button.setToolTip("Read the image again from disk")
        link_row(reload_hint, self.plot_button)
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
        self.fit_gaussian_btn = QPushButton("Fit Gaussian")
        self.fit_gaussian_btn.setToolTip("Fit a 2D Gaussian to the selected region")
        self.fit_gaussian_btn.clicked.connect(self._fit_gaussian)
        self.fit_gaussian_btn.hide()
        # The region's exports (the Region menu's), under one button
        self.export_region_btn = QPushButton("Export")
        self.export_region_btn.setToolTip("Export the region: as a sub-image, or as a CASA region file")
        export_menu = QMenu(self.export_region_btn)
        export_menu.addAction("Sub-image (ROI)…", self._export_sub_image)
        export_menu.addAction("ROI as region file…", self._export_region)
        self.export_region_btn.setMenu(export_menu)
        self.export_region_btn.hide()
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(self.histogram_btn)
        row.addWidget(self.fit_gaussian_btn)
        row.addWidget(self.export_region_btn)
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

    def _export_sub_image(self):
        """Region > Export Sub-Image (ROI)."""
        window = self.window()
        if hasattr(window, "save_sub_image"):
            window.save_sub_image()

    def _export_region(self):
        """Region > Export ROI as Region."""
        window = self.window()
        if hasattr(window, "export_casa_region"):
            window.export_casa_region()

    def _fit_gaussian(self):
        """Fitting > Fit 2D Gaussian, on this tab's region."""
        window = self.window()
        if hasattr(window, "fit_2d_gaussian"):
            window.fit_2d_gaussian()

    def _show_recent_menu(self):
        """File > Open Recent, at the pointer."""
        from PyQt5.QtGui import QCursor

        menu = getattr(self.window(), "recent_menu", None)
        if menu is not None:
            menu.exec_(QCursor.pos())

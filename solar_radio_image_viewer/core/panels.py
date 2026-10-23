"""
The image tab's sidebar beside the image, in folding sections: Image (the
file, its navigation, Stokes, threshold), Levels (the histogram with the
range on it, colormap, stretch, gamma), Overlays, and Statistics (region
and image tables, side by side when the sidebar is wide). The cursor
readout is a label the window shows in its status bar.

The image keeps the tab's full height; the sidebar takes the width a
square image leaves over, so the one wide thing here, the histogram,
grows with it while the control rows keep their size.
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
    QCheckBox,
    QHeaderView,
    QSizePolicy,
)
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QIcon
from ..ui.geometry import fit_width_to_text
from ..ui.histogram import LevelsHistogram
from ..ui.searchable_combobox import ColormapSelector
from ..ui.icons import get_resource_path, themed_icon
from ..ui.styles import set_tone
from ..ui.widgets import FoldSection, ResponsiveRow
from ..ui.item_delegates import DisabledItemDelegate

logger = logging.getLogger(__name__)

# A row of controls is never wider than this: a wide sidebar leaves room
# beside it rather than stretching combos and fields
ROW_MAX_WIDTH = 520


def _fit_rows(table):
    """Make ``table`` exactly as tall as its rows: no blank rows below them
    in a tall window, no scrolling in a short one."""
    row_height = table.fontMetrics().height() + 8
    for row in range(table.rowCount()):
        table.setRowHeight(row, row_height)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    header = table.horizontalHeader()
    header.setFixedHeight(table.fontMetrics().height() + 12)
    table.setFixedHeight(
        header.height() + row_height * table.rowCount() + 2 * table.frameWidth()
    )
    table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)


def _metric_table(rows):
    """A two-column Metric / Value table with ``rows`` [(label, tooltip)],
    as tall as its rows."""
    table = QTableWidget(len(rows), 2)
    table.setHorizontalHeaderLabels(["Metric", "Value"])
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
    table.horizontalHeader().setSectionsMovable(False)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    for row, (label, tip) in enumerate(rows):
        item = QTableWidgetItem(label)
        if tip:
            item.setToolTip(tip)
        table.setItem(row, 0, item)
        table.setItem(row, 1, QTableWidgetItem("−"))
        table.item(row, 1).setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    _fit_rows(table)
    return table


def _gear(tip, slot):
    """A small settings button."""
    button = QPushButton()
    button.setObjectName("IconOnlyNBGButton")
    button.setIcon(QIcon(themed_icon("settings.png")))
    button.setIconSize(QSize(16, 16))
    button.setToolTip(tip)
    button.clicked.connect(slot)
    return button


def _switch(text, slot, checked=False):
    """An on/off switch."""
    box = QCheckBox(text)
    box.setObjectName("Switch")
    box.setChecked(checked)
    box.stateChanged.connect(slot)
    return box


def _nav_button(text, tip, slot, enabled=False):
    """A flat button of the file navigation row."""
    button = QPushButton(text)
    button.setObjectName("NavButton")
    button.setToolTip(tip)
    button.setEnabled(enabled)
    button.clicked.connect(slot)
    return button


def _row(*items, spacing=8):
    """A horizontal layout of widgets (an int is a stretch)."""
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for item in items:
        if isinstance(item, int):
            layout.addStretch(item)
        else:
            layout.addWidget(item)
    return layout


def _capped(layout):
    """``layout`` in a widget no wider than ROW_MAX_WIDTH, kept to the left."""
    box = QWidget()
    box.setLayout(layout)
    box.setMaximumWidth(ROW_MAX_WIDTH)
    outer = QHBoxLayout()
    outer.setContentsMargins(0, 0, 0, 0)
    outer.addWidget(box)
    outer.addStretch()
    return outer


class PanelsMixin:
    """Builds the sidebar and the cursor readout.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``_apply_gamma_change``, ``_on_first_file``, ``_on_last_file``,
    ``_on_next_file``, ``_on_prev_file``, ``_on_threshold_edited``,
    ``_show_filter_dialog``, ``_show_playlist_dialog``,
    ``_show_roi_histogram``, ``apply_auto_range``, ``current_image_data``,
    ``imagename``, ``invalidate_contours``, ``on_checkbox_changed``,
    ``on_stokes_changed``, ``on_stretch_changed``,
    ``on_visualization_changed``, ``open_helioviewer_with_time``,
    ``reset_view``, ``select_file_or_directory``, ``set_solar_disk_center``,
    ``show_beam_settings``, ``show_contour_settings``, ``show_grid_settings``,
    ``show_noaa_events_for_current_image``, ``show_range``,
    ``show_rms_box_dialog``, ``update_display``, ``update_gamma_slider``,
    ``update_gamma_value``
    """

    def create_sidebar(self, parent_layout):
        """The sidebar's sections, top to bottom."""
        self.create_image_section(parent_layout)
        self.create_levels_section(parent_layout)
        self.create_overlays_section(parent_layout)
        self.create_statistics_section(parent_layout)
        parent_layout.addStretch()

    # ------------------------------------------------------------------ Image
    def create_image_section(self, parent_layout):
        fold = FoldSection("Image", "image")
        layout = fold.layout

        self.dir_entry = QLineEdit()
        self.dir_entry.setPlaceholderText("FITS file or CASA image")
        self.dir_entry.setReadOnly(True)
        self.browse_btn = QPushButton()
        self.browse_btn.setObjectName("IconOnlyNBGButton")
        self.browse_btn.setIcon(QIcon(get_resource_path("assets/browse.png")))
        self.browse_btn.setIconSize(QSize(22, 22))
        self.browse_btn.setToolTip("Open a FITS file or CASA image (Ctrl+O)")
        self.browse_btn.clicked.connect(self.select_file_or_directory)
        layout.addLayout(_row(self.dir_entry, self.browse_btn, spacing=6))

        # The files beside this one in its folder
        self._first_file_btn = _nav_button("|◀", "First file in the folder", self._on_first_file)
        self._prev_file_btn = _nav_button("◀", "Previous file ([)", self._on_prev_file)
        self._file_pos_label = QLabel("")
        self._file_pos_label.setObjectName("Hint")
        self._file_pos_label.setAlignment(Qt.AlignCenter)
        self._next_file_btn = _nav_button("▶", "Next file (])", self._on_next_file)
        self._last_file_btn = _nav_button("▶|", "Last file in the folder", self._on_last_file)
        self._filter_btn = _nav_button("", "Which files the arrows step through", self._show_filter_dialog, True)
        self._filter_btn.setIcon(QIcon(themed_icon("settings.png")))
        self._filter_btn.setIconSize(QSize(16, 16))
        self._playlist_btn = _nav_button("☰", "All files in the folder", self._show_playlist_dialog, True)
        nav = _row(
            self._first_file_btn, self._prev_file_btn, self._file_pos_label,
            self._next_file_btn, self._last_file_btn, 1, self._filter_btn, self._playlist_btn,
            spacing=2,
        )
        layout.addLayout(_capped(nav))

        self.stokes_combo = QComboBox()
        self.stokes_combo.addItems(
            ["I", "Q", "U", "V", "L", "Lfrac", "Vfrac", "Q/I", "U/I", "U/V", "PANG"]
        )
        self.stokes_combo.setToolTip("Stokes parameter or product to show")
        self.stokes_combo.currentTextChanged.connect(self.on_stokes_changed)
        self.stokes_combo.setItemDelegate(DisabledItemDelegate(self.stokes_combo))
        self.stokes_combo.setMinimumWidth(self.fontMetrics().horizontalAdvance("Vfrac") + 40)

        threshold_tip = (
            "Threshold, in units of the RMS (σ) measured in the RMS box.\n"
            "Lfrac, Vfrac, Q/I, U/I and U/V: pixels whose numerator is below "
            "threshold × RMS are set to 0.\n"
            "PANG: pixels whose polarized intensity is below it are left blank.\n"
            "I, Q, U, V and L are shown as they are."
        )
        thresh_label = QLabel("Threshold:")
        thresh_label.setToolTip(threshold_tip)
        self.threshold_entry = QLineEdit("10")
        fit_width_to_text(self.threshold_entry, "0000")
        self.threshold_entry.setToolTip(threshold_tip)
        self.threshold_entry.editingFinished.connect(self._on_threshold_edited)
        sigma = QLabel("σ")
        sigma.setToolTip(threshold_tip)
        rms_tip = (
            "RMS box: where the noise (RMS) is measured, by default a band "
            "along the bottom of the image.\nThe RMS sets the threshold of the "
            "derived Stokes products, the image statistics (RMS, dynamic range) "
            "and contour levels in σ.\nClick the gear to change the box."
        )
        self.rms_label = QLabel("RMS box")
        self.rms_label.setObjectName("Hint")
        self.rms_label.setToolTip(rms_tip)
        self.rms_settings_btn = _gear(rms_tip, self.show_rms_box_dialog)
        self.rms_settings_btn.setAccessibleName("RMS box settings")
        layout.addLayout(_capped(_row(
            QLabel("Stokes:"), self.stokes_combo, 1, thresh_label, self.threshold_entry, sigma,
            self.rms_label, self.rms_settings_btn,
        )))

        self.downsample_toggle = QCheckBox("Fast preview")
        self.downsample_toggle.setObjectName("Switch")
        self.downsample_toggle.setToolTip(
            "Load images at reduced resolution, for quick browsing"
        )
        # Says "preview" while the image shown is the reduced one
        self.downsample_status = QLabel("")
        self.downsample_status.setObjectName("Hint")
        set_tone(self.downsample_status, "success")

        def update_fast_load_status(checked):
            self.downsample_status.setText("shown reduced" if checked else "")
            # Contour data is read again at the new resolution
            self.invalidate_contours()
            if self.current_image_data is not None and self.imagename:
                self.on_visualization_changed()
                self.reset_view()

        self.downsample_toggle.stateChanged.connect(update_fast_load_status)
        # Display changes apply as they are made; this reads the image
        # again, which is rarely needed
        self.plot_button = QPushButton("Reload")
        self.plot_button.setObjectName("NavButton")
        self.plot_button.setToolTip("Read the image again from disk")
        self.plot_button.clicked.connect(self.on_visualization_changed)
        layout.addLayout(_capped(_row(
            self.downsample_toggle, self.downsample_status, 1, self.plot_button
        )))
        parent_layout.addWidget(fold)

    # ----------------------------------------------------------------- Levels
    def create_levels_section(self, parent_layout):
        """The histogram with the range on it, and the range, colormap,
        stretch and gamma controls under it."""
        fold = FoldSection("Levels", "levels")
        layout = fold.layout

        self.levels = LevelsHistogram()
        self.levels.rangeEdited.connect(self._on_levels_dragged)
        layout.addWidget(self.levels)

        self.vmin_entry = QLineEdit("0.0")
        self.vmin_entry.setToolTip("Lowest value shown")
        fit_width_to_text(self.vmin_entry, "-0.000000")
        self.vmin_entry.editingFinished.connect(self.update_display)
        self.vmax_entry = QLineEdit("1.0")
        self.vmax_entry.setToolTip("Highest value shown")
        fit_width_to_text(self.vmax_entry, "-0.000000")
        self.vmax_entry.editingFinished.connect(self.update_display)
        self.auto_minmax_button = QPushButton("Auto")
        self.auto_minmax_button.setObjectName("NavButton")
        self.auto_minmax_button.setToolTip("Range: the image's minimum to its maximum (F5)")
        self.auto_minmax_button.clicked.connect(lambda: self.apply_auto_range("minmax"))
        self.auto_percentile_button = QPushButton("1–99%")
        self.auto_percentile_button.setObjectName("NavButton")
        self.auto_percentile_button.setToolTip("Range: the 1st to the 99th percentile (F6)")
        self.auto_percentile_button.clicked.connect(lambda: self.apply_auto_range("p1_99"))
        self.auto_median_button = QPushButton("Med±3σ")
        self.auto_median_button.setObjectName("NavButton")
        self.auto_median_button.setToolTip("Range: the median ± 3 times the RMS (F7)")
        self.auto_median_button.clicked.connect(lambda: self.apply_auto_range("median_rms"))
        layout.addLayout(_capped(_row(
            QLabel("Range:"), self.vmin_entry, QLabel("to"), self.vmax_entry, 1,
            self.auto_minmax_button, self.auto_percentile_button, self.auto_median_button,
            spacing=6,
        )))

        radio_colormaps = [
            "viridis", "plasma", "inferno", "magma", "gist_heat", "hot",
            "CMRmap", "gnuplot2", "jet", "twilight",
        ]
        all_colormaps = sorted(plt.colormaps())
        self.cmap_combo = ColormapSelector(
            preferred_items=radio_colormaps, all_items=all_colormaps
        )
        self.cmap_combo.setCurrentText("viridis")
        self.cmap_combo.setToolTip("Colormap")
        self.cmap_combo.colormapSelected.connect(self.update_display)
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
        gamma_label = QLabel("γ")
        gamma_label.setToolTip("Gamma of the power stretch")
        self.gamma_slider = QSlider(Qt.Horizontal)
        self.gamma_slider.setRange(1, 60)
        self.gamma_slider.setValue(20)
        self.gamma_slider.setToolTip("Gamma of the power stretch")
        self.gamma_slider.setMinimumWidth(70)
        self.gamma_slider.valueChanged.connect(self.update_gamma_value)
        self.gamma_slider.sliderReleased.connect(self._apply_gamma_change)
        self.gamma_entry = QLineEdit("1.0")
        fit_width_to_text(self.gamma_entry, "0.00")
        self.gamma_entry.editingFinished.connect(self.update_gamma_slider)
        layout.addLayout(_capped(_row(
            self.cmap_combo, self.stretch_combo, gamma_label, self.gamma_slider, self.gamma_entry,
            spacing=6,
        )))
        parent_layout.addWidget(fold)

    def _on_levels_dragged(self, vmin, vmax):
        """A range marker was dragged on the histogram."""
        self.show_range(vmin, vmax)
        self.update_display()

    # --------------------------------------------------------------- Overlays
    def create_overlays_section(self, parent_layout):
        fold = FoldSection("Overlays", "overlays")
        self.show_beam_checkbox = _switch("Beam", self.on_checkbox_changed, checked=True)
        self.show_beam_label = self.show_beam_checkbox  # (disabled with it when there is no beam)
        self.beam_settings_button = _gear("Beam Settings", self.show_beam_settings)
        self.show_grid_checkbox = _switch("Grid", self.on_checkbox_changed)
        self.grid_settings_button = _gear("Grid Settings", self.show_grid_settings)
        self.show_solar_disk_checkbox = _switch("Solar Disk", self.on_checkbox_changed)
        self.solar_disk_center_button = _gear("Customize Solar Disk", self.set_solar_disk_center)
        self.show_contours_checkbox = _switch("Contours", self.on_checkbox_changed)
        self.contour_settings_button = _gear("Contour Settings", self.show_contour_settings)
        cells = []
        for switch, gear in (
            (self.show_beam_checkbox, self.beam_settings_button),
            (self.show_grid_checkbox, self.grid_settings_button),
            (self.show_solar_disk_checkbox, self.solar_disk_center_button),
            (self.show_contours_checkbox, self.contour_settings_button),
        ):
            cell = QWidget()
            cell.setLayout(_row(switch, gear, 1, spacing=2))
            cells.append(cell)
        # One row when the sidebar is wide, two when it is narrow
        fold.layout.addWidget(ResponsiveRow(cells, cell_width=130))
        parent_layout.addWidget(fold)

    # ------------------------------------------------------------- Statistics
    def create_statistics_section(self, parent_layout):
        """Region and image statistics: side by side when the sidebar is
        wide, stacked when it is narrow. The region table appears with
        the first region drawn."""
        fold = FoldSection("Statistics", "statistics")
        region = QWidget()
        region_layout = QVBoxLayout(region)
        region_layout.setContentsMargins(0, 0, 0, 0)
        region_layout.setSpacing(6)
        self.create_stats_table(region_layout)
        image = QWidget()
        image_layout = QVBoxLayout(image)
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_layout.setSpacing(6)
        self.create_image_stats_table(image_layout)
        fold.layout.addWidget(ResponsiveRow([image, region], cell_width=270))
        parent_layout.addWidget(fold)

    def create_stats_table(self, parent_layout):
        title = QLabel("Region")
        title.setObjectName("Subheading")
        parent_layout.addWidget(title)
        self.info_label = QLabel("Draw a rectangle or ellipse on the image.")
        self.info_label.setObjectName("Hint")
        self.info_label.setWordWrap(True)
        parent_layout.addWidget(self.info_label)
        self.stats_table = _metric_table(
            [(name, "") for name in ("Min", "Max", "Mean", "Std Dev", "Sum", "RMS")]
        )
        self.stats_table.hide()  # until there are statistics
        parent_layout.addWidget(self.stats_table)
        self.histogram_btn = QPushButton("Histogram")
        self.histogram_btn.setObjectName("NavButton")
        self.histogram_btn.setToolTip("The pixel value histogram of the region")
        self.histogram_btn.clicked.connect(self._show_roi_histogram)
        self.histogram_btn.hide()  # with the table
        parent_layout.addLayout(_row(self.histogram_btn, 1))
        parent_layout.addStretch()

    def create_image_stats_table(self, parent_layout):
        title = QLabel("Image")
        title.setObjectName("Subheading")
        parent_layout.addWidget(title)
        self.image_info_label = QLabel("Full image statistics")
        self.image_info_label.setObjectName("Hint")
        self.image_info_label.setWordWrap(True)
        parent_layout.addWidget(self.image_info_label)
        # Short labels, so the values keep their room in a narrow panel
        self.image_stats_table = _metric_table(
            [
                ("Max", ""),
                ("Min", ""),
                ("RMS", "RMS inside the RMS box"),
                ("Box mean", "Mean inside the RMS box"),
                ("Pos. DR", "Positive dynamic range: Max / RMS"),
                ("Neg. DR", "Negative dynamic range: Min / RMS"),
            ]
        )
        parent_layout.addWidget(self.image_stats_table)
        self.create_noaa_events_button(parent_layout)
        parent_layout.addStretch()

    def create_noaa_events_button(self, parent_layout):
        """Solar Activity and Helioviewer buttons for the image's observation time."""
        self.noaa_events_btn = QPushButton("Activity")
        self.noaa_events_btn.setObjectName("NavButton")
        self.noaa_events_btn.setToolTip(
            "Solar activity at the image's time (events, active regions, conditions, CMEs)"
        )
        self.noaa_events_btn.setEnabled(False)  # until an image with a time is shown
        self.noaa_events_btn.clicked.connect(self.show_noaa_events_for_current_image)
        self.helioviewer_btn = QPushButton("Helioviewer")
        self.helioviewer_btn.setObjectName("NavButton")
        self.helioviewer_btn.setToolTip("Helioviewer images around the image's time")
        self.helioviewer_btn.setEnabled(False)
        self.helioviewer_btn.clicked.connect(self.open_helioviewer_with_time)
        parent_layout.addLayout(_row(self.noaa_events_btn, self.helioviewer_btn, 1))

    # --------------------------------------------------------- cursor readout
    def create_coord_display(self):
        """The cursor readout: one monospace line the window shows in its
        status bar while this tab is current (see the window's
        ``_show_current_readout``)."""
        self.coord_label = QLabel("No image loaded")
        self.coord_label.setObjectName("Mono")
        self.coord_label.setTextFormat(Qt.RichText)
        self.coord_label.hide()

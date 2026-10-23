"""
The image tab's sidebar beside the image (Image, Display, Overlays, Region
Statistics, Image Statistics: folding sections) and the cursor readout the
window shows in its status bar. The image keeps the full height of the tab;
the sidebar takes the width a square image leaves over.
"""

import logging
import matplotlib.pyplot as plt
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QCheckBox,
    QGridLayout,
    QHeaderView,
    QSizePolicy,
)
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QIcon
from ..ui.geometry import fit_width_to_text
from ..ui.searchable_combobox import ColormapSelector
from ..ui.icons import get_resource_path, themed_icon
from ..ui.styles import set_tone
from ..ui.widgets import FoldSection
from ..ui.item_delegates import DisabledItemDelegate

logger = logging.getLogger(__name__)


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


class PanelsMixin:
    """Builds the sidebar and the cursor readout.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``_apply_gamma_change``, ``_on_threshold_edited``,
    ``_show_roi_histogram``, ``apply_auto_range``, ``current_image_data``,
    ``imagename``, ``invalidate_contours``, ``on_checkbox_changed``,
    ``on_stokes_changed``, ``on_stretch_changed``,
    ``on_visualization_changed``, ``open_helioviewer_with_time``,
    ``reset_view``, ``select_file_or_directory``, ``set_solar_disk_center``,
    ``show_beam_settings``, ``show_contour_settings``, ``show_grid_settings``,
    ``show_noaa_events_for_current_image``, ``show_rms_box_dialog``,
    ``update_display``, ``update_gamma_slider``, ``update_gamma_value``
    """

    # -------------------------------------------------------- display section
    def create_display_section(self, parent_layout):
        """How the image is shown: colormap and stretch, the intensity
        range with its presets, and gamma. The first section of the sidebar,
        so the canvas keeps its height for the image."""
        fold = FoldSection("Display", "display")
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)

        radio_colormaps = [
            "viridis", "plasma", "inferno", "magma", "gist_heat", "hot",
            "CMRmap", "gnuplot2", "jet", "twilight",
        ]
        all_colormaps = sorted(plt.colormaps())
        self.cmap_combo = ColormapSelector(
            preferred_items=radio_colormaps, all_items=all_colormaps
        )
        self.cmap_combo.setCurrentText("viridis")
        self.cmap_combo.colormapSelected.connect(self.update_display)
        grid.addWidget(QLabel("Colormap:"), 0, 0)
        grid.addWidget(self.cmap_combo, 0, 1)

        self.stretch_combo = QComboBox()
        self.stretch_combo.addItems(
            ["linear", "sqrt", "log", "arcsinh", "power", "zscale", "histeq"]
        )
        self.stretch_combo.setCurrentText("power")
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
        grid.addWidget(QLabel("Stretch:"), 1, 0)
        grid.addWidget(self.stretch_combo, 1, 1)

        range_row = QHBoxLayout()
        range_row.setSpacing(6)
        self.vmin_entry = QLineEdit("0.0")
        self.vmin_entry.setToolTip("Lowest value shown")
        self.vmin_entry.editingFinished.connect(self.update_display)
        self.vmax_entry = QLineEdit("1.0")
        self.vmax_entry.setToolTip("Highest value shown")
        self.vmax_entry.editingFinished.connect(self.update_display)
        range_row.addWidget(self.vmin_entry, 1)
        range_row.addWidget(QLabel("to"))
        range_row.addWidget(self.vmax_entry, 1)
        grid.addWidget(QLabel("Range:"), 2, 0)
        grid.addLayout(range_row, 2, 1)

        gamma_row = QHBoxLayout()
        gamma_row.setSpacing(6)
        self.gamma_slider = QSlider(Qt.Horizontal)
        self.gamma_slider.setRange(1, 60)
        self.gamma_slider.setValue(20)
        self.gamma_slider.setToolTip("Gamma of the power stretch")
        self.gamma_slider.valueChanged.connect(self.update_gamma_value)
        self.gamma_slider.sliderReleased.connect(self._apply_gamma_change)
        self.gamma_entry = QLineEdit("1.0")
        fit_width_to_text(self.gamma_entry, "0.00")
        self.gamma_entry.editingFinished.connect(self.update_gamma_slider)
        gamma_row.addWidget(self.gamma_slider, 1)
        gamma_row.addWidget(self.gamma_entry)
        grid.addWidget(QLabel("Gamma:"), 3, 0)
        grid.addLayout(gamma_row, 3, 1)
        fold.layout.addLayout(grid)

        presets = QHBoxLayout()
        presets.setSpacing(6)
        self.auto_minmax_button = QPushButton("Auto")
        self.auto_minmax_button.setToolTip("Range: the image's minimum to its maximum (F5)")
        self.auto_minmax_button.clicked.connect(lambda: self.apply_auto_range("minmax"))
        self.auto_percentile_button = QPushButton("1-99%")
        self.auto_percentile_button.setToolTip("Range: the 1st to the 99th percentile (F6)")
        self.auto_percentile_button.clicked.connect(lambda: self.apply_auto_range("p1_99"))
        self.auto_median_button = QPushButton("Med±3σ")
        self.auto_median_button.setToolTip("Range: the median ± 3 times the RMS (F7)")
        self.auto_median_button.clicked.connect(lambda: self.apply_auto_range("median_rms"))
        for button in (self.auto_minmax_button, self.auto_percentile_button, self.auto_median_button):
            presets.addWidget(button)
        fold.layout.addLayout(presets)
        parent_layout.addWidget(fold)

    # ---------------------------------------------------------------- sidebar
    def create_sidebar(self, parent_layout):
        """The sidebar beside the image: the image and how it is read,
        the overlays, and the statistics. Each section folds."""
        self.create_image_section(parent_layout)
        self.create_display_section(parent_layout)
        self.create_overlays_section(parent_layout)
        self.create_stats_table(parent_layout)
        self.create_image_stats_table(parent_layout)
        parent_layout.addStretch()

    def create_image_section(self, parent_layout):
        fold = FoldSection("Image", "image")
        layout = fold.layout

        file_row = QHBoxLayout()
        file_row.setSpacing(6)
        self.dir_entry = QLineEdit()
        self.dir_entry.setPlaceholderText("FITS file or CASA image")
        self.dir_entry.setReadOnly(True)
        self.browse_btn = QPushButton()
        self.browse_btn.setObjectName("IconOnlyNBGButton")
        self.browse_btn.setIcon(QIcon(get_resource_path("assets/browse.png")))
        self.browse_btn.setIconSize(QSize(22, 22))
        self.browse_btn.setToolTip("Open a FITS file or CASA image (Ctrl+O)")
        self.browse_btn.clicked.connect(self.select_file_or_directory)
        file_row.addWidget(self.dir_entry, 1)
        file_row.addWidget(self.browse_btn)
        layout.addLayout(file_row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)

        self.stokes_combo = QComboBox()
        self.stokes_combo.addItems(
            ["I", "Q", "U", "V", "L", "Lfrac", "Vfrac", "Q/I", "U/I", "U/V", "PANG"]
        )
        self.stokes_combo.currentTextChanged.connect(self.on_stokes_changed)
        self.stokes_combo.setItemDelegate(DisabledItemDelegate(self.stokes_combo))
        grid.addWidget(QLabel("Stokes:"), 0, 0)
        grid.addWidget(self.stokes_combo, 0, 1)

        threshold_tip = (
            "Threshold, in units of the RMS (σ) measured in the RMS box.\n"
            "Lfrac, Vfrac, Q/I, U/I and U/V: pixels whose numerator is below "
            "threshold × RMS are set to 0.\n"
            "PANG: pixels whose polarized intensity is below it are left blank.\n"
            "I, Q, U, V and L are shown as they are."
        )
        thresh_label = QLabel("Threshold (σ):")
        thresh_label.setToolTip(threshold_tip)
        self.threshold_entry = QLineEdit("10")
        fit_width_to_text(self.threshold_entry, "0000")
        self.threshold_entry.setToolTip(threshold_tip)
        self.threshold_entry.editingFinished.connect(self._on_threshold_edited)
        grid.addWidget(thresh_label, 1, 0)
        threshold_row = QHBoxLayout()
        threshold_row.setSpacing(8)
        threshold_row.addWidget(self.threshold_entry)
        threshold_row.addStretch()
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
        threshold_row.addWidget(self.rms_label)
        threshold_row.addWidget(self.rms_settings_btn)
        grid.addLayout(threshold_row, 1, 1)
        layout.addLayout(grid)

        options_row = QHBoxLayout()
        options_row.setSpacing(8)
        self.downsample_toggle = QCheckBox("Fast")
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
            # Contour data is read again at the new resolution
            self.invalidate_contours()
            if self.current_image_data is not None and self.imagename:
                self.on_visualization_changed()
                self.reset_view()

        self.downsample_toggle.stateChanged.connect(update_fast_load_status)
        options_row.addWidget(self.downsample_toggle)
        options_row.addWidget(self.downsample_status)
        options_row.addStretch()
        # Display changes apply as they are made; this reads the image
        # again, which is rarely needed
        self.plot_button = QPushButton("Reload")
        self.plot_button.setToolTip("Read the image again from disk")
        self.plot_button.clicked.connect(self.on_visualization_changed)
        options_row.addWidget(self.plot_button)
        layout.addLayout(options_row)

        parent_layout.addWidget(fold)

    def create_overlays_section(self, parent_layout):
        fold = FoldSection("Overlays", "overlays")
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        grid.setContentsMargins(0, 0, 0, 0)

        self.show_beam_checkbox = _switch("Beam", self.on_checkbox_changed, checked=True)
        self.show_beam_label = self.show_beam_checkbox  # (disabled with it when there is no beam)
        self.beam_settings_button = _gear("Beam Settings", self.show_beam_settings)
        self.show_grid_checkbox = _switch("Grid", self.on_checkbox_changed)
        self.grid_settings_button = _gear("Grid Settings", self.show_grid_settings)
        self.show_solar_disk_checkbox = _switch("Solar Disk", self.on_checkbox_changed)
        self.solar_disk_center_button = _gear("Customize Solar Disk", self.set_solar_disk_center)
        self.show_contours_checkbox = _switch("Contours", self.on_checkbox_changed)
        self.contour_settings_button = _gear("Contour Settings", self.show_contour_settings)

        cells = [
            (self.show_beam_checkbox, self.beam_settings_button, 0, 0),
            (self.show_grid_checkbox, self.grid_settings_button, 0, 1),
            (self.show_solar_disk_checkbox, self.solar_disk_center_button, 1, 0),
            (self.show_contours_checkbox, self.contour_settings_button, 1, 1),
        ]
        for switch, gear, row, col in cells:
            cell = QHBoxLayout()
            cell.setContentsMargins(0, 0, 0, 0)
            cell.setSpacing(2)
            cell.addWidget(switch)
            cell.addWidget(gear)
            cell.addStretch()
            grid.addLayout(cell, row, col)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        fold.layout.addLayout(grid)
        parent_layout.addWidget(fold)

    def create_stats_table(self, parent_layout):
        fold = FoldSection("Region Statistics", "region_stats")
        layout = fold.layout
        self.info_label = QLabel("No selection. Draw a rectangle or ellipse on the image.")
        self.info_label.setObjectName("Hint")
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)
        self.stats_table = _metric_table(
            [(name, "") for name in ("Min", "Max", "Mean", "Std Dev", "Sum", "RMS")]
        )
        layout.addWidget(self.stats_table)
        self.histogram_btn = QPushButton("Histogram")
        self.histogram_btn.setToolTip("Show pixel value histogram for current ROI")
        self.histogram_btn.clicked.connect(self._show_roi_histogram)
        layout.addWidget(self.histogram_btn)
        parent_layout.addWidget(fold)

    def create_image_stats_table(self, parent_layout):
        fold = FoldSection("Image Statistics", "image_stats")
        layout = fold.layout
        self.image_info_label = QLabel("Full image statistics")
        self.image_info_label.setObjectName("Hint")
        self.image_info_label.setWordWrap(True)
        layout.addWidget(self.image_info_label)
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
        layout.addWidget(self.image_stats_table)
        self.create_noaa_events_button(layout)
        parent_layout.addWidget(fold)

    def create_noaa_events_button(self, parent_layout):
        """Solar Activity and Helioviewer buttons for the image's observation time."""
        buttons_layout = QHBoxLayout()
        self.noaa_events_btn = QPushButton("Activity")
        self.noaa_events_btn.setToolTip(
            "View Solar Activity (Events, Active Regions, Conditions, CMEs)"
        )
        self.noaa_events_btn.setEnabled(False)  # until an image with a time is shown
        self.noaa_events_btn.clicked.connect(self.show_noaa_events_for_current_image)
        buttons_layout.addWidget(self.noaa_events_btn)
        self.helioviewer_btn = QPushButton("Helioviewer")
        self.helioviewer_btn.setToolTip(
            "Browse Helioviewer images around current observation time"
        )
        self.helioviewer_btn.setEnabled(False)
        self.helioviewer_btn.clicked.connect(self.open_helioviewer_with_time)
        buttons_layout.addWidget(self.helioviewer_btn)
        parent_layout.addLayout(buttons_layout)

    # --------------------------------------------------------- cursor readout
    def create_coord_display(self):
        """The cursor readout: one monospace line the window shows in its
        status bar while this tab is current (see the window's
        ``_show_current_readout``)."""
        self.coord_label = QLabel("No image loaded")
        self.coord_label.setObjectName("Mono")
        self.coord_label.setTextFormat(Qt.RichText)
        self.coord_label.hide()

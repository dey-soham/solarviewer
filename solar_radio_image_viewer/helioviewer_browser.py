#!/usr/bin/env python3
"""
Helioviewer Browser - Time-series viewer for solar images from Helioviewer API.
"""

import logging

from PyQt5.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QSplitter,
    QLabel,
    QPushButton,
    QLineEdit,
    QSpinBox,
    QScrollArea,
    QFrame,
    QProgressBar,
    QComboBox,
    QMessageBox,
    QGroupBox,
    QTreeWidget,
    QTreeWidgetItem,
    QHeaderView,
    QRubberBand,
    QGridLayout,
    QGraphicsOpacityEffect,
    QSlider,
    QShortcut,
)
from PyQt5.QtCore import (
    Qt,
    QDateTime,
    QTimer,
    QSize,
    QPoint,
    QPointF,
    QRect,
    QVariantAnimation,
)
from PyQt5.QtGui import QPainter, QPixmap, QColor, QKeySequence, QIcon, QPolygonF
from collections import OrderedDict, deque

TIME_QT_FORMAT = "yyyy-MM-dd HH:mm:ss"
from .helioviewer_data import ImageDownloader, InstrumentLoader
from .helioviewer_export import HelioviewerExportMixin, difference_image
from .ui.activity import ActivityLabel
from .ui.busy import busy
from .ui.date_picker import attach_date_picker
from .ui.icons import themed_icon
from .ui.styles import SPACE_L, SPACE_M, SPACE_S, SPACE_XS, set_hand_cursor, theme_manager, themed
from .ui.widgets import empty_note, vrule
from .video.ui.progress import WorkDialog

logger = logging.getLogger(__name__)

# The playback buttons' shapes on a 16 px square: filled polygons
_BAR_LEFT = ((3, 3), (5, 3), (5, 13), (3, 13))
_BAR_RIGHT = ((11, 3), (13, 3), (13, 13), (11, 13))
TRANSPORT_SHAPES = {
    "first": (_BAR_LEFT, ((13, 3), (6, 8), (13, 13))),
    "previous": (((12, 3), (4, 8), (12, 13)),),
    "play": (((4, 2.5), (13, 8), (4, 13.5)),),
    "pause": (((4, 3), (7, 3), (7, 13), (4, 13)), ((9, 3), (12, 3), (12, 13), (9, 13))),
    "next": (((4, 3), (12, 8), (4, 13)),),
    "last": (((3, 3), (10, 8), (3, 13)), _BAR_RIGHT),
}


def transport_icon(kind, palette):
    """The icon of a playback button, in the theme's colours."""
    icon = QIcon()
    for mode, key in ((QIcon.Normal, "text_strong"), (QIcon.Disabled, "disabled")):
        for scale in (1, 2):
            pixmap = QPixmap(16 * scale, 16 * scale)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.scale(scale, scale)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(palette[key]))
            for shape in TRANSPORT_SHAPES[kind]:
                painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in shape]))
            painter.end()
            icon.addPixmap(pixmap, mode)
    return icon

# Global list to keep threads alive if window is closed while they are running
# This prevents "QThread: Destroyed while thread is still running"
_active_threads = []


def _park(thread):
    """Keep ``thread`` until it ends by itself (nothing can interrupt a request)."""
    _active_threads[:] = [t for t in _active_threads if t.isRunning()]
    if thread.isRunning():
        _active_threads.append(thread)


from .ui.geometry import fit_to_screen


class InteractiveImageLabel(QLabel):
    """A QLabel that allows drawing a selection rectangle and panning."""

    def __init__(self, parent=None, scroll_area=None):
        super().__init__(parent)
        self.rubber_band = None
        self.origin = QPoint()
        self.selection_rect = QRect()
        self.zoom_factor = 1.0
        self.scroll_area = scroll_area
        self.pan_last_pos = QPoint()
        self.panning = False
        self.crop_mode = False
        self.setCursor(Qt.ArrowCursor)

    def mousePressEvent(self, event):
        if self.crop_mode and event.button() == Qt.LeftButton:
            self.setCursor(Qt.CrossCursor)
            # Selection mode
            if not self.rubber_band:
                self.rubber_band = QRubberBand(QRubberBand.Rectangle, self)
            self.origin = event.pos()
            self.rubber_band.setGeometry(QRect(self.origin, QSize()))
            self.rubber_band.show()
        elif event.button() == Qt.RightButton or (
            event.button() == Qt.MiddleButton
        ) or (not self.crop_mode and event.button() == Qt.LeftButton):
            # Panning mode (Right click, Middle click, or Left click if not cropping)
            self.panning = True
            self.pan_last_pos = event.globalPos()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self.panning:
            delta = event.globalPos() - self.pan_last_pos
            self.pan_last_pos = event.globalPos()

            if self.scroll_area:
                h_bar = self.scroll_area.horizontalScrollBar()
                v_bar = self.scroll_area.verticalScrollBar()
                h_bar.setValue(h_bar.value() - delta.x())
                v_bar.setValue(v_bar.value() - delta.y())
        elif self.crop_mode and self.rubber_band and not self.origin.isNull():
            self.rubber_band.setGeometry(QRect(self.origin, event.pos()).normalized())

    def mouseReleaseEvent(self, event):
        if self.crop_mode and event.button() == Qt.LeftButton:
            if self.rubber_band:
                self.selection_rect = self.rubber_band.geometry()
        else:
            self.panning = False
            self.setCursor(Qt.ArrowCursor)

    def get_selection_rect(self):
        """Return the selection rectangle relative to the ORIGINAL pixmap scale."""
        if self.selection_rect.isEmpty() or not self.pixmap():
            return QRect()

        # Map widget coordinates to current scaled pixmap coordinates
        pixmap_size = self.pixmap().size()
        widget_rect = self.rect()

        x_offset = (widget_rect.width() - pixmap_size.width()) // 2
        y_offset = (widget_rect.height() - pixmap_size.height()) // 2

        # Selection in screen (zoomed) pixels, relative to pixmap top-left
        selection_zoomed = self.selection_rect.translated(-x_offset, -y_offset)

        # Scale selection back to original image resolution
        x = int(selection_zoomed.x() / self.zoom_factor)
        y = int(selection_zoomed.y() / self.zoom_factor)
        w = int(selection_zoomed.width() / self.zoom_factor)
        h = int(selection_zoomed.height() / self.zoom_factor)

        # Only the image can be cropped, not the margin around it
        return QRect(x, y, w, h).intersected(QRect(QPoint(0, 0), pixmap_size / self.zoom_factor))


class ZoomOverlayFrame(QFrame):
    """A floating toolbar frame that fades in on hover."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_effect)
        self.opacity_effect.setOpacity(0.4)  # Default transparency

    def enterEvent(self, event):
        self.opacity_effect.setOpacity(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.opacity_effect.setOpacity(0.4)
        super().leaveEvent(event)


'''class FullImageDialog(QDialog):
    """Dialog to show full resolution image."""

    def __init__(self, parent, pixmap, title):
        super().__init__(parent)
        self.setWindowTitle(title)
        fit_to_screen(self, 1200, 1200)

        layout = QVBoxLayout(self)

        # Scroll area for large image
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        label = QLabel()
        label.setPixmap(pixmap)
        label.setAlignment(Qt.AlignCenter)

        scroll.setWidget(label)
        layout.addWidget(scroll)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)'''


class HelioviewerBrowser(HelioviewerExportMixin, QMainWindow):
    """Main browser window for Helioviewer time-series."""

    def __init__(self, parent=None, initial_start=None, initial_end=None):
        super().__init__(parent)
        self.setWindowTitle("Helioviewer browser")
        fit_to_screen(self, 1600, 1000)

        # Data storage
        self.frames = OrderedDict()  # {frame_index: {instrument: pixmap}}
        self.timestamps = []
        self.current_frame = 0
        self.playing = False
        self.frame_loaders = []

        # Download queue management for parallel loading
        self.download_queue = deque()  # Queue of (frame_idx, instrument_data)
        self.active_downloads = 0
        self.downloads_total = 0
        self.downloads_done = 0  # those that failed too
        self.failed_downloads = {}  # (frame_idx, instrument) -> error
        self.max_concurrent_downloads = 8

        # Store initial times
        self.initial_start = initial_start
        self.initial_end = initial_end

        # Animation timer
        self.animation_timer = QTimer()
        self.animation_timer.timeout.connect(self.next_frame)

        # Seekbar animation for smooth sliding during playback
        self.seekbar_anim = QVariantAnimation()
        self.seekbar_anim.valueChanged.connect(self._update_seekbar_smoothly)

        # Flag to prevent updates during close
        self._closing = False
        self._close_after_export = False

        # Cropping state
        self.crop_mode = False
        self.selection_label = None

        # Loading state
        self._loading = False
        self._loading_names = set()  # the instruments of the load

        # Zoom state
        self.zoom_factor = 1.0

        # Base difference state
        self.diff_type = "None" # "None", "Base", "Running"

        self.init_ui()
        self.setup_shortcuts()
        themed(self, self._style_icons)
        set_hand_cursor(self)

    def init_ui(self):
        """Initialize the user interface."""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(SPACE_M, SPACE_M, SPACE_M, SPACE_M)
        main_layout.setSpacing(SPACE_M)

        # Top panel - Time range controls
        self.create_time_controls(main_layout)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)

        # Splitter for left/right panels
        splitter = QSplitter(Qt.Horizontal)

        # Left panel - Instrument filter
        self.create_instrument_panel(splitter)

        # Right panel - Image grid
        self.create_image_panel(splitter)

        splitter.setStretchFactor(1, 1)
        splitter.setSizes([460, 1140])
        main_layout.addWidget(splitter, 1)

        # Bottom panel - Animation controls
        self.create_animation_controls(main_layout)

    def create_time_controls(self, parent_layout):
        """Create time range input controls."""
        group = QGroupBox("Time range (UTC)")
        layout = QHBoxLayout(group)
        layout.setContentsMargins(SPACE_M, SPACE_XS, SPACE_M, SPACE_XS)
        layout.setSpacing(SPACE_S)

        # Start time
        layout.addWidget(self._form_label("Start"))
        self.start_datetime = QLineEdit()
        self.start_datetime.setMinimumWidth(170)
        self.start_datetime.setMaximumWidth(220)
        attach_date_picker(self.start_datetime, lambda text: None, band=lambda: self._picked_time(self.end_datetime))
        # Default to yesterday 10:00 UTC or initial_start if provided
        if hasattr(self, "initial_start") and self.initial_start:
            self._set_time(self.start_datetime, self.initial_start)
        else:
            default_start = QDateTime.currentDateTimeUtc().addDays(-1)
            default_start.setTime(
                default_start.time().fromString("10:00:00", "HH:mm:ss")
            )
            self._set_time(self.start_datetime, default_start)
        layout.addWidget(self.start_datetime)

        # End time
        layout.addSpacing(SPACE_S)
        layout.addWidget(self._form_label("End"))
        self.end_datetime = QLineEdit()
        self.end_datetime.setMinimumWidth(170)
        self.end_datetime.setMaximumWidth(220)
        attach_date_picker(self.end_datetime, lambda text: None, band=lambda: self._picked_time(self.start_datetime))
        if hasattr(self, "initial_end") and self.initial_end:
            self._set_time(self.end_datetime, self.initial_end)
        else:
            default_end = self._time_of(self.start_datetime).addSecs(3600)  # 1 hour later
            self._set_time(self.end_datetime, default_end)
        layout.addWidget(self.end_datetime)

        # Interval
        layout.addSpacing(SPACE_S)
        layout.addWidget(self._form_label("Interval"))
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(10, 3600)
        self.interval_spin.setValue(30)  # Default 30 seconds
        self.interval_spin.setSuffix(" s")
        layout.addWidget(self.interval_spin)

        layout.addStretch()

        self.load_button = QPushButton("Load")
        self.load_button.setObjectName("PrimaryButton")
        self.load_button.clicked.connect(self.load_time_series)
        layout.addWidget(self.load_button)

        # Cancel button (initially hidden)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel_loading)
        self.cancel_button.setVisible(False)
        layout.addWidget(self.cancel_button)

        parent_layout.addWidget(group)

    @staticmethod
    def _form_label(text):
        label = QLabel(text)
        label.setObjectName("Hint")
        return label

    @staticmethod
    def _set_time(field, value):
        field.setText(value.toString(TIME_QT_FORMAT))

    @staticmethod
    def _time_of(field):
        """The field's text as a UTC QDateTime (invalid if it does not parse).
        Local time would shift the frames across a daylight saving change."""
        value = QDateTime.fromString(field.text().strip() + " UTC", TIME_QT_FORMAT + " t")
        return value.toUTC() if value.isValid() else value

    def _picked_time(self, field):
        value = self._time_of(field)
        return value.toPyDateTime() if value.isValid() else None

    def create_instrument_panel(self, parent_splitter):
        """Create left panel with instrument filter (tree view grouped by observatory)."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, SPACE_S, 0)
        layout.setSpacing(SPACE_S)

        # Instrument tree - grouped by observatory
        self.instrument_tree = QTreeWidget()
        self.instrument_tree.setHeaderLabels(["Instrument", "Shows", "Cadence"])
        self.instrument_tree.setColumnCount(3)
        self.instrument_tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.instrument_tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.instrument_tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.instrument_tree.header().setStretchLastSection(False)
        self.instrument_tree.header().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.instrument_tree.setAlternatingRowColors(True)
        self.instrument_tree.setRootIsDecorated(True)
        self.instrument_tree.setAnimated(True)
        self.instrument_tree.setIndentation(SPACE_L)

        # Connect selection change
        self.instrument_tree.itemSelectionChanged.connect(self.on_instrument_selected)
        layout.addWidget(self.instrument_tree)

        # Said when the list is not the one Helioviewer gave
        self.instruments_note = QLabel()
        self.instruments_note.setObjectName("Tip")
        self.instruments_note.setWordWrap(True)
        self.instruments_note.linkActivated.connect(lambda _: self.load_all_instruments())
        self.instruments_note.hide()
        layout.addWidget(self.instruments_note)

        # Load instruments
        self.load_all_instruments()

        settings = QFormLayout()
        settings.setContentsMargins(0, SPACE_XS, 0, 0)
        settings.setSpacing(SPACE_S)
        settings.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        # Image size control
        self.image_size_spin = QSpinBox()
        self.image_size_spin.setRange(100, 4000)
        self.image_size_spin.setValue(600)
        self.image_size_spin.setSuffix(" px")
        self.image_size_spin.setToolTip("Image dimensions (100-4000 pixels)")
        settings.addRow(self._form_label("Image size"), self.image_size_spin)

        # Concurrent downloads control
        self.concurrent_spin = QSpinBox()
        self.concurrent_spin.setRange(1, 32)
        self.concurrent_spin.setValue(self.max_concurrent_downloads)
        self.concurrent_spin.setToolTip("Maximum concurrent downloads (1-32)")
        self.concurrent_spin.valueChanged.connect(self.on_concurrent_changed)
        settings.addRow(self._form_label("Downloads at once"), self.concurrent_spin)
        layout.addLayout(settings)

        parent_splitter.addWidget(panel)

        # Keep reference for compatibility
        self.instrument_list = self.instrument_tree

    def on_concurrent_changed(self, value):
        """Update maximum concurrent downloads."""
        self.max_concurrent_downloads = value

    def load_all_instruments(self):
        """Ask Helioviewer for all available instruments, on a thread: the
        window opens at once, with a spinner in the list until they are in."""
        self.instruments_problem = None
        self.instruments_note.hide()
        if hasattr(self, "instrument_loader"):  # asked again: the note's Retry
            _park(self.instrument_loader)
        self.instrument_tree.clear()
        self.all_instruments = []
        row = QTreeWidgetItem(self.instrument_tree)
        row.setFlags(Qt.NoItemFlags)
        row.setFirstColumnSpanned(True)
        self.instruments_status = ActivityLabel(parent=self.instrument_tree)
        self.instruments_status.start("Loading the instruments…")
        self.instrument_tree.setItemWidget(row, 0, self.instruments_status)
        self.instrument_loader = InstrumentLoader()
        self.instrument_loader.loaded.connect(self._on_instruments_loaded)
        self.instrument_loader.start()

    def _on_instruments_loaded(self, instruments, problem):
        """Fill the list, grouped by observatory."""
        if self._closing:
            return
        self.instrument_tree.clear()
        self.all_instruments = instruments
        if problem:
            self.instruments_problem = problem
            self._say_instruments_problem(theme_manager.palette)
            self.instruments_note.setToolTip(problem)
            self.instruments_note.show()

        # Observatory names
        observatory_info = {
            "SDO": "Solar Dynamics Observatory",
            "SOHO": "Solar and Heliospheric Observatory",
            "STEREO_A": "STEREO Ahead",
            "STEREO_B": "STEREO Behind",
            "GOES-16": "GOES-16 Satellite",
            "GOES-17": "GOES-17 Satellite",
            "GOES-18": "GOES-18 Satellite",
        }

        # Wavelength descriptions
        wavelength_descriptions = {
            "94": "Fe XVIII · 6.3 MK",
            "131": "Fe VIII/XXI · 0.4/10 MK",
            "171": "Fe IX · 0.6 MK",
            "193": "Fe XII/XXIV · 1.6/20 MK",
            "195": "Fe XII · 1.6 MK",
            "211": "Fe XIV · 2.0 MK",
            "284": "Fe XV · 2.0 MK",
            "304": "He II · 0.05 MK",
            "335": "Fe XVI · 2.5 MK",
            "1600": "C IV + cont · 0.1 MK",
            "1700": "Continuum · 5000 K",
            "4500": "Continuum · 5000 K",
            "magnetogram": "Magnetic field",
            "continuum": "White light",
            "white-light": "Coronagraph",
        }

        # Group instruments by observatory
        obs_groups = {}
        for nickname, layer_path, observatory, cadence in self.all_instruments:
            if observatory not in obs_groups:
                obs_groups[observatory] = []
            obs_groups[observatory].append((nickname, layer_path, cadence))

        # Create tree items
        for observatory in sorted(obs_groups.keys()):
            full_name = observatory_info.get(observatory, observatory)

            # Create observatory node
            obs_item = QTreeWidgetItem()
            obs_item.setText(0, observatory)
            obs_item.setToolTip(0, full_name)

            font = obs_item.font(0)
            font.setBold(True)
            obs_item.setFont(0, font)

            # Add instruments under this observatory
            for nickname, layer_path, cadence in sorted(obs_groups[observatory]):
                # Format cadence
                if cadence < 60:
                    cad_str = f"{cadence} s"
                elif cadence < 3600:
                    cad_str = f"{cadence//60} min"
                else:
                    cad_str = f"{cadence//3600} h"

                # Get wavelength description
                desc = ""
                for wave, wave_desc in wavelength_descriptions.items():
                    if wave in nickname.lower() or wave in nickname:
                        desc = wave_desc
                        break

                # Create instrument item
                inst_item = QTreeWidgetItem()
                inst_item.setText(0, nickname)
                inst_item.setText(1, desc)
                inst_item.setText(2, cad_str)
                inst_item.setData(
                    0, Qt.UserRole, (nickname, layer_path, observatory, cadence)
                )
                inst_item.setToolTip(
                    0,
                    (
                        f"{nickname}\nCadence: {cad_str}\n{desc}"
                        if desc
                        else f"{nickname}\nCadence: {cad_str}"
                    ),
                )

                obs_item.addChild(inst_item)

            self.instrument_tree.addTopLevelItem(obs_item)
            obs_item.setFirstColumnSpanned(True)

        # Expand all by default
        self.instrument_tree.expandAll()
        self._style_instruments(theme_manager.palette)

    def _say_instruments_problem(self, palette):
        self.instruments_note.setText(
            "Helioviewer did not answer: these are the usual instruments, and "
            f"may not all have images. <a href='retry' style='color:{palette['link']}'>Retry</a>"
        )

    def _style_instruments(self, palette):
        for i in range(self.instrument_tree.topLevelItemCount()):
            observatory = self.instrument_tree.topLevelItem(i)
            for j in range(observatory.childCount()):
                for column in (1, 2):
                    observatory.child(j).setForeground(column, QColor(palette["text_secondary"]))

    def _style_icons(self, palette):
        """What holds a colour of its own: the buttons' icons, the list's
        second text and the note's link."""
        for button, kind in self._transport_buttons:
            button.setIcon(transport_icon(kind, palette))
        self.play_btn.setIcon(transport_icon("pause" if self.playing else "play", palette))
        for button, icon in self._zoom_buttons:
            button.setIcon(QIcon(themed_icon(icon)))
        self._style_instruments(palette)
        if self.instruments_problem:
            self._say_instruments_problem(palette)

    def get_selected_instruments(self):
        """Get currently selected instrument (single selection from tree)."""
        selected_items = self.instrument_tree.selectedItems()
        instruments = []
        for item in selected_items:
            data = item.data(0, Qt.UserRole)
            if data:
                instruments.append(data)
        return instruments

    def set_all_instruments(self, checked):
        """Not used in single-selection mode."""
        pass

    def on_instrument_selected(self):
        """Handle instrument selection change."""
        self.update_interval_for_instrument()
        if self.frames:
            self.display_current_frame()

    def update_interval_for_instrument(self):
        """Update interval spinner based on selected instrument's cadence."""
        selected = self.get_selected_instruments()
        if selected:
            nickname, layer_path, observatory, cadence = selected[0]
            # Set interval to instrument cadence
            self.interval_spin.setValue(int(cadence))

    def create_image_panel(self, parent_splitter):
        """Create right panel with image grid and floating zoom controls."""
        # Container to allow floating overlays
        panel_container = QWidget()
        layout = QGridLayout(panel_container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Scroll area (Base layer)
        self.image_scroll = QScrollArea()
        scroll = self.image_scroll
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        # Container widget inside scroll area
        self.image_container = QWidget()
        self.image_grid = QVBoxLayout(self.image_container)

        # Placeholder
        self.image_grid.addWidget(empty_note("Choose an instrument and press Load to see its images"), 1)

        scroll.setWidget(self.image_container)
        layout.addWidget(scroll, 0, 0)

        # Zoom buttons, floating over the bottom right of the image
        zoom_toolbar = ZoomOverlayFrame()
        zoom_toolbar.setObjectName("Card")
        toolbar_layout = QHBoxLayout(zoom_toolbar)
        toolbar_layout.setContentsMargins(SPACE_XS, SPACE_XS, SPACE_XS, SPACE_XS)
        toolbar_layout.setSpacing(SPACE_XS)
        self._zoom_buttons = []
        for icon, tip, slot in (
            ("zoom_in.png", "Zoom in (+)", self.zoom_in),
            ("zoom_out.png", "Zoom out (-)", self.zoom_out),
            ("reset.png", "Reset zoom (R)", self.reset_zoom),
        ):
            button = QPushButton()
            button.setObjectName("IconOnlyNBGButton")
            button.setIcon(QIcon(themed_icon(icon)))
            button.setIconSize(QSize(18, 18))
            button.setToolTip(tip)
            button.clicked.connect(slot)
            toolbar_layout.addWidget(button)
            self._zoom_buttons.append((button, icon))

        # Container for alignment (Small wrapper to provide a floating offset)
        self.zoom_controls = QWidget()
        wrapper_layout = QVBoxLayout(self.zoom_controls)
        wrapper_layout.setContentsMargins(0, 0, SPACE_L, SPACE_L)
        wrapper_layout.addWidget(zoom_toolbar)
        self.zoom_controls.hide()  # until there is an image to zoom

        # Add wrapper to grid - it will overlay the scroll area but only in its own small bounding box
        layout.addWidget(self.zoom_controls, 0, 0, Qt.AlignBottom | Qt.AlignRight)

        parent_splitter.addWidget(panel_container)

    def create_animation_controls(self, parent_layout):
        """Create bottom panel with animation controls."""
        group = QGroupBox("Animation")
        v_layout = QVBoxLayout(group)
        v_layout.setContentsMargins(SPACE_M, SPACE_XS, SPACE_M, SPACE_XS)
        
        # Seekbar
        self.seekbar = QSlider(Qt.Horizontal)
        self.seekbar.setRange(0, 0)
        self.seekbar.setEnabled(False)
        self.seekbar.setToolTip("Seek through frames")
        self.seekbar.sliderMoved.connect(self.on_seek)
        # We also connect valueChanged but only handle it when NOT playing to avoid recursion
        self.seekbar.valueChanged.connect(self.on_seekbar_value_changed)
        v_layout.addWidget(self.seekbar)

        layout = QHBoxLayout()
        layout.setSpacing(SPACE_S)

        # Playback buttons
        playback = QHBoxLayout()
        playback.setSpacing(SPACE_XS)
        self._transport_buttons = []
        for attribute, kind, tip, slot in (
            ("first_btn", "first", "First frame (Shift+[)", self.first_frame),
            ("prev_btn", "previous", "Previous frame ([)", self.prev_frame),
            ("play_btn", "play", "Play (P / Space)", self.toggle_play),
            ("next_btn", "next", "Next frame (])", self.next_frame),
            ("last_btn", "last", "Last frame (Shift+])", self.last_frame),
        ):
            button = QPushButton()
            button.setObjectName("IconOnlyNBGButton")
            button.setIconSize(QSize(16, 16))
            button.setToolTip(tip)
            button.clicked.connect(slot)
            button.setEnabled(False)
            playback.addWidget(button)
            setattr(self, attribute, button)
            self._transport_buttons.append((button, kind))
        layout.addLayout(playback)

        layout.addSpacing(SPACE_M)

        # Frame indicator
        self.frame_label = QLabel("Frame 0 / 0")
        self.frame_label.setObjectName("Hint")
        layout.addWidget(self.frame_label)

        layout.addSpacing(SPACE_M)

        # Speed control
        layout.addWidget(self._form_label("Speed"))
        self.speed_combo = QComboBox()
        self.speed_combo.setToolTip("Playback speed (>/<)")
        self.speed_combo.addItems(["0.25x", "0.5x", "1x", "2x", "4x", "8x", "16x"])
        self.speed_combo.setCurrentText("1x")
        self.speed_combo.currentIndexChanged.connect(self.on_speed_changed)
        layout.addWidget(self.speed_combo)

        layout.addStretch()

        # Difference mode selection
        self.diff_label = self._form_label("Difference")
        layout.addWidget(self.diff_label)
        self.diff_combo = QComboBox()
        self.diff_combo.addItems(["None", "Base", "Running"])
        self.diff_combo.setToolTip("Select difference map mode (D)")
        self.diff_combo.currentIndexChanged.connect(self.on_diff_mode_changed)
        self.diff_combo.setEnabled(False)
        self.diff_label.setEnabled(False)
        layout.addWidget(self.diff_combo)
        
        # Crop tool
        self.crop_btn = QPushButton("Crop")
        self.crop_btn.setToolTip("Select a region to crop all frames")
        self.crop_btn.clicked.connect(self.toggle_crop_mode)
        self.crop_btn.setEnabled(False)
        layout.addWidget(self.crop_btn)

        self.confirm_crop_btn = QPushButton("Confirm")
        self.confirm_crop_btn.clicked.connect(self.confirm_crop)
        self.confirm_crop_btn.setVisible(False)
        layout.addWidget(self.confirm_crop_btn)

        self.cancel_crop_btn = QPushButton("Cancel")
        self.cancel_crop_btn.clicked.connect(self.cancel_crop)
        self.cancel_crop_btn.setVisible(False)
        layout.addWidget(self.cancel_crop_btn)

        layout.addSpacing(SPACE_XS)
        layout.addWidget(vrule())
        layout.addSpacing(SPACE_XS)

        # Save current frame
        save_frame_btn = QPushButton("Save frame")
        save_frame_btn.setToolTip("Save current frame (Ctrl+S)")
        save_frame_btn.clicked.connect(self.save_current_frame)
        layout.addWidget(save_frame_btn)

       # Export animation
        export_btn = QPushButton("Export animation")
        export_btn.setToolTip("Export animation (Ctrl+E)")
        export_btn.clicked.connect(self.export_animation)
        layout.addWidget(export_btn)

        # Batch download
        batch_btn = QPushButton("Batch download")
        batch_btn.clicked.connect(self.batch_download)
        layout.addWidget(batch_btn)

        v_layout.addLayout(layout)
        parent_layout.addWidget(group)

    def on_seek(self, value):
        """Handle seekbar manual scrubbing."""
        if not self.timestamps:
            return
        
        # Map 0-10000 to frame index
        total_frames = len(self.timestamps)
        if total_frames > 1:
            frame_idx = round(value * (total_frames - 1) / 10000)
        else:
            frame_idx = 0
            
        if frame_idx != self.current_frame:
            self.current_frame = frame_idx
            self.display_current_frame()

    def on_seekbar_value_changed(self, value):
        """Handle seekbar value change (e.g. from clicking)."""
        if not self.playing and self.timestamps:
            total_frames = len(self.timestamps)
            if total_frames > 1:
                frame_idx = round(value * (total_frames - 1) / 10000)
            else:
                frame_idx = 0
                
            if frame_idx != self.current_frame:
                self.current_frame = frame_idx
                self.display_current_frame()

    def _update_seekbar_smoothly(self, value):
        """Helper for QVariantAnimation to update seekbar without triggering loops."""
        self.seekbar.blockSignals(True)
        self.seekbar.setValue(value)
        self.seekbar.blockSignals(False)

    def on_speed_changed(self):
        """Handle speed change - update animation timer if playing."""
        if self.playing:
            # Recalculate interval and restart timer
            speed_text = self.speed_combo.currentText()
            speed = float(speed_text.replace("x", ""))
            interval_ms = int(0.5 * 1000 / speed)  # Base: 2 fps
            self.animation_timer.setInterval(interval_ms)

    def on_instrument_filter_changed(self):
        """Handle instrument filter change."""
        if self.frames:
            self.display_current_frame()

    def load_time_series(self):
        """Load time series based on user inputs."""
        start = self._time_of(self.start_datetime)
        end = self._time_of(self.end_datetime)
        interval_sec = self.interval_spin.value()

        if not start.isValid() or not end.isValid():
            QMessageBox.warning(
                self, "Invalid time", "Enter times as YYYY-MM-DD HH:MM:SS."
            )
            return

        # Validate
        if start >= end:
            QMessageBox.warning(
                self, "Invalid range", "Start time must be before end time."
            )
            return

        # Get selected instruments
        selected_instruments = self.get_selected_instruments()
        if not selected_instruments:
            QMessageBox.warning(
                self, "No instrument", "Choose an instrument first."
            )
            return

        # Check for large frame count
        count = start.secsTo(end) // interval_sec + 1
        if count > 5000:
            reply = QMessageBox.question(
                self,
                "Large request",
                f"This time range will generate {count} frames, which may take a long time to load and consumes significant memory.\n\n"
                "Do you want to limit it to the first 5000 frames?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                count = 5000

        # A crop drawn on the old frames ends here
        if self.crop_mode:
            self.cancel_crop()

        # Clear old data
        self.frames.clear()
        self.failed_downloads.clear()
        self.current_frame = 0

        # Show progress
        self.downloads_total = count * len(selected_instruments)
        self.downloads_done = 0
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, self.downloads_total)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%v of %m")
        self.load_button.setEnabled(False)
        self.cancel_button.setVisible(True)
        self.crop_btn.setEnabled(False)
        self.diff_combo.setEnabled(False)
        self.diff_label.setEnabled(False)
        self.diff_combo.setCurrentIndex(0) # Reset to "None"
        self._loading = True
        self._loading_names = {inst[0] for inst in selected_instruments}

        # Queue ALL frames for parallel download
        if count > 5000:
            with busy(f"Preparing {count} frames…", self):
                self._queue_frames(start, interval_sec, count, selected_instruments)
        else:
            self._queue_frames(start, interval_sec, count, selected_instruments)

        # Start initial batch of downloads
        self._process_frame_download_queue()

        #self.seekbar.setRange(0, len(self.timestamps) - 1)
        # Update seekbar range (High resolution for continuous animation)
        if len(self.timestamps) > 1:
            self.seekbar.setRange(0, 10000)
            self.seekbar.setEnabled(True)
        else:
            self.seekbar.setRange(0, 0)
            self.seekbar.setEnabled(False)
        self.seekbar.setValue(0)

        # Enable controls (navigation etc) - images will appear as they load
        self.enable_controls()

    def _queue_frames(self, start, interval_sec, count, instruments):
        self.timestamps = [start.addSecs(i * interval_sec) for i in range(count)]
        for frame_idx in range(count):
            for inst_data in instruments:
                self.download_queue.append((frame_idx, inst_data))

    def _process_frame_download_queue(self):
        """Start next frame downloads if under concurrent limit."""
        while (
            self.active_downloads < self.max_concurrent_downloads
            and self.download_queue
        ):
            frame_idx, inst_data = self.download_queue.popleft()
            nickname, layer_path, observatory, description = inst_data
            timestamp = self.timestamps[frame_idx]

            self.active_downloads += 1
            downloader = ImageDownloader(
                nickname,
                layer_path,
                timestamp,
                width=self.image_size_spin.value(),
                height=self.image_size_spin.value(),
            )
            downloader.finished.connect(
                lambda inst, image, idx=frame_idx: self.on_parallel_image_downloaded(
                    idx, inst, image
                )
            )
            downloader.error.connect(
                lambda inst, err, idx=frame_idx: self.on_parallel_download_error(
                    idx, inst, err
                )
            )
            downloader.error.connect(self._on_frame_download_finished)
            downloader.finished.connect(self._on_frame_download_finished)
            self.frame_loaders.append(downloader)
            downloader.start()

    def on_parallel_image_downloaded(self, frame_idx, instrument, image):
        """Handle parallel image download completion."""
        if self._closing or not self._loading:
            return

        if frame_idx not in self.frames:
            self.frames[frame_idx] = {}

        self.frames[frame_idx][instrument] = QPixmap.fromImage(image)

        # If this is the current frame, update display
        if frame_idx == self.current_frame:
            self.display_current_frame()

    def on_parallel_download_error(self, frame_idx, instrument, error):
        """Handle parallel download error."""
        if self._closing or not self._loading:
            return
        logger.warning("Error downloading %s for frame %s: %s", instrument, frame_idx, error)
        self.failed_downloads[(frame_idx, instrument)] = error
        if frame_idx == self.current_frame:
            self.display_current_frame()

    def _on_frame_download_finished(self):
        """Handle download thread finish and start next in queue."""
        if self._closing:
            return

        self.active_downloads -= 1
        if self.active_downloads < 0:
            self.active_downloads = 0

        # Those that failed count too: the bar ends when the work does
        self.downloads_done += 1
        self.progress_bar.setValue(self.downloads_done)
        if self.failed_downloads:
            self.progress_bar.setFormat(f"%v of %m, {len(self.failed_downloads)} failed")
        self.frame_loaders = [l for l in self.frame_loaders if l.isRunning()]

        # Process next in queue
        self._process_frame_download_queue()

        # Check if all downloads are complete
        if self.active_downloads == 0 and len(self.download_queue) == 0:
            if self._loading: # Only call if not already canceled
                self.on_loading_finished()

    def cancel_loading(self):
        """Cancel current loading process gracefully."""
        if not self._loading:
            return

        self._loading = False
        self.download_queue.clear()
        
        # Safely clear loaders and disconnect signals to prevent race conditions
        loaders = self.frame_loaders[:]
        self.frame_loaders = []
        
        for loader in loaders:
            self._abandon(loader)
        
        self.active_downloads = 0
        self.on_loading_finished(canceled=True)

    @staticmethod
    def _abandon(loader):
        """Have ``loader`` stop, and hear no more from it."""
        try:
            # Disconnect all signals to ensure no UI updates are triggered
            loader.finished.disconnect()
            loader.error.disconnect()
        except Exception:
            pass
        loader.stop()
        # Keep a reference alive until the thread actually exits
        _park(loader)

    def on_loading_finished(self, canceled=False):
        """Handle loading completion or cancellation."""
        self._loading = False
        self.progress_bar.setVisible(False)
        self.load_button.setEnabled(True)
        self.cancel_button.setVisible(False)
        self.crop_btn.setEnabled(True)  # Enable crop after all loaded
        self.diff_combo.setEnabled(True)  # Enable diff after all loaded
        self.diff_label.setEnabled(True)
        if self.timestamps:
            self.display_current_frame()  # no longer "loading"
        
        summary = f"Loaded {len(self.frames)} frames with selected instruments."
        if self.failed_downloads:
            summary += f"\nCould not download: {len(self.failed_downloads)}."
        if canceled:
            QMessageBox.information(self, "Loading cancelled", summary)
        else:
            QMessageBox.information(self, "Loading complete", summary)

    def enable_controls(self):
        """Enable animation controls."""
        has_frames = len(self.timestamps) > 0
        self.first_btn.setEnabled(has_frames)
        self.prev_btn.setEnabled(has_frames)
        self.play_btn.setEnabled(has_frames)
        self.next_btn.setEnabled(has_frames)
        self.last_btn.setEnabled(has_frames)
        self.seekbar.setEnabled(has_frames)

        if has_frames:
            self.display_current_frame()

    def display_current_frame(self):
        """Display the current frame's image (single instrument, fullscreen)."""
        if not 0 <= self.current_frame < len(self.timestamps):
            return

        # Clear grid
        while self.image_grid.count():
            child = self.image_grid.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self.selection_label = None

        timestamp = self.timestamps[self.current_frame]
        frame_data = self.frames.get(self.current_frame, {})

        # Get selected instrument (single selection)
        selected = self.get_selected_instruments()
        if not selected:
            self.zoom_controls.hide()
            self.image_grid.addWidget(empty_note("Choose an instrument from the list"), 1)
            return

        nickname, layer_path, observatory, cadence = selected[0]

        # Title with timestamp and instrument
        title = QLabel(f"{nickname} ({observatory})")
        title.setAlignment(Qt.AlignCenter)
        title.setObjectName("SectionTitle")
        self.image_grid.addWidget(title)

        time_label = QLabel(f"{timestamp.toString('yyyy-MM-dd HH:mm:ss')} UTC")
        time_label.setAlignment(Qt.AlignCenter)
        time_label.setObjectName("Hint")
        self.image_grid.addWidget(time_label)

        # Fullscreen image
        pixmap = frame_data.get(nickname)
        self.zoom_controls.setVisible(bool(self.frames))

        # Apply difference map if active
        if self.diff_type != "None" and pixmap:
            if self.current_frame > 0:
                # Difference for subsequent frames
                ref_idx = 0 if self.diff_type == "Base" else self.current_frame - 1
                ref_data = self.frames.get(ref_idx, {})
                ref_pixmap = ref_data.get(nickname)
                if ref_pixmap:
                    pixmap = self._calculate_difference(pixmap, ref_pixmap)
            else:
                # Neutral Gray for first frame (representing zero change)
                gray_pixmap = QPixmap(pixmap.size())
                gray_pixmap.fill(QColor(128, 128, 128))
                pixmap = gray_pixmap

        img_label = InteractiveImageLabel(scroll_area=self.image_scroll)
        img_label.crop_mode = self.crop_mode
        self.selection_label = img_label if self.crop_mode else None

        img_label.setAlignment(Qt.AlignCenter)
        waiting = None

        if pixmap:
            # Display scaled pixmap
            scaled_size = pixmap.size() * self.zoom_factor
            scaled_pixmap = pixmap.scaled(
                scaled_size, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            img_label.setPixmap(scaled_pixmap)
            img_label.zoom_factor = self.zoom_factor

        else:
            error = self.failed_downloads.get((self.current_frame, nickname))
            if error:
                img_label.setText("Could not download this frame")
                img_label.setToolTip(error)
            elif self._loading and nickname in self._loading_names:
                waiting = ActivityLabel()
                waiting.start("Downloading this frame…")
            else:
                img_label.setText("No data loaded for this instrument")
            img_label.setObjectName("Hint")
            img_label.setMargin(60)

        # Add with stretch to center vertically
        if waiting is not None:
            self.image_grid.addWidget(waiting, 1, Qt.AlignCenter)
        else:
            self.image_grid.addWidget(img_label, 1)

        # Update frame label
        self.frame_label.setText(
            f"Frame {self.current_frame + 1} / {len(self.timestamps)}"
        )
        
        # Update seekbar position
        if self.timestamps and len(self.timestamps) > 1:
            target_val = round(self.current_frame * 10000 / (len(self.timestamps) - 1))
            
            if self.playing:
                # Smooth slide animation during playback
                self.seekbar_anim.stop()
                
                # Get current interval for duration
                speed_text = self.speed_combo.currentText()
                speed = float(speed_text.replace("x", ""))
                interval_ms = int(0.5 * 1000 / speed)
                
                # If we just looped back to 0, jump instantly to avoid slow reverse slide
                if self.current_frame == 0:
                    self._update_seekbar_smoothly(0)
                else:
                    self.seekbar_anim.setDuration(interval_ms)
                    self.seekbar_anim.setStartValue(self.seekbar.value())
                    self.seekbar_anim.setEndValue(target_val)
                    self.seekbar_anim.start()
            else:
                # Instant jump if manually seeking or not playing
                self._update_seekbar_smoothly(target_val)
        else:
            self._update_seekbar_smoothly(0)


    '''def show_full_image(self, pixmap, title):
        """Show full resolution image in dialog."""
        dialog = FullImageDialog(self, pixmap, title)
        dialog.exec_()'''

    # Animation controls
    def first_frame(self):
        """Jump to first frame."""
        self.current_frame = 0
        self.display_current_frame()

    def prev_frame(self):
        """Go to previous frame."""
        if self.current_frame > 0:
            self.current_frame -= 1
            self.display_current_frame()

    def next_frame(self):
        """Go to next frame."""
        if self.current_frame < len(self.timestamps) - 1:
            self.current_frame += 1
            self.display_current_frame()
        else:
            # Loop back to start
            self.current_frame = 0
            self.display_current_frame()

    def last_frame(self):
        """Jump to last frame."""
        if self.timestamps:
            self.current_frame = len(self.timestamps) - 1
            self.display_current_frame()

    def toggle_play(self):
        """Toggle animation playback."""
        if not self.timestamps:
            return
        if not self.playing:
            self.playing = True
            self.play_btn.setIcon(transport_icon("pause", theme_manager.palette))
            self.play_btn.setToolTip("Pause (P / Space)")

            # Get speed
            speed_text = self.speed_combo.currentText()
            speed = float(speed_text.replace("x", ""))
            interval_ms = int(0.5 * 1000 / speed)  # Base: 2 fps

            self.animation_timer.start(interval_ms)
        else:
            self.pause_animation()

    def pause_animation(self):
        """Pause animation."""
        self.playing = False
        self.play_btn.setIcon(transport_icon("play", theme_manager.palette))
        self.play_btn.setToolTip("Play (P / Space)")
        self.animation_timer.stop()

    # Difference methods
    def on_diff_mode_changed(self):
        """Handle difference mode change and refresh display."""
        with busy("Updating the image…", self):
            self.diff_type = self.diff_combo.currentText()
            self.display_current_frame()

    def cycle_diff_mode(self):
        """Cycle through difference modes."""
        count = self.diff_combo.count()
        idx = (self.diff_combo.currentIndex() + 1) % count
        self.diff_combo.setCurrentIndex(idx)

    def increase_speed(self):
        """Increase animation speed."""
        idx = self.speed_combo.currentIndex()
        if idx < self.speed_combo.count() - 1:
            self.speed_combo.setCurrentIndex(idx + 1)

    def decrease_speed(self):
        """Decrease animation speed."""
        idx = self.speed_combo.currentIndex()
        if idx > 0:
            self.speed_combo.setCurrentIndex(idx - 1)

    def setup_shortcuts(self):
        """Define keyboard shortcuts for common actions."""
        # Playback
        QShortcut(QKeySequence("P"), self).activated.connect(self.toggle_play)
        QShortcut(QKeySequence("Space"), self).activated.connect(self.toggle_play)
        
        # Navigation
        QShortcut(QKeySequence("]"), self).activated.connect(self.next_frame)
        QShortcut(QKeySequence("["), self).activated.connect(self.prev_frame)
        QShortcut(QKeySequence("Shift+]"), self).activated.connect(self.last_frame)
        QShortcut(QKeySequence("Shift+["), self).activated.connect(self.first_frame)
        
        # Zoom
        QShortcut(QKeySequence("+"), self).activated.connect(self.zoom_in)
        QShortcut(QKeySequence("="), self).activated.connect(self.zoom_in)
        QShortcut(QKeySequence("-"), self).activated.connect(self.zoom_out)
        QShortcut(QKeySequence("R"), self).activated.connect(self.reset_zoom)
        
        # Speed
        QShortcut(QKeySequence(">"), self).activated.connect(self.increase_speed)
        QShortcut(QKeySequence("."), self).activated.connect(self.increase_speed)
        QShortcut(QKeySequence("<"), self).activated.connect(self.decrease_speed)
        QShortcut(QKeySequence(","), self).activated.connect(self.decrease_speed)
        
        # Image / Diff
        QShortcut(QKeySequence("D"), self).activated.connect(self.cycle_diff_mode)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self.save_current_frame)
        QShortcut(QKeySequence("Ctrl+E"), self).activated.connect(self.export_animation)

        # Close
        QShortcut(QKeySequence("Ctrl+Q"), self).activated.connect(self.close)

    def _calculate_difference(self, current_pixmap, reference_pixmap):
        """Calculate pixel-wise grayscale difference between two pixmaps."""
        if not current_pixmap or not reference_pixmap:
            return current_pixmap

        # Only large frames take long enough to say so (and while playing,
        # the frames moving on say it)
        if self.playing or current_pixmap.width() * current_pixmap.height() < 2500 ** 2:
            result = difference_image(current_pixmap.toImage(), reference_pixmap.toImage())
        else:
            with busy("Working out the difference…", self):
                result = difference_image(current_pixmap.toImage(), reference_pixmap.toImage())
        return QPixmap.fromImage(result) if result is not None else current_pixmap

    # Zoom controls
    def _show_zoomed(self):
        """Show the frame at the new zoom: scaling a large one takes a while."""
        if self.image_size_spin.value() * self.zoom_factor > 3000:
            with busy("Scaling the image…", self):
                self.display_current_frame()
        else:
            self.display_current_frame()

    def zoom_in(self):
        """Increase zoom factor."""
        self.zoom_factor *= 1.25
        if self.zoom_factor > 10.0:
            self.zoom_factor = 10.0
        self._show_zoomed()

    def zoom_out(self):
        """Decrease zoom factor."""
        self.zoom_factor /= 1.25
        if self.zoom_factor < 0.1:
            self.zoom_factor = 0.1
        self._show_zoomed()

    def reset_zoom(self):
        """Reset zoom factor to 1.0."""
        self.zoom_factor = 1.0
        self._show_zoomed()

    def toggle_crop_mode(self):
        """Enable/disable crop mode and update UI."""
        if not self.frames:
            return

        self.crop_mode = not self.crop_mode
        self.crop_btn.setVisible(not self.crop_mode)
        self.confirm_crop_btn.setVisible(self.crop_mode)
        self.cancel_crop_btn.setVisible(self.crop_mode)

        # Pause animation while cropping
        if self.crop_mode and self.playing:
            self.pause_animation()

        self.display_current_frame()

    def confirm_crop(self):
        """Apply the crop to all loaded frames."""
        if not self.selection_label:
            return

        crop_rect = self.selection_label.get_selection_rect()
        if crop_rect.isEmpty():
            QMessageBox.warning(self, "No selection", "Draw a rectangle to crop.")
            return

        # Apply crop to all frames
        total = len(self.frames)
        with busy(f"Cropping {total} frames…", self) as task:
            for done, frame_data in enumerate(self.frames.values(), 1):
                for inst_name, pixmap in frame_data.items():
                    if pixmap:
                        frame_data[inst_name] = pixmap.copy(crop_rect)
                task.update(done=done, total=total)

        QMessageBox.information(
            self, "Crop applied", f"Cropped {len(self.frames)} frames."
        )

        # Exit crop mode
        self.toggle_crop_mode()

    def cancel_crop(self):
        """Exit crop mode without changes."""
        self.toggle_crop_mode()

    def closeEvent(self, event):
        """Handle window close."""
        # An export under way goes first: its dialog says it is stopping
        dialog = self.findChild(WorkDialog)
        if dialog is not None and dialog.isVisible():
            self._close_after_export = True
            dialog.reject()
            event.ignore()
            return

        # Set closing flag first to prevent signal handlers from updating UI
        self._closing = True

        # Clear download queue first to prevent new downloads from starting
        self.download_queue.clear()

        # Requests cannot be interrupted, and take up to their timeout: the
        # window does not wait for them
        for loader in self.frame_loaders:
            self._abandon(loader)
        self.frame_loaders.clear()
        _park(self.instrument_loader)  # (its result finds _closing set)

        self.pause_animation()
        event.accept()


def main():
    import sys

    from .ui.app import create_app

    app = create_app()
    window = HelioviewerBrowser()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

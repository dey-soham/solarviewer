import logging
import os
import threading

import numpy as np

import matplotlib

matplotlib.use("Qt5Agg")
import matplotlib.pyplot as plt
import matplotlib.style as mplstyle
from matplotlib import rcParams

from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QAction,
    QMessageBox,
    QHBoxLayout,
    QVBoxLayout,
    QPushButton,
    QDialogButtonBox,
    # QPlainTextEdit,
    # QListWidget,
    QSplitter,
    QScrollArea,
    QFrame,
)
from PyQt5.QtCore import Qt, QEventLoop, QSettings, QTimer, pyqtSignal, QThread
from PyQt5.QtGui import QIcon

from .ui.styles import (
    theme_manager,
    set_hand_cursor,
    set_tone,
)
from .ui.app import apply_theme
from .ui.geometry import fit_to_screen
from .ui.icons import get_resource_path, themed_icon
from .ui.document_tabs import DocumentTabs
from .utils.update_checker import check_for_updates
from .version import __version__
from .tools import (
    RegionMode,
    RoiStatsMixin,
    RulerMixin,
    ProfileMixin,
    TbModeMixin,
    AnnotationsMixin,
    AnnotationDialogsMixin,
    FittingMixin,
    HpcViewMixin,
    SolarDiskMixin,
    ImageStatsMixin,
    SolarEventsMixin,
)
from .core.presets import display_range
from .processing.colormaps import ensure_solar_colormaps
from .processing.fits_io import SPECTRUM, fits_kind
from .core import (
    CanvasMixin,
    ContoursMixin,
    FileNavigationMixin,
    PresetsMixin,
    RenderingMixin,
    ExportsMixin,
    ExternalToolsMixin,
    PresetMenuMixin,
    RemoteModeMixin,
)
from .core.canvas import EMPTY_HINT_GID
from .core.figure_toolbar import FigureToolbarMixin
from .core.help import HelpMixin
from .core.loading import LoadingMixin
from .core.menus import MenusMixin
from .core.panels import PanelsMixin
from .core.window_dialogs import WindowDialogsMixin
from .ui import background
from .ui.background import Cancelled
from .ui.busy import busy
from .processing.contour_layers import default_contour_options, new_contour_layer
from .processing.prefetch import image_prefetcher
from .processing.image_data import ImageUnreadable
from .ui.errors import show_error
from .processing.plot_style import (
    DEFAULT_BEAM_STYLE,
    DEFAULT_GRID_STYLE,
    DEFAULT_PLOT_SETTINGS,
    DEFAULT_SOLAR_DISK_STYLE,
    default_style,
)

logger = logging.getLogger(__name__)


def _preload_display_modules():
    """Import modules that drawing the first image otherwise waits ~1 s for:
    astropy.wcs (plot axes) and sunpy's coordinates (solar disk size)."""
    try:
        import astropy.wcs  # noqa: F401
        import sunpy.coordinates.sun  # noqa: F401
    except Exception:
        pass


class CheckUpdateThread(QThread):
    """Background thread to check for updates on PyPI."""

    update_available = pyqtSignal(str)  # Emits latest version string

    def run(self):
        try:
            is_available, latest_ver, error = check_for_updates(__version__)
            if is_available and latest_ver:
                self.update_available.emit(latest_ver)
        except Exception:
            pass  # Silently fail for updates


def update_matplotlib_theme():
    """Update matplotlib rcParams based on current theme."""
    rcParams.update(theme_manager.matplotlib_params)


# Initialize matplotlib with default theme
rcParams["axes.linewidth"] = 1.4
rcParams["font.size"] = 12
update_matplotlib_theme()
mplstyle.use("fast")


class SolarRadioImageTab(
    PanelsMixin,
    FigureToolbarMixin,
    LoadingMixin,
    RenderingMixin,
    CanvasMixin,
    ContoursMixin,
    FileNavigationMixin,
    PresetsMixin,
    RoiStatsMixin,
    RulerMixin,
    ProfileMixin,
    TbModeMixin,
    AnnotationsMixin,
    HpcViewMixin,
    SolarDiskMixin,
    ImageStatsMixin,
    SolarEventsMixin,
    QWidget,
):
    def __init__(self, parent=None, tab_name=""):
        super().__init__(parent)
        self.setObjectName(tab_name)

        # Register theme change callback for icon updates
        theme_manager.register_callback(self._on_theme_change)

        self.stokes_combo = None
        self.current_image_data = None
        self.current_wcs = None
        self.psf = None
        self.current_roi = None
        self.roi_selector = None
        self.imagename = None
        self.solar_disk_center = None
        self.solar_disk_diameter_arcmin = 32.0
        self.solar_disk_auto_compute = True
        # Solar disk style properties
        self.solar_disk_style = default_style(DEFAULT_SOLAR_DISK_STYLE)

        # Annotations (tracked for remove/clear support)
        self.shape_annotations = []
        self.text_annotations = []
        self.arrow_annotations = []

        # Beam style properties
        self.beam_style = default_style(DEFAULT_BEAM_STYLE)

        # Grid style properties
        self.grid_style = default_style(DEFAULT_GRID_STYLE)

        # Initialize RMS box values
        self.current_rms_box = [0, 200, 0, 130]

        # File navigation state
        self._file_list = []  # List of file paths in current directory
        self._file_list_index = -1  # Current position in file list
        self._file_filter_pattern = "*"  # Glob pattern for filtering
        self._file_base_dir = None  # Base directory of loaded file

        # Remote file navigation state
        self._remote_file_list = []  # List of RemoteFileInfo for remote navigation
        self._remote_base_dir = None  # Remote directory path
        self._is_remote_mode = False  # Whether current file is from remote

        # Ruler/distance measurement state
        self._ruler_mode = False
        self._ruler_start = None  # (x, y) pixel coords
        self._ruler_line = None  # matplotlib line object
        self._ruler_text = None  # matplotlib text annotation

        # Profile cut tool state
        self._profile_mode = False
        self._profile_method = "line"  # "line" or "radial"
        self._profile_start = None  # (x, y) pixel coords
        self._profile_line = None  # matplotlib line object
        self._profile_crosshairs = []  # crosshair line objects
        self._profile_preview_line = None  # live preview line

        # Brightness temperature map state
        self._tb_mode = False  # Currently showing TB map?
        self._tb_original_imagename = None  # Original file before TB/Flux conversion
        self._tb_original_unit = None  # Original unit before conversion
        self._tb_temp_file = None  # Path to TB/Flux temp file
        self._original_flux_data = None  # Store original flux data when in TB mode
        self._original_bunit = None  # Store original unit

        # HPC (Helioprojective) conversion state
        self._original_imagename = None  # Original RA/Dec file before HPC conversion
        self._hpc_temp_file = None  # Path to HPC temp file
        self._hpc_original_imagename = (
            None  # Original HPC file before RA/Dec conversion
        )
        self._radec_temp_file = None  # Path to RA/Dec temp file (from HPC->RA/Dec)

        # Coordinate-transformed copies of contour images, by source path
        self._contour_conversions = {}

        # Track non-modal dialogs to prevent garbage collection
        self._open_dialogs = []

        # Unique ID for temp file naming (prevents collisions in multi-tab);
        # it carries the process id, so leftovers of closed sessions can be
        # told apart (processing.temp_files)
        from .processing.temp_files import session_id

        self._temp_file_id = session_id()

        # Image metadata cache for lazy WCS/colorbar computation
        # These are expensive to compute and only need to refresh when imagename changes
        self._cached_imagename = None  # Track which image is cached
        self._cached_fits_header = None  # FITS header cache
        self._cached_fits_flag = False  # Whether current image is FITS
        self._cached_csys = None  # CASA coordsys cache
        self._cached_summary = None  # CASA summary cache
        self._cached_csys_record = None  # CASA csys record cache

        # Debounce timers for UI responsiveness
        self._gamma_debounce_timer = None
        self._later_timers = []  # see call_later

        # Contour layers (up to MAX_CONTOUR_LAYERS) and options shared by all
        self.contour_layers = [new_contour_layer()]
        self.contour_options = default_contour_options()

        # Plot customization settings
        self.plot_settings = default_style(DEFAULT_PLOT_SETTINGS)

        # Rendering optimization state
        self._last_rendered_state = {}
        self.image_plot = None

        # View navigation: zoom/pan glides, drag panning
        self._zoom_timer = QTimer(self)
        self._zoom_timer.timeout.connect(self._perform_zoom_step)
        self._view_anim = None
        self._is_panning = False

        # Default mode
        self.region_mode = RegionMode.PAN

        self.setup_ui()
        self._setup_file_navigation_shortcuts()
        self._set_button_cursors()

    def _set_button_cursors(self):
        """Set pointing hand cursor on all buttons and interactive widgets for better UX"""
        set_hand_cursor(self)

    def show_status_message(self, message, timeout=0, flush=True):
        """Helper method to show messages in the status bar

        ``flush`` puts the message on screen at once, before work that blocks;
        callers on every mouse move pass False, as the wait would slow them.
        """
        main_window = self.window()
        try:
            if main_window:
                main_window.statusBar().showMessage(message, timeout)
                if flush:
                    QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
        except AttributeError:
            pass

    def setup_ui(self):
        """The top bar over the canvas and its toolbar, the inspector at
        the right; the empty canvas until an image is loaded."""
        from PyQt5.QtWidgets import QStackedWidget

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        self.create_top_bar(main_layout)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setChildrenCollapsible(False)

        # The canvas (or the empty state) with its toolbar under it
        column = QWidget()
        column_layout = QVBoxLayout(column)
        column_layout.setContentsMargins(0, 0, 0, 0)
        column_layout.setSpacing(0)
        self._canvas_stack = QStackedWidget()
        self._canvas_stack.addWidget(self.create_empty_canvas())
        canvas_host = QWidget()
        host_layout = QVBoxLayout(canvas_host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(0)
        self.setup_canvas(host_layout)
        self._canvas_stack.addWidget(canvas_host)
        column_layout.addWidget(self._canvas_stack, 1)
        self.setup_figure_toolbar(column_layout)
        column.setMinimumWidth(400)
        splitter.addWidget(column)

        inspector = self.create_inspector()
        inspector.setMinimumWidth(inspector.fontMetrics().averageCharWidth() * 40)
        splitter.addWidget(inspector)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        width = inspector.fontMetrics().averageCharWidth() * 48
        splitter.setSizes([max(800, 1400 - width), width])

        main_layout.addWidget(splitter, 1)
        self._name_icon_only_controls()

    def _name_icon_only_controls(self):
        """Name the icon-only buttons and toolbar actions for screen readers,
        from the start of their tooltips ("Pan: drag ..." -> "Pan")."""
        from PyQt5.QtWidgets import QAbstractButton

        def short(tip):
            return tip.split("\n")[0].split(":")[0].split(" - ")[0].strip()

        for button in self.findChildren(QAbstractButton):
            if not button.text() and not button.accessibleName() and button.toolTip():
                button.setAccessibleName(short(button.toolTip()))
        for action in self.findChildren(QAction):
            if not action.text() and action.toolTip():
                action.setText(short(action.toolTip()))

    def update_tab_name_from_path(self, path):
        """Update the tab name to the basename of the given path"""
        if path:
            basename = os.path.basename(
                path.rstrip("/")
            )  # Remove trailing slash for directories

            # Get the main window
            main_window = self.window()
            if isinstance(main_window, SolarRadioImageViewerApp):
                # Get the tab widget
                tab_widget = main_window.tab_widget
                # Find our index in the tabs list
                try:
                    index = main_window.tabs.index(self)
                    tab_widget.setTabText(index, basename)
                    main_window.update_window_title()
                except ValueError:
                    pass  # Not found in tabs list

    def schedule_plot(self):
        # If a timer already exists and is active, stop it.
        if hasattr(self, "_plot_timer") and self._plot_timer.isActive():
            self._plot_timer.stop()
        else:
            self._plot_timer = QTimer(self)
            self._plot_timer.setSingleShot(True)
            # Use a lambda to call plot_image with current parameters.
            self._plot_timer.timeout.connect(
                lambda: self.plot_image(
                    float(self.vmin_entry.text()),
                    float(self.vmax_entry.text()),
                    self.stretch_combo.currentText(),
                    self.cmap_combo.currentText(),
                    float(self.gamma_entry.text()),
                )
            )
        self._plot_timer.start(50)  # 10ms delay

    def plot_data(self):
        self.on_visualization_changed()

    def update_display(self, colormap_name=None):
        """
        Fast visualization update - only redraws without reloading data.
        Use this for colormap, stretch, gamma, vmin/vmax changes.
        """
        if self.current_image_data is None:
            return
        self.on_visualization_changed(colormap_name=colormap_name, reload_data=False)

    def on_visualization_changed(
        self, colormap_name=None, dir_load=False, reload_data=True
    ):
        """
        Handle visualization parameter changes.

        Args:
            colormap_name: Optional colormap to use
            dir_load: Whether this is a directory load (applies presets)
            reload_data: If False, skip load_data and just redraw (faster for viz-only changes)

        Returns True once shown, False if loading or drawing failed (after
        saying why), None if the user cancelled loading.
        """
        if not hasattr(self, "imagename") or not self.imagename:
            QMessageBox.warning(self, "No Image", "Please open an image first.")
            return False

        with busy():
            return self._update_visualization(colormap_name, dir_load, reload_data)

    def _update_visualization(self, colormap_name, dir_load, reload_data):
        """on_visualization_changed's work, run with the wait cursor."""
        tight_layout = False  # (the layout itself is decided in _apply_layout_settings)

        main_window = self.parent()
        # if main_window and hasattr(main_window, "statusBar"):
        if main_window:
            if reload_data:
                name = os.path.basename(self.imagename.rstrip("/"))
                self.show_status_message(f"Loading {name}…")
            else:
                self.show_status_message("Updating display...")

        stokes = self.stokes_combo.currentText() if self.stokes_combo else "I"
        try:
            threshold = float(self.threshold_entry.text())
        except (ValueError, AttributeError):
            threshold = 10.0
            if hasattr(self, "threshold_entry"):
                self.threshold_entry.setText("10.0")

        try:
            try:
                vmin_val = float(self.vmin_entry.text())
                vmax_val = float(self.vmax_entry.text())
            except (ValueError, AttributeError):
                vmin_val = None
                vmax_val = None

            try:
                gamma = float(self.gamma_entry.text())
            except (ValueError, AttributeError):
                gamma = 1.0
                if hasattr(self, "gamma_entry"):
                    self.gamma_entry.setText("1.0")

            stretch = (
                self.stretch_combo.currentText()
                if hasattr(self, "stretch_combo")
                else "linear"
            )

            cmap = "viridis"
            requested = colormap_name or getattr(
                getattr(self, "cmap_combo", None), "currentText", lambda: ""
            )()
            if requested and requested not in plt.colormaps():
                ensure_solar_colormaps()
            if colormap_name and colormap_name in plt.colormaps():
                cmap = colormap_name
                self.cmap_combo.setCurrentText(cmap)
            elif hasattr(self, "cmap_combo"):
                cmap_text = self.cmap_combo.currentText()
                if cmap_text in plt.colormaps():
                    cmap = cmap_text
                else:
                    matches = [
                        cm for cm in plt.colormaps() if cmap_text.lower() in cm.lower()
                    ]
                    if matches:
                        cmap = matches[0]
                        self.cmap_combo.setCurrentText(cmap)
                    else:
                        print(
                            f"[WARNING] {cmap_text} colormap not available, using default"
                        )
                        if main_window:
                            self.show_status_message(
                                f"WARNING: {cmap_text} colormap not available, using default"
                            )
                        self.cmap_combo.setCurrentText("viridis")

            # Only reload data if needed (new image, stokes change, etc.)
            # Skip for visualization-only changes (colormap, stretch, gamma, vmin/vmax)
            if reload_data:
                if not dir_load and getattr(self, "_range_stokes", stokes) != stokes:
                    # The Min/Max boxes hold the range of the previously shown
                    # Stokes product, which means nothing for this one
                    vmin_val = vmax_val = None
                # A new image lacking the selected product shows Stokes I
                stokes = self.load_data(
                    self.imagename,
                    stokes,
                    threshold,
                    auto_adjust_rms=dir_load,
                    fallback_stokes="I" if dir_load else None,
                )
                self._range_stokes = stokes
            if dir_load:
                mw = self.window()
                auto_presets = (
                    hasattr(mw, "auto_preset_act") and mw.auto_preset_act.isChecked()
                )
                is_preset_matched = False

                if auto_presets:
                    is_preset_matched = self._auto_apply_instrument_preset()

                if not is_preset_matched:
                    # Reset to defaults if the previous load was a preset
                    if getattr(self, "_was_preset_image", False):
                        if hasattr(self, "cmap_combo"):
                            self.cmap_combo.blockSignals(True)
                            self.cmap_combo.setCurrentText("viridis")
                            self.cmap_combo.blockSignals(False)
                        if hasattr(self, "stretch_combo"):
                            self.stretch_combo.blockSignals(True)
                            self.stretch_combo.setCurrentText("linear")
                            self.stretch_combo.blockSignals(False)
                        if hasattr(self, "gamma_entry"):
                            self.gamma_entry.blockSignals(True)
                            self.gamma_entry.setText("1.0")
                            self.gamma_entry.blockSignals(False)
                        cmap = "viridis"
                        stretch = "linear"
                        gamma = 1.0

                    vmin_val = float(np.nanmin(self.current_image_data))
                    vmax_val = float(np.nanmax(self.current_image_data))
                    self.set_range(vmin_val, vmax_val)
                    # Only call plot_image here - preset methods already call it internally
                    self.plot_image(
                        vmin_val,
                        vmax_val,
                        stretch,
                        cmap,
                        gamma,
                        tight_layout=tight_layout,
                        preserve_view=not dir_load,
                    )

                self._was_preset_image = is_preset_matched
            elif vmin_val is None or vmax_val is None:
                self.apply_auto_range("minmax")
            else:
                self.plot_image(
                    vmin_val,
                    vmax_val,
                    stretch,
                    cmap,
                    gamma,
                    tight_layout=tight_layout,
                    preserve_view=not dir_load,
                )

            if main_window:
                if dir_load:
                    img_name = os.path.basename(self.imagename)
                    self.show_status_message(f"Loaded {img_name}")
                else:
                    self.show_status_message("Display updated")
            if dir_load:
                # After the caller has rescanned the directory
                QTimer.singleShot(0, self._prefetch_neighbour_files)
            return True
        except Exception as e:
            import traceback

            name = os.path.basename(str(self.imagename).rstrip("/"))
            if dir_load:
                # Show no image rather than the last one under this one's name
                self.current_image_data = None
                self.figure.clear()
                self.show_empty_hint()
                self.canvas.draw_idle()
            if isinstance(e, Cancelled):
                if main_window:
                    self.show_status_message(f"Loading {name} cancelled")
                return None
            if main_window:
                self.show_status_message(f"Could not show {name}")
            if isinstance(e, ImageUnreadable):
                message = (
                    f"{name} could not be read.\n\nCheck that it is a FITS file "
                    "or a CASA image, and that it is complete (not still being "
                    "copied or downloaded)."
                )
            else:
                message = (
                    f"Something went wrong loading or drawing {name}:\n\n{e}\n\n"
                    "The details below, and the log (View > Show Log Console), "
                    "say more."
                )
            show_error(self, "Could Not Show Image", message, traceback.format_exc())
            return False

    def set_range(self, vmin_val, vmax_val):
        if self.current_image_data is None:
            return

        data = self.current_image_data
        finite = np.isfinite(data).any()
        dmin = float(np.nanmin(data)) if finite else float("nan")
        dmax = float(np.nanmax(data)) if finite else float("nan")
        if not dmax > dmin:
            # Constant or fully masked map: don't keep the previous range
            low, high = display_range(dmin, dmax)
            self.show_range(low, high)
            return

        if vmin_val < dmin:
            vmin_val = dmin
        if vmax_val > dmax:
            vmax_val = dmax
        if vmax_val <= vmin_val:
            vmax_val = vmin_val + 1e-6

        self.show_range(vmin_val, vmax_val)

    def show_range(self, vmin_val, vmax_val):
        """Write the display range into the Min/Max boxes, showing each start."""
        for entry, value in ((self.vmin_entry, vmin_val), (self.vmax_entry, vmax_val)):
            entry.setText(f"{value:.6g}")
            # A long number would otherwise show its end, hiding sign and digits
            entry.setCursorPosition(0)
            entry.setToolTip(f"{value:.10g}")

    def _on_threshold_edited(self):
        """Reload the image if the threshold was changed."""
        if self.current_image_data is None:
            return
        try:
            threshold = float(self.threshold_entry.text())
        except ValueError:
            return
        if threshold != getattr(self, "_loaded_threshold", None):
            self.on_visualization_changed()

    def update_gamma_value(self):
        """Update gamma from slider - debounced for responsiveness."""
        gamma = self.gamma_slider.value() / 20.0
        self.gamma_entry.setText(f"{gamma:.1f}")

        # Only update plot if using power stretch
        if (
            self.current_image_data is not None
            and self.stretch_combo.currentText() == "power"
        ):
            # Use debouncing to avoid multiple redraws while dragging
            if self._gamma_debounce_timer is None:
                from PyQt5.QtCore import QTimer

                self._gamma_debounce_timer = QTimer(self)
                self._gamma_debounce_timer.setSingleShot(True)
                self._gamma_debounce_timer.timeout.connect(self._apply_gamma_change)

            # Only update the plot immediately if NOT dragging (e.g. keyboard or track click)
            # If dragging, we wait for sliderReleased
            if not self.gamma_slider.isSliderDown():
                # Restart the timer (30ms delay)
                self._gamma_debounce_timer.start(30)

    def _apply_gamma_change(self):
        """Actually apply the gamma change after debounce delay."""
        if self.current_image_data is None:
            return
        try:
            gamma = float(self.gamma_entry.text())
            vmin_val = float(self.vmin_entry.text())
            vmax_val = float(self.vmax_entry.text())
            stretch = self.stretch_combo.currentText()
            cmap = self.cmap_combo.currentText()
            self.plot_image(vmin_val, vmax_val, stretch, cmap, gamma)
        except ValueError:
            pass

    def update_gamma_slider(self):
        try:
            gamma = float(self.gamma_entry.text())
            if 0.1 <= gamma <= 3.0:
                self.gamma_slider.blockSignals(True)
                self.gamma_slider.setValue(int(gamma * 20))
                self.gamma_slider.blockSignals(False)

                if (
                    self.current_image_data is not None
                    and self.stretch_combo.currentText() == "power"
                ):
                    try:
                        vmin_val = float(self.vmin_entry.text())
                        vmax_val = float(self.vmax_entry.text())
                        stretch = self.stretch_combo.currentText()
                        cmap = self.cmap_combo.currentText()
                        self.plot_image(vmin_val, vmax_val, stretch, cmap, gamma)
                    except ValueError:
                        pass
        except ValueError:
            self.gamma_entry.setText("1.0")
            self.gamma_slider.setValue(20)

    def on_stretch_changed(self, index):
        self.update_gamma_slider_state()
        stretch = self.stretch_combo.currentText()
        # The plot reads the range, stretch and gamma from the controls
        if self.current_image_data is not None:
            self.schedule_plot()
        main_window = self.parent()
        if main_window:
            self.show_status_message(f"Changed stretch to {stretch}")

    def update_gamma_slider_state(self):
        is_power = self.stretch_combo.currentText() == "power"
        self.gamma_slider.setEnabled(is_power)
        self.gamma_entry.setEnabled(is_power)

    def _on_theme_change(self, theme):
        """Handle theme change - update icons"""
        from .ui.styles import get_icon_path

        # Update ruler icon
        if hasattr(self, "ruler_action"):
            self.ruler_action.setIcon(
                QIcon(get_resource_path(f"assets/{get_icon_path('ruler.png')}"))
            )

        # Update profile icon
        if hasattr(self, "profile_action"):
            self.profile_action.setIcon(
                QIcon(get_resource_path(f"assets/{get_icon_path('profile.png')}"))
            )

        # Update beam settings button icon
        if hasattr(self, "beam_settings_button"):
            self.beam_settings_button.setIcon(
                QIcon(get_resource_path(f"assets/{get_icon_path('settings.png')}"))
            )

        # Update grid settings button icon
        if hasattr(self, "grid_settings_button"):
            self.grid_settings_button.setIcon(
                QIcon(get_resource_path(f"assets/{get_icon_path('settings.png')}"))
            )

        # Update gamma slider disabled styling for new theme
        if hasattr(self, "gamma_slider") and hasattr(self, "stretch_combo"):
            self.update_gamma_slider_state()

    def on_checkbox_changed(self):
        if not hasattr(self, "current_image_data") or self.current_image_data is None:
            return

        # Determine which checkbox was changed
        sender = self.sender()
        if sender == self.show_beam_checkbox:
            status = "enabled" if self.show_beam_checkbox.isChecked() else "disabled"
            self.show_status_message(f"Beam display {status}")
        elif sender == self.show_grid_checkbox:
            status = "enabled" if self.show_grid_checkbox.isChecked() else "disabled"
            self.show_status_message(f"Grid display {status}")
        elif sender == self.show_solar_disk_checkbox:
            status = (
                "enabled" if self.show_solar_disk_checkbox.isChecked() else "disabled"
            )
            self.show_status_message(f"Solar disk display {status}")
        elif sender == self.show_contours_checkbox:
            status = (
                "enabled" if self.show_contours_checkbox.isChecked() else "disabled"
            )
            self.show_status_message(f"Contours display {status}")

        self.schedule_plot()

    def show_beam_settings(self):
        """Show non-modal beam settings dialog."""
        from .dialogs import BeamSettingsDialog

        # If dialog already exists and is visible, just raise it
        if (
            hasattr(self, "_beam_settings_dialog")
            and self._beam_settings_dialog is not None
        ):
            try:
                if self._beam_settings_dialog.isVisible():
                    self._beam_settings_dialog.raise_()
                    self._beam_settings_dialog.activateWindow()
                    return
            except RuntimeError:
                self._beam_settings_dialog = None

        dialog = BeamSettingsDialog(self, beam_style=self.beam_style)
        self._beam_settings_dialog = dialog

        # Set up for non-modal behavior
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(lambda: setattr(self, "_beam_settings_dialog", None))

        # Track dialog for garbage collection
        dialog.destroyed.connect(
            lambda: (
                self._open_dialogs.remove(dialog)
                if dialog in self._open_dialogs
                else None
            )
        )
        self._open_dialogs.append(dialog)

        dialog.show()

    def show_grid_settings(self):
        """Show non-modal grid settings dialog."""
        from .dialogs import GridSettingsDialog

        # If dialog already exists and is visible, just raise it
        if (
            hasattr(self, "_grid_settings_dialog")
            and self._grid_settings_dialog is not None
        ):
            try:
                if self._grid_settings_dialog.isVisible():
                    self._grid_settings_dialog.raise_()
                    self._grid_settings_dialog.activateWindow()
                    return
            except RuntimeError:
                self._grid_settings_dialog = None

        dialog = GridSettingsDialog(self, grid_style=self.grid_style)
        self._grid_settings_dialog = dialog

        # Set up for non-modal behavior
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(lambda: setattr(self, "_grid_settings_dialog", None))

        # Track dialog for garbage collection
        dialog.destroyed.connect(
            lambda: (
                self._open_dialogs.remove(dialog)
                if dialog in self._open_dialogs
                else None
            )
        )
        self._open_dialogs.append(dialog)

        dialog.show()

    def show_plot_customization_dialog(self):
        """Open a non-modal plot customization dialog."""
        from .dialogs import PlotCustomizationDialog

        # If dialog already exists and is visible, just raise it
        if (
            hasattr(self, "_plot_customization_dialog")
            and self._plot_customization_dialog is not None
        ):
            try:
                if self._plot_customization_dialog.isVisible():
                    self._plot_customization_dialog.raise_()
                    self._plot_customization_dialog.activateWindow()
                    return
            except RuntimeError:
                self._plot_customization_dialog = None

        dialog = PlotCustomizationDialog(self, self.plot_settings)
        self._plot_customization_dialog = dialog

        # Store reference to viewer
        viewer = self

        def on_apply():
            """Apply settings without closing dialog."""
            try:
                from PyQt5 import sip

                if sip.isdeleted(dialog):
                    return

                if not viewer:
                    try:
                        dialog.close()
                    except RuntimeError:
                        pass
                    return

                viewer.plot_settings = dialog.get_settings()
                # Refresh the plot with new settings
                if viewer.current_image_data is not None:
                    try:
                        vmin_val = float(viewer.vmin_entry.text())
                        vmax_val = float(viewer.vmax_entry.text())
                        stretch = viewer.stretch_combo.currentText()
                        cmap = viewer.cmap_combo.currentText()
                        gamma = float(viewer.gamma_entry.text())
                        viewer.plot_image(vmin_val, vmax_val, stretch, cmap, gamma)
                    except (ValueError, AttributeError):
                        viewer.plot_image()
                viewer.show_status_message("Plot settings applied")
            except RuntimeError:
                # Dialog was likely deleted during long-running plot update
                pass
            except Exception as e:
                if viewer:
                    viewer.show_status_message(f"Error applying settings: {e}")

        def on_close():
            dialog.close()

        def on_dialog_destroyed():
            if hasattr(viewer, "_plot_customization_dialog"):
                viewer._plot_customization_dialog = None

        # Replace the standard button box with Apply/Close
        button_box = dialog.findChild(QDialogButtonBox)
        if button_box:
            button_box.clear()
            apply_btn = button_box.addButton("Apply", QDialogButtonBox.ApplyRole)
            close_btn = button_box.addButton("Close", QDialogButtonBox.RejectRole)
            apply_btn.clicked.connect(on_apply)
            close_btn.clicked.connect(on_close)

        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(on_dialog_destroyed)

        # Track dialog for garbage collection
        dialog.destroyed.connect(
            lambda: (
                self._open_dialogs.remove(dialog)
                if dialog in self._open_dialogs
                else None
            )
        )
        self._open_dialogs.append(dialog)

        dialog.show()

    '''def _toggle_fullscreen(self):
        """Toggle fullscreen mode for the main window"""
        main_window = self.window()
        if main_window:
            if main_window.isFullScreen():
                main_window.showNormal()
            else:
                main_window.showFullScreen()
    '''

    def call_later(self, msec, function):
        """Call ``function`` in ``msec`` ms, unless the tab is closed first."""
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(function)
        timer.start(msec)
        self._later_timers.append(timer)

    def cleanup(self):
        """Release what the tab holds once it is closed: its timers, its
        temporary files (HPC, RA/Dec, TB and converted contour images) and
        its image."""
        for timer in (
            self._zoom_timer,
            getattr(self, "_settle_timer", None),
            getattr(self, "_plot_timer", None),
            self._gamma_debounce_timer,
            *self._later_timers,
        ):
            if timer is not None:
                timer.stop()
        for path in (self._hpc_temp_file, self._radec_temp_file, self._tb_temp_file):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        self._hpc_temp_file = self._radec_temp_file = self._tb_temp_file = None
        self.remove_contour_conversions()
        self.figure.clear()
        self.image_plot = None
        self.current_image_data = None
        self._cached_transposed = None
        theme_manager.unregister_owner(self)

    def closeEvent(self, event):
        self.cleanup()
        super().closeEvent(event)

    def refresh_icons(self):
        """Refresh all icons to match the current theme."""
        if hasattr(self, "browse_action"):
            self.browse_action.setIcon(QIcon(themed_icon("browse.png")))
        if hasattr(self, "solar_disk_center_button"):
            self.solar_disk_center_button.setIcon(QIcon(themed_icon("settings.png")))
        if hasattr(self, "contour_settings_button"):
            self.contour_settings_button.setIcon(QIcon(themed_icon("settings.png")))
        if hasattr(self, "beam_settings_button"):
            self.beam_settings_button.setIcon(QIcon(themed_icon("settings.png")))
        if hasattr(self, "grid_settings_button"):
            self.grid_settings_button.setIcon(QIcon(themed_icon("settings.png")))

        # Update navigation buttons (if they exist in left panel)
        if hasattr(self, "zoom_in_button"):
            self.zoom_in_button.setIcon(QIcon(themed_icon("zoom_in.png")))
        if hasattr(self, "zoom_out_button"):
            self.zoom_out_button.setIcon(QIcon(themed_icon("zoom_out.png")))
        if hasattr(self, "reset_view_button"):
            self.reset_view_button.setIcon(QIcon(themed_icon("reset.png")))
        if hasattr(self, "zoom_60arcmin_button"):
            self.zoom_60arcmin_button.setIcon(QIcon(themed_icon("zoom_60arcmin.png")))

        # Update toolbar actions (in the figure toolbar)
        if hasattr(self, "zoom_in_action"):
            self.zoom_in_action.setIcon(QIcon(themed_icon("zoom_in.png")))
        if hasattr(self, "zoom_out_action"):
            self.zoom_out_action.setIcon(QIcon(themed_icon("zoom_out.png")))
        if hasattr(self, "zoom_60arcmin_action"):
            self.zoom_60arcmin_action.setIcon(QIcon(themed_icon("zoom_60arcmin.png")))
        if hasattr(self, "reset_view_action"):
            self.reset_view_action.setIcon(QIcon(themed_icon("reset.png")))
        if hasattr(self, "pan_action"):
            self.pan_action.setIcon(QIcon(themed_icon("pan.png")))
        if hasattr(self, "rect_action"):
            self.rect_action.setIcon(QIcon(themed_icon("rectangle_selection.png")))
        if hasattr(self, "ellipse_action"):
            self.ellipse_action.setIcon(QIcon(themed_icon("ellipse_selection.png")))
        if hasattr(self, "info_action"):
            self.info_action.setIcon(QIcon(themed_icon("icons8-info-90.png")))
        if hasattr(self, "_filter_btn"):
            self._filter_btn.setIcon(QIcon(themed_icon("settings.png")))
        # Update search button in colormap selector
        if hasattr(self, "colormap_selector") and hasattr(
            self.colormap_selector, "search_button"
        ):
            self.colormap_selector.search_button.setIcon(
                QIcon(themed_icon("search.png"))
            )


class SolarRadioImageViewerApp(
    MenusMixin,
    HelpMixin,
    WindowDialogsMixin,
    AnnotationDialogsMixin,
    FittingMixin,
    ExportsMixin,
    ExternalToolsMixin,
    PresetMenuMixin,
    RemoteModeMixin,
    QMainWindow,
):
    def __init__(self, imagename=None, fast_preview=False):
        super().__init__()

        self._fast_preview = fast_preview

        # Initialize bundled fonts before any widgets are created
        theme_manager.initialize_fonts()

        self.setWindowTitle("SolarViewer")

        fit_to_screen(self, 1920, 1080)

        # The image tabs: their strip at the right end of the menu bar
        self.tab_widget = DocumentTabs()
        self.tab_widget.tabCloseRequested.connect(self.close_tab)
        self.tab_widget.currentChanged.connect(self.update_window_title)
        self.tab_widget.add_tab_button.clicked.connect(self.handle_add_tab)
        self.setCentralWidget(self.tab_widget)
        self.setMinimumSize(1100, 720)
        self.tabs = []
        self.settings = QSettings("SolarViewer", "SolarViewer")
        self._open_dialogs = []  # Track non-modal dialogs to prevent garbage collection

        # Remote mode state
        self.remote_connection = None  # SSHConnection when connected
        self.remote_cache = None  # RemoteFileCache for downloaded files

        # Remote connection status indicator in status bar (clickable)
        self.remote_status_btn = QPushButton("Not connected")
        self.remote_status_btn.setObjectName("StatusButton")
        self.remote_status_btn.setCursor(Qt.PointingHandCursor)
        self.remote_status_btn.setToolTip("Click to connect to a remote server")
        self.remote_status_btn.clicked.connect(self._on_remote_status_clicked)
        self.statusBar().addPermanentWidget(self.remote_status_btn)

        # Update Notification Button (Hidden by default)
        self.update_btn = QPushButton("Update available")
        self.update_btn.setObjectName("StatusButton")
        set_tone(self.update_btn, "success")
        self.update_btn.setCursor(Qt.PointingHandCursor)
        self.update_btn.setVisible(False)
        self.update_btn.clicked.connect(self._on_update_clicked)
        self.statusBar().addPermanentWidget(self.update_btn)

        # Start update check in background
        self._update_thread = CheckUpdateThread()
        self._update_thread.update_available.connect(self._on_update_available)
        self._update_thread.start()

        # Import what the first image will need while the user picks one
        threading.Thread(target=_preload_display_modules, daemon=True).start()
        # Start the process that reads images
        QTimer.singleShot(0, background.warm_up)

        # Register for theme changes
        theme_manager.register_callback(self._on_theme_changed)

        # Enable drag and drop
        self.setAcceptDrops(True)

        self.statusBar().showMessage("Ready")
        self.create_menus()
        self.menuBar().setCornerWidget(self.tab_widget.strip, Qt.TopRightCorner)

        # Set hand cursor for all interactive elements programmatically
        self._set_hand_cursor_recursive(self)

        first_tab = self.add_new_tab("Tab1")

        # Apply fast preview flag from CLI
        if self._fast_preview and hasattr(first_tab, "downsample_toggle"):
            first_tab.downsample_toggle.setChecked(True)

        if imagename and os.path.exists(imagename) and fits_kind(imagename) == SPECTRUM:
            # Once this window is up, in the Dynamic Spectrum Viewer
            QTimer.singleShot(100, lambda: self.open_spectrum(imagename))
        elif imagename and os.path.exists(imagename):
            first_tab.imagename = imagename
            first_tab.dir_entry.setText(imagename)

            # Load once the tab is set up (not if it is closed first)
            first_tab.call_later(
                20, lambda: first_tab.on_visualization_changed(dir_load=True)
            )
            first_tab.update_tab_name_from_path(imagename)
            # Scan directory for file navigation (delay to ensure UI is ready)
            first_tab.call_later(100, first_tab._scan_directory_files)
        """else:
            first_tab.imagename = get_resource_path("assets/splash.fits")
            QTimer.singleShot(
                20,
                lambda: first_tab.on_visualization_changed(
                    dir_load=True, colormap_name="inferno"
                ),
            )"""

    def dragEnterEvent(self, event):
        """Handle drag enter events for file dropping."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        """Handle drop events to load images/files."""
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()

            # Use QTimer to delay processing slightly so the drag operation completes properly
            from PyQt5.QtCore import QTimer

            QTimer.singleShot(10, lambda: self._process_dropped_urls(urls))

            event.acceptProposedAction()

    def _process_dropped_urls(self, urls):
        """Process dropped URLs and open them in tabs."""
        for i, url in enumerate(urls):
            if not url.isLocalFile():
                continue

            file_path = url.toLocalFile()
            if not os.path.exists(file_path):
                continue
            if fits_kind(file_path) == SPECTRUM:
                self.open_spectrum(file_path)
                continue

            # If there's only one tab and it's empty, use it. Otherwise add tab.
            current_tab = self.tab_widget.currentWidget()
            if current_tab and not current_tab.imagename and i == 0:
                target_tab = current_tab
            else:
                basename = os.path.basename(file_path.rstrip("/"))
                target_tab = self.add_new_tab(basename)

            target_tab.open_image_path(file_path)

    def _on_update_available(self, latest_ver):
        """Show the update button when an update is found."""
        self.update_btn.setText(f"Update available: {latest_ver}")
        self.update_btn.setToolTip(
            f"A new version ({latest_ver}) is available! Click to learn more."
        )
        self.update_btn.setVisible(True)
        self.latest_version = latest_ver

    def _on_update_clicked(self):
        """Show the software update dialog."""
        self.show_update_dialog()

    def close_tab(self, index):
        """Close the tab at the given index (the last one gives way to an
        empty tab)"""
        if index >= 0 and index < len(self.tabs):
            if len(self.tabs) == 1:
                self.add_new_tab("Tab1")
            tab = self.tabs.pop(index)
            self.tab_widget.removeTab(index)
            # A removed page is not deleted by Qt: free it and its image
            tab.cleanup()
            tab.deleteLater()

    def close_current_tab(self):
        """Close the currently active tab"""
        current_idx = self.tab_widget.currentIndex()
        self.close_tab(current_idx)

    def handle_add_tab(self):
        """Handle the add tab button click"""
        tab_count = len(self.tabs) + 1
        tab_name = f"Tab{tab_count}"
        self.add_new_tab(tab_name)

    def add_new_tab(self, name):
        new_tab = SolarRadioImageTab(self, name)
        self.tabs.append(new_tab)
        self.tab_widget.addTab(new_tab, name)
        self.tab_widget.setCurrentWidget(new_tab)

        # Refresh icons to match current theme
        new_tab.refresh_icons()

        # Apply hand cursors to the new tab
        self._set_hand_cursor_recursive(new_tab)
        return new_tab

    def update_window_title(self, *args):
        """Window title: the current tab's image, then the app name."""
        tab = self.tab_widget.currentWidget()
        if tab is not None and getattr(tab, "imagename", None):
            name = self.tab_widget.tabText(self.tab_widget.currentIndex())
            self.setWindowTitle(f"{name} — SolarViewer")
        else:
            self.setWindowTitle("SolarViewer")

    def toggle_theme(self):
        """Toggle between dark and light themes."""
        new_theme = theme_manager.toggle_theme()
        self.statusBar().showMessage(
            f"Switched to {'Light' if new_theme == 'light' else 'Dark'} mode"
        )

    def _toggle_fullscreen(self):
        """Toggle fullscreen mode for the main window."""
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _open_preferences_dialog(self):
        """Open the application preferences dialog (non-modal)."""
        # Reuse existing dialog or create new one
        if (
            hasattr(self, "_preferences_dialog")
            and self._preferences_dialog is not None
        ):
            try:
                self._preferences_dialog.raise_()
                self._preferences_dialog.activateWindow()
                return
            except RuntimeError:
                # Dialog was deleted
                self._preferences_dialog = None

        from .dialogs import PreferencesDialog

        self._preferences_dialog = PreferencesDialog(self)
        self._preferences_dialog.setAttribute(Qt.WA_DeleteOnClose)
        self._preferences_dialog.destroyed.connect(
            lambda: setattr(self, "_preferences_dialog", None)
        )
        self._preferences_dialog.show()

    def _on_theme_changed(self, new_theme):
        """Handle theme change events."""
        # Update matplotlib rcParams
        update_matplotlib_theme()

        # The tools' windows follow
        link = getattr(self, "_tool_link", None)
        if link is not None:
            link.broadcast({"theme": new_theme})

        # Update Qt application palette BEFORE refreshing icons
        self._update_application_palette()

        # Update theme action text
        self._update_theme_action_text()

        # Icons are pictures of the theme
        for tab in self.tabs:
            tab.refresh_icons()

        # Refresh tab bar theme (colors and icons)
        if hasattr(self, "tab_widget"):
            self.tab_widget.refresh_theme()

        # Refresh all matplotlib plots
        self.refresh_all_plots()

        # Re-apply hand cursors to any recreated or theme-affected widgets
        self._set_hand_cursor_recursive(self)

    def _set_hand_cursor_recursive(self, widget):
        """Recursively set PointingHandCursor for all buttons and interactive widgets."""
        set_hand_cursor(widget)


    def _update_theme_action_text(self):
        """Update theme toggle action text based on current theme."""
        if theme_manager.is_dark:
            self.theme_action.setText("Switch to Light Mode")
        else:
            self.theme_action.setText("Switch to Dark Mode")

    def _update_application_palette(self):
        """Restyle the application (palette and stylesheet) for the theme."""
        apply_theme(QApplication.instance())

    def refresh_all_plots(self):
        """Refresh all matplotlib plots to apply new theme colors."""
        palette = theme_manager.palette
        is_dark = theme_manager.is_dark

        # Use plot-specific colors for light mode, otherwise use standard palette
        if is_dark:
            fig_bg = palette["window"]
            axes_bg = palette["base"]
            text_color = palette["text"]
        else:
            fig_bg = palette.get("plot_bg", "#ffffff")
            axes_bg = palette.get("plot_bg", "#ffffff")
            text_color = palette.get("plot_text", "#1a1a1a")

        for tab in self.tabs:
            if hasattr(tab, "figure") and tab.figure:
                # Update figure background
                tab.figure.set_facecolor(fig_bg)
                for text in tab.figure.texts:
                    if text.get_gid() == EMPTY_HINT_GID:
                        text.set_color(text_color)

                # Update axes if present
                for ax in tab.figure.get_axes():
                    ax.set_facecolor(axes_bg)
                    ax.tick_params(colors=text_color)
                    ax.xaxis.label.set_color(text_color)
                    ax.yaxis.label.set_color(text_color)
                    ax.title.set_color(text_color)
                    for spine in ax.spines.values():
                        spine.set_color(text_color)

                    # Handle WCSAxes
                    # WCSAxes use ax.coords for coordinate handling instead of standard xaxis/yaxis
                    if hasattr(ax, "coords"):
                        try:
                            # Update axis labels and tick labels for each coordinate
                            for coord in ax.coords:
                                # Get current label text and set with new color
                                current_label = coord.get_axislabel()
                                if current_label:
                                    coord.set_axislabel(current_label, color=text_color)
                                coord.set_ticklabel(color=text_color)
                                coord.set_ticks(color=text_color)

                            # Update frame (border) color for WCSAxes
                            if hasattr(ax.coords, "frame"):
                                ax.coords.frame.set_color(text_color)
                        except Exception as e:
                            logger.error(f"Error updating WCSAxes colors: {e}")
                            self.show_status_message(
                                f"Error updating WCSAxes colors: {e}"
                            )

                # Redraw canvas
                if hasattr(tab, "canvas") and tab.canvas:
                    tab.canvas.draw_idle()

    def keyPressEvent(self, event):
        if (
            event.key() == Qt.Key_Space
            or event.key() == Qt.Key_Return
            or event.key() == Qt.Key_Enter
        ):
            current_tab = self.tab_widget.currentWidget()
            if current_tab:
                current_tab.schedule_plot()
                self.statusBar().showMessage("Plot updated")
        elif event.key() == Qt.Key_Escape:
            # Leave a text field so the view keys work again
            current_tab = self.tab_widget.currentWidget()
            if current_tab:
                current_tab.canvas.setFocus()
        elif (
            self.tab_widget.currentWidget() is not None
            and self.tab_widget.currentWidget().handle_view_key(event)
        ):
            pass
        elif event.key() == Qt.Key_F5:
            current_tab = self.tab_widget.currentWidget()
            if current_tab:
                current_tab.apply_auto_range("minmax")
        elif event.key() == Qt.Key_F6:
            current_tab = self.tab_widget.currentWidget()
            if current_tab:
                current_tab.apply_auto_range("p1_99")
        elif event.key() == Qt.Key_F7:
            current_tab = self.tab_widget.currentWidget()
            if current_tab:
                current_tab.apply_auto_range("median_rms")
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        image_prefetcher.shutdown()
        background.shutdown()
        # The tools' windows close with this one
        link = getattr(self, "_tool_link", None)
        if link is not None:
            link.close_tools()
        # Tabs get no close event of their own when the window closes
        for tab in self.tabs:
            tab.cleanup()
        super().closeEvent(event)

        # Ensure the entire application (including Log Console) quits
        from PyQt5.QtWidgets import QApplication

        QApplication.instance().quit()



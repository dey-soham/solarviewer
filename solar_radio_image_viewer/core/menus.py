"""
The main window's menu bar, and opening images from it (Open, Open Recent).
"""

import logging
import os
from PyQt5.QtWidgets import QAction
from ..ui.styles import (
    theme_manager,
)

logger = logging.getLogger(__name__)


class MenusMixin:
    """Builds the menu bar and opens images chosen from the File menu.

    Mixed into :class:`SolarRadioImageViewerApp`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``_add_preset_actions``, ``_launch_pipeline_tool``, ``_on_theme_changed``,
    ``_open_preferences_dialog``, ``_toggle_fullscreen``,
    ``_update_cache_menu_item``, ``_update_theme_action_text``,
    ``add_new_tab``, ``close_current_tab``, ``disconnect_remote``,
    ``export_as_casa_image``, ``export_as_fits``, ``export_as_hpc_fits``,
    ``export_casa_region``, ``export_data``, ``fit_2d_gaussian``,
    ``handle_add_tab``, ``launch_data_downloader_gui``,
    ``launch_radio_data_downloader_gui``, ``open_helioviewer_browser``,
    ``save_sub_image``, ``save_tb_map_as_fits``, ``show_about_dialog``,
    ``show_add_arrow_annotation_dialog``, ``show_add_text_annotation_dialog``,
    ``show_cache_manager``, ``show_coordinate_transformation_dialog``,
    ``show_create_video_dialog``, ``show_format_conversion_dialog``,
    ``show_keyboard_shortcuts``, ``show_log_console``, ``show_metadata``,
    ``show_noaa_events_viewer``, ``show_phase_shift_dialog``,
    ``show_remote_connection_dialog``, ``show_remote_file_browser``,
    ``show_unit_conversion_dialog``, ``show_update_dialog``, ``tab_widget``,
    ``toggle_theme``
    """

    def create_menus(self):
        """File · View · Image · Tools · Download · Apps · Help."""
        menubar = self.menuBar()
        self._create_file_menu(menubar)
        self._create_view_menu(menubar)
        self._create_image_menu(menubar)
        self._create_tools_menu(menubar)
        self._create_download_menu(menubar)
        self._create_apps_menu(menubar)
        self._create_help_menu(menubar)

    def _action(self, menu, text, slot, tip, shortcut=None):
        """Add an action to ``menu``; ``shortcut`` is a key or a list of them."""
        act = QAction(text, self)
        if shortcut:
            if isinstance(shortcut, (list, tuple)):
                act.setShortcuts(list(shortcut))
            else:
                act.setShortcut(shortcut)
        act.setStatusTip(tip)
        act.triggered.connect(slot)
        menu.addAction(act)
        return act

    def _create_file_menu(self, menubar):
        """File: open, tabs, remote server, export, preferences, quit."""
        menu = menubar.addMenu("&File")
        # Ctrl+Shift+O opened FITS files when they had their own item
        self._action(menu, "Open Image...", self.open_image,
                     "Open a FITS file or CASA image (a dynamic spectrum opens in its viewer)",
                     ["Ctrl+O", "Ctrl+Shift+O"])
        # The same opening, under a name that says spectra are welcome
        self._action(menu, "Open Dynamic Spectrum...", self.open_dynamic_spectrum,
                     "Open a dynamic spectrum FITS file in the Dynamic Spectrum Viewer")
        self.recent_menu = menu.addMenu("Open Recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)

        menu.addSeparator()
        self._action(menu, "New Tab", self.handle_add_tab, "Open a new image tab", "Ctrl+N")
        self._action(menu, "Close Tab", self.close_current_tab, "Close the current tab", "Ctrl+W")

        menu.addSeparator()
        remote = menu.addMenu("Remote Server")
        self.connect_remote_act = self._action(
            remote, "Connect...", self.show_remote_connection_dialog,
            "Connect to a remote server via SSH", "Ctrl+Shift+R")
        self.browse_remote_act = self._action(
            remote, "Open Remote Image...", self.show_remote_file_browser,
            "Open a FITS file or CASA image from the connected server")
        self.browse_remote_act.setVisible(False)  # until connected
        self.disconnect_remote_act = self._action(
            remote, "Disconnect", self.disconnect_remote, "Disconnect from the remote server")
        self.disconnect_remote_act.setVisible(False)  # until connected
        self.clear_cache_act = self._action(
            menu, "Manage Cache...", self.show_cache_manager,
            "View and manage cached data (remote files, NOAA events, etc.)")
        menu.aboutToShow.connect(self._update_cache_menu_item)

        menu.addSeparator()
        # Ctrl+S saves, as in most applications; Ctrl+E as before
        self._action(menu, "Export Figure...", self.export_data,
                     "Save the figure as an image (PNG, PDF, SVG...)", ["Ctrl+S", "Ctrl+E"])
        export = menu.addMenu("Export Image As")
        self._action(export, "FITS...", self.export_as_fits,
                     "Save the image's data as a FITS file", "Ctrl+Shift+F")
        self._action(export, "CASA Image...", self.export_as_casa_image,
                     "Save the image's data as a CASA image")
        self._action(export, "Helioprojective FITS...", self.export_as_hpc_fits,
                     "Save the image in helioprojective coordinates as FITS", "Ctrl+H")
        self._action(export, "Brightness Temperature FITS...", self.save_tb_map_as_fits,
                     "Convert the image to brightness temperature (K) and save it as FITS")

        menu.addSeparator()
        self._action(menu, "Preferences...", self._open_preferences_dialog,
                     "Application preferences (UI scale, etc.)", "Ctrl+,")
        menu.addSeparator()
        self._action(menu, "Exit", self.close, "Exit the application", "Ctrl+Q")

    def _create_view_menu(self, menubar):
        """View: theme, fullscreen, scroll zoom, log console."""
        menu = menubar.addMenu("&View")
        self.theme_action = self._action(
            menu, "Switch to Light Mode", self.toggle_theme,
            "Toggle between dark and light themes", "Ctrl+D")
        self.fullscreen_action = self._action(
            menu, "Toggle Fullscreen", self._toggle_fullscreen, "Toggle fullscreen mode", "F11")
        self.scroll_zoom_action = QAction("Interactive Scroll Zoom", self, checkable=True)
        self.scroll_zoom_action.setChecked(True)
        self.scroll_zoom_action.setStatusTip("Enable/disable zooming with the mouse wheel")
        menu.addAction(self.scroll_zoom_action)
        menu.addSeparator()
        self.log_console_action = self._action(
            menu, "Log Console", self.show_log_console, "Show the application's log", "Ctrl+L")

        self._update_theme_action_text()
        theme_manager.register_callback(self._on_theme_changed)

    def _create_image_menu(self, menubar):
        """Image: what works on the image in the current tab: its details,
        display presets, annotations, fits and the selected region."""
        menu = menubar.addMenu("&Image")
        self._action(menu, "Image Metadata...", self.show_metadata,
                     "The current image's header and details", "Ctrl+M")

        menu.addSeparator()
        # Presets: entries come from core/presets.py
        presets = menu.addMenu("Display Presets")
        self.auto_preset_act = QAction("Auto-apply Instrument Presets", self, checkable=True)
        self.auto_preset_act.setChecked(True)
        self.auto_preset_act.setStatusTip(
            "Automatically apply colormaps and limits for known instruments (AIA, LASCO, etc.)"
        )
        presets.addAction(self.auto_preset_act)
        presets.addSeparator()
        self._add_preset_actions(presets)

        annotate = menu.addMenu("Annotate")
        self._action(annotate, "Add Text...", self.show_add_text_annotation_dialog,
                     "Add a text annotation to the image", "Ctrl+T")
        self._action(annotate, "Add Arrow...", self.show_add_arrow_annotation_dialog,
                     "Add an arrow annotation to the image", "Ctrl+Shift+A")
        # Shapes (circle, ellipse, square, rectangle), solar radii, remove / clear
        from ..shape_annotations import setup_shape_menu

        setup_shape_menu(self, annotate)

        menu.addSeparator()
        self._action(menu, "Fit 2D Gaussian", self.fit_2d_gaussian,
                     "Fit a 2D Gaussian to the selected region", "Ctrl+G")

        menu.addSeparator()
        self._action(menu, "Export Region as Sub-Image...", self.save_sub_image,
                     "Save the selected region as a new CASA image", "Ctrl+Shift+S")
        self._action(menu, "Export Region as CASA Region...", self.export_casa_region,
                     "Save the selected region as a CASA region file", "Ctrl+R")

    def _create_tools_menu(self, menubar):
        """Tools: making new images from images, one or a folder of them."""
        menu = menubar.addMenu("&Tools")
        self._action(menu, "Solar Phase Center Shift...", self.show_phase_shift_dialog,
                     "Move an image, or a folder of images, to the true solar center", "Ctrl+P")
        self._action(menu, "Transform Coordinates...", self.show_coordinate_transformation_dialog,
                     "Convert images between RA/Dec and helioprojective coordinates")
        self._action(menu, "Convert Units...", self.show_unit_conversion_dialog,
                     "Convert images between flux density (Jy/beam) and brightness temperature (K)")
        self._action(menu, "Convert Image Format...", self.show_format_conversion_dialog,
                     "Convert images between CASA image and FITS")
        menu.addSeparator()
        self._action(menu, "Create Video...", self.show_create_video_dialog,
                     "Make a video from a sequence of images")
        return menu

    def _create_download_menu(self, menubar):
        """Download: data from the archives."""
        menu = menubar.addMenu("&Download")
        self._action(menu, "Solar Data...", self.launch_data_downloader_gui,
                     "Download solar images (SDO, SOHO, STEREO, GOES, IRIS) and prepare them")
        self._action(menu, "Radio Spectra...", self.launch_radio_data_downloader_gui,
                     "Download radio spectra (RSTN, e-CALLISTO) as FITS")

    def _create_apps_menu(self, menubar):
        """Apps: the other windows SolarViewer ships."""
        menu = menubar.addMenu("&Apps")
        self._action(menu, "Solar Activity Viewer", self.show_noaa_events_viewer,
                     "Flares, active regions and other solar activity of a day")
        self._action(menu, "Helioviewer Browser", self.open_helioviewer_browser,
                     "Browse solar images from Helioviewer")
        menu.addSeparator()
        self._action(menu, "Dynamic Spectrum Viewer",
                     lambda: self._launch_pipeline_tool("dynamic_spectrum_viewer", "Dynamic Spectrum Viewer"),
                     "View and clean dynamic spectra FITS files")
        self._action(menu, "Create Dynamic Spectra...",
                     lambda: self._launch_pipeline_tool("create_dynamic_spectra", "Dynamic Spectra Creator"),
                     "Create dynamic spectra FITS from MS files")
        self._action(menu, "Calibration Table Visualizer",
                     lambda: self._launch_pipeline_tool("caltable_visualizer", "Calibration Table Visualizer"),
                     "Visualize bandpass, selfcal, and crossphase calibration tables")
        self._action(menu, "Log Viewer",
                     lambda: self._launch_pipeline_tool("log_viewer", "Log Viewer"),
                     "View and filter pipeline log files")

    def _create_help_menu(self, menubar):
        """Help: shortcuts, updates, about."""
        menu = menubar.addMenu("&Help")
        self._action(menu, "Keyboard Shortcuts", self.show_keyboard_shortcuts,
                     "Show keyboard shortcuts", "F1")
        self._action(menu, "Check for Updates...", self.show_update_dialog,
                     "Check for application updates")
        menu.addSeparator()
        self._action(menu, "About SolarViewer", self.show_about_dialog,
                     "Show information about this application")

    def open_image(self):
        """Open a FITS file or CASA image in the current tab (File menu)."""
        current_tab = self.tab_widget.currentWidget()
        if current_tab:
            current_tab.select_file_or_directory()

    def open_dynamic_spectrum(self):
        """File > Open Dynamic Spectrum: choose a FITS file, opened where it
        belongs (a spectrum in the Dynamic Spectrum Viewer)."""
        from PyQt5.QtWidgets import QFileDialog

        from ..ui.open_image import SPECTRUM_FILTER
        from ..ui.recent_files import last_image_folder

        path, _ = QFileDialog.getOpenFileName(
            self, "Open Dynamic Spectrum", last_image_folder(), SPECTRUM_FILTER
        )
        if path:
            self.open_path(path)

    def _fill_recent_menu(self):
        """File > Open Recent: the images opened lately, newest first."""
        from ..ui.recent_files import clear_recent_images, recent_images

        menu = self.recent_menu
        menu.clear()
        paths = recent_images()
        for path in paths:
            name = os.path.basename(path.rstrip("/")) or path
            action = menu.addAction(name)
            action.setToolTip(path)
            action.setStatusTip(path)
            action.triggered.connect(lambda _=False, p=path: self.open_recent_image(p))
        if not paths:
            menu.addAction("No recent images").setEnabled(False)
            return
        menu.addSeparator()
        menu.addAction("Clear List").triggered.connect(clear_recent_images)

    def open_recent_image(self, path):
        """Open ``path`` where it belongs (File > Open Recent)."""
        return self.open_path(path)

    def open_path(self, path, tab=None):
        """Open the local ``path`` where it belongs: a dynamic spectrum in
        the Dynamic Spectrum Viewer, an image in ``tab``, else in the current
        tab (in a new one if that shows an image from a server). Every way
        of opening a file comes through here. Returns whether it loaded
        (None if the user cancelled), as ``open_image_path``."""
        from ..processing.fits_io import SPECTRUM, fits_kind

        if fits_kind(path) == SPECTRUM:
            return self.open_spectrum(path)
        if tab is None:
            tab = self.tab_widget.currentWidget()
            if tab is None or getattr(tab, "_is_remote_mode", False):
                tab = self.add_new_tab(os.path.basename(path.rstrip("/")))
        return tab.open_image_path(path)

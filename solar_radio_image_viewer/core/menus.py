"""
The main window's menu bar, and opening images from it (Open, Open Recent).
"""

import logging
import os
from PyQt5.QtWidgets import (
    QAction,
    QMenu,
)
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
    ``fit_2d_ring``, ``handle_add_tab``, ``launch_data_downloader_gui``,
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
        menubar = self.menuBar()
        self._create_file_menu(menubar)
        self._create_view_menu(menubar)
        tools_menu = self._create_tools_menu(menubar)
        self._create_region_menu(menubar)
        self._create_fitting_menu(menubar)
        self._create_annotations_menu(menubar)
        self._create_presets_menu(menubar)
        self._create_tabs_menu(menubar)
        self._create_download_menu(menubar)
        self._create_help_menu(menubar)
        self._create_lofar_menu(tools_menu)

    def _create_file_menu(self, menubar):
        """File menu: open, remote, export, quit."""
        file_menu = menubar.addMenu("&File")

        open_act = QAction("Open Image...", self)
        # Ctrl+Shift+O opened FITS files when they had their own item
        open_act.setShortcuts(["Ctrl+O", "Ctrl+Shift+O"])
        open_act.setStatusTip(
            "Open a FITS file or CASA image (a dynamic spectrum opens in its viewer)"
        )
        open_act.triggered.connect(self.open_image)
        file_menu.addAction(open_act)

        # The same opening, under a name that says spectra are welcome
        open_spectrum_act = QAction("Open Dynamic Spectrum...", self)
        open_spectrum_act.setStatusTip(
            "Open a dynamic spectrum FITS file in the Dynamic Spectrum Viewer"
        )
        open_spectrum_act.triggered.connect(self.open_dynamic_spectrum)
        file_menu.addAction(open_spectrum_act)

        self.recent_menu = file_menu.addMenu("Open Recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)

        file_menu.addSeparator()

        # Remote mode actions
        self.connect_remote_act = QAction("🌐 Connect to Remote Server...", self)
        self.connect_remote_act.setShortcut("Ctrl+Shift+R")
        self.connect_remote_act.setStatusTip("Connect to a remote server via SSH")
        self.connect_remote_act.triggered.connect(self.show_remote_connection_dialog)
        file_menu.addAction(self.connect_remote_act)

        self.browse_remote_act = QAction("📁 Open Remote FITS File...", self)
        self.browse_remote_act.setStatusTip(
            "Open a FITS file from the connected remote server"
        )
        self.browse_remote_act.triggered.connect(self.show_remote_file_browser)
        self.browse_remote_act.setVisible(False)  # Hidden until connected
        file_menu.addAction(self.browse_remote_act)

        self.disconnect_remote_act = QAction("❌ Disconnect from Remote", self)
        self.disconnect_remote_act.setStatusTip("Disconnect from the remote server")
        self.disconnect_remote_act.triggered.connect(self.disconnect_remote)
        self.disconnect_remote_act.setVisible(False)  # Hidden until connected
        file_menu.addAction(self.disconnect_remote_act)

        self.clear_cache_act = QAction("🗑️ Manage Cache...", self)
        self.clear_cache_act.setStatusTip(
            "View and manage cached data (remote files, NOAA events, etc.)"
        )
        self.clear_cache_act.triggered.connect(self.show_cache_manager)
        file_menu.addAction(self.clear_cache_act)

        # Update cache size when menu shows
        file_menu.aboutToShow.connect(self._update_cache_menu_item)

        file_menu.addSeparator()

        export_act = QAction("Export Figure", self)
        # Ctrl+S saves, as in most applications; Ctrl+E as before
        export_act.setShortcuts(["Ctrl+S", "Ctrl+E"])
        export_act.setStatusTip("Export current figure as image file")
        export_act.triggered.connect(self.export_data)
        file_menu.addAction(export_act)

        export_data_act = QAction("Export as FITS", self)
        export_data_act.setShortcut("Ctrl+Shift+F")
        export_data_act.setStatusTip("Export current data as FITS file")
        export_data_act.triggered.connect(self.export_as_fits)
        file_menu.addAction(export_data_act)

        export_casa_act = QAction("Export as CASA Image", self)
        export_casa_act.setStatusTip("Export/convert current data to CASA image format")
        export_casa_act.triggered.connect(self.export_as_casa_image)
        file_menu.addAction(export_casa_act)

        export_tb_act = QAction("Export TB Map as FITS", self)
        export_tb_act.setStatusTip(
            "Convert and save brightness temperature map as FITS file"
        )
        export_tb_act.triggered.connect(self.save_tb_map_as_fits)
        file_menu.addAction(export_tb_act)

        export_hpc_fits_act = QAction("Export as HPC FITS", self)
        export_hpc_fits_act.setShortcut("Ctrl+H")
        export_hpc_fits_act.setStatusTip(
            "Export current image as helioprojective FITS file"
        )
        export_hpc_fits_act.triggered.connect(self.export_as_hpc_fits)
        file_menu.addAction(export_hpc_fits_act)

        file_menu.addSeparator()
        exit_act = QAction("Exit", self)
        exit_act.setShortcut("Ctrl+Q")
        exit_act.setStatusTip("Exit the application")
        exit_act.triggered.connect(self.close)
        file_menu.addAction(exit_act)

    def _create_view_menu(self, menubar):
        """View menu: theme, fullscreen, metadata, scroll zoom."""
        # View menu with theme toggle
        view_menu = menubar.addMenu("&View")

        # Theme toggle action
        self.theme_action = QAction("🌙 Switch to Light Mode", self)
        self.theme_action.setShortcut("Ctrl+D")
        self.theme_action.setStatusTip("Toggle between dark and light themes")
        self.theme_action.triggered.connect(self.toggle_theme)
        view_menu.addAction(self.theme_action)

        # Fullscreen toggle action
        self.fullscreen_action = QAction("Toggle Fullscreen", self)
        self.fullscreen_action.setShortcut("F11")
        self.fullscreen_action.setStatusTip("Toggle fullscreen mode")
        self.fullscreen_action.triggered.connect(self._toggle_fullscreen)
        view_menu.addAction(self.fullscreen_action)

        # Log Console action
        self.log_console_action = QAction("Show Log Console", self)
        self.log_console_action.setShortcut("Ctrl+L")
        self.log_console_action.setStatusTip("Show application log console")
        self.log_console_action.triggered.connect(self.show_log_console)
        view_menu.addAction(self.log_console_action)

        metadata_act = QAction("Image Metadata", self)
        metadata_act.setShortcut("Ctrl+M")
        metadata_act.setStatusTip("View detailed metadata for the current image")
        metadata_act.triggered.connect(self.show_metadata)
        view_menu.addAction(metadata_act)

        # Interactive Scroll Zoom toggle action
        self.scroll_zoom_action = QAction(
            "Interactive Scroll Zoom", self, checkable=True
        )
        self.scroll_zoom_action.setChecked(True)
        self.scroll_zoom_action.setStatusTip(
            "Enable/disable zooming with the mouse wheel"
        )
        view_menu.addAction(self.scroll_zoom_action)

        view_menu.addSeparator()

        # Preferences action
        preferences_action = QAction("⚙️ Preferences...", self)
        preferences_action.setStatusTip("Open application preferences (UI scale, etc.)")
        preferences_action.triggered.connect(self._open_preferences_dialog)
        view_menu.addAction(preferences_action)

        # Update action text based on current theme
        self._update_theme_action_text()

        # Register for theme changes
        theme_manager.register_callback(self._on_theme_changed)

    def _create_tools_menu(self, menubar):
        """Tools menu (the LOFAR submenu is added by _create_lofar_menu)."""
        tools_menu = menubar.addMenu("&Tools")

        # Add Solar Phase Shift option

        phase_shift_act = QAction("Solar Phase Center Shift", self)
        phase_shift_act.setShortcut("Ctrl+P")
        phase_shift_act.setStatusTip("Shift solar center to phase center")
        phase_shift_act.triggered.connect(self.show_phase_shift_dialog)
        tools_menu.addAction(phase_shift_act)

        # Batch Processing Submenu
        batch_process_submenu = QMenu("Batch Processing", self)
        tools_menu.addMenu(batch_process_submenu)

        # Add Coordinate Transformation option to submenu

        batch_transform_act = QAction("Coordinate Transformation", self)
        batch_transform_act.setStatusTip(
            "Transform multiple files between RA/DEC and helioprojective coordinates"
        )
        batch_transform_act.triggered.connect(
            self.show_coordinate_transformation_dialog
        )
        batch_process_submenu.addAction(batch_transform_act)

        # Add Solar Phase Center Shift option to batch processing submenu
        batch_phase_shift_act = QAction("Solar Phase Center Shift", self)
        batch_phase_shift_act.setStatusTip("Batch shift solar center to phase center")
        batch_phase_shift_act.triggered.connect(
            lambda: self.show_phase_shift_dialog(batch_mode=True)
        )
        batch_process_submenu.addAction(batch_phase_shift_act)

        # Add Unit Conversion option to batch processing submenu
        batch_unit_conv_act = QAction("Unit Conversion", self)
        batch_unit_conv_act.setStatusTip(
            "Batch convert units between Flux Density and Brightness Temperature"
        )
        batch_unit_conv_act.triggered.connect(self.show_unit_conversion_dialog)
        batch_process_submenu.addAction(batch_unit_conv_act)

        # Add Format Conversion option to batch processing submenu

        batch_format_conv_act = QAction("Format Conversion", self)
        batch_format_conv_act.setStatusTip(
            "Batch convert between CASA Images and FITS files"
        )
        batch_format_conv_act.triggered.connect(self.show_format_conversion_dialog)
        batch_process_submenu.addAction(batch_format_conv_act)

        # Add Create Video action
        create_video_act = QAction("Create &Video", self)
        create_video_act.setStatusTip("Create video from sequence of FITS files")
        create_video_act.triggered.connect(self.show_create_video_dialog)
        tools_menu.addAction(create_video_act)

        tools_menu.addSeparator()

        # Add NOAA Solar Events Viewer
        noaa_events_act = QAction("Solar Activity Viewer", self)
        noaa_events_act.setStatusTip("View solar activity for a specific date")
        noaa_events_act.triggered.connect(self.show_noaa_events_viewer)
        tools_menu.addAction(noaa_events_act)

        # Add Helioviewer Browser
        helioviewer_action = QAction("Helioviewer Browser", self)
        helioviewer_action.setStatusTip("Browse solar images from Helioviewer")
        helioviewer_action.triggered.connect(self.open_helioviewer_browser)
        tools_menu.addAction(helioviewer_action)

        # Dynamic Spectrum Viewer
        dynamic_spectrum_act = QAction("Dynamic Spectrum Viewer", self)
        dynamic_spectrum_act.setStatusTip("View and clean dynamic spectra FITS files")
        dynamic_spectrum_act.triggered.connect(
            lambda: self._launch_pipeline_tool(
                "dynamic_spectrum_viewer", "Dynamic Spectrum Viewer"
            )
        )
        tools_menu.addAction(dynamic_spectrum_act)
        return tools_menu

    def _create_region_menu(self, menubar):
        """Region menu."""
        region_menu = menubar.addMenu("&Region")
        subimg_act = QAction("Export Sub-Image (ROI)", self)
        subimg_act.setShortcut("Ctrl+Shift+S")
        subimg_act.setStatusTip("Export the selected region as a new CASA image")
        subimg_act.triggered.connect(self.save_sub_image)
        region_menu.addAction(subimg_act)

        export_roi_act = QAction("Export ROI as Region", self)
        export_roi_act.setShortcut("Ctrl+R")
        export_roi_act.setStatusTip("Export the selected region as a CASA region file")
        export_roi_act.triggered.connect(self.export_casa_region)
        region_menu.addAction(export_roi_act)

    def _create_fitting_menu(self, menubar):
        """Fitting menu."""
        fitting_menu = menubar.addMenu("F&itting")
        gauss_act = QAction("Fit 2D Gaussian", self)
        gauss_act.setShortcut("Ctrl+G")
        gauss_act.setStatusTip("Fit a 2D Gaussian to the selected region")
        gauss_act.triggered.connect(self.fit_2d_gaussian)
        fitting_menu.addAction(gauss_act)
        ring_act = QAction("Fit Elliptical Ring", self)
        ring_act.setStatusTip("Fit an elliptical ring to the selected region")
        ring_act.triggered.connect(self.fit_2d_ring)
        fitting_menu.addAction(ring_act)

    def _create_annotations_menu(self, menubar):
        """Annotations menu (text, arrows, shapes)."""
        annot_menu = menubar.addMenu("&Annotations")
        text_act = QAction("Add Text Annotation", self)
        text_act.setShortcut("Ctrl+T")
        text_act.setStatusTip("Add text annotation to the image")
        text_act.triggered.connect(self.show_add_text_annotation_dialog)
        annot_menu.addAction(text_act)

        arrow_act = QAction("Add Arrow Annotation", self)
        arrow_act.setShortcut("Ctrl+Shift+A")
        arrow_act.setStatusTip("Add arrow annotation to the image")
        arrow_act.triggered.connect(self.show_add_arrow_annotation_dialog)
        annot_menu.addAction(arrow_act)

        # Shape annotations (circle, ellipse, square, rectangle) + solar radii presets
        from ..shape_annotations import setup_shape_menu

        setup_shape_menu(self, annot_menu)

    def _create_presets_menu(self, menubar):
        """Presets menu; entries come from core/presets.py."""
        preset_menu = menubar.addMenu("Presets")

        self.auto_preset_act = QAction(
            "Auto-apply Instrument Presets", self, checkable=True
        )
        self.auto_preset_act.setChecked(True)
        self.auto_preset_act.setStatusTip(
            "Automatically apply colormaps and limits for known instruments (AIA, LASCO, etc.)"
        )
        preset_menu.addAction(self.auto_preset_act)
        preset_menu.addSeparator()

        self._add_preset_actions(preset_menu)

    def _create_tabs_menu(self, menubar):
        """Tabs menu."""
        tabs_menu = menubar.addMenu("&Tabs")
        new_tab_act = QAction("Add New Tab", self)
        new_tab_act.setShortcut("Ctrl+N")
        new_tab_act.setStatusTip("Add a new tab for comparing images")
        new_tab_act.triggered.connect(self.handle_add_tab)
        tabs_menu.addAction(new_tab_act)
        close_tab_act = QAction("Close Current Tab", self)
        close_tab_act.setShortcut("Ctrl+W")
        close_tab_act.setStatusTip("Close the current tab")
        close_tab_act.triggered.connect(self.close_current_tab)
        tabs_menu.addAction(close_tab_act)

    def _create_download_menu(self, menubar):
        """Download menu."""
        # Add Data Download menu after File menu
        download_menu = menubar.addMenu("&Download")

        # GUI Downloader action
        gui_downloader_action = QAction("Non-radio Solar Data Downloader", self)
        gui_downloader_action.setStatusTip(
            "Launch the graphical interface for downloading non-radio solar data"
        )
        gui_downloader_action.triggered.connect(self.launch_data_downloader_gui)
        download_menu.addAction(gui_downloader_action)

        # Radio Data Downloader action
        radio_downloader_action = QAction("Radio Solar Data Downloader", self)
        radio_downloader_action.setStatusTip(
            "Download radio solar data (Learmonth, etc.) and convert to FITS"
        )
        radio_downloader_action.triggered.connect(self.launch_radio_data_downloader_gui)
        download_menu.addAction(radio_downloader_action)

        # CLI Downloader action
        """cli_downloader_action = QAction("Solar Data Downloader (CLI)", self)
        cli_downloader_action.setStatusTip(
            "Launch the command-line interface for downloading solar data"
        )
        cli_downloader_action.triggered.connect(self.launch_data_downloader_cli)
        download_menu.addAction(cli_downloader_action)"""

    def _create_help_menu(self, menubar):
        """Help menu."""
        help_menu = menubar.addMenu("&Help")

        """tutorial_act = QAction("Tutorial", self)
        tutorial_act.setStatusTip("Open comprehensive tutorial and user guide")
        tutorial_act.triggered.connect(self.show_tutorial)
        help_menu.addAction(tutorial_act)"""

        shortcuts_act = QAction("Keyboard Shortcuts", self)
        shortcuts_act.setShortcut("F1")
        shortcuts_act.setStatusTip("Show keyboard shortcuts")
        shortcuts_act.triggered.connect(self.show_keyboard_shortcuts)
        help_menu.addAction(shortcuts_act)

        update_act = QAction("Check for Updates...", self)
        update_act.setStatusTip("Check for application updates")
        update_act.triggered.connect(self.show_update_dialog)
        help_menu.addAction(update_act)

        about_act = QAction("About", self)
        about_act.setStatusTip("Show information about this application")
        about_act.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_act)

    def _create_lofar_menu(self, tools_menu):
        """LOFAR Tools submenu at the end of the Tools menu."""
        # Add LOFAR Tools submenu
        tools_menu.addSeparator()
        lofar_menu = QMenu("LOFAR Tools", self)

        # Calibration Table Visualizer
        caltable_act = QAction("Calibration Table Visualizer", self)
        caltable_act.setStatusTip(
            "Visualize bandpass, selfcal, and crossphase calibration tables"
        )
        caltable_act.triggered.connect(
            lambda: self._launch_pipeline_tool(
                "caltable_visualizer", "Calibration Table Visualizer"
            )
        )
        lofar_menu.addAction(caltable_act)

        # Log Viewer
        log_viewer_act = QAction("Log Viewer", self)
        log_viewer_act.setStatusTip("View and filter log files")
        log_viewer_act.triggered.connect(
            lambda: self._launch_pipeline_tool("log_viewer", "Log Viewer")
        )
        lofar_menu.addAction(log_viewer_act)

        # Create Dynamic Spectra
        lofar_menu.addSeparator()
        create_ds_act = QAction("Create Dynamic Spectra...", self)
        create_ds_act.setStatusTip("Create dynamic spectra FITS from MS files")
        create_ds_act.triggered.connect(
            lambda: self._launch_pipeline_tool(
                "create_dynamic_spectra", "Dynamic Spectra Creator"
            )
        )
        lofar_menu.addAction(create_ds_act)

        tools_menu.addMenu(lofar_menu)

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

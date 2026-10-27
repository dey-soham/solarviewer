"""
Tutorial dialog for SolarViewer.
Provides a comprehensive guide to using the application.
"""

from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QFrame,
    QScrollArea,
    QWidget,
)
from PyQt5.QtCore import Qt


from .ui.geometry import fit_to_screen, fit_width_to_text


class TutorialDialog(QDialog):
    """A comprehensive tutorial dialog with sidebar navigation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        from .ui.styles import set_hand_cursor

        self._setup_ui()
        set_hand_cursor(self)

    def _setup_ui(self):
        self.setWindowTitle("SolarViewer Tutorial")
        self.setMinimumSize(720, 520)
        fit_to_screen(self, 950, 700)

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Sidebar
        sidebar = self._create_sidebar()
        main_layout.addWidget(sidebar)

        # Content area
        content_area = self._create_content_area()
        main_layout.addWidget(content_area, 1)

        # Select first item
        self.nav_list.setCurrentRow(0)

    def _create_sidebar(self):
        """Create the navigation sidebar."""
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")


        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 15, 12, 15)
        layout.setSpacing(8)

        header = QLabel("Tutorial")
        header.setObjectName("DialogTitle")
        layout.addWidget(header)

        # Navigation list
        self.nav_list = QListWidget()
        self.nav_list.setObjectName("NavList")
        fit_width_to_text(self.nav_list, "Stokes Parameters..........")

        # Add navigation items
        sections = [
            "Getting Started",
            "Opening Images",
            "Display Settings",
            "Stokes Parameters",
            "Navigation",
            "Fitting Tools",
            "Contour Overlays",
            "Regions & Export",
            "Remote Access",
            "Dynamic Spectra",
            "Advanced Tools",
        ]

        for title in sections:
            self.nav_list.addItem(QListWidgetItem(title))

        self.nav_list.currentRowChanged.connect(self._on_section_changed)
        layout.addWidget(self.nav_list, 1)

        # Close button at bottom
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

        return sidebar

    def _create_content_area(self):
        """Create the main content area with stacked pages."""
        container = QFrame()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        self.content_stack = QStackedWidget()

        # Add all section pages
        self.content_stack.addWidget(self._create_getting_started())
        self.content_stack.addWidget(self._create_opening_images())
        self.content_stack.addWidget(self._create_display_settings())
        self.content_stack.addWidget(self._create_stokes_params())
        self.content_stack.addWidget(self._create_navigation())
        self.content_stack.addWidget(self._create_fitting_tools())
        self.content_stack.addWidget(self._create_contours())
        self.content_stack.addWidget(self._create_regions_export())
        self.content_stack.addWidget(self._create_remote_access())
        self.content_stack.addWidget(self._create_dynamic_spectra())
        self.content_stack.addWidget(self._create_advanced_tools())

        layout.addWidget(self.content_stack)
        return container

    def _on_section_changed(self, index):
        """Handle navigation selection change."""
        self.content_stack.setCurrentIndex(index)

    def _create_scroll_page(self, title, content_widget):
        """Create a scrollable page with title."""
        page = QFrame()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 25, 30, 25)
        layout.setSpacing(0)

        # Title
        title_label = QLabel(title)
        title_label.setObjectName("DialogTitle")
        layout.addWidget(title_label)
        layout.addSpacing(16)

        # Scroll area for content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(content_widget)

        layout.addWidget(scroll, 1)
        return page

    def _create_step(self, number, title, description, tip=None):
        """Create a numbered step item."""
        step = QFrame()
        step_layout = QHBoxLayout(step)
        step_layout.setContentsMargins(0, 12, 0, 12)
        step_layout.setSpacing(15)

        # Number badge
        num_label = QLabel(str(number))
        num_label.setObjectName("StepNumber")
        num_label.setAlignment(Qt.AlignCenter)
        step_layout.addWidget(num_label, 0, Qt.AlignTop)

        # Content
        content = QVBoxLayout()
        content.setSpacing(6)

        title_label = QLabel(title)
        title_label.setObjectName("SectionTitle")
        content.addWidget(title_label)

        desc_label = QLabel(description)
        desc_label.setWordWrap(True)
        content.addWidget(desc_label)

        if tip:
            tip_label = QLabel(f"Tip: {tip}")
            tip_label.setObjectName("Tip")
            tip_label.setWordWrap(True)
            content.addWidget(tip_label)

        step_layout.addLayout(content, 1)
        return step

    def _create_info_box(self, title, items):
        """Create an info box with bullet points."""
        box = QFrame()
        box.setObjectName("Card")

        layout = QVBoxLayout(box)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(10)

        header = QLabel(title)
        header.setObjectName("SectionTitle")
        layout.addWidget(header)

        for item in items:
            item_label = QLabel(f"• {item}")
            item_label.setWordWrap(True)
            layout.addWidget(item_label)

        return box

    # ==================== Section Pages ====================

    def _create_getting_started(self):
        """Getting Started section."""
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 15, 15)
        layout.setSpacing(15)

        # Welcome message
        welcome = QLabel(
            "Welcome to SolarViewer! This tutorial will guide you through "
            "all the features of the application for solar radio image visualization."
        )
        welcome.setWordWrap(True)
        layout.addWidget(welcome)

        # What is SolarViewer
        layout.addWidget(
            self._create_info_box(
                "What is SolarViewer?",
                [
                    "A visualization tool for solar radio astronomy images",
                    "Supports CASA image format (.im, .image) and FITS files",
                    "Multi-frequency and multi-Stokes (I, Q, U, V) support",
                    "2D Gaussian model fitting",
                    "Remote file access via SSH/SFTP",
                    "Video creation from image sequences",
                ],
            )
        )

        layout.addSpacing(10)

        # Quick start steps
        quick_title = QLabel("Quick Start")
        quick_title.setObjectName("SectionTitle")
        layout.addWidget(quick_title)

        layout.addWidget(
            self._create_step(
                1,
                "Open an Image",
                "Use File → Open Image (Ctrl+O) for a FITS file or a CASA image, "
                "or File → Open Recent for one you opened before. "
                "You can also drag and drop files onto the window.",
            )
        )

        layout.addWidget(
            self._create_step(
                2,
                "Adjust Display",
                "Use the bar above the image for the colormap and stretch, and the inspector's Display tab to configure "
                "stretch function, and min/max values. Use F5 (Auto), F6 (1-99%), "
                "or F7 (Med±3σ) for quick presets.",
            )
        )

        layout.addWidget(
            self._create_step(
                3,
                "Navigate",
                "Scroll or pinch to zoom, middle-drag to pan, right-drag to zoom "
                "to a box. Press R to reset view. "
                "Use [ and ] keys to navigate between files in the directory.",
            )
        )

        layout.addWidget(
            self._create_step(
                4,
                "Analyze",
                "Use Tools menu for metadata, fitting, and analysis. "
                "Use File menu for various export options including FITS, HPC FITS, and figures.",
            )
        )

        layout.addStretch()
        return self._create_scroll_page("Getting Started", content)

    def _create_opening_images(self):
        """Opening Images section."""
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 15, 15)
        layout.setSpacing(15)

        intro = QLabel(
            "File → Open Image (Ctrl+O) opens both kinds of image: a FITS file is "
            "picked as in any file dialog, a CASA image by opening its folder "
            "(double-click it, or select it and press Open). File → Open Recent "
            "lists the images opened lately."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # CASA Images
        layout.addWidget(
            self._create_info_box(
                "CASA Images",
                [
                    "Native format for radio interferometry data",
                    "Supports .im and .image directories",
                    "Multi-frequency and multi-Stokes cubes supported",
                    "Full metadata and coordinate extraction",
                ],
            )
        )

        # FITS Files
        layout.addWidget(
            self._create_info_box(
                "FITS Files",
                [
                    "Standard astronomical FITS format",
                    "Compressed .fits.gz files supported",
                    "WCS coordinates automatically parsed",
                    "Compatible with SDO/AIA, HMI, SOHO, STEREO, GOES, IRIS data",
                ],
            )
        )

        # Remote Access
        layout.addWidget(
            self._create_info_box(
                "Remote Files (Ctrl+Shift+R)",
                [
                    "Connect to servers via SSH/SFTP",
                    "Browse remote directories",
                    "Save connection profiles",
                    "Files cached locally for faster access",
                ],
            )
        )

        # The bar above the image
        layout.addWidget(
            self._create_info_box(
                "The bar above the image",
                [
                    "File field: type or paste a path and press Enter, or click its icon to choose",
                    "Empty canvas: Open file…, Paste path, or click a recent file",
                    "Stokes: I, Q, U or V; the derived products under More",
                    "Threshold: masking threshold (σ) for the derived products",
                    "Fast load (inspector, Display tab): a quick preview of large images",
                ],
            )
        )

        layout.addStretch()
        return self._create_scroll_page("Opening Images", content)

    def _create_display_settings(self):
        """Display Settings section."""
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 15, 15)
        layout.setSpacing(15)

        intro = QLabel(
            "Configure how your images are displayed with the bar above the image and the inspector's Display tab."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # Colormap
        layout.addWidget(
            self._create_step(
                1,
                "Colormap",
                "Choose from various colormaps: viridis, plasma, inferno, gray, and "
                "solar-specific maps like sdoaia171, sdoaia304, etc. "
                "Type in the colormap field to search.",
                "Append '_r' to reverse any colormap (e.g., 'viridis_r')",
            )
        )

        # Stretch
        layout.addWidget(
            self._create_step(
                2,
                "Stretch Function",
                "Apply scaling transformations: Linear, Log, Sqrt, Squared, Power, "
                "Sinh, Asinh. For Gamma stretch, adjust the gamma value in the field below.",
                "Log and Sqrt work well for high dynamic range radio images",
            )
        )

        # Min/Max
        layout.addWidget(
            self._create_step(
                3,
                "Min/Max Range",
                "Set display range manually or use preset buttons: "
                "Auto (full range), 1-99% (percentile clipping), Med±3σ (median-based).",
                "Scroll over Min/Max fields to adjust values incrementally",
            )
        )

        # Presets
        layout.addWidget(
            self._create_info_box(
                "Preset Shortcuts",
                [
                    "F5 - Auto Min/Max (full data range)",
                    "F6 - 1-99% percentile clipping",
                    "F7 - Median ± 3×RMS",
                    "F8 - SDO/AIA 171 Å preset",
                    "F9 - SDO/HMI preset",
                ],
            )
        )

        # Instrument Presets
        layout.addWidget(
            self._create_info_box(
                "Instrument Presets (Presets Menu)",
                [
                    "SDO/AIA: 94, 131, 171, 193, 211, 304, 335, 1600, 1700, 4500 Å",
                    "SDO/HMI: Magnetogram and continuum",
                    "SOHO/EIT: 171, 195, 284, 304 Å",
                    "SOHO/LASCO: C2 and C3 coronagraphs",
                    "GOES SUVI, IRIS SJI, STEREO EUVI/COR1/COR2",
                ],
            )
        )

        layout.addStretch()
        return self._create_scroll_page("Display Settings", content)

    def _create_stokes_params(self):
        """Stokes Parameters section."""
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 15, 15)
        layout.setSpacing(15)

        intro = QLabel(
            "For polarimetric observations, SolarViewer provides full Stokes parameter support."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # What are Stokes params
        layout.addWidget(
            self._create_info_box(
                "Stokes Parameters",
                [
                    "I - Total intensity (always available)",
                    "Q - Linear polarization (horizontal vs vertical)",
                    "U - Linear polarization (diagonal)",
                    "V - Circular polarization (RCP − LCP)",
                ],
            )
        )

        layout.addSpacing(10)

        # How to use
        layout.addWidget(
            self._create_step(
                1,
                "Select Stokes Parameter",
                "Use the Stokes buttons in the bar above the image (I, Q, U, V; the derived products under More). "
                "Unavailable parameters are automatically grayed out based on your data.",
            )
        )

        layout.addWidget(
            self._create_step(
                2,
                "Threshold Masking",
                "When viewing Q, U, or V, enable threshold masking to hide regions "
                "where |V|/I is below the threshold. Default is 0.01 (1%).",
                "Helps focus on regions with significant polarization",
            )
        )

        layout.addWidget(
            self._create_step(
                3,
                "RMS Box Configuration",
                "Click the gear button next to Stokes to configure the RMS calculation box. "
                "This affects RMS-based statistics and the Med±3σ preset.",
            )
        )

        layout.addStretch()
        return self._create_scroll_page("Stokes Parameters", content)

    def _create_navigation(self):
        """Navigation section."""
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 15, 15)
        layout.setSpacing(15)

        intro = QLabel(
            "Navigate through your images efficiently with mouse and keyboard controls."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # Mouse controls
        layout.addWidget(
            self._create_info_box(
                "Mouse Controls",
                [
                    "Scroll wheel or pinch - Zoom about the pointer",
                    "Middle-drag - Pan the view (left-drag too, with the Pan tool)",
                    "Right-drag - Zoom to the box drawn",
                    "Double-click - Zoom in with the Pan tool (Shift: zoom out)",
                    "Hover - Coordinates and pixel value at the cursor",
                ],
            )
        )

        # Keyboard navigation
        layout.addWidget(
            self._create_info_box(
                "View Controls",
                [
                    "R - Reset view to full image",
                    "1 - Zoom to 1°×1° field of view",
                    "+/= - Zoom in",
                    "− - Zoom out",
                    "Arrow keys - Pan",
                    "Space/Enter - Refresh display",
                    "Esc - Leave a text field, so these keys work again",
                    "F11 - Toggle fullscreen",
                ],
            )
        )

        # File navigation
        layout.addWidget(
            self._create_info_box(
                "File Navigation",
                [
                    "[ - Previous file in directory",
                    "] - Next file in directory",
                    "{ - Jump to first file",
                    "} - Jump to last file",
                ],
            )
        )

        # Status bar
        layout.addWidget(
            self._create_info_box(
                "Status Bar Information",
                [
                    "Pixel coordinates (x, y)",
                    "World coordinates (RA/Dec or helioprojective)",
                    "Pixel value at cursor position",
                    "Current file path",
                ],
            )
        )

        layout.addStretch()
        return self._create_scroll_page("Navigation", content)

    def _create_fitting_tools(self):
        """Fitting Tools section."""
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 15, 15)
        layout.setSpacing(15)

        intro = QLabel(
            "SolarViewer includes 2D fitting tools for source characterization."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # Gaussian fitting
        layout.addWidget(
            self._create_step(
                1,
                "2D Gaussian Fitting (Ctrl+G)",
                "Fit a 2D elliptical Gaussian to point sources. "
                "Via Image → Fit 2D Gaussian. Select a region containing the source. "
                "Results include: position, FWHM (major/minor), position angle, peak and integrated flux.",
                "Best for isolated, roughly Gaussian-shaped sources",
            )
        )

        # Tips
        layout.addWidget(
            self._create_info_box(
                "Fitting Tips",
                [
                    "Draw a selection rectangle around the source region first",
                    "Check the residual image to assess fit quality",
                    "Results shown in both pixel and world coordinates",
                    "Fitted parameters can be copied to clipboard",
                    "Beam ellipse shown for size comparison in radio images",
                ],
            )
        )

        layout.addStretch()
        return self._create_scroll_page("Fitting Tools", content)

    def _create_contours(self):
        """Contour Overlays section."""
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 15, 15)
        layout.setSpacing(15)

        intro = QLabel(
            "Overlay contours from the current image or external images, "
            "up to ten layers at once."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        layout.addWidget(
            self._create_step(
                1,
                "Open Contour Settings",
                "Right-click on the image and select 'Contour Settings', or use the "
                "Contours switch in the inspector's Display tab.",
            )
        )

        layout.addWidget(
            self._create_step(
                2,
                "Select Contour Source",
                "Choose 'Self' to contour the current image, or 'External' to load "
                "a different CASA or FITS image for contours.",
            )
        )

        layout.addWidget(
            self._create_step(
                3,
                "Configure Contour Levels",
                "Set levels as fractions (0.1, 0.3, 0.5, 0.7, 0.9), absolute values, "
                "or RMS multiples. Enable 'Negative contours' for dashed negative lines.",
            )
        )

        layout.addWidget(
            self._create_step(
                4,
                "Customize Appearance",
                "Choose contour color, line width (0.5-3.0), and line style. "
                "Enable 'Show contour labels' to display level values.",
            )
        )

        layout.addWidget(
            self._create_step(
                5,
                "Add More Layers",
                "Use '+ Add' or 'Duplicate' in the Layers list to overlay up to ten "
                "contour sets, e.g. Stokes I and V of one file, or several files. "
                "Each layer has its own source, levels and colour; untick a layer "
                "to hide it. 'Show legend' names each layer on the image; its "
                "position and font size can be set there too.",
            )
        )

        layout.addWidget(
            self._create_info_box(
                "Coordinate Matching",
                [
                    "Contours automatically aligned to the base image",
                    "Works across RA/Dec ↔ Helioprojective transformations",
                    "Different pixel scales handled automatically",
                    "Untick 'Full extent' to keep contours within 1.5x the image",
                ],
            )
        )

        layout.addStretch()
        return self._create_scroll_page("Contour Overlays", content)

    def _create_regions_export(self):
        """Regions and Export section."""
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 15, 15)
        layout.setSpacing(15)

        intro = QLabel("Export images, sub-regions, and data in various formats.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # Region operations
        layout.addWidget(
            self._create_info_box(
                "Region Operations",
                [
                    "Export Sub-Image (Ctrl+Shift+S): Save selected region as new image",
                    "Export ROI as Region (Ctrl+R): Save as CASA region file",
                ],
            )
        )

        # Export options
        layout.addWidget(
            self._create_info_box(
                "Export Options (File Menu)",
                [
                    "Export Figure (Ctrl+S or Ctrl+E): Save as PNG, PDF, or SVG with colorbar",
                    "Export as FITS (Ctrl+Shift+F): Convert to standard FITS",
                    "Export as CASA Image: Save in CASA format",
                    "Export TB Map: Convert to brightness temperature FITS",
                    "Export as HPC FITS (Ctrl+H): Helioprojective coordinates",
                ],
            )
        )

        # Annotations
        layout.addWidget(
            self._create_info_box(
                "Annotations (Annotations Menu)",
                [
                    "Add Text Annotation (Ctrl+T): Place text labels",
                    "Add Arrow Annotation (Ctrl+Shift+A): Draw arrows to features",
                ],
            )
        )

        layout.addStretch()
        return self._create_scroll_page("Regions & Export", content)

    def _create_remote_access(self):
        """Remote Access section."""
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 15, 15)
        layout.setSpacing(15)

        intro = QLabel("Access files on remote servers directly via SSH/SFTP.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        layout.addWidget(
            self._create_step(
                1,
                "Connect to Server (Ctrl+Shift+R)",
                "File → Connect to Remote Server. Enter hostname, port (default 22), "
                "username, and choose password or SSH key authentication.",
            )
        )

        layout.addWidget(
            self._create_step(
                2,
                "Save Connection Profile",
                "Save frequently used connections for quick access. "
                "Profiles store hostname, port, and username (not passwords).",
            )
        )

        layout.addWidget(
            self._create_step(
                3,
                "Browse and Open Files",
                "After connecting, File → Open Remote FITS File becomes available. "
                "Navigate directories, use path auto-complete, and select files to open.",
            )
        )

        layout.addWidget(
            self._create_step(
                4,
                "Cache Management",
                "Downloaded files are cached locally for faster access. "
                "Use File → Clear Remote Cache to free disk space.",
            )
        )

        layout.addWidget(
            self._create_info_box(
                "Security",
                [
                    "SSH key authentication recommended",
                    "Passwords entered each session, not stored",
                    "All transfers use encrypted SFTP protocol",
                ],
            )
        )

        layout.addStretch()
        return self._create_scroll_page("Remote Access", content)

    def _create_dynamic_spectra(self):
        """Dynamic Spectra section."""
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 15, 15)
        layout.setSpacing(15)

        intro = QLabel(
            "SolarViewer also views and cleans dynamic spectra (frequency "
            "against time). They open in the Dynamic Spectrum Viewer, a "
            "companion window that follows SolarViewer's theme and closes "
            "with it."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        layout.addWidget(
            self._create_step(
                1,
                "Open a Spectrum Like Any File",
                "File → Open Image or Open Dynamic Spectrum, Open Recent, a drop "
                "on the window, or the command line: SolarViewer reads the "
                "FITS headers and sends a dynamic spectrum to its viewer. "
                "Opening another spectrum reuses the same window.",
                tip="Next/Previous file in a folder step over spectra, so "
                "browsing images is not interrupted",
            )
        )

        layout.addWidget(
            self._create_step(
                2,
                "Explore",
                "Pan and zoom the spectrum with the minimap for your place in "
                "it. Profile gives the light curve or spectrum through a point; "
                "Drift measures a burst's drift rate from its start and end.",
            )
        )

        layout.addWidget(
            self._create_step(
                3,
                "Clean",
                "Mask radio interference by region (ROI) or across the whole "
                "file, protect features with Keep, normalise by the bandpass, "
                "and undo any step. Save the cleaned spectrum or a figure of it.",
            )
        )

        layout.addWidget(
            self._create_step(
                4,
                "See the Image at a Time",
                "With the Image tool, click a time in the spectrum: SolarViewer "
                "shows its image nearest that time from the current tab's "
                "folder (or the frequency folder beside it). An image opened "
                "from the viewer's own Open dialog comes back to a SolarViewer tab.",
            )
        )

        layout.addWidget(
            self._create_info_box(
                "Making Spectra",
                [
                    "Apps → Create Dynamic Spectra: from Measurement Sets",
                    "viewds on the command line runs the viewer on its own",
                ],
            )
        )

        layout.addStretch()
        return self._create_scroll_page("Dynamic Spectra", content)

    def _create_advanced_tools(self):
        """Advanced Tools section."""
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 15, 15)
        layout.setSpacing(15)

        intro = QLabel("Additional tools for analysis, visualization, and data access.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # Tools Menu
        layout.addWidget(
            self._create_info_box(
                "Tools Menu",
                [
                    "Image Metadata (Ctrl+M): View complete image metadata",
                    "Solar Phase Center Shift (Ctrl+P): Align phase center with Sun",
                    "Create Video: Create animations from FITS sequences",
                ],
            )
        )

        # Solar Activity Viewer
        layout.addWidget(
            self._create_info_box(
                "Solar Activity Viewer",
                [
                    "Access via Apps → Solar Activity Viewer or the activity icon at the top of the inspector",
                    "NOAA solar event reports and active regions",
                    "Solar conditions summary and CME alerts",
                    "GOES X-ray flux plots",
                    "Real-time context images from SDO, SOHO, STEREO",
                ],
            )
        )

        # Helioviewer Browser
        layout.addWidget(
            self._create_info_box(
                "Helioviewer Browser",
                [
                    "Access via Apps → Helioviewer Browser or the sun icon at the top of the inspector",
                    "Browse solar images by time range",
                    "Multiple instruments: SDO, SOHO, STEREO, GOES, Solar Orbiter",
                    "Frame-by-frame navigation with time slider",
                    "Batch download and MP4 export",
                ],
            )
        )

        # Data Downloaders
        layout.addWidget(
            self._create_info_box(
                "Data Downloaders (Download Menu)",
                [
                    "Non-radio Solar Data: SDO, SOHO, STEREO, GOES, IRIS archives",
                    "Radio Solar Data: Learmonth and other radio data with FITS conversion",
                ],
            )
        )

        # Multi-Tab
        layout.addWidget(
            self._create_info_box(
                "Multi-Tab Comparison (Tabs Menu)",
                [
                    "Add New Tab (Ctrl+N): Open multiple images",
                    "Close Tab (Ctrl+W): Close current tab",
                    "Each tab has independent display settings",
                ],
            )
        )

        # View Options
        layout.addWidget(
            self._create_info_box(
                "View Options (View Menu)",
                [
                    "Toggle Dark/Light Mode (Ctrl+D)",
                    "Fullscreen Mode (F11)",
                    "Preferences: UI scale adjustment",
                ],
            )
        )

        layout.addStretch()
        return self._create_scroll_page("Advanced Tools", content)

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
    QGridLayout,
    QSizePolicy,
)
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QFont, QIcon


class TutorialDialog(QDialog):
    """A comprehensive tutorial dialog with sidebar navigation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        from .styles import theme_manager, set_hand_cursor

        self.is_dark = theme_manager.is_dark
        self._setup_ui()
        set_hand_cursor(self)

    def _setup_ui(self):
        self.setWindowTitle("SolarViewer Tutorial")
        self.setMinimumSize(900, 650)
        self.resize(950, 700)

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
        sidebar.setFixedWidth(220)

        if self.is_dark:
            sidebar.setStyleSheet(
                """
                QFrame {
                    background-color: #1e1e2e;
                    border-right: 1px solid #3a3a4a;
                }
            """
            )
        else:
            sidebar.setStyleSheet(
                """
                QFrame {
                    background-color: #f5f5f7;
                    border-right: 1px solid #e0e0e0;
                }
            """
            )

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 15, 12, 15)
        layout.setSpacing(8)

        # Header
        header = QLabel("📖 Tutorial")
        header.setStyleSheet(
            f"""
            font-size: 16pt;
            font-weight: bold;
            color: {'#a78bfa' if self.is_dark else '#7c3aed'};
            padding: 8px 0 15px 5px;
        """
        )
        layout.addWidget(header)

        # Navigation list
        self.nav_list = QListWidget()
        self.nav_list.setStyleSheet(self._get_nav_list_style())
        self.nav_list.setSpacing(2)

        # Add navigation items
        sections = [
            ("🚀", "Getting Started"),
            ("📂", "Opening Images"),
            ("🎨", "Display Settings"),
            ("📊", "Stokes Parameters"),
            ("🧭", "Navigation"),
            ("📐", "Fitting Tools"),
            ("📈", "Contour Overlays"),
            ("✂️", "Regions & Export"),
            ("🌐", "Remote Access"),
            ("🎬", "Advanced Tools"),
        ]

        for icon, title in sections:
            item = QListWidgetItem(f"{icon}  {title}")
            item.setSizeHint(QSize(0, 40))
            self.nav_list.addItem(item)

        self.nav_list.currentRowChanged.connect(self._on_section_changed)
        layout.addWidget(self.nav_list, 1)

        # Close button at bottom
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(
            """
            QPushButton {
                padding: 10px 20px;
                font-size: 11pt;
            }
        """
        )
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

        return sidebar

    def _get_nav_list_style(self):
        if self.is_dark:
            return """
                QListWidget {
                    background-color: transparent;
                    border: none;
                    font-size: 11pt;
                    outline: none;
                }
                QListWidget::item {
                    color: #c0c0d0;
                    padding: 10px 12px;
                    border-radius: 8px;
                    margin: 2px 0;
                }
                QListWidget::item:selected {
                    background-color: rgba(124, 58, 237, 0.3);
                    color: #a78bfa;
                }
                QListWidget::item:hover:!selected {
                    background-color: rgba(255, 255, 255, 0.05);
                }
            """
        else:
            return """
                QListWidget {
                    background-color: transparent;
                    border: none;
                    font-size: 11pt;
                    outline: none;
                }
                QListWidget::item {
                    color: #4a4a5a;
                    padding: 10px 12px;
                    border-radius: 8px;
                    margin: 2px 0;
                }
                QListWidget::item:selected {
                    background-color: rgba(124, 58, 237, 0.15);
                    color: #7c3aed;
                }
                QListWidget::item:hover:!selected {
                    background-color: rgba(0, 0, 0, 0.03);
                }
            """

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
        title_label.setStyleSheet(
            f"""
            font-size: 20pt;
            font-weight: bold;
            color: {'#e0e0f0' if self.is_dark else '#1f2937'};
            padding-bottom: 20px;
        """
        )
        layout.addWidget(title_label)

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
        num_label.setFixedSize(32, 32)
        num_label.setAlignment(Qt.AlignCenter)
        num_label.setStyleSheet(
            f"""
            QLabel {{
                background-color: {'#7c3aed' if self.is_dark else '#7c3aed'};
                color: white;
                border-radius: 16px;
                font-size: 12pt;
                font-weight: bold;
            }}
        """
        )
        step_layout.addWidget(num_label, 0, Qt.AlignTop)

        # Content
        content = QVBoxLayout()
        content.setSpacing(6)

        title_label = QLabel(title)
        title_label.setStyleSheet(
            f"""
            font-size: 12pt;
            font-weight: bold;
            color: {'#e0e0f0' if self.is_dark else '#1f2937'};
        """
        )
        content.addWidget(title_label)

        desc_label = QLabel(description)
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet(
            f"""
            font-size: 11pt;
            color: {'#a0a0b0' if self.is_dark else '#4b5563'};
            line-height: 1.5;
        """
        )
        content.addWidget(desc_label)

        if tip:
            tip_label = QLabel(f"💡 {tip}")
            tip_label.setWordWrap(True)
            tip_label.setStyleSheet(
                f"""
                font-size: 10pt;
                color: {'#a78bfa' if self.is_dark else '#7c3aed'};
                padding: 8px 12px;
                background-color: {'rgba(124, 58, 237, 0.1)' if self.is_dark else 'rgba(124, 58, 237, 0.08)'};
                border-radius: 6px;
                margin-top: 6px;
            """
            )
            content.addWidget(tip_label)

        step_layout.addLayout(content, 1)
        return step

    def _create_info_box(self, icon, title, items):
        """Create an info box with icon and bullet points."""
        box = QFrame()
        box.setStyleSheet(
            f"""
            QFrame {{
                background-color: {'rgba(255, 255, 255, 0.03)' if self.is_dark else 'rgba(0, 0, 0, 0.02)'};
                border-radius: 10px;
                padding: 15px;
            }}
        """
        )

        layout = QVBoxLayout(box)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(10)

        header = QLabel(f"{icon}  {title}")
        header.setStyleSheet(
            f"""
            font-size: 13pt;
            font-weight: bold;
            color: {'#a78bfa' if self.is_dark else '#7c3aed'};
        """
        )
        layout.addWidget(header)

        for item in items:
            item_label = QLabel(f"• {item}")
            item_label.setWordWrap(True)
            item_label.setStyleSheet(
                f"""
                font-size: 11pt;
                color: {'#c0c0d0' if self.is_dark else '#4b5563'};
                padding-left: 10px;
            """
            )
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
        welcome.setStyleSheet(
            f"""
            font-size: 12pt;
            color: {'#c0c0d0' if self.is_dark else '#4b5563'};
            padding-bottom: 10px;
        """
        )
        layout.addWidget(welcome)

        # What is SolarViewer
        layout.addWidget(
            self._create_info_box(
                "☀️",
                "What is SolarViewer?",
                [
                    "A visualization tool for solar radio astronomy images",
                    "Supports CASA image format (.im, .image) and FITS files",
                    "Multi-frequency and multi-Stokes (I, Q, U, V) support",
                    "2D Gaussian and elliptical ring model fitting",
                    "Remote file access via SSH/SFTP",
                    "Video creation from image sequences",
                ],
            )
        )

        layout.addSpacing(10)

        # Quick start steps
        quick_title = QLabel("Quick Start")
        quick_title.setStyleSheet(
            f"""
            font-size: 14pt;
            font-weight: bold;
            color: {'#e0e0f0' if self.is_dark else '#1f2937'};
            padding: 10px 0;
        """
        )
        layout.addWidget(quick_title)

        layout.addWidget(
            self._create_step(
                1,
                "Open an Image",
                "Use File → Open Solar Radio Image (Ctrl+O) for CASA images or "
                "File → Open FITS File (Ctrl+Shift+O) for FITS files. "
                "You can also drag and drop files onto the window.",
            )
        )

        layout.addWidget(
            self._create_step(
                2,
                "Adjust Display",
                "Use the Display Settings panel on the left to configure colormap, "
                "stretch function, and min/max values. Use F5 (Auto), F6 (1-99%), "
                "or F7 (Med±3σ) for quick presets.",
            )
        )

        layout.addWidget(
            self._create_step(
                3,
                "Navigate",
                "Scroll to zoom, click and drag to pan. Press R to reset view. "
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
        return self._create_scroll_page("🚀 Getting Started", content)

    def _create_opening_images(self):
        """Opening Images section."""
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 15, 15)
        layout.setSpacing(15)

        intro = QLabel(
            "SolarViewer supports multiple image formats and loading methods."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(
            f"font-size: 11pt; color: {'#a0a0b0' if self.is_dark else '#4b5563'};"
        )
        layout.addWidget(intro)

        # CASA Images
        layout.addWidget(
            self._create_info_box(
                "📦",
                "CASA Images (Ctrl+O)",
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
                "📄",
                "FITS Files (Ctrl+Shift+O)",
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
                "🌐",
                "Remote Files (Ctrl+Shift+R)",
                [
                    "Connect to servers via SSH/SFTP",
                    "Browse remote directories",
                    "Save connection profiles",
                    "Files cached locally for faster access",
                ],
            )
        )

        # Image Selection Panel
        layout.addWidget(
            self._create_info_box(
                "📋",
                "Image Selection Panel",
                [
                    "Directory Entry: Type or paste paths directly",
                    "File Selector: Choose from files in directory",
                    "Fast Load: Enable for quick preview of large images",
                    "Stokes: Select I, Q, U, or V parameter",
                    "Threshold: Masking threshold for Q, U, V display",
                ],
            )
        )

        layout.addStretch()
        return self._create_scroll_page("📂 Opening Images", content)

    def _create_display_settings(self):
        """Display Settings section."""
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 15, 15)
        layout.setSpacing(15)

        intro = QLabel(
            "Configure how your images are displayed using the Display Settings panel."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(
            f"font-size: 11pt; color: {'#a0a0b0' if self.is_dark else '#4b5563'};"
        )
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
                "⚡",
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
                "🔬",
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
        return self._create_scroll_page("🎨 Display Settings", content)

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
        intro.setStyleSheet(
            f"font-size: 11pt; color: {'#a0a0b0' if self.is_dark else '#4b5563'};"
        )
        layout.addWidget(intro)

        # What are Stokes params
        layout.addWidget(
            self._create_info_box(
                "📊",
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
                "Use the Stokes dropdown in the Image Selection panel. "
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
                "Click the gear icon (⚙️) next to Stokes to configure the RMS calculation box. "
                "This affects RMS-based statistics and the Med±3σ preset.",
            )
        )

        layout.addStretch()
        return self._create_scroll_page("📊 Stokes Parameters", content)

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
        intro.setStyleSheet(
            f"font-size: 11pt; color: {'#a0a0b0' if self.is_dark else '#4b5563'};"
        )
        layout.addWidget(intro)

        # Mouse controls
        layout.addWidget(
            self._create_info_box(
                "🖱️",
                "Mouse Controls",
                [
                    "Scroll wheel - Zoom in/out",
                    "Click + Drag - Pan the view",
                    "Right-click - Context menu with quick actions",
                    "Hover - Display coordinates in status bar",
                ],
            )
        )

        # Keyboard navigation
        layout.addWidget(
            self._create_info_box(
                "⌨️",
                "View Controls",
                [
                    "R - Reset view to full image",
                    "1 - Zoom to 1°×1° field of view",
                    "+/= - Zoom in",
                    "− - Zoom out",
                    "Space/Enter - Refresh display",
                    "F11 - Toggle fullscreen",
                ],
            )
        )

        # File navigation
        layout.addWidget(
            self._create_info_box(
                "📁",
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
                "📍",
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
        return self._create_scroll_page("🧭 Navigation", content)

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
        intro.setStyleSheet(
            f"font-size: 11pt; color: {'#a0a0b0' if self.is_dark else '#4b5563'};"
        )
        layout.addWidget(intro)

        # Gaussian fitting
        layout.addWidget(
            self._create_step(
                1,
                "2D Gaussian Fitting (Ctrl+G)",
                "Fit a 2D elliptical Gaussian to point sources. "
                "Via Fitting → Fit 2D Gaussian. Select a region containing the source. "
                "Results include: position, FWHM (major/minor), position angle, peak and integrated flux.",
                "Best for isolated, roughly Gaussian-shaped sources",
            )
        )

        # Ring fitting
        layout.addWidget(
            self._create_step(
                2,
                "Elliptical Ring Fitting (Ctrl+L)",
                "Fit a thin ring model to circular features. "
                "Via Fitting → Fit Elliptical Ring. Provide initial guesses for "
                "center, inner and outer radii.",
                "Useful for CME fronts and limb brightenings",
            )
        )

        # Tips
        layout.addWidget(
            self._create_info_box(
                "💡",
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
        return self._create_scroll_page("📐 Fitting Tools", content)

    def _create_contours(self):
        """Contour Overlays section."""
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 15, 15)
        layout.setSpacing(15)

        intro = QLabel("Overlay contours from the current image or external images.")
        intro.setWordWrap(True)
        intro.setStyleSheet(
            f"font-size: 11pt; color: {'#a0a0b0' if self.is_dark else '#4b5563'};"
        )
        layout.addWidget(intro)

        layout.addWidget(
            self._create_step(
                1,
                "Open Contour Settings",
                "Right-click on the image and select 'Contour Settings', or use the "
                "Contours button in the right panel.",
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
            self._create_info_box(
                "🔄",
                "Coordinate Matching",
                [
                    "Contours automatically reprojected to match base image",
                    "Works across RA/Dec ↔ Helioprojective transformations",
                    "Different pixel scales handled automatically",
                    "Enable 'Show full extent' for contours beyond image boundaries",
                ],
            )
        )

        layout.addStretch()
        return self._create_scroll_page("📈 Contour Overlays", content)

    def _create_regions_export(self):
        """Regions and Export section."""
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 15, 15)
        layout.setSpacing(15)

        intro = QLabel("Export images, sub-regions, and data in various formats.")
        intro.setWordWrap(True)
        intro.setStyleSheet(
            f"font-size: 11pt; color: {'#a0a0b0' if self.is_dark else '#4b5563'};"
        )
        layout.addWidget(intro)

        # Region operations
        layout.addWidget(
            self._create_info_box(
                "✂️",
                "Region Operations",
                [
                    "Export Sub-Image (Ctrl+S): Save selected region as new image",
                    "Export ROI as Region (Ctrl+R): Save as CASA region file",
                ],
            )
        )

        # Export options
        layout.addWidget(
            self._create_info_box(
                "💾",
                "Export Options (File Menu)",
                [
                    "Export Figure (Ctrl+E): Save as PNG, PDF, or SVG with colorbar",
                    "Export as FITS (Ctrl+F): Convert to standard FITS",
                    "Export as CASA Image: Save in CASA format",
                    "Export TB Map: Convert to brightness temperature FITS",
                    "Export as HPC FITS (Ctrl+H): Helioprojective coordinates",
                ],
            )
        )

        # Annotations
        layout.addWidget(
            self._create_info_box(
                "✏️",
                "Annotations (Annotations Menu)",
                [
                    "Add Text Annotation (Ctrl+T): Place text labels",
                    "Add Arrow Annotation (Ctrl+A): Draw arrows to features",
                ],
            )
        )

        layout.addStretch()
        return self._create_scroll_page("✂️ Regions & Export", content)

    def _create_remote_access(self):
        """Remote Access section."""
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 15, 15)
        layout.setSpacing(15)

        intro = QLabel("Access files on remote servers directly via SSH/SFTP.")
        intro.setWordWrap(True)
        intro.setStyleSheet(
            f"font-size: 11pt; color: {'#a0a0b0' if self.is_dark else '#4b5563'};"
        )
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
                "🔒",
                "Security",
                [
                    "SSH key authentication recommended",
                    "Passwords entered each session, not stored",
                    "All transfers use encrypted SFTP protocol",
                ],
            )
        )

        layout.addStretch()
        return self._create_scroll_page("🌐 Remote Access", content)

    def _create_advanced_tools(self):
        """Advanced Tools section."""
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 15, 15)
        layout.setSpacing(15)

        intro = QLabel("Additional tools for analysis, visualization, and data access.")
        intro.setWordWrap(True)
        intro.setStyleSheet(
            f"font-size: 11pt; color: {'#a0a0b0' if self.is_dark else '#4b5563'};"
        )
        layout.addWidget(intro)

        # Tools Menu
        layout.addWidget(
            self._create_info_box(
                "🔧",
                "Tools Menu",
                [
                    "Image Metadata (Ctrl+M): View complete image metadata",
                    "Solar Phase Center Shift (Ctrl+P): Align phase center with Sun",
                    "Fast Viewer (Napari) (Ctrl+Shift+N): Ultra-fast 3D visualization",
                    "Create Video: Create animations from FITS sequences",
                ],
            )
        )

        # Solar Activity Viewer
        layout.addWidget(
            self._create_info_box(
                "☀️",
                "Solar Activity Viewer",
                [
                    "Access via Tools → Solar Activity Viewer or Activity button",
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
                "🌐",
                "Helioviewer Browser",
                [
                    "Access via Tools → Helioviewer Browser or Helioviewer button",
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
                "⬇️",
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
                "📑",
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
                "👁️",
                "View Options (View Menu)",
                [
                    "Toggle Dark/Light Mode (Ctrl+D)",
                    "Fullscreen Mode (F11)",
                    "Preferences: UI scale adjustment",
                ],
            )
        )

        layout.addStretch()
        return self._create_scroll_page("🎬 Advanced Tools", content)

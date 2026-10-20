"""
Shared theme module for LOFAR/SIMPL tools.

This module provides theme support for LOFAR tools launched as separate processes
from the Solar Radio Image Viewer. It reuses the same palettes and stylesheets
to ensure visual consistency.
"""

import sys
import os
from PyQt5.QtCore import Qt, QSettings
from PyQt5.QtWidgets import QApplication

def setup_high_dpi():
    """Enable HiDPI scaling for high-resolution displays.
    Must be called BEFORE creating a QApplication instance.
    """
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    pre_settings = QSettings("SolarViewer", "SolarViewer")
    ui_scale_factor = pre_settings.value("ui_scale_factor", 1.0, type=float)
    if ui_scale_factor != 1.0:
        os.environ["QT_SCALE_FACTOR"] = str(ui_scale_factor)

# Try to import palettes and stylesheet from main viewer styles for consistency
try:
    from ..styles import (
        DARK_PALETTE,
        LIGHT_PALETTE,
        theme_manager,
        load_bundled_fonts,
        get_stylesheet as get_viewer_stylesheet,
        get_matplotlib_params as get_viewer_matplotlib_params,
    )

    _HAS_VIEWER_STYLES = True
except ImportError:
    _HAS_VIEWER_STYLES = False
    theme_manager = None

    def load_bundled_fonts():
        """Fallback font loader if main styles cannot be imported."""
        from PyQt5.QtGui import QFontDatabase, QFont

        base_dir = os.path.dirname(os.path.abspath(__file__))
        assets_dir = os.path.join(os.path.dirname(base_dir), "assets")

        if not os.path.exists(assets_dir):
            return "Inter"

        font_db = QFontDatabase()
        inter_family = "Inter"

        font_files = [
            "Inter-Regular.ttf",
            "Inter-Medium.ttf",
            "Inter-SemiBold.ttf",
            "Inter-Bold.ttf",
        ]

        for f in font_files:
            p = os.path.join(assets_dir, f)
            if os.path.exists(p):
                font_id = font_db.addApplicationFont(p)
                if font_id != -1:
                    families = font_db.applicationFontFamilies(font_id)
                    if families:
                        inter_family = families[0]
        
        # Load emoji fallback
        emoji_p = os.path.join(assets_dir, "NotoEmoji-Regular.ttf")
        if os.path.exists(emoji_p):
            font_db.addApplicationFont(emoji_p)
            
        return inter_family

    # Fallback palettes matching solarviewer's styles.py
    DARK_PALETTE = {
        "window": "#0f0f1a",
        "base": "#1a1a2e",
        "text": "#f0f0f5",
        "text_secondary": "#a0a0b0",
        "highlight": "#6366f1",
        "highlight_hover": "#818cf8",
        "highlight_glow": "rgba(99, 102, 241, 0.3)",
        "button": "#252542",
        "button_hover": "#32325d",
        "button_pressed": "#1a1a35",
        "button_gradient_start": "#3730a3",
        "button_gradient_end": "#4f46e5",
        "border": "#2d2d4a",
        "border_light": "#3d3d5c",
        "disabled": "#4a4a6a",
        "success": "#22c55e",
        "warning": "#f59e0b",
        "error": "#ef4444",
        "secondary": "#8b5cf6",
        "surface": "#16162a",
        "surface_elevated": "#1e1e3a",
        "shadow": "rgba(0, 0, 0, 0.4)",
    }

    LIGHT_PALETTE = {
        "window": "#f5f3eb",
        "base": "#ffffff",
        "text": "#1f2937",
        "text_secondary": "#6b7280",
        "input_text": "#1f2937",
        "highlight": "#4f46e5",
        "highlight_hover": "#6366f1",
        "highlight_glow": "rgba(79, 70, 229, 0.2)",
        "button": "#e5e5e5",
        "button_hover": "#d4d4d4",
        "button_pressed": "#c4c4c4",
        "button_gradient_start": "#4f46e5",
        "button_gradient_end": "#6366f1",
        "border": "#d1d5db",
        "border_light": "#e5e7eb",
        "disabled": "#9ca3af",
        "success": "#16a34a",
        "warning": "#d97706",
        "error": "#dc2626",
        "secondary": "#7c3aed",
        "surface": "#fafaf8",
        "surface_elevated": "#ffffff",
        "toolbar_bg": "#ebebdf",
        "plot_bg": "#ffffff",
        "plot_text": "#1f2937",
        "plot_grid": "#e5e7eb",
        "shadow": "rgba(0, 0, 0, 0.08)",
    }


def get_palette(theme_name):
    """Get palette dict for the given theme name."""
    return DARK_PALETTE if theme_name == "dark" else LIGHT_PALETTE


def get_stylesheet(theme_name):
    """Generate stylesheet for LOFAR tools matching solarviewer theme."""
    if _HAS_VIEWER_STYLES:
        return get_viewer_stylesheet(get_palette(theme_name), is_dark=(theme_name == "dark"))

    palette = get_palette(theme_name)
    is_dark = theme_name == "dark"

    # Minimal fallback logic mirrored from styles.py if import fails
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        suffix = "_light" if not is_dark else ""
        arrow_up = os.path.join(os.path.dirname(base_dir), "assets", f"spinbox_up{suffix}.png").replace("\\", "/")
        arrow_down = os.path.join(os.path.dirname(base_dir), "assets", f"spinbox_down{suffix}.png").replace("\\", "/")
    except:
        arrow_up = arrow_down = ""

    input_bg = palette["base"]
    input_text = palette.get("input_text", palette["text"])
    highlight = palette["highlight"]
    surface = palette["surface"]
    border = palette["border"]
    border_light = palette.get("border_light", palette["border"])
    surface_elevated = palette.get("surface_elevated", palette["surface"])
    text_secondary = palette.get("text_secondary", palette["disabled"])

    return f"""
    /* ===== GLOBAL STYLES ===== */
    QWidget {{
        font-family: 'Inter', 'Noto Emoji', 'Segoe UI Emoji', 'Apple Color Emoji', 'Segoe UI', 'SF Pro Display', -apple-system, Arial, sans-serif;
        font-size: 11pt;
        color: {palette['text']};
    }}
    
    QMainWindow, QDialog, QScrollArea {{
        background-color: {palette['window']};
    }}
    
    QScrollArea > QWidget {{
        background-color: transparent;
    }}
    
    /* ===== GROUP BOXES ===== */
    QGroupBox {{
        background-color: {palette['surface']};
        border: 1px solid {border};
        border-radius: 10px;
        margin-top: 10px;
        padding: 12px 12px 12px 12px;
        font-weight: 600;
    }}
    
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 16px;
        padding: 2px 10px;
        color: {highlight};
        background-color: {palette['surface']};
        border-radius: 4px;
        font-weight: 700;
        font-size: 10pt;
        letter-spacing: 0.5px;
        text-transform: uppercase;
    }}
    
    /* ===== BUTTONS ===== */
    QPushButton {{
        background-color: {palette['button']};
        color: {palette['text']};
        border: 1px solid {palette['border']};
        border-radius: 6px;
        padding: 4px 12px;
        min-width: 60px;
        min-height: 26px;
        font-size: 10pt;
        font-weight: 500;
    }}
    
    QPushButton:hover {{
        background-color: {palette['button_hover']};
        border-color: {highlight};
    }}
    
    QPushButton:pressed, QPushButton:checked {{
        background-color: {highlight};
        color: white;
        border-color: {highlight};
    }}
    
    QPushButton:disabled {{
        background-color: {palette['button']};
        color: {palette['disabled']};
        border-color: {palette['border']};
        opacity: 0.6;
    }}
    
    /* ===== INPUT FIELDS ===== */
    QLineEdit, QSpinBox, QDoubleSpinBox {{
        background-color: {input_bg};
        color: {input_text};
        border: 1px solid {palette['border']};
        border-radius: 5px;
        padding: 4px 8px;
        min-height: 24px;
        font-size: 11pt;
    }}
    
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border-color: {highlight};
        border-width: 2px;
        background-color: {surface_elevated};
    }}
    
    /* ===== TAB WIDGET ===== */
    QTabWidget::pane {{
        border: 1px solid {palette['border']};
        border-radius: 10px;
        background-color: {palette['surface']};
        padding: 4px;
    }}
    
    QTabBar::tab {{
        background: {palette['button']};
        color: {palette['text']};
        padding: 10px 24px;
        border-top-left-radius: 10px;
        border-top-right-radius: 10px;
        margin-right: 3px;
        font-size: 10pt;
        font-weight: 500;
        border: 1px solid {palette['border']};
        border-bottom: none;
    }}
    
    QTabBar::tab:selected {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {highlight}, stop:1 {palette.get('button_gradient_start', highlight)});
        color: #ffffff;
        font-weight: 600;
        border-color: {highlight};
    }}
    
    QTabBar::tab:hover:!selected {{
        background: {palette['button_hover']};
        border-color: {highlight};
    }}

    /* ===== TABLE WIDGET ===== */
    QTableWidget {{
        font-size: 11pt;
        background-color: {palette['base']};
        alternate-background-color: {palette['surface']};
        gridline-color: {border_light};
        border: 1px solid {palette['border']};
        border-radius: 10px;
        selection-background-color: {highlight};
    }}
    
    QTableWidget QHeaderView::section:horizontal {{
        background-color: {palette['button']};
        color: {palette['text']};
        font-size: 10pt;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        padding: 10px 8px;
        border: none;
        border-bottom: 2px solid {highlight};
    }}
    
    QTableWidget QHeaderView::section:vertical {{
        background-color: {palette['button']};
        color: {palette['text']};
        padding: 8px;
        border: none;
        border-right: 1px solid {palette['border']};
        border-bottom: 1px solid {palette['border']};
    }}
    
    /* ===== SCROLL BARS ===== */
    QScrollBar:vertical {{
        background: {palette['window']};
        width: 12px;
        border-radius: 6px;
        margin: 0;
    }}
    
    QScrollBar::handle:vertical {{
        background: {palette['button']};
        min-height: 30px;
        border-radius: 6px;
        margin: 2px;
    }}
    
    QScrollBar::handle:vertical:hover {{
        background: {palette['button_hover']};
    }}
    
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    
    QScrollBar:horizontal {{
        background: {palette['window']};
        height: 12px;
        border-radius: 6px;
        margin: 0;
    }}
    
    QScrollBar::handle:horizontal {{
        background: {palette['button']};
        min-width: 30px;
        border-radius: 6px;
        margin: 2px;
    }}
    
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0;
    }}
    
    /* ===== TOOLBAR ===== */
    QToolBar {{
        background-color: {palette.get('toolbar_bg', palette['surface'])};
        border: none;
        padding: 2px;
        spacing: 2px;
        max-height: 32px;
    }}
    
    QToolButton {{
        background-color: transparent;
        color: {palette['text']};
        border: none;
        border-radius: 4px;
        padding: 4px;
        max-height: 24px;
    }}
    
    QToolButton:hover {{
        background-color: {palette['button_hover']};
    }}
    
    QToolButton:pressed {{
        background-color: {palette['button_pressed']};
    }}
    
    QToolButton:checked {{
        background-color: {palette['highlight']};
    }}
    
    /* ===== STATUS BAR ===== */
    QStatusBar {{
        background-color: {surface};
        color: {palette['text']};
        border-top: 1px solid {border};
        font-size: 10pt;
    }}
    """


def get_matplotlib_params(theme_name):
    """Get matplotlib rcParams for the given theme."""
    if _HAS_VIEWER_STYLES:
        return get_viewer_matplotlib_params(get_palette(theme_name), is_dark=(theme_name == "dark"))

    palette = get_palette(theme_name)
    is_dark = theme_name == "dark"

    if is_dark:
        return {
            "figure.facecolor": palette["window"],
            "axes.facecolor": palette["base"],
            "axes.edgecolor": palette["text"],
            "axes.labelcolor": palette["text"],
            "xtick.color": palette["text"],
            "ytick.color": palette["text"],
            "grid.color": palette["border"],
            "text.color": palette["text"],
            "legend.facecolor": palette["base"],
            "legend.edgecolor": palette["border"],
            "figure.edgecolor": palette["border"],
            "axes.linewidth": 1.4,
            "font.size": 12,
        }
    else:
        return {
            "figure.facecolor": palette.get("plot_bg", "#ffffff"),
            "axes.facecolor": palette.get("plot_bg", "#ffffff"),
            "axes.edgecolor": palette.get("plot_text", "#1a1a1a"),
            "axes.labelcolor": palette.get("plot_text", "#1a1a1a"),
            "xtick.color": palette.get("plot_text", "#1a1a1a"),
            "ytick.color": palette.get("plot_text", "#1a1a1a"),
            "grid.color": palette.get("plot_grid", "#cccccc"),
            "text.color": palette.get("plot_text", "#1a1a1a"),
            "legend.facecolor": palette.get("plot_bg", "#ffffff"),
            "legend.edgecolor": palette.get("border", "#b8b8bc"),
            "figure.edgecolor": palette.get("border", "#b8b8bc"),
            "axes.linewidth": 1.4,
            "font.size": 12,
        }


def apply_theme(app, theme_name="dark"):
    """Apply theme to a QApplication instance."""
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtGui import QPalette, QColor
    from matplotlib import rcParams

    # Load fonts first
    try:
        load_bundled_fonts()
    except Exception as e:
        print(f"Warning: Could not load bundled fonts: {e}")

    # Apply stylesheet
    app.setStyleSheet(get_stylesheet(theme_name))

    # Apply matplotlib params
    import matplotlib.style as mplstyle
    mplstyle.use("fast")
    rcParams.update(get_matplotlib_params(theme_name))

    # Set palette for native widgets
    palette = get_palette(theme_name)
    qt_palette = QPalette()
    qt_palette.setColor(QPalette.Window, QColor(palette["window"]))
    qt_palette.setColor(QPalette.WindowText, QColor(palette["text"]))
    qt_palette.setColor(QPalette.Base, QColor(palette["base"]))
    qt_palette.setColor(QPalette.Text, QColor(palette["text"]))
    qt_palette.setColor(QPalette.Button, QColor(palette["button"]))
    qt_palette.setColor(QPalette.ButtonText, QColor(palette["text"]))
    qt_palette.setColor(QPalette.Highlight, QColor(palette["highlight"]))
    qt_palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(qt_palette)


def get_theme_from_args():
    """Get theme name from command line arguments."""
    for i, arg in enumerate(sys.argv):
        if arg == "--theme" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return "dark"  # Default to dark theme

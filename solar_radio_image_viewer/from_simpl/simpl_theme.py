"""
Shared theme module for LOFAR/SIMPL tools.

This module provides theme support for LOFAR tools launched as separate processes
from the Solar Radio Image Viewer. It reuses the same palettes and stylesheets
to ensure visual consistency.
"""

import sys

# Try to import palettes from main viewer styles for consistency
try:
    from ..styles import DARK_PALETTE, LIGHT_PALETTE, theme_manager
    _HAS_VIEWER_STYLES = True
except ImportError:
    _HAS_VIEWER_STYLES = False
    theme_manager = None
    
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
    # Always use the theme_name argument to determine which palette to return
    # The palettes are already imported from styles.py if available, so we get consistent colors
    return DARK_PALETTE if theme_name == "dark" else LIGHT_PALETTE



def get_stylesheet(theme_name):
    """Generate stylesheet for LOFAR tools matching solarviewer theme."""
    palette = get_palette(theme_name)
    is_dark = theme_name == "dark"
    
    input_bg = palette["base"]
    input_text = palette.get("input_text", palette["text"])
    
    return f"""
    QWidget {{
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 11pt;
        color: {palette['text']};
    }}
    
    QMainWindow, QDialog {{
        background-color: {palette['window']};
    }}
    
    QGroupBox {{
        background-color: {palette['surface']};
        border: 1px solid {palette['border']};
        border-radius: 8px;
        margin-top: 16px;
        padding: 12px 8px 8px 8px;
        font-weight: 600;
    }}
    
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 14px;
        padding: 0 6px;
        color: {palette['text']};
    }}
    
    QPushButton {{
        background-color: {palette['button']};
        color: {palette['text']};
        border: 1px solid {palette['border']};
        border-radius: 6px;
        padding: 6px 14px;
        min-width: 80px;
        min-height: 28px;
    }}
    
    QPushButton:hover {{
        background-color: {palette['button_hover']};
        border-color: {palette['highlight']};
    }}
    
    QPushButton:pressed {{
        background-color: {palette['button_pressed']};
    }}
    
    QPushButton:disabled {{
        background-color: {palette['disabled']};
        color: {'#666666' if is_dark else '#aaaaaa'};
        border: 1px dashed {'#555555' if is_dark else '#cccccc'};
    }}
    
    QLineEdit, QSpinBox, QDoubleSpinBox {{
        background-color: {input_bg};
        color: {input_text};
        border: 1px solid {palette['border']};
        border-radius: 6px;
        padding: 6px 10px;
        min-height: 28px;
    }}
    
    QLineEdit:focus, QSpinBox:focus {{
        border-color: {palette['highlight']};
    }}
    
    QComboBox {{
        background-color: {input_bg};
        color: {input_text};
        border: 1px solid {palette['border']};
        border-radius: 6px;
        padding: 6px 10px;
        min-height: 28px;
    }}
    
    QComboBox QAbstractItemView {{
        background-color: {palette['surface']};
        color: {palette['text']};
        selection-background-color: {palette['highlight']};
    }}
    
    QTableWidget {{
        background-color: {palette['base']};
        alternate-background-color: {palette['surface']};
        gridline-color: {palette['border']};
        border: 1px solid {palette['border']};
    }}
    
    QTableWidget::item:selected {{
        background-color: {palette['highlight']};
        color: #ffffff;
    }}
    
    QHeaderView::section {{
        background-color: {palette['button']};
        color: {palette['text']};
        padding: 8px;
        border: none;
        border-bottom: 1px solid {palette['border']};
    }}
    
    QLabel {{
        color: {palette['text']};
    }}
    
    QCheckBox {{
        color: {palette['text']};
        spacing: 8px;
    }}
    
    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border: 2px solid {palette['border']};
        border-radius: 4px;
        background-color: {palette['base']};
    }}
    
    QCheckBox::indicator:checked {{
        background-color: {palette['highlight']};
        border-color: {palette['highlight']};
    }}
    
    QMenuBar {{
        background-color: {palette['window']};
        color: {palette['text']};
    }}
    
    QMenuBar::item:selected {{
        background-color: {palette['button_hover']};
    }}
    
    QMenu {{
        background-color: {palette['surface']};
        color: {palette['text']};
        border: 1px solid {palette['border']};
    }}
    
    QMenu::item:selected {{
        background-color: {palette['highlight']};
        color: #ffffff;
    }}
    
    QToolBar {{
        background-color: {palette.get('toolbar_bg', palette['surface'])};
        border: none;
        padding: 4px;
    }}
    
    QToolButton {{
        background-color: transparent;
        color: {palette['text']};
        border: none;
        border-radius: 6px;
        padding: 6px;
    }}
    
    QToolButton:hover {{
        background-color: {palette['button_hover']};
    }}
    
    QToolButton:checked {{
        background-color: {palette['highlight']};
    }}
    
    QStatusBar {{
        background-color: {palette['surface']};
        color: {palette['text']};
        border-top: 1px solid {palette['border']};
    }}
    
    QScrollBar:vertical {{
        background: {palette['window']};
        width: 12px;
    }}
    
    QScrollBar::handle:vertical {{
        background: {palette['button']};
        min-height: 30px;
        border-radius: 6px;
    }}
    
    QScrollBar:horizontal {{
        background: {palette['window']};
        height: 12px;
    }}
    
    QScrollBar::handle:horizontal {{
        background: {palette['button']};
        min-width: 30px;
        border-radius: 6px;
    }}
    
    QProgressBar {{
        background-color: {palette['base']};
        border: 1px solid {palette['border']};
        border-radius: 6px;
        text-align: center;
        color: {palette['text']};
    }}
    
    QProgressBar::chunk {{
        background-color: {palette['highlight']};
        border-radius: 5px;
    }}
    
    QSlider::groove:horizontal {{
        height: 6px;
        background: {palette['border']};
        border-radius: 3px;
    }}
    
    QSlider::handle:horizontal {{
        background: {palette['highlight']};
        width: 18px;
        height: 18px;
        margin: -6px 0;
        border-radius: 9px;
    }}
    """


def get_matplotlib_params(theme_name):
    """Get matplotlib rcParams for the given theme."""
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
        }


def apply_theme(app, theme_name="dark"):
    """Apply theme to a QApplication instance."""
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtGui import QPalette, QColor
    from matplotlib import rcParams
    
    # Apply stylesheet
    app.setStyleSheet(get_stylesheet(theme_name))
    
    # Apply matplotlib params
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

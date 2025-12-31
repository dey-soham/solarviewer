"""
Shared theme module for LOFAR/SIMPL tools.

This module provides theme support for LOFAR tools launched as separate processes
from the Solar Radio Image Viewer. It reuses the same palettes and stylesheets
to ensure visual consistency.
"""

import sys

# Theme palettes matching solarviewer's styles.py
DARK_PALETTE = {
    "window": "#1a1a2e",
    "base": "#16213e",
    "text": "#eeeeee",
    "highlight": "#e94560",
    "highlight_hover": "#ff6b6b",
    "button": "#0f3460",
    "button_hover": "#1a4a7a",
    "button_pressed": "#0a2540",
    "border": "#2a3f5f",
    "disabled": "#4a4a6a",
    "success": "#2ecc71",
    "warning": "#f39c12",
    "error": "#e74c3c",
    "secondary": "#533483",
    "surface": "#1f2940",
}

LIGHT_PALETTE = {
    "window": "#A59D84",
    "base": "#ECEBDE",
    "text": "#1a1a1a",
    "input_text": "#1a1a1a",
    "highlight": "#0066cc",
    "highlight_hover": "#0052a3",
    "button": "#f0f0f0",
    "button_hover": "#e0e0e0",
    "button_pressed": "#d0d0d0",
    "border": "#b0b0b0",
    "disabled": "#999999",
    "success": "#28a745",
    "warning": "#ffc107",
    "error": "#dc3545",
    "secondary": "#6c5ce7",
    "surface": "#ECEBDE",
    "toolbar_bg": "#D7D3BF",
    "plot_bg": "#ECEBDE",
    "plot_text": "#1a1a1a",
    "plot_grid": "#d0d0d0",
}


def get_palette(theme_name):
    """Get palette dict for the given theme name."""
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

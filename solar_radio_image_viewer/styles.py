# Theme palettes for the Solar Radio Image Viewer
# Supports both dark and light modes with modern, premium styling

import os
from PyQt5.QtGui import QFontDatabase, QFont
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QAbstractButton, QComboBox, QTabBar, QMenu, QMenuBar, QWidget

# Global flag to track if fonts are loaded
_fonts_loaded = False
_inter_font_family = "Inter"


def load_bundled_fonts():
    """Load bundled Inter font from assets folder for consistent appearance."""
    global _fonts_loaded, _inter_font_family

    if _fonts_loaded:
        return _inter_font_family

    font_files = [
        "Inter-Regular.ttf",
        "Inter-Medium.ttf",
        "Inter-SemiBold.ttf",
        "Inter-Bold.ttf",
    ]

    font_db = QFontDatabase()
    loaded_any = False

    for font_file in font_files:
        try:
            # Use os.path instead of pkg_resources for speed
            base_dir = os.path.dirname(os.path.abspath(__file__))
            font_path = os.path.join(base_dir, "assets", font_file)

            font_id = font_db.addApplicationFont(font_path)
            if font_id != -1:
                families = font_db.applicationFontFamilies(font_id)
                if families:
                    _inter_font_family = families[0]
                    loaded_any = True
        except Exception as e:
            print(f"Could not load font {font_file}: {e}")

    # Load Noto Emoji as fallback for emoji characters
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        emoji_path = os.path.join(base_dir, "assets", "NotoEmoji-Regular.ttf")
        font_db.addApplicationFont(emoji_path)
    except Exception as e:
        print(f"Could not load emoji font: {e}")

    _fonts_loaded = True

    if loaded_any:
        # Set Inter as the default application font
        app = QApplication.instance()
        if app:
            font = QFont(_inter_font_family, 10)
            app.setFont(font)

    return _inter_font_family


DARK_PALETTE = {
    "window": "#0f0f1a",  # Deep space black with blue undertone
    "base": "#1a1a2e",  # Rich dark navy for inputs
    "text": "#f0f0f5",  # Soft bright white for readability
    "text_secondary": "#a0a0b0",  # Muted text for secondary info
    "highlight": "#6366f1",  # Modern indigo accent (primary)
    "highlight_hover": "#818cf8",  # Lighter indigo for hover
    "highlight_glow": "rgba(99, 102, 241, 0.3)",  # Glow effect
    "button": "#252542",  # Elevated button background
    "button_hover": "#32325d",  # Button hover state
    "button_pressed": "#1a1a35",  # Button pressed state
    "button_gradient_start": "#3730a3",  # Gradient button start
    "button_gradient_end": "#4f46e5",  # Gradient button end
    "border": "#2d2d4a",  # Subtle visible border
    "border_light": "#3d3d5c",  # Lighter border for separators
    "disabled": "#4a4a6a",
    "success": "#22c55e",  # Modern green
    "warning": "#f59e0b",  # Warm amber
    "error": "#ef4444",  # Bright red
    "secondary": "#8b5cf6",  # Purple accent
    "surface": "#16162a",  # Elevated surfaces (cards, groups)
    "surface_elevated": "#1e1e3a",  # More elevated surfaces
    "shadow": "rgba(0, 0, 0, 0.4)",  # Shadow color
}

LIGHT_PALETTE = {
    "window": "#f5f3eb",  # Warm off-white background
    "base": "#ffffff",  # Pure white for inputs
    "text": "#1f2937",  # Rich dark gray for readability
    "text_secondary": "#6b7280",  # Muted gray for secondary text
    "input_text": "#1f2937",  # Dark text for inputs
    "highlight": "#4f46e5",  # Modern indigo (matches dark theme)
    "highlight_hover": "#6366f1",  # Hover state
    "highlight_glow": "rgba(79, 70, 229, 0.2)",  # Glow effect
    "button": "#e5e5e5",  # Subtle gray buttons
    "button_hover": "#d4d4d4",  # Button hover
    "button_pressed": "#c4c4c4",  # Button pressed
    "button_gradient_start": "#4f46e5",  # Gradient button start
    "button_gradient_end": "#6366f1",  # Gradient button end
    "border": "#d1d5db",  # Soft gray border
    "border_light": "#e5e7eb",  # Lighter border for separators
    "disabled": "#9ca3af",
    "success": "#16a34a",  # Forest green
    "warning": "#d97706",  # Rich amber
    "error": "#dc2626",  # Alert red
    "secondary": "#7c3aed",  # Purple accent
    "surface": "#fafaf8",  # Slightly elevated surface
    "surface_elevated": "#ffffff",  # Most elevated (cards)
    "toolbar_bg": "#ebebdf",  # Warm toolbar
    "plot_bg": "#ffffff",
    "plot_text": "#1f2937",
    "plot_grid": "#e5e7eb",
    "shadow": "rgba(0, 0, 0, 0.08)",  # Subtle shadow for light theme
}


def get_stylesheet(palette, is_dark=True):
    """Generate the complete stylesheet for the given palette."""

    # Get asset path for arrow images
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        suffix = "_light" if not is_dark else ""

        arrow_up = os.path.join(base_dir, "assets", f"spinbox_up{suffix}.png").replace(
            "\\", "/"
        )
        arrow_down = os.path.join(
            base_dir, "assets", f"spinbox_down{suffix}.png"
        ).replace("\\", "/")

    except Exception:
        arrow_up = ""
        arrow_down = ""

    # Adjust some colors based on theme
    input_bg = palette["base"]
    input_text = palette.get("input_text", palette["text"])
    group_border = palette["border"]
    tab_selected_bg = palette["highlight"]
    hover_text = "#ffffff" if is_dark else palette["text"]
    surface_elevated = palette.get("surface_elevated", palette["surface"])
    shadow = palette.get("shadow", "rgba(0,0,0,0.2)")
    text_secondary = palette.get("text_secondary", palette["disabled"])
    border_light = palette.get("border_light", palette["border"])

    return f"""
    /* ===== GLOBAL STYLES ===== */
    QWidget {{
        font-family: 'Inter', 'Noto Emoji', 'Segoe UI Emoji', 'Apple Color Emoji', 'Segoe UI', 'SF Pro Display', -apple-system, Arial, sans-serif;
        font-size: 11pt;
        color: {palette['text']};
    }}
    
    QMainWindow {{
        background-color: {palette['window']};
    }}
    
    /* ===== GROUP BOXES ===== */
    QGroupBox {{
        background-color: {palette['surface']};
        border: 1px solid {group_border};
        border-radius: 10px;
        margin-top: 10px;
        padding: 12px 12px 12px 12px;
        font-weight: 600;
    }}
    
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 16px;
        padding: 2px 10px;
        color: {palette['highlight']};
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
        border-color: {palette['highlight']};
    }}

    QPushButton:pressed, QPushButton:checked {{
        background-color: {palette['highlight']};
        /*background-color: {palette['button_pressed']};*/
        color: #ffffff;
        border-color: {palette['highlight']};
    }}
    
    QPushButton:disabled {{
        color: {palette['disabled']};
        background-color: {palette['button']};
        border-color: {palette['border']};
        opacity: 0.6;
    }}
    
    /* Primary action button style */
    QPushButton#PrimaryButton {{
        background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, 
            stop:0 {palette.get('button_gradient_start', palette['highlight'])}, 
            stop:1 {palette.get('button_gradient_end', palette['highlight_hover'])});
        color: #ffffff;
        border: none;
        font-weight: 600;
        letter-spacing: 0.3px;
    }}
    
    QPushButton#PrimaryButton:hover {{
        background-color: {palette['highlight_hover']};
    }}
    
    QPushButton#PrimaryButton:disabled {{
        background-color: {palette['disabled']};
        color: {border_light};
    }}

    QPushButton#IconOnlyButton {{
        min-width: 32px;
        max-width: 32px;
        min-height: 32px;
        max-height: 32px;
        padding: 6px;
        border-radius: 8px;
        border: 1px solid {border_light};
    }}
    
    QPushButton#IconOnlyButton:hover {{
        background-color: {palette['highlight']};
        border-color: {palette['highlight']};
    }}

    QPushButton#IconOnlyNBGButton {{
        background-color: transparent;
        border: none;
        padding: 8px;
        margin: 0px;
        min-width: 0px;
        min-height: 0px;
        border-radius: 8px;
    }}

    QPushButton#IconOnlyNBGButton:hover {{
        background-color: {palette['button_hover']};
    }}

    QPushButton#IconOnlyNBGButton:pressed {{
        background-color: {palette['button_pressed']};
    }}
    
    /* ===== INPUT FIELDS ===== */
    QLineEdit, QSpinBox, QDoubleSpinBox {{
        background-color: {input_bg};
        color: {input_text};
        border: 1px solid {palette['border']};
        border-radius: 5px;
        padding: 2px 6px;
        min-height: 20px;
        font-size: 11pt;
        selection-background-color: {palette['highlight']};
    }}
    
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border-color: {palette['highlight']};
        border-width: 2px;
        background-color: {surface_elevated};
    }}
    
    QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
        background-color: {palette['surface']};
        color: {palette['disabled']};
        opacity: 0.7;
    }}
    
    QLineEdit::placeholder {{
        color: {text_secondary};
        font-style: italic;
    }}
    
    /* Spinbox buttons with image-based arrows */
    QSpinBox::up-button, QDoubleSpinBox::up-button {{
        subcontrol-origin: border;
        subcontrol-position: top right;
        width: 16px;
        border-left: 1px solid {border_light};
        border-top-right-radius: 4px;
        background-color: {palette['button']};
    }}
    
    QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover {{
        background-color: {palette['button_hover']};
    }}
    
    QSpinBox::down-button, QDoubleSpinBox::down-button {{
        subcontrol-origin: border;
        subcontrol-position: bottom right;
        width: 16px;
        border-left: 1px solid {border_light};
        border-bottom-right-radius: 4px;
        background-color: {palette['button']};
    }}
    
    QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
        background-color: {palette['button_hover']};
    }}
    
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
        image: url({arrow_up});
        width: 10px;
        height: 6px;
    }}
    
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
        image: url({arrow_down});
        width: 10px;
        height: 6px;
    }}
    
    QComboBox {{
        background-color: {input_bg};
        color: {input_text};
        border: 1px solid {palette['border']};
        border-radius: 5px;
        padding: 2px 6px;
        min-height: 20px;
        font-size: 11pt;
    }}
    
    QComboBox:hover {{
        border-color: {palette['highlight']};
    }}
    
    QComboBox:disabled {{
        background-color: {palette['surface']};
        color: {palette['disabled']};
        opacity: 0.7;
    }}
    
    QComboBox:focus {{
        border-color: {palette['highlight']};
        border-width: 2px;
    }}
    
    /*QComboBox::drop-down {{
        border: none;
        width: 28px;
        border-left: 1px solid {border_light};
        border-top-right-radius: 8px;
        border-bottom-right-radius: 8px;
    }}
    
    QComboBox::down-arrow {{
        width: 12px;
        height: 12px;
    }}*/
    
    QComboBox QAbstractItemView {{
        background-color: {surface_elevated};
        color: {palette['text']};
        border: 1px solid {palette['border']};
        border-radius: 8px;
        padding: 4px;
        selection-background-color: {palette['highlight']};
        selection-color: #ffffff;
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
            stop:0 {palette['highlight']}, stop:1 {palette.get('button_gradient_start', palette['highlight'])});
        color: #ffffff;
        font-weight: 600;
        border-color: {palette['highlight']};
    }}
    
    QTabBar::tab:hover:!selected {{
        background: {palette['button_hover']};
        border-color: {palette['highlight']};
    }}
    
    /* ===== TABLE WIDGET ===== */
    QTableWidget {{
        font-size: 11pt;
        background-color: {palette['base']};
        alternate-background-color: {palette['surface']};
        gridline-color: {border_light};
        border: 1px solid {palette['border']};
        border-radius: 10px;
        selection-background-color: {palette['highlight']};
    }}
    
    QTableWidget QHeaderView::section {{
        background-color: {palette['button']};
        color: {palette['text']};
        font-size: 10pt;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        padding: 10px 8px;
        border: none;
        border-bottom: 2px solid {palette['highlight']};
    }}
    
    QTableWidget QHeaderView::section:vertical {{
        background-color: {palette['button']};
        color: {palette['text']};
        padding: 8px;
        border: none;
        border-right: 1px solid {palette['border']};
        border-bottom: 1px solid {palette['border']};
    }}
    
    QTableWidget QHeaderView::section:hover {{
        background-color: {palette['button_hover']};
    }}
    
    QTableWidget::item {{
        padding: 8px 6px;
        border-bottom: 1px solid {border_light};
    }}
    
    QTableWidget::item:selected {{
        background-color: {palette['highlight']};
        color: #ffffff;
    }}
    
    QTableWidget::item:hover {{
        background-color: {palette['button_hover']};
    }}
    
    /* ===== LABELS ===== */
    QLabel {{
        font-size: 11pt;
        color: {palette['text']};
    }}

    QLabel:disabled {{
        color: {palette['disabled']};
    }}
    
    /* Status label - for displaying status messages */
    QLabel#StatusLabel {{
        padding: 8px 12px;
        background-color: {palette['surface']};
        border: 1px solid {palette['border']};
        border-radius: 6px;
        font-size: 10pt;
    }}
    
    /* Secondary text - for hints and descriptions */
    QLabel#SecondaryText {{
        color: {palette['disabled']};
        font-style: italic;
        font-size: 10pt;
    }}
    
    /* ===== CHECKBOXES & RADIO BUTTONS ===== */
    QCheckBox {{
        font-size: 11pt;
        min-height: 24px;
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
    
    QCheckBox::indicator:hover {{
        border-color: {palette['highlight']};
    }}
    
    QRadioButton {{
        font-size: 11pt;
        min-height: 24px;
        spacing: 8px;
    }}
    
    QRadioButton::indicator {{
        width: 18px;
        height: 18px;
        border: 2px solid {palette['border']};
        border-radius: 9px;
        background-color: {palette['base']};
    }}
    
    QRadioButton::indicator:checked {{
        background-color: {palette['highlight']};
        border-color: {palette['highlight']};
    }}
    
    /* ===== SLIDERS ===== */
    QSlider {{
        min-height: 28px;
    }}
    
    QSlider::groove:horizontal {{
        height: 6px;
        background: {palette['border']};
        border-radius: 3px;
    }}
    
    QSlider::groove:horizontal:disabled {{
        background: {palette['surface']};
    }}
    
    QSlider::handle:horizontal {{
        background: {palette['highlight']};
        width: 18px;
        height: 18px;
        margin: -6px 0;
        border-radius: 9px;
    }}
    
    QSlider::handle:horizontal:disabled {{
        background: {palette['disabled']};
        border: 1px solid {palette['border']};
    }}
    
    QSlider::handle:horizontal:hover {{
        background: {palette['highlight_hover']};
    }}
    
    QSlider::sub-page:horizontal {{
        background: {palette['highlight']};
        border-radius: 3px;
    }}
    
    QSlider::sub-page:horizontal:disabled {{
        background: {palette['disabled']};
    }}
    
    /* ===== MENU BAR ===== */
    QMenuBar {{
        background-color: {palette['window']};
        color: {palette['text']};
        padding: 4px;
        font-size: 11pt;
    }}
    
    QMenuBar::item {{
        padding: 6px 12px;
        border-radius: 4px;
    }}
    
    QMenuBar::item:selected {{
        background-color: {palette['button_hover']};
    }}
    
    QMenu {{
        background-color: {palette['surface']};
        color: {palette['text']};
        border: 1px solid {palette['border']};
        border-radius: 8px;
        padding: 6px;
    }}
    
    QMenu::item {{
        padding: 8px 32px 8px 16px;
        border-radius: 4px;
    }}
    
    QMenu::item:selected {{
        background-color: {palette['highlight']};
        color: #ffffff;
    }}
    
    QMenu::separator {{
        height: 1px;
        background: {palette['border']};
        margin: 6px 12px;
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
        background-color: {"#555555" if not is_dark and 'toolbar_bg' in palette else palette['button_hover']};
    }}
    
    QToolButton:pressed {{
        background-color: {"#333333" if not is_dark and 'toolbar_bg' in palette else palette['button_pressed']};
    }}
    
    QToolButton:checked {{
        background-color: {palette['highlight']};
    }}
    
    /* ===== PROGRESS BAR ===== */
    QProgressBar {{
        border: 1px solid {palette['border']};
        border-radius: 6px;
        text-align: center;
        background-color: {palette['base']};
        color: {palette['text']};
        font-weight: bold;
    }}

    QProgressBar::chunk {{
        background-color: {palette['highlight']};
        border-radius: 5px;
    }}

    /* ===== STATUS BAR ===== */
    QStatusBar {{
        background-color: {palette['surface']};
        color: {palette['text']};
        font-size: 10pt;
        border-top: 1px solid {palette['border']};
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
    
    QScrollBar::handle:horizontal:hover {{
        background: {palette['button_hover']};
    }}
    
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0;
    }}
    
    /* ===== DIALOGS ===== */
    QDialog {{
        background-color: {palette['window']};
    }}
    
    QDialogButtonBox QPushButton {{
        min-width: 90px;
    }}
    
    /* ===== MESSAGE BOX ===== */
    QMessageBox {{
        background-color: {palette['window']};
    }}
    
    /* ===== SPLITTER ===== */
    QSplitter::handle {{
        background-color: {palette['border']};
    }}
    
    QSplitter::handle:horizontal {{
        width: 2px;
    }}
    
    QSplitter::handle:vertical {{
        height: 2px;
    }}
    
    /* ===== FRAME ===== */
    QFrame {{
        border-radius: 4px;
    }}
"""


def get_matplotlib_params(palette, is_dark=True):
    """Get matplotlib rcParams for the given palette."""
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
        # Light mode - use white background, dark text
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


class ThemeManager:
    """Manages theme switching for the application."""

    DARK = "dark"
    LIGHT = "light"

    def __init__(self):
        self._current_theme = self.DARK
        self._callbacks = []
        self._fonts_initialized = False

    def initialize_fonts(self):
        """Initialize bundled fonts. Call after QApplication is created."""
        if not self._fonts_initialized:
            load_bundled_fonts()
            self._fonts_initialized = True

    @property
    def current_theme(self):
        return self._current_theme

    @property
    def is_dark(self):
        return self._current_theme == self.DARK

    @property
    def palette(self):
        return DARK_PALETTE if self.is_dark else LIGHT_PALETTE

    @property
    def stylesheet(self):
        return get_stylesheet(self.palette, self.is_dark)

    @property
    def matplotlib_params(self):
        return get_matplotlib_params(self.palette, self.is_dark)

    def set_theme(self, theme):
        """Set the current theme."""
        if theme not in (self.DARK, self.LIGHT):
            raise ValueError(f"Invalid theme: {theme}")

        if theme != self._current_theme:
            self._current_theme = theme
            self._notify_callbacks()

    def toggle_theme(self):
        """Toggle between dark and light themes."""
        new_theme = self.LIGHT if self.is_dark else self.DARK
        self.set_theme(new_theme)
        return new_theme

    def register_callback(self, callback):
        """Register a callback to be called when theme changes."""
        if callback not in self._callbacks:
            self._callbacks.append(callback)

    def unregister_callback(self, callback):
        """Unregister a theme change callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def _notify_callbacks(self):
        """Notify all registered callbacks of theme change."""
        for callback in self._callbacks:
            try:
                callback(self._current_theme)
            except Exception as e:
                print(f"Error in theme callback: {e}")


# Global theme manager instance
theme_manager = ThemeManager()

# For backward compatibility
STYLESHEET = get_stylesheet(DARK_PALETTE, is_dark=True)


def get_icon_path(icon_name):
    """Get the appropriate icon path based on current theme.

    For light mode, returns the _light version of the icon if it exists.

    Args:
        icon_name: Base icon filename (e.g., 'browse.png')

    Returns:
        Icon filename to use (e.g., 'browse.png' or 'browse_light.png')
    """
    if theme_manager.is_dark:
        return icon_name
    else:
        # Use light version for light mode
        name, ext = icon_name.rsplit(".", 1)
        return f"{name}_light.{ext}"

def set_hand_cursor(widget):
    """
    Recursively set PointingHandCursor for all buttons and interactive widgets.
    
    Args:
        widget: The root widget (e.g., a QDialog or QMainWindow)
    """
    from PyQt5.QtWidgets import QAbstractButton, QComboBox, QMenu, QMenuBar, QTabBar, QHeaderView, QSlider, QAbstractSpinBox
    
    # Check if this widget should have a hand cursor
    if isinstance(widget, (QAbstractButton, QComboBox, QMenu, QMenuBar, QTabBar, QHeaderView, QSlider, QAbstractSpinBox)):
        widget.setCursor(Qt.PointingHandCursor)
    
    # Recurse into children
    for child in widget.children():
        if isinstance(child, QWidget):
            set_hand_cursor(child)

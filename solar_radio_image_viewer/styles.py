# Theme palettes for the Solar Radio Image Viewer
# Supports both dark and light modes with modern, premium styling

DARK_PALETTE = {
    "window": "#1a1a2e",      # Deep rich dark blue
    "base": "#16213e",        # Navy undertone for inputs
    "text": "#eeeeee",         # Soft white for readability
    "highlight": "#e94560",   # Vibrant accent for primary actions
    "highlight_hover": "#ff6b6b",
    "button": "#0f3460",      # Subtle blue-gray buttons
    "button_hover": "#1a4a7a",
    "button_pressed": "#0a2540",
    "border": "#2a3f5f",      # Subtle border color
    "disabled": "#4a4a6a",
    "success": "#2ecc71",
    "warning": "#f39c12",
    "error": "#e74c3c",
    "secondary": "#533483",   # Secondary accent
    "surface": "#1f2940",     # Elevated surfaces
}

LIGHT_PALETTE = {
    #"window": "#e8e8e8",      # Light grey background - proper light theme
    "window": "#A59D84",
    #"base": "#ffffff",        # White for inputs
    "base": "#ECEBDE",
    "text": "#1a1a1a",        # Dark text for readability on light backgrounds
    "input_text": "#1a1a1a",  # Dark text for inputs (same as text in light mode)
    "highlight": "#0066cc",   # Professional blue
    "highlight_hover": "#0052a3",
    "button": "#f0f0f0",      # Light grey buttons
    "button_hover": "#e0e0e0",
    "button_pressed": "#d0d0d0",
    "border": "#b0b0b0",      # Visible border
    "disabled": "#999999",
    "success": "#28a745",
    "warning": "#ffc107",
    "error": "#dc3545",
    "secondary": "#6c5ce7",   
    #"surface": "#f5f5f5",     # Light grey surfaces
    "surface": "#ECEBDE",
    #"toolbar_bg": "#404040",  # Dark toolbar for white icons
    "toolbar_bg": "#D7D3BF",
    #"plot_bg": "#ffffff",     
    "plot_bg": "#ECEBDE",
    "plot_text": "#1a1a1a",
    "plot_grid": "#d0d0d0",
}


def get_stylesheet(palette, is_dark=True):
    """Generate the complete stylesheet for the given palette."""
    
    # Adjust some colors based on theme
    input_bg = palette["base"]
    input_text = palette.get("input_text", palette["text"])
    group_border = palette["border"]
    tab_selected_bg = palette["highlight"]
    hover_text = "#ffffff" if is_dark else palette["text"]
    
    return f"""
    /* ===== GLOBAL STYLES ===== */
    QWidget {{
        font-family: 'Segoe UI', 'SF Pro Display', -apple-system, Arial, sans-serif;
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
        font-weight: bold;
        font-size: 11pt;
    }}
    
    /* ===== BUTTONS ===== */
    QPushButton {{
        background-color: {palette['button']};
        color: {palette['text']};
        border: 1px solid {palette['border']};
        border-radius: 6px;
        padding: 6px 14px;
        min-width: 80px;
        min-height: 28px;
        font-size: 11pt;
        font-weight: 500;
    }}
    
    QPushButton:hover {{
        background-color: {palette['button_hover']};
        border-color: {palette['highlight']};
    }}

    QPushButton:pressed {{
        background-color: {palette['button_pressed']};
    }}
    
    QPushButton:disabled {{
        color: {palette['disabled']};
        background-color: {palette['button']};
        border-color: {palette['border']};
    }}
    
    /* Primary action button style */
    QPushButton#PrimaryButton {{
        background-color: {palette['highlight']};
        color: #ffffff;
        border: none;
        font-weight: 600;
    }}
    
    QPushButton#PrimaryButton:hover {{
        background-color: {palette['highlight_hover']};
    }}

    QPushButton#IconOnlyButton {{
        min-width: 30px;
        max-width: 30px;
        max-height: 30px;
        padding: 4px;
        border-radius: 6px;
    }}

    QPushButton#IconOnlyNBGButton {{
        background-color: transparent;
        border: none;
        padding: 8px;
        margin: 0px;
        min-width: 0px;
        min-height: 0px;
        border-radius: 6px;
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
        border-radius: 6px;
        padding: 6px 10px;
        min-height: 28px;
        font-size: 11pt;
        selection-background-color: {palette['highlight']};
    }}
    
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border-color: {palette['highlight']};
        border-width: 2px;
    }}
    
    QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
        background-color: {palette['surface']};
        color: {palette['disabled']};
    }}
    
    QComboBox {{
        background-color: {input_bg};
        color: {input_text};
        border: 1px solid {palette['border']};
        border-radius: 6px;
        padding: 6px 10px;
        min-height: 28px;
        font-size: 11pt;
    }}
    
    QComboBox:hover {{
        border-color: {palette['highlight']};
    }}
    
    QComboBox::drop-down {{
        border: none;
        width: 24px;
    }}
    
    QComboBox::down-arrow {{
        width: 12px;
        height: 12px;
    }}
    
    QComboBox QAbstractItemView {{
        background-color: {palette['surface']};
        color: {palette['text']};
        border: 1px solid {palette['border']};
        border-radius: 6px;
        selection-background-color: {palette['highlight']};
        selection-color: #ffffff;
    }}
    
    /* ===== TAB WIDGET ===== */
    QTabWidget::pane {{
        border: 1px solid {palette['border']};
        border-radius: 8px;
        background-color: {palette['surface']};
    }}
    
    QTabBar::tab {{
        background: {palette['button']};
        color: {palette['text']};
        padding: 10px 20px;
        border-top-left-radius: 8px;
        border-top-right-radius: 8px;
        margin-right: 2px;
        font-size: 11pt;
        font-weight: 500;
    }}
    
    QTabBar::tab:selected {{
        background: {tab_selected_bg};
        color: #ffffff;
    }}
    
    QTabBar::tab:hover:!selected {{
        background: {palette['button_hover']};
    }}
    
    /* ===== TABLE WIDGET ===== */
    QTableWidget {{
        font-size: 11pt;
        background-color: {palette['base']};
        alternate-background-color: {palette['surface']};
        gridline-color: {palette['border']};
        border: 1px solid {palette['border']};
        border-radius: 6px;
    }}
    
    QTableWidget QHeaderView::section {{
        background-color: {palette['button']};
        color: {palette['text']};
        font-size: 11pt;
        font-weight: bold;
        padding: 8px;
        border: none;
        border-bottom: 1px solid {palette['border']};
    }}
    
    QTableWidget::item {{
        padding: 6px;
    }}
    
    QTableWidget::item:selected {{
        background-color: {palette['highlight']};
        color: #ffffff;
    }}
    
    /* ===== LABELS ===== */
    QLabel {{
        font-size: 11pt;
        color: {palette['text']};
    }}
    
    /* Status label - for displaying status messages */
    QLabel#StatusLabel {{
        padding: 8px 12px;
        background-color: {palette['surface']};
        border: 1px solid {palette['border']};
        border-radius: 6px;
        font-size: 11pt;
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
    
    QSlider::handle:horizontal {{
        background: {palette['highlight']};
        width: 18px;
        height: 18px;
        margin: -6px 0;
        border-radius: 9px;
    }}
    
    QSlider::handle:horizontal:hover {{
        background: {palette['highlight_hover']};
    }}
    
    QSlider::sub-page:horizontal {{
        background: {palette['highlight']};
        border-radius: 3px;
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
        padding: 4px;
        spacing: 4px;
    }}
    
    QToolButton {{
        background-color: transparent;
        color: {"#ffffff" if not is_dark and 'toolbar_bg' in palette else palette['text']};
        border: none;
        border-radius: 6px;
        padding: 6px;
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
        }


class ThemeManager:
    """Manages theme switching for the application."""
    
    DARK = "dark"
    LIGHT = "light"
    
    def __init__(self):
        self._current_theme = self.DARK
        self._callbacks = []
    
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
        name, ext = icon_name.rsplit('.', 1)
        return f"{name}_light.{ext}"

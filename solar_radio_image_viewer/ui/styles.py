"""
The look of SolarViewer: palettes, design tokens and the stylesheet.

Everything visual comes from here. A widget is styled by what it is (its Qt
class) or by a role it is given with ``setObjectName`` (see ROLES); widgets
do not carry stylesheets of their own. Sizes are in points so they follow
the display's scale factor; the only pixel values are hairlines and
indicator images.
"""

import os
from PyQt5.QtGui import QFontDatabase, QFont
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QWidget

from .icons import PACKAGE_DIR, get_resource_path

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
            font_path = get_resource_path("assets", font_file)
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
        emoji_path = get_resource_path("assets", "NotoEmoji-Regular.ttf")
        font_db.addApplicationFont(emoji_path)
    except Exception as e:
        print(f"Could not load emoji font: {e}")

    _fonts_loaded = True

    if loaded_any:
        app = QApplication.instance()
        if app:
            app.setFont(QFont(_inter_font_family, FONT_BODY))

    return _inter_font_family


# ===== Design tokens =====
# Type scale, in points: four sizes are enough for the whole application.
FONT_SMALL = 10  # buttons, tabs, table headers, hints, status bar
FONT_BODY = 11  # everything else
FONT_SECTION = 12  # section titles inside a window
FONT_TITLE = 18  # a window's or dialog's one title
# Spacing steps, in pixels (scaled by Qt with the display)
SPACE_XS, SPACE_S, SPACE_M, SPACE_L = 4, 8, 12, 16
RADIUS = 6  # of every rounded corner
FONT_FAMILY = "'Inter', 'Noto Emoji', 'Segoe UI', -apple-system, Arial, sans-serif"
MONO_FAMILY = "monospace"  # the generic family: Qt picks the system's fixed font

TOKENS = {
    "font_small": FONT_SMALL,
    "font_body": FONT_BODY,
    "font_section": FONT_SECTION,
    "font_title": FONT_TITLE,
    "space": (SPACE_XS, SPACE_S, SPACE_M, SPACE_L),
    "radius": RADIUS,
}

# Roles a widget takes with ``widget.setObjectName(role)``:
#   PrimaryButton   the one main action of a window or dialog (accent fill)
#   IconOnlyButton  a square icon button with a border
#   IconOnlyNBGButton  a square icon button with no background
#   NavButton       a flat text/glyph button in a toolbar row
#   Switch          a QCheckBox drawn as an on/off switch
#   SectionTitle    a QLabel heading a section
#   Subheading      a QLabel heading a block inside a section
#   DialogTitle     a QLabel titling a dialog
#   SecondaryText   a QLabel hint in muted text (alias: Hint)
#   StatusLabel     a QLabel framed as a status line
#   StatusButton    a flat outlined button in the status bar
#   Badge           a QLabel pill with a short fact ("12 files")
#   Key             a QLabel drawn as a keyboard key
#   Mono            a QLabel in the monospace font (coordinates, values)
#   Tip             a QLabel of advice, marked with an accent bar
#   StepNumber      a QLabel with a step's number in an accent circle
#   Banner          a QFrame header filled with the accent (About dialog)
#   Card            a QFrame panel on the surface colour with a border
#   Rule            a QFrame drawn as a 1px horizontal line
#   Sidebar         a QFrame navigation column
#   NavList         a QListWidget of sections in a Sidebar
#   Console         a QPlainTextEdit of log text, monospace
#   ImageWell       a QFrame or QScrollArea showing a picture on black
#   Segmented       a QWidget holding a row of checkable buttons, one on
#   SectionHeader   a QPushButton heading a collapsible section
#   FoldHeader      a QToolButton heading a FoldSection (ui.widgets)
# A label or StatusButton can also take a tone with ``set_tone``:
# "success", "warning" or "error" colour it accordingly, None clears it.
ROLES = (
    "PrimaryButton",
    "IconOnlyButton",
    "IconOnlyNBGButton",
    "NavButton",
    "Switch",
    "SectionTitle",
    "Subheading",
    "DialogTitle",
    "SecondaryText",
    "Hint",
    "StatusLabel",
    "StatusButton",
    "Badge",
    "Key",
    "Mono",
    "Tip",
    "StepNumber",
    "Banner",
    "Card",
    "Rule",
    "Sidebar",
    "NavList",
    "Console",
    "ImageWell",
    "Segmented",
    "SectionHeader",
    "FoldHeader",
)

# ===== Palettes =====
# Every colour the application paints with, by role. The two palettes have
# the same keys; the accent (highlight) is the same indigo in both.
DARK_PALETTE = {
    "window": "#0f0f1a",  # window background
    "base": "#1a1a2e",  # inputs, lists, tables
    "surface": "#16162a",  # group boxes, panels, menus
    "surface_elevated": "#1e1e3a",  # popups, tooltips, focused inputs
    "button": "#252542",
    "button_hover": "#32325d",
    "button_pressed": "#1a1a35",
    "border": "#2d2d4a",
    "border_light": "#3d3d5c",  # separators, table grid
    "text": "#f0f0f5",
    "text_secondary": "#a0a0b0",
    "input_text": "#f0f0f5",
    "disabled": "#5c5c7a",
    "highlight": "#6366f1",  # the accent
    "highlight_hover": "#818cf8",
    "highlight_text": "#ffffff",  # text on the accent
    "success": "#22c55e",
    "warning": "#f59e0b",
    "error": "#ef4444",
    "secondary": "#8b5cf6",
    "switch_track": "#3a3d4d",
    "switch_track_hover": "#4a4d5d",
    "plot_bg": "#1a1a2e",
    "plot_text": "#f0f0f5",
    "plot_grid": "#2d2d4a",
    "toolbar_bg": "#16162a",
    # Kept for widgets not yet restyled: flat values of the old gradients
    "highlight_glow": "rgba(99, 102, 241, 0.25)",
    "button_gradient_start": "#6366f1",
    "button_gradient_end": "#6366f1",
    "shadow": "rgba(0, 0, 0, 0.4)",
}

LIGHT_PALETTE = {
    "window": "#f4f4f6",
    "base": "#ffffff",
    "surface": "#ffffff",
    "surface_elevated": "#ffffff",
    "button": "#e8e8ee",
    "button_hover": "#dcdce4",
    "button_pressed": "#cfcfd8",
    "border": "#d4d4dc",
    "border_light": "#e6e6ec",
    "text": "#1f2328",
    "text_secondary": "#656d76",
    "input_text": "#1f2328",
    "disabled": "#a0a4ad",
    "highlight": "#4f46e5",
    "highlight_hover": "#6366f1",
    "highlight_text": "#ffffff",
    "success": "#16a34a",
    "warning": "#d97706",
    "error": "#dc2626",
    "secondary": "#7c3aed",
    "switch_track": "#cfcfd8",
    "switch_track_hover": "#bdbdc8",
    "plot_bg": "#ffffff",
    "plot_text": "#1f2328",
    "plot_grid": "#e6e6ec",
    "toolbar_bg": "#ffffff",
    "highlight_glow": "rgba(79, 70, 229, 0.15)",
    "button_gradient_start": "#4f46e5",
    "button_gradient_end": "#4f46e5",
    "shadow": "rgba(0, 0, 0, 0.08)",
}


def palette_for(theme):
    """The palette of ``theme`` ("dark" or "light")."""
    return LIGHT_PALETTE if theme == "light" else DARK_PALETTE


def get_stylesheet(palette, is_dark=True):
    """The application stylesheet for ``palette``."""
    suffix = "" if is_dark else "_light"
    arrow_up = os.path.join(PACKAGE_DIR, "assets", f"spinbox_up{suffix}.png").replace(
        "\\", "/"
    )
    arrow_down = os.path.join(
        PACKAGE_DIR, "assets", f"spinbox_down{suffix}.png"
    ).replace("\\", "/")

    p = palette
    on_accent = p["highlight_text"]

    return f"""
    /* ===== Base ===== */
    QWidget {{
        font-family: {FONT_FAMILY};
        font-size: {FONT_BODY}pt;
        color: {p['text']};
    }}
    QMainWindow, QDialog, QMessageBox {{
        background-color: {p['window']};
    }}
    QToolTip {{
        background-color: {p['surface_elevated']};
        color: {p['text']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS}px;
        padding: {SPACE_XS}px {SPACE_S}px;
        font-size: {FONT_SMALL}pt;
    }}
    QScrollArea {{
        background-color: transparent;
        border: none;
    }}
    QScrollArea > QWidget > QWidget {{
        background-color: transparent;
    }}
    QSplitter::handle {{
        background-color: {p['border']};
    }}
    QSplitter::handle:horizontal {{
        width: 2px;
    }}
    QSplitter::handle:vertical {{
        height: 2px;
    }}

    /* ===== Text ===== */
    QLabel {{
        background-color: transparent;
    }}
    QLabel:disabled {{
        color: {p['disabled']};
    }}
    QLabel#SectionTitle {{
        font-size: {FONT_SECTION}pt;
        font-weight: 600;
    }}
    QLabel#Subheading {{
        font-weight: 600;
    }}
    QLabel#DialogTitle {{
        font-size: {FONT_TITLE}pt;
        font-weight: 600;
    }}
    QLabel#SecondaryText, QLabel#Hint {{
        color: {p['text_secondary']};
        font-size: {FONT_SMALL}pt;
    }}
    QLabel#StatusLabel {{
        padding: {SPACE_S}px {SPACE_M}px;
        background-color: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS}px;
        font-size: {FONT_SMALL}pt;
    }}
    QLabel#Mono {{
        font-family: {MONO_FAMILY};
    }}
    QLabel#Badge {{
        color: {p['text_secondary']};
        font-size: {FONT_SMALL}pt;
        padding: 2px {SPACE_S}px;
        background-color: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS}px;
    }}
    QLabel#Key {{
        font-family: {MONO_FAMILY};
        font-size: {FONT_SMALL}pt;
        font-weight: 600;
        padding: 2px {SPACE_S}px;
        background-color: {p['button']};
        border: 1px solid {p['border_light']};
        border-bottom: 2px solid {p['border_light']};
        border-radius: 4px;
    }}
    QLabel#Tip {{
        color: {p['text']};
        font-size: {FONT_SMALL}pt;
        padding: {SPACE_XS + 2}px {SPACE_M}px;
        background-color: {p['surface']};
        border-left: 3px solid {p['highlight']};
        border-radius: 0px;
    }}
    QLabel#StepNumber {{
        background-color: {p['highlight']};
        color: {p['highlight_text']};
        font-weight: 600;
        border-radius: 14px;
        min-width: 28px;
        max-width: 28px;
        min-height: 28px;
        max-height: 28px;
    }}
    QFrame#Banner {{
        background-color: {p['highlight']};
        border: none;
        border-radius: 0px;
    }}
    QFrame#Banner QLabel {{
        color: {p['highlight_text']};
        background-color: transparent;
    }}
    QFrame#Card {{
        background-color: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS}px;
    }}
    QFrame#Rule {{
        background-color: {p['border']};
        border: none;
        max-height: 1px;
        min-height: 1px;
    }}
    QFrame#Sidebar {{
        background-color: {p['surface']};
        border: none;
        border-right: 1px solid {p['border']};
    }}
    QListWidget#NavList {{
        background-color: transparent;
        border: none;
    }}
    QListWidget#NavList::item {{
        padding: {SPACE_S}px {SPACE_M}px;
        border-radius: {RADIUS}px;
        margin: 1px 0;
    }}
    QFrame#ImageWell, QScrollArea#ImageWell, QScrollArea#ImageWell > QWidget > QWidget {{
        background-color: #000000;
        border: 1px solid {p['border']};
        border-radius: {RADIUS}px;
    }}
    QFrame#ImageWell QLabel {{
        color: {p['text_secondary']};
    }}
    QWidget#Segmented, QFrame#Segmented {{
        background-color: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS}px;
    }}
    QWidget#Segmented QPushButton, QFrame#Segmented QPushButton {{
        background-color: transparent;
        color: {p['text_secondary']};
        border: none;
        border-radius: {RADIUS - 2}px;
        padding: {SPACE_XS}px {SPACE_S}px;
        min-height: 22px;
    }}
    QWidget#Segmented QPushButton:hover, QFrame#Segmented QPushButton:hover {{
        color: {p['text']};
        background-color: {p['button_hover']};
    }}
    QWidget#Segmented QPushButton:checked, QFrame#Segmented QPushButton:checked {{
        background-color: {p['highlight']};
        color: {p['highlight_text']};
        font-weight: 600;
    }}
    QToolButton#FoldHeader {{
        text-align: left;
        padding: {SPACE_XS}px 0px;
        font-size: {FONT_SECTION}pt;
        font-weight: 600;
        color: {p['text']};
        background-color: transparent;
        border: none;
        border-bottom: 1px solid {p['border']};
        border-radius: 0px;
    }}
    QToolButton#FoldHeader:hover {{
        color: {p['highlight']};
        background-color: transparent;
    }}
    QToolButton#FoldHeader:checked {{
        background-color: transparent;
        color: {p['text']};
    }}
    QPushButton#SectionHeader {{
        text-align: left;
        padding: {SPACE_S}px {SPACE_M}px;
        font-weight: 600;
        background-color: {p['button']};
        border: 1px solid {p['border']};
    }}
    QPushButton#SectionHeader:hover {{
        background-color: {p['button_hover']};
        border-color: {p['highlight']};
    }}
    QPushButton#SectionHeader:pressed {{
        background-color: {p['button_pressed']};
        color: {p['text']};
    }}
    QPlainTextEdit#Console {{
        font-family: {MONO_FAMILY};
        font-size: {FONT_SMALL}pt;
    }}
    QLabel[tone="success"] {{
        color: {p['success']};
    }}
    QLabel[tone="warning"] {{
        color: {p['warning']};
    }}
    QLabel[tone="error"] {{
        color: {p['error']};
    }}

    /* ===== Group boxes ===== */
    QGroupBox {{
        background-color: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS}px;
        margin-top: {SPACE_M}px;
        padding: {SPACE_S}px;
        font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: {SPACE_M}px;
        padding: 0 {SPACE_XS}px;
        color: {p['highlight']};
        background-color: {p['surface']};
        font-size: {FONT_BODY}pt;
        font-weight: 600;
    }}

    /* ===== Buttons ===== */
    QPushButton {{
        background-color: {p['button']};
        color: {p['text']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS}px;
        padding: {SPACE_XS}px {SPACE_M}px;
        min-height: 26px;
        font-size: {FONT_SMALL}pt;
        font-weight: 500;
    }}
    QPushButton:hover {{
        background-color: {p['button_hover']};
        border-color: {p['highlight']};
    }}
    QPushButton:pressed, QPushButton:checked {{
        background-color: {p['highlight']};
        color: {on_accent};
        border-color: {p['highlight']};
    }}
    QPushButton:disabled {{
        color: {p['disabled']};
        background-color: {p['button']};
        border-color: {p['border']};
    }}
    QPushButton#PrimaryButton {{
        background-color: {p['highlight']};
        color: {on_accent};
        border: 1px solid {p['highlight']};
        font-weight: 600;
    }}
    QPushButton#PrimaryButton:hover {{
        background-color: {p['highlight_hover']};
        border-color: {p['highlight_hover']};
    }}
    QPushButton#PrimaryButton:disabled {{
        background-color: {p['button']};
        color: {p['disabled']};
        border-color: {p['border']};
    }}
    QPushButton#IconOnlyButton {{
        min-width: 32px;
        max-width: 32px;
        min-height: 32px;
        max-height: 32px;
        padding: {SPACE_XS}px;
    }}
    QPushButton#IconOnlyButton:hover {{
        background-color: {p['highlight']};
        border-color: {p['highlight']};
    }}
    QPushButton#IconOnlyNBGButton {{
        background-color: transparent;
        border: none;
        padding: {SPACE_XS}px;
        min-width: 0px;
        min-height: 0px;
    }}
    QPushButton#IconOnlyNBGButton:hover {{
        background-color: {p['button_hover']};
    }}
    QPushButton#IconOnlyNBGButton:pressed {{
        background-color: {p['button_pressed']};
    }}
    QPushButton#NavButton {{
        background-color: transparent;
        border: none;
        padding: 2px {SPACE_S}px;
        min-width: 0px;
        min-height: 0px;
        font-weight: 600;
    }}
    QPushButton#NavButton:hover {{
        background-color: {p['button_hover']};
    }}
    QPushButton#NavButton:pressed, QPushButton#NavButton:checked {{
        background-color: {p['highlight']};
        color: {on_accent};
    }}
    QPushButton#NavButton:disabled {{
        background-color: transparent;
        color: {p['disabled']};
    }}
    QPushButton#StatusButton {{
        background-color: transparent;
        color: {p['text_secondary']};
        border: 1px solid {p['border_light']};
        padding: 2px {SPACE_S}px;
        min-height: 0px;
        font-weight: 600;
    }}
    QPushButton#StatusButton:hover {{
        background-color: {p['button_hover']};
        border-color: {p['highlight']};
        color: {p['text']};
    }}
    QPushButton#StatusButton[tone="success"] {{
        color: {p['success']};
        border-color: {p['success']};
    }}
    QPushButton#StatusButton[tone="warning"] {{
        color: {p['warning']};
        border-color: {p['warning']};
    }}
    QPushButton#StatusButton[tone="error"] {{
        color: {p['error']};
        border-color: {p['error']};
    }}
    QPushButton[tone="error"] {{
        background-color: {p['error']};
        color: {p['highlight_text']};
        border-color: {p['error']};
    }}
    QDialogButtonBox QPushButton {{
        min-width: 80px;
    }}
    QDialogButtonBox QPushButton:default {{
        background-color: {p['highlight']};
        color: {on_accent};
        border-color: {p['highlight']};
        font-weight: 600;
    }}
    QDialogButtonBox QPushButton:default:hover {{
        background-color: {p['highlight_hover']};
        border-color: {p['highlight_hover']};
    }}
    QDialogButtonBox QPushButton:default:disabled {{
        background-color: {p['button']};
        color: {p['disabled']};
        border-color: {p['border']};
    }}
    QToolButton {{
        background-color: transparent;
        color: {p['text']};
        border: none;
        border-radius: {RADIUS}px;
        padding: {SPACE_XS}px;
    }}
    QToolButton:hover {{
        background-color: {p['button_hover']};
    }}
    QToolButton:pressed {{
        background-color: {p['button_pressed']};
    }}
    QToolButton:checked {{
        background-color: {p['highlight']};
        color: {on_accent};
    }}
    QToolButton:disabled {{
        color: {p['disabled']};
    }}
    QToolBar {{
        background-color: {p['toolbar_bg']};
        border: none;
        padding: 2px;
        spacing: 2px;
    }}
    QToolBar::separator {{
        background-color: {p['border_light']};
        width: 1px;
        margin: {SPACE_XS}px {SPACE_XS}px;
    }}

    /* ===== Inputs ===== */
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox,
    QDateEdit, QTimeEdit, QDateTimeEdit {{
        background-color: {p['base']};
        color: {p['input_text']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS}px;
        padding: 2px {SPACE_S}px;
        min-height: 22px;
        selection-background-color: {p['highlight']};
        selection-color: {on_accent};
    }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
    QDateEdit:focus, QTimeEdit:focus, QDateTimeEdit:focus {{
        border-color: {p['highlight']};
    }}
    QComboBox:hover, QDateEdit:hover, QTimeEdit:hover, QDateTimeEdit:hover {{
        border-color: {p['highlight']};
    }}
    QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled,
    QDateEdit:disabled, QTimeEdit:disabled, QDateTimeEdit:disabled {{
        background-color: {p['surface']};
        color: {p['disabled']};
    }}
    QSpinBox::up-button, QDoubleSpinBox::up-button,
    QDateEdit::up-button, QTimeEdit::up-button, QDateTimeEdit::up-button {{
        subcontrol-origin: border;
        subcontrol-position: top right;
        width: 16px;
        border-left: 1px solid {p['border_light']};
        border-top-right-radius: {RADIUS - 1}px;
        background-color: {p['button']};
    }}
    QSpinBox::down-button, QDoubleSpinBox::down-button,
    QDateEdit::down-button, QTimeEdit::down-button, QDateTimeEdit::down-button {{
        subcontrol-origin: border;
        subcontrol-position: bottom right;
        width: 16px;
        border-left: 1px solid {p['border_light']};
        border-bottom-right-radius: {RADIUS - 1}px;
        background-color: {p['button']};
    }}
    QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
    QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover,
    QDateEdit::up-button:hover, QDateEdit::down-button:hover,
    QTimeEdit::up-button:hover, QTimeEdit::down-button:hover,
    QDateTimeEdit::up-button:hover, QDateTimeEdit::down-button:hover {{
        background-color: {p['button_hover']};
    }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow,
    QDateEdit::up-arrow, QTimeEdit::up-arrow, QDateTimeEdit::up-arrow {{
        image: url({arrow_up});
        width: 10px;
        height: 6px;
    }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow,
    QDateEdit::down-arrow, QTimeEdit::down-arrow, QDateTimeEdit::down-arrow {{
        image: url({arrow_down});
        width: 10px;
        height: 6px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {p['surface_elevated']};
        color: {p['text']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS}px;
        padding: {SPACE_XS}px;
        selection-background-color: {p['highlight']};
        selection-color: {on_accent};
    }}
    QTextEdit, QPlainTextEdit {{
        background-color: {p['base']};
        color: {p['input_text']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS}px;
        selection-background-color: {p['highlight']};
        selection-color: {on_accent};
    }}

    /* ===== Check boxes, radio buttons, switches ===== */
    QCheckBox, QRadioButton {{
        min-height: 24px;
        spacing: {SPACE_S}px;
    }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 18px;
        height: 18px;
        border: 2px solid {p['border']};
        background-color: {p['base']};
    }}
    QCheckBox::indicator {{
        border-radius: 4px;
    }}
    QRadioButton::indicator {{
        border-radius: 9px;
    }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
        background-color: {p['highlight']};
        border-color: {p['highlight']};
    }}
    QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
        border-color: {p['highlight']};
    }}
    QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
        border-color: {p['border']};
        background-color: {p['surface']};
    }}
    QCheckBox:disabled, QRadioButton:disabled {{
        color: {p['disabled']};
    }}
    QCheckBox#Switch {{
        spacing: 5px;
        font-size: {FONT_SMALL}pt;
    }}
    QCheckBox#Switch::indicator {{
        width: 28px;
        height: 14px;
        border-radius: 7px;
        border: 1px solid {p['switch_track_hover']};
        background-color: {p['switch_track']};
    }}
    QCheckBox#Switch::indicator:hover {{
        background-color: {p['switch_track_hover']};
    }}
    QCheckBox#Switch::indicator:checked {{
        background-color: {p['highlight']};
        border-color: {p['highlight_hover']};
    }}
    QCheckBox#Switch::indicator:checked:hover {{
        background-color: {p['highlight_hover']};
    }}
    QCheckBox#Switch::indicator:disabled {{
        background-color: {p['switch_track']};
        border-color: {p['switch_track']};
    }}

    /* ===== Sliders ===== */
    QSlider {{
        min-height: 28px;
    }}
    QSlider::groove:horizontal {{
        height: 6px;
        background: {p['border']};
        border-radius: 3px;
    }}
    QSlider::groove:horizontal:disabled {{
        background: {p['surface']};
    }}
    QSlider::handle:horizontal {{
        background: {p['highlight']};
        width: 18px;
        height: 18px;
        margin: -6px 0;
        border-radius: 9px;
    }}
    QSlider::handle:horizontal:hover {{
        background: {p['highlight_hover']};
    }}
    QSlider::handle:horizontal:disabled {{
        background: {p['disabled']};
    }}
    QSlider::sub-page:horizontal {{
        background: {p['highlight']};
        border-radius: 3px;
    }}
    QSlider::sub-page:horizontal:disabled {{
        background: {p['disabled']};
    }}

    /* ===== Tabs ===== */
    QTabWidget::pane {{
        border: 1px solid {p['border']};
        border-radius: {RADIUS}px;
        background-color: {p['surface']};
        padding: {SPACE_XS}px;
    }}
    QTabBar::tab {{
        background: {p['button']};
        color: {p['text']};
        padding: {SPACE_S - 2}px {SPACE_M}px;
        border-top-left-radius: {RADIUS}px;
        border-top-right-radius: {RADIUS}px;
        margin-right: 2px;
        font-size: {FONT_SMALL}pt;
        font-weight: 500;
        border: 1px solid {p['border']};
        border-bottom: none;
    }}
    QTabBar::tab:selected {{
        background: {p['highlight']};
        color: {on_accent};
        font-weight: 600;
        border-color: {p['highlight']};
    }}
    QTabBar::tab:hover:!selected {{
        background: {p['button_hover']};
        border-color: {p['highlight']};
    }}

    /* ===== Lists, trees, tables ===== */
    QListWidget, QListView, QTreeWidget, QTreeView, QTableWidget, QTableView {{
        background-color: {p['base']};
        alternate-background-color: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS}px;
        gridline-color: {p['border_light']};
        selection-background-color: {p['highlight']};
        selection-color: {on_accent};
        outline: none;
    }}
    QListWidget::item, QListView::item, QTreeWidget::item, QTreeView::item {{
        padding: 2px {SPACE_XS}px;
    }}
    QTableWidget::item, QTableView::item {{
        padding: {SPACE_XS}px {SPACE_S}px;
        border-bottom: 1px solid {p['border_light']};
    }}
    QAbstractItemView::item:selected {{
        background-color: {p['highlight']};
        color: {on_accent};
    }}
    QAbstractItemView::item:hover:!selected {{
        background-color: {p['button_hover']};
    }}
    QHeaderView::section {{
        background-color: {p['button']};
        color: {p['text']};
        font-size: {FONT_SMALL}pt;
        font-weight: 600;
        padding: {SPACE_XS + 2}px {SPACE_S}px;
        border: none;
        border-bottom: 1px solid {p['highlight']};
    }}
    QHeaderView::section:vertical {{
        border-bottom: 1px solid {p['border']};
        border-right: 1px solid {p['border']};
    }}
    QHeaderView::section:hover {{
        background-color: {p['button_hover']};
    }}
    QTableCornerButton::section {{
        background-color: {p['button']};
        border: none;
    }}

    /* ===== Menus ===== */
    QMenuBar {{
        background-color: {p['window']};
        color: {p['text']};
        padding: 2px;
    }}
    QMenuBar::item {{
        padding: {SPACE_XS + 2}px {SPACE_M}px;
        border-radius: {RADIUS - 2}px;
    }}
    QMenuBar::item:selected {{
        background-color: {p['button_hover']};
    }}
    QMenu {{
        background-color: {p['surface']};
        color: {p['text']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS}px;
        padding: {SPACE_XS}px;
    }}
    QMenu::item {{
        padding: {SPACE_XS + 2}px {SPACE_L * 2}px {SPACE_XS + 2}px {SPACE_M}px;
        border-radius: {RADIUS - 2}px;
    }}
    QMenu::item:selected {{
        background-color: {p['highlight']};
        color: {on_accent};
    }}
    QMenu::item:disabled {{
        color: {p['disabled']};
        background-color: transparent;
    }}
    QMenu::separator {{
        height: 1px;
        background: {p['border']};
        margin: {SPACE_XS}px {SPACE_S}px;
    }}

    /* ===== Progress, status ===== */
    QProgressBar {{
        border: 1px solid {p['border']};
        border-radius: {RADIUS}px;
        text-align: center;
        background-color: {p['base']};
        color: {p['text']};
        font-size: {FONT_SMALL}pt;
    }}
    QProgressBar::chunk {{
        background-color: {p['highlight']};
        border-radius: {RADIUS - 1}px;
    }}
    QStatusBar {{
        background-color: {p['surface']};
        color: {p['text']};
        font-size: {FONT_SMALL}pt;
        border-top: 1px solid {p['border']};
    }}
    QStatusBar::item {{
        border: none;
    }}

    /* ===== Scroll bars ===== */
    QScrollBar:vertical {{
        background: transparent;
        width: 12px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {p['button']};
        min-height: 30px;
        border-radius: 4px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {p['button_hover']};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 12px;
        margin: 0;
    }}
    QScrollBar::handle:horizontal {{
        background: {p['button']};
        min-width: 30px;
        border-radius: 4px;
        margin: 2px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {p['button_hover']};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0;
    }}
    QScrollBar::add-page, QScrollBar::sub-page {{
        background: transparent;
    }}
"""


def get_matplotlib_params(palette, is_dark=True):
    """Matplotlib rcParams drawing figures in the palette's colours."""
    return {
        "figure.facecolor": palette["window"] if is_dark else palette["plot_bg"],
        "figure.edgecolor": palette["border"],
        "axes.facecolor": palette["plot_bg"],
        "axes.edgecolor": palette["plot_text"],
        "axes.labelcolor": palette["plot_text"],
        "xtick.color": palette["plot_text"],
        "ytick.color": palette["plot_text"],
        "grid.color": palette["plot_grid"],
        "text.color": palette["plot_text"],
        "legend.facecolor": palette["plot_bg"],
        "legend.edgecolor": palette["border"],
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
        return palette_for(self._current_theme)

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

    def register_callback(self, callback, owner=None):
        """Call ``callback(theme)`` when the theme changes, for as long as
        ``owner`` (a QObject; for a bound method, its object) exists."""
        if owner is None:
            owner = getattr(callback, "__self__", None)
        if all(registered != callback for registered, _ in self._callbacks):
            self._callbacks.append((callback, owner))

    def unregister_callback(self, callback):
        """Unregister a theme change callback."""
        self._callbacks = [(c, o) for c, o in self._callbacks if c != callback]

    def unregister_owner(self, owner):
        """Unregister every callback registered for ``owner``."""
        self._callbacks = [(c, o) for c, o in self._callbacks if o is not owner]

    def _notify_callbacks(self):
        """Notify all registered callbacks of theme change."""
        # Callbacks of widgets deleted since (closed tabs, dialogs) go
        self._callbacks = [(c, o) for c, o in self._callbacks if not _qt_deleted(o)]
        for callback, _owner in list(self._callbacks):
            try:
                callback(self._current_theme)
            except Exception as e:
                print(f"Error in theme callback: {e}")


def _qt_deleted(obj):
    """Whether ``obj`` is a Qt object whose C++ side has been deleted."""
    if obj is None:
        return False
    try:
        from PyQt5 import sip
    except ImportError:
        import sip
    try:
        return isinstance(obj, sip.simplewrapper) and sip.isdeleted(obj)
    except TypeError:
        return False


# Global theme manager instance
theme_manager = ThemeManager()


def get_icon_path(icon_name):
    """The file name of ``icon_name`` ('browse.png') in the current theme:
    the ``_light`` version in the light theme."""
    if theme_manager.is_dark:
        return icon_name
    name, ext = icon_name.rsplit(".", 1)
    return f"{name}_light.{ext}"


def set_tone(widget, tone):
    """Colour ``widget`` (a label or StatusButton) by ``tone``: "success",
    "warning", "error", or None for the plain colour."""
    widget.setProperty("tone", tone)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def set_hand_cursor(widget):
    """Give every button and other interactive control under ``widget`` the
    pointing-hand cursor."""
    from PyQt5.QtWidgets import (
        QAbstractButton,
        QComboBox,
        QMenu,
        QMenuBar,
        QTabBar,
        QHeaderView,
        QSlider,
        QAbstractSpinBox,
    )

    if isinstance(
        widget,
        (
            QAbstractButton,
            QComboBox,
            QMenu,
            QMenuBar,
            QTabBar,
            QHeaderView,
            QSlider,
            QAbstractSpinBox,
        ),
    ):
        widget.setCursor(Qt.PointingHandCursor)

    for child in widget.children():
        if isinstance(child, QWidget):
            set_hand_cursor(child)

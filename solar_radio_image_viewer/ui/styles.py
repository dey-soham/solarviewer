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
# Type scale, in points (Nocturne: 13px body, 12px controls, 11px meta,
# 10px kickers): four sizes are enough for the whole application.
FONT_META = 8  # kickers, status bar, the small second line of a row
FONT_SMALL = 9  # buttons, tabs, table rows, hints
FONT_BODY = 10  # everything else
FONT_SECTION = 11  # section titles inside a window
FONT_TITLE = 18  # a window's or dialog's one title
FONT_HEADLINE = 21  # the empty canvas's one line
# Spacing steps, in pixels (scaled by Qt with the display)
SPACE_XS, SPACE_S, SPACE_M, SPACE_L = 4, 8, 12, 16
RADIUS = 6  # of every rounded corner (controls)
RADIUS_LG = 8  # of containers: segmented groups, cards, menus
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
#   PrimaryButton   the one main action of a window or dialog (accent outline)
#   LinkButton      a text button in the accent, no border ("RMS box…")
#   IconOnlyButton  a square icon button with a border
#   IconOnlyNBGButton  a square icon button with no background
#   NavButton       a flat text/glyph button in a toolbar row
#   Switch          a QCheckBox drawn as an on/off switch
#   SwitchLabel     a QLabel naming a switch with no text of its own
#   Kicker          a QLabel heading a block: small capitals in the accent
#   SectionTitle    a QLabel heading a section
#   DialogTitle     a QLabel titling a dialog
#   Headline        a QLabel of one large line (the empty canvas)
#   SecondaryText   a QLabel hint in muted text (alias: Hint)
#   Meta            a QLabel of the smallest muted text (dates, sizes)
#   StatusLabel     a QLabel framed as a status line
#   StatusButton    a flat outlined button in the status bar
#   Badge           a QLabel pill with a short fact ("12 files")
#   Key             a QLabel drawn as a keyboard key
#   Mono            a QLabel in the monospace font (coordinates, values)
#   Tip             a QLabel of advice, marked with an accent bar
#   StepNumber      a QLabel with a step's number in an accent circle
#   Banner          a QFrame header filled with the accent (About dialog)
#   Card            a QFrame panel on the surface colour with a border
#   Surface         a QFrame on the surface colour, no border (toggle rows)
#   Rule            a QFrame drawn as a 1px horizontal line, fading at the ends
#   VRule           a QFrame drawn as a 1px vertical line
#   TabRow          the row holding the image tabs on macOS (a line below)
#   TopBar          the main window's control bar (a line below)
#   CanvasBar       the toolbar under the image (a line above)
#   Inspector       the main window's right column (a line on its left)
#   InspectorHeader the row at its top, on the surface (a line below)
#   Pinned          a QFrame pinned at the inspector's foot, on the surface
#   Sidebar         a QFrame navigation column
#   NavList         a QListWidget of sections in a Sidebar
#   Console         a QPlainTextEdit of log text, monospace
#   ImageWell       a QFrame or QScrollArea showing a picture on black
#   Segmented       a QWidget holding a row of checkable buttons, one on
#   HeaderSegmented the same on a filled track with no border (the header)
#   LauncherButton  a 28px bordered icon button opening a window
#   SectionHeader   a QPushButton heading a collapsible section
#   KeyValue        a QTableWidget of label/value rows, no frame or header
#   DocumentTabs    the QTabBar of the main window's image tabs
#   RecentRow       a clickable QFrame row of the empty canvas's recent files
#   PaneFrame       a QFrame round one pane of a compare page (accent when active)
#   PaneHeader      the row over a pane: its badge, name, meta and buttons
#   PaneBadge       a QLabel with a pane's letter, on that pane's colour
#   PaneName        a QLabel with a pane's file name, monospace
#   EmptySlot       a dashed QFrame where a compare page's next image goes
#   EditingRow      the inspector's row naming the pane its controls edit
# A label or StatusButton can also take a tone with ``set_tone``:
# "success", "warning" or "error" colour it accordingly, None clears it.
ROLES = (
    "PrimaryButton",
    "LinkButton",
    "IconOnlyButton",
    "IconOnlyNBGButton",
    "NavButton",
    "Switch",
    "SwitchLabel",
    "Kicker",
    "SectionTitle",
    "DialogTitle",
    "Headline",
    "SecondaryText",
    "Hint",
    "Meta",
    "StatusLabel",
    "StatusButton",
    "Badge",
    "Key",
    "Mono",
    "Tip",
    "StepNumber",
    "Banner",
    "Card",
    "Surface",
    "Rule",
    "VRule",
    "TabRow",
    "TopBar",
    "CanvasBar",
    "Inspector",
    "InspectorHeader",
    "Pinned",
    "Sidebar",
    "NavList",
    "Console",
    "ImageWell",
    "Segmented",
    "HeaderSegmented",
    "LauncherButton",
    "SectionHeader",
    "KeyValue",
    "DocumentTabs",
    "RecentRow",
    "PaneFrame",
    "PaneHeader",
    "PaneBadge",
    "PaneName",
    "EmptySlot",
    "EditingRow",
)

# The panes of a compare page, A to D: (background, letter) of each badge
# and the colour of its crosshair. Four hues at four brightnesses, from
# the lightest to the darkest, so the panes tell apart by brightness as
# well as hue, with the letter always there.
PANE_COLORS_DARK = (
    ("#e8c547", "#161826"),
    ("#7aa2ff", "#161826"),
    ("#d9603b", "#f5f4ff"),
    ("#3d8a5c", "#f5f4ff"),
)
PANE_COLORS_LIGHT = (
    ("#d4a712", "#1d1f2b"),
    ("#4d7be0", "#ffffff"),
    ("#bf4a28", "#ffffff"),
    ("#2b6d45", "#ffffff"),
)

# ===== Palettes =====
# Every colour the application paints with, by role: the Nocturne design
# system (bg #161826, surface #232532, text #e9e9ed, the blurple accent
# #9184d9, with neutral and accent ramps on one lightness scale). The two
# palettes have the same keys. The rgba entries are tints laid over
# whatever is beneath (Nocturne's color-mix with transparent): for the
# stylesheet only, as Matplotlib and QColor want the hex ones.
DARK_PALETTE = {
    "window": "#161826",  # window background
    "base": "#232532",  # inputs, lists, tables
    "surface": "#232532",  # group boxes, panels, menus
    "surface_elevated": "#292b31",  # popups, tooltips
    "canvas": "#161826",  # the empty canvas's ground
    "canvas_glow": "#1b1d2e",  # the empty canvas's centre
    "button": "#232532",
    "button_hover": "#2d2f3d",
    "button_pressed": "#363846",
    "border": "#383a47",  # text at 16% over the window
    "border_light": "#3f424d",  # separators, table grid (neutral-800)
    "divider": "rgba(233, 233, 237, 0.16)",
    "hover": "rgba(233, 233, 237, 0.07)",
    "pressed": "rgba(233, 233, 237, 0.14)",
    "row_rule": "rgba(233, 233, 237, 0.08)",
    "text": "#e9e9ed",
    "text_strong": "#cfd3e5",  # neutral-300: menus, values
    "text_dim": "#b2b6ca",  # neutral-400: a switch's label while off
    "text_secondary": "#9397ab",  # neutral-500: labels, hints
    "input_text": "#e9e9ed",
    "disabled": "#595d6c",  # neutral-700
    "highlight": "#9184d9",  # the accent
    "highlight_hover": "#b5abfc",  # accent-400
    "highlight_soft": "#423a6a",  # accent-800: fills behind the accent, histogram bars within the range
    "highlight_deep": "#5d5294",  # accent-700: a switch's track when on
    "highlight_knob": "#e7e5fe",  # accent-200: a switch's knob when on
    "slider_handle": "#e7e5fe",  # the gamma slider's knob, the histogram's range lines
    "slider_groove": "#292b31",  # the part of the slider past the knob (neutral-900)
    "hist_out": "#292b31",  # histogram bars outside the range (neutral-900)
    "text_bright": "#f5f4ff",  # accent-100: the chosen segment's text
    "link": "#d2cefd",  # accent-300: link buttons
    "outline": "#595d6c",  # neutral-700: a bordered icon button's edge
    "accent_hover": "rgba(145, 132, 217, 0.12)",
    "accent_pressed": "rgba(145, 132, 217, 0.22)",
    "highlight_text": "#161826",  # text on the accent
    "success": "#4ade80",
    "warning": "#fbbf24",
    "error": "#f87171",
    "secondary": "#a7a1db",
    "switch_track": "#292b31",  # neutral-900
    "switch_track_hover": "#3f424d",  # neutral-800
    "switch_knob": "#595d6c",  # neutral-700
    "plot_bg": "#161826",
    "plot_text": "#e9e9ed",
    "plot_grid": "#3f424d",
    "toolbar_bg": "#161826",
    # Kept for widgets not yet restyled: flat values of the old gradients
    "highlight_glow": "rgba(145, 132, 217, 0.25)",
    "button_gradient_start": "#9184d9",
    "button_gradient_end": "#9184d9",
    "shadow": "rgba(0, 0, 0, 0.55)",
    "pane_colors": PANE_COLORS_DARK,
}

# The light theme: the colours of Claude Design's light suggestion
# (design_screenshots/light_theme.png, 2026-10-24), sampled role by role.
LIGHT_PALETTE = {
    "window": "#f6f7fb",  # top bar, inspector, canvas bar, footer
    "base": "#ffffff",
    "surface": "#eceef5",  # menu bar, status bar, cards, recent rows, cursor block
    "surface_elevated": "#ffffff",
    "canvas": "#f0f1f8",  # a shade below the window
    "canvas_glow": "#fcfcfd",
    "button": "#ffffff",
    "button_hover": "#f6f7fb",
    "button_pressed": "#eceef5",
    "border": "#d6d7d9",  # inputs, segmented groups
    "border_light": "#d7d8de",  # separators, table grid
    "divider": "rgba(29, 31, 43, 0.16)",
    "hover": "rgba(29, 31, 43, 0.07)",
    "pressed": "rgba(29, 31, 43, 0.14)",
    "row_rule": "rgba(29, 31, 43, 0.08)",
    "text": "#1d1f2b",
    "text_strong": "#4a4e5e",  # menus, values
    "text_dim": "#5b5f70",  # a segment's text while not chosen
    "text_secondary": "#6f7386",  # labels, hints, placeholders
    "input_text": "#1d1f2b",
    "disabled": "#b2b6ca",
    "highlight": "#6a5fb8",  # the accent: outlines, kickers, the tab underline
    "highlight_hover": "#5d5294",
    "highlight_soft": "#c9c3f0",  # histogram bars within the range
    "highlight_deep": "#9184d9",  # a switch's track when on
    "highlight_knob": "#4a4180",  # a switch's knob when on (assets/switch_on_light.svg)
    "slider_handle": "#4a4180",  # the gamma slider's knob, the histogram's range lines
    "slider_groove": "#d6d9e6",
    "hist_out": "#d6d9e6",  # histogram bars outside the range
    "text_bright": "#6a5fb8",  # the chosen segment's text
    "link": "#5d5294",
    "outline": "#c6c8cd",  # a bordered button's edge
    "accent_hover": "rgba(106, 95, 184, 0.10)",
    "accent_pressed": "rgba(106, 95, 184, 0.20)",
    "highlight_text": "#ffffff",
    "success": "#16a34a",
    "warning": "#d97706",
    "error": "#dc2626",
    "secondary": "#5c5783",
    "switch_track": "#6f7386",  # a switch's track when off
    "switch_track_hover": "#5b5f70",
    "switch_knob": "#d6d9e6",  # a switch's knob when off (assets/switch_off_light.svg)
    "plot_bg": "#ffffff",
    "plot_text": "#1d1f2b",
    "plot_grid": "#d7d8de",
    "toolbar_bg": "#f6f7fb",
    "highlight_glow": "rgba(106, 95, 184, 0.15)",
    "button_gradient_start": "#6a5fb8",
    "button_gradient_end": "#6a5fb8",
    "shadow": "rgba(29, 31, 43, 0.12)",
    "pane_colors": PANE_COLORS_LIGHT,
}


def palette_for(theme):
    """The palette of ``theme`` ("dark" or "light")."""
    return LIGHT_PALETTE if theme == "light" else DARK_PALETTE


def get_stylesheet(palette, is_dark=True):
    """The application stylesheet for ``palette``."""
    suffix = "" if is_dark else "_light"
    assets = os.path.join(PACKAGE_DIR, "assets").replace("\\", "/")
    pane_badges = "\n".join(
        f'QLabel#PaneBadge[letter="{letter}"] {{ background-color: {bg}; color: {fg}; }}'
        for letter, (bg, fg) in zip("ABCD", palette["pane_colors"])
    )
    arrow_up = f"{assets}/spinbox_up{suffix}.png"
    arrow_down = f"{assets}/spinbox_down{suffix}.png"
    switch_on = f"{assets}/switch_on{suffix}.svg"
    switch_off = f"{assets}/switch_off{suffix}.svg"

    p = palette
    on_accent = p["highlight_text"]
    # Nocturne's freestanding rules fade to transparent at both ends
    fading_rule = (
        "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 transparent, "
        f"stop:0.12 {p['divider']}, stop:0.88 {p['divider']}, stop:1 transparent)"
    )

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
        border: 1px solid {p['border_light']};
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
        width: 1px;
    }}
    QSplitter::handle:vertical {{
        height: 1px;
    }}

    /* ===== Text ===== */
    QLabel {{
        background-color: transparent;
    }}
    QLabel:disabled {{
        color: {p['disabled']};
    }}
    QLabel#Kicker {{
        color: {p['highlight']};
        font-size: {FONT_META}pt;
        font-weight: 600;
    }}
    QLabel#SectionTitle {{
        font-size: {FONT_SECTION}pt;
        font-weight: 500;
    }}
    QLabel#DialogTitle {{
        font-size: {FONT_TITLE}pt;
        font-weight: 500;
    }}
    QLabel#Headline {{
        font-size: {FONT_HEADLINE}pt;
        font-weight: 500;
    }}
    QLabel#SecondaryText, QLabel#Hint {{
        color: {p['text_secondary']};
        font-size: {FONT_SMALL}pt;
    }}
    QLabel#Meta {{
        color: {p['text_secondary']};
        font-size: {FONT_META}pt;
    }}
    QLabel#StatusLabel {{
        padding: {SPACE_S}px {SPACE_M}px;
        background-color: {p['surface']};
        border: 1px solid {p['divider']};
        border-radius: {RADIUS}px;
        font-size: {FONT_SMALL}pt;
    }}
    QLabel#Mono {{
        font-family: {MONO_FAMILY};
        color: {p['text_strong']};
    }}
    QLabel#Badge {{
        color: {p['text_secondary']};
        font-size: {FONT_META}pt;
        padding: 1px {SPACE_S - 2}px;
        background-color: transparent;
        border: 1px solid {p['divider']};
        border-radius: 3px;
    }}
    QLabel#Key {{
        font-family: {MONO_FAMILY};
        font-size: {FONT_SMALL}pt;
        font-weight: 600;
        padding: 2px {SPACE_S}px;
        background-color: {p['surface']};
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
        background-color: {p['highlight_soft']};
        color: {p['highlight_hover']};
        font-weight: 600;
        border-radius: 14px;
        min-width: 28px;
        max-width: 28px;
        min-height: 28px;
        max-height: 28px;
    }}
    QFrame#Banner {{
        background-color: {p['highlight_soft']};
        border: none;
        border-radius: 0px;
    }}
    QFrame#Banner QLabel {{
        color: {p['text']};
        background-color: transparent;
    }}
    QFrame#Card {{
        background-color: {p['surface']};
        border: 1px solid {p['divider']};
        border-radius: {RADIUS_LG}px;
    }}
    QFrame#Surface {{
        background-color: {p['surface']};
        border: none;
        border-radius: {RADIUS}px;
    }}
    QFrame#Rule {{
        background: {fading_rule};
        border: none;
        max-height: 1px;
        min-height: 1px;
    }}
    QFrame#VRule {{
        background-color: {p['divider']};
        border: none;
        max-width: 1px;
        min-width: 1px;
    }}
    QFrame#TabRow {{
        background-color: {p['surface']};
        border: none;
        border-bottom: 1px solid {p['divider']};
    }}
    QFrame#TopBar {{
        background-color: {p['window']};
        border: none;
        border-bottom: 1px solid {p['divider']};
    }}
    QFrame#CanvasBar {{
        background-color: {p['window']};
        border: none;
        border-top: 1px solid {p['divider']};
    }}
    QFrame#Inspector {{
        background-color: {p['window']};
        border: none;
        border-left: 1px solid {p['divider']};
    }}
    QFrame#InspectorHeader {{
        background-color: {p['surface']};
        border: none;
        border-bottom: 1px solid {p['divider']};
        min-height: 40px;
        max-height: 40px;
    }}
    QFrame#PaneFrame {{
        background-color: {p['window']};
        border: 1px solid {p['divider']};
        border-radius: {RADIUS}px;
    }}
    QFrame#PaneFrame[active="true"] {{
        border-color: {p['highlight']};
    }}
    QFrame#PaneFrame[single="true"] {{
        border: none;
        border-radius: 0px;
    }}
    QFrame#PaneHeader {{
        background-color: {p['surface']};
        border: none;
        border-bottom: 1px solid {p['divider']};
        border-top-left-radius: {RADIUS - 1}px;
        border-top-right-radius: {RADIUS - 1}px;
        min-height: 30px;
        max-height: 30px;
    }}
    QFrame#PaneHeader QLabel {{
        background-color: transparent;
    }}
    QLabel#PaneBadge {{
        font-size: {FONT_META}pt;
        font-weight: 600;
        min-width: 18px;
        max-width: 18px;
        min-height: 18px;
        max-height: 18px;
        border-radius: 4px;
        qproperty-alignment: AlignCenter;
    }}
    {pane_badges}
    QLabel#PaneName {{
        font-family: {MONO_FAMILY};
        font-size: {FONT_SMALL}pt;
        color: {p['text']};
    }}
    QFrame#PaneHeader QToolButton {{
        border: none;
        background-color: transparent;
        padding: 2px;
        border-radius: 4px;
    }}
    QFrame#PaneHeader QToolButton:hover {{
        background-color: {p['hover']};
    }}
    QFrame#EditingRow {{
        background-color: {p['surface']};
        border: none;
        border-bottom: 1px solid {p['divider']};
        min-height: 30px;
        max-height: 30px;
    }}
    QFrame#EditingRow QLabel {{
        background-color: transparent;
    }}
    QFrame#EmptySlot {{
        background-color: {p['canvas']};
        border: 1px dashed {p['border_light']};
        border-radius: {RADIUS}px;
    }}
    QFrame#EmptySlot QLabel {{
        background-color: transparent;
    }}
    QFrame#Pinned {{
        background-color: {p['surface']};
        border: none;
        border-top: 1px solid {p['divider']};
    }}
    QFrame#Sidebar {{
        background-color: {p['surface']};
        border: none;
        border-right: 1px solid {p['divider']};
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
        border: 1px solid {p['divider']};
        border-radius: {RADIUS}px;
    }}
    QFrame#ImageWell QLabel {{
        color: {p['text_secondary']};
    }}
    QFrame#RecentRow {{
        background-color: transparent;
        border: none;
        border-radius: {RADIUS}px;
    }}
    QFrame#RecentRow:hover {{
        background-color: {p['surface']};
    }}
    QFrame#RecentRow QLabel {{
        background-color: transparent;
    }}

    /* A row of choices, one on: the group has the border, the one chosen
       an accent outline (never a fill) */
    QWidget#Segmented, QFrame#Segmented {{
        background-color: transparent;
        border: 1px solid {p['divider']};
        border-radius: {RADIUS_LG}px;
    }}
    QWidget#Segmented QPushButton, QFrame#Segmented QPushButton,
    QWidget#Segmented QToolButton, QFrame#Segmented QToolButton {{
        background-color: transparent;
        color: {p['text_secondary']};
        border: 1px solid transparent;
        border-radius: {RADIUS}px;
        padding: 2px {SPACE_S}px;
        min-height: 20px;
        font-weight: 500;
    }}
    QWidget#Segmented QPushButton:hover, QFrame#Segmented QPushButton:hover,
    QWidget#Segmented QToolButton:hover, QFrame#Segmented QToolButton:hover {{
        color: {p['text']};
        background-color: {p['hover']};
    }}
    QWidget#Segmented QPushButton:checked, QFrame#Segmented QPushButton:checked,
    QWidget#Segmented QToolButton:checked, QFrame#Segmented QToolButton:checked {{
        background-color: transparent;
        color: {p['text_bright']};
        border-color: {p['highlight']};
    }}
    QFrame#HeaderSegmented {{
        background-color: {p['surface_elevated']};
        border: none;
        border-radius: {RADIUS}px;
        min-height: 26px;
        max-height: 26px;
    }}
    QFrame#HeaderSegmented QPushButton {{
        background-color: transparent;
        color: {p['text_secondary']};
        border: 1px solid transparent;
        border-radius: {RADIUS}px;
        padding: 0px 9px;
        min-height: 22px;
        max-height: 22px;
        font-size: {FONT_META}pt;
        font-weight: 500;
    }}
    QFrame#HeaderSegmented QPushButton:hover {{
        color: {p['text']};
        background-color: {p['hover']};
    }}
    QFrame#HeaderSegmented QPushButton:checked {{
        background-color: transparent;
        color: {p['text_bright']};
        border-color: {p['highlight']};
    }}
    QFrame#HeaderSegmented QPushButton:disabled {{
        color: {p['disabled']};
        background-color: transparent;
    }}
    QPushButton#LauncherButton {{
        background-color: transparent;
        border: 1px solid {p['outline']};
        border-radius: {RADIUS}px;
        padding: 0px;
        min-width: 26px;
        max-width: 26px;
        min-height: 26px;
        max-height: 26px;
    }}
    QPushButton#LauncherButton:hover {{
        background-color: {p['hover']};
    }}
    QPushButton#LauncherButton:disabled {{
        border-color: {p['border']};
    }}
    QWidget#Segmented QPushButton:disabled, QFrame#Segmented QPushButton:disabled,
    QWidget#Segmented QToolButton:disabled, QFrame#Segmented QToolButton:disabled {{
        color: {p['disabled']};
        background-color: transparent;
    }}
    QWidget#Segmented QLabel {{
        color: {p['text_secondary']};
        padding: 0 {SPACE_XS}px;
    }}
    QPushButton#SectionHeader {{
        text-align: left;
        padding: {SPACE_S}px {SPACE_M}px;
        font-weight: 500;
        background-color: {p['surface']};
        border: 1px solid {p['divider']};
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
        border: 1px solid {p['divider']};
        border-radius: {RADIUS_LG}px;
        margin-top: {SPACE_M}px;
        padding: {SPACE_S}px;
        font-weight: 500;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: {SPACE_M}px;
        padding: 0 {SPACE_XS}px;
        color: {p['highlight']};
        background-color: {p['window']};
        font-size: {FONT_SMALL}pt;
        font-weight: 600;
    }}
    QDialog QGroupBox::title {{
        background-color: {p['window']};
    }}

    /* ===== Buttons: outlined, never filled ===== */
    QPushButton {{
        background-color: transparent;
        color: {p['text']};
        border: 1px solid {p['divider']};
        border-radius: {RADIUS}px;
        padding: {SPACE_XS}px {SPACE_M}px;
        min-height: 24px;
        font-size: {FONT_SMALL}pt;
        font-weight: 500;
    }}
    QPushButton:hover {{
        background-color: {p['hover']};
    }}
    QPushButton:pressed {{
        background-color: {p['pressed']};
    }}
    QPushButton:checked {{
        background-color: transparent;
        color: {p['highlight']};
        border-color: {p['highlight']};
    }}
    QPushButton:checked:hover {{
        background-color: {p['accent_hover']};
    }}
    QPushButton:disabled {{
        color: {p['disabled']};
        background-color: transparent;
        border-color: {p['divider']};
    }}
    QPushButton::menu-indicator {{
        image: url({arrow_down});
        width: 10px;
        height: 6px;
        subcontrol-position: right center;
        subcontrol-origin: padding;
        right: {SPACE_S}px;
    }}
    QPushButton#PrimaryButton {{
        background-color: transparent;
        color: {p['highlight']};
        border: 1px solid {p['highlight']};
        font-weight: 500;
    }}
    QPushButton#PrimaryButton:hover {{
        background-color: {p['accent_hover']};
    }}
    QPushButton#PrimaryButton:pressed {{
        background-color: {p['accent_pressed']};
    }}
    QPushButton#PrimaryButton:disabled {{
        color: {p['disabled']};
        border-color: {p['divider']};
    }}
    QPushButton#LinkButton {{
        background-color: transparent;
        color: {p['link']};
        border: 1px solid transparent;
        padding: 2px {SPACE_XS}px;
        min-height: 20px;
        font-weight: 500;
    }}
    QPushButton#LinkButton:hover {{
        background-color: {p['accent_hover']};
    }}
    QPushButton#LinkButton:pressed {{
        background-color: {p['accent_pressed']};
    }}
    QPushButton#LinkButton:disabled {{
        color: {p['disabled']};
        border-color: transparent;
    }}
    QPushButton#IconOnlyButton {{
        min-width: 32px;
        max-width: 32px;
        min-height: 32px;
        max-height: 32px;
        padding: {SPACE_XS}px;
    }}
    QPushButton#IconOnlyButton:hover {{
        border-color: {p['highlight']};
    }}
    QPushButton#IconOnlyNBGButton {{
        background-color: transparent;
        border: 1px solid transparent;
        padding: {SPACE_XS}px;
        min-width: 0px;
        min-height: 0px;
    }}
    QPushButton#IconOnlyNBGButton:hover {{
        background-color: {p['hover']};
    }}
    QPushButton#IconOnlyNBGButton:pressed {{
        background-color: {p['pressed']};
    }}
    QPushButton#NavButton {{
        background-color: transparent;
        color: {p['text_secondary']};
        border: 1px solid transparent;
        padding: 2px {SPACE_S - 2}px;
        min-width: 0px;
        min-height: 0px;
        font-weight: 500;
    }}
    QPushButton#NavButton:hover {{
        color: {p['text']};
        background-color: {p['hover']};
    }}
    QPushButton#NavButton:pressed {{
        background-color: {p['pressed']};
    }}
    QPushButton#NavButton:checked {{
        color: {p['highlight']};
        border-color: {p['highlight']};
    }}
    QPushButton#NavButton:disabled {{
        background-color: transparent;
        color: {p['disabled']};
    }}
    QPushButton#StatusButton {{
        background-color: transparent;
        color: {p['text_secondary']};
        border: 1px solid transparent;
        padding: 1px {SPACE_S}px;
        min-height: 0px;
        font-size: {FONT_META}pt;
        font-weight: 500;
    }}
    QPushButton#StatusButton:hover {{
        background-color: {p['hover']};
        color: {p['text']};
    }}
    QPushButton#StatusButton[tone="success"] {{
        color: {p['success']};
    }}
    QPushButton#StatusButton[tone="warning"] {{
        color: {p['warning']};
    }}
    QPushButton#StatusButton[tone="error"] {{
        color: {p['error']};
    }}
    QPushButton[tone="error"] {{
        color: {p['error']};
        border-color: {p['error']};
    }}
    QDialogButtonBox QPushButton {{
        min-width: 80px;
    }}
    QDialogButtonBox QPushButton:default {{
        color: {p['highlight']};
        border-color: {p['highlight']};
    }}
    QDialogButtonBox QPushButton:default:hover {{
        background-color: {p['accent_hover']};
    }}
    QDialogButtonBox QPushButton:default:disabled {{
        color: {p['disabled']};
        border-color: {p['divider']};
    }}
    QToolButton {{
        background-color: transparent;
        color: {p['text_secondary']};
        border: 1px solid transparent;
        border-radius: {RADIUS}px;
        padding: {SPACE_XS}px;
    }}
    QToolButton:hover {{
        color: {p['text']};
        background-color: {p['hover']};
    }}
    QToolButton:pressed {{
        background-color: {p['pressed']};
    }}
    QToolButton:checked {{
        color: {p['highlight']};
        border-color: {p['highlight']};
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
        background-color: {p['divider']};
        width: 1px;
        margin: {SPACE_XS}px {SPACE_XS}px;
    }}

    /* ===== Inputs ===== */
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox,
    QDateEdit, QTimeEdit, QDateTimeEdit {{
        background-color: {p['base']};
        color: {p['input_text']};
        border: 1px solid {p['divider']};
        border-radius: {RADIUS}px;
        padding: 2px {SPACE_S}px;
        min-height: 22px;
        selection-background-color: {p['highlight_soft']};
        selection-color: {p['text']};
    }}
    QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover,
    QDateEdit:hover, QTimeEdit:hover, QDateTimeEdit:hover {{
        border-color: {p['text_secondary']};
    }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
    QDateEdit:focus, QTimeEdit:focus, QDateTimeEdit:focus {{
        border-color: {p['highlight']};
    }}
    QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled,
    QDateEdit:disabled, QTimeEdit:disabled, QDateTimeEdit:disabled {{
        background-color: transparent;
        color: {p['disabled']};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 18px;
    }}
    QComboBox::down-arrow {{
        image: url({arrow_down});
        width: 10px;
        height: 6px;
    }}
    QSpinBox::up-button, QDoubleSpinBox::up-button,
    QDateEdit::up-button, QTimeEdit::up-button, QDateTimeEdit::up-button {{
        subcontrol-origin: border;
        subcontrol-position: top right;
        width: 16px;
        border-left: 1px solid {p['divider']};
        border-top-right-radius: {RADIUS - 1}px;
        background-color: transparent;
    }}
    QSpinBox::down-button, QDoubleSpinBox::down-button,
    QDateEdit::down-button, QTimeEdit::down-button, QDateTimeEdit::down-button {{
        subcontrol-origin: border;
        subcontrol-position: bottom right;
        width: 16px;
        border-left: 1px solid {p['divider']};
        border-bottom-right-radius: {RADIUS - 1}px;
        background-color: transparent;
    }}
    QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
    QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover,
    QDateEdit::up-button:hover, QDateEdit::down-button:hover,
    QTimeEdit::up-button:hover, QTimeEdit::down-button:hover,
    QDateTimeEdit::up-button:hover, QDateTimeEdit::down-button:hover {{
        background-color: {p['hover']};
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
        border: 1px solid {p['border_light']};
        border-radius: {RADIUS}px;
        padding: {SPACE_XS}px;
        selection-background-color: {p['highlight_soft']};
        selection-color: {p['text']};
    }}
    QTextEdit, QPlainTextEdit {{
        background-color: {p['base']};
        color: {p['input_text']};
        border: 1px solid {p['divider']};
        border-radius: {RADIUS}px;
        selection-background-color: {p['highlight_soft']};
        selection-color: {p['text']};
    }}

    /* ===== Check boxes, radio buttons, switches ===== */
    QCheckBox, QRadioButton {{
        min-height: 22px;
        spacing: {SPACE_S}px;
    }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 16px;
        height: 16px;
        border: 1.5px solid {p['border_light']};
        background-color: transparent;
    }}
    QCheckBox::indicator {{
        border-radius: 4px;
    }}
    QRadioButton::indicator {{
        border-radius: 8px;
    }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
        background-color: {p['highlight']};
        border-color: {p['highlight']};
    }}
    QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
        border-color: {p['highlight']};
    }}
    QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
        border-color: {p['divider']};
        background-color: transparent;
    }}
    QCheckBox:disabled, QRadioButton:disabled {{
        color: {p['disabled']};
    }}
    QCheckBox#Switch {{
        spacing: {SPACE_S}px;
        font-size: {FONT_SMALL}pt;
        min-height: 18px;
        color: {p['text_dim']};
    }}
    QCheckBox#Switch:checked {{
        color: {p['text']};
    }}
    QLabel#SwitchLabel {{
        color: {p['text_dim']};
    }}
    QLabel#SwitchLabel[on="true"] {{
        color: {p['text']};
    }}
    QFrame#Surface[on="true"] {{
        border: 1px solid {p['border_light']};
    }}
    QCheckBox#Switch::indicator {{
        width: 24px;
        height: 14px;
        border-radius: 7px;
        border: none;
        background-color: {p['switch_track']};
        image: url({switch_off});
    }}
    QCheckBox#Switch::indicator:hover {{
        background-color: {p['switch_track_hover']};
    }}
    QCheckBox#Switch::indicator:checked {{
        background-color: {p['highlight_deep']};
        image: url({switch_on});
    }}
    QCheckBox#Switch::indicator:checked:hover {{
        background-color: {p['highlight']};
    }}
    QCheckBox#Switch::indicator:disabled {{
        background-color: {p['switch_track']};
    }}

    /* ===== Sliders ===== */
    QSlider {{
        min-height: 20px;
    }}
    QSlider::groove:horizontal {{
        height: 2px;
        background: {p['slider_groove']};
        border-radius: 1px;
    }}
    QSlider::groove:horizontal:disabled {{
        background: {p['border']};
    }}
    QSlider::handle:horizontal {{
        background: {p['slider_handle']};
        width: 10px;
        height: 10px;
        margin: -4px 0;
        border-radius: 5px;
    }}
    QSlider::handle:horizontal:hover {{
        background: {p['highlight_hover']};
    }}
    QSlider::handle:horizontal:disabled {{
        background: {p['disabled']};
    }}
    QSlider::sub-page:horizontal {{
        background: {p['highlight']};
        border-radius: 1px;
    }}
    QSlider::sub-page:horizontal:disabled {{
        background: {p['disabled']};
    }}

    /* ===== Tabs: an accent underline under the one open ===== */
    QTabWidget::pane {{
        border: none;
        border-top: 1px solid {p['divider']};
        background-color: transparent;
        padding: 0px;
    }}
    QTabBar::tab {{
        background: transparent;
        color: {p['text_secondary']};
        padding: {SPACE_S}px 0px;
        margin-right: {SPACE_L + 2}px;
        font-size: {FONT_SMALL}pt;
        font-weight: 500;
        border: none;
        border-bottom: 2px solid transparent;
    }}
    QTabBar::tab:selected {{
        color: {p['text']};
        border-bottom: 2px solid {p['highlight']};
    }}
    QTabBar::tab:hover:!selected {{
        color: {p['text']};
    }}
    QTabBar::tab:disabled {{
        color: {p['disabled']};
    }}
    QTabWidget#InspectorTabs::tab-bar {{
        left: 14px;
    }}
    QTabBar#DocumentTabs::tab {{
        color: {p['text_secondary']};
        padding: 2px {SPACE_S + 2}px;
        margin: 0px 2px;
        border: 1px solid transparent;
        border-radius: {RADIUS}px;
        min-height: 18px;
    }}
    QTabBar#DocumentTabs::tab:selected {{
        color: {p['text']};
        border: 1px solid {p['highlight']};
    }}
    QTabBar#DocumentTabs::tab:hover:!selected {{
        background: {p['hover']};
    }}

    /* ===== Lists, trees, tables ===== */
    QListWidget, QListView, QTreeWidget, QTreeView, QTableWidget, QTableView {{
        background-color: {p['base']};
        alternate-background-color: {p['surface']};
        border: 1px solid {p['divider']};
        border-radius: {RADIUS}px;
        gridline-color: {p['border_light']};
        selection-background-color: {p['highlight_soft']};
        selection-color: {p['text']};
        outline: none;
    }}
    QListWidget::item, QListView::item, QTreeWidget::item, QTreeView::item {{
        padding: 2px {SPACE_XS}px;
    }}
    QTableWidget::item, QTableView::item {{
        padding: {SPACE_XS}px {SPACE_S}px;
        border-bottom: 1px solid {p['row_rule']};
    }}
    QAbstractItemView::item:selected {{
        background-color: {p['highlight_soft']};
        color: {p['text']};
    }}
    QAbstractItemView::item:hover:!selected {{
        background-color: {p['hover']};
    }}
    QHeaderView::section {{
        background-color: transparent;
        color: {p['text_secondary']};
        font-size: {FONT_META}pt;
        font-weight: 600;
        padding: {SPACE_XS + 2}px {SPACE_S}px;
        border: none;
        border-bottom: 1px solid {p['divider']};
    }}
    QHeaderView::section:vertical {{
        border-bottom: 1px solid {p['row_rule']};
        border-right: 1px solid {p['divider']};
    }}
    QHeaderView::section:hover {{
        background-color: {p['hover']};
    }}
    QTableCornerButton::section {{
        background-color: transparent;
        border: none;
    }}
    QTableWidget#KeyValue {{
        background-color: transparent;
        border: none;
        border-radius: 0px;
        font-size: {FONT_SMALL}pt;
    }}
    QTableWidget#KeyValue::item {{
        padding: 2px {SPACE_XS}px;
        border-bottom: 1px solid {p['row_rule']};
    }}

    /* ===== Menus ===== */
    QMenuBar {{
        background-color: {p['surface']};
        color: {p['text_strong']};
        padding: 0px {SPACE_S}px;
        border: none;
    }}
    QMenuBar::item {{
        padding: {SPACE_XS + 2}px {SPACE_M - 1}px;
        border-radius: {RADIUS - 2}px;
        color: {p['text_strong']};
    }}
    QMenuBar::item:selected {{
        background-color: {p['hover']};
        color: {p['text']};
    }}
    QMenu {{
        background-color: {p['surface_elevated']};
        color: {p['text']};
        border: 1px solid {p['border_light']};
        border-radius: {RADIUS_LG}px;
        padding: {SPACE_XS}px;
    }}
    QMenu::item {{
        padding: {SPACE_XS + 2}px {SPACE_L * 2}px {SPACE_XS + 2}px {SPACE_M}px;
        border-radius: {RADIUS - 2}px;
    }}
    QMenu::item:selected {{
        background-color: {p['highlight_soft']};
        color: {p['text']};
    }}
    QMenu::item:disabled {{
        color: {p['disabled']};
        background-color: transparent;
    }}
    QMenu::separator {{
        height: 1px;
        background: {p['divider']};
        margin: {SPACE_XS}px {SPACE_S}px;
    }}

    /* ===== Progress, status ===== */
    QProgressBar {{
        border: 1px solid {p['divider']};
        border-radius: {RADIUS}px;
        text-align: center;
        background-color: transparent;
        color: {p['text']};
        font-size: {FONT_SMALL}pt;
    }}
    QProgressBar::chunk {{
        background-color: {p['highlight_deep']};
        border-radius: {RADIUS - 1}px;
    }}
    QStatusBar {{
        background-color: {p['surface']};
        color: {p['text_secondary']};
        font-size: {FONT_META}pt;
        border-top: 1px solid {p['divider']};
    }}
    QStatusBar QLabel {{
        color: {p['text_secondary']};
        font-size: {FONT_META}pt;
    }}
    QStatusBar::item {{
        border: none;
    }}

    /* ===== Scroll bars ===== */
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {p['border_light']};
        min-height: 30px;
        border-radius: 3px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {p['switch_track_hover']};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 10px;
        margin: 0;
    }}
    QScrollBar::handle:horizontal {{
        background: {p['border_light']};
        min-width: 30px;
        border-radius: 3px;
        margin: 2px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {p['switch_track_hover']};
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

"""
The tutorial (HelpMixin.show_tutorial; not in the Help menu for now):
SolarViewer explained task by task, in a window that stays open beside the
main one.

The text is data, in ``SECTIONS`` below: sections made of blocks (a
paragraph, numbered steps, a bullet list, a table of keys, a tip, the
main window's figure), drawn by the generic code at the end of the file.
To change what the tutorial says, change the data; tests/test_tutorial.py
checks it against the menus and the image's keys.

Inline markup in the text:

- ``**Fast load**``: a name the window shows, in bold
- ```Ctrl+O```: a key, drawn as a key cap
- ``%%solarviewer image.fits%%``: something to type, in the fixed font
- ``{File → Open image…}``: a path in the main window's menus, in bold;
  ``{File → Open image…|Ctrl+O}`` with its shortcut (more keys after more
  bars). Both are checked against the menu bar by the tests.
- ``{ds:View → Go to time…|Ctrl+G}``: a menu path of another window
  (``OTHER_WINDOWS`` says which), not one of the main window's
- ``[[contours]]``, ``[[contours|the contour settings]]``: a link to
  another section, by its id, named by its title or by the words given
"""

import html
import re
from collections import namedtuple

from PyQt5.QtCore import QEvent, QPointF, QRectF, QSettings, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPen, QPolygonF, QRadialGradient
from PyQt5.QtWidgets import (
    QAbstractButton,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .ui.geometry import fit_to_screen
from .ui.styles import FONT_META, SPACE_L, SPACE_M, SPACE_S, SPACE_XS, set_hand_cursor, theme_manager
from .ui.widgets import empty_note, kicker, link_button, rule

# ---------------------------------------------------------------------------
# The blocks a section is made of
# ---------------------------------------------------------------------------

Section = namedtuple("Section", "id group title blocks window")
Section.__new__.__defaults__ = ("main",)
Section.__doc__ = """A page of the tutorial. ``window`` is "main" when it is
about the main window (its keys are then checked against the main window's
menus and image keys), else "other"."""

Para = namedtuple("Para", "text")
Heading = namedtuple("Heading", "text")
Tip = namedtuple("Tip", "text")
Steps = namedtuple("Steps", "items")  # numbered; items are str or Item
Bullets = namedtuple("Bullets", "items")  # items are str or Item
Item = namedtuple("Item", "text try_it")  # try_it: a menu path, or ("button", tooltip)
Keys = namedtuple("Keys", "rows source")
Keys.__doc__ = """A table of keys: rows of (keys, what they do), the keys a
string or a tuple of alternatives. ``source`` says what they are checked
against: "image" (the image's keys, HelpMixin.IMAGE_KEYS), "mouse"
(HelpMixin.MOUSE_GESTURES), "menu" (what they do is a menu path whose
action has these shortcuts), or "other" (another window's)."""
Figure = namedtuple("Figure", "name legend")  # legend: [(title, text)], numbered as the markers
Button = namedtuple("Button", "text try_it")  # a button doing ``try_it``

# The windows whose menu paths the text gives as {scope:Menu → Item}, and
# the modules that build those menus (the tests look for the names there)
OTHER_WINDOWS = {
    "ds": ("Dynamic spectrum viewer", ("pipeline/view_dynamic_spectra_GUI.py",)),
}

GROUPS = (
    "Getting started",
    "Images",
    "Measure and annotate",
    "Save and convert",
    "Other windows",
    "Reference",
)

# Where the last section read is kept, to open there next time
SETTINGS = ("SolarViewer", "SolarViewer")
LAST_SECTION_KEY = "tutorial/section"

# ---------------------------------------------------------------------------
# What the tutorial says
# ---------------------------------------------------------------------------

_MAIN_WINDOW_LEGEND = (
    ("Menu bar", "**File**, **View**, **Image**, **Tools**, **Download**, **Apps** and **Help**."),
    (
        "Image tabs",
        "Each tab holds an image or, with a layout from the bar under the image, up to six side by "
        "side ([[comparing]]); **+** opens a new tab. See [[opening]].",
    ),
    (
        "Top bar",
        "The image's path, the Stokes product with its **Threshold** and **Within**, the colormap and "
        "the stretch. See [[display]] and [[stokes]].",
    ),
    ("The image", "With its colorbar. Drop files here to open them. See [[navigation]]."),
    (
        "Bar under the image",
        "The tools (**Pan**, **Rectangle**, **Ellipse**, **Ruler**, **Profile**, **Annotate**), the "
        "files of the folder, the zoom, and the layouts for [[comparing|comparing images]].",
    ),
    (
        "Inspector header",
        "**Jy/beam** | **K** and **RA/Dec** | **Solar XY** ([[coordinates]]), and two buttons that open "
        "the [[activity|Solar activity viewer]] and the [[helioviewer|Helioviewer browser]] at the "
        "image's time.",
    ),
    (
        "Inspector",
        "**Display**: overlays, intensity range, loading options and a summary of the image "
        "(**Image**). **Statistics**: the regions' and the image's numbers ([[regions]]). With several "
        "images, a **Compare** tab.",
    ),
    ("Cursor", "The pixel, the position and the value under the pointer ([[coordinates]])."),
    (
        "Status bar",
        "Messages, the connection to a server (**Not connected**, see [[remote]]) and, when there is "
        "one, **Update available**.",
    ),
)

_IMAGE_KEYS = (
    ("R", "Reset the view: the whole image"),
    ("1", "Zoom to 1° × 1° about the middle of the view (an image with sky coordinates)"),
    (("+", "="), "Zoom in"),
    ("-", "Zoom out"),
    ("Arrow keys", "Move the view"),
    ("H", "Pan tool"),
    ("B", "Rectangle tool: draw a region"),
    ("E", "Ellipse tool: draw a region"),
    ("L", "Ruler tool: measure a distance"),
    ("P", "Profile tool: the values along a line"),
    ("A", "Annotate tool: text, arrows and shapes"),
    (("Delete", "Backspace"), "Delete the selected region or annotation"),
    (("Space", "Enter"), "Redraw the image; with the **Annotate** tool, `Enter` changes the selected "
                         "annotation"),
    ("Esc", "Leave a text field, so that the keys above reach the image; deselect"),
    (("[", "]"), "Previous / next file in the folder"),
    (("{", "}"), "First / last file in the folder"),
    (("Ctrl+Tab", "Ctrl+Shift+Tab"), "Next / previous tab"),
    (("Ctrl+PgDown", "Ctrl+PgUp"), "Next / previous tab"),
)

_MENU_KEYS = (
    (("Ctrl+O", "Ctrl+Shift+O"), "{File → Open image…}"),
    ("Ctrl+N", "{File → New tab}"),
    ("Ctrl+W", "{File → Close tab}"),
    (("Ctrl+S", "Ctrl+E"), "{File → Export figure…}"),
    ("Ctrl+Shift+F", "{File → Export image as → FITS…}"),
    ("Ctrl+H", "{File → Export image as → Helioprojective FITS…}"),
    ("Ctrl+Shift+R", "{File → Remote server → Connect…}"),
    ("Ctrl+,", "{File → Preferences…}"),
    ("Ctrl+Q", "{File → Exit}"),
    ("Ctrl+D", "{View → Switch to light mode} (or to dark mode)"),
    ("F11", "{View → Toggle fullscreen}"),
    ("Ctrl+L", "{View → Log console}"),
    ("Ctrl+M", "{Image → Image metadata…}"),
    ("F5", "{Image → Display presets → Auto min/max}"),
    ("F6", "{Image → Display presets → Auto percentile (1%,99%)}"),
    ("F7", "{Image → Display presets → Auto percentile (0.1%,99.9%)}"),
    ("F8", "{Image → Display presets → AIA presets → 171 Å}"),
    ("F9", "{Image → Display presets → HMI preset}"),
    ("Ctrl+T", "{Image → Annotate → Text}"),
    ("Ctrl+Shift+A", "{Image → Annotate → Arrow}"),
    ("Ctrl+Z", "{Image → Annotate → Undo}"),
    (("Ctrl+Shift+Z", "Ctrl+Y"), "{Image → Annotate → Redo}"),
    ("Ctrl+G", "{Image → Fit 2D Gaussian}"),
    ("Ctrl+Shift+S", "{Image → Export region as sub-image…}"),
    ("Ctrl+R", "{Image → Export region as CASA region…}"),
    ("Ctrl+P", "{Tools → Solar phase center shift…}"),
    ("F1", "{Help → Keyboard shortcuts}"),
)

# The gestures are written as the shortcuts window has them; a modifier
# before a hyphen ("Shift-drag") is drawn as a key
_MOUSE = (
    (("Scroll", "Pinch"), "Zoom about the pointer ({View → Interactive scroll zoom} turns the scroll "
                          "zoom off)"),
    ("Middle-drag", "Move the image; with the **Pan** tool, a left drag too"),
    ("Right-drag", "Zoom to the box drawn"),
    ("Double-click", "Zoom in, with the **Pan** tool (with `Shift`: out); change an annotation, with "
                     "**Annotate**"),
    ("Shift-drag", "A square or a circle (**Rectangle**, **Ellipse**); 15° steps when turning"),
    ("Right-click", "What can be done with the annotation under the pointer (**Annotate**)"),
)

SECTIONS = (
    # ------------------------------------------------------------ Getting started
    Section("welcome", "Getting started", "Welcome", (
        Para(
            "SolarViewer shows FITS files and CASA images of the Sun, and is made for radio images: "
            "Stokes products, the beam, brightness temperature and helioprojective coordinates. "
            "Around it are windows for dynamic spectra, solar activity, Helioviewer images, "
            "downloads and videos."
        ),
        Heading("How to use this tutorial"),
        Bullets((
            "The list at the left has every topic, grouped by task. Type a word in the search field "
            "above it to keep the topics that mention it; `Enter` opens the best match.",
            "**Try it** beside a step does that step in the main window, as its menu would. The "
            "tutorial stays open beside it, so you can follow along.",
            "The keys a step names work in the main window: click the image there first, then press "
            "the key. In this window they would go to the search field.",
            "**Previous** and **Next** at the bottom go through the topics in order. The tutorial "
            "opens again where you left it.",
            "Names in **bold** are written as the window shows them; keys look like `Ctrl+O`; and "
            "{Image → Fit 2D Gaussian} means the **Fit 2D Gaussian** item of the **Image** menu.",
            "Most controls say what they do when the pointer rests on them.",
        )),
        Heading("Start SolarViewer"),
        Bullets((
            "From a terminal: %%solarviewer%% (or %%sv%%); with an image to open it at once, as in "
            "%%solarviewer image.fits%%.",
            "%%--fast%% starts with **Fast load** on, %%--light%% in the light theme; %%--install%% "
            "adds SolarViewer to the desktop's list of applications.",
            "The other windows start on their own too: %%viewds%% (dynamic spectra), %%createds%%, "
            "%%viewsolaractivity%%, %%heliobrowser%%, %%viewcaltable%% and %%viewlogs%%.",
        )),
        Tip("New to SolarViewer? [[first-steps]] opens an image, sets its levels, measures a source "
            "and saves a figure, in seven steps. [[keyboard]] lists the keys, and [[troubleshooting]] "
            "helps when something does not work."),
    )),
    Section("first-steps", "Getting started", "Your first five minutes", (
        Para("An image opened, looked at, measured and saved. Keep the main window beside this one "
             "and follow along. The keys work in the main window: click the image there first, then "
             "press the key."),
        Steps((
            Item("Open an image: {File → Open image…|Ctrl+O}, or drop a FITS file or a CASA image on "
                 "the window. A CASA image is a folder: select it and press **Open**. No image at "
                 "hand? {Download → Solar data…} fetches solar images to practise on ([[downloads]]).",
                 "File → Open image…"),
            "Find your way: the image is in the middle; how it is shown is set in the top bar above it "
            "and in the inspector at the right. [[main-window]] names every part.",
            "Set the levels: in the inspector's **Display** tab press **Auto**, **1–99%** or **0.1–99.9%** "
            "(`F5`, `F6`, `F7`), or drag the two markers on the histogram. Pick a colormap and a "
            "stretch in the top bar. More in [[display]].",
            "Look closer: scroll over the image to zoom at the pointer, drag with the middle button to "
            "move it, and press `R` to see all of it again. More in [[navigation]].",
            "Measure a source: press `B` for the **Rectangle** tool and drag a box round it, then "
            "click the **Statistics** tab of the inspector: it gives the Min, Max, Mean, Std Dev, Sum "
            "and RMS of the pixels inside. More in [[regions]].",
            "Go through the folder: `]` opens the next image of the same folder and `[` the one "
            "before, with the same view. More in [[opening]].",
            Item("Save a figure: {File → Export figure…|Ctrl+S} saves the image as it is shown, as PNG "
                 "or PDF. More in [[exporting]].", "File → Export figure…"),
        )),
        Tip("While you type in a field, the keys go to the field: press `Esc` to give them back to "
            "the image."),
    )),
    Section("main-window", "Getting started", "The main window", (
        Para("One window holds the images: they are opened, shown, measured and saved here. "
             "The numbers on the drawing match the list under it."),
        Figure("main-window", _MAIN_WINDOW_LEGEND),
        Tip("{View → Switch to light mode|Ctrl+D} switches SolarViewer's windows, the Dynamic spectrum "
            "viewer included, between the dark and the light theme (in the light theme it reads "
            "**Switch to dark mode**). **Create dynamic spectra**, the **Calibration table "
            "visualizer** and the **Log viewer** change theme only when they are opened again."),
    )),
    # ------------------------------------------------------------ Images
    Section("opening", "Images", "Opening and browsing", (
        Heading("Open an image"),
        Bullets((
            Item("{File → Open image…|Ctrl+O}, or the folder icon at the left of the path field, opens "
                 "a FITS file (.fits, .fts, .fit) or a CASA image. A CASA image is a folder: "
                 "double-click it, or select it and press **Open**.", "File → Open image…"),
            "Type or paste a path in the path field (**FITS file or CASA image**) and press `Enter`. "
            "An empty tab offers **Open file…** and **Paste path**, and lists the latest images "
            "under **Recent**.",
            "{File → Open recent} lists the last 30 images and spectra opened; the status bar shows "
            "the full path of the one under the pointer. **Clear list** empties it.",
            "Drop files on the window: the first opens in the current tab if it is empty, the others "
            "in new tabs.",
            "An image that takes a while shows **Please wait** with **Cancel**, which stops the "
            "reading; the tab is then left without an image.",
            "Images on another computer: {File → Remote server → Connect…}. While connected, "
            "{File → Open image…} shows the server's files ([[remote]]).",
            "A dynamic spectrum opened in any of these ways goes to the "
            "[[spectrum-viewer|Dynamic spectrum viewer]]; {File → Open dynamic spectrum…} opens one "
            "there directly.",
        )),
        Tip("Big images open faster with **Fast load** on (inspector, **Display** tab, **Loading**): "
            "they are read at a reduced resolution. Turn it off before you measure."),
        Heading("Go through a folder"),
        Para("The ‹ and › buttons in the middle of the bar under the image (**Previous file in "
             "directory**, **Next file in directory**), and the first and last buttons beside them, "
             "step through the images of the same folder whose names match a pattern: at first, "
             "those with the open image's extension (*.fits, say)."),
        Keys((
            (("[", "]"), "Previous / next file"),
            (("{", "}"), "First / last file"),
        ), "image"),
        Bullets((
            "The position (**1/59**, say) tells where you are; the pointer on it shows the file's "
            "name and the pattern.",
            "The funnel button (**Set file filter pattern**) chooses the files: * stands for any "
            "part of a name, as in *110.74MHz*.fits or *.image.",
            "The list button (**File list**) shows the files in a window with a search box: "
            "double-click one to open it. The File list keeps the folder it was opened on: after "
            "opening an image from another folder, close the File list window and open it again.",
            "The zoom and pan, the RA/Dec or Solar XY view and the Jy/beam or K unit carry over to "
            "the next image. Each new image is shown from its minimum to its maximum (**Auto**), or "
            "with its instrument's preset; turn on **Keep for next images** to keep the range and the "
            "radial filter instead, for a time series ([[display]]).",
        )),
        Heading("Tabs"),
        Bullets((
            "Each tab holds an image, with its own folder, Stokes product, display settings, regions "
            "and annotations, or up to six images side by side ([[comparing]]). The tabs are at the "
            "right end of the menu bar.",
            "{File → New tab|Ctrl+N}, or **+** after the tabs, opens a tab; {File → Close tab|Ctrl+W}, "
            "or the tab's **×**, closes it.",
            "`Ctrl+Tab` and `Ctrl+Shift+Tab` (or `Ctrl+PgDown` and `Ctrl+PgUp`) go to the next and "
            "the previous tab, round from the last to the first.",
            "Double-click a tab to rename it; the name lasts until another image opens in it.",
        )),
        Heading("Good to know"),
        Bullets((
            Item("{Image → Image metadata…|Ctrl+M} shows the header: observation, frequency, beam and "
                 "the image's coordinates, and every keyword under **All**, with a filter.",
                 "Image → Image metadata…"),
            "Of an image with several frequency or time planes (a cube), the first plane is shown.",
            "An image without sky coordinates is shown in pixels; the beam, the solar disk and "
            "Solar XY do not apply to it.",
        )),
    )),
    Section("display", "Images", "Display", (
        Para("How the image is shown: its colours in the top bar, its levels (the contrast) in the "
             "inspector's **Display** tab. Display changes apply at once and never change the file."),
        Heading("Colormap and stretch"),
        Bullets((
            "The colormap list in the top bar has the usual maps. The magnifier beside it (**Search "
            "all colormaps**) finds any Matplotlib or solar colormap by name, reversed ones (ending "
            "in _r) too.",
            "The stretch list sets how the values between **Min** and **Max** become colours: "
            "**linear**, **sqrt** and **log** bring out fainter and fainter emission, **arcsinh** is "
            "like log for negative values too, and **power** follows **Gamma**.",
            "**zscale** finds its own range in the image, as DS9's ZScale does (**Min**, **Max**, "
            "the histogram's markers and **Auto**, **1–99%** and **0.1–99.9%** are greyed while it "
            "is on): the faint emission around a bright Sun shows, the Sun itself is saturated. **histeq** gives each colour about as many of the pixels between **Min** "
            "and **Max**, so the most common values (the noise, the faint emission) get most of "
            "the colours; its colorbar is labelled at even steps.",
            "**Gamma** (the slider and box under **Min** and **Max**) is used by the **power** "
            "stretch only: below 1 it brings out faint emission, above 1 the bright sources.",
        )),
        Heading("Intensity range"),
        Bullets((
            "**Min** and **Max** are the values at the two ends of the colormap: type one and press "
            "`Enter`, or drag the markers on the histogram above them. Double-click the histogram "
            "for the image's whole range.",
            "**Auto** (`F5`) is the image's minimum to its maximum, **1–99%** (`F6`) the 1st to the "
            "99th percentile, and **0.1–99.9%** (`F7`) the 0.1 to the 99.9 percentile: wider, it keeps "
            "more of a bright source without letting one hot pixel set the range. The button "
            "outlined is the one that set the range.",
            "{Image → Display presets} has these too, and **Auto percentile (5%,95%)** and "
            "**Auto median ± 3×RMS** (the median and three times the spread of the whole image "
            "about it, either side: not the RMS of the RMS box).",
            "**Keep for next images** shows the next images (`[`, `]`) with the same range, stretch, "
            "gamma, colormap and radial filter, for a time series; without it each new image starts "
            "from its minimum and maximum, or its instrument's preset. An image in another unit or "
            "Stokes product is scaled afresh.",
            "**Reset** puts the display back as the image opened.",
        )),
        Heading("Instrument presets"),
        Bullets((
            "{Image → Display presets} has the colormap, stretch and range for images of AIA, HMI, "
            "SOHO/EIT, SOHO/LASCO, IRIS SJI, GOES SUVI and STEREO, as "
            "{Image → Display presets → AIA presets → 171 Å|F8} and "
            "{Image → Display presets → HMI preset|F9}.",
            "With {Image → Display presets → Auto-apply instrument presets} on (as it is at first), "
            "an image from a known instrument opens with its preset.",
            "{Image → Display presets → Radial filter} flattens the fall of brightness away from the "
            "Sun, for coronagraph images: **Divide by radial median**, **Sigma from radial median** "
            "or **Sigma from Fourier background (FNRGF)**. For such images a **Radial filter** row "
            "appears under **Gamma** too.",
            "In the same submenu, **Subtract background** takes away the background a prepared LASCO "
            "image carries: the F corona and stray light. For such images the **Background** row "
            "(**Subtract the F corona and stray light**) does the same.",
        )),
        Heading("Overlays"),
        Para("The **Overlays** block of the **Display** tab draws on the image: **Beam** (the "
             "restoring beam, at the bottom left), **Grid** (coordinate lines), **Solar disk** (the "
             "limb) and **Contours** (see [[contours]]). The gear beside each opens its settings: "
             "colours and lines, the levels of the contours, and for **Solar disk** also its centre "
             "and size (the **Position** tab), to put a limb drawn in the wrong place right."),
        Heading("Loading"),
        Bullets((
            "**Fast load** reads large images at a reduced resolution, for quick browsing; it says "
            "**preview** while it is on. Turn it off before you measure.",
            "**RMS box…** sets where the noise (RMS) is measured: by default a band along the bottom "
            "of the image. The RMS sets the **Threshold** of the derived Stokes products and the "
            "image's dynamic range, so choose a part with no source in it. It applies to the image "
            "shown: each new image, the next one with `]` too, starts again with the default band.",
            "**Plot style…** sets the title, labels, fonts, colours and margins of the plot.",
            "**Reload image** reads the file again from disk, after it was written anew.",
        )),
        Tip("**Image**, at the foot of the **Display** tab, gives the image's size and its Max, RMS "
            "and positive dynamic range (**Pos. DR**). The **Statistics** tab has more ([[regions]])."),
    )),
    Section("stokes", "Images", "Stokes and polarization", (
        Para("For images with polarization planes, the top bar chooses what is shown."),
        Bullets((
            "**I** (the total intensity), **Q** and **U** (the linear polarization) and **V** (the "
            "circular polarization) show those planes. Those the image does not have are greyed.",
            "**More** has the products made from them: **L** (the linear polarization, √(Q² + U²)), "
            "**Lfrac** (L/I), **Vfrac** (V/I, the fraction of circular polarization), **Q/I**, "
            "**U/I**, **U/V** and **PANG** (the polarization angle, in degrees). Those the image "
            "cannot make are greyed.",
            "An image holding correlations (RR, LL, XX, YY…) offers those in **More** too, and the "
            "Stokes parameters made from them: **I** and **V** from RR and LL, **I** and **Q** from "
            "XX and YY.",
            "If the next image lacks the product shown, SolarViewer says so (**Stokes parameter "
            "unavailable**) and shows Stokes I, or the first product the image has.",
            "The colorbar and the cursor readout follow the product's unit; the ratios have none.",
        )),
        Heading("Threshold and Within"),
        Para("The ratios and the angle are noise where the signal is weak, so they are shown only "
             "where it is strong, near the Sun:"),
        Bullets((
            "**Threshold** (in σ, 10 at first): Lfrac, Vfrac, Q/I, U/I and U/V are left blank where "
            "their numerator is below Threshold × RMS, and PANG where the polarized intensity is.",
            "**Within** (in arcminutes, 55 at first): all six are left blank further than this from "
            "the Sun's centre; 0 shows them over the whole image.",
            "Both are greyed for I, Q, U, V, L and the correlations, which are shown as they are.",
            "The RMS is measured in the RMS box: **RMS box…** in the **Loading** block of the "
            "**Display** tab ([[display]]).",
        )),
        Tip("A ratio that is blank all over: lower the **Threshold**, press `Enter`, then press "
            "**Auto** (`F5`) to rescale."),
    )),
    Section("coordinates", "Images", "Coordinates and units", (
        Para("Two switches at the top of the inspector change how the image is read."),
        Bullets((
            "**RA/Dec** | **Solar XY** reprojects the image between sky coordinates and helioprojective "
            "ones: Solar X and Solar Y, in arcseconds from the Sun's centre, solar north up. The "
            "axes, the grid and the cursor readout follow. It needs an image in RA/Dec whose header "
            "gives the time of the observation (DATE-OBS) and the telescope (TELESCOP, or "
            "OBSGEO-X/Y/Z).",
            "Images already in helioprojective coordinates (AIA, HMI and the other images of "
            "{Download → Solar data…}) open in **Solar XY**; the switch then shows them in RA/Dec.",
            "**Jy/beam** | **K** shows a radio image as brightness temperature (K, the Tb) or in its "
            "flux density. It needs an image in Jy/beam (or K) with the beam and the frequency in "
            "its header; otherwise it is greyed.",
            "Both show converted copies of the image; the file is not changed. After switching both, "
            "switching back does not always give the file's own values: to see the file as it is on "
            "disk, open it again ({File → Open recent}).",
        )),
        Heading("The cursor"),
        Para("The **Cursor** block at the foot of the inspector follows the pointer over the image: "
             "**Pixel** (x, y), the position (**RA/Dec** or **Solar XY**) and **Value**, in the "
             "image's unit. Off the image it says **off the image**."),
        Tip("For a value to keep, such as the exact peak of a source, draw a region round it: the "
            "**Statistics** tab gives its Max ([[regions]])."),
    )),
    Section("navigation", "Images", "Moving around the image", (
        Heading("With the mouse"),
        Keys(_MOUSE[:4], "mouse"),
        Heading("With the keys"),
        Keys((
            (("+", "="), "Zoom in"),
            ("-", "Zoom out"),
            ("R", "Reset the view: the whole image"),
            ("1", "Zoom to 1° × 1° about the middle of the view (an image with sky coordinates)"),
            ("Arrow keys", "Move the view"),
            ("H", "The Pan tool"),
        ), "image"),
        Heading("With the buttons"),
        Para("Near the right end of the bar under the image, before the layout buttons, are **Zoom "
             "in** (`+`), the zoom level, **Zoom out** (`-`), **Reset view** (`R`) and **1°×1° Zoom** "
             "(`1`). In a narrow window some buttons give way; their keys still work."),
        Bullets((
            "While the **Ruler** or **Profile** tool is chosen, the mouse neither zooms nor moves the "
            "image: choose **Pan** (`H`), zoom, then measure.",
            "{View → Toggle fullscreen|F11} gives the window the whole screen.",
            "The single-letter keys reach the image only when no text field has the keyboard: press "
            "`Esc` first.",
        )),
    )),
    # ------------------------------------------------------------ Measure and annotate
    Section("regions", "Measure and annotate", "Regions and statistics", (
        Para("A region is a rectangle or an ellipse drawn on the image to measure what is inside it."),
        Steps((
            "Choose **Rectangle** (`B`) or **Ellipse** (`E`) in the bar under the image and drag on "
            "the image. Hold `Shift` for a square or a circle.",
            "The **Statistics** tab of the inspector shows the region's Min, Max, Mean, Std Dev, Sum "
            "and RMS and, above them, its centre and size on the sky; the status bar repeats the "
            "main ones.",
            "With either tool, click a region to select it: drag it to move it, drag its handles to "
            "resize it and the round handle to turn it (`Shift`: in 15° steps).",
            "With **Rectangle** or **Ellipse** chosen, `Delete` or `Backspace` removes the selected "
            "region and `Esc` deselects it; **Delete**, beside the **Region** heading, does the same, "
            "and **Clear all** removes every region (it asks first).",
        )),
        Para("Under the region's numbers:"),
        Bullets((
            "**Histogram**: the region's pixel values in a window of their own, with the number of "
            "bins, a log scale, and a Gaussian or Poisson fit.",
            "**Fit Gaussian**: a 2D Gaussian fitted in the region ([[measuring]]).",
            "**Export**: the region as a sub-image or as a CASA region file ([[exporting]]).",
        )),
        Para("Regions stay when you step to the next file (`[`, `]`) and are measured again on it, so "
             "one box follows a source through a series; annotations stay too. A region that falls "
             "off the new image is removed."),
        Heading("The whole image"),
        Para("Under **Image**, the **Statistics** tab gives the image's Max, Min, RMS, Box mean, and "
             "positive and negative dynamic range (**Pos. DR**, **Neg. DR**). The RMS is measured in "
             "the box set with **RMS box…** in the **Display** tab ([[display]])."),
        Tip("A deleted region cannot be brought back (`Ctrl+Z` undoes annotations only), and regions "
            "are not drawn in exported figures: for a box in a figure, use the **Annotate** tool "
            "([[annotations]])."),
    )),
    Section("measuring", "Measure and annotate", "Fitting, profiles and the ruler", (
        Heading("Fit a 2D Gaussian"),
        Steps((
            "Draw a region round the source, a little larger than it ([[regions]]).",
            Item("{Image → Fit 2D Gaussian|Ctrl+G}, or **Fit Gaussian** in the **Statistics** tab.",
                 "Image → Fit 2D Gaussian"),
            "Choose **Fit offset** if the source sits on a background that is not zero, else "
            "**Zero background**.",
            "**Fit result** gives the fitted component (its position, **Size**, **Position angle** "
            "and **Flux**) and its size deconvolved from the beam; **Copy** copies them. The log, "
            "in {View → Log console|Ctrl+L}, has more of the fit: the peak, the pixel position and "
            "the errors.",
        )),
        Para("The fit is CASA's imfit, run on the file itself in Stokes I, Q, U or V; a derived "
             "product is fitted as Stokes I."),
        Heading("Measure a distance"),
        Steps((
            "Choose **Ruler** (`L`) in the bar under the image.",
            "Click two points on the image (`Shift`: a horizontal or a vertical line). The distance "
            "is drawn on the image and given in the status bar.",
        )),
        Heading("Plot a profile"),
        Steps((
            "Choose **Profile** (`P`), pick **Line** or **Radial** in **Profile cut method**, and press "
            "**Continue**.",
            "Line: click the two ends of the cut (`Shift`: horizontal or vertical), correct them in "
            "**Profile cut coordinates** if needed, and press **Extract profile**.",
            "Radial: click the centre; in **Radial profile settings** set the **Angle** (0° is "
            "east-west) and the **Length** (100 px at first; the cut runs that far both ways), and "
            "press **Extract profile**.",
            "The **Flux profile** window plots the values along the cut, with the FWHM when there is "
            "one clear peak. **Save CSV…** saves the values.",
        )),
        Tip("The ruler's lines and the profile's cut stay on the image, and in an exported figure, "
            "until you choose another tool (`H` for **Pan** clears them)."),
    )),
    Section("contours", "Measure and annotate", "Contours", (
        Steps((
            "Turn on **Contours** in the **Overlays** block of the **Display** tab: the image gets "
            "contours of its Stokes I (choose another product in the **Source** tab).",
            Item("Its gear opens **Contour settings**.", ("button", "Contour Settings")),
            "Change what you need and press **Apply**. The window stays open, so you can try again; "
            "**Close** closes it.",
        )),
        Heading("In Contour settings"),
        Bullets((
            "**Layers**: up to 10 sets of contours at once. **Add**, **Duplicate** and **Remove** "
            "them, and untick one to hide it; the tabs at the right (**Source**, **Levels**, "
            "**Appearance**, **RMS**) set the selected layer.",
            "**Source**: the **Current image** or an **External** file (another frequency or "
            "instrument, say), and its Stokes product.",
            "**Levels**: the **Level type**, **Fraction** (of the largest absolute value), **Sigma** "
            "(times the RMS) or **Absolute**; the presets **5 levels**, **3 levels** and **Dense**; "
            "and the **Positive** and **Negative** levels. **Negative** levels are typed as positive "
            "numbers: with **Fraction**, 0.3 draws a contour at −0.3 times the peak.",
            "**Appearance**: the colour, line style and width of the positive and the negative "
            "contours, and **Contour labels**.",
            "**RMS**: where the RMS of **Sigma** levels is measured: **Use default region**, or a "
            "box of your own.",
            "**Options**, for all the layers: **Show legend** (where, its **Font size**, and whether to "
            "**Include negative contours**), **Full extent** and **Downsample**.",
        )),
    )),
    Section("annotations", "Measure and annotate", "Annotations", (
        Para("Annotations are text, arrows, rectangles and ellipses on the image, for figures. Unlike "
             "regions, they are drawn in exported figures."),
        Steps((
            "Choose **Annotate** (`A`) in the bar under the image: a strip of buttons appears at the "
            "top left of the image.",
            "Pick **Text**, **Arrow**, **Rectangle** or **Ellipse** in the strip, and a colour.",
            "Text: click where it goes, type, and press `Enter` (`Esc` drops it). Arrow: drag from its "
            "tail to its head. Rectangle and ellipse: drag (`Shift`: a square or a circle).",
            "Click an annotation to select it and drag it, or its handles. Double-click it (or press "
            "`Enter`) to change it; `Delete` removes it.",
        )),
        Bullets((
            "The strip's properties button, or a right-click and **Properties…**, opens **Annotation "
            "properties**: exact position, size, colour, line, font and fill.",
            "A right-click on an annotation also offers **Duplicate** and **Delete** (and **Edit "
            "text**, on a text).",
            "The strip's last button has **Solar radius circle** (dashed circles from 1 to 10 R☉ "
            "round the Sun), **Undo** and **Redo**, and **Delete all annotations…** to clear the image.",
            "{Image → Annotate} has the same commands: {Image → Annotate → Text|Ctrl+T}, "
            "{Image → Annotate → Arrow|Ctrl+Shift+A}, {Image → Annotate → Undo|Ctrl+Z} and "
            "{Image → Annotate → Redo|Ctrl+Shift+Z|Ctrl+Y}.",
        )),
        Tip("`Ctrl+Z` undoes up to 100 changes to the annotations, deletions too; it does not bring "
            "back regions. The annotations stay when you step to the next file; their history starts "
            "afresh after a switch between RA/Dec and Solar XY."),
    )),
    Section("comparing", "Measure and annotate", "Comparing images", (
        Para("Two ways to look at several images: tabs, one image each ([[opening]]), or several "
             "images side by side in one tab."),
        Heading("Side by side"),
        Steps((
            "Choose a layout at the right end of the bar under the image: **One image**, **Two images "
            "side by side**, **Up to four images in a grid** or **Up to six images in two rows of "
            "three**.",
            "An empty pane offers **Add image…**, or takes a file dropped on it.",
            "Click a pane to make it the active one: the top bar and the inspector then show and set "
            "its image, and `[` `]` step through its folder.",
        )),
        Para("The menus work on the active pane too: {File → Export figure…} saves that pane alone, "
             "and {Image → Fit 2D Gaussian} fits in it."),
        Heading("What is kept in step"),
        Bullets((
            "The link button in the bar under the image names the panes that follow each other. "
            "Click it to choose what they share: **Zoom & pan** and **Cursor crosshair** at first. "
            "Turn on **Frame stepping** for `[` `]` `{` `}` in one pane to step every linked pane "
            "through its own folder, and **Intensity range** for a range set in one pane to go to the "
            "panes showing the same Stokes product in the same unit. **Sync all** brings every pane "
            "back in.",
            "The link button in a pane's header lets that pane go its own way.",
            "Panes follow each other only within one frame: a pane in the other frame shows **Other "
            "frame** (switch it with **RA/Dec** | **Solar XY**), and one showing another part of the "
            "sky shows **No overlap**.",
            "A region drawn in one pane is drawn in the panes that follow it, at the same place on the "
            "sky.",
            "The **Compare** tab of the inspector turns **Axes**, **Colorbar**, **Solar disk** and "
            "**Grid** on or off in every pane. **Apply to all panes** (there, and in the **Editing** "
            "row over the inspector's tabs) gives every pane the active pane's colormap, stretch, "
            "gamma and overlay switches (not its contours), and its range where the Stokes product "
            "and unit match.",
        )),
        Heading("The values side by side"),
        Para("**Values at cursor**, under the cursor readout, gives the value under the pointer in "
             "each pane, and the **Compare** tab's **Region** and **Image** tables put the panes' "
             "statistics side by side."),
    )),
    # ------------------------------------------------------------ Save and convert
    Section("exporting", "Save and convert", "Exporting", (
        Heading("A figure"),
        Bullets((
            Item("{File → Export figure…|Ctrl+S|Ctrl+E} saves the image as it is shown, with its axes, "
                 "colorbar, contours, overlays and annotations, at 300 dpi, as PNG or PDF. For an SVG, "
                 "choose **All files (*)** and type a name ending in .svg.", "File → Export figure…"),
            "Regions are not drawn in it. The ruler's and the profile's lines are, while their tool "
            "is chosen ([[measuring]]).",
            "With several images in the tab, it saves the active pane alone ([[comparing]]).",
            "The figure keeps the theme's background: for a light one, switch to the light theme "
            "first with {View → Switch to light mode|Ctrl+D}.",
        )),
        Heading("The image's data"),
        Para("{File → Export image as} writes the image as a new file (for a folder of images, see "
             "[[tools-menu]]):"),
        Bullets((
            "{File → Export image as → FITS…|Ctrl+Shift+F}: the image file as FITS, with every Stokes "
            "plane and channel (a FITS file is copied as it is). With the **K** or **Solar XY** view "
            "on, the converted image is saved.",
            "{File → Export image as → CASA image…}: a CASA image (a folder ending in .image). "
            "This needs CASA.",
            "{File → Export image as → Helioprojective FITS…|Ctrl+H}: a FITS file in Solar X and "
            "Solar Y, solar north up, of the Stokes product shown. The image must say when it was "
            "observed (DATE-OBS) and where the telescope was, or name one CASA knows.",
            "{File → Export image as → Brightness temperature FITS…}: the image in kelvin (the Tb), "
            "from its beam and frequency.",
        )),
        Tip("In the FITS and region exports, type the name's ending (.fits, .crtf) yourself: the name "
            "is kept as it is typed."),
        Heading("A region"),
        Bullets((
            "{Image → Export region as sub-image…|Ctrl+Shift+S}: the pixels of the selected region (a "
            "crop of the image) as a new CASA image (of an ellipse, the box round it).",
            "{Image → Export region as CASA region…|Ctrl+R}: the region as a CASA region file (.crtf), "
            "for CASA tasks such as imstat.",
            "Both are under **Export** in the **Statistics** tab too. Draw the region first "
            "([[regions]]).",
        )),
    )),
    Section("tools-menu", "Save and convert", "The Tools menu", (
        Para("The **Tools** menu makes new image files from others. **Solar phase center shift** "
             "works on one image or a folder of them; **Transform coordinates**, **Convert units** "
             "and **Convert image format** work on a folder of images (for one image, use "
             "{File → Export image as}: see [[exporting]]). Each of these opens a window with the "
             "settings at the left and, at the right, **You'll get**: how many images, where they go "
             "and the first names. The main button says what will happen (**Shift 59 images**, say); "
             "while it is greyed, **You'll get** says what is missing."),
        Bullets((
            Item("{Tools → Solar phase center shift…|Ctrl+P} moves an image's coordinates so that the "
                 "Sun is where it really was (the ionosphere displaces low-frequency images). "
                 "**Find the Sun by**: **Gaussian fit** or **Centre of mass**. **Move the Sun to the "
                 "image centre** is on at first, and moves the pixels too; turn it off to change only "
                 "the coordinates.", "Tools → Solar phase center shift…"),
            "Its **Shift** is **One image** (**Image** is the input and **File** the output, "
            "centered_<name>.fits beside the image if left empty) or **A folder**: then choose a "
            "**Reference** image as well; the shift is worked out from it and applied to every "
            "matching image.",
            "{Tools → Transform coordinates…} converts the images of a folder to helioprojective "
            "coordinates or to RA/Dec.",
            "{Tools → Convert units…} converts them to brightness temperature (K) or to flux density "
            "(Jy/beam).",
            "{Tools → Convert image format…} converts between FITS files and CASA images. Make CASA "
            "images only of RA/Dec FITS files: convert a helioprojective (Solar X/Y) FITS to RA/Dec "
            "first with {Tools → Transform coordinates…} or, for one image, use "
            "{File → Export image as → CASA image…}, which converts it itself.",
            "{Tools → Create video…}, at the end of the menu, makes a video of a series of images: "
            "see [[videos]].",
        )),
        Heading("In the windows for a folder"),
        Bullets((
            "**Folder** and **Matching** choose the images (*.fits, say); **Save to** gives the folder "
            "and the **Names** of the new files (* stands for the input's name). Images already in "
            "the chosen system, unit or format are copied.",
            "While it runs, the window shows the progress and a log, and **Cancel** becomes **Stop**. "
            "**Show folder** opens the results.",
        )),
        Tip("Files of the same name in the **Save to** folder are replaced: choose a new folder to "
            "keep earlier results."),
    )),
    Section("videos", "Save and convert", "Videos", (
        Para("{Tools → Create video…} makes a video (MP4, WebM, GIF, AVI or PNG frames) of a series "
             "of images."),
        Steps((
            "Open the first image of the series and set it up in the main window: colormap, stretch, "
            "contours, overlays.",
            Item("Open {Tools → Create video…}: the window starts from that image's folder, colormap, "
                 "stretch, Stokes, solar disk, beam, grid and plot appearance. Press **Use current "
                 "view** (in **Input**) to take its contours, its Min and Max (as a **Fixed range**) "
                 "and its zoomed area as the video's region too; **Copy from main window**, in the "
                 "**Contours** tab, takes the contours only.", "Tools → Create video…"),
            "**Input**: **Show** **Image series** (one frame for each image of the **Folder** whose "
            "name matches the **Pattern**, in the order of **Sort by**; **Scan** reads the folder "
            "again) or **Single image** (the same image in every frame, the frames following the "
            "first contour layer that is a series), and the **Stokes**.",
            "The other tabs: **Contours**, **Frames** (a time range, every n-th frame, frames left "
            "out), **Display** (colormap, stretch, range, difference movies, frame size), "
            "**Region**, **Overlays** (time stamp, disk, beam, grid, a light curve under the image) "
            "and **Output** (the file, its format, frames per second).",
            "The preview at the right shows a frame as it will be written; the slider under it goes "
            "through the frames. Drag a box on it, or use the zoom buttons above it, to set the "
            "video's region.",
            "Press **Create video**.",
        )),
        Bullets((
            "**Save settings…** keeps every setting in a file and **Load settings…** brings them "
            "back; closing the window forgets them.",
            "For a brightness that does not change from frame to frame, choose **Global auto** as the "
            "range **Mode** in **Display**.",
            "**Images on** (in **Input**) can take the images from a server, over the main window's "
            "connection ([[remote]]).",
        )),
    )),
    # ------------------------------------------------------------ Other windows
    Section("spectrum-viewer", "Other windows", "Dynamic spectrum viewer", (
        Para("A window of its own for dynamic spectra (time against frequency) in FITS files: to "
             "look at them, clean them of interference and measure bursts (to make one from "
             "Measurement Sets, see [[create-spectra]]). It has menus of its own: "
             "the menu paths on this page are the viewer's, not the main window's."),
        Bullets((
            Item("{Apps → Dynamic spectrum viewer} opens it; a spectrum opened in the main window "
                 "({File → Open dynamic spectrum…}, {File → Open image…}, {File → Open recent}, a "
                 "drop) goes to it too.", "Apps → Dynamic spectrum viewer"),
            "In the viewer, its {ds:File → Open FITS…|Ctrl+O} and {ds:File → Open from server…|Ctrl+Shift+O} "
            "open a spectrum, or drop a file on the window.",
        )),
        Heading("Move through time"),
        Bullets((
            "In the time bar, **All** shows the whole file, **Pages** a page of a set length at a time "
            "(`[` and `]` turn the pages), and **Range** the data between two times (`Ctrl+T`).",
            "The overview strip shows the whole file, the part shown outlined: click or drag on it to "
            "move; in **Pages** and **Range**, drag an edge of the outline to change the length.",
            "The viewer's {ds:View → Go to time…|Ctrl+G} puts the cursor in the **Go to time** box at "
            "the right of the time bar: type a time (UTC) and press `Enter`.",
            "`+` and `-` zoom and `R` shows everything again; the viewer's {ds:View → Frequency axis} "
            "lays the frequencies on a **Linear** or a **Logarithmic** axis (**Automatic** at first).",
        )),
        Heading("Tools"),
        Bullets((
            "**Pan**: drag to move, scroll to zoom. **Mask**: drag a box to blank it. **Keep**: drag a "
            "box the automatic flagging must leave alone.",
            "To remove a mask or a Keep box, click it with the **Mask** or **Keep** tool and press "
            "`Delete` (the viewer's {ds:Edit → Delete region}); `Esc` deselects it.",
            "**Profile**: click for the light curve at that frequency or the spectrum at that time; "
            "its window's **Save CSV…** saves the values.",
            "**Drift**: click a burst's start, then its end, for its drift rate and the speed of its "
            "source in the Newkirk model (the **Drift** block of the side panel).",
            "**Image at cursor** (it shows the time, as **Image at 10:42:05**) shows, in SolarViewer's "
            "current tab, the image of that tab's folder nearest that time (and frequency). It needs "
            "the viewer opened from SolarViewer and an image of the series open there. A click on "
            "the spectrum fixes the time; `Esc` lets it follow the cursor again.",
        )),
        Heading("Clean and save"),
        Bullets((
            "The side panel's **Display** sets the colormap, scale and limits. **Clean** has three "
            "steps: **Masks** (the boxes drawn, extended to all times if you wish), **RFI flagging** "
            "(automatic flagging: **Auto-flag page**, with its **Settings…**) and **Background** "
            "(each channel's median divided out or subtracted).",
            "The viewer's {ds:Tools → Compare original vs cleaned|Ctrl+Shift+C} shows both side by "
            "side, and its {ds:Edit → Undo|Ctrl+Z} undoes the last change to the masks or Keep boxes "
            "({ds:Edit → Redo|Ctrl+Y} does it again).",
            "The viewer's {ds:File → Save FITS…|Ctrl+S} saves the cleaned spectrum. The masks are kept "
            "for each file, and come back when it is opened again; {ds:File → Save masks…} and "
            "{ds:File → Load masks…} carry them to another file.",
        )),
    ), "other"),
    Section("create-spectra", "Other windows", "Create dynamic spectra", (
        Para("{Apps → Create dynamic spectra…} turns Measurement Sets into a dynamic spectrum FITS "
             "file, on this computer or on an SSH server. The computer that does the work needs "
             "python-casacore."),
        Steps((
            Item("**Run on**: **This computer** or **Server**. The main window's server is used by "
                 "itself when it signs in with a key or the SSH agent. Otherwise choosing **Server** "
                 "opens **Connect to remote server**; later, **Connect…** (or **Change…**) beside the "
                 "buttons signs in again.", "Apps → Create dynamic spectra…"),
            "**Data** tab: the folder of Measurement Sets (or one MS) and the FITS file to write. Use "
            "**Browse…**, or type or paste the path and leave the field with `Tab` (not `Enter`: in "
            "this window it presses **This computer**). After a moment the window reads the data "
            "and says what it found.",
            "In the **Data** tab's **Output**, **Also save a plot (PNG)** writes a picture beside the "
            "FITS file; on a server, **Download the result to this computer when done** copies them "
            "to the folder given (Downloads at first).",
            "**Spectrum** tab: the **Time bin**, the **Columns** (one per subband, or every channel) and "
            "the channels to **Average**, and a frequency or time range if you want only part of the "
            "data.",
            "**Visibilities** tab: the polarization product (Stokes I is the usual one for solar "
            "bursts) and the baselines: a UV range, or the autocorrelations only.",
            "**Processing** tab: the **CPUs** (processes working at once; **Auto** uses what the "
            "machine offers) and the **Read block** (the data each one reads at a time, in MB; each "
            "uses about three times this in memory, so lower it, or the CPUs, if memory runs short); "
            "on a server, **Run now** or **Slurm job**, with the container and the Slurm settings.",
            "Press **Create dynamic spectrum**. The log under the tabs follows the run (**Hide log** "
            "hides it); **Stop** stops it. When it is done, **Open in dynamic spectrum viewer** shows "
            "the result.",
        )),
        Tip("A job on a server goes on when the window is closed: the next time, the window offers to "
            "**Re-attach** to it."),
    ), "other"),
    Section("remote", "Other windows", "Remote server", (
        Para("SolarViewer can open images that are on another computer, over SSH, as if they were "
             "here: each image is downloaded when it is opened, and kept."),
        Steps((
            Item("{File → Remote server → Connect…|Ctrl+Shift+R}, or click **Not connected** at the "
                 "right of the status bar.", "File → Remote server → Connect…"),
            "In **Connect to remote server**, type the host, port and username, and choose how to "
            "**Sign in with**: **SSH agent**, **Key file** or **Password**. **Test connection** tries "
            "without connecting. A server under **Saved servers** connects at once with a "
            "double-click; **Import**, under the list, adds the hosts of %%~/.ssh/config%%.",
            "Press **Connect**. The first time, SolarViewer shows the server's key fingerprint and "
            "asks whether to trust it.",
            "{File → Open image…|Ctrl+O} now shows the server's files (**Open remote image**), as "
            "{File → Remote server → Open remote image…} does. **Local** switches to this computer's, "
            "and **Read header** shows a file's header before it is downloaded.",
            "`[` and `]` step through the server folder, downloading each image.",
        )),
        Bullets((
            "**Save to list** keeps the server for next time, under its **Name**. **Remember in "
            "system keyring** keeps the password, if the keyring package is installed; otherwise it "
            "is greyed.",
            "While connected, the status bar button names the server; a click on it disconnects at "
            "once, as {File → Remote server → Disconnect} does.",
            "If the connection drops, SolarViewer connects again by itself.",
            "The downloaded copies stay until {File → Manage cache…} clears them.",
            "**Create video** can take its images over the same connection (**Images on**). The "
            "dynamic spectrum viewer's **Open from server…** and **Create dynamic spectra** connect "
            "to the same server by themselves when you sign in with a key or the SSH agent; with a "
            "password, type it again in their **Connect to remote server** window.",
        )),
    )),
    Section("activity", "Other windows", "Solar activity viewer", (
        Para("What the Sun did on a day: flares, radio bursts, CMEs, active regions and indices, "
             "context images and radio spectra, from NOAA's and other archives. It needs the "
             "network; a day fetched once opens again from the disk, offline too."),
        Bullets((
            Item("{Apps → Solar activity viewer}, or the activity button in the inspector header, "
                 "opens it at the date and time of the image shown (without one, on yesterday). A day "
                 "not fetched before is fetched at once, but the **Radio spectra** tab waits for a "
                 "press of **Fetch**.", "Apps → Solar activity viewer"),
            "At the top, the arrows (`Alt+Left`, `Alt+Right`) and the date change the day, **Fetch** "
            "downloads it, and **From image** goes to the date and time of the image open in the "
            "main window.",
            "The band under them lays the whole day on one time axis: the GOES X-ray curve and lanes "
            "of events. Click it to set the cursor, drag across it to zoom, double-click it for the "
            "whole day; `[` and `]` go to the previous or next event.",
            "The cursor is shared by the tabs: the tables scroll to the nearest event, and **Context "
            "images** has **Show images at** (the images nearest the cursor's time) and **Browse "
            "around** (the [[helioviewer|Helioviewer browser]] around it).",
            "The tabs: **Events**, **Radio spectra** (the day's spectra from many observatories, on "
            "the same time axis), **Context images**, **CMEs**, and **Regions and indices**.",
            "{File → Manage cache…} in the main window clears what was fetched.",
        )),
    ), "other"),
    Section("helioviewer", "Other windows", "Helioviewer browser", (
        Para("Solar images from Helioviewer (SDO, SOHO, STEREO and more) over a time range, to see "
             "a radio image in context. It needs the network."),
        Bullets((
            Item("{Apps → Helioviewer browser} opens on the time of the image open in the main window "
                 "and loads its images at once; without an image, it opens on the last range used. "
                 "The sun button in the inspector header opens half an hour either side of the "
                 "image's time, and marks that time on the band (**Radio image**).",
                 "Apps → Helioviewer browser"),
            "Click an instrument in the list at the left (hold `Ctrl` and click a second one to lay it "
            "over the first), set **Start** and **End** (UTC), or a **Span** about the middle (48 "
            "hours at most), and press **Load**.",
            "Step through the frames, or play them, with the buttons under the image (`P` plays and "
            "pauses, `[` and `]` step); the band under it shows the frames and the flares of the "
            "range.",
            "The difference buttons show each frame minus the one before it (**Running**) or minus a "
            "base frame (**Base**, or **Base = radio time** when opened with the sun button in the "
            "inspector header), and "
            "**Reframe** loads a region you drag at full detail.",
            "**Export…** writes a movie (MP4, GIF) or PNG frames, **Save frame** the frame shown, and "
            "**Use in viewer** fetches the frame's FITS image for SolarViewer: in a new tab, beside "
            "the radio image, or as a contour layer. **Copy Helioviewer link** puts a link to the same "
            "view on helioviewer.org on the clipboard.",
        )),
        Tip("Keep the image's tab open while you use the browser it opened."),
    ), "other"),
    Section("downloads", "Other windows", "Downloading data", (
        Heading("Solar images"),
        Steps((
            Item("{Download → Solar data…} opens **Download solar data**. Opened with an image "
                 "showing, it is set to that image's time.", "Download → Solar data…"),
            "Choose the instrument and its channel, the **Time range** (UTC) and the **Cadence**: "
            "every image, or one a minute, every 5 or 15 minutes, or every hour.",
            "**Source**: **VSO** needs no account. **JSOC export** (the only source of some series, "
            "such as HMI's 720 s and MDI's 96 min ones) needs an e-mail address registered at JSOC, "
            "and **Download** stays greyed until one is typed.",
            "**Save to** is the base folder (**Browse…** changes it), %%~/solar_data%% at first: each "
            "download goes into a folder of its date and, in it, of its instrument and channel, with "
            "its log (download.log). **Prepare for SolarViewer** brings the files to a level ready to "
            "view (AIA level 1.5, say).",
            "**You'll get** says how many files to expect and their size, and the **Download** button "
            "how many it will fetch.",
            "When it is done, the first file opens by itself in the current tab, in place of the image "
            "there, while **Opens in SolarViewer when done** (at the bottom left, on at first) is on: "
            "turn it off, or open a new tab (`Ctrl+N`) first, to keep your image. **Open in "
            "SolarViewer** opens the file again, and `[` `]` step through the others.",
        )),
        Heading("Radio spectra"),
        Steps((
            Item("{Download → Radio spectra…} opens **Download radio spectra**.", "Download → Radio spectra…"),
            "Choose the source, on the ground (RSTN, e-CALLISTO, OVRO-LWA) or in space (Wind/WAVES, "
            "STEREO/WAVES, Parker Solar Probe/FIELDS, Solar Orbiter/RPW), and in the list beside it a "
            "station, product, receiver or spacecraft.",
            "Choose **Whole day** or **Time range**, and the folder. A click on a burst in the list "
            "of the day's bursts sets a time range round it.",
            "Press **Download**: the spectra are written as FITS files. With **Opens in the Dynamic "
            "Spectrum Viewer when done** on (as it is at first), the first one opens in the "
            "[[spectrum-viewer|Dynamic spectrum viewer]]; **Open spectrum** opens it again, and "
            "**Show folder** shows the folder with all of them.",
        )),
        Tip("Closing a download window with its title bar's × stops the download; **Run in "
            "background** (or `Esc`) hides it and lets the download go on. Closing SolarViewer's "
            "main window closes the download windows too, and stops their downloads."),
    ), "other"),
    Section("calibration-logs", "Other windows", "Calibration tables and logs", (
        Heading("Calibration table visualizer"),
        Bullets((
            Item("{Apps → Calibration table visualizer} opens a window with three tabs, each with its "
                 "own **Load…**: **Bandpass** and **Selfcal** take a CASA calibration table (a folder), "
                 "**Crossphase** a SIMPL crossphase file (.kcross or .npy). It needs python-casacore.",
                 "Apps → Calibration table visualizer"),
            "**Bandpass** shows the solutions against frequency, a page of plots at a time (one plot "
            "per antenna for a table with one solution time and one spectral window). `[` and `]` "
            "turn the pages, `{` and `}` go to the first and the last; **View** has other ways of "
            "showing them, and **Plots per page** sets 2x2 to 5x5.",
            "**Show phase** or **Show amplitude** (or `T`) switches the Bandpass and Selfcal plots; "
            "the toolbar under the plots zooms, pans and saves them.",
            "`1`, `2` and `3` switch the tabs, `Ctrl+O` loads a table for the tab shown, and `F1` "
            "lists the window's keys.",
        )),
        Heading("Log viewer"),
        Bullets((
            Item("{Apps → Log viewer} shows a pipeline's log as a table (time, level, source, "
                 "message), and follows it while the pipeline writes.", "Apps → Log viewer"),
            "It reads lines such as %%2024-05-10 12:00:00 - INFO - name - message%% (the SIMPL "
            "pipeline's). A log of another form, SolarViewer's own solarviewer.log among them, shows "
            "**Open a log file to see its lines**: read it in a text editor.",
            "**Open…** opens a log; **Minimum level** hides the lines below a level (INFO at first, so "
            "DEBUG lines are hidden); **Search in logs…** keeps the lines whose message or source "
            "contains the text (**Regular expression** for a pattern); **Previous error** and **Next "
            "error** jump between the errors.",
            "**Clear filters** (`Esc`) shows every line again; **Only the last** (`Ctrl+T`) keeps the "
            "newest lines; **Export…** (`Ctrl+E`) saves the log as CSV.",
            "**Auto-refresh** adds the new lines as the file grows, and **Auto-scroll** keeps the "
            "newest in view (both at the bottom right). **Help** (`F1`) lists the rest.",
        )),
    ), "other"),
    # ------------------------------------------------------------ Reference
    Section("keyboard", "Reference", "Keyboard and mouse", (
        Para("The keys used most. `F1` in the main window, or the button below, lists every one, read "
             "from the menus themselves. The keys work in the main window, once its image has been "
             "clicked."),
        Button("All keyboard shortcuts (F1)", "Help → Keyboard shortcuts"),
        Heading("On the image"),
        Keys(_IMAGE_KEYS, "image"),
        Heading("In the menus"),
        Keys(_MENU_KEYS, "menu"),
        Heading("With the mouse"),
        Keys(_MOUSE, "mouse"),
        Tip("With the **Ruler** or **Profile** tool, scrolling and dragging do not move the view: use "
            "the keys (`+`, `-`, the arrow keys, `R`), or press `H` for **Pan**."),
    )),
    Section("troubleshooting", "Reference", "Troubleshooting", (
        Bullets((
            "**SolarViewer waits as it starts.** CASA looks for newer data once a day as it starts, "
            "and the start waits for it: the splash screen says so, and the status bar says afterwards "
            "how long it took. To turn the look off, put %%data_auto_update = False%% and "
            "%%measures_auto_update = False%% in %%~/.casa/config.py%% (make the file if there is "
            "none); CASA's data then stay as they are until %%python -m casaconfig --update-all%% "
            "is run.",
            "**A window says a package is missing.** Some parts need packages that are not always "
            "installed: python-casacore for the [[calibration-logs|Calibration table visualizer]], "
            "and for reading "
            "Measurement Sets on this computer in **Create dynamic spectra** (a server can read them "
            "instead); paramiko for servers. The message says what to install. Without the keyring "
            "package, **Remember in system keyring** is greyed (its tooltip says so).",
            "**A file is not in the Open window.** Compressed files (.fits.gz, .fit.gz) show only when "
            "you choose **All files (*)** as the type of file.",
            "**The keys do nothing.** A text field has the keyboard, or another window is in front: "
            "click the image in the main window, or press `Esc`.",
            "**What happened?** {View → Log console|Ctrl+L} shows SolarViewer's messages (**Copy "
            "all** copies them); they are also written to %%~/.solarviewer/logs/solarviewer.log%%. "
            "After an unexpected error a crash report is saved in %%~/.solarviewer/crash_reports%%.",
            "Four of the **Apps** windows (Dynamic spectrum viewer, Create dynamic spectra, Calibration table "
            "visualizer, Log viewer) are programs of their own: their messages are not in the Log "
            "console. Start SolarViewer from a terminal to see them there.",
            Item("**The disk fills up.** {File → Manage cache…} (it shows the total, as **Manage cache "
                 "(32.2 MB)…**) lists what SolarViewer keeps: server files, spectrum slices, the masks "
                 "drawn on dynamic spectra (clearing them forgets the masks), solar activity data, "
                 "Helioviewer frames and SunPy's data. It clears what you tick. Downloads are not in "
                 "it: delete them from their folder (%%~/solar_data%% at first).",
                 "File → Manage cache…"),
            "**Is there a newer version?** {Help → Check for updates…} says so, and can update "
            "SolarViewer. When one is found at start-up, **Update available** appears in the status "
            "bar.",
            Item("**The window is too large or too small, or its text is blurred.** "
                 "{File → Preferences…|Ctrl+,} sets the **UI scale**, on Linux the **Window system** "
                 "(**Automatic**, **X11 (XWayland)** or **Wayland**: Wayland gives sharper text on a "
                 "scaled screen, but its windows cannot bring themselves to the front), and whether to "
                 "check for updates at start-up; the changes apply after a restart.",
                 "File → Preferences…"),
            "**Reporting a problem.** {Help → About SolarViewer} gives the developer's e-mail address. "
            "Say what you did and what happened, and attach the log file and any crash report.",
        )),
    )),
)

# ---------------------------------------------------------------------------
# The markup
# ---------------------------------------------------------------------------

_MARKUP = re.compile(
    r"\*\*(?P<bold>.+?)\*\*"
    r"|`(?P<key>[^`]+)`"
    r"|%%(?P<code>.+?)%%"
    r"|\{(?:(?P<scope>[a-z]+):)?(?P<menu>[^{}|]+?)(?:\|(?P<keys>[^{}]+))?\}"
    r"|\[\[(?P<link>[a-z0-9-]+)(?:\|(?P<label>[^\]]+))?\]\]"
)
ARROW = "→"


def section_by_id(section_id):
    return next((s for s in SECTIONS if s.id == section_id), None)


def menu_refs(text):
    """The menu paths in ``text``: [(scope or None, "File → Open image…", (keys, ...))]."""
    refs = []
    for match in _MARKUP.finditer(text):
        if match.group("menu"):
            keys = tuple(k.strip() for k in (match.group("keys") or "").split("|") if k.strip())
            refs.append((match.group("scope"), match.group("menu").strip(), keys))
    return refs


def key_tokens(text):
    """The keys written as `key` in ``text``."""
    return [match.group("key") for match in _MARKUP.finditer(text) if match.group("key")]


def links(text):
    """The section ids ``text`` links to."""
    return [match.group("link") for match in _MARKUP.finditer(text) if match.group("link")]


def _link_label(match):
    if match.group("label"):
        return match.group("label")
    target = section_by_id(match.group("link"))
    return target.title if target is not None else match.group("link")


def plain(text):
    """``text`` without its markup, as read."""

    def word(match):
        if match.group("menu"):
            keys = match.group("keys")
            return match.group("menu") + (f" ({', '.join(keys.split('|'))})" if keys else "")
        if match.group("link"):
            return _link_label(match)
        return next(g for g in (match.group("bold"), match.group("key"), match.group("code")) if g)

    return _MARKUP.sub(word, text)


def key_html(key, palette):
    """``key`` drawn as a key cap, in rich text (a QLabel's rich text has no
    borders, so the cap is a filled box)."""
    text = html.escape(key).replace(" ", "&nbsp;")
    return (
        f'<span style="font-weight:600; background-color:{palette["border_light"]}; '
        f'color:{palette["text"]};">&nbsp;{text}&nbsp;</span>'
    )


def keys_html(keys, palette):
    """One key or a tuple of alternatives, as caps with "or" between them."""
    if isinstance(keys, str):
        keys = (keys,)
    return " or ".join(key_html(k, palette) for k in keys)


_MODIFIER_GESTURE = re.compile(r"^(Shift|Ctrl|Alt)-(.+)$")


def gesture_html(gestures, palette):
    """Mouse gestures ("Scroll", "Shift-drag"), or a tuple of alternatives,
    as bold words, a modifier before them drawn as a key."""
    if isinstance(gestures, str):
        gestures = (gestures,)
    shown = []
    for gesture in gestures:
        modifier = _MODIFIER_GESTURE.match(gesture)
        if modifier:
            shown.append(key_html(modifier.group(1), palette) + f"<b>{unbroken('-' + modifier.group(2))}</b>")
        else:
            shown.append(f"<b>{unbroken(html.escape(gesture, quote=False))}</b>")
    return " or ".join(shown)


def unbroken(text):
    """A name the window shows, kept whole at a hyphen, slash or dash (a
    line can still break at its spaces): "1–99%", "Auto-flag", "min/max"."""
    return text.replace("-", "‑").replace("/", "/⁠").replace("–", "⁠–⁠")


def key_parts(key):
    """The keys pressed together for ``key``: "Ctrl+Shift+Z" -> Ctrl, Shift,
    Z; "+" and "Ctrl++" end with the + key itself."""
    if len(key) == 1:
        return [key]
    if key.endswith("++"):
        return key[:-2].split("+") + ["+"]
    return [part for part in key.split("+") if part]


def _plain(text):
    """Plain ``text`` as rich text: no line break after a slash ("Jy/beam",
    "RA/Dec")."""
    return html.escape(text, quote=False).replace("/", "/\u2060")


def to_html(text, palette):
    """``text`` with its markup as rich text in ``palette``'s colours."""
    parts = []
    last = 0
    for match in _MARKUP.finditer(text):
        parts.append(_plain(text[last:match.start()]))
        last = match.end()
        if match.group("bold"):
            parts.append(f"<b>{unbroken(html.escape(match.group('bold'), quote=False))}</b>")
        elif match.group("key"):
            parts.append(key_html(match.group("key"), palette))
        elif match.group("code"):
            parts.append(
                f'<span style="font-family:monospace; color:{palette["text_strong"]};">'
                f"{html.escape(match.group('code'), quote=False).replace(' ', '&nbsp;').replace('/', '/&#8288;')}"
                "</span>"
            )
        elif match.group("menu"):
            names = [
                unbroken(html.escape(n.strip(), quote=False)).replace(" ", "&nbsp;")
                for n in match.group("menu").split(ARROW)
            ]
            parts.append("<b>" + f"&nbsp;{ARROW} ".join(names) + "</b>")
            if match.group("keys"):
                caps = keys_html(tuple(k.strip() for k in match.group("keys").split("|")), palette)
                parts.append(f" ({caps})")
        else:
            parts.append(
                f'<a href="section:{match.group("link")}">'
                f'<span style="color:{palette["link"]}; text-decoration:underline;">'
                f"{html.escape(_link_label(match))}</span></a>"
            )
    parts.append(_plain(text[last:]))
    return "".join(parts)


def _item_text(item):
    return item.text if isinstance(item, Item) else item


def _item_target(item):
    return item.try_it if isinstance(item, Item) else None


def block_texts(block):
    """Every piece of marked-up text in ``block``."""
    if isinstance(block, (Para, Heading, Tip)):
        return [block.text]
    if isinstance(block, (Steps, Bullets)):
        return [_item_text(item) for item in block.items]
    if isinstance(block, Keys):
        return [text for _keys, text in block.rows]
    if isinstance(block, Figure):
        return [f"{title}: {text}" for title, text in block.legend]
    if isinstance(block, Button):
        return [block.text]
    return []


def block_keys(block):
    """The keys a Keys block names, as (keys tuple, text) rows."""
    return [((keys,) if isinstance(keys, str) else tuple(keys), text) for keys, text in block.rows]


def try_targets(section):
    """What the section's Try it links and buttons do: menu paths or
    ("button", tooltip) pairs."""
    targets = []
    for block in section.blocks:
        if isinstance(block, (Steps, Bullets)):
            targets += [_item_target(item) for item in block.items if _item_target(item)]
        elif isinstance(block, Button):
            targets.append(block.try_it)
    return targets


def section_text(section):
    """All the words of ``section``, plain, for the search."""
    words = [section.title, section.group]
    for block in section.blocks:
        words += [plain(text) for text in block_texts(block)]
        if isinstance(block, Keys):
            words += [" ".join(keys) for keys, _ in block_keys(block)]
    return "\n".join(words)


# ---------------------------------------------------------------------------
# Finding the main window's commands
# ---------------------------------------------------------------------------

_CACHE_SIZE = re.compile(r" \([\d.,]+ ?[KMGT]?i?B\)(?=…$)")
_ALIASES = {"Switch to dark mode": "Switch to light mode"}


def action_name(action):
    """``action``'s text as the tutorial writes it: no mnemonic, no
    changing parts (the cache's size; the theme's name; a connection)."""
    text = _CACHE_SIZE.sub("", action.text().replace("&", ""))
    if text.startswith("Connected: "):
        return "Connect…"
    return _ALIASES.get(text, text)


def find_action(window, path):
    """The QAction at menu ``path`` ("Image → Fit 2D Gaussian") of
    ``window``'s menu bar, hidden or not, or None."""
    if window is None or not hasattr(window, "menuBar"):
        return None
    names = [name.strip() for name in path.split(ARROW)]
    actions = window.menuBar().actions()
    for depth, name in enumerate(names):
        found = next((a for a in actions if not a.isSeparator() and action_name(a) == name), None)
        if found is None:
            return None
        if depth == len(names) - 1:
            return found
        if found.menu() is None:
            return None
        actions = found.menu().actions()
    return None


def find_button(window, tooltip):
    """The button of ``window``'s current image tab whose tooltip is
    ``tooltip`` (the gears of the inspector), or None."""
    tabs = getattr(window, "tab_widget", None)
    tab = tabs.currentWidget() if tabs is not None else None
    if tab is None:
        return None
    # The tab's own controls, wherever they are shown (the inspector of a
    # page of several images is the page's, not the tab's)
    owned = [value for value in vars(tab).values() if isinstance(value, QAbstractButton)]
    return next((b for b in owned + tab.findChildren(QAbstractButton) if b.toolTip() == tooltip), None)


def find_target(window, target):
    """The QAction or button ``target`` (a menu path or ("button", tooltip)) names, or None."""
    if isinstance(target, tuple) and target and target[0] == "button":
        return find_button(window, target[1])
    return find_action(window, target)


def _target_words(target):
    if isinstance(target, tuple):
        return f"the {target[1].lower()} button of the inspector"
    return target


# ---------------------------------------------------------------------------
# The main window, drawn
# ---------------------------------------------------------------------------

_VIRIDIS = ("#440154", "#3b528b", "#21918c", "#5ec962", "#fde725")


class MainWindowFigure(QWidget):
    """A schematic of the main window in the theme's colours, its parts
    numbered as ``_MAIN_WINDOW_LEGEND``. Drawn on a 1000 × 610 plan scaled
    to the width. The numbers keep their size, and so do the words that
    name the parts (the menus, Stokes, the inspector's tabs, CURSOR...),
    so that the figure still reads when it is small; the other words are
    left out when they would be too small to read."""

    PLAN = QSize(1000, 610)
    MARKER = 11  # radius of a number's disc, in pixels
    SMALLEST = 7.5  # pixels: smaller words are left out...
    KEPT = 8.5  # ...but the names of the parts are never smaller than this

    def __init__(self, parent=None):
        super().__init__(parent)
        sizing = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        sizing.setHeightForWidth(True)
        self.setSizePolicy(sizing)
        self.setMinimumWidth(320)
        self.setAccessibleName("The main window, its parts numbered")
        self._marks = []  # where the numbers go, on the plan, as drawn last

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return round(width * self.PLAN.height() / self.PLAN.width())

    def sizeHint(self):
        return QSize(720, self.heightForWidth(720))

    def minimumSizeHint(self):
        return QSize(320, self.heightForWidth(320))

    def _scale(self):
        return min(self.width() / self.PLAN.width(), self.height() / self.PLAN.height())

    def paintEvent(self, event):
        p = theme_manager.palette
        scale = self._scale()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.save()
        painter.scale(scale, scale)
        self._draw_window(painter, p, scale)
        painter.restore()
        self._draw_markers(painter, p, scale)
        painter.end()

    # -- the parts, on the plan
    def _box(self, painter, rect, fill, line=None, radius=0):
        painter.setPen(QPen(QColor(line), 1.2) if line else Qt.NoPen)
        painter.setBrush(QColor(fill) if fill else Qt.NoBrush)
        if radius:
            painter.drawRoundedRect(QRectF(*rect), radius, radius)
        else:
            painter.drawRect(QRectF(*rect))

    def _line(self, painter, color, x1, y1, x2, y2, width=1.2):
        painter.setPen(QPen(QColor(color), width))
        painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    def _arrow(self, painter, color, x, y):
        """A list's ▾, its middle at (x, y)."""
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(color))
        painter.drawPolygon(QPolygonF([QPointF(x - 4, y - 2), QPointF(x + 4, y - 2), QPointF(x, y + 3)]))

    def _font(self, size, bold, scale, kept):
        """The font of a word ``size`` px high on the plan (a kept one never
        smaller than KEPT on screen), and that size."""
        if kept:
            size = max(size, self.KEPT / scale)
        font = QFont(self.font())
        font.setPixelSize(max(1, round(size)))
        font.setBold(bold)
        return font, size

    def _width(self, painter, scale, text, size=13, bold=False, kept=False):
        """How wide ``text`` is on the plan, drawn or not."""
        font, _size = self._font(size, bold, scale, kept)
        painter.setFont(font)
        return painter.fontMetrics().horizontalAdvance(text)

    def _text(self, painter, scale, x, y, text, color, size=13, bold=False, align=Qt.AlignLeft, kept=False):
        """``text`` at (x, y) on the plan, its middle at y; left out when it
        would be too small to read, unless ``kept``. Returns its width on
        the plan."""
        font, size = self._font(size, bold, scale, kept)
        painter.setFont(font)
        width = painter.fontMetrics().horizontalAdvance(text)
        if size * scale < self.SMALLEST:
            return width
        painter.setPen(QColor(color))
        left = x - width / 2 if align == Qt.AlignHCenter else x - width if align == Qt.AlignRight else x
        painter.drawText(QRectF(left, y - size, width + 2, size * 2), Qt.AlignVCenter | Qt.AlignLeft, text)
        return width

    def _draw_window(self, painter, p, scale):
        line, dim, text = p["border_light"], p["text_secondary"], p["text"]
        accent, soft, off = p["highlight"], p["highlight_soft"], p["disabled"]
        r = self.MARKER / scale  # a number's radius, on the plan
        gap = 6 / scale
        marks = {}
        # The window
        self._box(painter, (0, 0, 1000, 610), p["window"], line, 6)
        # Menu bar, with the tabs at its right end
        self._box(painter, (1, 1, 998, 33), p["surface"])
        x = 16
        for name in ("File", "View", "Image", "Tools", "Download", "Apps", "Help"):
            x += self._text(painter, scale, x, 18, name, text, 13, kept=True) + 22
        marks[1] = (x - 22 + gap + r, 17)
        self._box(painter, (762, 6, 190, 23), p["window"], line, 5)
        self._text(painter, scale, 774, 18, "image.fits", text, 12)
        self._text(painter, scale, 936, 18, "×", dim, 13)
        self._text(painter, scale, 966, 18, "+", text, 15, bold=True, kept=True)
        marks[2] = (762 - gap - r, 17)
        # Top bar: path, Stokes, Threshold, Within, colormap, stretch
        top, height, middle = 43, 27, 57
        self._line(painter, line, 0, 78, 1000, 78)
        self._box(painter, (12, top, 238, height), p["base"], line, 5)
        self._text(painter, scale, 24, middle, "…/FREQ_110MHz/image.fits", dim, 12)
        marks[3] = (250 - gap - r, middle)
        x = 262
        x += self._text(painter, scale, x, middle, "Stokes", dim, 12, kept=True) + 8
        self._box(painter, (x, top, 108, height), None, line, 5)
        self._box(painter, (x + 2, top + 2, 25, height - 4), soft, accent, 4)
        for i, name in enumerate("IQUV"):
            self._text(painter, scale, x + 14.5 + 27 * i, middle, name, text if i == 0 else dim, 12, align=Qt.AlignHCenter)
        x += 108 + 8
        self._box(painter, (x, top, 58, height), p["base"], line, 5)
        self._text(painter, scale, x + 9, middle, "More", text, 12)
        self._arrow(painter, dim, x + 48, middle)
        x += 58 + 12
        for name, value in (("Threshold", "10"), ("Within", "55")):
            x += self._text(painter, scale, x, middle, name, off, 12) + 6
            self._box(painter, (x, top, 36, height), None, off, 5)
            self._text(painter, scale, x + 8, middle, value, off, 12)
            x += 36 + 10
        x += 2
        rest = 988 - x - 30
        viridis = round(rest * 0.54)
        self._box(painter, (x, top, viridis, height), p["base"], line, 5)
        self._text(painter, scale, x + 10, middle, "viridis", text, 12, kept=True)
        self._arrow(painter, dim, x + viridis - 12, middle)
        x += viridis + 15
        # The magnifier: Search all colormaps
        painter.setPen(QPen(QColor(dim), 1.6))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(x - 2, middle - 2), 5, 5)
        self._line(painter, dim, x + 2, middle + 2, x + 6, middle + 6, 1.8)
        x += 15
        self._box(painter, (x, top, 988 - x, height), p["base"], line, 5)
        self._text(painter, scale, x + 10, middle, "power", text, 12, kept=True)
        self._arrow(painter, dim, 976, middle)
        # The image and its colorbar
        self._box(painter, (0, 79, 700, 453), p["plot_bg"])
        plot = QRectF(150, 106, 380, 380)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(_VIRIDIS[0]))
        painter.drawRect(plot)
        glow = QRadialGradient(QPointF(340, 296), 120)
        for stop, color in zip((0.0, 0.25, 0.55, 0.8, 1.0), reversed(_VIRIDIS)):
            glow.setColorAt(stop, QColor(color))
        painter.setBrush(QBrush(glow))
        painter.drawEllipse(QPointF(340, 296), 120, 120)
        painter.setPen(QPen(QColor(p["plot_text"]), 1.2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(plot)
        bar = QLinearGradient(0, 486, 0, 106)
        for i, color in enumerate(_VIRIDIS):
            bar.setColorAt(i / (len(_VIRIDIS) - 1), QColor(color))
        painter.setBrush(QBrush(bar))
        painter.drawRect(QRectF(556, 106, 18, 380))
        self._text(painter, scale, 340, 508, "Right Ascension", p["plot_text"], 12, align=Qt.AlignHCenter)
        marks[4] = (120, 120)
        # Bar under the image: tools, files, zoom, layouts
        self._line(painter, line, 0, 532, 700, 532)
        self._box(painter, (12, 541, 196, 28), None, line, 6)
        for i in range(6):
            self._box(painter, (17 + 32 * i, 545, 24, 20), soft if i == 0 else None, accent if i == 0 else line, 4)
        marks[5] = (208 + gap + r, 555)
        self._text(painter, scale, 316, 555, "‹   1/59   ›", text, 12, align=Qt.AlignHCenter)
        self._box(painter, (430, 541, 140, 28), None, line, 6)
        self._text(painter, scale, 500, 555, "+   100%   −", text, 12, align=Qt.AlignHCenter)
        self._box(painter, (584, 541, 104, 28), None, line, 6)
        for i in range(4):
            self._box(painter, (589 + 25 * i, 546, 20, 18), soft if i == 0 else None, accent if i == 0 else line, 3)
        # Inspector header: the two switches, sized to their words as the
        # window's are, and the two buttons
        self._line(painter, line, 700, 79, 700, 578)
        self._box(painter, (701, 79, 299, 40), p["surface"])
        self._line(painter, line, 700, 119, 1000, 119)
        x, pad = 710, 6
        for names in (("Jy/beam", "K"), ("RA/Dec", "Solar XY")):
            first, second = (self._width(painter, scale, name, 11) + 2 * pad for name in names)
            self._box(painter, (x, 87, first + second + 4, 24), p["window"], None, 5)
            self._box(painter, (x + 2, 89, first, 20), None, accent, 4)
            self._text(painter, scale, x + 2 + pad, 99, names[0], text, 11)
            self._text(painter, scale, x + 2 + first + pad, 99, names[1], dim, 11)
            x += first + second + 4 + 8
        marks[6] = ((x - 8 + 942) / 2, 99)
        for left in (942, 970):
            self._box(painter, (left, 87, 24, 24), None, line, 5)
        painter.setPen(QPen(QColor(dim), 1.4))
        painter.setBrush(Qt.NoBrush)
        painter.drawPolyline(QPolygonF([QPointF(947, 101), QPointF(951, 101), QPointF(954, 93), QPointF(957, 105), QPointF(960, 99), QPointF(962, 99)]))
        painter.drawEllipse(QPointF(982, 99), 4, 4)
        # Inspector tabs
        width = self._text(painter, scale, 714, 136, "Display", text, 12, bold=True, kept=True)
        self._line(painter, accent, 712, 150, 716 + width, 150, 2)
        self._text(painter, scale, 714 + width + 22, 136, "Statistics", dim, 12, kept=True)
        self._line(painter, line, 700, 151, 1000, 151)
        marks[7] = (1000 - gap - r, 136)
        # The Display tab: overlays, intensity range, loading, the image's summary
        self._text(painter, scale, 714, 170, "OVERLAYS", accent, 10, bold=True)
        for y in (182, 210):
            for column in range(2):
                left = 712 + 142 * column
                self._box(painter, (left, y, 134, 24), p["surface"], None, 5)
                self._box(painter, (left + 8, y + 8, 16, 8), off, None, 4)
        self._text(painter, scale, 714, 252, "INTENSITY RANGE", accent, 10, bold=True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(soft))
        for i in range(22):
            height = 34 - abs(i - 6) * 1.6 if i < 14 else 12
            painter.drawRect(QRectF(714 + 12 * i, 300 - max(height, 6), 9, max(height, 6)))
        self._box(painter, (712, 306, 134, 20), p["base"], line, 4)
        self._box(painter, (854, 306, 134, 20), p["base"], line, 4)
        self._box(painter, (712, 334, 276, 20), None, line, 5)
        self._box(painter, (714, 336, 88, 16), None, accent, 4)
        self._text(painter, scale, 758, 344, "Auto", text, 11, align=Qt.AlignHCenter)
        self._text(painter, scale, 850, 344, "1–99%", dim, 11, align=Qt.AlignHCenter)
        self._text(painter, scale, 942, 344, "0.1–99.9%", dim, 11, align=Qt.AlignHCenter)
        self._text(painter, scale, 714, 374, "LOADING", accent, 10, bold=True)
        for column in range(2):
            self._box(painter, (712 + 142 * column, 386, 134, 22), p["surface"], None, 5)
        self._text(painter, scale, 714, 424, "IMAGE", accent, 10, bold=True)
        for i, (name, value) in enumerate((("Max", "1643"), ("RMS", "7.32"), ("Pos. DR", "224"))):
            self._text(painter, scale, 714 + 92 * i, 440, name, dim, 10)
            self._text(painter, scale, 714 + 92 * i, 456, value, text, 11)
        # The cursor block
        self._box(painter, (701, 472, 299, 106), p["surface"])
        self._line(painter, line, 700, 472, 1000, 472)
        self._text(painter, scale, 714, 490, "CURSOR", accent, 10, bold=True, kept=True)
        for i, name in enumerate(("Pixel", "RA/Dec", "Value")):
            self._text(painter, scale, 714, 512 + 19 * i, name, dim, 11)
            self._text(painter, scale, 780, 512 + 19 * i, "—", text, 11)
        marks[8] = (1000 - gap - r, 490)
        # Status bar: a message, and the connection at the right
        self._line(painter, line, 0, 578, 1000, 578)
        self._box(painter, (1, 579, 998, 30), p["surface"])
        self._text(painter, scale, 14, 594, "Loaded image.fits in 0.8 s", dim, 11)
        width = self._text(painter, scale, 986, 594, "Not connected", dim, 11, align=Qt.AlignRight, kept=True)
        dot = max(3.5, 2.5 / scale)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(p["text_faint"]))
        painter.drawEllipse(QPointF(986 - width - 6 - dot, 594), dot, dot)
        marks[9] = (330, 594)
        self._marks = [marks[number] for number in sorted(marks)]

    def _draw_markers(self, painter, p, scale):
        font = QFont(self.font())
        font.setPixelSize(12)
        font.setBold(True)
        painter.setFont(font)
        r = self.MARKER
        for number, (x, y) in enumerate(self._marks, start=1):
            centre = QPointF(x * scale, y * scale)
            # Kept inside the figure, whatever its size
            centre.setX(min(max(centre.x(), r + 1), self.width() - r - 1))
            centre.setY(min(max(centre.y(), r + 1), self.height() - r - 1))
            painter.setPen(QPen(QColor(p["window"]), 2))
            painter.setBrush(QColor(p["highlight"]))
            painter.drawEllipse(centre, r, r)
            painter.setPen(QColor(p["highlight_text"]))
            painter.drawText(QRectF(centre.x() - r, centre.y() - r, 2 * r, 2 * r), Qt.AlignCenter, str(number))


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

DEFAULT_SIZE = QSize(1080, 760)
MINIMUM_SIZE = QSize(720, 480)
PAGE_WIDTH = 760  # the text's widest, for lines that read easily
_SECTION_ROLE = Qt.UserRole
_GROUP_ROLE = Qt.UserRole + 1


class TutorialDialog(QDialog):
    """The tutorial, beside the main window (``parent``), whose commands
    its Try it links run. Opens on ``section`` (an id), else on the one
    read last."""

    sectionShown = pyqtSignal(str)

    def __init__(self, parent=None, section=None):
        super().__init__(parent)
        self.setWindowTitle("SolarViewer tutorial")
        self.setWindowFlag(Qt.WindowMinMaxButtonsHint, True)
        self.setModal(False)
        self._main = parent
        self._pages = {}
        self._try_links = []  # (button, target) on the pages built
        self._index = 0
        self._haystack = {s.id: section_text(s).lower() for s in SECTIONS}

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._sidebar())
        outer.addWidget(self._content(), 1)

        self.setMinimumSize(MINIMUM_SIZE)
        fit_to_screen(self, DEFAULT_SIZE.width(), DEFAULT_SIZE.height())
        theme_manager.register_callback(self._on_theme_changed, owner=self)
        set_hand_cursor(self)
        if not isinstance(section, str) or section_by_id(section) is None:
            section = self._remembered()
        self.show_section(section or SECTIONS[0].id)

    # -- building
    def _sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(SPACE_M, SPACE_L, SPACE_M, SPACE_M)
        layout.setSpacing(SPACE_S)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search the tutorial")
        self.search.setClearButtonEnabled(True)
        self.search.setToolTip("Keeps the topics that mention every word typed; Enter opens the best match")
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._open_best_match)
        layout.addWidget(self.search)

        self.nav = QListWidget()
        self.nav.setObjectName("NavList")
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nav.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        for group in GROUPS:
            heading = QListWidgetItem(group.upper())
            heading.setData(_GROUP_ROLE, group)
            heading.setFlags(Qt.NoItemFlags)
            self.nav.addItem(heading)
            for section in SECTIONS:
                if section.group == group:
                    item = QListWidgetItem(section.title)
                    item.setData(_SECTION_ROLE, section.id)
                    item.setToolTip(section.title)
                    self.nav.addItem(item)
        self._style_headings()
        # Wide enough for the longest title, with the items' padding and
        # room for the scroll bar
        self.nav.ensurePolished()
        widest = max(self.nav.fontMetrics().horizontalAdvance(s.title) for s in SECTIONS)
        sidebar.setFixedWidth(widest + 2 * SPACE_M + 3 * SPACE_L + SPACE_S)
        self.nav.currentItemChanged.connect(self._on_nav_changed)
        layout.addWidget(self.nav, 1)

        self.no_match = empty_note("No topic has all these words.")
        self.no_match.hide()
        layout.addWidget(self.no_match)
        layout.addStretch(0)  # (under the note, while the list is hidden)
        return sidebar

    def _style_headings(self):
        """The group headings as the inspector's kickers: small capitals in the accent."""
        palette = theme_manager.palette
        font = QFont(self.nav.font())
        font.setPointSize(FONT_META)
        font.setBold(True)
        font.setLetterSpacing(QFont.PercentageSpacing, 110)
        line = self.nav.fontMetrics().height()
        for row in range(self.nav.count()):
            item = self.nav.item(row)
            if item.data(_GROUP_ROLE):
                item.setFont(font)
                item.setForeground(QColor(palette["highlight"]))
                # Room above, to set the group off from the one before
                item.setSizeHint(QSize(0, line + (2 * SPACE_S if row else SPACE_S)))
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignBottom)

    def _content(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        layout.addWidget(rule())

        footer = QHBoxLayout()
        footer.setContentsMargins(SPACE_L, SPACE_M, SPACE_L, SPACE_M)
        footer.setSpacing(SPACE_S)
        self.prev_button = QPushButton("‹ Previous")
        self.prev_button.setAutoDefault(False)
        self.prev_button.clicked.connect(lambda: self.step(-1))
        self.next_button = QPushButton("Next ›")
        self.next_button.setObjectName("PrimaryButton")
        self.next_button.setAutoDefault(False)
        self.next_button.clicked.connect(lambda: self.step(1))
        self.position = QLabel()
        self.position.setObjectName("Meta")
        close = QPushButton("Close")
        close.setAutoDefault(False)
        close.clicked.connect(self.close)
        footer.addWidget(self.prev_button)
        footer.addWidget(self.next_button)
        footer.addSpacing(SPACE_S)
        footer.addWidget(self.position)
        footer.addStretch()
        footer.addWidget(close)
        layout.addLayout(footer)
        return content

    def _label(self, text, role=None):
        label = QLabel(to_html(text, theme_manager.palette))
        label.setTextFormat(Qt.RichText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        label.setOpenExternalLinks(False)
        label.linkActivated.connect(self._follow_link)
        if role:
            label.setObjectName(role)
        return label

    def _try_button(self, target, text="Try it"):
        button = link_button(text, lambda: self.run(target))
        button.setAutoDefault(False)  # (Enter in the search field is not a click)
        button.setToolTip(f"Does it in the main window: {_target_words(target)}")
        button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._try_links.append((button, target))
        return button

    def _row(self, marker, text, target=None):
        """A line of a list: its marker (a number or a bullet), its text and
        its Try it link."""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE_M)
        row.addWidget(marker, 0, Qt.AlignTop)
        row.addWidget(self._label(text), 1)
        if target is not None:
            row.addWidget(self._try_button(target), 0, Qt.AlignTop)
        return row

    def _number(self, number):
        label = QLabel(str(number))
        label.setObjectName("StepNumber")
        label.setAlignment(Qt.AlignCenter)
        return label

    @staticmethod
    def _key_caps(keys):
        """``keys`` (alternatives) drawn as the keyboard shortcuts window
        draws them: a Key cap for each key pressed, "+" between them, "or"
        between alternatives; long alternatives one under the other."""
        cell = QWidget()
        lines = QVBoxLayout(cell)
        lines.setContentsMargins(0, 0, 0, 0)
        lines.setSpacing(SPACE_XS)
        parts = [part for key in keys for part in key_parts(key)]
        stacked = len(keys) > 1 and (len(parts) > 3 or sum(map(len, parts)) > 8)
        line = None
        for index, key in enumerate(keys):
            if line is None or stacked:
                line = QHBoxLayout()
                line.setContentsMargins(0, 0, 0, 0)
                line.setSpacing(SPACE_XS)
                lines.addLayout(line)
            for word, role in ([("or", "Hint")] if index else []) + [
                pair
                for i, part in enumerate(key_parts(key))
                for pair in ([("+", "Hint")] if i else []) + [(part, "Key")]
            ]:
                label = QLabel(word)
                label.setObjectName(role)
                line.addWidget(label)
        for i in range(lines.count()):
            lines.itemAt(i).layout().addStretch()
        return cell

    def _block(self, block):
        """The widget (or layout) showing ``block``."""
        if isinstance(block, Para):
            return self._label(block.text)
        if isinstance(block, Heading):
            label = QLabel(block.text)
            label.setObjectName("SectionTitle")
            label.setWordWrap(True)
            return label
        if isinstance(block, Tip):
            return self._label(block.text, "Tip")
        if isinstance(block, (Steps, Bullets)):
            box = QVBoxLayout()
            box.setSpacing(SPACE_S + (2 if isinstance(block, Steps) else 0))
            for number, item in enumerate(block.items, start=1):
                if isinstance(block, Steps):
                    marker = self._number(number)
                else:
                    marker = QLabel("•")
                    marker.setFixedWidth(10)
                box.addLayout(self._row(marker, _item_text(item), _item_target(item)))
            return box
        if isinstance(block, Keys):
            # Each table's first column as wide as its own keys
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(SPACE_L)
            grid.setVerticalSpacing(SPACE_S)
            for row, (keys, text) in enumerate(block_keys(block)):
                if block.source == "mouse":
                    cell = QLabel(gesture_html(keys, theme_manager.palette))
                    cell.setTextFormat(Qt.RichText)
                else:
                    cell = self._key_caps(keys)
                grid.addWidget(cell, row, 0, Qt.AlignTop | Qt.AlignLeft)
                grid.addWidget(self._label(text), row, 1)
            grid.setColumnStretch(1, 1)
            return grid
        if isinstance(block, Figure):
            box = QVBoxLayout()
            box.setSpacing(SPACE_M)
            box.addWidget(MainWindowFigure())
            legend = QVBoxLayout()
            legend.setSpacing(SPACE_S)
            for number, (title, text) in enumerate(block.legend, start=1):
                legend.addLayout(self._row(self._number(number), f"**{title}**: {text}"))
            box.addLayout(legend)
            return box
        if isinstance(block, Button):
            row = QHBoxLayout()
            button = QPushButton(block.text)
            button.setAutoDefault(False)
            button.clicked.connect(lambda: self.run(block.try_it))
            self._try_links.append((button, block.try_it))
            row.addWidget(button)
            row.addStretch()
            return row
        raise TypeError(f"Not a tutorial block: {block!r}")

    def _page(self, section):
        """The page of ``section``: a column of its blocks, scrolled
        vertically only."""
        scroll = QScrollArea()
        scroll.setObjectName(f"page-{section.id}")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(SPACE_L + SPACE_S, SPACE_L + SPACE_XS, SPACE_L, SPACE_L)
        row.setSpacing(0)
        column = QWidget()
        column.setMaximumWidth(PAGE_WIDTH)
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_M)
        layout.addWidget(kicker(section.group))
        title = QLabel(section.title)
        title.setObjectName("DialogTitle")
        title.setWordWrap(True)
        layout.addWidget(title)
        layout.addSpacing(SPACE_XS)
        for index, block in enumerate(section.blocks):
            if isinstance(block, Heading) and index:
                layout.addSpacing(SPACE_S)
            shown = self._block(block)
            if isinstance(shown, QWidget):
                layout.addWidget(shown)
            else:
                layout.addLayout(shown)
        layout.addStretch()
        # At the left, beside the list, whether the page scrolls or not; a
        # wide window's room goes to the right of it
        row.addWidget(column, 1)
        row.addStretch(0)
        scroll.setWidget(holder)
        set_hand_cursor(holder)
        return scroll

    # -- moving between sections
    def current_section(self):
        return SECTIONS[self._index].id

    def show_section(self, section_id):
        """Show the section ``section_id``. False if there is none."""
        index = next((i for i, s in enumerate(SECTIONS) if s.id == section_id), None)
        if index is None:
            return False
        self._index = index
        section = SECTIONS[index]
        page = self._pages.get(section.id)
        if page is None:
            page = self._pages[section.id] = self._page(section)
            self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)
        page.verticalScrollBar().setValue(0)
        for row in range(self.nav.count()):
            if self.nav.item(row).data(_SECTION_ROLE) == section.id:
                self.nav.blockSignals(True)
                self.nav.setCurrentRow(row)
                self.nav.blockSignals(False)
                self.nav.scrollToItem(self.nav.item(row))
                break
        self.position.setText(f"{index + 1} of {len(SECTIONS)}")
        self.prev_button.setEnabled(index > 0)
        self.next_button.setEnabled(index < len(SECTIONS) - 1)
        self.refresh_try_links()
        self._remember(section.id)
        self.sectionShown.emit(section.id)
        return True

    def step(self, by):
        """Show the section ``by`` places on (1: the next, -1: the one before)."""
        index = min(max(self._index + by, 0), len(SECTIONS) - 1)
        if index != self._index:
            self.show_section(SECTIONS[index].id)

    def _on_nav_changed(self, item, _previous):
        if item is not None and item.data(_SECTION_ROLE):
            self.show_section(item.data(_SECTION_ROLE))

    def _follow_link(self, link):
        if link.startswith("section:"):
            self.show_section(link.split(":", 1)[1])

    # -- the search
    def matches(self, text):
        """The ids of the sections that have every word of ``text``, in order."""
        words = text.lower().split()
        return [s.id for s in SECTIONS if all(word in self._haystack[s.id] for word in words)]

    def _filter(self, text):
        shown = set(self.matches(text))
        groups = set()
        for row in range(self.nav.count()):
            item = self.nav.item(row)
            section_id = item.data(_SECTION_ROLE)
            if section_id:
                item.setHidden(section_id not in shown)
                if section_id in shown:
                    groups.add(section_by_id(section_id).group)
        for row in range(self.nav.count()):
            item = self.nav.item(row)
            if item.data(_GROUP_ROLE):
                item.setHidden(item.data(_GROUP_ROLE) not in groups)
        self.nav.setVisible(bool(shown))
        self.no_match.setVisible(not shown)

    def best_match(self, text):
        """The section ``text`` is most likely after, or None (nothing
        typed, or nothing found): of those that match, the one with the
        words in its title, then in its headings, then the one that uses
        them most, then the first. Only whole words (or their plural) count
        in a title or a heading, so "tab" is not "tables". Welcome, which
        names much of the tutorial, only when nothing else matches."""
        words = text.lower().split()
        found = self.matches(text) if words else []
        if not found:
            return None

        def named(where):
            return sum(bool(re.search(rf"\b{re.escape(word)}(?:s|es)?\b", where)) for word in words)

        phrase = re.compile(rf"\b{re.escape(' '.join(words))}") if len(words) > 1 else None

        def weight(section_id):
            section = section_by_id(section_id)
            headings = "\n".join(b.text for b in section.blocks if isinstance(b, Heading)).lower()
            haystack = self._haystack[section_id]
            # Words typed together ("rms box") count most where they are
            # written together
            together = len(phrase.findall(haystack)) if phrase else 0
            uses = sum(haystack.count(word) for word in words)
            return named(section.title.lower()), together, named(headings), uses

        return max([s for s in found if s != SECTIONS[0].id] or found, key=weight)

    def _open_best_match(self):
        """Enter in the search field: the best match, if any (Enter with
        nothing typed stays where it is)."""
        found = self.best_match(self.search.text())
        if found:
            self.show_section(found)

    # -- Try it
    def run(self, target):
        """Do ``target`` in the main window: trigger its menu action, or
        click its button."""
        found = find_target(self._main, target)
        if found is None or not found.isEnabled():
            self.refresh_try_links()
            return False
        if isinstance(found, QAbstractButton):
            found.click()
        else:
            found.trigger()
        return True

    def refresh_try_links(self):
        """Each Try it link shown only if the main window has its command
        (and it is visible), greyed while that is disabled."""
        alive = []
        for button, target in self._try_links:
            try:
                found = find_target(self._main, target)
                hidden = found is None or (not isinstance(found, QAbstractButton) and not found.isVisible())
                button.setVisible(not hidden)
                button.setEnabled(not hidden and found.isEnabled())
                alive.append((button, target))
            except RuntimeError:  # (its page was rebuilt)
                pass
        self._try_links = alive

    def changeEvent(self, event):
        # Back from the main window: what can be done there may have changed
        if event.type() == QEvent.ActivationChange and self.isActiveWindow():
            self.refresh_try_links()
        super().changeEvent(event)

    def keyPressEvent(self, event):
        # Esc in the search field empties it, rather than closing the window
        if event.key() == Qt.Key_Escape and self.search.hasFocus() and self.search.text():
            self.search.clear()
            return
        super().keyPressEvent(event)

    # -- theme and memory
    def _on_theme_changed(self, _theme):
        """The pages' colours are in their text: draw them again."""
        for page in self._pages.values():
            self.stack.removeWidget(page)
            page.deleteLater()
        self._pages = {}
        self._try_links = []
        self._style_headings()
        self.show_section(self.current_section())

    @staticmethod
    def _remembered():
        try:
            value = QSettings(*SETTINGS).value(LAST_SECTION_KEY, "")
            return value if isinstance(value, str) and section_by_id(value) is not None else None
        except Exception:
            return None

    @staticmethod
    def _remember(section_id):
        try:
            QSettings(*SETTINGS).setValue(LAST_SECTION_KEY, section_id)
        except Exception:
            pass

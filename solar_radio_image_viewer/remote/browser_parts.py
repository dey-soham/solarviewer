"""
The pieces of the remote file browser (remote_file_browser.py): its line
icons, what an entry is called and how its size and date read, the path
bar, the places column, the file list and the details column.

They show what they are given and say what was clicked; the dialog does
the listing, reading and fetching.
"""

import os
import posixpath
import re
from datetime import datetime

from PyQt5.QtCore import (
    QAbstractTableModel, QByteArray, QEvent, QModelIndex, QRect, QRectF, QSize, Qt, pyqtSignal,
)
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionButton,
    QStylePainter,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ..ui.styles import FONT_META, FONT_SMALL, STATUS_DOT, theme_manager
from ..ui.widgets import kicker, kicker_row, rule

# ---------------------------------------------------------------- icons ----

# The inside of each 24x24 SVG (the handoff's line icons) and its stroke width
_SHAPES = {
    "back": ('<path d="M15 6l-6 6 6 6"/>', 1.8),
    "up": ('<path d="M12 19V5M6 11l6-6 6 6"/>', 1.8),
    "home": ('<path d="M4 11l8-7 8 7v9h-5v-6H9v6H4z"/>', 1.6),
    "search": ('<circle cx="11" cy="11" r="6"/><path d="M16 16l4 4"/>', 1.8),
    "refresh": ('<path d="M20 12a8 8 0 11-2.3-5.7M20 4v4h-4"/>', 1.6),
    "place": ('<path d="M3 7h6l2 2h10v10H3z"/>', 1.6),
    "recent": ('<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>', 1.6),
    "file": ('<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>', 1.6),
    "spectrum": ('<path d="M6 3h8l4 4v14H6z"/><path d="M3 15h4M3 18h6M3 12h2"/>', 1.6),
    "check": ('<path d="M5 12l5 5 9-10"/>', 2.4),
    "sort_down": ('<path d="M6 9l6 6 6-6"/>', 2.0),
    "sort_up": ('<path d="M6 15l6-6 6 6"/>', 2.0),
}
# Filled shapes
_FILLED = {"folder": '<path d="M3 6h6l2 2h10v11H3z"/>'}

_pixmaps = {}


def _render(name, colour, pixels):
    """The icon ``name`` in ``colour``, ``pixels`` square."""
    key = (name, colour, pixels)
    if key not in _pixmaps:
        from PyQt5.QtSvg import QSvgRenderer

        if name in _FILLED:
            inside, paint = _FILLED[name], f'fill="{colour}" stroke="none"'
        else:
            inside, width = _SHAPES[name]
            paint = (
                f'fill="none" stroke="{colour}" stroke-width="{width}" '
                'stroke-linecap="round" stroke-linejoin="round"'
            )
        markup = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
            f'viewBox="0 0 24 24" {paint}>{inside}</svg>'
        )
        renderer = QSvgRenderer(QByteArray(markup.encode("utf-8")))
        pixmap = QPixmap(pixels, pixels)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        renderer.render(painter)
        painter.end()
        _pixmaps[key] = pixmap
    return _pixmaps[key]


def pixmap(name, colour, size, ratio=1.0):
    """The icon ``name`` in ``colour`` (a palette value), ``size`` px
    square, sharp on a display of device pixel ratio ``ratio``."""
    result = QPixmap(_render(name, colour, max(1, round(size * ratio))))
    result.setDevicePixelRatio(result.width() / size)
    return result


def icon(name, palette, size=15):
    """``name`` as a button's icon: in the values' colour, the text's when
    hovered, greyed when disabled."""
    result = QIcon()
    for pixels in (size, size * 2):
        result.addPixmap(_render(name, palette["text_strong"], pixels), QIcon.Normal)
        result.addPixmap(_render(name, palette["text"], pixels), QIcon.Active)
        result.addPixmap(_render(name, palette["disabled"], pixels), QIcon.Disabled)
    return result


def qcolor(value):
    """A palette value ("#rrggbb" or "rgba(r, g, b, a)") as a QColor."""
    match = re.match(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)", value)
    if not match:
        return QColor(value)
    red, green, blue, alpha = match.groups()
    colour = QColor(int(red), int(green), int(blue))
    colour.setAlphaF(float(alpha))
    return colour


def mono_font(points):
    """The monospace font (the stylesheet's ``monospace``) at ``points``."""
    font = QFont("monospace")
    # (and as its only family: a painter resolves a font against its
    # widget's, whose stylesheet family list would come first)
    font.setFamilies(["monospace"])
    font.setStyleHint(QFont.Monospace)
    font.setPointSizeF(points)
    return font


def breakable(text):
    """``text`` with places a wrapped label may break it: after each /, _,
    - and . (a file name or path has no spaces to break at)."""
    return re.sub(r"([/_.\-])", "\\1\u200b", text)


# ------------------------------------------------------ what entries are ----

FOLDER, CASA, TABLE, FITS, SPECTRUM, OTHER = "folder", "casa", "table", "fits", "spectrum", "other"
IMAGES = (CASA, FITS, SPECTRUM)  # what the main window opens
FOLDERS = (FOLDER, TABLE)  # what opening goes into

_FITS_ENDINGS = (".fits", ".fts", ".fit")
_SPECTRUM_NAME = re.compile(r"dyn_?spec|dspec|spectr", re.IGNORECASE)
_TABLE_TYPES = {"Measurement Set": "MS", "Calibration": "Cal table"}


def fits_named(name):
    """Whether ``name`` is a FITS file's: .fits, .fts or .fit, also
    compressed (.gz, .fz), as the main window opens them."""
    lower = name.lower()
    for suffix in (".gz", ".fz"):
        if lower.endswith(suffix):
            lower = lower[: -len(suffix)]
    return lower.endswith(_FITS_ENDINGS)


def casa_named(name):
    """Whether a folder's ``name`` is a CASA image's (x.image, x.im,
    x.image.pbcor...)."""
    lower = name.lower()
    return lower.endswith((".image", ".im")) or ".image." in lower


def kind_of(entry):
    """What the listed ``entry`` (a RemoteFileInfo) is, from its name and,
    for a folder, the table it holds when the listing said: FOLDER, CASA,
    TABLE (another CASA table, a folder too), FITS, SPECTRUM (a FITS file
    named as a dynamic spectrum; a header read can tell better) or OTHER."""
    if entry.is_dir:
        table = entry.table_type
        if table == "Image" or (table is None and casa_named(entry.name)):
            return CASA
        return TABLE if table else FOLDER
    if fits_named(entry.name):
        return SPECTRUM if _SPECTRUM_NAME.search(entry.name) else FITS
    return OTHER


def type_text(entry, kind):
    """The Type column's word for ``entry``."""
    if kind == TABLE:
        return _TABLE_TYPES.get(entry.table_type, "CASA table")
    if kind == OTHER:
        extension = os.path.splitext(entry.name)[1][1:]
        if extension and len(extension) <= 5 and not entry.name.startswith("."):
            return f"{extension.upper()} file"
        return "File"
    return {FOLDER: "Folder", CASA: "CASA image", FITS: "FITS image", SPECTRUM: "Spectrum"}[kind]


def has_size(entry, kind):
    """Whether ``entry``'s size is known: a file's, or a CASA image's whole
    folder when the server added it up (a folder's own size says nothing)."""
    return not entry.is_dir or (kind == CASA and entry.table_type == "Image")


_UNITS = (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024))


def size_text(size):
    """``size`` bytes as "6.3 MB", "380 MB"."""
    for unit, scale in _UNITS:
        if size >= scale:
            value = size / scale
            return f"{value:.1f} {unit}" if value < 10 else f"{value:.0f} {unit}"
    return f"{size} B"


def progress_text(done, total):
    """``done`` of ``total`` bytes as "142 / 380 MB"."""
    unit, scale = next(((u, s) for u, s in _UNITS if total >= s), ("B", 1))
    digits = 1 if total / scale < 10 and scale > 1 else 0
    return f"{done / scale:.{digits}f} / {total / scale:.{digits}f} {unit}"


def short_time(mtime):
    return datetime.fromtimestamp(mtime).strftime("%m-%d %H:%M")


def full_time(mtime):
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")


def short_folder(path):
    """A folder's last two names ("solar/2026-10-24"), for a Recent row."""
    parts = [part for part in re.split(r"[/\\]", path) if part]
    if len(parts) <= 2:
        return path
    return "/".join(parts[-2:])


# ------------------------------------------------------------- headers ----


def _axis(ctype):
    name = str(ctype or "").upper()
    return next((kind for kind in ("TIME", "FREQ") if kind in name), name)


def header_kind(text):
    """SPECTRUM or FITS (an image) from a header read, as fits_io.fits_kind
    tells them; None if it says neither."""
    from astropy.io import fits

    header = fits.Header.fromstring(text)
    if str(header.get("EXTNAME", "")).strip().upper() in ("DYNAMIC_SPECTRUM", "TIME_AXIS", "FREQ_AXIS"):
        return SPECTRUM
    if {_axis(header.get("CTYPE1")), _axis(header.get("CTYPE2"))} == {"TIME", "FREQ"}:
        return SPECTRUM
    columns = {_axis(header.get(f"TTYPE{i}")) for i in range(1, int(header.get("TFIELDS", 0) or 0) + 1)}
    if {"TIME", "FREQ"} <= columns:
        return SPECTRUM
    if int(header.get("NAXIS", 0) or 0) >= 2:
        return FITS
    return None


def _trim(value):
    return f"{value:.2f}".rstrip("0").rstrip(".")


_FREQUENCY_UNITS = (("GHz", 1e9), ("MHz", 1e6), ("kHz", 1e3))
_HERTZ = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}


def _frequency(low, high=None):
    top = abs(high if high is not None else low)
    unit, scale = next(((u, s) for u, s in _FREQUENCY_UNITS if top >= s), ("Hz", 1.0))
    if high is None or _trim(low / scale) == _trim(high / scale):
        return f"{_trim(low / scale)} {unit}"
    return f"{_trim(low / scale)}–{_trim(high / scale)} {unit}"


def header_rows(text):
    """[(label, value, mono)] summing up a FITS header: its telescope, when
    it was observed, its frequency (or wavelength), unit and beam; only
    those it has."""
    from astropy.io import fits

    header = fits.Header.fromstring(text)

    def value(*keys):
        for key in keys:
            found = header.get(key)
            if found not in (None, "") and str(found).strip():
                return found
        return None

    rows = []
    telescope = value("TELESCOP", "INSTRUME", "OBSERVAT", "OBSRVTRY")
    if telescope is not None:
        rows.append(("Telescope", str(telescope).strip(), False))

    date = value("DATE-OBS", "DATE_OBS", "DATE-BEG")
    if date is not None:
        date = str(date).strip()
        day, _, time_of_day = date.replace("T", " ").partition(" ")
        if re.match(r"\d{4}/\d\d/\d\d$", day):
            day = day.replace("/", "-")
        if not time_of_day:
            time_of_day = str(value("TIME-OBS", "TIME_OBS") or "").strip()
        rows.append(("Observed", f"{day} {time_of_day[:8]}".strip(), True))

    frequency = None
    for n in range(1, int(header.get("NAXIS", 0) or 0) + 1):
        ctype = str(header.get(f"CTYPE{n}", "")).upper()
        if "FREQ" not in ctype:
            continue
        # (the unit in CUNIT, or in the name: "Frequency [MHz]")
        unit = str(header.get(f"CUNIT{n}", "") or "").strip().upper()
        if not unit:
            bracket = re.search(r"\[(\w+)\]", ctype)
            unit = bracket.group(1) if bracket else "HZ"
        scale = _HERTZ.get(unit, 1.0)
        try:
            centre = float(header[f"CRVAL{n}"]) * scale
        except (KeyError, TypeError, ValueError):
            break
        length = int(header.get(f"NAXIS{n}", 1) or 1)
        step = float(header.get(f"CDELT{n}", 0) or 0) * scale
        if length > 1 and step:
            pixel = float(header.get(f"CRPIX{n}", 1) or 1)
            ends = sorted((centre + (1 - pixel) * step, centre + (length - pixel) * step))
            # (an axis that is not linear, its channels in a table, says
            # nothing true: e-Callisto's placeholder 200 MHz down by 1)
            if ends[0] > 0:
                frequency = _frequency(*ends)
        elif centre > 0:
            frequency = _frequency(centre)
        break
    if frequency is None:
        keyword = value("FREQ", "OBS-FREQ", "OBSFREQ", "RESTFRQ", "RESTFREQ")
        try:
            hertz = float(keyword)
        except (TypeError, ValueError):
            hertz = None
        if hertz:
            frequency = _frequency(hertz if hertz > 1e4 else hertz * 1e6)  # (small: in MHz)
    if frequency is not None:
        rows.append(("Frequency", frequency, False))
    else:
        wavelength = value("WAVELNTH")
        if wavelength is not None:
            unit = str(header.get("WAVEUNIT", "") or "").strip()
            unit = {"angstrom": "Å", "angstroms": "Å"}.get(unit.lower(), unit)
            rows.append(("Wavelength", f"{wavelength} {unit}".strip(), False))

    unit = value("BUNIT")
    if unit is not None:
        rows.append(("Unit", str(unit).strip(), False))

    try:
        major, minor = float(header["BMAJ"]) * 3600, float(header["BMIN"]) * 3600
    except (KeyError, TypeError, ValueError):
        pass
    else:
        def arcsec(v):
            return f"{v:.0f}″" if v >= 10 else f"{v:.1f}″"

        rows.append(("Beam", f"{arcsec(major)} × {arcsec(minor)}", False))
    return rows


def header_lines(text):
    """A header's text as its 80-character cards, one a line, to END."""
    lines = []
    for start in range(0, len(text), 80):
        card = text[start : start + 80].rstrip()
        lines.append(card)
        if card == "END":
            break
    return "\n".join(lines)


# ------------------------------------------------------------ small parts ----


class ElidedLabel(QLabel):
    """A one-line label that cuts its text short (with …) to fit its width,
    instead of widening its column."""

    def __init__(self, text="", mode=Qt.ElideRight, parent=None):
        super().__init__(parent)
        self._full = ""
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text):
        self._full = text
        super().setText(text)

    def text(self):
        return self._full

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.contentsRect()
        shown = self.fontMetrics().elidedText(self._full, self._mode, rect.width())
        self.style().drawItemText(
            painter, rect, Qt.AlignLeft | Qt.AlignVCenter, self.palette(),
            self.isEnabled(), shown, self.foregroundRole(),
        )


class StatusDot(QWidget):
    """The connection's dot: the palette's status_on or status_off."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(STATUS_DOT + 1, STATUS_DOT + 1)
        self._on = False

    def set_on(self, on):
        self._on = bool(on)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme_manager.palette["status_on" if self._on else "status_off"]))
        painter.drawEllipse(QRectF(0.5, 0.5, STATUS_DOT, STATUS_DOT))


class SmallLink(QPushButton):
    """A link in the smallest type (the SmallLink role): "Reconnect"."""

    def __init__(self, text, slot=None, parent=None):
        super().__init__(text, parent)
        self.setObjectName("SmallLink")
        self.setCursor(Qt.PointingHandCursor)
        self.setAutoDefault(False)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        if slot is not None:
            self.clicked.connect(slot)


# -------------------------------------------------------------- path bar ----


class PathBar(QFrame):
    """The folder shown, as its folders to click (the PathBar role). Clicked
    on its empty part (or ``start_editing``, Ctrl+L) it becomes a field to
    type a path in: Enter goes there, Esc puts the folders back."""

    pathChosen = pyqtSignal(str)  # a folder clicked, or a path typed

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PathBar")
        self.setFixedHeight(30)
        self.setCursor(Qt.IBeamCursor)
        self.setToolTip("Click to type a path")
        self._path = "/"
        self._split = posixpath.split
        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(1, 1, 1, 1)  # (inside its outline)

        crumbs = QWidget()
        crumbs.setCursor(Qt.IBeamCursor)
        self._row = QHBoxLayout(crumbs)
        self._row.setContentsMargins(8, 0, 8, 0)
        self._row.setSpacing(2)
        self._stack.addWidget(crumbs)
        self._crumbs = crumbs
        self._items = []  # (separator before it or None, segment button)
        # Stands for the folders left out when the path is too long
        self._more = self._segment("…", None)
        self._more_separator = self._separator()

        self.edit = QLineEdit()
        self.edit.setObjectName("PathEdit")
        self.edit.installEventFilter(self)
        self._stack.addWidget(self.edit)

    def _segment(self, text, target):
        button = QPushButton(text)
        button.setObjectName("PathSegment")
        button.setCursor(Qt.PointingHandCursor)
        button.setFocusPolicy(Qt.NoFocus)
        button.setAutoDefault(False)
        button.setProperty("target", target)
        button.clicked.connect(lambda: self.pathChosen.emit(button.property("target")))
        return button

    def _separator(self):
        label = QLabel("›")
        label.setObjectName("PathSeparator")
        return label

    @property
    def editing(self):
        return self._stack.currentWidget() is self.edit

    def path(self):
        return self._path

    def set_path(self, path, split=posixpath.split):
        """Show ``path``, a folder split into its parent and name by
        ``split`` (posixpath.split on a server, os.path.split here)."""
        self._path = path
        self._split = split
        self.stop_editing()
        while self._row.count():
            item = self._row.takeAt(0)
            widget = item.widget()
            if widget is not None and widget not in (self._more, self._more_separator):
                widget.hide()  # (now: it is deleted later)
                widget.deleteLater()
        parts = []
        current = path
        while True:
            head, tail = split(current)
            if not tail:
                parts.append((head or current, head or current))
                break
            parts.append((tail, current))
            if head == current:
                break
            current = head
        parts.reverse()
        self._items = []
        for n, (name, target) in enumerate(parts):
            separator = self._separator() if n else None
            button = self._segment(name, target)
            button.setToolTip(target)
            self._items.append((separator, button))
        self._row.addWidget(self._items[0][1])
        self._row.addWidget(self._more_separator)
        self._row.addWidget(self._more)
        for separator, button in self._items[1:]:
            self._row.addWidget(separator)
            self._row.addWidget(button)
        self._row.addStretch()
        last = self._items[-1][1]
        last.setProperty("current", "true")
        self._fit()

    def _fit(self):
        """Leave out the folders after the first that do not fit, for "…"."""
        if not self._items:
            return
        room = self._crumbs.width() - 16
        spacing = self._row.spacing()

        def width(widget):
            return widget.sizeHint().width() + spacing

        needed = sum(width(b) + (width(s) if s else 0) for s, b in self._items)
        hidden = 0
        if needed > room and len(self._items) > 2:
            needed += width(self._more) + width(self._more_separator)
            while hidden < len(self._items) - 2 and needed > room:
                separator, button = self._items[1 + hidden]
                needed -= width(button) + width(separator)
                hidden += 1
        for n, (separator, button) in enumerate(self._items):
            shown = not (1 <= n <= hidden)
            button.setVisible(shown)
            if separator is not None:
                separator.setVisible(shown)
        self._more.setVisible(hidden > 0)
        self._more_separator.setVisible(hidden > 0)
        if hidden:
            # It goes to the last folder left out
            target = self._items[hidden][1].property("target")
            self._more.setProperty("target", target)
            self._more.setToolTip(target)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def showEvent(self, event):
        super().showEvent(event)
        self._fit()

    def mousePressEvent(self, event):
        # (on the bar's empty part: a folder's button takes its own clicks)
        if event.button() == Qt.LeftButton and not self.editing:
            self.start_editing()
        else:
            super().mousePressEvent(event)

    def start_editing(self):
        self.edit.setText(self._path)
        self._stack.setCurrentWidget(self.edit)
        self._outline(True)
        self.edit.setFocus(Qt.ShortcutFocusReason)
        self.edit.selectAll()

    def stop_editing(self):
        if self.editing:
            self._stack.setCurrentWidget(self._crumbs)
            self._outline(False)
            self._fit()

    def _outline(self, editing):
        # (the accent outline of a field being typed in)
        self.setProperty("editing", "true" if editing else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def eventFilter(self, watched, event):
        if watched is self.edit:
            if event.type() == QEvent.KeyPress:
                if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                    text = self.edit.text().strip()
                    if text:
                        self.pathChosen.emit(text)
                    else:
                        self.stop_editing()
                    return True  # (not the dialog's Open)
                if event.key() == Qt.Key_Escape:
                    self.stop_editing()
                    return True  # (not closing the dialog)
            elif event.type() == QEvent.FocusOut and event.reason() != Qt.PopupFocusReason:
                self.stop_editing()
        return super().eventFilter(watched, event)


# ---------------------------------------------------------------- places ----


class PlaceRow(QPushButton):
    """A row of the places column (the PlaceRow role): an icon and a name,
    cut short in the middle if long; the folder it goes to in its tooltip.
    ``current`` marks the folder shown (with an accent bar)."""

    def __init__(self, icon_name, text, path, current=False, parent=None):
        super().__init__(parent)
        self.setObjectName("PlaceRow")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAutoDefault(False)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setToolTip(path)
        self.setProperty("current", "true" if current else "false")
        self.icon_name = icon_name
        self.label = text
        self.path = path

    def sizeHint(self):
        return QSize(120, max(self.fontMetrics().height(), 14) + 14)

    def minimumSizeHint(self):
        return QSize(40, self.sizeHint().height())

    def paintEvent(self, event):
        painter = QStylePainter(self)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = ""
        option.icon = QIcon()
        painter.drawControl(QStyle.CE_PushButtonBevel, option)  # (its fill and accent bar)
        palette = theme_manager.palette
        colour = palette["text"] if self.property("current") == "true" else palette["text_strong"]
        left, size, height = 6, 14, self.height()
        painter.drawPixmap(
            left, (height - size) // 2, pixmap(self.icon_name, colour, size, self.devicePixelRatioF())
        )
        text_left = left + size + 8
        room = self.width() - text_left - 6
        painter.setPen(QColor(colour))
        painter.drawText(
            QRect(text_left, 0, room, height), Qt.AlignLeft | Qt.AlignVCenter,
            self.fontMetrics().elidedText(self.label, Qt.ElideMiddle, room),
        )


class PlacesPanel(QFrame):
    """The places column (the Sidebar role): the connection, Home and the
    pinned folders, the recent ones, and the cache at the foot."""

    placeChosen = pyqtSignal(str)
    unpinRequested = pyqtSignal(str)
    reconnectRequested = pyqtSignal()
    switchRequested = pyqtSignal()
    clearCacheRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(184)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 14, 10, 14)
        layout.setSpacing(18)

        # The connection
        connection = QWidget()
        column = QVBoxLayout(connection)
        column.setContentsMargins(4, 0, 4, 0)
        column.setSpacing(8)
        column.addWidget(kicker("Connection"))
        line = QHBoxLayout()
        line.setSpacing(8)
        self.dot = StatusDot()
        line.addWidget(self.dot, 0, Qt.AlignVCenter)
        names = QVBoxLayout()
        names.setSpacing(0)
        self.host_label = ElidedLabel()
        self.host_label.setObjectName("DetailValue")
        names.addWidget(self.host_label)
        self.user_label = ElidedLabel()
        self.user_label.setObjectName("Meta")
        names.addWidget(self.user_label)
        line.addLayout(names, 1)
        column.addLayout(line)
        self.reason_label = QLabel()
        self.reason_label.setObjectName("Meta")
        self.reason_label.setWordWrap(True)
        column.addWidget(self.reason_label)
        self.switch_link = SmallLink("Switch server…", self.switchRequested.emit)
        column.addWidget(self.switch_link)
        self.reconnect_link = SmallLink("Reconnect", self.reconnectRequested.emit)
        column.addWidget(self.reconnect_link)
        layout.addWidget(connection)

        # Home and the pinned folders; the recent ones
        self._places = self._group("Places")
        layout.addWidget(self._places)
        self._recent = self._group("Recent")
        layout.addWidget(self._recent)
        layout.addStretch()

        # The cache
        cache = QWidget()
        column = QVBoxLayout(cache)
        column.setContentsMargins(4, 0, 4, 0)
        column.setSpacing(4)
        self.cache_label = QLabel()
        self.cache_label.setObjectName("Meta")
        self.cache_label.setWordWrap(True)
        column.addWidget(self.cache_label)
        self.clear_link = SmallLink("Clear cache", self.clearCacheRequested.emit)
        column.addWidget(self.clear_link)
        layout.addWidget(cache)

    def _group(self, title):
        group = QWidget()
        column = QVBoxLayout(group)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        heading = kicker(title)
        heading.setContentsMargins(4, 0, 4, 4)
        column.addWidget(heading)
        return group

    def _fill(self, group, rows, empty_text=""):
        column = group.layout()
        while column.count() > 1:
            widget = column.takeAt(1).widget()
            if widget is not None:
                widget.hide()  # (now: it is deleted later)
                widget.deleteLater()
        for row in rows:
            column.addWidget(row)
        if not rows and empty_text:
            hint = QLabel(empty_text)
            hint.setObjectName("Meta")
            hint.setWordWrap(True)
            hint.setContentsMargins(4, 0, 4, 0)
            column.addWidget(hint)

    def _row(self, icon_name, text, path, current=False):
        row = PlaceRow(icon_name, text, path, current)
        row.clicked.connect(lambda: self.placeChosen.emit(path))
        return row

    def set_places(self, home, pinned, current=None):
        """Home (``home``, or no row if None) and the ``pinned`` folders."""
        rows = [self._row("home", "Home", home, home == current)] if home else []
        for path in pinned:
            row = self._row("place", path, path, path == current)
            row.setContextMenuPolicy(Qt.CustomContextMenu)
            row.customContextMenuRequested.connect(lambda _pos, p=path, r=row: self._unpin_menu(p, r))
            rows.append(row)
        self._fill(self._places, rows)

    def _unpin_menu(self, path, row):
        from PyQt5.QtGui import QCursor
        from PyQt5.QtWidgets import QMenu

        menu = QMenu(row)
        menu.addAction("Unpin from Places", lambda: self.unpinRequested.emit(path))
        menu.exec_(QCursor.pos())

    def set_recent(self, paths, current=None):
        """The folders images were last opened from, ``current`` marked."""
        rows = [self._row("recent", short_folder(p), p, p == current) for p in paths]
        self._fill(self._recent, rows, "Folders you open images from show here.")

    def show_connected(self, host, detail, can_switch):
        self.dot.set_on(True)
        self.host_label.setText(host)
        self.user_label.setText(detail)
        self.user_label.show()
        self.reason_label.hide()
        self.switch_link.setVisible(can_switch)
        self.reconnect_link.hide()

    def show_disconnected(self, reason, reconnecting=False):
        self.dot.set_on(False)
        self.host_label.setText("Reconnecting…" if reconnecting else "Disconnected")
        self.user_label.hide()
        self.reason_label.setText(reason)
        self.reason_label.setVisible(bool(reason))
        self.switch_link.hide()
        self.reconnect_link.show()
        self.reconnect_link.setEnabled(not reconnecting)

    def set_cache(self, size, count):
        if count:
            files = "1 file" if count == 1 else f"{count} files"
            self.cache_label.setText(f"Cache · {files}, {size_text(size)}")
        else:
            self.cache_label.setText("Cache · empty")
        self.clear_link.setEnabled(bool(count))


# ------------------------------------------------------------- file list ----

ENTRY_ROLE = Qt.UserRole + 1
KIND_ROLE = Qt.UserRole + 2
CACHED_ROLE = Qt.UserRole + 3

NAME, TYPE, SIZE, MODIFIED = range(4)


class FileListModel(QAbstractTableModel):
    """A folder's entries as rows of Name, Type, Size and Modified: folders
    first, then the rest, each in the order chosen; filtered by a text,
    "Images only" and "Hidden files"."""

    HEADERS = ("Name", "Type", "Size", "Modified")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all = []  # [(entry, kind)] of the whole folder
        self._rows = []  # those shown, in order
        self._cached = set()  # paths with an up-to-date copy in the cache
        self.text = ""
        self.images_only = True
        self.show_hidden = False
        self.order = (MODIFIED, Qt.DescendingOrder)

    # (QAbstractTableModel)
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 4

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        entry, kind = self._rows[index.row()]
        column = index.column()
        if role == ENTRY_ROLE:
            return entry
        if role == KIND_ROLE:
            return kind
        if role == CACHED_ROLE:
            return entry.path in self._cached
        if role == Qt.DisplayRole:
            if column == NAME:
                return entry.name
            if column == TYPE:
                return type_text(entry, kind)
            if column == SIZE:
                return size_text(entry.size) if has_size(entry, kind) else "—"
            return short_time(entry.mtime)
        if role == Qt.ToolTipRole:
            if column == NAME:
                return entry.name
            if column == SIZE and entry.path in self._cached:
                return "In cache"
            if column == MODIFIED:
                return full_time(entry.mtime)
        return None

    def sort(self, column, order=Qt.AscendingOrder):
        self.order = (column, order)
        self.layoutAboutToBeChanged.emit()
        old = self.persistentIndexList()
        kept = [(self._rows[i.row()][0].path, i.column()) for i in old]
        self._sort()
        rows = {entry.path: n for n, (entry, _kind) in enumerate(self._rows)}
        self.changePersistentIndexList(old, [self.index(rows[p], c) for p, c in kept])
        self.layoutChanged.emit()

    # (its own)
    def set_entries(self, entries, cached=(), known=None):
        """Show ``entries`` (RemoteFileInfo), those whose paths are in
        ``cached`` with a copy in the cache; ``known``: {path: kind} for
        those a header read told better than their names."""
        known = known or {}
        self.beginResetModel()
        self._all = [(entry, known.get(entry.path) or kind_of(entry)) for entry in entries]
        self._cached = set(cached)
        self._apply()
        self.endResetModel()

    def refilter(self):
        self.beginResetModel()
        self._apply()
        self.endResetModel()

    def set_kind(self, path, kind):
        """``path`` is ``kind`` after all (its header read)."""
        self._all = [(e, kind if e.path == path else k) for e, k in self._all]
        self.refilter()

    def set_cached(self, cached):
        self._cached = set(cached)
        if self._rows:
            self.dataChanged.emit(self.index(0, SIZE), self.index(len(self._rows) - 1, SIZE))

    def is_cached(self, path):
        return path in self._cached

    def row_of(self, path):
        return next((n for n, (entry, _kind) in enumerate(self._rows) if entry.path == path), None)

    def entries(self):
        return [entry for entry, _kind in self._all]

    def _visible(self, entry, kind):
        return (self.show_hidden or not entry.name.startswith(".")) and (
            not self.images_only or kind != OTHER
        )

    def _apply(self):
        text = self.text.lower()
        self._rows = [
            (entry, kind) for entry, kind in self._all
            if self._visible(entry, kind) and (not text or text in entry.name.lower())
        ]
        self._sort()

    def _sort(self):
        column, order = self.order
        if column == TYPE:
            def key(row):
                return (type_text(*row).lower(), row[0].name.lower())
        elif column == SIZE:
            def key(row):
                return (row[0].size if has_size(*row) else -1, row[0].name.lower())
        elif column == MODIFIED:
            def key(row):
                return (row[0].mtime, row[0].name.lower())
        else:
            def key(row):
                return row[0].name.lower()
        descending = order == Qt.DescendingOrder
        folders = sorted((r for r in self._rows if r[1] in FOLDERS), key=key, reverse=descending)
        files = sorted((r for r in self._rows if r[1] not in FOLDERS), key=key, reverse=descending)
        self._rows = folders + files

    def counts(self):
        """What the folder holds and what is left out: {"folders", "images",
        "others", "others_hidden", "hidden", "shown", "matching"}."""
        counts = dict.fromkeys(("folders", "images", "others", "others_hidden", "hidden"), 0)
        unfiltered = 0
        for entry, kind in self._all:
            if not self.show_hidden and entry.name.startswith("."):
                counts["hidden"] += 1
                continue
            if kind in FOLDERS:
                counts["folders"] += 1
            elif kind in IMAGES:
                counts["images"] += 1
            elif self.images_only:
                counts["others_hidden"] += 1
                continue
            else:
                counts["others"] += 1
            unfiltered += 1
        counts["shown"] = unfiltered
        counts["matching"] = len(self._rows)
        return counts


def _count(n, one, many=None):
    return f"{n} {one if n == 1 else (many or one + 's')}"


def count_text(counts, filtering):
    """The line under the list: "2 folders · 7 images · 31 other files hidden"."""
    if filtering:
        return f"{counts['matching']} of {counts['shown']} match"
    parts = [_count(counts["folders"], "folder"), _count(counts["images"], "image")]
    if counts["others"]:
        parts.append(_count(counts["others"], "other file"))
    if counts["others_hidden"]:
        parts.append(_count(counts["others_hidden"], "other file") + " hidden")
    if counts["hidden"]:
        parts.append(_count(counts["hidden"], "hidden file"))
    return " · ".join(parts)


# Each column's room past its content's: the 16px edges, the 12px gaps
_PADDING = {NAME: (16, 0), TYPE: (12, 0), SIZE: (12, 0), MODIFIED: (12, 16)}
COLUMN_WIDTHS = {TYPE: 12 + 80, SIZE: 12 + 64, MODIFIED: 12 + 84 + 16}


def _content(rect, column):
    left, right = _PADDING[column]
    return rect.adjusted(left, 0, -right, 0)


class FileListHeader(QHeaderView):
    """The list's column names in the meta type, muted, with a caret after
    the one sorted by and a line under them."""

    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.setSectionsClickable(True)
        self.setHighlightSections(False)
        self.setSortIndicatorShown(True)
        self.setStretchLastSection(False)
        self.setMinimumSectionSize(40)

    def _font(self):
        font = QFont(self.font())
        font.setPointSizeF(FONT_META)
        font.setWeight(QFont.Normal)
        return font

    def sizeHint(self):
        hint = super().sizeHint()
        hint.setHeight(QFontMetrics(self._font()).height() + 18)
        return hint

    def paintSection(self, painter, rect, logical):
        palette = theme_manager.palette
        painter.save()
        font = self._font()
        painter.setFont(font)
        colour = palette["text_secondary"]
        painter.setPen(QColor(colour))
        content = _content(rect, logical)
        text = self.model().headerData(logical, Qt.Horizontal) if self.model() else ""
        right_aligned = logical == SIZE
        width = QFontMetrics(font).horizontalAdvance(text)
        painter.drawText(content, (Qt.AlignRight if right_aligned else Qt.AlignLeft) | Qt.AlignVCenter, text)
        if self.isSortIndicatorShown() and self.sortIndicatorSection() == logical:
            name = "sort_down" if self.sortIndicatorOrder() == Qt.DescendingOrder else "sort_up"
            caret = 10
            x = content.right() - width - 4 - caret if right_aligned else content.left() + width + 4
            painter.drawPixmap(
                x, content.center().y() - caret // 2 + 1,
                pixmap(name, colour, caret, self.devicePixelRatioF()),
            )
        painter.restore()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        painter.setPen(QPen(qcolor(theme_manager.palette["divider"]), 1))
        y = self.height() - 1
        painter.drawLine(0, y, self.width(), y)


class FileListDelegate(QStyledItemDelegate):
    """Paints a file list cell as the handoff draws its rows: the name with
    its icon (monospace for a file), muted type and date, the size with a
    check when the file is in the cache."""

    def _fonts(self, option):
        body = QFont(option.font)
        body.setPointSizeF(FONT_SMALL)
        body.setWeight(QFont.Normal)
        return body, mono_font(FONT_SMALL), mono_font(FONT_META)

    def sizeHint(self, option, index):
        body, mono, _small = self._fonts(option)
        height = max(QFontMetrics(body).height(), QFontMetrics(mono).height(), 15) + 14
        return QSize(80, height)

    def paint(self, painter, option, index):
        palette = theme_manager.palette
        body, mono, small = self._fonts(option)
        selected = bool(option.state & QStyle.State_Selected)
        kind = index.data(KIND_ROLE)
        column = index.column()
        rect = _content(option.rect, column)
        ratio = painter.device().devicePixelRatioF()
        text = index.data(Qt.DisplayRole) or ""
        painter.save()
        if column == NAME:
            folder = kind in FOLDERS or kind == CASA
            name = "folder" if folder else ("spectrum" if kind == SPECTRUM else "file")
            if selected:
                tint = palette["link"]
            else:
                tint = palette["text_faint"] if folder else palette["text_secondary"]
            size = 15
            painter.drawPixmap(rect.left(), rect.center().y() - size // 2 + 1, pixmap(name, tint, size, ratio))
            rect = rect.adjusted(size + 8, 0, 0, 0)
            font = body if folder else mono
            painter.setFont(font)
            painter.setPen(QColor(palette["text"] if selected else palette["text_strong"]))
            shown = QFontMetrics(font).elidedText(text, Qt.ElideMiddle, rect.width())
            painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, shown)
        elif column == TYPE:
            painter.setFont(body)
            painter.setPen(QColor(palette["text_strong"] if selected else palette["text_secondary"]))
            painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, QFontMetrics(body).elidedText(text, Qt.ElideRight, rect.width()))
        elif column == SIZE:
            if text == "—":
                painter.setFont(body)
                painter.setPen(QColor(palette["text_faint"]))
                painter.drawText(rect, Qt.AlignRight | Qt.AlignVCenter, text)
            else:
                painter.setFont(small)
                painter.setPen(QColor(palette["text"] if selected else palette["text_strong"]))
                painter.drawText(rect, Qt.AlignRight | Qt.AlignVCenter, text)
                if index.data(CACHED_ROLE):
                    check = 10
                    x = rect.right() - QFontMetrics(small).horizontalAdvance(text) - 4 - check
                    painter.drawPixmap(
                        x, rect.center().y() - check // 2 + 1,
                        pixmap("check", palette["status_on"], check, ratio),
                    )
        else:
            painter.setFont(small)
            painter.setPen(QColor(palette["text_dim"] if selected else palette["text_secondary"]))
            painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)
        painter.restore()


class FileListView(QTreeView):
    """The folder's files (the FileList role): rows with rounded corners, the
    selected one on the surface with an accent outline. Double-click or
    Enter opens a row; Backspace goes up; an empty list says why."""

    openRequested = pyqtSignal(QModelIndex)
    upRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("FileList")
        self.setHeader(FileListHeader(self))
        self.setItemDelegate(FileListDelegate(self))
        self.setRootIsDecorated(False)
        self.setItemsExpandable(False)
        self.setIndentation(0)
        self.setUniformRowHeights(True)
        self.setAllColumnsShowFocus(False)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.NoFrame)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WA_Hover)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.doubleClicked.connect(self.openRequested.emit)
        self.empty_text = ""
        self._hover_row = -1

    def setModel(self, model):
        super().setModel(model)
        header = self.header()
        header.setSectionResizeMode(NAME, QHeaderView.Stretch)
        for column, width in COLUMN_WIDTHS.items():
            header.setSectionResizeMode(column, QHeaderView.Fixed)
            header.resizeSection(column, width)

    def set_empty_text(self, text):
        self.empty_text = text
        self.viewport().update()

    def drawRow(self, painter, option, index):
        palette = theme_manager.palette
        row = QRectF(6, option.rect.top(), self.viewport().width() - 12, option.rect.height())
        path = QPainterPath()
        path.addRoundedRect(row.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        if self.selectionModel() is not None and self.selectionModel().isRowSelected(index.row(), index.parent()):
            painter.fillPath(path, QColor(palette["surface"]))
            painter.setPen(QPen(QColor(palette["highlight"]), 1))
            painter.drawPath(path)
        elif index.row() == self._hover_row:
            painter.fillPath(path, qcolor(palette["hover"]))
        painter.restore()
        super().drawRow(painter, option, index)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.empty_text and (self.model() is None or self.model().rowCount() == 0):
            painter = QPainter(self.viewport())
            font = QFont(self.font())
            font.setPointSizeF(FONT_SMALL)
            painter.setFont(font)
            painter.setPen(QColor(theme_manager.palette["text_secondary"]))
            painter.drawText(
                self.viewport().rect().adjusted(40, 0, -40, 0),
                Qt.AlignCenter | Qt.TextWordWrap, self.empty_text,
            )

    def mouseMoveEvent(self, event):
        row = self.indexAt(event.pos()).row()
        if row != self._hover_row:
            self._hover_row = row
            self.viewport().update()
        super().mouseMoveEvent(event)

    def viewportEvent(self, event):
        if event.type() == QEvent.Leave and self._hover_row != -1:
            self._hover_row = -1
            self.viewport().update()
        return super().viewportEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self.currentIndex().isValid():
                self.openRequested.emit(self.currentIndex())
            event.accept()  # (not the dialog's default button as well)
            return
        if event.key() == Qt.Key_Backspace and not event.modifiers():
            self.upRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


# --------------------------------------------------------------- details ----


class DetailsPanel(QFrame):
    """The details column (the Inspector role): the selected entry, from its
    listing alone, and its FITS header, read only when asked (or with
    "Read headers automatically" on)."""

    readRequested = pyqtSignal()
    fullHeaderRequested = pyqtSignal()

    LABEL_WIDTH = 64

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Inspector")
        self.setFixedWidth(236)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(14)

        layout.addWidget(kicker("Selected"))
        self.empty_label = QLabel("Select a file to see its size, date and header.")
        self.empty_label.setObjectName("Hint")
        self.empty_label.setWordWrap(True)
        layout.addWidget(self.empty_label)

        self.entry_block = QWidget()
        block = QVBoxLayout(self.entry_block)
        block.setContentsMargins(0, 0, 0, 0)
        block.setSpacing(14)
        names = QVBoxLayout()
        names.setSpacing(2)
        self.name_label = QLabel()
        self.name_label.setObjectName("DetailName")
        self.name_label.setWordWrap(True)
        names.addWidget(self.name_label)
        self.type_label = QLabel()
        self.type_label.setObjectName("Meta")
        names.addWidget(self.type_label)
        block.addLayout(names)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        grid.setColumnMinimumWidth(0, self.LABEL_WIDTH)
        grid.setColumnStretch(1, 1)
        self._rows = {}
        self.size_value = self._add_row(grid, "Size", QLabel(), "DetailValue")
        self.modified_value = self._add_row(grid, "Modified", QLabel(), "DetailMono")
        cache = QWidget()
        line = QHBoxLayout(cache)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(5)
        self.cache_check = QLabel()
        line.addWidget(self.cache_check)
        self.cache_text = QLabel()
        self.cache_text.setObjectName("DetailValue")
        line.addWidget(self.cache_text, 1)
        self._add_row(grid, "Cache", cache, None)
        self.path_value = self._add_row(grid, "Path", QLabel(), "DetailMono")
        self.path_value.setProperty("muted", "true")
        self.path_value.setWordWrap(True)
        block.addLayout(grid)
        layout.addWidget(self.entry_block)

        # The header, read when asked
        self.header_block = QWidget()
        block = QVBoxLayout(self.header_block)
        block.setContentsMargins(0, 0, 0, 0)
        block.setSpacing(8)
        block.addWidget(rule())
        heading, self.header_state = kicker_row("Header")
        self.full_link = SmallLink("Full header…", self.fullHeaderRequested.emit)
        heading.layout().addWidget(self.full_link)
        block.addWidget(heading)
        self.read_button = QPushButton("Read header")
        self.read_button.setAutoDefault(False)
        self.read_button.setCursor(Qt.PointingHandCursor)
        self.read_button.clicked.connect(self.readRequested.emit)
        button_row = QHBoxLayout()
        button_row.addWidget(self.read_button)
        button_row.addStretch()
        block.addLayout(button_row)
        self.note = QLabel("Fetches only the first few KB. Results are kept for this session.")
        self.note.setObjectName("Hint")
        self.note.setWordWrap(True)
        block.addWidget(self.note)
        self.header_error = QLabel()
        self.header_error.setObjectName("Hint")
        self.header_error.setWordWrap(True)
        block.addWidget(self.header_error)
        self.header_grid = QGridLayout()
        self.header_grid.setHorizontalSpacing(10)
        self.header_grid.setVerticalSpacing(6)
        self.header_grid.setColumnMinimumWidth(0, self.LABEL_WIDTH)
        self.header_grid.setColumnStretch(1, 1)
        block.addLayout(self.header_grid)
        layout.addWidget(self.header_block)
        layout.addStretch()

        self.auto_switch = QCheckBox("Read headers automatically")
        self.auto_switch.setObjectName("Switch")
        self.auto_switch.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self.auto_switch)
        self.show_nothing()

    def _add_row(self, grid, name, value, role):
        row = grid.rowCount()
        label = QLabel(name)
        label.setObjectName("Hint")
        grid.addWidget(label, row, 0, Qt.AlignLeft | Qt.AlignTop)
        if role:
            value.setObjectName(role)
        grid.addWidget(value, row, 1, Qt.AlignTop)
        self._rows[name] = (label, value)
        return value

    def _show_row(self, name, shown):
        for widget in self._rows[name]:
            widget.setVisible(shown)

    def show_nothing(self, text="Select a file to see its size, date and header."):
        self.empty_label.setText(text)
        self.empty_label.show()
        self.entry_block.hide()
        self.header_block.hide()

    def show_entry(self, entry, kind, cached, folder):
        """``entry`` (of ``kind``) from its listing; ``cached``: whether a
        copy is in the cache (None: not a thing that is fetched)."""
        self.empty_label.hide()
        self.entry_block.show()
        self.name_label.setText(breakable(entry.name))
        self.name_label.setToolTip(entry.name)
        self.type_label.setText(type_text(entry, kind))
        known = has_size(entry, kind)
        self._show_row("Size", known)
        self.size_value.setText(size_text(entry.size) if known else "")
        self.modified_value.setText(full_time(entry.mtime))
        self._show_row("Cache", cached is not None)
        if cached is not None:
            ratio = self.devicePixelRatioF()
            self.cache_check.setPixmap(
                pixmap("check", theme_manager.palette["status_on"], 10, ratio) if cached else QPixmap()
            )
            self.cache_check.setVisible(bool(cached))
            self.cache_text.setText("Cached" if cached else "Not cached")
        separator = "\\" if "\\" in folder and "/" not in folder else "/"
        shown = folder if folder.endswith(separator) else folder + separator
        self.path_value.setText(breakable(shown))
        self.path_value.setToolTip(shown)

    def show_header(self, state, rows=(), error=""):
        """The Header block: ``state`` "hidden" (not a FITS file), "unread",
        "reading", "read" (with ``rows`` from header_rows) or "failed"
        (with ``error``)."""
        self.header_block.setVisible(state != "hidden")
        read = state == "read"
        self.header_state.setText({"unread": "not read", "reading": "reading…", "failed": "not read"}.get(state, ""))
        self.full_link.setVisible(read)
        self.read_button.setVisible(not read)
        self.read_button.setEnabled(state != "reading")
        self.read_button.setText("Try again" if state == "failed" else "Read header")
        self.note.setVisible(state in ("unread", "reading"))
        self.header_error.setVisible(state == "failed")
        self.header_error.setText(f"Couldn’t read the header: {error}" if error else "")
        while self.header_grid.count():
            widget = self.header_grid.takeAt(0).widget()
            if widget is not None:
                widget.hide()  # (now: it is deleted later)
                widget.deleteLater()
        for n, (name, value, mono) in enumerate(rows if read else ()):
            label = QLabel(name)
            label.setObjectName("Hint")
            self.header_grid.addWidget(label, n, 0, Qt.AlignLeft | Qt.AlignTop)
            shown = QLabel(breakable(value) if mono else value)
            shown.setObjectName("DetailMono" if mono else "DetailValue")
            shown.setWordWrap(True)
            shown.setToolTip(value)
            self.header_grid.addWidget(shown, n, 1, Qt.AlignTop)
        if read and not rows:
            empty = QLabel("No telescope, date, frequency, unit or beam in it.")
            empty.setObjectName("Hint")
            empty.setWordWrap(True)
            self.header_grid.addWidget(empty, 0, 0, 1, 2)


class HeaderDialog(QDialog):
    """A file's whole FITS header, one card a line, in monospace."""

    def __init__(self, name, text, parent=None):
        super().__init__(parent)
        from ..ui.geometry import fit_to_screen
        from ..ui.widgets import button_bar

        self.setWindowTitle(f"Header of {name}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        view = QPlainTextEdit()
        view.setObjectName("Console")
        view.setReadOnly(True)
        view.setLineWrapMode(QPlainTextEdit.NoWrap)
        view.setPlainText(header_lines(text))
        layout.addWidget(view, 1)
        box, _buttons = button_bar(secondary=("Close", self.reject))
        layout.addWidget(box)
        fit_to_screen(self, 720, 560)

"""
The solar events viewer's Context Images tab: images of the Sun from
several instruments, in groups that each ask for their images only when
they are on, downloaded a few at a time.
"""

import logging
import re
from datetime import date, datetime, time, timedelta

import numpy as np
from PyQt5.QtWidgets import (
    QBoxLayout,
    QPushButton,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QMenu,
    QScrollArea,
    QSizePolicy,
)
from PyQt5.QtCore import QEvent, QSettings, Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices, QImage, QPainter, QPixmap
from ..solar_context import context_images as ci
from ..solar_context import radio_images as ri
from ..ui.activity import ActivityLabel
from ..ui.download_parts import abandon, abandoned
from ..ui.errors import show_error
from ..ui.styles import CONTROL_HEIGHT, SPACE_M, SPACE_S, SPACE_XS, set_tone
from ..ui.widgets import empty_note, link_button, segmented

from .cache import _noaa_cache, keep
from .widgets import ClickableLabel, ElidedLabel, FullImageViewer, megabytes_text, source_note
from .workers import (
    ImageSaver, MOVIE_NO_STILL, MovieStillLoader, StoppableThread, Stopped, ThumbnailLoader, thumbnail_image,
)
logger = logging.getLogger(__name__)

MAX_CONCURRENT = 4
MOST_LEFT_BEHIND = 2 * MAX_CONCURRENT  # stalled downloads a change of tiles has left: no more start until they end
WAIT_FOR_THREADS_MS = 1000
LEAST_TILE = 140  # of an image to make out what it shows
CLICK_TILE = "Click to view the high-resolution image"
RECENT_DAYS = 30  # archives can still add to a date this recent, so it is asked again each day
GROUP_HINTS = {"viewpoints": "Not the Sun as seen from Earth: each caption says where it was seen from"}
FAR_MINUTES = 60  # an image further than this from the time asked for is flagged
WIDEST_TIME = "00:00 +76m"
IMAGE_CREDITS = {
    "Helioviewer": 'Data source: <a href="https://helioviewer.org/">Helioviewer.org</a> '
    "and the missions named on the tiles",
    "SolarMonitor": 'Data source: <a href="https://solarmonitor.org/">SolarMonitor.org</a>',
    "SunDish": f'<a href="{ri.SUNDISH_PAGE}">SunDish</a> (INAF)',
    "EOVSA": f'<a href="{ri.EOVSA_PAGE}">EOVSA</a> (NJIT)',
    "SRH": f'<a href="{ri.SRH_PAGE}">SRH</a> (ISTP)',
    "OVRO-LWA": f'<a href="{ri.LWA_PAGE}">OVRO-LWA</a>',
}


def span_text(minutes):
    """A length of time in words: ``76 min``, ``3 h 10 min``."""
    minutes = abs(minutes)
    return f"{minutes} min" if minutes < 120 else f"{minutes // 60} h {minutes % 60} min"


def short_span(minutes):
    """A signed length of time for a tile's one line: ``+76m``, ``−3h10``. Any
    longer takes the end off a title, which tells LASCO C2 from C3."""
    sign = "+" if minutes > 0 else "−"
    minutes = abs(minutes)
    return f"{sign}{minutes}m" if minutes < 120 else f"{sign}{minutes // 60}h{minutes % 60:02d}"


def minutes_off(image, near):
    """How far ``image`` is from ``near`` (a datetime or None), 0 if it has no time."""
    moment = ci.image_moment(image)
    if not (moment and near):
        return 0
    end = getattr(image, "image_end", None)
    if isinstance(end, datetime) and moment <= near <= end:
        return 0  # a map made across that time
    return round((moment - near).total_seconds() / 60)


def image_details(image, near=None):
    """What a tile leaves out, a line each."""
    moment = ci.image_moment(image)
    lines = [
        f"Instrument: {image.instrument}",
        image.description,
        f"Credits: {image.credits}",
        f"{moment:%Y-%m-%d %H:%M:%S} UTC" if moment else f"{ci.image_day(image)}",
    ]
    minutes = minutes_off(image, near)
    if minutes:
        lines.append(f"{span_text(minutes)} {'after' if minutes > 0 else 'before'} {near:%H:%M}")
    return lines


def caption_time(image, near=None, noon=False):
    """A tile's time, and whether it is too far from ``near`` (a datetime) to
    pass for it, or in doubt. SolarMonitor's images are dated by their folder
    alone. ``noon``: ``near`` is the list's own time, not one that was asked for."""
    moment = ci.image_moment(image)
    doubted = bool(image.doubt)
    if moment is None:
        return image.span or str(ci.image_day(image)), doubted
    minutes = minutes_off(image, near)
    far = abs(minutes) > FAR_MINUTES
    if image.span:
        return image.span, far or doubted
    if not minutes or (noon and not far):  # at noon a time is only said to be off when it is
        return f"{moment:%H:%M}", doubted
    return f"{moment:%H:%M} {short_span(minutes)}", far or doubted


def left_out_text(left_out, not_yet=()):
    """The line naming the images that are not shown, by the date they have,
    and the instruments not yet at work on the date."""
    by_date = {}
    for image in left_out:
        by_date.setdefault(_nearest(image), []).append(image.title)
    for title, first in not_yet:
        by_date.setdefault(f"from {first}", []).append(title)
    return "Not shown: " + " · ".join(f"{', '.join(titles)} ({date})" for date, titles in by_date.items())


def _nearest(image):
    day = ci.image_day(image)
    return f"nearest {day}" if day else "date not known"


def tile_title(image):
    """A tile's title, with where it was seen from if not from Earth: the
    side and angle tell that, and the heading and the tooltip say the rest."""
    if image.view:
        return f"{image.title} · {image.view.removesuffix(' of Earth')}"
    return f"{image.title} · not from Earth" if image.title in ci.OTHER_VIEW_TITLES else image.title


def thumbnail_key(image, when):
    """What ``image``'s thumbnail is cached under: the noon list's have the
    title alone, and what does not change with the time has a key of its own."""
    if image.key:
        return image.key
    return f"{image.title} {when:%H%M}" if when else image.title


def load_image_groups():
    settings = QSettings("SolarViewer", "SolarViewer")
    saved = settings.value("solar_activity/image_groups", None)
    if saved is None:
        return set(ci.USUAL_GROUPS)
    return {key for key, _name in ci.GROUPS if key in saved.split(",")}


def save_image_groups(keys):
    QSettings("SolarViewer", "SolarViewer").setValue("solar_activity/image_groups", ",".join(sorted(keys)))


def all_eovsa_bands():
    return QSettings("SolarViewer", "SolarViewer").value("solar_activity/eovsa_all_bands", "0") == "1"


def save_all_eovsa_bands(on):
    QSettings("SolarViewer", "SolarViewer").setValue("solar_activity/eovsa_all_bands", "1" if on else "0")


def eovsa_artefacts(image):
    """Whether an EOVSA map's QImage looks like artefacts, not the Sun."""
    side = ri.EOVSA_SIDE
    image = image.scaled(side, side, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).convertToFormat(QImage.Format_RGB32)
    pixels = image.constBits()
    pixels.setsize(image.byteCount())
    array = np.frombuffer(pixels, np.uint8).reshape(side, image.bytesPerLine() // 4, 4)
    return ri.fills_the_sky(array[:, :side, :3].mean(axis=2))


def squared(image):
    """``image`` centred on black, so that a square tile never stretches it."""
    if image.width() == image.height():
        return image
    side = max(image.width(), image.height())
    square = QImage(side, side, QImage.Format_RGB32)
    square.fill(Qt.black)
    painter = QPainter(square)
    painter.drawImage((side - image.width()) // 2, (side - image.height()) // 2, image)
    painter.end()
    return square


class ImagesNearTime(StoppableThread):
    """Looks up the day's images nearest to a time of day (a dozen requests)."""

    found = pyqtSignal(object)
    failed = pyqtSignal(str)
    looked_up = pyqtSignal(int, int)  # images so far, of this many

    def __init__(self, day, when):
        super().__init__()
        self.day = day
        self.when = when

    def run(self):
        try:
            images = ci.fetch_context_images(self.day, progress=self._looked_up, when=self.when)
            self.check_stop()
            self.found.emit(images)
        except Stopped:
            pass
        except Exception as e:
            self.failed.emit(str(e))

    def _looked_up(self, done, total):
        self.check_stop()
        self.looked_up.emit(done, total)


class MoreImages(StoppableThread):
    """Looks up the layers of the groups that are on, a request or two each."""

    found = pyqtSignal(object, object, object)  # day, time (None for noon), images
    failed = pyqtSignal(str)

    def __init__(self, day, when, layers):
        super().__init__()
        self.day = day
        self.when = when
        self.layers = layers

    def run(self):
        try:
            images = ci.fetch_from_helioviewer(
                self.day, lambda _done, _total: self.check_stop(), self.when or ci.NOON, self.layers
            )
            self.check_stop()
            self.found.emit(self.day, self.when, images)
        except Stopped:
            pass
        except Exception as e:
            self.failed.emit(str(e))


def sundish_index():
    """The index, asked for at most daily, and a sentence if it is not fresh.
    The last good copy stands in while the archive does not answer."""
    saved = _noaa_cache.load_sundish_index()
    if saved and datetime.now() - saved["fetched"] < timedelta(days=ri.INDEX_DAYS):
        return saved["rows"], ""
    try:
        rows, refused = ri.parse_sundish_index(ri.fetch_sundish_index())
    except ri.RadioProblem as e:
        if saved is None:
            raise
        return saved["rows"], f"SunDish index from {saved['fetched']:%Y-%m-%d}: {e}"
    if refused:
        logger.info("SunDish index: %d rows do not agree on their date", refused)
    keep(_noaa_cache.save_sundish_index, rows, refused)
    return rows, ""


class RadioImages(StoppableThread):
    """What the radio archives have for a date, or what stands in the cache:
    a request or two each, off the window's thread. ``found`` carries the date
    it is for, and the list is only saved under that date."""

    found = pyqtSignal(object, object, object)  # day, images, notes

    def __init__(self, day, only_cached=False):
        super().__init__()
        self.day = day
        self.only_cached = only_cached

    def _settled(self, record):
        """Whether what was saved can stand: an old date with no source in
        trouble, or a recent one that was asked today."""
        if record is None or record["notes"].get("trouble"):
            return False
        old = date.today() - self.day > timedelta(days=RECENT_DAYS)
        return old or record.get("asked") == date.today().isoformat()

    def run(self):
        try:
            record = _noaa_cache.load_radio_images(self.day)
            if self.only_cached or self._settled(record):
                if record is not None:
                    self.found.emit(self.day, record["images"], record["notes"])
                return
            images, notes = self.ask()
            self.check_stop()
            keep(_noaa_cache.save_radio_images, self.day, images, notes)
            self.found.emit(self.day, images, notes)
        except Stopped:
            pass
        except Exception as e:
            logger.warning("Radio images of %s failed: %s", self.day, e)
            self.found.emit(self.day, [], {"said": [f"Could not fetch the radio images: {e}"], "trouble": True})

    def ask(self):
        images, said, notes = [], [], {}

        def source(name, ask):
            """Each source on its own: one in trouble leaves the others."""
            self.check_stop()
            try:
                return ask()
            except ri.RadioProblem as e:
                said.append(f"{name}: {e}")
                notes["trouble"] = True
            except Exception as e:
                logger.warning("%s failed: %s", name, e)
                said.append(f"{name}: could not be read")
                notes["trouble"] = True
            return None

        index = source("SunDish index", sundish_index)
        if index is not None:
            rows, stale = index
            maps, note = ri.sundish_for_date(rows, self.day)
            images += ri.check_sundish_files(maps)
            self.check_stop()
            if note:
                said.append(note)
                nearest = ri.nearest_sundish(rows, self.day)
                notes["nearest_sundish"] = nearest.isoformat() if nearest else ""
            if stale:
                said.append(stale)
                notes["trouble"] = True

        eovsa = source("EOVSA", lambda: ri.fetch_eovsa(self.day))
        if eovsa is not None:
            maps, note, thumbnail = eovsa
            images += maps
            if note:
                said.append(note)
            if thumbnail is not None:
                keep(_noaa_cache.save_thumbnail, self.day, f"EOVSA {ri.EOVSA_USUAL[0]}", thumbnail)

        stamps = source("SRH", lambda: ri.fetch_srh_stamps(self.day))
        if stamps is not None:
            notes["srh_stamps"] = stamps
            if not stamps:
                said.append("No SRH images for this date")

        movie = source("OVRO-LWA", lambda: ri.fetch_lwa_movie(self.day))
        if movie is not None:
            images += movie[0]
            if movie[1]:
                said.append(movie[1])

        notes["said"] = said
        return images, notes


class ImageLabel(ClickableLabel):
    """A tile's image, stretched over the tile: ``squared`` keeps it true.
    ``check`` looks at the image once it is here, where a tile asks for that."""

    def __init__(self):
        super().__init__()
        self.check = None
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setScaledContents(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(CLICK_TILE)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:  # the right one opens the menu
            super().mouseReleaseEvent(event)


class ImageTile(QWidget):
    """An image and, under it, its title and time. They share a line where
    they fit, and the time goes under the title where not: the end of a title
    is what tells one image from another. The tooltip has it all."""

    def __init__(self, title, when, far, details):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_XS)

        well = QFrame()
        well.setObjectName("ImageWell")
        well_layout = QVBoxLayout(well)
        well_layout.setContentsMargins(0, 0, 0, 0)
        self.label = ImageLabel()
        well_layout.addWidget(self.label)
        layout.addWidget(well, 1)

        self.caption = QWidget()
        self.caption.setToolTip(details)
        self.caption_layout = QBoxLayout(QBoxLayout.LeftToRight, self.caption)
        self.caption_layout.setContentsMargins(0, 0, 0, 0)
        self.caption_layout.setSpacing(SPACE_S)
        self.title = ElidedLabel(title)
        self.title.setToolTip(details)
        self.caption_layout.addWidget(self.title, 1)
        self.time = QLabel(when)
        self.time.setObjectName("DetailMono")
        if far:
            set_tone(self.time, "warning")
        self.caption_layout.addWidget(self.time)
        layout.addWidget(self.caption)

    def check_map(self, image):
        """An EOVSA map, once it has come, can turn out to be no map of the Sun."""
        if eovsa_artefacts(image):
            self.time.setText("artefacts?")
            set_tone(self.time, "warning")
            details = f"{ri.EOVSA_ARTEFACTS}\n{self.caption.toolTip()}"
            self.caption.setToolTip(details)
            self.title.setToolTip(details)
            self.label.setToolTip(ri.EOVSA_ARTEFACTS)

    def one_line_width(self):
        """What the title needs beside a time, whichever time it is: the same
        images at another time keep their grid."""
        self.title.ensurePolished()
        self.time.ensurePolished()
        title = self.title.fontMetrics().horizontalAdvance(self.title.text())
        return title + SPACE_S + self.time.fontMetrics().horizontalAdvance(WIDEST_TIME)

    def two_lines(self, on):
        self.caption_layout.setDirection(QBoxLayout.TopToBottom if on else QBoxLayout.LeftToRight)
        self.caption_layout.setSpacing(0 if on else SPACE_S)
        self.caption_layout.setStretchFactor(self.title, 0 if on else 1)


class GroupHeading(QWidget):
    """A group's name over its tiles, with what it has to say on one line:
    the line gives way, and the tooltip has it all."""

    def __init__(self, name, said=(), links=()):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_S)
        self.name = QLabel(name)
        self.name.setObjectName("SectionTitle")
        layout.addWidget(self.name)
        self.hint = ElidedLabel(" · ".join(said))
        self.hint.setObjectName("Hint")
        self.hint.setToolTip("\n".join(said))
        layout.addWidget(self.hint, 1)
        for button in links:
            layout.addWidget(button)


class TileGrid(QWidget):
    """As many columns of square tiles as fit the width, in ``area``, under
    the heading of each group."""

    def __init__(self, area):
        super().__init__()
        self.area = area
        self.tiles = []
        self.groups = []  # (heading or None, tiles)
        area.installEventFilter(self)  # the fit reads its size, which can change while this one's does not

    def set_tiles(self, tiles):
        self.set_groups([(None, tiles)])

    def set_groups(self, groups):
        for heading, tiles in self.groups:
            for widget in tiles + ([heading] if heading else []):
                widget.hide()
                widget.deleteLater()
        self.groups = groups
        self.tiles = [tile for _heading, tiles in groups for tile in tiles]
        for heading, tiles in groups:
            for widget in tiles + ([heading] if heading else []):
                widget.setParent(self)
                widget.show()
        self._place()

    def _heading_height(self):
        return max((heading.sizeHint().height() for heading, _tiles in self.groups if heading), default=0)

    def _height(self, columns, tile):
        """All the groups, at ``tile`` high in ``columns``."""
        heading = self._heading_height()
        total = 0
        for index, (title, tiles) in enumerate(self.groups):
            rows = (len(tiles) + columns - 1) // columns
            total += SPACE_M if index else 0
            total += (heading + SPACE_S) if title else 0
            total += rows * tile + max(rows - 1, 0) * SPACE_M
        return total

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Resize:
            self._place()
        return False

    def _caption(self, side):
        """The height of the captions at ``side``. All the tiles alike, which
        is all the rows alike: the list decides it, not a time's glyphs."""
        line = self.tiles[0].title.sizeHint().height()  # (a time's height changes with its glyphs)
        return line if all(tile.one_line_width() <= side for tile in self.tiles) else 2 * line

    def _fit(self, width, height):
        """The columns and side of the tiles: the largest that show them all at
        once, or, if none do, the smallest that can be made out."""
        count = len(self.tiles)
        side_of = lambda columns: (width - SPACE_M * (columns - 1)) // columns
        for columns in range(1, count + 1):
            side = side_of(columns)
            if side >= LEAST_TILE and self._height(columns, side + SPACE_XS + self._caption(side)) <= height:
                # (no tile alone on a row, if they can all share one)
                narrower = side_of(count)
                if (
                    len(self.groups) == 1 and count == columns + 1 and narrower >= LEAST_TILE
                    and self._height(count, narrower + SPACE_XS + self._caption(narrower)) <= height
                ):
                    columns = count
                return columns, side_of(columns)
        # A busy date scrolls at about the size a less busy one fits at: the
        # tiles do not jump in size from one date to the next
        columns = max(1, (width + SPACE_M) // (LEAST_TILE + SPACE_M))
        return columns, side_of(columns)

    def _place(self):
        if not self.tiles:
            self.setMinimumHeight(0)
            return
        # The bar's room stays set aside whether it shows or not: taller
        # tiles could bring it up, and the narrower ones send it away again
        width = self.area.width() - self.area.verticalScrollBar().sizeHint().width()
        columns, side = self._fit(width, self.area.height())
        caption = self._caption(side)
        two_lines = caption > self.tiles[0].title.sizeHint().height()
        height = side + SPACE_XS + caption
        heading_height = self._heading_height()
        top = 0
        for title, tiles in self.groups:
            if title:
                title.setGeometry(0, top, width, heading_height)
                top += heading_height + SPACE_S
            for index, tile in enumerate(tiles):
                row, column = divmod(index, columns)
                tile.two_lines(two_lines)
                tile.caption.setFixedHeight(caption)
                tile.setGeometry(column * (side + SPACE_M), top + row * (height + SPACE_M), side, height)
            top += (len(tiles) + columns - 1) // columns * (height + SPACE_M)
        self.setMinimumHeight(self._height(columns, height))


class ContextImagesTabMixin:
    """Builds the Context Images tab and downloads its images.

    Mixed into :class:`NOAAEventsViewer`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``begin_job``, ``day_axis``, ``download_queue``, ``end_job``, ``go_to``,
    ``image_downloads``, ``image_savers``, ``image_viewers``, ``say``,
    ``shown_date``, ``tabs``, ``update_job``
    """

    def _create_context_images_tab(self):
        """Context Images tab."""
        self.images_lookup = None  # the look-up of the images at the cursor
        self.day_images = None  # the list fetched for the date, as it came
        self.images_at = {}  # (date, time) -> the list looked up, for the session
        self.images_shown = []  # the images with a tile
        self.images_when = None  # the time they were looked up for, None for noon
        self.image_groups = load_image_groups()  # the groups that are on
        self.radio_worker = None
        self.radio_day = None  # the date of what the radio archives said
        self.radio_images = []
        self.radio_notes = {}
        self.more_lookup = None  # the look-up of the layers of a group
        self.layers_asked = set()  # (date, time, title), once a session each
        self._queue_timer = QTimer(self)
        self._queue_timer.setSingleShot(True)
        self._queue_timer.setInterval(WAIT_FOR_THREADS_MS)
        self._queue_timer.timeout.connect(self._process_download_queue)

        images_tab = QWidget()
        images_layout = QVBoxLayout(images_tab)
        images_layout.setContentsMargins(0, SPACE_M, 0, 0)
        images_layout.setSpacing(SPACE_S)

        self.group_buttons = {}
        for key, name in ci.GROUPS:
            button = QPushButton(name)
            button.setCheckable(True)
            button.setChecked(key in self.image_groups)
            button.setToolTip(self._group_tip(key))
            button.clicked.connect(lambda _on, k=key: self._group_toggled(k))
            self.group_buttons[key] = button
        chooser = QHBoxLayout()
        chooser.addWidget(segmented(list(self.group_buttons.values())))
        chooser.addStretch()
        images_layout.addLayout(chooser)

        # Set to the buttons' height, so that they do not move the tiles
        self.images_header = QWidget()
        self.images_header.setMinimumHeight(CONTROL_HEIGHT)
        header_layout = QHBoxLayout(self.images_header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(SPACE_S)
        self.images_status = ActivityLabel()
        self.images_status.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        header_layout.addWidget(self.images_status)
        self.images_near = QLabel()
        self.images_near.setObjectName("Hint")
        header_layout.addWidget(self.images_near)
        header_layout.addStretch()
        self.images_noon_btn = link_button("", self.display_day_images)
        header_layout.addWidget(self.images_noon_btn)
        self.images_cursor_btn = link_button("", self.show_images_at_cursor)
        header_layout.addWidget(self.images_cursor_btn)
        images_layout.addWidget(self.images_header)

        self.images_empty = empty_note("")
        images_layout.addWidget(self.images_empty, 1)

        self.images_scroll = QScrollArea()
        self.images_scroll.setWidgetResizable(True)
        self.images_scroll.setFrameShape(QFrame.NoFrame)
        self.images_grid = TileGrid(self.images_scroll)
        self.images_scroll.setWidget(self.images_grid)
        images_layout.addWidget(self.images_scroll, 1)

        # One line, whatever a list shows here: more would take height from
        # the tiles and re-tile them
        footer = QHBoxLayout()
        footer.setSpacing(SPACE_M)
        self.images_credits = {name: source_note(html) for name, html in IMAGE_CREDITS.items()}
        for note in self.images_credits.values():
            footer.addWidget(note)
        self.images_note = ElidedLabel()
        self.images_note.setObjectName("Hint")
        self.images_note.hide()
        footer.addWidget(self.images_note, 1)
        images_layout.addLayout(footer)

        self.tabs.addTab(images_tab, "Context images")
        self.day_axis.cursor_changed.connect(self._show_image_times)
        self.clear_context_images()

    @staticmethod
    def _group_tip(key):
        if key == "radio":
            return "SunDish, EOVSA, the Siberian Radioheliograph and OVRO-LWA"
        return ", ".join(ci.GROUP_MEMBERS[key])

    def _group_toggled(self, key):
        """A group on or off: its images are only looked up when it is on."""
        self.day_band.setFocus()  # the button stays, and the arrow keys stay on the cursor
        self.image_groups = {k for k, button in self.group_buttons.items() if button.isChecked()}
        save_image_groups(self.image_groups)
        if self.day_images is not None:
            self._show_current_list()
            self._ask_groups()

    def _show_current_list(self):
        when = self.images_when
        images = self.images_at.get((self.shown_date, when)) if when else self.day_images
        self.display_context_images(images, when)

    def clear_context_images(self):
        """Nothing fetched for the date: no tiles, nothing left of another date."""
        self.day_images = None
        self.leave_image_lookups()
        self._show_tiles([], [], None, "Fetch a date to see its context images")

    def leave_image_lookups(self):
        """Forget what the groups were asking for (the date has gone, or the window)."""
        self._leave_radio()
        self._leave_more_lookup()

    def _leave_radio(self):
        worker, self.radio_worker = self.radio_worker, None
        if worker is not None:
            abandon(worker)
            self.end_job("radio_images")

    def _leave_more_lookup(self):
        lookup, self.more_lookup = self.more_lookup, None
        if lookup is not None:
            abandon(lookup)
            self.end_job("images_more")

    def _ask_groups(self, only_cached=False):
        """Ask for what the groups that are on have yet to show."""
        day = self.shown_date
        again = not only_cached and self.radio_notes.get("trouble")  # a Fetch tries once more
        if "radio" in self.image_groups and (self.radio_day != day or again):
            self._ask_radio(day, only_cached)
        if not only_cached:
            self._ask_more_layers(day)

    def _ask_radio(self, day, only_cached):
        if self.radio_worker is not None:
            if self.radio_worker.day == day and not self.radio_worker.only_cached:
                return
            self._leave_radio()
        self.radio_worker = RadioImages(day, only_cached)
        self.radio_worker.found.connect(self._on_radio_found)
        self.radio_worker.finished.connect(lambda worker=self.radio_worker: self._on_radio_ended(worker))
        if not only_cached:
            self.begin_job("radio_images", "Finding the radio images…", self._cancel_radio)
        self.radio_worker.start()

    def _on_radio_ended(self, worker):
        if self.radio_worker is worker:
            self._leave_radio()
            if self.day_images is not None:
                self._show_current_list()  # it found nothing to say: nor can the tiles wait

    def _cancel_radio(self):
        self._leave_radio()
        if self.day_images is not None:
            self._show_current_list()

    def _on_radio_found(self, day, images, notes):
        self._leave_radio()
        if day != self.shown_date:
            return  # the date has changed since
        self.radio_day = day
        self.radio_images = images
        self.radio_notes = notes
        if self.day_images is not None:
            self._show_current_list()

    def _radio_tiles(self, when):
        """The radio images of the date to show at ``when``: dated by their
        own archive, so a look-up at a time leaves them, but for SRH."""
        if self.radio_day != self.shown_date:
            return []
        all_bands = all_eovsa_bands()
        usual = {f"EOVSA {band}" for band in ri.EOVSA_USUAL}
        shown = [i for i in self.radio_images if all_bands or not i.key.startswith("EOVSA") or i.key in usual]
        snapshot = ri.srh_image(self.shown_date, self.radio_notes.get("srh_stamps") or [], when)
        if snapshot is not None:
            movies = [i for i in shown if i.link]
            shown = [i for i in shown if not i.link] + [snapshot] + movies
        return shown

    def _ask_more_layers(self, day):
        when = self.images_when
        if self.more_lookup is not None:
            if (self.more_lookup.day, self.more_lookup.when) == (day, when):
                return
            self._leave_more_lookup()
        have = {image.title for image in (self.images_at.get((day, when)) if when else self.day_images) or []}
        layers = [
            entry for entry in ci.layers_of(self.image_groups)
            if entry[0] not in have
            and (day, when, entry[0]) not in self.layers_asked
            and day >= ci.FIRST_DATES.get(entry[0], day)
        ]
        if not layers:
            return
        self.more_lookup = MoreImages(day, when, layers)
        self.more_lookup.found.connect(self._on_more_found)
        self.more_lookup.failed.connect(lambda _message: self._leave_more_lookup())
        self.more_lookup.finished.connect(lambda lookup=self.more_lookup: self._on_more_ended(lookup))
        self.begin_job("images_more", f"Finding {len(layers)} more images…", self._leave_more_lookup)
        self.more_lookup.start()

    def _on_more_ended(self, lookup):
        if self.more_lookup is lookup:
            self._leave_more_lookup()

    def _on_more_found(self, day, when, images):
        self._leave_more_lookup()
        if day != self.shown_date:
            return  # the date has changed since
        # A layer that could not be looked up has no date, and is asked for again
        unknown = [image.title for image in images if ci.image_day(image) is None]
        images = [image for image in images if ci.image_day(image) is not None]
        if unknown:
            self.say(f"Could not reach Helioviewer for {', '.join(unknown)}. Press Fetch to ask again.")
        self.layers_asked |= {(day, when, image.title) for image in images}
        if when:
            self.images_at[day, when] = (self.images_at.get((day, when)) or []) + images
        else:
            self.day_images = (self.day_images or []) + images
            keep(_noaa_cache.save_images, day, self.day_images)
        if when == self.images_when:
            self._show_current_list()

    def display_context_images(self, images, when=None):
        """Show the images of the date in ``images``, the list fetched for it
        or, with ``when`` (a time of day), the one looked up for that time.
        This is the one way in: only here does an image get a tile."""
        if when is None:
            self.day_images = images
        day = self.shown_date
        kept, left_out = ci.images_of_date(images, day)
        kept = [image for image in kept if self._group_on(image)]
        left_out = [image for image in left_out if self._group_on(image)]
        kept += ci.images_of_date(self._radio_tiles(when) if "radio" in self.image_groups else [], day)[0]
        if not self.image_groups:
            empty = "Choose a group of images above"
        elif self.image_groups == {"radio"} and self.radio_worker is not None:
            empty = f"Finding the radio images of {day}…"
        elif self.image_groups == {"radio"} and self.radio_day != day:
            empty = f"The radio images of {day} are not fetched. Press Fetch to load them"
        elif self.image_groups == {"radio"}:
            empty = f"No radio images from {day}"
        elif any(ci.image_day(image) for image in left_out):
            empty = f"No context images from {day}"
        elif left_out:
            empty = f"The dates of the images for {day} could not be confirmed. Check the connection and press Fetch."
        else:
            empty = f"Could not fetch the context images for {day}. Check the connection and press Fetch."
        self._show_tiles(kept, left_out, when, empty)

    def _group_on(self, image):
        """Whether ``image`` belongs to a group that is on, or to none."""
        group = ci.group_of(image)
        return not group or group in self.image_groups

    def display_fetched_images(self, images, fetched=True):
        """The list fetched for the date, or the one at the cursor if it is
        here: stepping days compares the same clock time. Not ``fetched`` is
        a date stepped through, which asks no archive of its own."""
        self.day_images = images
        self._leave_more_lookup()
        if fetched:  # a fresh list has only the essentials: what the last one gained is asked for again
            self.layers_asked = {asked for asked in self.layers_asked if asked[:2] != (self.shown_date, None)}
        self._ask_groups(only_cached=not fetched)  # first: the tiles say that they are coming
        self._show_fetched(images)

    def _show_fetched(self, images):
        when = self._cursor_time()
        if (self.shown_date, when) in self.images_at:
            self.display_context_images(self.images_at[self.shown_date, when], when)
        else:
            self.display_context_images(images)

    def display_day_images(self):
        self.day_band.setFocus()  # the button goes, and the arrow keys stay on the cursor
        self.display_context_images(self.day_images)

    def _show_tiles(self, images, left_out, when, empty_text):
        self.leave_image_downloads()
        self.images_loaded = self.images_failed = 0
        self.images_total = len(images)
        self.images_shown = images
        self.images_when = when
        self.images_status.stop()
        self.images_status.setVisible(bool(images))
        not_yet = ci.not_started(self.shown_date, self.image_groups) if self.shown_date else []
        self.images_note.setText(left_out_text(left_out, not_yet))
        self.images_note.setToolTip(self.images_note.text().replace(" · ", "\n"))
        self.images_note.setVisible(bool(left_out or not_yet))
        said = self._radio_said()[0]
        if said and "radio" in self.image_groups:
            empty_text = f"{empty_text}\n{' · '.join(said)}"
        self.images_empty.setText(empty_text)
        self.images_empty.setVisible(not images)
        self.images_scroll.setVisible(bool(images))

        near = datetime.combine(self.shown_date, when or ci.NOON) if self.shown_date else None
        tiles = []
        for img in images:
            caption = caption_time(img, near, noon=when is None)
            tile = ImageTile(tile_title(img), *caption, "\n".join(image_details(img, near)))
            if img.link:
                tile.label.setToolTip("Click to open the movie")
            if img.key.startswith("EOVSA"):
                tile.label.check = tile.check_map
            tile.label.clicked.connect(lambda i=img: self._open_image(i))
            tile.setContextMenuPolicy(Qt.CustomContextMenu)
            tile.customContextMenuRequested.connect(
                lambda pos, t=tile, i=img: self._show_tile_menu(t, pos, i)
            )
            tiles.append(tile)
            key = thumbnail_key(img, when)
            # On disk it shows at once: a thread for each would blank the tiles
            image = self._cached_thumbnail(key)
            if image is not None:
                self._show_tile_image(tile.label, image)
                self.images_loaded += 1
            else:
                tile.label.start_loading("Waiting…")
                self.download_queue.append((img.thumb_url, tile.label, img.page_url, key, self.shown_date))
        self._set_grid(images, tiles)

        credits = {img.credits for img in images} or {"Helioviewer"}
        for name, note in self.images_credits.items():
            note.setVisible(name in credits)

        self._show_image_times()
        if images:
            self._show_images_progress()
            self._process_download_queue()

    def _cached_thumbnail(self, key):
        """A file that cannot be read is no thumbnail: it is fetched again."""
        cached = _noaa_cache.load_thumbnail(self.shown_date, key)
        try:
            return thumbnail_image(cached.read_bytes()) if cached else None
        except OSError as e:
            logger.info("Could not read the thumbnail %s: %s", cached, e)
            return None

    def _set_grid(self, images, tiles):
        """The tiles of ``images`` under their groups, in the groups' order and
        each in its place in the group. Images in no group come last, with no
        heading."""
        members = {}
        for image, tile in sorted(zip(images, tiles), key=lambda pair: ci.place_in_group(pair[0])):
            members.setdefault(ci.group_of(image), []).append(tile)
        if "radio" in self.image_groups and self._radio_said()[0]:
            members.setdefault("radio", [])  # what it has to say, even with no image to show
        groups = [(GroupHeading(name, *self._group_says(key)), members[key]) for key, name in ci.GROUPS if key in members]
        if "" in members:
            groups.append((None, members[""]))
        self.images_grid.set_groups(groups)

    def _group_says(self, key):
        """What a group's heading says, and its links."""
        if key != "radio":
            return [GROUP_HINTS[key]] if key in GROUP_HINTS else [], []
        said, notes = self._radio_said()
        links = []
        nearest = notes.get("nearest_sundish")
        if nearest:
            links.append(link_button(f"Nearest SunDish map: {nearest}", lambda: self._go_to_map(nearest)))
        if self.radio_day == self.shown_date and any(i.key.startswith("EOVSA") for i in self.radio_images):
            words = f"Usual {len(ri.EOVSA_USUAL)} EOVSA bands" if all_eovsa_bands() else f"All {len(ri.EOVSA_BANDS)} EOVSA bands"
            links.append(link_button(words, self._toggle_eovsa_bands))
        return said, links

    def _go_to_map(self, day):
        """To another date: a step, so its data is asked for here."""
        if self.go_to(date.fromisoformat(day)):
            self.fetch_data()

    def _radio_said(self):
        """What the radio archives said of the date on show, and all they noted."""
        notes = self.radio_notes if self.radio_day == self.shown_date else {}
        return notes.get("said") or [], notes

    def _toggle_eovsa_bands(self):
        save_all_eovsa_bands(not all_eovsa_bands())
        self.day_band.setFocus()
        self._show_current_list()

    def _open_image(self, img):
        """A tile's click: the large view, or a movie in the browser."""
        if img.link:
            QDesktopServices.openUrl(QUrl(img.link))
        else:
            self.show_high_res_image(img)

    def _show_tile_menu(self, tile, pos, img):
        menu = QMenu(tile)
        if img.link:
            menu.addAction("Open the movie", lambda: self._open_image(img))
        else:
            menu.addAction("View high resolution", lambda: self.show_high_res_image(img))
            menu.addAction("Save high-res…", lambda: self.save_high_res_image(img))
        if img.source_page:
            menu.addAction("Open source page", lambda: QDesktopServices.openUrl(QUrl(img.source_page)))
        menu.exec_(tile.mapToGlobal(pos))

    def _cursor_time(self):
        """The cursor as a time of day, to the minute, or None."""
        if self.day_axis.cursor is None:
            return None
        hours, minutes = divmod(min(round(self.day_axis.cursor * 60), 1439), 60)
        return time(hours, minutes)

    def _show_image_times(self):
        """What the tiles are near, and how to get those of another time."""
        when = self.images_when or ci.NOON
        cursor = self._cursor_time()
        text = ""
        if any(ci.image_moment(image) for image in self.images_shown):
            text = f"near {when:%H:%M} UTC"
            if cursor is not None and cursor != when:
                minutes = (cursor.hour - when.hour) * 60 + cursor.minute - when.minute
                text += f", {span_text(minutes)} {'before' if minutes > 0 else 'after'} the cursor"
        elif any(image.credits == "SolarMonitor" for image in self.images_shown):
            text = "SolarMonitor gives the date, not the time"
        self.images_near.setText(text)

        self.images_noon_btn.setText(f"Show images at {ci.NOON:%H:%M}")
        self.images_noon_btn.setVisible(self.images_when is not None)
        # (the noon list has a button of its own)
        offer = self.day_images is not None and cursor is not None and cursor not in (when, ci.NOON)
        self.images_cursor_btn.setVisible(offer)
        if offer:
            self.images_cursor_btn.setText(f"Show images at {cursor:%H:%M}")
            self.images_cursor_btn.setEnabled(self.images_lookup is None)

    def show_images_at_cursor(self):
        """The images nearest the cursor. A dozen requests, so only when asked."""
        when = self._cursor_time()
        day = self.shown_date
        if when is None or self.images_lookup is not None:
            return
        self.day_band.setFocus()  # the button goes, and the arrow keys stay on the cursor
        if (day, when) in self.images_at:
            self.display_context_images(self.images_at[day, when], when)
            self._ask_more_layers(day)
            return
        label = f"Finding the images at {when:%H:%M}…"
        self.images_lookup = ImagesNearTime(day, when)
        self.images_lookup.looked_up.connect(
            lambda done, total: self.update_job("images_at", f"{label} {done} of {total}")
        )
        self.images_lookup.found.connect(lambda images: self._on_images_found(day, when, images))
        self.images_lookup.failed.connect(self._on_images_lookup_failed)
        self.begin_job("images_at", label, self._leave_images_lookup)
        self.images_lookup.start()
        self._show_image_times()

    def _leave_images_lookup(self):
        """The look-up is over or no longer wanted; a cancel gets here too."""
        lookup, self.images_lookup = self.images_lookup, None
        if lookup is not None:
            abandon(lookup)
            self.end_job("images_at")
            self._show_image_times()

    def _on_images_found(self, day, when, images):
        self._leave_images_lookup()
        if not ci.images_of_date(images, day)[0]:
            # (not kept: this may only be the connection)
            self.say(f"No images found near {when:%H:%M} on {day}. Check the connection and try again.")
            return
        self.images_at[day, when] = images
        self.display_context_images(images, when)
        self._ask_more_layers(day)

    def _on_images_lookup_failed(self, message):
        self._leave_images_lookup()
        show_error(self, "Could not find the images", "Check the connection and try again.", message)

    def leave_image_downloads(self):
        """Forget the images under way and waiting (the labels they were for
        are going, or the window is)."""
        self._leave_images_lookup()
        for loader in self.image_downloads.values():
            abandon(loader)
        self.image_downloads = {}  # Active ones
        self.download_queue = []  # Waiting ones
        self._queue_timer.stop()

    def _show_images_progress(self):
        done = self.images_loaded + self.images_failed
        if done < self.images_total:
            self.images_status.start(f"Loading images… {done} of {self.images_total}")
        elif self.images_failed:
            self.images_status.stop(f"{self.images_loaded} of {self.images_total} images loaded")
        else:
            self.images_status.stop(f"{self.images_total} images loaded")

    def show_high_res_image(self, img_obj):
        """Open dialog to show full resolution image."""
        # Use None as parent to make it an independent window
        viewer = FullImageViewer(
            None, img_obj.title, img_obj.page_url, save=lambda: self.save_high_res_image(img_obj),
            event_date=ci.image_day(img_obj) or self.shown_date,
        )
        details = QLabel(" · ".join(image_details(img_obj)))
        details.setObjectName("Hint")
        details.setWordWrap(True)
        viewer.layout().insertWidget(0, details)
        viewer.setAttribute(Qt.WA_DeleteOnClose)  # Cleanup on close

        # Keep reference to prevent GC
        self.image_viewers.append(viewer)
        viewer.finished.connect(lambda result, v=viewer: self._cleanup_viewer(v))

        viewer.show()  # Non-blocking

    def _cleanup_viewer(self, viewer):
        """Safely remove viewer reference."""
        try:
            if viewer in self.image_viewers:
                self.image_viewers.remove(viewer)
        except RuntimeError:
            pass

    def save_high_res_image(self, img_obj):
        """Save high resolution image as PNG."""
        from PyQt5.QtWidgets import QFileDialog

        # Ask user for save location, in the archive's own format
        stamp = f"{self.shown_date:%Y%m%d}"
        moment = ci.image_moment(img_obj)
        if (self.images_when is not None or img_obj.key) and moment is not None:
            stamp = f"{moment:%Y%m%d_%H%M}"  # one image of several from the date
        suffix, kind = (".jpg", "JPEG Image (*.jpg)") if "ext=jpg" in img_obj.page_url else (".png", "PNG Image (*.png)")
        title = re.sub(r"[^\w.-]+", "_", img_obj.title)  # a title can hold a ?
        default_name = f"{title}_{stamp}{suffix}"

        file_path, _ = QFileDialog.getSaveFileName(self, "Save high-resolution image", default_name, kind)

        if not file_path:
            return  # User cancelled

        # Ensure the extension
        if not file_path.lower().endswith(suffix):
            file_path += suffix
        if file_path in self.image_savers:
            return

        # The window stays free meanwhile: this can take a while
        saver = ImageSaver(img_obj.page_url, file_path, ci.image_day(img_obj) or self.shown_date)
        self.image_savers[file_path] = saver
        label = f"Saving {img_obj.title}…"
        self.begin_job(file_path, label, lambda: self._cancel_save(file_path))
        saver.received.connect(
            lambda done, total: self.update_job(file_path, f"{label} {megabytes_text(done, total)}")
        )
        saver.saved.connect(self._on_image_saved)
        saver.error.connect(lambda message: self._on_save_error(file_path, message))
        saver.start()

    def _leave_saver(self, file_path):
        """Forget the save of ``file_path``; False if it was cancelled. Even a
        saver that has said how it ended is still running, so ``abandon`` it."""
        saver = self.image_savers.pop(file_path, None)
        if saver is None:
            return False
        abandon(saver)
        return True

    def _cancel_save(self, file_path):
        self._leave_saver(file_path)

    def _on_image_saved(self, file_path):
        if not self._leave_saver(file_path):
            return
        self.end_job(file_path)
        self.say(f"Image saved to {file_path}")

    def _on_save_error(self, file_path, message):
        if not self._leave_saver(file_path):
            return
        self.end_job(file_path)
        show_error(self, "Could not save the image", f"The image could not be saved to:\n{file_path}", message)

    def _process_download_queue(self):
        """Start next downloads if under limit. A stalled download cannot be
        stopped, so those left behind count too, however often the tiles change."""
        if self.download_queue and abandoned(ThumbnailLoader) >= MOST_LEFT_BEHIND:
            self._queue_timer.start()
            return
        while len(self.image_downloads) < MAX_CONCURRENT and self.download_queue:
            self._start_download(*self.download_queue.pop(0))

    def _start_download(self, url, label, page_url, title, day):
        try:
            label.start_loading("Loading…")
        except RuntimeError:
            return  # the card has gone
        kind = MovieStillLoader if url.endswith(".mp4") else ThumbnailLoader
        loader = kind(url, page_url, day, title)  # (a queued one can outlast its date)
        loader.ready.connect(lambda image, l=label: self._on_image_loaded(image, l))
        loader.error.connect(lambda err, l=label: self._on_image_error(err, l))

        # Cleanup and process next on finish
        loader.finished.connect(self._on_download_finished)

        # Keep reference
        self.image_downloads[id(loader)] = loader
        loader.start()

    def _on_download_finished(self):
        """Handle download thread finish (cleanup and next)."""
        try:
            sender = self.sender()
            if sender is not None and self.image_downloads.pop(id(sender), None) is None:
                return  # one left behind by a later fetch

            self._show_images_progress()
            self._process_download_queue()
        except RuntimeError:
            pass

    @staticmethod
    def _show_tile_image(label, image):
        label.setPixmap(QPixmap.fromImage(squared(image)))
        if label.check is not None:
            label.check(image)

    def _on_image_loaded(self, image, label):
        """Show a card's image."""
        try:
            label.stop_loading()
            if image is not None:
                self._show_tile_image(label, image)
                self.images_loaded += 1
            else:
                label.setText("Could not read this image")
                self.images_failed += 1
        except RuntimeError:
            # Widget deleted, ignore
            pass

    def _on_image_error(self, error_msg, label):
        """Handle download error."""
        self.images_failed += 1
        try:
            label.stop_loading()
            # shorten error message
            short = (
                "Connection error"
                if "101" in error_msg or "Unreachable" in str(error_msg)
                else "Could not load this image"
            )
            if error_msg.startswith(("Google Drive", "Not shown", "The archive sent", "The connection", MOVIE_NO_STILL)):
                short = error_msg  # already a sentence for the tile
            elif error_msg == "HTTP 404":
                short = "Not in the archive for this date"
            elif error_msg.startswith("HTTP 5"):
                short = f"The archive had a fault ({error_msg})"
            elif error_msg.startswith("HTTP "):
                short = f"The archive refused ({error_msg})"
            label.setText(short)
        except RuntimeError:
            pass

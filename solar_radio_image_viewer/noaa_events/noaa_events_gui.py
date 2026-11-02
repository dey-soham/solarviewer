#!/usr/bin/env python3
"""
Solar Activity Viewer GUI - Comprehensive solar context data display.
"""

import sys
import os
import re
from datetime import datetime, date, timedelta, timezone
from typing import Optional
import logging

# Qt imports
try:
    from PyQt5.QtWidgets import (
        QAbstractItemView,
        QButtonGroup,
        QMainWindow,
        QShortcut,
        QWidget,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QLineEdit,
        QFrame,
        QScrollArea,
        QSizePolicy,
        QMessageBox,
        QTabWidget,
    )
    from PyQt5.QtCore import QSize, Qt, QTimer
    from PyQt5.QtGui import QIcon, QKeySequence
    from PyQt5.QtNetwork import QNetworkAccessManager
except ImportError:
    print("PyQt5 is required. Install with: pip install PyQt5")
    sys.exit(1)

try:
    # Try relative imports (when run as module)
    from . import noaa_events as ne
    from ..ui.styles import SPACE_L, SPACE_M, SPACE_S, theme_manager, set_hand_cursor, themed
except ImportError:
    # Fallback for standalone execution
    # Add project root to path to allow absolute imports
    import sys
    import os

    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(current_dir, "../../"))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from solar_radio_image_viewer.noaa_events import noaa_events as ne
    from solar_radio_image_viewer.ui.styles import SPACE_L, SPACE_M, SPACE_S, theme_manager, set_hand_cursor, themed
from .cache import _noaa_cache
from .conditions_tab import ConditionsTabMixin
from .context_images_tab import ContextImagesTabMixin
from .day_axis import DayAxis, DayBand
from .day_records_view import VIEW_LABELS, DayRecordsMixin, view_switch
from .radio_spectra_tab import RadioSpectraTabMixin
from .widgets import SORT_ROLE, ElidedLabel, EventTable, set_source_note, source_note
from .workers import FetchWorker, FileSaver, GoesDayWorker, saved_early
from ..processing.fits_io import is_fits
from ..processing.image_meta import casa_info, casa_obs_time, fits_header


from ..ui.activity import ActivityLabel
from ..ui.background import Cancelled, run_in_background
from ..ui.busy import busy
from ..ui.date_picker import attach_date_picker
from ..ui.download_parts import abandon
from ..ui.errors import show_error
from ..ui.geometry import fit_to_screen
from ..ui.icons import themed_icon
from ..ui.widgets import link_button, segmented

logger = logging.getLogger(__name__)

DATE_FORMAT = "%Y-%m-%d"
CANCEL_NOTE_MS = 4000  # how long "Cancelled" stays beside the buttons
COMPACT_HEIGHT = 720  # under this window height the day band's GOES lane shrinks
BAD_DATE = "Enter the date as YYYY-MM-DD."
NOT_FETCHED = "Nothing fetched for {day} yet. Press Fetch."
FETCHING_GOES = "Fetching GOES data…"
GOES_NOT_FETCHED = "GOES data not fetched. Press Fetch to load it"
EMPTY_LIST = "the event list has nothing for this date"
NOT_YET = "{day} has not happened yet, so there is nothing to fetch."
FETCH_HOLDS_DATE = "Staying on {day} during the fetch. From image works after it ends."
NO_CMES_YET = "The CMEs of this date have not been fetched yet. Press Fetch."
EVENT_KINDS = {"xray": "X-ray", "optical": "H-alpha", "radio": "Radio", "cme": "CME", "other": "Other"}
FILTERS = ("All", "X-ray", "H-alpha", "Radio", "CME", "Other")
EVENT_COLUMNS = [
    "Time (UTC)", "Peak", "Duration", "Kind", "Class", "Location or frequency", "Region", "Detail", "Observatory",
]
TIME_MARKS = {"B": "<", "A": ">", "U": "~", "E": "~"}
MARKS_NOTE = "< before, > after, ~ uncertain"
CDAW_CREDIT = '<a href="https://cdaw.gsfc.nasa.gov/CME_list/">CDAW LASCO catalogue</a>'
DONKI_CREDIT = '<a href="https://kauai.ccmc.gsfc.nasa.gov/DONKI/">NASA DONKI</a>'
EVENT_LIST_CREDIT = '<a href="https://www.swpc.noaa.gov/products/solar-and-geophysical-event-reports">{source}</a>'
CALLISTO_CREDIT = '<a href="https://soleil.i4ds.ch/solarradio/">e-CALLISTO</a> (C. Monstein)'
NOTE_LENGTH = 60  # of a CME's note in its row; all of it in the tooltip


def _event_credits(events):
    """The Events tab's sources, naming where the list on show came from."""
    source = ne.SOURCE_NAMES.get(getattr(events, "source", ""), ne.SOURCE_NAMES["solarmonitor"])
    return f"Data sources: {EVENT_LIST_CREDIT.format(source=source)}, {CALLISTO_CREDIT}"


def _in_the_future(day):
    return day > datetime.now(timezone.utc).date()


def _hours(text):
    """Hours from 00:00 of a time field (0642, B0937, 13:16), or ``None``."""
    digits = ne._clock(text or "").replace(":", "")
    if len(digits) != 4 or not digits.isdigit():
        return None
    return int(digits[:2]) + int(digits[2:]) / 60


def _clock_hours(when):
    """Hours from 00:00 of a datetime, to the second."""
    return when.hour + when.minute / 60 + when.second / 3600


def _time_text(text):
    """HH:MM with the field's mark as a sign, "—" if there is none."""
    if _hours(text) is None:
        return "—"
    digits = ne._clock(text).replace(":", "")
    return f"{TIME_MARKS.get(text[0], '')}{digits[:2]}:{digits[2:]}"


def _frequency_text(event):
    """The location of an optical flare, the band of an X-ray or radio one."""
    value = event.location_or_freq
    if not value or value.strip("/") == "":
        return "—"
    if event.event_type == "XRA":
        return value.replace("-", "–").replace("A", " Å")
    if event.type_info.get("category") == "radio":
        parts = [p.lstrip("0") or "0" for p in value.split("-")]
        return f"{'–'.join(parts)} MHz"
    return value


def _detail(event):
    """What the event's record says beyond its class."""
    rest = event.particulars.split()[1:]
    if event.event_type == "XRA" and rest:
        return f"{rest[0]} J/m²"
    return " ".join(rest) or "—"


def day_rows(events, bursts, cmes, day):
    """All that happened on ``day``, as rows for the table and the band, in
    order of time. CMEs of other days stay in the CMEs tab."""
    rows = []
    for event in events or ():
        kind = EVENT_KINDS.get(event.type_info.get("category"), "Other")
        begin, end = _hours(event.begin_time), _hours(event.end_time)
        flare_class = event.flare_class if event.event_type == "XRA" else (event.particulars.split() or ["—"])[0]
        marked = any((t or " ")[0] in TIME_MARKS for t in (event.begin_time, event.max_time, event.end_time))
        rows.append({
            "kind": kind,
            "begin": begin,
            "end": end,
            "peak": _hours(event.max_time),
            "values": [
                f"{_time_text(event.begin_time)} – {_time_text(event.end_time)}",
                _time_text(event.max_time),
                f"{event.duration_minutes} min" if event.duration_minutes is not None else "—",
                kind,
                (event.event_type if kind == "Other" else "") or flare_class or "—",
                _frequency_text(event),
                event.active_region or "—",
                _detail(event) if kind != "Other" else event.particulars or "—",
                event.observatory_name or "—",
            ],
            "tooltips": {0: MARKS_NOTE} if marked else {},
            "tones": {4: event.flare_class_tone} if event.flare_class_letter in ("M", "X") else {},
            "label": flare_class if kind != "Other" else event.event_type,
        })
    for burst in bursts or ():
        stations = burst.stations_list
        begin = _hours(burst.begin_time)
        rows.append({
            "kind": "Radio",
            "begin": begin,
            "end": _hours(burst.end_time),
            "peak": None,
            "values": [
                f"{_time_text(burst.begin_time)} – {_time_text(burst.end_time)}", "—", "—", "Radio", burst.burst_type,
                "—", "—", "—", f"e-CALLISTO, {len(stations)} station{'s' if len(stations) != 1 else ''}",
            ],
            "tooltips": {8: burst.stations},
            "tones": {},
            "label": burst.burst_type.strip("- "),  # the list has --- for a type not told
        })
    for cme in cmes or ():
        if cme.start_time.date() != day:
            continue
        begin = cme.start_time.hour + cme.start_time.minute / 60
        detail = [f"{cme.speed:.0f} km/s"]
        if cme.source == "CDAW":
            detail.append("first seen in C2")
        rows.append({
            "kind": "CME",
            "begin": begin,
            "end": begin,
            "peak": None,
            "values": [
                f"{cme.start_time:%H:%M}", "—", "—", "CME", cme.speed_category, cme.source_location,
                cme.active_region or "—", ", ".join(detail), "LASCO" if cme.source == "CDAW" else "DONKI",
            ],
            "tooltips": {7: ", ".join(detail)},
            "tones": {},
            "label": f"{cme.speed:.0f}",
        })
    rows.sort(key=lambda row: 99.0 if row["begin"] is None else row["begin"])
    return rows


def band_lanes(rows):
    """``rows`` by lane of the day band."""
    lanes = {}
    for row in rows:
        if row["begin"] is None or row["kind"] == "Other":
            continue
        # An event that runs past midnight has an end before its begin
        end = row["end"] if row["end"] is not None and row["end"] >= row["begin"] else 24.0
        if row["end"] is None:
            end = row["begin"]
        values = row["values"]
        tip = f"{row['label']} · {values[0]} UTC"
        lanes.setdefault(row["kind"], []).append((row["begin"], end, row["peak"], row["label"], tip))
    return lanes


class NOAAEventsViewer(
    ConditionsTabMixin, DayRecordsMixin, RadioSpectraTabMixin, ContextImagesTabMixin, QMainWindow
):
    """Main Solar Activity Viewer window - displays events, active regions, conditions, and CMEs."""

    def __init__(self, parent=None, initial_date: Optional[date] = None):
        super().__init__(parent)
        self.setWindowTitle("Solar activity")
        fit_to_screen(self, 1000, 800)

        # Network Manager for image downloading
        self.nam = QNetworkAccessManager(self)
        self.image_downloads = {}  # Keep references to replies
        self.image_viewers = []  # Keep references to open image windows

        self.worker = None
        self.goes_worker = None
        self.goes_day_worker = None
        self.records_worker = None
        self.day_records = {}  # of the date on show: key -> record
        self.dh_worker = None
        self.events = []
        self.events_rows = []  # what the Events tab and the band show
        self.download_queue = []
        self.image_savers = {}  # file path -> its ImageSaver
        self.shown_date = None  # the date the tables and images are of
        self._spectra_date = None  # the date of the last spectra fetched
        self._jobs = {}  # what is running, oldest first: key -> [text, how to cancel it]
        self._fetch_spectra = True  # whether the fetch under way goes on to the spectra
        self._notes_before = None  # what the empty tabs said before the fetch
        self._closing = False
        self._cursor_after_load = None  # where the cursor goes when the date being loaded is in
        self._note_date = None  # the date field's text, while a note names that date
        self._summary_before_bad_date = ""

        self.is_dark_theme = theme_manager.is_dark
        self.day_axis = DayAxis(self)

        self.init_ui()

        # Set initial date; a datetime (of an image) also places the cursor
        if initial_date:
            self._set_date(initial_date)
            if isinstance(initial_date, datetime):
                self.day_axis.set_cursor(_clock_hours(initial_date))
        else:
            # Default to yesterday
            self._set_date(date.today() - timedelta(days=1))
        self._date_committed()

        set_hand_cursor(self)

    def _set_date(self, value):
        # (strftime leaves out the zeros of a year before 1000: it would not read back)
        self.date_edit.setText(f"{value.year:04d}-{value.month:02d}-{value.day:02d}")

    def _date_edited(self, text):
        """A note about a date goes when the field leaves that date."""
        if self._note_date not in (None, text):
            self._note_date = None
            self._note_timer.stop()
            self._show_jobs()

    def _date_committed(self):
        """The date in the field is the one to show. Only a date already
        fetched loads by itself, and with no request; a new one is a job of
        many requests, which stepping through days must not start. What is on
        show stays, under "Loading…", until the new date replaces it at once."""
        day = self.selected_date()
        if day is None:
            return
        if "fetch" in self._jobs:  # (its result may be queued with the thread gone)
            if not self.worker.only_cached or self.worker.event_date == day:
                return
            self._cancel_fetch()
        if day == self.shown_date and self.day_axis.day == day:
            return
        self.stop_dh_spectra()
        if _noaa_cache.has_events(day):
            self._start_fetch(day, only_cached=True)
        else:
            self._show_nothing(day)

    def _show_nothing(self, day):
        """The date has no data yet: nothing of another date stays under it."""
        self._leave_goes_day()
        self._leave_day_records()
        self.shown_date = day
        self._spectra_date = None  # the lanes go by the date on show
        self.day_axis.set_day(day)
        self.events = []
        text = (NOT_YET if _in_the_future(day) else NOT_FETCHED).format(day=day)
        self.summary_label.setText(text)
        self.clear_tables()
        self._show_rows([])
        self._say_when_empty(text)
        self._say_events_source(None)
        self.cme_info_label.setText("")
        self.clear_regions_and_indices(text)
        self.clear_context_images()
        self.clear_dh_spectra_ui()
        self.display_ecallisto_bursts([])
        self.day_band.set_goes_text("")
        self._clear_record_views()
        self.indices.clear(text)

    def _step_day(self, days):
        """Go to the day before or after, at the same clock time."""
        day = self.selected_date()
        if day is not None and self.date_edit.isEnabled():
            try:
                self._set_date(day + timedelta(days=days))
            except OverflowError:
                self.say(f"There is no date {'before' if days < 0 else 'after'} {day}")
                return
            self._date_committed()

    def go_to(self, when):
        """To the date of ``when``, and for a datetime to its time as well,
        unless a fetch has the date field. True if it went."""
        if not self.date_edit.isEnabled():
            return False
        timed = isinstance(when, datetime)
        self._cursor_after_load = None
        self._set_date(when.date() if timed else when)
        self._date_committed()
        if timed and "fetch" in self._jobs:
            self._cursor_after_load = _clock_hours(when)  # not over the date on show now
        elif timed:
            self.day_axis.set_cursor(_clock_hours(when))
        return True

    def selected_date(self) -> Optional[date]:
        """The date in the field; if it is not one, None, and it says so."""
        try:
            # (YYYY.MM.DD, the form it had, still reads)
            day = datetime.strptime(self.date_edit.text().strip().replace(".", "-"), DATE_FORMAT).date()
        except ValueError:
            if self.summary_label.text() != BAD_DATE:
                self._summary_before_bad_date = self.summary_label.text()
            self.summary_label.setText(BAD_DATE)
            return None
        if self.summary_label.text() == BAD_DATE:
            self.summary_label.setText(self._summary_before_bad_date)
        return day

    def closeEvent(self, event):
        """Leave what is running to end by itself: a stalled server must not
        hold the window up as it closes."""
        self._closing = True
        for _, cancel in reversed(list(self._jobs.values())):
            cancel()
        self._jobs.clear()
        self.stop_dh_spectra()
        self._leave_goes_day()
        self._leave_day_records()
        self.leave_image_downloads()
        self.leave_image_lookups()
        if self._callisto_view is not None:
            self._callisto_view.reject()
        for viewer in list(self.image_viewers):
            viewer.close()  # (each is a window of its own, with its Save tied to this one)
        self._note_timer.stop()
        self._show_jobs()
        self._let_go_of_images()
        super().closeEvent(event)

    def _let_go_of_images(self):
        """What a closed window has no use for: a date's lanes alone take a
        couple of hundred megabytes, and Python can hold on to the window."""
        self.clear_dh_spectra_ui()
        self.clear_context_images()
        self.images_at = {}
        self.radio_images = []

    # What is running, beside the buttons: the fetch, the GOES plot and saved
    # images overlap, and the line lasts until the last of them has ended

    def begin_job(self, key, text, cancel):
        """Show ``text`` until ``end_job(key)``; ``cancel()`` is what the
        Cancel button calls to stop this job."""
        self._jobs[key] = [text, cancel]
        self._note_timer.stop()
        self._show_jobs()

    def update_job(self, key, text):
        if key in self._jobs:
            self._jobs[key][0] = text
            self._show_jobs()

    def end_job(self, key):
        self._jobs.pop(key, None)
        self._show_jobs()

    def _show_jobs(self):
        """The latest job is the one shown, and the one Cancel stops."""
        running = bool(self._jobs)
        self.cancel_btn.setVisible(running)
        self.fetch_btn.setEnabled("fetch" not in self._jobs and "spectra" not in self._jobs)
        if self._note_timer.isActive():
            pass  # a note has the line for now
        elif running:
            self.activity_line.start(list(self._jobs.values())[-1][0])
            self.activity_line.show()
        else:
            self.activity_line.stop()
            self.activity_line.hide()
        # The line takes the summary's place, and gives it back
        self.summary_label.setVisible(self.activity_line.isHidden())

    def cancel_latest_job(self):
        if not self._jobs:
            return
        key = list(self._jobs)[-1]
        cancel = self._jobs[key][1]
        self._jobs.pop(key)
        cancel()
        if self._jobs:
            self._show_jobs()
        else:
            self.say("Cancelled")

    def say(self, text, of_date=False):
        """A short note in the activity line, gone after a few seconds, when a
        job under way has the line back. ``of_date``: it names the date in the
        field, and goes if the field moves on."""
        self._note_date = self.date_edit.text() if of_date else None
        self.activity_line.stop(text)
        self.activity_line.show()
        self._note_timer.start()
        self._show_jobs()

    def init_ui(self):
        """Initialize the user interface."""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        # Always there, whatever the tab: the date and the day it is about
        top = QFrame()
        top.setObjectName("DayHeader")
        themed(top, lambda palette: top.setStyleSheet(
            f"QFrame#DayHeader {{ background-color: {palette['surface']}; border: none;"
            f" border-bottom: 1px solid {palette['divider']}; }}"
        ))
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, SPACE_M, 0, 0)
        top_layout.setSpacing(SPACE_S)
        top_layout.addLayout(self._create_header())

        self.plot_goes_btn = link_button("Open plot", self.plot_goes_xray)
        self.plot_goes_btn.setToolTip("Plot the GOES X-ray light curve of this date in a window of its own")
        self.whole_day_btn = link_button("Whole day", self.day_axis.whole_day)
        self.whole_day_btn.setToolTip("Back to the whole day (double-click the band, or Esc)")
        self.day_band = DayBand(self.day_axis, self.plot_goes_btn, self.whole_day_btn)
        top_layout.addWidget(self.day_band)
        layout.addWidget(top)
        # A click on anything that does not take the keys itself gives them to
        # the band, so that Left and Right stay on the cursor
        central.setFocusPolicy(Qt.ClickFocus)
        central.setFocusProxy(self.day_band)

        self.tabs = QTabWidget()
        self._create_events_tab()
        self._create_radio_spectra_tab()
        self._create_context_images_tab()
        self._create_cme_tab()
        self._create_regions_tab()

        page = QVBoxLayout()
        page.setContentsMargins(SPACE_L, SPACE_S, SPACE_L, SPACE_M)
        page.addWidget(self.tabs)
        layout.addLayout(page, 1)
        # Clicks pass through to the band; a scroll area would keep Left and
        # Right, and a tab bar with the focus would stop the click and use them
        # itself (Ctrl+Tab still changes tab)
        self.tabs.setFocusPolicy(Qt.NoFocus)
        self.tabs.tabBar().setFocusPolicy(Qt.NoFocus)
        for area in self.findChildren(QScrollArea):
            area.setFocusPolicy(Qt.TabFocus)

        self.day_axis.cursor_set.connect(self._cursor_chosen)
        self.day_axis.cursor_changed.connect(self._cursor_moved)
        for keys, slot in (
            ("Alt+Left", lambda: self._step_day(-1)),
            ("Alt+Right", lambda: self._step_day(1)),
            ("[", lambda: self.day_band.step_event(-1)),
            ("]", lambda: self.day_band.step_event(1)),
            ("Esc", self._clear_time),
        ):
            QShortcut(QKeySequence(keys), self, slot)

    def _create_header(self):
        """Date, summary or what is running, and the window's one main action."""
        bar = QHBoxLayout()
        bar.setContentsMargins(SPACE_L, 0, SPACE_L, 0)
        bar.setSpacing(SPACE_S)

        bar.addWidget(self._day_button("fd_back.svg", "Day before (Alt+Left)", -1))

        self.date_edit = QLineEdit()
        self.date_edit.setMinimumWidth(130)
        self.date_edit.setMaximumWidth(150)
        attach_date_picker(self.date_edit, lambda text: self._date_committed(), fmt=DATE_FORMAT)
        self.date_edit.editingFinished.connect(self._date_committed)
        self.date_edit.textChanged.connect(self._date_edited)
        bar.addWidget(self.date_edit)

        bar.addWidget(self._day_button("fd_forward.svg", "Day after (Alt+Right)", 1))

        # Get date from current tab button: there is no tab without a viewer
        self.get_date_btn = QPushButton("From image")
        self.get_date_btn.setToolTip("Take the date and time from the image or FITS file open now")
        self.get_date_btn.clicked.connect(self.get_date_from_parent_tab)
        self.get_date_btn.setVisible(self.parent() is not None)
        bar.addWidget(self.get_date_btn)

        self.summary_label = ElidedLabel("Pick a date and press Fetch to see that day's solar activity.")
        bar.addSpacing(SPACE_S)
        bar.addWidget(self.summary_label, 1)

        self.activity_line = ActivityLabel()
        self.activity_line.label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.activity_line.hide()
        bar.addWidget(self.activity_line, 1)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.cancel_latest_job)
        self.cancel_btn.hide()
        bar.addWidget(self.cancel_btn)

        self._note_timer = QTimer(self)
        self._note_timer.setSingleShot(True)
        self._note_timer.setInterval(CANCEL_NOTE_MS)
        self._note_timer.timeout.connect(self._show_jobs)

        self.fetch_btn = QPushButton("Fetch")
        self.fetch_btn.setObjectName("PrimaryButton")
        self.fetch_btn.setToolTip("Fetch this date's lists, images and radio spectra")
        self.fetch_btn.clicked.connect(lambda: self.fetch_data())
        bar.addWidget(self.fetch_btn)
        return bar

    def _day_button(self, icon, tip, days):
        button = QPushButton()
        button.setObjectName("IconOnlyNBGButton")
        button.setIconSize(QSize(18, 18))
        button.setToolTip(tip)
        themed(button, lambda palette: button.setIcon(QIcon(themed_icon(icon))))
        button.clicked.connect(lambda: self._step_day(days))
        return button

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.day_band.set_compact(event.size().height() < COMPACT_HEIGHT)

    def keyPressEvent(self, event):
        """Left and Right that no widget had a use for. A table at the end of
        its row passes them on, which is not a move of the cursor."""
        if isinstance(self.focusWidget(), QAbstractItemView) or not self.day_axis.arrow_key(event):
            super().keyPressEvent(event)

    def _create_events_tab(self):
        """Events tab: all that happened, in one table in time order."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, SPACE_M, 0, 0)
        layout.setSpacing(SPACE_S)

        self.filter_group = QButtonGroup(self)
        buttons = []
        for text in FILTERS:
            button = QPushButton(text)
            button.setCheckable(True)
            button.setChecked(text == FILTERS[0])
            self.filter_group.addButton(button)
            buttons.append(button)
        self.filter_group.buttonClicked.connect(lambda button: self._apply_filter())
        events_filter = segmented(buttons)

        day_view = QWidget()
        day_layout = QVBoxLayout(day_view)
        day_layout.setContentsMargins(0, 0, 0, 0)
        day_layout.setSpacing(SPACE_S)
        lists_view = self._create_lists_view()
        switch, self.events_views = view_switch(VIEW_LABELS, (day_view, lists_view), self)
        self.events_views.buttonClicked.connect(
            lambda button: self._show_events_filter(events_filter, button.text() == VIEW_LABELS[0])
        )
        self._show_events_filter(events_filter, True)
        row = QHBoxLayout()
        row.addWidget(switch)
        row.addSpacing(SPACE_M)
        row.addWidget(events_filter)
        row.addWidget(self.list_filter)
        row.addStretch()
        layout.addLayout(row)

        self.events_table = EventTable(EVENT_COLUMNS, empty_text="Fetch a date to see its events")
        self.events_table.give_way_column = EVENT_COLUMNS.index("Detail")
        self.events_table.itemSelectionChanged.connect(self._event_selected)
        self.events_table.clicked.connect(self._event_selected)  # a row selected already, after Esc
        day_layout.addWidget(self.events_table, 1)

        self.events_note = QLabel()  # where the list on show falls short of the final one
        self.events_note.setObjectName("Hint")
        self.events_note.setWordWrap(True)
        self.events_note.hide()
        day_layout.addWidget(self.events_note)
        self._events_credits = _event_credits(None)
        self.events_credits_label = source_note(self._events_credits)
        day_layout.addWidget(self.events_credits_label)
        layout.addWidget(day_view, 1)
        layout.addWidget(lists_view, 1)

        self.tabs.addTab(page, "Events")

    def _show_events_filter(self, events_filter, on_events):
        events_filter.setVisible(on_events)
        self.list_filter.setVisible(not on_events)

    def _create_cme_tab(self):
        """CMEs tab."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, SPACE_M, 0, 0)
        layout.setSpacing(SPACE_S)

        self.cme_table = EventTable(
            [
                "Time (UTC)",
                "Speed (km/s)",
                "Source",
                "Half angle",
                "Earth-directed",
                "Width",
                "Class",
                "Note",
            ],
            empty_text="Fetch a date to see the CMEs around it",
        )
        layout.addWidget(self.cme_table, 1)

        self.cme_info_label = QLabel()
        self.cme_info_label.setWordWrap(True)
        self.cme_info_label.setObjectName("Hint")
        layout.addWidget(self.cme_info_label)

        self.cme_credits_label = source_note(f"Data source: {DONKI_CREDIT}")
        layout.addWidget(self.cme_credits_label)

        self.tabs.addTab(page, "CMEs")

    def _apply_filter(self):
        """Only the rows of the kind chosen."""
        kind = self.filter_group.checkedButton().text()
        table = self.events_table
        for row in range(table.rowCount()):
            table.setRowHidden(row, kind != FILTERS[0] and table.item(row, 3).text() != kind)
        if table.rowCount():
            table.set_empty_text(f"No {kind} events on this date")
        table._show_empty_note()

    def _clear_time(self):
        """Off a drag under way, then the cursor, then the zoom."""
        if any([row.cancel_drag() for row in (self.day_band, *self._rows(), *self.indices.strips)]):
            return
        if self.day_axis.cursor is None:
            self.day_axis.whole_day()
        self.day_axis.set_cursor(None)

    def _event_selected(self):
        rows = self.events_table.selectionModel().selectedRows()
        if rows:
            hours = self.events_table.item(rows[0].row(), 0).data(SORT_ROLE)
            if hours is not None and hours < 24:
                self.day_axis.set_cursor(hours)

    def _cursor_chosen(self):
        """A choice of the reader's outranks the image's time still to come."""
        self._cursor_after_load = None

    def _cursor_moved(self):
        """The nearest row comes into view, the selected one of a tie; the
        selection stays the reader's."""
        hours = self.day_axis.cursor
        if hours is None:
            return
        for table in (self.events_table, self.lists_table):
            rows = [r for r in range(table.rowCount()) if not table.isRowHidden(r) and table.item(r, 0).data(SORT_ROLE) is not None]
            if rows:
                selected = {index.row() for index in table.selectionModel().selectedRows()}
                nearest = min(rows, key=lambda r: (abs(table.item(r, 0).data(SORT_ROLE) - hours), r not in selected))
                table.scrollToItem(table.item(nearest, 0))

    def _load_goes_day(self, day, only_cached=True):
        """Fill the band's GOES lane behind everything else. A curve saved
        while its day was young is shown, and asked for again on a Fetch."""
        self._leave_goes_day()
        flux = _noaa_cache.load_goes_day(day)
        if flux is not None:  # in at once, with the tables
            self.day_band.set_flux(flux)
            if only_cached or not saved_early(_noaa_cache.goes_day_path(day), day):
                return
        else:
            self.day_band.set_goes_text(FETCHING_GOES)
        self.goes_day_worker = GoesDayWorker(day)
        self.goes_day_worker.ready.connect(self._goes_day_ready)
        self.goes_day_worker.failed.connect(self._goes_day_failed)
        self.goes_day_worker.start()

    def _leave_goes_day(self):
        if self.goes_day_worker is not None:
            abandon(self.goes_day_worker)
            self.goes_day_worker = None

    def _cancel_goes_day(self):
        self._leave_goes_day()
        if self.day_band.goes_text == FETCHING_GOES:
            self.day_band.set_goes_text(GOES_NOT_FETCHED)

    def _goes_day_ready(self, day, flux):
        if day == self.shown_date:
            self.day_band.set_flux(flux)

    def _goes_day_failed(self, day, _detail):
        if day == self.shown_date:
            self.day_band.set_goes_text("GOES data could not be fetched")

    def fetch_data(self, with_spectra=True):
        """Start fetching data for the selected date: the lists and images,
        then, one job at a time, the spectra (``with_spectra`` False: only
        those already here)."""
        if "fetch" in self._jobs:
            if not self.worker.only_cached:
                return
            self._cancel_fetch()

        selected_date = self.selected_date()
        if selected_date is None:
            return
        if _in_the_future(selected_date):
            self.summary_label.setText(NOT_YET.format(day=selected_date))
            return

        self.stop_dh_spectra()
        self._start_fetch(selected_date, with_spectra=with_spectra)

    def _start_fetch(self, day, only_cached=False, with_spectra=False):
        self._fetch_spectra = with_spectra
        if not only_cached:
            self.date_edit.setEnabled(False)
            self.get_date_btn.setEnabled(False)
            self._say_when_empty(f"Fetching {day}…")
            self._notes_before = self.images_empty.text(), self.spectra_empty.text()
            self.images_empty.setText(f"Fetching {day}…")
            self.spectra_empty.setText(f"Fetching {day}…")
        self._summary_before = self.summary_label.text()
        self.begin_job(
            "fetch", f"Loading {day}…" if only_cached else "Starting…", self._fetch_cancelled,
        )

        # Clean up old worker if exists
        if self.worker is not None:
            abandon(self.worker)  # (it can be returning from run() still)

        self.worker = FetchWorker(day, only_cached=only_cached)
        self.worker.done.connect(self.on_fetch_finished)
        self.worker.error.connect(self.on_fetch_error)
        self.worker.progress.connect(self.on_fetch_progress)
        self.worker.start()

    def on_fetch_progress(self, message):
        self.update_job("fetch", message)

    def _say_when_empty(self, text):
        for table in (self.events_table, self.cme_table, self.ar_table):
            table.set_empty_text(text)

    def _fetch_ended(self):
        self.date_edit.setEnabled(True)
        self.get_date_btn.setEnabled(True)
        self._cursor_after_load = None
        self.end_job("fetch")

    def _cancel_fetch(self):
        """Back to the tables and summary from before the fetch: nothing on
        show changes until the fetch has something to show instead."""
        abandon(self.worker)
        self.worker = None
        self._fetch_ended()
        self.summary_label.setText(self._summary_before)
        self._say_when_empty(self._summary_before)
        if self._notes_before:
            self.images_empty.setText(self._notes_before[0])
            self.spectra_empty.setText(self._notes_before[1])

    def _fetch_cancelled(self):
        """Cancel pressed: the field goes back to the date still on show."""
        self._cancel_fetch()
        self._cancel_goes_day()
        if self.shown_date is not None:
            self._set_date(self.shown_date)

    def _from_old_worker(self):
        """Whether the worker's signal being handled is of one left behind: a
        queued signal comes even after ``abandon``."""
        return self.worker is None or self.sender() not in (None, self.worker)

    def on_fetch_finished(self, events, active_regions, conditions, cmes, images, ecallisto_bursts=None):
        """Handle fetched data."""
        if self._from_old_worker():
            return  # cancelled
        ecallisto_bursts = ecallisto_bursts or []
        worker = self.worker
        event_date = worker.event_date
        shown = False
        try:
            # Check for validity
            if not self.isVisible() and not self.parent():
                return  # Window closed

            label = f"Showing the data for {event_date}…"
            self.update_job("fetch", label)
            with busy(label, self):
                # Past busy's turn of the event loop: band and tables change in one paint
                self.events = events
                self.shown_date = event_date
                self.day_axis.set_day(event_date)
                if self._cursor_after_load is not None:
                    self.day_axis.set_cursor(self._cursor_after_load)

                # Display events
                self.display_events(events, ecallisto_bursts, cmes)
                self.display_ecallisto_bursts(ecallisto_bursts)

                # Display active regions
                self.display_active_regions(active_regions)

                # Display conditions
                self.display_solar_conditions(conditions)

                # Display CMEs
                self.display_cme_events(cmes, fetched=not worker.only_cached)

                # Display images
                self.display_fetched_images(images, fetched=not worker.only_cached)

                # Update comprehensive summary
                self._update_comprehensive_summary(events, active_regions, conditions, cmes)
            shown = True

        except Exception:
            # (a widget deleted during the update, too.) One date that cannot
            # be shown must not take the application with it
            logger.exception("Could not show the data for %s", event_date)
            try:
                self.summary_label.setText(f"Could not show the data for {event_date}. The log has the details.")
            except RuntimeError:
                pass
        finally:
            self._fetch_ended()
        if shown:
            self._after_tables(event_date, only_cached=worker.only_cached)

    def _after_tables(self, day, only_cached=False):
        """One job at a time: the spectra's requests start when the tables are
        in, and the GOES lane and the day's records fill behind everything."""
        self.fetch_dh_spectra(day, only_cached=not self._fetch_spectra)
        self._load_goes_day(day, only_cached)
        self._ask_day_records(day, only_cached)

    def _update_comprehensive_summary(self, events, active_regions, conditions, cmes):
        """Update the main summary label with a comprehensive overview of all data."""
        summary_parts = []
        day = self.shown_date

        # Flares of the date, then what else it had
        if events:
            categories = ne.categorize_events(events)
            xray = categories.get("xray", [])
            max_class = ne.get_event_statistics(events).get("max_xray_class", None)
            flare_part = f"{len(xray)} flare{'' if len(xray) == 1 else 's'}"
            if max_class:
                flare_part += f", max {max_class}"
            summary_parts.append(flare_part)
            summary_parts.append(f"{len(categories.get('optical', []))} H-alpha")
        elif isinstance(events, ne.EmptyList):
            summary_parts.append(EMPTY_LIST)
        elif events is not None:  # None: the list could not be fetched
            summary_parts.append("0 flares")
        if events is not None:
            radio = [r for r in self.events_rows if r["kind"] == "Radio"]
            if radio or not isinstance(events, ne.EmptyList):
                summary_parts.append(f"{len(radio)} radio")

        if cmes is not None:
            of_day = [cme for cme in cmes if cme.start_time.date() == day]
            summary_parts.append(f"{len(of_day)} CME{'' if len(of_day) == 1 else 's'}")
            earth_directed = sum(1 for cme in of_day if cme.is_earth_directed)
            if earth_directed > 0:
                summary_parts.append(f"{earth_directed} Earth-directed")

        if active_regions:  # (none: a file with none, or none to be had)
            summary_parts.append(f"{len(active_regions)} regions")

        if conditions and conditions.f107_flux:
            summary_parts.append(f"{conditions.f107_flux.sunspot_number} sunspots")
            summary_parts.append(f"F10.7 {conditions.f107_flux.flux_value:.0f} sfu")

        if ne.is_snapshot(events):
            summary_parts.append("same-day snapshot")

        if not summary_parts:
            self.summary_label.setText("No data available for this date.")
        else:
            self.summary_label.setText(" · ".join(summary_parts))

    def on_fetch_error(self, error_msg):
        """Handle fetch error."""
        if self._from_old_worker():
            return  # cancelled
        try:
            self._fetch_ended()
            if self.shown_date is not None:
                self._set_date(self.shown_date)

            # Check validity
            if not self.isVisible() and not self.parent():
                return

            self.summary_label.setText(f"Could not fetch the data: {error_msg}")
            show_error(
                self, "Could not fetch the data",
                "The data for this date could not be fetched. Check the connection and try again.",
                error_msg,
            )
        except RuntimeError:
            pass

    def clear_tables(self):
        """Clear all event tables."""
        self.events_table.setRowCount(0)
        self.ar_table.setRowCount(0)
        self.cme_table.setRowCount(0)

    def display_events(self, events, ecallisto_bursts=None, cmes=None):
        """All the day's events in one table, and in the band."""
        self.clear_tables()
        self._say_events_source(events)
        if events is None:
            self.events_table.set_empty_text("Could not fetch the events")
            self.summary_label.setText("No data could be fetched.")
            self._show_rows([])
            return
        self.events_table.set_empty_text(EMPTY_LIST.capitalize() if isinstance(events, ne.EmptyList) else "No events on this date")
        rows = day_rows(events, ecallisto_bursts, cmes, self.shown_date)
        self._show_rows(rows)
        self.events_table.scrollToTop()
        self.events_table.doItemsLayout()  # (the scroll bar's range, for what follows)
        self._cursor_moved()  # a cursor set before the rows were in

    def _say_events_source(self, events):
        """Which file the list on show is from, and what it may lack."""
        self._events_credits = _event_credits(events)
        set_source_note(self.events_credits_label, self._events_credits)
        note = ne.events_note(events, self.shown_date) if self.shown_date else ""
        self.events_note.setText(note)
        self.events_note.setVisible(bool(note))

    def _show_rows(self, rows):
        self.events_rows = rows
        table = self.events_table
        table.setRowCount(0)
        with table.filling():
            for row in rows:
                table.add_event_row(
                    row["values"],
                    row["tones"],
                    row["tooltips"],
                    {0: row["begin"], 1: row["peak"] if row["peak"] is not None else row["begin"]},
                )
        self.day_band.set_events(band_lanes(rows))
        self._apply_filter()

    def display_cme_events(self, cmes, fetched=True):
        """Display CME events in the CME table: the date's first, then those
        around it. ``fetched`` False: a list of ``None`` was not asked for."""
        self.cme_table.setRowCount(0)
        day = self.shown_date

        if cmes is None or len(cmes) == 0:
            text = (
                NO_CMES_YET if cmes is None and not fetched
                else "Could not fetch the CMEs for this date."
                if cmes is None
                else "No CME activity detected in the ±3 day range for this date."
            )
            self.cme_table.set_empty_text(text)
            self.cme_info_label.setText(text)
            self.cme_info_label.show()
            return

        of_day = sum(1 for cme in cmes if cme.start_time.date() == day)
        counts = f"{of_day} on {day}, {len(cmes)} within ±3 days"
        cdaw = cmes[0].source == "CDAW"
        if cdaw:
            self.cme_info_label.setText(
                f"{counts}, from the CDAW LASCO catalogue. NASA DONKI could not be "
                "reached, so their source and Earth direction are not known."
            )
        else:
            self.cme_info_label.setText(f"{counts}, from NASA DONKI.")
        credit = CDAW_CREDIT if cdaw else DONKI_CREDIT
        set_source_note(self.cme_credits_label, f"Data source: {credit}")
        set_source_note(self.events_credits_label, f"{self._events_credits}, {credit}")

        with self.cme_table.filling():
            for cme in sorted(cmes, key=lambda c: (c.start_time.date() != day, c.start_time)):
                time_str = cme.start_time.strftime("%Y-%m-%d %H:%M")
                speed_str = f"{cme.speed:.0f}"
                half_str = f"{cme.half_angle:.0f}°" if cme.half_angle and not cdaw else "—"
                width_str = f"{cme.half_angle * 2:.0f}°" if cme.half_angle and cdaw else "—"
                earth_str = (
                    "—" if cme.is_earth_directed is None
                    else "Yes" if cme.is_earth_directed else "No"
                )
                note = " ".join((cme.note or "").split()) or "—"

                # Other days step back; what matters keeps its tone
                tones = {col: "text_secondary" for col in range(8)} if cme.start_time.date() != day else {}
                if cme.is_earth_directed:
                    tones[4] = "error" if cme.speed >= 1000 else "warning"
                category = cme.speed_category
                if category in ("Fast", "Extreme"):
                    tones[6] = "warning" if category == "Fast" else "error"

                short = note if len(note) <= NOTE_LENGTH else f"{note[:NOTE_LENGTH - 1]}…"
                self.cme_table.add_event_row(
                    [
                        time_str,
                        speed_str,
                        cme.source_location,
                        half_str,
                        earth_str,
                        width_str,
                        category,
                        short,
                    ],
                    tones,
                    {7: note} if short != note else None,
                )

        # A column the source has nothing for stays out of the way
        table = self.cme_table
        for col in range(table.columnCount()):
            table.setColumnHidden(col, all(table.item(r, col).text() == "—" for r in range(table.rowCount())))
        table.fit_columns()
        table.scrollToTop()

    def get_date_from_parent_tab(self):
        """Extract date from the currently open tab in the parent viewer.

        Uses the same logic as the viewer's figure title date extraction.
        """
        try:
            # Get parent main window
            parent = self.parent()
            if parent is None:
                QMessageBox.information(
                    self, "No image", "No parent viewer found. Open an image first."
                )
                return

            # The tab open now, whichever pane the window was opened from
            current_tab = None
            host = parent if hasattr(parent, "tab_widget") else parent.window()
            if hasattr(host, "tab_widget"):
                current_tab = host.tab_widget.currentWidget()

            if current_tab is None:
                QMessageBox.information(self, "No image", "No image is currently open.")
                return

            extracted_date = None
            image_time = None
            when = None  # the image's time, if the pane can tell
            imagename = getattr(current_tab, "imagename", None)

            # As the menu reads it, from the file the tab is on: its
            # current_header is the last file's until a load has ended
            observation = getattr(current_tab, "_observation_datetime", None)
            if observation is not None:
                with busy("Reading the observation time…", self):
                    when = observation()
                if when is not None:
                    extracted_date = when.date() if isinstance(when, datetime) else when

            # Method 1: Try FITS header from tab attribute first
            header = None if extracted_date else getattr(current_tab, "current_header", None) or None

            # Method 1b: If no header attribute, read FITS/FTS file directly
            if extracted_date is None and header is None and imagename:
                if is_fits(imagename):
                    try:
                        header = run_in_background(
                            self, f"Reading the date of {os.path.basename(imagename)}…", fits_header, imagename
                        )
                    except Cancelled:
                        raise
                    except Exception as fits_err:
                        logger.warning("FITS header read failed: %s", fits_err)

            # Extract date from header
            if header is not None:
                # Check DATE-OBS (standard), DATE_OBS (IRIS), and STARTOBS
                image_time = (
                    header.get("DATE-OBS")
                    or header.get("DATE_OBS")
                    or header.get("STARTOBS")
                )

                # Special handling for SOHO (DATE-OBS + TIME-OBS)
                if (
                    header.get("TELESCOP") == "SOHO"
                    and header.get("TIME-OBS")
                    and image_time
                ):
                    image_time = f"{image_time}T{header['TIME-OBS']}"

                if image_time:
                    extracted_date = self._parse_date_string(str(image_time))

            # Method 2: CASA image - read csys_record directly from file (like viewer.py)
            if extracted_date is None and imagename:
                # Check if it's a CASA image (directory, not .fits/.fts)
                is_casa_image = not is_fits(imagename)

                if is_casa_image:
                    try:
                        info = run_in_background(
                            self, f"Reading the date of {os.path.basename(imagename)}…", casa_info, imagename
                        )
                        t = casa_obs_time(info.csys_record)
                        if t is not None:
                            extracted_date = t.to_datetime().date()
                    except Cancelled:
                        raise
                    except Exception as casa_err:
                        logger.warning("CASA date extraction failed: %s", casa_err)

            # Method 3: Try filename parsing (e.g., 20231002_image.fits)
            if extracted_date is None and imagename:
                # The file's own name first: a folder's digits say less
                filename = f"{os.path.basename(imagename)} {imagename}"
                # Try various date patterns in filename
                patterns = [
                    r"(\d{4})(\d{2})(\d{2})",  # YYYYMMDD
                    r"(\d{4})-(\d{2})-(\d{2})",  # YYYY-MM-DD
                    r"(\d{4})\.(\d{2})\.(\d{2})",  # YYYY.MM.DD
                ]
                for pattern in patterns:
                    match = re.search(pattern, filename)
                    if match:
                        try:
                            y, m, d = (
                                int(match.group(1)),
                                int(match.group(2)),
                                int(match.group(3)),
                            )
                            if 1990 < y < 2100 and 1 <= m <= 12 and 1 <= d <= 31:
                                extracted_date = date(y, m, d)
                                break
                        except (ValueError, IndexError):
                            continue

            if extracted_date:
                if self.go_to(extracted_date if when is None else when):
                    self.say(f"Date set to {extracted_date} from current image.", of_date=True)
                self.day_band.setFocus()  # the arrow keys stay on the cursor
            else:
                QMessageBox.information(
                    self,
                    "No date found",
                    "Could not extract date from the current image.\n\n"
                    "Supported formats:\n"
                    "• FITS files with DATE-OBS header\n"
                    "• CASA images with observation date\n"
                    "• Files with date in filename (YYYYMMDD)",
                )

        except Cancelled:
            self.summary_label.setText("Cancelled")
        except Exception as e:
            show_error(self, "No date found", "Could not read the date of the current image.", e)

    def _parse_date_string(self, date_str: str) -> Optional[date]:
        """Parse various date string formats."""
        if not date_str:
            return None

        date_str = str(date_str).strip()

        try:
            # ISO format with time (2023-10-02T12:30:00)
            if "T" in date_str:
                # Clean up the date string for parsing
                clean_str = date_str.replace("Z", "").split("+")[0].split(".")[0]
                # Handle potential timezone info
                if "-" in clean_str[11:]:  # Timezone like -05:00 after time
                    clean_str = clean_str[:19]
                try:
                    dt = datetime.fromisoformat(clean_str)
                    return dt.date()
                except ValueError:
                    # Fallback: just extract date part
                    date_part = clean_str.split("T")[0]
                    if len(date_part) >= 10:
                        return datetime.strptime(date_part[:10], "%Y-%m-%d").date()

            # YYYY-MM-DD
            if "-" in date_str and len(date_str) >= 10:
                return datetime.strptime(date_str[:10], "%Y-%m-%d").date()

            # YYYY/MM/DD
            if "/" in date_str and len(date_str) >= 10:
                return datetime.strptime(date_str[:10], "%Y/%m/%d").date()

            # YYYYMMDD (8 digits)
            if date_str.isdigit() and len(date_str) >= 8:
                return datetime.strptime(date_str[:8], "%Y%m%d").date()

            # MJD (Modified Julian Date)
            if date_str.replace(".", "").isdigit():
                mjd = float(date_str)
                if 40000 < mjd < 100000:  # Valid MJD range
                    from astropy.time import Time

                    t = Time(mjd, format="mjd")
                    return t.to_datetime().date()
        except (ValueError, TypeError, ImportError):
            pass

        return None

    def set_date_from_fits(self, fits_date: Optional[date]):
        """Set the date from a FITS file's DATE-OBS."""
        if fits_date:
            self._set_date(fits_date)
            self._date_committed()


def _time_order(event):
    """Sort key: an event's begin time, without the mark it may carry."""
    return ne._clock(event.begin_time or "9999")


def _open_viewer(parent):
    """The window of the viewer ``parent`` is in, if one is open: the menu
    and the main window, the launcher a pane in it."""
    if parent is None:
        return None
    return next((w for w in parent.window().findChildren(NOAAEventsViewer) if w.isVisible()), None)


def show_noaa_events_viewer(parent=None, initial_date: Optional[date] = None):
    """
    Show the NOAA Events Viewer dialog.

    Args:
        parent: Parent widget
        initial_date: Optional initial date (e.g., from FITS header), or a
            datetime, whose time places the cursor

    Returns:
        The viewer window instance
    """
    viewer = _open_viewer(parent)
    if viewer is None:
        # The main window's, not a pane's: closing an image must not close this
        viewer = NOAAEventsViewer(None if parent is None else parent.window(), initial_date)
        viewer.setAttribute(Qt.WA_DeleteOnClose)  # a parent outlives many openings, each with a date's images
        viewer.show()
    else:
        viewer.raise_()
        viewer.activateWindow()
        if initial_date and not viewer.go_to(initial_date):
            viewer.say(FETCH_HOLDS_DATE.format(day=viewer.date_edit.text()))

    # If initial date provided, auto-fetch; the spectra's many requests wait
    # for a press of Fetch
    if initial_date and not _noaa_cache.has_events(viewer.selected_date()):
        viewer.fetch_data(with_spectra=False)

    return viewer


def main():
    import argparse

    # Parse command line arguments
    parser = argparse.ArgumentParser(description="NOAA Solar Events Viewer")
    parser.add_argument(
        "--theme",
        choices=["light", "dark"],
        default=None,
        help="Set application theme (light or dark; default: the one SolarViewer uses)",
    )
    args = parser.parse_args()

    from ..ui.app import create_app

    app = create_app(args.theme)

    viewer = NOAAEventsViewer()
    viewer.show()
    code = app.exec_()
    # What is left is waiting on a socket, and has nothing to save: Python would
    # wait for it, up to the longest timeout
    FileSaver.sweep()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":
    main()

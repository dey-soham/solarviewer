"""
What the solar events viewer does with the day's records: asks for them behind
everything else, and shows each where it belongs: the protons on the day band,
the indices on the Indices page and the flare lists beside the Events table.
"""

from datetime import datetime, timezone

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QButtonGroup, QLabel, QPushButton, QVBoxLayout, QWidget

from ..solar_context.realtime_data import SolarWindData, storm_words
from ..ui.download_parts import abandon
from ..ui.styles import SPACE_S
from ..ui.widgets import segmented
from .indices_view import kp_text, storm_tone
from .radio_spectra_tab import NOT_FETCHED, hhmm
from .widgets import SORT_ROLE, EventTable, FullImageViewer, set_cell_tone, source_note
from .workers import DayRecords

LIST_COLUMNS = ["Peak (UTC)", "Time (UTC)", "List", "Class", "Peak flux", "Peak frequency", "Spectrum", "Detail"]
SPECTRUM_COLUMN = LIST_COLUMNS.index("Spectrum")
LISTS = ("All", "EOVSA", "STIX", "HEK")
LIST_CREDITS = (
    'Data sources: <a href="https://ovsa.njit.edu/eovsadata/">EOVSA</a> (NJIT), '
    '<a href="https://datacenter.stix.i4ds.net/">STIX Data Center</a> (Solar Orbiter), '
    '<a href="https://www.lmsal.com/hek/">Heliophysics Event Knowledgebase</a>'
)
STIX_NOTE = (
    "STIX looks from Solar Orbiter, so it can see flares GOES cannot, and its times can differ from "
    "Earth's by the light travel time. ~ marks STIX's own estimate of a class. GOES at peak is GOES's level "
    "at that minute, whatever made it."
)
SPECTRUM_TIP = "Open this flare's dynamic spectrum, from the EOVSA archive"
FLARE_TONES = {"M": "warning", "X": "error"}
VIEW_LABELS = ("Day's events", "Flare lists")


def view_switch(names, pages, parent):
    """Two views of one tab, as a segmented pair; the row and the group."""
    group = QButtonGroup(parent)
    buttons = []
    for name, page in zip(names, pages):
        button = QPushButton(name)
        button.setCheckable(True)
        button.setChecked(page is pages[0])
        button.setProperty("page", name)
        group.addButton(button)
        buttons.append(button)

    def show(chosen):
        for name, page in zip(names, pages):
            page.setVisible(chosen.text() == name)

    group.buttonClicked.connect(show)
    show(buttons[0])
    return segmented(buttons), group


def _span_text(row):
    begin, end = row.get("begin"), row.get("end")
    if begin is None:
        return "—"
    begin = hhmm(begin % 24)
    return begin if end is None else f"{begin} – {hhmm(end % 24)}"


def wind_summary(wind):
    """The day's wind in a card's words: {key: text}, {key: tone}."""

    def known(name):
        return [value for value in wind[name] if value is not None]

    speed, density, bz, temperature = known("speed"), known("density"), known("bz"), known("temperature")
    texts = {
        "speed": f"mean {sum(speed) / len(speed):.0f}, max {max(speed):.0f} km/s" if speed else "—",
        "density": f"mean {sum(density) / len(density):.1f}, max {max(density):.1f} cm⁻³" if density else "—",
        "bz": f"lowest {min(bz):.1f} nT" if bz else "—",
        "temperature": f"mean {sum(temperature) / len(temperature):.0f} K" if temperature else "—",
        "status": "—",
    }
    tone = None
    if speed:
        category = SolarWindData(None, max(speed), 0, 0).speed_category
        texts["status"] = f"{category} speed"
        tone = {"High": "error", "Elevated": "warning"}.get(category)
    return texts, tone


class DayRecordsMixin:
    """Mixed into :class:`NOAAEventsViewer`.

    Host attributes/methods used: ``begin_job``, ``end_job``, ``day_axis``,
    ``day_band``, ``image_viewers``, ``shown_date``, ``_cleanup_viewer`` and
    the Indices page's strips and cards.
    """

    # The Flare lists view of the Events tab

    def _create_lists_view(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_S)
        self.list_group = QButtonGroup(self)
        buttons = []
        for text in LISTS:
            button = QPushButton(text)
            button.setCheckable(True)
            button.setChecked(text == LISTS[0])
            self.list_group.addButton(button)
            buttons.append(button)
        self.list_group.buttonClicked.connect(lambda button: self._filter_lists())
        self.list_filter = segmented(buttons)

        self.lists_table = EventTable(LIST_COLUMNS, empty_text="Fetch a date to see its flare lists")
        self.lists_table.give_way_column = LIST_COLUMNS.index("Detail")
        self.lists_table.itemSelectionChanged.connect(self._list_row_chosen)
        self.lists_table.clicked.connect(self._list_row_clicked)
        self.lists_table.doubleClicked.connect(lambda index: self._open_flare_spectrum(index.row()))
        layout.addWidget(self.lists_table, 1)

        self.lists_note = QLabel()
        self.lists_note.setObjectName("Hint")
        self.lists_note.setWordWrap(True)
        layout.addWidget(self.lists_note)
        layout.addWidget(source_note(LIST_CREDITS))
        return page

    def _filter_lists(self):
        chosen = self.list_group.checkedButton().text()
        table = self.lists_table
        for row in range(table.rowCount()):
            table.setRowHidden(row, chosen != LISTS[0] and table.item(row, 2).text() != chosen)
        if table.rowCount():
            table.set_empty_text(f"Nothing from {chosen} on this date")
        table._show_empty_note()

    def _list_row_chosen(self):
        rows = self.lists_table.selectionModel().selectedRows()
        if rows:
            self.day_axis.set_cursor(self.lists_table.item(rows[0].row(), 0).data(SORT_ROLE))

    def _list_row_clicked(self, index):
        self._list_row_chosen()  # a row selected already, after Esc
        if index.column() == SPECTRUM_COLUMN:
            self._open_flare_spectrum(index.row())

    def _open_flare_spectrum(self, row):
        url = self.lists_table.item(row, SPECTRUM_COLUMN).data(Qt.UserRole)
        if not url:
            return
        peak = self.lists_table.item(row, 0).text()
        viewer = FullImageViewer(None, f"EOVSA flare {self.shown_date} {peak}", url)
        viewer.setAttribute(Qt.WA_DeleteOnClose)
        self.image_viewers.append(viewer)
        viewer.finished.connect(lambda result, v=viewer: self._cleanup_viewer(v))
        viewer.show()

    def _show_lists(self):
        table = self.lists_table
        rows, said = [], []
        for key, name in (("eovsa", "EOVSA"), ("stix", "STIX"), ("hek", "HEK")):
            record = self.day_records.get(key)
            if record is None:
                continue
            if "said" in record:
                said.append(f"{record['said'] or f'{name} has nothing for this date'}.")
                continue
            rows += record["rows"]
            count = len(record["rows"])
            if key != "hek":
                said.append(
                    f"{name} lists {count} flare{'' if count == 1 else 's'}."
                    if count else f"{name} lists no flares on this date."
                )
            if key == "stix" and record["rows"]:
                said.append(STIX_NOTE)
            if key == "hek":
                said.append(self._hek_sentence(record))
        rows.sort(key=lambda row: row["peak"])
        table.setRowCount(0)
        with table.filling():
            for row in rows:
                tone = FLARE_TONES.get(row["class"].lstrip("~")[:1])
                image = row.get("image")
                table.add_event_row(
                    [
                        hhmm(row["peak"]), _span_text(row), row["list"], row["class"] or "—", row["flux"] or "—",
                        row["frequency"] or "—", "Open" if image else "—", row["detail"] or "—",
                    ],
                    {3: tone, SPECTRUM_COLUMN: "link" if image else None},
                    {SPECTRUM_COLUMN: SPECTRUM_TIP, 7: row["detail"]} if image else {7: row["detail"]},
                    {0: row["peak"]},
                )
                table.item(table.rowCount() - 1, SPECTRUM_COLUMN).setData(Qt.UserRole, image)
        table.set_empty_text("Nothing in the flare lists for this date")
        self._filter_lists()
        self.lists_note.setText(" ".join(part for part in said if part))
        features = (self.day_records.get("hek") or {}).get("features") or ()
        self.lists_note.setToolTip("\n".join(
            f"{feature['name'].capitalize()}: {', '.join(feature['where'])}" for feature in features
        ))

    @staticmethod
    def _hek_sentence(record):
        parts = [
            f"{feature['count']} {feature['name'].rstrip('s') if feature['count'] == 1 else feature['name']}"
            f" ({feature['method']})"
            for feature in record["features"]
        ]
        waves = len(record["rows"])
        if not parts:
            return "" if waves else "HEK has no features for this date."
        return f"HEK at 12:00 UTC: {', '.join(parts)}."

    # Asking

    def _ask_day_records(self, day, only_cached):
        self._leave_day_records()
        self.day_records = {}
        self._clear_record_views("Fetching…" if not only_cached else "")
        self.records_worker = DayRecords(day, only_cached)
        self.records_worker.found.connect(self._on_day_record)
        self.records_worker.finished.connect(lambda worker=self.records_worker: self._on_records_ended(worker))
        if not only_cached:
            self.begin_job("records", "Fetching the indices and flare lists…", self._cancel_day_records)
        self.records_worker.start()

    def _leave_day_records(self):
        worker, self.records_worker = self.records_worker, None
        if worker is not None:
            abandon(worker)
            self.end_job("records")

    def _records_stopped(self):
        self._leave_day_records()
        self._say_not_fetched()

    def _cancel_day_records(self):
        self._records_stopped()
        self._cancel_goes_day()

    def _on_records_ended(self, worker):
        if self.records_worker is worker:
            self._records_stopped()

    def _say_not_fetched(self):
        """A date shown from the cache asks no archive, and a cancel stops
        asking: what is not here says so, not that it is on its way."""
        missing = {
            "protons": lambda: self.day_band.set_protons_text(NOT_FETCHED),
            "kp": lambda: self.indices.kp.clear(NOT_FETCHED),
            "dst": lambda: self.indices.dst.clear(NOT_FETCHED),
            "wind": lambda: [strip.clear(NOT_FETCHED) for strip in self.indices.strips[2:]],
        }
        for key, say in missing.items():
            if key not in self.day_records:
                say()
        if not {"eovsa", "stix", "hek"} & set(self.day_records):
            self.lists_table.set_empty_text(NOT_FETCHED)

    def _clear_record_views(self, text=""):
        self.day_records = {}
        self.day_band.set_protons_text(text)
        self.indices.clear(text)
        self.lists_table.setRowCount(0)
        self.lists_note.setText("")
        self.lists_note.setToolTip("")
        self.lists_table.set_empty_text(text or "Fetch a date to see its flare lists")
        self.wind_card.hide()
        self.forecast_card.hide()
        self.records_status.setText("")

    def _on_day_record(self, day, key, record):
        if day != self.shown_date:
            return  # the date has changed since
        self.day_records[key] = record
        if key in ("eovsa", "stix", "hek"):
            self._show_lists()
        else:
            getattr(self, f"_show_{key}_record")(record)
            self._say_record_status()

    # Each record in its place

    def _show_protons_record(self, record):
        if "said" in record:
            self.day_band.set_protons_text(record["said"])
        else:
            self.day_band.set_protons(record)

    def _show_kp_record(self, record):
        strip = self.indices.kp
        if "said" in record:
            kp = getattr(getattr(self, "shown_conditions", None), "kp_index", None)
            if kp is not None and kp.kp_values:  # SWPC's estimate, the card has it already
                strip.set_values((list(kp.kp_values) + [None] * 8)[:8], None)
                return
            strip.clear(record["said"], record.get("trouble", False))
            return
        strip.set_values(record["kp"], record["hp30"])
        known = [value for value in record["kp"] if value is not None]
        self.geo_kp_max_label.setText(kp_text(max(known)))
        self.geo_kp_avg_label.setText(f"{sum(known) / len(known):.1f}")
        self.geo_kp_vals_label.setText(" ".join(kp_text(value) for value in known))
        peak = max(known)
        self.geo_storm_label.setText(storm_words(peak))
        set_cell_tone(self.geo_storm_label, storm_tone(peak))
        if record["ap"] is not None:
            self.geo_ap_label.setText(f"{record['ap']:.0f}")

    def _show_dst_record(self, record):
        strip = self.indices.dst
        if "said" in record:
            strip.clear(record["said"], record.get("trouble", False))
        else:
            strip.set_values(record["values"], 60)

    def _show_wind_record(self, record):
        strips = self.indices.speed, self.indices.density, self.indices.bz
        if "said" in record:
            for strip in strips:
                strip.clear(record["said"], record.get("trouble", False))
            self.wind_card.hide()
            return
        for strip, name in zip(strips, ("speed", "density", "bz")):
            if any(value is not None for value in record[name]):
                strip.set_values(record[name], 1)
            else:
                strip.clear("No values in this record for this date")
        texts, tone = wind_summary(record)
        self.sw_speed_label.setText(texts["speed"])
        self.sw_density_label.setText(texts["density"])
        self.sw_bz_label.setText(texts["bz"])
        self.sw_temp_label.setText(texts["temperature"])
        self.sw_status_label.setText(texts["status"])
        set_cell_tone(self.sw_status_label, tone)
        self.wind_card.show()

    def _show_forecast_record(self, record):
        today = datetime.now(timezone.utc).date()
        if "said" in record or not record.get("days") or self.shown_date != today:
            self.forecast_card.hide()  # (a forecast saved on another day stays there)
            return
        self.fc_issued_label.setText(f"{record['issued']} UTC")
        self.fc_days_label.setText(", ".join(record["days"]))
        self.fc_kp_label.setText(", ".join(f"{value:.2f}" for value in record["kp"]))
        self.fc_s1_label.setText(", ".join(record["s1"]))
        self.fc_r1_label.setText(", ".join(record["r1"]))
        self.fc_r3_label.setText(", ".join(record["r3"]))
        self.forecast_card.show()

    def _say_record_status(self):
        """Whose each number is and how final it is, in one line."""
        parts = []
        protons = self.day_records.get("protons") or {}
        if "values" in protons:
            lower = " (reads lower)" if protons.get("note") else ""
            parts.append(f"Protons: {protons['satellite']}{lower}, {protons['source']}")
        kp = self.day_records.get("kp") or {}
        if "kp" in kp:
            parts.append(f"Kp: GFZ Potsdam, {kp['status']}")
        elif getattr(getattr(self, "shown_conditions", None), "kp_index", None) is not None:
            parts.append("Kp: NOAA SWPC estimate")
        dst = self.day_records.get("dst") or {}
        if "values" in dst:
            parts.append(f"Dst: WDC Kyoto, {dst['tier']}")
        wind = self.day_records.get("wind") or {}
        if "speed" in wind:
            craft = f", {wind['spacecraft']}" if wind["spacecraft"] else ""
            parts.append(f"Wind: {wind['source']}{craft}, {wind['place']}")
        self.records_status.setText(" · ".join(parts))

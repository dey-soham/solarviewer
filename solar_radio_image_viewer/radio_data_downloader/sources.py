#!/usr/bin/env python3
"""
The sources of the Radio Data Downloader, one entry each: what the window
offers for it (its stations or receivers, what a whole day means, its
processing), what the archive is asked while the form is filled in, and
how a request is downloaded. The window (radio_data_downloader_gui) knows
no source by name: a new archive is a new entry in SOURCES.

Nothing here needs Qt.
"""

from datetime import datetime, timedelta
from typing import NamedTuple

from . import ovro_lwa as lwa
from . import radio_data_downloader as rdd
from . import space_radio as space

GROUND = "On the ground"
SPACE = "In space"

OPTIONS = ("flag_channels", "flag_calibration", "divide_median", "subtract_background", "keep_raw")


class Switch(NamedTuple):
    """The processing a source offers, on or off."""

    setting: str  # where its state is kept
    default: bool
    title: str
    aside: str
    on: str  # what is done when on
    off: str


def _hours_text(hours):
    first, last = hours
    return f"{first:02d}–{last:02d} UTC"


def _years_text(first, last=None):
    return f"{first}–{last}" if last else f"{first} to now"


def _midnight(day):
    return datetime.combine(day, datetime.min.time())


class Source:
    key = ""  # in the settings and in a request; its folder under the day
    name = ""  # in the list of sources
    group = GROUND
    tip = ""
    picks = "Station"  # what its second list chooses
    listed = False  # the choices are what the archive lists for the window
    unit = "files"  # what a download counts
    seconds_each = 1.0  # requests and processing per unit, for the time estimate
    bursts = "ecallisto"  # the burst list that goes with it: "noaa" (its station's reports) or "ecallisto"
    observing_day = False  # a whole day is the instrument's observing day, not 00 to 24 UT
    switch = None
    options = ()
    timeline = False  # the files are shown across the window

    def choices(self):
        """[(text, value, tooltip)] of its second list."""
        return []

    def note(self, choice):
        """A line about what is chosen."""
        return ""

    def day_note(self, choice):
        return "00:00 to 24:00 UTC"

    def day_window(self, choice, day):
        """(start, end) of a whole day, UT."""
        return _midnight(day), _midnight(day) + timedelta(days=1) - timedelta(seconds=1)

    def folder(self, choice):
        return self.key

    def request(self, choice, start, end, day):
        """What the archive is asked for the form (a tuple that begins with
        the source's key): the same request is not asked twice."""
        return (self.key, choice, start, end, day)

    def ask(self, request):
        """The archive's answer to ``request``. Runs in a thread; raises
        ArchiveUnreachable when it cannot be asked."""
        raise NotImplementedError

    def estimate(self, answer, choice, start, end, on):
        """What a download would be: files, spectra and size_mb (and what
        else the summary shows)."""
        raise NotImplementedError

    def no_data(self, answer, choice, start, end, day):
        """Why the archive has nothing for the form."""
        return "The archive has no file for this window"

    def night(self, choice, start, end):
        """Said when the window falls outside the instrument's hours."""
        return ""

    def describe(self, choice, estimate):
        """(name, aside, title of the row below, its text) for the summary."""
        return self.name, "", "Observes", ""

    def download(self, p, **hooks):
        """Download and write the spectra of a request; the FITS paths."""
        raise NotImplementedError


# ---------------------------------------------------------------------- RSTN
class Rstn(Source):
    key = "rstn"
    name = "RSTN spectrographs"
    tip = ("The US Air Force's Radio Solar Telescope Network spectrographs:\n"
           "Learmonth, San Vito, Palehua, Sagamore Hill and Holloman")
    setting = "rstn_station"
    seconds_each = 4.0
    bursts = "noaa"
    observing_day = True
    switch = Switch(
        "clean", True, "Clean the spectrum", "flag and fill",
        "Known bad channels and calibration periods blanked, then filled in along frequency",
        "The levels as recorded (0–255), nothing blanked",
    )
    options = ("flag_channels", "flag_calibration", "divide_median", "keep_raw")
    band = "25–180 MHz every 3 s"

    @staticmethod
    def _years(site):
        return _years_text(site["first_year"], site["last_year"])

    def choices(self):
        out = []
        for name, site in rdd.RSTN_SITES.items():
            years = f" · {self._years(site)}" if site["last_year"] else ""
            out.append((f"{name} · {site['place']}{years}", name, ""))
        return out

    def note(self, station):
        site = rdd.RSTN_SITES[station]
        return f"{self.band} · {self._years(site)} · observes about {_hours_text(site['hours'])}"

    def day_note(self, station):
        return f"its observing day, about {_hours_text(rdd.RSTN_SITES[station]['hours'])}"

    def day_window(self, station, day):
        before, after = rdd.RSTN_SITES[station]["span"]
        return _midnight(day) + timedelta(hours=before), _midnight(day) + timedelta(hours=after)

    def folder(self, station):
        return station.lower().replace(" ", "-")

    def ask(self, request):
        _, station, start, end, day = request
        files = rdd.find_rstn(station, start, end, day)
        latest = None
        if not files:  # not published yet, or a gap?
            try:
                latest = rdd.rstn_latest(station)
            except rdd.ArchiveUnreachable:
                pass
        return files, latest

    def estimate(self, answer, station, start, end, on):
        files, _latest = answer
        return {"files": len(files), "spectra": 1 if files else 0, "size_mb": len(files) * rdd.RSTN_FILE_MB,
                "names": [f["name"] for f in files]}

    def no_data(self, answer, station, start, end, day):
        site = rdd.RSTN_SITES[station]
        latest = answer[1] if answer else None
        year = (day or start.date()).year
        if day is None and not rdd.rstn_observes(station, start, end):
            return f"{station} does not observe in this window (about {_hours_text(site['hours'])})"
        if year < site["first_year"] or (site["last_year"] and year > site["last_year"]):
            return f"{station}'s archive covers {self._years(site)}"
        if latest is not None and (day or end.date()) > latest:
            return (f"Not published yet: {station}'s archive ends on {latest:%Y-%m-%d}. NOAA's burst "
                    "reports come in real time; the spectra follow weeks later")
        return f"The archive has no {station} file for this day (the station may have been down)"

    def night(self, station, start, end):
        if rdd.rstn_observes(station, start, end):
            return ""
        return f"{station} observes about {_hours_text(rdd.RSTN_SITES[station]['hours'])}; this window falls in its night."

    def describe(self, station, estimate):
        site = rdd.RSTN_SITES[station]
        return station, site["place"], "Observes", f"about {_hours_text(site['hours'])}"

    def download(self, p, **hooks):
        path = rdd.rstn_spectrum(
            p["choice"], p["start"], p["end"], p["output_dir"], whole_day=p["whole_day"],
            do_flag=p["on"] and p["flag_channels"], flag_cal_time=p["on"] and p["flag_calibration"],
            bkg_sub=p["divide_median"], keep_raw=p["keep_raw"], **hooks,
        )
        return [path] if path else []


# ---------------------------------------------------------------- e-CALLISTO
class Ecallisto(Source):
    key = "ecallisto"
    name = "e-CALLISTO"
    tip = "The worldwide e-CALLISTO network of solar radio spectrometers"
    setting = "ecallisto_station"
    listed = True
    seconds_each = 0.6
    timeline = True
    switch = Switch(
        "join", True, "Join into one spectrum per receiver", "cut to the window",
        "The 15 min files of each focus code joined and cut to the window",
        "One spectrum per 15 min file, as the archive keeps them",
    )
    options = ("subtract_background", "keep_raw")
    every = "*"  # the choice that stands for every station

    def note(self, station):
        return (f"Each station its own band, about 45–870 MHz · every 0.25 s · 15 min files · "
                f"{rdd.ECALLISTO_FIRST_YEAR} to now")

    def folder(self, station):
        return "ecallisto_" + ("all" if station in (self.every, "", None) else station).lower()

    def request(self, station, start, end, day):
        return (self.key, start, end)  # every station's files: choosing another does not ask again

    def ask(self, request):
        _, start, end = request
        return rdd.ecallisto_files(start, end)

    def listing(self, files):
        """([(text, value, tooltip)], the value to choose when nothing is
        wanted) of the stations with files in the window."""
        counts, receivers = {}, {}
        for item in files:
            counts[item["station"]] = counts.get(item["station"], 0) + 1
            receivers.setdefault(item["station"], set()).add(item["focus"])
        if not counts:
            return [("No station has files in this window", "", "")], ""
        items = []
        for name in sorted(counts, key=str.upper):
            n, r = counts[name], len(receivers[name])
            text = f"{name} · {n} file{'s' if n > 1 else ''}" + (f" · {r} receivers" if r > 1 else "")
            items.append((text, name, "Focus codes " + ", ".join(sorted(receivers[name]))))
        items.append((f"Every station · {len(files)} files", self.every, ""))
        return items, max(counts, key=lambda s: (counts[s], s))

    def _of(self, files, station):
        return [f for f in files if station == self.every or f["station"].upper() == station.upper()]

    def estimate(self, files, station, start, end, on):
        if not station:  # the stations are not listed yet, or there are none
            return {} if files else {"files": 0, "spectra": 0}
        files = self._of(files, station)
        receivers = sorted({(f["station"], f["focus"]) for f in files})
        window = (end - start).total_seconds()
        starts = sorted({f["start"] for f in files})
        return {
            "files": len(files), "spectra": len(receivers) if on else len(files),
            "size_mb": len(files) * rdd.ECALLISTO_FILE_MB, "receivers": receivers,
            "positions": [max(0.0, (s - start).total_seconds()) / window for s in starts] if window > 0 else [],
        }

    def no_data(self, answer, station, start, end, day):
        return "No e-CALLISTO station has files in this window"

    def describe(self, station, estimate):
        receivers = estimate.get("receivers") or []
        name = "every station" if station == self.every else (station or "—")
        focus = ", ".join(sorted({f for _, f in receivers})) if receivers and station not in ("", self.every) else ""
        return name, "e-CALLISTO", "Receivers", f"focus code{'s' if len(receivers) > 1 else ''} {focus}" if focus else ""

    def download(self, p, **hooks):
        station = None if p["choice"] == self.every else p["choice"]
        return rdd.ecallisto_spectra(
            p["start"], p["end"], station, p["output_dir"], join=p["on"], crop=p["on"],
            subtract_background=p["subtract_background"], keep_raw=p["keep_raw"], **hooks,
        )


# ------------------------------------------------------------------ OVRO-LWA
class OvroLwa(Source):
    key = "lwa"
    name = "OVRO-LWA"
    tip = "The Long Wavelength Array at Owens Valley, California:\n15–85 MHz, read out of its daily file for the time asked"
    setting = "lwa_product"
    picks = "Product"
    unit = "channels"
    seconds_each = 0.03
    observing_day = True

    def choices(self):
        return [("Total intensity", "I", "")]

    def note(self, choice):
        return (f"15–85 MHz in {lwa.LWA_CHANNELS} channels every 0.26 s · {lwa.LWA_FIRST:%Y-%m} to now · "
                f"observes about {_hours_text(lwa.LWA_HOURS)}")

    def day_note(self, choice):
        return f"its observing day, about {_hours_text(lwa.LWA_HOURS)} (about 1 GB)"

    def day_window(self, choice, day):
        before, after = lwa.LWA_SPAN
        return _midnight(day) + timedelta(hours=before), _midnight(day) + timedelta(hours=after)

    def folder(self, choice):
        return "ovro-lwa"

    def request(self, choice, start, end, day):
        return (self.key, start, end, day)

    def ask(self, request):
        _, start, end, day = request
        files = lwa.day_files(start, end, day)
        latest = None
        today = datetime.utcnow().date()
        if not files and (day or end.date()) >= today - timedelta(days=14):  # not published yet?
            try:
                latest = lwa.latest_day(today)
            except rdd.ArchiveUnreachable:
                pass
        return files, latest

    def estimate(self, answer, choice, start, end, on):
        files, _latest = answer
        whole = len(files) == 1 and start <= files[0].start and end >= files[0].end
        megabytes = sum((f.samples if whole else lwa.samples_in(f, start, end)) * f.channels * f.width for f in files) / 1e6
        return {"files": len(files), "spectra": 1 if files else 0, "size_mb": megabytes,
                "units": sum(f.channels for f in files), "names": [f.name for f in files]}

    def no_data(self, answer, choice, start, end, day):
        latest = answer[1] if answer else None
        if (day or end.date()) < lwa.LWA_FIRST:
            return f"OVRO-LWA's daily spectra start on {lwa.LWA_FIRST:%Y-%m-%d}"
        if day is None and not lwa.lwa_observes(start, end):
            return f"OVRO-LWA does not observe in this window (about {_hours_text(lwa.LWA_HOURS)})"
        if latest is not None and (day or start.date()) > latest:
            return f"Not published yet: OVRO-LWA's archive ends on {latest:%Y-%m-%d}"
        return "The archive has no OVRO-LWA samples for this window (the array may have been down)"

    def night(self, choice, start, end):
        if lwa.lwa_observes(start, end):
            return ""
        return f"OVRO-LWA observes about {_hours_text(lwa.LWA_HOURS)}; this window falls in its night."

    def describe(self, choice, estimate):
        return "OVRO-LWA", "Owens Valley, California", "Observes", f"about {_hours_text(lwa.LWA_HOURS)}"

    def download(self, p, **hooks):
        return lwa.lwa_spectrum(p["start"], p["end"], p["output_dir"], whole_day=p["whole_day"], **hooks)


# ---------------------------------------------------------------- spacecraft
class Spacecraft(Source):
    group = SPACE
    seconds_each = 3.0
    options = ("keep_raw",)

    def __init__(self, mission, picks="Receiver"):
        self.config = space.MISSIONS[mission]
        self.key = mission
        self.name = self.config["name"]
        self.tip = self.config["about"]
        self.setting = f"{mission}_receiver"
        self.picks = picks

    def choices(self):
        return [(text, key, "") for key, text, _receivers in self.config["choices"]]

    def _receivers(self, choice):
        return space.receivers_of(self.key, choice)

    def note(self, choice):
        parts = []
        for receiver in self._receivers(choice):
            settings = self.config["receivers"][receiver]
            parts.append(f"{settings['label']}: {settings['note']}" if len(self._receivers(choice)) > 1 else settings["note"])
        return " · ".join(parts + [_years_text(self.config["first_year"]), "daily files from NASA's SPDF"])

    def folder(self, choice):
        return self.config["slug"]

    def request(self, choice, start, end, day):
        return (self.key, self._receivers(choice), start, end)

    def ask(self, request):
        _, receivers, start, end = request
        files = space.spacecraft_files(self.key, receivers, start, end)
        latest = None
        if not files:  # not published yet, or a gap?
            try:
                latest = space.latest_day(self.key, receivers[-1])
            except rdd.ArchiveUnreachable:
                pass
        return files, latest

    def estimate(self, answer, choice, start, end, on):
        files, _latest = answer
        sizes = {f["url"]: f["mb"] for f in files}  # (two receivers can share a file)
        receivers = [r for r in self._receivers(choice) if any(f["receiver"] == r for f in files)]
        return {"files": len(sizes), "spectra": len(receivers), "size_mb": sum(sizes.values()),
                "names": sorted(f["name"] for f in files), "labels": [self.config["receivers"][r]["label"] for r in receivers]}

    def no_data(self, answer, choice, start, end, day):
        latest = answer[1] if answer else None
        name = self.name
        ended = [self.config["receivers"][r].get("last") for r in self._receivers(choice)]
        if all(ended) and start.date() > max(ended):
            label = " and ".join(self.config["receivers"][r]["label"] for r in self._receivers(choice))
            return f"{label} has no data after {max(ended):%Y-%m-%d}"
        if end.year < self.config["first_year"]:
            return f"{name}'s archive starts in {self.config['first_year']}"
        if latest is not None and start.date() > latest:
            return f"Not published yet: {name}'s archive ends on {latest:%Y-%m-%d} (the team publishes weeks to months later)"
        return f"The archive has no {name} file for this window"

    def describe(self, choice, estimate):
        labels = [self.config["receivers"][r]["label"] for r in self._receivers(choice)]
        return self.name, "NASA SPDF", self.picks + ("s" if len(labels) > 1 else ""), ", ".join(labels)

    def download(self, p, **hooks):
        return space.spacecraft_spectra(
            self.key, self._receivers(p["choice"]), p["start"], p["end"], p["output_dir"],
            whole_day=p["whole_day"], keep_raw=p["keep_raw"], **hooks,
        )


SOURCES = {
    source.key: source
    for source in (
        Rstn(), Ecallisto(), OvroLwa(),
        Spacecraft("wind"), Spacecraft("stereo", picks="Spacecraft"), Spacecraft("psp"), Spacecraft("solo"),
    )
}
DEFAULT = "rstn"

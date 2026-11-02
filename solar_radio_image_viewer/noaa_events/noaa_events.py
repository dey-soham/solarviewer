#!/usr/bin/env python3
"""
NOAA Solar Events Parser - Core module for fetching and parsing NOAA solar events.

SWPC's edited events of a day are recreated until three days after it, so a
file is told by its own header: its date, and when it was made. Each place
below can hold an early copy, so the first file made late enough is the one
used; failing that, the latest one to be had.

Data sources, for files from 1996-07-31:
    https://services.swpc.noaa.gov/text/solar-geophysical-event-reports.txt (today's)
    https://www.ngdc.noaa.gov/stp/space-weather/swpc-products/daily_reports/solar_event_reports/
    ftp://ftp.swpc.noaa.gov/pub/indices/events/ (from 2015-06-29, and the last days)
    ftp://ftp.swpc.noaa.gov/pub/warehouse/{YYYY}/{YYYY}_events.tar.gz
    https://solarmonitor.org/data/{YYYY}/{MM}/{DD}/meta/noaa_events_raw_{YYYYMMDD}.txt
"""

import ftplib
import io
import logging
import re
import tarfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Dict, Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

SWPC_TODAY = "https://services.swpc.noaa.gov/text/solar-geophysical-event-reports.txt"
NCEI_EVENTS = (
    "https://www.ngdc.noaa.gov/stp/space-weather/swpc-products/daily_reports/"
    "solar_event_reports/{day:%Y}/{day:%m}/{day:%Y%m%d}events.txt"
)
SWPC_EVENTS = "ftp://ftp.swpc.noaa.gov/pub/indices/events/{day:%Y%m%d}events.txt"
SWPC_YEAR = "ftp://ftp.swpc.noaa.gov/pub/warehouse/{day:%Y}/{day:%Y}_events.tar.gz"
SOLARMONITOR_EVENTS = "https://solarmonitor.org/data/{day:%Y}/{day:%m}/{day:%d}/meta/noaa_events_raw_{day:%Y%m%d}.txt"
EVENTS_FIRST = date(1996, 7, 31)  # the first file of the edited events
SWPC_FILES_FIRST = date(2015, 6, 29)  # of the folder of single files
EVENTS_SETTLE_DAYS = 3  # recreated daily until then
EVENTS_LAST_LOOK_DAYS = 7  # a file older than this has had its last edit, whatever it says
EVENTS_TIMEOUT = 20  # seconds, of each place asked
SOURCE_NAMES = {
    "swpc": "NOAA/SWPC",
    "ncei": "NOAA/SWPC, from the NCEI archive",
    "solarmonitor": "NOAA/SWPC, from SolarMonitor's copy",
}


# Event type definitions with descriptions
EVENT_TYPES = {
    "XRA": {
        "name": "X-ray Flare",
        "description": "Solar X-ray event observed by GOES spacecraft (1-8 Ångström)",
        "category": "xray",
    },
    "FLA": {
        "name": "Optical Flare",
        "description": "Optical flare observed in H-alpha wavelength",
        "category": "optical",
    },
    "RSP": {
        "name": "Sweep Radio Burst",
        "description": "Sweep-frequency radio burst (Type II/III/IV/V)",
        "category": "radio",
    },
    "RBR": {
        "name": "Fixed-freq Radio Burst",
        "description": "Fixed-frequency radio burst (245, 410, 610 MHz, etc.)",
        "category": "radio",
    },
    "RNS": {
        "name": "Radio Noise Storm",
        "description": "Prolonged enhanced radio emission from sunspot groups",
        "category": "radio",
    },
    "CME": {
        "name": "Coronal Mass Ejection",
        "description": "Massive plasma/magnetic field ejection from corona",
        "category": "cme",
    },
}

# X-ray flare class tones (for UI)
FLARE_CLASS_TONES = {
    "A": None,  # minor
    "B": "success",  # weak
    "C": "warning",  # small
    "M": "warning",  # moderate
    "X": "error",  # major
}

# Observatory codes
OBSERVATORY_CODES = {
    "G16": "GOES-16",
    "G19": "GOES-19",
    "G18": "GOES-18",
    "G17": "GOES-17",
    "G15": "GOES-15",
    "LEA": "Learmonth",
    "SVI": "San Vito",
    "PAL": "Palehua",
    "HOL": "Holloman",
    "SAG": "Sagamore Hill",
    "RAM": "Ramey",
    "CUL": "Culgoora",
}


REGION_COLUMNS = (76, 77, 78)  # where Reg# starts: the files keep to it, 1996 to 2026; a CME's long Loc/Frq can move it


# Event ID is up to 5 digits (e.g., 40, 250, 4470). Any time can be marked B, A,
# C or U (before, after, uncertain); the fields after Loc/Frq can be empty. End
# and Obs can be empty too, Obs be cut to GOES's 12, run into End as a station's
# number, or be ////.
_TIME = r"[ABCEU]?\d{4}|////"
EVENT_LINE = re.compile(
    rf"(\d{{1,5}})\s*\+?\s+({_TIME})\s+({_TIME})\s+(?:({_TIME})\s*)?"
    r"(?:(\w{2,5}|////)\s*)?(\S+)\s+(\w{2,3})\s+(\S+)\s*(.*)"
)


def _clock(text: str) -> str:
    """The HHMM of a time field, without its B (before), A (after) or U
    (uncertain) mark."""
    return text.lstrip("ABEU")


@dataclass
class SolarEvent:
    """Represents a single NOAA solar event."""

    event_id: str
    is_followup: bool  # Has '+' marker
    begin_time: Optional[str]  # HHMM format or None
    max_time: Optional[str]  # HHMM format or None (can be "////")
    end_time: Optional[str]  # HHMM format or None
    observatory: str
    quality: str
    event_type: str  # XRA, FLA, RSP, RBR, RNS, CME
    location_or_freq: str
    particulars: str
    active_region: Optional[str]
    raw_line: str = ""

    @property
    def type_info(self) -> Dict[str, Any]:
        """Get event type metadata."""
        return EVENT_TYPES.get(
            self.event_type,
            {
                "name": self.event_type,
                "description": "Unknown event type",
                "category": "other",
            },
        )

    @property
    def observatory_name(self) -> str:
        """Get full observatory name."""
        return OBSERVATORY_CODES.get(self.observatory, self.observatory)

    @property
    def begin_time_formatted(self) -> str:
        """Format begin time as HH:MM."""
        if not self.begin_time or self.begin_time == "////":
            return "—"
        t = _clock(self.begin_time)
        if len(t) == 4:
            return f"{t[:2]}:{t[2:]}"
        return self.begin_time

    @property
    def max_time_formatted(self) -> str:
        """Format max time as HH:MM."""
        if not self.max_time or self.max_time == "////":
            return "—"
        t = _clock(self.max_time)
        if len(t) == 4:
            return f"{t[:2]}:{t[2:]}"
        return self.max_time

    @property
    def end_time_formatted(self) -> str:
        """Format end time as HH:MM."""
        if not self.end_time or self.end_time == "////":
            return "—"
        t = _clock(self.end_time)
        if len(t) == 4:
            return f"{t[:2]}:{t[2:]}"
        return self.end_time

    @property
    def time_range(self) -> str:
        """Get formatted time range string."""
        begin = self.begin_time_formatted
        end = self.end_time_formatted
        if begin == "—" and end == "—":
            return "—"
        return f"{begin} – {end}"

    @property
    def duration_minutes(self) -> Optional[int]:
        """Calculate event duration in minutes."""
        try:
            if not self.begin_time or not self.end_time:
                return None
            begin = _clock(self.begin_time)
            end = _clock(self.end_time)
            if begin == "////" or end == "////":
                return None
            begin_mins = int(begin[:2]) * 60 + int(begin[2:])
            end_mins = int(end[:2]) * 60 + int(end[2:])
            if end_mins < begin_mins:
                end_mins += 24 * 60  # Crosses midnight
            return end_mins - begin_mins
        except (ValueError, IndexError):
            return None

    @property
    def flare_class(self) -> Optional[str]:
        """Extract flare class for XRA events (e.g., 'M1.9')."""
        if self.event_type == "XRA":
            # Particulars contains something like "M1.9    1.5E-02"
            parts = self.particulars.split()
            if parts:
                return parts[0]
        return None

    @property
    def flare_class_letter(self) -> Optional[str]:
        """Get just the letter class (A, B, C, M, X)."""
        fc = self.flare_class
        if fc and len(fc) > 0:
            return fc[0].upper()
        return None

    @property
    def flare_class_tone(self) -> Optional[str]:
        """Get the tone of the flare class."""
        return FLARE_CLASS_TONES.get(self.flare_class_letter)

    @property
    def optical_class(self) -> Optional[str]:
        """Extract optical flare class for FLA events (e.g., 'SF', '1N')."""
        if self.event_type == "FLA":
            parts = self.particulars.split()
            if parts:
                return parts[0]
        return None

@dataclass
class ECallistoBurst:
    """Represents a single e-CALLISTO radio burst event."""
    date: str  # YYYYMMDD
    time_range: str  # HH:MM-HH:MM
    burst_type: str  # II, III, IV, etc.
    stations: str  # Comma-separated list of stations
    raw_line: str = ""

    @property
    def begin_time(self) -> str:
        """Get begin time in HH:MM format."""
        if "-" in self.time_range:
            return self.time_range.split("-")[0]
        return self.time_range

    @property
    def end_time(self) -> str:
        """Get end time in HH:MM format."""
        if "-" in self.time_range:
            return self.time_range.split("-")[1]
        return self.time_range

    @property
    def stations_list(self) -> List[str]:
        """Get stations as a list."""
        return [s.strip() for s in self.stations.split(",")]

class EventFile(str):
    """The text of a day's events file, and which place it came from."""

    source = ""


def _utc_today():
    return datetime.now(timezone.utc).date()


def _file_day(text):
    """The date a file says it is of, ``None`` if it says none."""
    found = re.search(r"^:Date:\s*(\d{4}) (\d\d) (\d\d)", text, re.M)
    if found:
        fields, form = " ".join(found.groups()), "%Y %m %d"
    else:
        found = re.search(r"^EDITED EVENTS for (\d{4} \w{3} \d\d)", text, re.M | re.I)
        fields, form = (found.group(1), "%Y %b %d") if found else (None, None)
    try:
        return datetime.strptime(fields, form).date() if fields else None
    except ValueError:
        return None


def _file_made(text):
    """The day a file was made, ``None`` if it does not say."""
    found = re.search(r"^:Created:\s*(\d{4} \w{3} \d\d) \d{4} UT", text, re.M)
    if found:
        fields, form = found.group(1), "%Y %b %d"
    else:
        found = re.search(r"^\d{4} UT (\d{1,2} \w{3} \d{4})", text, re.M)  # before 1999
        fields, form = (found.group(1), "%d %b %Y") if found else (None, None)
    try:
        return datetime.strptime(fields, form).date() if fields else None
    except ValueError:
        return None


def _settled(day, made):
    return made is not None and (made - day).days >= EVENTS_SETTLE_DAYS


def _read_web(url):
    """``url``'s text, fresh, ``None`` if it has no such file. The session's
    cache would hand back the copy of hours ago."""
    try:
        from ..utils import get_global_session
    except ImportError:
        from solar_radio_image_viewer.utils import get_global_session

    try:
        return get_global_session().get(url, use_cache=False, timeout=EVENTS_TIMEOUT).text
    except Exception as e:
        if getattr(getattr(e, "response", None), "status_code", None) == 404:
            return None
        raise


def _read_ftp(url):
    """``url``'s bytes, ``None`` if the server has no such file."""
    where = urlsplit(url)
    chunks = []
    with ftplib.FTP(where.hostname, timeout=EVENTS_TIMEOUT) as server:
        server.login()
        try:
            server.retrbinary(f"RETR {where.path}", chunks.append)
        except ftplib.error_perm as e:
            if str(e).startswith("550"):
                return None
            raise
    return b"".join(chunks)


def _read_ftp_text(url):
    data = _read_ftp(url)
    return None if data is None else data.decode("latin-1")


def _read_year_file(url, day):
    """``day``'s file out of SWPC's file of a year."""
    data = _read_ftp(url)
    if data is None:
        return None
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        wanted = f"{day:%Y%m%d}events.txt"
        for member in archive:
            if member.isfile() and member.name.rsplit("/", 1)[-1] == wanted:
                return archive.extractfile(member).read().decode("latin-1")
    return None


def _event_places(day):
    """``(source, read())`` of where ``day``'s file can be, in order."""
    today = _utc_today()
    places = []
    if day == today:
        places.append(("swpc", lambda: _read_web(SWPC_TODAY)))
    elif day >= EVENTS_FIRST and day < today:
        places.append(("ncei", lambda: _read_web(NCEI_EVENTS.format(day=day))))
    if day >= SWPC_FILES_FIRST:
        places.append(("swpc", lambda: _read_ftp_text(SWPC_EVENTS.format(day=day))))
    elif day >= EVENTS_FIRST:
        places.append(("swpc", lambda: _read_year_file(SWPC_YEAR.format(day=day), day)))
    return places


def fetch_events_raw(event_date: date) -> Optional[str]:
    """
    Fetch the raw events file of a date, as an ``EventFile``.

    Args:
        event_date: The date to fetch events for

    Returns:
        The best file of the date, "" if an archive sent an empty one, None if
        there is none or the fetch failed. Nothing is taken for the date's
        unless it says so.
    """
    best, answered, empty = None, True, False
    for source, read in _event_places(event_date):
        try:
            text = read()
        except (OSError, ValueError, EOFError, ftplib.Error, tarfile.TarError) as e:
            logger.info("%s has no events file of %s: %s", source, event_date, e)
            answered = False
            continue
        if text is None:
            continue
        empty = empty or not text.strip()
        if _file_day(text) != event_date:
            continue
        found = EventFile(text)
        found.source = source
        if best is None or (_file_made(found) or date.min) > (_file_made(best) or date.min):
            best = found
        # (none fresher can be had: SWPC remakes a recent file once in hours)
        if _settled(event_date, _file_made(found)) or _file_made(found) == _utc_today():
            return found
    if best is not None and answered:
        return best
    # SWPC could not be asked: SolarMonitor's copy of a date can be a late one
    try:
        text = _read_web(SOLARMONITOR_EVENTS.format(day=event_date))
    except Exception as e:
        print(f"Error fetching NOAA events: {e}")
        return best
    if text is None:
        return best
    if not text.strip():
        return best if best is not None else ""
    if _file_day(text) != event_date:
        return best if best is not None else ("" if empty else None)
    if best is None or (_file_made(text) or date.min) > (_file_made(best) or date.min):
        best = EventFile(text)
        best.source = "solarmonitor"
    return best


def parse_events(raw_text: str) -> List[SolarEvent]:
    """
    Parse raw NOAA events text into structured SolarEvent objects.

    Args:
        raw_text: Raw text from NOAA events file

    Returns:
        List of SolarEvent objects
    """
    events = []

    for line in raw_text.split("\n"):
        # Skip comments and empty lines
        raw, line = line, line.strip()
        if not line or line.startswith("#") or line.startswith(":"):
            continue

        # Parse event line
        # Format: Event# +  Begin  Max    End   Obs  Q  Type  Loc/Frq  Particulars  Reg#
        # Example: 4470 +     1235   1246      1258  G16  5   XRA  1-8A      M1.9    1.5E-02   3455

        try:
            # Event ID (first 4-5 chars)
            event_id = line[:5].strip()

            # Check for '+' marker (follow-up event)

            match = EVENT_LINE.match(line)

            if match:
                event_id = match.group(1)
                begin_time = match.group(2) if match.group(2) != "////" else None
                max_time = match.group(3) if match.group(3) != "////" else None
                end_time = match.group(4) if match.group(4) != "////" else None
                observatory = (match.group(5) or "").strip("/")
                quality = match.group(6)
                event_type = match.group(7)
                location_or_freq = match.group(8)
                rest = match.group(9).strip()

                # Split rest into particulars and region
                parts = rest.split()
                # By its column: a burst's peak flux and a shock's speed can
                # be four digits too, and end the line when it has no region
                if parts and parts[-1].isdigit() and len(parts[-1]) in (4, 5) and raw.rfind(parts[-1]) in REGION_COLUMNS:
                    active_region = parts[-1]
                    particulars = " ".join(parts[:-1])
                else:
                    active_region = None
                    particulars = rest

                events.append(
                    SolarEvent(
                        event_id=event_id,
                        is_followup="+" in line[4:7],
                        begin_time=begin_time,
                        max_time=max_time,
                        end_time=end_time,
                        observatory=observatory,
                        quality=quality,
                        event_type=event_type,
                        location_or_freq=location_or_freq,
                        particulars=particulars,
                        active_region=active_region,
                        raw_line=raw.rstrip(),  # (the columns of a line start where it does)
                    )
                )
        except Exception:
            # Skip malformed lines
            continue

    return events


class EventList(list):
    """A date's events, and the file they are from: its ``source``, the day it
    was ``created``, and whether it is ``final``, past its last edit. ``source``
    empty: saved before this was noted, from SolarMonitor's snapshot."""

    source = ""
    created = ""
    final = False

    def standing(self):
        """How much this list can be trusted, to compare it with another."""
        return (self.final, self.created)


class EmptyList(EventList):
    """The archive sent an empty file: nothing is known of the date, which is
    not the same as a list with no events."""


def standing(events):
    return events.standing() if isinstance(events, EventList) else (False, "")


def better_events(fetched, kept):
    """Which of a list just fetched and one saved earlier to show and keep: a
    copy made earlier, or none, does not replace what is known."""
    if kept is None:
        return fetched
    if fetched is None or (not fetched and kept and not standing(fetched)[0]):
        return kept
    return fetched if standing(fetched) >= standing(kept) else kept


def is_snapshot(events):
    """Whether ``events`` are only known from a copy of SolarMonitor's that
    can stop short of the day."""
    return isinstance(events, EventList) and not isinstance(events, EmptyList) and not events.final and events.source in ("", "solarmonitor")


def events_note(events, day):
    """What a reader should know of where ``events`` of ``day`` came from,
    empty for a final list. Only an ``EventList`` says."""
    if not isinstance(events, EventList) or events.final or isinstance(events, EmptyList):
        return ""
    settles = day + timedelta(days=EVENTS_SETTLE_DAYS)
    if is_snapshot(events):
        made = f" of {events.created}" if events.created else ""
        return (
            f"SolarMonitor's same-day snapshot{made}: it can miss the day's later events and "
            "their regions. Press Fetch for SWPC's final list."
        )
    made = f", as of {events.created}" if events.created else ""
    if _utc_today() < settles:
        return f"SWPC's list so far{made}: it is edited until {settles}."
    return f"An early copy of SWPC's list{made}. Press Fetch for the final one."


def fetch_and_parse_events(event_date: date) -> Optional[List[SolarEvent]]:
    """
    Fetch and parse NOAA events for a given date.

    Args:
        event_date: The date to fetch events for

    Returns:
        List of SolarEvent objects or None if fetch failed
    """
    raw_text = fetch_events_raw(event_date)
    if raw_text is None:
        return None
    if not raw_text.strip():
        return EmptyList()
    events = EventList(parse_events(raw_text))
    made = _file_made(raw_text)
    events.source = getattr(raw_text, "source", "") or "solarmonitor"
    events.created = made.isoformat() if made else ""
    # Past its last look a file stands, as it says or not: 289 of them made early never came again
    events.final = _settled(event_date, made) or (
        (_utc_today() - event_date).days > EVENTS_LAST_LOOK_DAYS and events.source != "solarmonitor"
    )
    return events


def categorize_events(events: List[SolarEvent]) -> Dict[str, List[SolarEvent]]:
    """
    Categorize events by type category.

    Returns:
        Dict with keys: 'xray', 'optical', 'radio', 'cme', 'other'
    """
    categories = {
        "xray": [],
        "optical": [],
        "radio": [],
        "cme": [],
        "other": [],
    }

    for event in events:
        category = event.type_info.get("category", "other")
        if category in categories:
            categories[category].append(event)
        else:
            categories["other"].append(event)

    return categories


def get_event_statistics(events: List[SolarEvent]) -> Dict[str, Any]:
    """
    Calculate statistics for a list of events.

    Returns:
        Dict with statistics like counts, max flare class, etc.
    """
    stats = {
        "total": len(events),
        "by_type": {},
        "max_xray_class": None,
        "max_xray_event": None,
        "active_regions": set(),
    }

    max_class_order = {"A": 0, "B": 1, "C": 2, "M": 3, "X": 4}
    max_class_value = -1

    for event in events:
        # Count by type
        t = event.event_type
        stats["by_type"][t] = stats["by_type"].get(t, 0) + 1

        # Track active regions
        if event.active_region:
            stats["active_regions"].add(event.active_region)

        # Find max X-ray class
        if event.event_type == "XRA":
            letter = event.flare_class_letter
            if letter and letter in max_class_order:
                class_val = max_class_order[letter]
                if class_val > max_class_value:
                    max_class_value = class_val
                    stats["max_xray_class"] = event.flare_class
                    stats["max_xray_event"] = event
                elif class_val == max_class_value:
                    # Compare numeric part
                    try:
                        current_num = float(event.flare_class[1:])
                        max_num = float(stats["max_xray_class"][1:])
                        if current_num > max_num:
                            stats["max_xray_class"] = event.flare_class
                            stats["max_xray_event"] = event
                    except (ValueError, IndexError):
                        pass

    stats["active_regions"] = list(stats["active_regions"])
    return stats

def fetch_ecallisto_bursts_raw(event_date: date) -> Optional[str]:
    """
    Fetch raw e-CALLISTO burst list text for the month of the given date.
    
    Source: https://soleil.i4ds.ch/solarradio/data/BurstLists/2010-yyyy_Monstein/{YYYY}/e-CALLISTO_{YYYY}_{MM}.txt
    """
    year = event_date.strftime("%Y")
    month = event_date.strftime("%m")
    
    # 2012-2019 are missing or in a different format according to user
    # Link: https://soleil.i4ds.ch/solarradio/data/BurstLists/2010-yyyy_Monstein/
    if 2012 <= event_date.year <= 2019:
        return None

    url = f"https://soleil.i4ds.ch/solarradio/data/BurstLists/2010-yyyy_Monstein/{year}/e-CALLISTO_{year}_{month}.txt"

    try:
        from ..utils import get_global_session
    except ImportError:
        try:
            from solar_radio_image_viewer.utils import get_global_session
        except ImportError:
            import requests
            session = requests.Session()
            response = session.get(url, timeout=10)
            return response.text

    session = get_global_session()

    try:
        response = session.get(url, timeout=10)
        if response.status_code == 200:
            return response.text
        return None
    except Exception as e:
        print(f"Error fetching e-CALLISTO bursts: {e}")
        return None


def parse_ecallisto_bursts(raw_text: str, event_date: date) -> List[ECallistoBurst]:
    """
    Parse e-CALLISTO burst list text into structured ECallistoBurst objects for a specific date.
    """
    bursts = []
    target_date_str = event_date.strftime("%Y%m%d")
    
    # Header skip logic
    lines = raw_text.split("\n")
    data_started = False
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        if "Date" in line and "Time" in line and "Type" in line:
            data_started = True
            continue
            
        if line.startswith("---") or line.startswith("#---"):
            data_started = True
            continue

        if line.startswith("#"):
            continue

        if data_started:
            # Format: YYYYMMDD        HH:MM-HH:MM     Type    Stations
            # Example: 20241201        02:36-02:37     III     Australia-ASSA, INDIA-GAURI
            
            # Use regex to handle variable whitespace
            # Date is 8 digits, Time is HH:MM-HH:MM or HH:MM
            pattern = r"^(\d{8})\s+(\d{2}:\d{2}-\d{2}:\d{2}|\d{2}:\d{2})\s+(\S+)\s+(.*)$"
            match = re.match(pattern, line)
            
            if match:
                b_date = match.group(1)
                if b_date == target_date_str:
                    bursts.append(ECallistoBurst(
                        date=b_date,
                        time_range=match.group(2),
                        burst_type=match.group(3),
                        stations=match.group(4),
                        raw_line=line
                    ))
            elif line.startswith(target_date_str):
                # Fallback simple split if regex fails but date matches
                parts = line.split(None, 3)
                if len(parts) >= 4:
                    bursts.append(ECallistoBurst(
                        date=parts[0],
                        time_range=parts[1],
                        burst_type=parts[2],
                        stations=parts[3],
                        raw_line=line
                    ))
                    
    return bursts


def fetch_and_parse_ecallisto_bursts(event_date: date) -> Optional[List[ECallistoBurst]]:
    """
    Fetch and parse e-CALLISTO bursts for a given date, ``None`` if the list
    could not be had: that says nothing of the date, and what was saved stands.
    """
    raw_text = fetch_ecallisto_bursts_raw(event_date)
    if not raw_text:
        return None
    return parse_ecallisto_bursts(raw_text, event_date)



if __name__ == "__main__":
    # Test with sample date
    from datetime import date

    test_date = date(2023, 10, 2)
    print(f"Fetching events for {test_date}...")

    events = fetch_and_parse_events(test_date)
    if events:
        print(f"Found {len(events)} events")

        categories = categorize_events(events)
        for cat, cat_events in categories.items():
            if cat_events:
                print(f"\n{cat.upper()} ({len(cat_events)} events):")
                for e in cat_events[:3]:
                    print(
                        f"  {e.time_range} | {e.event_type} | {e.particulars} | AR {e.active_region}"
                    )

        stats = get_event_statistics(events)
        print("\nStatistics:")
        print(f"  Max X-ray class: {stats['max_xray_class']}")
        print(f"  Active regions: {stats['active_regions']}")
    else:
        print("No events found or fetch failed")

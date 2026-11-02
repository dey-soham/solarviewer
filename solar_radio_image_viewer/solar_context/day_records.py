"""
The records of one UTC day that are not images: GOES protons, Kp and Hp30, Dst,
the solar wind, the EOVSA and STIX flare lists, HEK features and SWPC's forecast.

Each ``fetch_`` here makes a request or a few, and returns a record that can go
in a JSON file. An archive with nothing for the date raises ``NoRecord`` with a
sentence saying so, and one that cannot be asked raises ``RecordProblem``. A
value only enters a record if its own time stamp is of the date asked for.

The proton values are SWPC's own, not worked out here. The GOES-16 and later
files (NCEI, sgps-l2-avg1m) hold differential channels and only a >500 MeV
integral, and summing those to >=10 MeV does not give SWPC's number: on 2024 events
its median ran 0.96 to 1.33 times SWPC's, with twice its peak on 2024-05-10 and
twice its quiet level, with the two look directions up to five times apart.
"""

import html
import io
import math
import re
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Optional

from ..version import __version__

USER_AGENT = f"SolarViewer/{__version__} (python-requests)"
TIMEOUT = 30
MINUTES_PER_DAY = 24 * 60

ISWA_HAPI = "https://iswa.gsfc.nasa.gov/IswaSystemWebApp/hapi/data"
CDAWEB_HAPI = "https://cdaweb.gsfc.nasa.gov/hapi/data"
SWPC_PROTONS = "https://services.swpc.noaa.gov/json/goes/primary/integral-protons-7-day.json"
SWPC_FORECAST = "https://services.swpc.noaa.gov/text/3-day-forecast.txt"
NCEI_EPS = (
    "https://www.ncei.noaa.gov/data/goes-space-environment-monitor/access/avg/"
    "{first:%Y/%m}/goes{satellite}/netcdf/g{satellite}_eps_5m_{first:%Y%m%d}_{last:%Y%m%d}.nc"
)
GFZ_JSON = "https://kp.gfz.de/app/json/?start={day}T00:00:00Z&end={day}T23:59:59Z&index={index}"
DST_PAGE = "https://wdc.kugi.kyoto-u.ac.jp/dst_{tier}/{day:%Y%m}/index.html"
EOVSA_FLARES = "https://ovsa.njit.edu/eovsadata/api/flare-query.php"
STIX_FLARES = "https://datacenter.stix.i4ds.net/api/request/flare-list"
HEK_SEARCH = "https://www.lmsal.com/hek/her"

NO_SUCH_THING = (404, 410)
HAPI_NO_DATA = (1201, 1405, 1406)  # none for the time range, or outside the dataset's
PROTON_STEP = 5  # minutes, in both archives and at SWPC
ISWA_FIRST = date(2010, 4, 14)  # its first date: NCEI's files come before it
EPS_FIRST = date(1995, 1, 1)  # GOES-8's first files
# The satellite whose peaks are SWPC's in its event list (2000 to 2003 and 2003 to
# 2005). GOES-11's files have gaps until 2003-06-19 and GOES-8's run to that date,
# so each stands in for the other, and GOES-10 for both: 25 dates in April to June
# 2003 have neither. It is not their equal, so its record says so.
GOES_8_LAST = date(2003, 3, 31)
THIRD_SATELLITE = "10"
GOES_10_NOTE = (
    "GOES-10 stands in here for GOES-8 and GOES-11, and reads lower: "
    "1.4 to 2.6 times below them on the dates in May and June 2003 they share"
)
LEAST_PROTONS = 144  # of 288: with fewer a satellite gives way to one with more
S_LEVELS = (10.0, 100.0, 1e3, 1e4, 1e5)  # pfu of S1 to S5
FILLS = {"BZ_GSM": 9999.99, "flow_speed": 99999.9, "proton_density": 999.99, "T": 9999999.0}
OMNI_PARAMETERS = "BZ_GSM,flow_speed,proton_density,T"  # the server wants them in its order
KP_STATUS = {"def": "definitive", "pre": "preliminary", "now": "nowcast"}
DST_TIERS = ("final", "provisional", "realtime")
DST_NO_MONTH = (403, 404, 410)
DST_FINAL_LAST = 2020  # the year the final values reach for now: others are tried next
RECENT_LIST_DAYS = 14  # a flare list can still change for this long
SETTLE_DAYS = 3  # the protons of older dates do not change
OMNI_SETTLE_DAYS = 60
TIME_STAMP = "%Y-%m-%dT%H:%M:%S"
HEK_TIMED = {"CW": "Coronal wave", "FE": "Filament eruption"}
HEK_FEATURES = {"CH": "coronal holes", "SS": "sigmoids", "FI": "filaments"}
TAG = re.compile(r"<[^>]*>")
STORM_TAG = re.compile(r"\([GRS]\d\)")


class NoRecord(Exception):
    """The archive has nothing for this date: a sentence, not a fault."""


class RecordProblem(Exception):
    """The archive could not be asked, or sent what cannot be read."""


def _get(url, params=None, data=None, files=None, timeout=TIMEOUT, none=NO_SUCH_THING, plain=()):
    """``url``'s answer, ``None`` if the archive says it has no such thing, which
    is an answer ``none``. Only an answer of ``plain`` passes as it is: anything
    else, 403 above all, can be a fault, and a fault must not read as no data."""
    import requests

    method = requests.post if data is not None or files is not None else requests.get
    try:
        response = method(
            url, params=params, data=data, files=files, timeout=timeout, headers={"User-Agent": USER_AGENT}
        )
    except requests.RequestException as e:
        raise RecordProblem("could not be reached") from e
    if response.status_code in none:
        return None
    if response.status_code != 200 and response.status_code not in plain:
        raise RecordProblem(f"answered with HTTP {response.status_code}")
    return response


def _json(response):
    try:
        return response.json()
    except ValueError as e:
        raise RecordProblem("sent something that could not be read") from e


def _utc_today():
    return datetime.now(timezone.utc).date()


def strip_html(text):
    """The words of an archive's wrapped value."""
    return html.unescape(TAG.sub("", str(text or ""))).strip()


def finite(value):
    """Whether ``value`` is a number one can reckon with: JSON has NaN too."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _number(value):
    """A float from a value that may be wrapped in HTML, ``None`` if it is none."""
    try:
        number = float(strip_html(value))
    except ValueError:
        return None
    return number if finite(number) else None


def _hours(stamp, day):
    """Hours from 00:00 of ``day`` to an archive's time stamp, which may lie
    outside it. ``None`` if it cannot be read."""
    try:
        when = datetime.strptime(str(stamp)[:19], TIME_STAMP)
    except ValueError:
        return None
    return (when - datetime.combine(day, time())).total_seconds() / 3600


def _flux_text(value):
    return f"{value:.0f}" if value >= 100 else f"{value:.1f}" if value >= 1 else f"{value:.2f}"


def _location(latitude, longitude):
    """S17W89, from heliographic degrees."""
    try:
        latitude, longitude = float(latitude), float(longitude)
    except (TypeError, ValueError):
        return ""
    if not (finite(latitude) and finite(longitude)):
        return ""
    return f"{'N' if latitude >= 0 else 'S'}{abs(latitude):02.0f}{'W' if longitude >= 0 else 'E'}{abs(longitude):02.0f}"


def hapi_rows(server, dataset, parameters, day) -> List[List[str]]:
    """The CSV rows of ``dataset`` stamped with ``day``, the time as a
    ``datetime`` in front. A date the server does not hold gives none."""
    next_day = day + timedelta(days=1)
    response = _get(server, none=(), plain=(400, 403, 404), params={
        "id": dataset, "parameters": parameters, "format": "csv",
        "time.min": f"{day}T00:00:00Z", "time.max": f"{next_day}T00:00:00Z",
    })
    text = response.text.strip()
    if text.startswith("{"):
        if _json(response).get("status", {}).get("code") in HAPI_NO_DATA:
            return []  # HAPI's way of saying there is no data, in any HTTP status
        raise RecordProblem("said it could not answer")
    if response.status_code != 200:
        raise RecordProblem(f"answered with HTTP {response.status_code}")
    rows, read = [], not text
    for line in text.splitlines():
        cells = line.strip().split(",")
        try:
            when = datetime.strptime(cells[0][:19], TIME_STAMP)
        except ValueError:
            continue
        read = True
        if when.date() == day:
            rows.append([when, *cells[1:]])
    if not read:
        raise RecordProblem("sent something that could not be read")
    return rows


# Protons


def _proton_record(values, satellite, source, day, note=""):
    return {
        "values": values,
        "step": PROTON_STEP,
        "satellite": satellite,
        "source": source,
        "note": note,
        "settled": (_utc_today() - day).days > SETTLE_DAYS,
    }


def _empty_protons():
    return [None] * (MINUTES_PER_DAY // PROTON_STEP)


def _iswa_protons(day):
    rows = hapi_rows(ISWA_HAPI, "goesp_part_flux_P5M", "P10,satelliteProton", day)
    values = _empty_protons()
    satellites = Counter()
    for row in rows:
        flux = _number(row[1]) if len(row) > 2 else None
        if flux is None or flux < 0:  # -100000 where it has none
            continue
        values[(row[0].hour * 60 + row[0].minute) // PROTON_STEP] = flux
        satellites[row[2].strip()] += 1
    if not satellites:
        return None
    return _proton_record(values, satellites.most_common(1)[0][0], "SWPC values, archived by NASA CCMC iSWA", day)


def _swpc_protons(day):
    """SWPC's own file of the last week, for when the archive is out of reach."""
    response = _get(SWPC_PROTONS)
    rows = _json(response) if response is not None else []
    values = _empty_protons()
    satellites = Counter()
    for row in rows:
        when = str(row.get("time_tag", ""))
        if row.get("energy") != ">=10 MeV" or not when.startswith(str(day)):
            continue
        hours = _hours(when, day)
        if hours is not None and finite(row.get("flux")) and row["flux"] >= 0:
            values[round(hours * 60) // PROTON_STEP] = float(row["flux"])
            satellites[f"GOES-{row.get('satellite')}"] += 1
    if not satellites:
        return None
    return _proton_record(values, satellites.most_common(1)[0][0], "NOAA SWPC", day)


def read_eps_protons(data, day):
    """The >10 MeV integral flux of ``day`` from a month's EPS file, as 5-minute
    values. The channel is the one whose own description says >10 MeV."""
    import numpy as np
    from scipy.io import netcdf_file

    try:
        with netcdf_file(io.BytesIO(data), "r", mmap=False) as f:
            flux = f.variables["p3_flux_ic"]
            text = flux.description
            text = text.decode("ascii", "replace") if isinstance(text, bytes) else str(text)
            if not re.search(r">\s*10 MeV", text):
                raise RecordProblem("does not say its channel is >10 MeV")
            stamps = np.array(f.variables["time_tag"].data, dtype=float) / 1000
            fluxes = np.array(flux.data, dtype=float)
    except (KeyError, TypeError, ValueError, OSError, AttributeError) as e:
        raise RecordProblem("sent a file that could not be read") from e
    start = datetime.combine(day, time(), timezone.utc).timestamp()
    values = _empty_protons()
    for stamp, value in zip(stamps, fluxes):
        slot = int((stamp - start) // (PROTON_STEP * 60))
        if 0 <= slot < len(values) and value >= 0:
            values[slot] = float(value)
    return values


def _ncei_protons(day):
    first = day.replace(day=1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    best, most = None, 0
    for satellite in ("08", "11", THIRD_SATELLITE) if day <= GOES_8_LAST else ("11", "08", THIRD_SATELLITE):
        response = _get(NCEI_EPS.format(first=first, last=last, satellite=satellite), timeout=60)
        if response is None:
            continue
        values = read_eps_protons(response.content, day)
        known = sum(value is not None for value in values)
        if known > most:
            best, most = (values, satellite), known
        if most >= LEAST_PROTONS:
            break
    if best is None:
        return None
    values, satellite = best
    note = GOES_10_NOTE if satellite == THIRD_SATELLITE else ""
    return _proton_record(values, f"GOES-{int(satellite)}", "NOAA NCEI, GOES EPS", day, note)


def fetch_protons(day) -> Dict:
    if day < EPS_FIRST:
        raise NoRecord(f"The GOES proton record here starts in {EPS_FIRST.year}")
    if day < ISWA_FIRST:
        record = _ncei_protons(day)
    else:
        try:
            record = _iswa_protons(day)
        except RecordProblem:
            if (_utc_today() - day).days > 7:
                raise
            record = None
        if record is None and (_utc_today() - day).days <= 7:
            record = _swpc_protons(day)
    if record is None:
        raise NoRecord("No GOES proton data for this date")
    return record


def s_level(flux):
    """The NOAA S scale's number for a flux in pfu, 0 under S1."""
    return sum(flux >= level for level in S_LEVELS)


# Kp and Hp30


def _gfz(index, day):
    # (a date it has nothing for is an empty answer: a 404 is no such answer)
    answer = _json(_get(GFZ_JSON.format(day=day, index=index), none=()))
    if not isinstance(answer, dict):
        raise RecordProblem("sent something that could not be read")
    return answer


def fetch_kp(day) -> Dict:
    """The day's eight Kp from GFZ Potsdam, Hp30 and Ap as far as they go."""
    answer = _gfz("Kp", day)
    kp = [None] * 8
    states = []
    for stamp, value, state in zip(answer.get("datetime", ()), answer.get("Kp", ()), answer.get("status", ())):
        hours = _hours(stamp, day)
        if hours is not None and 0 <= hours < 24 and finite(value) and value >= 0:
            kp[int(hours // 3)] = float(value)
            states.append(state)
    if not states:
        raise NoRecord("No Kp from GFZ for this date yet")
    record = {"kp": kp, "hp30": None, "ap": None, "status": "definitive"}
    if any(state != "def" for state in states):
        worst = next(state for state in reversed(states) if state != "def")
        record["status"] = KP_STATUS.get(worst, worst)
    record["settled"] = record["status"] == "definitive" and all(value is not None for value in kp)
    try:
        answer = _gfz("Hp30", day)
        hp30 = [None] * 48
        for stamp, value in zip(answer.get("datetime", ()), answer.get("Hp30", ())):
            hours = _hours(stamp, day)
            if hours is not None and 0 <= hours < 24 and finite(value) and value >= 0:
                hp30[int(hours * 2)] = float(value)
        if any(value is not None for value in hp30):
            record["hp30"] = hp30
        answer = _gfz("Ap", day)
        ap, stamps = answer.get("Ap"), answer.get("datetime")
        if ap and stamps and finite(ap[0]) and str(stamps[0]).startswith(str(day)):
            record["ap"] = ap[0]
    except RecordProblem:
        record["settled"] = False  # the Kp stand without them
    return record


# Dst


def parse_dst(page, day) -> Optional[Dict]:
    """``day``'s row of a monthly Dst page, whose fields run together at -100.
    ``None`` if the page is not of that month or the row has no value; a page
    that is no Dst table is a fault, not an empty month."""
    heading = re.search(r"Hourly Equatorial Dst Values\s*\(([A-Z\-]+)\)", page)
    if heading is None:
        raise RecordProblem("sent a page that is not a Dst table")
    month = re.search(r"\b([A-Z]{3,9})\s+(\d{4})\s*\n", page[heading.end():])
    if month is None or (month.group(1)[:3].title(), int(month.group(2))) != (f"{day:%b}", day.year):
        return None
    for line in page[heading.end():].splitlines():
        if line[:2].strip() != str(day.day) or not line[:2].strip().isdigit():
            continue
        fields = [line[3 + 4 * hour + hour // 8: 7 + 4 * hour + hour // 8] for hour in range(24)]
        values = []
        for field in fields:
            field = field.strip()
            values.append(int(field) if re.fullmatch(r"-?\d+", field) and field != "9999" else None)
        if any(value is not None for value in values):
            return {"values": values, "tier": heading.group(1).lower()}
        return None
    return None


def fetch_dst(day) -> Dict:
    """From the most final of Kyoto's pages that has the month."""
    tiers = DST_TIERS if day.year <= DST_FINAL_LAST else DST_TIERS[1:]
    refused = []
    for tier in tiers:
        # (403 and 404 are how a tier says it does not have the month)
        response = _get(DST_PAGE.format(tier=tier, day=day), none=(), plain=DST_NO_MONTH)
        if response.status_code in DST_NO_MONTH:
            refused.append(response.status_code)
            continue
        record = parse_dst(response.content.decode("euc-jp", "replace"), day)
        if record is not None:
            record["settled"] = record["tier"] == "final"
            return record
    # Some tier has every month: one that refuses them all is at fault, and
    # only a page that lacks the date says that it has none
    if len(refused) == len(tiers):
        raise RecordProblem(f"answered with HTTP {refused[-1]}")
    raise NoRecord("No Dst for this date yet")


# The solar wind


def _omni_wind(day):
    rows = hapi_rows(CDAWEB_HAPI, "OMNI_HRO_1MIN", OMNI_PARAMETERS, day)
    names = ("bz", "speed", "density", "temperature")
    wind = {name: [None] * MINUTES_PER_DAY for name in names}
    filled = 0
    for row in rows:
        minute = row[0].hour * 60 + row[0].minute
        for name, key, cell in zip(names, FILLS, row[1:]):
            value = _number(cell)
            if value is not None and value < FILLS[key] * 0.999:
                wind[name][minute] = value
                filled += 1
    if not filled:
        return None
    wind.update(
        source="OMNI", place="shifted to the bow shock nose", spacecraft="",
        settled=(_utc_today() - day).days > OMNI_SETTLE_DAYS,
    )
    return wind


def _realtime_wind(day):
    plasma = hapi_rows(ISWA_HAPI, "swpc_rtsw_plasma_P1M", "ProtonDensity,BulkSpeed,IonTemperature,isPrimary,source", day)
    field = hapi_rows(ISWA_HAPI, "swpc_rtsw_mag_P1M", "B_z,isPrimary", day)
    wind = {name: [None] * MINUTES_PER_DAY for name in ("bz", "speed", "density", "temperature")}
    craft = Counter()
    for row in plasma:
        if len(row) < 6 or row[4].strip() != "1":
            continue
        minute = row[0].hour * 60 + row[0].minute
        for name, cell in zip(("density", "speed", "temperature"), row[1:4]):
            value = _number(cell)
            if value is not None and value > -999:
                wind[name][minute] = value
        craft[row[5].strip()] += 1
    for row in field:
        value = _number(row[1]) if len(row) > 2 and row[2].strip() == "1" else None
        if value is not None and value > -999:
            wind["bz"][row[0].hour * 60 + row[0].minute] = value
    if not craft and not any(value is not None for value in wind["bz"]):
        return None
    spacecraft = craft.most_common(1)[0][0] if craft else ""
    wind.update(source="NOAA SWPC real-time", place="at L1, not shifted to Earth", spacecraft=spacecraft, settled=False)
    return wind


def fetch_wind(day) -> Dict:
    """OMNI's one-minute values, then, for the weeks it has yet to reach, what
    SWPC had in real time: not the same thing, and the record says so."""
    trouble = None
    try:
        wind = _omni_wind(day)
    except RecordProblem as e:
        wind, trouble = None, e
    if wind is None:
        wind = _realtime_wind(day)
    if wind is not None:
        return wind
    if trouble is not None:
        raise trouble
    raise NoRecord("No solar wind data for this date")


# The flare lists


def _eovsa_row(flare, day):
    peak = _hours(flare.get("peak"), day)
    if peak is None or not 0 <= peak < 24:
        return None
    flux = flare.get("peak_flux_sfu")
    flux = _number(flare.get("Fpk_XP")) if flux is None else flux
    frequency = flare.get("pk_freq_ghz")
    frequency = _number(flare.get("Pk_freq_GHz")) if frequency is None else frequency
    image = flare.get("dspec_xp_img_url") or flare.get("dspec_tp_img_url") or ""
    # The file names its flare, the flare its date
    if not (isinstance(image, str) and image.startswith("https://") and f"flare_id_{day:%Y%m%d}" in image):
        image = ""
    flare_class = str(flare.get("GOES_class") or flare.get("goes_label") or "")
    return {
        "list": "EOVSA",
        "begin": _hours(flare.get("start"), day),
        "end": _hours(flare.get("end"), day),
        "peak": peak,
        "class": f"~{flare_class}" if flare_class and flare.get("goes_est") else flare_class,
        "flux": f"{_flux_text(flux)} sfu" if finite(flux) else "",
        "frequency": f"{frequency:g} GHz" if finite(frequency) else "",
        "detail": strip_html(flare.get("comments")),
        "image": image,
    }


def fetch_eovsa_flares(day) -> Dict:
    response = _get(EOVSA_FLARES, data={"start": str(day), "end": str(day + timedelta(days=1))})
    if response is None:
        raise RecordProblem("did not answer")
    answer = _json(response)
    flares = answer.get("result") if isinstance(answer, dict) else None
    if not isinstance(flares, list):
        raise RecordProblem("sent something that could not be read")
    rows = [_eovsa_row(flare, day) for flare in flares if flare.get("list_status", "listed") == "listed"]
    return {"rows": [row for row in rows if row], "settled": (_utc_today() - day).days > RECENT_LIST_DAYS}


def _class_text(value):
    """A flare class, "" where the list has "NA" or "None" for it."""
    text = str(value or "").strip()
    return "" if text in ("NA", "None") else text


def _stix_row(flare, day):
    peak = _hours(flare.get("peak_UTC"), day)
    if peak is None or not 0 <= peak < 24:
        return None
    goes, estimate, low, high = (
        _class_text(flare.get(key))
        for key in ("GOES_class", "goes_estimated_mean_class", "goes_estimated_min_class", "goes_estimated_max_class")
    )
    # GOES_class is GOES's level at this flare's peak minute, whatever made it:
    # a B7 microflare in the decay of an X flare would read as C4.0
    detail = [f"STIX estimate {estimate} ({low} to {high})"] if estimate else []
    if goes:
        detail.append(f"GOES at peak {goes}")
    counts = flare.get("LC0_PEAK_COUNTS_4S")
    if finite(counts):
        detail.append(f"{counts:.0f} counts per 4 s")
    return {
        "list": "STIX",
        "begin": _hours(flare.get("start_UTC"), day),
        "end": _hours(flare.get("end_UTC"), day),
        "peak": peak,
        "class": f"~{estimate}" if estimate else "",
        "flux": "",
        "frequency": "",
        "detail": ", ".join(detail),
        "image": "",
    }


def fetch_stix_flares(day) -> Dict:
    response = _get(STIX_FLARES, files={
        "start_utc": (None, f"{day}T00:00:00"), "end_utc": (None, f"{day + timedelta(days=1)}T00:00:00"),
    })
    if response is None:
        raise RecordProblem("did not answer")
    flares = _json(response)
    if not isinstance(flares, list):
        raise RecordProblem("sent something that could not be read")
    rows = [row for row in (_stix_row(flare, day) for flare in flares) if row]
    rows.sort(key=lambda row: row["peak"])
    return {"rows": rows, "settled": (_utc_today() - day).days > RECENT_LIST_DAYS}


# HEK


def fetch_hek(day) -> Dict:
    """Coronal waves and filament eruptions as rows; what lies on the disk all
    day, reported again every few hours, as a count at midday."""
    response = _get(HEK_SEARCH, params={
        "cosec": 2, "cmd": "search", "type": "column", "event_type": "ch,ss,cw,fi,fe",
        "event_starttime": f"{day}T00:00:00", "event_endtime": f"{day}T23:59:59",
        "event_coordsys": "helioprojective", "x1": -1200, "x2": 1200, "y1": -1200, "y2": 1200,
        "result_limit": 500,
        "return": "event_type,event_starttime,event_endtime,frm_name,hgs_x,hgs_y,event_description",
    }, timeout=45)
    if response is None:
        raise RecordProblem("did not answer")
    found = _json(response)
    events = found.get("result") if isinstance(found, dict) else None
    if not isinstance(events, list):
        raise RecordProblem("sent something that could not be read")
    rows, spread = [], {}
    for event in events:
        kind = event.get("event_type")
        begin, end = _hours(event.get("event_starttime"), day), _hours(event.get("event_endtime"), day)
        if begin is None:
            continue
        place = _location(event.get("hgs_y"), event.get("hgs_x"))
        if kind in HEK_TIMED and 0 <= begin < 24:
            detail = [place, strip_html(event.get("frm_name")), strip_html(event.get("event_description"))]
            rows.append({
                "list": "HEK", "begin": begin, "end": end, "peak": begin, "class": HEK_TIMED[kind],
                "flux": "", "frequency": "", "detail": ", ".join(part for part in detail if part), "image": "",
            })
        elif kind in HEK_FEATURES and end is not None and begin <= 12 < end:
            # By method: two methods' counts of one feature do not add up
            spread.setdefault((kind, strip_html(event.get("frm_name"))), []).append(place)
    features = [
        {
            "name": HEK_FEATURES[kind], "count": len(places), "method": method,
            "where": sorted({place for place in places if place}),
        }
        for (kind, method), places in sorted(spread.items(), key=lambda item: list(HEK_FEATURES).index(item[0][0]))
    ]
    rows.sort(key=lambda row: row["peak"])
    return {"rows": rows, "features": features, "settled": (_utc_today() - day).days > OMNI_SETTLE_DAYS}


# SWPC's forecast


def parse_forecast(text) -> Optional[Dict]:
    """What the three-day forecast says for each of its days: the largest Kp,
    and the chances of S1 and of radio blackouts."""
    issued = re.search(r":Issued:\s*(\d{4} \w{3} \d{2} \d{4}) UTC", text)
    days = re.search(r"NOAA Kp index breakdown.*\n\s*\n\s*(\w{3} \d{2})\s+(\w{3} \d{2})\s+(\w{3} \d{2})", text)
    if issued is None or days is None:
        return None
    kp = [[] for _ in range(3)]
    plain = STORM_TAG.sub(" ", text)  # 7.67 (G4) on a storm's rows, which would shift the days
    for row in re.findall(r"^\d\d-\d\dUT\s+(\S+)\s+(\S+)\s+(\S+)", plain, re.MULTILINE):
        for values, cell in zip(kp, row):
            number = _number(cell)
            if number is not None:
                values.append(number)
    chances = {}
    for key, label in (("s1", "S1 or greater"), ("r1", "R1-R2"), ("r3", "R3 or greater")):
        row = re.search(rf"^{re.escape(label)}\s+(\d+%)\s+(\d+%)\s+(\d+%)", text, re.MULTILINE)
        if row is None:
            return None
        chances[key] = list(row.groups())
    if not all(kp):
        return None
    return {"issued": issued.group(1), "days": list(days.groups()), "kp": [max(values) for values in kp], **chances}


def fetch_forecast(day) -> Dict:
    """Today's, and nothing for any other date: there is no archive of them."""
    if day != _utc_today():
        raise NoRecord("")
    response = _get(SWPC_FORECAST)
    if response is None:
        raise RecordProblem("did not answer")
    record = parse_forecast(response.text)
    if record is None:
        raise RecordProblem("sent a forecast that could not be read")
    when = datetime.strptime(record["issued"], "%Y %b %d %H%M").date()
    if not 0 <= (day - when).days <= 1:
        raise NoRecord("SWPC's latest forecast is not from today")
    record["settled"] = False
    return record


# What a record must hold to be shown: one saved by hand or cut short must not
# get to the window


def _series(values, length):
    return (
        isinstance(values, list) and len(values) == length
        and all(value is None or finite(value) for value in values)
        and any(value is not None for value in values)
    )


def _texts(values):
    return isinstance(values, list) and all(isinstance(value, str) for value in values)


def _protons_ok(record):
    return (
        record["step"] == PROTON_STEP and _series(record["values"], MINUTES_PER_DAY // PROTON_STEP)
        and isinstance(record["satellite"], str) and isinstance(record["source"], str)
        and isinstance(record.get("note", ""), str)
    )


def _kp_ok(record):
    return (
        _series(record["kp"], 8) and (record["hp30"] is None or _series(record["hp30"], 48))
        and (record["ap"] is None or finite(record["ap"])) and isinstance(record["status"], str)
    )


def _dst_ok(record):
    return _series(record["values"], 24) and isinstance(record["tier"], str)


def _wind_ok(record):
    # (a quantity can be missing all day, where the others are not)
    return all(
        isinstance(record[name], list) and len(record[name]) == MINUTES_PER_DAY
        and all(value is None or finite(value) for value in record[name])
        for name in ("bz", "speed", "density", "temperature")
    ) and all(isinstance(record[name], str) for name in ("source", "place", "spacecraft"))


def _row_ok(row):
    return (
        finite(row["peak"]) and all(row[name] is None or finite(row[name]) for name in ("begin", "end"))
        and all(isinstance(row[name], str) for name in ("list", "class", "flux", "frequency", "detail", "image"))
    )


def _flares_ok(record):
    return isinstance(record["rows"], list) and all(_row_ok(row) for row in record["rows"])


def _feature_ok(feature):
    return (
        isinstance(feature["name"], str) and isinstance(feature["method"], str)
        and isinstance(feature["count"], int) and _texts(feature["where"])
    )


def _hek_ok(record):
    return _flares_ok(record) and isinstance(record["features"], list) and all(_feature_ok(f) for f in record["features"])


def _forecast_ok(record):
    return (
        isinstance(record["issued"], str) and _texts(record["days"]) and _series(record["kp"], 3)
        and all(_texts(record[name]) for name in ("s1", "r1", "r3"))
    )


CHECKS = {
    "protons": _protons_ok, "kp": _kp_ok, "dst": _dst_ok, "wind": _wind_ok,
    "eovsa": _flares_ok, "stix": _flares_ok, "hek": _hek_ok, "forecast": _forecast_ok,
}


def well_formed(key, record):
    """Whether ``record`` is one of ``key`` that the window can show as it is."""
    if not isinstance(record, dict) or key not in CHECKS:
        return False
    if "said" in record:
        return isinstance(record["said"], str)
    try:
        return CHECKS[key](record)
    except (KeyError, TypeError, AttributeError):
        return False


FETCHERS = {
    "protons": fetch_protons,
    "kp": fetch_kp,
    "dst": fetch_dst,
    "wind": fetch_wind,
    "eovsa": fetch_eovsa_flares,
    "stix": fetch_stix_flares,
    "hek": fetch_hek,
    "forecast": fetch_forecast,
}
NAMES = {
    "protons": "The GOES proton archive", "kp": "GFZ Potsdam", "dst": "Kyoto's Dst service", "wind": "The solar wind archive",
    "eovsa": "EOVSA's flare list", "stix": "The STIX data centre", "hek": "HEK", "forecast": "SWPC's forecast",
}

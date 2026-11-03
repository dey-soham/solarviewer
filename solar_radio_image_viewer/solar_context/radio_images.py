"""
Radio images of the Sun for a date: SunDish maps, EOVSA daily band maps,
Siberian Radioheliograph snapshots and the OVRO-LWA daily movie.

Each source says what it has as a list of ``ContextImage`` and, when it has
nothing, a sentence why. An archive with nothing for the date is not a fault:
only a source that could not be asked raises ``RadioProblem``.
"""

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Tuple

from ..version import __version__
from .context_images import ContextImage

USER_AGENT = f"SolarViewer/{__version__} (python-requests)"
TIMEOUT = 30

SUNDISH_INDEX = (
    "https://docs.google.com/spreadsheets/d/"
    "1KVEOuLBxjVJ1HcTg8T5WX45-DlEsbN5r_404ac9KiBU/export?format=xlsx"
)
SUNDISH_PAGE = "https://sites.google.com/inaf.it/sundish/sundish-images-archive/sundish-solar-archive"
SUNDISH_FILE = "https://drive.google.com/uc?export=download&id={}"
SUNDISH_TELESCOPES = {"MED": "Medicina", "SRT": "SRT"}
INDEX_DAYS = 1  # the index is asked for again after this long

EOVSA_PAGE = "https://ovsa.njit.edu/eovsadata/"
EOVSA_IMAGE = (
    "https://ovsa.njit.edu/eovsadata/api/data.php?kind=browser&date={day}"
    "&type={kind}&channel=_eovsa_{band}_v2.0&ext=jpg"
)
# As the maps print them: the site's own table rounds two of them differently
EOVSA_BANDS = [
    ("bd01", "1.4"), ("bd02", "2.9"), ("bd03", "4.3"), ("bd04", "6.9"),
    ("bd05", "10.2"), ("bd06", "13.9"), ("bd07", "17.0"),
]
# Of 67 dates of 2021 to 2026 with maps, looked at by eye and by ``fills_the_sky``:
# 3 of 66 at 4.3 GHz were no map of the Sun, 4 of 66 at 6.9, 7 of 67 at 2.9 and at
# 10.2 (2024-05-12 and 14 among them), 18 of 65 at 13.9 and 20 of 41 at 17 GHz
EOVSA_USUAL = ("bd02", "bd03", "bd04")
EOVSA_OFTEN_STRIPES = ("bd06", "bd07")
# Of the 256 pixels of a thumbnail: the disk's limb is inside this radius, and
# the rows below hold the map's small print
EOVSA_SIDE, EOVSA_SKY, EOVSA_PRINT = 256, 118, 236
EOVSA_BRIGHT = 120  # of 255, as a mean of red, green and blue
EOVSA_SKY_LIMIT = 0.015  # of the sky bright: every map at or over it that was viewed had no Sun to speak of
EOVSA_ARTEFACTS = (
    "This map looks like imaging artefacts: stripes or sidelobes fill the sky around the Sun, "
    "so what it shows cannot be taken for the Sun at this band"
)

SRH_PAGE = "https://badary.iszf.irk.ru/srhDaily.php"
SRH_IMAGE = "https://badary.iszf.irk.ru/corrPlots/{moment:%Y}/srh_{moment:%Y%m%d_%H%M}.png"
SRH_FILE = re.compile(r"corrPlots/\d{4}/srh_(\d{8})_(\d{4})\.png")

LWA_PAGE = "https://ovsa.njit.edu/lwa/portal"
LWA_MOVIE = "https://ovsa.njit.edu/lwa/portal/preview/movie/{day}"
LWA_FIRST = date(2023, 5, 2)  # the portal's first date (its avail-dates); 2023-07-19 is the next

MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
RELATION = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
DRIVE_FILE_ID = re.compile(r"(?:/file/d/|[?&]id=)([-\w]{20,})")
HYPERLINK = re.compile(r'HYPERLINK\(\s*"([^"]+)"', re.IGNORECASE)
MAP_ID = re.compile(r"^(MED|SRT)_(\d{2})(\d{2})(\d{2})(?:_|$)")
TIME_CELL = re.compile(r"^(\d{2}):(\d{2})(?:\s*-\s*(\d{2}):(\d{2}))?")
QUALITY = re.compile(r"^[HML]Q(/[HML]Q)?$")
EPOCH_ZERO = date(1899, 12, 30)  # what a spreadsheet's day numbers count from
# SUN_MED_261028_0722_..., SUN_MED_20180418_1231_..., 20191009_1028_25GHz_ch3.png
# and 20180215_111959_24GHz_ch2.png. A date of any other length (2021021, for one)
# is not read: its missing digit could be either.
DRIVE_NAME = re.compile(r"(?:^|[^\d])(?:(MED|SRT)_)?(?:20)?(\d{2})(\d{2})(\d{2})_(\d{2})(\d{2})(?:\d{2})?(?:_|\.)")
DRIVE_GHZ = re.compile(r"([\d.]+)\s*GHz", re.IGNORECASE)
FILE_NAME = re.compile(r'filename="?([^";]+)')
TIME_SLACK = 10  # minutes between a file's name and its row: both are the map's start, to about 3


class RadioProblem(Exception):
    """A source could not be asked; its text is a sentence for the user."""


def _get(url, **kwargs):
    import requests

    try:
        return requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, **kwargs)
    except requests.RequestException as e:
        raise RadioProblem("could not reach the archive") from e


# ------------------------------------------------------------ SunDish


def _sheet(data: bytes) -> Tuple[Dict[str, str], Dict[str, str]]:
    """The cells of a workbook's first sheet, ``{"A2": text}``, and the links
    in them, ``{"E2": url}``: a link is either the sheet's or a formula's."""
    with zipfile.ZipFile(io.BytesIO(data)) as book:
        names = set(book.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            for item in ET.fromstring(book.read("xl/sharedStrings.xml")):
                shared.append("".join(part.text or "" for part in item.iter(f"{MAIN}t")))
        workbook = ET.fromstring(book.read("xl/workbook.xml"))
        first = workbook.find(f"{MAIN}sheets")[0].get(f"{RELATION}id")
        targets = {r.get("Id"): r.get("Target") for r in ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))}
        path = targets[first].lstrip("/")
        path = path if path.startswith("xl/") else f"xl/{path}"
        sheet = ET.fromstring(book.read(path))
        folder, name = path.rsplit("/", 1)
        rels = f"{folder}/_rels/{name}.rels"
        external = {}
        if rels in names:
            external = {r.get("Id"): r.get("Target") for r in ET.fromstring(book.read(rels))}

    cells, links = {}, {}
    for cell in sheet.iter(f"{MAIN}c"):
        ref = cell.get("r")
        value = cell.find(f"{MAIN}v")
        formula = cell.find(f"{MAIN}f")
        if value is not None and value.text is not None:
            cells[ref] = shared[int(value.text)] if cell.get("t") == "s" else value.text
        elif cell.get("t") == "inlineStr":
            cells[ref] = "".join(part.text or "" for part in cell.iter(f"{MAIN}t"))
        linked = HYPERLINK.search(formula.text or "") if formula is not None else None
        if linked:
            links[ref] = linked.group(1)
    for link in sheet.iter(f"{MAIN}hyperlink"):
        target = external.get(link.get(f"{RELATION}id"))
        if target:
            links[link.get("ref")] = target
    return cells, links


def _epoch(text: str) -> Optional[date]:
    """A date cell: ``28/10/2026`` or a spreadsheet's day number."""
    text = (text or "").strip()
    try:
        if "/" in text:
            day, month, year = text.split("/")
            return date(int(year), int(month), int(day))
        return EPOCH_ZERO + timedelta(days=int(float(text)))
    except (ValueError, OverflowError):
        return None


def _map_row(cell, link) -> Optional[dict]:
    """A row of the index, or None unless its MAP_ID and epoch agree on its
    date. The PNG's label is typed by hand and is out on a few rows: the file's
    own name is checked when it comes down."""
    map_id = cell("MAP_ID").strip()
    named = MAP_ID.match(map_id)
    if named is None:
        return None
    telescope, *ymd = named.groups()
    try:
        day = date(2000 + int(ymd[0]), int(ymd[1]), int(ymd[2]))
    except ValueError:
        return None
    if _epoch(cell("EPOCH")) != day:
        return None
    span = TIME_CELL.match(cell("TIME").strip())
    start = f"{span.group(1)}:{span.group(2)}" if span else ""
    end = f"{span.group(3)}:{span.group(4)}" if span and span.group(3) else ""
    file_id = DRIVE_FILE_ID.search(link("IMAGE") or "")
    flags = [flag.strip() for flag in cell("FLAGS").split(",")]
    return {
        "id": map_id,
        "telescope": telescope,
        "day": day.isoformat(),
        "start": start,
        "end": end,
        "ghz": cell("CENT. FREQ").strip(),
        "quality": next((flag for flag in flags if QUALITY.match(flag)), ""),
        "feed": cell("FEED").strip(),
        "notes": cell("NOTES").strip(),
        "file": file_id.group(1) if file_id else "",
    }


def parse_sundish_index(data: bytes) -> Tuple[List[dict], int]:
    """The rows of the archive's index (an .xlsx file), and how many were
    refused because they do not agree on their date."""
    try:
        cells, links = _sheet(data)
    except (zipfile.BadZipFile, ET.ParseError, KeyError, IndexError, TypeError, ValueError, AttributeError) as e:
        raise RadioProblem("the index is not a readable spreadsheet") from e

    heads = {}
    rows = set()
    for ref, text in cells.items():
        place = re.fullmatch(r"([A-Z]+)(\d+)", ref or "")
        if place is None:  # what else is wrong with it cannot be told: the last good copy stands
            raise RadioProblem("the index is not a readable spreadsheet")
        column, row = place.groups()
        rows.add(int(row))
        if row == "1":
            heads[" ".join(text.split()).upper()] = column

    def column_of(head):
        return next((column for text, column in heads.items() if text.startswith(head)), None)

    if column_of("MAP_ID") is None or column_of("IMAGE") is None:
        raise RadioProblem("the index no longer has the columns it had")

    parsed, refused = [], 0
    for row in sorted(rows - {1}):
        cell = lambda head: cells.get(f"{column_of(head)}{row}", "")
        link = lambda head: links.get(f"{column_of(head)}{row}", "")
        if not cell("MAP_ID").strip():
            continue
        entry = _map_row(cell, link)
        if entry is None:
            refused += 1
        else:
            parsed.append(entry)
    if not parsed:
        raise RadioProblem("the index has no maps in it")
    return parsed, refused


def good_index_row(row) -> bool:
    """Whether ``row`` is one ``parse_sundish_index`` could have made."""
    if not isinstance(row, dict) or row.get("telescope") not in SUNDISH_TELESCOPES:
        return False
    if not all(isinstance(row.get(key), str) for key in ("id", "day", "start", "end", "ghz", "quality", "notes", "file")):
        return False
    try:
        date.fromisoformat(row["day"])
    except ValueError:
        return False
    return True


def fetch_sundish_index() -> bytes:
    response = _get(SUNDISH_INDEX)
    if response.status_code != 200:
        raise RadioProblem(f"the index answered HTTP {response.status_code}")
    if response.content.lstrip()[:1] == b"<":
        raise RadioProblem("the index came back as a web page")
    return response.content


def sundish_images(rows: List[dict], day: date) -> Tuple[List[ContextImage], List[dict]]:
    """The maps of ``day`` with a public PNG, in order of time, and the rows
    of that day without one."""
    of_day = [row for row in rows if row["day"] == day.isoformat()]
    images = []
    for row in sorted((r for r in of_day if r["file"]), key=lambda r: (r["start"], r["id"])):
        name = SUNDISH_TELESCOPES[row["telescope"]]
        span = "-".join(part for part in (row["start"], row["end"]) if part)
        start = _clock(row["start"])
        end = _clock(row["end"])
        moment = datetime.combine(day, start) if start else None
        finish = datetime.combine(day, end) if end else None
        if moment and finish and finish < moment:
            finish += timedelta(days=1)
        lines = [f"{row['id']}: {name} single-dish map, {row['ghz']} GHz, {span} UT"]
        if row["quality"]:
            lines.append(f"Quality flag: {row['quality']}")
        if row["notes"]:
            lines.append(f"Notes: {row['notes']}")
        lines.append("PNG preview: FITS and raw data are on request from the PI")
        url = SUNDISH_FILE.format(row["file"])
        images.append(
            ContextImage(
                title=f"{row['ghz']} GHz {row['telescope']}",  # the end gives way on a small tile
                thumb_url=url,
                page_url=url,
                instrument="SunDish (INAF)",
                description="\n".join(lines),
                credits="SunDish",
                image_date=moment,
                group="radio",
                proved_day=day,
                key=row["id"],
                span=" ".join(part for part in (row["start"], row["quality"]) if part),
                image_end=finish,
                source_page=SUNDISH_PAGE,
            )
        )
    return images, [row for row in of_day if not row["file"]]


def _clock(text: str) -> Optional[time]:
    try:
        hours, minutes = text.split(":")
        return time(int(hours), int(minutes))
    except ValueError:
        return None


def nearest_sundish(rows: List[dict], day: date) -> Optional[date]:
    """The date nearest ``day`` with a map to show, not ``day`` itself."""
    days = {date.fromisoformat(row["day"]) for row in rows if row["file"]} - {day}
    return min(days, key=lambda other: (abs((other - day).days), other)) if days else None


def drive_name_parts(name: str) -> Optional[dict]:
    """What a SunDish file's own name says of it, such as
    ``SUN_MED_261028_0722_18.9GHz_...png``: ``day``, ``time`` and, if it says
    them, ``ghz`` and ``telescope``. ``None`` if it gives no date that can be
    read. The name can stand in a header's ``filename="..."``."""
    named = DRIVE_NAME.search(name or "")
    if named is None:
        return None
    telescope, *ymd, hours, minutes = named.groups()
    try:
        day = date(2000 + int(ymd[0]), int(ymd[1]), int(ymd[2]))
        moment = time(int(hours), int(minutes))
    except ValueError:
        return None
    ghz = DRIVE_GHZ.search(name)
    return {"day": day, "time": moment, "ghz": ghz.group(1).rstrip(".") if ghz else "", "telescope": telescope or ""}


def drive_name_date(name: str) -> Optional[date]:
    """The date in a SunDish file's own name, if it has one."""
    parts = drive_name_parts(name)
    return parts["day"] if parts else None


def is_sundish_file(url: str) -> bool:
    return url.startswith(SUNDISH_FILE.format(""))


def sundish_file_name(url: str) -> str:
    """The name the archive gives a file, from the headers alone. The list
    is made once for a date, and it is what the captions go on."""
    with _get(url, stream=True) as response:
        if response.status_code != 200:
            raise RadioProblem(f"the file answered HTTP {response.status_code}")
        named = FILE_NAME.search(response.headers.get("Content-Disposition", ""))
    return named.group(1) if named else ""


def _ghz_agree(row: str, file: str) -> bool:
    """A name can give its frequency in whole GHz, as often cut off as rounded."""
    try:
        return abs(float(row) - float(file)) < (1 if "." not in file else 0.05)
    except ValueError:
        return False


def file_doubt(image: ContextImage, name: str) -> ContextImage:
    """``image`` of the index, told what its file's own ``name`` says. Where the
    two differ on the same date neither can be shown to be right: a date is
    refused when the file comes down, and here a time or a frequency is shown
    as not known, with both in the tooltip. A name that says nothing changes nothing."""
    parts = drive_name_parts(name)
    if parts is None or parts["day"] != image.proved_day:
        return image
    ghz, telescope = image.title.split(" GHz ")
    said, changes = [], {}
    if image.image_date is not None:
        apart = abs(image.image_date.hour * 60 + image.image_date.minute - parts["time"].hour * 60 - parts["time"].minute)
        if apart > TIME_SLACK:
            said.append(f"the index has {image.image_date:%H:%M}, the file's name {parts['time']:%H:%M}")
            changes.update(image_date=None, image_end=None, span=f"time? {image.span.partition(' ')[2]}".rstrip())
    if ghz and parts["ghz"] and not _ghz_agree(ghz, parts["ghz"]):
        said.append(f"the index has {ghz} GHz, the file's name {parts['ghz']} GHz")
        ghz = "?"
    if parts["telescope"] and parts["telescope"] != telescope:
        said.append(f"the index has {telescope}, the file's name {parts['telescope']}")
        telescope = "?"
    if not said:
        return image
    doubt = f"The index and this file disagree: {'; '.join(said)}"
    return replace(
        image, title=f"{ghz} GHz {telescope}", doubt=doubt, description=f"{doubt}\n{image.description}", **changes,
    )


def check_sundish_files(images: List[ContextImage], name_of=sundish_file_name) -> List[ContextImage]:
    """``images`` told what their files say. A file that cannot be asked is
    still checked when it comes down."""
    checked = []
    for image in images:
        try:
            checked.append(file_doubt(image, name_of(image.page_url)))
        except RadioProblem:
            checked.append(image)
    return checked


def sundish_for_date(rows: List[dict], day: date) -> Tuple[List[ContextImage], str]:
    images, without_png = sundish_images(rows, day)
    if images:
        return images, ""
    if without_png:
        return [], "SunDish lists this date without a public PNG"
    return [], "No SunDish map on this date"


# ------------------------------------------------------------ EOVSA


def eovsa_images(day: date) -> List[ContextImage]:
    """All the bands of a day; which ones get a tile is the tab's choice."""
    images = []
    for band, ghz in EOVSA_BANDS:
        thumb = EOVSA_IMAGE.format(day=day.isoformat(), kind="t", band=band)
        full = EOVSA_IMAGE.format(day=day.isoformat(), kind="f", band=band)
        often = " This band is often imaging stripes, with no Sun to be seen." if band in EOVSA_OFTEN_STRIPES else ""
        images.append(
            ContextImage(
                title=f"EOVSA {ghz} GHz",
                thumb_url=thumb,
                page_url=full,
                instrument="EOVSA (NJIT)",
                description=f"Full-disk daily map at {ghz} GHz. The map prints its own time.{often}",
                credits="EOVSA",
                group="radio",
                proved_day=day,  # the archive answers 404, not another date's map
                key=f"EOVSA {band}",
                span="daily",
                source_page=EOVSA_PAGE,
            )
        )
    return images


def fills_the_sky(pixels) -> bool:
    """Whether a thumbnail of ``EOVSA_SIDE`` square (grey, 0 to 255) is bright
    beyond the Sun's limb, where a good map is dark. It tells stripes and
    sidelobes that fill the frame, and misses a few that keep to a corner."""
    import numpy as np

    rows, columns = np.mgrid[0:EOVSA_SIDE, 0:EOVSA_SIDE]
    middle = (EOVSA_SIDE - 1) / 2
    sky = (np.hypot(columns - middle, rows - middle) > EOVSA_SKY) & (rows < EOVSA_PRINT)
    return bool((pixels[sky] > EOVSA_BRIGHT).mean() >= EOVSA_SKY_LIMIT)


def fetch_eovsa(day: date) -> Tuple[List[ContextImage], str, Optional[bytes]]:
    """The day's maps, a sentence if there are none, and the bytes of the
    thumbnail that proved they exist: asking again would be a waste."""
    response = _get(EOVSA_IMAGE.format(day=day.isoformat(), kind="t", band=EOVSA_USUAL[0]))
    if response.status_code == 404:
        return [], "No EOVSA band maps for this date", None
    if response.status_code != 200:
        raise RadioProblem(f"the archive answered HTTP {response.status_code}")
    if response.content[:3] != b"\xff\xd8\xff":
        raise RadioProblem("the archive did not send an image")
    if b"\xff\xd9" not in response.content[-64:]:  # it would stay as the tile's thumbnail
        raise RadioProblem("the archive sent part of an image")
    return eovsa_images(day), "", response.content


# ------------------------------------------------------------ Siberian Radioheliograph


def good_stamp(stamp) -> bool:
    """Whether ``stamp`` is a time of day, ``HHMM``."""
    return (
        isinstance(stamp, str) and len(stamp) == 4 and stamp.isdigit()
        and int(stamp[:2]) < 24 and int(stamp[2:]) < 60
    )


def parse_srh_stamps(html: str, day: date) -> List[str]:
    """The snapshot times (``HHMM``) a day's page lists. The page falls back
    on today when it has no such date, so a file of another date counts for nothing."""
    wanted = f"{day:%Y%m%d}"
    return sorted({stamp for named, stamp in SRH_FILE.findall(html) if named == wanted and good_stamp(stamp)})


def fetch_srh_stamps(day: date) -> List[str]:
    import requests

    form = {
        "Dip": 1, "Type": 1, "Year": day.year, "Month": day.month, "Day": day.day,
        "selected_date": "Submit",
    }
    try:
        response = requests.post(SRH_PAGE, data=form, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    except requests.RequestException as e:
        raise RadioProblem("could not reach the archive") from e
    if response.status_code != 200:
        raise RadioProblem(f"the archive answered HTTP {response.status_code}")
    page = response.content.decode("latin-1")
    if "selected_date" not in page and not SRH_FILE.search(page):
        raise RadioProblem("the archive sent another page")  # its own has the form, data or none
    return parse_srh_stamps(page, day)


def srh_image(day: date, stamps: List[str], when: Optional[time] = None) -> Optional[ContextImage]:
    """The snapshot nearest ``when``, or the middle one of the day's."""
    if not stamps:
        return None
    if when is None:
        stamp = stamps[len(stamps) // 2]
    else:
        minutes = when.hour * 60 + when.minute
        stamp = min(stamps, key=lambda s: abs(int(s[:2]) * 60 + int(s[2:]) - minutes))
    moment = datetime.combine(day, time(int(stamp[:2]), int(stamp[2:])))
    url = SRH_IMAGE.format(moment=moment)
    return ContextImage(
        title="SRH",
        thumb_url=url,
        page_url=url,
        instrument="Siberian Radioheliograph",
        description=(
            "Snapshot figure by band. The time here is the file's name: each panel prints "
            "the time it was observed, up to some minutes earlier on the older ones."
        ),
        credits="SRH",
        image_date=moment,
        group="radio",
        key=f"SRH {stamp}",
        span=f"~{moment:%H:%M}",
        source_page=SRH_PAGE,
    )


# ------------------------------------------------------------ OVRO-LWA


def lwa_movie_of(answer, day: date) -> bool:
    """Whether the portal's ``answer`` names a movie of ``day``."""
    url = answer.get("url") or ""
    return url.startswith("https://") and answer.get("date") == day.isoformat() and f"_{day:%Y%m%d}." in url


def fetch_lwa_movie(day: date) -> Tuple[List[ContextImage], str]:
    if day < LWA_FIRST:
        return [], f"OVRO-LWA movies start on {LWA_FIRST}"
    response = _get(LWA_MOVIE.format(day=day.isoformat()))
    if response.status_code == 404:
        return [], "No OVRO-LWA movie for this date"
    if response.status_code != 200:
        raise RadioProblem(f"the portal answered HTTP {response.status_code}")
    try:
        answer = response.json()
    except ValueError as e:
        raise RadioProblem("the portal did not answer in its usual form") from e
    url = answer.get("url") or ""
    if not url:
        return [], "No OVRO-LWA movie for this date"
    if not lwa_movie_of(answer, day):
        return [], "OVRO-LWA named a movie of another date, so it is not shown"
    return [
        ContextImage(
            title="OVRO-LWA",
            thumb_url=url,
            page_url=url,
            instrument="OVRO-LWA",
            description=(
                "Daily imaging movie, 34-84 MHz, starting about 14 UT and running into the next UT day. "
                "The still is one frame of it, and it prints its own time. Click to play the movie."
            ),
            credits="OVRO-LWA",
            group="radio",
            proved_day=day,
            key="OVRO-LWA movie",
            span="movie",
            link=url,
            source_page=LWA_PAGE,
        )
    ], ""

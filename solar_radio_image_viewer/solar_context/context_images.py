#!/usr/bin/env python3
"""
Context Images Fetcher - Get solar context imagery from Helioviewer API or SolarMonitor.org.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Callable, List, Optional, Tuple
import logging
import re
from urllib.parse import urljoin

from .. import helioviewer_api as hv
from .. import helioviewer_catalog as catalog

logger = logging.getLogger(__name__)

NOON = time(12, 0)
ARCHIVE_FOLDER = re.compile(r"/data/(\d{4})/(\d{2})/(\d{2})/")  # SolarMonitor's, by observation date


@dataclass
class ContextImage:
    """Represents a context image."""

    title: str
    thumb_url: str
    page_url: str  # URL to the full disk page or full-res image
    instrument: str
    description: str
    credits: str = "Helioviewer"  # Credit source
    source_id: Optional[int] = None  # Helioviewer sourceId if applicable
    image_date: Optional[datetime] = None
    group: str = ""  # one of GROUPS; found from the title if empty
    proved_day: Optional[date] = None  # the date, for an archive that gives no time
    key: str = ""  # what its thumbnail is cached under, if not the title
    span: str = ""  # the time to show, if not image_date's
    image_end: Optional[datetime] = None  # where an image takes a while to make
    link: str = ""  # opened in the browser on a click, instead of the large view
    source_page: str = ""  # the archive's page, for the tile's menu
    view: str = ""  # where it was seen from, if not from Earth
    doubt: str = ""  # a sentence, where what the tile says of it cannot be trusted


# Essential instruments for context viewing
# Format: (nickname, layer_path, observatory, description, source_id)
# layer_path format: [observatory,instrument,detector,measurement,visible,opacity]
ESSENTIAL_INSTRUMENTS = [
    (
        "AIA 304",
        "[SDO,AIA,AIA,304,1,100]",
        "SDO",
        "304Å Chromosphere & Transition Region (He II)",
        13,
    ),
    (
        "AIA 193",
        "[SDO,AIA,AIA,193,1,100]",
        "SDO",
        "193Å Corona & Flare Plasma (Fe XII/XXIV)",
        11,
    ),
    (
        "AIA 171",
        "[SDO,AIA,AIA,171,1,100]",
        "SDO",
        "171Å Upper Transition Region & Quiet Corona (Fe IX)",
        10,
    ),
    (
        "AIA 335",
        "[SDO,AIA,AIA,335,1,100]",
        "SDO",
        "335Å Active Region Corona (Fe XVI)",
        14,
    ),
    (
        "HMI Mag",
        "[SDO,HMI,HMI,magnetogram,1,100]",
        "SDO",
        "Magnetogram (Photospheric Magnetic Field)",
        19,
    ),
    (
        "HMI Int",
        "[SDO,HMI,HMI,continuum,1,100]",
        "SDO",
        "Continuum Intensity (Photosphere, Sunspots)",
        18,
    ),
    (
        "LASCO C2",
        "[SOHO,LASCO,C2,white-light,1,100]",
        "SOHO",
        "Inner Coronagraph (2-6 R☉, White Light)",
        4,
    ),
    (
        "LASCO C3",
        "[SOHO,LASCO,C3,white-light,1,100]",
        "SOHO",
        "Outer Coronagraph (4-30 R☉, White Light)",
        5,
    ),
    (
        "SUVI 171",
        "[GOES,SUVI,SUVI,171,1,100]",
        "GOES",
        "171Å Upper Transition Region & Corona",
        2002,
    ),
    (
        "SUVI 304",
        "[GOES,SUVI,SUVI,304,1,100]",
        "GOES",
        "304Å Chromosphere & Transition Region",
        2005,
    ),
    (
        "EUVI-A 171",
        "[STEREO_A,SECCHI,EUVI,171,1,100]",
        "STEREO_A",
        "171Å Corona from STEREO-A (Far-side view)",
        20,
    ),
    (
        "EUVI-A 304",
        "[STEREO_A,SECCHI,EUVI,304,1,100]",
        "STEREO_A",
        "304Å Chromosphere from STEREO-A (Far-side view)",
        23,
    ),
]


# Layers that only ask for their images once their group is on
EXTRA_INSTRUMENTS = [
    ("AIA 94", "[SDO,AIA,AIA,94,1,100]", "SDO", "94Å Hot Flare Plasma (Fe XVIII)", 8),
    ("AIA 131", "[SDO,AIA,AIA,131,1,100]", "SDO", "131Å Flare Plasma (Fe VIII/XXI)", 9),
    ("AIA 211", "[SDO,AIA,AIA,211,1,100]", "SDO", "211Å Active Region Corona (Fe XIV)", 12),
    ("SWAP 174", "[PROBA2,SWAP,174,1,100]", "PROBA2", "174Å Corona, wider field than AIA", 32),
    ("XRT Al-poly", "[Hinode,XRT,Al_poly,Open,1,100]", "Hinode", "Soft X-rays (Al-poly filter)", 48),
    ("GONG H-alpha", "[GONG,GONG,H-alpha,6562,1,100]", "GONG", "6563Å Chromosphere, ground network", 94),
    ("AIA 1600", "[SDO,AIA,AIA,1600,1,100]", "SDO", "1600Å Flare Ribbons & Upper Photosphere", 15),
    ("KCor", "[MLSO,COSMO,KCor,735,1,100]", "MLSO", "Ground Coronagraph (1.05-3 R☉, Polarized Light)", 83),
    ("PUNCH", "[PUNCH,WFI+NFI,Total Brightness,1,100]", "PUNCH", "Wide Field Mosaic (out to about 180 R☉)", 131),
    ("COR2-A", "[STEREO_A,SECCHI,COR2,white-light,1,100]", "STEREO_A", "Coronagraph from STEREO-A (2.5-15 R☉)", 29),
    ("FSI 174", "[SOLO,EUI,FSI,174,1,100]", "SOLO", "174Å Corona from Solar Orbiter (EUI)", 84),
    ("FSI 304", "[SOLO,EUI,FSI,304,1,100]", "SOLO", "304Å Chromosphere from Solar Orbiter (EUI)", 85),
]

GROUPS = [
    ("radio", "Radio"),
    ("euv", "EUV and X-ray"),
    ("chromosphere", "Chromosphere and magnetic"),
    ("coronagraphs", "Coronagraphs"),
    ("viewpoints", "Other viewpoints"),
]
USUAL_GROUPS = ("radio", "euv")  # on until the user says otherwise

GROUP_MEMBERS = {
    "euv": (
        "AIA 94", "AIA 131", "AIA 171", "AIA 193", "AIA 211", "AIA 304", "AIA 335",
        "SUVI 171", "SUVI 304", "SWAP 174", "XRT Al-poly",
    ),
    "chromosphere": ("GONG H-alpha", "AIA 1600", "HMI Mag", "HMI Int"),
    "coronagraphs": ("LASCO C2", "LASCO C3", "KCor", "PUNCH"),
    "viewpoints": ("EUVI-A 171", "EUVI-A 304", "COR2-A", "FSI 174", "FSI 304"),
}
GROUP_OF_TITLE = {title: group for group, titles in GROUP_MEMBERS.items() for title in titles}

# The large view's width in pixels; the thumbnail is 320. Both show the
# instrument's default field of view (the shared table in helioviewer_catalog):
# Helioviewer scales every instrument to 1 AU, so one field fits all that show
# the disk, however far away they are
THUMB_SIZE = 320
FULL_SIZE = 1600
FULL_SIZES = {
    "LASCO C2": 2048,
    "LASCO C3": 2048,
    "SWAP 174": 1024,
    "KCor": 1024,
    "PUNCH": 2048,
    "COR2-A": 2048,
}

# Before these a layer has no image of the date, so it is not asked
FIRST_DATES = {
    "SUVI 171": date(2022, 6, 15),
    "SUVI 304": date(2022, 6, 9),
    "GONG H-alpha": date(2015, 12, 22),
    "KCor": date(2013, 9, 30),
    "PUNCH": date(2025, 6, 22),
    "FSI 174": date(2020, 5, 12),
    "FSI 304": date(2020, 5, 12),
}
LEAST_FULL_FIELD = 1500  # arcsec: an XRT image narrower than this is of a part of the Sun
SOLO_TITLES = ("FSI 174", "FSI 304")
OTHER_VIEW_TITLES = SOLO_TITLES + ("EUVI-A 171", "EUVI-A 304", "COR2-A")
HEADER_VALUE = "<{}[^>]*>\\s*([-+\\d.eE]+)\\s*<"
AU_METRES = 1.495978707e11
# Answered with the file itself, not a page to look for it on
DIRECT_HOSTS = ("helioviewer.org", "drive.google.com", "ovsa.njit.edu", "badary.iszf.irk.ru")


def group_of(image) -> str:
    """The key of the group ``image`` belongs to, "" for one in none."""
    return getattr(image, "group", "") or GROUP_OF_TITLE.get(getattr(image, "title", ""), "")


def place_in_group(image) -> int:
    """Where ``image`` goes among its group: as listed, if its group lists it."""
    titles = GROUP_MEMBERS.get(group_of(image), ())
    title = getattr(image, "title", "")
    return titles.index(title) if title in titles else len(titles)


def layers_of(groups) -> list:
    """The layers of ``groups`` that wait for their group to be on."""
    return [entry for entry in EXTRA_INSTRUMENTS if GROUP_OF_TITLE.get(entry[0]) in groups]


def not_started(day: date, groups) -> List[Tuple[str, date]]:
    """Layers of ``groups`` whose instrument was not yet at work on ``day``."""
    titles = [entry[0] for entry in ESSENTIAL_INSTRUMENTS + EXTRA_INSTRUMENTS]
    return [
        (title, FIRST_DATES[title])
        for title in titles
        if title in FIRST_DATES and day < FIRST_DATES[title] and GROUP_OF_TITLE.get(title) in groups
    ]


def is_direct_image(url: str) -> bool:
    return any(host in (url or "") for host in DIRECT_HOSTS)


def refusal(url: str, status: Optional[int] = None) -> str:
    """Why a download gave no image, for the tile to say. Drive sends a web
    page, or an error, when a file's download limit is used up."""
    if "drive.google.com" in (url or "") or "googleusercontent" in (url or ""):
        return "Google Drive did not send the map. Its download limit may be used up: try later."
    if status is not None:
        return f"HTTP {status}"
    return "The archive sent a web page, not an image"


def image_moment(image) -> Optional[datetime]:
    """When ``image`` was observed, as a naive UTC datetime, if it says so.
    (``load_images`` leaves a date it could not parse as a string.)"""
    moment = getattr(image, "image_date", None)
    if not isinstance(moment, datetime):
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment


def image_day(image) -> Optional[date]:
    """The UTC date ``image`` was observed on, or None if that cannot be
    proved: Helioviewer's closest image may be from any date, and only a
    SolarMonitor file's folder dates it without a time. An archive that
    answers for one date alone gives it as ``proved_day``."""
    moment = image_moment(image)
    if moment is not None:
        return moment.date()
    proved = getattr(image, "proved_day", None)
    if isinstance(proved, date):
        return proved
    folder = ARCHIVE_FOLDER.search(getattr(image, "thumb_url", None) or "")
    if folder is None:
        return None
    try:
        return date(*map(int, folder.groups()))
    except ValueError:
        return None


def images_of_date(images, day: date) -> Tuple[list, list]:
    """``images`` split into those observed on ``day`` and the others."""
    kept, left_out = [], []
    for image in images or []:
        (kept if image_day(image) == day else left_out).append(image)
    return kept, left_out


def view_from_header(header: str) -> Tuple[str, str]:
    """Where a spacecraft stood, from an image's FITS header: for the tile and
    for its tooltip. Stonyhurst longitude is its angle from Earth."""
    found = re.search(HEADER_VALUE.format("HGLN_OBS"), header)
    distance = re.search(HEADER_VALUE.format("DSUN_OBS"), header)
    if found is None:
        return "", ""
    angle = float(found.group(1))
    words = f"{abs(angle):.0f}°{'W' if angle >= 0 else 'E'} of Earth"
    if distance is None:
        return words, f"Seen from {words}"
    return words, f"Seen from {words}, {float(distance.group(1)) / AU_METRES:.2f} AU from the Sun"


def _closest_image(nickname, date_str, source_id) -> Optional[hv.Closest]:
    """Helioviewer's closest image to a time, None if it does not say."""
    try:
        return hv.closest(source_id, date_str)
    except Exception as e:
        logger.warning("Failed to fetch image date for %s: %s", nickname, e)
    return None


def _other_view(nickname, image_id) -> Tuple[str, str]:
    """One more request, for the images that are not seen from Earth."""
    try:
        return view_from_header(hv.jp2_header_xml(image_id))
    except Exception as e:
        logger.warning("Failed to fetch the viewpoint of %s: %s", nickname, e)
    return "", ""


def fetch_from_helioviewer(
    event_date: date,
    progress: Optional[Callable[[int, int], None]] = None,
    when: time = NOON,
    layers: Optional[list] = None,
) -> List[ContextImage]:
    """
    Fetch context images from Helioviewer API as PNG screenshots.

    Args:
        event_date: Date to fetch images for
        progress: Called with (images looked up, images in all) before each lookup
        when: The UTC time of day to find the images nearest to
        layers: Entries of the instrument lists, the essential ones if None

    Returns:
        List of ContextImage objects with PNG image URLs
    """
    # Format date for Helioviewer API (ISO 8601 UTC)
    date_str = f"{event_date.strftime('%Y-%m-%d')}T{when.strftime('%H:%M:%S')}Z"
    chosen = ESSENTIAL_INSTRUMENTS if layers is None else layers
    chosen = [entry for entry in chosen if event_date >= FIRST_DATES.get(entry[0], date.min)]

    images = []

    for index, entry in enumerate(chosen):
        if progress is not None:
            progress(index, len(chosen))
        nickname, layer_path, observatory, description, source_id = entry
        fov = catalog.fov_of_layer(layer_path, nickname)
        full_size = FULL_SIZES.get(nickname, FULL_SIZE)

        found = _closest_image(nickname, date_str, source_id)

        image_date = found.date if found is not None else None

        view = ""
        if nickname in OTHER_VIEW_TITLES and found is not None and found.id is not None:
            view, seen_from = _other_view(nickname, found.id)
            description = f"{description}. {seen_from}" if seen_from else description
        field = found.field_arcsec if found is not None else 0
        if nickname.startswith("XRT") and 0 < field < LEAST_FULL_FIELD:
            description = f"{description}. Pointed at part of the Sun, {field:.0f}″ across"

        images.append(
            ContextImage(
                title=nickname,
                thumb_url=hv.screenshot_url(layer_path, date_str, hv.fov_scale(fov, THUMB_SIZE), 0, 0, THUMB_SIZE),
                page_url=hv.screenshot_url(layer_path, date_str, hv.fov_scale(fov, full_size), 0, 0, full_size),
                instrument=observatory,
                description=description,
                credits="Helioviewer",
                source_id=source_id,
                image_date=image_date,
                view=view,
            )
        )

    return images


def fetch_from_solarmonitor(event_date: date) -> List[ContextImage]:
    """
    Scrape SolarMonitor index page to find all available context images for the date.
    Returns list of ContextImage objects with thumbnail and page URLs.

    This is the legacy/fallback method.
    """
    date_str = event_date.strftime("%Y%m%d")
    base_url = "https://solarmonitor.org/"
    index_url = f"{base_url}index.php?date={date_str}"

    images = []

    try:
        from ..utils import get_global_session
    except ImportError:
        from solar_radio_image_viewer.utils import get_global_session

    session = get_global_session()

    try:
        response = session.get(index_url)

        if response.status_code != 200:
            logger.warning("SolarMonitor index fetch failed: %s", response.status_code)
            return []

        html = response.text

        # Regex to find links to full_disk.php wrapping a thumbnail
        pattern = re.compile(
            r'href=["\']?(full_disk\.php[^"\'>]+)["\']?[^>]*>[\s\S]*?<img[^>]+src=["\']?([^"\'>  ]+thumb\.png)["\']?',
            re.IGNORECASE,
        )

        matches = pattern.findall(html)

        seen_types = set()

        for page_link, thumb_path in matches:
            # Extract 'type' from page_link to use as title/ID
            type_match = re.search(r"type=([a-zA-Z0-9_]+)", page_link)
            img_type = type_match.group(1) if type_match else "unknown"

            if img_type in seen_types:
                continue
            seen_types.add(img_type)

            # Construct absolute URLs
            full_page_url = urljoin(base_url, page_link)
            full_thumb_url = urljoin(base_url, thumb_path)

            # Metadata lookup
            instrument, desc = _identify_instrument(img_type)

            images.append(
                ContextImage(
                    title=img_type.replace("_", " ").upper(),
                    thumb_url=full_thumb_url,
                    page_url=full_page_url,
                    instrument=instrument,
                    description=desc,
                    credits="SolarMonitor",
                )
            )

    except Exception as e:
        logger.warning("Error scraping SolarMonitor: %s", e)

    return images


def fetch_context_images(
    event_date: date,
    use_helioviewer: bool = True,
    progress: Optional[Callable[[int, int], None]] = None,
    when: Optional[time] = None,
) -> List[ContextImage]:
    """
    Fetch context images for a given date.

    Args:
        event_date: Date to fetch images for
        use_helioviewer: If True, use Helioviewer API (default, returns PNG).
                        If False, use SolarMonitor (strict backup only).
        progress: Called with (images looked up, images in all), for Helioviewer
        when: The UTC time of day to look near, instead of the day's noon list.
              Helioviewer only: SolarMonitor has one image a day.

    Returns:
        List of ContextImage objects
    """
    if when is not None:
        return fetch_from_helioviewer(event_date, progress, when)

    if use_helioviewer:
        images = fetch_from_helioviewer(event_date, progress)
        # The list is built whether or not Helioviewer answers, and what it
        # answers may be from another date: only an image of the day will do
        if any(image_day(image) == event_date for image in images):
            return images
        # Fall back to SolarMonitor if Helioviewer fails
        logger.info("Helioviewer has no image of %s, falling back to SolarMonitor", event_date)
        return fetch_from_solarmonitor(event_date) or images

    return fetch_from_solarmonitor(event_date)


def resolve_full_image_url(page_url: str) -> Optional[str]:
    """
    Given an image page URL, resolve to the actual full-resolution image URL.

    For Helioviewer URLs, returns the URL directly.
    For SolarMonitor URLs, scrapes the page to find the image.
    """
    # It's already a direct image URL
    if is_direct_image(page_url):
        return page_url

    # Otherwise, it's a SolarMonitor URL - scrape it
    try:
        from ..utils import get_global_session
    except ImportError:
        from solar_radio_image_viewer.utils import get_global_session

    try:
        session = get_global_session()
        response = session.get(page_url)
        if response.status_code != 200:
            return None

        html = response.text

        # Find all PNG images
        all_imgs = re.findall(r'src=["\']?([^"\'>  ]+\.png)["\']?', html, re.IGNORECASE)

        for img_path in all_imgs:
            if "thmb" not in img_path and "common_files" not in img_path:
                return urljoin("https://solarmonitor.org/", img_path)

    except Exception as e:
        logger.warning("Error resolving full image: %s", e)

    return None


def _identify_instrument(img_type: str) -> Tuple[str, str]:
    """Refine instrument name and description based on code (for SolarMonitor)."""
    code = img_type.lower()
    if "saia" in code:
        if "193" in code:
            return "SDO AIA", "193Å (Corona)"
        if "094" in code:
            return "SDO AIA", "94Å (Hot Flare)"
        if "335" in code:
            return "SDO AIA", "335Å (Active Region)"
        return "SDO AIA", "EUV Image"
    if "seit" in code:
        return "SOHO EIT", "EUV (Historical)"
    if "shmi" in code:
        return "SDO HMI", "Magnetogram"
    if "smdi" in code:
        return "SOHO MDI", "Magnetogram (Historical)"
    if "gong" in code:
        return "GONG", "H-Alpha (Chromosphere)"
    if "bbso" in code:
        return "BBSO", "H-Alpha"
    if "swap" in code:
        return "Proba-2 SWAP", "174Å (Corona)"
    if "trce" in code:
        return "TRACE", "EUV (Historical)"
    if "sxi" in code or "suvi" in code or "goes" in code:
        return "GOES", "X-Ray Imager"
    if "lasc" in code or "c2" in code or "c3" in code:
        return "SOHO LASCO", "Coronagraph"
    return "Unknown", "Solar Context"

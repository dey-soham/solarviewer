"""
The Helioviewer API, for the browser and the context images: one place that
knows the endpoints, their parameters and what they answer.

Every request goes through the app's shared rate-limited session, which
says who is asking (``User-Agent: SolarViewer/<version>``). Times in and out
are naive UTC datetimes. Nothing here touches Qt, so it all runs on any
thread.

Helioviewer draws every image as if seen from 1 AU: a solar radius is
``RSUN_ARCSEC`` on every screenshot, whatever the spacecraft's distance. The
``imageScale``, ``x0``/``y0`` and event positions are in those normalised
arcsec; ``scaleCorrection`` from ``closest()`` turns them into the
observer's true arcsec.

Everything here takes ``y0`` as Solar Y, positive north. Helioviewer's own
``y0`` (and the site's ``centerY``) grows southward, like a row number, so
the sign is turned at the request: ``screenshot_url`` and
``helioviewer_link`` are the only places that know.
"""

import logging
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlencode

from .version import __version__

logger = logging.getLogger(__name__)

BASE = "https://api.helioviewer.org/v2/"
SITE = "https://helioviewer.org/"
TIMEOUT = 30  # s, for one request
PROBE_TIMEOUT = 3
RSUN_ARCSEC = 959.63
MOST_JPX_FRAMES = 1000  # the server thins a longer answer and says so in its message
EVENT_TYPES = ("FL", "AR")
EVENT_STEP = 10 * 60  # s between the instants flares are asked at
MOST_EVENT_CALLS = 25
FITS_HISTORY = "Helioviewer display values (8-bit log-scaled), not DN"
HEADER_TIME = "%Y-%m-%dT%H:%M:%S"
CLOSEST_TIME = "%Y-%m-%d %H:%M:%S"
ISO_Z = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z?$")


class HelioviewerError(Exception):
    """Helioviewer did not answer, or not with what was asked for."""


# ---------------------------------------------------------------- requests


def session():
    """The app's shared session: rate limited, with the SolarViewer User-Agent."""
    from . import utils

    return utils.get_global_session()


def get(url: str, timeout: float = TIMEOUT, stream: bool = False):
    """GET ``url`` on the shared session, without its response cache: these
    answers change by the minute, and images are binary. ``stream=True`` for
    a download read in chunks."""
    kwargs = {"timeout": timeout}
    if stream:
        kwargs["stream"] = True
    source = session()
    from .utils.rate_limiter import CachedSession, RateLimitedSession

    if isinstance(source, CachedSession):
        # Its rate limit and User-Agent, not its response cache: that would
        # write every PNG and JP2 to disk as text and read a stream whole
        response = RateLimitedSession.get(source, url, **kwargs)
    else:
        response = source.get(url, **kwargs)
    status = getattr(response, "status_code", 200)
    if status != 200:
        raise HelioviewerError(f"Helioviewer answered HTTP {status}")
    return response


def url_of(endpoint: str, **params) -> str:
    """The address of ``endpoint`` with ``params``; None values are left out.
    Brackets, commas and colons stay as they are: Helioviewer reads
    ``[10,1,100]`` literally, and ``16:00:00Z`` is easier to read in a log."""
    query = urlencode({k: v for k, v in params.items() if v is not None}, safe="[],:")
    return f"{BASE}{endpoint}/?{query}"


def iso(when) -> str:
    """A naive UTC datetime as Helioviewer writes it: ``2024-05-14T16:00:00Z``.
    A string already in that form is passed through."""
    if isinstance(when, str):
        if not ISO_Z.match(when):
            raise ValueError(f"Not a UTC time: {when!r}")
        return when if when.endswith("Z") else when + "Z"
    if when.tzinfo is not None:
        when = when.astimezone(timezone.utc).replace(tzinfo=None)
    return when.strftime(HEADER_TIME) + "Z"


def parse_time(text: str) -> datetime:
    """``2024-05-14 16:49:57``, ``2024-05-14T16:49:57.352`` or with a ``Z``, as
    a naive UTC datetime."""
    text = str(text).strip().rstrip("Z").replace("T", " ")
    for form in (CLOSEST_TIME, CLOSEST_TIME + ".%f"):
        try:
            return datetime.strptime(text, form)
        except ValueError:
            pass
    raise ValueError(f"Not a Helioviewer time: {text!r}")


def from_unix(stamp) -> datetime:
    return datetime.fromtimestamp(float(stamp), timezone.utc).replace(tzinfo=None)


def to_unix_ms(when: datetime) -> int:
    return int(when.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _json(response):
    try:
        return response.json()
    except ValueError as e:
        raise HelioviewerError("Helioviewer did not answer in its usual form") from e


# ---------------------------------------------------------------- sources, times


def data_sources() -> dict:
    """The ``getDataSources`` tree: observatory > instrument > [detector >]
    measurement > {sourceId, nickname, layeringOrder, start, end}."""
    data = _json(get(url_of("getDataSources")))
    if not isinstance(data, dict):
        raise HelioviewerError("Helioviewer sent no list of instruments")
    return data


def frame_times(source_id: int, start: datetime, end: datetime, cadence: Optional[int] = None) -> Tuple[List[datetime], str]:
    """The archive's image times between ``start`` and ``end``, from a linked
    JPX movie asked for verbosely (one request, no movie downloaded), and the
    server's message: it says when it had to thin the list to 1000 frames.
    ``cadence`` should be about the archive's own (a cadence of 1 s made the
    server skip frames)."""
    url = url_of(
        "getJPX",
        startTime=iso(start),
        endTime=iso(end),
        sourceId=int(source_id),
        cadence=int(cadence) if cadence else None,
        linked="true",
        verbose="true",
        jpip="true",
    )
    answer = _json(get(url))
    if not isinstance(answer, dict) or "frames" not in answer:
        raise HelioviewerError(f"Helioviewer gave no frame times: {str(answer)[:200]}")
    times = sorted(from_unix(stamp) for stamp in answer["frames"] or [])
    return times, str(answer.get("message") or "")


def data_coverage(source_id: int, start: datetime, end: datetime, resolution: str = "5m") -> List[datetime]:
    """When the archive has images between ``start`` and ``end``, from
    ``getDataCoverage``: at a fine ``resolution`` one entry per image, at a
    coarse one the start of each bin (repeated by its count)."""
    url = url_of(
        "getDataCoverage",
        startDate=to_unix_ms(start),
        endDate=to_unix_ms(end),
        imageLayers=f"[{int(source_id)},1,100]",
        resolution=resolution,
    )
    answer = _json(get(url))
    rows = answer if isinstance(answer, list) else []
    times = []
    for row in rows:
        if not isinstance(row, dict) or int(row.get("sourceId", -1)) != int(source_id):
            continue
        for stamp, count in row.get("data") or []:
            when = from_unix(int(stamp) / 1000)
            if start <= when <= end:
                times.extend([when] * int(count or 0))
    return sorted(times)


@dataclass
class Closest:
    """What ``getClosestImage`` says of the archive image nearest a time."""

    id: Optional[int]
    date: Optional[datetime]
    name: str = ""
    scale: Optional[float] = None  # normalised arcsec per pixel of the original
    scale_correction: float = 1.0  # true arcsec = normalised arcsec × this
    width: Optional[int] = None
    height: Optional[int] = None
    ref_pixel_x: Optional[float] = None
    ref_pixel_y: Optional[float] = None
    rotation: float = 0.0
    rsun: Optional[float] = None  # solar radius in the original's pixels
    dsun: Optional[float] = None  # observer's distance from the Sun, m
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def field_arcsec(self) -> float:
        """How wide the original image is, in normalised arcsec."""
        return (self.width or 0) * (self.scale or 0)


def _number(value, kind=float):
    try:
        return kind(value)
    except (TypeError, ValueError):
        return None


def closest(source_id: int, when: datetime) -> Closest:
    """The archive image of ``source_id`` nearest ``when``, with its true
    time, id and geometry."""
    answer = _json(get(url_of("getClosestImage", date=iso(when), sourceId=int(source_id))))
    if not isinstance(answer, dict) or "error" in answer:
        raise HelioviewerError(f"Helioviewer has no image near {iso(when)} for source {source_id}")
    moment = None
    if answer.get("date"):
        try:
            moment = parse_time(answer["date"])
        except ValueError as e:
            logger.warning("Helioviewer gave an odd image time %r: %s", answer["date"], e)
    return Closest(
        id=_number(answer.get("id"), int),
        date=moment,
        name=str(answer.get("name") or ""),
        scale=_number(answer.get("scale")),
        scale_correction=_number(answer.get("scaleCorrection")) or 1.0,
        width=_number(answer.get("width"), int),
        height=_number(answer.get("height"), int),
        ref_pixel_x=_number(answer.get("refPixelX")),
        ref_pixel_y=_number(answer.get("refPixelY")),
        rotation=_number(answer.get("rotation")) or 0.0,
        rsun=_number(answer.get("rsun")),
        dsun=_number(answer.get("dsun")),
        raw=answer,
    )


# ---------------------------------------------------------------- images


def fov_scale(fov_arcsec: float, width_px: int) -> float:
    """The ``imageScale`` that fits ``fov_arcsec`` across ``width_px`` pixels."""
    return float(fov_arcsec) / max(1, int(width_px))


def _scale_text(value) -> str:
    """A number for a URL: no exponent, no trailing zeros, no ``-0``."""
    value = float(value)
    if value == 0:
        return "0"
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def screenshot_url(layers: str, when, image_scale: float, x0: float = 0.0, y0: float = 0.0, width: int = 1024, height: Optional[int] = None) -> str:
    """A rendered PNG from ``takeScreenshot``: ``layers`` (``[10,1,100]`` or
    several, comma-joined) at ``when``, ``image_scale`` normalised arcsec per
    pixel, centred ``x0``, ``y0`` arcsec from disk centre (Solar Y, north
    positive: Helioviewer counts its ``y0`` southward, so the sign turns
    here), ``width`` by ``height`` pixels (square if ``height`` is None),
    without a watermark."""
    return url_of(
        "takeScreenshot",
        date=iso(when),
        imageScale=_scale_text(image_scale),
        layers=layers,
        x0=_scale_text(x0),
        y0=_scale_text(-float(y0)),
        width=str(int(width)),
        height=str(int(height if height is not None else width)),
        display="true",
        watermark="false",
    )


def helioviewer_link(when, image_scale: float, layers: str, x0: float = 0.0, y0: float = 0.0) -> str:
    """The same view on helioviewer.org, for the user to open or share
    (``y0`` Solar Y north positive; the site's ``centerY`` grows south)."""
    query = urlencode(
        {
            "date": iso(when),
            "imageScale": _scale_text(image_scale),
            "imageLayers": layers,
            "centerX": _scale_text(x0),
            "centerY": _scale_text(-float(y0)),
        },
        safe="[],:",
    )
    return f"{SITE}?{query}"


def jp2_image(image_id: int) -> bytes:
    """The archive's JPEG 2000 file of image ``image_id`` (about 1 MB)."""
    response = get(url_of("getJP2Image", id=int(image_id)))
    content = response.content
    if not content or content[:50].lstrip().startswith(b"{") or content[:50].lstrip().startswith(b"<"):
        raise HelioviewerError(f"Helioviewer sent no JP2 image for id {image_id}")
    return content


def jp2_header_xml(image_id: int) -> str:
    """The XML header of image ``image_id``: ``<meta><fits>…</fits><helioviewer>…</helioviewer></meta>``."""
    text = get(url_of("getJP2Header", id=int(image_id))).text
    if not text.strip() or text.lstrip().startswith("{"):
        raise HelioviewerError(f"Helioviewer sent no header for image {image_id}")
    return text


def header_from_xml(text: str):
    """The ``<fits>`` part of a JP2 header as an ``astropy.io.fits.Header``:
    numbers as numbers, the rest as strings; keys FITS cannot take are left out."""
    from astropy.io import fits

    root = ET.fromstring(text.strip())
    section = root.find("fits") if root.tag != "fits" else root
    if section is None:
        raise HelioviewerError("The JP2 header has no FITS section")
    header = fits.Header()
    for element in section:
        value = element.text
        if value is None:
            continue
        value = value.strip()
        for convert in (int, float):
            try:
                value = convert(value)
                break
            except ValueError:
                pass
        if isinstance(value, str) and value.upper() in ("T", "F") and element.tag in ("SIMPLE", "EXTEND"):
            value = value.upper() == "T"
        try:
            header[element.tag] = value
        except (ValueError, KeyError) as e:
            logger.debug("JP2 header key %s left out: %s", element.tag, e)
    return header


def jp2_header(image_id: int):
    """The FITS header of image ``image_id``, as astropy reads it."""
    return header_from_xml(jp2_header_xml(image_id))


# ---------------------------------------------------------------- events


def events(start: datetime, end: datetime, types: Sequence[str] = EVENT_TYPES) -> List[dict]:
    """HEK events of ``types`` between ``start`` and ``end``, as Helioviewer's
    flat rows (``event_type``, ``frm_name``, ``event_starttime``,
    ``event_peaktime``, ``event_endtime``, ``hpc_x``, ``hpc_y``, ``fl_goescls``,
    ``ar_noaanum``, ``kb_archivid``…). Helioviewer only answers with the events
    active at one instant, so the range is asked at several, every
    ``EVENT_STEP`` seconds up to ``MOST_EVENT_CALLS`` of them; a flare shorter
    than the step between two instants can be missed. Active regions last the
    day, so one instant is enough for them."""
    types = tuple(types)
    rows: dict = {}
    short = tuple(t for t in types if t != "AR")
    if "AR" in types:
        _add_events(rows, start + (end - start) / 2, ("AR",))
    if short:
        span = max(0.0, (end - start).total_seconds())
        count = min(MOST_EVENT_CALLS, int(span // EVENT_STEP) + 1)
        step = span / max(1, count - 1) if count > 1 else 0
        for i in range(count):
            _add_events(rows, start + timedelta(seconds=step * i), short)
    return list(rows.values())


def _add_events(rows: dict, when: datetime, types: Iterable[str]):
    layers = ",".join(f"[{t},all,1]" for t in types)
    answer = _json(get(url_of("getEventsByEventLayers", startTime=iso(when), eventLayers=layers)))
    for row in answer if isinstance(answer, list) else []:
        if not isinstance(row, dict):
            continue
        key = row.get("kb_archivid") or (row.get("event_type"), row.get("frm_name"), row.get("event_starttime"), row.get("hpc_x"))
        rows.setdefault(key, row)


def event_times(row: dict) -> Tuple[Optional[datetime], Optional[datetime], Optional[datetime]]:
    """An event row's (start, peak, end), None where it gives none."""
    out = []
    for key in ("event_starttime", "event_peaktime", "event_endtime"):
        try:
            out.append(parse_time(row[key]) if row.get(key) else None)
        except ValueError:
            out.append(None)
    return tuple(out)


# ---------------------------------------------------------------- science data, reachability


def sci_script(source_ids: Sequence[int], start: datetime, end: datetime, image_scale: float = 2.4, lang: str = "sunpy", provider: str = "vso") -> str:
    """A ready-to-run sunpy (or ``lang="sswidl"``) script that downloads the
    original science data of ``source_ids`` between ``start`` and ``end``."""
    url = url_of(
        "getSciDataScript",
        imageScale=_scale_text(image_scale),
        sourceIds="[" + ",".join(str(int(s)) for s in source_ids) + "]",
        startDate=iso(start),
        endDate=iso(end),
        lang=lang,
        provider=provider,
    )
    text = get(url).text
    if not text.strip():
        raise HelioviewerError("Helioviewer sent an empty script")
    return text


def helioviewer_reachable(timeout: float = PROBE_TIMEOUT) -> bool:
    """Whether the Helioviewer API answers at all (this waits: call it on a thread)."""
    import requests

    try:
        requests.get(BASE, timeout=timeout, headers={"User-Agent": f"SolarViewer/{__version__} (python-requests)"})
    except requests.RequestException:
        return False
    return True


# ---------------------------------------------------------------- FITS from a JP2


def cache_root() -> Path:
    """Where Helioviewer files are kept: ``$XDG_CACHE_HOME/solarviewer/helioviewer``."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "solarviewer" / "helioviewer"


def fits_folder() -> Path:
    return cache_root() / "fits"


def fits_path(image_id: int, reduce: int = 2, folder: Optional[Path] = None) -> Path:
    name = f"{int(image_id)}.fits" if not reduce else f"{int(image_id)}_r{int(reduce)}.fits"
    return Path(folder or fits_folder()) / name


def fits_from_jp2(jp2: bytes, header, reduce: int = 2, history: str = FITS_HISTORY):
    """A ``sunpy.map.Map`` of a JP2's pixels under its archive header.
    ``reduce`` is Pillow's: the number of JPEG 2000 resolution levels left
    out, each halving the image (2 → 1024² from AIA's 4096²). The pixel
    scale and reference pixel are corrected for it. The picture's first row
    is its top (solar north); FITS counts rows from the bottom, so the rows
    are turned over, as sunpy's own JP2 reader does."""
    import io

    import numpy as np
    from PIL import Image

    image = Image.open(io.BytesIO(jp2))
    reduce = max(0, int(reduce or 0))
    if reduce:
        image.reduce = reduce
    image.load()
    data = np.asarray(image.convert("L") if image.mode not in ("L", "I;16", "I") else image).astype(np.float32)
    data = np.ascontiguousarray(data[::-1])
    header = header.copy()
    factor = 2 ** reduce
    if factor > 1:
        for axis in ("1", "2"):
            if "CDELT" + axis in header:
                header["CDELT" + axis] = float(header["CDELT" + axis]) * factor
            if "CRPIX" + axis in header:
                header["CRPIX" + axis] = (float(header["CRPIX" + axis]) - 0.5) / factor + 0.5
    header["NAXIS1"], header["NAXIS2"] = data.shape[1], data.shape[0]
    header["BITPIX"] = -32
    for key in ("BLANK", "BSCALE", "BZERO"):
        header.remove(key, ignore_missing=True)
    header.add_history(history)
    import sunpy.map

    return sunpy.map.Map(data, header)


def fits_for(frame, reduce: int = 2, folder: Optional[Path] = None) -> Path:
    """A FITS file of ``frame``'s archive image, written once into ``folder``
    (the cache's ``fits`` folder by default) and reused after. ``reduce`` as
    ``fits_from_jp2`` takes it: 2 gives a quarter-size image, 0 the whole.
    The frame's ``image_id`` is looked up from its observed time if it has
    none, and set."""
    if getattr(frame, "image_id", None) is None:
        found = closest(frame.source_id, frame.observed)
        if found.id is None:
            raise HelioviewerError("Helioviewer has no image id for this frame")
        frame.image_id = found.id
        if found.date is not None and getattr(frame, "observed", None) != found.date:
            logger.info("Frame %s observed %s, archive image %s is of %s", frame.index, frame.observed, found.id, found.date)
    path = fits_path(frame.image_id, reduce, folder)
    if path.exists():
        return path
    jp2 = jp2_image(frame.image_id)
    header = jp2_header(frame.image_id)
    solar_map = fits_from_jp2(jp2, header, reduce)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{os.getpid():x}.{path.name}.part")
    try:
        solar_map.save(str(partial), filetype="fits", overwrite=True)
        partial.replace(path)
    except Exception:
        try:
            partial.unlink()
        except OSError:
            pass
        raise
    return path

"""
Helioviewer data for the browser: the frames of a load, as records that
carry the archive's true time and the request's geometry, and the plan that
turns a time range and a step into them. Nothing here touches Qt.

All times are naive UTC datetimes. ``x0``/``y0`` are Solar X/Y in
Helioviewer's normalised arcsec, Y positive north (the request URL turns the
sign, as Helioviewer counts y0 southward).
"""

import bisect
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, List, Optional, Tuple

from . import helioviewer_api as hv
from .helioviewer_catalog import Source

logger = logging.getLogger(__name__)

PLANNED, LOADING, READY, FAILED = "planned", "loading", "ready", "failed"
HELIOVIEWER = "helioviewer"  # a frame's origin unless another archive's
SHORT_ANSWER = 0.5  # a JPX list under this share of the archive's count is checked and replaced
MOST_CLOSEST = 240  # frames a plan may fill one request at a time, when the JPX list fails it
MOST_WIDTH = 1200  # px, a request's longest side


@dataclass
class View:
    """The geometry of a request: ``width`` by ``height`` pixels at
    ``image_scale`` normalised arcsec per pixel, centred ``x0``, ``y0`` arcsec
    from disk centre (Helioviewer's x0/y0: x east to west, y south to north)."""

    image_scale: float
    x0: float = 0.0
    y0: float = 0.0
    width: int = 1024
    height: int = 1024

    @classmethod
    def of_fov(cls, fov_arcsec: float, width_px: int, x0: float = 0.0, y0: float = 0.0, height_px: Optional[int] = None) -> "View":
        """The view that fits ``fov_arcsec`` across ``width_px`` pixels (at most ``MOST_WIDTH``)."""
        width_px = max(1, min(int(width_px), MOST_WIDTH))
        height_px = width_px if height_px is None else max(1, min(int(height_px), MOST_WIDTH))
        return cls(hv.fov_scale(fov_arcsec, width_px), float(x0), float(y0), width_px, height_px)

    @property
    def fov(self) -> float:
        """The field of view across, normalised arcsec."""
        return self.image_scale * self.width

    def corners(self) -> Tuple[float, float, float, float]:
        """``(x1, y1, x2, y2)`` in normalised arcsec, left-bottom to right-top."""
        half_w, half_h = self.image_scale * self.width / 2, self.image_scale * self.height / 2
        return (self.x0 - half_w, self.y0 - half_h, self.x0 + half_w, self.y0 + half_h)


@dataclass
class Frame:
    """One frame of a load. ``wanted`` is the grid time asked for, ``observed``
    the archive's time of the image that answers it: everything shown, named
    or written uses ``observed``. A frame whose image another frame already
    has (a coarse archive under a fine step) names it in ``duplicate_of`` and
    is not downloaded again. ``image_id`` is looked up only when needed.
    ``origin`` is ``HELIOVIEWER`` unless the frame is another archive's
    (OVRO-LWA's movie), whose geometry is not known: then there is no
    request URL, no Helioviewer link and no archive image behind it."""

    index: int
    wanted: datetime
    observed: datetime
    source_id: int
    layers: str  # "[10,1,100]"
    image_scale: float  # normalised arcsec per pixel
    x0: float = 0.0
    y0: float = 0.0  # Solar Y of the centre, north positive
    width: int = 1024
    height: int = 1024
    scale_correction: float = 1.0  # true arcsec = normalised × this
    image_id: Optional[int] = None
    state: str = PLANNED  # planned, loading, ready, failed
    duplicate_of: Optional[int] = None  # index of the frame with the same image
    error: str = ""
    path: Optional[str] = None  # the PNG on disk, once downloaded
    pixmap: Any = None  # a QPixmap, made on the GUI thread, once ready
    origin: str = HELIOVIEWER

    @property
    def from_helioviewer(self) -> bool:
        return self.origin == HELIOVIEWER

    @property
    def url(self) -> str:
        return hv.screenshot_url(self.layers, self.observed, self.image_scale, self.x0, self.y0, self.width, self.height)

    @property
    def offset(self) -> float:
        """``observed - wanted`` in seconds: how far the archive's image is from the time asked for."""
        return (self.observed - self.wanted).total_seconds()

    @property
    def fov(self) -> float:
        return self.image_scale * self.width

    @property
    def link(self) -> str:
        """The same view on helioviewer.org."""
        return hv.helioviewer_link(self.observed, self.image_scale, self.layers, self.x0, self.y0)

    def provenance(self) -> dict:
        """What a sidecar records of the frame (its times and origin only
        when it is not Helioviewer's: the rest would be made up)."""
        if not self.from_helioviewer:
            return {
                "requested": self.wanted.strftime(hv.HEADER_TIME) + "Z",
                "observed": self.observed.strftime(hv.HEADER_TIME) + "Z",
                "origin": self.origin,
                "layers": self.layers,
            }
        return {
            "requested": self.wanted.strftime(hv.HEADER_TIME) + "Z",
            "observed": self.observed.strftime(hv.HEADER_TIME) + "Z",
            "image_id": self.image_id,
            "source_id": self.source_id,
            "layers": self.layers,
            "image_scale": self.image_scale,
            "x0": self.x0,
            "y0": self.y0,
            "width": self.width,
            "height": self.height,
            "scale_correction": self.scale_correction,
            "url": self.url,
            "helioviewer_link": self.link,
        }


@dataclass
class Plan:
    """The frames of a load, before any is downloaded."""

    source: Source
    start: datetime
    end: datetime
    step_s: Optional[int]  # None: every image the archive has
    view: View
    frames: List[Frame] = field(default_factory=list)
    message: str = ""  # Helioviewer's note (it thinned the list), or why the plan fell back
    by_closest: bool = False  # filled one request at a time, not from the JPX list
    scale_correction: float = 1.0
    closest: Optional[hv.Closest] = None  # the first frame's archive record

    @property
    def downloads(self) -> List[Frame]:
        """The frames that need their own download: duplicates are left out."""
        return [frame for frame in self.frames if frame.duplicate_of is None]

    @property
    def observed_times(self) -> List[datetime]:
        return [frame.observed for frame in self.frames]

    def order(self, at: Optional[datetime] = None) -> List[Frame]:
        """The downloads, nearest to ``at`` first (the first frame if None)."""
        frames = self.downloads
        if at is None or not frames:
            return frames
        return sorted(frames, key=lambda frame: (abs((frame.observed - at).total_seconds()), frame.index))

    def nearest(self, when: datetime) -> Optional[Frame]:
        """The frame observed nearest ``when``."""
        if not self.frames:
            return None
        return min(self.frames, key=lambda frame: abs((frame.observed - when).total_seconds()))


def grid(start: datetime, end: datetime, step_s: int) -> List[datetime]:
    """The times asked for: ``start``, every ``step_s``, up to ``end``."""
    step_s = max(1, int(step_s))
    count = int((end - start).total_seconds() // step_s) + 1
    return [start + timedelta(seconds=i * step_s) for i in range(max(0, count))]


def nearest_time(times: List[datetime], when: datetime) -> datetime:
    """The entry of the sorted ``times`` nearest ``when``."""
    place = bisect.bisect_left(times, when)
    choices = [times[i] for i in (place - 1, place) if 0 <= i < len(times)]
    return min(choices, key=lambda t: abs((t - when).total_seconds()))


def _frames_of(times: List[Tuple[datetime, datetime, Optional[int]]], source: Source, view: View) -> List[Frame]:
    """Frames of ``(wanted, observed, image_id)`` rows, repeats marked."""
    frames = []
    first_of: dict = {}
    for index, (wanted, observed, image_id) in enumerate(times):
        key = image_id if image_id is not None else observed
        frames.append(
            Frame(
                index=index,
                wanted=wanted,
                observed=observed,
                source_id=source.source_id,
                layers=source.layer,
                image_scale=view.image_scale,
                x0=view.x0,
                y0=view.y0,
                width=view.width,
                height=view.height,
                image_id=image_id,
                duplicate_of=first_of.get(key),
            )
        )
        first_of.setdefault(key, index)
    return frames


def jpx_cadence(source: Source, start: datetime, end: datetime, step_s: Optional[int]) -> int:
    """The cadence to ask the JPX list at: the archive's own, unless that would
    pass the server's 1000 frames and the step is coarser."""
    span = (end - start).total_seconds()
    cadence = max(1, int(source.cadence))
    if span / cadence > hv.MOST_JPX_FRAMES and step_s and step_s > cadence:
        return int(step_s)
    return cadence


def plan_frames(
    source: Source,
    start: datetime,
    end: datetime,
    step_s: Optional[int],
    view: View,
    check: Optional[Callable[[], None]] = None,
) -> Plan:
    """The frames of ``source`` between ``start`` and ``end``, one request for
    their times. ``step_s`` None ("every image") keeps every archive image;
    a step picks the archive image nearest each grid time (the list is asked
    a step wider than the range, so the end slots have a nearest too) and
    marks the repeats, so a coarse archive under a fine step is downloaded
    once. One
    ``closest()`` on the first frame gives the load its ``scale_correction``.
    When the JPX list fails, or holds under half the images the archive
    counts, the frames are found one request at a time instead (at most
    ``MOST_CLOSEST`` of them). ``check`` is called between requests, to stop."""
    if end <= start:
        raise ValueError("The end must come after the start")
    plan = Plan(source=source, start=start, end=end, step_s=step_s, view=view)
    times: List[datetime] = []
    message = problem = ""
    # With a step the list is asked a little wider than the grid, so that the
    # first and last slots can take an archive image just outside the range
    # when that is the nearest; every image keeps to the range itself
    pad = timedelta(seconds=max(int(step_s), int(source.cadence))) if step_s else timedelta()
    lo, hi = start - pad, end + pad
    try:
        times, message = hv.frame_times(source.source_id, lo, hi, jpx_cadence(source, lo, hi, step_s))
    except Exception as e:
        logger.warning("No frame list from Helioviewer for %s: %s", source.name, e)
        problem = f"Helioviewer gave no list of images: {e}"
    if check:
        check()
    plan.message = message
    if not times or _short(times, message, source, lo, hi, step_s):
        slots = grid(start, end, step_s) if step_s else grid(start, end, source.cadence)
        if len(slots) <= MOST_CLOSEST:
            logger.info("JPX list for %s has %d frames; asking frame by frame", source.name, len(times))
            return _plan_by_closest(plan, slots, check, problem)
        if not times:
            plan.message = problem or "Helioviewer gave no images for this range"
            return plan
    if step_s:
        rows = [(wanted, nearest_time(times, wanted), None) for wanted in grid(start, end, step_s)]
    else:
        rows = [(observed, observed, None) for observed in times]
    plan.frames = _frames_of(rows, source, view)
    _fill_geometry(plan, check)
    return plan


def _short(times, message, source, start, end, step_s) -> bool:
    """Whether the JPX list looks short: fewer frames than the cadence promises,
    or thinned by the server while the archive counts twice as many."""
    expected = (end - start).total_seconds() / max(1, jpx_cadence(source, start, end, step_s)) + 1
    if len(times) >= hv.MOST_JPX_FRAMES:
        return False  # the server's cap: nothing more to be had
    if not message and len(times) >= SHORT_ANSWER * expected:
        return False
    try:
        counted = len(hv.data_coverage(source.source_id, start, end))
    except Exception as e:
        logger.info("No coverage count for %s: %s", source.name, e)
        return False
    return counted > 0 and len(times) < SHORT_ANSWER * counted


def _plan_by_closest(plan: Plan, slots: List[datetime], check, problem: str = "") -> Plan:
    """Frames one ``getClosestImage`` at a time, repeats of an id marked. The
    plan's message is Helioviewer's note on the list this replaces, if any;
    ``problem``, why there was no list, only if this fails too."""
    rows = []
    first: Optional[hv.Closest] = None
    for wanted in slots:
        if check:
            check()
        try:
            found = hv.closest(plan.source.source_id, wanted)
        except Exception as e:
            logger.warning("No image near %s for %s: %s", wanted, plan.source.name, e)
            continue
        if found.date is None:
            continue
        if first is None:
            first = found
        rows.append((wanted, found.date, found.id))
    plan.frames = _frames_of(rows, plan.source, plan.view)
    plan.by_closest = True
    if not rows and not plan.message:
        plan.message = problem or "Helioviewer gave no images for this range"
    if first is not None:
        plan.closest = first
        plan.scale_correction = first.scale_correction
        for frame in plan.frames:
            frame.scale_correction = first.scale_correction
    return plan


def _fill_geometry(plan: Plan, check):
    """One ``closest()`` on the first frame: its id and the load's scale correction."""
    if not plan.frames:
        return
    if check:
        check()
    first = plan.frames[0]
    try:
        found = hv.closest(plan.source.source_id, first.observed)
    except Exception as e:
        logger.warning("No archive record for the first frame of %s: %s", plan.source.name, e)
        return
    plan.closest = found
    plan.scale_correction = found.scale_correction
    for frame in plan.frames:
        frame.scale_correction = found.scale_correction
    if found.id is not None and found.date == first.observed:
        first.image_id = found.id

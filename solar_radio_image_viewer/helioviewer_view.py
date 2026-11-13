"""
The Helioviewer browser's image view: one frame on the black well with a
coordinate frame drawn over it.

Helioviewer rescales every image so that the solar radius is 959.63 arcsec,
the value seen from 1 AU: its ``imageScale``, ``x0``/``y0`` and the frame's
field of view are in those *normalised* arcsec. The observer's true Solar X/Y
are the normalised values times ``scaleCorrection`` (1.7 % for SDO, 36 % for
Solar Orbiter). The rule here: the limb, the R☉ rings and the radio map's box
are drawn in normalised units (the limb is always 959.63″ from disk centre);
the tick ruler, the scale bar and the readout speak true arcsec.

- ``Geometry``: pixel ↔ arcsec for one frame
- ``FrameView``: the view (``ImageZoomView``) with the overlays, the pointer
  readout, the Reframe mode and the zoom cluster's calls
- ``OverlayToggles``: the Limb · Axes · Events · Radio map switches
- ``Readout``: the line under the well (Solar X/Y, R☉, observer; the region
  while reframing)
- ``draw_overlays``: the painter, shared with the export's "Overlays as shown"
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np
from PyQt5.QtCore import QPointF, QRectF, QSettings, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont, QFontMetricsF, QImage, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QCheckBox,
    QFrame,
    QGraphicsRectItem,
    QGraphicsView,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .helioviewer_api import RSUN_ARCSEC
from .noaa_events.widgets import ImageZoomView
from .ui.styles import FONT_META, MONO_FAMILY, SPACE_S, theme_manager, themed
from .ui.widgets import kicker, link_button

logger = logging.getLogger(__name__)

KM_PER_ARCSEC_1AU = 725.27  # one arcsec at 1 AU, in km (so one normalised arcsec anywhere)
TICK_STEPS = (10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000)  # arcsec
TICK_ROOM = 64  # px, the least between two tick labels
BAR_LEAST = 70  # px, the shortest scale bar
RINGS_FROM = 2.5  # the 2 and 3 R☉ rings go on when the field is wider than this, in R☉
RINGS = (2, 3)  # R☉
AR_LABELS_FROM = 120  # px, the limb's radius on screen from which region numbers are readable
LIMB_TOLERANCE = 0.02  # the limb drawn may be this far off the disk in the image
LIMB_LEAST = 60  # px, the smallest limb worth measuring
LIMB_ROOM = 8  # px, the least between the limb and the image's edge for a measurement
LEAST_DRAG = 8  # px, a region any smaller is a slip
OVERLAYS = ("limb", "axes", "events", "radio")
NO_COORDINATES = "No coordinate frame for these frames"
OVERLAY_NAMES = {"limb": "Limb", "axes": "Axes", "events": "Events", "radio": "Radio map"}
SETTINGS = ("SolarViewer", "SolarViewer")
SETTINGS_KEY = "helioviewer/overlays/{}"
OBSERVERS = {  # the short name of an observatory, as the readout says it
    "SOLO": "Solar Orbiter",
    "PROBA2": "PROBA-2",
    "STEREO_A": "STEREO Ahead",
    "STEREO_B": "STEREO Behind",
    "MLSO": "Mauna Loa",
}
SSW = "sswlatestevents"
# Observatories far from the Earth's line of sight: HEK positions and the
# radio image's field, both Earth-view, cannot be drawn on their frames
FAR_OBSERVERS = ("SOLO", "STEREO_A", "STEREO_B")
# Synodic differential rotation (Howard et al. 1990), degrees a day: how an
# active region reported at midnight has moved by the frame's time
ROTATION = (14.713 - 0.9856, -2.396, -1.787)


# ---------------------------------------------------------------- geometry


@dataclass(frozen=True)
class Geometry:
    """Pixel ↔ arcsec for one frame of ``width`` × ``height`` pixels at
    ``image_scale`` normalised arcsec per pixel, centred on (``x0``, ``y0``)
    normalised arcsec from disk centre: Solar X grows to the right, Solar Y
    upwards (row 0 at the top). True arcsec = normalised × ``scale_correction``."""

    image_scale: float
    x0: float = 0.0
    y0: float = 0.0
    width: int = 1024
    height: int = 1024
    scale_correction: float = 1.0

    def __post_init__(self):
        if not self.scale_correction or self.scale_correction <= 0:
            object.__setattr__(self, "scale_correction", 1.0)

    @classmethod
    def of_frame(cls, frame, pixmap=None) -> "Geometry":
        """``frame``'s geometry; measured on ``pixmap`` (or its own) when the
        server sent a size other than the one asked for."""
        pixmap = frame.pixmap if pixmap is None else pixmap
        width, height, scale = frame.width, frame.height, frame.image_scale
        if pixmap is not None and not pixmap.isNull() and pixmap.width() and pixmap.width() != width:
            scale = scale * width / pixmap.width()
            width, height = pixmap.width(), pixmap.height()
        return cls(scale, frame.x0, frame.y0, width, height, frame.scale_correction)

    @property
    def fov(self) -> float:
        """The field across, normalised arcsec."""
        return self.image_scale * self.width

    @property
    def fov_true(self) -> float:
        return self.fov * self.scale_correction

    @property
    def fov_rsun(self) -> float:
        return self.fov / RSUN_ARCSEC

    @property
    def true_scale(self) -> float:
        """True arcsec per pixel."""
        return self.image_scale * self.scale_correction

    def normalised_of_pixel(self, px: float, py: float) -> Tuple[float, float]:
        return (
            self.x0 + (px - self.width / 2) * self.image_scale,
            self.y0 - (py - self.height / 2) * self.image_scale,
        )

    def true_of_pixel(self, px: float, py: float) -> Tuple[float, float]:
        x, y = self.normalised_of_pixel(px, py)
        return x * self.scale_correction, y * self.scale_correction

    def pixel_of_normalised(self, x: float, y: float) -> Tuple[float, float]:
        return (
            self.width / 2 + (x - self.x0) / self.image_scale,
            self.height / 2 - (y - self.y0) / self.image_scale,
        )

    def pixel_of_true(self, x: float, y: float) -> Tuple[float, float]:
        return self.pixel_of_normalised(x / self.scale_correction, y / self.scale_correction)

    def rsun_of_pixel(self, px: float, py: float) -> float:
        """Distance from disk centre in solar radii (exact in normalised units)."""
        x, y = self.normalised_of_pixel(px, py)
        return math.hypot(x, y) / RSUN_ARCSEC

    def limb(self) -> Tuple[float, float, float]:
        """The limb's (centre x, centre y, radius) in pixels."""
        cx, cy = self.pixel_of_normalised(0.0, 0.0)
        return cx, cy, RSUN_ARCSEC / self.image_scale

    def limb_inside(self) -> bool:
        """Whether the whole limb lies in the image, with room around it."""
        cx, cy, r = self.limb()
        room = min(cx, cy, self.width - cx, self.height - cy)
        return r >= LIMB_LEAST and r + LIMB_ROOM <= room

    def corners_true(self) -> Tuple[float, float, float, float]:
        """``(x1, y1, x2, y2)`` in true arcsec, left-bottom to right-top."""
        x1, y2 = self.true_of_pixel(0, 0)
        x2, y1 = self.true_of_pixel(self.width, self.height)
        return x1, y1, x2, y2

    def contains(self, px: float, py: float) -> bool:
        return 0 <= px < self.width and 0 <= py < self.height

    def megametres(self, arcsec_true: float) -> float:
        """How long ``arcsec_true`` is on the Sun, in Mm."""
        return arcsec_true / self.scale_correction * KM_PER_ARCSEC_1AU / 1000


@dataclass(frozen=True)
class Region:
    """A region to ask Helioviewer for: centre and size in normalised arcsec
    (its own units, ready for ``View.of_fov``), with the true corners for
    the readout."""

    x0: float
    y0: float
    width: float
    height: float
    corners_true: Tuple[float, float, float, float]

    def text(self) -> str:
        x1, y1, x2, y2 = self.corners_true
        return (
            f"Region {arcsec_text(x2 - x1)} × {arcsec_text(y2 - y1)} · "
            f"X {signed_arcsec(x1)} to {signed_arcsec(x2)} · Y {signed_arcsec(y1)} to {signed_arcsec(y2)}"
        )


@dataclass(frozen=True)
class Pointer:
    """Where the pointer is on the image, in true arcsec and solar radii."""

    x: float
    y: float
    rsun: float


def region_of_pixels(geometry: Geometry, x1: float, y1: float, x2: float, y2: float) -> Region:
    """The region covering image pixels (``x1``, ``y1``) to (``x2``, ``y2``)."""
    left, right = sorted((x1, x2))
    top, bottom = sorted((y1, y2))
    nx1, ny2 = geometry.normalised_of_pixel(left, top)
    nx2, ny1 = geometry.normalised_of_pixel(right, bottom)
    sc = geometry.scale_correction
    return Region(
        (nx1 + nx2) / 2, (ny1 + ny2) / 2, nx2 - nx1, ny2 - ny1,
        (nx1 * sc, ny1 * sc, nx2 * sc, ny2 * sc),
    )


def group_digits(value: float) -> str:
    """``2000``, ``57 578``: digits grouped from five on."""
    if abs(value) < 10000:
        return f"{value:.0f}"
    return f"{value:,.0f}".replace(",", " ")


def arcsec_text(value: float) -> str:
    """``−500″``, ``0″``, ``12 000″``."""
    n = int(round(value))
    return ("−" if n < 0 else "") + group_digits(abs(n)) + "″"


def signed_arcsec(value: float) -> str:
    """``+233″``, ``−512″``, as the readout writes a coordinate."""
    n = int(round(value))
    return ("−" if n < 0 else "+") + group_digits(abs(n)) + "″"


def tick_step(span_arcsec: float, span_px: float) -> float:
    """The tick step, in arcsec, that keeps labels ``TICK_ROOM`` apart."""
    for step in TICK_STEPS:
        if span_arcsec <= 0 or step / span_arcsec * span_px >= TICK_ROOM:
            return step
    return TICK_STEPS[-1]


def bar_length(true_scale_px: float) -> float:
    """The scale bar's length in true arcsec, for ``true_scale_px`` true
    arcsec per screen pixel: the first step at least ``BAR_LEAST`` px long."""
    for step in TICK_STEPS:
        if step / true_scale_px >= BAR_LEAST:
            return step
    return TICK_STEPS[-1]


def observer_name(observatory: str) -> str:
    """How the readout names the observer: ``SDO``, ``Solar Orbiter``."""
    return OBSERVERS.get(observatory or "", observatory or "")


def readout_text(pointer: Optional[Pointer], observer: str = "") -> str:
    """``Solar X −512″  Y +233″ · 0.58 R☉ · as seen from SDO``, or
    ``off the image``."""
    if pointer is None:
        return "off the image"
    text = f"Solar X {signed_arcsec(pointer.x)}  Y {signed_arcsec(pointer.y)} · {pointer.rsun:.2f} R☉"
    if observer:
        text += f" · as seen from {observer}"
    return text


# ---------------------------------------------------------------- events


def _frm(row: dict) -> str:
    return "".join(ch for ch in str(row.get("frm_name") or "").lower() if ch.isalnum())


def _position(row: dict) -> Optional[Tuple[float, float]]:
    try:
        x, y = float(row.get("hpc_x")), float(row.get("hpc_y"))
    except (TypeError, ValueError):
        return None
    if (x == 0 and y == 0) or not (math.isfinite(x) and math.isfinite(y)):
        return None
    return x, y


def _times(row: dict) -> Tuple[Optional[datetime], Optional[datetime], Optional[datetime]]:
    from . import helioviewer_api as hv

    return hv.event_times(row)


def rotated(x: float, y: float, since: datetime, until: datetime, rsun: float = RSUN_ARCSEC) -> Optional[Tuple[float, float]]:
    """Where a feature at (``x``, ``y``) arcsec (Earth view, solar radius
    ``rsun`` arcsec) at ``since`` has rotated to by ``until``, from the
    Sun's differential rotation; None once it has gone round the limb. The
    tilt of the Sun's axis towards the Earth (under 7°) is left out."""
    sin_b = max(-1.0, min(1.0, y / rsun))
    cos_b = math.sqrt(1 - sin_b * sin_b)
    if cos_b * rsun < 1e-6:
        return x, y
    sin_l = max(-1.0, min(1.0, x / (rsun * cos_b)))
    longitude = math.asin(sin_l)
    days = (until - since).total_seconds() / 86400
    rate = ROTATION[0] + ROTATION[1] * sin_b ** 2 + ROTATION[2] * sin_b ** 4
    longitude += math.radians(rate * days)
    if abs(longitude) > math.pi / 2:
        return None
    return rsun * cos_b * math.sin(longitude), y


@dataclass
class Marker:
    """An event to draw: a label at a true-arcsec position (as reported at
    ``at``), a ring for a flare."""

    x: float
    y: float
    label: str
    kind: str  # "AR" or "FL"
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    at: Optional[datetime] = None  # when the position was reported

    def active(self, when: Optional[datetime]) -> bool:
        if self.kind != "FL":
            return True
        if when is None or self.start is None:
            return False
        end = self.end or self.start + timedelta(minutes=30)
        return self.start <= when <= end

    def position(self, when: Optional[datetime], rsun: float = RSUN_ARCSEC) -> Optional[Tuple[float, float]]:
        """The position at ``when``: an active region reported hours earlier
        has rotated on since; a flare is where it was reported."""
        if self.kind != "AR" or when is None or self.at is None:
            return self.x, self.y
        return rotated(self.x, self.y, self.at, when, rsun)


def markers_of(rows: Sequence[dict]) -> List[Marker]:
    """The markers of Helioviewer's event rows: active regions by NOAA number
    (one each), flares from SSW Latest Events when it reported any (its
    positions are the ones to trust), else from whoever did. Rows at (0, 0)
    have no position and are left out."""
    regions: dict = {}
    flares: dict = {}
    for row in rows:
        at = _position(row)
        if at is None:
            continue
        kind = str(row.get("event_type") or "")
        if kind == "AR":
            number = row.get("ar_noaanum")
            if not number:
                continue
            reported = _times(row)[0]
            regions.setdefault(int(number), Marker(at[0], at[1], str(int(number)), "AR", at=reported))
        elif kind == "FL":
            start, peak, end = _times(row)
            label = str(row.get("fl_goescls") or "").strip() or "flare"
            key = (peak or start, label)
            flares.setdefault(_frm(row) == SSW, {}).setdefault(key, Marker(at[0], at[1], label, "FL", start, end))
    chosen = flares.get(True) or flares.get(False) or {}
    return list(regions.values()) + list(chosen.values())


# ---------------------------------------------------------------- the painter


@dataclass
class Canvas:
    """Scene (image pixel) → device mapping: ``device = scene × scale + offset``."""

    scale: float = 1.0
    dx: float = 0.0
    dy: float = 0.0

    def point(self, px: float, py: float) -> QPointF:
        return QPointF(px * self.scale + self.dx, py * self.scale + self.dy)

    def rect(self, x: float, y: float, w: float, h: float) -> QRectF:
        return QRectF(x * self.scale + self.dx, y * self.scale + self.dy, w * self.scale, h * self.scale)


@dataclass
class Scene:
    """What the overlays describe: the frame's geometry and time, the events
    and the radio map's field (true arcsec corners) with its label."""

    geometry: Geometry
    observed: Optional[datetime] = None
    markers: List[Marker] = field(default_factory=list)
    radio_field: Optional[Tuple[float, float, float, float]] = None
    radio_label: str = "Radio map"
    show: dict = field(default_factory=lambda: {name: True for name in OVERLAYS})
    earth_view: bool = True  # False for an observer elsewhere: the events and the radio field are not drawn


def _pen(colour, width=1.0, style=Qt.SolidLine):
    pen = QPen(QColor(colour), width)
    pen.setCosmetic(True)
    pen.setStyle(style)
    return pen


def _mono(font: QFont, size=FONT_META) -> QFont:
    font = QFont(font)
    font.setFamily(MONO_FAMILY)
    font.setPointSizeF(size)
    return font


def draw_overlays(painter: QPainter, scene: Scene, canvas: Canvas, clip: QRectF, palette=None, font: Optional[QFont] = None):
    """Draw the overlays over the image: ``painter`` in device coordinates,
    ``canvas`` placing the image on it, ``clip`` the part of the device in
    view. The same call paints the view and an exported frame."""
    palette = palette or theme_manager.palette
    g = scene.geometry
    frame = canvas.rect(0, 0, g.width, g.height).intersected(clip)
    if frame.isEmpty():
        return
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setClipRect(frame)
    painter.setFont(_mono(font or painter.font()))
    show = scene.show
    if show.get("limb", True):
        _draw_limb(painter, g, canvas, palette)
    # HEK positions and the radio image's field are the Earth's view: on a
    # frame from elsewhere (Solar Orbiter, STEREO) they would land on the
    # wrong place, so they stay off
    if show.get("events", True) and scene.markers and scene.earth_view:
        _draw_events(painter, scene, canvas, palette)
    if show.get("radio", True) and scene.radio_field and scene.earth_view:
        _draw_radio_field(painter, scene, canvas, palette)
    if show.get("axes", True):
        _draw_ruler(painter, g, canvas, frame, palette)
        _draw_scale_bar(painter, g, canvas, frame, palette)
        _draw_north(painter, frame, palette)
    painter.restore()


def _draw_limb(painter, g, canvas, palette):
    cx, cy, r = g.limb()
    centre = canvas.point(cx, cy)
    painter.setBrush(Qt.NoBrush)
    painter.setPen(_pen(palette["text_faint"]))
    painter.drawEllipse(centre, r * canvas.scale, r * canvas.scale)
    if g.fov_rsun > RINGS_FROM:
        painter.setPen(_pen(palette["text_faint"], style=Qt.DashLine))
        for n in RINGS:
            radius = n * r * canvas.scale
            painter.drawEllipse(centre, radius, radius)
            text = f"{n} R☉"
            painter.setPen(_pen(palette["text_dim"]))
            painter.drawText(QPointF(centre.x() + 4, centre.y() - radius - 3), text)
            painter.setPen(_pen(palette["text_faint"], style=Qt.DashLine))


def _draw_events(painter, scene, canvas, palette):
    g = scene.geometry
    small_disk = g.limb()[2] * canvas.scale < AR_LABELS_FROM
    rsun_true = RSUN_ARCSEC * g.scale_correction
    for marker in scene.markers:
        if not marker.active(scene.observed) or (small_disk and marker.kind == "AR"):
            continue
        where = marker.position(scene.observed, rsun_true)
        if where is None:
            continue  # rotated past the limb
        px, py = g.pixel_of_true(*where)
        if not g.contains(px, py):
            continue
        at = canvas.point(px, py)
        if marker.kind == "FL":
            painter.setBrush(Qt.NoBrush)
            painter.setPen(_pen(palette["warning"], 1.5))
            painter.drawEllipse(at, 14, 14)
            painter.drawText(QPointF(at.x() + 17, at.y() - 4), marker.label)
        else:
            painter.setPen(_pen(palette["text_faint"]))
            painter.setBrush(QBrush(QColor(palette["text_faint"])))
            painter.drawEllipse(at, 2, 2)
            painter.setPen(_pen(palette["text_strong"]))
            painter.drawText(QPointF(at.x() + 5, at.y() - 4), marker.label)


def _draw_radio_field(painter, scene, canvas, palette):
    g = scene.geometry
    x1, y1, x2, y2 = scene.radio_field
    sc = g.scale_correction
    left, top = g.pixel_of_normalised(min(x1, x2) / sc, max(y1, y2) / sc)
    right, bottom = g.pixel_of_normalised(max(x1, x2) / sc, min(y1, y2) / sc)
    box = canvas.rect(left, top, right - left, bottom - top)
    painter.setBrush(Qt.NoBrush)
    painter.setPen(_pen(palette["highlight"], style=Qt.DashLine))
    painter.drawRect(box)
    painter.setPen(_pen(palette["highlight"]))
    metrics = QFontMetricsF(painter.font())
    y = box.top() - 4 if box.top() - metrics.height() > 0 else box.top() + metrics.ascent() + 3
    painter.drawText(QPointF(box.left() + 2, y), scene.radio_label)


def _draw_ruler(painter, g, canvas, frame, palette):
    """Ticks in true arcsec along the frame's left and bottom edges."""
    metrics = QFontMetricsF(painter.font())
    # The true arcsec in view
    x_left, y_top = g.true_of_pixel((frame.left() - canvas.dx) / canvas.scale, (frame.top() - canvas.dy) / canvas.scale)
    x_right, y_bottom = g.true_of_pixel((frame.right() - canvas.dx) / canvas.scale, (frame.bottom() - canvas.dy) / canvas.scale)
    step = tick_step(x_right - x_left, frame.width())
    bar_room = QRectF(frame.left(), frame.bottom() - 44, 220, 44)  # the scale bar's corner
    tick, text = _pen(palette["text_faint"]), _pen(palette["text_dim"])
    # Bottom edge: Solar X
    value = math.ceil(x_left / step) * step
    while value <= x_right:
        px, _ = g.pixel_of_true(value, 0)
        x = canvas.point(px, 0).x()
        painter.setPen(tick)
        painter.drawLine(QPointF(x, frame.bottom() - 6), QPointF(x, frame.bottom()))
        label = arcsec_text(value)
        box = QRectF(x - 40, frame.bottom() - 8 - metrics.height(), 80, metrics.height())
        if not box.intersects(bar_room) and box.left() >= frame.left() - 20 and box.right() <= frame.right() + 20:
            painter.setPen(text)
            painter.drawText(box, Qt.AlignHCenter | Qt.AlignVCenter, label)
        value += step
    # Left edge: Solar Y
    value = math.ceil(y_bottom / step) * step
    while value <= y_top:
        _, py = g.pixel_of_true(0, value)
        y = canvas.point(0, py).y()
        painter.setPen(tick)
        painter.drawLine(QPointF(frame.left(), y), QPointF(frame.left() + 6, y))
        box = QRectF(frame.left() + 9, y - metrics.height() / 2, 80, metrics.height())
        if not box.intersects(bar_room) and box.top() > frame.top() + 2:
            painter.setPen(text)
            painter.drawText(box, Qt.AlignLeft | Qt.AlignVCenter, arcsec_text(value))
        value += step


def _draw_scale_bar(painter, g, canvas, frame, palette):
    """``500″ = 363 Mm`` at the bottom left."""
    true_per_px = g.true_scale / canvas.scale
    length = bar_length(true_per_px)
    px = length / true_per_px
    if px > frame.width() - 24:
        return
    x, y = frame.left() + 12, frame.bottom() - 14
    painter.setPen(_pen(palette["text_strong"]))
    painter.drawLine(QPointF(x, y), QPointF(x + px, y))
    painter.drawLine(QPointF(x, y - 3), QPointF(x, y + 3))
    painter.drawLine(QPointF(x + px, y - 3), QPointF(x + px, y + 3))
    painter.setPen(_pen(palette["text_dim"]))
    painter.drawText(QPointF(x, y - 6), f"{arcsec_text(length)} = {group_digits(g.megametres(length))} Mm")


def _draw_north(painter, frame, palette):
    """An arrow and N at the top centre: solar north is up."""
    x = frame.center().x()
    top = frame.top()
    painter.setPen(_pen(palette["text_faint"]))
    painter.drawLine(QPointF(x, top + 22), QPointF(x, top + 8))
    painter.drawLine(QPointF(x - 3, top + 12), QPointF(x, top + 8))
    painter.drawLine(QPointF(x + 3, top + 12), QPointF(x, top + 8))
    painter.setPen(_pen(palette["text_dim"]))
    painter.drawText(QPointF(x + 5, top + 19), "N")


# ---------------------------------------------------------------- the limb check


def grey_array(image: QImage, longest: int = 1024) -> np.ndarray:
    """``image`` as a grey float array, no wider than ``longest`` pixels."""
    if max(image.width(), image.height()) > longest:
        image = image.scaled(longest, longest, Qt.KeepAspectRatio, Qt.FastTransformation)
    image = image.convertToFormat(QImage.Format_Grayscale8)
    pointer = image.constBits()
    pointer.setsize(image.byteCount())
    rows = np.frombuffer(pointer, dtype=np.uint8).reshape(image.height(), image.bytesPerLine())
    return rows[:, : image.width()].astype(float)


def measure_limb(grey: np.ndarray, cx: float, cy: float, expected: float) -> Optional[float]:
    """The radius, in pixels, of the steepest fall in brightness around
    (``cx``, ``cy``) within 30 % of ``expected``: the limb, on a disk
    image. None when there is nothing to measure."""
    h, w = grey.shape
    room = min(cx, cy, w - cx, h - cy)  # beyond the image there is nothing to average
    low, high = expected * 0.7, min(expected * 1.3, room - 1)
    if high < 4 or high <= low:
        return None
    yy, xx = np.ogrid[:h, :w]
    r = np.hypot(xx - cx, yy - cy)
    mask = (r >= low) & (r < high)
    if not mask.any():
        return None
    bins = (r[mask] - low).astype(int)
    counts = np.bincount(bins)
    profile = np.bincount(bins, weights=grey[mask]) / np.maximum(1, counts)
    if len(profile) < 5 or profile.max() - profile.min() < 8:
        return None  # flat: no disk here
    smooth = np.convolve(profile, np.ones(3) / 3, mode="valid")  # bin i of this is bin i + 1 of the profile
    fall = np.gradient(smooth)
    return float(low + np.argmin(fall[1:-1]) + 2 + 0.5)


def check_limb(image: QImage, geometry: Geometry) -> Optional[Tuple[float, float]]:
    """(measured, expected) limb radius in image pixels, or None when the
    limb is not in the image to measure."""
    if not geometry.limb_inside():
        return None
    grey = grey_array(image)
    factor = grey.shape[1] / geometry.width
    cx, cy, r = geometry.limb()
    measured = measure_limb(grey, cx * factor, cy * factor, r * factor)
    if measured is None:
        return None
    return measured / factor, r


# ---------------------------------------------------------------- the view


class FrameView(ImageZoomView):
    """The browser's image: one frame at a time on the well (the stored
    pixmaps are only shown, never changed), the overlays drawn over it, the
    pointer's Solar X/Y told, a region picked in Reframe mode."""

    frame_changed = pyqtSignal()  # another frame (or none) is shown
    pointer = pyqtSignal(object)  # Pointer, or None off the image
    zoom_changed = pyqtSignal(float)  # the view's scale, 1.0 = one image pixel per screen pixel
    region_changed = pyqtSignal(object)  # Region while dragging, None when there is none
    reframe_requested = pyqtSignal(object)  # Region: load this region
    reframe_ended = pyqtSignal()  # Reframe mode left, by a request or Esc
    limb_checked = pyqtSignal(float, float)  # measured, expected radius (px) on the first ready frame

    def __init__(self, parent=None, settings: Optional[QSettings] = None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._frame = None
        self._geometry: Optional[Geometry] = None
        self._scene = None
        self._markers: List[Marker] = []
        self._radio_field = None
        self._radio_label = "Radio map"
        self.observer = ""
        self.earth_view = True
        self.coordinates_known = True  # False for frames with no geometry (OVRO-LWA's movie): no overlays, no readout
        self._settings = settings
        self.overlays = {name: self._saved(name) for name in OVERLAYS}
        self._limb_checked = False
        # Reframe mode
        self.reframing = False
        self._region: Optional[Region] = None
        self._drag_from: Optional[QPointF] = None
        self._box = QGraphicsRectItem()
        self._box.setZValue(2)
        self._box.hide()
        self.scene().addItem(self._box)
        themed(self, self._style_box)
        themed(self, lambda _palette: self.viewport().update())

    # -- settings

    def _saved(self, name) -> bool:
        settings = self._settings or QSettings(*SETTINGS)
        return settings.value(SETTINGS_KEY.format(name), True, type=bool)

    def set_overlay(self, name: str, on: bool):
        """Turn the overlay ``name`` (limb, axes, events, radio) on or off, and remember it."""
        self.overlays[name] = bool(on)
        settings = self._settings or QSettings(*SETTINGS)
        settings.setValue(SETTINGS_KEY.format(name), bool(on))
        self.viewport().update()

    # -- what is shown

    @property
    def frame(self):
        return self._frame

    @property
    def frame_geometry(self) -> Optional[Geometry]:
        """The current frame's ``Geometry`` (the widget's own ``geometry()`` is its place on screen)."""
        return self._geometry

    def set_frame(self, frame):
        """Show ``frame``'s pixmap (None clears the well): the picture's
        pixmap changes, nothing else is rebuilt."""
        self._frame = frame
        pixmap = frame.pixmap if frame is not None else None
        if pixmap is None or pixmap.isNull():
            self._picture.setPixmap(QPixmap())
            self._geometry = None
            self.viewport().update()
            self.frame_changed.emit()
            return
        resized = pixmap.size() != self._picture.pixmap().size()
        self._picture.setPixmap(pixmap)
        self._geometry = Geometry.of_frame(frame, pixmap) if self.coordinates_known else None
        if resized:
            self.scene().setSceneRect(self._picture.boundingRect())
            if self._fitted:
                self.fit()
        self.viewport().update()
        self.frame_changed.emit()
        if not self._limb_checked and self._geometry is not None:
            self._limb_checked = True
            QTimer.singleShot(0, lambda: self._check_limb(pixmap, self._geometry))

    def set_events(self, rows: Sequence[dict]):
        """The HEK rows (``helioviewer_api.events``) to mark: active regions
        by number, flares ringed while they last."""
        self._markers = markers_of(rows)
        self.viewport().update()

    def set_radio_field(self, corners_true, label: str = "Radio map"):
        """Box the radio image's field: ``(x1, y1, x2, y2)`` true arcsec, or None."""
        self._radio_field = tuple(corners_true) if corners_true else None
        self._radio_label = label
        self.viewport().update()

    def set_observer(self, observatory: str):
        """Who saw the image, for the readout ("SDO", "Solar Orbiter"); from
        far off the Earth's line of sight the Earth-view overlays stay off."""
        self.observer = observer_name(observatory)
        self.earth_view = (observatory or "") not in FAR_OBSERVERS
        self.viewport().update()

    def set_coordinates_known(self, known: bool):
        """Whether the frames shown have a geometry (Helioviewer's do; a
        movie's do not): without one there are no overlays and no readout."""
        self.coordinates_known = bool(known)
        if self._frame is not None:
            self.set_frame(self._frame)

    def reset_limb_check(self):
        """The next frame shown is checked again (a new load)."""
        self._limb_checked = False

    def overlay_scene(self) -> Optional[Scene]:
        """What the overlays describe now, for the export's painter."""
        if self._geometry is None:
            return None
        return Scene(
            self._geometry,
            self._frame.observed if self._frame is not None else None,
            self._markers,
            self._radio_field,
            self._radio_label,
            dict(self.overlays),
            self.earth_view,
        )

    def canvas(self) -> Canvas:
        """Image pixels → viewport pixels, as the view stands."""
        origin = self.mapFromScene(QPointF(0, 0))
        return Canvas(self.transform().m11(), origin.x(), origin.y())

    def drawForeground(self, painter, rect):
        scene = self.overlay_scene()
        if scene is None:
            return
        painter.save()
        painter.setWorldMatrixEnabled(False)
        draw_overlays(painter, scene, self.canvas(), QRectF(self.viewport().rect()), font=self.font())
        painter.restore()

    # -- the limb check

    def _check_limb(self, pixmap, geometry):
        if geometry is None or pixmap is None or pixmap.isNull():
            return
        try:
            result = check_limb(pixmap.toImage(), geometry)
        except Exception as e:  # a check, never in the way of the image
            logger.warning("Could not measure the limb: %s", e)
            return
        if result is not None:
            self.limb_checked.emit(*result)

    # -- zoom (the footer's cluster)

    def zoom_in(self):
        self._zoom_by(1.25)

    def zoom_out(self):
        self._zoom_by(1 / 1.25)

    def _zoom_by(self, factor):
        if self._picture.pixmap().isNull():
            return
        scale = self.transform().m11()
        wanted = min(self.MAX_SCALE, scale * factor)
        if wanted <= self._fit_scale():
            self.fit()
            return
        self._fitted = False
        self.scale(wanted / scale, wanted / scale)
        self._note_zoom()

    def _fit_scale(self) -> float:
        rect = self._picture.boundingRect()
        if rect.isEmpty():
            return 1.0
        return min(self.viewport().width() / rect.width(), self.viewport().height() / rect.height())

    def fit(self):
        super().fit()
        self._note_zoom()

    def actual_size(self):
        super().actual_size()
        self._note_zoom()

    def zoom_percent(self) -> float:
        return self.transform().m11() * 100

    def zoom_text(self) -> str:
        """``100%``, ``36%``: image pixels to screen pixels."""
        return f"{self.zoom_percent():.0f}%"

    def _note_zoom(self):
        self.zoom_changed.emit(self.transform().m11())

    def wheelEvent(self, event):
        super().wheelEvent(event)
        self._note_zoom()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._note_zoom()

    # -- the pointer

    def pointer_at(self, scene_pos: QPointF) -> Optional[Pointer]:
        g = self._geometry
        if g is None or not g.contains(scene_pos.x(), scene_pos.y()):
            return None
        x, y = g.true_of_pixel(scene_pos.x(), scene_pos.y())
        return Pointer(x, y, g.rsun_of_pixel(scene_pos.x(), scene_pos.y()))

    def mouseMoveEvent(self, event):
        where = self.mapToScene(event.pos())
        if self.reframing and self._drag_from is not None:
            self._extend_region(where)
        else:
            super().mouseMoveEvent(event)
        self.pointer.emit(self.pointer_at(where))

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.pointer.emit(None)

    # -- Reframe mode

    def _style_box(self, palette):
        self._box.setPen(_pen(palette["highlight"]))
        fill = QColor(palette["highlight_soft"])
        fill.setAlpha(90)
        self._box.setBrush(QBrush(fill))

    def start_reframe(self):
        """Pick a region: a drag on the image draws it, Esc leaves."""
        if self._geometry is None:
            return
        self.reframing = True
        self._region = None
        self._drag_from = None
        self._box.hide()
        self.setDragMode(QGraphicsView.NoDrag)
        self.viewport().setCursor(Qt.CrossCursor)
        self.setFocus()
        self.region_changed.emit(None)

    def cancel_reframe(self):
        if not self.reframing:
            return
        self._leave_reframe()
        self.reframe_ended.emit()

    def _leave_reframe(self):
        self.reframing = False
        self._drag_from = None
        self._region = None
        self._box.hide()
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.viewport().unsetCursor()
        self.region_changed.emit(None)

    @property
    def region(self) -> Optional[Region]:
        """The region drawn, if any."""
        return self._region

    def request_region(self):
        """Ask for the region drawn (the "Load this region" link) and leave the mode."""
        region = self._region
        if region is None:
            return
        self._leave_reframe()
        self.reframe_requested.emit(region)
        self.reframe_ended.emit()

    def _begin_region(self, where: QPointF):
        self._drag_from = where
        self._region = None
        self._box.setRect(QRectF(where, where))
        self._box.show()

    def _box_rect(self, where: QPointF) -> QRectF:
        g = self._geometry
        rect = QRectF(self._drag_from, where).normalized()
        return rect.intersected(QRectF(0, 0, g.width, g.height))

    def _extend_region(self, where: QPointF):
        if self._drag_from is None or self._geometry is None:
            return
        rect = self._box_rect(where)
        self._box.setRect(rect)
        self._region = region_of_pixels(self._geometry, rect.left(), rect.top(), rect.right(), rect.bottom())
        self.region_changed.emit(self._region)

    def _end_region(self, where: QPointF):
        if self._drag_from is None:
            return
        rect = self._box_rect(where)
        too_small = rect.width() * self.transform().m11() < LEAST_DRAG or rect.height() * self.transform().m11() < LEAST_DRAG
        self._drag_from = None
        if too_small:
            self._region = None
            self._box.hide()
            self.region_changed.emit(None)
            return
        self._box.setRect(rect)
        self._region = region_of_pixels(self._geometry, rect.left(), rect.top(), rect.right(), rect.bottom())
        self.region_changed.emit(self._region)

    def mousePressEvent(self, event):
        if self.reframing and event.button() == Qt.LeftButton and self._geometry is not None:
            self._begin_region(self.mapToScene(event.pos()))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self.reframing and self._drag_from is not None and event.button() == Qt.LeftButton:
            self._end_region(self.mapToScene(event.pos()))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.reframing:
            self.cancel_reframe()
            event.accept()
            return
        super().keyPressEvent(event)


# ---------------------------------------------------------------- the switches and the readout


class OverlayToggles(QWidget):
    """The OVERLAYS block: Limb · Axes · Events · Radio map switches, each
    remembered, driving ``view``'s overlays."""

    def __init__(self, view: FrameView, parent=None):
        super().__init__(parent)
        self.view = view
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_S)
        layout.addWidget(kicker("Overlays"))
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(SPACE_S)
        grid.setVerticalSpacing(SPACE_S)
        self.switches = {}
        for i, name in enumerate(OVERLAYS):
            switch = QCheckBox(OVERLAY_NAMES[name])
            switch.setObjectName("Switch")
            switch.setCursor(Qt.PointingHandCursor)
            switch.setChecked(view.overlays.get(name, True))
            switch.toggled.connect(lambda on, name=name: view.set_overlay(name, on))
            grid.addWidget(switch, i // 2, i % 2)
            self.switches[name] = switch
        layout.addLayout(grid)
        self._tips = {
            "limb": "The solar limb, 959.63 arcsec from disk centre, and the 2 and 3 solar radii rings on wide fields",
            "axes": "Solar X and Y ticks, the scale bar and the north mark",
            "events": "Active region numbers (rotated on to the frame's time) and flares in progress, from the Heliophysics Events Knowledgebase",
            "radio": "The field of the radio image this browser was opened from",
        }
        for name, tip in self._tips.items():
            self.switches[name].setToolTip(tip)

    def set_radio_available(self, available: bool):
        """The Radio map switch is only there when a radio image's field is known."""
        self.switches["radio"].setVisible(bool(available))

    def set_earth_view(self, earth_view: bool, observer: str = ""):
        """Events and the radio field are Earth-view positions: the switches
        are greyed, and say why, for frames from elsewhere."""
        for name, what in (("events", "Event positions"), ("radio", "The radio image's field")):
            switch = self.switches[name]
            switch.setEnabled(bool(earth_view))
            if earth_view:
                switch.setToolTip(self._tips[name])
            else:
                seen = f"from {observer}" if observer else "from there"
                switch.setToolTip(f"{what} are as seen from the Earth: they cannot be drawn on frames {seen}")


class Readout(QFrame):
    """The line under the well: Solar X/Y, distance from disk centre in solar
    radii and the observer, in monospace; the frame's scale and field at the
    right. While reframing, the region's size and corners and the "Load this
    region" link; a note when the limb drawn misses the disk."""

    def __init__(self, view: FrameView, parent=None):
        super().__init__(parent)
        self.setObjectName("Pinned")
        self.view = view
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.setSpacing(SPACE_S)
        self.coords = QLabel("—")
        self.coords.setObjectName("Mono")
        self.coords.setMinimumWidth(self.coords.fontMetrics().horizontalAdvance("Solar X −0000″  Y −0000″ · 0.00 R☉"))
        layout.addWidget(self.coords)
        self.note = QLabel("")
        self.note.setObjectName("Hint")
        self.note.hide()
        layout.addWidget(self.note)
        self.load_region = link_button("Load this region", view.request_region)
        self.load_region.hide()
        layout.addWidget(self.load_region)
        layout.addStretch()
        self.aside = QLabel("")
        self.aside.setObjectName("DetailMono")
        self.aside.setProperty("muted", "true")
        self.aside.setToolTip("The request's scale and field, in Helioviewer's arcsec (the Sun 959.63 arcsec across its radius)")
        layout.addWidget(self.aside)
        view.pointer.connect(self.show_pointer)
        view.region_changed.connect(self.show_region)
        view.reframe_ended.connect(self._reframe_ended)
        view.limb_checked.connect(self.show_limb_check)
        view.frame_changed.connect(self.show_frame)
        self._pointer_text = "—"

    def _blank(self) -> str:
        """What the coordinates say with no geometry: nothing, or why."""
        if self.view.frame is not None and not self.view.coordinates_known:
            return NO_COORDINATES
        return "—"

    def show_frame(self):
        """The aside follows the frame shown."""
        g = self.view.frame_geometry
        if g is None:
            self.aside.setText("")
            self._pointer_text = self._blank()
            if not self.view.reframing:
                self.coords.setText(self._pointer_text)
            return
        scale = f"{g.image_scale:.3g}".rstrip("0").rstrip(".") if g.image_scale < 10 else f"{g.image_scale:.0f}"
        self.aside.setText(f"{scale}″/px · {arcsec_text(g.fov)}")

    def show_pointer(self, pointer):
        self._pointer_text = readout_text(pointer, self.view.observer) if self.view.frame_geometry is not None else self._blank()
        if not self.view.reframing:
            self.coords.setText(self._pointer_text)

    def show_region(self, region):
        if not self.view.reframing:
            return
        self.load_region.show()
        self.load_region.setEnabled(region is not None)
        if region is None:
            self.coords.setText("Drag a box on the image · Esc leaves")
        else:
            self.coords.setText(region.text())

    def _reframe_ended(self):
        self.load_region.hide()
        self.coords.setText(self._pointer_text)

    def show_limb_check(self, measured, expected):
        off = abs(measured - expected) / expected if expected else 0.0
        if off > LIMB_TOLERANCE:
            self.note.setText(f"Limb drawn {off * 100:.0f} % off the disk in this image")
            self.note.setToolTip(
                f"The disk's edge in the image is at {measured:.0f} px from centre, the limb drawn at {expected:.0f} px: "
                "Helioviewer's scale for this image may be off."
            )
            self.note.show()
        else:
            self.note.hide()


def overlay_painter(view: FrameView) -> Callable[[QPainter, QRectF], None]:
    """A painter for an exported frame at the image's own size: draws the
    overlays as the view shows them (``draw_overlays`` with a unit canvas).
    Call it with a painter on the frame's QImage and that image's rect."""

    def paint(painter: QPainter, rect: QRectF, scene: Optional[Scene] = None):
        scene = scene or view.overlay_scene()
        if scene is not None:
            draw_overlays(painter, scene, Canvas(), rect, font=view.font())

    return paint

"""
Saving from the Helioviewer browser: the current frame, the animation as
MP4 or GIF, the frames as PNG files, the science-data script and the
Helioviewer link. Every file written gets a JSON sidecar that says exactly
which archive images it holds and how they were asked for.

Everything shown, named or written uses a frame's **observed** time, the
archive's own, never the grid time asked for. Outcomes go through the
window's ``say()`` line with a "Show in folder" link; nothing here opens a
message box.
"""

import dataclasses
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PyQt5.QtCore import QSettings, Qt, QRect, QUrl
from PyQt5.QtGui import QColor, QDesktopServices, QFont, QImage, QPainter, QPen
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from . import helioviewer_api as hv
from .helioviewer_timeline import Playback
from .ui.app import SETTINGS
from .ui.busy import busy
from .ui.errors import show_error
from .ui.geometry import fit_to_screen
from .ui.segmented_control import SegmentedControl
from .ui.styles import MONO_FAMILY, SPACE_S
from .ui.widgets import button_bar, dialog_layout, kicker
from .version import __version__
from .video.ui.progress import run_with_dialog

logger = logging.getLogger(__name__)

KINDS = ("MP4", "GIF", "PNG frames")
RANGES = ("All", "Band zoom", "Cursor to end")
FPS_CHOICES = Playback.FPS  # the playback's rates, so the dialog's prefilled speed is one of them
DIFF_MODES = ("None", "Running", "Base")
MOST_ID_FAILURES = 2  # consecutive failed id look-ups before the rest are left blank
IMAGE_NOTE = "Helioviewer display values (8-bit, log-scaled), not DN"
COORDINATES_NOTE = (
    "x0, y0: centre of the field in Helioviewer's normalised arcsec (the Sun 959.63 arcsec in radius), "
    "Solar Y positive north; the request URL's y0 is Helioviewer's, positive south"
)
NOT_HELIOVIEWER = "Not from Helioviewer: the OVRO-LWA movie has no archive images behind it"
LAZY_KEPT = 2  # frame images an export keeps decoded at once, besides the base of a difference


class _Stop(Exception):
    """The export was cancelled."""


# ---------------------------------------------------------------- names and stamps


def stamp_text(name, observed, note=""):
    """What is printed on a frame: ``AIA 171 2024-05-14 16:49:57 UT``, with
    ``note`` after it when the frame has one (an OVRO-LWA movie's name)."""
    text = f"{name} {observed:%Y-%m-%d %H:%M:%S} UT"
    return f"{text} · {note}" if note else text


def name_time(observed):
    """A time as it goes in a file name: ``20240514_164957``."""
    return f"{observed:%Y%m%d_%H%M%S}"


def file_stem(name, prefix="helioviewer"):
    """``helioviewer_AIA_171`` for ``AIA 171`` (``prefix`` says where the
    frames came from: ``ovro`` for an OVRO-LWA movie's)."""
    safe = re.sub(r"[^A-Za-z0-9.+-]+", "_", name).strip("_") or "frame"
    return f"{prefix}_{safe}"


def range_name(frames):
    """``20240514_160021-162933``: the first and last observed times."""
    first, last = frames[0].observed, frames[-1].observed
    return f"{name_time(first)}-{last:%H%M%S}" if first.date() == last.date() else f"{name_time(first)}-{name_time(last)}"


def sidecar_path(path):
    """The JSON beside ``path``: ``movie.mp4.json``, or ``frames.json`` in a folder."""
    path = Path(path)
    return path / "frames.json" if path.is_dir() else path.with_name(path.name + ".json")


def sidecar(kind, path, frames, source, settings=None, files=None, note="", images=None):
    """What a sidecar records: the app, the file, how the export was made,
    and every frame's provenance (``Frame.provenance``), with its file name
    when each frame is a file of its own. ``images`` says what the pixels
    are (Helioviewer's screenshots unless the host says otherwise)."""
    rows = []
    for i, frame in enumerate(frames):
        row = frame.provenance()
        if files is not None and i < len(files):
            row = {"file": files[i], **row}
        rows.append(row)
    data = {
        "app": f"SolarViewer {__version__}",
        "written": datetime.now(timezone.utc).strftime(hv.HEADER_TIME) + "Z",
        "kind": kind,
        "file": Path(path).name,
        "source": source,
        "images": images or f"Helioviewer.org takeScreenshot · {IMAGE_NOTE}",
        "times": "observed: the archive image's own time (UTC); requested: the grid time asked for",
        "coordinates": COORDINATES_NOTE,
    }
    if settings is not None:
        data["export"] = settings.as_dict()
    if note:
        data["note"] = note
    data["frames"] = rows
    return data


def write_sidecar(path, data):
    """Write ``data`` as the JSON beside ``path``; the sidecar's path."""
    target = sidecar_path(path)
    partial = target.with_name(target.name + ".part")
    with open(partial, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, default=str)
    os.replace(partial, target)
    return target


def fetch_ids(frames, progress=None, cancelled=None):
    """Fill in the archive image id of every frame that lacks one, one
    ``getClosestImage`` per distinct observed time (frames sharing an image
    share the answer). Stops asking after ``MOST_ID_FAILURES`` failures in a
    row, or when ``cancelled()``; returns what the sidecar should note of
    that ("" when every id is known). Runs on the export's thread."""
    missing = {}
    for frame in frames:
        if frame.image_id is None:
            missing.setdefault((frame.source_id, frame.observed), []).append(frame)
    failures = total = 0
    for number, ((source_id, observed), group) in enumerate(missing.items(), 1):
        if cancelled is not None and cancelled():
            return "image ids: incomplete, the export was cancelled while looking them up"
        if progress is not None:
            progress(f"Looking up image ids: {number} of {len(missing)}", number, len(missing))
        try:
            found = hv.closest(source_id, observed)
        except Exception as e:
            logger.warning("No image id for %s at %s: %s", source_id, observed, e)
            failures += 1
            if failures >= MOST_ID_FAILURES:
                return "image ids: incomplete, Helioviewer did not answer"
            continue
        failures = 0
        if found.id is None:
            total += 1
            continue
        for frame in group:
            frame.image_id = found.id
    return ""


def show_in_folder(path):
    """Open the folder holding ``path`` in the system's file manager."""
    folder = Path(path)
    if not folder.is_dir():
        folder = folder.parent
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


# ---------------------------------------------------------------- images (QImage: these run on a thread)


def _rgb_array(image):
    """``image`` as an array of rows of RGB pixels."""
    import numpy as np

    image = image.convertToFormat(QImage.Format_RGB888)
    width = image.width()
    height = image.height()
    ptr = image.bits()
    ptr.setsize(image.byteCount())
    # Handle scanline stride (bytesPerLine) to avoid skewing
    arr = np.frombuffer(ptr, np.uint8).reshape((height, image.bytesPerLine()))
    # Slice out the padding bytes at the end of each scanline
    return arr[:, : width * 3].reshape((height, width, 3)).copy()


def difference_image(current, reference):
    """Pixel-wise grayscale difference of two QImages, or None if their sizes
    differ."""
    import numpy as np

    if current.size() != reference.size():
        return None
    height, width = current.height(), current.width()
    # Current minus reference, 128 for "no change"
    diff = _rgb_array(current).astype(np.int16) - _rgb_array(reference).astype(np.int16)
    diff = (diff * 2.5 + 128).clip(0, 255)
    gray_arr = np.mean(diff, axis=2).astype(np.uint8)
    result = QImage(gray_arr.data, width, height, width, QImage.Format_Grayscale8)
    return result.copy()  # copy() ensures memory safety


def stamp_font(height):
    """The stamp's font: monospace, sized to the image (6 to 22 pt)."""
    font = QFont(MONO_FAMILY, max(6, min(22, int(height * 0.030))))
    font.setStyleHint(QFont.Monospace)
    font.setBold(True)
    return font


def stamped_image(image, timestamp_text, show_bg=True):
    """Copy of ``image`` with ``timestamp_text`` at its bottom left, in
    monospace sized to the image, on a translucent dark box if ``show_bg``."""
    result = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)
    font = stamp_font(result.height())
    painter.setFont(font)
    font_size = font.pointSize()
    metrics = painter.fontMetrics()
    text_rect = metrics.boundingRect(timestamp_text)
    padding_h = max(4, font_size)
    padding_v = max(2, font_size // 2)
    margin = max(4, font_size // 2)
    bg_rect = QRect(
        margin,
        result.height() - text_rect.height() - padding_v * 2 - margin,
        text_rect.width() + padding_h * 2,
        text_rect.height() + padding_v * 2,
    )
    if show_bg:
        painter.fillRect(bg_rect, QColor(0, 0, 0, 100))
    painter.setPen(QPen(QColor(255, 255, 255)))
    painter.drawText(
        bg_rect.adjusted(padding_h, padding_v, -padding_h, -padding_v),
        Qt.AlignLeft | Qt.AlignVCenter,
        timestamp_text,
    )
    painter.end()
    return result


def painted_image(image, frame, painter_fn):
    """Copy of ``image`` with the window's overlays drawn on it by
    ``painter_fn(painter, frame, width, height)``, the same painter the
    view uses, in the image's own pixels."""
    result = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.Antialiasing)
    try:
        painter_fn(painter, frame, result.width(), result.height())
    finally:
        painter.end()
    return result


class LazyImages:
    """The QImages of an export, one at a time: a frame with a file on disk
    is read from it when asked for (on the export's thread), so the export
    never holds more than a few decoded frames, whatever the plan's size;
    a frame with no file (OVRO-LWA's, or one the disk refused) is given
    decoded. The base of a difference, asked for again and again, is kept."""

    def __init__(self, frames, ready=None, keep=LAZY_KEPT, pinned=None, context=()):
        self.frames = list(frames)
        self.context = list(context) or self.frames  # every frame, for a repeat's original
        self.ready = dict(ready or {})  # index into frames → QImage already decoded
        self.keep = max(1, int(keep))
        self.pinned = pinned  # the base's position, kept once read
        self._recent = {}  # position → QImage, the last few read

    def __len__(self):
        return len(self.frames)

    def __iter__(self):
        for i in range(len(self.frames)):
            yield self[i]

    def __getitem__(self, i):
        if i < 0:
            i += len(self.frames)
        if not 0 <= i < len(self.frames):
            raise IndexError(i)
        if i in self.ready:
            return self.ready[i]
        if i in self._recent:
            return self._recent[i]
        image = _read_frame_file(self.frames[i], self.context)
        if i == self.pinned:
            self.ready[i] = image
            return image
        self._recent[i] = image
        while len(self._recent) > self.keep:
            del self._recent[next(iter(self._recent))]
        return image


def _file_path(frame, frames=()):
    """The PNG on disk of ``frame`` or of the frame it repeats, or None."""
    path = getattr(frame, "path", None)
    if not path and frame.duplicate_of is not None and 0 <= frame.duplicate_of < len(frames):
        path = getattr(frames[frame.duplicate_of], "path", None)
    return path if path and os.path.exists(path) else None


def _read_frame_file(frame, frames=()):
    path = _file_path(frame, frames)
    if not path:
        raise Exception(f"The frame of {frame.observed:%H:%M:%S} UTC is no longer on disk")
    image = QImage(path)
    if image.isNull():
        raise Exception(f"The cached frame {os.path.basename(path)} could not be read")
    return image


def composed_frames(images, frames, stamps, diff_mode="None", base=0, overlay=None):
    """Each of ``images`` as the export shows it: the difference against the
    base or the frame before (``diff_mode``), the overlays of ``overlay``
    (a painter, or None), then its stamp of ``stamps`` ("" for none). An
    RGB array each. ``frames`` are the records the overlay painter reads."""
    base = min(max(0, base), len(images) - 1) if images else 0
    for i, image in enumerate(images):
        if diff_mode != "None":
            ref = base if diff_mode == "Base" else i - 1
            if ref < 0 or ref == i:
                # Nothing to subtract: neutral grey, as the window shows it
                gray = QImage(image.size(), QImage.Format_RGB32)
                gray.fill(QColor(128, 128, 128))
                image = gray
            else:
                difference = difference_image(image, images[ref])
                if difference is not None:
                    image = difference
        if overlay is not None and i < len(frames):
            image = painted_image(image, frames[i], overlay)
        if i < len(stamps) and stamps[i]:
            # No box in difference mode, where it would read as a feature
            image = stamped_image(image, stamps[i], show_bg=(diff_mode == "None"))
        yield _rgb_array(image)


def _export_frames(images, stamps, diff_type):
    """The frames as ``composed_frames`` gives them, with no overlays."""
    return composed_frames(images, [], stamps, diff_type)


def _partial_path(file_path):
    """Written first, so that a cancelled export leaves any earlier file there."""
    stem, ext = os.path.splitext(str(file_path))
    return f"{stem}.part{ext}"


def write_gif(images, stamps, diff_type, file_path, duration_ms, progress, cancelled, frames=(), base=0, overlay=None):
    """Write the frames as an animated GIF with the given speed. Raises _Stop
    if ``cancelled()``."""
    try:
        from PIL import Image
    except ImportError:
        raise Exception("Pillow is needed to write a GIF. Install it with: pip install Pillow")

    total = len(images)
    rows = composed_frames(images, frames, stamps, diff_type, base, overlay)

    # Pillow takes the frames as they come, and quantizes each as it does
    # (the longest part), so these counts are how far it really is
    def later_frames():
        for number, frame in enumerate(rows, 2):
            if cancelled():
                raise _Stop()
            progress(f"Encoding frame {number} of {total}", number, total)
            yield Image.fromarray(frame)
        progress("Writing the GIF file…")

    partial = _partial_path(file_path)
    try:
        Image.fromarray(next(rows)).save(
            partial,
            format="GIF",
            save_all=True,
            append_images=later_frames(),
            duration=duration_ms,
            loop=0,
        )
        if cancelled():
            raise _Stop()
        os.replace(partial, file_path)
    finally:
        if os.path.exists(partial):
            os.remove(partial)


def write_mp4(images, stamps, diff_type, file_path, fps, progress, cancelled, frames=(), base=0, overlay=None):
    """Write the frames as an H.264 MP4 video (with imageio's ffmpeg writer,
    as Create Video does). Raises _Stop if ``cancelled()``."""
    import imageio

    total = len(images)
    progress("Starting the encoder…")
    partial = _partial_path(file_path)
    out = imageio.get_writer(
        partial,
        format="FFMPEG",
        fps=fps,
        codec="libx264",
        quality=None,
        pixelformat="yuv420p",
        macro_block_size=1,
        output_params=["-crf", "18"],
    )
    try:
        try:
            for number, arr in enumerate(composed_frames(images, frames, stamps, diff_type, base, overlay), 1):
                if cancelled():
                    raise _Stop()
                progress(f"Encoding frame {number} of {total}", number, total)
                height, width = arr.shape[:2]
                # H.264 (yuv420p) needs even sizes
                out.append_data(arr[: height - height % 2, : width - width % 2])
            progress("Finishing the video file…")
        finally:
            out.close()
        if cancelled():
            raise _Stop()
        os.replace(partial, file_path)
    finally:
        if os.path.exists(partial):
            os.remove(partial)


def write_frames(images, stamps, diff_type, folder, names, progress, cancelled, frames=(), base=0, overlay=None):
    """Write the frames as PNG files ``names`` in ``folder``, composed as
    the animation would be. Cancelled, the files so far stay. Returns the
    names written."""
    from PIL import Image

    written = []
    total = len(images)
    for number, (arr, name) in enumerate(zip(composed_frames(images, frames, stamps, diff_type, base, overlay), names), 1):
        partial = os.path.join(folder, f".{name}.part")
        Image.fromarray(arr).save(partial, format="PNG")
        os.replace(partial, os.path.join(folder, name))
        written.append(name)
        progress(f"Saving frames: {number} of {total}", number, total)
        if cancelled():  # (after the frame: a composed frame is kept, not thrown away)
            break
    return written


# ---------------------------------------------------------------- the dialog


@dataclasses.dataclass
class ExportSettings:
    """What the export dialog was told."""

    kind: str = "MP4"  # MP4, GIF, PNG frames
    fps: int = 4
    stamp: bool = True
    overlays: bool = False
    difference: bool = False
    which: str = "All"  # All, Band zoom, Cursor to end

    def as_dict(self):
        return dataclasses.asdict(self)


def _settings():
    return QSettings(*SETTINGS)


def remembered_settings(fps=None):
    """The last export's choices, ``fps`` (the playback's) over the saved one."""
    store = _settings()
    kind = store.value("helioviewer/export/kind", "MP4")
    which = store.value("helioviewer/export/range", "All")
    saved_fps = store.value("helioviewer/export/fps", 4, type=int)
    return ExportSettings(
        kind=kind if kind in KINDS else "MP4",
        fps=int(fps) if fps else (saved_fps if saved_fps in FPS_CHOICES else 4),
        stamp=store.value("helioviewer/export/stamp", True, type=bool),
        overlays=store.value("helioviewer/export/overlays", False, type=bool),
        difference=store.value("helioviewer/export/difference", False, type=bool),
        which=which if which in RANGES else "All",
    )


def remember_settings(settings):
    store = _settings()
    store.setValue("helioviewer/export/kind", settings.kind)
    store.setValue("helioviewer/export/fps", settings.fps)
    store.setValue("helioviewer/export/stamp", settings.stamp)
    store.setValue("helioviewer/export/overlays", settings.overlays)
    store.setValue("helioviewer/export/difference", settings.difference)
    store.setValue("helioviewer/export/range", settings.which)


def _switch(text, on, enabled=True, tip=""):
    box = QCheckBox(text)
    box.setObjectName("Switch")
    box.setChecked(bool(on) and enabled)
    box.setEnabled(enabled)
    if tip:
        box.setToolTip(tip)
    return box


class ExportDialog(QDialog):
    """One dialog for every export: the format, the speed, what is drawn
    on the frames, and which frames. ``candidates`` maps each of ``RANGES``
    to the frames it would export (a range with none is left out);
    ``diff_mode`` is the window's difference mode ("None" for none) and
    ``has_overlays`` whether it can draw its overlays on a frame."""

    def __init__(self, parent, candidates, settings, diff_mode="None", has_overlays=False, zoomed=False):
        super().__init__(parent)
        self.setWindowTitle("Export")
        self.setWindowModality(Qt.WindowModal)
        self.candidates = candidates
        self.ranges = [name for name in RANGES if candidates.get(name) and (name != "Band zoom" or zoomed)]
        if not self.ranges:
            self.ranges = ["All"]
        layout = dialog_layout(self)

        layout.addWidget(kicker("Format"))
        self.kind = SegmentedControl(list(KINDS))
        self.kind.setSelectedIndex(KINDS.index(settings.kind) if settings.kind in KINDS else 0)
        layout.addWidget(self.kind)

        speed = QHBoxLayout()
        speed.setSpacing(SPACE_S)
        self.speed_label = QLabel("Speed")
        speed.addWidget(self.speed_label)
        self.fps = SegmentedControl([f"{n} fps" for n in FPS_CHOICES])
        self.fps.setSelectedIndex(FPS_CHOICES.index(settings.fps) if settings.fps in FPS_CHOICES else 2)
        speed.addWidget(self.fps)
        speed.addStretch()
        layout.addLayout(speed)

        layout.addWidget(kicker("On the frames"))
        switches = QWidget()
        column = QVBoxLayout(switches)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACE_S)
        self.stamp = _switch("Time stamp", settings.stamp, tip="The instrument and the observed time, UTC, in monospace at the bottom left")
        self.overlays = _switch(
            "Overlays as shown", settings.overlays, has_overlays,
            "The limb, axes, events and radio field as the window draws them" if has_overlays else "The window has no overlays on",
        )
        diff_on = diff_mode in DIFF_MODES and diff_mode != "None"
        self.difference = _switch(
            f"Apply difference ({diff_mode})" if diff_on else "Apply difference", settings.difference, diff_on,
            "" if diff_on else "No difference mode is on in the window",
        )
        for box in (self.stamp, self.overlays, self.difference):
            column.addWidget(box)
        layout.addWidget(switches)

        layout.addWidget(kicker("Frames"))
        self.which = SegmentedControl(self.ranges)
        self.which.setSelectedIndex(self.ranges.index(settings.which) if settings.which in self.ranges else 0)
        layout.addWidget(self.which)
        self.count = QLabel()
        self.count.setObjectName("Hint")
        layout.addWidget(self.count)

        box, self.buttons = button_bar(primary=("Export", self.accept), secondary=("Cancel", self.reject), parent=self)
        layout.addWidget(box)
        self.kind.selectionChanged.connect(lambda _i: self._refresh())
        self.which.selectionChanged.connect(lambda _i: self._refresh())
        self._refresh()
        fit_to_screen(self, 440, self.sizeHint().height())

    def frames(self):
        """The frames the chosen range exports."""
        return list(self.candidates.get(self.which.selectedText(), []))

    def _refresh(self):
        frames = self.frames()
        animation = self.kind.selectedText() != "PNG frames"
        self.fps.setEnabled(animation)
        self.speed_label.setEnabled(animation)
        if not frames:
            text = "No frame has loaded yet"
        elif len(frames) == 1:
            text = f"1 frame · {frames[0].observed:%H:%M:%S} UTC"
        else:
            text = f"{len(frames)} frames · {frames[0].observed:%H:%M:%S}–{frames[-1].observed:%H:%M:%S} UTC"
        if animation and len(frames) < 2:
            text += " · an animation needs at least 2"
        self.count.setText(text)
        self.buttons["Export"].setEnabled(bool(frames) and (not animation or len(frames) >= 2))

    def settings(self):
        return ExportSettings(
            kind=self.kind.selectedText(),
            fps=FPS_CHOICES[self.fps.selectedIndex()],
            stamp=self.stamp.isChecked(),
            overlays=self.overlays.isChecked() and self.overlays.isEnabled(),
            difference=self.difference.isChecked() and self.difference.isEnabled(),
            which=self.which.selectedText(),
        )

    @classmethod
    def ask(cls, parent, candidates, settings, **kwargs):
        """Run the dialog; ``(settings, frames)`` or ``(None, [])`` if cancelled."""
        dialog = cls(parent, candidates, settings, **kwargs)
        try:
            if dialog.exec_() != QDialog.Accepted:
                return None, []
            return dialog.settings(), dialog.frames()
        finally:
            dialog.deleteLater()


# ---------------------------------------------------------------- the mixin


class HelioviewerExportMixin:
    """Saves frames and animations from the browser, and hands frames to
    the viewer. Mixed into the browser window; relies on its attributes via
    ``self``.

    Host interface (what the window must provide):

    ``export_frames()``
        The plan's frames (``helioviewer_data.Frame``) in order. A ready
        frame carries its ``pixmap``; a duplicate may leave it None, the
        original's is used. Frames without an image are left out of exports.
    ``current_frame_index()``
        The index (into ``export_frames()``) of the frame shown.
    ``source_name()``
        The instrument's name as shown, "AIA 171": stamps and file names.
    ``say(text, link=None)``
        The window's note line; ``link`` is ``(label, callable)`` for a link
        after the text ("Show in folder").
    ``diff_mode()`` → "None" | "Running" | "Base" and ``diff_base()`` → index
        (optional; no difference without them)
    ``band_zoom()`` → ``(start, end)`` naive UTC or None
        (optional; the "Band zoom" range)
    ``overlay_painter()`` → ``fn(painter, frame, width, height)`` or None
        (optional; "Overlays as shown")
    ``fps()`` → the playback speed (optional; the dialog's prefilled speed)
    ``frame_note(frame)`` → text after the time in stamps (optional;
        OVRO-LWA's "from the 2024-05-14 movie")
    ``file_prefix()`` → the first word of file names (optional; "helioviewer"
        unless the frames are OVRO-LWA's: "ovro")
    ``images_note()`` → what the sidecar says the pixels are (optional;
        Helioviewer's screenshots unless the host says otherwise)
    ``frames_are_helioviewer()`` → False when the frames shown are another
        archive's (OVRO-LWA's movie): no link, no FITS, no script (optional)
    ``_close_after_export`` and ``close()``
        The window's close deferred until an export's dialog has gone.

    What the window calls: ``save_current_frame`` (Ctrl+S), ``export_animation``
    (Export…, Ctrl+E), ``batch_download`` (the same, PNG frames first),
    ``copy_helioviewer_link``, ``science_data_script``, ``use_in_viewer_menu()``
    (a QMenu for the "Use in viewer" button).
    """

    _use_menu = None

    # --- what the host has

    def _host(self, name, default=None):
        method = getattr(self, name, None)
        if method is None:
            return default
        try:
            return method()
        except Exception as e:  # a host hook must never stop an export
            logger.warning("Helioviewer export: %s() failed: %s", name, e)
            return default

    def _frames(self):
        return list(self._host("export_frames", []) or [])

    def _name(self):
        return str(self._host("source_name", "") or "Helioviewer")

    def _stem(self):
        return file_stem(self._name(), str(self._host("file_prefix", "") or "helioviewer"))

    def _sidecar(self, kind, path, frames, **kwargs):
        return sidecar(kind, path, frames, self._name(), images=self._host("images_note"), **kwargs)

    def _stamp(self, frame):
        note = ""
        if hasattr(self, "frame_note"):
            note = self.frame_note(frame) or ""
        return stamp_text(self._name(), frame.observed, note)

    def _image_of(self, frame, frames):
        """The frame's pixmap in memory, or the pixmap of the frame it repeats."""
        pixmap = frame.pixmap
        if pixmap is None and frame.duplicate_of is not None and 0 <= frame.duplicate_of < len(frames):
            pixmap = frames[frame.duplicate_of].pixmap
        return pixmap if pixmap is not None and not pixmap.isNull() else None

    def _has_image(self, frame, frames):
        """Whether the frame can be exported: its pixmap is in memory, or its
        file is on disk (a frame dropped from memory, read again in the job)."""
        return self._image_of(frame, frames) is not None or _file_path(frame, frames) is not None

    def _from_helioviewer(self):
        return bool(self._host("frames_are_helioviewer", True))

    def export_candidates(self):
        """The frames with an image, by range: every one, those within the
        band's zoom, those from the current frame on."""
        frames = self._frames()
        loaded = [frame for frame in frames if self._has_image(frame, frames)]
        out = {"All": loaded}
        zoom = self._host("band_zoom")
        if zoom:
            start, end = zoom
            out["Band zoom"] = [frame for frame in loaded if start <= frame.observed <= end]
        current = int(self._host("current_frame_index", 0) or 0)
        out["Cursor to end"] = [frame for frame in loaded if frame.index >= current]
        return out

    def current_export_frame(self):
        """The frame shown, with an image; None if there is none."""
        frames = self._frames()
        current = int(self._host("current_frame_index", 0) or 0)
        for frame in frames:
            if frame.index == current and self._has_image(frame, frames):
                return frame
        return None

    def _said(self, text, path=None):
        """``say`` the outcome, with "Show in folder" when there is a file."""
        link = ("Show in folder", lambda p=path: show_in_folder(p)) if path else None
        self.say(text, link)

    def _after_job(self):
        """Whether a close was asked for during the job (and done now)."""
        if getattr(self, "_close_after_export", False):
            self.close()
            return True
        return False

    # --- the actions

    def save_current_frame(self):
        """Save the frame shown as a PNG, with its sidecar."""
        frame = self.current_export_frame()
        if frame is None:
            self.say("No frame to save yet")
            return
        frames = self._frames()
        default_name = f"{self._stem()}_{name_time(frame.observed)}.png"
        file_path, _ = QFileDialog.getSaveFileName(self, "Save frame", default_name, "PNG image (*.png)")
        if not file_path:
            return
        if not file_path.lower().endswith(".png"):
            file_path += ".png"
        pixmap = self._image_of(frame, frames)
        held = pixmap.toImage() if pixmap is not None else None  # (else the file is read in the job)

        def work(progress, cancelled):
            progress(f"Saving {os.path.basename(file_path)}…")
            image = held if held is not None else _read_frame_file(frame, frames)
            if not image.save(file_path, "PNG"):
                raise Exception("The image could not be written")
            note = fetch_ids([frame], progress, cancelled)
            write_sidecar(file_path, self._sidecar("png", file_path, [frame], note=note))
            return file_path

        outcome = run_with_dialog(self, "Saving the frame", "Saving the frame…", work)
        if self._after_job():
            return
        if outcome.error:
            show_error(self, "Could not save the frame", "The image could not be saved. Check the path and its permissions.", outcome.error)
        elif outcome.result:
            self._said(f"Saved {os.path.basename(file_path)}", file_path)

    def batch_download(self):
        """Every frame as a PNG file: the export dialog, PNG frames first."""
        self.export_animation(kind="PNG frames")

    def export_animation(self, kind=None):
        """Export the frames as an MP4, a GIF or PNG files, as the dialog says."""
        candidates = self.export_candidates()
        if not candidates["All"]:
            self.say("No frame has loaded yet")
            return
        settings = remembered_settings(self._host("fps"))
        if kind in KINDS:
            settings.kind = kind
        diff_mode = str(self._host("diff_mode", "None") or "None")
        painter = self._host("overlay_painter")
        settings, frames = ExportDialog.ask(
            self, candidates, settings, diff_mode=diff_mode, has_overlays=painter is not None, zoomed=bool(self._host("band_zoom"))
        )
        if settings is None:
            return
        remember_settings(settings)
        if settings.kind == "PNG frames":
            self._export_frames_to_folder(settings, frames, painter if settings.overlays else None, diff_mode)
        else:
            self._export_movie(settings, frames, painter if settings.overlays else None, diff_mode)

    def _prepare(self, settings, frames, diff_mode):
        """The images (read one at a time on the export's thread, from the
        frames' files; a frame without one is decoded here), stamps and
        difference settings of an export."""
        all_frames = self._frames()
        stamps = [self._stamp(frame) if settings.stamp else "" for frame in frames]
        diff = diff_mode if settings.difference and diff_mode in DIFF_MODES else "None"
        base = 0
        if diff == "Base":
            base_index = int(self._host("diff_base", 0) or 0)
            base = next((i for i, frame in enumerate(frames) if frame.index == base_index), 0)
        ready = {}
        with busy(f"Preparing {len(frames)} frames…", self):
            for i, frame in enumerate(frames):
                if _file_path(frame, all_frames) is None:
                    ready[i] = self._image_of(frame, all_frames).toImage()
        images = LazyImages(frames, ready, pinned=base if diff == "Base" else None, context=all_frames)
        return images, stamps, diff, base

    def _export_movie(self, settings, frames, painter, diff_mode):
        ext = settings.kind.lower()
        default_name = f"{self._stem()}_{range_name(frames)}.{ext}"
        file_filter = "GIF image (*.gif)" if ext == "gif" else "MP4 video (*.mp4)"
        file_path, _ = QFileDialog.getSaveFileName(self, "Export animation", default_name, file_filter)
        if not file_path:
            return
        if not file_path.lower().endswith(f".{ext}"):  # the encoder goes by it
            file_path += f".{ext}"
        images, stamps, diff, base = self._prepare(settings, frames, diff_mode)

        def work(progress, cancelled):
            if ext == "gif":
                write_gif(images, stamps, diff, file_path, int(1000 / settings.fps), progress, cancelled, frames, base, painter)
            else:
                write_mp4(images, stamps, diff, file_path, float(settings.fps), progress, cancelled, frames, base, painter)
            note = fetch_ids(frames, progress, cancelled)
            write_sidecar(file_path, self._sidecar(ext, file_path, frames, settings=settings, note=note))
            return file_path

        outcome = run_with_dialog(self, "Exporting animation", "Exporting animation…", work)
        if self._after_job():
            return
        if outcome.cancelled and not outcome.result:
            self.say("Export cancelled")
        elif outcome.error:
            show_error(self, "Could not export the animation", "The animation could not be written. Check the path and the free disk space.", outcome.error)
        else:
            self._said(f"Exported {len(frames)} frames to {os.path.basename(file_path)}", file_path)

    def _export_frames_to_folder(self, settings, frames, painter, diff_mode):
        dir_path = QFileDialog.getExistingDirectory(self, "Choose a folder for the frames")
        if not dir_path:
            return
        folder = os.path.join(dir_path, f"{self._stem()}_{range_name(frames)}")
        try:
            os.makedirs(folder, exist_ok=True)
        except Exception as e:
            show_error(self, "Could not create the folder", f"The folder could not be created:\n{folder}", e)
            return
        images, stamps, diff, base = self._prepare(settings, frames, diff_mode)
        names = [f"{self._stem()}_{name_time(frame.observed)}.png" for frame in frames]

        def work(progress, cancelled):
            written = write_frames(images, stamps, diff, folder, names, progress, cancelled, frames, base, painter)
            kept = frames[: len(written)]
            note = "" if len(written) == len(frames) else f"cancelled after {len(written)} of {len(frames)} frames"
            note = " · ".join(n for n in (note, fetch_ids(kept, progress, cancelled)) if n)
            write_sidecar(folder, self._sidecar("png frames", folder, kept, settings=settings, files=written, note=note))
            return len(written)

        outcome = run_with_dialog(self, "Saving frames", "Saving frames…", work)
        if self._after_job():
            return
        if outcome.error:
            show_error(self, "Could not save the frames", f"Saving the frames to this folder failed:\n{folder}", outcome.error)
        elif outcome.cancelled:
            self._said(f"Cancelled · {outcome.result or 0} of {len(frames)} frames saved", folder)
        else:
            self._said(f"Saved {outcome.result} frames to {os.path.basename(folder)}", folder)

    def copy_helioviewer_link(self):
        """The current frame's view on helioviewer.org, to the clipboard."""
        frame = self.current_export_frame()
        frames = self._frames()
        if frame is None:
            current = int(self._host("current_frame_index", 0) or 0)
            frame = next((f for f in frames if f.index == current), None)
        if frame is None:
            self.say("No frame to link to yet")
            return
        if not self._from_helioviewer() or not getattr(frame, "from_helioviewer", True):
            self.say(NOT_HELIOVIEWER)
            return
        QApplication.clipboard().setText(frame.link)
        self.say(f"Helioviewer link copied · {frame.observed:%H:%M:%S} UTC")

    def science_data_script(self):
        """A sunpy script (VSO) that downloads the science data behind the
        loaded frames, from Helioviewer, saved where the user says."""
        frames = [frame for frame in self._frames() if getattr(frame, "from_helioviewer", True)]
        if not frames or not self._from_helioviewer():
            self.say("Load Helioviewer frames first: the script covers their time range" if self._from_helioviewer() else NOT_HELIOVIEWER)
            return
        start, end = min(f.observed for f in frames), max(f.observed for f in frames)
        if end <= start:
            end = start + timedelta(seconds=1)
        source_ids = list(dict.fromkeys(frame.source_id for frame in frames))
        image_scale = frames[0].image_scale
        default_name = f"{self._stem()}_{range_name(frames)}_sunpy.py"
        file_path, _ = QFileDialog.getSaveFileName(self, "Save the science-data script", default_name, "Python script (*.py)")
        if not file_path:
            return

        def work(progress, cancelled):
            progress("Asking Helioviewer for the script…")
            text = hv.sci_script(source_ids, start, end, image_scale)
            partial = file_path + ".part"
            with open(partial, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(partial, file_path)
            return file_path

        outcome = run_with_dialog(self, "Science-data script", "Asking Helioviewer for the script…", work)
        if self._after_job():
            return
        if outcome.error:
            show_error(self, "Could not get the script", "Helioviewer did not give the script. Try again in a moment.", outcome.error)
        elif outcome.result:
            self._said(f"Script saved: {os.path.basename(file_path)}", file_path)

    # --- the viewer

    def use_in_viewer_menu(self):
        """The "Use in viewer" menu: the current frame as a FITS file in the
        viewer (a new tab, beside the radio image, as a contour layer) or
        saved. Made once; its entries follow the frame and the viewer when
        it opens."""
        if self._use_menu is None:
            from . import helioviewer_use as use

            menu = QMenu("Use in viewer", self)
            self._use_actions = {
                "tab": menu.addAction("Open as new tab", lambda: self._use(use.open_as_new_tab)),
                "beside": menu.addAction("Open beside the radio image", lambda: self._use(use.open_beside)),
                "contour": menu.addAction("Add as contour layer", lambda: self._use(use.add_contour_layer)),
            }
            menu.addSeparator()
            self._use_actions["save"] = menu.addAction("Save FITS…", lambda: self._use(use.save_fits))
            menu.addSeparator()
            full = menu.addAction("Full size (4096 px)")
            full.setCheckable(True)
            full.setChecked(_settings().value("helioviewer/fits_full_size", False, type=bool))
            full.setToolTip("The whole archive image; otherwise a quarter-size 1024 px one")
            full.toggled.connect(lambda on: _settings().setValue("helioviewer/fits_full_size", on))
            self._use_actions["full"] = full
            menu.addSeparator()
            script = menu.addAction("Science-data script (sunpy/VSO)…", self.science_data_script)
            script.setToolTip("A sunpy script that downloads the original science data behind the loaded frames")
            self._use_actions["script"] = script
            menu.aboutToShow.connect(self._refresh_use_menu)
            self._use_menu = menu
        return self._use_menu

    def _refresh_use_menu(self):
        from . import helioviewer_use as use

        frame = self.current_export_frame()
        parent = self.parent()
        helioviewer = self._from_helioviewer()
        has_frame = frame is not None and helioviewer
        viewer = use.main_window(parent) is not None
        pane = use.pane_of(parent)
        actions = self._use_actions
        actions["tab"].setEnabled(has_frame and viewer)
        actions["beside"].setEnabled(has_frame and pane is not None and getattr(pane, "page", None) is not None)
        actions["contour"].setEnabled(has_frame and pane is not None and bool(getattr(pane, "imagename", "")))
        actions["save"].setEnabled(has_frame)
        actions["full"].setEnabled(helioviewer)
        actions["script"].setEnabled(helioviewer and bool(self._frames()))
        reasons = {"tab": "Open an image in SolarViewer first", "beside": "Open an image in SolarViewer first", "contour": "Open a radio image in SolarViewer first", "save": ""}
        for key, reason in reasons.items():
            if actions[key].isEnabled():
                actions[key].setToolTip("")
            elif not helioviewer:
                actions[key].setToolTip(NOT_HELIOVIEWER)
            else:
                actions[key].setToolTip("Load a frame first" if frame is None else reason)

    def _use(self, action):
        frame = self.current_export_frame()
        if frame is None:
            self.say("Load a frame first")
            return
        if not self._from_helioviewer() or not getattr(frame, "from_helioviewer", True):
            self.say(NOT_HELIOVIEWER)
            return
        reduce = 0 if self._use_actions["full"].isChecked() else 2
        action(self.parent(), frame, self._name(), reduce=reduce, say=self.say, dialog_parent=self)

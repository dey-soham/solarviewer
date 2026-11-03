"""
OVRO-LWA's daily movies in the Helioviewer browser. They are not from
Helioviewer: the OVRO-LWA portal (NJIT) names each day's movie, a 3 by 4
montage of 12 bands, and its quick-look folder lists one image per frame,
which gives each frame its time.

The movie and its table of times are kept in the solar events cache, and
asked for again with the server's tags, as old days are replotted too.
"""

import logging
import math
import re
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import List, Optional

import numpy as np
from PyQt5.QtCore import QBuffer, QByteArray, QIODevice, QThread, Qt, pyqtSignal
from PyQt5.QtGui import QImage, QImageReader

from .version import __version__

logger = logging.getLogger(__name__)

OBSERVATORY = "OVRO-LWA"
LAYER = "ovro-lwa:"  # what an entry's layer starts with: it is no Helioviewer layer
MONTAGE = "all"
BANDS = ("34", "39", "43", "48", "52", "57", "62", "66", "71", "75", "80", "84")  # MHz, as the panels go
ALL_BANDS = "LWA all bands"
LEAST_CADENCE = 10  # s: the closest frames are; the interval keeps all of them at this
TIME_SLACK = 2  # s: the stamps of a steady cadence wander by a second

# The montage's panels, inclusive pixel boxes, of a frame of this size
FRAME_SIZE = (1200, 976)
COLUMNS = ((83, 353), (357, 627), (632, 902), (906, 1176))
ROWS = ((58, 328), (333, 602), (607, 877))
BOXES = {
    band: (COLUMNS[i % 4][0], ROWS[i // 4][0], COLUMNS[i % 4][1], ROWS[i // 4][1])
    for i, band in enumerate(BANDS)
}
SEPARATOR_COLUMNS = ((354, 356), (628, 631), (903, 905))  # white between the panels
SEPARATOR_ROWS = ((329, 332), (603, 606))
WHITE = 230

PORTAL = "https://ovsa.njit.edu/lwa/portal/preview/movie/{day}"
LISTING = "https://ovsa.njit.edu/lwa-data/qlook_images/slow/synop/{day:%Y/%m/%d}/"
FRAME_NAME = re.compile(
    r'href="(ovro-lwa[\w.-]*?\.(\d{4})-(\d\d)-(\d\d)T(\d\d)(\d\d)(\d\d)Z\.image_I\.png)"'
)
USER_AGENT = f"SolarViewer/{__version__} (python-requests)"
TIMEOUT = 60
CHUNK_BYTES = 256 * 1024
DOWNLOAD_TRIES = 3  # a cut download goes on from where it stopped, this many times

CHECK_WINDOW = 3  # frames each side of a checked one that its image must beat
CHECK_RATIO = 0.6  # of the next best: the frame's own image is clearly the best
CHECK_ALONE = 3.0  # mean difference, of 255, that a movie of one frame must be within
MOST_PIXELS = 4000 * 4000  # of a checked image: they are 1200 by 975

MEMORY_BYTES = 1536 * 1024 * 1024  # all the frames of a load, as the window holds them
BYTES_PER_PIXEL = 4
MOST_FRAMES = 5000
MOST_DAYS = 7  # of movies in a range before the browser asks: each is a 5 to 20 MB download
MOST_RUNS = 40  # of frames, picked in ffmpeg; beyond, the frames between are read and dropped


_DAY_LOCKS = {}  # a day's movie is downloaded by one loader at a time
_DAY_LOCKS_LOCK = threading.Lock()


class LwaProblem(Exception):
    """What went wrong, as a sentence for the user."""


class _Stopped(Exception):
    pass


class _Cut(Exception):
    """The connection ended before the file did."""


def entries():
    """The browser's entries: the montage, then a band each. The layer names
    the source and the view; the cadence is the least, as it varies."""
    rows = [(ALL_BANDS, LAYER + MONTAGE, OBSERVATORY, LEAST_CADENCE)]
    rows += [(band_name(band), LAYER + band, OBSERVATORY, LEAST_CADENCE) for band in BANDS]
    return rows


def band_name(band):
    return f"LWA {band} MHz"


def is_lwa(entry):
    return bool(entry) and isinstance(entry[1], str) and entry[1].startswith(LAYER)


def view_of(entry):
    """``MONTAGE`` or a band's MHz."""
    return entry[1][len(LAYER):]


def _get(url, **kwargs):
    import requests

    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    try:
        return requests.get(url, timeout=TIMEOUT, headers=headers, **kwargs)
    except requests.RequestException as e:
        raise LwaProblem("Could not reach OVRO-LWA") from e


def movie_url(day):
    """The address of ``day``'s movie, as the portal gives it now (replots
    move it), or None if there is none."""
    from .solar_context.radio_images import lwa_movie_of

    response = _get(PORTAL.format(day=day.isoformat()))
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise LwaProblem(f"The OVRO-LWA portal answered HTTP {response.status_code}")
    try:
        answer = response.json()
    except ValueError as e:
        raise LwaProblem("The OVRO-LWA portal did not answer in its usual form") from e
    if not isinstance(answer, dict) or not answer.get("url"):
        return None
    if not lwa_movie_of(answer, day):
        logger.warning("OVRO-LWA named a movie of another date for %s: %s", day, answer)
        return None
    return answer["url"]


def parse_listing(html):
    """The frame images of a folder's page, ``[(time, name)]`` by time."""
    found = {}
    for name, *parts in FRAME_NAME.findall(html):
        try:
            found[name] = datetime(*map(int, parts))
        except ValueError:
            continue
    return sorted(((when, name) for name, when in found.items()))


def listing(day):
    """``day``'s frame images, ``[(time, name)]``; empty if the folder is not there."""
    response = _get(LISTING.format(day=day))
    if response.status_code == 404:
        return []
    if response.status_code != 200:
        raise LwaProblem(f"OVRO-LWA's frame list answered HTTP {response.status_code}")
    return parse_listing(response.text)


def ffmpeg_program():
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError) as e:
        raise LwaProblem(
            "The video decoder is missing (the imageio-ffmpeg package), so the movie cannot be played"
        ) from e


DIMENSIONS = re.compile(r"^#dimensions 0: (\d+)x(\d+)", re.MULTILINE)


def probe(program, path):
    """``(frames, width, height)`` of a movie, from its packets: no decoding."""
    try:
        out = subprocess.run(
            [program, "-v", "error", "-nostdin", "-i", str(path), "-map", "0:v:0", "-c", "copy", "-f", "framecrc", "-"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise LwaProblem("The movie could not be read") from e
    size = DIMENSIONS.search(out.stdout)
    frames = sum(1 for line in out.stdout.splitlines() if line.startswith("0,"))
    if out.returncode != 0 or size is None or frames == 0:
        raise LwaProblem("The movie could not be read: it may be damaged")
    return frames, int(size.group(1)), int(size.group(2))


def _runs(numbers):
    """Sorted ``numbers`` as runs ``[(first, last)]``."""
    runs = []
    for n in numbers:
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return runs


def decode(program, path, wanted, width, height, started=None, stopped=lambda: False):
    """Yield ``(n, frame)`` for the movie's frames ``wanted``, in order, in one
    pass: each frame an RGB array, gone once the next is read. ``started``
    hears of the ffmpeg process, to kill it."""
    wanted = sorted(set(wanted))
    if not wanted:
        return
    runs = _runs(wanted)
    picked = runs if len(runs) <= MOST_RUNS else [[wanted[0], wanted[-1]]]
    choice = "+".join(f"between(n,{first},{last})" for first, last in picked)
    out_frames = sum(last - first + 1 for first, last in picked)
    command = [
        program, "-v", "error", "-nostdin", "-i", str(path), "-map", "0:v:0",
        "-vf", f"select='{choice}'", "-fps_mode", "passthrough",
        "-frames:v", str(out_frames), "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    except OSError as e:
        raise LwaProblem("The video decoder could not be started") from e
    if started is not None:
        started(process)
    size = width * height * 3
    keep = set(wanted)
    numbers = (n for first, last in picked for n in range(first, last + 1))
    try:
        for n in numbers:
            if stopped():
                raise _Stopped()
            data = process.stdout.read(size)
            if stopped():
                raise _Stopped()
            if len(data) < size:
                raise LwaProblem("The movie could not be decoded to its end: it may be damaged")
            if n in keep:
                yield n, np.frombuffer(data, np.uint8).reshape(height, width, 3)
    finally:
        if process.poll() is None:
            process.kill()
        process.stdout.close()
        process.wait()


def image_array(data):
    """An image file's bytes as an RGB array, None if it is no image."""
    from .helioviewer_export import _rgb_array

    array = QByteArray(data)
    source = QBuffer(array)
    source.open(QIODevice.ReadOnly)
    reader = QImageReader(source)
    size = reader.size()
    if size.isValid() and size.width() * size.height() > MOST_PIXELS:
        return None
    image = reader.read()
    return None if image.isNull() else _rgb_array(image)


def difference(frame, image):
    """Mean difference of a frame and an image over what both cover: the
    quick-look images are a row shorter than the movie."""
    height = min(frame.shape[0], image.shape[0])
    width = min(frame.shape[1], image.shape[1])
    return float(np.abs(frame[:height, :width].astype(np.int16) - image[:height, :width]).mean())


def checked_frames(frames):
    """The frames whose times are proved: the first, the middle and the last."""
    return sorted({0, frames // 2, frames - 1})


def frame_matches(errors, n):
    """Whether image ``n`` is clearly frame ``n``'s, of ``errors`` {frame: its
    difference from that image}: a relative test, as the coding blurs each
    frame by its own amount."""
    ranked = sorted(errors.items(), key=lambda item: item[1])
    if ranked[0][0] != n:
        return False
    if len(ranked) == 1:
        return ranked[0][1] <= CHECK_ALONE
    return ranked[0][1] <= CHECK_RATIO * ranked[1][1]


def prove_times(program, path, frames, width, height, image_of, started=None, stopped=lambda: False):
    """Whether the movie's frames are the listed images in order: each
    checked frame's image ``image_of(n)`` (an array or None) is clearly that
    frame's, of those near it."""
    checks = checked_frames(frames)
    images = {}
    for n in checks:
        image = image_of(n)
        if image is None:
            return False
        images[n] = image
    wanted = set()
    for n in checks:
        wanted.update(range(max(0, n - CHECK_WINDOW), min(frames, n + CHECK_WINDOW + 1)))
    errors = {n: {} for n in checks}
    for m, frame in decode(program, path, wanted, width, height, started, stopped):
        for n in checks:
            if abs(m - n) <= CHECK_WINDOW:
                errors[n][m] = difference(frame, images[n])
    return all(errors[n] and frame_matches(errors[n], n) for n in checks)


def layout_is_usual(frame):
    """Whether a frame is the usual 3 by 4 montage, so a band can be cut out."""
    if frame.shape[1] != FRAME_SIZE[0] or frame.shape[0] < FRAME_SIZE[1] - 1:
        return False
    top, bottom = ROWS[0][0], ROWS[-1][1]
    left, right = COLUMNS[0][0], COLUMNS[-1][1]
    columns = all(frame[top:bottom, a:b + 1].min() >= WHITE for a, b in SEPARATOR_COLUMNS)
    rows = all(frame[a:b + 1, left:right].min() >= WHITE for a, b in SEPARATOR_ROWS)
    return columns and rows


def shaped(frame, view, side):
    """The frame as the browser shows it: the montage or one band's panel,
    no larger than ``side`` either way. The panel is cut after the move to
    RGB: in the movie's own colours, an odd offset is a pixel out."""
    if view != MONTAGE:
        x0, y0, x1, y1 = BOXES[view]
        frame = frame[y0:y1 + 1, x0:x1 + 1]
    frame = np.ascontiguousarray(frame)
    height, width = frame.shape[:2]
    image = QImage(frame.data, width, height, 3 * width, QImage.Format_RGB888).copy()
    if max(width, height) > side:
        image = image.scaled(side, side, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return image


def native_size(view, width=FRAME_SIZE[0], height=FRAME_SIZE[1]):
    if view == MONTAGE:
        return width, height
    x0, y0, x1, y1 = BOXES[view]
    return x1 - x0 + 1, y1 - y0 + 1


def side_for(view, count, asked, width=FRAME_SIZE[0], height=FRAME_SIZE[1]):
    """The longest side of each of ``count`` frames: what was asked, and no
    more than the frame has or than keeps them all within ``MEMORY_BYTES``."""
    w, h = native_size(view, width, height)
    longest = max(w, h)
    share = MEMORY_BYTES / max(count, 1) / BYTES_PER_PIXEL
    fits = int(math.sqrt(share * longest / min(w, h)))
    return max(16, min(asked, longest, fits))


def thinned(times, interval):
    """Positions in ``times`` (sorted) of frames at least ``interval`` s apart."""
    if interval <= LEAST_CADENCE:
        return list(range(len(times)))
    kept = []
    for i, when in enumerate(times):
        if not kept or (when - times[kept[-1]]).total_seconds() >= interval - TIME_SLACK:
            kept.append(i)
    return kept


@dataclass(eq=False)
class Movie:
    """One day's movie, as far as it is known."""

    day: date
    url: str = ""
    path: Optional[str] = None
    etag: str = ""
    frames: int = 0
    width: int = 0
    height: int = 0
    listed: Optional[list] = None  # [(time, name)] of the folder; None if it could not be read
    times: Optional[list] = None  # proved: a time for each frame
    planned: Optional[list] = None  # what the times look like before they are proved
    why: str = ""  # why the times are not proved
    offline: bool = False
    stale: bool = False  # a newer one could not be downloaded: this is the saved copy
    hoped: Optional[int] = None  # frames in the range it looked to have, before its proof failed


def _within(when, start, end):
    return (start is None or when >= start) and (end is None or when <= end)


def choose(movies, start, end, interval):
    """The frames to show, ``[(time, movie, n)]`` by time, then thinned to
    ``interval``. Neighbouring days' movies overlap: each time comes once,
    from the movie with more frames in the range (one download fewer), or
    on a tie from the movie of its own date."""
    def times_of(movie):
        return (movie.times if movie.times is not None else movie.planned) or []

    held_in_range = {movie: sum(_within(t, start, end) for t in times_of(movie)) for movie in movies}

    def rank(movie, when):
        return held_in_range[movie], movie.day == when.date()

    best = {}
    for movie in movies:
        for n, when in enumerate(times_of(movie)):
            if not _within(when, start, end):
                continue
            held = best.get(when)
            if held is None or rank(movie, when) > rank(held[0], when):
                best[when] = (movie, n)
    picked = [(when, movie, n) for when, (movie, n) in sorted(best.items(), key=lambda item: item[0])]
    return [picked[i] for i in thinned([p[0] for p in picked], interval)]


@dataclass
class Plan:
    """What a load will show, before its frames come."""

    times: List[Optional[datetime]]  # None: not proved
    days: List[date]  # the movie each frame is from
    numbers: List[int]  # its frame in that movie
    notes: List[str] = field(default_factory=list)
    clip: bool = False
    side: int = 0


class LwaMovieLoader(QThread):
    """Loads OVRO-LWA frames between two times (or all of one day's movie),
    each with the time it was taken: the movies come down once, are checked
    against the frame list, and are decoded in one pass each, keeping only
    the frames shown, at the size shown."""

    stage = pyqtSignal(str, int, int)  # what it is doing, how far, of how much (0: not known)
    planned = pyqtSignal(object)  # a Plan
    range_found = pyqtSignal(object, object)  # the movie's first and last time (one day's movie)
    frame_ready = pyqtSignal(int, QImage)
    failed = pyqtSignal(str)
    done = pyqtSignal()

    def __init__(self, view, start=None, end=None, interval=LEAST_CADENCE, side=600, movie_day=None, cache=None):
        super().__init__()
        self.view = view
        self.start_time = start
        self.end_time = end
        self.interval = interval
        self.side = side
        self.movie_day = movie_day
        self.cache = cache
        self.running = True
        self._lock = threading.Lock()
        self._process = None

    def stop(self):
        """Stop at the next chance, and kill ffmpeg now."""
        self.running = False
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            process.kill()

    def _started(self, process):
        with self._lock:
            self._process = process
        if not self.running:
            process.kill()

    def _stopped(self):
        return not self.running

    def _check(self):
        if not self.running:
            raise _Stopped()

    def run(self):
        try:
            self._load()
        except _Stopped:
            pass
        except LwaProblem as e:
            if self.running:
                self.failed.emit(str(e))
        except Exception as e:
            logger.exception("OVRO-LWA load failed")
            if self.running:
                self.failed.emit(f"The OVRO-LWA movie could not be loaded: {e}")

    # ---------------------------------------------------------- the steps

    def _cache(self):
        if self.cache is None:
            from .noaa_events.cache import _noaa_cache

            self.cache = _noaa_cache
        return self.cache

    def _days(self):
        if self.movie_day is not None:
            return [self.movie_day]
        first = self.start_time.date() - timedelta(days=1)  # its movie runs into the next day
        return [first + timedelta(days=i) for i in range((self.end_time.date() - first).days + 1)]

    def _in_range_days(self, movies):
        if self.movie_day is not None:
            return movies
        first, last = self.start_time.date(), self.end_time.date()
        return [m for m in movies if first <= m.day <= last]

    def _load(self):
        program = ffmpeg_program()
        start = None if self.movie_day else self.start_time
        end = None if self.movie_day else self.end_time
        days = self._days()
        movies, unreachable, cut_at = [], [], None
        for i, day in enumerate(days):
            self._check()
            if self.movie_day is None and movies:
                # A day's movie starts on its own date: once the frames before
                # it fill a load, the later days are not asked for
                so_far = choose(movies, start, end, self.interval)
                if len(so_far) >= MOST_FRAMES and so_far[MOST_FRAMES - 1][0] < datetime.combine(day, datetime.min.time()):
                    cut_at = so_far[MOST_FRAMES - 1][0]
                    break
            self.stage.emit(f"Asking OVRO-LWA for the movie of {day}…", i, len(days))
            movie = self._ask(day, unreachable)
            if movie is not None:
                movies.append(movie)
        if not movies:
            if unreachable:
                raise LwaProblem(
                    f"Could not reach OVRO-LWA, and no copy of its movie for {self._dates_text()} is saved here. "
                    "Check the connection and try again."
                )
            raise LwaProblem(f"OVRO-LWA has no movie for {self._dates_text()}.")

        ready, failed, left_out = set(), [], []
        in_range = self._in_range_days(movies)
        while True:
            chosen = choose(movies, start, end, self.interval)[:MOST_FRAMES]
            last = chosen[-1][0] if len(chosen) == MOST_FRAMES else cut_at
            needed = {movie for _, movie, _ in chosen}
            # A movie with no times at all may hold the range: it is fetched to find out
            needed.update(
                m for m in in_range
                if m.times is None and m.planned is None and (last is None or m.day <= last.date())
            )
            needed -= ready
            if not needed:
                break
            for movie in sorted(needed, key=lambda m: m.day):
                if movie.planned is not None:
                    movie.hoped = sum(_within(t, start, end) for t in movie.planned)
                self._fetch(movie, program)
                ready.add(movie)
                if movie.times is not None:
                    continue
                if movie in in_range:
                    failed.append(movie)
                else:
                    # The day before only lends its last hours: they are left out
                    movies.remove(movie)
                    left_out.append(f"{movie.why} Its frames after midnight are left out.")

        chosen = choose([m for m in movies if m.times is not None], start, end, self.interval)
        clip = None
        if failed:
            failed.sort(key=lambda m: m.day)
            clip = max(failed, key=lambda m: m.frames if m.hoped is None else m.hoped)
            held = clip.frames if clip.hoped is None else clip.hoped
            if chosen and held <= len(chosen):
                # The proved movies hold more of the range: the others are left out
                left_out += [f"{m.why} Its frames are left out." for m in failed]
                clip = None

        notes = list(left_out)
        if unreachable:
            notes.append(f"Could not ask OVRO-LWA about {', '.join(map(str, unreachable))}.")
        if any(m.offline for m in ready):
            notes.append("OVRO-LWA could not be reached: these are the copies saved earlier.")
        notes += [f"The newer {m.day} movie could not be downloaded: this is the copy saved earlier." for m in ready if m.stale]
        if clip is not None:
            plan_frames = self._clip(clip, notes, others=bool(chosen) or len(failed) > 1)
            if len(plan_frames) > MOST_FRAMES:
                notes.append(f"The first {MOST_FRAMES} of its {len(plan_frames)} frames.")
                plan_frames = plan_frames[:MOST_FRAMES]
        else:
            if not chosen:
                raise LwaProblem(self._nothing_between(movies))
            plan_frames = chosen
            if self.interval > LEAST_CADENCE:
                notes.append(f"Frames at least {self.interval} s apart; set the interval to {LEAST_CADENCE} s for all of them.")
            if len(plan_frames) > MOST_FRAMES or cut_at is not None:
                plan_frames = plan_frames[:MOST_FRAMES]
                notes.append(
                    f"The first {MOST_FRAMES} frames, to {plan_frames[-1][0]:%Y-%m-%d %H:%M} UTC: "
                    "a larger interval or a shorter range shows the rest."
                )
        if self.movie_day is not None and clip is None:
            self.range_found.emit(plan_frames[0][0], plan_frames[-1][0])

        some = next(m for _, m, _ in plan_frames)
        side = side_for(self.view, len(plan_frames), self.side, some.width, some.height)
        native = max(native_size(self.view, some.width, some.height))
        if side < min(self.side, native):
            notes.append(
                f"Shown at {side} px to keep these {len(plan_frames)} frames within about "
                f"{MEMORY_BYTES / 1024 ** 3:.1f} GB; a larger interval or a shorter range gives larger frames."
            )
        plan = Plan(
            times=[when for when, _, _ in plan_frames],
            days=[movie.day for _, movie, _ in plan_frames],
            numbers=[n for _, _, n in plan_frames],
            notes=notes,
            clip=clip is not None,
            side=side,
        )
        self._check()
        self.planned.emit(plan)
        self._decode_all(program, plan_frames, side)
        self._check()
        self.done.emit()

    def _dates_text(self):
        if self.movie_day is not None:
            return str(self.movie_day)
        first, last = self.start_time.date(), self.end_time.date()
        return str(first) if first == last else f"{first} to {last}"

    def _nothing_between(self, movies):
        if self.movie_day is not None:
            return f"OVRO-LWA's movie for {self.movie_day} has no frames."
        times = [t for m in movies for t in (m.times or m.planned or [])]
        text = (
            f"OVRO-LWA has no frames between {self.start_time:%Y-%m-%d %H:%M:%S} and "
            f"{self.end_time:%Y-%m-%d %H:%M:%S} UTC."
        )
        if times:
            text += f" Its movies of these dates run from {min(times):%Y-%m-%d %H:%M} to {max(times):%Y-%m-%d %H:%M} UTC."
        return text

    def _clip(self, movie, notes, others):
        """Every frame of one movie, in order, with no times: neighbouring
        days' movies overlap, and with no times their frames cannot be
        matched, so one movie plays alone."""
        title = "each frame prints its time in its title" if self.view == MONTAGE else "all bands show each frame's time in its title"
        text = f"Times not confirmed: {movie.why} The {movie.day} movie plays whole, as a clip; {title}."
        if others:
            text += " Other dates' frames are not shown with it."
        notes.insert(0, text)
        return [(None, movie, n) for n in range(movie.frames)]

    def _ask(self, day, unreachable):
        """``day``'s movie with the times it looks to have, None if there is none."""
        cache = self._cache()
        record = cache.load_lwa_movie_record(day)
        try:
            url = movie_url(day)
        except LwaProblem:
            if record is not None and cache.cached_lwa_movie(day, record["url"]):
                movie = Movie(day, url=record["url"], offline=True)
                if record.get("proof") == "confirmed":
                    movie.planned = [datetime.fromisoformat(t) for t in record["times"]]
                return movie
            unreachable.append(day)
            return None
        if url is None:
            return None
        movie = Movie(day, url=url)
        if record is not None and record["url"] == url and record.get("proof") == "confirmed":
            movie.planned = [datetime.fromisoformat(t) for t in record["times"]]
        else:
            self._list(movie)
        return movie

    def _list(self, movie):
        try:
            movie.listed = listing(movie.day)
        except LwaProblem as e:
            logger.info("No frame list for %s: %s", movie.day, e)
            movie.listed = None
        # An empty or missing folder says nothing of the movie's times
        movie.planned = [when for when, _ in movie.listed] if movie.listed else None

    def _fetch(self, movie, program):
        """The movie on disk, and its times proved, or why they are not."""
        self._download(movie)
        self._check()
        movie.frames, movie.width, movie.height = probe(program, movie.path)
        if self.view != MONTAGE and not self._usual(program, movie):
            raise LwaProblem(
                f"The {movie.day} movie is not laid out as the usual 3 by 4 panels, so one band "
                "cannot be cut from it. Choose all bands."
            )
        cache = self._cache()
        record = cache.load_lwa_movie_record(movie.day)
        if (
            record is not None and record["url"] == movie.url and record["etag"] == movie.etag
            and record.get("proof") == "confirmed" and record.get("frames") == movie.frames
        ):
            movie.times = [datetime.fromisoformat(t) for t in record["times"]]
            return
        if movie.offline:
            movie.why = f"OVRO-LWA could not be reached to check the times of the {movie.day} movie."
            return
        if movie.listed is None:
            self._list(movie)
        if movie.listed is None:
            movie.why = f"OVRO-LWA's list of frames for {movie.day} could not be read."
        elif not movie.listed:
            movie.why = f"OVRO-LWA has no list of frames for {movie.day}."
        elif len(movie.listed) < movie.frames:
            movie.why = f"OVRO-LWA lists fewer frames for {movie.day} than its movie has."
        else:
            self.stage.emit(f"Checking the times of the {movie.day} movie…", 0, 0)
            base = LISTING.format(day=movie.day)
            ok = prove_times(
                program, movie.path, movie.frames, movie.width, movie.height,
                lambda n: self._listed_image(base + movie.listed[n][1]),
                self._started, self._stopped,
            )
            self._check()
            if ok:
                # (a day still growing lists more than its movie has: the first ones are its frames)
                movie.times = [when for when, _ in movie.listed[: movie.frames]]
            else:
                movie.why = f"The {movie.day} movie's frames do not match OVRO-LWA's list of frames."
        if record is not None and record["url"] == movie.url and record["etag"] == movie.etag:
            from .noaa_events.cache import keep

            record = dict(record, frames=movie.frames, proof="confirmed" if movie.times else "failed")
            if movie.times:
                record["times"] = [when.isoformat() for when in movie.times]
            keep(cache.save_lwa_movie_record, movie.day, record)
        if movie.times is None:
            movie.planned = None

    def _listed_image(self, url):
        self._check()
        try:
            response = _get(url)
        except LwaProblem:
            return None
        if response.status_code != 200:
            return None
        return image_array(response.content)

    def _usual(self, program, movie):
        for _, frame in decode(program, movie.path, [0], movie.width, movie.height, self._started, self._stopped):
            return layout_is_usual(frame)
        return False

    def _download(self, movie):
        """Fetch the movie into the cache, or check that the copy there is
        still the server's: a cut download goes on where it stopped. One
        window at a time, as they share the partial file."""
        with _DAY_LOCKS_LOCK:
            lock = _DAY_LOCKS.setdefault(movie.day, threading.Lock())
        while not lock.acquire(timeout=0.2):
            self._check()
        try:
            for attempt in range(DOWNLOAD_TRIES):
                try:
                    return self._download_once(movie)
                except _Cut:
                    logger.info("The download of the %s movie was cut (try %d)", movie.day, attempt + 1)
        finally:
            lock.release()
        cache = self._cache()
        record = cache.load_lwa_movie_record(movie.day)
        have = cache.cached_lwa_movie(movie.day, movie.url)
        if have and record is not None:
            # A newer movie would not come down whole: the saved one plays
            movie.path, movie.etag, movie.stale = str(have), record["etag"], True
            return
        raise LwaProblem(
            f"The download of the {movie.day} movie was cut short. Try again: it goes on where it stopped."
        )

    def _download_once(self, movie):
        from .noaa_events.cache import keep
        import requests

        cache = self._cache()
        day = movie.day
        record = cache.load_lwa_movie_record(day)
        have = cache.cached_lwa_movie(day, movie.url)
        path, partial = cache.lwa_movie_path(day), cache.lwa_movie_partial(day)

        def use_saved():
            movie.path, movie.etag = str(have), record["etag"]

        if movie.offline:
            use_saved()
            return
        headers, resume_from = {}, 0
        if have:
            if record["etag"]:
                headers["If-None-Match"] = record["etag"]
            if record["last_modified"]:
                headers["If-Modified-Since"] = record["last_modified"]
        else:
            part = cache.load_lwa_partial_record(day)
            try:
                size = partial.stat().st_size
            except OSError:
                size = 0
            if part and part["url"] == movie.url and (part["etag"] or part["last_modified"]) and size:
                headers["Range"] = f"bytes={size}-"
                headers["If-Range"] = part["etag"] or part["last_modified"]
                resume_from = size
        self.stage.emit(f"Asking for the {day} movie…", 0, 0)
        try:
            response = _get(movie.url, headers=headers, stream=True)
        except LwaProblem:
            if have:
                movie.offline = True
                use_saved()
                return
            raise LwaProblem(f"Could not reach OVRO-LWA to download the {day} movie. Check the connection and try again.")
        with response:
            if response.status_code == 304 and have:
                use_saved()
                return
            if response.status_code == 206 and resume_from:
                mode = "ab"
            elif response.status_code == 200:
                mode, resume_from = "wb", 0
            elif response.status_code == 416 and resume_from:
                # The partial file is already whole, or longer than the movie
                # now is: it starts again
                try:
                    partial.unlink()
                except OSError:
                    pass
                cache.drop_lwa_partial_record(day)
                raise _Cut()
            else:
                raise LwaProblem(f"OVRO-LWA answered HTTP {response.status_code} for the {day} movie")
            etag = response.headers.get("ETag", "")
            modified = response.headers.get("Last-Modified", "")
            length = int(response.headers.get("Content-Length") or 0)
            total = resume_from + length if length else 0
            if mode == "wb":
                keep(cache.save_lwa_partial_record, day, {"url": movie.url, "etag": etag, "last_modified": modified})
            done = resume_from
            try:
                f = open(partial, mode)
            except OSError as e:
                raise LwaProblem(f"Could not save the {day} movie: {e}") from e
            with f:
                try:
                    for chunk in response.iter_content(CHUNK_BYTES):
                        self._check()
                        f.write(chunk)
                        done += len(chunk)
                        self.stage.emit(
                            f"Downloading the {day} movie: {done / 1e6:.1f} of {total / 1e6:.1f} MB"
                            if total else f"Downloading the {day} movie: {done / 1e6:.1f} MB",
                            done, total,
                        )
                except requests.RequestException as e:
                    raise _Cut() from e
                except OSError as e:
                    raise LwaProblem(f"Could not save the {day} movie: {e}") from e
        if total and done < total:
            raise _Cut()
        try:
            partial.replace(path)
        except OSError as e:
            raise LwaProblem(f"Could not save the {day} movie: {e}") from e
        record = {
            "url": movie.url, "etag": etag, "last_modified": modified, "bytes": done,
            "saved": datetime.now().isoformat(timespec="seconds"),
        }
        keep(cache.save_lwa_movie_record, day, record)
        cache.drop_lwa_partial_record(day)
        movie.path, movie.etag = str(path), etag

    def _decode_all(self, program, plan_frames, side):
        """One pass of each movie, its frames sent as they come."""
        by_movie = {}
        for index, (_, movie, n) in enumerate(plan_frames):
            by_movie.setdefault(movie, {})[n] = index
        total, done = len(plan_frames), 0
        for movie in sorted(by_movie, key=lambda m: m.day):
            places = by_movie[movie]
            for n, frame in decode(program, movie.path, places, movie.width, movie.height, self._started, self._stopped):
                self._check()
                self.frame_ready.emit(places[n], shaped(frame, self.view, side))
                done += 1
                self.stage.emit("Decoding frames", done, total)

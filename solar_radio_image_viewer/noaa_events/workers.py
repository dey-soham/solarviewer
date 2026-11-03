"""
Background threads of the solar events viewer: fetching the day's data,
resolving and loading images, the GOES X-ray plot and the radio dynamic
spectra.
"""

import errno
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from datetime import datetime, date, timedelta
from typing import Optional
from PyQt5.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QImage, QImageReader
from . import noaa_events as ne

from .cache import _noaa_cache, keep
from ..version import __version__

logger = logging.getLogger(__name__)

FETCH_STAGES = 6
THUMBNAIL_SIZE = 320
CACHED_SIDE = 640  # the widest thumbnail kept on disk, from a far larger original
MOST_PIXELS = 100_000_000  # no image here comes near it
MOVIE_NO_STILL = "Click to open the movie"
MOVIE_LENGTH = re.compile(r"Duration: (\d+):(\d+):(\d+\.\d+)")
STILL_AT = 0.4  # of the way through: about 19 UT of a movie from 14 UT to 02 UT, on its own date
CHUNK_BYTES = 64 * 1024
MINUTES_PER_DAY = 24 * 60
GOES_FILL = 1e-9  # the files' floor: what they hold where they have no value


NICT_TRANSITION_DATE = date(2016, 7, 11)  # Yamagawa took over from HiRAS
EOVSA_FIRST = date(2017, 1, 1)  # its daily plots start early in 2017
CONTEXT_FIRST = date(2023, 5, 1)  # the composites start between May and June 2023
LWA_FIRST = date(2023, 5, 2)  # its first daily spectrum: 2023-05-01 is not there, and 2023-07-19 is the next
LEARMONTH_FIRST = date(2000, 1, 1)  # as the app's RSTN downloader
NICT_THREADS = 3  # hours of a station at once, which take a second or two each
SPECTRA_THREADS = 5  # sources fetched at once; each archive is still asked in turn
RECORD_THREADS = 4  # a day's records fetched at once, each from its own archive
RECORD_SETTLE_DAYS = 30  # an archive with nothing for a date older than this is not asked again
LATEST_DAYS = 30  # older than this, a date's plots are not waiting for the archive to get there

EOVSA_DATA = "https://ovsa.njit.edu/eovsadata/api/data.php"
EOVSA_LATEST = "https://ovsa.njit.edu/eovsadata/api/availability.php"  # the newest date of its images
LWA_DAILY = "https://ovsa.njit.edu/lwa-data/qlook_spec_v2/daily/{day:%Y%m%d}.png"
LWA_DATES = "https://ovsa.njit.edu/lwa/portal/avail-dates"
LWA_MOVIE_LOOKUP = "https://ovsa.njit.edu/lwa/portal/preview/movie/{day:%Y-%m-%d}"
LWA_FITS = "https://ovro-lwa-solar.s3.us-west-2.amazonaws.com/spec_fits/{day:%Y}/{day:%Y%m%d}.fits"
SWPC_XRAYS = "https://services.swpc.noaa.gov/json/goes/primary/xrays-7-day.json"
SWPC_XRAY_DAYS = 6  # whole days that file reaches back
GOES_WHOLE_DAY = 1430  # minutes: a curve with these has nothing more to gain
LEARMONTH_GIF = "https://downloads.sws.bom.gov.au/wdc/wdc_spec/data/learmonth/images/{day:%y}/{day:%Y%m%d}spectrograph.gif"
USER_AGENT = f"SolarViewer/{__version__} (python-urllib)"


def expected_sources(day):
    """The spectra that can exist for ``day``, high frequency to low, ground
    before space. The composite has no lane, and goes last."""
    sources = ["norp"]
    if day >= EOVSA_FIRST:
        sources.append("eovsa")
    sources += ["yamagawa" if day >= NICT_TRANSITION_DATE else "hiras", "orfees"]
    if day >= LEARMONTH_FIRST:
        sources.append("learmonth")
    if day >= LWA_FIRST:
        sources.append("lwa")
    sources.append("nda")
    if day.year >= 2007:  # STEREO started in October 2006
        sources.append("stereo_a")
    if 2007 <= day.year <= 2014:  # STEREO-B fell silent in 2014
        sources.append("stereo_b")
    sources.append("wind")
    if day >= CONTEXT_FIRST:
        sources.append("context")
    return sources


# Every source, in the order the worker goes through them
SPECTRA_NAMES = {
    'wind': "WIND/WAVES", 'stereo_a': "STEREO-A", 'stereo_b': "STEREO-B", 'norp': "Nobeyama (NORP)",
    'orfees': "ORFEES", 'nda': "NDA", 'yamagawa': "Yamagawa", 'hiras': "HiRAS",
    'eovsa': "EOVSA", 'lwa': "OVRO-LWA", 'learmonth': "Learmonth", 'context': "EOVSA composite",
}
# Their files are looked after by their own fetchers, which share the composite
OVSA_SOURCES = ("eovsa", "lwa", "context")
IMAGE_KINDS = (b"\x89PNG", b"\xff\xd8", b"GIF8")
# (ENOENT: the date's folder could not be made, for one of the others)
DISK_FAULTS = (errno.ENOSPC, errno.EDQUOT, errno.EROFS, errno.EACCES, errno.ENOENT)


def eovsa_latest(data):
    """The date of ``availability.php``'s answer, which can say a date its
    spectra have yet to reach: the band maps come first."""
    answer = json.loads(data)
    return date.fromisoformat(answer["date"]) if answer.get("ok") else None


def lwa_latest(data):
    """The last date of the portal's list of them."""
    return max(date.fromisoformat(day) for day in json.loads(data))


def _failure_kind(error):
    """Whether ``error`` means the archive has no such file (``no_file``), it
    could not be asked (``unreachable``: timeouts, refused, 5xx), or the file
    came and could not be kept (``no_room``)."""
    if isinstance(error, OSError) and error.errno in DISK_FAULTS:
        return "no_room"
    code = getattr(error, "code", None) or getattr(getattr(error, "response", None), "status_code", None)
    return "no_file" if code in (403, 404, 410) else "unreachable"


def cut_short(data):
    """Whether an image's bytes stop before the end its kind has to have. Bytes
    of no kind known here are the decoder's to judge."""
    if data.startswith(b"\x89PNG"):
        return b"IEND" not in data[-16:]
    if data.startswith(b"\xff\xd8"):
        return b"\xff\xd9" not in data[-64:]
    if data.startswith(b"GIF8"):
        return b";" not in data[-8:]
    return False


def whole_file(data):
    """Whether these are all the bytes of an image: a download cut short, a
    web page or nothing at all would stay in the cache for good."""
    return data.startswith(IMAGE_KINDS) and not cut_short(data)


def whole_on_disk(path):
    """``whole_file`` of a cached file, which for Learmonth is its records."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
            f.seek(-min(64, os.path.getsize(path)), os.SEEK_END)
            tail = f.read()
    except OSError:
        return False
    if str(path).endswith(".srs"):
        from ..radio_data_downloader.radio_data_downloader import RECORD_SIZE
        from .spectra_reading import learmonth_head

        return learmonth_head(head) and os.path.getsize(path) % RECORD_SIZE == 0
    return whole_file(head + tail)


def _still_growing(day):
    """Whether the lists for ``day`` can still gain entries: SWPC's for today
    and a CME window of three days to either side. The observatories' daily
    plots settle in about the same time."""
    return (datetime.utcnow().date() - day).days <= 3


def saved_early(path, day):
    """Whether the file ``path`` of ``day`` was saved while that day could
    still grow, so that it can be partial: one saved after that is final, and
    the archive is not asked again."""
    try:
        saved = datetime.utcfromtimestamp(os.path.getmtime(path)).date()
    except OSError:
        return False
    return _still_growing(day) or (saved - day).days <= 3


def _asked_now(path):
    """The archive had nothing fuller than the file ``path``: it stands now."""
    try:
        os.utime(path)
    except OSError as e:
        logger.info("Could not mark %s: %s", path, e)


def on_threads(work, items, width):
    """``work(item)`` of each of ``items``, ``width`` at a time, the results in
    their order. As a pool would, but on daemon threads: Python joins a pool's
    threads when it exits, and a stalled host would hold the quit up for its
    timeout. The first failure comes out here, when the others have ended."""
    items = list(items)
    results = [None] * len(items)
    failures = []
    turns = iter(range(len(items)))  # (next() on it is safe across the threads)

    def worker():
        for index in turns:
            try:
                results[index] = work(items[index])
            except BaseException as e:  # Stopped is one
                failures.append(e)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(min(width, len(items)))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if failures:
        raise failures[0]
    return results


class Stopped(BaseException):
    """The work was told to stop. Not an Exception, so that it passes the
    handlers that note a failed source and go on to the next."""


class StoppableThread(QThread):
    """A thread that stops at its next ``check_stop()`` once told to: its
    network calls cannot be interrupted, so ``ui.download_parts.abandon``
    leaves it to get there alone."""

    cancelled = False

    def cancel(self):
        self.cancelled = True

    def check_stop(self):
        if self.cancelled:
            raise Stopped()


class ImageUrlResolver(StoppableThread):
    found = pyqtSignal(str)

    def __init__(self, page_url):
        super().__init__()
        self.url = page_url

    def run(self):
        from ..solar_context import context_images as ci

        try:
            url = ci.resolve_full_image_url(self.url)
        except Exception as e:
            print(f"Failed to resolve high-res: {e}")
            url = None
        self.found.emit(url or "")


class ImageLoader(StoppableThread):
    """
    Thread to download image data.
    If page_url is provided, it tries to resolve the High-Res image first.
    Otherwise (or if resolve fails), it falls back to the direct url (thumbnail).
    """

    loaded = pyqtSignal(bytes)
    error = pyqtSignal(str)
    received = pyqtSignal(int, int)  # bytes so far, of this many (0: not known)

    def __init__(self, url, page_url=None, event_date=None):
        super().__init__()
        self.url = url
        self.page_url = page_url
        self.event_date = event_date  # the date it was asked for, if that matters
        self.file_name = ""  # as the server named it, if it did

    def run(self):
        try:
            self.loaded.emit(self.fetch())
        except Stopped:
            pass
        except Exception as e:
            self.error.emit(str(e))

    def fetch(self):
        """The image's bytes."""
        target_url = self.url
        from ..solar_context import context_images as ci

        # Try to resolve high-res if page_url available
        # BUT: Skip for direct image URLs
        if self.page_url and not ci.is_direct_image(self.page_url):
            try:
                resolved = ci.resolve_full_image_url(self.page_url)
                if resolved:
                    target_url = resolved
            except Exception as e:
                print(f"Failed to resolve high-res: {e}")

        data = bytearray()
        self.stream(target_url, data.extend)
        if bytes(data[:64]).lstrip()[:1] == b"<":  # a page, where the image was not found
            raise RuntimeError(ci.refusal(target_url))
        self.refuse_another_date()
        if cut_short(data):  # no Content-Length to tell by
            raise RuntimeError("The connection was cut before the end of the file")
        return bytes(data)

    def refuse_another_date(self):
        """A SunDish file names its own date: the index has to agree with it,
        and one whose name gives none has nothing to agree with."""
        from ..solar_context import radio_images

        if self.event_date is None:
            return
        named = radio_images.drive_name_date(self.file_name)
        if named is None and radio_images.is_sundish_file(self.url):
            raise RuntimeError("Not shown: the file's name does not give its date")
        if named is not None and named != self.event_date:
            raise RuntimeError(f"Not shown: the file is dated {named}")

    def stream(self, url, write):
        """Download ``url`` a piece at a time into ``write``, saying how far
        along it is and stopping when told to."""
        import requests

        with requests.get(
            url, timeout=60, headers={"User-Agent": f"SolarViewer/{__version__} (python-requests)"}, stream=True
        ) as response:
            if response.status_code != 200:
                from ..solar_context import context_images as ci

                raise RuntimeError(ci.refusal(url, response.status_code))
            self.file_name = response.headers.get("Content-Disposition", "")
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            for chunk in response.iter_content(CHUNK_BYTES):
                self.check_stop()
                write(chunk)
                done += len(chunk)
                self.received.emit(done, max(total, done) if total else 0)
            if done < total and "Content-Encoding" not in response.headers:
                raise RuntimeError("The connection was cut before the end of the file")


def _decoded(data):
    """A file's QImage, null if it is no image or is absurdly large."""
    array = QByteArray(data)
    source = QBuffer(array)
    source.open(QIODevice.ReadOnly)
    reader = QImageReader(source)
    size = reader.size()
    if size.isValid() and size.width() * size.height() > MOST_PIXELS:
        return QImage()
    return QImage.fromData(array)


def thumbnail_image(data):
    """A card's scaled QImage from a file's bytes, None if they are no image
    or only part of one: Qt decodes a cut file as grey."""
    image = _decoded(data)
    if image.isNull() or cut_short(data):
        return None
    return image.scaled(
        QSize(THUMBNAIL_SIZE, THUMBNAIL_SIZE),
        Qt.KeepAspectRatio,
        Qt.SmoothTransformation,
    )


def smaller_file(data):
    """A large image's bytes as a smaller PNG, or as they are: a tile decodes
    its file on the window's thread when it comes from the cache."""
    image = _decoded(data)
    if image.isNull() or max(image.width(), image.height()) <= CACHED_SIDE:
        return data
    smaller = image.scaled(QSize(CACHED_SIDE, CACHED_SIDE), Qt.KeepAspectRatio, Qt.SmoothTransformation)
    array = QByteArray()
    target = QBuffer(array)
    target.open(QIODevice.WriteOnly)
    smaller.save(target, "PNG")
    return bytes(array)


class ThumbnailLoader(ImageLoader):
    """A card's image, from the cache if it has it: read, decoded and scaled
    here, so that a dozen of them do not hold up the window."""

    ready = pyqtSignal(object)  # the scaled QImage, None if the data is no image

    def __init__(self, url, page_url, event_date, title):
        super().__init__(url, page_url, event_date)
        self.title = title

    def run(self):
        try:
            image = self.cached()
            if image is None:
                data = self.fetch()
                image = thumbnail_image(data)
                self.check_stop()
                if image is not None and self.title:
                    self.save(smaller_file(data))
            self.check_stop()
            self.ready.emit(image)
        except Stopped:
            pass
        except Exception as e:
            self.error.emit(str(e))

    def cached(self):
        """A file that cannot be read is no thumbnail: it is fetched again."""
        path = _noaa_cache.load_thumbnail(self.event_date, self.title)
        try:
            return thumbnail_image(path.read_bytes()) if path else None
        except OSError as e:
            logger.info("Could not read the thumbnail %s: %s", path, e)
            return None

    def save(self, data):
        try:
            _noaa_cache.save_thumbnail(self.event_date, self.title, data)
        except Exception as e:
            print(f"Failed to cache thumbnail: {e}")


class MovieStillLoader(ThumbnailLoader):
    """A tile's image from one frame of a movie. The server keeps the movie's
    index at its end, so the whole file comes down once, and is then dropped:
    only the still is kept. Without a still the tile is still a link."""

    def fetch(self):
        try:
            import imageio_ffmpeg

            program = imageio_ffmpeg.get_ffmpeg_exe()
        except (ImportError, RuntimeError) as e:
            raise RuntimeError(MOVIE_NO_STILL) from e
        with tempfile.TemporaryDirectory(prefix="solarviewer-movie-") as folder:
            # The Helioviewer browser's copy, if it has this one
            saved = _noaa_cache.cached_lwa_movie(self.event_date, self.url) if self.event_date else None
            movie = str(saved) if saved else os.path.join(folder, "movie.mp4")
            still = os.path.join(folder, "still.png")
            if not saved:
                with open(movie, "wb") as f:
                    self.stream(self.url, f.write)
            self.check_stop()
            try:
                # (ffmpeg writes what it found to stderr, and fails for want of an output)
                length = subprocess.run(
                    [program, "-hide_banner", "-i", movie], capture_output=True, text=True, timeout=30
                )
                found = MOVIE_LENGTH.search(length.stderr)
                if found is None:
                    raise RuntimeError(MOVIE_NO_STILL)
                hours, minutes, seconds = found.groups()
                at = (int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * STILL_AT
                subprocess.run(
                    [program, "-hide_banner", "-y", "-ss", f"{at:.1f}", "-i", movie,
                     "-frames:v", "1", "-update", "1", still],
                    capture_output=True, timeout=30, check=True,
                )
                with open(still, "rb") as f:
                    return f.read()
            except (OSError, subprocess.SubprocessError) as e:
                raise RuntimeError(MOVIE_NO_STILL) from e


class ImageSaver(ImageLoader):
    """Downloads an image into the file ``path``, which appears only when the
    image is complete: stopped or failed, what the file held stays. Nothing
    is written before the last byte has arrived, as a stalled download may
    never get to clean up after itself."""

    saved = pyqtSignal(str)

    def __init__(self, url, path, event_date=None):
        super().__init__(url, page_url=url, event_date=event_date)  # a page, for SolarMonitor images
        self.path = path

    def run(self):
        partial = f"{self.path}.{id(self):x}.part"  # another save may share the path
        try:
            data = self.fetch()
            self.check_stop()
            with open(partial, "wb") as f:
                f.write(data)
            os.replace(partial, self.path)
            self.saved.emit(self.path)
        except Stopped:
            pass
        except Exception as e:
            self.discard(partial)
            self.error.emit(str(e))

    @staticmethod
    def discard(path):
        try:
            os.remove(path)
        except OSError:
            pass


class FileSaver(ImageLoader):
    """Saves a large file a piece at a time, for files too big to hold in
    memory. The file appears only when it is whole, and a stop or a failure
    leaves nothing behind."""

    saved = pyqtSignal(str)
    parts = set()  # still being written, for a program leaving with one under way

    def __init__(self, url, path):
        super().__init__(url)
        self.path = path

    def run(self):
        partial = f"{self.path}.{id(self):x}.part"
        self.parts.add(partial)
        try:
            with open(partial, "wb") as f:
                self.stream(self.url, f.write)
            self.check_stop()
            os.replace(partial, self.path)
            self.saved.emit(self.path)
        except Stopped:
            ImageSaver.discard(partial)
        except Exception as e:
            ImageSaver.discard(partial)
            self.error.emit(str(e))
        finally:
            self.parts.discard(partial)

    @classmethod
    def sweep(cls):
        for partial in list(cls.parts):
            ImageSaver.discard(partial)


def _images_kept(fetched, saved):
    """The list just fetched, with what was saved standing in for the images
    that could not be looked up: Helioviewer's list is built whether it answers
    or not, and an entry with no date would take the place of a good one."""
    from ..solar_context import context_images as ci

    known = {image.title: image for image in saved or () if ci.image_day(image) is not None}
    if not known:
        return fetched
    kept = [known.get(image.title, image) if ci.image_day(image) is None else image for image in fetched]
    return kept or saved


class FetchWorker(StoppableThread):
    """Worker thread for fetching events, active regions, conditions, CMEs, and images.
    
    Uses cache when available - only fetches missing data from network, and
    with ``only_cached`` nothing at all: a date already fetched, on showing it.
    """

    done = pyqtSignal(
        object, object, object, object, object, object
    )  # (events, active_regions, conditions, cmes, images, ecallisto_bursts) tuple
    error = pyqtSignal(str)
    progress = pyqtSignal(str)  # Progress messages

    def __init__(self, event_date: date, force_refresh: bool = False, only_cached: bool = False):
        super().__init__()
        self.event_date = event_date
        self.only_cached = only_cached
        # The lists of the last days still grow: what was saved then is partial
        self.force_refresh = not only_cached and (force_refresh or _still_growing(event_date))
        self._stage = 0

    def _say(self, message, stage=None):
        """Report what is under way: a new ``stage`` of the six if given."""
        self.check_stop()
        if stage is not None:
            self._stage = stage
        self.progress.emit(f"{message} {self._stage}/{FETCH_STAGES}")

    def _image_looked_up(self, done, total):
        self._say(f"Fetching context images, {done} of {total}…")

    def run(self):
        try:
            cache = _noaa_cache
            
            # Check cache status
            cached_status = cache.get_cached_status(self.event_date)
            
            # === Events ===
            events = None
            if not self.force_refresh and cached_status['events']:
                self._say("Loading events from cache…", 1)
                events = cache.load_events(self.event_date)
            
            # A list saved before its file was final is asked for again: a
            # better one may be there by now, and it stands if none is
            if ne.standing(events)[0] is False and not self.only_cached:
                self._say("Fetching solar events…", 1)
                saved = events if events is not None else cache.load_events(self.event_date)
                fetched = ne.fetch_and_parse_events(self.event_date)
                events = ne.better_events(fetched, saved)
                if events is fetched and (fetched or ne.standing(fetched)[0]):
                    keep(cache.save_events, self.event_date, events)

            # === Active Regions ===
            active_regions = None
            if not self.force_refresh and cached_status['active_regions']:
                self._say("Loading active regions from cache…", 2)
                active_regions = cache.load_active_regions(self.event_date)
            
            if active_regions is None and not self.only_cached:
                try:
                    self._say("Fetching active regions…", 2)
                    from ..solar_context import active_regions as ar
                    active_regions = ar.fetch_and_parse_active_regions(self.event_date)
                    if active_regions:
                        cache.save_active_regions(self.event_date, active_regions)
                except Exception as ar_err:
                    print(f"Active regions fetch failed: {ar_err}")

            # === Conditions ===
            conditions = None
            if not self.force_refresh and cached_status['conditions']:
                self._say("Loading conditions from cache…", 3)
                conditions = cache.load_conditions(self.event_date)
            
            if conditions is None and not self.only_cached:
                try:
                    self._say("Fetching solar conditions…", 3)
                    from ..solar_context import realtime_data as rt
                    conditions = rt.fetch_conditions_for_date(self.event_date)
                    # (it comes back empty, not None, when the sources fail)
                    if conditions and (conditions.kp_index is not None or conditions.f107_flux is not None):
                        cache.save_conditions(self.event_date, conditions)
                except Exception as cond_err:
                    print(f"Solar conditions fetch failed: {cond_err}")

            # === CMEs ===
            cmes = None
            if not self.force_refresh and cached_status['cmes']:
                self._say("Loading CMEs from cache…", 4)
                cmes = cache.load_cmes(self.event_date)
            
            if cmes is None and not self.only_cached:
                try:
                    self._say("Fetching CME alerts…", 4)
                    from ..solar_context import cme_alerts as cme
                    cmes = cme.fetch_and_parse_cme_events(self.event_date)
                    # (the stand-in apart, or DONKI's would never be fetched again)
                    if cmes and cmes[0].source == "DONKI":
                        cache.save_cmes(self.event_date, cmes)
                    elif cmes:
                        cache.save_cdaw_cmes(self.event_date, cmes)
                except Exception as cme_err:
                    print(f"CME fetch failed: {cme_err}")

            # === Context Images ===
            images = None
            if not self.force_refresh and cached_status['images']:
                self._say("Loading context images from cache…", 5)
                images = cache.load_images(self.event_date)
                
                # Auto-heal cache for old images whose date cannot be told
                from ..solar_context import context_images as ci
                if images and not self.only_cached and any(ci.image_day(img) is None for img in images):
                    self._say("Refreshing the image list for its timestamps…")
                    images = None
            
            if images is None and not self.only_cached:
                try:
                    self._say("Fetching context images…", 5)
                    from ..solar_context import context_images as ci
                    images = _images_kept(
                        ci.fetch_context_images(self.event_date, progress=self._image_looked_up),
                        cache.load_images(self.event_date),
                    )
                    if any(ci.image_day(image) is not None for image in images):
                        keep(cache.save_images, self.event_date, images)
                except Exception as img_err:
                    print(f"Context images fetch failed: {img_err}")
            
            if images is None:
                images = cache.load_images(self.event_date) or []

            # === e-CALLISTO Bursts ===
            ecallisto_bursts = None
            if not self.force_refresh and cached_status.get('ecallisto_bursts'):
                self._say("Loading e-CALLISTO bursts from cache…", 6)
                ecallisto_bursts = cache.load_ecallisto_bursts(self.event_date)
            
            if ecallisto_bursts is None and not self.only_cached:
                try:
                    # Check if within supported range (2010, 2011, 2020+)
                    if self.event_date.year in [2010, 2011] or self.event_date.year >= 2020:
                        self._say("Fetching e-CALLISTO radio bursts…", 6)
                        ecallisto_bursts = ne.fetch_and_parse_ecallisto_bursts(self.event_date)
                        if ecallisto_bursts:
                            keep(cache.save_ecallisto_bursts, self.event_date, ecallisto_bursts)
                    else:
                        ecallisto_bursts = []
                except Exception as eb_err:
                    print(f"e-CALLISTO bursts fetch failed: {eb_err}")

            # What was saved earlier beats nothing when a refresh fails
            if ecallisto_bursts is None:
                ecallisto_bursts = cache.load_ecallisto_bursts(self.event_date) or []
            if conditions is None or (conditions.kp_index is None and conditions.f107_flux is None):
                conditions = cache.load_conditions(self.event_date) or conditions
            if events is None:
                events = cache.load_events(self.event_date)
            if active_regions is None:
                active_regions = cache.load_active_regions(self.event_date)
            if cmes is None or (cmes and cmes[0].source != "DONKI"):
                cmes = cache.load_cmes(self.event_date) or cmes or cache.load_cdaw_cmes(self.event_date)

            self.check_stop()
            self.done.emit(events, active_regions, conditions, cmes, images, ecallisto_bursts)
        except Stopped:
            pass
        except Exception as e:
            self.error.emit(str(e))


class GOESPlotWorker(StoppableThread):
    """Worker thread for fetching and plotting GOES X-ray flux."""

    done = pyqtSignal(object)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, event_date: date):
        super().__init__()
        self.event_date = event_date

    def run(self):
        try:
            self.progress.emit("Preparing the GOES search…")
            from sunpy.net import Fido, attrs as a
            from sunpy.timeseries import TimeSeries

            # Define time range for the full day
            t_start = datetime.combine(self.event_date, datetime.min.time())
            t_end = datetime.combine(self.event_date, datetime.max.time())

            self.check_stop()
            self.progress.emit("Searching for GOES data…")
            # Search for GOES XRS data
            # Use a.Resolution.flx1s (1-second data) if possible, or avg1m (1-minute)
            res = Fido.search(a.Time(t_start, t_end), a.Instrument("GOES"))

            if len(res) == 0:
                raise Exception("No GOES X-ray data found for this date.")

            # Filter results to get the "best" single file
            # 1. Prefer High Cadence (flx1s) over Average (avg1m)

            # Simple conversion to astropy table to sort/filter
            # tbl = res[0]

            # Searching for 'flx1s' first
            res_high = Fido.search(
                a.Time(t_start, t_end), a.Instrument("GOES"), a.Resolution("flx1s")
            )

            if len(res_high) > 0:
                res = res_high
            else:
                pass  # Fallback to whatever we found (likely 1m)

            # If we still have multiple satellites (e.g. 16 and 18), pick one.
            # Converting to list of rows and picking the first one is safest to avoid downloading 4 files.

            # Slice the UnifiedResponse to keep only the first row of the first provider results
            best_result = res[0, 0]

            self.check_stop()
            self.progress.emit("Downloading the GOES file…")
            files = Fido.fetch(best_result)

            if not files:
                raise Exception("Failed to download GOES data file.")

            self.check_stop()
            self.progress.emit("Reading the GOES file…")
            # Load TimeSeries
            ts = TimeSeries(files)

            # Concatenate if multiple files (though usually one per day/search)
            if isinstance(ts, list):
                if len(ts) > 1:
                    # TODO: Concatenate the TimeSeries objects
                    pass

            self.check_stop()
            self.done.emit(ts)

        except Stopped:
            pass
        except Exception as e:
            self.error.emit(str(e))


def read_goes_day(path, day):
    """The 1–8 Å flux of ``day`` from a GOES file: one value per minute, ``None``
    where there is none or it is flagged (the eclipses, above all)."""
    flux = [None] * MINUTES_PER_DAY
    try:
        import h5py
        import numpy as np

        with h5py.File(path, "r") as f:  # the GOES-16 and later files
            epoch = datetime(2000, 1, 1, 12)
            seconds = f["time"][:]
            values = f["xrsb_flux"][:]
            flags = f["xrsb_flag"][:] if "xrsb_flag" in f else np.zeros(len(values))
        start = (datetime.combine(day, datetime.min.time()) - epoch).total_seconds()
        for second, value, flag in zip(seconds, values, flags):
            minute = int((second - start) // 60)
            if 0 <= minute < MINUTES_PER_DAY and not flag and value > GOES_FILL:
                flux[minute] = float(value)
        return flux
    except OSError:
        pass  # not HDF5: an older satellite's file

    from sunpy.timeseries import TimeSeries

    table = TimeSeries(path).to_dataframe()
    for when, row in table.iterrows():
        minute = (when.to_pydatetime() - datetime.combine(day, datetime.min.time())) // timedelta(minutes=1)
        value = row.get("xrsb")
        if 0 <= minute < MINUTES_PER_DAY and value > GOES_FILL and not row.get("xrsb_quality", 0):
            flux[minute] = float(value)
    return flux


def read_swpc_goes(rows, day):
    """The 1-8 Å flux of ``day`` from SWPC's file, as ``read_goes_day``. Its
    ``flux`` has the electrons taken out, as the archive's has."""
    flux = [None] * MINUTES_PER_DAY
    begin = datetime.combine(day, datetime.min.time())
    for row in rows:
        if row.get("energy") != "0.1-0.8nm" or not str(row.get("time_tag", "")).startswith(str(day)):
            continue
        minute = (datetime.strptime(row["time_tag"][:19], "%Y-%m-%dT%H:%M:%S") - begin) // timedelta(minutes=1)
        value = row.get("flux")
        if isinstance(value, (int, float)) and value > GOES_FILL:
            flux[minute] = float(value)
    return flux


def known_minutes(flux):
    return sum(value is not None for value in flux or ())


class GoesDayWorker(StoppableThread):
    """The day's GOES 1–8 Å flux for the day band, from the cache or a 1-minute
    file. It holds nothing up: the tables are shown without waiting for it."""

    ready = pyqtSignal(object, object)  # the date, its flux per minute
    failed = pyqtSignal(object, str)  # the date, what went wrong

    def __init__(self, event_date: date):
        super().__init__()
        self.event_date = event_date

    def run(self):
        try:
            flux = _noaa_cache.load_goes_day(self.event_date)
            path = _noaa_cache.goes_day_path(self.event_date)
            if flux is None or saved_early(path, self.event_date):
                flux = self.fuller(flux, path)
            self.check_stop()
            self.ready.emit(self.event_date, flux)
        except Stopped:
            pass
        except Exception as e:
            logger.info("GOES 1-minute data for %s: %s", self.event_date, e)
            self.failed.emit(self.event_date, str(e))

    def fuller(self, saved, path):
        """What the archives have now if it has more than ``saved``, a curve
        that may stop short. A failure leaves ``saved``, if there is one."""
        try:
            flux = self.fetch()
        except Exception as e:
            if saved is None:
                raise
            logger.info("GOES 1-minute data for %s: %s", self.event_date, e)
            return saved
        self.check_stop()
        if saved is not None and known_minutes(flux) < known_minutes(saved):
            _asked_now(path)
            return saved
        keep(_noaa_cache.save_goes_day, self.event_date, flux)
        return flux

    def fetch(self):
        """The fuller of SWPC's file of the last week, the only one to reach the
        last hours, and the archive's daily file, which can stop short for days."""
        recent = None
        if (datetime.utcnow().date() - self.event_date).days <= SWPC_XRAY_DAYS:
            recent = self.fetch_recent()
            self.check_stop()
            if known_minutes(recent) >= GOES_WHOLE_DAY:
                return recent
        try:
            filed = self.fetch_file()
        except Exception:
            if recent and any(recent):
                return recent
            raise
        return recent if recent and known_minutes(recent) > known_minutes(filed) else filed

    def fetch_recent(self):
        """SWPC's 1-minute 0.1-0.8 nm flux of the date, ``None`` where it has none."""
        request = urllib.request.Request(SWPC_XRAYS, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return read_swpc_goes(json.load(response), self.event_date)
        except (OSError, ValueError) as e:
            logger.info("SWPC's GOES file for %s: %s", self.event_date, e)
            return None

    def fetch_file(self):
        from sunpy.net import Fido, attrs as a

        begin = datetime.combine(self.event_date, datetime.min.time())
        found = Fido.search(a.Time(begin, begin + timedelta(hours=23, minutes=59)), a.Instrument("GOES"), a.Resolution("avg1m"))
        if len(found) == 0 or len(found[0]) == 0:
            raise LookupError("No GOES data found for this date")
        self.check_stop()
        with tempfile.TemporaryDirectory() as folder:
            files = Fido.fetch(found[0, 0], path=os.path.join(folder, "{file}"), progress=False)
            if not files:
                raise LookupError("The GOES file could not be downloaded")
            self.check_stop()
            return read_goes_day(files[0], self.event_date)


class DayRecords(StoppableThread):
    """The day's records of ``solar_context.day_records``, each from the cache
    while that can stand, or from its archive, a few at a time. ``found`` says
    which date it is for, and a record is only saved under that date. What the
    record holds on failing is a sentence: ``{"said": ..., "trouble": True}``."""

    found = pyqtSignal(object, str, object)  # day, key, record

    def __init__(self, day, only_cached=False):
        super().__init__()
        self.day = day
        self.only_cached = only_cached

    def run(self):
        from ..solar_context import day_records as dr

        def one(key):
            self.check_stop()
            self.one_record(dr, key)

        try:
            on_threads(one, dr.FETCHERS, RECORD_THREADS)
        except Stopped:
            pass
        except Exception as e:
            logger.warning("The records of %s failed: %s", self.day, e)

    def one_record(self, dr, key):
        saved = _noaa_cache.load_day_record(self.day, key)  # a bad file reads as none, so it is asked for again
        if saved is not None and (
            self.only_cached or saved["record"].get("settled") or saved["asked"] == date.today().isoformat()
        ):
            self.found.emit(self.day, key, saved["record"])
            return
        if self.only_cached:
            return
        try:
            record = self.ask(dr, key)
            if not dr.well_formed(key, record):
                raise dr.RecordProblem("sent something that could not be read")
        except dr.NoRecord as e:
            # Old dates will not change, the last days can
            # (only an archive's own answer gets here: a refusal is a RecordProblem)
            record = {"said": str(e), "settled": (date.today() - self.day).days > RECORD_SETTLE_DAYS}
        except Exception as e:
            logger.info("%s of %s: %s", key, self.day, e)
            if saved is not None:
                self.check_stop()
                self.found.emit(self.day, key, saved["record"])
                return
            detail = e if isinstance(e, dr.RecordProblem) else "could not be read"
            self.check_stop()
            self.found.emit(self.day, key, {"said": f"{dr.NAMES[key]} {detail}", "trouble": True})
            return
        self.check_stop()
        if key != "forecast" or "said" not in record:
            keep(_noaa_cache.save_day_record, self.day, key, record)
        self.found.emit(self.day, key, record)

    def ask(self, dr, key):
        return dr.FETCHERS[key](self.day)


class DHSpectraWorker(StoppableThread):
    """Worker thread for fetching the day's radio spectra from their archives.
    
    Uses unified cache system - checks cache first before downloading.
    """

    progress = pyqtSignal(str)
    result_ready = pyqtSignal(str, str)  # Emits (source_key, file_path)
    done = pyqtSignal(dict, list)  # Emits (results_dict, error_list)
    source_ended = pyqtSignal(str)  # this one is dealt with, data or not
    sources_done = pyqtSignal(int, int)  # how many of how many
    # just before its source_ended: source, "no_file" or "unreachable", detail
    source_failed = pyqtSignal(str, str, str)
    # Removing error signal as we now accumulate errors in a list

    # CDAW server URLs
    # Primary: calibrated L2 daily spectrograms (well calibrated)
    WAVES_CALIBRATED_URL = 'https://cdaw.gsfc.nasa.gov/images/wind/waves/test_daily/'
    # Fallback: 24h full-day spectrograms
    WAVES_URL_24H = 'https://cdaw.gsfc.nasa.gov/images/wind/waves_h2M_24h/'
    SWAVES_URL = 'https://cdaw.gsfc.nasa.gov/images/stereo/swaves/'
    SWAVES_URL_NEW = 'https://cdaw.gsfc.nasa.gov/images/stereo/swaves_new/'

    def __init__(self, event_datetime: datetime, force_refresh: bool = False, sources=None):
        super().__init__()
        self.event_datetime = event_datetime
        self.force_refresh = force_refresh
        self.sources = sources  # only these, for Try again; None for all
        self._sources_done = 0
        self._count_lock = threading.Lock()
        self._composite_lock = threading.Lock()
        self._composites = {}  # day: the file, or what kept it away
        self._latest_lock = threading.Lock()
        self._latest_asked = False

    def _source_done(self, source):
        """``source`` has been dealt with, unless told to stop."""
        self.check_stop()
        with self._count_lock:
            self._sources_done += 1
            done = self._sources_done
        self.source_ended.emit(source)
        self.sources_done.emit(done, len(self._wanted()))

    def _wanted(self):
        expected = expected_sources(self.event_datetime.date())
        return [s for s in expected if self.sources is None or s in self.sources]

    def _found(self, source, path, results):
        results[source] = str(path)
        self.result_ready.emit(source, str(path))

    def run(self):
        """Fetch spectra from their archives, using cache when available."""
        cache = _noaa_cache
        event_date = self.event_datetime.date()
        results = {source: None for source in SPECTRA_NAMES}
        errors = []
        wanted = self._wanted()

        def one_source(source):
            self.check_stop()
            name = SPECTRA_NAMES[source]
            cached = None if self.force_refresh or source in OVSA_SOURCES else cache.load_spectra(event_date, source)
            cached = cached if cached and whole_on_disk(cached) else None
            # (Learmonth's file is its station's whole day from the first)
            if cached and (source == "learmonth" or not saved_early(cached, event_date)):
                self.progress.emit(f"Loading {name} from cache…")
                if source == "learmonth":
                    self._learmonth_next()
                self._found(source, cached, results)
            else:
                self._fetch_source(source, name, results, errors, cached)
            self._source_done(source)

        try:
            on_threads(one_source, wanted, SPECTRA_THREADS)
            self.done.emit(results, errors)
        except Stopped:
            pass
        except Exception as e:
            # Final safety net for the entire worker run
            errors.append(f"Critical worker error: {str(e)}")
            self.done.emit(results, errors)

    def _fetch_source(self, source, name, results, errors, kept=None):
        """Fetch one source; if it brings nothing, say why. ``kept`` is an
        earlier file that can be partial: it stands unless a fuller one comes."""
        try:
            data = getattr(self, f"_fetch_{source}")()
            if data is None:
                if kept:
                    self._found(source, kept, results)
                    return
                self.progress.emit(f"No {name} data for this date")
                self.source_failed.emit(source, "no_file", f"No {name} file for this date")
                return
            if not isinstance(data, os.PathLike):
                self._refuse_bad_file(source, data)
            if kept and not isinstance(data, os.PathLike) and len(data) < os.path.getsize(kept):
                # A stitched day's hours with data compress far worse than its blank ones
                _asked_now(kept)
                data = kept
            path = data if isinstance(data, os.PathLike) else _noaa_cache.save_spectra(self.event_datetime.date(), source, data)
            self._found(source, path, results)
            self.progress.emit(f"{name} data found!")
        except Exception as e:
            if kept:
                logger.info("%s kept, as the archive failed: %s", source, e)
                self._found(source, kept, results)
                return
            err_msg = f"{name}: {str(e)[:50]}"
            self.progress.emit(err_msg)
            errors.append(err_msg)
            self.source_failed.emit(source, _failure_kind(e), str(e))

    @staticmethod
    def _refuse_bad_file(source, data):
        """Whatever came under the file's name is only saved if it is the file:
        a proxy's page or an empty answer must not read as a day of data."""
        if source == "learmonth":
            return  # read as records, by its fetcher
        if data.lstrip()[:1] == b"<":
            raise ConnectionError("The archive sent a web page, not its file")
        if not data.startswith(IMAGE_KINDS):
            raise ConnectionError("The archive sent something other than an image")
        if cut_short(data):
            raise ConnectionError("The archive sent part of its file")

    @staticmethod
    def _read(url, timeout=15, context=None):
        import urllib.request

        extra = {"context": context} if context is not None else {}
        return urllib.request.urlopen(url, timeout=timeout, **extra).read()

    def _get(self, url, timeout=30, byte_range=None):
        """``url``'s bytes as the archives like to be asked: by name."""
        headers = {"User-Agent": USER_AGENT}
        if byte_range:
            headers["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as response:
            promised = int(response.headers.get("Content-Length") or 0)
            data = bytearray()
            while True:
                self.check_stop()
                chunk = response.read(CHUNK_BYTES)
                if not chunk:
                    break
                data += chunk
        if len(data) < promised:  # a read of a part says nothing of a cut connection
            raise ConnectionError(f"{len(data)} of {promised} bytes came from {url}")
        return bytes(data)

    def _get_or_none(self, url, kinds, **kwargs):
        """As ``_get``, ``None`` where the archive has no such file or sends
        anything but one of ``kinds``: its first bytes, a web page above all."""
        try:
            data = self._get(url, **kwargs)
        except urllib.error.HTTPError as e:
            if _failure_kind(e) == "no_file":
                return None
            raise
        if data.startswith(kinds):
            return data
        if data.lstrip()[:1] == b"<":
            raise ConnectionError("The archive sent a web page, not its file")
        return None

    def _ovsa_file(self, day, source, url, kinds):
        """``day``'s file of ``source``, from the cache once the day has
        settled and from it again if the archive cannot be asked. Saved under
        its own date, whatever the day on show, and only if it is whole."""
        cached = _noaa_cache.load_spectra(day, source)
        cached = cached if cached and whole_on_disk(cached) else None
        settled = not _still_growing(day)
        if cached and settled and not self.force_refresh:
            return cached
        try:
            data = self._get_or_none(url, kinds, timeout=60)
            if data and not whole_file(data):
                raise ConnectionError("The archive sent part of its file")
        except Exception:
            if cached:
                return cached
            raise
        return _noaa_cache.save_spectra(day, source, data) if data else cached

    def _composite(self, day):
        """The composite of ``day``, asked for once however many want it. What
        kept it away is kept too, for the source that has to say."""
        with self._composite_lock:
            if day not in self._composites:
                self.check_stop()
                try:
                    self._composites[day] = None if day < CONTEXT_FIRST else self._ovsa_file(
                        day, "context", f"{EOVSA_DATA}?kind=spec&date={day}", (b"\xff\xd8",)
                    )
                except Exception as e:
                    self._composites[day] = e
            return self._composites[day]

    def _fetch_context(self):
        found = self._composite(self.event_datetime.date())
        if isinstance(found, Exception):
            raise found
        return found

    def _ask_latest(self):
        """How far EOVSA and OVRO-LWA have got, once for a recent date: what
        decides between no data and none yet. Kept with the date, for when it
        is shown from the cache. Not an answer is no answer, and a lane says less."""
        day = self.event_datetime.date()
        with self._latest_lock:
            if self._latest_asked or (datetime.utcnow().date() - day).days > LATEST_DAYS:
                return
            self._latest_asked = True
            latest = {}
            for source, url, read in (("eovsa", EOVSA_LATEST, eovsa_latest), ("lwa", LWA_DATES, lwa_latest)):
                self.check_stop()
                try:
                    found = read(self._get(url, timeout=20))
                    if found:
                        latest[source] = found.isoformat()
                except Exception as e:
                    logger.info("%s's latest date: %s", source, e)
            if latest:
                keep(_noaa_cache.save_archive_latest, day, latest)

    def _band_files(self, source, url_of, kinds):
        """A lane's plots: the composite and its own plot, of the day and the
        day before, whose evening runs into this one. Either alone will do."""
        day = self.event_datetime.date()
        before = day - timedelta(days=1)
        self.progress.emit(f"Searching for {SPECTRA_NAMES[source]} data…")
        self._ask_latest()
        context = self._composite(day)
        self._composite(before)
        try:
            found = self._ovsa_file(day, source, url_of(day), kinds)
            try:
                self._ovsa_file(before, source, url_of(before), kinds)
            except Exception as e:
                logger.info("%s of the day before: %s", source, e)
        except Exception:
            if isinstance(context, os.PathLike):
                return context
            raise
        return found or (context if isinstance(context, os.PathLike) else None)

    def _fetch_eovsa(self):
        return self._band_files(
            "eovsa", lambda day: f"{EOVSA_DATA}?kind=flaremon_xsp&date={day}", (b"\x89PNG",)
        )

    def _fetch_lwa(self):
        found = self._band_files("lwa", lambda day: LWA_DAILY.format(day=day), (b"\x89PNG",))
        if found:
            day = self.event_datetime.date()
            self.progress.emit("Reading OVRO-LWA's sample times…")
            self._lwa_times(day)
            self._lwa_times(day - timedelta(days=1))
            self._lwa_links()
        return found

    def _lwa_times(self, day):
        """The time table of the plot of ``day``, a few pieces of its FITS, and
        kept: the plot is not placed without it. It can come days after the plot."""
        from .spectra_reading import read_lwa_table

        saved = _noaa_cache.load_lwa_times(day)
        # (one flat throughout was read before its floor followed the file's scale)
        if (saved and len(set(saved["levels"])) > 1) or day < LWA_FIRST:
            return
        url = LWA_FITS.format(day=day)
        try:
            table = read_lwa_table(lambda first, last: self._get(url, timeout=60, byte_range=(first, last)), day)
            self.check_stop()
            if table:
                _noaa_cache.save_lwa_times(day, table)
        except (OSError, ValueError, KeyError, IndexError) as e:
            logger.info("OVRO-LWA's times of %s: %s", day, e)

    def _lwa_links(self):
        """Where the day's movie and FITS are, and how big the FITS: a size can
        only be said by asking, and is never guessed. Each is asked for until
        it has come."""
        from ..solar_context import radio_images

        day = self.event_datetime.date()
        links = _noaa_cache.load_lwa_links(day)
        if "movie" in links and "fits" in links:
            return
        if "movie" not in links:
            try:
                answer = json.loads(self._get(LWA_MOVIE_LOOKUP.format(day=day)).decode("utf-8"))
                if radio_images.lwa_movie_of(answer, day):
                    links["movie"] = answer["url"]
            except Exception as e:
                logger.info("OVRO-LWA movie of %s: %s", day, e)
        if "fits" not in links:
            url = LWA_FITS.format(day=day)
            try:
                request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="HEAD")
                with urllib.request.urlopen(request, timeout=20) as response:
                    size = int(response.headers.get("Content-Length") or 0)
                if size:
                    links.update(fits=url, fits_bytes=size)
            except Exception as e:
                logger.info("OVRO-LWA FITS of %s: %s", day, e)
        if links:
            keep(_noaa_cache.save_lwa_links, day, links)

    def _fetch_learmonth(self):
        """The station's file of the day and, from the next one, the records
        after its evening start that belong to this day. Those are asked for
        by range, so that a day shown costs little more than its own file."""
        from ..radio_data_downloader.radio_data_downloader import RECORD_SIZE, rstn_day_file
        from .spectra_reading import learmonth_head

        day = self.event_datetime.date()
        self.progress.emit("Searching for Learmonth data…")
        hit = rstn_day_file("Learmonth", day)
        if not hit:
            return None
        self._learmonth_next()
        try:
            data = self._get_or_none(LEARMONTH_GIF.format(day=day), (b"GIF",))
            if data:
                _noaa_cache.save_spectra(day, "learmonth_plot", data)
        except Exception as e:
            logger.info("Learmonth's plot of %s: %s", day, e)
        self.progress.emit("Fetching the Learmonth file…")
        data = self._get(hit[0], timeout=120)
        if hit[0].endswith(".gz"):
            import gzip

            data = gzip.decompress(data)
        if data.lstrip()[:1] == b"<":
            raise ConnectionError("The archive sent a web page, not its file")
        return data if len(data) >= RECORD_SIZE and learmonth_head(data[:24]) else None

    def _learmonth_next(self):
        """The end of the day, from the next file: the first thing in it says
        how much of it is wanted. Asked again until it has come."""
        from ..radio_data_downloader.radio_data_downloader import RECORD_SIZE as record_size, rstn_day_file

        day = self.event_datetime.date()
        next_day = day + timedelta(days=1)
        try:
            saved = _noaa_cache.load_spectra(day, "learmonth_next")
            if saved and whole_on_disk(saved):
                return
            hit = rstn_day_file("Learmonth", next_day)
            if not hit or hit[0].endswith(".gz"):
                return  # NOAA's old files are packed, and cannot be read in part
            head = self._get(hit[0], byte_range=(0, record_size - 1))
            if len(head) < record_size:
                return
            year, month, date_of_month, hour, minute, second = head[:6]
            start = datetime(2000 + year, month, date_of_month, hour, minute, second)
            left = (datetime.combine(next_day, datetime.min.time()) - start).total_seconds()
            if 0 < left < 12 * 3600:
                records = int(left / 3) + 100  # a little over, as a record can take longer
                data = self._get(hit[0], timeout=60, byte_range=(0, records * record_size - 1))
                _noaa_cache.save_spectra(day, "learmonth_next", data[: len(data) // record_size * record_size])
        except Exception as e:
            logger.info("The end of Learmonth's %s: %s", day, e)

    def _fetch_wind(self):
        date_str = self.event_datetime.strftime('%Y%m%d')
        date_path = self.event_datetime.strftime('%Y/%m/%d')
        self.progress.emit("Searching for calibrated WIND/WAVES data…")
        try:
            # Calibrated source: test_daily/YYYY/YYYYMMDD_windwaves__scale_2to6sfu_l2_cdf_v1.jpg
            filename = f"{date_str}_windwaves__scale_2to6sfu_l2_cdf_v1.jpg"
            data = self._read(f"{self.WAVES_CALIBRATED_URL}{self.event_datetime.year}/{filename}")
            if whole_file(data):
                return data
        except Exception as e:
            logger.info("Calibrated WIND/WAVES of %s: %s", self.event_datetime.date(), e)
        # Many dates have no calibrated file: only the fallback's answer counts
        self.progress.emit("Calibrated source unavailable, trying fallback…")

        wind_dir_url = f"{self.WAVES_URL_24H}{date_path}/"
        html = self._read(wind_dir_url).decode('utf-8')
        # Parse for 24h windwaves PNG files (prefer h2M over h2hk)
        matches = re.findall(r'href="([^"]*windwaves_h2M_sfu_24h\.png)"', html, re.IGNORECASE)
        if not matches:
            matches = re.findall(r'href="([^"]*windwaves_h2hk_sfu_24h\.png)"', html, re.IGNORECASE)
        # The latest 24h image: the last one of the day
        best_match = self._find_latest_image(matches)
        return self._read(f"{wind_dir_url}{best_match}", timeout=30) if best_match else None

    def _fetch_stereo(self, pattern_new, pattern_old, name):
        """A day's 7-hour plots side by side, or the latest if they cannot be."""
        year = self.event_datetime.year
        swaves_url = self.SWAVES_URL_NEW if year >= 2018 else self.SWAVES_URL
        stereo_dir_url = f"{swaves_url}{self.event_datetime.strftime('%Y/%m/%d')}/"
        self.progress.emit(f"Searching for {name} data…")
        html = self._read(stereo_dir_url).decode('utf-8')
        pattern = pattern_old if swaves_url == self.SWAVES_URL else pattern_new
        matches = sorted(re.findall(pattern, html, re.IGNORECASE))
        if not matches:
            return None
        self.progress.emit(f"Stitching {name} images…")
        stitched = self._stitch_images(stereo_dir_url, matches)
        if stitched:
            return stitched
        best = self._find_latest_image(matches)
        return self._read(f"{stereo_dir_url}{best}", timeout=30) if best else None

    def _fetch_stereo_a(self):
        # Old format (2007-2017): swaves2A; new format (2018+): sta_waves_7h
        return self._fetch_stereo(r'href="([^"]*sta_waves_7h[^"]*\.png)"', r'href="([^"]*swaves2A\.png)"', "STEREO-A")

    def _fetch_stereo_b(self):
        return self._fetch_stereo(r'href="([^"]*swaves2B\.png)"', r'href="([^"]*swaves2B\.png)"', "STEREO-B")

    def _fetch_norp(self):
        self.progress.emit("Searching for Nobeyama (NORP) data…")
        return self._read(self.event_datetime.strftime(
            "https://solar.nro.nao.ac.jp/norp/html/daily/%Y/%m/pl%y%m%d.png"
        ))

    def _fetch_nancay(self, url, name):
        import ssl

        self.progress.emit(f"Searching for {name} data…")
        return self._read(self.event_datetime.strftime(url), context=ssl._create_unverified_context())

    def _fetch_orfees(self):
        return self._fetch_nancay("https://rsdb.obs-nancay.fr/QL/Orfees/orfees%Y%m%d.png", "ORFEES")

    def _fetch_nda(self):
        return self._fetch_nancay("https://rsdb.obs-nancay.fr/QL/Nda/NdaP1F%Y%m%d.png", "NDA")

    def _fetch_yamagawa(self):
        self.progress.emit("Fetching and stitching Yamagawa data…")
        return self._fetch_nict_spectra('yamagawa', self.event_datetime.date())

    def _fetch_hiras(self):
        self.progress.emit("Fetching and stitching HiRAS data…")
        return self._fetch_nict_spectra('hiras', self.event_datetime.date())

    def _fetch_nict_spectra(self, station: str, event_date: date) -> Optional[bytes]:
        """Fetch hourly strips, legend, and range from NICT and stitch them horizontally.
        
        Follows the logic from yamagawa.py precisely.
        """
        import requests
        from PIL import Image
        import io
        
        referer = "https://solobs.nict.go.jp/radio/cgi-bin/MainDisplay.pl"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Referer': referer
        }
        
        base_url = f"https://solobs.nict.go.jp/radio/data/img/{station}"
        fixed_url = f"{base_url}/fixed"
        
        if station == 'yamagawa':
            prefix = "YAMAGAWA"
            name = "Yamagawa"
        else:
            prefix = name = "HiRAS"

        def download_pil_image(url):
            """The image, ``None`` if the archive has no such file; a server
            that fails does not read as an empty archive."""
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code >= 500:
                raise RuntimeError(f"HTTP {r.status_code} from {url}")
            return Image.open(io.BytesIO(r.content)) if r.status_code == 200 else None

        # 1. Download legend and range
        cb = download_pil_image(f"{fixed_url}/{prefix}_FIXTMIMG_legend.png")
        ax = download_pil_image(f"{fixed_url}/{prefix}_FIXTMIMG_range.png")

        if not cb or not ax:
            return None

        # 2. Download 24 hourly images
        hourly_images = []
        real_hours = 0
        target_size = (450, 600) 
        
        year = event_date.strftime("%Y")
        month = event_date.strftime("%m")
        day = event_date.strftime("%d")
        date_compact = event_date.strftime("%Y%m%d")

        count = iter(range(1, 25))  # (next() on it is safe across the threads)

        def one_hour(h):
            """The hour's strip and whether it is the date's own."""
            self.check_stop()
            self.progress.emit(f"Fetching {name} hour {next(count)} of 24…")
            hour_str = f"{h:02d}"
            img = download_pil_image(f"{base_url}/{year}/{month}/{day}/{prefix}_{date_compact}{hour_str}.png")
            if img:
                img.load()
                return img, True
            return download_pil_image(f"{fixed_url}/{prefix}_FIXTMIMG{hour_str}.png"), False

        strips = on_threads(one_hour, range(24), NICT_THREADS)
        for img, real in strips:
            if real:
                real_hours += 1
                target_size = img.size
                hourly_images.append(img)
            elif img:
                hourly_images.append(img.resize(target_size, Image.Resampling.LANCZOS))
            else:
                hourly_images.append(Image.new('RGB', target_size, (0, 0, 0)))

        if not real_hours:
            return None  # the fixed fallbacks alone are no day of data

        # 3. Stitch
        max_height = max(cb.height, ax.height, max(img.height for img in hourly_images))
        total_width = cb.width + ax.width + sum(img.width for img in hourly_images)

        stitched_img = Image.new('RGB', (total_width, max_height), (255, 255, 255))
        
        x_offset = 0
        # Paste Colorbar
        stitched_img.paste(cb, (x_offset, 0))
        x_offset += cb.width
        
        # Paste Axis
        stitched_img.paste(ax, (x_offset, 0))
        x_offset += ax.width
        
        # Paste Hourly Plots
        for img in hourly_images:
            stitched_img.paste(img, (x_offset, 0))
            x_offset += img.width

        img_byte_arr = io.BytesIO()
        stitched_img.save(img_byte_arr, format='PNG')
        return img_byte_arr.getvalue()

    def _find_latest_image(self, filenames: list) -> str:
        """Find the image file with the latest timestamp for full-day coverage."""
        from datetime import datetime as dt
        import re
        
        if not filenames:
            return None
        
        best_file = None
        max_time = None
        
        for fname in filenames:
            # Try to extract timestamp from filename (format: YYYYMMDD_HHMMSS)
            match = re.search(r'(\d{8})_(\d{6})', fname)
            if match:
                try:
                    file_dt = dt.strptime(f"{match.group(1)}_{match.group(2)}", "%Y%m%d_%H%M%S")
                    if max_time is None or file_dt > max_time:
                        max_time = file_dt
                        best_file = fname
                except ValueError:
                    continue
        
        # If no timestamp found, return the last file (usually latest in sorted order)
        return best_file if best_file else filenames[-1]

    def _stitch_images(self, base_url: str, filenames: list) -> Optional[bytes]:
        """Download multiple images (7 hours apart) and stitch them horizontally."""
        from PIL import Image
        import io
        import urllib.request
        from datetime import datetime as dt
        import re
        
        if not filenames:
            return None
            
        # Parse timestamps and sort
        file_times = []
        for fname in filenames:
            match = re.search(r'(\d{8})_(\d{6})', fname)
            if match:
                try:
                    file_dt = dt.strptime(f"{match.group(1)}_{match.group(2)}", "%Y%m%d_%H%M%S")
                    file_times.append((file_dt, fname))
                except ValueError:
                    continue
        
        file_times.sort() # Ensure chronological order
        
        # Select files approximately 7 hours apart to cover the day without redundancy
        selected_filenames = []
        if file_times:
            last_time = None
            for ftime, fname in file_times:
                # Use slightly less than 7h (e.g. 6.5h) to ensure we don't miss a segment 
                # due to slightly varied start times (7h plots often start at 00, 07, 14, 21)
                if last_time is None or (ftime - last_time).total_seconds() >= 6.5 * 3600:
                    selected_filenames.append(fname)
                    last_time = ftime
        else:
            # Fallback for old formats without timestamps (unlikely for multi-file)
            selected_filenames = sorted(list(set(filenames)))[:4]
            
        if not selected_filenames:
            return None
            
        images = []
        try:
            # Limit to 5 images to avoid excessive memory usage
            for fname in selected_filenames[:5]:
                self.check_stop()
                img_url = f"{base_url}{fname}"
                req = urllib.request.urlopen(img_url, timeout=20)
                img_data = req.read()
                img = Image.open(io.BytesIO(img_data))
                images.append(img)
                
            if not images:
                return None
                
            if len(images) == 1:
                # Still return bytes for consistent interface
                img_byte_arr = io.BytesIO()
                images[0].save(img_byte_arr, format='PNG')
                return img_byte_arr.getvalue()
                
            # Stitch horizontally
            widths, heights = zip(*(i.size for i in images))
            total_width = sum(widths)
            max_height = max(heights)
            
            new_im = Image.new('RGB', (total_width, max_height))
            
            x_offset = 0
            for im in images:
                # Align to top (y=0)
                new_im.paste(im, (x_offset, 0))
                x_offset += im.size[0]
                
            # Convert back to bytes
            img_byte_arr = io.BytesIO()
            new_im.save(img_byte_arr, format='PNG')
            return img_byte_arr.getvalue()
            
        except Exception as e:
            print(f"Stitching failed: {e}")
            return None

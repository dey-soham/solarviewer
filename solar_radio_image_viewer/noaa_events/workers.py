"""
Background threads of the solar events viewer: fetching the day's data,
resolving and loading images, the GOES X-ray plot and the radio dynamic
spectra.
"""

import logging
import os
import re
import tempfile
from datetime import datetime, date, timedelta
from typing import Optional
from PyQt5.QtCore import QSize, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QImage
from . import noaa_events as ne

from .cache import _noaa_cache
from ..version import __version__

logger = logging.getLogger(__name__)

FETCH_STAGES = 6
THUMBNAIL_SIZE = 320
CHUNK_BYTES = 64 * 1024
MINUTES_PER_DAY = 24 * 60
GOES_FILL = 1e-9  # the files' floor: what they hold where they have no value


NICT_TRANSITION_DATE = date(2016, 7, 11)  # Yamagawa took over from HiRAS


def expected_sources(day):
    """The spectra that can exist for ``day``, high frequency to low, ground
    before space."""
    sources = ["norp", "yamagawa" if day >= NICT_TRANSITION_DATE else "hiras", "orfees", "nda"]
    if day.year >= 2007:  # STEREO started in October 2006
        sources.append("stereo_a")
    if 2007 <= day.year <= 2014:  # STEREO-B fell silent in 2014
        sources.append("stereo_b")
    sources.append("wind")
    return sources


# Every source, in the order the worker goes through them
SPECTRA_NAMES = {
    'wind': "WIND/WAVES", 'stereo_a': "STEREO-A", 'stereo_b': "STEREO-B", 'norp': "Nobeyama (NORP)",
    'orfees': "ORFEES", 'nda': "NDA", 'yamagawa': "Yamagawa", 'hiras': "HiRAS",
}


def _failure_kind(error):
    """Whether ``error`` means the archive has no such file (``no_file``) or
    it could not be asked (``unreachable``: timeouts, refused, 5xx)."""
    code = getattr(error, "code", None) or getattr(getattr(error, "response", None), "status_code", None)
    return "no_file" if code in (403, 404, 410) else "unreachable"


def _still_growing(day):
    """Whether the lists for ``day`` can still gain entries: SWPC's for today
    and a CME window of three days to either side."""
    return (datetime.utcnow().date() - day).days <= 3


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

    def __init__(self, url, page_url=None):
        super().__init__()
        self.url = url
        self.page_url = page_url

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
        # Try to resolve high-res if page_url available
        # BUT: Skip for Helioviewer URLs (they're already direct image URLs)
        if self.page_url and "helioviewer.org" not in self.page_url:
            try:
                from ..solar_context import context_images as ci

                resolved = ci.resolve_full_image_url(self.page_url)
                if resolved:
                    target_url = resolved
            except Exception as e:
                print(f"Failed to resolve high-res: {e}")

        data = bytearray()
        self.stream(target_url, data.extend)
        return bytes(data)

    def stream(self, url, write):
        """Download ``url`` a piece at a time into ``write``, saying how far
        along it is and stopping when told to."""
        import requests

        with requests.get(
            url, timeout=60, headers={"User-Agent": f"SolarViewer/{__version__} (python-requests)"}, stream=True
        ) as response:
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}")
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            for chunk in response.iter_content(CHUNK_BYTES):
                self.check_stop()
                write(chunk)
                done += len(chunk)
                self.received.emit(done, max(total, done) if total else 0)


def thumbnail_image(data):
    """A card's scaled QImage from a file's bytes, None if they are no image."""
    image = QImage.fromData(data)
    if image.isNull():
        return None
    return image.scaled(
        QSize(THUMBNAIL_SIZE, THUMBNAIL_SIZE),
        Qt.KeepAspectRatio,
        Qt.SmoothTransformation,
    )


class ThumbnailLoader(ImageLoader):
    """A card's image, from the cache if it has it: read, decoded and scaled
    here, so that a dozen of them do not hold up the window."""

    ready = pyqtSignal(object)  # the scaled QImage, None if the data is no image

    def __init__(self, url, page_url, event_date, title):
        super().__init__(url, page_url)
        self.event_date = event_date
        self.title = title

    def run(self):
        try:
            cached = _noaa_cache.load_thumbnail(self.event_date, self.title)
            image = thumbnail_image(cached.read_bytes()) if cached else None
            if image is None:
                data = self.fetch()
                image = thumbnail_image(data)
                self.check_stop()
                if image is not None and self.title:
                    self.save(data)
            self.check_stop()
            self.ready.emit(image)
        except Stopped:
            pass
        except Exception as e:
            self.error.emit(str(e))

    def save(self, data):
        try:
            _noaa_cache.save_thumbnail(self.event_date, self.title, data)
        except Exception as e:
            print(f"Failed to cache thumbnail: {e}")


class ImageSaver(ImageLoader):
    """Downloads an image into the file ``path``, which appears only when the
    image is complete: stopped or failed, what the file held stays. Nothing
    is written before the last byte has arrived, as a stalled download may
    never get to clean up after itself."""

    saved = pyqtSignal(str)

    def __init__(self, url, path):
        super().__init__(url, page_url=url)  # a page, for SolarMonitor images
        self.path = path

    def run(self):
        partial = f"{self.path}.{id(self):x}.part"  # another save may share the path
        try:
            data = self.fetch()
            self.check_stop()
            if data.lstrip()[:1] == b"<":  # the page, when its image was not found
                raise RuntimeError("The archive sent a web page, not an image")
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
            
            if events is None and not self.only_cached:
                self._say("Fetching solar events…", 1)
                events = ne.fetch_and_parse_events(self.event_date)
                if events:
                    cache.save_events(self.event_date, events)

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
                    images = ci.fetch_context_images(
                        self.event_date, progress=self._image_looked_up
                    )
                    if images:
                        cache.save_images(self.event_date, images)
                except Exception as img_err:
                    print(f"Context images fetch failed: {img_err}")
            
            if images is None:
                images = []

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
                            cache.save_ecallisto_bursts(self.event_date, ecallisto_bursts)
                    else:
                        ecallisto_bursts = []
                except Exception as eb_err:
                    print(f"e-CALLISTO bursts fetch failed: {eb_err}")
                    ecallisto_bursts = []

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
            if flux is None:
                flux = self.fetch()
                self.check_stop()
                _noaa_cache.save_goes_day(self.event_date, flux)
            self.check_stop()
            self.ready.emit(self.event_date, flux)
        except Stopped:
            pass
        except Exception as e:
            logger.info("GOES 1-minute data for %s: %s", self.event_date, e)
            self.failed.emit(self.event_date, str(e))

    def fetch(self):
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

    def _source_done(self, source):
        """``source`` has been dealt with: the next, unless told to stop."""
        self.check_stop()
        self._sources_done += 1
        self.source_ended.emit(source)
        self.sources_done.emit(self._sources_done, len(self._wanted()))

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
        try:
            for source in sorted(wanted, key=list(SPECTRA_NAMES).index):
                name = SPECTRA_NAMES[source]
                cached = None if self.force_refresh else cache.load_spectra(event_date, source)
                if cached:
                    self.progress.emit(f"Loading {name} from cache…")
                    self._found(source, cached, results)
                else:
                    self._fetch_source(source, name, results, errors)
                self._source_done(source)
            self.done.emit(results, errors)
        except Stopped:
            pass
        except Exception as e:
            # Final safety net for the entire worker run
            errors.append(f"Critical worker error: {str(e)}")
            self.done.emit(results, errors)

    def _fetch_source(self, source, name, results, errors):
        """Fetch one source; if it brings nothing, say why."""
        try:
            data = getattr(self, f"_fetch_{source}")()
            if data:
                self._found(source, _noaa_cache.save_spectra(self.event_datetime.date(), source, data), results)
                self.progress.emit(f"{name} data found!")
            else:
                self.progress.emit(f"No {name} data for this date")
                self.source_failed.emit(source, "no_file", f"No {name} file for this date")
        except Exception as e:
            err_msg = f"{name}: {str(e)[:50]}"
            self.progress.emit(err_msg)
            errors.append(err_msg)
            self.source_failed.emit(source, _failure_kind(e), str(e))

    @staticmethod
    def _read(url, timeout=15, context=None):
        import urllib.request

        extra = {"context": context} if context is not None else {}
        return urllib.request.urlopen(url, timeout=timeout, **extra).read()

    def _fetch_wind(self):
        date_str = self.event_datetime.strftime('%Y%m%d')
        date_path = self.event_datetime.strftime('%Y/%m/%d')
        self.progress.emit("Searching for calibrated WIND/WAVES data…")
        try:
            # Calibrated source: test_daily/YYYY/YYYYMMDD_windwaves__scale_2to6sfu_l2_cdf_v1.jpg
            filename = f"{date_str}_windwaves__scale_2to6sfu_l2_cdf_v1.jpg"
            return self._read(f"{self.WAVES_CALIBRATED_URL}{self.event_datetime.year}/{filename}")
        except Exception:
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

        for h in range(24):
            self.check_stop()
            self.progress.emit(f"Fetching {name} hour {h + 1} of 24…")
            hour_str = f"{h:02d}"
            url = f"{base_url}/{year}/{month}/{day}/{prefix}_{date_compact}{hour_str}.png"
            img = download_pil_image(url)
            
            if not img:
                fallback_url = f"{fixed_url}/{prefix}_FIXTMIMG{hour_str}.png"
                img = download_pil_image(fallback_url)
                if img:
                    img = img.resize(target_size, Image.Resampling.LANCZOS)
                else:
                    img = Image.new('RGB', target_size, (0, 0, 0))
            else:
                real_hours += 1
                if img.size != target_size:
                    target_size = img.size
            
            hourly_images.append(img)

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

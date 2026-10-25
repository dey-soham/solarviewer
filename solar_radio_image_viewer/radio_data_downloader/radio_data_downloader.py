#!/usr/bin/env python3
"""
Radio Data Downloader - Core module for downloading and processing radio solar data.

This module downloads solar radio dynamic spectra and writes them as FITS
files the Dynamic Spectrum Viewer opens (the spectrum as time x frequency,
with TIME_AXIS and FREQ_AXIS tables).

Supported:
- RSTN solar radio spectrographs (SRS, 25-180 MHz, 3 s): Learmonth (Australia),
  San Vito (Italy), Palehua (Hawaii), Sagamore Hill (Massachusetts) and
  Holloman (New Mexico, 2000-2004), from NOAA NCEI and, for Learmonth, the
  Australian Bureau of Meteorology
- the e-CALLISTO network of spectrometers (about 45-870 MHz, 0.25 s), from
  the FHNW archive

A time window is in UT. RSTN stations keep one file per local observing day,
which can start the evening before or end the morning after in UT; the
files that may hold the window are read and the window cut out of them.
"""

import gzip
import os
import re
import shutil
import socket
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from struct import unpack
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import interpolate
from scipy.signal import medfilt


class Cancelled(Exception):
    """The download was stopped."""


class ArchiveUnreachable(OSError):
    """The archive could not be asked (not: it has nothing)."""


def _say(progress, message):
    if progress:
        progress(message)


def _check(should_stop):
    if should_stop is not None and should_stop():
        raise Cancelled("Download cancelled")


# ============================================================================
# Network
# ============================================================================

_ipv4_preferred = None
HTTP_TIMEOUT_S = 30


def _prefer_ipv4():
    """Put IPv4 addresses first when IPv6 does not reach the archives (some
    networks hand out IPv6 addresses that do not route; each attempt then
    waits for a timeout)."""
    global _ipv4_preferred
    if _ipv4_preferred is not None:
        return
    _ipv4_preferred = False
    try:
        infos = socket.getaddrinfo("www.ngdc.noaa.gov", 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return
    v6 = [i for i in infos if i[0] == socket.AF_INET6]
    v4 = [i for i in infos if i[0] == socket.AF_INET]
    if not v6 or not v4:
        return

    def reaches(info):
        try:
            with socket.socket(info[0], socket.SOCK_STREAM) as s:
                s.settimeout(3)
                s.connect(info[4])
            return True
        except OSError:
            return False

    if reaches(v6[0]) or not reaches(v4[0]):
        return
    real = socket.getaddrinfo

    def ipv4_first(*args, **kwargs):
        return sorted(real(*args, **kwargs), key=lambda i: i[0] != socket.AF_INET)

    socket.getaddrinfo = ipv4_first
    _ipv4_preferred = True


_listings = {}


def _listing(url):
    """The file and folder names a directory page links to; [] when the
    archive has no such directory. Raises ArchiveUnreachable when it cannot
    be asked. Answers are kept for the session."""
    if url in _listings:
        return _listings[url]
    _prefer_ipv4()
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_S) as response:
            html = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            _listings[url] = []
            return []
        raise ArchiveUnreachable(f"{url}: HTTP {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        raise ArchiveUnreachable(f"{url}: {getattr(e, 'reason', e)}") from e
    names = [href.rstrip("/") for href in re.findall(r'href="([^"?/][^"?]*)"', html) if "://" not in href]
    _listings[url] = names
    return names


def fetch(url, path, should_stop=None, progress=None):
    """Download ``url`` to ``path``, unpacking a .gz on the way. The file
    appears only when complete (a part file until then), so a stopped or
    failed download never leaves something that looks finished."""
    _prefer_ipv4()
    part = path + ".part"
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_S) as response, open(part, "wb") as out:
            while True:
                _check(should_stop)
                chunk = response.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
        if url.endswith(".gz"):
            unpacked = part + ".unpacked"
            with gzip.open(part, "rb") as f_in, open(unpacked, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            os.replace(unpacked, part)
        os.replace(part, path)
    finally:
        for leftover in (part, part + ".unpacked"):
            if os.path.exists(leftover):
                os.remove(leftover)
    return path


# ============================================================================
# RSTN stations
# ============================================================================

NOAA_RSTN = "https://www.ngdc.noaa.gov/stp/space-weather/solar-data/solar-features/solar-radio/rstn-spectral"
BOM_LEARMONTH = "https://downloads.sws.bom.gov.au/wdc/wdc_spec/data/learmonth/raw"

# ``hours``: when the station typically observes, UT (a day's file runs
# from about local sunrise to sunset). ``span``: hours from 00:00 UT of a
# file's date between which all of that file's records fall (measured on
# summer and winter files), used to choose the files that may hold a window.
RSTN_SITES = {
    "Learmonth": {
        "id": 3, "code": "LEA", "folder": "learmonth", "place": "Western Australia",
        "first_year": 2000, "last_year": None, "hours": (22, 11), "span": (-4, 13), "bom": True,
    },
    "San Vito": {
        "id": 4, "code": "SVI", "folder": "san-vito", "place": "Italy",
        "first_year": 2000, "last_year": None, "hours": (5, 17), "span": (2, 20),
    },
    "Palehua": {
        "id": 1, "code": "PAL", "folder": "palehua", "place": "Hawaii, USA",
        "first_year": 2001, "last_year": None, "hours": (17, 4), "span": (14, 31),
    },
    "Sagamore Hill": {
        "id": 5, "code": "SAG", "folder": "sagamore-hill", "place": "Massachusetts, USA",
        "first_year": 2004, "last_year": None, "hours": (10, 0), "span": (7, 27),
    },
    "Holloman": {
        "id": 2, "code": "HOL", "folder": "holloman", "place": "New Mexico, USA",
        "first_year": 2000, "last_year": 2004, "hours": (12, 2), "span": (-14, 5),
    },
}

SITE_NAMES = {site["id"]: name for name, site in RSTN_SITES.items()}

RSTN_FREQ_RANGE = (25.0, 180.0)  # MHz
RSTN_CADENCE_S = 3.0
RSTN_FILE_MB = 12.0  # a day's file, unpacked

# Known bad frequency channels (as indices into the sorted 802 channels)
RSTN_BAD_CHANNELS = [
    (488, 499), (524, 533), (540, 550), (638, 642), (119, 129), (108, 111),
    (150, 160), (197, 199), (285, 289), (621, 632), (592, 600), (700, 712),
    (410, 416), (730, 741), (635, 645), (283, 292), (216, 222), (590, 602),
    (663, 667), (684, 690), (63, 66), (54, 59), (27, 31),
]


def rstn_observes(site, start, end):
    """True when [start, end] (UT) overlaps the station's usual observing
    hours on some day."""
    first, last = RSTN_SITES[site]["hours"]
    hour = start.replace(minute=0, second=0, microsecond=0)
    while hour <= end:
        h = hour.hour
        inside = first <= h < last if first < last else (h >= first or h < last)
        if inside:
            return True
        hour += timedelta(hours=1)
    return False


def rstn_days(site, start, end):
    """The file dates whose records may fall in [start, end]."""
    before, after = RSTN_SITES[site]["span"]
    day = (start - timedelta(hours=after)).date()
    days = []
    while datetime.combine(day, datetime.min.time()) + timedelta(hours=before) <= end:
        if datetime.combine(day, datetime.min.time()) + timedelta(hours=after) >= start:
            days.append(day)
        day += timedelta(days=1)
    return days


def _rstn_name_pattern(day, bom=False):
    stamp = day.strftime("%y%m%d")
    if bom:
        return re.compile(rf"^LM{stamp}\.srs$", re.IGNORECASE)
    # the prefix changed over the years (PA and kp at Palehua, k7 at
    # Sagamore Hill) and older files are in capitals
    return re.compile(rf"^[a-z0-9]{{2}}{stamp}\.srs(\.gz)?$", re.IGNORECASE)


def rstn_day_file(site, day):
    """(url, name) of the station's file for ``day``, None when the archive
    has none. Raises ArchiveUnreachable when no archive could be asked."""
    config = RSTN_SITES[site]
    unreachable = None
    if config.get("bom"):
        folder = f"{BOM_LEARMONTH}/{day:%y}/"
        try:
            pattern = _rstn_name_pattern(day, bom=True)
            for name in _listing(folder):
                if pattern.match(name):
                    return folder + name, name
        except ArchiveUnreachable as e:
            unreachable = e
    folder = f"{NOAA_RSTN}/{config['folder']}/{day:%Y}/{day:%m}/"
    try:
        pattern = _rstn_name_pattern(day)
        for name in _listing(folder):
            if pattern.match(name):
                return folder + name, name
    except ArchiveUnreachable:
        if unreachable is not None or not config.get("bom"):
            raise
    return None


def _latest_in(folder, pattern):
    """The latest date (YYMMDD in the first group of ``pattern``) among the
    names in ``folder``."""
    days = []
    for name in _listing(folder):
        m = pattern.match(name)
        if m:
            try:
                days.append(datetime.strptime(m.group(1), "%y%m%d").date())
            except ValueError:
                pass
    return max(days) if days else None


def rstn_latest(site):
    """The last day the archive holds a file for (the archives publish weeks
    after the observation), None when it has none."""
    config = RSTN_SITES[site]
    if config.get("bom"):
        try:
            years = sorted(n for n in _listing(f"{BOM_LEARMONTH}/") if re.fullmatch(r"\d{2}", n))
            for year in reversed(years):
                latest = _latest_in(f"{BOM_LEARMONTH}/{year}/", re.compile(r"^LM(\d{6})\.srs$", re.IGNORECASE))
                if latest:
                    return latest
        except ArchiveUnreachable:
            pass
    base = f"{NOAA_RSTN}/{config['folder']}/"
    pattern = re.compile(r"^[a-z0-9]{2}(\d{6})\.srs(?:\.gz)?$", re.IGNORECASE)
    for year in sorted((n for n in _listing(base) if re.fullmatch(r"\d{4}", n)), reverse=True):
        for month in sorted((n for n in _listing(f"{base}{year}/") if re.fullmatch(r"\d{2}", n)), reverse=True):
            latest = _latest_in(f"{base}{year}/{month}/", pattern)
            if latest:
                return latest
    return None


def find_rstn(site, start, end, whole_day=None):
    """The station's files for the window (or for ``whole_day``, a date):
    a list of dicts with day, url and name."""
    days = [whole_day] if whole_day is not None else rstn_days(site, start, end)
    found = []
    for day in days:
        hit = rstn_day_file(site, day)
        if hit:
            found.append({"day": day, "url": hit[0], "name": hit[1]})
    return found


# ============================================================================
# SRS files
# ============================================================================

RECORD_SIZE = 826
RECORD_HEADER_SIZE = 24
RECORD_ARRAY_SIZE = 401


def _full_year(year):
    return 2000 + year if year < 50 else 1900 + year if year < 100 else year


class SRSRecord:
    """Holds one 826 byte SRS Record."""

    def __init__(self):
        self.year = None
        self.month = None
        self.day = None
        self.hour = None
        self.minute = None
        self.seconds = None
        self.site_number = None
        self.site_name = None
        self.n_bands_per_record = None

        self.a_start_freq = None
        self.a_end_freq = None
        self.a_values = {}  # frequency -> level

        self.b_start_freq = None
        self.b_end_freq = None
        self.b_values = {}  # frequency -> level

    def _parse_header(self, header_bytes):
        """Parse the 24-byte record header."""
        fields = unpack(
            ">BBBBBBBB"  # Year, Month, Day, Hour, Minute, Second, Site, n_bands
            "hHHBB"  # A-band: start, end, n_bytes, ref_level, attenuation
            "HHHBB",  # B-band: start, end, n_bytes, ref_level, attenuation
            header_bytes,
        )
        (self.year, self.month, self.day, self.hour, self.minute, self.seconds,
         self.site_number, self.n_bands_per_record) = fields[:8]
        self.site_name = SITE_NAMES.get(self.site_number, "Unknown")
        self.a_start_freq = fields[8]
        self.a_end_freq = fields[9]
        self.b_start_freq = fields[13]
        self.b_end_freq = fields[14]

    def _parse_a_levels(self, a_bytes):
        """Parse the A-band (25-75 MHz) levels."""
        for i in range(401):
            self.a_values[25 + 50 * i / 400.0] = a_bytes[i]

    def _parse_b_levels(self, b_bytes):
        """Parse the B-band (75-180 MHz) levels."""
        for i in range(401):
            self.b_values[75 + 105 * i / 400.0] = b_bytes[i]

    def get_timestamp(self) -> datetime:
        """Get the timestamp for this record."""
        return datetime(_full_year(self.year), self.month, self.day, self.hour, self.minute, self.seconds)

    def __str__(self):
        return f"{self.day:02d}/{self.month:02d}/{self.year:02d}, {self.hour:02d}:{self.minute:02d}:{self.seconds:02d}"


def read_srs_file(fname: str) -> List[SRSRecord]:
    """Parse an SRS file and return a list of SRSRecord objects (slow; the
    downloader reads files with read_srs)."""
    srs_records = []
    with open(fname, "rb") as f:
        while True:
            record_data = f.read(RECORD_SIZE)
            if len(record_data) < RECORD_SIZE:
                break
            record = SRSRecord()
            record._parse_header(record_data[:RECORD_HEADER_SIZE])
            record._parse_a_levels(record_data[RECORD_HEADER_SIZE : RECORD_HEADER_SIZE + RECORD_ARRAY_SIZE])
            record._parse_b_levels(record_data[RECORD_HEADER_SIZE + RECORD_ARRAY_SIZE : RECORD_SIZE])
            srs_records.append(record)
    return srs_records


def read_srs(fname):
    """(times, freqs, levels) of an SRS file: datetime64[s] per record, the
    802 channel frequencies in MHz (A band 25-75, B band 75-180, as stored)
    and the levels, records x channels. Records with an impossible date are
    dropped."""
    raw = np.fromfile(fname, dtype=np.uint8)
    n = raw.size // RECORD_SIZE
    records = raw[: n * RECORD_SIZE].reshape(n, RECORD_SIZE)
    head = records[:, :6].astype(int)
    keep, times = [], []
    for i, (y, mo, d, h, mi, s) in enumerate(head):
        try:
            times.append(np.datetime64(datetime(_full_year(y), mo, d, h, mi, s), "s"))
            keep.append(i)
        except ValueError:
            continue
    freqs = np.concatenate([25 + 50 * np.arange(401) / 400.0, 75 + 105 * np.arange(401) / 400.0])
    levels = records[keep, RECORD_HEADER_SIZE:RECORD_SIZE]
    return np.array(times, dtype="datetime64[s]"), freqs, levels


def fill_nan(arr: np.ndarray) -> np.ndarray:
    """Interpolate to fill NaN values in array."""
    try:
        inds = np.arange(arr.shape[0])
        good = np.where(np.isfinite(arr))
        if len(good[0]) == 0:
            return arr
        f = interpolate.interp1d(inds[good], arr[good], bounds_error=False, kind="linear", fill_value="extrapolate")
        out_arr = np.where(np.isfinite(arr), arr, f(inds))
    except Exception:
        out_arr = arr
    return out_arr


def clean_rstn(times, freqs, levels, do_flag=True, flag_cal_time=True, bkg_sub=False, progress=None):
    """Sort an SRS day by time and frequency and clean it: flag the known
    bad channels and the calibration periods, fill the flagged samples
    along frequency, blank the top channels and, with ``bkg_sub``, divide
    each channel by its median. Returns (times, freqs, data)."""
    order_t = np.argsort(times, kind="stable")
    freqs = np.round(freqs, 1)
    order_f = np.argsort(freqs, kind="stable")
    times = times[order_t]
    freqs = freqs[order_f]
    data = levels[order_t][:, order_f].astype(float)

    if do_flag:
        for start, end in RSTN_BAD_CHANNELS:
            if start < data.shape[1] and end <= data.shape[1]:
                data[:, start:end] = np.nan
        if flag_cal_time and len(data) > 2:
            y = np.nanmedian(data, axis=1)
            c = y / medfilt(y, min(1001, len(y) // 2 * 2 + 1))
            c_std = np.nanstd(c)
            data[np.where(c > 1 + (10 * c_std)), :] = np.nan

    _say(progress, "Interpolating missing data...")
    for i in range(data.shape[0]):
        data[i, :] = fill_nan(data[i, :])

    if do_flag and data.shape[1] > 780:
        data[:, 780:] = np.nan

    if bkg_sub:
        _say(progress, "Dividing each channel by its median...")
        with np.errstate(all="ignore"):
            medians = np.nanmedian(data, axis=0)
        good = medians > 0
        data[:, good] = data[:, good] / medians[good]
    return times, freqs, data


def srs_to_dataframe(
    srs_file: str,
    bkg_sub: bool = False,
    do_flag: bool = True,
    flag_cal_time: bool = True,
    progress_callback=None,
) -> Tuple[Optional[pd.DataFrame], Optional[np.ndarray], Optional[np.ndarray]]:
    """An SRS file as a cleaned DataFrame (time x frequency), with its
    frequencies and timestamps."""
    _say(progress_callback, "Reading SRS file...")
    times, freqs, levels = read_srs(srs_file)
    if not len(times):
        return None, None, None
    _say(progress_callback, f"Read {len(times)} records")
    times, freqs, data = clean_rstn(times, freqs, levels, do_flag, flag_cal_time, bkg_sub, progress_callback)
    index = pd.to_datetime(times)
    return pd.DataFrame(data, index=index, columns=freqs), freqs, index


# ============================================================================
# FITS output
# ============================================================================


def write_spectrum(path, data, times, freqs, telescope, instrument, history, bunit="arbitrary", extra=None):
    """Write a dynamic spectrum as the Dynamic Spectrum Viewer reads it:
    ``data`` time x frequency in the primary HDU, the times (UTC) in a
    TIME_AXIS table as MJD and the frequencies (MHz) in FREQ_AXIS."""
    from astropy.io import fits
    from astropy.table import Table
    from astropy.time import Time

    times = np.asarray(times, dtype="datetime64[ms]")
    t = Time(times.astype("datetime64[ms]").astype(str), format="isot", scale="utc")
    hdu = fits.PrimaryHDU(np.asarray(data, dtype=np.float32))
    header = hdu.header
    header["TELESCOP"] = telescope
    header["INSTRUME"] = instrument
    header["OBJECT"] = "Sun"
    header["BUNIT"] = bunit
    header["DATE-OBS"] = t[0].isot
    header["DATE-END"] = t[-1].isot
    header["TIMESYS"] = "UTC"
    header["CTYPE1"] = "FREQ"
    header["CUNIT1"] = "MHz"
    header["CRPIX1"] = 1
    header["CRVAL1"] = float(freqs[0])
    header["CDELT1"] = float(np.median(np.diff(freqs))) if len(freqs) > 1 else 1.0
    header["CTYPE2"] = "TIME"
    header["CUNIT2"] = "s"
    header["CRPIX2"] = 1
    header["CRVAL2"] = 0.0
    seconds = np.diff(times).astype("timedelta64[ms]").astype(float) / 1000.0
    header["CDELT2"] = float(np.median(seconds)) if len(seconds) else 1.0
    header["FREQ_MIN"] = float(np.nanmin(freqs))
    header["FREQ_MAX"] = float(np.nanmax(freqs))
    for key, value in (extra or {}).items():
        header[key] = value
    header["HISTORY"] = "Created by SolarViewer"
    for line in history if isinstance(history, (list, tuple)) else [history]:
        header["HISTORY"] = line
    time_table = Table()
    time_table["TIME_MJD"] = t.mjd
    freq_table = Table()
    freq_table["FREQ_MHz"] = np.asarray(freqs, dtype=np.float64)
    hdul = fits.HDUList(
        [hdu, fits.BinTableHDU(time_table, name="TIME_AXIS"), fits.BinTableHDU(freq_table, name="FREQ_AXIS")]
    )
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    hdul.writeto(path, overwrite=True)
    return path


def dataframe_to_fits(
    df: pd.DataFrame,
    freqs: np.ndarray,
    timestamps: pd.DatetimeIndex,
    output_file: str,
    site_name: str = "Learmonth",
    progress_callback=None,
) -> Optional[str]:
    """Write a DataFrame (time x frequency) as a dynamic spectrum FITS file."""
    _say(progress_callback, "Creating FITS file...")
    write_spectrum(
        output_file, df.to_numpy(), np.asarray(timestamps, dtype="datetime64[ms]"), np.asarray(freqs),
        site_name, f"{site_name} Spectrograph", f"Source: {site_name} Solar Spectrograph",
    )
    _say(progress_callback, f"FITS file created: {output_file}")
    return output_file


# ============================================================================
# RSTN: window to spectrum
# ============================================================================


def rstn_output_name(site, start, end, whole_day=None):
    slug = site.lower().replace(" ", "-")
    if whole_day is not None:
        return f"{slug}_{whole_day:%Y%m%d}_day_spectrum.fits"
    tail = f"{end:%H%M%S}" if end.date() == start.date() else f"{end:%Y%m%d_%H%M%S}"
    return f"{slug}_{start:%Y%m%d_%H%M%S}-{tail}_spectrum.fits"


def rstn_spectrum(
    site, start, end, output_dir, whole_day=None, do_flag=True, flag_cal_time=True, bkg_sub=False,
    keep_raw=False, progress=None, counts=None, should_stop=None,
):
    """Download the station's files for [start, end] (UT) or for the
    observing day ``whole_day`` (a date), clean them and write one dynamic
    spectrum. Returns the FITS path, or None when there is no data (the
    reason is said through ``progress``). ``counts(fetched, processed,
    total)`` follows the files."""
    if site not in RSTN_SITES:
        raise ValueError(f"Unknown RSTN station: {site}")
    _say(progress, f"Asking the archive for {site}'s files...")
    files = find_rstn(site, start, end, whole_day)
    if not files:
        _say(progress, f"The archive has no {site} file for this window")
        return None
    total = len(files)
    _say(progress, f"Found {total} file{'s' if total > 1 else ''}: {', '.join(f['name'] for f in files)}")
    os.makedirs(output_dir, exist_ok=True)
    pieces, raw_paths = [], []
    for i, item in enumerate(files):
        _check(should_stop)
        raw = os.path.join(output_dir, re.sub(r"\.gz$", "", item["name"], flags=re.IGNORECASE))
        if not os.path.exists(raw):
            _say(progress, f"Downloading {item['name']}")
            fetch(item["url"], raw, should_stop)
        raw_paths.append(raw)
        if counts:
            counts(i + 1, i, total)
        times, freqs, levels = read_srs(raw)
        if not len(times):
            _say(progress, f"{item['name']} holds no records")
            continue
        _say(progress, f"Cleaning {item['name']} ({len(times)} records, {times[0]} to {times[-1]})")
        times, freqs, data = clean_rstn(times, freqs, levels, do_flag, flag_cal_time, bkg_sub)
        if whole_day is None:
            inside = (times >= np.datetime64(start, "s")) & (times <= np.datetime64(end, "s"))
            times, data = times[inside], data[inside]
        if len(times):
            pieces.append((times, freqs, data))
        if counts:
            counts(i + 1, i + 1, total)
    if not pieces:
        first, last = RSTN_SITES[site]["hours"]
        _say(progress, f"No {site} records in this window ({site} observes about {first:02d}-{last:02d} UT)")
        if not keep_raw:
            _remove(raw_paths)
        return None
    times = np.concatenate([p[0] for p in pieces])
    data = np.concatenate([p[2] for p in pieces])
    order = np.argsort(times, kind="stable")
    times, data = times[order], data[order]
    unique = np.concatenate([[True], np.diff(times.astype("int64")) > 0])
    times, data = times[unique], data[unique]
    path = os.path.join(output_dir, rstn_output_name(site, start, end, whole_day))
    steps = ["flagged bad channels" if do_flag else None,
             "flagged calibration periods" if do_flag and flag_cal_time else None,
             "each channel divided by its median" if bkg_sub else None]
    history = [f"Source: RSTN {site} Solar Radio Spectrograph ({', '.join(f['name'] for f in files)})"]
    if any(steps):
        history.append("Processing: " + ", ".join(s for s in steps if s))
    write_spectrum(path, data, times, pieces[0][1], site, f"{site} Spectrograph", history,
                   bunit="relative" if bkg_sub else "arbitrary")
    _say(progress, f"Wrote {os.path.basename(path)} ({len(times)} times x {data.shape[1]} channels)")
    if not keep_raw:
        _remove(raw_paths)
    return path


def _remove(paths):
    for path in paths:
        try:
            os.remove(path)
        except OSError:
            pass


def download_rstn_data(site: str, date: str, output_dir: str = ".", progress_callback=None) -> Optional[str]:
    """Download the station's SRS file for ``date`` ('YYYY-MM-DD'); its
    path, or None."""
    try:
        day = datetime.strptime(date, "%Y-%m-%d").date()
        hit = rstn_day_file(site, day)
    except (ValueError, KeyError, ArchiveUnreachable) as e:
        _say(progress_callback, f"Download failed: {e}")
        return None
    if hit is None:
        _say(progress_callback, f"No {site} file for {date}")
        return None
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, re.sub(r"\.gz$", "", hit[1], flags=re.IGNORECASE))
    if not os.path.exists(path):
        fetch(hit[0], path)
    _say(progress_callback, f"Downloaded: {os.path.basename(path)}")
    return path


def download_learmonth(date: str, output_dir: str = ".", progress_callback=None) -> Optional[str]:
    """Download Learmonth's SRS file for ``date``."""
    return download_rstn_data("Learmonth", date, output_dir, progress_callback)


def download_and_convert_rstn(
    site: str,
    date: str,
    output_dir: str = ".",
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    bkg_sub: bool = False,
    do_flag: bool = True,
    flag_cal_time: bool = True,
    progress_callback=None,
) -> Optional[str]:
    """The station's spectrum for ``date``: its whole observing day, or
    from ``start_time`` to ``end_time`` ('HH:MM:SS', UT on that date)."""
    day = datetime.strptime(date, "%Y-%m-%d").date()
    if not start_time and not end_time:
        start = datetime.combine(day, datetime.min.time())
        return rstn_spectrum(site, start, start + timedelta(days=1), output_dir, whole_day=day,
                             do_flag=do_flag, flag_cal_time=flag_cal_time, bkg_sub=bkg_sub, progress=progress_callback)
    start = datetime.strptime(f"{date} {start_time or '00:00:00'}", "%Y-%m-%d %H:%M:%S")
    end = datetime.strptime(f"{date} {end_time or '23:59:59'}", "%Y-%m-%d %H:%M:%S")
    if end < start:
        end += timedelta(days=1)
    return rstn_spectrum(site, start, end, output_dir, do_flag=do_flag, flag_cal_time=flag_cal_time,
                         bkg_sub=bkg_sub, progress=progress_callback)


def download_and_convert_learmonth(date, output_dir=".", start_time=None, end_time=None, bkg_sub=False,
                                   do_flag=True, flag_cal_time=True, progress_callback=None):
    """Learmonth's spectrum for ``date`` (see download_and_convert_rstn)."""
    return download_and_convert_rstn("Learmonth", date, output_dir, start_time, end_time, bkg_sub, do_flag,
                                     flag_cal_time, progress_callback)


# ============================================================================
# e-CALLISTO
# ============================================================================

# The FHNW archive (the same as on soleil80.cs.technik.fhnw.ch, whose
# certificate does not name it)
ECALLISTO_BASE_URL = "https://soleil.i4ds.ch/solarradio/data/2002-20yy_Callisto"
ECALLISTO_FIRST_YEAR = 2002
ECALLISTO_FILE_SECONDS = 900  # each file holds 15 minutes
ECALLISTO_FILE_MB = 0.2  # packed, as downloaded
ECALLISTO_CADENCE_S = 0.25
ECALLISTO_FILE = re.compile(
    r"^(?P<station>.+?)_(?P<date>\d{8})_(?P<time>\d{6})_(?P<focus>[0-9A-Za-z]+)\.fit(?:\.gz)?$", re.IGNORECASE
)


def ecallisto_day(day):
    """Every file the archive holds for ``day``: dicts with name, url,
    station, focus (the receiver's focus code) and start."""
    folder = f"{ECALLISTO_BASE_URL}/{day:%Y}/{day:%m}/{day:%d}/"
    files = []
    for name in _listing(folder):
        m = ECALLISTO_FILE.match(name)
        if not m:
            continue
        try:
            start = datetime.strptime(m.group("date") + m.group("time"), "%Y%m%d%H%M%S")
        except ValueError:
            continue
        files.append({"name": name, "url": folder + name, "station": m.group("station"),
                      "focus": m.group("focus"), "start": start})
    return files


def ecallisto_files(start, end, station=None):
    """The files holding part of [start, end] (UT), of ``station`` or of all
    stations, in time order."""
    day = (start - timedelta(seconds=ECALLISTO_FILE_SECONDS)).date()
    found = []
    while day <= end.date():
        for item in ecallisto_day(day):
            if station and item["station"].upper() != station.upper():
                continue
            if item["start"] <= end and item["start"] + timedelta(seconds=ECALLISTO_FILE_SECONDS) > start:
                found.append(item)
        day += timedelta(days=1)
    return sorted(found, key=lambda f: (f["station"].upper(), f["focus"], f["start"]))


def ecallisto_stations(start, end):
    """The stations with files in [start, end]: {station: {"files": n,
    "receivers": [focus codes]}}."""
    stations = {}
    for item in ecallisto_files(start, end):
        entry = stations.setdefault(item["station"], {"files": 0, "receivers": set()})
        entry["files"] += 1
        entry["receivers"].add(item["focus"])
    return {name: {"files": e["files"], "receivers": sorted(e["receivers"])} for name, e in stations.items()}


def fetch_ecallisto_observatories(date: str, progress_callback=None) -> List[str]:
    """The stations with files on ``date`` ('YYYY-MM-DD')."""
    try:
        day = datetime.strptime(date, "%Y-%m-%d").date()
        return sorted({f["station"] for f in ecallisto_day(day)})
    except (ValueError, ArchiveUnreachable) as e:
        _say(progress_callback, f"Error: {e}")
        return []


def read_callisto(path):
    """(times, freqs, data, header) of an e-CALLISTO file: datetime64[ms]
    per sample, frequencies in MHz ascending, data time x frequency (raw
    digits)."""
    from astropy.io import fits

    with fits.open(path) as hdul:
        header = hdul[0].header.copy()
        data = np.asarray(hdul[0].data)
        table = hdul[1].data if len(hdul) > 1 and hdul[1].data is not None else None
        names = [n.upper() for n in hdul[1].columns.names] if table is not None else []
        if "TIME" in names:
            seconds = np.asarray(table[hdul[1].columns.names[names.index("TIME")]]).ravel().astype(float)
        else:
            seconds = np.arange(data.shape[1]) * float(header.get("CDELT1", ECALLISTO_CADENCE_S))
        if "FREQUENCY" in names:
            freqs = np.asarray(table[hdul[1].columns.names[names.index("FREQUENCY")]]).ravel().astype(float)
        else:
            freqs = float(header.get("CRVAL2", 45.0)) + np.arange(data.shape[0]) * float(header.get("CDELT2", 1.0))
    date_obs = str(header.get("DATE-OBS", "")).replace("/", "-")
    time_obs = str(header.get("TIME-OBS", "00:00:00"))
    start = np.datetime64(f"{date_obs}T{time_obs}", "ms")
    times = start + (seconds * 1000).astype("int64").astype("timedelta64[ms]")
    order = np.argsort(freqs, kind="stable")
    return times, freqs[order], data[order].T, header


def ecallisto_output_name(station, focus, start, end):
    tail = f"{end:%H%M%S}" if end.date() == start.date() else f"{end:%Y%m%d_%H%M%S}"
    return f"{station}_{focus}_{start:%Y%m%d_%H%M%S}-{tail}_spectrum.fits"


def _ecallisto_write(output_dir, station, focus, times, freqs, data, subtract_background, names):
    data = data.astype(np.float32)
    if subtract_background:
        with np.errstate(all="ignore"):
            data = data - np.nanmedian(data, axis=0)
    start = times[0].astype("datetime64[s]").astype(datetime)
    end = times[-1].astype("datetime64[s]").astype(datetime)
    path = os.path.join(output_dir, ecallisto_output_name(station, focus, start, end))
    history = [f"Source: e-CALLISTO {station} focus code {focus}"]
    history += [f"File: {name}" for name in names[:20]]
    if subtract_background:
        history.append("Processing: each channel's median subtracted (constant background)")
    return write_spectrum(path, data, times, freqs, station, "e-CALLISTO", history, bunit="digits",
                          extra={"FOCUSCOD": focus})


def ecallisto_spectra(
    start, end, station, output_dir, join=True, crop=True, subtract_background=False, keep_raw=False,
    progress=None, counts=None, should_stop=None,
):
    """Download ``station``'s files (every station's when None) for
    [start, end] (UT) and write dynamic spectra: with ``join``, one per
    receiver (focus code) joining its 15 min files, cut to the window with
    ``crop``; otherwise one per file. Returns the FITS paths."""
    label = station or "every station"
    _say(progress, f"Asking the archive for {label}'s files...")
    files = ecallisto_files(start, end, station)
    if not files:
        _say(progress, f"The archive has no {label} files in this window")
        return []
    total = len(files)
    _say(progress, f"Found {total} files")
    os.makedirs(output_dir, exist_ok=True)
    groups = {}
    raw_paths = []
    fetched = 0
    for item in files:
        _check(should_stop)
        raw = os.path.join(output_dir, item["name"])
        try:
            if not os.path.exists(raw):
                fetch(item["url"], raw, should_stop)
        except Cancelled:
            raise
        except Exception as e:
            _say(progress, f"Could not download {item['name']}: {e}")
            continue
        fetched += 1
        raw_paths.append(raw)
        if counts:
            counts(fetched, 0, total)
        _say(progress, f"Downloaded {item['name']}")
        groups.setdefault((item["station"], item["focus"]), []).append((item, raw))

    outputs = []
    processed = 0
    lo, hi = np.datetime64(start, "ms"), np.datetime64(end, "ms")
    for (stn, focus), members in groups.items():
        run = None  # (times, freqs, data, names) being joined
        for item, raw in members:
            _check(should_stop)
            try:
                times, freqs, data, _ = read_callisto(raw)
            except Exception as e:
                _say(progress, f"Could not read {item['name']}: {e}")
                continue
            if crop:
                inside = (times >= lo) & (times <= hi)
                times, data = times[inside], data[inside]
            processed += 1
            if counts:
                counts(fetched, processed, total)
            if not len(times):
                continue
            if not join:
                outputs.append(_ecallisto_write(output_dir, stn, focus, times, freqs, data, subtract_background,
                                                [item["name"]]))
                _say(progress, f"Wrote {os.path.basename(outputs[-1])}")
                continue
            if run is not None and (run[1].shape != freqs.shape or not np.allclose(run[1], freqs)):
                # the receiver changed its channels: start a new spectrum
                outputs.append(_ecallisto_write(output_dir, stn, focus, *run[:3], subtract_background, run[3]))
                _say(progress, f"Wrote {os.path.basename(outputs[-1])}")
                run = None
            if run is None:
                run = (times, freqs, data, [item["name"]])
            else:
                run = (np.concatenate([run[0], times]), run[1], np.concatenate([run[2], data]), run[3] + [item["name"]])
        if run is not None:
            order = np.argsort(run[0], kind="stable")
            outputs.append(_ecallisto_write(output_dir, stn, focus, run[0][order], run[1], run[2][order],
                                            subtract_background, run[3]))
            _say(progress, f"Wrote {os.path.basename(outputs[-1])}")
    if not keep_raw:
        _remove(raw_paths)
    if not outputs:
        _say(progress, "No e-CALLISTO samples fall in this window")
    return outputs


def download_ecallisto_data(start_time: str, end_time: str, observatory: Optional[str] = None,
                            output_dir: str = ".", progress_callback=None) -> List[str]:
    """Download the e-CALLISTO files (as the archive keeps them) holding
    part of the window ('YYYY-MM-DD HH:MM:SS', UT)."""
    start = datetime.strptime(start_time.replace("T", " "), "%Y-%m-%d %H:%M:%S")
    end = datetime.strptime(end_time.replace("T", " "), "%Y-%m-%d %H:%M:%S")
    station = observatory if observatory and observatory != "All Observatories" else None
    os.makedirs(output_dir, exist_ok=True)
    paths = []
    for item in ecallisto_files(start, end, station):
        path = os.path.join(output_dir, item["name"])
        if not os.path.exists(path):
            fetch(item["url"], path)
        _say(progress_callback, f"Downloaded {item['name']}")
        paths.append(path)
    return paths


def ecallisto_to_fits(input_file: str, output_file: Optional[str] = None, progress_callback=None) -> Optional[str]:
    """Convert one e-CALLISTO file to a dynamic spectrum FITS file."""
    try:
        times, freqs, data, header = read_callisto(input_file)
    except Exception as e:
        _say(progress_callback, f"Error converting e-CALLISTO file: {e}")
        return None
    if output_file is None:
        base = re.sub(r"\.fit(\.gz)?$", "", os.path.basename(input_file), flags=re.IGNORECASE)
        output_file = os.path.join(os.path.dirname(input_file), f"{base}_spectrum.fits")
    station = str(header.get("INSTRUME", "e-CALLISTO"))
    write_spectrum(output_file, data, times, freqs, station, "e-CALLISTO",
                   f"Source: e-CALLISTO {os.path.basename(input_file)}", bunit="digits")
    _say(progress_callback, f"FITS file created: {output_file}")
    return output_file


def download_and_convert_ecallisto(start_time: str, end_time: str, observatory: Optional[str] = None,
                                   output_dir: str = ".", progress_callback=None) -> List[str]:
    """One dynamic spectrum per e-CALLISTO file in the window."""
    start = datetime.strptime(start_time.replace("T", " "), "%Y-%m-%d %H:%M:%S")
    end = datetime.strptime(end_time.replace("T", " "), "%Y-%m-%d %H:%M:%S")
    station = observatory if observatory and observatory != "All Observatories" else None
    return ecallisto_spectra(start, end, station, output_dir, join=False, crop=False, progress=progress_callback)


# ============================================================================
# Bursts reported on a day
# ============================================================================


def _hhmm(day, text, after=None):
    """``text`` (HHMM or HH:MM, maybe B-prefixed) on ``day`` as a datetime;
    past midnight when it is before ``after``."""
    text = text.lstrip("BEU").replace(":", "")
    if not re.fullmatch(r"\d{4}", text):
        return None
    hour, minute = int(text[:2]), int(text[2:])
    if hour > 23 or minute > 59:
        return None
    t = datetime.combine(day, datetime.min.time()) + timedelta(hours=hour, minutes=minute)
    if after is not None and t < after:
        t += timedelta(days=1)
    return t


def radio_bursts(day):
    """The radio bursts reported on ``day``: dicts with start, end (UT
    datetimes), type ("III", "II"...), stations (e-CALLISTO station names
    or RSTN station names) and source ("e-CALLISTO" or "NOAA"). Lists that
    cannot be fetched give nothing."""
    from ..noaa_events import noaa_events

    bursts = []
    try:
        for b in noaa_events.fetch_and_parse_ecallisto_bursts(day):
            start = _hhmm(day, b.begin_time)
            end = _hhmm(day, b.end_time, after=start)
            if start is None:
                continue
            stations = [s.strip().strip("()") for s in b.stations_list if s.strip().strip("()")]
            bursts.append({"start": start, "end": end or start, "type": b.burst_type,
                           "stations": stations, "source": "e-CALLISTO"})
    except Exception:
        pass
    try:
        names = {site["code"]: name for name, site in RSTN_SITES.items()}
        for e in noaa_events.fetch_and_parse_events(day) or []:
            if e.event_type != "RSP" or e.observatory not in names or not e.begin_time:
                continue
            start = _hhmm(day, e.begin_time)
            end = _hhmm(day, e.end_time or e.begin_time, after=start)
            if start is None:
                continue
            kind = e.particulars.split("/")[0].split()[0] if e.particulars else "?"
            bursts.append({"start": start, "end": end or start, "type": kind,
                           "stations": [names[e.observatory]], "source": "NOAA"})
    except Exception:
        pass
    return sorted(bursts, key=lambda b: b["start"])


if __name__ == "__main__":
    import sys

    day = sys.argv[1] if len(sys.argv) > 1 else "2024-01-15"
    print(f"Learmonth's spectrum for {day}...")
    result = download_and_convert_learmonth(day, output_dir="./learmonth_data", progress_callback=lambda m: print("  " + m))
    print(f"FITS file: {result}" if result else "No data")

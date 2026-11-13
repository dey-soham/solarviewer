#!/usr/bin/env python3
"""
OVRO-LWA's solar dynamic spectrum (the Long Wavelength Array at Owens
Valley, California): 15-85 MHz in 731 channels about every 0.26 s, total
intensity, one FITS file per observing day on the project's public
storage.

A day's file is 1 to 2 GB, so it is not downloaded: the samples a window
asks for are read out of it by byte range, channel by channel (the file
keeps each channel's samples together), and written as a dynamic spectrum
FITS file (radio_data_downloader.write_spectrum). The values and their
unit are the file's own.
"""

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import NamedTuple

import numpy as np

from . import radio_data_downloader as rdd
from .radio_data_downloader import ArchiveUnreachable, Cancelled, _check, _say

LWA_FITS = "https://ovro-lwa-solar.s3.us-west-2.amazonaws.com/spec_fits/{day:%Y}/{day:%Y%m%d}.fits"
LWA_FIRST = date(2023, 5, 2)  # its first daily spectrum
LWA_HOURS = (14, 2)  # when it typically observes, UT (local sunrise to sunset)
LWA_SPAN = (12, 28)  # hours from 00:00 UT of a file's date between which all of its samples fall
LWA_CHANNELS = 731
BLOCK = 2880
READERS = 12  # connections reading channels at once
TRIES = 3
TIMEOUT_S = 60

_local = threading.local()


class DayFile(NamedTuple):
    day: date
    url: str
    name: str
    samples: int
    channels: int
    width: int  # bytes per value
    data_start: int  # where the first plane (total intensity) begins
    tables_start: int  # where the tables after the data begin
    start: datetime  # of the observation, as the header gives it
    end: datetime
    unit: str


def read_range(url, first, last):
    """Bytes ``first`` to ``last`` of ``url``; None when the archive has no
    such file. Raises ArchiveUnreachable when it cannot be asked (or does
    not send the part asked for)."""
    import requests

    session = getattr(_local, "session", None)
    if session is None:
        session = _local.session = requests.Session()  # one connection per thread, kept open
    rdd._prefer_ipv4()
    try:
        with session.get(url, headers={"Range": f"bytes={first}-{last}"}, timeout=TIMEOUT_S, stream=True) as response:
            if response.status_code == 404:
                return None
            if response.status_code != 206:  # (200 would be the whole file)
                raise ArchiveUnreachable(f"{url}: HTTP {response.status_code}")
            data = response.content
    except requests.RequestException as e:
        raise ArchiveUnreachable(f"{url}: {e}") from e
    if len(data) != last - first + 1:
        raise ArchiveUnreachable(f"{url}: {len(data)} of {last - first + 1} bytes came")
    return data


def _header(url, offset):
    """(header, bytes it takes) of the FITS header at ``offset``; None when
    there is no such file."""
    from astropy.io import fits

    raw = b""
    for _block in range(16):
        more = read_range(url, offset + len(raw), offset + len(raw) + BLOCK - 1)
        if more is None:
            return None
        raw += more
        cards = [raw[i:i + 80] for i in range(len(raw) - BLOCK, len(raw), 80)]
        if any(card.rstrip() == b"END" for card in cards):
            return fits.Header.fromstring(raw.decode("ascii", "replace")), len(raw)
    raise ArchiveUnreachable(f"{url}: not a FITS file as OVRO-LWA's are")


def _in_blocks(size):
    return -(-size // BLOCK) * BLOCK


def _when(text):
    return datetime.fromisoformat(str(text).strip()[:19])


def day_file(day):
    """What the archive's file of the observing day ``day`` holds, from its
    header; None when it has none. Raises ArchiveUnreachable when it cannot
    be asked."""
    url = LWA_FITS.format(day=day)
    found = _header(url, 0)
    if found is None:
        return None
    header, length = found
    try:
        samples, channels = int(header["NAXIS1"]), int(header["NAXIS2"])
        planes = int(header.get("NAXIS3", 1)) * int(header.get("NAXIS4", 1))
        width = abs(int(header["BITPIX"])) // 8
        start, end = _when(header["DATE_OBS"]), _when(header["DATE_END"])
    except (KeyError, ValueError) as e:
        raise ArchiveUnreachable(f"{url}: not a FITS file as OVRO-LWA's are ({e})") from e
    if int(header["BITPIX"]) not in (-32, -64):
        raise ArchiveUnreachable(f"{url}: not a FITS file as OVRO-LWA's are (BITPIX {header['BITPIX']})")
    return DayFile(day, url, os.path.basename(url), samples, channels, width, length,
                   length + _in_blocks(width * samples * channels * planes), start, end,
                   str(header.get("BUNIT", "")).strip())


def day_files(start, end, whole_day=None):
    """The day files holding part of [start, end] (UT), or the file of the
    observing day ``whole_day``. A file's date is its local day: it runs
    into the next UT day."""
    if whole_day is not None:
        days = [whole_day]
    else:
        before, after = LWA_SPAN
        day = (start - timedelta(hours=after)).date()
        days = []
        while datetime.combine(day, datetime.min.time()) + timedelta(hours=before) <= end:
            if datetime.combine(day, datetime.min.time()) + timedelta(hours=after) >= start:
                days.append(day)
            day += timedelta(days=1)
    found = []
    for day in days:
        if day < LWA_FIRST:
            continue
        item = day_file(day)
        if item and (whole_day is not None or (item.start <= end and item.end >= start)):
            found.append(item)
    return found


def samples_in(item, start, end):
    """About how many of the file's samples fall in [start, end] (from its
    header alone: its gaps are not known before its times are read)."""
    length = (item.end - item.start).total_seconds()
    if length <= 0:
        return 0
    inside = (min(end, item.end) - max(start, item.start)).total_seconds()
    return int(round(item.samples * max(0.0, min(1.0, inside / length))))


def lwa_observes(start, end):
    """True when [start, end] (UT) overlaps the array's usual observing
    hours on some day."""
    first, last = LWA_HOURS
    hour = start.replace(minute=0, second=0, microsecond=0)
    while hour <= end:
        if hour.hour >= first or hour.hour < last:
            return True
        hour += timedelta(hours=1)
    return False


def latest_day(today=None):
    """The last day the archive holds a file for, looking back two weeks
    from ``today``; None when none of them is there."""
    today = today or datetime.utcnow().date()
    for back in range(15):
        day = today - timedelta(days=back)
        if day >= LWA_FIRST and _header(LWA_FITS.format(day=day), 0) is not None:
            return day
    return None


def read_axes(item):
    """(times, freqs) of a day file, from the tables after its data:
    datetime64[ms] UTC per sample and MHz per channel."""
    offset = item.tables_start
    times = freqs = None
    for _table in range(6):
        found = _header(item.url, offset)
        if found is None:
            break
        header, length = found
        row, rows = int(header["NAXIS1"]), int(header["NAXIS2"])
        name = str(header.get("EXTNAME", "")).strip().upper()
        if name == "SFREQ" and (row, rows) == (4, item.channels):
            freqs = np.frombuffer(read_range(item.url, offset + length, offset + length + row * rows - 1), ">f4")
            freqs = freqs.astype(np.float64) * 1e3  # GHz in the file
        elif name == "UT" and (row, rows) == (8, item.samples):
            stamps = np.frombuffer(read_range(item.url, offset + length, offset + length + row * rows - 1), ">i4")
            days, milliseconds = stamps[::2].astype("int64"), stamps[1::2].astype("int64")  # MJD and ms of the day
            times = np.datetime64("1858-11-17T00:00:00", "ms") + (days * 86400000 + milliseconds).astype("timedelta64[ms]")
        if times is not None and freqs is not None:
            return times, freqs
        offset += length + _in_blocks(row * rows)
    raise ArchiveUnreachable(f"{item.url}: its tables of times and frequencies are not as OVRO-LWA's are")


def read_samples(item, first, last, progress=None, should_stop=None):
    """Samples ``first`` to ``last`` (exclusive) of every channel of the
    file's total intensity, time x channel, as float32. ``progress(done)``
    follows the channels read."""
    count = last - first
    data = np.empty((count, item.channels), dtype=np.float32)
    dtype = {4: ">f4", 8: ">f8"}[item.width]

    def channel(index):
        offset = item.data_start + (index * item.samples + first) * item.width
        error = None
        for _attempt in range(TRIES):
            if should_stop is not None and should_stop():
                raise Cancelled("Download cancelled")
            try:
                raw = read_range(item.url, offset, offset + count * item.width - 1)
            except ArchiveUnreachable as e:
                error = e
                continue
            if raw is None:
                raise ArchiveUnreachable(f"{item.url}: the file is not there any more")
            data[:, index] = np.frombuffer(raw, dtype)
            return index
        raise error

    pool = ThreadPoolExecutor(max_workers=READERS)
    futures = [pool.submit(channel, index) for index in range(item.channels)]
    done = 0
    try:
        for future in as_completed(futures):
            future.result()
            done += 1
            if progress:
                progress(done)
            _check(should_stop)
    finally:
        for future in futures:
            future.cancel()
        pool.shutdown(wait=True)
    return data


def output_name(start, end, whole_day=None):
    if whole_day is not None:
        return f"ovro-lwa_{whole_day:%Y%m%d}_day_spectrum.fits"
    tail = f"{end:%H%M%S}" if end.date() == start.date() else f"{end:%Y%m%d_%H%M%S}"
    return f"ovro-lwa_{start:%Y%m%d_%H%M%S}-{tail}_spectrum.fits"


def lwa_spectrum(start, end, output_dir, whole_day=None, progress=None, counts=None, should_stop=None):
    """Read OVRO-LWA's samples in [start, end] (UT), or its whole observing
    day ``whole_day`` (a date), and write them as dynamic spectra: one, or
    one per day file when their channels differ. Returns the FITS paths;
    none when there is no data, and the reason is said through
    ``progress``. ``counts(read, written, total)`` follows the channels."""
    _say(progress, "Asking the archive for OVRO-LWA's files…")
    files = day_files(start, end, whole_day)
    if not files:
        _say(progress, "The archive has no OVRO-LWA file for this window")
        return []
    _say(progress, f"Found {len(files)} file{'s' if len(files) > 1 else ''}: {', '.join(f.name for f in files)}")
    total = sum(f.channels for f in files)
    lo, hi = np.datetime64(start, "ms"), np.datetime64(end, "ms")
    pieces, read = [], 0
    for item in files:
        _check(should_stop)
        _say(progress, f"Reading the times of {item.name}")
        times, freqs = read_axes(item)
        if whole_day is not None:
            first, last = 0, item.samples
        else:
            first, last = int(np.searchsorted(times, lo, "left")), int(np.searchsorted(times, hi, "right"))
        if last <= first:
            _say(progress, f"{item.name} has no samples in this window ({times[0]} to {times[-1]})")
            read += item.channels
            continue
        megabytes = (last - first) * item.channels * item.width / 1e6
        _say(progress, f"Reading {last - first} samples of {item.channels} channels from {item.name} ({megabytes:.0f} MB)")
        base = read
        data = read_samples(
            item, first, last, should_stop=should_stop,
            progress=(lambda done, base=base: counts(base + done, 0, total)) if counts else None,
        )
        read += item.channels
        order = np.argsort(freqs, kind="stable")
        pieces.append((item, times[first:last], freqs[order], data[:, order]))
        _say(progress, f"Downloaded {item.name} ({last - first} samples)")
    if not pieces:
        return []
    same = all(len(p[2]) == len(pieces[0][2]) and np.allclose(p[2], pieces[0][2]) for p in pieces)
    groups = [pieces] if same else [[p] for p in pieces]
    os.makedirs(output_dir, exist_ok=True)
    outputs = []
    for group in groups:
        times = np.concatenate([p[1] for p in group])
        data = np.concatenate([p[3] for p in group]) if len(group) > 1 else group[0][3]
        first = group[0][0]
        if len(groups) > 1:  # each day its own file
            name = output_name(start, end, first.day)
        else:
            name = output_name(start, end, whole_day)
        path = os.path.join(output_dir, name)
        history = [
            f"Source: OVRO-LWA daily spectrum ({', '.join(p[0].name for p in group)}), total intensity",
            "Read by byte range from the project's storage; values and unit as in the file",
        ]
        rdd.write_spectrum(path, data, times, group[0][2], "OVRO-LWA", "OVRO-LWA", history,
                           bunit=first.unit or "arbitrary", extra={"ORIGIN": "OVRO-LWA", "POLARIZA": "I"})
        outputs.append(path)
        _say(progress, f"Wrote {os.path.basename(path)} ({len(times)} times x {data.shape[1]} channels)")
    if counts:
        counts(total, total, total)
    return outputs

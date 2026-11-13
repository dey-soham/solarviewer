#!/usr/bin/env python3
"""
Radio spectra of spacecraft receivers, from the daily CDF files of NASA's
Space Physics Data Facility (SPDF):

- Wind/WAVES: RAD1 (20-1040 kHz) and RAD2 (1.075-13.825 MHz)
- STEREO/WAVES: 2.6 kHz-16 MHz in one-minute averages, Ahead and (until
  2014) Behind
- Parker Solar Probe FIELDS/RFS: LFR (10 kHz-1.7 MHz) and HFR (1.3-19.2 MHz)
- Solar Orbiter RPW: TNR (4 kHz-1 MHz) and HFR (0.4-16.4 MHz)

Each is read into times, frequencies and intensities and written as a
dynamic spectrum FITS file (radio_data_downloader.write_spectrum). The
values are the files' own, in their units: nothing is calibrated again,
no background is taken off.
"""

import os
import re
from datetime import date, datetime, timedelta
from typing import NamedTuple

import numpy as np

from . import radio_data_downloader as rdd
from .radio_data_downloader import _check, _say

SPDF = "https://spdf.gsfc.nasa.gov/pub/data"
FILL = -1e30  # the files mark a missing value with -1e31


class Spectrum(NamedTuple):
    times: np.ndarray  # datetime64[ms], UTC, ascending
    freqs: np.ndarray  # MHz, ascending
    data: np.ndarray  # time x frequency, NaN where nothing was measured
    unit: str
    antenna: object  # its name ("" when the file names none), or a name per time where the receiver changes antenna
    notes: tuple  # what a reader of the FITS should know
    rules: str  # the file's own rules of use


# ============================================================================
# CDF files
# ============================================================================


class MissingPackage(RuntimeError):
    """The package that reads CDF files is not installed."""


def _open(path):
    try:
        import cdflib
    except ImportError as e:
        raise MissingPackage("Reading these files needs the cdflib package: pip install cdflib") from e
    return cdflib.CDF(path)


def _values(raw):
    """A variable's values as float32, NaN where the file has none."""
    values = np.array(raw, dtype=np.float64)
    values[~np.isfinite(values) | (values < FILL)] = np.nan
    return values.astype(np.float32)


def _utc(cdf, name, records=None):
    """The times of a CDF time variable (CDF_EPOCH or TT2000) as
    datetime64[ms] UTC, NaT where the record has none; of ``records`` only
    when given."""
    import cdflib

    raw = np.atleast_1d(cdf.varget(name))
    if records is not None:
        raw = raw[records]
    if raw.dtype.kind in "iu":
        good = raw > np.iinfo(np.int64).min
    else:
        good = np.isfinite(raw) & (raw > 0)
    times = np.full(raw.shape, np.datetime64("NaT"), dtype="datetime64[ms]")
    if good.any():
        times[good] = np.asarray(cdflib.cdfepoch.to_datetime(raw[good])).astype("datetime64[ms]")
    return times


def _in_mhz(cdf, name):
    """A frequency variable in MHz (the file says its unit), NaN where it
    has none."""
    freqs = np.array(cdf.varget(name), dtype=np.float64)
    freqs[~np.isfinite(freqs) | (freqs <= 0)] = np.nan
    unit = str(cdf.varattsget(name).get("UNITS", "Hz")).strip().lower()
    return freqs * {"hz": 1e-6, "khz": 1e-3, "mhz": 1.0}.get(unit, 1e-6)


def _rules(cdf):
    """The file's rules of use (or its acknowledgement), on one line."""
    attributes = cdf.globalattsget()
    for key in ("Rules_of_use", "Acknowledgement"):
        value = attributes.get(key)
        if value:
            text = " ".join(str(v) for v in value) if isinstance(value, (list, tuple)) else str(value)
            return " ".join(text.split()).encode("ascii", "replace").decode("ascii")  # (a FITS header is ASCII)
    return ""


def _one_grid(freqs, data):
    """(freqs, data) with one column per frequency: ``freqs`` is one list
    for every record, or a list per record (then each value goes to the
    column of its frequency, NaN where a record did not measure one)."""
    freqs = np.asarray(freqs, dtype=np.float64)
    if freqs.ndim == 2 and not np.all((freqs == freqs[0]) | (np.isnan(freqs) & np.isnan(freqs[0]))):
        known = np.isfinite(freqs)
        grid = np.unique(freqs[known])
        out = np.full((len(data), len(grid)), np.nan, dtype=np.float32)
        rows = np.broadcast_to(np.arange(len(data))[:, None], freqs.shape)
        out[rows[known], np.searchsorted(grid, freqs[known])] = data[known]
        return grid, out
    row = freqs[0] if freqs.ndim == 2 else freqs
    known = np.flatnonzero(np.isfinite(row))
    order = known[np.argsort(row[known], kind="stable")]
    return row[order], data[:, order]


def _spectrum(cdf, times, freqs, data, unit, antenna, notes):
    """The spectrum in time order, without the records and channels that
    hold nothing; None when nothing is left."""
    timed = ~np.isnat(times)
    times, data = times[timed], data[timed]
    order = np.argsort(times, kind="stable")
    times, data = times[order], data[order]
    measured = np.isfinite(data)
    rows, columns = measured.any(axis=1), measured.any(axis=0)
    if len(times):
        rows &= np.concatenate([[True], np.diff(times.astype("int64")) > 0])
    if not isinstance(antenna, str):
        antenna = np.asarray(antenna)[timed][order][rows]
    times, freqs, data = times[rows], freqs[columns], data[rows][:, columns]
    if not len(times) or not len(freqs):
        return None
    return Spectrum(times, freqs, data, unit, antenna, tuple(notes), _rules(cdf))


# ============================================================================
# Readers
# ============================================================================


def read_wind(path):
    """A Wind/WAVES level 2 file (RAD1 or RAD2): the power spectral density
    of the S channel."""
    cdf = _open(path)
    times = _utc(cdf, "Epoch")
    freqs, data = _one_grid(_in_mhz(cdf, "FREQUENCY"), _values(cdf.varget("PSD_V2_S")))
    notes = ["Power spectral density of the S channel (PSD_V2_S), before antenna calibration"]
    try:
        late = float(np.nanmax(_values(cdf.varget("TIMING_S"))))
    except (ValueError, KeyError):
        late = float("nan")
    if late >= 1:
        notes.append(f"A spectrum has the time its record starts; its channels are measured up to {late:.0f} s later")
    return _spectrum(cdf, times, freqs, data, "V^2/Hz", "", notes)


def read_stereo(path, spacecraft):
    """A STEREO/WAVES combined level 2 file: the one-minute averages of
    ``spacecraft`` ("ahead" or "behind"), in dB above the background."""
    cdf = _open(path)
    times = _utc(cdf, "Epoch")
    data = _values(cdf.varget(f"avg_intens_{spacecraft}"))
    with np.errstate(invalid="ignore"):
        data[:, ~(np.nan_to_num(data) != 0).any(axis=0)] = np.nan  # channels the receiver never tunes to hold zeros
    freqs, data = _one_grid(_in_mhz(cdf, "frequency"), data)
    notes = ["Intensity above the background in dB, one-minute averages (LFR and HFR)"]
    return _spectrum(cdf, times, freqs, data, "dB", "", notes)


PSP_ANTENNAS = ("ch0_V1V2", "ch1_V3V4")  # the two dipoles, the usual ones first


def read_psp(path, band):
    """A Parker Solar Probe FIELDS/RFS level 2 file of ``band`` ("lfr" or
    "hfr"): the auto spectrum of the V1-V2 dipole, or of the antenna that
    was recorded."""
    cdf = _open(path)
    prefix = f"psp_fld_l2_rfs_{band}_auto_averages_"
    recorded = {}
    for name in cdf.cdf_info().zVariables:
        if name.startswith(prefix) and not name.endswith(("_GND", "_SCM")):
            records = cdf.varinq(name).Last_Rec + 1
            if records > 0:
                recorded[name[len(prefix):]] = records
    if not recorded:
        return None
    antenna = next((a for a in PSP_ANTENNAS if a in recorded), None) or max(recorded, key=recorded.get)
    times = _utc(cdf, f"epoch_{band}_auto_averages_{antenna}")
    freqs, data = _one_grid(_in_mhz(cdf, f"frequency_{band}_auto_averages_{antenna}"), _values(cdf.varget(prefix + antenna)))
    name = antenna.split("_", 1)[1]
    name = f"{name[:2]}-{name[2:]}" if len(name) == 4 else name
    return _spectrum(cdf, times, freqs, data, "V^2/Hz", name, [f"Auto spectrum of {name}"])


# Solar Orbiter RPW's sensors, as SENSOR_CONFIG numbers them
SOLO_SENSORS = {1: "V1", 2: "V2", 3: "V3", 4: "V1-V2", 5: "V2-V3", 6: "V3-V1", 9: "V1-V2", 10: "V2-V3", 11: "V3-V1"}
SOLO_TNR_ORDER = (4, 5, 6, 1, 2, 3)  # dipoles before monopoles
SOLO_HFR_ORDER = (9, 10, 11)
SOLO_HOLD_S = 30.0  # another antenna is used where the first has no sweep this near


def _sweeps(sweep_numbers):
    """The first record of each sweep, and each record's sweep."""
    changes = np.flatnonzero(np.diff(np.asarray(sweep_numbers, dtype=np.int64)) != 0) + 1
    starts = np.concatenate([[0], changes])
    return starts, np.repeat(np.arange(len(starts)), np.diff(np.concatenate([starts, [len(sweep_numbers)]])))


def _sweep_sensors(sweep, sensor, count):
    """The sensor of each of ``count`` sweeps: that of its first measured
    record, 0 when it has none."""
    out = np.zeros(count, dtype=int)
    measured = np.flatnonzero(sensor > 0)
    which, first = np.unique(sweep[measured], return_index=True)
    out[which] = sensor[measured[first]]
    return out


def _one_antenna(seconds, sensors, order):
    """Which sweeps to keep: RPW takes its antennas in turn, sweep by
    sweep, and they do not measure the same level. The first of ``order``
    that was used, and another only where that one has no sweep nearby
    (in burst mode the receiver stays on one antenna)."""
    keep = np.zeros(len(sensors), dtype=bool)
    hold = SOLO_HOLD_S
    for sensor in order:
        mine = np.flatnonzero(sensors == sensor)
        if not len(mine):
            continue
        if not keep.any():
            keep[mine] = True
            if len(mine) > 2:
                hold = max(hold, 3 * float(np.median(np.diff(seconds[mine]))))
            continue
        kept = seconds[keep]
        after = np.searchsorted(kept, seconds[mine])
        gap = np.minimum(
            np.abs(seconds[mine] - kept[np.clip(after - 1, 0, len(kept) - 1)]),
            np.abs(kept[np.clip(after, 0, len(kept) - 1)] - seconds[mine]),
        )
        keep[mine[gap > hold]] = True
    return keep


def _sensor_names(sensors):
    return np.array([SOLO_SENSORS.get(int(s), "") for s in sensors], dtype=object)


def read_solo_hfr(path):
    """A Solar Orbiter RPW HFR survey file: one spectrum per sweep (the
    file holds a record per frequency), of one antenna."""
    cdf = _open(path)
    freqs = np.atleast_1d(_in_mhz(cdf, "FREQUENCY"))
    records = len(freqs)
    if not records:
        return None
    sensors = np.asarray(cdf.varget("SENSOR_CONFIG"), dtype=int).reshape(records, -1)
    power = [_values(cdf.varget(name)) for name in ("AGC1", "AGC2")]
    value = np.full(records, np.nan, dtype=np.float32)
    sensor = np.zeros(records, dtype=int)
    for channel in (1, 0):  # channel 1 over channel 2 where both measured
        with np.errstate(invalid="ignore"):
            on = (power[channel] > 0) & np.isin(sensors[:, channel], SOLO_HFR_ORDER)
        value[on], sensor[on] = power[channel][on], sensors[on, channel]
    starts, sweep = _sweeps(cdf.varget("SWEEP_NUM"))
    sweep_sensor = _sweep_sensors(sweep, sensor, len(starts))
    times = _utc(cdf, "Epoch", starts)  # a sweep has the time of its first record
    keep = _one_antenna(times.astype("int64") / 1000.0, sweep_sensor, SOLO_HFR_ORDER) & ~np.isnat(times)
    if not keep.any():
        return None
    row = np.cumsum(keep) - 1
    wanted = keep[sweep] & np.isfinite(value) & np.isfinite(freqs) & (sensor == sweep_sensor[sweep])
    grid = np.unique(freqs[wanted])
    data = np.full((int(keep.sum()), len(grid)), np.nan, dtype=np.float32)
    data[row[sweep[wanted]], np.searchsorted(grid, freqs[wanted])] = value[wanted]
    return _spectrum(cdf, times[keep], grid, data, "V^2/Hz", _sensor_names(sweep_sensor[keep]),
                     ["One spectrum per sweep of the receiver, of one antenna"])


def read_solo_tnr(path):
    """A Solar Orbiter RPW TNR survey file: one spectrum per sweep of its
    four bands (the file holds a record per band), of one antenna."""
    cdf = _open(path)
    band = np.atleast_1d(np.asarray(cdf.varget("TNR_BAND"), dtype=int))
    records = len(band)
    if not records:
        return None
    grid = _in_mhz(cdf, "TNR_BAND_FREQ").reshape(-1)  # bands A to D, 32 channels each
    width = len(grid) // 4
    sensors = np.asarray(cdf.varget("SENSOR_CONFIG"), dtype=int).reshape(records, -1)
    front = np.asarray(cdf.varget("FRONT_END"), dtype=int)
    power = [_values(cdf.varget(name)).reshape(records, width) for name in ("AUTO1", "AUTO2")]
    value = np.full((records, width), np.nan, dtype=np.float32)
    sensor = np.zeros(records, dtype=int)
    for channel in (1, 0):
        with np.errstate(invalid="ignore"):
            on = (power[channel] > 0).any(axis=1) & np.isin(sensors[:, channel], SOLO_TNR_ORDER) & (front == 1)
        value[on], sensor[on] = power[channel][on], sensors[on, channel]
    with np.errstate(invalid="ignore"):
        value[~(value > 0)] = np.nan
    starts, sweep = _sweeps(cdf.varget("SWEEP_NUM"))
    sweep_sensor = _sweep_sensors(sweep, sensor, len(starts))
    times = _utc(cdf, "Epoch", starts)
    keep = _one_antenna(times.astype("int64") / 1000.0, sweep_sensor, SOLO_TNR_ORDER) & ~np.isnat(times)
    if not keep.any():
        return None
    row = np.cumsum(keep) - 1
    wanted = np.flatnonzero(keep[sweep] & (sensor == sweep_sensor[sweep]) & (band >= 0) & (band < 4))
    data = np.full((int(keep.sum()), len(grid)), np.nan, dtype=np.float32)
    columns = band[wanted, None] * width + np.arange(width)
    data[row[sweep[wanted]][:, None], columns] = value[wanted]
    order = np.argsort(grid, kind="stable")
    return _spectrum(cdf, times[keep], grid[order], data[:, order], "V^2/Hz", _sensor_names(sweep_sensor[keep]),
                     ["One spectrum per sweep of the four bands A to D, of one antenna"])


# ============================================================================
# The archive
# ============================================================================

# ``choices``: what the form offers, each a tuple of receivers to write (one
# spectrum each, the higher band first: it is the one opened when done). ``file``: the name of a day's file, with its version.
# ``last``: the receiver's last day of data.
MISSIONS = {
    "wind": {
        "name": "Wind/WAVES", "telescope": "Wind", "slug": "wind-waves", "first_year": 1994,
        "about": "Near Earth, at L1 since 2004",
        "choices": (
            ("both", "RAD1 and RAD2 · 20 kHz–13.8 MHz", ("rad2", "rad1")),
            ("rad2", "RAD2 · 1.075–13.825 MHz", ("rad2",)),
            ("rad1", "RAD1 · 20–1040 kHz", ("rad1",)),
        ),
        "receivers": {
            "rad1": {"label": "RAD1", "instrument": "WAVES RAD1", "folder": "wind/waves/rad1_l2",
                     "file": r"wi_l2_wav_rad1_{day}_v(\d+)\.cdf", "mb": 0.5, "note": "20–1040 kHz, a sweep every 3 min",
                     "read": read_wind},
            "rad2": {"label": "RAD2", "instrument": "WAVES RAD2", "folder": "wind/waves/rad2_l2",
                     "file": r"wi_l2_wav_rad2_{day}_v(\d+)\.cdf", "mb": 9.0, "note": "1.075–13.825 MHz every 16 s",
                     "read": read_wind},
        },
    },
    "stereo": {
        "name": "STEREO/WAVES", "telescope": "STEREO", "slug": "stereo-waves", "first_year": 2006,
        "about": "Away from the Sun–Earth line: bursts Earth does not see",
        "choices": (
            ("ahead", "STEREO-A · 2.6 kHz–16 MHz", ("ahead",)),
            ("behind", "STEREO-B · until 2014", ("behind",)),
            ("both", "STEREO-A and STEREO-B", ("ahead", "behind")),
        ),
        "receivers": {
            "ahead": {"label": "STEREO-A", "telescope": "STEREO-A", "instrument": "WAVES",
                      "folder": "stereo/combined/swaves/level2_cdf", "file": r"stereo_level2_swaves_{day}_v(\d+)\.cdf",
                      "mb": 0.9, "note": "2.6 kHz–16 MHz, one-minute averages", "read": lambda p: read_stereo(p, "ahead")},
            "behind": {"label": "STEREO-B", "telescope": "STEREO-B", "instrument": "WAVES",
                       "folder": "stereo/combined/swaves/level2_cdf", "file": r"stereo_level2_swaves_{day}_v(\d+)\.cdf",
                       "mb": 0.9, "note": "2.6 kHz–16 MHz, one-minute averages; contact was lost on 2014-10-01",
                       "last": date(2014, 10, 1), "read": lambda p: read_stereo(p, "behind")},
        },
    },
    "psp": {
        "name": "Parker Solar Probe/FIELDS", "telescope": "Parker Solar Probe", "slug": "psp-fields", "first_year": 2018,
        "about": "Close to the Sun; no data on some days",
        "choices": (
            ("both", "LFR and HFR · 10 kHz–19.2 MHz", ("hfr", "lfr")),
            ("hfr", "HFR · 1.3–19.2 MHz", ("hfr",)),
            ("lfr", "LFR · 10 kHz–1.7 MHz", ("lfr",)),
        ),
        "receivers": {
            "lfr": {"label": "LFR", "instrument": "FIELDS RFS LFR", "folder": "psp/fields/l2/rfs_lfr",
                    "file": r"psp_fld_l2_rfs_lfr_{day}_v(\d+)\.cdf", "mb": 30.0, "note": "10 kHz–1.7 MHz every 7 s",
                    "read": lambda p: read_psp(p, "lfr")},
            "hfr": {"label": "HFR", "instrument": "FIELDS RFS HFR", "folder": "psp/fields/l2/rfs_hfr",
                    "file": r"psp_fld_l2_rfs_hfr_{day}_v(\d+)\.cdf", "mb": 25.0, "note": "1.3–19.2 MHz every 7 s",
                    "read": lambda p: read_psp(p, "hfr")},
        },
    },
    "solo": {
        "name": "Solar Orbiter/RPW", "telescope": "Solar Orbiter", "slug": "solar-orbiter-rpw", "first_year": 2020,
        "about": "Out of the Sun–Earth line for most of its orbit",
        "choices": (
            ("both", "TNR and HFR · 4 kHz–16.4 MHz", ("hfr", "tnr")),
            ("hfr", "HFR · 0.4–16.4 MHz", ("hfr",)),
            ("tnr", "TNR · 4 kHz–1 MHz", ("tnr",)),
        ),
        "receivers": {
            "tnr": {"label": "TNR", "instrument": "RPW TNR", "folder": "solar-orbiter/rpw/science/l2/tnr-surv",
                    "file": r"solo_l2_rpw-tnr-surv_{day}_v(\d+)\.cdf", "mb": 70.0, "note": "4 kHz–1 MHz, one antenna every 14 s",
                    "read": read_solo_tnr},
            "hfr": {"label": "HFR", "instrument": "RPW HFR", "folder": "solar-orbiter/rpw/science/l2/hfr-surv",
                    "file": r"solo_l2_rpw-hfr-surv_{day}_v(\d+)\.cdf", "mb": 40.0, "note": "0.4–16.4 MHz, one antenna every 6–8 s",
                    "read": read_solo_hfr},
        },
    },
}

SIZE = re.compile(
    r'href="([^"?/][^"?]*)"[^>]*>[^<]*</a>\s*</td>\s*<td[^>]*>[^<]*</td>\s*<td[^>]*>\s*([\d.]+)\s*([KMG]?)\s*</td>'
)
YEAR = re.compile(r"\d{4}")


def receivers_of(mission, choice):
    """The receivers a choice of the form stands for."""
    for key, _label, receivers in MISSIONS[mission]["choices"]:
        if key == choice:
            return receivers
    return MISSIONS[mission]["choices"][0][2]


def _sizes(folder):
    """{name: MB} of the files in a folder of the archive, as its page
    lists them (rounded as it rounds them)."""
    scale = {"": 1e-6, "K": 1e-3, "M": 1.0, "G": 1e3}
    return {name: float(number) * scale[unit] for name, number, unit in SIZE.findall(rdd._page(folder) or "")}


def day_file(mission, receiver, day):
    """The receiver's file of ``day``, its latest version: a dict with
    receiver, day, name, url and mb; None when the archive has none.
    Raises ArchiveUnreachable when it cannot be asked."""
    config = MISSIONS[mission]["receivers"][receiver]
    folder = f"{SPDF}/{config['folder']}/{day:%Y}/"
    pattern = re.compile(config["file"].format(day=f"{day:%Y%m%d}"))
    versions = sorted((int(m.group(1)), name) for name in rdd._listing(folder) for m in [pattern.fullmatch(name)] if m)
    if not versions:
        return None
    name = versions[-1][1]
    return {"receiver": receiver, "day": day, "name": name, "url": folder + name,
            "mb": _sizes(folder).get(name) or config["mb"]}


def spacecraft_files(mission, receivers, start, end):
    """The day files holding part of [start, end] (UT), receiver by
    receiver and day by day."""
    found = []
    for receiver in receivers:
        last = MISSIONS[mission]["receivers"][receiver].get("last")
        day = start.date()
        while day <= min(end.date(), last or end.date()):
            item = day_file(mission, receiver, day)
            if item:
                found.append(item)
            day += timedelta(days=1)
    return found


def latest_day(mission, receiver):
    """The last day the archive holds a file of the receiver for (the
    teams publish weeks to months after the observation), None when it
    has none."""
    config = MISSIONS[mission]["receivers"][receiver]
    base = f"{SPDF}/{config['folder']}/"
    pattern = re.compile(config["file"].format(day=r"(\d{8})"))
    for year in sorted((n for n in rdd._listing(base) if YEAR.fullmatch(n)), reverse=True):
        days = []
        for name in rdd._listing(f"{base}{year}/"):
            m = pattern.fullmatch(name)
            if m:
                try:
                    days.append(datetime.strptime(m.group(1), "%Y%m%d").date())
                except ValueError:
                    pass
        if days:
            return max(days)
    return None


# ============================================================================
# Window to spectra
# ============================================================================


def output_name(mission, receiver, start, end, whole_day=None):
    slug = f"{MISSIONS[mission]['slug']}_{receiver}"
    if whole_day is not None:
        return f"{slug}_{whole_day:%Y%m%d}_day_spectrum.fits"
    tail = f"{end:%H%M%S}" if end.date() == start.date() else f"{end:%Y%m%d_%H%M%S}"
    return f"{slug}_{start:%Y%m%d_%H%M%S}-{tail}_spectrum.fits"


def _antennas(pieces):
    """(header value, note) of the antennas the spectra were measured
    with, the most used first; ("", None) when the files name none."""
    names = np.concatenate([
        np.full(len(p.times), p.antenna, dtype=object) if isinstance(p.antenna, str) else p.antenna for p in pieces
    ])
    found, counts = np.unique(names[names != ""].astype(str), return_counts=True)
    if not len(found):
        return "", None
    order = np.argsort(-counts, kind="stable")
    found, counts = found[order], counts[order]
    note = f"Antenna {found[0]}"
    if len(found) > 1:
        others = ", ".join(f"{name} ({n} spectra)" for name, n in zip(found[1:], counts[1:]))
        note += f" ({counts[0]} spectra); where it was not measured: {others}"
    return ", ".join(found), note


def _joined(pieces):
    """(times, freqs, data) of the days' spectra one after another, on the
    channels of all of them (NaN where a day did not have a channel)."""
    freqs = np.unique(np.concatenate([p.freqs for p in pieces]))
    blocks = []
    for piece in pieces:
        if len(piece.freqs) == len(freqs):
            blocks.append(piece.data)
            continue
        block = np.full((len(piece.times), len(freqs)), np.nan, dtype=np.float32)
        block[:, np.searchsorted(freqs, piece.freqs)] = piece.data
        blocks.append(block)
    times = np.concatenate([p.times for p in pieces])
    data = np.concatenate(blocks)
    order = np.argsort(times, kind="stable")
    return times[order], freqs, data[order]


def spacecraft_spectra(
    mission, receivers, start, end, output_dir, whole_day=None, keep_raw=False,
    progress=None, counts=None, should_stop=None,
):
    """Download the receivers' day files for [start, end] (UT) and write a
    dynamic spectrum per receiver, cut to the window. Returns the FITS
    paths; a receiver without data in the window gives none, and the
    reason is said through ``progress``. ``counts(fetched, read, total)``
    follows the files."""
    config = MISSIONS[mission]
    _say(progress, f"Asking the archive for {config['name']}'s files…")
    files = spacecraft_files(mission, receivers, start, end)
    if not files:
        _say(progress, f"The archive has no {config['name']} file for this window")
        return []
    urls = list(dict.fromkeys(f["url"] for f in files))  # (STEREO's two spacecraft share a file)
    total = len(urls)
    _say(progress, f"Found {total} file{'s' if total > 1 else ''}: {', '.join(os.path.basename(u) for u in urls)}")
    os.makedirs(output_dir, exist_ok=True)
    raw_paths, fetched = [], 0
    for url in urls:
        _check(should_stop)
        raw = os.path.join(output_dir, os.path.basename(url))
        if not os.path.exists(raw):
            _say(progress, f"Downloading {os.path.basename(url)}")
            rdd.fetch(url, raw, should_stop)
            raw_paths.append(raw)  # (a file that was already there stays)
            _say(progress, f"Downloaded {os.path.basename(url)}")
        fetched += 1
        if counts:
            counts(fetched, 0, total)

    outputs, read = [], set()
    lo, hi = np.datetime64(start, "ms"), np.datetime64(end, "ms")
    for receiver in receivers:
        settings = config["receivers"][receiver]
        pieces, names = [], []
        for item in (f for f in files if f["receiver"] == receiver):
            _check(should_stop)
            _say(progress, f"Reading {item['name']} ({settings['label']})")
            try:
                spectrum = settings["read"](os.path.join(output_dir, item["name"]))
            except MissingPackage:
                raise
            except Exception as e:
                _say(progress, f"Could not read {item['name']}: {e}")
                spectrum = None
            read.add(item["url"])
            if counts:
                counts(fetched, len(read), total)
            if spectrum is None:
                continue
            inside = (spectrum.times >= lo) & (spectrum.times <= hi)
            if inside.any():
                antenna = spectrum.antenna if isinstance(spectrum.antenna, str) else spectrum.antenna[inside]
                pieces.append(spectrum._replace(times=spectrum.times[inside], data=spectrum.data[inside], antenna=antenna))
                names.append(item["name"])
        if not pieces:
            _say(progress, f"{settings['label']} has no measurements in this window")
            continue
        times, freqs, data = _joined(pieces)
        first = pieces[0]
        path = os.path.join(output_dir, output_name(mission, receiver, start, end, whole_day))
        history = [f"Source: {config['name']} {settings['label']}, NASA SPDF ({', '.join(names)})", *first.notes]
        extra = {"RECEIVER": settings["label"], "ORIGIN": "NASA SPDF"}
        antenna, note = _antennas(pieces)
        if antenna:
            extra["ANTENNA"] = antenna
            if not isinstance(first.antenna, str):  # (a reader with one antenna has said which)
                history.append(note)
        if first.rules:
            history.append(f"Rules of use: {first.rules}")
        rdd.write_spectrum(path, data, times, freqs, settings.get("telescope", config["telescope"]),
                           settings["instrument"], history, bunit=first.unit, extra=extra)
        outputs.append(path)
        _say(progress, f"Wrote {os.path.basename(path)} ({len(times)} times x {len(freqs)} channels)")
    if not keep_raw:
        rdd._remove(raw_paths)
    return outputs

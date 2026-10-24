"""
Solar data download and preparation for SolarViewer.

Downloads images from the observatories below and prepares them so that
SolarViewer shows them correctly: one 2-D image per file in the primary HDU,
solar north up with the Sun at the reference pixel, exposure-normalised
where the data are raw counts, a standard WCS (HPLN-TAN/HPLT-TAN in
arcsec) and an ISO DATE-OBS. Every prepared image is saved as 32-bit
floats.

- SDO/AIA: level 1 to level 1.5 with aiapy (pointing update, optional PSF
  deconvolution, registration, degradation correction, DN/s)
- SDO/HMI: north up and Sun-centred at the native 0.504"/px
- IRIS slit-jaw images: one file per exposure with that exposure's time,
  pointing and exposure time, DN/s, north up
- SOHO/EIT: the level 1 product where SDAC has it (dark, flat, degridding,
  degradation and exposure corrected), else level 0 made DN/s; north up
- SOHO/LASCO C2 and C3: level 0.5 calibrated to mean solar brightness as
  NRL's level 1 pipeline does (bias, exposure, calibration factor,
  vignetting, C3 stray-light ramp and mask, distortion), north up; see
  lasco_calibration.py and LASCO_CALIBRATION.md
- SOHO/MDI: north up (the level 1.8 product arrives in a tar file)
- GOES/SUVI: level 2 composites rewritten into the primary HDU; level 1b
  rotated north up
- STEREO/SECCHI: COR1 and COR2 polarizer triplets calibrated to mean solar
  brightness (on-board processing undone, bias, exposure, calibration
  factor, vignetting, Mueller combination, COR2 distortion, masks), north
  up, with the team's monthly minimum background in a second HDU; EUVI
  with its bias, filter and flat corrected, DN/s, north up; see
  secchi_calibration.py and SECCHI_CALIBRATION.md

Downloads go through SunPy's Fido (the VSO, NOAA) or, for SDO with an
email address registered at JSOC, through a JSOC export. Some networks
hand out IPv6 addresses that do not route; the downloader notices that
and prefers IPv4, which is the difference between a search taking seconds
and minutes.
"""

import os
import re
import shutil
import socket
import tarfile
import time
import warnings
from datetime import datetime, timedelta

import numpy as np
from astropy.io import fits
import astropy.units as u

try:
    from aiapy.calibrate import register, update_pointing, correct_degradation
    from aiapy.calibrate.utils import get_pointing_table, get_correction_table
    from aiapy.psf import deconvolve as aia_deconvolve

    HAS_AIAPY = True
except ImportError:  # the AIA steps that need it are skipped, with a note
    HAS_AIAPY = False

# Default download settings
DEFAULT_MAX_CONN = 5  # simultaneous connections
DEFAULT_MAX_SPLITS = 3  # parallel parts per file
DEFAULT_MAX_RETRIES = 4  # retries of the files that failed
DEFAULT_TIMEOUT = 120  # seconds without data before a file is abandoned and retried

TIME_FORMAT = "%Y.%m.%d %H:%M:%S"

# SDO series at JSOC
AIA_SERIES = {"12s": "aia.lev1_euv_12s", "24s": "aia.lev1_uv_24s", "1h": "aia.lev1_vis_1h"}
WAVELENGTHS = {
    "12s": ["94", "131", "171", "193", "211", "304", "335"],
    "24s": ["1600", "1700"],
    "1h": ["4500"],
}
# HMI series: the key is what the interfaces call it. Only the 45 s series
# are served by the VSO; the 720 s ones need a JSOC export (an email).
HMI_SERIES = {
    "M_45s": "hmi.M_45s",
    "M_720s": "hmi.M_720s",
    "Ic_45s": "hmi.Ic_45s",
    "Ic_720s": "hmi.Ic_720s",
    "Ic_noLimbDark_720s": "hmi.Ic_noLimbDark_720s",
    "V_45s": "hmi.V_45s",
    "V_720s": "hmi.V_720s",
}
HMI_PHYSOBS = {"M": "LOS_magnetic_field", "Ic": "intensity", "V": "LOS_velocity"}


def _quiet():
    import logging

    warnings.filterwarnings("ignore")
    logging.getLogger("sunpy").setLevel(logging.ERROR)


# ------------------------------------------------------------------ network
_ipv4_preferred = None


def prefer_ipv4():
    """Prefer IPv4 for this process when IPv6 does not reach the internet.

    Some networks hand out IPv6 addresses (often NAT64 ones) that do not
    route. Python then waits for each IPv6 connection attempt to time out
    before trying IPv4, which makes every search and download minutes slow.
    This probes once; when IPv6 fails and IPv4 works, name lookups put the
    IPv4 addresses first from then on. Returns True when IPv4 is preferred.
    """
    global _ipv4_preferred
    if _ipv4_preferred is not None:
        return _ipv4_preferred
    _ipv4_preferred = False
    try:
        infos = socket.getaddrinfo("sdac.virtualsolar.org", 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    v6 = [i for i in infos if i[0] == socket.AF_INET6]
    v4 = [i for i in infos if i[0] == socket.AF_INET]
    if not v6 or not v4:
        return False

    def reaches(info):
        try:
            with socket.socket(info[0], socket.SOCK_STREAM) as s:
                s.settimeout(3)
                s.connect(info[4])
            return True
        except OSError:
            return False

    if reaches(v6[0]) or not reaches(v4[0]):
        return False
    real = socket.getaddrinfo

    def ipv4_first(*args, **kwargs):
        found = real(*args, **kwargs)
        return sorted(found, key=lambda i: i[0] != socket.AF_INET)

    socket.getaddrinfo = ipv4_first
    _ipv4_preferred = True
    print("IPv6 does not reach the data centres from here; using IPv4.", flush=True)
    return True


def _downloader(max_conn, max_splits, timeout, progress):
    """A parfive Downloader that abandons a file when no data has arrived for
    ``timeout`` seconds (a stalled connection), so the retry loop can try it
    again. A slow file that keeps receiving data is never cut off."""
    import aiohttp
    from parfive import Downloader, SessionConfig

    timeouts = aiohttp.ClientTimeout(total=None, sock_connect=timeout, sock_read=timeout)
    if prefer_ipv4():

        def session(config):
            return aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(family=socket.AF_INET),
                timeout=timeouts,
                headers=config.headers,
            )

        config = SessionConfig(aiohttp_session_generator=session)
    else:
        config = SessionConfig(timeouts=timeouts)
    return Downloader(max_conn=max_conn, max_splits=max_splits, progress=progress, config=config)


def robust_fido_fetch(
    result,
    output_dir,
    max_conn=DEFAULT_MAX_CONN,
    max_splits=DEFAULT_MAX_SPLITS,
    max_retries=DEFAULT_MAX_RETRIES,
    timeout=DEFAULT_TIMEOUT,
    progress=True,
):
    """Fido.fetch with retries of the files that failed, each retry with
    fewer connections. Returns the parfive Results (``.errors`` lists what
    still failed)."""
    from sunpy.net import Fido

    print(
        f"Starting download with settings: max_conn={max_conn}, retries={max_retries}, "
        f"timeout={timeout}s without data",
        flush=True,
    )
    downloaded = Fido.fetch(
        result,
        path=os.path.join(output_dir, "{file}"),
        downloader=_downloader(max_conn, max_splits, timeout, progress),
    )
    retry = 0
    while downloaded.errors and retry < max_retries:
        retry += 1
        print(
            f"\nRetrying {len(downloaded.errors)} failed downloads (attempt {retry}/{max_retries})...",
            flush=True,
        )
        time.sleep(2)
        downloaded = Fido.fetch(
            downloaded, downloader=_downloader(max(1, max_conn // 2), max_splits, timeout, progress)
        )
    _report_errors(downloaded)
    return downloaded


def _report_errors(downloaded):
    if downloaded.errors:
        print("\n" + "=" * 60 + f"\nWARNING: {len(downloaded.errors)} files failed to download\n" + "=" * 60)
        for error in downloaded.errors[:5]:
            print(f"  - {str(error).strip().splitlines()[-1][:300]}")
        if len(downloaded.errors) > 5:
            print(f"  ... and {len(downloaded.errors) - 5} more errors")
        print("\nTroubleshooting tips:")
        print("  - The data provider may be slow or down; try again later")
        print("  - Try a smaller time range or a coarser cadence")
    else:
        print(f"\nSuccessfully downloaded {len(downloaded)} files.", flush=True)


def _fetch_urls(urls, output_dir, filenames=None, max_conn=DEFAULT_MAX_CONN, max_retries=DEFAULT_MAX_RETRIES):
    """Download plain URLs (a JSOC export) in parallel, with retries. Returns
    the paths that arrived."""
    downloader = _downloader(max_conn, 1, DEFAULT_TIMEOUT, True)
    for i, url in enumerate(urls):
        downloader.enqueue_file(url, path=output_dir, filename=filenames[i] if filenames else None)
    results = downloader.download()
    retry = 0
    while results.errors and retry < max_retries:
        retry += 1
        print(f"\nRetrying {len(results.errors)} failed downloads (attempt {retry}/{max_retries})...", flush=True)
        time.sleep(2)
        results = downloader.retry(results)
    _report_errors(results)
    return [str(p) for p in results]


# -------------------------------------------------------------- time helpers
def _parse_time(text):
    """A 'YYYY.MM.DD HH:MM:SS' string (or ISO) as a datetime."""
    text = str(text).strip().replace("_", " ").replace("T", " ")
    for fmt in (
        TIME_FORMAT,
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y.%m.%d %H:%M",
        "%Y-%m-%d %H:%M",
        "%Y.%m.%d",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot read the time {text!r}; use YYYY.MM.DD HH:MM:SS")


def _sample_attrs(sample):
    """The Fido attribute asking for one image every ``sample`` seconds, or
    nothing for every image."""
    from sunpy.net import attrs as a

    return [a.Sample(float(sample) * u.second)] if sample else []


def _jsoc_record(series, start_dt, end_dt, sample=None, wavelength=None):
    """A JSOC record set string for ``series`` over the time range, every
    ``sample`` seconds (the series' own cadence when None), e.g.
    aia.lev1_euv_12s[2022.05.19_12:00:00_UTC/3600s@60s][171]."""
    seconds = max(1, int(round((end_dt - start_dt).total_seconds())))
    cadence = f"@{int(round(float(sample)))}s" if sample else ""
    record = f"{series}[{start_dt.strftime('%Y.%m.%d_%H:%M:%S')}_UTC/{seconds}s{cadence}]"
    if wavelength is not None:
        record += f"[{int(wavelength)}]"
    return record


def aiaexport(wavelength, cadence, start_time, end_time, sample=None):
    """The JSOC record set for AIA ``wavelength`` at the series ``cadence``
    ('12s', '24s' or '1h'), or None for a wavelength the series lacks."""
    if cadence not in AIA_SERIES:
        print(f"Error: Invalid cadence '{cadence}'. Use '12s', '24s', or '1h'.")
        return None
    if str(wavelength) not in WAVELENGTHS[cadence]:
        print(f"Error: {wavelength}Å image not available for {cadence} cadence")
        return None
    return _jsoc_record(AIA_SERIES[cadence], _parse_time(start_time), _parse_time(end_time), sample, wavelength)


def hmiexport(series, start_time, end_time, sample=None):
    """The JSOC record set for the HMI ``series`` (a key of HMI_SERIES)."""
    if series not in HMI_SERIES:
        print(f"Error: Invalid HMI series '{series}'. Use one of: {', '.join(HMI_SERIES)}")
        return None
    return _jsoc_record(HMI_SERIES[series], _parse_time(start_time), _parse_time(end_time), sample)


def aia_cadence(wavelength):
    """The AIA series cadence ('12s', '24s' or '1h') that has ``wavelength``."""
    for cadence, wavelengths in WAVELENGTHS.items():
        if str(wavelength) in wavelengths:
            return cadence
    return None


def get_key(val, my_dict):
    """The key of ``my_dict`` whose value is ``val``, or None."""
    for key, value in my_dict.items():
        if val == value:
            return key
    return None


def get_time_list(start_time, end_time, interval_seconds=0.5):
    """'HH:MM:SS' strings from ``start_time`` to ``end_time`` every
    ``interval_seconds``."""
    stt, ett = _parse_time(start_time), _parse_time(end_time)
    times = []
    while stt <= ett:
        times.append(stt.strftime("%H:%M:%S"))
        stt += timedelta(seconds=interval_seconds)
    return times


# ----------------------------------------------------------- map preparation
def _roll_degrees(smap):
    """How far the map's pixel grid is turned from solar north, in degrees."""
    pc = smap.rotation_matrix
    return float(np.degrees(np.arctan2(pc[1, 0], pc[0, 0])))


def _north_up(smap, steps):
    """The map turned so that solar north is up and its reference
    coordinate (the Sun's centre for full-disk and coronagraph images) sits
    at the image centre. The map's own rotation matrix is what rotate()
    undoes when it is given no angle; the header then records no roll.
    Maps already north up are only re-centred when the reference pixel is
    off centre."""
    from sunpy.map import Map

    roll = _roll_degrees(smap)
    data = smap.data if smap.data.dtype.kind == "f" else smap.data.astype(np.float32)
    smap = Map(data, smap.meta)
    if abs(roll) > 0.01:
        turned = smap.rotate(recenter=True, missing=np.nan, order=3)
        steps.append(f"Rotated {-roll:.3f} deg to solar north, reference coordinate centred")
        return turned
    centre = ((smap.data.shape[1] - 1) / 2, (smap.data.shape[0] - 1) / 2)
    ref = (smap.reference_pixel.x.value, smap.reference_pixel.y.value)
    if abs(centre[0] - ref[0]) > 0.5 or abs(centre[1] - ref[1]) > 0.5:
        turned = smap.rotate(recenter=True, missing=np.nan, order=3)
        steps.append("Reference coordinate centred")
        return turned
    return smap


def _is_per_second(unit):
    unit = str(unit or "").replace(" ", "")
    return "/s" in unit or unit.endswith("s-1") or unit.endswith("s^-1")


def _per_second(smap, steps, exptime=None):
    """The map in counts per second, unless it already is or has no
    exposure time."""
    from sunpy.map import Map

    unit = str(smap.meta.get("bunit", "") or "")
    if _is_per_second(unit):
        return smap
    if exptime is None:
        try:
            exptime = float(smap.exposure_time.to_value(u.s))
        except Exception:
            return smap
    if not np.isfinite(exptime) or exptime <= 0:
        return smap
    meta = dict(smap.meta)
    meta["bunit"] = f"{unit or 'DN'}/s"
    steps.append(f"Divided by the exposure time ({exptime:.4g} s)")
    return Map(np.asarray(smap.data, dtype=np.float32) / np.float32(exptime), meta)


def _standard_header(smap, steps, extra=None):
    """The map with a header SolarViewer reads at face value: an ISO
    DATE-OBS (SOHO splits date and time), HPLN-TAN/HPLT-TAN axes in arcsec
    (SOHO writes SOLAR-X), and a HISTORY of what was done."""
    from sunpy.map import Map

    meta = dict(smap.meta)
    try:
        meta["date-obs"] = smap.date.isot
    except Exception:
        pass
    for key in ("time-obs", "date_obs"):
        meta.pop(key, None)
    if str(meta.get("ctype1", "")).upper().startswith("SOLAR"):
        meta["ctype1"], meta["ctype2"] = "HPLN-TAN", "HPLT-TAN"
    for key in ("cunit1", "cunit2"):
        if str(meta.get(key, "")).upper() == "ARCSEC":
            meta[key] = "arcsec"
    if extra:
        meta.update(extra)
    history = meta.get("history")
    lines = list(history) if isinstance(history, (list, tuple)) else str(history).splitlines() if history else []
    lines += [f"SolarViewer: {step}" for step in steps]
    meta["history"] = "\n".join(lines)
    return Map(smap.data, meta)


def _save_map(smap, path):
    """Save ``smap`` as a plain FITS file: 32-bit floats in the primary HDU."""
    from sunpy.map import Map

    data = smap.data
    if data.dtype != np.float32:
        data = data.astype(np.float32)
    Map(data, smap.meta).save(path, overwrite=True)
    return path


def _strip_fits(name):
    for ending in (".fits.gz", ".fts.gz", ".fits.fz", ".fits", ".fts", ".fit"):
        if name.lower().endswith(ending):
            return name[: -len(ending)]
    return name


def _out_name(path, output_dir, suffix="_prepped"):
    """The output path for the prepared version of ``path``."""
    return os.path.join(output_dir, f"{_strip_fits(os.path.basename(path))}{suffix}.fits")


def _clean_header(header):
    """A copy of a FITS ``header`` without the cards astropy cannot parse
    (SUVI level 1b has a broken CONTINUE card) and without comments."""
    clean = fits.Header()
    for card in header.cards:
        try:
            value = card.value
        except Exception:
            continue
        if card.keyword in ("", "COMMENT", "HISTORY", "CONTINUE"):
            continue
        try:
            clean[card.keyword] = (value, card.comment)
        except Exception:
            continue
    return clean


def _read_map(path):
    """``path`` as a sunpy Map, reading the first HDU with an image (SUVI
    level 1b and HMI keep theirs in the first extension) and cleaning the
    header when sunpy cannot read the file as it is."""
    from sunpy.map import Map

    try:
        return Map(path)
    except Exception:
        pass
    with fits.open(path) as hdul:
        for hdu in hdul:
            if hdu.data is not None and getattr(hdu.data, "ndim", 0) >= 2:
                return Map(np.array(hdu.data), _clean_header(hdu.header))
    raise ValueError(f"No image in {os.path.basename(path)}")


# per-instrument preparation ------------------------------------------------
def _prepare_generic(path, output_dir, options, per_second):
    """North up, reference coordinate centred, per second when asked,
    standard header."""
    steps = []
    smap = _read_map(path)
    if per_second:
        smap = _per_second(smap, steps)
    smap = _north_up(smap, steps)
    smap = _standard_header(smap, steps)
    return [_save_map(smap, _out_name(path, output_dir))]


def _prepare_aia(path, output_dir, options):
    """Level 1 to level 1.5 with aiapy."""
    from sunpy.map import Map

    steps = []
    smap = Map(path)
    if options.get("pointing_table") is not None:
        try:
            smap = update_pointing(smap, pointing_table=options["pointing_table"])
            steps.append("Pointing updated from the JSOC master pointing table")
        except Exception as e:
            print(f"  - Warning: could not update the pointing: {e}", flush=True)
    if options.get("apply_psf"):
        smap = aia_deconvolve(smap, iterations=25)
        steps.append("PSF deconvolved (Richardson-Lucy, 25 iterations)")
    smap = register(smap)
    steps.append("Registered: north up, 0.6 arcsec/pixel, Sun centred (level 1.5)")
    if options.get("apply_degradation", True):
        try:
            smap = correct_degradation(smap, correction_table=options.get("correction_table"))
            steps.append("Corrected for the instrument degradation")
        except Exception as e:
            print(f"  - Warning: could not correct the degradation: {e}", flush=True)
    if options.get("apply_exposure_norm", True):
        smap = _per_second(smap, steps)
    smap = _standard_header(smap, steps, {"lvl_num": 1.5})
    base = os.path.basename(path)
    if re.search(r"lev1(\.fits|\.fits\.gz|\.fts)$", base):
        out = os.path.join(output_dir, re.sub(r"lev1(\.fits|\.fits\.gz|\.fts)$", "lev1.5.fits", base))
    else:
        out = _out_name(path, output_dir, "_lev1.5")
    return [_save_map(smap, out)]


def _prepare_iris_sji(path, output_dir, options):
    """One 2-D file per slit-jaw exposure of an IRIS level 2 cube, each with
    its own time, exposure time and pointing from the cube's auxiliary
    table, in DN/s and turned north up. Exposures with no data are skipped,
    and with a cadence set only one exposure per interval is kept."""
    from sunpy.map import Map

    outputs = []
    sample = float(options.get("sample") or 0)
    with fits.open(path) as hdul:
        header = hdul[0].header
        cube = hdul[0].data
        if cube is None or cube.ndim != 3:
            raise ValueError("not an IRIS slit-jaw cube")
        aux, columns = None, {}
        if len(hdul) > 1 and hdul[1].data is not None and getattr(hdul[1].data, "ndim", 0) == 2:
            aux = np.asarray(hdul[1].data, dtype=float)
            skip = ("NAXIS", "NAXIS1", "NAXIS2", "BITPIX", "PCOUNT", "GCOUNT")
            columns = {
                k: int(hdul[1].header[k])
                for k in hdul[1].header
                if k not in skip and isinstance(hdul[1].header[k], (int, np.integer))
            }
        start = None
        for key in ("STARTOBS", "DATE_OBS", "DATE-OBS"):  # TIME counts from STARTOBS
            if header.get(key):
                try:
                    start = datetime.fromisoformat(str(header[key]).rstrip("Z"))
                    break
                except ValueError:
                    continue
        base = _strip_fits(os.path.basename(path))
        frame_header = _clean_header(header)
        for key in ("NAXIS3", "CTYPE3", "CUNIT3", "CRVAL3", "CRPIX3", "CDELT3", "PC3_1", "PC3_2", "PC3_3", "PC1_3", "PC2_3"):
            frame_header.remove(key, ignore_missing=True)
        frame_header["NAXIS"] = 2
        for key in ("CUNIT1", "CUNIT2"):
            if not frame_header.get(key):
                frame_header[key] = "arcsec"

        def column(name, i):
            if aux is None or name not in columns or columns[name] >= aux.shape[1]:
                return None
            value = aux[i, columns[name]]
            return float(value) if np.isfinite(value) else None

        last_kept = None
        for i in range(cube.shape[0]):
            frame = cube[i]
            if not np.any(frame > -199):  # a missing exposure is all -200
                continue
            seconds = column("TIME", i)
            when = start + timedelta(seconds=seconds) if (start is not None and seconds is not None) else None
            if sample and when is not None and last_kept is not None:
                if (when - last_kept).total_seconds() < sample - 0.5:
                    continue
            if when is not None:
                last_kept = when
            meta = frame_header.copy()
            if when is not None:
                meta["DATE-OBS"] = when.isoformat(timespec="milliseconds")
                meta["DATE_OBS"] = meta["DATE-OBS"]
            exptime = column("EXPTIMES", i)
            if exptime is not None and exptime > 0:
                meta["EXPTIME"] = exptime
            xcen, ycen = column("XCENIX", i), column("YCENIX", i)
            if xcen is not None and ycen is not None and (xcen, ycen) != (0.0, 0.0):
                meta["CRVAL1"], meta["CRVAL2"] = xcen, ycen
                meta["XCEN"], meta["YCEN"] = xcen, ycen
            pc = [column(k, i) for k in ("PC1_1IX", "PC1_2IX", "PC2_1IX", "PC2_2IX")]
            if all(v is not None for v in pc) and any(v != 0 for v in pc):
                meta["PC1_1"], meta["PC1_2"], meta["PC2_1"], meta["PC2_2"] = pc
            meta["FRAME"] = i
            data = np.array(frame, dtype=np.float32)
            data[data <= -199] = np.nan
            steps = [f"Exposure {i} of the IRIS level 2 cube {os.path.basename(path)}"]
            smap = Map(data, meta)
            smap = _per_second(smap, steps, exptime)
            smap = _north_up(smap, steps)
            smap = _standard_header(smap, steps)
            outputs.append(_save_map(smap, os.path.join(output_dir, f"{base}_frame{i:04d}.fits")))
    return outputs


def _prepare_secchi(paths, output_dir, options):
    """STEREO COR1, COR2 and EUVI calibrated by secchi_calibration.py: a
    COR polarizer triplet becomes one total-brightness image in mean solar
    brightness with the team's monthly minimum background in a BACKGROUND
    extension; EUVI becomes counts per second with the bias, filter and
    flat corrected; everything north up. When the calibration files
    cannot be fetched or the file is not one the port handles, the images
    are prepared as before (north up, counts per second; a triplet as 2/3
    of the sum of its images) and the HISTORY says so."""
    from . import secchi_calibration

    paths = [paths] if isinstance(paths, str) else list(paths)
    try:
        return secchi_calibration.calibrate_files(paths, output_dir, warp=not options.get("no_warp"))
    except secchi_calibration.CalibrationError as e:
        print(f"  Warning: {', '.join(os.path.basename(p) for p in paths)} not calibrated ({e}); saving counts per second instead", flush=True)
        return _prepare_cor_group(paths, output_dir, options, note=f"Not calibrated to mean solar brightness: {e}")


def _prepare_cor_group(paths, output_dir, options, note=None):
    """The fallback for SECCHI files: a COR1/COR2 polarizer triplet (0, 120
    and 240 degrees) combined into total brightness, 2/3 of the sum of the
    three per-second images (Billings 1966), north up; a lone image is
    prepared on its own."""
    from sunpy.map import Map

    paths = sorted(paths)
    notes = [note] if note else []
    if len(paths) != 3:
        outputs = []
        for p in paths:
            steps = list(notes)
            smap = _read_map(p)
            smap = _per_second(smap, steps)
            smap = _north_up(smap, steps)
            smap = _standard_header(smap, steps)
            outputs.append(_save_map(smap, _out_name(p, output_dir)))
        return outputs
    maps = [_per_second(_read_map(p), []) for p in paths]
    reference = maps[1]
    if not all(m.data.shape == reference.data.shape for m in maps):
        return [out for p in paths for out in _prepare_cor_group([p], output_dir, options, note)]
    total = (2.0 / 3.0) * sum(np.asarray(m.data, dtype=np.float64) for m in maps)
    meta = dict(reference.meta)
    meta.pop("polar", None)
    steps = notes + [
        "Total brightness: 2/3 of the sum of the polarizer images at 0, 120 and 240 degrees ("
        + ", ".join(os.path.basename(p) for p in paths)
        + "), each divided by its exposure time"
    ]
    smap = Map(total.astype(np.float32), meta)
    smap = _north_up(smap, steps)
    smap = _standard_header(smap, steps)
    stem = re.sub(r"_[a-z]\dc[12][AB]$", "", _strip_fits(os.path.basename(paths[0])))
    detector = str(reference.meta.get("detector", "cor")).lower()
    craft = str(reference.meta.get("obsrvtry", "STEREO_A"))[-1].upper()
    return [_save_map(smap, os.path.join(output_dir, f"{stem}_{detector}{craft}_total_brightness.fits"))]


def _cor_groups(paths):
    """COR images grouped into polarizer triplets taken within three minutes
    of each other at 0, 120 and 240 degrees; the rest as single files."""
    from .secchi_calibration import group_files

    return group_files(paths)


def _prepare_lasco(path, output_dir, options):
    """LASCO C2 and C3 calibrated to mean solar brightness (NRL level 1:
    bias, exposure, calibration factor, vignetting, C3 ramp and mask,
    distortion) and turned north up, with NRL's monthly minimum background
    in a BACKGROUND extension, by lasco_calibration.py. When the
    calibration files cannot be fetched, the image is prepared like the
    other SOHO data (north up, counts per second) and the HISTORY says so."""
    from . import lasco_calibration

    try:
        return [lasco_calibration.calibrate_file(path, output_dir, warp=not options.get("no_warp"))]
    except lasco_calibration.CalibrationError as e:
        print(f"  Warning: {os.path.basename(path)} not calibrated ({e}); saving counts per second instead", flush=True)
        steps = [f"Not calibrated to mean solar brightness: {e}"]
        smap = _read_map(path)
        smap = _per_second(smap, steps)
        smap = _north_up(smap, steps)
        smap = _standard_header(smap, steps)
        return [_save_map(smap, _out_name(path, output_dir))]


PREPARERS = {
    "aia": _prepare_aia,
    "hmi": lambda p, d, o: _prepare_generic(p, d, o, per_second=False),
    "eit": lambda p, d, o: _prepare_generic(p, d, o, per_second=True),
    "lasco": _prepare_lasco,
    "mdi": lambda p, d, o: _prepare_generic(p, d, o, per_second=False),
    "suvi": lambda p, d, o: _prepare_generic(p, d, o, per_second=False),
    "euvi": _prepare_secchi,
    "cor": _prepare_secchi,
    "iris_sji": _prepare_iris_sji,
}


def _prepare_one(kind, item, output_dir, options):
    """Prepare one downloaded file (or file group) of ``kind``; returns
    (the output paths or None, the lines to print). Runs in a worker
    process."""
    _quiet()
    name = ", ".join(os.path.basename(p) for p in (item if isinstance(item, list) else [item]))
    try:
        outputs = PREPARERS[kind](item, output_dir, options)
        return outputs, [f"Processed {name} -> {', '.join(os.path.basename(o) for o in outputs)}"]
    except Exception as e:
        return None, [f"Warning: could not process {name}: {e}"]


def prepare_files(kind, items, output_dir, options=None, keep_originals=False, workers=None):
    """Prepare the downloaded ``items`` (paths, or lists of paths for file
    groups) of ``kind`` for SolarViewer, several at a time in worker
    processes. Returns the output paths; an item that fails stays as it was
    downloaded and its path is returned instead. The originals are deleted
    unless ``keep_originals``."""
    options = dict(options or {})
    items = list(items)
    if not items:
        return []
    wanted = options.pop("workers", None) or 4
    if workers is None:
        workers = max(1, min(len(items), wanted, (os.cpu_count() or 2) // 2))
    results = [None] * len(items)
    many = f" ({workers} at a time)" if workers > 1 else ""
    print(f"Processing {len(items)} files for SolarViewer{many}...", flush=True)
    done = 0

    def finish(i, result):
        nonlocal done
        done += 1
        outputs, lines = result
        for line in lines:
            print(f"  [{done}/{len(items)}] {line}", flush=True)
        results[i] = outputs

    if workers > 1 and len(items) > 1:
        try:
            import multiprocessing
            from concurrent.futures import ProcessPoolExecutor

            context = multiprocessing.get_context("spawn")
            with ProcessPoolExecutor(max_workers=workers, mp_context=context) as pool:
                futures = {pool.submit(_prepare_one, kind, item, output_dir, options): i for i, item in enumerate(items)}
                for future in futures:
                    finish(futures[future], future.result())
        except Exception as e:
            print(f"  Worker processes failed ({e}); processing one by one.", flush=True)
            results, done = [None] * len(items), 0
            for i, item in enumerate(items):
                finish(i, _prepare_one(kind, item, output_dir, options))
    else:
        for i, item in enumerate(items):
            finish(i, _prepare_one(kind, item, output_dir, options))

    final = []
    for item, outputs in zip(items, results):
        originals = item if isinstance(item, list) else [item]
        if outputs is None:
            final.extend(originals)
            continue
        final.extend(outputs)
        if not keep_originals:
            for p in originals:
                if p not in outputs and os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
    print(f"Successfully processed {sum(o is not None for o in results)} of {len(items)} files.", flush=True)
    return final


def _ensure_dir(path):
    if not os.path.isdir(path):
        os.makedirs(path)


def _unpack_archives(paths, output_dir):
    """FITS files inside any tar archives among ``paths`` (MDI arrives that
    way) moved next to the others; the archives are removed."""
    out = []
    for p in paths:
        if not re.search(r"\.(tar|tar\.gz|tgz)$", p, re.I):
            out.append(p)
            continue
        try:
            with tarfile.open(p, "r:*") as tar:
                for member in tar.getmembers():
                    if not member.isfile() or not re.search(r"\.(fits|fts|fit)(\.gz)?$", member.name, re.I):
                        continue
                    target = os.path.join(output_dir, os.path.basename(member.name))
                    with tar.extractfile(member) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    out.append(target)
            os.remove(p)
        except Exception as e:
            print(f"Warning: could not unpack {os.path.basename(p)}: {e}")
            out.append(p)
    return out


def _fits_extension(paths):
    """Downloaded files renamed to end in .fits when they are FITS files
    without an extension (SDAC's EIT level 0 files)."""
    out = []
    for p in paths:
        if re.search(r"\.(fits|fts|fit|fz|gz|tar|tgz)$", p, re.I):
            out.append(p)
            continue
        try:
            with fits.open(p):
                pass
            os.rename(p, p + ".fits")
            out.append(p + ".fits")
        except Exception:
            out.append(p)
    return out


def _print_search(label, start_time, end_time, sample):
    cadence = f", one image every {int(sample)} s" if sample else ""
    print(f"Searching for {label} data from {start_time} to {end_time}{cadence}", flush=True)


def _no_data(label, start_time, end_time):
    print("\n" + "=" * 60 + "\nNO DATA FOUND\n" + "=" * 60)
    print(f"  Time range: {start_time} to {end_time}\n  {label}")
    print("\nPossible causes:\n  - No observations in this time range\n  - The provider may be temporarily unavailable")
    if _parse_time(end_time) > datetime.now():
        print("  - NOTE: End time is in the future!")


def _found(result):
    """Print and return how many files a Fido result (or one of its tables) lists."""
    total = result.file_num if hasattr(result, "file_num") else len(result)
    if total:
        print(f"Found {total} files. Downloading...", flush=True)
    return total


def _thin_rows(table, sample, hold=0):
    """Row indices of a Fido result ``table`` keeping one image (or one
    sequence of up to ``hold`` seconds) every ``sample`` seconds."""
    keep, last = [], None
    for i, row in enumerate(table):
        when = row["Start Time"].to_datetime()
        if last is None or (when - last).total_seconds() >= float(sample) - 0.5:
            last = when
        if (when - last).total_seconds() <= hold:
            keep.append(i)
    return keep


# ------------------------------------------------------------------ SDO / AIA
def _aia_tables(start_dt, end_dt, apply_degradation):
    """The JSOC pointing table and degradation correction table for the time
    range, fetched once for all the files (None where unavailable)."""
    pointing_table = correction_table = None
    for source in ("jsoc", "lmsal"):
        try:
            pointing_table = get_pointing_table(
                source, time_range=(start_dt - timedelta(hours=12), end_dt + timedelta(hours=12))
            )
            break
        except Exception:
            continue
    if pointing_table is None:
        print("  - Warning: the pointing table could not be fetched; the header pointing is kept")
    if apply_degradation:
        for source in ("JSOC", "SSW"):
            try:
                correction_table = get_correction_table(source)
                break
            except Exception:
                continue
        if correction_table is None:
            print("  - Warning: the degradation table could not be fetched; aiapy will try itself per file")
    return pointing_table, correction_table


def _finish_aia(files, output_dir, start_dt, end_dt, skip_calibration, apply_psf, apply_degradation, apply_exposure_norm, keep_originals):
    files = [str(p) for p in files]
    if skip_calibration or not files:
        if files:
            print(f"Successfully downloaded {len(files)} AIA level 1 files.", flush=True)
        return files
    if not HAS_AIAPY:
        print("  Calibration: DISABLED (aiapy not installed; pip install aiapy)")
        return files
    print("  Calibration: ENABLED (aiapy level 1.5)", flush=True)
    pointing_table, correction_table = _aia_tables(start_dt, end_dt, apply_degradation)
    options = dict(
        pointing_table=pointing_table,
        correction_table=correction_table,
        apply_psf=apply_psf,
        apply_degradation=apply_degradation,
        apply_exposure_norm=apply_exposure_norm,
        workers=1 if apply_psf else 2,  # a 4k image needs about 1 GB while it is registered
    )
    return prepare_files("aia", files, output_dir, options, keep_originals)


def download_aia_with_fido(
    wavelength,
    start_time,
    end_time,
    output_dir,
    skip_calibration=False,
    apply_psf=False,
    apply_degradation=True,
    apply_exposure_norm=True,
    sample=None,
    keep_originals=False,
):
    """Download AIA ``wavelength`` images through the VSO (no email needed)
    and prepare them to level 1.5. ``sample`` is the cadence in seconds
    (every image when None). Returns the output paths."""
    from sunpy.net import Fido, attrs as a

    _quiet()
    prefer_ipv4()
    _ensure_dir(output_dir)
    start_dt, end_dt = _parse_time(start_time), _parse_time(end_time)
    label = f"AIA {wavelength}Å"
    _print_search(label, start_time, end_time, sample)
    try:
        result = Fido.search(
            a.Time(start_dt, end_dt),
            a.Instrument("AIA"),
            a.Wavelength(int(wavelength) * u.angstrom),
            *_sample_attrs(sample),
        )
        if not _found(result):
            _no_data(label, start_time, end_time)
            return []
        downloaded = robust_fido_fetch(result, output_dir)
    except Exception as e:
        print(f"Error during Fido search/fetch: {e}")
        return []
    if downloaded.errors and len(downloaded) == 0:
        print("The VSO's AIA export is not answering; the JSOC method (an email registered at JSOC) is the alternative.")
    return _finish_aia(downloaded, output_dir, start_dt, end_dt, skip_calibration, apply_psf, apply_degradation, apply_exposure_norm, keep_originals)


def _jsoc_export(record, email, output_dir, segment=None):
    """Export ``record`` from JSOC as FITS files with full headers and
    download them in parallel. Returns the paths."""
    import drms

    prefer_ipv4()
    if not email:
        print("Error: an email address registered at JSOC is required for JSOC exports.")
        print("Register at http://jsoc.stanford.edu/ajax/register_email.html, or use the Fido method.")
        return []
    client = drms.Client(email=email)
    print(f"Requesting a JSOC export of {record}", flush=True)
    try:
        response = client.export(record, method="url", protocol="fits")
        print("Waiting for the JSOC export to be ready...", flush=True)
        response.wait()
        if response.status != 0:
            print(f"Export failed with status {response.status}")
            return []
        table = response.urls
    except Exception as e:
        print(f"Error during the JSOC export: {e}")
        return []
    if table is None or len(table) == 0:
        print("No data returned from the JSOC export.")
        return []
    if segment:
        table = table[table["filename"].str.contains(segment)]
    urls, names = list(table["url"]), list(table["filename"])
    print(f"Found {len(urls)} files. Downloading...", flush=True)
    return _fetch_urls(urls, output_dir, names)


def download_aia(
    wavelength,
    cadence=None,
    start_time=None,
    end_time=None,
    output_dir=None,
    email=None,
    interval_seconds=None,
    skip_calibration=False,
    sample=None,
    apply_psf=False,
    apply_degradation=True,
    apply_exposure_norm=True,
    keep_originals=False,
):
    """Download AIA ``wavelength`` images with a JSOC export (``email`` must
    be registered at JSOC) and prepare them to level 1.5. ``cadence`` is
    the series ('12s', '24s', '1h'), found from the wavelength when None;
    ``sample`` the cadence in seconds. Returns the output paths."""
    _quiet()
    _ensure_dir(output_dir)
    cadence = cadence or aia_cadence(wavelength)
    if sample is None and interval_seconds:
        sample = interval_seconds
    record = aiaexport(wavelength, cadence, start_time, end_time, sample)
    if record is None:
        return []
    files = _jsoc_export(record, email, output_dir, segment="image")
    start_dt, end_dt = _parse_time(start_time), _parse_time(end_time)
    return _finish_aia(files, output_dir, start_dt, end_dt, skip_calibration, apply_psf, apply_degradation, apply_exposure_norm, keep_originals)


# ------------------------------------------------------------------ SDO / HMI
def _finish_hmi(files, output_dir, skip_calibration, keep_originals):
    files = [str(p) for p in files]
    if skip_calibration or not files:
        if files:
            print(f"Successfully downloaded {len(files)} HMI files.", flush=True)
        return files
    print("  Processing: north up, Sun centred, native 0.504 arcsec/pixel", flush=True)
    return prepare_files("hmi", files, output_dir, {"workers": 2}, keep_originals)


def download_hmi_with_fido(series, start_time, end_time, output_dir, skip_calibration=False, sample=None, keep_originals=False):
    """Download an HMI 45 s ``series`` ('M_45s', 'Ic_45s', 'V_45s') through
    the VSO and turn the images north up. The 720 s series are not at the
    VSO; use download_hmi (JSOC) for them."""
    from sunpy.net import Fido, attrs as a

    _quiet()
    prefer_ipv4()
    _ensure_dir(output_dir)
    series = str(series)
    if "720" in series:
        print(f"Error: the VSO serves only the 45 s HMI series; {series} needs the JSOC method (an email).")
        return []
    physobs = HMI_PHYSOBS.get(series.split("_")[0], "LOS_magnetic_field")
    start_dt, end_dt = _parse_time(start_time), _parse_time(end_time)
    label = f"HMI {series}"
    _print_search(label, start_time, end_time, sample)
    try:
        result = Fido.search(
            a.Time(start_dt, end_dt), a.Instrument("HMI"), a.Physobs(physobs), *_sample_attrs(sample)
        )
        if not _found(result):
            _no_data(label, start_time, end_time)
            return []
        downloaded = robust_fido_fetch(result, output_dir)
    except Exception as e:
        print(f"Error during Fido search/fetch: {e}")
        return []
    return _finish_hmi(downloaded, output_dir, skip_calibration, keep_originals)


def download_hmi(series, start_time, end_time, output_dir, email=None, interval_seconds=None, skip_calibration=False, sample=None, keep_originals=False):
    """Download an HMI ``series`` (a key of HMI_SERIES) with a JSOC export and
    turn the images north up."""
    _quiet()
    _ensure_dir(output_dir)
    if sample is None and interval_seconds:
        sample = interval_seconds
    record = hmiexport(series, start_time, end_time, sample)
    if record is None:
        return []
    files = _jsoc_export(record, email, output_dir)
    return _finish_hmi(files, output_dir, skip_calibration, keep_originals)


def update_hmi_pointing(hmi_map):
    """An HMI map turned north up with the Sun centred (the old name)."""
    return _north_up(hmi_map, [])


# ----------------------------------------------------------------------- IRIS
def _row_wavelength(row):
    """The wavelength (Å) a VSO result row names, as an int, or None."""
    try:
        value = row["Wavelength"]
        value = value[0] if getattr(value, "shape", ()) else value
        return int(round(float(getattr(value, "value", value))))
    except Exception:
        return None


def download_iris(start_time, end_time, output_dir, obs_type="SJI", wavelength=None, skip_calibration=False, sample=None, keep_originals=False):
    """Download IRIS level 2 slit-jaw cubes (``wavelength`` 1330, 1400, 2796
    or 2832 Å, any when None) and split them into one north-up image per
    exposure. Rasters are spectra, which SolarViewer does not show, so only
    slit-jaw images are offered. ``sample`` thins the exposures to one per
    that many seconds."""
    from sunpy.net import Fido, attrs as a

    _quiet()
    prefer_ipv4()
    _ensure_dir(output_dir)
    if str(obs_type).lower() != "sji":
        print("Only IRIS slit-jaw images (SJI) are supported: rasters are spectra, not images.")
        return []
    start_dt, end_dt = _parse_time(start_time), _parse_time(end_time)
    label = "IRIS SJI" + (f" {wavelength}Å" if wavelength else "")
    _print_search(label, start_time, end_time, sample)
    try:
        query = [a.Time(start_dt, end_dt), a.Instrument("IRIS"), a.Physobs("intensity")]
        if wavelength:
            query.append(a.Wavelength(int(wavelength) * u.angstrom))
        result = Fido.search(*query)
        if not len(result):
            _no_data(label, start_time, end_time)
            return []
        # Each row is a whole observation's cube; drop the raster archives
        table = result[0]
        rows = [i for i, row in enumerate(table) if not str(row["fileid"]).lower().startswith("raster")]
        if wavelength:
            rows = [i for i in rows if _row_wavelength(table[i]) == int(wavelength)]
        if not rows:
            _no_data(label, start_time, end_time)
            return []
        print(f"Found {len(rows)} files. Downloading...", flush=True)
        downloaded = robust_fido_fetch(result[0, rows], output_dir)
    except Exception as e:
        print(f"Error during Fido search/fetch: {e}")
        return []
    files = [str(p) for p in downloaded]
    if skip_calibration or not files:
        if files:
            print(f"Successfully downloaded {len(files)} IRIS files.", flush=True)
        return files
    print("  Processing: one file per exposure, counts per second, north up", flush=True)
    return prepare_files("iris_sji", files, output_dir, {"sample": sample, "workers": 2}, keep_originals)


# ----------------------------------------------------------------------- SOHO
def download_soho(instrument, start_time, end_time, output_dir, wavelength=None, detector=None, skip_calibration=False, sample=None, physobs=None, keep_originals=False):
    """Download SOHO data through the VSO and prepare it: EIT (``wavelength``
    171, 195, 284 or 304; the level 1 product when SDAC has it; counts per
    second otherwise), LASCO (``detector`` C2 or C3; calibrated to mean
    solar brightness) or MDI (``physobs`` 'magnetogram' or 'continuum').
    Everything is turned north up."""
    from sunpy.net import Fido, attrs as a

    _quiet()
    prefer_ipv4()
    _ensure_dir(output_dir)
    instrument = str(instrument).upper()
    if instrument not in ("EIT", "LASCO", "MDI"):
        print(f"Error: Invalid SOHO instrument '{instrument}'. Use 'EIT', 'LASCO', or 'MDI'.")
        return []
    start_dt, end_dt = _parse_time(start_time), _parse_time(end_time)
    query = [a.Time(start_dt, end_dt), a.Instrument(instrument), *_sample_attrs(sample)]
    label = f"SOHO/{instrument}"
    if instrument == "EIT":
        query.append(a.Provider("SDAC"))
        if wavelength:
            query.append(a.Wavelength(int(wavelength) * u.angstrom))
            label += f" {wavelength}Å"
    elif instrument == "LASCO":
        if detector:
            query.append(a.Detector(str(detector).upper()))
            label += f" {str(detector).upper()}"
    elif instrument == "MDI" and physobs:
        continuum = any(s in str(physobs).lower() for s in ("cont", "int"))
        query.append(a.Physobs("intensity" if continuum else "LOS_magnetic_field"))
        label += " continuum" if continuum else " magnetogram"
    _print_search(label, start_time, end_time, sample)
    try:
        result = Fido.search(*query)
        if instrument == "EIT" and len(result):
            # SDAC lists level 0 (lz) and level 1 (l1) of each image: take
            # the calibrated level 1 where it exists
            table = result[0]
            l1 = [i for i, row in enumerate(table) if "/l1/" in str(row["fileid"])]
            if l1:
                print(f"  Taking the {len(l1)} EIT level 1 files (calibrated) of {len(table)} listed", flush=True)
                result = result[0, l1]
        if not _found(result):
            _no_data(label, start_time, end_time)
            return []
        downloaded = robust_fido_fetch(result, output_dir)
    except Exception as e:
        print(f"Error during Fido search/fetch: {e}")
        return []
    files = _unpack_archives(_fits_extension([str(p) for p in downloaded]), output_dir)
    if skip_calibration or not files:
        if files:
            print(f"Successfully downloaded {len(files)} SOHO/{instrument} files.", flush=True)
        return files
    kind = instrument.lower()
    if kind == "lasco":
        print("  Processing: calibrated to mean solar brightness (NRL level 1), distortion removed, north up, F corona background in a second HDU", flush=True)
    else:
        per_second = ", counts per second" if kind == "eit" else ""
        print(f"  Processing: north up, Sun centred{per_second}", flush=True)
    return prepare_files(kind, files, output_dir, {}, keep_originals)


# ------------------------------------------------------------------ GOES SUVI
def download_goes_suvi(start_time, end_time, output_dir, wavelength=None, level="2", skip_calibration=False, sample=None, keep_originals=False):
    """Download GOES SUVI images from NOAA (``wavelength`` 94, 131, 171, 195,
    284 or 304 Å; ``level`` '2' composites or '1b' single exposures) and
    prepare them: level 2 goes into the primary HDU as it is, level 1b is
    turned north up."""
    from sunpy.net import Fido, attrs as a

    _quiet()
    prefer_ipv4()
    _ensure_dir(output_dir)
    start_dt, end_dt = _parse_time(start_time), _parse_time(end_time)
    label = f"GOES SUVI level {level}" + (f" {wavelength}Å" if wavelength else "")
    _print_search(label, start_time, end_time, sample)
    try:
        query = [a.Time(start_dt, end_dt), a.Instrument("SUVI"), a.Level(str(level))]
        if wavelength:
            wl = int(wavelength)
            query.append(a.Wavelength((wl - 1) * u.angstrom, (wl + 1) * u.angstrom))
        result = Fido.search(*query)
        if sample and len(result):  # NOAA's client has no cadence attribute
            result = result[0, _thin_rows(result[0], sample)]
        if not _found(result):
            _no_data(label, start_time, end_time)
            return []
        downloaded = robust_fido_fetch(result, output_dir)
    except Exception as e:
        print(f"Error during Fido search/fetch: {e}")
        return []
    files = [str(p) for p in downloaded]
    if skip_calibration or not files:
        if files:
            print(f"Successfully downloaded {len(files)} GOES SUVI files.", flush=True)
        return files
    print("  Processing: image into the primary HDU" + (", north up" if str(level) != "2" else ""), flush=True)
    return prepare_files("suvi", files, output_dir, {}, keep_originals)


# --------------------------------------------------------------------- STEREO
def download_stereo(start_time, end_time, output_dir, spacecraft="A", instrument="EUVI", wavelength=None, skip_calibration=False, sample=None, keep_originals=False):
    """Download STEREO SECCHI data through the VSO: EUVI (``wavelength`` 171,
    195, 284 or 304 Å) with its bias, filter and flat corrected, DN/s,
    north up; or COR1/COR2 whose polarizer triplets become total-brightness
    images in mean solar brightness, north up, with the team's monthly
    minimum background in a second HDU (secchi_calibration.py).
    STEREO-B ended in 2014."""
    from sunpy.net import Fido, attrs as a

    _quiet()
    prefer_ipv4()
    _ensure_dir(output_dir)
    start_dt, end_dt = _parse_time(start_time), _parse_time(end_time)
    spacecraft, instrument = str(spacecraft).upper(), str(instrument).upper()
    source = f"STEREO_{spacecraft}"
    label = f"{source}/{instrument}" + (f" {wavelength}Å" if instrument == "EUVI" and wavelength else "")
    _print_search(label, start_time, end_time, sample)
    try:
        query = [a.Time(start_dt, end_dt), a.Source(source), a.Instrument("SECCHI"), a.Detector(instrument)]
        if instrument == "EUVI":
            if wavelength:
                query.append(a.Wavelength(int(wavelength) * u.angstrom))
            query += _sample_attrs(sample)
        result = Fido.search(*query)
        if instrument != "EUVI" and sample and len(result):
            # keep whole polarizer sequences (three images within three minutes)
            result = result[0, _thin_rows(result[0], sample, hold=180)]
        if not _found(result):
            _no_data(label, start_time, end_time)
            return []
        downloaded = robust_fido_fetch(result, output_dir)
    except Exception as e:
        print(f"Error during Fido search/fetch: {e}")
        return []
    files = [str(p) for p in downloaded]
    if skip_calibration or not files:
        if files:
            print(f"Successfully downloaded {len(files)} {source}/{instrument} files.", flush=True)
        return files
    if instrument == "EUVI":
        print("  Processing: on-board processing undone, bias, filter and flat corrected, counts per second, north up", flush=True)
        return prepare_files("euvi", files, output_dir, {}, keep_originals)
    print("  Processing: polarizer triplets calibrated to mean solar brightness (SECCHI level 1), north up, monthly background in a second HDU", flush=True)
    return prepare_files("cor", _cor_groups(files), output_dir, {}, keep_originals)


# ----------------------------------------------------------------------- GONG
def download_gong(start_time, end_time, output_dir, sample=None, skip_calibration=True, keep_originals=False):
    """Download GONG synoptic magnetograms (Carrington maps of the
    line-of-sight field, Gauss, one about every hour) from NSO through
    sunpy's GONG client. The files are kept as the archive sends them:
    they are already in physical units and a synoptic map has no north to
    turn up (``skip_calibration`` and ``keep_originals`` are accepted for
    the interfaces and change nothing). NSO's server is not always
    reachable."""
    from sunpy.net import Fido, attrs as a

    _quiet()
    prefer_ipv4()
    _ensure_dir(output_dir)
    start_dt, end_dt = _parse_time(start_time), _parse_time(end_time)
    _print_search("GONG synoptic magnetogram", start_time, end_time, sample)
    try:
        # sunpy's GONG client takes no Sample attribute (with one, no client
        # answers the query): the cadence is applied to the rows instead
        result = Fido.search(a.Time(start_dt, end_dt), a.Instrument("GONG"), a.Physobs("LOS_magnetic_field"))
        errors = []
        for table in result:
            found = getattr(table, "errors", None)
            if found:
                errors += list(found) if isinstance(found, (list, tuple)) else [found]
        if errors and not result.file_num:
            # the client reports an unreachable server as an empty result
            print(f"Error: could not reach NSO's GONG archive ({errors[0]}). The server may be down or blocked on this network.", flush=True)
            return []
        if sample and len(result):
            result = result[0, _thin_rows(result[0], sample)]
        if not _found(result):
            _no_data("GONG magnetograms", start_time, end_time)
            return []
        downloaded = robust_fido_fetch(result, output_dir)
    except Exception as e:
        print(f"Error during Fido search/fetch: {e}")
        return []
    files = [str(p) for p in downloaded]
    print(f"Successfully downloaded {len(files)} GONG synoptic magnetogram files (Gauss, as the archive sends them).", flush=True)
    return files


# ------------------------------------------------------------------------ CLI
def main():
    """Command line: python -m solar_radio_image_viewer.solar_data_downloader --instrument aia ..."""
    import argparse

    parser = argparse.ArgumentParser(prog="python -m solar_radio_image_viewer.solar_data_downloader", description="Download solar images and prepare them for SolarViewer.")
    parser.add_argument("--instrument", required=True, choices=["aia", "hmi", "iris", "soho", "suvi", "stereo", "gong"])
    parser.add_argument("--start", required=True, help="start time, YYYY.MM.DD HH:MM:SS")
    parser.add_argument("--end", required=True, help="end time, YYYY.MM.DD HH:MM:SS")
    parser.add_argument("--output", default="./solar_data", help="output folder")
    parser.add_argument("--wavelength", help="AIA/EIT/SUVI/EUVI/IRIS wavelength in Å")
    parser.add_argument("--series", default="M_45s", help="HMI series: " + ", ".join(HMI_SERIES))
    parser.add_argument("--soho-instrument", default="EIT", choices=["EIT", "LASCO", "MDI"])
    parser.add_argument("--detector", default="C2", help="LASCO detector (C2, C3) or STEREO instrument (EUVI, COR1, COR2)")
    parser.add_argument("--spacecraft", default="A", choices=["A", "B"], help="STEREO spacecraft")
    parser.add_argument("--physobs", default="magnetogram", help="MDI product: magnetogram or continuum")
    parser.add_argument("--level", default="2", help="SUVI level: 2 or 1b")
    parser.add_argument("--sample", type=float, help="cadence in seconds (every image when omitted)")
    parser.add_argument("--email", help="email registered at JSOC (JSOC exports of AIA/HMI)")
    parser.add_argument("--use-jsoc", action="store_true", help="export from JSOC instead of the VSO (needs --email)")
    parser.add_argument("--no-process", action="store_true", help="keep the files as downloaded")
    parser.add_argument("--keep-originals", action="store_true", help="keep the downloaded files next to the prepared ones")
    parser.add_argument("--psf", action="store_true", help="AIA: deconvolve the PSF (slow)")
    parser.add_argument("--no-degradation", action="store_true", help="AIA: skip the degradation correction")
    parser.add_argument("--no-exposure-norm", action="store_true", help="AIA: keep DN instead of DN/s")
    args = parser.parse_args()

    common = dict(
        start_time=args.start,
        end_time=args.end,
        output_dir=args.output,
        skip_calibration=args.no_process,
        sample=args.sample,
        keep_originals=args.keep_originals,
    )
    try:
        if args.instrument == "aia":
            if not args.wavelength:
                parser.error("--wavelength is required for AIA")
            aia = dict(apply_psf=args.psf, apply_degradation=not args.no_degradation, apply_exposure_norm=not args.no_exposure_norm)
            if args.use_jsoc:
                files = download_aia(args.wavelength, email=args.email, **common, **aia)
            else:
                files = download_aia_with_fido(args.wavelength, **common, **aia)
        elif args.instrument == "hmi":
            if args.use_jsoc or "720" in args.series:
                files = download_hmi(args.series, email=args.email, **common)
            else:
                files = download_hmi_with_fido(args.series, **common)
        elif args.instrument == "iris":
            files = download_iris(wavelength=args.wavelength, **common)
        elif args.instrument == "soho":
            files = download_soho(args.soho_instrument, wavelength=args.wavelength, detector=args.detector, physobs=args.physobs, **common)
        elif args.instrument == "suvi":
            files = download_goes_suvi(wavelength=args.wavelength, level=args.level, **common)
        elif args.instrument == "stereo":
            detector = args.detector.upper() if args.detector.upper() in ("EUVI", "COR1", "COR2") else "EUVI"
            files = download_stereo(spacecraft=args.spacecraft, instrument=detector, wavelength=args.wavelength, **common)
        else:
            files = download_gong(**common)
    except Exception as e:
        print(f"Error: {e}")
        return 1
    print(f"\n{len(files)} files in {args.output}")
    for f in files:
        print("  " + f)
    return 0 if files else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())

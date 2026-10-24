"""
LASCO C2 and C3 calibration: level 0.5 counts to level 1 mean solar
brightness.

This is a Python port of the NRL LASCO level 1 pipeline (SolarSoft
``reduce_level_1``, ``c2_calibrate``, ``c3_calibrate``, ``c2_warp`` and
``c3_warp``). It needs no IDL and no SolarSoft install: the calibration
arrays and the exposure tables are fetched once from the SolarSoft server
and cached in ``~/.cache/solarviewer/lasco_calib``.

For each image:

1. the CCD bias is subtracted (from NRL's per-image exposure table when
   the day's table exists, else the header's OFFSET),
2. the counts are divided by the corrected exposure time,
3. multiplied by the calibration factor of the detector, filter and
   polarizer (mean solar brightness per count per second),
4. multiplied by the inverse vignetting array (C3 also has its stray-light
   ramp subtracted and its occulter, pylon and edge mask applied),
5. the C2 occulter and its diffraction ring are blanked (2.3 solar radii),
6. missing telemetry blocks are blanked,
7. the optical distortion is removed with the NRL radial polynomial,
8. the image is turned to solar north with the Sun at the image centre.

The result is written as 32-bit floats with BUNIT 'MSB' and HISTORY cards
naming every input. Run ``python -m
solar_radio_image_viewer.solar_data_downloader.lasco_calibration --help``
for the command line. LASCO_CALIBRATION.md next to this file explains the
steps and their provenance.
"""

import argparse
import os
import re
import socket
import sys
import tempfile
import urllib.request
import warnings
from datetime import datetime

import numpy as np
from astropy.io import fits

SSW_URL = "https://soho.nascom.nasa.gov/solarsoft/soho/lasco/idl/"
DEFAULT_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "solarviewer", "lasco_calib")
CACHE_ENV = "SOLARVIEWER_LASCO_CALIB"

# calibration arrays, relative to SSW_URL (as c2_calibrate.pro and
# c3_calibrate.pro read them from $LASCO_DATA/calib)
VIGNETTING = {
    "C2": "data/calib/c2vig_final.fts",  # inverse vignetting, all dates
    "C3_PRE": "data/calib/c3vig_preint_final.fts",  # before MJD 51000 (1998-07-06)
    "C3_POST": "data/calib/c3vig_postint_final.fts",
}
C3_RAMP = "data/calib/C3ramp.fts"  # stray-light ramp, clear filter
C3_MASK = "data/calib/c3_cl_mask_lvl1.fts"  # occulter, pylon and edge
C3_OCCULTER = "convert/c3_occltr_cntr.dat"  # occulter centre by date

# distortion_coeffs.pro: radial polynomial in millimetres (0.021 mm/pixel)
DISTORTION = {
    "C2": (0.0051344125, -0.00012233862, 1.0978595e-7),
    "C3": (-0.0151657, 0.000165455, 0.0),
}
PIXEL_MM = 0.021
C2_OCCULTER = (512.634, 505.293)  # occulter_center.dat, zero-based pixels
PLATE_SCALE = {"C2": 11.9, "C3": 56.0}  # arcsec/pixel, subtense.pro
SUN_ARCSEC = 960.0  # the solar radius the mask radii count in
# C2 has no NRL mask file: the occulter (2.0 solar radii, NRL's display
# mask) and the diffraction ring around it (bright out to about 2.3) are
# blanked within this radius of the occulter centre. C3's mask file
# already blanks its occulter (4.1 solar radii), pylon and edge.
C2_MASK_RADIUS = 2.3

MJD_EPOCH = datetime(1858, 11, 17)
MSB_UNIT = "MSB"

_ipv4_preferred = None


class CalibrationError(Exception):
    """The image cannot be calibrated (wrong detector, no data...)."""


# ------------------------------------------------------------ small helpers
def _prefer_ipv4():
    """Put IPv4 addresses first when IPv6 does not reach the server (some
    networks hand out IPv6 addresses that do not route; each attempt then
    waits for a timeout)."""
    global _ipv4_preferred
    if _ipv4_preferred is not None:
        return
    _ipv4_preferred = False
    try:
        infos = socket.getaddrinfo("soho.nascom.nasa.gov", 443, proto=socket.IPPROTO_TCP)
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
        found = real(*args, **kwargs)
        return sorted(found, key=lambda i: i[0] != socket.AF_INET)

    socket.getaddrinfo = ipv4_first
    _ipv4_preferred = True


def cache_dir(path=None):
    """The directory holding the fetched calibration files."""
    return path or os.environ.get(CACHE_ENV) or DEFAULT_CACHE_DIR


def fetch(relpath, directory=None, required=True, quiet=False):
    """The local path of the SolarSoft file ``relpath``, downloading it into
    the cache the first time. Returns None when ``required`` is False and
    the file is not on the server (a day without an exposure table)."""
    directory = cache_dir(directory)
    local = os.path.join(directory, *relpath.split("/"))
    if os.path.isfile(local) and os.path.getsize(local) > 0:
        return local
    os.makedirs(os.path.dirname(local), exist_ok=True)
    _prefer_ipv4()
    url = SSW_URL + relpath
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                data = response.read()
            break
        except urllib.error.HTTPError as e:
            if e.code == 404 and not required:
                return None
            last = e
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            last = e
    else:
        raise CalibrationError(f"could not fetch {url}: {last}")
    if not quiet and len(data) > 200000:
        print(f"  Fetched {os.path.basename(relpath)} ({len(data) / 1e6:.1f} MB) into {directory}", flush=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(local))
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, local)
    return local


def _mjd(header):
    """The integer MJD of the observation date (MID_DATE when present)."""
    mid = header.get("MID_DATE")
    if mid:
        return int(mid)
    return int((_date_obs(header) - MJD_EPOCH).days)


def _date_obs(header):
    """The observation date and time as a datetime (level 0.5 splits them
    into DATE-OBS 'YYYY/MM/DD' and TIME-OBS)."""
    date = str(header.get("DATE-OBS", "")).strip().replace("/", "-")
    if "T" not in date:
        time = str(header.get("TIME-OBS", "00:00:00")).strip()
        date = f"{date}T{time}"
    try:
        return datetime.fromisoformat(date[:26])
    except ValueError:
        return datetime.strptime(date[:19], "%Y-%m-%dT%H:%M:%S")


def _detector(header):
    detector = str(header.get("DETECTOR", "")).strip().upper()
    if detector not in ("C2", "C3"):
        raise CalibrationError(f"not a LASCO C2 or C3 image (DETECTOR = {detector or 'missing'})")
    return detector


def _summing(header):
    """(x, y) pixel summing: on-chip (SUMCOL/SUMROW) times LEB (LEBXSUM/
    LEBYSUM)."""
    sx = max(int(header.get("SUMCOL", 0) or 0), 1) * max(int(header.get("LEBXSUM", 1) or 1), 1)
    sy = max(int(header.get("SUMROW", 0) or 0), 1) * max(int(header.get("LEBYSUM", 1) or 1), 1)
    return sx, sy


def _subfield(header):
    """The (x1, x2, y1, y2) slice of the full 1024x1024 frame this image
    covers, in zero-based pixels of the calibration arrays."""
    r1col = int(header.get("R1COL", 20) or 20)
    r2col = int(header.get("R2COL", 1043) or 1043)
    r1row = int(header.get("R1ROW", 1) or 1)
    r2row = int(header.get("R2ROW", 1024) or 1024)
    return r1col - 20, r2col - 20, r1row - 1, r2row - 1


def _match_array(array, header, shape):
    """``array`` (a full-frame calibration array) cut to the image's
    subfield and binned to its summing, so that it lines up with the image."""
    x1, x2, y1, y2 = _subfield(header)
    if (x1, x2, y1, y2) != (0, 1023, 0, 1023):
        array = array[y1 : y2 + 1, x1 : x2 + 1]
    sx, sy = _summing(header)
    if sx > 1 or sy > 1:
        ny, nx = array.shape[0] // sy, array.shape[1] // sx
        array = array[: ny * sy, : nx * sx].reshape(ny, sy, nx, sx).mean(axis=(1, 3))
    if array.shape != shape:
        raise CalibrationError(f"calibration array {array.shape} does not match the image {shape}")
    return array


# -------------------------------------------------------- calibration factor
def calibration_factor(header):
    """The factor turning counts per second into mean solar brightness,
    as c2_calfactor.pro / c3_calfactor.pro give it: by detector, filter,
    polarizer and (for C2 orange and C3 clear) date, divided by the pixel
    summing."""
    detector = _detector(header)
    mjd = _mjd(header)
    filt = str(header.get("FILTER", "")).replace(" ", "").upper()
    polar = str(header.get("POLAR", "")).replace(" ", "").upper()
    if detector == "C2":
        base = {
            "ORANGE": 4.60403e-07 * mjd + 0.0374116,
            "BLUE": 0.1033,
            "DEEPRD": 0.1033,
            "HALPHA": 0.01055,
            "LENS": 0.01055,
        }.get(filt)
        if base is None:
            raise CalibrationError(f"no C2 calibration factor for filter {filt!r}")
        polref = base / 0.25256
        factor = {"CLEAR": base, "+60DEG": polref, "0DEG": polref, "-60DEG": polref, "ND": polref}.get(polar, base)
    else:
        table = {
            "ORANGE": (0.0297, 0.9648, 1.0798),
            "BLUE": (0.0975, 0.9734, 1.0613),
            "CLEAR": (7.43e-8 * (mjd - 50000) + 5.96e-3, 0.9832, 1.0235),
            "DEEPRD": (0.0259, 0.9983, 1.0300),
            "IR": (0.0887, 0.9833, 1.0288),
        }.get(filt)
        if table is None:
            raise CalibrationError(f"no C3 calibration factor for filter {filt!r}")
        base, zero, minus = table
        polref = base / 0.25256
        if filt == "CLEAR" and polar == "H_ALPHA":
            factor = 1.541
        elif filt == "CLEAR" and polar not in ("CLEAR", "+60DEG", "0DEG", "-60DEG"):
            raise CalibrationError(f"no C3 calibration factor for polarizer {polar!r}")
        else:
            factor = {"CLEAR": base, "+60DEG": polref, "0DEG": polref * zero, "-60DEG": polref * minus}.get(polar, base)
    sx, sy = _summing(header)
    return factor * 1e-10 / (sx * sy)


# -------------------------------------------------------- exposure and bias
def exposure_correction(header, directory=None):
    """(exposure factor, bias, source): NRL's measured correction of the
    commanded exposure time and the CCD bias for this image, read from
    the day's exposure table (``expfac/data/YYMM/c2_expfactor_YYMMDD.dat``).
    When the table or the image is not there, the factor is 1 and the bias
    is the header's OFFSET, which the level 0.5 pipeline estimated from
    the same measurements."""
    detector = _detector(header).lower()
    date = _date_obs(header)
    yymmdd = date.strftime("%y%m%d")
    name = f"{detector}_expfactor_{yymmdd}.dat"
    fallback = (1.0, float(header.get("OFFSET", 0.0) or 0.0), "header OFFSET (no exposure table for this day)")
    try:
        path = fetch(f"expfac/data/{yymmdd[:4]}/{name}", directory, required=False, quiet=True)
    except CalibrationError as e:
        return fallback[0], fallback[1], f"header OFFSET ({e})"
    if path is None:
        return fallback
    filename = str(header.get("FILENAME", "")).strip()
    mid_time = float(header.get("MID_TIME", -1) or -1)
    with open(path, encoding="latin-1") as f:
        for line in f:
            if len(line) < 40:
                continue
            # (a12,f10.6,f10.1,2x,a6,2x,f10.2,2x,a12,a12,a9,i4,e10.2)
            entry = line[:12].strip()
            try:
                factor = float(line[12:22])
                bias = float(line[22:32])
                time = float(line[42:52])
            except ValueError:
                continue
            if entry == filename or (mid_time >= 0 and abs(time - mid_time) < 0.1):
                try:
                    nregion = int(line[78:82])
                except ValueError:
                    nregion = -1
                note = " (no exposure correction measured)" if nregion in (0, 97) else ""
                return factor, bias, f"{name}{note}"
    return fallback[0], fallback[1], f"header OFFSET ({filename or 'image'} not in {name})"


# --------------------------------------------------------------- distortion
def _occulter_centre(header, directory=None):
    """The occulter centre in zero-based pixels of the full frame: the
    fixed C2 value, or the C3 value nearest in date (c3_occltr_cntr.dat)."""
    if _detector(header) == "C2":
        return C2_OCCULTER
    try:
        path = fetch(C3_OCCULTER, directory, quiet=True)
        mjd = _mjd(header)
        best = None
        with open(path, encoding="latin-1") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3 or not re.match(r"\d{4}/\d{2}/\d{2}", parts[0]):
                    continue
                day = (datetime.strptime(parts[0], "%Y/%m/%d") - MJD_EPOCH).days
                if best is None or abs(day - mjd) < abs(best[0] - mjd):
                    best = (day, float(parts[1]), float(parts[2]))
        if best:
            return best[1], best[2]
    except (CalibrationError, OSError, ValueError):
        pass
    return 516.284, 529.489


def distorted_radius(radius, detector):
    """Where a feature seen at ``radius`` pixels from the occulter centre
    in the undistorted image lies in the recorded image (c2_distortion.pro
    and c3_distortion.pro, in full-resolution pixels)."""
    c0, c1, c2 = DISTORTION[detector]
    mm = np.asarray(radius, dtype=float) * PIXEL_MM
    return radius + mm * (c0 + c1 * mm**2 + c2 * mm**4) / PIXEL_MM


def occulter_mask(shape, header, radius_rsun, directory=None):
    """True where a pixel lies within ``radius_rsun`` solar radii (of
    SUN_ARCSEC) of the occulter centre, in the recorded image's pixels."""
    detector = _detector(header)
    xc, yc = _occulter_centre(header, directory)
    sx, sy = _summing(header)
    x1, _, y1, _ = _subfield(header)
    ny, nx = shape
    yy, xx = np.mgrid[0:ny, 0:nx]
    fx = (xx + x1 / sx) * sx
    fy = (yy + y1 / sy) * sy
    scale = float(header.get("PLATESCL", 0) or 0) or PLATE_SCALE[detector]
    return np.hypot(fx - xc, fy - yc) < radius_rsun * SUN_ARCSEC / scale


def undistort(image, header, directory=None, order=1):
    """``image`` resampled onto the undistorted grid: every output pixel
    takes the value found at its distorted radius from the occulter
    centre, as c2_warp.pro and c3_warp.pro do with a 33x33 control grid
    (here evaluated at every pixel)."""
    from scipy.ndimage import map_coordinates

    detector = _detector(header)
    xc, yc = _occulter_centre(header, directory)
    sx, sy = _summing(header)
    x1, _, y1, _ = _subfield(header)
    ny, nx = image.shape
    yy, xx = np.mgrid[0:ny, 0:nx]
    # positions in full-resolution pixels of the full frame
    fx = (xx + x1 / sx) * sx
    fy = (yy + y1 / sy) * sy
    dx, dy = fx - xc, fy - yc
    r = np.hypot(dx, dy)
    with np.errstate(invalid="ignore", divide="ignore"):
        scale = np.where(r > 0, distorted_radius(r, detector) / r, 1.0)
    sx0 = (xc + dx * scale) / sx - x1 / sx
    sy0 = (yc + dy * scale) / sy - y1 / sy
    filled = np.where(np.isfinite(image), image, 0.0)
    out = map_coordinates(filled, [sy0, sx0], order=order, mode="nearest")
    if not np.all(np.isfinite(image)):
        bad = map_coordinates((~np.isfinite(image)).astype(float), [sy0, sx0], order=1, mode="nearest")
        out[bad > 0.01] = np.nan
    return out


# -------------------------------------------------------------- calibration
def calibrate(data, header, directory=None, warp=True, blank_missing=True, inner_radius="auto"):
    """``data`` (counts, as recorded) calibrated to mean solar brightness.
    Returns (image, steps, cards): the calibrated float32 image, the list
    of HISTORY lines describing what was done, and the header cards to set
    (BUNIT, EXPTIME, OFFSET, LEVEL). ``inner_radius`` blanks everything
    within that many solar radii of the occulter centre: 'auto' is
    C2_MASK_RADIUS for C2 and nothing beyond the mask file for C3; 0 or
    None blanks nothing."""
    detector = _detector(header)
    directory = cache_dir(directory)
    steps, cards = [], {}
    raw = np.asarray(data, dtype=np.float64)
    missing = raw <= 0 if blank_missing else np.zeros(raw.shape, bool)

    factor, bias, source = exposure_correction(header, directory)
    exptime = float(header.get("EXPTIME", 0.0) or 0.0) * factor
    if exptime <= 0:
        raise CalibrationError("the header has no exposure time")
    calfac = calibration_factor(header)
    steps.append(f"Bias {bias:.1f} DN subtracted ({source})")
    steps.append(f"Divided by the exposure time {exptime:.4f} s (commanded {float(header.get('EXPTIME')):.4f} s x {factor:.6f})")
    steps.append(f"Calibration factor {calfac:.5e} MSB/(DN/s) ({header.get('FILTER', '').strip()}, {header.get('POLAR', '').strip()} polarizer)")

    image = (raw - bias) / exptime
    if detector == "C2":
        vig_name = VIGNETTING["C2"]
        vig = np.clip(fits.getdata(fetch(vig_name, directory)).astype(np.float64), 0.0, 100.0)
        image = image * calfac * _match_array(vig, header, image.shape)
        steps.append(f"Multiplied by the inverse vignetting array {os.path.basename(vig_name)}")
    else:
        vig_name = VIGNETTING["C3_PRE"] if _mjd(header) < 51000 else VIGNETTING["C3_POST"]
        vig = fits.getdata(fetch(vig_name, directory)).astype(np.float64)
        image = image * calfac * _match_array(vig, header, image.shape)
        steps.append(f"Multiplied by the inverse vignetting array {os.path.basename(vig_name)}")
        if str(header.get("FILTER", "")).strip().lower() == "clear":
            ramp = fits.getdata(fetch(C3_RAMP, directory)).astype(np.float64)
            image = image - _match_array(ramp, header, image.shape)
            steps.append(f"Stray-light ramp {os.path.basename(C3_RAMP)} subtracted")
        mask = fits.getdata(fetch(C3_MASK, directory)).astype(np.float64)
        mask = _match_array(mask, header, image.shape)
        image = np.where(mask > 0.5, image, np.nan)
        steps.append(f"Occulter, pylon and edge blanked with {os.path.basename(C3_MASK)}")
    if inner_radius == "auto":
        inner_radius = C2_MASK_RADIUS if detector == "C2" else None
    if inner_radius:
        inside = occulter_mask(image.shape, header, float(inner_radius), directory)
        image[inside] = np.nan
        px = float(inner_radius) * SUN_ARCSEC / (float(header.get("PLATESCL", 0) or 0) or PLATE_SCALE[detector])
        steps.append(f"Occulter and diffraction ring blanked within {float(inner_radius):.2f} solar radii ({px:.0f} px) of the occulter centre")
    if blank_missing and missing.any():
        image[missing] = np.nan
        steps.append(f"{int(missing.sum())} pixels of missing telemetry blanked")
    if warp:
        image = undistort(image, header, directory)
        c = DISTORTION[detector]
        steps.append(f"Optical distortion removed (radial polynomial {c[0]:.6g}, {c[1]:.6g}, {c[2]:.6g} per mm, 0.021 mm/px)")
    cards["BUNIT"] = (MSB_UNIT, "Mean solar brightness")
    cards["EXPTIME"] = (exptime, "Corrected exposure time (s)")
    cards["OFFSET"] = (bias, "CCD bias subtracted (DN)")
    cards["LEVEL"] = ("1.0", "Calibrated as NRL level 1 (SolarViewer port)")
    return image.astype(np.float32), steps, cards


def _prepared_meta(header, cards, steps):
    """The level 0.5 header rewritten for the calibrated image: the
    calibration cards, an ISO DATE-OBS, helioprojective axis names and
    HISTORY lines."""
    meta = fits.Header()
    for card in header.cards:
        if card.keyword in ("", "COMMENT", "HISTORY", "BSCALE", "BZERO", "BLANK") or card.keyword.startswith("DATA"):
            continue
        try:
            meta[card.keyword] = (card.value, card.comment)
        except Exception:
            continue
    for key, value in cards.items():
        meta[key] = value
    meta["DATE-OBS"] = (_date_obs(header).isoformat(timespec="milliseconds"), "Start of the exposure")
    for key in ("TIME-OBS", "DATE_OBS"):
        meta.remove(key, ignore_missing=True)
    if str(meta.get("CTYPE1", "")).upper().startswith("SOLAR"):
        meta["CTYPE1"], meta["CTYPE2"] = "HPLN-TAN", "HPLT-TAN"
    for key in ("CUNIT1", "CUNIT2"):
        if str(meta.get(key, "")).upper() == "ARCSEC":
            meta[key] = "arcsec"
    for line in header.get("HISTORY", []):
        meta["HISTORY"] = re.sub(r"[^\x20-\x7e]", " ", str(line))  # NRL lines can hold tabs
    for step in steps:
        meta["HISTORY"] = f"SolarViewer: {step}"
    return meta


def _north_up(image, meta, steps):
    """``image`` turned so solar north is up and the Sun sits at the image
    centre, using the header's roll and Sun centre (sunpy does the
    resampling; the header comes back with no roll)."""
    import sunpy
    from sunpy.map import Map

    sunpy.log.setLevel("WARNING")  # the header has no solar radius; sunpy assumes the standard one
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        smap = Map(image, meta)
        pc = smap.rotation_matrix
        roll = float(np.degrees(np.arctan2(pc[1, 0], pc[0, 0])))
        turned = smap.rotate(recenter=True, missing=np.nan, order=3)
    steps.append(f"Rotated {-roll:.3f} deg to solar north, Sun centred")
    out = turned.fits_header
    for line in steps[-1:]:
        out["HISTORY"] = f"SolarViewer: {line}"
    return np.asarray(turned.data, dtype=np.float32), out


def calibrate_file(path, output_dir=None, suffix="_prepped", directory=None, warp=True, north_up=True, overwrite=True, inner_radius="auto"):
    """Calibrate the level 0.5 file ``path`` and save the result next to it
    (or in ``output_dir``) as ``<name><suffix>.fits``. Returns the output
    path."""
    with fits.open(path) as hdul:
        hdu = next(h for h in hdul if h.data is not None and getattr(h.data, "ndim", 0) == 2)
        data, header = np.array(hdu.data), hdu.header.copy()
    image, steps, cards = calibrate(data, header, directory, warp=warp, inner_radius=inner_radius)
    meta = _prepared_meta(header, cards, steps)
    if north_up:
        image, meta = _north_up(image, meta, steps)
    base = os.path.basename(path)
    for ending in (".fits.gz", ".fts.gz", ".fits", ".fts", ".fit"):
        if base.lower().endswith(ending):
            base = base[: -len(ending)]
            break
    out = os.path.join(output_dir or os.path.dirname(os.path.abspath(path)), f"{base}{suffix}.fits")
    fits.PrimaryHDU(image, meta).writeto(out, overwrite=overwrite)
    return out


# ------------------------------------------------------------- command line
def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m solar_radio_image_viewer.solar_data_downloader.lasco_calibration",
        description="Calibrate LASCO C2/C3 level 0.5 FITS files to mean solar brightness (NRL level 1), north up.",
    )
    parser.add_argument("files", nargs="+", help="level 0.5 FITS files (as the VSO or the NRL archive serve them)")
    parser.add_argument("-o", "--output-dir", help="where to write the results (default: next to each input)")
    parser.add_argument("--suffix", default="_prepped", help="suffix of the output names (default _prepped)")
    parser.add_argument("--calib-dir", help=f"where the calibration files are cached (default ${CACHE_ENV} or {DEFAULT_CACHE_DIR})")
    parser.add_argument("--no-warp", action="store_true", help="skip the optical distortion correction")
    parser.add_argument("--keep-roll", action="store_true", help="do not rotate to solar north (the header keeps CROTA)")
    parser.add_argument(
        "--inner-radius",
        type=float,
        default=None,
        metavar="RSUN",
        help=f"blank everything within this many solar radii of the occulter centre (default {C2_MASK_RADIUS} for C2, the NRL mask file alone for C3; 0 blanks nothing)",
    )
    args = parser.parse_args(argv)
    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)
    inner = "auto" if args.inner_radius is None else args.inner_radius
    failed = 0
    for path in args.files:
        try:
            out = calibrate_file(path, args.output_dir, args.suffix, args.calib_dir, warp=not args.no_warp, north_up=not args.keep_roll, inner_radius=inner)
            print(f"{os.path.basename(path)} -> {out}", flush=True)
        except Exception as e:
            failed += 1
            print(f"{os.path.basename(path)}: not calibrated: {e}", file=sys.stderr, flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

"""
STEREO SECCHI calibration: COR1 and COR2 level 0.5 counts to mean solar
brightness, EUVI to counts per second corrected for bias, filter and flat.

This is a Python port of the SECCHI team's ``secchi_prep`` chain in
SolarSoft (``cor_calibrate``, ``cor1_calibrate``, ``cor_polariz``,
``cor2_warp``, ``euvi_correction``, ``get_calimg``, ``get_smask`` and
``scc_getbkgimg``). It needs no IDL and no SolarSoft install: the
calibration arrays, masks and monthly backgrounds are fetched once from the
SolarSoft and STEREO Science Center servers and cached in
``~/.cache/solarviewer/secchi_calib``.

For each image:

1. the on-board processing is undone (the divisions by 2, 4, 8... that the
   image processor applied before downlink, listed in IP_00_19),
2. the CCD bias is subtracted (BIASMEAN, scaled to the pixel summing),
3. the counts are divided by the exposure time,
4. COR: multiplied by the calibration factor of the detector (mean solar
   brightness per count per second, COR1's with its annual drift) and
   divided by the vignetting or flat-field array; EUVI: normalised to the
   open filter and divided by the flat field,
5. a COR polarizer triplet (0, 120 and 240 degrees) is combined into total
   brightness through the inverse Mueller matrix (polarized brightness on
   request),
6. COR2: the optical distortion is removed with the SECCHI radial
   polynomial,
7. COR: the occulter and the field edge are blanked with the team's masks,
8. the image is turned to solar north with the Sun at the image centre,
9. COR: the F corona and stray light under it, the team's monthly minimum
   backgrounds for each polarizer bracketing the date, scaled and combined
   exactly like the image, are saved in a second HDU named BACKGROUND (the
   image itself stays the total brightness).

The result is written as 32-bit floats with BUNIT 'MSB' (COR) or 'DN/s'
(EUVI) and HISTORY cards naming every input. Run ``python -m
solar_radio_image_viewer.solar_data_downloader.secchi_calibration --help``
for the command line. SECCHI_CALIBRATION.md next to this file explains the
steps and their provenance.
"""

import argparse
import os
import re
import sys
import tempfile
import time
import urllib.request
import warnings
from datetime import datetime, timedelta, timezone

import numpy as np
from astropy.io import fits

from .lasco_calibration import CalibrationError, _north_up, _prefer_ipv4, _prepared_meta

CALIB_URL = "https://soho.nascom.nasa.gov/solarsoft/stereo/secchi/calibration/"
BACKGROUND_URL = "https://stereo-ssc.nascom.nasa.gov/data/ins_data/secchi/backgrounds/"
DEFAULT_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "solarviewer", "secchi_calib")
CACHE_ENV = "SOLARVIEWER_SECCHI_CALIB"

# get_calimg.pro: the vignetting (COR2) and flat-field (COR1, EUVI) arrays
CALIBRATION_IMAGE = {
    ("COR1", "A"): "20090723_flatfd_fCc1A.fts",
    ("COR1", "B"): "20090723_flatfd_fCc1B.fts",
    ("COR2", "A"): "20060929_vignet_vCc2A.fts",
    ("COR2", "B"): "20140723_vignet_vCc2B.fts",
}
EUVI_FLAT = "20060823_wav{wavelength}_fCeu{craft}.fts"
# get_smask.pro: the occulter, pylon and field-edge masks (1 where the image is good)
MASK = {"COR1": "cor1_mask.fts", "COR2": "cor2{craft}_mask.fts", "EUVI": "euvi_mask.fts"}
# get_calfac.pro: mean solar brightness per count per second
COR1_CALFAC = {  # factor, reference date, fractional loss per year
    "A": (6.578e-11, datetime(2007, 12, 1, 3, 41, 48), 0.00648),
    "B": (7.080e-11, datetime(2008, 1, 17, 2, 20, 15), 0.00258),
}
COR2_CALFAC = {"A": 2.7e-12 * 0.5, "B": 2.8e-12 * 0.5}  # 0.5: the polarizer's transmission
EUVI_GAIN = 15.0  # electrons per DN; photons per DN = gain * 3.65 eV * wavelength / (13.6 eV * 911 A)
# euvi_get_normal.pro: transmission of the aluminium filters relative to the open position
EUVI_FILTER = {
    171: {"OPEN": 1.0, "S1": 0.49, "S2": 0.49, "DBL": 0.41},
    195: {"OPEN": 1.0, "S1": 0.49, "S2": 0.49, "DBL": 0.41},
    284: {"OPEN": 1.0, "S1": 0.33, "S2": 0.33, "DBL": 0.24},
    304: {"OPEN": 1.0, "S1": 0.29, "S2": 0.29, "DBL": 0.22},
}
# cor2_distortion.pro: r0 = r + c2 + c1 r + c0 r^2 in full-resolution pixels
COR2_DISTORTION = {"A": (1.04872e-05, -0.00801293, -0.243670), "B": (1.96029e-05, -0.0201616, 4.68841)}
# secchi_rectify.pro: how the pipeline turned each detector's CCD frame
# before the 2015 superior conjunction (IDL ROTATE codes); the masks are
# drawn in that frame. From 2015-05-19 to 2023-08-12 STEREO-A flew upside
# down and the pipeline turned its images by a further 180 degrees.
RECTIFY = {("A", "EUVI"): 6, ("A", "COR1"): 3, ("A", "COR2"): 1, ("B", "EUVI"): 3, ("B", "COR1"): 1, ("B", "COR2"): 3}
UPSIDE_DOWN = (datetime(2015, 5, 19), datetime(2023, 8, 12))
# IDL ROTATE codes as the map (x, y) -> (x1, y1) of the output pixel that
# receives input pixel (x, y), as 2x2 matrices on (x, y) with -1 meaning
# "counted from the far edge"
_ROTATE = {
    0: ((1, 0), (0, 1)),
    1: ((0, -1), (1, 0)),
    2: ((-1, 0), (0, -1)),
    3: ((0, 1), (-1, 0)),
    4: ((0, 1), (1, 0)),
    5: ((-1, 0), (0, 1)),
    6: ((0, -1), (-1, 0)),
    7: ((1, 0), (0, -1)),
}
FULL_FRAME = 2048  # active pixels of the CCD along each axis
# scc_sebip.pro: on-board divisions to undo, by image-processing code
SEBIP_FACTORS = {16: 64.0, 17: 64.0, 50: 4.0, 82: 2.0, 83: 4.0, 84: 8.0, 85: 16.0, 86: 32.0, 87: 64.0, 88: 128.0, 118: 3.0}
# the team's monthly minimum backgrounds: one file per polarizer about every
# week, the minimum over a month of daily images, bias removed, per second,
# in the detector's frame (scc_getbkgimg.pro reads them)
BACKGROUND_PATTERN = r"m(c[12])([AB])_(p\d{3}|pTBr|dbTB)_(\d{6})\.fts"
BACKGROUND_MAX_GAP = 60  # days: no background when the nearest one is further from the image than this
BACKGROUND_MAX_SPAN = 120  # days: a bracketing file further than this is not interpolated with
TRIPLET_SECONDS = 180  # a polarizer sequence is taken within this many seconds
ROLL_TOLERANCE = 10.0  # degrees: an image rolled further than this from the usual attitude gets no background
MSB_UNIT = "MSB"


# ------------------------------------------------------------ small helpers
def cache_dir(path=None):
    """The directory holding the fetched calibration files."""
    return path or os.environ.get(CACHE_ENV) or DEFAULT_CACHE_DIR


def _download(url, required=True):
    """The bytes at ``url`` (three attempts), None for a 404 when not
    ``required``."""
    _prefer_ipv4()
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as e:
            if e.code == 404 and not required:
                return None
            last = e
        except (urllib.error.URLError, OSError) as e:
            last = e
    raise CalibrationError(f"could not fetch {url}: {last}")


def _store(local, data):
    os.makedirs(os.path.dirname(local), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(local))
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, local)


def fetch(name, directory=None, quiet=False):
    """The local path of the calibration file or monthly background
    ``name``, downloading it into the cache the first time. A background
    is named by its file name alone and comes from the STEREO Science
    Center into ``backgrounds/``."""
    directory = cache_dir(directory)
    match = re.fullmatch(BACKGROUND_PATTERN, name)
    if match:
        craft, yymmdd = match.group(2), match.group(4)
        url = f"{BACKGROUND_URL}{craft.lower()}/monthly_min/20{yymmdd[:4]}/{name}"
        local = os.path.join(directory, "backgrounds", name)
    else:
        url = CALIB_URL + name
        local = os.path.join(directory, name)
    if os.path.isfile(local) and os.path.getsize(local) > 0:
        return local
    data = _download(url)
    if not quiet and len(data) > 200000:
        print(f"  Fetched {name} ({len(data) / 1e6:.1f} MB) into {directory}", flush=True)
    _store(local, data)
    return local


def _date(value, default=None):
    text = str(value or "").strip().rstrip("Z")
    if not text:
        if default is not None:
            return default
        raise CalibrationError("the header has no observation date")
    try:
        return datetime.fromisoformat(text[:26])
    except ValueError:
        return datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")


def _date_obs(header):
    return _date(header.get("DATE-OBS", header.get("DATE_OBS")))


def _date_avg(header):
    return _date(header.get("DATE-AVG"), _date_obs(header))


def _detector(header):
    detector = str(header.get("DETECTOR", "")).strip().upper()
    if detector not in ("COR1", "COR2", "EUVI"):
        raise CalibrationError(f"not a SECCHI COR1, COR2 or EUVI image (DETECTOR = {detector or 'missing'})")
    return detector


def _craft(header):
    name = str(header.get("OBSRVTRY", "")).strip().upper()
    if name not in ("STEREO_A", "STEREO_B"):
        raise CalibrationError(f"not a STEREO image (OBSRVTRY = {name or 'missing'})")
    return name[-1]


def _ipsum(header):
    return max(int(round(float(header.get("IPSUM", 1) or 1))), 1)


def _summed(header):
    return max(int(round(float(header.get("SUMMED", 1) or 1))), 1)


def _binning(header):
    """How many CCD pixels each image pixel covers along one axis."""
    return 2 ** (_summed(header) - 1)


def _polar(header):
    try:
        return float(header.get("POLAR", -1))
    except (TypeError, ValueError):
        return -1.0


def _flag(value):
    return value is True or str(value).strip().upper() in ("T", "TRUE", "1")


def _upside_down(header):
    """Whether STEREO-A flew upside down for this image, so that the
    pipeline turned it by 180 degrees more than before (get_calimg.pro)."""
    return UPSIDE_DOWN[0] <= _date_obs(header) < UPSIDE_DOWN[1]


def ip_codes(header):
    """The on-board image-processing steps listed in IP_00_19, as integers
    (scc_sebip.pro's reading: three characters each; a 117 marks where the
    list restarts)."""
    text = str(header.get("IP_00_19", "") or "")
    while len(text) % 3:
        text = " " + text
    codes = []
    for i in range(0, len(text), 3):
        try:
            codes.append(int(text[i : i + 3]))
        except ValueError:
            codes.append(0)
    if codes.count(117) == 1:
        codes = codes[codes.index(117) :]
    return codes


# ------------------------------------------------------------- orientation
def _compose(outer, inner):
    """The IDL ROTATE code equal to ``inner`` followed by ``outer``."""
    a, b = np.array(_ROTATE[outer]), np.array(_ROTATE[inner])
    product = tuple(tuple(int(v) for v in row) for row in a @ b)
    return next(code for code, matrix in _ROTATE.items() if matrix == product)


def _inverse(code):
    return next(c for c in _ROTATE if _compose(code, c) == 0)


def idl_rotate(array, code):
    """``array`` turned as IDL's ROTATE(array, code) turns an image whose
    first index is x: 0 nothing, 1/2/3 by 90/180/270 degrees
    counterclockwise, 4 to 7 the same after a transpose. Numpy arrays are
    indexed [y, x], so the pure rotations run the other way here."""
    code = int(code) % 8
    if code == 0:
        return array
    if code == 1:
        return np.rot90(array, -1)
    if code == 2:
        return array[::-1, ::-1]
    if code == 3:
        return np.rot90(array, 1)
    if code == 4:
        return array.T
    if code == 5:
        return array[:, ::-1]
    if code == 6:
        return array[::-1, ::-1].T
    return array[::-1, :]


def _image_orientation(header):
    """The IDL ROTATE code the pipeline applied to this image's CCD frame
    (RECTROTA; 0 when the image was not rectified)."""
    if not _flag(header.get("RECTIFY", False)):
        return 0
    try:
        return int(header.get("RECTROTA", 0) or 0) % 8
    except (TypeError, ValueError):
        return 0


def _rebin(array, factor, how="mean"):
    """``array`` reduced by an integer ``factor`` along both axes, by the
    block mean or by taking every factor-th pixel (IDL's REBIN /SAMPLE)."""
    if factor <= 1:
        return array
    if how == "sample":
        return array[::factor, ::factor]
    ny, nx = array.shape[0] // factor, array.shape[1] // factor
    return array[: ny * factor, : nx * factor].reshape(ny, factor, nx, factor).mean(axis=(1, 3))


def _check_full_frame(header, shape):
    binning = _binning(header)
    if tuple(shape) != (FULL_FRAME // binning, FULL_FRAME // binning):
        raise CalibrationError(f"only full-frame images are calibrated (this one is {shape[1]}x{shape[0]} at summing {binning})")


def calibration_image(header, shape, directory=None):
    """The vignetting or flat-field array to divide the image by, in the
    image's frame (get_calimg.pro): cut to the active CCD area, turned the
    way the pipeline turned the image, binned like it. Returns (array,
    file name)."""
    detector, craft = _detector(header), _craft(header)
    _check_full_frame(header, shape)
    if detector == "EUVI":
        name = EUVI_FLAT.format(wavelength=int(header.get("WAVELNTH", 0) or 0), craft=craft)
    else:
        name = CALIBRATION_IMAGE[(detector, craft)]
    with fits.open(fetch(name, directory)) as hdul:
        chdr, cal = hdul[0].header, np.asarray(hdul[0].data, dtype=np.float64)
    if int(chdr.get("P1COL", 0) or 0) <= 1 and cal.shape[1] > FULL_FRAME:
        cal = cal[0:FULL_FRAME, 50 : 50 + FULL_FRAME]  # the active area of the 2150-wide readout
    if cal.shape != (FULL_FRAME, FULL_FRAME):
        raise CalibrationError(f"{name} is {cal.shape[1]}x{cal.shape[0]}, not {FULL_FRAME} square")
    cal = idl_rotate(cal, _compose(_image_orientation(header), _inverse(_image_orientation(chdr))))
    cal = _rebin(cal, _binning(header))
    return cal, name


def field_mask(header, shape, directory=None):
    """True where the team's mask keeps the image (get_smask.pro): the
    masks are drawn in the frame of the images before the 2015
    conjunction, so they are turned the way the pipeline turned this
    image relative to that."""
    detector, craft = _detector(header), _craft(header)
    _check_full_frame(header, shape)
    name = MASK[detector].format(craft=craft)
    mask = np.asarray(fits.getdata(fetch(name, directory)), dtype=np.float64)
    if mask.shape != (FULL_FRAME, FULL_FRAME):
        raise CalibrationError(f"{name} is {mask.shape[1]}x{mask.shape[0]}, not {FULL_FRAME} square")
    mask = idl_rotate(mask, _compose(_image_orientation(header), _inverse(RECTIFY[(craft, detector)])))
    return _rebin(mask, _binning(header), "sample") > 0.5, name


# -------------------------------------------------------------- the factors
def undo_onboard(image, header, steps):
    """``image`` with the divisions the image processor applied on board
    multiplied back (scc_sebip.pro); the square-root compression undone."""
    codes = ip_codes(header)
    factor = 1.0
    halves = codes.count(1) - (1 if _flag(header.get("DIV2CORR", False)) else 0)
    if halves > 0:
        factor *= 2.0**halves
    for code, value in SEBIP_FACTORS.items():
        n = codes.count(code)
        if n:
            factor *= value**n
    n53 = codes.count(53)
    if n53 and _ipsum(header) > 1:
        factor *= 4.0**n53
    roots = codes.count(2)
    out = np.asarray(image, dtype=np.float64)
    if roots:
        out = out ** (2.0**roots)
        steps.append(f"On-board square root undone ({roots} x)")
    if factor != 1.0:
        out = out * factor
        steps.append(f"On-board division undone: counts multiplied by {factor:g} (IP codes {' '.join(str(c) for c in codes if c)})")
    return out


def bias(header):
    """The CCD bias to subtract from the counts as recorded
    (get_biasmean.pro): BIASMEAN scaled to the pixel summing, or nothing
    when it was removed on board or already (OFFSETCR)."""
    codes = ip_codes(header)
    if any(c in codes for c in (103, 37, 38)):
        return 0.0
    if float(header.get("OFFSETCR", 0) or 0) > 0:
        return 0.0
    value = float(header.get("BIASMEAN", 0) or 0)
    ipsum = _ipsum(header)
    if ipsum > 1:
        value *= (2.0 ** (ipsum - 1)) ** 2
    return value


def calibration_factor(header):
    """Mean solar brightness per count per second for COR1 and COR2 (COR1
    with its annual loss of sensitivity), or detected photons per count
    for EUVI, divided by the pixel summing (get_calfac.pro)."""
    detector, craft = _detector(header), _craft(header)
    if detector == "COR1":
        base, epoch, rate = COR1_CALFAC[craft]
        years = (_date_avg(header) - epoch).total_seconds() / (365.25 * 86400)
        factor = base / (1 - rate * years)
    elif detector == "COR2":
        factor = COR2_CALFAC[craft]
    else:
        wavelength = float(header.get("WAVELNTH", 0) or 0)
        factor = EUVI_GAIN * (3.65 * wavelength) / (13.6 * 911)
    ipsum = _ipsum(header)
    if ipsum > 1:
        factor /= (2.0 ** (ipsum - 1)) ** 2
    if _polar(header) > 1000 and str(header.get("SEB_PROG", "")).strip().upper() != "DOUBLE":
        factor *= 2  # a total-brightness file made from polarizer images
    return factor


def filter_factor(header):
    """EUVI's filter transmission relative to the open position
    (euvi_get_normal.pro)."""
    wavelength = int(float(header.get("WAVELNTH", 0) or 0))
    name = str(header.get("FILTER", "")).strip().upper()
    table = EUVI_FILTER.get(wavelength)
    if table is None:
        raise CalibrationError(f"no EUVI filter table for {wavelength} A")
    if name not in table:
        raise CalibrationError(f"no EUVI filter factor for {name!r}")
    return table[name]


def mueller_inverse(angles):
    """The matrix giving Stokes I, Q and U from three polarizer images at
    ``angles`` degrees (cor_mueller.pro: each image sees I/2 + Q cos(2a)/2
    + U sin(2a)/2)."""
    rows = [[0.5, 0.5 * np.cos(2 * np.radians(a)), 0.5 * np.sin(2 * np.radians(a))] for a in angles]
    return np.linalg.inv(np.array(rows))


# -------------------------------------------------------------- calibration
def calibrate(data, header, directory=None, photons=False):
    """``data`` (counts, as recorded) calibrated: COR1 and COR2 to mean
    solar brightness (this polarizer's image, before the triplet is
    combined), EUVI to counts per second normalised to the open filter
    (detected photons per second with ``photons``). Returns (image,
    steps, cards): the float64 image in the detector's frame, the HISTORY
    lines describing what was done, and the header cards to set."""
    detector = _detector(header)
    directory = cache_dir(directory)
    steps, cards = [], {}
    raw = np.asarray(data, dtype=np.float64)
    _check_full_frame(header, raw.shape)
    missing = (raw <= 0) if float(header.get("NMISSING", 0) or 0) > 0 else np.zeros(raw.shape, bool)

    image = undo_onboard(raw, header, steps)
    offset = bias(header)
    if offset:
        image = image - offset
        steps.append(f"Bias {offset:.1f} DN subtracted (BIASMEAN {float(header.get('BIASMEAN', 0)):.2f} x pixel summing)")
    else:
        steps.append("Bias already removed (on board or OFFSETCR)")
    exptime = float(header.get("EXPTIME", 0.0) or 0.0)
    if exptime <= 0:
        raise CalibrationError("the header has no exposure time")
    image = image / exptime
    steps.append(f"Divided by the exposure time {exptime:.4f} s")
    cal, cal_name = calibration_image(header, raw.shape, directory)
    if detector == "EUVI":
        normal = filter_factor(header)
        if normal != 1.0:
            image = image / normal
            steps.append(f"Normalised to the open filter position (filter {str(header.get('FILTER', '')).strip()} transmits {normal:g})")
        image = image * cal  # the EUVI flat multiplies, as euvi_correction.pro does
        steps.append(f"Multiplied by the flat field {cal_name}")
        if photons:
            factor = calibration_factor(header)
            image = image * factor
            steps.append(f"Counts to detected photons: x {factor:.4f} photons per DN")
            cards["BUNIT"] = ("photon/s", "Detected photons per second")
        else:
            cards["BUNIT"] = ("DN/s", "Counts per second, open-filter equivalent")
    else:
        factor = calibration_factor(header)
        image = image * factor
        steps.append(f"Calibration factor {factor:.5e} MSB/(DN/s) (polarizer {_polar(header):.0f} deg, summing included)")
        with np.errstate(invalid="ignore", divide="ignore"):
            image = np.where(cal > 0, image / cal, np.nan)
        steps.append(f"Divided by the {'vignetting' if detector == 'COR2' else 'flat-field'} array {cal_name}")
        cards["BUNIT"] = (MSB_UNIT, "Mean solar brightness")
    if missing.any():
        image[missing] = np.nan
        steps.append(f"{int(missing.sum())} pixels of missing telemetry blanked")
    cards["OFFSETCR"] = (offset, "CCD bias subtracted (DN, at the pixel summing)")
    cards["IPSUM"] = (1, "On-board summing accounted for in the values")
    cards["LEVEL"] = ("1.0", "Calibrated as SECCHI level 1 (SolarViewer port)")
    return image, steps, cards


def undistort(image, header, directory=None, order=1):
    """A COR2 ``image`` resampled onto the undistorted grid: every output
    pixel takes the value found at its distorted radius from the Sun
    centre, as cor2_warp.pro does with a 32-pixel control grid (here at
    every pixel)."""
    from scipy.ndimage import map_coordinates

    c0, c1, c2 = COR2_DISTORTION[_craft(header)]
    binning = _binning(header)
    xc, yc = float(header["CRPIX1"]) - 1, float(header["CRPIX2"]) - 1
    ny, nx = image.shape
    yy, xx = np.mgrid[0:ny, 0:nx]
    dx, dy = xx - xc, yy - yc
    r = np.hypot(dx, dy) * binning  # full-resolution pixels
    with np.errstate(invalid="ignore", divide="ignore"):
        r0 = r + c2 + c1 * r + c0 * r * r
        scale = np.where(r > 0, r0 / r, 1.0)
    sx0 = xc + dx * scale
    sy0 = yc + dy * scale
    filled = np.where(np.isfinite(image), image, 0.0)
    out = map_coordinates(filled, [sy0, sx0], order=order, mode="nearest")
    if not np.all(np.isfinite(image)):
        bad = map_coordinates((~np.isfinite(image)).astype(float), [sy0, sx0], order=1, mode="nearest")
        out[bad > 0.01] = np.nan
    return out


def _finish_cor(image, header, directory, steps, warp, mask):
    """A COR image (total brightness or background) with the COR2
    distortion removed and the team's mask applied, in that order."""
    detector = _detector(header)
    if warp and detector == "COR2":
        image = undistort(image, header, directory)
        c = COR2_DISTORTION[_craft(header)]
        steps.append(f"Optical distortion removed (radial polynomial {c[0]:.6g} r^2 + {c[1]:.6g} r + {c[2]:.6g} full-resolution pixels)")
    if mask:
        keep, name = field_mask(header, image.shape, directory)
        image = np.where(keep, image, np.nan)
        steps.append(f"Occulter and field edge blanked with {name}")
    return image


def _combine(images, headers):
    """Stokes I, Q and U from three calibrated polarizer images (cor_polariz.pro)."""
    angles = [_polar(h) for h in headers]
    if len(set(angles)) != 3:
        raise CalibrationError(f"a polarizer triplet needs three different angles, not {angles}")
    inverse = mueller_inverse(angles)
    stokes = [sum(inverse[k, i] * images[i] for i in range(3)) for k in range(3)]
    return stokes[0], stokes[1], stokes[2]


def calibrate_group(datas, headers, directory=None, warp=True, mask=True, photons=False):
    """One image (EUVI, or a lone COR image) or a COR polarizer triplet
    calibrated and, for a triplet, combined into total brightness.
    Returns (image, pB, header, steps, cards): the image in the
    detector's frame, the polarized brightness (None but for a triplet),
    the header the output is based on, the HISTORY lines and the cards."""
    directory = cache_dir(directory)
    if len(datas) not in (1, 3):
        raise CalibrationError(f"expected one image or a polarizer triplet, got {len(datas)} files")
    order = sorted(range(len(headers)), key=lambda i: _date_obs(headers[i]))
    datas, headers = [datas[i] for i in order], [headers[i] for i in order]
    detector = _detector(headers[0])
    images, steps = [], []
    for i, (data, header) in enumerate(zip(datas, headers)):
        image, image_steps, cards = calibrate(data, header, directory, photons=photons)
        prefix = f"Image {i + 1} ({os.path.basename(str(header.get('FILENAME', '') or ''))}): " if len(datas) == 3 else ""
        steps += [prefix + s for s in image_steps]
        images.append(image)
    header = headers[len(headers) // 2].copy()
    pb = None
    if len(images) == 3:
        if detector == "EUVI":
            raise CalibrationError("EUVI images are not combined")
        if not all(im.shape == images[0].shape for im in images):
            raise CalibrationError("the polarizer images differ in size")
        total, q, u = _combine(images, headers)
        pb = np.hypot(q, u)
        angles = ", ".join(f"{_polar(h):.0f}" for h in headers)
        steps.append(f"Total brightness from the polarizer images at {angles} deg through the inverse Mueller matrix (cor_polariz.pro)")
        cards["POLAR"] = (1001, "Total brightness from a polarizer triplet")
        cards["EXPTIME"] = (sum(float(h.get("EXPTIME", 0) or 0) for h in headers) / 3, "Mean exposure of the three images (s); values are per second")
        cards["N_IMAGES"] = (3, "Polarizer images combined")
        image = total
    else:
        image = images[0]
    if detector != "EUVI":
        image = _finish_cor(image, header, directory, steps, warp, mask)
        if pb is not None:
            pb = _finish_cor(pb, header, directory, [], warp, mask)
    return image, pb, header, steps, cards


# ---------------------------------------------------------------- background
def _background_series(header):
    """The monthly background series for this image: the polarizer's own
    ('p000'), 'dbTB' for a double-exposure total-brightness image."""
    if str(header.get("SEB_PROG", "")).strip().upper() == "DOUBLE":
        return "dbTB"
    polar = _polar(header)
    if 0 <= polar <= 360:
        return f"p{int(round(polar)):03d}"
    raise CalibrationError(f"no background series for POLAR {polar}")


def _background_date(name):
    yymmdd = re.fullmatch(BACKGROUND_PATTERN, name).group(4)
    return datetime(2000 + int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6]), 12)


def background_index(header, directory=None, months=None):
    """The names of the monthly backgrounds on the server for the image's
    spacecraft in the months around its date, from cached copies of the
    directory listings (refreshed daily while the month is recent)."""
    directory = cache_dir(directory)
    craft = _craft(header).lower()
    date = _date_obs(header)
    if months is None:
        first = date.replace(day=1)
        months = [(first - timedelta(days=1)).strftime("%Y%m"), first.strftime("%Y%m"), (first + timedelta(days=32)).strftime("%Y%m")]
    names = []
    for month in months:
        local = os.path.join(directory, "backgrounds", f"index_{craft}_{month}.txt")
        recent = datetime.strptime(month, "%Y%m") > datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=70)
        stale = not os.path.isfile(local) or (recent and time.time() - os.path.getmtime(local) > 86400)
        if stale:
            page = _download(f"{BACKGROUND_URL}{craft}/monthly_min/{month}/", required=False)
            found = sorted(set(re.findall(BACKGROUND_PATTERN.replace("(", "(?:"), page.decode("latin-1")))) if page else []
            if page is not None or not os.path.isfile(local):
                _store(local, ("\n".join(found) + "\n").encode())
        if os.path.isfile(local):
            with open(local, encoding="utf-8") as f:
                names += [line.strip() for line in f if line.strip()]
    return sorted(set(names))


def background_files(header, directory=None, names=None):
    """The monthly backgrounds to combine for this image as [(name,
    weight)]: the two bracketing the observation (within
    BACKGROUND_MAX_SPAN days), weighted by their distance in time; else
    the nearest within BACKGROUND_MAX_GAP days."""
    detector, craft = _detector(header), _craft(header)
    if detector == "EUVI":
        raise CalibrationError("EUVI images have no background model")
    series = _background_series(header)
    prefix = f"m{detector.lower().replace('cor', 'c')}{craft}_{series}_"
    if names is None:
        names = background_index(header, directory)
    date = _date_obs(header)
    dated = sorted((_background_date(n), n) for n in names if n.startswith(prefix) and re.fullmatch(BACKGROUND_PATTERN, n))
    if not dated:
        raise CalibrationError(f"no {prefix}* background on the server around {date:%Y-%m-%d}")
    before = [(d, n) for d, n in dated if d <= date]
    after = [(d, n) for d, n in dated if d > date]
    if before and after:
        (d0, n0), (d1, n1) = before[-1], after[0]
        span = (d1 - d0).total_seconds()
        if span <= BACKGROUND_MAX_SPAN * 86400:
            w1 = (date - d0).total_seconds() / span
            return [(n0, 1.0)] if w1 < 1e-6 else [(n0, 1.0 - w1), (n1, w1)]
    nearest = min(dated, key=lambda item: abs((item[0] - date).total_seconds()))
    gap = abs((nearest[0] - date).days)
    if gap > BACKGROUND_MAX_GAP:
        raise CalibrationError(f"the nearest monthly background, {nearest[1]}, is {gap} days from the image")
    return [(nearest[1], 1.0)]


def _scaled_background(name, header, shape, directory):
    """The monthly background ``name`` as this image's calibration leaves
    the counts of one polarizer: the file holds counts per second with
    the bias removed at its own summing (scc_getbkgimg.pro's MATCH), so
    it is scaled to the image's summing, then divided by the vignetting
    and multiplied by the calibration factor like the image."""
    with fits.open(fetch(name, directory)) as hdul:
        bhdr, raw = hdul[0].header, np.asarray(hdul[0].data, dtype=np.float64)
    if float(bhdr.get("OFFSETCR", 0) or 0) <= 0:
        raise CalibrationError(f"{name} still holds the bias")
    exptime = float(bhdr.get("EXPTIME", 1.0) or 1.0)
    image = raw / exptime * 4.0 ** (_ipsum(header) - _ipsum(bhdr))
    image[raw <= 0] = np.nan
    if image.shape != tuple(shape):
        ratio = image.shape[0] / shape[0]
        if ratio >= 1 and ratio == int(ratio) and image.shape[1] / shape[1] == ratio:
            image = _rebin(image, int(ratio))
        elif ratio < 1 and (1 / ratio) == int(1 / ratio):
            image = np.kron(image, np.ones((int(1 / ratio), int(1 / ratio))))
        else:
            raise CalibrationError(f"{name} is {image.shape[1]}x{image.shape[0]}, the image {shape[1]}x{shape[0]}")
    cal, _ = calibration_image(header, shape, directory)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(cal > 0, image * calibration_factor(header) / cal, np.nan)


def _check_roll(header, name, directory):
    """Raise when the image was taken with the spacecraft rolled away from
    its usual attitude (a calibration roll): the monthly backgrounds are
    made at the usual attitude, and turning one by the roll would turn the
    stray light with it (scc_getbkgimg.pro's ROLL option), so none fits.
    COR1 is left alone: its stray light dominates and does not change with
    the roll. The usual attitude is the CROTA of a total-brightness
    background of the same date (the per-polarizer files carry a
    meaningless CROTA)."""
    if _detector(header) != "COR2":
        return
    match = re.fullmatch(BACKGROUND_PATTERN, name)
    companion = f"m{match.group(1)}{match.group(2)}_dbTB_{match.group(4)}.fts"
    try:
        usual = float(fits.getheader(fetch(companion, directory, quiet=True)).get("CROTA", 0) or 0)
    except (CalibrationError, OSError, ValueError):
        return
    roll = float(header.get("CROTA", 0) or 0) - usual
    roll = (roll + 180.0) % 360.0 - 180.0
    if abs(roll) > ROLL_TOLERANCE:
        raise CalibrationError(f"the spacecraft was rolled {roll:.1f} deg from its usual attitude (CROTA {float(header.get('CROTA', 0)):.1f} against {usual:.1f}); the monthly backgrounds are made at the usual attitude")


def background(headers, shape, directory=None, warp=True, mask=True, names=None):
    """The F corona and stray light under the image made from ``headers``
    (one, or a polarizer triplet): the team's monthly minimum backgrounds
    for each polarizer bracketing the observation, scaled, calibrated and
    combined exactly like the image (see ``calibrate_group``) and
    interpolated linearly to the observation time, as float32 mean solar
    brightness in the detector's frame. Returns (image, steps)."""
    directory = cache_dir(directory)
    parts, used = [], []
    for header in headers:
        files = background_files(header, directory, names)
        _check_roll(header, files[0][0], directory)
        total = None
        for name, weight in files:
            part = _scaled_background(name, header, shape, directory) * weight
            total = part if total is None else total + part
        parts.append(total)
        used.append(" + ".join(f"{weight:.3f} x {name}" for name, weight in files))
    if len(parts) == 3:
        image, _, _ = _combine(parts, headers)
    else:
        image = parts[0]
    image = _finish_cor(image, headers[len(headers) // 2], directory, [], warp, mask)
    steps = [f"Background: monthly minimum {u}" for u in used]
    steps.append("Background scaled, calibrated and combined like the image, kept in the BACKGROUND extension")
    return image.astype(np.float32), steps


# ------------------------------------------------------------------ files
def _read(path):
    with fits.open(path) as hdul:
        hdu = next(h for h in hdul if h.data is not None and getattr(h.data, "ndim", 0) == 2)
        return np.array(hdu.data), hdu.header.copy()


def _stem(path):
    base = os.path.basename(path)
    for ending in (".fits.gz", ".fts.gz", ".fits", ".fts", ".fit"):
        if base.lower().endswith(ending):
            base = base[: -len(ending)]
            break
    return base


def output_name(paths, headers, output_dir=None, suffix="_prepped"):
    """Where the calibrated version of ``paths`` goes: next to the first
    input (or in ``output_dir``), a triplet named after its first image,
    detector and spacecraft."""
    folder = output_dir or os.path.dirname(os.path.abspath(paths[0]))
    if len(paths) == 3:
        stem = re.sub(r"_[a-z]\dc[12][AB]$", "", _stem(sorted(paths)[0]))
        return os.path.join(folder, f"{stem}_{_detector(headers[0]).lower()}{_craft(headers[0])}_total_brightness.fits")
    return os.path.join(folder, f"{_stem(paths[0])}{suffix}.fits")


def group_files(paths):
    """COR polarizer triplets (0, 120 and 240 degrees within
    TRIPLET_SECONDS of each other) as lists of three paths, every other
    file as a list of one, in time order."""
    records = []
    for p in paths:
        try:
            h = fits.getheader(p)
            records.append((_date_obs(h), _polar(h), p))
        except Exception:
            records.append((None, -1.0, p))
    records.sort(key=lambda r: (r[0] or datetime.min, r[2]))
    groups, i = [], 0
    while i < len(records):
        trio = records[i : i + 3]
        if (
            len(trio) == 3
            and all(r[0] is not None for r in trio)
            and sorted(r[1] for r in trio) == [0.0, 120.0, 240.0]
            and (trio[-1][0] - trio[0][0]).total_seconds() < TRIPLET_SECONDS
        ):
            groups.append([r[2] for r in trio])
            i += 3
        else:
            groups.append([records[i][2]])
            i += 1
    return groups


def calibrate_files(
    paths,
    output_dir=None,
    suffix="_prepped",
    directory=None,
    warp=True,
    mask=True,
    north_up=True,
    overwrite=True,
    with_background=True,
    polarized=False,
    photons=False,
    quiet=False,
):
    """Calibrate the level 0.5 file(s) ``paths`` (one image, or a COR
    polarizer triplet) and save the result (see ``output_name``): the
    image in the primary HDU and, for COR with ``with_background``, the
    F corona and stray light under it in an extension named BACKGROUND
    (the primary header's BKGEXT names it). With ``polarized`` a triplet
    also gives a ``..._polarized_brightness.fits``. When no background
    can be had the file is still written, and its HISTORY says why.
    Returns the output paths."""
    paths = list(paths)
    datas, headers = zip(*(_read(p) for p in paths))
    image, pb, header, steps, cards = calibrate_group(list(datas), list(headers), directory, warp=warp, mask=mask, photons=photons)
    bkg = None
    if with_background and _detector(header) != "EUVI":
        try:
            bkg, bkg_steps = background(list(headers), image.shape, directory, warp=warp, mask=mask)
            steps += bkg_steps
            cards["BKGEXT"] = ("BACKGROUND", "Extension with the F corona and stray light")
        except Exception as e:  # the server, or an unexpected file
            steps.append(f"Background not available: {e}")
            if not quiet:
                print(f"  Warning: {os.path.basename(paths[0])}: no background ({e})", file=sys.stderr, flush=True)
    meta = _prepared_meta(header, cards, steps)
    image = image.astype(np.float32)
    if north_up:
        unrotated = meta.copy()
        image, meta = _north_up(image, meta, steps)
        if bkg is not None:
            bkg, _ = _north_up(bkg, unrotated, [])
        if pb is not None:
            pb, _ = _north_up(pb.astype(np.float32), unrotated, [])
    out = output_name(paths, list(headers), output_dir, suffix)
    hdus = [fits.PrimaryHDU(image, meta)]
    if bkg is not None:
        bmeta = fits.Header()
        for card in meta.cards:
            if card.keyword in ("HISTORY", "COMMENT", "BKGEXT", "", "SIMPLE", "BITPIX", "EXTEND") or card.keyword.startswith("NAXIS"):
                continue
            bmeta[card.keyword] = (card.value, card.comment)
        bmeta["BUNIT"] = (MSB_UNIT, "Mean solar brightness")
        bmeta["COMMENT"] = "F corona and stray light: SECCHI monthly minimum backgrounds, calibrated like the image"
        hdus.append(fits.ImageHDU(bkg, bmeta, name="BACKGROUND"))
    fits.HDUList(hdus).writeto(out, overwrite=overwrite)
    outputs = [out]
    if polarized and pb is not None:
        pmeta = meta.copy()
        pmeta["POLAR"] = (1002, "Polarized brightness from a polarizer triplet")
        pmeta["HISTORY"] = "SolarViewer: polarized brightness sqrt(Q^2 + U^2) of the same triplet"
        pout = out.replace("_total_brightness.fits", "_polarized_brightness.fits")
        fits.PrimaryHDU(pb.astype(np.float32), pmeta).writeto(pout, overwrite=overwrite)
        outputs.append(pout)
    return outputs


def calibrate_file(path, output_dir=None, **kwargs):
    """``calibrate_files`` for one file; returns its output path."""
    return calibrate_files([path], output_dir, **kwargs)[0]


# ------------------------------------------------------------- command line
def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m solar_radio_image_viewer.solar_data_downloader.secchi_calibration",
        description="Calibrate STEREO SECCHI level 0.5 FITS files: COR1/COR2 polarizer triplets to total brightness in mean solar brightness, EUVI to corrected counts per second; north up.",
    )
    parser.add_argument("files", nargs="+", help="level 0.5 FITS files (as the VSO serves them); COR polarizer triplets are found by time and angle")
    parser.add_argument("-o", "--output-dir", help="where to write the results (default: next to each input)")
    parser.add_argument("--suffix", default="_prepped", help="suffix of the output names of single images (default _prepped)")
    parser.add_argument("--calib-dir", help=f"where the calibration files are cached (default ${CACHE_ENV} or {DEFAULT_CACHE_DIR})")
    parser.add_argument("--no-warp", action="store_true", help="skip the COR2 optical distortion correction")
    parser.add_argument("--no-mask", action="store_true", help="do not blank the occulter and field edge of COR images")
    parser.add_argument("--keep-roll", action="store_true", help="do not rotate to solar north (the header keeps CROTA)")
    parser.add_argument("--no-background", action="store_true", help="do not fetch the monthly minimum backgrounds for the BACKGROUND extension")
    parser.add_argument("--pb", action="store_true", help="also write the polarized brightness of each triplet")
    parser.add_argument("--photons", action="store_true", help="EUVI in detected photons per second instead of counts per second")
    args = parser.parse_args(argv)
    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)
    failed = 0
    for group in group_files(args.files):
        try:
            outs = calibrate_files(
                group,
                args.output_dir,
                args.suffix,
                args.calib_dir,
                warp=not args.no_warp,
                mask=not args.no_mask,
                north_up=not args.keep_roll,
                with_background=not args.no_background,
                polarized=args.pb,
                photons=args.photons,
            )
            print(f"{', '.join(os.path.basename(p) for p in group)} -> {', '.join(outs)}", flush=True)
        except Exception as e:
            failed += 1
            print(f"{', '.join(os.path.basename(p) for p in group)}: not calibrated: {e}", file=sys.stderr, flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

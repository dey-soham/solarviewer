"""
Automatic RFI flagging of dynamic spectra: ConvRFI (Zhang, Offringa, Zucca,
Kozarev & Mancini 2023, MNRAS 521, 630; https://github.com/peijin94/ConvRFI),
done with numpy and scipy instead of PyTorch.

Six fixed convolution kernels find what solar bursts don't look like:
broadband spikes one sample long and narrowband lines one channel wide (3x3
line kernels), and sharp edges (5x5). A pixel is flagged where a kernel's
response is positive, grown along the kernel's bright side. The thresholds
compare a pixel with its neighbours as a ratio, so the data must be positive
power (raw, not background-subtracted), at full resolution: a burst only a
few samples wide looks like RFI.

Every kernel is a time profile times a frequency profile, so each
convolution is two 1-D passes.
"""

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import correlate1d, maximum_filter1d

# ConvRFI's defaults: the line kernels' weights (transient, narrow band) and
# the edge kernels' (time, frequency); larger flags more
DEFAULT_FACTORS = (1.6, 1.65, 0.5, 0.5)


def factors_for(sensitivity, edges=True):
    """Kernel weights for a sensitivity from 0 (gentle) to 1 (strong); 0.5
    gives ConvRFI's defaults. A line is flagged when brighter than its
    neighbours by 1.43x (gentle) to 1.11x (strong); edges likewise by 1.67x
    to 1.11x. Without ``edges`` only lines are flagged."""
    s = min(max(float(sensitivity), 0.0), 1.0) - 0.5
    lines = (1.6 + 0.4 * s, 1.65 + 0.4 * s)
    edge = 0.5 + 0.2 * s if edges else 0.0
    return lines + (edge, edge)


def _pass(data, weights, axis, mode):
    return correlate1d(data, np.asarray(weights, data.dtype), axis=axis, mode=mode, cval=0.0)


def _grow(flags, time_weights, freq_weights):
    """Binary dilation by a separable box (zero outside)."""
    grown = _pass(_pass(flags, time_weights, 0, "constant"), freq_weights, 1, "constant")
    return grown > 0


# Rows a flag can depend on either side (line 2, edge 2 + growth 2, nearness
# of lines 3): blocks overlapping by more give the flags of the whole
OVERLAP = 8
BLOCK_VALUES = 4_000_000  # values flagged at a time (bounds memory)


def flag_rfi(data, factors=DEFAULT_FACTORS, padding="nearest"):
    """Boolean RFI flags of ``data`` ((time, freq), positive linear power or
    log-scaled digits; NaN allowed, filled from its channel first).

    ``padding``: "nearest" repeats the edge samples, so a page's first and
    last samples aren't flagged for lying next to nothing; "constant" pads
    with zeros as ConvRFI does (the same flags as the original).

    Large data is flagged in overlapping blocks of time (the same flags,
    memory for a block at a time)."""
    nt, nf = data.shape
    rows = max(4 * OVERLAP, BLOCK_VALUES // max(nf, 1))
    if nt <= rows + 2 * OVERLAP:
        return _flag_block(data, factors, padding)
    flags = np.zeros((nt, nf), dtype=bool)
    for start in range(0, nt, rows):
        stop = min(start + rows, nt)
        lo, hi = max(0, start - OVERLAP), min(nt, stop + OVERLAP)
        block = _flag_block(data[lo:hi], factors, padding)
        flags[start:stop] = block[start - lo : stop - lo]
    return flags


def _flag_block(data, factors, padding):
    x = np.asarray(data, dtype=np.float32)
    bad = ~np.isfinite(x)
    if bad.any():
        x = x.copy()
        with np.errstate(all="ignore"):
            fill = np.nanmedian(np.where(bad, np.nan, x), axis=0)
        fill = np.where(np.isfinite(fill), fill, 0.0).astype(np.float32)
        x[bad] = np.broadcast_to(fill, x.shape)[bad]
    mode = "constant" if padding == "constant" else padding
    a0, a1, a2, a3 = (float(f) for f in factors)

    # Sums over 3 and 5 neighbours, shared by the kernels
    sum3_freq = _pass(x, [1, 1, 1], 1, mode)
    sum3_time = _pass(x, [1, 1, 1], 0, mode)
    sum5_freq = _pass(x, [1] * 5, 1, mode)
    sum5_time = _pass(x, [1] * 5, 0, mode)

    # Lines: a broadband spike (one time sample) and a narrow band (one channel)
    transient = _pass(sum3_freq, [-1, a0, -1], 0, mode) > 0
    narrow = _pass(sum3_time, [-1, a1, -1], 1, mode) > 0
    flags = _grow(transient, [1], [1, 1, 1]) | _grow(narrow, [1, 1, 1], [1])

    # Edges: a step at earlier/later times or lower/higher channels, where
    # no line was found nearby
    ones5 = [1] * 5
    near = (_grow(transient, ones5, ones5), _grow(narrow, ones5, ones5))
    edge_kernels = (
        (0, [a2, a2, a2, -1, -1], [1, 1, 1, 0, 0]),  # brighter at earlier times
        (0, [-1, -1, a2, a2, a2], [0, 0, 1, 1, 1]),  # later times
        (1, [a3, a3, a3, -1, -1], [1, 1, 1, 0, 0]),  # lower channels
        (1, [-1, -1, a3, a3, a3], [0, 0, 1, 1, 1]),  # higher channels
    )
    for axis, weights, bright in edge_kernels:
        if axis == 0:
            edge = (_pass(sum5_freq, weights, 0, mode) > 0) & ~near[0]
            flags |= _grow(edge, bright, ones5)
        else:
            edge = (_pass(sum5_time, weights, 1, mode) > 0) & ~near[1]
            flags |= _grow(edge, ones5, bright)
    return flags


# ---------------------------------------------------------------------------
# SumThreshold (Offringa et al. 2010, MNRAS 405, 155; as in AOFlagger)
# ---------------------------------------------------------------------------

def _block_background(x, axis, block=16):
    """A smooth background of ``x`` along ``axis``: medians of blocks of
    ``block`` samples, interpolated linearly between the blocks' centres
    (robust to RFI and bursts narrower than a block; far faster than a
    running median)."""
    x = np.moveaxis(x, axis, -1)
    n = x.shape[-1]
    blocks = -(-n // block)
    padded = np.concatenate([x, np.repeat(x[..., -1:], blocks * block - n, axis=-1)], axis=-1)
    with np.errstate(all="ignore"):
        medians = np.nanmedian(padded.reshape(*x.shape[:-1], blocks, block), axis=-1)
    if blocks == 1:
        return np.moveaxis(np.repeat(medians, n, axis=-1), -1, axis)
    # Linear between the blocks' centres, and beyond the outer ones
    position = (np.arange(n) - (block - 1) / 2) / block
    j = np.clip(np.floor(position).astype(int), 0, blocks - 2)
    frac = position - j
    nxt = j + 1
    background = medians[..., j] * (1 - frac) + medians[..., nxt] * frac
    return np.moveaxis(background, -1, axis)


def _robust_sigma(r):
    sample = r[np.isfinite(r)]
    if sample.size > 1_000_000:
        sample = sample[:: sample.size // 1_000_000]
    if not sample.size:
        return 1.0
    center = np.median(sample)
    mad = np.median(np.abs(sample - center))
    # (no spread: most values equal; then any departure stands out)
    return float(1.4826 * mad) or float(1e-6 * abs(center)) or 1e-12


def _sum_threshold_1d(r, axis, chi1, rho=1.5, windows=(1, 2, 4, 8, 16, 32, 64)):
    """Flags along ``axis`` where runs of 1, 2, 4... samples of ``r`` sum
    above M * chi1 / rho**log2(M) (positive excess only: RFI adds power).
    Samples already flagged count as the threshold in longer runs."""
    r = np.ascontiguousarray(np.moveaxis(np.nan_to_num(r, nan=0.0), axis, -1), dtype=np.float64)
    n = r.shape[-1]
    flags = np.zeros(r.shape, bool)
    for m in windows:
        if m > n:
            break
        chi = chi1 / rho ** np.log2(m)
        v = np.where(flags, chi, r)
        cs = np.cumsum(v, axis=-1)
        sums = cs[..., m - 1 :].copy()  # windows ending at m-1 .. n-1
        sums[..., 1:] -= cs[..., : n - m]
        starts = np.zeros(r.shape, bool)
        starts[..., : n - m + 1] = sums > m * chi  # a window from each start
        # Every sample a flagged window covers: a start among the m before it
        flags |= maximum_filter1d(starts, m, axis=-1, origin=(m - 1) // 2, mode="constant", cval=0)
    return np.moveaxis(flags, -1, axis)


def sum_threshold(data, sensitivity=0.5):
    """SumThreshold flags of ``data`` ((time, freq), positive power) for
    narrowband RFI: on the excess over a background along frequency, in
    which bursts (broadband) are, run along time (lasting lines) and along
    frequency (a few channels). Spikes in time are left to ConvRFI: against
    a background along time, short bursts are excess too. The first
    threshold is 10 (gentle) to 5 (strong) robust sigmas."""
    x = np.asarray(data, np.float32)
    k = 10.0 - 5.0 * min(max(float(sensitivity), 0.0), 1.0)
    with np.errstate(all="ignore"):
        r = x / _block_background(x, 1) - 1.0
    chi1 = k * _robust_sigma(r)
    return _sum_threshold_1d(r, 0, chi1) | _sum_threshold_1d(r, 1, chi1)


# ---------------------------------------------------------------------------
# The noise of each pixel (an image of standard deviations, e.g. LOFAR's)
# ---------------------------------------------------------------------------

def noise_flags(data, stddev, sensitivity=0.5):
    """Flags where a pixel's noise isn't what its power implies. The sky's
    (the Sun's too) noise is proportional to its power, std/mean depending
    only on the channel width and integration: about the same in
    neighbouring channels. RFI's often isn't: flagged where std/mean is off
    a smooth curve across the channels (block medians of each channel's)
    by more than 10 (gentle) to 5 (strong) robust deviations, either way;
    a channel off all the time is found too."""
    with np.errstate(all="ignore"):
        q = np.asarray(stddev, np.float32) / np.asarray(data, np.float32)
        q[~np.isfinite(q)] = np.nan
        per_channel = np.nanmedian(q, axis=0)
    expected = _block_background(per_channel[None, :], 1)[0]
    deviation = q - expected
    spread = _robust_sigma(deviation)
    k = 10.0 - 5.0 * min(max(float(sensitivity), 0.0), 1.0)
    with np.errstate(all="ignore"):
        return np.abs(deviation) > k * spread


# ---------------------------------------------------------------------------
# After the detectors
# ---------------------------------------------------------------------------

def fill_gaps(flags, eta=0.2):
    """The scale-invariant rank operator (Offringa et al. 2012, A&A 539,
    A95), along time and frequency: a sample is flagged when, in some
    interval around it, at least 1 - eta of the samples are."""
    if eta <= 0:
        return flags
    out = flags.copy()
    for axis in (0, 1):
        f = np.moveaxis(flags, axis, -1)
        v = np.where(f, eta, eta - 1.0)
        w = np.concatenate([np.zeros(f.shape[:-1] + (1,)), np.cumsum(v, axis=-1)], axis=-1)
        prefix_min = np.minimum.accumulate(w[..., :-1], axis=-1)  # min over W[0..i]
        suffix_max = np.maximum.accumulate(w[..., :0:-1], axis=-1)[..., ::-1]  # max over W[i+1..n]
        # (at least 1 - eta: ties count, whatever the sums' rounding)
        out |= np.moveaxis(suffix_max - prefix_min >= -1e-9, -1, axis)
    return out


def flag_whole(flags, channel_limit=0.5, time_limit=0.5):
    """Whole channels flagged more than ``channel_limit`` of the time, and
    whole times flagged in more than ``time_limit`` of the channels."""
    out = flags.copy()
    if channel_limit < 1:
        out[:, flags.mean(axis=0) > channel_limit] = True
    if time_limit < 1:
        out[flags.mean(axis=1) > time_limit, :] = True
    return out


# ---------------------------------------------------------------------------
# All of it
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RfiSettings:
    """What automatic RFI flagging does (hashable: the viewer's cache key)."""

    sensitivity: float = 0.5  # 0 gentle .. 1 strong, for every detector
    convrfi: bool = True  # lines and edges (ConvRFI)
    edges: bool = True  # ConvRFI's edge kernels too
    sumthreshold: bool = False  # SumThreshold (for quiet data: it flags burst fine structure too)
    noise: bool = True  # std/mean, when the file has a noise image
    fill_gaps: float = 0.2  # scale-invariant rank operator's eta (0: off)
    channel_limit: float = 0.5  # flag a whole channel above this share (1: off)
    time_limit: float = 1.0  # flag a whole time above this share (1: off; a
    # burst can cover most channels at once, so off unless asked)


def flag_page(data, settings=RfiSettings(), stddev=None):
    """RFI flags of ``data`` ((time, freq), positive power) by the detectors
    ``settings`` turns on, then filled and widened to whole channels/times.
    ``stddev``: the page's noise image, if the file has one."""
    s = settings.sensitivity
    flags = np.zeros(data.shape, bool)
    if settings.convrfi:
        flags |= flag_rfi(data, factors_for(s, settings.edges))
    if settings.sumthreshold:
        flags |= sum_threshold(data, s)
    if settings.noise and stddev is not None and stddev.shape == data.shape:
        flags |= noise_flags(data, stddev, s)
    flags = fill_gaps(flags, settings.fill_gaps)
    return flag_whole(flags, settings.channel_limit, settings.time_limit)

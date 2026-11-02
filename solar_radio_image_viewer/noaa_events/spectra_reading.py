"""
Reading a day's spectra off their own images, for the Radio spectra tab.

EOVSA and OVRO-LWA plot from about 14:00 to 02:00 the next day, so a day's lane
is made of two plots. Everything a lane states comes from the plots: the date
from the title, the time from the tick labels at the tick marks, the frequency
from the ticks of the axis. Anything not read exactly is refused. Learmonth
needs none of this: its file is numbers, each record with its own time.

OVRO-LWA's plots, the daily one and the composite's panel alike, put their
samples side by side, so that a gap in the observing closes up and everything
before it slides later under the ticks: 53 minutes at 21 UT on 2024-05-14. Their
time axis is not used. The UT table of the day's FITS, a little over a megabyte
out of a gigabyte, says when each sample was taken, and one real channel from
the same file proves that the plot is that run of samples.
"""

import base64
import logging
import re
import zlib
from datetime import date, timedelta
from functools import lru_cache
from typing import NamedTuple

import numpy as np
from PyQt5.QtGui import QImage, QPainter

logger = logging.getLogger(__name__)

GAP_LEAST = 5 / 60  # hours: less than this is an edge of the plot, not a gap


class Registration(NamedTuple):
    """Where the day lies in an image. Each slice is ``(left, right, top,
    bottom, start, end)``: edges in image pixels, then hours. Where slices
    overlap the later one is drawn over the earlier. ``top``, ``bottom`` and
    ``scale`` are for images that state their own band; ``note`` is said
    beside the lane. ``panels`` are for a figure whose rows do not share one
    time axis: ``(Registration, top row, bottom row)`` each."""

    slices: tuple
    top: str = ""
    bottom: str = ""
    scale: str = ""
    note: str = ""
    panels: tuple = ()

    def gaps(self):
        """The parts of the day no slice covers, as ``(start, end)`` hours."""
        spans = sorted((max(s[4], 0.0), min(s[5], 24.0)) for s in self.slices if s[5] > 0 and s[4] < 24)
        gaps, reached = [], 0.0
        for start, end in spans:
            if start - reached > GAP_LEAST:
                gaps.append((reached, start))
            reached = max(reached, end)
        if 24.0 - reached > GAP_LEAST:
            gaps.append((reached, 24.0))
        return gaps

    def shows(self, start, end):
        return any(s[5] > start and s[4] < end for s in self.slices)

    def image_x(self, hours):
        """The image column of ``hours``, ``None`` where there is no data."""
        for left, right, _top, _bottom, start, end in reversed(self.slices):
            if start <= hours <= end:
                return left + (hours - start) / (end - start) * (right - left)
        return None

    def marks(self, hours):
        """The lines of a cursor at ``hours``: ``[(column, top row, bottom row)]``
        with ``None`` rows for the whole image, and only where it is true."""
        panels = self.panels or ((self, None, None),)
        columns = [(part.image_x(hours), top, bottom) for part, top, bottom in panels]
        return [mark for mark in columns if mark[0] is not None]


def grey_of(image):
    """``image`` as a 2-D array of bytes."""
    grey = image.convertToFormat(QImage.Format_Grayscale8)
    bits = grey.constBits()
    bits.setsize(grey.byteCount())
    pixels = np.frombuffer(bits, np.uint8).reshape(grey.height(), grey.bytesPerLine())
    return pixels[:, : grey.width()].copy()


def _runs(mask):
    """``[(first, last)]`` of the runs of True in a 1-D array."""
    edges = np.diff(np.r_[0, np.asarray(mask).astype(np.int8), 0])
    return list(zip(np.flatnonzero(edges == 1).tolist(), (np.flatnonzero(edges == -1) - 1).tolist()))


def _centres(mask):
    return [(first + last) / 2 for first, last in _runs(mask)]


# Glyphs of matplotlib's font at the three sizes of these plots: each is the mean
# of its ink box over real plots, 8 by 12 cells of 0 to 15, then its ink box in
# quarter pixels. Held out, 1846 glyphs from 40 plots read with none wrong.
GLYPH_CELLS = (12, 8)
GLYPHS = {
    "t12": (
        '-0123456789:',
        'c-'
        'pm7ORf+x3~PS<Hx+vp5A=>O_dgqMW(JVhASAr8X4E=%lBRS(l>QTc(LIeguf0TC74^z<?@nFS_jbvgH(<);aF(C^{D05A_g<Y>_x'
        'n|iNygqH#)o~ce0cvOcj$9&O(O=;*K1}Wwh33YH=(iOFzF76?E5<$d#0o1g=!F2_}}>YjVfseh8=m}<7&!Lp_X<k%v&)<FMK3OwO'
        'T~*KciJgd97JBT%$YoIAOn?m_Vm8jUp7fAJ>k$b`GF(hrMPwJgghoOJ0WuRKae=4|f^vYO4@NL8PMsB4s;Z7OgnvKCc{Mc;^)1z~'
        'Lso2b>pC743VBIlJ<mao!pC=BJyt#obap88V3*G6>8dgQGXB13fOim(f#yCa^}wR7kNeSc*h8W}}?av1gPh(kBissid<e!i%|fgU'
        'B+J=%~Okk0j$nD37;Hk<uTFZ&iJ}9dn25yieWh5JQXV_hpJ1Ap@ebGz7Gc+E}$nFUre(Vei;#HJzRrlg{3)FJ!6rdr-'
        'b*GsX5fb54d;P3^Si&Jaz)%P5qw1L6=lrW>{Jp|O}Nq2%;c5_QpRctbvFsgvQy{Z}fQ4ZSv8>Sz4j_qR&`23{^e2KHZKJ;)FK(EI'
        '!WH{mHI'
    ),
    "t10": (
        '0123456789:',
        'c-'
        'oayNtRn73<NYHrfpu*2k<d+|5K&Jw|kw|!u1WBsw7TuZ930j?JlyFV=bp3TL*_$`#xtX*8nc1Z+xGB{62A?Jm;PF>m^i6hm*P1#J'
        'PG)MA(mT?`S^p8&=h0)RNZZ*UP2V%D%*Ugffr0_8wL<_Uv9R&i8+3PEgV$;N%kpjCz8KB63UPK49f-'
        's6e$g>ARyqSMCdLD_C!V#J%P)>1RqUl+x@ql>FX9rCWI!%^N4TQRKOeH;MiGl{BUK<8}ibntC?XyZG5^Gl0S4B-'
        'XJnmPXWSBXdVKhyd$sSGT<X;MdEpU^I;!+~zh8aK&86BIZ6$#H;~*2V|jfLowM$J<$&)hw1DJ4$d-'
        '?1KB)(UfDVZv9^du1p4U7ceB8CtW!l8-'
        '@6ozp)Cr}%*a*dL;Iq#Q7oux6T1YJ29&8)0vw*1SIg;ywgCbgI`7N!X?0@>pH6u{^S&Z1mCI;|-eco_y}{=mljrf*Yv-tO=WLh|4'
        'G_f25s1!{=(!q(#;rljI6c+0HtuQ;k<#z`VC+8PSTxmDifvC`RKQomfP_8rrBp<&PNQI=SamOWVOVu(s64gu<XRdt=Zekmoojao('
        'aDN_R|%cm0;q7`3hex+O<3*?im+jk*4sY!nQOo&?-lp`?>`wSDai'
    ),
    "t30": (
        '0123456789:ADFJMNOS]abceglnoprtuvy',
        'c-'
        'o~_JFZ+e3>8KFzg|PC>>yc!3kP^WUg2UEkRpZ46cgYTUPQ_)HHW0`0M9Q*5L?ZetC1*v54pQ1@_kPq#t42E8hZ~afj1Thih_|S38'
        'n{n+xOSG>mKCv4mrCs5%>N4XAa~%&Jg1kaP8#<_cvni4GQ<+oi6|Wp1s2pb#d-'
        '<M5@aUt7g#6E)TP01yOV_9QzvV=u($O_5%HK&x98sxV0fKG*ibU8ZO!IdqXSzdyE#Yb9^+3biRFRb9THI$TWYt>%P+q#Vbx<6=NE'
        'L%}>GQ>lVym!KAjS^j=-'
        '=B+C&mtnSPO<0v5b@t)0^k*SGsE0Z4DnWp~8E^x?mmskoSHFUF6IJ&!6h)|>s8v<3a*l}GS?t2x{0?JXHvyEZy#OGS_Mqrrc^J4O'
        'CWLM1I;Dc&XT%xJi#E~1j%pD0?_6UwBujf0ThzzlyIrD#U{kYjwc15?7n-(3jz?_Nr`{SEzy1Aif+46K>mGrGnam-'
        '95t4@;Nf<6x0Z@Y`#bx%gVCu&i}2Jgf1rMHMzN&_||xCU-YutA*Uc<f4f4(O~m;3}6bmt0b0RkS+aiA(lK{T?~?lS|8+`jy6jukp'
        '&3bcN#k`F@;q($~3g*8ZRCy60XkqIVy~C8o$Eb<D0ISnZS&RewYu`ggbi?6Na9BUI+tT4LOSL7rwc_NY;<$)~b9{eBnka#<T`)-'
        'pv&d-<a(LNmkS4dJF&SjaGjbFW=;_{jb3kxOj#(Am$!7Z;8>k+*U~ikf{NSnKV%f5-'
        'QC^$)zK$lr%TcS}gdxd>+UoZ9;FZ?BcZ&P&BiP)W!V3i5>|6i6OFmmk2`22Ws3B?}5q3vQ;RSn^hsH!q}5h(p(!@-'
        '7$#c?y_AG;AQ$u4SDB3vzPGx8$gPBN|l^sji5MHH5~uyPC!me3#IH!pc&{3;xzJj?t>SvXyi557hm_*Ezj6sZA8A$k7Ug+)wx2Q{'
        'Y%;qX25-'
        'Vnnf)R+>hvD<?E@V1f8qqBk1ftC95&)bu*M!evOw#Uy<PrTzTkNvn;7eZjM2wDkSxmDL^n@Yj$k1m9Ff01@uBv_%*L?&SB#%uEjL'
        '+zX=l%(arUevPbAc7eih%!26TLWu;Y1`(UxhudM4{T64*@P!9a2dP9!fQCv8*7?0n1l};Bt|fjxdP;L9>ONt5facGir9Z$5hAVw8'
        '$q!<QCDgG#5$LL*^$M^3>)aqMv3be4<i5XcGe^37DomqF5GSW-tPFN;Sm{BH6sj(NG9g+EK?>_U&><(5%wf+cqegwcZE0~4gm2bS'
        '&}>CVyXGQeGU78gWi1yXk%Vc9_Npn5bPY6drP?tm^d-'
        'p@`u_FBI5tMpFeSq;zrP*deRf&=m6pRGlG#;f>=CrY(Ze)XV9Nu`t8e!oNFQ$29UCG=ITMZKlr)lc*>EHw4U4h%1JAzY7J_*0O_A'
        'e;XiV<Yjk07_*<^93WN~0R{-'
        '<Jn;4!yK4oyPJb4J&|q}n2<vF+S`gHJC^wQ09NErpCMa8!(KgC+@^vcURH_|t7SXHE3rD3}YduI!u@{Dt-'
        '9xvwIaG$X&^vLG^k{=@j8IC_Qki|@C1-D5R1x+p_428@P+%buN*TqzIM$doLyv6-'
        'Lil*;S4hOU6ZQ;O2xI54HUaG=?2m6BE+<R+Wn&ZI`d^dE10_@zqGFJJVwT$Q5MCPYJDTN2r2Q$e==b%wiO5u2x~qjpX7CVl7Q%Re'
        'Bjhe7'
    ),
}
GLYPH_LIMIT = 0.30  # the worst glyph held out scored 0.26
GLYPH_MARGIN = 0.02  # over the next nearest
INK = 150


@lru_cache(maxsize=None)
def _glyphs(font):
    chars, *packed = GLYPHS[font]
    data = np.frombuffer(zlib.decompress(base64.b85decode("".join(packed))), np.uint8)
    size = GLYPH_CELLS[0] * GLYPH_CELLS[1]
    records = data.reshape(len(chars), size + 2)
    cells = records[:, :size].reshape(-1, *GLYPH_CELLS).astype(float) * 17
    aspects = records[:, size].astype(float) / records[:, size + 1]
    return chars, cells, aspects


def _cell(crop):
    """``crop`` averaged down to the cells of a glyph."""
    rows, columns = crop.shape
    tall, wide = GLYPH_CELLS
    return crop.repeat(tall, 0).repeat(wide, 1).astype(float).reshape(tall, rows, wide, columns).mean(axis=(1, 3))


def read_text(band, font, ink=INK):
    """The characters in the image ``band``, ``?`` for any not told for sure.
    Spaces are not reported. Each glyph must stand clear of the next."""
    chars, cells, aspects = _glyphs(font)
    dark = band < ink
    text = ""
    for first, last in _runs(dark.any(axis=0)):
        rows = np.flatnonzero(dark[:, first:last + 1].any(axis=1))
        crop = band[rows[0]:rows[-1] + 1, first:last + 1]
        aspect = crop.shape[1] / crop.shape[0]
        scores = np.abs(_cell(crop) - cells).mean(axis=(1, 2)) / 255 + 0.3 * np.abs(np.log(aspect / aspects))
        order = np.argsort(scores)
        sure = scores[order[0]] <= GLYPH_LIMIT and scores[order[1]] - scores[order[0]] >= GLYPH_MARGIN
        text += chars[order[0]] if sure else "?"
    return text


def _clock(text):
    """The hour of ``HH:00``, ``None`` for anything else."""
    match = re.fullmatch(r"(\d\d):00", text)
    return int(match.group(1)) if match and int(match.group(1)) < 24 else None


def _axis_of_ticks(ticks):
    """``(column, hour)`` per tick read, along a plot: hours past midnight of
    the plot's first day, ``None`` unless at least three neighbours agree on
    one line. Returns ``hour(column)``."""
    read = [(float(x), h) for x, h in ticks if h is not None]
    if len(read) < 3:
        return None
    # They all start in the afternoon: hours before noon are the next day's
    hours = [read[0][1] + (24 if read[0][1] < 12 else 0)]
    for _x, hour in read[1:]:
        hours.append(hours[-1] + (hour - hours[-1]) % 24)
    steps = np.diff(hours)
    columns = np.array([x for x, _h in read])
    if steps.min() <= 0 or steps.max() > 4:
        return None
    per_hour = (columns[-1] - columns[0]) / (hours[-1] - hours[0])
    if np.abs(columns - (columns[0] + (np.array(hours) - hours[0]) * per_hour)).max() > 1.5:
        return None
    first_x, first_hour = columns[0], hours[0]
    return lambda column: first_hour + (column - first_x) / per_hour


def _edges(value):
    return f"{value:.0f}" if value >= 10 else f"{value:.1f}"


def _log_band(ticks, height, unit, size):
    """``(top, bottom)`` of a log axis of ``height`` rows from its tick rows.
    Every whole ``unit`` has a tick, so the spacing alone says which is which:
    only one run of whole numbers fits, or the axis is refused."""
    if len(ticks) < 4:
        return None
    rows = np.array(ticks, float)
    fits = []
    for first in range(len(rows) + 1, 12):
        values = np.log10(first - np.arange(len(rows)))
        slope, offset = np.polyfit(values, rows, 1)
        if np.abs(slope * values + offset - rows).max() < 1.5:
            fits.append((slope, offset))
    if len(fits) != 1:
        return None
    slope, offset = fits[0]
    return tuple(f"{_edges(10 ** ((row - offset) / slope) * size)} {unit}" for row in (0, height))


def _linear_band(labelled, height, unit):
    """As ``_log_band`` for a linear axis, from ``(row, value)`` of labels read."""
    if len(labelled) < 3:
        return None
    rows, values = np.array(labelled, float).T
    slope, offset = np.polyfit(rows, values, 1)
    if slope >= 0 or np.abs(slope * rows + offset - values).max() > abs(slope) * 1.5:
        return None
    return tuple(f"{_edges(slope * row + offset)} {unit}" for row in (0, height))


def _dark_line(pixels):
    """Whether the strip ``pixels`` holds a rule across its length."""
    return (pixels < 120).any(axis=0).mean() > 0.97 if pixels.shape[0] < pixels.shape[1] else (pixels < 120).any(axis=1).mean() > 0.97


def _data_runs(grey, top, bottom, left, right, hour, least):
    """Slices of the columns of a plot with data in them: its ground is white."""
    filled = (grey[top:bottom, left:right] < 245).mean(axis=0) > 0.2
    runs = [(a + left, b + left + 1) for a, b in _runs(filled) if b - a + 1 >= least]
    joined = []
    for a, b in runs:  # a thin white line is not a gap
        if joined and a - joined[-1][1] < least:
            joined[-1] = (joined[-1][0], b)
        else:
            joined.append((a, b))
    return tuple((a, b, top, bottom, float(hour(a)), float(hour(b))) for a, b in joined)


# The composite of GOES, STIX, EOVSA and OVRO-LWA, one size since 2023: rows
# inside each panel's frame, all sharing the frame's columns and the time axis.
CONTEXT_SIZE = (3600, 3600)
CONTEXT_COLUMNS = (234, 3329)
CONTEXT_PANELS = {"eovsa": (1450, 2233), "lwa": (2380, 3424)}
CONTEXT_BANDS = {"eovsa": ("GHz", 1), "lwa": ("MHz", 10)}  # whole units with a tick, in the plot's unit


def _context_time(grey, day):
    """``hour(column)`` of a composite of ``day``, ``None`` to refuse."""
    if grey.shape != CONTEXT_SIZE or day is None:
        return None
    left, right = CONTEXT_COLUMNS
    for top, bottom in CONTEXT_PANELS.values():
        if not _dark_line(grey[bottom - 1:bottom + 3, left:right]) or not _dark_line(grey[top:bottom, left - 3:left + 1]):
            return None
    ticks = _centres(grey[3434, left:right] < 128)
    labels = [_clock(read_text(grey[3456:3488, int(x) + left - 60:int(x) + left + 61], "t30", 120)) for x in ticks]
    hour = _axis_of_ticks([(x + left, label) for x, label in zip(ticks, labels)])
    if hour is None:
        return None
    rows = _runs((grey[3500:3560, 1000:2600] < 120).any(axis=1))
    stamp = read_text(grey[3500 + rows[0][0]:3500 + rows[0][1] + 1, 1000:2600], "t30", 120) if rows else ""
    return hour if stamp.endswith(day.strftime("%Y%b%d]")) else None


def read_context(grey, day):
    """What a composite of ``day`` holds: ``{"eovsa": ..., "lwa": ...}`` of
    their Registrations, which have no slices for a panel with no data, ``None``
    if the plot is refused. Hours run on past 24 into the next day."""
    hour = _context_time(grey, day)
    if hour is None:
        return None
    left, right = CONTEXT_COLUMNS
    read = {}
    for key, (top, bottom) in CONTEXT_PANELS.items():
        unit, size = CONTEXT_BANDS[key]
        slices = _data_runs(grey, top, bottom, left, right, hour, 12)
        ticks = _centres(grey[top:bottom, left - 5] < 128)
        band = _log_band(ticks, bottom - top, unit, size)
        if not slices:
            read[key] = Registration(())
        elif band is None:
            return None
        else:
            read[key] = Registration(slices, *band, "sfu, log colour")
    return read


def _context(grey, day):
    """The whole composite on the day, for the large view's cursor."""
    hour = _context_time(grey, day)
    if hour is None:
        return None
    left, right = CONTEXT_COLUMNS
    return Registration(((left, right, 0, grey.shape[0], float(hour(left)), float(hour(right))),))


CONTEXT_ABOVE_LWA = 2306  # a row between the EOVSA panel and the OVRO-LWA one
CONTEXT_GAPS_NOTE = "The composite's OVRO-LWA panel closes up its gaps: the cursor there follows the FITS's true times"
CONTEXT_UNPROVEN_NOTE = "The cursor stops above the composite's OVRO-LWA panel, whose times could not be proved for this date"


def composite_view(image, day, times):
    """The composite as the large view's cursor has to take it: the panels
    above share the plot's time axis, which does not hold for OVRO-LWA's, as
    its samples are closed up. ``None`` if the plot is refused."""
    grey = grey_of(image)
    whole = _context(grey, day)
    if whole is None:
        return None
    lwa = read_context(grey, day)
    lwa = lwa["lwa"] if lwa else Registration(())
    if not lwa.slices:
        return whole
    placed = place_lwa(grey, lwa, "context", times(day) if times else None)
    above = (whole, 0, CONTEXT_ABOVE_LWA)
    if placed is None:
        return whole._replace(note=CONTEXT_UNPROVEN_NOTE, panels=(above,))
    panel = (placed, *CONTEXT_PANELS["lwa"])
    return whole._replace(note=CONTEXT_GAPS_NOTE if lwa_note(placed) else "", panels=(above, panel))


XSP_FRAME = (32, 397, 57, 925)  # top, bottom, left, right
XSP_NOTE = "EOVSA's daily plot: the GOES curve and calibration marks are drawn over the data"
XSP_TICKS = (90, 224, 357, 491, 625, 758, 892)
# EOVSA's time labels are slanted, and drawn alike to the bit on 29 plots from 2017
# to 2026: the print of each, as Nobeyama's and STEREO's.
XSP_STAMPS = {
    "6f22b262": 14, "c7ac8e92": 16, "5039b728": 18, "a4934fe9": 20, "ec2b105f": 22, "2df8eb73": 0, "ef3c1c4d": 2,
}


def print_of(pixels, length=12):
    """A short name for exactly these pixels, as ink on a ground."""
    import hashlib

    return hashlib.sha1(np.packbits(pixels > 127).tobytes()).hexdigest()[:length]


def _xsp(grey, day):
    """EOVSA's daily plot: 1-minute data, on a linear axis, in the plot's units."""
    top, bottom, left, right = XSP_FRAME
    if grey.shape[0] != 468 or not 1040 <= grey.shape[1] <= 1090 or day is None:
        return None
    if not _dark_line(grey[bottom - 1:bottom + 2, left:right]) or not _dark_line(grey[top:bottom, left - 1:left + 2]):
        return None
    if read_text(grey[4:30, 300:700], "t12")[-10:] != day.isoformat():
        return None
    ticks = _centres(grey[399, left:right] < 128)
    if len(ticks) != len(XSP_TICKS) or max(abs(a + left - b) for a, b in zip(ticks, XSP_TICKS)) > 1:
        return None
    hour = _axis_of_ticks([
        (x, XSP_STAMPS.get(print_of(grey[402:440, x - 42:x + 8], 8))) for x in XSP_TICKS
    ])
    if hour is None:
        return None
    labelled = []
    for row in _centres(grey[:bottom + 2, left - 2] < 128):
        text = read_text(grey[max(int(row) - 8, 0):int(row) + 9, 20:52], "t10")
        if text.isdigit():
            labelled.append((row, int(text)))
    band = _linear_band(labelled, 0, "GHz")
    slope_band = _linear_band([(row - top, value) for row, value in labelled], bottom - top, "GHz")
    if band is None or slope_band is None:
        return None
    slices = _data_runs(grey, top + 1, bottom, left + 1, right, hour, 5)
    return Registration(slices, *slope_band, "1 min, arb. units", XSP_NOTE)


LWA_FRAME = (37, 361, 62, 716)  # top, bottom, left, right
LWA_DAILY_EDGE = 1  # columns left of the slice: its samples fill the frame from the frame's own line, on 13 dates of 2024 to 2026
LWA_DAILY_ROUNDING = 1  # columns of the axis's end: its ticks cannot tell less from the end of the data
FITS_BLOCK = 2880
LWA_RUN_GAP = 5 / 3600  # hours without a sample that end a run: the files' joins lose a minute
LWA_CHANNEL = 45.0  # MHz: on every plot, and not the band's edge
LWA_LEVELS = 4096  # of that channel, side by side
LWA_FLOOR = 0.1
LWA_FLOOR_OF_MEDIAN = 3  # a file on another scale has its floor here: 2024-03-23's are 1e-5
LWA_LAG_SEARCH = 0.04  # of the width, to either side
LWA_LAG_LIMIT = 0.004  # the best match has to be this near: about 3 minutes in 12 hours
LWA_LEAST_MATCH = 0.4
LWA_SPAN_LIMIT = 0.15  # hours between the ends of the table and of the plot, whose ticks can run on
LWA_NOTE_GAP = 3  # minutes


def _lwa_daily(grey, day):
    """OVRO-LWA's daily plot. Its title gives the first second and the date; the
    scale is the ticks', as on some days the axis does not end where the title
    does (2024-03-23: 5 minutes in 7 hours)."""
    top, bottom, left, right = LWA_FRAME
    if grey.shape != (400, 800) or day is None:
        return None
    if not _dark_line(grey[top - 1:top + 2, left:right]) or not _dark_line(grey[bottom - 1:bottom + 2, left:right]):
        return None
    title = read_text(grey[5:32], "t12")
    match = re.fullmatch(r"(\d{4}-\d\d-\d\d)(\d\d):(\d\d):(\d\d)-\d{4}-\d\d-\d\d\d\d:\d\d:\d\d", title)
    if not match or match.group(1) != day.isoformat():
        return None
    begins = int(match.group(2)) + int(match.group(3)) / 60 + int(match.group(4)) / 3600
    ticks = _centres(grey[363, left - 12:right + 8] < 128)
    labels = [_clock(read_text(grey[366:384, int(x) + left - 36:int(x) + left + 13], "t10")) for x in ticks]
    hour = _axis_of_ticks([(x + left - 12, label) for x, label in zip(ticks, labels)])
    if hour is None or abs(hour(left) - begins) > 0.1:
        return None
    labelled = []
    for row in _centres(grey[top:bottom + 1, left - 2] < 128):
        text = read_text(grey[top + int(row) - 8:top + int(row) + 9, 28:56], "t10")
        if text.isdigit():
            labelled.append((row, int(text)))
    band = _linear_band(labelled, bottom - top, "MHz")
    if band is None:
        return None
    return Registration(((left + 1, right, top + 1, bottom, float(hour(left + 1)), float(hour(right))),), *band, "sfu, log colour")


SMALL_HOURS_ELSEWHERE = "The first hours UTC are on the previous day's plot, with another band"
SMALL_HOURS_UNPROVEN = "OVRO-LWA's first hours UTC are on the previous day's plot, whose times could not be proved"
LWA_GAPS_NOTE = "OVRO-LWA's plots close up its gaps: the lane has the true times, from the FITS"


def one_day(registration, shift, begin, end):
    """The slices of ``registration`` between hours ``begin`` and ``end`` of a
    plot, on a lane where its hour ``shift`` is midnight."""
    cut = []
    for left, right, top, bottom, start, last in registration.slices:
        a, b = max(start, begin), min(last, end)
        if b - a < 1 / 120:
            continue
        per_hour = (right - left) / (last - start)
        cut.append((left + (a - start) * per_hour, left + (b - start) * per_hour, top, bottom, a - shift, b - shift))
    return cut


def stitch(parts):
    """``(image, Registration)`` of a lane from ``[(image, slices)]`` of plots
    with the same band: just the columns used, side by side."""
    pieces = [(image, part) for image, slices in parts for part in slices if round(part[1]) > round(part[0])]
    if not pieces:
        return None
    width = sum(round(part[1]) - round(part[0]) for _image, part in pieces)
    height = max(part[3] - part[2] for _image, part in pieces)
    out = QImage(width, height, QImage.Format_RGB32)
    painter = QPainter(out)
    x, slices = 0, []
    for image, (left, right, top, bottom, start, end) in pieces:
        run = round(right) - round(left)
        painter.drawImage(x, 0, image, round(left), top, run, bottom - top)
        slices.append((x, x + run, 0, height, start, end))
        x += run
    painter.end()
    return out, tuple(slices)


def _fits_cards(header):
    """``{keyword: value}`` of a FITS header, and whether it ended here."""
    cards, ended = {}, False
    for start in range(0, len(header), 80):
        card = header[start:start + 80].decode("ascii", "replace")
        if card.rstrip() == "END":
            ended = True
            break
        if card[8:10] == "= ":
            value = card[10:].strip()
            value = value.split("'")[1].strip() if value.startswith("'") else value.split("/")[0].strip()
            cards[card[:8].strip()] = value
    return cards, ended


def _fits_header(read, offset):
    """``(cards, bytes it takes)`` of the header at ``offset``."""
    data = b""
    for _block in range(8):
        data += read(offset + len(data), offset + len(data) + FITS_BLOCK - 1)
        cards, ended = _fits_cards(data)
        if ended:
            return cards, len(data)
    raise ValueError("A FITS header that does not end")


def _in_blocks(size):
    return -(-size // FITS_BLOCK) * FITS_BLOCK


def read_lwa_table(read, day):
    """What the plots of ``day`` leave out, from its FITS: ``read(first, last)``
    gives those bytes of the file. Runs of samples ``[first, last, start hour,
    end hour]`` with hours from midnight of ``day``, how many samples, and the
    levels of one channel side by side, as the plots have them. ``None`` if the
    file is not of ``day`` or not as these files are."""
    primary, head = _fits_header(read, 0)
    samples, channels = int(primary["NAXIS1"]), int(primary["NAXIS2"])
    width = abs(int(primary["BITPIX"])) // 8
    dtype = {4: ">f4", 8: ">f8"}[width]
    begin = primary.get("DATE_OBS", "")
    if not begin.startswith(day.isoformat()) or not primary.get("FILENAME", "").endswith(f"{day:%Y%m%d}.fits"):
        return None
    size = width * samples * channels * int(primary["NAXIS3"]) * int(primary["NAXIS4"])
    offset = head + _in_blocks(size)
    stamps = None
    for _extension in range(4):
        cards, length = _fits_header(read, offset)
        row, rows = int(cards["NAXIS1"]), int(cards["NAXIS2"])
        if cards.get("EXTNAME") == "UT":
            if (row, rows, cards.get("TTYPE1"), cards.get("TTYPE2")) != (8, samples, "mjd", "time"):
                return None
            stamps = np.frombuffer(read(offset + length, offset + length + row * rows - 1), ">i4")
            break
        offset += length + _in_blocks(row * rows)
    if stamps is None or stamps.size != samples * 2:
        return None
    midnight = (day - date(1858, 11, 17)).days
    hours = (stamps[::2].astype(float) - midnight) * 24 + stamps[1::2] / 3.6e6
    if not 0 <= hours[0] < 24 or (np.diff(hours) < 0).any():
        return None
    breaks = np.flatnonzero(np.diff(hours) > LWA_RUN_GAP) + 1
    runs = [
        [int(first), int(last - 1), float(hours[first]), float(hours[last - 1])]
        for first, last in zip(np.r_[0, breaks], np.r_[breaks, samples]) if last - first > 1
    ]

    low, high = float(primary["FREQMIN"]) * 1e3, float(primary["FREQMAX"]) * 1e3
    channel = round((LWA_CHANNEL - low) / (high - low) * (channels - 1))
    first = head + width * samples * channel
    levels = np.frombuffer(read(first, first + width * samples - 1), dtype).astype(float)
    if levels.size != samples:
        return None
    above = levels[np.isfinite(levels) & (levels > 0)]
    floor = min(LWA_FLOOR, LWA_FLOOR_OF_MEDIAN * float(np.median(above))) if above.size else LWA_FLOOR
    levels = np.log10(np.clip(np.nan_to_num(levels, nan=floor), floor, None))
    wide = min(LWA_LEVELS, samples)
    bins = (np.arange(samples) * wide // samples).astype(int)
    shown = np.bincount(bins, levels, wide) / np.maximum(np.bincount(bins, minlength=wide), 1)
    return {"samples": samples, "runs": runs, "levels": [round(float(v), 3) for v in shown]}


def _same_run(row, levels):
    """Whether the plot's ``row`` is the run of samples ``levels`` in the same
    columns: it must match best where it stands, and not by chance."""
    wide = len(row)
    levels = np.interp(np.linspace(0, len(levels) - 1, wide), np.arange(len(levels)), levels)
    reach, step = int(wide * LWA_LAG_SEARCH), max(1, wide // 1000)

    def match(lag):
        a, b = row[max(lag, 0):wide + min(lag, 0)], levels[max(-lag, 0):wide + min(-lag, 0)]
        a, b = a - a.mean(), b - b.mean()
        scale = np.sqrt((a * a).sum() * (b * b).sum())
        return float((a * b).sum() / scale) if scale else 0.0

    score, lag = max((match(lag), lag) for lag in range(-reach, reach + 1, step))
    return score >= LWA_LEAST_MATCH and abs(lag) <= wide * LWA_LAG_LIMIT + 2


def place_lwa(grey, registration, kind, table):
    """``registration`` of an OVRO-LWA plot moved to the times of ``table``,
    with its real gaps. ``None`` if there is no table, it does not span the
    plot, or the plot is not the run of samples the table goes with."""
    if not registration.slices:
        return registration
    if not table or not table.get("runs"):
        return None
    first, last = registration.slices[0], registration.slices[-1]
    runs, samples = table["runs"], table["samples"]
    left, right = first[0], last[1]
    if kind != "context":  # its axis can run on past the last sample
        left -= LWA_DAILY_EDGE
        tail = (last[5] - runs[-1][3]) * (last[1] - first[0]) / (last[5] - first[4])
        right -= tail if tail > LWA_DAILY_ROUNDING else 0
    if abs(runs[0][2] - first[4]) > LWA_SPAN_LIMIT or abs(runs[-1][3] - last[5]) > LWA_SPAN_LIMIT:
        return None
    top_hz, bottom_hz = (float(text.split()[0]) for text in (registration.top, registration.bottom))
    if kind == "context":
        place = (np.log10(top_hz) - np.log10(LWA_CHANNEL)) / (np.log10(top_hz) - np.log10(bottom_hz))
    else:
        place = (top_hz - LWA_CHANNEL) / (top_hz - bottom_hz)
    rows = first[3] - first[2]
    row = first[2] + int(place * rows)
    middle = max(rows // 80, 2)
    frame = 0 if kind == "context" else LWA_DAILY_EDGE  # (the frame's line is no data)
    if not _same_run(grey[row - middle:row + middle + 1, round(left) + frame:round(right)].astype(float).mean(axis=0), np.array(table["levels"])):
        return None
    per_sample = (right - left) / (samples - 1)
    placed = tuple(
        (left + a * per_sample, left + b * per_sample, first[2], first[3], start, end) for a, b, start, end in runs
    )
    return registration._replace(slices=placed)


def lwa_note(registration):
    """What a reader of the archive's plots should know, on a day with a gap."""
    longest = max((b[4] - a[5] for a, b in zip(registration.slices, registration.slices[1:])), default=0)
    return LWA_GAPS_NOTE if longest * 60 >= LWA_NOTE_GAP else ""


def _one_plot(kind, source, image, day, times=None):
    """The Registration of ``source`` in a plot of ``kind``, ``None`` to refuse.
    ``times(day)`` gives OVRO-LWA's table of a day, ``None`` if there is none."""
    grey = grey_of(image)
    read = _as_drawn(kind, source, grey, day)
    if source == "lwa" and read is not None:
        return place_lwa(grey, read, kind, times(day) if times else None)
    return read


def _as_drawn(kind, source, grey, day):
    """The Registration of ``source`` by the plot's own axis."""
    if kind == "context":
        read = read_context(grey, day)
        return None if read is None else read[source]
    return (_xsp if source == "eovsa" else _lwa_daily)(grey, day)


def _reaches_next_day(kind, source, image, day):
    """Whether a plot not proved runs past midnight, by its own axis: a plot
    that ends before it has nothing of the next day to prove."""
    read = _as_drawn(kind, source, grey_of(image), day)
    return read is None or read.shows(24.0, 48.0)


def lwa_plot(image, day, times):
    """The Registration of a plot as the archive drew it, for the large view:
    the composite's, or its own."""
    if image.isNull():
        return None
    kind = "context" if (image.height(), image.width()) == CONTEXT_SIZE else "lwa"
    return _one_plot(kind, "lwa", image, day, times)


def ovsa_lane(source, day, load, times=None):
    """``(image, Registration)`` of the lane of ``source`` (``eovsa`` or
    ``lwa``) on ``day``, ``None`` if no plot could be read, and with no slices
    if the plots say there is no data. ``load(day, kind)`` gives the QImage of
    a plot, ``None`` if there is none, ``times`` as for ``_one_plot``. The
    composite is the better of the two, being calibrated and clean, so only it
    is used where it can be."""
    before = day - timedelta(days=1)
    plots = []
    for kind in ("context", source):
        today, earlier = load(day, kind), load(before, kind)
        known = None if today is None else _one_plot(kind, source, today, day, times)
        previous = None if earlier is None else _one_plot(kind, source, earlier, before, times)
        if earlier is not None and previous is None and not _reaches_next_day(kind, source, earlier, before):
            earlier = None
        plots.append((kind, today, known, earlier, previous))
    if all(known is None and previous is None for _kind, _today, known, _earlier, previous in plots):
        return None
    # The day's own hours decide: a plot with only the small hours would hide them
    plots.sort(key=lambda plot: not (plot[2] and plot[2].slices))
    for kind, today, known, earlier, previous in plots:
        unproven = earlier is not None and previous is None
        known = known if known is not None and known.slices else None
        previous = previous if previous is not None and previous.slices else None
        if known is None and previous is None:
            continue
        lead = known or previous
        note = ""
        parts = []
        if previous is not None and known is not None and (previous.top, previous.bottom) != (known.top, known.bottom):
            previous = None
            note = SMALL_HOURS_ELSEWHERE
        if previous is not None:
            parts.append((earlier, one_day(previous, 24.0, 24.0, 48.0)))
        elif known is not None and kind == "context":
            evening = plots[1 - [plot[0] for plot in plots].index("context")][4]
            if evening is not None and evening.shows(24.5, 48.0):  # the composite can leave out what its plot has
                note = SMALL_HOURS_ELSEWHERE
            elif source == "lwa" and unproven:
                note = SMALL_HOURS_UNPROVEN
        if known is not None:
            parts.append((today, one_day(known, 0.0, 0.0, 24.0)))
        made = stitch(parts)
        if made is not None:
            placed = Registration(made[1], lead.top, lead.bottom, lead.scale, note)
            notes = (note, lead.note, lwa_note(placed) if source == "lwa" else "")
            return made[0], placed._replace(note=" · ".join(part for part in notes if part))
    if any((today and known is None) or (earlier and previous is None) for _kind, today, known, earlier, previous in plots):
        return None  # a plot not proved says nothing, nor does "no data" stand beside it
    return QImage(), Registration(())


LEARMONTH_SITE = 3
LEARMONTH_BAND = (25, 180)  # MHz
LEARMONTH_BIN = 4  # records to a column, of 3 s each: 12 s, as wide as a burst of interest
LEARMONTH_ROWS = 620  # 0.25 MHz each, the coarser band's channel
LEARMONTH_RUN_GAP = 60  # seconds without a record that end a run
LEARMONTH_HOT = 4  # spreads over a channel's median


def _is_learmonth(path):
    """Whether a file starts as the format says, so that a web page or another
    site's file is not read as levels."""
    return learmonth_head(np.fromfile(path, np.uint8, count=24))


def learmonth_head(head):
    """As ``_is_learmonth`` for the file's first 24 bytes."""
    head = np.frombuffer(head, np.uint8) if isinstance(head, bytes) else head
    if len(head) < 24:
        return False
    fields = head[8:].astype(int)
    a_start, a_end, a_bytes = fields[0] * 256 + fields[1], fields[2] * 256 + fields[3], fields[4] * 256 + fields[5]
    b_start, b_end, b_bytes = fields[8] * 256 + fields[9], fields[10] * 256 + fields[11], fields[12] * 256 + fields[13]
    return head[6] == LEARMONTH_SITE and (a_start, a_end, a_bytes, b_start, b_end, b_bytes) == (25, 75, 401, 75, 180, 401)


def _viridis():
    from matplotlib import colormaps

    return (colormaps["viridis"](np.arange(256))[:, :3] * 255).astype(np.uint8)


def spectrogram_image(levels):
    """A QImage of ``levels``, columns x rows from the top, 0 to 1."""
    colours = _viridis()[np.clip(levels * 255, 0, 255).astype(np.uint8)]
    rows, columns = colours.shape[:2]
    pixels = np.dstack((colours[..., 2], colours[..., 1], colours[..., 0], np.full((rows, columns), 255, np.uint8)))
    image = QImage(pixels.tobytes(), columns, rows, columns * 4, QImage.Format_RGB32)
    return image.copy()


def learmonth_lane(paths, day):
    """``(image, Registration)`` of Learmonth's records of ``day`` from its
    files, ``None`` if there are none. A file holds its station's day, from
    about 23:00 UT the day before, so the end of ``day`` is in the next file.
    Each channel is shown against its own median and spread, which takes out
    the receiver's shape and most of the steady interference."""
    from ..radio_data_downloader.radio_data_downloader import read_srs

    runs = []
    for path in paths:
        if not _is_learmonth(path):
            logger.info("Not a Learmonth file: %s", path)
            continue
        runs.append(read_srs(path))
    if not runs:
        return None
    times = np.concatenate([run[0] for run in runs])
    levels = np.concatenate([run[2] for run in runs])
    begin = np.datetime64(day, "s")
    seconds = (times - begin).astype(float)
    inside = (seconds >= 0) & (seconds < 86400)
    if not inside.any():
        return None
    order = np.argsort(seconds[inside], kind="stable")
    seconds, levels = seconds[inside][order], levels[inside][order].astype(float)
    channels = np.argsort(runs[0][1], kind="stable")
    levels = levels[:, channels]

    medians = np.median(levels, axis=0)
    spread = np.median(np.abs(levels - medians), axis=0) * 1.48 + 1
    strength = (levels - medians) / spread
    # A channel hot far more often than the rest is interference, whatever the
    # sun is doing: a burst is minutes of a day, and across many channels
    hot = (strength > LEARMONTH_HOT).mean(axis=0)
    good = hot <= max(0.02, 4 * np.median(hot))
    if good.any():
        strength = strength[:, good]
        channels = channels[good]

    frequencies = runs[0][1][channels]
    wanted = np.linspace(*LEARMONTH_BAND[::-1], LEARMONTH_ROWS)
    picked = np.clip(np.searchsorted(frequencies, wanted), 0, len(frequencies) - 1)

    breaks = np.flatnonzero(np.diff(seconds) > LEARMONTH_RUN_GAP) + 1
    columns, slices, x = [], [], 0
    for first, last in zip(np.r_[0, breaks], np.r_[breaks, len(seconds)]):
        count = (last - first) // LEARMONTH_BIN
        if count < 1:
            continue
        chunk = strength[first:first + count * LEARMONTH_BIN][:, picked]
        columns.append(np.median(chunk.reshape(count, LEARMONTH_BIN, -1), axis=1))
        slices.append((x, x + count, 0, LEARMONTH_ROWS, seconds[first] / 3600, seconds[last - 1] / 3600))
        x += count
    if not columns:
        return None
    image = spectrogram_image(np.clip((np.concatenate(columns).T + 1) / 9, 0, 1))
    return image, Registration(tuple(slices), "180 MHz", "25 MHz", "over channel median")

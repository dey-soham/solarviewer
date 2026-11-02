"""
The solar events viewer's Radio spectra tab: the day's dynamic spectra, as an
aligned stack on the window's time column or as the archive's own images.

An image only joins the stack once its time axis has been read from its pixels
(:func:`register`). A silent offset is worse than no axis, so anything not
recognised exactly is refused, and stays available as the archive drew it.
"""

import base64
import hashlib
import logging
import os
import re
import shutil
import zlib
from collections import namedtuple
from datetime import datetime
from functools import lru_cache, partial
from itertools import product
from typing import NamedTuple

import numpy as np
from PyQt5.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QUrl, pyqtSignal
from PyQt5.QtGui import (
    QBrush,
    QColor,
    QDesktopServices,
    QFont,
    QFontMetrics,
    QIcon,
    QImage,
    QImageReader,
    QKeySequence,
    QPainter,
    QPen,
)
from PyQt5.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QShortcut,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ..ui.activity import ActivityLabel
from ..ui.download_parts import abandon
from ..ui.errors import show_error
from ..ui.geometry import fit_to_screen
from ..ui.icons import themed_icon
from ..ui.styles import (
    CONTROL_HEIGHT,
    FONT_META,
    FONT_SMALL,
    LIGHT_PALETTE,
    MONO_FAMILY,
    SPACE_M,
    SPACE_S,
    SPACE_XS,
    set_hand_cursor,
    theme_manager,
    themed,
)
from ..ui.widgets import button_bar, dialog_layout, empty_note, kicker, link_button, segmented

from .cache import _noaa_cache
from .widgets import ImageZoomView, source_note
from .workers import DHSpectraWorker, StoppableThread, expected_sources

logger = logging.getLogger(__name__)

CDAW_PAGE = "https://cdaw.gsfc.nasa.gov/"
NICT_PAGE = "https://solobs.nict.go.jp/radio/cgi-bin/MainDisplay.pl"
NANCAY_PAGE = "https://rsdb.obs-nancay.fr/"
NORP_PAGE = "https://solar.nro.nao.ac.jp/norp/"

Source = namedtuple("Source", "key name top bottom scale save_name archive page space span")

# High frequency to low, ground before space. ``top`` and ``bottom`` are what
# the archive's own axis shows at the lane's edges once it is high side up.
SOURCES = (
    Source("norp", "NORP", "200 sfu", "−100 sfu", "1–17 GHz, R+L", "Nobeyama_NORP", "Nobeyama", NORP_PAGE, False, 1),
    Source("yamagawa", "Yamagawa", "9 GHz", "70 MHz", "0–30 dB", "NICT_Yamagawa", "NICT", NICT_PAGE, False, 3),
    Source("hiras", "HiRAS", "", "", "no colour scale", "NICT_HiRAS", "NICT", NICT_PAGE, False, 3),
    Source("orfees", "ORFEES", "1 GHz", "144 MHz", "no colour scale", "Nancay_ORFEES", "Nançay", NANCAY_PAGE, False, 1),
    Source("nda", "NDA", "80 MHz", "10 MHz", "no colour scale", "Nancay_NDA", "Nançay", NANCAY_PAGE, False, 1),
    Source("stereo_a", "STEREO-A", "16 MHz", "175 kHz", "no colour scale", "STEREO_A", "CDAW", CDAW_PAGE, True, 2),
    Source("stereo_b", "STEREO-B", "16 MHz", "175 kHz", "no colour scale", "STEREO_B", "CDAW", CDAW_PAGE, True, 2),
    Source("wind", "WIND/WAVES", "14 MHz", "20 kHz", "10²–10⁶ sfu", "WIND_WAVES", "CDAW", CDAW_PAGE, True, 1),
)
BY_KEY = {source.key: source for source in SOURCES}
# The order of the contact sheet: wide ones lead their row
ORIGINALS_ORDER = ("yamagawa", "hiras", "norp", "orfees", "nda", "wind", "stereo_a", "stereo_b")
# Drawn with low frequency up; the stack turns these over, nothing else does
FLIPPED = ("orfees", "nda")

THIN_LANE = CONTROL_HEIGHT  # a lane of one line, with room for its link
LEAST_LANE = 40  # of a lane with an image
LANE_GAP = 3
GROUND_RULE = 5  # of the room over the first lane in space, its rule in the middle
SHEET_GAP = SPACE_S
CAPTION_HEIGHT = 20
GUTTER_LINE = 15  # the name's line in a lane's gutter
GAP_LEAST = 5 / 60  # hours: less than this is an edge of the plot, not a gap

IDLE, WAITING, READY, NO_FILE, UNREACHABLE = "idle", "waiting", "ready", "no file", "unreachable"
NOT_ALIGNED = "Time axis could not be read"
NOT_FETCHED = "Not fetched. Press Fetch to load it"
SPECTRA_EMPTY = "Press Fetch to load the day's spectra"
LANE_TIP = "Click to set the cursor, drag to zoom, double-click or press Enter to open large"
_decoding = set()  # threads still at it, which outlive a closed window


def hhmm(hours):
    minutes = int(round(hours * 60))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


class Registration(NamedTuple):
    """Where the day lies in an image. Each slice is ``(left, right, top,
    bottom, start, end)``: edges in image pixels, then hours. Where slices
    overlap the later one is drawn over the earlier."""

    slices: tuple

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


def _grey(image):
    """``image`` as a 2-D array of bytes."""
    grey = image.convertToFormat(QImage.Format_Grayscale8)
    bits = grey.constBits()
    bits.setsize(grey.byteCount())
    pixels = np.frombuffer(bits, np.uint8).reshape(grey.height(), grey.bytesPerLine())
    return pixels[:, : grey.width()].copy()


def _print(pixels):
    """A short name for exactly these pixels, as ink on a ground."""
    return hashlib.sha1(np.packbits(pixels > 127).tobytes()).hexdigest()[:12]


def _wind(grey):
    # Ticks every hour from column 120. JPEG leaves them a pixel's doubt
    if grey.shape != (800, 1600):
        return None
    ticks = np.flatnonzero(grey[722] > 120)
    if len(ticks) != 24 or np.abs(ticks - (120 + np.arange(24) * 1360 / 24)).max() > 1:
        return None
    if (grey[719, 130:1470] > 200).mean() < 0.95:
        return None
    per_hour = 1360 / 24
    return Registration(((121, 1479, 40, 718, 0.5 / per_hour, 1358.5 / per_hour),))


YAMAGAWA_LEFT, YAMAGAWA_HOUR = 378, 450


def _real_hours(grey):
    """The hours of a stitched NICT image with data: the archive's "No DATA"
    strip is white, an hour nothing came for is black."""
    return [
        hour for hour in range(24)
        if 15 < grey[100:500, YAMAGAWA_LEFT + YAMAGAWA_HOUR * hour + 20:YAMAGAWA_LEFT + YAMAGAWA_HOUR * (hour + 1) - 20].mean() < 150
    ]


def _yamagawa(grey):
    if grey.shape != (605, 11178):
        return None
    if (grey[10:550, YAMAGAWA_LEFT - 1] > 200).mean() < 0.95 or (grey[10:550, YAMAGAWA_LEFT - 5] < 60).mean() < 0.9:
        return None
    hours = _real_hours(grey)
    edges = [YAMAGAWA_LEFT + YAMAGAWA_HOUR * hour for hour in hours]
    # each hour's first column is a dark rule, in any hour with data
    if not hours or (grey[10:550, edges] > 60).mean(axis=0).max() > 0.2:
        return None
    if (grey[559, edges[0]:edges[0] + YAMAGAWA_HOUR] < 200).any():
        return None
    runs = []
    for hour in hours:
        if runs and runs[-1][1] == hour:
            runs[-1][1] = hour + 1
        else:
            runs.append([hour, hour + 1])
    # A strip stops short of the hour's edge where the next has no data
    white = grey[10:550].mean(axis=0) > 200
    slices = []
    for a, b in runs:
        left, right = YAMAGAWA_LEFT + YAMAGAWA_HOUR * a, YAMAGAWA_LEFT + YAMAGAWA_HOUR * b
        left += int(np.flatnonzero(~white[left + 1:left + 40])[0]) + 2
        right -= int(np.flatnonzero(~white[right - 40:right - 1][::-1])[0]) + 2
        hour = lambda x: (x - YAMAGAWA_LEFT) / YAMAGAWA_HOUR
        slices.append((left, right, 1, 557, hour(left), hour(right)))
    return Registration(tuple(slices))


NORP_TICKS = [122, 170, 219, 267, 315, 364, 412, 460, 508, 557, 605]  # 22:00 to 08:00, hourly
NORP_MIDNIGHT = "61f53eeaa7c2"  # the label under the 00:00 tick


def _norp(grey):
    if grey.shape != (800, 640):
        return None
    if list(np.flatnonzero(grey[27, 91:613] > 120) + 91) != NORP_TICKS:
        return None
    if (grey[40:340, 90] < 150).any() or (grey[40:340, 613] < 150).any():
        return None
    if _print(grey[350:364, 196:244]) != NORP_MIDNIGHT:
        return None  # same ticks on another plot's hours
    per_hour = (NORP_TICKS[-1] - NORP_TICKS[0]) / 10
    hour = lambda x: (x - NORP_TICKS[0] - 0.5) / per_hour - 2
    return Registration(((91, 613, 27, 345, hour(91), hour(613)),))


# The time stamp in the title of a STEREO panel, which is the rule in its middle
STEREO_STAMPS = {"5b650894bdce": 0.0, "52f84b9e0a83": 6.5, "fb3927447ee8": 13.0, "1afeedcda900": 19.5}
STEREO_PANELS = (0.0, 6.5, 13.0, 19.5)
STEREO_WIDTH, STEREO_PER_HOUR = 512, 60.14


def _stereo(grey):
    if grey.shape != (512, 2048):
        return None
    slices = []
    for panel, stamp in enumerate(STEREO_PANELS):
        x = STEREO_WIDTH * panel
        if STEREO_STAMPS.get(_print(grey[12:31, x + 430:x + 486])) != stamp:
            return None
        if (grey[50:460, x + 64:x + 66] < 230).any() or (grey[50:460, x + 244:x + 246] < 230).any():
            return None
        hour = lambda column: stamp - 3 + (column - 65) / STEREO_PER_HOUR
        slices.append((x + 66, x + 486, 44, 467, hour(66), hour(486)))
    return Registration(tuple(slices))


# ORFEES and NDA start at a different time each day, and the only record of it
# is the tick labels. Matplotlib draws one the same to within a mean of 0.78
# per pixel from day to day, against 1.27 for the nearest other label.
NANCAY_LABEL_NAMES = {
    "orfees": ("08:00", "09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00"),
    "nda": ("08:10", "08:20", "08:30", "08:40", "08:50", "09:00", "09:10", "09:20"),
}
NANCAY_LABEL_LIMIT = 1.0
# Labels closer than a crop's width run into each other's crops, and no one of
# them matches. As a whole run they still do: the right run scored 1.51 against
# 4.01 for the next, on 2021-07-03. Hence a limit, and a margin over the next.
NANCAY_RUN_LIMIT, NANCAY_RUN_MARGIN, NANCAY_RUN_LEAST = 2.0, 2.0, 6
NANCAY_LABEL_SIZE = (40, 74)  # rows 324 to 364, 60 pixels left of the tick to 14 right
NANCAY_LABELS = (  # base85 of the zlib of the crops, in the order above
    'c-rmV2~<_p8UWyT(=;`5L}lgzz63-By9@~;qM~3qKn|qEQ^O%CF!d3lhEEEm(8_SaS0ZJI^g={Ui4}-g3=T;cN+^)bgK(Me@VC$9'
    'TFG;j?f<Oz%>I8V_g>cGU+3(z&pF@u?>Tz|a7X^%@<E(<xHDC3t^f)OWMo&0ljoF)ss4&<A|$%$5MG=h^)18ZQA29~n*a$et8jVR'
    '+5baNT?gpeVtp2o=d_z|WC`T-O=a)%rmp2r6#K-wAnC*kaQA3opBDP4)x2xw6$+3cn*lkG?C%6vXUX5zX72zfj!RM?$nf87x2J67'
    '&Q$Nck~O&8Zw$cl5l%)nMf~j)<Z_-ZS+Z$5wB_%Q=h<QxyV%dTqfjgjsbhz3gKXex>O>=}!HaJ~ndys75MdY1_BrDMt~EMhzQ6j|'
    'fUf{j*xxGx^%g{1__gwEF7=ApN&u@&?PqzSpHl?Sv4fp~+<i-7gX!jUp)VE+zf}D3JZVxBL;t34^rz^XZt}7Y19*S@7hDZg=CrXm'
    'Q98T6^8;BeK=AZ(IO~0hZ*T2a43Oj+00C>D6C#G!b9ci}?O7jIMvT6&+8Lm6UZ9FQ)0M}P+0Tp9)?UzgYK55$v1`B#H#z&cXcn9Z'
    '@jHk*JYMR(n;kns%|$r!Z_tlzY4&_WjI+@yJx3F(@5IQ|ej)bIUm6JHaq>6?d7L~>9_RD8S|pDbX%gW*o**6P7^8$zw8uY`#%6Dq'
    'L^VS)(&L?Tf<Xndh9AOv+$0*>V9CgNWjK!;EG-PxD}fZ(m1bE0yD%O%^F1?3->6S4=)?hJVLYDb7Nkr&&<1kL{rtPD*5j5L!iSR<'
    '#r&DH9`_7}#==<Rzz#Kks&0=jo&^Q-jrWM)@eLMi!N}v}aq>6?#e)!^`-G(fsL^@4)(i@8(|NN?1le1j0C-)t160^dU+Xd2RljU|'
    '1G9eLd~rh!8HT4>iZqu{{yYuO=-7^U6W~dIMxXX2v+B4reIP5U+-M#OpFWz&7HYQ<|H?Tm2WW}zRRr#i>?|1R)XWQe_58)`rFz0N'
    'xHZZp>LdL~zC23yfA#b$EaA~7zJ}bOr!97HuUi?Uo9tvweN`q@66=+JJj0PC?=q^^dOzO<FSwinsOgu;(<jyPMMM3phJD7kv9=aU'
    '-6v$o<LvpWq<Q9-0p_?R0ozb2IJZD&?11<AE_x4|m$ECD>2vF~yT-*tj%}ZDinZ_I;bknxUWbnWiX-?gSZZI8uzeIdhHYb)1&z+H'
    'Dd6)V(L9M1F`fzi-(t`VA5_B{D0n>kT`K3X;~0eNGOOMiJ)aqPsjpJW+YyBZ)(>a`I5*(A!yk=|!yM5%;Yjb(>^0fP)?_dC@aR0H'
    '8fw__7Mn)_1-;&VMV2ed`A8-)IVDD(;xYYW#W_RX*xW`QCy!H5{0}%jeMLg62pm^!8nkAP?y?Sis%krS#@ah>J(lBYDAhd<&=fiN'
    'DrTY1#TT@~W!pV$X$svJ@#krpt0Nn>1i)ivCA)ap>Qus=>9EcB%?C`ELbfEX1K_AWKkU7H1o=u*VWIs=fP+@myc-`@xe5*LV-Iyx'
    'pIjef5W?5s0(stfI-TXq?R^GH-kLFnpX1jc*D;8-PyHmJa@Wq$nNDlIHfmn%&;eo22iQA<7_L59K3n3W!Q9(H6;S1Ge5~TA^?9x<'
    'y3m<B`kWA&sY7V(A65++{dhFr20NSv^X=uoGRG-7C&$?Z`1O9fy03uNWdn~|wHz1vQP_xb;muj|U=+uN?Pl}}%M6f(<G2uq`lr6+'
    'TZ-d&_tj4GNQqgt2pore^C6#z!SPB+4HJjs<F63M$>Zd43JQ$JsXYGMLFMuLAjd;l9#092y9ug#Y<V1tCmPN+v}|RohAfYlcrL9{'
    '+#1H(q!m{lhlXi6Y@_+S^z$$H%j1p0(ZfM=S%4{zmvqau&RpYXY<Zl;8RII=O*qK%IO~(IPl3+YqS{69xaa<4eZ8;o9#<FjeYF2B'
    'F+5&dJt9OTk6&sL$K%H2aZ)sSoPs<~9w(2J$L~av|HgAnh-i;rbJY)U98`e!c=5B#Sg+C9hxfSh<8sy})4vzN<LqCG!9bgZdFb0!'
    '0B$V``}Rv4zdEeP)u3)?ohjO^3GZ<hD)m|yer*2A<4QPag?bG0PJKLojqA{Z*u$gdc9oWZaB)0tU#I{Pg6iX>X!1A(d7L~>9)Bo2'
    'UL}&pFPgpD0ICMG#{u4zOgag8kF#$4ex#lMj{t2*kF)SjJZ>o5%B(_qTm`e!!F$eoVQ7!5VL}=tJ=S+4zCK<ttzvoaQDbue?eW`s'
    'lJVKKqgL_{)N;q;^>4?s&9-IUZM?_3#5iIzk5@tEoh3wjTy{Z}^AUNR6iptdAdnNt3FQA@kgG)k`8kn5erV`|cDCKnATP8V+PxZB'
    'kY6zy#Z1-#_yW1&Wv_yLZj<Mg|9tVm4f3oB18v?5v39tGFOYA0wS;{b5w1dmy!Hp->Ta1ch*BU2`Q*jGUH-iTa<#C{Vr{;}9MSF+'
    '$a4cH4EY!j@@lA&FWQLBLj`j3IC-3cLJW`71D(I4J9?n=_c#6n^+4wt%^k$}2Rc7Bl3iAd@IWW*HQHGx+<zQ}e4rEBrtXH;?<zF~'
    'a?}Hz&?wDKFmN2aqfxs?{10?qwB79*cXhoi1ph#%)nf~p?lw5zKtIrVRN~J*CP~Zj4|IB%pJiQPY#rh_q`hIKWR!i>9+NF%csxJQ'
    '-CC6KRjM^l0EppnS)M58Bl0*YnmkVNCyOr&m7?THGm+~0X5vfHP(OKn)hRcJ>v)f!f8+|lS@)Uvl{;&b$*dSZEETbSnc-E;7)7Qr'
    'T>z!e&E?J`50>a15ajAN?mGJz=D~d${A%LCXXajoE~x4EMm<2JUI%Zc#$#~O?oHwQ@2J0rH8l7Jqgs23N2X9j+@?R)&=N7&f?5HW'
    'Wx=gNs%yuNa3s*q2lO9JPu%R31SNc3@|XF5G}Y)@j|HnA<r>uDuK9q3@RvxFcv_~%d_Zv9whzzs%Dv_T+R4!!X+EI)3Yrh-zJtae'
    'Cy$fIDgJyx^8wvQh%p~fn(xc|ZbKe=KA^@Ih6c_bvbhDOAm;<>h?;SMok^drhrC|M`GB@0sJbAtUkyuwnh!|XU9a6IQ!%xyK+eyp'
    'V(k*T4Ad&*{G1w(tF2=+#bmU{;oE_Auw8ae1dku_N*=8%hR0PeN&0RQ$K%U`ILhPq@OUclj6*^mCy$fI$>Zd43i9}a<#Clr9&ZxK'
    '<EK5m*W^Pk+T-;L28V97eNm3~c-OvXuQEWpw?ZCve$J}}Oc|@gt2!YV<#Dw@-#qJn_D?Pc2*7$A5?(2*jMx1HT9F>_CRF+8ZG7L^'
    '+~sj0!}(EsW7YNWct=}`?LT<#)bHu>TiZMbz9o*wkL=`FpnG`y1lKoF<Z<#id7L~>9;YCWKb#&H=oBx4#}yMKCjl;?Jr3$>ORuTy'
    'bt=;1ENYyhZ9ia((`z5j;{eB=Yh7l?UjJ!;@wmX%^cnD#%RKKwbbTDQE`*z-d(TuuJJRE@V|z$nC+Ft@>v1`(l#KmMxo6~IyvJ)}'
    '8vu&d@=Rv<U-zr<9y{)FzY0&#{VL>f@;G^%JWfF#e@Hzp*8M8+3UNGsQaYeSErQ2uCO^JOvwGP2xMHiM6yRDTKrXgEUTpeCvekIq'
    'x69D=ao8=fiuq#wIDdfUNRQtN7sdwMZk>g^U*$7U)C-H2H6({`L4NUgOZQ5CXwEmac#rycdwZ(&2CioA;qf2;<*u_$9FHH`PW5r}'
    'IC-2rP9CSAAdidfap68zQSMi9^NlQl9DIE|_K9^t(uoz|j;)WkdxZjI$Ywwe*5hikcbM;wOHv@n5LX|s_g={wT<$jp;c;zy1i74N'
    '?~_f_c|BgFNfdV!ilrfS?9gqH%~u~!kdAYVQ9`Ndi%k$=7tQuLW4_`1K9t60Z<j<hLo)k&MWEh-XbZnqo(Afi6U@9jYq(Y#@<czU'
    '2u_ciL_-@a89A>EwDob0!RnaD4VD&$>Xkr>>q@gMfL)mSxS8*nN%}^8VnHVkAPeL1M7JPi+JQEZTkhxIU9}#!%+OqvTNLwW(t6x8'
    '7#a&>jRQLn54?AKeDN$Om~XsC1dng9U<*bbCy$fIDJb}ZhF7_dpy5@1J07gzRkR%kJ-mvR<LKd4e&INBc$K?2E(GuJDm^%k8D6C)'
    '$2o^rA&wKriR0vP@;Jp`JE%T>A3@`*{B}H8<Ev=f13kXVuR)F(U*#^4W5!qM0doHFReFFNGrmd>kn@hO(i7x}@l^=q1aboT-SHDa'
    'FI$r'
)


@lru_cache(maxsize=None)
def _nancay_labels(source):
    crops = zlib.decompress(base64.b85decode("".join(NANCAY_LABELS)))
    crops = np.frombuffer(crops, np.uint8).reshape(-1, *NANCAY_LABEL_SIZE).astype(float)
    first = 0 if source == "orfees" else len(NANCAY_LABEL_NAMES["orfees"])
    return dict(zip(NANCAY_LABEL_NAMES[source], crops[first:]))


def _label_distance(grey, tick, crop):
    """How unlike ``crop`` the label at column ``tick`` is, ``None`` at the edge."""
    if tick - 61 < 0 or tick + 15 > grey.shape[1]:
        return None
    return min(
        np.abs(grey[324 + dy:364 + dy, tick - 60 + dx:tick + 14 + dx].astype(float) - crop).mean()
        for dx, dy in product((-1, 0, 1), (-1, 0, 1))
    )


def _label_minutes(name):
    return int(name[:2]) * 60 + int(name[3:])


def _read_label(grey, tick, labels):
    """The minutes of the day under column tick, None if it is no
    label known exactly."""
    best, name = NANCAY_LABEL_LIMIT, None
    for label, crop in labels.items():
        distance = _label_distance(grey, tick, crop)
        if distance is not None and distance < best:
            best, name = distance, label
    return None if name is None else _label_minutes(name)


def _read_run(grey, ticks, labels):
    """``[(tick's index, minutes)]`` of the one run of labels that fits all the
    ticks together, ``None`` if no run stands out."""
    names = list(labels)
    runs = {}
    for first in range(1 - len(ticks), len(names)):
        pairs = [(i, names[first + i]) for i in range(len(ticks)) if 0 <= first + i < len(names)]
        distances = [_label_distance(grey, int(ticks[i]), labels[name]) for i, name in pairs]
        pairs = [pair for pair, distance in zip(pairs, distances) if distance is not None]
        distances = [distance for distance in distances if distance is not None]
        if len(pairs) >= NANCAY_RUN_LEAST:
            runs[first] = (float(np.mean(distances)), pairs)
    ranked = sorted(runs.values(), key=lambda run: run[0])
    if not ranked or ranked[0][0] > NANCAY_RUN_LIMIT:
        return None
    if len(ranked) > 1 and ranked[1][0] < ranked[0][0] * NANCAY_RUN_MARGIN:
        return None
    return [(i, _label_minutes(name)) for i, name in ranked[0][1]]


def _nancay(source, grey):
    if grey.shape != (400, 800):
        return None
    if (grey[60:310, [100, 720]] >= 100).any() or (grey[[48, 320], 110:710] >= 100).any():
        return None
    ticks = np.flatnonzero(grey[322, 100:] < 128) + 100
    if len(ticks) < 3 or np.ptp(np.diff(ticks)) > 1:
        return None
    labels = _nancay_labels(source)
    read = [(i, _read_label(grey, int(tick), labels)) for i, tick in enumerate(ticks)]
    read = [(i, minutes) for i, minutes in read if minutes is not None]
    if not any(read[k + 2][0] - read[k][0] == 2 for k in range(len(read) - 2)):
        read = _read_run(grey, ticks, labels) or []
    if len(read) < 3:
        return None
    # Every label read must lie on one line, and three must be neighbours
    step = (read[1][1] - read[0][1]) / (read[1][0] - read[0][0])
    if step <= 0 or any(m != read[0][1] + (i - read[0][0]) * step for i, m in read):
        return None
    indices = [i for i, _ in read]
    if not any(i + 1 in indices and i + 2 in indices for i in indices):
        return None
    first, last = read[0], read[-1]
    per_hour = (ticks[last[0]] - ticks[first[0]]) / ((last[1] - first[1]) / 60)
    hour = lambda x: first[1] / 60 + (x - ticks[first[0]] - 0.5) / per_hour
    return Registration(((101, 720, 49, 320, hour(101), hour(720)),))


READERS = {
    "wind": _wind,
    "yamagawa": _yamagawa,
    "norp": _norp,
    "stereo_a": _stereo,
    "stereo_b": _stereo,
    "orfees": partial(_nancay, "orfees"),
    "nda": partial(_nancay, "nda"),
}


def register(source, image, day=None):
    """How ``image`` of ``source`` lies on the day, ``None`` to refuse. Every
    format is known by its exact size and marks, and anything else refuses:
    older archives can draw the same size differently. ``day`` is for formats
    that change by date."""
    reader = READERS.get(source)
    if reader is None or image.isNull():
        return None
    return reader(_grey(image))


def sheet_image(source, image, registration):
    """What the contact sheet shows: display only, never saved. NORP's R+L
    panel; Yamagawa without its "No DATA" hours, a thin gap where they were."""
    if source == "norp" and image.size() == QSize(640, 800):
        return image.copy(0, 0, 640, 396)
    if source != "yamagawa" or registration is None:
        return image
    gap = 12
    runs = [(s[0], s[1] - s[0]) for s in registration.slices]
    width = YAMAGAWA_LEFT + sum(run[1] for run in runs) + gap * (len(runs) - 1)
    out = QImage(width, image.height(), QImage.Format_RGB32)
    out.fill(QColor("#ffffff"))  # as the image's own ground
    painter = QPainter(out)
    painter.drawImage(0, 0, image, 0, 0, YAMAGAWA_LEFT, image.height())
    x = YAMAGAWA_LEFT
    for left, run in runs:
        painter.drawImage(x, 0, image, left, 0, run, image.height())
        x += run + gap
    painter.end()
    return out


def kept_hours(registration):
    """``00–09, 22–24 UTC``: the hours a sheet image keeps."""
    return ", ".join(f"{round(s[4]):02d}–{round(s[5]):02d}" for s in registration.slices) + " UTC"


def image_format(path):
    """The file's real format as a suffix: the cache's names do not prove it."""
    reader = QImageReader(path)
    reader.setDecideFormatFromContent(True)
    found = bytes(reader.format()).decode().lower()
    return {"jpeg": "jpg"}.get(found, found) or "png"


class SpectrumDecoder(StoppableThread):
    """Decodes one spectrum and reads its axis: Yamagawa's stitched image
    alone is 27 MB."""

    decoded = pyqtSignal(str, str, object, object)  # source, path, QImage, Registration

    def __init__(self, source, path, day):
        super().__init__()
        self.source, self.path, self.day = source, path, day

    def run(self):
        image = QImage(self.path).convertToFormat(QImage.Format_RGB32)
        self.decoded.emit(self.source, self.path, image, register(self.source, image, self.day))


def _font(size, bold=False, mono=False):
    font = QFont(QApplication.font())
    font.setPointSize(size)
    font.setBold(bold)
    if mono:
        font.setFamily(MONO_FAMILY)
        font.setStyleHint(QFont.Monospace)
    return font


def _chip(painter, centre_x, centre_y, text, palette):
    """``text`` on the page's ground, for over the hatching."""
    width = painter.fontMetrics().horizontalAdvance(text) + SPACE_S * 2
    rect = QRectF(centre_x - width / 2, centre_y - 8, width, 16)
    painter.fillRect(rect, QColor(palette["window"]))
    painter.drawText(rect, Qt.AlignCenter, text)


class TimeRow(QWidget):
    """A row of the stack: a gutter, then the window's time column."""

    def __init__(self, axis, parent=None):
        super().__init__(parent)
        self.axis = axis
        self.rule_above = False
        self._press = self._drag = None  # where a click or a drag to zoom began, and has got to
        themed(self, lambda palette: self.update())

    def has_time(self):
        """Whether position in the column means time here."""
        return True

    def column(self):
        return self.axis.x_of(self.axis.start, self), self.axis.x_of(self.axis.end, self)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        left, right = self.column()
        palette = theme_manager.palette
        self.paint_row(painter, self.width(), self.height(), left, right, self.axis.start, self.axis.end, palette)
        if self.hasFocus():
            painter.setPen(QColor(palette["highlight"]))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(0, 0, int(left) - SPACE_S, self.height() - 1)
        if self.has_time() and self.axis.cursor is not None:
            paint_cursor(painter, self.axis.x_of(self.axis.cursor, self), self.height(), left, right, palette)
        if self._drag is not None:
            shade = QColor(palette["highlight"])
            shade.setAlpha(60)
            painter.fillRect(QRectF(self._press, 0, self._drag - self._press, self.height()), shade)

    def mousePressEvent(self, event):
        left, right = self.column()
        if event.button() == Qt.LeftButton and self.has_time() and left <= event.x() <= right:
            self._press = event.x()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press is not None and abs(event.x() - self._press) >= QApplication.startDragDistance():
            left, right = self.column()
            self._drag = max(left, min(right, event.x()))
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        press, drag = self._press, self._drag
        self._press = self._drag = None
        if drag is not None:
            self.axis.set_window(self.axis.hours_at(press, self), self.axis.hours_at(drag, self))
            self.update()
        elif press is not None:
            self.axis.set_cursor(self.axis.hours_at(press, self))
        super().mouseReleaseEvent(event)

    def cancel_drag(self):
        """Forget a drag under way; True if there was one."""
        dragging = self._drag is not None
        self._press = self._drag = None
        self.update()
        return dragging


def paint_cursor(painter, x, height, left, right, palette):
    if left <= x <= right:
        painter.setPen(QPen(QColor(palette["text_strong"]), 1))
        painter.drawLine(QPointF(x, 0), QPointF(x, height))


class SpectrumLane(TimeRow):
    """One source on the stack, or the one line that says why it is not."""

    def __init__(self, source, tab, parent=None):
        super().__init__(tab.day_axis, parent)
        self.source = source
        self.tab = tab
        self.state = IDLE
        self.message = NOT_FETCHED
        self.image = self.sheet = self.registration = None
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.CrossCursor)
        self.setToolTip(LANE_TIP)
        self.activity = ActivityLabel(parent=self)
        self.activity.hide()
        self.action = link_button("", self._act, self)
        self.action.hide()

    def has_time(self):
        return self.state == READY and self.registration is not None

    def full(self):
        return self.has_time() and self.registration.shows(self.axis.start, self.axis.end)

    def set_state(self, state, message="", tooltip="", action=""):
        self.state, self.message = state, message
        self.activity.setVisible(state == WAITING)
        if state == WAITING:
            self.activity.start(message)
        else:
            self.activity.stop()
        self.action.setText(action)
        self.action.setVisible(bool(action))
        self.setToolTip(tooltip or LANE_TIP)
        self.place_children()
        self.update()

    def set_image(self, image, registration):
        self.image, self.registration = image, registration
        self.sheet = sheet_image(self.source.key, image, registration)
        self.set_state(READY, "" if registration else NOT_ALIGNED, action="" if registration else "Open")

    def clear(self):
        self.image = self.sheet = self.registration = None
        self.set_state(IDLE, NOT_FETCHED)

    def _act(self):
        if self.state == UNREACHABLE:
            self.tab.fetch_dh_spectra(self.tab._spectra_date, sources=[self.source.key])
        else:
            self.tab.open_spectrum(self.source.key)

    def place_children(self):
        left, _right = self.column()
        self.activity.adjustSize()
        self.activity.move(int(left) + SPACE_S, (self.height() - self.activity.height()) // 2)
        self.action.adjustSize()
        width = QFontMetrics(_font(FONT_SMALL)).horizontalAdvance(self.message)
        self.action.move(int(left) + SPACE_S * 2 + width, (self.height() - self.action.height()) // 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.place_children()

    def mouseDoubleClickEvent(self, event):
        if self.image is not None:
            self.tab.open_spectrum(self.source.key)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.image is not None:
            self.tab.open_spectrum(self.source.key)
        elif not self.axis.arrow_key(event):
            super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        if self.image is not None:
            self.tab.spectrum_menu(self.source.key, event.globalPos())

    def paint_row(self, painter, width, height, left, right, start, end, palette):
        top = GROUND_RULE if self.rule_above else 0
        if self.rule_above:
            painter.setPen(QColor(palette["plot_grid"]))
            painter.drawLine(0, GROUND_RULE // 2, int(right), GROUND_RULE // 2)
        body = QRectF(left, top, right - left, height - top - LANE_GAP)
        self.paint_gutter(painter, QRectF(0, top, left - SPACE_S, body.height()), body.height() > THIN_LANE, palette)
        painter.setFont(_font(FONT_SMALL))
        if self.has_time():
            self.paint_time(painter, body, start, end, palette)
        elif self.state != WAITING:
            tone = "warning" if self.state == UNREACHABLE else "text_secondary"
            painter.setPen(QColor(palette[tone]))
            painter.drawText(body.adjusted(SPACE_S, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, self.message)

    def paint_gutter(self, painter, rect, tall, palette):
        source = self.source
        painter.setFont(_font(FONT_SMALL, bold=True))
        painter.setPen(QColor(palette["text"]))
        painter.drawText(rect, (Qt.AlignTop if tall else Qt.AlignVCenter) | Qt.AlignLeft, source.name)
        if not tall:
            return
        painter.setFont(_font(FONT_META))
        painter.setPen(QColor(palette["text_secondary"]))
        painter.drawText(rect.adjusted(0, GUTTER_LINE, 0, 0), Qt.AlignTop | Qt.AlignLeft, source.scale)
        painter.setFont(_font(FONT_META, mono=True))
        painter.drawText(rect, Qt.AlignTop | Qt.AlignRight, source.top)
        painter.drawText(rect, Qt.AlignBottom | Qt.AlignRight, source.bottom)

    def paint_time(self, painter, body, start, end, palette):
        hatch = QColor(palette["plot_grid"])
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(hatch, Qt.BDiagPattern))
        painter.drawRect(body)
        scale = body.width() / (end - start)
        x = lambda hours: body.left() + (hours - start) * scale
        painter.save()
        painter.setClipRect(body)
        flip = self.source.key in FLIPPED
        for left, right, top, bottom, first, last in self.registration.slices:
            a, b = max(first, start), min(last, end)
            if b <= a:
                continue
            per_hour = (right - left) / (last - first)
            part = QRectF(left + (a - first) * per_hour, top, (b - a) * per_hour, bottom - top)
            target = QRectF(x(a), body.top(), x(b) - x(a), body.height())
            if flip:
                painter.save()
                painter.translate(0, 2 * body.top() + body.height())
                painter.scale(1, -1)
                painter.drawImage(target, self.image, part)
                painter.restore()
            else:
                painter.drawImage(target, self.image, part)
        painter.restore()
        painter.setBrush(Qt.NoBrush)
        painter.setPen(hatch)
        painter.drawRect(body)
        painter.setFont(_font(FONT_META, mono=True))
        painter.setPen(QColor(palette["text_secondary"]))
        for a, b in self.registration.gaps():
            shown_a, shown_b = max(a, start), min(b, end)
            text = f"No data {hhmm(a)}–{hhmm(b)}"
            if x(shown_b) - x(shown_a) > painter.fontMetrics().horizontalAdvance(text) + SPACE_M * 2:
                _chip(painter, (x(shown_a) + x(shown_b)) / 2, body.center().y(), text, palette)


class CallistoLane(TimeRow):
    """The day's reported e-CALLISTO bursts: marks, as the app holds no
    spectra of them."""

    def __init__(self, tab, parent=None):
        super().__init__(tab.day_axis, parent)
        self.bursts = []  # (start, end, label, stations)
        self.setFixedHeight(THIN_LANE)
        self.setCursor(Qt.CrossCursor)
        # In the tab's footer: on a busy day the marks fill the lane
        self.link = link_button("Get e-CALLISTO FITS…", tab.open_radio_downloader)
        self.link.setToolTip("Download e-CALLISTO spectra for this time")
        self.link.hide()

    def set_bursts(self, bursts):
        self.bursts = []
        for burst in bursts:
            try:
                begin, end = (int(t[:2]) + int(t[3:5]) / 60 for t in burst.time_range.split("-"))
            except ValueError:
                logger.info(f"e-CALLISTO burst without a time range: {burst}")
                continue
            stations = [s.strip() for s in burst.stations.split(",") if s.strip()]
            self.bursts.append((begin, max(end, begin), f"{burst.burst_type} · {len(stations)} stations", ", ".join(stations)))
        self.update()

    def event(self, event):
        if event.type() == QEvent.ToolTip:
            near = [b for b in self.bursts if abs(self.axis.x_of(b[0], self) - event.x()) < 80]
            QToolTip.showText(event.globalPos(), near[0][3] if near else "", self)
            return True
        return super().event(event)

    def paint_row(self, painter, width, height, left, right, start, end, palette):
        body = QRectF(left, 0, right - left, height - LANE_GAP)
        painter.setFont(_font(FONT_SMALL, bold=True))
        painter.setPen(QColor(palette["text"]))
        painter.drawText(QRectF(0, 0, left, body.height()), Qt.AlignVCenter | Qt.AlignLeft, "e-CALLISTO")
        painter.setFont(_font(FONT_SMALL))
        if not self.bursts:
            painter.setPen(QColor(palette["text_secondary"]))
            painter.drawText(body.adjusted(SPACE_S, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, "No bursts listed")
            return
        scale = body.width() / (end - start)
        painter.save()
        painter.setClipRect(body)
        shown = sorted(b for b in self.bursts if b[1] >= start and b[0] <= end)
        for index, (first, last, label, _stations) in enumerate(shown):
            x = body.left() + (first - start) * scale
            mark = QRectF(x, SPACE_XS, max(3, (last - first) * scale), body.height() - SPACE_S)
            painter.fillRect(mark, QColor(palette["link"]))
            # A label only where the next mark leaves it room
            room = body.left() + (shown[index + 1][0] - start) * scale if index + 1 < len(shown) else body.right()
            if mark.right() + SPACE_XS + painter.fontMetrics().horizontalAdvance(label) + SPACE_XS <= room:
                painter.setPen(QColor(palette["text"]))
                painter.drawText(QRectF(mark.right() + SPACE_XS, 0, 200, body.height()), Qt.AlignVCenter, label)
        painter.restore()


class SheetWell(QFrame):
    """An image fitted to the black well: nothing depends on a guess of the
    width, as the scaling is done here at the size there is."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ImageWell")
        self.image = self._scaled = None

    def set_image(self, image):
        self.image, self._scaled = image, None
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.image is None:
            return
        size = self.image.size().scaled(self.size(), Qt.KeepAspectRatio)
        if size.isEmpty():
            return
        if self._scaled is None or self._scaled.size() != size:
            self._scaled = self.image.scaled(size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        QPainter(self).drawImage((self.width() - size.width()) // 2, (self.height() - size.height()) // 2, self._scaled)


class SpectrumTile(QWidget):
    """One archive image of the contact sheet, with its name and band."""

    def __init__(self, source, tab, parent=None):
        super().__init__(parent)
        self.source = source
        self.tab = tab
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to open large, right-click for more")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        caption = QHBoxLayout()
        caption.setSpacing(SPACE_S)
        name = QLabel(source.name)
        name.setFont(_font(FONT_SMALL, bold=True))
        name.setFixedHeight(CAPTION_HEIGHT)  # the sheet counts on this for the well's aspect
        caption.addWidget(name)
        caption.addStretch()
        self.band = QLabel()
        self.band.setObjectName("Meta")
        caption.addWidget(self.band)
        layout.addLayout(caption)
        self.well = SheetWell(self)
        layout.addWidget(self.well, 1)
        self.activity = ActivityLabel(parent=self.well)
        self.activity.hide()

    def aspect(self):
        image = self.well.image
        if image is not None and not image.isNull():
            return image.width() / image.height()
        return 9.0 if self.source.span == 3 else self.source.span * 2.0

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.activity.adjustSize()
        self.activity.move(SPACE_S, SPACE_S)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.well.image is not None:
            self.tab.open_spectrum(self.source.key)

    def contextMenuEvent(self, event):
        if self.well.image is not None:
            self.tab.spectrum_menu(self.source.key, event.globalPos())


class ContactSheet(QWidget):
    """The archive's images, in three columns on two kickers. Everything scales
    together until it fits, so the columns stay aligned with no scroll bar."""

    def __init__(self, tiles, parent=None):
        super().__init__(parent)
        self.tiles = tiles
        for tile in tiles.values():
            tile.setParent(self)
        self.kickers = {False: kicker("Ground", self), True: kicker("Spacecraft", self)}

    def _rows(self):
        """Per group, what is shown in rows of three columns."""
        groups = {False: [], True: []}
        for key in ORIGINALS_ORDER:
            tile = self.tiles[key]
            if tile.isVisibleTo(self):
                row = groups[tile.source.space]
                if not row or sum(t.source.span for t in row[-1]) + tile.source.span > 3:
                    row.append([])
                row[-1].append(tile)
        return groups

    @staticmethod
    def _height(row, width):
        """Of the images in ``row``. A 2:1 column sets it unless the row is
        one wide image, which has an aspect of its own."""
        if row[0].source.span == 3:
            return width / row[0].aspect()
        return (width - 2 * SHEET_GAP) / 6

    def arrange(self):
        groups = self._rows()
        rows = sum(len(g) for g in groups.values())
        shown = sum(1 for g in groups.values() if g)
        fixed = shown * (self.kickers[False].sizeHint().height() + SPACE_XS) + rows * CAPTION_HEIGHT
        fixed += (rows + shown) * SHEET_GAP

        def heights(width):
            return sum(self._height(row, width) for g in groups.values() for row in g)

        width = self.width()
        if rows and heights(width) > self.height() - fixed:
            width = (self.height() - fixed) / (heights(width) / width)
        width = max(int(width), 120)
        x0, y = (self.width() - width) // 2, 0
        column = (width - 2 * SHEET_GAP) / 3
        for space, group in groups.items():
            label = self.kickers[space]
            label.setVisible(bool(group))
            if not group:
                continue
            label.setGeometry(x0, y, width, label.sizeHint().height())
            y += label.height() + SPACE_XS
            for row in group:
                height = round(self._height(row, width)) + CAPTION_HEIGHT
                x = x0
                for tile in row:
                    span = tile.source.span
                    tile_width = round(span * column + (span - 1) * SHEET_GAP)
                    tile.setGeometry(round(x), y, tile_width, height)
                    x += tile_width + SHEET_GAP
                y += height + SHEET_GAP
            y += SHEET_GAP

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.arrange()


class SpectrumViewer(QDialog):
    """The large view, one for the whole window: the archive's image, untouched,
    with the cursor drawn through its registration, which is also the check by
    eye that the alignment is right."""

    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab
        self.key = None
        self.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        fit_to_screen(self, 1280, 800)
        layout = dialog_layout(self)
        self.view = ImageZoomView(self)
        layout.addWidget(self.view, 1)

        self.previous_btn = self._icon_button("Previous source (Left)", lambda: self.step(-1))
        self.next_btn = self._icon_button("Next source (Right)", lambda: self.step(1))
        themed(self, self._style_icons)
        extras = [self.previous_btn, self.next_btn]
        fit_btn = QPushButton("Fit")
        fit_btn.setToolTip("See the whole image (F)")
        fit_btn.clicked.connect(self.view.fit)
        actual_btn = QPushButton("100%")
        actual_btn.setToolTip("One image pixel to one screen pixel (1)")
        actual_btn.clicked.connect(self.view.actual_size)
        save_btn = QPushButton("Save…")
        save_btn.clicked.connect(lambda: self.key and self.tab.save_spectra_image(self.key))
        bar = QHBoxLayout()
        bar.setSpacing(SPACE_S)
        for widget in extras + [fit_btn, actual_btn, save_btn]:
            bar.addWidget(widget)
        bar.addStretch()
        buttons, _ = button_bar(secondary=("Close", self.reject))
        bar.addWidget(buttons)
        layout.addLayout(bar)

        for key, action in (
            (Qt.Key_Left, lambda: self.step(-1)),
            (Qt.Key_Right, lambda: self.step(1)),
            (Qt.Key_1, self.view.actual_size),
            (Qt.Key_F, self.view.fit),
        ):
            QShortcut(QKeySequence(key), self, activated=action)
        tab.day_axis.cursor_changed.connect(self.show_marker)

    def _icon_button(self, tooltip, slot):
        button = QToolButton()
        button.setToolTip(tooltip)
        button.clicked.connect(slot)
        set_hand_cursor(button)
        return button

    def _style_icons(self, palette):
        self.previous_btn.setIcon(QIcon(themed_icon("fd_back.svg")))
        self.next_btn.setIcon(QIcon(themed_icon("fd_forward.svg")))

    def show_source(self, key):
        lane = self.tab.spectrum_lanes[key]
        self.key = key
        date = self.tab._spectra_date
        self.setWindowTitle(f"{lane.source.name} · {date:%Y-%m-%d}" if date else lane.source.name)
        self.view.set_image(lane.image)
        self.show_marker()

    def show_marker(self):
        if self.key is None:
            return
        registration = self.tab.spectrum_lanes[self.key].registration
        hours = self.tab.day_axis.cursor
        self.view.set_marker(None if registration is None or hours is None else registration.image_x(hours))

    def step(self, by):
        """To the next image in frequency order, round and round."""
        keys = [s.key for s in SOURCES if self.tab.spectrum_lanes[s.key].image is not None]
        if self.key in keys and len(keys) > 1:
            self.show_source(keys[(keys.index(self.key) + by) % len(keys)])


class RadioSpectraTabMixin:
    """Builds the Radio spectra tab and fills it as spectra arrive.

    Mixed into :class:`NOAAEventsViewer`; relies on its attributes via ``self``:
    ``begin_job``, ``day_axis``, ``dh_worker``, ``end_job``, ``say``,
    ``selected_date``, ``tabs``, ``update_job``.
    """

    def _create_radio_spectra_tab(self):
        self._spectra_paths = {}
        self._spectrum_viewer = None
        self._lanes_coming = set()  # the sources of a date that is decoding, which keep the lanes' heights
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, SPACE_M, 0, 0)
        layout.setSpacing(SPACE_S)

        top = QHBoxLayout()
        top.setSpacing(SPACE_S)
        self.spectra_status_label = QLabel()
        self.spectra_status_label.setObjectName("Hint")
        self.spectra_status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)  # never widens the window
        top.addWidget(self.spectra_status_label, 1)
        self.aligned_btn = QPushButton("Aligned")
        self.originals_btn = QPushButton("Originals")
        self.aligned_btn.setToolTip("On the window's time column, with the axes read from each image")
        self.originals_btn.setToolTip("The archive's own images, axes and all")
        views = QButtonGroup(tab)
        for button in (self.aligned_btn, self.originals_btn):
            button.setCheckable(True)
            views.addButton(button)
            button.clicked.connect(self._show_spectra_page)
        self.aligned_btn.setChecked(True)
        top.addWidget(segmented([self.aligned_btn, self.originals_btn]))
        self.save_stack_btn = QPushButton("Save stack…")
        self.save_stack_btn.setToolTip("Save the stack as shown as a PNG for a paper or slide")
        self.save_stack_btn.clicked.connect(self.save_spectra_stack)
        top.addWidget(self.save_stack_btn)
        self.save_all_btn = QPushButton("Save all…")
        self.save_all_btn.setToolTip("Save every image as the archive sent it, into one folder")
        self.save_all_btn.clicked.connect(self.save_all_spectra)
        top.addWidget(self.save_all_btn)
        layout.addLayout(top)

        self.spectra_pages = QStackedWidget()
        self.spectra_empty = empty_note(SPECTRA_EMPTY)
        self.spectra_pages.addWidget(self.spectra_empty)

        self.spectra_stack = QWidget()
        self.spectra_stack_layout = QVBoxLayout(self.spectra_stack)
        self.spectra_stack_layout.setContentsMargins(0, 0, 0, 0)
        self.spectra_stack_layout.setSpacing(0)
        self.spectrum_lanes = {}
        for source in SOURCES:
            lane = SpectrumLane(source, self)
            self.spectrum_lanes[source.key] = lane
            self.spectra_stack_layout.addWidget(lane)
        self.callisto_lane = CallistoLane(self)
        self.callisto_lane.hide()
        self.spectra_stack_layout.addWidget(self.callisto_lane)
        self.spectra_stack_layout.addStretch(0)
        self.spectra_pages.addWidget(self.spectra_stack)

        self.spectrum_tiles = {source.key: SpectrumTile(source, self) for source in SOURCES}
        self.contact_sheet = ContactSheet(self.spectrum_tiles)
        self.spectra_pages.addWidget(self.contact_sheet)
        layout.addWidget(self.spectra_pages, 1)

        footer = QHBoxLayout()
        footer.setSpacing(SPACE_M)
        self.spectra_note = QLabel()
        self.spectra_note.setObjectName("Hint")
        footer.addWidget(self.spectra_note)
        footer.addWidget(self.callisto_lane.link)
        credits_label = source_note(
            'Data sources: <a href="https://cdaw.gsfc.nasa.gov/">CDAW NASA</a>, '
            '<a href="https://solar.nro.nao.ac.jp/norp/">NoRP</a>, '
            '<a href="https://rsdb.obs-nancay.fr/">Nançay</a>, '
            '<a href="https://solobs.nict.go.jp/radio/cgi-bin/MainDisplay.pl">NICT</a>'
        )
        credits_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        footer.addWidget(credits_label, 1)
        layout.addLayout(footer)

        self.day_axis.window_changed.connect(self._on_spectra_window)
        self.day_axis.cursor_changed.connect(self._on_spectra_cursor)
        self.day_axis.column_changed.connect(self._on_spectra_column)
        self.tabs.addTab(tab, "Radio spectra")
        self._arrange_spectra()

    # ------------------------------------------------------------------ states

    def _rows(self):
        return [*self.spectrum_lanes.values(), self.callisto_lane]

    def _on_spectra_window(self):
        self._arrange_spectra()
        for row in self._rows():
            row.update()

    def _on_spectra_cursor(self):
        self._say_spectra_status()
        for row in self._rows():
            row.update()

    def _on_spectra_column(self):
        for lane in self.spectrum_lanes.values():
            lane.place_children()
        for row in self._rows():
            row.update()

    def _spectra_keys(self):
        """The sources that can exist for the date on show."""
        day = self._spectra_date or self.day_axis.day
        return expected_sources(day) if day else [s.key for s in SOURCES if s.key not in ("hiras", "stereo_b")]

    def _arrange_spectra(self):
        """Who is on the stack and how tall: a lane with nothing in the window
        gives up its height."""
        keys = self._spectra_keys()
        first_space = True
        least = 0  # the height all of them take with an image: a zoom leaves the window's least height alone
        for source in SOURCES:
            lane = self.spectrum_lanes[source.key]
            shown = source.key in keys
            lane.setVisible(shown)
            lane.rule_above = shown and source.space and first_space
            first_space = first_space and not (shown and source.space)
            if shown:
                least += LEAST_LANE + (GROUND_RULE if lane.rule_above else 0)
            if self._lanes_coming:
                pass  # they change once, when all of them are in
            elif lane.full():
                lane.setMinimumHeight(LEAST_LANE + (GROUND_RULE if lane.rule_above else 0))
                lane.setMaximumHeight(16777215)
                self.spectra_stack_layout.setStretchFactor(lane, 1)
            else:
                lane.setFixedHeight(THIN_LANE + (GROUND_RULE if lane.rule_above else 0))
                self.spectra_stack_layout.setStretchFactor(lane, 0)
            tile = self.spectrum_tiles[source.key]
            tile.band.setText(self._tile_band(lane))
            tile.setVisible(shown and (lane.image is not None or lane.state == WAITING))
            tile.well.set_image(lane.sheet)
            tile.activity.setVisible(lane.state == WAITING)
            if lane.state == WAITING:
                tile.activity.start(lane.message)
                tile.activity.adjustSize()
        self.spectra_stack.setMinimumHeight(least)
        ended = "STEREO began in 2006" if "stereo_a" not in keys else "STEREO-B ended in 2014" if "stereo_b" not in keys else ""
        self.spectra_note.setText(ended)
        self.contact_sheet.arrange()
        self.save_all_btn.setEnabled(bool(self._spectra_paths))
        self._show_spectra_page()

    def _tile_band(self, lane):
        source = lane.source
        band = source.scale if source.key == "norp" else f"{source.bottom}–{source.top}" if source.top else ""
        if source.key == "yamagawa" and lane.registration is not None:
            band += f" · {kept_hours(lane.registration)}"
        return band

    def _show_spectra_page(self):
        lanes = self.spectrum_lanes.values()
        busy = any(lane.state != IDLE for lane in lanes)
        if not busy:
            page = self.spectra_empty
        else:
            page = self.contact_sheet if self.originals_btn.isChecked() else self.spectra_stack
        self.spectra_pages.setCurrentWidget(page)
        self.save_stack_btn.setEnabled(
            page is self.spectra_stack and any(lane.has_time() for lane in lanes)
        )
        self._say_spectra_status()

    def _say_spectra_status(self):
        if self.originals_btn.isChecked():
            lanes = [self.spectrum_lanes[key] for key in self._spectra_keys()]
            have = [lane for lane in lanes if lane.image is not None]
            text = f"{len(have)} of {len(lanes)} sources, as the archives drew them"
            missing = [lane.source.name for lane in lanes if lane.state in (NO_FILE, UNREACHABLE)]
            if missing:
                text += f" · nothing from {', '.join(missing)}"
        else:
            axis = self.day_axis
            parts = [f"{hhmm(axis.start)}–{hhmm(axis.end)} UTC"]
            if axis.cursor is not None:
                parts.append(f"cursor {axis.cursor_text()}")
            parts.append("high frequency at the top of each lane")
            text = " · ".join(parts)
        self.spectra_status_label.setText(text)

    def clear_dh_spectra_ui(self, keep_viewer=False):
        """Back to no spectra at all."""
        if not keep_viewer:
            self._lanes_coming = set()
        self._spectra_paths = {}
        self.spectra_empty.setText(SPECTRA_EMPTY)
        for lane in self.spectrum_lanes.values():
            lane.clear()
        self.spectra_status_label.setToolTip("")
        if self._spectrum_viewer is not None and not keep_viewer:
            self._spectrum_viewer.reject()
        self._arrange_spectra()

    def display_ecallisto_bursts(self, bursts):
        self.callisto_lane.set_bursts(bursts)
        self.callisto_lane.show()
        self.callisto_lane.link.show()

    # ---------------------------------------------------------------- fetching

    def fetch_dh_spectra(self, day=None, only_cached=False, sources=None):
        """The spectra of ``day`` (the selected date if ``None``). With
        ``only_cached`` no request is made. ``sources`` limits it to those, for
        Try again."""
        if day is None:
            day = self.selected_date()
            if day is None:
                return
        if only_cached:
            self._show_cached_spectra(day)
            return
        if self.dh_worker and self.dh_worker.isRunning():
            return

        self._spectra_date = day
        self._lanes_coming = set()
        keys = [key for key in expected_sources(day) if sources is None or key in sources]
        if sources is None:
            self._spectra_paths = {}
            for lane in self.spectrum_lanes.values():
                lane.clear()
        for key in keys:
            self._spectra_paths.pop(key, None)
            self.spectrum_lanes[key].clear()
            self.spectrum_lanes[key].set_state(WAITING, "Waiting…")
        self._arrange_spectra()

        self.dh_worker = DHSpectraWorker(datetime(day.year, day.month, day.day, 12, 0, 0), sources=sources)
        self.dh_worker.progress.connect(self.on_dh_spectra_progress)
        self.dh_worker.result_ready.connect(self.on_dh_partial_ready)
        self.dh_worker.source_failed.connect(self.on_dh_source_failed)
        self.dh_worker.source_ended.connect(self.on_dh_source_ended)
        self.dh_worker.sources_done.connect(self.on_dh_sources_done)
        self.dh_worker.done.connect(self.on_dh_spectra_ready)
        self.begin_job("spectra", "Fetching spectra…", self.cancel_dh_spectra)
        self.dh_worker.start()

    def _show_cached_spectra(self, day):
        """What the cache holds for ``day``, and no request. The large view
        stays open and goes over to the date when it is in."""
        paths = {key: _noaa_cache.load_spectra(day, key) for key in expected_sources(day)}
        self._lanes_coming = {key for key, path in paths.items() if path}
        self.clear_dh_spectra_ui(keep_viewer=True)
        self._spectra_date = day
        for key in self._lanes_coming:
            self._spectra_paths[key] = str(paths[key])
            self.spectrum_lanes[key].set_state(WAITING, "Loading…")
            self._decode_spectrum(key, str(paths[key]))
        self._arrange_spectra()
        if not self._lanes_coming:
            self._viewer_to_date()

    def _viewer_to_date(self):
        """The large view shows its source on the date now in, or says why it closes."""
        viewer = self._spectrum_viewer
        if viewer is None or not viewer.isVisible():
            return
        lane = self.spectrum_lanes[viewer.key]
        if lane.image is not None:
            viewer.show_source(viewer.key)
        else:
            viewer.reject()
            self.say(f"The large view closed: no {lane.source.name} spectrum here for {self._spectra_date}")

    def _decode_spectrum(self, source, path):
        decoder = SpectrumDecoder(source, path, self._spectra_date)
        decoder.decoded.connect(self._on_spectrum_decoded)
        decoder.finished.connect(lambda: _decoding.discard(decoder))
        _decoding.add(decoder)
        decoder.start()

    def _on_spectrum_decoded(self, source, path, image, registration):
        if self._spectra_paths.get(source) != path:
            return  # another date's by now
        lane = self.spectrum_lanes[source]
        if image.isNull():
            self._spectra_paths.pop(source)
            lane.set_state(NO_FILE, f"The {lane.source.name} file could not be read")
        else:
            lane.set_image(image, registration)
        last = source in self._lanes_coming and len(self._lanes_coming) == 1
        self._lanes_coming.discard(source)
        self._arrange_spectra()
        if last:
            self.spectra_stack_layout.activate()  # all of it at once, not a size at a time
            self._viewer_to_date()

    def stop_dh_spectra(self):
        """Stop a running fetch, if any, nothing more said about it. True if
        there was one."""
        if not self.dh_worker or not self.dh_worker.isRunning():
            return False
        abandon(self.dh_worker)
        self.dh_worker = None
        self.end_job("spectra")
        for key, lane in self.spectrum_lanes.items():
            if lane.state == WAITING and key not in self._spectra_paths:
                lane.set_state(IDLE, NOT_FETCHED)
        self._arrange_spectra()
        return True

    def cancel_dh_spectra(self):
        """Cancel: what has arrived stays, and what was here already comes back."""
        self.stop_dh_spectra()
        for key in expected_sources(self._spectra_date):
            path = _noaa_cache.load_spectra(self._spectra_date, key)
            if path and key not in self._spectra_paths:
                self._spectra_paths[key] = str(path)
                self.spectrum_lanes[key].set_state(WAITING, "Loading…")
                self._decode_spectrum(key, str(path))
        self._arrange_spectra()

    def on_dh_source_ended(self, source):
        """A source that gave nothing stops waiting now: the last source can be
        minutes away."""
        if self.dh_worker is not None:
            self._say_no_file(source)

    def _say_no_file(self, source):
        lane = self.spectrum_lanes[source]
        if lane.state == WAITING and source not in self._spectra_paths:
            lane.set_state(NO_FILE, f"No file in the {lane.source.archive} archive for this date")
            self._arrange_spectra()

    def on_dh_source_failed(self, source, kind, detail):
        if self.dh_worker is None:
            return
        lane = self.spectrum_lanes[source]
        if kind == "unreachable":
            lane.set_state(UNREACHABLE, f"Could not reach {lane.source.archive}", detail, "Try again")
            self._arrange_spectra()
        else:
            self._say_no_file(source)

    def on_dh_sources_done(self, done, total):
        if self.dh_worker is not None:
            self.update_job("spectra", f"Spectra: {done} of {total} sources")

    def on_dh_spectra_progress(self, message: str):
        if self.dh_worker is None:
            return
        self.update_job("spectra", message)
        hour = re.search(r"(Yamagawa|HiRAS) hour (\d+ of \d+)", message)
        if hour:
            lane = self.spectrum_lanes[hour.group(1).lower()]
            if lane.state == WAITING:
                lane.activity.start(f"Hour {hour.group(2)}")
                lane.activity.adjustSize()

    def on_dh_partial_ready(self, source: str, path: str):
        """Show a source as soon as it is here."""
        if self.dh_worker is None or not path or not os.path.exists(path):
            return
        self._spectra_paths[source] = path
        self._decode_spectrum(source, path)

    def on_dh_spectra_ready(self, results: dict, errors: list):
        if self.dh_worker is None:
            return  # cancelled
        for source in expected_sources(self._spectra_date):
            self._say_no_file(source)
        self.dh_worker = None
        self.end_job("spectra")
        self.spectra_status_label.setToolTip("\n".join(errors))
        self._arrange_spectra()

    # ----------------------------------------------------------------- actions

    def open_spectrum(self, source):
        """The large view of ``source``, which stays open for the next one."""
        if self.spectrum_lanes[source].image is None:
            return
        if self._spectrum_viewer is None:
            self._spectrum_viewer = SpectrumViewer(self)
        self._spectrum_viewer.show_source(source)
        self._spectrum_viewer.show()
        self._spectrum_viewer.raise_()

    def spectrum_menu(self, source, position):
        info = BY_KEY[source]
        lane = self.spectrum_lanes[source]
        menu = QMenu(self)
        menu.addAction("Open large", lambda: self.open_spectrum(source))
        menu.addAction("Save…", lambda: self.save_spectra_image(source))
        menu.addAction("Copy image", lambda: QApplication.clipboard().setImage(lane.image))
        menu.addAction("Open source page", lambda: QDesktopServices.openUrl(QUrl(info.page)))
        menu.exec_(position)

    def open_radio_downloader(self):
        """e-CALLISTO's FITS come through the app's one radio downloader."""
        from ..radio_data_downloader import launch_gui

        day = self._spectra_date or self.selected_date()
        when = self.day_axis.cursor_datetime()
        if when is None and day is not None:
            when = datetime(day.year, day.month, day.day, 12, 0, 0)
        launch_gui(self, initial_datetime=when)

    def _spectrum_name(self, source, suffix):
        date_str = f"{self._spectra_date:%Y%m%d}"
        return f"{BY_KEY[source].save_name}_{date_str}.{suffix}"

    def save_spectra_image(self, source: str):
        """Save one spectrum: the archive's own bytes if the chosen name asks
        for the file's format, converted otherwise."""
        path = self._spectra_paths.get(source)
        if not path or not os.path.exists(path):
            self.say("No image to save")
            return
        real = image_format(path)
        kinds = [real] if real == "png" else [real, "png"]
        filters = ";;".join(f"{kind.upper()} image (*.{kind})" for kind in kinds) + ";;All files (*)"
        target, _ = QFileDialog.getSaveFileName(self, "Save spectrum", self._spectrum_name(source, real), filters)
        if not target:
            return
        suffix = os.path.splitext(target)[1].lstrip(".").lower()
        if not suffix:
            suffix, target = real, f"{target}.{real}"
        try:
            if {"jpeg": "jpg"}.get(suffix, suffix) == real:
                shutil.copyfile(path, target)
            elif not QImage(path).save(target):
                raise OSError(f"Could not write {suffix} to {target}")
        except OSError as e:
            show_error(self, "Could not save the image", f"The image could not be saved to:\n{target}", str(e))
            return
        self.say(f"Saved {os.path.basename(target)}")

    def save_all_spectra(self):
        """Every file there is into one folder, as the archives sent them."""
        folder = QFileDialog.getExistingDirectory(self, "Save all spectra into")
        if not folder:
            return
        names = {
            source: os.path.join(folder, self._spectrum_name(source, image_format(path)))
            for source, path in self._spectra_paths.items()
        }
        taken = [name for name in names.values() if os.path.exists(name)]
        if taken:
            box = QMessageBox(self)
            box.setWindowTitle("Replace files?")
            box.setText(f"{len(taken)} of these files are already in that folder. Replace them?")
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
            box.setDefaultButton(QMessageBox.Cancel)
            if box.exec_() != QMessageBox.Yes:
                return
        try:
            for source, name in names.items():
                shutil.copyfile(self._spectra_paths[source], name)
        except OSError as e:
            show_error(self, "Could not save the images", f"The images could not be saved to:\n{folder}", str(e))
            return
        self.say(f"Saved {len(names)} files to {folder}")

    def save_spectra_stack(self):
        day = self._spectra_date
        axis = self.day_axis
        name = f"Radio_spectra_{day:%Y%m%d}_{hhmm(axis.start).replace(':', '')}-{hhmm(axis.end).replace(':', '')}.png"
        target, _ = QFileDialog.getSaveFileName(self, "Save stack", name, "PNG image (*.png)")
        if not target:
            return
        if not os.path.splitext(target)[1]:
            target += ".png"
        if self.render_spectra_stack(LIGHT_PALETTE).save(target, "PNG"):
            self.say(f"Saved {os.path.basename(target)}")
        else:
            show_error(self, "Could not save the stack", f"The image could not be saved to:\n{target}")

    def render_spectra_stack(self, palette):
        """The stack as now, at twice its size, with what a page needs around
        it: date, time axis, band labels, credits."""
        axis = self.day_axis
        rows = [row for row in self._rows() if row.isVisibleTo(self.spectra_stack)]
        left, right = rows[0].column()
        width = int(self.spectra_stack.width())
        head, foot, margin = 44, 20, SPACE_M
        height = head + rows[-1].geometry().bottom() + 1 + foot
        image = QImage((width + 2 * margin) * 2, (height + 2 * margin) * 2, QImage.Format_RGB32)
        image.fill(QColor(palette["window"]))
        painter = QPainter(image)
        painter.setRenderHints(QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        painter.scale(2, 2)
        painter.translate(margin, margin)
        painter.setPen(QColor(palette["text"]))
        painter.setFont(_font(FONT_SMALL, bold=True))
        day = self._spectra_date
        title = f"{day:%Y-%m-%d} · {hhmm(axis.start)}–{hhmm(axis.end)} UTC · high frequency at the top of each lane"
        painter.drawText(QRectF(0, 0, width, 20), Qt.AlignVCenter | Qt.AlignLeft, title)
        self._paint_ruler(painter, left, right, 20, palette)
        for row in rows:
            painter.save()
            painter.translate(0, head + row.y())
            row.paint_row(painter, width, row.height(), left, right, axis.start, axis.end, palette)
            if row.has_time() and axis.cursor is not None:
                x = left + (axis.cursor - axis.start) / (axis.end - axis.start) * (right - left)
                paint_cursor(painter, x, row.height(), left, right, palette)
            painter.restore()
        painter.setPen(QColor(palette["text_secondary"]))
        painter.setFont(_font(FONT_META))
        painter.drawText(
            QRectF(0, height - foot, width, foot), Qt.AlignBottom | Qt.AlignLeft,
            "Data: CDAW NASA, NoRP, Nançay, NICT, e-CALLISTO",
        )
        painter.end()
        return image

    def _paint_ruler(self, painter, left, right, top, palette):
        axis = self.day_axis
        span = axis.end - axis.start
        per_hour = (right - left) / span
        step = next((s for s in (1 / 6, 0.25, 0.5, 1, 2, 3, 4, 6) if s * per_hour >= 64), 6)
        painter.setFont(_font(FONT_META, mono=True))
        painter.setPen(QColor(palette["text_secondary"]))
        tick = np.ceil(axis.start / step) * step
        while tick <= axis.end + 1e-6:
            x = left + (tick - axis.start) * per_hour
            painter.drawText(QRectF(min(x, right - 30) - 30, top, 60, 16), Qt.AlignCenter, hhmm(tick))
            painter.drawLine(int(x), top + 16, int(x), top + 22)
            tick += step

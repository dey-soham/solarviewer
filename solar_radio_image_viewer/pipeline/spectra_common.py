"""
Small pieces shared by the dynamic spectrum viewer: collapsible sections,
the background reader of a server's file, rebinning and noise statistics,
and drift rates (with the Newkirk density model).
"""

import logging
import numpy as np
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QToolButton,
    QFrame,
)
from PyQt5.QtCore import Qt, pyqtSignal, QThread

logger = logging.getLogger(__name__)


class _Section(QFrame):
    """A titled section of the side panel: click its title to fold it (the
    state is remembered)."""

    def __init__(self, title, key, palette, parent=None):
        super().__init__(parent)
        from PyQt5.QtCore import QSettings

        self._settings = QSettings("SolarViewer", "DynamicSpectrumViewer")
        self._key = f"panel/{key}"
        self._title = title
        self.setObjectName("Section")
        self.setStyleSheet(self.style(palette))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 14)
        layout.setSpacing(12)
        self.title = QToolButton()
        self.title.setObjectName("SectionTitle")
        self.title.setCursor(Qt.PointingHandCursor)
        self.title.setCheckable(True)
        self.title.setToolTip("Click to fold or unfold")
        layout.addWidget(self.title)
        self.body = QWidget()
        layout.addWidget(self.body)
        self.title.toggled.connect(self._fold)
        opened = self._settings.value(self._key, True, type=bool)
        self.title.setChecked(opened)
        self._fold(opened)

    @staticmethod
    def style(palette):
        """The style sheet for a section in the theme of ``palette``."""
        border = palette.get("border", "#3a3a5a")
        return f"""
            QFrame#Section {{
                background-color: {palette["surface"]};
                border: 1px solid {border};
                border-radius: 10px;
            }}
            QToolButton#SectionTitle {{
                border: none;
                background: transparent;
                color: {palette["highlight"]};
                font-weight: 600;
                padding: 0px;
                text-align: left;
            }}
        """

    def _fold(self, opened):
        self.title.setText(("▾  " if opened else "▸  ") + self._title)
        self.body.setVisible(opened)
        self._settings.setValue(self._key, opened)

    def unfold(self):
        self.title.setChecked(True)


class _RemoteRead(QThread):
    """Reads ``work()`` from the server in the background."""

    done = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(object, object)  # (done bytes, total bytes)

    def __init__(self, work, parent=None):
        super().__init__(parent)
        self._work = work
        self.stopped = False

    def run(self):
        from ..remote.remote_fits import reporting

        try:
            with reporting(self.progress.emit, lambda: self.stopped):
                result = self._work()
        except Exception as e:
            if not self.stopped:
                self.failed.emit(str(e))
            return
        if not self.stopped:
            self.done.emit(result)


def rebin(data, factor, axis):
    """Means of blocks of ``factor`` samples along ``axis`` (NaN ignored; the
    last block may be shorter); 1-D axes and 2-D data alike."""
    if factor <= 1 or data.shape[axis] <= 1:
        return data
    starts = np.arange(0, data.shape[axis], factor)
    finite = np.isfinite(data)
    sums = np.add.reduceat(np.where(finite, data, 0), starts, axis=axis, dtype=np.float64)
    counts = np.add.reduceat(finite, starts, axis=axis, dtype=np.int64)
    with np.errstate(invalid="ignore", divide="ignore"):
        means = sums / counts
    return means.astype(data.dtype if data.dtype.kind == "f" else np.float64)


def _local_block_stats(image, time_dim, skip):
    """(mean, max) of each block of ``skip`` time samples of a local image
    (every sample read, in blocks; progress and stopping as set by
    remote_fits.reporting)."""
    from ..remote.remote_fits import Cancelled, block_stats, current_reporting

    progress, cancelled = current_reporting()
    nt = image.shape[0] if time_dim == 0 else image.shape[1]
    nf = image.shape[1] if time_dim == 0 else image.shape[0]
    nb = -(-nt // skip)
    mean = np.empty((nb, nf), np.float32)
    peak = np.empty((nb, nf), np.float32)
    per = max(1, (16_000_000 // nf) // skip)  # blocks per read
    for b0 in range(0, nb, per):
        if cancelled and cancelled():
            raise Cancelled()
        b1 = min(b0 + per, nb)
        s, e = b0 * skip, min(b1 * skip, nt)
        raw = image[s:e, :] if time_dim == 0 else image[:, s:e].T
        x = np.array(raw, dtype=np.float32)
        x[np.isinf(x)] = np.nan
        mean[b0:b1], peak[b0:b1] = block_stats(x, skip)
        if progress:
            progress(e * nf * 4, nt * nf * 4)
    return mean, peak


def _channel_medians(data):
    """Median over time (axis 0) of each channel; NaN for channels without
    values (computed only where there are some: no warnings, which can't be
    silenced safely from the background thread saving a file)."""
    medians = np.full(data.shape[1], np.nan, dtype=np.float64)
    has_values = np.isfinite(data).any(axis=0)
    if has_values.any():
        medians[has_values] = np.nanmedian(data[:, has_values], axis=0)
    return medians


R_SUN_KM = 695700.0


def _newkirk_height(f_mhz, harmonic=1):
    """Heliocentric distance (solar radii) where the plasma frequency is
    ``f_mhz / harmonic`` in the Newkirk (1961) density model
    n = 4.2e4 * 10**(4.32 / r) cm^-3; None outside the model (r <= 1)."""
    fp = f_mhz / harmonic
    n = (fp / 8.98e-3) ** 2  # cm^-3 (f_p = 8.98 kHz * sqrt(n))
    if n <= 4.2e4:
        return None
    r = 4.32 / np.log10(n / 4.2e4)
    return r if r > 1.0 else None


def drift_rate(t1, f1, t2, f2):
    """A burst's drift between two points (seconds, MHz): dict with ``dt``
    (s), ``df`` (MHz), ``rate`` (MHz/s) and the radial speeds ``v_f`` and
    ``v_h`` (km/s; fundamental or harmonic emission, Newkirk model; None
    where the model doesn't reach)."""
    if t2 < t1:
        t1, f1, t2, f2 = t2, f2, t1, f1
    dt, df = t2 - t1, f2 - f1
    result = {"dt": dt, "df": df, "rate": df / dt if dt else float("nan")}
    for key, harmonic in (("v_f", 1), ("v_h", 2)):
        r1, r2 = _newkirk_height(f1, harmonic), _newkirk_height(f2, harmonic)
        result[key] = (r2 - r1) * R_SUN_KM / dt if dt and r1 and r2 else None
    return result


def _size_text(nbytes):
    for unit in ("bytes", "KB", "MB", "GB"):
        if nbytes < 1024 or unit == "GB":
            return f"{nbytes:.0f} {unit}" if unit == "bytes" else f"{nbytes:.1f} {unit}"
        nbytes /= 1024

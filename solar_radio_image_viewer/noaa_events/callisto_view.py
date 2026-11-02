"""
A station's own e-CALLISTO spectrum of a reported burst, for the Radio spectra
tab. The burst list names stations; each keeps 15-minute FITS files, so a look
at one burst is one or two small files, fetched on asking and kept in the
day's cache.
"""

import logging
import os
import re
from datetime import datetime, timedelta

import numpy as np
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QImage
from PyQt5.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QFileDialog, QPushButton

from ..ui.download_parts import abandon
from ..ui.errors import show_error
from ..ui.geometry import fit_to_screen
from ..ui.styles import SPACE_S
from ..ui.widgets import button_bar, dialog_layout

from .cache import _noaa_cache
from .widgets import ImageZoomView
from .workers import Stopped, StoppableThread

logger = logging.getLogger(__name__)

MAX_FILES = 8  # two hours of a station, whatever a list says of a burst
PAD_MINUTES = 2  # around the listed times, which are to the minute
GAP_SECONDS = 5  # without a sample that make a gap in the plot: a sweep takes 0.25 s
PLOT_ROWS = 800  # on a linear frequency axis, finer than the plot
SHORT_PLOT = 8  # seconds: ticks can come closer than a second under this
CHANNEL_HOLE = 3  # channel spacings from the nearest channel: more is a band with nothing in it


def burst_stations(text):
    """The stations a list line names, sure ones first: a name in brackets is
    a doubtful detection."""
    names = [name.strip() for name in text.split(",") if name.strip()]
    sure = [name for name in names if not name.startswith("(")]
    doubtful = [name.strip("()") for name in names if name.startswith("(")]
    return sure + doubtful


def own_channels(freqs, data):
    """``(freqs, data)`` with each frequency once, ascending: a receiver can
    sweep one twice, and then it is their mean. Data is time x frequency."""
    unique, which = np.unique(freqs, return_inverse=True)
    if len(unique) == len(freqs):
        return unique, data.astype(float)
    totals = np.zeros((data.shape[0], len(unique)))
    np.add.at(totals, (slice(None), which), data.astype(float))
    return unique, totals / np.bincount(which)


def on_frequency_rows(freqs, shown):
    """``shown`` (channels x time, ``freqs`` ascending) on rows even in
    frequency, top row highest, each from the nearest channel: the channels of
    these receivers are not evenly spaced, and a row per channel puts a burst
    at the wrong frequency. Rows in a hole in the band are blank."""
    step = (freqs[-1] - freqs[0]) / PLOT_ROWS
    rows = freqs[0] + (np.arange(PLOT_ROWS) + 0.5) * step
    nearest = np.clip(np.searchsorted((freqs[:-1] + freqs[1:]) / 2, rows), 0, len(freqs) - 1)
    out = shown[nearest].astype(float)
    out[np.abs(freqs[nearest] - rows) > CHANNEL_HOLE * np.median(np.diff(freqs))] = np.nan
    return out[::-1]


def time_labels(axes, seconds):
    """Seconds on the time axis, and tenths on a plot too short for them to
    tell one tick from the next."""
    from matplotlib.dates import DateFormatter, num2date
    from matplotlib.ticker import FuncFormatter

    if seconds < SHORT_PLOT:
        axes.xaxis.set_major_formatter(FuncFormatter(lambda x, _place: num2date(x).strftime("%H:%M:%S.%f")[:10]))
    else:
        axes.xaxis.set_major_formatter(DateFormatter("%H:%M:%S"))


def spectrum_figure(times, freqs, data, title):
    """A QImage of a spectrum with its axes, each channel against its median."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.dates import date2num
    from matplotlib.figure import Figure

    freqs, data = own_channels(freqs, data)
    shown = data.T
    shown = shown - np.median(shown, axis=1, keepdims=True)
    limits = np.percentile(shown, [2, 99.7])
    shown = on_frequency_rows(freqs, shown)
    figure = Figure(figsize=(11, 5), dpi=100)
    canvas = FigureCanvasAgg(figure)
    axes = figure.add_subplot(111)
    # One extent over a missing file would stretch the rest across it, and move a burst
    seconds = (times - times[0]) / np.timedelta64(1, "s")
    breaks = np.flatnonzero(np.diff(seconds) > GAP_SECONDS) + 1
    place = lambda index: date2num(times[index].astype("datetime64[ms]").astype(datetime))
    for first, last in zip(np.r_[0, breaks], np.r_[breaks, len(times)]):
        axes.imshow(
            shown[:, first:last], aspect="auto", extent=(place(first), place(last - 1), freqs[0], freqs[-1]),
            cmap="viridis", vmin=limits[0], vmax=max(limits[1], limits[0] + 1), interpolation="nearest",
        )
    axes.set_xlim(place(0), place(-1))
    axes.set_ylim(freqs[0], freqs[-1])
    axes.xaxis_date()
    time_labels(axes, seconds[-1])
    axes.set_xlabel("Time (UT)")
    axes.set_ylabel("Frequency (MHz)")
    axes.set_title(title)
    figure.tight_layout()
    canvas.draw()
    width, height = canvas.get_width_height()
    return QImage(bytes(canvas.buffer_rgba()), width, height, QImage.Format_RGBA8888).copy()


class BrokenFile(Exception):
    """A station's file that cannot be read."""


class CallistoSpectrumWorker(StoppableThread):
    """One station's spectrum around a burst. Says in one sentence why not."""

    ready = pyqtSignal(object, str)  # the QImage, its description
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, day, begin, end, station):
        super().__init__()
        self.day, self.station = day, station
        midnight = datetime(day.year, day.month, day.day)
        self.begin = midnight + timedelta(hours=begin, minutes=-PAD_MINUTES)
        self.end = midnight + timedelta(hours=end, minutes=PAD_MINUTES)

    def run(self):
        from ..radio_data_downloader.radio_data_downloader import ArchiveUnreachable

        try:
            self.ready.emit(*self.fetch())
        except Stopped:
            pass
        except LookupError as e:
            self.failed.emit(str(e))
        except ArchiveUnreachable:
            self.failed.emit("Could not reach the e-CALLISTO archive")
        except OSError as e:
            logger.info("e-CALLISTO spectrum: %s", e)
            self.failed.emit("The e-CALLISTO files could not be fetched")
        except BrokenFile:
            self.failed.emit(f"The file from {self.station} could not be read")
        except Exception:  # out of a thread it would take the application along
            logger.exception("e-CALLISTO spectrum of %s", self.station)
            self.failed.emit(f"The spectrum from {self.station} could not be made")

    def _file(self, item):
        """A file of the archive, from the day's cache if it is there."""
        from ..radio_data_downloader.radio_data_downloader import fetch

        folder = _noaa_cache.get_date_cache_dir(self.day) / "callisto"
        folder.mkdir(exist_ok=True)
        path = folder / re.sub(r"\.gz$", "", item["name"], flags=re.IGNORECASE)
        if not path.exists():
            fetch(item["url"], str(path), self.check_stop_flag)
        return str(path)

    def check_stop_flag(self):
        return self.cancelled

    def fetch(self):
        from ..radio_data_downloader.radio_data_downloader import ecallisto_files, read_callisto

        self.progress.emit(f"Looking for {self.station}'s files…")
        found = ecallisto_files(self.begin, self.end, self.station)
        self.check_stop()
        if not found:
            raise LookupError(f"The archive has no file from {self.station} at this time")
        focus = found[0]["focus"]  # a station's receivers do not share a band
        found = [item for item in found if item["focus"] == focus][:MAX_FILES]
        parts = []
        for index, item in enumerate(found):
            self.progress.emit(f"File {index + 1} of {len(found)} from {self.station}…")
            path = self._file(item)
            try:
                parts.append(read_callisto(path)[:3])
            except Exception as e:  # astropy has many ways of saying it
                logger.info("e-CALLISTO file %s: %s", path, e)
                os.remove(path)
                raise BrokenFile() from e
            self.check_stop()
        if any(not np.array_equal(part[1], parts[0][1]) for part in parts):
            parts = parts[:1]  # the band changed part way through
        times = np.concatenate([part[0] for part in parts])
        data = np.concatenate([part[2] for part in parts])
        keep = (times >= np.datetime64(self.begin, "ms")) & (times <= np.datetime64(self.end, "ms"))
        if not keep.any():
            raise LookupError(f"The files from {self.station} hold nothing at this time")
        times, data = times[keep], data[keep]
        freqs = parts[0][1]
        title = f"{self.station} · {self.day} · digits over each channel's median"
        text = (
            f"{self.station}, focus code {focus}: {str(times[0])[11:19]}–{str(times[-1])[11:19]} UT, "
            f"{freqs[0]:.0f}–{freqs[-1]:.0f} MHz, uncalibrated"
        )
        return spectrum_figure(times, freqs, data, title), text


class CallistoView(QDialog):
    """One burst, one station at a time."""

    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab
        self.worker = None
        self.burst = self.day = None
        self.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        fit_to_screen(self, 1100, 640)
        layout = dialog_layout(self)
        top = QHBoxLayout()
        top.setSpacing(SPACE_S)
        top.addWidget(QLabel("Station"))
        self.stations = QComboBox()
        self.stations.activated.connect(lambda _index: self.fetch())
        top.addWidget(self.stations)
        self.status = QLabel()
        self.status.setObjectName("Hint")
        top.addWidget(self.status, 1)
        layout.addLayout(top)
        self.view = ImageZoomView(self)
        layout.addWidget(self.view, 1)
        bar = QHBoxLayout()
        bar.setSpacing(SPACE_S)
        self.save_btn = QPushButton("Save…")
        self.save_btn.clicked.connect(self.save)
        self.save_btn.setEnabled(False)
        bar.addWidget(self.save_btn)
        bar.addStretch()
        buttons, _ = button_bar(secondary=("Close", self.reject))
        bar.addWidget(buttons)
        layout.addLayout(bar)
        self.image = None

    def show_burst(self, day, burst):
        self.day, self.burst = day, burst
        self.setWindowTitle(f"e-CALLISTO · {day} · {burst[2].split(' · ')[0]} at {int(burst[0]):02d}:{int(burst[0] * 60) % 60:02d}")
        self.stations.clear()
        self.stations.addItems(burst_stations(burst[3]))
        self.fetch()

    def _leave_worker(self):
        if self.worker is not None:
            abandon(self.worker)
            self.worker = None

    def fetch(self):
        self._leave_worker()
        station = self.stations.currentText()
        self.image = None
        self.save_btn.setEnabled(False)
        self.view.set_image(QImage())
        if not station:
            self.status.setText("The list names no station for this burst")
            return
        self.worker = CallistoSpectrumWorker(self.day, self.burst[0], self.burst[1], station)
        self.worker.progress.connect(self.status.setText)
        self.worker.ready.connect(self.on_ready)
        self.worker.failed.connect(self.status.setText)
        self.worker.start()

    def on_ready(self, image, text):
        self.image = image
        self.view.set_image(image)
        self.status.setText(text)
        self.save_btn.setEnabled(True)

    def save(self):
        name = f"eCALLISTO_{self.stations.currentText()}_{self.day:%Y%m%d}_{int(self.burst[0] * 60):04d}.png"
        target, _ = QFileDialog.getSaveFileName(self, "Save spectrum", name, "PNG image (*.png)")
        if not target:
            return
        if not os.path.splitext(target)[1]:
            target += ".png"
        if not self.image.save(target, "PNG"):
            show_error(self, "Could not save the spectrum", f"The image could not be saved to:\n{target}")

    def reject(self):
        self._leave_worker()
        super().reject()

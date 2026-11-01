"""
Opening dynamic spectra in the viewer: local and server files, their
images, time and frequency axes, and the metadata window.
"""

import logging
import os
import gc
import json
import posixpath
import threading
import time
from concurrent.futures import Future
from types import SimpleNamespace
import numpy as np
from astropy.io import fits
from PyQt5.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFileDialog,
    QMessageBox,
    QLabel,
    QDialog,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QTabWidget,
)
from PyQt5 import QtWidgets
from PyQt5.QtCore import QSettings, QSignalBlocker, Qt, QTimer
from PyQt5.QtGui import QFont, QColor
from ..ui.styles import palette_for as get_palette

from .spectra_common import _RemoteRead, _local_block_stats, _size_text, read_view
logger = logging.getLogger(__name__)


from ..ui.activity import begin_activity, track_thread
from ..ui.background import Cancelled, run_in_thread, wait_for
from ..ui.busy import busy
from ..ui.recent_files import forget_spectrum, remember_spectrum
from ..ui.widgets import button_bar
from ..ui.geometry import fit_to_screen


class _NoDataImage:
    """An image whose NODATA samples (the header's value for a missing
    sample, as e-CALLISTO and Yamagawa files mark their gaps) read as NaN.
    Reads are float32 copies of the array (memory-mapped or not)."""

    def __init__(self, data, nodata):
        self._data = data
        self._nodata = float(nodata)
        self.shape, self.ndim, self.size = data.shape, data.ndim, data.size
        self.dtype = np.dtype(np.float32)

    def __getitem__(self, key):
        x = np.array(self._data[key], dtype=np.float32)
        x[x == self._nodata] = np.nan
        return x

    @property
    def T(self):
        return self[...].T


def open_local_spectrum(path):
    """The HDUs of the FITS file ``path``, memory-mapped (only what is
    shown is read), unless an image of its is scaled (BSCALE, BZERO or
    BLANK, as 16-bit e-CALLISTO and Yamagawa files are): astropy cannot
    map those, so the file is read whole."""
    hdul = fits.open(path, memmap=True)
    for hdu in hdul:
        header = hdu.header
        if getattr(hdu, "is_image", False) and (
            header.get("BSCALE", 1) != 1
            or header.get("BZERO", 0) != 0
            or "BLANK" in header
        ):
            hdul.close()
            return fits.open(path, memmap=False)
    return hdul


_SECONDS_PER = {"d": 86400.0, "h": 3600.0, "min": 60.0, "ms": 0.001}
_SECOND_NAMES = ("s", "sec", "second", "seconds")


def observation_start(header):
    """DATE-OBS as an astropy Time, completed by TIME-OBS when DATE-OBS is
    the date alone (as e-CALLISTO and Yamagawa files write them), or None.
    Returns (time, whether it is a date only)."""
    from astropy.time import Time as AstroTime

    date_obs = header.get("DATE-OBS")
    if not date_obs:
        return None, True
    text = str(date_obs).strip().replace("/", "-")
    date_only = "T" not in text and " " not in text
    if date_only and header.get("TIME-OBS"):
        text += "T" + str(header["TIME-OBS"]).strip()
        date_only = False
    try:
        return AstroTime(text, scale="utc"), date_only
    except Exception:
        return None, True


def axes_from_header(header, naxis1, naxis2):
    """(time axis in MJD seconds, frequency axis in MHz) of a spectrum
    described by its header's CTYPE, CRVAL, CDELT, CRPIX and CUNIT (either
    None if the header does not describe that axis).

    A CRPIX of 0 is taken as 0-based (pixel 0 at CRVAL: e-CALLISTO and
    Yamagawa files). A time CRVAL is Unix seconds, a JD or an MJD in days,
    or seconds: of the day of DATE-OBS (the instant TIME-OBS or DATE-OBS
    itself names, when it equals it) or from DATE-OBS.
    """
    time_axis = freq_axis = None
    for ax_idx, naxis in ((1, naxis1), (2, naxis2)):
        ctype = str(header.get(f"CTYPE{ax_idx}", "")).upper()
        cunit = str(header.get(f"CUNIT{ax_idx}", "")).strip().lower()
        crval = float(header.get(f"CRVAL{ax_idx}", 0))
        cdelt = float(header.get(f"CDELT{ax_idx}", 1))
        crpix = float(header.get(f"CRPIX{ax_idx}", 1))
        offsets = np.arange(naxis) - (crpix - 1 if crpix != 0 else 0)
        if "FREQ" in ctype:
            freqs = crval + offsets * cdelt
            if cunit == "hz":
                freqs /= 1e6
            elif cunit == "khz":
                freqs /= 1000.0
            elif cunit == "ghz":
                freqs *= 1000.0
            freq_axis = freqs
        elif "TIME" in ctype:
            per_second = _SECONDS_PER.get(cunit, 1.0)
            in_seconds = cunit in _SECOND_NAMES
            start, date_only = observation_start(header)
            if crval > 1000000000:
                from astropy.time import Time as AstroTime

                ref_s = AstroTime(crval, format="unix").mjd * 86400.0
            elif crval > 2400000.5 and not in_seconds:
                ref_s = (crval - 2400000.5) * 86400.0
            elif (
                30000 < crval < 100000
                and not in_seconds
                and (start is None or abs(crval - start.mjd) < 1.5)
            ):
                ref_s = crval * 86400.0
            elif start is None:
                ref_s = crval * per_second
            else:
                start_s = start.mjd * 86400.0
                day_s = np.floor(start.mjd) * 86400.0
                crval_s = crval * per_second
                if abs(day_s + crval_s - start_s) <= max(1.0, abs(cdelt) * per_second):
                    ref_s = start_s  # the seconds of the day DATE-OBS/TIME-OBS name
                else:
                    ref_s = (day_s if date_only else start_s) + crval_s
            time_axis = ref_s + offsets * cdelt * per_second
    return time_axis, freq_axis


class SpectraFilesMixin:
    """Opens files (local or on a server) and reads their axes and metadata.

    Mixed into :class:`MainWindow`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``_NOISE_NAMES``, ``_bandpass_scope``, ``_cache_page``, ``_completeLoad``,
    ``_data_cache``, ``_display_data``, ``_get_slice_data``,
    ``_is_bandpass_enabled``, ``_redisplay``, ``_remote_reads``,
    ``_restore_masks``, ``_select_region``, ``_total_pages``,
    ``_updateNavigationStats``, ``_update_mask_summary``,
    ``_update_undo_actions``, ``canvas``, ``current_theme``, ``imageMenu``,
    ``loadSlice``, ``logger``, ``minimap``, ``navModeCombo``, ``redo_stack``,
    ``swapAct``, ``undo_stack``
    """

    @staticmethod
    def _image_data(hdu, index):
        """An HDU's image as 2-D (leading axes of length 1 dropped), or None."""
        if index != 0 and not getattr(hdu, "is_image", True):
            return None  # a table
        data = hdu.data
        if data is None or getattr(data, "ndim", 0) < 2:
            return None
        while data.ndim > 2 and data.shape[0] == 1:
            data = data[0]
        if data.ndim != 2:
            return None
        nodata = hdu.header.get("NODATA")
        if nodata is not None and isinstance(data, np.ndarray):
            try:
                return _NoDataImage(data, nodata)
            except (TypeError, ValueError):
                pass
        return data

    def _header_of(self, index, hdul=None):
        """HDU ``index``'s header (of the open file, or of ``hdul``), with
        the primary header's cards it lacks (e.g. DATE-OBS, when the image
        is in an extension)."""
        hdul = self.hdul if hdul is None else hdul
        header = hdul[index].header
        if index == 0:
            return header
        header = header.copy()
        for card in hdul[0].header.cards:
            if card.keyword and card.keyword not in header and card.keyword not in (
                "SIMPLE", "BITPIX", "NAXIS", "EXTEND", "COMMENT", "HISTORY",
            ):
                header.append(card)
        return header

    def _image(self):
        """The image shown: the spectrum, or another of the file's images
        of the same shape (View > Image), e.g. a noise image."""
        return self._image_data(self.hdul[self._image_index], self._image_index)

    def _fill_image_menu(self, shape):
        """View > Data Shown: the file's images that can be shown in place of
        the spectrum (``_images``: the primary image and extensions of the
        spectrum's ``shape``)."""
        self.imageMenu.clear()
        group = QtWidgets.QActionGroup(self.imageMenu)
        for i, name in self._images:
            act = self.imageMenu.addAction(self._image_label(i, name))
            act.setCheckable(True)
            act.setChecked(i == self._image_index)
            act.triggered.connect(lambda _=False, i=i: self.showImage(i))
            group.addAction(act)
        # (the submenu's entry is its menuAction: disabling the menu alone
        # leaves the entry looking and opening as if enabled)
        self.imageMenu.menuAction().setEnabled(len(self._images) > 1)
        # Swapping time and frequency only applies to square images
        self.swapAct.setEnabled(shape[0] == shape[1])

    def _check_shown_image(self):
        """View > Data Shown ticks the image shown (not one that failed)."""
        for act, (i, _) in zip(self.imageMenu.actions(), self._images):
            act.setChecked(i == self._image_index)

    def _image_label(self, index, name):
        """How View > Data Shown names the file's image ``index``."""
        if index == self._spectrum_index:
            return "Dynamic spectrum"
        if str(name).upper() in self._NOISE_NAMES:
            return f"Noise per pixel ({name})"
        return str(name)

    def showImage(self, index):
        """Show the file's image of HDU ``index`` instead (same page)."""
        if index == self._image_index or not self.hdul:
            return
        name = dict(self._images).get(index, "")
        shown = self._image_label(index, name)
        try:
            view = self._read_view(f"Reading {shown}…", self._image_data(self.hdul[index], index))
            self._image_index = index
            self._stop_remote_reads()
            self._data_cache.clear()
            self._cached_bandpass_profile = None
            with busy(f"Showing {shown}…", self):
                self._show_view(view)
        except Exception as e:
            self._check_shown_image()
            if type(e).__name__ == "Cancelled":
                self.statusBar().showMessage("Cancelled", 5000)
            else:
                QMessageBox.critical(self, "Error", f"Failed to show {name}:\n{e}")
            return
        self.statusBar().showMessage(f"Showing: {shown}", 5000)

    def _read_view(self, label, image, time_dim=None):
        """``read_view`` of ``image`` (all of it in Full Spectrum mode) off
        the GUI thread; nothing on show changes."""
        time_dim = self._time_dim if time_dim is None else time_dim
        total, whole = len(self._full_time_axis), self._nav_mode == "Full"
        if self._remote:
            label = label.replace("…", " from the server…")
        return self._server_work(label, lambda *_: read_view(image, time_dim, total, whole))

    def _show_view(self, view, choose_mode=False):
        """Show what ``read_view`` read of the image shown (_image): the
        current page, or all of it; on opening (``choose_mode``) Windowed
        or Full by the file's size, from the first page."""
        skip, overview, whole = view
        self._show_overview(skip, overview)

        # Initialize Navigation
        if choose_mode:
            self._nav_mode = "Windowed" if self._is_large_file else "Full"
            self.navModeCombo.blockSignals(True)
            self.navModeCombo.setCurrentText(
                "Windowed" if self._is_large_file else "Full Spectrum"
            )
            self.navModeCombo.blockSignals(False)
            self._current_page = 0
            self._page_origin = 0
        self._updateNavigationStats()

        if self._nav_mode != "Full":
            self.loadSlice()  # Initial load (a page, or the time range)
        else:
            # Use centralized display logic to honor persistent toggles (Bandpass, etc.)
            self._display_data(whole, self._full_time_axis, self._full_freq_axis)

    def _every_sample_wanted(self):
        """Whether the overview is also built from every sample (a burst
        shorter than a block then shows in the strip): always for a local
        file; for one on a server, where it means reading all of it, only
        if View > Overview from Every Sample on Server Files is on."""
        return self._remote is None or self.overviewAct.isChecked()

    def _show_overview(self, skip, overview):
        """The whole file at low resolution (every ``skip``-th time sample,
        as read by ``read_view``), for the strip, the preview of a page
        being read and the file's bandpass; then, in the background, from
        every sample."""
        self._lowres_skip = skip
        self._full_lowres_data = overview

        # Minimap makes its own copy internally, so pass view directly
        self.minimap.set_full_data(overview.T)
        # Then, in the background, from every sample (a burst shorter than
        # a block shows in the strip, as its maximum)
        self._overview_exact = False
        if self._every_sample_wanted():
            self._start_overview_stats()

    def onOverviewToggled(self, on):
        """View > Overview from Every Sample on Server Files: remembered;
        for a file open on a server, started now, or stopped (the strip
        then shows the every-k-th-sample overview again)."""
        QSettings("SolarViewer", "DynamicSpectrumViewer").setValue("overview_remote", on)
        if not self.hdul or self._remote is None:
            return
        if on:
            self._start_overview_stats()
            return
        self._stop_overview()
        if getattr(self, "_overview_exact", False):
            image, time_dim, total = self._image(), self._time_dim, len(self._full_time_axis)
            try:
                skip, overview, _ = self._server_work(
                    "Reading the overview from the server…",
                    lambda *_: read_view(image, time_dim, total, False),
                )
            except Exception as e:
                if type(e).__name__ == "Cancelled":
                    self.statusBar().showMessage("Cancelled", 5000)
                else:
                    QMessageBox.critical(self, "Error", f"Failed to read the overview:\n{e}")
                return
            self._show_overview(skip, overview)
            self._updateNavigationStats()
            if self._is_bandpass_enabled and self._bandpass_scope == "file":
                self._redisplay()

    @staticmethod
    def _guess_time_dim(data, header, nt):
        """Which axis of the image (numpy order) is time: the one as long
        as the time axis (``nt`` samples); for a square image the header's
        (CTYPE), else the rows (View > Swap Time and Frequency corrects it)."""
        if data.shape[0] != data.shape[1]:
            return 0 if data.shape[0] == nt else 1
        # numpy axis 0 is FITS axis 2, axis 1 is FITS axis 1
        ctype1 = str(header.get("CTYPE1", "")).upper()
        ctype2 = str(header.get("CTYPE2", "")).upper()
        if "TIME" in ctype1 or "FREQ" in ctype2:
            return 1
        return 0

    def swapTimeAndFrequency(self):
        """Take the image's other axis as time (square images the header
        doesn't describe)."""
        if not self.hdul:
            return
        data = self._image()
        if data.shape[0] != data.shape[1]:
            QMessageBox.information(
                self,
                "Swap Time and Frequency",
                "The image's axes have different lengths, so which is time is certain.",
            )
            return
        time_dim = 1 - self._time_dim
        try:
            view = self._read_view("Reading the image…", data, time_dim)
            self._time_dim = time_dim
            self._stop_remote_reads()
            self._data_cache.clear()
            self._cached_bandpass_profile = None
            with busy("Showing the image…", self):
                self._show_view(view)
        except Exception as e:
            if type(e).__name__ == "Cancelled":
                self.statusBar().showMessage("Cancelled", 5000)
            else:
                QMessageBox.critical(self, "Error", f"Failed to swap the axes:\n{e}")
            return
        self.statusBar().showMessage(
            f"Time is now the image's {'rows' if self._time_dim == 0 else 'columns'}.", 5000
        )

    def _start_overview_stats(self):
        """Compute the overview from every sample in the background: each
        block of _lowres_skip time samples gives its mean (for the quick
        preview and file medians) and maximum (for the overview strip).
        Local files are read here; files on a server, on the server."""
        self._stop_overview()
        skip = self._lowres_skip
        if skip <= 1:
            return  # the overview has every sample already
        image, time_dim, remote = self._image(), self._time_dim, self._remote is not None

        def work():
            if remote:
                return image.block_stats(time_dim, skip)
            return _local_block_stats(image, time_dim, skip)

        job = _RemoteRead(work, self)
        job.task = track_thread(job, "Building the overview from every sample…", self)
        self._overview_job = job
        hdul, index = self.hdul, self._image_index

        def current():
            return job is self._overview_job and hdul is self.hdul and index == self._image_index

        def progress(done, total):
            if current() and total:
                job.task.update(done=100 * done // total, total=100)

        def done(result):
            if not current():
                return
            self._overview_job = None
            self._overview_exact = True
            mean, peak = result
            if mean.shape != self._full_lowres_data.shape:
                return
            self._full_lowres_data = np.ascontiguousarray(mean, dtype=np.float32)
            self._cached_bandpass_profile = None
            self.minimap.set_full_data(np.ascontiguousarray(peak.T))
            self._updateNavigationStats()
            self.statusBar().showMessage(
                "Overview built from every sample (the strip shows each block's maximum)", 5000
            )
            if self._is_bandpass_enabled and self._bandpass_scope == "file":
                self._redisplay()

        def failed(message):
            if current():
                self._overview_job = None
                self.logger.info(f"Overview from every k-th sample only: {message}")
                self.statusBar().showMessage(
                    f"The overview uses every {skip}th sample ({message})", 8000
                )

        job.progress.connect(progress)
        job.done.connect(done)
        job.failed.connect(failed)
        job.finished.connect(job.deleteLater)
        job.start()

    def _table_time_axis(self, hdu):
        """MJD seconds from a TIME_AXIS table (TIME_MJD, and UTC strings).

        Older files from make_dynamic_spectra stored TIME_MJD in single
        precision (values ~5.6 minutes apart) and UTC to the second: their
        times come from the UTC column, fitted to a straight line when the
        samples are evenly spaced (sub-second cadences).
        """
        mjd = np.asarray(hdu.data["TIME_MJD"])
        names = [n.upper() for n in hdu.columns.names]
        if mjd.dtype.itemsize >= 8 or "UTC" not in names:
            return mjd.astype(np.float64) * 86400.0
        try:
            from astropy.time import Time as AstroTime

            utc = np.char.rstrip(np.char.strip(np.asarray(hdu.data["UTC"]).astype(str)), ".")
            seconds = AstroTime(utc, format="iso", scale="utc").mjd * 86400.0
            if max(len(u) for u in utc) <= 19:
                # Cut to whole seconds (not rounded): 0.5 s early on average
                seconds += 0.5
        except Exception as e:
            self.logger.warning(f"UTC column not read ({e}); times are single precision")
            return mjd.astype(np.float64) * 86400.0
        if len(seconds) > 2:
            index = np.arange(len(seconds))
            fit = np.polyval(np.polyfit(index, seconds - seconds[0], 1), index) + seconds[0]
            if np.max(np.abs(fit - seconds)) < 1.0:  # even spacing
                seconds = fit
        self.logger.info("Times read from the UTC column (TIME_MJD is single precision)")
        return seconds

    def _interpret_time_column(self, t_raw, header):
        """Interpret a raw TIME column from a FITS binary table."""
        t_first = float(t_raw[0])

        # Each candidate: (label, conversion_func producing MJD-seconds)
        candidates = [
            ("MJD",
             lambda t: t * 86400.0),
            ("Unix epoch days",
             lambda t: (t + 40587.0) * 86400.0),
            ("Unix seconds",
             lambda t: (t / 86400.0 + 40587.0) * 86400.0),
            ("Proleptic Gregorian day",
             lambda t: (t - 678576.0) * 86400.0),
        ]

        # --- Try to validate against DATE-OBS ---
        date_obs_str = header.get("DATE-OBS", None)
        if date_obs_str:
            try:
                from astropy.time import Time as AstroTime

                ref = AstroTime(date_obs_str.replace("/", "-"))
                ref_mjd = ref.mjd  # e.g. 59764.0 for 2022-07-04

                best = None
                best_diff = float("inf")
                for label, func in candidates:
                    try:
                        mjd_sec = func(t_first)
                        candidate_mjd = mjd_sec / 86400.0
                        diff = abs(candidate_mjd - ref_mjd)
                        if diff < best_diff:
                            best_diff = diff
                            best = (label, func)
                    except Exception:
                        continue

                if best is not None and best_diff < 1.5:
                    self.logger.info(
                        f"TIME column interpreted as '{best[0]}' "
                        f"(delta={best_diff:.4f}d from DATE-OBS)"
                    )
                    return best[1](t_raw)

                self.logger.warning(
                    f"No TIME interpretation matched DATE-OBS "
                    f"(best delta={best_diff:.1f}d); using range fallback"
                )
            except Exception as e:
                self.logger.warning(f"DATE-OBS validation failed ({e}); using range fallback")

        # --- Fallback: range-based heuristic ---
        if 600000 < t_first < 800000:
            return (t_raw - 678576.0) * 86400.0
        elif t_first > 1000000000:
            return (t_raw / 86400.0 + 40587.0) * 86400.0
        elif 30000 < t_first < 100000:
            return t_raw * 86400.0
        elif 10000 < t_first < 30000:
            return (t_raw + 40587.0) * 86400.0
        else:
            return t_raw

    def openFile(self, fileName=None, remote=None):
        """Open a FITS file; asks with a file dialog if no path is given.

        (When connected to a QAction, Qt passes ``checked`` as the first
        argument, which is falsy here, so the dialog is still shown.)
        With ``remote`` (a RemoteFits), the file is on a server and
        ``fileName`` names it.
        """
        if not fileName:
            fileName, _ = QFileDialog.getOpenFileName(
                self, "Open FITS File", "", "FITS Files (*.fits *.fts);;All Files (*)"
            )
        if not fileName:
            return
        if remote is None and self.link.connected:
            # An image belongs in SolarViewer, which shows it in a tab
            from ..processing.fits_io import IMAGE, fits_kind

            if fits_kind(fileName) == IMAGE:
                self.link.send({"open_image": fileName})
                self.statusBar().showMessage(
                    f"{os.path.basename(fileName)} is an image: opened in SolarViewer",
                    8000,
                )
                return

        # (a read is waited for in a local event loop: _on_link_message waits)
        self._working += 1
        try:
            self._open(fileName, remote)
        finally:
            self._working -= 1

    def _open(self, fileName, remote):
        """Read the file off the GUI thread (a file that can't be read, or
        Cancel, leaves the one shown, its masks and its undo history as they
        are), then show it."""
        shown = os.path.basename(fileName)
        opened = []

        def read(progress, cancelled):
            hdul = remote.hdul if remote else open_local_spectrum(fileName)
            opened.append(hdul)
            try:
                result = self._read_file(hdul, progress)
                if cancelled():
                    raise Cancelled()
            except BaseException:
                hdul.close()
                raise
            return hdul, result

        label = f"Reading {shown} from the server…" if remote else f"Opening {shown}…"
        try:
            hdul, file = self._server_work(label, read)
        except Exception as e:
            if type(e).__name__ == "Cancelled":
                self.statusBar().showMessage("Opening cancelled", 5000)
                return
            self.logger.error(f"Failed to open FITS file: {e}")
            if not opened and remote is None:
                forget_spectrum(fileName)
            QMessageBox.critical(self, "Error", f"Failed to open FITS file:\n{e}")
            return

        try:
            with busy(f"Showing {shown}…", self):
                self._show_file(fileName, remote, hdul, file)
        except Exception as e:
            self.logger.exception(f"Failed to open or plot FITS file: {e}")
            # (part shown: nothing of it, or of the file before, is kept)
            self._close_file()
            QMessageBox.critical(self, "Error", f"Failed to open FITS file:\n{e}")

    def _read_file(self, hdul, progress):
        """All that showing the file ``hdul`` takes from it. Runs off the GUI
        thread, so it sets nothing on the window and touches no widget;
        ``progress(stage=...)`` says what it is reading. (Memory-mapped or
        not, an image's data is read or mapped once, here.)"""
        progress(stage="Finding the spectrum…")
        # The spectrum: the first 2-D image, primary or extension
        spectrum = next(
            (i for i, hdu in enumerate(hdul) if self._image_data(hdu, i) is not None), None
        )
        if spectrum is None:
            raise ValueError("the file has no 2-D image (dynamic spectrum)")
        data_ref = self._image_data(hdul[spectrum], spectrum)
        images = []
        for i, hdu in enumerate(hdul):
            data = self._image_data(hdu, i)
            if data is not None and data.shape == data_ref.shape:
                images.append((i, hdu.name or f"HDU {i}"))
        header = self._header_of(spectrum, hdul)

        # Detect if large (e.g., > 10^7 pixels)
        is_large = data_ref.size > 1e7 or data_ref.shape[0] > 10000

        progress(stage="Reading the time and frequency axes…")
        time_axis, freq_axis = self._read_axes(hdul, header, data_ref)
        nt = len(time_axis)
        time_dim = self._guess_time_dim(data_ref, header, nt)

        progress(stage="Reading the spectrum…" if not is_large else "Reading the overview…")
        return SimpleNamespace(
            spectrum=spectrum,
            images=images,
            headers=[(hdu.name if hdu.name else f"HDU {i}", hdu.header) for i, hdu in enumerate(hdul)],
            metadata=str(header),
            is_large=is_large,
            time_axis=time_axis,
            freq_axis=freq_axis,
            time_dim=time_dim,
            shape=data_ref.shape,
            view=read_view(data_ref, time_dim, nt, not is_large),
        )

    def _read_axes(self, hdul, header, data_ref):
        """(time axis, frequency axis) of the file: from its tables, else
        from its header (either None if neither has it)."""
        time_axis = None
        freq_axis = None

        for hdu in hdul:
            if hdu.name.upper() == "TIME_AXIS":
                time_axis = self._table_time_axis(hdu)
            elif hdu.name.upper() == "FREQ_AXIS":
                freq_axis = hdu.data["FREQ_MHz"]
            elif isinstance(hdu, fits.BinTableHDU):
                cols = [c.name.upper() for c in hdu.columns]
                if "TIME" in cols and time_axis is None:
                    t_raw = np.array(hdu.data["TIME"].tolist()).flatten()
                    if len(t_raw) > 0:
                        time_axis = self._interpret_time_column(t_raw, header)
                if "FREQ" in cols and freq_axis is None:
                    f_raw = np.array(hdu.data["FREQ"].tolist()).flatten()
                    if len(f_raw) > 0:
                        freq_axis = f_raw

        if time_axis is None or freq_axis is None:
            try:
                naxis1 = header.get(
                    "NAXIS1",
                    data_ref.shape[1] if len(data_ref.shape) > 1 else 0,
                )
                naxis2 = header.get("NAXIS2", data_ref.shape[0])
                header_time, header_freq = axes_from_header(header, int(naxis1), int(naxis2))
                if time_axis is None:
                    time_axis = header_time
                if freq_axis is None:
                    freq_axis = header_freq
            except Exception as wcs_err:
                self.logger.warning(f"Axis extraction error: {wcs_err}")
        return time_axis, freq_axis

    def _show_file(self, fileName, remote, hdul, file):
        """The file read by ``_read_file`` replaces the one shown."""
        # Close previous file handle if any
        self._stop_remote_reads()
        self._stop_overview()
        if self.hdul:
            self.hdul.close()
        self.current_filename = fileName
        self._data_cache.clear()
        self._remote = remote
        self._goto_time = None
        self._range = None
        self._select_region(None)
        self.canvas.mark_time(None)
        self._cached_bandpass_profile = None  # Invalidate bandpass cache
        self._global_masked_freq_indices.clear()  # Clear stale masks
        self._masks = []
        self._keeps = []
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._update_mask_summary()
        gc.collect()  # Force memory release

        self.logger.info(f"Opening FITS file: {fileName}")
        self.hdul = hdul
        self._spectrum_index = self._image_index = file.spectrum
        self._images = file.images
        self._all_headers = file.headers
        self._metadata = file.metadata
        self._is_large_file = file.is_large
        self._full_time_axis = file.time_axis
        self._full_freq_axis = file.freq_axis
        self._time_dim = file.time_dim

        self._fill_image_menu(file.shape)
        self._restore_masks()
        self._show_view(file.view, choose_mode=True)

        self.undo_stack.clear()
        self.redo_stack.clear()
        self._update_undo_actions()
        gc.collect()  # Force memory release
        self.statusBar().showMessage(f"Loaded {os.path.basename(fileName)}", 5000)
        if remote is None:
            remember_spectrum(fileName)

    # -------------------------- Files on a server -------------------------------
    def openRemote(self):
        """Open a FITS file on an SSH server, reading only what is shown."""
        files = self._server()
        if files is None:
            return
        from ..remote import RemoteFileBrowser

        picked = []
        browser = RemoteFileBrowser(files.connection, cache=files.cache, parent=self, pick="file")
        browser.pathPicked.connect(picked.append)
        browser.exec_()
        if picked:
            self._open_remote_path(picked[0])

    def _server(self):
        """The server's RemoteFiles, connecting first if need be (None if
        not connected). Launched from SolarViewer while it is connected, the
        connection dialog tries the same server straight away (keys or
        agent; no password is passed)."""
        files = self._remote_files
        if files is not None and files.connection.is_connected():
            return files
        try:
            from ..remote import ConnectionDialog, RemoteFileCache
            from ..remote.remote_files import RemoteFiles
        except ImportError as e:
            QMessageBox.warning(
                self,
                "Missing Dependency",
                f"Opening files on a server needs paramiko:\n\npip install paramiko\n\n{e}",
            )
            return None

        dialog = ConnectionDialog(self)
        saved = os.environ.get("SOLARVIEWER_SSH")
        if saved and files is None:
            try:
                dialog.connect_to(json.loads(saved))
            except (ValueError, TypeError, AttributeError) as e:
                self.logger.info(f"Could not reuse SolarViewer's server: {e}")
        if dialog.exec_() != QDialog.Accepted:
            return None
        connection = dialog.get_connection()
        if connection is None:
            return None
        if files is not None:
            files.close()
        self._remote_files = RemoteFiles(connection, RemoteFileCache())
        return self._remote_files

    def _open_remote_path(self, path):
        from ..remote.remote_fits import NotSliceable, RemoteFits

        files = self._remote_files
        name = f"{files.host}:{path}"
        try:
            remote = self._server_work(
                f"Reading {posixpath.basename(path)} from the server…",
                lambda *_: RemoteFits(files, path),
            )
        except NotSliceable as e:
            # Only a whole copy will do
            try:
                size = self._server_work(
                    f"Checking {posixpath.basename(path)}…", lambda *_: files._stat(path)[1]
                )
            except Exception as error:
                if type(error).__name__ == "Cancelled":
                    self.statusBar().showMessage("Opening cancelled", 5000)
                else:
                    QMessageBox.critical(self, "Error", f"Failed to open {name}:\n{error}")
                return
            reply = QMessageBox.question(
                self,
                "Download File",
                f"{posixpath.basename(path)} can't be read in parts ({e}).\n\n"
                f"Download all of it ({_size_text(size)})?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply != QMessageBox.Yes:
                return
            try:
                local = self._server_work(
                    f"Downloading {posixpath.basename(path)}…",
                    lambda progress, cancelled: files.fetch(
                        [path],
                        progress=lambda done, total, *_: progress(done, total),
                        cancelled=cancelled,
                    )[path],
                )
            except Exception as e:
                if type(e).__name__ == "Cancelled":
                    self.statusBar().showMessage("Opening cancelled", 5000)
                else:
                    QMessageBox.critical(self, "Error", f"Failed to download:\n{e}")
                return
            self.openFile(local)
            return
        except Exception as e:
            if type(e).__name__ == "Cancelled":
                self.statusBar().showMessage("Opening cancelled", 5000)
            else:
                self.logger.exception(f"Failed to open {name}")
                QMessageBox.critical(self, "Error", f"Failed to open {name}:\n{e}")
            return
        self.openFile(name, remote=remote)

    def _server_work(self, label, work, parent=None):
        """``work(progress, cancelled)`` on a thread, the window drawing and
        its input held back meanwhile, as ``ui.background``; returns what it
        returns (raises Cancelled if cancelled, once it has stopped: then no
        file it writes is left half done). No Qt or changing of the window's
        state in ``work``.

        ``progress(done, total)`` (bytes), and reads made in ``work``, show
        how far it got on the pill, ``progress(stage=...)`` what it is at.
        The pill and any dialog are ``parent``'s (another window of this one)
        if given."""
        from ..remote.remote_fits import reporting

        stop = threading.Event()
        ended = Future()
        parent = parent or self
        doing = [label]
        said = [label]
        tasks = []

        def announce():
            # (the pill stays up beside the dialog of a long wait, with the figures)
            tasks.append(begin_activity(label, parent, blocking=False))
            tasks[0].update(label=said[0])

        def progress(done=None, total=None, stage=None):
            if stop.is_set():
                return  # (it says "Cancelling…" until it has stopped)
            doing[0] = stage or doing[0]
            sizes = f"  {_size_text(done)} of {_size_text(total)}" if total else ""
            said[0] = doing[0] + sizes
            for task in tasks:
                task.update(label=said[0])

        def run():
            try:
                with reporting(progress, stop.is_set):
                    return work(progress, stop.is_set)
            finally:
                ended.set_result(None)

        # The pill says the newest activity, and the wait's own can only say
        # ``label``: this one begins once the wait has
        begin = QTimer()
        begin.setSingleShot(True)
        begin.timeout.connect(announce)
        begin.start(0)
        self._working += 1
        try:
            return run_in_thread(parent, label, run)
        except Cancelled:
            stop.set()
            for task in tasks:
                task.update(label="Cancelling…")
            try:
                wait_for(parent, "Cancelling…", ended)
            except Cancelled:
                pass  # (a stalled read: it ends with its timeout, unwatched)
            raise
        finally:
            begin.stop()
            self._working -= 1
            for task in tasks:
                task.finish()

    def _read_page(self, page_idx, show=True):
        """Read page ``page_idx`` (from the file or the server) in the
        background; shown when read if it is still the current page, else
        kept for later."""
        if page_idx in self._remote_reads:
            return
        hdul = self.hdul
        read = _RemoteRead(lambda: self._get_slice_data(page_idx, downsample=1), self)
        read.waited = None  # the Activity, while the view waits for this read
        self._remote_reads[page_idx] = read

        def finished():
            if self._remote_reads.get(page_idx) is read:
                del self._remote_reads[page_idx]
            if self._page_read is read:
                self._end_page_wait()
            read.deleteLater()

        def done(result):
            if hdul is not self.hdul:
                return
            if result[0] is None:
                failed("see the log")
                return
            self._cache_page(page_idx, *result)
            if page_idx == self._current_page:
                self.statusBar().clearMessage()
                self._completeLoad(page_idx, *result)
                # The pages either side, ready for paging
                # (from a server only the one ahead: the other is rarely
                # wanted, and reading it takes the link from what is)
                ahead = page_idx + (1 if getattr(self, "_page_step", 1) >= 0 else -1)
                for near in (ahead,) if self._remote else (page_idx + 1, page_idx - 1):
                    if 0 <= near < self._total_pages and near not in self._data_cache:
                        self._read_page(near)

        def failed(message):
            if hdul is self.hdul and page_idx == self._current_page:
                where = " from the server" if self._remote else ""
                self.statusBar().showMessage(f"Reading{where} failed: {message}")

        def progress(done_bytes, total):
            if read is self._page_read and total:
                read.task.update(label=f"{read.waited}  {_size_text(done_bytes)} of {_size_text(total)}")

        read.done.connect(done)
        read.failed.connect(failed)
        read.progress.connect(progress)
        read.finished.connect(finished)
        read.start()

    def _wait_for_page(self, page_idx):
        """The pill says the view is waiting for page ``page_idx``, being
        read (read-ahead, which nobody waits for, says nothing)."""
        self._end_page_wait()
        read = self._remote_reads.get(page_idx)
        if read is None:
            return
        where = " from the server" if self._remote else ""
        part = "the time range" if self._nav_mode == "Range" else f"page {page_idx + 1}"
        read.waited = f"Reading {part}{where}…"
        read.task = begin_activity(read.waited, self, blocking=False)
        self._page_read = read

    def _end_page_wait(self):
        read, self._page_read = self._page_read, None
        if read is not None:
            read.task.finish()

    def _stop_overview(self):
        if self._overview_job is not None:
            self._overview_job.stop()
            self._overview_job = None

    def _stop_remote_reads(self, wait=False):
        """Stop the background work on what is shown; with ``wait`` (closing),
        until it has stopped, 5 s at most in all."""
        deadline = time.monotonic() + 5

        def stop(job):
            job.stop()
            if wait:
                job.wait(max(0, int(1000 * (deadline - time.monotonic()))))

        self._end_page_wait()
        if self._fill_job is not None:
            stop(self._fill_job)
            self._fill_job = None
        if self._rfi_job is not None:
            stop(self._rfi_job)
            self._rfi_job = None
        if wait and self._overview_job is not None:
            stop(self._overview_job)
        for read in list(self._remote_reads.values()):
            stop(read)
        self._remote_reads.clear()

    def _close_file(self):
        """Forget the open file (e.g. when showing it failed)."""
        self._stop_remote_reads()
        self._stop_overview()
        if self.hdul:
            self.hdul.close()
        self.hdul = None
        self._remote = None
        self._data_cache.clear()
        self.current_filename = ""
        self._shown_raw = None
        self.canvas.clear_plot()
        self.minimap.set_full_data(None)
        self._select_region(None)
        self._masks = []
        self._keeps = []
        self._global_masked_freq_indices = set()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._update_mask_summary()
        self._update_undo_actions()
        self._update_status_summary()  # (the empty canvas again)

    # --------------------- View Metadata Dialog ---------------------------------
    def viewMetadata(self):
        """Show all FITS HDU metadata."""
        if not self._all_headers:
            QMessageBox.information(self, "Metadata", "No metadata available.")
            return

        # If a dialog is already open, raise it and return
        if self._metadata_dialog is not None:
            self._metadata_dialog.raise_()
            self._metadata_dialog.activateWindow()
            return

        with busy("Opening the metadata…", self):
            self._build_metadata_window()

    def _build_metadata_window(self):
        palette = get_palette(self.current_theme)

        # Create a non-modal dialog
        dlg = QDialog(self, Qt.Window)
        self._metadata_dialog = dlg
        dlg.setAttribute(Qt.WA_DeleteOnClose)

        def on_close():
            self._metadata_dialog = None

        dlg.finished.connect(on_close)
        dlg.setWindowTitle("FITS File Metadata")
        dlg.setMinimumSize(750, 750)
        fit_to_screen(dlg, 750, 750)

        main_layout = QVBoxLayout(dlg)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Compact Aesthetic Source Info
        source_container = QWidget()
        source_layout = QHBoxLayout(source_container)
        source_layout.setContentsMargins(35, 20, 35, 0)

        source_label = QLabel(os.path.basename(self.current_filename))
        source_label.setObjectName("Badge")
        source_layout.addWidget(source_label)
        source_layout.addStretch()
        main_layout.addWidget(source_container)

        # Search area - Styled precisely like SolarViewer
        search_widget = QWidget()
        search_layout = QHBoxLayout(search_widget)
        search_layout.setContentsMargins(35, 15, 35, 20)
        search_layout.setSpacing(15)

        search_label = QLabel("Search:")
        search_layout.addWidget(search_label)

        search_input = QLineEdit()
        search_input.setPlaceholderText("Filter headers across all columns...")
        search_layout.addWidget(search_input)
        main_layout.addWidget(search_widget)

        # Tab Widget Container
        tab_container = QWidget()
        tab_container_layout = QVBoxLayout(tab_container)
        tab_container_layout.setContentsMargins(35, 0, 35, 20)

        tab_widget = QTabWidget()
        tab_container_layout.addWidget(tab_widget)
        main_layout.addWidget(tab_container)

        # Typography for technical data
        mono_font = QFont("Monospace", 11)
        mono_font.setStyleHint(QFont.TypeWriter)
        bold_mono = QFont(mono_font)
        bold_mono.setBold(True)

        for hdu_name, hdu_header in self._all_headers:
            table = QTableWidget()
            table.setColumnCount(3)
            table.setHorizontalHeaderLabels(["Keyword", "Value", "Comment"])
            table.verticalHeader().setVisible(False)
            table.setSelectionBehavior(QAbstractItemView.SelectRows)
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            table.setAlternatingRowColors(True)
            table.setShowGrid(False)

            h_header = table.horizontalHeader()
            h_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
            h_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
            h_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
            h_header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)

            cards = hdu_header.cards
            table.setRowCount(len(cards))
            for i, card in enumerate(cards):
                k = QTableWidgetItem(str(card.keyword))
                k.setFont(bold_mono)

                v = QTableWidgetItem(str(card.value) if card.value is not None else "")
                v.setFont(mono_font)

                c = QTableWidgetItem(str(card.comment) if card.comment else "")
                c.setFont(mono_font)
                c.setForeground(QColor(palette["text_secondary"]))

                table.setItem(i, 0, k)
                table.setItem(i, 1, v)
                table.setItem(i, 2, c)

            table.setViewportMargins(5, 5, 5, 5)
            tab_widget.addTab(table, hdu_name)

        def filter_active_tab(text):
            text = text.lower()
            tbl = tab_widget.currentWidget()
            if not tbl:
                return
            for i in range(tbl.rowCount()):
                match = any(
                    text in (tbl.item(i, j).text().lower() if tbl.item(i, j) else "")
                    for j in range(tbl.columnCount())
                )
                tbl.setRowHidden(i, not match)

        search_input.textChanged.connect(filter_active_tab)
        tab_widget.currentChanged.connect(
            lambda: filter_active_tab(search_input.text())
        )

        box, _ = button_bar(
            primary=("Close", dlg.close),
            extras=[("Copy to Clipboard", self._copy_metadata_to_clipboard)],
        )
        box.setContentsMargins(35, 10, 35, 20)
        main_layout.addWidget(box)
        dlg.show()

    def _copy_metadata_to_clipboard(self):
        """Format and copy all metadata to clipboard."""
        if not self._all_headers:
            return

        lines = []
        for hdu_name, hdu_header in self._all_headers:
            lines.append(f"=== HDU: {hdu_name} ===")
            lines.append(str(hdu_header))
            lines.append("\n")

        text = "\n".join(lines)
        QApplication.clipboard().setText(text)
        self.statusBar().showMessage("Metadata copied to clipboard", 3000)

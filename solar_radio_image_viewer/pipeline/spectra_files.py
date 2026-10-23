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
    QPushButton,
    QDialog,
    QFrame,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QTabWidget,
    QProgressDialog,
)
from PyQt5 import QtWidgets
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QColor
from .simpl_theme import get_palette

from .spectra_common import _RemoteRead, _local_block_stats, _size_text
logger = logging.getLogger(__name__)


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
    ``swapAct``, ``undo_stack``, ``wait_cursor``
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
        return data if data.ndim == 2 else None

    def _find_spectrum(self):
        """Index of the file's first 2-D image (None if it has none)."""
        for i, hdu in enumerate(self.hdul):
            if self._image_data(hdu, i) is not None:
                return i
        return None

    def _header_of(self, index):
        """HDU ``index``'s header, with the primary header's cards it lacks
        (e.g. DATE-OBS, when the image is in an extension)."""
        header = self.hdul[index].header
        if index == 0:
            return header
        header = header.copy()
        for card in self.hdul[0].header.cards:
            if card.keyword and card.keyword not in header and card.keyword not in (
                "SIMPLE", "BITPIX", "NAXIS", "EXTEND", "COMMENT", "HISTORY",
            ):
                header.append(card)
        return header

    def _image(self):
        """The image shown: the spectrum, or another of the file's images
        of the same shape (View > Image), e.g. a noise image."""
        return self._image_data(self.hdul[self._image_index], self._image_index)

    def _find_images(self):
        """The file's images that can be shown in place of the spectrum
        (the primary image and extensions of its shape), in View > Image."""
        shape = self._image_data(self.hdul[self._spectrum_index], self._spectrum_index).shape
        self._images = []
        for i, hdu in enumerate(self.hdul):
            data = self._image_data(hdu, i)
            if data is not None and data.shape == shape:
                self._images.append((i, hdu.name or f"HDU {i}"))
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
        self._image_index = index
        self._stop_remote_reads()
        self._data_cache.clear()
        self._cached_bandpass_profile = None
        name = dict(self._images).get(index, "")
        try:
            if self._remote:
                self._server_work(
                    f"Reading {self._image_label(index, name)} from the server...",
                    lambda *_: self._show_image(),
                )
            else:
                with self.wait_cursor():
                    self._show_image()
        except Exception as e:
            if type(e).__name__ != "Cancelled":
                QMessageBox.critical(self, "Error", f"Failed to show {name}:\n{e}")
            return
        shown = self._image_label(index, name)
        self.statusBar().showMessage(f"Showing: {shown}", 5000)

    def _show_image(self, choose_mode=False):
        """Read the overview of the image shown (_image) and show it: the
        current page, or all of it; on opening (``choose_mode``) Windowed
        or Full by the file's size, from the first page."""
        data_ref = self._image()
        # Feature 2: Load Minimap (Full spectrum overview) - Always load for all files
        # Downsample significantly (e.g., 2000 points wide)
        total_samples = len(self._full_time_axis)
        self._lowres_skip = max(1, total_samples // 2000)
        if self._remote:
            # Few enough separate reads from the server
            time_dim = self._time_dim
            self._lowres_skip = max(
                self._lowres_skip, data_ref.min_step(time_dim)
            )
        skip = self._lowres_skip

        # Slicing the memmapped data carefully
        if self._time_dim == 0:
            # (time, freq)
            mini_data = data_ref[::skip, :]
            self._full_lowres_data = np.array(mini_data, dtype=np.float32)
            mini_data_for_display = self._full_lowres_data.T
        else:
            # (freq, time)
            mini_data = data_ref[:, ::skip]
            mini_data_for_display = np.array(mini_data, dtype=np.float32)
            self._full_lowres_data = mini_data_for_display.T

        # Handle infinities (NaN stays NaN naturally)
        self._full_lowres_data[np.isinf(self._full_lowres_data)] = np.nan

        # Minimap makes its own copy internally, so pass view directly
        self.minimap.set_full_data(mini_data_for_display)
        # Then, in the background, from every sample (a burst shorter than
        # a block shows in the strip, as its maximum)
        self._start_overview_stats()

        # Initialize Navigation
        if choose_mode:
            self._nav_mode = "Windowed" if self._is_large_file else "Full"
            self.navModeCombo.blockSignals(True)
            self.navModeCombo.setCurrentText(
                "Windowed" if self._is_large_file else "Full Spectrum"
            )
            self.navModeCombo.blockSignals(False)
            self._current_page = 0
        self._updateNavigationStats()

        if self._nav_mode != "Full":
            self.loadSlice()  # Initial load (a page, or the time range)
        else:
            # Load everything (copy to RAM)
            raw_data = self._image()
            if self._time_dim == 0:
                data = np.array(raw_data[:, :], dtype=np.float32)
            else:
                data = np.array(raw_data[:, :].T, dtype=np.float32)

            # Handle infinities (NaN stays NaN naturally)
            data[np.isinf(data)] = np.nan

            # Use centralized display logic to honor persistent toggles (Bandpass, etc.)
            self._display_data(data, self._full_time_axis, self._full_freq_axis)

    def _guess_time_dim(self, data, header):
        """Which axis of the image (numpy order) is time: the one as long
        as the time axis; for a square image the header's (CTYPE), else the
        rows (View > Swap Time and Frequency corrects it)."""
        nt = len(self._full_time_axis)
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
        self._time_dim = 1 - self._time_dim
        self._stop_remote_reads()
        self._data_cache.clear()
        self._cached_bandpass_profile = None
        with self.wait_cursor():
            self._show_image()
        self.statusBar().showMessage(
            f"Time is now the image's {'rows' if self._time_dim == 0 else 'columns'}.", 5000
        )

    def _start_overview_stats(self):
        """Compute the overview from every sample in the background: each
        block of _lowres_skip time samples gives its mean (for the quick
        preview and file medians) and maximum (for the overview strip).
        Local files are read here; files on a server, on the server."""
        if self._overview_job is not None:
            self._overview_job.stopped = True
            self._overview_job = None
        skip = self._lowres_skip
        if skip <= 1:
            return  # the overview has every sample already
        image, time_dim, remote = self._image(), self._time_dim, self._remote is not None

        def work():
            if remote:
                return image.block_stats(time_dim, skip)
            return _local_block_stats(image, time_dim, skip)

        job = _RemoteRead(work, self)
        self._overview_job = job
        hdul, index = self.hdul, self._image_index

        def current():
            return job is self._overview_job and hdul is self.hdul and index == self._image_index

        def progress(done, total):
            if current() and total:
                self.statusBar().showMessage(
                    f"Building the overview from every sample... {100 * done // total}%"
                )

        def done(result):
            if not current():
                return
            self._overview_job = None
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

        with self.wait_cursor():
            new_hdul = None
            if remote is None:
                # (opened first: a file that can't be read leaves the one
                # shown, its masks and its undo history as they are)
                try:
                    new_hdul = fits.open(fileName, memmap=True)
                except Exception as e:
                    self.logger.error(f"Failed to open FITS file: {e}")
                    QMessageBox.critical(self, "Error", f"Failed to open FITS file:\n{e}")
                    return
            self.current_filename = fileName

            try:
                # Close previous file handle if any
                if hasattr(self, "hdul") and self.hdul:
                    self.hdul.close()
                self._data_cache.clear()
                self._stop_remote_reads()
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
                self.hdul = remote.hdul if remote else new_hdul
                # The spectrum: the first 2-D image, primary or extension
                self._spectrum_index = self._find_spectrum()
                if self._spectrum_index is None:
                    raise ValueError("the file has no 2-D image (dynamic spectrum)")
                self._image_index = self._spectrum_index
                header = self._header_of(self._spectrum_index)
                data_ref = self._image()

                # Capture metadata from all HDUs
                self._all_headers = []
                for i, hdu in enumerate(self.hdul):
                    name = hdu.name if hdu.name else f"HDU {i}"
                    self._all_headers.append((name, hdu.header))

                self._metadata = str(header)

                # Detect if large (e.g., > 10^7 pixels)
                num_pixels = data_ref.size
                self._is_large_file = num_pixels > 1e7 or data_ref.shape[0] > 10000

                time_axis = None
                freq_axis = None

                for hdu in self.hdul:
                    if hdu.name.upper() == "TIME_AXIS":
                        time_axis = self._table_time_axis(hdu)
                    elif hdu.name.upper() == "FREQ_AXIS":
                        freq_axis = hdu.data["FREQ_MHz"]
                    elif isinstance(hdu, fits.BinTableHDU):
                        cols = [c.name.upper() for c in hdu.columns]
                        if "TIME" in cols and time_axis is None:
                            t_raw = np.array(hdu.data["TIME"].tolist()).flatten()
                            if len(t_raw) > 0:
                                time_axis = self._interpret_time_column(
                                    t_raw, header
                                )
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
                        for ax_idx in [1, 2]:
                            ctype = str(header.get(f"CTYPE{ax_idx}", "")).upper()
                            cunit = str(header.get(f"CUNIT{ax_idx}", "")).lower()
                            crval = header.get(f"CRVAL{ax_idx}", 0)
                            cdelt = header.get(f"CDELT{ax_idx}", 1)
                            crpix = header.get(f"CRPIX{ax_idx}", 1)
                            naxis = naxis1 if ax_idx == 1 else naxis2
                            if "FREQ" in ctype:
                                freqs = crval + (np.arange(naxis) - (crpix - 1)) * cdelt
                                if cunit == "hz":
                                    freqs /= 1e6
                                elif cunit == "khz":
                                    freqs /= 1000.0
                                elif cunit == "ghz":
                                    freqs *= 1000.0
                                freq_axis = freqs
                            elif "TIME" in ctype:
                                start_mjd = 0
                                if crval > 1000000000:
                                    from astropy.time import Time as AstroTime
                                    start_mjd = AstroTime(crval, format="unix").mjd
                                elif crval > 2400000.5:
                                    start_mjd = crval - 2400000.5
                                elif 30000 < crval < 100000:
                                    start_mjd = crval
                                else:
                                    date_obs = header.get("DATE-OBS", None)
                                    if date_obs:
                                        from astropy.time import Time as AstroTime

                                        start_mjd = AstroTime(date_obs).mjd
                                cdelt_s = cdelt * (
                                    86400.0
                                    if cunit == "d"
                                    else 3600.0
                                    if cunit == "h"
                                    else 60.0
                                    if cunit == "min"
                                    else 0.001
                                    if cunit == "ms"
                                    else 1.0
                                )
                                time_axis = (start_mjd * 86400.0) + (
                                    np.arange(naxis) - (crpix - 1)
                                ) * cdelt_s
                    except Exception as wcs_err:
                        self.logger.warning(f"Axis extraction error: {wcs_err}")

                self._full_time_axis = time_axis
                self._full_freq_axis = freq_axis
                self._time_dim = self._guess_time_dim(data_ref, header)

                self._find_images()
                self._restore_masks()
                self._show_image(choose_mode=True)

                self.undo_stack.clear()
                self.redo_stack.clear()
                self._update_undo_actions()
                gc.collect()  # Force memory release
                self.statusBar().showMessage(
                    f"Loaded {os.path.basename(fileName)}", 5000
                )

            except Exception as e:
                if remote and type(e).__name__ == "Cancelled":
                    self._close_file()
                    self.statusBar().showMessage("Opening cancelled", 5000)
                    return
                self.logger.exception(f"Failed to open or plot FITS file: {e}")
                # (part read: nothing of it, or of the file before, is kept)
                self._close_file()
                QMessageBox.critical(self, "Error", f"Failed to open FITS file:\n{e}")

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
        same server is tried first (keys or agent; no password is passed)."""
        files = self._remote_files
        if files is not None and files.connection.is_connected():
            return files
        try:
            from ..remote import ConnectionDialog, RemoteFileCache, SSHConnection
            from ..remote.remote_files import RemoteFiles
        except ImportError as e:
            QMessageBox.warning(
                self,
                "Missing Dependency",
                f"Opening files on a server needs paramiko:\n\npip install paramiko\n\n{e}",
            )
            return None

        connection = None
        saved = os.environ.get("SOLARVIEWER_SSH")
        if saved and files is None:
            try:
                with self.wait_cursor():
                    connection = SSHConnection()
                    connection.connect(**json.loads(saved), timeout=15.0)
            except Exception as e:
                self.logger.info(f"Could not reuse SolarViewer's server: {e}")
                connection = None
        if connection is None:
            dialog = ConnectionDialog(self)
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
                f"Reading {posixpath.basename(path)} from the server...",
                lambda *_: RemoteFits(files, path),
            )
        except NotSliceable as e:
            # Only a whole copy will do
            size = files._stat(path)[1]
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
                    f"Downloading {posixpath.basename(path)}...",
                    lambda progress, cancelled: files.fetch(
                        [path],
                        progress=lambda done, total, *_: progress(done, total),
                        cancelled=cancelled,
                    )[path],
                    threaded=True,
                )
            except Exception as e:
                if type(e).__name__ != "Cancelled":
                    QMessageBox.critical(self, "Error", f"Failed to download:\n{e}")
                return
            self.openFile(local)
            return
        except Exception as e:
            if type(e).__name__ != "Cancelled":
                self.logger.exception(f"Failed to open {name}")
                QMessageBox.critical(self, "Error", f"Failed to open {name}:\n{e}")
            return
        self._server_work(
            f"Reading {posixpath.basename(path)} from the server...",
            lambda *_: self.openFile(name, remote=remote),
        )

    def _server_work(self, label, work, threaded=False, title="Server"):
        """``work(progress, cancelled)``, reading from the server, with a
        progress dialog; returns what it returns (raises Cancelled if the
        dialog is cancelled). Unless ``threaded``, it runs here, and the
        dialog updates as reads report progress."""
        from ..remote.remote_fits import reporting

        state = {"done": 0, "total": 0, "cancel": False}
        dialog = QProgressDialog(label, "Cancel", 0, 0, self)
        dialog.setWindowTitle(title)
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setMinimumDuration(500)
        dialog.setAutoReset(False)
        dialog.setAutoClose(False)
        dialog.canceled.connect(lambda: state.update(cancel=True))

        def update():
            if state["total"]:
                dialog.setMaximum(1000)
                dialog.setValue(int(1000 * state["done"] / state["total"]))
                dialog.setLabelText(
                    f"{label}\n{_size_text(state['done'])} of {_size_text(state['total'])}"
                )
            else:
                dialog.setValue(0)

        def progress(done, total):
            state.update(done=done, total=total)
            if threading.current_thread() is threading.main_thread():
                update()
                QApplication.processEvents()

        def cancelled():
            return state["cancel"]

        try:
            if not threaded:
                with reporting(progress, cancelled):
                    return work(progress, cancelled)
            result = {}

            def run():
                try:
                    with reporting(progress, cancelled):
                        result["value"] = work(progress, cancelled)
                except BaseException as e:
                    result["error"] = e

            thread = threading.Thread(target=run, daemon=True)
            thread.start()
            while thread.is_alive():
                update()
                QApplication.processEvents()
                thread.join(0.05)
            if "error" in result:
                raise result["error"]
            return result["value"]
        finally:
            dialog.close()
            dialog.deleteLater()

    def _read_page(self, page_idx, show=True):
        """Read page ``page_idx`` (from the file or the server) in the
        background; shown when read if it is still the current page, else
        kept for later."""
        if page_idx in self._remote_reads:
            return
        hdul = self.hdul
        read = _RemoteRead(lambda: self._get_slice_data(page_idx, downsample=1), self)
        self._remote_reads[page_idx] = read

        def finished():
            if self._remote_reads.get(page_idx) is read:
                del self._remote_reads[page_idx]
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
                for near in (page_idx + 1, page_idx - 1):
                    if 0 <= near < self._total_pages and near not in self._data_cache:
                        self._read_page(near)

        def failed(message):
            if hdul is self.hdul and page_idx == self._current_page:
                where = " from the server" if self._remote else ""
                self.statusBar().showMessage(f"Reading{where} failed: {message}")

        def progress(done_bytes, total):
            if hdul is self.hdul and page_idx == self._current_page and total:
                self.statusBar().showMessage(
                    f"Reading page {page_idx + 1}{' from the server' if self._remote else ''}... "
                    f"{_size_text(done_bytes)} of {_size_text(total)}"
                )

        read.done.connect(done)
        read.failed.connect(failed)
        read.progress.connect(progress)
        read.finished.connect(finished)
        read.start()

    def _stop_remote_reads(self, wait=False):
        if self._fill_job is not None:
            self._fill_job.stopped = True
            if wait:
                self._fill_job.wait(5000)
            self._fill_job = None
        if self._rfi_job is not None:
            self._rfi_job.stopped = True
            if wait:
                self._rfi_job.wait(5000)
            self._rfi_job = None
        if wait and self._overview_job is not None:
            self._overview_job.stopped = True
            self._overview_job.wait(5000)
        for read in list(self._remote_reads.values()):
            read.stopped = True
            if wait:
                read.wait(5000)
        self._remote_reads.clear()

    def _close_file(self):
        """Forget the open file (e.g. when opening it failed or was cancelled)."""
        self._stop_remote_reads()
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

        # Fetch authoritative palette from simpl_theme
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

        # We rely on the global stylesheet applied in __init__ via apply_theme.
        # Minimal dialog-specific tweaks for layout spacing
        dlg.setStyleSheet(
            dlg.styleSheet()
            + f"""
            QDialog {{ background-color: {palette["window"]}; }}
            QTableWidget {{ border: none; }}
        """
        )

        main_layout = QVBoxLayout(dlg)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Compact Aesthetic Source Info
        source_container = QWidget()
        source_layout = QHBoxLayout(source_container)
        source_layout.setContentsMargins(35, 20, 35, 0)

        source_label = QLabel(f"📄 {os.path.basename(self.current_filename)}")
        source_label.setStyleSheet(f"""
            font-size: 11pt; 
            font-weight: 600; 
            color: {palette["text_secondary"]};
            background: {palette["button"]};
            padding: 4px 12px;
            border-radius: 6px;
        """)
        source_layout.addWidget(source_label)
        source_layout.addStretch()
        main_layout.addWidget(source_container)

        # Search area - Styled precisely like SolarViewer
        search_widget = QWidget()
        search_layout = QHBoxLayout(search_widget)
        search_layout.setContentsMargins(35, 15, 35, 20)
        search_layout.setSpacing(15)

        search_label = QLabel("🔍 Search:")
        search_label.setStyleSheet(
            f"font-weight: 600; font-size: 11.5pt; color: {palette['text_secondary']};"
        )
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

        # Bottom Bar
        bottom_bar = QFrame()
        bottom_bar.setFixedHeight(95)
        bottom_bar.setStyleSheet(f"border-top: 1px solid {palette['border']};")
        b_layout = QHBoxLayout(bottom_bar)
        b_layout.setContentsMargins(35, 0, 35, 0)
        b_layout.setSpacing(20)

        copy_btn = QPushButton("📋 Copy to Clipboard")
        copy_btn.clicked.connect(self._copy_metadata_to_clipboard)
        b_layout.addWidget(copy_btn)

        b_layout.addStretch()

        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(140)
        close_btn.setStyleSheet("font-weight: bold;")
        close_btn.clicked.connect(dlg.close)
        b_layout.addWidget(close_btn)

        main_layout.addWidget(bottom_bar)
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

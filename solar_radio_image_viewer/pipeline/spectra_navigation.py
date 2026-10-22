"""
Moving through a dynamic spectrum: pages, ranges and the full file,
reading each part when it is shown, and going to a time.
"""

import logging
import numpy as np
from datetime import datetime, timedelta
from PyQt5.QtWidgets import (
    QMessageBox,
)
from PyQt5.QtCore import QTimer

from .spectra_common import _size_text
logger = logging.getLogger(__name__)


class SpectraNavigationMixin:
    """Pages, ranges and going to a time; reads the part shown.

    Mixed into :class:`MainWindow`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``_bin``, ``_cache_limit``, ``_data_cache``, ``_display_data``,
    ``_full_freq_axis``, ``_full_lowres_data``, ``_full_time_axis``,
    ``_image``, ``_is_large_file``, ``_lowres_skip``, ``_modeButtons``,
    ``_read_page``, ``_remote``, ``_server_work``, ``_shown_raw``,
    ``_stop_remote_reads``, ``_time_dim``, ``canvas``, ``controlScroll``,
    ``firstBtn``, ``gotoEdit``, ``hdul``, ``lastBtn``, ``logger``,
    ``minimap``, ``navGroup``, ``navModeCombo``, ``nextBtn``, ``pageLabel``,
    ``pagesWidget``, ``prevBtn``, ``rangeFromEdit``, ``rangeToEdit``,
    ``rangeWidget``, ``wait_cursor``
    """

    def _page_bounds(self, page_idx):
        """(start, stop) rows of the file shown as page ``page_idx``: a
        window (Windowed), the time range (Time Range) or all (Full)."""
        total = len(self._full_time_axis)
        if self._nav_mode == "Range" and self._range is not None:
            return self._range
        if self._nav_mode != "Windowed":
            return 0, total
        window = self._calculate_window_samples()
        start = page_idx * window
        return start, min(start + window, total)

    def _calculate_window_samples(self):
        """Safely calculate window samples from time axis and duration."""
        if self._full_time_axis is None or len(self._full_time_axis) < 2:
            return 1

        # The typical spacing (not just the first two samples': cadences
        # can be uneven, with gaps)
        sample = self._full_time_axis[:100_001]
        dt = float(np.median(np.abs(np.diff(sample))))
        if dt == 0:
            self.logger.warning(
                "Zero time delta detected in time axis. Using total samples as window."
            )
            # Return total samples to avoid infinite pages/invalid windowing
            return len(self._full_time_axis)

        try:
            val = (self._window_duration_min * 60.0) / dt
            if np.isinf(val) or np.isnan(val):
                return len(self._full_time_axis)
            return max(1, int(val))
        except (ZeroDivisionError, OverflowError, ValueError):
            return len(self._full_time_axis)

    # -------------------------- Paging & Navigation -----------------------------
    def _show_nav_mode(self):
        """The mode switch, and only the mode's controls."""
        value = {"Full": "Full Spectrum", "Windowed": "Windowed", "Range": "Time Range"}[self._nav_mode]
        self._modeButtons[value].setChecked(True)
        self.pagesWidget.setVisible(self._nav_mode == "Windowed")
        self.rangeWidget.setVisible(self._nav_mode == "Range")

    def _updateNavigationStats(self):
        """Calculate total pages and update UI labels."""
        if self._full_time_axis is None:
            self.navGroup.setEnabled(True)
            self.pageLabel.setText("1/1")
            self.firstBtn.setEnabled(False)
            self.prevBtn.setEnabled(False)
            self.nextBtn.setEnabled(False)
            self.lastBtn.setEnabled(False)

            self._show_nav_mode()
            return

        total_samples = len(self._full_time_axis)
        window_samples = self._calculate_window_samples()

        if self._nav_mode != "Windowed":
            self._total_pages = 1
        else:
            self._total_pages = max(1, int(np.ceil(total_samples / window_samples)))

        if self._current_page >= self._total_pages:
            self._current_page = self._total_pages - 1

        self.pageLabel.setText(f"{self._current_page + 1}/{self._total_pages}")

        # Keep navGroup enabled even for 1 page, so user can switch View Mode
        self.navGroup.setEnabled(True)

        self.firstBtn.setEnabled(self._current_page > 0)
        self.prevBtn.setEnabled(self._current_page > 0)
        self.nextBtn.setEnabled(self._current_page < self._total_pages - 1)
        self.lastBtn.setEnabled(self._current_page < self._total_pages - 1)

        self._show_nav_mode()

        # Feature 2: Update Minimap Highlight & Markers
        if self._full_time_axis is not None:
            total_samples = len(self._full_time_axis)
            window_samples = self._calculate_window_samples()

            if self._nav_mode == "Windowed":
                start_idx = self._current_page * window_samples
                end_idx = min(start_idx + window_samples, total_samples)

                start_pct = start_idx / total_samples
                end_pct = end_idx / total_samples
                self.minimap.update_view_percent(start_pct, end_pct)
                self.minimap.draw_page_markers(total_samples, window_samples)
            elif self._nav_mode == "Range" and self._range is not None:
                start_idx, end_idx = self._range
                self.minimap.update_view_percent(
                    start_idx / total_samples, end_idx / total_samples
                )
                self.minimap.draw_page_markers(total_samples, total_samples)
            else:
                self.minimap.update_view_percent(0, 1)
                self.minimap.draw_page_markers(total_samples, total_samples)

    def _get_slice_data(self, page_idx, downsample=1):
        """Helper to extract a data slice from the FITS handle."""
        if not self.hdul or self._full_time_axis is None:
            return None, None, None

        total_samples = len(self._full_time_axis)
        start_idx, end_idx = self._page_bounds(page_idx)

        if start_idx >= total_samples or start_idx < 0:
            return None, None, None

        raw_data = self._image()

        # Apply downsampling for fast preview if requested
        step = max(1, downsample)

        try:
            if self._time_dim == 0:
                # (time, freq)
                data_slice = raw_data[start_idx:end_idx:step, :]
            else:
                # (freq, time) -> (time, freq)
                data_slice = raw_data[:, start_idx:end_idx:step].T

            time_slice = self._full_time_axis[start_idx:end_idx:step]

            # Convert to RAM and handle infinities (NaN stays NaN naturally)
            data = np.array(data_slice, dtype=np.float32)
            data[np.isinf(data)] = np.nan
            return data, time_slice, self._full_freq_axis

        except Exception as e:
            if type(e).__name__ == "Cancelled":
                raise  # (a background read stopped)
            self.logger.error(f"Error extracting slice at page {page_idx}: {e}")
            return None, None, None

    def _get_fast_slice_data(self, page_idx):
        """Ultra-fast extraction of low-res data from RAM-cached overview."""
        if self._full_lowres_data is None or self._full_time_axis is None:
            return None, None, None

        start_idx, end_idx = self._page_bounds(page_idx)

        # The overview's rows (blocks of _lowres_skip samples) on the page,
        # each at its block's middle sample: a time for every row
        skip = self._lowres_skip
        total = len(self._full_time_axis)
        low_start = start_idx // skip
        low_end = min(-(-end_idx // skip), self._full_lowres_data.shape[0])

        # Robust slicing: _full_lowres_data is now guaranteed to be (time, freq)
        try:
            data_slice = self._full_lowres_data[low_start:low_end, :]
            middle = np.minimum(np.arange(low_start, low_end) * skip + skip // 2, total - 1)
            time_slice = self._full_time_axis[middle]
            return data_slice.copy(), time_slice, self._full_freq_axis

        except Exception as e:
            self.logger.error(f"Fast slice error: {e}")
            return None, None, None

    def loadSlice(self):
        """Extract and load the current data slice into the canvas with progressive rendering."""
        if not self.hdul:
            return

        page_to_load = self._current_page

        # 1. Instant Preview from RAM (Feature 5 Optimized)
        # We do this FIRST even if cached, to provide instantaneous visual feedback of the button press.
        if self._is_large_file and self._full_lowres_data is not None:
            data_fast, time_fast, freq_fast = self._get_fast_slice_data(page_to_load)
            if data_fast is not None:
                self._display_data(data_fast, time_fast, freq_fast, fast=True)
                # Force Qt to repaint the fast preview BEFORE we schedule the high-res update
                # QtWidgets.QApplication.processEvents()

        # 2. Check Cache
        if page_to_load in self._data_cache:
            data, time_axis, freq_axis = self._data_cache[page_to_load]
            self.logger.info(f"Loaded page {page_to_load} from cache")
            # Schedule the high-res update to allow the fast-preview to be drawn first
            self._pending_page = page_to_load
            QTimer.singleShot(
                10, lambda: self._completeLoad(page_to_load, data, time_axis, freq_axis)
            )
            return

        # 3. Schedule Full-res Data (Async Load from file)
        self._pending_page = page_to_load
        QTimer.singleShot(10, lambda: self._loadFullRes(page_to_load))

    def _completeLoad(self, page_idx, data, time_axis, freq_axis):
        """Final display of high-res data for both cache hits and file loads."""
        if (
            not self.hdul
            or page_idx != self._current_page
            or page_idx != self._pending_page
        ):
            return
        with self.wait_cursor():
            self._display_data(data, time_axis, freq_axis)
            self._pending_page = -1  # Completed

    def _loadFullRes(self, page_idx):
        """Perform the actual high-resolution load scheduled by loadSlice."""
        # Ensure we are still on the same page and not cleared
        if (
            not self.hdul
            or page_idx != self._current_page
            or page_idx != self._pending_page
        ):
            return

        # In the background (the preview shows meanwhile; the window stays
        # responsive however slow the disk or the server)
        self._read_page(page_idx)

    def _read_all(self, raw_data, progress, cancelled):
        """All of a local image as (time, freq) float32, read in blocks (so
        it reports progress and can stop)."""
        from ..remote.remote_fits import Cancelled

        time_major = self._time_dim == 0
        nt = raw_data.shape[0] if time_major else raw_data.shape[1]
        nf = raw_data.shape[1] if time_major else raw_data.shape[0]
        data = np.empty((nt, nf), np.float32)
        rows = max(1, 16_000_000 // nf)
        for start in range(0, nt, rows):
            if cancelled():
                raise Cancelled()
            stop = min(start + rows, nt)
            block = raw_data[start:stop, :] if time_major else raw_data[:, start:stop].T
            data[start:stop] = block
            progress(stop * nf * 4, nt * nf * 4)
        data[np.isinf(data)] = np.nan
        return data

    def _cache_page(self, page_idx, data, time_axis, freq_axis):
        self._data_cache[page_idx] = (data, time_axis, freq_axis)
        if len(self._data_cache) > self._cache_limit:
            keys = sorted(self._data_cache.keys())
            furthest = (
                keys[0]
                if abs(keys[0] - page_idx) > abs(keys[-1] - page_idx)
                else keys[-1]
            )
            del self._data_cache[furthest]

    def _file_ranges(self, ixmin, ixmax, iymin, iymax):
        """The file's (t0, t1, f0, f1) under samples ixmin..ixmax (time) and
        iymin..iymax (frequency) of the data shown (averaged or not)."""
        raw_time = self._shown_raw[1]
        time_bin, freq_bin = self._bin
        r0 = ixmin * time_bin
        r1 = min((ixmax + 1) * time_bin, len(raw_time))
        full = self._full_time_axis
        t0 = int(np.searchsorted(full, raw_time[r0], "left"))
        t1 = int(np.searchsorted(full, raw_time[r1 - 1], "right"))
        nf = self._shown_raw[0].shape[1]
        return t0, max(t1, t0 + 1), iymin * freq_bin, min((iymax + 1) * freq_bin, nf)

    def onNavModeChanged(self, mode):
        if not self.hdul:
            return
        self._stop_remote_reads()  # (reads of the previous mode's pages)
        previous = self._page_bounds(self._current_page) if self._full_time_axis is not None else None
        self._nav_mode = "Windowed" if "Windowed" in mode else "Range" if "Range" in mode else "Full"

        if self._nav_mode == "Range":
            # From the page shown (the first window on the first visit)
            if self._range is None:
                self._range = previous if previous and previous != (0, len(self._full_time_axis)) else (
                    0, min(self._calculate_window_samples(), len(self._full_time_axis))
                )
            self._set_range_edits(*self._range)
            self._show_range(*self._range)
            self._reveal_range_controls()
            return

        if self._nav_mode == "Full":
            # Warning for very large files
            if self._is_large_file:
                reply = QMessageBox.warning(
                    self,
                    "Large Data Warning",
                    "Loading the full spectrum for a very large file may consume significant memory. Continue?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply == QMessageBox.No:
                    self.navModeCombo.blockSignals(True)
                    self.navModeCombo.setCurrentText("Windowed")
                    self.navModeCombo.blockSignals(False)
                    self._nav_mode = "Windowed"
                    return

            # Load full data into RAM (subject to memory limits)
            raw_data = self._image()
            if self._remote:
                try:
                    data = self._server_work(
                        "Reading the whole spectrum from the server...",
                        lambda *_: raw_data[:, :],
                    )
                except Exception as e:
                    if type(e).__name__ != "Cancelled":
                        QMessageBox.critical(self, "Error", f"Failed to read the spectrum:\n{e}")
                    self.navModeCombo.blockSignals(True)
                    self.navModeCombo.setCurrentText("Windowed")
                    self.navModeCombo.blockSignals(False)
                    self._nav_mode = "Windowed"
                    return
                if self._time_dim != 0:
                    data = np.ascontiguousarray(data.T)
                data[np.isinf(data)] = np.nan
                self._display_data(data, self._full_time_axis, self._full_freq_axis)
                return
            if self._is_large_file:
                try:
                    data = self._server_work(
                        "Reading the whole spectrum...",
                        lambda progress, cancelled: self._read_all(raw_data, progress, cancelled),
                        threaded=True,
                        title="Loading",
                    )
                except Exception as e:
                    if type(e).__name__ != "Cancelled":
                        QMessageBox.critical(self, "Error", f"Failed to read the spectrum:\n{e}")
                    self.navModeCombo.setCurrentText("Windowed")
                    return
                self._display_data(data, self._full_time_axis, self._full_freq_axis)
                return
            with self.wait_cursor():
                if self._time_dim == 0:
                    data = np.array(raw_data, dtype=np.float32)
                else:
                    data = np.array(raw_data.T, dtype=np.float32)
                self._display_data(data, self._full_time_axis, self._full_freq_axis)
            return

        # Switching/Returning to Windowed mode
        self._data_cache.clear()
        self._current_page = 0
        with self.wait_cursor():
            self.loadSlice()

    def showRangeControls(self):
        """Time Range mode, with its From/To boxes in view and focused."""
        if self._full_time_axis is None:
            QMessageBox.information(self, "Info", "No data loaded.")
            return
        if self._nav_mode != "Range":
            self.navModeCombo.setCurrentText("Time Range")
        self._reveal_range_controls()

    def _reveal_range_controls(self):
        self.navGroup.unfold()
        QTimer.singleShot(0, lambda: self.controlScroll.ensureWidgetVisible(self.rangeWidget))
        self.rangeFromEdit.setFocus()
        self.rangeFromEdit.selectAll()

    def _time_text(self, index):
        """UTC of the file's sample ``index``, as typed in the range boxes."""
        when = datetime(1858, 11, 17) + timedelta(seconds=float(self._full_time_axis[index]))
        return when.strftime("%H:%M:%S.%f")[:-4]

    def _set_range_edits(self, start, stop):
        self.rangeFromEdit.setText(self._time_text(start))
        self.rangeToEdit.setText(self._time_text(stop - 1))

    def onShowRange(self):
        """Show the data between the times typed (Time Range)."""
        if self._full_time_axis is None:
            return
        try:
            t0 = self._parse_time(self.rangeFromEdit.text())
            t1 = self._parse_time(self.rangeToEdit.text())
        except Exception as e:
            self.statusBar().showMessage(f"Not a time: {e}", 5000)
            return
        if t1 < t0:
            t0, t1 = t1, t0
        times = self._full_time_axis
        start = int(np.searchsorted(times, t0, "left"))
        stop = int(np.searchsorted(times, t1, "right"))
        if stop <= start:
            self.statusBar().showMessage("No data between those times.", 5000)
            return
        if self._nav_mode != "Range":
            self._range = (start, stop)
            self.navModeCombo.setCurrentText("Time Range")  # (shows it)
            return
        self._show_range(start, stop)

    def _show_range(self, start, stop):
        """Show rows ``start``..``stop`` of the file (Time Range mode)."""
        nf = len(self._full_freq_axis) if self._full_freq_axis is not None else 1
        values = (stop - start) * nf
        if values > 50_000_000:
            reply = QMessageBox.warning(
                self,
                "Large Range",
                f"This range holds {values / 1e6:.0f} million values "
                f"({_size_text(values * 4)} in memory). Show it?\n\n"
                "(Averaging time samples keeps what is drawn small.)",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        self._range = (start, stop)
        self._set_range_edits(start, stop)
        self._stop_remote_reads()
        self._data_cache.clear()
        self._current_page = 0
        self._updateNavigationStats()
        with self.wait_cursor():
            self.loadSlice()

    def onNavDurationChanged(self, val):
        if not self.hdul:
            return
        self._window_duration_min = val
        self._current_page = 0  # Reset to start when duration changes

        # Invalidate cache
        with self.wait_cursor():
            self._data_cache.clear()

            # Recalculate total pages based on new duration
            if self._full_time_axis is not None:
                total_samples = len(self._full_time_axis)
                window_samples = self._calculate_window_samples()
                if window_samples > 0:
                    self._total_pages = (
                        total_samples + window_samples - 1
                    ) // window_samples
                else:
                    self._total_pages = 1

            self.loadSlice()
            self._updateNavigationStats()  # Ensure button states and minimap rectangle update

    def onPrevPage(self):
        if self._current_page > 0:
            self._current_page -= 1
            with self.wait_cursor():
                self.loadSlice()

    def onNextPage(self):
        if self._current_page < self._total_pages - 1:
            self._current_page += 1
            with self.wait_cursor():
                self.loadSlice()

    def onFirstPage(self):
        if self._current_page != 0:
            self._current_page = 0
            with self.wait_cursor():
                self.loadSlice()

    def onLastPage(self):
        if self._current_page != self._total_pages - 1:
            self._current_page = self._total_pages - 1
            with self.wait_cursor():
                self.loadSlice()

    def onMinimapSeek(self, x_data):
        """Handle seek request from minimap click/drag."""
        if self._full_time_axis is None:
            return

        with self.wait_cursor():
            total_samples = len(self._full_time_axis)

            # x_data is coordinate in the axes
            xlim = self.minimap.ax.get_xlim()
            pct = (x_data - xlim[0]) / (xlim[1] - xlim[0])
            pct = max(0, min(0.999, pct))

            # Map percentage directly to sample index
            target_idx = int(pct * total_samples)

            if self._nav_mode == "Range" and self._range is not None:
                # The same range length, centered on the click
                length = self._range[1] - self._range[0]
                start = max(0, min(total_samples - length, target_idx - length // 2))
                self._show_range(start, start + length)
                return

            # Determine which page contains this sample
            window_samples = self._calculate_window_samples()

            new_page = target_idx // window_samples
            if new_page >= self._total_pages:
                new_page = self._total_pages - 1

            if new_page != self._current_page:
                self._current_page = new_page
                self.loadSlice()
            else:
                # Snap the minimap rectangle back to the current page position
                start_idx = self._current_page * window_samples
                end_idx = min(start_idx + window_samples, total_samples)
                self.minimap.update_view_percent(
                    start_idx / total_samples, end_idx / total_samples
                )

    def _parse_time(self, text):
        """MJD seconds of ``text``: a UTC date and time, or a time on the
        date of the data (the next day if that is before the start)."""
        from astropy.time import Time as AstroTime

        text = text.strip()
        start = float(self._full_time_axis[0])
        if "-" not in text:  # a time of day
            parts = [float(p) for p in text.split(":")]
            if not 2 <= len(parts) <= 3:
                raise ValueError("expected HH:MM or HH:MM:SS")
            day = np.floor(start / 86400.0) * 86400.0
            seconds = day + parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) > 2 else 0)
            if seconds < start and seconds + 86400 <= float(self._full_time_axis[-1]):
                seconds += 86400
            return seconds
        return AstroTime(text.replace("T", " "), format="iso", scale="utc").mjd * 86400.0

    def onGoToTime(self):
        """Show the page holding the time typed, centered on it and marked."""
        if self._full_time_axis is None:
            return
        try:
            target = self._parse_time(self.gotoEdit.text())
        except Exception as e:
            self.statusBar().showMessage(f"Not a time: {e}", 5000)
            return
        times = self._full_time_axis
        if not times[0] <= target <= times[-1]:
            self.statusBar().showMessage("That time is outside the data.", 5000)
            return
        index = int(np.searchsorted(times, target))
        if self._nav_mode == "Range" and self._range is not None:
            if not self._range[0] <= index < self._range[1]:
                self.statusBar().showMessage(
                    "That time is outside the range shown (change From/To).", 5000
                )
                return
        page = index // self._calculate_window_samples() if self._nav_mode == "Windowed" else 0
        page = min(page, self._total_pages - 1)
        if page != self._current_page:
            self._goto_time = target  # centered once shown (_display_data)
            self._current_page = page
            self.loadSlice()
        else:
            self._center_on_time(target)

    def _center_on_time(self, target):
        self.canvas.center_on(target)
        self.canvas.mark_time(target)

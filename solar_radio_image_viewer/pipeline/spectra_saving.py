"""
Saving cleaned dynamic spectra: the part shown or the whole file (page by
page), locally or on the server, and comparing with the original.
"""

import logging
import os
import posixpath
import numpy as np
import numpy.ma as ma
from astropy.io import fits
from PyQt5.QtWidgets import (
    QFileDialog,
    QMessageBox,
    QInputDialog,
)
import matplotlib.pyplot as plt

from .spectra_common import rebin
logger = logging.getLogger(__name__)


class SpectraSavingMixin:
    """Saves cleaned data and compares or reverts it.

    Mixed into :class:`MainWindow`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``_NOT_COPIED``, ``_apply_bandpass_to_data``, ``_apply_keeps``,
    ``_apply_masks``, ``_bandpass_method``, ``_bandpass_scope``, ``_bin``,
    ``_binned``, ``_binned_axes``, ``_calculate_window_samples``, ``_do``,
    ``_freq_axis``, ``_full_freq_axis``, ``_full_time_axis``,
    ``_global_masked_freq_indices``, ``_header_of``, ``_image``,
    ``_image_index``, ``_is_bandpass_enabled``, ``_keeps``, ``_masked``,
    ``_masks``, ``_nav_mode``, ``_noise_image``, ``_noise_rows``,
    ``_original_data``, ``_previewing``, ``_remote``, ``_rfi_enabled``,
    ``_rfi_options``, ``_rfi_settings``, ``_server``, ``_server_work``,
    ``_time_axis``, ``_time_dim``, ``_total_pages``, ``_unmodified``,
    ``canvas``, ``current_filename``, ``hdul``, ``wait_cursor``
    """

    def _ask_save_extent(self):
        """"file" (the whole file, cleaned page by page) or "shown" (the
        data shown), as the user chooses; None if cancelled. Without pages
        (Full Spectrum) what is shown is the whole file."""
        if self._nav_mode == "Full" or (self._nav_mode == "Windowed" and self._total_pages <= 1):
            return "shown"
        box = QMessageBox(self)
        box.setWindowTitle("Save Cleaned FITS")
        box.setText("Save the whole file, or only what is shown?")
        box.setInformativeText(
            "The whole file is cleaned and saved page by page (masks, "
            "averaging and normalisation as shown), never all in memory."
        )
        whole = box.addButton("Whole File", QMessageBox.AcceptRole)
        shown = box.addButton(
            "This Range" if self._nav_mode == "Range" else "This Page", QMessageBox.AcceptRole
        )
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(whole)
        box.exec_()
        return {whole: "file", shown: "shown"}.get(box.clickedButton())

    def _save_to(self, fileName, extent):
        """Write the cleaned data (``extent`` "file" or "shown") to
        ``fileName``; raises Cancelled if the user stops it."""
        if extent == "shown" and self._previewing():
            raise ValueError("the page is still loading: save it once it shows in full")
        if extent == "shown":
            with self.wait_cursor():
                self._write_cleaned(fileName)
            return
        self._server_work(
            "Saving the whole file...",
            lambda progress, cancelled: self._write_whole(fileName, progress, cancelled),
            threaded=True,
            title="Save",
        )

    def saveCleanedData(self):
        if self._original_data is None:
            QMessageBox.information(self, "Info", "No data to save.")
            return
        extent = self._ask_save_extent()
        if extent is None:
            return
        fileName, _ = QFileDialog.getSaveFileName(
            self, "Save Cleaned FITS", "", "FITS Files (*.fits)"
        )
        if not fileName:
            return
        try:
            self._save_to(fileName, extent)
            self.statusBar().showMessage(f"Saved cleaned data to {fileName}", 5000)
        except Exception as e:
            if type(e).__name__ != "Cancelled":
                QMessageBox.critical(self, "Error", f"Failed to save FITS:\n{e}")

    def saveCleanedToServer(self):
        """Save the data shown to a folder on the server (the opened file's
        folder by default)."""
        if self._original_data is None:
            QMessageBox.information(self, "Info", "No data to save.")
            return
        extent = self._ask_save_extent()
        if extent is None:
            return
        files = self._server()
        if files is None:
            return
        from ..remote import RemoteFileBrowser

        if self._remote is not None:
            folder = posixpath.dirname(self._remote.path)
            stem = os.path.splitext(posixpath.basename(self._remote.path))[0]
            RemoteFileBrowser._last_paths[files.host] = folder
        else:
            stem = os.path.splitext(os.path.basename(self.current_filename))[0] or "spectrum"
        picked = []
        browser = RemoteFileBrowser(files.connection, cache=files.cache, parent=self, pick="folder")
        browser.pathPicked.connect(picked.append)
        browser.exec_()
        if not picked:
            return
        name, ok = QInputDialog.getText(
            self, "Save to Server", f"File name in {picked[0]}:", text=f"{stem}_cleaned.fits"
        )
        name = name.strip()
        if not ok or not name:
            return
        path = posixpath.join(picked[0], name)
        if self._remote is not None and path == self._remote.path:
            QMessageBox.warning(self, "Save to Server", "That is the file opened: choose another name.")
            return
        if files.exists(path):
            reply = QMessageBox.question(
                self, "Save to Server", f"{path} exists. Replace it?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        import tempfile

        local = os.path.join(tempfile.mkdtemp(prefix="solarviewer-"), name)
        try:
            self._save_to(local, extent)
            self._server_work(
                f"Uploading {name}...",
                lambda progress, cancelled: files.upload(local, path, progress, cancelled),
                threaded=True,
            )
            self.statusBar().showMessage(f"Saved cleaned data to {files.host}:{path}", 8000)
        except Exception as e:
            if type(e).__name__ != "Cancelled":
                QMessageBox.critical(self, "Error", f"Failed to save to the server:\n{e}")
        finally:
            import shutil

            shutil.rmtree(os.path.dirname(local), ignore_errors=True)

    def _cleaned_header(self, shape, time_axis, freq_axis):
        """Header of a cleaned file of ``shape`` (time, freq): the original's
        cards, a linear WCS of the axes, and what was done."""
        header = fits.Header()
        header["SIMPLE"] = True
        header["BITPIX"] = -32
        header["NAXIS"] = 2
        header["NAXIS1"] = shape[1]
        header["NAXIS2"] = shape[0]
        header["EXTEND"] = True
        if time_axis is not None and freq_axis is not None:
            nt, nf = len(time_axis), len(freq_axis)
            header["CTYPE1"] = "FREQ"
            header["CUNIT1"] = "MHz"
            header["CRPIX1"] = 1.0
            header["CRVAL1"] = float(freq_axis[0])
            header["CDELT1"] = float((freq_axis[-1] - freq_axis[0]) / (nf - 1)) if nf > 1 else 1.0
            header["CTYPE2"] = "TIME"
            header["CUNIT2"] = "d"
            header["CRPIX2"] = 1.0
            header["CRVAL2"] = float(time_axis[0]) / 86400.0  # MJD
            header["CDELT2"] = (
                float((time_axis[-1] - time_axis[0]) / (nt - 1)) / 86400.0 if nt > 1 else 1.0
            )
        source = self._header_of(self._image_index) if self.hdul else fits.Header()
        for card in source.cards:
            if card.keyword in ("COMMENT", "HISTORY", ""):
                header.append(card)
            elif not self._NOT_COPIED.match(card.keyword) and card.keyword not in header:
                header.append(card)
        header.add_history("Cleaned with the SolarViewer Dynamic Spectrum Viewer:")
        if self._masks or self._global_masked_freq_indices:
            header.add_history(
                f"  {len(self._masks)} masked regions, "
                f"{len(self._global_masked_freq_indices)} channels masked at all times"
            )
        if self._rfi_enabled:
            st = self._rfi_settings()
            used = [
                name for name, on in (
                    ("ConvRFI (Zhang et al. 2023)", st.convrfi),
                    ("SumThreshold (Offringa et al. 2010)", st.sumthreshold),
                    ("noise ratio", st.noise and self._noise_image() is not None),
                ) if on
            ]
            header.add_history(f"  RFI flagged automatically: {', '.join(used)}")
            def limit(share):
                return f"> {share:.0%}" if share < 1 else "off"

            header.add_history(f"  sensitivity {st.sensitivity:.2f}, fill gaps {st.fill_gaps or 'off'}")
            if self._rfi_options["fill"] != "blank":
                how = {"median": "median of good neighbours", "gaussian": "Gaussian-weighted mean"}
                header.add_history(
                    f"  flagged pixels filled: {how[self._rfi_options['fill']]}, "
                    f"reach {self._rfi_options['reach']} px"
                )
            header.add_history(
                f"  whole channels {limit(st.channel_limit)}, whole times {limit(st.time_limit)}"
                + (f", {len(self._keeps)} kept regions" if self._keeps else "")
            )
        if self._bin != (1, 1):
            header.add_history(f"  averaged {self._bin[0]} time samples x {self._bin[1]} channels")
        if self._is_bandpass_enabled:
            header.add_history(
                f"  {'divided by' if self._bandpass_method == 'divide' else 'minus'} the "
                f"median of each channel over {'each page' if self._bandpass_scope == 'page' else 'the file'}"
            )
        return header

    def _axis_tables(self, time_axis, freq_axis):
        """TIME_AXIS (TIME_MJD, UTC) and FREQ_AXIS (FREQ_MHz) tables."""
        tables = []
        if time_axis is not None:
            mjd = np.asarray(time_axis, np.float64) / 86400.0
            try:
                from astropy.time import Time as AstroTime

                utc = AstroTime(mjd, format="mjd", scale="utc").iso
            except Exception:
                utc = np.array([""] * len(mjd))
            tables.append(
                fits.BinTableHDU.from_columns(
                    [
                        fits.Column(name="TIME_MJD", format="D", array=mjd),
                        fits.Column(name="UTC", format="23A", array=np.asarray(utc)),
                    ],
                    name="TIME_AXIS",
                )
            )
        if freq_axis is not None:
            tables.append(
                fits.BinTableHDU.from_columns(
                    [fits.Column(name="FREQ_MHz", format="D", array=np.asarray(freq_axis, np.float64))],
                    name="FREQ_AXIS",
                )
            )
        return tables

    def _write_cleaned(self, fileName):
        """Write the data shown (and its axes) to FITS file ``fileName``."""
        data = np.asarray(self._original_data, np.float32)
        header = self._cleaned_header(data.shape, self._time_axis, self._freq_axis)
        hdul = fits.HDUList(
            [fits.PrimaryHDU(data, header)] + self._axis_tables(self._time_axis, self._freq_axis)
        )
        hdul.writeto(fileName, overwrite=True)

    def _cleaning_chunks(self):
        """(start, stop) rows of the file cleaned together when saving it:
        the pages (as shown: averaging and per-page medians match), or
        without pages blocks of ~16 million values."""
        total = len(self._full_time_axis)
        if self._nav_mode == "Windowed":
            window = self._calculate_window_samples()
            origin = getattr(self, "_page_origin", 0)
            return [(max(0, s), min(s + window, total)) for s in range(origin, total, window)]
        else:
            nf = len(self._full_freq_axis) if self._full_freq_axis is not None else 1
            time_bin = self._bin[0]
            window = max(time_bin, (16_000_000 // max(nf, 1)) // time_bin * time_bin)
        return [(s, min(s + window, total)) for s in range(0, total, window)]

    def _write_whole(self, fileName, progress, cancelled):
        """Write the whole file, cleaned as shown, to ``fileName``: read,
        cleaned and written page by page (runs in a background thread)."""
        image = self._image()
        full_time = self._full_time_axis
        total = len(full_time)
        time_major = self._time_dim == 0
        chunks = self._cleaning_chunks()
        time_bin, freq_bin = self._bin
        time_out = np.concatenate([rebin(full_time[s:e].astype(np.float64), time_bin, 0) for s, e in chunks])
        _, freq_out = self._binned_axes(None, self._full_freq_axis)
        nf = image.shape[1] if time_major else image.shape[0]
        nf_out = -(-nf // freq_bin)
        header = self._cleaned_header((len(time_out), nf_out), time_out, freq_out)

        partial = f"{fileName}.part"
        if os.path.exists(partial):
            os.remove(partial)
        stream = fits.StreamingHDU(partial, header)
        try:
            self._stream_chunks(stream, image, chunks, time_major, nf, total, progress, cancelled)
        except BaseException:
            stream.close()
            os.remove(partial)
            raise
        stream.close()
        with fits.open(partial, mode="append") as hdul:
            for table in self._axis_tables(time_out, freq_out):
                hdul.append(table)
        os.replace(partial, fileName)

    def _stream_chunks(self, stream, image, chunks, time_major, nf, total, progress, cancelled):
        """Clean the file's rows ``chunks`` and write them to ``stream``."""
        from ..remote.remote_fits import Cancelled, reporting

        full_time = self._full_time_axis
        settings = self._rfi_settings() if self._rfi_enabled else None
        noise = self._noise_image() if settings is not None and settings.noise else None
        fill_mode = self._rfi_options["fill"]
        done = 0
        for s, e in chunks:
            if cancelled():
                raise Cancelled()
            key = np.s_[s:e, :] if time_major else np.s_[:, s:e]
            # (inner reads only stop when cancelled: progress is by page)
            with reporting(None, cancelled):
                raw = image.read(key, cache=False) if hasattr(image, "read") else image[key]
            data = np.array(raw if time_major else raw.T, dtype=np.float32)
            data[np.isinf(data)] = np.nan
            flags = None
            if settings is not None:
                from .rfi_flagging import flag_page

                finite = data[np.isfinite(data)]
                if not finite.size or float(finite.min()) >= 0:
                    with reporting(None, cancelled):
                        stddev = self._noise_rows(noise, s, e, cache=False) if noise is not None else None
                    flags = self._apply_keeps(flag_page(data, settings, stddev), full_time[s:e])
                    data[flags] = np.nan
            data = self._apply_masks(data, full_time[s:e])
            if settings is not None and fill_mode != "blank" and flags is not None and flags.any():
                from .rfi_flagging import fill_flagged

                fill = flags & ~self._masked(data.shape, full_time[s:e])
                data = fill_flagged(data, fill, fill_mode, self._rfi_options["reach"])
            data = self._binned(data)
            if self._is_bandpass_enabled:
                data = self._apply_bandpass_to_data(data)
            stream.write(np.ascontiguousarray(data, dtype=">f4"))
            done += e - s
            progress(done * nf * 4, total * nf * 4)

    def compareOriginalCleaned(self):
        original = self._unmodified()
        if original is None or self._original_data is None:
            QMessageBox.information(self, "Info", "No data loaded.")
            return
        with self.wait_cursor():
            fig, axes = plt.subplots(1, 2, figsize=(12, 5), sharey=True)
        axes[0].set_title("Original Unmodified")
        axes[1].set_title("Current Cleaned")
        orig_ma = ma.masked_invalid(original)
        cle_ma = ma.masked_invalid(self._original_data)

        # Apply the same scaling as the current view
        if self.canvas._smart_scale == "0-100%":
            orig_vals = orig_ma.compressed()
            cle_vals = cle_ma.compressed()
            orig_vmin, orig_vmax = orig_vals.min(), orig_vals.max()
            cle_vmin, cle_vmax = cle_vals.min(), cle_vals.max()
        elif self.canvas._smart_scale == "0.1-99.9%":
            orig_vals = orig_ma.compressed()
            cle_vals = cle_ma.compressed()
            orig_vmin = np.percentile(orig_vals, 0.1)
            orig_vmax = np.percentile(orig_vals, 99.9)
            cle_vmin = np.percentile(cle_vals, 0.1)
            cle_vmax = np.percentile(cle_vals, 99.9)
        elif self.canvas._smart_scale == "0.5-99.5%":
            orig_vals = orig_ma.compressed()
            cle_vals = cle_ma.compressed()
            orig_vmin = np.percentile(orig_vals, 0.5)
            orig_vmax = np.percentile(orig_vals, 99.5)
            cle_vmin = np.percentile(cle_vals, 0.5)
            cle_vmax = np.percentile(cle_vals, 99.5)
        else:  # "1-99%"
            orig_vals = orig_ma.compressed()
            cle_vals = cle_ma.compressed()
            orig_vmin = np.percentile(orig_vals, 1)
            orig_vmax = np.percentile(orig_vals, 99)
            cle_vmin = np.percentile(cle_vals, 1)
            cle_vmax = np.percentile(cle_vals, 99)

        norm1 = self.canvas.get_normalization(orig_vmin, orig_vmax)
        norm2 = self.canvas.get_normalization(cle_vmin, cle_vmax)
        im1 = axes[0].imshow(
            orig_ma.T, origin="lower", aspect="auto", norm=norm1, cmap=self.canvas._cmap
        )
        fig.colorbar(im1, ax=axes[0], label="")
        im2 = axes[1].imshow(
            cle_ma.T, origin="lower", aspect="auto", norm=norm2, cmap=self.canvas._cmap
        )
        fig.colorbar(im2, ax=axes[1], label="")
        axes[0].set_xlabel("Time Index")
        axes[1].set_xlabel("Time Index")
        axes[0].set_ylabel("Frequency Index")
        axes[1].set_ylabel("Frequency Index")
        fig.suptitle("Original vs Cleaned")
        fig.tight_layout()
        fig.show()

    def revertToOriginal(self):
        """Remove all masks, drawn and extended, and the Keep regions (Undo
        brings them back)."""
        if not self._masks and not self._global_masked_freq_indices and not self._keeps:
            self.statusBar().showMessage("There are no masks.", 5000)
            return
        self._do(("clear", (list(self._masks), frozenset(self._global_masked_freq_indices), list(self._keeps))))
        self.statusBar().showMessage(
            "Cleared all masks and Keep regions (Ctrl+Z restores).", 5000
        )

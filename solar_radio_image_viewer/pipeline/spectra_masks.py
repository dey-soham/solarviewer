"""
Masks and RFI flags in the dynamic spectrum viewer: drawing, keeping and
extending masks, saving them with the file, and automatic RFI flagging.
"""

import logging
import os
import json
import numpy as np
from PyQt5.QtWidgets import (
    QVBoxLayout,
    QFileDialog,
    QMessageBox,
    QLabel,
    QComboBox,
    QFormLayout,
    QDoubleSpinBox,
    QSpinBox,
    QCheckBox,
    QDialogButtonBox,
    QGroupBox,
    QDialog,
)

from .spectra_common import _RemoteRead
logger = logging.getLogger(__name__)


class SpectraMasksMixin:
    """Masks, keep regions and RFI flags.

    Mixed into :class:`MainWindow`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``_NOISE_NAMES``, ``_binned``, ``_do``, ``_file_ranges``,
    ``_full_freq_axis``, ``_full_time_axis``, ``_image``, ``_image_data``,
    ``_image_index``, ``_images``, ``_original_data``, ``_previewing``,
    ``_redisplay``, ``_remote``, ``_rfi_cache``, ``_rfi_options``,
    ``_shown_raw``, ``_spectrum_index``, ``_time_dim``, ``canvas``,
    ``clearMaskBtn``, ``crossBtn``, ``current_filename``, ``deleteRegionAct``,
    ``driftBtn``, ``extendAllAct``, ``extendMaskBtn``, ``extendSelectedAct``,
    ``hdul``, ``keepBtn``, ``logger``, ``maskSummary``,
    ``revertToOriginal``, ``rfiSlider``, ``roiButton``, ``_update_status_summary``
    """

    def _masks_file(self):
        """Where the open file's masks are kept between sessions (by the
        file: path, size and time; on a server, its host too)."""
        import hashlib

        if self._remote is not None:
            key = os.path.basename(self._remote._dir)
        else:
            try:
                st = os.stat(self.current_filename)
            except OSError:
                return None
            key = hashlib.sha1(
                f"{os.path.realpath(self.current_filename)}\0{st.st_size}\0{st.st_mtime}".encode()
            ).hexdigest()
        folder = os.path.join(os.path.expanduser("~"), ".cache", "solarviewer", "dynspec_masks")
        return os.path.join(folder, f"{key}.json")

    def _autosave_masks(self):
        """Keep the masks for when the file is opened again."""
        path = self._masks_file()
        if path is None:
            return
        try:
            if not self._masks and not self._global_masked_freq_indices and not self._keeps:
                if os.path.exists(path):
                    os.remove(path)
                return
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".part", "w") as f:
                json.dump(
                    {
                        "masks": [list(map(int, m)) for m in self._masks],
                        "channels": sorted(int(c) for c in self._global_masked_freq_indices),
                        "keeps": [list(map(int, k)) for k in self._keeps],
                    },
                    f,
                )
            os.replace(path + ".part", path)
        except OSError as e:
            self.logger.warning(f"Masks not kept: {e}")

    def _restore_masks(self):
        """The masks kept from the last time this file was open."""
        path = self._masks_file()
        if path is None or not os.path.exists(path):
            return
        try:
            with open(path) as f:
                saved = json.load(f)
        except (OSError, ValueError):
            return
        nt = len(self._full_time_axis)
        nf = len(self._full_freq_axis) if self._full_freq_axis is not None else 0
        masks = [
            tuple(m) for m in saved.get("masks", [])
            if len(m) == 4 and 0 <= m[0] < m[1] <= nt and 0 <= m[2] < m[3] <= nf
        ]
        channels = {c for c in saved.get("channels", []) if 0 <= c < nf}
        keeps = [
            tuple(k) for k in saved.get("keeps", [])
            if len(k) == 4 and 0 <= k[0] < k[1] <= nt and 0 <= k[2] < k[3] <= nf
        ]
        self._keeps = keeps
        if masks or channels:
            self._masks = masks
            self._global_masked_freq_indices = channels
            self._mask_version += 1
            self._update_mask_summary()
            self.statusBar().showMessage(
                f"Restored the masks from last time ({len(masks)} regions, "
                f"{len(channels)} channels); Clear removes them.", 8000
            )

    def saveMasks(self):
        """Save the masks as times and frequencies (for this file or others)."""
        if not self._masks and not self._global_masked_freq_indices:
            QMessageBox.information(self, "Save Masks", "There are no masks.")
            return
        fileName, _ = QFileDialog.getSaveFileName(
            self, "Save Masks", "masks.json", "Masks (*.json)"
        )
        if not fileName:
            return
        times, freqs = self._full_time_axis, self._full_freq_axis
        regions = []
        for t0, t1, f0, f1 in self._masks:
            region = {"from_mjd": times[t0] / 86400.0, "to_mjd": times[t1 - 1] / 86400.0}
            if freqs is not None:
                band = freqs[f0:f1]
                region.update(from_mhz=float(np.min(band)), to_mhz=float(np.max(band)))
            else:
                region.update(from_channel=int(f0), to_channel=int(f1 - 1))
            regions.append(region)
        channels = sorted(self._global_masked_freq_indices)
        saved = {
            "source": self.current_filename,
            "regions": regions,
            "channels_mhz": [float(freqs[c]) for c in channels] if freqs is not None else [],
            "channels": [int(c) for c in channels] if freqs is None else [],
        }
        with open(fileName, "w") as f:
            json.dump(saved, f, indent=1)
        self.statusBar().showMessage(f"Saved {len(regions)} masks to {fileName}", 5000)

    def loadMasks(self):
        """Add masks saved with Save Masks (matched by time and frequency)."""
        if self._full_time_axis is None:
            QMessageBox.information(self, "Info", "No data loaded.")
            return
        fileName, _ = QFileDialog.getOpenFileName(self, "Load Masks", "", "Masks (*.json)")
        if not fileName:
            return
        try:
            with open(fileName) as f:
                saved = json.load(f)
        except (OSError, ValueError) as e:
            QMessageBox.critical(self, "Error", f"Failed to read the masks:\n{e}")
            return
        times, freqs = self._full_time_axis, self._full_freq_axis
        nf = len(freqs) if freqs is not None else self._image().shape[1]
        masks = []
        for region in saved.get("regions", []):
            t0 = int(np.searchsorted(times, region["from_mjd"] * 86400.0 - 1e-3, "left"))
            t1 = int(np.searchsorted(times, region["to_mjd"] * 86400.0 + 1e-3, "right"))
            if "from_mhz" in region and freqs is not None:
                inside = np.flatnonzero((freqs >= region["from_mhz"] - 1e-6) & (freqs <= region["to_mhz"] + 1e-6))
                if not len(inside):
                    continue
                f0, f1 = int(inside[0]), int(inside[-1]) + 1
            else:
                f0, f1 = region.get("from_channel", 0), region.get("to_channel", nf - 1) + 1
            if t0 < t1 and 0 <= f0 < f1 <= nf:
                masks.append((t0, t1, f0, f1))
        channels = set(c for c in saved.get("channels", []) if 0 <= c < nf)
        if freqs is not None:
            # Within half a channel of a channel here
            tolerance = 1e-3 + (0.5 * np.median(np.abs(np.diff(freqs))) if nf > 1 else np.inf)
            for mhz in saved.get("channels_mhz", []):
                c = int(np.argmin(np.abs(freqs - mhz)))
                if abs(freqs[c] - mhz) <= tolerance:
                    channels.add(c)
        if not masks and not channels:
            self.statusBar().showMessage("None of those masks fall on this file.", 5000)
            return
        before = (list(self._masks), set(self._global_masked_freq_indices))
        after = (before[0] + masks, before[1] | channels)
        self._do(("set", (before, after)))
        self.statusBar().showMessage(
            f"Loaded {len(masks)} masked regions and {len(channels)} channels.", 5000
        )

    def _update_mask_summary(self):
        regions, channels = len(self._masks), len(self._global_masked_freq_indices)
        parts = []
        if regions:
            parts.append(f"{regions} mask{'s' * (regions != 1)}")
        if channels:
            parts.append(f"{channels} channel{'s' * (channels != 1)}")
        if self._keeps:
            parts.append(f"{len(self._keeps)} kept")
        self.maskSummary.setText(" · ".join(parts) if parts else "none yet")
        self.maskSummary.setToolTip("Masks hold for the whole file")
        self.extendMaskBtn.setEnabled(bool(regions))
        self._update_extend_button()
        self.clearMaskBtn.setEnabled(bool(regions or channels or self._keeps))
        self._update_status_summary()

    def _rfi_settings(self):
        """The automatic flagging's settings (rfi_flagging.RfiSettings)."""
        from .rfi_flagging import RfiSettings

        o = self._rfi_options
        return RfiSettings(
            sensitivity=self.rfiSlider.value() / 100.0,
            convrfi=o["convrfi"], edges=o["edges"], sumthreshold=o["sumthreshold"],
            noise=o["noise"], fill_gaps=o["fill_gaps"],
            channel_limit=o["channel_limit"], time_limit=o["time_limit"],
        )

    def _noise_image(self):
        """The file's image of each pixel's standard deviation (e.g. LOFAR's
        STDDEV), when the spectrum is shown: for the noise-ratio detector."""
        if self._image_index != self._spectrum_index:
            return None
        for i, name in self._images:
            if i != self._spectrum_index and str(name).upper() in self._NOISE_NAMES:
                return self._image_data(self.hdul[i], i)
        return None

    def _noise_rows(self, noise, start, stop, cache=True):
        """Rows start..stop of the noise image, as (time, freq) float32."""
        key = np.s_[start:stop, :] if self._time_dim == 0 else np.s_[:, start:stop]
        raw = noise.read(key, cache=cache) if hasattr(noise, "read") else noise[key]
        rows = np.array(raw if self._time_dim == 0 else raw.T, dtype=np.float32)
        rows[~np.isfinite(rows)] = np.nan
        return rows

    def _apply_keeps(self, flags, time_axis):
        """``flags`` with the Keep regions cleared (a copy if any falls on it)."""
        if not self._keeps or time_axis is None or not len(time_axis):
            return flags
        full = self._full_time_axis
        flags = flags.copy()
        for t0, t1, f0, f1 in self._keeps:
            lo = np.searchsorted(time_axis, full[t0], "left")
            hi = np.searchsorted(time_axis, full[t1 - 1], "right")
            if lo < hi:
                flags[lo:hi, f0:f1] = False
        return flags

    def _view_box(self, region):
        """A region of the file (t0, t1, f0, f1) as view coordinates."""
        t0, t1, f0, f1 = region
        times, freqs = self._full_time_axis, self._full_freq_axis
        x0, x1 = float(times[t0]), float(times[t1 - 1])
        if freqs is None:
            return x0, x1, f0 - 0.5, f1 - 0.5
        band = freqs[f0:f1]
        return x0, x1, float(np.min(band)), float(np.max(band))

    def _apply_rfi_flags(self, data, time_axis, fast=False):
        """``data`` (a page as read) with its automatic RFI flags set to NaN
        (a copy; with the tint display, as it is: the flags are drawn over
        it), when they are on. Computed in the background the first time
        (the page shows unflagged meanwhile). Not on the quick preview: its
        samples are block means, where bursts look like RFI."""
        self._rfi_page_flags = None
        if not self._rfi_enabled or fast or data.size == 0:
            return data
        noise = self._noise_image() if self._rfi_options["noise"] else None
        settings = self._rfi_settings()
        key = (id(data), settings, noise is not None)
        cached = self._rfi_cache.get(key)
        # (the cache holds the page too, so its id can't be another's)
        if cached is None or cached[0] is not data:
            self._flag_in_background(data, time_axis, key, settings, noise)
            return data
        flags = self._apply_keeps(cached[1], time_axis)
        self._rfi_page_flags = flags
        if self._rfi_options["tint"] or not flags.any():
            return data
        data = data.copy()
        data[flags] = np.nan
        return data

    def _masked(self, shape, time_axis):
        """Where the masks are on a page of ``shape`` at times ``time_axis``."""
        return np.isnan(self._apply_masks(np.zeros(shape, np.float32), time_axis))

    def _fill_flags(self, raw, data, time_axis):
        """``data`` (flags blanked, masks applied) with the automatic flags
        filled from the good pixels around them, when chosen (not with the
        tint: it shows the data as is). Computed in the background (blank meanwhile), kept until the
        page, the flags or the masks change."""
        o = self._rfi_options
        flags = self._rfi_page_flags
        if flags is None or o["tint"] or o["fill"] == "blank" or not flags.any():
            return data
        key = (id(raw), self._rfi_settings(), o["fill"], o["reach"], self._mask_version)
        if self._fill_cache is not None and self._fill_cache[0] == key and self._fill_cache[1] is raw:
            return self._fill_cache[2]
        if self._fill_job is not None and self._fill_job.key == key:
            return data  # being filled
        if self._fill_job is not None:
            self._fill_job.stopped = True
        from .rfi_flagging import fill_flagged

        fill = flags & ~self._masked(data.shape, time_axis)
        method, reach = o["fill"], o["reach"]
        job = _RemoteRead(lambda: fill_flagged(data, fill, method, reach), self)
        job.key = key
        self._fill_job = job
        self.statusBar().showMessage("Filling the flagged pixels...")

        def done(filled):
            if self._fill_job is job:
                self._fill_job = None
            self._fill_cache = (key, raw, filled)
            if self._shown_raw is not None and self._shown_raw[0] is raw:
                self._redisplay()
                self.statusBar().showMessage("Flagged pixels filled", 4000)

        def failed(message):
            if self._fill_job is job:
                self._fill_job = None
            self.statusBar().showMessage(f"Filling failed: {message}", 8000)

        job.done.connect(done)
        job.failed.connect(failed)
        job.finished.connect(job.deleteLater)
        job.start()
        return data

    def _show_rfi_extras(self, time_axis, freq_axis):
        """With automatic flags: their tint (if chosen), each channel's
        flagged share beside the spectrum, and the Keep regions' outlines."""
        flags = self._rfi_page_flags
        o = self._rfi_options
        overlay = share = None
        if flags is not None:
            shares = self._binned(flags.astype(np.float32))  # flagged share of each shown sample
            if o["tint"]:
                overlay = shares
            if o["share"]:
                share = np.nanmean(shares, axis=0)
        self.canvas.set_overlay(overlay)
        self.canvas.set_channel_share(share, freq_axis if share is not None else None)
        self._show_keep_boxes(time_axis)

    def _show_keep_boxes(self, time_axis=None):
        """Outline the regions the tool on edits: Keep regions (green) with
        the Keep tool, masks (red) with the Mask tool; none otherwise."""
        if time_axis is None:
            time_axis = self._shown_raw[1] if self._shown_raw is not None else None
        regions, color = [], "#3ddc84"
        if self.keepBtn.isChecked():
            regions = self._keeps
        elif self.roiButton.isChecked():
            regions, color = self._masks, "#ff6b6b"
        boxes = []
        if regions and time_axis is not None and len(time_axis):
            full = self._full_time_axis
            for region in regions:
                if full[region[0]] <= time_axis[-1] and full[region[1] - 1] >= time_axis[0]:
                    boxes.append(self._view_box(region))
        self.canvas.set_boxes(boxes, color)

    def openRfiSettings(self):
        """What automatic RFI flagging does: detectors, filling, display."""
        o = dict(self._rfi_options)
        dialog = QDialog(self)
        dialog.setWindowTitle("RFI Flagging")
        layout = QVBoxLayout(dialog)
        detectors = QGroupBox("Detectors")
        dl = QVBoxLayout(detectors)
        convrfi = QCheckBox("Spikes and narrow lines (ConvRFI)")
        convrfi.setChecked(o["convrfi"])
        edges = QCheckBox("Sharp edges too (ConvRFI)")
        edges.setChecked(o["edges"])
        convrfi.toggled.connect(edges.setEnabled)
        edges.setEnabled(o["convrfi"])
        sumthr = QCheckBox("Faint lasting narrowband RFI (SumThreshold)")
        sumthr.setChecked(o["sumthreshold"])
        sumthr.setToolTip("AOFlagger's method; also flags fine burst structure")
        noise_name = None
        for i, name in self._images:
            if i != self._spectrum_index and str(name).upper() in self._NOISE_NAMES:
                noise_name = name
        noise = QCheckBox(
            f"Noise not proportional to power ({noise_name} image)" if noise_name
            else "Noise not proportional to power (the file has no noise image)"
        )
        noise.setChecked(o["noise"])
        noise.setEnabled(noise_name is not None)
        noise.setToolTip("Each pixel's std/mean against nearby channels'")
        for w in (convrfi, edges, sumthr, noise):
            dl.addWidget(w)
        layout.addWidget(detectors)

        after = QGroupBox("Then")
        al = QFormLayout(after)
        gaps = QDoubleSpinBox()
        gaps.setRange(0.0, 0.5)
        gaps.setSingleStep(0.05)
        gaps.setValue(o["fill_gaps"])
        gaps.setSpecialValueText("off")
        gaps.setToolTip("Flag gaps mostly surrounded by flags (0: off)")
        al.addRow("Fill gaps (eta):", gaps)
        channels = QSpinBox()
        channels.setRange(10, 100)
        channels.setSuffix(" %")
        channels.setValue(int(round(o["channel_limit"] * 100)))
        channels.setSpecialValueText("off")
        channels.setToolTip("Flag a whole channel above this share (100: off)")
        al.addRow("Whole channels above:", channels)
        times = QSpinBox()
        times.setRange(10, 100)
        times.setSuffix(" %")
        times.setValue(int(round(o["time_limit"] * 100)))
        times.setToolTip("Flag a whole time above this share (100: off)")
        al.addRow("Whole times above:", times)
        layout.addWidget(after)

        pixels = QGroupBox("Flagged pixels")
        pl = QFormLayout(pixels)
        fillCombo = QComboBox()
        for text, value in (
            ("Blank", "blank"),
            ("Median of good neighbours", "median"),
            ("Gaussian-weighted mean", "gaussian"),
        ):
            fillCombo.addItem(text, value)
        fillCombo.setCurrentIndex(max(0, fillCombo.findData(o["fill"])))
        fillCombo.setToolTip("Fill from good pixels around, in time and frequency")
        pl.addRow("Fill:", fillCombo)
        maxGap = QSpinBox()
        maxGap.setRange(1, 50)
        maxGap.setValue(int(o["reach"]))
        maxGap.setSuffix(" pixels")
        maxGap.setToolTip("Farther from any good pixel: left blank")
        maxGap.setEnabled(o["fill"] != "blank")
        fillCombo.currentIndexChanged.connect(lambda _: maxGap.setEnabled(fillCombo.currentData() != "blank"))
        pl.addRow("Reach:", maxGap)
        mask_note = QLabel("Masks you draw always stay blank.")
        mask_note.setObjectName("Hint")
        pl.addRow(mask_note)
        layout.addWidget(pixels)

        show = QGroupBox("Show")
        sl = QVBoxLayout(show)
        tint = QCheckBox("Flags as a tint over the data (else blanked)")
        tint.setChecked(o["tint"])
        share = QCheckBox("Each channel's flagged share, beside the spectrum")
        share.setChecked(o["share"])
        sl.addWidget(tint)
        sl.addWidget(share)
        note = QLabel("Saved files have no tint: flags blank or filled.")
        note.setObjectName("Hint")
        sl.addWidget(note)
        layout.addWidget(show)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec_() != QDialog.Accepted:
            return
        self._rfi_options.update(
            convrfi=convrfi.isChecked(), edges=edges.isChecked(),
            sumthreshold=sumthr.isChecked(), noise=noise.isChecked(),
            fill_gaps=float(gaps.value()),
            channel_limit=channels.value() / 100.0, time_limit=times.value() / 100.0,
            tint=tint.isChecked(), share=share.isChecked(),
            fill=fillCombo.currentData(), reach=maxGap.value(),
        )
        if self._rfi_enabled:
            self._redisplay()

    def _flag_in_background(self, data, time_axis, key, settings, noise):
        if self._rfi_job is not None and self._rfi_job.key == key:
            return  # being flagged
        if self._rfi_job is not None:
            self._rfi_job.stopped = True
        finite = data[np.isfinite(data)]
        if finite.size and float(finite.min()) < 0:
            self.statusBar().showMessage(
                "RFI flagging needs positive power: this data has negative values "
                "(e.g. background-subtracted), so it is not flagged.", 8000
            )
            return
        from .rfi_flagging import flag_page

        start = int(np.searchsorted(self._full_time_axis, time_axis[0])) if noise is not None else 0

        def work():
            stddev = None
            if noise is not None:
                stddev = self._noise_rows(noise, start, start + data.shape[0])
            return flag_page(data, settings, stddev)

        job = _RemoteRead(work, self)
        job.key = key
        self._rfi_job = job
        self.statusBar().showMessage("Flagging RFI...")

        def done(flags):
            if self._rfi_job is job:
                self._rfi_job = None
            if len(self._rfi_cache) >= 4:
                self._rfi_cache.pop(next(iter(self._rfi_cache)))
            self._rfi_cache[key] = (data, flags)
            # Show it, if that page is still the one shown
            if self._rfi_enabled and self._shown_raw is not None and self._shown_raw[0] is data:
                self._redisplay()
                shown = self._rfi_page_flags if self._rfi_page_flags is not None else flags
                self.statusBar().showMessage(f"RFI flagged: {shown.mean():.1%} of this page", 5000)

        def failed(message):
            if self._rfi_job is job:
                self._rfi_job = None
            self.statusBar().showMessage(f"RFI flagging failed: {message}", 8000)

        job.done.connect(done)
        job.failed.connect(failed)
        job.finished.connect(job.deleteLater)
        job.start()

    def onKeepToggled(self, checked):
        if checked and self._original_data is None:
            QMessageBox.information(self, "Info", "No data loaded.")
            self.keepBtn.setChecked(False)
            return
        if checked:
            for other in (self.roiButton, self.crossBtn, self.driftBtn):
                if other.isChecked():
                    other.setChecked(False)
            if not self._rfi_enabled:
                self.statusBar().showMessage(
                    "Keep regions apply to the automatic RFI flags (Clean, step 2)", 6000
                )
            self.canvas.enable_roi_selector(True, self._keep_roi_values, "KEEP")
        else:
            self.canvas.enable_roi_selector(False)
        self._show_keep_boxes()

    def _select_region_at(self, x, y):
        """Select the mask (Mask tool) or Keep region (Keep tool) the click
        at view (x, y) is in (the most recent, if several); Delete removes
        it. A click elsewhere deselects."""
        sample = self.canvas._index_at(x, y) if self.canvas._data is not None else None
        hits = []
        if sample is not None and self._shown_raw is not None and not self._previewing():
            t, _, f, _ = self._file_ranges(sample[0], sample[0], sample[1], sample[1])
            regions = self._keeps if self.keepBtn.isChecked() else self._masks
            hits = [r for r in regions if r[0] <= t < r[1] and r[2] <= f < r[3]]
        self._select_region(("keep" if self.keepBtn.isChecked() else "mask", hits[-1]) if hits else None)
        if hits:
            self.statusBar().showMessage("Selected: press Delete to remove it (Esc: deselect)", 5000)

    def _select_region(self, selection):
        """Select (kind, region), or nothing (None)."""
        self._selected_region = selection
        self.deleteRegionAct.setEnabled(selection is not None)
        self._update_extend_button()
        self.canvas.set_selected_box(self._view_box(selection[1]) if selection else None)

    def deleteSelectedRegion(self):
        """Remove the selected mask or Keep region (Undo restores it)."""
        selection = getattr(self, "_selected_region", None)
        if selection is None:
            return
        kind, region = selection
        if region not in (self._keeps if kind == "keep" else self._masks):
            self._select_region(None)
            return
        masks = [r for r in self._masks if not (kind == "mask" and r is region)]
        keeps = [r for r in self._keeps if not (kind == "keep" and r is region)]
        before = (list(self._masks), set(self._global_masked_freq_indices), list(self._keeps))
        after = (masks, set(self._global_masked_freq_indices), keeps)
        self._select_region(None)
        self._do(("set", (before, after)))
        self.statusBar().showMessage(
            f"Removed the {'Keep region' if kind == 'keep' else 'mask'} (Ctrl+Z restores)", 4000
        )

    def _keep_roi_values(self, ixmin, ixmax, iymin, iymax):
        """A region (samples of the data shown) the automatic flags leave alone."""
        if self._shown_raw is None or ixmax < ixmin or iymax < iymin:
            return
        if self._previewing():
            self.statusBar().showMessage(
                "The page is still loading: mark it once it shows in full.", 5000
            )
            return
        region = self._file_ranges(ixmin, ixmax, iymin, iymax)
        self._do(("keep", region))
        self.statusBar().showMessage("Kept from the automatic RFI flags", 4000)

    def onRfiToggled(self, checked):
        self._rfi_enabled = checked
        self._redisplay()
        self._update_status_summary()
        if not checked:
            self.statusBar().showMessage("RFI flags off", 3000)

    def _rfi_settings_changed(self):
        if self._rfi_enabled:
            self._redisplay()

    def _apply_masks(self, data, time_axis):
        """``data`` (rows at times ``time_axis``, all the file's channels)
        with the masks applied: a copy if any falls on it."""
        channels = [c for c in sorted(self._global_masked_freq_indices) if c < data.shape[1]]
        rows = []
        if self._masks and time_axis is not None and len(time_axis):
            full = self._full_time_axis
            for t0, t1, f0, f1 in self._masks:
                lo = np.searchsorted(time_axis, full[t0], "left")
                hi = np.searchsorted(time_axis, full[t1 - 1], "right")
                if lo < hi:
                    rows.append((lo, hi, f0, f1))
        if not channels and not rows:
            return data
        data = data.copy()
        if channels:
            data[:, channels] = np.nan
        for lo, hi, f0, f1 in rows:
            data[lo:hi, f0:f1] = np.nan
        return data

    def _mask_roi_values(self, ixmin, ixmax, iymin, iymax):
        """Mask a region of interest (samples of the data shown): kept for
        the whole file, as a range of its samples and channels."""
        if self._shown_raw is None or ixmax < ixmin or iymax < iymin:
            return
        if self._previewing():
            self.statusBar().showMessage(
                "The page is still loading: mask it once it shows in full.", 5000
            )
            return
        mask = self._file_ranges(ixmin, ixmax, iymin, iymax)
        self._do(("add", mask))
        self.statusBar().showMessage(
            f"Masked {mask[1] - mask[0]} time samples × {mask[3] - mask[2]} channels", 5000
        )

    def _selected_mask(self):
        selection = getattr(self, "_selected_region", None)
        if selection and selection[0] == "mask" and selection[1] in self._masks:
            return selection[1]
        return None

    def _update_extend_button(self):
        """The button's main part: the selected mask, or all masks."""
        if not hasattr(self, "extendMaskBtn"):
            return
        selected = self._selected_mask() is not None
        self.extendMaskBtn.setText("Extend selected to all pages" if selected else "Extend to all pages")
        self.extendSelectedAct.setEnabled(selected)
        self.extendAllAct.setEnabled(bool(self._masks))

    def onExtendDefault(self):
        if self._selected_mask() is not None:
            self.onExtendSelectedMask()
        else:
            self.onExtendMask()

    def onExtendSelectedMask(self):
        """Mask, at all times, the channels of the selected mask."""
        region = self._selected_mask()
        if region is None:
            self.statusBar().showMessage(
                "Select a mask first (Mask tool: click inside it).", 5000
            )
            return
        added = frozenset(set(range(region[2], region[3])) - self._global_masked_freq_indices)
        if not added:
            self.statusBar().showMessage("Those channels are masked at all times already.", 5000)
            return
        self._do(("extend", added))
        self.statusBar().showMessage(
            f"Extended the selected mask: {len(added)} channels masked at all times.", 5000
        )

    def onExtendMask(self):
        """Mask, across all time, the channels masked on the page shown."""
        if self._shown_raw is None:
            return
        times = self._shown_raw[1]
        full = self._full_time_axis
        channels = set()
        for t0, t1, f0, f1 in self._masks:
            if times is not None and full[t0] <= times[-1] and full[t1 - 1] >= times[0]:
                channels.update(range(f0, f1))
        added = frozenset(channels - self._global_masked_freq_indices)
        if not added:
            self.statusBar().showMessage(
                "No masked channels on this page to extend.", 5000
            )
            return
        self._do(("extend", added))
        self.statusBar().showMessage(
            f"Extended mask to {len(self._global_masked_freq_indices)} channels globally.",
            5000,
        )

    def onClearMasks(self):
        """Clear all masks, drawn and extended, and the Keep regions (Undo
        brings them back)."""
        reply = QMessageBox.question(
            self,
            "Clear Masks",
            "Clear all masks, extended channels and Keep regions?\n(Undo brings them back.)",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.revertToOriginal()

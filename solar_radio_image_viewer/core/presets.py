"""
Display presets: automatic display ranges and per-instrument presets.

Everything is driven by the tables below:

- ``AUTO_RANGES``: display-range modes that keep the current colormap/stretch.
- ``INSTRUMENTS``: colormap, stretch, gamma and range per instrument/variant.

To add an instrument, add an ``Instrument`` to ``INSTRUMENTS`` (it shows up in
the Presets menu automatically) and, if it should be applied automatically on
load, a rule in ``detect_instrument``.
"""

import os
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple, Union

import matplotlib
import numpy as np

from ..processing.colormaps import ensure_solar_colormaps

# --------------------------------------------------------------------------
# Display ranges
# --------------------------------------------------------------------------


def compute_range(data, mode):
    """Return ``(vmin, vmax)`` for ``data`` using a named range mode.

    ``mode`` may also be a ``(vmin, vmax)`` tuple, which is returned as-is.
    """
    if isinstance(mode, tuple):
        return mode
    if mode == "minmax":
        return float(np.nanmin(data)), float(np.nanmax(data))
    if mode == "zero_max":
        return 0.0, float(np.nanmax(data))
    if mode == "zero_p99.5":
        # 99.5 percentile avoids hot pixels
        return 0.0, float(np.nanpercentile(data, 99.5))
    if mode == "p1_99":
        return np.nanpercentile(data, 1), np.nanpercentile(data, 99)
    if mode == "p01_999":
        return np.nanpercentile(data, 0.1), np.nanpercentile(data, 99.9)
    if mode == "p5_95":
        return np.nanpercentile(data, 5), np.nanpercentile(data, 95)
    if mode == "median_rms":
        median_val = np.nanmedian(data)
        rms_val = np.sqrt(np.nanmean((data - median_val) ** 2))
        return median_val - 3 * rms_val, median_val + 3 * rms_val
    raise ValueError(f"Unknown range mode: {mode!r}")


def display_range(vmin, vmax):
    """Make ``(vmin, vmax)`` usable as a colour range: finite, with vmax > vmin.

    A constant map (vmin == vmax, e.g. Lfrac/Vfrac with everything below the
    threshold) gets a small range centred on its value; an all-NaN map (e.g. a
    fully masked PANG) gets (0, 1).
    """
    vmin, vmax = float(vmin), float(vmax)
    if not (np.isfinite(vmin) and np.isfinite(vmax)):
        return 0.0, 1.0
    if vmax <= vmin:
        pad = abs(vmin) * 0.01 if vmin != 0 else 1.0
        return vmin - pad, vmin + pad
    return vmin, vmax


@dataclass(frozen=True)
class AutoRange:
    menu_label: str
    status_tip: str
    status_message: str  # formatted with vmin, vmax
    shortcut: Optional[str] = None


# Order here is the order in the Presets menu.
AUTO_RANGES = {
    "minmax": AutoRange(
        "Auto Min/Max",
        "Set display range to data min/max",
        "Set display range to min/max: [{vmin:.4g}, {vmax:.4g}]",
        "F5",
    ),
    "p1_99": AutoRange(
        "Auto Percentile (1%,99%)",
        "Set display range to 1st and 99th percentiles",
        "Set display range to 1-99 percentile: [{vmin:.4g}, {vmax:.4g}]",
        "F6",
    ),
    "p01_999": AutoRange(
        "Auto Percentile (0.1%,99.9%)",
        "Set display range to 0.1st and 99.9th percentiles",
        "Set display range to 0.1st and 99.9th percentiles: "
        "[{vmin:.4g}, {vmax:.4g}]",
    ),
    "p5_95": AutoRange(
        "Auto Percentile (5%,95%)",
        "Set display range to 5th and 95th percentiles",
        "Set display range to 5th and 95th percentiles: [{vmin:.4g}, {vmax:.4g}]",
    ),
    "median_rms": AutoRange(
        "Auto Median ± 3×RMS",
        "Set display range to median ± 3×RMS",
        "Set display range to median±3×RMS: [{vmin:.4g}, {vmax:.4g}]",
        "F7",
    ),
}

# --------------------------------------------------------------------------
# Instrument presets
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Preset:
    label: str
    range: Union[str, Tuple[float, float]]  # compute_range mode or fixed range
    stretch: str
    cmap: str  # preferred colormap
    fallback_cmap: Optional[str]  # used if cmap is missing; None keeps current
    gamma: Optional[float] = 1.0  # None keeps the current gamma


@dataclass(frozen=True)
class Instrument:
    menu_label: str
    presets: Dict[Union[int, str], Preset]
    default_variant: Optional[Union[int, str]] = None
    # Used for variants not in ``presets`` (otherwise ``default_variant``)
    unknown_variant: Optional[Preset] = None
    variant_label: str = "{} Å"  # menu label for each variant
    shortcuts: Dict[Union[int, str], str] = field(default_factory=dict)
    in_menu: bool = True


def _aia(wavelength, stretch="sqrt", gamma=None):
    return Preset(
        f"AIA {wavelength} Å",
        "zero_max",
        stretch,
        f"sdoaia{wavelength}",
        None,
        gamma,
    )


_MAGNETOGRAM = dict(range=(-1000, 1000), stretch="linear", cmap="gray")

INSTRUMENTS = {
    "AIA": Instrument(
        "AIA Presets",
        {
            94: _aia(94, "arcsinh"),
            131: _aia(131),
            171: _aia(171),
            193: _aia(193),
            211: _aia(211),
            304: _aia(304, "arcsinh"),
            335: _aia(335, "power", 0.3),
            1600: _aia(1600, "power", 0.55),
            1700: _aia(1700, "power", 0.7),
            4500: _aia(4500, "linear"),
        },
        default_variant=171,
        shortcuts={171: "F8"},
    ),
    "HMI": Instrument(
        "HMI Preset",
        {None: Preset("HMI", fallback_cmap="gray", **_MAGNETOGRAM)},
        default_variant=None,
        shortcuts={None: "F9"},
    ),
    "EIT": Instrument(
        "SOHO/EIT Presets",
        {
            w: Preset(f"SOHO EIT {w}", "zero_max", "sqrt", f"sohoeit{w}", "inferno")
            for w in (171, 195, 284, 304)
        },
        default_variant=171,
    ),
    "LASCO": Instrument(
        "SOHO/LASCO Presets",
        {
            d: Preset(f"SOHO LASCO {d}", "minmax", "sqrt", f"soholasco{d[1]}", "afmhot")
            for d in ("C2", "C3")
        },
        default_variant="C2",
        variant_label="{}",
    ),
    "IRIS": Instrument(
        "IRIS SJI Presets",
        {
            w: Preset(f"IRIS SJI {w}", "zero_p99.5", "sqrt", f"irissji{w}", "viridis")
            for w in (1330, 1400, 2796, 2832)
        },
        default_variant=1330,
    ),
    "SUVI": Instrument(
        "GOES SUVI Presets",
        {
            w: Preset(
                f"GOES SUVI {w}", "zero_p99.5", "sqrt", f"goes-rsuvi{w}", "inferno"
            )
            for w in (94, 131, 171, 195, 284, 304)
        },
        unknown_variant=Preset("GOES SUVI", "zero_p99.5", "sqrt", "inferno", "inferno"),
    ),
    "STEREO": Instrument(
        "STEREO Presets",
        {
            "EUVI": Preset("STEREO EUVI", "minmax", "sqrt", "sdoaia171", "inferno"),
            "COR1": Preset("STEREO COR1", "p1_99", "sqrt", "soholasco2", "afmhot"),
            "COR2": Preset("STEREO COR2", "p1_99", "sqrt", "soholasco2", "afmhot"),
        },
        unknown_variant=Preset("STEREO", "minmax", "linear", "viridis", "viridis"),
        variant_label="{}",
    ),
    "GONG": Instrument(
        "GONG Preset",
        {None: Preset("GONG magnetogram", fallback_cmap="gray", **_MAGNETOGRAM)},
        default_variant=None,
        in_menu=False,
    ),
}


def _normalize_variant(variant):
    if isinstance(variant, str):
        return int(variant) if variant.isdigit() else variant.upper()
    return variant


def get_preset(instrument, variant=None):
    """Look up the ``Preset`` for an instrument and variant (wavelength/detector)."""
    inst = INSTRUMENTS[instrument]
    variant = _normalize_variant(variant)
    if variant in inst.presets:
        return inst.presets[variant]
    if inst.unknown_variant is not None:
        return inst.unknown_variant
    return inst.presets[inst.default_variant]


def resolve_cmap(preset, current_cmap):
    """Return the colormap to use: preferred, else fallback, else current."""
    ensure_solar_colormaps()
    if preset.cmap in matplotlib.colormaps:
        return preset.cmap
    return preset.fallback_cmap or current_cmap


def _variant_from_filename(fname, candidates, default, patterns=("{}",)):
    for w in candidates:
        if any(p.format(w) in fname for p in patterns):
            return w
    return default


def detect_instrument(header, filename):
    """Guess ``(instrument, variant)`` from a FITS header and file name.

    Returns ``None`` when no instrument preset applies.
    """
    fname = os.path.basename(filename).lower()
    telescop = str(header.get("TELESCOP", "")).upper().strip()
    instrume = str(header.get("INSTRUME", "")).upper().strip()
    detector = str(header.get("DETECTOR", "")).upper().strip()
    wavelnth = str(header.get("WAVELNTH", ""))
    if not wavelnth:
        wavelnth = str(header.get("TWAVE1", ""))  # IRIS fallback
    try:
        wavelnth_int = int(float(wavelnth)) if wavelnth else 0
    except Exception:
        wavelnth_int = 0

    if telescop == "SDO/HMI" or "HMI" in instrume or "hmi" in fname:
        return "HMI", None
    if telescop == "SDO/AIA" or "aia" in fname:
        wv = wavelnth_int
        if wv == 0 and "aia" in fname:
            wv = _variant_from_filename(fname, INSTRUMENTS["AIA"].presets, 171)
        return "AIA", wv
    if (telescop == "SOHO" and instrume == "EIT") or "eit" in fname or "efz" in fname:
        wv = wavelnth_int
        if wv == 0 and ("eit" in fname or "efz" in fname):
            wv = _variant_from_filename(fname, INSTRUMENTS["EIT"].presets, 171)
        return "EIT", wv
    if (telescop == "SOHO" and "LASCO" in instrume) or "lasco" in fname:
        det = detector
        if not det and "lasco" in fname:
            if "cal_2" in fname:
                det = "C2"
            elif "cal_3" in fname:
                det = "C3"
        return "LASCO", "C3" if "C3" in det else "C2"
    if telescop == "IRIS" or "SJI" in instrume or "iris" in fname or "sji" in fname:
        wv = wavelnth_int
        if wv == 0 and ("iris" in fname or "sji" in fname):
            wv = _variant_from_filename(fname, INSTRUMENTS["IRIS"].presets, 1330)
        return "IRIS", wv
    if "SUVI" in instrume or "suvi" in fname:
        wv = wavelnth_int
        if wv == 0 and "suvi" in fname:
            wv = _variant_from_filename(
                fname, INSTRUMENTS["SUVI"].presets, 171, ("{}", "ci0{}")
            )
        return "SUVI", wv
    if "GONG" in telescop or "GONG" in instrume or "gong" in fname:
        return "GONG", None
    stereo_names = {
        "EUVI": ("euvi", "eua", "eub"),
        "COR1": ("cor1", "c1a", "c1b"),
        "COR2": ("cor2", "c2a", "c2b"),
    }
    if (telescop == "STEREO" and instrume == "SECCHI") or any(
        n in fname for names in stereo_names.values() for n in names
    ):
        for det, names in stereo_names.items():
            if detector == det or any(n in fname for n in names):
                return "STEREO", det
    return None


def _format_gamma(gamma):
    return f"{gamma:.1f}" if round(gamma, 1) == gamma else f"{gamma:.2f}"


# --------------------------------------------------------------------------
# Qt glue
# --------------------------------------------------------------------------


class PresetsMixin:
    """Apply automatic display ranges and instrument presets to a tab.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``cmap_combo``, ``current_header``, ``current_image_data``, ``gamma_entry``,
    ``imagename``, ``plot_image``, ``set_range``, ``show_status_message``,
    ``stretch_combo``, ``update_gamma_slider_state``, ``vmax_entry``,
    ``vmin_entry``
    """

    def _current_gamma(self):
        try:
            return float(self.gamma_entry.text())
        except (ValueError, AttributeError):
            return 1.0

    def apply_auto_range(self, mode):
        """Set the display range using an ``AUTO_RANGES`` mode, keeping the
        current colormap, stretch and gamma."""
        if self.current_image_data is None:
            return

        vmin, vmax = compute_range(self.current_image_data, mode)
        self.set_range(vmin, vmax)
        if hasattr(self, "show_range_preset"):
            self.show_range_preset(mode)

        stretch = (
            self.stretch_combo.currentText()
            if hasattr(self, "stretch_combo")
            else "linear"
        )
        cmap = (
            self.cmap_combo.currentText() if hasattr(self, "cmap_combo") else "viridis"
        )
        self.plot_image(vmin, vmax, stretch, cmap, self._current_gamma())

        if self.parent():
            self.show_status_message(
                AUTO_RANGES[mode].status_message.format(vmin=vmin, vmax=vmax)
            )

    def apply_instrument_preset(self, instrument, variant=None):
        """Apply an ``INSTRUMENTS`` preset (colormap, stretch, gamma, range)."""
        if self.current_image_data is None:
            return

        preset = get_preset(instrument, variant)
        vmin, vmax = compute_range(self.current_image_data, preset.range)
        self.set_range(vmin, vmax)

        gamma = self._current_gamma() if preset.gamma is None else preset.gamma
        cmap = resolve_cmap(preset, self.cmap_combo.currentText())
        if cmap != preset.cmap:
            print(f"[Warning] {preset.cmap} colormap not available, using {cmap}")
            self.show_status_message(
                f"Warning: {preset.cmap} colormap not available, using {cmap}"
            )

        self.show_range(vmin, vmax)
        # Without signals: their handlers would each redraw the image, only
        # for the plot_image below to draw it again
        self.stretch_combo.blockSignals(True)
        self.stretch_combo.setCurrentText(preset.stretch)
        self.stretch_combo.blockSignals(False)
        self.update_gamma_slider_state()
        self.cmap_combo.blockSignals(True)
        self.cmap_combo.setCurrentText(cmap)
        self.cmap_combo.blockSignals(False)
        self.gamma_entry.setText(_format_gamma(gamma))
        # A redraw still queued from an earlier widget change (schedule_plot)
        # would replace this one with the rounded values in the entry boxes
        if getattr(self, "_plot_timer", None) is not None:
            self._plot_timer.stop()
        self.plot_image(
            vmin, vmax, preset.stretch, cmap, gamma, interpolation="nearest"
        )

        if self.parent():
            self.show_status_message(
                f"Set {preset.label} preset: range [{vmin:.4g}, {vmax:.4g}], "
                f"colormap={cmap}, stretch={preset.stretch}"
            )

    def _auto_apply_instrument_preset(self):
        """Apply the preset matching the loaded image. Returns True if one did."""
        match = detect_instrument(
            getattr(self, "current_header", {}) or {}, self.imagename
        )
        if match is None:
            return False
        self.apply_instrument_preset(*match)
        return True


class PresetMenuMixin:
    """Build the main window's Presets menu from the preset tables.

    Mixed into :class:`SolarRadioImageViewerApp`; relies on its attributes via
    ``self``.

    Host attributes/methods used:

    ``tab_widget``
    """

    def _apply_to_current_tab(self, method_name, *args):
        current_tab = self.tab_widget.currentWidget()
        if current_tab:
            getattr(current_tab, method_name)(*args)

    def _add_preset_actions(self, preset_menu):
        from PyQt5.QtWidgets import QAction, QMenu

        def add_action(menu, label, status_tip, shortcut, method_name, *args):
            action = QAction(label, self)
            if shortcut:
                action.setShortcut(shortcut)
            action.setStatusTip(status_tip)
            action.triggered.connect(
                lambda checked=False: self._apply_to_current_tab(method_name, *args)
            )
            menu.addAction(action)

        for mode, auto in AUTO_RANGES.items():
            add_action(
                preset_menu,
                auto.menu_label,
                auto.status_tip,
                auto.shortcut,
                "apply_auto_range",
                mode,
            )

        for name, inst in INSTRUMENTS.items():
            if not inst.in_menu:
                continue
            if list(inst.presets) == [None]:
                preset = inst.presets[None]
                add_action(
                    preset_menu,
                    inst.menu_label,
                    f"Set display range and colormap for {preset.label}",
                    inst.shortcuts.get(None),
                    "apply_instrument_preset",
                    name,
                    None,
                )
                continue
            submenu = QMenu(inst.menu_label, self)
            for variant, preset in inst.presets.items():
                add_action(
                    submenu,
                    inst.variant_label.format(variant),
                    f"Set display range and colormap for {preset.label}",
                    inst.shortcuts.get(variant),
                    "apply_instrument_preset",
                    name,
                    variant,
                )
            preset_menu.addMenu(submenu)

"""
Rendering the image tab: plot_image and its drawing steps.
"""

import logging
import os

import matplotlib
import numpy as np
from matplotlib.axes import Axes
from matplotlib.colors import LogNorm, Normalize
from matplotlib.patches import Ellipse

from ..processing.norms import AsinhNorm, HistEqNorm, PowerNorm, SqrtNorm, ZScaleNorm
from ..processing.image_meta import casa_info, casa_obs_time, fits_header
from ..processing.colormaps import ensure_solar_colormaps
from ..processing.contour_layers import contour_layer_settings
from .canvas import READOUT_HINT
from .display_resolution import draw_at_screen_resolution
from .presets import display_range
from ..processing.fits_io import is_fits, readable_image_path
from ..ui.busy import busy

logger = logging.getLogger(__name__)


from ..processing.plot_style import DEFAULT_PLOT_SETTINGS

_PAD_KEYS = ("pad_left", "pad_right", "pad_top", "pad_bottom")


class RenderingMixin:
    """Draws the current image: norm, WCS axes, title, colorbar, styling, beam, layout.

    plot_image() is the entry point; it reuses the existing image in place
    (_try_fast_redraw) when only the colormap/normalization changed.

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``arrow_annotations``, ``beam_style``, ``canvas``, ``contour_layers``,
    ``current_image_data``, ``current_wcs``, ``draw_contours``,
    ``_draw_solar_disk``, ``figure``, ``grid_style``, ``imagename``,
    ``init_region_editor``, ``plot_settings``, ``psf``, ``shape_annotations``,
    ``show_beam_checkbox``, ``show_solar_disk_checkbox``,
    ``show_status_message``, ``solar_disk_style``, ``text_annotations``
    """

    def _get_render_state(self, tight_layout=False):
        """
        Capture the current structural state of the plot.
        Returns a dict of values that, if changed, require a full redraw.
        """
        state = {
            "data_id": (
                id(self.current_image_data)
                if self.current_image_data is not None
                else None
            ),
            "wcs_id": id(self.current_wcs) if self.current_wcs else None,
            "fits_flag": self._cached_fits_flag,
            "tight_layout": tight_layout,
            # Overlay states
            "show_beam": self.show_beam_checkbox.isChecked(),
            "show_grid": self.show_grid_checkbox.isChecked(),
            "show_solar_disk": self.show_solar_disk_checkbox.isChecked(),
            "show_contours": self.show_contours_checkbox.isChecked(),
        }

        # Settings snapshots (identity of loaded contour data tracked separately)
        state["contour_data_id"] = tuple(
            id(layer.get("contour_data")) for layer in self.contour_layers
        )
        state["contour_settings"] = str(
            [contour_layer_settings(layer) for layer in self.contour_layers]
        ) + str(self.contour_options)

        state["grid_settings"] = str(self.grid_style)
        state["beam_settings"] = str(self.beam_style)
        state["solar_disk_settings"] = str(self.solar_disk_style)
        state["plot_settings"] = str(self.plot_settings)
        state["shape_annotations"] = str(self.shape_annotations)
        state["text_annotations"] = str(self.text_annotations)
        state["arrow_annotations"] = str(self.arrow_annotations)

        return state

    def plot_image(self, *args, **kwargs):
        """Draw the image (see _plot_image), with the wait cursor until it is on screen."""
        with busy():
            self._plot_image(*args, **kwargs)

    def _plot_image(
        self,
        vmin_val=None,
        vmax_val=None,
        stretch="linear",
        cmap="viridis",
        gamma=1.0,
        interpolation="nearest",
        tight_layout=False,
        preserve_view=True,
    ):
        if isinstance(cmap, str) and cmap not in matplotlib.colormaps:
            ensure_solar_colormaps()

        self._refresh_metadata_cache()

        # Use cached values
        fits_flag = self._cached_fits_flag
        header = self._cached_fits_header
        csys = self._cached_csys
        summary = self._cached_summary
        csys_record = self._cached_csys_record

        # Null-safe fallbacks for header and csys_record
        if header is None:
            header = {}
        if csys_record is None:
            csys_record = {}

        if self.current_image_data is None:
            return

        data = self.current_image_data

        self._update_transposed_cache(data)
        transposed_data = self._cached_transposed

        stored_xlim = None
        stored_ylim = None
        if preserve_view and self.figure.axes:
            try:
                stored_xlim = self.figure.axes[0].get_xlim()
                stored_ylim = self.figure.axes[0].get_ylim()
            except Exception:
                stored_xlim = None
                stored_ylim = None

        # Determine vmin/vmax
        if vmin_val is None:
            vmin_val = np.nanmin(data)
        if vmax_val is None:
            vmax_val = np.nanmax(data)
        vmin_val, vmax_val = display_range(vmin_val, vmax_val)

        norm = self._make_norm(stretch, vmin_val, vmax_val, gamma)
        if self._try_fast_redraw(
            norm, cmap, stretch, gamma, vmin_val, vmax_val, tight_layout, preserve_view
        ):
            return

        self.figure.clear()
        # Fresh norm for the new image (the one above may be attached to the old one)
        norm = self._make_norm(stretch, vmin_val, vmax_val, gamma)

        # Monkey patch removal if present
        if hasattr(self.figure, "_monkey_patched"):
            del self.figure._monkey_patched
            self.figure.clear = self.figure._original_clear

        wcs_obj = self._get_wcs_object(fits_flag, header, csys, summary)

        ax, im = self._draw_image_axes(
            wcs_obj,
            transposed_data,
            cmap,
            norm,
            interpolation,
            fits_flag,
            header,
            csys_record,
        )
        draw_at_screen_resolution(im)

        if stored_xlim is not None and stored_ylim is not None:
            ax.set_xlim(stored_xlim)
            ax.set_ylim(stored_ylim)

        self._set_plot_title(ax, fits_flag, header, csys_record)

        cb = self._add_colorbar(ax, im, stretch, vmin_val, vmax_val)

        self._apply_plot_customization(ax, cb)

        self._draw_beam(ax)

        self._draw_solar_disk(ax, data)

        # Draw shape, text, and arrow annotations
        from ..shape_annotations import (
            draw_arrow_annotations,
            draw_shape_annotations,
            draw_text_annotations,
        )

        draw_shape_annotations(self, ax)
        draw_text_annotations(self, ax)
        draw_arrow_annotations(self, ax)

        # Draw contours if enabled
        if (
            hasattr(self, "show_contours_checkbox")
            and self.show_contours_checkbox.isChecked()
        ):
            self.draw_contours(ax)

        self.init_region_editor(ax)
        self._init_box_zoom(ax)

        self._apply_layout_settings(tight_layout)

        # Draw now rather than when idle, so it happens under the wait cursor
        self.canvas.draw()

        if self.coord_label.text() == "No image loaded":
            self.coord_label.setText(READOUT_HINT)

        # Save state for next update
        self._last_rendered_state = self._get_render_state(tight_layout=tight_layout)

        self._last_norm_params = (vmin_val, vmax_val, stretch, gamma)

    @staticmethod
    def _make_norm(stretch, vmin, vmax, gamma):
        """Matplotlib normalization for a stretch name."""
        if stretch == "log":
            safe_min = max(vmin, 1e-8)
            safe_max = max(vmax, safe_min * 1.01)
            return LogNorm(vmin=safe_min, vmax=safe_max)
        elif stretch == "sqrt":
            return SqrtNorm(vmin=vmin, vmax=vmax)
        elif stretch == "arcsinh":
            return AsinhNorm(vmin=vmin, vmax=vmax)
        elif stretch == "power":
            return PowerNorm(vmin=vmin, vmax=vmax, gamma=gamma)
        elif stretch == "zscale":
            return ZScaleNorm(vmin=vmin, vmax=vmax, contrast=0.25, num_samples=600)
        elif stretch == "histeq":
            return HistEqNorm(vmin=vmin, vmax=vmax, n_bins=256)
        else:
            return Normalize(vmin=vmin, vmax=vmax)

    def _refresh_metadata_cache(self):
        """Reload FITS header / CASA coordsys for self.imagename if it changed."""
        # Lazy load image metadata - only refresh when imagename changes
        if self._cached_imagename != self.imagename:
            # Cache miss - need to reload metadata
            self._cached_fits_flag = False
            self._cached_fits_header = None
            self._cached_csys = None
            self._cached_summary = None
            self._cached_csys_record = None

            if is_fits(self.imagename):
                from astropy.io import fits

                try:
                    self._cached_fits_flag = True
                    self._cached_fits_header = dict(fits_header(self.imagename))
                except Exception:
                    try:
                        self._cached_fits_header = dict(fits.getheader(readable_image_path(self.imagename)))
                    except Exception as e2:
                        logger.error(f"Error getting header: {e2}")
                        self.show_status_message(f"Error getting header: {e2}")
                        self._cached_fits_flag = False

            try:
                info = casa_info(self.imagename)
                self._cached_csys = info.coordsys()
                self._cached_summary = info.summary
                self._cached_csys_record = info.csys_record
            except Exception as e:
                logger.error(f"Error getting image metadata: {e}")
                self.show_status_message(f"Error getting image metadata: {e}")

            self._cached_imagename = self.imagename

    def _update_transposed_cache(self, data):
        """Cache data.T."""
        if not hasattr(self, "_cached_data_id") or self._cached_data_id != id(data):
            self._cached_transposed = data.transpose()
            self._cached_data_id = id(data)

    def _try_fast_redraw(
        self, norm, cmap, stretch, gamma, fp_vmin, fp_vmax, tight_layout, preserve_view
    ):
        """Update the existing image in place if the plot structure is unchanged.

        Must run before figure.clear(). Returns True if the redraw was handled.
        """
        # FAST PATH OPTIMIZATION: Update existing plot if structure hasn't changed
        # MUST BE DONE BEFORE figure.clear()
        current_state = self._get_render_state(tight_layout=tight_layout)

        can_fast_path = (
            hasattr(self, "image_plot")
            and self.image_plot is not None
            and self.figure.axes
            and preserve_view
        )

        if can_fast_path:
            if self._last_rendered_state == current_state:
                # Success! Update and Return.
                try:
                    self.image_plot.set_cmap(cmap)
                    self.image_plot.set_norm(norm)
                    norm_params = (fp_vmin, fp_vmax, stretch, gamma)

                    if hasattr(self, "colorbar") and self.colorbar:
                        self.colorbar.update_normal(self.image_plot)
                        if stretch in ("power", "histeq", "sqrt", "arcsinh", "log"):
                            try:
                                ticks = self.colorbar.ax.get_yticks()
                                valid_ticks = [
                                    t for t in ticks if fp_vmin <= t <= fp_vmax
                                ]
                                if len(valid_ticks) >= 2:
                                    self.colorbar.ax.set_yticks(valid_ticks)
                            except Exception:
                                pass

                    """if last_norm_params != norm_params:
                        if hasattr(self, "colorbar") and self.colorbar:
                            self.colorbar.update_normal(self.image_plot)
                            if stretch in ("power", "histeq", "sqrt", "arcsinh", "log"):
                                try:
                                    ticks = self.colorbar.ax.get_yticks()
                                    valid_ticks = [t for t in ticks if fp_vmin <= t <= fp_vmax]
                                    if len(valid_ticks) >= 2:
                                        self.colorbar.ax.set_yticks(valid_ticks)
                                except Exception:
                                    pass
                    """

                    # Cache new params
                    self._last_norm_params = norm_params

                    self.canvas.draw()
                    return True
                except Exception as e:
                    print(f"Fast Path Update Failed: {e}")
            else:
                # Debugging Mismatch (Optional log, keeping it minimal)
                pass
        return False

    def _get_wcs_object(self, fits_flag, header, csys, summary):
        """Build (and cache per current_wcs) a 2D astropy WCS for plotting."""
        # Cache the WCS object if current_wcs hasn't changed.
        wcs_obj = None
        if self.current_wcs:
            if (not hasattr(self, "_cached_wcs_id")) or (
                self._cached_wcs_id != id(self.current_wcs)
            ):
                try:
                    self._cached_wcs_obj = self._build_display_wcs(
                        self.current_wcs, fits_flag, header, csys, summary
                    )
                    self._cached_wcs_id = id(self.current_wcs)
                except Exception as e:
                    logger.error(f"Error creating WCS: {e}")
                    self.show_status_message(f"Error creating WCS: {e}")
                    self._cached_wcs_obj = None
            wcs_obj = self._cached_wcs_obj
        return wcs_obj

    def _build_display_wcs(self, csys_tool, fits_flag, header, csys, summary):
        """2D astropy WCS for an image's pixel grid, from its CASA coordsys
        ``csys_tool`` (as loaded, so including any downsampling) and its FITS
        header / full coordsys / summary. None if the projection is unsupported.
        """
        from astropy.wcs import WCS

        ref_val = csys_tool.referencevalue()["numeric"][0:2]
        ref_pix = csys_tool.referencepixel()["numeric"][0:2]
        increment = csys_tool.increment()["numeric"][0:2]
        wcs_obj = WCS(naxis=2)
        wcs_obj.wcs.crpix = [
            ref_pix[0] + 1,
            ref_pix[1] + 1,
        ]  # CASA 0-indexed -> FITS 1-indexed
        temp_flag = False
        if fits_flag:
            if (
                header["CTYPE1"] == "HPLN-TAN"
                or header["CTYPE1"] == "RA---SIN"
                or header["CTYPE1"] == "RA---TAN"
            ):
                wcs_obj.wcs.crval = [
                    ref_val[0] * 180 / np.pi,
                    ref_val[1] * 180 / np.pi,
                ]
                wcs_obj.wcs.cdelt = [
                    increment[0] * 180 / np.pi,
                    increment[1] * 180 / np.pi,
                ]
                temp_flag = True
            elif header["CTYPE1"] == "SOLAR-X":
                wcs_obj.wcs.crval = ref_val
                wcs_obj.wcs.cdelt = increment
                temp_flag = True
        if not temp_flag:
            if "Right Ascension" in summary["axisnames"]:
                wcs_obj.wcs.crval = [
                    ref_val[0] * 180 / np.pi,
                    ref_val[1] * 180 / np.pi,
                ]
                wcs_obj.wcs.cdelt = [
                    increment[0] * 180 / np.pi,
                    increment[1] * 180 / np.pi,
                ]
            else:
                wcs_obj.wcs.crval = ref_val
                wcs_obj.wcs.cdelt = increment
        try:
            if fits_flag:
                try:
                    wcs_obj.wcs.ctype = [
                        header["CTYPE1"],
                        header["CTYPE2"],
                    ]
                except Exception as e:
                    logger.error(f"Error getting projection: {e}")
                    self.show_status_message(
                        f"Error getting projection: {e}"
                    )
                    wcs_obj = None
            elif (csys.projection()["type"] == "SIN") and (
                "Right Ascension" in summary["axisnames"]
            ):
                wcs_obj.wcs.ctype = [
                    "RA---SIN",
                    "DEC--SIN",
                ]
            elif (csys.projection()["type"] == "TAN") and (
                "Right Ascension" in summary["axisnames"]
            ):
                wcs_obj.wcs.ctype = [
                    "RA---TAN",
                    "DEC--TAN",
                ]

            else:
                projection = csys.projection()["type"]
                logger.error(f"Unsupported projection: {projection}")
                self.show_status_message(
                    f"Unsupported projection: {projection}"
                )
                wcs_obj = None

        except Exception as e:
            logger.error(f"Error getting projection: {e}")
            self.show_status_message(f"Error getting projection: {e}")
            wcs_obj = None
        return wcs_obj

    def _draw_image_axes(
        self,
        wcs_obj,
        transposed_data,
        cmap,
        norm,
        interpolation,
        fits_flag,
        header,
        csys_record,
    ):
        """Create the axes (WCS-aware if possible), draw the image, set axis labels."""
        # Plot with or without WCS
        if wcs_obj is not None:
            try:
                ax = self.figure.add_subplot(111, projection=wcs_obj)
                # Matplotlib's imshow directly: WCSAxes.imshow only adds
                # handling for PIL images, behind a version check that scans
                # every installed package's metadata on each call
                im = Axes.imshow(
                    ax,
                    transposed_data,
                    origin="lower",
                    cmap=cmap,
                    norm=norm,
                    interpolation=interpolation,
                )
                self.image_plot = im
                if fits_flag:
                    if header["CTYPE1"] == "HPLN-TAN":
                        ax.set_xlabel("Solar X")
                        ax.set_ylabel("Solar Y")
                    elif header["CTYPE1"] == "SOLAR-X":
                        ax.set_xlabel(f"Solar X ({header['CUNIT1']})")
                        ax.set_ylabel(f"Solar Y ({header['CUNIT2']})")
                    else:
                        ax.set_xlabel("Right Ascension (J2000)")
                        ax.set_ylabel("Declination (J2000)")
                elif wcs_obj.wcs.ctype[0] == "RA---SIN":
                    ax.set_xlabel("Right Ascension (J2000)")
                    ax.set_ylabel("Declination (J2000)")
                elif wcs_obj.wcs.ctype[0] == "SOLAR-X":
                    if csys_record["linear0"]["units"][0] == "arcsec":
                        ax.set_xlabel("Solar X (arcsec)")
                        ax.set_ylabel("Solar Y (arcsec)")
                    elif csys_record["linear0"]["units"][0] == "arcmin":
                        ax.set_xlabel("Solar X (arcmin)")
                        ax.set_ylabel("Solar Y (arcmin)")
                    elif csys_record["linear0"]["units"][0] == "deg":
                        ax.set_xlabel("Solar X (deg)")
                        ax.set_ylabel("Solar Y (deg)")
                    else:
                        ax.set_xlabel("Solar X")
                        ax.set_ylabel("Solar Y")
                elif wcs_obj.wcs.ctype[0] == "RA---TAN":
                    ax.set_xlabel("Right Ascension (J2000)")
                    ax.set_ylabel("Declination (J2000)")
                else:
                    ax.set_xlabel("Right Ascension (J2000)")
                    ax.set_ylabel("Declination (J2000)")
                if (
                    hasattr(self, "show_grid_checkbox")
                    and self.show_grid_checkbox.isChecked()
                ):
                    ax.coords.grid(
                        True,
                        color=self.grid_style.get("color", "white"),
                        alpha=self.grid_style.get("alpha", 0.5),
                        linestyle=self.grid_style.get("linestyle", "--"),
                    )
                else:
                    ax.coords.grid(False)
                if (
                    wcs_obj.wcs.ctype[0] == "RA---SIN"
                    or wcs_obj.wcs.ctype[0] == "RA---TAN"
                ):
                    ax.coords[0].set_major_formatter("hh:mm:ss.s")
                    ax.coords[1].set_major_formatter("dd:mm:ss")
                ax.tick_params(axis="both", which="major", labelsize=10)
            except Exception as e:
                logger.error(f"Error setting up WCS axes: {e}")
                self.show_status_message(f"Error setting up WCS axes: {e}")
                ax = self.figure.add_subplot(111)
                im = ax.imshow(
                    transposed_data,
                    origin="lower",
                    cmap=cmap,
                    norm=norm,
                    interpolation=interpolation,
                    aspect="auto",
                )
                self.image_plot = im
                ax.set_xlabel("Pixel X")
                ax.set_ylabel("Pixel Y")
        else:
            ax = self.figure.add_subplot(111)
            im = ax.imshow(
                transposed_data,
                origin="lower",
                cmap=cmap,
                norm=norm,
                interpolation=interpolation,
                aspect="auto",
            )
            self.image_plot = im
            ax.set_xlabel("Pixel X")
            ax.set_ylabel("Pixel Y")
        return ax, im

    def _set_plot_title(self, ax, fits_flag, header, csys_record):
        """Title with observation time, frequency / instrument."""
        # Display the image time in UTC and freq in MHz as a title
        if self.current_image_data is not None:
            # Get the image time and frequency
            try:
                temp_flag = False
                image_time = None
                image_freq = None
                if fits_flag:
                    try:
                        # Check both DATE-OBS (standard) and DATE_OBS (IRIS uses this)
                        image_time = (
                            header.get("DATE-OBS")
                            or header.get("DATE_OBS")
                            or header.get("STARTOBS")
                        )
                        if header.get("TELESCOP") == "SOHO" and header.get("TIME-OBS"):
                            image_time = f"{image_time}T{header['TIME-OBS']}"
                        # Keep upto one decimal place if image_time seconds have more than one decimal place
                        if image_time and "T" in str(image_time):
                            date = image_time.split("T")[0]
                            time_str = image_time.split("T")[1]
                            time_parts = time_str.split(":")
                            seconds = time_parts[-1]
                            if "." in seconds:
                                seconds = seconds[:4]
                            image_time = (
                                f"{date}T{time_parts[0]}:{time_parts[1]}:{seconds}"
                            )
                        temp_flag = True
                    except Exception as e:
                        logger.error(f"Error getting image time: {e}")
                        self.show_status_message(f"Error getting image time: {e}")
                        image_time = None

                    try:
                        image_freq = header.get("FREQ")
                        if image_freq is not None:
                            freq_unit = header.get("FREQUNIT")
                            if freq_unit == "Hz":
                                image_freq = f"{image_freq * 1e-6:.2f} MHz"
                            else:
                                image_freq = f"{image_freq:.2f} {freq_unit}"
                    except Exception as e:
                        logger.error(f"Error getting image frequency: {e}")
                        self.show_status_message(f"Error getting image frequency: {e}")
                        image_freq = None

                if "spectral2" in csys_record and image_freq is None:
                    spectral2 = csys_record["spectral2"]
                    wcs = spectral2.get("wcs", {})
                    frequency_ref = wcs.get("crval", None)
                    frequency_unit = spectral2.get("unit", None)
                    if frequency_unit == "Hz":
                        image_freq = f"{frequency_ref * 1e-6:.2f} MHz"
                    else:
                        image_freq = f"{frequency_ref:.2f} {frequency_unit}"

                if not temp_flag:
                    t = casa_obs_time(csys_record)
                    if t is not None:
                        t.precision = 1
                        image_time = t.iso
                    else:
                        image_time = None

                if fits_flag:
                    if (
                        header.get("TELESCOP") == "SOHO"
                        and header.get("INSTRUME") == "LASCO"
                    ):
                        title = f"{image_time} | {header['TELESCOP']} {header['INSTRUME']} {header.get('DETECTOR', '')}"
                    elif (
                        header.get("TELESCOP") == "SOHO"
                        and header.get("INSTRUME") == "EIT"
                    ):
                        wl = header.get("WAVELNTH", "")
                        title = (
                            f"{image_time} | {header['TELESCOP']} {header['INSTRUME']} {wl} Å"
                            if wl
                            else f"{image_time} | {header['TELESCOP']} {header['INSTRUME']}"
                        )
                    elif (
                        header.get("TELESCOP") == "SOHO"
                        and header.get("INSTRUME") == "MDI"
                    ):
                        title = (
                            f"{image_time} | {header['TELESCOP']} {header['INSTRUME']}"
                        )
                    elif header.get("TELESCOP") == "SDO/AIA":
                        title = f"{image_time} | {header['TELESCOP']} {header['WAVELNTH']} $\\AA$"
                    elif header.get("TELESCOP") == "SDO/HMI":
                        title = f"{image_time} | {header['TELESCOP']}"
                    elif (
                        header.get("INSTRUME") == "SJI"
                        or header.get("TELESCOP") == "IRIS"
                    ):
                        # IRIS SJI
                        wl = header.get("TWAVE1", header.get("WAVELNTH", ""))
                        title = (
                            f"{image_time} | IRIS SJI {wl} Å"
                            if wl
                            else f"{image_time} | IRIS SJI"
                        )
                    elif "SUVI" in str(header.get("INSTRUME", "")):
                        # GOES SUVI
                        sat = header.get("TELESCOP", "GOES")
                        wl = header.get("WAVELNTH", "")
                        title = (
                            f"{image_time} | {sat} SUVI {wl} Å"
                            if wl
                            else f"{image_time} | {sat} SUVI"
                        )
                    elif "GONG" in str(header.get("TELESCOP", "")) or "GONG" in str(
                        header.get("INSTRUME", "")
                    ):
                        # GONG
                        title = f"{image_time} | GONG Magnetogram"
                    elif (
                        header.get("TELESCOP") == "STEREO"
                        and header.get("INSTRUME") == "SECCHI"
                    ):
                        # STEREO SECCHI (COR1, COR2, EUVI)
                        obs = header.get("OBSRVTRY", "STEREO").replace("_", "-")
                        det = header.get("DETECTOR", "")
                        title = f"{image_time} | {obs} {det}"

                    elif image_time is not None and image_freq is not None:
                        title = f"Time: {image_time} | Freq: {image_freq}"
                    elif image_time is not None and image_freq is None:
                        title = f"Time: {image_time}"
                    elif image_time is None and image_freq is not None:
                        title = f"Freq: {image_freq}"
                    else:
                        title = (
                            os.path.basename(self.imagename)
                            if self.imagename
                            else "No Image"
                        )

                elif image_time is not None and image_freq is None:
                    title = f"Time: {image_time}"
                elif image_time is None and image_freq is not None:
                    title = f"Freq: {image_freq}"
                elif image_time is not None and image_freq is not None:
                    title = f"Time: {image_time} | Freq: {image_freq}"
                else:
                    title = (
                        os.path.basename(self.imagename)
                        if self.imagename
                        else "No Image"
                    )
                ax.set_title(self._fit_title(title))
            except Exception as e:
                logger.error(f"Error getting title: {e}")
                self.show_status_message(f"Error getting title: {e}")
                title = (
                    os.path.basename(self.imagename) if self.imagename else "No Image"
                )
                ax.set_title(self._fit_title(title))

    def _add_colorbar(self, ax, im, stretch, vmin_val, vmax_val):
        """Add the colorbar (dropping out-of-range ticks for non-linear stretches)."""
        # Format the time and frequency as a title
        # For non-linear stretches, filter ticks to only show those within data range
        if stretch in ("power", "histeq", "sqrt", "arcsinh", "log"):
            cb = self.figure.colorbar(
                im,
                ax=ax,
                aspect=30,
            )
            # Remove ticks that are outside the actual data range
            from ..processing.plot_style import drop_outside_ticks

            drop_outside_ticks(cb, vmin_val, vmax_val)
        else:
            cb = self.figure.colorbar(
                im,
                ax=ax,
                aspect=30,
            )
        self.colorbar = cb
        return cb

    def _apply_plot_customization(self, ax, cb):
        """Apply self.plot_settings: labels, fonts, colors, ticks, borders."""
        from ..processing.plot_style import apply_plot_settings
        from ..ui.styles import theme_manager

        def on_error(message):
            logger.error(f"{message}")
            self.show_status_message(message)

        apply_plot_settings(
            ax,
            cb,
            self.plot_settings,
            self.figure,
            dark=theme_manager.is_dark,
            bunit=getattr(self, "_current_bunit", None),
            on_error=on_error,
        )

    def _draw_beam(self, ax):
        """Draw the restoring beam ellipse if enabled."""
        # Draw beam if available
        if self.psf and self.show_beam_checkbox.isChecked():
            try:
                if isinstance(self.psf["major"]["value"], list):
                    major_deg = float(self.psf["major"]["value"][0]) / 3600.0
                else:
                    major_deg = float(self.psf["major"]["value"]) / 3600.0

                if isinstance(self.psf["minor"]["value"], list):
                    minor_deg = float(self.psf["minor"]["value"][0]) / 3600.0
                else:
                    minor_deg = float(self.psf["minor"]["value"]) / 3600.0

                if isinstance(self.psf["positionangle"]["value"], list):
                    pa_deg = float(self.psf["positionangle"]["value"][0]) - 90
                else:
                    pa_deg = float(self.psf["positionangle"]["value"]) - 90

                if self.current_wcs:
                    cdelt = self.current_wcs.increment()["numeric"][0:2]
                    if isinstance(cdelt, list):
                        cdelt = [float(c) for c in cdelt]
                    _hdr = getattr(self, "_cached_fits_header", None) or {}
                    _is_solar = "SOLAR-X" in str(_hdr.get("CTYPE1", "")).upper()
                    if _is_solar:
                        cdelt = np.array(cdelt) / 3600.0  # arcsec -> degrees
                    else:
                        cdelt = np.array(cdelt) * 180 / np.pi  # radians -> degrees
                    dx_deg = abs(cdelt[0])
                else:
                    dx_deg = 1.0 / 3600

                major_pix = major_deg / dx_deg
                minor_pix = minor_deg / dx_deg

                xlim = ax.get_xlim()
                ylim = ax.get_ylim()
                view_width = xlim[1] - xlim[0]
                view_height = ylim[1] - ylim[0]
                margin_x = view_width * 0.05
                margin_y = view_height * 0.05
                beam_x = xlim[0] + margin_x + major_pix / 2
                beam_y = ylim[0] + margin_y + minor_pix / 2

                ellipse = Ellipse(
                    (beam_x, beam_y),
                    width=major_pix,
                    height=minor_pix,
                    angle=pa_deg,
                    fill=True,
                    edgecolor=self.beam_style.get("edgecolor", "black"),
                    linewidth=self.beam_style.get("linewidth", 1.5),
                    facecolor=self.beam_style.get("facecolor", "white"),
                    alpha=self.beam_style.get("alpha", 0.4),
                )
                ax.add_patch(ellipse)
                self.beam_properties = {
                    "major_pix": major_pix,
                    "minor_pix": minor_pix,
                    "pa_deg": pa_deg,
                    "margin": 0.05,
                }
            except Exception as e:
                logger.error(f"Error drawing beam: {e}")
                self.show_status_message(f"Error drawing beam: {e}")

    def _update_beam_position(self, ax):
        # Don't draw beam if no PSF or beam properties
        if (
            not self.psf
            or not hasattr(self, "beam_properties")
            or not self.beam_properties
        ):
            return

        for patch in list(ax.patches):
            if (
                isinstance(patch, Ellipse)
                and not getattr(patch, "_shape_annotation", False)
                and not getattr(patch, "_solar_disk", False)
            ):
                patch.remove()

        xlim = ax.get_xlim()
        ylim = ax.get_ylim()

        major_pix = self.beam_properties["major_pix"]
        minor_pix = self.beam_properties["minor_pix"]
        pa_deg = self.beam_properties["pa_deg"]

        view_width = xlim[1] - xlim[0]
        view_height = ylim[1] - ylim[0]

        margin_x = view_width * 0.05
        margin_y = view_height * 0.05

        beam_x = xlim[0] + margin_x + major_pix / 2
        beam_y = ylim[0] + margin_y + minor_pix / 2

        ellipse = Ellipse(
            (beam_x, beam_y),
            width=major_pix,
            height=minor_pix,
            angle=pa_deg,
            fill=True,
            edgecolor=self.beam_style.get("edgecolor", "black"),
            facecolor=self.beam_style.get("facecolor", "white"),
            linewidth=self.beam_style.get("linewidth", 1.5),
            alpha=self.beam_style.get("alpha", 0.4),
        )
        ax.add_patch(ellipse)

    def _apply_layout_settings(self, tight_layout):
        """Matplotlib's tight layout, if turned on in Plot Customization
        (set again whenever the canvas is resized); otherwise the custom
        padding set there, or margins sized for the fonts and the canvas
        when that padding is the default."""
        ps = self.plot_settings
        if ps.get("use_tight_layout", False):
            self.figure.tight_layout()
            return
        pads = {k: ps.get(k, DEFAULT_PLOT_SETTINGS[k]) for k in _PAD_KEYS}
        if all(pads[k] == DEFAULT_PLOT_SETTINGS[k] for k in _PAD_KEYS):
            pads.update(self._margins_for_fonts())
        self.figure.subplots_adjust(
            left=pads["pad_left"],
            right=pads["pad_right"],
            top=pads["pad_top"],
            bottom=pads["pad_bottom"],
            wspace=ps.get("pad_wspace", 0.2),
            hspace=ps.get("pad_hspace", 0.2),
        )

    def _fit_title(self, title):
        """``title`` on two lines ("Time: ...\\nFreq: ...") when the canvas
        is too narrow for it on one: Matplotlib centres a title over the
        axes and would cut a long one off at both sides."""
        width = self.figure.get_size_inches()[0] * self.figure.dpi
        if " | " in title and width < 1100:
            return title.replace(" | ", "\n", 1)
        return title

    def _margins_for_fonts(self):
        """Subplot margins (fractions of the figure) leaving room for the
        title above, the tick labels and axis label left and below, and the
        colorbar's tick labels and label on the right, at the current font
        sizes and canvas size."""
        ps = self.plot_settings
        width, height = self.figure.get_size_inches() * self.figure.dpi
        px = self.figure.dpi / 72.0  # pixels per point
        tick = ps.get("axis_tick_fontsize", 10)
        label = ps.get("axis_label_fontsize", 12)
        title = ps.get("title_fontsize", 12)
        cb_tick = ps.get("colorbar_tick_fontsize", 10)
        cb_label = ps.get("colorbar_label_fontsize", 10)
        axes = self.figure.axes[0] if self.figure.axes else None
        title_lines = axes.get_title().count("\n") + 1 if axes is not None else 1
        top_px = title * 1.5 * title_lines * px + 10
        bottom_px = (tick * 1.4 + label * 1.6) * px + 12
        left_px = (tick * 0.65 * 8 + label * 2.0) * px + 12
        right_px = (cb_tick * 0.6 * 6 + cb_label * 1.6) * px + 10
        return {
            "pad_left": min(0.35, left_px / max(width, 1)),
            "pad_right": max(0.6, 1 - right_px / max(width, 1)),
            "pad_top": max(0.7, 1 - top_px / max(height, 1)),
            "pad_bottom": min(0.3, bottom_px / max(height, 1)),
        }

    def relayout_for_size(self):
        """Margins again for a canvas of a new size (the resize hook)."""
        if self.current_image_data is not None and self.figure.axes:
            self._apply_layout_settings(False)

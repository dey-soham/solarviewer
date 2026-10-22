"""
Reading pixel data (one Stokes plane, optional thresholding) from FITS/CASA images.

Planes are indexed [x, y] (CASA's axis order; the viewer draws them
transposed), and an RMS box is (x1, x2, y1, y2) in the same pixels.
"""

import logging
from typing import NamedTuple, Optional

import numpy as np

from .dependencies import CASA_AVAILABLE, IA
from .fits_io import is_fits, readable_image_path
from .image_meta import (
    CasaInfo,
    casa_info,
    file_signature,
    fits_header,
    remember_casa_info,
    remember_fits_header,
    sky_axes,
)
from .stokes import (
    StokesUnavailable,
    casa_stokes_labels,
    fits_stokes_axis,
    fits_stokes_labels,
    get_available_stokes,
    missing_planes,
    plane_index,
    planes_needed,
    stokes_product,
)

logger = logging.getLogger(__name__)


class ImageUnreadable(Exception):
    """Neither CASA nor the FITS reader can read an image file."""


class ImageRead(NamedTuple):
    """Everything a load reads from an image file (see :func:`read_image`),
    in a form a worker process can send back."""

    signature: Optional[tuple]  # file_signature() of the file read
    stokes: str  # the Stokes product read
    available: list  # the Stokes planes the image has (get_available_stokes)
    pix: np.ndarray  # the product's pixels, [x, y]
    csys_record: Optional[dict]  # coordsys.torecord(); None from the FITS reader
    psf: Optional[dict]  # restoring beam; None from the FITS reader
    info: Optional[CasaInfo]  # what ia.open reports; None from the FITS reader
    header: Optional[object]  # primary header of a FITS file

    def remember(self):
        """Cache the metadata read with the pixels in this process, so the
        viewer's header and coordinate readers need not open the file again."""
        if self.signature is None:
            return
        if self.info is not None:
            remember_casa_info(self.signature, self.info)
        if self.header is not None:
            remember_fits_header(self.signature, self.header)


def downsample_factor(sky_shape, target_size):
    """The factor that brings the larger sky dimension of ``sky_shape`` down
    to about ``target_size`` pixels (1 for no downsampling)."""
    if target_size > 0 and max(sky_shape) > target_size:
        return max(1, int(np.ceil(max(sky_shape) / target_size)))
    return 1


def get_pixel_values_from_image(
    imagename,
    stokes,
    thres,
    rms_box=(0, 200, 0, 130),
    downsample=1,
    target_size=0,
):
    """
    Retrieve pixel values from a CASA image or FITS file.

    Only the planes the product needs are read: their Stokes planes, the
    first channel, and with ``downsample`` every Nth pixel.

    Parameters:
      imagename : str
         Path to the CASA image directory or FITS file.
      stokes : str
         The stokes parameter to extract ("I", "Q", "U", "V", "L", "Lfrac", "Vfrac", "Q/I", "U/I", "U/V", or "PANG").
      thres : float
         Threshold, in units of the RMS, for the derived parameters.
      rms_box : tuple, optional
         Region (x1, x2, y1, y2), in the pixels returned, for RMS estimation.
      downsample : int, optional
         Fixed factor by which to downsample. Default is 1 (no downsampling).
         If target_size is set, this is ignored.
      target_size : int, optional
         Target maximum dimension in pixels. If > 0, downsample factor is calculated
         automatically to achieve approximately this size. Default is 0 (disabled).

    Returns:
      pix : numpy.ndarray
         The extracted pixel data.
      csys : object
         Coordinate system object from CASA.
      psf : object
         Beam information from CASA.

    Raises:
      StokesUnavailable: If the image has no plane the product needs.
      RuntimeError: For errors in reading the image.
    """

    if not CASA_AVAILABLE:
        raise RuntimeError("CASA is not available")

    labels_needed = planes_needed(stokes)
    signature = file_signature(imagename)
    try:
        ia_tool = IA()
        ia_tool.open(readable_image_path(imagename))
    except Exception as e:
        raise RuntimeError(f"Failed to open image {imagename}: {e}")

    try:
        summary = ia_tool.summary()
        # Spare the viewer's header/coordsys readers another open of this file
        try:
            remember_casa_info(
                signature, CasaInfo.from_image_tool(ia_tool, summary).copy()
            )
        except Exception:
            pass
        dimension_names = summary.get("axisnames")
        if dimension_names is None:
            raise ValueError("Image summary does not contain 'axisnames'")
        dimension_names = [str(name) for name in dimension_names]
        shape = [int(n) for n in summary.get("shape")]
        x_idx, y_idx = sky_axes(dimension_names)

        # Calculate smart downsample factor based on image dimensions
        if target_size > 0:
            downsample = downsample_factor((shape[x_idx], shape[y_idx]), target_size)

        csys = ia_tool.coordsys()
        psf = ia_tool.restoringbeam()
        stokes_idx = (
            dimension_names.index("Stokes") if "Stokes" in dimension_names else None
        )
        labels = casa_stokes_labels(csys) if stokes_idx is not None else None
        # getchunk returns float64 even for a float32 image: keep it float32
        # (half the memory)
        single = ia_tool.pixeltype() == "float"

        def read_plane(label):
            """The ``label`` Stokes plane, first channel, as [x, y]."""
            index = plane_index(labels, label)
            # The first channel of every axis but the sky axes ...
            blc = [0] * len(shape)
            trc = [n - 1 if axis in (x_idx, y_idx) else 0 for axis, n in enumerate(shape)]
            # ... this Stokes plane, and every ``downsample``th pixel on the sky
            if index is not None:
                blc[stokes_idx] = trc[stokes_idx] = index
            inc = [1] * len(shape)
            inc[x_idx] = inc[y_idx] = downsample
            chunk = ia_tool.getchunk(blc=blc, trc=trc, inc=inc)
            plane = chunk[
                tuple(
                    slice(None) if axis in (x_idx, y_idx) else 0
                    for axis in range(len(shape))
                )
            ]
            return plane.astype(np.float32) if single else plane

        planes = {label: read_plane(label) for label in labels_needed}

        # Adjust coordinate system for downsampled data
        if downsample > 1:
            # Get current increment and reference pixel
            current_inc = csys.increment()
            current_refpix = csys.referencepixel()

            # Scale increment (pixel size) by downsample factor
            new_inc_vals = [v * downsample for v in current_inc["numeric"]]
            csys.setincrement(value=new_inc_vals)

            # Adjust reference pixel position (divide by downsample factor)
            new_refpix_vals = [(v / downsample) for v in current_refpix["numeric"]]
            csys.setreferencepixel(value=new_refpix_vals)
    except RuntimeError:
        ia_tool.close()
        raise
    except Exception as e:
        ia_tool.close()
        raise RuntimeError(f"Error reading image metadata: {e}")
    ia_tool.close()

    pix = stokes_product(stokes, planes, thres, rms_box, csys)
    return pix, csys, psf


def read_fits_stokes(imagename, stokes, thres, rms_box=(0, 200, 0, 130)):
    """
    The ``stokes`` product of a FITS file read with astropy, for files CASA
    cannot read. Returns an [x, y] plane like get_pixel_values_from_image,
    computed the same way.
    """
    from astropy.io import fits

    with fits.open(readable_image_path(imagename), memmap=True) as hdul:
        header = hdul[0].header
        data = hdul[0].data
        labels = fits_stokes_labels(header)
        stokes_axis = fits_stokes_axis(header)

        def plane(name):
            """The ``name`` plane, first channel of every other axis, as [x, y]."""
            # NumPy axes are the FITS axes reversed: the last two are y, x
            index = [0] * (data.ndim - 2) + [slice(None), slice(None)]
            if stokes_axis is not None:
                index[data.ndim - stokes_axis] = plane_index(labels, name)
            else:
                plane_index(None, name)  # only Stokes I without a Stokes axis
            return np.array(data[tuple(index)], dtype=float).T

        planes = {name: plane(name) for name in planes_needed(stokes)}
    return stokes_product(stokes, planes, thres, rms_box)


def read_image(
    imagename, stokes, threshold, rms_box, target_size, fallback_stokes=None
):
    """
    Read what the viewer shows of ``imagename``, as an :class:`ImageRead`:
    the ``stokes`` product's pixels (downsampled to about ``target_size``
    pixels if that is not 0), its coordinates, beam and header.

    Every file access of a load happens here, so that it can run in a worker
    process (see ``ui/background`` and ``processing/prefetch``). If the image
    lacks a plane ``stokes`` needs and ``fallback_stokes`` is given, that
    product is read instead. FITS files CASA cannot read are read with
    astropy, without coordinates or beam.

    Raises StokesUnavailable, or ImageUnreadable if neither reader can read it.
    """
    signature = file_signature(imagename)
    available = get_available_stokes(imagename)
    if fallback_stokes is not None and missing_planes(stokes, available):
        stokes = fallback_stokes
    try:
        pix, csys, psf = get_pixel_values_from_image(
            imagename, stokes, threshold, rms_box=rms_box, target_size=target_size
        )
        csys_record = csys.torecord()
        info = casa_info(imagename)  # cached by the read above
    except StokesUnavailable:
        # The image lacks a plane this product needs: say so, rather than
        # showing another plane under this one's name
        raise
    except Exception as e:
        from astropy.io import fits

        # FITS files CASA cannot read
        csys_record = psf = info = None
        try:
            pix = read_fits_stokes(imagename, stokes, threshold, rms_box)
        except StokesUnavailable:
            raise
        except Exception as fits_err:
            logger.error(f"Error loading FITS data: {fits_err}")
            # Last resort: the raw pixels of a 2D image, as [x, y]
            try:
                raw = np.squeeze(fits.getdata(readable_image_path(imagename)))
            except Exception as e2:
                raise ImageUnreadable(
                    f"Neither CASA nor the FITS reader can read it. CASA: {e}. "
                    f"FITS: {e2}"
                ) from e2
            if raw.ndim != 2:
                raise ImageUnreadable(
                    f"CASA cannot read it ({e}), and the FITS reader found no "
                    f"2D image in it ({fits_err})"
                ) from fits_err
            pix = raw.T

    header = None
    if is_fits(imagename):
        try:
            header = fits_header(imagename)
        except Exception:
            pass
    return ImageRead(signature, stokes, available, pix, csys_record, psf, info, header)

"""
Reading pixel data (one Stokes plane, optional thresholding) from FITS/CASA images.

Planes are indexed [x, y] (CASA's axis order; the viewer draws them
transposed), and an RMS box is (x1, x2, y1, y2) in the same pixels.
"""

import numpy as np

from .dependencies import CASA_AVAILABLE, IA
from .fits_io import readable_image_path
from .image_meta import CasaInfo, file_signature, remember_casa_info, sky_axes
from .stokes import casa_stokes_labels, plane_index, planes_needed, stokes_product


class ImageUnreadable(Exception):
    """Neither CASA nor the FITS reader can read an image file."""


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
            return chunk[
                tuple(
                    slice(None) if axis in (x_idx, y_idx) else 0
                    for axis in range(len(shape))
                )
            ]

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

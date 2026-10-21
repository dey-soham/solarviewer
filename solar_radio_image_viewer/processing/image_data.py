"""
Reading pixel data (one Stokes plane, optional thresholding) from FITS/CASA images.
"""

import os

import numpy as np

from .dependencies import CASA_AVAILABLE, IA
from .immath import run_immath_subprocess
from .rms import estimate_rms_near_Sun, remove_pixels_away_from_sun
from .fits_io import readable_image_path
from .image_meta import CasaInfo, file_signature, remember_casa_info


# TODO: Handle single stokes case, return flag so that some features can be disabled
def get_pixel_values_from_image(
    imagename,
    stokes,
    thres,
    rms_box=(0, 200, 0, 130),
    stokes_map={"I": 0, "Q": 1, "U": 2, "V": 3},
    downsample=1,
    target_size=0,
):
    """
    Retrieve pixel values from a CASA image with proper error handling and dimension checks.

    Parameters:
      imagename : str
         Path to the CASA image directory.
      stokes : str
         The stokes parameter to extract ("I", "Q", "U", "V", "L", "Lfrac", "Vfrac", "Q/I", "U/I", "U/V", or "PANG").
      thres : float
         Threshold value.
      rms_box : tuple, optional
         Region coordinates (x1, x2, y1, y2) for RMS estimation.
      stokes_map : dict, optional
         Mapping of standard stokes parameters to their corresponding axis indices.
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
      RuntimeError: For errors in reading the image or if required dimensions are missing.
    """

    if not CASA_AVAILABLE:
        raise RuntimeError("CASA is not available")

    single_stokes_flag = False
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
        dimension_shapes = summary.get("shape")
        if dimension_names is None:
            raise ValueError("Image summary does not contain 'axisnames'")
        # Ensure we can index; convert to numpy array if needed
        dimension_names = np.array(dimension_names)

        # Calculate smart downsample factor based on image dimensions
        if target_size > 0 and len(dimension_shapes) >= 2:
            # Get spatial dimensions (first two axes are typically RA/Dec or X/Y)
            max_spatial_dim = max(dimension_shapes[0], dimension_shapes[1])
            if max_spatial_dim > target_size:
                # Calculate factor to achieve target size
                downsample = max(1, int(np.ceil(max_spatial_dim / target_size)))

        if "Right Ascension" in dimension_names:
            try:
                ra_idx = int(np.where(dimension_names == "Right Ascension")[0][0])
            except IndexError:
                raise ValueError("Right Ascension axis not found in image summary.")

            try:
                dec_idx = int(np.where(dimension_names == "Declination")[0][0])
            except IndexError:
                raise ValueError("Declination axis not found in image summary.")

            if "Stokes" in dimension_names:
                stokes_idx = int(np.where(dimension_names == "Stokes")[0][0])
                if dimension_shapes[stokes_idx] == 1:
                    single_stokes_flag = True
            else:
                # Assume single stokes; set index to 0
                stokes_idx = None
                single_stokes_flag = True

            if "Frequency" in dimension_names:
                freq_idx = int(np.where(dimension_names == "Frequency")[0][0])
            else:
                # If Frequency axis is missing, assume index 0
                freq_idx = None

            # Use strided reading for fast downsampling (reads every Nth pixel from disk)
            # IMPORTANT: Only downsample spatial axes, not Stokes or Frequency
            if downsample > 1:
                inc = [1] * len(dimension_shapes)  # Start with no downsampling
                inc[ra_idx] = downsample  # Downsample RA axis
                inc[dec_idx] = downsample  # Downsample Dec axis
                # Keep Stokes and Frequency at 1 to read all values
                data = ia_tool.getchunk(inc=inc)
            else:
                data = ia_tool.getchunk()
            psf = ia_tool.restoringbeam()
            csys = ia_tool.coordsys()

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

        if "SOLAR-X" in dimension_names:
            try:
                ra_idx = int(np.where(dimension_names == "SOLAR-X")[0][0])
            except IndexError:
                raise ValueError("SOLAR-X axis not found in image summary.")
            try:
                dec_idx = int(np.where(dimension_names == "SOLAR-Y")[0][0])
            except IndexError:
                raise ValueError("SOLAR-Y axis not found in image summary.")

            if "Stokes" in dimension_names:
                stokes_idx = int(np.where(dimension_names == "Stokes")[0][0])
                if dimension_shapes[stokes_idx] == 1:
                    single_stokes_flag = True
            else:
                # Assume single stokes; set index to 0
                stokes_idx = None
            if "Frequency" in dimension_names:
                freq_idx = int(np.where(dimension_names == "Frequency")[0][0])
            else:
                # If Frequency axis is missing, assume index 0
                freq_idx = None
            # Use strided reading for fast downsampling (reads every Nth pixel from disk)
            # IMPORTANT: Only downsample spatial axes, not Stokes or Frequency
            if downsample > 1:
                inc = [1] * len(dimension_shapes)  # Start with no downsampling
                inc[ra_idx] = downsample  # Downsample SOLAR-X axis
                inc[dec_idx] = downsample  # Downsample SOLAR-Y axis
                # Keep Stokes and Frequency at 1 to read all values
                data = ia_tool.getchunk(inc=inc)
            else:
                data = ia_tool.getchunk()
            psf = ia_tool.restoringbeam()
            csys = ia_tool.coordsys()

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

    except Exception as e:
        ia_tool.close()
        raise RuntimeError(f"Error reading image metadata: {e}")
    ia_tool.close()

    # Verify that our slice indices are within data dimensions
    n_dims = len(data.shape)
    if stokes_idx is not None and (stokes_idx >= n_dims):
        raise RuntimeError(
            "The determined axis index is out of bounds for the image data."
        )
    if freq_idx is not None and (freq_idx >= n_dims):
        raise RuntimeError(
            "The determined axis index is out of bounds for the image data."
        )

    # Process based on stokes type
    if stokes in ["I", "Q", "U", "V"]:
        idx = stokes_map.get(stokes)
        if idx is None:
            raise ValueError(f"Unknown Stokes parameter: {stokes}")
        slice_list = [slice(None)] * n_dims
        if stokes_idx is not None:
            if single_stokes_flag:
                if stokes != "I":
                    raise RuntimeError(
                        "The image is single stokes, but the Stokes parameter is not 'I'."
                    )
            slice_list[stokes_idx] = idx
        if freq_idx is not None:
            slice_list[freq_idx] = 0
        pix = data[tuple(slice_list)]
    elif stokes == "L":
        if stokes_idx is None:
            raise RuntimeError("The image does not have a Stokes axis.")
        elif single_stokes_flag:
            raise RuntimeError(
                "The image is single stokes, but the Stokes parameter is not 'I'."
            )
        slice_list_Q = [slice(None)] * n_dims
        slice_list_U = [slice(None)] * n_dims
        slice_list_Q[stokes_idx] = 1
        slice_list_U[stokes_idx] = 2
        slice_list_Q[freq_idx] = 0
        slice_list_U[freq_idx] = 0
        pix_Q = data[tuple(slice_list_Q)]
        pix_U = data[tuple(slice_list_U)]
        pix = np.sqrt(pix_Q**2 + pix_U**2)
    elif stokes == "Lfrac":
        if stokes_idx is None:
            raise RuntimeError("The image does not have a Stokes axis.")
        elif single_stokes_flag:
            raise RuntimeError(
                "The image is single stokes, but the Stokes parameter is not 'I'."
            )
        outfile = "temp_p_map.im"
        try:
            run_immath_subprocess(imagename=imagename, outfile=outfile, mode="lpoli")
            p_rms = estimate_rms_near_Sun(outfile, "I", rms_box)
        except Exception as e:
            raise RuntimeError(f"Error generating polarization map: {e}")
        finally:
            os.system(f"rm -rf {outfile}")
        slice_list_Q = [slice(None)] * n_dims
        slice_list_U = [slice(None)] * n_dims
        slice_list_I = [slice(None)] * n_dims
        slice_list_Q[stokes_idx] = 1
        slice_list_U[stokes_idx] = 2
        slice_list_I[stokes_idx] = 0
        slice_list_Q[freq_idx] = 0
        slice_list_U[freq_idx] = 0
        slice_list_I[freq_idx] = 0
        pix_Q = data[tuple(slice_list_Q)]
        pix_U = data[tuple(slice_list_U)]
        pix_I = data[tuple(slice_list_I)]
        pvals = np.sqrt(pix_Q**2 + pix_U**2)
        mask = pvals < (thres * p_rms)
        pvals[mask] = 0
        pix = pvals / pix_I
        pix = remove_pixels_away_from_sun(pix, csys, 55)
    elif stokes == "Vfrac":
        if stokes_idx is None:
            raise RuntimeError("The image does not have a Stokes axis.")
        elif single_stokes_flag:
            raise RuntimeError(
                "The image is single stokes, but the Stokes parameter is not 'I'."
            )
        slice_list_V = [slice(None)] * n_dims
        slice_list_I = [slice(None)] * n_dims
        slice_list_V[stokes_idx] = 3
        slice_list_I[stokes_idx] = 0
        if freq_idx is not None:
            slice_list_V[freq_idx] = 0
            slice_list_I[freq_idx] = 0
        pix_V = data[tuple(slice_list_V)]
        pix_I = data[tuple(slice_list_I)]
        v_rms = estimate_rms_near_Sun(imagename, "V", rms_box)
        mask = np.abs(pix_V) < (thres * v_rms)
        pix_V[mask] = 0
        pix = pix_V / pix_I
        pix = remove_pixels_away_from_sun(pix, csys, 55)
    elif stokes == "Q/I":
        if stokes_idx is None:
            raise RuntimeError("The image does not have a Stokes axis.")
        elif single_stokes_flag:
            raise RuntimeError(
                "The image is single stokes, but the Stokes parameter is not 'I'."
            )
        q_rms = estimate_rms_near_Sun(imagename, "Q", rms_box)
        slice_list_Q = [slice(None)] * n_dims
        slice_list_I = [slice(None)] * n_dims
        slice_list_Q[stokes_idx] = 1
        slice_list_I[stokes_idx] = 0
        if freq_idx is not None:
            slice_list_Q[freq_idx] = 0
            slice_list_I[freq_idx] = 0
        pix_Q = data[tuple(slice_list_Q)]
        mask = np.abs(pix_Q) < (thres * q_rms)
        pix_Q[mask] = 0
        pix_I = data[tuple(slice_list_I)]
        pix = pix_Q / pix_I
        pix = remove_pixels_away_from_sun(pix, csys, 55)
    elif stokes == "U/I":
        if stokes_idx is None:
            raise RuntimeError("The image does not have a Stokes axis.")
        elif single_stokes_flag:
            raise RuntimeError(
                "The image is single stokes, but the Stokes parameter is not 'I'."
            )
        u_rms = estimate_rms_near_Sun(imagename, "U", rms_box)
        slice_list_U = [slice(None)] * n_dims
        slice_list_I = [slice(None)] * n_dims
        slice_list_U[stokes_idx] = 2
        slice_list_I[stokes_idx] = 0
        if freq_idx is not None:
            slice_list_U[freq_idx] = 0
            slice_list_I[freq_idx] = 0
        pix_U = data[tuple(slice_list_U)]
        mask = np.abs(pix_U) < (thres * u_rms)
        pix_U[mask] = 0
        pix_I = data[tuple(slice_list_I)]
        pix = pix_U / pix_I
        pix = remove_pixels_away_from_sun(pix, csys, 55)
    elif stokes == "U/V":
        if stokes_idx is None:
            raise RuntimeError("The image does not have a Stokes axis.")
        elif single_stokes_flag:
            raise RuntimeError(
                "The image is single stokes, but the Stokes parameter is not 'I'."
            )
        u_rms = estimate_rms_near_Sun(imagename, "U", rms_box)
        slice_list_U = [slice(None)] * n_dims
        slice_list_V = [slice(None)] * n_dims
        slice_list_U[stokes_idx] = 2
        slice_list_V[stokes_idx] = 3
        if freq_idx is not None:
            slice_list_U[freq_idx] = 0
            slice_list_V[freq_idx] = 0
        pix_U = data[tuple(slice_list_U)]
        pix_V = data[tuple(slice_list_V)]
        mask = np.abs(pix_U) < (thres * u_rms)
        pix_U[mask] = 0
        pix = pix_U / pix_V
        pix = remove_pixels_away_from_sun(pix, csys, 55)
    elif stokes == "PANG":
        if stokes_idx is None:
            raise RuntimeError("The image does not have a Stokes axis.")
        elif single_stokes_flag:
            raise RuntimeError(
                "The image is single stokes, but the Stokes parameter is not 'I'."
            )
        # Get Q and U data
        slice_list_Q = [slice(None)] * n_dims
        slice_list_U = [slice(None)] * n_dims
        slice_list_Q[stokes_idx] = 1
        slice_list_U[stokes_idx] = 2
        slice_list_Q[freq_idx] = 0
        slice_list_U[freq_idx] = 0
        pix_Q = data[tuple(slice_list_Q)]
        pix_U = data[tuple(slice_list_U)]

        # Calculate polarized intensity for thresholding
        p_intensity = np.sqrt(pix_Q**2 + pix_U**2)

        # Estimate RMS for polarized intensity using L (linear polarization) estimation
        # We use Q RMS as an approximation since we can't directly estimate L RMS
        q_rms = estimate_rms_near_Sun(imagename, "Q", rms_box)
        u_rms = estimate_rms_near_Sun(imagename, "U", rms_box)
        p_rms = np.sqrt(q_rms**2 + u_rms**2)

        # Calculate polarization angle: 0.5 * arctan2(U, Q) in degrees
        pix = 0.5 * np.arctan2(pix_U, pix_Q) * 180 / np.pi

        # Apply threshold mask - only show where polarized intensity is significant
        mask = p_intensity < (thres * p_rms)
        pix[mask] = np.nan

        # Handle any infinite values by setting them to NaN
        pix = np.where(np.isinf(pix), np.nan, pix)

        # Remove pixels away from the Sun
        pix = remove_pixels_away_from_sun(pix, csys, 55)

    else:
        slice_list_I = [slice(None)] * n_dims
        slice_list_I[stokes_idx] = 0
        slice_list_I[freq_idx] = 0
        pix = data[tuple(slice_list_I)]

    return pix, csys, psf

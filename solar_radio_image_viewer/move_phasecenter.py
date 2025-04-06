import os
import numpy as np
import sys
import glob
from casatools import msmetadata, table, measures, quanta, image
from casatasks import *
from astropy.io import fits
from astropy.wcs import WCS
import scipy.ndimage as ndi
import argparse
import multiprocessing
from multiprocessing import Pool
from functools import partial
import shutil


class SolarPhaseCenter:
    """
    Class to calculate and apply phase shifts to solar images

    This class contains methods to:
    1. Calculate the difference between solar center and phase center
    2. Apply the phase shift to align the solar center with the phase center

    Parameters
    ----------
    msname : str
        Name of the measurement set
    cellsize : float
        Cell size of the image in arcsec
    imsize : int
        Size of the image in pixels
    """

    def __init__(self, msname=None, cellsize=None, imsize=None):
        self.msname = msname
        self.cellsize = cellsize  # in arcsec
        self.imsize = imsize

        # Get working directory
        self.cwd = os.getcwd()

        # Initialize rms boxes with default values
        self.rms_box = "50,50,100,75"
        self.rms_box_nearsun = "40,40,80,60"

        # Setup RMS box for calculations (near Sun and general)
        if imsize is not None and cellsize is not None:
            self.setup_rms_boxes(imsize, cellsize)

    def setup_rms_boxes(self, imsize, cellsize):
        """
        Set up RMS boxes for calculations

        Parameters
        ----------
        imsize : int
            Size of the image in pixels
        cellsize : float
            Cell size in arcsec
        """
        self.rms_box = f"50,50,{imsize-50},{int(imsize/4)}"

        # Calculate box center, ensuring it stays within valid range
        boxcenter_y = max(0, int(imsize / 2) - int(3 * 3600 / cellsize))
        ywidth = min(
            int(3600 / cellsize), int(imsize / 4)
        )  # Limit width to 1/4 of image
        boxcenter_x = int(imsize / 2)

        # Calculate box bounds, ensuring they're all within valid range
        x_min = max(0, boxcenter_x - int(imsize / 5))
        y_min = max(0, boxcenter_y - 2 * ywidth)
        x_max = min(imsize - 1, boxcenter_x + int(imsize / 5))
        y_max = min(imsize - 1, boxcenter_y + ywidth)

        # Ensure the box has some size (at least 10x10 pixels)
        if x_max - x_min < 10:
            x_max = min(imsize - 1, x_min + 10)
        if y_max - y_min < 10:
            y_max = min(imsize - 1, y_min + 10)

        self.rms_box_nearsun = f"{x_min},{y_min},{x_max},{y_max}"

    def get_phasecenter(self):
        """
        Get the phase center of the MS

        Returns
        -------
        tuple
            (radec_str, radeg, decdeg) - RA/DEC as string and degrees
        """
        if self.msname is None:
            print("Error: MS name not provided")
            return None, None, None

        ms_meta = msmetadata()
        ms_meta.open(self.msname)

        # Get field ID 0 (assuming single field)
        t = table()
        t.open(f"{self.msname}/FIELD")
        direction = t.getcol("PHASE_DIR")
        t.close()

        # Convert to degrees
        radeg = np.degrees(direction[0][0][0])
        decdeg = np.degrees(direction[0][0][1])

        # Format as strings
        ra_hms = self.deg2hms(radeg)
        dec_dms = self.deg2dms(decdeg)

        ms_meta.close()
        return [ra_hms, dec_dms], radeg, decdeg

    def deg2hms(self, ra_deg):
        """
        Convert RA from degrees to HH:MM:SS.SSS format

        Parameters
        ----------
        ra_deg : float
            RA in degrees

        Returns
        -------
        str
            RA in HH:MM:SS.SSS format
        """
        ra_hour = ra_deg / 15.0
        ra_h = int(ra_hour)
        ra_m = int((ra_hour - ra_h) * 60)
        ra_s = ((ra_hour - ra_h) * 60 - ra_m) * 60
        return f"{ra_h:02d}h{ra_m:02d}m{ra_s:.3f}s"

    def deg2dms(self, dec_deg):
        """
        Convert DEC from degrees to DD:MM:SS.SSS format

        Parameters
        ----------
        dec_deg : float
            DEC in degrees

        Returns
        -------
        str
            DEC in DD:MM:SS.SSS format
        """
        dec_sign = "+" if dec_deg >= 0 else "-"
        dec_deg = abs(dec_deg)
        dec_d = int(dec_deg)
        dec_m = int((dec_deg - dec_d) * 60)
        dec_s = ((dec_deg - dec_d) * 60 - dec_m) * 60
        return f"{dec_sign}{dec_d:02d}d{dec_m:02d}m{dec_s:.3f}s"

    def negative_box(self, max_pix, imsize=None, box_width=3):
        """
        Create a box around the maximum pixel for searching

        Parameters
        ----------
        max_pix : list
            Maximum pixel [xxmax, yymax]
        imsize : int
            Image size (if None, use self.imsize)
        box_width : float
            Box width in degrees (default: 3 degrees)

        Returns
        -------
        str
            CASA box format 'xblc,yblc,xrtc,yrtc'
        """
        if imsize is None:
            imsize = self.imsize

        if self.cellsize is None:
            print("Error: Cell size not provided")
            return "0,0,0,0"

        max_pix_xx = max_pix[0]
        max_pix_yy = max_pix[1]

        # Calculate box length in pixels (box_width in degrees, cellsize in arcsec)
        box_length = (float(box_width) * 3600.0) / self.cellsize

        xblc = max(0, int(max_pix_xx - (box_length / 2.0)))
        yblc = max(0, int(max_pix_yy - (box_length / 2.0)))
        xrtc = min(imsize - 1, int(max_pix_xx + (box_length / 2.0)))
        yrtc = min(imsize - 1, int(max_pix_yy + (box_length / 2.0)))

        return f"{xblc},{yblc},{xrtc},{yrtc}"

    def create_circular_mask(self, h, w, center=None, radius=None):
        """
        Create a circular mask for an image

        Parameters
        ----------
        h, w : int
            Height and width of the image
        center : tuple
            (x, y) center of the circle
        radius : float
            Radius of the circle

        Returns
        -------
        ndarray
            Boolean mask array (True inside circle, False outside)
        """
        if center is None:
            center = (int(w / 2), int(h / 2))
        if radius is None:
            radius = min(center[0], center[1], w - center[0], h - center[1])

        Y, X = np.ogrid[:h, :w]
        dist_from_center = np.sqrt((X - center[0]) ** 2 + (Y - center[1]) ** 2)

        mask = dist_from_center <= radius
        return mask

    def calc_sun_dia(self):
        """
        Calculate the apparent diameter of the sun in arcmin

        Returns
        -------
        float
            Sun diameter in arcmin
        """
        # Standard solar diameter in arcmin at 1 AU
        standard_dia = 32.0

        if self.msname is None:
            return standard_dia

        try:
            # Get the observation time
            ms_meta = msmetadata()
            ms_meta.open(self.msname)

            time_mid = ms_meta.timesforfield(0)[int(len(ms_meta.timesforfield(0)) / 2)]

            # Setup measures and quanta tools
            me = measures()
            qa = quanta()

            # Set the reference frame
            me.doframe(me.epoch("UTC", qa.quantity(time_mid, "s")))
            me.doframe(me.observatory("LOFAR"))  # Assuming LOFAR observations

            # Get the sun position
            sun_pos = me.direction("SUN")

            # Get the distance to sun in AU
            sun_dist = me.separation(me.direction("SUN"), me.direction("SUN_DIST"))
            sun_dist_au = qa.convert(sun_dist, "AU")["value"]

            # Scale the solar diameter
            sun_dia = standard_dia / sun_dist_au

            ms_meta.close()
            return sun_dia
        except Exception as e:
            print(f"Error calculating sun diameter: {e}")
            return standard_dia

    def cal_solar_phaseshift(self, imagename, fit_gaussian=True, sigma=10):
        """
        Calculate the difference between solar center and phase center of the image

        Parameters
        ----------
        imagename : str
            Name of the image
        fit_gaussian : bool
            Perform Gaussian fitting to unresolved Sun to estimate solar center
        sigma : float
            If Gaussian fitting is not used, threshold for estimating center of mass

        Returns
        -------
        float
            RA of the solar center in degrees
        float
            DEC of the solar center in degrees
        bool
            Whether phase shift is required or not
        """
        # Get current phase center
        if self.msname:
            radec_str, radeg, decdeg = self.get_phasecenter()
        else:
            # If no MS provided, extract from image header
            ia = image()
            ia.open(imagename)
            csys = ia.coordsys()
            radeg = csys.referencepixel()["numeric"][0]
            decdeg = csys.referencepixel()["numeric"][1]
            ia.close()

        # Extract cell size and imsize from image if not provided
        if self.cellsize is None or self.imsize is None:
            try:
                header = imhead(imagename=imagename, mode="list")
                self.cellsize = np.abs(
                    np.rad2deg(header["cdelt1"]) * 3600.0
                )  # Convert to arcsec
                self.imsize = header["shape"][0]

                # Setup RMS boxes now that we have the required parameters
                self.setup_rms_boxes(self.imsize, self.cellsize)
            except Exception as e:
                print(f"Error extracting image properties: {e}")
                return radeg, decdeg, False

        # Method 1: Fit Gaussian
        if fit_gaussian:
            sun_dia = self.calc_sun_dia()  # In arcmin
            unresolved_image = f"{imagename.split('.image')[0]}_unresolved.image"

            if os.path.exists(unresolved_image):
                os.system(f"rm -rf {unresolved_image}")

            # Smooth the image to sun size
            imsmooth(
                imagename=imagename,
                targetres=True,
                major=f"{sun_dia}arcmin",
                minor=f"{sun_dia}arcmin",
                pa="0deg",
                outfile=unresolved_image,
            )

            maxpos = imstat(imagename=imagename)["maxpos"]
            fit_box = self.negative_box(maxpos, box_width=3)

            # Fit gaussian to smoothed image
            fitted_params = imfit(imagename=unresolved_image, box=fit_box)

            try:
                # Extract RA/DEC from fit
                ra = np.rad2deg(
                    fitted_params["deconvolved"]["component0"]["shape"]["direction"][
                        "m0"
                    ]["value"]
                )
                dec = np.rad2deg(
                    fitted_params["deconvolved"]["component0"]["shape"]["direction"][
                        "m1"
                    ]["value"]
                )

                # Check if shift is significant
                if np.sqrt((ra - radeg) ** 2 + (dec - decdeg) ** 2) < (
                    self.cellsize / 3600.0
                ):
                    os.system(f"rm -rf {unresolved_image}")
                    return radeg, decdeg, False
                else:
                    os.system(f"rm -rf {unresolved_image}")
                    return ra, dec, True
            except:
                os.system(f"rm -rf {unresolved_image}")
                print("Error in Gaussian fitting, trying alternate method")
                # Fall through to center of mass method

        # Method 2: Center of mass method
        image_path = os.path.dirname(os.path.abspath(imagename))
        temp_prefix = f"{image_path}/phaseshift"

        os.system(f"rm -rf {temp_prefix}*")

        # Setup for center of mass calculation
        if os.path.isfile(f"{temp_prefix}.fits"):
            os.system(f"rm -rf {temp_prefix}.fits")

        # Export to FITS for easier manipulation
        exportfits(
            imagename=imagename,
            fitsimage=f"{temp_prefix}.fits",
            dropdeg=True,
            dropstokes=True,
        )

        # Calculate RMS for thresholding
        rms = imstat(imagename=imagename, box=self.rms_box_nearsun)["rms"][0]

        # Load FITS data
        f = fits.open(f"{temp_prefix}.fits")
        data = fits.getdata(f"{temp_prefix}.fits")

        # Apply threshold
        data[data <= sigma * rms] = 0
        data[data > sigma * rms] = 1

        # Handle different dimensionality
        ndim = data.ndim
        if ndim > 2:
            if ndim == 3:
                data = data[0, :, :]
            elif ndim == 4:
                data = data[0, 0, :, :]

        # Create circular mask around center (5 degrees radius)
        circular_mask = self.create_circular_mask(
            data.shape[0],
            data.shape[1],
            center=(int(data.shape[0] / 2), int(data.shape[1] / 2)),
            radius=int(5 / (self.cellsize / 3600.0)),
        )
        data[~circular_mask] = 0

        # Calculate center of mass
        cy, cx = ndi.center_of_mass(data)

        # Convert pixel position to world coordinates
        w = WCS(f"{temp_prefix}.fits")
        try:
            result = w.pixel_to_world(int(cx), int(cy))
            ra = float(result.ra.deg)
            dec = float(result.dec.deg)
        except:
            # Alternative method for older astropy versions
            try:
                result = w.array_index_to_world(0, int(cy), int(cx))
                ra = result[0].ra.deg
                dec = result[0].dec.deg
            except:
                result = w.array_index_to_world(int(cy), int(cx))
                ra = result.ra.deg
                dec = result.dec.deg

        # Clean up
        os.system(f"rm -rf {temp_prefix}*")

        # Check if shift is significant
        if np.sqrt((ra - radeg) ** 2 + (dec - decdeg) ** 2) < (self.cellsize / 3600.0):
            return radeg, decdeg, False
        else:
            return ra, dec, True

    def shift_phasecenter(self, imagename, ra, dec, stokes="I"):
        """
        Function to shift solar center to phase center of the measurement set

        Parameters
        ----------
        imagename : str
            Name of the image
        ra : float
            Solar center RA in degrees
        dec : float
            Solar center DEC in degrees
        stokes : str
            Stokes parameter to use

        Returns
        -------
        int
            Success code 0: Successfully shifted, 1: Shifting not required, 2: Error
        """
        try:
            if stokes is None:
                return 2

            # Determine image type
            if os.path.isdir(imagename):
                imagetype = "casa"
            else:
                imagetype = "fits"

            # Get target phase center
            if self.msname:
                radec_str, radeg, decdeg = self.get_phasecenter()
            else:
                radec_str = ["Unknown", "Unknown"]
                radeg, decdeg = ra, dec  # Just use the calculated center

            image_path = os.path.dirname(os.path.abspath(imagename))
            temp_image = f"{image_path}/I.model"
            temp_fits = f"{image_path}/wcs_model.fits"

            # Clean up previous files
            if os.path.isfile(temp_fits):
                os.system(f"rm -rf {temp_fits}")
            if os.path.isdir(temp_image):
                os.system(f"rm -rf {temp_image}")

            # Handle trailing slashes
            if imagename.endswith("/"):
                imagename = imagename[:-1]

            # Extract stokes plane for coordinate calculation
            imsubimage(
                imagename=imagename, outfile=temp_image, stokes=stokes, dropdeg=False
            )
            exportfits(
                imagename=temp_image, fitsimage=temp_fits, dropdeg=True, dropstokes=True
            )

            # Calculate pixel position for the target RA/DEC
            w = WCS(temp_fits)
            pix = np.nanmean(
                w.all_world2pix(np.array([[ra, dec], [ra, dec]]), 0), axis=0
            )
            ra_pix = int(pix[0])
            dec_pix = int(pix[1])

            # Apply the shift
            if imagetype == "casa":
                # Update CRPIX values in CASA image
                imhead(
                    imagename=imagename, mode="put", hdkey="CRPIX1", hdvalue=str(ra_pix)
                )
                imhead(
                    imagename=imagename,
                    mode="put",
                    hdkey="CRPIX2",
                    hdvalue=str(dec_pix),
                )
            elif imagetype == "fits":
                # Update CRPIX values in FITS header
                data = fits.getdata(imagename)
                header = fits.getheader(imagename)
                header["CRPIX1"] = float(ra_pix)
                header["CRPIX2"] = float(dec_pix)
                fits.writeto(imagename, data=data, header=header, overwrite=True)
            else:
                print("Image is not either fits or CASA format.")
                return 1

            print(
                f"Image phase center shifted to, RA: {radec_str[0]}, DEC: {radec_str[1]}"
            )

            # Clean up
            os.system(f"rm -rf {temp_image} {temp_fits}")
            return 0

        except Exception as e:
            print(f"Error in shift_phasecenter: {e}")
            return 2

    def visually_center_image(self, imagename, output_file, crpix1, crpix2):
        """
        Create a new visually centered image with the Sun in the middle

        Parameters
        ----------
        imagename : str
            Name of the input image
        output_file : str
            Name of the output image
        crpix1 : int
            X coordinate of the reference pixel (solar center)
        crpix2 : int
            Y coordinate of the reference pixel (solar center)

        Returns
        -------
        bool
            True if successful, False if there was an error
        """
        try:
            # Load the image
            hdul = fits.open(imagename)
            header = hdul[0].header
            data = hdul[0].data

            # Get image dimensions
            if len(data.shape) == 2:
                ny, nx = data.shape
            else:
                ny, nx = data.shape[-2:]

            # Create a new array for the centered image
            new_data = np.zeros_like(data)
            center_x = nx // 2
            center_y = ny // 2

            # Calculate offsets
            offset_x = center_x - crpix1
            offset_y = center_y - crpix2

            print(f"Original image dimensions: {data.shape}")
            print(f"Original reference pixel: CRPIX1={crpix1}, CRPIX2={crpix2}")
            print(
                f"Shifting data by ({offset_x}, {offset_y}) pixels to visually center"
            )

            # Shift the data
            if len(data.shape) == 2:
                # Handle 2D image
                for y in range(ny):
                    for x in range(nx):
                        new_y = y - offset_y
                        new_x = x - offset_x
                        if 0 <= new_y < ny and 0 <= new_x < nx:
                            new_data[y, x] = data[new_y, new_x]
            else:
                # Handle higher dimensions
                for y in range(ny):
                    for x in range(nx):
                        new_y = y - offset_y
                        new_x = x - offset_x
                        if 0 <= new_y < ny and 0 <= new_x < nx:
                            new_data[..., y, x] = data[..., new_y, new_x]

            # Update the header
            header["CRPIX1"] = center_x
            header["CRPIX2"] = center_y

            # Save the centered image
            hdul[0].data = new_data
            hdul.writeto(output_file, overwrite=True)
            hdul.close()

            print(f"Created a visually centered image: {output_file}")
            print(f"New reference pixel: CRPIX1={center_x}, CRPIX2={center_y}")
            return True

        except Exception as e:
            print(f"Error creating visually centered image: {e}")
            return False

    def shift_phasecenter_ms(self, msname, ra, dec):
        """
        Apply phase shift to a measurement set

        Parameters
        ----------
        msname : str
            Name of the measurement set
        ra : float
            RA of the new phase center in degrees
        dec : float
            DEC of the new phase center in degrees

        Returns
        -------
        int
            Success code 0: Successfully shifted, 1: Error in shifting
        """
        try:
            # Create a table tool
            t = table()

            # Get original phase center from MS
            t.open(f"{msname}/FIELD")
            orig_dir = t.getcol("PHASE_DIR")
            # Convert the new coordinates to radians
            new_ra_rad = np.deg2rad(ra)
            new_dec_rad = np.deg2rad(dec)

            # Format for display
            ra_hms = self.deg2hms(ra)
            dec_dms = self.deg2dms(dec)
            orig_ra_deg = np.degrees(orig_dir[0][0][0])
            orig_dec_deg = np.degrees(orig_dir[0][0][1])
            orig_ra_hms = self.deg2hms(orig_ra_deg)
            orig_dec_dms = self.deg2dms(orig_dec_deg)

            print(
                f"Original phase center: RA = {orig_ra_hms} ({orig_ra_deg} deg), DEC = {orig_dec_dms} ({orig_dec_deg} deg)"
            )
            print(
                f"New phase center: RA = {ra_hms} ({ra} deg), DEC = {dec_dms} ({dec} deg)"
            )

            # Update the phase center
            for i in range(orig_dir.shape[0]):
                orig_dir[i][0][0] = new_ra_rad
                orig_dir[i][0][1] = new_dec_rad

            # Write back to the table
            t.putcol("PHASE_DIR", orig_dir)
            t.close()

            # Update UVW coordinates to match the new phase center
            fixvis(
                vis=msname,
                outputvis="",
                phasecenter=f"J2000 {ra_hms} {dec_dms}",
                datacolumn="all",
            )

            print(f"Phase center of MS successfully updated")
            return 0
        except Exception as e:
            print(f"Error shifting phase center in MS: {e}")
            return 1

    def apply_shift_to_multiple_fits(
        self,
        ra,
        dec,
        input_pattern,
        output_pattern=None,
        stokes="I",
        visual_center=False,
        use_multiprocessing=True,
        max_processes=None,
    ):
        """
        Apply the same phase shift to multiple FITS files

        Parameters
        ----------
        ra : float
            RA of the solar center in degrees
        dec : float
            DEC of the solar center in degrees
        input_pattern : str
            Glob pattern for input files (e.g., "path/to/*.fits")
        output_pattern : str, optional
            Pattern for output files (if None, input files will be modified)
        stokes : str
            Stokes parameter to use
        visual_center : bool
            Whether to also create visually centered images
        use_multiprocessing : bool
            Whether to use multiprocessing for batch processing
        max_processes : int, optional
            Maximum number of processes to use (defaults to number of CPU cores)

        Returns
        -------
        list
            List of [success_count, total_count]
        """
        try:
            # Get list of files matching the pattern
            files = glob.glob(input_pattern)
            if not files:
                print(f"No files found matching pattern: {input_pattern}")
                return [0, 0]

            total_count = len(files)
            print(f"Found {total_count} files matching pattern: {input_pattern}")
            print(f"Applying phase shift: RA = {ra} deg, DEC = {dec} deg")

            # If only one file or multiprocessing is disabled, use the single-processing approach
            if total_count == 1 or not use_multiprocessing:
                success_count = 0
                for i, file in enumerate(files):
                    print(f"Processing file {i+1}/{total_count}: {file}")

                    # Determine output file
                    if output_pattern:
                        file_basename = os.path.basename(file)
                        file_name, file_ext = os.path.splitext(file_basename)

                        # Replace wildcards in the output pattern
                        output_file = output_pattern.replace("*", file_name)
                        if not output_file.endswith(file_ext):
                            output_file += file_ext

                        # Make a copy of the input file
                        if os.path.isdir(file):
                            os.system(f"rm -rf {output_file}")
                            os.system(f"cp -r {file} {output_file}")
                            target = output_file
                        else:
                            shutil.copy(file, output_file)
                            target = output_file
                    else:
                        target = file

                    # Apply the phase shift
                    result = self.shift_phasecenter(
                        imagename=target, ra=ra, dec=dec, stokes=stokes
                    )

                    if result == 0:
                        success_count += 1

                        # Create a visually centered image if requested
                        if visual_center:
                            try:
                                # Get the reference pixel values from the shifted image
                                header = fits.getheader(target)
                                crpix1 = int(header["CRPIX1"])
                                crpix2 = int(header["CRPIX2"])

                                # Generate output filename for visually centered image
                                visual_output = (
                                    os.path.splitext(target)[0]
                                    + "_centered"
                                    + os.path.splitext(target)[1]
                                )

                                print(
                                    f"Creating visually centered image: {visual_output}"
                                )
                                # Create the visually centered image
                                self.visually_center_image(
                                    target, visual_output, crpix1, crpix2
                                )
                                print(
                                    f"Visually centered image created: {visual_output}"
                                )
                            except Exception as e:
                                print(
                                    f"Error creating visually centered image for {target}: {e}"
                                )

                print(f"Successfully processed {success_count}/{total_count} files")
                return [success_count, total_count]

            # Use multiprocessing for batch processing
            else:
                # Determine number of processes to use
                if max_processes is None:
                    max_processes = min(multiprocessing.cpu_count(), total_count)
                else:
                    max_processes = min(
                        max_processes, multiprocessing.cpu_count(), total_count
                    )

                print(f"Using multiprocessing with {max_processes} processes")

                # Prepare the arguments for each file
                file_args = [
                    (file, ra, dec, stokes, output_pattern, visual_center)
                    for file in files
                ]

                # Create a process pool and process the files
                with Pool(processes=max_processes) as pool:
                    results = pool.map(self.process_single_file, file_args)

                # Count successful operations
                success_count = sum(1 for success, _, _ in results if success)

                # Print any errors or warnings
                for success, file, message in results:
                    if message:
                        print(f"{file}: {message}")

                print(f"Successfully processed {success_count}/{total_count} files")
                return [success_count, total_count]

        except Exception as e:
            print(f"Error in applying shift to multiple files: {e}")
            try:
                return [0, total_count]
            except:
                return [0, 0]

    def process_single_file(self, file_info):
        """
        Process a single file for multiprocessing in batch mode

        Parameters
        ----------
        file_info : tuple
            Tuple containing (file_path, ra, dec, stokes, output_pattern, visual_center)

        Returns
        -------
        tuple
            Tuple containing (success, file_path, error_message)
        """
        file, ra, dec, stokes, output_pattern, visual_center = file_info

        try:
            # Determine output file
            if output_pattern:
                file_basename = os.path.basename(file)
                file_name, file_ext = os.path.splitext(file_basename)

                # Replace wildcards in the output pattern
                output_file = output_pattern.replace("*", file_name)
                if not output_file.endswith(file_ext):
                    output_file += file_ext

                # Make a copy of the input file
                if os.path.isdir(file):
                    os.system(f"rm -rf {output_file}")
                    os.system(f"cp -r {file} {output_file}")
                    target = output_file
                else:
                    shutil.copy(file, output_file)
                    target = output_file
            else:
                target = file

            # Apply the phase shift
            result = self.shift_phasecenter(
                imagename=target, ra=ra, dec=dec, stokes=stokes
            )

            if result == 0:
                # Create a visually centered image if requested
                if visual_center:
                    try:
                        # Get the reference pixel values from the shifted image
                        header = fits.getheader(target)
                        crpix1 = int(header["CRPIX1"])
                        crpix2 = int(header["CRPIX2"])

                        # Generate output filename for visually centered image
                        visual_output = (
                            os.path.splitext(target)[0]
                            + "_centered"
                            + os.path.splitext(target)[1]
                        )

                        # Create the visually centered image
                        self.visually_center_image(
                            target, visual_output, crpix1, crpix2
                        )
                        return (True, file, None)
                    except Exception as e:
                        return (
                            True,
                            file,
                            f"Warning: Error creating visually centered image: {str(e)}",
                        )

                return (True, file, None)
            else:
                return (False, file, f"Error applying phase shift (code: {result})")

        except Exception as e:
            return (False, file, f"Error: {str(e)}")


def main():
    """
    Main function to run from command line
    """
    parser = argparse.ArgumentParser(
        description="Calculate and apply phase shifts to solar images"
    )
    parser.add_argument(
        "--imagename",
        type=str,
        required=False,
        help="Input image name (CASA or FITS format) for calculating phase shift",
    )
    parser.add_argument(
        "--msname", type=str, default=None, help="Measurement set name (optional)"
    )
    parser.add_argument(
        "--cellsize",
        type=float,
        default=None,
        help="Cell size in arcsec (optional, will be read from image if not provided)",
    )
    parser.add_argument(
        "--imsize",
        type=int,
        default=None,
        help="Image size in pixels (optional, will be read from image if not provided)",
    )
    parser.add_argument(
        "--stokes", type=str, default="I", help="Stokes parameter to use (default: I)"
    )
    parser.add_argument(
        "--fit_gaussian",
        action="store_true",
        default=False,
        help="Use Gaussian fitting for solar center",
    )
    parser.add_argument(
        "--sigma",
        type=float,
        default=10,
        help="Sigma threshold for center-of-mass calculation (default: 10)",
    )
    parser.add_argument(
        "--apply_shift",
        action="store_true",
        default=True,
        help="Apply the calculated shift to the image",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output image name (if not specified, input image will be modified)",
    )
    parser.add_argument(
        "--visual_center",
        action="store_true",
        default=False,
        help="Create a visually centered image (moves pixel data)",
    )

    # New arguments for batch processing
    parser.add_argument(
        "--ra",
        type=float,
        default=None,
        help="RA in degrees (if provided, skips calculation)",
    )
    parser.add_argument(
        "--dec",
        type=float,
        default=None,
        help="DEC in degrees (if provided, skips calculation)",
    )
    parser.add_argument(
        "--apply_to_ms",
        action="store_true",
        default=False,
        help="Apply the calculated/provided shift to the MS file",
    )
    parser.add_argument(
        "--input_pattern",
        type=str,
        default=None,
        help="Glob pattern for batch processing multiple files",
    )
    parser.add_argument(
        "--output_pattern",
        type=str,
        default=None,
        help='Output pattern for batch processing (e.g., "/path/to/shifted_*.fits")',
    )

    args = parser.parse_args()

    # Initialize the object
    spc = SolarPhaseCenter(
        msname=args.msname, cellsize=args.cellsize, imsize=args.imsize
    )

    # Determine phase shift coordinates
    if args.ra is not None and args.dec is not None:
        # Use provided coordinates
        ra = args.ra
        dec = args.dec
        needs_shift = True
        print(f"Using provided coordinates: RA = {ra} deg, DEC = {dec} deg")
    elif args.imagename:
        # Calculate from image
        ra, dec, needs_shift = spc.cal_solar_phaseshift(
            imagename=args.imagename, fit_gaussian=args.fit_gaussian, sigma=args.sigma
        )
        print(f"Calculated solar center: RA = {ra} deg, DEC = {dec} deg")
        print(f"Phase shift needed: {needs_shift}")
    else:
        print(
            "Error: Either provide an image for calculation or specify RA and DEC coordinates"
        )
        return

    # Handle MS phase shift
    if args.apply_to_ms and args.msname:
        if needs_shift:
            result = spc.shift_phasecenter_ms(args.msname, ra, dec)
            if result == 0:
                print(f"Successfully applied phase shift to MS: {args.msname}")
            else:
                print(f"Failed to apply phase shift to MS: {args.msname}")
        else:
            print("No phase shift needed for the MS")

    # Handle batch processing of FITS files
    if args.input_pattern:
        if needs_shift:
            success_count, total_count = spc.apply_shift_to_multiple_fits(
                ra,
                dec,
                args.input_pattern,
                args.output_pattern,
                args.stokes,
                args.visual_center,
            )
            if success_count == total_count:
                print(f"Successfully applied phase shift to all {total_count} files")
            else:
                print(
                    f"Applied phase shift to {success_count} out of {total_count} files"
                )
        else:
            print("No phase shift needed for the image files")

    # Handle single image (original functionality)
    elif args.imagename and args.apply_shift and needs_shift:
        if args.output:
            # Make a copy of the image
            if os.path.isdir(args.imagename):
                os.system(f"rm -rf {args.output}")
                os.system(f"cp -r {args.imagename} {args.output}")
                target = args.output
            else:
                import shutil

                shutil.copy(args.imagename, args.output)
                target = args.output
        else:
            target = args.imagename

        result = spc.shift_phasecenter(
            imagename=target, ra=ra, dec=dec, stokes=args.stokes
        )

        if result == 0:
            print("Phase shift successfully applied")

            # Create a visually centered image if requested
            if args.visual_center and args.output:
                # Get the reference pixel values from the shifted image
                header = fits.getheader(target)
                crpix1 = int(header["CRPIX1"])
                crpix2 = int(header["CRPIX2"])

                # Generate output filename for visually centered image
                visual_output = (
                    os.path.splitext(args.output)[0]
                    + "_centered"
                    + os.path.splitext(args.output)[1]
                )

                # Create the visually centered image
                spc.visually_center_image(target, visual_output, crpix1, crpix2)

        elif result == 1:
            print("Phase shift not needed")
        else:
            print("Error applying phase shift")
    elif args.imagename and args.output and not needs_shift:
        # User requested output file but no shift needed
        if os.path.isdir(args.imagename):
            os.system(f"rm -rf {args.output}")
            os.system(f"cp -r {args.imagename} {args.output}")
        else:
            import shutil

            shutil.copy(args.imagename, args.output)
        print(f"No phase shift needed. Copied original image to {args.output}")

        # If visual centering was requested but no shift needed, still create it
        if args.visual_center:
            # Need to get current reference pixels
            header = fits.getheader(args.output)
            crpix1 = int(header["CRPIX1"])
            crpix2 = int(header["CRPIX2"])

            # Generate output filename for visually centered image
            visual_output = (
                os.path.splitext(args.output)[0]
                + "_centered"
                + os.path.splitext(args.output)[1]
            )

            # Create the visually centered image
            spc.visually_center_image(args.output, visual_output, crpix1, crpix2)


if __name__ == "__main__":
    main()

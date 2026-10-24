# LASCO C2 and C3 calibration

The Solar Data Downloader turns SOHO/LASCO level 0.5 files, which hold raw
camera counts, into calibrated images in **mean solar brightness** (MSB),
the unit NRL's level 1 products use. The work is done by
`lasco_calibration.py` in this directory, which is also a stand-alone
command. This note explains what is done, why, with which inputs, and what
the result does and does not contain.

## Why the downloaded files need this

The Virtual Solar Observatory only serves LASCO **level 0.5**: counts (DN)
as the camera recorded them, with the spacecraft roll still in the image.
Two things make such an image hard to compare with anything else:

- **Vignetting.** The coronagraph's optics pass less light towards the
  occulter and towards the edge of the field. The recorded brightness falls
  off in a way that has nothing to do with the corona.
- **Instrument units.** Counts depend on the exposure time, the filter, the
  polarizer and the slow ageing of the detector. Two images from different
  dates, or C2 and C3 of the same moment, are not on the same scale.

NRL's level 1 pipeline removes both. Its own level 1 archive stops in
August 2017, so for later dates the calibration has to be done locally.
`lasco_calibration.py` is a Python port of that pipeline: the SolarSoft
routines `reduce_level_1`, `c2_calibrate`, `c3_calibrate`, `c2_calfactor`,
`c3_calfactor`, `get_exp_factor`, `c2_warp`, `c3_warp` and
`distortion_coeffs`. It was checked against NRL level 1 files of
2017-08-31: the median ratio of the two is 1.000 for both detectors, and
the pixel-to-pixel spread is within 0.02 % (C2) and 0.07 % (C3).

## The steps

For an image `DN` with header values `OFFSET`, `EXPTIME`, `FILTER`,
`POLAR` and the observation date:

1. **Bias.** The CCD bias is subtracted. NRL measures it for every image and
   publishes it in a daily *exposure table*
   (`expfac/data/YYMM/c2_expfactor_YYMMDD.dat`). When the day's table is
   on the server the bias comes from there; otherwise the header's `OFFSET`
   is used, which the level 0.5 pipeline estimated from the same
   measurement (the two agree to 0.1 DN).

2. **Exposure time.** The counts are divided by the corrected exposure time:
   the commanded `EXPTIME` times the per-image factor from the same table
   (typically 0.997 to 1.003). Without a table the factor is 1.

3. **Calibration factor.** The result, in counts per second, is multiplied
   by the factor that gives mean solar brightness. It depends on detector,
   filter and polarizer, and for the two usual filters on the date:

   | Detector, filter | Factor, MSB per (DN/s) |
   |---|---|
   | C2 orange, clear polarizer | (4.60403e-7 × MJD + 0.0374116) × 1e-10 |
   | C3 clear, clear polarizer | (7.43e-8 × (MJD − 50000) + 5.96e-3) × 1e-10 |

   The other filter and polarizer combinations use the constants in
   `c2_calfactor.pro` and `c3_calfactor.pro`. Summed images get the factor
   divided by the summing.

4. **Vignetting.** The image is multiplied by NRL's inverse vignetting
   array: `c2vig_final.fts` for C2, `c3vig_preint_final.fts` (before
   1998-07-06) or `c3vig_postint_final.fts` for C3. For C3 with the clear
   filter the stray-light ramp `C3ramp.fts` is then subtracted, and the
   occulter, pylon and edge are blanked with `c3_cl_mask_lvl1.fts`.

5. **Missing telemetry.** Pixels that arrived as zero or negative (lost
   blocks) are blanked.

6. **Distortion.** The optics distort radially; a feature at radius `r`
   (in mm from the occulter centre, 0.021 mm per pixel) was recorded at
   `r + r·(c0 + c1·r² + c2·r⁴)`. The image is resampled onto the
   undistorted grid with NRL's coefficients: C2 (0.0051344125,
   −0.00012233862, 1.0978595e-7), C3 (−0.0151657, 0.000165455, 0). The
   shift is up to a few pixels at the edge of the field. NRL evaluates this
   on a 33×33 grid and warps between the points; the port evaluates it at
   every pixel.

7. **North up.** The image is rotated by the header's `CROTA` with the
   Sun's centre (`CRPIX1/2`) moved to the middle of the frame, as every
   other instrument in the downloader is. SOHO was often rolled by 180°;
   that is part of the same rotation.

Blanked pixels are stored as NaN, not zero, so that SolarViewer leaves them
out of its statistics and colour stretch. NRL's level 1 files fill the C3
mask with a constant instead.

## The output file

A plain FITS file, 32-bit floats in the primary HDU, named
`<original>_prepped.fits`:

- `BUNIT = 'MSB'`, `LEVEL = '1.0'`
- `EXPTIME` is the corrected exposure time, `OFFSET` the bias subtracted
- `DATE-OBS` in ISO form (level 0.5 splits date and time)
- `CTYPE1/2 = 'HPLN-TAN'/'HPLT-TAN'`, `CUNIT1/2 = 'arcsec'`, `CDELT1/2`
  11.9 (C2) or 56.0 (C3) arcsec per pixel, no roll left in the header
- `HISTORY` lines starting with `SolarViewer:` record every step with the
  numbers used (bias, exposure factor and its source, calibration factor,
  file names, distortion coefficients, rotation)

Typical values are 1e-10 to 1e-7 MSB in C2 and 1e-11 to 1e-9 in C3. The
sky brightness near the C2 occulter is about 1e-8, a bright streamer a few
1e-9 at 3 solar radii.

## What is not done

- **Stray light and the diffraction ring.** NRL's level 1 leaves the bright
  ring around the C2 occulter in place and so does this port. Masking it
  is a display choice, not calibration.
- **Background subtraction.** No monthly minimum background or running
  difference is applied; the result is the total brightness of the corona
  plus the F corona, as in NRL level 1.
- **Pointing refinement.** NRL later corrects `CRPIX` and `CROTA` with
  spacecraft tables that are not published as plain files. The level 0.5
  header values are used; they are good to a fraction of a pixel and a
  few hundredths of a degree.
- **Missing block replacement.** NRL's `fuzzy_image` fills lost blocks from
  a background model when more than a thousand pixels are missing; the
  port blanks them instead.
- **Calibration factor choice.** NRL's factors trace to the pre-flight
  calibration. In-flight star measurements (see the LASCO Coronal
  Brightness Index paper, arXiv:2001.07596) suggest C2 should be a few per
  cent brighter. The port follows NRL so that its output matches the level
  1 archive.

## The calibration files

Everything comes from the public SolarSoft tree at
`https://soho.nascom.nasa.gov/solarsoft/soho/lasco/idl/` and is fetched
once into `~/.cache/solarviewer/lasco_calib/` (about 17 MB in all):

| File | Used for |
|---|---|
| `data/calib/c2vig_final.fts` | C2 inverse vignetting |
| `data/calib/c3vig_preint_final.fts`, `c3vig_postint_final.fts` | C3 inverse vignetting |
| `data/calib/C3ramp.fts` | C3 stray-light ramp |
| `data/calib/c3_cl_mask_lvl1.fts` | C3 occulter, pylon and edge mask |
| `convert/c3_occltr_cntr.dat` | C3 occulter centre by date |
| `expfac/data/YYMM/c?_expfactor_YYMMDD.dat` | per-image bias and exposure factor, one small file per day |

Set the environment variable `SOLARVIEWER_LASCO_CALIB` to keep the cache
elsewhere. Without network access and without a filled cache the
downloader cannot calibrate; it then saves the image north up in counts
per second, prints a warning, and the file's `HISTORY` says so.

## Using the script on its own

```
python -m solar_radio_image_viewer.solar_data_downloader.lasco_calibration FILE.fts [FILE2.fts ...]
    -o DIR          write the results into DIR (default: next to each input)
    --suffix TEXT   output name suffix (default _prepped)
    --calib-dir DIR where the calibration files are cached
    --no-warp       skip the distortion correction
    --keep-roll     do not rotate to solar north
```

From Python:

```python
from solar_radio_image_viewer.solar_data_downloader import lasco_calibration

out = lasco_calibration.calibrate_file("22967077.fts", output_dir="calibrated")

# or on arrays, without saving
image, steps, cards = lasco_calibration.calibrate(data, header)
```

The inputs are level 0.5 files as the VSO or the NRL archive
(`https://lasco-www.nrl.navy.mil/lz/level_05/YYMMDD/c2/`) serve them.

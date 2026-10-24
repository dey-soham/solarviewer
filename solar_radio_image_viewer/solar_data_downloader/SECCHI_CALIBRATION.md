# STEREO SECCHI calibration

`secchi_calibration.py` turns the level 0.5 files the VSO serves for
STEREO's COR1, COR2 and EUVI into calibrated images, the way the SECCHI
team's `secchi_prep` does in SolarSoft, without IDL. COR1 and COR2
polarizer triplets become one total-brightness image in mean solar
brightness (MSB, the brightness of the solar disk centre) with the team's
monthly minimum background in a second HDU; EUVI becomes counts per
second with the on-board processing undone and the bias, filter and flat
field corrected. The downloader runs it on every STEREO download; it is
also a command line and a small Python API.

## Why

The level 0.5 files hold raw counts: the CCD bias is still in them (for
COR2 about 2060 of a typical 2270 DN), the on-board processor has divided
the summed pixels, the polarizer transmits half the light, and the
vignetting darkens the field by a factor of several towards the edge.
Before this module the downloader only divided by the exposure time and
summed the triplet, so about 90 percent of its "total brightness" was
bias. The SECCHI team defines what a calibrated image is in
`secchi_prep.pro` and its helpers; this module follows them step by step
and names each step in the HISTORY of the output.

## The steps

All the routines named here are in
<https://soho.nascom.nasa.gov/solarsoft/stereo/secchi/idl/prep/>.

1. **On-board processing undone** (`scc_sebip.pro`). IP_00_19 lists the
   steps the image processor applied, three characters each. The
   divisions are multiplied back: code 1 by 2 (one fewer when DIV2CORR is
   set), 50 and 83 by 4, 53 by 4 when the image was summed on board
   (IPSUM above 1), 82 by 2, 84 by 8, 85 by 16, 86 by 32, 87 and 16/17 by
   64, 88 by 128, 118 by 3; code 2 (square-root compression) is squared
   back. A COR1 image with IPSUM 3 comes back as the sum of 16 pixels of
   two exposures, which is what its exposure time and background files
   count.
2. **Bias** (`get_biasmean.pro`). BIASMEAN, multiplied by the number of
   CCD pixels summed on board, `(2^(IPSUM-1))^2`. Nothing when codes 103,
   37 or 38 say it was removed on board, or OFFSETCR says it was removed
   already.
3. **Exposure.** Divided by EXPTIME, which already covers the exposures
   summed on board (N_IMAGES).
4. **COR: calibration factor** (`get_calfac.pro`), mean solar brightness
   per count per second. COR2: 2.7e-12 x 0.5 (A) or 2.8e-12 x 0.5 (B);
   the 0.5 is the polarizer's transmission. COR1: 6.578e-11 (A) or
   7.080e-11 (B) divided by `1 - rate x years` since the reference date
   (A: 0.648 percent a year from 2007-12-01, B: 0.258 percent from
   2008-01-17). Divided by the pixels summed on board. A total-brightness
   file made from polarizer images (POLAR 1001, SEB_PROG not DOUBLE) gets
   twice the factor, as in SolarSoft; raw files never do.
5. **COR: vignetting or flat** (`get_calimg.pro`): divided by
   `20060929_vignet_vCc2A.fts` / `20140723_vignet_vCc2B.fts` (COR2) or
   `20090723_flatfd_fCc1[AB].fts` (COR1). The 2150-pixel arrays are cut
   to the 2048 active columns (50 to 2097), turned the way the pipeline
   turned the image (see Orientation), and binned like it.
6. **EUVI: filter and flat** (`euvi_get_normal.pro`,
   `euvi_correction.pro`). Divided by the filter's transmission relative
   to the open position (171 and 195 Å: S1/S2 0.49, DBL 0.41; 284: 0.33,
   0.24; 304: 0.29, 0.22) and multiplied by `20060823_wav{λ}_fCeu[AB].fts`
   (all ones today). The unit stays DN/s; `--photons` multiplies by
   SolarSoft's `15 x 3.65 λ / (13.6 x 911)` detected photons per count
   instead (BUNIT `photon/s`).
7. **COR: the triplet** (`cor_polariz.pro`, `cor_mueller.pro`). Each
   polarizer image at angle a sees `I/2 + Q cos(2a)/2 + U sin(2a)/2`. The
   three equations are inverted; for 0, 120 and 240 degrees the total
   brightness is `2/3` of the sum. The polarized brightness
   `sqrt(Q^2 + U^2)` is written too with `--pb`. The output header takes
   the middle image's cards, POLAR 1001, the mean exposure.
8. **COR2: distortion** (`cor2_warp.pro`, `cor2_distortion.pro`). Every
   output pixel at `r` full-resolution pixels from the Sun centre (CRPIX)
   takes the recorded value at `r + c2 + c1 r + c0 r^2`, with
   (1.04872e-05, -0.00801293, -0.243670) for A and (1.96029e-05,
   -0.0201616, 4.68841) for B. The pointing refinement `cor2_point.pro`
   is not ported: the header's CRPIX is used.
9. **COR: masks** (`get_smask.pro`). `cor2[AB]_mask.fts` and
   `cor1_mask.fts` blank the occulter and the field edge (NaN). EUVI is
   not masked (its mask only trims the corners of the round field).
10. **North up.** sunpy turns the image by CROTA with the Sun at the
    centre; the frame grows to hold the turned image.
11. **COR: background.** See below.

The output is 32-bit floats with BUNIT `MSB` (COR) or `DN/s` (EUVI),
OFFSETCR holding the bias removed, IPSUM 1, LEVEL 1.0, and one HISTORY
line per step.

## Orientation

The pipeline turns each detector's CCD frame before writing the level
0.5 file (`secchi_rectify.pro`, RECTIFY and RECTROTA in the header):
before the 2015 conjunction STEREO-A's EUVI by IDL `ROTATE` code 6, COR1
by 3, COR2 by 1, and STEREO-B's by 3, 1 and 3. From 2015-05-19 to
2023-08-12 STEREO-A flew upside down and its images were turned by a
further 180 degrees (COR2's RECTROTA became 3, EUVI's 4). The calibration
arrays are in the CCD frame (or, for the COR1 flat, in a rectified frame
of their own, RECTROTA in their header) and the masks in the pre-2015
rectified frame; each is turned by the rotation that takes its frame to
the image's RECTROTA, composed in the dihedral group. SolarSoft reaches
the same result by rectifying with the old table and flipping by 180
degrees in the upside-down years.

## Background

The team keeps monthly minimum backgrounds at
<https://stereo-ssc.nascom.nasa.gov/data/ins_data/secchi/backgrounds/{a,b}/monthly_min/YYYYMM/>:
for each polarizer (`mc2A_p000_YYMMDD.fts`, `_p120_`, `_p240_`) and for
double-exposure total brightness (`mc2A_dbTB_`), one about every week
since 2006, the minimum over a month of daily images, bias removed, per
second, at the usual binning (1024 pixels for COR2, 512 for COR1), in
the detector's frame. (The `_pTBr_` total-brightness files are not used:
COR1's hold half the Mueller total and COR2's the whole, so they do not
combine like the image.)

For a triplet the two files of each polarizer's series bracketing the
observation (within 120 days) are weighted by their distance in time;
else the nearest within 60 days; else there is no background and the
HISTORY says so. Each is scaled to the image's on-board summing (times
`4^(IPSUM difference)`, `scc_getbkgimg.pro`'s MATCH), divided by the
vignetting and multiplied by the calibration factor like the image, and
the three are combined through the same inverse Mueller matrix, then
warped and masked like the image. A double-exposure image takes the
`dbTB` series, a lone polarizer image its own.

On a normal day the COR2 image sits 2 to 17 percent above this
background (the 16th to 84th percentile of the ratio, 2022-05-10) with
one to seven percent of pixels below it, as a minimum should. COR1's
stray light is so strong that image and background agree to a percent.
During a calibration roll (the spacecraft turned by more than 10 degrees
from its usual attitude, found by comparing CROTA with that of the
`dbTB` file of the same date) COR2 gets no background: the files are
made at the usual attitude, and turning one by the roll turns the stray
light with it, which fits no better (checked on 2022-05-19, rolled 119
degrees; SolarSoft's ROLL option does that turn).

The background goes into an ImageHDU named `BACKGROUND`, with the image
header's cards and BUNIT `MSB`; the primary header's `BKGEXT` names it.
The viewer's Display tab shows a "Background" switch for such files, and
the Presets menu a "Subtract Background" entry; the file keeps the total
brightness.

## Files

| file | from | holds |
| --- | --- | --- |
| `20060929_vignet_vCc2A.fts`, `20140723_vignet_vCc2B.fts` | `.../secchi/calibration/` | COR2 vignetting, 2150 square |
| `20090723_flatfd_fCc1A.fts`, `..._fCc1B.fts` | same | COR1 flat field, rectified |
| `20060823_wav{171,195,284,304}_fCeu{A,B}.fts` | same | EUVI flat field (ones) |
| `cor2A_mask.fts`, `cor2B_mask.fts`, `cor1_mask.fts` | same | 1 where the image is good |
| `mc[12][AB]_p{000,120,240}_YYMMDD.fts`, `_dbTB_` | stereo-ssc `backgrounds/{a,b}/monthly_min/YYYYMM/` | monthly minimum, DN/s |
| `backgrounds/index_{a,b}_YYYYMM.txt` | made here | cached directory listings |

Everything is cached in `~/.cache/solarviewer/secchi_calib` (or
`$SOLARVIEWER_SECCHI_CALIB`, or `--calib-dir`). The first COR2 run fetches
about 18 MB of calibration arrays and 4 MB per background file.

## Command line

```
python -m solar_radio_image_viewer.solar_data_downloader.secchi_calibration FILES... [-o DIR] [--pb] [--no-background] [--no-warp] [--no-mask] [--keep-roll] [--photons]
```

Polarizer triplets among FILES are found by time and angle (three images
within three minutes at 0, 120 and 240 degrees); each becomes
`YYYYMMDD_HHMMSS_cor2A_total_brightness.fits` (and
`..._polarized_brightness.fits` with `--pb`); any other image becomes
`<name>_prepped.fits`.

## Python

```python
from solar_radio_image_viewer.solar_data_downloader import secchi_calibration as sc

for group in sc.group_files(paths):
    outputs = sc.calibrate_files(group, output_dir)

image, pb, header, steps, cards = sc.calibrate_group(datas, headers)  # detector frame, no north-up
bkg, steps = sc.background(headers, image.shape)
```

## Not ported

The pointing refinements (`cor2_point.pro`, `euvi_point.pro`) and
`euvi_prep`'s de-jitter; the HI telescopes; subfield images (anything
but the 2048-pixel active area at its summing); SolarSoft's rotation of a
background to a rolled image; the `daily_med` and `roll_min` background
series.

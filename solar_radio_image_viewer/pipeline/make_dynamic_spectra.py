"""
Create Dynamic Spectra from MS Files

This script:
  1. Accepts a directory (--indir) containing MS files (each representing one subband).
  2. Processes each MS file in parallel (using --ncpu processes) to:
       - Read TIME, DATA, FLAG, and UVW columns using casacore.
       - Filter rows with uv distance (in wavelengths) outside the range [130, 500].
       - Bin data in time with a specified bin width (in seconds) and compute the mean amplitude.
  3. Combines the results into a dynamic spectrum (2D array: time x subband).
  4. Reads each MS file's frequency (median from the SPECTRAL_WINDOW table) and converts it to MHz.
  5. Saves the dynamic spectrum to a FITS file. In the FITS file:
       - The primary HDU contains the dynamic spectrum.
       - A BinTableHDU contains the time axis in MJD and a second column with UTC strings.
       - A second BinTableHDU contains the subband frequencies in MHz.
  6. Optionally saves and/or displays a plot.

Usage:
  python create_dynamic_spectra.py --indir /path/to/msfiles --outfits dynamic_spectra.fits --binwidth 1.0 --ncpu 10 --saveplot False --showplot False
"""

import os
import sys
import glob
import argparse
import re
import numpy as np
import matplotlib.pyplot as plt
from casacore.tables import table
from astropy.io import fits
from astropy.time import Time
import concurrent.futures
from matplotlib.dates import DateFormatter, date2num
import logging

try:
    from pipeline.tasks.basic_functions import MS_inquiry

    pipeline_run = True
except ImportError:
    pipeline_run = False


# ------------------ Helper Functions ------------------
def extract_sb_number(filename):
    """Extract integer following '_SB' from the filename for sorting."""
    m = re.search(r"_SB(\d+)_", filename)
    return int(m.group(1)) if m else -1


def read_frequency(ms_file):
    """Read the median channel frequency (Hz) from the SPECTRAL_WINDOW subtable."""
    sp_tab = table(os.path.join(ms_file, "SPECTRAL_WINDOW"), ack=False, readonly=True)
    chan_freq = sp_tab.getcol("CHAN_FREQ")
    median_freq = np.median(chan_freq)
    sp_tab.close()
    return median_freq


def read_ms(ms_file, binwidth=1.0, uv_min=130.0, uv_max=500.0):
    """
    Read one MS file and produce binned mean amplitude values.

    - Reads TIME, DATA, FLAG, and UVW columns.
    - Computes uv distance (in wavelengths) using wavelength = 299792458/freq.
    - Filters rows with uv distance outside [uv_min, uv_max] lambda.
    - Bins the data in time bins of width binwidth (seconds) and computes the mean amplitude.

    Returns:
      bin_centers (np.array): Array of bin center times (in seconds).
      mean_amp (np.array): Mean amplitude for each bin.
    """
    try:
        sp_tab = table(
            os.path.join(ms_file, "SPECTRAL_WINDOW"), ack=False, readonly=True
        )
        chan_freq = sp_tab.getcol("CHAN_FREQ")
        freq = np.median(chan_freq)
        sp_tab.close()
    except Exception as e:
        print(f"Error reading frequency from {ms_file}: {e}")
        raise

    wavelength = 299792458.0 / freq  # in meters
    print(
        f"Processing {os.path.basename(ms_file)}: freq = {freq/1e6:.2f} MHz, λ = {wavelength*1e2:.2f} cm"
    )

    try:
        tb = table(ms_file, ack=False, readonly=True)
        times = tb.getcol("TIME")  # seconds (assumed absolute)
        # For simplicity, take the first correlation from DATA.
        data = tb.getcol("DATA")[:, :, 0]  # shape: (nrow, nchan)
        flags = tb.getcol("FLAG")
        uvw = tb.getcol("UVW")  # shape: (nrow, 3)
        tb.close()
    except Exception as e:
        print(f"Error reading MS columns from {ms_file}: {e}")
        raise

    uv_dist = np.sqrt(uvw[:, 0] ** 2 + uvw[:, 1] ** 2)
    uv_lambda = uv_dist / wavelength
    valid_mask = (uv_lambda >= uv_min) & (uv_lambda <= uv_max)
    if np.sum(valid_mask) == 0:
        print(f"Warning: No valid data in uv-lambda range for {ms_file}.")
        return np.array([]), np.array([])

    times = times[valid_mask]
    data = data[valid_mask]
    flags = flags[valid_mask]
    # times = np.unique(times)

    min_time = np.min(times)
    max_time = np.max(times)
    edges = np.arange(min_time, max_time + binwidth, binwidth)

    # Use vectorized binning.
    # Compute mean amplitude per row (averaged over channels).
    row_mean = np.mean(np.abs(data), axis=1)
    # Digitize times into bins.
    bin_idx = np.digitize(times, edges) - 1  # 0-indexed
    nbins = len(edges) - 1
    sum_per_bin = np.bincount(bin_idx, weights=row_mean, minlength=nbins)
    count_per_bin = np.bincount(bin_idx, minlength=nbins)
    mean_amp = np.full(nbins, np.nan)
    valid = count_per_bin > 0
    mean_amp[valid] = sum_per_bin[valid] / count_per_bin[valid]
    bin_centers = (edges[:-1] + edges[1:]) / 2.0

    return bin_centers, mean_amp


def create_dynamic_spectra(
    indir,
    binwidth=1.0,
    ncpu=10,
    uv_min=130.0,
    uv_max=500.0,
    startfreq=None,
    endfreq=None,
):
    """
    Process all MS files in 'indir' in parallel to create a dynamic spectrum.

    Each MS file (each representing one subband) is processed to extract its
    time bin centers and mean amplitude (filtered for uv-lambda between uv_min and uv_max).

    Returns:
      all_times (np.array): Sorted array of unique time bin centers (in seconds).
      dynamic_spectra (2D np.array): Array of shape (Ntime, Nsub) with mean amplitudes.
         Missing values are filled with NaN.
      subband_indices (list): List of subband numbers corresponding to each column.
      subband_freqs (list): List of frequencies (in MHz) for each subband.
    """
    if (startfreq is None and endfreq is None) or (startfreq == 0.0 and endfreq == 0.0):
        msfiles = sorted(
            glob.glob(os.path.join(indir, "*.MS")),
            key=lambda x: extract_sb_number(os.path.basename(x)),
        )
    elif startfreq < 0.0 or endfreq < 0.0:
        raise RuntimeError(
            f"Invalid frequency range: {startfreq} to {endfreq}. Exiting..."
        )
    else:
        from pipeline.tasks.basic_functions import get_filtered_MSs_for_given_freq_range

        msfiles = get_filtered_MSs_for_given_freq_range(indir, startfreq, endfreq)
    if len(msfiles) == 0:
        raise RuntimeError(f"No MS files found in {indir}")

    results = {}
    subband_freqs = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=ncpu) as executor:
        futures = {
            executor.submit(
                read_ms, msfile, binwidth, uv_min=uv_min, uv_max=uv_max
            ): msfile
            for msfile in msfiles
        }
        for future in concurrent.futures.as_completed(futures):
            msfile = futures[future]
            try:
                t, amp = future.result()
                results[msfile] = (t, amp)
            except Exception as e:
                print(f"Error processing {msfile}: {e}")
        for msfile in msfiles:
            try:
                freq = read_frequency(msfile)
                subband_freqs.append(freq / 1e6)
            except Exception as e:
                print(f"Error reading frequency from {msfile}: {e}")
                subband_freqs.append(np.nan)

    all_times_set = set()
    subband_indices = []
    for msfile in msfiles:
        if msfile in results:
            t, amp = results[msfile]
            all_times_set.update(t)
            subband_indices.append(extract_sb_number(os.path.basename(msfile)))
        else:
            subband_indices.append(extract_sb_number(os.path.basename(msfile)))
    all_times = np.array(sorted(list(all_times_set)))
    Ntime = len(all_times)
    Nsub = len(msfiles)
    dynamic_spectra = np.full((Ntime, Nsub), np.nan, dtype=np.float32)
    for s, msfile in enumerate(msfiles):
        if msfile not in results:
            continue
        t, amp = results[msfile]
        time_amp = {t[i]: amp[i] for i in range(len(t))}
        for i, gt in enumerate(all_times):
            if gt in time_amp:
                dynamic_spectra[i, s] = time_amp[gt]
    return all_times, dynamic_spectra, subband_indices, subband_freqs


def write_fits(all_times, dynamic_spectra, subband_freqs, outfits):
    """
    Write the dynamic spectrum to a FITS file.

    The primary HDU holds the dynamic spectrum (axes: TIME x Subband).
    Two BinTable HDUs are added:
      - One for the time axis (TIME_MJD and UTC columns).
      - One for the subband frequencies (FREQ_MHz column).
    """
    # Convert global time (in seconds) to MJD and UTC.
    time_mjd = all_times / 86400.0
    utc_times = Time(time_mjd, format="mjd", scale="utc").iso

    # Primary HDU with dynamic spectrum.
    hdu = fits.PrimaryHDU(dynamic_spectra)
    hdr = hdu.header
    hdr["EXTNAME"] = "DYNAMIC_SPECTRUM"
    hdr["BUNIT"] = "Amplitude"
    hdr["COMMENT"] = "Axis0 = time (s), Axis1 = subband (sorted by _SBxxx_ in filename)"

    # BinTable HDU for time axis.
    col_time = fits.Column(name="TIME_MJD", format="E", array=time_mjd)
    col_utc = fits.Column(name="UTC", format="20A", array=np.array(utc_times))
    time_hdu = fits.BinTableHDU.from_columns([col_time, col_utc])
    time_hdu.name = "TIME_AXIS"

    # BinTable HDU for frequency axis.
    col_freq = fits.Column(name="FREQ_MHz", format="E", array=np.array(subband_freqs))
    freq_hdu = fits.BinTableHDU.from_columns([col_freq])
    freq_hdu.name = "FREQ_AXIS"

    hdul = fits.HDUList([hdu, time_hdu, freq_hdu])
    hdu.header.add_history("Dynamic spectrum created with SolarViewer")
    hdul.writeto(outfits, overwrite=True)
    print(f"Dynamic spectra saved to {outfits}")


def run_dynamic_spectra(
    indir,
    outfits,
    binwidth=1.0,
    ncpu=10,
    uvmin=130.0,
    uvmax=500.0,
    startfreq=None,
    endfreq=None,
    saveplot=False,
    showplot=False,
    logger=None,
    plot_filename=None,
):
    """
    Process all MS files in 'indir' to create a dynamic spectrum and save to a FITS file.

    Parameters
    ----------
    indir : str
        Directory containing MS files.
    outfits : str
        Output FITS file path.
    binwidth : float, optional
        Time bin width in seconds, by default 1.0
    ncpu : int, optional
        Number of CPU cores to use, by default 10
    uvmin : float, optional
        Minimum UV baseline in lambda, by default 130.0
    uvmax : float, optional
        Maximum UV baseline in lambda, by default 500.0
    startfreq : float, optional
        Start frequency in MHz, by default None
    endfreq : float, optional
        End frequency in MHz, by default None
    saveplot : bool, optional
        Whether to save the plot, by default False
    showplot : bool, optional
        Whether to show the plot, by default False
    logger : logging.Logger, optional
        Logger instance, by default None
    plot_filename : str, optional
        Path where the plot should be saved, by default None
        If None but saveplot is True, "dynamic_spectrum.png" will be used

    Returns
    -------
    str
        Path to the output FITS file.
    """
    if logger is None:
        logger = logging.getLogger("create_dynamic_spectra")

    try:
        all_times, dynamic_spectra, subband_indices, subband_freqs = (
            create_dynamic_spectra(
                indir,
                binwidth=binwidth,
                ncpu=ncpu,
                uv_min=uvmin,
                uv_max=uvmax,
                startfreq=startfreq,
                endfreq=endfreq,
            )
        )
    except Exception as e:
        if logger:
            logger.exception(f"Error creating dynamic spectra: {e}")
        else:
            print(f"Error creating dynamic spectra: {e}")
        return None

    # For plotting, convert time (in seconds) to UTC datetime numbers.
    time_mjd = all_times / 86400.0
    utc_dt = Time(time_mjd, format="mjd", scale="utc").to_datetime()
    utc_num = [date2num(dt) for dt in utc_dt]

    # Plot dynamic spectrum with x-axis in UTC.
    if saveplot or showplot:
        from matplotlib.dates import DateFormatter

        plt.figure(figsize=(10, 6))
        extent = [utc_num[0], utc_num[-1], min(subband_freqs), max(subband_freqs)]
        plt.imshow(
            dynamic_spectra.T,
            aspect="auto",
            origin="lower",
            extent=extent,
            cmap="viridis",
        )
        plt.xlabel("Time (UTC)")
        plt.ylabel("Frequency (MHz)")
        plt.title("Dynamic Spectrum")
        plt.colorbar(label="Amplitude")
        ax = plt.gca()
        date_formatter = DateFormatter("%Y-%m-%d\n%H:%M:%S")
        ax.xaxis.set_major_formatter(date_formatter)
        plt.gcf().autofmt_xdate()

        if saveplot:
            # Use the provided plot_filename or default to "dynamic_spectrum.png"
            save_path = plot_filename if plot_filename else "dynamic_spectrum.png"
            plt.savefig(save_path, dpi=150, bbox_inches="tight")
            if logger:
                logger.info(f"Dynamic spectrum plot saved as {save_path}")
            else:
                print(f"Dynamic spectrum plot saved as {save_path}")

        if showplot:
            plt.show()
        else:
            plt.close()

    write_fits(all_times, dynamic_spectra, subband_freqs, outfits)
    return outfits


def main():
    parser = argparse.ArgumentParser(
        description="Create Dynamic Spectra from MS files in a directory."
    )
    parser.add_argument(
        "--indir", type=str, required=True, help="Directory containing MS files"
    )
    parser.add_argument(
        "--outfits",
        type=str,
        default="dynamic_spectra.fits",
        help="Output FITS file name",
    )
    parser.add_argument(
        "--binwidth",
        type=float,
        default=1.0,
        help="Time bin width in seconds (default 1 s)",
    )
    parser.add_argument(
        "--ncpu", type=int, default=10, help="Number of CPU threads to use (default 10)"
    )
    parser.add_argument(
        "--uvmin", type=float, default=130.0, help="Minimum UV baseline (lambda)"
    )
    parser.add_argument(
        "--uvmax", type=float, default=500.0, help="Maximum UV baseline (lambda)"
    )
    parser.add_argument(
        "--startfreq",
        type=float,
        default=None,
        help="Start frequency in MHz (optional)",
    )
    parser.add_argument(
        "--endfreq", type=float, default=None, help="End frequency in MHz (optional)"
    )
    parser.add_argument("--saveplot", action="store_true", help="Save plot to file")
    parser.add_argument("--showplot", action="store_true", help="Show plot in window")
    parser.add_argument(
        "--plotfile", type=str, default=None, help="Filename to save the plot"
    )
    args = parser.parse_args()

    # Setup a logger
    logger = logging.getLogger("create_dynamic_spectra")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)

    run_dynamic_spectra(
        args.indir,
        outfits=args.outfits,
        binwidth=args.binwidth,
        ncpu=args.ncpu,
        uvmin=args.uvmin,
        uvmax=args.uvmax,
        startfreq=args.startfreq,
        endfreq=args.endfreq,
        saveplot=args.saveplot,
        showplot=args.showplot,
        logger=logger,
        plot_filename=args.plotfile,
    )


if __name__ == "__main__":
    from matplotlib.dates import date2num  # imported here for plotting conversion

    main()

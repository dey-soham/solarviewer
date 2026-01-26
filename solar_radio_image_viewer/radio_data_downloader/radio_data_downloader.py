#!/usr/bin/env python3
"""
Radio Data Downloader - Core module for downloading and processing radio solar data.

This module provides functions for downloading radio solar data from various observatories
and converting it to FITS format compatible with the Dynamic Spectrum Viewer.

Currently supported:
- Learmonth Solar Observatory (Australia) - SRS spectrograph data
- San Vito (Italy) - RSTN SRS spectrograph data
- Palehua (Hawaii) - RSTN SRS spectrograph data
- Holloman (New Mexico) - RSTN SRS spectrograph data
- e-CALLISTO network - Global network of solar radio spectrometers
"""

import os
import sys
import numpy as np
import pandas as pd
from struct import unpack
from datetime import datetime
from scipy.signal import medfilt
from scipy import interpolate
from typing import Optional, Tuple, List
import urllib.request
import urllib.error
import tempfile
import gzip
import shutil


# ============================================================================
# RSTN Site Configuration
# ============================================================================

# All RSTN sites use the same 826-byte SRS binary format
RSTN_SITES = {
    "Learmonth": {
        "id": 3,
        "file_prefix": "LM",  # Uppercase for BOM Australia
        "location": "Australia",
        "latitude": -22.22,
        "longitude": 114.09,
        "url_template": "https://downloads.sws.bom.gov.au/wdc/wdc_spec/data/learmonth/raw/{year2}/{filename}",
        "alt_url_template": "https://www.ngdc.noaa.gov/stp/space-weather/solar-data/solar-features/solar-radio/rstn-spectral/learmonth/{year4}/{month}/{filename_lower}.gz",
    },
    "San Vito": {
        "id": 4,
        "file_prefix": "sv",  # Lowercase for NOAA NCEI
        "location": "Italy",
        "latitude": 40.63,
        "longitude": 17.86,
        "url_template": "https://www.ngdc.noaa.gov/stp/space-weather/solar-data/solar-features/solar-radio/rstn-spectral/san-vito/{year4}/{month}/{filename}.gz",
        "alt_url_template": "https://www.ngdc.noaa.gov/stp/space-weather/solar-data/solar-features/solar-radio/rstn-spectral/san-vito/{year4}/{month}/{filename}",
    },
    "Palehua": {
        "id": 1,
        "file_prefix": "kp",  # Ka'ena Point prefix used by NOAA
        "location": "Hawaii, USA",
        "latitude": 21.42,
        "longitude": -158.03,
        "url_template": "https://www.ngdc.noaa.gov/stp/space-weather/solar-data/solar-features/solar-radio/rstn-spectral/palehua/{year4}/{month}/{filename}.gz",
        "alt_url_template": "https://www.ngdc.noaa.gov/stp/space-weather/solar-data/solar-features/solar-radio/rstn-spectral/palehua/{year4}/{month}/{filename}",
    },
    "Holloman": {
        "id": 2,
        "file_prefix": "ho",  # Lowercase for NOAA NCEI
        "location": "New Mexico, USA",
        "latitude": 32.95,
        "longitude": -106.01,
        "url_template": "https://www.ngdc.noaa.gov/stp/space-weather/solar-data/solar-features/solar-radio/rstn-spectral/holloman/{year4}/{month}/{filename}.gz",
        "alt_url_template": "https://www.ngdc.noaa.gov/stp/space-weather/solar-data/solar-features/solar-radio/rstn-spectral/holloman/{year4}/{month}/{filename}",
        "note": "Data limited: approximately April 2000 to July 2004",
    },
}

SITE_NAMES = {
    1: "Palehua",
    2: "Holloman",
    3: "Learmonth",
    4: "San Vito",
}

# ============================================================================
# e-CALLISTO Network Configuration
# ============================================================================

# Popular e-CALLISTO observatories
ECALLISTO_OBSERVATORIES = [
    "BIR",           # Birr, Ireland
    "OOTY",          # Ooty Radio Telescope, India
    "ALASKA",        # Alaska, USA
    "ALMATY",        # Almaty, Kazakhstan
    "AUSTRALIA-ASSA",# Australia
    "DARO",          # Swiss Alps
    "DENMARK",       # Denmark
    "EGYPT",         # Egypt
    "GAURI",         # India
    "GERMAN",        # Germany
    "GLASGOW",       # Scotland, UK
    "GREENLAND",     # Greenland
    "HUMAIN",        # Belgium
    "INDONESIA",     # Indonesia
    "KASI",          # Korea
    "KENYA",         # Kenya
    "MEXICO",        # Mexico
    "MONGOLIA",      # Mongolia
    "NORWAY",        # Norway
    "PERU",          # Peru
    "ROSWELL",       # New Mexico, USA
    "RWANDA",        # Rwanda
    "SPAIN",         # Spain
    "SWISS",         # Switzerland
    "TAIWAN",        # Taiwan
    "THAILAND",      # Thailand
    "TRIEST",        # Italy
    "USA-BOSTON",    # Boston, USA
    "USA-HAYSTACK",  # Haystack Observatory, USA
]


def fetch_ecallisto_observatories(
    date: str,
    progress_callback=None,
) -> List[str]:
    """
    Fetch available e-CALLISTO observatories from the server for a given date.

    Args:
        date: Date string in format 'YYYY-MM-DD'
        progress_callback: Optional callback for progress updates

    Returns:
        Sorted list of observatory names available for that date
    """
    import re
    from datetime import datetime as dt

    try:
        date_obj = dt.strptime(date, "%Y-%m-%d")
    except ValueError as e:
        if progress_callback:
            progress_callback(f"Error parsing date: {e}")
        return []

    # Build URL for the date directory
    date_url = f"{ECALLISTO_BASE_URL}/{date_obj.year:04d}/{date_obj.month:02d}/{date_obj.day:02d}/"

    if progress_callback:
        progress_callback(f"Fetching observatory list for {date}...")

    try:
        req = urllib.request.urlopen(date_url, timeout=15)
        html = req.read().decode('utf-8')
    except urllib.error.HTTPError as e:
        if progress_callback:
            progress_callback(f"No data found for {date}: {e}")
        return []
    except Exception as e:
        if progress_callback:
            progress_callback(f"Error accessing server: {e}")
        return []

    # Parse HTML for .fit files and extract observatory names
    fit_pattern = re.compile(r'href="([^"]+\.fit(?:\.gz)?)"', re.IGNORECASE)
    all_fit_files = fit_pattern.findall(html)

    if not all_fit_files:
        if progress_callback:
            progress_callback(f"No .fit files found for {date}")
        return []

    # Extract unique observatory names from filenames
    # Filename format: OBSERVATORY_YYYYMMDD_HHMMSS_NN.fit.gz
    observatories = set()
    for filename in all_fit_files:
        # Observatory is everything before the first underscore followed by date
        parts = filename.split('_')
        if len(parts) >= 2:
            # Handle observatories with hyphens like "ALASKA-COHOE"
            # Find where the date part starts (8-digit number)
            obs_parts = []
            for i, part in enumerate(parts):
                if len(part) == 8 and part.isdigit():
                    # This is the date part, observatory is everything before
                    obs_name = '_'.join(parts[:i])
                    if obs_name:
                        observatories.add(obs_name)
                    break

    sorted_obs = sorted(observatories)

    if progress_callback:
        progress_callback(f"Found {len(sorted_obs)} observatories for {date}")

    return sorted_obs

# ============================================================================
# SRS File Parser (adapted from learmonth-py/srs_data.py)
# ============================================================================

RECORD_SIZE = 826
RECORD_HEADER_SIZE = 24
RECORD_ARRAY_SIZE = 401


class SRSRecord:
    """Holds one 826 byte SRS Record."""

    def __init__(self):
        self.year = None
        self.month = None
        self.day = None
        self.hour = None
        self.minute = None
        self.seconds = None
        self.site_number = None
        self.site_name = None
        self.n_bands_per_record = None

        self.a_start_freq = None
        self.a_end_freq = None
        self.a_values = {}  # frequency -> level

        self.b_start_freq = None
        self.b_end_freq = None
        self.b_values = {}  # frequency -> level

    def _parse_header(self, header_bytes):
        """Parse the 24-byte record header."""
        fields = unpack(
            ">BBBBBBBB"  # Year, Month, Day, Hour, Minute, Second, Site, n_bands
            "hHHBB"  # A-band: start, end, n_bytes, ref_level, attenuation
            "HHHBB",  # B-band: start, end, n_bytes, ref_level, attenuation
            header_bytes,
        )

        self.year = fields[0]
        self.month = fields[1]
        self.day = fields[2]
        self.hour = fields[3]
        self.minute = fields[4]
        self.seconds = fields[5]
        self.site_number = fields[6]
        self.site_name = SITE_NAMES.get(self.site_number, "Unknown")
        self.n_bands_per_record = fields[7]

        self.a_start_freq = fields[8]
        self.a_end_freq = fields[9]
        self.b_start_freq = fields[13]
        self.b_end_freq = fields[14]

    def _parse_a_levels(self, a_bytes):
        """Parse the A-band (25-75 MHz) levels."""
        for i in range(401):
            freq_a = 25 + 50 * i / 400.0
            level_a = unpack(">B", a_bytes[i : i + 1])[0]
            self.a_values[freq_a] = level_a

    def _parse_b_levels(self, b_bytes):
        """Parse the B-band (75-180 MHz) levels."""
        for i in range(401):
            freq_b = 75 + 105 * i / 400.0
            level_b = unpack(">B", b_bytes[i : i + 1])[0]
            self.b_values[freq_b] = level_b

    def get_timestamp(self) -> datetime:
        """Get the timestamp for this record."""
        # Handle 2-digit year
        full_year = 2000 + self.year if self.year < 100 else self.year
        return datetime(
            full_year, self.month, self.day, self.hour, self.minute, self.seconds
        )

    def __str__(self):
        return f"{self.day:02d}/{self.month:02d}/{self.year:02d}, {self.hour:02d}:{self.minute:02d}:{self.seconds:02d}"


def read_srs_file(fname: str) -> List[SRSRecord]:
    """Parse an SRS file and return a list of SRSRecord objects."""
    srs_records = []
    with open(fname, "rb") as f:
        while True:
            record_data = f.read(RECORD_SIZE)
            if len(record_data) == 0:
                break
            if len(record_data) < RECORD_SIZE:
                break

            header_bytes = record_data[:RECORD_HEADER_SIZE]
            a_bytes = record_data[
                RECORD_HEADER_SIZE : RECORD_HEADER_SIZE + RECORD_ARRAY_SIZE
            ]
            b_bytes = record_data[
                RECORD_HEADER_SIZE
                + RECORD_ARRAY_SIZE : RECORD_HEADER_SIZE
                + 2 * RECORD_ARRAY_SIZE
            ]

            record = SRSRecord()
            record._parse_header(header_bytes)
            record._parse_a_levels(a_bytes)
            record._parse_b_levels(b_bytes)
            srs_records.append(record)

    return srs_records


# ============================================================================
# Download Functions
# ============================================================================


def download_rstn_data(
    site: str,
    date: str,
    output_dir: str = ".",
    progress_callback=None,
) -> Optional[str]:
    """
    Download RSTN spectrograph data for a given site and date.

    Args:
        site: Station name (Learmonth, San Vito, Palehua, Holloman)
        date: Date in format 'YYYY-MM-DD' or 'DD-MM-YYYY'
        output_dir: Directory to save the downloaded file
        progress_callback: Optional callback function for progress updates

    Returns:
        Path to the downloaded SRS file, or None if download failed
    """
    # Validate site
    if site not in RSTN_SITES:
        if progress_callback:
            progress_callback(
                f"Unknown site: {site}. Available: {list(RSTN_SITES.keys())}"
            )
        return None

    site_config = RSTN_SITES[site]

    # Parse the date
    try:
        if "-" in date:
            parts = date.split("-")
            if len(parts[0]) == 4:  # YYYY-MM-DD
                dt = datetime.strptime(date, "%Y-%m-%d")
            else:  # DD-MM-YYYY
                dt = datetime.strptime(date, "%d-%m-%Y")
        else:
            raise ValueError(f"Invalid date format: {date}")
    except ValueError as e:
        if progress_callback:
            progress_callback(f"Error parsing date: {e}")
        return None

    # Construct filename
    year2 = str(dt.year)[2:]  # Last 2 digits
    year4 = str(dt.year)  # Full year
    month = f"{dt.month:02d}"
    day_stamp = f"{dt.day:02d}"
    prefix = site_config["file_prefix"]
    file_name = f"{prefix}{year2}{month}{day_stamp}.srs"
    file_name_lower = file_name.lower()  # For NOAA URLs

    output_path = os.path.join(output_dir, file_name)

    # Check if file already exists
    if os.path.exists(output_path):
        if progress_callback:
            progress_callback(f"File already exists: {file_name}")
        return output_path

    # Create output directory if needed
    os.makedirs(output_dir, exist_ok=True)

    # Construct download URL
    url_template = site_config["url_template"]
    download_url = url_template.format(
        year2=year2,
        year4=year4,
        month=month,
        filename=file_name,
        filename_lower=file_name_lower,
    )

    if progress_callback:
        progress_callback(f"Downloading from: {download_url}")

    # Helper function to download and decompress if needed
    def download_and_decompress(url, out_path):
        import gzip
        import shutil
        import tempfile

        if url.endswith(".gz"):
            # Download to temp file, then decompress
            with tempfile.NamedTemporaryFile(delete=False, suffix=".srs.gz") as tmp:
                tmp_path = tmp.name
            urllib.request.urlretrieve(url, tmp_path)
            with gzip.open(tmp_path, "rb") as f_in:
                with open(out_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            os.remove(tmp_path)
        else:
            urllib.request.urlretrieve(url, out_path)

    try:
        download_and_decompress(download_url, output_path)
        if progress_callback:
            progress_callback(f"Downloaded: {file_name}")
        return output_path
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        # Try alternate URL if available
        if "alt_url_template" in site_config:
            alt_url = site_config["alt_url_template"].format(
                year2=year2,
                year4=year4,
                month=month,
                filename=file_name,
                filename_lower=file_name_lower,
            )
            if progress_callback:
                progress_callback(f"Primary URL failed, trying alternate: {alt_url}")
            try:
                download_and_decompress(alt_url, output_path)
                if progress_callback:
                    progress_callback(f"Downloaded: {file_name}")
                return output_path
            except Exception as e2:
                if progress_callback:
                    progress_callback(f"Alternate URL also failed: {e2}")

        if progress_callback:
            progress_callback(f"Download failed: {e}")
        return None
    except Exception as e:
        if progress_callback:
            progress_callback(f"Download error: {e}")
        return None


def download_learmonth(
    date: str,
    output_dir: str = ".",
    progress_callback=None,
) -> Optional[str]:
    """
    Download Learmonth spectrograph data for a given date.

    This is a convenience wrapper around download_rstn_data for backwards compatibility.

    Args:
        date: Date in format 'YYYY-MM-DD' or 'DD-MM-YYYY'
        output_dir: Directory to save the downloaded file
        progress_callback: Optional callback function for progress updates

    Returns:
        Path to the downloaded SRS file, or None if download failed
    """
    return download_rstn_data("Learmonth", date, output_dir, progress_callback)


# ============================================================================
# e-CALLISTO Download Functions
# ============================================================================

# e-CALLISTO data server base URL
ECALLISTO_BASE_URL = "http://soleil80.cs.technik.fhnw.ch/solarradio/data/2002-20yy_Callisto"


def download_ecallisto_data(
    start_time: str,
    end_time: str,
    observatory: Optional[str] = None,
    output_dir: str = ".",
    progress_callback=None,
) -> List[str]:
    """
    Download e-CALLISTO spectrograph data for a given time range.

    Uses direct HTTP access to the e-CALLISTO data server.

    Args:
        start_time: Start datetime in format 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DDTHH:MM:SS'
        end_time: End datetime in format 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DDTHH:MM:SS'
        observatory: Optional observatory name (e.g., 'BIR', 'OOTY', 'ALASKA-COHOE'). If None, downloads first available.
        output_dir: Directory to save the downloaded files
        progress_callback: Optional callback function for progress updates

    Returns:
        List of paths to downloaded files, or empty list if download failed
    """
    import re
    from datetime import datetime as dt
    
    # Normalize time format
    start_time = start_time.replace("T", " ")
    end_time = end_time.replace("T", " ")
    
    try:
        start_dt = dt.strptime(start_time, "%Y-%m-%d %H:%M:%S")
        end_dt = dt.strptime(end_time, "%Y-%m-%d %H:%M:%S")
    except ValueError as e:
        if progress_callback:
            progress_callback(f"Error parsing time: {e}")
        return []
    
    if progress_callback:
        obs_msg = f" from {observatory}" if observatory else " (searching observatories)"
        progress_callback(f"Searching e-CALLISTO data{obs_msg}...")

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)
    
    downloaded_files = []
    
    try:
        # Build URL for the date directory
        # e-CALLISTO files are organized by date: BASE_URL/YYYY/MM/DD/
        date_url = f"{ECALLISTO_BASE_URL}/{start_dt.year:04d}/{start_dt.month:02d}/{start_dt.day:02d}/"
        
        if progress_callback:
            progress_callback(f"Accessing: {date_url}")
        
        # Fetch directory listing
        try:
            req = urllib.request.urlopen(date_url, timeout=30)
            html = req.read().decode('utf-8')
        except urllib.error.HTTPError as e:
            if progress_callback:
                progress_callback(f"No data found for {start_dt.date()}: {e}")
            return []
        except Exception as e:
            if progress_callback:
                progress_callback(f"Error accessing server: {e}")
            return []
        
        # Parse HTML for .fit files
        # Pattern: observatory_YYYYMMDD_HHMMSS_NN.fit.gz or .fit
        fit_pattern = re.compile(r'href="([^"]+\.fit(?:\.gz)?)"', re.IGNORECASE)
        all_fit_files = fit_pattern.findall(html)
        
        if not all_fit_files:
            if progress_callback:
                progress_callback(f"No .fit files found for {start_dt.date()}")
            return []
        
        if progress_callback:
            progress_callback(f"Found {len(all_fit_files)} total files on server")
        
        # Filter by observatory if specified
        if observatory and observatory != "All Observatories":
            # Observatory name is at the start of the filename
            obs_upper = observatory.upper()
            matching_files = [f for f in all_fit_files if f.upper().startswith(obs_upper)]
            if progress_callback:
                progress_callback(f"Found {len(matching_files)} files for {observatory}")
        else:
            matching_files = all_fit_files
            # Get unique observatories from files
            obs_set = set()
            #for f in all_fit_files[:100]:  # Sample first 100 files
            for f in all_fit_files:
                parts = f.split('_')
                if parts:
                    obs_set.add(parts[0])
            if progress_callback and obs_set:
                progress_callback(f"Available observatories: {', '.join(sorted(obs_set)[:10])}")
        
        if not matching_files:
            if progress_callback:
                progress_callback(f"No files found for observatory: {observatory}")
            return []
        
        # Filter by time range
        # Filename format: OBSERVATORY_YYYYMMDD_HHMMSS_NN.fit.gz
        time_filtered = []
        for filename in matching_files:
            try:
                # Extract datetime from filename
                parts = filename.replace('.fit.gz', '').replace('.fit', '').split('_')
                if len(parts) >= 3:
                    file_date = parts[-3] if len(parts[-3]) == 8 else parts[1]
                    file_time = parts[-2] if len(parts[-2]) == 6 else parts[2]
                    file_dt = dt.strptime(f"{file_date}_{file_time}", "%Y%m%d_%H%M%S")
                    
                    # Check if within time range
                    if start_dt <= file_dt <= end_dt:
                        time_filtered.append(filename)
            except (ValueError, IndexError):
                continue
        
        if progress_callback:
            progress_callback(f"Found {len(time_filtered)} files in time range")
        
        # If no files in exact time range, take first few files for the day
        files_to_download = time_filtered if time_filtered else matching_files[:5]
        
        # Limit downloads to avoid overwhelming
        '''max_files = 2000
        if len(files_to_download) > max_files:
            if progress_callback:
                progress_callback(f"Limiting download to {max_files} files (out of {len(files_to_download)})")
            files_to_download = files_to_download[:max_files]'''
        
        # Download files
        for i, filename in enumerate(files_to_download):
            file_url = f"{date_url}{filename}"
            output_path = os.path.join(output_dir, filename)
            
            # Skip if already exists
            if os.path.exists(output_path):
                if progress_callback:
                    progress_callback(f"Already exists: {filename}")
                downloaded_files.append(output_path)
                continue
            
            try:
                if progress_callback:
                    progress_callback(f"Downloading ({i+1}/{len(files_to_download)}): {filename}")
                urllib.request.urlretrieve(file_url, output_path)
                downloaded_files.append(output_path)
            except Exception as e:
                if progress_callback:
                    progress_callback(f"Error downloading {filename}: {e}")
        
        if progress_callback:
            progress_callback(f"Downloaded {len(downloaded_files)} files")
        
        return downloaded_files
        
    except Exception as e:
        if progress_callback:
            progress_callback(f"Error downloading e-CALLISTO data: {e}")
        return []


def list_ecallisto_observatories(
    start_time: str,
    end_time: str,
    progress_callback=None,
) -> List[str]:
    """
    List available e-CALLISTO observatories for a given time range.

    Args:
        start_time: Start datetime in format 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DDTHH:MM:SS'
        end_time: End datetime in format 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DDTHH:MM:SS'
        progress_callback: Optional callback function for progress updates

    Returns:
        List of observatory names with data in the time range
    """
    try:
        from sunpy.net import Fido, attrs as a
    except ImportError as e:
        if progress_callback:
            progress_callback(f"Error: sunpy is required. {e}")
        return []

    # Normalize time format
    start_time = start_time.replace("T", " ")
    end_time = end_time.replace("T", " ")

    if progress_callback:
        progress_callback(f"Searching for available e-CALLISTO observatories...")

    try:
        res = Fido.search(
            a.Time(start_time, end_time),
            a.Instrument("ecallisto"),
        )

        if len(res) == 0:
            if progress_callback:
                progress_callback("No e-CALLISTO data found for this time range")
            return []

        # Extract unique observatories
        if "callisto" in res.keys():
            try:
                observatories = list(res["callisto"]["Observatory"].data)
                unique_obs = sorted(set(observatories))
                if progress_callback:
                    progress_callback(f"Found {len(unique_obs)} observatories with data")
                return unique_obs
            except Exception:
                pass

        return []

    except Exception as e:
        if progress_callback:
            progress_callback(f"Error querying observatories: {e}")
        return []


def ecallisto_to_fits(
    input_file: str,
    output_file: Optional[str] = None,
    progress_callback=None,
) -> Optional[str]:
    """
    Convert e-CALLISTO .fit/.fit.gz file to FITS format compatible with Dynamic Spectrum Viewer.
    
    Reads e-CALLISTO files directly using astropy (no radiospectra required).

    Args:
        input_file: Path to the e-CALLISTO .fit or .fit.gz file
        output_file: Optional output FITS file path. If None, uses input filename with _spectrum.fits suffix
        progress_callback: Optional callback for progress updates

    Returns:
        Path to the created FITS file, or None if failed
    """
    try:
        from astropy.io import fits as pyfits
        from astropy.time import Time
        from astropy.table import Table
    except ImportError:
        if progress_callback:
            progress_callback("Error: astropy is required for FITS output")
        return None

    if progress_callback:
        progress_callback(f"Reading e-CALLISTO file: {os.path.basename(input_file)}")

    try:
        # Read the e-CALLISTO FITS file directly with astropy
        # Handle gzipped files
        with pyfits.open(input_file) as hdul:
            # e-CALLISTO standard format:
            # Primary HDU (HDU 0): Contains the spectrogram data (n_freq, n_time)
            # BinTable HDU (HDU 1): Contains BOTH TIME and FREQUENCY columns (single-row arrays)
            
            primary_header = hdul[0].header
            data = hdul[0].data  # Shape: (n_freq, n_time) typically
            
            if data is None:
                if progress_callback:
                    progress_callback("Error: No data in primary HDU")
                return None
            
            # Get observatory info from header
            observatory = primary_header.get('INSTRUME', 'e-CALLISTO')
            if not observatory:
                observatory = primary_header.get('CONTENT', 'e-CALLISTO')
            
            # Get date/time from header
            date_obs = primary_header.get('DATE-OBS', primary_header.get('DATE_OBS', ''))
            time_obs = primary_header.get('TIME-OBS', primary_header.get('TIME_OBS', '00:00:00'))
            
            # Try to get time and frequency axes from HDU 1
            time_seconds = None
            frequencies = None
            time_mjd = None
            
            if len(hdul) > 1 and hasattr(hdul[1], 'columns') and hdul[1].columns:
                try:
                    col_names = [c.upper() for c in hdul[1].columns.names]
                    
                    # e-CALLISTO format: single-row table with arrays
                    table_data = hdul[1].data
                    
                    # Extract TIME column
                    if 'TIME' in col_names:
                        idx = col_names.index('TIME')
                        time_arr = table_data[hdul[1].columns.names[idx]]
                        # Flatten if it's a nested array (single-row table format)
                        if hasattr(time_arr, 'flatten'):
                            time_seconds = time_arr.flatten()
                        else:
                            time_seconds = np.array(time_arr).flatten()
                    
                    # Extract FREQUENCY column
                    if 'FREQUENCY' in col_names:
                        idx = col_names.index('FREQUENCY')
                        freq_arr = table_data[hdul[1].columns.names[idx]]
                        # Flatten if it's a nested array
                        if hasattr(freq_arr, 'flatten'):
                            frequencies = freq_arr.flatten()
                        else:
                            frequencies = np.array(freq_arr).flatten()
                    
                    if progress_callback:
                        if time_seconds is not None:
                            progress_callback(f"Time axis: {len(time_seconds)} points, range {time_seconds[0]:.2f}s - {time_seconds[-1]:.2f}s")
                        if frequencies is not None:
                            progress_callback(f"Freq axis: {len(frequencies)} points, range {frequencies.min():.2f} - {frequencies.max():.2f} MHz")
                            
                except Exception as e:
                    if progress_callback:
                        progress_callback(f"Warning: Could not read time/freq axes from HDU 1: {e}")
            
            # Convert time_seconds to MJD
            if time_seconds is not None and date_obs:
                try:
                    # Parse date and time
                    date_obs_clean = date_obs.replace('/', '-')
                    ref_time_str = f"{date_obs_clean}T{time_obs.split('.')[0]}"  # Remove milliseconds from time
                    ref_time = Time(ref_time_str, format='isot')
                    
                    # Convert seconds to days and add to ref MJD
                    time_mjd = ref_time.mjd + (time_seconds / 86400.0)
                    
                    if progress_callback:
                        progress_callback(f"Reference time: {ref_time_str}")
                except Exception as e:
                    if progress_callback:
                        progress_callback(f"Warning: Could not parse time: {e}")
            
            # Fallback: generate axes from header if not found
            if time_seconds is None:
                # Generate time axis from data shape
                n_time = data.shape[1] if len(data.shape) > 1 else data.shape[0]
                time_seconds = np.arange(n_time) * 0.25  # Assume 0.25s cadence
                if date_obs:
                    date_obs_clean = date_obs.replace('/', '-')
                    ref_time_str = f"{date_obs_clean}T{time_obs.split('.')[0]}"
                    try:
                        ref_time = Time(ref_time_str, format='isot')
                        time_mjd = ref_time.mjd + (time_seconds / 86400.0)
                    except Exception:
                        pass
                        
            if frequencies is None:
                # Generate frequency axis from header or defaults
                try:
                    freq_min = float(primary_header.get('CRVAL1', 45.0))
                    freq_delta = float(primary_header.get('CDELT1', 0.25))
                    n_freq = data.shape[0]
                    frequencies = np.arange(n_freq) * freq_delta + freq_min
                except Exception:
                    frequencies = np.arange(data.shape[0])
            
            if progress_callback:
                progress_callback(f"Observatory: {observatory}")
                progress_callback(f"Data shape: {data.shape} (freq x time)")

        # Create output filename if not specified
        if output_file is None:
            base_name = os.path.basename(input_file)
            # Remove .fit.gz or .fit extension
            if base_name.endswith(".fit.gz"):
                base_name = base_name[:-7]
            elif base_name.endswith(".fit"):
                base_name = base_name[:-4]
            output_file = os.path.join(os.path.dirname(input_file), f"{base_name}_spectrum.fits")

        # Ensure frequencies are in ascending order (lower frequencies first)
        if frequencies is not None and len(frequencies) > 1:
            if frequencies[0] > frequencies[-1]:
                # Frequency is descending, flip both data and frequencies
                data = np.flip(data, axis=0)
                frequencies = np.flip(frequencies)
                if progress_callback:
                    progress_callback("Flipped frequency axis to ascending order")

        data = np.array(data).astype(np.float32).T

        # Create primary HDU with the data
        hdu = pyfits.PrimaryHDU(data)
        header = hdu.header

        # Basic info
        header["TELESCOP"] = str(observatory)
        header["INSTRUME"] = "e-CALLISTO"
        header["OBJECT"] = "Sun"
        header["BUNIT"] = "arbitrary"

        # Time info from original header or calculated
        if date_obs:
            date_obs_formatted = date_obs.replace('/', '-')
            header["DATE-OBS"] = f"{date_obs_formatted}T{time_obs.split('.')[0]}"
            # Calculate end time if we have time data
            if time_seconds is not None and len(time_seconds) > 0:
                from astropy.time import Time as AstropyTime
                try:
                    ref_time = AstropyTime(f"{date_obs_formatted}T{time_obs.split('.')[0]}", format='isot')
                    end_time = ref_time + (time_seconds[-1] / 86400.0)
                    header["DATE-END"] = end_time.isot
                except Exception:
                    pass
        header["TIMESYS"] = "UTC"

        # Frequency axis (axis 1 = rows = frequency)
        header["CTYPE1"] = "FREQ"
        header["CUNIT1"] = "MHz"
        header["CRPIX1"] = 1
        header["CRVAL1"] = float(frequencies[0]) if frequencies is not None else 45.0
        if frequencies is not None and len(frequencies) > 1:
            header["CDELT1"] = float(frequencies[1] - frequencies[0])
        else:
            header["CDELT1"] = 1.0
        header["NAXIS1"] = len(frequencies) if frequencies is not None else data.shape[0]

        # Time axis (axis 2 = columns = time)
        header["CTYPE2"] = "TIME"
        header["CUNIT2"] = "s"
        header["CRPIX2"] = 1
        header["CRVAL2"] = 0.0
        header["CDELT2"] = 0.25  # e-CALLISTO typically 0.25s cadence
        header["NAXIS2"] = data.shape[1] if len(data.shape) > 1 else 1

        # Frequency range for convenience
        if frequencies is not None:
            header["FREQ_MIN"] = float(np.nanmin(frequencies))
            header["FREQ_MAX"] = float(np.nanmax(frequencies))

        # History
        header["HISTORY"] = "Created by SolarViewer"
        header["HISTORY"] = f"Source: e-CALLISTO {observatory}"

        # Create HDU list
        hdul_out = pyfits.HDUList([hdu])

        # Add TIME_AXIS extension with MJD times if available
        if time_mjd is not None:
            try:
                time_table = Table()
                time_table["TIME_MJD"] = time_mjd
                time_hdu = pyfits.BinTableHDU(time_table, name="TIME_AXIS")
                hdul_out.append(time_hdu)
                if progress_callback:
                    progress_callback("Added TIME_AXIS extension")
            except Exception as e:
                if progress_callback:
                    progress_callback(f"Warning: Could not add TIME_AXIS: {e}")

        # Add FREQ_AXIS extension
        if frequencies is not None:
            try:
                freq_table = Table()
                freq_table["FREQ_MHz"] = np.array(frequencies).astype(np.float64)
                freq_hdu = pyfits.BinTableHDU(freq_table, name="FREQ_AXIS")
                hdul_out.append(freq_hdu)
                if progress_callback:
                    progress_callback("Added FREQ_AXIS extension")
            except Exception as e:
                if progress_callback:
                    progress_callback(f"Warning: Could not add FREQ_AXIS: {e}")

        # Ensure output directory exists
        out_dir = os.path.dirname(output_file)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        hdul_out.writeto(output_file, overwrite=True)

        if progress_callback:
            progress_callback(f"FITS file created: {output_file}")

        return output_file

    except Exception as e:
        if progress_callback:
            progress_callback(f"Error converting e-CALLISTO file: {e}")
        return None


def download_and_convert_ecallisto(
    start_time: str,
    end_time: str,
    observatory: Optional[str] = None,
    output_dir: str = ".",
    progress_callback=None,
) -> List[str]:
    """
    Download e-CALLISTO data and convert to FITS format in one step.

    Args:
        start_time: Start datetime in format 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DDTHH:MM:SS'
        end_time: End datetime in format 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DDTHH:MM:SS'
        observatory: Optional observatory name (e.g., 'BIR', 'OOTY'). If None, searches all.
        output_dir: Directory for output files
        progress_callback: Optional callback for progress updates

    Returns:
        List of paths to created FITS files
    """
    # Download e-CALLISTO files
    downloaded_files = download_ecallisto_data(
        start_time, end_time, observatory, output_dir, progress_callback
    )

    if not downloaded_files:
        return []

    # Convert each file to FITS
    fits_files = []
    for f in downloaded_files:
        if f.endswith(".fit") or f.endswith(".fit.gz"):
            fits_file = ecallisto_to_fits(f, progress_callback=progress_callback)
            if fits_file:
                fits_files.append(fits_file)

    if progress_callback:
        progress_callback(f"Converted {len(fits_files)} files to FITS format")
        progress_callback(f"Deleting {len(downloaded_files)} .fit/.fit.gz files")
        
    for f in downloaded_files:
        try:
            os.remove(f)
        except Exception:
            pass
            
    if progress_callback:
        progress_callback(f"Deleted {len(downloaded_files)} .fit/.fit.gz files")

    return fits_files


# ============================================================================
# Data Processing Functions
# ============================================================================


def fill_nan(arr: np.ndarray) -> np.ndarray:
    """Interpolate to fill NaN values in array."""
    try:
        inds = np.arange(arr.shape[0])
        good = np.where(np.isfinite(arr))
        if len(good[0]) == 0:
            return arr
        f = interpolate.interp1d(
            inds[good],
            arr[good],
            bounds_error=False,
            kind="linear",
            fill_value="extrapolate",
        )
        out_arr = np.where(np.isfinite(arr), arr, f(inds))
    except Exception:
        out_arr = arr
    return out_arr


def srs_to_dataframe(
    srs_file: str,
    bkg_sub: bool = False,
    do_flag: bool = True,
    flag_cal_time: bool = True,
    progress_callback=None,
) -> Tuple[Optional[pd.DataFrame], Optional[np.ndarray], Optional[np.ndarray]]:
    """
    Convert SRS file to pandas DataFrame with processing.

    Args:
        srs_file: Path to the SRS file
        bkg_sub: Whether to perform background subtraction
        do_flag: Whether to flag known bad channels
        flag_cal_time: Whether to flag calibration time periods
        progress_callback: Optional callback for progress updates

    Returns:
        Tuple of (DataFrame, frequencies array, timestamps array)
    """
    if progress_callback:
        progress_callback("Reading SRS file...")

    srs_records = read_srs_file(srs_file)

    if not srs_records:
        return None, None, None

    if progress_callback:
        progress_callback(f"Read {len(srs_records)} records")

    # Extract timestamps
    timestamps = [record.get_timestamp() for record in srs_records]
    timestamps = pd.to_datetime(timestamps)

    # Get frequency arrays
    a_freqs = list(srs_records[0].a_values.keys())
    b_freqs = list(srs_records[0].b_values.keys())
    freqs = np.array(a_freqs + b_freqs)
    freqs = np.round(freqs, 1)

    # Build data array
    if progress_callback:
        progress_callback("Building data array...")

    data = []
    for record in srs_records:
        a_data = list(record.a_values.values())
        b_data = list(record.b_values.values())
        data.append(a_data + b_data)

    data = np.array(data).astype("float")

    # Create DataFrame
    df = pd.DataFrame(data, index=timestamps, columns=freqs)
    df = df.sort_index(axis=0)
    df = df.sort_index(axis=1)

    # Get sorted arrays
    final_freqs = df.columns.values
    final_timestamps = df.index
    final_data = df.to_numpy().astype("float")

    if progress_callback:
        progress_callback("Processing data...")

    # Flagging bad channels
    if do_flag:
        # Known bad frequency channel ranges (as indices)
        bad_ranges = [
            (488, 499),
            (524, 533),
            (540, 550),
            (638, 642),
            (119, 129),
            (108, 111),
            (150, 160),
            (197, 199),
            (285, 289),
            (621, 632),
            (592, 600),
            (700, 712),
            (410, 416),
            (730, 741),
            (635, 645),
            (283, 292),
            (216, 222),
            (590, 602),
            (663, 667),
            (684, 690),
            (63, 66),
            (54, 59),
            (27, 31),
        ]
        for start, end in bad_ranges:
            if start < final_data.shape[1] and end <= final_data.shape[1]:
                final_data[:, start:end] = np.nan

        # Flag calibration times if requested
        if flag_cal_time:
            y = np.nanmedian(final_data, axis=1)
            c = y / medfilt(y, min(1001, len(y) // 2 * 2 + 1))  # Ensure odd kernel size
            c_std = np.nanstd(c)
            pos = np.where(c > 1 + (10 * c_std))
            final_data[pos, :] = np.nan

    # Interpolate over NaNs
    if progress_callback:
        progress_callback("Interpolating missing data...")

    for i in range(final_data.shape[0]):
        final_data[i, :] = fill_nan(final_data[i, :])

    # Flag edge channels
    if do_flag and final_data.shape[1] > 780:
        final_data[:, 780:] = np.nan

    # Background subtraction
    if bkg_sub:
        if progress_callback:
            progress_callback("Subtracting background...")
        for ch in range(final_data.shape[1]):
            median_val = np.nanmedian(final_data[:, ch])
            if median_val > 0:
                final_data[:, ch] = final_data[:, ch] / median_val

    # Create final DataFrame
    result_df = pd.DataFrame(final_data, index=final_timestamps, columns=final_freqs)

    return result_df, final_freqs, final_timestamps


def dataframe_to_fits(
    df: pd.DataFrame,
    freqs: np.ndarray,
    timestamps: pd.DatetimeIndex,
    output_file: str,
    site_name: str = "Learmonth",
    progress_callback=None,
) -> Optional[str]:
    """
    Convert DataFrame to FITS file compatible with Dynamic Spectrum Viewer.

    Args:
        df: DataFrame with shape (n_times, n_freqs)
        freqs: Frequency array in MHz
        timestamps: Timestamp array
        output_file: Output FITS file path
        site_name: Name of the observatory
        progress_callback: Optional callback for progress updates

    Returns:
        Path to the created FITS file, or None if failed
    """
    try:
        from astropy.io import fits
        from astropy.time import Time
    except ImportError:
        print("Error: astropy is required for FITS output")
        return None

    if progress_callback:
        progress_callback("Creating FITS file...")

    # Get data array (time x frequency)
    data = df.to_numpy().astype(np.float32) # df has row time and column freq

    # Create primary HDU with the data
    hdu = fits.PrimaryHDU(data)

    # Add header keywords
    header = hdu.header

    # Basic info
    header["TELESCOP"] = site_name
    header["INSTRUME"] = f"{site_name} Spectrograph"
    header["OBJECT"] = "Sun"
    header["BUNIT"] = "arbitrary"

    # Time info
    t_start = Time(timestamps[0])
    t_end = Time(timestamps[-1])
    header["DATE-OBS"] = t_start.isot
    header["DATE-END"] = t_end.isot
    header["TIMESYS"] = "UTC"

    # Frequency axis (axis 1 = rows = frequency)
    header["CTYPE1"] = "FREQ"
    header["CUNIT1"] = "MHz"
    header["CRPIX1"] = 1
    header["CRVAL1"] = float(freqs[0])
    if len(freqs) > 1:
        header["CDELT1"] = float(freqs[1] - freqs[0])
    else:
        header["CDELT1"] = 1.0
    header["NAXIS1"] = len(freqs)

    # Time axis (axis 2 = columns = time)
    header["CTYPE2"] = "TIME"
    header["CUNIT2"] = "s"
    header["CRPIX2"] = 1
    header["CRVAL2"] = 0.0
    if len(timestamps) > 1:
        dt = (timestamps[1] - timestamps[0]).total_seconds()
        header["CDELT2"] = dt
    else:
        header["CDELT2"] = 3.0  # Default 3 second cadence
    header["NAXIS2"] = len(timestamps)

    # Frequency range for convenience
    header["FREQ_MIN"] = float(np.nanmin(freqs))
    header["FREQ_MAX"] = float(np.nanmax(freqs))

    # History
    header["HISTORY"] = f"Created by SolarViewer"
    header["HISTORY"] = f"Source: {site_name} Solar Spectrograph"

    # Create HDU list starting with primary
    hdul = fits.HDUList([hdu])

    # Add TIME_AXIS extension with MJD times (required by Dynamic Spectra Viewer)
    try:
        from astropy.table import Table

        # Convert timestamps to MJD
        time_objs = Time(list(timestamps))
        time_mjd = time_objs.mjd

        time_table = Table()
        time_table["TIME_MJD"] = time_mjd
        time_hdu = fits.BinTableHDU(time_table, name="TIME_AXIS")
        hdul.append(time_hdu)

        if progress_callback:
            progress_callback("Added TIME_AXIS extension with MJD times")
    except Exception as e:
        if progress_callback:
            progress_callback(f"Warning: Could not add TIME_AXIS: {e}")

    # Add FREQ_AXIS extension with frequencies in MHz (required by Dynamic Spectra Viewer)
    try:
        freq_table = Table()
        freq_table["FREQ_MHz"] = freqs.astype(np.float64)
        freq_hdu = fits.BinTableHDU(freq_table, name="FREQ_AXIS")
        hdul.append(freq_hdu)

        if progress_callback:
            progress_callback("Added FREQ_AXIS extension with MHz frequencies")
    except Exception as e:
        if progress_callback:
            progress_callback(f"Warning: Could not add FREQ_AXIS: {e}")

    # Ensure output directory exists
    os.makedirs(
        os.path.dirname(output_file) if os.path.dirname(output_file) else ".",
        exist_ok=True,
    )

    hdul.writeto(output_file, overwrite=True)

    if progress_callback:
        progress_callback(f"FITS file created: {output_file}")

    return output_file


# ============================================================================
# High-level convenience function
# ============================================================================


def download_and_convert_rstn(
    site: str,
    date: str,
    output_dir: str = ".",
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    bkg_sub: bool = False,
    do_flag: bool = True,
    flag_cal_time: bool = True,
    progress_callback=None,
) -> Optional[str]:
    """
    Download RSTN data from any site and convert to FITS in one step.

    Args:
        site: Station name (Learmonth, San Vito, Palehua, Holloman)
        date: Date in format 'YYYY-MM-DD' or 'DD-MM-YYYY'
        output_dir: Directory for output files
        start_time: Optional start time in format 'HH:MM:SS' to filter data
        end_time: Optional end time in format 'HH:MM:SS' to filter data
        bkg_sub: Whether to perform background subtraction
        do_flag: Whether to flag known bad channels
        flag_cal_time: Whether to flag calibration time periods
        progress_callback: Optional callback for progress updates

    Returns:
        Path to the created FITS file, or None if failed
    """
    # Download SRS file
    srs_file = download_rstn_data(site, date, output_dir, progress_callback)
    if not srs_file:
        return None

    # Convert to DataFrame
    df, freqs, timestamps = srs_to_dataframe(
        srs_file, bkg_sub, do_flag, flag_cal_time, progress_callback
    )
    if df is None:
        return None

    # Filter by time range if specified
    if start_time or end_time:
        if progress_callback:
            progress_callback(
                f"Filtering time range: {start_time or 'start'} to {end_time or 'end'}"
            )

        # Debug: show actual data time range
        if progress_callback:
            progress_callback(f"Data time range: {timestamps[0]} to {timestamps[-1]}")

        original_len = len(df)

        # The SRS data can span two calendar days (e.g., Dec 24 21:43 to Dec 25 10:56 UTC)
        # So we need to filter by time-of-day, not by absolute datetime

        if start_time and end_time:
            # Parse times
            start_h, start_m, start_s = map(int, start_time.split(":"))
            end_h, end_m, end_s = map(int, end_time.split(":"))

            # Create time objects for comparison
            from datetime import time as dt_time

            start_t = dt_time(start_h, start_m, start_s)
            end_t = dt_time(end_h, end_m, end_s)

            if progress_callback:
                progress_callback(f"Filtering for times between {start_t} and {end_t}")

            # Filter by time-of-day
            mask = [
                (idx.time() >= start_t) and (idx.time() <= end_t) for idx in df.index
            ]
            df = df[mask]

        elif start_time:
            start_h, start_m, start_s = map(int, start_time.split(":"))
            from datetime import time as dt_time

            start_t = dt_time(start_h, start_m, start_s)
            mask = [idx.time() >= start_t for idx in df.index]
            df = df[mask]

        elif end_time:
            end_h, end_m, end_s = map(int, end_time.split(":"))
            from datetime import time as dt_time

            end_t = dt_time(end_h, end_m, end_s)
            mask = [idx.time() <= end_t for idx in df.index]
            df = df[mask]

        # Update timestamps and freqs from filtered dataframe
        if len(df) > 0:
            timestamps = df.index
            freqs = df.columns.values

        if progress_callback:
            progress_callback(f"Filtered from {original_len} to {len(df)} time samples")

    if len(df) == 0:
        if progress_callback:
            progress_callback("Error: No data in selected time range")
        return None

    # Create FITS file with time range info in filename
    base_name = os.path.basename(srs_file).replace(".srs", "")
    if start_time and end_time:
        time_suffix = f"_{start_time.replace(':', '')}-{end_time.replace(':', '')}"
    elif start_time:
        time_suffix = f"_{start_time.replace(':', '')}-end"
    elif end_time:
        time_suffix = f"_start-{end_time.replace(':', '')}"
    else:
        time_suffix = ""

    fits_file = os.path.join(
        output_dir, f"{base_name}{time_suffix}_dynamic_spectrum.fits"
    )
    result = dataframe_to_fits(
        df, freqs, timestamps, fits_file, site, progress_callback
    )

    # Clean up SRS file if conversion succeeded
    if result and os.path.exists(srs_file):
        if progress_callback:
            progress_callback(f"Cleaning up intermediate SRS file: {os.path.basename(srs_file)}")
        try:
            os.remove(srs_file)
        except Exception as e:
            if progress_callback:
                progress_callback(f"Warning: Could not delete SRS file: {e}")

    return result


def download_and_convert_learmonth(
    date: str,
    output_dir: str = ".",
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    bkg_sub: bool = False,
    do_flag: bool = True,
    flag_cal_time: bool = True,
    progress_callback=None,
) -> Optional[str]:
    """
    Download Learmonth data and convert to FITS in one step.

    Convenience wrapper around download_and_convert_rstn for backwards compatibility.
    """
    return download_and_convert_rstn(
        "Learmonth",
        date,
        output_dir,
        start_time,
        end_time,
        bkg_sub,
        do_flag,
        flag_cal_time,
        progress_callback,
    )


if __name__ == "__main__":
    # Test with a sample date
    import sys

    if len(sys.argv) > 1:
        date = sys.argv[1]
    else:
        date = "2024-01-15"

    def progress(msg):
        print(f"  {msg}")

    print(f"Downloading and converting Learmonth data for {date}...")
    result = download_and_convert_learmonth(
        date, output_dir="./learmonth_data", progress_callback=progress
    )
    if result:
        print(f"Success! FITS file: {result}")
    else:
        print("Failed to download/convert data")

# Solar Data Downloader

A Python tool for downloading and calibrating data from multiple solar observatories:

- SDO/AIA (Atmospheric Imaging Assembly)
- SDO/HMI (Helioseismic and Magnetic Imager)
- IRIS (Interface Region Imaging Spectrograph)
- SOHO (Solar and Heliospheric Observatory) instruments (EIT, LASCO, MDI)

## Requirements

- Python 3.6+
- SunPy 6.0+
- DRMS
- AstroPy
- For AIA Level 1.5 calibration: aiapy

You can install the required packages with:
```bash
pip install sunpy drms astropy
pip install aiapy  # Optional, for AIA Level 1.5 calibration
```

## Usage

### As a Command-Line Tool

```bash
python solar_data_downloader.py [options]
```

### As a Python Module

```python
import solar_data_downloader

# Download AIA data
aia_files = solar_data_downloader.download_aia(
    wavelength='171',
    cadence='12s',
    start_time='2022.06.25 04:14:14',
    end_time='2022.06.25 04:14:44',
    output_dir='./aia_data',
    email='your.email@example.com'  # Optional but recommended
)

# Download HMI data
hmi_files = solar_data_downloader.download_hmi(
    series='45s',
    start_time='2022.06.25 04:14:14',
    end_time='2022.06.25 04:14:44',
    output_dir='./hmi_data',
    email='your.email@example.com'  # Optional but recommended
)

# Download IRIS data
iris_files = solar_data_downloader.download_iris(
    start_time='2022.06.25 04:14:14',
    end_time='2022.06.25 04:14:44',
    output_dir='./iris_data',
    obs_type='SJI',
    wavelength=1400
)

# Download SOHO/EIT data
soho_files = solar_data_downloader.download_soho(
    instrument='EIT',
    start_time='2022.06.25 04:14:14',
    end_time='2022.06.25 04:14:44',
    output_dir='./soho_data',
    wavelength=195
)
```

## Command-Line Examples

### AIA Data

```bash
# Download AIA 171Å data using DRMS (with email)
python solar_data_downloader.py --instrument aia --wavelength 171 --cadence 12s \
  --start-time "2022.06.25 04:14:14" --end-time "2022.06.25 04:14:44" \
  --output-dir ./aia_data --email your.email@example.com

# Download AIA 171Å data using Fido (no email required)
python solar_data_downloader.py --instrument aia --wavelength 171 \
  --start-time "2022.06.25 04:14:14" --end-time "2022.06.25 04:14:44" \
  --output-dir ./aia_data --use-fido
```

### HMI Data

```bash
# Download HMI magnetogram data
python solar_data_downloader.py --instrument hmi --series B_45s \
  --start-time "2022.06.25 04:14:14" --end-time "2022.06.25 04:14:44" \
  --output-dir ./hmi_data --email your.email@example.com

# Download HMI continuum intensity data using Fido
python solar_data_downloader.py --instrument hmi --series Ic_720s \
  --start-time "2022.06.25 04:14:14" --end-time "2022.06.25 04:14:44" \
  --output-dir ./hmi_data --use-fido
```

### IRIS Data

```bash
# Download IRIS SJI data
python solar_data_downloader.py --instrument iris --obs-type SJI --wavelength 1400 \
  --start-time "2022.06.25 04:14:14" --end-time "2022.06.25 04:14:44" \
  --output-dir ./iris_data

# Download IRIS raster data
python solar_data_downloader.py --instrument iris --obs-type raster \
  --start-time "2022.06.25 04:14:14" --end-time "2022.06.25 04:14:44" \
  --output-dir ./iris_data
```

### SOHO Data

```bash
# Download SOHO/EIT data
python solar_data_downloader.py --instrument soho --soho-instrument EIT --wavelength 195 \
  --start-time "2022.01.25 04:14:14" --end-time "2022.01.25 04:44:44" \
  --output-dir ./soho_data

# Download SOHO/LASCO data
python solar_data_downloader.py --instrument soho --soho-instrument LASCO --detector C2 \
  --start-time "2022.01.25 04:14:14" --end-time "2022.01.25 04:44:44" \
  --output-dir ./soho_data
```

## Notes on Calibration

- **AIA**: Level 1.5 calibration requires the `aiapy` package
- **HMI**: Basic calibration is performed, but specialized tools may be needed for scientific analysis
- **IRIS**: Downloaded data is typically Level 2, which includes basic calibration
- **SOHO**: Basic calibration is performed for some instruments, but specialized tools may be needed

## Troubleshooting

- If you get "Bad record-set subset specification" errors, try using `--use-fido`
- For "email required" errors, either provide `--email` or use `--use-fido`
- If downloads fail, try a smaller time range between start and end times
- If using Fido, ensure you have the latest SunPy version installed
- IRIS and SOHO data are only available through the Fido client 
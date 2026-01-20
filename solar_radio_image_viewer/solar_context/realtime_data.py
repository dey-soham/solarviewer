#!/usr/bin/env python3
"""
Solar Conditions Data Fetcher - Fetches solar conditions for a specific date.

Data sources:
- Historical Kp Index: services.swpc.noaa.gov/text/daily-geomagnetic-indices.txt (30-day history)
- Historical F10.7 Flux: services.swpc.noaa.gov/text/daily-solar-indices.txt (30-day history)
- Current Solar Wind: services.swpc.noaa.gov/products/solar-wind/plasma-7-day.json (7-day only)

Note: For dates older than 30 days, data may not be available from SWPC.
"""

import re
import json
import urllib.request
import urllib.error
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List, Tuple


@dataclass
class SolarWindData:
    """Current solar wind conditions."""

    timestamp: datetime
    speed: float  # km/s
    density: float  # protons/cm³
    temperature: float  # Kelvin

    @property
    def speed_category(self) -> str:
        """Categorize solar wind speed."""
        if self.speed < 350:
            return "Slow"
        elif self.speed < 500:
            return "Normal"
        elif self.speed < 700:
            return "Elevated"
        else:
            return "High"


@dataclass
class KpIndexData:
    """Kp geomagnetic index data for a date."""

    event_date: date
    ap_value: int  # Daily Ap index
    kp_values: List[float]  # 8 Kp values for the day (one per 3-hour interval)

    @property
    def kp_max(self) -> float:
        """Get maximum Kp for the day."""
        return max(self.kp_values) if self.kp_values else 0

    @property
    def kp_avg(self) -> float:
        """Get average Kp for the day."""
        return sum(self.kp_values) / len(self.kp_values) if self.kp_values else 0

    @property
    def storm_level(self) -> str:
        """Get NOAA geomagnetic storm scale level based on max Kp."""
        kp = self.kp_max
        if kp >= 9:
            return "G5 (Extreme)"
        elif kp >= 8:
            return "G4 (Severe)"
        elif kp >= 7:
            return "G3 (Strong)"
        elif kp >= 6:
            return "G2 (Moderate)"
        elif kp >= 5:
            return "G1 (Minor)"
        else:
            return "Quiet"

    @property
    def color_code(self) -> str:
        """Get color code for Kp value."""
        kp = self.kp_max
        if kp >= 7:
            return "#F44336"  # Red
        elif kp >= 5:
            return "#FF9800"  # Orange
        elif kp >= 4:
            return "#FFC107"  # Amber
        elif kp >= 2:
            return "#4CAF50"  # Green
        else:
            return "#2196F3"  # Blue (very quiet)


@dataclass
class F107FluxData:
    """F10.7 cm radio flux data for a date."""

    event_date: date
    flux_value: float  # Solar Flux Units (sfu)
    sunspot_number: int  # SESC sunspot number
    sunspot_area: int  # 10E-6 Hemisphere
    xray_background: str  # Daily background X-ray flux (e.g., B1.5, *)

    @property
    def activity_level(self) -> str:
        """Categorize solar activity based on F10.7."""
        if self.flux_value < 70:
            return "Very Low"
        elif self.flux_value < 90:
            return "Low"
        elif self.flux_value < 120:
            return "Moderate"
        elif self.flux_value < 150:
            return "Elevated"
        elif self.flux_value < 200:
            return "High"
        else:
            return "Very High"


@dataclass
class SolarConditions:
    """Combined solar conditions for a specific date."""

    event_date: date
    kp_index: Optional[KpIndexData]
    f107_flux: Optional[F107FluxData]
    is_historical: bool  # True if data is from archive, False if current
    data_source: str  # Description of data source
    solar_wind: Optional[SolarWindData] = None  # Only for current date

    @property
    def summary(self) -> str:
        """Get a one-line summary of conditions."""
        parts = []

        if self.kp_index:
            parts.append(
                f"Kp max: {self.kp_index.kp_max:.0f} ({self.kp_index.storm_level})"
            )

        if self.solar_wind:
            parts.append(f"Wind: {self.solar_wind.speed:.0f} km/s")

        if self.f107_flux:
            parts.append(
                f"F10.7: {self.f107_flux.flux_value:.0f} sfu ({self.f107_flux.activity_level})"
            )

        return " | ".join(parts) if parts else "No data available"


def fetch_json(url: str, timeout: int = 30) -> Optional[Any]:
    """Fetch and parse JSON from URL."""
    try:
        from ..utils import get_global_session

        session = get_global_session()
        response = session.get(url)
        return json.loads(response.text)
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None


def fetch_solar_wind(target_date: Optional[date] = None) -> Optional[SolarWindData]:
    """
    Fetch solar wind conditions from SWPC 7-day history.

    Args:
        target_date: If provided, get the latest data for this specific date.
                     If None, get the absolute latest data available.
    """
    url = "https://services.swpc.noaa.gov/products/solar-wind/plasma-7-day.json"
    data = fetch_json(url)

    if not data or len(data) < 2:
        return None

    # Data format: [["time_tag", "density", "speed", "temperature"], [...], ...]
    # Iterate backwards to find latest valid entry for the target date
    for entry in reversed(data[1:]):
        try:
            timestamp_str = entry[0]
            timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S.%f")

            # If target_date is specified, verify match
            if target_date and timestamp.date() != target_date:
                # Optimized: Since data is chronological, if we see a date NEWER than target, skip.
                # If we see a date OLDER, we missed our window (data not present).
                if timestamp.date() > target_date:
                    continue
                else:
                    # Found data older than target, so target date has no data in this file
                    return None

            # Found potential match (or no target date), check values
            density = float(entry[1]) if entry[1] else None
            speed = float(entry[2]) if entry[2] else None
            temperature = float(entry[3]) if entry[3] else None

            if speed is None or density is None:
                continue

            return SolarWindData(
                timestamp=timestamp,
                speed=speed,
                density=density,
                temperature=temperature or 0,
            )
        except (ValueError, IndexError):
            continue
    return None


def fetch_text(url: str, timeout: int = 30) -> Optional[str]:
    """Fetch text content from URL."""
    try:
        from ..utils import get_global_session

        session = get_global_session()
        response = session.get(url)
        return response.text
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None


def parse_daily_geomagnetic_indices(text: str) -> Dict[date, KpIndexData]:
    """
    Parse SWPC daily-geomagnetic-indices.txt file.

    Returns dict mapping dates to KpIndexData.
    """
    results = {}

    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(":"):
            continue

        # Format: YYYY MM DD  A  K K K K K K K K  A  K K K K K K K K  A  Kp Kp Kp Kp Kp Kp Kp Kp
        # We want the planetary (Estimated) Kp values at the end
        try:
            parts = line.split()
            if len(parts) < 30:
                continue

            year = int(parts[0])
            month = int(parts[1])
            day = int(parts[2])
            event_date = date(year, month, day)

            # Get planetary A value (index 21 approximately, may vary)
            # Actually the format shows 3 sets of A + 8 K values
            # Fredericksburg (10), College (10), Planetary (10)
            # So planetary Kp starts at index 22

            # Parse planetary Kp values (last 8 values)
            kp_values = []
            for i in range(-8, 0):
                try:
                    kp = float(parts[i])
                    kp_values.append(kp)
                except ValueError:
                    kp_values.append(0.0)

            # Get planetary A index (before the 8 Kp values)
            try:
                ap_value = int(parts[-9])
            except (ValueError, IndexError):
                ap_value = 0

            results[event_date] = KpIndexData(
                event_date=event_date,
                ap_value=ap_value,
                kp_values=kp_values,
            )
        except (ValueError, IndexError) as e:
            continue

    return results


def parse_daily_solar_indices(text: str) -> Dict[date, F107FluxData]:
    """
    Parse SWPC daily-solar-indices.txt file.

    Returns dict mapping dates to F107FluxData.
    """
    results = {}

    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(":"):
            continue

        # Format: YYYY MM DD  F10.7  SSN  Area  NewReg  Field  Bkgd  C M X  S 1 2 3
        try:
            parts = line.split()
            if len(parts) < 5:
                continue

            year = int(parts[0])
            month = int(parts[1])
            day = int(parts[2])
            event_date = date(year, month, day)

            flux = float(parts[3])
            sunspot_number = int(parts[4])
            sunspot_area = int(parts[5])
            xray_background = parts[8]

            results[event_date] = F107FluxData(
                event_date=event_date,
                flux_value=flux,
                sunspot_number=sunspot_number,
                sunspot_area=sunspot_area,
                xray_background=xray_background,
            )
        except (ValueError, IndexError) as e:
            continue

    return results


def fetch_conditions_for_date(event_date: date) -> SolarConditions:
    """
    Fetch comprehensive solar conditions for a specific date.

    Strategy:
    1. Geomagnetic & Solar Indices:
       - < 35 days: Daily text files
       - > 35 days: FTP Archive
    2. Solar Wind (Speed/Density):
       - < 7 days: 7-day JSON history
       - > 7 days: Not available (set to None)
    """
    kp_data = None
    f107_data = None
    solar_wind = None
    data_source = "NOAA SWPC daily indices (30-day archive)"

    today = date.today()
    days_ago = (today - event_date).days

    # 1. Fetch Solar Wind (if within 7 days)
    if 0 <= days_ago <= 7:
        try:
            solar_wind = fetch_solar_wind(event_date)
        except Exception as e:
            print(f"Solar wind fetch failed: {e}")

    # 2. Fetch Kp/F10.7 Indices
    # Fetch from daily files (last 30 days)
    if 0 <= days_ago <= 35:
        # Fetch geomagnetic indices (Kp)
        geo_text = fetch_text(
            "https://services.swpc.noaa.gov/text/daily-geomagnetic-indices.txt"
        )
        if geo_text:
            kp_dict = parse_daily_geomagnetic_indices(geo_text)
            kp_data = kp_dict.get(event_date)

        # Fetch solar indices (F10.7)
        solar_text = fetch_text(
            "https://services.swpc.noaa.gov/text/daily-solar-indices.txt"
        )
        if solar_text:
            f107_dict = parse_daily_solar_indices(solar_text)
            f107_data = f107_dict.get(event_date)

        return SolarConditions(
            event_date=event_date,
            kp_index=kp_data,
            f107_flux=f107_data,
            is_historical=days_ago > 0,
            data_source=data_source,
            solar_wind=solar_wind,
        )

    # For older data, try FTP archives
    if days_ago > 35:
        try:
            kp_data, f107_data = fetch_historical_from_ftp(event_date)
            if kp_data or f107_data:
                return SolarConditions(
                    event_date=event_date,
                    kp_index=kp_data,
                    f107_flux=f107_data,
                    is_historical=True,
                    data_source="NOAA SWPC FTP Archive",
                )
        except Exception as e:
            print(f"FTP fetch error: {e}")

    # Fallback / No data found
    return SolarConditions(
        event_date=event_date,
        kp_index=None,
        f107_flux=None,
        is_historical=True,
        data_source="Data not available",
    )


# Cache for FTP data to avoid repeated connections
_ftp_cache = {}


def fetch_historical_from_ftp(
    event_date: date,
) -> Tuple[Optional[KpIndexData], Optional[F107FluxData]]:
    """
    Fetch historical data from SWPC FTP archives.

    Files are typically:
    - YYYYQx_DGD.txt (Geomagnetic)
    - YYYYQx_DSD.txt (Solar/F10.7)

    Legacy might be YYYY_DGD.txt
    """
    from ftplib import FTP
    import io

    year = event_date.year
    quarter = (event_date.month - 1) // 3 + 1

    # Try quarterly format first, then yearly
    prefixes = [f"{year}Q{quarter}", f"{year}"]

    kp_data = None
    f107_data = None

    # Check cache first
    cache_key_kp = f"{year}_Q{quarter}_Kp"
    cache_key_solar = f"{year}_Q{quarter}_Solar"

    if cache_key_kp in _ftp_cache:
        kp_dict = _ftp_cache[cache_key_kp]
        kp_data = kp_dict.get(event_date)
    else:
        # Need to fetch Kp
        pass  # Will fetch below

    if cache_key_solar in _ftp_cache:
        f107_dict = _ftp_cache[cache_key_solar]
        f107_data = f107_dict.get(event_date)

    # If we have both from cache, return
    if kp_data and f107_data:
        return kp_data, f107_data

    # If missing, fetch from FTP
    try:
        ftp = FTP("ftp.swpc.noaa.gov")
        ftp.login()
        ftp.cwd("pub/indices/old_indices")

        # Fetch Geomagnetic Data (DGD) if needed
        if cache_key_kp not in _ftp_cache:
            dgd_content = None
            for prefix in prefixes:
                filename = f"{prefix}_DGD.txt"
                try:
                    # Download file
                    bio = io.BytesIO()
                    ftp.retrbinary(f"RETR {filename}", bio.write)
                    dgd_content = bio.getvalue().decode("utf-8", errors="ignore")
                    break  # Success
                except Exception:
                    continue

            if dgd_content:
                kp_dict = parse_daily_geomagnetic_indices(dgd_content)
                _ftp_cache[cache_key_kp] = kp_dict
                kp_data = kp_dict.get(event_date)

        # Fetch Solar Data (DSD) if needed
        if cache_key_solar not in _ftp_cache:
            dsd_content = None
            for prefix in prefixes:
                filename = f"{prefix}_DSD.txt"
                try:
                    # Download file
                    bio = io.BytesIO()
                    ftp.retrbinary(f"RETR {filename}", bio.write)
                    dsd_content = bio.getvalue().decode("utf-8", errors="ignore")
                    break  # Success
                except Exception:
                    continue

            if dsd_content:
                f107_dict = parse_daily_solar_indices(dsd_content)
                _ftp_cache[cache_key_solar] = f107_dict
                f107_data = f107_dict.get(event_date)

        ftp.quit()

    except Exception as e:
        print(f"FTP connection failed: {e}")
        return None, None

    return kp_data, f107_data


# Legacy function for backwards compatibility
def fetch_current_conditions() -> SolarConditions:
    """
    Fetch current solar conditions (today's data).

    Returns:
        SolarConditions object with today's data
    """
    return fetch_conditions_for_date(date.today())


if __name__ == "__main__":
    from datetime import date, timedelta

    # Test with a recent date
    test_date = date.today() - timedelta(days=3)
    print(f"Fetching solar conditions for {test_date}...")

    conditions = fetch_conditions_for_date(test_date)

    print(f"\nDate: {conditions.event_date}")
    print(f"Data Source: {conditions.data_source}")
    print(f"Summary: {conditions.summary}")

    if conditions.kp_index:
        kp = conditions.kp_index
        print(f"\nKp Index:")
        print(f"  Ap: {kp.ap_value}")
        print(f"  Kp max: {kp.kp_max:.1f}")
        print(f"  Kp values: {kp.kp_values}")
        print(f"  Storm Level: {kp.storm_level}")

    if conditions.f107_flux:
        f107 = conditions.f107_flux
        print(f"\nF10.7 Radio Flux:")
        print(f"  Value: {f107.flux_value:.1f} sfu")
        print(f"  Sunspot #: {f107.sunspot_number}")
        print(f"  Activity: {f107.activity_level}")

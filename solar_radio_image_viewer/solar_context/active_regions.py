#!/usr/bin/env python3
"""
Active Regions Parser - Fetches and parses NOAA active region data from solarmonitor.org.

Data sources:
- SRS file: Solar Region Summary (AR#, Location, Area, McIntosh class, Mag Type)
- arm_forecast: Flare probability predictions (C/M/X class percentages)
- arm_ar_summary: Recent flare activity per region
"""

import re
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import List, Optional, Dict, Any


@dataclass
class ActiveRegion:
    """Represents a NOAA Active Region."""

    noaa_number: str
    location: str  # e.g., "S17W72"
    longitude: int  # Carrington longitude
    area: int  # Millionths of solar hemisphere
    mcintosh_class: str  # e.g., "Ekc", "Dao"
    num_spots: int
    spot_count: int  # NN field
    mag_type: str  # Magnetic classification (Alpha, Beta, Beta-Gamma, etc.)

    # Flare probabilities (optional, from arm_forecast)
    prob_c: Optional[int] = None  # C-class probability %
    prob_m: Optional[int] = None  # M-class probability %
    prob_x: Optional[int] = None  # X-class probability %

    # Recent flares (optional, from arm_ar_summary)
    recent_flares: List[str] = field(default_factory=list)

    @property
    def location_formatted(self) -> str:
        """Format location with hemisphere indicators."""
        return self.location

    @property
    def is_complex(self) -> bool:
        """Check if region has complex magnetic configuration."""
        return "Gamma" in self.mag_type or "Delta" in self.mag_type

    @property
    def flare_risk_level(self) -> str:
        """Assess overall flare risk based on probabilities."""
        if self.prob_x and self.prob_x >= 10:
            return "Very High"
        if self.prob_m and self.prob_m >= 50:
            return "High"
        if self.prob_m and self.prob_m >= 20:
            return "Moderate"
        if self.prob_c and self.prob_c >= 50:
            return "Low"
        return "Quiet"

    @property
    def mcintosh_description(self) -> str:
        """Get human-readable McIntosh class description."""
        if len(self.mcintosh_class) < 3:
            return "Unknown"

        # Modified Zurich class (first letter)
        zurich = {
            "A": "Unipolar",
            "B": "Bipolar, no penumbra",
            "C": "Bipolar, penumbra one end",
            "D": "Bipolar, penumbra both ends",
            "E": "Large bipolar",
            "F": "Very large bipolar",
            "H": "Unipolar with penumbra",
        }

        # Penumbra of largest spot (second letter)
        penumbra = {
            "x": "undefined",
            "r": "rudimentary",
            "s": "small symmetric",
            "a": "small asymmetric",
            "h": "large symmetric",
            "k": "large asymmetric",
        }

        # Spot distribution (third letter)
        dist = {
            "x": "undefined",
            "o": "open",
            "i": "intermediate",
            "c": "compact",
        }

        z = self.mcintosh_class[0].upper()
        p = self.mcintosh_class[1].lower()
        d = self.mcintosh_class[2].lower()

        z_desc = zurich.get(z, z)
        p_desc = penumbra.get(p, p)
        d_desc = dist.get(d, d)

        return f"{z_desc}, {p_desc} penumbra, {d_desc} distribution"


def fetch_srs_data(event_date: date) -> Optional[str]:
    """
    Fetch Solar Region Summary data from solarmonitor.org.

    Args:
        event_date: The date to fetch data for

    Returns:
        Raw text content or None if fetch failed
    """
    year = event_date.strftime("%Y")
    month = event_date.strftime("%m")
    day = event_date.strftime("%d")

    # SRS filename format: MMDDSRS.txt (e.g., 0609SRS.txt)
    srs_filename = f"{month}{day}SRS.txt"

    url = f"https://solarmonitor.org/data/{year}/{month}/{day}/meta/{srs_filename}"

    try:
        from ..utils import get_global_session

        session = get_global_session()
        response = session.get(url)
        return response.text
    except Exception as e:
        if hasattr(e, "response") and e.response is not None:
            if e.response.status_code == 404:
                return None
        print(f"Error fetching SRS data: {e}")
        return None


def fetch_forecast_data(event_date: date) -> Optional[str]:
    """
    Fetch ARM flare forecast data from solarmonitor.org.

    Args:
        event_date: The date to fetch data for

    Returns:
        Raw text content or None if fetch failed
    """
    year = event_date.strftime("%Y")
    month = event_date.strftime("%m")
    day = event_date.strftime("%d")
    date_str = event_date.strftime("%Y%m%d")

    url = f"https://solarmonitor.org/data/{year}/{month}/{day}/meta/arm_forecast_{date_str}.txt"

    try:
        from ..utils import get_global_session

        session = get_global_session()
        response = session.get(url)
        return response.text
    except Exception as e:
        print(f"Error fetching ARM forecast: {e}")
        return None


def parse_srs_data(raw_text: str) -> List[ActiveRegion]:
    """
    Parse Solar Region Summary text into ActiveRegion objects.

    Args:
        raw_text: Raw SRS text file content

    Returns:
        List of ActiveRegion objects
    """
    regions = []
    in_sunspot_section = False

    for line in raw_text.split("\n"):
        line = line.strip()

        # Detect section start
        if "Regions with Sunspots" in line:
            in_sunspot_section = True
            continue

        # Detect section end
        if line.startswith("IA.") or line.startswith("II."):
            in_sunspot_section = False
            continue

        if not in_sunspot_section:
            continue

        # Skip header line
        if line.startswith("Nmbr") or not line:
            continue

        # Parse region line
        # Format: Nmbr Location  Lo  Area  Z   LL   NN Mag Type
        # Example: 3697 S17W72   349  0360 Ekc  15   19 Beta-Gamma-Delta

        try:
            # Use regex for flexible parsing
            pattern = r"(\d{4})\s+([NS]\d{2}[EW]\d{2})\s+(\d+)\s+(\d+)\s+(\w{3})\s+(\d+)\s+(\d+)\s+(.*)"
            match = re.match(pattern, line)

            if match:
                noaa_num = match.group(1)
                location = match.group(2)
                longitude = int(match.group(3))
                area = int(match.group(4))
                mcintosh = match.group(5)
                ll = int(match.group(6))
                nn = int(match.group(7))
                mag_type = match.group(8).strip()

                regions.append(
                    ActiveRegion(
                        noaa_number=noaa_num,
                        location=location,
                        longitude=longitude,
                        area=area,
                        mcintosh_class=mcintosh,
                        num_spots=ll,
                        spot_count=nn,
                        mag_type=mag_type,
                    )
                )
        except Exception:
            continue

    return regions


def parse_forecast_data(raw_text: str) -> Dict[str, Dict[str, int]]:
    """
    Parse ARM forecast data to get flare probabilities.

    Format: AR# McIntosh C%(method1)(method2)(method3) M%(...) X%(...)
    Example: 13697 Ekc 89(93)(90) 77(82)(60) 15(20)(20)

    Returns:
        Dict mapping AR# to {'c': %, 'm': %, 'x': %}
    """
    forecasts = {}

    for line in raw_text.split("\n"):
        line = line.strip()
        if not line:
            continue

        try:
            # Pattern: AR# McIntosh C(...)(...) M(...)(...) X(...)
            parts = line.split()
            if len(parts) >= 5:
                ar_num = parts[0]

                # Extract first number from each probability field
                # Format like: 89(93)(90)
                c_prob = int(parts[2].split("(")[0])
                m_prob = int(parts[3].split("(")[0])
                x_prob = int(parts[4].split("(")[0])

                forecasts[ar_num] = {
                    "c": c_prob,
                    "m": m_prob,
                    "x": x_prob,
                }
        except (ValueError, IndexError):
            continue

    return forecasts


def fetch_and_parse_active_regions(event_date: date) -> Optional[List[ActiveRegion]]:
    """
    Fetch and parse all active region data for a date.

    Args:
        event_date: The date to fetch data for

    Returns:
        List of ActiveRegion objects with probabilities, or None if fetch failed
    """
    # Fetch SRS data
    srs_text = fetch_srs_data(event_date)
    if srs_text is None:
        return None

    # Parse regions
    regions = parse_srs_data(srs_text)
    if not regions:
        return []

    # Fetch and merge forecast data
    forecast_text = fetch_forecast_data(event_date)
    if forecast_text:
        forecasts = parse_forecast_data(forecast_text)

        for region in regions:
            # Try different AR number formats (SRS uses 4-digit, forecast often uses 5-digit with "1" prefix)
            ar_keys_to_try = [
                region.noaa_number,
                "1" + region.noaa_number,  # 3697 -> 13697
                (
                    region.noaa_number.lstrip("1")
                    if region.noaa_number.startswith("1")
                    else None
                ),  # 13697 -> 3697
            ]

            for ar_key in ar_keys_to_try:
                if ar_key and ar_key in forecasts:
                    probs = forecasts[ar_key]
                    region.prob_c = probs.get("c")
                    region.prob_m = probs.get("m")
                    region.prob_x = probs.get("x")
                    break

    return regions


def get_ar_statistics(regions: List[ActiveRegion]) -> Dict[str, Any]:
    """
    Calculate statistics for a list of active regions.

    Returns:
        Dict with statistics like counts, max complexity, etc.
    """
    stats = {
        "total": len(regions),
        "complex_count": sum(1 for r in regions if r.is_complex),
        "total_area": sum(r.area for r in regions),
        "max_area_region": None,
        "highest_risk_region": None,
    }

    if regions:
        stats["max_area_region"] = max(regions, key=lambda r: r.area)

        # Find highest risk region
        risk_order = {"Very High": 4, "High": 3, "Moderate": 2, "Low": 1, "Quiet": 0}
        stats["highest_risk_region"] = max(
            regions, key=lambda r: risk_order.get(r.flare_risk_level, 0)
        )

    return stats


if __name__ == "__main__":
    # Test with sample date
    from datetime import date

    test_date = date(2024, 6, 9)
    print(f"Fetching active regions for {test_date}...")

    regions = fetch_and_parse_active_regions(test_date)
    if regions:
        print(f"Found {len(regions)} active regions:")
        for r in regions:
            prob_str = ""
            if r.prob_c is not None:
                prob_str = f" | C:{r.prob_c}% M:{r.prob_m}% X:{r.prob_x}%"
            print(
                f"  AR{r.noaa_number} | {r.location} | {r.mcintosh_class} | {r.mag_type}{prob_str}"
            )

        stats = get_ar_statistics(regions)
        print(f"\nStatistics:")
        print(f"  Total area: {stats['total_area']} millionths")
        print(f"  Complex regions: {stats['complex_count']}")
        if stats["highest_risk_region"]:
            hr = stats["highest_risk_region"]
            print(f"  Highest risk: AR{hr.noaa_number} ({hr.flare_risk_level})")
    else:
        print("No active regions found or fetch failed")

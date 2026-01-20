#!/usr/bin/env python3
"""
CME Alerts Parser - Fetches Coronal Mass Ejection data from NASA DONKI API.

Data source:
- NASA DONKI (Space Weather Database of Notifications, Knowledge, Information)
- Endpoint: kauai.ccmc.gsfc.nasa.gov/DONKI/WS/get/CME
"""

import json
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any


@dataclass
class CMEEvent:
    """Represents a Coronal Mass Ejection event."""

    activity_id: str
    start_time: datetime
    source_location: str  # e.g., "S17W72"
    active_region: Optional[str]
    speed: float  # km/s
    half_angle: float  # degrees (angular width)
    latitude: float
    longitude: float
    is_earth_directed: bool
    earth_arrival_time: Optional[datetime]
    note: str
    analysis_link: str

    @property
    def speed_category(self) -> str:
        """Categorize CME speed."""
        if self.speed < 500:
            return "Slow"
        elif self.speed < 1000:
            return "Moderate"
        elif self.speed < 2000:
            return "Fast"
        else:
            return "Extreme"

    @property
    def color_code(self) -> str:
        """Get color based on speed and Earth-directed status."""
        if self.is_earth_directed:
            if self.speed >= 1500:
                return "#F44336"  # Red - fast Earth-directed
            elif self.speed >= 1000:
                return "#FF9800"  # Orange
            else:
                return "#FFC107"  # Amber
        else:
            return "#4CAF50"  # Green - not Earth-directed

    @property
    def start_time_str(self) -> str:
        """Format start time."""
        return self.start_time.strftime("%Y-%m-%d %H:%M")

    @property
    def arrival_str(self) -> str:
        """Format Earth arrival time."""
        if self.earth_arrival_time:
            return self.earth_arrival_time.strftime("%Y-%m-%d %H:%M")
        return "—"


def fetch_cme_data(event_date: date, days_range: int = 3) -> Optional[List[Dict]]:
    """
    Fetch CME data from NASA DONKI API.

    Args:
        event_date: Center date to search around
        days_range: Number of days before and after to search

    Returns:
        Raw JSON data or None if fetch failed
    """
    start_date = (event_date - timedelta(days=days_range)).strftime("%Y-%m-%d")
    end_date = (event_date + timedelta(days=days_range)).strftime("%Y-%m-%d")

    url = f"https://kauai.ccmc.gsfc.nasa.gov/DONKI/WS/get/CME?startDate={start_date}&endDate={end_date}"

    try:
        from ..utils import get_global_session

        session = get_global_session()
        response = session.get(url)
        return json.loads(response.text)
    except Exception as e:
        if hasattr(e, "response") and e.response is not None:
            if e.response.status_code == 404:
                return []
        print(f"CME fetch error: {e}")
        return None


def parse_cme_events(raw_data: List[Dict]) -> List[CMEEvent]:
    """
    Parse CME JSON data into CMEEvent objects.

    Args:
        raw_data: Raw JSON from DONKI API

    Returns:
        List of CMEEvent objects
    """
    events = []

    for cme in raw_data:
        try:
            activity_id = cme.get("activityID", "Unknown")
            start_time_str = (
                activity_id.split("-CME-")[0] if "-CME-" in activity_id else None
            )

            if start_time_str:
                try:
                    start_time = datetime.fromisoformat(start_time_str)
                except ValueError:
                    start_time = datetime.now()
            else:
                start_time = datetime.now()

            # Get the most accurate analysis
            analyses = cme.get("cmeAnalyses") or []
            best_analysis = None
            for analysis in analyses:
                if analysis.get("isMostAccurate"):
                    best_analysis = analysis
                    break
            if not best_analysis and analyses:
                best_analysis = analyses[0]

            if not best_analysis:
                continue

            speed = best_analysis.get("speed", 0) or 0
            half_angle = best_analysis.get("halfAngle", 0) or 0
            latitude = best_analysis.get("latitude", 0) or 0
            longitude = best_analysis.get("longitude", 0) or 0
            note = best_analysis.get("note", "") or ""
            analysis_link = best_analysis.get("link", "")

            # Check for Earth impact
            is_earth_directed = False
            earth_arrival_time = None

            enlil_list = best_analysis.get("enlilList") or []
            for enlil in enlil_list:
                if enlil.get("isEarthGB") or enlil.get("isEarthMinorImpact"):
                    is_earth_directed = True
                impact_list = enlil.get("impactList") or []
                for impact in impact_list:
                    if "Earth" in impact.get("location", ""):
                        is_earth_directed = True
                        arrival_str = impact.get("arrivalTime")
                        if arrival_str:
                            try:
                                earth_arrival_time = datetime.fromisoformat(
                                    arrival_str.replace("Z", "")
                                )
                            except ValueError:
                                pass

            # Determine if likely Earth-directed based on longitude
            if abs(longitude) < 45:  # CME centered within ±45° of Sun-Earth line
                is_earth_directed = True

            # Format source location
            lat_hem = "N" if latitude >= 0 else "S"
            lon_hem = "E" if longitude <= 0 else "W"
            source_location = (
                f"{lat_hem}{abs(int(latitude))}{lon_hem}{abs(int(longitude))}"
            )

            active_region = cme.get("activeRegionNum")
            if active_region:
                active_region = str(active_region)

            events.append(
                CMEEvent(
                    activity_id=activity_id,
                    start_time=start_time,
                    source_location=source_location,
                    active_region=active_region,
                    speed=speed,
                    half_angle=half_angle,
                    latitude=latitude,
                    longitude=longitude,
                    is_earth_directed=is_earth_directed,
                    earth_arrival_time=earth_arrival_time,
                    note=note[:200] + "..." if len(note) > 200 else note,
                    analysis_link=analysis_link,
                )
            )
        except Exception as e:
            print(f"Error parsing CME: {e}")
            continue

    # Sort by start time
    events.sort(key=lambda e: e.start_time, reverse=True)
    return events


def fetch_and_parse_cme_events(
    event_date: date, days_range: int = 3
) -> Optional[List[CMEEvent]]:
    """
    Fetch and parse CME events for a date range.

    Args:
        event_date: Center date to search around
        days_range: Number of days before and after to search

    Returns:
        List of CMEEvent objects or None if fetch failed
    """
    raw_data = fetch_cme_data(event_date, days_range)
    if raw_data is None:
        return None
    if not raw_data:
        return []
    return parse_cme_events(raw_data)


if __name__ == "__main__":
    # Test with sample date
    from datetime import date

    test_date = date(2024, 6, 9)
    print(f"Fetching CME events around {test_date}...")

    events = fetch_and_parse_cme_events(test_date)
    if events:
        print(f"Found {len(events)} CME events:")
        for e in events[:5]:
            earth_str = "🌍 Earth-directed" if e.is_earth_directed else ""
            print(
                f"  {e.start_time_str} | {e.speed:.0f} km/s ({e.speed_category}) | {e.source_location} {earth_str}"
            )
    else:
        print("No CME events found or fetch failed")

#!/usr/bin/env python3
"""
CME Alerts Parser - Fetches Coronal Mass Ejection data from NASA DONKI API.

Data source:
- NASA DONKI (Space Weather Database of Notifications, Knowledge, Information)
- Endpoint: kauai.ccmc.gsfc.nasa.gov/DONKI/WS/get/CME
- CDAW LASCO CME catalogue, when DONKI cannot be reached
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict

CDAW_URL = (
    "https://cdaw.gsfc.nasa.gov/CME_list/UNIVERSAL_ver2/"
    "{year}_{month:02d}/univ{year}_{month:02d}.html"
)


@dataclass
class CMEEvent:
    """Represents a Coronal Mass Ejection event."""

    activity_id: str
    start_time: datetime
    source_location: str  # e.g., "S17W72"
    active_region: Optional[str]
    speed: float  # km/s
    half_angle: float  # degrees (half the angular width)
    latitude: Optional[float]
    longitude: Optional[float]
    is_earth_directed: Optional[bool]  # None: not known
    earth_arrival_time: Optional[datetime]
    note: str
    analysis_link: str
    source: str = "DONKI"  # or "CDAW", which gives no position

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
    def tone(self) -> str:
        """Get the tone for the speed and Earth-directed status."""
        if self.is_earth_directed:
            return "error" if self.speed >= 1500 else "warning"
        return "success"

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
            latitude = best_analysis.get("latitude")
            longitude = best_analysis.get("longitude")
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
            # (the analysis may give no position)
            if longitude is not None and abs(longitude) < 45:  # CME centered within ±45° of Sun-Earth line
                is_earth_directed = True

            # Format source location
            if latitude is None or longitude is None:
                source_location = "—"
            else:
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


def parse_cdaw_page(html: str, page_url: str = "") -> List[CMEEvent]:
    """The CMEs of one month's page of the CDAW LASCO catalogue. It measures
    them in the plane of the sky, so it gives no source position and cannot
    say whether one is Earth-directed."""
    events = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I):
        cells = [
            re.sub(r"<[^>]+>", "", cell).strip()
            for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)
        ]
        if len(cells) < 5:
            continue
        try:
            start_time = datetime.strptime(f"{cells[0]} {cells[1]}", "%Y/%m/%d %H:%M:%S")
        except ValueError:
            continue  # not a row of the table
        # A width can read ">206" (a lower limit), and "----" means not measured
        width = re.search(r"\d+", cells[3])
        speed = re.search(r"\d+", cells[4])
        halo = "halo" in cells[2].lower()
        events.append(
            CMEEvent(
                activity_id=f"{start_time.isoformat()}-CME-CDAW",
                start_time=start_time,
                source_location="—",
                active_region=None,
                speed=float(speed.group()) if speed else 0,
                half_angle=float(width.group()) / 2 if width else 0,
                latitude=None,
                longitude=None,
                is_earth_directed=None,
                earth_arrival_time=None,
                note="Halo CME" if halo else "",
                analysis_link=page_url,
                source="CDAW",
            )
        )
    return events


def fetch_cdaw_cmes(event_date: date, days_range: int = 3) -> Optional[List[CMEEvent]]:
    """The CMEs around ``event_date`` from the CDAW catalogue, newest first,
    or None if none of its pages could be had. A page appears some months
    after the fact."""
    from ..utils import get_global_session

    first = event_date - timedelta(days=days_range)
    last = event_date + timedelta(days=days_range)
    months = {(day.year, day.month) for day in (first, last)}
    session = get_global_session()
    events = None
    for year, month in sorted(months):
        url = CDAW_URL.format(year=year, month=month)
        try:
            response = session.get(url)
            response.raise_for_status()
        except Exception as e:
            print(f"CDAW CME catalogue fetch error: {e}")
            continue
        events = (events or []) + parse_cdaw_page(response.text, url)
    if events is None:
        return None
    events = [e for e in events if first <= e.start_time.date() <= last]
    events.sort(key=lambda e: e.start_time, reverse=True)
    return events


def fetch_and_parse_cme_events(
    event_date: date, days_range: int = 3
) -> Optional[List[CMEEvent]]:
    """
    Fetch and parse CME events for a date range: from DONKI, or the CDAW
    catalogue when DONKI cannot be reached.

    Args:
        event_date: Center date to search around
        days_range: Number of days before and after to search

    Returns:
        List of CMEEvent objects or None if fetch failed
    """
    raw_data = fetch_cme_data(event_date, days_range)
    if raw_data is None:
        return fetch_cdaw_cmes(event_date, days_range)
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
            earth_str = "Earth-directed" if e.is_earth_directed else ""
            print(
                f"  {e.start_time_str} | {e.speed:.0f} km/s ({e.speed_category}) | {e.source_location} {earth_str}"
            )
    else:
        print("No CME events found or fetch failed")

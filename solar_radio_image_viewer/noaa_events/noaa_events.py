#!/usr/bin/env python3
"""
NOAA Solar Events Parser - Core module for fetching and parsing NOAA solar events.

Data source: https://solarmonitor.org/data/{YYYY}/{MM}/{DD}/meta/noaa_events_raw_{YYYYMMDD}.txt
"""

import re
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any


# Event type definitions with descriptions and icons
EVENT_TYPES = {
    "XRA": {
        "name": "X-ray Flare",
        "description": "Solar X-ray event observed by GOES spacecraft (1-8 Ångström)",
        "icon": "☀️",
        "category": "xray",
    },
    "FLA": {
        "name": "Optical Flare",
        "description": "Optical flare observed in H-alpha wavelength",
        "icon": "🔥",
        "category": "optical",
    },
    "RSP": {
        "name": "Sweep Radio Burst",
        "description": "Sweep-frequency radio burst (Type II/III/IV/V)",
        "icon": "📻",
        "category": "radio",
    },
    "RBR": {
        "name": "Fixed-freq Radio Burst",
        "description": "Fixed-frequency radio burst (245, 410, 610 MHz, etc.)",
        "icon": "📡",
        "category": "radio",
    },
    "RNS": {
        "name": "Radio Noise Storm",
        "description": "Prolonged enhanced radio emission from sunspot groups",
        "icon": "🌊",
        "category": "radio",
    },
    "CME": {
        "name": "Coronal Mass Ejection",
        "description": "Massive plasma/magnetic field ejection from corona",
        "icon": "💥",
        "category": "cme",
    },
}

# X-ray flare class colors (for UI)
FLARE_CLASS_COLORS = {
    "A": "#808080",  # Gray - minor
    "B": "#4CAF50",  # Green - weak
    "C": "#FFC107",  # Yellow/Amber - small
    "M": "#FF9800",  # Orange - moderate
    "X": "#F44336",  # Red - major
}

# Observatory codes
OBSERVATORY_CODES = {
    "G16": "GOES-16",
    "G18": "GOES-18",
    "G17": "GOES-17",
    "G15": "GOES-15",
    "LEA": "Learmonth",
    "SVI": "San Vito",
    "PAL": "Palehua",
    "HOL": "Holloman",
    "SAG": "Sagamore Hill",
    "RAM": "Ramey",
    "CUL": "Culgoora",
}


@dataclass
class SolarEvent:
    """Represents a single NOAA solar event."""

    event_id: str
    is_followup: bool  # Has '+' marker
    begin_time: Optional[str]  # HHMM format or None
    max_time: Optional[str]  # HHMM format or None (can be "////")
    end_time: Optional[str]  # HHMM format or None
    observatory: str
    quality: str
    event_type: str  # XRA, FLA, RSP, RBR, RNS, CME
    location_or_freq: str
    particulars: str
    active_region: Optional[str]
    raw_line: str = ""

    @property
    def type_info(self) -> Dict[str, Any]:
        """Get event type metadata."""
        return EVENT_TYPES.get(
            self.event_type,
            {
                "name": self.event_type,
                "description": "Unknown event type",
                "icon": "❓",
                "category": "other",
            },
        )

    @property
    def observatory_name(self) -> str:
        """Get full observatory name."""
        return OBSERVATORY_CODES.get(self.observatory, self.observatory)

    @property
    def begin_time_formatted(self) -> str:
        """Format begin time as HH:MM."""
        if not self.begin_time or self.begin_time == "////":
            return "—"
        # Handle B-prefixed times (began before monitoring started)
        t = self.begin_time.lstrip("B")
        if len(t) == 4:
            return f"{t[:2]}:{t[2:]}"
        return self.begin_time

    @property
    def max_time_formatted(self) -> str:
        """Format max time as HH:MM."""
        if not self.max_time or self.max_time == "////":
            return "—"
        if len(self.max_time) == 4:
            return f"{self.max_time[:2]}:{self.max_time[2:]}"
        return self.max_time

    @property
    def end_time_formatted(self) -> str:
        """Format end time as HH:MM."""
        if not self.end_time or self.end_time == "////":
            return "—"
        if len(self.end_time) == 4:
            return f"{self.end_time[:2]}:{self.end_time[2:]}"
        return self.end_time

    @property
    def time_range(self) -> str:
        """Get formatted time range string."""
        begin = self.begin_time_formatted
        end = self.end_time_formatted
        if begin == "—" and end == "—":
            return "—"
        return f"{begin} – {end}"

    @property
    def duration_minutes(self) -> Optional[int]:
        """Calculate event duration in minutes."""
        try:
            if not self.begin_time or not self.end_time:
                return None
            begin = self.begin_time.lstrip("B")
            end = self.end_time
            if begin == "////" or end == "////":
                return None
            begin_mins = int(begin[:2]) * 60 + int(begin[2:])
            end_mins = int(end[:2]) * 60 + int(end[2:])
            if end_mins < begin_mins:
                end_mins += 24 * 60  # Crosses midnight
            return end_mins - begin_mins
        except (ValueError, IndexError):
            return None

    @property
    def flare_class(self) -> Optional[str]:
        """Extract flare class for XRA events (e.g., 'M1.9')."""
        if self.event_type == "XRA":
            # Particulars contains something like "M1.9    1.5E-02"
            parts = self.particulars.split()
            if parts:
                return parts[0]
        return None

    @property
    def flare_class_letter(self) -> Optional[str]:
        """Get just the letter class (A, B, C, M, X)."""
        fc = self.flare_class
        if fc and len(fc) > 0:
            return fc[0].upper()
        return None

    @property
    def flare_class_color(self) -> str:
        """Get color for flare class."""
        letter = self.flare_class_letter
        return FLARE_CLASS_COLORS.get(letter, "#808080")

    @property
    def optical_class(self) -> Optional[str]:
        """Extract optical flare class for FLA events (e.g., 'SF', '1N')."""
        if self.event_type == "FLA":
            parts = self.particulars.split()
            if parts:
                return parts[0]
        return None

@dataclass
class ECallistoBurst:
    """Represents a single e-CALLISTO radio burst event."""
    date: str  # YYYYMMDD
    time_range: str  # HH:MM-HH:MM
    burst_type: str  # II, III, IV, etc.
    stations: str  # Comma-separated list of stations
    raw_line: str = ""

    @property
    def begin_time(self) -> str:
        """Get begin time in HH:MM format."""
        if "-" in self.time_range:
            return self.time_range.split("-")[0]
        return self.time_range

    @property
    def end_time(self) -> str:
        """Get end time in HH:MM format."""
        if "-" in self.time_range:
            return self.time_range.split("-")[1]
        return self.time_range

    @property
    def stations_list(self) -> List[str]:
        """Get stations as a list."""
        return [s.strip() for s in self.stations.split(",")]

def fetch_events_raw(event_date: date) -> Optional[str]:
    """
    Fetch raw NOAA events text from solarmonitor.org.

    Args:
        event_date: The date to fetch events for

    Returns:
        Raw text content or None if fetch failed
    """
    year = event_date.strftime("%Y")
    month = event_date.strftime("%m")
    day = event_date.strftime("%d")
    date_str = event_date.strftime("%Y%m%d")

    url = f"https://solarmonitor.org/data/{year}/{month}/{day}/meta/noaa_events_raw_{date_str}.txt"

    try:
        from ..utils import get_global_session
    except ImportError:
        from solar_radio_image_viewer.utils import get_global_session

    session = get_global_session()

    try:
        response = session.get(url)
        return response.text
    except Exception as e:
        # Handle 404 or other errors
        if hasattr(e, "response") and e.response is not None:
            if e.response.status_code == 404:
                return None  # No events file for this date
        print(f"Error fetching NOAA events: {e}")
        return None


def parse_events(raw_text: str) -> List[SolarEvent]:
    """
    Parse raw NOAA events text into structured SolarEvent objects.

    Args:
        raw_text: Raw text from NOAA events file

    Returns:
        List of SolarEvent objects
    """
    events = []

    for line in raw_text.split("\n"):
        # Skip comments and empty lines
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(":"):
            continue

        # Parse event line
        # Format: Event# +  Begin  Max    End   Obs  Q  Type  Loc/Frq  Particulars  Reg#
        # Example: 4470 +     1235   1246      1258  G16  5   XRA  1-8A      M1.9    1.5E-02   3455

        try:
            # Event ID (first 4-5 chars)
            event_id = line[:5].strip()

            # Check for '+' marker (follow-up event)
            is_followup = "+" in line[5:7]

            # Times: positions 7-11, 14-18, 24-28 (approximate, use regex)
            # Use regex for more reliable parsing
            # Event ID can be 3-5 digits (e.g., 250, 4470)
            pattern = r"(\d{3,5})\s*\+?\s+([B]?\d{4}|////)\s+(\d{4}|////)\s+(\d{4}|////)\s+(\w{3})\s+(\S+)\s+(\w{3})\s+(\S+)\s+(.*)"
            match = re.match(pattern, line)

            if match:
                event_id = match.group(1)
                begin_time = match.group(2) if match.group(2) != "////" else None
                max_time = match.group(3) if match.group(3) != "////" else None
                end_time = match.group(4) if match.group(4) != "////" else None
                observatory = match.group(5)
                quality = match.group(6)
                event_type = match.group(7)
                location_or_freq = match.group(8)
                rest = match.group(9).strip()

                # Split rest into particulars and region
                parts = rest.split()
                if parts and parts[-1].isdigit() and len(parts[-1]) == 4:
                    active_region = parts[-1]
                    particulars = " ".join(parts[:-1])
                else:
                    active_region = None
                    particulars = rest

                events.append(
                    SolarEvent(
                        event_id=event_id,
                        is_followup="+" in line[4:7],
                        begin_time=begin_time,
                        max_time=max_time,
                        end_time=end_time,
                        observatory=observatory,
                        quality=quality,
                        event_type=event_type,
                        location_or_freq=location_or_freq,
                        particulars=particulars,
                        active_region=active_region,
                        raw_line=line,
                    )
                )
        except Exception:
            # Skip malformed lines
            continue

    return events


def fetch_and_parse_events(event_date: date) -> Optional[List[SolarEvent]]:
    """
    Fetch and parse NOAA events for a given date.

    Args:
        event_date: The date to fetch events for

    Returns:
        List of SolarEvent objects or None if fetch failed
    """
    raw_text = fetch_events_raw(event_date)
    if raw_text is None:
        return None
    return parse_events(raw_text)


def categorize_events(events: List[SolarEvent]) -> Dict[str, List[SolarEvent]]:
    """
    Categorize events by type category.

    Returns:
        Dict with keys: 'xray', 'optical', 'radio', 'cme', 'other'
    """
    categories = {
        "xray": [],
        "optical": [],
        "radio": [],
        "cme": [],
        "other": [],
    }

    for event in events:
        category = event.type_info.get("category", "other")
        if category in categories:
            categories[category].append(event)
        else:
            categories["other"].append(event)

    return categories


def get_event_statistics(events: List[SolarEvent]) -> Dict[str, Any]:
    """
    Calculate statistics for a list of events.

    Returns:
        Dict with statistics like counts, max flare class, etc.
    """
    stats = {
        "total": len(events),
        "by_type": {},
        "max_xray_class": None,
        "max_xray_event": None,
        "active_regions": set(),
    }

    max_class_order = {"A": 0, "B": 1, "C": 2, "M": 3, "X": 4}
    max_class_value = -1

    for event in events:
        # Count by type
        t = event.event_type
        stats["by_type"][t] = stats["by_type"].get(t, 0) + 1

        # Track active regions
        if event.active_region:
            stats["active_regions"].add(event.active_region)

        # Find max X-ray class
        if event.event_type == "XRA":
            letter = event.flare_class_letter
            if letter and letter in max_class_order:
                class_val = max_class_order[letter]
                if class_val > max_class_value:
                    max_class_value = class_val
                    stats["max_xray_class"] = event.flare_class
                    stats["max_xray_event"] = event
                elif class_val == max_class_value:
                    # Compare numeric part
                    try:
                        current_num = float(event.flare_class[1:])
                        max_num = float(stats["max_xray_class"][1:])
                        if current_num > max_num:
                            stats["max_xray_class"] = event.flare_class
                            stats["max_xray_event"] = event
                    except (ValueError, IndexError):
                        pass

    stats["active_regions"] = list(stats["active_regions"])
    return stats

def fetch_ecallisto_bursts_raw(event_date: date) -> Optional[str]:
    """
    Fetch raw e-CALLISTO burst list text for the month of the given date.
    
    Source: https://soleil.i4ds.ch/solarradio/data/BurstLists/2010-yyyy_Monstein/{YYYY}/e-CALLISTO_{YYYY}_{MM}.txt
    """
    year = event_date.strftime("%Y")
    month = event_date.strftime("%m")
    
    # 2012-2019 are missing or in a different format according to user
    # Link: https://soleil.i4ds.ch/solarradio/data/BurstLists/2010-yyyy_Monstein/
    if 2012 <= event_date.year <= 2019:
        return None

    url = f"https://soleil.i4ds.ch/solarradio/data/BurstLists/2010-yyyy_Monstein/{year}/e-CALLISTO_{year}_{month}.txt"

    try:
        from ..utils import get_global_session
    except ImportError:
        try:
            from solar_radio_image_viewer.utils import get_global_session
        except ImportError:
            import requests
            session = requests.Session()
            response = session.get(url, timeout=10)
            return response.text

    session = get_global_session()

    try:
        response = session.get(url, timeout=10)
        if response.status_code == 200:
            return response.text
        return None
    except Exception as e:
        print(f"Error fetching e-CALLISTO bursts: {e}")
        return None


def parse_ecallisto_bursts(raw_text: str, event_date: date) -> List[ECallistoBurst]:
    """
    Parse e-CALLISTO burst list text into structured ECallistoBurst objects for a specific date.
    """
    bursts = []
    target_date_str = event_date.strftime("%Y%m%d")
    
    # Header skip logic
    lines = raw_text.split("\n")
    data_started = False
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        if "Date" in line and "Time" in line and "Type" in line:
            data_started = True
            continue
            
        if line.startswith("---") or line.startswith("#---"):
            data_started = True
            continue

        if line.startswith("#"):
            continue

        if data_started:
            # Format: YYYYMMDD        HH:MM-HH:MM     Type    Stations
            # Example: 20241201        02:36-02:37     III     Australia-ASSA, INDIA-GAURI
            
            # Use regex to handle variable whitespace
            # Date is 8 digits, Time is HH:MM-HH:MM or HH:MM
            pattern = r"^(\d{8})\s+(\d{2}:\d{2}-\d{2}:\d{2}|\d{2}:\d{2})\s+(\S+)\s+(.*)$"
            match = re.match(pattern, line)
            
            if match:
                b_date = match.group(1)
                if b_date == target_date_str:
                    bursts.append(ECallistoBurst(
                        date=b_date,
                        time_range=match.group(2),
                        burst_type=match.group(3),
                        stations=match.group(4),
                        raw_line=line
                    ))
            elif line.startswith(target_date_str):
                # Fallback simple split if regex fails but date matches
                parts = line.split(None, 3)
                if len(parts) >= 4:
                    bursts.append(ECallistoBurst(
                        date=parts[0],
                        time_range=parts[1],
                        burst_type=parts[2],
                        stations=parts[3],
                        raw_line=line
                    ))
                    
    return bursts


def fetch_and_parse_ecallisto_bursts(event_date: date) -> List[ECallistoBurst]:
    """
    Fetch and parse e-CALLISTO bursts for a given date.
    """
    raw_text = fetch_ecallisto_bursts_raw(event_date)
    if not raw_text:
        return []
    return parse_ecallisto_bursts(raw_text, event_date)



if __name__ == "__main__":
    # Test with sample date
    from datetime import date

    test_date = date(2023, 10, 2)
    print(f"Fetching events for {test_date}...")

    events = fetch_and_parse_events(test_date)
    if events:
        print(f"Found {len(events)} events")

        categories = categorize_events(events)
        for cat, cat_events in categories.items():
            if cat_events:
                print(f"\n{cat.upper()} ({len(cat_events)} events):")
                for e in cat_events[:3]:
                    print(
                        f"  {e.time_range} | {e.event_type} | {e.particulars} | AR {e.active_region}"
                    )

        stats = get_event_statistics(events)
        print(f"\nStatistics:")
        print(f"  Max X-ray class: {stats['max_xray_class']}")
        print(f"  Active regions: {stats['active_regions']}")
    else:
        print("No events found or fetch failed")

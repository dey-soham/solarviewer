"""
NOAA Solar Events module - Fetch and display NOAA solar events.
"""

from .noaa_events import (
    SolarEvent,
    EVENT_TYPES,
    FLARE_CLASS_COLORS,
    fetch_events_raw,
    parse_events,
    fetch_and_parse_events,
    categorize_events,
    get_event_statistics,
)

from .noaa_events_gui import (
    NOAAEventsViewer,
    show_noaa_events_viewer,
)

__all__ = [
    "SolarEvent",
    "EVENT_TYPES",
    "FLARE_CLASS_COLORS",
    "fetch_events_raw",
    "parse_events",
    "fetch_and_parse_events",
    "categorize_events",
    "get_event_statistics",
    "NOAAEventsViewer",
    "show_noaa_events_viewer",
]

#!/usr/bin/env python3
"""
Solar Context Module - Provides solar activity context data.

Submodules:
- active_regions: NOAA Active Region data
- realtime_data: Real-time solar wind, Kp index, F10.7 flux
- cme_alerts: CME data from NASA DONKI
"""

from .active_regions import (
    ActiveRegion,
    fetch_and_parse_active_regions,
    get_ar_statistics,
)

from .realtime_data import (
    KpIndexData,
    F107FluxData,
    SolarConditions,
    fetch_conditions_for_date,
    fetch_current_conditions,
)

from .cme_alerts import (
    CMEEvent,
    fetch_and_parse_cme_events,
)

__all__ = [
    "ActiveRegion",
    "fetch_and_parse_active_regions",
    "get_ar_statistics",
    "KpIndexData",
    "F107FluxData",
    "SolarConditions",
    "fetch_conditions_for_date",
    "fetch_current_conditions",
    "CMEEvent",
    "fetch_and_parse_cme_events",
]

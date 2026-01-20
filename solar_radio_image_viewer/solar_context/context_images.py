#!/usr/bin/env python3
"""
Context Images Fetcher - Get solar context imagery from Helioviewer API or SolarMonitor.org.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional, Dict, Tuple
import requests
import re
from urllib.parse import urljoin
import json


@dataclass
class ContextImage:
    """Represents a context image."""

    title: str
    thumb_url: str
    page_url: str  # URL to the full disk page or full-res image
    instrument: str
    description: str
    credits: str = "Helioviewer"  # Credit source
    source_id: Optional[int] = None  # Helioviewer sourceId if applicable


# Essential instruments for context viewing
# Format: (nickname, layer_path, description)
# layer_path format: [observatory,instrument,detector,measurement,visible,opacity]
ESSENTIAL_INSTRUMENTS = [
    (
        "AIA 304",
        "[SDO,AIA,AIA,304,1,100]",
        "SDO",
        "304Å Chromosphere & Transition Region (He II)",
    ),
    (
        "AIA 193",
        "[SDO,AIA,AIA,193,1,100]",
        "SDO",
        "193Å Corona & Flare Plasma (Fe XII/XXIV)",
    ),
    (
        "AIA 171",
        "[SDO,AIA,AIA,171,1,100]",
        "SDO",
        "171Å Upper Transition Region & Quiet Corona (Fe IX)",
    ),
    ("AIA 335", "[SDO,AIA,AIA,335,1,100]", "SDO", "335Å Active Region Corona (Fe XVI)"),
    (
        "HMI Mag",
        "[SDO,HMI,HMI,magnetogram,1,100]",
        "SDO",
        "Magnetogram (Photospheric Magnetic Field)",
    ),
    (
        "HMI Int",
        "[SDO,HMI,HMI,continuum,1,100]",
        "SDO",
        "Continuum Intensity (Photosphere, Sunspots)",
    ),
    (
        "LASCO C2",
        "[SOHO,LASCO,C2,white-light,1,100]",
        "SOHO",
        "Inner Coronagraph (2-6 R☉, White Light)",
    ),
    (
        "LASCO C3",
        "[SOHO,LASCO,C3,white-light,1,100]",
        "SOHO",
        "Outer Coronagraph (4-30 R☉, White Light)",
    ),
    (
        "SUVI 171",
        "[GOES,SUVI,SUVI,171,1,100]",
        "GOES",
        "171Å Upper Transition Region & Corona",
    ),
    (
        "SUVI 304",
        "[GOES,SUVI,SUVI,304,1,100]",
        "GOES",
        "304Å Chromosphere & Transition Region",
    ),
    (
        "EUVI-A 171",
        "[STEREO_A,SECCHI,EUVI,171,1,100]",
        "STEREO_A",
        "171Å Corona from STEREO-A (Far-side view)",
    ),
    (
        "EUVI-A 304",
        "[STEREO_A,SECCHI,EUVI,304,1,100]",
        "STEREO_A",
        "304Å Chromosphere from STEREO-A (Far-side view)",
    ),
]


def fetch_from_helioviewer(event_date: date) -> List[ContextImage]:
    """
    Fetch essential context images from Helioviewer API as PNG screenshots.

    Args:
        event_date: Date to fetch images for

    Returns:
        List of ContextImage objects with PNG image URLs
    """
    # Format date for Helioviewer API (ISO 8601 UTC)
    date_str = f"{event_date.strftime('%Y-%m-%d')}T12:00:00Z"

    images = []

    # Fetch only essential instruments with PNG screenshots
    for nickname, layer_path, observatory, description in ESSENTIAL_INSTRUMENTS:
        # Determine image type for appropriate field of view
        is_lasco_c2 = "LASCO,C2" in layer_path
        is_lasco_c3 = "LASCO,C3" in layer_path

        # Thumbnail parameters - optimized for each image type
        base_url = "https://api.helioviewer.org/v2/takeScreenshot/"

        if is_lasco_c2:
            # LASCO C2: Inner coronagraph - needs MORE FoV than before
            thumb_params = {
                "date": date_str,
                "imageScale": "75",
                "layers": layer_path,
                "x0": "0",
                "y0": "0",
                "width": "160",
                "height": "160",
                "display": "true",
                "watermark": "false",
            }
            full_params = {
                "date": date_str,
                "imageScale": "6",
                "layers": layer_path,
                "x0": "0",
                "y0": "0",
                "width": "2048",
                "height": "2048",
                "display": "true",
                "watermark": "false",
            }
        elif is_lasco_c3:
            # LASCO C3: Outer coronagraph - needs A LOT MORE FoV
            thumb_params = {
                "date": date_str,
                "imageScale": "240",
                "layers": layer_path,
                "x0": "0",
                "y0": "0",
                "width": "160",
                "height": "160",
                "display": "true",
                "watermark": "false",
            }
            full_params = {
                "date": date_str,
                "imageScale": "28.0",
                "layers": layer_path,
                "x0": "0",
                "y0": "0",
                "width": "2048",
                "height": "2048",
                "display": "true",
                "watermark": "false",
            }
        else:
            # Solar disk images - need LESS FoV (less black space)
            thumb_params = {
                "date": date_str,
                "imageScale": "15.0",
                "layers": layer_path,
                "x0": "0",
                "y0": "0",
                "width": "160",
                "height": "160",
                "display": "true",
                "watermark": "false",
            }
            full_params = {
                "date": date_str,
                "imageScale": "1.6",
                "layers": layer_path,
                "x0": "0",
                "y0": "0",
                "width": "1600",
                "height": "1600",
                "display": "true",
                "watermark": "false",
            }

        # Construct URLs with parameters
        from urllib.parse import urlencode

        thumb_url = f"{base_url}?{urlencode(thumb_params)}"
        full_url = f"{base_url}?{urlencode(full_params)}"

        images.append(
            ContextImage(
                title=nickname,
                thumb_url=thumb_url,
                page_url=full_url,
                instrument=observatory,
                description=description,
                credits="Helioviewer",
                source_id=None,
            )
        )

    return images


def fetch_from_solarmonitor(event_date: date) -> List[ContextImage]:
    """
    Scrape SolarMonitor index page to find all available context images for the date.
    Returns list of ContextImage objects with thumbnail and page URLs.

    This is the legacy/fallback method.
    """
    date_str = event_date.strftime("%Y%m%d")
    base_url = "https://solarmonitor.org/"
    index_url = f"{base_url}index.php?date={date_str}"

    images = []

    try:
        from ..utils import get_global_session
    except ImportError:
        from solar_radio_image_viewer.utils import get_global_session

    session = get_global_session()

    try:
        response = session.get(index_url)

        if response.status_code != 200:
            print(f"SolarMonitor index fetch failed: {response.status_code}")
            return []

        html = response.text

        # Regex to find links to full_disk.php wrapping a thumbnail
        pattern = re.compile(
            r'href=["\']?(full_disk\.php[^"\'>]+)["\']?[^>]*>[\s\S]*?<img[^>]+src=["\']?([^"\'>  ]+thumb\.png)["\']?',
            re.IGNORECASE,
        )

        matches = pattern.findall(html)

        seen_types = set()

        for page_link, thumb_path in matches:
            # Extract 'type' from page_link to use as title/ID
            type_match = re.search(r"type=([a-zA-Z0-9_]+)", page_link)
            img_type = type_match.group(1) if type_match else "unknown"

            if img_type in seen_types:
                continue
            seen_types.add(img_type)

            # Construct absolute URLs
            full_page_url = urljoin(base_url, page_link)
            full_thumb_url = urljoin(base_url, thumb_path)

            # Metadata lookup
            instrument, desc = _identify_instrument(img_type)

            images.append(
                ContextImage(
                    title=img_type.replace("_", " ").upper(),
                    thumb_url=full_thumb_url,
                    page_url=full_page_url,
                    instrument=instrument,
                    description=desc,
                    credits="SolarMonitor",
                )
            )

    except Exception as e:
        print(f"Error scraping SolarMonitor: {e}")

    return images


def fetch_context_images(
    event_date: date, use_helioviewer: bool = True
) -> List[ContextImage]:
    """
    Fetch context images for a given date.

    Args:
        event_date: Date to fetch images for
        use_helioviewer: If True, use Helioviewer API (default, returns PNG).
                        If False, use SolarMonitor (strict backup only).

    Returns:
        List of ContextImage objects
    """
    if use_helioviewer:
        images = fetch_from_helioviewer(event_date)
        if images:
            return images
        # Fall back to SolarMonitor if Helioviewer fails
        print("Helioviewer fetch failed, falling back to SolarMonitor")

    return fetch_from_solarmonitor(event_date)


def resolve_full_image_url(page_url: str) -> Optional[str]:
    """
    Given an image page URL, resolve to the actual full-resolution image URL.

    For Helioviewer URLs, returns the URL directly.
    For SolarMonitor URLs, scrapes the page to find the image.
    """
    # Check if it's a Helioviewer URL
    if "helioviewer.org" in page_url:
        # It's already a direct image URL from Helioviewer
        return page_url

    # Otherwise, it's a SolarMonitor URL - scrape it
    try:
        from ..utils import get_global_session
    except ImportError:
        from solar_radio_image_viewer.utils import get_global_session

        session = get_global_session()
        response = session.get(page_url)
        if response.status_code != 200:
            return None

        html = response.text

        # Find all PNG images
        all_imgs = re.findall(r'src=["\']?([^"\'>  ]+\.png)["\']?', html, re.IGNORECASE)

        for img_path in all_imgs:
            if "thmb" not in img_path and "common_files" not in img_path:
                return urljoin("https://solarmonitor.org/", img_path)

    except Exception as e:
        print(f"Error resolving full image: {e}")

    return None


def _identify_instrument(img_type: str) -> Tuple[str, str]:
    """Refine instrument name and description based on code (for SolarMonitor)."""
    code = img_type.lower()
    if "saia" in code:
        if "193" in code:
            return "SDO AIA", "193Å (Corona)"
        if "094" in code:
            return "SDO AIA", "94Å (Hot Flare)"
        if "335" in code:
            return "SDO AIA", "335Å (Active Region)"
        return "SDO AIA", "EUV Image"
    if "seit" in code:
        return "SOHO EIT", "EUV (Historical)"
    if "shmi" in code:
        return "SDO HMI", "Magnetogram"
    if "smdi" in code:
        return "SOHO MDI", "Magnetogram (Historical)"
    if "gong" in code:
        return "GONG", "H-Alpha (Chromosphere)"
    if "bbso" in code:
        return "BBSO", "H-Alpha"
    if "swap" in code:
        return "Proba-2 SWAP", "174Å (Corona)"
    if "trce" in code:
        return "TRACE", "EUV (Historical)"
    if "sxi" in code or "suvi" in code or "goes" in code:
        return "GOES", "X-Ray Imager"
    if "lasc" in code or "c2" in code or "c3" in code:
        return "SOHO LASCO", "Coronagraph"
    return "Unknown", "Solar Context"

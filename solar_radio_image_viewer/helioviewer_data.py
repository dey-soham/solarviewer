"""
Helioviewer data for the browser: the instruments and their cadences, the
list of data sources from the Helioviewer API, and the thread that
downloads one image.
"""

import logging
from PyQt5.QtCore import (
    QThread,
    pyqtSignal,
)
from PyQt5.QtGui import QPixmap, QImage
import requests
from typing import List, Tuple

logger = logging.getLogger(__name__)


# Known instrument cadences (in seconds) - Based on official mission documentation
INSTRUMENT_CADENCES = {
    # SDO/AIA - 12s for EUV, 24s for UV, 3600s for visible
    "AIA 94": 12,
    "AIA 131": 12,
    "AIA 171": 12,
    "AIA 193": 12,
    "AIA 211": 12,
    "AIA 304": 12,
    "AIA 335": 12,
    "AIA 1600": 24,
    "AIA 1700": 24,
    "AIA 4500": 3600,
    # SDO/HMI - 45s for both magnetogram and continuum
    "HMI magnetogram": 45,
    "HMI continuum": 45,
    # SOHO/EIT - 12 minutes (720s) for all wavelengths
    "EIT 171": 720,
    "EIT 195": 720,
    "EIT 284": 720,
    "EIT 304": 720,
    # SOHO/MDI - 96 minutes
    "MDI magnetogram": 96,
    "MDI continuum": 96,
    # SOHO/LASCO - 30 minutes for both C2 and C3
    "LASCO": 1800,
    "LASCO C2": 1800,
    "LASCO C3": 1800,
    # STEREO/SECCHI EUVI - 2.5 minutes (150s)
    "EUVI": 150,
    "EUVI 171": 150,
    "EUVI 195": 150,
    "EUVI 284": 150,
    "EUVI 304": 150,
    # STEREO/SECCHI Coronagraphs
    "COR1": 300,  # 5 minutes
    "COR2": 900,  # 15 minutes
    # GOES/SUVI - 10s for all channels
    "SUVI": 10,
    "SUVI 94": 10,
    "SUVI 131": 10,
    "SUVI 171": 10,
    "SUVI 195": 10,
    "SUVI 284": 10,
    "SUVI 304": 10,
    # Solar Orbiter/EUI
    "EUI": 600,
    "FSI": 600,  # Full Sun Imager - 10 minutes
    "HRI": 300,  # High Resolution Imager - 5 minutes
    # Legacy/Other
    "SXT": 60,  # Yohkoh SXT
}


# Whitelist: Only instruments with verified cadence AND FOV parameters
# Using patterns that match API-returned names
VERIFIED_INSTRUMENTS = [
    # SDO - 100% verified
    "AIA 94",
    "AIA 131",
    "AIA 171",
    "AIA 193",
    "AIA 211",
    "AIA 304",
    "AIA 335",
    "AIA 1600",
    "AIA 1700",
    "AIA 4500",
    "HMI magnetogram",
    "HMI continuum",
    # SOHO - 100% verified
    "EIT 171",
    "EIT 195",
    "EIT 284",
    "EIT 304",
    "LASCO/C2 white-light",
    "LASCO/C3 white-light",  # API format
    # STEREO - 100% verified (both A and B)
    "SECCHI/EUVI 171",
    "SECCHI/EUVI 195",
    "SECCHI/EUVI 284",
    "SECCHI/EUVI 304",
    "SECCHI/COR1 white-light",
    "SECCHI/COR2 white-light",
    # GOES/SUVI - 100% verified
    "SUVI 94",
    "SUVI 131",
    "SUVI 171",
    "SUVI 195",
    "SUVI 284",
    "SUVI 304",
]


def fetch_all_instruments() -> List[Tuple[str, str, str, str]]:
    """Fetch all available instruments from Helioviewer API."""
    try:
        url = "https://api.helioviewer.org/v2/getDataSources/"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        instruments = []

        for observatory, obs_data in sorted(data.items()):
            if not isinstance(obs_data, dict):
                continue

            for instrument, inst_data in sorted(obs_data.items()):
                if not isinstance(inst_data, dict):
                    continue

                # At this level, keys are either measurements (dict with metadata) or detectors (dict of measurements)
                for key, value in sorted(inst_data.items()):
                    if not isinstance(value, dict):
                        continue

                    # Check if this is a measurement (has 'sourceId') or a detector level
                    if "sourceId" in value:
                        # This is a measurement directly under instrument
                        measurement = key
                        source_id = value["sourceId"]
                        # Layer format is [sourceId,visible,opacity]
                        layer = f"[{source_id},1,100]"
                        name = f"{instrument} {measurement}"

                        # Determine cadence - try exact match first, then instrument family
                        cadence = INSTRUMENT_CADENCES.get(
                            name,  # Try exact: "AIA 171"
                            INSTRUMENT_CADENCES.get(
                                instrument, 60  # Try instrument: "AIA"  # Default: 60s
                            ),
                        )

                        instruments.append((name, layer, observatory, cadence))
                    else:
                        # This is a detector level, iterate measurements
                        detector = key
                        for measurement, meas_data in sorted(value.items()):
                            if not isinstance(meas_data, dict):
                                continue
                            if "sourceId" not in meas_data:
                                continue

                            source_id = meas_data["sourceId"]
                            # Layer format is [sourceId,visible,opacity]
                            layer = f"[{source_id},1,100]"
                            name = f"{instrument}/{detector} {measurement}"

                            # Determine cadence - try exact match first, then detector, then instrument
                            cadence = INSTRUMENT_CADENCES.get(
                                name,  # Try exact: "SECCHI/EUVI 171"
                                INSTRUMENT_CADENCES.get(
                                    f"{detector} {measurement}",  # Try: "EUVI 171"
                                    INSTRUMENT_CADENCES.get(
                                        detector,  # Try: "EUVI"
                                        INSTRUMENT_CADENCES.get(
                                            instrument, 60
                                        ),  # Default: 60s
                                    ),
                                ),
                            )

                            instruments.append((name, layer, observatory, cadence))

        print(f"Loaded {len(instruments)} instruments from Helioviewer")

        # Filter to only verified instruments
        verified = []
        for name, layer, obs, cad in instruments:
            # Check if instrument name is in verified list
            # Also check without observatory prefix for STEREO instruments
            if name in VERIFIED_INSTRUMENTS:
                verified.append((name, layer, obs, cad))
                continue

            # For STEREO, also accept if base name matches (e.g., "EUVI 171" from "SECCHI/EUVI 171")
            if "/" in name:
                base_name = name.split("/")[-1]  # Get "EUVI 171" from "SECCHI/EUVI 171"
                if base_name in [
                    "EUVI 171",
                    "EUVI 195",
                    "EUVI 284",
                    "EUVI 304",
                    "COR1 white-light",
                    "COR2 white-light",
                ]:
                    verified.append((name, layer, obs, cad))

        print(
            f"Filtered to {len(verified)} verified instruments (with known cadence & FOV)"
        )
        return verified
    except Exception as e:
        print(f"Error fetching instruments: {e}")
        import traceback

        traceback.print_exc()
        # Transform VERIFIED_INSTRUMENTS to (name, layer_path, observatory, cadence)
        fallback = []
        for name in VERIFIED_INSTRUMENTS:
            # Format: [observatory,instrument,detector,measurement,visible,opacity]
            
            obs = ""
            inst = ""
            det = ""
            meas = ""
            
            if name.startswith("AIA"):
                obs, inst, det = "SDO", "AIA", "AIA"
                meas = name.split(" ")[1]
            elif name.startswith("HMI"):
                obs, inst, det = "SDO", "HMI", "HMI"
                meas = name.split(" ")[1]
            elif name.startswith("EIT"):
                obs, inst, det = "SOHO", "EIT", "EIT"
                meas = name.split(" ")[1]
            elif name.startswith("LASCO"):
                obs, inst, det = "SOHO", "LASCO", name.split(" ")[1]
                meas = "white-light"
            elif "EUVI" in name:
                # Default to STEREO_A for fallback if not specified
                obs = "STEREO_A"
                inst = "SECCHI"
                det = "EUVI"
                meas = name.split(" ")[1]
            elif "COR" in name:
                obs = "STEREO_A"
                inst = "SECCHI"
                det = name.split(" ")[1] if "/" not in name else name.split("/")[1].split(" ")[0]
                meas = "white-light"
            elif name.startswith("SUVI"):
                obs, inst, det = "GOES-16", "SUVI", "SUVI" # Default to GOES-16
                meas = name.split(" ")[1]
            else:
                continue
                
            layer = f"[{obs},{inst},{det},{meas},1,100]"
            cadence = INSTRUMENT_CADENCES.get(name, 60)
            fallback.append((name, layer, obs, cadence))

        return fallback


class ImageDownloader(QThread):
    """Thread to download a single image."""

    finished = pyqtSignal(str, QPixmap)  # instrument, pixmap
    error = pyqtSignal(str, str)  # instrument, error_msg

    def __init__(self, instrument_name, layer_path, timestamp, width=1000, height=1000):
        super().__init__()
        self.instrument_name = instrument_name
        self.layer_path = layer_path
        self.timestamp = timestamp
        self.width = width
        self.height = height
        self.running = True

    def stop(self):
        """Signal the thread to stop."""
        self.running = False

    def run(self):
        try:
            # Determine imageScale based on instrument

            # Parse instrument from layer (format: [sourceId,1,100])
            # Need to determine instrument type from name passed in
            inst_lower = self.instrument_name.lower()

            # Base imageScale for 1000x1000 images
            # Solar disk instruments - tight fit (2.0-4.0)
            if any(x in inst_lower for x in ["aia", "hmi", "eit", "euvi", "suvi"]):
                if "hmi continuum" in inst_lower:
                    base_image_scale = 3.0  # HMI continuum - slightly wider
                elif "hmi" in inst_lower:
                    base_image_scale = 2.5  # HMI magnetogram
                else:
                    base_image_scale = (
                        2.5  # AIA, EIT, EUVI, SUVI - fits solar disk in 1000px
                    )

            # Coronagraphs - wide field
            elif "lasco" in inst_lower and "c2" in inst_lower:
                base_image_scale = 12.0  # LASCO C2: fits up to 6 Rsun in 1000px
            elif "lasco" in inst_lower and "c3" in inst_lower:
                base_image_scale = 58.0  # LASCO C3: fits up to 30 Rsun in 1000px
            elif any(x in inst_lower for x in ["cor1", "cor2"]):
                if "cor1" in inst_lower:
                    base_image_scale = 6.0  # COR1: ~1.4-4 Rsun
                else:
                    base_image_scale = 15.0  # COR2: ~2.5-15 Rsun

            # Other instruments
            else:
                base_image_scale = 4.0  # Default moderate zoom

            # Adjust imageScale to maintain FOV for different image sizes
            # Formula: new_scale = base_scale * (1000 / actual_size)
            size_factor = 1000.0 / self.width
            image_scale = str(base_image_scale * size_factor)

            # Build Helioviewer URL
            from urllib.parse import urlencode

            base_url = "https://api.helioviewer.org/v2/takeScreenshot/"
            params = {
                "date": self.timestamp.toString("yyyy-MM-ddTHH:mm:ss") + "Z",
                "imageScale": image_scale,
                "layers": self.layer_path,
                "x0": "0",
                "y0": "0",
                "width": str(self.width),
                "height": str(self.height),
                "display": "true",
                "watermark": "false",
            }
            url = f"{base_url}?{urlencode(params)}"

            if not self.running:
                return

            # Download image
            response = requests.get(url, timeout=60)
            response.raise_for_status()

            if not self.running:
                return

            # Convert to QPixmap
            qimage = QImage()
            if qimage.loadFromData(response.content):
                pixmap = QPixmap.fromImage(qimage)
                if self.running:
                    self.finished.emit(self.instrument_name, pixmap)
            else:
                if self.running:
                    self.error.emit(self.instrument_name, "Failed to load image data")

        except Exception as e:
            if self.running:
                self.error.emit(self.instrument_name, str(e))

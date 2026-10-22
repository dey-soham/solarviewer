"""
The solar events viewer's disk cache: fetched events, active regions,
conditions, CMEs, images and e-CALLISTO bursts, kept per day.
"""

import logging
from datetime import datetime, date
from typing import Optional
from . import noaa_events as ne
import json
from pathlib import Path

logger = logging.getLogger(__name__)


def _serialize_obj(obj):
    """Serialize an object to a JSON-compatible dict."""
    if hasattr(obj, '__dict__'):
        d = {}
        for k, v in obj.__dict__.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
            elif isinstance(v, date):
                d[k] = v.isoformat()
            elif isinstance(v, list):
                d[k] = [_serialize_obj(item) for item in v]
            elif hasattr(v, '__dict__'):
                d[k] = _serialize_obj(v)
            else:
                d[k] = v
        d['__class__'] = type(obj).__name__
        return d
    return obj


class NOAAEventsCache:
    """
    Cache manager for NOAA events data.
    
    Stores all fetched data in ~/.cache/solarviewer/noaa_events/YYYY/MM/DD/
    organized by date. Uses JSON for structured data with proper object reconstruction.
    """
    
    DEFAULT_CACHE_DIR = Path.home() / ".cache" / "solarviewer" / "noaa_events"
    
    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or self.DEFAULT_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def get_date_cache_dir(self, event_date: date) -> Path:
        """Get cache directory for a specific date."""
        date_dir = self.cache_dir / str(event_date.year) / f"{event_date.month:02d}" / f"{event_date.day:02d}"
        date_dir.mkdir(parents=True, exist_ok=True)
        return date_dir
    
    def _get_cache_path(self, event_date: date, filename: str) -> Path:
        """Get full path for a cached file."""
        return self.get_date_cache_dir(event_date) / filename
    
    # === Events ===
    def save_events(self, event_date: date, events: list):
        """Save solar events to cache."""
        path = self._get_cache_path(event_date, "events.json")
        with open(path, "w") as f:
            json.dump([_serialize_obj(e) for e in events], f, default=str)
    
    def load_events(self, event_date: date) -> Optional[list]:
        """Load solar events from cache and reconstruct SolarEvent objects."""
        path = self._get_cache_path(event_date, "events.json")
        if not path.exists():
            return None
        try:
            with open(path, "r") as f:
                data = json.load(f)
            # Reconstruct SolarEvent objects
            events = []
            for d in data:
                if isinstance(d, dict):
                    d.pop('__class__', None)  # Remove class marker
                    events.append(ne.SolarEvent(**d))
                else:
                    events.append(d)
            return events
        except Exception as e:
            print(f"Error loading events cache: {e}")
            return None
    
    # === Active Regions ===
    def save_active_regions(self, event_date: date, active_regions: list):
        """Save active regions to cache."""
        path = self._get_cache_path(event_date, "active_regions.json")
        with open(path, "w") as f:
            json.dump([_serialize_obj(ar) for ar in active_regions], f, default=str)
    
    def load_active_regions(self, event_date: date) -> Optional[list]:
        """Load active regions from cache and reconstruct ActiveRegion objects."""
        path = self._get_cache_path(event_date, "active_regions.json")
        if not path.exists():
            return None
        try:
            with open(path, "r") as f:
                data = json.load(f)
            from ..solar_context import active_regions as ar_module
            regions = []
            for d in data:
                if isinstance(d, dict):
                    d.pop('__class__', None)
                    regions.append(ar_module.ActiveRegion(**d))
                else:
                    regions.append(d)
            return regions
        except Exception as e:
            print(f"Error loading active regions cache: {e}")
            return None
    
    # === Conditions ===
    def save_conditions(self, event_date: date, conditions):
        """Save solar conditions to cache."""
        path = self._get_cache_path(event_date, "conditions.json")
        with open(path, "w") as f:
            json.dump(_serialize_obj(conditions), f, default=str)
    
    def load_conditions(self, event_date: date):
        """Load solar conditions from cache and reconstruct SolarConditions object."""
        path = self._get_cache_path(event_date, "conditions.json")
        if not path.exists():
            return None
        try:
            with open(path, "r") as f:
                data = json.load(f)
            from ..solar_context import realtime_data as rt
            
            # Reconstruct nested objects
            kp_index = None
            if data.get('kp_index'):
                kp_data = data['kp_index']
                kp_data.pop('__class__', None)
                kp_data['event_date'] = date.fromisoformat(kp_data['event_date']) if isinstance(kp_data.get('event_date'), str) else kp_data.get('event_date')
                kp_index = rt.KpIndexData(**kp_data)
            
            f107_flux = None
            if data.get('f107_flux'):
                f107_data = data['f107_flux']
                f107_data.pop('__class__', None)
                f107_data['event_date'] = date.fromisoformat(f107_data['event_date']) if isinstance(f107_data.get('event_date'), str) else f107_data.get('event_date')
                f107_flux = rt.F107FluxData(**f107_data)
            
            solar_wind = None
            if data.get('solar_wind'):
                sw_data = data['solar_wind']
                sw_data.pop('__class__', None)
                sw_data['timestamp'] = datetime.fromisoformat(sw_data['timestamp']) if isinstance(sw_data.get('timestamp'), str) else sw_data.get('timestamp')
                solar_wind = rt.SolarWindData(**sw_data)
            
            data.pop('__class__', None)
            cond_date = date.fromisoformat(data['event_date']) if isinstance(data.get('event_date'), str) else data.get('event_date')
            
            return rt.SolarConditions(
                event_date=cond_date,
                kp_index=kp_index,
                f107_flux=f107_flux,
                is_historical=data.get('is_historical', True),
                data_source=data.get('data_source', 'Cache'),
                solar_wind=solar_wind
            )
        except Exception as e:
            print(f"Error loading conditions cache: {e}")
            return None
    
    # === CMEs ===
    def save_cmes(self, event_date: date, cmes: list):
        """Save CME events to cache."""
        path = self._get_cache_path(event_date, "cmes.json")
        with open(path, "w") as f:
            json.dump([_serialize_obj(c) for c in cmes], f, default=str)
    
    def load_cmes(self, event_date: date) -> Optional[list]:
        """Load CME events from cache and reconstruct CMEEvent objects."""
        path = self._get_cache_path(event_date, "cmes.json")
        if not path.exists():
            return None
        try:
            with open(path, "r") as f:
                data = json.load(f)
            from ..solar_context import cme_alerts as cme_module
            cmes = []
            for d in data:
                if isinstance(d, dict):
                    d.pop('__class__', None)
                    # Convert datetime strings back to datetime objects
                    if isinstance(d.get('start_time'), str):
                        d['start_time'] = datetime.fromisoformat(d['start_time'])
                    if d.get('earth_arrival_time') and isinstance(d['earth_arrival_time'], str):
                        d['earth_arrival_time'] = datetime.fromisoformat(d['earth_arrival_time'])
                    cmes.append(cme_module.CMEEvent(**d))
                else:
                    cmes.append(d)
            return cmes
        except Exception as e:
            print(f"Error loading CMEs cache: {e}")
            return None

    # === e-CALLISTO Bursts ===
    def save_ecallisto_bursts(self, event_date: date, bursts: list):
        """Save e-CALLISTO bursts to cache."""
        path = self._get_cache_path(event_date, "ecallisto_bursts.json")
        with open(path, "w") as f:
            json.dump([_serialize_obj(b) for b in bursts], f, default=str)

    def load_ecallisto_bursts(self, event_date: date) -> Optional[list]:
        """Load e-CALLISTO bursts from cache and reconstruct ECallistoBurst objects."""
        path = self._get_cache_path(event_date, "ecallisto_bursts.json")
        if not path.exists():
            return None
        try:
            with open(path, "r") as f:
                data = json.load(f)
            bursts = []
            for d in data:
                if isinstance(d, dict):
                    d.pop('__class__', None)
                    bursts.append(ne.ECallistoBurst(**d))
                else:
                    bursts.append(d)
            return bursts
        except Exception as e:
            print(f"Error loading e-CALLISTO bursts cache: {e}")
            return None

    # === Context Images ===
    def save_images(self, event_date: date, images: list):
        """Save context image metadata to cache."""
        path = self._get_cache_path(event_date, "images.json")
        with open(path, "w") as f:
            json.dump([_serialize_obj(img) for img in images], f, default=str)
    
    def load_images(self, event_date: date) -> Optional[list]:
        """Load context image metadata from cache and reconstruct ContextImage objects."""
        path = self._get_cache_path(event_date, "images.json")
        if not path.exists():
            return None
        try:
            with open(path, "r") as f:
                data = json.load(f)
            from ..solar_context import context_images as ci_module
            images = []
            for d in data:
                if isinstance(d, dict):
                    d.pop('__class__', None)
                    if 'image_date' in d and isinstance(d['image_date'], str):
                        try:
                            d['image_date'] = datetime.fromisoformat(d['image_date'])
                        except ValueError:
                            pass
                    images.append(ci_module.ContextImage(**d))
                else:
                    images.append(d)
            return images
        except Exception as e:
            print(f"Error loading images cache: {e}")
            return None
    
    # === Context Image Thumbnails ===
    def get_thumbnail_path(self, event_date: date, image_title: str) -> Path:
        """Get local path for a cached thumbnail."""
        safe_name = image_title.replace(" ", "_").replace("/", "_")
        return self._get_cache_path(event_date, f"thumb_{safe_name}.png")
    
    def save_thumbnail(self, event_date: date, image_title: str, data: bytes) -> Path:
        """Save a thumbnail image to cache."""
        path = self.get_thumbnail_path(event_date, image_title)
        with open(path, "wb") as f:
            f.write(data)
        return path
    
    def load_thumbnail(self, event_date: date, image_title: str) -> Optional[Path]:
        """Check if thumbnail is cached and return path."""
        path = self.get_thumbnail_path(event_date, image_title)
        return path if path.exists() else None
    
    # === Radio Spectra ===
    def get_spectra_path(self, event_date: date, source: str) -> Path:
        """Get the cache path for a spectra image."""
        ext = "jpg" if source == "wind" else "png"
        return self._get_cache_path(event_date, f"spectra_{source}.{ext}")
    
    def save_spectra(self, event_date: date, source: str, data: bytes) -> Path:
        """Save spectra image to cache."""
        path = self.get_spectra_path(event_date, source)
        with open(path, "wb") as f:
            f.write(data)
        return path
    
    def load_spectra(self, event_date: date, source: str) -> Optional[Path]:
        """Check if spectra is cached and return path."""
        path = self.get_spectra_path(event_date, source)
        return path if path.exists() else None
    
    # === Check what's cached ===
    def get_cached_status(self, event_date: date) -> dict:
        """Check what data is cached for a date."""
        cache_dir = self.get_date_cache_dir(event_date)
        return {
            'events': (cache_dir / "events.json").exists(),
            'active_regions': (cache_dir / "active_regions.json").exists(),
            'conditions': (cache_dir / "conditions.json").exists(),
            'cmes': (cache_dir / "cmes.json").exists(),
            'images': (cache_dir / "images.json").exists(),
            'spectra_wind': self.get_spectra_path(event_date, 'wind').exists(),
            'spectra_stereo_a': self.get_spectra_path(event_date, 'stereo_a').exists(),
            'spectra_stereo_b': self.get_spectra_path(event_date, 'stereo_b').exists(),
            'spectra_orfees': self.get_spectra_path(event_date, 'orfees').exists(),
            'spectra_nda': self.get_spectra_path(event_date, 'nda').exists(),
            'spectra_yamagawa': self.get_spectra_path(event_date, 'yamagawa').exists(),
            'spectra_hiras': self.get_spectra_path(event_date, 'hiras').exists(),
            'ecallisto_bursts': (cache_dir / "ecallisto_bursts.json").exists()
        }
    
    def has_any_spectra(self, event_date: date) -> bool:
        """Check if any radio spectra are cached for this date."""
        status = self.get_cached_status(event_date)
        return any([
            status.get('spectra_wind'),
            status.get('spectra_stereo_a'),
            status.get('spectra_stereo_b'),
            status.get('spectra_orfees'),
            status.get('spectra_nda'),
            status.get('spectra_yamagawa'),
            status.get('spectra_hiras')
        ])
    
    def is_fully_cached(self, event_date: date) -> bool:
        """Check if all main data is cached (events, active_regions, conditions, cmes, images)."""
        status = self.get_cached_status(event_date)
        # Core data (not including spectra which is fetched separately)
        return all([
            status['events'],
            status['active_regions'],
            status['conditions'],
            status['cmes'],
            status['images'],
        ])


# Global cache instance
_noaa_cache = NOAAEventsCache()

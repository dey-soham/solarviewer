"""
The solar events viewer's disk cache: fetched events, active regions,
conditions, CMEs, images and e-CALLISTO bursts, kept per day.
"""

import dataclasses
import hashlib
import logging
import math
import os
import threading
import typing
from datetime import datetime, date
from typing import Optional
from . import noaa_events as ne
import json
from pathlib import Path

logger = logging.getLogger(__name__)


LONGEST_TITLE = 200  # bytes of an image's title in a file's name

# What a source's file is, where it is no PNG
SPECTRA_SUFFIXES = {
    "wind": "jpg", "context": "jpg", "learmonth": "srs", "learmonth_next": "srs", "learmonth_plot": "gif",
}


def keep(save, *args):
    """``save(*args)``, True if it worked. A cache that cannot be written (a
    full disk, above all) costs the next run a download: it must not cost this
    one what it has already fetched."""
    try:
        save(*args)
        return True
    except OSError as e:
        logger.warning("Could not write to the cache: %s", e)
        return False


def _write_whole(path, data):
    """``data`` into the file ``path``, which holds all of it or stays as it
    was: a write cut short would read as a file from the archive. The partial
    file's name stays short, for a path at the system's limit."""
    partial = path.with_name(f".{os.getpid():x}.{threading.get_ident():x}.part")
    try:
        with open(partial, "wb") as f:
            f.write(data)
        partial.replace(path)
    except OSError:
        try:
            partial.unlink(missing_ok=True)
        except OSError as e:
            logger.info("Could not remove %s: %s", partial, e)
        raise


def _write_json(path, record):
    """``record`` as JSON into ``path``, by ``_write_whole``: the last good
    file stays until the new one is all there."""
    _write_whole(path, json.dumps(record, default=str).encode("utf-8"))


def _there(path):
    """Whether ``path`` can be seen: a name too long or a folder that may not
    be read is no file to this cache, not a failure in a window's paint."""
    return os.path.exists(path)


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


def _fits(value, hint):
    """Whether ``value`` is of the type ``hint`` of a dataclass field."""
    kind = typing.get_origin(hint)
    if kind is typing.Union:
        return any(_fits(value, one) for one in typing.get_args(hint))
    if kind is list:
        return isinstance(value, list) and all(_fits(item, typing.get_args(hint)[0]) for item in value)
    if hint is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if hint is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if hint is date:
        return isinstance(value, date)
    return isinstance(value, hint)


def _checked(item):
    """``item``, a dataclass read from a file, if each field is what it says.
    A cached file is only as good as the last hand or crash to touch it, and
    what is wrong in one would surface far off, in a window's paint."""
    hints = typing.get_type_hints(type(item))
    for field in dataclasses.fields(item):
        if not _fits(getattr(item, field.name), hints[field.name]):
            raise ValueError(f"{type(item).__name__}.{field.name} is not of its type")
    return item


def _entries(data):
    """The dicts of a list read from a file, each without its class mark."""
    if not isinstance(data, list) or not all(isinstance(entry, dict) for entry in data):
        raise ValueError("not a list of records")
    for entry in data:
        entry.pop('__class__', None)
    return data


def _when(value):
    """A stamp from a file, as ``_serialize_obj`` wrote it, or None."""
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def _day_of(value):
    return date.fromisoformat(value) if isinstance(value, str) else value


def _numbers(values, length=None):
    """Whether ``values`` is a list of numbers and gaps, ``length`` long."""
    return (
        isinstance(values, list) and (length is None or len(values) == length)
        and all(value is None or _fits(value, float) for value in values)
    )


def _has_keys(record, keys):
    return isinstance(record, dict) and all(key in record for key in keys)


def _good_lwa_times(table):
    """A table as ``read_lwa_table`` makes it: nothing else may place a lane."""
    return (
        _has_keys(table, ("runs", "samples", "levels"))
        and isinstance(table["runs"], list)
        and all(_numbers(run, 4) and None not in run for run in table["runs"])
        and _fits(table["samples"], int) and _numbers(table["levels"])
    )


def _good_lwa_movie(record):
    """A record as the browser's OVRO-LWA loader writes it: a movie on disk
    is only used for what this says it is."""
    if not (
        _has_keys(record, ("url", "etag", "last_modified", "bytes"))
        and all(isinstance(record[key], str) for key in ("url", "etag", "last_modified"))
        and _fits(record["bytes"], int) and record["bytes"] > 0
    ):
        return False
    proof = record.get("proof", "")
    if proof not in ("", "confirmed", "failed"):
        return False
    if proof == "confirmed":
        times = record.get("times")
        if not (_fits(times, typing.List[str]) and times and _fits(record.get("frames"), int)):
            return False
        try:
            stamps = [datetime.fromisoformat(stamp) for stamp in times]
        except ValueError:
            return False
        if len(stamps) != record["frames"] or stamps != sorted(stamps):
            return False
    return True


def _good_radio_notes(notes):
    """The notes ``RadioImages`` keeps: the window reads them as they stand."""
    from ..solar_context import radio_images as ri

    if not isinstance(notes, dict) or not _fits(notes.get("said", []), typing.List[str]):
        return False
    nearest = notes.get("nearest_sundish", "")
    if not isinstance(nearest, str) or (nearest and not date.fromisoformat(nearest)):
        return False
    stamps = notes.get("srh_stamps", [])
    return isinstance(stamps, list) and all(ri.good_stamp(stamp) for stamp in stamps)


class NOAAEventsCache:
    """
    Cache manager for NOAA events data.
    
    Stores all fetched data in ~/.cache/solarviewer/noaa_events/YYYY/MM/DD/
    organized by date. Uses JSON for structured data with proper object reconstruction.
    """
    
    DEFAULT_CACHE_DIR = Path.home() / ".cache" / "solarviewer" / "noaa_events"
    
    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or self.DEFAULT_CACHE_DIR
        self._make_folder(self.cache_dir)

    @staticmethod
    def _make_folder(folder):
        """Reading a date must work on a disk that takes no more: what cannot
        be written fails when it is saved, and ``keep`` looks after that."""
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("Could not make the cache folder %s: %s", folder, e)
    
    def get_date_cache_dir(self, event_date: date) -> Path:
        """Get cache directory for a specific date."""
        date_dir = self.cache_dir / str(event_date.year) / f"{event_date.month:02d}" / f"{event_date.day:02d}"
        self._make_folder(date_dir)
        return date_dir
    
    def _get_cache_path(self, event_date: date, filename: str) -> Path:
        """Get full path for a cached file."""
        return self.get_date_cache_dir(event_date) / filename
    
    # === Events ===
    def save_events(self, event_date: date, events: list):
        """Save solar events to cache, with which file they are from."""
        record = {
            "source": getattr(events, "source", ""),
            "created": getattr(events, "created", ""),
            "final": getattr(events, "final", False),
            "events": [_serialize_obj(e) for e in events],
        }
        _write_json(self._get_cache_path(event_date, "events.json"), record)
    
    def load_events(self, event_date: date) -> Optional[list]:
        """Load solar events from cache and reconstruct SolarEvent objects."""
        path = self._get_cache_path(event_date, "events.json")
        if not _there(path):
            return None
        try:
            with open(path, "r") as f:
                data = json.load(f)
            # (a bare list was saved before the file was noted: a snapshot)
            noted = data if isinstance(data, dict) else {"events": data}
            if not all(isinstance(noted.get(key, ""), str) for key in ("source", "created")):
                raise ValueError("its source cannot be read")
            # Reconstruct SolarEvent objects
            events = ne.EventList()
            events.source = noted.get("source", "")
            events.created = noted.get("created", "")
            events.final = noted.get("final") is True
            for d in _entries(noted.get("events")):
                # Its own line, read as lines are read now: a list saved
                # earlier can hold a flux as a region
                again = ne.parse_events(d.get('raw_line') or "")
                events.append(_checked(again[0] if len(again) == 1 else ne.SolarEvent(**d)))
            return events
        except Exception as e:
            print(f"Error loading events cache: {e}")
            return None
    
    # === Active Regions ===
    def save_active_regions(self, event_date: date, active_regions: list):
        """Save active regions to cache."""
        path = self._get_cache_path(event_date, "active_regions.json")
        _write_json(path, [_serialize_obj(ar) for ar in active_regions])
    
    def load_active_regions(self, event_date: date) -> Optional[list]:
        """Load active regions from cache and reconstruct ActiveRegion objects."""
        path = self._get_cache_path(event_date, "active_regions.json")
        if not _there(path):
            return None
        try:
            with open(path, "r") as f:
                data = json.load(f)
            from ..solar_context import active_regions as ar_module
            return [_checked(ar_module.ActiveRegion(**d)) for d in _entries(data)]
        except Exception as e:
            print(f"Error loading active regions cache: {e}")
            return None
    
    # === Conditions ===
    def save_conditions(self, event_date: date, conditions):
        """Save solar conditions to cache."""
        path = self._get_cache_path(event_date, "conditions.json")
        _write_json(path, _serialize_obj(conditions))
    
    def load_conditions(self, event_date: date):
        """Load solar conditions from cache and reconstruct SolarConditions object."""
        path = self._get_cache_path(event_date, "conditions.json")
        if not _there(path):
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
                kp_data['event_date'] = _day_of(kp_data.get('event_date'))
                kp_index = _checked(rt.KpIndexData(**kp_data))
            
            f107_flux = None
            if data.get('f107_flux'):
                f107_data = data['f107_flux']
                f107_data.pop('__class__', None)
                f107_data['event_date'] = _day_of(f107_data.get('event_date'))
                f107_flux = _checked(rt.F107FluxData(**f107_data))
            
            solar_wind = None
            if data.get('solar_wind'):
                sw_data = data['solar_wind']
                sw_data.pop('__class__', None)
                sw_data['timestamp'] = _when(sw_data.get('timestamp'))
                solar_wind = _checked(rt.SolarWindData(**sw_data))
            
            data.pop('__class__', None)
            return _checked(rt.SolarConditions(
                event_date=_day_of(data.get('event_date')),
                kp_index=kp_index,
                f107_flux=f107_flux,
                is_historical=data.get('is_historical', True),
                data_source=data.get('data_source', 'Cache'),
                solar_wind=solar_wind
            ))
        except Exception as e:
            print(f"Error loading conditions cache: {e}")
            return None
    
    # === CMEs ===
    def save_cmes(self, event_date: date, cmes: list):
        """Save CME events to cache."""
        path = self._get_cache_path(event_date, "cmes.json")
        _write_json(path, [_serialize_obj(c) for c in cmes])
    
    def load_cmes(self, event_date: date, filename: str = "cmes.json") -> Optional[list]:
        """Load CME events from cache and reconstruct CMEEvent objects."""
        path = self._get_cache_path(event_date, filename)
        if not _there(path):
            return None
        try:
            with open(path, "r") as f:
                data = json.load(f)
            from ..solar_context import cme_alerts as cme_module
            cmes = []
            for d in _entries(data):
                # Convert datetime strings back to datetime objects
                d['start_time'] = _when(d.get('start_time'))
                d['earth_arrival_time'] = _when(d.get('earth_arrival_time') or None)
                cmes.append(_checked(cme_module.CMEEvent(**d)))
            return cmes
        except Exception as e:
            print(f"Error loading CMEs cache: {e}")
            return None

    def save_cdaw_cmes(self, event_date: date, cmes: list):
        """Save the CDAW stand-in for the CMEs, apart from DONKI's: a date
        lacking ``cmes.json`` is one DONKI is still asked about."""
        path = self._get_cache_path(event_date, "cmes_cdaw.json")
        _write_json(path, [_serialize_obj(c) for c in cmes])

    def load_cdaw_cmes(self, event_date: date) -> Optional[list]:
        return self.load_cmes(event_date, "cmes_cdaw.json")

    # === GOES 1-minute flux ===
    def save_goes_day(self, event_date: date, flux: list):
        path = self._get_cache_path(event_date, "goes_1m.json")
        _write_json(path, flux)

    def goes_day_path(self, event_date: date) -> Path:
        return self.cache_dir / event_date.strftime("%Y/%m/%d") / "goes_1m.json"

    def load_goes_day(self, event_date: date) -> Optional[list]:
        path = self._get_cache_path(event_date, "goes_1m.json")
        if not _there(path):
            return None
        try:
            with open(path, "r") as f:
                flux = json.load(f)
        except Exception as e:
            logger.warning("Error loading GOES cache: %s", e)
            return None
        if not _numbers(flux) or any(value is not None and value < 0 for value in flux):
            logger.warning("The GOES cache of %s is not a curve", event_date)
            return None
        return flux

    # === e-CALLISTO Bursts ===
    def save_ecallisto_bursts(self, event_date: date, bursts: list):
        """Save e-CALLISTO bursts to cache."""
        path = self._get_cache_path(event_date, "ecallisto_bursts.json")
        _write_json(path, [_serialize_obj(b) for b in bursts])

    def load_ecallisto_bursts(self, event_date: date) -> Optional[list]:
        """Load e-CALLISTO bursts from cache and reconstruct ECallistoBurst objects."""
        path = self._get_cache_path(event_date, "ecallisto_bursts.json")
        if not _there(path):
            return None
        try:
            with open(path, "r") as f:
                data = json.load(f)
            return [_checked(ne.ECallistoBurst(**d)) for d in _entries(data)]
        except Exception as e:
            print(f"Error loading e-CALLISTO bursts cache: {e}")
            return None

    # === Context Images ===
    def save_images(self, event_date: date, images: list):
        """Save context image metadata to cache."""
        path = self._get_cache_path(event_date, "images.json")
        _write_json(path, [_serialize_obj(img) for img in images])
    
    def load_images(self, event_date: date) -> Optional[list]:
        """Load context image metadata from cache and reconstruct ContextImage objects."""
        path = self._get_cache_path(event_date, "images.json")
        if not _there(path):
            return None
        try:
            with open(path, "r") as f:
                data = json.load(f)
            return [self._image_from(d) for d in _entries(data)]
        except Exception as e:
            print(f"Error loading images cache: {e}")
            return None

    @staticmethod
    def _image_from(d):
        """A ContextImage from what ``_serialize_obj`` made of it."""
        from ..solar_context import context_images as ci_module
        for field in ('image_date', 'image_end'):
            if isinstance(d.get(field), str):
                try:
                    d[field] = datetime.fromisoformat(d[field])
                except ValueError:
                    pass
        d['proved_day'] = _day_of(d.get('proved_day'))
        image = ci_module.ContextImage(**d)
        # (a time that cannot be read stays as text: ``image_moment`` proves nothing by it)
        _checked(dataclasses.replace(image, image_date=None, image_end=None))
        return image

    def save_radio_images(self, event_date: date, images: list, notes: dict):
        """What the radio archives had for a date, and what they said."""
        record = {
            "asked": date.today().isoformat(),
            "images": [_serialize_obj(image) for image in images],
            "notes": notes,
        }
        _write_json(self._get_cache_path(event_date, "radio_images.json"), record)

    def load_radio_images(self, event_date: date) -> Optional[dict]:
        path = self._get_cache_path(event_date, "radio_images.json")
        if not _there(path):
            return None
        try:
            with open(path, "r") as f:
                record = json.load(f)
            if not _good_radio_notes(record["notes"]) or not isinstance(record["asked"], str):
                raise ValueError("its notes cannot be read")
            record["images"] = [self._image_from(d) for d in _entries(record["images"])]
            return record
        except Exception as e:
            logger.warning("Error loading the radio images: %s", e)
            return None

    # === The SunDish index: one file, for all dates ===
    def save_sundish_index(self, rows: list, refused: int):
        record = {"fetched": datetime.now().isoformat(), "rows": rows, "refused": refused}
        _write_json(self.cache_dir / "sundish_index.json", record)

    def load_sundish_index(self) -> Optional[dict]:
        path = self.cache_dir / "sundish_index.json"
        if not _there(path):
            return None
        try:
            with open(path, "r") as f:
                record = json.load(f)
            from ..solar_context import radio_images as ri

            record["fetched"] = datetime.fromisoformat(record["fetched"])
            if not all(ri.good_index_row(row) for row in record["rows"]):
                raise ValueError("a row cannot be read")
            return record
        except Exception as e:
            logger.warning("Error loading the SunDish index: %s", e)
            return None
    
    # === Context Image Thumbnails ===
    def get_thumbnail_path(self, event_date: date, image_title: str) -> Path:
        """Get local path for a cached thumbnail."""
        safe_name = image_title.replace(" ", "_").replace("/", "_")
        if len(safe_name.encode("utf-8", "replace")) > LONGEST_TITLE:  # a server's title: a file's name has a limit
            safe_name = hashlib.sha1(safe_name.encode("utf-8", "replace")).hexdigest()
        return self._get_cache_path(event_date, f"thumb_{safe_name}.png")
    
    def save_thumbnail(self, event_date: date, image_title: str, data: bytes) -> Path:
        """Save a thumbnail image to cache."""
        path = self.get_thumbnail_path(event_date, image_title)
        _write_whole(path, data)
        return path
    
    def load_thumbnail(self, event_date: date, image_title: str) -> Optional[Path]:
        """Check if thumbnail is cached and return path."""
        path = self.get_thumbnail_path(event_date, image_title)
        return path if _there(path) else None
    
    # === Radio Spectra ===
    def get_spectra_path(self, event_date: date, source: str) -> Path:
        """Get the cache path for a spectra image."""
        ext = SPECTRA_SUFFIXES.get(source, "png")
        return self._get_cache_path(event_date, f"spectra_{source}.{ext}")
    
    def save_spectra(self, event_date: date, source: str, data: bytes) -> Path:
        """Save spectra image to cache."""
        path = self.get_spectra_path(event_date, source)
        _write_whole(path, data)
        return path
    
    def load_spectra(self, event_date: date, source: str) -> Optional[Path]:
        """Check if spectra is cached and return path."""
        path = self.get_spectra_path(event_date, source)
        return path if _there(path) else None

    def load_spectra_or_context(self, event_date: date, source: str) -> Optional[Path]:
        """The file a source's lane starts from: EOVSA's and OVRO-LWA's can be
        made from the composite alone."""
        path = self.load_spectra(event_date, source)
        if path is None and source in ("eovsa", "lwa"):
            return self.load_spectra(event_date, "context")
        return path

    def save_lwa_links(self, event_date: date, links: dict):
        """Where OVRO-LWA's movie and FITS of the day are, and the FITS's size."""
        _write_json(self._get_cache_path(event_date, "lwa_links.json"), links)

    def load_lwa_links(self, event_date: date) -> dict:
        path = self._get_cache_path(event_date, "lwa_links.json")
        if not _there(path):
            return {}
        try:
            with open(path, "r") as f:
                links = json.load(f)
        except Exception as e:
            logger.warning("Error loading OVRO-LWA links: %s", e)
            return {}
        if not isinstance(links, dict):
            return {}
        # Each stands alone: what is not as it should be is asked for again
        kept = {}
        if isinstance(links.get("movie"), str):
            kept["movie"] = links["movie"]
        if isinstance(links.get("fits"), str) and _fits(links.get("fits_bytes"), int) and links["fits_bytes"] > 0:
            kept.update(fits=links["fits"], fits_bytes=links["fits_bytes"])
        return kept

    def save_no_file_answers(self, event_date: date, sources: list):
        """The spectra sources that said they have no file for this date."""
        record = {"asked": date.today().isoformat(), "no_file": sources}
        _write_json(self._get_cache_path(event_date, "spectra_answers.json"), record)

    def load_no_file_answers(self, event_date: date, settle_days: int) -> list:
        """What ``save_no_file_answers`` kept, while it can stand: an old date,
        or a recent one that was asked today."""
        path = self.cache_dir / event_date.strftime("%Y/%m/%d") / "spectra_answers.json"
        if not _there(path):
            return []
        try:
            with open(path, "r") as f:
                record = json.load(f)
        except Exception as e:
            logger.warning("Error loading the spectra answers: %s", e)
            return []
        if not _has_keys(record, ("asked", "no_file")) or not _fits(record["no_file"], typing.List[str]):
            logger.warning("The spectra answers of %s cannot be read", event_date)
            return []
        old = (date.today() - event_date).days > settle_days
        return record["no_file"] if old or record["asked"] == date.today().isoformat() else []

    def save_archive_latest(self, event_date: date, latest: dict):
        """How far EOVSA and OVRO-LWA said they had got when ``event_date`` was asked."""
        _write_json(self._get_cache_path(event_date, "archive_latest.json"), latest)

    def load_archive_latest(self, event_date: date) -> dict:
        """``{source: date}``, of those dates that can be read."""
        path = self.cache_dir / event_date.strftime("%Y/%m/%d") / "archive_latest.json"
        if not _there(path):
            return {}
        try:
            with open(path, "r") as f:
                latest = json.load(f)
            return {key: date.fromisoformat(value) for key, value in latest.items()}
        except Exception as e:
            logger.warning("Error loading the archives' latest dates: %s", e)
            return {}

    def save_lwa_times(self, event_date: date, table: dict):
        """When OVRO-LWA's samples of the plot of ``event_date`` were taken."""
        _write_json(self._get_cache_path(event_date, "lwa_times.json"), table)

    def load_lwa_times(self, event_date: date) -> Optional[dict]:
        path = self.cache_dir / event_date.strftime("%Y/%m/%d") / "lwa_times.json"
        if not _there(path):
            return None
        try:
            with open(path, "r") as f:
                table = json.load(f)
        except Exception as e:
            logger.warning("Error loading OVRO-LWA's times: %s", e)
            return None
        if not _good_lwa_times(table):
            logger.warning("OVRO-LWA's times of %s cannot be read", event_date)
            return None
        return table

    # === OVRO-LWA's daily movie, for the Helioviewer browser ===
    def lwa_movie_path(self, event_date: date) -> Path:
        """Where the movie of ``event_date`` is kept, whole."""
        return self._get_cache_path(event_date, "lwa_movie.mp4")

    def lwa_movie_partial(self, event_date: date) -> Path:
        """The movie while it comes down: kept, so a cut download goes on."""
        return self._get_cache_path(event_date, "lwa_movie.mp4.part")

    def save_lwa_movie_record(self, event_date: date, record: dict):
        """What the movie on disk is: its address, the server's tags for it
        and, once proved, the time of each frame."""
        _write_json(self._get_cache_path(event_date, "lwa_movie.json"), record)

    def load_lwa_movie_record(self, event_date: date) -> Optional[dict]:
        path = self.cache_dir / event_date.strftime("%Y/%m/%d") / "lwa_movie.json"
        if not _there(path):
            return None
        try:
            with open(path, "r") as f:
                record = json.load(f)
        except Exception as e:
            logger.warning("Error loading OVRO-LWA's movie record: %s", e)
            return None
        if not _good_lwa_movie(record):
            logger.warning("OVRO-LWA's movie record of %s cannot be read", event_date)
            return None
        return record

    def save_lwa_partial_record(self, event_date: date, record: dict):
        """Which movie the partial file is part of, to go on with it."""
        _write_json(self._get_cache_path(event_date, "lwa_movie.part.json"), record)

    def load_lwa_partial_record(self, event_date: date) -> Optional[dict]:
        path = self.cache_dir / event_date.strftime("%Y/%m/%d") / "lwa_movie.part.json"
        if not _there(path):
            return None
        try:
            with open(path, "r") as f:
                record = json.load(f)
        except Exception as e:
            logger.info("Error loading OVRO-LWA's partial movie record: %s", e)
            return None
        if not (isinstance(record, dict) and all(isinstance(record.get(k), str) for k in ("url", "etag", "last_modified"))):
            return None
        return record

    def drop_lwa_partial_record(self, event_date: date):
        path = self.cache_dir / event_date.strftime("%Y/%m/%d") / "lwa_movie.part.json"
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            logger.info("Could not remove %s: %s", path, e)

    def cached_lwa_movie(self, event_date: date, url: str) -> Optional[Path]:
        """The movie on disk, if it is the one at ``url`` and whole."""
        record = self.load_lwa_movie_record(event_date)
        path = self.cache_dir / event_date.strftime("%Y/%m/%d") / "lwa_movie.mp4"
        try:
            whole = record is not None and record["url"] == url and path.stat().st_size == record["bytes"]
        except OSError:
            return None
        return path if whole else None

    # === A day's records: protons, indices, flare lists ===
    def save_day_record(self, event_date: date, key: str, record: dict):
        """Saved under the date it was asked for, with the day it was asked."""
        path = self._get_cache_path(event_date, f"record_{key}.json")
        _write_json(path, {"asked": date.today().isoformat(), "record": record})

    def load_day_record(self, event_date: date, key: str) -> Optional[dict]:
        """``{"asked", "record"}``, or None, as for a file that cannot be shown:
        then it is asked for again. A date stepped through leaves no folder."""
        from ..solar_context import day_records as dr

        path = self.cache_dir / event_date.strftime("%Y/%m/%d") / f"record_{key}.json"
        if not _there(path):
            return None
        try:
            with open(path, "r") as f:
                saved = json.load(f)
        except Exception as e:
            logger.warning("Error loading the %s record: %s", key, e)
            return None
        if not _has_keys(saved, ("asked", "record")) or not dr.well_formed(key, saved["record"]):
            logger.warning("The %s record of %s cannot be read", key, event_date)
            return None
        return saved

    # === Check what's cached ===
    def has_events(self, event_date: date) -> bool:
        """Whether ``event_date`` has been fetched; a date stepped through
        leaves no folder behind."""
        return _there(self.cache_dir / event_date.strftime("%Y/%m/%d") / "events.json")

    def get_cached_status(self, event_date: date) -> dict:
        """Check what data is cached for a date."""
        cache_dir = self.get_date_cache_dir(event_date)
        return {
            'events': _there(cache_dir / "events.json"),
            'active_regions': _there(cache_dir / "active_regions.json"),
            'conditions': _there(cache_dir / "conditions.json"),
            'cmes': _there(cache_dir / "cmes.json"),
            'images': _there(cache_dir / "images.json"),
            'spectra_wind': _there(self.get_spectra_path(event_date, 'wind')),
            'spectra_stereo_a': _there(self.get_spectra_path(event_date, 'stereo_a')),
            'spectra_stereo_b': _there(self.get_spectra_path(event_date, 'stereo_b')),
            'spectra_norp': _there(self.get_spectra_path(event_date, 'norp')),
            'spectra_orfees': _there(self.get_spectra_path(event_date, 'orfees')),
            'spectra_nda': _there(self.get_spectra_path(event_date, 'nda')),
            'spectra_yamagawa': _there(self.get_spectra_path(event_date, 'yamagawa')),
            'spectra_hiras': _there(self.get_spectra_path(event_date, 'hiras')),
            'spectra_eovsa': _there(self.get_spectra_path(event_date, 'eovsa')),
            'spectra_lwa': _there(self.get_spectra_path(event_date, 'lwa')),
            'spectra_context': _there(self.get_spectra_path(event_date, 'context')),
            'spectra_learmonth': _there(self.get_spectra_path(event_date, 'learmonth')),
            'ecallisto_bursts': _there(cache_dir / "ecallisto_bursts.json")
        }
    
    def has_any_spectra(self, event_date: date) -> bool:
        """Check if any radio spectra are cached for this date."""
        status = self.get_cached_status(event_date)
        return any([
            status.get('spectra_wind'),
            status.get('spectra_stereo_a'),
            status.get('spectra_stereo_b'),
            status.get('spectra_norp'),
            status.get('spectra_orfees'),
            status.get('spectra_nda'),
            status.get('spectra_yamagawa'),
            status.get('spectra_hiras'),
            status.get('spectra_eovsa'),
            status.get('spectra_lwa'),
            status.get('spectra_context'),
            status.get('spectra_learmonth'),
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

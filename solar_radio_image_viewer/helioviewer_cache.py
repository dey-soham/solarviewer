"""
The browser's disk cache of Helioviewer frames: each rendered PNG under its
observed time and the geometry it was asked at, with a JSON sidecar that
records where it came from. A frame downloaded once is never fetched again;
the cache is trimmed to ``CACHE_BYTES`` oldest-first, on a thread.

Where: ``<root>/<source ids>/<observed %Y%m%dT%H%M%S>_<x0>_<y0>_<fov>_<w>.png``,
``root`` being ``$XDG_CACHE_HOME/solarviewer/helioviewer/frames`` unless the
cache is made with another (tests give it a folder of their own). Nothing
here touches Qt, so it all runs on any thread.
"""

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from . import helioviewer_api as hv
from .noaa_events.cache import _write_whole, keep
from .version import __version__

logger = logging.getLogger(__name__)

CACHE_BYTES = 2 * 1024 ** 3  # the frames kept, at most
STAMP = "%Y%m%dT%H%M%S"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
STALE_PART_S = 24 * 3600  # a partial file this old was left by a run that ended badly
LAYER = re.compile(r"\[([^\]]*)\]")


def default_root() -> Path:
    """Where the frames go unless a cache is given another folder."""
    return hv.cache_root() / "frames"


def _num(value) -> str:
    """A number for a file name: no exponent, no trailing zeros."""
    text = f"{float(value):.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def layer_ids(layers: str) -> List[int]:
    """The source ids of a layer string, ``[10,1,100],[4,1,60]`` → ``[10, 4]``."""
    ids = []
    for inside in LAYER.findall(str(layers)):
        first = inside.split(",")[0].strip()
        try:
            ids.append(int(first))
        except ValueError:
            continue
    return ids


def layer_tag(layers: str) -> Tuple[str, str]:
    """``(folder, suffix)`` of a layer string: the ids joined by ``+`` and,
    only when a layer is not shown whole, its visibility and opacity."""
    folder = "+".join(str(i) for i in layer_ids(layers)) or "unknown"
    extra = []
    for inside in LAYER.findall(str(layers)):
        parts = [p.strip() for p in inside.split(",")]
        if len(parts) >= 3 and (parts[-2], parts[-1]) != ("1", "100"):
            extra.append(f"{parts[-2]}-{parts[-1]}")
        else:
            extra.append("")
    suffix = "_op" + "_".join(extra) if any(extra) else ""
    return folder, suffix


def frame_name(frame) -> str:
    """The relative path of ``frame``'s file, from its observed time and geometry."""
    folder, suffix = layer_tag(frame.layers)
    size = str(int(frame.width))
    if int(frame.height) != int(frame.width):
        size += f"x{int(frame.height)}"
    stamp = frame.observed.strftime(STAMP)
    return f"{folder}/{stamp}_{_num(frame.x0)}_{_num(frame.y0)}_{_num(frame.image_scale * frame.width)}_{size}{suffix}.png"


class FrameCache:
    """The frames on disk under ``root``, at most ``limit`` bytes of them."""

    def __init__(self, root: Optional[Path] = None, limit: int = CACHE_BYTES):
        self.root = Path(root) if root is not None else default_root()
        self.limit = int(limit)
        self._trimmer: Optional[threading.Thread] = None

    # ------------------------------------------------------------ where

    def path(self, frame) -> Path:
        return self.root / frame_name(frame)

    @staticmethod
    def sidecar_of(path: Path) -> Path:
        return path.with_suffix(".json")

    def sidecar(self, frame) -> Path:
        return self.sidecar_of(self.path(frame))

    def find(self, frame) -> Optional[Path]:
        """The frame's file if it is here (and marked as used now), else None."""
        path = self.path(frame)
        try:
            if not path.is_file() or path.stat().st_size == 0:
                return None
            os.utime(path, None)  # used now: the trim takes the oldest first
        except OSError as e:
            logger.info("Could not read the cache at %s: %s", path, e)
            return None
        return path

    def has(self, frame) -> bool:
        return self.find(frame) is not None

    # ------------------------------------------------------------ writing

    def put(self, frame, data: bytes) -> Optional[Path]:
        """``data`` (a PNG) as the frame's file, whole or not at all, with its
        sidecar; the path, or None when the disk takes no more (the frame is
        still shown from memory)."""
        if not data or not data.startswith(PNG_MAGIC):
            raise ValueError("Not a PNG: the cache keeps only images")
        path = self.path(frame)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("Could not make the cache folder %s: %s", path.parent, e)
            return None
        if not keep(_write_whole, path, data):
            return None
        keep(_write_whole, self.sidecar_of(path), _json_bytes(self.record(frame, len(data))))
        return path

    def record(self, frame, size: Optional[int] = None) -> dict:
        """What the sidecar says of ``frame``."""
        record = dict(frame.provenance())
        record["cached"] = datetime.now(timezone.utc).replace(tzinfo=None).strftime(hv.HEADER_TIME) + "Z"
        record["app_version"] = __version__
        if size is not None:
            record["bytes"] = int(size)
        return record

    def read_record(self, frame) -> dict:
        """The sidecar of ``frame`` as it stands, or ``{}``."""
        try:
            return json.loads(self.sidecar(frame).read_text("utf-8"))
        except (OSError, ValueError):
            return {}

    def note_id(self, frame) -> bool:
        """The image id, learnt after the download, into the frame's sidecar."""
        if getattr(frame, "image_id", None) is None:
            return False
        path = self.sidecar(frame)
        record = self.read_record(frame)
        if not record and not self.path(frame).is_file():
            return False
        if record.get("image_id") == frame.image_id:
            return True
        record.update(image_id=frame.image_id)
        return keep(_write_whole, path, _json_bytes(record))

    # ------------------------------------------------------------ size, trimming

    def files(self) -> List[Path]:
        """The frames here, oldest use first."""
        if not self.root.exists():
            return []
        found = []
        for path in self.root.rglob("*.png"):
            try:
                found.append((path.stat().st_mtime, path))
            except OSError:
                continue
        found.sort()
        return [path for _, path in found]

    def size(self) -> Tuple[int, int]:
        """``(bytes, frames)`` on disk, sidecars counted in the bytes."""
        total = count = 0
        if not self.root.exists():
            return 0, 0
        for path in self.root.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
                    if path.suffix == ".png":
                        count += 1
            except OSError:
                continue
        return total, count

    def trim(self, limit: Optional[int] = None, check=None) -> int:
        """Drop the longest unused frames until the rest fit ``limit`` bytes,
        and partial files left by an earlier run; the bytes freed. ``check``
        is called between files (raise to stop)."""
        limit = self.limit if limit is None else int(limit)
        freed = 0
        if not self.root.exists():
            return 0
        now = time.time()
        for part in self.root.rglob(".*.part"):
            try:
                if now - part.stat().st_mtime > STALE_PART_S:
                    freed += part.stat().st_size
                    part.unlink()
            except OSError:
                continue
        sizes = {}
        for path in self.root.rglob("*"):
            try:
                if path.is_file():
                    sizes[path] = path.stat().st_size
            except OSError:
                continue
        total = sum(sizes.values())
        if total <= limit:
            return freed
        for path in self.files():
            if total <= limit:
                break
            if check:
                check()
            for victim in (path, self.sidecar_of(path)):
                size = sizes.get(victim, 0)
                try:
                    victim.unlink()
                except OSError as e:
                    if victim.exists():
                        logger.info("Could not remove %s: %s", victim, e)
                    continue
                total -= size
                freed += size
        if freed:
            logger.info("Helioviewer cache trimmed by %.1f MB", freed / 1024 ** 2)
        return freed

    def trim_in_background(self) -> Optional[threading.Thread]:
        """``trim()`` on a daemon thread (one at a time); the thread, to join in tests."""
        if self._trimmer is not None and self._trimmer.is_alive():
            return self._trimmer

        def run():
            try:
                self.trim()
            except Exception as e:
                logger.warning("Could not trim the Helioviewer cache: %s", e)

        self._trimmer = threading.Thread(target=run, name="helioviewer-cache-trim", daemon=True)
        self._trimmer.start()
        return self._trimmer

    def clear(self) -> int:
        """Every frame and sidecar gone; how many files went."""
        gone = 0
        if not self.root.exists():
            return 0
        for path in sorted(self.root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            try:
                if path.is_file():
                    path.unlink()
                    gone += 1
                elif path.is_dir():
                    path.rmdir()
            except OSError as e:
                logger.info("Could not remove %s: %s", path, e)
        return gone


def _json_bytes(record: dict) -> bytes:
    return json.dumps(record, indent=1, default=str).encode("utf-8")

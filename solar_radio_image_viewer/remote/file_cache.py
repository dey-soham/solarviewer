"""
Remote File Cache for SolarViewer.

Manages local caching of files downloaded from remote servers.
Cache is stored in ~/.cache/solarviewer/remote/<host>/<path>
"""

import os
import json
import hashlib
import time
from pathlib import Path
from typing import Optional, Dict, Tuple
from dataclasses import dataclass, asdict


@dataclass
class CacheEntry:
    """Metadata for a cached file."""

    remote_path: str
    local_path: str
    remote_host: str
    remote_mtime: float
    remote_size: int
    cached_time: float

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CacheEntry":
        return cls(**data)


class RemoteFileCache:
    """
    Manages local caching of remote files.

    Files are cached in ~/.cache/solarviewer/remote/<host>/<path>
    A metadata file tracks remote file info to detect changes.

    Usage:
        cache = RemoteFileCache()

        # Check if file is cached and still valid
        local_path = cache.get_cached_path(host, remote_path, remote_mtime, remote_size)

        if local_path is None:
            # Need to download
            local_path = cache.get_cache_path(host, remote_path)
            ssh_conn.download_file(remote_path, local_path)
            cache.mark_cached(host, remote_path, local_path, remote_mtime, remote_size)
    """

    DEFAULT_CACHE_DIR = Path.home() / ".cache" / "solarviewer" / "remote"
    METADATA_FILE = "cache_metadata.json"

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or self.DEFAULT_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._metadata: Dict[str, CacheEntry] = {}
        self._load_metadata()

    def _get_metadata_path(self) -> Path:
        return self.cache_dir / self.METADATA_FILE

    def _load_metadata(self):
        """Load cache metadata from disk."""
        meta_path = self._get_metadata_path()
        if meta_path.exists():
            try:
                with open(meta_path, "r") as f:
                    data = json.load(f)
                self._metadata = {k: CacheEntry.from_dict(v) for k, v in data.items()}
            except (json.JSONDecodeError, KeyError, TypeError):
                # Corrupted metadata, start fresh
                self._metadata = {}

    def _save_metadata(self):
        """Save cache metadata to disk."""
        meta_path = self._get_metadata_path()
        data = {k: v.to_dict() for k, v in self._metadata.items()}
        with open(meta_path, "w") as f:
            json.dump(data, f, indent=2)

    def _make_cache_key(self, host: str, remote_path: str) -> str:
        """Generate a unique key for a cached file."""
        return f"{host}:{remote_path}"

    def _sanitize_path(self, path: str) -> str:
        """Convert a remote path to a safe local path component."""
        # Remove leading slashes and replace problematic characters
        safe = path.lstrip("/").replace(":", "_")
        return safe

    def get_cache_path(self, host: str, remote_path: str) -> Path:
        """
        Get the local cache path for a remote file.

        This doesn't check if the file exists or is valid,
        just returns where it should be stored.
        """
        safe_host = host.replace(":", "_").replace("@", "_at_")
        safe_path = self._sanitize_path(remote_path)
        return self.cache_dir / safe_host / safe_path

    def get_cached_path(
        self,
        host: str,
        remote_path: str,
        remote_mtime: float,
        remote_size: int,
    ) -> Optional[Path]:
        """
        Get the cached local path if file is cached and still valid.

        Returns None if:
        - File is not cached
        - Cached file doesn't exist on disk
        - Remote file has been modified (different mtime or size)
        """
        key = self._make_cache_key(host, remote_path)

        if key not in self._metadata:
            return None

        entry = self._metadata[key]
        local_path = Path(entry.local_path)

        # Check if local file still exists
        if not local_path.exists():
            del self._metadata[key]
            self._save_metadata()
            return None

        # Check if remote file has changed
        if entry.remote_mtime != remote_mtime or entry.remote_size != remote_size:
            # Remote file changed, invalidate cache
            try:
                local_path.unlink()
            except:
                pass
            del self._metadata[key]
            self._save_metadata()
            return None

        return local_path

    def mark_cached(
        self,
        host: str,
        remote_path: str,
        local_path: Path,
        remote_mtime: float,
        remote_size: int,
    ):
        """Mark a file as cached after downloading."""
        key = self._make_cache_key(host, remote_path)
        self._metadata[key] = CacheEntry(
            remote_path=remote_path,
            local_path=str(local_path),
            remote_host=host,
            remote_mtime=remote_mtime,
            remote_size=remote_size,
            cached_time=time.time(),
        )
        self._save_metadata()

    def _get_directory_size(self, path: Path) -> int:
        """Calculate total size of a directory recursively."""
        total = 0
        try:
            for entry in os.scandir(path):
                # Don't follow symlinks
                if entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
                elif entry.is_dir(follow_symlinks=False):
                    total += self._get_directory_size(Path(entry.path))
        except Exception:
            pass
        return total

    def get_cache_size(self) -> Tuple[int, int]:
        """
        Get total cache size and number of files.

        Returns:
            (total_bytes, file_count)
        """
        total_size = 0
        file_count = 0

        for entry in self._metadata.values():
            local_path = Path(entry.local_path)
            if local_path.exists():
                try:
                    if local_path.is_dir():
                        total_size += self._get_directory_size(local_path)
                    else:
                        total_size += local_path.stat().st_size
                    file_count += 1
                except:
                    pass

        return total_size, file_count

    def clear_cache(self, host: Optional[str] = None):
        """
        Clear cached files.

        Args:
            host: If specified, only clear cache for this host.
                  If None, clear all cached files.
        """
        import shutil

        # SAFETY CHECK: Ensure we are deleting a safe directory
        # We only want to delete inside ~/.cache/solarviewer/remote or similar
        resolved_path = self.cache_dir.resolve()
        safe_path_indicator = "solarviewer"

        if (
            resolved_path == Path.home().resolve()
            or resolved_path == Path("/").resolve()
        ):
            print(
                f"SAFETY ERROR: Refusing to clear cache at dangerous path: {resolved_path}"
            )
            return

        if safe_path_indicator not in str(resolved_path) and ".cache" not in str(
            resolved_path
        ):
            print(
                f"SAFETY ERROR: Refusing to clear cache at suspicious path (missing 'solarviewer' or '.cache'): {resolved_path}"
            )
            return

        if host is None:
            # Clear everything
            keys_to_remove = list(self._metadata.keys())

            # Delete physical files recursively
            try:
                if not self.cache_dir.exists():
                    return

                # Iterate over subdirectories in cache root
                for item in self.cache_dir.iterdir():
                    # Double check item is inside cache_dir
                    if item.parent.resolve() != resolved_path:
                        continue

                    if item.is_dir():
                        shutil.rmtree(item)
                    elif item.name != self.METADATA_FILE:
                        item.unlink()
            except Exception as e:
                print(f"Error clearing cache directory: {e}")

        else:
            # Clear specific host only
            safe_host = host.replace(":", "_").replace("@", "_at_")
            host_dir = self.cache_dir / safe_host

            if host_dir.exists() and host_dir.parent.resolve() == resolved_path:
                try:
                    shutil.rmtree(host_dir)
                except Exception as e:
                    print(f"Error clearing host cache {host}: {e}")

            keys_to_remove = [
                k for k, v in self._metadata.items() if v.remote_host == host
            ]

        # Update metadata
        for key in keys_to_remove:
            del self._metadata[key]

        self._save_metadata()

    def get_cached_files_for_host(self, host: str) -> list:
        """Get list of cached files for a specific host."""
        result = []
        for entry in self._metadata.values():
            if entry.remote_host == host:
                local_path = Path(entry.local_path)
                if local_path.exists():
                    result.append(entry)
        return result

#!/usr/bin/env python3
"""
Rate Limiting and Caching utilities for HTTP requests.

Prevents IP blocking by enforcing delays between requests and caching responses.
"""

import time
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import requests
from threading import Lock


class RateLimitedSession:
    """
    HTTP session with automatic rate limiting per domain.

    Enforces minimum delay between consecutive requests to the same domain
    to prevent triggering anti-bot protection.
    """

    def __init__(self, min_delay: float = 0.5):
        """
        Initialize rate-limited session.

        Args:
            min_delay: Minimum seconds between requests to same domain (default 0.5)
        """
        self.session = requests.Session()
        self.min_delay = min_delay
        self.last_request_time: Dict[str, float] = {}
        self.lock = Lock()

        # Set a browser-like User-Agent to avoid simple bot detection
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
        )

    def _get_domain(self, url: str) -> str:
        """Extract domain from URL."""
        from urllib.parse import urlparse

        return urlparse(url).netloc

    def _enforce_rate_limit(self, domain: str):
        """Enforce minimum delay since last request to this domain."""
        with self.lock:
            if domain in self.last_request_time:
                elapsed = time.time() - self.last_request_time[domain]
                if elapsed < self.min_delay:
                    sleep_time = self.min_delay - elapsed
                    time.sleep(sleep_time)

            self.last_request_time[domain] = time.time()

    def get(self, url: str, **kwargs) -> requests.Response:
        """
        Perform GET request with automatic rate limiting.

        Args:
            url: URL to fetch
            **kwargs: Additional arguments passed to requests.get()

        Returns:
            Response object

        Raises:
            requests.exceptions.RequestException: On HTTP errors
        """
        domain = self._get_domain(url)
        self._enforce_rate_limit(domain)

        # Set default timeout if not provided
        if "timeout" not in kwargs:
            kwargs["timeout"] = 30

        response = self.session.get(url, **kwargs)

        # Handle rate limiting responses
        if response.status_code == 429:
            raise requests.exceptions.HTTPError(
                "Rate limit exceeded. Please wait before making more requests.",
                response=response,
            )
        elif response.status_code == 403:
            # Could be IP block or other forbidden access
            raise requests.exceptions.HTTPError(
                "Access forbidden. Your IP may be temporarily blocked. Please try again later.",
                response=response,
            )

        response.raise_for_status()
        return response


class CachedSession(RateLimitedSession):
    """
    Rate-limited session with file-based response caching.

    Caches successful responses to disk with configurable TTL.
    Prevents redundant requests and speeds up repeated fetches.
    """

    def __init__(
        self,
        min_delay: float = 0.5,
        cache_ttl_hours: int = 24,
        cache_dir: Optional[Path] = None,
    ):
        """
        Initialize cached session.

        Args:
            min_delay: Minimum seconds between requests to same domain
            cache_ttl_hours: How long to keep cached responses (default 24 hours)
            cache_dir: Directory for cache storage (default ~/.cache/solarviewer/)
        """
        super().__init__(min_delay)
        self.cache_ttl = timedelta(hours=cache_ttl_hours)

        if cache_dir is None:
            cache_dir = Path.home() / ".cache" / "solarviewer"

        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_cache_key(self, url: str) -> str:
        """Generate cache key from URL."""
        return hashlib.md5(url.encode()).hexdigest()

    def _get_cache_path(self, cache_key: str) -> Path:
        """Get path to cache file for given key."""
        return self.cache_dir / f"{cache_key}.json"

    def _is_cache_valid(self, cache_path: Path) -> bool:
        """Check if cached response is still valid (not expired)."""
        if not cache_path.exists():
            return False

        try:
            with open(cache_path, "r") as f:
                cached = json.load(f)

            cached_time = datetime.fromisoformat(cached["timestamp"])
            age = datetime.now() - cached_time

            return age < self.cache_ttl
        except (json.JSONDecodeError, KeyError, ValueError):
            return False

    def _load_from_cache(self, cache_path: Path) -> Optional[Dict[str, Any]]:
        """Load cached response if valid."""
        if not self._is_cache_valid(cache_path):
            return None

        try:
            with open(cache_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

    def _save_to_cache(self, cache_path: Path, url: str, response: requests.Response):
        """Save response to cache."""
        try:
            cache_data = {
                "timestamp": datetime.now().isoformat(),
                "url": url,
                "status_code": response.status_code,
                "content": response.text,
                "headers": dict(response.headers),
            }

            with open(cache_path, "w") as f:
                json.dump(cache_data, f)
        except (IOError, TypeError):
            # Silently fail if caching doesn't work
            pass

    def get(self, url: str, use_cache: bool = True, **kwargs) -> requests.Response:
        """
        Perform GET request with caching.

        Args:
            url: URL to fetch
            use_cache: Whether to use cached response if available
            **kwargs: Additional arguments passed to requests.get()

        Returns:
            Response object (either from cache or fresh request)
        """
        cache_key = self._get_cache_key(url)
        cache_path = self._get_cache_path(cache_key)

        # Try to load from cache
        if use_cache:
            cached = self._load_from_cache(cache_path)
            if cached:
                # Create a mock response object from cached data
                response = requests.Response()
                response.status_code = cached["status_code"]
                response._content = cached["content"].encode()
                response.headers.update(cached["headers"])
                response.url = url
                response.from_cache = True  # Custom attribute to indicate cache hit
                return response

        # Not in cache or cache disabled, fetch fresh
        response = super().get(url, **kwargs)

        # Cache successful responses
        if response.status_code == 200:
            self._save_to_cache(cache_path, url, response)

        response.from_cache = False
        return response

    def clear_cache(self):
        """Remove all cached responses."""
        for cache_file in self.cache_dir.glob("*.json"):
            try:
                cache_file.unlink()
            except OSError:
                pass

    def cleanup_expired_cache(self):
        """Remove expired cache entries."""
        for cache_file in self.cache_dir.glob("*.json"):
            if not self._is_cache_valid(cache_file):
                try:
                    cache_file.unlink()
                except OSError:
                    pass


# Global cached session instance for reuse across modules
_global_session: Optional[CachedSession] = None


def get_global_session(
    min_delay: float = 0.5, cache_ttl_hours: int = 24
) -> CachedSession:
    """
    Get or create global cached session instance.

    This allows multiple modules to share the same session and benefit from
    shared rate limiting and caching.

    Args:
        min_delay: Minimum seconds between requests
        cache_ttl_hours: Cache validity period

    Returns:
        Global CachedSession instance
    """
    global _global_session
    if _global_session is None:
        _global_session = CachedSession(
            min_delay=min_delay, cache_ttl_hours=cache_ttl_hours
        )
    return _global_session

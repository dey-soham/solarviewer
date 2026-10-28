"""
Astropy's leap-second list and Earth-rotation table, never fetched while
converting a time.

Astropy needs both: the leap seconds the first time a UTC time is converted
(drawing a solar image does), the IERS Earth-rotation table the first time a
place on Earth is (a radio telescope's, for helioprojective coordinates).
When it finds its copies old it fetches new ones from the internet there and
then, on the thread converting: the window's. Over a slow or stalled network
(or from a server that answers over IPv4 only, tried after IPv6 times out)
the window stood still for up to a minute.

So astropy here uses the copies it has and fetches nothing
(:func:`use_local_tables`, at start up in the window's process and in each
worker process), and :func:`refresh_in_background` fetches newer copies in a
thread when they are old. Those go into astropy's cache at the next start:
replacing a cached file while another thread may be reading it could make
astropy look for a mirror on the network.
"""

import logging
import os
import shutil
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Copies fetched for astropy's cache, put there at the next start
STAGING_DIR = Path.home() / ".cache" / "solarviewer" / "astropy"
# When astropy would fetch: a leap-second list expiring within 150 days
# (180 days less auto_max_age), an Earth-rotation table over 30 days old
LEAP_SECONDS_MARGIN = timedelta(days=150)
EARTH_ROTATION_MAX_AGE_S = 30 * 86400
# A list fetched within this long is not fetched again (one is published
# every six months)
LEAP_SECONDS_RECHECK_S = 7 * 86400
TIMEOUT_S = 30  # (IPv6, tried first, may never answer)


def use_local_tables():
    """Have astropy use the leap-second list and Earth-rotation table it has,
    fetching neither. Call before any time is converted."""
    try:
        from astropy.time import Time
        from astropy.utils import iers
        from astropy.utils.data import is_url_in_cache
    except ImportError:
        return
    try:
        _import_staged(iers)
        # The Earth-rotation table fetched into astropy's cache, if there is
        # one, else the one bundled with astropy; its predictions are used
        # however old (astropy would fetch a new table, or refuse them)
        iers.conf.auto_download = any(is_url_in_cache(url) for url in _earth_rotation_urls(iers))
        iers.conf.auto_max_age = None
        # Astropy checks its leap-second list once, at the first UTC time
        # converted: now, from its copies and the system's
        with iers.conf.set_temp("auto_download", False):
            Time("2000-01-01", scale="utc").tai
    except Exception as e:
        logger.warning(f"Astropy may fetch its time tables while converting times: {e}")


def refresh_in_background():
    """:func:`refresh` in a thread (at start up)."""
    thread = threading.Thread(target=refresh, name="astropy-tables", daemon=True)
    thread.start()
    return thread


def refresh():
    """Fetch new copies of the tables astropy would find old, for the next
    start. Call after :func:`use_local_tables`."""
    try:
        import erfa
        from astropy.utils import iers
        from astropy.utils.data import cache_contents
    except ImportError:
        return
    try:
        cached = cache_contents()
        leap_urls = _leap_second_urls(iers)
        expires = erfa.leap_seconds.expires  # (of the list in use)
        if expires < datetime.now() + LEAP_SECONDS_MARGIN and _age(cached, leap_urls) > LEAP_SECONDS_RECHECK_S:
            _fetch(leap_urls, lambda path: iers.LeapSeconds.open(path))
        earth_urls = _earth_rotation_urls(iers)
        if _age(cached, earth_urls) > EARTH_ROTATION_MAX_AGE_S:
            _fetch(earth_urls, lambda path: iers.IERS_A.read(path))
    except Exception as e:
        logger.info(f"Astropy's time tables were not refreshed: {e}")


def _leap_second_urls(iers):
    return [url for url in (iers.conf.iers_leap_second_auto_url, iers.conf.ietf_leap_second_auto_url) if url]


def _earth_rotation_urls(iers):
    return [url for url in (iers.conf.iers_auto_url, iers.conf.iers_auto_url_mirror) if url]


def _staged(url):
    return STAGING_DIR / url.rstrip("/").rsplit("/", 1)[-1]


def _age(cached, urls):
    """Seconds since the newest copy of ``urls`` in astropy's cache was
    fetched (inf if none)."""
    times = [os.path.getmtime(cached[url]) for url in urls if url in cached]
    return time.time() - max(times) if times else float("inf")


def _fetch(urls, check):
    """Fetch the table from the first of ``urls`` that gives one ``check``
    can read, and keep it for astropy's cache under the first URL (as
    astropy keeps a mirror's copy)."""
    from astropy.utils.data import download_file

    for url in urls:
        try:
            path = download_file(url, cache=False, timeout=TIMEOUT_S, show_progress=False)
        except Exception as e:
            logger.info(f"Could not fetch {url}: {e}")
            continue
        try:
            check(path)
            STAGING_DIR.mkdir(parents=True, exist_ok=True)
            staged = _staged(urls[0])
            part = staged.with_name(staged.name + ".part")
            shutil.copyfile(path, part)
            os.replace(part, staged)
            return True
        except Exception as e:
            logger.info(f"{url} gave no readable table: {e}")
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
    return False


def _import_staged(iers):
    """Put the copies fetched in an earlier session into astropy's cache."""
    from astropy.utils.data import import_file_to_cache

    for url in (_leap_second_urls(iers)[:1] + _earth_rotation_urls(iers)[:1]):
        staged = _staged(url)
        if staged.exists():
            try:
                import_file_to_cache(url, str(staged), remove_original=True)
            except Exception as e:
                logger.info(f"Could not give astropy {staged.name}: {e}")

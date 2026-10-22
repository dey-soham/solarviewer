"""
Background threads of the solar events viewer: fetching the day's data,
resolving and loading images, the GOES X-ray plot and the radio dynamic
spectra.
"""

import logging
from datetime import datetime, date
from typing import Optional
from PyQt5.QtCore import QThread, pyqtSignal
from . import noaa_events as ne

from .cache import _noaa_cache
logger = logging.getLogger(__name__)


class ImageUrlResolver(QThread):
    found = pyqtSignal(str)

    def __init__(self, page_url):
        super().__init__()
        self.url = page_url

    def run(self):
        from ..solar_context import context_images as ci

        url = ci.resolve_full_image_url(self.url)
        self.found.emit(url)


class ImageLoader(QThread):
    """
    Thread to download image data.
    If page_url is provided, it tries to resolve the High-Res image first.
    Otherwise (or if resolve fails), it falls back to the direct url (thumbnail).
    """

    loaded = pyqtSignal(bytes)
    error = pyqtSignal(str)

    def __init__(self, url, page_url=None):
        super().__init__()
        self.url = url
        self.page_url = page_url

    def run(self):
        try:
            target_url = self.url
            # Try to resolve high-res if page_url available
            # BUT: Skip for Helioviewer URLs (they're already direct image URLs)
            if self.page_url and "helioviewer.org" not in self.page_url:
                try:
                    from ..solar_context import context_images as ci

                    resolved = ci.resolve_full_image_url(self.page_url)
                    if resolved:
                        target_url = resolved
                except Exception as e:
                    print(f"Failed to resolve high-res: {e}")

            import requests

            response = requests.get(
                target_url, timeout=60, headers={"User-Agent": "Mozilla/5.0"}
            )
            if response.status_code == 200:
                self.loaded.emit(response.content)
            else:
                self.error.emit(f"HTTP {response.status_code}")
        except Exception as e:
            self.error.emit(str(e))


class FetchWorker(QThread):
    """Worker thread for fetching events, active regions, conditions, CMEs, and images.
    
    Uses cache when available - only fetches missing data from network.
    """

    finished = pyqtSignal(
        object, object, object, object, object, object
    )  # (events, active_regions, conditions, cmes, images, ecallisto_bursts) tuple
    error = pyqtSignal(str)
    progress = pyqtSignal(str)  # Progress messages

    def __init__(self, event_date: date, force_refresh: bool = False):
        super().__init__()
        self.event_date = event_date
        self.force_refresh = force_refresh

    def run(self):
        try:
            cache = _noaa_cache
            
            # Check cache status
            cached_status = cache.get_cached_status(self.event_date)
            
            # === Events ===
            events = None
            if not self.force_refresh and cached_status['events']:
                self.progress.emit("Loading events from cache...")
                events = cache.load_events(self.event_date)
            
            if events is None:
                self.progress.emit("Fetching solar events...")
                events = ne.fetch_and_parse_events(self.event_date)
                if events:
                    cache.save_events(self.event_date, events)

            # === Active Regions ===
            active_regions = None
            if not self.force_refresh and cached_status['active_regions']:
                self.progress.emit("Loading active regions from cache...")
                active_regions = cache.load_active_regions(self.event_date)
            
            if active_regions is None:
                try:
                    self.progress.emit("Fetching active regions...")
                    from ..solar_context import active_regions as ar
                    active_regions = ar.fetch_and_parse_active_regions(self.event_date)
                    if active_regions:
                        cache.save_active_regions(self.event_date, active_regions)
                except Exception as ar_err:
                    print(f"Active regions fetch failed: {ar_err}")

            # === Conditions ===
            conditions = None
            if not self.force_refresh and cached_status['conditions']:
                self.progress.emit("Loading conditions from cache...")
                conditions = cache.load_conditions(self.event_date)
            
            if conditions is None:
                try:
                    self.progress.emit("Fetching solar conditions...")
                    from ..solar_context import realtime_data as rt
                    conditions = rt.fetch_conditions_for_date(self.event_date)
                    if conditions:
                        cache.save_conditions(self.event_date, conditions)
                except Exception as cond_err:
                    print(f"Solar conditions fetch failed: {cond_err}")

            # === CMEs ===
            cmes = None
            if not self.force_refresh and cached_status['cmes']:
                self.progress.emit("Loading CMEs from cache...")
                cmes = cache.load_cmes(self.event_date)
            
            if cmes is None:
                try:
                    self.progress.emit("Fetching CME alerts...")
                    from ..solar_context import cme_alerts as cme
                    cmes = cme.fetch_and_parse_cme_events(self.event_date)
                    if cmes:
                        cache.save_cmes(self.event_date, cmes)
                except Exception as cme_err:
                    print(f"CME fetch failed: {cme_err}")

            # === Context Images ===
            images = None
            if not self.force_refresh and cached_status['images']:
                self.progress.emit("Loading context images from cache...")
                images = cache.load_images(self.event_date)
                
                # Auto-heal cache for old images missing the timestamp
                if images and any(not hasattr(img, 'image_date') or img.image_date is None for img in images):
                    self.progress.emit("Stale image cache detected. Refreshing for timestamps...")
                    images = None
            
            if images is None:
                try:
                    self.progress.emit("Fetching context images...")
                    from ..solar_context import context_images as ci
                    images = ci.fetch_context_images(self.event_date)
                    if images:
                        cache.save_images(self.event_date, images)
                except Exception as img_err:
                    print(f"Context images fetch failed: {img_err}")
            
            if images is None:
                images = []

            # === e-CALLISTO Bursts ===
            ecallisto_bursts = None
            if not self.force_refresh and cached_status.get('ecallisto_bursts'):
                self.progress.emit("Loading e-CALLISTO bursts from cache...")
                ecallisto_bursts = cache.load_ecallisto_bursts(self.event_date)
            
            if ecallisto_bursts is None:
                try:
                    # Check if within supported range (2010, 2011, 2020+)
                    if self.event_date.year in [2010, 2011] or self.event_date.year >= 2020:
                        self.progress.emit("Fetching e-CALLISTO radio bursts...")
                        ecallisto_bursts = ne.fetch_and_parse_ecallisto_bursts(self.event_date)
                        if ecallisto_bursts:
                            cache.save_ecallisto_bursts(self.event_date, ecallisto_bursts)
                    else:
                        ecallisto_bursts = []
                except Exception as eb_err:
                    print(f"e-CALLISTO bursts fetch failed: {eb_err}")
                    ecallisto_bursts = []

            if ecallisto_bursts is None:
                ecallisto_bursts = []

            self.finished.emit(events, active_regions, conditions, cmes, images, ecallisto_bursts)
        except Exception as e:
            self.error.emit(str(e))


class GOESPlotWorker(QThread):
    """Worker thread for fetching and plotting GOES X-ray flux."""

    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, event_date: date):
        super().__init__()
        self.event_date = event_date

    def run(self):
        try:
            from sunpy.net import Fido, attrs as a
            from sunpy.timeseries import TimeSeries

            # Define time range for the full day
            t_start = datetime.combine(self.event_date, datetime.min.time())
            t_end = datetime.combine(self.event_date, datetime.max.time())

            # Search for GOES XRS data
            # Use a.Resolution.flx1s (1-second data) if possible, or avg1m (1-minute)
            res = Fido.search(a.Time(t_start, t_end), a.Instrument("GOES"))

            if len(res) == 0:
                raise Exception("No GOES X-ray data found for this date.")

            # Filter results to get the "best" single file
            # 1. Prefer High Cadence (flx1s) over Average (avg1m)

            # Simple conversion to astropy table to sort/filter
            # tbl = res[0]

            # Searching for 'flx1s' first
            res_high = Fido.search(
                a.Time(t_start, t_end), a.Instrument("GOES"), a.Resolution("flx1s")
            )

            if len(res_high) > 0:
                res = res_high
            else:
                pass  # Fallback to whatever we found (likely 1m)

            # If we still have multiple satellites (e.g. 16 and 18), pick one.
            # Converting to list of rows and picking the first one is safest to avoid downloading 4 files.

            # Slice the UnifiedResponse to keep only the first row of the first provider results
            best_result = res[0, 0]

            files = Fido.fetch(best_result)

            if not files:
                raise Exception("Failed to download GOES data file.")

            # Load TimeSeries
            ts = TimeSeries(files)

            # Concatenate if multiple files (though usually one per day/search)
            if isinstance(ts, list):
                if len(ts) > 1:
                    # TODO: Concatenate the TimeSeries objects
                    pass

            self.finished.emit(ts)

        except Exception as e:
            self.error.emit(str(e))


class DHSpectraWorker(QThread):
    """Worker thread for fetching DH-band dynamic spectra from CDAW NASA.
    
    Fetches WIND/WAVES (24h full-day, calibrated) and STEREO A/B (7h) spectrograms for the selected date.
    Uses unified cache system - checks cache first before downloading.
    """

    progress = pyqtSignal(str)
    result_ready = pyqtSignal(str, str)  # Emits (source_key, file_path)
    finished = pyqtSignal(dict, list)  # Emits (results_dict, error_list)
    # Removing error signal as we now accumulate errors in a list

    # CDAW server URLs
    # Primary: calibrated L2 daily spectrograms (well calibrated)
    WAVES_CALIBRATED_URL = 'https://cdaw.gsfc.nasa.gov/images/wind/waves/test_daily/'
    # Fallback: 24h full-day spectrograms
    WAVES_URL_24H = 'https://cdaw.gsfc.nasa.gov/images/wind/waves_h2M_24h/'
    SWAVES_URL = 'https://cdaw.gsfc.nasa.gov/images/stereo/swaves/'
    SWAVES_URL_NEW = 'https://cdaw.gsfc.nasa.gov/images/stereo/swaves_new/'

    def __init__(self, event_datetime: datetime, force_refresh: bool = False):
        super().__init__()
        self.event_datetime = event_datetime
        self.force_refresh = force_refresh

    def run(self):
        """Fetch spectra from CDAW servers, using cache when available."""
        import urllib.request
        import urllib.error
        import re
        
        try:
            cache = _noaa_cache
            event_date = self.event_datetime.date()
            results = {
                'wind': None, 'stereo_a': None, 'stereo_b': None, 
                'combined': None, 'norp': None, 'orfees': None, 
                'nda': None, 'yamagawa': None, 'hiras': None
            }
            errors = []
            dt_obj = self.event_datetime
            date_path = f"{dt_obj.year}/{dt_obj.month:02d}/{dt_obj.day:02d}"
            date_str = dt_obj.strftime('%Y%m%d')
            
            # STEREO URL based on date
            swaves_url = self.SWAVES_URL_NEW if dt_obj.year >= 2018 else self.SWAVES_URL
            
            # === WIND/WAVES ===
            # Check cache first
            cached_wind = cache.load_spectra(event_date, 'wind')
            if not self.force_refresh and cached_wind:
                self.progress.emit("Loading WIND/WAVES from cache...")
                results['wind'] = str(cached_wind)
                self.result_ready.emit('wind', str(cached_wind))
            else:
                # Try calibrated L2 source first (test_daily)
                wind_found = False
                self.progress.emit("Searching for calibrated WIND/WAVES data...")
                
                try:
                    # Calibrated source: test_daily/YYYY/YYYYMMDD_windwaves__scale_2to6sfu_l2_cdf_v1.jpg
                    calibrated_filename = f"{date_str}_windwaves__scale_2to6sfu_l2_cdf_v1.jpg"
                    calibrated_url = f"{self.WAVES_CALIBRATED_URL}{dt_obj.year}/{calibrated_filename}"
                    
                    # Try to fetch the calibrated file directly
                    req = urllib.request.urlopen(calibrated_url, timeout=15)
                    data = req.read()
                    wind_file = cache.save_spectra(event_date, 'wind', data)
                    results['wind'] = str(wind_file)
                    self.result_ready.emit('wind', str(wind_file))
                    wind_found = True
                    self.progress.emit("Calibrated WIND/WAVES data found!")
                    
                except Exception:
                    self.progress.emit("Calibrated source unavailable, trying fallback...")
                
                # Fallback to 24h source if calibrated not found
                if not wind_found:
                    try:
                        wind_dir_url = f"{self.WAVES_URL_24H}{date_path}/"
                        req = urllib.request.urlopen(wind_dir_url, timeout=15)
                        html = req.read().decode('utf-8')
                        
                        # Parse for 24h windwaves PNG files (prefer h2M over h2hk)
                        pattern = re.compile(r'href="([^"]*windwaves_h2M_sfu_24h\.png)"', re.IGNORECASE)
                        matches = pattern.findall(html)
                        
                        if not matches:
                            # Fallback to h2hk if h2M not available
                            pattern = re.compile(r'href="([^"]*windwaves_h2hk_sfu_24h\.png)"', re.IGNORECASE)
                            matches = pattern.findall(html)
                        
                        if matches:
                            # Get the latest 24h image (last one in the day)
                            best_match = self._find_latest_image(matches)
                            if best_match:
                                wind_url = f"{wind_dir_url}{best_match}"
                                req = urllib.request.urlopen(wind_url, timeout=30)
                                data = req.read()
                                wind_file = cache.save_spectra(event_date, 'wind', data)
                                results['wind'] = str(wind_file)
                                self.result_ready.emit('wind', str(wind_file))
                                wind_found = True
                                self.progress.emit("WIND/WAVES 24h data found!")
                        
                        if not wind_found:
                            self.progress.emit("No WIND/WAVES data for this date")
                    except Exception as e:
                        err_msg = f"WIND/WAVES: {str(e)[:50]}"
                        self.progress.emit(err_msg)
                        errors.append(err_msg)
            
            # Check if date is before STEREO mission
            if dt_obj.year < 2007:
                self.progress.emit("Date predates STEREO mission (launched Oct 2006)")
                self.finished.emit(results)
                return
            
            # === STEREO-A ===
            cached_sta = cache.load_spectra(event_date, 'stereo_a')
            if not self.force_refresh and cached_sta:
                self.progress.emit("Loading STEREO-A from cache...")
                results['stereo_a'] = str(cached_sta)
                self.result_ready.emit('stereo_a', str(cached_sta))
            else:
                self.progress.emit("Searching for STEREO-A data...")
                try:
                    stereo_dir_url = f"{swaves_url}{date_path}/"
                    req = urllib.request.urlopen(stereo_dir_url, timeout=15)
                    html = req.read().decode('utf-8')
                    
                    if swaves_url == self.SWAVES_URL:
                        # Old format (2007-2017): swaves2A (2 = 7h plot)
                        pattern_a = re.compile(r'href="([^"]*swaves2A\.png)"', re.IGNORECASE)
                    else:
                        # New format (2018+): sta_waves_7h (7-hour coverage)
                        pattern_a = re.compile(r'href="([^"]*sta_waves_7h[^"]*\.png)"', re.IGNORECASE)
                    
                    matches_a = sorted(pattern_a.findall(html))
                    
                    if matches_a:
                        self.progress.emit("Stitching STEREO-A images...")
                        stitched_data = self._stitch_images(stereo_dir_url, matches_a)
                        if stitched_data:
                            sta_file = cache.save_spectra(event_date, 'stereo_a', stitched_data)
                            results['stereo_a'] = str(sta_file)
                            self.result_ready.emit('stereo_a', str(sta_file))
                            self.progress.emit("STEREO-A stitched data saved!")
                        else:
                            # Fallback to single image if stitching fails
                            best_a = self._find_latest_image(matches_a)
                            if best_a:
                                sta_url = f"{stereo_dir_url}{best_a}"
                                req = urllib.request.urlopen(sta_url, timeout=30)
                                data = req.read()
                                sta_file = cache.save_spectra(event_date, 'stereo_a', data)
                                results['stereo_a'] = str(sta_file)
                                self.result_ready.emit('stereo_a', str(sta_file))
                                self.progress.emit("STEREO-A 7h data found (stitching failed)!")
                            
                except Exception as e:
                    err_msg = f"STEREO-A: {str(e)[:50]}"
                    self.progress.emit(err_msg)
                    errors.append(err_msg)
            
            # === STEREO-B (only for dates before 2014) ===
            if dt_obj.year <= 2014:
                cached_stb = cache.load_spectra(event_date, 'stereo_b')
                if not self.force_refresh and cached_stb:
                    self.progress.emit("Loading STEREO-B from cache...")
                    results['stereo_b'] = str(cached_stb)
                    self.result_ready.emit('stereo_b', str(cached_stb))
                else:
                    try:
                        stereo_dir_url = f"{swaves_url}{date_path}/"
                        req = urllib.request.urlopen(stereo_dir_url, timeout=15)
                        html = req.read().decode('utf-8')
                        
                        # Old format: swaves2B (2 = 7h plot)
                        pattern_b = re.compile(r'href="([^"]*swaves2B\.png)"', re.IGNORECASE)
                        matches_b = sorted(pattern_b.findall(html))
                        
                        if matches_b:
                            self.progress.emit("Stitching STEREO-B images...")
                            stitched_data = self._stitch_images(stereo_dir_url, matches_b)
                            if stitched_data:
                                stb_file = cache.save_spectra(event_date, 'stereo_b', stitched_data)
                                results['stereo_b'] = str(stb_file)
                                self.result_ready.emit('stereo_b', str(stb_file))
                                self.progress.emit("STEREO-B stitched data saved!")
                            else:
                                # Fallback to single image
                                best_b = self._find_latest_image(matches_b)
                                if best_b:
                                    stb_url = f"{stereo_dir_url}{best_b}"
                                    req = urllib.request.urlopen(stb_url, timeout=30)
                                    data = req.read()
                                    stb_file = cache.save_spectra(event_date, 'stereo_b', data)
                                    results['stereo_b'] = str(stb_file)
                                    self.result_ready.emit('stereo_b', str(stb_file))
                                    self.progress.emit("STEREO-B 7h data found (stitching failed)!")
                                
                    except Exception as e:
                        err_msg = f"STEREO-B: {str(e)[:50]}"
                        self.progress.emit(err_msg)
                        errors.append(err_msg)
            
            # === Nobeyama (NORP) ===
            cached_norp = cache.load_spectra(event_date, 'norp')
            if not self.force_refresh and cached_norp:
                self.progress.emit("Loading Nobeyama (NORP) from cache...")
                results['norp'] = str(cached_norp)
                self.result_ready.emit('norp', str(cached_norp))
            else:
                self.progress.emit("Searching for Nobeyama (NORP) data...")
                try:
                    # https://solar.nro.nao.ac.jp/norp/html/daily/YYYY/MM/plYYMMDD.png
                    year4 = dt_obj.strftime('%Y')
                    month = dt_obj.strftime('%m')
                    year2 = dt_obj.strftime('%y')
                    date_str2 = dt_obj.strftime('%y%m%d')
                    norp_url = f"https://solar.nro.nao.ac.jp/norp/html/daily/{year4}/{month}/pl{date_str2}.png"
                    
                    req = urllib.request.urlopen(norp_url, timeout=15)
                    data = req.read()
                    norp_file = cache.save_spectra(event_date, 'norp', data)
                    results['norp'] = str(norp_file)
                    self.result_ready.emit('norp', str(norp_file))
                    self.progress.emit("Nobeyama (NORP) data found!")
                except Exception as e:
                    err_msg = f"Nobeyama (NORP): {str(e)[:50]}"
                    self.progress.emit(err_msg)
                    errors.append(err_msg)

            # === ORFEES (Nancay) ===
            cached_orfees = cache.load_spectra(event_date, 'orfees')
            if not self.force_refresh and cached_orfees:
                self.progress.emit("Loading ORFEES from cache...")
                results['orfees'] = str(cached_orfees)
                self.result_ready.emit('orfees', str(cached_orfees))
            else:
                self.progress.emit("Searching for ORFEES data...")
                try:
                    # https://rsdb.obs-nancay.fr/QL/Orfees/orfeesYYYYMMDD.png
                    url = f"https://rsdb.obs-nancay.fr/QL/Orfees/orfees{event_date.strftime('%Y%m%d')}.png"
                    import ssl
                    context = ssl._create_unverified_context()
                    req = urllib.request.urlopen(url, timeout=15, context=context)
                    data = req.read()
                    orfees_file = cache.save_spectra(event_date, 'orfees', data)
                    results['orfees'] = str(orfees_file)
                    self.result_ready.emit('orfees', str(orfees_file))
                    self.progress.emit("ORFEES data found!")
                except Exception as e:
                    err_msg = f"ORFEES: {str(e)[:50]}"
                    self.progress.emit(err_msg)
                    errors.append(err_msg)

            # === NDA (Nancay) ===
            cached_nda = cache.load_spectra(event_date, 'nda')
            if not self.force_refresh and cached_nda:
                self.progress.emit("Loading NDA from cache...")
                results['nda'] = str(cached_nda)
                self.result_ready.emit('nda', str(cached_nda))
            else:
                self.progress.emit("Searching for NDA data...")
                try:
                    # https://rsdb.obs-nancay.fr/QL/Nda/NdaP1FYYYYMMDD.png
                    url = f"https://rsdb.obs-nancay.fr/QL/Nda/NdaP1F{event_date.strftime('%Y%m%d')}.png"
                    import ssl
                    context = ssl._create_unverified_context()
                    req = urllib.request.urlopen(url, timeout=15, context=context)
                    data = req.read()
                    nda_file = cache.save_spectra(event_date, 'nda', data)
                    results['nda'] = str(nda_file)
                    self.result_ready.emit('nda', str(nda_file))
                    self.progress.emit("NDA data found!")
                except Exception as e:
                    err_msg = f"NDA: {str(e)[:50]}"
                    self.progress.emit(err_msg)
                    errors.append(err_msg)
            # === NICT (Yamagawa or HiRAS) ===
            from datetime import date
            NICT_TRANSITION_DATE = date(2016, 7, 11)
            
            if event_date >= NICT_TRANSITION_DATE:
                # Yamagawa
                cached_yamagawa = cache.load_spectra(event_date, 'yamagawa')
                if not self.force_refresh and cached_yamagawa:
                    self.progress.emit("Loading Yamagawa from cache...")
                    results['yamagawa'] = str(cached_yamagawa)
                    self.result_ready.emit('yamagawa', str(cached_yamagawa))
                else:
                    self.progress.emit("Fetching and stitching Yamagawa data...")
                    try:
                        yamagawa_data = self._fetch_nict_spectra('yamagawa', event_date)
                        if yamagawa_data:
                            yamagawa_file = cache.save_spectra(event_date, 'yamagawa', yamagawa_data)
                            results['yamagawa'] = str(yamagawa_file)
                            self.result_ready.emit('yamagawa', str(yamagawa_file))
                            self.progress.emit("Yamagawa data stitched!")
                    except Exception as e:
                        err_msg = f"Yamagawa: {str(e)[:50]}"
                        self.progress.emit(err_msg)
                        errors.append(err_msg)
            else:
                # HiRAS
                cached_hiras = cache.load_spectra(event_date, 'hiras')
                if not self.force_refresh and cached_hiras:
                    self.progress.emit("Loading HiRAS from cache...")
                    results['hiras'] = str(cached_hiras)
                    self.result_ready.emit('hiras', str(cached_hiras))
                else:
                    self.progress.emit("Fetching and stitching HiRAS data...")
                    try:
                        hiras_data = self._fetch_nict_spectra('hiras', event_date)
                        if hiras_data:
                            hiras_file = cache.save_spectra(event_date, 'hiras', hiras_data)
                            results['hiras'] = str(hiras_file)
                            self.result_ready.emit('hiras', str(hiras_file))
                            self.progress.emit("HiRAS data stitched!")
                    except Exception as e:
                        err_msg = f"HiRAS: {str(e)[:50]}"
                        self.progress.emit(err_msg)
                        errors.append(err_msg)
            
            self.finished.emit(results, errors)
        except Exception as e:
            # Final safety net for the entire worker run
            if 'results' not in locals():
                results = {}
            if 'errors' not in locals():
                errors = [f"Critical worker error: {str(e)}"]
            else:
                errors.append(f"Critical worker error: {str(e)}")
            self.finished.emit(results, errors)
            
    def _fetch_nict_spectra(self, station: str, event_date: date) -> Optional[bytes]:
        """Fetch hourly strips, legend, and range from NICT and stitch them horizontally.
        
        Follows the logic from yamagawa.py precisely.
        """
        import requests
        from PIL import Image
        import io
        
        referer = "https://solobs.nict.go.jp/radio/cgi-bin/MainDisplay.pl"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Referer': referer
        }
        
        base_url = f"https://solobs.nict.go.jp/radio/data/img/{station}"
        fixed_url = f"{base_url}/fixed"
        
        if station == 'yamagawa':
            prefix = "YAMAGAWA"
        else:
            prefix = "HiRAS"

        def download_pil_image(url):
            try:
                r = requests.get(url, headers=headers, timeout=10)
                if r.status_code == 200:
                    return Image.open(io.BytesIO(r.content))
            except Exception as e:
                print(f"Error downloading {url}: {e}")
            return None

        # 1. Download legend and range
        cb = download_pil_image(f"{fixed_url}/{prefix}_FIXTMIMG_legend.png")
        ax = download_pil_image(f"{fixed_url}/{prefix}_FIXTMIMG_range.png")

        if not cb or not ax:
            return None

        # 2. Download 24 hourly images
        hourly_images = []
        target_size = (450, 600) 
        
        year = event_date.strftime("%Y")
        month = event_date.strftime("%m")
        day = event_date.strftime("%d")
        date_compact = event_date.strftime("%Y%m%d")

        for h in range(24):
            hour_str = f"{h:02d}"
            url = f"{base_url}/{year}/{month}/{day}/{prefix}_{date_compact}{hour_str}.png"
            img = download_pil_image(url)
            
            if not img:
                fallback_url = f"{fixed_url}/{prefix}_FIXTMIMG{hour_str}.png"
                img = download_pil_image(fallback_url)
                if img:
                    img = img.resize(target_size, Image.Resampling.LANCZOS)
                else:
                    img = Image.new('RGB', target_size, (0, 0, 0))
            else:
                if img.size != target_size:
                    target_size = img.size
            
            hourly_images.append(img)

        # 3. Stitch
        max_height = max(cb.height, ax.height, max(img.height for img in hourly_images))
        total_width = cb.width + ax.width + sum(img.width for img in hourly_images)

        stitched_img = Image.new('RGB', (total_width, max_height), (255, 255, 255))
        
        x_offset = 0
        # Paste Colorbar
        stitched_img.paste(cb, (x_offset, 0))
        x_offset += cb.width
        
        # Paste Axis
        stitched_img.paste(ax, (x_offset, 0))
        x_offset += ax.width
        
        # Paste Hourly Plots
        for img in hourly_images:
            stitched_img.paste(img, (x_offset, 0))
            x_offset += img.width

        img_byte_arr = io.BytesIO()
        stitched_img.save(img_byte_arr, format='PNG')
        return img_byte_arr.getvalue()

    def _find_latest_image(self, filenames: list) -> str:
        """Find the image file with the latest timestamp for full-day coverage."""
        from datetime import datetime as dt
        import re
        
        if not filenames:
            return None
        
        best_file = None
        max_time = None
        
        for fname in filenames:
            # Try to extract timestamp from filename (format: YYYYMMDD_HHMMSS)
            match = re.search(r'(\d{8})_(\d{6})', fname)
            if match:
                try:
                    file_dt = dt.strptime(f"{match.group(1)}_{match.group(2)}", "%Y%m%d_%H%M%S")
                    if max_time is None or file_dt > max_time:
                        max_time = file_dt
                        best_file = fname
                except ValueError:
                    continue
        
        # If no timestamp found, return the last file (usually latest in sorted order)
        return best_file if best_file else filenames[-1]

    def _stitch_images(self, base_url: str, filenames: list) -> Optional[bytes]:
        """Download multiple images (7 hours apart) and stitch them horizontally."""
        from PIL import Image
        import io
        import urllib.request
        from datetime import datetime as dt
        import re
        
        if not filenames:
            return None
            
        # Parse timestamps and sort
        file_times = []
        for fname in filenames:
            match = re.search(r'(\d{8})_(\d{6})', fname)
            if match:
                try:
                    file_dt = dt.strptime(f"{match.group(1)}_{match.group(2)}", "%Y%m%d_%H%M%S")
                    file_times.append((file_dt, fname))
                except ValueError:
                    continue
        
        file_times.sort() # Ensure chronological order
        
        # Select files approximately 7 hours apart to cover the day without redundancy
        selected_filenames = []
        if file_times:
            last_time = None
            for ftime, fname in file_times:
                # Use slightly less than 7h (e.g. 6.5h) to ensure we don't miss a segment 
                # due to slightly varied start times (7h plots often start at 00, 07, 14, 21)
                if last_time is None or (ftime - last_time).total_seconds() >= 6.5 * 3600:
                    selected_filenames.append(fname)
                    last_time = ftime
        else:
            # Fallback for old formats without timestamps (unlikely for multi-file)
            selected_filenames = sorted(list(set(filenames)))[:4]
            
        if not selected_filenames:
            return None
            
        images = []
        try:
            # Limit to 5 images to avoid excessive memory usage
            for fname in selected_filenames[:5]:
                img_url = f"{base_url}{fname}"
                req = urllib.request.urlopen(img_url, timeout=20)
                img_data = req.read()
                img = Image.open(io.BytesIO(img_data))
                images.append(img)
                
            if not images:
                return None
                
            if len(images) == 1:
                # Still return bytes for consistent interface
                img_byte_arr = io.BytesIO()
                images[0].save(img_byte_arr, format='PNG')
                return img_byte_arr.getvalue()
                
            # Stitch horizontally
            widths, heights = zip(*(i.size for i in images))
            total_width = sum(widths)
            max_height = max(heights)
            
            new_im = Image.new('RGB', (total_width, max_height))
            
            x_offset = 0
            for im in images:
                # Align to top (y=0)
                new_im.paste(im, (x_offset, 0))
                x_offset += im.size[0]
                
            # Convert back to bytes
            img_byte_arr = io.BytesIO()
            new_im.save(img_byte_arr, format='PNG')
            return img_byte_arr.getvalue()
            
        except Exception as e:
            print(f"Stitching failed: {e}")
            return None

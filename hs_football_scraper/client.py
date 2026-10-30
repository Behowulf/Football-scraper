"""HTTP layer: polite, retrying fetcher with an optional on-disk cache.

247Sports sometimes serves bot-challenge pages to plain HTTP clients. If that
happens, pass ``use_browser=True`` to fetch pages through headless Chromium
(requires ``pip install playwright && playwright install chromium``).
"""

from __future__ import annotations

import hashlib
import logging
import random
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

BASE_URL = "https://247sports.com"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class FetchError(RuntimeError):
    pass


class Fetcher:
    def __init__(
        self,
        delay: float = 1.5,
        cache_dir: str | Path | None = None,
        use_browser: bool = False,
        timeout: int = 30,
    ):
        self.delay = delay
        self.timeout = timeout
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.use_browser = use_browser
        self._last_request = 0.0
        self._browser = None
        self._page = None

        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        retry = Retry(
            total=5,
            backoff_factor=2,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.mount("http://", HTTPAdapter(max_retries=retry))

    # -- public -----------------------------------------------------------
    def get(self, url: str, use_cache: bool = True) -> str:
        if url.startswith("/"):
            url = BASE_URL + url
        cached = self._cache_path(url)
        if use_cache and cached and cached.exists():
            return cached.read_text(encoding="utf-8")

        self._throttle()
        html = self._get_browser(url) if self.use_browser else self._get_requests(url)
        if cached:
            cached.write_text(html, encoding="utf-8")
        return html

    def close(self) -> None:
        if self._browser:
            self._browser.close()
            self._pw.stop()
            self._browser = None

    # -- internals --------------------------------------------------------
    def _throttle(self) -> None:
        wait = self.delay + random.uniform(0, self.delay / 2) - (time.time() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.time()

    def _cache_path(self, url: str) -> Path | None:
        if not self.cache_dir:
            return None
        return self.cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".html")

    def _get_requests(self, url: str) -> str:
        log.debug("GET %s", url)
        try:
            resp = self.session.get(url, timeout=self.timeout)
        except requests.RequestException as exc:
            raise FetchError(f"{url}: {exc}") from exc
        if resp.status_code == 404:
            raise FetchError(f"{url}: 404 Not Found")
        if resp.status_code >= 400:
            raise FetchError(
                f"{url}: HTTP {resp.status_code}. If this persists, the site may be "
                "blocking scripted requests; try --browser."
            )
        return resp.text

    def _get_browser(self, url: str) -> str:
        if self._browser is None:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:
                raise FetchError("--browser requires: pip install playwright") from exc
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
            self._page = self._browser.new_page(user_agent=DEFAULT_HEADERS["User-Agent"])
        log.debug("BROWSER GET %s", url)
        self._page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
        try:
            self._page.wait_for_selector("a[href*='/player/' i]", timeout=10000)
        except Exception:
            pass
        return self._page.content()

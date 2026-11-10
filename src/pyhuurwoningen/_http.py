from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Callable
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

if TYPE_CHECKING:
    from curl_cffi import requests

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_BACKOFF_SECONDS = 120.0


class HuurwoningenError(Exception):
    """Something went wrong talking to huurwoningen.nl. `status` is the HTTP code, if any."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class RobotsDisallowed(HuurwoningenError):
    """The site's robots.txt asks scrapers not to visit this page."""


class PoliteClient:
    def __init__(
        self,
        base_url: str,
        user_agent: str,
        timeout: float = 20.0,
        max_retries: int = 3,
        respect_robots: bool = False,
        impersonate: str | None = "chrome",
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_retries = max_retries
        self.respect_robots = respect_robots
        self.impersonate = impersonate
        if session is None:
            from curl_cffi import requests

            session = requests.Session(impersonate=impersonate)
        self.session = session
        self._netloc = urlparse(self.base_url).netloc
        headers = {"Accept-Language": "nl-NL,nl;q=0.9,en-US;q=0.8,en;q=0.7"}
        if not impersonate:
            headers |= {"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml"}
        self.session.headers.update(headers)
        self._sleep = sleep
        self._robots: RobotFileParser | None = None

    def get(self, path_or_url: str, params: dict | None = None) -> requests.Response:
        url = urljoin(self.base_url, path_or_url)
        if urlparse(url).netloc != self._netloc:
            raise ValueError(f"{url} is not on {self.base_url}")
        if self.respect_robots and not self._allowed(url):
            raise RobotsDisallowed(f"robots.txt does not allow scraping {url}")

        for attempt in range(self.max_retries + 1):
            resp = self._request(url, params)
            if resp.status_code not in RETRY_STATUSES or attempt == self.max_retries:
                break
            backoff = self._retry_after(resp)
            if backoff is None:
                backoff = min(MAX_BACKOFF_SECONDS, 10.0 * 2**attempt)
            elif backoff > MAX_BACKOFF_SECONDS:
                raise HuurwoningenError(
                    f"huurwoningen.nl is busy (HTTP {resp.status_code}) and asked to wait "
                    f"{backoff / 60:.0f} minutes before trying again.",
                    resp.status_code,
                )
            log.warning("HTTP %s on %s, retrying in %.0fs", resp.status_code, url, backoff)
            self._sleep(backoff)

        if resp.status_code == 403 and resp.headers.get("cf-mitigated"):
            raise HuurwoningenError(
                "huurwoningen.nl blocked this request with a bot check (Cloudflare challenge). "
                "A JavaScript challenge needs a real web browser to solve; this library won't. Try "
                "again later, or read pages you saved from your browser with pyhuurwoningen.parsing.",
                403,
            )
        if resp.status_code >= 400:
            raise HuurwoningenError(f"huurwoningen.nl answered HTTP {resp.status_code} for {url}", resp.status_code)
        return resp

    def close(self) -> None:
        self.session.close()

    # -- internals --------------------------------------------------------

    def _request(self, url: str, params: dict | None = None) -> requests.Response:
        from curl_cffi.requests.exceptions import RequestException  # cached after the first call

        log.info("GET %s %s", url, params or "")
        try:
            return self.session.get(url, params=params, timeout=self.timeout)
        except RequestException as e:
            raise HuurwoningenError(f"Could not reach huurwoningen.nl ({e.__class__.__name__}): {e}") from e

    def _allowed(self, url: str) -> bool:
        if self._robots is None:
            resp = self._request(urljoin(self.base_url, "/robots.txt"))
            robots = RobotFileParser()
            robots.parse([] if resp.status_code >= 400 else resp.text.splitlines())
            self._robots = robots
        return self._robots.can_fetch(self.user_agent, url)

    @staticmethod
    def _retry_after(resp: requests.Response) -> float | None:
        """Seconds to wait: Retry-After is either a number of seconds or an HTTP date."""
        value = (resp.headers.get("Retry-After") or "").strip()
        if value.isdigit():
            return float(value)
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())

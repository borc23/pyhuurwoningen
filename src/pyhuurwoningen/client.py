from __future__ import annotations

import difflib
import logging
import os
import time
from collections import Counter
from collections.abc import Iterator
from importlib.metadata import PackageNotFoundError, version

from pyhuurwoningen._http import HuurwoningenError, PoliteClient
from pyhuurwoningen.models import Listing, ListingDetails, SitemapEntry
from pyhuurwoningen.parsing import (
    BASE_URL,
    SITEMAP_PATH,
    city_slug,
    parse_detail_page,
    parse_search_page,
    parse_sitemap,
)

log = logging.getLogger(__name__)

try:
    PACKAGE_VERSION = version("pyhuurwoningen")
except PackageNotFoundError:  # running from a source checkout that isn't installed
    PACKAGE_VERSION = "0+unknown"

DEFAULT_USER_AGENT = f"pyhuurwoningen/{PACKAGE_VERSION} (personal, low-volume)"
DEFAULT_IMPERSONATE = "chrome"  # newest Chrome profile in the installed curl_cffi
SITEMAP_MAX_AGE = 60.0  # seconds a downloaded sitemap is reused by the same Huurwoningen


class Huurwoningen:
    """A connection to huurwoningen.nl.

    Example:
        hw = Huurwoningen()
        homes = hw.search("utrecht", max_price=1200)
    """

    def __init__(
        self,
        user_agent: str | None = None,
        impersonate: str | None = None,
    ) -> None:
        """
        user_agent:  name used for robots.txt rules (and sent as User-Agent when
                     impersonate is off). Default: $HW_USER_AGENT, or "pyhuurwoningen/<version> (...)".
        impersonate: browser whose network fingerprint to use, e.g. "chrome", "safari", "firefox".
                     "" or "none" turns it off. Default: $HW_IMPERSONATE, or "chrome".
        """
        self._http = PoliteClient(
            BASE_URL,
            user_agent=user_agent or os.getenv("HW_USER_AGENT", DEFAULT_USER_AGENT),
            impersonate=_impersonate_setting(impersonate),
        )
        self._sitemap: tuple[float, list[SitemapEntry]] | None = None  # (fetched at, entries)

    def search(
        self,
        city: str,
        max_price: int | None = None,
        min_rooms: int | None = None,
        min_area: float | None = None,
        pages: int = 1,
        skip_ids: set[str] | None = None,
    ) -> list[Listing]:
        """Rental homes in a city, newest first.

        city:      e.g. "rotterdam", "Den Haag", "amsterdam".
        max_price: only homes up to this rent (euros per month).
        min_rooms: only homes with at least this many rooms.
        min_area:  only homes of at least this many m².
        pages:     how many result pages to read (~30 homes each, one request each).
        skip_ids:  ids you have already seen. They are left out, and reading stops at
                   the page that ends in one, so checking for new homes usually costs
                   one request.

        Each home is returned once, even if it moves to the next page while reading.

        Homes that don't show a price/rooms/area are kept, not filtered out.
        """
        found: list[Listing] = []
        returned: set[str] = set()
        skip = skip_ids or set()
        for page in range(1, pages + 1):
            params = {"sort": "published_at", "direction": "desc"}
            if page > 1:
                params["page"] = str(page)
            try:
                html = self._http.get(f"/in/{city_slug(city)}/", params=params).content
            except HuurwoningenError as e:
                if e.status == 404 and page > 1:
                    break  # ran past the last results page: keep what we have
                if e.status == 404:
                    raise HuurwoningenError(
                        f"City {city!r} not found on huurwoningen.nl.{self._city_hint(city)}", 404
                    ) from e
                raise
            listings = parse_search_page(html)  # raw bytes: lexbor reads UTF-8 itself, no decode
            if not listings:
                break
            for x in listings:
                if x.id not in returned and x.id not in skip:
                    returned.add(x.id)
                    found.append(x)
            if listings[-1].id in skip:
                break

        return filter_listings(found, max_price=max_price, min_rooms=min_rooms, min_area=min_area)

    def get_details(self, listing: Listing | str) -> ListingDetails:
        """Everything on a listing's own page. Pass a Listing from search(), or its URL."""
        url = listing.url if isinstance(listing, Listing) else listing
        return parse_detail_page(self._http.get(url).content, url)

    def listing_index(self, city: str | None = None) -> list[SitemapEntry]:
        """Every listing on the site, or in one city: id, URL and last change, newest first.

        One request (the site's sitemap, ~2.5 MB) however many listings there are,
        reused for a minute, so cities() then download() fetches it only once.
        It has no price, size etc.: use download() or get_details() for those.
        """
        if self._sitemap is None or time.monotonic() - self._sitemap[0] > SITEMAP_MAX_AGE:
            self._sitemap = (time.monotonic(), parse_sitemap(self._http.get(SITEMAP_PATH).content))
        entries = list(self._sitemap[1])
        if city is None:
            return entries
        slug = city_slug(city)
        return [e for e in entries if e.city == slug]

    def cities(self) -> dict[str, int]:
        """Every city with homes for rent, and how many: {"amsterdam": 1568, ...}, most first.

        The names are the form search(), download() and the command line accept.
        One request (the site's sitemap).
        """
        return dict(Counter(e.city for e in self.listing_index()).most_common())

    def download(self, city: str | None = None, skip_ids: set[str] | None = None) -> Iterator[ListingDetails]:
        """Full details of every listing in a city (or, with no city, the whole country).

        Homes come one at a time, newest first, one request each. So use it in a
        for loop and save each home as it arrives:

            for home in hw.download("rotterdam", skip_ids=seen):
                save(home)
                seen.add(home.id)

        skip_ids:  ids you already have. Pass them to get only new homes, or to
                   carry on where an earlier run stopped.

        Listings that are gone (HTTP 404/410) are skipped. Other errors (blocked,
        rate-limited) raise HuurwoningenError; everything yielded so far is yours.
        """
        skip = skip_ids or set()
        entries = self.listing_index()
        if city is not None:
            slug = city_slug(city)
            matching = [e for e in entries if e.city == slug]
            if not matching:
                log.warning("no listings for %r.%s", city, _did_you_mean(city, {e.city for e in entries}))
            entries = matching
        for entry in entries:
            if entry.id in skip:
                continue
            try:
                yield self.get_details(entry.url)
            except HuurwoningenError as e:
                if e.status not in (404, 410):
                    raise
                log.info("skipping %s: listing is gone (HTTP %s)", entry.url, e.status)

    def _city_hint(self, city: str) -> str:
        """' Did you mean ...?' for a city that wasn't found (costs one request), or a spelling tip."""
        try:
            hint = _did_you_mean(city, self.cities())
        except HuurwoningenError:
            hint = ""
        return hint or " Check the spelling, e.g. 'den-haag'; `huurwoningen --cities` lists them all."

    def close(self) -> None:
        """Close the network connection (optional; `with Huurwoningen() as hw:` does it for you)."""
        self._http.close()

    def __enter__(self) -> Huurwoningen:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def filter_listings(
    listings: list[Listing],
    max_price: int | None = None,
    min_rooms: int | None = None,
    min_area: float | None = None,
) -> list[Listing]:
    """Keep only the listings that match. Listings missing a value are kept."""
    return [
        x for x in listings
        if _at_most(x.price, max_price) and _at_least(x.rooms, min_rooms) and _at_least(x.area, min_area)
    ]


def _at_most(value: float | None, limit: float | None) -> bool:
    return limit is None or value is None or value <= limit


def _at_least(value: float | None, limit: float | None) -> bool:
    return limit is None or value is None or value >= limit


def _did_you_mean(city: str, known: object) -> str:
    """' Did you mean 'rotterdam'?' when `city` looks like a typo of a known city, else ''."""
    close = difflib.get_close_matches(city_slug(city), list(known), n=3, cutoff=0.75)  # type: ignore[call-overload]
    return f" Did you mean {' or '.join(repr(c) for c in close)}?" if close else ""


def _impersonate_setting(value: str | None) -> str | None:
    value = value if value is not None else os.getenv("HW_IMPERSONATE", DEFAULT_IMPERSONATE)
    return None if value.strip().lower() in {"", "none", "off"} else value.strip()

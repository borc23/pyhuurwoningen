from __future__ import annotations

from collections.abc import Iterator

from pyhuurwoningen._http import HuurwoningenError
from pyhuurwoningen.client import PACKAGE_VERSION as __version__
from pyhuurwoningen.client import Huurwoningen, filter_listings
from pyhuurwoningen.models import Listing, ListingDetails, SitemapEntry, save_csv
from pyhuurwoningen.schema import schema

_default: Huurwoningen | None = None


def _client() -> Huurwoningen:
    global _default
    if _default is None:
        _default = Huurwoningen()
    return _default


def search(
    city: str,
    max_price: int | None = None,
    min_rooms: int | None = None,
    min_area: float | None = None,
    pages: int = 1,
    skip_ids: set[str] | None = None,
) -> list[Listing]:
    return _client().search(city, max_price, min_rooms, min_area, pages, skip_ids)


def get_details(listing: Listing | str) -> ListingDetails:
    return _client().get_details(listing)


def listing_index(city: str | None = None) -> list[SitemapEntry]:
    return _client().listing_index(city)


def cities() -> dict[str, int]:
    return _client().cities()


def download(city: str | None = None, skip_ids: set[str] | None = None) -> Iterator[ListingDetails]:
    return _client().download(city, skip_ids)


search.__doc__ = Huurwoningen.search.__doc__
get_details.__doc__ = Huurwoningen.get_details.__doc__
listing_index.__doc__ = Huurwoningen.listing_index.__doc__
download.__doc__ = Huurwoningen.download.__doc__
cities.__doc__ = Huurwoningen.cities.__doc__

__all__ = [
    "Huurwoningen",
    "HuurwoningenError",
    "Listing",
    "ListingDetails",
    "SitemapEntry",
    "cities",
    "download",
    "filter_listings",
    "get_details",
    "listing_index",
    "save_csv",
    "schema",
    "search",
]

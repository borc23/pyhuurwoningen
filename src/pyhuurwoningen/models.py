from __future__ import annotations

import csv
from dataclasses import dataclass, field, fields
from datetime import date, datetime
from functools import cache


@cache
def _field_names(cls: type) -> tuple[str, ...]:
    return tuple(f.name for f in fields(cls))


# slots: no per-object __dict__, so each home/sitemap entry takes less memory.
@dataclass(slots=True)
class Listing:
    id: str  # short id from the URL, e.g. "ea81e68d"
    url: str
    title: str
    price: int | None = None  # euros per month
    area: float | None = None  # square metres (m²)
    rooms: int | None = None
    postcode: str | None = None  # e.g. "3083CR"
    city: str | None = None
    neighbourhood: str | None = None
    build_year: int | None = None
    is_new: bool = False  # marked "new" on the search page

    def to_dict(self) -> dict:
        """All fields as a plain dictionary."""
        # Same result as dataclasses.asdict() for these flat models, without its deep copy
        # of every value; only the one mutable field (features) is copied.
        d = {name: getattr(self, name) for name in _field_names(type(self))}
        if "features" in d:
            d["features"] = dict(d["features"])
        return d

    def __str__(self) -> str:
        price = f"€{self.price}/month" if self.price else "price unknown"
        area = f"{self.area:g} m²" if self.area else "? m²"
        rooms = f"{self.rooms} rooms" if self.rooms else "? rooms"
        where = self.neighbourhood or self.city or self.postcode or ""
        return f"{self.title} | {price} | {area} | {rooms} | {where}"


@dataclass(slots=True)
class ListingDetails(Listing):
    street: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    deposit: int | None = None  # euros
    service_costs: int | None = None  # euros per month

    bedrooms: int | None = None
    bathrooms: int | None = None
    dwelling_type: str | None = None  # e.g. "Appartement"
    property_type: str | None = None  # e.g. "Galerijflat"
    construction_type: str | None = None  # e.g. "Bestaande bouw"
    interior: str | None = None  # "Kaal" / "Gestoffeerd" / "Gemeubileerd"
    energy_label: str | None = None

    status: str | None = None  # as shown, e.g. "Te huur", "Onder optie", "Verhuurd"
    is_available: bool | None = None
    available_from: str | None = None  # as shown, e.g. "Per direct"
    offered_since: date | None = None

    description: str | None = None
    image_url: str | None = None

    # Every label/value pair on the page, exactly as shown, so nothing is lost.
    features: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class SitemapEntry:
    id: str  # short id from the URL, e.g. "ea81e68d"
    url: str
    city: str  # as in the URL, e.g. "den-haag"
    lastmod: datetime | None = None  # last change; edits count too, so not the publish date


def save_csv(listings: list[Listing], path: str) -> None:
    """Save listings to a CSV file you can open in Excel, Numbers or Google Sheets."""
    if not listings:
        raise ValueError("there are no listings to save")
    columns = [name for name in _field_names(type(listings[0])) if name != "features"]
    # utf-8-sig so Excel shows "€" and "m²" correctly.
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        # Plain Listings mixed with ListingDetails leave the extra columns empty.
        writer.writerows([getattr(x, c, None) for c in columns] for x in listings)

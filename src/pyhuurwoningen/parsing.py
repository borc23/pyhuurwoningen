from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from pyhuurwoningen.models import Listing, ListingDetails, SitemapEntry

BASE_URL = "https://www.huurwoningen.nl"
DETAIL_PATH = re.compile(r"^/(?:en/)?huren/([^/]+)/([0-9a-f]+)/[^/]+/?$")
SITEMAP_PATH = "/sitemap/listings.xml"
_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"

# Label on the page (Dutch or English) -> ListingDetails field. Matched case-insensitively.
LABELS: dict[str, str] = {
    "huurprijs": "price",
    "rental price": "price",
    "aangeboden sinds": "offered_since",
    "offered since": "offered_since",
    "status": "status",
    "beschikbaar": "available_from",
    "available": "available_from",
    "woonoppervlakte": "area",
    "living area": "area",
    "type woning": "dwelling_type",
    "soort woning": "property_type",
    "soort bouw": "construction_type",
    "type of construction": "construction_type",
    "bouwjaar": "build_year",
    "year of construction": "build_year",
    "aantal kamers": "rooms",
    "number of rooms": "rooms",
    "aantal slaapkamers": "bedrooms",
    "number of bedrooms": "bedrooms",
    "aantal badkamers": "bathrooms",
    "number of bathrooms": "bathrooms",
    "interieur": "interior",
    "interior": "interior",
    "energielabel": "energy_label",
    "energy rating": "energy_label",
    "waarborgsom": "deposit",
    "deposit": "deposit",
    "servicekosten": "service_costs",
    "service costs": "service_costs",
}
AVAILABLE_STATUSES = {"te huur", "for rent"}


# --------------------------------------------------------------------------- #
# Small helpers for Dutch number formats ("€ 1.207", "8,5 m²")
# --------------------------------------------------------------------------- #

def parse_euros(text: str | None) -> int | None:
    """'€ 1.207 per maand' -> 1207; '€ 915,-' -> 915."""
    if not text:
        return None
    m = re.search(r"(\d{1,3}(?:[.\s]\d{3})+|\d+)", text.replace("\xa0", " "))
    return int(re.sub(r"[.\s]", "", m.group(1))) if m else None


def parse_area(text: str | None) -> float | None:
    """'50 m²' -> 50.0; '8,5 m²' -> 8.5."""
    if not text:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*m", text)
    return float(m.group(1).replace(",", ".")) if m else None


def parse_int(text: str | None) -> int | None:
    """First whole number in the text: '3 kamers' -> 3."""
    if not text:
        return None
    m = re.search(r"\d+", text)
    return int(m.group(0)) if m else None


def parse_nl_date(text: str | None) -> date | None:
    """'09-11-2026' or '2026-11-09' -> date(2026, 11, 9)."""
    if not text:
        return None
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.strip()[:10], fmt).date()
        except ValueError:
            continue
    return None


def parse_location(text: str) -> tuple[str | None, str | None, str | None]:
    """'3083 CR Rotterdam (Zuidplein)' -> ('3083CR', 'Rotterdam', 'Zuidplein').
    Also handles the detail page form '3083 CR (Zuidplein)' (no city)."""
    m = re.match(r"^\s*(\d{4})\s?([A-Z]{2})\s*(.*?)\s*(?:\(([^)]*)\))?\s*$", text)
    if not m:
        return None, None, None
    return f"{m.group(1)}{m.group(2)}", (m.group(3) or None), (m.group(4) or None)


def city_slug(city: str) -> str:
    """'Den Haag' -> 'den-haag' (the form the site uses in URLs)."""
    return re.sub(r"[^a-z0-9]+", "-", city.strip().lower()).strip("-")


def id_from_url(url: str) -> str | None:
    """'https://www.huurwoningen.nl/huren/rotterdam/ea81e68d/zuidplein/' -> 'ea81e68d'."""
    path = re.sub(r"^https?://[^/]+", "", url).split("?")[0]
    m = DETAIL_PATH.match(path)
    return m.group(2) if m else None


def _text(el: Tag | None) -> str:
    return " ".join(el.get_text(" ").split()) if el else ""


def _number(value: object) -> float | None:
    """JSON-LD numbers can arrive as 915, 915.0 or "915"."""
    try:
        return float(value) if value not in (None, "") else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _int(value: object) -> int | None:
    n = _number(value)
    return int(n) if n is not None else None


# Schema.org lets most JSON-LD properties be a dict, a list, or a bare string/number.
# These helpers accept any of those, so odd JSON-LD falls back to the page labels
# instead of crashing.

def _node(value: object) -> dict:
    """The (first) object: {...} -> {...}, [{...}, ...] -> {...}, anything else -> {}."""
    if isinstance(value, list):
        value = next((v for v in value if isinstance(v, dict)), None)
    return value if isinstance(value, dict) else {}


def _value(value: object) -> object:
    """A quantity: 50, "50", {"value": 50}, [50] and [{"value": 50}] all give 50."""
    if isinstance(value, list):
        value = value[0] if value else None
    return value.get("value") if isinstance(value, dict) else value


def _str(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


# --------------------------------------------------------------------------- #
# Page parsers
# --------------------------------------------------------------------------- #

def parse_sitemap(xml: str | bytes) -> list[SitemapEntry]:
    """The listings sitemap -> one SitemapEntry per listing, most recently changed first.

    URLs that aren't listing pages are left out.
    """
    entries: list[SitemapEntry] = []
    for url_el in ET.fromstring(xml).iter(f"{_SITEMAP_NS}url"):
        loc = (url_el.findtext(f"{_SITEMAP_NS}loc") or "").strip()
        m = DETAIL_PATH.match(re.sub(r"^https?://[^/]+", "", loc))
        if not m:
            continue
        try:
            lastmod = datetime.fromisoformat((url_el.findtext(f"{_SITEMAP_NS}lastmod") or "").strip())
        except ValueError:
            lastmod = None
        entries.append(SitemapEntry(id=m.group(2), url=loc, city=m.group(1), lastmod=lastmod))
    # Newest first; entries without a date go last.
    entries.sort(key=lambda e: e.lastmod.timestamp() if e.lastmod else float("-inf"), reverse=True)
    return entries


def parse_search_page(html: str, base_url: str = BASE_URL) -> list[Listing]:
    """All listing cards on one search-results page."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[Listing] = []
    for card in soup.select("section.listing-search-item"):
        link = card.select_one("a.listing-search-item__link--title") or card.select_one("a[href*='/huren/']")
        if not link or not link.get("href"):
            continue
        url = urljoin(base_url, str(link["href"]))
        listing_id = id_from_url(url) or card.get("data-listing-search-item-id")
        if not listing_id:
            continue
        postcode, city, hood = parse_location(_text(card.select_one(".listing-search-item__sub-title")))
        out.append(
            Listing(
                id=str(listing_id),
                url=url,
                title=_text(link),
                price=parse_euros(_text(card.select_one(".listing-search-item__price-main"))),
                area=parse_area(_text(card.select_one(".illustrated-features__item--surface-area"))),
                rooms=parse_int(_text(card.select_one(".illustrated-features__item--number-of-rooms"))),
                postcode=postcode,
                city=city,
                neighbourhood=hood,
                build_year=parse_int(_text(card.select_one(".illustrated-features__item--construction-period"))),
                is_new=card.select_one(".listing-label--new") is not None,
            )
        )
    return out


def _find_listing_jsonld(soup: BeautifulSoup) -> dict:
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except json.JSONDecodeError:
            continue
        nodes = data.get("@graph", [data]) if isinstance(data, dict) else data
        for node in nodes if isinstance(nodes, list) else [nodes]:
            types = node.get("@type", []) if isinstance(node, dict) else []
            if "RealEstateListing" in (types if isinstance(types, list) else [types]):
                return node
    return {}


def parse_detail_page(html: str, url: str) -> ListingDetails:
    """Everything on one listing's own page."""
    soup = BeautifulSoup(html, "html.parser")
    ld = _find_listing_jsonld(soup)

    # 1) All label/value pairs, exactly as shown.
    features: dict[str, str] = {}
    for dt in soup.select("dt.listing-features__term"):
        dd = dt.find_next_sibling("dd")
        main = dd.select_one(".listing-features__main-description") if isinstance(dd, Tag) else None
        label, value = _text(dt), _text(main or dd)  # type: ignore[arg-type]
        if label and value and label not in features:
            features[label] = value
    page: dict[str, str] = {}
    for label, value in features.items():
        name = LABELS.get(label.lower())
        if name and name not in page:
            page[name] = value

    # 2) JSON-LD first, page labels as fallback.
    address = _node(ld.get("address"))  # a plain-string address is skipped: labels fill in
    geo = _node(ld.get("geo"))

    loc_postcode, _, loc_hood = parse_location(_text(soup.select_one(".listing-detail-summary__location")))
    status = page.get("status")
    image = ld.get("image")
    description = _str(ld.get("description"))
    if not description:
        desc_el = soup.select_one(".listing-detail-description__content")
        if desc_el:
            for title in desc_el.select(".listing-detail-description__title"):
                title.decompose()
            description = _text(desc_el) or None

    return ListingDetails(
        id=id_from_url(url) or url,
        url=url,
        title=_text(soup.select_one("h1.listing-detail-summary__title")) or _str(ld.get("name")) or "",
        price=_int(_node(ld.get("offers")).get("price")) or parse_euros(page.get("price")),
        area=_number(_value(ld.get("floorSize"))) or parse_area(page.get("area")),
        rooms=_int(_value(ld.get("numberOfRooms"))) or parse_int(page.get("rooms")),
        postcode=(_str(address.get("postalCode")) or loc_postcode or "").replace(" ", "") or None,
        city=_str(address.get("addressLocality")),
        neighbourhood=loc_hood or _str(address.get("addressRegion")),
        build_year=parse_int(page.get("build_year")),
        street=_str(address.get("streetAddress")),
        latitude=_number(geo.get("latitude")),
        longitude=_number(geo.get("longitude")),
        deposit=parse_euros(page.get("deposit")),
        service_costs=parse_euros(page.get("service_costs")),
        bedrooms=_int(_value(ld.get("numberOfBedrooms"))) or parse_int(page.get("bedrooms")),
        bathrooms=_int(_value(ld.get("numberOfBathroomsTotal"))) or parse_int(page.get("bathrooms")),
        dwelling_type=page.get("dwelling_type"),
        property_type=page.get("property_type"),
        construction_type=page.get("construction_type"),
        interior=page.get("interior"),
        energy_label=page.get("energy_label"),
        status=status,
        is_available=(status.lower() in AVAILABLE_STATUSES) if status else None,
        available_from=page.get("available_from"),
        offered_since=parse_nl_date(page.get("offered_since") or _str(ld.get("datePosted"))),
        description=description,
        image_url=image if isinstance(image, str) else None,
        features=features,
    )

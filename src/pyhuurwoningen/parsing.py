from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime, tzinfo
from operator import attrgetter
from urllib.parse import urljoin
from xml.sax.saxutils import unescape as _xml_unescape

from selectolax.lexbor import LexborHTMLParser, LexborNode

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

# Compiled once: these run for every card, every page, every sitemap entry.
_EUROS = re.compile(r"(\d{1,3}(?:[.\s]\d{3})+|\d+)")
_EURO_SEPARATORS = re.compile(r"[.\s]")
_AREA = re.compile(r"(\d+(?:[.,]\d+)?)\s*m")
_INT = re.compile(r"\d+")
_LOCATION = re.compile(r"^\s*(\d{4})\s?([A-Z]{2})\s*(.*?)\s*(?:\(([^)]*)\))?\s*$")
_CITY_SLUG = re.compile(r"[^a-z0-9]+")
_SCHEME_HOST = re.compile(r"^https?://[^/]+")
# A full listing URL, so a sitemap <loc> is matched without stripping the host first.
_DETAIL_URL = re.compile(r"^https?://[^/]+/(?:en/)?huren/([^/]+)/([0-9a-f]+)/[^/]+/?$")
# One <loc> and the <lastmod> right after it. Runs over the whole sitemap in C.
_SITEMAP_ENTRY = re.compile(rb"<loc>\s*([^<]*?)\s*</loc>(?:\s*<lastmod>\s*([^<]*?)\s*</lastmod>)?")


# --------------------------------------------------------------------------- #
# Small helpers for Dutch number formats ("€ 1.207", "8,5 m²")
# --------------------------------------------------------------------------- #

def parse_euros(text: str | None) -> int | None:
    """'€ 1.207 per maand' -> 1207; '€ 915,-' -> 915."""
    if not text:
        return None
    m = _EUROS.search(text.replace("\xa0", " "))
    return int(_EURO_SEPARATORS.sub("", m.group(1))) if m else None


def parse_area(text: str | None) -> float | None:
    """'50 m²' -> 50.0; '8,5 m²' -> 8.5."""
    if not text:
        return None
    m = _AREA.search(text)
    return float(m.group(1).replace(",", ".")) if m else None


def parse_int(text: str | None) -> int | None:
    """First whole number in the text: '3 kamers' -> 3."""
    if not text:
        return None
    m = _INT.search(text)
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
    m = _LOCATION.match(text)
    if not m:
        return None, None, None
    return f"{m.group(1)}{m.group(2)}", (m.group(3) or None), (m.group(4) or None)


def city_slug(city: str) -> str:
    """'Den Haag' -> 'den-haag' (the form the site uses in URLs)."""
    return _CITY_SLUG.sub("-", city.strip().lower()).strip("-")


def id_from_url(url: str) -> str | None:
    """'https://www.huurwoningen.nl/huren/rotterdam/ea81e68d/zuidplein/' -> 'ea81e68d'."""
    path = _SCHEME_HOST.sub("", url).split("?")[0]
    m = DETAIL_PATH.match(path)
    return m.group(2) if m else None


def _text(el: LexborNode | None) -> str:
    """All text inside the element, whitespace collapsed."""
    return " ".join(el.text(separator=" ").split()) if el is not None else ""


def _number(value: object) -> float | None:
    """JSON-LD numbers can arrive as 915, 915.0 or "915"."""
    try:
        return float(value) if value not in (None, "") else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _int(value: object) -> int | None:
    n = _number(value)
    return int(n) if n is not None else None


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
    data = xml.encode() if isinstance(xml, str) else xml
    pairs = _scan_sitemap(data)
    if pairs is None:
        pairs = _sitemap_pairs_xml(data)

    entries: list[SitemapEntry] = []
    undated: list[SitemapEntry] = []
    match, match_path, fromiso, intern = _DETAIL_URL.match, DETAIL_PATH.match, datetime.fromisoformat, sys.intern
    zones: dict[object, tzinfo] = {}
    for loc, lastmod in pairs:
        m = match(loc) or match_path(loc)
        if not m:
            continue
        when: datetime | None = None
        if lastmod:
            try:
                when = fromiso(lastmod)
            except ValueError:
                pass
            else:
                if when.tzinfo is not None:
                    when = when.replace(tzinfo=zones.setdefault(when.utcoffset(), when.tzinfo))
        entry = SitemapEntry(id=m.group(2), url=loc, city=intern(m.group(1)), lastmod=when)
        (entries if when is not None else undated).append(entry)
    try:
        entries.sort(key=attrgetter("lastmod"), reverse=True)
    except TypeError:  # a mix of dates with and without a time zone can't be compared
        entries.sort(key=lambda e: e.lastmod.timestamp(), reverse=True)  # type: ignore[union-attr]
    entries += undated
    return entries


def _scan_sitemap(data: bytes) -> list[tuple[str, str | None]] | None:
    """(loc, lastmod) pairs found with one regex pass, or None if the file isn't the
    simple shape the regex expects (then the XML parser handles it)."""
    found = _SITEMAP_ENTRY.findall(data)
    if not found or len(found) != data.count(b"<loc>") or b"<![CDATA[" in data:
        return None
    if sum(1 for _, lastmod in found if lastmod) != data.count(b"<lastmod>"):
        return None
    out: list[tuple[str, str | None]] = []
    for loc_b, lastmod_b in found:
        loc = loc_b.decode()
        if "&" in loc:
            loc = _xml_unescape(loc, {"&quot;": '"', "&apos;": "'"})
        out.append((loc, lastmod_b.decode() if lastmod_b else None))
    return out


def _sitemap_pairs_xml(data: bytes) -> list[tuple[str, str | None]]:
    """(loc, lastmod) pairs via the XML parser: slower, handles any valid sitemap."""
    url_tag, loc_tag, lastmod_tag = f"{_SITEMAP_NS}url", f"{_SITEMAP_NS}loc", f"{_SITEMAP_NS}lastmod"
    out: list[tuple[str, str | None]] = []
    for url_el in ET.fromstring(data).iter(url_tag):
        lastmod = (url_el.findtext(lastmod_tag) or "").strip()
        out.append(((url_el.findtext(loc_tag) or "").strip(), lastmod or None))
    return out


def parse_search_page(html: str | bytes, base_url: str = BASE_URL) -> list[Listing]:
    """All listing cards on one search-results page."""
    tree = LexborHTMLParser(html)
    out: list[Listing] = []
    for card in tree.css("section.listing-search-item"):
        link = card.css_first("a.listing-search-item__link--title") or card.css_first("a[href*='/huren/']")
        href = link.attributes.get("href") if link is not None else None
        if not href:
            continue
        url = urljoin(base_url, href)
        listing_id = id_from_url(url) or card.attributes.get("data-listing-search-item-id")
        if not listing_id:
            continue
        postcode, city, hood = parse_location(_text(card.css_first(".listing-search-item__sub-title")))
        out.append(
            Listing(
                id=str(listing_id),
                url=url,
                title=_text(link),
                price=parse_euros(_text(card.css_first(".listing-search-item__price-main"))),
                area=parse_area(_text(card.css_first(".illustrated-features__item--surface-area"))),
                rooms=parse_int(_text(card.css_first(".illustrated-features__item--number-of-rooms"))),
                postcode=postcode,
                city=city,
                neighbourhood=hood,
                build_year=parse_int(_text(card.css_first(".illustrated-features__item--construction-period"))),
                is_new=card.css_first(".listing-label--new") is not None,
            )
        )
    return out


def _find_listing_jsonld(tree: LexborHTMLParser) -> dict:
    for script in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.text(deep=True) or "")
        except json.JSONDecodeError:
            continue
        nodes = data.get("@graph", [data]) if isinstance(data, dict) else data
        for node in nodes if isinstance(nodes, list) else [nodes]:
            types = node.get("@type", []) if isinstance(node, dict) else []
            if "RealEstateListing" in (types if isinstance(types, list) else [types]):
                return node
    return {}


def _next_dd(dt: LexborNode) -> LexborNode | None:
    """The next <dd> sibling (skipping text and other tags), like BeautifulSoup's find_next_sibling."""
    node = dt.next
    while node is not None and node.tag != "dd":
        node = node.next
    return node


def parse_detail_page(html: str | bytes, url: str) -> ListingDetails:
    """Everything on one listing's own page."""
    tree = LexborHTMLParser(html)
    ld = _find_listing_jsonld(tree)

    features: dict[str, str] = {}
    for dt in tree.css("dt.listing-features__term"):
        dd = _next_dd(dt)
        main = dd.css_first(".listing-features__main-description") if dd is not None else None
        label, value = _text(dt), _text(main if main is not None else dd)
        if label and value and label not in features:
            features[label] = value
    page: dict[str, str] = {}
    for label, value in features.items():
        name = LABELS.get(label.lower())
        if name and name not in page:
            page[name] = value

    address = _node(ld.get("address"))
    geo = _node(ld.get("geo"))

    loc_postcode, _, loc_hood = parse_location(_text(tree.css_first(".listing-detail-summary__location")))
    status = page.get("status")
    image = ld.get("image")
    description = _str(ld.get("description"))
    if not description:
        desc_el = tree.css_first(".listing-detail-description__content")
        if desc_el is not None:
            for title in desc_el.css(".listing-detail-description__title"):
                title.decompose()
            description = _text(desc_el) or None

    return ListingDetails(
        id=id_from_url(url) or url,
        url=url,
        title=_text(tree.css_first("h1.listing-detail-summary__title")) or _str(ld.get("name")) or "",
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

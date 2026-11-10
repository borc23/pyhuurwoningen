from datetime import date
from pathlib import Path

import pytest

import pyhuurwoningen as hw
from pyhuurwoningen import Huurwoningen, HuurwoningenError, Listing, ListingDetails
from pyhuurwoningen.parsing import (
    city_slug,
    parse_area,
    parse_detail_page,
    parse_euros,
    parse_location,
    parse_search_page,
)

FIX = Path(__file__).parent / "fixtures" / "huurwoningen"
SEARCH_HTML = (FIX / "search_rotterdam.html").read_text(encoding="utf-8")
DETAIL_HTML = (FIX / "detail_zuidplein.html").read_text(encoding="utf-8")
DETAIL_URL = "https://www.huurwoningen.nl/huren/rotterdam/ea81e68d/zuidplein/"


@pytest.mark.parametrize(
    "text,expected",
    [("€\xa0915 per maand", 915), ("€ 1.207 per maand", 1207), ("€ 915,-", 915), ("", None)],
)
def test_parse_euros(text, expected):
    assert parse_euros(text) == expected


def test_parse_area_and_location():
    assert parse_area("8,5 m²") == 8.5
    assert parse_location("3083 CR Rotterdam (Zuidplein)") == ("3083CR", "Rotterdam", "Zuidplein")
    assert parse_location("3083 CR (Zuidplein)") == ("3083CR", None, "Zuidplein")
    assert city_slug("Den Haag") == "den-haag"


def test_search_page():
    items = parse_search_page(SEARCH_HTML)
    assert len(items) == 3
    first, second = items[0], items[1]
    assert first.id == "ea81e68d"
    assert first.url == DETAIL_URL
    assert first.title == "Appartement Zuidplein"
    assert (first.price, first.area, first.rooms) == (915, 50.0, 3)
    assert (first.postcode, first.city, first.neighbourhood) == ("3083CR", "Rotterdam", "Zuidplein")
    assert first.is_new and not items[2].is_new
    assert second.price == 1207 and second.build_year == 1985
    assert str(first) == "Appartement Zuidplein | €915/month | 50 m² | 3 rooms | Zuidplein"


def test_detail_page():
    d = parse_detail_page(DETAIL_HTML, DETAIL_URL)
    assert isinstance(d, Listing)  # details are a Listing too
    assert d.id == "ea81e68d"
    assert d.title == "Appartement Zuidplein in Rotterdam"
    assert (d.price, d.area) == (915, 50)
    assert (d.rooms, d.bedrooms, d.bathrooms) == (3, 2, 1)
    assert (d.postcode, d.city) == ("3083CR", "Rotterdam")
    assert d.latitude == pytest.approx(51.8897, abs=1e-3)
    assert d.dwelling_type == "Appartement"
    assert d.property_type == "Galerijflat"
    assert d.status == "Te huur" and d.is_available is True
    assert d.available_from == "Per direct"
    assert d.offered_since == date(2026, 11, 9)
    assert d.description is not None
    assert d.description.startswith("Te huur: 3-kamerappartement")
    assert d.features["Prijs per m²"] == "€ 18"


def test_detail_page_without_jsonld_falls_back_to_labels():
    start = DETAIL_HTML.index('<script type="application/ld+json">')
    end = DETAIL_HTML.index("</script>", start) + len("</script>")
    d = parse_detail_page(DETAIL_HTML[:start] + DETAIL_HTML[end:], DETAIL_URL)
    assert (d.price, d.area, d.rooms, d.bedrooms) == (915, 50.0, 3, 2)
    assert d.postcode == "3083CR" and d.neighbourhood == "Zuidplein"
    assert d.description is not None
    assert d.description.startswith("Te huur: 3-kamerappartement")
    assert "Over deze woning" not in d.description


# --------------------------------------------------------------------------- #
# The Huurwoningen class, with a fake website instead of the internet
# --------------------------------------------------------------------------- #

class FakeHttp:
    """Stands in for the real HTTP client: serves saved pages, records what was asked."""

    def __init__(self, html: str = SEARCH_HTML, status: int = 200):
        self.html, self.status, self.calls = html, status, []

    def get(self, path, params=None):
        self.calls.append((path, params))
        if self.status >= 400:
            raise HuurwoningenError(f"HTTP {self.status}", self.status)
        return type("Response", (), {"text": self.html})()

    def close(self):
        pass


def make(http: FakeHttp) -> Huurwoningen:
    client = Huurwoningen()
    client._http = http  # type: ignore[assignment]
    return client


def test_search_stops_at_seen_listings():
    http = FakeHttp()
    new = make(http).search("Rotterdam", pages=5, skip_ids={"d54e87ad"})
    assert [s.id for s in new] == ["ea81e68d", "e58a3472"]
    assert len(http.calls) == 1  # hit a known listing on page 1, so no page 2
    assert http.calls[0] == ("/in/rotterdam/", {"sort": "published_at", "direction": "desc"})


def test_search_filters():
    homes = make(FakeHttp()).search("rotterdam", max_price=1000)
    assert homes and all(h.price is None or h.price <= 1000 for h in homes)
    assert "e58a3472" not in {h.id for h in homes}  # the €1,207 one


def test_unknown_city_gives_a_clear_error():
    with pytest.raises(HuurwoningenError, match="City 'atlantis' not found"):
        make(FakeHttp(status=404)).search("atlantis")


def test_get_details_accepts_listing_or_url():
    client = make(FakeHttp(html=DETAIL_HTML))
    listing = parse_search_page(SEARCH_HTML)[0]
    assert isinstance(client.get_details(listing), ListingDetails)
    assert client.get_details(DETAIL_URL).price == 915


def test_save_csv(tmp_path):
    path = tmp_path / "homes.csv"
    hw.save_csv(parse_search_page(SEARCH_HTML), str(path))
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].startswith("id,url,title,price,area,rooms")
    assert len(lines) == 4  # header + 3 homes


def test_public_api():
    assert callable(hw.search) and callable(hw.get_details)
    with Huurwoningen(user_agent="school-project/1.0") as client:
        assert client._http.user_agent == "school-project/1.0"


def test_impersonate_can_be_turned_off(monkeypatch):
    assert Huurwoningen()._http.impersonate == "chrome"
    assert Huurwoningen(impersonate="safari")._http.impersonate == "safari"
    monkeypatch.setenv("HW_IMPERSONATE", "none")
    assert Huurwoningen()._http.impersonate is None


def test_search_keeps_results_when_pages_run_out():
    class TwoPages(FakeHttp):
        def get(self, path, params=None):
            if params and params.get("page") == "3":
                self.calls.append((path, params))
                raise HuurwoningenError("HTTP 404", 404)
            return super().get(path, params)

    http = TwoPages()
    homes = make(http).search("zwolle", pages=5)
    assert homes  # pages 1-2 kept, no "City not found"
    assert len(http.calls) == 3  # stopped at the 404 on page 3


def test_import_does_not_build_a_client(monkeypatch):
    import importlib

    importlib.reload(hw)
    assert hw._default is None
    from pyhuurwoningen.parsing import parse_search_page as p

    assert p(SEARCH_HTML)


@pytest.mark.parametrize(
    "ld, expected",
    [
        ({"offers": [{"price": 915}]}, {"price": 915}),
        ({"address": "Zuidplein 1, Rotterdam"}, {"street": None}),
        ({"floorSize": 50}, {"area": 50.0}),
        ({"floorSize": [{"value": "50"}]}, {"area": 50.0}),
        ({"numberOfRooms": [3]}, {"rooms": 3}),
        ({"numberOfRooms": []}, {"rooms": None}),
        ({"geo": [{"latitude": "51.9", "longitude": 4.5}]}, {"latitude": 51.9, "longitude": 4.5}),
        ({"offers": "915", "geo": "x", "description": ["a"], "name": 7}, {"price": None, "description": None}),
    ],
)
def test_detail_tolerates_odd_jsonld_shapes(ld, expected):
    import json

    ld = {"@type": "RealEstateListing", **ld}
    html = f'<script type="application/ld+json">{json.dumps(ld)}</script>'
    details = parse_detail_page(html, DETAIL_URL)
    for field, value in expected.items():
        assert getattr(details, field) == value


class PagedHttp(FakeHttp):
    """Serves a different list of ids per page number."""

    def __init__(self, pages_ids):
        super().__init__()
        self.pages_ids = pages_ids
        self.cards = {c.id: c for c in parse_search_page(SEARCH_HTML)}

    def get(self, path, params=None):
        self.calls.append((path, params))
        page = int((params or {}).get("page", 1))
        ids = self.pages_ids[page - 1] if page <= len(self.pages_ids) else []
        html = "".join(
            f'<section class="listing-search-item"><a class="listing-search-item__link--title" '
            f'href="/huren/rotterdam/{i}/x/">{i}</a></section>'
            for i in ids
        )
        return type("Response", (), {"text": html})()


def test_promoted_seen_home_does_not_stop_reading():
    http = PagedHttp([["aaa1", "bbb2", "ccc3"], ["ddd4", "aaa1"]])  # aaa1 pinned on top
    homes = make(http).search("rotterdam", pages=5, skip_ids={"aaa1"})
    assert [h.id for h in homes] == ["bbb2", "ccc3", "ddd4"]
    assert len(http.calls) == 2  # page 2 ends in a seen home: stop there


def test_home_shifted_to_next_page_is_returned_once():
    http = PagedHttp([["aaa1", "bbb2", "ccc3"], ["ccc3", "ddd4"]])
    homes = make(http).search("rotterdam", pages=2)
    assert [h.id for h in homes] == ["aaa1", "bbb2", "ccc3", "ddd4"]


def test_neighbourhood_prefers_page_over_jsonld_region():
    # schema.org addressRegion means province; the page's location line has the neighbourhood.
    html = DETAIL_HTML.replace('"addressRegion":"Zuidplein"', '"addressRegion":"Zuid-Holland"')
    assert '"Zuid-Holland"' in html
    assert parse_detail_page(html, DETAIL_URL).neighbourhood == "Zuidplein"


# --------------------------------------------------------------------------- #
# Sitemap: listing_index() and download()
# --------------------------------------------------------------------------- #

SITEMAP_XML = (FIX / "sitemap_listings.xml").read_text(encoding="utf-8")


class SiteHttp:
    """The sitemap, plus the saved detail page for every listing except `gone` (404) and `blocked` (403)."""

    def __init__(self, gone=(), blocked=()):
        self.gone, self.blocked, self.calls = set(gone), set(blocked), []

    def get(self, path, params=None):
        from pyhuurwoningen.parsing import id_from_url

        self.calls.append(path)
        if path == "/sitemap/listings.xml":
            return type("Response", (), {"text": SITEMAP_XML})()
        listing_id = id_from_url(path)
        if listing_id in self.gone:
            raise HuurwoningenError("HTTP 404", 404)
        if listing_id in self.blocked:
            raise HuurwoningenError("HTTP 403", 403)
        return type("Response", (), {"text": DETAIL_HTML})()

    def close(self):
        pass


def test_parse_sitemap():
    from pyhuurwoningen.parsing import parse_sitemap

    entries = parse_sitemap(SITEMAP_XML)
    assert [e.id for e in entries] == ["ea81e68d", "0a1b2c3d", "d54e87ad", "e58a3472"]  # newest first, bad date last
    assert entries[0].city == "rotterdam" and entries[1].city == "den-haag"
    assert entries[0].url == DETAIL_URL
    assert entries[0].lastmod.isoformat() == "2026-11-10T13:17:10+01:00"
    assert entries[-1].lastmod is None
    assert all("/in/" not in e.url for e in entries)  # non-listing URLs left out


def test_listing_index_filters_by_city():
    http = SiteHttp()
    client = make(http)
    assert [e.id for e in client.listing_index("Den Haag")] == ["0a1b2c3d"]
    assert len(client.listing_index()) == 4
    assert client.cities() == {"rotterdam": 3, "den-haag": 1}
    assert http.calls == ["/sitemap/listings.xml"]  # downloaded once, then reused


def test_sitemap_is_fetched_again_after_a_minute(monkeypatch):
    import pyhuurwoningen.client as client_module

    now = [1000.0]
    monkeypatch.setattr(client_module.time, "monotonic", lambda: now[0])
    http = SiteHttp()
    client = make(http)
    client.listing_index()
    now[0] += 61
    client.listing_index()
    assert http.calls == ["/sitemap/listings.xml"] * 2


def test_download_skips_seen_and_gone_listings():
    http = SiteHttp(gone={"e58a3472"})
    homes = list(make(http).download("rotterdam", skip_ids={"d54e87ad"}))
    assert [h.id for h in homes] == ["ea81e68d"]
    assert isinstance(homes[0], ListingDetails) and homes[0].price == 915
    assert http.calls == [
        "/sitemap/listings.xml",
        "https://www.huurwoningen.nl/huren/rotterdam/ea81e68d/zuidplein/",
        "https://www.huurwoningen.nl/huren/rotterdam/e58a3472/coolsingel/",
    ]  # the seen one isn't fetched at all


def test_download_fetches_one_home_at_a_time():
    http = SiteHttp()
    homes = make(http).download("rotterdam")
    assert http.calls == []  # nothing until you start reading
    next(homes)
    assert len(http.calls) == 2  # sitemap + one detail page


def test_download_stops_when_blocked_but_keeps_what_it_got():
    got = []
    with pytest.raises(HuurwoningenError):
        for home in make(SiteHttp(blocked={"d54e87ad"})).download("rotterdam"):
            got.append(home.id)
    assert got == ["ea81e68d"]


# --------------------------------------------------------------------------- #
# Helpers for users: schema() and cities()
# --------------------------------------------------------------------------- #

def test_schema_describes_every_field():
    import dataclasses

    from pyhuurwoningen.schema import MODELS

    for model in MODELS:
        s = hw.schema(model)
        names = [f.name for f in dataclasses.fields(model)]
        assert list(s["properties"]) == names == s["required"]
        assert all(p["description"] for p in s["properties"].values())
    props = hw.schema(ListingDetails)["properties"]
    assert props["price"]["type"] == ["integer", "null"]
    assert props["id"]["type"] == "string"
    assert props["offered_since"] == {**props["offered_since"], "type": ["string", "null"], "format": "date"}
    assert props["features"]["additionalProperties"] == {"type": "string"}
    with pytest.raises(ValueError):
        hw.schema(dict)


def test_real_output_matches_schema():
    import json

    jsonschema = pytest.importorskip("jsonschema")
    doc = hw.schema()
    jsonschema.Draft202012Validator.check_schema(doc)
    rows = [*parse_search_page(SEARCH_HTML), parse_detail_page(DETAIL_HTML, DETAIL_URL)]
    for row in rows:  # exactly what `huurwoningen --json` prints
        jsonschema.validate(json.loads(json.dumps(row.to_dict(), default=str)), doc)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"id": "x", "url": "u", "title": "t", "price": "cheap"}, doc)


def test_cities_counts_listings():
    assert make(SiteHttp()).cities() == {"rotterdam": 3, "den-haag": 1}


def test_typo_in_city_suggests_the_right_one(caplog):
    assert list(make(SiteHttp()).download("rotterdm")) == []
    assert "Did you mean 'rotterdam'?" in caplog.text


def test_unknown_city_in_search_suggests_close_match():
    class NotFound(SiteHttp):
        def get(self, path, params=None):
            if path.startswith("/in/"):
                self.calls.append(path)
                raise HuurwoningenError("HTTP 404", 404)
            return super().get(path, params)

    with pytest.raises(HuurwoningenError, match="Did you mean 'den-haag'"):
        make(NotFound()).search("den-hag")

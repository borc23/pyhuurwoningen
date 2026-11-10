import json
from pathlib import Path

from pyhuurwoningen import cli
from pyhuurwoningen.parsing import parse_detail_page, parse_search_page

FIX = Path(__file__).parent / "fixtures" / "huurwoningen"
SEARCH = parse_search_page((FIX / "search_rotterdam.html").read_text(encoding="utf-8"))
DETAIL_HTML = (FIX / "detail_zuidplein.html").read_text(encoding="utf-8")


class FakeHuurwoningen:
    def __init__(self, **kw):
        self.detail_calls = 0

    def search(self, city, pages=1, skip_ids=None):
        return [h for h in SEARCH if h.id not in (skip_ids or set())]

    def get_details(self, listing):
        self.detail_calls += 1
        return parse_detail_page(DETAIL_HTML, listing.url)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


def test_details_keeps_all_homes(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "Huurwoningen", FakeHuurwoningen)
    state, out_csv = tmp_path / "seen.json", tmp_path / "homes.csv"
    assert cli.main(["rotterdam", "--details", "1", "--json", "--state", str(state), "--csv", str(out_csv)]) == 0

    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert [r["id"] for r in rows] == [h.id for h in SEARCH]  # all 3, not just 1
    assert "description" in rows[0] and "description" not in rows[1]  # only the first has details
    assert len(out_csv.read_text(encoding="utf-8-sig").splitlines()) == 1 + len(SEARCH)
    assert set(json.loads(state.read_text())) == {h.id for h in SEARCH}


def run_with_failing_details(monkeypatch, tmp_path, status):
    from pyhuurwoningen import HuurwoningenError

    class Failing(FakeHuurwoningen):
        def get_details(self, listing):
            if self.detail_calls == 1:  # the 2nd detail request fails
                self.detail_calls += 1
                raise HuurwoningenError(f"HTTP {status}", status)
            return super().get_details(listing)  # counts the call

    made = []
    monkeypatch.setattr(cli, "Huurwoningen", lambda **kw: made.append(Failing()) or made[-1])
    state = tmp_path / "seen.json"
    code = cli.main(["rotterdam", "--details", "3", "--json", "--state", str(state)])
    return code, made[0].detail_calls, state


def test_rate_limited_details_keep_results(monkeypatch, tmp_path, capsys):
    code, calls, state = run_with_failing_details(monkeypatch, tmp_path, 429)
    out, err = capsys.readouterr()
    rows = [json.loads(line) for line in out.splitlines() if line]
    assert code == 1 and "no details for" in err
    assert [r["id"] for r in rows] == [h.id for h in SEARCH]  # every home still printed
    assert "description" in rows[0] and "description" not in rows[1]
    assert calls == 2  # stopped after the 429 instead of hammering on
    assert set(json.loads(state.read_text())) == {h.id for h in SEARCH}


def test_removed_listing_skips_only_that_one(monkeypatch, tmp_path, capsys):
    code, calls, _ = run_with_failing_details(monkeypatch, tmp_path, 404)
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert code == 1 and calls == 3
    assert "description" in rows[2]  # carried on past the removed listing


# --------------------------------------------------------------------------- #
# --all: every home in the city, via the sitemap
# --------------------------------------------------------------------------- #

class DownloadingFake(FakeHuurwoningen):
    """download() yields full details for the 3 saved homes; `fail_at` raises a 403 there."""

    fail_at: int | None = None

    def download(self, city=None, skip_ids=None):
        from pyhuurwoningen import HuurwoningenError

        for i, h in enumerate(h for h in SEARCH if h.id not in (skip_ids or set())):
            if i == self.fail_at:
                raise HuurwoningenError("HTTP 403", 403)
            yield parse_detail_page(DETAIL_HTML, h.url)


def run_all(monkeypatch, *args):
    monkeypatch.setattr(cli, "Huurwoningen", DownloadingFake)
    return cli.main(["rotterdam", "--all", "--json", *args])


def test_all_with_limit_continues_next_time(monkeypatch, tmp_path, capsys):
    state = tmp_path / "seen.json"
    assert run_all(monkeypatch, "--limit", "2", "--state", str(state)) == 0
    first = [json.loads(line)["id"] for line in capsys.readouterr().out.splitlines() if line]
    assert first == [h.id for h in SEARCH[:2]]
    assert set(json.loads(state.read_text())) == {h.id for h in SEARCH[:2]}

    assert run_all(monkeypatch, "--state", str(state)) == 0
    second = [json.loads(line)["id"] for line in capsys.readouterr().out.splitlines() if line]
    assert second == [SEARCH[2].id]  # only the one not fetched before
    assert set(json.loads(state.read_text())) == {h.id for h in SEARCH}


def test_all_stopped_by_block_keeps_and_remembers_homes(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(DownloadingFake, "fail_at", 1)
    state, out_csv = tmp_path / "seen.json", tmp_path / "homes.csv"
    assert run_all(monkeypatch, "--state", str(state), "--csv", str(out_csv)) == 1
    out, err = capsys.readouterr()
    assert "Stopped after 1 home(s)" in err
    assert [json.loads(line)["id"] for line in out.splitlines() if line] == [SEARCH[0].id]
    assert set(json.loads(state.read_text())) == {SEARCH[0].id}
    assert len(out_csv.read_text(encoding="utf-8-sig").splitlines()) == 2


def test_all_applies_filters_but_remembers_every_home(monkeypatch, tmp_path, capsys):
    state = tmp_path / "seen.json"
    assert run_all(monkeypatch, "--max-price", "100", "--state", str(state)) == 0
    assert capsys.readouterr().out.strip() == ""  # the saved home costs €915
    assert set(json.loads(state.read_text())) == {h.id for h in SEARCH}


def test_schema_needs_no_city_or_internet(monkeypatch, capsys):
    monkeypatch.setattr(cli, "Huurwoningen", None)  # would crash if used
    assert cli.main(["--schema"]) == 0
    assert json.loads(capsys.readouterr().out)["title"] == "huurwoningen --json line"


def test_cities(monkeypatch, capsys):
    class Fake(FakeHuurwoningen):
        def cities(self):
            return {"amsterdam": 1568, "rotterdam": 645}

    monkeypatch.setattr(cli, "Huurwoningen", Fake)
    assert cli.main(["--cities", "--json"]) == 0
    out = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert out == [{"city": "amsterdam", "homes": 1568}, {"city": "rotterdam", "homes": 645}]


def test_city_is_required_otherwise():
    import pytest

    with pytest.raises(SystemExit):
        cli.main([])

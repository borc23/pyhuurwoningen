# pyhuurwoningen

Search rental homes on [huurwoningen.nl](https://www.huurwoningen.nl) from Python, in a few lines.

```python
import pyhuurwoningen as hw

homes = hw.search("rotterdam", max_price=1500)
for home in homes:
    print(home)
```

```
Appartement Zuidplein | €915/month | 50 m² | 3 rooms | Zuidplein
Appartement Groenendaal | €1207/month | 53 m² | 2 rooms | Stadsdriehoek
Appartement Hanoistraat | €1432/month | 76 m² | 3 rooms | Katendrecht
```

## Install

```bash
pip install pyhuurwoningen
```

or, with uv:

```bash
uv add pyhuurwoningen
```

Then in Python: `import pyhuurwoningen as hw`.

Works on Python 3.10 and newer.

## The whole API

| What | Gives you |
|---|---|
| `hw.search(city, max_price=, min_rooms=, min_area=, pages=1)` | a list of `Listing`s, newest first |
| `hw.get_details(listing)` | a `ListingDetails` with everything on the home's page |
| `hw.download(city, skip_ids=)` | every home in the city as `ListingDetails`, one at a time |
| `hw.listing_index(city)` | every home's id, URL and last change, in one request |
| `hw.cities()` | every city with homes for rent, and how many |
| `hw.schema()` | JSON Schema of the results (no internet) |
| `hw.save_csv(listings, "file.csv")` | a file you can open in Excel or Google Sheets |
| `hw.filter_listings(listings, max_price=, min_rooms=, min_area=)` | the listings that match |

That's all you need. In Python, `help(hw.search)` shows the details.

### What's in a `Listing`

| Field | Example | Meaning |
|---|---|---|
| `id` | `"ea81e68d"` | unique id of the home |
| `url` | `"https://www.huurwoningen.nl/huren/..."` | link to the page |
| `title` | `"Appartement Zuidplein"` | |
| `price` | `915` | rent in euros per month |
| `area` | `50.0` | size in m² |
| `rooms` | `3` | |
| `postcode`, `city`, `neighbourhood` | `"3083CR"`, `"Rotterdam"`, `"Zuidplein"` | |
| `build_year` | `1985` | |
| `is_new` | `True` | marked "new" on the site |

Any of these can be `None` if the site doesn't show it.

### What's extra in `ListingDetails`

A `ListingDetails` has all the fields above, plus:
`street`, `latitude`, `longitude`, `deposit`, `service_costs`, `bedrooms`, `bathrooms`,
`dwelling_type`, `property_type`, `construction_type`, `interior`, `energy_label`,
`status`, `is_available`, `available_from`, `offered_since`, `description`, `image_url`,
and `features` (every label and value on the page, exactly as shown).

Use `.to_dict()` on either one to get a plain dictionary.

## Examples

The [examples/](examples/) folder goes step by step:

1. [first search](examples/1_first_search.py)
2. [details of one home](examples/2_details_of_one_home.py)
3. [save to Excel](examples/3_save_to_excel.py)
4. [cheapest per m², average rent](examples/4_cheapest_per_m2.py)

## When something goes wrong

Everything raises a `HuurwoningenError` with a readable message:

```python
try:
    homes = hw.search("atlantis")
except hw.HuurwoningenError as e:
    print(e)   # City 'atlantis' not found on huurwoningen.nl. Check the spelling, e.g. 'den-haag'.
```

## Command line

```bash
huurwoningen rotterdam --max-price 1500 --min-rooms 2
huurwoningen utrecht --pages 2 --csv utrecht.csv
huurwoningen rotterdam --details 3 --json          # full details as JSON lines
huurwoningen rotterdam --state seen.json           # only homes you haven't seen yet
```

Run the last one every 15 minutes (cron, Task Scheduler) for a simple new-homes alert.

### Which cities, and what comes out

```bash
huurwoningen --cities              # every city with homes for rent, most first
huurwoningen --schema              # JSON Schema of one --json line (no internet)
```

Use the city names exactly as `--cities` prints them. A typo gets a suggestion
("Did you mean 'rotterdam'?"). `--schema` is for loading the `--json` output into
another program or a database: every field, its type, whether it can be `null`, and
what it means. In Python: `hw.schema()`, or `hw.schema(ListingDetails)` for one model.

### Every home in a city

```bash
huurwoningen rotterdam --all --state seen.json --json >> rotterdam.jsonl
huurwoningen rotterdam --all --state seen.json --limit 50   # at most 50 per run
```

`--all` reads the site's list of all listings (its sitemap) and fetches each home's
page, so you get full details for the whole city, not just the first search pages.
That is one request per home: Rotterdam has ~650 homes. Homes are printed as they arrive. With `--state`, a later run
fetches only homes it hasn't seen, and a run stopped by an error or Ctrl+C carries on
where it left off. That makes it a good new-homes alert too: after the first run, a
check costs one sitemap request plus one per new home.

## More control

`hw.search` and `hw.get_details` share one ready-made connection. To choose your own
settings, make a `Huurwoningen` object:

```python
from pyhuurwoningen import Huurwoningen

with Huurwoningen(user_agent="my-school-project/1.0") as site:
    seen = {"ea81e68d"}
    new_homes = site.search("delft", skip_ids=seen)   # stops at the first home you've seen
```

### Downloading everything

`download` is a generator: homes arrive one by one, so save each as it comes. Pass the
ids you already have as `skip_ids` to get only new ones, or to resume after a stop.

```python
import json
from pyhuurwoningen import Huurwoningen

seen = set()  # load these from your database or file
with Huurwoningen() as site, open("homes.jsonl", "a", encoding="utf-8") as out:
    for home in site.download("rotterdam", skip_ids=seen):   # no city: the whole country
        out.write(json.dumps(home.to_dict(), default=str) + "\n")
        seen.add(home.id)
```

Listings that have been taken down are skipped. If the site blocks or rate-limits you,
`download` raises `HuurwoningenError`; everything you saved so far stays saved.

`user_agent` can also be set with the `HW_USER_AGENT` environment variable.

### Browser impersonation

huurwoningen.nl is behind Cloudflare, which rejects plain Python HTTP clients.
Requests go through [curl_cffi](https://github.com/lexiforest/curl_cffi), which sends
the TLS (JA3/JA4) and HTTP/2 fingerprint of a real browser. By default it uses the
newest Chrome profile in the installed curl_cffi version (`impersonate="chrome"`).

- In this mode the User-Agent, `sec-ch-ua` client hints and header order all come from
  the browser profile, so they match the fingerprint. Your `user_agent` is then only
  used for robots.txt rules. Don't add your own User-Agent header: a UA that doesn't
  match the TLS fingerprint is the first thing bot checks look for.
- Keep curl_cffi up to date (`uv lock --upgrade-package curl_cffi`). An old browser
  version stands out among visitors on current browsers.
- Choose another profile with `Huurwoningen(impersonate="safari")` or
  `HW_IMPERSONATE=firefox`. `impersonate="none"` turns it off.
- This gets past Cloudflare's passive fingerprint check, not a JavaScript (managed)
  challenge. That still raises a `HuurwoningenError`.

Got HTML saved already? The parsers work offline:

```python
from pyhuurwoningen.parsing import parse_search_page, parse_detail_page
```

## How it works

| File | What it does |
|---|---|
| [`models.py`](src/pyhuurwoningen/models.py) | `Listing`, `ListingDetails` (plain dataclasses), `save_csv` |
| [`parsing.py`](src/pyhuurwoningen/parsing.py) | HTML/XML in, `Listing`/`SitemapEntry` out. Pure functions, no internet needed |
| [`client.py`](src/pyhuurwoningen/client.py) | `Huurwoningen`: builds the URLs, pages through results, filters |
| [`_http.py`](src/pyhuurwoningen/_http.py) | polite downloading: robots.txt, retries, browser fingerprint, readable errors |
| [`schema.py`](src/pyhuurwoningen/schema.py) | JSON Schema built from the models, with a description per field |
| [`cli.py`](src/pyhuurwoningen/cli.py) | the `huurwoningen` command |

- **Search:** `/in/<city>/?sort=published_at&direction=desc`, newest first.
- **All listings:** `/sitemap/listings.xml`, every listing in the country with its last
  change (~8,000, one ~2.5 MB file). `lastmod` changes on edits too, so new homes are
  found by id, not by date.
- **Details:** read from the page's schema.org JSON-LD first, then from its label/value list.
- **Ids:** the short id in the URL (`/huren/rotterdam/ea81e68d/zuidplein/` → `ea81e68d`).

## Develop

```bash
uv sync          # set up .venv
uv run pytest    # offline tests, using saved pages in tests/fixtures/
```

## Known gaps

- Labels for interior, energy label, build year, deposit and service costs are mapped
  but weren't on the saved test page. Check `features` on a few real listings and adjust
  `LABELS` in `parsing.py` if the wording differs.
- Filters run in Python after downloading; the site's own filter URLs aren't used.
- If Cloudflare escalates to a JavaScript challenge, requests fail until it relaxes;
  solving it would need a headless browser, which this library deliberately avoids.

## Fair use

This is an unofficial tool for personal use and learning. It is not made by or
connected to huurwoningen.nl. Respect the website's terms and don't republish its data.

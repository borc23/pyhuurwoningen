"""Try the scraper. Pick what to run, so you only wait for what you test:

    uv run python local.py                     # everything (~5 requests)
    uv run python local.py search details      # just these parts
    uv run python local.py schema              # instant, no internet

Parts: cities, search, details, download, schema.
"""

import logging
import sys
from itertools import islice

import pyhuurwoningen as hw

logging.basicConfig(level=logging.INFO)  # shows each request; remove to make it quiet

CITY = "rotterdam"
PARTS = ["cities", "search", "details", "download", "schema"]

wanted = sys.argv[1:] or PARTS
unknown = set(wanted) - set(PARTS)
if unknown:
    sys.exit(f"Unknown part(s): {', '.join(unknown)}. Choose from: {', '.join(PARTS)}")

with hw.Huurwoningen() as site:
    if "cities" in wanted:  # 1 request (the sitemap; download reuses it)
        cities = site.cities()
        print(f"\n{len(cities)} cities, top 5: {dict(islice(cities.items(), 5))}")

    homes = []
    if "search" in wanted or "details" in wanted:  # 1 request
        homes = site.search(CITY, max_price=1500)
        print(f"\n{len(homes)} homes in {CITY} up to €1500:")
        for home in homes[:5]:
            print(" ", home)

    if "details" in wanted and homes:  # 1 request
        details = site.get_details(homes[0])
        print(f"\nDetails of {details.url}:")
        print(f"  status={details.status}  deposit={details.deposit}  interior={details.interior}")
        print(f"  {details.street}, lat/lon={details.latitude},{details.longitude}")

    if "download" in wanted:  # sitemap (if not fetched yet) + 2 detail pages
        print(f"\nNewest homes in {CITY} from the sitemap:")
        for home in islice(site.download(CITY), 2):
            print(" ", home)

if "schema" in wanted:  # no internet
    print("\nListingDetails fields:", ", ".join(hw.schema(hw.ListingDetails)["properties"]))

"""Command line: `huurwoningen rotterdam --max-price 1500`  (see `huurwoningen --help`)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable
from itertools import islice
from pathlib import Path

from pyhuurwoningen import Huurwoningen, HuurwoningenError, Listing, filter_listings, save_csv, schema


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="huurwoningen",
        description="Search rental homes on huurwoningen.nl (newest first).",
    )
    p.add_argument("city", nargs="?", help="e.g. rotterdam, amsterdam, den-haag (see --cities)")
    p.add_argument("--max-price", type=int, help="maximum rent in euros per month")
    p.add_argument("--min-rooms", type=int, help="minimum number of rooms")
    p.add_argument("--min-area", type=float, help="minimum size in m²")
    p.add_argument("--pages", type=int, default=1, help="result pages to read (~30 homes each)")
    p.add_argument("--details", type=int, default=0, metavar="N", help="also fetch full details of the first N (all homes are still listed)")
    p.add_argument(
        "--all", action="store_true",
        help="every home in the city with full details, from the site's list of all listings "
             "(one request per home; with --state only homes not seen before)",
    )
    p.add_argument("--limit", type=int, metavar="N", help="with --all: stop after N homes (--state continues next time)")
    p.add_argument("--csv", type=Path, help="save the results to this CSV file")
    p.add_argument("--json", action="store_true", help="print JSON lines instead of a table")
    p.add_argument("--state", type=Path, help="JSON file of seen ids: only show homes not seen before")
    p.add_argument("--cities", action="store_true", help="list every city with homes for rent, and how many (one request)")
    p.add_argument("--schema", action="store_true", help="print the JSON Schema of the --json output and exit (no internet)")
    p.add_argument("-v", "--verbose", action="store_true", help="show each request")
    args = p.parse_args(argv)
    if args.schema:
        print(json.dumps(schema(), indent=2, ensure_ascii=False))
        return 0
    if args.cities:
        return _print_cities(args)
    if not args.city:
        p.error("give a city, e.g. `huurwoningen rotterdam` (see `huurwoningen --cities`)")

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    seen: set[str] = set(json.loads(args.state.read_text())) if args.state and args.state.exists() else set()

    def show(h: Listing) -> None:
        print(json.dumps(h.to_dict(), default=str, ensure_ascii=False) if args.json else f"{h}  {h.url}", flush=True)

    fetched: set[str] = set()  # ids read this run, for --state
    homes: list[Listing] = []
    shown = 0
    failed = False
    try:
        with Huurwoningen() as hw:
            if args.all:
                # Keep matching homes only if --csv needs them: a whole-country run would
                # otherwise hold every home's full details in memory until the end.
                failed, shown = _download(hw, args, seen, fetched, homes if args.csv else None, show)
            else:
                homes, everything, failed = _search(hw, args, seen)
                fetched = {h.id for h in everything}
                for h in homes:
                    show(h)
                shown = len(homes)
    except (HuurwoningenError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"\n{shown} home(s)", file=sys.stderr)

    if args.csv and homes:
        save_csv(homes, str(args.csv))
        print(f"Saved to {args.csv}", file=sys.stderr)
    if args.state:
        args.state.write_text(json.dumps(sorted(seen | fetched)))
    return 1 if failed else 0  # results are saved, but let scripts notice


def _print_cities(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    try:
        with Huurwoningen() as hw:
            counts = hw.cities()
    except (HuurwoningenError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    for city, n in counts.items():
        print(json.dumps({"city": city, "homes": n}) if args.json else f"{n:6}  {city}")
    print(f"\n{len(counts)} cities, {sum(counts.values())} homes", file=sys.stderr)
    return 0


def _search(hw: Huurwoningen, args: argparse.Namespace, seen: set[str]) -> tuple[list[Listing], list[Listing], bool]:
    """Search pages, plus details for the first --details homes. Returns (shown, all found, failed)."""
    # Filters are applied here, so homes hidden by them are still marked as seen.
    everything = hw.search(args.city, pages=args.pages, skip_ids=seen)
    homes: list[Listing] = filter_listings(everything, args.max_price, args.min_rooms, args.min_area)
    # Details replace the first N; the rest stay in the list as they were.
    # A failed detail page keeps the plain listing, so the search isn't wasted.
    failed = False
    for i, home in enumerate(homes[: args.details]):
        try:
            homes[i] = hw.get_details(home)
        except HuurwoningenError as e:
            failed = True
            print(f"Warning: no details for {home.url}: {e}", file=sys.stderr)
            if e.status not in (404, 410):
                break  # rate-limited or blocked: more requests now won't help
    return homes, everything, failed


def _download(
    hw: Huurwoningen,
    args: argparse.Namespace,
    seen: set[str],
    fetched: set[str],
    keep: list[Listing] | None,
    show: Callable[[Listing], None],
) -> tuple[bool, int]:
    """--all: fetch every (new) home, showing each as it arrives.

    Ids go into `fetched` as homes come (so a stop by error or Ctrl+C keeps them for
    --state), and matching homes into `keep` if given. Returns (stopped early, shown).
    """
    shown = 0
    try:
        for home in islice(hw.download(args.city, skip_ids=seen), args.limit):
            fetched.add(home.id)
            if filter_listings([home], args.max_price, args.min_rooms, args.min_area):
                show(home)
                shown += 1
                if keep is not None:
                    keep.append(home)
    except HuurwoningenError as e:
        if not fetched:
            raise
        print(f"Stopped after {len(fetched)} home(s): {e}", file=sys.stderr)
        return True, shown
    except KeyboardInterrupt:
        print(f"\nStopped after {len(fetched)} home(s); saving what we have.", file=sys.stderr)
        return True, shown
    return False, shown


if __name__ == "__main__":
    raise SystemExit(main())

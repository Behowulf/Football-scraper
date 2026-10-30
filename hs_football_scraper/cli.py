"""Command-line entry point: ``python -m hs_football_scraper --help``."""

from __future__ import annotations

import argparse
import logging
import sys

from .client import FetchError, Fetcher
from .db import Database
from .export import export
from .scraper import rankings_url, scrape_profiles, scrape_rankings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="hs_football_scraper",
        description="Build a database of high school football prospects from 247Sports composite rankings.",
    )
    ap.add_argument("--year", type=int, default=2027, help="recruiting class (default: 2027)")
    ap.add_argument("--db", default="data/prospects.db", help="SQLite database path")
    ap.add_argument("--out", default="data/export", help="folder for CSV/Excel/JSON exports")
    ap.add_argument("--max-pages", type=int, help="stop after this many rankings pages (50 players each)")
    ap.add_argument("--limit", type=int, help="only scrape the top N players")
    ap.add_argument("--list-only", action="store_true", help="skip individual player profile pages")
    ap.add_argument("--no-interests", action="store_true", help="skip offers/visits pages")
    ap.add_argument("--refresh", action="store_true", help="re-scrape profiles already in the database")
    ap.add_argument("--delay", type=float, default=1.5, help="seconds between requests (default: 1.5)")
    ap.add_argument("--cache", default="data/cache", help="cache profile pages here ('' to disable)")
    ap.add_argument("--no-browser", action="store_true",
                    help="use plain HTTP requests instead of headless Chromium (247Sports currently refuses these)")
    ap.add_argument("--export-only", action="store_true", help="just re-export the existing database")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    log = logging.getLogger("hs_football_scraper")

    if not args.export_only:
        db = Database(args.db)
        fetcher = Fetcher(delay=args.delay, cache_dir=args.cache or None, use_browser=not args.no_browser)
        run = db.start_run(args.year, rankings_url(args.year))
        count = errors = 0
        try:
            count = scrape_rankings(fetcher, db, args.year, args.max_pages, args.limit)
            log.info("Stored %d prospects from the rankings list", count)
            if not args.list_only:
                errors = scrape_profiles(fetcher, db, args.year, interests=not args.no_interests,
                                         refresh=args.refresh, limit=args.limit)
        except FetchError as exc:
            log.error("%s", exc)
            errors += 1
        except KeyboardInterrupt:
            log.warning("Interrupted; progress so far is saved. Re-run to resume.")
        finally:
            db.finish_run(run, count, errors)
            db.close()
            fetcher.close()

    for path in export(args.db, args.out):
        log.info("Wrote %s", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

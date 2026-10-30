"""Orchestrates a full scrape: rankings list -> player profiles -> recruiting interests."""

from __future__ import annotations

import logging

from .client import FetchError, Fetcher
from .db import Database
from .parsers import next_page_url, parse_interests_page, parse_profile_page, parse_rankings_page

log = logging.getLogger(__name__)

RANKINGS_URL = "https://247sports.com/season/{year}-football/compositerecruitrankings/?InstitutionGroup=HighSchool"


def rankings_url(year: int) -> str:
    return RANKINGS_URL.format(year=year)


def scrape_rankings(fetcher: Fetcher, db: Database, year: int, max_pages: int | None = None,
                    limit: int | None = None) -> int:
    """Walk every page of the composite rankings list (50 per page) and store each prospect."""
    seen: set[int] = set()
    url: str | None = rankings_url(year)
    page = 0
    while url and (max_pages is None or page < max_pages):
        page += 1
        html = fetcher.get(url, use_cache=False)
        players = parse_rankings_page(html, year)
        if page == 1 and not players:
            raise FetchError(
                f"No players found on {url}. The page layout may have changed, or the site returned an "
                "error page instead of the rankings."
            )
        new = [p for p in players if p["player_id"] not in seen]
        if not new:
            break
        for p in new:
            if limit is not None and len(seen) >= limit:
                break
            seen.add(p["player_id"])
            db.upsert_player(p, source="list")
            db.upsert_ranking(p["player_id"], year, {**p, "source": "247Sports Composite"})
            if p.get("rank_247") is not None:
                db.upsert_ranking(p["player_id"], year, {"source": "247Sports", "national_rank": p["rank_247"]})
        db.commit()
        log.info("Rankings page %d done (%d players so far)", page, len(seen))
        if limit is not None and len(seen) >= limit:
            break
        url = next_page_url(html)
    return len(seen)


def scrape_profiles(fetcher: Fetcher, db: Database, year: int, interests: bool = True,
                    refresh: bool = False, limit: int | None = None) -> int:
    """Visit each stored player's profile (and recruiting-interest page) for full details."""
    rows = db.players_needing_profiles(year, refresh=refresh)
    if limit is not None:
        rows = rows[:limit]
    errors = 0
    for n, row in enumerate(rows, 1):
        pid, url = row["player_id"], row["profile_url"]
        try:
            result = parse_profile_page(fetcher.get(url))
            player = result["player"]
            player.pop("class_year", None)  # trust the class from the rankings list
            interests_url = player.get("interests_url") or row["interests_url"]
            db.upsert_player({"player_id": pid, **player}, source="profile", extra=result["extra"])
            for r in result["rankings"]:
                db.upsert_ranking(pid, year, r)
            db.replace_timeline(pid, result["timeline"])
            if interests and interests_url:
                try:
                    db.replace_interests(pid, parse_interests_page(fetcher.get(interests_url)))
                except FetchError as exc:
                    log.warning("Interests for %s unavailable: %s", row["name"], exc)
            db.commit()
            log.info("[%d/%d] %s", n, len(rows), row["name"])
        except FetchError as exc:
            errors += 1
            log.error("[%d/%d] %s failed: %s", n, len(rows), row["name"], exc)
        except Exception:  # a parsing bug on one page shouldn't sink the whole run
            errors += 1
            log.exception("[%d/%d] %s: unexpected error", n, len(rows), row["name"])
    return errors

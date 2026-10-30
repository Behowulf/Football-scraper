"""Orchestrates a full scrape: rankings list -> player profiles -> recruiting interests."""

from __future__ import annotations

import logging

from .client import FetchError, Fetcher
from .db import Database
from .parsers import has_next_page, parse_interests_page, parse_profile_page, parse_rankings_page

log = logging.getLogger(__name__)

RANKINGS_URL = "https://247sports.com/season/{year}-football/compositerecruitrankings/?InstitutionGroup=HighSchool"


def rankings_url(year: int, page: int = 1) -> str:
    url = RANKINGS_URL.format(year=year)
    return url if page == 1 else f"{url}&Page={page}"


def scrape_rankings(fetcher: Fetcher, db: Database, year: int, max_pages: int | None = None,
                    limit: int | None = None) -> int:
    """Walk every page of the composite rankings list and store each prospect."""
    seen: set[int] = set()
    page = 1
    while max_pages is None or page <= max_pages:
        url = rankings_url(year, page)
        html = fetcher.get(url, use_cache=False)
        players = parse_rankings_page(html, year)
        new = [p for p in players if p["player_id"] not in seen]
        if page == 1 and not players:
            raise FetchError(
                f"No players found on {url}. The page layout may have changed, or the site returned a "
                "bot-check page; try --browser, or save the page and open an issue."
            )
        if not new:
            break
        for p in new:
            if limit is not None and len(seen) >= limit:
                break
            seen.add(p["player_id"])
            db.upsert_player(p, source="list")
            db.upsert_ranking(p["player_id"], year, {**p, "source": "247Sports Composite"})
        db.commit()
        log.info("Rankings page %d: %d players (total %d)", page, len(new), len(seen))
        if (limit is not None and len(seen) >= limit) or not has_next_page(html) and len(players) < 50:
            break
        page += 1
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
            interests_url = player.pop("interests_url", None)
            player.pop("class_year", None)  # trust the class from the rankings list
            db.upsert_player({"player_id": pid, **player}, source="profile", extra=result["extra"])
            for r in result["rankings"]:
                db.upsert_ranking(pid, year, r)
            if interests:
                interests_url = interests_url or url.rstrip("/") + "/recruitinterests/"
                try:
                    db.replace_interests(pid, parse_interests_page(fetcher.get(interests_url)))
                except FetchError as exc:
                    log.warning("Interests for %s unavailable: %s", row["name"], exc)
            db.commit()
            log.info("[%d/%d] %s", n, len(rows), row["name"])
        except FetchError as exc:
            errors += 1
            log.error("[%d/%d] %s failed: %s", n, len(rows), row["name"], exc)
        except Exception:  # parsing bug on one page shouldn't sink the whole run
            errors += 1
            log.exception("[%d/%d] %s: unexpected error", n, len(rows), row["name"])
    return errors

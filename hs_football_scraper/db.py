"""SQLite storage for prospects, their rankings and their recruiting interests."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    player_id           INTEGER PRIMARY KEY,
    name                TEXT,
    class_year          INTEGER,
    position            TEXT,
    height              TEXT,
    height_in           REAL,
    weight              INTEGER,
    high_school         TEXT,
    city                TEXT,
    state               TEXT,
    hometown            TEXT,
    birth_date          TEXT,
    twitter             TEXT,
    image_url           TEXT,
    profile_url         TEXT,
    composite_rank      INTEGER,
    stars               INTEGER,
    rating              REAL,
    national_rank       INTEGER,
    position_rank       INTEGER,
    state_rank          INTEGER,
    commit_school       TEXT,
    commit_status       TEXT,
    commit_date         TEXT,
    offer_count         INTEGER,
    extra_json          TEXT,
    list_scraped_at     TEXT,
    profile_scraped_at  TEXT
);

-- One row per ranking service (247Sports Composite, 247Sports, Rivals, ESPN, On3...).
CREATE TABLE IF NOT EXISTS rankings (
    player_id      INTEGER NOT NULL REFERENCES players(player_id),
    class_year     INTEGER,
    source         TEXT NOT NULL,
    stars          INTEGER,
    rating         REAL,
    national_rank  INTEGER,
    position       TEXT,
    position_rank  INTEGER,
    state_rank     INTEGER,
    scraped_at     TEXT,
    PRIMARY KEY (player_id, source)
);

-- Offers, visits and commitment history per school.
CREATE TABLE IF NOT EXISTS interests (
    player_id    INTEGER NOT NULL REFERENCES players(player_id),
    school       TEXT NOT NULL,
    school_url   TEXT,
    status       TEXT,
    offered      INTEGER,
    status_date  TEXT,
    visit        TEXT,
    raw_text     TEXT,
    scraped_at   TEXT,
    PRIMARY KEY (player_id, school)
);

CREATE TABLE IF NOT EXISTS scrape_runs (
    run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT,
    finished_at   TEXT,
    class_year    INTEGER,
    source_url    TEXT,
    players_seen  INTEGER,
    errors        INTEGER
);

CREATE INDEX IF NOT EXISTS idx_players_class ON players(class_year);
CREATE INDEX IF NOT EXISTS idx_players_state ON players(state);
CREATE INDEX IF NOT EXISTS idx_players_position ON players(position);
CREATE INDEX IF NOT EXISTS idx_players_commit ON players(commit_school);
"""

PLAYER_COLUMNS = (
    "name", "class_year", "position", "height", "height_in", "weight", "high_school", "city", "state",
    "hometown", "birth_date", "twitter", "image_url", "profile_url", "composite_rank", "stars", "rating",
    "national_rank", "position_rank", "state_rank", "commit_school", "commit_status", "commit_date",
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    # -- writes ---------------------------------------------------------
    def upsert_player(self, player: dict, source: str = "list", extra: dict | None = None) -> None:
        """Insert or update a player. Missing (None) values never overwrite stored ones."""
        pid = player["player_id"]
        values = {c: player.get(c) for c in PLAYER_COLUMNS if player.get(c) is not None}
        stamp = "list_scraped_at" if source == "list" else "profile_scraped_at"
        values[stamp] = now()
        if extra:
            row = self.conn.execute("SELECT extra_json FROM players WHERE player_id=?", (pid,)).fetchone()
            merged = json.loads(row["extra_json"]) if row and row["extra_json"] else {}
            merged.update(extra)
            values["extra_json"] = json.dumps(merged, ensure_ascii=False, default=str)

        cols = ", ".join(["player_id", *values])
        marks = ", ".join("?" * (len(values) + 1))
        updates = ", ".join(f"{c}=excluded.{c}" for c in values)
        self.conn.execute(
            f"INSERT INTO players ({cols}) VALUES ({marks}) ON CONFLICT(player_id) DO UPDATE SET {updates}",
            (pid, *values.values()),
        )

    def upsert_ranking(self, player_id: int, class_year: int | None, r: dict) -> None:
        self.conn.execute(
            """INSERT INTO rankings (player_id, class_year, source, stars, rating, national_rank, position,
                                     position_rank, state_rank, scraped_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(player_id, source) DO UPDATE SET
                 class_year=excluded.class_year, stars=excluded.stars, rating=excluded.rating,
                 national_rank=excluded.national_rank, position=excluded.position,
                 position_rank=excluded.position_rank, state_rank=excluded.state_rank,
                 scraped_at=excluded.scraped_at""",
            (player_id, class_year, r["source"], r.get("stars"), r.get("rating"), r.get("national_rank"),
             r.get("position"), r.get("position_rank"), r.get("state_rank"), now()),
        )

    def replace_interests(self, player_id: int, interests: list[dict]) -> None:
        self.conn.execute("DELETE FROM interests WHERE player_id=?", (player_id,))
        stamp = now()
        for i in interests:
            self.conn.execute(
                """INSERT OR REPLACE INTO interests
                   (player_id, school, school_url, status, offered, status_date, visit, raw_text, scraped_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (player_id, i["school"], i.get("school_url"), i.get("status"),
                 None if i.get("offered") is None else int(i["offered"]),
                 i.get("status_date"), i.get("visit"), i.get("raw_text"), stamp),
            )
        offers = sum(1 for i in interests if i.get("offered"))
        self.conn.execute("UPDATE players SET offer_count=? WHERE player_id=?", (offers, player_id))

    def start_run(self, class_year: int, url: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO scrape_runs (started_at, class_year, source_url) VALUES (?,?,?)", (now(), class_year, url)
        )
        return cur.lastrowid

    def finish_run(self, run_id: int, players_seen: int, errors: int) -> None:
        self.conn.execute(
            "UPDATE scrape_runs SET finished_at=?, players_seen=?, errors=? WHERE run_id=?",
            (now(), players_seen, errors, run_id),
        )
        self.conn.commit()

    def commit(self) -> None:
        self.conn.commit()

    # -- reads ----------------------------------------------------------
    def players_needing_profiles(self, class_year: int, refresh: bool = False) -> list[sqlite3.Row]:
        sql = "SELECT player_id, profile_url, name FROM players WHERE class_year=? AND profile_url IS NOT NULL"
        if not refresh:
            sql += " AND profile_scraped_at IS NULL"
        return self.conn.execute(sql + " ORDER BY composite_rank", (class_year,)).fetchall()

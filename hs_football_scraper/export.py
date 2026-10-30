"""Export the SQLite database to CSV, JSON and a multi-sheet Excel workbook."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

FLAT_QUERY = """
SELECT p.*,
       r247.stars          AS stars_247,
       r247.rating         AS rating_247,
       r247.national_rank  AS national_rank_247,
       r247.position_rank  AS position_rank_247,
       r247.state_rank     AS state_rank_247,
       (SELECT group_concat(school, '; ') FROM interests i
          WHERE i.player_id = p.player_id AND i.offered = 1) AS offers
FROM players p
LEFT JOIN rankings r247 ON r247.player_id = p.player_id AND r247.source = '247Sports'
ORDER BY p.class_year, p.composite_rank
"""


def export(db_path: str | Path, out_dir: str | Path) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    frames = {
        "prospects": pd.read_sql_query(FLAT_QUERY, conn),
        "rankings": pd.read_sql_query("SELECT * FROM rankings ORDER BY player_id, source", conn),
        "interests": pd.read_sql_query("SELECT * FROM interests ORDER BY player_id, school", conn),
    }
    conn.close()

    written = []
    for name, df in frames.items():
        path = out / f"{name}.csv"
        df.to_csv(path, index=False)
        written.append(path)

    xlsx = out / "prospects.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as xw:
        for name, df in frames.items():
            df.drop(columns=["extra_json"], errors="ignore").to_excel(xw, sheet_name=name, index=False)
    written.append(xlsx)

    # Nested JSON: one object per prospect with their rankings and interests inline.
    prospects = frames["prospects"].to_dict("records")
    by_pid = lambda df: {pid: g.drop(columns="player_id").to_dict("records") for pid, g in df.groupby("player_id")}
    rankings, interests = by_pid(frames["rankings"]), by_pid(frames["interests"])
    for p in prospects:
        extra = p.pop("extra_json", None)
        p["extra"] = json.loads(extra) if isinstance(extra, str) else {}
        p["rankings"] = rankings.get(p["player_id"], [])
        p["interests"] = interests.get(p["player_id"], [])
    path = out / "prospects.json"
    path.write_text(json.dumps(prospects, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    written.append(path)
    return written

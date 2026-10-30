# High School Football Prospects Scraper

Builds a database of high school football recruits from the
[247Sports Composite rankings](https://247sports.com/season/2027-football/compositerecruitrankings/?InstitutionGroup=HighSchool).

For every prospect it collects:

| From the rankings list | From the player's profile | From the recruiting page |
|---|---|---|
| Composite rank, stars, rating | Birth date, Twitter/X handle, photo | Every school showing interest |
| National / position / state rank | 247Sports' own rating & ranks | Whether each school offered |
| Position, height, weight | Rankings from other services shown on the page | Status (offered, visited, committed...) |
| High school, city, state | Commitment school and date | Visit info and dates |
| Commitment status | Any other profile details (kept in `extra`) | Total offer count |

## How to run it (step by step)

You need Python 3.10 or newer. Check with `python3 --version` (on Windows: `python --version`).

1. **Download the code**: on GitHub, click the green **Code** button, then **Download ZIP**, and unzip it.
   (Or: `git clone https://github.com/Behowulf/Football-scraper.git`.)
2. **Open a terminal in that folder** (on Windows, PowerShell; on Mac, Terminal).
3. **Install the requirements** (one time only):
   ```
   pip install -r requirements.txt
   ```
4. **Do a quick test run** with just the top 10 players:
   ```
   python3 -m hs_football_scraper --limit 10
   ```
5. **Run the full scrape**:
   ```
   python3 -m hs_football_scraper
   ```
   This visits every player's pages with a pause between requests, so it takes a while (around an hour
   per 1,000 players). If it stops partway, just run the same command again: players already
   done are skipped.

## What you get

All files are written to the `data/` folder:

- `data/export/prospects.xlsx`: **open this in Excel.** It has three sheets: prospects, rankings, interests.
- `data/export/prospects.csv`: one row per player, including a list of offering schools.
- `data/export/rankings.csv`: one row per player per ranking service.
- `data/export/interests.csv`: one row per player per interested school.
- `data/export/prospects.json`: everything nested per player.
- `data/prospects.db`: the SQLite database itself, for SQL queries.

## Useful options

| Option | What it does |
|---|---|
| `--year 2028` | Scrape a different recruiting class (default 2027) |
| `--limit 100` | Only the top 100 players |
| `--list-only` | Only the rankings list (fast; skips profile pages) |
| `--no-interests` | Skip the offers/visits pages |
| `--refresh` | Re-scrape profiles already in the database |
| `--delay 3` | Wait longer between requests (default 1.5 seconds) |
| `--browser` | Use a real headless browser if the site blocks normal requests (see below) |
| `--export-only` | Just regenerate the export files from the database |

Run `python3 -m hs_football_scraper --help` to see them all.

## If something goes wrong

- **"No players found"** or **HTTP 403** errors: 247Sports may be blocking scripted requests. Try browser mode:
  ```
  pip install playwright
  playwright install chromium
  python3 -m hs_football_scraper --browser
  ```
- **A column is empty for everyone** (for example, no birth dates): 247Sports may have changed that page's
  layout. Open an issue with the error output and the player's URL, and the parser can be updated.

The parsers in `hs_football_scraper/parsers.py` try several layouts and fall back to generic scanning.
Anything they find but can't map to a column is saved in the `extra` field, so details aren't silently lost.

## Please scrape responsibly

The default 1.5-second delay keeps the load on 247Sports light. Check 247Sports' terms of use before
using the data, and keep it for personal use.

## Running the tests

```
python3 -m unittest discover -s tests
```

The tests use hand-written sample pages in `tests/fixtures/` that mimic 247Sports' layout. They check the
parsing and database logic, but not the live site.

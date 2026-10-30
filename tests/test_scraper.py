import tempfile
import unittest
from pathlib import Path

from hs_football_scraper import parsers
from hs_football_scraper.db import Database
from hs_football_scraper.export import export
from hs_football_scraper.scraper import scrape_profiles, scrape_rankings

FIX = Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIX / name).read_text()


class FakeFetcher:
    """Serves fixture pages instead of hitting the network."""

    def __init__(self):
        self.urls = []

    def get(self, url, use_cache=True):
        self.urls.append(url)
        if "compositerecruitrankings" in url:
            return fixture("rankings_page1.html") if "Page=" not in url else "<html></html>"
        if "recruitinterests" in url:
            return fixture("interests.html")
        return fixture("profile.html")


class ParserTests(unittest.TestCase):
    def test_rankings_page(self):
        html = fixture("rankings_page1.html")
        players = parsers.parse_rankings_page(html, 2027)
        self.assertEqual(len(players), 2)  # the "Load More" item is skipped
        p = players[0]
        self.assertEqual(p["player_id"], 46100001)  # player id, not the high-school stint id
        self.assertEqual(p["name"], "Test Player")
        self.assertEqual((p["composite_rank"], p["rank_247"]), (1, 4))
        self.assertEqual(p["high_school"], "Example High School")
        self.assertEqual((p["city"], p["state"]), ("Springfield", "OH"))
        self.assertEqual((p["height"], p["height_in"], p["weight"]), ("6-3", 75, 210))
        self.assertEqual((p["stars"], p["rating"]), (5, 0.9987))
        self.assertEqual((p["national_rank"], p["position_rank"], p["state_rank"]), (1, 1, 1))
        self.assertEqual((p["commit_school"], p["commit_status"]), ("Example U", "Committed"))
        self.assertEqual(p["interests_url"],
                         "https://247sports.com/recruitment/test-player-100001/recruitinterests/")
        q = players[1]
        self.assertEqual((q["stars"], q["height_in"]), (4, 77.5))
        self.assertEqual((q["commit_school"], q["commit_status"]), (None, "Uncommitted"))
        self.assertIn("Page=2", parsers.next_page_url(html))

    def test_profile_page(self):
        res = parsers.parse_profile_page(fixture("profile.html"))
        p = res["player"]
        self.assertEqual(p["birth_date"], "2009-01-02")
        self.assertEqual((p["position"], p["height"], p["weight"], p["height_in"]), ("QB", "6-3", 210, 75))
        self.assertEqual((p["high_school"], p["city"], p["state"]), ("Example High School", "Springfield", "OH"))
        self.assertNotIn("twitter", p)  # 247's own site-wide Twitter link must not be picked up
        self.assertEqual((p["commit_school"], p["commit_status"], p["commit_date"]),
                         ("Example U", "Hard Commit", "2026-06-15"))
        self.assertTrue(p["interests_url"].endswith("/recruitment/test-player-100001/recruitinterests/"))
        ranks = {r["source"]: r for r in res["rankings"]}
        self.assertEqual((ranks["247Sports Composite"]["stars"], ranks["247Sports Composite"]["rating"]), (5, 0.9987))
        r247 = ranks["247Sports"]
        self.assertEqual((r247["stars"], r247["rating"], r247["national_rank"]), (4, 96, 4))
        self.assertEqual((r247["position"], r247["position_rank"], r247["state_rank"]), ("QB", 2, 1))
        self.assertEqual(res["timeline"][0], {"event_date": "2026-06-15", "event_type": "Commitment",
                                              "description": "Test Player commits to Example U"})
        self.assertEqual(res["extra"]["schools_attended"], ["Example High School (HS)", "Old School (HS)"])
        self.assertEqual(res["extra"]["highlight_videos"], ["https://www.hudl.com/video/3/1/abc"])
        self.assertNotIn("crystal_ball_predictions", res["extra"])

    def test_interests_page(self):
        rows = parsers.parse_interests_page(fixture("interests.html"))
        self.assertEqual([r["school"] for r in rows], ["Example U", "State Tech", "Small College"])
        self.assertEqual([r["offered"] for r in rows], [True, True, False])
        self.assertEqual([r["status"] for r in rows], ["Committed", "Cool", "None"])
        self.assertEqual([r["visit"] for r in rows], ["Jun 1", None, None])
        first = rows[0]
        self.assertEqual(first["status_date"], "2026-06-15")
        self.assertEqual(first["recruited_by"], "Coach One; Coach Two")
        self.assertEqual(first["roster_outlook"], "4 On Roster, 1 Commits")
        self.assertNotIn("S. Else", first["raw_text"])  # depth chart is excluded

    def test_helpers(self):
        self.assertEqual(parsers.parse_hometown("St. John's (Washington, DC)")["state"], "DC")
        self.assertEqual(parsers.parse_hometown("No Parens HS"), {"high_school": "No Parens HS"})
        self.assertIsNone(parsers.player_id_from_url("/college/foo/"))
        self.assertEqual(parsers.player_id_from_url("https://247sports.com/Player/A-B-123/"), 123)
        self.assertEqual(parsers.iso_date("Jun 1, 2026"), "2026-06-01")


class EndToEndTests(unittest.TestCase):
    def test_full_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "t.db")
            fetcher = FakeFetcher()
            self.assertEqual(scrape_rankings(fetcher, db, 2027), 2)
            self.assertTrue(any("Page=2" in u for u in fetcher.urls))
            self.assertEqual(scrape_profiles(fetcher, db, 2027), 0)
            row = db.conn.execute("SELECT * FROM players WHERE player_id=46100001").fetchone()
            self.assertEqual((row["offer_count"], row["birth_date"], row["composite_rank"], row["commit_status"]),
                             (2, "2009-01-02", 1, "Hard Commit"))
            # The list's 247Sports rank survives being merged with the profile's full 247Sports row.
            r = db.conn.execute("SELECT * FROM rankings WHERE player_id=46100001 AND source='247Sports'").fetchone()
            self.assertEqual((r["national_rank"], r["stars"]), (4, 4))
            self.assertEqual(db.conn.execute("SELECT count(*) FROM timeline").fetchone()[0], 4)
            self.assertEqual(len(db.players_needing_profiles(2027)), 0)  # a re-run resumes, not repeats

            # Re-scraping the list keeps profile-only ranking fields (stars/rating) intact.
            scrape_rankings(fetcher, db, 2027)
            r = db.conn.execute("SELECT * FROM rankings WHERE player_id=46100001 AND source='247Sports'").fetchone()
            self.assertEqual((r["stars"], r["rating"]), (4, 96))
            db.close()

            names = {p.name for p in export(Path(tmp) / "t.db", Path(tmp) / "out")}
            self.assertEqual(names, {"prospects.csv", "rankings.csv", "interests.csv", "timeline.csv",
                                     "prospects.xlsx", "prospects.json"})


if __name__ == "__main__":
    unittest.main()

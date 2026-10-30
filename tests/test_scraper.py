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

    def get(self, url, use_cache=True):
        if "compositerecruitrankings" in url:
            return fixture("rankings_page1.html") if "Page=" not in url else "<html></html>"
        if "recruitinterests" in url:
            return fixture("interests.html")
        return fixture("profile.html")


class ParserTests(unittest.TestCase):
    def test_rankings_page(self):
        players = parsers.parse_rankings_page(fixture("rankings_page1.html"), 2027)
        self.assertEqual(len(players), 2)
        p = players[0]
        self.assertEqual(p["player_id"], 46100001)
        self.assertEqual(p["name"], "Test Player")
        self.assertEqual(p["high_school"], "Example High School")
        self.assertEqual((p["city"], p["state"]), ("Springfield", "OH"))
        self.assertEqual((p["height"], p["height_in"], p["weight"]), ("6-3", 75, 210))
        self.assertEqual((p["stars"], p["rating"]), (5, 0.9987))
        self.assertEqual((p["national_rank"], p["position_rank"], p["state_rank"]), (1, 1, 1))
        self.assertEqual((p["commit_school"], p["commit_status"]), ("Example U", "Committed"))
        q = players[1]
        self.assertEqual(q["player_id"], 46100002)  # capitalised /Player/ URL still matched
        self.assertEqual((q["stars"], q["height_in"]), (4, 77.5))
        self.assertIsNone(q["commit_school"])
        self.assertTrue(parsers.has_next_page(fixture("rankings_page1.html")))

    def test_profile_page(self):
        res = parsers.parse_profile_page(fixture("profile.html"))
        p = res["player"]
        self.assertEqual(p["birth_date"], "2009-01-02")
        self.assertEqual((p["position"], p["weight"], p["height_in"]), ("QB", 210, 75))
        self.assertEqual((p["city"], p["state"], p["twitter"]), ("Springfield", "OH", "@testplayer7"))
        self.assertEqual((p["commit_school"], p["commit_date"]), ("Example U", "6/15/2026"))
        self.assertTrue(p["interests_url"].endswith("/recruitinterests/"))
        ranks = {r["source"]: r for r in res["rankings"]}
        self.assertEqual(ranks["247Sports Composite"]["stars"], 5)
        self.assertEqual(ranks["247Sports Composite"]["rating"], 0.9987)
        self.assertEqual(ranks["247Sports"]["national_rank"], 3)
        self.assertEqual((ranks["247Sports"]["position"], ranks["247Sports"]["position_rank"]), ("QB", 2))
        self.assertEqual(ranks["247Sports"]["state_rank"], 1)

    def test_interests_page(self):
        rows = parsers.parse_interests_page(fixture("interests.html"))
        self.assertEqual([r["school"] for r in rows], ["Example U", "State Tech", "Small College"])
        self.assertEqual([r["offered"] for r in rows], [True, True, False])
        self.assertTrue(rows[0]["status"].startswith("Committed"))

    def test_helpers(self):
        self.assertEqual(parsers.parse_hometown("St. John's (Washington, DC)")["state"], "DC")
        self.assertEqual(parsers.parse_hometown("No Parens HS"), {"high_school": "No Parens HS"})
        self.assertIsNone(parsers.player_id_from_url("/college/foo/"))


class EndToEndTests(unittest.TestCase):
    def test_full_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "t.db")
            fetcher = FakeFetcher()
            self.assertEqual(scrape_rankings(fetcher, db, 2027), 2)
            self.assertEqual(scrape_profiles(fetcher, db, 2027), 0)
            row = db.conn.execute("SELECT * FROM players WHERE player_id=46100001").fetchone()
            self.assertEqual((row["offer_count"], row["birth_date"], row["composite_rank"]), (2, "2009-01-02", 1))
            self.assertEqual(db.conn.execute("SELECT count(*) FROM rankings").fetchone()[0], 4)
            # Second run skips already-profiled players.
            self.assertEqual(len(db.players_needing_profiles(2027)), 0)
            db.close()
            names = {p.name for p in export(Path(tmp) / "t.db", Path(tmp) / "out")}
            self.assertEqual(names, {"prospects.csv", "rankings.csv", "interests.csv", "prospects.xlsx",
                                     "prospects.json"})


if __name__ == "__main__":
    unittest.main()

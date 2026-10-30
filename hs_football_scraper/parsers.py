"""HTML parsers for 247Sports rankings, player profile and recruiting-interest pages.

Selectors match 247Sports' markup as of October 2026. Each parser also has
fallbacks, and anything recognised but not mapped to a column is kept in an
``extra`` dict so no detail is silently lost if the layout shifts.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

BASE_URL = "https://247sports.com"

# /player/jalen-brewster-46145095/high-school-365893/ -> 46145095 (the player, not the school stint)
PLAYER_ID_RE = re.compile(r"/player/[^/]*?-(\d+)(?:/|$|\?)", re.I)
RECRUITMENT_RE = re.compile(r"/recruitment/([^/?#]+)", re.I)
HOMETOWN_RE = re.compile(r"^(?P<school>.*?)\s*\((?P<city>[^()]*?),\s*(?P<state>[A-Za-z .]+)\)\s*$")
HEIGHT_RE = re.compile(r"(\d)\s*[-' ]\s*(\d{1,2}(?:\.\d+)?)")
DATE_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")
COMMIT_WORDS = ("Enrolled", "Signed", "Hard Commit", "Soft Commit", "Committed")

US_STATES = set(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM "
    "NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)


# -- small helpers ------------------------------------------------------------
def clean(text) -> str | None:
    if text is None:
        return None
    if isinstance(text, Tag):
        text = text.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text or None


def to_int(text) -> int | None:
    m = re.search(r"-?\d[\d,]*", clean(text) or "")
    return int(m.group().replace(",", "")) if m else None


def to_float(text) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", clean(text) or "")
    return float(m.group()) if m else None


def height_to_inches(text) -> float | None:
    m = HEIGHT_RE.search(clean(text) or "")
    return int(m.group(1)) * 12 + float(m.group(2)) if m else None


def iso_date(text) -> str | None:
    """'10/2/2008 12:00:00 AM' / '10/4/2025' / 'Jun 11, 2026' -> '2008-10-02'."""
    text = clean(text)
    if not text:
        return None
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y", "%m/%d/%y", "%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return text


def player_id_from_url(url: str | None) -> int | None:
    m = PLAYER_ID_RE.search(url or "")
    return int(m.group(1)) if m else None


def abs_url(href: str | None) -> str | None:
    return urljoin(BASE_URL, href) if href else None


def interests_url_from_recruitment(href: str | None) -> str | None:
    """'/Recruitment/jalen-brewster-168773?view=Complex' -> full recruit-interests URL."""
    m = RECRUITMENT_RE.search(href or "")
    return f"{BASE_URL}/recruitment/{m.group(1)}/recruitinterests/" if m else None


def parse_hometown(meta: str | None) -> dict:
    """'Bishop Gorman (Las Vegas, NV)' -> school / city / state."""
    meta = clean(meta)
    if not meta:
        return {}
    m = HOMETOWN_RE.match(meta)
    if m:
        return {
            "high_school": clean(m["school"]),
            "city": clean(m["city"]),
            "state": clean(m["state"]),
            "hometown": f"{clean(m['city'])}, {clean(m['state'])}",
        }
    return {"high_school": meta}


def parse_metrics(text: str | None) -> dict:
    """'6-3 / 210' -> height, height_in, weight."""
    text = clean(text)
    if not text:
        return {}
    parts = [p.strip() for p in text.split("/")]
    out = {"height": parts[0] if parts[0] not in ("", "-") else None, "height_in": height_to_inches(parts[0])}
    if len(parts) > 1:
        out["weight"] = to_int(parts[1])
    return out


def count_stars(node: Tag | None) -> int | None:
    if node is None:
        return None
    stars = node.select(".icon-starsolid.yellow")
    return len(stars) if stars else None


def _img_src(img: Tag | None) -> str | None:
    if img is None:
        return None
    src = img.get("data-src") or img.get("src")
    return None if not src or src.endswith("1x1.gif") else abs_url(src)


def _commit_status(text: str) -> str | None:
    return next((w for w in COMMIT_WORDS if w.lower() in text.lower()), None)


# -- rankings list page -------------------------------------------------------
def parse_rankings_page(html: str, class_year: int) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    items = soup.select("li.rankings-page__list-item") or [
        li for li in soup.select("li") if li.select_one("a[href*='/player/' i]") and li.select_one(".score")
    ]
    players = []
    for li in items:
        link = li.select_one("a.rankings-page__name-link") or li.select_one(".recruit a[href*='/player/' i]")
        if link is None:  # e.g. the "Load More" item
            continue
        url = abs_url(link.get("href"))
        pid = player_id_from_url(url)
        if pid is None:
            continue
        p = {
            "player_id": pid,
            "name": clean(link),
            "profile_url": url,
            "class_year": class_year,
            "composite_rank": to_int(li.select_one(".rank-column .primary")),
            "rank_247": to_int(li.select_one(".rank-column .other")),
            "position": clean(li.select_one(".position")),
        }
        p.update(parse_hometown(li.select_one(".recruit .meta")))
        p.update(parse_metrics(li.select_one(".metrics")))
        star_block = li.select_one(".rankings-page__star-and-score")
        p["stars"] = count_stars(star_block)
        p["rating"] = to_float(li.select_one(".score"))
        p["national_rank"] = to_int(li.select_one(".natrank"))
        p["position_rank"] = to_int(li.select_one(".posrank"))
        p["state_rank"] = to_int(li.select_one(".sttrank"))
        p.update(_parse_list_status(li.select_one(".status")))
        recruitment = li.select_one("a[href*='/recruitment/' i]")
        p["interests_url"] = interests_url_from_recruitment(recruitment.get("href") if recruitment else None)
        p["image_url"] = _img_src(li.select_one(".circle-image-block img, .recruit img"))
        players.append(p)
    return players


def _parse_list_status(status: Tag | None) -> dict:
    out = {"commit_school": None, "commit_status": "Uncommitted"}
    if status is None:
        return out
    img = status.select_one("img[title], img[alt]")
    if img is not None:
        out["commit_school"] = clean(img.get("title") or img.get("alt"))
        out["commit_status"] = "Committed"
    word = _commit_status(clean(status) or "")
    if word:
        out["commit_status"] = word
    return out


def next_page_url(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    a = soup.select_one(".rankings-page__showmore a, a.rankings-page__showmore, a[data-js='showmore']")
    return abs_url(a.get("href")) if a else None


# -- player profile page ------------------------------------------------------
PROFILE_KEYS = {
    "pos": "position", "position": "position",
    "height": "height", "weight": "weight",
    "high school": "high_school", "city": "hometown", "hometown": "hometown",
    "class": "class_year", "birth date": "birth_date", "birthdate": "birth_date",
}

RANK_LABELS = {"natl": "national_rank", "national": "national_rank", "overall": "national_rank",
               "pos": "position_rank", "st": "state_rank", "state": "state_rank"}

RANK_SOURCES = ("247Sports Composite", "247Sports", "Rivals", "ESPN", "On3", "Industry")


def parse_profile_page(html: str) -> dict:
    """Return {'player': {...}, 'rankings': [...], 'timeline': [...], 'extra': {...}}."""
    soup = BeautifulSoup(html, "lxml")
    header = soup.select_one("header.profile-header, .profile-header, .upper-cards") or soup
    player: dict = {}
    extra: dict = {}

    name = header.select_one("h1.name") or soup.select_one("h1")
    player["name"] = clean(name)

    for li in header.select("ul.metrics-list li, ul.details li"):
        spans = [clean(c) for c in li.find_all(["span", "b", "strong"], recursive=False)]
        spans = [s for s in spans if s]
        if len(spans) < 2:
            continue
        label, value = spans[0], " ".join(spans[1:])
        key = PROFILE_KEYS.get(label.lower().rstrip(":"))
        if key:
            player.setdefault(key, value)
        else:
            extra[label] = value
        hs_link = li.select_one("a[href*='maxpreps']")
        if hs_link:
            extra["maxpreps_url"] = hs_link["href"]

    _merge_json_ld(soup, player, extra)  # fills gaps only (e.g. birth date)

    if player.get("height"):
        player["height_in"] = height_to_inches(player["height"])
    player["weight"] = to_int(player.get("weight"))
    player["class_year"] = to_int(player.get("class_year"))
    player["birth_date"] = iso_date(player.get("birth_date"))
    if player.get("hometown") and "," in player["hometown"]:
        city, state = player["hometown"].rsplit(",", 1)
        player["city"], player["state"] = city.strip(), state.strip()

    player.setdefault("image_url", _img_src(header.select_one(".img-block img, img")))

    schools = [clean(a) for a in header.select(".institution-list a")]
    if len(schools) > 1:
        extra["schools_attended"] = schools

    for a in header.select("a[href*='twitter.com/'], a[href*='x.com/']"):
        player["twitter"] = "@" + a["href"].rstrip("/").rsplit("/", 1)[-1].lstrip("@")
    highlights = header.select_one("a.watch-highlights-link")
    if highlights:
        extra["highlights_url"] = abs_url(highlights["href"])

    banner = soup.select_one(".commit-banner")
    if banner is not None:
        img = banner.select_one("img")
        if img is not None:
            player["commit_school"] = clean(img.get("title") or img.get("alt"))
        text = clean(banner) or ""
        player["commit_status"] = _commit_status(text) or "Committed"
        date = DATE_RE.search(text)
        player["commit_date"] = iso_date(date.group()) if date else None

    cb = soup.select_one("section.crystal-ball")
    if cb is not None:
        preds = [clean(li) for li in cb.select("li") if clean(li)]
        if preds and "No Crystal Ball predictions" not in (clean(cb) or ""):
            extra["crystal_ball_predictions"] = preds

    interests = soup.find("a", href=re.compile(r"/recruitment/.+/recruitinterests", re.I))
    player["interests_url"] = abs_url(interests["href"]) if interests else None

    return {
        "player": {k: v for k, v in player.items() if v is not None},
        "rankings": _parse_profile_rankings(soup, player.get("state")),
        "timeline": _parse_timeline(soup),
        "extra": extra,
    }


def _merge_json_ld(soup: BeautifulSoup, player: dict, extra: dict) -> None:
    for script in soup.select("script[type='application/ld+json']"):
        try:
            data = json.loads(script.string or "")
        except (ValueError, TypeError):
            continue
        for obj in data if isinstance(data, list) else [data]:
            if not isinstance(obj, dict):
                continue
            if obj.get("@type") == "Person":
                for src, dst in (("name", "name"), ("birthDate", "birth_date"), ("height", "height"),
                                 ("weight", "weight")):
                    val = obj.get(src)
                    if isinstance(val, list):
                        val = val[0] if val else None
                    if isinstance(val, dict):
                        val = val.get("value")
                    if val and not player.get(dst):
                        player[dst] = clean(val)
                img = obj.get("image")
                if img and not player.get("image_url"):
                    player["image_url"] = img.get("url") if isinstance(img, dict) else img
                aff = obj.get("affiliation")
                if isinstance(aff, dict) and aff.get("name") and not player.get("high_school"):
                    player["high_school"] = clean(aff["name"])
            elif isinstance(obj.get("data"), list):  # highlight videos
                videos = [v.get("path") for v in obj["data"] if isinstance(v, dict) and v.get("path")]
                if videos:
                    extra["highlight_videos"] = videos


def _parse_profile_rankings(soup: BeautifulSoup, state: str | None = None) -> list[dict]:
    rankings = []
    for sec in soup.select("section.rankings-section, .rankings-section"):
        title = (clean(sec.select_one("h3, .title")) or "").replace("®", "").strip()
        source = next((s for s in RANK_SOURCES if title.lower().startswith(s.lower())), title or None)
        if not source:
            continue
        r = {"source": source, "stars": count_stars(sec), "rating": to_float(sec.select_one(".rank-block"))}
        for li in sec.select("ul.ranks-list li"):
            label = (clean(li.select_one("b")) or "").strip()
            key = RANK_LABELS.get(label.lower().rstrip(".:"))
            # Position/state ranks are labelled with the code itself, e.g. "DL" / "GA".
            if key is None and re.fullmatch(r"[A-Za-z]{1,5}", label):
                if state:
                    is_state = label == state
                else:  # "DE" is both Delaware and defensive end: fall back to list order (Pos before State).
                    is_state = label in US_STATES and ("position_rank" in r or label != "DE")
                key = "state_rank" if is_state else "position_rank"
                if key == "position_rank":
                    r["position"] = label
            if key:
                r[key] = to_int(li.select_one("strong")) or to_int((clean(li) or "").replace(label, "", 1))
        if any(r.get(k) is not None for k in ("stars", "rating", "national_rank")):
            rankings.append(r)
    return rankings


def _parse_timeline(soup: BeautifulSoup) -> list[dict]:
    """Timeline events: 'Jun 11, 2026: O. Visit' / 'X officially visits Florida Gators'."""
    events = []
    for ev in soup.select("section.timeline .event"):
        head = clean(ev.select_one("h3")) or ""
        date, _, kind = head.partition(":")
        events.append({
            "event_date": iso_date(date),
            "event_type": clean(kind),
            "description": clean(ev.select_one("h4")),
        })
    return events


# -- recruiting interests (offers / visits) page ------------------------------
def parse_interests_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    ul = soup.select_one("ul.recruit-interest-index_lst")
    items = ul.find_all("li", recursive=False) if ul else []
    out = []
    for li in items:
        for hidden in li.select(".depthchart_blk, .edit_anc"):
            hidden.decompose()
        school_link = li.select_one(".first_blk a[href*='/college/' i]")
        img = li.select_one("img")
        school = clean(school_link) or (clean(img.get("title") or img.get("alt")) if img else None)
        if not school:
            continue
        raw_text = clean(li)
        status_node = li.select_one(".status")
        status_text = clean(status_node) or ""
        status = re.sub(r"^Status:\s*", "", DATE_RE.sub("", status_text)).strip(" ()") or None
        date = DATE_RE.search(status_text)
        offer = _field(li, "offer")
        coaches = [clean(a) for a in li.select(".interest-details_lst a[href*='/coach/']")]
        out.append({
            "school": school,
            "school_url": abs_url(school_link.get("href")) if school_link else None,
            "status": status,
            "status_date": iso_date(date.group()) if date else None,
            "offered": None if offer is None else offer.lower().startswith("yes"),
            "visit": _field(li, "visit"),
            "recruited_by": "; ".join(coaches) or None,
            "roster_outlook": _field(li, "roster"),
            "raw_text": raw_text,
        })
    return out


def _field(li: Tag, cls: str) -> str | None:
    """'<span class="visit"><span class="heading">Visit:</span> May 29</span>' -> 'May 29'."""
    node = li.select_one(f".{cls}")
    if node is None:
        return None
    heading = node.select_one(".heading")
    if heading:
        heading.extract()
    value = clean(node)
    return None if value in (None, "-") else value

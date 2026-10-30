"""HTML parsers for 247Sports rankings, player profile and recruiting pages.

247Sports changes its markup from time to time, so every parser tries several
selectors and falls back to generic label/value scanning. Anything recognised
but not mapped to a column is kept in an ``extra`` dict so no detail is lost.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

BASE_URL = "https://247sports.com"

PLAYER_ID_RE = re.compile(r"-(\d+)/?(?:[?#].*)?$")
HOMETOWN_RE = re.compile(r"^(?P<school>.*?)\s*\((?P<city>[^()]*?),\s*(?P<state>[A-Za-z .]+)\)\s*$")
HEIGHT_RE = re.compile(r"(\d)\s*[-' ]\s*(\d{1,2}(?:\.\d+)?)")


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


def player_id_from_url(url: str | None) -> int | None:
    m = PLAYER_ID_RE.search(url or "")
    return int(m.group(1)) if m else None


def abs_url(href: str | None) -> str | None:
    return urljoin(BASE_URL, href) if href else None


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
    out = {"height": parts[0] or None, "height_in": height_to_inches(parts[0])}
    if len(parts) > 1:
        out["weight"] = to_int(parts[1])
    return out


def count_stars(node: Tag | None) -> int | None:
    if node is None:
        return None
    stars = node.select(".icon-starsolid.yellow, .icon-starsolid.yellow-star, .star.yellow")
    if not stars:
        stars = [s for s in node.select("[class*='starsolid']") if "yellow" in " ".join(s.get("class", []))]
    return len(stars) if stars else None


# -- rankings list page -------------------------------------------------------
def _ranking_items(soup: BeautifulSoup) -> list[Tag]:
    items = soup.select("li.rankings-page__list-item")
    if items:
        return items
    # Fallback: any list item that holds a player link and a score.
    return [
        li for li in soup.select("li")
        if li.select_one("a[href*='/player/' i]") and li.select_one(".score, [class*='score']")
    ]


def parse_rankings_page(html: str, class_year: int) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    players = []
    for li in _ranking_items(soup):
        link = li.select_one("a.rankings-page__name-link") or li.select_one(".recruit a[href*='/player/' i]") \
            or li.select_one("a[href*='/player/' i]")
        if link is None:
            continue
        url = abs_url(link.get("href"))
        p = {
            "player_id": player_id_from_url(url),
            "name": clean(link),
            "profile_url": url,
            "class_year": class_year,
            "composite_rank": to_int(li.select_one(".rank-column .primary, .primary")),
            "position": clean(li.select_one(".position")),
        }
        p.update(parse_hometown(clean(li.select_one(".recruit .meta, .meta"))))
        p.update(parse_metrics(clean(li.select_one(".metrics"))))

        rating = li.select_one(".rating, .rankings-page__star-and-score") or li
        p["stars"] = count_stars(rating)
        p["rating"] = to_float(rating.select_one(".score"))
        p["national_rank"] = to_int(li.select_one(".natrank"))
        p["position_rank"] = to_int(li.select_one(".posrank"))
        p["state_rank"] = to_int(li.select_one(".sttrank"))

        p.update(_parse_status(li.select_one(".status")))
        p["image_url"] = _img_src(li.select_one(".recruit img, img.jsonly"))
        if p["player_id"] is None:
            continue
        players.append(p)
    return players


def _img_src(img: Tag | None) -> str | None:
    if img is None:
        return None
    return abs_url(img.get("data-src") or img.get("src"))


def _parse_status(status: Tag | None) -> dict:
    out = {"commit_school": None, "commit_status": "Uncommitted", "commit_date": None}
    if status is None:
        return out
    img = status.select_one("img")
    if img is not None:
        out["commit_school"] = clean(img.get("title") or img.get("alt"))
        out["commit_status"] = "Committed"
    text = clean(status) or ""
    for word in ("Enrolled", "Signed", "Committed"):
        if word.lower() in text.lower() or word.lower() in " ".join(status.get("class", [])).lower():
            out["commit_status"] = word
            break
    m = re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", text)
    if m:
        out["commit_date"] = m.group()
    if status.select_one(".crystal-ball, [class*='crystal']") and not img:
        out["commit_status"] = "Uncommitted (Crystal Ball predictions)"
    return out


def has_next_page(html: str) -> bool:
    soup = BeautifulSoup(html, "lxml")
    return soup.select_one("a.rankings-page__showmore, .showmore a, a[class*='showmore']") is not None


# -- player profile page ------------------------------------------------------
PROFILE_KEYS = {
    "pos": "position", "position": "position",
    "height": "height", "ht": "height",
    "weight": "weight", "wt": "weight",
    "high school": "high_school", "school": "high_school",
    "city": "hometown", "hometown": "hometown", "home town": "hometown",
    "class": "class_year", "class of": "class_year",
    "birth date": "birth_date", "birthdate": "birth_date", "dob": "birth_date",
    "40 yard dash": "forty", "40": "forty", "forty": "forty",
    "shuttle": "shuttle", "vertical": "vertical", "bench": "bench",
    "gpa": "gpa", "jersey": "jersey",
}

RANK_LABELS = {
    "natl": "national_rank", "national": "national_rank", "nat'l": "national_rank", "overall": "national_rank",
    "pos": "position_rank", "position": "position_rank",
    "st": "state_rank", "state": "state_rank",
}

US_STATES = set(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM "
    "NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)

RANK_SOURCES = ("247Sports Composite", "247Sports", "Rivals", "ESPN", "On3", "Industry")


def parse_profile_page(html: str) -> dict:
    """Return {'player': {...}, 'rankings': [...], 'extra': {...}}."""
    soup = BeautifulSoup(html, "lxml")
    player: dict = {}
    extra: dict = {}

    _merge_json_ld(soup, player, extra)

    name = soup.select_one("h1.name, .profile-header h1, h1")
    if name and not player.get("name"):
        player["name"] = clean(name)

    for label, value in _label_value_pairs(soup):
        key = PROFILE_KEYS.get(label.lower().rstrip(":").strip())
        if key and not player.get(key):
            player[key] = value
        else:
            extra.setdefault(label, value)

    if player.get("height"):
        player["height_in"] = height_to_inches(player["height"])
    if isinstance(player.get("weight"), str):
        player["weight"] = to_int(player["weight"])
    if isinstance(player.get("class_year"), str):
        player["class_year"] = to_int(player["class_year"])
    if player.get("hometown") and "," in player["hometown"]:
        city, state = player["hometown"].rsplit(",", 1)
        player.setdefault("city", city.strip())
        player.setdefault("state", state.strip())

    img = soup.select_one(".main-header img, .profile-header img, img.player-image")
    if img and not player.get("image_url"):
        player["image_url"] = _img_src(img)

    tw = soup.select_one("a[href*='twitter.com/'], a[href*='x.com/']")
    if tw:
        player["twitter"] = "@" + tw["href"].rstrip("/").rsplit("/", 1)[-1].lstrip("@")

    commit = soup.select_one(".commit-banner, .commitment, [class*='commit']")
    if commit is not None:
        img = commit.select_one("img")
        if img is not None and (img.get("alt") or img.get("title")):
            player["commit_school"] = clean(img.get("title") or img.get("alt"))
        text = clean(commit) or ""
        for word in ("Enrolled", "Signed", "Committed"):
            if word.lower() in text.lower():
                player["commit_status"] = word
                break
        m = re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", text)
        if m:
            player["commit_date"] = m.group()

    interests = soup.find("a", href=re.compile(r"recruit-?interest", re.I))
    player["interests_url"] = abs_url(interests["href"]) if interests else None

    return {"player": {k: v for k, v in player.items() if v is not None},
            "rankings": _parse_profile_rankings(soup, player.get("state")),
            "extra": extra}


def _merge_json_ld(soup: BeautifulSoup, player: dict, extra: dict) -> None:
    for script in soup.select("script[type='application/ld+json']"):
        try:
            data = json.loads(script.string or "")
        except (ValueError, TypeError):
            continue
        for obj in data if isinstance(data, list) else [data]:
            if not isinstance(obj, dict) or obj.get("@type") != "Person":
                continue
            player.setdefault("name", obj.get("name"))
            player.setdefault("birth_date", obj.get("birthDate"))
            img = obj.get("image")
            player.setdefault("image_url", img.get("url") if isinstance(img, dict) else img)
            for src, dst in (("height", "height"), ("weight", "weight")):
                val = obj.get(src)
                if isinstance(val, dict):
                    val = val.get("value")
                if val:
                    player.setdefault(dst, clean(val))
            aff = obj.get("affiliation")
            if isinstance(aff, dict) and aff.get("name"):
                player.setdefault("high_school", aff["name"])
            addr = (obj.get("homeLocation") or {}).get("address") if isinstance(obj.get("homeLocation"), dict) else None
            if isinstance(addr, dict):
                player.setdefault("city", addr.get("addressLocality"))
                player.setdefault("state", addr.get("addressRegion"))
            extra["json_ld"] = obj


def _label_value_pairs(soup: BeautifulSoup):
    """Yield (label, value) from the profile's detail/vitals lists."""
    seen = set()
    for li in soup.select("ul.details li, ul.metrics-list li, .vitals li, .upper-cards li, .details li"):
        if id(li) in seen:
            continue
        seen.add(id(li))
        parts = [clean(c) for c in li.find_all(["span", "b", "strong", "dt", "dd"], recursive=False)]
        parts = [p for p in parts if p]
        if len(parts) >= 2:
            yield parts[0], " ".join(parts[1:])
        else:
            text = clean(li) or ""
            if ":" in text:
                label, value = text.split(":", 1)
                if label.strip() and value.strip():
                    yield label.strip(), value.strip()


def _parse_profile_rankings(soup: BeautifulSoup, state: str | None = None) -> list[dict]:
    rankings = []
    sections = soup.select("section.rankings-section, .rankings-section, section.rankings > div, .ranking-block")
    for sec in sections:
        title = clean(sec.select_one("h3, h2, .title, header")) or ""
        source = next((s for s in RANK_SOURCES if s.lower() in title.lower()), title or None)
        if not source:
            continue
        r = {
            "source": source.replace("®", "").strip(),
            "stars": count_stars(sec),
            "rating": to_float(sec.select_one(".rank-block, .score, .rating")),
        }
        for li in sec.select("ul.ranks-list li, ul.ranks li, li"):
            label = clean(li.select_one("b, .label, span")) or ""
            norm = label.lower().rstrip(".:").strip()
            key = RANK_LABELS.get(norm)
            # 247 often labels position/state ranks with the code itself, e.g. "QB" / "TX".
            if key is None and re.fullmatch(r"[A-Z]{1,5}", label.strip()):
                code = label.strip()
                if state:
                    is_state = code == state
                else:  # "DE" is both Delaware and defensive end: fall back to list order (Pos before State).
                    is_state = code in US_STATES and ("position_rank" in r or code != "DE")
                key = "state_rank" if is_state else "position_rank"
                if key == "position_rank":
                    r["position"] = label.strip()
            if key:
                r[key] = to_int(li.select_one("strong, .value, a")) or to_int(clean(li).replace(label, "", 1))
        if any(r.get(k) is not None for k in ("stars", "rating", "national_rank")):
            rankings.append(r)
    return rankings


# -- recruiting interests (offers / visits) page ------------------------------
INTEREST_STATUSES = ("Enrolled", "Signed", "Committed", "Visit", "Offered", "Warm", "Cool", "Interest", "None")


def parse_interests_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    items = soup.select("ul.recruit-interest-index_lst li, .recruit-interest-index_lst > li, li.recruit-interest")
    if not items:
        items = [li for li in soup.select("li") if li.select_one("a[href*='/college/' i]")]
    out = []
    for li in items:
        school_link = li.select_one(".first_blk a, a[href*='/college/' i]")
        img = li.select_one("img")
        school = clean(school_link) or (clean(img.get("alt") or img.get("title")) if img else None)
        if not school:
            continue
        text = clean(li) or ""
        status_node = li.select_one(".status")
        status = clean(status_node)
        if not status:
            status = next((s for s in INTEREST_STATUSES if re.search(rf"\b{s}\b", text, re.I)), None)
        offer_node = li.select_one(".offer")
        offer_text = clean(offer_node) or ""
        offered = None
        if offer_text:
            offered = "yes" in offer_text.lower() or "offer" == offer_text.lower().replace(":", "").strip()
        elif re.search(r"\bOffer(ed)?\b", text, re.I):
            offered = True
        date = re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", text)
        out.append({
            "school": school,
            "school_url": abs_url(school_link.get("href")) if school_link else None,
            "status": status,
            "offered": offered,
            "status_date": date.group() if date else None,
            "visit": clean(li.select_one(".visit, [class*='visit']")),
            "raw_text": text,
        })
    return out

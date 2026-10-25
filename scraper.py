"""Scrape every Bellhaven community from the public website."""
import re
import time
import requests
from bs4 import BeautifulSoup
import config as C

LABELS = ["Address", "Care Offerings", "Administrator", "Phone"]


def _get(url):
    for attempt in range(3):
        r = requests.get(url, timeout=30)
        if r.ok:
            return BeautifulSoup(r.text, "html.parser")
        time.sleep(2 ** attempt)
    r.raise_for_status()


def _slugs(soup):
    out = set()
    for a in soup.select("a[href]"):
        m = re.search(r"/communities/([^/?#]+)", a["href"])
        if m:
            out.add(m.group(1))
    return out


def find_slugs():
    """Home page (catches 'new' communities not in the directory) plus every
    directory page. Stops when a page adds nothing new, so a broken page
    count or a page that repeats page 1 cannot fool it."""
    slugs = _slugs(_get(C.BASE_URL))
    for page in range(1, 30):
        found = _slugs(_get(f"{C.BASE_URL}/communities?page={page}"))
        if page > 1 and not (found - slugs):
            break
        slugs |= found
    return sorted(slugs)


def parse_detail(slug):
    soup = _get(f"{C.BASE_URL}/communities/{slug}")
    lines = [l.strip() for l in soup.get_text("\n").split("\n") if l.strip()]
    fields = {}
    for i, line in enumerate(lines):
        if line in LABELS:
            vals = []
            for nxt in lines[i + 1:]:
                if nxt in LABELS or nxt.startswith("←") or nxt.startswith("©"):
                    break
                vals.append(nxt)
            fields[line] = vals
    addr = fields.get("Address", [])
    city = state = zip_ = None
    if len(addr) > 1:
        m = re.match(r"(.+?),\s*([A-Z]{2})\s+(\d{5})", addr[-1])
        if m:
            city, state, zip_ = m.groups()
    h1 = soup.find("h1")
    return {
        "slug": slug,
        "name": h1.get_text(strip=True) if h1 else slug,
        "street": " ".join(addr[:-1]) if len(addr) > 1 else (addr[0] if addr else None),
        "city": city, "state": state, "zip": zip_,
        "care_offerings": fields.get("Care Offerings", []),
        "administrator": " ".join(fields.get("Administrator", [])),
        "phone": " ".join(fields.get("Phone", [])),
        "url": f"{C.BASE_URL}/communities/{slug}",
    }


def scrape():
    locs = [parse_detail(s) for s in find_slugs()]
    bad = [l["slug"] for l in locs if not (l["street"] and l["zip"])]
    if bad:
        print("WARNING: could not parse address for:", bad)
    return locs


if __name__ == "__main__":
    for l in scrape():
        print(l["name"], "|", l["street"], l["city"], l["state"], l["zip"], "|", l["care_offerings"])

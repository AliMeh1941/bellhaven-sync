"""Text cleanup so website and CRM values can be compared fairly."""
import re
from difflib import SequenceMatcher

ABBR = {
    "street": "st", "avenue": "ave", "av": "ave", "road": "rd", "drive": "dr",
    "boulevard": "blvd", "lane": "ln", "court": "ct", "place": "pl",
    "parkway": "pkwy", "highway": "hwy", "circle": "cir", "terrace": "ter",
    "trail": "trl", "square": "sq", "route": "rte", "north": "n", "south": "s",
    "east": "e", "west": "w", "northwest": "nw", "northeast": "ne",
    "southwest": "sw", "southeast": "se", "pk": "pike", "pke": "pike",
    "centre": "center",
}

# Website wording -> CRM care_type picklist
CARE_MAP = {
    "short term rehabilitation and nursing": "Skilled Nursing",
    "skilled nursing": "Skilled Nursing",
    "memory support": "Memory Care",
    "memory care": "Memory Care",
    "assisted living": "Assisted Living",
    "independent living": "Independent Living",
}


def crm_care(site_offerings):
    """Website offerings mapped to CRM values, in website order."""
    out = []
    for x in site_offerings or []:
        v = CARE_MAP.get(clean(x))
        if v and v not in out:
            out.append(v)
    return out


def is_po_box(s):
    return bool(re.match(r"\s*p\.?\s*o\.?\s*box\b", s or "", re.I))
UNIT_RE = re.compile(r"(\b(suite|ste|unit|apt|bldg|building)\b|#).*$", re.I)
NAME_STOP = {"bellhaven", "the", "of", "at", "and", "senior", "living", "llc", "inc"}


def clean(s):
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(s.split())


def norm_street(s):
    s = UNIT_RE.sub("", s or "")
    return " ".join(ABBR.get(t, t) for t in clean(s).split())


def street_number(s):
    m = re.match(r"\s*(\d+)", s or "")
    return m.group(1) if m else None


def norm_zip(z):
    m = re.search(r"\d{5}", str(z or ""))
    return m.group(0) if m else None


def norm_name(s):
    return " ".join("center" if t == "centre" else t for t in clean(s).split())


def core_name(s):
    """Name without brand and filler words: 'Bellhaven of Marion' -> 'marion'."""
    return " ".join(t for t in norm_name(s).split() if t not in NAME_STOP)


def similarity(a, b):
    """0 to 100, word order insensitive."""
    if not a or not b:
        return 0
    a2, b2 = " ".join(sorted(a.split())), " ".join(sorted(b.split()))
    return round(100 * max(SequenceMatcher(None, a, b).ratio(),
                           SequenceMatcher(None, a2, b2).ratio()))


def care_set(v):
    if not v:
        return set()
    items = v if isinstance(v, list) else re.split(r"[;,|]", str(v))
    return {clean(x) for x in items if clean(x)}


def money(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0

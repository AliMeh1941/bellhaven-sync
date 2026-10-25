"""
Step 1: Explore the Bellhaven website and CRM API.
Saves everything into ./explore/ so we can plan the matching logic.

Setup:
    pip install requests beautifulsoup4 pandas
    Windows PowerShell:  $env:BH_TOKEN="your_token"
    Mac/Linux:           export BH_TOKEN="your_token"
Run:
    python explore.py
"""
import os
import re
import json
import requests
import pandas as pd
from bs4 import BeautifulSoup

BASE = "https://analyst-assessment-production.up.railway.app"
API = BASE + "/api/v1"
TOKEN = os.environ["BH_TOKEN"]
H = {"Authorization": f"Bearer {TOKEN}"}
OUT = "explore"
os.makedirs(OUT, exist_ok=True)


# ---------------------------------------------------------------
# 1. API spec
# ---------------------------------------------------------------
print("=" * 60, "\n1. API SPEC\n", "=" * 60)
spec = None
for p in ["/api/openapi.json", "/openapi.json", "/api/v1/openapi.json"]:
    r = requests.get(BASE + p, headers=H, timeout=30)
    if r.ok and '"paths"' in r.text:
        spec = r.json()
        with open(f"{OUT}/openapi.json", "w") as f:
            json.dump(spec, f, indent=2)
        print("Spec found at", p)
        break

if spec:
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            params = [x["name"] for x in op.get("parameters", [])]
            print(f"  {method.upper():6} {path}  params={params}")
else:
    print("Spec not found. Check /api/docs in your browser.")


# ---------------------------------------------------------------
# 2. All CRM accounts
# ---------------------------------------------------------------
print("\n", "=" * 60, "\n2. CRM ACCOUNTS\n", "=" * 60)


def extract_rows(payload):
    """Handle a plain list or a dict wrapping the list."""
    if isinstance(payload, list):
        return payload
    for k in ["items", "data", "results", "accounts"]:
        if isinstance(payload.get(k), list):
            return payload[k]
    return []


first = requests.get(f"{API}/accounts", headers=H, timeout=30)
print("Status:", first.status_code)
raw = first.json()
with open(f"{OUT}/accounts_raw_first_call.json", "w") as f:
    json.dump(raw, f, indent=2)
if isinstance(raw, dict):
    print("Top level keys:", list(raw.keys()))

# Page through until no new ids show up (works whatever the paging style)
from crm import CRM
accounts = CRM().list_accounts()

print("Total accounts pulled:", len(accounts))
with open(f"{OUT}/accounts.json", "w") as f:
    json.dump(accounts, f, indent=2)

df = pd.json_normalize(accounts)
df.to_csv(f"{OUT}/accounts.csv", index=False)
print("Columns:", list(df.columns))

if "parent_id" in df.columns:
    names = dict(zip(df["account_id"], df["name"]))
    counts = df["parent_id"].value_counts()
    print("\nParent accounts and how many children each has:")
    for pid, n in counts.items():
        print(f"  {pid}  {names.get(pid, '(parent not in list)')}: {n}")
    print("\nAccounts with no parent:", int(df["parent_id"].isna().sum()))

for col in ["status"]:
    if col in df.columns:
        print(f"\n{col} values:\n", df[col].value_counts(dropna=False))


# ---------------------------------------------------------------
# 3. Website scrape
# ---------------------------------------------------------------
print("\n", "=" * 60, "\n3. WEBSITE\n", "=" * 60)


def soup(url):
    return BeautifulSoup(requests.get(url, timeout=30).text, "html.parser")


def community_links(s):
    out = set()
    for a in s.select("a[href]"):
        href = a["href"]
        m = re.search(r"/communities/([^/?#]+)", href)
        if m:
            out.add(m.group(1))
    return out


slugs = set()
slugs |= community_links(soup(BASE))           # home page (catches "new" ones)
for page in range(1, 20):                       # directory, stop when nothing new
    found = community_links(soup(f"{BASE}/communities?page={page}"))
    if not (found - slugs) and page > 1:
        break
    slugs |= found
print("Unique community slugs found:", len(slugs))

LABELS = ["Address", "Care Offerings", "Administrator", "Phone"]


def parse_detail(slug):
    s = soup(f"{BASE}/communities/{slug}")
    lines = [l.strip() for l in s.get_text("\n").split("\n") if l.strip()]
    rec = {"slug": slug, "name": s.find("h1").get_text(strip=True)}
    for i, l in enumerate(lines):
        if l in LABELS:
            vals = []
            for nxt in lines[i + 1:]:
                if nxt in LABELS or nxt.startswith("←"):
                    break
                vals.append(nxt)
            rec[l] = vals
    addr = rec.get("Address", [])
    rec["street"] = addr[0] if addr else None
    m = re.match(r"(.+),\s*([A-Z]{2})\s+(\d{5})", addr[1]) if len(addr) > 1 else None
    rec["city"], rec["state"], rec["zip"] = (m.groups() if m else (None, None, None))
    rec["care_offerings"] = "; ".join(rec.pop("Care Offerings", []))
    rec["administrator"] = " ".join(rec.pop("Administrator", []))
    rec["phone"] = " ".join(rec.pop("Phone", []))
    rec.pop("Address", None)
    return rec


site = pd.DataFrame([parse_detail(s) for s in sorted(slugs)])
site.to_csv(f"{OUT}/website.csv", index=False)
print(site[["name", "city", "state", "zip", "care_offerings"]].to_string())

print("\nDone. Attach these files back in chat:")
for f in sorted(os.listdir(OUT)):
    print("  explore/" + f)

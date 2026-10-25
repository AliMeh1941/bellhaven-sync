"""Daily job: scrape -> pull CRM -> build proposals -> queue new ones for review.
Never writes to the CRM. Writes only happen when a reviewer approves in app.py."""
import json
import os

from crm import CRM
from matcher import build
from scraper import scrape
from store import Store


def run(store=None):
    store = store or Store()
    site = scrape()
    accounts = CRM().list_accounts()
    os.makedirs("state", exist_ok=True)
    with open("state/last_site_snapshot.json", "w") as f:
        json.dump(site, f, indent=2)
    proposals, summary = build(site, accounts)
    return store.sync(proposals, summary)


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))

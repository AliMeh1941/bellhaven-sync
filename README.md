# bellhaven-sync

Keeps the CRM's picture of Bellhaven Senior Living correct. Every day it scrapes
Bellhaven's website, compares each community to the CRM, and queues proposed
fixes. A reviewer approves or rejects each one in a small app. **Nothing is written
to the CRM without approval.**

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows   (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
$env:BH_TOKEN="your_token"      # Windows PowerShell   (Mac/Linux: export BH_TOKEN=...)
```

## Run

```bash
python pipeline.py              # scrape + match + queue proposals (no CRM writes)
streamlit run app.py            # review queue; Approve writes to the CRM
python -m unittest discover tests
```

## Files

| File | Job |
|---|---|
| `scraper.py` | Every community from the website (home page + all directory pages + each detail page) |
| `crm.py` | API client; maps raw account fields to standard names |
| `normalize.py` | Address and name cleanup (St/Street, suite numbers, "Centre"/"Center") |
| `matcher.py` | Matching and classification; builds proposals with evidence |
| `executor.py` | Applies an approved proposal with safety checks |
| `store.py` | SQLite memory of every proposal and decision |
| `app.py` | Streamlit review app |
| `.github/workflows/daily-sync.yml` | Daily schedule |

## How matching works

1. **Address first.** Same street number, street name at least 88% similar after
   cleanup, and same zip (or same city and state). Names change after acquisitions;
   buildings don't move.
2. **Name as backup** only when the CRM record has no street, and city and state agree.
3. **Weak matches are never linked**, only shown to the reviewer as "possible matches".
4. One CRM account can match only one website location.

## What it proposes

| Type | When | What it writes |
|---|---|---|
| FIX | Matched, right parent, fields stale | Name, address, care offerings, status Active |
| REPARENT | Wrong parent, **no** revenue history or **no** open AR | `parent_id` = Bellhaven (+ fixes) |
| CHOW | Wrong parent, revenue history **and** open AR | Old account untouched. New account under Bellhaven. Old `chow_current_account` = new id |
| CHOW_LINK | Old billed account + a Bellhaven account already exists | Old `chow_current_account` = existing id |
| CREATE | On website, not in CRM | New account under Bellhaven |
| DUPLICATE | Two CRM records for one facility | Loser: `duplicate_of_account`, Inactive, note |
| STALE | Under Bellhaven but not on website | Same address under another owner: duplicate (or CHOW link if billed). Otherwise Needs Review + note |

Duplicate tie-break: keep the record with billing history, then the one under the right
parent, then Active, then closest name, then oldest id.

## Safety

- **SOP enforced twice:** when the proposal is built, and again at write time on fresh
  data. A parent change is refused if the account now has revenue and open AR.
- **Stale check:** each update stores the values seen at proposal time. If the account
  changed since, the write is blocked and the reviewer is told to re-run.
- All checks run before the first write, so a blocked proposal writes nothing.
- After each write the account is read back to confirm the change stuck.

## Safe re-runs

Each proposal gets a fingerprint (type + account + website location + proposed values,
ignoring dated notes). The store keeps every decision:
- Approved or rejected fingerprints are never queued again.
- Approved changes fix the CRM, so the next run doesn't produce them anyway.
- Accounts already marked duplicate or CHOW'd are skipped by matching.
- Pending items no longer produced (CRM fixed by someone else) become "superseded".
- If the website changes later (new name, new address), the fingerprint changes and a
  fresh proposal appears.

## Schedule

GitHub Actions, daily at 6:17 AM Toronto (`.github/workflows/daily-sync.yml`), token in
repo secret `BH_TOKEN`. Local cron alternative:

```
17 6 * * * cd /path/to/bellhaven-sync && .venv/bin/python pipeline.py >> state/pipeline.log 2>&1
```

"""Demo helper: puts two small, realistic data errors into YOUR sandbox CRM, as if a
rep had made typos, so the daily pipeline has something fresh to catch.

    python demo_drift.py         introduce the two errors
    python demo_drift.py undo    put the correct values back directly (only if needed)

The normal way to fix them is to run the pipeline and approve in the app.
"""
import sys

from crm import CRM

# (account id, field, wrong value, correct value)
ERRORS = [
    ("001CF3LDWVRGL09P4F", "billing_zip", "45626", "45662"),            # Bellhaven of Portsmouth
    ("001JQER1RMYKNRLNME", "name", "Goshen Assisted Living", "Bellhaven of Goshen"),
]

undo = len(sys.argv) > 1 and sys.argv[1] == "undo"
crm = CRM()
for acct, field, wrong, right in ERRORS:
    cur = crm.get(acct)
    expected_now, new = (wrong, right) if undo else (right, wrong)
    if str(cur.get(field)) != expected_now:
        print(f"SKIPPED {cur.get('name')}: {field} is {cur.get(field)!r}, "
              f"expected {expected_now!r}. Nothing changed.")
        continue
    crm.update(acct, {field: new})
    print(f"{cur.get('name')}: {field} {expected_now!r} -> {new!r}")

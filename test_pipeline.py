"""Run:  python -m unittest discover tests -v
Fake site + fake CRM covering every proposal type, the CHOW SOP, and re-run safety."""
import copy
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executor import BlockedError, apply  # noqa: E402
from matcher import build  # noqa: E402
from store import Store  # noqa: E402

REHAB, AL = "Short-Term Rehabilitation & Nursing", "Assisted Living"


def loc(slug, name, street, city, zip_, care=(AL,)):
    return {"slug": slug, "name": name, "street": street, "city": city, "state": "OH",
            "zip": zip_, "care_offerings": list(care), "url": f"http://x/{slug}"}


def acct(id_, name, street, city, zip_, parent, status="Active", rev=0, ar=0,
         care=AL, **extra):
    return {"account_id": id_, "name": name, "billing_street": street,
            "billing_city": city, "billing_state": "OH", "billing_zip": zip_,
            "parent_id": parent, "status": status, "note": None,
            "care_type": care, "lifetime_revenue": rev, "outstanding_ar": ar,
            "duplicate_of_account": None, "chow_current_account": None, **extra}


SITE = [
    loc("marion", "Bellhaven of Marion", "100 Main St", "Marion", "43302"),
    loc("tiffin", "Bellhaven of Tiffin", "200 Oak Ave", "Tiffin", "44883"),
    loc("akron", "Bellhaven Terrace of Akron", "300 Elm Rd", "Akron", "44301"),
    loc("toledo", "Bellhaven Woods of Toledo", "400 Pine Dr", "Toledo", "43601"),
    loc("findlay", "Bellhaven Meadows of Findlay", "1800 N Blanchard St", "Findlay", "45840"),
    loc("kettering", "Bellhaven of Kettering", "500 Maple Ln", "Kettering", "45429"),
    loc("lima", "Bellhaven Crossings of Lima", "600 Cedar St", "Lima", "45801"),
]

CRM_DATA = [
    acct(1, "Bellhaven Senior Living", "400 Bellhaven Way", "Columbus", "43215", None),
    acct(2, "Harborview Care Group", "1 Harbor Rd", "Cleveland", "44101", None),
    acct(3, "Cedar Trail Senior Living", "9 Trail Rd", "Dayton", "45402", None),
    acct(4, "Bellhaven Senior Living Inc", "", "Columbus", "", None),        # dup parent
    acct(10, "Bellhaven of Marion", "100 Main Street", "Marion", "43302", 1),  # clean
    acct(11, "Harborview of Tiffin", "200 Oak Avenue", "Tiffin", "44883", 2),  # reparent
    acct(12, "Cedar Trail Akron", "300 Elm Road Suite 2", "Akron", "44301", 3,
         rev=5000, ar=1200),                                                   # CHOW
    acct(13, "Bellhaven Woods Toledo", "400 Pine Dr", "Toledo", "43601", 1,
         status="Needs Review"),                                               # fix
    acct(14, "Bellhaven of Kettering", "500 Maple Ln", "Kettering", "45429", 1,
         rev=900, ar=0),                                                       # survivor
    acct(15, "Bellhaven Kettering", "500 Maple Lane", "Kettering", "45429", 1),  # dup
    acct(19, "Bellhaven Crossings of Lima", "600 Cedar St", "Lima", "45801", 1),
    acct(20, "Harborview of Lima", "600 Cedar Street", "Lima", "45801", 2,
         rev=300, ar=50),                                                      # CHOW_LINK
    acct(16, "Bellhaven of Oldtown", "900 Gone Rd", "Oldtown", "40000", 1),    # stale
    acct(21, "Bellhaven of Riverside", "700 River Rd", "Riverside", "45000", 1),  # sold
    acct(22, "Cedar Trail Riverside", "700 River Road", "Riverside", "45000", 3),
    acct(30, "Unrelated Care Home", "5 Other St", "Marion", "43302", None),
    acct(31, "Unrelated Child", "6 Other St", "Marion", "43302", 30),
]


def by_type(props):
    out = {}
    for p in props:
        out.setdefault(p["type"], []).append(p)
    return out


class FakeCRM:
    def __init__(self, rows):
        self.rows = {r["account_id"]: copy.deepcopy(r) for r in rows}
        self.writes = []

    def get(self, i):
        return copy.deepcopy(self.rows[i])

    def update(self, i, fields):
        self.writes.append(("update", i, fields))
        self.rows[i].update(fields)
        return self.rows[i]

    def create(self, fields):
        new_id = max(self.rows) + 1
        self.rows[new_id] = {"account_id": new_id, **fields}
        self.writes.append(("create", new_id, fields))
        return self.rows[new_id]

    def list_accounts(self):
        return list(copy.deepcopy(self.rows).values())


class TestMatching(unittest.TestCase):
    def setUp(self):
        self.props, self.summary = build(SITE, CRM_DATA)
        self.t = by_type(self.props)

    def test_parent_and_clean_match(self):
        self.assertEqual(self.summary["bellhaven_parent"], 1)
        self.assertEqual(self.summary["clean_matches"], 3)   # Marion, Kettering(14), Lima(19)
        self.assertFalse(any(p["account_id"] == 10 for p in self.props))

    def test_reparent_without_billing(self):
        p = [p for p in self.t["REPARENT"] if p["account_id"] == 11][0]
        s = p["actions"][0]["set"]
        self.assertEqual(s["parent_id"], 1)
        self.assertEqual(s["name"], "Bellhaven of Tiffin")

    def test_chow_with_revenue_and_ar(self):
        p = [p for p in self.t["CHOW"] if p["account_id"] == 12][0]
        create, link = p["actions"]
        self.assertEqual(create["op"], "create")
        self.assertEqual(create["set"]["parent_id"], 1)
        self.assertEqual(link["set"], {"chow_current_account": "$new"})  # nothing else
        self.assertNotIn(12, [p["account_id"] for p in self.t.get("REPARENT", [])])

    def test_fix_name_and_status(self):
        p = [p for p in self.t["FIX"] if p["account_id"] == 13][0]
        self.assertEqual(p["actions"][0]["set"]["status"], "Active")
        self.assertEqual(p["actions"][0]["set"]["name"], "Bellhaven Woods of Toledo")

    def test_create_missing(self):
        self.assertEqual([p["site_slug"] for p in self.t["CREATE"]], ["findlay"])

    def test_duplicate_keeps_billed_copy(self):
        p = [p for p in self.t["DUPLICATE"] if p["account_id"] == 15][0]
        s = p["actions"][0]["set"]
        self.assertEqual(s["duplicate_of_account"], 14)
        self.assertEqual(s["status"], "Inactive")

    def test_chow_link_to_existing(self):
        p = self.t["CHOW_LINK"][0]
        self.assertEqual(p["account_id"], 20)
        self.assertEqual(p["actions"][0]["set"], {"chow_current_account": 19})

    def test_stale_cases(self):
        stale = {p["account_id"]: p for p in self.t["STALE"]}
        self.assertEqual(stale[16]["actions"][0]["set"]["status"], "Needs Review")
        self.assertEqual(stale[21]["actions"][0]["set"]["duplicate_of_account"], 22)
        self.assertNotIn(22, stale)        # Cedar Trail's own record is left alone

    def test_duplicate_parent(self):
        self.assertTrue(any(p["account_id"] == 4 for p in self.t["DUPLICATE"]))

    def test_unrelated_untouched(self):
        ids = {p["account_id"] for p in self.props}
        self.assertFalse(ids & {2, 3, 30, 31})


class TestExecutor(unittest.TestCase):
    def setUp(self):
        self.props, _ = build(SITE, CRM_DATA)
        self.t = by_type(self.props)

    def test_chow_apply_keeps_old_account(self):
        crm = FakeCRM(CRM_DATA)
        before = crm.get(12)
        apply(self.t["CHOW"][0], crm)
        after = crm.get(12)
        new_id = after["chow_current_account"]
        self.assertEqual(crm.get(new_id)["parent_id"], 1)
        self.assertEqual({k: v for k, v in after.items() if k != "chow_current_account"},
                         {k: v for k, v in before.items() if k != "chow_current_account"})

    def test_sop_guard_blocks_if_billing_appears(self):
        data = copy.deepcopy(CRM_DATA)
        crm = FakeCRM(data)
        crm.rows[11]["lifetime_revenue"], crm.rows[11]["outstanding_ar"] = 100, 10
        p = [p for p in self.t["REPARENT"] if p["account_id"] == 11][0]
        with self.assertRaises(BlockedError):
            apply(p, crm)
        self.assertEqual(crm.writes, [])      # nothing written

    def test_stale_data_blocks(self):
        crm = FakeCRM(CRM_DATA)
        crm.rows[13]["name"] = "Someone renamed it"
        with self.assertRaises(BlockedError):
            apply(self.t["FIX"][0], crm)
        self.assertEqual(crm.writes, [])


class TestRerunSafety(unittest.TestCase):
    def test_decided_items_never_come_back(self):
        db = os.path.join(tempfile.mkdtemp(), "t.db")
        store = Store(db)
        props, summary = build(SITE, CRM_DATA)
        first = store.sync(props, summary)
        self.assertEqual(first["new"], len(props))

        rejected = store.list("pending")[0]["key"]
        store.decide(rejected, "rejected")

        props2, summary2 = build(SITE, CRM_DATA)     # same day or next day
        second = store.sync(props2, summary2)
        self.assertEqual(second["new"], 0)
        self.assertEqual(second["already_decided"], 1)
        self.assertNotIn(rejected, [r["key"] for r in store.list("pending")])

    def test_recurring_error_is_proposed_again(self):
        """Fix approved, then someone re-enters the same typo: it must come back."""
        store, crm = Store(os.path.join(tempfile.mkdtemp(), "t.db")), FakeCRM(CRM_DATA)
        props, summary = build(SITE, crm.list_accounts())
        store.sync(props, summary)
        for p in store.list("pending"):
            apply(p, crm)
            store.decide(p["key"], "approved")
        # exact same bad values come back
        crm.rows[13]["name"], crm.rows[13]["status"] = "Bellhaven Woods Toledo", "Needs Review"
        props2, summary2 = build(SITE, crm.list_accounts())
        self.assertEqual(len(props2), 1)
        s = store.sync(props2, summary2)
        self.assertEqual(s["came_back"], 1)
        back = store.list("pending")[0]
        self.assertIn("came back", back["evidence"]["warning"])
        self.assertEqual(json.loads(back["history"])[0]["status"], "approved")
        # a rejected item still never comes back
        store.decide(back["key"], "rejected")
        self.assertEqual(store.sync(*build(SITE, crm.list_accounts()))["already_decided"], 1)

    def test_retired_items_tagged_with_latest_run(self):
        store = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        props, summary = build(SITE, CRM_DATA)
        store.sync(props, summary)
        dropped = props[0]["key"]
        s2 = store.sync(props[1:], summary)          # next run no longer produces it
        self.assertEqual(s2["superseded"], 1)
        retired = store.list("superseded")
        self.assertEqual([r["key"] for r in retired], [dropped])
        self.assertEqual(retired[0]["decided_at"], store.last_run_id())

    def test_undo_reject_only_for_rejected(self):
        store = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        props, summary = build(SITE, CRM_DATA)
        store.sync(props, summary)
        a, b = [r["key"] for r in store.list("pending")[:2]]
        store.decide(a, "rejected")
        store.decide(b, "approved")
        self.assertTrue(store.reopen(a))
        self.assertFalse(store.reopen(b))          # approved stays approved
        self.assertIn(a, [r["key"] for r in store.list("pending")])

    def test_approved_changes_produce_nothing_next_run(self):
        db = os.path.join(tempfile.mkdtemp(), "t.db")
        store, crm = Store(db), FakeCRM(CRM_DATA)
        props, summary = build(SITE, crm.list_accounts())
        store.sync(props, summary)
        for p in store.list("pending"):
            apply(p, crm)
            store.decide(p["key"], "approved")
        props2, summary2 = build(SITE, crm.list_accounts())
        self.assertEqual(props2, [], [p["title"] for p in props2])
        self.assertEqual(store.sync(props2, summary2)["new"], 0)


if __name__ == "__main__":
    unittest.main()

"""SQLite memory of every proposal and decision. This is what makes re-runs safe."""
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone

import config as C

SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
  key TEXT PRIMARY KEY, type TEXT, title TEXT, account_id TEXT, site_slug TEXT,
  evidence TEXT, actions TEXT,
  status TEXT,              -- pending | approved | rejected | superseded
  first_seen TEXT, last_seen_run TEXT, decided_at TEXT,
  reviewer_note TEXT, result TEXT, last_error TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, started TEXT, summary TEXT
);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path=None):
        path = path or C.DB_PATH
        if os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        try:                                   # add history column to older databases
            self.db.execute("ALTER TABLE proposals ADD COLUMN history TEXT")
        except sqlite3.OperationalError:
            pass

    def sync(self, proposals, summary):
        """Rules for each proposal this run produced:
          never seen  -> new, pending
          pending     -> refresh its evidence
          rejected    -> skip (a reviewer said no; it never comes back)
          approved    -> the same problem is back, so the fix didn't stick: put it back
                         in the queue, keeping the earlier approval in its history
        Pending items the latest run no longer produces become 'superseded'."""
        run_id = f"{now()}-{uuid.uuid4().hex[:6]}"
        counts = {"new": 0, "still_pending": 0, "already_decided": 0, "came_back": 0,
                  "superseded": 0}
        keys = set()
        for p in proposals:
            keys.add(p["key"])
            row = self.db.execute("SELECT * FROM proposals WHERE key=?",
                                  (p["key"],)).fetchone()
            if row is None:
                self.db.execute(
                    "INSERT INTO proposals (key,type,title,account_id,site_slug,evidence,"
                    "actions,status,first_seen,last_seen_run) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (p["key"], p["type"], p["title"], str(p["account_id"] or ""),
                     p["site_slug"], json.dumps(p["evidence"], default=str),
                     json.dumps(p["actions"], default=str), "pending", run_id, run_id))
                counts["new"] += 1
            elif row["status"] == "pending":
                # refresh evidence so the reviewer sees today's data
                self.db.execute("UPDATE proposals SET evidence=?, actions=?, last_seen_run=? "
                                "WHERE key=?", (json.dumps(p["evidence"], default=str),
                                                json.dumps(p["actions"], default=str),
                                                run_id, p["key"]))
                counts["still_pending"] += 1
            elif row["status"] == "approved":
                history = json.loads(row["history"] or "[]")
                history.append({"status": "approved", "decided_at": row["decided_at"],
                                "note": row["reviewer_note"], "result": row["result"]})
                ev = dict(p["evidence"])
                ev["warning"] = (f"This came back: it was approved on "
                                 f"{(row['decided_at'] or '')[:10]}, but the CRM has the old "
                                 "value again (someone may have changed it back).")
                self.db.execute(
                    "UPDATE proposals SET status='pending', evidence=?, actions=?, "
                    "last_seen_run=?, decided_at=NULL, reviewer_note=NULL, result=NULL, "
                    "history=? WHERE key=?",
                    (json.dumps(ev, default=str), json.dumps(p["actions"], default=str),
                     run_id, json.dumps(history, default=str), p["key"]))
                counts["came_back"] += 1
            else:
                counts["already_decided"] += 1
        for row in self.db.execute("SELECT key FROM proposals WHERE status='pending'").fetchall():
            if row["key"] not in keys:
                self.db.execute("UPDATE proposals SET status='superseded', decided_at=? "
                                "WHERE key=?", (run_id, row["key"]))
                counts["superseded"] += 1
        summary = {**summary, **counts}
        self.db.execute("INSERT INTO runs VALUES (?,?,?)", (run_id, run_id, json.dumps(summary)))
        self.db.commit()
        return summary

    def list(self, status=None):
        q, args = "SELECT * FROM proposals", ()
        if status:
            q, args = q + " WHERE status=?", (status,)
        rows = self.db.execute(q + " ORDER BY type, title", args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["evidence"] = json.loads(d["evidence"])
            d["actions"] = json.loads(d["actions"])
            d["result"] = json.loads(d["result"]) if d["result"] else None
            out.append(d)
        return out

    def decide(self, key, status, note="", result=None):
        self.db.execute("UPDATE proposals SET status=?, decided_at=?, reviewer_note=?, "
                        "result=?, last_error=NULL WHERE key=?",
                        (status, now(), note, json.dumps(result, default=str) if result else None,
                         key))
        self.db.commit()

    def reopen(self, key):
        """Undo a reject: back to pending. Approved items can't be reopened,
        because their change is already in the CRM."""
        cur = self.db.execute("UPDATE proposals SET status='pending', decided_at=NULL, "
                              "reviewer_note=NULL WHERE key=? AND status='rejected'", (key,))
        self.db.commit()
        return cur.rowcount == 1

    def error(self, key, msg):
        self.db.execute("UPDATE proposals SET last_error=? WHERE key=?", (msg, key))
        self.db.commit()

    def last_run_id(self):
        r = self.db.execute("SELECT run_id FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
        return r["run_id"] if r else None

    def last_run(self):
        r = self.db.execute("SELECT * FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
        return json.loads(r["summary"]) if r else None

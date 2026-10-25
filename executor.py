"""Apply one approved proposal to the CRM, safely.

Safety checks before anything is written:
  1. Every account we update must still hold the values we saw when the
     proposal was built ('expect'). If someone changed it, stop.
  2. Any parent change re-checks the billing SOP on fresh data.
  3. A CHOW re-checks that revenue and AR are still both above zero.
All checks run before the first write, so a failed check writes nothing.
"""
import config as C
from crm import acct_id
from normalize import money


class BlockedError(Exception):
    pass


def _same(a, b):
    if a in (None, "") and b in (None, ""):
        return True
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


def _key(name):
    return C.ALIASES[name][0]


def preflight(proposal, crm):
    fresh = {}
    for op in proposal["actions"]:
        if op["op"] != "update":
            continue
        cur = crm.get(op["id"])
        fresh[op["id"]] = cur
        for field, expected in op.get("expect", {}).items():
            if not _same(cur.get(field), expected):
                raise BlockedError(f'Account {op["id"]} changed since this was proposed: '
                                   f'{field} is now {cur.get(field)!r}, expected {expected!r}. '
                                   'Re-run the pipeline.')
        rev = money(cur.get(_key("lifetime_revenue")))
        ar = money(cur.get(_key("outstanding_ar")))
        if _key("parent_id") in op["set"] and rev > 0 and ar > 0:
            raise BlockedError(f'SOP block: account {op["id"]} has revenue and open AR, '
                               'so its parent must not change.')
        if proposal["type"] == "CHOW" and op["set"].get(_key("chow_current_account")) == "$new" \
                and not (rev > 0 and ar > 0):
            raise BlockedError("Billing changed: CHOW no longer required. Re-run the pipeline.")
    return fresh


def apply(proposal, crm):
    preflight(proposal, crm)
    refs, log = {}, []
    for op in proposal["actions"]:
        if op["op"] == "create":
            new = crm.create(op["set"])
            new_id = acct_id(new)
            if new_id is None:
                raise RuntimeError(f"Create returned no id: {new}")
            refs[op["ref"]] = new_id
            log.append({"created": new_id, "fields": op["set"]})
        else:
            payload = {k: refs[v[1:]] if isinstance(v, str) and v.startswith("$") else v
                       for k, v in op["set"].items()}
            crm.update(op["id"], payload)
            after = crm.get(op["id"])
            bad = {k: after.get(k) for k, v in payload.items() if not _same(after.get(k), v)}
            if bad:
                raise RuntimeError(f'Write to {op["id"]} did not stick: {bad}')
            log.append({"updated": op["id"], "fields": payload})
    return log

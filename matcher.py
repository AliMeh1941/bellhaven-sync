"""Turn (website locations, CRM accounts) into reviewable proposals.

Proposal types
  FIX        matched, right parent, some fields stale (name, address, care, status)
  REPARENT   matched, wrong parent, no billing block: move it (plus field fixes)
  CHOW       matched, wrong parent, revenue AND open AR: keep old account as is,
             create a new one under Bellhaven, link old -> new via chow_current_account
  CHOW_LINK  a second matching account already exists under Bellhaven: just link the
             old billed account to it instead of creating another one
  CREATE     on the website, no CRM account
  DUPLICATE  extra CRM copy of a matched facility: Inactive + duplicate_of_account
  STALE      under Bellhaven (or named Bellhaven) but not on the website
Confident matches with nothing to change produce no proposal; they are counted.
"""
import hashlib
import json
from datetime import date

import config as C
from crm import std
from normalize import (core_name, crm_care, is_po_box, money, norm_name,
                       norm_street, norm_zip, similarity, street_number)

# ---------------------------------------------------------------- helpers


def has_billing_block(a):
    """SOP: revenue history AND outstanding AR > 0 means do not re-parent."""
    return money(a["lifetime_revenue"]) > 0 and money(a["outstanding_ar"]) > 0


def retired(a):
    """Already resolved in an earlier run: duplicate or CHOW'd. Never re-match."""
    return bool(a["duplicate_of_account"]) or bool(a["chow_current_account"])


def score(loc, a):
    st_sim = similarity(norm_street(loc["street"]), norm_street(a["street"]))
    num_eq = bool(street_number(loc["street"])) and \
        street_number(loc["street"]) == street_number(a["street"])
    zip_eq = bool(norm_zip(a["zip"])) and norm_zip(loc["zip"]) == norm_zip(a["zip"])
    city_eq = norm_name(loc["city"]) == norm_name(a["city"])
    state_eq = (loc["state"] or "").upper() == (a["state"] or "").upper()
    name_sim = max(similarity(core_name(loc["name"]), core_name(a["name"])),
                   similarity(norm_name(loc["name"]), norm_name(a["name"])))

    if num_eq and st_sim >= C.STREET_MATCH and (zip_eq or (city_eq and state_eq)):
        level = "address"
    elif (not a["street"] or is_po_box(a["street"])) and city_eq and state_eq \
            and name_sim >= C.NAME_MATCH:
        level = "name"          # CRM has no usable street (blank or PO Box)
    elif (city_eq and state_eq and name_sim >= C.POSSIBLE_NAME) or (zip_eq and st_sim >= 70):
        level = "possible"      # shown as evidence only, never auto-linked
    else:
        level = None
    return {"level": level, "street_sim": st_sim, "number_eq": num_eq,
            "zip_eq": zip_eq, "city_eq": city_eq, "name_sim": name_sim}


def view(a, by_id):
    """What the reviewer sees for a CRM account."""
    if a is None:
        return None
    p = by_id.get(a["parent_id"])
    return {
        "id": a["id"], "name": a["name"], "street": a["street"], "city": a["city"],
        "state": a["state"], "zip": a["zip"], "status": a["status"],
        "parent": f'{p["name"]} ({p["id"]})' if p else (a["parent_id"] or "(none)"),
        "care_offerings": a["care"], "lifetime_revenue": money(a["lifetime_revenue"]),
        "outstanding_ar": money(a["outstanding_ar"]), "note": a["note"],
    }


def site_view(loc):
    return {k: loc.get(k) for k in
            ("name", "street", "city", "state", "zip", "care_offerings", "url")}


def ownership_note(old_parent):
    """Effective date isn't published, so record what we know and say so."""
    old_parent = (old_parent or "").replace("(Parent Account)", "").strip()
    year = next((y for k, y in C.ACQUISITIONS.items()
                 if k in norm_name(old_parent or "")), None)
    src = (f" Per Bellhaven About page, joined from {old_parent} in {year}."
           if year else "")
    return (f"Ownership change to Bellhaven detected {date.today().isoformat()} "
            f"(previous parent: {old_parent or 'none'}).{src} Exact effective date unknown.")


def append_note(a, text):
    stamp = f"[bellhaven-sync {date.today().isoformat()}] {text}"
    return f'{a["note"]}\n{stamp}' if a["note"] else stamp


def proposal(ptype, title, actions, evidence, account=None, loc=None):
    fingerprint = {
        "type": ptype,
        "account": account["id"] if account else None,
        "site": loc["slug"] if loc else None,
        # notes carry a date, so leave them out or every run looks "new"
        "changes": [{k: v for k, v in (op.get("set") or {}).items()
                     if k not in ("note", "notes")} for op in actions],
    }
    key = hashlib.sha1(json.dumps(fingerprint, sort_keys=True, default=str)
                       .encode()).hexdigest()[:16]
    return {"key": f"{ptype}:{key}", "type": ptype, "title": title,
            "account_id": account["id"] if account else None,
            "site_slug": loc["slug"] if loc else None,
            "actions": actions, "evidence": evidence}


def update(a, set_fields, also_expect=()):
    """An update op that only runs if the fields still hold what we saw."""
    expect = {a["_keys"][k]: a[k] for k in set(set_fields) | set(also_expect)}
    return {"op": "update", "id": a["id"],
            "set": {a["_keys"][k]: v for k, v in set_fields.items()},
            "expect": expect}


def new_account_fields(loc, parent_id, template, note):
    """CRM care_type holds one value, so a new account gets the first offering."""
    k = template["_keys"]
    out = {k["name"]: loc["name"], k["street"]: loc["street"], k["city"]: loc["city"],
           k["state"]: loc["state"], k["zip"]: loc["zip"],
           k["parent_id"]: parent_id, k["status"]: C.ACTIVE, k["note"]: note}
    care = crm_care(loc["care_offerings"])
    if care:
        out[k["care"]] = care[0]
    if loc.get("phone"):
        out["phone"] = loc["phone"]
    return out


def field_diffs(loc, a):
    d = {}
    if norm_name(loc["name"]) != norm_name(a["name"]):
        d["name"] = loc["name"]
    if loc["street"] and norm_street(loc["street"]) != norm_street(a["street"]):
        d["street"] = loc["street"]
    if loc["city"] and norm_name(loc["city"]) != norm_name(a["city"]):
        d["city"] = loc["city"]
    if loc["state"] and (loc["state"] or "").upper() != (a["state"] or "").upper():
        d["state"] = loc["state"]
    if loc["zip"] and norm_zip(loc["zip"]) != norm_zip(a["zip"]):
        d["zip"] = loc["zip"]
    care = crm_care(loc["care_offerings"])
    if care and a["care"] not in care:     # only flag a real conflict
        d["care"] = care[0]
    if a["status"] != C.ACTIVE:
        d["status"] = C.ACTIVE
    return d


# ---------------------------------------------------------------- parent


def find_bellhaven_parent(accts, parent_ids):
    cands = [a for a in accts if "bellhaven" in norm_name(a["name"]) and not a["parent_id"]
             and (a["id"] in parent_ids or "senior living" in norm_name(a["name"]))]
    if C.BELLHAVEN_PARENT_ID:
        canon = next(a for a in accts if str(a["id"]) == str(C.BELLHAVEN_PARENT_ID))
    elif cands:
        kids = {a["id"]: sum(1 for b in accts if b["parent_id"] == a["id"]) for a in cands}
        canon = max(cands, key=lambda a: (
            norm_street(a["street"]) == norm_street(C.CORPORATE_STREET),
            a["status"] == C.ACTIVE, kids[a["id"]]))
    else:
        raise RuntimeError("No Bellhaven parent account found; set BH_PARENT_ID.")
    return canon, [a for a in cands if a["id"] != canon["id"]]


# ---------------------------------------------------------------- main


def build(site, raw_accounts):
    accts = [std(r) for r in raw_accounts]
    by_id = {a["id"]: a for a in accts}
    parent_ids = {a["parent_id"] for a in accts if a["parent_id"]}
    canon, bh_dupes = find_bellhaven_parent(accts, parent_ids)
    bh_ids = {canon["id"]} | {d["id"] for d in bh_dupes}
    parents = parent_ids | bh_ids
    pool = [a for a in accts if a["id"] not in parents and not retired(a)]

    props, used, clean_matches = [], set(), []

    for loc in site:
        scored = [(a, score(loc, a)) for a in pool]
        hits = [h for h in scored if h[1]["level"] == "address"] or \
               [h for h in scored if h[1]["level"] == "name"]
        hits = [h for h in hits if h[0]["id"] not in used]   # one account, one facility
        possible = [{"account": view(a, by_id), "scores": s}
                    for a, s in scored if s["level"] == "possible"]

        if not hits:
            props.append(proposal(
                "CREATE", f'New location: {loc["name"]} ({loc["city"]}, {loc["state"]})',
                [{"op": "create", "ref": "new",
                  "set": new_account_fields(loc, canon["id"], accts[0],
                                            "Created by bellhaven-sync from Bellhaven website")}],
                {"reason": "On the Bellhaven website, but no CRM account at this address.",
                 "site": site_view(loc), "crm": None, "possible_matches": possible},
                loc=loc))
            continue

        for a, _ in hits:
            used.add(a["id"])
        current = [h for h in hits if h[0]["parent_id"] == canon["id"]]
        group = current or hits
        group.sort(key=lambda h: (not (money(h[0]["lifetime_revenue"]) > 0 or
                                       money(h[0]["outstanding_ar"]) > 0),
                                  h[0]["status"] != C.ACTIVE,
                                  (h[0]["street"] or "").strip() != (loc["street"] or "").strip(),
                                  -h[1]["name_sim"],
                                  -h[1]["street_sim"], str(h[0]["id"])))
        survivor, s_score = group[0]

        # extra copies of the same facility
        for a, s in hits:
            if a is survivor:
                continue
            ev = {"site": site_view(loc), "crm": view(a, by_id),
                  "survivor": view(survivor, by_id), "scores": s}
            if a["parent_id"] != canon["id"] and has_billing_block(a) \
                    and survivor["parent_id"] == canon["id"]:
                ev["reason"] = ("Old-owner account with revenue and open AR. A current "
                                "Bellhaven account already exists, so per SOP keep this one "
                                "untouched and link it via chow_current_account.")
                props.append(proposal(
                    "CHOW_LINK", f'Link old account to current: {a["name"]}',
                    [update(a, {"chow_current_account": survivor["id"]})], ev, a, loc))
            else:
                ev["reason"] = (f'Same facility as account {survivor["id"]}. Kept '
                                f'{survivor["id"]} (better billing history / parent / status).')
                if money(a["outstanding_ar"]) > 0:
                    ev["warning"] = "This copy has open AR. Tell billing."
                props.append(proposal(
                    "DUPLICATE", f'Duplicate: {a["name"]} -> keep {survivor["id"]}',
                    [update(a, {"duplicate_of_account": survivor["id"], "status": C.INACTIVE,
                                "note": append_note(a, f'Duplicate of {survivor["id"]} '
                                                    f'({loc["name"]}).')})], ev, a, loc))

        # the surviving account itself
        a, diffs = survivor, field_diffs(loc, survivor)
        ev = {"site": site_view(loc), "crm": view(a, by_id), "scores": s_score,
              "billing": {"lifetime_revenue": money(a["lifetime_revenue"]),
                          "outstanding_ar": money(a["outstanding_ar"]),
                          "blocks_reparent": has_billing_block(a)}}

        if a["parent_id"] != canon["id"]:
            old_parent = by_id.get(a["parent_id"], {}).get("name", a["parent_id"])
            if has_billing_block(a):
                ev["reason"] = (f'Now Bellhaven, but parent is "{old_parent}". Revenue '
                                f'${money(a["lifetime_revenue"]):,.0f} and open AR '
                                f'${money(a["outstanding_ar"]):,.0f}, so per SOP the old '
                                'account stays exactly as is. Create a new account under '
                                'Bellhaven and point the old one to it.')
                props.append(proposal(
                    "CHOW", f'Change of ownership: {loc["name"]}',
                    [{"op": "create", "ref": "new",
                      "set": new_account_fields(loc, canon["id"], a,
                                                f'CHOW from account {a["id"]}. '
                                                + ownership_note(old_parent))},
                     update(a, {"chow_current_account": "$new"},
                            also_expect=("parent_id", "lifetime_revenue", "outstanding_ar"))],
                    ev, a, loc))
            else:
                ev["reason"] = (f'Now Bellhaven, but parent is "{old_parent}". No revenue '
                                'history or no open AR, so re-parent directly per SOP.')
                props.append(proposal(
                    "REPARENT", f'Move to Bellhaven: {a["name"]}',
                    [update(a, {"parent_id": canon["id"], **diffs,
                                "note": append_note(a, ownership_note(old_parent))},
                            also_expect=("lifetime_revenue", "outstanding_ar"))],
                    ev, a, loc))
        elif diffs:
            ev["reason"] = "Matched and under Bellhaven, but some fields differ from the website."
            props.append(proposal("FIX", f'Update fields: {a["name"]}',
                                  [update(a, diffs)], ev, a, loc))
        else:
            clean_matches.append(a["id"])

    # under Bellhaven (or named Bellhaven) but not on the website
    for a in pool:
        if a["id"] in used:
            continue
        under_bh = a["parent_id"] in bh_ids
        if not (under_bh or "bellhaven" in norm_name(a["name"])):
            continue
        twin = next((b for b in pool if b["id"] != a["id"] and b["parent_id"] not in bh_ids
                     and b["street"] and street_number(b["street"]) == street_number(a["street"])
                     and similarity(norm_street(a["street"]), norm_street(b["street"]))
                     >= C.STREET_MATCH and norm_zip(a["zip"]) == norm_zip(b["zip"])), None)
        ev = {"site": None, "crm": view(a, by_id), "twin": view(twin, by_id),
              "under_bellhaven": under_bh}
        if twin and has_billing_block(a):
            ev["reason"] = (f'Not on the website. Same address now held by account '
                            f'{twin["id"]} under another owner. Has revenue and open AR, '
                            'so per SOP leave it and link via chow_current_account.')
            props.append(proposal("STALE", f'Sold, link to new owner record: {a["name"]}',
                                  [update(a, {"chow_current_account": twin["id"]})], ev, a))
        elif twin:
            ev["reason"] = (f'Not on the website. Same address exists as account '
                            f'{twin["id"]} under another owner: this copy is stale.')
            props.append(proposal("STALE", f'Sold, mark duplicate: {a["name"]}',
                                  [update(a, {"duplicate_of_account": twin["id"],
                                              "status": C.INACTIVE,
                                              "note": append_note(a, f'Not on Bellhaven site; '
                                                                  f'facility now account {twin["id"]}.')})],
                                  ev, a))
        elif a["status"] not in (C.INACTIVE, C.NEEDS_REVIEW):
            where = "under the Bellhaven parent" if under_bh else "named Bellhaven"
            ev["reason"] = (f'Account is {where} but is not on the Bellhaven website. '
                            'Could be closed or sold; no other account found at this '
                            'address, so flag for a human.')
            props.append(proposal("STALE", f'Not on website: {a["name"]}',
                                  [update(a, {"status": C.NEEDS_REVIEW,
                                              "note": append_note(a, "Not listed on Bellhaven "
                                                                  "website. Confirm closed or sold.")})],
                                  ev, a))

    # extra Bellhaven parent records
    for d in bh_dupes:
        if retired(d):
            continue
        props.append(proposal(
            "DUPLICATE", f'Duplicate parent: {d["name"]} -> keep {canon["id"]}',
            [update(d, {"duplicate_of_account": canon["id"], "status": C.INACTIVE,
                        "note": append_note(d, f'Duplicate Bellhaven parent; use {canon["id"]}.')})],
            {"reason": f'Second Bellhaven parent record. Canonical parent is {canon["id"]} '
                       f'({canon["name"]}). Its children are moved by their own proposals.',
             "crm": view(d, by_id), "survivor": view(canon, by_id)}, d))

    # id -> name for every account a proposal touches, so the app can show names
    for p in props:
        ids = set()
        for op in p["actions"]:
            ids.add(op.get("id"))
            ids.update(v for v in op["set"].values() if isinstance(v, str))
        p["evidence"]["names"] = {i: by_id[i]["name"] for i in ids if i in by_id}

    summary = {"site_locations": len(site), "crm_accounts": len(accts),
               "bellhaven_parent": canon["id"], "clean_matches": len(clean_matches),
               "proposals": len(props)}
    return props, summary

"""Review app.  Run:  streamlit run app.py
Every proposal shows its evidence. Nothing is written to the CRM until Approve."""
import pandas as pd
import streamlit as st

import config as C
from crm import CRM
from executor import BlockedError, apply
from normalize import crm_care, money, norm_name, norm_street, norm_zip
from store import Store

st.set_page_config(page_title="Bellhaven Sync Review", page_icon="🏥", layout="wide")
store = Store()

# ------------------------------------------------------------------ look and feel
st.markdown("""
<style>
.badge {display:inline-block;padding:3px 10px;border-radius:999px;font-size:0.78rem;
        font-weight:600;color:white;margin-right:8px;vertical-align:middle}
.card-title {font-size:1.15rem;font-weight:600;vertical-align:middle}
.why {background:rgba(128,128,128,0.08);border-left:4px solid #888;padding:8px 12px;
      border-radius:4px;margin:6px 0 10px 0}
.chip {display:inline-block;padding:2px 8px;border-radius:6px;font-size:0.8rem;
       border:1px solid rgba(128,128,128,0.4);margin-right:6px}
</style>""", unsafe_allow_html=True)

# type: (label, colour, what it means in plain words, review order)
# One business event (Bellhaven now owns it), recorded two ways depending on the SOP.
TYPES = {
    "CHOW":      ("Ownership change", "#c0392b",
                  "Now Bellhaven, but the old account has billing history and open AR. "
                  "Old account stays untouched. A new account is created under Bellhaven "
                  "and the old one points to it.", 1),
    "CHOW_LINK": ("Ownership change", "#c0392b",
                  "Old billed account and a Bellhaven account both exist. Link old to new.", 1),
    "REPARENT":  ("Ownership change", "#c0392b",
                  "Now Bellhaven, under the wrong parent. No open AR, so move it directly.", 1),
    "DUPLICATE": ("Duplicate record", "#7d3c98",
                  "Second record for the same building. Mark it Inactive and point it "
                  "to the record we keep.", 4),
    "CREATE":    ("New location", "#239b56",
                  "On the Bellhaven website but not in the CRM.", 5),
    "FIX":       ("Update details", "#5d6d7e",
                  "Right account and right parent, but some details are out of date.", 6),
    "STALE":     ("Not on website", "#b7950b",
                  "In the CRM as Bellhaven but no longer on their website.", 7),
}
SOP_OUTCOME = {
    "CHOW": "🔒 SOP: billing history + open AR → new account, old one kept and linked",
    "CHOW_LINK": "🔒 SOP: billing history + open AR → old account kept, linked to existing one",
    "REPARENT": "SOP: no open AR → move the existing account",
}
LABELS = {
    "name": "Name", "billing_street": "Street", "billing_city": "City",
    "billing_state": "State", "billing_zip": "Zip", "parent_id": "Parent",
    "status": "Status", "note": "Note", "care_type": "Care type", "phone": "Phone",
    "duplicate_of_account": "Duplicate of", "chow_current_account": "CHOW: current account",
}
ID_FIELDS = {"parent_id", "duplicate_of_account", "chow_current_account"}


def badge(ptype):
    label, colour, _, _ = TYPES.get(ptype, (ptype, "#555", "", 9))
    return f'<span class="badge" style="background:{colour}">{label}</span>'


def show(v, names, field=None):
    if v in (None, ""):
        return "(blank)"
    if v == "$new":
        return "→ the new account created above"
    if field in ID_FIELDS and v in names:
        return f"{names[v]}  ({v})"
    return str(v)


def change_table(actions, names):
    rows = []
    for op in actions:
        if op["op"] == "create":
            who = "New account"
            for k, v in op["set"].items():
                rows.append({"Account": who, "Field": LABELS.get(k, k),
                             "Before": "", "After": show(v, names, k)})
        else:
            who = names.get(op["id"], op["id"])
            for k, v in op["set"].items():
                rows.append({"Account": who, "Field": LABELS.get(k, k),
                             "Before": show(op.get("expect", {}).get(k), names, k),
                             "After": show(v, names, k)})
    return pd.DataFrame(rows)


def evidence_table(ev):
    site, crm_ = ev.get("site") or {}, ev.get("crm") or {}
    if not crm_ and not site:
        return None
    checks = [
        ("Name", "name", norm_name), ("Street", "street", norm_street),
        ("City", "city", norm_name), ("State", "state", str.upper), ("Zip", "zip", norm_zip),
    ]
    rows = []
    for label, f, fn in checks:
        s, c = site.get(f), crm_.get(f)
        same = "" if not (site and crm_) else ("✅" if (s and c and fn(str(s)) == fn(str(c))) else "❌")
        rows.append({"": label, "Website": s or "", "CRM now": c or "", "Match": same})
    care_site = ", ".join(site.get("care_offerings") or []) if site else ""
    care_ok = "" if not (site and crm_) else ("✅" if crm_.get("care_offerings") in
                                              crm_care(site.get("care_offerings")) else "❌")
    rows.append({"": "Care", "Website": care_site, "CRM now": crm_.get("care_offerings") or "",
                 "Match": care_ok})
    if crm_:
        rows.append({"": "Parent", "Website": "Bellhaven" if site else "",
                     "CRM now": crm_.get("parent", ""), "Match": ""})
        rows.append({"": "Status", "Website": "", "CRM now": crm_.get("status", ""), "Match": ""})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.title("🏥 Bellhaven Sync")
    st.caption("Website → CRM ownership check")
    if st.button("🔄 Run pipeline now", use_container_width=True):
        from pipeline import run
        with st.spinner("Reading the website and the CRM..."):
            s = run(store)
        st.session_state["flash"] = (f"Pipeline done: {s['new']} new, "
                                     f"{s.get('came_back', 0)} came back after approval, "
                                     f"{s['still_pending']} still pending, "
                                     f"{s['already_decided']} rejected before (skipped), "
                                     f"{s['superseded']} retired.")
        st.rerun()
    last = store.last_run()
    if last:
        st.markdown("**Last run**")
        st.markdown(f"- {last['site_locations']} website locations\n"
                    f"- {last['crm_accounts']} CRM accounts\n"
                    f"- {last.get('clean_matches', 0)} already correct\n"
                    f"- {last['proposals']} proposals")
    st.divider()
    st.markdown("**How to review**")
    st.caption("1. Read *Why*.  2. Check *Website vs CRM*.  3. Check *What will change*.  "
               "4. Approve or Reject. Only Approve writes to the CRM.")
    st.divider()
    st.caption("Safety checks run before every write: billing SOP re-checked on live data, "
               "and the write is blocked if the account changed since it was proposed.")

# ------------------------------------------------------------------ header
pending, approved = store.list("pending"), store.list("approved")
rejected, superseded = store.list("rejected"), store.list("superseded")
decided = len(approved) + len(rejected)

st.title("Review queue")
if "flash" in st.session_state:
    msg = st.session_state.pop("flash")
    (st.error if msg.startswith("⛔") else st.success)(msg)

last_id = store.last_run_id()
new_retired = sum(1 for r in superseded if r.get("decided_at") == last_id)
m = st.columns(4)
m[0].metric("To review", len(pending))
m[1].metric("Approved", len(approved))
m[2].metric("Rejected", len(rejected))
m[3].metric("Retired", len(superseded),
            delta=f"+{new_retired} in latest run" if new_retired else None,
            delta_color="off")
if decided + len(pending):
    st.progress(decided / (decided + len(pending)),
                text=f"{decided} of {decided + len(pending)} reviewed")

views = ["To review", "Approved", "Rejected", "Retired"]
saved_view = st.session_state.get("view_saved", "To review")
view = st.radio("Show", views, horizontal=True, label_visibility="collapsed",
                index=views.index(saved_view) if saved_view in views else 0)
st.session_state["view_saved"] = view
rows = {"To review": pending, "Approved": approved, "Rejected": rejected,
        "Retired": superseded}.get(view, pending)

if view == "Retired":
    st.caption("Never approved, and dropped because a later run no longer needed them. "
               "Nothing here was written to the CRM.")
    rc1, rc2 = st.columns([1, 2])
    latest_only = rc1.toggle("Only from the latest run", value=True)
    search = rc2.text_input("Search by facility name", placeholder="e.g. Kettering")
    if latest_only:
        last_id = store.last_run_id()
        rows = [r for r in rows if r.get("decided_at") == last_id]
    if search:
        rows = [r for r in rows if search.lower() in r["title"].lower()]

def group(t):
    return TYPES.get(t, (t, "", "", 9))[0]


counts = pd.Series([group(r["type"]) for r in rows]).value_counts().to_dict() if rows else {}
# Fixed options (counts only in the display text) so the selection survives approvals.
GROUPS = []
for v in sorted(TYPES.values(), key=lambda v: v[3]):
    if v[0] not in GROUPS:
        GROUPS.append(v[0])
total = len(rows)
# Labels carry counts, so Streamlit sees a "new" widget after each decision and would
# reset it. Remember the choice ourselves and restore it.
type_options = ["All"] + GROUPS
saved = st.session_state.get("type_saved", "All")
choice = st.radio("Type", type_options, horizontal=True,
                  index=type_options.index(saved) if saved in type_options else 0,
                  format_func=lambda g: f"All ({total})" if g == "All"
                  else f"{g} ({counts.get(g, 0)})")
st.session_state["type_saved"] = choice
if choice != "All":
    rows = [r for r in rows if group(r["type"]) == choice]
rows = sorted(rows, key=lambda r: (TYPES.get(r["type"], ("", "", "", 9))[3], r["title"]))

if not rows:
    st.info("Nothing here. Use **Run pipeline now** in the sidebar to check for changes."
            if view == "To review" else "Nothing here yet.")

# ------------------------------------------------------------------ cards
for r in rows:
    ev, names = r["evidence"], r["evidence"].get("names", {})
    with st.container(border=True):
        head, chips = st.columns([3, 2])
        head.markdown(f'{badge(r["type"])}<span class="card-title">{r["title"]}</span>',
                      unsafe_allow_html=True)
        if r["type"] in SOP_OUTCOME:
            head.markdown(f"**{SOP_OUTCOME[r['type']]}**")
        crm_ = ev.get("crm") or {}
        if crm_:
            rev, ar = money(crm_.get("lifetime_revenue")), money(crm_.get("outstanding_ar"))
            flag = " · 🔒 SOP: keep old account" if rev > 0 and ar > 0 else ""
            chips.markdown(f'<div style="text-align:right"><span class="chip">Revenue '
                           f'${rev:,.0f}</span><span class="chip">Open AR ${ar:,.0f}</span>'
                           f'{flag}</div>', unsafe_allow_html=True)

        st.markdown(f'<div class="why"><b>Why:</b> {ev.get("reason", "")}</div>',
                    unsafe_allow_html=True)
        if ev.get("warning"):
            st.warning(ev["warning"])

        left, right = st.columns(2)
        with left:
            st.markdown("**Evidence: website vs CRM**")
            t = evidence_table(ev)
            if t is not None:
                st.dataframe(t, hide_index=True, use_container_width=True)
            if (ev.get("site") or {}).get("url"):
                st.markdown(f"[Open the website page ↗]({ev['site']['url']})")
        with right:
            st.markdown("**What will change**")
            st.dataframe(change_table(r["actions"], names), hide_index=True,
                         use_container_width=True)
            st.caption(TYPES.get(r["type"], ("", "", "", 0))[2])

        with st.expander("More details"):
            if ev.get("scores"):
                s = ev["scores"]
                st.markdown(f"Match type: **{s['level']}** · street similarity "
                            f"{s['street_sim']}% · name similarity {s['name_sim']}% · "
                            f"same zip: {'yes' if s['zip_eq'] else 'no'}")
            for label in ("survivor", "twin"):
                if ev.get(label):
                    v = ev[label]
                    st.markdown(f"**{'Record we keep' if label == 'survivor' else 'Other record at this address'}:** "
                                f"{v['name']} ({v['id']}), {v['street']}, {v['city']} · "
                                f"parent {v['parent']} · revenue ${v['lifetime_revenue']:,.0f} · "
                                f"AR ${v['outstanding_ar']:,.0f}")
            for pm in ev.get("possible_matches") or []:
                a = pm["account"]
                st.markdown(f"**Possible match, not linked:** {a['name']} ({a['id']}), "
                            f"{a['street']}, {a['city']} · parent {a['parent']} · "
                            f"name similarity {pm['scores']['name_sim']}%")
            st.caption(f"Proposal id {r['key']}")

        if r.get("last_error"):
            st.error(r["last_error"])

        if r["status"] == "pending":
            note_col, a_col, r_col = st.columns([6, 1, 1], vertical_alignment="bottom")
            note = note_col.text_input("Reviewer note (optional)", key=f"n_{r['key']}",
                                       placeholder="Why you approved or rejected")
            if a_col.button("✅ Approve", key=f"a_{r['key']}", type="primary",
                            use_container_width=True):
                try:
                    log = apply(r, CRM())
                    store.decide(r["key"], "approved", note, log)
                    st.session_state["flash"] = f"✅ Saved to CRM: {r['title']}"
                except BlockedError as e:
                    store.error(r["key"], f"Blocked, nothing was written. {e}")
                    st.session_state["flash"] = f"⛔ Blocked: {r['title']}"
                except Exception as e:
                    store.error(r["key"], f"Write failed: {e}")
                    st.session_state["flash"] = f"⛔ Write failed: {r['title']}"
                st.rerun()
            if r_col.button("Reject", key=f"r_{r['key']}", use_container_width=True):
                store.decide(r["key"], "rejected", note)
                st.session_state["flash"] = f"Rejected: {r['title']} (won't be proposed again)"
                st.rerun()
        else:
            when = (r.get("decided_at") or "")[:16].replace("T", " ")
            st.caption(f"{r['status'].title()} {when} UTC"
                       + (f" · Note: {r['reviewer_note']}" if r.get("reviewer_note") else ""))
            if r["status"] == "rejected" and st.button("↩ Undo reject", key=f"u_{r['key']}"):
                store.reopen(r["key"])
                st.session_state["flash"] = f"Moved back to review: {r['title']}"
                st.rerun()
            for entry in r.get("result") or []:
                if "created" in entry:
                    st.caption(f"Created account {entry['created']}")
                elif "updated" in entry:
                    st.caption(f"Updated {names.get(entry['updated'], entry['updated'])}: "
                               + ", ".join(LABELS.get(k, k) for k in entry["fields"]))

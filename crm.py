"""Thin CRM API client plus a standard view of an account record."""
import time
import requests
import config as C


def unwrap(payload):
    """API may return the object directly or wrapped, e.g. {"account": {...}}."""
    if isinstance(payload, dict):
        for k in ("account", "data", "item", "result"):
            if isinstance(payload.get(k), dict):
                return payload[k]
    return payload


def rows_of(payload):
    if isinstance(payload, list):
        return payload
    for k in ("items", "data", "results", "accounts"):
        if isinstance(payload.get(k), list):
            return payload[k]
    return []


def acct_id(raw):
    return raw.get(C.ID_FIELD, raw.get("id"))


def std(raw):
    """Map a raw account to standard keys, remembering the real field names."""
    flat = dict(raw)
    a = {"id": acct_id(raw), "raw": raw, "_keys": {}}
    for k, aliases in C.ALIASES.items():
        key = next((x for x in aliases if x in flat), aliases[0])
        a["_keys"][k] = key
        a[k] = flat.get(key)
    return a


class CRM:
    def __init__(self, token=None):
        token = token or C.TOKEN
        if not token:
            raise SystemExit("Set the BH_TOKEN environment variable first.")
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token}"

    def _req(self, method, path, **kw):
        for attempt in range(4):
            r = self.s.request(method, C.API_URL + path, timeout=30, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            if not r.ok:
                raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
            return r.json() if r.content else None
        raise RuntimeError(f"{method} {path} kept failing ({r.status_code})")

    def list_accounts(self):
        """Pull every account, whatever paging style the API uses."""
        out, seen, total = [], set(), None
        for page in range(1, 100):
            payload = self._req("GET", "/accounts",
                                params={"page": page, "page_size": C.PAGE_SIZE})
            if isinstance(payload, dict):
                total = payload.get("total", total)
            new = [a for a in rows_of(payload) if acct_id(a) not in seen]
            if not new:
                break
            seen.update(acct_id(a) for a in new)
            out.extend(new)
            if total is not None and len(out) >= total:
                break
        if total is not None and len(out) != total:
            raise RuntimeError(f"Pulled {len(out)} accounts but API says {total}.")
        return out

    def get(self, account_id):
        return unwrap(self._req("GET", f"/accounts/{account_id}"))

    def update(self, account_id, fields):
        return unwrap(self._req(C.UPDATE_METHOD, f"/accounts/{account_id}", json=fields))

    def create(self, fields):
        return unwrap(self._req("POST", "/accounts", json=fields))

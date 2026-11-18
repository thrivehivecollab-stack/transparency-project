"""Shared helpers for every collector.

Each collector builds a list of items and calls save_items(). Items always land as
'pending', and an existing item is never overwritten, so nothing already reviewed
can change behind the reviewer's back.
"""
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

USER_AGENT = "TransparencyProject/1.0 (public-interest research; github.com/thrivehivecollab-stack/transparency-project)"
SESSION = requests.Session()
SESSION.headers["User-Agent"] = USER_AGENT

_CFG = json.loads((Path(__file__).resolve().parent.parent / "config.json").read_text())
SUPABASE_URL = (os.environ.get("SUPABASE_URL") or _CFG.get("supabase_url") or "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
DRY_RUN = os.environ.get("DRY_RUN") == "1" or not (SUPABASE_URL and SUPABASE_KEY)

# Wayback Machine allows a limited number of anonymous saves, so cap them per run.
MAX_ARCHIVES_PER_RUN = int(os.environ.get("MAX_ARCHIVES_PER_RUN", "8"))
_archives_done = 0


def log(*args):
    print(datetime.now(timezone.utc).strftime("%H:%M:%S"), *args, flush=True)


def get_json(url, params=None, method="GET", body=None, retries=3):
    for attempt in range(retries):
        try:
            if method == "POST":
                r = SESSION.post(url, json=body, timeout=60)
            else:
                r = SESSION.get(url, params=params, timeout=60)
            if r.status_code == 429:
                time.sleep(20 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            log("request failed", url, e)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"giving up on {url}")


def content_hash(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def check_live(url):
    """True if the source page still answers. Used as an automatic check for the reviewer."""
    try:
        r = SESSION.get(url, timeout=30, allow_redirects=True, stream=True)
        r.close()
        return r.status_code < 400
    except requests.RequestException:
        return False


def archive(url):
    """Ask the Wayback Machine to save a copy. Returns the archive URL or None."""
    global _archives_done
    if _archives_done >= MAX_ARCHIVES_PER_RUN:
        return None
    _archives_done += 1
    try:
        r = SESSION.get(f"https://web.archive.org/save/{url}", timeout=90, allow_redirects=False)
        loc = r.headers.get("Content-Location") or r.headers.get("Location")
        time.sleep(6)
        if loc:
            return loc if loc.startswith("http") else f"https://web.archive.org{loc}"
        if r.status_code < 400:
            return f"https://web.archive.org/web/{datetime.now(timezone.utc):%Y%m%d%H%M%S}/{url}"
    except requests.RequestException as e:
        log("archive failed", url, e)
    return None


def make_item(tracker, source_id, statement, data, sources, priority=0, kind="record"):
    return {
        "tracker": tracker,
        "kind": kind,
        "source_id": str(source_id),
        "statement": statement,
        "data": data,
        "sources": sources,
        "content_hash": content_hash(data),
        "priority": priority,
    }


def existing_ids(tracker):
    """Source ids already stored for a tracker, so we skip archiving and checking them again."""
    if DRY_RUN:
        return set()
    ids, start = set(), 0
    while True:
        r = SESSION.get(
            f"{SUPABASE_URL}/rest/v1/items",
            params={"select": "source_id", "tracker": f"eq.{tracker}"},
            headers={**_auth(), "Range": f"{start}-{start + 999}"},
            timeout=60,
        )
        r.raise_for_status()
        rows = r.json()
        ids.update(row["source_id"] for row in rows)
        if len(rows) < 1000:
            return ids
        start += 1000


def _auth():
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}


def save_items(tracker, items):
    """Add automatic checks to new items and insert them as pending."""
    known = existing_ids(tracker)
    new = [i for i in items if i["source_id"] not in known]
    log(f"{tracker}: {len(items)} found, {len(new)} new")
    for item in new:
        main = item["sources"][0]["url"] if item["sources"] else None
        item["checks"] = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "source_live": check_live(main) if main else False,
        }
        if main and not item["sources"][0].get("archive_url"):
            item["sources"][0]["archive_url"] = archive(main)
        item["checks"]["archived"] = bool(item["sources"][0].get("archive_url")) if main else False
    if DRY_RUN:
        log("DRY RUN, not saving. Sample:")
        print(json.dumps(new[:3], indent=2, default=str))
        return len(new)
    for chunk in range(0, len(new), 200):
        r = SESSION.post(
            f"{SUPABASE_URL}/rest/v1/items",
            params={"on_conflict": "tracker,source_id"},
            headers={**_auth(), "Content-Type": "application/json",
                     "Prefer": "resolution=ignore-duplicates,return=minimal"},
            data=json.dumps(new[chunk:chunk + 200], default=str),
            timeout=120,
        )
        if r.status_code >= 300:
            log("save failed", r.status_code, r.text[:500])
            sys.exit(1)
    return len(new)


def money(x):
    try:
        return "${:,.0f}".format(float(x))
    except (TypeError, ValueError):
        return "an unreported amount"

"""Fill in archive copies for pending items that didn't get one during collection,
and re-check that each source is still live. Runs after the collectors."""
from datetime import datetime, timezone

from common import DRY_RUN, SESSION, SUPABASE_URL, _auth, archive, check_live, log

if __name__ == "__main__":
    if DRY_RUN:
        log("dry run, skipping backlog")
        raise SystemExit
    r = SESSION.get(f"{SUPABASE_URL}/rest/v1/items",
                    params={"select": "id,sources,checks", "status": "in.(pending,needs_more)",
                            "order": "priority.desc", "limit": "40"},
                    headers=_auth(), timeout=60)
    r.raise_for_status()
    fixed = 0
    for row in r.json():
        sources = row["sources"] or []
        if not sources or sources[0].get("archive_url"):
            continue
        url = archive(sources[0]["url"])
        if not url:
            continue
        sources[0]["archive_url"] = url
        checks = {**(row["checks"] or {}), "archived": True, "source_live": check_live(sources[0]["url"]),
                  "checked_at": datetime.now(timezone.utc).isoformat()}
        SESSION.patch(f"{SUPABASE_URL}/rest/v1/items", params={"id": f"eq.{row['id']}"},
                      headers={**_auth(), "Content-Type": "application/json", "Prefer": "return=minimal"},
                      json={"sources": sources, "checks": checks}, timeout=60).raise_for_status()
        fixed += 1
    log(f"archived {fixed} backlog items")

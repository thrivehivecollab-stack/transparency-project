"""Federal money spent on persuasion: influencer, PR, social media and ad campaigns.

Source: USAspending.gov API v2 (free, no key). Keyword matches are broad on purpose;
the reviewer rejects anything that isn't actually about public messaging.
"""
import json
import os
from datetime import date, timedelta
from pathlib import Path

from common import get_json, log, make_item, money, save_items

TRACKER = "usaspending_persuasion"
API = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
WATCHLIST = json.loads((Path(__file__).parent / "watchlist.json").read_text())
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "90"))
FIELDS = ["Award ID", "Recipient Name", "Award Amount", "Awarding Agency", "Description", "Start Date"]
GROUPS = {"contract": ["A", "B", "C", "D"], "grant": ["02", "03", "04", "05"]}


def search(keyword, codes):
    end = date.today()
    body = {
        "filters": {
            "keywords": [keyword],
            "award_type_codes": codes,
            "time_period": [{"start_date": str(end - timedelta(days=LOOKBACK_DAYS)), "end_date": str(end)}],
        },
        "fields": FIELDS,
        "sort": "Award Amount",
        "order": "desc",
        "limit": 50,
        "page": 1,
    }
    return get_json(API, method="POST", body=body).get("results", [])


def to_item(row, keyword, group):
    gid = row.get("generated_internal_id") or row.get("internal_id")
    agency = row.get("Awarding Agency") or "A federal agency"
    recipient = row.get("Recipient Name") or "an unnamed recipient"
    desc = (row.get("Description") or "").strip()
    statement = (f"{agency} awarded a {group} worth {money(row.get('Award Amount'))} to {recipient}"
                 + (f" (start date {row.get('Start Date')})" if row.get("Start Date") else "")
                 + (f", described as \"{desc[:200]}\"" if desc else "") + ", according to USAspending.gov.")
    data = {k: row.get(k) for k in FIELDS}
    data.update({"generated_internal_id": gid, "matched_keyword": keyword, "award_group": group})
    amount = float(row.get("Award Amount") or 0)
    priority = 8 if amount >= 1_000_000 else 4 if amount >= 100_000 else 1
    return make_item(TRACKER, gid, statement, data,
                     [{"label": "Award page (USAspending.gov)", "url": f"https://www.usaspending.gov/award/{gid}"}],
                     priority=priority)


if __name__ == "__main__":
    items, seen = [], set()
    for kw in WATCHLIST["usaspending_keywords"]:
        for group, codes in GROUPS.items():
            for row in search(kw, codes):
                gid = row.get("generated_internal_id") or row.get("internal_id")
                if gid and gid not in seen:
                    seen.add(gid)
                    items.append(to_item(row, kw, group))
    log(f"{len(items)} persuasion-related awards")
    save_items(TRACKER, items)

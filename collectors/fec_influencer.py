"""Campaign money paid to influencer and creator marketing.

Source: FEC Schedule B (disbursements) API. Committees must report who they paid, so
payments to influencer agencies show up here even when the creators don't disclose.
Uses the free DEMO_KEY unless FEC_API_KEY is set (DEMO_KEY allows about 30 calls an hour).
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

from common import get_json, log, make_item, money, save_items

TRACKER = "fec_influencer"
API = "https://api.open.fec.gov/v1/schedules/schedule_b/"
KEY = os.environ.get("FEC_API_KEY") or "DEMO_KEY"
WATCHLIST = json.loads((Path(__file__).parent / "watchlist.json").read_text())


def cycle():
    y = datetime.now(timezone.utc).year
    return y if y % 2 == 0 else y + 1


def fetch(params):
    base = {"api_key": KEY, "two_year_transaction_period": cycle(), "per_page": 100,
            "sort": "-disbursement_date"}
    return get_json(API, params={**base, **params}).get("results", [])


def to_item(row, matched):
    committee = (row.get("committee") or {}).get("name") or row.get("committee_id")
    recipient = row.get("recipient_name") or "an unnamed recipient"
    desc = (row.get("disbursement_description") or "").strip()
    date = (row.get("disbursement_date") or "")[:10]
    statement = (f"{committee} reported paying {recipient} {money(row.get('disbursement_amount'))} "
                 f"on {date}" + (f" for \"{desc}\"" if desc else "") + " (FEC filing).")
    data = {k: row.get(k) for k in ("sub_id", "committee_id", "recipient_name", "disbursement_amount",
                                    "disbursement_date", "disbursement_description", "image_number",
                                    "file_number", "pdf_url")}
    data["committee_name"] = committee
    data["matched_on"] = matched
    sources = []
    if row.get("pdf_url"):
        sources.append({"label": "Original FEC filing page", "url": row["pdf_url"]})
    sources.append({"label": "FEC disbursement search",
                    "url": f"https://www.fec.gov/data/disbursements/?committee_id={row.get('committee_id')}"
                           f"&recipient_name={quote_plus(recipient)}"})
    sources.append({"label": "Committee profile (FEC)",
                    "url": f"https://www.fec.gov/data/committee/{row.get('committee_id')}/"})
    # Payments to a watchlisted agency go to the top of the review queue
    priority = 10 if matched.startswith("watchlist") else 5
    return make_item(TRACKER, row.get("sub_id"), statement, data, sources, priority=priority)


if __name__ == "__main__":
    items, seen = [], set()
    for term in WATCHLIST["fec_description_terms"]:
        for row in fetch({"disbursement_description": term}):
            if row.get("sub_id") not in seen:
                seen.add(row.get("sub_id"))
                items.append(to_item(row, f"description: {term}"))
    for name in WATCHLIST["influencer_agencies"]:
        for row in fetch({"recipient_name": name}):
            if row.get("sub_id") not in seen:
                seen.add(row.get("sub_id"))
                items.append(to_item(row, f"watchlist: {name}"))
    log(f"{len(items)} influencer-related disbursements")
    save_items(TRACKER, items)

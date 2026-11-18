"""House members' stock trade disclosures (Periodic Transaction Reports).

Source: the House Clerk's public financial disclosure index, a yearly zip with an XML list
of every filing. Filing type 'P' is a Periodic Transaction Report (a stock trade report).
Phase 1 records each report with a link to the original PDF; reading the individual
trades out of the PDFs comes in phase 2.
"""
import io
import os
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone

from common import SESSION, log, make_item, save_items

TRACKER = "house_trades"
INDEX = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
PDF = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc}.pdf"
# Only queue recent filings so the first run doesn't flood the review queue.
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "30"))


def text(el, tag):
    found = el.find(tag)
    return (found.text or "").strip() if found is not None and found.text else ""


def collect(year):
    r = SESSION.get(INDEX.format(year=year), timeout=120)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    xml_name = next(n for n in z.namelist() if n.lower().endswith(".xml"))
    root = ET.fromstring(z.read(xml_name))
    items = []
    cutoff = datetime.now() - timedelta(days=LOOKBACK_DAYS)
    for m in root.iter("Member"):
        if text(m, "FilingType") != "P":
            continue
        doc = text(m, "DocID")
        name = " ".join(p for p in [text(m, "Prefix"), text(m, "First"), text(m, "Last"), text(m, "Suffix")] if p)
        district = text(m, "StateDst")
        filed = text(m, "FilingDate")
        try:
            if datetime.strptime(filed, "%m/%d/%Y") < cutoff:
                continue
        except ValueError:
            pass
        data = {"name": name, "district": district, "filing_date": filed, "doc_id": doc, "year": year}
        url = PDF.format(year=year, doc=doc)
        statement = (f"{name} ({district}) filed a stock trade disclosure (Periodic Transaction Report) "
                     f"with the House Clerk on {filed}.")
        items.append(make_item(TRACKER, doc, statement, data,
                               [{"label": "Original filing (House Clerk PDF)", "url": url}]))
    return items


if __name__ == "__main__":
    year = datetime.now(timezone.utc).year
    items = collect(year)
    log(f"{len(items)} trade reports in {year} index")
    save_items(TRACKER, items)

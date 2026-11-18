"""Builds the public site as plain static pages from approved items only.

Static pages served from a CDN can take very heavy traffic for free, and because the
build reads with the public (anon) key, database rules guarantee only approved items
can ever be included.
"""
import csv
import html
import json
import os
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

from trackers import TRACKERS, tracker

HERE = Path(__file__).parent
OUT = HERE / "dist"
CFG = json.loads((HERE.parent / "config.json").read_text())
SITE_NAME = os.environ.get("SITE_NAME") or CFG.get("site_name") or "Transparency Project"
SUPABASE_URL = (os.environ.get("SUPABASE_URL") or CFG.get("supabase_url") or "").rstrip("/")
ANON_KEY = os.environ.get("SUPABASE_ANON_KEY") or CFG.get("supabase_anon_key") or ""
SAMPLE = os.environ.get("SAMPLE") == "1"
SITE_URL = (os.environ.get("SITE_URL") or CFG.get("site_url") or "").rstrip("/")

e = html.escape


def fetch(table, params):
    if SAMPLE:
        return json.loads((HERE / "sample" / f"{table}.json").read_text())
    if not (SUPABASE_URL and ANON_KEY):
        print(f"database not configured yet, building {table} as empty")
        return []
    rows, start = [], 0
    while True:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}", params=params, timeout=60,
                         headers={"apikey": ANON_KEY, "Authorization": f"Bearer {ANON_KEY}",
                                  "Range": f"{start}-{start + 999}"})
        r.raise_for_status()
        batch = r.json()
        rows += batch
        if len(batch) < 1000:
            return rows
        start += 1000


def fmt_date(s):
    if not s:
        return ""
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%b %d, %Y")
    except ValueError:
        return s


def page(title, body, depth=0):
    up = "../" * depth
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)} | {e(SITE_NAME)}</title>
<meta name="description" content="Public records on money, influence and government, each linked to its original source.">
<link rel="stylesheet" href="{up}style.css">
<link rel="alternate" type="application/rss+xml" title="{e(SITE_NAME)}" href="{up}feed.xml">
</head><body>
<header><a class="brand" href="{up}index.html">{e(SITE_NAME)}</a>
<nav><a href="{up}index.html">Latest</a><a href="{up}polls.html">Polls</a><a href="{up}corrections.html">Corrections</a><a href="{up}methodology.html">How this works</a><a href="{up}data/items.json">Data</a></nav></header>
<main>{body}</main>
<footer><p>Every entry links to the official record it came from and an archived copy. Every entry was checked by a person before publishing.
Spot an error? Corrections are logged publicly.</p><p>Built {datetime.now(timezone.utc):%b %d, %Y %H:%M} UTC.</p></footer>
</body></html>"""


def source_list(sources):
    out = []
    for s in sources or []:
        arch = f' · <a href="{e(s["archive_url"])}" rel="nofollow">archived copy</a>' if s.get("archive_url") else ""
        out.append(f'<li><a href="{e(s["url"])}" rel="nofollow">{e(s.get("label") or s["url"])}</a>{arch}</li>')
    return "<ul class='sources'>" + "".join(out) + "</ul>"


def card(item, depth=0):
    up = "../" * depth
    t = tracker(item["tracker"])
    return f"""<article class="card">
<div class="meta"><a href="{up}t/{e(item['tracker'])}.html">{e(t['name'])}</a> · verified {e(fmt_date(item.get('published_at') or item.get('reviewed_at')))}</div>
<p class="statement"><a href="{up}i/{item['id']}.html">{e(item['statement'])}</a></p>
{source_list(item.get('sources'))}
</article>"""


def build():
    items = fetch("items", {"select": "id,tracker,kind,statement,data,sources,published_at,reviewed_at",
                            "status": "eq.approved", "order": "published_at.desc.nullslast"})
    corrections = fetch("corrections", {"select": "*", "order": "changed_at.desc"})
    if OUT.exists():
        shutil.rmtree(OUT)
    for d in ("t", "i", "data"):
        (OUT / d).mkdir(parents=True, exist_ok=True)
    shutil.copy(HERE / "static" / "style.css", OUT / "style.css")

    by_tracker = defaultdict(list)
    for it in items:
        by_tracker[it["tracker"]].append(it)
    corr_by_item = defaultdict(list)
    for c in corrections:
        corr_by_item[c["item_id"]].append(c)

    # Home
    chips = "".join(f'<a class="chip" href="t/{k}.html">{e(tracker(k)["name"])} ({len(v)})</a>'
                    for k, v in sorted(by_tracker.items()))
    latest = "".join(card(it) for it in items[:60]) or "<p>No verified entries yet. Check back soon.</p>"
    home = (f"<section class='intro'><h1>Public records on money, influence and government</h1>"
            f"<p>Who pays whom, what officials trade, where tax dollars go. Every entry is pulled from an official "
            f"record, archived, and checked by a person before it appears here.</p>"
            f"<p><a class='button' href='polls.html'>Vote on what we track next</a></p></section>"
            f"<div class='chips'>{chips}</div><h2>Latest verified entries</h2>{latest}")
    (OUT / "index.html").write_text(page("Latest", home))

    # Tracker pages
    for key, rows in by_tracker.items():
        t = tracker(key)
        body = (f"<h1>{e(t['name'])}</h1><p>{e(t['blurb'])}</p><p class='method'><strong>Source:</strong> {e(t['source'])}. "
                f"{e(t['method'])}</p>" + "".join(card(r, 1) for r in rows))
        (OUT / "t" / f"{key}.html").write_text(page(t["name"], body, 1))

    # Item pages
    for it in items:
        hist = corr_by_item.get(it["id"], [])
        corr = ""
        if hist:
            corr = "<h2>Corrections</h2><ul>" + "".join(
                f"<li>{e(fmt_date(c['changed_at']))}: {e(c['reason'])}<br><small>Previously: {e(c.get('before') or '')}</small></li>"
                for c in hist) + "</ul>"
        details = "".join(f"<tr><th>{e(str(k).replace('_', ' '))}</th><td>{e(str(v))}</td></tr>"
                          for k, v in (it.get("data") or {}).items() if v not in (None, ""))
        body = (f"<p class='meta'><a href='../t/{e(it['tracker'])}.html'>{e(tracker(it['tracker'])['name'])}</a></p>"
                f"<h1 class='statement'>{e(it['statement'])}</h1>"
                f"<p class='meta'>Verified by a reviewer on {e(fmt_date(it.get('published_at') or it.get('reviewed_at')))}</p>"
                f"<h2>Sources</h2>{source_list(it.get('sources'))}{corr}"
                f"<h2>Record details</h2><table class='details'>{details}</table>")
        (OUT / "i" / f"{it['id']}.html").write_text(page(it["statement"][:70], body, 1))

    # Corrections log
    rows = "".join(f"<li><a href='i/{c['item_id']}.html'>Entry {c['item_id']}</a>, {e(fmt_date(c['changed_at']))}: "
                   f"{e(c['reason'])}</li>" for c in corrections) or "<li>No corrections so far.</li>"
    (OUT / "corrections.html").write_text(page("Corrections", f"<h1>Corrections log</h1><p>Every change made after "
                                                              f"publishing is listed here with the reason.</p><ul>{rows}</ul>"))

    # Methodology
    meth = "".join(f"<h2>{e(t['name'])}</h2><p><strong>Source:</strong> {e(t['source'])}</p><p>{e(t['method'])}</p>"
                   for t in TRACKERS.values())
    (OUT / "methodology.html").write_text(page("How this works", (HERE / "static" / "methodology.html").read_text() + meth))

    # Polls and review pages get the public connection settings
    cfg = f"window.TP={{url:{json.dumps(SUPABASE_URL)},key:{json.dumps(ANON_KEY)}}};"
    (OUT / "config.js").write_text(cfg)
    (OUT / "polls.html").write_text(page("Polls", (HERE / "static" / "polls.html").read_text()))
    shutil.copy(HERE / "static" / "admin.html", OUT / "admin.html")

    # Open data
    (OUT / "data" / "items.json").write_text(json.dumps(items, indent=1, default=str))
    with open(OUT / "data" / "items.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "tracker", "statement", "verified", "source_url", "archive_url"])
        for it in items:
            s = (it.get("sources") or [{}])[0]
            w.writerow([it["id"], it["tracker"], it["statement"], it.get("published_at"), s.get("url"), s.get("archive_url")])

    # RSS feed for alerts
    entries = "".join(
        f"<item><title>{e(it['statement'][:120])}</title><link>{SITE_URL}/i/{it['id']}.html</link>"
        f"<guid isPermaLink='false'>tp-{it['id']}</guid><description>{e(it['statement'])}</description></item>"
        for it in items[:50])
    (OUT / "feed.xml").write_text(f"<?xml version='1.0' encoding='UTF-8'?><rss version='2.0'><channel>"
                                  f"<title>{e(SITE_NAME)}</title><link>{SITE_URL}/</link>"
                                  f"<description>Verified public records</description>{entries}</channel></rss>")
    (OUT / ".nojekyll").write_text("")
    print(f"built {len(items)} items, {len(corrections)} corrections into {OUT}")


if __name__ == "__main__":
    build()

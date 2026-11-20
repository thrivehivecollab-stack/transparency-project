"""Check a handle or a name against public money and influence records.

Usage:
  python check.py --query "@somehandle"            # handle checks + name checks on the handle text
  python check.py --query "Jane Example"           # name checks only
  python check.py --query "@somehandle" --name "Jane Example LLC"   # handle plus a known real/business name

Rules this tool follows:
- Public records only. No addresses, family, employers or other personal details are collected.
- Anything found by name is a POSSIBLE match until a person confirms it is the same individual.
- "Nothing found" never means "not paid": private deals with no filing and no tracked link leave no public trace.
"""
import argparse
import csv
import io
import json
import re
import sys
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urljoin, urlparse

import requests

ROOT = Path(__file__).resolve().parent.parent
WATCH = json.loads((ROOT / "collectors" / "watchlist.json").read_text())
S = requests.Session()
S.headers["User-Agent"] = "TransparencyProject-Check/1.0 (public-interest research)"
TIMEOUT = 40
ERRORS = []  # sources that could not be checked, so "nothing found" is never reported for a failed source

# Only codes that identify WHO gets credit or paid count as a signal. Ordinary utm_* share tags are
# added by news sites and social tools to everything they post, so they are never treated as a paid signal.
TRACKING_PARAMS = {"ref", "aff", "aff_id", "affiliate", "affid", "creator", "creator_id", "creator_code",
                   "irclickid", "subid", "sub_id", "clickid", "partner", "promo", "code", "influencer", "kol"}
AFFILIATE_HOSTS = ["amzn.to", "skimresources", "shareasale", "sjv.io", "pxf.io", "rstyle.me", "liketk.it",
                   "geni.us", "go.magik.ly", "howl.me", "awin1.com", "anrdoezrs", "dpbolvw", "jdoqocy", "tkqlhce",
                   "impact.com", "partnerize", "linksynergy", "mavely", "shopmy"]
FUNDING_HOSTS = {"buymeacoffee.com": "Buy Me a Coffee", "patreon.com": "Patreon", "ko-fi.com": "Ko-fi",
                 "substack.com": "Substack", "locals.com": "Locals", "givesendgo.com": "GiveSendGo",
                 "gofundme.com": "GoFundMe", "cash.app": "Cash App", "venmo.com": "Venmo", "paypal.me": "PayPal",
                 "subscribestar.com": "SubscribeStar", "rumble.com": "Rumble", "streamlabs.com": "Streamlabs",
                 "x.com/i/subscriptions": "X subscriptions"}
SHORTENERS = ["bit.ly", "t.co", "tinyurl.com", "ow.ly", "buff.ly", "rebrand.ly", "shorturl.at", "lnkd.in"]
SPONSOR_WORDS = re.compile(r"#ad\b|#sponsored|paid partnership|sponsored by|in partnership with|use (my )?code|#partner\b",
                           re.I)


# ---------------------------------------------------------------- helpers
def get(url, **kw):
    try:
        r = S.get(url, timeout=TIMEOUT, **kw)
        if r.status_code in (401, 403, 429) or r.status_code >= 500:
            ERRORS.append(f"{urlparse(url).netloc}{urlparse(url).path} answered {r.status_code}")
        return r
    except requests.RequestException as e:
        ERRORS.append(f"{urlparse(url).netloc}{urlparse(url).path} unreachable ({type(e).__name__})")
        return None


def tokens(name):
    return [t for t in re.findall(r"[a-z0-9]+", name.lower()) if len(t) > 1]


def name_matches(text, toks):
    low = text.lower()
    return all(re.search(rf"\b{re.escape(t)}\b", low) for t in toks)


def trace(url, max_hops=8):
    """Follow redirects one hop at a time and record every stop."""
    hops = [url]
    cur = url
    for _ in range(max_hops):
        try:
            r = S.get(cur, allow_redirects=False, timeout=20, stream=True)
            r.close()
        except (requests.RequestException, ValueError):
            break
        if r.is_redirect and r.headers.get("Location"):
            cur = urljoin(cur, r.headers["Location"])
            hops.append(cur)
        else:
            break
    return hops


def flag_link(hops):
    """Return reasons a link chain looks like paid or tracked promotion."""
    reasons = []
    for h in hops:
        try:
            u = urlparse(h)
            u.netloc.lower()
        except ValueError:
            continue
        host = u.netloc.lower()
        if any(d in host for d in WATCH.get("redirect_domains", [])):
            reasons.append(f"routes through watchlisted campaign domain {host}")
        if any(a in host for a in AFFILIATE_HOSTS):
            reasons.append(f"affiliate network ({host})")
        for fh, label in FUNDING_HOSTS.items():
            if fh in (host + u.path).lower():
                reasons.append(f"self-declared funding channel: {label}")
        params = {k.lower(): v for k, v in parse_qsl(u.query)}
        hit = sorted(set(params) & TRACKING_PARAMS)
        if "tag" in params and "amazon." in host:
            hit.append("tag")
        if hit:
            reasons.append("creator/affiliate codes: " + ", ".join(f"{k}={params[k][:40]}" for k in hit))
    return sorted(set(reasons))


def extract_links(html, skip_hosts=()):
    links = set(re.findall(r'https?://[^\s"\'<>\\]+', html))
    out = []
    for l in links:
        l = l.rstrip(").,;")
        try:
            host = urlparse(l).netloc.lower()
        except ValueError:
            continue
        if not host or any(s in host for s in skip_hosts):
            continue
        if re.search(r"\.(png|jpe?g|gif|svg|webp|css|js|woff2?|ico)(\?|$)", l):
            continue
        out.append(l)
    return sorted(out)


# ---------------------------------------------------------------- handle checks
def check_link_in_bio(handle):
    found = []
    pages = {
        "Linktree": (f"https://linktr.ee/{handle}", ["linktr.ee", "linktree"]),
        "Beacons": (f"https://beacons.ai/{handle}", ["beacons.ai", "beacons.page"]),
        "Stan Store": (f"https://stan.store/{handle}", ["stan.store", "stanwith.me"]),
    }
    for label, (url, skip) in pages.items():
        r = get(url)
        if not r or r.status_code != 200 or handle.lower() not in r.text.lower():
            continue
        links = extract_links(r.text, skip + ["googleapis", "gstatic", "cloudfront", "sentry", "schema.org",
                                              "w3.org", "facebook.com/tr", "google-analytics", "googletagmanager"])[:30]
        checked = []
        for l in links:
            hops = trace(l)
            checked.append({"link": l, "final": hops[-1], "hops": hops, "flags": flag_link(hops)})
        found.append({"page": label, "url": url, "links": checked})
    return found


def check_youtube(handle):
    r = get(f"https://www.youtube.com/@{handle}")
    if r is None:
        return None
    if r.status_code == 404:
        return {"channel": None, "channel_links": [], "videos": []}
    if r.status_code != 200:
        return None
    m = re.search(r'"(?:channelId|externalId)":"(UC[\w-]{22})"', r.text)
    if not m:
        return {"channel": None, "channel_links": [], "videos": []}
    cid = m.group(1)
    about_links = []
    for l in extract_links(r.text.replace("\\u0026", "&"), ["youtube.com", "youtu.be", "ytimg", "ggpht",
                                                           "googlevideo", "google.com", "gstatic", "doubleclick"])[:40]:
        f = flag_link([l])
        if f:
            about_links.append({"link": l, "flags": f})
    feed = get(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}")
    if not feed or feed.status_code != 200:
        return {"channel": f"https://www.youtube.com/channel/{cid}", "channel_links": about_links, "videos": []}
    videos = []
    for entry in re.findall(r"<entry>(.*?)</entry>", feed.text, re.S):
        title = re.search(r"<title>(.*?)</title>", entry, re.S)
        link = re.search(r'<link rel="alternate" href="([^"]+)"', entry)
        desc = re.search(r"<media:description>(.*?)</media:description>", entry, re.S)
        published = re.search(r"<published>(.*?)</published>", entry)
        d = (desc.group(1) if desc else "").replace("&amp;", "&")
        links = extract_links(d, ["youtube.com", "youtu.be"])[:15]
        flagged = []
        for l in links:
            hops = trace(l)
            f = flag_link(hops)
            if f:
                flagged.append({"link": l, "final": hops[-1], "flags": f})
        sponsor_text = bool(SPONSOR_WORDS.search(d))
        if flagged or sponsor_text:
            videos.append({"title": title.group(1) if title else "", "url": link.group(1) if link else "",
                           "published": published.group(1)[:10] if published else "",
                           "discloses_sponsor": sponsor_text, "flagged_links": flagged})
    return {"channel": f"https://www.youtube.com/channel/{cid}", "channel_links": about_links, "videos": videos}


def check_threadreader(handle, max_threads=25):
    """Thread Reader App keeps public, readable copies of X threads people asked it to unroll."""
    import time
    r = get(f"https://threadreaderapp.com/user/{handle}")
    if not r or r.status_code != 200:
        return None if (r is None or r.status_code != 404) else []
    ids = list(dict.fromkeys(re.findall(r'/thread/(\d{8,25})', r.text)))[:max_threads]
    threads = []
    for tid in ids:
        time.sleep(2)  # be polite
        t = get(f"https://threadreaderapp.com/thread/{tid}.html")
        if not t or t.status_code != 200:
            continue
        body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", t.text, flags=re.S)
        text = re.sub(r"<[^>]+>", " ", body)
        title = re.search(r"<title>(.*?)</title>", t.text, re.S)
        date_m = re.search(r'datetime="([0-9-]{10})', t.text)
        links = [l for l in extract_links(body, ["threadreaderapp", "twitter.com", "x.com", "twimg", "googletag",
                                                  "google-analytics", "gstatic", "cloudflare", "fonts.", "schema.org",
                                                  "w3.org", "facebook.com/tr", "doubleclick"])][:20]
        flagged = []
        for l in links:
            hops = trace(l)
            f = flag_link(hops)
            if f:
                flagged.append({"link": l, "final": hops[-1], "flags": f})
        threads.append({"url": f"https://threadreaderapp.com/thread/{tid}.html",
                        "original": f"https://x.com/{handle}/status/{tid}",
                        "title": (title.group(1).strip()[:120] if title else ""),
                        "date": date_m.group(1) if date_m else "",
                        "links_found": len(links), "flagged_links": flagged,
                        "discloses_sponsor": bool(SPONSOR_WORDS.search(text))})
    return threads


def check_archived_posts(handle, limit=150):
    """Read archived copies of X posts from the Wayback Machine and trace the links inside them.
    This is the Midas tracked-link method applied to posts we can read without logging in to X."""
    import html as htmlmod
    import time
    caps = []
    for dom in ("x.com", "twitter.com"):
        r = get("https://web.archive.org/cdx/search/cdx",
                params={"url": f"{dom}/{handle}/status/*", "output": "json", "fl": "original,timestamp",
                        "filter": "statuscode:200", "collapse": "urlkey", "limit": "3000"})
        if r is None or r.status_code != 200:
            continue
        try:
            caps += r.json()[1:]
        except ValueError:
            pass
    seen, uniq = set(), []
    for orig, ts in sorted(caps, key=lambda x: x[1], reverse=True):
        m = re.search(r"/status/(\d+)", orig)
        if m and m.group(1) not in seen and handle.lower() in orig.lower():
            seen.add(m.group(1))
            uniq.append((m.group(1), orig, ts))
    posts, readable = [], 0
    link_cache = {}
    for pid, orig, ts in uniq[:limit]:
        time.sleep(1.2)
        a = get(f"https://web.archive.org/web/{ts}id_/{orig}")
        if not a or a.status_code != 200:
            continue
        page = a.text
        texts = re.findall(r'<meta[^>]+(?:property|name)="(?:og:description|twitter:description)"[^>]+content="([^"]*)"', page)
        texts += re.findall(r'content="([^"]*)"[^>]+(?:property|name)="(?:og:description|twitter:description)"', page)
        body = re.findall(r'data-testid="tweetText"[^>]*>(.*?)</div>', page, re.S)
        text = htmlmod.unescape(" ".join(texts + [re.sub(r"<[^>]+>", " ", b) for b in body])).strip()
        if not text:
            continue
        readable += 1
        links = sorted(set(re.findall(r"https?://t\.co/\w+", page)) |
                       set(l for l in extract_links(text, ["x.com", "twitter.com", "twimg"])))[:8]
        flagged = []
        for l in links:
            if l not in link_cache:
                hops = trace(l)
                link_cache[l] = (hops[-1], flag_link(hops))
            final, f = link_cache[l]
            if f:
                flagged.append({"link": l, "final": final, "flags": f})
        date = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"
        is_reply = text.lstrip().startswith("@")
        posts.append({"id": pid, "archived": f"https://web.archive.org/web/{ts}/{orig}", "captured": date, "reply": is_reply,
                      "text": text[:280], "links": [link_cache.get(l, (l,))[0] for l in links],
                      "discloses_sponsor": bool(SPONSOR_WORDS.search(text)), "flagged_links": flagged})
    # Destinations promoted again and again are a coordination signal worth a human look
    dest_count = {}
    for p in posts:
        for d in set(p["links"]):
            host = urlparse(d).netloc.lower()
            if host and not any(x in host for x in ("x.com", "twitter.com", "twimg")):
                dest_count[host] = dest_count.get(host, 0) + 1
    top = sorted(dest_count.items(), key=lambda x: -x[1])[:10]
    return {"archived_posts": len(uniq), "attempted": min(len(uniq), limit), "readable": readable,
            "posts": posts, "top_destinations": top}


def check_wayback(handle):
    out = []
    for label, pattern in [("X/Twitter posts", f"x.com/{handle}/status/*"),
                           ("Twitter posts (old domain)", f"twitter.com/{handle}/status/*"),
                           ("Instagram", f"instagram.com/{handle}/*"),
                           ("TikTok", f"tiktok.com/@{handle}/*")]:
        r = get("https://web.archive.org/cdx/search/cdx",
                params={"url": pattern, "output": "json", "fl": "original,timestamp", "collapse": "urlkey",
                        "limit": "2000"})
        if not r or r.status_code != 200:
            continue
        try:
            rows = r.json()[1:]
        except ValueError:
            continue
        if rows:
            out.append({"where": label, "archived_pages": len(rows),
                        "browse": f"https://web.archive.org/web/*/{pattern}",
                        "latest": sorted(rows, key=lambda x: x[1])[-5:]})
    return out


# ---------------------------------------------------------------- name checks
FARA_FILES = {
    "short_form": "https://efile.fara.gov/bulk/zip/FARA_All_ShortForms.csv.zip",
    "registrants": "https://efile.fara.gov/bulk/zip/FARA_All_Registrants.csv.zip",
    "foreign_principals": "https://efile.fara.gov/bulk/zip/FARA_All_ForeignPrincipals.csv.zip",
    "docs": "https://efile.fara.gov/bulk/zip/FARA_All_RegistrantDocs.csv.zip",
}
_fara_cache = {}


def fara_rows(kind):
    if kind in _fara_cache:
        return _fara_cache[kind]
    r = get(FARA_FILES[kind])
    rows = []
    if r and r.status_code == 200:
        z = zipfile.ZipFile(io.BytesIO(r.content))
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        text = z.read(name).decode("iso-8859-1")
        rows = list(csv.DictReader(io.StringIO(text)))
    _fara_cache[kind] = rows
    return rows


def reg_number(row):
    for k, v in row.items():
        if k and "registration" in k.lower() and "number" in k.lower() and v:
            return v.strip()
    return None


def check_fara(name):
    toks = tokens(name)
    if not toks:
        return {}
    result = {}
    labels = {"short_form": "Individual foreign agents (short-form registrants)",
              "registrants": "Registered foreign-agent firms and people",
              "foreign_principals": "Foreign principals (who the agents work for)"}
    regs = set()
    for kind, label in labels.items():
        # Street addresses and phone numbers are dropped: an individual agent's filing can carry a home address.
        hits = [{k: v for k, v in row.items() if k and not re.search(r"address|zip|phone", k, re.I)}
                for row in fara_rows(kind)
                if name_matches(" ".join(str(v) for k, v in row.items() if k and v), toks)]
        if hits:
            result[label] = hits[:25]
            regs.update(filter(None, (reg_number(h) for h in hits)))
    if regs:
        docs = [d for d in fara_rows("docs") if reg_number(d) in regs]
        result["Filings for those registrations (PDF links)"] = docs[-25:]
    return result


def check_fec(name):
    key = __import__("os").environ.get("FEC_API_KEY") or "DEMO_KEY"
    y = date.today().year
    cycle = y if y % 2 == 0 else y + 1
    out = []
    for c in (cycle, cycle - 2):
        r = get("https://api.open.fec.gov/v1/schedules/schedule_b/",
                params={"api_key": key, "recipient_name": name, "two_year_transaction_period": c,
                        "per_page": 50, "sort": "-disbursement_date"})
        if r and r.status_code == 200:
            for row in r.json().get("results", []):
                out.append({"committee": (row.get("committee") or {}).get("name"), "paid_to": row.get("recipient_name"),
                            "amount": row.get("disbursement_amount"), "date": (row.get("disbursement_date") or "")[:10],
                            "purpose": row.get("disbursement_description"), "filing": row.get("pdf_url")})
    return out


def check_lda(name):
    for base in ("https://lda.gov/api/v1/filings/", "https://lda.senate.gov/api/v1/filings/"):
        r = get(base, params={"lobbyist_name": name})
        if r and r.status_code == 200:
            try:
                res = r.json().get("results", [])
            except ValueError:
                continue
            return [{"registrant": (f.get("registrant") or {}).get("name"), "client": (f.get("client") or {}).get("name"),
                     "year": f.get("filing_year"), "period": f.get("filing_period_display"),
                     "filing": f.get("filing_document_url")} for f in res[:25]]
    return []


def check_usaspending(name):
    out = []
    for group, codes in {"contract": ["A", "B", "C", "D"], "grant": ["02", "03", "04", "05"]}.items():
        body = {"filters": {"recipient_search_text": [name], "award_type_codes": codes},
                "fields": ["Award ID", "Recipient Name", "Award Amount", "Awarding Agency", "Description"],
                "sort": "Award Amount", "order": "desc", "limit": 25, "page": 1}
        try:
            r = S.post("https://api.usaspending.gov/api/v2/search/spending_by_award/", json=body, timeout=TIMEOUT)
            if r.status_code != 200:
                ERRORS.append(f"api.usaspending.gov answered {r.status_code}")
            else:
                for row in r.json().get("results", []):
                    gid = row.get("generated_internal_id")
                    out.append({"type": group, "recipient": row.get("Recipient Name"), "amount": row.get("Award Amount"),
                                "agency": row.get("Awarding Agency"), "description": row.get("Description"),
                                "award_page": f"https://www.usaspending.gov/award/{gid}" if gid else None})
        except requests.RequestException as e:
            ERRORS.append(f"api.usaspending.gov unreachable ({type(e).__name__})")
    return out


def check_nonprofits(name):
    r = get("https://projects.propublica.org/nonprofits/api/v2/search.json", params={"q": name})
    if not r or r.status_code != 200:
        return []
    return [{"org": o.get("name"), "city": o.get("city"), "state": o.get("state"),
             "page": f"https://projects.propublica.org/nonprofits/organizations/{o.get('ein')}"}
            for o in r.json().get("organizations", [])[:15]]


# ---------------------------------------------------------------- report
def safe(label, fn, *args):
    try:
        return fn(*args)
    except Exception as e:  # one broken source must never sink the whole report
        ERRORS.append(f"{label} check crashed: {type(e).__name__}: {str(e)[:150]}")
        return None


def run(query, name=None):
    q = query.strip()
    handle = q.lstrip("@") if (q.startswith("@") or " " not in q) else None
    names = [n for n in dict.fromkeys([name, None if handle else q]) if n]
    rep = {"query": q, "handle": handle, "names": names,
           "ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "sections": {}}
    if handle:
        rep["sections"]["Link-in-bio pages"] = safe("Link-in-bio", check_link_in_bio, handle)
        rep["sections"]["YouTube"] = safe("YouTube", check_youtube, handle)
        rep["sections"]["X threads (Thread Reader App copies)"] = safe("Thread Reader", check_threadreader, handle)
        rep["sections"]["Archived posts (Wayback Machine)"] = safe("Wayback", check_wayback, handle)
        rep["sections"]["Archived X posts read for tracked links"] = safe("Archived X posts", check_archived_posts, handle)
    for n in names:
        rep["sections"][f"FARA foreign agent records for \"{n}\""] = safe("FARA", check_fara, n)
        rep["sections"][f"FEC campaign payments to \"{n}\""] = safe("FEC", check_fec, n)
        rep["sections"][f"Lobbying disclosures naming \"{n}\""] = safe("Lobbying", check_lda, n)
        rep["sections"][f"Federal contracts and grants to \"{n}\""] = safe("USAspending", check_usaspending, n)
        rep["sections"][f"Nonprofits named \"{n}\""] = safe("Nonprofits", check_nonprofits, n)
    rep["could_not_check"] = sorted(set(ERRORS))
    return rep


def md_value(v):
    if isinstance(v, str) and v.startswith("http"):
        return f"[link]({v})"
    return str(v).replace("|", "/")[:160] if v not in (None, "") else ""


def to_markdown(rep):
    L = [f"# Check: {rep['query']}", f"Run {rep['ran_at']}.", "",
         "> Public records only. Name-based results are **possible matches** until a person confirms it is the same "
         "individual. Finding nothing does **not** mean someone is unpaid: private deals with no filing and no "
         "tracked link leave no public trace.", ""]
    if rep.get("could_not_check"):
        L += ["**Could not check (treat these sections as unknown, not clean):**"] + \
             [f"- {e}" for e in rep["could_not_check"]] + [""]
    for title, data in rep["sections"].items():
        L.append(f"## {title}")
        if data is None:
            L += ["**Could not check.** See the list at the top. This is unknown, not clean.", ""]
            continue
        if not data:
            L += ["Nothing found.", ""]
            continue
        if title == "Link-in-bio pages":
            for page in data:
                flagged = [l for l in page["links"] if l["flags"]]
                L.append(f"**{page['page']}** ({page['url']}): {len(page['links'])} links checked, {len(flagged)} flagged")
                for l in flagged:
                    L.append(f"- {l['link']} → {l['final']}  \n  {'; '.join(l['flags'])}")
            L.append("")
        elif title == "YouTube" and not data.get("channel"):
            L += ["No YouTube channel under this handle.", ""]
        elif title == "YouTube":
            L.append(f"Channel: {data['channel']}. Recent videos with sponsor language or tracked links: {len(data['videos'])}")
            for cl in data.get("channel_links", []):
                L.append(f"- Channel page link: {cl['link']} ({'; '.join(cl['flags'])})")
            for v in data["videos"]:
                disc = "discloses a sponsor" if v["discloses_sponsor"] else "**no sponsor disclosure found in description**"
                L.append(f"- {v['published']} [{v['title']}]({v['url']}): {disc}")
                for f in v["flagged_links"]:
                    L.append(f"  - {f['link']} → {f['final']} ({'; '.join(f['flags'])})")
            L.append("")
        elif title.startswith("X threads"):
            nflag = sum(1 for t in data if t["flagged_links"])
            L.append(f"{len(data)} unrolled threads read, {sum(t['links_found'] for t in data)} outside links traced, "
                     f"{nflag} threads with tracked, affiliate or funding links.")
            for t in data:
                if t["flagged_links"] or t["discloses_sponsor"]:
                    disc = "sponsor language present" if t["discloses_sponsor"] else "**no sponsor disclosure in text**"
                    L.append(f"- {t['date']} [{t['title']}]({t['url']}) ([original]({t['original']})): {disc}")
                    for f in t["flagged_links"]:
                        L.append(f"  - {f['link']} → {f['final']} ({'; '.join(f['flags'])})")
            L.append("")
        elif title.startswith("Archived X posts"):
            fl = [p for p in data["posts"] if p["flagged_links"]]
            sp = [p for p in data["posts"] if p["discloses_sponsor"]]
            L.append(f"{data['archived_posts']} posts archived; read {data['attempted']} newest, "
                     f"{data['readable']} had readable text. Posts with tracked, affiliate or funding links: {len(fl)}. "
                     f"Posts with sponsor language: {len(sp)}.")
            if data["top_destinations"]:
                L.append("Most-linked outside sites: " + ", ".join(f"{h} ({n})" for h, n in data["top_destinations"]))
            for p in fl:
                disc = "sponsor language present" if p["discloses_sponsor"] else "**no sponsor disclosure in text**"
                note = " (reply: the link may belong to the post being replied to)" if p.get("reply") else ""
                L.append(f"- {p['captured']} [archived post]({p['archived']}): {disc}{note}  \n  \"{p['text'][:160]}\"")
                for f in p["flagged_links"]:
                    L.append(f"  - {f['link']} → {f['final']} ({'; '.join(f['flags'])})")
            L.append("")
        elif title.startswith("Archived posts"):
            for a in data:
                L.append(f"- {a['where']}: {a['archived_pages']} archived pages. [Browse]({a['browse']})")
            L.append("")
        elif isinstance(data, dict):
            L.append("_Possible matches by name. Confirm identity before using._")
            for sub, rows in data.items():
                L.append(f"### {sub} ({len(rows)})")
                cols = list(rows[0].keys())[:7]
                L.append("| " + " | ".join(cols) + " |")
                L.append("|" + "---|" * len(cols))
                for row in rows:
                    L.append("| " + " | ".join(md_value(row.get(c)) for c in cols) + " |")
            L.append("")
        else:
            L.append("_Possible matches by name. Confirm identity before using._")
            cols = list(data[0].keys())
            L.append("| " + " | ".join(cols) + " |")
            L.append("|" + "---|" * len(cols))
            for row in data:
                L.append("| " + " | ".join(md_value(row.get(c)) for c in cols) + " |")
            L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True, help="a handle (with or without @) or a full name")
    ap.add_argument("--name", help="optional real or business name to check alongside a handle")
    ap.add_argument("--out", default="report")
    a = ap.parse_args()
    rep = run(a.query, a.name)
    Path(f"{a.out}.json").write_text(json.dumps(rep, indent=1, default=str))
    try:
        md = to_markdown(rep)
    except Exception as e:
        md = f"# Check: {rep['query']}\n\nReport formatting failed ({e}). Raw data is in the .json file."
    Path(f"{a.out}.md").write_text(md)
    print(f"wrote {a.out}.md and {a.out}.json")

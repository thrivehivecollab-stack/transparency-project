# Transparency Project

Public records on money, influence and government. Every entry comes from an official source, is archived, and is checked by a person before it is published.

## How it works

```
GitHub Actions (every 6h)          Supabase (free)                 GitHub Pages + CDN
collectors/*.py  ───────────────▶  items: status = pending   
  pull official records             │                         
  save source link + archive        ▼                         
  run automatic checks          admin.html review queue        
                                  approve / edit / reject      
                                    │                          
                                    ▼                          
                                 status = approved ─────────▶ site/build.py (every 30 min)
                                                               static pages, RSS, open data
polls.html ◀──── votes and suggestions (one per device, suggestions reviewed first)
```

- Nothing is public until approved. The public site is built with the public (anon) key, and database rules only let that key read approved entries.
- Changes after publishing go through a public corrections log.
- Static pages served from a CDN handle heavy traffic at no cost.

## Trackers

| Tracker | Source | Phase |
| --- | --- | --- |
| House stock trade reports | House Clerk financial disclosures | 1 |
| Campaign money to influencers | FEC Schedule B | 1 |
| Tax dollars spent on persuasion | USAspending.gov | 1 |
| Votes vs donors, buried bills, promises | House/Senate roll calls, Congress.gov, GAO | 2 |
| Hidden studies | ClinicalTrials.gov, FDAAA TrialsTracker, UCSF Industry Documents | 3 |
| Covert influencer campaigns | Tracked-link domains, Wayback CDX, ad libraries | 3 |

To change what gets tracked, edit `collectors/watchlist.json`.

## Running locally
```
pip install -r requirements.txt
cd collectors && DRY_RUN=1 python fec_influencer.py     # prints what it would save
cd site && SAMPLE=1 python build.py                     # builds the site from sample data
```

See SETUP.md for the one-time setup.

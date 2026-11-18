"""Plain-language descriptions of each tracker, shown on the site and the methodology page."""

TRACKERS = {
    "house_trades": {
        "name": "Congress stock trades",
        "blurb": "Stock trade reports filed by members of the House.",
        "method": ("Pulled from the House Clerk's public financial disclosure index. Each entry is one "
                   "Periodic Transaction Report, linked to the original PDF. Members can file up to 45 days "
                   "after a trade, so a report can describe trades from weeks earlier."),
        "source": "House Clerk, Financial Disclosure Reports",
    },
    "fec_influencer": {
        "name": "Campaign money to influencers",
        "blurb": "Political committees' payments to influencer and creator marketing.",
        "method": ("Pulled from the FEC's itemized disbursements (Schedule B), searching payment descriptions "
                   "for influencer and creator terms and payee names of known influencer agencies. Committees "
                   "usually report the agency they paid, not the individual creators."),
        "source": "Federal Election Commission",
    },
    "usaspending_persuasion": {
        "name": "Tax dollars spent on persuasion",
        "blurb": "Federal contracts and grants for PR, ads, social media and influencer campaigns.",
        "method": ("Pulled from USAspending.gov by keyword. Keyword matches are broad, and each award is "
                   "reviewed by hand before it appears here. Award amounts are the totals reported to "
                   "USAspending, which can lag 30 to 60 days."),
        "source": "USAspending.gov (U.S. Treasury)",
    },
}


def tracker(key):
    return TRACKERS.get(key, {"name": key.replace("_", " ").title(), "blurb": "", "method": "", "source": ""})

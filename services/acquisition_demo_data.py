"""
services/acquisition_demo_data.py

Generates 20 synthetic online communities for Tab 3 demo.

Reflects the real community landscape: Facebook diaspora groups and Instagram
accounts serving Balkan migrants in Turkish cities.  Data is seeded for
reproducibility (seed=42) and covers a realistic spread of:
  - Platforms (Facebook-heavy, some Instagram)
  - Languages (Serbian, Bosnian, Croatian, Macedonian, Albanian, mixed)
  - Cities (Istanbul-weighted, plus Bursa, Ankara, Izmir, Edirne)
  - Sizes (500 – 48 000 members)
  - Quality (some inactive/low-relevance communities for filtering realism)

Seeded for reproducibility (seed=42).
"""

from __future__ import annotations

import random

from models.lead_acquisition import (
    Channel,
    Community,
    Language,
    Platform,
)

# ---------------------------------------------------------------------------
# Community templates — (name, platform, language, city, member_count_range,
#                         activity_score_range, url_template, notes)
# ---------------------------------------------------------------------------

_COMMUNITY_TEMPLATES: list[dict] = [
    # ── Large Serbian Facebook groups ──────────────────────────────────────
    {
        "name": "Srbi u Turskoj – Istanbul",
        "platform": Platform.FACEBOOK,
        "language": Language.SERBIAN,
        "city": "Istanbul",
        "members": (12_000, 18_000),
        "activity": (0.70, 0.90),
        "url": "https://facebook.com/groups/srbiistanbul",
        "notes": ["Most active Serbian group in Istanbul", "Admin responds to DMs"],
    },
    {
        "name": "Srpska zajednica Turska",
        "platform": Platform.FACEBOOK,
        "language": Language.SERBIAN,
        "city": "Istanbul",
        "members": (6_000, 9_500),
        "activity": (0.55, 0.75),
        "url": "https://facebook.com/groups/srpska.zajednica.turska",
        "notes": ["Posts in both Serbian and Turkish", "Weekly event announcements"],
    },
    # ── Bosnian groups ────────────────────────────────────────────────────
    {
        "name": "Bosanci u Turskoj",
        "platform": Platform.FACEBOOK,
        "language": Language.BOSNIAN,
        "city": "Istanbul",
        "members": (8_500, 14_000),
        "activity": (0.65, 0.85),
        "url": "https://facebook.com/groups/bosanci.turska",
        "notes": ["Strong admin team", "Regular Ramadan event posts"],
    },
    {
        "name": "Bošnjaci Istanbul",
        "platform": Platform.FACEBOOK,
        "language": Language.BOSNIAN,
        "city": "Istanbul",
        "members": (3_200, 5_000),
        "activity": (0.40, 0.60),
        "url": "https://facebook.com/groups/bosnjaci.istanbul",
        "notes": ["Smaller but highly engaged", "Mostly Sarajevo-origin members"],
    },
    {
        "name": "Bosanska dijaspora Bursa",
        "platform": Platform.FACEBOOK,
        "language": Language.BOSNIAN,
        "city": "Bursa",
        "members": (1_800, 3_000),
        "activity": (0.30, 0.55),
        "url": "https://facebook.com/groups/bosanska.dijaspora.bursa",
        "notes": ["Smaller city group", "Mix of Bosnian and Croatian speakers"],
    },
    # ── Croatian groups ───────────────────────────────────────────────────
    {
        "name": "Hrvati u Turskoj",
        "platform": Platform.FACEBOOK,
        "language": Language.CROATIAN,
        "city": "Istanbul",
        "members": (4_100, 7_000),
        "activity": (0.50, 0.70),
        "url": "https://facebook.com/groups/hrvati.turska",
        "notes": ["Active Croatian community", "Admin is responsive"],
    },
    {
        "name": "Hrvatska zajednica Ankara",
        "platform": Platform.FACEBOOK,
        "language": Language.CROATIAN,
        "city": "Ankara",
        "members": (900, 1_800),
        "activity": (0.20, 0.40),
        "url": "https://facebook.com/groups/hrvatska.zajednica.ankara",
        "notes": ["Low activity", "Many members appear inactive"],
    },
    # ── Macedonian groups ─────────────────────────────────────────────────
    {
        "name": "Makedonci vo Turčija",
        "platform": Platform.FACEBOOK,
        "language": Language.MACEDONIAN,
        "city": "Istanbul",
        "members": (5_500, 9_000),
        "activity": (0.60, 0.80),
        "url": "https://facebook.com/groups/makedonci.turcija",
        "notes": ["Very active", "Admin posts job listings and local news"],
    },
    {
        "name": "Makedonska dijaspora Bursa",
        "platform": Platform.FACEBOOK,
        "language": Language.MACEDONIAN,
        "city": "Bursa",
        "members": (1_100, 2_200),
        "activity": (0.25, 0.45),
        "url": "https://facebook.com/groups/makedonska.dijaspora.bursa",
        "notes": ["Moderate activity", "Textile industry workers"],
    },
    # ── Albanian groups ───────────────────────────────────────────────────
    {
        "name": "Shqiptarët në Turqi",
        "platform": Platform.FACEBOOK,
        "language": Language.ALBANIAN,
        "city": "Istanbul",
        "members": (22_000, 32_000),
        "activity": (0.75, 0.92),
        "url": "https://facebook.com/groups/shqiptaret.turqi",
        "notes": ["Largest Balkan diaspora group", "Admin DMs are unreliable"],
    },
    {
        "name": "Albanians in Turkey – Business",
        "platform": Platform.FACEBOOK,
        "language": Language.ALBANIAN,
        "city": "Istanbul",
        "members": (3_800, 6_200),
        "activity": (0.55, 0.72),
        "url": "https://facebook.com/groups/albanians.turkey.business",
        "notes": ["Business-focused", "Higher income bracket — premium package interest"],
    },
    {
        "name": "Shqiptarët Izmir",
        "platform": Platform.FACEBOOK,
        "language": Language.ALBANIAN,
        "city": "Izmir",
        "members": (800, 1_600),
        "activity": (0.20, 0.38),
        "url": "https://facebook.com/groups/shqiptaret.izmir",
        "notes": ["Smaller city", "Low recent activity"],
    },
    # ── Mixed-language groups ─────────────────────────────────────────────
    {
        "name": "Balkanlılar İstanbul",
        "platform": Platform.FACEBOOK,
        "language": Language.MIXED,
        "city": "Istanbul",
        "members": (38_000, 48_000),
        "activity": (0.72, 0.88),
        "url": "https://facebook.com/groups/balkanlilar.istanbul",
        "notes": [
            "Largest mixed-origin Balkan group",
            "Turkish+Serbian+Bosnian+Albanian posts",
            "Admin is a prominent community figure",
        ],
    },
    {
        "name": "Balkan Diaspora Turkey",
        "platform": Platform.FACEBOOK,
        "language": Language.MIXED,
        "city": "Istanbul",
        "members": (11_000, 17_000),
        "activity": (0.58, 0.78),
        "url": "https://facebook.com/groups/balkan.diaspora.turkey",
        "notes": ["English-language meta-group", "Cross-community announcements"],
    },
    {
        "name": "Ex-Yu Zajednica Turska",
        "platform": Platform.FACEBOOK,
        "language": Language.MIXED,
        "city": "Istanbul",
        "members": (7_200, 11_500),
        "activity": (0.45, 0.65),
        "url": "https://facebook.com/groups/exyu.zajednica.turska",
        "notes": ["Ex-Yugoslav identity focus", "Serbian and Croatian speakers dominant"],
    },
    # ── Instagram accounts ─────────────────────────────────────────────────
    {
        "name": "@srbi_istanbul",
        "platform": Platform.INSTAGRAM,
        "language": Language.SERBIAN,
        "city": "Istanbul",
        "members": (4_200, 7_800),
        "activity": (0.60, 0.82),
        "url": "https://instagram.com/srbi_istanbul",
        "notes": ["High engagement on Reels", "Stories reach estimated 30% of followers"],
    },
    {
        "name": "@bosanci_turska",
        "platform": Platform.INSTAGRAM,
        "language": Language.BOSNIAN,
        "city": "Istanbul",
        "members": (2_100, 4_500),
        "activity": (0.50, 0.70),
        "url": "https://instagram.com/bosanci_turska",
        "notes": ["Lifestyle and food content", "DM-friendly account"],
    },
    {
        "name": "@makedonci_istanbul",
        "platform": Platform.INSTAGRAM,
        "language": Language.MACEDONIAN,
        "city": "Istanbul",
        "members": (1_400, 3_200),
        "activity": (0.40, 0.62),
        "url": "https://instagram.com/makedonci_istanbul",
        "notes": ["Younger audience (18–35)", "Responds well to promotions"],
    },
    {
        "name": "@shqiptaret_istanbul",
        "platform": Platform.INSTAGRAM,
        "language": Language.ALBANIAN,
        "city": "Istanbul",
        "members": (8_900, 14_000),
        "activity": (0.68, 0.85),
        "url": "https://instagram.com/shqiptaret_istanbul",
        "notes": ["News + community events", "Story ads perform well"],
    },
    # ── Low-quality / outlier communities (for realism) ──────────────────
    {
        "name": "Balkanci u Turskoj – neaktivan",
        "platform": Platform.FACEBOOK,
        "language": Language.MIXED,
        "city": None,   # no city data
        "members": (500, 900),
        "activity": (0.02, 0.10),
        "url": None,
        "notes": ["Appears abandoned", "Last post >6 months ago", "No admin contact found"],
    },
]

# Priority city scores (Istanbul/Bursa highest, others moderate, None lowest)
_CITY_OVERLAP: dict[str | None, float] = {
    "Istanbul": 1.0,
    "Bursa":    0.85,
    "Edirne":   0.70,
    "Ankara":   0.60,
    "Izmir":    0.55,
    None:       0.10,
}

# Language match scores (how well each language fits MTEL's core campaign targets)
_LANG_MATCH: dict[Language, float] = {
    Language.SERBIAN:    0.95,
    Language.BOSNIAN:    0.92,
    Language.CROATIAN:   0.88,
    Language.MACEDONIAN: 0.82,
    Language.ALBANIAN:   0.75,
    Language.MIXED:      0.70,
    Language.UNKNOWN:    0.30,
}


def generate_demo_communities(seed: int = 42) -> list[Community]:
    """
    Generate the fixed set of 20 synthetic communities for Tab 3 demo.

    All values are deterministically derived from templates + seeded RNG so
    the demo is reproducible across runs.
    """
    rng = random.Random(seed)
    communities: list[Community] = []

    for i, tpl in enumerate(_COMMUNITY_TEMPLATES):
        members_lo, members_hi = tpl["members"]
        activity_lo, activity_hi = tpl["activity"]

        member_count  = rng.randint(members_lo, members_hi)
        activity      = round(rng.uniform(activity_lo, activity_hi), 3)
        lang          = tpl["language"]
        city          = tpl["city"]

        lang_match    = round(_LANG_MATCH[lang] + rng.uniform(-0.05, 0.05), 3)
        lang_match    = max(0.0, min(1.0, lang_match))
        city_overlap  = round(_CITY_OVERLAP.get(city, 0.10) + rng.uniform(-0.04, 0.04), 3)
        city_overlap  = max(0.0, min(1.0, city_overlap))

        community_id  = f"C{i+1:03d}"

        communities.append(Community(
            community_id=community_id,
            platform=tpl["platform"],
            name=tpl["name"],
            url=tpl["url"],
            language=lang,
            city=city,
            member_count=member_count,
            activity_score=activity,
            language_match_score=lang_match,
            city_overlap_score=city_overlap,
            scraper_notes=list(tpl["notes"]),
        ))

    return communities


def load_demo_communities() -> list[Community]:
    """Public convenience wrapper used by the UI and workflow."""
    return generate_demo_communities(seed=42)

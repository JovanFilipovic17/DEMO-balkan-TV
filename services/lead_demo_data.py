"""
services/lead_demo_data.py

Generates 200 synthetic raw leads for Tab 2 demo.

Reflects realistic data quality problems from manually-entered lead lists:
  - Duplicate phone numbers across different name entries
  - All-caps names from sign-up sheets
  - Missing names (field left blank)
  - Invalid Turkish phone formats
  - Placeholder emails (test@test.com, noreply@ etc.)
  - Suspicious phone patterns (all same digit)
  - Missing city

Seeded for reproducibility (seed=99).
"""

from __future__ import annotations

import random
from typing import Optional

from models.lead import RawLead

# ---------------------------------------------------------------------------
# Name pools
# ---------------------------------------------------------------------------

_FIRST_NAMES = [
    "Stefan", "Marko", "Nikola", "Petar", "Aleksandar", "Miroslav", "Dragan",
    "Ivan", "Bojan", "Dejan", "Zoran", "Milan", "Nemanja", "Vladimir", "Sasa",
    "Ana", "Ivana", "Marija", "Jelena", "Milica", "Dragana", "Vesna", "Sonja",
    "Bojana", "Tijana", "Katarina", "Snezana", "Gordana", "Natasa", "Maja",
    "Ante", "Tomislav", "Hrvoje", "Damir", "Igor", "Karlo", "Luka", "Mateo",
    "Iva", "Petra", "Valentina", "Nives", "Marina", "Dora", "Lucija",
    "Emir", "Admir", "Nermin", "Senad", "Samir", "Muamer", "Kenan", "Dzenan",
    "Amira", "Belma", "Lejla", "Selma", "Sanela", "Emina", "Merima",
    "Goran", "Darko", "Jovan", "Slobodan", "Drago", "Predrag", "Radoslav",
    "Mujo", "Huso", "Suljo", "Ferid", "Amir",
    "Gjorgji", "Aleksandar", "Blagoja", "Trajce", "Zoran",
    "Besnik", "Agron", "Artan", "Florian", "Erjon", "Blendi",
    "Fatmire", "Vjosa", "Teuta", "Albana",
]

_LAST_NAMES = [
    "Jovanovic", "Petrovic", "Nikolic", "Djordjevic", "Markovic", "Stojanovic",
    "Ilic", "Stankovic", "Radic", "Kovacevic", "Bogdanovic", "Milovanovic",
    "Horvat", "Kovac", "Babic", "Maric", "Juric", "Knezevic", "Novak",
    "Hadzic", "Muratovic", "Omerovic", "Beganovic", "Delic", "Hasanovic",
    "Popovic", "Lazarevic", "Todorovic", "Milosevic", "Pejovic",
    "Trajkovski", "Ristovski", "Angelovski", "Stojanov",
    "Krasniqi", "Berisha", "Gashi", "Hoxha", "Shehu", "Mustafa",
    "Osmani", "Rama", "Kurti",
]

_TURKISH_CITIES = [
    "Istanbul", "Bursa", "Ankara", "Izmir", "Edirne",
    "Istanbul", "Istanbul", "Istanbul",  # weighted
    "Bursa", "Bursa",
]

_LANGUAGES = ["sr", "hr", "bs", "mk", "sq", "sl"]
_LANG_WEIGHTS = [0.33, 0.23, 0.23, 0.10, 0.07, 0.04]

_SOURCES = ["Facebook", "Referral", "Event", "WhatsApp Group", "Website", "Unknown"]
_SOURCE_WEIGHTS = [0.35, 0.25, 0.15, 0.15, 0.07, 0.03]


# ---------------------------------------------------------------------------
# Phone generators
# ---------------------------------------------------------------------------

def _valid_turkish_phone(rng: random.Random) -> str:
    prefixes = ["532", "533", "535", "537", "541", "542", "543", "544",
                "545", "546", "547", "548", "549", "551", "552", "553",
                "554", "555", "559", "561"]
    return f"+90{rng.choice(prefixes)}{rng.randint(1000000, 9999999)}"


def _invalid_phone(rng: random.Random) -> str:
    """Phone that looks like it was entered wrong."""
    bad = [
        "05321234567",         # missing country code
        "+9053",               # truncated
        "0532 123 45 67",      # spaces, no +
        "+90532000000",        # too short
        "+905320000000000",    # too long
        "5321234567",          # no country code
        "00905321234567",      # double zero format
    ]
    return rng.choice(bad)


def _suspicious_phone(rng: random.Random) -> str:
    """Phone with obviously fake digits."""
    digit = str(rng.randint(0, 9))
    return f"+905{digit * 9}"


# ---------------------------------------------------------------------------
# Email generators
# ---------------------------------------------------------------------------

def _valid_email(name: str, rng: random.Random) -> str:
    parts = name.lower().split()
    domains = ["gmail.com", "hotmail.com", "yahoo.com", "outlook.com"]
    base = ".".join(parts[:2]) if len(parts) >= 2 else parts[0]
    base = base.replace("đ", "d").replace("ž", "z").replace("š", "s").replace("ć", "c")
    num = rng.choice(["", str(rng.randint(1, 99))])
    return f"{base}{num}@{rng.choice(domains)}"


_PLACEHOLDER_EMAILS = [
    "test@test.com", "noemail@gmail.com", "none@none.com",
    "no@email.com", "aaa@aaa.com", "123@123.com", "x@x.com",
    "noreply@noreply.com", "email@email.com",
]


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate_demo_leads(n: int = 200, seed: int = 99) -> list[RawLead]:
    """
    Generate n synthetic raw leads with realistic data quality issues.

    Injected issues (approximate):
        ~15 duplicate phone pairs
        ~8  duplicate email pairs
        ~15 invalid Turkish phones
        ~10 all-caps names
        ~12 missing names
        ~8  placeholder emails
        ~5  suspicious phone patterns
        ~20 missing city
        Remainder: reasonably clean with minor variations
    """
    rng = random.Random(seed)
    leads: list[RawLead] = []

    def _name(caps: bool = False) -> str:
        first = rng.choice(_FIRST_NAMES)
        last  = rng.choice(_LAST_NAMES)
        full  = f"{first} {last}"
        return full.upper() if caps else full

    # ── Build base pool of 165 leads ─────────────────────────────────────
    for i in range(165):
        caps        = i < 10                     # first 10: all-caps
        no_name     = 10 <= i < 22               # next 12: missing name
        bad_phone   = 22 <= i < 37              # next 15: invalid phone
        sus_phone   = 37 <= i < 42              # next 5: suspicious
        ph_email    = 42 <= i < 50              # next 8: placeholder email
        no_city     = 50 <= i < 70              # next 20: missing city
        no_email    = 70 <= i < 90              # next 20: no email

        full_name = None if no_name else _name(caps=caps)
        phone     = (
            _suspicious_phone(rng) if sus_phone
            else _invalid_phone(rng) if bad_phone
            else _valid_turkish_phone(rng)
        )
        email = (
            rng.choice(_PLACEHOLDER_EMAILS) if ph_email
            else None if no_email
            else (_valid_email(full_name, rng) if full_name else None)
        )
        city     = None if no_city else rng.choice(_TURKISH_CITIES)
        language = rng.choices(_LANGUAGES, _LANG_WEIGHTS)[0]
        source   = rng.choices(_SOURCES, _SOURCE_WEIGHTS)[0]

        leads.append(RawLead(
            row_index=i,
            full_name=full_name,
            phone=phone,
            email=email,
            city=city,
            language=language,
            source=source,
            notes=None,
        ))

    # ── Inject duplicate phones (15 pairs) ───────────────────────────────
    # Pick 15 leads with valid phones and clone their phone onto a new lead
    valid_phone_leads = [l for l in leads if l.phone and l.phone.startswith("+905")]
    dup_sources = rng.sample(valid_phone_leads, min(15, len(valid_phone_leads)))
    for orig in dup_sources:
        dup_name = _name()  # different name, same phone
        leads.append(RawLead(
            row_index=len(leads),
            full_name=dup_name,
            phone=orig.phone,  # same phone!
            email=_valid_email(dup_name, rng) if rng.random() > 0.4 else None,
            city=rng.choice(_TURKISH_CITIES),
            language=rng.choices(_LANGUAGES, _LANG_WEIGHTS)[0],
            source=rng.choices(_SOURCES, _SOURCE_WEIGHTS)[0],
            notes=None,
        ))

    # ── Inject duplicate emails (8 pairs) ────────────────────────────────
    valid_email_leads = [l for l in leads if l.email and "@" in l.email
                         and l.email not in _PLACEHOLDER_EMAILS]
    dup_email_sources = rng.sample(valid_email_leads, min(8, len(valid_email_leads)))
    for orig in dup_email_sources:
        dup_name = _name()
        leads.append(RawLead(
            row_index=len(leads),
            full_name=dup_name,
            phone=_valid_turkish_phone(rng),
            email=orig.email,  # same email!
            city=rng.choice(_TURKISH_CITIES),
            language=rng.choices(_LANGUAGES, _LANG_WEIGHTS)[0],
            source=rng.choices(_SOURCES, _SOURCE_WEIGHTS)[0],
            notes=None,
        ))

    # ── Trim or pad to exactly n ──────────────────────────────────────────
    leads = leads[:n]

    # Re-index
    for i, lead in enumerate(leads):
        object.__setattr__(lead, "row_index", i) if lead.model_config.get("frozen") \
            else leads.__setitem__(i, lead.model_copy(update={"row_index": i}))

    return leads


def load_demo_leads(n: int = 200) -> list[RawLead]:
    """Public convenience wrapper used by the UI."""
    return generate_demo_leads(n=n, seed=99)

"""
services/demo_data.py

Synthetic demo dataset for Tab 1: Existing Customers / ERP Follow-up.

Business context:
    This TV provider operates in Turkey and serves Balkan diaspora customers
    (Serbian, Croatian, Bosnian, Macedonian, Slovenian communities) living
    across Turkish cities. There is a single subscription offer at $39/month.

Generates 500 realistic CustomerRecord instances covering:
    - All CustomerStatus scenarios (active, overdue, expired, suspended, unknown)
    - All contact edge cases (valid both channels, phone only, email only, neither, malformed)
    - Turkish cities as the customer location
    - Balkan names and languages (sr, hr, bs, mk, sl)
    - Single plan at $39/month; outstanding balances in whole multiples of $39

Generation is seeded (default seed=42) so results are fully reproducible
across runs -- essential for consistent UI demos and repeatable unit tests.

Usage:
    from services.demo_data import load_demo_customers
    records = load_demo_customers()         # 500 records, seed 42
    records = load_demo_customers(n=10)    # first 10, same seed
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Optional

from models.customer import CustomerRecord


# ---------------------------------------------------------------------------
# Business constants
# ---------------------------------------------------------------------------

PLAN_NAME = "Balkan TV"
PLAN_PRICE_USD = 39.00   # single fixed monthly price


# ---------------------------------------------------------------------------
# Data pools
# ---------------------------------------------------------------------------

_FIRST_NAMES: list[str] = [
    # Serbian / Bosnian
    "Marko", "Nikola", "Stefan", "Aleksa", "Milos", "Nemanja", "Bojan", "Dragan",
    "Vladimir", "Darko", "Zoran", "Sinisa", "Predrag", "Goran", "Slavko", "Dusan",
    "Nenad", "Sasa", "Mladen", "Rade", "Dejan", "Ivan", "Luka", "Petar", "Filip",
    "Milica", "Jelena", "Ana", "Ivana", "Bojana", "Sanja", "Dragana", "Gordana",
    "Vesna", "Maja", "Tamara", "Tijana", "Nikolina", "Snezana", "Zorana", "Mirjana",
    # Croatian
    "Tomislav", "Josip", "Hrvoje", "Damir", "Kresimir", "Branko", "Zvonimir",
    "Vedran", "Ante", "Marin", "Domagoj", "Stjepan", "Alen", "Denis", "Bruno",
    "Katarina", "Marina", "Mirela", "Danijela", "Petra", "Andreja", "Lucija",
    "Iva", "Valentina", "Renata", "Tanja", "Silvija", "Jasmina",
    # Bosnian
    "Adnan", "Emir", "Haris", "Senad", "Dzemal", "Almir", "Amer", "Kenan",
    "Muamer", "Tarik", "Nedim", "Enis", "Sead", "Mirza", "Eldin",
    "Amira", "Lejla", "Selma", "Emina", "Amela", "Dina", "Belma", "Sabina",
    # Macedonian
    "Aleksandar", "Dimitar", "Igor", "Kiril", "Tome", "Blagoj", "Orce", "Vlatko",
    "Elena", "Sonja", "Marija", "Natasha", "Dragica", "Biljana", "Lidija",
    # Slovenian
    "Janez", "Matej", "Andrej", "Gregor", "Simon", "Rok", "Blaz", "Jure",
    "Mojca", "Tjasa", "Spela", "Urska", "Anja", "Natasa", "Katja",
    # Albanian (understand Balkan languages, present in Turkish diaspora)
    "Arben", "Besnik", "Driton", "Ergys", "Faton", "Gezim", "Ilir", "Kujtim",
    "Lulzim", "Mentor", "Nexhip", "Omer", "Qemal", "Rexhep", "Skender",
    "Albana", "Blerina", "Donika", "Ermira", "Flutura", "Gentiana", "Lindita",
    "Mimoza", "Nafije", "Pranvera", "Shpresa", "Teuta", "Valentina", "Zamira",
]

_LAST_NAMES: list[str] = [
    # Serbian
    "Jovanovic", "Petrovic", "Nikolic", "Markovic", "Djordjevic", "Stojanovic",
    "Ilic", "Stankovic", "Popovic", "Lazic", "Pavlovic", "Milosevic", "Savic",
    "Vukovic", "Simic", "Todorovic", "Filipovic", "Djuric", "Jankovic", "Kostic",
    "Bogdanovic", "Vasic", "Marinkovic", "Ristic", "Pejic", "Kovacevic",
    # Croatian
    "Horvat", "Kovac", "Babic", "Maric", "Tomic", "Juric", "Novak", "Blazevic",
    "Knezevic", "Vukovic", "Pavic", "Bozic", "Peric", "Matic", "Radic",
    "Simic", "Kovacic", "Loncar", "Saric", "Grgic",
    # Bosnian
    "Hodzic", "Mehmedovic", "Basic", "Jusic", "Muratovic", "Ibrahimovic",
    "Hasanovic", "Salihovic", "Mujic", "Beganovic", "Smajic", "Avdic",
    # Macedonian
    "Stojanovski", "Dimovski", "Petrovski", "Nikolovski", "Georgievski",
    "Trajkovski", "Ristovski", "Todorov", "Blazhevski", "Mitevski",
    # Slovenian
    "Novak", "Kovac", "Krajnc", "Zupan", "Potocnik", "Vidmar", "Golob",
    "Pregelj", "Kos", "Bertoncelj",
    # Albanian
    "Hoxha", "Shehu", "Kelmendi", "Berisha", "Rama", "Doda", "Gashi",
    "Leka", "Murati", "Osmani", "Prifti", "Qosja", "Shala", "Thaqi", "Zeqiri",
]

# Turkish cities where Balkan diaspora communities are concentrated.
# Istanbul is by far the largest hub, weighted accordingly.
_CITIES: list[str] = [
    "Istanbul", "Istanbul", "Istanbul", "Istanbul", "Istanbul",  # ~50% in Istanbul
    "Bursa", "Bursa",         # historic Balkan settlement hub
    "Ankara", "Ankara",
    "Izmir",
    "Antalya",
    "Edirne",                 # border city, significant Balkan community
    "Kirklareli",
    "Tekirdag",
    "Kocaeli",
    "Eskisehir",
    "Sakarya",
    "Balikesir",
]

_LANGUAGES: list[tuple[str, float]] = [
    ("sr", 0.33),
    ("hr", 0.23),
    ("bs", 0.23),
    ("mk", 0.10),
    ("sl", 0.04),
    ("sq", 0.07),   # Albanian -- understand Balkan content, present in Turkish diaspora
]

_ERP_NOTES_POOL: list[str] = [
    "Customer prefers contact in the evening.",
    "Left voicemail on last call attempt.",
    "Renewed last year without issues.",
    "No answer on three consecutive calls.",
    "Prefers WhatsApp contact.",
    "Payment usually late but always arrives.",
    "Contact via email only.",
    "Long-term customer since 2021.",
    "Asked about adding a second screen.",
    "Mentioned family also wants to subscribe.",
    "Disputed charge -- resolved.",
    "Referred by existing customer.",
    "Has seasonal travel -- sometimes pays late.",
    "Requested receipt via email.",
    "Recently changed phone number.",
    "Works night shifts -- call before noon.",
    "Interested in annual discount option.",
    "Previously churned, came back 2023.",
    "",
    "",
    "",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _weighted_choice(rng: random.Random, options: list[tuple[str, float]]) -> str:
    """Pick from a weighted list of (value, weight) tuples."""
    values, weights = zip(*options)
    return rng.choices(list(values), weights=list(weights), k=1)[0]


def _random_phone(rng: random.Random, valid: bool) -> Optional[str]:
    """
    Return a Turkish mobile number (+90 5XX XXX XXXX) or a broken value.

    Turkish mobile numbers start with +90 5, followed by 9 digits.
    Invalid variants simulate real ERP data quality issues.
    """
    if not valid:
        return rng.choice([None, None, "00000", "N/A", "123", "+90"])
    operator = rng.choice(["530", "531", "532", "533", "535", "537",
                            "541", "542", "543", "544", "545",
                            "551", "552", "553", "554", "555"])
    rest = "".join(str(rng.randint(0, 9)) for _ in range(7))
    return f"+90{operator}{rest}"


def _random_email(rng: random.Random, first: str, last: str, valid: bool) -> Optional[str]:
    """
    Return a plausible email address or a broken/missing value.

    Valid addresses use common domains. Invalid variants simulate
    truncated, placeholder, or missing ERP entries.
    """
    if not valid:
        return rng.choice([
            None, None,
            "noemail",
            f"{first.lower()}@",
            "invalid@nodomain",
            "n/a",
        ])
    domains = [
        "gmail.com", "gmail.com", "gmail.com",
        "hotmail.com", "yahoo.com",
        "outlook.com", "icloud.com",
        "yandex.com",       # common in Turkey
        "hotmail.com.tr",
    ]
    # Normalise Balkan diacritics for ASCII-safe email slugs
    replacements = str.maketrans(
        "đšćčžĐŠĆČŽ",
        "dscczDSCCZ",
    )
    slug = f"{first.lower()}.{last.lower()}".translate(replacements).replace(" ", "")
    suffix = rng.choice(["", str(rng.randint(1, 99))])
    return f"{slug}{suffix}@{rng.choice(domains)}"


def _make_dates(
    rng: random.Random,
    today: date,
    scenario: str,
) -> tuple[Optional[date], Optional[date], Optional[date]]:
    """
    Return (subscription_start, subscription_end, last_payment_date) for a scenario.

    Scenarios map to realistic ERP states:
        active_healthy    -- active, paid on time, renews in 30-90 days
        active_expiring   -- active, expires within 14 days
        overdue_short     -- overdue 1-30 days (one missed payment)
        overdue_long      -- overdue 31-120 days (multiple missed payments)
        expired           -- lapsed 3-18 months ago (win-back candidate)
        suspended         -- admin-suspended despite having an end date
        unknown           -- missing date data, needs data cleanup
    """
    if scenario == "active_healthy":
        start = today - timedelta(days=rng.randint(30, 300))
        end = today + timedelta(days=rng.randint(30, 90))
        paid = today - timedelta(days=rng.randint(1, 28))

    elif scenario == "active_expiring":
        start = today - timedelta(days=rng.randint(30, 300))
        end = today + timedelta(days=rng.randint(1, 13))
        paid = today - timedelta(days=rng.randint(15, 45))

    elif scenario == "overdue_short":
        start = today - timedelta(days=rng.randint(60, 300))
        end = today - timedelta(days=rng.randint(1, 30))
        paid = today - timedelta(days=rng.randint(30, 90))

    elif scenario == "overdue_long":
        start = today - timedelta(days=rng.randint(90, 400))
        end = today - timedelta(days=rng.randint(31, 120))
        paid = today - timedelta(days=rng.randint(90, 200))

    elif scenario == "expired":
        start = today - timedelta(days=rng.randint(400, 700))
        end = today - timedelta(days=rng.randint(90, 540))
        paid = end - timedelta(days=rng.randint(5, 30))

    elif scenario == "suspended":
        start = today - timedelta(days=rng.randint(200, 500))
        end = today + timedelta(days=rng.randint(10, 60))
        paid = today - timedelta(days=rng.randint(90, 180))

    else:  # unknown
        return None, None, None

    return start, end, paid


def _outstanding_balance(rng: random.Random, scenario: str) -> float:
    """
    Return an outstanding balance appropriate to the scenario.

    Since there is only one plan at $39/month, balances are whole
    multiples of $39 (1, 2, or 3 missed months).
    """
    if scenario in ("overdue_short",):
        months = rng.choice([1, 1, 1, 2])
    elif scenario in ("overdue_long",):
        months = rng.choice([1, 2, 2, 3])
    elif scenario == "expired":
        months = rng.choice([0, 1, 2, 3])
    elif scenario == "suspended":
        months = rng.choice([1, 2])
    else:
        months = 0

    return round(months * PLAN_PRICE_USD, 2)


# ---------------------------------------------------------------------------
# Scenario and contact distributions
# ---------------------------------------------------------------------------

# (scenario_label, weight)
_SCENARIOS: list[tuple[str, float]] = [
    ("active_healthy",  0.30),
    ("active_expiring", 0.12),
    ("overdue_short",   0.20),
    ("overdue_long",    0.15),
    ("expired",         0.14),
    ("suspended",       0.05),
    ("unknown",         0.04),
]

# (phone_valid, email_valid, weight)
_CONTACT_CASES: list[tuple[bool, bool, float]] = [
    (True,  True,  0.50),   # both channels valid
    (True,  False, 0.22),   # phone only (very common in Turkish market)
    (False, True,  0.13),   # email only
    (False, False, 0.15),   # no valid contact -- needs data cleanup
]


# ---------------------------------------------------------------------------
# Record factory
# ---------------------------------------------------------------------------

def _make_record(rng: random.Random, today: date, idx: int) -> CustomerRecord:
    """Build one synthetic CustomerRecord."""
    first = rng.choice(_FIRST_NAMES)
    last = rng.choice(_LAST_NAMES)

    scenario = _weighted_choice(rng, _SCENARIOS)
    sub_start, sub_end, last_paid = _make_dates(rng, today, scenario)

    phone_valid, email_valid, _ = rng.choices(
        _CONTACT_CASES,
        weights=[c[2] for c in _CONTACT_CASES],
        k=1,
    )[0]

    last_amount: Optional[float] = PLAN_PRICE_USD if last_paid is not None else None

    note = rng.choice(_ERP_NOTES_POOL)

    return CustomerRecord(
        customer_id=f"ERP-{idx:05d}",
        full_name=f"{first} {last}",
        phone=_random_phone(rng, phone_valid),
        email=_random_email(rng, first, last, email_valid),
        country=rng.choice(_CITIES),   # city in Turkey
        language=_weighted_choice(rng, _LANGUAGES),
        subscription_plan=PLAN_NAME,
        subscription_start=sub_start,
        subscription_end=sub_end,
        last_payment_date=last_paid,
        last_payment_amount=last_amount,
        outstanding_balance=_outstanding_balance(rng, scenario),
        notes=note or None,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_demo_customers(
    n: int = 500,
    seed: int = 42,
    reference_date: Optional[date] = None,
) -> list[CustomerRecord]:
    """
    Generate `n` synthetic CustomerRecord instances.

    Args:
        n: Number of records to generate. Default 500.
        seed: Random seed for reproducibility. Change to get a different dataset.
        reference_date: Date used as "today" for computing overdue/expiry.
                        Defaults to the actual current date.

    Returns:
        List of CustomerRecord objects ready for pipeline ingestion.

    Example:
        >>> records = generate_demo_customers(n=5)
        >>> records[0].subscription_plan
        'Balkan TV'
    """
    today = reference_date or date.today()
    rng = random.Random(seed)
    return [_make_record(rng, today, idx + 1) for idx in range(n)]


def load_demo_customers(n: int = 500) -> list[CustomerRecord]:
    """
    Convenience wrapper returning the canonical 500-record demo dataset.

    This is the function the UI and workflow call by default.
    The seed is fixed so the demo is identical across app restarts.

    Args:
        n: Slice size. Default 500. Pass a smaller number for fast
           dev/test cycles without changing the seed.
    """
    return generate_demo_customers(n=n, seed=42)

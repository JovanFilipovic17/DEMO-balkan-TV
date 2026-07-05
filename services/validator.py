"""
services/validator.py

Deterministic contact validation for Tab 1: Existing Customers / ERP Follow-up.

Responsibilities:
    - Validate phone numbers against all accepted country prefixes
    - Validate email addresses with a lightweight regex (no external calls)
    - Build a ContactValidity object summarising what is usable and what is not

Design notes:
    Phone validation is prefix-based, not carrier-lookup-based. The goal is to
    determine whether a number is plausibly reachable, not to verify it exists.

    Accepted prefixes cover two categories:
        1. Turkey (+90)  -- where the customers currently live
        2. Balkan / Albanian home-country codes -- customers often retain their
           original SIM or have a second number from their country of origin.
           This is especially common for diaspora communities. When a real
           operator uploads their ERP table, numbers in these formats are valid
           and should not be flagged as errors.

    Email validation uses a simple RFC-5322-inspired regex. It is intentionally
    not exhaustive -- the goal is to catch obvious junk (missing @, no domain,
    placeholder text) rather than to be a full validator.

Extension points:
    - Add WhatsApp reachability check here when WhatsApp Business API is connected.
    - Add carrier lookup / HLR query when a telecom connector is available.
    - Add email deliverability check (MX lookup) when needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from models.customer import ContactValidity


# ---------------------------------------------------------------------------
# Accepted phone prefixes
# ---------------------------------------------------------------------------

# Turkey -- where customers currently live
_PREFIX_TURKEY: dict[str, str] = {
    "+90": "Turkey",
}

# Balkan home-country codes.
# Customers frequently retain a SIM from their country of origin,
# use it as a second number, or have it listed in older ERP entries.
_PREFIXES_BALKAN: dict[str, str] = {
    "+381": "Serbia",
    "+385": "Croatia",
    "+387": "Bosnia and Herzegovina",
    "+389": "North Macedonia",
    "+386": "Slovenia",
    "+382": "Montenegro",
    "+383": "Kosovo",
}

# Albania -- included because Albanian speakers consume Balkan content
# and are present in the Turkish diaspora community.
_PREFIX_ALBANIA: dict[str, str] = {
    "+355": "Albania",
}

# Combined lookup used by the validator.
# Sorted longest-first so that prefix matching is unambiguous
# (e.g. +387 must be checked before +38).
ACCEPTED_PHONE_PREFIXES: dict[str, str] = {
    **_PREFIX_TURKEY,
    **_PREFIXES_BALKAN,
    **_PREFIX_ALBANIA,
}

_SORTED_PREFIXES: list[str] = sorted(
    ACCEPTED_PHONE_PREFIXES.keys(), key=len, reverse=True
)

# Minimum digits after stripping the country code.
# E.g. +90 5321234567 has 10 digits after +90 → valid.
# Prevents single-digit or obviously truncated numbers from passing.
_MIN_LOCAL_DIGITS = 7


# ---------------------------------------------------------------------------
# Email pattern
# ---------------------------------------------------------------------------

# Matches user@domain.tld — intentionally permissive on the local part
# but strict enough to catch missing @, missing domain, or placeholder text.
_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _normalise_phone(raw: str) -> str:
    """
    Strip spaces, dashes, parentheses, and dots from a phone string.
    Returns the cleaned string for prefix matching.

    Examples:
        "+90 532 123 45 67" -> "+905321234567"
        "+381 (11) 123-456" -> "+38111123456"
    """
    return re.sub(r"[\s\-\(\)\.]", "", raw)


# ---------------------------------------------------------------------------
# Validation functions (pure, testable)
# ---------------------------------------------------------------------------

def validate_phone(raw: str | None) -> tuple[bool, str | None]:
    """
    Check whether a phone string is reachable for this provider.

    Args:
        raw: Raw phone string from the ERP record. May be None.

    Returns:
        (is_valid, issue_message)
        is_valid is True only when the number has a known prefix and
        sufficient digits after it.
        issue_message is None when valid, otherwise a short description.

    Examples:
        >>> validate_phone("+905321234567")
        (True, None)
        >>> validate_phone("+381111234567")
        (True, None)
        >>> validate_phone("00000")
        (False, "No recognised country prefix (+90, +381, +385, ...)")
        >>> validate_phone(None)
        (False, "Phone number is missing")
    """
    if not raw or not raw.strip():
        return False, "Phone number is missing"

    cleaned = _normalise_phone(raw.strip())

    if not cleaned.startswith("+"):
        return False, "No recognised country prefix (+90, +381, +385, ...)"

    matched_prefix: str | None = None
    for prefix in _SORTED_PREFIXES:
        if cleaned.startswith(prefix):
            matched_prefix = prefix
            break

    if matched_prefix is None:
        return False, "No recognised country prefix (+90, +381, +385, ...)"

    local_part = cleaned[len(matched_prefix):]
    if not local_part.isdigit():
        return False, "Non-digit characters after country code"

    if len(local_part) < _MIN_LOCAL_DIGITS:
        return False, f"Number too short ({len(local_part)} digits after country code)"

    return True, None


def validate_email(raw: str | None) -> tuple[bool, str | None]:
    """
    Check whether an email string looks deliverable.

    Uses a regex check only -- no DNS or MX lookup.

    Args:
        raw: Raw email string from the ERP record. May be None.

    Returns:
        (is_valid, issue_message)

    Examples:
        >>> validate_email("marko.petrovic@gmail.com")
        (True, None)
        >>> validate_email("noemail")
        (False, "Missing @ sign or invalid format")
        >>> validate_email(None)
        (False, "Email address is missing")
    """
    if not raw or not raw.strip():
        return False, "Email address is missing"

    cleaned = raw.strip().lower()

    # Catch obvious placeholder values before hitting the regex
    PLACEHOLDERS = {"n/a", "na", "none", "noemail", "no email", "-", "null"}
    if cleaned in PLACEHOLDERS:
        return False, "Placeholder value, not a real email address"

    if _EMAIL_RE.match(cleaned):
        return True, None

    if "@" not in cleaned:
        return False, "Missing @ sign or invalid format"

    local, _, domain = cleaned.partition("@")
    if not domain or "." not in domain:
        return False, "Missing or invalid domain"

    return False, "Invalid email format"


# ---------------------------------------------------------------------------
# ContactValidity builder
# ---------------------------------------------------------------------------

def build_contact_validity(phone: str | None, email: str | None) -> ContactValidity:
    """
    Run both validators and return a populated ContactValidity object.

    This is the function the ERP Scanner Agent calls per customer record.
    The returned object summarises what is usable and collects all issues
    in a human-readable list for the UI and downstream agents.

    Args:
        phone: Raw phone string from CustomerRecord.phone
        email: Raw email string from CustomerRecord.email

    Returns:
        ContactValidity with flags and a list of issue descriptions.

    Example:
        >>> cv = build_contact_validity("+905321234567", None)
        >>> cv.phone_valid, cv.email_valid, cv.has_any_contact
        (True, False, True)
        >>> cv.issues
        ['Email address is missing']
    """
    phone_ok, phone_issue = validate_phone(phone)
    email_ok, email_issue = validate_email(email)

    issues: list[str] = []
    if phone_issue:
        issues.append(phone_issue)
    if email_issue:
        issues.append(email_issue)

    return ContactValidity(
        phone_valid=phone_ok,
        email_valid=email_ok,
        has_any_contact=phone_ok or email_ok,
        issues=issues,
    )


def describe_prefix(phone: str | None) -> str | None:
    """
    Return the country name for the phone's prefix, or None if unrecognised.

    Useful for display in the UI (e.g. "Serbia (+381)") and for agents
    deciding which language to use in the outreach message.

    Example:
        >>> describe_prefix("+381111234567")
        'Serbia'
        >>> describe_prefix("+905321234567")
        'Turkey'
    """
    if not phone:
        return None
    cleaned = _normalise_phone(phone.strip())
    for prefix in _SORTED_PREFIXES:
        if cleaned.startswith(prefix):
            return ACCEPTED_PHONE_PREFIXES[prefix]
    return None

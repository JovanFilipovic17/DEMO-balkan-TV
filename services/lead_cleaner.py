"""
services/lead_cleaner.py

Deterministic cleaning and qualification logic for Tab 2.

Responsibilities:
    1. Validate phone and email contacts (reuses services/validator.py)
    2. Detect duplicate leads (by phone, then by email)
    3. Detect data quality issues (missing fields, all-caps, placeholders, etc.)
    4. Score each lead on completeness, contact quality, and engagement potential
    5. Assign an overall LeadQuality classification

No LLM calls here — all rule-based. Fast enough to run on 1000+ leads instantly.
AI enrichment (name normalization, deeper observations) is handled separately
by agents/lead_cleaner_agent.py for FIXABLE and POOR leads only.
"""

from __future__ import annotations

from models.lead import (
    IssueType,
    LeadIssue,
    LeadQuality,
    LeadScore,
    RawLead,
    Tab2Result,
)
from services.validator import validate_email, validate_phone

# ---------------------------------------------------------------------------
# Placeholder email blocklist (extends validator's list)
# ---------------------------------------------------------------------------

_PLACEHOLDER_EMAILS = {
    "test@test.com", "noemail@gmail.com", "none@none.com", "no@email.com",
    "aaa@aaa.com", "123@123.com", "x@x.com", "noreply@noreply.com",
    "email@email.com", "info@info.com", "admin@admin.com",
}

# Source engagement weights (higher = warmer lead)
_SOURCE_ENGAGEMENT = {
    "referral":      1.0,
    "event":         0.8,
    "whatsapp group": 0.7,
    "whatsapp":      0.7,
    "website":       0.6,
    "facebook":      0.5,
    "unknown":       0.2,
}


# ---------------------------------------------------------------------------
# Issue detection
# ---------------------------------------------------------------------------

def _detect_issues(lead: RawLead, phone_ok: bool, email_ok: bool) -> list[LeadIssue]:
    issues: list[LeadIssue] = []

    # Missing name
    if not lead.full_name or not lead.full_name.strip():
        issues.append(LeadIssue(
            issue_type=IssueType.MISSING_NAME,
            field="full_name",
            description="Name is missing — lead cannot be properly addressed.",
            severity="error",
            auto_fixable=False,
        ))
    elif lead.full_name == lead.full_name.upper() and len(lead.full_name) > 2:
        issues.append(LeadIssue(
            issue_type=IssueType.NAME_ALL_CAPS,
            field="full_name",
            description=f"Name is all-caps: '{lead.full_name}' — likely a data entry issue.",
            severity="warning",
            auto_fixable=True,
        ))

    # Phone issues
    if not lead.phone:
        if not lead.email:
            issues.append(LeadIssue(
                issue_type=IssueType.MISSING_CONTACT,
                field="phone/email",
                description="No phone or email — lead is unreachable.",
                severity="error",
                auto_fixable=False,
            ))
    elif not phone_ok:
        issues.append(LeadIssue(
            issue_type=IssueType.INVALID_PHONE,
            field="phone",
            description=f"Phone '{lead.phone}' is not a valid accepted format.",
            severity="error",
            auto_fixable=False,
        ))
    elif lead.phone and len(set(lead.phone.replace("+", "").replace("9", "").replace("0", ""))) <= 1:
        issues.append(LeadIssue(
            issue_type=IssueType.SUSPICIOUS_PHONE,
            field="phone",
            description=f"Phone '{lead.phone}' looks like a fake number (repeated digits).",
            severity="warning",
            auto_fixable=False,
        ))

    # Email issues
    if lead.email:
        if lead.email.lower() in _PLACEHOLDER_EMAILS:
            issues.append(LeadIssue(
                issue_type=IssueType.PLACEHOLDER_EMAIL,
                field="email",
                description=f"Email '{lead.email}' is a known placeholder.",
                severity="warning",
                auto_fixable=False,
            ))
        elif not email_ok:
            issues.append(LeadIssue(
                issue_type=IssueType.INVALID_EMAIL,
                field="email",
                description=f"Email '{lead.email}' has an invalid format.",
                severity="warning",
                auto_fixable=False,
            ))

    # Missing city
    if not lead.city:
        issues.append(LeadIssue(
            issue_type=IssueType.MISSING_CITY,
            field="city",
            description="City is missing — useful for regional outreach routing.",
            severity="info",  # not a blocker
            auto_fixable=False,
        ))

    return issues


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score(lead: RawLead, phone_ok: bool, email_ok: bool) -> LeadScore:
    # Completeness: name, phone, email, city, language = 5 key fields
    fields = [lead.full_name, lead.phone, lead.email, lead.city, lead.language]
    completeness = sum(1 for f in fields if f) / len(fields)

    # Contact quality: valid phone is most important, valid email secondary
    if phone_ok:
        contact_quality = 1.0 if email_ok else 0.75
    elif email_ok:
        contact_quality = 0.4
    else:
        contact_quality = 0.0

    # Engagement: based on source
    source_key = (lead.source or "unknown").lower().strip()
    engagement = _SOURCE_ENGAGEMENT.get(source_key, 0.3)

    overall = (completeness * 0.35) + (contact_quality * 0.50) + (engagement * 0.15)

    return LeadScore(
        completeness=round(completeness, 2),
        contact_quality=round(contact_quality, 2),
        engagement=round(engagement, 2),
        overall=round(overall, 2),
    )


# ---------------------------------------------------------------------------
# Quality classification
# ---------------------------------------------------------------------------

def _classify(
    issues: list[LeadIssue],
    score: LeadScore,
    is_duplicate: bool,
) -> LeadQuality:
    if is_duplicate:
        return LeadQuality.REJECT

    error_count   = sum(1 for i in issues if i.severity == "error")
    warning_count = sum(1 for i in issues if i.severity == "warning")

    if error_count >= 2 or score.overall < 0.25:
        return LeadQuality.POOR
    if error_count == 1 or warning_count >= 2 or score.overall < 0.55:
        return LeadQuality.FIXABLE
    if warning_count == 1 or score.overall < 0.80:
        return LeadQuality.FIXABLE
    return LeadQuality.GOOD


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def clean_leads(leads: list[RawLead]) -> list[Tab2Result]:
    """
    Run the full deterministic cleaning pipeline over a list of raw leads.

    Steps:
        1. Validate each lead's contact info
        2. Detect duplicates across the batch (phone first, then email)
        3. Detect per-record issues
        4. Score and classify each lead

    Returns Tab2Result objects in the same order as input.
    Duplicate detection is O(n) using phone/email hash sets.
    """
    # ── Pass 1: validate and build initial results ────────────────────────
    results: list[Tab2Result] = []
    seen_phones: dict[str, int] = {}   # normalized phone → first row_index
    seen_emails: dict[str, int] = {}   # lowered email    → first row_index

    # Normalise phone for dedup (strip spaces, dashes)
    def _norm_phone(p: str) -> str:
        return p.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")

    for lead in leads:
        phone_ok, _ = validate_phone(lead.phone) if lead.phone else (False, None)
        # Treat placeholder emails as invalid for dedup purposes
        if lead.email and lead.email.lower() in _PLACEHOLDER_EMAILS:
            email_ok = False
        else:
            email_ok, _ = validate_email(lead.email) if lead.email else (False, None)

        issues = _detect_issues(lead, phone_ok, email_ok)
        score  = _score(lead, phone_ok, email_ok)

        results.append(Tab2Result(
            raw=lead,
            quality=LeadQuality.GOOD,   # placeholder — set below
            issues=issues,
            score=score,
            phone_valid=phone_ok,
            email_valid=email_ok,
        ))

    # ── Pass 2: duplicate detection ───────────────────────────────────────
    for result in results:
        lead = result.raw

        # Phone duplicate check (only valid phones)
        if result.phone_valid and lead.phone:
            norm = _norm_phone(lead.phone)
            if norm in seen_phones:
                orig_row = seen_phones[norm]
                result.issues.append(LeadIssue(
                    issue_type=IssueType.DUPLICATE_PHONE,
                    field="phone",
                    description=(
                        f"Phone {lead.phone} already seen at row {orig_row + 1}. "
                        f"Likely the same person entered twice."
                    ),
                    severity="error",
                    auto_fixable=False,
                ))
                object.__setattr__(result, "is_duplicate",     True)
                object.__setattr__(result, "duplicate_of_row", orig_row)
                object.__setattr__(result, "duplicate_reason", "phone")
            else:
                seen_phones[norm] = lead.row_index

        # Email duplicate check (only real emails)
        if result.email_valid and lead.email:
            norm_email = lead.email.lower().strip()
            if norm_email in seen_emails:
                orig_row = seen_emails[norm_email]
                if not result.is_duplicate:   # don't double-flag
                    result.issues.append(LeadIssue(
                        issue_type=IssueType.DUPLICATE_EMAIL,
                        field="email",
                        description=(
                            f"Email {lead.email} already seen at row {orig_row + 1}."
                        ),
                        severity="error",
                        auto_fixable=False,
                    ))
                    object.__setattr__(result, "is_duplicate",     True)
                    object.__setattr__(result, "duplicate_of_row", orig_row)
                    object.__setattr__(result, "duplicate_reason", "email")
            else:
                seen_emails[norm_email] = lead.row_index

    # ── Pass 3: final quality classification ──────────────────────────────
    for result in results:
        quality = _classify(result.issues, result.score, result.is_duplicate)
        object.__setattr__(result, "quality", quality)

    return results

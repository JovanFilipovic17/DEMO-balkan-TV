"""
models/lead.py

Data shapes for Tab 2: Lead Database Cleaner & Qualifier.

A "lead" is someone who expressed interest but is not yet a subscriber.
Leads come from Facebook groups, events, referrals, WhatsApp communities, etc.
Data quality is typically poor — duplicates, all-caps names, invalid phones.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class LeadQuality(str, Enum):
    GOOD    = "good"     # ready to import to CRM
    FIXABLE = "fixable"  # minor issues, auto-fixed or operator review
    POOR    = "poor"     # major issues, needs manual work
    REJECT  = "reject"   # duplicate or completely unusable


class IssueType(str, Enum):
    DUPLICATE_PHONE   = "duplicate_phone"
    DUPLICATE_EMAIL   = "duplicate_email"
    INVALID_PHONE     = "invalid_phone"
    INVALID_EMAIL     = "invalid_email"
    MISSING_NAME      = "missing_name"
    MISSING_CONTACT   = "missing_contact"
    NAME_ALL_CAPS     = "name_all_caps"
    PLACEHOLDER_EMAIL = "placeholder_email"
    SUSPICIOUS_PHONE  = "suspicious_phone"
    MISSING_CITY      = "missing_city"


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------

class RawLead(BaseModel):
    """One row from the imported lead CSV, minimally parsed."""
    row_index: int
    full_name:  Optional[str] = None
    phone:      Optional[str] = None
    email:      Optional[str] = None
    city:       Optional[str] = None
    language:   Optional[str] = None
    source:     Optional[str] = None   # "Facebook", "Referral", "Event", etc.
    notes:      Optional[str] = None


class LeadIssue(BaseModel):
    """A specific problem found in a lead record."""
    issue_type:   IssueType
    field:        str
    description:  str
    severity:     str   # "error" | "warning"
    auto_fixable: bool = False


class LeadScore(BaseModel):
    """Data quality + conversion potential scores, each 0.0–1.0."""
    completeness:    float   # % of key fields present
    contact_quality: float   # valid phone/email present
    engagement:      float   # source quality (referral > event > facebook > unknown)
    overall:         float   # weighted average


class Tab2Result(BaseModel):
    """Full result for one lead after the cleaning + qualification pipeline."""
    raw:              RawLead
    quality:          LeadQuality
    issues:           list[LeadIssue]
    score:            LeadScore

    # Duplicate detection
    is_duplicate:     bool           = False
    duplicate_of_row: Optional[int]  = None   # row_index of the original record
    duplicate_reason: Optional[str]  = None   # "phone" | "email"

    # Contact validity
    phone_valid:  bool = False
    email_valid:  bool = False

    # AI outputs
    normalized_name: Optional[str] = None   # AI-suggested corrected name
    ai_notes:        list[str]     = []

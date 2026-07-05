"""
services/scorer.py

Deterministic risk and priority scoring for Tab 1: Existing Customers / ERP Follow-up.

Responsibilities:
    - Compute payment_risk_score    [0, 1]  -- likelihood customer won't pay
    - Compute churn_risk_score      [0, 1]  -- likelihood customer will leave
    - Compute contact_quality_score [0, 1]  -- how reachable the customer is
    - Derive overall_priority       (HIGH / MEDIUM / LOW / SKIP)
    - Select recommended_channel    (PHONE / EMAIL / WHATSAPP / NONE)
    - Produce next_action           (short human-readable instruction)

Design principles:
    - Zero LLM calls. All logic is rule-based and fully testable.
    - Scores are normalised to [0, 1] so the UI can render them as progress
      bars or colour badges without any additional mapping.
    - Scoring rules are isolated in small, named functions so individual
      factors can be unit-tested and tuned independently.
    - The single public entry point is `score_customer()`, which takes a
      CustomerSummary and returns a RiskEvaluation.

Extension points:
    - Payment history patterns (number of late payments) can be added
      once the ERP connector exposes them.
    - LTV (lifetime value) weighting can be layered in without changing
      the function signatures.
"""

from __future__ import annotations

from models.customer import (
    ContactChannel,
    ContactValidity,
    CustomerStatus,
    CustomerSummary,
    Priority,
    RiskEvaluation,
)


# ---------------------------------------------------------------------------
# Scoring constants
# (collected here so a product owner can tune them without touching logic)
# ---------------------------------------------------------------------------

# Days-overdue thresholds
_OVERDUE_LOW    =  14   # grace period -- low additional risk
_OVERDUE_MED    =  30   # one missed billing cycle
_OVERDUE_HIGH   =  60   # two missed cycles -- high payment risk
_OVERDUE_CRIT   = 120   # four+ months -- near-certain churn

# Days-until-expiry thresholds
_EXPIRY_URGENT  =   7   # expires this week
_EXPIRY_WARN    =  14   # expires within two weeks

# Monthly plan price used to normalise outstanding balance risk.
# Matches the single plan price defined in services/demo_data.py.
_PLAN_PRICE     = 39.0


# ---------------------------------------------------------------------------
# Individual scoring factors
# ---------------------------------------------------------------------------

def _score_payment_risk(
    status: CustomerStatus,
    days_overdue: int,
    outstanding_balance: float,
    days_until_expiry: int | None,
) -> float:
    """
    Return a payment risk score in [0, 1].

    Logic:
        - SKIP / ACTIVE with no balance and expiry far out → near zero
        - Expiring soon → small bump (renewal not yet confirmed)
        - OVERDUE: increases linearly with days overdue and balance depth
        - EXPIRED: high baseline, capped below 1.0 so churn score can add signal
        - SUSPENDED: high, treated like long overdue
        - UNKNOWN: moderate (we simply don't know)
    """
    if status == CustomerStatus.ACTIVE:
        if outstanding_balance == 0 and (days_until_expiry is None or days_until_expiry > _EXPIRY_WARN):
            return 0.05  # healthy active customer

        if days_until_expiry is not None and days_until_expiry <= _EXPIRY_URGENT:
            return 0.30  # expiring very soon -- renewal not confirmed
        if days_until_expiry is not None and days_until_expiry <= _EXPIRY_WARN:
            return 0.20  # expiring soon

        if outstanding_balance > 0:
            # Small balance on an active record -- partial payment or timing issue
            balance_months = outstanding_balance / _PLAN_PRICE
            return min(0.40, 0.10 + balance_months * 0.10)

        return 0.10

    if status == CustomerStatus.OVERDUE:
        # Base risk grows with time overdue
        if days_overdue <= _OVERDUE_LOW:
            base = 0.45
        elif days_overdue <= _OVERDUE_MED:
            base = 0.55
        elif days_overdue <= _OVERDUE_HIGH:
            base = 0.70
        else:
            base = 0.82

        # Balance depth adds up to 0.10 on top
        balance_months = min(outstanding_balance / _PLAN_PRICE, 3)
        return min(0.92, base + balance_months * 0.033)

    if status == CustomerStatus.EXPIRED:
        return 0.88

    if status == CustomerStatus.SUSPENDED:
        return 0.78

    # UNKNOWN
    return 0.40


def _score_churn_risk(
    status: CustomerStatus,
    days_overdue: int,
    days_until_expiry: int | None,
) -> float:
    """
    Return a churn risk score in [0, 1].

    Churn risk is distinct from payment risk:
        - A customer can be overdue but fully intend to pay (high payment risk,
          lower churn risk).
        - A customer who has been expired for 6+ months has effectively churned
          (very high churn risk).
    """
    if status == CustomerStatus.ACTIVE:
        if days_until_expiry is None:
            return 0.20
        if days_until_expiry <= _EXPIRY_URGENT:
            return 0.45
        if days_until_expiry <= _EXPIRY_WARN:
            return 0.30
        return 0.10

    if status == CustomerStatus.OVERDUE:
        if days_overdue <= _OVERDUE_LOW:
            return 0.35
        if days_overdue <= _OVERDUE_MED:
            return 0.50
        if days_overdue <= _OVERDUE_HIGH:
            return 0.65
        return 0.80

    if status == CustomerStatus.EXPIRED:
        # Longer lapse → harder to win back
        if days_overdue <= 180:
            return 0.82
        return 0.93

    if status == CustomerStatus.SUSPENDED:
        return 0.70

    # UNKNOWN
    return 0.35


def _score_contact_quality(contact: ContactValidity) -> float:
    """
    Return a contact quality score in [0, 1].

    Both channels valid → 1.0  (can reach by phone or email)
    Phone only          → 0.65 (direct, but no email fallback)
    Email only          → 0.50 (lower response rate for payment follow-up)
    Neither             → 0.0  (unreachable -- contact cleanup required first)
    """
    if contact.phone_valid and contact.email_valid:
        return 1.0
    if contact.phone_valid:
        return 0.65
    if contact.email_valid:
        return 0.50
    return 0.0


# ---------------------------------------------------------------------------
# Priority derivation
# ---------------------------------------------------------------------------

def _derive_priority(
    payment_risk: float,
    churn_risk: float,
    contact_quality: float,
    status: CustomerStatus,
    days_until_expiry: int | None,
) -> Priority:
    """
    Map scores and status to an overall contact priority.

    Rules (evaluated top-to-bottom, first match wins):

        SKIP   -- healthy active customer, no balance, expiry far out,
                  no immediate action needed.
        HIGH   -- overdue/expired/suspended with reachable contact, or
                  very high combined risk.
        MEDIUM -- moderate risk, or active customer expiring soon.
        LOW    -- low risk active customer, or unreachable (contact cleanup
                  should still be flagged, just not urgent).
    """
    # Healthy active → skip outreach
    if (
        status == CustomerStatus.ACTIVE
        and payment_risk <= 0.12
        and churn_risk <= 0.15
        and contact_quality > 0
    ):
        return Priority.SKIP

    # Unreachable: still flag but de-prioritise -- agent can't do much
    if contact_quality == 0.0:
        if payment_risk >= 0.60:
            return Priority.MEDIUM   # needs data cleanup urgently
        return Priority.LOW

    # High risk: must contact now
    if payment_risk >= 0.65 or churn_risk >= 0.75:
        return Priority.HIGH

    # Expiring very soon: proactive renewal call
    if (
        status == CustomerStatus.ACTIVE
        and days_until_expiry is not None
        and days_until_expiry <= _EXPIRY_URGENT
    ):
        return Priority.HIGH

    # Moderate risk or expiring in 2 weeks
    if payment_risk >= 0.35 or churn_risk >= 0.40:
        return Priority.MEDIUM

    if (
        status == CustomerStatus.ACTIVE
        and days_until_expiry is not None
        and days_until_expiry <= _EXPIRY_WARN
    ):
        return Priority.MEDIUM

    return Priority.LOW


# ---------------------------------------------------------------------------
# Channel and action selection
# ---------------------------------------------------------------------------

def _select_channel(contact: ContactValidity) -> ContactChannel:
    """
    Choose the best outreach channel based on what contact data is available.

    Phone is preferred for this market (Balkan diaspora in Turkey) because
    WhatsApp and voice calls have higher engagement than email for payment
    follow-up. Email is the fallback when phone is missing.

    Extension point: when WhatsApp Business API is connected, promote
    WHATSAPP above PHONE here for customers who prefer it (check notes field).
    """
    if contact.phone_valid:
        return ContactChannel.PHONE
    if contact.email_valid:
        return ContactChannel.EMAIL
    return ContactChannel.NONE


def _select_next_action(
    priority: Priority,
    status: CustomerStatus,
    channel: ContactChannel,
    days_until_expiry: int | None,
    days_overdue: int,
) -> str:
    """
    Return a concise human-readable action instruction for the operator.

    These strings appear in the UI action column and in the agent's reasoning.
    They should be short enough to scan at a glance.
    """
    if channel == ContactChannel.NONE:
        return "Update contact information -- customer is currently unreachable"

    if priority == Priority.SKIP:
        return "No action needed -- customer is active and in good standing"

    if status == CustomerStatus.EXPIRED:
        if days_overdue > 180:
            return "Send win-back offer -- customer lapsed 6+ months ago"
        return "Call and offer renewal -- customer recently expired"

    if status == CustomerStatus.SUSPENDED:
        return "Call to clarify suspension and arrange payment"

    if status == CustomerStatus.OVERDUE:
        if days_overdue > _OVERDUE_HIGH:
            return f"Urgent call -- {days_overdue} days overdue, risk of permanent churn"
        if days_overdue > _OVERDUE_MED:
            return f"Call to arrange payment -- {days_overdue} days overdue"
        return f"Send payment reminder -- {days_overdue} days overdue"

    if status == CustomerStatus.ACTIVE and days_until_expiry is not None:
        if days_until_expiry <= _EXPIRY_URGENT:
            return f"Call to confirm renewal -- expires in {days_until_expiry} day(s)"
        if days_until_expiry <= _EXPIRY_WARN:
            return f"Send renewal reminder -- expires in {days_until_expiry} days"

    if status == CustomerStatus.UNKNOWN:
        return "Verify subscription status and contact details in ERP"

    return "Send friendly check-in message"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def score_customer(summary: CustomerSummary) -> RiskEvaluation:
    """
    Compute a full RiskEvaluation for one customer from their CustomerSummary.

    This is the single function the Customer Risk Evaluator Agent calls.
    All scoring is deterministic -- no LLM involved at this stage.

    Args:
        summary: CustomerSummary produced by the ERP Scanner Agent.

    Returns:
        RiskEvaluation with scores, priority, channel, action, and notes.

    Example:
        >>> from services.erp_scanner import scan_customer
        >>> from services.scorer import score_customer
        >>> summary = scan_customer(record)
        >>> evaluation = score_customer(summary)
        >>> evaluation.overall_priority
        <Priority.HIGH: 'high'>
    """
    record = summary.record

    payment_risk = _score_payment_risk(
        status=summary.status,
        days_overdue=summary.days_overdue,
        outstanding_balance=record.outstanding_balance,
        days_until_expiry=summary.days_until_expiry,
    )

    churn_risk = _score_churn_risk(
        status=summary.status,
        days_overdue=summary.days_overdue,
        days_until_expiry=summary.days_until_expiry,
    )

    contact_quality = _score_contact_quality(summary.contact)

    priority = _derive_priority(
        payment_risk=payment_risk,
        churn_risk=churn_risk,
        contact_quality=contact_quality,
        status=summary.status,
        days_until_expiry=summary.days_until_expiry,
    )

    channel = _select_channel(summary.contact)

    next_action = _select_next_action(
        priority=priority,
        status=summary.status,
        channel=channel,
        days_until_expiry=summary.days_until_expiry,
        days_overdue=summary.days_overdue,
    )

    # Build human-readable notes explaining the scores for agents / UI
    notes: list[str] = []
    if summary.days_overdue > 0:
        notes.append(f"{summary.days_overdue} days overdue.")
    if record.outstanding_balance > 0:
        notes.append(f"Outstanding balance: ${record.outstanding_balance:.2f}.")
    if summary.days_until_expiry is not None and summary.days_until_expiry <= _EXPIRY_WARN:
        notes.append(f"Subscription expires in {summary.days_until_expiry} day(s).")
    if not summary.contact.has_any_contact:
        notes.append("No valid contact channel available.")
    if summary.contact.issues:
        notes.extend(summary.contact.issues)

    return RiskEvaluation(
        customer_id=record.customer_id,
        payment_risk_score=round(payment_risk, 3),
        churn_risk_score=round(churn_risk, 3),
        contact_quality_score=round(contact_quality, 3),
        overall_priority=priority,
        recommended_channel=channel,
        next_action=next_action,
        evaluator_notes=notes,
    )

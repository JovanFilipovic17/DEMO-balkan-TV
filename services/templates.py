"""
services/templates.py

Message template registry for Tab 1: Existing Customers / ERP Follow-up.

The provider owns the four message bodies below.
The system owns the slot definitions, rendering logic, and template selection.

How it works:
    1. Each template has a body string with named slots: {name}, {days_overdue}, etc.
    2. `render_template()` fills those slots with real customer data.
    3. `select_template()` picks the right template key from a CustomerSummary.
    4. The Follow-up Delivery Agent may then call an LLM to propose a small
       improvement to the rendered message (stored in FollowUpAction.suggested_improvement).
       The original rendered message is always preserved -- the suggestion is advisory.

To use your real messages:
    Replace the `body` strings in the TEMPLATE_REGISTRY dict below.
    The slot names (e.g. {name}, {balance_usd}) must be kept exactly as shown,
    or updated in `_build_slot_context()` to match your naming.
    Everything else -- selection, rendering, agent review -- stays the same.

Supported slots (all optional in the body -- unused ones are simply not inserted):
    {name}              Full name of the customer
    {first_name}        First word of the full name
    {plan}              Subscription plan name  (e.g. "Balkan TV")
    {price_usd}         Monthly plan price      (e.g. "39")
    {balance_usd}       Outstanding balance     (e.g. "78")
    {days_overdue}      Days since subscription ended
    {days_until_expiry} Days remaining before subscription ends
    {city}              Customer city / region
    {language}          Customer language code  (e.g. "sr")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from models.customer import CustomerStatus, CustomerSummary, TemplateKey
from services.demo_data import PLAN_NAME, PLAN_PRICE_USD


# ---------------------------------------------------------------------------
# Template data structure
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MessageTemplate:
    """
    One provider-approved message template.

    Attributes:
        key:         Unique identifier matching TemplateKey enum.
        label:       Human-readable name shown in the UI.
        situation:   One-line description of when this template is used.
        subject:     Email subject line (optional -- leave None for SMS/WhatsApp).
        body:        Message body with {slot} placeholders.
                     *** REPLACE THIS WITH YOUR ACTUAL MESSAGE TEXT ***
        notes:       Guidance for the operator or the AI review agent.
    """
    key: TemplateKey
    label: str
    situation: str
    body: str
    subject: Optional[str] = None
    notes: str = ""


# ---------------------------------------------------------------------------
# Template registry
# ---------------------------------------------------------------------------
# Each entry below is one of the four provider-approved message templates.
#
# *** REPLACE THE `body` (AND OPTIONALLY `subject`) STRINGS WITH YOUR ACTUAL
#     MESSAGES. ALL OTHER FIELDS AND THE SLOT NAMES CAN BE KEPT AS-IS. ***
#
# The demo bodies below are realistic placeholders written in English.
# In production your messages may be in Serbian, Croatian, Bosnian, etc.
# Language selection can be layered in by the Follow-up Delivery Agent.
# ---------------------------------------------------------------------------

TEMPLATE_REGISTRY: dict[TemplateKey, MessageTemplate] = {

    TemplateKey.PAYMENT_REMINDER: MessageTemplate(
        key=TemplateKey.PAYMENT_REMINDER,
        label="Payment Reminder",
        situation="Customer is overdue (1–60 days). First or second contact attempt.",
        subject="Your Balkan TV subscription – payment reminder",
        # ── REPLACE BELOW WITH YOUR ACTUAL MESSAGE ───────────────────────
        body=(
            "Hello {first_name},\n\n"
            "We noticed that your {plan} subscription has been inactive for {days_overdue} days "
            "and there is an outstanding balance of ${balance_usd}.\n\n"
            "We'd love to keep you connected to all your favourite Balkan channels. "
            "If you've already arranged payment, please ignore this message.\n\n"
            "To renew or ask any questions, just reply to this message or give us a call — "
            "we're happy to help.\n\n"
            "Warm regards,\n"
            "Balkan TV Team"
        ),
        # ── END OF PLACEHOLDER ────────────────────────────────────────────
        notes=(
            "Tone: friendly, not aggressive. "
            "Do not mention legal consequences or threats of any kind. "
            "If the customer has already paid after the ERP snapshot, the operator must skip this message."
        ),
    ),

    TemplateKey.EXPIRY_WARNING: MessageTemplate(
        key=TemplateKey.EXPIRY_WARNING,
        label="Expiry Warning",
        situation="Active customer whose subscription expires in ≤14 days.",
        subject="Your Balkan TV subscription expires soon",
        # ── REPLACE BELOW WITH YOUR ACTUAL MESSAGE ───────────────────────
        body=(
            "Hello {first_name},\n\n"
            "Just a quick heads-up: your {plan} subscription will expire in {days_until_expiry} day(s).\n\n"
            "To continue enjoying uninterrupted access to Balkan TV, "
            "please renew before your expiry date. Renewal is ${price_usd}/month.\n\n"
            "Have questions or need help renewing? Reply here or call us — we're always around.\n\n"
            "Warm regards,\n"
            "Balkan TV Team"
        ),
        # ── END OF PLACEHOLDER ────────────────────────────────────────────
        notes=(
            "Send 7–14 days before expiry. "
            "Do not send if the customer has already renewed in the current cycle."
        ),
    ),

    TemplateKey.WIN_BACK: MessageTemplate(
        key=TemplateKey.WIN_BACK,
        label="Win-Back",
        situation="Customer subscription expired 30+ days ago. Re-engagement attempt.",
        subject="We miss you – come back to Balkan TV",
        # ── REPLACE BELOW WITH YOUR ACTUAL MESSAGE ───────────────────────
        body=(
            "Hello {first_name},\n\n"
            "It's been a while since we've seen you on Balkan TV, and we wanted to reach out.\n\n"
            "We know life gets busy — if there's anything we can do to make your experience "
            "better, or if you have any questions about reactivating your subscription, "
            "we'd love to hear from you.\n\n"
            "Reactivation is easy and takes just a minute. Your plan is ${price_usd}/month "
            "and everything you love about Balkan TV is still here waiting for you.\n\n"
            "Warm regards,\n"
            "Balkan TV Team"
        ),
        # ── END OF PLACEHOLDER ────────────────────────────────────────────
        notes=(
            "Tone: warm and welcoming, not pushy. "
            "Do not mention outstanding balance in the opening — address it only if the customer asks. "
            "Best used within 6 months of lapse; after 12 months consider a different approach."
        ),
    ),

    TemplateKey.GENERAL_CHECK_IN: MessageTemplate(
        key=TemplateKey.GENERAL_CHECK_IN,
        label="General Check-in",
        situation="Low-urgency outreach. Active customer, good standing, relationship touch.",
        subject=None,   # SMS / WhatsApp only — no email subject needed
        # ── REPLACE BELOW WITH YOUR ACTUAL MESSAGE ───────────────────────
        body=(
            "Hello {first_name},\n\n"
            "Hope you're enjoying Balkan TV! We just wanted to check in and see "
            "if there's anything we can help you with.\n\n"
            "If you have any questions about your subscription or want to know "
            "about anything new, don't hesitate to reach out.\n\n"
            "Thanks for being with us!\n\n"
            "Balkan TV Team"
        ),
        # ── END OF PLACEHOLDER ────────────────────────────────────────────
        notes=(
            "Use sparingly — max once per quarter per customer. "
            "Not suitable for customers with outstanding balances."
        ),
    ),
}


# ---------------------------------------------------------------------------
# Slot context builder
# ---------------------------------------------------------------------------

def _build_slot_context(summary: CustomerSummary) -> dict[str, str]:
    """
    Build the {slot} → value mapping for a given customer.

    All values are strings so format() never raises a TypeError.
    Missing or unknown values fall back to a safe empty string
    so the template still renders even with incomplete ERP data.
    """
    record = summary.record
    name = record.full_name or ""
    first_name = name.split()[0] if name else ""

    return {
        "name": name,
        "first_name": first_name,
        "plan": record.subscription_plan or PLAN_NAME,
        "price_usd": str(int(PLAN_PRICE_USD)),
        "balance_usd": str(int(record.outstanding_balance)) if record.outstanding_balance else "0",
        "days_overdue": str(summary.days_overdue) if summary.days_overdue else "0",
        "days_until_expiry": str(summary.days_until_expiry) if summary.days_until_expiry is not None else "",
        "city": record.country or "",
        "language": record.language or "",
    }


# ---------------------------------------------------------------------------
# Template selection
# ---------------------------------------------------------------------------

def select_template_key(summary: CustomerSummary) -> TemplateKey:
    """
    Choose the most appropriate template for a customer based on their status
    and subscription timing.

    Selection rules (first match wins):
        EXPIRY_WARNING  -- active and expiring within 14 days
        PAYMENT_REMINDER-- overdue or has outstanding balance
        WIN_BACK        -- expired (lapsed subscription)
        GENERAL_CHECK_IN-- everything else (healthy active, unknown)

    The Follow-up Delivery Agent may override this selection if its
    reasoning suggests a different template is more appropriate.
    """
    status = summary.status
    days_until = summary.days_until_expiry

    if status == CustomerStatus.ACTIVE and days_until is not None and days_until <= 14:
        return TemplateKey.EXPIRY_WARNING

    if status == CustomerStatus.OVERDUE:
        return TemplateKey.PAYMENT_REMINDER

    if record := summary.record:
        if record.outstanding_balance > 0 and status != CustomerStatus.EXPIRED:
            return TemplateKey.PAYMENT_REMINDER

    if status == CustomerStatus.EXPIRED:
        return TemplateKey.WIN_BACK

    if status == CustomerStatus.SUSPENDED:
        return TemplateKey.PAYMENT_REMINDER

    return TemplateKey.GENERAL_CHECK_IN


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_template(
    key: TemplateKey,
    summary: CustomerSummary,
) -> tuple[str, Optional[str]]:
    """
    Render the template body and subject for a customer.

    Args:
        key:     Template to use (from select_template_key or agent override).
        summary: CustomerSummary with customer data for slot filling.

    Returns:
        (rendered_body, rendered_subject)
        rendered_subject is None for templates without a subject (SMS/WhatsApp).

    Raises:
        KeyError: If `key` is not in TEMPLATE_REGISTRY (should never happen
                  unless a new TemplateKey was added without a matching entry).
    """
    template = TEMPLATE_REGISTRY[key]
    ctx = _build_slot_context(summary)

    body = template.body.format_map(_SafeFormat(ctx))
    subject = template.subject.format_map(_SafeFormat(ctx)) if template.subject else None

    return body, subject


class _SafeFormat(dict):  # type: ignore[type-arg]
    """
    dict subclass that returns the original {key} placeholder for any
    missing key instead of raising a KeyError.

    This makes rendering safe even when the provider's message body
    uses a slot name that is not in our context dict.
    """
    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"


# ---------------------------------------------------------------------------
# Convenience accessor
# ---------------------------------------------------------------------------

def get_template(key: TemplateKey) -> MessageTemplate:
    """Return the MessageTemplate for a given key. Useful for UI display."""
    return TEMPLATE_REGISTRY[key]


def all_templates() -> list[MessageTemplate]:
    """Return all four templates in a stable order. Useful for UI listing."""
    return [TEMPLATE_REGISTRY[k] for k in TemplateKey]

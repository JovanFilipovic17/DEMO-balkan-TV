"""
models/customer.py

Single source of truth for all data shapes used in Tab 1: Existing Customers / ERP Follow-up.

Every agent, service, and UI component imports from here.
Keeping models in one place ensures that schema changes propagate automatically
and that the code stays readable for anyone auditing this as a portfolio project.

Data flow:
    CustomerRecord          (raw ERP input)
        -> CustomerSummary  (ERP Scanner Agent output)
        -> RiskEvaluation   (Customer Risk Evaluator Agent output)
        -> FollowUpAction   (Follow-up Delivery Agent output)
        -> ReviewResult     (optional Claude Review Agent output)
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class CustomerStatus(str, Enum):
    """Billing / subscription lifecycle state derived from ERP data."""

    ACTIVE = "active"
    OVERDUE = "overdue"
    EXPIRED = "expired"
    SUSPENDED = "suspended"
    UNKNOWN = "unknown"


class ContactChannel(str, Enum):
    """
    Preferred or recommended outreach channel.

    Extension point: add WHATSAPP, EMAIL, CRM_TASK, etc. when
    WhatsApp Business API or email integrations are wired in.
    """

    PHONE = "phone"
    EMAIL = "email"
    WHATSAPP = "whatsapp"   # reserved for future WhatsApp Business API
    NONE = "none"            # no valid contact found


class Priority(str, Enum):
    """Overall contact urgency produced by the Risk Evaluator Agent."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    SKIP = "skip"            # customer should not be contacted (e.g. recently paid)


class TemplateKey(str, Enum):
    """
    The four approved message templates available to the Follow-up Delivery Agent.

    Template content lives in services/templates.py.
    Adding a new template only requires adding a key here and a matching
    entry in the template registry -- no agent code changes needed.
    """

    PAYMENT_REMINDER = "payment_reminder"    # overdue, first notice
    EXPIRY_WARNING = "expiry_warning"        # subscription expiring soon
    WIN_BACK = "win_back"                    # expired / churned
    GENERAL_CHECK_IN = "general_check_in"   # low urgency, relationship touch


# ---------------------------------------------------------------------------
# Raw ERP input
# ---------------------------------------------------------------------------


class CustomerRecord(BaseModel):
    """
    One row from the ERP export / uploaded CSV.

    All fields are typed loosely to tolerate dirty real-world data.
    Validation and normalisation happen in services/validator.py,
    not here -- this model only enforces basic type contracts.

    Note: email is stored as a raw Optional[str] (not EmailStr) because ERP
    exports routinely contain malformed or placeholder values. The validator
    service decides whether the stored string is actually usable.
    """

    customer_id: str = Field(..., description="Unique identifier from the ERP system.")
    full_name: str = Field(..., description="Customer full name.")
    phone: Optional[str] = Field(None, description="Primary phone number. May be missing or malformed.")
    email: Optional[str] = Field(None, description="Raw email string from ERP. May be missing or malformed. Validated by services/validator.py.")
    country: Optional[str] = Field(None, description="Country of residence (diaspora location).")
    language: Optional[str] = Field(
        None,
        description="Preferred language for outreach (e.g. 'sr', 'hr', 'bs', 'en').",
    )
    subscription_plan: Optional[str] = Field(None, description="Plan name from ERP (e.g. 'Balkan Basic', 'Premium').")
    subscription_start: Optional[date] = Field(None, description="Date the current subscription period began.")
    subscription_end: Optional[date] = Field(None, description="Date the current subscription period ends.")
    last_payment_date: Optional[date] = Field(None, description="Date of the most recent successful payment.")
    last_payment_amount: Optional[float] = Field(None, ge=0, description="Amount of the last payment in EUR.")
    outstanding_balance: float = Field(0.0, ge=0, description="Amount currently owed. Defaults to zero.")
    notes: Optional[str] = Field(None, description="Free-text notes from the ERP (e.g. 'customer called 2024-11-01').")

    @field_validator("phone", mode="before")
    @classmethod
    def strip_phone(cls, v: object) -> Optional[str]:
        """Remove whitespace from phone numbers before storage."""
        if isinstance(v, str):
            return v.strip() or None
        return v  # type: ignore[return-value]

    @field_validator("full_name", mode="before")
    @classmethod
    def strip_name(cls, v: object) -> str:
        if isinstance(v, str):
            return v.strip()
        return str(v)

    model_config = {"str_strip_whitespace": True}


# ---------------------------------------------------------------------------
# ERP Scanner Agent output
# ---------------------------------------------------------------------------


class ContactValidity(BaseModel):
    """Breakdown of which contact fields are usable."""

    phone_valid: bool = False
    email_valid: bool = False
    has_any_contact: bool = False
    issues: list[str] = Field(default_factory=list, description="Human-readable list of contact problems found.")


class CustomerSummary(BaseModel):
    """
    Enriched view of a CustomerRecord produced by the ERP Scanner Agent.

    Adds computed fields (status, days overdue, days until expiry, contact
    validity) so that downstream agents work with pre-digested facts rather
    than raw dates and nulls.
    """

    record: CustomerRecord
    status: CustomerStatus
    days_overdue: int = Field(0, ge=0, description="Days since the subscription end date (0 if not overdue).")
    days_until_expiry: Optional[int] = Field(
        None,
        description="Days remaining before subscription_end. Negative means already expired. None if no end date.",
    )
    contact: ContactValidity
    scanner_notes: list[str] = Field(
        default_factory=list,
        description="Observations added by the ERP Scanner Agent (e.g. 'no email on file').",
    )


# ---------------------------------------------------------------------------
# Customer Risk Evaluator Agent output
# ---------------------------------------------------------------------------


class RiskEvaluation(BaseModel):
    """
    Risk and priority assessment produced by the Customer Risk Evaluator Agent.

    Scores are normalised to [0, 1] so they can be compared across customers
    and displayed as progress bars or colour-coded badges in the UI.
    """

    customer_id: str
    payment_risk_score: float = Field(..., ge=0.0, le=1.0, description="Likelihood of non-payment. 1 = highest risk.")
    churn_risk_score: float = Field(..., ge=0.0, le=1.0, description="Likelihood of churning. 1 = highest risk.")
    contact_quality_score: float = Field(
        ..., ge=0.0, le=1.0,
        description="Quality of available contact data. 1 = all channels valid.",
    )
    overall_priority: Priority
    recommended_channel: ContactChannel
    next_action: str = Field(..., description="Short human-readable action description (e.g. 'Call and offer renewal').")
    evaluator_notes: list[str] = Field(
        default_factory=list,
        description="Reasoning notes from the Risk Evaluator Agent.",
    )


# ---------------------------------------------------------------------------
# Follow-up Delivery Agent output
# ---------------------------------------------------------------------------


class FollowUpAction(BaseModel):
    """
    Proposed outreach action produced by the Follow-up Delivery Agent.

    The message is NOT sent automatically. It is surfaced in the UI for
    human review and approval before any outreach occurs.

    Extension point: add `whatsapp_payload`, `email_payload`, etc. fields
    here when delivery integrations are added.
    """

    customer_id: str
    template_used: TemplateKey
    message_subject: Optional[str] = Field(None, description="Subject line (relevant for email channel).")
    message_body: str = Field(..., description="Personalised message body ready for human review.")
    suggested_improvement: Optional[str] = Field(
        None,
        description="Optional small tweak suggested by the agent (e.g. 'Add customer name in greeting').",
    )
    delivery_notes: list[str] = Field(
        default_factory=list,
        description="Notes from the Follow-up Delivery Agent (e.g. 'used Serbian language variant').",
    )

    @model_validator(mode="after")
    def message_body_not_empty(self) -> FollowUpAction:
        if not self.message_body.strip():
            raise ValueError("message_body must not be empty.")
        return self


# ---------------------------------------------------------------------------
# Optional Claude Review Agent output
# ---------------------------------------------------------------------------


class ReviewStatus(str, Enum):
    APPROVED = "approved"
    NEEDS_REVISION = "needs_revision"
    REJECTED = "rejected"


class ReviewResult(BaseModel):
    """
    Assessment produced by the optional Claude Review Agent.

    The agent checks whether the priority is justified, the message is
    appropriately toned, and the recommended channel makes sense.
    This is a soft gate -- human operators make the final call.
    """

    customer_id: str
    status: ReviewStatus
    priority_justified: bool = Field(..., description="Whether the assigned priority appears correct.")
    message_tone_ok: bool = Field(..., description="Whether the message tone is professional and non-aggressive.")
    improvement_notes: Optional[str] = Field(
        None,
        description="Specific revision suggestions if status is NEEDS_REVISION.",
    )
    reviewer_notes: list[str] = Field(
        default_factory=list,
        description="Free-form observations from the Claude Review Agent.",
    )


# ---------------------------------------------------------------------------
# Composite pipeline result (one record, full pipeline)
# ---------------------------------------------------------------------------


class Tab1Result(BaseModel):
    """
    Complete result for a single customer after running the full Tab 1 pipeline.

    Workflows produce a list of Tab1Result objects; the UI renders each one
    as a row or expandable card.
    """

    summary: CustomerSummary
    evaluation: RiskEvaluation
    followup: FollowUpAction
    review: Optional[ReviewResult] = Field(
        None,
        description="Present only when the optional Claude Review Agent is enabled.",
    )

    @property
    def customer_id(self) -> str:
        return self.summary.record.customer_id

    @property
    def display_name(self) -> str:
        return self.summary.record.full_name

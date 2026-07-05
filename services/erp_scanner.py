"""
services/erp_scanner.py

Deterministic ERP record scanner for Tab 1: Existing Customers / ERP Follow-up.

Converts a raw CustomerRecord into a CustomerSummary by:
    - Deriving CustomerStatus from dates and balance
    - Computing days_overdue and days_until_expiry
    - Calling the validator to build ContactValidity
    - Adding plain-English scanner notes for agents and the UI

This is pure Python -- no LLM, no I/O, fully testable.
"""

from __future__ import annotations

from datetime import date

from models.customer import (
    ContactValidity,
    CustomerRecord,
    CustomerStatus,
    CustomerSummary,
)
from services.validator import build_contact_validity


def _derive_status(
    record: CustomerRecord,
    today: date,
) -> CustomerStatus:
    """Infer subscription status from dates and balance."""
    if record.subscription_end is None:
        return CustomerStatus.UNKNOWN

    if record.subscription_end >= today:
        # Within subscription window
        if record.outstanding_balance > 0 and record.last_payment_date is None:
            return CustomerStatus.SUSPENDED
        return CustomerStatus.ACTIVE

    # Past subscription end
    if record.outstanding_balance > 0:
        days_since = (today - record.subscription_end).days
        if days_since > 120:
            return CustomerStatus.EXPIRED
        return CustomerStatus.OVERDUE

    return CustomerStatus.EXPIRED


def scan_customer(
    record: CustomerRecord,
    today: date | None = None,
) -> CustomerSummary:
    """
    Convert one CustomerRecord into a CustomerSummary.

    Args:
        record: Raw ERP row.
        today:  Reference date. Defaults to date.today().

    Returns:
        CustomerSummary ready for the scorer.
    """
    today = today or date.today()
    status = _derive_status(record, today)

    days_overdue = 0
    if record.subscription_end and record.subscription_end < today:
        days_overdue = (today - record.subscription_end).days

    days_until_expiry: int | None = None
    if record.subscription_end:
        days_until_expiry = (record.subscription_end - today).days

    contact = build_contact_validity(record.phone, record.email)

    notes: list[str] = []
    if status == CustomerStatus.UNKNOWN:
        notes.append("No subscription dates in ERP -- status cannot be determined.")
    if days_overdue > 0:
        notes.append(f"Subscription ended {days_overdue} day(s) ago.")
    if record.outstanding_balance > 0:
        notes.append(f"Outstanding balance: ${record.outstanding_balance:.2f}.")
    if not contact.has_any_contact:
        notes.append("No valid contact channel found.")

    return CustomerSummary(
        record=record,
        status=status,
        days_overdue=days_overdue,
        days_until_expiry=days_until_expiry,
        contact=contact,
        scanner_notes=notes,
    )

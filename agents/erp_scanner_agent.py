"""
agents/erp_scanner_agent.py

ERP Scanner Agent — qualitative enrichment layer for Tab 1.

The deterministic erp_scanner service already computes hard facts:
status, days overdue, contact validity, and balance. This agent reads
those facts and adds the qualitative layer that rule-based code cannot:

    - Language / phone-prefix mismatch detection
      (e.g. phone is +381 Serbia but language tag says 'hr' — worth noting)
    - ERP notes interpretation
      (e.g. "prefers WhatsApp" → flag for future WhatsApp integration)
    - Risk context phrasing
      (e.g. "lapsed 8 months ago — win-back will require a stronger offer")
    - Data quality observations
      (e.g. "name appears duplicated in ERP — verify record integrity")

The agent does NOT re-derive status or scores. It only adds scanner_notes
to an already-populated CustomerSummary.

Design:
    - Receives a CustomerSummary as input.
    - Sends a compact, structured prompt to Claude.
    - Parses a JSON list of observations from the response.
    - Returns a new CustomerSummary with the additional notes appended.
    - If the Claude call fails, returns the original summary unchanged (safe fallback).

Extension points:
    - When WhatsApp Business API is connected, check notes for WhatsApp preference
      and tag the record here so the workflow can route it correctly.
    - When MCP ERP connector is live, pull additional payment history here
      before sending to Claude.
"""

from __future__ import annotations

import json
import logging
import time

import anthropic

from models.customer import CustomerSummary
from services.validator import describe_prefix

logger = logging.getLogger(__name__)

# Maximum observations Claude should return per customer.
# Keeps responses concise and costs predictable.
_MAX_OBSERVATIONS = 4

_SYSTEM_PROMPT = """\
You are an ERP data analyst assistant for a Balkan TV provider operating in Turkey.
The provider's customers are Balkan diaspora (Serbian, Croatian, Bosnian, Macedonian, \
Slovenian, Albanian) living in Turkish cities. The service costs $39/month (one plan only).

You will receive a structured summary of one customer record.
The summary already contains computed facts (status, days overdue, contact validity, balance).
Your job is to add QUALITATIVE observations that deterministic code cannot produce.

Return a JSON array of short observation strings. Maximum {max_obs} items.
Each observation must be:
  - Specific and actionable (not generic)
  - Based only on the data provided
  - Written for an operator who will contact the customer

Good observations:
  - "Phone prefix is +381 (Serbia) but language tag is 'hr' (Croatian) — confirm preferred language before calling."
  - "ERP notes say customer prefers WhatsApp — route to WhatsApp when integration is available."
  - "Lapsed 9 months ago with no balance — standard win-back offer may not be compelling enough."
  - "Name appears twice in notes — verify this is not a duplicate record."

Bad observations (too generic — do not return these):
  - "Customer is overdue."
  - "Contact the customer."
  - "Outstanding balance needs to be paid."

If no meaningful qualitative observations exist, return an empty array: []
Return ONLY the JSON array. No explanation, no markdown.
""".format(max_obs=_MAX_OBSERVATIONS)


def _build_prompt(summary: CustomerSummary) -> str:
    """Serialize the CustomerSummary into a compact prompt for Claude."""
    r = summary.record
    phone_country = describe_prefix(r.phone)

    data = {
        "customer_id": r.customer_id,
        "name": r.full_name,
        "city": r.country,
        "language_tag": r.language,
        "phone_country_prefix": phone_country,
        "email_present": r.email is not None,
        "subscription_status": summary.status.value,
        "days_overdue": summary.days_overdue,
        "days_until_expiry": summary.days_until_expiry,
        "outstanding_balance_usd": r.outstanding_balance,
        "contact_issues": summary.contact.issues,
        "existing_scanner_notes": summary.scanner_notes,
        "erp_notes": r.notes or "",
    }
    return json.dumps(data, indent=2)


def _parse_observations(raw: str) -> list[str]:
    """
    Extract the list of observations from Claude's response.
    Returns an empty list if parsing fails — never raises.
    """
    try:
        raw = raw.strip()
        # Handle cases where Claude wraps the array in markdown
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)
        if isinstance(result, list):
            return [str(item) for item in result if item]
        return []
    except (json.JSONDecodeError, IndexError, TypeError):
        logger.warning("ERP Scanner Agent: could not parse Claude response: %s", raw[:200])
        return []


def enrich_summary(
    summary: CustomerSummary,
    client: anthropic.Anthropic,
    model: str = "claude-haiku-4-5-20251001",
) -> CustomerSummary:
    """
    Call Claude to add qualitative observations to a CustomerSummary.

    Args:
        summary: CustomerSummary from the deterministic erp_scanner service.
        client:  Anthropic client (injected — do not create inside this function).
        model:   Claude model to use. Haiku is fast and cheap for this structured task.

    Returns:
        A new CustomerSummary with additional entries in scanner_notes.
        If the Claude call fails, the original summary is returned unchanged.

    Note:
        This function is intentionally synchronous. Gradio runs in a thread pool
        and the upstream workflow controls concurrency.
    """
    prompt = _build_prompt(summary)
    cid    = summary.record.customer_id

    logger.debug(
        "\n┌─ ERP Scanner Agent ── %s ─────────────────────────\n"
        "│ Model  : %s\n"
        "│ Prompt :\n%s\n"
        "└────────────────────────────────────────────────────",
        cid, model,
        "\n".join(f"│   {l}" for l in prompt.splitlines()),
    )

    try:
        _t0 = time.perf_counter()
        response = client.messages.create(
            model=model,
            max_tokens=256,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        _elapsed_ms = (time.perf_counter() - _t0) * 1000
        raw = response.content[0].text

        # Log tool calls if any exist (none today, but ready for future)
        tool_calls = [b for b in response.content if b.type == "tool_use"] \
                     if hasattr(response, "content") else []
        if tool_calls:
            for tc in tool_calls:
                logger.debug(
                    "│ TOOL CALL  : %s  input=%s", tc.name, tc.input
                )

        logger.debug(
            "\n┌─ ERP Scanner Agent ── RESPONSE ── %s ─────────────\n"
            "│ Stop reason : %s\n"
            "│ Latency     : %.0f ms\n"
            "│ Tokens      : in=%d  out=%d\n"
            "│ Raw output  : %s\n"
            "└────────────────────────────────────────────────────",
            cid,
            response.stop_reason,
            _elapsed_ms,
            response.usage.input_tokens,
            response.usage.output_tokens,
            raw[:300],
        )

        new_observations = _parse_observations(raw)

    except Exception as exc:
        logger.error("ERP Scanner Agent failed for %s: %s", cid, exc)
        return summary

    if not new_observations:
        logger.debug("ERP Scanner Agent [%s]: no new observations.", cid)
        return summary

    logger.debug(
        "ERP Scanner Agent [%s]: added %d observation(s): %s",
        cid, len(new_observations), new_observations,
    )

    return summary.model_copy(
        update={"scanner_notes": summary.scanner_notes + new_observations}
    )

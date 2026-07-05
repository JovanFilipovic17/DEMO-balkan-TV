"""
agents/followup_delivery_agent.py

Follow-up Delivery Agent for Tab 1: Existing Customers / ERP Follow-up.

Responsibilities:
    1. Confirm or override the deterministic template selection
       (the scorer may miss nuance visible only to an LLM).
    2. Personalise the rendered message body with a small,
       context-aware improvement — if and only if one is warranted.
    3. Keep the tone professional, warm, and non-aggressive.
    4. Never invent facts not present in the customer data.

The agent does NOT send the message. It produces a FollowUpAction
with a suggested_improvement field that the human operator reviews
before any outreach occurs.

Design:
    - Input:  CustomerSummary + RiskEvaluation + already-rendered message body.
    - Output: FollowUpAction with optional suggested_improvement string.
    - The original rendered body is always preserved unchanged.
      The suggestion is a separate field — the operator decides which to use.
    - If Claude returns no meaningful improvement, suggested_improvement is None.
    - Safe fallback: if the call fails, a FollowUpAction is still returned
      using the deterministic render without any suggestion.

Extension points:
    - When multi-language templates exist, pass the language tag here so
      Claude can confirm the correct language variant is used.
    - When WhatsApp Business API is connected, add a whatsapp_payload field
      to FollowUpAction and have this agent populate it.
"""

from __future__ import annotations

import logging
import time

import anthropic

from models.customer import (
    ContactChannel,
    CustomerSummary,
    FollowUpAction,
    RiskEvaluation,
    TemplateKey,
)
from services.templates import render_template, select_template_key

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a customer communication specialist for a Balkan TV provider in Turkey.
Customers are Balkan diaspora (Serbian, Croatian, Bosnian, Macedonian, Slovenian, Albanian).
The service has one plan at $39/month.

You will receive:
  - A rendered customer message (already personalised with their name, balance, etc.)
  - Key facts about the customer

Your task:
  Suggest ONE small, specific improvement to the message — only if the message
  would genuinely be better with your change.

Rules:
  - The improvement must be a single sentence or phrase, not a full rewrite.
  - Do not change the core content or facts.
  - Keep the tone warm, professional, and non-aggressive.
  - Do not invent facts not present in the customer data.
  - If the message is already good, reply with exactly: NO_IMPROVEMENT_NEEDED

Good improvement examples:
  - "Add 'We understand things get busy' before the payment mention to soften the tone."
  - "Mention that they can also pay via bank transfer, if that option exists."
  - "Since the ERP notes say they prefer evenings, add 'Feel free to call us in the evening.'"

Reply with ONLY the improvement suggestion or NO_IMPROVEMENT_NEEDED. No explanation.
"""


def _build_prompt(
    summary: CustomerSummary,
    evaluation: RiskEvaluation,
    rendered_body: str,
    template_key: TemplateKey,
) -> str:
    r = summary.record
    lines = [
        f"TEMPLATE USED: {template_key.value}",
        f"CUSTOMER: {r.full_name} | city={r.country} | language={r.language}",
        f"STATUS: {summary.status.value} | days_overdue={summary.days_overdue}",
        f"BALANCE: ${r.outstanding_balance:.0f} | priority={evaluation.overall_priority.value}",
        f"CHANNEL: {evaluation.recommended_channel.value}",
        f"ERP NOTES: {r.notes or 'none'}",
        f"SCANNER NOTES: {'; '.join(summary.scanner_notes) or 'none'}",
        "",
        "RENDERED MESSAGE:",
        "---",
        rendered_body,
        "---",
    ]
    return "\n".join(lines)


def generate_followup(
    summary: CustomerSummary,
    evaluation: RiskEvaluation,
    client: anthropic.Anthropic,
    model: str = "claude-haiku-4-5-20251001",
) -> FollowUpAction:
    """
    Select a template, render it, and optionally suggest an improvement.

    Args:
        summary:    CustomerSummary (may already be enriched by ERP Scanner Agent).
        evaluation: RiskEvaluation from the scorer.
        client:     Anthropic client (injected).
        model:      Claude model. Haiku keeps latency and cost low.

    Returns:
        FollowUpAction with:
            - message_body: the deterministically rendered message (always present)
            - suggested_improvement: Claude's proposed tweak (None if none needed)
    """
    template_key = select_template_key(summary)
    body, subject = render_template(template_key, summary)

    notes: list[str] = [f"Template selected: {template_key.value}"]

    # Skip improvement suggestion for SKIP-priority customers (no message needed)
    if evaluation.overall_priority.value == "skip":
        return FollowUpAction(
            customer_id=summary.record.customer_id,
            template_used=template_key,
            message_subject=subject,
            message_body=body,
            suggested_improvement=None,
            delivery_notes=["Priority is SKIP — no outreach needed."],
        )

    # Call Claude for improvement suggestion
    cid    = summary.record.customer_id
    prompt = _build_prompt(summary, evaluation, body, template_key)

    logger.debug(
        "\n┌─ Follow-up Delivery Agent ── %s ──────────────────\n"
        "│ Model    : %s\n"
        "│ Template : %s\n"
        "│ Prompt   :\n%s\n"
        "└────────────────────────────────────────────────────",
        cid, model, template_key.value,
        "\n".join(f"│   {l}" for l in prompt.splitlines()),
    )

    suggested_improvement: str | None = None
    try:
        _t0 = time.perf_counter()
        response = client.messages.create(
            model=model,
            max_tokens=128,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        _elapsed_ms = (time.perf_counter() - _t0) * 1000
        raw = response.content[0].text.strip()

        # Log tool calls if any (none today — placeholder for future tools)
        tool_calls = [b for b in response.content if b.type == "tool_use"] \
                     if hasattr(response, "content") else []
        if tool_calls:
            for tc in tool_calls:
                logger.debug("│ TOOL CALL  : %s  input=%s", tc.name, tc.input)

        logger.debug(
            "\n┌─ Follow-up Delivery Agent ── RESPONSE ── %s ──────\n"
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
            raw,
        )

        if raw and raw != "NO_IMPROVEMENT_NEEDED":
            suggested_improvement = raw
            notes.append("Agent suggested an improvement.")
            logger.debug("Follow-up Agent [%s]: improvement → %s", cid, raw)
        else:
            notes.append("Agent: message is good as-is.")
            logger.debug("Follow-up Agent [%s]: no improvement needed.", cid)

    except Exception as exc:
        logger.error("Follow-up Delivery Agent failed for %s: %s", cid, exc)
        notes.append("Agent call failed — using deterministic render only.")

    return FollowUpAction(
        customer_id=summary.record.customer_id,
        template_used=template_key,
        message_subject=subject,
        message_body=body,
        suggested_improvement=suggested_improvement,
        delivery_notes=notes,
    )

"""
agents/lead_cleaner_agent.py

Lead Cleaner Agent — AI enrichment layer for Tab 2.

Runs only on FIXABLE and POOR quality leads (triage gate — GOOD and REJECT
skip this agent entirely to save cost).

Responsibilities:
    1. Normalize the lead's name:
         - Title-case ALL-CAPS names only (e.g. "MARKO JOVIC" → "Marko Jovic")
         - Do NOT guess or add diacritics — return the name exactly as written
         - Flag clearly non-personal names (company names, single words)
    2. Add qualitative observations about the lead record:
         - Suggest likely language if not set (based on name pattern)
         - Flag suspiciously generic or bot-like data
         - Note opportunities (e.g. referral with phone → high intent)

Returns a Tab2Result with normalized_name and ai_notes populated.
Safe fallback: if the call fails, original result is returned unchanged.

Extension points:
    - When CRM MCP is connected, cross-check name against existing customer DB.
    - When language detection improves, pass detected language back to lead record.
"""

from __future__ import annotations

import json
import logging
import time

import anthropic

from models.lead import Tab2Result

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a data quality specialist for a Balkan TV provider in Turkey.
Customers are Balkan diaspora: Serbian, Croatian, Bosnian, Macedonian, Slovenian, Albanian.
You receive a raw lead record with potential data quality issues.

Your tasks:
  1. NORMALIZE the name:
     - If the name is ALL-CAPS, convert to proper Title Case (e.g. "MARKO JOVIC" → "Marko Jovic").
     - Do NOT add or change diacritics — return the name exactly as written, only fixing case.
     - If the name looks like a company, nickname, or is unrecognizable, flag it in ai_notes.
     - If name is missing, set normalized_name to null.

  2. ADD observations (max 3, specific and actionable):
     - Suggest likely language if not set (based on name pattern).
     - Flag suspiciously generic data (fake-looking phone, bot pattern).
     - Note conversion opportunities (e.g. referral source = high intent).
     - Do NOT repeat issues already obvious from the data (e.g. do not say "phone is invalid").

Return ONLY a JSON object with this exact shape:
{
  "normalized_name": "Proper Name Here" or null,
  "ai_notes": ["note 1", "note 2"]
}
No explanation. No markdown. No extra keys.
"""


def _build_prompt(result: Tab2Result) -> str:
    lead = result.raw
    issues_summary = [
        f"{i.field}: {i.description}" for i in result.issues
        if i.severity in ("error", "warning")
    ]
    data = {
        "name":    lead.full_name,
        "phone":   lead.phone,
        "email":   lead.email,
        "city":    lead.city,
        "language": lead.language,
        "source":  lead.source,
        "notes":   lead.notes,
        "known_issues": issues_summary,
        "quality":  result.quality.value,
        "score":    result.score.overall,
    }
    return json.dumps(data, indent=2, ensure_ascii=False)


def _parse_response(raw: str) -> tuple[str | None, list[str]]:
    """Extract normalized_name and ai_notes from Claude's JSON response."""
    try:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        obj = json.loads(raw)
        name  = obj.get("normalized_name") or None
        notes = [str(n) for n in obj.get("ai_notes", []) if n]
        return name, notes
    except Exception:
        logger.warning("Lead Cleaner Agent: could not parse response: %s", raw[:200])
        return None, []


def enrich_lead(
    result: Tab2Result,
    client: anthropic.Anthropic,
    model: str = "claude-haiku-4-5-20251001",
) -> Tab2Result:
    """
    Call Claude to normalize the name and add qualitative observations.

    Args:
        result: Tab2Result from the deterministic cleaner (FIXABLE or POOR only).
        client: Anthropic client (injected).
        model:  Haiku for low cost and latency.

    Returns:
        Tab2Result with normalized_name and ai_notes populated.
        Falls back to original result on any error.
    """
    cid    = f"ROW-{result.raw.row_index:03d}"
    prompt = _build_prompt(result)

    logger.debug(
        "\n┌─ Lead Cleaner Agent ── %s ────────────────────────\n"
        "│ Model   : %s\n"
        "│ Quality : %s  Score=%.2f\n"
        "│ Name    : %s\n"
        "└────────────────────────────────────────────────────",
        cid, model, result.quality.value, result.score.overall,
        result.raw.full_name or "(missing)",
    )

    try:
        _t0 = time.perf_counter()
        response = client.messages.create(
            model=model,
            max_tokens=200,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        _elapsed_ms = (time.perf_counter() - _t0) * 1000
        raw = response.content[0].text

        logger.debug(
            "\n┌─ Lead Cleaner Agent ── RESPONSE ── %s ────────────\n"
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

        normalized_name, ai_notes = _parse_response(raw)

    except Exception as exc:
        logger.error("Lead Cleaner Agent failed for %s: %s", cid, exc)
        return result

    return result.model_copy(update={
        "normalized_name": normalized_name,
        "ai_notes":        ai_notes,
    })

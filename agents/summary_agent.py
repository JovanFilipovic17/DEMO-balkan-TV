"""
agents/summary_agent.py

AI Business Summary Agent — runs once after the Tab 1 pipeline completes.

Reads pipeline stats and top-priority customers, then writes a concise
3-sentence executive summary for the operations manager.

Single Haiku call, ~150 output tokens. Adds ~1s of latency after the main
pipeline — negligible vs. the pipeline itself.

Extension points:
    - Connect to a CRM MCP to pull this period's targets.
    - Add rolling comparison ("up 14% vs last month") when historical data
      is available.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING

import anthropic

if TYPE_CHECKING:
    from models.customer import Tab1Result

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a business intelligence analyst for a Balkan TV provider in Turkey.
Subscribers are Balkan diaspora (Serbian, Croatian, Bosnian, Macedonian, Albanian) paying $39/month.

You receive pipeline results from an automated ERP follow-up analysis.
Write exactly 3 sentences for the operations manager:
  1. The revenue risk situation — use the actual dollar amount and counts.
  2. The single most important action to take right now and why.
  3. One operational insight or pattern worth noting.

Rules:
  - Specific numbers only. No vague language.
  - No bullet points, no headers. Flowing prose.
  - Max 3 sentences. Be punchy.
"""


def generate_summary(
    results: list[Tab1Result],
    stats: dict,
    client: anthropic.Anthropic,
    model: str = "claude-haiku-4-5-20251001",
) -> str:
    """
    Generate a 3-sentence executive summary of the pipeline results.

    Args:
        results: Full Tab1Result list from run_tab1_pipeline().
        stats:   Dict from summarise_results() — totals and revenue figures.
        client:  Anthropic client (injected — already created in on_run).
        model:   Defaults to Haiku for speed and cost.

    Returns:
        Plain-text summary string. Empty string on failure (caller handles).
    """
    # Compact top-5 data packet — keeps prompt short
    top_customers = []
    for r in results[:5]:
        rec = r.summary.record
        ev  = r.evaluation
        top_customers.append({
            "name":     rec.full_name,
            "city":     rec.country or "unknown",
            "status":   r.summary.status.value,
            "priority": ev.overall_priority.value,
            "balance":  rec.outstanding_balance,
            "channel":  ev.recommended_channel.value,
            "days_overdue": r.summary.days_overdue,
        })

    payload = {
        "stats": {
            "total_customers":    stats["total"],
            "high_priority":      stats["high"],
            "medium_priority":    stats["medium"],
            "unreachable":        stats["unreachable"],
            "recoverable_revenue_usd": round(stats.get("recoverable_revenue", 0), 2),
            "ai_suggestions":     stats["with_ai_suggestion"],
        },
        "top_priority_customers": top_customers,
    }
    prompt = json.dumps(payload, indent=2, ensure_ascii=False)

    logger.debug(
        "\n┌─ Summary Agent ──────────────────────────────────────\n"
        "│ Model   : %s\n"
        "│ Records : %d (top %d sampled)\n"
        "│ Revenue : $%.0f recoverable\n"
        "└──────────────────────────────────────────────────────",
        model, stats["total"], len(top_customers),
        stats.get("recoverable_revenue", 0),
    )

    try:
        _t0 = time.perf_counter()
        response = client.messages.create(
            model=model,
            max_tokens=220,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        elapsed_ms = (time.perf_counter() - _t0) * 1000
        text = response.content[0].text.strip()

        logger.debug(
            "\n┌─ Summary Agent ── RESPONSE ──────────────────────────\n"
            "│ Latency : %.0f ms\n"
            "│ Tokens  : in=%d  out=%d\n"
            "│ Output  : %s\n"
            "└──────────────────────────────────────────────────────",
            elapsed_ms,
            response.usage.input_tokens,
            response.usage.output_tokens,
            text[:300],
        )
        return text

    except Exception as exc:
        logger.error("Summary Agent failed: %s", exc)
        return ""

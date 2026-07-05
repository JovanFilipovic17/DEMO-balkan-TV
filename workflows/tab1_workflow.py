"""
workflows/tab1_workflow.py

Orchestrator for Tab 1: Existing Customers / ERP Follow-up.

Pipeline stages per customer record:

    Stage 1 — ERP Scan (deterministic, ALL customers)
        erp_scanner.scan_customer()
        → CustomerSummary  (status, days overdue, contact validity)

    Stage 2 — Risk Scoring (deterministic, ALL customers)
        scorer.score_customer()
        → RiskEvaluation  (payment risk, churn risk, priority, channel, action)

    ── TRIAGE GATE ── only HIGH + MEDIUM continue to AI stages ──────────────

    Stage 3 — ERP Scanner Agent (LLM, HIGH+MEDIUM only)
        agents.erp_scanner_agent.enrich_summary()
        → CustomerSummary with additional qualitative scanner_notes

    Stage 4 — Follow-up Delivery Agent (LLM, HIGH+MEDIUM only)
        agents.followup_delivery_agent.generate_followup()
        → FollowUpAction  (rendered message + optional improvement suggestion)

    LOW + SKIP → deterministic message only, no API call.

    Stage 5 — Results packaging
        → Tab1Result  sorted by priority (HIGH → MEDIUM → LOW → SKIP)

Design decisions:
    - Stages 1 and 2 always run — instant, no external dependencies.
    - Triage gate: AI agents only fire for HIGH and MEDIUM priority customers.
      For 500 customers this typically halves API calls vs. running AI on all.
    - Records are processed in parallel using ThreadPoolExecutor.
      max_workers controls concurrency (default 8).
      The anthropic client is thread-safe (httpx under the hood).
    - Errors in individual records are caught and logged — one bad record
      does not abort the whole batch.
    - A progress_callback is supported for Gradio live updates (thread-safe).

Extension points:
    - Stage 5: add an optional Claude Review Agent gate here.
    - Stage 1: swap scan_customer() for a live ERP MCP connector call.
    - Stage 4: add WhatsApp payload building when API is connected.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Callable, Optional

import anthropic

from agents.erp_scanner_agent import enrich_summary
from agents.followup_delivery_agent import generate_followup
from models.customer import (
    ContactChannel,
    CustomerRecord,
    FollowUpAction,
    Priority,
    Tab1Result,
    TemplateKey,
)
from services.erp_scanner import scan_customer
from services.scorer import score_customer
from services.templates import render_template, select_template_key

logger = logging.getLogger(__name__)

# Priority sort order for UI display (most urgent first)
_PRIORITY_ORDER = {
    Priority.HIGH: 0,
    Priority.MEDIUM: 1,
    Priority.LOW: 2,
    Priority.SKIP: 3,
}


# ---------------------------------------------------------------------------
# Deterministic fallback for FollowUpAction
# (used when use_ai_agents=False or when the agent call fails)
# ---------------------------------------------------------------------------

def _build_followup_deterministic(summary, evaluation) -> FollowUpAction:
    """Build a FollowUpAction using only the template renderer — no LLM."""
    key = select_template_key(summary)
    body, subject = render_template(key, summary)
    return FollowUpAction(
        customer_id=summary.record.customer_id,
        template_used=key,
        message_subject=subject,
        message_body=body,
        suggested_improvement=None,
        delivery_notes=["Deterministic render — AI agent not used."],
    )


# ---------------------------------------------------------------------------
# Per-record pipeline
# ---------------------------------------------------------------------------

def _process_one(
    record: CustomerRecord,
    today: date,
    use_ai_agents: bool,
    client: Optional[anthropic.Anthropic],
    ai_priorities: frozenset = frozenset({Priority.HIGH, Priority.MEDIUM}),
) -> Tab1Result:
    """
    Run the full pipeline for a single CustomerRecord.

    Triage logic:
        Stage 1 + 2 (deterministic) always run — instant, free.
        Stage 3 + 4 (AI agents) only run if the customer's priority is in
        ai_priorities (default: HIGH and MEDIUM).
        LOW and SKIP customers get a deterministic message only — no API call.
    """

    # Stage 1: deterministic ERP scan
    summary = scan_customer(record, today)

    # Stage 2: deterministic risk scoring
    evaluation = score_customer(summary)

    # Triage: does this customer warrant AI attention?
    run_ai = (
        use_ai_agents
        and client is not None
        and evaluation.overall_priority in ai_priorities
    )

    if run_ai:
        # Stage 3: LLM qualitative enrichment
        summary = enrich_summary(summary, client)
        # Stage 4: LLM message improvement
        followup = generate_followup(summary, evaluation, client)
    else:
        followup = _build_followup_deterministic(summary, evaluation)
        # If AI was available but priority didn't qualify, note it clearly
        if use_ai_agents and client is not None:
            followup = followup.model_copy(update={
                "delivery_notes": followup.delivery_notes + [
                    f"Priority is {evaluation.overall_priority.value.upper()} "
                    f"— AI skipped (only HIGH/MEDIUM get AI analysis)."
                ]
            })

    return Tab1Result(
        summary=summary,
        evaluation=evaluation,
        followup=followup,
        review=None,   # Stage 5 (Claude Review Agent) reserved for future use
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_tab1_pipeline(
    records: list[CustomerRecord],
    use_ai_agents: bool = True,
    reference_date: Optional[date] = None,
    max_records: Optional[int] = None,
    anthropic_api_key: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    max_workers: int = 8,
) -> list[Tab1Result]:
    """
    Run the full Tab 1 pipeline over a list of CustomerRecord objects.

    Args:
        records:
            List of CustomerRecord objects to process.
            Use services.demo_data.load_demo_customers() for the demo dataset.

        use_ai_agents:
            If True, calls the ERP Scanner Agent and Follow-up Delivery Agent
            via the Anthropic API. Set to False for instant demos or testing.
            Defaults to True; falls back to False if no API key is available.

        reference_date:
            Date used as "today" for overdue/expiry calculations.
            Defaults to date.today(). Override in tests for reproducibility.

        max_records:
            Process only the first N records. Useful for quick UI previews.
            None means process all records.

        anthropic_api_key:
            Anthropic API key. Falls back to the ANTHROPIC_API_KEY environment
            variable if not provided. If neither is set, use_ai_agents is
            automatically disabled.

        progress_callback:
            Optional callable(current, total, label) for live progress updates.
            Gradio's gr.Progress object satisfies this interface.

        max_workers:
            Number of parallel threads. Default 8. Each thread makes independent
            Anthropic API calls — the client is thread-safe. Lower this if you
            hit rate limits; raise it (up to ~16) for large batches.

    Returns:
        List of Tab1Result objects sorted by priority (HIGH first).

    Example:
        >>> from services.demo_data import load_demo_customers
        >>> records = load_demo_customers(n=20)
        >>> results = run_tab1_pipeline(records, use_ai_agents=False)
        >>> results[0].evaluation.overall_priority
        <Priority.HIGH: 'high'>
    """
    today = reference_date or date.today()
    batch = records[:max_records] if max_records else records
    total = len(batch)

    # Resolve Anthropic client
    client: Optional[anthropic.Anthropic] = None
    if use_ai_agents:
        api_key = anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
        if api_key:
            client = anthropic.Anthropic(api_key=api_key)
        else:
            logger.warning(
                "ANTHROPIC_API_KEY not set — running without AI agents."
            )
            use_ai_agents = False

    results: list[Tab1Result] = []
    _lock    = threading.Lock()
    _done    = [0]   # mutable int for thread-safe counter
    _t_start = time.perf_counter()

    def _process_and_track(record: CustomerRecord) -> Tab1Result:
        result = _process_one(record, today, use_ai_agents, client)
        with _lock:
            _done[0] += 1
            if progress_callback:
                progress_callback(
                    _done[0], total,
                    f"[{_done[0]}/{total}] {record.customer_id} done",
                )
        return result

    workers = min(max_workers, total) if total > 0 else 1
    logger.debug(
        "Tab1 pipeline: %d records, %d workers, AI=%s",
        total, workers, use_ai_agents,
    )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_process_and_track, rec): rec for rec in batch}
        for future in as_completed(futures):
            rec = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                logger.error("Pipeline failed for %s: %s", rec.customer_id, exc)

    elapsed = time.perf_counter() - _t_start
    logger.debug(
        "Tab1 pipeline complete: %d results in %.1fs (%.2fs/record)",
        len(results), elapsed, elapsed / max(len(results), 1),
    )

    if progress_callback:
        progress_callback(total, total, f"Done — {len(results)} customers in {elapsed:.1f}s")

    # Sort: HIGH → MEDIUM → LOW → SKIP
    results.sort(key=lambda r: _PRIORITY_ORDER.get(r.evaluation.overall_priority, 99))

    return results


# ---------------------------------------------------------------------------
# Convenience stats helper (used by the UI summary panel)
# ---------------------------------------------------------------------------

def summarise_results(results: list[Tab1Result]) -> dict:
    """
    Return a flat dict of counts for the UI dashboard header.

    Keys: total, high, medium, low, skip,
          reachable, unreachable, with_ai_suggestion, recoverable_revenue
    """
    from collections import Counter
    priority_counts = Counter(r.evaluation.overall_priority.value for r in results)
    reachable = sum(
        1 for r in results
        if r.evaluation.recommended_channel != ContactChannel.NONE
    )
    with_suggestion = sum(
        1 for r in results
        if r.followup.suggested_improvement is not None
    )
    # Recoverable revenue: outstanding balance for overdue/expired reachable customers
    recoverable_revenue = sum(
        r.summary.record.outstanding_balance
        for r in results
        if r.summary.status.value in ("overdue", "expired")
        and r.evaluation.recommended_channel != ContactChannel.NONE
        and r.summary.record.outstanding_balance > 0
    )
    return {
        "total": len(results),
        "high": priority_counts.get("high", 0),
        "medium": priority_counts.get("medium", 0),
        "low": priority_counts.get("low", 0),
        "skip": priority_counts.get("skip", 0),
        "reachable": reachable,
        "unreachable": len(results) - reachable,
        "with_ai_suggestion": with_suggestion,
        "recoverable_revenue": recoverable_revenue,
    }

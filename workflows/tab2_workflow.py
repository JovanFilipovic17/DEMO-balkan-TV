"""
workflows/tab2_workflow.py

Orchestrator for Tab 2: Lead Database Cleaner & Qualifier.

Pipeline:

    Stage 1 — Deterministic cleaning (ALL leads)
        lead_cleaner.clean_leads()
        → list[Tab2Result] with issues, scores, duplicates flagged

    ── TRIAGE GATE ── only FIXABLE + POOR leads continue to AI ──────────

    Stage 2 — AI enrichment (FIXABLE + POOR only)
        agents.lead_cleaner_agent.enrich_lead()
        → Tab2Result with normalized_name + ai_notes

    GOOD  → no AI (already clean)
    REJECT → no AI (duplicates — operator will discard)

    Stage 3 — Results packaging
        Sorted: GOOD → FIXABLE → POOR → REJECT

Design:
    - Stage 1 is instant (<1s for 200 leads).
    - Stage 2 runs in parallel (ThreadPoolExecutor, default 8 workers).
    - Typically 40-60% of leads reach Stage 2.
    - Safe fallback: if AI call fails, result is returned as-is from Stage 1.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

import anthropic

from agents.lead_cleaner_agent import enrich_lead
from models.lead import LeadQuality, RawLead, Tab2Result
from services.lead_cleaner import clean_leads

logger = logging.getLogger(__name__)

# Sort order for UI display
_QUALITY_ORDER = {
    LeadQuality.GOOD:    0,
    LeadQuality.FIXABLE: 1,
    LeadQuality.POOR:    2,
    LeadQuality.REJECT:  3,
}

# Which quality levels get AI enrichment
_AI_QUALITIES = frozenset({LeadQuality.FIXABLE, LeadQuality.POOR})


def run_tab2_pipeline(
    leads: list[RawLead],
    use_ai_agents: bool = True,
    max_leads: Optional[int] = None,
    anthropic_api_key: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    max_workers: int = 8,
) -> list[Tab2Result]:
    """
    Run the full Tab 2 pipeline.

    Args:
        leads:             Raw leads from CSV or demo data.
        use_ai_agents:     If False, skip AI enrichment entirely.
        max_leads:         Cap the batch size (None = all).
        anthropic_api_key: Falls back to ANTHROPIC_API_KEY env var.
        progress_callback: callable(current, total, label) for UI updates.
        max_workers:       Parallel threads for AI stage (default 8).

    Returns:
        List of Tab2Result sorted GOOD → FIXABLE → POOR → REJECT.
    """
    batch = leads[:max_leads] if max_leads else leads
    total = len(batch)
    _t_start = time.perf_counter()

    # ── Stage 1: deterministic cleaning (instant) ─────────────────────────
    if progress_callback:
        progress_callback(0, total, "Running deterministic cleaning…")

    results = clean_leads(batch)

    ai_candidates = [r for r in results if r.quality in _AI_QUALITIES]
    logger.debug(
        "Tab2 Stage 1 complete: %d leads, %d go to AI (%d GOOD, %d REJECT)",
        total,
        len(ai_candidates),
        sum(1 for r in results if r.quality == LeadQuality.GOOD),
        sum(1 for r in results if r.quality == LeadQuality.REJECT),
    )

    # ── Resolve Anthropic client ──────────────────────────────────────────
    client: Optional[anthropic.Anthropic] = None
    if use_ai_agents and ai_candidates:
        api_key = anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
        if api_key:
            client = anthropic.Anthropic(api_key=api_key)
        else:
            logger.warning("ANTHROPIC_API_KEY not set — skipping AI enrichment.")
            use_ai_agents = False

    # ── Stage 2: AI enrichment (parallel) ────────────────────────────────
    if use_ai_agents and client and ai_candidates:
        _lock = threading.Lock()
        _done = [0]
        ai_total = len(ai_candidates)

        # Build a lookup so we can replace results in-place
        result_by_index: dict[int, int] = {
            r.raw.row_index: i for i, r in enumerate(results)
        }

        def _enrich(r: Tab2Result) -> Tab2Result:
            enriched = enrich_lead(r, client)
            with _lock:
                _done[0] += 1
                if progress_callback:
                    progress_callback(
                        _done[0], ai_total,
                        f"[{_done[0]}/{ai_total}] AI enriching row {r.raw.row_index}…",
                    )
            return enriched

        workers = min(max_workers, ai_total)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_enrich, r): r for r in ai_candidates}
            for future in as_completed(futures):
                orig = futures[future]
                try:
                    enriched = future.result()
                    idx = result_by_index[orig.raw.row_index]
                    results[idx] = enriched
                except Exception as exc:
                    logger.error(
                        "AI enrichment failed for row %d: %s",
                        orig.raw.row_index, exc,
                    )

    elapsed = time.perf_counter() - _t_start
    logger.debug(
        "Tab2 pipeline complete: %d results in %.1fs", len(results), elapsed
    )

    if progress_callback:
        progress_callback(total, total, f"Done — {total} leads processed in {elapsed:.1f}s")

    # ── Sort: GOOD → FIXABLE → POOR → REJECT ─────────────────────────────
    results.sort(key=lambda r: _QUALITY_ORDER.get(r.quality, 99))
    return results


def summarise_tab2(results: list[Tab2Result]) -> dict:
    """Flat stats dict for the UI summary panel."""
    from collections import Counter
    quality_counts = Counter(r.quality.value for r in results)
    return {
        "total":           len(results),
        "good":            quality_counts.get("good", 0),
        "fixable":         quality_counts.get("fixable", 0),
        "poor":            quality_counts.get("poor", 0),
        "reject":          quality_counts.get("reject", 0),
        "duplicates":      sum(1 for r in results if r.is_duplicate),
        "invalid_contact": sum(1 for r in results if not r.phone_valid and not r.email_valid),
        "ai_enriched":     sum(1 for r in results if r.ai_notes),
        "ready_to_import": quality_counts.get("good", 0),
    }

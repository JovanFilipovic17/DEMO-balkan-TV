"""
workflows/tab3_workflow.py

Orchestrator for Tab 3: Acquisition Intelligence Agent.

Pipeline (per community):

    Stage 1 — Community discovery (demo data or Agent 1 scraper — placeholder)
        services.acquisition_demo_data.load_demo_communities()
        → list[Community]

    Stage 2 — Audience scoring (ALL communities)
        agents.community_scorer_agent.score_community()
        → AudienceProfile  (switching_likelihood_score, research factors)

    Stage 3 — Message generation (ALL communities above threshold)
        agents.message_generator_agent.generate_campaign()
        → Campaign  (message in target language, campaign type, reward)

    Stage 4 — Channel recommendation (ALL communities)
        agents.channel_recommender_agent.recommend_channel()
        → ActionRecommendation  (channel, reach, priority rank, rationale)

    Stage 5 — Results packaging
        → list[Tab3Result] sorted by switching_likelihood_score descending

Design:
    - All AI stages run in parallel per community (ThreadPoolExecutor).
    - Stages 2–4 are sequential PER community (each stage feeds the next).
    - Safe fallback in every agent — one failed community does not abort the batch.
    - priority_rank is assigned post-hoc, sorted by switching_likelihood_score.
    - Deterministic mode (use_ai_agents=False) runs all three agent fallbacks.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

import anthropic

from agents.channel_recommender_agent import recommend_channel
from agents.community_scorer_agent import score_community
from agents.message_generator_agent import generate_campaign
from models.lead_acquisition import (
    ActionRecommendation,
    AudienceProfile,
    Campaign,
    Channel,
    Community,
    RewardType,
    Tab3Result,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Deterministic fallbacks (used when use_ai_agents=False)
# ---------------------------------------------------------------------------

def _deterministic_audience(community: Community) -> AudienceProfile:
    score = round(
        community.activity_score * 0.40
        + community.language_match_score * 0.35
        + community.city_overlap_score * 0.25,
        3,
    )
    return AudienceProfile(
        community_id=community.community_id,
        estimated_subscriber_profile=(
            f"Deterministic estimate: {community.language.value}-speaking community "
            f"in {community.city or 'unknown city'} with {community.member_count or 0} members. "
            f"Score based on activity, language match, and city overlap."
        ),
        switching_likelihood_score=min(1.0, max(0.0, score)),
        supporting_research_factors=[
            f"activity_score={community.activity_score:.2f}",
            f"language_match_score={community.language_match_score:.2f}",
            f"city_overlap_score={community.city_overlap_score:.2f}",
        ],
        scorer_notes=["Deterministic mode — AI agent not used."],
    )


def _deterministic_campaign(community: Community, audience: AudienceProfile) -> Campaign:
    from agents.message_generator_agent import (
        _fallback_message,
        _select_campaign_type,
    )
    campaign_type = _select_campaign_type(audience, community)
    return Campaign(
        target_community_id=community.community_id,
        campaign_type=campaign_type,
        target_language=community.language,
        reward_type=RewardType.NONE,
        generated_message=_fallback_message(community, campaign_type),
        message_subject=None,
        generator_notes=["Deterministic mode — AI agent not used."],
    )


def _deterministic_recommendation(
    community: Community,
    priority_rank: int,
) -> ActionRecommendation:
    from agents.channel_recommender_agent import _fallback_channel
    channel, reach, confidence, rationale = _fallback_channel(community)
    return ActionRecommendation(
        community_id=community.community_id,
        recommended_channel=channel,
        estimated_reach=reach,
        priority_rank=priority_rank,
        confidence_score=confidence,
        rationale=rationale,
        recommender_notes=["Deterministic mode — AI agent not used."],
    )


# ---------------------------------------------------------------------------
# Per-community pipeline
# ---------------------------------------------------------------------------

def _process_one(
    community: Community,
    use_ai_agents: bool,
    client: Optional[anthropic.Anthropic],
    priority_rank: int,
) -> Tab3Result:
    """
    Run Stages 2–4 for a single Community.

    priority_rank is a placeholder (1) here; the workflow re-ranks all results
    after sorting by switching_likelihood_score.
    """
    if use_ai_agents and client is not None:
        audience = score_community(community, client)
        campaign = generate_campaign(community, audience, client)
        recommendation = recommend_channel(community, audience, campaign, client, priority_rank)
    else:
        audience = _deterministic_audience(community)
        campaign = _deterministic_campaign(community, audience)
        recommendation = _deterministic_recommendation(community, priority_rank)

    return Tab3Result(
        community=community,
        audience=audience,
        campaign=campaign,
        recommendation=recommendation,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_tab3_pipeline(
    communities: list[Community],
    use_ai_agents: bool = True,
    max_communities: Optional[int] = None,
    anthropic_api_key: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    max_workers: int = 6,
) -> list[Tab3Result]:
    """
    Run the full Tab 3 pipeline over a list of Community objects.

    Args:
        communities:       List of Community objects (from demo data or scraper).
        use_ai_agents:     If False, all three AI stages use deterministic fallbacks.
        max_communities:   Cap the batch size for quick previews.
        anthropic_api_key: Falls back to ANTHROPIC_API_KEY env var.
        progress_callback: callable(current, total, label) for UI progress updates.
        max_workers:       Parallel threads (default 6 — 3 AI calls per community).

    Returns:
        List of Tab3Result sorted by switching_likelihood_score descending.
    """
    batch = communities[:max_communities] if max_communities else communities
    total = len(batch)
    _t_start = time.perf_counter()

    # Resolve Anthropic client
    client: Optional[anthropic.Anthropic] = None
    if use_ai_agents:
        api_key = anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
        if api_key:
            client = anthropic.Anthropic(api_key=api_key)
        else:
            logger.warning("ANTHROPIC_API_KEY not set — running without AI agents.")
            use_ai_agents = False

    results: list[Tab3Result] = []
    _lock  = threading.Lock()
    _done  = [0]

    def _process_and_track(community: Community) -> Tab3Result:
        result = _process_one(community, use_ai_agents, client, priority_rank=1)
        with _lock:
            _done[0] += 1
            if progress_callback:
                progress_callback(
                    _done[0], total,
                    f"[{_done[0]}/{total}] {community.name} scored…",
                )
        return result

    workers = min(max_workers, total) if total > 0 else 1
    logger.debug(
        "Tab3 pipeline: %d communities, %d workers, AI=%s",
        total, workers, use_ai_agents,
    )

    if progress_callback:
        progress_callback(0, total, "Starting acquisition intelligence pipeline…")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_process_and_track, c): c for c in batch}
        for future in as_completed(futures):
            comm = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                logger.error("Pipeline failed for %s: %s", comm.community_id, exc)

    # ── Sort by switching_likelihood_score descending ──────────────────────
    results.sort(
        key=lambda r: r.audience.switching_likelihood_score,
        reverse=True,
    )

    # ── Re-assign priority ranks after sorting ────────────────────────────
    for rank, result in enumerate(results, start=1):
        updated_rec = result.recommendation.model_copy(update={"priority_rank": rank})
        results[rank - 1] = result.model_copy(update={"recommendation": updated_rec})

    elapsed = time.perf_counter() - _t_start
    logger.debug(
        "Tab3 pipeline complete: %d results in %.1fs", len(results), elapsed
    )

    if progress_callback:
        progress_callback(
            total, total,
            f"Done — {len(results)} communities analysed in {elapsed:.1f}s",
        )

    return results


# ---------------------------------------------------------------------------
# Stats helper
# ---------------------------------------------------------------------------

def summarise_tab3(results: list[Tab3Result]) -> dict:
    """Flat stats dict for the UI KPI cards."""
    from collections import Counter

    platform_counts = Counter(r.community.platform.value for r in results)
    channel_counts  = Counter(r.recommendation.recommended_channel.value for r in results)
    high_priority   = sum(1 for r in results if r.audience.switching_likelihood_score >= 0.65)
    total_reach     = sum(r.recommendation.estimated_reach for r in results)

    avg_score = (
        sum(r.audience.switching_likelihood_score for r in results) / len(results)
        if results else 0.0
    )

    return {
        "total":          len(results),
        "high_priority":  high_priority,
        "facebook":       platform_counts.get("facebook", 0),
        "instagram":      platform_counts.get("instagram", 0),
        "total_reach":    total_reach,
        "avg_score":      round(avg_score, 3),
        "group_posts":    channel_counts.get("group_post", 0),
        "admin_dms":      channel_counts.get("admin_dm", 0),
        "ig_stories":     channel_counts.get("ig_story", 0),
    }

"""
agents/community_scorer_agent.py

Community Scorer Agent — Tab 3, Stage 2.

Receives a Community object and returns an AudienceProfile that estimates:
  - The audience composition (age, income bracket, IPTV likelihood)
  - A switching_likelihood_score grounded in GENAR market research
  - Research factors supporting the score
  - Qualitative notes

Grounding data injected into the prompt:
  - 144 TL price gap vs. next-cheapest competitor
  - 66.4% of survey respondents want a cheaper package
  - 8.6% Balkan-origin people in Turkey are currently aware of MTEL
  - 53.3% of MTEL subscribers first heard of it via friends/family

Safe fallback: if the API call fails, a deterministic fallback score is
computed from Community.activity_score + language_match_score + city_overlap_score.
"""

from __future__ import annotations

import json
import logging
import time

import anthropic

from models.lead_acquisition import (
    AudienceProfile,
    Community,
    MARKET_RESEARCH,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are an audience intelligence analyst for MTEL, a Balkan-language TV provider
serving the Balkan diaspora in Turkey.

MARKET RESEARCH CONTEXT (GENAR survey, May 2026, n=1 200):
- Price gap: MTEL is 144 TL cheaper per month than the next competitor
- 66.4% of Balkan diaspora respondents want a cheaper TV package
- Only 8.6% of Balkan-origin people in Turkey are currently aware of MTEL
- 53.3% of existing MTEL subscribers discovered MTEL via friends or family

Your task: analyse the community data provided and produce an AudienceProfile.

Rules:
  1. estimated_subscriber_profile: 2-3 sentences describing likely audience
     demographics and their probable current TV situation.
  2. switching_likelihood_score: a float from 0.0 to 1.0 reflecting how likely
     a community member is to adopt MTEL. Ground this in the GENAR data above.
     - 0.8+ reserved for large, active, high-city-overlap, price-sensitive communities
     - 0.5–0.79 for moderate-quality communities
     - Below 0.5 for inactive, low-relevance, or already-saturated communities
  3. supporting_research_factors: 2-4 specific data points from the research context
     or observable community signals that justify the score.
  4. scorer_notes: 1-2 short operational notes (e.g. "admin DM recommended", "verify city").

Return ONLY valid JSON:
{
  "estimated_subscriber_profile": "...",
  "switching_likelihood_score": 0.0,
  "supporting_research_factors": ["...", "..."],
  "scorer_notes": ["...", "..."]
}
No markdown. No explanation. No extra keys.
"""


def _build_prompt(community: Community) -> str:
    data = {
        "community_id":         community.community_id,
        "name":                 community.name,
        "platform":             community.platform.value,
        "language":             community.language.value,
        "city":                 community.city,
        "member_count":         community.member_count,
        "activity_score":       community.activity_score,
        "language_match_score": community.language_match_score,
        "city_overlap_score":   community.city_overlap_score,
        "scraper_notes":        community.scraper_notes,
        "market_research":      MARKET_RESEARCH,
    }
    return json.dumps(data, indent=2, ensure_ascii=False)


def _parse_response(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw)


def _fallback_score(community: Community) -> float:
    """Deterministic score when AI call fails."""
    score = (
        community.activity_score * 0.40
        + community.language_match_score * 0.35
        + community.city_overlap_score * 0.25
    )
    return round(min(1.0, max(0.0, score)), 3)


def score_community(
    community: Community,
    client: anthropic.Anthropic,
    model: str = "claude-haiku-4-5-20251001",
) -> AudienceProfile:
    """
    Call Claude to produce an AudienceProfile for a Community.

    Args:
        community: Community object from Agent 1 (scraper / demo data).
        client:    Anthropic client (injected by workflow).
        model:     Haiku for cost efficiency.

    Returns:
        AudienceProfile with scoring and research context.
        Falls back to deterministic score on any error.
    """
    cid    = community.community_id
    prompt = _build_prompt(community)

    logger.debug(
        "\n┌─ Community Scorer ── %s ────────────────────────\n"
        "│ Name    : %s\n"
        "│ Platform: %s  Language=%s\n"
        "│ Members : %s  Activity=%.2f\n"
        "└─────────────────────────────────────────────────",
        cid, community.name, community.platform.value,
        community.language.value, community.member_count, community.activity_score,
    )

    try:
        _t0 = time.perf_counter()
        response = client.messages.create(
            model=model,
            max_tokens=400,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        _elapsed_ms = (time.perf_counter() - _t0) * 1000
        raw = response.content[0].text

        logger.debug(
            "\n┌─ Community Scorer ── RESPONSE ── %s ───────────\n"
            "│ Latency : %.0f ms  tokens in=%d out=%d\n"
            "│ Output  : %s\n"
            "└─────────────────────────────────────────────────",
            cid, _elapsed_ms,
            response.usage.input_tokens, response.usage.output_tokens,
            raw[:300],
        )

        obj = _parse_response(raw)

        return AudienceProfile(
            community_id=cid,
            estimated_subscriber_profile=obj["estimated_subscriber_profile"],
            switching_likelihood_score=float(obj["switching_likelihood_score"]),
            supporting_research_factors=list(obj.get("supporting_research_factors", [])),
            scorer_notes=list(obj.get("scorer_notes", [])),
        )

    except Exception as exc:
        logger.error("Community Scorer failed for %s: %s", cid, exc)
        fallback = _fallback_score(community)
        return AudienceProfile(
            community_id=cid,
            estimated_subscriber_profile=(
                f"Score estimated deterministically from activity, language match, "
                f"and city overlap. AI scoring unavailable."
            ),
            switching_likelihood_score=fallback,
            supporting_research_factors=[
                f"activity_score={community.activity_score:.2f}",
                f"language_match_score={community.language_match_score:.2f}",
                f"city_overlap_score={community.city_overlap_score:.2f}",
            ],
            scorer_notes=["Fallback deterministic score — AI call failed."],
        )

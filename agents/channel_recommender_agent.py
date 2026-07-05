"""
agents/channel_recommender_agent.py

Channel Recommender Agent — Tab 3, Stage 4.

Receives Community + AudienceProfile + Campaign and recommends:
  - The best outreach channel (group_post / admin_dm / ig_story / email)
  - Estimated reach
  - Priority rank (assigned by the workflow after all communities are scored)
  - Confidence score
  - One-sentence rationale

Channel selection logic (grounded in agent output, refined by heuristics):
  admin_dm    → high-trust, low-reach; best for active admins with clear contact
  group_post  → max reach; best for large active groups
  ig_story    → visual platform; Instagram-only communities
  email       → when contact info is available (rare in demo data)

Safe fallback: deterministic channel selection from community signals.
"""

from __future__ import annotations

import json
import logging
import time

import anthropic

from models.lead_acquisition import (
    ActionRecommendation,
    AudienceProfile,
    Campaign,
    Channel,
    Community,
    Platform,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are an outreach strategy advisor for MTEL, a Balkan-language TV provider in Turkey.

You receive data about a social community (platform, size, activity, city) plus the
campaign message already drafted for it.  Your job is to recommend the best outreach
channel and estimate the likely reach.

Channels available:
  group_post — post in the community feed. Highest reach (50-80% of members see it).
               Best for large (>5 000 members), active (activity_score > 0.5) groups.
  admin_dm   — private message to the group admin. Low reach but highest trust.
               Best when scraper_notes mention an identifiable admin.
  ig_story   — Instagram Story or paid boost. Only for Instagram platform.
               Reach ~30-40% of followers.
  email      — direct email. Only when contact email is known (usually not for social groups).

Rules:
  1. recommended_channel: one of group_post, admin_dm, ig_story, email.
  2. estimated_reach: realistic integer. Use member_count as the base:
     - group_post: 50-70% of member_count
     - admin_dm: 1 (the admin) — write 1 for reach
     - ig_story: 30-40% of member_count
     - email: 1 unless a list was scraped
  3. confidence_score: 0.0–1.0 reflecting how confident you are in this channel.
  4. rationale: ONE sentence explaining why.
  5. recommender_notes: 1-2 operational notes (e.g. "find admin in group About section").

Return ONLY valid JSON:
{
  "recommended_channel": "group_post|admin_dm|ig_story|email",
  "estimated_reach": 0,
  "confidence_score": 0.0,
  "rationale": "...",
  "recommender_notes": ["...", "..."]
}
No markdown. No explanation. No extra keys.
"""


def _build_prompt(
    community: Community,
    audience: AudienceProfile,
    campaign: Campaign,
) -> str:
    data = {
        "community_id":     community.community_id,
        "name":             community.name,
        "platform":         community.platform.value,
        "city":             community.city,
        "member_count":     community.member_count,
        "activity_score":   community.activity_score,
        "city_overlap":     community.city_overlap_score,
        "scraper_notes":    community.scraper_notes,
        "switching_score":  audience.switching_likelihood_score,
        "campaign_type":    campaign.campaign_type.value,
        "target_language":  campaign.target_language.value,
    }
    return json.dumps(data, indent=2, ensure_ascii=False)


def _parse_response(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw)


def _fallback_channel(community: Community) -> tuple[Channel, int, float, str]:
    """Deterministic channel + reach when AI call fails."""
    if community.platform == Platform.INSTAGRAM:
        members = community.member_count or 1000
        return (
            Channel.IG_STORY,
            int(members * 0.35),
            0.65,
            "Instagram community — Story format is native to the platform.",
        )
    members = community.member_count or 1000
    has_admin_hint = any("admin" in n.lower() for n in community.scraper_notes)
    if has_admin_hint and members < 5000:
        return (
            Channel.ADMIN_DM,
            1,
            0.70,
            "Smaller community with identifiable admin — DM for higher trust.",
        )
    return (
        Channel.GROUP_POST,
        int(members * 0.60),
        0.60,
        "Large active group — group post maximises reach.",
    )


def recommend_channel(
    community: Community,
    audience: AudienceProfile,
    campaign: Campaign,
    client: anthropic.Anthropic,
    priority_rank: int = 1,
    model: str = "claude-haiku-4-5-20251001",
) -> ActionRecommendation:
    """
    Call Claude to recommend an outreach channel for a community.

    Args:
        community:     Community object from Agent 1.
        audience:      AudienceProfile from Agent 2.
        campaign:      Campaign from Agent 3.
        client:        Anthropic client (injected by workflow).
        priority_rank: Rank assigned by the workflow (1 = highest priority).
        model:         Haiku for cost efficiency.

    Returns:
        ActionRecommendation with channel, reach, confidence, rationale.
        Falls back to deterministic selection on any error.
    """
    cid    = community.community_id
    prompt = _build_prompt(community, audience, campaign)

    logger.debug(
        "\n┌─ Channel Recommender ── %s ─────────────────────\n"
        "│ Name     : %s  Platform=%s\n"
        "│ Members  : %s  Activity=%.2f  Rank=%d\n"
        "└──────────────────────────────────────────────────",
        cid, community.name, community.platform.value,
        community.member_count, community.activity_score, priority_rank,
    )

    try:
        _t0 = time.perf_counter()
        response = client.messages.create(
            model=model,
            max_tokens=300,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        _elapsed_ms = (time.perf_counter() - _t0) * 1000
        raw = response.content[0].text

        logger.debug(
            "\n┌─ Channel Recommender ── RESPONSE ── %s ────────\n"
            "│ Latency : %.0f ms  tokens in=%d out=%d\n"
            "│ Output  : %s\n"
            "└──────────────────────────────────────────────────",
            cid, _elapsed_ms,
            response.usage.input_tokens, response.usage.output_tokens,
            raw[:300],
        )

        obj = _parse_response(raw)

        try:
            channel = Channel(obj["recommended_channel"])
        except (KeyError, ValueError):
            channel, _, _, _ = _fallback_channel(community)

        return ActionRecommendation(
            community_id=cid,
            recommended_channel=channel,
            estimated_reach=int(obj.get("estimated_reach", 0)),
            priority_rank=priority_rank,
            confidence_score=float(obj.get("confidence_score", 0.5)),
            rationale=obj.get("rationale", "See recommender notes."),
            recommender_notes=list(obj.get("recommender_notes", [])),
        )

    except Exception as exc:
        logger.error("Channel Recommender failed for %s: %s", cid, exc)
        channel, reach, confidence, rationale = _fallback_channel(community)
        return ActionRecommendation(
            community_id=cid,
            recommended_channel=channel,
            estimated_reach=reach,
            priority_rank=priority_rank,
            confidence_score=confidence,
            rationale=rationale,
            recommender_notes=["Fallback deterministic selection — AI call failed."],
        )

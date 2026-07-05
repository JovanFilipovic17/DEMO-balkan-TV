"""
agents/message_generator_agent.py

Message Generator Agent — Tab 3, Stage 3.

Receives a Community + AudienceProfile and drafts a campaign message in the
community's target language. The message is NOT sent until a human approves it.

Campaign types and when to use them:
  price_hook  — when switching_likelihood >= 0.60 and price gap is relevant
  referral    — when community is active and word-of-mouth is the angle
  survey      — when data is thin or awareness is very low
  awareness   — when language or city match is strong but activity/size is small

Message register:
  - Warm and community-appropriate (not corporate)
  - Calls out the 144 TL price advantage when using price_hook
  - Always in the community's dominant language
  - No English unless community.language == Language.MIXED and platform is global
"""

from __future__ import annotations

import json
import logging
import time

import anthropic

from models.lead_acquisition import (
    AudienceProfile,
    Campaign,
    CampaignType,
    Community,
    Language,
    MARKET_RESEARCH,
    RewardType,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Language labels for the prompt (ISO codes → human names)
# ---------------------------------------------------------------------------

_LANG_LABEL: dict[Language, str] = {
    Language.SERBIAN:    "Serbian (Latin script)",
    Language.BOSNIAN:    "Bosnian",
    Language.CROATIAN:   "Croatian",
    Language.MACEDONIAN: "Macedonian (Cyrillic script)",
    Language.ALBANIAN:   "Albanian",
    Language.MIXED:      "a mix of Serbian and Bosnian (use Serbian Latin as default)",
    Language.UNKNOWN:    "Serbian (as safe default)",
}

_SYSTEM_PROMPT = """\
You are a community outreach specialist for MTEL, a Balkan-language satellite
TV provider serving the Balkan diaspora in Turkey.

MARKET RESEARCH CONTEXT (GENAR, May 2026, n=1 200):
- MTEL is 144 TL cheaper per month than the next-cheapest competitor
- 66.4% of Balkan diaspora in Turkey want a cheaper TV package
- Only 8.6% are currently aware of MTEL (huge awareness gap)
- 53.3% of MTEL subscribers discovered it via friends / word of mouth

Campaign type instructions:
  price_hook  → lead with the 144 TL savings, mention the Balkan channel lineup
  referral    → invite the community to share with friends; cite word-of-mouth stat
  survey      → polite 3-question interest survey; don't hard-sell
  awareness   → brand introduction; emphasise Balkan content in native language

Rules:
  - Write ONLY in the language specified in the request.
  - Keep the message under 200 words.
  - Warm, community-appropriate tone (not corporate).
  - Do NOT include any contact details, links, or phone numbers — those are added later.
  - If a reward is offered, mention it naturally.
  - message_subject is only needed for email channel; otherwise null.

Return ONLY valid JSON:
{
  "generated_message": "...",
  "message_subject": "..." or null,
  "campaign_type": "price_hook|referral|survey|awareness",
  "reward_type": "discount|free_trial|gift|cash|none",
  "generator_notes": ["...", "..."]
}
No markdown. No explanation. No extra keys.
"""


def _select_campaign_type(audience: AudienceProfile, community: Community) -> CampaignType:
    """
    Pick the best campaign type deterministically before calling the LLM.
    The LLM may override this in its response; this is the suggested default.
    """
    score = audience.switching_likelihood_score
    if score >= 0.65:
        return CampaignType.PRICE_HOOK
    elif community.activity_score >= 0.60:
        return CampaignType.REFERRAL
    elif score < 0.35:
        return CampaignType.AWARENESS
    else:
        return CampaignType.SURVEY


def _build_prompt(community: Community, audience: AudienceProfile) -> str:
    suggested_type = _select_campaign_type(audience, community)
    lang_label = _LANG_LABEL.get(community.language, "Serbian")

    data = {
        "community_name":     community.name,
        "platform":           community.platform.value,
        "language":           lang_label,
        "city":               community.city,
        "member_count":       community.member_count,
        "audience_profile":   audience.estimated_subscriber_profile,
        "switching_score":    audience.switching_likelihood_score,
        "suggested_campaign": suggested_type.value,
        "market_research":    MARKET_RESEARCH,
        "channel_hint": (
            "email" if community.platform.value == "other" else community.platform.value
        ),
    }
    return json.dumps(data, indent=2, ensure_ascii=False)


def _parse_response(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw)


def _fallback_message(community: Community, campaign_type: CampaignType) -> str:
    """Deterministic fallback when AI call fails."""
    lang = community.language
    if lang == Language.BOSNIAN:
        return (
            "Zdravo! Da li znate za MTEL — Balkansku televiziju u Turskoj? "
            "Kontaktirajte nas za više informacija."
        )
    elif lang == Language.CROATIAN:
        return (
            "Pozdrav! Jeste li čuli za MTEL — balkansku TV u Turskoj? "
            "Javite se za više detalja."
        )
    elif lang == Language.MACEDONIAN:
        return (
            "Здраво! Дали сте слушнале за МТЕЛ — балканска ТВ во Турција? "
            "Контактирајте не за повеќе информации."
        )
    elif lang == Language.ALBANIAN:
        return (
            "Përshëndetje! Keni dëgjuar për MTEL — televizionin ballkanik në Turqi? "
            "Na kontaktoni për më shumë informacion."
        )
    else:
        # Serbian / Mixed / Unknown — safe default
        return (
            "Zdravo! Da li ste čuli za MTEL — balkansku televiziju u Turskoj? "
            "Kontaktirajte nas za više informacija."
        )


def generate_campaign(
    community: Community,
    audience: AudienceProfile,
    client: anthropic.Anthropic,
    model: str = "claude-haiku-4-5-20251001",
) -> Campaign:
    """
    Call Claude to generate a campaign message for a community.

    Args:
        community: Community object (language, platform, city, size).
        audience:  AudienceProfile from Agent 2.
        client:    Anthropic client (injected by workflow).
        model:     Haiku for cost efficiency.

    Returns:
        Campaign with generated_message, campaign_type, reward_type, notes.
        Falls back to a deterministic template message on any error.
    """
    cid    = community.community_id
    prompt = _build_prompt(community, audience)

    logger.debug(
        "\n┌─ Message Generator ── %s ────────────────────────\n"
        "│ Name     : %s  Lang=%s\n"
        "│ Score    : %.2f  Suggested: %s\n"
        "└──────────────────────────────────────────────────",
        cid, community.name, community.language.value,
        audience.switching_likelihood_score,
        _select_campaign_type(audience, community).value,
    )

    try:
        _t0 = time.perf_counter()
        response = client.messages.create(
            model=model,
            max_tokens=600,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        _elapsed_ms = (time.perf_counter() - _t0) * 1000
        raw = response.content[0].text

        logger.debug(
            "\n┌─ Message Generator ── RESPONSE ── %s ──────────\n"
            "│ Latency : %.0f ms  tokens in=%d out=%d\n"
            "│ Output  : %s\n"
            "└──────────────────────────────────────────────────",
            cid, _elapsed_ms,
            response.usage.input_tokens, response.usage.output_tokens,
            raw[:400],
        )

        obj = _parse_response(raw)

        # Parse campaign_type — fall back to suggestion if LLM returns invalid value
        try:
            campaign_type = CampaignType(obj.get("campaign_type", "awareness"))
        except ValueError:
            campaign_type = _select_campaign_type(audience, community)

        try:
            reward_type = RewardType(obj.get("reward_type", "none"))
        except ValueError:
            reward_type = RewardType.NONE

        return Campaign(
            target_community_id=cid,
            campaign_type=campaign_type,
            target_language=community.language,
            reward_type=reward_type,
            generated_message=obj["generated_message"],
            message_subject=obj.get("message_subject") or None,
            generator_notes=list(obj.get("generator_notes", [])),
        )

    except Exception as exc:
        logger.error("Message Generator failed for %s: %s", cid, exc)
        fallback_type = _select_campaign_type(audience, community)
        return Campaign(
            target_community_id=cid,
            campaign_type=fallback_type,
            target_language=community.language,
            reward_type=RewardType.NONE,
            generated_message=_fallback_message(community, fallback_type),
            message_subject=None,
            generator_notes=["Fallback deterministic message — AI call failed."],
        )

"""
models/lead_acquisition.py

Data shapes for Tab 3: Acquisition Intelligence Agent.

A "community" is an online group (Facebook group, Instagram account, etc.)
identified as a pool of potential MTEL subscribers from the Balkan diaspora
in Turkey. The pipeline scores communities, profiles their audience, generates
personalised campaign messages, and recommends an outreach channel — all
requiring human approval before anything is sent.

Data flow:
    Community               (discovered by Agent 1: scraper)
        -> AudienceProfile  (scored by Agent 2: community scorer)
        -> Campaign         (drafted by Agent 3: message generator)
        -> ActionRecommendation  (decided by Agent 4: channel recommender)
        -> Tab3Result       (aggregated for UI display)
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Research constants (GENAR survey, May 2026, n=1 200)
# ---------------------------------------------------------------------------
# These numbers are embedded in agent prompts to ground AI reasoning in
# verified market data. Centralise them here so any agent that references
# them imports from a single authoritative source.

MARKET_RESEARCH: dict[str, object] = {
    "price_gap_tl": 144,                   # TL cheaper than next-cheapest competitor
    "want_cheaper_package_pct": 66.4,       # % of respondents who want a cheaper option
    "balkan_origin_mtel_awareness_pct": 8.6, # % of Balkan-origin people aware of MTEL
    "word_of_mouth_acquisition_pct": 53.3,  # % who hear about MTEL via friends/family
    "survey_n": 1_200,
    "survey_month": "2026-05",
    "source": "GENAR, May 2026",
}


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Platform(str, Enum):
    """Social platform on which the community was discovered."""

    FACEBOOK  = "facebook"
    INSTAGRAM = "instagram"
    WHATSAPP  = "whatsapp"   # reserved — WhatsApp group scraping not yet wired in
    OTHER     = "other"


class Language(str, Enum):
    """
    Balkan diaspora language spoken in the target community.

    Codes follow ISO 639-1 conventions. Albanian uses 'sq' (Shqip).
    """

    BOSNIAN    = "bs"
    SERBIAN    = "sr"
    ALBANIAN   = "sq"
    CROATIAN   = "hr"
    MACEDONIAN = "mk"
    MIXED      = "mixed"   # community uses more than one Balkan language
    UNKNOWN    = "unknown"


class CampaignType(str, Enum):
    """
    The acquisition angle the campaign message will use.

    price_hook  — leads with the 144 TL price advantage over competitors
    referral    — word-of-mouth hook (53.3% of MTEL subscribers found via friends)
    survey      — brief questionnaire to qualify interest and gather data
    awareness   — brand introduction for communities with <10% MTEL awareness
    """

    PRICE_HOOK = "price_hook"
    REFERRAL   = "referral"
    SURVEY     = "survey"
    AWARENESS  = "awareness"


class RewardType(str, Enum):
    """Incentive offered to community members in the campaign."""

    DISCOUNT    = "discount"      # percentage or fixed-amount reduction
    FREE_TRIAL  = "free_trial"    # n-day trial at no cost
    GIFT        = "gift"          # physical or digital gift
    CASH        = "cash"          # referral cash reward
    NONE        = "none"          # no reward offered


class Channel(str, Enum):
    """
    Recommended outreach channel selected by Agent 4.

    group_post — post directly in the community feed (highest reach, lowest trust)
    admin_dm   — private DM to group admin (lowest reach, highest trust)
    ig_story   — Instagram Story mention or paid boost
    email      — direct email if contact info is available
    """

    GROUP_POST = "group_post"
    ADMIN_DM   = "admin_dm"
    IG_STORY   = "ig_story"
    EMAIL      = "email"


# ---------------------------------------------------------------------------
# Community (Agent 1 output: scraper)
# ---------------------------------------------------------------------------


class Community(BaseModel):
    """
    A discovered online community that may contain prospective MTEL subscribers.

    Produced by Agent 1 (scraper). Raw fields — scoring happens in Agent 2.
    """

    community_id: str = Field(..., description="Unique identifier, e.g. FB group ID or IG handle.")
    platform: Platform
    name: str = Field(..., description="Display name of the group or account.")
    url: Optional[str] = Field(None, description="Direct URL to the community page.")
    language: Language = Field(Language.UNKNOWN, description="Dominant language detected in the community.")
    city: Optional[str] = Field(None, description="Turkish city the community is associated with (e.g. 'Istanbul', 'Ankara').")
    member_count: Optional[int] = Field(None, ge=0, description="Number of members / followers at scrape time.")
    activity_score: float = Field(
        0.0, ge=0.0, le=1.0,
        description="Recency and volume of posts/interactions, normalised to [0, 1].",
    )
    language_match_score: float = Field(
        0.0, ge=0.0, le=1.0,
        description="How well the community language aligns with the target campaign language. 1 = exact match.",
    )
    city_overlap_score: float = Field(
        0.0, ge=0.0, le=1.0,
        description="How closely the community's city matches MTEL's primary target cities. 1 = top-priority city.",
    )
    scraper_notes: list[str] = Field(
        default_factory=list,
        description="Observations from the scraper agent (e.g. 'private group, admin approval required').",
    )

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, v: object) -> str:
        if isinstance(v, str):
            return v.strip()
        return str(v)

    model_config = {"str_strip_whitespace": True}


# ---------------------------------------------------------------------------
# AudienceProfile (Agent 2 output: community scorer)
# ---------------------------------------------------------------------------


class AudienceProfile(BaseModel):
    """
    Audience intelligence produced by Agent 2 for a single Community.

    Estimates what share of the community are likely unsubscribed Balkan diaspora
    who could convert to MTEL, and explains the reasoning with research-backed factors.
    """

    community_id: str = Field(..., description="Foreign key to Community.community_id.")
    estimated_subscriber_profile: str = Field(
        ...,
        description=(
            "Narrative description of the estimated audience composition "
            "(e.g. 'Predominantly working-age Bosnian men in Istanbul, high likelihood of "
            "existing Turkish IPTV subscription based on income proxy')."
        ),
    )
    switching_likelihood_score: float = Field(
        ..., ge=0.0, le=1.0,
        description=(
            "Estimated probability that a community member would switch to or adopt MTEL. "
            "Grounded in GENAR research (66.4% want cheaper package, 144 TL price gap)."
        ),
    )
    supporting_research_factors: list[str] = Field(
        default_factory=list,
        description=(
            "Specific GENAR data points or observable community signals that support the score "
            "(e.g. '66.4% of survey respondents want a cheaper package — price hook applicable')."
        ),
    )
    scorer_notes: list[str] = Field(
        default_factory=list,
        description="Additional reasoning notes from Agent 2.",
    )


# ---------------------------------------------------------------------------
# Campaign (Agent 3 output: message generator)
# ---------------------------------------------------------------------------


class Campaign(BaseModel):
    """
    A draft outreach campaign produced by Agent 3 for a specific community.

    The generated_message is NOT sent until a human operator approves it.
    Language must match the target community's dominant language.
    """

    target_community_id: str = Field(..., description="Foreign key to Community.community_id.")
    campaign_type: CampaignType
    target_language: Language = Field(
        ...,
        description="Language in which the message is written. Must match community language.",
    )
    reward_type: RewardType = Field(RewardType.NONE)
    generated_message: str = Field(
        ...,
        description="Full draft message text, ready for human review. Not sent automatically.",
    )
    message_subject: Optional[str] = Field(
        None,
        description="Subject line for email channel; None for social-media posts.",
    )
    generator_notes: list[str] = Field(
        default_factory=list,
        description="Notes from Agent 3 (e.g. 'used informal Bosnian register', 'price gap cited in TL').",
    )

    @model_validator(mode="after")
    def message_not_empty(self) -> Campaign:
        if not self.generated_message.strip():
            raise ValueError("generated_message must not be empty.")
        return self


# ---------------------------------------------------------------------------
# ActionRecommendation (Agent 4 output: channel recommender)
# ---------------------------------------------------------------------------


class ActionRecommendation(BaseModel):
    """
    Channel and priority recommendation produced by Agent 4 for a community.

    Combines community size, activity, and audience profile to decide where
    and how urgently to deploy the campaign.
    """

    community_id: str = Field(..., description="Foreign key to Community.community_id.")
    recommended_channel: Channel
    estimated_reach: int = Field(
        ..., ge=0,
        description="Estimated number of unique community members the message will reach.",
    )
    priority_rank: int = Field(
        ..., ge=1,
        description="Relative priority across all communities in this run. 1 = highest priority.",
    )
    confidence_score: float = Field(
        ..., ge=0.0, le=1.0,
        description="Agent 4's confidence in this channel recommendation.",
    )
    rationale: str = Field(
        ...,
        description="One-sentence explanation of why this channel was chosen.",
    )
    recommender_notes: list[str] = Field(
        default_factory=list,
        description="Additional notes from Agent 4 (e.g. 'admin contact found in bio').",
    )


# ---------------------------------------------------------------------------
# Composite pipeline result (one community, full pipeline)
# ---------------------------------------------------------------------------


class Tab3Result(BaseModel):
    """
    Complete result for a single community after running the full Tab 3 pipeline.

    Workflows produce a list of Tab3Result objects; the UI renders each one
    as a community card with an expandable approval queue.
    """

    community: Community
    audience: AudienceProfile
    campaign: Campaign
    recommendation: ActionRecommendation

    @property
    def community_id(self) -> str:
        return self.community.community_id

    @property
    def display_name(self) -> str:
        return self.community.name

    @property
    def approved_for_send(self) -> bool:
        """
        Always False until a human operator explicitly approves.

        Extension point: set by the UI approval queue handler, not by any agent.
        """
        return False

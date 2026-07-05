"""
services/serper_search.py

Serper API client for Tab 3: Acquisition Intelligence — community discovery.

Two search modes:
    1. Organic — Google search for Facebook groups / Instagram accounts matching
       Balkan diaspora + Turkey keywords. Returns list[Community].

    2. Places — Google Maps search for physical Balkan diaspora venues in Turkish
       cities (cultural centres, associations, restaurants). Returns list[Community]
       with Platform.OTHER and notes flagging the physical location.

Both return Community objects compatible with the Tab 3 pipeline unchanged.

Usage:
    from services.serper_search import discover_communities
    communities = discover_communities(api_key="...", max_communities=20)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import urllib.request
from typing import Callable, Optional

from models.lead_acquisition import Community, Language, Platform

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Target queries
# ---------------------------------------------------------------------------

_ORGANIC_QUERIES: list[str] = [
    "Bosanci Istanbul Facebook group members",
    "Srbi u Turskoj Facebook group",
    "Jugosloveni Istanbul Facebook",
    "Makedonci Turska Facebook grupa",
    "Balkanlılar İstanbul Facebook grubu",
    "Balkan diaspora Istanbul Facebook group",
    "Bosnian community Turkey Instagram account",
    "Kosovari Turska Facebook",
    "Bošnjaci Turska Facebook",
    "Srbi Bursa Facebook",
    "Bosna Hersek Turska Facebook topluluğu",
    "Albanci Turska Facebook",
    "Serbian diaspora Turkey Instagram",
]

_PLACES_QUERIES: list[str] = [
    "Bosna Hersek kültür merkezi Istanbul",
    "Balkan restaurant Istanbul",
    "Bosnian cultural center Istanbul",
    "Makedonya derneği Istanbul",
    "Srbistan kültür merkezi Istanbul",
    "Balkan diaspora association Bursa",
    "Yugoslav cultural centre Ankara",
]


# ---------------------------------------------------------------------------
# NLP helpers — language & city detection
# ---------------------------------------------------------------------------

_LANG_KEYWORDS: dict[Language, list[str]] = {
    Language.BOSNIAN:    ["bosanc","bosna","bosnian","bošnjac","bih","bosna-hercegovina"],
    Language.SERBIAN:    ["srb","srbija","serbian","srbi"],
    Language.CROATIAN:   ["hrvat","hrvatska","croatian","croat"],
    Language.MACEDONIAN: ["makedon","macedonian","macedoni","severna makedonija"],
    Language.ALBANIAN:   ["albanc","kosov","shqip","albanian","albanija"],
    Language.MIXED:      ["balkan","jugoslav","yugoslav","ex-yu","exyu","balkanlı"],
}

_CITY_KEYWORDS: dict[str, list[str]] = {
    "Istanbul":  ["istanbul","İstanbul"],
    "Bursa":     ["bursa"],
    "Ankara":    ["ankara"],
    "Izmir":     ["izmir","İzmir"],
    "Edirne":    ["edirne"],
    "Antalya":   ["antalya"],
    "Kocaeli":   ["kocaeli","izmit"],
    "Gaziantep": ["gaziantep"],
}

_CITY_OVERLAP: dict[str, float] = {
    "Istanbul":  1.00,
    "Bursa":     0.85,
    "Edirne":    0.70,
    "Ankara":    0.60,
    "Izmir":     0.55,
    "Kocaeli":   0.50,
    "Antalya":   0.45,
    "Gaziantep": 0.40,
}

_LANG_MATCH: dict[Language, float] = {
    Language.SERBIAN:    0.95,
    Language.BOSNIAN:    0.92,
    Language.CROATIAN:   0.88,
    Language.MACEDONIAN: 0.82,
    Language.ALBANIAN:   0.75,
    Language.MIXED:      0.70,
    Language.UNKNOWN:    0.40,
}


def _detect_language(text: str) -> Language:
    """Return the most likely Language from keywords in text."""
    lower = text.lower()
    scores: dict[Language, int] = {}
    for lang, kws in _LANG_KEYWORDS.items():
        hit = sum(1 for kw in kws if kw in lower)
        if hit:
            scores[lang] = hit
    if not scores:
        return Language.UNKNOWN
    # MIXED wins only if no more specific language dominates
    best = max(scores, key=lambda l: scores[l])
    return best


def _detect_city(text: str) -> Optional[str]:
    """Return the first recognised Turkish city found in text."""
    lower = text.lower()
    for city, kws in _CITY_KEYWORDS.items():
        if any(kw.lower() in lower for kw in kws):
            return city
    return None


def _extract_member_count(text: str) -> Optional[int]:
    """Parse member/follower count from snippet text."""
    # Patterns: "5,200 members", "5.2K members", "52K followers", "5 200 üye"
    text_lower = text.lower()
    m = re.search(r"([\d,\.]+)\s*k\s*(?:member|follower|abone|üye)", text_lower)
    if m:
        try:
            return int(float(m.group(1).replace(",","")) * 1000)
        except ValueError:
            pass
    m = re.search(r"([\d][,\.\d]*)\s*(?:member|follower|abone|üye)", text_lower)
    if m:
        try:
            return int(m.group(1).replace(",","").replace(".",""))
        except ValueError:
            pass
    return None


def _detect_platform(url: str, title: str) -> Optional[Platform]:
    lower_url = url.lower(); lower_title = title.lower()
    if "facebook.com" in lower_url or "fb.com" in lower_url:
        return Platform.FACEBOOK
    if "instagram.com" in lower_url:
        return Platform.INSTAGRAM
    if "facebook" in lower_title:
        return Platform.FACEBOOK
    if "instagram" in lower_title:
        return Platform.INSTAGRAM
    return None


def _make_id(seed: str, prefix: str = "SERP") -> str:
    return f"{prefix}-{hashlib.md5(seed.encode()).hexdigest()[:8].upper()}"


def _activity_from_position(position: int, member_count: Optional[int]) -> float:
    """Estimate activity score from result rank and member size."""
    base = max(0.3, 0.90 - (position - 1) * 0.05)
    if member_count and member_count >= 5000:
        base = min(1.0, base + 0.05)
    elif member_count and member_count < 500:
        base = max(0.3, base - 0.10)
    return round(base, 2)


# ---------------------------------------------------------------------------
# Serper HTTP client
# ---------------------------------------------------------------------------

def _serper_post(endpoint: str, payload: dict, api_key: str, timeout: int = 15) -> dict:
    """POST to Serper API and return parsed JSON."""
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        f"https://google.serper.dev/{endpoint}",
        data=data,
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        logger.error("Serper %s failed: %s", endpoint, exc)
        return {}


# ---------------------------------------------------------------------------
# Organic search → Community objects
# ---------------------------------------------------------------------------

def _parse_organic(response: dict, query: str) -> list[Community]:
    """Convert organic search hits to Community objects."""
    out: list[Community] = []
    for item in response.get("organic", []):
        url     = item.get("link","")
        title   = item.get("title","")
        snippet = item.get("snippet","") or ""
        pos     = item.get("position", 10)

        platform = _detect_platform(url, title)
        if platform not in (Platform.FACEBOOK, Platform.INSTAGRAM):
            continue  # skip non-social results

        full_text = f"{query} {title} {snippet}"
        lang     = _detect_language(full_text)
        city     = _detect_city(full_text)
        members  = _extract_member_count(snippet)
        activity = _activity_from_position(pos, members)

        # Clean up title (remove "| Facebook" etc.)
        name = re.sub(r"\s*[|·]\s*(Facebook|Instagram).*$", "", title, flags=re.IGNORECASE).strip()
        if not name:
            name = title

        community = Community(
            community_id=_make_id(url or name),
            platform=platform,
            name=name,
            url=url or None,
            language=lang,
            city=city,
            member_count=members,
            activity_score=activity,
            language_match_score=_LANG_MATCH.get(lang, 0.40),
            city_overlap_score=_CITY_OVERLAP.get(city, 0.10) if city else 0.10,
            scraper_notes=[
                f"Serper organic result (position {pos})",
                f"Query: {query}",
                *([] if not snippet else [f"Snippet: {snippet[:120]}"]),
            ],
        )
        out.append(community)
    return out


# ---------------------------------------------------------------------------
# Places search → Community objects
# ---------------------------------------------------------------------------

def _parse_places(response: dict, query: str) -> list[Community]:
    """Convert Google Maps place hits to Community objects."""
    out: list[Community] = []
    for item in response.get("places", []):
        title    = item.get("title","")
        address  = item.get("address","") or ""
        rating   = float(item.get("rating",0) or 0)
        reviews  = int(item.get("ratingCount",0) or 0)
        category = item.get("category","") or ""

        full_text = f"{query} {title} {address}"
        lang = _detect_language(full_text)
        city = _detect_city(address) or _detect_city(title) or _detect_city(query)

        # Activity proxy: normalise rating (0–5) and review count
        activity = round(min(1.0, (rating / 5.0) * 0.7 + min(reviews, 500) / 500 * 0.3), 2)

        community = Community(
            community_id=_make_id(f"{title}{address}", prefix="PLCE"),
            platform=Platform.OTHER,
            name=title,
            url=None,
            language=lang,
            city=city,
            member_count=None,
            activity_score=activity,
            language_match_score=_LANG_MATCH.get(lang, 0.40),
            city_overlap_score=_CITY_OVERLAP.get(city, 0.10) if city else 0.10,
            scraper_notes=[
                f"Google Maps / Places result",
                f"Category: {category}" if category else "Category: unknown",
                f"Address: {address}" if address else "",
                f"Rating: {rating}/5 ({reviews} reviews)" if rating else "",
                f"Query: {query}",
            ],
        )
        out.append(community)
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def discover_communities(
    api_key: str,
    max_communities: int = 20,
    include_places: bool = True,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    request_delay: float = 0.4,
) -> list[Community]:
    """
    Run organic + places Serper searches and return a deduplicated list of
    Community objects ready for the Tab 3 pipeline.

    Args:
        api_key:           Serper API key (SERPER_API_KEY from .env).
        max_communities:   Cap on total communities returned.
        include_places:    If True, also run Places queries.
        progress_callback: callable(current, total, label) for UI progress.
        request_delay:     Seconds to sleep between API calls (rate limit safety).

    Returns:
        list[Community] sorted by city_overlap_score * language_match_score descending.
    """
    all_queries   = list(_ORGANIC_QUERIES)
    places_queries = list(_PLACES_QUERIES) if include_places else []
    total_calls   = len(all_queries) + len(places_queries)
    done          = 0
    seen_ids: set[str] = set()
    communities: list[Community] = []

    if progress_callback:
        progress_callback(0, total_calls, "Starting Serper community discovery…")

    # ── Organic searches ──────────────────────────────────────────────────
    for query in all_queries:
        if len(communities) >= max_communities * 2:
            break  # gathered enough to cull later
        logger.debug("Serper organic: %s", query)
        resp = _serper_post("search", {"q": query, "gl": "tr", "hl": "tr", "num": 10}, api_key)
        new  = _parse_organic(resp, query)
        for c in new:
            if c.community_id not in seen_ids:
                seen_ids.add(c.community_id)
                communities.append(c)
        done += 1
        if progress_callback:
            progress_callback(done, total_calls, f"Searched: {query[:50]}…")
        if done < total_calls:
            time.sleep(request_delay)

    # ── Places searches ───────────────────────────────────────────────────
    for query in places_queries:
        logger.debug("Serper places: %s", query)
        resp = _serper_post("places", {"q": query, "gl": "tr", "hl": "tr"}, api_key)
        new  = _parse_places(resp, query)
        for c in new:
            if c.community_id not in seen_ids:
                seen_ids.add(c.community_id)
                communities.append(c)
        done += 1
        if progress_callback:
            progress_callback(done, total_calls, f"Places: {query[:50]}…")
        if done < total_calls:
            time.sleep(request_delay)

    # ── Rank and cap ──────────────────────────────────────────────────────
    communities.sort(
        key=lambda c: c.city_overlap_score * c.language_match_score * c.activity_score,
        reverse=True,
    )
    result = communities[:max_communities]

    logger.info("Serper discovery: %d raw results → %d communities", len(communities), len(result))
    if progress_callback:
        progress_callback(total_calls, total_calls, f"Found {len(result)} communities via Serper")

    return result


def search_places_only(api_key: str, queries: Optional[list[str]] = None) -> list[Community]:
    """Run only the Places queries and return results. Useful for standalone map view."""
    queries = queries or _PLACES_QUERIES
    seen: set[str] = set()
    out:  list[Community] = []
    for q in queries:
        resp = _serper_post("places", {"q": q, "gl": "tr", "hl": "tr"}, api_key)
        for c in _parse_places(resp, q):
            if c.community_id not in seen:
                seen.add(c.community_id)
                out.append(c)
        time.sleep(0.3)
    return out

"""
ui/tab3.py

Gradio UI for Tab 3: Acquisition Intelligence Agent.

Layout:
    Pipeline diagram (full width, decorative)
    Controls + Run button (full width)
    KPI cards (full width)
    Charts row: Score distribution + Turkey community map
    Main row:
        Left  (2/3) : Priority-sorted Dataframe table
        Right (1/3) : AI Pipeline Summary
                      Selected Community Review (native gr.Tabs)
                      Human Approval Queue + Approve/Reject buttons
"""

from __future__ import annotations

import os
from collections import Counter
from typing import Optional

import gradio as gr

try:
    import plotly.graph_objects as go
    _HAS_PLOTLY = True
except ImportError:
    _HAS_PLOTLY = False

from models.lead_acquisition import Channel, CampaignType, Language, Platform, Tab3Result
from services.acquisition_demo_data import load_demo_communities
from workflows.tab3_workflow import run_tab3_pipeline, summarise_tab3


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_TAB_CSS = """
<style>
label:has(input[type="radio"]) input[type="radio"] { display: none !important; }
label:has(input[type="radio"]) { cursor: pointer; }
label:has(input[type="radio"]:checked) span { color:#e2e8f0 !important; font-weight:600 !important; }
label:has(input[type="radio"]:checked) { background:rgba(99,102,241,0.12) !important; border-radius:6px; }
</style>
"""

_PIPELINE_HTML = """
<div style="background:#1e293b;border:1px solid #334155;border-radius:12px;padding:10px 20px;margin-bottom:14px;">
  <div style="font-size:9px;color:#475569;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;margin-bottom:8px;">Pipeline Flow</div>
  <div style="display:flex;align-items:center;gap:0;">
    <div style="text-align:center;flex:1;"><div style="font-size:18px;">🌐</div><div style="font-size:10px;color:#94a3b8;margin-top:3px;font-weight:500;">Communities</div></div>
    <div style="color:#334155;font-size:14px;margin:0 2px;">──→</div>
    <div style="text-align:center;flex:1;"><div style="font-size:18px;filter:drop-shadow(0 0 5px #6366f1);">🤖</div><div style="font-size:10px;color:#6366f1;margin-top:3px;font-weight:600;">Scorer</div></div>
    <div style="color:#334155;font-size:14px;margin:0 2px;">──→</div>
    <div style="text-align:center;flex:1;"><div style="font-size:18px;filter:drop-shadow(0 0 5px #6366f1);">✍️</div><div style="font-size:10px;color:#6366f1;margin-top:3px;font-weight:600;">Message Gen</div></div>
    <div style="color:#334155;font-size:14px;margin:0 2px;">──→</div>
    <div style="text-align:center;flex:1;"><div style="font-size:18px;filter:drop-shadow(0 0 5px #6366f1);">📡</div><div style="font-size:10px;color:#6366f1;margin-top:3px;font-weight:600;">Channel Rec</div></div>
    <div style="color:#334155;font-size:14px;margin:0 2px;">──→</div>
    <div style="text-align:center;flex:1;"><div style="font-size:18px;">👤</div><div style="font-size:10px;color:#94a3b8;margin-top:3px;font-weight:500;">Approval</div></div>
    <div style="color:#334155;font-size:14px;margin:0 2px;">──→</div>
    <div style="text-align:center;flex:1;"><div style="font-size:18px;">📤</div><div style="font-size:10px;color:#94a3b8;margin-top:3px;font-weight:500;">Send</div></div>
  </div>
</div>
"""


# ---------------------------------------------------------------------------
# Display constants
# ---------------------------------------------------------------------------

_CHANNEL_LABEL = {
    Channel.GROUP_POST: "Group Post",
    Channel.ADMIN_DM:   "Admin DM",
    Channel.IG_STORY:   "IG Story",
    Channel.EMAIL:      "Email",
}
_CAMPAIGN_LABEL = {
    CampaignType.PRICE_HOOK: "💰 Price Hook",
    CampaignType.REFERRAL:   "🤝 Referral",
    CampaignType.SURVEY:     "📋 Survey",
    CampaignType.AWARENESS:  "📣 Awareness",
}
_LANG_FLAG = {
    Language.SERBIAN:    "🇷🇸 SR",
    Language.BOSNIAN:    "🇧🇦 BS",
    Language.CROATIAN:   "🇭🇷 HR",
    Language.MACEDONIAN: "🇲🇰 MK",
    Language.ALBANIAN:   "🇦🇱 SQ",
    Language.MIXED:      "🌐 MIX",
    Language.UNKNOWN:    "❓",
}


# ---------------------------------------------------------------------------
# KPI cards
# ---------------------------------------------------------------------------

_KPI_PLACEHOLDER = '<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;padding:16px;text-align:center;color:#475569;margin-bottom:14px;font-size:13px;">Run the pipeline to see metrics</div>'

def _kpi_html(stats: dict) -> str:
    reach = stats.get("total_reach",0); avg = stats.get("avg_score",0.0)
    cards = [
        ("🌐","Total Communities",str(stats["total"]),          "#6366f1"),
        ("🔴","High Priority",    str(stats["high_priority"]),  "#ef4444"),
        ("📘","Facebook Groups",  str(stats["facebook"]),       "#3b82f6"),
        ("📷","Instagram Accs",   str(stats["instagram"]),      "#ec4899"),
        ("👁️","Total Reach",      f"{reach:,}",                "#10b981"),
        ("📊","Avg Switch Score", f"{avg:.0%}",                 "#f59e0b"),
    ]
    items = ""
    for icon, label, value, color in cards:
        items += (
            '<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;padding:14px 16px;min-width:0;">' +
            '<div style="font-size:10px;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:8px;">' +
            icon + " " + label + '</div>' +
            '<div style="font-size:26px;font-weight:700;color:' + color + ';line-height:1;">' + value + '</div></div>'
        )
    return '<div style="display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin-bottom:14px;">' + items + '</div>'


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

_CITY_COORDS: dict[str, tuple[float, float]] = {
    "Istanbul":(41.0082,28.9784),"Bursa":(40.1885,29.0610),
    "Ankara":(39.9334,32.8597),"Izmir":(38.4192,27.1287),
    "Edirne":(41.6818,26.5623),"Antalya":(36.8969,30.7133),
}

def _score_chart(results: list[Tab3Result]):
    if not _HAS_PLOTLY or not results: return None
    buckets = {"0–25%":0,"25–50%":0,"50–75%":0,"75–100%":0}
    for r in results:
        s = r.audience.switching_likelihood_score
        if s < 0.25: buckets["0–25%"] += 1
        elif s < 0.50: buckets["25–50%"] += 1
        elif s < 0.75: buckets["50–75%"] += 1
        else: buckets["75–100%"] += 1
    fig = go.Figure(go.Bar(
        x=list(buckets.keys()), y=list(buckets.values()),
        marker_color=["#ef4444","#f59e0b","#22c55e","#10b981"],
        text=list(buckets.values()), textposition="outside",
        textfont=dict(color="#e2e8f0",size=12),
        hovertemplate="%{x}: %{y} communities<extra></extra>",
    ))
    fig.update_layout(
        plot_bgcolor="#0f172a", paper_bgcolor="#1e293b",
        font=dict(color="#94a3b8",size=11,family="Inter, sans-serif"),
        margin=dict(l=10,r=10,t=36,b=10), height=210, showlegend=False,
        title=dict(text="Switching Score Distribution",font=dict(size=12,color="#64748b"),x=0.5,xanchor="center"),
        yaxis=dict(gridcolor="#1e293b",tickfont=dict(color="#94a3b8"),showgrid=True),
        xaxis=dict(tickfont=dict(color="#e2e8f0",size=11),showgrid=False),
    )
    return fig

def _community_map(results: list[Tab3Result]):
    if not _HAS_PLOTLY or not results: return None
    city_data: dict[str, list] = {}
    for r in results:
        city = (r.community.city or "").strip().title()
        if city and city in _CITY_COORDS:
            city_data.setdefault(city, []).append(r)
    if not city_data: return None
    lats, lons, texts, sizes = [], [], [], []
    for city, crs in city_data.items():
        lat, lon = _CITY_COORDS[city]; count = len(crs)
        avg_score = sum(r.audience.switching_likelihood_score for r in crs) / count
        lats.append(lat); lons.append(lon)
        texts.append(f"{city}: {count} communities<br>Avg score: {avg_score:.0%}")
        sizes.append(max(10, min(40, count*8)))
    fig = go.Figure(go.Scattergeo(
        lat=lats, lon=lons, text=texts, hovertemplate="%{text}<extra></extra>",
        mode="markers", marker=dict(size=sizes,color="#6366f1",opacity=0.85,line=dict(width=1,color="#818cf8")),
    ))
    fig.update_layout(
        geo=dict(scope="asia",center=dict(lat=39.0,lon=35.0),projection_scale=4.5,
                 showland=True,landcolor="#1e293b",showocean=True,oceancolor="#0f172a",
                 showcoastlines=True,coastlinecolor="#334155",showframe=False,bgcolor="#0f172a",
                 showcountries=True,countrycolor="#334155"),
        paper_bgcolor="#1e293b", font=dict(color="#94a3b8",size=11),
        margin=dict(l=0,r=0,t=36,b=0), height=210,
        title=dict(text="Community Distribution — Turkey",font=dict(size=12,color="#64748b"),x=0.5,xanchor="center"),
    )
    return fig


# ---------------------------------------------------------------------------
# Table builder
# ---------------------------------------------------------------------------

_TABLE_HEADERS = ["#","Community","Platform","Lang","City","Members","Switch %","Campaign","Channel","Est. Reach"]

def _build_table_data(results: list[Tab3Result]) -> list[list]:
    rows = []
    for r in results:
        score   = r.audience.switching_likelihood_score
        members = r.community.member_count or 0
        channel = _CHANNEL_LABEL.get(r.recommendation.recommended_channel,"—")
        camp    = _CAMPAIGN_LABEL.get(r.campaign.campaign_type, r.campaign.campaign_type.value)
        lang    = _LANG_FLAG.get(r.community.language,"❓")
        plat    = "📘 FB" if r.community.platform == Platform.FACEBOOK else "📷 IG"
        rows.append([
            r.recommendation.priority_rank, r.community.name, plat, lang,
            r.community.city or "—", f"{members:,}", f"{score:.0%}",
            camp, channel, f"{r.recommendation.estimated_reach:,}",
        ])
    return rows


# ---------------------------------------------------------------------------
# Pipeline summary
# ---------------------------------------------------------------------------

def _summary_html(results: list[Tab3Result], stats: dict) -> str:
    if not results:
        return '<div style="color:#475569;padding:16px;font-size:13px;">No results yet.</div>'
    top = results[0]
    camp_counts = Counter(r.campaign.campaign_type.value for r in results)
    chan_counts  = Counter(r.recommendation.recommended_channel.value for r in results)
    html = (
        '<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;padding:16px;">' +
        '<div style="font-size:12px;font-weight:600;color:#e2e8f0;margin-bottom:10px;">🤖 Pipeline Summary</div>' +
        '<div style="margin-bottom:12px;">' +
        '<div style="font-size:10px;color:#64748b;margin-bottom:3px;text-transform:uppercase;letter-spacing:0.05em;">Top Community</div>' +
        f'<div style="font-size:13px;font-weight:600;color:#e2e8f0;">{top.community.name}</div>' +
        f'<div style="font-size:11px;color:#94a3b8;">Score: {top.audience.switching_likelihood_score:.0%} · {_CHANNEL_LABEL.get(top.recommendation.recommended_channel,"—")}</div>' +
        '</div>' +
        '<div style="margin-bottom:10px;">' +
        '<div style="font-size:10px;color:#64748b;margin-bottom:4px;text-transform:uppercase;letter-spacing:0.05em;">Campaign Mix</div>'
    )
    for ct, cnt in camp_counts.most_common():
        label = _CAMPAIGN_LABEL.get(CampaignType(ct), ct)
        html += (
            '<div style="display:flex;justify-content:space-between;font-size:12px;color:#94a3b8;margin-bottom:2px;">' +
            f'<span>{label}</span><span style="color:#e2e8f0;font-weight:600;">{cnt}</span></div>'
        )
    html += '</div><div>' + '<div style="font-size:10px;color:#64748b;margin-bottom:4px;text-transform:uppercase;letter-spacing:0.05em;">Channel Mix</div>'
    for ch, cnt in chan_counts.most_common():
        label = _CHANNEL_LABEL.get(Channel(ch), ch)
        html += (
            '<div style="display:flex;justify-content:space-between;font-size:12px;color:#94a3b8;margin-bottom:2px;">' +
            f'<span>{label}</span><span style="color:#e2e8f0;font-weight:600;">{cnt}</span></div>'
        )
    html += '</div></div>'
    return html


# ---------------------------------------------------------------------------
# Approval queue
# ---------------------------------------------------------------------------

def _queue_html(results: list[Tab3Result], idx: int, approved: list[int], rejected: list[int]) -> str:
    pending = [i for i in range(len(results)) if i not in approved and i not in rejected]
    if not results:
        return '<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;padding:20px;text-align:center;color:#475569;font-size:13px;">Run the pipeline to populate the queue.</div>'
    if not pending:
        return (
            '<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;padding:20px;text-align:center;">' +
            '<div style="font-size:32px;margin-bottom:8px;">✅</div>' +
            '<div style="color:#10b981;font-size:14px;font-weight:600;">All communities reviewed!</div>' +
            f'<div style="color:#94a3b8;font-size:12px;margin-top:6px;">{len(approved)} approved · {len(rejected)} rejected</div>' +
            '</div>'
        )
    cur_i   = pending[idx % len(pending)]
    r       = results[cur_i]
    score   = r.audience.switching_likelihood_score
    sc_clr  = "#10b981" if score>=0.65 else "#f59e0b" if score>=0.40 else "#ef4444"
    channel = _CHANNEL_LABEL.get(r.recommendation.recommended_channel,"—")
    camp    = _CAMPAIGN_LABEL.get(r.campaign.campaign_type, r.campaign.campaign_type.value)
    preview = r.campaign.generated_message[:180] + ("…" if len(r.campaign.generated_message)>180 else "")
    html = (
        '<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;padding:14px;margin-bottom:8px;">' +
        f'<div style="font-size:10px;color:#6366f1;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:6px;">👤 Pending — {len(pending)} remaining</div>' +
        f'<div style="font-size:14px;font-weight:700;color:#e2e8f0;margin-bottom:6px;">{r.community.name}</div>' +
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;margin-bottom:8px;">' +
        f'<div style="font-size:10px;color:#64748b;">Score</div><div style="font-size:12px;font-weight:700;color:{sc_clr};">{score:.0%}</div>' +
        f'<div style="font-size:10px;color:#64748b;">Channel</div><div style="font-size:11px;color:#e2e8f0;">{channel}</div>' +
        f'<div style="font-size:10px;color:#64748b;">Campaign</div><div style="font-size:11px;color:#e2e8f0;">{camp}</div>' +
        f'<div style="font-size:10px;color:#64748b;">Reach</div><div style="font-size:11px;color:#e2e8f0;">{r.recommendation.estimated_reach:,}</div>' +
        '</div>' +
        f'<div style="background:#0f172a;border-radius:6px;padding:8px;font-size:11px;color:#94a3b8;line-height:1.5;max-height:70px;overflow:hidden;">{preview}</div>' +
        '</div>'
    )
    remaining = [i for i in pending if i != cur_i]
    if remaining:
        html += '<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;padding:10px;max-height:180px;overflow-y:auto;">' + f'<div style="font-size:10px;color:#475569;font-weight:700;text-transform:uppercase;margin-bottom:6px;">Remaining ({len(remaining)})</div>'
        for ri in remaining:
            rr = results[ri]; rs = rr.audience.switching_likelihood_score
            rc = "#10b981" if rs>=0.65 else "#f59e0b" if rs>=0.40 else "#ef4444"
            html += (
                '<div style="display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid #0f172a;">' +
                f'<div style="font-size:12px;color:#94a3b8;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{rr.community.name}</div>' +
                f'<div style="font-size:12px;font-weight:700;color:{rc};min-width:36px;text-align:right;">{rs:.0%}</div></div>'
            )
        html += '</div>'
    return html


# ---------------------------------------------------------------------------
# Review panel builders (right sidebar — replaces modal)
# ---------------------------------------------------------------------------

_REVIEW_PLACEHOLDER = '<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;padding:24px;text-align:center;color:#475569;font-size:13px;">Select a row from the table to review community details.</div>'
_TAB_PLACEHOLDER    = '<div style="color:#475569;font-size:13px;padding:20px;text-align:center;">Select a community to see details here.</div>'

def _review_header_html(r: Tab3Result) -> str:
    score   = r.audience.switching_likelihood_score
    sc_clr  = "#10b981" if score>=0.65 else "#f59e0b" if score>=0.40 else "#ef4444"
    plat    = "📘 Facebook" if r.community.platform==Platform.FACEBOOK else "📷 Instagram"
    lang    = _LANG_FLAG.get(r.community.language,"❓")
    members = f"{r.community.member_count:,}" if r.community.member_count else "—"
    return (
        '<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;padding:14px 16px;">' +
        '<div style="display:flex;justify-content:space-between;align-items:flex-start;">' +
        '<div style="flex:1;min-width:0;">' +
        f'<div style="font-size:15px;font-weight:700;color:#e2e8f0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{r.community.name}</div>' +
        f'<div style="font-size:11px;color:#64748b;margin-top:2px;">{plat} · {lang} · {r.community.city or "—"} · {members} members</div>' +
        '</div>' +
        f'<div style="text-align:right;margin-left:10px;flex-shrink:0;">' +
        f'<div style="font-size:20px;font-weight:800;color:{sc_clr};">{score:.0%}</div>' +
        f'<div style="font-size:10px;color:#64748b;">Switch Score</div>' +
        '</div></div></div>'
    )

def _review_audience_html(r: Tab3Result) -> str:
    def bar(label, val, clr):
        pct = int(val*100)
        return (
            f'<div style="margin-bottom:9px;">' +
            f'<div style="display:flex;justify-content:space-between;font-size:11px;color:#64748b;margin-bottom:3px;"><span>{label}</span><span style="color:#e2e8f0;font-weight:700;">{pct}%</span></div>' +
            f'<div style="background:#0f172a;border-radius:4px;height:6px;">' +
            f'<div style="background:{clr};width:{pct}%;height:100%;border-radius:4px;"></div></div></div>'
        )
    c = r.community
    factors_html = "".join(
        f'<div style="font-size:12px;color:#94a3b8;padding:3px 0;border-bottom:1px solid #1e293b;">• {f}</div>'
        for f in r.audience.supporting_research_factors
    )
    notes_html = ""
    if r.audience.scorer_notes:
        notes_html = "".join(f'<div style="font-size:11px;color:#64748b;margin-top:3px;">📝 {n}</div>' for n in r.audience.scorer_notes)
    return (
        '<div style="padding:4px 0;">' +
        bar("Switching Likelihood", r.audience.switching_likelihood_score, "#10b981" if r.audience.switching_likelihood_score>=0.65 else "#f59e0b" if r.audience.switching_likelihood_score>=0.40 else "#ef4444") +
        bar("Activity Score",     c.activity_score,      "#6366f1") +
        bar("Language Match",     c.language_match_score,"#818cf8") +
        bar("City Overlap",       c.city_overlap_score,  "#3b82f6") +
        '<div style="margin-top:10px;padding:8px 10px;background:#0f172a;border-radius:8px;">' +
        '<div style="font-size:10px;color:#64748b;margin-bottom:5px;text-transform:uppercase;letter-spacing:0.05em;">Audience Profile</div>' +
        f'<div style="font-size:12px;color:#cbd5e1;line-height:1.6;">{r.audience.estimated_subscriber_profile}</div>' +
        '</div>' +
        (
            '<div style="margin-top:8px;">' +
            '<div style="font-size:10px;color:#64748b;margin-bottom:4px;text-transform:uppercase;letter-spacing:0.05em;">Research Factors</div>' +
            factors_html + '</div>'
            if factors_html else ""
        ) +
        notes_html + '</div>'
    )

def _review_campaign_html(r: Tab3Result) -> str:
    camp_label = _CAMPAIGN_LABEL.get(r.campaign.campaign_type, r.campaign.campaign_type.value)
    lang_label = _LANG_FLAG.get(r.campaign.target_language, r.campaign.target_language.value)
    reward     = r.campaign.reward_type.value.title() if r.campaign.reward_type.value != "none" else "—"
    msg        = r.campaign.generated_message.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    subj_html  = ""
    if r.campaign.message_subject:
        s = r.campaign.message_subject.replace("<","&lt;").replace(">","&gt;")
        subj_html = f'<div style="background:#0f172a;border-radius:6px;padding:7px 10px;margin-bottom:8px;"><span style="font-size:10px;color:#64748b;">Subject: </span><span style="font-size:12px;color:#e2e8f0;">{s}</span></div>'
    return (
        '<div style="padding:4px 0;">' +
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:10px;background:#0f172a;border-radius:8px;padding:10px;">' +
        f'<div><div style="font-size:10px;color:#64748b;">Campaign Type</div><div style="font-size:12px;color:#e2e8f0;">{camp_label}</div></div>' +
        f'<div><div style="font-size:10px;color:#64748b;">Language</div><div style="font-size:12px;color:#e2e8f0;">{lang_label}</div></div>' +
        f'<div><div style="font-size:10px;color:#64748b;">Reward</div><div style="font-size:12px;color:#e2e8f0;">{reward}</div></div>' +
        f'<div><div style="font-size:10px;color:#64748b;">Priority Rank</div><div style="font-size:12px;color:#6366f1;font-weight:700;">#{r.recommendation.priority_rank}</div></div>' +
        '</div>' +
        subj_html +
        f'<div style="background:#0f172a;border-radius:8px;padding:11px;border-left:3px solid #6366f1;font-size:12px;color:#e2e8f0;line-height:1.7;white-space:pre-wrap;font-family:ui-monospace,monospace;max-height:260px;overflow-y:auto;">{msg}</div>' +
        '</div>'
    )

def _review_channel_html(r: Tab3Result) -> str:
    rec      = r.recommendation
    channel  = _CHANNEL_LABEL.get(rec.recommended_channel,"—")
    conf_pct = int(rec.confidence_score*100)
    icon_map = {Channel.GROUP_POST:"📢",Channel.ADMIN_DM:"💬",Channel.IG_STORY:"📸",Channel.EMAIL:"📧"}
    icon = icon_map.get(rec.recommended_channel,"📨")
    def section(notes, label, accent):
        if not notes: return ""
        items = "".join(f'<div style="font-size:12px;color:#cbd5e1;margin-bottom:4px;line-height:1.5;">• {n}</div>' for n in notes)
        return f'<div style="margin-bottom:10px;"><div style="font-size:10px;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;">{label}</div>{items}</div>'
    return (
        '<div style="padding:4px 0;">' +
        '<div style="background:#0f172a;border-radius:8px;padding:12px;margin-bottom:10px;">' +
        f'<div style="font-size:22px;margin-bottom:4px;">{icon}</div>' +
        f'<div style="font-size:14px;font-weight:700;color:#e2e8f0;">{channel}</div>' +
        f'<div style="font-size:11px;color:#64748b;">Confidence: {conf_pct}% · Est. reach: {rec.estimated_reach:,}</div>' +
        '</div>' +
        '<div style="padding:8px 10px;background:#0f172a;border-radius:8px;border-left:3px solid #6366f1;margin-bottom:10px;">' +
        '<div style="font-size:10px;color:#64748b;margin-bottom:3px;">Rationale</div>' +
        f'<div style="font-size:12px;color:#e2e8f0;line-height:1.5;">{rec.rationale}</div></div>' +
        section(list(r.community.scraper_notes),"🔍 Scraper Notes","#334155") +
        section(list(r.audience.scorer_notes),"📊 Scorer Notes","#1e3a5f") +
        section(list(rec.recommender_notes),"📡 Recommender Notes","#1a2942") +
        '</div>'
    )


# ---------------------------------------------------------------------------
# Render function
# ---------------------------------------------------------------------------

def render_tab3() -> None:
    gr.HTML(_TAB_CSS)
    gr.HTML(_PIPELINE_HTML)

    _env_key    = bool(os.getenv("ANTHROPIC_API_KEY"))
    _serper_key = os.getenv("SERPER_API_KEY","")

    with gr.Row():
        with gr.Column(scale=2):
            data_source = gr.Radio(
                choices=["Demo Communities","Upload CSV","🔍 Live Search (Serper)"],
                value="Demo Communities", label="Data Source",
            )
            csv_upload = gr.File(
                label="Upload Communities CSV", file_types=[".csv"], visible=False,
            )
            serper_key_input = gr.Textbox(
                label="Serper API Key",
                value=_serper_key,
                placeholder="82b6... (loaded from .env if set)",
                type="password",
                visible=False,
                info="Searches Google + Google Maps for real Balkan diaspora communities.",
            )
        with gr.Column(scale=2):
            pipeline_mode = gr.Radio(
                choices=["AI Agents","Deterministic Only"],
                value="Deterministic Only",
                label="Pipeline Mode",
            )
            gr.Markdown("✅ API key detected." if _env_key else "⚠️ No API key — deterministic only.")
        with gr.Column(scale=2):
            api_key_input = gr.Textbox(label="Anthropic API Key", placeholder="sk-ant-...", type="password")
        with gr.Column(scale=1):
            community_count = gr.Slider(minimum=5, maximum=20, value=20, step=1, label="Communities to analyse")

    run_btn = gr.Button("▶ Run Pipeline", variant="primary", size="lg")

    def on_source_change(source: str):
        return (
            gr.update(visible=(source == "Upload CSV")),
            gr.update(visible=(source == "🔍 Live Search (Serper)")),
        )

    data_source.change(
        fn=on_source_change, inputs=[data_source],
        outputs=[csv_upload, serper_key_input],
    )

    kpi_panel = gr.HTML(_KPI_PLACEHOLDER)

    with gr.Row():
        score_chart_comp = gr.Plot(label="Score Distribution", visible=False)
        map_chart_comp   = gr.Plot(label="Community Map — Turkey", visible=False)

    with gr.Row(equal_height=False):
        with gr.Column(scale=2):
            community_table = gr.Dataframe(
                headers=_TABLE_HEADERS,
                datatype=["number","str","str","str","str","str","str","str","str","str"],
                label="Communities (click row for details)",
                interactive=False, wrap=False,
            )
            # Approval Queue — inside left column, always below table
            gr.Markdown("#### 👤 Approval Queue")
            queue_panel = gr.HTML(
                '<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;padding:16px;color:#475569;font-size:13px;">No results yet.</div>'
            )
            with gr.Row():
                approve_btn = gr.Button("✅ Approve", variant="primary", size="sm")
                reject_btn  = gr.Button("❌ Reject",  variant="stop",    size="sm")

        with gr.Column(scale=1, min_width=300):
            # Pipeline Summary
            summary_panel = gr.HTML(
                '<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;padding:16px;color:#475569;font-size:13px;">Run the pipeline to see summary.</div>'
            )

            # Selected Community Review
            gr.Markdown("#### 🌐 Selected Community")
            review_header = gr.HTML(_REVIEW_PLACEHOLDER)
            with gr.Tabs():
                with gr.TabItem("📊 Audience / Scores"):
                    review_audience = gr.HTML(_TAB_PLACEHOLDER)
                with gr.TabItem("💬 Campaign Message"):
                    review_campaign = gr.HTML(_TAB_PLACEHOLDER)
                with gr.TabItem("📡 Channel & Notes"):
                    review_channel = gr.HTML(_TAB_PLACEHOLDER)

    # State
    results_store  = gr.State([])
    queue_idx      = gr.State(0)
    approved_store = gr.State([])
    rejected_store = gr.State([])

    # ── Callbacks ──────────────────────────────────────────────────────────

    def on_run(source, upload_csv, serper_key, mode, api_key, n_communities):
        _reset = (_REVIEW_PLACEHOLDER, _TAB_PLACEHOLDER, _TAB_PLACEHOLDER, _TAB_PLACEHOLDER)

        if source == "🔍 Live Search (Serper)":
            resolved_serper = (serper_key or "").strip() or os.getenv("SERPER_API_KEY","")
            if not resolved_serper:
                err_html = (
                    '<div style="background:#1e293b;border:1px solid #ef444455;border-radius:10px;'
                    'padding:16px;color:#ef4444;font-size:13px;">⚠️ Serper API key not set. '
                    'Add SERPER_API_KEY to .env or paste it in the field above.</div>'
                )
                return (err_html,) + (gr.update(visible=False),)*2 + ([], [], err_html, err_html, 0, [], []) + _reset
            from services.serper_search import discover_communities
            communities = discover_communities(
                api_key=resolved_serper,
                max_communities=int(n_communities),
                include_places=True,
            )
            if not communities:
                communities = load_demo_communities()

        elif source == "Upload CSV" and upload_csv is not None:
            import csv as _csv
            try:
                with open(upload_csv.name, encoding="utf-8") as fh:
                    reader = _csv.DictReader(fh)
                    rows = list(reader)
                # Minimal CSV → Community conversion
                communities = []
                for i, row in enumerate(rows):
                    name = row.get("name") or row.get("community_name") or f"Community {i+1}"
                    communities.append(Community(
                        community_id=f"CSV-{i:04d}", name=name,
                        platform=Platform.FACEBOOK, activity_score=0.5,
                        language_match_score=0.5, city_overlap_score=0.1,
                    ))
            except Exception:
                communities = load_demo_communities()
        else:
            communities = load_demo_communities()

        want_ai      = mode == "AI Agents"
        resolved_key = (api_key or "").strip() or os.getenv("ANTHROPIC_API_KEY","")
        use_ai       = want_ai and bool(resolved_key)

        results = run_tab3_pipeline(
            communities=communities, use_ai_agents=use_ai,
            max_communities=int(n_communities), anthropic_api_key=resolved_key or None,
        )
        stats     = summarise_tab3(results)
        sc_fig    = _score_chart(results)
        map_fig   = _community_map(results)
        approved: list[int] = []; rejected: list[int] = []

        return (
            _kpi_html(stats),
            gr.update(value=sc_fig,  visible=sc_fig  is not None),
            gr.update(value=map_fig, visible=map_fig is not None),
            _build_table_data(results), results,
            _summary_html(results, stats),
            _queue_html(results, 0, approved, rejected),
            0, approved, rejected,
        ) + _reset

    def on_row_select(evt: gr.SelectData, results: list[Tab3Result]):
        if not results or evt.index[0] >= len(results):
            return _REVIEW_PLACEHOLDER, _TAB_PLACEHOLDER, _TAB_PLACEHOLDER, _TAB_PLACEHOLDER
        r = results[evt.index[0]]
        return _review_header_html(r), _review_audience_html(r), _review_campaign_html(r), _review_channel_html(r)


    def on_approve(results, idx, approved, rejected):
        pending = [i for i in range(len(results)) if i not in approved and i not in rejected]
        if not pending:
            return _queue_html(results, idx, approved, rejected), idx, approved, rejected
        cur_i = pending[idx % len(pending)]
        new_appr = approved + [cur_i]
        new_idx  = (idx + 1) % max(1, len(pending) - 1) if len(pending) > 1 else 0
        return _queue_html(results, new_idx, new_appr, rejected), new_idx, new_appr, rejected

    def on_reject(results, idx, approved, rejected):
        pending = [i for i in range(len(results)) if i not in approved and i not in rejected]
        if not pending:
            return _queue_html(results, idx, approved, rejected), idx, approved, rejected
        cur_i = pending[idx % len(pending)]
        new_rej = rejected + [cur_i]
        new_idx = (idx + 1) % max(1, len(pending) - 1) if len(pending) > 1 else 0
        return _queue_html(results, new_idx, approved, new_rej), new_idx, approved, new_rej

    # ── Event wiring ───────────────────────────────────────────────────────

    _run_outputs = [
        kpi_panel, score_chart_comp, map_chart_comp,
        community_table, results_store,
        summary_panel, queue_panel,
        queue_idx, approved_store, rejected_store,
        review_header, review_audience, review_campaign, review_channel,
    ]
    run_btn.click(fn=on_run, inputs=[data_source, csv_upload, serper_key_input, pipeline_mode, api_key_input, community_count], outputs=_run_outputs)

    community_table.select(
        fn=on_row_select, inputs=[results_store],
        outputs=[review_header, review_audience, review_campaign, review_channel],
    )
    approve_btn.click(
        fn=on_approve, inputs=[results_store, queue_idx, approved_store, rejected_store],
        outputs=[queue_panel, queue_idx, approved_store, rejected_store],
    )
    reject_btn.click(
        fn=on_reject, inputs=[results_store, queue_idx, approved_store, rejected_store],
        outputs=[queue_panel, queue_idx, approved_store, rejected_store],
    )

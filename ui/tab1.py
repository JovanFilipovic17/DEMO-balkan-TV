"""
ui/tab1.py

Gradio UI for Tab 1: Existing Customers / ERP Follow-up.

Layout:
    Pipeline diagram (full width, decorative)
    Controls + Run button (full width)
    KPI cards (full width)
    Charts row (risk distribution + Turkey map)
    Main row:
        Left  (2/3) : Priority-sorted Dataframe table
        Right (1/3) : AI Business Summary
                      Selected Customer Review  (native gr.Tabs)
                      Human Approval Queue + action buttons
"""

from __future__ import annotations

import csv
import io
import os
from typing import Optional

import gradio as gr

try:
    import plotly.graph_objects as go
    _HAS_PLOTLY = True
except ImportError:
    _HAS_PLOTLY = False

from models.customer import CustomerRecord
from services.demo_data import load_demo_customers
from workflows.tab1_workflow import Tab1Result, run_tab1_pipeline, summarise_results


# ---------------------------------------------------------------------------
# Radio CSS
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
    <div style="text-align:center;flex:1;"><div style="font-size:18px;">🗄️</div><div style="font-size:10px;color:#94a3b8;margin-top:3px;font-weight:500;">ERP Data</div></div>
    <div style="color:#334155;font-size:14px;margin:0 2px;">──→</div>
    <div style="text-align:center;flex:1;"><div style="font-size:18px;">📊</div><div style="font-size:10px;color:#94a3b8;margin-top:3px;font-weight:500;">Risk Scoring</div></div>
    <div style="color:#334155;font-size:14px;margin:0 2px;">──→</div>
    <div style="text-align:center;flex:1;"><div style="font-size:18px;filter:drop-shadow(0 0 5px #6366f1);">🤖</div><div style="font-size:10px;color:#6366f1;margin-top:3px;font-weight:600;">AI Agent</div></div>
    <div style="color:#334155;font-size:14px;margin:0 2px;">──→</div>
    <div style="text-align:center;flex:1;"><div style="font-size:18px;">✉️</div><div style="font-size:10px;color:#94a3b8;margin-top:3px;font-weight:500;">Message Drafts</div></div>
    <div style="color:#334155;font-size:14px;margin:0 2px;">──→</div>
    <div style="text-align:center;flex:1;"><div style="font-size:18px;">👤</div><div style="font-size:10px;color:#94a3b8;margin-top:3px;font-weight:500;">Human Approval</div></div>
    <div style="color:#334155;font-size:14px;margin:0 2px;">──→</div>
    <div style="text-align:center;flex:1;"><div style="font-size:18px;">📤</div><div style="font-size:10px;color:#94a3b8;margin-top:3px;font-weight:500;">CRM Export</div></div>
  </div>
</div>
"""

# ---------------------------------------------------------------------------
# KPI cards
# ---------------------------------------------------------------------------

_KPI_PLACEHOLDER = '''<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;padding:16px;text-align:center;color:#475569;margin-bottom:14px;font-size:13px;">Run the pipeline to see metrics</div>'''

_PRIO_COLOR = {"high":"#ef4444","medium":"#f59e0b","low":"#22c55e","skip":"#64748b"}
_CH_ICON    = {"phone":"📞","email":"📧","whatsapp":"💬","none":"🚫"}
_PRIORITY_EMOJI = {"high":"🔴 HIGH","medium":"🟡 MED","low":"🟢 LOW","skip":"⚪ SKIP"}
_STATUS_LABEL   = {"active":"✅ Active","overdue":"⚠️ Overdue","expired":"❌ Expired","suspended":"🔒 Suspended","unknown":"❓"}
_CHANNEL_LABEL  = {"phone":"📞 Phone","email":"📧 Email","whatsapp":"💬 WhatsApp","none":"🚫 None"}

def _kpi_html(stats: dict) -> str:
    rev = stats.get("recoverable_revenue", 0)
    cards = [
        ("👥","Total Customers",    str(stats["total"]),               "#6366f1"),
        ("🔴","High Priority",      str(stats["high"]),                "#ef4444"),
        ("🟡","Medium Priority",    str(stats["medium"]),              "#f59e0b"),
        ("💰","Recoverable Revenue",f"${rev:,.0f}",                   "#10b981"),
        ("🚫","Unreachable",        str(stats["unreachable"]),         "#64748b"),
        ("🤖","AI Suggestions",     str(stats["with_ai_suggestion"]),  "#818cf8"),
    ]
    items = ""
    for icon, label, value, color in cards:
        items += (
            '<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;padding:14px 16px;min-width:0;">' +
            '<div style="font-size:10px;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:8px;">' +
            icon + " " + label + "</div>" +
            '<div style="font-size:26px;font-weight:700;color:' + color + ';line-height:1;">' + value + "</div></div>"
        )
    return '<div style="display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin-bottom:14px;">' + items + "</div>"


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

_CITY_COORDS: dict[str, tuple[float, float]] = {
    "Istanbul":(41.0082,28.9784),"Bursa":(40.1885,29.0610),"Ankara":(39.9334,32.8597),
    "Izmir":(38.4192,27.1287),"Edirne":(41.6818,26.5623),"Antalya":(36.8969,30.7133),
    "Konya":(37.8746,32.4932),"Gaziantep":(37.0662,37.3833),"Kocaeli":(40.7654,29.9408),
    "Mersin":(36.8,34.6333),"Adana":(37.0,35.3213),"Trabzon":(41.0015,39.7178),
}

def _risk_chart(results: list[Tab1Result]):
    if not _HAS_PLOTLY or not results:
        return None
    counts = {"HIGH":0,"MEDIUM":0,"LOW":0,"SKIP":0}
    for r in results:
        p = r.evaluation.overall_priority.value.upper()
        if p in counts:
            counts[p] += 1
    fig = go.Figure(go.Bar(
        x=list(counts.keys()), y=list(counts.values()),
        marker_color=["#ef4444","#f59e0b","#22c55e","#475569"],
        text=list(counts.values()), textposition="outside",
        textfont=dict(color="#e2e8f0",size=12),
        hovertemplate="%{x}: %{y} customers<extra></extra>",
    ))
    fig.update_layout(
        plot_bgcolor="#0f172a", paper_bgcolor="#1e293b",
        font=dict(color="#94a3b8",size=11,family="Inter, sans-serif"),
        margin=dict(l=10,r=10,t=36,b=10), height=210, showlegend=False,
        title=dict(text="Risk Distribution",font=dict(size=12,color="#64748b"),x=0.5,xanchor="center"),
        yaxis=dict(gridcolor="#1e293b",tickfont=dict(color="#94a3b8"),showgrid=True),
        xaxis=dict(tickfont=dict(color="#e2e8f0",size=11),showgrid=False),
    )
    return fig

def _turkey_map(results: list[Tab1Result]):
    if not _HAS_PLOTLY or not results:
        return None
    city_counts: dict[str,int] = {}
    for r in results:
        city = r.summary.record.country or ""
        if city in _CITY_COORDS:
            city_counts[city] = city_counts.get(city,0) + 1
    if not city_counts:
        return None
    cities = list(city_counts.keys())
    counts = [city_counts[c] for c in cities]
    max_c  = max(counts) if counts else 1
    fig = go.Figure(go.Scattergeo(
        lat=[_CITY_COORDS[c][0] for c in cities],
        lon=[_CITY_COORDS[c][1] for c in cities],
        text=[f"<b>{c}</b><br>{n} customer{'s' if n!=1 else ''}" for c,n in zip(cities,counts)],
        mode="markers+text", textposition="bottom center",
        textfont=dict(color="#e2e8f0",size=9,family="Inter, sans-serif"),
        hoverinfo="text",
        marker=dict(size=[max(10,10+28*(n/max_c)) for n in counts],color="#6366f1",opacity=0.85,line=dict(color="#818cf8",width=1.5)),
    ))
    fig.update_layout(
        paper_bgcolor="#1e293b", margin=dict(l=0,r=0,t=36,b=0), height=210,
        title=dict(text="Customer Distribution — Turkey",font=dict(size=12,color="#64748b"),x=0.5,xanchor="center"),
        geo=dict(showland=True,landcolor="#1e293b",showocean=True,oceancolor="#0f172a",
                 showcountries=True,countrycolor="#334155",showcoastlines=True,
                 coastlinecolor="#334155",projection_type="mercator",bgcolor="#0f172a",
                 lataxis=dict(range=[35.5,42.5]),lonaxis=dict(range=[25.5,44.5])),
    )
    return fig


# ---------------------------------------------------------------------------
# Summary panel
# ---------------------------------------------------------------------------

def _summary_panel_html(text: str, ai_used: bool = True) -> str:
    if not text:
        return ""
    badge = (
        '<span style="background:#6366f118;color:#818cf8;font-size:10px;font-weight:600;padding:2px 8px;border-radius:5px;border:1px solid #6366f133;">🤖 AI Summary</span>'
        if ai_used else
        '<span style="background:#33415518;color:#64748b;font-size:10px;font-weight:600;padding:2px 8px;border-radius:5px;border:1px solid #33415533;">⚙️ Auto Summary</span>'
    )
    text_safe = text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    return (
        '<div style="background:#1e293b;border:1px solid #334155;border-radius:12px;padding:14px;margin-bottom:10px;">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">' +
        '<span style="font-size:12px;font-weight:600;color:#e2e8f0;">AI Business Summary</span>' +
        '<div style="display:flex;gap:5px;">' + badge +
        '<span style="background:#10b98118;color:#10b981;font-size:10px;font-weight:600;padding:2px 8px;border-radius:5px;border:1px solid #10b98133;">✅ Done</span>' +
        '</div></div>' +
        '<div style="font-size:12.5px;color:#cbd5e1;line-height:1.75;background:#0f172a;padding:11px 13px;border-radius:8px;border-left:3px solid #6366f1;">' +
        text_safe + '</div></div>'
    )

def _deterministic_summary(stats: dict) -> str:
    total,high,med = stats["total"],stats["high"],stats["medium"]
    rev = stats.get("recoverable_revenue",0)
    unreach = stats["unreachable"]
    return (
        f"ERP analysis of {total} customers identified {high+med} requiring immediate outreach — "
        f"{high} high-priority and {med} medium-priority. "
        f"Recoverable revenue from overdue accounts totals ${rev:,.0f}. "
        f"{unreach} customer{'s' if unreach!=1 else ''} have no valid contact and require manual review."
    )


# ---------------------------------------------------------------------------
# Approval queue
# ---------------------------------------------------------------------------

def _queue_html(queue: list[Tab1Result], idx: int, approved: int, rejected: int) -> str:
    pending = max(0, len(queue) - idx)
    if not queue:
        return '<div style="background:#1e293b;border:1px solid #334155;border-radius:12px;padding:20px;text-align:center;color:#475569;font-size:13px;">Run the pipeline to populate the queue.</div>'
    if pending <= 0:
        return (
            '<div style="background:#1e293b;border:1px solid #334155;border-radius:12px;padding:20px;text-align:center;">' +
            '<div style="font-size:32px;margin-bottom:8px;">✅</div>' +
            '<div style="color:#10b981;font-size:14px;font-weight:600;">All messages reviewed!</div>' +
            f'<div style="color:#94a3b8;font-size:12px;margin-top:6px;">{approved} approved · {rejected} rejected</div>' +
            '</div>'
        )
    r = queue[idx]
    rec,ev,fu = r.summary.record, r.evaluation, r.followup
    prio  = ev.overall_priority.value
    color = _PRIO_COLOR.get(prio,"#64748b")
    ch    = ev.recommended_channel.value
    preview = (fu.message_body[:180] + ("…" if len(fu.message_body)>180 else "")).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    current = (
        f'<div style="background:#0f172a;border-radius:8px;padding:11px;margin-bottom:8px;border:1px solid #1e3a5f;">' +
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:7px;">' +
        f'<div><div style="font-size:13px;font-weight:600;color:#f1f5f9;">{rec.full_name}</div>' +
        f'<div style="font-size:10px;color:#64748b;margin-top:2px;">{_CH_ICON.get(ch,"📨")} {ch.title()} · {rec.country or "—"}</div></div>' +
        f'<span style="background:{color}22;color:{color};font-size:10px;font-weight:700;padding:2px 8px;border-radius:5px;border:1px solid {color}44;white-space:nowrap;margin-left:8px;">{prio.upper()}</span>' +
        f'</div>' +
        f'<div style="font-size:11px;color:#e2e8f0;line-height:1.55;background:#1e293b;padding:9px;border-radius:6px;border-left:3px solid #6366f1;white-space:pre-wrap;font-family:ui-monospace,monospace;max-height:80px;overflow-y:auto;">{preview}</div>' +
        f'</div>'
    )
    remaining = ""
    if len(queue) - idx - 1 > 0:
        remaining = (
            f'<div style="font-size:10px;color:#475569;font-weight:600;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">Remaining ({len(queue)-idx-1})</div>' +
            '<div style="max-height:120px;overflow-y:auto;display:flex;flex-direction:column;gap:2px;">'
        )
        for i in range(idx+1, len(queue)):
            pr = queue[i]; pr_prio = pr.evaluation.overall_priority.value; pr_color = _PRIO_COLOR.get(pr_prio,"#64748b")
            remaining += (
                f'<div style="padding:4px 8px;background:#0f172a;border-radius:4px;border:1px solid #1e293b;display:flex;justify-content:space-between;align-items:center;">' +
                f'<span style="font-size:11px;color:#94a3b8;">{pr.summary.record.full_name}</span>' +
                f'<span style="background:{pr_color}22;color:{pr_color};font-size:10px;padding:1px 5px;border-radius:3px;">{pr_prio.upper()}</span>' +
                '</div>'
            )
        remaining += '</div>'
    return (
        '<div style="background:#1e293b;border:1px solid #334155;border-radius:12px;padding:12px;">' +
        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">' +
        f'<span style="font-size:12px;font-weight:600;color:#e2e8f0;">Human Approval Queue</span>' +
        f'<span style="background:#334155;color:#94a3b8;font-size:10px;padding:2px 8px;border-radius:8px;">{pending} pending</span>' +
        f'</div>{current}{remaining}' +
        f'<div style="font-size:10px;color:#475569;text-align:center;margin-top:6px;">{idx+1} of {len(queue)} · {approved} approved · {rejected} rejected</div>' +
        '</div>'
    )


# ---------------------------------------------------------------------------
# Review panel HTML builders (right sidebar — replaces popup modal)
# ---------------------------------------------------------------------------

_REVIEW_PLACEHOLDER = '<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;padding:24px;text-align:center;color:#475569;font-size:13px;">Select a row from the table to review customer details.</div>'

_TAB_PLACEHOLDER = '<div style="color:#475569;font-size:13px;padding:20px;text-align:center;">Select a customer to see details here.</div>'

def _review_header_html(r: Tab1Result) -> str:
    rec  = r.summary.record
    ev   = r.evaluation
    prio = ev.overall_priority.value
    color = _PRIO_COLOR.get(prio,"#64748b")
    status = _STATUS_LABEL.get(r.summary.status.value, r.summary.status.value)
    plan = f" · {rec.subscription_plan}" if rec.subscription_plan else ""
    return (
        '<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;padding:14px 16px;">' +
        '<div style="display:flex;justify-content:space-between;align-items:flex-start;">' +
        '<div>' +
        f'<div style="font-size:15px;font-weight:700;color:#e2e8f0;">{rec.full_name}</div>' +
        f'<div style="font-size:11px;color:#64748b;margin-top:2px;">{rec.customer_id} · {status} · {rec.country or "—"} · {rec.language or "—"}{plan}</div>' +
        '</div>' +
        f'<span style="background:{color}22;color:{color};font-size:11px;font-weight:700;padding:3px 10px;border-radius:5px;border:1px solid {color}44;white-space:nowrap;margin-left:8px;">{prio.upper()}</span>' +
        '</div></div>'
    )

def _review_risk_html(r: Tab1Result) -> str:
    ev,sm,rec = r.evaluation, r.summary, r.summary.record
    def bar(val, clr):
        pct = int(val*100)
        return (
            f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">' +
            f'<div style="flex:1;background:#0f172a;border-radius:4px;height:7px;overflow:hidden;">' +
            f'<div style="width:{pct}%;height:100%;background:{clr};border-radius:4px;"></div></div>' +
            f'<span style="color:#e2e8f0;font-size:13px;font-weight:700;min-width:36px;text-align:right;">{val:.2f}</span></div>'
        )
    ch = ev.recommended_channel.value
    return (
        '<div style="padding:4px 0;">' +
        '<div style="font-size:11px;color:#64748b;margin-bottom:3px;">Payment Risk</div>' + bar(ev.payment_risk_score,"#ef4444") +
        '<div style="font-size:11px;color:#64748b;margin-bottom:3px;">Churn Risk</div>' + bar(ev.churn_risk_score,"#f59e0b") +
        '<div style="font-size:11px;color:#64748b;margin-bottom:3px;">Contact Quality</div>' + bar(ev.contact_quality_score,"#10b981") +
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:10px;background:#0f172a;border-radius:8px;padding:10px;">' +
        f'<div><div style="font-size:10px;color:#64748b;">Channel</div><div style="font-size:12px;color:#e2e8f0;">{_CH_ICON.get(ch,"📨")} {ch.title()}</div></div>' +
        f'<div><div style="font-size:10px;color:#64748b;">Overdue</div><div style="font-size:12px;color:#e2e8f0;">{f"{sm.days_overdue}d" if sm.days_overdue else "—"}</div></div>' +
        f'<div><div style="font-size:10px;color:#64748b;">Balance</div><div style="font-size:12px;color:#e2e8f0;">${rec.outstanding_balance:.0f}</div></div>' +
        f'<div><div style="font-size:10px;color:#64748b;">Plan</div><div style="font-size:12px;color:#e2e8f0;">{rec.subscription_plan or "—"}</div></div>' +
        '</div>' +
        '<div style="margin-top:8px;padding:8px 10px;background:#0f172a;border-radius:8px;border-left:3px solid #6366f1;">' +
        '<div style="font-size:10px;color:#64748b;margin-bottom:3px;">Next Action</div>' +
        f'<div style="font-size:12px;color:#e2e8f0;line-height:1.5;">{ev.next_action}</div></div>' +
        '</div>'
    )

def _review_message_html(r: Tab1Result) -> str:
    fu = r.followup
    msg = fu.message_body.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    subj = ""
    if fu.message_subject:
        s = fu.message_subject.replace("<","&lt;").replace(">","&gt;")
        subj = f'<div style="background:#0f172a;border-radius:6px;padding:8px 10px;margin-bottom:8px;"><span style="font-size:10px;color:#64748b;">Subject: </span><span style="font-size:12px;color:#e2e8f0;">{s}</span></div>'
    imp = ""
    if fu.suggested_improvement:
        i = fu.suggested_improvement.replace("<","&lt;").replace(">","&gt;")
        imp = f'<div style="margin-top:8px;padding:7px 9px;background:#1a2942;border-left:3px solid #6366f1;border-radius:0 6px 6px 0;font-size:11px;color:#94a3b8;font-style:italic;">🤖 {i}</div>'
    return (
        '<div style="padding:4px 0;">' + subj +
        f'<div style="font-size:10px;color:#64748b;margin-bottom:4px;">Template: <code style="color:#818cf8;">{fu.template_used.value}</code></div>' +
        f'<div style="background:#0f172a;border-radius:8px;padding:11px;border-left:3px solid #6366f1;font-size:12px;color:#e2e8f0;line-height:1.7;white-space:pre-wrap;font-family:ui-monospace,monospace;max-height:260px;overflow-y:auto;">{msg}</div>' +
        imp + '</div>'
    )

def _review_notes_html(r: Tab1Result) -> str:
    def section(notes, label):
        if not notes:
            return ""
        items = "".join(f'<div style="color:#cbd5e1;font-size:12px;margin-bottom:4px;line-height:1.5;">• {n}</div>' for n in notes)
        return f'<div style="margin-bottom:12px;"><div style="font-size:10px;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:5px;">{label}</div>{items}</div>'
    content = section(r.summary.scanner_notes,"🔍 Scanner") + section(r.evaluation.evaluator_notes,"⚖️ Evaluator") + section(r.followup.delivery_notes,"📋 Delivery")
    if not content:
        content = '<div style="color:#475569;font-size:12px;text-align:center;padding:20px 0;">No agent notes.</div>'
    return f'<div style="padding:4px 0;">{content}</div>'


# ---------------------------------------------------------------------------
# CSV parser
# ---------------------------------------------------------------------------

def _parse_csv(file_content: str) -> list[CustomerRecord]:
    from datetime import datetime as _dt
    def get(row, key):
        val = next((v for k,v in row.items() if k.strip().lower()==key),None)
        return val.strip() if val and val.strip() else None
    def to_date(val):
        if not val: return None
        for fmt in ("%Y-%m-%d","%d/%m/%Y","%m/%d/%Y","%d.%m.%Y"):
            try: return _dt.strptime(val,fmt).date()
            except ValueError: continue
        return None
    def to_float(val):
        if not val: return None
        try: return float(val.replace(",",".").replace("$","").strip())
        except ValueError: return None
    records = []
    for i,row in enumerate(csv.DictReader(io.StringIO(file_content))):
        try:
            records.append(CustomerRecord(
                customer_id=get(row,"customer_id") or f"ROW-{i+1:04d}",
                full_name=get(row,"full_name") or "Unknown",
                phone=get(row,"phone"), email=get(row,"email"),
                country=get(row,"country"), language=get(row,"language"),
                subscription_plan=get(row,"subscription_plan"),
                subscription_start=to_date(get(row,"subscription_start")),
                subscription_end=to_date(get(row,"subscription_end")),
                last_payment_date=to_date(get(row,"last_payment_date")),
                last_payment_amount=to_float(get(row,"last_payment_amount")),
                outstanding_balance=to_float(get(row,"outstanding_balance")) or 0.0,
                notes=get(row,"notes"),
            ))
        except Exception:
            continue
    return records


# ---------------------------------------------------------------------------
# Table formatter
# ---------------------------------------------------------------------------

def _results_to_table(results: list[Tab1Result]) -> list[list[str]]:
    rows = []
    for r in results:
        rec,ev = r.summary.record, r.evaluation
        rows.append([
            _PRIORITY_EMOJI.get(ev.overall_priority.value,ev.overall_priority.value),
            rec.customer_id, rec.full_name,
            _STATUS_LABEL.get(r.summary.status.value,r.summary.status.value),
            f"{r.summary.days_overdue}d" if r.summary.days_overdue else "—",
            f"${rec.outstanding_balance:.0f}",
            _CHANNEL_LABEL.get(ev.recommended_channel.value,ev.recommended_channel.value),
            ev.next_action,
        ])
    return rows


# ---------------------------------------------------------------------------
# Render function
# ---------------------------------------------------------------------------

def render_tab1() -> None:
    gr.HTML(_TAB_CSS)
    gr.HTML(_PIPELINE_HTML)

    _env_key = bool(os.getenv("ANTHROPIC_API_KEY"))

    with gr.Row():
        with gr.Column(scale=2):
            csv_upload = gr.File(label="Upload ERP CSV  (optional — uses 500-record demo if empty)", file_types=[".csv"])
        with gr.Column(scale=1):
            mode_radio = gr.Radio(
                choices=["⚙️ Deterministic","🤖 AI Agents"],
                value="⚙️ Deterministic",
                label="Pipeline mode",
            )
            gr.Markdown("✅ API key detected." if _env_key else "⚠️ No API key — deterministic only.")
            api_key_input = gr.Textbox(label="Override API Key (optional)", placeholder="sk-ant-...", type="password")
        with gr.Column(scale=1):
            max_slider = gr.Slider(minimum=10, maximum=500, value=100, step=10, label="Customers to analyse")

    run_btn = gr.Button("▶  Run Pipeline", variant="primary", size="lg")
    kpi_html_comp = gr.HTML(_KPI_PLACEHOLDER)

    with gr.Row():
        risk_chart_comp = gr.Plot(label="Risk Distribution",             visible=False, scale=1, min_width=200)
        turkey_map_comp = gr.Plot(label="Customer Distribution — Turkey", visible=False, scale=1, min_width=200)

    with gr.Row(equal_height=False):
        with gr.Column(scale=2):
            table = gr.Dataframe(
                headers=["Priority","ID","Name","Status","Overdue","Balance","Channel","Next Action"],
                datatype=["str"]*8, interactive=False, wrap=True,
                label="Customers — click a row to view details",
            )
            # Approval Queue — inside left column, always below table
            gr.Markdown("#### 👤 Approval Queue")
            queue_html_comp = gr.HTML(_queue_html([],0,0,0))
            with gr.Row():
                approve_btn = gr.Button("✅ Approve", variant="primary",  size="sm")
                edit_btn    = gr.Button("✏️ Edit",    variant="secondary", size="sm")
                reject_btn  = gr.Button("❌ Reject",  variant="stop",      size="sm")
            edit_box = gr.Textbox(label="Edit message before approving", lines=5, visible=False, placeholder="Edit the message here, then Save & Approve.")
            save_btn = gr.Button("💾 Save & Approve", variant="primary", visible=False, size="sm")

        with gr.Column(scale=1, min_width=300):
            # AI Summary
            summary_html_comp = gr.HTML("", visible=False)

            # Selected Customer Review
            gr.Markdown("#### 📋 Selected Customer")
            review_header = gr.HTML(_REVIEW_PLACEHOLDER)
            with gr.Tabs():
                with gr.TabItem("📊 Risk Scores"):
                    review_risk = gr.HTML(_TAB_PLACEHOLDER)
                with gr.TabItem("💬 Message"):
                    review_msg = gr.HTML(_TAB_PLACEHOLDER)
                with gr.TabItem("📋 Notes"):
                    review_notes = gr.HTML(_TAB_PLACEHOLDER)

    # State
    results_store  = gr.State([])
    queue_store    = gr.State([])
    queue_idx      = gr.State(0)
    approved_count = gr.State(0)
    rejected_count = gr.State(0)
    edit_open      = gr.State(False)

    # ── Callbacks ──────────────────────────────────────────────────────────

    def on_run(csv_file, mode, api_key, max_recs):
        _reset = (_REVIEW_PLACEHOLDER, _TAB_PLACEHOLDER, _TAB_PLACEHOLDER, _TAB_PLACEHOLDER)
        _err = (
            _KPI_PLACEHOLDER,
            gr.update(visible=False), gr.update(visible=False), gr.update(visible=False),
            [], [],
            _queue_html([],0,0,0), [], 0, 0, 0,
        ) + _reset
        if csv_file is not None:
            try:
                content = open(csv_file.name,encoding="utf-8").read()
                records = _parse_csv(content)
                if not records:
                    return _err
            except Exception as exc:
                return (f"❌ {exc}",) + _err[1:]
        else:
            records = load_demo_customers()

        want_ai      = mode == "🤖 AI Agents"
        resolved_key = (api_key or "").strip() or os.getenv("ANTHROPIC_API_KEY","")
        use_ai       = want_ai and bool(resolved_key)

        results = run_tab1_pipeline(
            records=records, use_ai_agents=use_ai,
            max_records=int(max_recs), anthropic_api_key=resolved_key or None,
        )
        stats = summarise_results(results)
        queue = [r for r in results if r.evaluation.overall_priority.value in ("high","medium") and r.followup.message_body]
        risk_fig = _risk_chart(results)
        map_fig  = _turkey_map(results)

        if use_ai and resolved_key:
            try:
                from agents.summary_agent import generate_summary
                import anthropic as _ant
                summary_text = generate_summary(results, stats, _ant.Anthropic(api_key=resolved_key))
                ai_used = bool(summary_text)
                if not summary_text:
                    summary_text = _deterministic_summary(stats); ai_used = False
            except Exception:
                summary_text = _deterministic_summary(stats); ai_used = False
        else:
            summary_text = _deterministic_summary(stats); ai_used = False

        return (
            _kpi_html(stats),
            gr.update(value=risk_fig, visible=risk_fig is not None),
            gr.update(value=map_fig,  visible=map_fig  is not None),
            gr.update(value=_summary_panel_html(summary_text, ai_used=ai_used), visible=True),
            _results_to_table(results), results,
            _queue_html(queue,0,0,0), queue, 0, 0, 0,
        ) + _reset

    def on_row_select(evt: gr.SelectData, results):
        if not results or evt.index[0] >= len(results):
            return _REVIEW_PLACEHOLDER, _TAB_PLACEHOLDER, _TAB_PLACEHOLDER, _TAB_PLACEHOLDER
        r = results[evt.index[0]]
        return _review_header_html(r), _review_risk_html(r), _review_message_html(r), _review_notes_html(r)

    def on_approve(queue, idx, approved, rejected):
        new_idx = idx + 1; new_appr = approved + 1
        return (_queue_html(queue,new_idx,new_appr,rejected), new_idx, new_appr,
                gr.update(visible=False,value=""), gr.update(visible=False), False)

    def on_reject(queue, idx, approved, rejected):
        new_idx = idx + 1; new_rej = rejected + 1
        return _queue_html(queue,new_idx,approved,new_rej), new_idx, new_rej

    def on_edit_toggle(queue, idx, is_open):
        if is_open:
            return gr.update(visible=False,value=""), gr.update(visible=False), gr.update(value="✏️ Edit"), False
        body = queue[idx].followup.message_body if queue and idx < len(queue) else ""
        return gr.update(visible=True,value=body), gr.update(visible=True), gr.update(value="✖ Cancel"), True

    def on_save(queue, idx, text, approved, rejected):
        new_idx = idx + 1; new_appr = approved + 1
        return (_queue_html(queue,new_idx,new_appr,rejected), new_idx, new_appr,
                gr.update(visible=False,value=""), gr.update(visible=False), gr.update(value="✏️ Edit"), False)

    # ── Event wiring ───────────────────────────────────────────────────────

    _run_outputs = [
        kpi_html_comp, risk_chart_comp, turkey_map_comp, summary_html_comp,
        table, results_store,
        queue_html_comp, queue_store, queue_idx, approved_count, rejected_count,
        review_header, review_risk, review_msg, review_notes,
    ]
    run_btn.click(fn=on_run, inputs=[csv_upload, mode_radio, api_key_input, max_slider], outputs=_run_outputs)

    table.select(
        fn=on_row_select, inputs=[results_store],
        outputs=[review_header, review_risk, review_msg, review_notes],
    )

    approve_btn.click(
        fn=on_approve, inputs=[queue_store, queue_idx, approved_count, rejected_count],
        outputs=[queue_html_comp, queue_idx, approved_count, edit_box, save_btn, edit_open],
    )
    reject_btn.click(
        fn=on_reject, inputs=[queue_store, queue_idx, approved_count, rejected_count],
        outputs=[queue_html_comp, queue_idx, rejected_count],
    )
    edit_btn.click(
        fn=on_edit_toggle, inputs=[queue_store, queue_idx, edit_open],
        outputs=[edit_box, save_btn, edit_btn, edit_open],
    )
    save_btn.click(
        fn=on_save, inputs=[queue_store, queue_idx, edit_box, approved_count, rejected_count],
        outputs=[queue_html_comp, queue_idx, approved_count, edit_box, save_btn, edit_btn, edit_open],
    )

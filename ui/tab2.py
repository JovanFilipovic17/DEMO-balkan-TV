"""
ui/tab2.py

Gradio UI for Tab 2: Lead Database Cleaner & Qualifier.

Layout:
    Controls + Run button (full width)
    KPI cards (full width)
    City map (full width)
    Main row:
        Left  (2/3) : Quality-sorted Dataframe table + Export
        Right (1/3) : Pipeline Summary + Selected Lead Review (native gr.Tabs)
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

from models.lead import LeadQuality, RawLead, Tab2Result
from services.lead_demo_data import load_demo_leads
from workflows.tab2_workflow import run_tab2_pipeline, summarise_tab2


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

_QUALITY_EMOJI = {
    "good":    "✅ GOOD",
    "fixable": "🟡 FIXABLE",
    "poor":    "🔴 POOR",
    "reject":  "⛔ REJECT",
}
_QUALITY_COLOR = {
    "good":    "#10b981",
    "fixable": "#f59e0b",
    "poor":    "#ef4444",
    "reject":  "#64748b",
}


# ---------------------------------------------------------------------------
# CSV / Excel parsing
# ---------------------------------------------------------------------------

_LEAD_ALIASES: dict[str, list[str]] = {
    "full_name": ["name","full_name","customer_name","ime","fullname"],
    "phone":     ["phone","telephone","tel","mob","mobile","mobitel","gsm"],
    "email":     ["email","e-mail","mail","e_mail"],
    "city":      ["city","grad","sehir","town","location"],
    "language":  ["language","lang","jezik","language_tag","language_segment","lang_segment"],
    "source":    ["source","izvor","kanal","channel","lead_source"],
    "notes":     ["notes","note","biljeske","comment","comments"],
}

def _parse_lead_csv(content: str) -> list[RawLead]:
    def _find(row, canonical):
        for alias in _LEAD_ALIASES[canonical]:
            for key, val in row.items():
                if key.strip().lower() == alias:
                    v = val.strip() if val else None
                    return v or None
        return None
    leads: list[RawLead] = []
    for i, row in enumerate(csv.DictReader(io.StringIO(content))):
        try:
            leads.append(RawLead(
                row_index=i, full_name=_find(row,"full_name"), phone=_find(row,"phone"),
                email=_find(row,"email"), city=_find(row,"city"), language=_find(row,"language"),
                source=_find(row,"source"), notes=_find(row,"notes"),
            ))
        except Exception:
            continue
    return leads

def _parse_lead_excel(file_path: str) -> list[RawLead]:
    try:
        import openpyxl
    except ImportError:
        raise RuntimeError("openpyxl required for Excel import. Run: pip install openpyxl")
    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        return []
    headers = [str(h).strip().lower() if h is not None else "" for h in rows[0]]
    def _find_col(canonical):
        for alias in _LEAD_ALIASES[canonical]:
            for i, h in enumerate(headers):
                if h == alias: return i
        return None
    col_map = {f: _find_col(f) for f in _LEAD_ALIASES}
    leads: list[RawLead] = []
    for i, row in enumerate(rows[1:]):
        def _cell(field, _row=row):
            idx = col_map.get(field)
            if idx is None or idx >= len(_row): return None
            val = _row[idx]
            s = str(val).strip() if val is not None else ""
            return s or None
        try:
            leads.append(RawLead(
                row_index=i, full_name=_cell("full_name"), phone=_cell("phone"),
                email=_cell("email"), city=_cell("city"), language=_cell("language"),
                source=_cell("source"), notes=_cell("notes"),
            ))
        except Exception:
            continue
    return leads

def _load_from_file(file_path: str) -> list[RawLead]:
    ext = os.path.splitext(file_path)[1].lower()
    if ext in (".xlsx",".xlsm",".xls"):
        return _parse_lead_excel(file_path)
    return _parse_lead_csv(open(file_path, encoding="utf-8").read())


# ---------------------------------------------------------------------------
# KPI cards
# ---------------------------------------------------------------------------

_KPI_PLACEHOLDER = '<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;padding:16px;text-align:center;color:#475569;margin-bottom:14px;font-size:13px;">Run the pipeline to see results</div>'

def _kpi_html(stats: dict) -> str:
    cards = [
        ("📋","Total",       str(stats["total"]),           "#6366f1"),
        ("✅","Good",        str(stats["good"]),             "#10b981"),
        ("🟡","Fixable",     str(stats["fixable"]),          "#f59e0b"),
        ("🔴","Poor",        str(stats["poor"]),             "#ef4444"),
        ("⛔","Reject",      str(stats["reject"]),           "#64748b"),
        ("🔁","Duplicates",  str(stats["duplicates"]),       "#8b5cf6"),
        ("🚫","No Contact",  str(stats["invalid_contact"]),  "#475569"),
        ("🤖","AI Enriched", str(stats["ai_enriched"]),      "#818cf8"),
    ]
    items = ""
    for icon, label, value, color in cards:
        items += (
            '<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;padding:12px 14px;min-width:0;">' +
            '<div style="font-size:9px;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">' +
            icon + " " + label + '</div>' +
            '<div style="font-size:22px;font-weight:700;color:' + color + ';line-height:1;">' + value + '</div></div>'
        )
    return '<div style="display:grid;grid-template-columns:repeat(8,1fr);gap:8px;margin-bottom:14px;">' + items + '</div>'


# ---------------------------------------------------------------------------
# Turkey map
# ---------------------------------------------------------------------------

_CITY_COORDS: dict[str, tuple[float, float]] = {
    "Istanbul":(41.0082,28.9784),"Bursa":(40.1885,29.0610),"Ankara":(39.9334,32.8597),
    "Izmir":(38.4192,27.1287),"Edirne":(41.6818,26.5623),"Antalya":(36.8969,30.7133),
    "Konya":(37.8746,32.4932),"Gaziantep":(37.0662,37.3833),"Kocaeli":(40.7654,29.9408),
    "Mersin":(36.8,34.6333),"Adana":(37.0,35.3213),"Trabzon":(41.0015,39.7178),
}

def _lead_city_map(results: list[Tab2Result]):
    if not _HAS_PLOTLY or not results:
        return None
    city_counts: dict[str,int] = {}
    for r in results:
        city = (r.raw.city or "").strip().title()
        if city in _CITY_COORDS:
            city_counts[city] = city_counts.get(city,0) + 1
    if not city_counts:
        return None
    cities = list(city_counts.keys()); counts = [city_counts[c] for c in cities]
    max_c = max(counts) if counts else 1
    fig = go.Figure(go.Scattergeo(
        lat=[_CITY_COORDS[c][0] for c in cities], lon=[_CITY_COORDS[c][1] for c in cities],
        text=[f"<b>{c}</b><br>{n} lead{'s' if n!=1 else ''}" for c,n in zip(cities,counts)],
        mode="markers+text", textposition="bottom center",
        textfont=dict(color="#e2e8f0",size=9,family="Inter, sans-serif"),
        hoverinfo="text",
        marker=dict(size=[max(10,10+28*(n/max_c)) for n in counts],color="#10b981",opacity=0.85,line=dict(color="#34d399",width=1.5)),
    ))
    fig.update_layout(
        paper_bgcolor="#1e293b", margin=dict(l=0,r=0,t=36,b=0), height=220,
        title=dict(text="Lead Distribution — Turkey",font=dict(size=12,color="#64748b"),x=0.5,xanchor="center"),
        geo=dict(showland=True,landcolor="#1e293b",showocean=True,oceancolor="#0f172a",
                 showcountries=True,countrycolor="#334155",showcoastlines=True,
                 coastlinecolor="#334155",projection_type="mercator",bgcolor="#0f172a",
                 lataxis=dict(range=[35.5,42.5]),lonaxis=dict(range=[25.5,44.5])),
    )
    return fig


# ---------------------------------------------------------------------------
# Table formatter
# ---------------------------------------------------------------------------

def _results_to_table(results: list[Tab2Result]) -> list[list[str]]:
    rows = []
    for r in results:
        lead   = r.raw
        issues = len([i for i in r.issues if i.severity in ("error","warning")])
        name_d = r.normalized_name or lead.full_name or "—"
        if r.normalized_name and r.normalized_name != lead.full_name:
            name_d = f"{r.normalized_name} ✏️"
        rows.append([
            _QUALITY_EMOJI.get(r.quality.value, r.quality.value),
            str(lead.row_index+1), name_d,
            "✓" if r.phone_valid else "✗",
            "✓" if r.email_valid else "✗",
            lead.city or "—", lead.source or "—",
            f"{r.score.overall:.0%}",
            str(issues) if issues else "—",
            f"→ row {r.duplicate_of_row+1}" if r.is_duplicate and r.duplicate_of_row is not None else "—",
        ])
    return rows


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def _export_csv(results: list[Tab2Result]) -> str:
    out = io.StringIO()
    w   = csv.writer(out)
    w.writerow(["row","quality","original_name","normalized_name","phone","phone_valid",
                "email","email_valid","city","language","source","score_overall",
                "is_duplicate","duplicate_of_row","duplicate_reason","issues","ai_notes"])
    for r in results:
        lead = r.raw
        issues = "; ".join(f"{i.field}: {i.description}" for i in r.issues)
        notes  = "; ".join(r.ai_notes)
        w.writerow([
            lead.row_index+1, r.quality.value,
            lead.full_name or "", r.normalized_name or "",
            lead.phone or "", "yes" if r.phone_valid else "no",
            lead.email or "", "yes" if r.email_valid else "no",
            lead.city or "", lead.language or "", lead.source or "",
            f"{r.score.overall:.2f}",
            "yes" if r.is_duplicate else "no",
            str(r.duplicate_of_row+1) if r.duplicate_of_row is not None else "",
            r.duplicate_reason or "", issues, notes,
        ])
    return out.getvalue()


# ---------------------------------------------------------------------------
# Pipeline summary
# ---------------------------------------------------------------------------

def _pipeline_summary_html(stats: dict) -> str:
    total    = stats["total"]
    good     = stats["good"]
    fixable  = stats["fixable"]
    poor     = stats["poor"]
    reject   = stats["reject"]
    dups     = stats["duplicates"]
    no_cont  = stats["invalid_contact"]
    ai_enr   = stats["ai_enriched"]
    usable   = good + fixable
    return (
        '<div style="background:#1e293b;border:1px solid #334155;border-radius:12px;padding:14px;margin-bottom:10px;">' +
        '<div style="font-size:12px;font-weight:600;color:#e2e8f0;margin-bottom:10px;">📊 Pipeline Summary</div>' +
        '<div style="font-size:12.5px;color:#cbd5e1;line-height:1.75;background:#0f172a;padding:11px 13px;border-radius:8px;border-left:3px solid #10b981;">' +
        f'{total} leads processed — {usable} usable ({good} good, {fixable} fixable). ' +
        f'{reject} rejected, {dups} duplicates removed, {no_cont} with no valid contact. ' +
        f'{ai_enr} leads AI-enriched with quality notes.' +
        '</div></div>'
    )


# ---------------------------------------------------------------------------
# Review panel builders (right sidebar — replaces modal)
# ---------------------------------------------------------------------------

_REVIEW_PLACEHOLDER = '<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;padding:24px;text-align:center;color:#475569;font-size:13px;">Select a row from the table to review lead details.</div>'
_TAB_PLACEHOLDER    = '<div style="color:#475569;font-size:13px;padding:20px;text-align:center;">Select a lead to see details here.</div>'

def _review_header_html(r: Tab2Result) -> str:
    lead  = r.raw
    q_val = r.quality.value
    color = _QUALITY_COLOR.get(q_val,"#64748b")
    display = r.normalized_name or lead.full_name or "(no name)"
    norm_note = ""
    if r.normalized_name and r.normalized_name != lead.full_name:
        orig = (lead.full_name or "").replace("<","&lt;").replace(">","&gt;")
        norm_note = f'<div style="font-size:11px;color:#f59e0b;margin-top:2px;">✏️ Normalized from: {orig}</div>'
    meta = " · ".join(filter(None,[f"Row {lead.row_index+1}",lead.city,lead.language,lead.source]))
    return (
        '<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;padding:14px 16px;">' +
        '<div style="display:flex;justify-content:space-between;align-items:flex-start;">' +
        '<div>' +
        f'<div style="font-size:15px;font-weight:700;color:#e2e8f0;">{display}</div>' +
        f'<div style="font-size:11px;color:#64748b;margin-top:2px;">{meta}</div>' +
        norm_note +
        '</div>' +
        f'<span style="background:{color}22;color:{color};font-size:11px;font-weight:700;padding:3px 10px;border-radius:5px;border:1px solid {color}44;white-space:nowrap;margin-left:8px;">{_QUALITY_EMOJI.get(q_val,q_val)}</span>' +
        '</div></div>'
    )

def _review_qual_html(r: Tab2Result) -> str:
    color = _QUALITY_COLOR.get(r.quality.value,"#64748b")
    def bar(val, clr, label):
        pct = int(val*100)
        return (
            f'<div style="margin-bottom:10px;">' +
            f'<div style="font-size:11px;color:#64748b;margin-bottom:3px;">{label}</div>' +
            f'<div style="display:flex;align-items:center;gap:8px;">' +
            f'<div style="flex:1;background:#0f172a;border-radius:4px;height:7px;overflow:hidden;">' +
            f'<div style="width:{pct}%;height:100%;background:{clr};border-radius:4px;"></div></div>' +
            f'<span style="color:#e2e8f0;font-size:13px;font-weight:700;min-width:36px;text-align:right;">{val:.0%}</span></div></div>'
        )
    dup_html = ""
    if r.is_duplicate:
        dr = f"matched on {r.duplicate_reason}" if r.duplicate_reason else ""
        dup_html = (
            f'<div style="margin-bottom:10px;padding:8px 10px;background:#0f172a;border-radius:6px;'
            f'border:1px solid #334155;font-size:11px;color:#f59e0b;">'
            f'🔁 Duplicate of row {r.duplicate_of_row+1} {dr}</div>'
        )
    # Contact info
    lead = r.raw
    phone_b = "✅" if r.phone_valid else "❌"
    email_b = "✅" if r.email_valid else "❌"
    # Issues
    if r.issues:
        issue_html = ""
        for iss in r.issues:
            sev_icon = "🔴" if iss.severity=="error" else "🟡"
            fix_note = ' <span style="color:#64748b;font-size:10px;">(auto-fixable)</span>' if iss.auto_fixable else ""
            issue_html += (
                f'<div style="margin-bottom:6px;padding:6px 8px;background:#0f172a;border-radius:5px;'
                f'border-left:3px solid {"#ef4444" if iss.severity=="error" else "#f59e0b"};font-size:11px;color:#cbd5e1;">'
                f'{sev_icon} <b>{iss.field.replace("<","&lt;")}</b>: {iss.description.replace("<","&lt;")}{fix_note}</div>'
            )
    else:
        issue_html = '<div style="color:#64748b;font-size:12px;padding:4px 0;">No issues found.</div>'
    return (
        '<div style="padding:4px 0;">' +
        bar(r.score.completeness,"#6366f1","Completeness") +
        bar(r.score.contact_quality,"#10b981","Contact Quality") +
        bar(r.score.engagement,"#f59e0b","Engagement Potential") +
        bar(r.score.overall,color,"Overall Score") +
        dup_html +
        '<div style="padding:8px 10px;background:#0f172a;border-radius:8px;margin-bottom:10px;">' +
        f'<div style="display:flex;justify-content:space-between;margin-bottom:6px;"><span style="font-size:11px;color:#64748b;">Phone</span><span style="font-size:12px;color:#e2e8f0;font-family:monospace;">{lead.phone or "—"} {phone_b}</span></div>' +
        f'<div style="display:flex;justify-content:space-between;"><span style="font-size:11px;color:#64748b;">Email</span><span style="font-size:12px;color:#e2e8f0;font-family:monospace;">{lead.email or "—"} {email_b}</span></div>' +
        '</div>' +
        '<div style="font-size:10px;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px;">Issues Found</div>' +
        issue_html + '</div>'
    )

def _review_msg_html(r: Tab2Result) -> str:
    # Tab 2 does not generate outreach messages — that happens in Tab 3 (Acquisition).
    return (
        '<div style="padding:4px 0;">' +
        '<div style="background:#0f172a;border-radius:8px;padding:16px;border-left:3px solid #334155;text-align:center;">' +
        '<div style="font-size:18px;margin-bottom:8px;">📤</div>' +
        '<div style="font-size:13px;color:#64748b;">No outreach message generated here.</div>' +
        '<div style="font-size:12px;color:#475569;margin-top:6px;line-height:1.5;">Outreach campaign messages are created in <strong style="color:#818cf8;">Tab 3 — Acquisition</strong> using the community campaign agent.</div>' +
        '</div></div>'
    )

def _review_notes_html(r: Tab2Result) -> str:
    if r.ai_notes:
        items = "".join(
            f'<div style="margin-bottom:7px;padding:7px 9px;background:#0f172a;border-radius:6px;'
            f'border-left:3px solid #6366f1;font-size:12px;color:#e2e8f0;line-height:1.5;">• {n}</div>'
            for n in r.ai_notes
        )
    else:
        items = '<div style="color:#475569;font-size:12px;text-align:center;padding:20px 0;">No AI notes — lead was processed deterministically.</div>'
    return f'<div style="padding:4px 0;">{items}</div>'


# ---------------------------------------------------------------------------
# Render function
# ---------------------------------------------------------------------------

def render_tab2() -> None:
    gr.HTML(_TAB_CSS)
    gr.Markdown("## 🧹 Lead Database Cleaner & Qualifier")
    gr.Markdown(
        "Import a raw lead list, detect duplicates and bad contacts, "
        "score each lead's quality, and export a clean file ready for CRM import.  \n"
        "AI agents normalize Balkan names (casing only — no diacritics guessing) "
        "and add qualitative observations for FIXABLE and POOR leads."
    )

    _env_key = bool(os.getenv("ANTHROPIC_API_KEY"))

    with gr.Row():
        with gr.Column(scale=2):
            source_radio = gr.Radio(
                choices=["🎭 Use demo data (200 synthetic leads)","📤 Upload file"],
                value="🎭 Use demo data (200 synthetic leads)",
                label="Data source",
            )
            file_upload = gr.File(label="Upload lead database  (.csv or .xlsx)", file_types=[".csv",".xlsx"], visible=False)
        with gr.Column(scale=1):
            mode_radio = gr.Radio(
                choices=["⚙️ Deterministic","🤖 AI Agents"],
                value="⚙️ Deterministic",
                label="Pipeline mode",
            )
            gr.Markdown("✅ API key detected." if _env_key else "⚠️ No API key — deterministic only.")
            api_key_input = gr.Textbox(label="Override API Key (optional)", placeholder="sk-ant-...", type="password")
            max_leads_slider = gr.Slider(minimum=10, maximum=500, value=200, step=10, label="Leads to process")

    run_btn = gr.Button("▶  Run Cleaning Pipeline", variant="primary")
    kpi_html_comp = gr.HTML(_KPI_PLACEHOLDER)
    city_map_comp = gr.Plot(label="Lead Distribution — Turkey", visible=False)

    with gr.Row(equal_height=False):
        with gr.Column(scale=2):
            table = gr.Dataframe(
                headers=["Quality","#","Name","Phone","Email","City","Source","Score","Issues","Duplicate of"],
                datatype=["str"]*10, interactive=False, wrap=True,
                label="Leads — click a row to view details",
            )
            export_btn  = gr.Button("⬇️  Export Cleaned CSV", variant="secondary", visible=False)
            export_file = gr.File(label="Download", visible=False)

        with gr.Column(scale=1, min_width=300):
            # Pipeline Summary
            summary_html_comp = gr.HTML("", visible=False)

            # Selected Lead Review
            gr.Markdown("#### 📋 Selected Lead")
            review_header = gr.HTML(_REVIEW_PLACEHOLDER)
            with gr.Tabs():
                with gr.TabItem("📊 Qualification"):
                    review_qual = gr.HTML(_TAB_PLACEHOLDER)
                with gr.TabItem("💬 Message"):
                    review_msg = gr.HTML(_TAB_PLACEHOLDER)
                with gr.TabItem("📋 Notes"):
                    review_notes = gr.HTML(_TAB_PLACEHOLDER)

    # State
    results_store = gr.State([])

    # ── Callbacks ──────────────────────────────────────────────────────────

    def on_source_change(source: str):
        return gr.update(visible=(source == "📤 Upload file"))

    def on_run(source, upload_file, mode, api_key, max_n):
        _reset = (_REVIEW_PLACEHOLDER, _TAB_PLACEHOLDER, _TAB_PLACEHOLDER, _TAB_PLACEHOLDER)
        _err = (_KPI_PLACEHOLDER, gr.update(visible=False), [], [], gr.update(visible=False), gr.update(visible=False), gr.update(visible=False)) + _reset
        if source == "📤 Upload file":
            if upload_file is None:
                return _err
            try:
                leads = _load_from_file(upload_file.name)
                if not leads:
                    return _err
            except Exception:
                return _err
        else:
            leads = load_demo_leads()

        want_ai      = mode == "🤖 AI Agents"
        resolved_key = (api_key or "").strip() or os.getenv("ANTHROPIC_API_KEY","")
        use_ai       = want_ai and bool(resolved_key)

        results = run_tab2_pipeline(leads=leads, use_ai_agents=use_ai, max_leads=int(max_n), anthropic_api_key=resolved_key or None)
        stats   = summarise_tab2(results)
        map_fig = _lead_city_map(results)

        return (
            _kpi_html(stats),
            gr.update(value=map_fig, visible=map_fig is not None),
            _results_to_table(results), results,
            gr.update(visible=True), gr.update(visible=False),
            gr.update(value=_pipeline_summary_html(stats), visible=True),
        ) + _reset

    def on_row_select(evt: gr.SelectData, results: list[Tab2Result]):
        if not results or evt.index[0] >= len(results):
            return _REVIEW_PLACEHOLDER, _TAB_PLACEHOLDER, _TAB_PLACEHOLDER, _TAB_PLACEHOLDER
        r = results[evt.index[0]]
        return _review_header_html(r), _review_qual_html(r), _review_msg_html(r), _review_notes_html(r)

    def on_export(results: list[Tab2Result]):
        if not results:
            return gr.update(visible=False)
        csv_str  = _export_csv(results)
        tmp_path = os.path.join(os.path.dirname(__file__), "..", "cleaned_leads.csv")
        with open(tmp_path, "w", encoding="utf-8", newline="") as f:
            f.write(csv_str)
        return gr.update(value=tmp_path, visible=True)

    # ── Event wiring ───────────────────────────────────────────────────────

    source_radio.change(fn=on_source_change, inputs=[source_radio], outputs=[file_upload])

    _run_outputs = [
        kpi_html_comp, city_map_comp, table, results_store,
        export_btn, export_file, summary_html_comp,
        review_header, review_qual, review_msg, review_notes,
    ]
    run_btn.click(fn=on_run, inputs=[source_radio, file_upload, mode_radio, api_key_input, max_leads_slider], outputs=_run_outputs)

    table.select(fn=on_row_select, inputs=[results_store], outputs=[review_header, review_qual, review_msg, review_notes])

    export_btn.click(fn=on_export, inputs=[results_store], outputs=[export_file])

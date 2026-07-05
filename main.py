"""
main.py

Entry point for the Balkan TV Lead & ERP Agent application.

Three-tab Gradio app:
    Tab 1 — Existing Customers / ERP Follow-up     ✅ implemented
    Tab 2 — Lead Database Cleaner & Qualifier      ✅ implemented
    Tab 3 — New Lead Acquisition                   🔜 coming soon

Run:
    python main.py

Environment variables (all optional — see .env.example):
    ANTHROPIC_API_KEY   — enables AI agents in Tab 1 and Tab 2
    VERBOSE             — set to "true" to log every agent call in the terminal
    APP_HOST            — bind host (default: 0.0.0.0)
    APP_PORT            — bind port (default: 7860)
    APP_SHARE           — "true" to create a public Gradio share link
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
load_dotenv()

import gradio as gr

from ui.tab1 import render_tab1
from ui.tab2 import render_tab2
from ui.tab3 import render_tab3


# ---------------------------------------------------------------------------
# Dark premium theme
# ---------------------------------------------------------------------------

_DARK_THEME = gr.themes.Base(
    primary_hue=gr.themes.colors.indigo,
    secondary_hue=gr.themes.colors.slate,
    neutral_hue=gr.themes.colors.slate,
    font=[gr.themes.GoogleFont("Inter"), "ui-sans-serif", "system-ui", "sans-serif"],
)

# Raw CSS — fills gaps the theme system can't reach
_CSS = """
/* ── Base dark background ─────────────────────────────────── */
:root {
    --body-background-fill: #0f172a;
    --background-fill-primary: #1e293b;
    --background-fill-secondary: #0f172a;
    --border-color-primary: #334155;
    --color-accent: #6366f1;
    --body-text-color: #e2e8f0;
    --body-text-color-subdued: #94a3b8;
    --block-background-fill: #1e293b;
    --block-border-color: #334155;
    --block-label-text-color: #94a3b8;
    --block-title-text-color: #e2e8f0;
    --input-background-fill: #0f172a;
    --input-border-color: #334155;
    --input-border-color-focus: #6366f1;
    --input-placeholder-color: #475569;
    --button-primary-background-fill: #6366f1;
    --button-primary-background-fill-hover: #4f46e5;
    --button-primary-text-color: #ffffff;
    --button-secondary-background-fill: #1e293b;
    --button-secondary-background-fill-hover: #334155;
    --button-secondary-text-color: #e2e8f0;
    --button-secondary-border-color: #334155;
    --table-even-background-fill: #1e293b;
    --table-odd-background-fill: #1a2942;
    --table-row-focus: #2d3f5a;
    --slider-color: #6366f1;
    --checkbox-background-color-selected: #6366f1;
    --radio-circle-color-selected: #6366f1;
    --stat-background-fill: #1e293b;
    --tab-selected-background-fill: #6366f1;
    --tab-text-color: #94a3b8;
    --tab-selected-text-color: #ffffff;
}

/* Full page dark */
body, .gradio-container { background: #0f172a !important; }

/* Header text */
.gradio-container h1, .gradio-container h2 {
    color: #f1f5f9 !important;
}

/* Tab bar styling */
.tab-nav { border-bottom: 1px solid #334155 !important; background: #0f172a !important; }
.tab-nav button {
    color: #94a3b8 !important;
    border-radius: 8px 8px 0 0 !important;
    font-weight: 500 !important;
    letter-spacing: 0.01em !important;
}
.tab-nav button.selected {
    background: #6366f1 !important;
    color: #ffffff !important;
    border-color: #6366f1 !important;
}

/* Tables */
table { border-collapse: collapse !important; }
table thead tr th {
    background: #0f172a !important;
    color: #94a3b8 !important;
    font-size: 12px !important;
    text-transform: uppercase !important;
    letter-spacing: 0.05em !important;
    border-bottom: 1px solid #334155 !important;
    padding: 10px 12px !important;
}
table tbody tr:hover td { background: #2d3f5a !important; }
table tbody tr td {
    color: #e2e8f0 !important;
    border-bottom: 1px solid #1e3a5f22 !important;
    padding: 10px 12px !important;
    font-size: 13px !important;
}

/* Dataframe wrapper */
.wrap.svelte-byatnx { background: #1e293b !important; border-radius: 10px !important; }

/* Input fields */
input, textarea, select {
    background: #0f172a !important;
    color: #e2e8f0 !important;
    border-color: #334155 !important;
}
input::placeholder, textarea::placeholder { color: #475569 !important; }

/* Sliders */
input[type="range"] { accent-color: #6366f1; }

/* Radio / checkbox labels */
.wrap label span { color: #e2e8f0 !important; }

/* File upload */
.file-preview { background: #1e293b !important; border-color: #334155 !important; }

/* Scrollbar */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: #0f172a; }
::-webkit-scrollbar-thumb { background: #334155; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #475569; }

/* Auto-scroll panel targets */
#customer_detail, #lead_detail { scroll-margin-top: 16px; }
"""

# JS: smooth-scroll to detail panels on content change
_JS = """
function() {
    function watchPanel(id) {
        var el = document.getElementById(id);
        if (!el) { setTimeout(function() { watchPanel(id); }, 600); return; }
        new MutationObserver(function() {
            el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }).observe(el, { childList: true, subtree: true });
    }
    setTimeout(function() {
        watchPanel('customer_detail');
        watchPanel('lead_detail');
    }, 1200);
}
"""


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _configure_logging() -> None:
    verbose = os.getenv("VERBOSE", "false").lower() == "true"
    level   = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="[%(asctime)s] %(levelname)-7s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in (
        "gradio", "httpx", "httpcore", "urllib3", "anthropic",
        "PIL", "matplotlib", "asyncio", "multipart",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# App assembly
# ---------------------------------------------------------------------------

def build_app() -> gr.Blocks:
    with gr.Blocks(title="Balkan TV - Lead & ERP Agent") as app:

        gr.HTML("""
        <div style="padding:16px 24px 12px; border-bottom:1px solid #334155; margin-bottom:4px;">
            <div style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:10px;">
                <div style="display:flex; align-items:center; gap:12px;">
                    <div style="width:38px;height:38px;background:linear-gradient(135deg,#6366f1,#8b5cf6);
                                border-radius:10px;display:flex;align-items:center;justify-content:center;
                                font-size:20px;flex-shrink:0;box-shadow:0 0 16px #6366f144;">📺</div>
                    <div>
                        <div style="font-size:17px;font-weight:700;color:#f1f5f9;line-height:1.2;">
                            Balkan TV - Lead & ERP Agent
                        </div>
                        <div style="font-size:11px;color:#64748b;margin-top:2px;">
                            Turkey Market · Balkan Diaspora Subscribers
                        </div>
                    </div>
                </div>
                <div style="display:flex; gap:6px; flex-wrap:wrap; align-items:center;">
                    <span style="background:#f59e0b18;color:#f59e0b;font-size:10px;font-weight:600;
                                 padding:3px 9px;border-radius:6px;border:1px solid #f59e0b33;
                                 letter-spacing:0.03em;">🧪 SYNTHETIC DEMO</span>
                    <span style="background:#10b98118;color:#10b981;font-size:10px;font-weight:600;
                                 padding:3px 9px;border-radius:6px;border:1px solid #10b98133;
                                 letter-spacing:0.03em;">👤 HUMAN-IN-THE-LOOP</span>
                    <span style="background:#6366f118;color:#818cf8;font-size:10px;font-weight:600;
                                 padding:3px 9px;border-radius:6px;border:1px solid #6366f133;
                                 letter-spacing:0.03em;">🤖 AI TRACE ACTIVE</span>
                </div>
            </div>
        </div>
        """)

        with gr.Tabs():
            with gr.Tab("📋 ERP Follow-up"):
                render_tab1()

            with gr.Tab("🧹 Lead Cleaner"):
                render_tab2()

            with gr.Tab("🎯 New Leads"):
                render_tab3()

    return app


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _configure_logging()

    host    = os.getenv("APP_HOST",  "0.0.0.0")
    port    = int(os.getenv("APP_PORT",  "7860"))
    share   = os.getenv("APP_SHARE", "false").lower() == "true"
    verbose = os.getenv("VERBOSE",   "false").lower() == "true"

    print("=" * 55)
    print("  Balkan TV — Lead & ERP Agent")
    print("=" * 55)
    print(f"  http://localhost:{port}")
    api_key_set = bool(os.getenv("ANTHROPIC_API_KEY"))
    print(f"  AI agents : {'✅ API key set' if api_key_set else '⚠️  No key - deterministic mode'}")
    print(f"  Verbose   : {'✅ ON' if verbose else 'OFF (set VERBOSE=true to enable)'}")
    print(f"  Share     : {share}")
    print("=" * 55)

    build_app().launch(
        server_name=host,
        server_port=port,
        share=share,
        show_error=True,
        theme=_DARK_THEME,
        css=_CSS,
        js=_JS,
    )

---
title: Balkan TV Agentic CRM
emoji: 📺
colorFrom: blue
colorTo: indigo
sdk: gradio
sdk_version: 6.19.0
app_file: main.py
pinned: false
---

# Balkan TV — Agentic CRM

Portfolio demo of an agentic CRM for an OTT/IPTV provider serving the Balkan diaspora in Turkey. Three tabs, each backed by a deterministic rule-based pipeline with optional AI enrichment via Claude.

- **Tab 1 — ERP Follow-up**: scans existing customers, flags overdue/at-risk accounts, drafts outreach messages.
- **Tab 2 — Lead Cleaner & Qualifier**: deduplicates and scores raw leads (CSV/XLSX upload or demo data).
- **Tab 3 — Acquisition Intelligence Agent**: profiles Balkan diaspora communities (Facebook/Instagram/local venues), scores switching likelihood, drafts campaign messages, recommends channels.

All AI actions require human approval before anything is sent — nothing is ever auto-dispatched.

## Demo mode (default)

This deployment runs entirely on **synthetic data** and **deterministic logic** by default — no API key required. Every pipeline (scoring, message drafting, channel recommendation) has a rule-based fallback that runs automatically when no API key is present, so the full app is explorable with zero setup.

## Using your own API keys

Two optional integrations can be enabled by entering your own key directly in the UI (no restart needed) or via environment variables:

| Key | Enables | Where to get one |
|---|---|---|
| `ANTHROPIC_API_KEY` | AI-generated evaluations, outreach messages, and campaign copy (Claude Haiku) in Tabs 1–3 | [console.anthropic.com](https://console.anthropic.com) |
| `SERPER_API_KEY` | **Live community discovery** in Tab 3 — real-time Google/Maps search for Balkan diaspora Facebook groups, Instagram accounts, and local venues in Turkey, instead of the bundled synthetic community list | [serper.dev](https://serper.dev) (free tier available) |

**In the UI:** each tab has an API key field in the sidebar — paste your key there and it's used for that session only, nothing is stored. In Tab 3, select the **"🔍 Live Search Serper"** data source option to trigger real scraping instead of demo/CSV data.

**Via `.env` file:** copy `.env.example` to `.env` and fill in the values:

```
ANTHROPIC_API_KEY=sk-ant-...
SERPER_API_KEY=...
```

Keys entered in the UI take priority over the `.env`/environment values.

## Running locally

This project uses [`uv`](https://docs.astral.sh/uv/) for dependency management. If you don't have it installed:

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then, from the project root:

```bash
uv venv
uv pip install -r requirements.txt
uv run python main.py
```

`uv venv` creates a local `.venv`, `uv pip install` installs the dependencies into it, and `uv run` executes `main.py` inside that environment — no manual activation needed.

The app starts on `http://localhost:7860` by default. Optional env vars: `APP_HOST`, `APP_PORT`, `APP_SHARE` (set to `true` for a public Gradio share link), `VERBOSE` (logs every agent call).

## Tech stack

Python 3.11+, `uv`, Gradio, Pydantic v2, Anthropic SDK (Claude Haiku), Plotly, Serper API.

## Notes

- All customer/lead data shipped with this repo is synthetic (fake names, `@lead-demo.com` emails) — safe to explore and publish.
- This is a portfolio project, not a production CRM.

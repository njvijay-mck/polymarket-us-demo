# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Demo/learning project for the [Polymarket US](https://polymarket.us) prediction market API. Nine numbered scripts cover the full API surface — public market data, authenticated trading, async/WebSocket streaming, and an AI-powered odds analysis engine (`09_odds_calculator.py`).

## Setup

```bash
uv sync                # Install all dependencies into .venv
cp .env.example .env   # Fill in real API keys before running
```

**Python 3.13** required (enforced by `.python-version` and `pyproject.toml`). Package manager is `uv` — do not use `pip` directly.

## Running Scripts

```bash
uv run 01_browse_markets.py
uv run 02_search_markets.py "bitcoin"
uv run 03_orderbook_viewer.py btc-100k-2025
uv run 08_markets_by_date.py --date 2026-02-25

# Script 09 — main tool
uv run 09_odds_calculator.py btc-100k-2025               # by slug
uv run 09_odds_calculator.py --search "NBA"              # keyword search
uv run 09_odds_calculator.py --date 2026-02-21 --limit 5 # by game date
uv run 09_odds_calculator.py --date 2026-02-21 --no-llm  # odds only, skip LLM
uv run 09_odds_calculator.py --date 2026-02-21 --deep-research --pdf
```

Scripts 04–07 require `POLYMARKET_KEY_ID` + `POLYMARKET_SECRET_KEY`. There are no tests or linting tools in this project.

---

## Script 09 — Full CLI Reference

| Flag | Default | Notes |
|---|---|---|
| `slug` (positional) | — | Mutually exclusive with `--search` / `--date` |
| `--search QUERY` | — | Keyword search across markets |
| `--date YYYY-MM-DD` | — | Game date (Eastern Time) — see API Quirks below |
| `--pick N` | all | Analyse only result N (0-indexed) from search/date list |
| `--limit N` | 10 | Max markets to display/analyse |
| `--no-llm` | off | Skip LLM; show odds table only |
| `--llm PROVIDER` | `claude` | `claude`, `openai`, `kimi`, `custom` |
| `--model MODEL` | see below | Override default model for chosen provider |
| `--llm-api-key KEY` | from env | Override API key at CLI instead of `.env` |
| `--llm-base-url URL` | — | Required for `--llm custom` |
| `--deep-research` | off | 4-stage pipeline: web search → research → critique → rebuttal → consolidation |
| `--web-search` | off | Single web search to enrich single-pass LLM call |
| `--edge-threshold N` | 5.0 | Minimum % edge to flag a recommended position |
| `--min-volume USD` | 1000 | Skip markets below this volume threshold |
| `--pdf [FILENAME]` | — | Save PDF report; auto-named if filename omitted |
| `--verbose` | off | Print raw market JSON |

---

## Switching LLM Providers

### Built-in providers

```bash
# Claude (default) — uses AnthropicLLMClient
uv run 09_odds_calculator.py --date 2026-02-21 --llm claude --model claude-opus-4-6

# OpenAI — uses OpenAICompatibleLLMClient
uv run 09_odds_calculator.py --date 2026-02-21 --llm openai --model gpt-4o

# Kimi — uses AnthropicLLMClient with custom base_url (Kimi speaks Anthropic API)
uv run 09_odds_calculator.py --date 2026-02-21 --llm kimi --model kimi-k2-0711-preview
```

**Default models** (defined in `_DEFAULT_MODELS` at line ~853):
- `claude` → `claude-haiku-4-5`
- `openai` → `gpt-4o`
- `kimi` → `kimi-for-coding`

### Adding a new provider (Groq, DeepSeek, Gemini, etc.)

Use `--llm custom` with any OpenAI-compatible endpoint:

```bash
# Groq
uv run 09_odds_calculator.py --date 2026-02-21 \
  --llm custom --llm-base-url https://api.groq.com/openai/v1 \
  --model llama-3.3-70b-versatile --llm-api-key $GROQ_API_KEY

# DeepSeek
uv run 09_odds_calculator.py --date 2026-02-21 \
  --llm custom --llm-base-url https://api.deepseek.com \
  --model deepseek-chat --llm-api-key $DEEPSEEK_API_KEY

# Google Gemini (OpenAI-compatible endpoint)
uv run 09_odds_calculator.py --date 2026-02-21 \
  --llm custom \
  --llm-base-url https://generativelanguage.googleapis.com/v1beta/openai/ \
  --model gemini-2.5-pro --llm-api-key $GEMINI_API_KEY
```

To permanently add a provider, add a branch to `create_llm_client()` (line ~912) and a default model to `_DEFAULT_MODELS` (line ~853).

### LLM Provider Documentation

| Provider | Docs | Models | Env Var |
|---|---|---|---|
| Anthropic Claude | [platform.claude.com/docs](https://platform.claude.com/docs) | [models list](https://docs.anthropic.com/en/docs/about-claude/models/overview) | `ANTHROPIC_API_KEY` |
| OpenAI | [platform.openai.com/docs](https://platform.openai.com/docs/api-reference) | [models list](https://platform.openai.com/docs/models) | `OPENAI_API_KEY` |
| Kimi / Moonshot | [platform.moonshot.ai/docs](https://platform.moonshot.ai/docs/guide/start-using-kimi-api) | `kimi-k2-0711-preview`, `kimi-k2.5` | `KIMI_API_KEY` |
| Groq | [console.groq.com/docs](https://console.groq.com/docs/quickstart) | [models](https://console.groq.com/docs/models) | `GROQ_API_KEY` |
| DeepSeek | [api-docs.deepseek.com](https://api-docs.deepseek.com/) | `deepseek-chat`, `deepseek-reasoner` | `DEEPSEEK_API_KEY` |
| Google Gemini | [ai.google.dev/gemini-api/docs/openai](https://ai.google.dev/gemini-api/docs/openai) | [models](https://ai.google.dev/gemini-api/docs/models) | `GEMINI_API_KEY` |

**Note on Kimi:** Kimi's `/coding/` endpoint speaks the **Anthropic** messages API (not OpenAI), so it is routed through `AnthropicLLMClient` with `base_url=https://api.kimi.com/coding`. All other third-party providers use `OpenAICompatibleLLMClient`.

---

## Architecture

**Flat script collection** — each numbered `.py` file is a self-contained CLI. No shared library; helpers (`_parse_json_str`, `_format_date`, `_status_label`) are duplicated across scripts.

### Script 09 Internal Structure

```
main()
├── argparse (source: slug | --search | --date — mutually exclusive)
├── Market fetching
│   ├── fetch_market_by_slug()
│   ├── search_and_pick()        — keyword → volume enrichment
│   └── search_by_date()         — paginated scan (see API Quirks)
│       └── _enrich_markets_with_volume()  — ThreadPoolExecutor(10) parallel
├── Per-market loop
│   ├── build_odds_table()       — outcomes/outcomePrices or marketSides → odds rows
│   ├── display_odds()           — terminal output
│   └── LLM analysis (unless --no-llm)
│       ├── llm_analysis()       — single-pass (+ optional web search)
│       └── deep_research_pipeline()
│           ├── web_search_context()    — Brave Search API (httpx)
│           ├── social_media_context()  — Brave Search Twitter/Reddit
│           ├── Stage 1: Research agent LLM call
│           ├── Stage 2: Critique agent LLM call
│           ├── Stage 3: Rebuttal agent LLM call
│           └── Stage 4: Consolidation → parse_llm_probabilities() + parse_sentiment()
├── display_edge_analysis() / display_ev_analysis() / display_sentiment_analysis()
└── generate_pdf() / generate_consolidated_pdf()  → reports/YYYY-MM-DD/
```

**LLM client interface** — both clients expose identical `.chat(system, user, max_tokens) -> str`:
- `AnthropicLLMClient` — wraps `anthropic.Anthropic` (Claude + Kimi)
- `OpenAICompatibleLLMClient` — wraps `openai.OpenAI` (OpenAI + Groq/DeepSeek/Gemini via base_url)

**Key dataclasses** (lines 59–93):
- `RunMetrics` — provider, model, pipeline, agents run, search queries, sources, elapsed time
- `ReportData` — market dict, odds rows, LLM analysis text, LLM probabilities, sentiment, metrics
- `ConsolidatedReport` — list of `ReportData`; used by `generate_consolidated_pdf()`

---

## Polymarket US API Quirks

These are non-obvious behaviours discovered through live testing. Read before touching any market-fetching code.

### 1. Date filtering uses slug game date, not endDate

`search_by_date()` matches on `_game_date_from_slug()`, **not** the market's `endDate`. The slug always ends with the game date (e.g. `aec-nba-hou-cha-2026-02-19` → game date Feb 19). Non-sports markets without a slug date fall back to `endDate` EST.

**Why:** Polymarket uses two settlement windows — next-day (game date + 1) for most markets, and **14-day batch** for others (e.g. Feb 21 games all had `endDate=2026-03-07`). Using `endDate` for the filter returns wrong-day games or misses entire dates.

### 2. Markets API sorts by endDate ascending, near-term markets are at high offsets

The full dataset has ~4,900 records. Without a `closed` filter:
- Offsets 0–2,400: Nov 2025 – Jan 2026 (settled/old markets)
- Offsets 4,600–4,800: current/upcoming markets

`search_by_date()` uses `max_pages=70` (7,000-record cap) to reach these. Old code with `max_pages=20` stopped at offset 1,900 and never saw recent games.

### 3. Short pages mid-dataset do not mean end of data

The API returns 99 records at offset 2,400 even though 100+ more exist at offset 2,500. **Do not break on `len(page) < page_size`** — only break on an empty page. This is an API bug.

### 4. `closed=True active=True` = pre-settlement state (not "game over")

Polymarket marks a market `closed=True` when trading is locked (at game tip-off), not when results are confirmed. The `active=True` flag stays set throughout. Both "game in progress" and "fully settled" states look identical. Filtering `closed=False` excludes today's games that are mid-play.

### 5. `orderBy` parameter — all documented field names are invalid

The API docs list `volumeNum`, `liquidityNum`, `createdAt`, `lastTradePrice` as valid `orderBy` fields. **All return `invalid OrderBy field` errors in practice.** Do not attempt to sort via the API.

### 6. `outcomes` and `outcomePrices` are JSON-encoded strings

These fields come from the API as strings, not lists. Always decode with `_parse_json_str()`. Sports markets may use `marketSides[]` instead — `build_odds_table()` handles both shapes.

### 7. `EventsListParams` supports date filters, `MarketsListParams` does not

`events.list()` accepts `endDateMin`/`endDateMax` but these filters are ignored in practice. `markets.list()` has no server-side date filter at all — all date filtering is client-side via pagination.

---

## Environment Variables (`.env`)

```
# Trading (scripts 04-07)
POLYMARKET_KEY_ID=
POLYMARKET_SECRET_KEY=

# LLM — set only the provider you use (script 09)
ANTHROPIC_API_KEY=      # Claude (default)
OPENAI_API_KEY=
KIMI_API_KEY=           # Kimi coding endpoint (speaks Anthropic API)
LLM_API_KEY=            # --llm custom endpoint
LLM_BASE_URL=           # --llm custom endpoint base URL

# Web search enrichment (--deep-research / --web-search)
BRAVE_SEARCH_API_KEY=
```

## Dependencies

| Package | Purpose |
|---|---|
| `polymarket-us` | Polymarket US API SDK |
| `anthropic` | Claude + Kimi LLM client |
| `openai` | OpenAI + any OpenAI-compatible endpoint |
| `httpx` | Brave Search API calls in deep research pipeline |
| `python-dotenv` | Auto-loads `.env` at startup |
| `reportlab` | PDF generation (`reports/YYYY-MM-DD/`) |
| `tzdata` | IANA timezone data for Eastern Time conversion |
